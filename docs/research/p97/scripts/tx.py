import json, sys, os
sys.path.insert(0, ".")
from rpc import call
os.makedirs("tx", exist_ok=True)
def get(sig):
    p = f"tx/{sig}.json"
    if os.path.exists(p): return json.load(open(p))
    r = call("getTransaction", [sig, {"encoding":"json","maxSupportedTransactionVersion":1}])
    json.dump(r, open(p, "w")); return r
if __name__ == "__main__":
    for s in sys.argv[1:]:
        r = get(s); m = r["transaction"]["message"]; keys = m["accountKeys"] + (r["meta"].get("loadedAddresses",{}).get("writable",[]) + r["meta"].get("loadedAddresses",{}).get("readonly",[]))
        print(s[:12], "err", r["meta"]["err"], "signers", keys[:m["header"]["numRequiredSignatures"]], "nkeys", len(keys))
        for l in r["meta"]["logMessages"][:12]: print("   ", l[:150])
