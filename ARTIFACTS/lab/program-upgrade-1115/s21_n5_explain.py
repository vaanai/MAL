"""Explain the N5 cases 'trade event present, 0 recognised trade instruction' (post 14 of 19,044 txs, pre 3 of 8,264): for each, which instruction names of pump / PumpSwap / fees
(top-level and inner) the tx carries and the program of the event. Offline from the cached blocks. Structure only."""
import collections, gzip, json, sys, os
from pathlib import Path
sys.argv = [sys.argv[0]]
exec(open("s06_parity.py").read().split("acc = dict(")[0])
BASE = Path(os.environ.get("A3_BASE", "/data2/mal/scratch/a3-repin-1115")); out = {}
for label, root, f in (("post", "raw", os.environ.get("UPGRADE_OUT", "out") + "/s06_parity.json"), ("pre", "raw_pre", os.environ.get("UPGRADE_OUT", "out") + "/s06_parity_pre.json")):
    d = json.load(open(f)); want = {(x["slot"]) for x in d["nondecode"] if x["cat"] == "N5" and not x.get("explained_by_cap") and x.get("trade_ix") == 0}
    rows = []
    for slot in sorted(want):
        blk = json.loads(gzip.open(BASE / root / "blocks" / f"{slot}.json.gz").read())["r"]
        for tx in blk["transactions"]:
            meta = tx.get("meta") or {}
            if meta.get("err") is not None: continue
            evs = [(PNAME.get(p), EV_BY_PROG[p].get(b[:8])) for p, b in log_events(meta.get("logMessages") or []) if p in PNAME and len(b) >= 8]
            if not any(n in ("TradeEvent", "BuyEvent", "SellEvent") for _, n in evs): continue
            names = [f"{PNAME[p]}.{INS.get((p, dd[:8]), dd[:8].hex())}" for p, dd, w in instructions(tx)]
            if any(is_trade_ix(n.split('.', 1)[1]) for n in names if n.startswith(("pump.", "pump_amm."))): continue
            rows.append({"slot": slot, "events": sorted({f"{p}.{n}" for p, n in evs if n in ("TradeEvent", "BuyEvent", "SellEvent")}), "ix": sorted(set(names))})
    out[label] = {"n": len(rows), "by_ix_set": dict(collections.Counter(json.dumps(r["ix"]) + " <- " + json.dumps(r["events"]) for r in rows))}
Path(os.environ.get("UPGRADE_OUT", "out") + "/s21_n5_explain.json").write_text(json.dumps(out, indent=1)); print(json.dumps(out, indent=1))
