"""(c2') live-send evidence count (QP-RULING-1121-a3-repin-triple Q2, sha256 d704d786...). Manager job on mal-fast-0. COUNTS ONLY.
Reads ONLY FLOOR bridge rows (bridge-log-live.jsonl) of kinds buy_sent/buy_landed/buy_failed/sell_sent/sell_landed/sell_failed with ts_ms >= 2026-11-20T19:28:12.000Z.
Prints per side: landed / failed counts, failure label words, landed_slot > 471854493 (bool counts), distinct pools (mints) count, send attempts per order (histogram).
Never prints amounts, balances, prices, P&L, exit_reason, pool/mint ids, order ids or signatures.
Why bridge rows carry err: at tree 5fc473b5 the port appends buy_landed only when the position opened and sell_landed only when it closed
(arena_live_bridge.py _finish_buy/_finish_sell ~1500-1522); a landed tx with meta.err != None is logged buy_failed/sell_failed label 'landed_failed'."""
import collections, json, sys
P = '/var/lib/mal-live/floor-lane/state/bridge-log-live.jsonl'
CUT_MS = 1795202892000
SLOT = 471854493
KINDS = {'buy_sent', 'buy_landed', 'buy_failed', 'sell_sent', 'sell_landed', 'sell_failed'}
rows = []
with open(P, 'rb') as f:
    for line in f:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get('kind') in KINDS and isinstance(r.get('ts_ms'), int) and r['ts_ms'] >= CUT_MS:
            rows.append(r)
mint_of = {}
for r in rows:
    if r['kind'] in ('buy_sent', 'sell_sent') and r.get('order_id') and r.get('mint'):
        mint_of.setdefault((r['kind'][:3] if r['kind'].startswith('buy') else 'sel', r['order_id']), r['mint'])
out = {'rows_read': len(rows), 'cut_utc': '2026-11-20T19:28:12Z', 'slot_bar': SLOT}
for side in ('buy', 'sell'):
    key = 'buy' if side == 'buy' else 'sel'
    sent = [r for r in rows if r['kind'] == f'{side}_sent']
    landed = [r for r in rows if r['kind'] == f'{side}_landed']
    failed = [r for r in rows if r['kind'] == f'{side}_failed']
    slot_gt = sum(1 for r in landed if isinstance(r.get('landed_slot'), int) and r['landed_slot'] > SLOT)
    slot_missing = sum(1 for r in landed if not isinstance(r.get('landed_slot'), int))
    pools = {mint_of.get((key, r.get('order_id'))) for r in landed} - {None}
    if side == 'sell':
        tries = collections.Counter(int(r.get('tries') or 0) for r in sent)
    else:
        tries = collections.Counter(collections.Counter(r.get('order_id') for r in sent).values())
    out[side] = {'sent': len(sent), 'landed': len(landed), 'failed': len(failed),
                 'failed_labels': dict(collections.Counter(str(r.get('label')) for r in failed)),
                 'landed_slot_gt_bar': slot_gt, 'landed_slot_missing': slot_missing,
                 'landed_err_null_by_construction': len(landed),
                 'distinct_pools_landed': len(pools), 'send_attempts_hist': {str(k): v for k, v in sorted(tries.items())}}
BAD = ('instruction', 'account', 'constraint', 'custom program', 'deserialize', 'seeds')
words = [str(r.get('label')).lower() for r in rows if r['kind'] in ('buy_failed', 'sell_failed')]
out['failed_with_instr_account_constraint_word'] = sum(1 for w in words if any(b in w for b in BAD))
b, s = out['buy'], out['sell']
ok = (b['landed'] >= 3 and s['landed'] >= 3 and b['landed_slot_gt_bar'] == b['landed'] and s['landed_slot_gt_bar'] == s['landed']
      and b['distinct_pools_landed'] >= 3 and s['distinct_pools_landed'] >= 3 and out['failed_with_instr_account_constraint_word'] == 0)
out['c2prime_bar_met'] = ok
print(json.dumps(out, indent=1, sort_keys=True))
sys.exit(0)
