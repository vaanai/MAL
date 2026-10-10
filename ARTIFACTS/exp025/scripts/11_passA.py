"""C1 v2 pass A, one graduation day per call:  python 11_passA.py <YYYY-MM-DD>
Reads the tape directly (no extract written): the canonical-pool PumpSwap prints of every universe mint graduating that day, hours D T00 .. D+2 T01.
For each mint, a decision grid at whole UTC minutes from grad + 10 min to grad + 24 h (clipped so decision + 1 h hold + 5 min stays inside the tape
segment). Features use only prints with slot < decision slot (decision slot = first slot whose block_time >= T).
Outputs (zstd):
  out/grid/<D>.parquet  every ALIVE point (>= 1 print in the previous 60 min): stage-1 features + forward labels (labels only for recall/precision)
  out/cand/<D>.parquet  superset points: honest fills (1.3 s / 1.9 s, 8 exits) + stage-2 expensive features
  out/mout/<D>.parquet  per-mint outcome at grad + 6 h (only for the creator track record of LATER tokens, used as-of grad + 6 h)
"""
import sys, os, time, math
import numpy as np, pandas as pd, duckdb
sys.path.insert(0, os.path.dirname(__file__))
from common2 import *

D = sys.argv[1]
for sub in ('grid', 'cand', 'mout'):
    os.makedirs(f'{O}/out/{sub}', exist_ok=True)
t00 = time.time()
U = pd.read_parquet(f'{O}/work/universe.parquet')
U = U[U.gday == D].sort_values('mid').reset_index(drop=True)
d0 = pd.Timestamp(D, tz='UTC')
hrs = [(d0 + pd.Timedelta(hours=h)).strftime('%Y-%m-%dT%H') for h in range(0, 50)]
fs = [f'{TAPE}/trades/{h}.parquet' for h in hrs if os.path.exists(f'{TAPE}/trades/{h}.parquet')]
L = "['" + "','".join(fs) + "']"
con = duckdb.connect()
con.execute(f"SET memory_limit='6GB'; SET threads=3; SET temp_directory='{TMP}'; SET max_temp_directory_size='8GB'; SET preserve_insertion_order=false")
con.register('U_df', U[['mid', 'mint', 'pool']])
con.execute("CREATE TEMP TABLE up AS SELECT * FROM U_df")
df = con.execute(f"""SELECT up.mid, t.slot, t.block_time bt, (t.side='buy') isbuy, t.sol_lamports::DOUBLE sol, t.token_raw tok, t.quote_reserve q,
        t.base_reserve b, hash(t.trader) th
     FROM read_parquet({L}, file_row_number=true) t JOIN up ON t.pool = up.pool AND t.mint = up.mint
     WHERE t.venue = 'pumpswap'
     ORDER BY up.mid, t.slot, t.tx_index, CASE WHEN t.tx_index IS NULL THEN t.file_row_number ELSE t.event_index END""").df()
clock = con.execute(f"""SELECT block_time bt, min(slot) smin, any_value(block) blk FROM read_parquet({L}) WHERE block_time IS NOT NULL GROUP BY 1 ORDER BY 1""").df()
CK_BT = clock.bt.values.astype(np.int64); CK_S = clock.smin.values.astype(np.int64)
# seconds per slot per UTC hour, measured on the clock
hsec = {}
hh = CK_BT // 3600
for h in np.unique(hh):
    m = hh == h
    b_, s_ = CK_BT[m], CK_S[m]
    if b_[-1] - b_[0] >= 1800 and s_[-1] > s_[0]:
        hsec[int(h)] = (b_[-1] - b_[0]) / (s_[-1] - s_[0])
blk_of_hour = {int(h): b for h, b in zip(hh, clock.blk.values)}


