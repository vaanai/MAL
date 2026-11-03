"""Counts-only probe of one premise of h5-lane-oc (PR #1220): the PumpSwap pool's BOOST vault SPL `amount` is the remaining BOOST budget and falls by
exactly the BOOST spend.

`poll`: every period, find pools the shadow still follows (boost_slice rows; ONLY type, pool, slot, s0, sps are read; s0 + POOL_LIFE_S/sps still ahead of the
newest slot seen), and do one getAccountInfo of each pool's BOOST vault over public RPC (no key). One row per read goes to --out: pool, vault, ctx_slot,
amount, reason, t_ms.
`join`: for consecutive reads (a, b) of a pool with ctx_slot_a < ctx_slot_b, compare delta = amount_a - amount_b with S = sum of `lamports` of that pool's
deduped boost_slice rows with slot in (ctx_slot_a, ctx_slot_b]. ONLY boost_slice pool, slot, lamports, s0, sps, signature, event_index, tx_index; gap / start / stop /
hb t_ms; gap from_slot, to_slot; hb last_event_ms are read. A pair is scored only on positive evidence the shadow was healthy (Coverage.scored), else it is
counted as `gap`; pairs outside the follow window and of pools with no slices are counted apart. Prints counts
only; never a price, reserve, outcome, PnL or trigger field. 0 Helius credits. Memory: streams files line by line; pool/slice dicts only.
"""
from __future__ import annotations

import argparse
import bisect
import glob
import inspect
import json
import calendar
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from typing import Any, Callable

from tools.h5_lane_oc_executor import boost_vault_address, parse_boost_vault
from tools.h5_shadow import POOL_LIFE_S, SILENCE_S, housekeeping  # the shadow follows a pool from its first print to s0 + POOL_LIFE_S, then drops it

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
FALLBACK_SPS = 0.5  # seconds per slot when a pool's slices carry no `sps`: conservative (a slower slot means FEWER slots inside POOL_LIFE_S)
COVER_BACK_MS = int((POOL_LIFE_S + 30) * 1000)  # a pair needs a healthy shadow from this far before read a (a pool's follow time plus 30 s)
END_MARGIN_MS = 5_000  # ... to this far after read b for gap / start / stop rows
HB_PERIOD_MS = int(inspect.signature(housekeeping).parameters["hb_s"].default * 1000)  # the shadow's heartbeat period (housekeeping hb_s default)
HB_MAX_SPACING_MS = 2 * HB_PERIOD_MS
SILENCE_MS = int(SILENCE_S * 1000)  # the shadow's own silence threshold
# The shadow writes compact JSON (separators (",", ":")); both spacings are accepted. A line is parsed only if it carries one of these exact type strings.
SLICE_TAGS = ('"type":"boost_slice"', '"type": "boost_slice"')
GAP_TAGS = ('"type":"gap"', '"type": "gap"')
STOP_TAGS = ('"type":"stop"', '"type": "stop"')
HB_TAGS = ('"type":"hb"', '"type": "hb"')
START_TAGS = ('"type":"start"', '"type": "start"')
HOUR_RE = re.compile(r"(\d{4})\D?(\d{2})\D?(\d{2})\D?(\d{2})")


def _has(line: str, tags: tuple) -> bool:
    return any(t in line for t in tags)


def _hour_range_ms(path: str) -> tuple[int, int] | None:
    m = HOUR_RE.search(os.path.basename(path))
    if not m:
        return None
    try:
        y, mo, d, h = (int(g) for g in m.groups())
        lo = calendar.timegm((y, mo, d, h, 0, 0)) * 1000
    except (ValueError, OverflowError):
        return None
    return lo, lo + 3_600_000
MAX_CALLS_PER_S = 4.0
HOUR_FILES_SCANNED = 2


def _hour_files(shadow_dir: str) -> list[str]:
    return sorted(glob.glob(os.path.join(shadow_dir, "h5-shadow-*.jsonl")))


def _ok_int(x: Any) -> bool:
    return type(x) is int


def _sps_ok(x: Any) -> bool:
    return type(x) in (int, float) and 0.05 <= x <= 5.0


def horizon_slots(sps: float | None) -> int:
    """Slots the shadow follows a pool for: POOL_LIFE_S / sps. `sps` is the shadow's own per-slice `sps` (the largest seen for the pool, i.e. the fewest
    slots); FALLBACK_SPS if none."""
    return int(POOL_LIFE_S / (sps if sps is not None else FALLBACK_SPS))


