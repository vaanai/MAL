"""P92 step 3: score Haiku vs kNN (PREREG.md). Usage: p92_score.py OUTDIR  (OUTDIR has keymap.json, ratings/batch_*.json)"""
import sys, json, glob
import numpy as np, pandas as pd
import duckdb
from scipy.stats import spearmanr, binomtest
sys.path.insert(0, ".")
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS
from tools.latency_curve import Pressure, fit_curve

OUT = sys.argv[1]
PS = "/data2/mal/p86/ps"
PRIO = 55_000; K = 25
FEATS = ["log_vol60", "ret_5m", "ret_15m", "ret_60m", "flow60", "log_signers15", "log_age", "top3_60m", "close_vs_hi", "log_qeff"]
TH = np.array([t for t, _ in PUMPSWAP_SOL_FEE_TIERS], float); TP = np.array([p for _, p in PUMPSWAP_SOL_FEE_TIERS], float)
tier = lambda Q, B: TP[np.searchsorted(TH, Q / B * 1e6 + 1e-9, "right") - 1] / 1e6

km = json.load(open(f"{OUT}/keymap.json"))
c = pd.read_parquet("/data2/mal/p86/cand.parquet")
L = pd.read_parquet("/data2/mal/p86/labeled.parquet")
curve = fit_curve([Pressure(a, b) for a, b in zip(L.ssb, L.nb)])
print("curve mean p over labelled (P86 reproduction):", round(float(np.mean([curve.p(Pressure(a, b)) for a, b in zip(L.ssb, L.nb)])), 4))

# ---- kNN walk-forward (P86 primary rule) over labelled memory
L["day"] = pd.to_datetime(L.td, unit="s", utc=True).dt.strftime("%Y-%m-%d")
cnt = L.groupby("td").size(); L = L[L.td.isin(cnt[cnt >= 3].index)].reset_index(drop=True)
c["day"] = pd.to_datetime(c.td, unit="s", utc=True).dt.strftime("%Y-%m-%d")
c = c.reset_index(drop=True)
c["knn"] = np.nan
for d in sorted(c[c.td >= 1788393600].day.unique()):
    mem = L[L.day < d]; cur = c.index[c.day == d]
    mu, sd = mem[FEATS].mean().values, mem[FEATS].std(ddof=0).values; sd = np.where(sd > 0, sd, 1.0)
    M = (mem[FEATS].values - mu) / sd; Z = (c.loc[cur, FEATS].values - mu) / sd
    D = ((Z[:, None, :] - M[None, :, :]) ** 2).sum(-1)
    nn = np.argsort(D, axis=1, kind="stable")[:, :K]
    c.loc[cur, "knn"] = mem.y.values[nn].mean(1)

# ---- sample candidates + labels (dead pools priced at last print)
tds = {v["td"]: k for k, v in km.items()}
S = c[c.td.isin(tds)].merge(L[["pool", "td", "pnl25", "ssb", "nb", "p_fail", "y"]], on=["pool", "td"], how="left")
S["dead"] = S.pnl25.isna()
con = duckdb.connect(); con.execute("PRAGMA threads=8; PRAGMA memory_limit='6GB'")
dead = S[S.dead][["pool", "td", "v_sol"]]
con.register("c", dead)
sel = "r.pool, c.td, r.slot, r.bt, r.side, r.sol, r.q::DOUBLE AS q, r.b::DOUBLE AS b, coalesce(r.txi,0) AS txi, coalesce(r.evi,0) AS evi"
E = con.execute(f"SELECT {sel} FROM read_parquet('{PS}/*.parquet') r JOIN c ON c.pool=r.pool AND r.bt >= c.td-140 AND r.bt < c.td+40 WHERE r.wsol").df().sort_values(["slot", "txi", "evi"])
X = con.execute(f"SELECT {sel} FROM read_parquet('{PS}/*.parquet') r JOIN c ON c.pool=r.pool AND r.bt >= c.td AND r.bt < c.td+5400 WHERE r.wsol").df().sort_values(["slot", "txi", "evi"])
Eg = {k: g for k, g in E.groupby(["pool", "td"])}; Xg = {k: g for k, g in X.groupby(["pool", "td"])}
nol = {"no_entry": 0, "exit_eq_entry": 0}
for i in S.index[S.dead]:
    r = S.loc[i]; V = r.v_sol * 1e9
    ge = Eg.get((r.pool, r.td)); gx = Xg.get((r.pool, r.td))
    pre = ge[ge.bt < r.td] if ge is not None else []
    if ge is None or len(pre) == 0: nol["no_entry"] += 1; continue
    Xs = int(pre.slot.max()) + 2; en = ge[ge.slot > Xs]
    if len(en) == 0: nol["no_entry"] += 1; continue
    en = en.iloc[0]
    ex = gx[gx.slot > int(en.slot)] if gx is not None else []
    if len(ex) == 0: ex = en; nol["exit_eq_entry"] += 1
    else: ex = ex.iloc[-1]
    buys = ge[(ge.side == "buy") & (ge.slot <= Xs)]
    ssb = int((buys.slot == Xs).sum()); nb = int(buys[buys.slot > Xs - 6].sol.sum())
    Qe, Be, Qx, Bx = en.q + V, en.b, ex.q + V, ex.b
    ok = Be > 0 and Bx > 0 and Qe > 0 and Qx > 0
    if not ok: nol["no_entry"] += 1; continue
    for nm, St in (("25", 0.25e9), ("100", 1.0e9)):
        f = tier(Qe, Be); net = St * (1 - f); tk = Be * net / (Qe + net)
        Q = Qx + net; B = Bx - tk
        proceeds = tk * Q / (B + tk) * (1 - tier(Q, B + tk))
        S.loc[i, "pnl" + nm] = proceeds - St - 2 * PRIO
    S.loc[i, "ssb"], S.loc[i, "nb"] = ssb, nb
    S.loc[i, "p_fail"] = curve.p(Pressure(ssb, nb))
