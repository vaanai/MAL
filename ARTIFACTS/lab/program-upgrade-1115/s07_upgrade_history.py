"""Write enumeration for BOTH redeployed programs' programdata accounts (pump B5Mv..., PumpSwap 6naE...) over [2026-11-15T07:00:00Z, now],
QP-RULING-1114 Q1 item 10 narrowed range (the 07:10:06Z monitor record saw both at their pins, so no write precedes it). getSignaturesForAddress paged back
with `before` until a signature with blockTime < 2026-11-15T07:00:00Z is seen. Every successful signature in the window is fetched whole (getTransaction) and
classified: the programdata account writable AND the BPF Upgradeable Loader called at top level or as an inner instruction (Squads CPI included) = an Upgrade
candidate; the loader instruction's data tag is recorded (3 = Upgrade, 0 InitializeBuffer, 1 Write, 4 SetAuthority, 5 Close ...). Public RPC, 0 credits."""
import json, time, calendar
from common import LClient, OUT, m
START = calendar.timegm(time.strptime("2026-11-15T07:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
LOADER = "BPFLoaderUpgradeab1e11111111111111111111111"
PD = {"pump": "B5MvUwXdiW1NMM6QFFD3ssPKBujD4zMohncbM73Z2BQu", "pumpswap": "6naEzKeUuFh1Jeeu51NXQgr5qkXgXtc9WKNct4xynVJc"}
c = LClient(max_calls=600, min_interval=0.8)
A58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
def b58d(s):
    n = 0
    for ch in s: n = n * 58 + A58.index(ch)
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(s) - len(s.lstrip("1"))) + b
res = {"start_utc": "2026-11-15T07:00:00Z", "start_epoch": START}
for name, addr in PD.items():
    sigs, before, complete, pages = [], None, False, 0
    while pages < 40:
        opts = {"limit": 1000, "commitment": "finalized"}
        if before: opts["before"] = before
        r = c.call("getSignaturesForAddress", [addr, opts]); pages += 1
        if not r: complete = True; break
        sigs += r; before = r[-1]["signature"]
        if (r[-1].get("blockTime") or 0) < START: complete = True; break
    win = [s for s in sigs if (s.get("blockTime") or 0) >= START]
    ok = sorted((s for s in win if s.get("err") is None), key=lambda s: s["slot"])
    rows = []
    for s in ok:
        t = c.call("getTransaction", [s["signature"], {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 1}])
        if not t: rows.append({"slot": s["slot"], "sig": s["signature"], "null": True}); continue
        msg = t["transaction"]["message"]; la = t["meta"].get("loadedAddresses") or {}
        keys = msg["accountKeys"] + la.get("writable", []) + la.get("readonly", [])
        h = msg["header"]; n = len(msg["accountKeys"]); i = keys.index(addr) if addr in keys else -1
        if i < 0: wr = None
        elif i < n: wr = i < h["numRequiredSignatures"] - h["numReadonlySignedAccounts"] or (h["numRequiredSignatures"] <= i < n - h["numReadonlyUnsignedAccounts"])
        else: wr = (i - n) < len(la.get("writable", []))
        ixs = [("top", ix) for ix in msg["instructions"]] + [("inner", ix) for g in (t["meta"].get("innerInstructions") or []) for ix in g["instructions"]]
        ld = [(w, ix) for w, ix in ixs if keys[ix["programIdIndex"]] == LOADER]
        tags = []
        for w, ix in ld:
            try: tags.append([w, b58d(ix["data"])[:4].hex()])
            except Exception: tags.append([w, "?"])
        rows.append({"slot": s["slot"], "utc": time.strftime("%FT%TZ", time.gmtime(s["blockTime"])), "sig": s["signature"], "pd_writable": wr,
                     "loader_ixs": tags, "upgrade_candidate": bool(wr and any(tg[1] == "03000000" for tg in tags)),
                     "progs": sorted({keys[ix["programIdIndex"]][:8] for w, ix in ixs})})
        print(name, json.dumps(rows[-1]), flush=True)
    res[name] = {"address": addr, "pages": pages, "n_sigs_fetched": len(sigs), "oldest_blockTime_fetched": sigs[-1].get("blockTime") if sigs else None,
                 "COMPLETE_back_to_start": complete, "n_in_window": len(win), "n_failed_in_window": len(win) - len(ok),
                 "successful_in_window": rows, "upgrades": [r for r in rows if r.get("upgrade_candidate")]}
res["rpc_attempts"] = c.total_calls
(OUT / "s07_upgrade_history.json").write_text(json.dumps(res, indent=1))
for name in PD: print(name, "complete", res[name]["COMPLETE_back_to_start"], "in window", res[name]["n_in_window"], "upgrades", [(u["slot"], u["utc"], u["sig"]) for u in res[name]["upgrades"]])