def recent_pools(shadow_dir: str, max_pools: int) -> list[str]:
    """Pools the shadow is still following: s0 + horizon is ahead of the newest slot seen. Newest s0 first, capped.
    Reads only type, pool, slot, s0, sps of boost_slice rows (a `pool` record carries no slot, so it cannot place a pool)."""
    info: dict[str, list] = {}  # pool -> [s0, max sps]
    newest = -1
    for path in _hour_files(shadow_dir)[-HOUR_FILES_SCANNED:]:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not _has(line, SLICE_TAGS):
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("type") != "boost_slice":
                    continue
                pool, slot, s0, sps = row.get("pool"), row.get("slot"), row.get("s0"), row.get("sps")
                if not (isinstance(pool, str) and _ok_int(slot) and _ok_int(s0)):
                    continue
                newest = max(newest, slot)
                cur = info.setdefault(pool, [s0, None])
                cur[0] = min(cur[0], s0)
                if _sps_ok(sps) and (cur[1] is None or sps > cur[1]):
                    cur[1] = float(sps)
    keep = [(s0, p) for p, (s0, sps) in info.items() if s0 + horizon_slots(sps) > newest]
    keep.sort(key=lambda t: (-t[0], t[1]))
    return [p for _s, p in keep[:max_pools]]


class RateLimited(Exception):
    pass


def http_rpc(url: str, timeout: float = 5.0) -> Callable[[str, list], dict]:
    def call(method: str, params: list) -> dict:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimited() from e
            raise
    return call


class Limiter:
    def __init__(self, per_s: float = MAX_CALLS_PER_S, clock=time.monotonic, sleep=time.sleep):
        self.gap, self.clock, self.sleep, self.next_ok = 1.0 / per_s, clock, sleep, 0.0

    def wait(self) -> None:
        now = self.clock()
        if now < self.next_ok:
            self.sleep(self.next_ok - now)
            now = self.next_ok
        self.next_ok = now + self.gap

    def penalty(self, seconds: float) -> None:
        self.next_ok = max(self.next_ok, self.clock()) + seconds


def read_vault(rpc: Callable[[str, list], dict], pool: str, limiter: Limiter, now_ms: Callable[[], int]) -> dict[str, Any]:
    vault, _auth = boost_vault_address(pool)

    def row(ctx, amount, reason):
        return {"pool": pool, "vault": vault, "ctx_slot": ctx, "amount": amount, "reason": reason, "t_ms": now_ms()}

    backoff = 2.0
    for _ in range(5):
        limiter.wait()
        try:
            res = rpc("getAccountInfo", [vault, {"encoding": "base64", "commitment": "confirmed"}])
        except RateLimited:
            limiter.penalty(backoff)
            backoff = min(backoff * 2, 60.0)
            continue
        except Exception as e:  # network error: one null row, keep going
            return row(None, None, f"rpc_error_{type(e).__name__}")
        r = res.get("result") if isinstance(res, dict) else None
        if not isinstance(r, dict):
            return row(None, None, "rpc_no_result")
        ctx = (r.get("context") or {}).get("slot")
        amount, reason = parse_boost_vault(r.get("value"), pool)
        return row(ctx if type(ctx) is int else None, amount, reason)
    return row(None, None, "rate_limited")


def poll(shadow_dir: str, out: str, period_s: float, max_pools: int, end_epoch: float, rpc, *, clock=time.time, sleep=time.sleep,
         limiter: Limiter | None = None) -> int:
    limiter = limiter or Limiter()
    n = 0
    while clock() < end_epoch:
        t0 = clock()
        pools = recent_pools(shadow_dir, max_pools)
        with open(out, "a", encoding="utf-8") as fh:
            for pool in pools:
                if clock() >= end_epoch:
                    break
                fh.write(json.dumps(read_vault(rpc, pool, limiter, lambda: int(clock() * 1000)), sort_keys=True) + "\n")
                fh.flush()
                n += 1
        left = period_s - (clock() - t0)
        if clock() + max(left, 0) >= end_epoch:
            break
        if left > 0:
            sleep(left)
    return n


def load_reads(path: str) -> tuple[dict[str, list[tuple]], Counter]:
    """(pool -> [(ctx_slot, amount, t_ms)] in file order, reads-by-reason Counter)."""
    by_pool: dict[str, list[tuple]] = defaultdict(list)
    reasons: Counter = Counter()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and isinstance(r.get("pool"), str):
                by_pool[r["pool"]].append((r.get("ctx_slot"), r.get("amount"), r.get("t_ms")))
                reasons[str(r.get("reason"))] += 1
    return by_pool, reasons


