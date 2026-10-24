import json, sys
d = json.load(open(sys.argv[1] + "/results.json"))
cells = d["cells"]
SUB = [("total", "flat"), ("total", "press"), ("sc", "flat"), ("sc", "press")]
def st(c, rule, fail): return c["stats"][f"1.9|{rule}|rent0|{fail}"]
rows, labels = [], {}
for name, c in cells.items():
    n = c["attempts"]
    dd = c["days_in_run"]
    lab = "FAIL"
    s = {sf: st(c, *sf) for sf in SUB}
    means = [s[sf]["mean"] for sf in SUB]
    ex3 = [s[sf]["ex_top3"] for sf in SUB]
    exb = [s[sf]["ex_best_day"] for sf in SUB]
    cil = [min(s[sf]["ci_lo_trade"], s[sf]["ci_lo"]) for sf in SUB]
    g = c["groups"]
    grp_ok = all(g.get(f"{b}|{r}|{f}", {}).get("mean", -1) > 0 for b in ("explore-0814", "september") for r, f in SUB)
    pos_maj = all(s[sf]["pos_days"] * 2 > s[sf]["days"] for sf in SUB)
    killed = c.get("kill")
    if min(means) > 0 and min(ex3) > 0 and min(exb) > 0:
        full = min(cil) > 0 and n >= 100 and min(s[sf]["days"] for sf in SUB) >= 5 and pos_maj and grp_ok and not killed
        lab = "SCREEN-PASS-CANDIDATE" if full else "MARGINAL"
    labels[name] = lab
    rows.append((name, c, s, lab))
out = []
for name, c, s, lab in rows:
    t = s[("total", "flat")]; p = s[("total", "press")]
    big = c["stake_1_0_total_rent0_1p9"]
    out.append(f"### {name}: {lab}")
    out.append(f"- attempts {c['attempts']} over {c['days_in_run']} days = {c['attempts_per_day']:.1f}/day (explore-0814 alone {c.get('explore_attempts_per_day')}/day); excluded block edge {c.get('excluded_share')}; exits {c['exits']}")
    for lbl, x in (("flat15 total", t), ("pressure total", p), ("flat15 sc", s[("sc", "flat")]), ("pressure sc", s[("sc", "press")])):
        out.append(f"- 0.25 SOL {lbl}: mean {x['mean']:.5f} SOL/trade ({x['mean_pct']:.2f}%), total {x['total']:.3f}, SOL/day {x['total']/c['days_in_run']:.4f}, ex-top3 {x['ex_top3']:.3f}, ex-best-day {x['ex_best_day']:.3f}, days+ {x['pos_days']}/{x['days']}, CI90lo trade {x['ci_lo_trade']:.5f} date {x['ci_lo']:.5f}")
    for f in ("flat", "press"):
        x = big[f]
        out.append(f"- 1.0 SOL {f} total: mean {x['mean']:.5f} SOL/trade ({x['mean_pct']:.2f}%), total {x['total']:.3f}, SOL/day {x['total']/c['days_in_run']:.4f}, ex-top3 {x['ex_top3']:.3f}, ex-best-day {x['ex_best_day']:.3f}, CI90lo trade {x['ci_lo_trade']:.5f}")
    im = c["impact"]["1.9"]
    out.append(f"- impact (entry) 0.25 SOL {im['0.25']*100:.3f}%, 1.0 SOL {im['1.0']*100:.3f}% (0.5 SOL interpolated {((im['0.25']+im['1.0'])/2)*100:.3f}%) ; worst-in-slot measured {c['worst_in_slot']['measured']}; kill {c.get('kill')} {c.get('kill_reasons')}; screen_p {c.get('screen_p',{}).get('cell_p')}")
    gg = c["groups"]
    out.append("- groups (total/flat mean%): " + ", ".join(f"{k.split('|')[0]} n={v['n']} {v['mean_pct']:.2f}" for k, v in gg.items() if k.endswith("total|flat")))
    out.append("")
print("\n".join(out))
json.dump(labels, open(sys.argv[1] + "/labels.json", "w"))
