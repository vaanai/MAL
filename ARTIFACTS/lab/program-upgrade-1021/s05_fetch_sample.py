"""Fetch the PRE-DECLARED decode-parity sample (rule in ARTIFACTS/lab/a3-repin-2026-10-21-program-upgrade-review.md section 3, committed before this ran).
Public RPC only, 0 Helius credits. Raw responses cached gz under RAW/{blocks,txs}. Structure only: no price or outcome is computed here.

Sample A (decoder-agnostic): per hour 2026-10-19T18 .. 2026-10-20T17 (24 closed tape hours), anchor slot = min slot of the first 200 pumpfun
  trade rows of the hour's tape file; the first 10 non-skipped slots at or after anchor+3000 are fetched with getBlock (full). Every
  successful tx that touches pump, PumpSwap or the fees program is in the sample.
Sample B (tape-stratified): per hour, within the first 600,000 lines of the tape file, the first row signatures: 1 per (prog, ix_name) of type
  trade, 3 per type create / complete / pool_create. Fetched with getTransaction.
Sample C (BOOST slices): hours with index % 4 == 0 (6 hours): the first B tx of that hour that holds a CompletePumpAmmMigrationEvent gives the
  pool; getSignaturesForAddress(boost vault, limit 100); the 2 oldest and 2 newest signatures are fetched with getTransaction.
"""
import base64, gzip, json, subprocess, sys, time
from pathlib import Path
from common import RAW, OUT, LClient, m
TAPE = Path("/data/mal/arena/tape")
HOURS = [f"2026-10-19T{h:02d}" for h in range(18, 24)] + [f"2026-10-20T{h:02d}" for h in range(0, 18)]
PROGS = {m.PUMP_PROGRAM_ID if hasattr(m, "PUMP_PROGRAM_ID") else "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
         "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"}
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

def tape_scan(hour):
    anchors, b_rows, seen = [], [], {}
    quota = {"create": 3, "complete": 3, "pool_create": 3}
    n_by = {"create": 0, "complete": 0, "pool_create": 0}
    proc = subprocess.Popen(["zstd", "-dc", str(TAPE / f"tape-{hour}.jsonl.zst")], stdout=subprocess.PIPE)
    for i, line in enumerate(proc.stdout):
        if i >= 600_000:
            break
        try:
            r = json.loads(line)
        except ValueError:
            continue
        t = r.get("type")
        if t == "trade":
            if r.get("prog") == "pumpfun" and len(anchors) < 200:
                anchors.append(r["slot"])
            k = (r.get("prog"), r.get("ix_name"))
            if k not in seen:
                seen[k] = 1; b_rows.append({"sig": r["signature"], "slot": r["slot"], "key": f"trade:{k[0]}:{k[1]}"})
        elif t in quota and n_by[t] < quota[t]:
            n_by[t] += 1; b_rows.append({"sig": r["signature"], "slot": r["slot"], "key": t})
    proc.kill(); proc.wait()
    return min(anchors), b_rows

plan = {"hours": {}}
a_txs = {}; b_txs = {}
for hour in HOURS:
    anchor, b_rows = tape_scan(hour)
    got, slot, skipped = [], anchor + 3000, 0
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
                    a_txs[tx["transaction"]["signatures"][0]] = {"slot": slot, "hour": hour, "blockTime": r.get("blockTime")}
        slot += 1
    plan["hours"][hour] = {"anchor": anchor, "blocks": got, "skipped_slots": skipped, "b_rows": b_rows}
    print(hour, anchor, len(got), skipped, len(b_rows), len(a_txs), flush=True)
for hour, h in plan["hours"].items():
    for row in h["b_rows"]:
        if row["sig"] in a_txs or row["sig"] in b_txs:
            continue
        r = cached("txs", row["sig"], "getTransaction", [row["sig"], {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": MAXV}])
        b_txs[row["sig"]] = {"hour": hour, "key": row["key"], "slot": row["slot"], "fetched": r is not None}
# sample C
c_info = []
for idx, hour in enumerate(HOURS):
    if idx % 4:
        continue
    for sig, meta in b_txs.items():
        if meta["hour"] != hour or not meta["fetched"]:
            continue
        tx = json.loads(gzip.open(RAW / "txs" / f"{sig}.json.gz").read())["r"]
        try:
            g = m.parse_migrate_tx(tx)
        except Exception:
            g = None
        if not g or not g.get("pool"):
            continue
        vault = m.boost_vault(g["pool"]) if g.get("quote_wsol", True) else None
        sigs = c.call("getSignaturesForAddress", [vault, {"limit": 100, "commitment": "finalized"}]) if vault else []
        ok = [x["signature"] for x in (sigs or []) if x.get("err") is None]
        pick = ok[-2:] + ok[:2]  # getSignaturesForAddress is newest first: the 2 oldest and the 2 newest
        for ps in pick:
            cached("txs", ps, "getTransaction", [ps, {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": MAXV}])
        c_info.append({"hour": hour, "mig_sig": sig, "pool": g["pool"], "n_sigs": len(ok), "picked": pick})
        break
plan["c_candidates"] = c_info
(OUT / "s05_plan.json").write_text(json.dumps({"hours": {k: {kk: vv for kk, vv in v.items() if kk != "b_rows"} | {"n_b_rows": len(v["b_rows"])} for k, v in plan["hours"].items()},
    "n_a_txs": len(a_txs), "n_b_txs": len(b_txs), "rpc_attempts": c.total_calls, "c_candidates": c_info}, indent=1))
(RAW / "sample_index.json").write_text(json.dumps({"a": a_txs, "b": b_txs, "c": c_info}))
print("A", len(a_txs), "B", len(b_txs), "rpc", c.total_calls, dict(c.calls))
