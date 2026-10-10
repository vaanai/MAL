"""C1-NF VERIFY step v2: an INDEPENDENT simulator (written for this VERIFY; shares no code with C1's 11_passA.py).
Audit venv (duckdb, pandas, numpy). Input: v/cands.npz (rows the frozen model selected: confirmation pred > 0.02 on stage 1, and the
discovery walk-forward selections). For each row it reads the RAW exploration tape (hour files) and recomputes from scratch:
  - the canonical PumpSwap pool (pool of the mint with the earliest first print slot; ties by address), compared with universe.pool;
  - V from the pool-account maps (pool_v_0909 -> pool_v_exp016 -> pool_v_0814 -> pool_v; outside [0, 30 SOL] -> 0);
  - the decision slot SD = min slot over tape rows with block_time >= t (suffix minimum, all venues);
  - h_top1 / h_top5: largest positive net PumpSwap token position (buys + tok, sells - tok) among wallets with a print in the canonical pool
    with slot < SD, over the sum of positive positions (pre-graduation holders not seen); v5, new5, qreal as cross-checks;
  - fills: landing slot X = SD + round(lat / s), s = seconds per slot of the decision's UTC hour (clock endpoints, span >= 1800 s);
    buy bound END (after every print of slot X), START (before them), WORST (the highest-price state inside slot X);
    PumpSwap fee tier on (Q incl. V, B) from the repo's tools/paper_curve_math.py; 0.25 SOL; guard: SIZE / tokens > 1.15 x decision spot.
    Exit: deadline time = time(X) + 300 s on the CLOCK (not on the pool's next print); sell slot = SD(deadline) + ceil(lag / s);
    sell bound END / START / WORST (lowest-value state inside the sell slot); own trade applied (+net quote, -tokens) to every later state.
    Pressure inputs: buy prints in slot X; buy lamports with slot in [X - round(2 / s), X].
Outputs v/sim.parquet (zstd). P&L columns are GROSS of send fees (lamports): fees and rent are applied in v3."""
import sys, os, json, math, time, glob, importlib.util
import numpy as np, pandas as pd, duckdb

V = '/data/mal/hunt-1008/c1nf-verify'
TAPE = '/data/mal/audit-1008/tape/trades'
PV = '/data/mal/pumpswap-virtual'
SIZE = 250_000_000
GUARD = 1.15
spec = importlib.util.spec_from_file_location('pcm', '/home/claude/.miscusi/repos/vaanai/MAL/tools/paper_curve_math.py')
pcm = importlib.util.module_from_spec(spec); sys.modules['pcm'] = pcm; spec.loader.exec_module(pcm)
TIER_T = np.array([a for a, _ in pcm.PUMPSWAP_SOL_FEE_TIERS], float); TIER_P = np.array([b for _, b in pcm.PUMPSWAP_SOL_FEE_TIERS], float)


def fee(Q, B):
    mc = (Q / 1e9) / (B / 1e6) * 1e9
    i = np.searchsorted(TIER_T, mc + 1e-9, side='right') - 1
    return TIER_P[max(0, min(int(i), len(TIER_P) - 1))] / 1e6


# V maps (exploration: no forward-1002 file is opened)
VMAP = {}
for fn in ('pool_v.json', 'pool_v_0814.json', 'pool_v_exp016.json', 'pool_v_0909.json'):   # later files override earlier ones
    for k, v in json.load(open(f'{PV}/{fn}'))['v'].items():
        if v is not None:
            VMAP[k] = v


def v_of(pool):
    v = VMAP.get(pool)
    if v is None or not (0 <= v <= 30e9):
        return 0.0
    return float(v)


CANDS = sys.argv[1] if len(sys.argv) > 1 else f'{V}/v/cands.npz'
SIMOUT = sys.argv[2] if len(sys.argv) > 2 else f'{V}/v/sim.parquet'
z = np.load(CANDS, allow_pickle=False)
R = pd.DataFrame({'idx': z['idx'], 'isconf': z['isconf'], 't': z['t'], 'mid': z['mid']})
U = pd.read_parquet(f'{V}/work/universe.parquet', columns=['mid', 'mint', 'pool', 'v', 'g', 'gday'])
R = R.merge(U, on='mid', how='left')
assert R.mint.notna().all()
print('rows', len(R), 'mints', R.mint.nunique(), 'gdays', R.gday.nunique(), flush=True)

