"""P78 step 1 summary: cost map by fee band from the extractor's pool-hour aggregates. Usage: p78_costmap.py AGG_DIR OUT_JSON"""
import sys, glob, json
import numpy as np, pandas as pd
a = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(sys.argv[1] + "/*.parquet"))], ignore_index=True)
a["dayd"] = pd.to_datetime(a.day * 86400, unit="s").dt.strftime("%Y-%m-%d")
hrs = a.groupby("dayd").hour.nunique()
full = hrs[hrs >= 12].index
print("pool-hours", len(a), "pools", a.pool.nunique(), "days", len(hrs), "full days (>=12 h)", len(full), "excluded edge days", sorted(set(hrs.index) - set(full)))
a = a[a.dayd.isin(full)].copy()
a["trad"] = (a.n >= 20) & (a.depth >= 100)
a["deep400"] = a.trad & (a.depth >= 400)
names = ["B0 [0,420)", "B1 [420,1470)", "B2 [1470,9820)", "B3 [9820,24560)", "B4 [24560,58940)", "B5 [58940,78590)", "B6 [78590,98240)", "B7 >=98240"]
nd = a.dayd.nunique(); rows = []
for bnd in range(8):
    s = a[a.band == bnd]
    if s.empty:
        rows.append(dict(band=names[bnd], pools_day=0)); continue
    t = s[s.trad]
    ppd = t.groupby("dayd").pool.nunique().reindex(sorted(a.dayd.unique())).fillna(0)
    allp = s.groupby("dayd").pool.nunique().reindex(sorted(a.dayd.unique())).fillna(0)
    f = (t if len(t) else s)
    fee = f.fee_ppm.median() / 1e6
    r = dict(band=names[bnd], pools_day_all=allp.mean(), pools_day_tradeable=ppd.mean(), poolhours_day_all=len(s) / nd, poolhours_day_tradeable=len(t) / nd,
             poolhours_day_deep400=int(s.deep400.sum()) / nd, prints_day=s.n.sum() / nd, vol_sol_day=s.vol.sum() / 1e9 / nd,
             creator_ppm=f.creator_ppm.median(), protocol_ppm=f.protocol_ppm.median(), lp_ppm=f.lp_ppm.median(), fee_leg_pct=100 * fee, rt_fee_pct=100 * (1 - (1 - fee) ** 2),
             imp025_oneway_pct=100 * f.imp_025.median(), imp1_oneway_pct=100 * f.imp_1.median(), lag2_med_pct=100 * f.lag_med.median(), depth_med=f.depth.median())
    for sk, S in (("025", 0.25), ("1", 1.0)):
        r[f"floor_{sk}_pct"] = r["rt_fee_pct"] + 100 * 2 * 55000 / (S * 1e9) + r["lag2_med_pct"]
        r[f"floor_{sk}_if_nonrevert_pct"] = r[f"floor_{sk}_pct"] + 2 * r[f"imp{sk}_oneway_pct"]
    rows.append(r)
d = pd.DataFrame(rows)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
print(d.round(4).T.to_string())
d.to_json(sys.argv[2], orient="records", indent=1)
elig = [i for i in range(8) if rows[i].get("pools_day_tradeable", 0) >= 20]
print("bands with >=20 tradeable pools/day:", [names[i] for i in elig], "-> chosen (cheapest):", names[max(elig)] if elig else "NONE")
print("lag pairs share: median lag_n per pool-hour", a.lag_n.median())
