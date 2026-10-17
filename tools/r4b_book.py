"""r4-b book engine, cost stack and statistics (walk-2 read tool PLAN builder task 3). Numpy only.

Ports, line for line in behaviour (the harness and the prep are the oracle; E-c proves it on the exploration cache):
  * entry / mark states      <- laya-opt/harness/prep_l3.py `build` per-mint loop (mint_states)
  * the hourly top-K engine  <- laya-opt/harness/harness.py `book_xs`            (xs_book, attach_marks)
  * round trip, fail legs    <- hlib.ps_round_trip / press_intercept / legs, with the VERIFY re-score changes
                                (send fee, rent and the DEC-021 haircut are parameters)     (round_trip, press_intercept, fail_legs)
  * statistics               <- hlib.boot_lo, leg_block, plus the day-level t over 24 h blocks      (book_stats, day_t)

Conventions that are easy to get wrong (all pinned in r4b_frozen):
  * PumpSwap reserves are PRE-trade and include the virtual quote reserve V. `vq` may be one number per mint (exploration, static map) or one per print (forward,
    event V). Own buy is re-injected into the exit state; own sell moves the pool.
  * Entry state = state before the first print with slot >= sd + k + 1 (END bound: if none, the derived state after the last print).
    Exit state = same at clock_slot(t) + ceil(0.55 / slot_s - 1e-9) + 1 (lag asserted >= 2).
  * Ranking is lexsort (T, -score, mid): descending score, ties to the lower mint id. A mint sold at a mark is not re-bought at that mark.
  * Haircut (VERIFY): tokens received / (1 + 0.002608), proceeds x (1 - 0.0016). Not the product approximation of PLAN section 5.
  * A guarded buy (exec price > 1.15 x spot) pays one send fee, no rent, no haircut, and is not "filled".
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import r4b_frozen as F  # noqa: E402

_TH = np.array([t for t, _ in F.FEE_TIERS], float)
_TP = np.array([p for _, p in F.FEE_TIERS], float)


# ---------------------------------------------------------------------------------------------------------------- fees and slots
def fee_ppm(Q, B):
    """PumpSwap fee tier on (Q incl. V, B): mcap SOL = q / (b * 1000) * 1e9 (cap_pick_score.fee_ppm)."""
    mcap = np.asarray(Q, float) / (np.asarray(B, float) * 1000.0) * 1e9
    idx = np.searchsorted(_TH, mcap + 1e-9, side="right") - 1
    return _TP[np.clip(idx, 0, len(_TP) - 1)]


def k_slots_vec(lat_s: float, slot_s, rounding: str | None = None) -> np.ndarray:
    """Vector form of r4b_frozen.k_slots (np.round is half to even like Python round)."""
    r = F.K_ROUNDING if rounding is None else rounding
    x = lat_s / np.asarray(slot_s, float)
    if r == "round":
        return np.round(x).astype(np.int64)
    if r == "ceil":
        return np.maximum(0, np.ceil(x - F.SLOT_TOL)).astype(np.int64)
    raise ValueError(f"rounding must be 'round' or 'ceil', got {r!r}")


def exit_lag_vec(slot_s) -> np.ndarray:
    lag = np.ceil(F.EXIT_LAG_S / np.asarray(slot_s, float) - F.SLOT_TOL).astype(np.int64)
    assert lag.size == 0 or int(lag.min()) >= F.MIN_EXIT_LAG_SLOTS, "exit lag below 2 slots"
    return lag


def clock_slot(t, ck_bt, ck_slot):
    """Slot of the first clock entry with block_time >= t (prep_l3.clock_slot); the last entry beyond the end."""
    i = np.minimum(np.searchsorted(ck_bt, t, "left"), len(ck_bt) - 1)
    return ck_slot[i]


def mark_grid(t_min: int, t_max: int, mark: int = 600, tail: int = 7200) -> np.ndarray:
    """prep_l3 mark times for one mint: every `mark` s from the first multiple >= t_min to t_max + tail."""
    return np.arange(((int(t_min) + mark - 1) // mark) * mark, int(t_max) + tail + 1, mark, dtype=np.int64)


# ---------------------------------------------------------------------------------------------------------------- entry / mark states
def mint_states(sl, isbuy, sol, tok, q, b, vq, sd, slot_s_dec, t_marks=None, slot_s_marks=None, clock_slot_marks=None,
                lats=F.LATENCIES_S, rounding=None, nearby_s=F.NEARBY_WINDOW_S):
    """States for one mint from its PumpSwap prints (rows ordered slot, tx index, event index; reserves PRE-trade).

    sl slot, isbuy, sol (lamports), tok, q (quote reserve), b (base reserve): per print. vq: scalar V or per-print virtual quote reserve.
    sd: decision slots (one per decision row); slot_s_dec: seconds per slot at each decision's hour (array or scalar).
    Returns {'spot', 'Qe': {lat: arr}, 'Be': {lat: arr}, 'ssb': {lat: arr}, 'nearby': {lat: arr}} and, when t_marks is given,
    {'Qx', 'Bx'} at the mark times (needs slot_s_marks and clock_slot_marks = clock_slot(t_marks)).
    Entry latency k per r4b_frozen.K_ROUNDING (or `rounding`). ssb = buys in the landing slot x = sd + k; nearby = buy lamports over slots [x - round(2 / s), x]."""
    sl = np.asarray(sl)
    ib = np.asarray(isbuy).astype(bool)
    so = np.asarray(sol).astype(float)
    tk = np.asarray(tok).astype(float)
    qq = np.asarray(q).astype(float)
    bb = np.asarray(b).astype(float)
    n = len(sl)
    assert n > 0, "mint without prints"
    vqa = np.broadcast_to(np.asarray(vq, float), (n,)) if np.ndim(vq) == 0 else np.asarray(vq, float)
    assert len(vqa) == n
    qL = qq[-1] + so[-1] * (1 - F.FINAL_FEE) if ib[-1] else qq[-1] - so[-1] / (1 - F.FINAL_FEE)
    bL = bb[-1] - tk[-1] if ib[-1] else bb[-1] + tk[-1]
    QX = np.append(qq, qL) + np.append(vqa, vqa[-1])          # state before print i (i < n); derived state after the last print at n
    BX = np.append(bb, bL)
    qp = np.where(ib, qq + so * 0.99, qq - so * 1.01) + vqa    # post-trade price numerator (the seed price convention of 02_build)
    bp = np.where(ib, bb - tk, bb + tk)
    bp = np.where(bp > 0, bp, bb)
    pp = qp / bp
    sd = np.asarray(sd)
    s_dec = np.broadcast_to(np.asarray(slot_s_dec, float), sd.shape)
    j = np.searchsorted(sl, sd, "left")
    out = {"spot": pp[np.maximum(j - 1, 0)], "Qe": {}, "Be": {}, "ssb": {}, "nearby": {}}
    cib = np.concatenate([[0], np.cumsum(ib)])
    cbv = np.concatenate([[0], np.cumsum(np.where(ib, so, 0.0))])
    for lat in lats:
        k = k_slots_vec(lat, s_dec, rounding)
        x = sd + k
        je = np.searchsorted(sl, x + 1, "left")
        ix = np.searchsorted(sl, x, "left")
        lo2 = np.searchsorted(sl, x - np.round(nearby_s / s_dec).astype(np.int64), "left")
        out["Qe"][lat] = QX[je]
        out["Be"][lat] = BX[je]
        out["ssb"][lat] = (cib[je] - cib[ix]).astype(float)
        out["nearby"][lat] = cbv[je] - cbv[lo2]
    if t_marks is not None:
        lag = exit_lag_vec(slot_s_marks)
        fi = np.searchsorted(sl, np.asarray(clock_slot_marks) + lag + 1, "left")
        out["Qx"] = QX[fi]
        out["Bx"] = BX[fi]
    return out


# ---------------------------------------------------------------------------------------------------------------- the xs engine
def xs_book(T, mid, score, k: int = F.K, rb: int = F.REBALANCE_S, rank_dtype=None):
    """Hourly top-K cross-section (harness.book_xs, no hysteresis, no min score, no max hold).

    T, mid, score: one entry per decision row. Rows count only if T % rb == 0 and the score is finite. rank_dtype=np.float32 reproduces the harness
    (get_score casts the bag to float32 before ranking); the forward read ranks in float64 (None).
    At every mark: sell held tokens not in the top-K (exit T1 = t), buy top-K not held (a token sold at this mark is not re-bought at it).
    A gap of more than rb between consecutive marks closes everything at last mark + rb ('gap'); the end closes at last mark + rb ('end').
    Returns arrays row, mid, T0, T1, reason sorted by (T0, mid)."""
    T = np.asarray(T)
    mids = np.asarray(mid)
    sc = np.asarray(score)
    if rank_dtype is not None:
        sc = sc.astype(rank_dtype)
    assert rb % 600 == 0 and rb <= 7200, "rebalance must be a multiple of 600 s and <= 7200 s (mark grid)"
    ok = (T % rb == 0) & np.isfinite(sc)
    idx = np.flatnonzero(ok)
    o = np.lexsort((mids[idx], -sc[idx], T[idx]))
    idx = idx[o]
    cut = np.flatnonzero(np.diff(T[idx])) + 1
    pos: dict = {}
    trades: list = []
    prev = None
    for g in np.split(idx, cut) if len(idx) else []:
        t = int(T[g[0]])
        if prev is not None and t - prev > rb:
            for m_, (t0, r0) in pos.items():
                trades.append((r0, m_, t0, prev + rb, "gap"))
            pos = {}
        prev = t
        gm = mids[g]
        top = gm[:k]
        keep = set(gm[:k].tolist())
        sold_now = set()
        for m_ in list(pos):
            t0, r0 = pos[m_]
            if m_ not in keep:
                trades.append((r0, m_, t0, t, "rank"))
                del pos[m_]
                sold_now.add(m_)
        for r, m_ in zip(g[:k], top):
            if m_ not in pos and m_ not in sold_now:
                pos[m_] = (t, r)
    for m_, (t0, r0) in pos.items():
        trades.append((r0, m_, t0, prev + rb, "end"))
    if not trades:
        return {"row": np.zeros(0, np.int64), "mid": np.zeros(0, np.int64), "T0": np.zeros(0, np.int64), "T1": np.zeros(0, np.int64),
                "reason": np.zeros(0, "U6")}
    row = np.array([t[0] for t in trades], np.int64)
    mm = np.array([t[1] for t in trades], np.int64)
    t0 = np.array([t[2] for t in trades], np.int64)
    t1 = np.array([t[3] for t in trades], np.int64)
    rs = np.array([t[4] for t in trades])
    o = np.lexsort((mm, t0))
    return {"row": row[o], "mid": mm[o], "T0": t0[o], "T1": t1[o], "reason": rs[o]}


def attach_marks(mid, T1, mk_mid, mk_t, mk_Qx, mk_Bx):
    """Exit state per trade from the mark table, joined on (mid, T1). Raises if any exit has no mark or a mark is duplicated."""
    def key(m, t):
        return (np.asarray(m, np.int64) << 32) | np.asarray(t, np.int64)

    mk = key(mk_mid, mk_t)
    o = np.argsort(mk, kind="stable")
    mks = mk[o]
    assert len(mks) < 2 or not (mks[1:] == mks[:-1]).any(), "duplicate (mid, t) in the mark table"
    kk = key(mid, T1)
    if len(kk) == 0:
        return np.zeros(0), np.zeros(0)
    assert len(mks) > 0, f"{len(kk)} exits have no mark state (empty mark table)"
    pos = np.minimum(np.searchsorted(mks, kk), len(mks) - 1)
    found = mks[pos] == kk
    assert found.all(), f"{int((~found).sum())} exits have no mark state"
    j = o[pos]
    return np.asarray(mk_Qx, float)[j], np.asarray(mk_Bx, float)[j]


# ---------------------------------------------------------------------------------------------------------------- cost stack
def round_trip(stake, Qe, Be, spot, Qx, Bx, send=F.SEND_FEE_DECIDING, rent=F.RENT_DECIDING, h_in=F.HAIRCUT_ENTRY, h_out=F.HAIRCUT_SELL,
               guard_ratio=F.GUARD, stranded=None, auto_stranded=True, rent_stranded=F.RENT_STRANDED, return_stranded=False, return_invalid=False, return_exit_invalid=False, k_tol=F.K_TOL, return_exit_cause=False):
    """One PumpSwap round trip in lamports; returns (pnl, filled) or (pnl, filled, stranded) with return_stranded.

    (Qe, Be): pool state the buy lands on (PRE-trade, Q incl. V); spot: decision-time spot; (Qx, Bx): state the sell lands on.
    net = stake x (1 - fee/1e6); tokens = Be x net / (Qe + net). Guard: (net / tokens) / spot > guard_ratio -> pnl = -send, not filled.
    Exit: pool = (Qx + net, max(Bx - tokens, 1)) (own buy re-injected); sold tokens = tokens / (1 + h_in);
    proceeds = tokens_sold x Qa / (Ba + tokens_sold) x (1 - fee(Qa, Ba)/1e6) x (1 - h_out).
    pnl = proceeds - stake - 2 x send - rent. With send 55,000, rent 0, h_in = h_out = 0 this is harness hlib.ps_round_trip bit for bit.
    Stranded (no sell lands): pnl = -stake - send (the buy) - rent_stranded, no sell send. A filled trade is stranded when the caller says so
    (`stranded`, a per-trade bool: unpriced exit, freeze authority, a Token-2022 extension that blocks close) or, with auto_stranded, when the
    simulated proceeds are below one send fee (the sell would not pay for itself). A guarded buy is never stranded (nothing was bought).
    Invalid entry state (entry_invalid): Qe < V_LO (17.5e9; Q includes V and V >= V_LO for every member pool, so less is a negative real quote
    reserve; this also covers Qe <= 0 and NaN), Be <= 0 or NaN, spot non-finite or <= 0 (a NaN spot would switch the guard off), or tokens <= 0 /
    non-finite after the entry formula. Priced as stranded (stake lost, buy send, rent_stranded, no sell send); the guard does not apply to it.
    Invalid exit state (exit_invalid, only on a bought, valid-entry trip): tokens >= Bx, Qx < V_LO, or Qx or Bx non-finite (inf or NaN). Same stranded price;
    there is no max(Bx - tokens, 1) clamp. Also exit_invalid: sell output before fees (tokens_sold x Qa / (Ba + tokens_sold)) above the pool's real
    quote Qa - V_LO (Qa = Qx + net, the exit quote incl. the own-buy re-injection): a physical bound, no tolerance. Also exit_invalid (C8, k_tol, default
    K_TOL = 1e-3; None switches it off): Qx x Bx < Qe x Be x (1 - k_tol), float64, V on both sides (the constant-product k of a pool does not fall).
    Flags come back as last return values: return_invalid (entry), return_exit_invalid, then return_exit_cause (int8 per trip: 0 none, 2 = C2 state,
    7 = C7 real-quote bound, 8 = C8 k-fall; when several apply the first of C2, C7, C8 is recorded)."""
    stake = np.asarray(stake, float)
    with np.errstate(all="ignore"):
        Qe_, Be_, spot_ = np.asarray(Qe, float), np.asarray(Be, float), np.asarray(spot, float)
        f = fee_ppm(Qe, Be)
        net = stake * (1 - f / 1e6)
        tok = Be * net / (Qe + net)
        bad = ~(np.isfinite(Qe_) & np.isfinite(Be_) & (Qe_ >= F.V_LO) & (Be_ > 0) & np.isfinite(spot_) & (spot_ > 0) & np.isfinite(tok) & (tok > 0))
        guard = ((net / tok) / spot > guard_ratio) & ~bad
        Qx_, Bx_ = np.asarray(Qx, float), np.asarray(Bx, float)
        ok_in = ~bad & ~guard
        c2 = ~(np.isfinite(Qx_) & (Qx_ >= F.V_LO) & np.isfinite(Bx_) & (tok < Bx_)) & ok_in
        tokh = tok / (1 + h_in)
        Qa = Qx + net
        Ba = np.where(c2 | bad, 1.0, Bx - tok)
        gross = tokh * Qa / (Ba + tokh)
        c7 = ok_in & ~c2 & ~(gross <= Qa - F.V_LO)      # physical bound: the sell cannot take more than the pool's real quote (Qa - V_LO)
        c8 = (ok_in & ~c2 & (Qx_ * Bx_ < Qe_ * Be_ * (1 - k_tol))) if k_tol is not None else np.zeros(np.shape(c2), bool)
        cause = np.where(c2, 2, np.where(c7, 7, np.where(c8, 8, 0))).astype(np.int8)
        xbad = cause > 0
    with np.errstate(all="ignore"):
        sv = gross * (1 - fee_ppm(Qa, Ba) / 1e6) * (1 - h_out)
    st = np.zeros(np.shape(sv), bool)
    if stranded is not None:
        st = st | np.asarray(stranded, bool)
    if auto_stranded:
        st = st | (sv < send)
    st = (st | bad | xbad) & ~guard
    pnl = np.where(guard, -float(send), np.where(st, -stake - send - rent_stranded, sv - stake - 2 * send - rent))
    out = (pnl, ~guard) + ((st,) if return_stranded else ()) + ((bad,) if return_invalid else ()) + ((xbad,) if return_exit_invalid else ()) + ((cause,) if return_exit_cause else ())
    return out


def _sig(z):
    return 1 / (1 + np.exp(-z))


def _zb(ssb, nearby_lamports):
    return F.PRESS_SLOPE_SLOT * np.log1p(np.asarray(ssb, float)) + F.PRESS_SLOPE_SOL * np.log1p(np.asarray(nearby_lamports, float) / 1e9)


def press_intercept(ssb, nearby_lamports, filled, target: float = F.PRESS_P_TARGET):
    """Intercept c with mean sigmoid(c + 0.8 ln(1+ssb) + 0.35 ln(1+nearby/1e9)) over the filled rows = target (80 bisections on [-40, 40]).
    Returns (c, p for every row)."""
    zb = _zb(ssb, nearby_lamports)
    filled = np.asarray(filled, bool)
    lo, hi = -40.0, 40.0
    for _ in range(80):
        mid = (lo + hi) / 2
        mp = _sig(mid + zb[filled]).mean() if filled.any() else 0
        if mp > target:
            hi = mid
        else:
            lo = mid
    c = (lo + hi) / 2
    return c, _sig(c + zb)


def fail_legs(pnl, filled, ssb, nearby, send=F.SEND_FEE_DECIDING, intercept=None):
    """Flat 15 % and pressure legs. A filled send fails with probability 0.15 (flat) or p (pressure) and then costs one send fee;
    otherwise it keeps pnl. Unfilled (guarded) rows already carry -send. The pressure intercept is refit on THIS cell's fills (mean p = 0.289)
    unless `intercept` is given. Returns (flat, press, c, p)."""
    pnl = np.asarray(pnl, float)
    filled = np.asarray(filled, bool)
    flat = np.where(filled, (1 - F.FAIL_FLAT) * pnl - F.FAIL_FLAT * send, pnl)
    if intercept is None:
        c, p = press_intercept(ssb, nearby, filled)
    else:
        c = float(intercept)
        p = _sig(c + _zb(ssb, nearby))
    press = np.where(filled, (1 - p) * pnl - p * send, pnl)
    return flat, press, float(c), p


def price_cell(Qe, Be, spot, Qx, Bx, ssb, nearby, stake=F.STAKE_LAMPORTS, send=F.SEND_FEE_DECIDING, rent=F.RENT_DECIDING,
               h_in=F.HAIRCUT_ENTRY, h_out=F.HAIRCUT_SELL, stranded=None, auto_stranded=True, k_tol=F.K_TOL):
    """One latency cell: no-fail pnl, flat and pressure legs (intercept refit on this cell's fills)."""
    stake = np.broadcast_to(np.asarray(stake, float), np.shape(Qe)).copy()
    pnl, filled, st, bad, xbad, cause = round_trip(stake, Qe, Be, spot, Qx, Bx, send, rent, h_in, h_out, stranded=stranded, auto_stranded=auto_stranded,
                                      return_stranded=True, return_invalid=True, return_exit_invalid=True, return_exit_cause=True, k_tol=k_tol)
    flat, press, c, p = fail_legs(pnl, filled, ssb, nearby, send)
    return {"nofail": pnl, "filled": filled, "stranded": st, "entry_invalid": bad, "exit_invalid": xbad, "exit_cause": cause, "flat": flat, "press": press, "intercept": c, "p": p, "stake": stake}


# ---------------------------------------------------------------------------------------------------------------- statistics
def utc_dates(T) -> np.ndarray:
    """'YYYY-MM-DD' of unix seconds (UTC)."""
    return np.asarray(T, np.int64).astype("datetime64[s]").astype("datetime64[D]").astype(str)


def boot_lo(x, draws: int = F.BOOT_DRAWS, seed: int = F.BOOT_SEED, q: float = F.BOOT_PCT):
    """5th percentile of the means of `draws` bootstrap resamples (hlib.boot_lo: one rng.integers call per draw)."""
    x = np.asarray(x, float)
    if len(x) == 0:
        return None
    rng = np.random.default_rng(seed)
    bm = np.array([x[rng.integers(0, len(x), len(x))].mean() for _ in range(draws)])
    return float(np.percentile(bm, q))


def t_sf(t: float, df: float) -> float:
    """P(T > t), Student t with df degrees of freedom (tools.cap_pick_score.t_sf)."""
    from tools.cap_pick_score import t_sf as _t_sf

    return _t_sf(t, df)


def day_t(sums):
    """One-sided day-level t of the block sums (SOL): t = mean / (sd(ddof=1) / sqrt(W)), df = W - 1, p = P(T > t).
    The caller decides which blocks are in `sums` (empty blocks dropped, or an explicit calendar list)."""
    s = np.asarray(sums, float)
    W = len(s)
    if W < 2:
        return {"W": W, "df": max(W - 1, 0), "mean_sol": float(s.mean()) if W else None, "sd_sol": None, "t": None, "p": None}
    sd = float(np.std(s, ddof=1))
    t = float(s.mean() / (sd / math.sqrt(W))) if sd > 0 else (math.inf if s.mean() > 0 else (-math.inf if s.mean() < 0 else 0.0))
    return {"W": W, "df": W - 1, "mean_sol": float(s.mean()), "sd_sol": sd, "t": t, "p": float(t_sf(t, W - 1))}


def block_sums(x_lamports, T0, w0: int, block_s: int = 86400, drop_empty: bool = True):
    """Sum of pnl (SOL) per 24 h block counted from w0 (block = (T0 - w0) // block_s). Returns (block ids, sums). Blocks before w0 are refused.
    drop_empty=True keeps only blocks that hold at least one trade; False fills every block from 0 to the last with zeros."""
    x = np.asarray(x_lamports, float) / 1e9
    b = (np.asarray(T0, np.int64) - int(w0)) // int(block_s)
    if len(b) and b.min() < 0:
        raise ValueError("a trade starts before w0")
    if not len(b):
        return np.zeros(0, np.int64), np.zeros(0)
    nb = int(b.max()) + 1
    s = np.bincount(b, x, minlength=nb)
    cnt = np.bincount(b, minlength=nb)
    ids = np.flatnonzero(cnt > 0) if drop_empty else np.arange(nb)
    return ids, s[ids]


def block_means(x_lamports, T0, w0: int, block_s: int = 86400):
    """Mean pnl (SOL) per attempt in each 24 h block counted from w0; empty blocks are dropped. Returns (block ids, means, counts).
    This is the DEC-021 Amendment 1 section 5 m_b."""
    x = np.asarray(x_lamports, float) / 1e9
    b = (np.asarray(T0, np.int64) - int(w0)) // int(block_s)
    if len(b) and b.min() < 0:
        raise ValueError("a trade starts before w0")
    if not len(b):
        return np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int64)
    nb = int(b.max()) + 1
    cnt = np.bincount(b, minlength=nb)
    ids = np.flatnonzero(cnt > 0)
    return ids, (np.bincount(b, x, minlength=nb)[ids] / cnt[ids]), cnt[ids]


