"""Fixtures only: no /data/mal read. Trigger, BOOST detector, fills, exits, legs, gate statistics and refusals of tools/boostfloor_score.py.

Runs under pytest (numpy only) or `python -m unittest tools.test_boostfloor_score`. The parquet end-to-end cases need pandas + pyarrow and
skip without them.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tools import boostfloor_score as bf

try:  # the audit venv has pandas + pyarrow; the pytest venv does not
    import pandas as pd

    HAVE_PANDAS = True
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        HAVE_PANDAS = False
except ImportError:
    HAVE_PANDAS = False

SOL = 1e9
V = 17.58e9
S0 = 1000
BT0 = 1_700_000_000
STAKE = 0.1e9


def quiet_main(argv):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return bf.main(argv)


def _u64(x):
    return np.array(x, dtype=np.uint64)


def make_pool(prints, v=V):
    """prints: (offset from s0, isbuy, sol in SOL, real quote pre-trade in SOL, wallet hash). sps is exactly 0.4 (last offset % 5 == 0)."""
    last = prints[-1][0]
    assert (2 * last) % 5 == 0 and last > 300
    n = len(prints)
    off = np.array([p[0] for p in prints])
    return bf.PoolPath(
        sl=(S0 + off).astype(np.int64),
        q=np.array([p[3] * SOL for p in prints], float),
        b=np.array([1e14 - i * 1e11 for i in range(n)], float),
        isb=np.array([p[1] for p in prints], bool),
        sol=np.array([p[2] * SOL for p in prints], float),
        tok=np.array([1e10 + i for i in range(n)], float),
        th=_u64([p[4] for p in prints]),
        bt=(BT0 + (2 * off) // 5).astype(np.int64),
    )


B, S = True, False
# BOOST wallet 7 buys 3 x 1 SOL; the sell at +24 leaves Q = 25 + V = 42.58 > 40; the sell at +40 leaves Q = 20 + V = 37.58 <= 40 (trigger,
# t = 16 s). Primary lands at slot +44 (ceil(1.3 / 0.4) = 4), binding at +45 (ceil(1.9 / 0.4) = 5); both END-bound states are the pre-trade
# state of the print at +47. The exit trigger is s0 + round(330 / 0.4) = +825, the sell lands +827, state = the print at +828.
POOL = [
    (0, B, 1.0, 40.0, 7), (8, B, 1.0, 41.0, 7), (16, B, 1.0, 42.0, 7),
    (24, S, 5.0, 43.0, 100),
    (40, S, 5.0, 25.0, 101),
    (43, B, 0.4, 20.0, 102), (44, B, 0.3, 20.4, 103), (44, B, 0.2, 20.7, 104),
    (47, S, 1.0, 20.9, 105),
    (800, B, 0.5, 18.0, 106), (826, S, 0.5, 17.0, 107), (828, B, 1.0, 16.0, 108),
    (1000, S, 0.1, 15.0, 109),
]


def expected_pnl(qe, be, qx, bx, stake, fee_buy=0.0125, fee_sell=0.0125):
    net = stake * (1 - fee_buy)
    tk = be * net / (qe + net)
    q2, b2 = qx + net, bx - tk
    proceeds = tk * q2 / (b2 + tk) * (1 - fee_sell)
    return proceeds - stake - 2 * 55_000, (q2 / b2) / (qe / be) - 1


class Detector(unittest.TestCase):
    def det(self, buys, sells=(), s0=S0):
        """buys: (offset, SOL, wallet). sells: (offset, SOL, wallet)."""
        rows = [(o, True, s, w) for o, s, w in buys] + [(o, False, s, w) for o, s, w in sells]
        rows.sort(key=lambda r: r[0])
        sl = np.array([s0 + r[0] for r in rows], np.int64)
        return bf.detect_boost_wallet(sl, s0, _u64([r[3] for r in rows]), np.array([r[2] * SOL for r in rows]), np.array([r[1] for r in rows]))

    def test_buy_only_wallet_with_three_buys(self):
        self.assertEqual(self.det([(0, 1.0, 7), (10, 1.0, 7), (20, 1.0, 7), (5, 0.1, 8)]), 7)

    def test_budget_completion_not_required(self):
        # 3 SOL total: an early stop still qualifies (and then counts against the rule)
        self.assertEqual(self.det([(0, 1.0, 7), (10, 1.0, 7), (20, 1.0, 7)]), 7)

    def test_rejects_wallet_that_also_sells(self):
        self.assertIsNone(self.det([(0, 1.0, 7), (10, 1.0, 7), (20, 1.0, 7)], sells=[(30, 0.5, 7)]))

    def test_rejects_fewer_than_three_buys(self):
        self.assertIsNone(self.det([(0, 1.0, 7), (10, 1.0, 7)]))

    def test_rejects_buy_below_0p2_or_above_2(self):
        self.assertIsNone(self.det([(0, 1.0, 7), (10, 1.0, 7), (20, 0.199, 7)]))
        self.assertIsNone(self.det([(0, 1.0, 7), (10, 1.0, 7), (20, 2.001, 7)]))
        self.assertEqual(self.det([(0, 0.2, 7), (10, 2.0, 7), (20, 1.0, 7)]), 7)  # both bounds inclusive

    def test_total_cap_17p7(self):
        self.assertEqual(self.det([(i, 1.0, 7) for i in range(17)] + [(17, 0.7, 7)]), 7)  # exactly 17.7 SOL
        self.assertIsNone(self.det([(i, 1.0, 7) for i in range(17)] + [(17, 0.8, 7)]))

    def test_most_buys_wins_then_lowest_hash(self):
        four = [(i, 1.0, 9) for i in range(4)]
        three = [(i + 10, 1.0, 5) for i in range(3)]
        self.assertEqual(self.det(four + three), 9)
        self.assertEqual(self.det([(i, 1.0, 9) for i in range(3)] + [(i + 10, 1.0, 5) for i in range(3)]), 5)

    def test_window_is_first_1600_slots(self):
        self.assertEqual(self.det([(0, 1.0, 7), (10, 1.0, 7), (1600, 1.0, 7)]), 7)  # +1600 is inside
        self.assertIsNone(self.det([(0, 1.0, 7), (10, 1.0, 7), (1601, 1.0, 7)]))  # +1601 is outside

    def test_no_prints_in_window(self):
        self.assertIsNone(bf.detect_boost_wallet(np.array([S0 + 2000], np.int64), S0, _u64([7]), np.array([SOL]), np.array([True])))

    def test_spent_is_cumulative_boost_buys(self):
        th, sol = _u64([7, 8, 7, 7]), np.array([1.0, 5.0, 2.0, 0.5]) * SOL
        self.assertTrue(np.allclose(bf.boost_spent(th, sol, 7), np.array([1.0, 1.0, 3.0, 3.5]) * SOL))
        self.assertTrue(np.all(bf.boost_spent(th, sol, None) == 0))


class Trigger(unittest.TestCase):
    sps = 0.4

    def trig(self, sl, isb, qpost_sol, spent_sol=None):
        sl = np.array(sl, np.int64)
        spent = np.zeros(len(sl)) if spent_sol is None else np.array(spent_sol) * SOL
        return bf.find_trigger(sl, 100, self.sps, np.array(isb, bool), np.array(qpost_sol) * SOL, spent)

    def test_first_qualifying_sell(self):
        self.assertEqual(self.trig([100, 110, 120], [False, False, False], [41, 40.0, 30]), 1)  # Q <= 40 inclusive; 41 does not qualify

    def test_buys_never_trigger(self):
        self.assertEqual(self.trig([100, 110, 120], [True, True, False], [10, 10, 10]), 2)
        self.assertIsNone(self.trig([100, 110], [True, True], [10, 10]))

    def test_window_0_to_300_s(self):
        # slot 100 + 750 = 300.0 s inclusive; +751 = 300.4 s outside; a print before s0 (t < 0) is outside
        self.assertEqual(self.trig([850], [False], [10]), 0)
        self.assertIsNone(self.trig([851], [False], [10]))
        self.assertIsNone(self.trig([99], [False], [10]))
        self.assertEqual(self.trig([100], [False], [10]), 0)  # t = 0 inclusive

    def test_boost_budget_must_remain(self):
        self.assertIsNone(self.trig([110], [False], [10], spent_sol=[17.585]))
        self.assertIsNone(self.trig([110], [False], [10], spent_sol=[0.999 * 17.585]))  # not strictly below
        self.assertEqual(self.trig([110], [False], [10], spent_sol=[0.998 * 17.585]), 0)

    def test_q_is_post_trade_state_of_the_print(self):
        # print 0 sells at pre-trade Q 37 (<= 40) but leaves 45: no trigger. print 1 sells from 45 and leaves 38: trigger
        q = np.array([37.0, 45.0, 38.0]) * SOL - V
        sl = np.array([100, 110, 120], np.int64)
        isb = np.array([False, False, False])
        sol, tok, b = np.array([1.0, 1.0, 1.0]) * SOL, np.ones(3), np.ones(3)
        qpost, _ = bf.post_trade_state(q + V, b, isb, sol, tok)  # q passed here includes V, as score_pool does
        self.assertTrue(np.allclose(qpost[:2], np.array([45.0, 38.0]) * SOL))
        self.assertEqual(bf.find_trigger(sl, 100, self.sps, isb, qpost, np.zeros(3)), 1)

    def test_last_print_post_state_is_approximated(self):
        q, b, sol, tok = np.array([10.0, 20.0]), np.array([100.0, 90.0]), np.array([1.0, 3.0]), np.array([5.0, 7.0])
        qp, bp = bf.post_trade_state(q, b, np.array([False, True]), sol, tok)
        self.assertEqual((qp[-1], bp[-1]), (20.0 + 0.9875 * 3.0, 90.0 - 7.0))
        qp, bp = bf.post_trade_state(q, b, np.array([True, False]), sol, tok)
        self.assertEqual((qp[-1], bp[-1]), (20.0 - 3.0 / 0.9875, 90.0 + 7.0))


class SlotLength(unittest.TestCase):
    def test_sps(self):
        sl = np.array([0, 500, 1000], np.int64)
        self.assertEqual(bf.seconds_per_slot(sl, np.array([100, 300, 500], np.int64)), 0.4)
        self.assertTrue(math.isnan(bf.seconds_per_slot(np.array([0, 100, 300], np.int64), np.array([100, 140, 220], np.int64))))  # span <= 300
        self.assertTrue(math.isnan(bf.seconds_per_slot(sl[:2], np.array([100, 300], np.int64))))
        self.assertTrue(math.isnan(bf.seconds_per_slot(sl, np.array([0, 0, 0], np.int64))))  # no block_time

    def test_sps_open_interval(self):
        self.assertFalse(bf.sps_ok(0.15))
        self.assertTrue(bf.sps_ok(0.1501))
        self.assertTrue(bf.sps_ok(0.5999))
        self.assertFalse(bf.sps_ok(0.6))
        self.assertFalse(bf.sps_ok(float("nan")))


class Fills(unittest.TestCase):
    def test_entry_slots(self):
        self.assertEqual(bf.entry_slots(0.4), {"primary": 4, "binding": 5})
        self.assertEqual(bf.entry_slots(0.2), {"primary": 7, "binding": 10})  # 6.5 -> 7, 9.5 -> 10
        self.assertEqual(bf.entry_slots(0.325)["primary"], 4)  # 1.3 / 0.325 is 4 up to float noise: the 1e-9 guard keeps 4

    def test_end_bound_state(self):
        sl = np.array([10, 20, 20, 30], np.int64)
        q, b = np.array([1.0, 2.0, 3.0, 4.0]), np.array([10.0, 20.0, 30.0, 40.0])
        qp, bp = np.array([2.0, 3.0, 4.0, 5.0]), np.array([20.0, 30.0, 40.0, 50.0])
        self.assertEqual(bf.state_at(sl, q, b, qp, bp, 19), (2.0, 20.0))  # first print with slot > 19 is the one at 20
        self.assertEqual(bf.state_at(sl, q, b, qp, bp, 20), (4.0, 40.0))  # both prints at slot 20 are in; next is slot 30
        self.assertEqual(bf.state_at(sl, q, b, qp, bp, 30), (5.0, 50.0))  # nothing later: the last print's post-trade state
        self.assertEqual(bf.state_at(sl, q, b, qp, bp, 5), (1.0, 10.0))

    def test_pressure_counts(self):
        sl = np.array([38, 39, 40, 43, 44, 44, 44, 46], np.int64)
        isb = np.array([True, True, False, True, True, True, False, True])
        sol = np.array([9, 1, 7, 1, 2, 4, 8, 16], float)
        # landing 44, window int(round(2.0 / 0.4)) = 5 slots: slots 39..44. buys in slot 44: two. buy lamports 39..44: 1 + 1 + 2 + 4 = 8
        self.assertEqual(bf.pressure_at(sl, isb, sol, 44, 0.4), (2, 8.0))
        # landing 45: no print in slot 45; window slots 40..45: buys 1 + 2 + 4 = 7
        self.assertEqual(bf.pressure_at(sl, isb, sol, 45, 0.4), (0, 7.0))

    def test_tier_fee(self):
        self.assertEqual(bf.tier_fee(40e9, 40e9 / 100 * 1e6), 0.0125)  # mcap 100 SOL
        self.assertEqual(bf.tier_fee(40e9, 40e9 / 500 * 1e6), 0.012)  # mcap 500 SOL (>= 420)
        self.assertEqual(bf.tier_fee(40e9, 40e9 / 420 * 1e6), 0.012)  # the boundary is inclusive

    def test_fill_round_trip_own_impact_and_two_fees(self):
        qe, be, qx, bx = 38.48e9, 9.2e13, 33.58e9, 8.9e13
        pnl, gross = bf.fill_round_trip(qe, be, qx, bx, STAKE)
        want_pnl, want_gross = expected_pnl(qe, be, qx, bx, STAKE)
        self.assertAlmostEqual(pnl, want_pnl, places=3)
        self.assertAlmostEqual(gross, want_gross, places=12)
        # a flat market (same state at entry and exit) loses the two tier fees, the impact round trip and 2 x 55,000
        flat_pnl, _ = bf.fill_round_trip(qe, be, qe, be, STAKE)
        self.assertLess(flat_pnl, -2 * 55_000 - 2 * 0.0125 * STAKE * 0.9)

    def test_fill_uses_the_tier_of_the_landing_state(self):
        be = 1e14
        qe = 420e-6 * be  # mcap exactly 420 SOL -> 1.2% on the buy
        a, _ = bf.fill_round_trip(qe, be, qe, be, STAKE)
        net = STAKE * (1 - 0.012)
        tk = be * net / (qe + net)
        q2, b2 = qe + net, be - tk
        want = tk * q2 / (b2 + tk) * (1 - bf.tier_fee(q2, b2 + tk)) - STAKE - 110_000
        self.assertAlmostEqual(a, want, places=3)


class ScorePool(unittest.TestCase):
    def rows(self, prints=POOL):
        res = bf.score_pool(make_pool(prints), S0, V)
        return res, {(r["leg"], r["stake_sol"]): r for r in res.rows}

    def test_one_trade_both_legs_both_stakes(self):
        res, rows = self.rows()
        self.assertEqual(res.status, "ok")
        self.assertEqual(sorted(rows), [("binding", "0.1"), ("binding", "0.25"), ("primary", "0.1"), ("primary", "0.25")])
        p = rows[("primary", "0.1")]
        self.assertEqual((p["landing_slot"], rows[("binding", "0.1")]["landing_slot"]), (S0 + 44, S0 + 45))
        self.assertEqual(p["exit_landing_slot"], S0 + 827)
        self.assertEqual(p["sps"], 0.4)
        self.assertAlmostEqual(p["trig_t_s"], 16.0)
        self.assertAlmostEqual(p["q_trig_sol"], 37.58)
        self.assertAlmostEqual(p["rem_boost_sol"], 17.585 - 3.0 - 0.0, places=6)  # BOOST wallet 7 spent 3 SOL; no other BOOST buy by slot +44

    def test_pnl_matches_hand_calculation(self):
        _, rows = self.rows()
        pool = make_pool(POOL)
        qe, be = 20.9e9 + V, pool.b[8]  # END bound: pre-trade of the print at +47
        qx, bx = 16.0e9 + V, pool.b[11]  # exit lands +827: pre-trade of the print at +828
        for leg in ("primary", "binding"):
            for label, stake in bf.STAKES_LAMPORTS:
                want_pnl, want_gross = expected_pnl(qe, be, qx, bx, stake)
                r = rows[(leg, label)]
                self.assertAlmostEqual(r["pnl_nofail_lamports"], want_pnl, places=2)
                self.assertAlmostEqual(r["gross"], want_gross, places=12)

    def test_pressure_columns(self):
        _, rows = self.rows()
        self.assertEqual((rows[("primary", "0.1")]["ssb"], rows[("primary", "0.1")]["nb_lamports"]), (2, 0.9 * SOL))
        self.assertEqual((rows[("binding", "0.1")]["ssb"], rows[("binding", "0.1")]["nb_lamports"]), (0, 0.9 * SOL))

    def test_one_trade_per_pool(self):
        # a second qualifying sell later in the window changes nothing
        extra = POOL[:9] + [(100, S, 1.0, 20.0, 110), (110, S, 1.0, 19.0, 111)] + POOL[9:]
        _, rows = self.rows(extra)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[("primary", "0.1")]["landing_slot"], S0 + 44)

    def test_exit_uses_last_state_when_no_later_print(self):
        prints = POOL[:10] + [(825, S, 0.5, 17.0, 107)]  # path ends at the exit trigger slot; sps stays 0.4 (330 s / 825 slots)
        res, rows = self.rows(prints)
        self.assertEqual(res.status, "ok")
        pool = make_pool(prints)
        qx, bx = 17.0e9 + V - 0.5e9 / 0.9875, pool.b[-1] + pool.tok[-1]  # last print is a sell: post-trade approximation
        want_pnl, _ = expected_pnl(20.9e9 + V, pool.b[8], qx, bx, STAKE)
        self.assertAlmostEqual(rows[("primary", "0.1")]["pnl_nofail_lamports"], want_pnl, places=2)

    def test_no_trigger_when_q_stays_above_40(self):
        prints = [(o, k, s, q + 10.0, w) for (o, k, s, q, w) in POOL]
        res, rows = self.rows(prints)
        self.assertEqual((res.status, rows), ("no_trigger", {}))

    def test_no_trigger_when_boost_budget_spent(self):
        boost = [(i, B, 17.58 / 18, 45.0 + i * 0.001, 7) for i in range(18)]  # 17.58 SOL >= 0.999 x 17.585, total <= 17.7: a detected BOOST
        rest = [p for p in POOL if p[4] != 7]
        res, _ = self.rows(boost + rest)
        self.assertEqual(res.status, "no_trigger")

    def test_boost_that_is_not_detected_does_not_block(self):
        # 19 buys of 0.95 SOL = 18.05 SOL > 17.7: not a BOOST wallet by the rule, so the budget check is vacuous
        boost = [(i, B, 0.95, 45.0 + i * 0.001, 7) for i in range(19)]
        res, _ = self.rows(boost + [p for p in POOL if p[4] != 7])
        self.assertEqual(res.status, "ok")

    def test_short_and_bad_sps_are_skipped(self):
        short = make_pool(POOL)
        short = bf.PoolPath(*[a[:5] for a in (short.sl, short.q, short.b, short.isb, short.sol, short.tok, short.th, short.bt)])
        self.assertEqual(bf.score_pool(short, S0, V).status, "short")
        pool = make_pool(POOL)
        pool.bt = (BT0 + pool.sl - S0).astype(np.int64)  # 1 s per slot
        self.assertEqual(bf.score_pool(pool, S0, V).status, "sps")
        pool = make_pool(POOL)
        pool.sl = pool.sl[::-1].copy()
        self.assertEqual(bf.score_pool(pool, S0, V).status, "unsorted")


class Legs(unittest.TestCase):
    def mk_rows(self, n=40):
        rng = np.random.default_rng(3)
        rows = []
        for i in range(n):
            ssb, nb = int(rng.integers(0, 4)), float(rng.integers(0, 5)) * SOL
            for leg in bf.LEGS:
                for label, _ in bf.STAKES_LAMPORTS:
                    rows.append(dict(day=f"2026-08-{14 + i % 5}", blk="explore-0814", mint=f"m{i}", leg=leg, stake_sol=label, ssb=ssb, nb_lamports=nb,
                                     gross=0.1 * (i % 7 - 3), pnl_nofail_lamports=float(rng.normal(1e6, 5e6))))
        return rows

    def test_flat_and_pressure_legs(self):
        rows = self.mk_rows()
        bf.apply_fail_legs(rows)
        for r in rows:
            pnl = r["pnl_nofail_lamports"]
            self.assertAlmostEqual(r["pnl_flat_lamports"], 0.85 * pnl + 0.15 * -55_000, places=6)
            self.assertAlmostEqual(r["pnl_press_lamports"], (1 - r["p_fail"]) * pnl + r["p_fail"] * -55_000, places=6)
        for leg in bf.LEGS:  # intercept refit so the mean fail rate on this book's sends is 0.289
            p = [r["p_fail"] for r in rows if r["leg"] == leg and r["stake_sol"] == "0.1"]
            self.assertAlmostEqual(sum(p) / len(p), 0.289, places=6)

    def test_more_pressure_fails_more(self):
        rows = self.mk_rows()
        for i, r in enumerate(rows):  # two clusters on top of the random book
            if i % 8 == 0:
                r["ssb"], r["nb_lamports"] = 0, 0.0
            elif i % 8 == 1:
                r["ssb"], r["nb_lamports"] = 3, 4.0 * SOL
        bf.apply_fail_legs(rows)
        lo = [r["p_fail"] for r in rows if r["ssb"] == 0 and r["nb_lamports"] == 0]
        hi = [r["p_fail"] for r in rows if r["ssb"] == 3 and r["nb_lamports"] == 4 * SOL]
        self.assertTrue(lo and hi and max(lo) < min(hi))

    def test_summarize_shape(self):
        rows = self.mk_rows()
        bf.apply_fail_legs(rows)
        out = bf.summarize(rows)
        self.assertEqual(sorted(out), ["binding|0.1", "binding|0.25", "primary|0.1", "primary|0.25"])
        cell = out["primary|0.1"]
        self.assertEqual(cell["flat"]["n"], 40)
        self.assertEqual(sorted(cell["by_block"]), ["explore-0814"])
        self.assertIn("note", cell["gate_arithmetic"])
        json.dumps(out)


class GateStats(unittest.TestCase):
    def test_constant_pnl_has_degenerate_ci(self):
        days = [f"d{i}" for i in range(6) for _ in range(3)]
        g = bf.gate_stats([1e7] * 18, days, 1e8)
        self.assertAlmostEqual(g["mean_pct"], 10.0)
        self.assertAlmostEqual(g["ci5_pct"], 10.0)
        self.assertAlmostEqual(g["ci95_pct"], 10.0)
        self.assertEqual((g["n"], g["days"], g["days_pos"]), (18, 6, 6))

    def test_ex_top3_ex_best_day_days_positive(self):
        x = np.array([10, 1, 1, 1, -5, -1], float) * SOL
        g = bf.gate_stats(x, ["a", "a", "b", "b", "c", "c"], 1e9)
        self.assertAlmostEqual(g["total_sol"], 7.0)
        self.assertAlmostEqual(g["ex_top3_sol"], 7.0 - 12.0)
        self.assertAlmostEqual(g["ex_best_day_sol"], 7.0 - 11.0)  # day a = 11
        self.assertEqual(g["days_pos"], 2)  # a: +11, b: +2, c: -6
        self.assertAlmostEqual(g["median_pct"], 100.0)

    def test_matches_a_direct_day_bootstrap(self):
        rng0 = np.random.default_rng(11)
        x = rng0.normal(2e6, 1e7, 60)
        days = np.array([f"2026-09-{3 + i % 7:02d}" for i in range(60)])
        # the lab's common.gate: rng.choice over the day labels themselves, concatenate the picked days, mean, 5th / 95th percentile
        ud = np.unique(days)
        rng = np.random.default_rng(1)
        bm = np.array([np.concatenate([x[days == d] for d in rng.choice(ud, size=len(ud), replace=True)]).mean() for _ in range(1000)]) / 1e9
        g = bf.gate_stats(x, days, 1e8)
        self.assertAlmostEqual(g["ci5_pct"], 100 * np.percentile(bm, 5) / 0.1, places=9)
        self.assertAlmostEqual(g["ci95_pct"], 100 * np.percentile(bm, 95) / 0.1, places=9)
        self.assertEqual(g, bf.gate_stats(x, days, 1e8))  # deterministic

    def test_gate_arithmetic(self):
        good = dict(n=100, days=5, days_pos=3, ci5_pct=0.1, ex_top3_sol=0.1)
        self.assertTrue(bf.gate_arithmetic(good, good)["all"])
        self.assertFalse(bf.gate_arithmetic(good, dict(good, n=99))["all"])
        self.assertFalse(bf.gate_arithmetic(dict(good, days_pos=2), good)["all"])  # 2 of 5 is not a majority
        self.assertFalse(bf.gate_arithmetic(good, dict(good, ci5_pct=0.0))["all"])
        self.assertFalse(bf.gate_arithmetic(dict(good, ex_top3_sol=0.0), good)["all"])
        self.assertFalse(bf.gate_arithmetic({"n": 0}, good)["all"])


class Refusals(unittest.TestCase):
    def test_constants_match_the_gate_replay_refusals(self):
        try:
            from tools import cap_pick_gate_replay as cp
        except ImportError:
            self.skipTest("cap_pick_gate_replay needs lightgbm")
        self.assertEqual((bf.VOID_LO, bf.VOID_HI, bf.CUTOFF), (cp.VOID_LO, cp.VOID_HI, cp.CUTOFF))
        self.assertTrue(set(cp.FORBIDDEN_NAMES) <= set(bf.FORBIDDEN_NAMES))

    def test_rule_sha_pin(self):
        self.assertEqual(len(bf.RULE_SHA256), 64)

    def test_names(self):
        for bad in ("fresh-0802", "fresh-0808", "fresh-0828", "forward-1002", "forward-1016", "forward-paper", "x/helius.env"):
            with self.assertRaises(bf.Refused):
                bf.refuse_name(f"/data/mal/{bad}/meta")
        bf.refuse_name(bf.DEFAULT_WORK_DIR)

    def test_hours(self):
        for h in ("2026-10-02T10", "2026-10-03T00", "2027-01-01T00"):
            with self.assertRaises(bf.Refused):
                bf.refuse_hour(h)
        for h in ("2026-09-15T12", "2026-09-17T05", "2026-09-18T22"):
            with self.assertRaises(bf.Refused):
                bf.refuse_hour(h)
        for h in ("2026-09-15T11", "2026-09-18T23", "2026-10-02T09"):
            bf.refuse_hour(h)

    def test_days(self):
        for d in bf.DISCOVERY_DAYS + bf.CONFIRMATION_DAYS:
            bf.refuse_day(d)
        self.assertEqual((len(bf.DISCOVERY_DAYS), len(bf.CONFIRMATION_DAYS)), (15, 21))
        for d in ("2026-10-02", "2026-10-03", "2026-10-16", "2026-08-02", "2026-08-08", "2026-08-29", "2026-09-16", "2026-09-17", "2026-9-3", "../x"):
            with self.assertRaises(bf.Refused):
                bf.refuse_day(d)

    def test_blocks(self):
        for blk in bf.ALLOWED_BLOCKS:
            bf.refuse_block(blk)
        for blk in ("fresh-0802", "fresh-0808", "fresh-0828", "forward-1016", "something-else"):
            with self.assertRaises(bf.Refused):
                bf.refuse_block(blk)

    def test_wait_for_memory(self):
        with tempfile.TemporaryDirectory() as d:
            mi = Path(d) / "meminfo"
            mi.write_text("MemTotal: 1 kB\nMemAvailable:   41943040 kB\n")  # 40 GB
            self.assertAlmostEqual(bf.wait_for_memory(30, meminfo=str(mi)), 40.0)
            mi.write_text("MemAvailable:   1048576 kB\n")  # 1 GB
            with self.assertRaises(bf.Refused):
                bf.wait_for_memory(30, max_wait_s=0, meminfo=str(mi))

    def test_cli_refuses_before_reading_anything(self):
        with tempfile.TemporaryDirectory() as d:
            for args in (["--days", "2026-10-02"], ["--days", "2026-08-02"], ["--days", "2026-09-16"],
                         ["--days", "2026-08-14", "--work-dir", "/data/mal/blocks/forward-1016"]):
                rc = quiet_main([*args, "--out-dir", d, "--min-avail-gb", "0"] if "--work-dir" in args else
                             [*args, "--work-dir", d, "--out-dir", str(Path(d) / "o"), "--min-avail-gb", "0"])
                self.assertEqual(rc, 2, args)
            self.assertEqual(quiet_main(["--days", "2026-08-14", "--work-dir", d, "--out-dir", "/x/fresh-0828/o", "--min-avail-gb", "0"]), 2)
            self.assertFalse((Path(d) / "o").exists())


@unittest.skipUnless(HAVE_PANDAS, "needs pandas + pyarrow")
class EndToEnd(unittest.TestCase):
    DAY = "2026-08-14"

    def build(self, work: Path, blk="explore-0814", mbt=1_786_720_211):
        pool = make_pool(POOL)
        (work / "meta").mkdir(parents=True)
        (work / "paths").mkdir(parents=True)
        mint = "MintA" + "x" * 30
        pd.DataFrame(dict(mint=[mint, "MintNoPath", "MintOutOfV"], pool=["p", "p2", "p3"], mslot=[S0 - 5] * 3, mbt=[mbt] * 3, s0=[S0] * 3,
                          v=[int(V), int(V), int(30e9)], npools=[1, 1, 1], blk=[blk] * 3, cslot=[1.0] * 3, day=[self.DAY] * 3,
                          uncensored=[True] * 3)).to_parquet(work / "meta" / f"{self.DAY}.parquet")
        n = len(pool.sl)
        pd.DataFrame(dict(mint=[mint] * n, slot=pool.sl, isbuy=pool.isb, sol=pool.sol.astype(np.int64), tok=pool.tok, q=pool.q, b=pool.b,
                          th=pool.th, bt=pool.bt)).to_parquet(work / "paths" / f"{self.DAY}.parquet")

    def test_cli_writes_rows_and_summary(self):
        with tempfile.TemporaryDirectory() as d:
            work, out = Path(d) / "work", Path(d) / "out"
            self.build(work)
            self.assertEqual(quiet_main(["--days", self.DAY, "--work-dir", str(work), "--out-dir", str(out), "--min-avail-gb", "0"]), 0)
            lines = (out / "rows.csv").read_text().splitlines()
            self.assertEqual(lines[0].split(","), list(bf.ROW_COLUMNS))
            self.assertEqual(len(lines) - 1, 4)
            summary = json.loads((out / "summary.json").read_text())
            self.assertEqual((summary["schema"], summary["rule_sha256"], summary["diagnostics"]["pools"], summary["diagnostics"]["ok"]),
                             (bf.SCHEMA, bf.RULE_SHA256, 1, 1))
            cell = summary["results"]["primary|0.1"]
            self.assertEqual((cell["flat"]["n"], cell["press"]["n"], cell["nofail"]["n"]), (1, 1, 1))
            pool = make_pool(POOL)
            want, _ = expected_pnl(20.9e9 + V, pool.b[8], 16.0e9 + V, pool.b[11], STAKE)
            self.assertAlmostEqual(cell["nofail"]["mean_sol"] * 1e9, want, places=0)

    def test_loader_refuses_a_sealed_block_and_late_graduations(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "work"
            self.build(work, mbt=1_790_000_000)  # 2026-09-21T14: not in the void, before the cutoff: allowed
            self.assertEqual(len(list(bf.load_day(work, self.DAY))), 1)
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "work"
            self.build(work, blk="fresh-0808")
            with self.assertRaises(bf.Refused):
                list(bf.load_day(work, self.DAY))
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "work"
            self.build(work, mbt=1_791_000_000)  # 2026-10-03
            with self.assertRaises(bf.Refused):
                list(bf.load_day(work, self.DAY))
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "work"
            self.build(work, mbt=1_789_500_000)  # 2026-09-15T20 is in the EXP-009 void
            with self.assertRaises(bf.Refused):
                list(bf.load_day(work, self.DAY))


if __name__ == "__main__":
    unittest.main()