con = duckdb.connect()
con.execute(f"SET memory_limit='6GB'; SET threads=2; SET temp_directory='{V}/tmp_duck'; SET preserve_insertion_order=false")
LATS = (('p', 1.3), ('b', 1.9)) + ((('l3', 3.0), ('l4', 4.0)) if os.environ.get('C1NF_EXTRA_LATS') else ())
LAGS = (('l055', 0.55), ('l2', 2.0), ('l5', 5.0))
out = []
t00 = time.time()
for gd, Rg in R.groupby('gday'):
    d0 = pd.Timestamp(gd, tz='UTC')
    tmax = int(Rg.t.max()) + 400
    hrs = []
    h = d0
    while h.timestamp() <= tmax and h < d0 + pd.Timedelta(hours=50):
        hrs.append(h.strftime('%Y-%m-%dT%H')); h += pd.Timedelta(hours=1)
    fs = [f'{TAPE}/{x}.parquet' for x in hrs if os.path.exists(f'{TAPE}/{x}.parquet')]
    L = "['" + "','".join(fs) + "']"
    mints = pd.DataFrame({'mint': Rg.mint.unique()})
    con.register('mm', mints)
    P = con.execute(f"""SELECT mint, pool, slot, block_time bt, tx_index ti, event_index ei, file_row_number frn, side = 'buy' isb, sol_lamports sol,
                               token_raw tok, quote_reserve q, base_reserve b, trader
                        FROM read_parquet({L}, file_row_number=true) WHERE venue = 'pumpswap' AND mint IN (SELECT mint FROM mm)""").df()
    CK = con.execute(f"SELECT block_time bt, min(slot) s FROM read_parquet({L}) WHERE block_time IS NOT NULL GROUP BY 1 ORDER BY 1").df()
    con.unregister('mm')
    ck_bt = CK.bt.values.astype(np.int64); ck_s = CK.s.values.astype(np.int64)
    suf = np.minimum.accumulate(ck_s[::-1])[::-1]          # SD(T) = min slot over rows with block_time >= T
    o = np.argsort(ck_s, kind='stable'); s_sorted = ck_s[o]; bt_run = np.maximum.accumulate(ck_bt[o])   # time(X) = latest block_time with min slot <= X
    hsec = {}
    for hh in np.unique(ck_bt // 3600):
        m = (ck_bt // 3600) == hh
        b_, s_ = ck_bt[m], ck_s[m]
        if b_[-1] - b_[0] >= 1800 and s_[-1] > s_[0]:
            hsec[int(hh)] = (b_[-1] - b_[0]) / (s_[-1] - s_[0])
    hk = np.array(sorted(hsec))

    def sps(t):
        h_ = int(t // 3600)
        return hsec[h_] if h_ in hsec else hsec[int(hk[np.argmin(np.abs(hk - h_))])]

    def SD(T):
        i = int(np.searchsorted(ck_bt, T, 'left'))
        return int(suf[min(i, len(suf) - 1)])

    def time_of(X):
        i = int(np.searchsorted(s_sorted, X, 'right')) - 1
        return int(bt_run[max(i, 0)])

    GI = P.groupby('mint', sort=False).indices
    for mint, Rm in Rg.groupby('mint'):
        if mint not in GI:
            for r in Rm.itertuples():
                out.append(dict(idx=r.idx, err='noprints'))
            continue
        Pm = P.iloc[GI[mint]]
        first = Pm.groupby('pool').slot.min().reset_index().sort_values(['slot', 'pool'])
        cpool = first.pool.iloc[0]
        upool = Rm.pool.iloc[0]
        Pm = Pm[Pm.pool == cpool].copy()
        Pm['ti2'] = Pm.ti.fillna(-1).astype(np.int64); Pm['ei2'] = Pm.ei.fillna(-1).astype(np.int64)
        Pm = Pm.sort_values(['slot', 'ti2', 'ei2', 'frn'], kind='stable')
        Vp = v_of(cpool)
        slot = Pm.slot.values.astype(np.int64)
        btr = Pm.bt.values.astype(float)
        bt = np.where(np.isnan(btr), -np.inf, btr); bt[0] = bt[0] if np.isfinite(bt[0]) else float(Rm.g.iloc[0]); bt = np.maximum.accumulate(bt)
        isb = Pm.isb.values.astype(bool); sol = Pm.sol.values.astype(float); tok = Pm.tok.values.astype(float)
        Qp = Pm.q.values.astype(float) + Vp; Bp = Pm.b.values.astype(float)
        Bl = Bp[-1] - tok[-1] if isb[-1] else Bp[-1] + tok[-1]
        Ql = Qp[-1] * Bp[-1] / Bl                       # post-state of the last print: constant product (only used when the sell is after the last print)
        SQ = np.append(Qp, Ql); SB = np.append(Bp, Bl)
        n = len(slot)
        codes, tr_idx = np.unique(Pm.trader.values, return_inverse=True)
        stok = np.where(isb, tok, -tok)
        # first-buy flags for new5
        fb = np.zeros(n, bool)
        bi_ = np.where(isb)[0]
        if len(bi_):
            _, first_b = np.unique(tr_idx[bi_], return_index=True); fb[bi_[first_b]] = True
        cs_sol = np.concatenate([[0.0], np.cumsum(sol)]); cs_fb = np.concatenate([[0.0], np.cumsum(fb)])
        buy_sol = np.where(isb, sol, 0.0); cs_bsol = np.concatenate([[0.0], np.cumsum(buy_sol)])
        hold = np.zeros(len(codes)); ptr = 0
        for r in Rm.sort_values('t').itertuples():
            t = int(r.t)
            sd = SD(t)
            i1 = int(np.searchsorted(slot, sd, 'left'))
            if i1 > ptr:
                np.add.at(hold, tr_idx[ptr:i1], stok[ptr:i1]); ptr = i1
            pos = hold[hold > 0]
            if len(pos):
                tot = pos.sum(); top1 = pos.max() / tot
                top5 = (np.partition(pos, -5)[-5:].sum() if len(pos) > 5 else tot) / tot
            else:
                top1 = top5 = np.nan
            i5 = min(int(np.searchsorted(bt, t - 300, 'left')), i1)
            spot = SQ[i1] / SB[i1]
            rec = dict(idx=r.idx, mint=mint, t=t, pool_match=bool(cpool == upool), V=Vp, sd=sd, h1=top1, h5=top5, v5=(cs_sol[i1] - cs_sol[i5]) / 1e9,
                       new5=cs_fb[i1] - cs_fb[i5], qreal=(SQ[i1] - Vp) / 1e9, nprint=n, err='')
            s_t = sps(t)
            for ln, lat in LATS:
                k = int(round(lat / s_t)); X = sd + k
                ix = int(np.searchsorted(slot, X, 'left')); je = int(np.searchsorted(slot, X, 'right'))
                rec[f'ssb_{ln}'] = int(isb[ix:je].sum())
                lo2 = int(np.searchsorted(slot, X - int(round(2.0 / s_t)), 'left'))
                rec[f'nearby_{ln}'] = float(cs_bsol[je] - cs_bsol[lo2])
                tX = time_of(X)
                cand_b = {'END': je, 'START': ix, 'WORST': ix + int(np.argmax(SQ[ix:je + 1] / SB[ix:je + 1]))}
                for bnd, jb in cand_b.items():
                    Qb, Bb = SQ[jb], SB[jb]
                    net = SIZE * (1 - fee(Qb, Bb)); tokens = Bb * net / (Qb + net)
                    guarded = (SIZE / tokens) > GUARD * spot
                    lags = LAGS if bnd == 'END' else LAGS[:1]
                    for lgn, lg in lags:
                        tag = f'{ln}_{bnd}_{lgn}'
                        if guarded:
                            rec[f'g_{tag}'] = 1; rec[f'pnl_{tag}'] = 0.0; rec[f'xt_{tag}'] = t; continue
                        tD = tX + 300
                        XS = SD(tD) + max(0, math.ceil(lg / sps(tD) - 1e-9))
                        is_ = int(np.searchsorted(slot, XS, 'left')); se = int(np.searchsorted(slot, XS, 'right'))
                        Qs = SQ[is_:se + 1] + net; Bs = SB[is_:se + 1] - tokens
                        vals = np.array([tokens * a / (b_ + tokens) * (1 - fee(a, b_)) for a, b_ in zip(Qs, Bs)])
                        val = vals[-1] if bnd == 'END' else vals[0] if bnd == 'START' else vals.min()
                        rec[f'g_{tag}'] = 0; rec[f'pnl_{tag}'] = float(val - SIZE); rec[f'xt_{tag}'] = int(time_of(XS))
                        if tag == 'p_END_l055':
                            rec['after_last'] = bool(se >= n); rec['hold_s'] = int(time_of(XS)) - tX
            out.append(rec)
    print(gd, 'rows', len(Rg), 'mints', Rg.mint.nunique(), 'prints', len(P), 'files', len(fs), 'elapsed', round(time.time() - t00, 1), flush=True)
    del P

S = pd.DataFrame(out)
S.to_parquet(SIMOUT, index=False, compression='zstd')
print('DONE rows', len(S), 'errors', int((S.err != '').sum()), 'pool mismatch', int((~S.pool_match.fillna(False)).sum()), 'secs', round(time.time() - t00, 1))
