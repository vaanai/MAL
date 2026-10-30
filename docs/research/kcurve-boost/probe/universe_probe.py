#!/usr/bin/env python3
"""kcurve-boost universe probe: STRUCTURE ONLY (s0 opening state, creates, Mayhem). No print after a pool's first slot is used.
Per canonical pool (WSOL, V in band) whose first in-sample print slot is m: the root print of slot m, its pre-trade (q, b);
the mint's create row in the same block (time, mayhem); organic = s0 - create >= 60 s.
Usage: universe_probe.py BLOCK HOUR [HOUR ...]   (hours as YYYY-MM-DDTHH)"""
import collections
import glob
import json
import os
import subprocess
import sys

import numpy as np

V_LO, V_HI = 17_500_000_000, 17_700_000_000
VMAPS = [json.load(open(p))["v"] for p in ("/data/mal/pumpswap-virtual/pool_v_0814.json", "/data/mal/pumpswap-virtual/pool_v_0909.json")]
ALLOWED = ("/data/mal/clean-view/explore-0814/", "/data/mal/clean-view/oracle-insample-2026-09-22_25", "/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00",
           "/data/mal/clean-view/oracle-live-2026-09-25_27")


def vget(pool):
    for m in VMAPS:
        v = m.get(pool)
        if v is not None:
            return float(v)
    return None


def zcat(path):
    if path.endswith(".zst"):
        return subprocess.run(["zstd", "-dc", "-q", path], capture_output=True, check=True).stdout.splitlines()
    return open(path, "rb").read().splitlines()


def creates_of(block):
    out = {}
    for p in sorted(glob.glob(f"{block}/creates/*")):
        for l in zcat(p):
            r = json.loads(l)
            if r.get("type") == "create":
                out[r["mint"]] = (r.get("block_time") or r.get("event_ts"), r.get("is_mayhem_mode"), r.get("signature"))
            elif r.get("type") == "ingest_hot" and r.get("txType") == "create":
                wp = r.get("ws_payload") or {}
                t = r.get("t_ws")
                from datetime import datetime
                ts = datetime.fromisoformat(t).timestamp() if t else None
                out[r["mint"]] = (ts, wp.get("is_mayhem_mode"), r.get("signature"))
    return out


def main():
    block, hours = sys.argv[1].rstrip("/"), sys.argv[2:]
    assert any(block.startswith(a.rstrip("/")) for a in ALLOWED), block
    cr = creates_of(block)
    first = {}
    for h in hours:
        p = glob.glob(f"{block}/trades/trades-{h}.jsonl.zst")
        assert len(p) == 1, (h, p)
        for l in zcat(p[0]):
            if b'"pumpswap"' not in l or b'"quote_is_wsol":true' not in l:
                continue
            r = json.loads(l)
            pool = r["pool"]
            f = first.get(pool)
            if f is None:
                first[pool] = [r["slot"], [r]]
            elif r["slot"] == f[0]:
                f[1].append(r)
    stats = collections.Counter()
    b0s, q0s, ages = [], [], []
    for pool, (m, rows) in first.items():
        v = vget(pool)
        if v is None or not (V_LO <= v <= V_HI):
            stats["not_vband"] += 1
            continue
        stats["vband_pools_first_seen"] += 1
        post_b = {(r["base_reserve"] - r["token_raw"]) if r["side"] == "buy" else (r["base_reserve"] + r["token_raw"]) for r in rows}
        roots = [r for r in rows if r["base_reserve"] not in post_b]
        if len(roots) != 1:
            stats["root_not_unique"] += 1
            continue
        r0 = roots[0]
        b0s.append(r0["base_reserve"])
        q0s.append(r0["quote_reserve"])
        seed = r0["base_reserve"] == 206_900_000_000_000
        stats["root_b_is_seed"] += seed
        if not seed:
            continue
        mint = r0["mint"]
        c = cr.get(mint)
        s0 = r0.get("block_time") or r0.get("event_ts")
        if c is None:
            stats["seed_no_create_in_block"] += 1
            continue
        stats["seed_with_create"] += 1
        stats[f"seed_mayhem_{c[1]}"] += 1
        age = s0 - c[0] if (s0 and c[0]) else None
        if age is not None:
            ages.append(age)
            stats["seed_organic_ge60"] += age >= 60
    print(json.dumps({"block": block, "hours": f"{hours[0]}..{hours[-1]} ({len(hours)})", **stats,
                      "root_b0_top": collections.Counter(b0s).most_common(3),
                      "root_q0_sol_pct": {q: round(float(np.percentile(np.array(q0s) / 1e9, q)), 4) for q in (1, 5, 50, 95, 99)} if q0s else None,
                      "age_s_pct": {q: round(float(np.percentile(ages, q)), 1) for q in (5, 25, 50, 75, 95)} if ages else None}))


if __name__ == "__main__":
    main()
