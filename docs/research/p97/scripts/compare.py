import json, sys, collections, statistics
sys.path.insert(0, ".")
from rpc import call
AUTH = "HckQ93Xqjjo8mwt5pNPWvyCTZXQZ858rvzmm7ZRrZg9t"
mint, rfile = sys.argv[1], sys.argv[2]
Hd = json.load(open(f"holders_{mint[:8]}.json")); R = json.load(open(rfile))
sup = int(Hd["supply"]); dec = Hd["decimals"]
own = collections.Counter()
for h in Hd["holders"]: own[h["owner"]] += h["amt"]
rec = collections.Counter()
for d in R["txs"]:
    for i in d["ixs"]:
        if i["name"] == "distribute_fee_to_holders":
            for o, a in zip(i["owners"], i["amounts"]): rec[o] += a
tot = sum(rec.values())
top = [o for o, _ in own.most_common(12)]
infos = call("getMultipleAccounts", [top, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}])["value"]
ptype = {o: (v["owner"] if v else "none") for o, v in zip(top, infos)}
print("supply(ui)", sup / 10**dec, "holders>0", sum(1 for v in own.values() if v > 0), "recipients", len(rec), "total paid", tot)
print("top holders: share of supply | owner program | paid share | paid/held ratio")
held_rec = sum(own[o] for o in rec)
for o in top:
    s = own[o] / sup; p = rec.get(o, 0) / tot if tot else 0
    print(f"  {o[:8]} {s:7.4%} {ptype[o][:8]:9s} paid {p:7.4%} ratio {p/s if s else 0:6.3f} {'RECIPIENT' if o in rec else '-'}")
nonrec = [(own[o], o) for o in own if own[o] > 0 and o not in rec]
print("non-recipients with balance:", len(nonrec), "their supply share", sum(a for a, _ in nonrec) / sup)
print("  smallest non-recipient balances (ui):", sorted(a / 10**dec for a, _ in nonrec)[:5], "largest:", sorted(a / 10**dec for a, _ in nonrec)[-5:])
rr = sorted(own[o] / 10**dec for o in rec if o != AUTH)
print("recipient balances (ui) min", rr[0] if rr else None, "median", statistics.median(rr) if rr else None, "; recipients with 0 balance now", sum(1 for o in rec if own.get(o, 0) == 0))
# proportionality among recipients with balance now
rat = sorted((rec[o] / tot) / (own[o] / held_rec) for o in rec if own.get(o, 0) > 0 and o != AUTH)
q = lambda p: rat[int(p * (len(rat) - 1))]
print("paid share / held share among recipients (1.0 = pro rata over recipients): p10 %.3f p50 %.3f p90 %.3f" % (q(.1), q(.5), q(.9)))
print("recipients' held share of supply", held_rec / sup, "self share of paid", rec.get(AUTH, 0) / tot)
json.dump({"ptype": ptype}, open(f"ptype_{mint[:8]}.json", "w"))
