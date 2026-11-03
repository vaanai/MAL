"""Counts-only probe of one premise of h5-lane-oc (PR #1220): the PumpSwap pool's BOOST vault SPL `amount` is the remaining BOOST budget and falls by
exactly the BOOST spend.

`poll`: every period, find pools in the newest shadow hour files (rows of type "pool" or "boost_slice"; ONLY the fields type, pool, slot are read), and do one
getAccountInfo of each pool's BOOST vault over public RPC (no key). One row per read goes to --out: pool, vault, ctx_slot, amount, reason, t_ms.
`join`: for consecutive reads (a, b) of a pool with ctx_slot_a < ctx_slot_b, compare delta = amount_a - amount_b with S = sum of `lamports` of that pool's
boost_slice rows with slot in (ctx_slot_a, ctx_slot_b]. ONLY boost_slice fields pool, slot, lamports are read. Prints counts only; never a price, reserve,
outcome, PnL or trigger field. 0 Helius credits. Memory: streams files line by line; pool/slice dicts only.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from typing import Any, Callable

from tools.h5_lane_oc_executor import boost_vault_address, parse_boost_vault

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
SLOTS_PER_MIN = 150  # ~400 ms slots
WINDOW_SLOTS = 60 * SLOTS_PER_MIN  # "last 60 min", measured in slots from the newest slot seen (rows carry slot)
MAX_CALLS_PER_S = 4.0
HOUR_FILES_SCANNED = 2
POOL_TYPES = ("pool", "boost_slice")


def _hour_files(shadow_dir: str) -> list[str]:
    return sorted(glob.glob(os.path.join(shadow_dir, "h5-shadow-*.jsonl")))


def recent_pools(shadow_dir: str, max_pools: int, window_slots: int = WINDOW_SLOTS) -> list[str]:
    """Pools seen within `window_slots` of the newest slot, newest first, capped. Reads only type, pool, slot."""
    last: dict[str, int] = {}
    for path in _hour_files(shadow_dir)[-HOUR_FILES_SCANNED:]:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"pool"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("type") not in POOL_TYPES:
                    continue
                pool, slot = row.get("pool"), row.get("slot")
                if isinstance(pool, str) and type(slot) is int and slot > last.get(pool, -1):
                    last[pool] = slot
    if not last:
        return []
    newest = max(last.values())
    keep = [(s, p) for p, s in last.items() if s >= newest - window_slots]
    keep.sort(key=lambda sp: (-sp[0], sp[1]))
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


def load_reads(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and isinstance(r.get("pool"), str):
                rows.append({k: r.get(k) for k in ("pool", "ctx_slot", "amount", "reason")})
    return rows


def load_slices(shadow_dir: str, pools: set[str]) -> dict[str, list[tuple[int, int]]]:
    """boost_slice rows of the wanted pools. Reads ONLY pool, slot, lamports."""
    out: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for path in _hour_files(shadow_dir):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"boost_slice"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("type") != "boost_slice":
                    continue
                pool, slot, lam = row.get("pool"), row.get("slot"), row.get("lamports")
                if pool in pools and type(slot) is int and type(lam) is int:
                    out[pool].append((slot, lam))
    return out


def _isint(x: Any) -> bool:
    return type(x) is int


def join(reads_path: str, shadow_dir: str) -> dict[str, Any]:
    reads = load_reads(reads_path)
    by_pool: dict[str, list[dict]] = defaultdict(list)
    for r in reads:
        by_pool[r["pool"]].append(r)
    slices = load_slices(shadow_dir, set(by_pool))
    res: dict[str, Any] = {"pairs_total": 0, "boundary": 0, "active": 0, "active_exact": 0, "active_mismatch": 0, "active_mismatch_delta_minus_S": [],
                           "quiet_delta0": 0, "quiet_delta_nonzero": 0, "quiet_nonzero_deltas": [], "pools_read": len(by_pool),
                           "reads_total": len(reads), "reads_by_reason": dict(Counter(str(r["reason"]) for r in reads))}
    for pool, rs in by_pool.items():
        sl = slices.get(pool, [])
        for a, b in zip(rs, rs[1:]):
            if not (_isint(a["ctx_slot"]) and _isint(b["ctx_slot"]) and a["ctx_slot"] < b["ctx_slot"] and _isint(a["amount"]) and _isint(b["amount"])):
                continue
            res["pairs_total"] += 1
            sa, sb = a["ctx_slot"], b["ctx_slot"]
            if any(s == sa or s == sb for s, _ in sl):
                res["boundary"] += 1  # ordering of a slice at a read slot is ambiguous: not scored as exact, mismatch or quiet
                continue
            delta = a["amount"] - b["amount"]
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