def book_stats(pnl_lamports, T0, mid, stake=F.STAKE_LAMPORTS, w0: int | None = None, block_s: int = 86400, draws: int = F.BOOT_DRAWS,
               seed: int = F.BOOT_SEED, daylist=None, entry_invalid=None, exit_invalid=None, exit_cause=None):
    """Gate statistics of one leg. pnl_lamports per trade; T0 entry mark (unix s); mid mint ids.

    n, dates (distinct UTC entry dates), days_pos (dates with total > 0; majority = days_pos * 2 > dates), mean_sol, mean_pct (of stake),
    total_sol, ci90_lo_sol (trade-level bootstrap, 1,000 draws, seed 1, 5th pct), ex_top3_sol (drop the 3 best trades),
    ex_best_block_sol (24 h blocks from w0), ex_best_date_sol (UTC dates), ex_best_mint_sol, and the deciding t_block over 24 h blocks from w0
    (one-sided t of per-block MEAN pnl per attempt, empty blocks dropped, df = W - 1; t_block_sum is the report-only version on block sums). w0 is required: None raises.
    daylist: optional explicit list of UTC dates ('YYYY-MM-DD'); the calendar-date t is then also given as t_dates (empty listed dates count as 0,
    as the VERIFY re-score does)."""
    if w0 is None:
        raise ValueError("w0 is required (the read's W0); there is no default")
    ei = None if entry_invalid is None else int(np.asarray(entry_invalid, bool).sum())
    xi = None if exit_invalid is None else int(np.asarray(exit_invalid, bool).sum())
    x = np.asarray(pnl_lamports, float)
    T0 = np.asarray(T0, np.int64)
    n = len(x)
    o = {"n": int(n)}
    if ei is not None:
        o["entry_invalid"] = ei
    if xi is not None:
        o["exit_invalid"] = xi
    if exit_cause is not None:
        ec = np.asarray(exit_cause)
        o["exit_invalid_by_cause"] = {"C2": int((ec == 2).sum()), "C7": int((ec == 7).sum()), "C8": int((ec == 8).sum())}
    if n == 0:
        return o
    stake = np.broadcast_to(np.asarray(stake, float), x.shape)
    dates = utc_dates(T0)
    ud, inv = np.unique(dates, return_inverse=True)
    dsum = np.bincount(inv, x) / 1e9
    o["dates"] = int(len(ud))
    o["days_pos"] = int((dsum > 0).sum())
    o["days_pos_majority"] = bool(o["days_pos"] * 2 > o["dates"])
    o["mean_sol"] = float(x.mean() / 1e9)
    o["mean_pct"] = float(np.mean(x / stake) * 100)
    o["total_sol"] = float(x.sum() / 1e9)
    o["ci90_lo_sol"] = boot_lo(x / 1e9, draws, seed)
    o["ex_top3_sol"] = float(np.sort(x)[::-1][3:].sum() / 1e9)
    o["ex_best_date_sol"] = float(dsum.sum() - dsum.max())
    o["best_date"] = str(ud[int(np.argmax(dsum))])
    ids, bs = block_sums(x, T0, w0, block_s, True)
    o["ex_best_block_sol"] = float(bs.sum() - bs.max())
    o["best_block"] = int(ids[int(np.argmax(bs))])
    mu, minv = np.unique(np.asarray(mid, np.int64), return_inverse=True)
    msum = np.bincount(minv, x) / 1e9
    o["ex_best_mint_sol"] = float(msum.sum() - msum.max())
    o["best_mint"] = int(mu[int(np.argmax(msum))])
    # deciding statistic (DEC-021 Amendment 1 section 5): one-sided t on per-block MEAN pnl per attempt, empty blocks dropped, df = W - 1
    _, bm, _ = block_means(x, T0, w0, block_s)
    dm = day_t(bm)
    o.update({"blocks_W": dm["W"], "block_df": dm["df"], "block_mean_sol": dm["mean_sol"], "block_sd_sol": dm["sd_sol"], "t_block": dm["t"],
              "p_block": dm["p"]})
    # report-only: the same t on block SUMS (not the deciding statistic)
    dt = day_t(bs)
    o.update({"block_sum_mean_sol": dt["mean_sol"], "block_sum_sd_sol": dt["sd_sol"], "t_block_sum": dt["t"], "p_block_sum": dt["p"]})
    if daylist is not None:
        lab = {d: i for i, d in enumerate(daylist)}
        missing = [d for d in ud if d not in lab]
        if missing:
            raise ValueError(f"trade dates outside daylist: {missing[:3]}")
        dd = np.zeros(len(daylist))
        for d, s in zip(ud, dsum):
            dd[lab[d]] = s
        td = day_t(dd)
        o.update({"dates_listed": len(daylist), "days_pos_listed": int((dd > 0).sum()), "t_dates": td["t"], "p_dates": td["p"],
                  "sd_dates_sol": td["sd_sol"], "mean_dates_sol": td["mean_sol"]})
    o["gate_numeric"] = bool(n >= 100 and o["dates"] >= 5 and o["days_pos_majority"] and o["ci90_lo_sol"] > 0 and o["ex_top3_sol"] > 0
                             and o["ex_best_block_sol"] > 0 and o["ex_best_date_sol"] > 0)
    return o


def drop_trades(mid, T0, mints=(), dates=()):
    """Keep mask for the VERIFY drops: not a listed mint and not a listed UTC date (of the entry mark)."""
    keep = ~np.isin(np.asarray(mid, np.int64), np.asarray(list(mints), np.int64))
    if dates:
        keep &= ~np.isin(utc_dates(T0), np.asarray(list(dates)))
    return keep
