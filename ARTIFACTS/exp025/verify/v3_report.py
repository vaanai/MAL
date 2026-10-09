"""C1-NF VERIFY step v3: books, legs, statistics and the JUDGE-4 3.3.1 kill rules. Audit venv (numpy, pandas). Own book / legs / stats code
(not mlcommon), same definitions: book = rows in (t, mid) order, a mint holds one position, next entry only at t >= max(exit, t) + 60 s;
flat leg: a fill pays 0.85 pnl + 0.15 (-fee), a guarded row -fee; pressure: p = sigmoid(c + 0.8 log1p(ssb) + 0.35 log1p(nearby SOL)), c fitted
so the mean p over the book's fills is 0.289; trade CI90 lo (1,000 draws, seed 1, 5th pct); date-cluster CI90 lo (1,000 date resamples, seed 1);
one-sided day-level t (mean of date means / (sd / sqrt(W)), W - 1 df)."""
import sys, json, math
import numpy as np, pandas as pd

V = '/data/mal/hunt-1008/c1nf-verify'
C1 = '/data/mal/hunt-1008/c1-cascade-postgrad'
SIZE = 250_000_000
F55, F505, RENT = 55_000, 505_000, 2_039_280
OUT = {}


def t_sf(t, df):
    """P(T > t), Student t, via the regularized incomplete beta (continued fraction)."""
    if not np.isfinite(t):
        return 0.0 if t > 0 else 1.0
    x = df / (df + t * t); a, b = df / 2.0, 0.5

    def cf(a, b, x):
        qab, qap, qam = a + b, a + 1, a - 1
        c, d = 1.0, 1 - qab * x / qap
        d = 1 / d if abs(d) > 1e-300 else 1e300; h = d
        for m in range(1, 300):
            m2 = 2 * m
            aa = m * (b - m) * x / ((qam + m2) * (a + m2))
            d = 1 + aa * d; d = 1 / d if abs(d) > 1e-300 else 1e300; c = 1 + aa / c if abs(c) > 1e-300 else 1e300; h *= d * c
            aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
            d = 1 + aa * d; d = 1 / d if abs(d) > 1e-300 else 1e300; c = 1 + aa / c if abs(c) > 1e-300 else 1e300; dl = d * c; h *= dl
            if abs(dl - 1) < 1e-14:
                break
        return h
    lb = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    ib = math.exp(lb) * cf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - math.exp(lb) * cf(b, a, 1 - x) / b
    p = 0.5 * ib
    return p if t >= 0 else 1 - p


def stats(x_lam, days, blks=None):
    x = np.asarray(x_lam, float) / 1e9; n = len(x)
    if n == 0:
        return dict(n=0)
    sz = SIZE / 1e9
    rng = np.random.default_rng(1)
    lo_t = np.percentile(x[rng.integers(0, n, size=(1000, n))].mean(1), 5)
    ud, inv = np.unique(np.asarray(days), return_inverse=True)
    ds = np.bincount(inv, weights=x); dn = np.bincount(inv); w = len(ud)
    rng2 = np.random.default_rng(1)
    di = rng2.integers(0, w, size=(1000, w))
    lo_d = np.percentile(ds[di].sum(1) / dn[di].sum(1), 5)
    dm = ds / dn
    tday = pday = None
    if w >= 2 and dm.std(ddof=1) > 0:
        tday = float(dm.mean() / (dm.std(ddof=1) / math.sqrt(w))); pday = t_sf(tday, w - 1)
    s = np.sort(x)
    r = dict(n=n, days=w, days_pos=int((ds > 0).sum()), mean_pct=100 * x.mean() / sz, ciT=100 * lo_t / sz, ciD=100 * lo_d / sz, total=float(x.sum()),
             ex_top3=float(x.sum() - s[-3:].sum()), ex_top10=float(x.sum() - s[-10:].sum()), ex_best_day=float(x.sum() - ds.max()), t_day=tday, p_day=pday,
             win=float((x > 0).mean()))
    if blks is not None:
        bb = np.asarray(blks); pb = {}
        for b in sorted(set(bb)):
            m = bb == b
            pb[b] = dict(n=int(m.sum()), mean_pct=100 * x[m].mean() / sz, total=float(x[m].sum()))
        r['blocks'] = pb
        r['ex_best_block'] = float(x.sum() - max(v['total'] for v in pb.values()))
    return r


def book(t, mid, xt, sel, cool=60):
    order = np.lexsort((mid, t)); free = {}; taken = []
    for i in order:
        if not sel[i]:
            continue
        m = int(mid[i])
        if t[i] < free.get(m, -1):
            continue
        taken.append(i); free[m] = max(int(xt[i]), int(t[i])) + cool
    return np.array(taken, dtype=np.int64)


