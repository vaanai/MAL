"""The pump_fees unknown discriminators (event 83a3d1707b8569eb, instruction 228edb706d368517) seen by s06 in BOTH post and pre samples: which txs carry them,
the top-level programs and the program id of the caller (the instruction whose CPI reaches pump_fees), the fees-program invoke count per tx, and whether pump is in the tx. Offline. Structure only."""
import collections, gzip, json, os, sys
from pathlib import Path
sys.argv = [sys.argv[0]]
exec(open("s06_parity.py").read().split("acc = dict(")[0])
from common import OUT
BASE = Path("/data2/mal/scratch/a3-repin-1128"); res = {}
FEES = PID["pump_fees"]
for label, root in (("post", "raw"), ("pre", "raw_pre")):
    n = 0; top = collections.Counter(); caller = collections.Counter(); has_pump = 0; has_amm = 0; dl = collections.Counter(); first_slot = None; n_txs_all = 0
    for p in sorted((BASE / root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        for tx in (blk or {}).get("transactions", []):
            meta = tx.get("meta") or {}
            if meta.get("err") is not None: continue
            keys = keys_of(tx); msg = tx["transaction"]["message"]; found = False
            for pid, d, w in instructions(tx):
                if pid == FEES and d[:8].hex() == "228edb706d368517":
                    found = True; dl[(w, len(d))] += 1
            for gi, ins in enumerate(msg["instructions"]):
                if keys[ins["programIdIndex"]] == FEES and b58d(ins["data"])[:8].hex() == "228edb706d368517": caller["TOP-LEVEL"] += 1
            if found:
                n += 1; top.update({keys[i["programIdIndex"]][:8] for i in msg["instructions"]}); has_pump += PID["pump"] in keys; has_amm += PID["pump_amm"] in keys
    res[label] = {"txs_with_fees_228edb70_ix": n, "top_level_program_prefixes": dict(top), "top_level_caller_prefix_of_the_cpi": dict(caller), "tx_contains_pump": has_pump, "tx_contains_pump_amm": has_amm, "ix_where_and_data_len": {str(k): v for k, v in dl.items()}}
(OUT / "s40_unknown_fees_disc.json").write_text(json.dumps(res, indent=1)); print(json.dumps(res, indent=1))
