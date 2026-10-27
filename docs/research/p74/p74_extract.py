"""P74 step 1: pull every row (bonding + pumpswap) of mints that reach >=85% bonding progress, exploration blocks only.
Run: /data/mal/audit-1008/venv/bin/python p74_extract.py OUT.parquet
"""
import glob, sys
import numpy as np, pyarrow as pa, pyarrow.parquet as pq, pyarrow.compute as pc

BLOCKS = ["explore-0814", "fresh-0903", "exp011-0909"]
TOK_OFF = 279_900_000_000_000
INIT = 793_100_000 * 10**6
COLS = ["venue", "mint", "side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve", "slot", "tx_index", "event_index", "block_time", "block", "hour"]
files = sorted(glob.glob("/data/mal/audit-1008/tape/trades/*.parquet"))
cand = set()
hours = []
for f in files:
    t = pq.read_table(f, columns=COLS, filters=[("block", "in", BLOCKS)])
    if t.num_rows == 0:
        continue
    hours.append(f)
    b = t.filter(pc.equal(t["venue"], "pump_bonding"))
    prog = 1.0 - np.maximum(0, b["base_reserve"].to_numpy(zero_copy_only=False) - TOK_OFF) / INIT
    m = prog >= 0.85
    cand.update(b["mint"].filter(pa.array(m)).unique().to_pylist())
print("hours", len(hours), "candidate mints", len(cand), flush=True)
cl = pa.array(sorted(cand))
out = []
for f in hours:
    t = pq.read_table(f, columns=COLS, filters=[("block", "in", BLOCKS)])
    out.append(t.filter(pc.is_in(t["mint"], value_set=cl)))
res = pa.concat_tables(out)
pq.write_table(res, sys.argv[1])
print("rows", res.num_rows)