def slice_key(pool: str, row: dict) -> tuple:
    sig = row.get("signature")
    if isinstance(sig, str) and sig:
        return (pool, sig, row.get("event_index"))
    return (pool, row.get("slot"), row.get("tx_index"), row.get("lamports"))


class Coverage:
    """Positive evidence the shadow was healthy around a read pair. Built from gap / start / stop rows (t_ms, and slot_jump from_slot / to_slot) and hb rows
    (t_ms, last_event_ms)."""

    def __init__(self) -> None:
        self.events: list[int] = []  # t_ms of gap, start and stop rows
        self.jumps: list[tuple[int, int]] = []  # slot_jump (from_slot, to_slot)
        self.unusable: list[tuple[int, int] | None] = []  # gap / start / stop rows with no usable t_ms: their file's UTC hour (None = unknown)
        self.hbs: list[tuple[int, Any]] = []  # (t_ms, last_event_ms)

    def finish(self) -> None:
        self.events.sort()
        self.hbs.sort(key=lambda h: h[0])
        self._hb_t = [h[0] for h in self.hbs]

    def scored(self, sa: int, sb: int, ta: Any, tb: Any, life_slots: int) -> bool:
        """True only if ALL hold: (1) no gap / start / stop row with t_ms in [ta - 430 s, tb + 5 s], no slot_jump overlapping (sa - life_slots, sb], no
        unusable-time row of the hour; (2) hb rows cover [ta - 430 s, tb]: one at or before ta - 430 s, one at or after tb, no two consecutive more than
        2 x the hb period apart; (3) at every hb of that chain the feed was not silent (t_ms - last_event_ms <= the shadow's silence threshold)."""
        if not (_ok_int(ta) and _ok_int(tb)) or tb < ta:
            return False
        lo = ta - COVER_BACK_MS
        i = bisect.bisect_left(self.events, lo)
        if i < len(self.events) and self.events[i] <= tb + END_MARGIN_MS:
            return False
        if any(fs < sb and ts > sa - life_slots for fs, ts in self.jumps):
            return False
        for hour in self.unusable:
            if hour is None or (hour[0] - END_MARGIN_MS <= tb and hour[1] + END_MARGIN_MS >= lo):
                return False
        j = bisect.bisect_right(self._hb_t, lo) - 1  # the last hb at or before lo
        if j < 0:
            return False
        prev = None
        for t, last in self.hbs[j:]:
            if prev is not None and t - prev > HB_MAX_SPACING_MS:
                return False
            if not _ok_int(last) or t - last > SILENCE_MS or last > t:
                return False
            prev = t
            if t >= tb:
                return True
        return False  # no hb at or after tb


def load_shadow(shadow_dir: str, pools: set[str]) -> tuple[dict, dict, Coverage, int]:
    """(slices, pool_meta, coverage, dupes_removed). Reads ONLY: boost_slice pool, slot, lamports, s0, sps, signature, event_index, tx_index; gap from_slot,
    to_slot, t_ms; start / stop / gap / hb t_ms; hb last_event_ms. A line is parsed only if it carries the exact `"type":"<boost_slice|gap|start|stop|hb>"`
    string (so an outcome or trigger line with a `"gap":true` key is never parsed)."""
    slices: dict[str, list[tuple[int, int]]] = defaultdict(list)
    meta: dict[str, list] = {}  # pool -> [s0, max sps]
    cov = Coverage()
    seen: set = set()
    dupes = 0
    for path in _hour_files(shadow_dir):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not (_has(line, SLICE_TAGS) or _has(line, GAP_TAGS) or _has(line, START_TAGS) or _has(line, STOP_TAGS) or _has(line, HB_TAGS)):
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                typ = row.get("type")
                if typ == "hb":
                    if _ok_int(row.get("t_ms")):
                        cov.hbs.append((row["t_ms"], row.get("last_event_ms")))
                    continue
                if typ in ("gap", "start", "stop"):
                    tm = row.get("t_ms")
                    if _ok_int(tm):
                        cov.events.append(tm)
                    else:
                        cov.unusable.append(_hour_range_ms(path))
                    fs, ts = row.get("from_slot"), row.get("to_slot")
                    if typ == "gap" and _ok_int(fs) and _ok_int(ts):
                        cov.jumps.append((fs, ts))
                    continue
                if typ != "boost_slice":
                    continue
                pool, slot, lam, s0, sps = row.get("pool"), row.get("slot"), row.get("lamports"), row.get("s0"), row.get("sps")
                if pool not in pools or not (_ok_int(slot) and _ok_int(lam)):
                    continue
                k = slice_key(pool, row)
                if k in seen:
                    dupes += 1
                    continue
                seen.add(k)
                slices[pool].append((slot, lam))
                if _ok_int(s0):
                    m = meta.setdefault(pool, [s0, None])
                    m[0] = min(m[0], s0)
                    if _sps_ok(sps) and (m[1] is None or sps > m[1]):
                        m[1] = float(sps)
    cov.finish()
    return slices, meta, cov, dupes


