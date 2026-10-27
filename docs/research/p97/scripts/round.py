import json, sys, collections, datetime
sys.path.insert(0, ".")
from tx import get
from decode import decode
AUTH = "HckQ93Xqjjo8mwt5pNPWvyCTZXQZ858rvzmm7ZRrZg9t"
mint, which = sys.argv[1], int(sys.argv[2])   # which: 1 = latest round, 2 = the one before
s = json.load(open(f"pda/{mint}.json"))
ok = sorted([x for x in s if not x["err"] and x["blockTime"]], key=lambda x: (x["blockTime"], x["slot"]))
rounds, cur = [], []
for x in ok:
    if cur and x["blockTime"] - cur[-1]["blockTime"] > 120: rounds.append(cur); cur = []
    cur.append(x)
rounds.append(cur)
R = rounds[-which]
out = {"mint": mint, "start": R[0]["blockTime"], "end": R[-1]["blockTime"], "txs": []}
for x in R:
    d = decode(get(x["signature"])); d["sig"] = x["signature"]; out["txs"].append(d)
json.dump(out, open(f"round_{mint[:8]}_{which}.json", "w"))
names = collections.Counter(i["name"] for d in out["txs"] for i in d["ixs"])
signers = collections.Counter(d["signer"][:8] for d in out["txs"])
dist = [i for d in out["txs"] for i in d["ixs"] if i["name"] == "distribute_fee_to_holders"]
rec = collections.Counter(); 
for i in dist:
    for o, a in zip(i["owners"], i["amounts"]): rec[o] += a
f = lambda t: datetime.datetime.fromtimestamp(t, datetime.UTC).strftime("%m-%dT%H:%M:%S")
print(mint[:8], "round", f(R[0]["blockTime"]), "->", f(R[-1]["blockTime"]), "txs", len(R), names, signers)
print("  recipients", len(rec), "dup owners", sum(1 for i in dist for o in i["owners"]) - len(rec), "total", sum(rec.values()), "self", rec.get(AUTH, 0), "quote", dist[0]["quote_mint"][:6] if dist else None)
