"""POST-HOC (after the one confirmation read; lessons only, never a rule): what did the September picks look like?"""
import sys, os, numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from mlcommon import *
O = '/data/mal/hunt-1008/c1-cascade-postgrad/ml'
d1, d2 = load(f'{O}/disc.npz'), load(f'{O}/conf.npz')
fn = list(d1['fnames']); F = np.vstack([d1['F'], d2['F']])
cat = lambda k: np.concatenate([d1[k], d2[k]])
pnl = cat('pnl_E3_p'); t = cat('t'); mid = cat('mid')
z = np.load(f'{O}/confirm_primary_trades.npz'); bi = z['idx']
wf = np.load(f'{O}/wf_pred_E3.npz')['pred']; s1d = stage1_mask(d1, (1, 3, 20, 20))
bd = book(d1, s1d & (wf > 0.02), 'E3')
x = pnl[bi] / SIZE * 100
print('conf picks', len(bi), 'disc WF picks', len(bd))
for f in ['age', 'qreal', 'v5', 'new5', 'wb5_new', 'h_top1', 'r_ath', 'vol15', 'avgbuy5', 'rgrad', 'r15', 'surge', 'ws5_n', 'wb5_n']:
    j = fn.index(f)
    print(f'{f:10s} conf picks p50 {np.nanmedian(F[bi, j]):10.4f} | disc picks p50 {np.nanmedian(d1["F"][bd, j]):10.4f}')
qr = F[bi, fn.index('qreal')]; age = F[bi, fn.index('age')]; wn = F[bi, fn.index('wb5_new')]; h1 = F[bi, fn.index('h_top1')]
for name, m in (('qreal<400', qr < 400), ('qreal>=400', qr >= 400), ('age<1800', age < 1800), ('age<1800 & wb5_new>0.9 & qreal<400', (age < 1800) & (wn > 0.9) & (qr < 400)),
                ('h_top1>0.5', h1 > 0.5), ('h_top1<=0.5', h1 <= 0.5)):
    print(f'{name:38s} n={m.sum():5d} mean%={x[m].mean():8.3f} win={np.mean(x[m] > 0):.3f} p1={np.percentile(x[m], 1) if m.sum() else np.nan:8.2f}')
