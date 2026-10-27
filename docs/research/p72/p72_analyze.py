"""P72 analysis against PREREG.md. Usage: p72_analyze.py TRADES.parquet STAKE_SOL"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "/home/claude/.miscusi/repos/vaanai/MAL")
from tools.latency_curve import Pressure, fit_curve  # noqa: E402

PRIO = 55_000
x = pd.read_parquet(sys.argv[1]); S = float(sys.argv[2]) * 1e9
NDAYS = 36   # days with paths (p72_run.py output)
print(f"clusters after the one-open-position rule {len(x)}; unfilled {int(x.unfilled.sum())}; clusters/day (declared, after position rule) {len(x) / NDAYS:.4f}")
x = x[~x.unfilled].copy()
pnl = x.pnl.values.astype(float)
curve = fit_curve([Pressure(int(a), int(b)) for a, b in zip(x.ssb, x.nb)])
p = np.array([curve.p(Pressure(int(a), int(b))) for a, b in zip(x.ssb, x.nb)])
x["flat"] = 0.85 * pnl + 0.15 * -PRIO; x["press"] = (1 - p) * pnl + p * -PRIO; x["nofail"] = pnl
print(f"trades {len(x)}, days with trades {x.day.nunique()}, trades/day over {NDAYS} days {len(x) / NDAYS:.4f}; mean p_fail {p.mean():.4f}")
print("exits:", x.reason.value_counts().to_dict(), "censored", int(x.censored.sum()))
print(f"hit rate (target) {100 * (x.reason == 'tp').mean():.3f}%  stop {100 * (x.reason == 'sl').mean():.3f}%  cap {100 * (x.reason == 'cap').mean():.3f}%")


def gate(v, days, draws=1000, seed=1):
    v = np.asarray(v, float) / 1e9; days = np.asarray(days); ud = np.unique(days)
    idx = {d: np.where(days == d)[0] for d in ud}
    per = pd.Series(v).groupby(days).sum()
    rng = np.random.default_rng(seed)
    bm = np.array([np.concatenate([v[idx[d]] for d in rng.choice(ud, len(ud), replace=True)]).mean() for _ in range(draws)])
    srt = np.sort(v)[::-1]
    bd = per.idxmax(); nb = (days != bd).sum()
    return dict(n=len(v), days=len(ud), mean_sol=v.mean(), mean_pct=100 * v.mean() / (S / 1e9), ci5_pct=100 * np.percentile(bm, 5) / (S / 1e9),
                total=v.sum(), days_pos=int((per > 0).sum()), ex_top3_total=v.sum() - srt[:3].sum(), ex_top3_mean_pct=100 * (v.sum() - srt[:3].sum()) / (len(v) - 3) / (S / 1e9),
                best_day=bd, ex_bestday_total=v.sum() - per.max(), ex_bestday_mean_pct=100 * (v.sum() - per.max()) / nb / (S / 1e9), median_pct=100 * np.median(v) / (S / 1e9))


res = {}
for m in ("nofail", "flat", "press"):
    g = gate(x[m].values, x.day.values); res[m] = g
    print(m, {k: (round(v, 5) if isinstance(v, float) else v) for k, v in g.items()})
print("\n--- died vs survived (report only, uses post-entry information; not causal) ---")
for lab, sub in (("died", x[x.died]), ("survived", x[~x.died])):
    if len(sub) < 5:
        print(lab, len(sub)); continue
    print(lab, f"n={len(sub)} hit_rate={100 * (sub.reason == 'tp').mean():.3f}% stop={100 * (sub.reason == 'sl').mean():.3f}%",
          {m: round(100 * sub[m].mean() / S, 4) for m in ("nofail", "flat", "press")}, "(mean % of stake)")
print("\n--- kill lines ---")
k = []
if res["flat"]["mean_sol"] <= 0 or res["press"]["mean_sol"] <= 0: k.append("1 mean net <= 0 under a fail model")
if len(x) / NDAYS < 2: k.append("2 clusters/day < 2")
if res["flat"]["ex_top3_total"] <= 0 or res["press"]["ex_top3_total"] <= 0: k.append("3 top-3 driven")
print("kill lines hit:", k or "none")
e = min(res["flat"]["mean_pct"], res["press"]["mean_pct"])
print(f"capacity: {len(x) / NDAYS:.4f} trades/day x {S / 1e9} SOL x {e:.4f}% (lower model) = {len(x) / NDAYS * S / 1e9 * e / 100:.5f} SOL/day")
g = x.groupby("day")[["flat", "press"]].sum() / 1e9
print("per-day totals (SOL):\n", g.round(4).to_string())
