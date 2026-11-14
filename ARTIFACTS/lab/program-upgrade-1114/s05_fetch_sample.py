"""Fetch the PRE-DECLARED decode-parity sample (rule: ARTIFACTS/lab/a3-repin-2026-11-14-pump-program-upgrade-review.md section 3, committed before this ran).
CHAIN-ONLY: no tape, arena, book or ledger file is opened; anchors are slot numbers computed here. Public RPC, 0 Helius credits.
Raw responses cached gz under RAW/{blocks,txs}. Structure only: nothing is priced.

Run: python3 -I s05_fetch_sample.py          (sample A + C, post-redeploy; RAW = UPGRADE_RAW or /data2/mal/scratch/a3-repin-1114/raw)
     SAMPLE=pre UPGRADE_RAW=/data2/mal/scratch/a3-repin-1114/raw_pre python3 -I s05_fetch_sample.py   (control P)
"""
import gzip, json, os, sys
from common import RAW, OUT, LClient, m

PRE2 = os.environ.get("SAMPLE") == "pre2"   # added after QP ruling 384d99f3: pre control extension P2
PRE = os.environ.get("SAMPLE") in ("pre", "pre2")
DEPLOY = 469055660
PROGS = {"6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"}
(RAW / "blocks").mkdir(parents=True, exist_ok=True); (RAW / "txs").mkdir(parents=True, exist_ok=True)
c = LClient(max_calls=6000, min_interval=0.35)
MAXV = 1


def cached(kind, key, method, params):
    p = RAW / kind / f"{key}.json.gz"
    if p.exists():
        return json.loads(gzip.open(p).read())["r"]
    r = c.call(method, params)
    with gzip.open(p, "wt") as fh:
        json.dump({"r": r}, fh)
    return r


latest = int(os.environ["S05_LATEST"]) if os.environ.get("S05_LATEST") else c.call("getSlot", [{"commitment": "finalized"}])   # S05_LATEST only resumes a cached run with the same anchors (first run: 469263564)
if PRE2:
    n_anchor, lo, hi = 10, DEPLOY - 80000, DEPLOY - 41000    # P2: the 40,000 slots before P, so that P+P2 span 80,000 slots (about 8.9 h)
elif PRE:
    n_anchor, lo, hi = 10, DEPLOY - 40000, DEPLOY - 600     # anchors evenly spaced; last anchor + 10 non-skipped slots ends >= 500 slots before the deploy
else:
    n_anchor, lo, hi = 24, DEPLOY + 3000, latest - 1000
anchors = [lo + (hi - lo) * i // (n_anchor - 1) for i in range(n_anchor)]
a_txs, plan = {}, {"latest_finalized_slot_at_plan": latest, "lo": lo, "hi": hi, "anchors": {}}
for anchor in anchors:
    got, slot, skipped = [], anchor, 0
    while len(got) < 10:
        r = cached("blocks", str(slot), "getBlock", [slot, {"encoding": "json", "transactionDetails": "full", "rewards": False, "maxSupportedTransactionVersion": MAXV, "commitment": "finalized"}])
        if r is None:
            skipped += 1
        else:
            r["slot"] = slot; got.append(slot)
            for tx in r["transactions"]:
                meta = tx.get("meta") or {}
                keys = list(tx["transaction"]["message"].get("accountKeys", [])) + list((meta.get("loadedAddresses") or {}).get("writable", [])) + list((meta.get("loadedAddresses") or {}).get("readonly", []))
                if meta.get("err") is None and PROGS & set(keys):
                    a_txs[tx["transaction"]["signatures"][0]] = {"slot": slot, "anchor": anchor, "blockTime": r.get("blockTime")}
        slot += 1
    plan["anchors"][anchor] = {"blocks": got, "skipped_slots": skipped}
    print(anchor, len(got), skipped, len(a_txs), flush=True)
# sample C (post only): up to 6 migration txs found in sample A (first 6 in slot order), boost vault signatures as in 1021: 2 oldest + 2 newest successful
c_info = []
if not PRE:
    done_pools = set(); _blk = {}
    for sig, meta in sorted(a_txs.items(), key=lambda kv: kv[1]["slot"]):
        if len(c_info) >= 6:
            break
        if meta["slot"] not in _blk:
            _blk.clear(); _blk[meta["slot"]] = json.loads(gzip.open(RAW / "blocks" / f"{meta['slot']}.json.gz").read())["r"]   # one block in memory (a_txs is in slot order)
        blk = _blk[meta["slot"]]
        tx = next(t for t in blk["transactions"] if t["transaction"]["signatures"][0] == sig)
        try:
            g = m.parse_migrate_tx(tx)
        except Exception:
            g = None
        if not g or not g.get("pool") or g["pool"] in done_pools:
            continue
        done_pools.add(g["pool"])
        vault = m.boost_vault(g["pool"]) if g.get("quote_wsol", True) else None
        sigs = c.call("getSignaturesForAddress", [vault, {"limit": 100, "commitment": "finalized"}]) if vault else []
        ok = [x["signature"] for x in (sigs or []) if x.get("err") is None]
        pick = ok[-2:] + ok[:2]  # newest first: the 2 oldest and the 2 newest
        for ps in pick:
            cached("txs", ps, "getTransaction", [ps, {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": MAXV}])
        c_info.append({"mig_sig": sig, "pool": g["pool"], "quote_wsol": g.get("quote_wsol"), "n_sigs": len(ok), "picked": pick})
plan.update(n_a_txs=len(a_txs), rpc_attempts=c.total_calls, c_candidates=c_info)
(OUT / ("s05_plan_pre2.json" if PRE2 else "s05_plan_pre.json" if PRE else "s05_plan.json")).write_text(json.dumps(plan, indent=1))
(RAW / "sample_index.json").write_text(json.dumps({"a": a_txs, "b": {}, "c": c_info}))
print("A", len(a_txs), "C", len(c_info), "rpc", c.total_calls, dict(c.calls))
