"""P75 step 1: pull every row (bonding + pumpswap) of mints that reach >=85% bonding progress, exploration blocks only.
Run: /data/mal/audit-1008/venv/bin/python p74_extract.py OUT.parquet
"""
import glob, sys
import numpy as np, pyarrow as pa, pyarrow.parquet as pq, pyarrow.compute as pc

BLOCKS = ["explore-0814", "fresh-0903", "exp011-0909"]
TOK_OFF = 279_900_000_000_000
INIT = 793_100_000 * 10**6
COLS = ["venue", "mint", "trader", "side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve", "slot", "tx_index", "event_index", "block_time", "block", "hour"]
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
import pandas as pd
tk = pq.read_table("/data/mal/hunt-shared/tokens.parquet", columns=["mint", "is_mayhem_mode"]).to_pandas()
mayh = set(tk.mint[tk.is_mayhem_mode.fillna(False)])
print("mayhem among candidates (excluded)", len(cand & mayh), flush=True)
cand -= mayh
print("non-mayhem candidates", len(cand), flush=True)
cl = pa.array(sorted(cand))
PS_KEEP = 5   # only the first PS_KEEP PumpSwap prints per mint are needed (first print after migration + lag)
kept = {}
w = None
n = 0
for f in hours:
    t = pq.read_table(f, columns=COLS, filters=[("block", "in", BLOCKS)])
    t = t.filter(pc.is_in(t["mint"], value_set=cl))
    df = t.to_pandas()
    ps = df[df.venue == "pumpswap"].sort_values(["mint", "slot", "tx_index", "event_index"])
    ps["r"] = ps.groupby("mint").cumcount() + ps.mint.map(kept).fillna(0)
    ps = ps[ps.r < PS_KEEP].drop(columns="r")
    for m, c_ in ps.groupby("mint").size().items():
        kept[m] = kept.get(m, 0) + c_
    df = pd.concat([df[df.venue != "pumpswap"], ps])
    t = pa.Table.from_pandas(df, schema=t.schema, preserve_index=False)
    if w is None:
        w = pq.ParquetWriter(sys.argv[1], t.schema)
    w.write_table(t); n += t.num_rows
w.close()
print("rows", n)
