"""P5 STACK $: per-book stake tables + stacked bankroll sim. EXPLORATION (Aug/Sep) in-sample screen numbers only; best-of-N; not forward evidence.
Inputs: floor-out/floor_trades.npz (job #1416), h5-flows boostdip_frozen_conf.parquet (H5 v1 Q40, Sept 3-25, 1124 trades).
Impact variants: A = screen-implied (FLOOR: kit pricing at each stake; H5: fixed-fee only, no impact growth)
                 B = capacity-pessimistic (A minus entry-impact growth vs 0.25 SOL: FLOOR s/Qe per trade; H5 2*(s-0.25)/q per trade, cap-impact style)."""
import sys, json, numpy as np, pandas as pd
sys.path.insert(0,'/data2/mal/wt/p5-stack')
from tools.latency_curve import Pressure, fit_curve
D='/data/mal/hunt-1008/push-1016/hunt29/p5/'
SEND=160_000; E1=384_022/0.05e9; FIX=2*SEND/1e9
GRID=[0.05,0.1,0.15,0.25,0.5,0.75,1.0,1.5,2.0]
rng0=np.random.default_rng(1)
# ---------------- FLOOR
z=np.load(D+'floor-out/floor_trades.npz')
T=z['T'].astype(np.int64); why=z['why']; xs=z['xs']; sd=z['sd']; Qe=z['Qe'].astype(float)
n_f=len(T); fday=T//86400
udays_f=np.unique(fday); NDAYS_F=25   # calendar days of the run per results.json (days_in_run)
off=(T%86400)+rng0.uniform(0,3600,n_f)   # hour-start + uniform jitter (decision second not dumped)
hold_f=np.where(why==0,86400+1.9,np.maximum(1.9,(xs-sd)*0.4+1.9))   # slot ~0.4 s; TP holds approximate
def scale(a): return a/1e9 if np.abs(a).max()>1e3 else a
F_r={}  # (variant,fail,s)-> return fraction per trade
for s in GRID:
    for fail in ('flat','press'):
        pn=scale(z[f'{fail}_{s}'].astype(float)); rk=pn/s
        F_r[('A',fail,s)]=rk
        imp=lambda st: st*1e9*(1-0.0125)/Qe
        F_r[('B',fail,s)]=rk-(imp(s)-imp(0.25))
# ---------------- H5
d=pd.read_parquet('/data/mal/hunt-1008/h5-flows/out/boostdip_frozen_conf.parquet')
x=d[(d.D==40)&(d.H=='end')&(d.leg=='b')&(d.stake=='025')].reset_index(drop=True)
curve=fit_curve([Pressure(int(a),int(b)) for a,b in zip(x.ssb,x.nb)])
pp=np.array([curve.p(Pressure(int(a),int(b))) for a,b in zip(x.ssb,x.nb)])
base=(x.pnl.values.astype(float)-2*(SEND-55_000)-E1*0.25e9)/1e9   # no-fail pnl SOL at 0.25, 160k, E1
r25=base/0.25; q=x.q_trig.values.astype(float); hday=x.day.values
H_r={}
for s in GRID:
    for var in ('A','B'):
        r=r25+FIX*(1/0.25-1/s)-(2*(s-0.25)/q if var=='B' else 0)
        pnl=s*r
        H_r[(var,'flat',s)]=(0.85*pnl-0.15*FIX/2)/s
        H_r[(var,'press',s)]=((1-pp)*pnl-pp*FIX/2)/s
n_h=len(x)
blk_latest=(hday>='2026-09-18')   # fast-pool-0918 + oracle-insample-0922 (+ any 09-25 rows): latest regime
hdays=np.unique(hday)
def book_table(name,R,apd,hold_mean_s,mask=None):
    rows=[]
    for var in ('A','B'):
        for fail in ('flat','press'):
            best=None
            for s in GRID:
                r=R[(var,fail,s)] if mask is None else R[(var,fail,s)][mask]
                pn=r*s; spd=apd*pn.mean()
                ex3=(pn.sum()-np.sort(pn)[-3:].sum())/len(pn)*apd
                conc=apd*hold_mean_s/86400
                rows.append(dict(book=name,variant=var,fail=fail,stake=s,mean_ret_pct=100*r.mean(),sol_per_trade=pn.mean(),sol_day=spd,sol_day_ex_top3=ex3,avg_open=conc,capital_sol=conc*s))
    return rows
