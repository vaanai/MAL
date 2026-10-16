import sys, time, importlib.util
import numpy as np, pandas as pd, duckdb
from pathlib import Path
WT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, WT)
spec = importlib.util.spec_from_file_location('E', WT + '/scripts/research/r4b_equiv_d.py')
E = importlib.util.module_from_spec(spec); spec.loader.exec_module(E)
from tools import r4b_features as rf
out = Path('/data/mal/hunt-1008/push-1016/hunt6/r4b-equiv-d/full2')
day = sys.argv[1]
work = out / 'adapter' / 'work'
mcn = pd.read_pickle(work / 'mintconst.pkl'); mkt = np.load(work / 'mkt.npz')
prep = rf.build_prep(np.load(work / 'clock_bt.npy'), np.load(work / 'clock_slot.npy'), mkt['mnt'], {k: mkt[k] for k in ('n_ps', 'v_ps', 'n_bd', 'v_bd', 'n_cr')}, gr=mcn.mbt.values.astype(np.int64))
sys.path.insert(0, str(E.L3 / 'scripts')); from common import SPH
duck = duckdb.connect(); duck.sql("SET memory_limit='8GB'; SET threads=4")
d = E.day_features(out, day, prep, mcn, SPH, False, duck)
z = np.load(E.L3 / 'feat' / f'{day}.npz'); Xl = z['X']; I = z['I']
bad = ~((d['X'] == Xl) | (np.isnan(d['X']) & np.isnan(Xl)))
rows = bad.any(axis=1); mids = np.unique(I[rows, 0]); print('X bad rows', rows.sum(), 'mints', len(mids), 'of', len(np.unique(I[:, 0])))
dc = pd.read_parquet(E.LAYA / 'cache/l3/dec' / f'{day}.parquet', columns=['Qe13', 'Be13', 'Qe19', 'Be19', 'spot', 'y_p_ssb', 'y_p_nearby', 'y_b_ssb', 'y_b_nearby']).to_numpy(float)
bd = ~((d['dec'] == dc) | (np.isnan(d['dec']) & np.isnan(dc)))
dm = np.unique(I[bd.any(axis=1), 0]); print('dec bad mints', len(dm), 'Xbad subset of decbad?', set(mids) <= set(dm), len(set(dm) - set(mids)), len(set(mids) - set(dm)))
d0 = int(pd.Timestamp(day, tz='UTC').timestamp())
hrs = [h for h in (time.strftime('%Y-%m-%dT%H', time.gmtime(d0 + 3600 * i)) for i in range(80)) if (out / 'adapter' / 'ut' / f'{h}.parquet').exists()]
fl = ",".join(f"'{out}/adapter/ut/{h}.parquet'" for h in hrs)
dup = duck.sql(f"select mid,count(*) from (select mid,slot,txi,evi,count(*) c from read_parquet([{fl}]) group by 1,2,3,4 having c>1 and count(distinct (isbuy,sol,tok,q,b,th))>1) group by 1").fetchall()
dupm = {m for m, _ in dup}
print('mints with dup keys', len(dupm), '; Xbad with dup', len(set(mids) & dupm), '; dec-bad with dup', len(set(dm) & dupm), '; dup not dec-bad', len(dupm - set(dm)))
print('dec-bad w/o dup:', sorted(set(dm) - dupm)[:10])
print('Xbad w/o dup:', sorted(set(mids) - dupm)[:10])
names = ['Qe13', 'Be13', 'Qe19', 'Be19', 'spot', 'ssb13', 'nearby13', 'ssb19', 'nearby19']
print('per col bad rows', dict(zip(names, bd.sum(axis=0).tolist())))
i = np.nonzero(bd.any(axis=1))[0][0]
print('first', i, I[i], names, d['dec'][i], dc[i], bd[i])
sub = np.nonzero(bd.any(axis=1) & ~np.isin(I[:, 0], list(dupm)))[0][:5]
for i in sub: print(I[i], 'bad cols', [names[c] for c in np.nonzero(bd[i])[0]], d['dec'][i][bd[i]], dc[i][bd[i]])
mr = pd.read_parquet(E.LAYA / 'cache/l3/marks' / f'{day}.parquet')
refm = np.column_stack([mr.mid.to_numpy(np.int64), mr.t.to_numpy(np.int64), mr.Qx.to_numpy(float), mr.Bx.to_numpy(float)])
bm = ~((d['mk'] == refm) | (np.isnan(d['mk']) & np.isnan(refm))); mm = set(np.unique(refm[bm.any(axis=1), 0]).tolist())
nt = ~np.isin(I[:, 0], list(dupm))
ntm = ~np.isin(refm[:, 0], list(dupm))
print('SUMMARY', day, 'tie-mints', len(dupm), 'rows in non-tie mints', int(nt.sum()), 'X diff', int(bad[nt].any(axis=1).sum()), 'dec diff', int(bd[nt].any(axis=1).sum()),
      'marks rows non-tie', int(ntm.sum()), 'marks diff non-tie', int(bm[ntm].any(axis=1).sum()), 'marks diff mints', len(mm), 'of which tie', len(mm & dupm),
      'I non-tie md5 eq', E.md5(d['I'][nt]) == E.md5(I[nt]), 'X non-tie md5', E.md5(d['X'][nt]), E.md5(np.asarray(Xl[nt], np.float32)), 'dec', E.md5(d['dec'][nt]), E.md5(dc[nt]), 'mk', E.md5(d['mk'][ntm]), E.md5(refm[ntm]))
