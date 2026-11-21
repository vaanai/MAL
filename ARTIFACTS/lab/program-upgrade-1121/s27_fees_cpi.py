"""Fees program, QP-style check (brief 2026-11-21): do PumpSwap buys and sells CPI into the fees program (pfeeUxB6...), post vs pre?
Offline over the cached samples (A + M post; P + M_pre pre). Over every SUCCESSFUL tx that holds a WSOL-quoted PumpSwap buy_exact_quote_in / buy / sell instruction
(top-level or inner), count: txs; txs with >= 1 inner instruction of the fees program; per inner-fees-instruction: 8-byte discriminator (IDL name), data length, account
count, and whether the fee_config PDA and the fee_program id sit in its account list. Also the executor's passed fee accounts (position of fee_config / fee_program in the
amm instruction) are checked in s18 (position_checks); here only the CPI side. Structure only. Run with /data/mal/venv/bin/python."""
import collections, gzip, json, os, sys
from pathlib import Path
sys.argv = [sys.argv[0]]
exec(open("s06_parity.py").read().split("acc = dict(")[0])
from common import OUT
BASE = Path(os.environ.get("A3_BASE", "/data2/mal/scratch/a3-repin-1121"))
MIG = json.load(open(BASE / "raw" / "migrate_index.json"))
AMM = PID["pump_amm"]; FEES = PID["pump_fees"]; WSOL = td.WSOL_MINT
DISC = {"c62e1552b4d9e870": "buy_exact_quote_in", "66063d1201daebea": "buy", "33e685a4017f83ad": "sell"}
FEE_CONFIG = "5PHirr8joyTMp9JMm6nW7hNDVyEYdkzDqazxPD7RaTjx"
def txs(root, label):
    idx = json.load(open(root / "sample_index.json"))["a"]
    for p in sorted((root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        for tx in (blk or {}).get("transactions", []):
            if tx["transaction"]["signatures"][0] in idx: yield tx
    seen = set(idx)
    for row in MIG["post" if label == "post" else "pre"]:
        if row["sig"] in seen: continue
        pth = BASE / "raw" / "txs" / f"{row['sig']}.json.gz"
        if pth.exists():
            r_ = json.loads(gzip.open(pth).read())["r"]
            if r_: seen.add(row["sig"]); yield r_
res = {}
for label, root in (("post", BASE / "raw"), ("pre", BASE / "raw_pre")):
    n_tx = collections.Counter(); fee_ix = collections.Counter(); fee_ix_by_parent = collections.Counter(); fee_top = 0; amm_top_only_fees_in_tx = 0
    for tx in txs(root, label):
        meta = tx.get("meta") or {}
        if meta.get("err") is not None: continue
        keys = keys_of(tx); msg = tx["transaction"]["message"]
        top = msg["instructions"]; groups = meta.get("innerInstructions") or []
        def is_amm(ins):
            if ins["programIdIndex"] >= len(keys) or keys[ins["programIdIndex"]] != AMM: return None
            d = b58d(ins["data"]); acc = ins["accounts"]
            if d[:8].hex() in DISC and len(acc) > 4 and keys[acc[4]] == WSOL: return DISC[d[:8].hex()]
            return None
        kinds = set()
        for i, ins in enumerate(top):
            k = is_amm(ins)
            if k: kinds.add(k)
        for g in groups:
            for ins in g["instructions"]:
                k = is_amm(ins)
                if k: kinds.add(k)
        if not kinds: continue
        key = "+".join(sorted(kinds)); n_tx[key] += 1
        has = False
        for ins in top:
            if keys[ins["programIdIndex"]] == FEES: fee_top += 1
        for g in groups:
            # parent kind: top-level instruction at g["index"]
            par = top[g["index"]] if g["index"] < len(top) else None
            pk = "amm" if par is not None and keys[par["programIdIndex"]] == AMM else "other"
            for ins in g["instructions"]:
                if keys[ins["programIdIndex"]] != FEES: continue
                has = True; d = b58d(ins["data"]); acc = [keys[a] for a in ins["accounts"]]
                nm = INS.get((FEES, d[:8]), "unknown:" + d[:8].hex())
                fee_ix[f"{nm}|{d[:8].hex()}|len={len(d)}|accts={len(acc)}|fee_config_in={FEE_CONFIG in acc}|parent={pk}"] += 1
        n_tx["_with_inner_fees_ix"] += int(has)
    res[label] = {"txs_with_wsol_amm_buy_sell": {k: v for k, v in n_tx.items() if not k.startswith("_")}, "n_txs": sum(v for k, v in n_tx.items() if not k.startswith("_")),
                  "txs_with_inner_fees_ix": n_tx["_with_inner_fees_ix"], "inner_fees_instruction_shapes": dict(sorted(fee_ix.items(), key=lambda kv: -kv[1])), "top_level_fees_ix_in_those_txs": fee_top}
Path(os.environ.get("UPGRADE_OUT", "out") + "/s27_fees_cpi.json").write_text(json.dumps(res, indent=1, sort_keys=True))
for l in res: print(l, res[l]["n_txs"], res[l]["txs_with_inner_fees_ix"], json.dumps(res[l]["inner_fees_instruction_shapes"])[:600])
