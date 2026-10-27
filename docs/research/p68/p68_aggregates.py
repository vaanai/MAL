"""P68 step 1: DISCOVERY-ONLY aggregates for the rule writer. Loads set==frozen_disc (explore-0814, Aug) ONLY.
Held-out rows are filtered out before anything is computed or printed."""
import pandas as pd, numpy as np
f = pd.read_parquet("/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet")
d = f[(f.blk == "explore-0814")].copy()
assert d.day.max() <= "2026-08-28"
d["hour"] = d.mbt_utc.dt.hour
d["flat_pp"] = d.flat / 1e6; d["press_pp"] = d.press / 1e6   # stake 0.1 SOL = 1e8 lamports -> pp of stake
print("n", len(d), "base mean pp flat/press", d.flat_pp.mean(), d.press_pp.mean())
for c in ["trig_t", "rem", "q_trig", "sps", "hour"]:
    print("\n##", c, d[c].describe()[["min", "25%", "50%", "75%", "max"]].round(3).to_dict())
    q = pd.qcut(d[c], 5, duplicates="drop") if c != "hour" else pd.cut(d[c], [-1, 5, 11, 17, 23])
    print(d.groupby(q, observed=True).agg(n=("flat_pp", "size"), flat=("flat_pp", "mean"), press=("press_pp", "mean"), win=("flat_pp", lambda s: (s > 0).mean())).round(3))