def legs(pnl_gross, guarded, ssb, nearby, fee, rent=0):
    """pnl_gross: before send fees. Returns per-attempt lamports for nofail / flat / press."""
    filled = ~np.asarray(guarded, bool)
    pnl = np.where(filled, pnl_gross - 2 * fee - rent, -float(fee))
    flat = np.where(filled, 0.85 * pnl + 0.15 * (-fee), pnl)
    z0 = 0.8 * np.log1p(ssb) + 0.35 * np.log1p(np.asarray(nearby, float) / 1e9)
    lo, hi = -40.0, 40.0
    zf = z0[filled]
    for _ in range(80):
        c = (lo + hi) / 2
        if (1 / (1 + np.exp(-(c + zf)))).mean() > 0.289:
            hi = c
        else:
            lo = c
    c = (lo + hi) / 2; p = 1 / (1 + np.exp(-(c + z0)))
    press = np.where(filled, (1 - p) * pnl + p * (-fee), pnl)
    return dict(nofail=pnl, flat=flat, press=press, c=c)


def brief(g):
    if g.get('n', 0) == 0:
        return 'n=0'
    return (f"n={g['n']} days={g['days_pos']}/{g['days']} mean={g['mean_pct']:.3f}% ciT={g['ciT']:.3f}% ciD={g['ciD']:.3f}% tot={g['total']:.4f} "
            f"exTop3={g['ex_top3']:.4f} exTop10={g['ex_top10']:.4f} exBestDay={g['ex_best_day']:.4f} dayT_p={g['p_day'] if g['p_day'] is None else round(g['p_day'], 6)}")


# ------------------------------------------------------------------ load
z = np.load(f'{V}/v/cands.npz', allow_pickle=False)
C = pd.DataFrame({k: z[k] for k in z.files})
S = pd.read_parquet(f'{V}/v/sim.parquet')
C = C.merge(S, on='idx', how='left', suffixes=('', '_sim'))
assert len(C) == len(z['idx'])
OUT['sim_errors'] = int((C.err.fillna('x') != '').sum()); OUT['pool_mismatch'] = int((~C.pool_match.fillna(False).astype(bool)).sum())
conf = C[C.isconf].reset_index(drop=True); disc = C[~C.isconf].reset_index(drop=True)
print('candidate rows conf', len(conf), 'disc', len(disc), 'sim errors', OUT['sim_errors'], 'canonical-pool mismatches', OUT['pool_mismatch'])

# ------------------------------------------------------------------ 1. frozen primary: rebuilt pinned scorer vs C1's file
o = np.load(f'{C1}/ml/confirm_primary_trades.npz'); r_ = np.load(f'{V}/ml/confirm_primary_trades.npz')
ko = {(int(a), int(b)): i for i, (a, b) in enumerate(zip(o['t'], o['mid']))}
kr = {(int(a), int(b)): i for i, (a, b) in enumerate(zip(r_['t'], r_['mid']))}
both = sorted(set(ko) & set(kr)); uni = set(ko) | set(kr)
io = np.array([ko[k] for k in both]); ir = np.array([kr[k] for k in both])
rep = dict(n_orig=len(ko), n_rebuilt=len(kr), n_both=len(both), status_agreement=len(both) / len(uni),
           nofail_equal_1lam=float((np.abs(o['nofail'][io] - r_['nofail'][ir]) <= 1).mean()) if len(both) else None,
           flat_equal_1lam=float((np.abs(o['flat'][io] - r_['flat'][ir]) <= 1).mean()) if len(both) else None,
           press_equal_1lam=float((np.abs(o['press'][io] - r_['press'][ir]) <= 1).mean()) if len(both) else None,
           pred_max_abs_diff=float(np.abs(o['pred'][io] - r_['pred'][ir]).max()) if len(both) else None)
OUT['primary_repro'] = rep
print('\n== 1. frozen primary repro', rep)
cj = json.load(open(f'{V}/ml/confirm_primary.json'))
for leg in ('p', 'b'):
    for k in ('flat', 'press'):
        g = cj[f'book_{leg}'][k]
        print(f'   rebuilt pinned 16_confirm {leg} {k}: n={g["n"]} pos={g["days_pos"]}/{g["days"]} mean={g["mean_pct"]:.3f} ciT={g["ci_trade_lo_pct"]:.3f} ciD={g["ci_date_lo_pct"]:.3f} tot={g["total_sol"]:.4f}')
