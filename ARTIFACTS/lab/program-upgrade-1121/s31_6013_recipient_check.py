"""QP-RULING-1121a section 3 item 4: for every WSOL PumpSwap buy_exact_quote_in / buy / sell instruction in the post sample A (and P) that failed with Custom 6013
InvalidProtocolFeeRecipient, report whether the account at instruction position 9 (the fee recipient; the executor's FEE recipient position, pumpswap_tx.swap_accounts)
is in pumpswap_global_config.protocol_fee_recipients (read now, one getAccountInfo; sha256 equals the pin per s01) -- and in reserved_fee_recipient(s). Counts only,
no addresses printed. Run: /data/mal/venv/bin/python s31_6013_recipient_check.py"""
import base64, collections, gzip, json, sys, os
from pathlib import Path
sys.argv = [sys.argv[0]]
src = open("s18_instruction_shapes.py").read()
exec(src.split("res = {}; own_inputs")[0])
from common import LClient
import hashlib
c = LClient(max_calls=5, min_interval=0.5)
r = c.call("getAccountInfo", ["ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw", {"encoding": "base64", "commitment": "finalized"}])
raw = base64.b64decode(r["value"]["data"][0]); sha = hashlib.sha256(raw).hexdigest()
pins = json.load(open("../../../tools/pump_structure_pins.json"))["accounts"]["pumpswap_global_config"]
t = TYPES["pump_amm"]["GlobalConfig"]["type"]; off = 8; cfg = {}
for f in t["fields"]:
    cfg[f["name"]], off = walk("pump_amm", f["type"], raw, off)
LISTED = set(cfg["protocol_fee_recipients"]); RES = {cfg["reserved_fee_recipient"], *cfg["reserved_fee_recipients"]}; ZERO = "11111111111111111111111111111111"
res = {"config_ctx_slot": r["context"]["slot"], "config_sha256_equals_pin": sha == pins["sha256"], "n_protocol_fee_recipients_nonzero": len([x for x in LISTED if x != ZERO])}
for label, root in (("post", BASE / "raw"), ("pre", BASE / "raw_pre")):
    cnt = collections.Counter(); per = []
    for p in sorted((root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        for tx in (blk or {}).get("transactions", []):
            meta = tx.get("meta") or {}
            if meta.get("err") is None or errword(meta["err"]) != "InstructionError:Custom=6013": continue
            for where, d, acc in amm_ixs(tx):
                if len(acc) < 10 or acc[4] != WSOL: continue
                cls = DISC[d[:8].hex()]
                a9 = acc[9]; k = "listed" if a9 in LISTED else ("reserved_only" if a9 in RES else "not_listed")
                cnt[f"{cls}|{k}"] += 1; per.append([cls, k, where, len(acc)])
    res[label] = {"n_failed_6013_instructions": len(per), "by_class_and_membership": dict(cnt), "rows": per}
Path(os.environ.get("UPGRADE_OUT", "out") + "/s31_6013_recipient_check.json").write_text(json.dumps(res, indent=1))
print(json.dumps({k: v for k, v in res.items() if k not in ("post", "pre")}), "\npost", res["post"]["n_failed_6013_instructions"], res["post"]["by_class_and_membership"], "\npre", res["pre"]["n_failed_6013_instructions"], res["pre"]["by_class_and_membership"])
