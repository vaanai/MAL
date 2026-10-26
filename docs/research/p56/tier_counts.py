#!/usr/bin/env python3
"""P56 counts-only: PumpSwap trades/pools/volume/depth per fee tier, per UTC day.
Exploration block only (caller passes hour files). No outcomes, no P&L. Pre-trade reserves (PumpSwap convention).
Usage: tier_counts.py OUT.json FILE.jsonl.zst ...
Filter: venue pumpswap, quote is wSOL, mint ends with "pump". Market cap = quote_SOL * 1e9 supply / base_UI
(raw base has 6 decimals: base_raw/1e6 UI, so mc = q/1e9 * 1e9 / (b/1e6) = q/1e9 * 1e15 / b).
"""
import sys, json, subprocess, bisect
from multiprocessing import Pool
sys.path.insert(0, ".")
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS as T
TH = [a for a, _ in T]


def tier(m):
    return bisect.bisect_right(TH, m + 1e-9) - 1


def one(path):
    out = {}
    p = subprocess.Popen(["zstdcat", path], stdout=subprocess.PIPE)
    for ln in p.stdout:
        if b'"venue":"pumpswap"' not in ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if not r.get("quote_is_wsol") or not str(r.get("mint", "")).endswith("pump"):
            continue
        q, b = r["quote_reserve"], r["base_reserve"]
        if not q or not b:
            continue
        mc = q / 1e9 * 1e15 / b
        t = tier(mc)
        day = (r.get("block_time") or r.get("event_ts") or 0) // 86400
        o = out.setdefault(f"{day}|{t}", [0, 0, set(), []])
        o[0] += 1
        o[1] += r["sol_lamports"]
        o[2].add(r["pool"])
        if o[0] % 50 == 1:
            o[3].append(q / 1e9)
    p.wait()
    return {k: [v[0], v[1], sorted(v[2]), v[3]] for k, v in out.items()}


if __name__ == "__main__":
    res = Pool(8).map(one, sys.argv[2:])
    agg = {}
    for d in res:
        for k, v in d.items():
            o = agg.setdefault(k, [0, 0, set(), []])
            o[0] += v[0]
            o[1] += v[1]
            o[2].update(v[2])
            o[3] += v[3]
    json.dump({k: [v[0], v[1], len(v[2]), v[3]] for k, v in agg.items()}, open(sys.argv[1], "w"))
