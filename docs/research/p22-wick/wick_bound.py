"""P22 wick-bid latency-0 upper bound. Exploration PumpSwap blocks only. See DECLARE.md. Usage: wick_bound.py OUTDIR [--counts]"""
import sys, os, json, glob, re, math, subprocess
import numpy as np, pandas as pd, duckdb
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
from tools import arena_stack as A

ROOT = '/data2/mal/blocks-clean'
BLOCKS = ['explore-0814', 'fresh-0903', 'exp011-0909']
WSOL = 'So11111111111111111111111111111111111111112'
FEE, SEND = 0.0025, 505_000
STAKES = (0.1, 0.25)
RULES = {'D8-300': (0.08, 300), 'D15-300': (0.15, 300), 'D15-60': (0.15, 60), 'D15-1800': (0.15, 1800), 'D15-R': (0.15, 'R')}
SLOT_S, NEARBY_SLOTS = 0.4, 5   # nearby = buy lamports over slots [x - round(2/0.4), x]
OUT = sys.argv[1]; COUNTS = '--counts' in sys.argv
os.makedirs(OUT, exist_ok=True)

def hour_files():
    m = {}
    for b in BLOCKS:
        for f in glob.glob(f'{ROOT}/{b}/*/trades/trades-*.deduped.jsonl.zst'):
            h = re.search(r'trades-(\d{4}-\d\d-\d\dT\d\d)\.', f).group(1)
            assert h not in m
            m[h] = (b, f)
    return m
HF = hour_files()
HOURS = sorted(HF)
assert not any(h >= '2026-10-01' for h in HOURS)
def hnext(h, k):
    t = pd.Timestamp(h.replace('T', ' ') + ':00') + pd.Timedelta(hours=k)
    return t.strftime('%Y-%m-%dT%H')

def v_map():
    v = {}
    for f in ['pool_v_0909.json', 'pool_v_exp016.json', 'pool_v_0814.json', 'pool_v.json']:
        for k, val in json.load(open('/data/mal/pumpswap-virtual/' + f))['v'].items():
            if k not in v and val is not None and 0 <= val <= 30e9:
                v[k] = int(val)
    return v
VM = v_map()

def creators():
    con = duckdb.connect(); out = {}
    for b in BLOCKS:
        for f in glob.glob(f'{ROOT}/{b}/*/creates/creates-*.deduped.jsonl.zst'):
            d = con.execute(f"select mint, creator from read_ndjson('{f}', columns={{mint:'VARCHAR', creator:'VARCHAR'}}, ignore_errors=true)").fetchall()
            for m, c in d: out.setdefault(m, c)
    return out
CREATOR = creators()

CR_DF = pd.DataFrame({'mint': list(CREATOR.keys()), 'creator': list(CREATOR.values())})
V_DF = pd.DataFrame({'pool': list(VM.keys()), 'vv': [float(x) for x in VM.values()]})

def load(hours):
    con = duckdb.connect(); con.execute("SET memory_limit='12GB'; SET threads=4; SET preserve_insertion_order=false;")
    con.register('cr', CR_DF); con.register('vm', V_DF)
    fs = [HF[h][1] for h in hours]
    df = con.execute(f"""select (hash(t.pool) >> 1)::BIGINT as pi, (t.side='buy') as isbuy, (t.side='sell') as issell,
        coalesce(t.trader = cr.creator, false) as iscr, t.sol_lamports, t.quote_reserve, t.base_reserve::DOUBLE as base_reserve,
        t.slot, coalesce(t.tx_index,0) tx, coalesce(t.event_index,0) ev, t.block_time, coalesce(vm.vv,0)/1e9 as v
      from read_ndjson({fs!r}, columns={{venue:'VARCHAR',mint:'VARCHAR',trader:'VARCHAR',side:'VARCHAR',sol_lamports:'BIGINT',quote_reserve:'BIGINT',base_reserve:'HUGEINT',
        quote_is_wsol:'BOOLEAN',pool:'VARCHAR',slot:'BIGINT',tx_index:'BIGINT',event_index:'BIGINT',block_time:'BIGINT'}}, ignore_errors=true) t
      left join cr on cr.mint = t.mint left join vm on vm.pool = t.pool
      where t.venue='pumpswap' and t.quote_is_wsol and t.mint <> '{WSOL}' and t.pool is not null and t.block_time is not null
        and t.quote_reserve is not null and t.base_reserve > 0""").fetchnumpy()
    df = pd.DataFrame(df)
    o = np.lexsort((df['ev'].values, df['tx'].values, df['slot'].values, df['pi'].values))
    return df.iloc[o].reset_index(drop=True)

def day_events(day, hours_day):
    """Candidate sells of one UTC day plus the pricing columns; returns per-rule rows."""
    buf = [h for k in (1, 2) if (h := hnext(hours_day[-1], k)) in HF]
    hours = hours_day + buf
    df = load(hours)
    pi = df['pi'].values; bt = df['block_time'].values; slot = df['slot'].values
    Q = df['quote_reserve'].values / 1e9; B = df['base_reserve'].values; V = df['v'].values
    X = Q + V; P = X / B
    n = len(df)
    same_next = np.zeros(n, bool); same_next[:-1] = pi[1:] == pi[:-1]
    nxt = np.arange(n) + 1
    nxt_c = np.minimum(nxt, n - 1)
    Pn = P[nxt_c]; dt = bt[nxt_c] - bt
    sell = df['issell'].values
    d0 = np.floor(pd.Timestamp(day).timestamp()); 
    inday = (bt >= d0) & (bt < d0 + 86400)
    cand = sell & same_next & inday & (X >= 100) & (dt >= 0) & (dt <= 60)
    drop = 1 - Pn / P
    cand &= drop >= 0.08
    idx = np.nonzero(cand)[0]
    idx = idx[~df['iscr'].values[idx]]
    return df, idx, drop, nxt_c, (pi, bt, slot, Q, B, V, X, P, df['isbuy'].values, df['sol_lamports'].values)

