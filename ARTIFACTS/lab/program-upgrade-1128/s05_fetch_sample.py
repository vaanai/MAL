"""Fetch the PRE-DECLARED decode-parity samples (rule: ARTIFACTS/lab/a3-repin-2026-11-20-pump-pumpswap-fees-program-upgrade-review.md section 3, committed before this ran).
CHAIN-ONLY: no tape, arena, book or ledger file is opened; anchors are slot numbers computed here. Public RPC, 0 Helius credits.
Raw responses cached gz under RAW/{blocks,txs}. Structure only: nothing is priced.
Modes (env SAMPLE): a1 (stage 1, post), a2 (stage 2, post, run later), pre (control P). RAW = UPGRADE_RAW (default /data2/mal/scratch/a3-repin-1128/raw).
  SAMPLE=a1  python3 s05_fetch_sample.py
  SAMPLE=pre UPGRADE_RAW=/data2/mal/scratch/a3-repin-1128/raw_pre python3 s05_fetch_sample.py
  SAMPLE=a2 A1_HI=<hi from out/s05_plan_a1.json> UPGRADE_RAW=/data2/mal/scratch/a3-repin-1128/raw_a2 python3 s05_fetch_sample.py
"""
import gzip, json, os, sys
from common import RAW, OUT, LClient, m

MODE = os.environ.get("SAMPLE", "a1")
PRE = MODE == "pre"
D_PUMP, D_SWAP, D_FEES = 474715515, 471854493, 471854409   # ONLY pump was redeployed (474715515, 2026-11-27T22:20:22Z); E = D_PUMP; PumpSwap and fees keep their 2026-11-20 slots
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
if PRE:     # 12 anchors x 10 non-skipped blocks, evenly spaced; last anchor + 10 non-skipped slots ends >= 150 slots before E = D_PUMP (hi = D_FEES - 600)
    n_anchor, lo, hi = 12, D_PUMP - 60000, D_PUMP - 600
elif MODE == "a2":   # stage 2: 24 anchors x 10, from 1000 slots after A1's hi to latest - 1000 (run only after 2026-11-16T03:30Z)
    n_anchor, lo, hi = 24, int(os.environ["A1_HI"]) + 1000, latest - 1000
else:       # a1: 24 anchors x 10, from D_SWAP + 150 to latest - 1000
    n_anchor, lo, hi = 24, D_PUMP + 150, latest - 1000
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
plan.update(n_a_txs=len(a_txs), rpc_attempts=c.total_calls)
(OUT / f"s05_plan_{MODE}.json").write_text(json.dumps(plan, indent=1))
(RAW / "sample_index.json").write_text(json.dumps({"a": a_txs}))
print("txs", len(a_txs), "rpc", c.total_calls, dict(c.calls))
