"""P6 table: line x leg. Reads out/<run>-<leg>/results.json. Control check vs archived. Prints TABLE.md."""
import json, os, sys
O = "/data/mal/hunt-1008/push-1016/hunt29/p6/out"
A = "/data/mal/hunt-1008/push-1016/hunt28"
LEGS = ["1.9", "1.0", "0.6", "0.3"]
LINES = [  # id, label, run, cell, archived results path
 ("FLOOR-v2", "FLOOR v2 (l1)", "l1", "l1", A + "/l3/out/l1-160000/results.json"),
 ("REVIVE-r2h", "REVIVE r2h", "l4", "r2h", A + "/l4/out/priced/results.json"),
 ("REVIVE-r6h", "REVIVE r6h", "l4", "r6h", A + "/l4/out/priced/results.json"),
 ("REVIVE-r12h", "REVIVE r12h", "l4", "r12h", A + "/l4/out/priced/results.json"),
 ("w2q-R4", "w2-quiet R4", "w2quiet", "R4", A + "/l3/out/w2quiet-160000/results.json"),
 ("w2q-R9", "w2-quiet R9", "w2quiet", "R9", A + "/l3/out/w2quiet-160000/results.json"),
 ("w2bs-DS-C1-g50", "w2b-strand DS-C1-g50", "w2bstrand", "DS-C1-g50", A + "/l3/out/w2bstrand-160000/results.json"),
 ("BREADTH-b5", "BREADTH b5", "l4", "b5", A + "/l4/out/priced/results.json"),
 ("SELLABS-s1", "SELLABS s1", "l4", "s1", A + "/l4/out/priced/results.json"),
]
def get(path, cell, leg):
    c = json.load(open(path))["cells"][cell]
    f = c["stats"][f"{leg}|total|rent0|flat"]; p = c["stats"][f"{leg}|total|rent0|press"]
    return dict(apd=c["attempts_per_day"], n=f["n"], fm=f["mean"], pm=p["mean"], fx3=f["ex_top3"], px3=p["ex_top3"],
                fd=f"{f['pos_days']}/{f['days']}", pd=f"{p['pos_days']}/{p['days']}", fci=f["ci_lo_trade"], pci=p["ci_lo_trade"],
                imp=c["impact"][leg]["0.25"], ftot=f["total"], ptot=p["total"], days=c["days_in_run"])
rows, out, ctl = {}, [], []
for lid, lab, run, cell, arch in LINES:
    for leg in LEGS:
        pth = f"{O}/{run}-{leg}/results.json"
        if os.path.exists(pth): rows[(lid, leg)] = get(pth, cell, leg)
    if (lid, "1.9") in rows:
        a = get(arch, cell, "1.9"); r = rows[(lid, "1.9")]
        ok = all(abs(a[k] - r[k]) <= 1e-12 + 1e-9 * abs(a[k]) for k in ("fm", "pm", "fx3", "px3", "ftot", "ptot")) and a["n"] == r["n"]
        cid = max(abs(a[k] - r[k]) / abs(a[k]) for k in ("fci", "pci"))  # bootstrap CI lower bound: informational
        ctl.append(f"| {lab} | {a['n']} / {r['n']} | {a['fm']:+.6f} / {r['fm']:+.6f} | {a['px3']:+.3f} / {r['px3']:+.3f} | {'REPRODUCED' if ok else 'MISMATCH'} | {cid*100:.2f}% |")
def cross(lid, key):
    # slowest-first scan: returns text about sign over the grid (1.9 -> 0.3)
    s = [(l, rows[(lid, l)][key]) for l in LEGS if (lid, l) in rows]
    return s
L = ["| line | leg s | trades/day | flat SOL/trade | press SOL/trade | ex-top-3 flat / press (SOL) | days+ flat / press | CI90 lo flat / press (SOL/trade) | impact 0.25 (%) |", "|---|---|---|---|---|---|---|---|---|"]
for lid, lab, *_ in LINES:
    for leg in LEGS:
        r = rows.get((lid, leg))
        if not r: continue
        L.append(f"| {lab} | {leg} | {r['apd']:.1f} | {r['fm']:+.5f} | {r['pm']:+.5f} | {r['fx3']:+.3f} / {r['px3']:+.3f} | {r['fd']} / {r['pd']} | {r['fci']:+.5f} / {r['pci']:+.5f} | {r['imp']*100:.2f} |")
Z = ["| line | flat mean sign at 1.9/1.0/0.6/0.3 | press mean sign | both-model positive legs | zero-crossing (both models) |", "|---|---|---|---|---|"]
for lid, lab, *_ in LINES:
    fs = [(l, rows[(lid, l)]["fm"]) for l in LEGS if (lid, l) in rows]; ps = [(l, rows[(lid, l)]["pm"]) for l in LEGS if (lid, l) in rows]
    sg = lambda v: "+" if v > 0 else "-"
    both = [l for (l, a), (_, b) in zip(fs, ps) if a > 0 and b > 0]
    if len(fs) < 4: note = "incomplete"
    elif len(both) == 4: note = "positive at every leg (no crossing in grid)"
    elif not both: note = "no crossing in grid (mean <= 0 under at least one model at every leg)"
    else:
        slow = max(float(l) for l in both); fast_neg = [float(l) for l, _ in fs if l not in both and float(l) > slow]
        note = f"both positive at {', '.join(both)}; slowest positive leg {slow} s" + ("" if not fast_neg else f"; nonpositive at {fast_neg}")
    Z.append(f"| {lab} | {' '.join(sg(v) for _, v in fs)} | {' '.join(sg(v) for _, v in ps)} | {', '.join(both) or '-'} | {note} |")
open("/data/mal/hunt-1008/push-1016/hunt29/p6/TABLE.md", "w").write("## Control check (archived 1.9 s vs P6 1.9 s)\n| line | n arch / P6 | flat mean arch / P6 | press ex-top-3 arch / P6 | verdict (n, means, ex-top-3, totals, rel tol 1e-9) | max rel diff of CI90 lo (informational) |\n|---|---|---|---|---|---|\n" + "\n".join(ctl) + "\n\n## Line x leg\n" + "\n".join(L) + "\n\n## Signs and crossings\n" + "\n".join(Z) + "\n")
json.dump({f"{k[0]}|{k[1]}": v for k, v in rows.items()}, open("/data/mal/hunt-1008/push-1016/hunt29/p6/rows.json", "w"), indent=1)
print(open("/data/mal/hunt-1008/push-1016/hunt29/p6/TABLE.md").read())
