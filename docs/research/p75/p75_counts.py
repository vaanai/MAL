"""P74 counts step (no pricing). Run: python p74_counts.py ROWS.parquet"""
import sys, numpy as np, pandas as pd
TOK_OFF = 279_900_000_000_000; INIT = 793_100_000 * 10**6
import pyarrow.parquet as pq
print("(extract output: 44,049 mints reached 85%; 13,418 Mayhem excluded; 30,631 non-mayhem candidates)")
t = pq.read_table(sys.argv[1], filters=[("venue", "=", "pump_bonding")], columns=["mint", "side", "token_raw", "quote_reserve", "base_reserve", "slot", "tx_index", "event_index", "block_time"])
t = t.set_column(0, "mint", t["mint"].dictionary_encode())
d = t.to_pandas(); del t
tk = pd.read_parquet("/data/mal/hunt-shared/tokens.parquet", columns=["mint", "complete_slot"]).drop_duplicates("mint")
cs = tk.dropna(subset=["complete_slot"]); csset = set(cs.mint)
d["mint"] = d["mint"].astype(object).astype("category")
b = d.sort_values(["mint", "slot", "tx_index", "event_index"]).reset_index(drop=True)
q = b.quote_reserve.values; bb = b.base_reserve.values; same = b.mint.values[1:] == b.mint.values[:-1]
print("bonding rows", len(b), "q==0 share", float((q == 0).mean()))
pos = (q[1:] > 0) & (q[:-1] > 0) & (bb[1:] > 0)
kd = np.abs(q[1:] * bb[1:] / (q[:-1] * bb[:-1] + 1e-300) - 1)
m = same & pos
print("CP pairs", int(m.sum()), "share |k drift|<1e-6", float((kd[m] < 1e-6).mean()))
# post-trade convention: base change equals -/+ token_raw
db = bb[1:] - bb[:-1]; tkr = b.token_raw.values[1:]; buy = (b.side.values[1:] == "buy")
exp = np.where(buy, -tkr, tkr)
ok = same & (np.abs(db - exp) <= 1 + 1e-9 * np.abs(exp))
print("post-trade base-change check (base_i - base_{i-1} == -/+ token_raw_i) share", float(ok[same].mean()))
b["prog"] = 1.0 - np.maximum(0, bb - TOK_OFF) / INIT
b["day"] = pd.to_datetime(b.block_time, unit="s").dt.strftime("%Y-%m-%d")
rows = []
for mint, g in b.groupby("mint", sort=False, observed=True):
    p = g.prog.values; idx = np.flatnonzero(p >= 0.85)
    if len(idx) == 0: continue
    i = idx[0]; rows.append((mint, g.day.values[i], i > 0, g.slot.values[i], bool(p.max() >= 0.9999 or (mint in csset))))
r = pd.DataFrame(rows, columns=["mint", "day", "observed_cross", "slot", "completes"])
print("mints reaching 85%", len(r), "observed crossing", int(r.observed_cross.sum()), "first print already >=85%", int((~r.observed_cross).sum()))
e = r[r.observed_cross]
print("completion share of entries (any complete in tape or tokens.complete_slot)", float(e.completes.mean()))
c = e.groupby("day").agg(entries=("mint", "size"), completes=("completes", "sum"))
print(c.to_string()); print("days", len(c), "entries/day mean", c.entries.mean(), "min", c.entries.min())