OUT['primary_rebuilt_json'] = {leg: {k: cj[f'book_{leg}'][k] for k in ('nofail', 'flat', 'press')} for leg in ('p', 'b')}

# ------------------------------------------------------------------ 2. h_top1 causality / agreement (pipeline vs independent)
for nm, a, b in (('h_top1', conf.f_h_top1, conf.h1), ('h_top5', conf.f_h_top5, conf.h5), ('v5', conf.f_v5, conf.v5), ('new5', conf.f_new5, conf.new5),
                 ('qreal', conf.f_qreal, conf.qreal)):
    a = a.values.astype(float); b = b.values.astype(float)
    m = np.isfinite(a) & np.isfinite(b)
    rel = np.abs(a[m] - b[m]) / np.maximum(np.abs(a[m]), 1e-9)
    OUT[f'agree_{nm}'] = dict(n=int(len(a)), both_finite=int(m.sum()), nan_pattern_equal=float((np.isfinite(a) == np.isfinite(b)).mean()),
                              rel_le_1e5=float((rel <= 1e-5).mean()), rel_le_1e3=float((rel <= 1e-3).mean()), max_rel=float(rel.max()) if m.any() else None)
    if nm == 'h_top1':
        OUT['agree_h_top1']['same_side_of_0.5'] = float(((a <= 0.5) == (b <= 0.5)).mean())
    print(f'== 2. {nm} pipeline vs independent: {OUT[f"agree_{nm}"]}')

# ------------------------------------------------------------------ 3. books on PIPELINE P&L (C1's own columns), C1 order vs spec order
def pipe_legs(df, idx, leg, fee=F55):
    g = df[f'guard_{leg}'].values[idx] > 0
    gross = df[f'pnl_E3_{leg}'].values[idx] + 2 * F55     # C1's pnl has 2 x 55k sends baked in
    return legs(gross, g, df[f'ssb_{leg}'].values[idx].astype(float), df[f'nearby_{leg}'].values[idx].astype(float), fee)


def run_pipe(df, sel, leg='p', post=None, fee=F55):
    bi = book(df.t.values, df.mid.values, df[f'xt_E3_{leg}'].values, sel)
    if post is not None:
        bi = bi[post[bi]]
    L = pipe_legs(df, bi, leg, fee)
    return bi, {k: stats(L[k], df.day.values[bi], df.blk.values[bi]) for k in ('nofail', 'flat', 'press')}


allc = np.ones(len(conf), bool)
h1p = conf.f_h_top1.values.astype(float)
capp = np.isfinite(h1p) & (h1p <= 0.5)
P = {}
for leg in ('p', 'b'):
    _, P[f'primary_{leg}'] = run_pipe(conf, allc, leg)
    bi_c1, P[f'c1order_{leg}'] = run_pipe(conf, allc, 'p' if leg == 'p' else 'b', post=capp)
    bi_sp, P[f'spec_{leg}'] = run_pipe(conf, capp, leg)
    _, P[f'spec505_{leg}'] = run_pipe(conf, capp, leg, fee=F505)
OUT['pipeline'] = P
print('\n== 3. pipeline P&L (C1 columns)')
for k, v in P.items():
    print(f'   {k:14s} flat  {brief(v["flat"])}\n   {"":14s} press {brief(v["press"])}')

# ------------------------------------------------------------------ 4. independent simulator books
def sim_run(df, sel, lat='p', bnd='END', lag='l055', fee=F505, rent=0, h=None):
    tag = f'{lat}_{bnd}_{lag}'
    xt = df[f'xt_{tag}'].values.astype(np.int64)
    bi = book(df.t.values, df.mid.values, xt, sel)
    L = legs(df[f'pnl_{tag}'].values[bi], df[f'g_{tag}'].values[bi] > 0, df[f'ssb_{lat}'].values[bi].astype(float), df[f'nearby_{lat}'].values[bi].astype(float), fee, rent)
    return bi, {k: stats(L[k], df.day.values[bi], df.blk.values[bi]) for k in ('nofail', 'flat', 'press')}, L


