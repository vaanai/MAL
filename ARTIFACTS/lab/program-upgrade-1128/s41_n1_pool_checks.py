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
# pre side (QP 1128 round 2, N1 must-fix): the first 12 WSOL pools of M_pre read with ONE getMultipleAccounts, the same fields as the post 10; their pool token accounts with a second call (token-program fields).
import base64
from common import LClient
c = LClient(max_calls=6, min_interval=0.5)
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
def b58e(b):
    n = int.from_bytes(b, "big"); out = ""
    while n: n, r_ = divmod(n, 58); out = B58[r_] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out
pre = d["pre"]["rows"][:12]
r1 = c.call("getMultipleAccounts", [[x["pool"] for x in pre], {"encoding": "base64", "commitment": "finalized"}])
res["pre_ctx_slot"] = r1["context"]["slot"]; infos = []
for x, v in zip(pre, r1["value"]):
    if v is None: infos.append({"pool": x["pool"], "closed": True}); continue
    b = base64.b64decode(v["data"][0])
    infos.append({"pool": x["pool"], "owner": v["owner"], "len": len(b), "disc": b[:8].hex(), "base_mint": b58e(b[43:75]), "coin_creator": b58e(b[211:243]), "mayhem_byte": b[243],
                  "pb": b58e(b[139:171]), "pq": b58e(b[171:203]), "event_mayhem": bool(x.get("is_mayhem_mode")), "event_coin_creator": x.get("coin_creator"), "mint": x["mint"]})
open_ = [i for i in infos if "owner" in i]
r2 = c.call("getMultipleAccounts", [[a for i in open_ for a in (i["pb"], i["pq"])], {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}, "commitment": "finalized"}])
own = {}
for (a), v in zip([a for i in open_ for a in (i["pb"], i["pq"])], r2["value"]): own[a] = v["owner"] if v else None
res["pre_pools"] = []
for i in open_:
    res["pre_pools"].append({"pool": i["pool"], "owner": i["owner"], "len": i["len"], "disc": i["disc"], "coin_creator_set": i["coin_creator"] != "1" * 32, "coin_creator_eq_event": i["coin_creator"] == i["event_coin_creator"],
        "mayhem_byte_eq_event": bool(i["mayhem_byte"]) == i["event_mayhem"], "base_mint_eq_event_mint": i["base_mint"] == i["mint"], "canonical_eq": str(pst.canonical_pool(Pubkey.from_string(i["base_mint"]))) == i["pool"],
        "base_token_account_owner_program": own.get(i["pb"]), "quote_token_account_owner_program": own.get(i["pq"])})
p_ = res["pre_pools"]
res["pre_summary"] = {"n_requested": len(pre), "n_read_open": len(p_), "owner_len_disc": sorted({(x["owner"], x["len"], x["disc"]) for x in p_}), "coin_creator_set": sum(x["coin_creator_set"] for x in p_), "mayhem_eq": sum(x["mayhem_byte_eq_event"] for x in p_),
                      "canonical_eq": sum(x["canonical_eq"] for x in p_), "token_program_fields": sorted({(x["base_token_account_owner_program"], x["quote_token_account_owner_program"]) for x in p_}, key=str)}
res["pre_vs_post_fields_equal"] = res["pre_summary"]["owner_len_disc"] == res["summary"]["owner_len_disc"] and res["pre_summary"]["token_program_fields"] == res["summary"]["token_program_fields"] and res["pre_summary"]["n_read_open"] == res["pre_summary"]["canonical_eq"] == res["pre_summary"]["mayhem_eq"] == res["pre_summary"]["coin_creator_set"]
Path("out/s41_n1_pool_checks.json").write_text(json.dumps(res, indent=1)); print(json.dumps({k: res[k] for k in ("summary", "pre_summary", "pre_vs_post_fields_equal", "post_rows_canonical", "pre_rows_canonical")}, indent=1))
