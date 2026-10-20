"""Haiku trader v4 (HV4) background chain-fact prefetch: the fix for `chain_stale` and `as_of_refusal` (shakedown #1219, 2026-10-19).

Paper only. Reads only; every call goes through tools.hv4_chain.HV4Chain (same allow-list, token bucket, daily cap and credit counter).

Why. The scanner built a card by fetching the chain facts (mint facts, holders, Mayhem) at card time, inside the scan. In live mode the scan runs at
T + ~100 ms, so a fetch made at card time is stamped after T: the as-of check refused it (`chain.as_of_ms=... is not before as_of_ms=...`).
The fetch was cached, so the NEXT card for that pool saw a stamp before T, but the mint cache keeps a clean mint for 24 h with its first stamp, and a
card refuses mint facts older than 15 min (`chain_stale`). A pool carded again later than 15 min after its first fetch was refused for ever. The
fetches also ran in the scan, one pool at a time (~34 s each on the public endpoint), which stalled the live loop for minutes.

What. A background refresher keeps a small store of chain facts per watchlist pool. The runner tells it which pools pass the filters (`watch`) after each
scan; a worker thread refreshes them in priority order inside an hourly call budget; the scan only READS the store (`part_at`), with no RPC:

  * the newest fact of each kind stamped STRICTLY before T (and not after the arena's latest slot), never a fact stamped at or after T;
  * a mint fact older than the card's 15 min limit is still returned, so the card refuses it as `chain_stale` (fail closed);
  * a holders fact older than the card's 5 min limit is left out (null holders block, the same as a failed read; holders are model facts, not vetoes);
  * no fact for the pool yet (newly passing): None, and the card is refused as `chain_unavailable`. The pool gets its facts in the next refresh pass.

DESIGN section 2 says holders are read "every 15 min and at card time". A read at card time cannot be as-of T (it is stamped after T), so this module
implements "the newest refresh before T". The hot pools (about to be carded) are refreshed every 4 min so their holders are inside the 5 min limit.

Call budget (DESIGN section 6: at most about 40k standard calls a day at 2 rps). One refresh is: mint facts (1 call, every MINT_REFRESH_MS), Mayhem (1 call
once per pool, then cached for ever), holders (1 call per refresh, plus a supply read once an hour per mint). The worker spends at most HOURLY_BUDGET
calls in any rolling hour (1,500 = 36k a day); the cold tier may use at most COLD_SHARE of it, so new and hot pools are never starved by the rest.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from tools import hv4_cards as C

log = logging.getLogger("hv4_prefetch")

MIN_MS = 60_000
HOT_HOLDERS_REFRESH_MS = 4 * MIN_MS  # under the card's 5 min holders limit, with slack for a cycle
MINT_REFRESH_MS = 10 * MIN_MS  # under the card's 15 min mint limit (C.CHAIN_MINT_MAX_AGE_MS)
HOT_LINGER_MS = 15 * MIN_MS  # a pool stays hot for three scans after it was last tagged, boarded or top by volume
DROPPED_KEEP_MS = 24 * 60 * MIN_MS  # how long a dropped pool is remembered, for the "re_entry" label of its first_seen row
DROP_AFTER_MS = 30 * MIN_MS  # a pool that has not passed the filters for this long leaves the store
RETRY_AFTER_MS = 60_000  # after a failed read of a kind, wait this long before trying that kind again; doubles with each failure in a row, up to RETRY_MAX_MS
RETRY_MAX_MS = 16 * MIN_MS
SPEND_SCHEMA = "hv4_prefetch.spend.v1"
HOURLY_BUDGET = 1_500  # policy v1 (d1dbcd6)
HOURLY_BUDGET_V2 = 2_000  # policy v2, Note N4: the cap in any rolling hour (the refresher plans to stop SAFETY_MARGIN short); 48,000 a day = hv4_chain.DEFAULT_DAILY_CALL_CAP
V2_HOT_HOLDERS_REFRESH_MS = 12 * MIN_MS  # policy v2: holders of a hot pool are re-read every 12 min (v1: HOT_HOLDERS_REFRESH_MS, 4 min)
V2_WARM_HOLDERS_REFRESH_MS = 12 * MIN_MS  # policy v2: holders of a warm pool (in the latest scan's filter-pass set, not hot) are re-read every 12 min; v1 reads none
WARM_HOLD_RESERVE = 600  # v2: warm holders reads stop this many calls short of the cap (P2: at least as far short as any other class; the others stop HOT_MINT_RESERVE short)
COLD_SHARE = 0.6
CYCLE_S = 5.0
KEEP = 3  # facts of each kind kept per pool, so a fact stamped after T can be skipped for an older one
HOUR_MS = 60 * MIN_MS

# Policy. v1 is the d1dbcd6 behaviour (kept as `_run_once_v1`, for the A-off equivalence proof). v2 is the HV4 v2 reprioritisation (see `_run_once_v2`): it spends no
# more calls than v1 may (the same rolling-hour cap, now measured on the chain counter's own record and with a safety margin); it only changes WHO is served first.
# NOT decision-neutral: it changes which chain facts a card sees.
POLICY_V1, POLICY_V2 = "v1", "v2"
MINT_RESERVE = 800  # v2: calls an hour held back for the mint class (new pools and mint refreshes); the holders class may not spend into it
HOT_MINT_RESERVE = 400  # v2: calls an hour held back for mint reads of new and hot pools; a cold pool's mint read may not plan into it (about 60 hot pools x 6 an hour)
SAFETY_MARGIN = 20  # v2: calls under the hourly budget that no refresh may plan to use (an in-flight refresh can retry; the counter counts every attempt)
MINT_EST = 1  # v2 planning estimate of a mint refresh (plus 1 for the first Mayhem read of a pool); the real count is read back from the counter
HOLDERS_EST = 3  # v2 planning estimate of a holders refresh (supply once an hour per mint, the largest-accounts read, one re-read on a supply move)


def default_policy(env: Mapping[str, str] | None = None) -> str:
    """MAL_HV4_PREFETCH_POLICY=v1 selects the d1dbcd6 policy (the A-off arm of the equivalence replays); anything else is v2."""
    return POLICY_V1 if str((os.environ if env is None else env).get("MAL_HV4_PREFETCH_POLICY", "v2")).strip().lower() == "v1" else POLICY_V2

assert HOT_HOLDERS_REFRESH_MS < C.CHAIN_HOLDERS_MAX_AGE_MS and MINT_REFRESH_MS < C.CHAIN_MINT_MAX_AGE_MS
assert V2_HOT_HOLDERS_REFRESH_MS < C.CHAIN_HOLDERS_MAX_AGE_MS_V2 and V2_WARM_HOLDERS_REFRESH_MS < C.CHAIN_HOLDERS_MAX_AGE_MS_V2  # 12 min against 15 min, both
assert WARM_HOLD_RESERVE >= 400  # warm reads stop at least as far short as the other lower classes (HOT_MINT_RESERVE)


@dataclass
class _Pool:
    pool: str
    mint: str | None
    first_ms: int
    last_seen_ms: int
    hot_until_ms: int = 0
    mints: deque = field(default_factory=lambda: deque(maxlen=KEEP))  # MintFacts, ok only
    mayhems: deque = field(default_factory=lambda: deque(maxlen=KEEP))  # (is_mayhem, as_of_ms), known flags only
    holders: deque = field(default_factory=lambda: deque(maxlen=KEEP))  # Holders, ok only
    next_try_ms: dict = field(default_factory=lambda: {"mint": 0, "holders": 0})
    fails: dict = field(default_factory=lambda: {"mint": 0, "holders": 0})


def _slot_ok(stamp_slot: Any, slot: Any) -> bool:
    """The same rule as hv4_cards.assert_as_of: a fact whose slot is after the arena's latest slot is a leak."""
    return not (isinstance(slot, int) and not isinstance(slot, bool) and isinstance(stamp_slot, int) and not isinstance(stamp_slot, bool) and stamp_slot > slot)


