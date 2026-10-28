"""P89 step 1: COUNTS ONLY (no outcome column is read). How many frozen H5 binding trades have all ten P86 features computable at the entry instant?
Rule copied from p86_build.py: price(t) = last print at or before t - w (w = 0, 5, 15, 60 min); a missing one -> candidate dropped. 6 h high uses available history.
Entry: trigger print block time + 1.9 s (leg b); prints with slot < entry slot X only. Usage: p89_count.py OUT.txt"""
import math, sys
import numpy as np, pandas as pd, pyarrow.parquet as pq
P = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"
FZ = "/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet"
fz = pd.read_parquet(FZ, columns=["day", "mint", "trig_t", "sps", "s0", "set"])  # no pnl / flat / press / net_pct columns
rows = []
for day, xd in fz.groupby("day"):
    paths = pq.read_table(f"{P}/paths/{day}.parquet", filters=[("mint", "in", list(xd.mint))]).to_pandas()
    pg = {m: g for m, g in paths.groupby("mint", sort=False)}
    for r in xd.itertuples():
        g = pg.get(r.mint)
        if g is None:
            rows.append(dict(set=r.set, day=day, ok=False, why="no_path")); continue
        g = g[g.slot >= r.s0]
        sl = g.slot.values; bt = g.bt.values
        t_first = bt[bt > 0][0]
        i = int(np.argmin(np.abs((sl - r.s0) * r.sps - r.trig_t)))
        X = int(sl[i]) + math.ceil(1.9 / r.sps - 1e-9)
        te = bt[i] + 1.9
        pre = bt[(sl < X) & (bt > 0)]
        age = te - t_first
        have = {w: bool((pre <= te - w * 60).any()) for w in (0, 5, 15, 60)}
        rows.append(dict(set=r.set, day=day, ok=all(have.values()), age_s=age, n_pre=len(pre), **{f"p{w}": v for w, v in have.items()}))
d = pd.DataFrame(rows)
with open(sys.argv[1], "w") as fh:
    def w(s):
        print(s); fh.write(s + "\n")
    w(f"trades {len(d)}; discovery {(d.set=='frozen_disc').sum()}; confirmation {(d.set!='frozen_disc').sum()} (sets: {d.set.value_counts().to_dict()})")
    w(f"all ten features computable (P86 rule): {int(d.ok.sum())}")
    for c in ("p0", "p5", "p15", "p60"):
        w(f"  price at or before t-{c[1:]} min exists: {int(d[c].sum())}")
    w("age at entry (s) quantiles 0/10/50/90/100: " + str(d.age_s.quantile([0, .1, .5, .9, 1]).round(1).tolist()))
    w("P86 memory age range: 7,200 s to 604,800 s (log_age 8.88 to 13.3)")
    w(f"trades with age at entry >= 7200 s: {int((d.age_s>=7200).sum())}")
