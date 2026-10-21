"""Upgrade history of the pump and PumpSwap programdata accounts (getSignaturesForAddress, public RPC) and a bisection of the slot where the
8 trailing bytes on Trade/Buy/Sell events first appear (IDL walk of one event per probe block). Counts and slots only."""
import json, os, sys
from common import LClient, OUT
c = LClient(max_calls=300, min_interval=0.4)
PD = {"pump": "B5MvUwXdiW1NMM6QFFD3ssPKBujD4zMohncbM73Z2BQu", "pumpswap": "6naEzKeUuFh1Jeeu51NXQgr5qkXgXtc9WKNct4xynVJc"}
res = {"signatures": {}}
for k, a in PD.items():
    r = c.call("getSignaturesForAddress", [a, {"limit": 8, "commitment": "finalized"}])
    res["signatures"][k] = [{"slot": x["slot"], "blockTime": x.get("blockTime"), "err": x.get("err"), "sig": x["signature"]} for x in r]
    print(k, [(x["slot"], x["err"]) for x in r])
# bisection helper: reuse the parity module's walkers without running its main body
src = open("s06_parity.py").read().split("acc = dict(")[0]
os.environ.setdefault("UPGRADE_RAW", "/data2/mal/scratch/a3-repin-1021/raw_bisect")
sys.argv = [sys.argv[0]]
exec(src)
def probe(slot):
    """returns list of (event name, trailing bytes) for trade events in the first non-skipped block at or after slot"""
    s = slot
    for _ in range(40):
        b = c.call("getBlock", [s, {"encoding": "json", "transactionDetails": "full", "rewards": False, "maxSupportedTransactionVersion": 1, "commitment": "finalized"}])
        if b is not None:
            out = []
            for tx in b["transactions"]:
                m = tx.get("meta") or {}
                if m.get("err") is not None: continue
                for pid, blob in log_events(m.get("logMessages") or []):
                    if pid in PNAME and len(blob) >= 8:
                        nm = EV_BY_PROG[pid].get(blob[:8])
                        if nm in TRADE_EV:
                            try: ev, end = parse_event(PNAME[pid], nm, blob)
                            except Short: continue
                            out.append((nm, len(blob) - end))
            if out: return s, out
        s += 1
    return s, []
import time
def kinds(out):
    d = {}
    for nm, t in out: d.setdefault(nm, set()).add(t)
    return {k: sorted(v) for k, v in d.items()}
res["bisect"] = {}
for kind in ("TradeEvent", "BuyEvent", "SellEvent"):
    lo, hi = 458886396, 458890594   # lo: kind has 0 trailing bytes, hi: 8 (from the first coarse bisection)
    trace = []
    while hi - lo > 8:
        mid = (lo + hi) // 2
        s2, out = probe(mid)
        k = kinds([x for x in out if x[0] == kind]).get(kind)
        trace.append({"probe_slot": s2, "trailing": k})
        if k is None: lo = mid; continue
        if k == [0]: lo = s2
        elif 0 not in k: hi = s2
        else: lo, hi = s2, s2
        if lo >= hi: break
    res["bisect"][kind] = {"trace": trace, "range": [lo, hi]}
    print(kind, lo, hi, flush=True)
bt = {}
for sl in (458888140, 458921440):
    bt[sl] = c.call("getBlockTime", [sl])
res["block_time"] = bt
# successful signatures on the PumpSwap programdata account near the pump upgrade (1000 newest, filter err None)
r = c.call("getSignaturesForAddress", [PD["pumpswap"], {"limit": 1000, "commitment": "finalized"}])
res["pumpswap_pd_oldest_in_page"] = {"slot": r[-1]["slot"], "n": len(r), "n_ok": sum(1 for x in r if x["err"] is None)}
(OUT / "s07_upgrade_history.json").write_text(json.dumps(res, indent=1))
print(json.dumps({k: v for k, v in res.items() if k != "bisect"}, indent=0)[:1500])
