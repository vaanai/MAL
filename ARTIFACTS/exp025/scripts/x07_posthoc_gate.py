"""POST-HOC ONLY (selected after seeing CONFIRMATION). Gate-style stats of the primary book's trades with top-holder share <= 0.5 (filter applied to
the taken trades; per-mint non-overlap not re-run). For the next pre-registration, never a verdict."""
import sys, os, numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from mlcommon import *
O = '/data/mal/hunt-1008/c1-cascade-postgrad/ml'
d1, d2 = load(f'{O}/disc.npz'), load(f'{O}/conf.npz')
d = {k: np.concatenate([d1[k], d2[k]]) for k in d1 if k not in ('F', 'fnames', 'days', 'day')}
d['F'] = np.vstack([d1['F'], d2['F']]); d['fnames'] = d1['fnames']
dn = np.concatenate([d1['days'][d1['day']], d2['days'][d2['day']]])
z = np.load(f'{O}/confirm_primary_trades.npz'); bi = z['idx']
h1 = col(d, 'h_top1')[bi]
for name, m in (('h_top1<=0.5', h1 <= 0.5), ('h_top1>0.5', h1 > 0.5)):
    sub = bi[m]
    for leg in ('p', 'b'):
        L = legs(d, sub, 'E3', leg)
        print(name, leg, 'flat ', fmt(gate(L['flat'], dn[sub])))
        print(name, leg, 'press', fmt(gate(L['press'], dn[sub])))
    blk = d['blk'][sub]; L = legs(d, sub, 'E3', 'p')
    print('   per block flat:', {b: (int((blk == b).sum()), round(float(L['flat'][blk == b].mean() / SIZE * 100), 3)) for b in sorted(set(blk))})
    print('   per day n/flat SOL:', {x[5:]: (int((dn[sub] == x).sum()), round(float(L['flat'][dn[sub] == x].sum() / 1e9), 3)) for x in sorted(set(dn[sub]))})
# post-hoc bot illustration on the h_top1 <= 0.5 subset
sub = bi[h1 <= 0.5]; L = legs(d, sub, 'E3', 'p'); qr = col(d, 'qreal')[sub]
print('posthoc subset: trades/day', len(sub) / 21, 'median qreal', float(np.median(qr)), 'p10 qreal', float(np.percentile(qr, 10)))
for K in (2, 4, 8):
    for lname in ('flat', 'press'):
        r = botsim(d['t'][sub], d['xt_E3_p'][sub], L[lname] / SIZE, qr, dn[sub], K=K)
        print('BOT', K, lname, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k != 'daily'}, {x[5:]: round(e, 3) for x, e in r['daily'].items()})
