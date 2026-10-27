"""P84 regime switch analysis. Usage: python p84_analyze.py P55_ROWS.parquet  (rows from docs/research/p55/p55_price.py)"""
import sys
import numpy as np
import pandas as pd

PRIO = 55_000
x = pd.read_parquet(sys.argv[1])
pf = x.p_fail.values
print("rows", len(x), x.set.value_counts().to_dict())
print("tool check max|repriced frozen - stored| lamports", (x.pnl_base_frozenlag - x.stored_pnl).abs().max())

# --- regime variable: graduations per UTC day (count only) ---
t = pd.read_parquet('/data/mal/hunt-shared/tokens.parquet', columns=['grad_ms']).grad_ms.dropna()
ts = pd.to_datetime(t, unit='ms')
G = ts.dt.strftime('%Y-%m-%d').value_counts()
H24 = ts.dt.floor('h').drop_duplicates().dt.strftime('%Y-%m-%d').value_counts()
def gprev(day):
    p = (pd.Timestamp(day) - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    return float(G[p]) if (H24.get(p, 0) == 24 and p in G.index) else np.nan

days = x.groupby('day').agg(set=('set', 'first'), blk=('blk', 'first'), n=('mint', 'size')).reset_index()
days['g'] = days.day.map(gprev)
disc = days[days.set == 'frozen_disc']
T = float(disc.g.dropna().median())
print("discovery defined days", int(disc.g.notna().sum()), "of", len(disc), "values", sorted(disc.g.dropna().astype(int)), "T =", T)
days['on'] = days.g > T
days['undef'] = days.g.isna()
# S1: per-block median
S1 = days.groupby('blk').g.median()
days['on_s1'] = days.g > days.blk.map(S1)
print("S1 block medians", S1.to_dict())
print(days[['day', 'blk', 'n', 'g', 'on', 'undef', 'on_s1']].to_string())
fz = pd.read_parquet('/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet', columns=['day', 'mint', 'q_trig']).drop_duplicates(['day', 'mint'])
x = x.merge(fz, on=['day', 'mint'], how='left', validate='m:1')
x = x.merge(days[['day', 'on', 'on_s1', 'undef']], on='day')
assert x.q_trig.notna().all() and len(x) == 2826

def models(pnl):
    return {"flat": 0.85 * pnl + 0.15 * -PRIO, "press": (1 - x.p_fail.values) * pnl + x.p_fail.values * -PRIO}

def boot_lo(v, d, draws=1000, seed=1):
    rng = np.random.default_rng(seed); ud = np.unique(d); idx = {u: np.where(d == u)[0] for u in ud}
    return float(np.percentile([np.concatenate([v[idx[u]] for u in rng.choice(ud, len(ud))]).mean() for _ in range(draws)], 5))

def placebo(v, d, onmask, draws=1000, seed=1):
    ud = np.array(sorted(set(d))); k = len(set(d[onmask]))
    if k == 0: return np.nan, np.nan
    obs = v[onmask].mean(); rng = np.random.default_rng(seed); ge = 0; ok = 0
    for _ in range(draws):
        pick = set(rng.choice(ud, k, replace=False)); m = np.isin(d, list(pick))
        if m.sum() == 0: continue
        ok += 1; ge += v[m].mean() >= obs
    return ge / ok, obs

def run(setname, col, label):
    s = (x.set == setname).values
    on = x[col].values & s
    d = x.day.values
    print(f"\n=== {label} | {setname} ===")
    dd = days[days.set == setname]
    print("days total", len(dd), "on", int(dd[col.replace('on_s1', 'on_s1')].sum()), "off", int((~dd[col]).sum()), "of which undefined-off", int(dd.undef.sum()))
    print("trades all", int(s.sum()), "kept", int(on.sum()))
    res = {}
    for m, arr in models(x.pnl_base_el2.values).items():
        pct = arr / 1e8 * 100  # percent of the 0.1 SOL stake
        al, kp = pct[s], pct[on]
        if on.sum() == 0:
            print(m, "no kept trades; all-mean %.4f" % al.mean()); res[m] = None; continue
        bd = pd.Series(kp).groupby(d[on]).sum()
        tot = kp.sum(); ex3 = tot - np.sort(kp)[::-1][:3].sum(); exd = tot - bd.max()
        pp, obs = placebo(pct, d, on)
        lo = boot_lo(kp, d[on])
        print(f"{m}: mean_all {al.mean():.4f}% mean_on {kp.mean():.4f}% delta_pp {kp.mean() - al.mean():.4f} | "
              f"total_all {al.sum() / 100 * 0.1:.4f} SOL total_on {tot / 100 * 0.1:.4f} SOL ex_top3 {ex3 / 100 * 0.1:.4f} ex_bestday({bd.idxmax()}) {exd / 100 * 0.1:.4f} | "
              f"days_pos {int((bd > 0).sum())}/{len(bd)} CI90lo {lo:.4f}% placebo_p {pp:.4f}")
        res[m] = dict(mean=kp.mean(), ex3=ex3, exd=exd, p=pp, nd=len(bd))
    if on.sum():
        q = x.q_trig.values[on]; Q = np.median(q); nd = len(set(d[on])); tpd = on.sum() / nd
        print(f"capacity: trades/on-day {tpd:.3f}, median Q {Q:.2f} SOL")
        for m, arr in models(x.pnl_base_el2.values).items():
            mean = (arr / 1e8 * 100)[on].mean()
            for st in (0.25, 1.0):
                hc = 2 * st / (Q + st) * 100
                print(f"  {m} stake {st}: haircut {hc:.2f}% mean_after {mean - hc:.2f}% net/day {tpd * st * (mean - hc) / 100:.4f} SOL (rough, first order)")
    return res

out = {}
for s in ("frozen_disc", "frozen_conf"):
    out[s] = run(s, 'on', 'PRIMARY T=%d' % T)
for s in ("frozen_disc", "frozen_conf"):
    run(s, 'on_s1', 'S1 report-only block-median')
c = out["frozen_conf"]; kills = []
nd = int(days[(days.set == 'frozen_conf')].on.sum())
if nd < 4: kills.append("4 fewer than 4 on-days (%d)" % nd)
for m in ("flat", "press"):
    r = c[m]
    if r is None: continue
    if r["mean"] <= 0: kills.append(f"1 mean<=0 {m}")
    if r["p"] > 0.10: kills.append(f"2 placebo p {m}")
    if r["ex3"] <= 0 or r["exd"] <= 0: kills.append(f"3 top3/one-day {m}")
print("\nCONFIRMATION kill lines hit:", kills or "none")
