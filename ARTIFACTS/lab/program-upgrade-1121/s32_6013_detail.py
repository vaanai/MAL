"""Follow-up to s31 (counts only, no addresses/signatures): for each post 6013 failure, which instruction raised it (err index vs the amm instruction's top-level index / inner parent),
compute-budget/other top-level program ids, number of amm trade instructions in the tx, and whether the position-9 account of EACH amm trade instruction of the tx is in
protocol_fee_recipients. Run: /data/mal/venv/bin/python s32_6013_detail.py"""
import base64, gzip, json, sys, os
from pathlib import Path
sys.argv = [sys.argv[0]]
src = open("s18_instruction_shapes.py").read()
exec(src.split("res = {}; own_inputs")[0])
cfgs = json.load(open("out/s31_6013_recipient_check.json"))
from common import LClient
c = LClient(max_calls=5, min_interval=0.5)
raw = base64.b64decode(c.call("getAccountInfo", ["ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw", {"encoding": "base64", "commitment": "finalized"}])["value"]["data"][0])
t = TYPES["pump_amm"]["GlobalConfig"]["type"]; off = 8; cfg = {}
for f in t["fields"]: cfg[f["name"]], off = walk("pump_amm", f["type"], raw, off)
LISTED = set(cfg["protocol_fee_recipients"]); rows = []
for p in sorted((BASE / "raw" / "blocks").glob("*.json.gz")):
    blk = json.loads(gzip.open(p).read())["r"]
    for tx in (blk or {}).get("transactions", []):
        meta = tx.get("meta") or {}
        if meta.get("err") is None or errword(meta["err"]) != "InstructionError:Custom=6013": continue
        keys = keys_of(tx); top = tx["transaction"]["message"]["instructions"]
        err_idx = meta["err"]["InstructionError"][0]
        tops = [pn if False else keys[i["programIdIndex"]][:8] for i in top]
        amm = amm_ixs(tx)
        rows.append({"err_top_index": err_idx, "n_top": len(top), "err_ix_program": tops[err_idx] if err_idx < len(tops) else None, "n_amm_trade_ixs": len(amm),
                     "amm_ixs": [{"cls": DISC[d[:8].hex()], "where": w, "pos9_listed": (a[9] in LISTED) if len(a) > 9 else None} for w, d, a in amm],
                     "top_programs": tops, "err_top_ix_is_amm": bool(err_idx < len(top) and keys[top[err_idx]["programIdIndex"]] == AMM),
                     "inner_amm_under_err_ix": any(g["index"] == err_idx and any(keys[i["programIdIndex"]] == AMM for i in g["instructions"]) for g in (meta.get("innerInstructions") or []))})
Path("out/s32_6013_detail.json").write_text(json.dumps(rows, indent=1))
for r in rows: print({k: r[k] for k in ("err_top_index", "n_top", "err_ix_program", "n_amm_trade_ixs", "err_top_ix_is_amm", "inner_amm_under_err_ix")}, [(a["cls"], a["where"], a["pos9_listed"]) for a in r["amm_ixs"]])
