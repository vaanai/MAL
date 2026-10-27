import json, sys
sys.path.insert(0, ".")
from rpc import call
addr, pages, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
allr, before = [], None
for p in range(pages):
    opt = {"limit": 1000}
    if before: opt["before"] = before
    r = call("getSignaturesForAddress", [addr, opt])
    if not r: break
    allr += r; before = r[-1]["signature"]
    if len(r) < 1000: break
json.dump(allr, open(out, "w"))
import datetime
f = lambda t: datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None
print(len(allr), "sigs; newest", f(allr[0]["blockTime"]), "oldest", f(allr[-1]["blockTime"]), "errs", sum(1 for x in allr if x["err"]))
