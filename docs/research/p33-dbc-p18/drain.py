"""P33 post-hoc (manager17 request after P39, PR #949): per migrated DAMM v2 pool, the DRAIN instant vs the first possible strand-sell landing. Decode only (reserves), no price.
drain = first DAMM trade row whose post-trade WSOL reserve < 1% of the max WSOL reserve seen so far, the max being >= 1 SOL (P39's 1%-of-reserve definition applied per print).
first possible sell landing = migration slot + k, k = 9 (round(1.9 s / 0.2166 s), the m10 decisions' k_in); also k + slot-close (1 slot) as a stricter line.
Exploration migrations only (P16 fetch: [2026-10-14T00, 2026-10-17T00), rows >= 10-17T00 dropped)."""
import json, subprocess, collections
import numpy as np
O = '/data/mal/hunt-1008/push-1016/p33/measure-1'
D = '/data2/mal/venue-tape-backfill/meteora_damm_v2/pools'
END = 1792195200  # 2026-10-17T00Z
SOL = 1e9
per = [json.loads(l) for l in open(f'{O}/damm_per_pool.jsonl')]
def q(a):
    a = np.asarray(a, float)
    return {"n": int(len(a)), **({f"p{p}": float(np.quantile(a, p / 100)) for p in (10, 25, 50, 75, 90, 99)} if len(a) else {})}
res = {}
rows_out = []
for e in per:
    if e.get('status') != 'ok' or not e.get('wsol_is_b'):
        continue
    raw = subprocess.run(['zstd', '-dc', '-q', f"{D}/pool-{e['damm_pool']}.jsonl.zst"], capture_output=True, check=True).stdout
    tr = [r for r in (json.loads(x) for x in raw.splitlines() if x) if r.get('type') == 'trade' and not r.get('failed') and r['block_time'] < END]
    ms, mbt = e['mig_slot'], e['mig_bt']
    mx, drain = 0, None
    for r in tr:
        rb = int(r['reserve_b'])
        if rb > mx: mx = rb
        if mx >= SOL and rb < 0.01 * mx:
            drain = r; break
    land = ms + 9
    pre_land = [r for r in tr if r['slot'] <= land]
    rb_land = int(pre_land[-1]['reserve_b']) if pre_land else None
    x = {"sample": e['sample'], "era": e['era'], "max_rb_sol": mx / SOL, "drained": drain is not None,
         "drain_after_mig_s": (drain['block_time'] - mbt) if drain else None, "drain_after_mig_slots": (drain['slot'] - ms) if drain else None,
         "land_before_drain": (drain is None) or (land < drain['slot']), "land10_before_drain": (drain is None) or (land + 1 < drain['slot']),
         "rb_at_land_sol": (rb_land / SOL) if rb_land is not None else None, "covered_s": e.get('covered_to_bt', END) - mbt}
    rows_out.append(x)
for g in ('uniform', 'strand'):
    for era in ('swap2', 'swap3', None):
        R = [x for x in rows_out if x['sample'] == g and (era is None or x['era'] == era)]
        R1 = [x for x in R if x['max_rb_sol'] >= 1]
        res[f"{g}_{era or 'all'}"] = {
            "pools_wsol_b": len(R), "max_reserve_ge_1sol": len(R1), "drained_of_ge1": sum(x['drained'] for x in R1),
            "drain_after_mig_s": q([x['drain_after_mig_s'] for x in R1 if x['drained']]),
            "drain_after_mig_slots": q([x['drain_after_mig_slots'] for x in R1 if x['drained']]),
            "drain_within_s": {str(N): sum(1 for x in R1 if x['drained'] and x['drain_after_mig_s'] <= N) for N in (2, 5, 10, 30, 60, 120, 300, 600)},
            "sell_land_mig_plus9_before_drain_of_ge1": sum(x['land_before_drain'] for x in R1),
            "sell_land_mig_plus10_before_drain_of_ge1": sum(x['land10_before_drain'] for x in R1),
            "rb_at_land_sol_ge1": q([x['rb_at_land_sol'] for x in R1 if x['rb_at_land_sol'] is not None])}
print(json.dumps(res, indent=1))
