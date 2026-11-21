"""QP follow-up (coordinator 2026-11-21): characterise the post-deploy 6013 InvalidProtocolFeeRecipient failures raised AT PumpSwap, and the recipient list used by ALL
sampled buy_exact_quote_in instructions, split by the pool's mayhem flag. Chain-only, cached blocks of A + one config read + pool reads (getMultipleAccounts).
Booleans and counts only: no address, signature or amount printed. Run: /data/mal/venv/bin/python s35_6013_characterise.py (writes out/s35_6013_characterise.json)"""
import base64, collections, gzip, json, sys, os, time
from pathlib import Path
sys.argv = [sys.argv[0]]
src = open("s18_instruction_shapes.py").read(); exec(src.split("res = {}; own_inputs")[0])
from common import LClient
c = LClient(max_calls=60, min_interval=0.5)
raw = base64.b64decode(c.call("getAccountInfo", ["ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw", {"encoding": "base64", "commitment": "finalized"}])["value"]["data"][0])
cfg = {}; off = 8
for f in TYPES["pump_amm"]["GlobalConfig"]["type"]["fields"]: cfg[f["name"]], off = walk("pump_amm", f["type"], raw, off)
ZERO = "11111111111111111111111111111111"
LISTS = {"protocol_fee_recipients": cfg["protocol_fee_recipients"], "reserved_fee_recipients": [cfg["reserved_fee_recipient"]] + cfg["reserved_fee_recipients"], "buyback_fee_recipients": cfg["buyback_fee_recipients"]}
def which(a):
    out = [f"{n}[{l.index(a)}]" for n, l in LISTS.items() if a in l]
    return out or ["none"]
cfg_info = {"mayhem_mode_enabled": cfg["mayhem_mode_enabled"], **{f"n_nonzero_{n}": len([x for x in l if x != ZERO]) for n, l in LISTS.items()},
            "idl_fields_with_mayhem_in_name": [f["name"] for f in TYPES["pump_amm"]["GlobalConfig"]["type"]["fields"] if "mayhem" in f["name"] or "reserved" in f["name"]],
            "idl_other_types_with_mayhem": [t["name"] for t in IDL["pump_amm"]["types"] if any("mayhem" in f.get("name", "") for f in (t["type"].get("fields") or []) if isinstance(f, dict))]}
# pass 1: every WSOL buy_exact_quote_in instruction in A (ok or failed), with its pool and position-9 account, failing flag for 6013-at-amm
rows = []; fails = []
for p in sorted((BASE / "raw" / "blocks").glob("*.json.gz")):
    blk = json.loads(gzip.open(p).read())["r"]
    if not blk: continue
    for tx in blk.get("transactions", []):
        meta = tx.get("meta") or {}; keys = keys_of(tx); top = tx["transaction"]["message"]["instructions"]
        err = meta.get("err"); ei = err["InstructionError"][0] if isinstance(err, dict) and "InstructionError" in err else None
        is6013 = err is not None and errword(err) == "InstructionError:Custom=6013"
        amm_tops = [(i, b58d(ix["data"]), [keys[a] for a in ix["accounts"]]) for i, ix in enumerate(top) if keys[ix["programIdIndex"]] == AMM]
        for where, d, acc in amm_ixs(tx):
            if DISC[d[:8].hex()] != "buy_exact_quote_in" or len(acc) < 10 or acc[4] != WSOL: continue
            ti = next((i for i, dd, aa in amm_tops if aa == acc and dd == d), None)
            raised = bool(is6013 and where == "top" and ti is not None and ti == ei)
            r = {"pool": acc[0], "p9": acc[9], "ok": err is None, "raised6013": raised, "where": where}
            rows.append(r)
            if raised:
                before = [dd[:8].hex() for i, dd, aa in amm_tops if i < ti]
                tprogs = [keys[ix["programIdIndex"]][:8] for ix in top]
                fails.append({"pool": acc[0], "p9": acc[9], "slot": blk["slot"] if "slot" in blk else p.name.split(".")[0], "utc": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(blk["blockTime"])),
                              "n_amm_ixs_before_failing": len(before), "amm_ix_discs_before": before, "failing_top_index": ti, "n_top": len(top), "failing_is_direct_top_level_pumpswap": True,
                              "top_programs_other_than_pumpswap_budget_system": sorted({t for t in tprogs if t not in ("ComputeB", "11111111", "ATokenGP", "pAMMBay6", "Tokenkeg", "TokenzQd")})})
pools = sorted({r["pool"] for r in rows}); mh = {}
for i in range(0, len(pools), 100):
    ch = pools[i:i + 100]; v = c.call("getMultipleAccounts", [ch, {"encoding": "base64", "commitment": "finalized"}])
    for a, x in zip(ch, v["value"]):
        if x is None: continue
        b = base64.b64decode(x["data"][0]); mh[a] = (b[243] if len(b) > 243 else None)
res = {"config": cfg_info, "n_buy_exact_quote_in_instructions_in_A": len(rows), "n_pools": len(pools), "n_pool_accounts_readable_now": len(mh)}
agg = collections.defaultdict(collections.Counter)
for r in rows:
    f = {1: "mayhem", 0: "non_mayhem"}.get(mh.get(r["pool"]), "pool_unreadable")
    agg[f]["n_all"] += 1; agg[f]["n_success"] += int(r["ok"]); agg[f]["n_6013_at_pumpswap"] += int(r["raised6013"])
    agg[f]["p9_list|" + "+".join(which(r["p9"]))[:80]] += 1 if r["ok"] else 0
    if not r["ok"]: agg[f]["failed_p9_list|" + "+".join(which(r["p9"]))[:80]] += 1
res["by_pool_mayhem_flag"] = {k: dict(v) for k, v in agg.items()}
tot = len(rows); n6 = sum(r["raised6013"] for r in rows)
res["rate_6013_overall"] = {"n": tot, "n_6013": n6, "share": n6 / tot}
res["rate_6013_by_flag"] = {k: {"n": v["n_all"], "n_6013": v["n_6013_at_pumpswap"], "share": v["n_6013_at_pumpswap"] / v["n_all"]} for k, v in agg.items()}
det = []
for f in fails:
    det.append({"pool_is_mayhem_mode_byte": mh.get(f["pool"]), "p9_in": which(f["p9"]), "slot": f["slot"], "utc": f["utc"], "n_amm_ixs_before_failing": f["n_amm_ixs_before_failing"], "n_top": f["n_top"],
                "failing_top_index": f["failing_top_index"], "direct_top_level_pumpswap": True, "other_top_level_programs": f["top_programs_other_than_pumpswap_budget_system"]})
res["the_6013_at_pumpswap_instructions"] = det
Path("out/s35_6013_characterise.json").write_text(json.dumps(res, indent=1)); print(json.dumps(res, indent=1))
