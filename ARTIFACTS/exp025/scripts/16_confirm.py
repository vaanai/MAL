"""C1 v2 CONFIRMATION scorer (ML venv). Runs ONCE, after RULE.md + rule.json are frozen and their sha256 is in REPORT.md.
python 16_confirm.py <rule.json> <disc.npz> <conf.npz> <out.json>
Walk-forward (pre-declared in RULE.md): for each confirmation UTC day D, the stage-2 model is retrained on every stage-1 row with t < D 00:00 - purge
(discovery + earlier confirmation days; labels of those rows are resolved by then), then scores day D's stage-1 rows. Fixed hyper-parameters and
threshold from rule.json. Books, fail legs, gate statistics, per block, lift over stage 1 (paired), bot illustration."""
import sys, os, json
import numpy as np, lightgbm as lgb
sys.path.insert(0, os.path.dirname(__file__))
from mlcommon import *
from sklearn.neighbors import KNeighborsRegressor

cfg = json.load(open(sys.argv[1]))
d1, d2 = load(sys.argv[2]), load(sys.argv[3])
assert list(d1['fnames']) == list(d2['fnames'])
F = np.vstack([d1['F'], d2['F']])
cat = lambda k: np.concatenate([d1[k], d2[k]])
d = {k: cat(k) for k in d1 if k not in ('F', 'fnames', 'days', 'day')}
d['F'] = F; d['fnames'] = d1['fnames']
dn = np.concatenate([d1['days'][d1['day']], d2['days'][d2['day']]])
isconf = np.concatenate([np.zeros(len(d1['t']), bool), np.ones(len(d2['t']), bool)])
s1 = stage1_mask(d, tuple(cfg['stage1']))
ex = cfg['exit']; th = cfg['threshold']; purge = cfg['purge_s']; nbr = cfg['rounds']
y = np.clip(d[f'pnl_{ex}_{cfg["train_leg"]}'] / SIZE, cfg['clip'][0], cfg['clip'][1])
pred = np.full(len(F), np.nan)
cdays = sorted(set(dn[isconf]))
for D in cdays:
    t0 = int(np.datetime64(D + 'T00:00:00').astype('datetime64[s]').astype(np.int64))
    tr = s1 & (d['t'] < t0 - purge)
    te = s1 & isconf & (dn == D)
    if te.sum() == 0:
        continue
    m = lgb.train(lgb_params(cfg['objective']), lgb.Dataset(F[tr], y[tr]), num_boost_round=nbr)
    p = m.predict(F[te])
    if cfg['model'] == 'ens':
        imp = m.feature_importance('gain'); top = np.argsort(-imp)[:cfg['knn_features']]
        Q = [np.nanpercentile(F[tr][:, j], np.linspace(0, 100, 101)) for j in top]
        rk = lambda A: np.column_stack([np.searchsorted(Q[i], np.nan_to_num(A[:, j], nan=-1e30)) / 101.0 for i, j in enumerate(top)])
        tri = np.where(tr)[0]; sub = np.random.default_rng(1).choice(tri, size=min(cfg['knn_train'], len(tri)), replace=False)
        knn = KNeighborsRegressor(n_neighbors=cfg['knn_k'], n_jobs=3).fit(rk(F[sub]), y[sub])
        p = (p + knn.predict(rk(F[te]))) / 2
    pred[te] = p
    print(D, 'train', int(tr.sum()), 'test', int(te.sum()), 'selected', int((p > th).sum()), flush=True)

