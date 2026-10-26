import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from rpc import call
OUT = os.environ["P49_OUT"]
sigs = json.load(open(os.path.join(OUT, "cd_curve_sigs.json")))
f = [s for s in sigs if "6001" in json.dumps(s.get("err"))][int(sys.argv[1])]
t = call("getTransaction", [f["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 1}])
m = t["meta"]; msg = t["transaction"]["message"]
keys = msg["accountKeys"] + (m.get("loadedAddresses") or {}).get("writable", []) + (m.get("loadedAddresses") or {}).get("readonly", [])
for i, ix in enumerate(msg["instructions"]): print(i, keys[ix["programIdIndex"]], len(ix["accounts"]), ix["data"][:16])
print("\n".join(m["logMessages"] or ["<no logs>"]))
