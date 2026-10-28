"""P91 (adapted from P86 step 2; dead pools kept with D1/D2 pnl): price the forward 60-min label for each candidate (PREREG.md). Usage: p86_label.py PSDIR CAND.parquet OUT.parquet LOG.txt
Pricing is p55_price.py's tier()/sell() math on PRE-trade reserves + V. Entry: first print with slot > slot_d + 2. Exit trigger: first print with
block_time >= t_d + 3600; sell at the first print with slot > trigger_slot + 2."""
import math, sys
import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, "/data2/mal/wt/p91")
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS
from tools.latency_curve import Pressure, fit_curve

PS, CAND, OUT, LOG = sys.argv[1:5]
TH = np.array([t for t, _ in PUMPSWAP_SOL_FEE_TIERS], float)
TP = np.array([p for _, p in PUMPSWAP_SOL_FEE_TIERS], float)
PRIO = 55_000
WIN = int(sys.argv[5]) if len(sys.argv) > 5 else 1800
STAKES = {"25": 0.25e9, "100": 1.0e9}


def tier(Q, B):
    return TP[np.searchsorted(TH, Q / B * 1e6 + 1e-9, "right") - 1] / 1e6


c = pd.read_parquet(CAND)
con = duckdb.connect()
con.execute("PRAGMA threads=8; PRAGMA memory_limit='8GB'")
con.register("c", c[["pool", "td", "v_sol"]])
glob_ = PS + "/*.parquet"
sel = "r.pool, c.td, r.slot, r.bt, r.side, r.sol, r.q::DOUBLE AS q, r.b::DOUBLE AS b, coalesce(r.txi,0) AS txi, coalesce(r.evi,0) AS evi"
E = con.execute(f"SELECT {sel} FROM read_parquet('{glob_}') r JOIN c ON c.pool=r.pool AND r.bt >= c.td-140 AND r.bt < c.td+40 WHERE r.wsol").df()
X = con.execute(f"""SELECT * FROM (SELECT {sel}, row_number() OVER (PARTITION BY r.pool, c.td ORDER BY r.slot, coalesce(r.txi,0), coalesce(r.evi,0)) AS rn
  FROM read_parquet('{glob_}') r JOIN c ON c.pool=r.pool AND r.bt >= c.td+3600 AND r.bt < c.td+3600+{WIN} WHERE r.wsol) WHERE rn <= 600""").df()
E = E.sort_values(["slot", "txi", "evi"]); X = X.sort_values(["slot", "txi", "evi"])
Eg = {k: g for k, g in E.groupby(["pool", "td"], sort=False)}
Xg = {k: g for k, g in X.groupby(["pool", "td"], sort=False)}
rows, drops = [], {"no_pre_print": 0, "no_entry_print": 0, "no_trigger": 0, "no_exit_print": 0, "bad_state": 0}
for r in c.itertuples():
    V = r.v_sol * 1e9
    ge, gx = Eg.get((r.pool, r.td)), Xg.get((r.pool, r.td))
    if ge is None:
        drops["no_pre_print"] += 1; continue
    pre = ge[ge.bt < r.td]
    if len(pre) == 0:
        drops["no_pre_print"] += 1; continue
    slot_d = int(pre.slot.max()); Xs = slot_d + 2
    en = ge[ge.slot > Xs]
    if len(en) == 0:
        drops["no_entry_print"] += 1; continue
    en = en.iloc[0]
    ex = None; why = None
    if gx is None:
        why = "no_trigger"
    else:
        tr = gx[gx.bt >= r.td + 3600]
        if len(tr) == 0:
            why = "no_trigger"
        else:
            tslot = int(tr.slot.iloc[0])
            exx = gx[gx.slot > tslot + 2]
            if len(exx) == 0:
                why = "no_exit_print"
            else:
                ex = exx.iloc[0]
    Qe, Be = en.q + V, en.b
    if Be <= 0 or Qe <= 0:
        drops["bad_state"] += 1; continue
    if ex is not None:
        Qx, Bx = ex.q + V, ex.b
        if Bx <= 0 or Qx <= 0:
            drops["bad_state"] += 1; continue
    buys = ge[(ge.side == "buy") & (ge.slot <= Xs)]
    ssb = int((buys.slot == Xs).sum()); nb = int(buys[buys.slot > Xs - 6].sol.sum())
    d = dict(pool=r.pool, td=r.td, ssb=ssb, nb=nb, entry_slot=int(en.slot), dead=int(ex is None), dead_why=why or "", lag_entry=int(en.slot) - slot_d,
             hold_slots=(int(ex.slot) - int(en.slot)) if ex is not None else -1, Qe=Qe, Be=Be)
    if ex is None:
        for nm, S in STAKES.items():
            d["pnl" + nm] = np.nan
            d["d2_" + nm] = -S - PRIO
        rows.append(d); continue
    ok = True
    for nm, S in STAKES.items():
        f = tier(Qe, Be); net = S * (1 - f); tk = Be * net / (Qe + net)
        Q = Qx + net; B = Bx - tk
        if B <= 0:
            ok = False; break
        proceeds = tk * Q / (B + tk) * (1 - tier(Q, B + tk))
        d["pnl" + nm] = proceeds - S - 2 * PRIO
        d["d2_" + nm] = proceeds - S - 2 * PRIO
    if not ok:
        drops["bad_state"] += 1; continue
    rows.append(d)