print("dead pools in sample:", int(S.dead.sum()), "of", len(S), "| unpriced (no entry print):", nol["no_entry"], "| exit==entry (no later print):", nol["exit_eq_entry"])
S["priced"] = S.pnl25.notna()
S["y"] = S.pnl25 / 0.25e9
S["flat"] = 0.85 * S.pnl25 + 0.15 * (-PRIO); S["press"] = (1 - S.p_fail) * S.pnl25 + S.p_fail * (-PRIO)
S["hk"] = S.td.map(tds)

# ---- haiku ranks, validity
H = {}
for f in sorted(glob.glob(f"{OUT}/ratings/*.json")):
    try: H.update(json.load(open(f)))
    except Exception as e: print("unparseable", f, e)
S["hscore"] = np.nan; invalid = []
for hk, g in S.groupby("hk"):
    ids = ["c%d" % (j + 1) for j in range(8)]
    pools = km[hk]["pools"]
    try: order = [x["id"] for x in H[hk]]
    except Exception: order = None
    if order is None or sorted(order) != sorted(ids): invalid.append(hk); continue
    pos = {cid: p for p, cid in enumerate(order)}
    for i in g.index:
        cid = [k for k, v in pools.items() if v == S.loc[i, "pool"]][0]
        S.loc[i, "hscore"] = 8 - pos[cid]
print("invalid/missing hours:", invalid)
S = S[~S.hk.isin(invalid)]
S.to_pickle(f"{OUT}/scored.pkl")
S = S[S.priced].copy()  # candidates with no entry print excluded (counted above)

def rk(x): return pd.Series(x).rank().values
rows = []
for hk, g in S.groupby("hk"):
    if len(g) < 3 or g.y.nunique() < 2: continue
    h, k_ = g.hscore.values, g.knn.values
    ens = (rk(h) + rk(k_)) / 2 + 1e-6 * rk(k_)
    rows.append(dict(hk=hk, td=g.td.iloc[0], day=g.day.iloc[0], n=len(g), ic_h=spearmanr(h, g.y)[0], ic_k=spearmanr(k_, g.y)[0], ic_e=spearmanr(ens, g.y)[0]))
I = pd.DataFrame(rows)
def tstats(col):
    m = I[col].mean(); t = m / (I[col].std(ddof=1) / np.sqrt(len(I)))
    dd = I.groupby("day")[col].mean(); return m, t, dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd)))
print("\n=== rank IC per hour (hours %d, days %d) ===" % (len(I), I.day.nunique()))
for nm, col in (("Haiku", "ic_h"), ("kNN", "ic_k"), ("ensemble", "ic_e")):
    m, t, tb = tstats(col); print(f"  {nm:9s} mean IC {m:+.5f}  hour-t {t:+.3f}  day-block t {tb:+.3f}")
print("corr(IC_h, IC_k) across hours:", round(float(I[["ic_h", "ic_k"]].corr().iloc[0, 1]), 3))
for nm, col in (("Haiku - kNN", "ic_h"), ("ensemble - kNN", "ic_e")):
    I["d"] = I[col] - I.ic_k
    dd = I.groupby("day").d.mean(); w, l = int((dd > 0).sum()), int((dd < 0).sum())
    p = binomtest(w, w + l, 0.5).pvalue if w + l else float("nan")
    print(f"  {nm}: mean diff {I.d.mean():+.5f}  day-mean diff {dd.mean():+.5f}  day-block t {dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd))):+.3f}  days+ {w} days- {l}  sign-test p (two-sided) {p:.4f}  | hour-level t {I.d.mean() / (I.d.std(ddof=1) / np.sqrt(len(I))):+.3f}")
print("per-day mean IC:"); print(I.groupby("day")[["ic_h", "ic_k", "ic_e"]].mean().round(3).to_string())

# top picks
rng = np.random.default_rng(1)
def picks(S, tag):
    P = {k: [] for k in ("Haiku", "kNN", "ens", "U", "R")}
    for hk, g in S.groupby("hk", sort=False):
        g = g.reset_index(drop=True)
        P["Haiku"].append(g.loc[g.hscore.idxmax()]); P["kNN"].append(g.loc[g.knn.idxmax()])
        ens = (rk(g.hscore.values) + rk(g.knn.values)) / 2 + 1e-6 * rk(g.knn.values); P["ens"].append(g.loc[int(np.argmax(ens))])
        P["R"].append(g.loc[int(rng.integers(len(g)))])
        u = g[["flat", "press"]].mean(); P["U"].append(pd.Series({"flat": u.flat, "press": u.press, "day": g.day.iloc[0]}))
    print(f"\n=== top-pick mean per trade, pp of 0.25 SOL stake ({tag}); hours {len(P['U'])} ===")
    for k, v in P.items():
        df = pd.DataFrame(v)
        print(f"  {k:6s} flat15 {df.flat.mean() / 0.25e9 * 100:+.3f} pp   pressure {df.press.mean() / 0.25e9 * 100:+.3f} pp   (SOL/trade flat {df.flat.mean() / 1e9:+.6f})")
rng = np.random.default_rng(1); picks(S, "dead pools priced at last print")
rng = np.random.default_rng(1); picks(S[~S.dead], "report-only: dead pools dropped, P86 convention")
