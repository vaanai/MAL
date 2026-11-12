"""P48 public-RPC helper (api.mainnet-beta.solana.com, 0 Helius): getTransaction / getSignatureStatuses.
Every call attempt is logged one line per attempt, for the call budget."""
import json,os,sys,urllib.request,time
URL="https://api.mainnet-beta.solana.com"
LOG=open(os.environ.get("P48_RPC_LOG","p48_rpc_calls.log"),'a')
def call(method,params):
    body=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    for i in range(4):
        try:
            req=urllib.request.Request(URL,body,{"Content-Type":"application/json"})
            r=json.load(urllib.request.urlopen(req,timeout=90)); LOG.write(method+"\n"); LOG.flush(); return r
        except Exception as e:
            LOG.write(method+" ERR\n"); time.sleep(2+3*i)
    return None
def tx(sig):
    return call("getTransaction",[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":1,"commitment":"confirmed"}])
if __name__=="__main__":
    for s in sys.argv[1:]:
        r=tx(s); res=(r or {}).get('result')
        if not res: print(s[:10],'NOT FOUND',r and r.get('error')); continue
        m=res['meta']; print(s[:10],'slot',res['slot'],'err',m.get('err'),'fee',m.get('fee'))
        time.sleep(0.5)
