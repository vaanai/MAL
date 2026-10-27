import json, sys, collections, statistics
AUTH = "HckQ93Xqjjo8mwt5pNPWvyCTZXQZ858rvzmm7ZRrZg9t"
mint, rfile = sys.argv[1], sys.argv[2]
Hd = json.load(open(f"holders_{mint[:8]}.json")); R = json.load(open(rfile)); dec = Hd["decimals"]; sup = int(Hd["supply"])
own = collections.Counter()
for h in Hd["holders"]: own[h["owner"]] += h["amt"]
rec = collections.Counter()
for d in R["txs"]:
    for i in d["ixs"]:
        if i["name"] == "distribute_fee_to_holders":
            for o, a in zip(i["owners"], i["amounts"]): rec[o] += a
# per-token rate k from recipients whose balance did not change much: use the mode of a/b
ks = sorted(rec[o] / own[o] for o in rec if own.get(o, 0) > 0 and o != AUTH)
k = statistics.median(ks)
impl = sorted(rec[o] / k / 10**dec for o in rec if o != AUTH)
print(mint[:8], "k lamports/raw token", k, "share of recipients within 1% of k", sum(1 for x in ks if abs(x / k - 1) < 0.01) / len(ks))
print("implied balance at round (ui): min", round(impl[0]), "p1", round(impl[len(impl)//100]), "median", round(statistics.median(impl)), "=> min as share of supply", impl[0] * 10**dec / sup)
print("implied eligible held supply share", sum(rec[o] for o in rec if o != AUTH) / k / sup)
nr = sorted(own[o] / 10**dec for o in own if own[o] > 0 and o not in rec)
print("non-recipients now above implied min:", sum(1 for x in nr if x >= impl[0]), "of", len(nr), "; below:", sum(1 for x in nr if x < impl[0]))
print("self amount", rec.get(AUTH, 0), "n recipients", len(rec), "smallest payouts (lamports or quote units)", sorted(rec.values())[:3])
