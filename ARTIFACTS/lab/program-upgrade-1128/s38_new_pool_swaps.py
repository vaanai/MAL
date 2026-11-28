"""QP-RULING-1128 N2 and N3. Offline over the cached blocks of A (raw/blocks) plus a pool-age lookup (public RPC, 0 credits, cached in RAW/pool_age.json).
Pool class: NEW = the pool's mint's pump bonding-curve PDA (["bonding-curve", mint]) has its newest signature at slot >= D+150 = 474715665 (after migration nothing writes that
account, so its newest signature is the migration: the disclosed rule of the s16 keyless sim); OLD = that slot <= D-150 ; a migration inside [D-150, D+150) is class GAP (not used).
N2: successful WSOL PumpSwap buy_exact_quote_in (c62e1552b4d9e870, 25 bytes, 26 accounts) and sell (33e685a4017f83ad, 24 bytes, 24 accounts) on NEW pools by any signer, with the
executor's derivations at every fixed and PDA position (tools/pumpswap_tx.py at e85447f8, as s18). N3: failed share (and ex-slippage: sell 6004, buy 6040) NEW vs OLD pools, per UTC
block hour and overall, same blocks. Structure only: counts, error words, booleans."""
import collections, gzip, json, os, sys, time
from pathlib import Path
sys.argv = [sys.argv[0]]
src = open("s18_instruction_shapes.py").read()
exec(src.split("res = {}; own_inputs")[0])
from common import LClient, RAW, OUT
D_PUMP = 474715515
c = LClient(max_calls=int(os.environ.get("S38_MAX_CALLS", "2500")), min_interval=0.4)
cache_p = RAW / "pool_age.json"
cache = json.load(open(cache_p)) if cache_p.exists() else {}
rows = []   # (hour, slot, name, ok, errword, pool, mint, acc, data_len)
for p in sorted((BASE / "raw" / "blocks").glob("*.json.gz")):
    blk = json.loads(gzip.open(p).read())["r"]
    if not blk: continue
    hr = time.strftime("%FT%HZ", time.gmtime(blk["blockTime"]))
    for tx in blk["transactions"]:
        meta = tx.get("meta") or {}
        for where, d, acc in amm_ixs(tx):
            if len(acc) < 5 or acc[4] != WSOL: continue
            nm = DISC[d[:8].hex()]
            if nm == "buy": continue
            rows.append((hr, blk["slot"], nm, meta.get("err") is None, errword(meta.get("err")), acc[0], acc[3], acc, len(d)))
pools = sorted({(r[5], r[6]) for r in rows})
def age_slot(pool, mint):
    if pool in cache: return cache[pool]
    bc = str(pst.pda([b"bonding-curve", bytes(P(mint))], pst.PUMP_PROGRAM)) if hasattr(pst, "pda") and hasattr(pst, "PUMP_PROGRAM") else None
    sigs = c.call("getSignaturesForAddress", [bc, {"limit": 1, "commitment": "finalized"}]) or []
    cache[pool] = sigs[0]["slot"] if sigs else None
    return cache[pool]
for i, (pool, mint) in enumerate(pools):
    age_slot(pool, mint)
    if i % 100 == 0: cache_p.write_text(json.dumps(cache)); print("pools", i, len(pools), c.total_calls, flush=True)
cache_p.write_text(json.dumps(cache))
def cls(pool):
    s = cache.get(pool)
    if s is None: return "UNKNOWN"
    return "NEW" if s >= D_PUMP + 150 else "OLD" if s <= D_PUMP - 150 else "GAP"
