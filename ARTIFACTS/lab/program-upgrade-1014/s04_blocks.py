"""Step 4: the lab's walker decoder (tools/pump_history_backfill.rows_from_block, event_v=True) on blocks before and after the
pump redeploy (slot 456494698), plus BondingCurve account sizes of curves created in those blocks.

Groups: pre = the 6 blocks before the deploy slot; post = the 6 blocks from deploy+2 (a program deployed in slot S is visible
from S+1; deploy+1 skipped to be safe); recent = 6 blocks near slot 456753000 (about 07:20Z). Skipped slots are passed over.
Counts only: no price, no P&L, no trade sizes are recorded.
"""
import json
from collections import Counter

from common import DEPLOY_SLOT, client, m, write_json
import tools.pump_history_backfill as W

c = client(max_calls=60)
PUMP = m.PUMP_PROGRAM
CREATE, TRADE, COMPLETE, MIGRATE = W._CREATE_DISC, W._TRADE_DISC_BYTES, W._COMPLETE_DISC, W._MIGRATE_DISC


def getblock(slot):
    return c.call("getBlock", [slot, {"encoding": "json", "transactionDetails": "full", "rewards": False, "maxSupportedTransactionVersion": 1, "commitment": "finalized"}])


def pick(start, step, n, span=14):
    out, s = [], start
    while len(out) < n and abs(s - start) < span:
        b = getblock(s)
        if b:
            b["slot"] = s
            out.append(b)
        s += step
    return out


groups = {"pre": pick(DEPLOY_SLOT - 1, -1, 6), "post": pick(DEPLOY_SLOT + 2, 1, 6), "recent": pick(456753000, 1, 6)}
res, curves = {}, {}
for g, blocks in groups.items():
    pool_mints = {}
    agg = Counter()
    ixn, lens, quote = Counter(), {k.hex(): Counter() for k in (CREATE, TRADE, COMPLETE, MIGRATE)}, Counter()
    unknown, bad, cpi = Counter(), Counter(), Counter()
    curves[g] = []
    for b in blocks:
        r = W.rows_from_block(b, pool_mints, event_v=True)
        agg["blocks"] += 1
        agg["trade_rows"] += len(r["trades"])
        agg["trade_rows_pump_bonding"] += sum(1 for t in r["trades"] if t.get("venue") == "pump_bonding")
        agg["trade_rows_pumpswap"] += sum(1 for t in r["trades"] if t.get("venue") == "pumpswap")
        agg["create_rows"] += len(r["creates"])
        agg["migration_rows"] += len(r["migrations"])
        agg["unresolved"] += len(r["unresolved"])
        for t in r["creates"]:
            quote["create_quote_wsol" if t.get("quote_mint") == m.WSOL_MINT else "create_quote_other"] += 1
            agg["create_mayhem"] += int(bool(t.get("is_mayhem_mode")))
        for t in r["migrations"]:
            quote["migration_quote_wsol" if t.get("quote_mint") == m.WSOL_MINT else "migration_quote_other"] += 1
        unknown.update(r["unknown_disc"])
        bad.update(r["bad_disc"])
        cpi.update(r["cpi_disc"])
        for tx in b["transactions"]:
            if tx["meta"]["err"] is not None:
                continue
            keys = m.tx_keys(tx)
            if PUMP not in keys:
                continue
            agg["pump_txs"] += 1
            ixn.update(m.program_instructions(m.tx_logs(tx), PUMP))
            for blob in m.tx_event_blobs(tx):
                d8 = blob[:8]
                if d8 in (CREATE, TRADE, COMPLETE, MIGRATE):
                    lens[d8.hex()][len(blob)] += 1
                if d8 == CREATE:
                    agg["create_blobs"] += 1
                    ev = W.decode_create_event(blob)
                    agg["create_blobs_decoded"] += int(ev is not None)
                    if ev is not None:
                        off = 8
                        for _ in range(3):
                            _s, off = W._read_string(blob, off)
                        curves[g].append(m.b58encode(blob[off + 32 : off + 64]))
                if d8 == TRADE:
                    agg["trade_blobs"] += 1
                if d8 == MIGRATE:
                    agg["migrate_blobs"] += 1
                    agg["migrate_blobs_decoded"] += int(W.decode_migration_event(blob) is not None)
                if d8 == COMPLETE:
                    agg["complete_blobs"] += 1
                    agg["complete_blobs_decoded"] += int(W.decode_complete_event(blob) is not None)
    res[g] = dict(slots=[b["slot"] for b in blocks], counts=dict(agg), quote=dict(quote), pump_instruction_names=dict(ixn.most_common(40)),
                  event_blob_lengths={k: dict(v) for k, v in lens.items() if v}, unknown_disc=dict(unknown), bad_disc=dict(bad), cpi_disc=dict(cpi))
    print(g, json.dumps({k: res[g][k] for k in ("slots", "counts", "quote", "bad_disc", "unknown_disc")}))

# BondingCurve account sizes (space) for the curves created in the sample blocks
for g in ("pre", "post", "recent"):
    cs = sorted(set(curves[g]))[:100]
    if not cs:
        res[g]["curve_space"] = {}
        continue
    r = c.call("getMultipleAccounts", [cs, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}, "commitment": "finalized"}])
    vals = r["value"]
    res[g]["curve_space"] = dict(n_curves=len(cs), n_missing=sum(1 for v in vals if v is None), space=dict(Counter(v["space"] for v in vals if v)), ctx_slot=r["context"]["slot"])
    print(g, "curve_space", res[g]["curve_space"])
write_json("s04_blocks.json", res)
