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
DROP_AFTER_MS = 30 * MIN_MS  # a pool that has not passed the filters for this long leaves the store
RETRY_AFTER_MS = 60_000  # after a failed read of a kind, wait this long before trying that kind again; doubles with each failure in a row, up to RETRY_MAX_MS
RETRY_MAX_MS = 16 * MIN_MS
SPEND_SCHEMA = "hv4_prefetch.spend.v1"
HOURLY_BUDGET = 1_500
COLD_SHARE = 0.6
CYCLE_S = 5.0
KEEP = 3  # facts of each kind kept per pool, so a fact stamped after T can be skipped for an older one

assert HOT_HOLDERS_REFRESH_MS < C.CHAIN_HOLDERS_MAX_AGE_MS and MINT_REFRESH_MS < C.CHAIN_MINT_MAX_AGE_MS


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
                 hourly_budget: int = HOURLY_BUDGET, spend_path: str | os.PathLike | None = None) -> None:
        self.chain, self.compose = chain, compose
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)
        provider = getattr(getattr(chain, "rpc", None), "provider", None)
        self.holders_on = (provider != "public") if holders is None else holders  # the public endpoint refuses getTokenLargestAccounts: do not spend calls on it
        self.hourly_budget = hourly_budget
        self.pools: dict[str, _Pool] = {}
        self._spent: deque[list[int]] = deque()  # [minute bucket ms, calls] in the last hour
        self.spend_path = spend_path
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats_: dict[str, int] = {}
        if spend_path is not None:
            self._load_spend()

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

    def _book(self, now: int, used: int) -> None:
        """Add `used` calls to the rolling hour (minute buckets, so the persisted file stays small) and save."""
        used = max(0, int(used))
        b = now - now % MIN_MS
        with self._lock:
            if self._spent and self._spent[-1][0] == b:
                self._spent[-1][1] += used
            else:
                self._spent.append([b, used])
            self._save_spend()

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
                json.dump({"schema": SPEND_SCHEMA, "spent": [list(r) for r in self._spent]}, f, sort_keys=True)
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
    def watch(self, items: Iterable[tuple[str, str | None]], hot: Iterable[str], now_ms: int) -> None:
        """The pools that pass the filters at this scan, and the ones about to be carded (tagged, on the board, top by volume)."""
        hot = set(hot)
        with self._lock:
            for pool, mint in items:
                p = self.pools.get(pool)
                if p is None:
                    p = self.pools[pool] = _Pool(pool, mint, now_ms, now_ms)
                    self._n("pools_added")
                p.last_seen_ms = now_ms
                if mint and p.mint != mint:
                    p.mint = mint
                if pool in hot:
                    p.hot_until_ms = now_ms + HOT_LINGER_MS
            for pool in [k for k, p in self.pools.items() if now_ms - p.last_seen_ms > DROP_AFTER_MS]:
                del self.pools[pool]
                self._n("pools_dropped")

    def part_at(self, pool: str, mint: str | None, T: int, slot: int) -> dict | None:
        """The chain dict for a card at scan instant T: only facts stamped strictly before T, no RPC. None when the pool has no usable mint fact."""
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
            flag = next(((v, ms) for v, ms in reversed(p.mayhems) if ms < T), None)
            hd = next((h for h in reversed(p.holders) if h.as_of_ms is not None and h.as_of_ms < T and _slot_ok(h.as_of_slot, slot)), None)
            if hd is not None and T - hd.as_of_ms > C.CHAIN_HOLDERS_MAX_AGE_MS:
                self._n("served_holders_omitted_stale")
                hd = None
            self._n("served_ok")
            return self.compose(mf, _Flag(flag[0] if flag else None), hd)

    # -- the worker's side
    def _due(self, p: _Pool, now: int) -> tuple[bool, bool]:
        mint_age = (now - p.mints[-1].as_of_ms) if p.mints and p.mints[-1].as_of_ms is not None else None
        need_mint = (mint_age is None or mint_age >= MINT_REFRESH_MS) and now >= p.next_try_ms["mint"]
        hot = now < p.hot_until_ms
        hold_age = (now - p.holders[-1].as_of_ms) if p.holders and p.holders[-1].as_of_ms is not None else None
        need_hold = (self.holders_on and hot and (hold_age is None or hold_age >= HOT_HOLDERS_REFRESH_MS) and now >= p.next_try_ms["holders"])
        return need_mint, need_hold

    def run_once(self, now_ms: int | None = None) -> int:
        """One pass: every due pool, new first, then hot, then cold, oldest fact first, until the hourly budget is spent. Returns the calls made."""
        now = self.clock() if now_ms is None else now_ms
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
                    "calls_last_hour": self._spent_last_hour(now), "hourly_budget": self.hourly_budget, "holders_on": self.holders_on}


@dataclass(frozen=True)
class _Flag:
    """The stored Mayhem answer, shaped like hv4_chain.MayhemFlag for `compose`."""
    is_mayhem: bool | None
