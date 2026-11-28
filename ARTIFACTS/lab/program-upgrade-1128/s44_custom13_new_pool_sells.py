"""Manager question (report only, no bar), QP observation: InstructionError Custom=13 on WSOL PumpSwap sells of NEW-binary pools (116) vs 0 on old pools, from the s38 sample (cached A blocks and the s38 pool-age cache).
Per failing tx: program that raised the error (from the log line "Program <id> failed: custom program error: 0xd"), the failing top-level instruction index and its program, the mint's owner program and Token-2022 extension types (one getMultipleAccounts read of the distinct mints, public RPC, 0 credits), and the count of successful sells on the same new pools. Counts only."""
import collections, gzip, json, os, sys, time, base64
from pathlib import Path
sys.argv = [sys.argv[0]]
src = open("s18_instruction_shapes.py").read()
exec(src.split("res = {}; own_inputs")[0])
from common import LClient, RAW, OUT
D_PUMP = 474715515
cache = json.load(open(RAW / "pool_age.json"))
def cls(pool):
    s = cache.get(pool)
    return "UNKNOWN" if s is None else "NEW" if s >= D_PUMP + 150 else "OLD" if s <= D_PUMP - 150 else "GAP"
fails = []; ok_sells = collections.Counter(); fail_pools = set(); all_sells = collections.Counter()
KNOWN = {PID["pump_amm"]: "PumpSwap", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": "SPL Token", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb": "Token-2022", "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL": "ATA", "11111111111111111111111111111111": "System", PID["pump"]: "pump", PID["pump_fees"]: "pump_fees"}
for p in sorted((BASE / "raw" / "blocks").glob("*.json.gz")):
    blk = json.loads(gzip.open(p).read())["r"]
    if not blk: continue
    for tx in blk["transactions"]:
        meta = tx.get("meta") or {}
        for where, d, acc in amm_ixs(tx):
            if len(acc) < 5 or acc[4] != WSOL or DISC[d[:8].hex()] != "sell": continue
            k = cls(acc[0]); all_sells[(k, meta.get("err") is None)] += 1
            if k != "NEW": continue
            if meta.get("err") is None: ok_sells[acc[0]] += 1; continue
            if errword(meta["err"]) != "InstructionError:Custom=13": continue
            keys = keys_of(tx); idx = meta["err"]["InstructionError"][0]
            raiser = None
            for ln in meta.get("logMessages") or []:
                if "failed: custom program error: 0xd" in ln: raiser = ln.split()[1]
            top = tx["transaction"]["message"]["instructions"][idx]
            fails.append({"sig": tx["transaction"]["signatures"][0], "ix_index": idx, "ix_program": KNOWN.get(keys[top["programIdIndex"]], keys[top["programIdIndex"]][:8]),
                          "raiser": KNOWN.get(raiser, raiser), "n_top_ixs": len(tx["transaction"]["message"]["instructions"]), "pool": acc[0], "mint": acc[3], "n_amm_sell_ixs_in_tx": len(amm_ixs(tx)), "amm_sell_where": where, "amm_invoked_in_logs": any(l.startswith("Program " + PID["pump_amm"] + " invoke") for l in meta.get("logMessages") or []), "amm_success_in_logs": any(l.startswith("Program " + PID["pump_amm"] + " success") for l in meta.get("logMessages") or []), "log_trunc": any(l.startswith("Log truncated") for l in meta.get("logMessages") or [])})
mints = sorted({f["mint"] for f in fails})
c = LClient(max_calls=10, min_interval=0.5); info = {}
for i in range(0, len(mints), 100):
    r = c.call("getMultipleAccounts", [mints[i:i + 100], {"encoding": "base64", "commitment": "finalized"}])
    for m, v in zip(mints[i:i + 100], r["value"]):
        if v is None: info[m] = {"owner": None}; continue
        b = base64.b64decode(v["data"][0]); ext = []
        if v["owner"].startswith("Tokenz") and len(b) > 166 and b[165] == 1:
            o = 166
            while o + 4 <= len(b):
                t = int.from_bytes(b[o:o + 2], "little"); n = int.from_bytes(b[o + 2:o + 4], "little")
                if t == 0 and n == 0: break
                ext.append(t); o += 4 + n
        info[m] = {"owner": KNOWN.get(v["owner"], v["owner"]), "extensions": ext}
res = {"n_custom13_new_pool_sells": len(fails), "raiser": dict(collections.Counter(f["raiser"] for f in fails)), "ix_program": dict(collections.Counter(f["ix_program"] for f in fails)),
       "ix_index": dict(collections.Counter(f["ix_index"] for f in fails)), "distinct_pools": len({f["pool"] for f in fails}), "distinct_mints": len(mints),
       "mint_owner_and_extensions": dict(collections.Counter(f"{v['owner']}|{v.get('extensions')}" for v in info.values())),
       "failing_txs_by_mint_owner": dict(collections.Counter(info[f["mint"]]["owner"] for f in fails)),
       "successful_sells_on_the_same_pools": sum(ok_sells.get(p, 0) for p in {f["pool"] for f in fails}), "pools_with_failure_and_a_success": sum(1 for p in {f["pool"] for f in fails} if ok_sells.get(p)),
       "sells_by_class_and_success_all_new_old": {f"{k[0]}|{'ok' if k[1] else 'failed'}": v for k, v in all_sells.items()}, "amm_sell_where": dict(collections.Counter(f["amm_sell_where"] for f in fails)), "amm_invoked_in_logs": sum(f["amm_invoked_in_logs"] for f in fails), "amm_success_in_logs": sum(f["amm_success_in_logs"] for f in fails), "n_log_truncated": sum(f["log_trunc"] for f in fails), "n_amm_sell_ixs_per_tx": dict(collections.Counter(f["n_amm_sell_ixs_in_tx"] for f in fails)),
       "rpc_attempts": c.total_calls}
(OUT / "s44_custom13_new_pool_sells.json").write_text(json.dumps(res, indent=1)); print(json.dumps(res, indent=1))