def join(reads_path: str, shadow_dir: str) -> dict[str, Any]:
    """Pair buckets, in this order of precedence: skipped (null amount / slot, or ctx slots not increasing; not in pairs_total), no_slices (the pool has no
    boost_slice row at all: sealed, or never BOOST-spent), uncovered (ctx_slot_b > s0 + POOL_LIFE_S / sps, `sps` = the shadow's own slice sps, the largest
    seen for the pool, FALLBACK_SPS if none), gap (no positive evidence the shadow was healthy: see Coverage.scored), boundary, then active exact / mismatch and quiet."""
    by_pool, reasons = load_reads(reads_path)
    slices, meta, cov, dupes = load_shadow(shadow_dir, set(by_pool))
    res: dict[str, Any] = {"pairs_total": 0, "pairs_skipped_null": 0, "pairs_skipped_order": 0, "no_slices": 0, "uncovered": 0, "gap": 0, "boundary": 0,
                           "active": 0, "active_exact": 0, "active_mismatch": 0, "active_mismatch_delta_minus_S": [], "quiet_delta0": 0,
                           "quiet_delta_nonzero": 0, "quiet_nonzero_deltas": [], "slice_dupes_removed": dupes, "pools_read": len(by_pool),
                           "reads_total": sum(reasons.values()), "reads_by_reason": dict(reasons)}
    for pool, rs in by_pool.items():
        sl = slices.get(pool, [])
        m = meta.get(pool)
        for a, b in zip(rs, rs[1:]):
            (sa, amt_a, ta), (sb, amt_b, tb) = a, b
            if not (_ok_int(sa) and _ok_int(sb) and _ok_int(amt_a) and _ok_int(amt_b)):
                res["pairs_skipped_null"] += 1
                continue
            if not sa < sb:
                res["pairs_skipped_order"] += 1
                continue
            res["pairs_total"] += 1
            if not sl or m is None:
                res["no_slices"] += 1
                continue
            if sb > m[0] + horizon_slots(m[1]):
                res["uncovered"] += 1
                continue
            if not cov.scored(sa, sb, ta, tb, horizon_slots(m[1])):
                res["gap"] += 1
                continue
            if any(s == sa or s == sb for s, _ in sl):
                res["boundary"] += 1  # ordering of a slice at a read slot is ambiguous: not scored as exact, mismatch or quiet
                continue
            delta = amt_a - amt_b
            S = sum(lam for s, lam in sl if sa < s <= sb)
            if S > 0:
                res["active"] += 1
                if delta == S:
                    res["active_exact"] += 1
                else:
                    res["active_mismatch"] += 1
                    res["active_mismatch_delta_minus_S"].append(delta - S)
            elif delta == 0:
                res["quiet_delta0"] += 1
            else:
                res["quiet_delta_nonzero"] += 1
                res["quiet_nonzero_deltas"].append(delta)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="BOOST vault premise probe (counts only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("poll")
    p.add_argument("--shadow-dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--period-s", type=float, default=20.0)
    p.add_argument("--max-pools", type=int, default=40)
    p.add_argument("--end-epoch", type=float, required=True)
    p.add_argument("--rpc", default=DEFAULT_RPC)
    j = sub.add_parser("join")
    j.add_argument("--reads", required=True)
    j.add_argument("--shadow-dir", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "poll":
        print(f"reads written: {poll(a.shadow_dir, a.out, a.period_s, a.max_pools, a.end_epoch, http_rpc(a.rpc))}")
        return 0
    for k, v in join(a.reads, a.shadow_dir).items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
