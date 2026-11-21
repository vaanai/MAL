"""Check M3, bar D2 of QP-RULING-1116-a3-stage2-addD.md (sha256 94db6cc7d778e9605eed9b4f1209bbd7fa25f611590325b5f29c4d32d5d90bc2) section 4. Committed BEFORE it was run on M3.
Inputs (all in UPGRADE_OUT, produced by s10 SAMPLE=m3, s11 and s24 on M3 only): s11_migration_economics.json (post rows = M3 WSOL migrations), s24_stage2_checks.json.
Item 1 invariants n of n; item 2 from s24 (field by field, frozen None, undecodable lines; 'walker short' = the layout_short category of the undecodable count, since every Program
data line is walked); item 3 stratified ranges (mayhem flag from the CreatePoolEvent); item 4 pool-account is_mayhem_mode byte (offset 243) by getMultipleAccounts, public RPC.
Report only: medians overall and by stratum, mayhem share, first-print V. Structure only; 0 Helius credits."""
import base64, json, math, os, statistics
from pathlib import Path
from common import LClient
OUTD = Path(os.environ.get("UPGRADE_OUT", "out"))
S2 = OUTD / "s25_migration_bar_d.json"   # 1121: written by s25 (union includes the 1114 and 1115 reviews, all pre-deploy)
s11 = json.load(open(OUTD / "s11_migration_economics.json")); s24 = json.load(open(OUTD / "s24_stage2_checks.json"))
rows = s11["post"]["rows"]; n = len(rows)
UNION = json.load(open(S2))["union_range"]   # ADD1 D union of pre data (this review's M_pre + 1114 P and M)
res = {"n_wsol": n}
# item 1
inv = {"sol_amount==pool_quote_amount==quote_amount_in": 0, "mint_amount==pool_base_amount==base_amount_in": 0, "initial_liquidity==isqrt(pool_base*pool_quote)": 0, "pool_migration_fee==15000001": 0, "creator_fee_bps==0": 0}
bad = []
for r in rows:
    try:
        ok = [r["sol_amount"] == r["pool_quote_amount"] == r["quote_amount_in"], r["mint_amount"] == r["pool_base_amount"] == r["base_amount_in"],
              r["initial_liquidity"] == math.isqrt(r["pool_base_amount"] * r["pool_quote_amount"]), r["pool_migration_fee"] == 15000001, r["creator_fee_bps"] == 0]
    except (KeyError, TypeError):
        ok = [False] * 5
    for k, v in zip(inv, ok): inv[k] += bool(v)
    if not all(ok): bad.append(r["sig"])
res["item1"] = {"counts_n_of_n": inv, "n": n, "failing_sigs": bad, "verdict": "MET" if not bad and n > 0 else "NOT MET"}
# item 2
pm = s24["post"]; fb = pm["field_by_field"]; ud = pm["undecodable_program_data"]
i2 = {t: {"n": fb[t]["n"], "compared": fb[t]["total_compared"], "unequal": fb[t]["total_unequal"], "frozen_none": fb[t]["frozen_none"]} for t in ("CompletePumpAmmMigrationEvent", "CreatePoolEvent")}
i2["undecodable_lines_successful"] = ud["total_hits_successful_sampled"]; i2["lines_checked"] = ud["lines_checked"]; i2["walker_short(layout_short)"] = ud["successful_sampled"].get("layout_short", 0)
res["item2"] = {**i2, "verdict": "MET" if all(i2[t]["unequal"] == 0 and i2[t]["frozen_none"] == 0 and i2[t]["n"] > 0 for t in ("CompletePumpAmmMigrationEvent", "CreatePoolEvent")) and i2["undecodable_lines_successful"] == 0 and i2["walker_short(layout_short)"] == 0 else "NOT MET"}
# item 3
out = {"non_mayhem_sol_out_of_range": [], "mayhem_sol_out_of_range": [], "below_5SOL_not_mayhem": [], "mint_out_of_range": [], "liquidity_out_of_union": [], "missing_mayhem_flag": []}
for r in rows:
    mh = r.get("is_mayhem_mode")
    if mh is None: out["missing_mayhem_flag"].append(r["sig"]); continue
    s = r["sol_amount"]
    if not mh and not (84990359056 <= s <= 94846797684): out["non_mayhem_sol_out_of_range"].append([r["sig"], s])
    if mh and not (12582941 <= s <= 12563593233): out["mayhem_sol_out_of_range"].append([r["sig"], s])
    if s < 5000000000 and not mh: out["below_5SOL_not_mayhem"].append([r["sig"], s])
    if not (185399044810855 <= r["mint_amount"] <= 206900000000000): out["mint_out_of_range"].append([r["sig"], r["mint_amount"]])
    if not (UNION["initial_liquidity"][0] <= r["initial_liquidity"] <= UNION["initial_liquidity"][1]): out["liquidity_out_of_union"].append([r["sig"], r["initial_liquidity"]])
res["item3"] = {**out, "union_initial_liquidity": UNION["initial_liquidity"], "verdict": "MET" if not any(out.values()) and n > 0 else "NOT MET"}
# item 4
c = LClient(max_calls=100, min_interval=0.5); flags = {}; unreadable = []
pools = [r["pool"] for r in rows]
for i in range(0, len(pools), 90):
    ch = pools[i:i + 90]; v = c.call("getMultipleAccounts", [ch, {"encoding": "base64", "commitment": "finalized"}])
    for a, x in zip(ch, v["value"]):
        if x is None: unreadable.append(a); continue
        d = base64.b64decode(x["data"][0]); flags[a] = d[243] if len(d) > 243 else None
mism = [[r["pool"], r["is_mayhem_mode"], flags[r["pool"]]] for r in rows if r["pool"] in flags and flags[r["pool"]] != int(bool(r["is_mayhem_mode"]))]
res["item4"] = {"readable": len(flags), "unreadable_pools": unreadable, "mismatches": mism, "rpc_attempts": c.total_calls, "verdict": "MET" if not mism and len(unreadable) <= 2 and len(flags) > 0 else "NOT MET"}
# report only
def vals(rs, k): return [r[k] for r in rs if r.get(k) is not None]
rep = {"mayhem_share": sum(1 for r in rows if r.get("is_mayhem_mode")) / n if n else None, "n_mayhem": sum(1 for r in rows if r.get("is_mayhem_mode"))}
for name, sub in (("all", rows), ("mayhem", [r for r in rows if r.get("is_mayhem_mode")]), ("non_mayhem", [r for r in rows if not r.get("is_mayhem_mode")])):
    rep[name] = {"n": len(sub), **{f"median_{k}": (statistics.median(vals(sub, k)) if sub else None) for k in ("sol_amount", "mint_amount", "initial_liquidity")}}
rep["first_print_V"] = s11["post"]["summary"].get("first_print_V"); rep["first_print_not_observed"] = s11["post"]["summary"].get("first_print_not_observed")
res["report_only"] = rep
res["all_items"] = "MET" if all(res[k]["verdict"] == "MET" for k in ("item1", "item2", "item3", "item4")) else "NOT MET"
(OUTD / "s26_check_m3_bar_d2.json").write_text(json.dumps(res, indent=1)); print(json.dumps(res, indent=1))