tabs=[]
tabs+=book_table('FLOOR v2 (13.28/day, 24h hold)',F_r,13.28,hold_f.mean())
tabs+=book_table('H5 v1Q40 latest-regime rows @13/day',H_r,13.0,330,blk_latest)
tabs+=book_table('H5 v1Q40 all-Sept rows @13/day',H_r,13.0,330)
tabs+=book_table('H5 v1Q40 all-Sept rows @53.5/day (Sept rate, stale)',H_r,53.5,330)
tdf=pd.DataFrame(tabs); tdf.to_csv(D+'stake_tables.csv',index=False)
# ---------------- stacked sim
def build_pool_floor():
    by={}
    for i in range(n_f): by.setdefault(int(fday[i]),[]).append(i)
    return [np.array(by[k]) for k in sorted(by)]
FD=build_pool_floor()
HD_all=[np.flatnonzero(hday==k) for k in hdays]
def sim(Wsol,sF,sH,var,fail,runs=400,days=32,warm=2,haircut=False,hpool='latest',h_rate=13.0,cap_open=None,cluster=False,seed=7,reserve=0.02):
    rng=np.random.default_rng(seed)
    rF=F_r[(var,fail,sF)]; rH=H_r[(var,fail,sH)]
    hidx=np.flatnonzero(blk_latest) if hpool=='latest' else np.arange(n_h)
    if haircut:   # live edge = 50% of the screen edge of the pool actually sampled
        rF=rF-0.5*rF.mean(); rH=rH-0.5*rH[hidx].mean()
    out=[];skips=[0,0,0,0];caps=[0,0]
    for run in range(runs):
        ev=[]  # (t_entry, book, idx)
        for dd in range(days):
            g=FD[rng.integers(len(FD))]
            for i in g: ev.append((dd*86400+off[i],0,i))
            nh=rng.poisson(h_rate)
            if nh:
                ii=rng.choice(hidx,nh)
                if cluster:
                    w0=rng.uniform(0,86400-4*3600); tt=w0+rng.uniform(0,4*3600,nh)
                else: tt=rng.uniform(0,86400,nh)
                for t,i in zip(tt,ii): ev.append((dd*86400+t,1,i))
        ev.sort()
        free=Wsol; openq=[]  # heap of exits
        import heapq
        nopen=[0,0]; daily=np.zeros(days+3); att=[0,0]; took=[0,0]
        for t,b,i in ev:
            while openq and openq[0][0]<=t:
                te,bb,stk,pn=heapq.heappop(openq); free+=stk+pn; nopen[bb]-=1; daily[int(te//86400)]+=pn
            s=sF if b==0 else sH
            att[b]+=1
            if cap_open is not None and nopen[b]>=cap_open: caps[b]+=1; continue
            if free<s+reserve: skips[b]+=1; continue
            r=rF[i] if b==0 else rH[i]
            hold=hold_f[i] if b==0 else 330+1.9
            free-=s; nopen[b]+=1; took[b]+=1
            heapq.heappush(openq,(t+hold,b,s,r*s))
        while openq:
            te,bb,stk,pn=heapq.heappop(openq); daily[min(int(te//86400),days+2)]+=pn
        out.append(dict(daily=daily[warm:days],att=att,took=took))
    DL=np.array([o['daily'] for o in out])
    tot=DL.sum(1); best=DL.max(1)
    att=np.array([o['att'] for o in out]).sum(0); took=np.array([o['took'] for o in out]).sum(0)
    return dict(W=Wsol,sF=sF,sH=sH,var=var,fail=fail,haircut=haircut,mean_day=DL.mean(),p05=np.percentile(DL,5),p50=np.percentile(DL,50),p95=np.percentile(DL,95),
        worst_day_med=float(np.median(DL.min(1))),worst_day_min=float(DL.min()),ex_best_day=float(((tot-best)/(DL.shape[1]-1)).mean()),
        p_neg30=float((tot<0).mean()),take_F=took[0]/max(att[0],1),take_H=took[1]/max(att[1],1),skipF=skips[0],skipH=skips[1],capF=caps[0],capH=caps[1])
if __name__=='__main__':
    print(tdf[(tdf.variant=='B')&(tdf.fail=='press')].round(4).to_string())
    print(tdf[(tdf.variant=='A')&(tdf.fail=='press')].round(4).to_string())