class ChainPrefetcher:
    """Keeps chain facts for the filter-passing pools fresh, off the scan path. `chain` is a tools.hv4_chain.HV4Chain (or anything with its
    mint_facts / mayhem_flag / largest_holders methods). `compose(mf, mh_flag, holders)` builds the card's chain dict (HV4ChainSource.compose)."""

    def __init__(self, chain: Any, compose: Callable[[Any, Any, Any], dict], *, clock: Callable[[], int] | None = None, holders: bool | None = None,
                 hourly_budget: int | None = None, spend_path: str | os.PathLike | None = None, policy: str | None = None, obs: Any = None,
                 cold_mint_refresh_ms: int | None = None, hot_holders_refresh_ms: int | None = None, warm_holders_refresh_ms: int | None = None) -> None:
        self.chain, self.compose = chain, compose
        # hot_holders_refresh_ms and warm_holders_refresh_ms are OVERRIDES for tests (None = the policy's value below); run mode passes neither.
        # A MEASUREMENT KNOB for the replay harness (hv4_memreplay.Rig through `hv4_scanprof run --cold-mint-refresh-min`); run mode never passes it (live_chain_source
        # builds the prefetcher without it, and no environment variable reaches it): the mint refresh interval of a pool that is not hot. None = MINT_REFRESH_MS for all.
        self.cold_mint_refresh_ms = cold_mint_refresh_ms
        self.policy = policy or default_policy()
        v2 = self.policy == POLICY_V2
        # Policy defaults (Note N4). The two constructor arguments are overrides for tests; run mode passes neither.
        self.hot_holders_refresh_ms = hot_holders_refresh_ms or (V2_HOT_HOLDERS_REFRESH_MS if v2 else HOT_HOLDERS_REFRESH_MS)
        self.warm_holders_refresh_ms = warm_holders_refresh_ms if warm_holders_refresh_ms is not None else (V2_WARM_HOLDERS_REFRESH_MS if v2 else None)
        self.holders_max_age_ms = C.holders_max_age_ms(self.policy)  # the ONE limit this policy serves with, the card vetoes with and the direct chain path reads with
        self._last_watch_ms: int | None = None
        self._promoted: set[str] = set()  # pools not hot before the latest scan that the scan made hot (tagged, on the board or top by volume)
        self._warm_before: set[str] = set()  # the part of those that were already in the store: warm pools
        self._warm_vol: dict[str, float] = {}
        self._warm_vol_ms: int | None = None
        self.policy = policy or default_policy()
        assert self.policy in (POLICY_V1, POLICY_V2)
        self.obs = obs  # tools.hv4_obs.ObsLog or None: append-only side rows, never read by a decision
        self._spent_mint: deque[list[int]] = deque()  # v2: [minute bucket ms, calls] of the mint class in the last hour
        self._spent_hot: deque[list[int]] = deque()  # v2: the part of the mint class spent on new and hot pools
        self._dropped: dict[str, int] = {}  # pool -> last_seen_ms of a pool that left the watch set (a later entry is a re-entry); entries older than DROPPED_KEEP_MS go
        self._obs_hour: int | None = None
        self._obs_start_ms = 0
        self._deferred_pools: dict[int, dict[str, set]] = {}  # hour index -> class ("hot_mint", "cold_mint", "hold") -> distinct pools deferred (obs on only)
        self._booked_by_hour: dict[int, int] = {}  # hour index -> calls this prefetcher booked in it (read by the chain_hour row)
        self._warm_by_hour: dict[int, list[int]] = {}  # hour index -> [warm holders reads, calls] (v2)
        self._at_load: dict[str, Any] = {}
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)
        provider = getattr(getattr(chain, "rpc", None), "provider", None)
        self.holders_on = (provider != "public") if holders is None else holders  # the public endpoint refuses getTokenLargestAccounts: do not spend calls on it
        self.hourly_budget = hourly_budget if hourly_budget is not None else (HOURLY_BUDGET_V2 if self.policy == POLICY_V2 else HOURLY_BUDGET)
        self.pools: dict[str, _Pool] = {}
        self._spent: deque[list[int]] = deque()  # [minute bucket ms, calls] in the last hour
        self.spend_path = spend_path
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats_: dict[str, int] = {}
        if spend_path is not None:
            self._load_spend()
        self._capture_load()

    def _capture_load(self) -> None:
        """What this process found at load (the spend file's rolling hour, the counter's running and per-day calls), read here and not when the obs file is attached: a
        call made in between must not make a reset spend file look like a reload (check 8)."""
        now = self.clock()
        c = getattr(self.chain, "counter", None)
        self._obs_start_ms = now
        try:
            self._at_load = {"process_start_ms": now, "spent_at_load": self._spent_last_hour(now), "spend_state_reset": self.stats_.get("spend_state_reset", 0),
                             "counter_calls_at_start": getattr(c, "calls", None),
                             "counter_day_calls_at_start": (c.calls_today() if c is not None and hasattr(c, "calls_today") else None)}
        except Exception:  # noqa: BLE001
            self._at_load = {"process_start_ms": now}

    def _obs_err(self) -> None:
        with self._lock:
            self._n("obs_errors")

    # -- bookkeeping
    def _n(self, k: str, n: int = 1) -> None:
        self.stats_[k] = self.stats_.get(k, 0) + n

    def _spent_last_hour(self, now: int) -> int:
        while self._spent and self._spent[0][0] <= now - 60 * MIN_MS:
            self._spent.popleft()
        return sum(n for _, n in self._spent)

    def _calls_total(self) -> int | None:
        """The counter's CUMULATIVE call count for this process. Not calls_today(): that is per UTC day and falls back to 0 at 00:00Z, so a refresh that
        straddles midnight would book a negative spend and open the budget for the next hour."""
        c = getattr(self.chain, "counter", None)
        for name in ("calls",):
            v = getattr(c, name, None)
            if isinstance(v, int) and not isinstance(v, bool):
                return v
        try:
            return int(c.calls_today()) if c is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _book(self, now: int, used: int, mint: bool = False, hot: bool = False) -> None:
        """Add `used` calls to the rolling hour (minute buckets, so the persisted file stays small) and save. `mint`: also book them to the mint class (v2); `hot`: and to
        its new-and-hot part."""
        used = max(0, int(used))
        b = now - now % MIN_MS
        with self._lock:
            hr = b // HOUR_MS
            self._booked_by_hour[hr] = self._booked_by_hour.get(hr, 0) + used
            for old in [k for k in self._booked_by_hour if k < hr - 3]:
                del self._booked_by_hour[old]
            for dq, on in ((self._spent, True), (self._spent_mint, mint), (self._spent_hot, hot)):
                if not on:
                    continue
                if dq and dq[-1][0] == b:
                    dq[-1][1] += used
                else:
                    dq.append([b, used])
            self._save_spend()

    def _mint_spent_last_hour(self, now: int) -> int:
        while self._spent_mint and self._spent_mint[0][0] <= now - 60 * MIN_MS:
            self._spent_mint.popleft()
        return sum(n for _, n in self._spent_mint)

    def _hot_spent_last_hour(self, now: int) -> int:
        while self._spent_hot and self._spent_hot[0][0] <= now - 60 * MIN_MS:
            self._spent_hot.popleft()
        return sum(n for _, n in self._spent_hot)

    def _hour_calls(self, now: int) -> int:
        """v2: calls in the rolling hour as the CHAIN COUNTER saw them (every attempt sent, by any caller, retries included), or the prefetcher's own booking when that
        is larger (after a restart the counter's minutes are empty and the spend file holds the previous process's calls). One extra minute is counted: conservative."""
        own = self._spent_last_hour(now)
        c = getattr(self.chain, "counter", None)
        fn = getattr(c, "calls_between", None)
        if fn is None:
            return own
        lo = now - 60 * MIN_MS - MIN_MS + 1
        try:
            # plus the minutes the PREVIOUS process left in the counter's state file (CreditCounter.prior_minutes): a lost or reset spend file cannot hide the hour
            # that crosses a restart. They are the earlier processes' calls only; this process's own minutes are what calls_between counts.
            prior = sum(int(m[1]) for m in (getattr(c, "prior_minutes", None) or []) if lo <= m[0] < now + 1)
            return max(own, int(fn(lo, now + 1)) + prior)
        except Exception:  # noqa: BLE001
            return own

    def _load_spend(self) -> None:
        """Restore the rolling-hour spend after a restart. A missing file is a fresh start; a corrupt one is treated as a fully spent hour (fail closed)."""
        try:
            text = open(self.spend_path, encoding="utf-8").read()
        except FileNotFoundError:
            return
        except OSError:
            text = ""
        now = self.clock()
        try:
            doc = json.loads(text)
            if not isinstance(doc, dict) or doc.get("schema") != SPEND_SCHEMA:
                raise ValueError("schema")
            rows = [[int(a), int(b)] for a, b in doc["spent"]]
            if any(b < 0 for _, b in rows):
                raise ValueError("negative")
        except (ValueError, KeyError, TypeError, OverflowError):  # OverflowError: a spent row of Infinity
            log.warning("hv4_prefetch spend state unreadable: treating the hour as fully spent")
            self._n("spend_state_reset")
            self._spent.append([now - now % MIN_MS, self.hourly_budget])
            return
        self._spent.extend(r for r in sorted(rows) if r[0] > now - 60 * MIN_MS)
        try:
            mint_rows = [r for r in sorted([int(a), int(b)] for a, b in doc.get("mint", [])) if r[0] > now - 60 * MIN_MS and r[1] >= 0]
            hot_rows = [r for r in sorted([int(a), int(b)] for a, b in doc.get("hot_mint", [])) if r[0] > now - 60 * MIN_MS and r[1] >= 0]
        except (ValueError, TypeError, OverflowError):
            # Fail closed means booking NOTHING to the mint classes: the holders and lower classes are measured as `spent - mint_spent` and `spent - hot_spent`, so a smaller
            # mint figure makes them look larger and they are deferred first. (Booking a full reserve here would have made them look smaller: permissive.)
            self._n("spend_class_reset")
            return
        self._spent_mint.extend(mint_rows)
        self._spent_hot.extend(hot_rows)

    def _save_spend(self) -> None:
        """Atomic write: temp file in the same directory, fsync, rename. Called with the lock held. A failed write is counted, never raised."""
        if self.spend_path is None:
            return
        from pathlib import Path

        path = Path(self.spend_path)
        tmp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".hv4-prefetch-spend.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                doc = {"schema": SPEND_SCHEMA, "spent": [list(r) for r in self._spent]}
                if self.policy == POLICY_V2:
                    doc["mint"] = [list(r) for r in self._spent_mint]  # extra keys: the v1 reader and the report tool read "spent" only
                    doc["hot_mint"] = [list(r) for r in self._spent_hot]
                json.dump(doc, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            tmp = None
        except OSError:
            self._n("spend_write_errors")
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    # -- the runner's side
    def watch(self, items: Iterable[tuple[str, str | None]], hot: Iterable[str], now_ms: int, vols: Mapping[str, Any] | None = None) -> None:
        """The pools that pass the filters at this scan, and the ones about to be carded (tagged, on the board, top by volume). `vols`: each pool's 60-min gross volume
        (features vol_60m) as of THIS scan's state, the order of the warm holders reads (policy v2); a later pass uses it only if it was taken at or before the pass."""
        hot = set(hot)
        entered: list[dict[str, Any]] = []
        n_pass = 0
        promoted: set[str] = set()
        warm_before: set[str] = set()
        with self._lock:
            for pool, mint in items:
                n_pass += 1
                p = self.pools.get(pool)
                if pool in hot and (p is None or now_ms >= p.hot_until_ms):  # not hot before this scan: this scan promotes it
                    promoted.add(pool)
                    if p is not None:
                        warm_before.add(pool)
                if p is None:
                    p = self.pools[pool] = _Pool(pool, mint, now_ms, now_ms)
                    self._n("pools_added")
                    gone = self._dropped.pop(pool, None)
                    entered.append({"pool": pool, "entry": "first" if gone is None else "re_entry", "since_last_seen_ms": None if gone is None else now_ms - gone,
                                    "hot": pool in hot})
                p.last_seen_ms = now_ms
                if mint and p.mint != mint:
                    p.mint = mint
                if pool in hot:
                    p.hot_until_ms = now_ms + HOT_LINGER_MS
            for pool in [k for k, p in self.pools.items() if now_ms - p.last_seen_ms > DROP_AFTER_MS]:
                self._dropped[pool] = self.pools[pool].last_seen_ms
                del self.pools[pool]
                self._n("pools_dropped")
            for pool in [k for k, ms in self._dropped.items() if now_ms - ms > DROPPED_KEEP_MS]:  # bounded: a re-entry after a day is reported as a first entry
                del self._dropped[pool]
            self._last_watch_ms = now_ms
            self._promoted, self._warm_before = promoted, warm_before
            if vols is not None:
                self._warm_vol = {k: float(v) for k, v in vols.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
                self._warm_vol_ms = now_ms
        obs = self.obs
        if entered and obs is not None and obs.enabled:  # one append for the scan's new pools, after the lock; the scanner's watch set is its filter-pass set
            try:
                scan_ms = getattr(obs, "scan_ms", None)
                obs.write_many([{"kind": "first_seen_by_scanner", "scan_ms": scan_ms if scan_ms is not None else now_ms, "now_ms": now_ms, "in_filter_pass": True,
                                 "n_filter_pass": n_pass, **e} for e in entered])
            except Exception:  # noqa: BLE001 - on the scan thread: never let a side row stop the scan
                self._obs_err()

    def part_at(self, pool: str, mint: str | None, T: int, slot: int) -> dict | None:
        """The chain dict for a card at scan instant T: only facts stamped strictly before T, no RPC. None when the pool has no usable mint fact."""
        stale_row: dict[str, Any] | None = None
        null_row: dict[str, Any] | None = None
        with self._lock:
            p = self.pools.get(pool)
            if p is None or not mint:
                self._n("served_unknown_pool")
                return None
            mf = next((m for m in reversed(p.mints) if m.as_of_ms is not None and m.as_of_ms < T and _slot_ok(m.as_of_slot, slot)), None)
            if mf is None:
                self._n("served_none")
                return None
            if T - mf.as_of_ms > C.CHAIN_MINT_MAX_AGE_MS:
                self._n("served_mint_stale")  # still returned: the card refuses it as chain_stale
                obs = self.obs
                if obs is not None and obs.enabled:
                    try:  # built under the lock, WRITTEN after it: the scan thread must not hold the lock across file I/O
                        now = self.clock()
                        stale_row = {"pool": pool, "scan_ms": T, "age_ms": T - mf.as_of_ms, "hot": now < p.hot_until_ms, "n_mints": len(p.mints),
                                     "fails_mint": p.fails["mint"], "next_try_in_ms": max(0, p.next_try_ms["mint"] - now), "pool_age_in_store_ms": now - p.first_ms}
                    except Exception:  # noqa: BLE001
                        self._n("obs_errors")
            flag = next(((v, ms) for v, ms in reversed(p.mayhems) if ms < T), None)
            hd = next((h for h in reversed(p.holders) if h.as_of_ms is not None and h.as_of_ms < T and _slot_ok(h.as_of_slot, slot)), None)
            newest_holders_ms = hd.as_of_ms if hd is not None else None
            if hd is not None and T - hd.as_of_ms > self.holders_max_age_ms:  # the policy's limit (v2: 15 min), the same constant the card's veto reads
                self._n("served_holders_omitted_stale")
                hd = None
            if hd is None and self.obs is not None and self.obs.enabled:  # a card with no holders block: say why (built here, written after the lock)
                promoted = pool in self._promoted
                null_row = {"pool": pool, "scan_ms": T, "hot": (not promoted) and self.clock() < p.hot_until_ms, "promoted_at_scan": promoted,
                            "warm": pool in self._warm_before, "newest_holders_age_ms": (T - newest_holders_ms) if newest_holders_ms is not None else None}
            self._n("served_ok")
            result = self.compose(mf, _Flag(flag[0] if flag else None), hd)
        if stale_row is not None:
            try:
                self.obs.write("chain_stale_served", **stale_row)
            except Exception:  # noqa: BLE001
                self._obs_err()
        if null_row is not None:
            try:
                self.obs.write("holders_null_served", **null_row)
            except Exception:  # noqa: BLE001
                self._obs_err()
        return result

    # -- the worker's side
    def _due(self, p: _Pool, now: int) -> tuple[bool, bool]:
        mint_age = (now - p.mints[-1].as_of_ms) if p.mints and p.mints[-1].as_of_ms is not None else None
        hot = now < p.hot_until_ms
        every = MINT_REFRESH_MS if (hot or self.cold_mint_refresh_ms is None) else self.cold_mint_refresh_ms
        need_mint = (mint_age is None or mint_age >= every) and now >= p.next_try_ms["mint"]
        hold_age = (now - p.holders[-1].as_of_ms) if p.holders and p.holders[-1].as_of_ms is not None else None
        if hot:
            every_h = self.hot_holders_refresh_ms
        elif self.warm_holders_refresh_ms is not None and p.last_seen_ms == self._last_watch_ms:
            every_h = self.warm_holders_refresh_ms  # warm: in the latest scan's filter-pass set and not hot
        else:
            every_h = None  # v1, or a pool that left the filter-pass set: no holders read until it is hot
        need_hold = (self.holders_on and every_h is not None and (hold_age is None or hold_age >= every_h) and now >= p.next_try_ms["holders"])
        return need_mint, need_hold



    def run_once(self, now_ms: int | None = None) -> int:
        """One pass under the active policy. Returns the calls made."""
        now = self.clock() if now_ms is None else now_ms
        try:
            made = self._run_once_v2(now) if self.policy == POLICY_V2 else self._run_once_v1(now)
        finally:  # the hour rows are written even when a pass raises on every loop (the loop counts pass_errors and goes on)
            try:
                self._obs_tick(now)
            except Exception:  # noqa: BLE001 - counted apart from pass_errors (which would mask a refresh failure), never raised
                self._obs_err()
        return made

    def _run_once_v1(self, now: int) -> int:
        """d1dbcd6: every due pool, new first, then hot, then cold, oldest fact first, until the hourly budget is spent. The cold tier may spend only while the TOTAL calls
        of the last hour are under COLD_SHARE of the budget (900): once the hot tier alone has spent that, no cold pool is refreshed. Returns the calls made."""
        with self._lock:
            work = []
            for p in self.pools.values():
                need_mint, need_hold = self._due(p, now)
                if not (need_mint or need_hold) or not p.mint:
                    continue
                tier = 0 if not p.mints else (1 if now < p.hot_until_ms else 2)
                age = (p.mints[-1].as_of_ms or 0) if p.mints else 0
                work.append((tier, age, p.pool, need_mint, need_hold))
            work.sort()
        made = 0
        for tier, _, pool, need_mint, need_hold in work:
            with self._lock:
                spent = self._spent_last_hour(now)
                est = int(need_mint) + int(need_hold) + (1 if pool in self.pools and not self.pools[pool].mayhems else 0)
                cap = self.hourly_budget if tier < 2 else int(self.hourly_budget * COLD_SHARE)
                if spent + est > cap:
                    self._n("budget_deferred")
                    continue
                p = self.pools.get(pool)
            if p is None:
                continue
            before = self._calls_total()
            self._refresh(p, now, need_mint, need_hold)
            after = self._calls_total()
            used = max(0, after - before) if (before is not None and after is not None) else est
            self._book(now, used)
            made += used
        self._n("passes")
        self._n("calls", made)
        return made

    def _run_once_v2(self, now: int) -> int:
        """HV4 v2. Why v1 left hot pools stale: its cold tier was capped at COLD_SHARE of the budget measured on the TOTAL calls of the hour (not on the cold tier's own),
        so once the hot tier's holders reads and mint refreshes had spent 900 calls (the live stub spent 1,044 in its first full hour) no cold pool got a mint refresh at
        all; its fact aged past the card's 15 min limit, and when the pool was tagged or boarded at the next scan (cold to hot inside `watch`, before its card) the card read
        the old fact and was refused chain_stale. A pool only becomes a card candidate by being in the filter-pass set, so a fresh mint fact for EVERY pool in the set is what
        the check needs; a holders block is a model fact, not a veto (a missing one only counts against check 4).

        Note N4 (QP ruling on PR #768): the order of service in one pass (the passes repeat every CYCLE_S) is
          0 mint reads of new pools (with the once-only Mayhem flag), 1 mint reads of hot pools, 2 holders reads of hot pools (every 12 min), 3 mint reads of the other pools
          (oldest fact first), 4 holders reads of warm pools (every 12 min; highest 60-min volume first, the volume taken from the scan's state at or before this pass).
        Caps, all on the rolling hour as the chain counter counts it (every attempt, every caller) and under `hourly_budget - SAFETY_MARGIN` (cap 2,000, plan stop 1,980);
        the reserves are ABSOLUTE calls, they do not scale with the cap (below a 1,500 test budget they shrink in proportion):
          P1  classes 0 and 1 (new and hot mint) may use all of it; classes 2, 3 and 4 TOGETHER stop HOT_MINT_RESERVE (400) short;
          classes 2 and 4 (all holders reads together) leave MINT_RESERVE (800) calls an hour to the mint classes;
          P2  class 4 (warm holders) stops WARM_HOLD_RESERVE (600) short, farther than any other class, and comes last: a loaded hour sheds warm holders reads first, then
          cold mint and hot holders reads, and new and hot mint reads last.
        The budget is counted as N3 counts it and is only spent in a different order than v1: a loaded hour sheds holders reads (null holders blocks, check 4) before it
        lets any mint fact go stale (check 2)."""
        with self._lock:
            work = []
            # warm order: the volumes the latest scan took from the arena state, used only if they were taken at or before this pass (never a later state)
            vol = self._warm_vol if (self._warm_vol_ms is not None and self._warm_vol_ms <= now) else {}
            for p in self.pools.values():
                if not p.mint:
                    continue
                need_mint, need_hold = self._due(p, now)
                hot = now < p.hot_until_ms
                # the order of service (Note N4): 0 new mint, 1 hot mint, 2 hot holders, 3 cold mint (oldest fact first), 4 warm holders (highest 60-min volume first)
                if need_mint:
                    age = (p.mints[-1].as_of_ms or 0) if p.mints else 0
                    work.append((0 if not p.mints else (1 if hot else 3), age, p.pool, "mint"))
                if need_hold:
                    if hot:
                        work.append((2, (p.holders[-1].as_of_ms or 0) if p.holders else 0, p.pool, "hold"))
                    else:
                        work.append((4, -vol.get(p.pool, 0.0), p.pool, "hold"))
            work.sort()
        made = 0
        scale = self.hourly_budget / HOURLY_BUDGET  # the margin and the reserve are fractions of the budget (a small test budget keeps its shape)
        hard = self.hourly_budget - int(SAFETY_MARGIN * min(1.0, scale))
        mint_reserve = int(MINT_RESERVE * min(1.0, scale))
        warm_reserve = int(WARM_HOLD_RESERVE * min(1.0, scale))
        for cls, _, pool, kind in work:
            with self._lock:
                p = self.pools.get(pool)
                if p is None:
                    continue
                if kind == "hold" and not p.mints:
                    continue  # no mint fact yet: the holders read needs the token program from it
                now = max(now, self.clock())  # a pass can run minutes: the window is the real one
                spent = self._hour_calls(now)
                hot_reserve = int(HOT_MINT_RESERVE * min(1.0, scale))
                lower = spent - self._hot_spent_last_hour(now)  # everything except new-pool and hot-pool mint reads: cold mint reads and holders reads
                if kind == "mint":
                    est = MINT_EST + (0 if p.mayhems else 1)
                    hot_class = cls in (0, 1)  # a new pool's first read counts with the hot reserve whether or not the pool is hot yet (N3 text, QP 6279836164)
                    ok = spent + est <= hard and (hot_class or lower + est <= hard - hot_reserve)  # the lower classes together stop HOT_MINT_RESERVE short of the cap
                else:
                    est = HOLDERS_EST
                    holders_spent = spent - self._mint_spent_last_hour(now)
                    hot_class = False
                    far = max(hot_reserve, warm_reserve) if cls == 4 else hot_reserve  # P2: warm holders reads stop at least as far short as any other class
                    ok = spent + est <= hard and lower + est <= hard - far and holders_spent + est <= hard - mint_reserve  # and holders alone leave MINT_RESERVE to the mint class
                if not ok:
                    self._n("budget_deferred")
                    self._n("deferred_" + kind)
                    if self.obs is not None and self.obs.enabled:  # distinct pools per class per hour, for the chain_hour row (a pool is deferred on every 5 s pass)
                        hr = now // HOUR_MS
                        by = self._deferred_pools.setdefault(hr, {})
                        by.setdefault(("warm_hold" if cls == 4 else "hold") if kind == "hold" else ("hot_mint" if hot_class else "cold_mint"), set()).add(pool)
                        for old in [k for k in self._deferred_pools if k < hr - 2]:
                            del self._deferred_pools[old]
                    continue
            c0 = self._calls_total()
            self._refresh(p, now, kind == "mint", kind == "hold")
            c1 = self._calls_total()
            used = max(0, c1 - c0) if (c0 is not None and c1 is not None) else est
            self._book(max(now, self.clock()), used, mint=(kind == "mint"), hot=hot_class)  # at the minute the calls were really made (a start-up pass runs minutes)
            if kind == "hold" and cls == 4:
                with self._lock:
                    w = self._warm_by_hour.setdefault(max(now, self.clock()) // HOUR_MS, [0, 0])
                    w[0] += 1
                    w[1] += used
                    for old in [k for k in self._warm_by_hour if k < max(now, self.clock()) // HOUR_MS - 3]:
                        del self._warm_by_hour[old]
            made += used
        self._n("passes")
        self._n("calls", made)
        return made

    # -- observability (side file; nothing here is read by a decision)
    def set_obs(self, obs: Any) -> None:
        """Attach the ObsLog (the runner does it once it has built one) and write the one `prefetch_start` row: what this process loaded (check 8, reload with no reset)."""
        self.obs = obs
        if obs is None or not obs.enabled:
            return
        try:
            c = getattr(self.chain, "counter", None)
            obs.write("prefetch_start", policy=self.policy, hourly_budget=self.hourly_budget, **self._at_load,  # process_start_ms, spent_at_load, spend_state_reset, counter_* at LOAD
                      # R3: what the previous process left (its last 61 per-minute counts [minute_ms, calls, credits] and the running total of all earlier processes),
                      # apart from this process's own record; check 5's restart hour is these minutes plus the old process's chain_hour rows
                      prior_minutes=list(getattr(c, "prior_minutes", None) or []), prior_calls_total=getattr(c, "prior_calls_total", None),
                      prior_minutes_error=getattr(c, "prior_error", None))
        except Exception:  # noqa: BLE001
            self._n("obs_errors")

    def _obs_tick(self, now: int) -> None:
        """One `chain_hour` row per CLOSED UTC hour (this process's own counter record: the per-minute calls, so any rolling hour is exact), written by the thread that
        runs the passes. A process that starts mid-hour marks that hour `partial_since_ms`; the hour that two processes share has one row from each."""
        obs = self.obs
        if obs is None or not obs.enabled:
            return
        h = now // HOUR_MS
        if self._obs_hour is None:
            self._obs_hour = h
            self._obs_start_ms = self._obs_start_ms or now
            return
        if h <= self._obs_hour:
            return
        c = getattr(self.chain, "counter", None)
        rows = []
        for hh in range(max(self._obs_hour, h - 24), h):
            start, end = hh * HOUR_MS, (hh + 1) * HOUR_MS
            mc = c.minute_counts(start, end) if c is not None and hasattr(c, "minute_counts") else None
            day = time.strftime("%Y-%m-%d", time.gmtime(start / 1000))
            rows.append({"kind": "chain_hour", "hour_start_ms": start, "hour_end_ms": end, "pid": os.getpid(), "process_start_ms": self._obs_start_ms,
                         "partial_since_ms": self._obs_start_ms if self._obs_start_ms > start else None, "policy": self.policy,
                         "calls": None if mc is None else sum(m[1] for m in mc), "credits": None if mc is None else sum(m[2] for m in mc),
                         "per_minute_calls": None if mc is None else [m[1] for m in mc], "counter_calls_total": getattr(c, "calls", None),
                         "counter_credits_total": getattr(c, "credits", None), "counter_errors": getattr(c, "errors", None),
                         "counter_day_calls": (getattr(c, "by_day", {}) or {}).get(day), "counter_state_write_errors": getattr(c, "state_write_errors", None),
                         "spend_write_errors": self.stats_.get("spend_write_errors", 0), "spend_state_reset": self.stats_.get("spend_state_reset", 0),
                         "budget_deferred": self.stats_.get("budget_deferred", 0), "deferred_mint": self.stats_.get("deferred_mint", 0),
                         "deferred_hold": self.stats_.get("deferred_hold", 0), "hourly_budget": self.hourly_budget, "prefetch_stats": dict(self.stats_),
                         # distinct pools deferred in this hour, by class (the *_mint / *_hold counters above count every 5 s pass), and the calls this prefetcher booked
                         # in the hour (its spend file's figure, next to the counter's `calls`)
                         "deferred_hot_mint": len(self._deferred_pools.get(hh, {}).get("hot_mint", ())), "deferred_cold_mint": len(self._deferred_pools.get(hh, {}).get("cold_mint", ())),
                         "deferred_hold_pools": len(self._deferred_pools.get(hh, {}).get("hold", ())), "booked_calls_hour": self._booked_by_hour.get(hh, 0),
                         # warm holders (policy v2): distinct pools deferred in the hour, and the reads and calls booked in it
                         "deferred_warm_hold": len(self._deferred_pools.get(hh, {}).get("warm_hold", ())),
                         "warm_holders_reads_hour": self._warm_by_hour.get(hh, [0, 0])[0], "warm_holders_calls_hour": self._warm_by_hour.get(hh, [0, 0])[1]})
        for hh in range(max(self._obs_hour, h - 24), h):
            self._deferred_pools.pop(hh, None)
            self._booked_by_hour.pop(hh, None)
            self._warm_by_hour.pop(hh, None)
        obs.write_many(rows)
        self._obs_hour = h

    @staticmethod
    def _backoff(p: _Pool, kind: str, now: int) -> None:
        """60 s after the first failure of a kind, doubling with each failure in a row, up to 16 min: a pool that never answers does not keep a place in the queue."""
        p.fails[kind] += 1
        p.next_try_ms[kind] = now + min(RETRY_MAX_MS, RETRY_AFTER_MS * 2 ** (p.fails[kind] - 1))

    def _refresh(self, p: _Pool, now: int, need_mint: bool, need_hold: bool) -> None:
        mf = p.mints[-1] if p.mints else None
        if need_mint:
            got = self.chain.mint_facts(p.mint, force=True)
            if getattr(got, "ok", False) and got.as_of_ms is not None and got.as_of_slot is not None:
                with self._lock:
                    p.mints.append(got)
                p.fails["mint"] = 0
                mf = got
                self._n("mint_refreshed")
            else:
                self._backoff(p, "mint", now)
                self._n("mint_errors")
        if mf is None:
            return
        if not p.mayhems:  # the flag is set at creation and never changes: one read, then the chain object's cache answers
            mh = self.chain.mayhem_flag(p.mint, pool=p.pool)
            if mh.is_mayhem is not None and mh.as_of_ms is not None:
                with self._lock:
                    p.mayhems.append((mh.is_mayhem, mh.as_of_ms))
                self._n("mayhem_refreshed")
            else:
                self._n("mayhem_errors")
        if need_hold:
            hd = self.chain.largest_holders(p.mint, token_program=mf.token_program_id, pool=p.pool, max_age_s=0)
            if getattr(hd, "ok", False) and hd.as_of_ms is not None:
                with self._lock:
                    p.holders.append(hd)
                p.fails["holders"] = 0
                self._n("holders_refreshed")
            else:
                self._backoff(p, "holders", now)
                self._n("holders_errors")

    # -- thread
    def start(self) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.run_once()
                except Exception:  # noqa: BLE001 - a refresh failure must never kill the thread; the next pass retries
                    self._n("pass_errors")
                self._stop.wait(CYCLE_S)

        self._thread = threading.Thread(target=loop, name="hv4-chain-prefetch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = self.clock()
            return {**self.stats_, "pools": len(self.pools), "hot": sum(1 for p in self.pools.values() if now < p.hot_until_ms),
                    "calls_last_hour": self._spent_last_hour(now), "hourly_budget": self.hourly_budget, "holders_on": self.holders_on, "policy": self.policy}


@dataclass(frozen=True)
class _Flag:
    """The stored Mayhem answer, shaped like hv4_chain.MayhemFlag for `compose`."""
    is_mayhem: bool | None
