import sys, os, json, collections, time
sys.path.insert(0, os.path.dirname(__file__))
from rpc import call
OUT = os.environ["P49_OUT"]
PID = "MEME4gfGe6itEpxgM1xaBVmiFF5XuCTc4oVXUE5w56F"
STOP_T = int(sys.argv[1]); MAXP = int(sys.argv[2])
before = None; allsig = []; pages = 0
while pages < MAXP:
    p = {"limit": 1000}
    if before: p["before"] = before
    r = call("getSignaturesForAddress", [PID, p]); pages += 1
    if not r: break
    allsig += r; before = r[-1]["signature"]
    if (r[-1].get("blockTime") or 0) < STOP_T: break
json.dump(allsig, open(os.path.join(OUT, "program_sigs.json"), "w"))
errs = collections.Counter()
for s in allsig:
    e = s.get("err")
    if e is None: continue
    k = json.dumps(e)
    errs[k] += 1
inw = [s for s in allsig if (s.get("blockTime") or 0) >= STOP_T]
print("pages", pages, "sigs", len(allsig), "in window", len(inw), "oldest", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(allsig[-1]["blockTime"])), "newest", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(allsig[0]["blockTime"])))
print("failed in window", sum(1 for s in inw if s.get("err")))
for k, v in errs.most_common(20): print(v, k)