h1 = conf.h1.values.astype(float); h5 = conf.h5.values.astype(float)
cap = lambda c: np.isfinite(h1) & (h1 <= c)
SIM = {}
bi_main, SIM['main_505'], L_main = sim_run(conf, cap(0.5))
for lat in ('p', 'b'):
    for fee, fn in ((F55, '55'), (F505, '505')):
        SIM[f'{lat}_END_l055_{fn}'] = sim_run(conf, cap(0.5), lat, 'END', 'l055', fee)[1]
        SIM[f'{lat}_END_l055_{fn}_rent'] = sim_run(conf, cap(0.5), lat, 'END', 'l055', fee, RENT)[1]
        SIM[f'{lat}_START_{fn}'] = sim_run(conf, cap(0.5), lat, 'START', 'l055', fee)[1]
        SIM[f'{lat}_WORST_{fn}'] = sim_run(conf, cap(0.5), lat, 'WORST', 'l055', fee)[1]
        SIM[f'{lat}_lag2_{fn}'] = sim_run(conf, cap(0.5), lat, 'END', 'l2', fee)[1]
        SIM[f'{lat}_lag5_{fn}'] = sim_run(conf, cap(0.5), lat, 'END', 'l5', fee)[1]
for fee, fn in ((F55, '55'), (F505, '505')):
    SIM[f'nocap_{fn}'] = sim_run(conf, allc, 'p', fee=fee)[1]
    for c in (0.3, 0.5, 0.7):
        SIM[f'cap{c}_{fn}'] = sim_run(conf, cap(c), 'p', fee=fee)[1]
    SIM[f'h5cap0.5_{fn}'] = sim_run(conf, np.isfinite(h5) & (h5 <= 0.5), 'p', fee=fee)[1]
    # x06 splits, as filters BEFORE the book (spec order) and AFTER the frozen book (C1's x06 order)
    qr = conf.f_qreal.values.astype(float); age = conf.f_age.values.astype(float); wn = conf.f_wb5_new.values.astype(float)
    splits = {'qreal<400': qr < 400, 'qreal>=400': qr >= 400, 'age<1800': age < 1800, 'age<1800&wb5_new>0.9&qreal<400': (age < 1800) & (wn > 0.9) & (qr < 400),
              'h_top1>0.5(farm)': ~cap(0.5)}
    bi_all, _, _ = sim_run(conf, allc, 'p', fee=fee)
    for nm, m in splits.items():
        SIM[f'x06pre_{nm}_{fn}'] = sim_run(conf, m, 'p', fee=fee)[1]
        tag = 'p_END_l055'; bpost = bi_all[m[bi_all]]
        Lp = legs(conf[f'pnl_{tag}'].values[bpost], conf[f'g_{tag}'].values[bpost] > 0, conf.ssb_p.values[bpost].astype(float), conf.nearby_p.values[bpost].astype(float), fee)
        SIM[f'x06post_{nm}_{fn}'] = {k: stats(Lp[k], conf.day.values[bpost], conf.blk.values[bpost]) for k in ('nofail', 'flat', 'press')}
    # C1 order inside the independent simulator (book on all picks, then cap)
    bpost = bi_all[cap(0.5)[bi_all]]
    Lp = legs(conf['pnl_p_END_l055'].values[bpost], conf['g_p_END_l055'].values[bpost] > 0, conf.ssb_p.values[bpost].astype(float), conf.nearby_p.values[bpost].astype(float), fee)
    SIM[f'c1order_{fn}'] = {k: stats(Lp[k], conf.day.values[bpost], conf.blk.values[bpost]) for k in ('nofail', 'flat', 'press')}
OUT['sim'] = SIM
print('\n== 4. independent simulator')
for k, v in SIM.items():
    print(f'   {k:40s} flat  {brief(v["flat"])}\n   {"":40s} press {brief(v["press"])}')

# row-level agreement sim vs pipeline (1.3 s END, 0.55 s lag), on the spec-order C1-NF book rows and on all picks
pp = conf.pnl_E3_p.values + 2 * F55; ps = conf.pnl_p_END_l055.values
gp = conf.guard_p.values > 0; gs = conf.g_p_END_l055.values > 0
m = ~gp & ~gs
OUT['row_agree_all_picks'] = dict(n=int(len(pp)), guard_agree=float((gp == gs).mean()), corr=float(np.corrcoef(pp[m], ps[m])[0, 1]),
                                  mean_diff_pct=float((ps[m] - pp[m]).mean() / SIZE * 100), within_1pct=float((np.abs(ps[m] - pp[m]) <= 0.01 * SIZE).mean()),
                                  within_5pct=float((np.abs(ps[m] - pp[m]) <= 0.05 * SIZE).mean()))