def first_ge(pi, bt, i0, t):   # first index >= i0 within pool of i0 with block_time >= t; -1 if none
    p = pi[i0]; n = len(pi)
    j = i0 + 1
    # block_time is sorted by slot within a pool; binary search over the pool segment
    hi_end = np.searchsorted(pi, p, 'right')
    seg = bt[j:hi_end]
    if len(seg) == 0: return -1
    k = np.searchsorted(seg, t, 'left')
    return -1 if k >= len(seg) else j + k

def run():
    byday = {}
    for h in HOURS: byday.setdefault(h[:10], []).append(h)
    rows = []; cens = {}
    for day in sorted(byday):
        if os.environ.get('DAYS') and day not in os.environ['DAYS'].split(','): continue
        hd = byday[day]
        df, idx, drop, nxt, (pi, bt, slot, Q, B, V, X, P, isbuy, sol) = day_events(day, hd)
        blk = np.array([HF[hnext(day + 'T00', 0)][0]]) if False else None
        cs = np.concatenate([[0], np.cumsum(np.where(isbuy, sol, 0))])
        print(day, 'hours', len(hd), 'rows', len(df), 'cand8', len(idx), 'cand15', int((drop[idx] >= 0.15).sum()), flush=True)
        if COUNTS: continue
        for i in idx:
            e = nxt[i]; t0 = bt[i]
            hour = pd.Timestamp(int(t0), unit='s').strftime('%Y-%m-%dT%H')
            block = HF[hour][0] if hour in HF else None
            if block is None: continue
            # pressure inputs, causal: buys in the sell's slot before it, buy lamports over [slot-5, slot] before it
            s0 = np.searchsorted(pi, pi[i], 'left')
            seg_slot = slot[s0:i]
            ssb = int(((seg_slot == slot[i]) & isbuy[s0:i]).sum())
            lo = s0 + np.searchsorted(seg_slot, slot[i] - NEARBY_SLOTS, 'left')
            nearby = float(sol[lo:i][isbuy[lo:i]].sum())
            Xe, Be = X[e], B[e]            # post-sell state: next print's pre-trade reserves
            for rule, (thr, hold) in RULES.items():
                if drop[i] < thr: continue
                if hold == 'R':
                    jj = -1; p = pi[i]; end = np.searchsorted(pi, p, 'right')
                    k = np.nonzero((P[e:end] >= P[i]) & (bt[e:end] <= t0 + 1800))[0] if end > e else []
                    jj = e + int(k[0]) if len(k) else first_ge(pi, bt, i, t0 + 1800)
                else:
                    jj = first_ge(pi, bt, i, t0 + hold)
                if jj < 0:
                    cens[rule] = cens.get(rule, 0) + 1; continue
                for S in STAKES:
                    for fee, send, tag in ((0.0, 0, 'gross'), (FEE, SEND, 'net')):
                        sn = S * (1 - fee)
                        tok = Be * sn / (Xe + sn)
                        out = (X[jj] + sn) * tok / B[jj] * (1 - fee)
                        pnl = (out - S) * 1e9 - 2 * send        # lamports
                        rows.append((block, day, rule, S, tag, pnl, ssb, nearby))
    if COUNTS: return
    R = pd.DataFrame(rows, columns=['block', 'day', 'rule', 'stake', 'cost', 'pnl_lam', 'ssb', 'nearby'])
    R.to_parquet(f'{OUT}/trades.parquet', index=False)
    json.dump(cens, open(f'{OUT}/censored.json', 'w'))
    stats(R)

def cellstats(sol_pnl, day):
    x = np.asarray(sol_pnl, float); n = len(x)
    if n == 0: return dict(n=0)
    srt = np.sort(x)[::-1]
    dsum = pd.Series(x).groupby(np.asarray(day)).sum()
    return dict(n=n, days=int(len(dsum)), pos_days=int((dsum > 0).sum()), mean=float(x.mean()), total=float(x.sum()),
                ci90_lo=A.boot_lo(x), ex_top3=float(x.sum() - srt[:3].sum()), ex_best_day=float(x.sum() - dsum.max()), median=float(np.median(x)))

def stats(R):
    res = []
    for (rule, S, cost), g in R.groupby(['rule', 'stake', 'cost']):
        for blk in BLOCKS + ['pooled']:
            h = g if blk == 'pooled' else g[g['block'] == blk]
            if not len(h): continue
            pnl = h['pnl_lam'].values.astype(float)
            if cost == 'gross':
                views = {'nofail': pnl}
            else:
                fl, pr, c, p = A.fail_legs(pnl, np.ones(len(h), bool), h['ssb'].values, h['nearby'].values, send=SEND)
                views = {'flat15': fl, 'pressure1': pr}
            for fm, v in views.items():
                d = cellstats(v / 1e9, h['day'].values); d.update(rule=rule, stake=S, cost=cost, fail=fm, block=blk)
                if fm == 'pressure1': d['press_mean_p'] = float(p.mean()); d['press_intercept'] = float(c)
                res.append(d)
    json.dump(res, open(f'{OUT}/summary.json', 'w'), indent=1)
    t = pd.DataFrame(res)
    cols = ['block', 'rule', 'stake', 'cost', 'fail', 'n', 'days', 'pos_days', 'mean', 'ci90_lo', 'ex_top3', 'ex_best_day', 'median', 'total']
    open(f'{OUT}/TABLE.md', 'w').write(t[cols].to_string(index=False))
    print(t[cols].to_string(), flush=True)

run()