def s_slot_at(t):
    h = int(t // 3600)
    if h in hsec:
        return hsec[h]
    ks = np.array(sorted(hsec)); return hsec[int(ks[np.argmin(np.abs(ks - h))])]


def dec_slot(t):
    i = np.searchsorted(CK_BT, t, 'left')
    return CK_S[np.clip(i, 0, len(CK_S) - 1)]


# prior-day wallet ledgers (tape days strictly before the decision day), restricted to the traders that print in these pools
ths = pd.DataFrame({'th': np.unique(df.th.values)})
con.register('ths_df', ths); con.execute("CREATE TEMP TABLE ths AS SELECT * FROM ths_df")
wdays = sorted(os.path.basename(p)[:10] for p in os.listdir(f'{O}/wl') if p.endswith('.parquet'))
LED = {}
for dd in (D, (d0 + pd.Timedelta(days=1)).strftime('%Y-%m-%d')):
    prior = [w for w in wdays if w < dd]
    if not prior:
        LED[dd] = None; continue
    WL = "['" + "','".join(f'{O}/wl/{w}.parquet' for w in prior) + "']"
    x = con.execute(f"""SELECT w.th, sum(n) n, sum(nbond) nbond, sum(cash) cash, sum(nwin) nwin, sum(nrt) nrt, count(*) ndays, sum(buy) buy
                        FROM read_parquet({WL}) w SEMI JOIN ths ON w.th = ths.th GROUP BY 1""").df()
    LED[dd] = (pd.Index(x.th.values), x[['n', 'nbond', 'cash', 'nwin', 'nrt', 'ndays', 'buy']].to_numpy(np.float64))
print(D, 'mints', len(U), 'rows', len(df), 'files', len(fs), 'load s', round(time.time() - t00, 1), flush=True)

mids = df.mid.values
starts = np.searchsorted(mids, U.mid.values, 'left'); ends = np.searchsorted(mids, U.mid.values, 'right')


def sparse_max(a):
    lv = [a]
    k = 1
    while 2 * k <= len(a):
        p = lv[-1]; lv.append(np.maximum(p[:-k], p[k:])); k *= 2
    return lv


def rmax(lv, lo, hi, default):
    """max a[lo:hi] (vectorized); default where hi <= lo"""
    out = np.full(len(lo), default, float)
    ok = hi > lo
    if ok.any():
        l, h = lo[ok], hi[ok]
        ln = h - l
        j = np.floor(np.log2(ln)).astype(int)
        r = np.empty(len(l))
        for jj in np.unique(j):
            m = j == jj; t = lv[jj]
            r[m] = np.maximum(t[l[m]], t[h[m] - (1 << jj)])
        out[ok] = r
    return out


grid_rows = []; cand_rows = []; mout = []
CAPMAX = max(e[5] for e in EXITS)
for mi, (r, a, z) in enumerate(zip(U.itertuples(), starts, ends)):
    n = z - a
    if n < 2:
        continue
    g0 = int(r.g)
    seg = seg_of(g0)
    if seg < 0:
        continue
    V = float(r.v)
    slot = df.slot.values[a:z].astype(np.int64)
    btr = df.bt.values[a:z].astype(float)
    bt = np.where(np.isnan(btr), -1, btr).astype(np.int64)
    if bt[0] < 0:
        bt[0] = g0
    bt = np.maximum.accumulate(bt)
    isb = df.isbuy.values[a:z].astype(bool)
    sol = df.sol.values[a:z].astype(np.float64)
    tok = df.tok.values[a:z].astype(np.float64)
    q = df.q.values[a:z].astype(np.float64); b = df.b.values[a:z].astype(np.float64)
    th = df.th.values[a:z]
    if isb[-1]:
        qf, bf = q[-1] + V + sol[-1] * (1 - G_FEE), b[-1] - tok[-1]
    else:
        qf, bf = q[-1] + V - sol[-1] / (1 - G_FEE), b[-1] + tok[-1]
    qpre = np.concatenate([q + V, [qf]]); bpre = np.concatenate([b, [bf]])
    if (qpre <= 0).any() or (bpre <= 0).any() or not np.isfinite(qpre).all() or not np.isfinite(bpre).all():
        continue
    ppre = qpre / bpre
    ppost = ppre[1:]
    p_grad = ppre[0]
    j6 = int(np.searchsorted(bt, g0 + 6 * 3600, 'left'))
    mout.append((r.mid, r.mint, r.ch, g0, float(ppost[:max(j6, 1)].max() / p_grad), float(ppre[j6] / p_grad), j6))
    uniq, tidx = np.unique(th, return_inverse=True)
    first_any = np.zeros(n, bool); _, fi_any = np.unique(tidx, return_index=True); first_any[fi_any] = True
    bidx = np.where(isb)[0]
    first_buy = np.zeros(n, bool)
    if len(bidx):
        _, fb = np.unique(tidx[bidx], return_index=True); first_buy[bidx[fb]] = True
    cz = lambda x: np.concatenate([[0.0], np.cumsum(x)])
    cs_vol = cz(sol); cs_bs = cz(np.where(isb, sol, 0.0)); cs_ss = cz(np.where(isb, 0.0, sol))
    cs_nb = cz(isb.astype(float)); cs_new = cz(first_buy.astype(float)); cs_any = cz(first_any.astype(float))
    stok = np.where(isb, tok, -tok)
    t_end = min(g0 + GRID_END, SEGS[seg][1] - MAX_HOLD - MARGIN)
    T = np.arange((g0 + GRID_START + 59) // 60 * 60, t_end + 1, 60, dtype=np.int64)
    if len(T) == 0:
        continue
    SD = dec_slot(T)
    I1 = np.searchsorted(slot, SD, 'left')
    W = lambda w: np.minimum(np.searchsorted(bt, T - w, 'left'), I1)
    I60 = W(3600); alive = (I1 - I60) > 0
    if not alive.any():
        continue
    T, SD, I1, I60 = T[alive], SD[alive], I1[alive], I60[alive]
    I1m = W(60); I5 = W(300); I10 = W(600); I15 = W(900)
    v1 = (cs_vol[I1] - cs_vol[I1m]) / 1e9; v5 = (cs_vol[I1] - cs_vol[I5]) / 1e9; v15 = (cs_vol[I1] - cs_vol[I15]) / 1e9; v60 = (cs_vol[I1] - cs_vol[I60]) / 1e9
    bs5 = (cs_bs[I1] - cs_bs[I5]) / 1e9; ss5 = (cs_ss[I1] - cs_ss[I5]) / 1e9
    bsp5 = (cs_bs[I5] - cs_bs[I10]) / 1e9; ssp5 = (cs_ss[I5] - cs_ss[I10]) / 1e9
    nb5 = cs_nb[I1] - cs_nb[I5]; n5 = (I1 - I5).astype(float)
    new5 = cs_new[I1] - cs_new[I5]; newp5 = cs_new[I5] - cs_new[I10]
    spot = ppre[I1]; qreal = (qpre[I1] - V) / 1e9
    p5 = ppre[I5]; p15 = ppre[I15]; p60 = ppre[I60]
    J60 = np.searchsorted(bt, T + 3600, 'left'); J30 = np.searchsorted(bt, T + 1800, 'left'); J10 = np.searchsorted(bt, T + 600, 'left')
    lv = sparse_max(ppost)
    fmax60 = rmax(lv, I1, J60, np.nan) / spot; fmax30 = rmax(lv, I1, J30, np.nan) / spot; fmax10 = rmax(lv, I1, J10, np.nan) / spot
    fmax60 = np.where(np.isnan(fmax60), 1.0, fmax60); fmax30 = np.where(np.isnan(fmax30), 1.0, fmax30); fmax10 = np.where(np.isnan(fmax10), 1.0, fmax10)
    fend60 = ppre[J60] / spot
    surge = v5 / (v60 / 12.0 + 0.05)
    net5 = bs5 - ss5; netp5 = bsp5 - ssp5
    g = pd.DataFrame(dict(mid=r.mid, t=T, age=(T - g0).astype(np.int32), v1=v1, v5=v5, v15=v15, v60=v60, bs5=bs5, ss5=ss5, net5=net5, netp5=netp5,
                          nb5=nb5, n5=n5, new5=new5, newp5=newp5, qreal=qreal, r5=spot / p5 - 1, r15=spot / p15 - 1, r60=spot / p60 - 1,
                          rgrad=spot / p_grad, surge=surge, fmax10=fmax10, fmax30=fmax30, fmax60=fmax60, fend60=fend60))
    pre = (v5 >= 0.5) & ((surge >= 1.5) | (new5 >= 8))
    g['pre'] = pre
    grid_rows.append(g)
    if not pre.any():
        continue
    # ---------------- superset points: honest fills + expensive features
    ath_run = np.maximum.accumulate(ppost); atl_run = np.minimum.accumulate(ppost)
    lr = np.diff(np.log(ppost), prepend=np.log(p_grad))
    cs_lr2 = cz(lr * lr)
    ch = np.uint64(r.ch) if r.ch is not None and r.ch == r.ch else None
    is_cr = (th == ch) if ch is not None else np.zeros(n, bool)
    cs_cr_sell = cz(np.where(is_cr & ~isb, tok, 0.0)); cs_cr_buy = cz(np.where(is_cr & isb, tok, 0.0))
    bought_cum = None
    for j in np.where(pre)[0]:
        t = int(T[j]); sd = int(SD[j]); i1 = int(I1[j]); i5 = int(I5[j]); i15 = int(I15[j]); i60 = int(I60[j])
        dday = day_of(t); s_slot = s_slot_at(t)
        rec = dict(mid=r.mid, t=t, day=dday, seg=seg, blk=blk_of_hour.get(t // 3600, ''), s_slot=s_slot)
        lag = max(0, math.ceil(EXIT_LAG_S / s_slot - 1e-9))
        for lname, lat in (('p', LAT_P), ('b', LAT_B)):
            k = int(round(lat / s_slot)); X = sd + k
            je = int(np.searchsorted(slot, X + 1, 'left'))
            qe, be = qpre[je], bpre[je]
            f = fee_frac(qe, be); net = SIZE * (1 - f); tokens = be * net / (qe + net); mark = (qe + net) / (be - tokens)
            exr = (SIZE / tokens) / spot[j]
            ix = int(np.searchsorted(slot, X, 'left'))
            lo2 = int(np.searchsorted(slot, X - int(round(2.0 / s_slot)), 'left'))
            rec[f'ssb_{lname}'] = int(isb[ix:je].sum()); rec[f'nearby_{lname}'] = float(np.where(isb[lo2:je], sol[lo2:je], 0).sum())
            rec[f'exr_{lname}'] = float(exr)
            if exr > GUARD:
                rec[f'guard_{lname}'] = 1
                for e in EXITS:
                    rec[f'pnl_{e[0]}_{lname}'] = -float(SEND_FEE); rec[f'xt_{e[0]}_{lname}'] = t; rec[f'xk_{e[0]}_{lname}'] = 0
                continue
            rec[f'guard_{lname}'] = 0
            il = je - 1
            bt_land = bt[il] if il >= 0 else t
            icmax = int(np.searchsorted(bt, bt_land + CAPMAX, 'left'))
            if icmax > je:
                qa = qpre[je + 1:icmax + 1] + net; ba = bpre[je + 1:icmax + 1] - tokens
                rel = (qa / ba) / mark          # rel[i] = post-trade spot after print je+i, over the mark
                peak = np.maximum.accumulate(np.maximum(rel, 1.0))
            for en, kind, tp, sl, tr, cap in EXITS:
                icap = int(np.searchsorted(bt, bt_land + cap, 'left'))
                hit = -1; xk = 0
                m = icap - je
                if m > 0 and kind != 'time':
                    rr = rel[:m]
                    if kind == 'tpsl':
                        cond = (rr >= 1 + tp) | (rr <= 1 - sl)
                    else:
                        cond = rr <= peak[:m] * (1 - tr)
                    ii = int(np.argmax(cond))
                    if cond[ii]:
                        hit = je + ii; xk = 1 if rr[ii] >= 1 else 2
                if hit >= 0:
                    fi = int(np.searchsorted(slot, slot[hit] + lag + 1, 'left'))
                elif icap < n:
                    fi = int(np.searchsorted(slot, slot[icap] + lag + 1, 'left')); xk = 3
                else:
                    fi = n; xk = 4
                qa_f, ba_f = qpre[fi] + net, bpre[fi] - tokens
                gross = tokens * qa_f / (ba_f + tokens)
                val = gross * (1 - fee_frac(qa_f, ba_f))
                rec[f'pnl_{en}_{lname}'] = float(val - SIZE - 2 * SEND_FEE)
                rec[f'xt_{en}_{lname}'] = int(bt[min(fi, n - 1)]); rec[f'xk_{en}_{lname}'] = xk
        # trajectory shape
        sp = spot[j]
        rec.update(r_ath=float(sp / ath_run[i1 - 1]) if i1 > 0 else 1.0, r_atl=float(sp / atl_run[i1 - 1]) if i1 > 0 else 1.0,
                   ath_grad=float(ath_run[i1 - 1] / p_grad) if i1 > 0 else 1.0,
                   vol15=float(np.sqrt(max(cs_lr2[i1] - cs_lr2[i15], 0.0) / max(i1 - i15, 1))),
                   vol60=float(np.sqrt(max(cs_lr2[i1] - cs_lr2[i60], 0.0) / max(i1 - i60, 1))),
                   ncum=i1, ntr_cum=float(cs_any[i1]), vcum=float(cs_vol[i1] / 1e9),
                   maxbuy5=float(np.where(isb[i5:i1], sol[i5:i1], 0).max() / 1e9) if i1 > i5 else 0.0,
                   avgbuy5=float(bs5[j] / max(nb5[j], 1)), buyshare5=float(bs5[j] / max(v5[j], 1e-9)),
                   cr_sold=float(cs_cr_sell[i1] / 1e15), cr_bought=float(cs_cr_buy[i1] / 1e15))
        # holder concentration from cumulative PumpSwap flows (pre-grad holders are not seen)
        hold = np.bincount(tidx[:i1], weights=stok[:i1], minlength=len(uniq))
        pos = np.sort(hold[hold > 0])[::-1]
        tot = pos.sum() if len(pos) else 0.0
        rec.update(h_npos=len(pos), h_top1=float(pos[:1].sum() / tot) if tot > 0 else np.nan, h_top5=float(pos[:5].sum() / tot) if tot > 0 else np.nan,
                   h_top10=float(pos[:10].sum() / tot) if tot > 0 else np.nan, h_pos_frac_supply=float(tot / 1e15))
        sidx = np.arange(i5, i1)[~isb[i5:i1]]
        if len(sidx):
            bought_ps = np.bincount(tidx[:i1][isb[:i1]], minlength=len(uniq)) > 0
            rec['sell_curveholder_share'] = float(sol[sidx][~bought_ps[tidx[sidx]]].sum() / max(sol[sidx].sum(), 1))
        else:
            rec['sell_curveholder_share'] = np.nan
        # wallet intelligence (ledger of tape days strictly before the decision day)
        led = LED.get(dday)
        if led is not None:
            idxL, M = led
            for wname, ia in (('5', i5), ('15', i15)):
                wsl = np.arange(ia, i1)
                skill_sol = {}
                for side, sx in (('b', wsl[isb[wsl]]), ('s', wsl[~isb[wsl]])):
                    pre_ = f'w{side}{wname}_'
                    if len(sx) == 0:
                        rec[pre_ + 'n'] = 0; skill_sol[side] = 0.0; continue
                    u, inv = np.unique(tidx[sx], return_inverse=True)
                    usol = np.bincount(inv, weights=sol[sx])
                    gi = idxL.get_indexer(uniq[u])
                    known = gi >= 0
                    st = np.full((len(u), M.shape[1]), np.nan); st[known] = M[gi[known]]
                    nn, nbond, cash, nwin, nrt, ndays, buy = st.T
                    skill = known & (cash > 0)
                    rec[pre_ + 'n'] = len(u)
                    rec[pre_ + 'new'] = float((~known).mean())
                    rec[pre_ + 'skill'] = float(skill.sum() / max(known.sum(), 1))
                    rec[pre_ + 'skill_sol'] = float(usol[skill].sum() / max(usol.sum(), 1))
                    skill_sol[side] = float(usol[skill].sum() / 1e9)
                    rec[pre_ + 'cash_med'] = float(np.nanmedian(cash)) if known.any() else np.nan
                    rec[pre_ + 'roi_med'] = float(np.nanmedian(cash / np.maximum(buy, 0.1))) if known.any() else np.nan
                    wr = nwin / np.where(nrt >= 5, nrt, np.nan)
                    rec[pre_ + 'winrate'] = float(np.nanmean(wr)) if np.isfinite(wr).any() else np.nan
                    rec[pre_ + 'bot'] = float((known & (nn / np.maximum(ndays, 1) >= 500)).mean())
                    rec[pre_ + 'sniper'] = float((known & (nn >= 20) & (nbond / np.maximum(nn, 1) >= 0.9)).mean())
                    rec[pre_ + 'actmed'] = float(np.nanmedian(nn / np.maximum(ndays, 1))) if known.any() else np.nan
                rec[f'sk_net{wname}'] = skill_sol['b'] - skill_sol['s']
        cand_rows.append(rec)
    if mi % 200 == 0:
        print(D, mi, len(U), round(time.time() - t00, 1), flush=True)

G = pd.concat(grid_rows, ignore_index=True) if grid_rows else pd.DataFrame()
for c_ in G.columns:
    if G[c_].dtype == np.float64:
        G[c_] = G[c_].astype(np.float32)
G.to_parquet(f'{O}/out/grid/{D}.parquet', index=False, compression='zstd')
C = pd.DataFrame(cand_rows)
for c_ in C.columns:
    if C[c_].dtype == np.float64 and not c_.startswith('pnl_'):
        C[c_] = C[c_].astype(np.float32)
C.to_parquet(f'{O}/out/cand/{D}.parquet', index=False, compression='zstd')
pd.DataFrame(mout, columns=['mid', 'mint', 'ch', 'g', 'max6', 'end6', 'n6']).to_parquet(f'{O}/out/mout/{D}.parquet', index=False, compression='zstd')
print(D, 'DONE grid', len(G), 'pre', int(G.pre.sum()) if len(G) else 0, 'cand', len(C), 'secs', round(time.time() - t00, 1), flush=True)
