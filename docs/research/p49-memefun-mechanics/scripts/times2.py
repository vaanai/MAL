import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from rpc import call
OUT = os.environ["P49_OUT"]
c = json.load(open(os.path.join(OUT, "curves.json")))
a = {int(k): v for k, v in json.load(open(os.path.join(OUT, "anchors.json"))).items()}
for s in sorted(set(r["created_slot"] for r in c["rows"])):
    if s > 460591131 and s not in a:
        a[s] = call("getBlockTime", [s])
json.dump({str(k): v for k, v in sorted(a.items())}, open(os.path.join(OUT, "anchors.json"), "w"), indent=1)
print(len(a))
