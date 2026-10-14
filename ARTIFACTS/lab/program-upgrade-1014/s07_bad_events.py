"""Step 7 (cache only, no new RPC): which trade-disc events did the walker count in bad_disc in step 4, and why.
Same rule as tools/pump_history_backfill.rows_from_block: an event whose discriminator is Trade/Buy/Sell (log line), for which
decode_program_data(raw, event_v=True) is None or not _event_v_complete. Prints blob length, reason, and whether the tx holds a
MultiHopSwap / v2 / v3 instruction name. Counts only."""
import json
from collections import Counter

from common import DEPLOY_SLOT, client, m, write_json
import tools.pump_history_backfill as W
from observe.trade_decode import decode_program_data

c = client(max_calls=1)  # every block below is a cache hit; a miss would be a real call and would raise on the cap of 1 after one attempt
SLOTS = {"pre": range(DEPLOY_SLOT - 6, DEPLOY_SLOT), "post": range(DEPLOY_SLOT + 2, DEPLOY_SLOT + 8), "recent": range(456753000, 456753006)}
res = {}
for g, rng in SLOTS.items():
    n_ev, bad = Counter(), []
    for s in rng:
        b = c.call("getBlock", [s, {"encoding": "json", "transactionDetails": "full", "rewards": False, "maxSupportedTransactionVersion": 1, "commitment": "finalized"}])
        if not b:
            continue
        for tx in b["transactions"]:
            if tx["meta"]["err"] is not None:
                continue
            logs = tx["meta"].get("logMessages") or []
            names = set(m.program_instructions(logs, m.PUMPSWAP_PROGRAM)) | set(m.program_instructions(logs, m.PUMP_PROGRAM))
            for line in logs:
                raw = W._program_data(line) if isinstance(line, str) else None
                if raw is None or raw[:8] not in W._TRADE_DISCS:
                    continue
                n_ev[raw[:8].hex()] += 1
                row_v = decode_program_data(raw, event_v=True)
                if row_v is None or not W._event_v_complete(raw[:8], row_v):
                    bad.append(dict(disc=raw[:8].hex(), len=len(raw), reason="decode_none" if row_v is None else "event_v_incomplete", ix=sorted(names)[:6]))
    res[g] = dict(n_events_by_disc=dict(n_ev), n_bad=len(bad), bad=bad)
    print(g, dict(n_ev), "bad", len(bad), Counter((x["disc"], x["len"], x["reason"]) for x in bad))
write_json("s07_bad_events.json", res)
