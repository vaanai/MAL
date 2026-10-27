"""P70 analysis of priced pool-days per PREREG. Run: python -I p70_analyze.py PRICED.parquet"""
import math, sys
import numpy as np, pandas as pd
sys.path.insert(0, "/home/claude/.miscusi/repos/vaanai/MAL")
from tools.latency_curve import Pressure, fit_curve  # noqa: E402
df = pd.read_parquet(sys.argv[1]); PRIO = 55_000
print('rows', len(df), 'pool-days (per stake)', len(df) // 2, 'days', df.day.nunique(), 'max |identity residual| lamports', float(df.resid.abs().max()))
u = df[df.stake == 0.25]
curve = fit_curve([Pressure(int(a), int(b * 1e9)) for a, b in zip(u.same, u.nearsol / 1e9)]) if False else fit_curve([Pressure(int(a), int(b)) for a, b in zip(u.same, u.nearsol)])
df['p_press'] = [curve.p(Pressure(int(a), int(b))) for a, b in zip(df.same, df.nearsol)]
print('pressure curve intercept', curve.intercept, 'mean p', float(df[df.stake == 0.25].p_press.mean()))
print('V known share of pool-days', float(u.Vok.mean()), ' 48h branch with next-day boundary', float(u.x2_ok.mean()))
df['flat'] = 0.85 * df.n24 + 0.15 * df.n48
df['press'] = (1 - df.p_press) * df.n24 + df.p_press * df.n48
df['collapsed'] = df.pxratio < 0.5
df['qlow'] = df.Qx < 5e9
rng = np.random.default_rng(1)


def ci(d, col):
    days = sorted(d.day.unique()); g = {k: v[col].values for k, v in d.groupby('day')}
    vals = []
    for _ in range(1000):
        pick = rng.integers(0, len(days), len(days))
        vals.append(np.concatenate([g[days[i]] for i in pick]).mean())
    return float(np.percentile(vals, 5))


out = []
for S in (0.25, 1.0):
    d = df[df.stake == S]; sl = S * 1e9
    print(f'\n=== stake {S} SOL: {len(d)} pool-days, {d.day.nunique()} days ===')
    print('components mean per pool-day (SOL, 24h branch): net', d.n24.mean() / 1e9, 'fees', d.fees.mean() / 1e9, 'IL', d.il.mean() / 1e9, 'price drift on bought basket', d.drift.mean() / 1e9,
          'entry cost', -d.entry_cost.mean() / 1e9, 'exit cost', -d.exit_cost.mean() / 1e9, 'fixed priority', -4 * PRIO / 1e9)
    print('median LP share g', float(d.gshare.median()), 'median swap fraction of stake at entry', float(d.swap_share.median()), 'median fees SOL', float(d.fees.median() / 1e9), 'median fees % stake', float((d.fees / sl).median() * 100))
    for m in ('flat', 'press'):
        x = d[m] / 1e9; t = x.sum(); top3 = t - x.nlargest(3).sum()
        pd_ = d.assign(v=x).groupby('day').v.sum(); best = pd_.idxmax()
        wo_best = t - pd_.max()
        maj = int((d.assign(pos=x > 0).groupby('day').pos.mean() > 0.5).sum())
        lo = ci(d.assign(v=x), 'v')
        print(f'[{m}] mean/pool-day {x.mean():.6f} SOL ({x.mean() / S * 100:.4f}% of S)  total {t:.6f}  total ex-top3 {top3:.6f}  total ex-best-day({best}) {wo_best:.6f}  '
              f'days majority-positive {maj}/{d.day.nunique()}  CI90 lower (date-cluster, 1000, seed1) {lo:.6f}  share positive pool-days {(x > 0).mean():.4f}')
        c = d.assign(v=x)
        for name, mask in (('collapsed(px<0.5)', c.collapsed), ('survivors', ~c.collapsed), ('Qx<5SOL', c.qlow)):
            s = c[mask]
            print(f'    {name}: n {len(s)}  total {s.v.sum():.6f}  mean {s.v.mean() if len(s) else float("nan"):.6f}')
        print(f'    ex-collapsed total {c[~c.collapsed].v.sum():.6f}  mean {c[~c.collapsed].v.mean():.6f}')
    dn = (d.fees + d.il - d.entry_cost - d.exit_cost - 4 * PRIO) / 1e9
    print(f'hedged-view net (fees + IL - costs, i.e. excluding price drift of the bought token basket): mean {dn.mean():.6f} SOL  total {dn.sum():.6f}  ex-top3 {dn.sum() - dn.nlargest(3).sum():.6f}  share positive {(dn > 0).mean():.4f}  fees-only mean {d.fees.mean() / 1e9:.6f}')
    print('24h-only net (no fail model) mean', d.n24.mean() / 1e9, 'median', d.n24.median() / 1e9)
    dd = d.assign(f=d.flat / 1e9, p=d.press / 1e9).groupby('day')[['f', 'p']].sum()
    print('per-day net (flat, press) SOL:'); print(dd.round(6).to_string())
    for m in ('flat', 'press'):
        x = d[m] / 1e9
        print(f'capacity [{m}]: 10 pool-days x {S} SOL x {x.mean() / S * 100:.4f}% = {10 * x.mean():.6f} SOL/day')
    print('worst 5 pool-days (flat, SOL):', (d.flat.nsmallest(5) / 1e9).round(5).tolist(), ' best 5:', (d.flat.nlargest(5) / 1e9).round(5).tolist())
df.to_parquet(sys.argv[1].replace('.parquet', '_scored.parquet'))
