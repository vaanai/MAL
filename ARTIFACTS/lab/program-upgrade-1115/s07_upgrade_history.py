"""Upgrade-write enumeration of the pump programdata account (B5MvUwXdiW1NMM6QFFD3ssPKBujD4zMohncbM73Z2BQu) between slot 458921440 (exclusive)
and now, QP-RULING-1021-repin Q4 (every write since the pin, not only the last). getSignaturesForAddress paged back with `before`
until a signature at or before slot 458921440 is seen or the page is empty. Public RPC, 0 credits. If paging cannot reach the pin the result says
COMPLETE=false (NOT OBSERVED). Errors (failed txs) are listed separately; only err=None are successful writes.
Also: the same for the PumpSwap and fees programdata (informational), and block times of every write."""
import json
from common import LClient, OUT
PIN_SLOT = 458921440
PD = {"pump": "B5MvUwXdiW1NMM6QFFD3ssPKBujD4zMohncbM73Z2BQu"}
c = LClient(max_calls=200, min_interval=1.0)
res = {"pin_slot": PIN_SLOT}
for name, addr in PD.items():
    sigs, before, complete, pages = [], None, False, 0
    while pages < 60:
        opts = {"limit": 1000, "commitment": "finalized"}
        if before: opts["before"] = before
        try:
            r = c.call("getSignaturesForAddress", [addr, opts])
        except Exception as e:
            res.setdefault("errors", []).append(f"{name} page {pages}: {type(e).__name__}: {e}"); break
        pages += 1
        if not r:
            complete = True; break      # history exhausted
        sigs += r; before = r[-1]["signature"]
        if r[-1]["slot"] <= PIN_SLOT:
            complete = True; break
    ok = [s for s in sigs if s.get("err") is None and s["slot"] > PIN_SLOT]
    failed = [s for s in sigs if s.get("err") is not None and s["slot"] > PIN_SLOT]
    res[name] = {"address": addr, "pages": pages, "n_sigs_fetched": len(sigs), "oldest_slot_fetched": sigs[-1]["slot"] if sigs else None, "COMPLETE_back_to_pin": complete,
                 "successful_after_pin": [{"slot": s["slot"], "blockTime": s.get("blockTime"), "sig": s["signature"]} for s in sorted(ok, key=lambda s: s["slot"])],
                 "n_failed_after_pin": len(failed), "failed_after_pin_slots": sorted(s["slot"] for s in failed)[:50],
                 "at_or_before_pin": [{"slot": s["slot"], "blockTime": s.get("blockTime"), "err": s.get("err")} for s in sigs if s["slot"] <= PIN_SLOT][:5]}
    print(name, json.dumps({k: v for k, v in res[name].items() if k != "failed_after_pin_slots"}, indent=0)[:2500])
(OUT / "s07_upgrade_history.json").write_text(json.dumps(res, indent=1))
