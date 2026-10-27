"""P79 metrics from the priced trades. Usage: python -I p79_report.py TRADES.parquet"""
import sys
import numpy as np
import pandas as pd

PRIO = 55_000
NDAYS = 28
t = pd.read_parquet(sys.argv[1])


def boot(v, days, draws=1000, seed=1):
    rng = np.random.default_rng(seed)
    n = len(v)
    tr = np.percentile([v[rng.integers(0, n, n)].mean() for _ in range(draws)], 5)
    rng = np.random.default_rng(seed)
    ud = np.unique(days); idx = {u: np.flatnonzero(days == u) for u in ud}
    cl = np.percentile([np.concatenate([v[idx[u]] for u in rng.choice(ud, len(ud))]).mean() for _ in range(draws)], 5)
    return tr, cl


print("entries", len(t), "days with a trade", t.day.nunique(), "trades/day over", NDAYS, "days:", len(t) / NDAYS)
for S in (0.25, 1.0):
    pnl = t[f"pnl_{S:g}"].values
    print(f"\n=== stake {S} SOL: gross (no fail) mean {pnl.mean()/1e9:.6f} SOL, median {np.median(pnl)/1e9:.6f}, share winners {np.mean(pnl>0):.4f}")
    for name, v in (("flat15", 0.85 * pnl + 0.15 * -PRIO), ("press", (1 - t.p.values) * pnl + t.p.values * -PRIO)):
        sol = v / 1e9; s = pd.Series(sol); bd = s.groupby(t.day.values).sum()
        srt = np.sort(sol)[::-1]
        lo_t, lo_c = boot(v, t.day.values)
        print(f"{name}: mean {sol.mean():.6f} SOL ({100*v.mean()/(S*1e9):.3f}% of stake) total {sol.sum():.4f} ex_top3 {sol.sum()-srt[:3].sum():.4f} "
              f"best_day {bd.idxmax()} ({bd.max():.4f}) ex_best_day {sol.sum()-bd.max():.4f} days_pos {int((bd>0).sum())}/{len(bd)} "
              f"CI90_lo trade {lo_t/1e9:.6f} dayclu {lo_c/1e9:.6f}")
        print(f"   capacity: trades/day {len(t)/NDAYS:.3f} x stake {S} x mean edge {v.mean()/(S*1e9):.5f} = {len(t)/NDAYS*sol.mean():.5f} SOL/day")
print("\nentry quote reserve SOL q10/50/90:", np.percentile(t.qe / 1e9, [10, 50, 90]))
print("per block entries:", t.groupby("block").size().to_dict())
