"""ADD1 D replacement bar on M + M2 (stage 2): medians equal pre's to the lamport, pool_migration_fee constant and equal to pre's, every post value inside the range of the
UNION of pre data (this review's M_pre plus the 1114 review's M_pre and M). Offline from out_stage2/s11_migration_economics.json (post = M then M2, in order) and the
1114 file program-upgrade-1114/out/s11_migration_economics.json. Also M2 alone and M alone. Structure only."""
import json, os, statistics
from pathlib import Path
OUTD = Path(os.environ.get("UPGRADE_OUT", "out"))
new = json.load(open(OUTD / "s11_migration_economics.json")); old = json.load(open("../program-upgrade-1114/out/s11_migration_economics.json"))
mig = json.load(open(os.environ.get("A3_BASE", "/data2/mal/scratch/a3-repin-1121") + "/raw/migrate_index.json")); n_m = mig["m_n"]
K = ("sol_amount", "mint_amount", "initial_liquidity", "pool_migration_fee")
post_all = new["post"]["rows"]; msigs = {r["sig"] for r in mig["post"][:n_m]}
sets = {"M": [r for r in post_all if r["sig"] in msigs], "M2": [r for r in post_all if r["sig"] not in msigs], "M+M2": post_all}
pre_new = new["pre"]["rows"]; pre_old = old["pre"]["rows"] + old["post"]["rows"]
union = pre_new + pre_old
def vals(rows, k): return [r[k] for r in rows if r.get(k) is not None]
res = {"n_pre_this_review": len(pre_new), "n_1114_pre": len(old["pre"]["rows"]), "n_1114_M": len(old["post"]["rows"]), "union_range": {k: [min(vals(union, k)), max(vals(union, k))] for k in K},
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

# POST-HOC, REPORT-ONLY (declared here after the M2 median read NOT MET; not a bar, not a replacement of ADD1 D): the same statistics split by is_mayhem_mode of the CreatePoolEvent,
# with the share of migrations with sol_amount < 5 SOL, per group (this review's M, M2, pre; 1114 pre and M).
grp = {"M": sets["M"], "M2": sets["M2"], "pre_this_review": pre_new, "pre_1114_P": old["pre"]["rows"], "M_1114": old["post"]["rows"]}
ph = {}
for g, rows in grp.items():
    lo = [r for r in rows if r["sol_amount"] < 5e9]
    d = {"n": len(rows), "n_sol_below_5_SOL": len(lo), "share_below_5_SOL": len(lo) / len(rows), "n_mayhem_true": sum(1 for r in rows if r.get("is_mayhem_mode")),
         "mayhem_true_equals_below_5_SOL": {r["sig"] for r in lo} == {r["sig"] for r in rows if r.get("is_mayhem_mode")}}
    for flag in (False, True):
        sub = [r for r in rows if bool(r.get("is_mayhem_mode")) == flag]
        if sub: d[f"mayhem_{flag}"] = {"n": len(sub), "median": {k: statistics.median(vals(sub, k)) for k in K[:3]}, "min": {k: min(vals(sub, k)) for k in K[:3]}, "max": {k: max(vals(sub, k)) for k in K[:3]}}
    ph[g] = d
res["post_hoc_split_by_mayhem_report_only"] = ph
(OUTD / "s25_migration_bar_d.json").write_text(json.dumps(res, indent=1))
print(json.dumps(ph, indent=1))
