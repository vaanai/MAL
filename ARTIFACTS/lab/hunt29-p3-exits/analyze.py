"""Analyze P3 results: python3 analyze.py OUTDIR  (needs KIT on sys.path for the kit's stat()). Writes SCREENS.md rows to stdout + rows.json."""
import json, os, sys
sys.path.insert(0, os.environ.get("KIT", "/data2/mal/wt/p3-exits"))
import numpy as np
import tools.arena_screen as S

out = sys.argv[1]
d = json.load(open(f"{out}/results.json"))
cells, names = d["cells"], d["meta"]["blocks"]
ndays = None
FAM = {"F": ("F0", "FLOOR v2"), "G": ("G0", "G4 v1 kit form")}
rows = {}


def row(name, s_flat, s_press, att, days, extra=None):
    r = dict(name=name, tpd=att / days, n=att,
             flat=s_flat["mean"], press=s_press["mean"], flat_pct=s_flat["mean_pct"], press_pct=s_press["mean_pct"],
             flat_day=s_flat["total"] / days, press_day=s_press["total"] / days,
             x3f=s_flat["ex_top3"], x3p=s_press["ex_top3"], bdf=s_flat["ex_best_day"], bdp=s_press["ex_best_day"],
             dpf=f'{s_flat["pos_days"]}/{s_flat["days"]}', dpp=f'{s_press["pos_days"]}/{s_press["days"]}',
             cif=s_flat["ci_lo_trade"], cip=s_press["ci_lo_trade"])
    if extra:
        r.update(extra)
    rows[name] = r


for nm, c in cells.items():
    if not c.get("attempts"):
        continue
    ndays = c["days_in_run"]
    sf, sp = c["stats"]["1.9|total|rent0|flat"], c["stats"]["1.9|total|rent0|press"]
    big = c["stake_1_0_total_rent0_1p9"]
    im = c["impact"]["1.9"]
    row(nm, sf, sp, c["attempts"], ndays, dict(big_f=big["flat"]["mean_pct"], big_p=big["press"]["mean_pct"], imp25=im["0.25"] * 100, imp10=im["1.0"] * 100,
                                          exits=c["exits"], excl=c["excluded_share"], kill=bool(c.get("kill")), kill_reasons=c.get("kill_reasons")))


def partial(a_name, b_name, tag):
    try:
        A = np.load(f"{out}/trades/trades_{a_name}.npz"); B = np.load(f"{out}/trades/trades_{b_name}.npz")
    except FileNotFoundError:
        return
    ka = {(int(b), int(p), int(s)): i for i, (b, p, s) in enumerate(zip(A["blk"], A["pool"], A["sd"]))}
    ia, ib = [], []
    for j, (b, p, s) in enumerate(zip(B["blk"], B["pool"], B["sd"])):
        i = ka.get((int(b), int(p), int(s)))
        if i is not None:
            ia.append(i); ib.append(j)
    ia, ib = np.array(ia), np.array(ib)
    hit = (A["why"][ia] == 1)
    res = {}
    for f in ("flat", "press"):
        lam = 0.5 * A[f][ia] + 0.5 * B[f][ib] - hit * 160000.0
        res[f] = S.stat(lam, B["T"][ib] // 86400, B["blk"][ib], names)
    row(tag, res["flat"], res["press"], len(ia), ndays, dict(match=f"{len(ia)} of A {len(ka)} / B {len(B['pool'])}", tp_hit_share=float(hit.mean()) if len(ia) else None))


partial("PA", "PB", "P50")
partial("GA", "GB", "GP50")
base = {"F": rows.get("F0"), "G": rows.get("G0")}
for g in ("r2h", "r6h", "r12h"):
    base[g] = rows.get(f"RC_{g}")


def fam(n):
    if n.startswith("G"):
        return "G"
    if n.endswith(("_r2h", "_r6h", "_r12h")):
        return n.rsplit("_", 1)[1]
    return "F"


for n, r in rows.items():
    b = base.get(fam(n))
    r["beats"] = bool(b and n != b["name"] and r["flat"] > b["flat"] and r["press"] > b["press"] and r["x3f"] > b["x3f"] and r["x3p"] > b["x3p"])
    r["lead"] = bool(r["beats"] and min(r["flat"], r["press"], r["x3f"], r["x3p"], r["bdf"], r["bdp"], r["cif"], r["cip"]) > 0 and r["n"] >= 100)
json.dump(rows, open(f"{out}/rows.json", "w"), indent=1)
print("| cell | trades/day | flat SOL/tr | press SOL/tr | flat/day | press/day | ex-top3 f/p | drop-best-day f/p | days+ f/p | CI90 lo f/p | beats base | lead | impact .25/1.0 | kill |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for n, r in sorted(rows.items(), key=lambda kv: -kv[1]["press"]):
    print(f"| {n} | {r['tpd']:.1f} | {r['flat']:+.5f} | {r['press']:+.5f} | {r['flat_day']:+.3f} | {r['press_day']:+.3f} | {r['x3f']:+.2f} / {r['x3p']:+.2f} | {r['bdf']:+.2f} / {r['bdp']:+.2f} | {r['dpf']} / {r['dpp']} | {r['cif']:+.5f} / {r['cip']:+.5f} | {r['beats']} | {r['lead']} | {r.get('imp25', float('nan')):.2f} / {r.get('imp10', float('nan')):.2f} | {r.get('kill')} |")
