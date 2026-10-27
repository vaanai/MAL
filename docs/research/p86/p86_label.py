"""P86 step 2: price the forward 60-min label for each candidate (PREREG.md). Usage: p86_label.py PSDIR CAND.parquet OUT.parquet LOG.txt
Pricing is p55_price.py's tier()/sell() math on PRE-trade reserves + V. Entry: first print with slot > slot_d + 2. Exit trigger: first print with
block_time >= t_d + 3600; sell at the first print with slot > trigger_slot + 2."""
import math, sys
import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, "/data2/mal/wt/p86")
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS
from tools.latency_curve import Pressure, fit_curve

PS, CAND, OUT, LOG = sys.argv[1:5]
TH = np.array([t for t, _ in PUMPSWAP_SOL_FEE_TIERS], float)
TP = np.array([p for _, p in PUMPSWAP_SOL_FEE_TIERS], float)
PRIO = 55_000
STAKES = {"25": 0.25e9, "100": 1.0e9}


def tier(Q, B):
    return TP[np.searchsorted(TH, Q / B * 1e6 + 1e-9, "right") - 1] / 1e6


c = pd.read_parquet(CAND)
con = duckdb.connect()
con.execute("PRAGMA threads=8; PRAGMA memory_limit='16GB'")
con.register("c", c[["pool", "td", "v_sol"]])
glob_ = PS + "/*.parquet"
sel = "r.pool, c.td, r.slot, r.bt, r.side, r.sol, r.q::DOUBLE AS q, r.b::DOUBLE AS b, coalesce(r.txi,0) AS txi, coalesce(r.evi,0) AS evi"
E = con.execute(f"SELECT {sel} FROM read_parquet('{glob_}') r JOIN c ON c.pool=r.pool AND r.bt >= c.td-140 AND r.bt < c.td+40 WHERE r.wsol").df()
X = con.execute(f"SELECT {sel} FROM read_parquet('{glob_}') r JOIN c ON c.pool=r.pool AND r.bt >= c.td+3600 AND r.bt < c.td+3600+240 WHERE r.wsol").df()
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
    if gx is None:
        drops["no_trigger"] += 1; continue
    tr = gx[gx.bt >= r.td + 3600]
    if len(tr) == 0:
        drops["no_trigger"] += 1; continue
    tslot = int(tr.slot.iloc[0])
    ex = gx[gx.slot > tslot + 2]
    if len(ex) == 0:
        drops["no_exit_print"] += 1; continue
    ex = ex.iloc[0]
    Qe, Be = en.q + V, en.b
    Qx, Bx = ex.q + V, ex.b
    if Be <= 0 or Bx <= 0 or Qe <= 0 or Qx <= 0:
        drops["bad_state"] += 1; continue
    buys = ge[(ge.side == "buy") & (ge.slot <= Xs)]
    ssb = int((buys.slot == Xs).sum()); nb = int(buys[buys.slot > Xs - 6].sol.sum())
    d = dict(pool=r.pool, td=r.td, ssb=ssb, nb=nb, entry_slot=int(en.slot), exit_slot=int(ex.slot), lag_entry=int(en.slot) - slot_d, hold_slots=int(ex.slot) - int(en.slot))
    ok = True
    for nm, S in STAKES.items():
        f = tier(Qe, Be); net = S * (1 - f); tk = Be * net / (Qe + net)
        Q = Qx + net; B = Bx - tk
        if B <= 0:
            ok = False; break
        proceeds = tk * Q / (B + tk) * (1 - tier(Q, B + tk))
        d["pnl" + nm] = proceeds - S - 2 * PRIO
    if not ok:
        drops["bad_state"] += 1; continue
    rows.append(d)
L = pd.DataFrame(rows)
curve = fit_curve([Pressure(a, b) for a, b in zip(L.ssb, L.nb)])
L["p_fail"] = [curve.p(Pressure(a, b)) for a, b in zip(L.ssb, L.nb)]
out = c.merge(L, on=["pool", "td"], how="inner")
out["y"] = out.pnl25 / 0.25e9
out.to_parquet(OUT)
with open(LOG, "w") as fh:
    fh.write(f"candidates in {len(c)}; priced {len(out)}; drops {drops}\n")
    fh.write(f"curve intercept {curve.intercept:.6f}, mean p_fail {L.p_fail.mean():.4f}\n")
    fh.write(f"entry lag slots (first print slot - slot_d): median {L.lag_entry.median()} p90 {L.lag_entry.quantile(.9)}; hold slots median {L.hold_slots.median()}\n")
    fh.write(f"mean y (no fail) {out.y.mean():.6f}; mean pnl25 lamports {out.pnl25.mean():.1f}\n")
print(open(LOG).read())
