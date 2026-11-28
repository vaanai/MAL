"""QP-RULING-1128 N1 from out/s11_migration_economics.json (pool accounts read there): pool address == canonical_pool(mint) at tools/pumpswap_tx.py e85447f8, is_mayhem_mode byte (offset 243)
== the CreatePoolEvent flag, owner pAMM, data length, 8-byte discriminator, coin_creator set, token-program fields, for the 10 post pools (new binary) and for the pre pools (rows with a pool read in the s11 c3 'old24' = 1114 pools are NOT used: pre = M_pre rows are checked by canonical_pool and flag only)."""
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("pst", "/data2/mal/scratch/a3-repin-1128/pumpswap_tx_e85447f8.py"); pst = importlib.util.module_from_spec(spec); sys.modules["pst"] = pst; spec.loader.exec_module(pst)
from solders.pubkey import Pubkey
d = json.load(open("out/s11_migration_economics.json"))
rows = {r["pool"]: r for r in d["post"]["rows"]}
res = {"post_new10": [], "post_rows_canonical": {}, "pre_rows_canonical": {}}
for i in d["c3_pool_accounts"]["new10"]:
    r = rows.get(i["pool"]); ok = "owner" in i
    res["post_new10"].append({"pool": i["pool"], "owner": i.get("owner"), "len": i.get("len"), "disc": i.get("disc"), "coin_creator_set": ok and not i["coin_creator_is_zero"],
        "mayhem_byte_eq_event": ok and r is not None and bool(i["is_mayhem_mode"]) == bool(r.get("is_mayhem_mode")), "canonical_eq": ok and str(pst.canonical_pool(Pubkey.from_string(i["base_mint"]))) == i["pool"],
        "base_token_account_owner_program": i.get("base_token_account_owner_program"), "quote_token_account_owner_program": i.get("quote_token_account_owner_program")})
for lab in ("post", "pre"):
    rs = d[lab]["rows"]; res[f"{lab}_rows_canonical"] = {"n": len(rs), "canonical_eq": sum(str(pst.canonical_pool(Pubkey.from_string(r["mint"]))) == r["pool"] for r in rs)}
n = res["post_new10"]
res["summary"] = {"n_post": len(n), "owner_len_disc": sorted({(x["owner"], x["len"], x["disc"]) for x in n}), "coin_creator_set": sum(x["coin_creator_set"] for x in n), "mayhem_eq": sum(x["mayhem_byte_eq_event"] for x in n),
                  "canonical_eq": sum(x["canonical_eq"] for x in n), "token_program_fields": sorted({(x["base_token_account_owner_program"], x["quote_token_account_owner_program"]) for x in n}, key=str)}
Path("out/s41_n1_pool_checks.json").write_text(json.dumps(res, indent=1)); print(json.dumps({k: res[k] for k in ("summary", "post_rows_canonical", "pre_rows_canonical")}, indent=1))
