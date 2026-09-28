"""Exit-family exploration: data fence, spec grid, and the exit evaluators.

Exploration only. No live runner, no network, no promotion claim.
"""

from __future__ import annotations

import unittest

from tools.latency_curve import MISS, SEND, _try_buy
from tools.exploration_exits import (
    ENTRY_LAND_K,
    ENTRY_PORTAL_PPM,
    ENTRY_PRIORITY_LAMPORTS,
    ENTRY_SIZE,
    POOL_END,
    POOL_HOURS,
    POOL_START,
    SPECS,
    _curve,
    _hour_info,
    _pressure,
    _state_index,
    _slot_time,
    build_specs,
    eval_spec,
    rank_by_min_day_pressure,
    score_migration,
    summarize,
)
from tools.paper_curve_math import LAMPORTS_PER_SOL
from tools.paper_price_path import TapePrint
from tools.latency_curve import _Mint


def _print(t_ms: int, slot: int, quote: int, base: int, venue: str = "pumpswap") -> TapePrint:
    price = quote / (base * 1000)
    return TapePrint(
        t_recv_ms=t_ms,
        slot=slot,
        event_index=1,
        venue=venue,
        side="buy",
        sol_lamports=1_000_000,
        quote_reserve=quote,
        base_reserve=base,
        price_sol=price,
        market_cap_sol=price * 1_000_000_000,
        signature=f"sig-{slot}-{t_ms}",
        tx_index=0,
    )


class DataFenceTests(unittest.TestCase):
    def test_pool_is_exactly_the_three_exploration_days(self) -> None:
        self.assertEqual(POOL_START, "2026-09-19T01")
        self.assertEqual(POOL_END, "2026-09-21T23")
        self.assertEqual(len(POOL_HOURS), 71)
        self.assertEqual(POOL_HOURS[0], POOL_START)
        self.assertEqual(POOL_HOURS[-1], POOL_END)
        self.assertNotIn("2026-09-19T00", POOL_HOURS)
        self.assertNotIn("2026-09-22T00", POOL_HOURS)

    def test_hour_info_rejects_an_hour_outside_the_pool(self) -> None:
        with self.assertRaises(AssertionError):
            _hour_info("2026-09-18T23")
        with self.assertRaises(AssertionError):
            _hour_info("2026-09-22T00")

    def test_entry_matches_the_frozen_cell(self) -> None:
        self.assertEqual(ENTRY_SIZE, 500_000_000)
        self.assertEqual(ENTRY_PORTAL_PPM, 0)
        self.assertEqual(ENTRY_PRIORITY_LAMPORTS, 500_000)
        self.assertEqual(ENTRY_LAND_K, 1)


class SpecGridTests(unittest.TestCase):
    def test_at_most_sixty_cells_no_duplicates(self) -> None:
        specs = build_specs()
        self.assertLessEqual(len(specs), 60)
        ids = [s["id"] for s in specs]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(specs), 41)

    def test_families_present(self) -> None:
        families = {s["family"] for s in SPECS}
        self.assertEqual(families, {"tp_sl_grid", "trailing_stop", "time_cap", "partial_ladder"})


class ExitEvaluatorTests(unittest.TestCase):
    """Synthetic single-mint price paths, not real tape."""

    def _mint_with_path(self, prints: list[TapePrint], mig_slot: int, mig_ms: int) -> _Mint:
        mint = _Mint(prints[0].slot, prints[0].t_recv_ms, 0, None)
        mint.had_bond = True
        for pr in prints:
            mint.add(pr)
        mint.mig_slot = mig_slot
        mint.mig_ms = mig_ms
        return mint

    def test_tp_hit_closes_at_the_delayed_state(self) -> None:
        t0 = 1_700_000_000_000
        entry = _print(t0, 100, 80_000_000_000, 400_000_000_000_000)
        # +60% price move -> should trigger tp50 well before the 30 min cap.
        up = _print(t0 + 60_000, 105, 128_000_000_000, 400_000_000_000_000)
        prints = [entry, up]
        mint = self._mint_with_path(prints, mig_slot=100, mig_ms=t0)
        rows = score_migration(mint, _curve(), tape_through_ms=t0 + 40 * 60 * 1000)
        by_spec = {r["spec"]: r for r in rows}
        self.assertIn("tpsl_tp50_sl30", by_spec)
        self.assertTrue(by_spec["tpsl_tp50_sl30"]["filled"])
        self.assertEqual(by_spec["tpsl_tp50_sl30"]["status"], SEND)
        # A gain should net positive gross before fees.
        self.assertGreater(by_spec["tpsl_tp50_sl30"]["gross"], 0)

    def test_slippage_cap_breach_is_a_uniform_miss_for_every_spec(self) -> None:
        t0 = 1_700_000_000_000
        # ref price comes from the first print at mig_slot; a second print in the
        # same slot more than 15% above it breaches the slippage cap on entry.
        ref_print = _print(t0, 100, 80_000_000_000, 400_000_000_000_000)
        jumped = _print(t0 + 10, 100, 200_000_000_000, 400_000_000_000_000)
        mint = self._mint_with_path([ref_print, jumped], mig_slot=100, mig_ms=t0)
        rows = score_migration(mint, _curve(), tape_through_ms=t0 + 60_000)
        self.assertEqual(len(rows), len(SPECS))
        self.assertTrue(all(r["status"] == MISS and not r["filled"] for r in rows))
        miss_flat = -ENTRY_PRIORITY_LAMPORTS
        for row in rows:
            self.assertAlmostEqual(row["flat"], miss_flat)
            self.assertAlmostEqual(row["press"], miss_flat)

    def test_censored_exit_is_omitted_not_zeroed(self) -> None:
        t0 = 1_700_000_000_000
        entry = _print(t0, 100, 80_000_000_000, 400_000_000_000_000)
        up = _print(t0 + 60_000, 105, 128_000_000_000, 400_000_000_000_000)
        mint = self._mint_with_path([entry, up], mig_slot=100, mig_ms=t0)
        # tape_through_ms cuts off before any exit (even the entry landing) can close.
        rows = score_migration(mint, _curve(), tape_through_ms=t0 + 1)
        self.assertEqual(rows, [])


class SummaryTests(unittest.TestCase):
    def test_rank_by_min_day_pressure_prefers_consistency_over_pooled_mean(self) -> None:
        rows = []
        # spec A: great on day 19, terrible on day 20 and 21 -> low min-day.
        for day, val in (("2026-09-19", 50_000_000), ("2026-09-20", -30_000_000), ("2026-09-21", -30_000_000)):
            rows.append({"spec": "A", "day": day, "status": SEND, "filled": True, "gross": val, "flat": val, "press": val})
        # spec B: modestly positive every day -> higher min-day even if pooled mean is lower.
        for day in ("2026-09-19", "2026-09-20", "2026-09-21"):
            rows.append(
                {"spec": "B", "day": day, "status": SEND, "filled": True, "gross": 5_000_000, "flat": 5_000_000, "press": 5_000_000}
            )
        test_specs = [
            {"id": "A", "family": "test", "desc": "A"},
            {"id": "B", "family": "test", "desc": "B"},
        ]
        cells = summarize(rows, specs=test_specs)
        ranking = rank_by_min_day_pressure(cells)
        self.assertIn("A", cells)
        self.assertIn("B", cells)
        self.assertLess(ranking.index("B"), ranking.index("A"))


if __name__ == "__main__":
    unittest.main()
