"""P64 step 1: can creator-fee claims be recovered causally, and how often do they precede entry?

Counts only. No outcome (pnl) column is read. Exploration blocks only (frozen H5/Q50 binding trades, Aug/Sep).
Public RPC only (api.mainnet-beta.solana.com), 0 Helius credits.

For a random sample of the frozen trades (seed 1, drawn without looking at any outcome):
  entry_t = mbt + trig_t + 1.9 s (mbt = migration block time, trig_t = seconds after the complete slot; leg b lands 1.9 s later).
  anchor  = first signature of getBlock(slot ~ mslot + (trig_t+1.9+3)/sps) so that getSignaturesForAddress(creator, before=anchor)
            walks backwards from just after entry. (The tape has no signature column on trades.)
  window  = creator-signed transactions with entry_t-3600 <= blockTime < entry_t, stopping at the create signature.
  claim   = a transaction whose logs contain Instruction: CollectCreatorFee / CollectCoinCreatorFee (pump / pump_amm
            collect_creator_fee, collect_creator_fee_v2, collect_coin_creator_fee). Sweep/distribute/transfer variants are counted separately.
Usage: python p64_probe_claims.py MAP.parquet OUT.json N_SAMPLE
"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

URL = 'https://api.mainnet-beta.solana.com'
FETCH_CAP = 30      # getTransaction calls per trade
PAGE_CAP = 3
W = '/data/mal/hunt-1008/h5-lossfilter'
CLAIM = ('Instruction: CollectCreatorFee', 'Instruction: CollectCoinCreatorFee')
OTHER = ('Instruction: SweepCreatorFee', 'Instruction: DistributeCreatorFees', 'Instruction: TransferCreatorFeesToPump')


def rpc(method, params, tries=6):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for i in range(tries):
        try:
            q = urllib.request.Request(URL, body, {'content-type': 'application/json'})
            j = json.load(urllib.request.urlopen(q, timeout=40))
            if 'error' in j and j['error'].get('code') in (429, -32005):
                time.sleep(1 + 2 * i); continue
            return j
        except Exception as e:
            time.sleep(1 + 2 * i)
            last = {'error': str(e)}
    return last


def one(r):
    out = dict(mint=r.mint, creator=r.creator, set=r.set, day=r.day, status='ok')
    entry = r.mbt + r.trig_t + 1.9
    slot0 = int(r.mslot + (r.trig_t + 1.9 + 3) / r.sps)
    anchor = None
    for k in range(0, 6):
        j = rpc("getBlock", [slot0 + k, {"transactionDetails": "signatures", "rewards": False, "maxSupportedTransactionVersion": 0}])
        res = j.get('result')
        if res and res.get('signatures'):
            anchor = res['signatures'][0]; out['anchor_dt'] = res['blockTime'] - entry; break
    if anchor is None:
        out['status'] = 'no_anchor'; return out
    sigs = []; before = anchor; done = False
    for _ in range(PAGE_CAP):
        j = rpc("getSignaturesForAddress", [r.creator, {"limit": 1000, "before": before, "until": r.signature}])
        res = j.get('result')
        if res is None:
            out['status'] = 'sig_err'; return out
        sigs += res
        if len(res) < 1000 or res[-1]['blockTime'] < entry - 3600:
            done = True; break
        before = res[-1]['signature']
    out['paged_complete'] = done
    win = [s for s in sigs if s['blockTime'] and entry - 3600 <= s['blockTime'] < entry and not s.get('err')]
    out['n_win_sigs'] = len(win)
    claims = []; other = []; fetched = 0; fail = 0
    for s in win[:FETCH_CAP]:
        j = rpc("getTransaction", [s['signature'], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
        res = j.get('result')
        if not res:
            fail += 1; continue
        fetched += 1
        logs = res['meta'].get('logMessages') or []
        if any(any(c in l for c in CLAIM) for l in logs):
            claims.append(entry - s['blockTime'])
        elif any(any(c in l for c in OTHER) for l in logs):
            other.append(entry - s['blockTime'])
    out.update(fetched=fetched, fetch_fail=fail, truncated=len(win) > FETCH_CAP, claim_lags_s=claims, other_lags_s=other)
    return out


def main(mp, outp, n):
    x = pd.read_parquet(mp)
    fz = pd.read_parquet(f'{W}/frozen_binding_trades.parquet', columns=['mint', 'trig_t', 'mslot', 'sps'])  # no pnl column read
    x = x.merge(fz, on='mint').drop_duplicates('mint')
    if 'set' not in x:
        x['set'] = np.where(x.day < '2026-09-01', 'disc', 'conf')
    s = x.sample(int(n), random_state=1)
    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(one, s.itertuples()))
    json.dump(rows, open(outp, 'w'))
    print('done', len(rows))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3])
