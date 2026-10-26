"""Control: base run vs L3 REPRICE l1-160000. Usage: python3 -I control.py <base results.json>"""
import json, sys
a = json.load(open(sys.argv[1]))["cells"]["l1"]
b = json.load(open("/data/mal/hunt-1008/push-1016/hunt28/l3/out/l1-160000/results.json"))["cells"]["l1"]
ok = True
for k in ("1.9|total|rent0|flat", "1.9|total|rent0|press"):
    x, y = a["stats"][k], b["stats"][k]
    same = x["n"] == y["n"] and x["mean_pct"] == y["mean_pct"] and x["total"] == y["total"]
    ok &= same
    print(k, "p59 n", x["n"], "mean_pct", x["mean_pct"], "| L3 n", y["n"], "mean_pct", y["mean_pct"], "| identical", same)
print("CONTROL", "PASS" if ok else "FAIL")
