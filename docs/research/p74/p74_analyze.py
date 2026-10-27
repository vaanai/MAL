"""P74 analysis. Run: python p74_analyze.py OUT.parquet"""
import sys, numpy as np, pandas as pd
r = pd.read_parquet(sys.argv[1]); PR = 55_000
def boot(x, seed=1, n=1000):
    rng = np.random.default_rng(seed); x = np.asarray(x); return np.percentile([x[rng.integers(0, len(x), len(x))].mean() for _ in range(n)], 5)
for name, S in (("s025", 0.25), ("s100", 1.0)):
    x = r[r[name].notna()].copy(); pnl = x[name].values
    x["flat"] = 0.85 * pnl + 0.15 * (-PR); x["press"] = (1 - x.p_fail) * pnl + x.p_fail * (-PR)
    ndays = x.day.nunique()
    print(f"\n=== stake {S} SOL: trades {len(x)} days {ndays} attempts/day mean {len(x)/ndays:.1f} min {x.groupby('day').size().min()}")
    print("impact of own buy: mean %.4f%% median %.4f%%" % (100 * x["impact_" + name].mean(), 100 * x["impact_" + name].median()))
    print("exit kinds:", x["kind_" + name].value_counts().to_dict())
    mig = x["kind_" + name] == "migration"
    print("completion rate (sold into migration share):", mig.mean(), " mean raw pnl SOL migration %.5f other %.5f" % (x[name][mig].mean() / 1e9, x[name][~mig].mean() / 1e9))
    for m in ("flat", "press"):
        v = x[m].values; dd = x.groupby("day")[m].sum(); dm = x.groupby("day")[m].mean()
        top3 = np.sort(v)[::-1][3:].sum(); best = dd.drop(dd.idxmax()).sum()
        dc = x.groupby("day")[m].apply(list); 
        print(f"[{m}] mean/trade {v.mean()/1e9:.6f} SOL ({100*v.mean()/(S*1e9):.3f}% of stake) total {v.sum()/1e9:.4f} ex-top3 {top3/1e9:.4f} ex-best-day {best/1e9:.4f} (best {dd.idxmax()} {dd.max()/1e9:.4f}) positive days {int((dd>0).sum())}/{len(dd)} CI90 lo(trade) {boot(v)/1e9:.6f}")
        dcm = dm.values; rng = np.random.default_rng(1); days = dd.index.values; g = {k: x[x.day == k][m].values for k in days}
        bs = [np.concatenate([g[k] for k in rng.choice(days, len(days))]).mean() for _ in range(1000)]
        print(f"     CI90 lo (day-cluster) {np.percentile(bs,5)/1e9:.6f}")
        print(f"     capacity: {len(x)/ndays:.1f}/day x {S} SOL x {v.mean()/(S*1e9):.5f} = {len(x)/ndays*v.mean()/1e9:.5f} SOL/day (concurrency uncapped)")
    h = x.groupby("day").size(); 
