"""P5: H5 v1 (Q*=40) Sept CONFIRMATION trade rows from h5-flows/out/boostdip_frozen_conf.parquet, repriced to 160,000/send + E1 floor 0.768% of stake. In-sample exploration."""
import sys, numpy as np, pandas as pd
sys.path.insert(0,'/data2/mal/wt/p5-stack')
from tools.latency_curve import Pressure, fit_curve
SEND=160_000; OLD=55_000; E1=384_022/0.05e9
d=pd.read_parquet('/data/mal/hunt-1008/h5-flows/out/boostdip_frozen_conf.parquet')
d=d[(d.D==40)&(d.H=='end')]
print(d.groupby(['leg','stake']).size())
out={}
for leg in ('p','b'):
    x0=d[(d.leg==leg)&(d.stake=='01')].reset_index(drop=True)
    x1=d[(d.leg==leg)&(d.stake=='025')].reset_index(drop=True)
    assert (x0.mint==x1.mint).all() and (x0.day==x1.day).all()
    curve=fit_curve([Pressure(int(a),int(b)) for a,b in zip(x1.ssb,x1.nb)])
    pp=np.array([curve.p(Pressure(int(a),int(b))) for a,b in zip(x1.ssb,x1.nb)])
    for nm,x,S in (('01',x0,0.1e9),('025',x1,0.25e9)):
        pnl=x.pnl.values.astype(float)-2*(SEND-OLD)-E1*S
        out[(leg,nm,'flat')]=(0.85*pnl+0.15*(-SEND))/1e9
        out[(leg,nm,'press')]=((1-pp)*pnl+pp*(-SEND))/1e9
    out[(leg,'day')]=x1.day.values; out[(leg,'trig_t')]=x1.trig_t.values; out[(leg,'q')]=x1.q_trig.values
    out[(leg,'p_mean')]=pp.mean()
import pickle; pickle.dump(out,open('/data/mal/hunt-1008/push-1016/hunt29/p5/h5_rows.pkl','wb'))
for leg in ('p','b'):
  for nm,S in (('01',.1),('025',.25)):
    for f in ('flat','press'):
        v=out[(leg,nm,f)]; print(leg,nm,f,'mean %.3f%%'%(100*v.mean()/S),'n',len(v),'tot',round(v.sum(),2))
print('pmean',out[('b','p_mean')])
days=sorted(set(out[('b','day')])); 
for lo,hi,nmb in (('2026-09-03','2026-09-09','fresh-0903'),('2026-09-10','2026-09-17','exp011'),('2026-09-18','2026-09-21','fastpool'),('2026-09-22','2026-09-26','oracle')):
    m=(out[('b','day')]>=lo)&(out[('b','day')]<=hi)
    print(nmb,m.sum(),[round(100*out[('b','025',f)][m].mean()/.25,2) for f in('flat','press')])
