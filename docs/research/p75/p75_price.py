"""P74 pricing of the pre-registered graduation play (PREREG.md + Amendment 1). Exploration blocks only.
Run: python p74_price.py ROWS.parquet OUT.parquet   (ROWS from p74_extract.py; Mayhem already excluded)
"""
import math, sys
import numpy as np, pandas as pd, pyarrow.parquet as pq
import os, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
PORTAL = int(os.environ.get("P75_PORTAL_PPM", "0")) / 1e6   # 0 primary; 5000 = 0.5% portal fee on each bonding leg
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS, BONDING_FEE_PPM
from tools.latency_curve import Pressure, fit_curve

TOK_OFF = 279_900_000_000_000; INIT = 793_100_000 * 10**6
FEE = BONDING_FEE_PPM / 1e6; PRIO = 55_000
P_ENTRY, P_EXIT, STOP, T_SLOTS, LAG = 0.85, 0.99, 0.20, 4500, 2   # 30 min / 0.4 s
STAKES = {"s025": 250_000_000, "s100": 1_000_000_000}
TH = np.array([t for t, _ in PUMPSWAP_SOL_FEE_TIERS], float); TP = np.array([p for _, p in PUMPSWAP_SOL_FEE_TIERS], float)
def ps_tier(Q, B): return TP[np.searchsorted(TH, Q / B * 1e6 + 1e-9, "right") - 1] / 1e6

cols = ["mint", "venue", "side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve", "slot", "tx_index", "event_index", "block_time"]
t = pq.read_table(sys.argv[1], columns=cols)
d = t.to_pandas(); del t
d = d.sort_values(["mint", "slot", "tx_index", "event_index"], kind="stable")
tk = pd.read_parquet("/data/mal/hunt-shared/tokens.parquet", columns=["mint", "complete_slot", "v0_lamports"]).drop_duplicates("mint").set_index("mint")
bond = d[d.venue == "pump_bonding"]; ps = d[d.venue == "pumpswap"]
psg = {m: g for m, g in ps.groupby("mint", sort=False)}
out = []; stats = dict(mints=0, not_observed=0, completed_before_entry=0, buy_refused=0, q0_path=0, censored=0, cap_hit=0, no_ps=0)
for mint, g in bond.groupby("mint", sort=False):
    sl = g.slot.values; q = g.quote_reserve.values.astype(float); b = g.base_reserve.values.astype(float)
    prog = 1.0 - np.maximum(0, b - TOK_OFF) / INIT
    idx = np.flatnonzero(prog >= P_ENTRY)
    if len(idx) == 0: continue
    stats["mints"] += 1
    i = idx[0]
    if i == 0: stats["not_observed"] += 1; continue
    X = sl[i] + LAG
    j = np.searchsorted(sl, X, "right") - 1
    cs = tk.complete_slot.get(mint, np.nan) if mint in tk.index else np.nan
    real0 = np.flatnonzero(prog >= 1 - 1e-9)
    comp_slot = min(sl[real0[0]] if len(real0) else 1 << 60, cs if cs == cs else 1 << 60)
    if comp_slot <= X: stats["completed_before_entry"] += 1; continue
    if q[j] <= 0 or b[j] <= 0: stats["q0_path"] += 1; continue
    # pressure on the mint's bonding tape
    side = g.side.values; solv = g.sol_lamports.values.astype(float)
    win = (sl >= X - 5) & (sl <= X) & (side == "buy")
    press = Pressure(int(((sl == X) & (side == "buy")).sum()), int(solv[win].sum()))
    t0 = g.block_time.values[j]
    day = pd.Timestamp(int(g.block_time.values[i]), unit="s").strftime("%Y-%m-%d")
    row = dict(mint=mint, day=day, X=int(X), same=press.same_slot_buys, near=press.nearby_buy_lamports)
    ok_all = True
    # trigger search over prints after the fill state j
    k = np.arange(j + 1, len(sl))
    tmax = X + T_SLOTS
    for name, S in STAKES.items():
        net = S * (1 - FEE) * (1 - PORTAL)
        tkn = b[j] * net / (q[j] + net)
        if tkn >= max(0.0, b[j] - TOK_OFF): row[name] = np.nan; row["refused_" + name] = True; continue
        row["impact_" + name] = (net / tkn) / (q[j] / b[j]) - 1
        qk = q[k]; bk = b[k]
        with np.errstate(all="ignore"):
            gross = tkn * (qk + net) / bk; proc = gross * (1 - FEE) * (1 - PORTAL)
        mark = (proc - S) / S
        trig = ((prog[k] >= P_EXIT) | (mark <= -STOP) | (sl[k] > tmax)) & (qk > 0)
        tt = np.flatnonzero(trig)
        if len(tt):
            tr = k[tt[0]]
            if sl[tr] > tmax: reason, tslot = "time", tmax
            elif prog[tr] >= P_EXIT: reason, tslot = "a99", sl[tr]
            else: reason, tslot = "stop", sl[tr]
        else: reason, tslot = "time", tmax
        land = tslot + LAG
        mig = comp_slot <= land
        if not mig:
            m = np.searchsorted(sl, land, "right") - 1
            if q[m] <= 0: row[name] = np.nan; row["q0_" + name] = True; continue
            gs = tkn * (q[m] + net) / b[m]
            # P75 fix: payable real SOL is the OBSERVED tape reserve less the 30 SOL virtual floor (our reinjected buy creates no real SOL beyond what it paid in)
            real_sol = q[m] - 30e9 if q[m] >= 30e9 else q[m]
            if gs > real_sol: stats["cap_hit"] += 1; row[name] = -S - 2 * PRIO; row["kind_" + name] = "sell_reverts"; continue
            proceeds = gs * (1 - FEE) * (1 - PORTAL); kind = reason
        else:
            kind = "migration"
            pg = psg.get(mint)
            if pg is None or len(pg) == 0: proceeds = 0.0; stats["no_ps"] += 1
            else:
                psl = pg.slot.values; Y = psl[0] + LAG
                m2 = np.searchsorted(psl, Y, "right")
                if m2 >= len(psl): m2 = len(psl) - 1
                v = tk.v0_lamports.get(mint, 0) if mint in tk.index else 0
                V = float(v) if (v == v and 17.5e9 <= v <= 17.7e9) else 0.0
                Q = float(pg.quote_reserve.values[m2]) + V; B = float(pg.base_reserve.values[m2])
                proceeds = tkn * Q / (B + tkn) * (1 - ps_tier(Q, B)) if Q > 0 and B > 0 else 0.0
        row[name] = proceeds - S - 2 * PRIO; row["kind_" + name] = kind
    out.append(row)
r = pd.DataFrame(out)
curve = fit_curve([Pressure(a, c) for a, c in zip(r.same, r.near)])
r["p_fail"] = [curve.p(Pressure(a, c)) for a, c in zip(r.same, r.near)]
print("fit intercept", curve.intercept, "mean p", r.p_fail.mean())
print(stats)
r.to_parquet(sys.argv[2])
