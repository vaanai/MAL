"""P59 summary: reads <out>/{base,a40,guardoff}/results.json, applies the PREREG kill lines. Usage: python3 -I summarize.py <out_dir>"""
import json, sys
out = sys.argv[1]
R = {v: json.load(open(f"{out}/{v}/results.json"))["cells"]["l1"] for v in ("base", "a40", "guardoff")}
D, C = "explore-0814", "september"
def g(v, grp, m, k): return R[v]["groups"][f"{grp}|total|{m}"][k]
def cal(v):  # calendar days: discovery 14, confirmation = days_in_run - 14
    return {D: 14, C: R[v]["days_in_run"] - 14}
lines = []
P = lines.append
P("| variant | grp | model | n | n/cal-day | mean % | delta vs base pp | total SOL | ex-top3 | ex-best-day | CI90 lo trade % | pos/days |")
P("|---|---|---|---|---|---|---|---|---|---|---|---|")
for v in R:
    for grp in (D, C):
        for m in ("flat", "press"):
            n = g(v, grp, m, "n")
            P(f"| {v} | {grp} | {m} | {n} | {n/cal(v)[grp]:.2f} | {g(v,grp,m,'mean_pct'):.4f} | {g(v,grp,m,'mean_pct')-g('base',grp,m,'mean_pct'):+.4f} | {g(v,grp,m,'total'):.4f} | {g(v,grp,m,'ex_top3'):.4f} | {g(v,grp,m,'ex_best_day'):.4f} | {g(v,grp,m,'ci_lo_trade_pct'):.3f} | {g(v,grp,m,'pos_days')}/{g(v,grp,m,'days')} |")
P("")
P("Pooled (all four blocks): " + "; ".join(f"{v}: n {R[v]['attempts']}, exits {R[v]['exits']}, flat mean {R[v]['stats']['1.9|total|rent0|flat']['mean_pct']:.4f}%, press {R[v]['stats']['1.9|total|rent0|press']['mean_pct']:.4f}%" for v in R))
P("")
v = "a40"
chk = [
 ("1 delta>=+1.0pp both models (conf)", all(g(v, C, m, "mean_pct") - g("base", C, m, "mean_pct") >= 1.0 for m in ("flat", "press"))),
 ("2 ex-top3 >0 both (conf)", all(g(v, C, m, "ex_top3") > 0 for m in ("flat", "press"))),
 ("3 ex-best-day >0 both (conf)", all(g(v, C, m, "ex_best_day") > 0 for m in ("flat", "press"))),
 ("4 attempts/day >=4.0 conf and disc", g(v, C, "flat", "n") / cal(v)[C] >= 4.0 and g(v, D, "flat", "n") / cal(v)[D] >= 4.0),
 ("4b (info) base conf attempts/day >=4.0", g("base", C, "flat", "n") / cal("base")[C] >= 4.0),
 ("5 discovery delta >0 both", all(g(v, D, m, "mean_pct") - g("base", D, m, "mean_pct") > 0 for m in ("flat", "press"))),
]
for k, ok in chk: P(f"- A40 line {k}: {'PASS' if ok else 'FAIL'}")
P(f"- A40 verdict: {'KEEP' if all(ok for k,ok in chk if not k.startswith('4b')) else 'DROP'}")
P("- A41 verdict: DROP (redundant: the engine's GUARD=1.15 is the base). Delta vs base is 0 by construction; no separate run (a40/base/guardoff only). guard-off ablation above is diagnostic.")
print("\n".join(lines))
