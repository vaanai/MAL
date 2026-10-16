"""Tests for tools/r4b_book.py: engine rules on a tiny synthetic panel, the cost stack by hand, the pressure intercept, the statistics."""
from __future__ import annotations

import math

import numpy as np
import pytest

from tools import r4b_book as B
from tools import r4b_frozen as F

H = 3600
T0 = 1_790_000_400 // H * H            # an hour mark


def panel(rows):
    """rows: (hour_index, mid, score) -> T, mid, score arrays in panel order (T, mid)."""
    rows = sorted(rows, key=lambda r: (r[0], r[1]))
    return (np.array([T0 + r[0] * H for r in rows], np.int64), np.array([r[1] for r in rows], np.int64), np.array([r[2] for r in rows], float))


# ------------------------------------------------------------------------------------------------------------------ engine rules
def test_rank_enter_hold_exit_and_end_close():
    # K=2. hour0: scores a=3 b=2 c=1 -> buy a,b. hour1: c=9 a=8 b=1 -> top {c, a}: sell b, buy c, a continues. hour2: a=5 d=4 -> sell c.. end close
    T, mid, sc = panel([(0, 1, 3), (0, 2, 2), (0, 3, 1), (1, 3, 9), (1, 1, 8), (1, 2, 1), (2, 1, 5), (2, 4, 4), (2, 3, 0)])
    bk = B.xs_book(T, mid, sc, k=2)
    got = sorted(zip(bk["mid"].tolist(), ((bk["T0"] - T0) // H).tolist(), ((bk["T1"] - T0) // H).tolist(), bk["reason"].tolist()))
    assert got == [(1, 0, 3, "end"), (2, 0, 1, "rank"), (3, 1, 2, "rank"), (4, 2, 3, "end")]
    assert (np.diff(bk["T0"]) >= 0).all()
    assert len(bk["mid"]) == 4                      # a (mid 1) held across 3 marks is ONE trade, no cost at the marks it continues


def test_tie_goes_to_lower_mint_id_and_float32_ranking():
    T, mid, sc = panel([(0, 7, 1.0), (0, 5, 1.0), (0, 6, 1.0)])
    bk = B.xs_book(T, mid, sc, k=1)
    assert bk["mid"].tolist() == [5]
    # two scores 1e-9 apart: float64 ranks the larger one, float32 (the harness) sees a tie and the lower id wins
    T, mid, sc = panel([(0, 7, 1.0 + 1e-9), (0, 5, 1.0)])
    assert B.xs_book(T, mid, sc, k=1)["mid"].tolist() == [7]
    assert B.xs_book(T, mid, sc, k=1, rank_dtype=np.float32)["mid"].tolist() == [5]


def test_same_mark_resell_blocked_and_rebuy_next_mark():
    # hour1: x leaves the top-1 for y, hour2: x is back. x is sold at hour1 and bought again at hour2 (a new trade), never sold and bought at one mark.
    T, mid, sc = panel([(0, 1, 5), (0, 2, 1), (1, 1, 1), (1, 2, 5), (2, 1, 5), (2, 2, 1)])
    bk = B.xs_book(T, mid, sc, k=1)
    got = [(int(m), int((a - T0) // H), int((b - T0) // H)) for m, a, b in zip(bk["mid"], bk["T0"], bk["T1"])]
    assert got == [(1, 0, 1), (2, 1, 2), (1, 2, 3)]


def test_nonfinite_scores_and_off_hour_rows_are_not_candidates():
    T = np.array([T0, T0, T0 + 600, T0 + H], np.int64)
    mid = np.array([1, 2, 1, 1], np.int64)
    sc = np.array([np.nan, 1.0, 9.0, 1.0])
    bk = B.xs_book(T, mid, sc, k=1)
    assert bk["mid"].tolist() == [2, 1]                                  # mid 1 (NaN at hour 0) is bought only at hour 1; the 600 s row is no mark
    assert bk["T0"].tolist() == [T0, T0 + H] and bk["reason"].tolist() == ["rank", "end"]


def test_gap_closes_everything_at_last_mark_plus_rebalance():
    T, mid, sc = panel([(0, 1, 5), (1, 1, 5), (5, 2, 5), (6, 2, 5)])
    bk = B.xs_book(T, mid, sc, k=1)
    got = [(int(m), int((a - T0) // H), int((b - T0) // H), r) for m, a, b, r in zip(bk["mid"], bk["T0"], bk["T1"], bk["reason"])]
    assert got == [(1, 0, 2, "gap"), (2, 5, 7, "end")]


def test_empty_book():
    bk = B.xs_book(np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0))
    assert len(bk["row"]) == 0


def test_attach_marks():
    qx, bx = B.attach_marks([3, 4], [T0 + H, T0 + 2 * H], [3, 3, 4], [T0, T0 + H, T0 + 2 * H], [10.0, 11.0, 12.0], [20.0, 21.0, 22.0])
    assert qx.tolist() == [11.0, 12.0] and bx.tolist() == [21.0, 22.0]
    with pytest.raises(AssertionError):
        B.attach_marks([3], [T0 + 5 * H], [3], [T0], [1.0], [1.0])
    with pytest.raises(AssertionError):
        B.attach_marks([3], [T0], [3, 3], [T0, T0], [1.0, 1.0], [1.0, 1.0])


# ------------------------------------------------------------------------------------------------------------------ states
def _prints(n=12, v=1.75e10):
    sl = np.arange(1000, 1000 + 10 * n, 10)
    ib = np.array([i % 2 == 0 for i in range(n)])
    so = np.full(n, 2e8)
    tk = np.full(n, 1e11)
    q = 1e9 + np.arange(n) * 1e8                      # quote reserve without V
    b = 1e15 - np.arange(n) * 1e12
    return sl, ib, so, tk, q, b, v


def test_mint_states_entry_mark_and_flow():
    sl, ib, so, tk, q, b, v = _prints()
    sd = np.array([1040])                              # prints with slot < 1040 are the past: indices 0..3
    r = B.mint_states(sl, ib, so, tk, q, b, v, sd, 0.4, t_marks=np.array([0]), slot_s_marks=np.array([0.4]), clock_slot_marks=np.array([1050]),
                      lats=(1.3,), rounding="round")
    # slot 0.4 s: 1.3 / 0.4 = 3.25 -> k = 3; x = 1043; first print with slot >= 1044 is index 5 (slot 1050)
    assert r["Qe"][1.3][0] == q[5] + v and r["Be"][1.3][0] == b[5]
    j = 4
    qp = (q[3] + so[3] * 1.01 * -1 + v) if not ib[3] else (q[3] + so[3] * 0.99 + v)
    bp = b[3] + tk[3] if not ib[3] else b[3] - tk[3]
    assert r["spot"][0] == pytest.approx(qp / bp)
    # exit: lag = ceil(0.55 / 0.4) = 2; slot 1050 + 2 + 1 = 1053 -> first print with slot >= 1053 is index 6
    assert r["Qx"][0] == q[6] + v and r["Bx"][0] == b[6]
    # no buy lands in slot 1043 (prints sit on multiples of 10): ssb 0; nearby window [x - round(2 / 0.4), x] = [1038, 1043] holds the print at 1040 (index 4, a buy)
    assert r["ssb"][1.3][0] == 0 and r["nearby"][1.3][0] == 2e8


def test_mint_states_end_bound_uses_derived_final_state():
    sl, ib, so, tk, q, b, v = _prints(4)
    r = B.mint_states(sl, ib, so, tk, q, b, v, np.array([1030]), 0.4, lats=(1.3,))
    qL = q[-1] - so[-1] / (1 - 0.0125)                 # last print (index 3) is a sell
    assert r["Qe"][1.3][0] == pytest.approx(qL + v) and r["Be"][1.3][0] == b[-1] + tk[-1]


def test_mint_states_per_print_virtual_reserve():
    sl, ib, so, tk, q, b, v = _prints()
    vq = v + np.arange(len(sl)) * 1e6
    r = B.mint_states(sl, ib, so, tk, q, b, vq, np.array([1040]), 0.4, lats=(1.3,))
    assert r["Qe"][1.3][0] == q[5] + vq[5]


def test_k_rounding_tie_changes_entry_state():
    # slot 0.2 s: 1.3 s = 6.5 slots. round -> k 6, ceil -> k 7. A print on every slot makes the two states differ.
    n = 40
    sl = np.arange(1000, 1000 + n)
    ib = np.ones(n, bool)
    so = np.full(n, 1e8)
    tk = np.full(n, 1e10)
    q = 1e9 + np.arange(n) * 1e8
    b = 1e15 - np.arange(n) * 1e10
    sd = np.array([1010])
    a = B.mint_states(sl, ib, so, tk, q, b, 1.75e10, sd, 0.2, lats=(1.3,), rounding="round")
    c = B.mint_states(sl, ib, so, tk, q, b, 1.75e10, sd, 0.2, lats=(1.3,), rounding="ceil")
    assert a["Qe"][1.3][0] == q[1010 + 6 + 1 - 1000] + 1.75e10
    assert c["Qe"][1.3][0] == q[1010 + 7 + 1 - 1000] + 1.75e10
    d = B.mint_states(sl, ib, so, tk, q, b, 1.75e10, sd, 0.2, lats=(1.3,))                 # default = F.K_ROUNDING
    assert d["Qe"][1.3][0] == (a if F.K_ROUNDING == "round" else c)["Qe"][1.3][0]
    assert a["ssb"][1.3][0] == 1 and c["ssb"][1.3][0] == 1


def test_exit_lag_floor_asserted():
    sl, ib, so, tk, q, b, v = _prints()
    with pytest.raises(AssertionError):
        B.mint_states(sl, ib, so, tk, q, b, v, np.array([1040]), 0.4, t_marks=np.array([0]), slot_s_marks=np.array([0.6]), clock_slot_marks=np.array([1050]))
    assert B.exit_lag_vec(np.array([0.2, 0.4])).tolist() == [3, 2]


# ------------------------------------------------------------------------------------------------------------------ cost stack
def hand_round_trip(stake, Qe, Be, spot, Qx, Bx, send, rent, h_in, h_out):
    f = float(B.fee_ppm(Qe, Be))
    net = stake * (1 - f / 1e6)
    tok = Be * net / (Qe + net)
    if (net / tok) / spot > 1.15:
        return -send, False
    tokh = tok / (1 + h_in)
    Qa, Ba = Qx + net, max(Bx - tok, 1.0)
    sv = tokh * Qa / (Ba + tokh) * (1 - float(B.fee_ppm(Qa, Ba)) / 1e6) * (1 - h_out)
    return sv - stake - 2 * send - rent, True


def test_fee_tiers():
    # mcap SOL = Q / (B * 1000) * 1e9 ; with B = 1e13, mcap = Q * 1e-7, so a market cap of m SOL is Q = m * 1e7
    assert B.fee_ppm(1e8, 1e13) == 12_500                 # 10 SOL
    assert B.fee_ppm(4.19e9, 1e13) == 12_500              # 419 SOL, just under the first step
    assert B.fee_ppm(4.2e9, 1e13) == 12_000               # 420 SOL
    assert B.fee_ppm(1e30, 1e13) == 3_000


def test_round_trip_matches_hand_calc_all_stacks():
    S = 250_000_000.0
    Qe, Be, spot = 8e10, 3e15, 8e10 / 3e15
    Qx, Bx = 8.2e10, 2.9e15
    for send, rent, hi, ho in ((55_000, 0.0, 0.0, 0.0), (505_000, 2_039_280, 0.002608, 0.0016), (55_000, 2_039_280, 0.0, 0.0)):
        pnl, fil = B.round_trip(np.array([S]), np.array([Qe]), np.array([Be]), np.array([spot]), np.array([Qx]), np.array([Bx]), send, rent, hi, ho)
        want, wf = hand_round_trip(S, Qe, Be, spot, Qx, Bx, send, rent, hi, ho)
        assert fil[0] and wf and pnl[0] == pytest.approx(want, rel=0, abs=1e-6)


def test_costs_are_ordered_and_each_term_bites():
    S = np.array([250e6])
    a = [np.array([x]) for x in (8e10, 3e15, 8e10 / 3e15, 8e10, 3e15)]
    base = B.round_trip(S, *a, send=55_000, rent=0.0, h_in=0.0, h_out=0.0)[0][0]
    assert B.round_trip(S, *a, send=505_000, rent=0.0, h_in=0.0, h_out=0.0)[0][0] == pytest.approx(base - 900_000)         # 2 x (505k - 55k)
    assert B.round_trip(S, *a, send=55_000, rent=2_039_280, h_in=0.0, h_out=0.0)[0][0] == pytest.approx(base - 2_039_280)
    h = B.round_trip(S, *a, send=55_000, rent=0.0, h_in=0.002608, h_out=0.0016)[0][0]
    assert 0.0035 * 250e6 < base - h < 0.0055 * 250e6                                                                       # about 0.42 % of proceeds


def test_guard_revert_pays_one_send_no_rent_no_haircut():
    S = np.array([250e6])
    Qe, Be = np.array([8e10]), np.array([3e15])
    spot = np.array([8e10 / 3e15 / 1.2])               # decision spot 20 % below the landing price: execution is above 1.15 x spot
    pnl, fil = B.round_trip(S, Qe, Be, spot, Qe, Be, send=505_000, rent=2_039_280)
    assert pnl[0] == -505_000 and not fil[0]
    pnl, fil = B.round_trip(S, Qe, Be, spot * 1.2, Qe, Be, send=505_000, rent=2_039_280)
    assert fil[0] and pnl[0] != -505_000


def test_press_intercept_hits_target_and_legs():
    rng = np.random.default_rng(3)
    ssb = rng.integers(0, 6, 400).astype(float)
    near = rng.integers(0, 5, 400) * 1e9
    fil = rng.random(400) > 0.1
    c, p = B.press_intercept(ssb, near, fil)
    assert abs(p[fil].mean() - 0.289) < 1e-9
    pnl = rng.normal(0, 3e7, 400)
    pnl = np.where(fil, pnl, -505_000.0)
    flat, press, c2, p2 = B.fail_legs(pnl, fil, ssb, near, send=505_000)
    assert c2 == c
    assert np.allclose(flat[fil], 0.85 * pnl[fil] - 0.15 * 505_000) and np.array_equal(flat[~fil], pnl[~fil])
    assert np.allclose(press[fil], (1 - p[fil]) * pnl[fil] - p[fil] * 505_000) and np.array_equal(press[~fil], pnl[~fil])
    _, press3, c3, _ = B.fail_legs(pnl, fil, ssb, near, send=505_000, intercept=c)           # the supplied intercept is used as is
    assert c3 == c and np.array_equal(press3, press)
    # more landing-slot buys and more nearby SOL raise the fail probability
    z = B.fail_legs(np.full(2, 1e6), np.ones(2, bool), np.array([0.0, 8.0]), np.array([0.0, 5e9]), intercept=-1.0)[3]
    assert z[1] > z[0]


def test_price_cell_refits_intercept_per_cell():
    n = 50
    e = dict(Qe=np.full(n, 8e10), Be=np.full(n, 3e15), spot=np.full(n, 8e10 / 3e15), Qx=np.full(n, 8.1e10), Bx=np.full(n, 2.95e15))
    ssb = np.arange(n) % 5
    cell = B.price_cell(e["Qe"], e["Be"], e["spot"], e["Qx"], e["Bx"], ssb, np.zeros(n))
    assert abs(cell["p"][cell["filled"]].mean() - 0.289) < 1e-9 and cell["filled"].all()


# ------------------------------------------------------------------------------------------------------------------ statistics
def test_boot_lo_matches_a_direct_computation():
    x = np.random.default_rng(0).normal(0.01, 0.1, 300)
    rng = np.random.default_rng(1)
    bm = [x[rng.integers(0, 300, 300)].mean() for _ in range(1000)]
    assert B.boot_lo(x) == float(np.percentile(bm, 5))


def test_t_sf_against_scipy():
    st = pytest.importorskip("scipy.stats")
    for t, df in ((1.9, 26), (2.5, 6), (-0.4, 9), (0.0, 12)):
        assert B.t_sf(t, df) == pytest.approx(float(st.t.sf(t, df)), rel=1e-9, abs=1e-12)


def _book(days_pnl, w0, per_day=2):
    """trades: per_day trades per listed day offset (day index -> total SOL split evenly), entry on the day's 12:00 mark."""
    x, t0, mid = [], [], []
    m = 0
    for d, tot in days_pnl:
        for _ in range(per_day):
            x.append(tot / per_day * 1e9)
            t0.append(w0 + d * 86400 + 12 * H)
            mid.append(m)
            m += 1
    return np.array(x), np.array(t0, np.int64), np.array(mid)


def test_stats_blocks_dates_and_day_t():
    w0 = 1_790_035_200 // 86400 * 86400
    x, t0, mid = _book([(0, 1.0), (1, 2.0), (3, -0.5), (4, 1.5), (5, 0.5)], w0)             # day 2 has no trades: an empty block, dropped
    s = B.book_stats(x, t0, mid, w0=w0)
    assert s["n"] == 10 and s["dates"] == 5 and s["days_pos"] == 4 and s["days_pos_majority"]
    assert s["total_sol"] == pytest.approx(4.5)
    assert s["ex_best_date_sol"] == pytest.approx(2.5) and s["ex_best_block_sol"] == pytest.approx(2.5)
    assert s["blocks_W"] == 5 and s["block_df"] == 4
    sums = np.array([1.0, 2.0, -0.5, 1.5, 0.5])
    assert s["t_block"] == pytest.approx(sums.mean() / (sums.std(ddof=1) / math.sqrt(5)))
    assert 0 < s["p_block"] < 0.5
    assert not s["gate_numeric"]                                                              # n < 100
    # the blocks are counted from w0, not from UTC midnight: shifting w0 by 13 h moves the 12:00 entries into the previous block
    s2 = B.book_stats(x, t0, mid, w0=w0 - 13 * H)
    assert s2["blocks_W"] == 5 and s2["total_sol"] == s["total_sol"]
    with pytest.raises(ValueError):
        B.book_stats(x, t0, mid, w0=w0 + 3 * 86400)                                           # entries before w0 are refused


def test_stats_block_boundary_differs_from_utc_date():
    # blocks counted from 06:00 UTC: entries at +17 h (23:00), +19 h (01:00 next date), +20 h are block 0; +30 h is block 1.
    # By UTC date the 23:00 entry sits alone with the first date, so the two groupings give different ex-best sums.
    w0 = 1_790_035_200 // 86400 * 86400 + 6 * H
    t0 = np.array([w0 + 17 * H, w0 + 19 * H, w0 + 20 * H, w0 + 30 * H], np.int64)
    x = np.array([1e9, 1e9, 1e9, -1e9])
    s = B.book_stats(x, t0, np.arange(4), stake=2.5e8, w0=w0)
    assert s["blocks_W"] == 2 and s["ex_best_block_sol"] == pytest.approx(-1.0)
    assert s["dates"] == 2 and s["ex_best_date_sol"] == pytest.approx(1.0)


def test_stats_listed_dates_count_empty_days_as_zero():
    w0 = 1_790_035_200 // 86400 * 86400
    x, t0, mid = _book([(0, 1.0), (1, 2.0)], w0)
    d0 = B.utc_dates(np.array([w0]))[0]
    d1 = B.utc_dates(np.array([w0 + 86400]))[0]
    s = B.book_stats(x, t0, mid, w0=w0, daylist=[d0, d1, "2030-01-01"])
    assert s["dates_listed"] == 3 and s["days_pos_listed"] == 2
    assert s["mean_dates_sol"] == pytest.approx(1.0) and s["t_dates"] == pytest.approx(1.0 / (np.std([1, 2, 0], ddof=1) / math.sqrt(3)))
    with pytest.raises(ValueError):
        B.book_stats(x, t0, mid, w0=w0, daylist=[d0])


def test_gate_numeric_and_drops():
    rng = np.random.default_rng(5)
    n = 700
    t0 = (1_790_035_200 // 86400 * 86400) + rng.integers(0, 7, n) * 86400 + 3600 * rng.integers(0, 24, n)
    x = rng.normal(2e6, 2e7, n)
    mid = rng.integers(0, 400, n)
    s = B.book_stats(x, t0, mid, w0=1_790_035_200 // 86400 * 86400)
    assert s["n"] == 700 and s["dates"] >= 5
    keep = B.drop_trades(mid, t0, mints=[int(mid[0])], dates=[B.utc_dates(t0[:1])[0]])
    assert not keep[0] and keep.sum() < n and keep.sum() > 0
    assert B.drop_trades(mid, t0).all()
