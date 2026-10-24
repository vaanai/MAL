"""P13 launchpad census: 10-minute public logsSubscribe sample, no key, no paid credits.
One websocket to the public RPC, one mentions subscription per program. Counts notices,
failures, 'Instruction: X' names and a crude same-slot-as-create proxy. Writes JSON."""
import asyncio, json, sys, time, collections, re
import websockets
PROG = {"launchlab":"LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj","dbc":"dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN",
 "heaven":"HEAVENoP2qxoeuF8Dj2oT1GHEnu49U5mJYkdeC8BAX2o","boop":"boop8hVGQGqehUK2iVEMEnMrL5RbjywRzHKBmBE7ry4",
 "moonit":"MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG","whirlpool":"whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
 "cpmm":"CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C","damm2":"cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG"}
DUR = int(sys.argv[1]) if len(sys.argv)>1 else 600
OUT = sys.argv[2] if len(sys.argv)>2 else "census_sample.json"
URL = sys.argv[3] if len(sys.argv)>3 else "wss://api.mainnet-beta.solana.com"
CREATE = re.compile(r"initialize|create|newpool|init_pool", re.I)
SWAP = re.compile(r"swap|buy|sell|trade", re.I)
async def main():
    st = {k:{"n":0,"fail":0,"ix":collections.Counter(),"slots_create":[],"slots_swap":[],"ex_logs":[]} for k in PROG}
    sub2name = {}
    t0=time.time()
    async with websockets.connect(URL, max_size=2**24, ping_interval=20) as ws:
        for i,(k,p) in enumerate(PROG.items()):
            await ws.send(json.dumps({"jsonrpc":"2.0","id":i+1,"method":"logsSubscribe","params":[{"mentions":[p]},{"commitment":"confirmed"}]}))
        pend = {i+1:k for i,k in enumerate(PROG)}
        while time.time()-t0 < DUR:
            try: m = json.loads(await asyncio.wait_for(ws.recv(), 30))
            except asyncio.TimeoutError: continue
            if "id" in m and "result" in m:
                sub2name[m["result"]] = pend[m["id"]]; continue
            if m.get("method")!="logsNotification": continue
            sub=m["params"]["subscription"]; k=sub2name.get(sub)
            if not k: continue
            r=m["params"]["result"]; slot=r["context"]["slot"]; v=r["value"]
            s=st[k]; s["n"]+=1
            if v.get("err"): s["fail"]+=1
            names=[re.sub(r"^Program log: Instruction: ","",l) for l in v["logs"] if l.startswith("Program log: Instruction: ")]
            for nm in names: s["ix"][nm]+=1
            isc = any(CREATE.search(n) for n in names) and not v.get("err")
            iss = any(SWAP.search(n) for n in names)
            if isc: s["slots_create"].append(slot)
            if iss: s["slots_swap"].append(slot)
            if len(s["ex_logs"])<2 and (isc or (names and s["n"]==1)): s["ex_logs"].append(v["logs"][:12])
    el=time.time()-t0; out={"elapsed_s":el,"url":URL,"venues":{}}
    for k,s in st.items():
        cs=set(s["slots_create"]); near=sum(1 for x in s["slots_swap"] if any((x-c) in (0,1,2) for c in cs)) if cs else 0
        out["venues"][k]={"program":PROG[k],"notices":s["n"],"failed":s["fail"],"creates":len(s["slots_create"]),"swaps":len(s["slots_swap"]),
          "swaps_within_2_slots_of_a_create":near,"top_ix":s["ix"].most_common(12),"ex_logs":s["ex_logs"]}
    json.dump(out,open(OUT,"w"),indent=1)
asyncio.run(main())
