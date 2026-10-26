import sys, os, json, time, collections
sys.path.insert(0, os.path.dirname(__file__))
from rpc import call
OUT = os.environ["P49_OUT"]
res = {}
for name, addr in [("treasury", "7BRjUvfDZ2Vyhzv1ajsj12nnFFkbqJcz4GXtEk51hT6F"), ("admin", "55cVpLhERp4aJZqZ1hAV6rSApu661ErwtBTCpbb8KACb")]:
    s = call("getSignaturesForAddress", [addr, {"limit": 1000}])
    res[name] = s
    print(name, len(s), "newest", time.strftime("%m-%dT%H:%M:%SZ", time.gmtime(s[0]["blockTime"])) if s else None, "oldest", time.strftime("%m-%dT%H:%M:%SZ", time.gmtime(s[-1]["blockTime"])) if s else None,
          "since 10-26 00Z:", sum(1 for x in s if (x.get("blockTime") or 0) >= 1792972800))
json.dump(res, open(os.path.join(OUT, "treas_sigs.json"), "w"))
# sample newest 4 txs of each, list programs + memefun ix names
for name in res:
    for x in res[name][:4]:
        t = call("getTransaction", [x["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 1}])
        logs = t["meta"]["logMessages"] or []
        ixn = [l.split("Instruction: ")[1] for l in logs if "Instruction: " in l]
        progs = sorted(set(l.split()[1] for l in logs if l.endswith("invoke [1]")))
        print(name, time.strftime("%m-%dT%H:%M:%SZ", time.gmtime(x["blockTime"])), progs, ixn[:8])
