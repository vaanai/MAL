"""QP-RULING-1128 ADD1 section 4: constant-product check of the D2 item 3 outlier. For every WSOL migration of M and M_pre (out/s11_migration_economics.json): product sol_amount x mint_amount,
relative distance to the pre (M_pre) non-mayhem median product, and the rows outside the 1116 stratum ranges. Structure only (no prices). Rule declared by ADD1 for future reviews: a non-mayhem value outside its range
passes if sol x mint is within 1e-6 relative of the pre median product and D2 items 1 and 2 hold."""
import json, statistics
d = json.load(open("out/s11_migration_economics.json"))
pre = [r for r in d["pre"]["rows"] if r.get("sol_amount") and not r.get("is_mayhem_mode")]
med = statistics.median(r["sol_amount"] * r["mint_amount"] for r in pre)
res = {"pre_non_mayhem_n": len(pre), "pre_median_product": med, "post": []}
for lab in ("post", "pre"):
    rows = [r for r in d[lab]["rows"] if r.get("sol_amount") and not r.get("is_mayhem_mode")]
    rel = [abs(r["sol_amount"] * r["mint_amount"] - med) / med for r in rows]
    res[f"{lab}_non_mayhem_n"] = len(rows); res[f"{lab}_max_rel_distance"] = max(rel); res[f"{lab}_n_within_1e-6"] = sum(x <= 1e-6 for x in rel)
for r in d["post"]["rows"]:
    if r["sig"].startswith("22UDcoA8"):
        res["outlier"] = {"sig": r["sig"], "sol_amount": r["sol_amount"], "mint_amount": r["mint_amount"], "product": r["sol_amount"] * r["mint_amount"], "rel_distance_to_pre_median": abs(r["sol_amount"] * r["mint_amount"] - med) / med}
res["range_endpoint_product"] = 94846797684 * 185399044810855
json.dump(res, open("out/s43_sol_x_mint.json", "w"), indent=1); print(json.dumps(res, indent=1))
