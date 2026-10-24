import sys, json; sys.path.insert(0,'.')
import stack as S
USD=150.0
G=S.GRID
res={'usd_per_sol_assumed':USD,'rows':[]}
def add(tag,**kw):
    r=S.sim(**kw); r['tag']=tag; r['usd_day']=r['mean_day']*USD; res['rows'].append(r); return r
# stake search per wallet (variant B, pressure, screen edge), selection in-sample
best={}
for W in (1,2,5):
    cand=[]
    for sF in [g for g in G if g<=W/2]:
        for sH in [g for g in G if g<=W]:
            r=S.sim(W,sF,sH,'B','press',runs=120,seed=3); cand.append((r['mean_day'],sF,sH))
    cand.sort(reverse=True); best[W]=cand[0][1:]; print(W,cand[:4],flush=True)
for W in (1,2,5):
    sF,sH=best[W]
    for var in ('B','A'):
        for fail in ('flat','press'):
            for hc in (False,True):
                add(f'free W={W} best-stake {var}',Wsol=W,sF=sF,sH=sH,var=var,fail=fail,haircut=hc,runs=1500)
    add(f'free W={W} B press screen, H5 clustered 4h',Wsol=W,sF=sF,sH=sH,var='B',fail='press',cluster=True,runs=1500)
    add(f'free W={W} B press screen, H5 all-Sept pool',Wsol=W,sF=sF,sH=sH,var='B',fail='press',hpool='all',runs=1500)
    add(f'free W={W} B press screen, H5 rate 16',Wsol=W,sF=sF,sH=sH,var='B',fail='press',h_rate=16.0,runs=1500)
# DEC-032 tiers, 1 SOL wallet
for tag,s,cap in (('C0 0.05 open<=2',0.05,2),('C1 0.10 open<=3',0.10,3)):
    for fail in ('flat','press'):
        for hc in (False,True):
            add(f'DEC {tag} B',Wsol=1,sF=s,sH=s,var='B',fail=fail,haircut=hc,cap_open=cap,runs=1500)
# single-book solo at W=5 for decomposition
res['best_stakes']={str(k):v for k,v in best.items()}
json.dump(res,open('stack_results.json','w'),default=float,indent=1)
import pandas as pd
df=pd.DataFrame(res['rows'])
cols=['tag','fail','haircut','sF','sH','mean_day','usd_day','p05','p50','p95','worst_day_med','worst_day_min','ex_best_day','p_neg30','take_F','take_H','capF','capH']
df[cols].round(4).to_csv('stack_results.csv',index=False)
print(df[cols].round(3).to_string())
