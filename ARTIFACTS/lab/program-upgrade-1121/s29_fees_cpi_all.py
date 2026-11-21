"""F4 report-only extension (QP-RULING-1121 F4: pump's bonding-curve pfee CPIs, and the origin of the one post-only inner pfee shape found by s27).
Over EVERY successful sampled tx (A post, P pre; the same cached blocks as s27, no migrate txs): every inner pfee instruction keyed by
(name, 8-byte disc, data len, account count, fee_config_in, parent program of the top-level instruction, direct parent of the pfee ix = first non-pfee instruction
of the inner group that precedes it, by program). Structure only. Run with /data/mal/venv/bin/python."""
import collections, gzip, json, os, sys
from pathlib import Path
sys.argv = [sys.argv[0]]
exec(open("s06_parity.py").read().split("acc = dict(")[0])
BASE = Path(os.environ.get("A3_BASE", "/data2/mal/scratch/a3-repin-1121"))
FEES = PID["pump_fees"]; FEE_CONFIG = "5PHirr8joyTMp9JMm6nW7hNDVyEYdkzDqazxPD7RaTjx"
def pn(k): return PNAME.get(k, k[:8])
res = {}
for label, root in (("post", BASE / "raw"), ("pre", BASE / "raw_pre")):
    idx = json.load(open(root / "sample_index.json"))["a"]; c = collections.Counter(); ex = {}
    for p in sorted((root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        for tx in (blk or {}).get("transactions", []):
            sg = tx["transaction"]["signatures"][0]
            if sg not in idx: continue
            keys = keys_of(tx); meta = tx["meta"]; top = tx["transaction"]["message"]["instructions"]
            for g in meta.get("innerInstructions") or []:
                tp = pn(keys[top[g["index"]]["programIdIndex"]]) if g["index"] < len(top) else "?"
                prev = None
                for ins in g["instructions"]:
                    k = keys[ins["programIdIndex"]]
                    if k == FEES:
                        d = b58d(ins["data"]); acc = [keys[a] for a in ins["accounts"]]
                        key = f"{INS.get((FEES, d[:8]), 'unknown')}|{d[:8].hex()}|len={len(d)}|accts={len(acc)}|fee_config_in={FEE_CONFIG in acc}|top={tp}|inner_prev={prev}"
                        c[key] += 1; ex.setdefault(key, sg[:8])
                    prev = pn(k)
    res[label] = {"shapes": dict(sorted(c.items(), key=lambda kv: -kv[1])), "example_sig_prefix": ex}
Path(os.environ.get("UPGRADE_OUT", "out") + "/s29_fees_cpi_all.json").write_text(json.dumps(res, indent=1))
for l in res:
    print(l)
    for k, v in res[l]["shapes"].items(): print("  ", v, k)
