"""2026-11-20 triple redeploy: ADD1 D replacement bar on M (single stage); the pre union now also holds the 1115 review M_pre, M and M2 (all before the deploy). Original text: ADD1 D replacement bar on M + M2 (stage 2): medians equal pre's to the lamport, pool_migration_fee constant and equal to pre's, every post value inside the range of the
UNION of pre data (this review's M_pre plus the 1114 review's M_pre and M). Offline from out_stage2/s11_migration_economics.json (post = M then M2, in order) and the
1114 file program-upgrade-1114/out/s11_migration_economics.json. Also M2 alone and M alone. Structure only."""
import json, os, statistics
from pathlib import Path
OUTD = Path(os.environ.get("UPGRADE_OUT", "out"))
new = json.load(open(OUTD / "s11_migration_economics.json")); old = json.load(open("../program-upgrade-1114/out/s11_migration_economics.json"))
old15 = json.load(open("../program-upgrade-1115/out_stage2/s11_migration_economics.json"))
old21 = json.load(open("../program-upgrade-1121/out/s11_migration_economics.json"))   # 2026-11-20 review: its M_pre and M, all before the 2026-11-27T22:20:22Z pump redeploy (PumpSwap changed at 11-20 but the migration economics bar compares the migrate path under the previous pump binaries)
K = ("sol_amount", "mint_amount", "initial_liquidity", "pool_migration_fee")
post_all = new["post"]["rows"]
sets = {"M": post_all}
pre_new = new["pre"]["rows"]; pre_old = old["pre"]["rows"] + old["post"]["rows"] + old15["pre"]["rows"] + old15["post"]["rows"] + old21["pre"]["rows"] + old21["post"]["rows"]   # the 1114 and 1115 reviews: all before 2026-11-27T22:20:22Z
union = pre_new + pre_old
def vals(rows, k): return [r[k] for r in rows if r.get(k) is not None]
res = {"n_pre_this_review": len(pre_new), "n_1114_pre": len(old["pre"]["rows"]), "n_1114_M": len(old["post"]["rows"]), "n_1115_pre": len(old15["pre"]["rows"]), "n_1115_M_plus_M2": len(old15["post"]["rows"]), "union_range": {k: [min(vals(union, k)), max(vals(union, k))] for k in K},
       "pre_median": {k: statistics.median(vals(pre_new, k)) for k in K}, "pre_fee_values": sorted(set(vals(pre_new, "pool_migration_fee")))}
for name, rows in sets.items():
    r = {"n_wsol": len(rows), "median": {k: statistics.median(vals(rows, k)) for k in K}, "min": {k: min(vals(rows, k)) for k in K}, "max": {k: max(vals(rows, k)) for k in K}, "fee_values": sorted(set(vals(rows, "pool_migration_fee")))}
    r["medians_equal_pre"] = {k: r["median"][k] == res["pre_median"][k] for k in K if k != "pool_migration_fee"}
    r["fee_constant_and_equal_pre"] = r["fee_values"] == res["pre_fee_values"] and len(r["fee_values"]) == 1
    r["outside_union"] = {k: [v for v in vals(rows, k) if not (res["union_range"][k][0] <= v <= res["union_range"][k][1])] for k in K}
    r["verdict"] = "MET" if all(r["medians_equal_pre"].values()) and r["fee_constant_and_equal_pre"] and not any(r["outside_union"].values()) else "NOT MET"
    res[name] = r
res["first_print_V_post"] = new["post"]["summary"]["first_print_V"]; res["first_print_V_pre"] = new["pre"]["summary"]["first_print_V"]
(OUTD / "s25_migration_bar_d.json").write_text(json.dumps(res, indent=1)); print(json.dumps({k: (v if not isinstance(v, dict) or "verdict" not in v else {"n": v["n_wsol"], "median": v["median"], "min": v["min"], "max": v["max"], "medians_equal_pre": v["medians_equal_pre"], "fee": v["fee_constant_and_equal_pre"], "outside": v["outside_union"], "verdict": v["verdict"]}) for k, v in res.items()}, indent=1))

