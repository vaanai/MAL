import json, sys, os, datetime
sys.path.insert(0, ".")
from rpc import call
# usage: pda_hist.py MINT PDA pages
mint, pda, pages = sys.argv[1], sys.argv[2], int(sys.argv[3])
os.makedirs("pda", exist_ok=True)
allr, before = [], None
for p in range(pages):
    opt = {"limit": 1000}
    if before: opt["before"] = before
    r = call("getSignaturesForAddress", [pda, opt])
    if not r: break
    allr += r; before = r[-1]["signature"]
    if len(r) < 1000: break
json.dump(allr, open(f"pda/{mint}.json", "w"))
ok = sorted([x for x in allr if not x["err"] and x["blockTime"]], key=lambda x: x["blockTime"])
f = lambda t: datetime.datetime.fromtimestamp(t, datetime.UTC).strftime("%m-%dT%H:%M:%S")
rounds, cur = [], []
for x in ok:
    if cur and x["blockTime"] - cur[-1]["blockTime"] > 120: rounds.append(cur); cur = []
    cur.append(x)
if cur: rounds.append(cur)
gaps = [rounds[i+1][0]["blockTime"] - rounds[i][0]["blockTime"] for i in range(len(rounds)-1)]
gaps.sort()
print(mint[:8], "sigs", len(allr), "ok", len(ok), "span", f(ok[0]["blockTime"]), "->", f(ok[-1]["blockTime"]), "full" if len(allr) < 1000*pages else "TRUNC",
      "rounds", len(rounds), "median gap s", gaps[len(gaps)//2] if gaps else None, "tx/round med", sorted(len(r) for r in rounds)[len(rounds)//2])
