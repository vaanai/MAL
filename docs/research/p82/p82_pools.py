"""P82 step 1a: distinct H5 frozen pools (meta only; no P&L). Usage: python p82_pools.py OUT.json"""
import json, sys
import pandas as pd
P = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"
FROZEN = "/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet"
fz = pd.read_parquet(FROZEN, columns=["day", "mint", "set"])
out = {}
for day, xd in fz.groupby("day"):
    meta = pd.read_parquet(f"{P}/meta/{day}.parquet", columns=["mint", "pool", "npools", "v"])
    m = meta[meta.mint.isin(xd.mint)]
    for r in m.itertuples():
        out[r.mint] = dict(pool=r.pool, npools=int(r.npools), day=day)
print(len(fz), "frozen trades;", len(out), "mints with meta;", len({v["pool"] for v in out.values()}), "distinct pools;", sum(v["npools"] > 1 for v in out.values()), "mints npools>1")
json.dump(out, open(sys.argv[1], "w"))
