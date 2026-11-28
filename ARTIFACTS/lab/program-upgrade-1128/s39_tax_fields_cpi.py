"""QP-RULING-1128 A extras, T4 (tax fields in A and M), T6 (CPI program set) and the create variants. Offline over cached blocks (A or P) and M / M_pre txs.
Usage: SAMPLE=post python3 s39_tax_fields_cpi.py  (post: new pump IDL walker; SAMPLE=pre: old IDL). Structure only: counts and zero tests; no price, no amount is saved."""
import collections, gzip, json, os, sys
from pathlib import Path
sys.argv = [sys.argv[0]]
exec(open("s06_parity.py").read().split("acc = dict(")[0])
from common import OUT
BASE = Path(os.environ.get("A3_BASE", "/data2/mal/scratch/a3-repin-1128"))
MODE = os.environ.get("SAMPLE", "post"); root = BASE / ("raw" if MODE == "post" else "raw_pre")
MIG = json.load(open(BASE / "raw" / "migrate_index.json"))["post" if MODE == "post" else "pre"]
idx = json.load(open(root / "sample_index.json"))["a"]
def txs():
    seen = set()
    for p in sorted((root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        for tx in (blk or {}).get("transactions", []):
            s = tx["transaction"]["signatures"][0]
            if s in idx: seen.add(s); yield "A", tx
    for r in MIG:
        if r["sig"] in seen: continue
        pth = BASE / "raw" / "txs" / f"{r['sig']}.json.gz"
        if pth.exists():
            t = json.loads(gzip.open(pth).read())["r"]
            if t: yield "M", t
cnt = collections.Counter(); cpi = collections.Counter(); cre = collections.Counter(); ixn = collections.Counter(); unk = collections.Counter()
for src, tx in txs():
    meta = tx.get("meta") or {}
    if meta.get("err") is not None: continue
    keys = keys_of(tx); msg = tx["transaction"]["message"]
    for pid, blob in log_events(meta.get("logMessages") or []):
        if PNAME.get(pid) != "pump" or len(blob) < 8: continue
        nm = EV_BY_PROG[pid].get(blob[:8])
        if nm is None: unk[blob[:8].hex()] += 1; continue
        try: ev, end = parse_event("pump", nm, blob)
        except Short: cnt[f"{src}|{nm}|short"] += 1; continue
        cnt[f"{src}|{nm}"] += 1
        if nm == "TradeEvent":
            cnt[f"{src}|TradeEvent|{'buy' if ev['is_buy'] else 'sell'}"] += 1
            for f in ("block0_fee", "quote_transfer_fee"):
                if f in ev: cnt[f"{src}|TradeEvent|{f}|n"] += 1; cnt[f"{src}|TradeEvent|{f}|nonzero"] += int(ev[f] != 0)
            if "ix_name" in ev: ixn[ev["ix_name"]] += 1
            cnt[f"{src}|TradeEvent|trailing{len(blob) - end}"] += 1
        if nm == "PostCompleteBuyEvent" and "block0_fee" in ev: cnt[f"{src}|PostCompleteBuyEvent|block0_fee_nonzero"] += int(ev["block0_fee"] != 0)
        if nm == "CreateEvent": cre[f"mayhem={ev.get('is_mayhem_mode')}|cashback={ev.get('is_cashback_enabled')}|quote_wsol={ev.get('quote_mint') in (None, '11111111111111111111111111111111', td.WSOL_MINT)}"] += 1
    # CPI program set under pump top-level instructions
    for gi, ins in enumerate(msg["instructions"]):
        if keys[ins["programIdIndex"]] != PID["pump"]: continue
        d = b58d(ins["data"]); ixn_name = INS.get((PID["pump"], d[:8]))
        cnt[f"{src}|pump_top_ix|{ixn_name or 'UNKNOWN:' + d[:8].hex()}"] += 1
        for g in meta.get("innerInstructions") or []:
            if g["index"] == gi:
                for i2 in g["instructions"]: cpi[keys[i2["programIdIndex"]]] += 1
res = {"mode": MODE, "counts": dict(cnt), "unknown_pump_event_disc": dict(unk), "trade_ix_name": dict(ixn), "create_variants": dict(cre), "cpi_programs_under_pump_top_level_ix": dict(cpi)}
(OUT / f"s39_tax_fields_cpi_{MODE}.json").write_text(json.dumps(res, indent=1, sort_keys=True))
print(json.dumps(res, indent=1)[:4000])
