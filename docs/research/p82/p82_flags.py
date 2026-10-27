"""P82 step 1b: read Pool.is_cashback_coin (byte 244) via PUBLIC RPC getMultipleAccounts. No Helius.
Usage: python p82_flags.py pools.json OUT.json   (<=3000 calls, <=5 rps; here ~29 calls at 1 rps)"""
import base64, json, sys, time, urllib.request
RPC = "https://api.mainnet-beta.solana.com"
pools = json.load(open(sys.argv[1]))
addrs = sorted({v["pool"] for v in pools.values()})
res = {}
calls = 0
for i in range(0, len(addrs), 100):
    chunk = addrs[i:i + 100]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts",
                       "params": [chunk, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 246}}]}).encode()
    for att in range(5):
        calls += 1
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(RPC, body, {"content-type": "application/json"}), timeout=30))
            if "result" in r:
                break
        except Exception as e:
            r = {"err": str(e)}
        time.sleep(2 + 3 * att)
    for a, v in zip(chunk, r["result"]["value"]):
        if v is None:
            res[a] = {"state": "missing"}
        else:
            d = base64.b64decode(v["data"][0])
            res[a] = {"state": "ok", "len": len(d), "mayhem": d[243] if len(d) > 243 else None,
                      "cashback": d[244] if len(d) > 244 else None, "owner": v.get("owner")}
    time.sleep(1.0)
print("calls", calls, "pools", len(addrs))
json.dump(res, open(sys.argv[2], "w"))
