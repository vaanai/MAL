import json, sys, base64, struct, time
sys.path.insert(0, ".")
from rpc import call, b58e
mint = sys.argv[1]
a = call("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
prog = a["value"]["owner"]; info = a["value"]["data"]["parsed"]["info"]
r = call("getProgramAccounts", [prog, {"encoding": "base64", "dataSlice": {"offset": 32, "length": 40}, "filters": [{"memcmp": {"offset": 0, "bytes": mint}}]}])
H = []
for x in r:
    b = base64.b64decode(x["account"]["data"][0])
    H.append({"acct": x["pubkey"], "owner": b58e(b[:32]), "amt": struct.unpack_from("<Q", b, 32)[0]})
json.dump({"mint": mint, "taken": int(time.time()), "supply": info["supply"], "decimals": info["decimals"], "holders": H}, open(f"holders_{mint[:8]}.json", "w"))
nz = [h for h in H if h["amt"] > 0]
print(mint[:8], "accounts", len(H), "nonzero", len(nz), "supply", info["supply"])
