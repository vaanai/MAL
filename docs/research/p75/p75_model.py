"""P75: features, logistic fit (explore-0814), cutoff (fresh-0903 top 10%), test (exp011-0909, once).
Run: python p75_model.py ROWS.parquet PRICED.parquet [--test]
Without --test the exp011-0909 rows are never scored."""
import sys, numpy as np, pandas as pd, pyarrow.parquet as pq
TOK_OFF = 279_900_000_000_000; INIT = 793_100_000 * 10**6; PR = 55_000; LAG = 2; T_SLOTS = 4500
TEST = "--test" in sys.argv
FEATS = ["vel60", "vel300", "buyers300", "flow300", "age_min"]
priced = pd.read_parquet(sys.argv[2]); pm = set(priced.mint)
t = pq.read_table(sys.argv[1], filters=[("venue", "=", "pump_bonding")],
                  columns=["mint", "trader", "side", "sol_lamports", "base_reserve", "slot", "tx_index", "event_index", "block_time", "block"])
d = t.to_pandas(); del t
d = d[d.mint.isin(pm)].sort_values(["mint", "slot", "tx_index", "event_index"], kind="stable")
tk = pd.read_parquet("/data/mal/hunt-shared/tokens.parquet", columns=["mint", "create_ms", "complete_slot"]).drop_duplicates("mint").set_index("mint")
rows = []
for mint, g in d.groupby("mint", sort=False):
    b = g.base_reserve.values.astype(float); prog = 1.0 - np.maximum(0, b - TOK_OFF) / INIT
    idx = np.flatnonzero(prog >= 0.85); i = idx[0]
    bt = g.block_time.values; sl = g.slot.values; ti = bt[i]
    X = sl[i] + LAG
    f = {"mint": mint, "block": g.block.values[i]}
    for w, nm in ((60, "vel60"), (300, "vel300")):
        j = max(np.searchsorted(bt[: i + 1], ti - w, "right") - 1, 0)
        f[nm] = prog[i] - prog[j]
    lo = np.searchsorted(bt[: i + 1], ti - 300, "right")
    side = g.side.values[lo: i + 1]; sol = g.sol_lamports.values[lo: i + 1].astype(float)
    buy = side == "buy"
    f["buyers300"] = len(set(g.trader.values[lo: i + 1][buy]))
    f["flow300"] = (sol[buy].sum() - sol[~buy].sum()) / 1e9
    cm = tk.create_ms.get(mint, np.nan)
    f["age_min"] = np.log1p(max((ti - cm / 1000.0) / 60.0, 0)) if cm == cm else np.nan
    real0 = np.flatnonzero(prog >= 1 - 1e-9)
    cs = tk.complete_slot.get(mint, np.nan)
    comp = min(sl[real0[0]] if len(real0) else 1 << 60, cs if cs == cs else 1 << 60)
    f["y"] = int(comp <= X + T_SLOTS)
    rows.append(f)
F = pd.DataFrame(rows)
r = priced.merge(F, on="mint")
r = r[r.s025.notna()].copy()
r["flat"] = 0.85 * r.s025 + 0.15 * (-PR); r["press"] = (1 - r.p_fail) * r.s025 + r.p_fail * (-PR)
r["flat100"] = np.where(r.s100.notna(), 0.85 * r.s100 + 0.15 * (-PR), np.nan); r["press100"] = np.where(r.s100.notna(), (1 - r.p_fail) * r.s100 + r.p_fail * (-PR), np.nan)
print("priced entries by block:", r.block.value_counts().to_dict(), " days:", r.groupby("block").day.nunique().to_dict())
print("age_min missing share by block:", r.groupby("block").age_min.apply(lambda s: s.isna().mean()).to_dict())
print("label y base rate by block:", r.groupby("block").y.mean().to_dict())
tr = r[r.block == "explore-0814"]; fr = r[r.block == "fresh-0903"]; te = r[r.block == "exp011-0909"]
med = tr.age_min.median()
def X_(x): return x[FEATS].fillna({"age_min": med}).values.astype(float)
mu = X_(tr).mean(0); sd = X_(tr).std(0) + 1e-12
def Z(x): return (X_(x) - mu) / sd
def fit(Xz, y, lam=1.0, it=50):
    A = np.hstack([np.ones((len(Xz), 1)), Xz]); w = np.zeros(A.shape[1]); P = lam * np.eye(A.shape[1]); P[0, 0] = 0
    for _ in range(it):
        p = 1 / (1 + np.exp(-A @ w)); g = A.T @ (p - y) + P @ w
        H = (A * (p * (1 - p))[:, None]).T @ A + P; step = np.linalg.solve(H, g); w -= step
        if np.abs(step).max() < 1e-9: break
    return w
