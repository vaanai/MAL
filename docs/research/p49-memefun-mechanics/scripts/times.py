import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from rpc import call
OUT = os.environ["P49_OUT"]
c = json.load(open(os.path.join(OUT, "curves.json")))
slots = sorted(set(r["created_slot"] for r in c["rows"]))
# anchors: min, max, and 10 quantiles
idx = sorted(set([0, len(slots) - 1] + [int(len(slots) * q / 12) for q in range(1, 12)]))
anchors = {}
for i in idx:
    s = slots[i]
    try:
        anchors[s] = call("getBlockTime", [s])
    except Exception as e:
        print("fail", s, e)
json.dump(anchors, open(os.path.join(OUT, "anchors.json"), "w"), indent=1)
import time
for s, t in sorted(anchors.items()): print(s, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)))
