"""Item (c1), failed transactions: the sampled blocks hold ALL transactions of the block (the s05 index keeps only successful pump/PumpSwap ones), so failed PumpSwap
buy / buy_exact_quote_in / sell transactions are counted here from the cached blocks, by error word, post (A1) vs pre (P). WSOL-quoted (account 4 == WSOL), top-level or inner.
Share = failed / (failed + successful) for the same instruction class. Offline. Structure only."""
import collections, gzip, json, sys
from pathlib import Path
sys.argv = [sys.argv[0]]
src = open("s18_instruction_shapes.py").read()
exec(src.split("res = {}; own_inputs")[0])
out = {}
for label, root in (("post", BASE / "raw"), ("pre", BASE / "raw_pre")):
    ok = collections.Counter(); bad = collections.Counter(); words = collections.defaultdict(collections.Counter)
    for p in sorted((root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        for tx in (blk or {}).get("transactions", []):
            meta = tx.get("meta") or {}
            for where, d, acc in amm_ixs(tx):
                if len(acc) < 5 or acc[4] != WSOL: continue
                cls = DISC[d[:8].hex()]
                if cls == "buy": continue
                if meta.get("err") is None: ok[cls] += 1
                else: bad[cls] += 1; words[cls][errword(meta["err"])] += 1
    out[label] = {c: {"success": ok[c], "failed": bad[c], "failed_share": (bad[c] / (ok[c] + bad[c])) if ok[c] + bad[c] else None, "words": dict(words[c])} for c in ("buy_exact_quote_in", "sell")}
out["note"] = "a transaction counts once per instruction; a failed tx counts every matching instruction it carries"
Path("out/s20_failed_shape_txs.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
