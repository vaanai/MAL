"""Classify signatures of the pump programdata account (from s07): is the programdata account WRITABLE in the tx, and which programs do its
instructions call? An upgrade write is a tx whose instruction calls the BPF Upgradeable Loader (BPFLoaderUpgradeab1e11111111111111111111111) with the
programdata writable. Modes: `sample` (a spread of ~14 txs) or `all-late` (every successful sig at slot >= 469000000, the neighbourhood of the 469055660 write).
Public RPC, 0 credits. Structure only."""
import json, sys, time
from common import LClient, OUT
mode = sys.argv[1] if len(sys.argv) > 1 else "sample"
LOADER = "BPFLoaderUpgradeab1e11111111111111111111111"
PD = "B5MvUwXdiW1NMM6QFFD3ssPKBujD4zMohncbM73Z2BQu"
ok = json.load(open("out/s07_upgrade_history.json"))["pump"]["successful_after_pin"]
if mode == "sample":
    idx = sorted({0, 30, 100, 400, 800, 1200, 1600, 2000, 2400, len(ok) - 8, len(ok) - 7, len(ok) - 1} | {i for i, s in enumerate(ok) if s["slot"] == 469055660})
    pick = [ok[i] for i in idx if 0 <= i < len(ok)]
elif mode == "all":
    pick = list(ok)
else:
    pick = [s for s in ok if s["slot"] >= 469000000]
c = LClient(max_calls=4000, min_interval=1.2 if mode == "all" else 0.8)
rows = []
for s in pick:
    t = c.call("getTransaction", [s["sig"], {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 1}])
    if not t:
        rows.append({"slot": s["slot"], "sig": s["sig"], "null": True}); continue
    msg = t["transaction"]["message"]; la = t["meta"].get("loadedAddresses") or {}
    keys = msg["accountKeys"] + la.get("writable", []) + la.get("readonly", [])
    h = msg["header"]; n = len(msg["accountKeys"]); i = keys.index(PD) if PD in keys else -1
    if i < 0: wr = None
    elif i < n: wr = i < h["numRequiredSignatures"] - h["numReadonlySignedAccounts"] or (h["numRequiredSignatures"] <= i < n - h["numReadonlyUnsignedAccounts"])
    else: wr = (i - n) < len(la.get("writable", []))
    progs = [keys[ix["programIdIndex"]] for ix in msg["instructions"]]
    inner = [keys[ix["programIdIndex"]] for g in (t["meta"].get("innerInstructions") or []) for ix in g["instructions"]]
    rows.append({"slot": s["slot"], "utc": time.strftime("%FT%TZ", time.gmtime(s["blockTime"])), "sig": s["sig"], "pd_writable": wr, "calls_loader": LOADER in progs, "inner_loader": LOADER in inner,
                 "progs": sorted({p[:8] for p in progs}), "inner_progs": sorted({p[:8] for p in inner}), "n_instr": len(progs), "fee_payer": keys[0][:8]})
    print(json.dumps(rows[-1]), flush=True)
(OUT / f"s09_classify_{mode}.json").write_text(json.dumps(rows, indent=1))
