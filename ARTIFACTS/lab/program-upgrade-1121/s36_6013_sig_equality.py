"""QP-RULING-1121a section 4 carried item 1: the 3 `sell` 6013 rows of s20 are the same 3 transactions as the 3 buy rows. Signature equality over the cached A blocks, count only.
Run: /data/mal/venv/bin/python s36_6013_sig_equality.py"""
import gzip, json, sys, os
from pathlib import Path
sys.argv = [sys.argv[0]]
src = open("s18_instruction_shapes.py").read(); exec(src.split("res = {}; own_inputs")[0])
S = {"buy_exact_quote_in": set(), "sell": set()}
for p in sorted((BASE / "raw" / "blocks").glob("*.json.gz")):
    blk = json.loads(gzip.open(p).read())["r"]
    for tx in (blk or {}).get("transactions", []):
        meta = tx.get("meta") or {}
        if meta.get("err") is None or errword(meta["err"]) != "InstructionError:Custom=6013": continue
        for where, d, acc in amm_ixs(tx):
            if len(acc) < 5 or acc[4] != WSOL: continue
            cls = DISC[d[:8].hex()]
            if cls in S: S[cls].add(tx["transaction"]["signatures"][0])
res = {"n_buy_tx": len(S["buy_exact_quote_in"]), "n_sell_tx": len(S["sell"]), "n_equal_signatures": len(S["buy_exact_quote_in"] & S["sell"]), "sell_not_in_buy": len(S["sell"] - S["buy_exact_quote_in"]), "buy_not_in_sell": len(S["buy_exact_quote_in"] - S["sell"])}
Path("out/s36_6013_sig_equality.json").write_text(json.dumps(res, indent=1)); print(res)
