"""Declared pressure curve: monotone, calibrated to 28.9%, causal."""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from tools.paper_curve_math import LAMPORTS_PER_SOL, PRIORITY_FEE_LAMPORTS
from tools.paper_fail_pressure import (
    B_SLOT,
    B_SOL,
    TARGET_FAIL_RATE,
    Attempt,
    Pressure,
    fit_curve,
    headline_pnl,
    mean_p,
    pressure_from_prints,
    summarize,
)
from tools.paper_tape_scoreboard import DEFAULT_FAIL_RATE


class _Print:
    def __init__(self, t_ms: int, slot: int, side: str, sol_lamports: int) -> None:
        self.t_recv_ms = t_ms
        self.slot = slot
        self.side = side
        self.sol_lamports = sol_lamports


def _p(slot_n: int, sol: float) -> Pressure:
    return Pressure(same_slot_buys=slot_n, nearby_buy_lamports=int(round(sol * LAMPORTS_PER_SOL)))


class PressureTests(unittest.TestCase):
    def test_future_print_is_ignored_and_window_is_inclusive(self) -> None:
        prints = [
            _Print(0, 5, "buy", 1_000_000_000),
            _Print(1000, 5, "buy", 2_000_000_000),
            _Print(1500, 9, "buy", 3_000_000_000),
            _Print(1600, 5, "sell", 9_000_000_000),
            _Print(5000, 5, "buy", 100_000_000_000),
        ]
        got = pressure_from_prints(prints, t_entry_ms=2000, entry_slot=5, nearby_ms=2000)
        self.assertEqual(got.same_slot_buys, 2)
        self.assertEqual(got.nearby_buy_lamports, 6_000_000_000)

    def test_anchor_slot_does_not_count_slot_zero(self) -> None:
        prints = [_Print(10, 0, "buy", 1_000_000_000)]
        got = pressure_from_prints(prints, t_entry_ms=1000, entry_slot=None, nearby_ms=2000)
        self.assertEqual(got.same_slot_buys, 0)
        self.assertEqual(got.nearby_buy_lamports, 1_000_000_000)

    def test_more_pressure_raises_p_at_a_fixed_intercept(self) -> None:
        curve = fit_curve([_p(0, 0.0)], scale=1.0)
        low = curve.p(_p(0, 0.0))
        mid = curve.p(_p(3, 1.0))
        high = curve.p(_p(12, 20.0))
        self.assertLess(low, mid)
        self.assertLess(mid, high)

    def test_scale_zero_is_flat_at_the_tape_share(self) -> None:
        pressures = [_p(0, 0.0), _p(4, 2.0), _p(30, 50.0)]
        curve = fit_curve(pressures, scale=0.0)
        self.assertEqual(curve.b_slot, 0.0)
        self.assertEqual(curve.b_sol, 0.0)
        self.assertAlmostEqual(curve.intercept, math.log(TARGET_FAIL_RATE / (1.0 - TARGET_FAIL_RATE)), places=6)
        for pr in pressures:
            self.assertAlmostEqual(curve.p(pr), TARGET_FAIL_RATE, places=6)

    def test_refit_keeps_the_mean_and_the_order(self) -> None:
        pressures = [_p(n, sol) for n, sol in ((0, 0.0), (1, 0.2), (2, 1.0), (8, 5.0), (20, 30.0))]
        for scale in (0.5, 1.0, 2.0):
            curve = fit_curve(pressures, scale=scale)
            fitted = mean_p(curve, pressures)
            assert fitted is not None
            self.assertAlmostEqual(fitted, TARGET_FAIL_RATE, places=6)
            self.assertAlmostEqual(curve.b_slot, B_SLOT * scale)
            self.assertAlmostEqual(curve.b_sol, B_SOL * scale)
            ps = [curve.p(pr) for pr in pressures]
            self.assertEqual(ps, sorted(ps))

    def test_higher_p_flatters_a_negative_landed_trade(self) -> None:
        landed = -5_000_000
        low = Attempt("filled", "realized", landed, _p(0, 0.0))
        high = Attempt("filled", "realized", landed, _p(40, 80.0))
        curve = fit_curve([low.pressure, high.pressure, _p(1, 0.5)], scale=1.0)
        mixed_low = headline_pnl(low, curve)
        mixed_high = headline_pnl(high, curve)
        assert mixed_low is not None and mixed_high is not None
        self.assertLess(landed, mixed_low)
        self.assertLess(mixed_low, mixed_high)
        self.assertLess(mixed_high, 0)

    def test_miss_is_not_mixed_again(self) -> None:
        attempt = Attempt("missed_slippage", "not_entered", -PRIORITY_FEE_LAMPORTS, _p(20, 40.0))
        curve = fit_curve([_p(0, 0.0), _p(5, 3.0)], scale=1.0)
        self.assertEqual(headline_pnl(attempt, curve), -PRIORITY_FEE_LAMPORTS)
        stats = summarize([attempt], curve)
        self.assertEqual(stats["n"], 1)
        self.assertEqual(stats["miss_n"], 1)
        self.assertEqual(stats["send_n"], 0)
        self.assertIsNone(stats["mean_p"])
        self.assertAlmostEqual(stats["total_sol"], -PRIORITY_FEE_LAMPORTS / LAMPORTS_PER_SOL)

    def test_censored_stays_out_of_n(self) -> None:
        attempt = Attempt("filled", "censored", None, _p(2, 1.0))
        curve = fit_curve([_p(0, 0.0)], scale=1.0)
        self.assertIsNone(headline_pnl(attempt, curve))
        stats = summarize([attempt], curve)
        self.assertEqual(stats["n"], 0)
        self.assertEqual(stats["censored_n"], 1)

    def test_live_rate_and_service_are_untouched(self) -> None:
        self.assertEqual(DEFAULT_FAIL_RATE, 0.15)
        root = Path(__file__).resolve().parent
        forward = (root / "forward_paper.py").read_text(encoding="utf-8")
        self.assertNotIn("paper_fail_pressure", forward)
        self.assertNotIn("paper_fail_table", forward)


if __name__ == "__main__":
    unittest.main()
