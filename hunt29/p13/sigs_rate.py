"""getSignaturesForAddress rate probe on public RPC: newest <=1000 sigs per program; rate = n/span; also fails share."""
import json,urllib.request,time,sys
import os,sys; sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from census_ids import PROG
def rpc(p):
    r=urllib.request.Request("https://api.mainnet-beta.solana.com",json.dumps({"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":p}).encode(),{"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(r,timeout=30))
out={}
for k,a in PROG.items():
    try:
        rs=rpc([a,{"limit":1000}])["result"]; time.sleep(1.5)
    except Exception as e: out[k]={"err":repr(e)[:80]}; continue
    if not rs: out[k]={"n":0}; continue
    now=time.time(); bt=[x.get("blockTime") for x in rs if x.get("blockTime")]
    span=max(bt)-min(bt) if len(bt)>1 else None
    out[k]={"n":len(rs),"newest_age_s":round(now-max(bt)),"oldest_age_s":round(now-min(bt)),"span_s":span,"per_day":round(len(rs)/span*86400) if span else None,"fail_share":round(sum(1 for x in rs if x.get("err"))/len(rs),2)}
print(json.dumps(out,indent=1)); json.dump(out,open("sigs_rate.json","w"),indent=1)