L = pd.DataFrame(rows)
# D1: last tape print of the pool with block_time > t_d (any time), sold at its pre-trade state
dead = L[L.dead == 1][["pool", "td"]].merge(c[["pool", "td", "v_sol"]], on=["pool", "td"])
con.register("dd", dead[["pool", "td"]])
LP = con.execute(f"""SELECT r.pool, dd.td, max_by(r.q::DOUBLE, r.slot*10000000+coalesce(r.txi,0)*1000+coalesce(r.evi,0)) AS q, max_by(r.b::DOUBLE, r.slot*10000000+coalesce(r.txi,0)*1000+coalesce(r.evi,0)) AS b,
  max(r.bt) AS lastbt FROM read_parquet('{glob_}') r JOIN dd ON dd.pool=r.pool AND r.bt >= dd.td WHERE r.wsol GROUP BY r.pool, dd.td""").df()
LP = LP.merge(dead[["pool", "td", "v_sol"]], on=["pool", "td"])
L = L.merge(LP.rename(columns={"q": "Ql", "b": "Bl"}), on=["pool", "td"], how="left")
for nm, S in STAKES.items():
    L["d1_" + nm] = np.nan
for i in L.index[L.dead == 1]:
    q = L.at[i, "Ql"]
    if not (q == q):
        continue
    Qx, Bx = q + L.at[i, "v_sol"] * 1e9, L.at[i, "Bl"]
    for nm, S in STAKES.items():
        Qe, Be = L.at[i, "Qe"], L.at[i, "Be"]
        f = tier(Qe, Be); net = S * (1 - f); tk = Be * net / (Qe + net)
        Q = Qx + net; B = Bx - tk
        if B <= 0 or Bx <= 0:
            L.at[i, "d1_" + nm] = -S - PRIO; continue
        proceeds = tk * Q / (B + tk) * (1 - tier(Q, B + tk))
        L.at[i, "d1_" + nm] = proceeds - S - 2 * PRIO
pr = L[L.dead == 0]
curve = fit_curve([Pressure(a, b) for a, b in zip(pr.ssb, pr.nb)])
L["p_fail"] = [curve.p(Pressure(a, b)) for a, b in zip(L.ssb, L.nb)]
out = c.merge(L, on=["pool", "td"], how="inner")
out["y"] = out.pnl25 / 0.25e9
out.to_parquet(OUT)
with open(LOG, "w") as fh:
    fh.write(f"candidates in {len(c)}; entered {len(out)} (priced {int((out.dead==0).sum())}, dead {int((out.dead==1).sum())}); drops {drops}\n")
    fh.write(f"dead reasons {out[out.dead==1].dead_why.value_counts().to_dict()}; dead with no later print at all {int(out[out.dead==1].d1_25.isna().sum())}\n")
    fh.write(f"curve intercept {curve.intercept:.6f}, mean p_fail (priced) {out[out.dead==0].p_fail.mean():.4f}\n")
    fh.write(f"entry lag slots: median {out.lag_entry.median()} p90 {out.lag_entry.quantile(.9)}; hold slots median {out[out.dead==0].hold_slots.median()}\n")
    fh.write(f"mean y (no fail, priced) {out.y.mean():.6f}; mean pnl25 lamports {out.pnl25.mean():.1f}\n")
print(open(LOG).read())