def score(w, Xz): return 1 / (1 + np.exp(-(np.hstack([np.ones((len(Xz), 1)), Xz]) @ w)))
def auc(s, y):
    from scipy.stats import rankdata
    rk = rankdata(s); n1 = y.sum(); n0 = len(y) - n1
    return (rk[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
w = fit(Z(tr), tr.y.values.astype(float))
print("weights (intercept, then standardised", FEATS, "):", np.round(w, 4).tolist())
for nm, x in (("train explore-0814", tr), ("fresh-0903", fr)):
    s = score(w, Z(x)); print(f"AUC {nm}: {auc(s, x.y.values):.4f}")
cut = np.percentile(score(w, Z(fr)), 90)
print("CUTOFF (fresh-0903 90th pct of scores):", cut)
def boot(x):
    rng = np.random.default_rng(1); return np.percentile([x[rng.integers(0, len(x), len(x))].mean() for _ in range(1000)], 5)
def report(nm, x, S=0.25):
    print(f"\n=== {nm}: trades {len(x)}, days {x.day.nunique()}, trades/day {len(x)/max(x.day.nunique(),1):.2f}")
    if len(x) == 0: return
    print("exit kinds:", x.kind_s025.value_counts().to_dict(), " impact mean %.4f%%" % (100 * x.impact_s025.mean()))
    for m, c in (("flat", "flat"), ("press", "press")):
        v = x[c].values; dd = x.groupby("day")[c].sum()
        print(f"[{m}] mean/trade {v.mean()/1e9:.6f} SOL ({100*v.mean()/(S*1e9):.3f}%) total {v.sum()/1e9:.4f} ex-top3 {np.sort(v)[::-1][3:].sum()/1e9:.4f} ex-best-day {dd.drop(dd.idxmax()).sum()/1e9:.4f} (best {dd.idxmax()}) positive days {int((dd>0).sum())}/{len(dd)} CI90lo {boot(v)/1e9:.6f}")
        print(f"     capacity 0.25: {len(x)/x.day.nunique():.2f}/day x 0.25 x {v.mean()/(S*1e9):.5f} = {len(x)/x.day.nunique()*v.mean()/1e9:.5f} SOL/day")
    for m in ("flat100", "press100"):
        y = x[x[m].notna()][m].values
        if len(y): print(f"[{m}] 1 SOL: n {len(y)} mean/trade {y.mean()/1e9:.6f} SOL ({100*y.mean()/1e9:.3f}%) total {y.sum()/1e9:.4f}  capacity {len(y)/x.day.nunique()*y.mean()/1e9:.5f} SOL/day")
    mg = x.kind_s025 == "migration"; print("migration share %.4f" % mg.mean())
report("fresh-0903 ALL (unselected)", fr); report("fresh-0903 top10% (cutoff set here)", fr[score(w, Z(fr)) >= cut])
if TEST:
    s = score(w, Z(te)); print(f"\nAUC exp011-0909 TEST: {auc(s, te.y.values):.4f}")
    report("exp011-0909 ALL (unselected)", te); report("exp011-0909 TEST top (score >= fresh cutoff)", te[s >= cut])
    print("share of exp011 selected:", (s >= cut).mean())
else:
    print("\n(--test not given: exp011-0909 not scored)")
