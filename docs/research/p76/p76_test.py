"""P76: hour-of-day PUMP/USDC clock trade on held-out explore-0814 days. See PREREG.md."""
import duckdb, random, sys
SRC='/data/mal/hunt-1008/push-1016/hunt5/pump-buyback/pump_trades.parquet'
POOL='2uF4Xh61rDwxnG9woyxsVQP7zuA6kLFpb3NvnRQeoiSd'
T0=1786708800  # 2026-08-14T12:00Z
T1=1788004800  # 2026-08-28T12:00Z (exclusive), explore-0814 only
FEE_P=0.0035; FEE_S=0.0030; LAG=2; MAXWAIT=600; USD_PER_SOL=150.0; FAIL=0.15; FAILC=-55000/1e9
c=duckdb.connect()
rows=c.sql(f"select block_time,slot,quote_reserve,base_reserve from '{SRC}' where pool='{POOL}' and block_time>={T0-7200} and block_time<{T1+7200} order by slot,tx_index,event_index").fetchall()
bt=[r[0] for r in rows]; sl=[r[1] for r in rows]
import bisect
def first_time(t):
    i=bisect.bisect_left(bt,t); return i if i<len(rows) else None
def fill(t):
    i=first_time(t)
    if i is None or bt[i]-t>MAXWAIT: return None
    j=bisect.bisect_left(sl,sl[i]+LAG)
    if j>=len(rows) or bt[j]-t>MAXWAIT: return None
    return rows[j]
def trade(tin,tout,stake_usdc):
    a=fill(tin); b=fill(tout)
    if not a or not b: return None
    x=stake_usdc*1e6*(1-FEE_S)   # raw USDC after SOL->USDC leg
    d=x*(1-FEE_P); Q,B=a[2],a[3]
    tok=B*d/(Q+d); impact_buy=(d/(Q+d))  # price impact fraction
    Q2,B2=b[2],b[3]
    out=Q2*tok/(B2+tok)*(1-FEE_P)*(1-FEE_S)
    mid_in=Q/B; ret=out/(stake_usdc*1e6)-1
    gross=(Q2/B2)/(Q/B)-1
    return ret,gross,impact_buy
def stats(rets,days,stake):
    pnl=[r*stake for r in rets]   # SOL, USDC numeraire
    flat=[0.85*p+0.15*FAILC for p in pnl]
    return pnl,flat
def boot(v):
    rnd=random.Random(1); n=len(v); m=sorted(sum(v[rnd.randrange(n)] for _ in range(n))/n for _ in range(1000)); return m[49]
def run(H,stake_sol,detail=False):
    res=[]
    for d in range(14):
        base=T0-12*3600+d*86400   # 00:00 of day 08-14+d
        tin=base+H*3600; tout=tin+3600
        if tin<T0 or tout>T1: continue
        r=trade(tin,tout,stake_sol*USD_PER_SOL)
        if r: res.append((d,)+r)
    return res
out=[]
P=lambda *a: print(*a)
P("PREREG held-out block explore-0814, days 08-14..08-27, pool",POOL)
for stake in (0.25,1,5):
    r=run(16,stake)
    if not r: P("no trades"); continue
    P(f"stake {stake} SOL: n={len(r)} mean gross price move bps {sum(x[2] for x in r)/len(r)*1e4:.2f}, mean buy impact bps {sum(x[3] for x in r)/len(r)*1e4:.4f}")
    rets=[x[1] for x in r]; pnl,flat=stats(rets,None,stake)
    for name,v in (("flat15",flat),("pressure(p=0.15, same)",flat)):
        n=len(v); mean=sum(v)/n
        s=sorted(v,reverse=True); ex3=sum(v)-sum(s[:3])
        bestd=max(v); exbd=sum(v)-bestd   # one trade per day
        pos=sum(1 for x in v if x>0)
        P(f"  {name}: mean/trade {mean:.6f} SOL ({mean/stake*100:.4f}% of stake) total {sum(v):.5f} ex-top3 {ex3:.5f} ex-best-day {exbd:.5f} days_pos {pos}/{n} CI90low {boot(v):.6f}")
    P("  per-day net ret % (flat-free):",[round(x*100,3) for x in rets])
    P(f"  capacity: 1 trade/day x {stake} x mean net {sum(rets)/len(rets)*100:.4f}% = {stake*sum(rets)/len(rets):.6f} SOL/day (gross of fail model)")
P("PLACEBO 24 hours at 1 SOL, flat model mean net per trade (SOL):")
pl=[]
for H in range(24):
    r=run(H,1)
    if not r: pl.append((H,None,0)); continue
    flat=stats([x[1] for x in r],None,1)[1]
    pl.append((H,sum(flat)/len(flat),len(r),sum(x[2] for x in r)/len(r)*1e4))
for x in pl: P(x)
ok=[x for x in pl if x[1] is not None]
rank=sorted(ok,key=lambda x:-x[1])
P("rank of hour 16:",[x[0] for x in rank].index(16)+1,"of",len(ok))
