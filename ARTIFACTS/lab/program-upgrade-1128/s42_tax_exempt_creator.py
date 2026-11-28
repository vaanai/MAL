"""QP-RULING-1128 T4: tax_exempt_creator values by count on the pump BondingCurve accounts of the WSOL mints of M (post migrations). BondingCurve is 206 bytes with
created_at_slot at 166..174 and tax_exempt_creator at 174..206 (docs BLOCK0_TAX.md, IDL order: 8 disc + fields); shorter accounts read as 0 / default. Public RPC, 0 credits.
Counts only: equal to creator / default / other; created_at_slot class (before or after D)."""
import base64, json
from common import OUT, LClient
from solders.pubkey import Pubkey
PUMP = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
d = json.load(open("out/s11_migration_economics.json")); mints = [r["mint"] for r in d["post"]["rows"]]
bcs = [str(Pubkey.find_program_address([b"bonding-curve", bytes(Pubkey.from_string(m))], PUMP)[0]) for m in mints]
c = LClient(max_calls=10, min_interval=0.5)
r = c.call("getMultipleAccounts", [bcs, {"encoding": "base64", "commitment": "finalized"}])
cnt = {"n": len(bcs), "closed_or_absent": 0, "len": {}, "exempt_equals_creator": 0, "exempt_default": 0, "exempt_other": 0, "created_at_slot_gt_0": 0}
for v in r["value"]:
    if v is None: cnt["closed_or_absent"] += 1; continue
    b = base64.b64decode(v["data"][0]); cnt["len"][str(len(b))] = cnt["len"].get(str(len(b)), 0) + 1
    creator = b[49:81]  # 8 disc + 5 u64 (40) + complete(1) = 49
    if len(b) >= 206:
        ex = b[174:206]; cnt["created_at_slot_gt_0"] += int.from_bytes(b[166:174], "little") > 0
        if ex == bytes(32): cnt["exempt_default"] += 1
        elif ex == creator: cnt["exempt_equals_creator"] += 1
        else: cnt["exempt_other"] += 1
    else: cnt["exempt_default"] += 1
cnt["ctx_slot"] = r["context"]["slot"]
(OUT / "s42_tax_exempt_creator.json").write_text(json.dumps(cnt, indent=1)); print(cnt)
