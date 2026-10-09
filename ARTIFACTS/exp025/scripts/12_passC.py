"""C1 v2 pass C: cross-token stage-2 features for every superset row (mid, t), strictly as-of t.
 - pre-grad (complete at graduation): bonding duration, bonding prints/traders/buy/sell SOL, mayhem flag, has_create
 - creator track record: prior creates, creates in the last 24 h, prior graduations before t, OTHER graduated tokens' 6 h outcomes known by t
   (a token's outcome is known at grad + 6 h; the token's own outcome is excluded)
 - narrative heat: shared name/symbol words among creates in the last 1 h / 6 h and graduations in the last 6 h / 24 h; exact-symbol creates in 1 h
 - market activity: PumpSwap and bonding prints / SOL over the whole minutes in [t - 60 min, t - 1 min), graduations and creates in the last 60 min
Usage: python 12_passC.py <day> [<day> ...]   (reads out/cand/<day>.parquet, writes out/candx/<day>.parquet)"""
import sys, os, re, glob
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from common2 import *
os.makedirs(f'{O}/out/candx', exist_ok=True)
U = pd.read_parquet(f'{O}/work/universe.parquet')
crt = pd.read_parquet(f'{O}/work/creates.parquet')
STOP = {'the', 'coin', 'token', 'sol', 'solana', 'pump', 'fun', 'and', 'for', 'of', 'meme', 'inu', 'official', 'new', 'first', 'just', 'you', 'this'}


def words(name, sym):
    s = f'{name or ""} {sym or ""}'.lower()
    return sorted({w for w in re.findall(r'[a-z0-9]+', s) if len(w) >= 3 and w not in STOP})


crt['w'] = [words(a, b) for a, b in zip(crt.name.values, crt.symbol.values)]
crt['sym'] = crt.symbol.fillna('').str.lower().str.strip()
by_cr = {k: np.sort(v.values) for k, v in crt.groupby('ch').cbt}
grd = crt[crt.gbt.notna()]
g_cr = {k: np.sort(v.values) for k, v in grd.groupby('ch').gbt}
ex = crt[['w', 'cbt', 'gbt']].explode('w').dropna(subset=['w'])
w_c = {k: np.sort(v.values) for k, v in ex.groupby('w').cbt}
w_g = {k: np.sort(v.dropna().values) for k, v in ex[ex.gbt.notna()].groupby('w').gbt}
s_c = {k: np.sort(v.values) for k, v in crt[crt.sym != ''].groupby('sym').cbt}
all_g = np.sort(grd.gbt.values.astype(float)); all_c = np.sort(crt.cbt.values.astype(float))
mo = pd.concat([pd.read_parquet(p) for p in glob.glob(f'{O}/out/mout/*.parquet')], ignore_index=True)
mo['known'] = mo.g + 6 * 3600
mo = mo.sort_values('known')
m_cr = {k: (v.known.values, v.mid.values, np.log(np.maximum(v.max6.values, 1e-9)), np.log(np.maximum(v.end6.values, 1e-9)), (v.max6.values >= 2).astype(float))
        for k, v in mo.groupby('ch')}
mkt = pd.read_parquet(f'{O}/work/mkt.parquet')
MK_T = mkt.mnt.values.astype(np.int64); MK = {c: np.concatenate([[0.0], np.cumsum(mkt[c].values.astype(float))]) for c in ('n_ps', 'v_ps', 'n_bd', 'v_bd')}
UI = U.set_index('mid')
cw = {m: (w, s) for m, w, s in zip(crt.mint.values, crt.w.values, crt.sym.values)}
E = np.array([])


def cnt(arr, lo, hi):
    return int(np.searchsorted(arr, hi, 'left') - np.searchsorted(arr, lo, 'left'))


for D in sys.argv[1:]:
    C = pd.read_parquet(f'{O}/out/cand/{D}.parquet')
    out = []
    for mid, t in zip(C.mid.values, C.t.values):
        u = UI.loc[mid]
        rec = dict(bc_dur=u.bc_dur, bc_n_trades=u.bc_n_trades, bc_n_traders=u.bc_n_traders, bc_buy_sol=u.bc_buy_sol, bc_sell_sol=u.bc_sell_sol,
                   mayhem=float(bool(u.mayhem)) if u.mayhem == u.mayhem and u.mayhem is not None else np.nan, has_create=float(bool(u.has_create)))
        if bool(u.has_create):
            ch = u.ch
            a = by_cr.get(ch, E)
            rec['cr_prev_creates'] = int(np.searchsorted(a, u.cbt, 'left'))
            rec['cr_creates_24h'] = cnt(a, t - 86400, t) - (1 if t - 86400 <= u.cbt < t else 0)
            ga = g_cr.get(ch, E)
            rec['cr_prev_grads'] = int(np.searchsorted(ga, t, 'left')) - (1 if u.g < t else 0)
            if ch in m_cr:
                kt, kmid, lmx, led, big = m_cr[ch]
                kk = int(np.searchsorted(kt, t, 'right'))
                sel = kmid[:kk] != mid
                nk = int(sel.sum())
                rec['cr_known_out'] = nk
                rec['cr_lmax6'] = float(lmx[:kk][sel].mean()) if nk else np.nan
                rec['cr_lend6'] = float(led[:kk][sel].mean()) if nk else np.nan
                rec['cr_big6'] = float(big[:kk][sel].mean()) if nk else np.nan
            else:
                rec['cr_known_out'] = 0
            w, sym = cw.get(u.mint, ([], ''))
            rec['nar_cr1h'] = max([cnt(w_c.get(x, E), t - 3600, t) for x in w] or [0])
            rec['nar_cr6h'] = max([cnt(w_c.get(x, E), t - 6 * 3600, t) for x in w] or [0])
            rec['nar_gr6h'] = max([cnt(w_g.get(x, E), t - 6 * 3600, t) for x in w] or [0])
            rec['nar_gr24h'] = max([cnt(w_g.get(x, E), t - 86400, t) for x in w] or [0])
            rec['nar_sym1h'] = cnt(s_c.get(sym, E), t - 3600, t) if sym else 0
            rec['nar_nwords'] = len(w)
        i0 = np.searchsorted(MK_T, t - 3600, 'left'); i1 = np.searchsorted(MK_T, t - 60, 'left')
        for c in MK:
            rec[f'mk_{c}60'] = MK[c][i1] - MK[c][i0]
        rec['mk_grads60'] = cnt(all_g, t - 3600, t); rec['mk_creates60'] = cnt(all_c, t - 3600, t)
        out.append(rec)
    X = pd.concat([C.reset_index(drop=True), pd.DataFrame(out).astype(np.float32)], axis=1)
    X.to_parquet(f'{O}/out/candx/{D}.parquet', index=False, compression='zstd')
    print(D, len(X), flush=True)
