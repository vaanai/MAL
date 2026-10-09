"""C1 v2 export: superset rows (candx + the grid's stage-1 features) -> one compressed npz for the ML venv (numpy only there).
Usage: python 14_export.py <out.npz> <day> [<day> ...]
Features exclude every landing-time or future field (ssb_*, nearby_*, exr_*, guard_*, pnl_*, xt_*, xk_*, s_slot, fmax*, fend60)."""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from common2 import *
out, days = sys.argv[1], sys.argv[2:]
GCOLS = ['mid', 't', 'age', 'v1', 'v5', 'v15', 'v60', 'bs5', 'ss5', 'net5', 'netp5', 'nb5', 'n5', 'new5', 'newp5', 'qreal', 'r5', 'r15', 'r60', 'rgrad', 'surge',
         'fmax60']
parts = []
for d in days:
    X = pd.read_parquet(f'{O}/out/candx/{d}.parquet')
    g = pd.read_parquet(f'{O}/out/grid/{d}.parquet', columns=GCOLS)
    X = X.merge(g, on=['mid', 't'], how='left')
    parts.append(X)
X = pd.concat(parts, ignore_index=True).sort_values(['t', 'mid']).reset_index(drop=True)
X['hod'] = ((X.t % 86400) // 3600).astype(np.float32)
LAB = [c for c in X.columns if c.startswith(('pnl_', 'xt_', 'xk_', 'ssb_', 'nearby_', 'exr_', 'guard_'))]
META = ['mid', 't', 'day', 'seg', 'blk', 's_slot', 'fmax60']
FEAT = [c for c in X.columns if c not in LAB and c not in META]
ud = np.array(sorted(X.day.astype(str).unique().tolist()))
np.savez_compressed(out, F=X[FEAT].to_numpy(np.float32), fnames=np.array(FEAT), mid=X.mid.values.astype(np.int64), t=X.t.values.astype(np.int64),
                    day=np.searchsorted(ud, np.array(X.day.astype(str).tolist())), days=np.array(ud.tolist(), dtype='U10'), seg=X.seg.values.astype(np.int8), blk=np.array(X.blk.astype(str).tolist(), dtype='U32'),
                    fmax60=X.fmax60.values.astype(np.float32),
                    **{c: X[c].to_numpy(np.float64 if c.startswith('pnl_') else np.int64 if c.startswith('xt_') else np.float32) for c in LAB})
print(out, X.shape, len(FEAT), 'features', 'days', ud[0], ud[-1])