conf_s1 = s1 & isconf
sel = conf_s1 & (pred > th)
OUT = {'rule': cfg, 'conf_days': cdays}
for leg in ('p', 'b'):
    bi = book(d, sel, ex, leg)
    L = legs(d, bi, ex, leg)
    OUT[f'book_{leg}'] = {k: gate(L[k], dn[bi]) for k in ('nofail', 'flat', 'press')}
    OUT[f'book_{leg}']['press_intercept'] = L['c']; OUT[f'book_{leg}']['press_mean_p'] = float(L['p'][L['filled']].mean()) if L['filled'].any() else None
    OUT[f'book_{leg}']['guarded'] = int((~L['filled']).sum())
    OUT[f'book_{leg}']['meets_gate_flat_press'] = bool(meets(OUT[f'book_{leg}']['flat']) and meets(OUT[f'book_{leg}']['press']))
    blk = d['blk'][bi]
    OUT[f'book_{leg}']['per_block'] = {b: {k: gate(L[k][blk == b], dn[bi][blk == b]) for k in ('nofail', 'flat', 'press')} for b in sorted(set(blk))}
    # daily table (flat and press)
    OUT[f'book_{leg}']['daily'] = {D: dict(n=int((dn[bi] == D).sum()), flat_sol=float(L['flat'][dn[bi] == D].sum() / 1e9),
                                           press_sol=float(L['press'][dn[bi] == D].sum() / 1e9)) for D in cdays}
    # stage-1-only book on the same confirmation days, and the paired lift on the same stage-1 book trades
    b1 = book(d, conf_s1, ex, leg)
    L1 = legs(d, b1, ex, leg)
    OUT[f'stage1_{leg}'] = {k: gate(L1[k], dn[b1]) for k in ('nofail', 'flat', 'press')}
    insel = sel[b1]
    x1 = d[f'pnl_{ex}_{leg}'][b1] / SIZE * 100
    diff = np.where(insel, x1, np.nan)
    ud = sorted(set(dn[b1]))
    rng = np.random.default_rng(1)
    dd = np.array([(np.nanmean(x1[(dn[b1] == D) & insel]) if ((dn[b1] == D) & insel).any() else np.nan, x1[dn[b1] == D].mean()) for D in ud])
    ok = ~np.isnan(dd[:, 0])
    dl = dd[ok, 0] - dd[ok, 1]
    bs = [dl[rng.integers(0, len(dl), len(dl))].mean() for _ in range(1000)] if len(dl) else [np.nan]
    OUT[f'lift_{leg}'] = dict(sel_mean_pct=float(np.nanmean(diff)) if insel.any() else None, s1_mean_pct=float(x1.mean()), n_sel=int(insel.sum()), n_s1=len(b1),
                              day_paired_lift_pp=float(dl.mean()) if len(dl) else None, day_paired_lift_ci90_lo=float(np.percentile(bs, 5)),
                              day_paired_lift_ci90_hi=float(np.percentile(bs, 95)))
    # stage-1 recall/precision proxies on the book: share of stage-1 trades selected, mean of non-selected
    OUT[f'lift_{leg}']['nonsel_mean_pct'] = float(x1[~insel].mean()) if (~insel).any() else None
    if leg == 'p':
        qr = col(d, 'qreal')[bi]
        r_flat = L['flat'] / SIZE; r_press = L['press'] / SIZE
        OUT['bot'] = {}
        for K in (2, 4, 8):
            for lname, r in (('flat', r_flat), ('press', r_press)):
                bs_ = botsim(d['t'][bi], d[f'xt_{ex}_p'][bi], r, qr, dn[bi], K=K)
                OUT['bot'][f'K{K}_{lname}'] = bs_
        # capacity: trades per day, median pool real quote at entry
        ev = sorted([(int(a), 1) for a in d['t'][bi]] + [(int(b), -1) for b in d[f'xt_{ex}_p'][bi]], key=lambda z: (z[0], z[1]))
        cur = mx = 0
        for _, s_ in ev:
            cur += s_; mx = max(mx, cur)
        OUT['capacity'] = dict(trades_per_day=len(bi) / max(len(cdays), 1), median_qreal_sol=float(np.median(qr)) if len(bi) else None,
                               p10_qreal_sol=float(np.percentile(qr, 10)) if len(bi) else None, max_concurrent=mx)
        np.savez_compressed(sys.argv[4].replace('.json', '_trades.npz'), idx=bi, t=d['t'][bi], mid=d['mid'][bi], day=dn[bi], blk=blk, nofail=L['nofail'],
                            flat=L['flat'], press=L['press'], xt=d[f'xt_{ex}_p'][bi], pred=pred[bi])
json.dump(OUT, open(sys.argv[4], 'w'), indent=1, default=float)
for leg in ('p', 'b'):
    for k in ('nofail', 'flat', 'press'):
        print(leg, k, fmt(OUT[f'book_{leg}'][k]))
    print(leg, 'meets gate (flat & press):', OUT[f'book_{leg}']['meets_gate_flat_press'])
    print(leg, 'stage1-only flat', fmt(OUT[f'stage1_{leg}']['flat']))
    print(leg, 'lift', OUT[f'lift_{leg}'])