mb = np.zeros(len(conf), bool); mb[bi_main] = True; m2 = m & mb
OUT['row_agree_c1nf_book'] = dict(n=int(mb.sum()), corr=float(np.corrcoef(pp[m2], ps[m2])[0, 1]), mean_diff_pct=float((ps[m2] - pp[m2]).mean() / SIZE * 100),
                                  within_1pct=float((np.abs(ps[m2] - pp[m2]) <= 0.01 * SIZE).mean()), after_last_print=int(conf.after_last.fillna(False).values[mb].astype(bool).sum()),
                                  hold_s_median=float(np.nanmedian(conf.hold_s.values[mb].astype(float))), hold_s_p95=float(np.nanpercentile(conf.hold_s.values[mb].astype(float), 95)))
print('\n   row agreement (all picks):', OUT['row_agree_all_picks']); print('   row agreement (C1-NF book):', OUT['row_agree_c1nf_book'])

# per-day table of the main book (sim, 1.3 s, 505k) and 55k
for fn, fee in (('505', F505), ('55', F55)):
    bi, _, L = sim_run(conf, cap(0.5), 'p', fee=fee)
    dd = conf.day.values[bi]
    OUT[f'daily_{fn}'] = {d: dict(n=int((dd == d).sum()), flat=float(L['flat'][dd == d].sum() / 1e9), press=float(L['press'][dd == d].sum() / 1e9)) for d in sorted(set(dd))}
print('   daily (505k):', {k[5:]: (v['n'], round(v['flat'], 3), round(v['press'], 3)) for k, v in OUT['daily_505'].items()})

# ------------------------------------------------------------------ 5. discovery walk-forward book, with and without the cap
dh1 = disc.f_h_top1.values.astype(float); dh1s = disc.h1.values.astype(float)
alld = np.ones(len(disc), bool)
D = {}
for nm, sel in (('nocap', alld), ('cap0.5_pre', np.isfinite(dh1) & (dh1 <= 0.5))):
    _, D[f'pipe_{nm}'] = run_pipe(disc, sel, 'p')
    D[f'sim_{nm}_55'] = sim_run(disc, sel if nm == 'nocap' else (np.isfinite(dh1s) & (dh1s <= 0.5)), 'p', fee=F55)[1]
    D[f'sim_{nm}_505'] = sim_run(disc, sel if nm == 'nocap' else (np.isfinite(dh1s) & (dh1s <= 0.5)), 'p', fee=F505)[1]
OUT['discovery_wf'] = D
print('\n== 5. discovery walk-forward (E3, th 0.02)')
for k, v in D.items():
    print(f'   {k:22s} flat  {brief(v["flat"])}\n   {"":22s} press {brief(v["press"])}')

# out-of-support medians: discovery WF picks vs September C1-NF picks
bd, _ = run_pipe(disc, alld, 'p')[0], None
sup = {}
for f in ('qreal', 'h_top1', 'wb5_new', 'v5', 'age', 'new5'):
    sup[f] = dict(disc_wf_picks=float(np.nanmedian(disc[f'f_{f}'].values[bd])), sept_c1nf_picks=float(np.nanmedian(conf[f'f_{f}'].values[bi_main])))
OUT['support_medians'] = sup
print('   support medians', sup)

# ------------------------------------------------------------------ 6. kill rules (JUDGE-4 3.3.1 item 5), spec order, 1.3 s
def kill(fn):
    m_ = SIM[f'p_END_l055_{fn}']
    blocks = m_['press']['blocks']
    r = {'ciD_le0_either_leg': bool(m_['flat']['ciD'] <= 0 or m_['press']['ciD'] <= 0),
         'press_blocks_le0': int(sum(1 for v in blocks.values() if v['mean_pct'] <= 0)),
         'worst_in_slot_mean_le0': bool(SIM[f'p_WORST_{fn}']['flat']['mean_pct'] <= 0 or SIM[f'p_WORST_{fn}']['press']['mean_pct'] <= 0),
         'caps_0.3_and_0.7_both_le0_flat': bool(SIM[f'cap0.3_{fn}']['flat']['mean_pct'] <= 0 and SIM[f'cap0.7_{fn}']['flat']['mean_pct'] <= 0)}
    r['press_2plus_blocks_le0'] = r['press_blocks_le0'] >= 2
    r['KILL'] = bool(r['ciD_le0_either_leg'] or r['press_2plus_blocks_le0'] or r['worst_in_slot_mean_le0'] or r['caps_0.3_and_0.7_both_le0_flat'])
    return r


OUT['kill_505'] = kill('505'); OUT['kill_55'] = kill('55')
print('\n== 6. kill rules @505k (deciding cost):', OUT['kill_505']); print('   kill rules @55k:', OUT['kill_55'])
json.dump(OUT, open(f'{V}/v/results.json', 'w'), indent=1, default=float)
print('wrote v/results.json')