SLIP = {"sell": "InstructionError:Custom=6004", "buy_exact_quote_in": "InstructionError:Custom=6040"}
res = {"n_distinct_pools": len(pools), "pool_class_counts": dict(collections.Counter(cls(p) for p, _ in pools)), "rpc_attempts": c.total_calls}
share = {}
for k in ("NEW", "OLD"):
    for nm in ("buy_exact_quote_in", "sell"):
        sel = [r for r in rows if cls(r[5]) == k and r[2] == nm]
        ok = sum(r[3] for r in sel); bad = len(sel) - ok; sl = sum(1 for r in sel if not r[3] and r[4] == SLIP[nm])
        byh = collections.defaultdict(lambda: [0, 0, 0])
        for r in sel: h = byh[r[0]]; h[0] += 1; h[1] += (not r[3]); h[2] += (not r[3] and r[4] == SLIP[nm])
        words = collections.Counter(r[4] for r in sel if not r[3])
        share[f"{k}|{nm}"] = {"n": len(sel), "success": ok, "failed": bad, "slippage_failed": sl, "failed_share": bad / len(sel) if sel else None,
                              "failed_share_ex_slippage": (bad - sl) / (len(sel) - sl) if len(sel) - sl else None, "words": dict(words), "per_hour_n_failed_slip": {h: v for h, v in sorted(byh.items())}}
res["failed_share"] = share
# N2 shapes on NEW pools (successful only)
FX = {2: str(pst.GLOBAL_CONFIG), 4: str(pst.WSOL_MINT), 13: str(pst.SYSTEM_PROGRAM), 14: str(pst.ATA_PROGRAM), 15: str(pst.event_authority()), 16: str(pst.PUMPSWAP_PROGRAM)}
n2 = {}
for nm, (dlen, na) in (("buy_exact_quote_in", (25, 26)), ("sell", (24, 24))):
    sel = [r for r in rows if r[3] and cls(r[5]) == "NEW" and r[2] == nm]
    shape = collections.Counter((r[8], len(r[7])) for r in sel)
    fx = dict(FX); sell = nm == "sell"
    fx.update({19: str(pst.fee_config()), 20: str(pst.FEE_PROGRAM)} if sell else {19: str(pst.global_volume_accumulator()), 21: str(pst.fee_config()), 22: str(pst.FEE_PROGRAM)})
    fixed_ok = sum(all(len(r[7]) > pos and r[7][pos] == a for pos, a in fx.items()) for r in sel)
    pda_ok = 0; pda_err = 0
    for r in sel:
        acc = r[7]
        try:
            tp12 = P(acc[12]); tp11 = P(acc[11])
            chk = [acc[21 if sell else 23] == str(pst.pool_v2(P(acc[3]))), acc[5] == str(pst.ata(P(acc[1]), P(acc[3]), tp11)), acc[6] == str(pst.ata(P(acc[1]), pst.WSOL_MINT, tp12)),
                   acc[7] == str(pst.ata(P(acc[0]), P(acc[3]), tp11)), acc[8] == str(pst.ata(P(acc[0]), pst.WSOL_MINT, tp12)), acc[10] == str(pst.ata(P(acc[9]), pst.WSOL_MINT, tp12))]
            if not sell: chk.append(acc[20] == str(pst.user_volume_accumulator(P(acc[1]))))
            pda_ok += all(chk)
        except Exception: pda_err += 1
    n2[nm] = {"n_success_new_pools": len(sel), "distinct_new_pools": len({r[5] for r in sel}), "shape_(data_len,accounts)": {str(k): v for k, v in shape.items()},
              "expected": [dlen, na], "all_expected_shape": all(k == (dlen, na) for k in shape), "fixed_positions_equal_executor": fixed_ok, "pda_positions_equal_executor": pda_ok, "derive_errors": pda_err,
              "discriminator": {"c62e1552b4d9e870" if nm != "sell" else "33e685a4017f83ad": len(sel)}}
res["N2"] = n2
(OUT / "s38_new_pool_swaps.json").write_text(json.dumps(res, indent=1, sort_keys=True))
print(json.dumps({k: v for k, v in res.items() if k != "failed_share"}, indent=1)); print({k: {a: b for a, b in v.items() if a not in ("per_hour_n_failed_slip", "words")} for k, v in share.items()})
