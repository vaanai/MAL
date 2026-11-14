"""QP ruling 384d99f3 item 9(i): the mayhem flag. s06's value-containment compare skips booleans, so compare them explicitly:
frozen `bf.decode_create_event(blob)` (is_mayhem_mode) vs the independent IDL parse (is_mayhem_mode, is_cashback_enabled) on every CreateEvent of
samples A, P, P2; and the independent is_mayhem_mode of every CreatePoolEvent (frozen td.decode_program_data has no mayhem key: stated, not compared).
Offline over the cached blocks. Structure only."""
import collections, gzip, json, os, sys
from pathlib import Path
sys.argv = [sys.argv[0]]
exec(open("s06_parity.py").read().split("acc = dict(")[0])
BASE = Path("/data2/mal/scratch/a3-repin-1114")
res = {}
for label, root in (("post", "raw"), ("pre_P", "raw_pre"), ("pre_P2", "raw_pre2")):
    c = collections.Counter(); mism = []
    for p in sorted((BASE / root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        if not blk: continue
        for tx in blk["transactions"]:
            meta = tx.get("meta") or {}
            if meta.get("err") is not None: continue
            for pid, blob in log_events(meta.get("logMessages") or []):
                if pid not in PNAME or len(blob) < 8: continue
                nm = EV_BY_PROG[pid].get(blob[:8])
                if nm == "CreateEvent":
                    ev, _ = parse_event("pump", nm, blob); fro = bf.decode_create_event(blob)
                    c["CreateEvent"] += 1
                    c["mayhem_true_indep"] += bool(ev["is_mayhem_mode"])
                    if fro is None: c["frozen_none"] += 1; continue
                    if bool(fro.get("is_mayhem_mode")) != bool(ev["is_mayhem_mode"]): c["mayhem_mismatch"] += 1; mism.append(tx["transaction"]["signatures"][0])
                    for k_f, k_i in (("mint", "mint"), ("creator", "creator"), ("bonding_curve", "bonding_curve")):
                        if k_f in fro and fro[k_f] != ev[k_i]: c[f"{k_f}_mismatch"] += 1
                elif nm == "CreatePoolEvent":
                    ev, _ = parse_event("pump_amm", nm, blob); c["CreatePoolEvent"] += 1; c["pool_mayhem_true_indep"] += bool(ev["is_mayhem_mode"])
    res[label] = {"counts": dict(c), "mismatching_sigs": mism[:10]}
(Path("out") / "s14_mayhem_parity.json").write_text(json.dumps(res, indent=1))
print(json.dumps(res, indent=1))
