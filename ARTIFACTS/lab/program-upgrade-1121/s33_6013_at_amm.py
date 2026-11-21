"""Follow-up to s31/s32 (counts only): 6013 failures whose erroring top-level instruction is PumpSwap itself, in A, P and the three 1115 caches (old PumpSwap binary 469811067 and
the 10-19 binary). For each: which of the tx's top-level amm trade instructions is the erroring one (err index), its class, and whether its position-9 account is listed
in protocol_fee_recipients (config read now). Run: /data/mal/venv/bin/python s33_6013_at_amm.py"""
import base64, gzip, json, sys, os, collections
from pathlib import Path
sys.argv = [sys.argv[0]]
src = open("s18_instruction_shapes.py").read()
exec(src.split("res = {}; own_inputs")[0])
from common import LClient
c = LClient(max_calls=5, min_interval=0.5)
raw = base64.b64decode(c.call("getAccountInfo", ["ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw", {"encoding": "base64", "commitment": "finalized"}])["value"]["data"][0])
t = TYPES["pump_amm"]["GlobalConfig"]["type"]; off = 8; cfg = {}
for f in t["fields"]: cfg[f["name"]], off = walk("pump_amm", f["type"], raw, off)
LISTED = set(cfg["protocol_fee_recipients"])
B15 = Path("/data2/mal/scratch/a3-repin-1115")
out = {}
for label, root in {"A 1121 post": BASE / "raw", "P 1121 pre": BASE / "raw_pre", "1115 A1": B15 / "raw", "1115 A2": B15 / "raw_a2", "1115 pre": B15 / "raw_pre"}.items():
    n = collections.Counter(); rows = []
    for p in sorted((root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        for tx in (blk or {}).get("transactions", []):
            meta = tx.get("meta") or {}
            if meta.get("err") is None or errword(meta["err"]) != "InstructionError:Custom=6013": continue
            n["6013_failed_txs"] += 1
            keys = keys_of(tx); top = tx["transaction"]["message"]["instructions"]; ei = meta["err"]["InstructionError"][0]
            if ei >= len(top) or keys[top[ei]["programIdIndex"]] != AMM: continue
            ins = top[ei]; d = b58d(ins["data"]); acc = [keys[a] for a in ins["accounts"]]
            cls = DISC.get(d[:8].hex(), "other:" + d[:8].hex()); n["raised_at_pumpswap_top_ix"] += 1
            wsol = len(acc) > 4 and acc[4] == WSOL
            rows.append({"cls": cls, "wsol": wsol, "n_accts": len(acc), "data_len": len(d), "pos9_listed": (acc[9] in LISTED) if len(acc) > 9 else None})
    out[label] = {"counts": dict(n), "erroring_pumpswap_ix": rows}
Path("out/s33_6013_at_amm.json").write_text(json.dumps(out, indent=1))
for k, v in out.items(): print(k, v["counts"], collections.Counter((r["cls"], r["wsol"], r["n_accts"], r["pos9_listed"]) for r in v["erroring_pumpswap_ix"]))
