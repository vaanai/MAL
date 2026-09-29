"""Tests for the pure pieces of tools/exploration_entry_model_b2.py: the
ex-top-3 concentration check, the day-consistency counter, and the pool-B
worker chunking. No live data required.

Exploration only. See ARTIFACTS/lab/exploration-entry-model-b2-2026-09-28.md.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.exploration_entry_model import _Feat, iter_rows_jsonl
from tools.exploration_entry_model_b2 import (
    DAYS_A,
    DAYS_ALL,
    DAYS_B,
    SETTINGS_B2,
    _chunk,
    _cohort_stats_b2,
    _ex_top3_sol,
    consistency_counts,
    leave_one_day_out_days,
    plan_workers_b,
    run_worker_b,
)
from tools.latency_curve import _Mint
from tools.oracle_live_adapter import POOL_B_HOURS
from tools.paper_curve_math import LAMPORTS_PER_SOL
from tools.paper_price_path import TapePrint


def _print(t_ms: int, slot: int, quote: int, base: int, venue: str = "pumpswap", side: str = "buy") -> TapePrint:
    price = quote / (base * 1000)
    return TapePrint(
        t_recv_ms=t_ms,
        slot=slot,
        event_index=1,
        venue=venue,
        side=side,
        sol_lamports=1_000_000_000,
        quote_reserve=quote,
        base_reserve=base,
        price_sol=price,
        market_cap_sol=price * 1_000_000_000,
        signature=f"sig-{slot}-{t_ms}",
        tx_index=0,
    )


class ExTop3Tests(unittest.TestCase):
    def test_empty_is_none(self) -> None:
        self.assertIsNone(_ex_top3_sol([]))

    def test_removes_exactly_the_three_largest(self) -> None:
        vals = [10, 5, 1, -2, -3]  # lamports; largest 3 are 10, 5, 1
        kept_expected = (-2 + -3) / LAMPORTS_PER_SOL
        self.assertAlmostEqual(_ex_top3_sol(vals), kept_expected)

    def test_fewer_than_three_values_removes_all_of_them(self) -> None:
        vals = [10, 5]
        self.assertAlmostEqual(_ex_top3_sol(vals), 0.0)

    def test_matches_the_promotion_gates_own_definition_shape(self) -> None:
        # Same operation the gate uses ("total SOL positive after removing
        # the top 3 trades"), just applied to one exploration cohort.
        vals = [100, 90, 80, 1, 1, 1]
        out = _ex_top3_sol(vals)
        self.assertAlmostEqual(out, 3 / LAMPORTS_PER_SOL)


class CohortStatsB2Tests(unittest.TestCase):
    def test_adds_total_and_ex_top3_sol_on_top_of_base_cohort_stats(self) -> None:
        rows = [
            {"gross": 0, "flat": 10, "press": v}
            for v in (100, 90, 80, 1, 1, 1)
        ]
        for r in rows:
            r["flat"] = r["press"]
        out = _cohort_stats_b2(rows)
        self.assertEqual(out["n"], 6)
        self.assertAlmostEqual(out["press_net_total_sol"], sum(r["press"] for r in rows) / LAMPORTS_PER_SOL)
        self.assertAlmostEqual(out["ex_top3_press_net_sol"], 3 / LAMPORTS_PER_SOL)

    def test_empty_cohort_does_not_crash(self) -> None:
        out = _cohort_stats_b2([])
        self.assertEqual(out["n"], 0)
        self.assertIsNone(out["press_net_total_sol"])
        self.assertIsNone(out["ex_top3_press_net_sol"])


class ConsistencyCountsTests(unittest.TestCase):
    def _fold(self, top10_pct: float | None, all_pct: float | None, ex_top3: float | None) -> dict:
        return {
            "trained": True,
            "cohorts": {
                "all": {"press_net_mean_pct": all_pct},
                "top10": {"press_net_mean_pct": top10_pct, "ex_top3_press_net_sol": ex_top3},
            },
        }

    def test_counts_lift_and_double_positive_separately(self) -> None:
        lodo = {
            "d1": self._fold(top10_pct=2.0, all_pct=0.5, ex_top3=0.1),  # lift + both positive
            "d2": self._fold(top10_pct=1.0, all_pct=2.0, ex_top3=0.1),  # no lift, but top10>0 and ex3>0
            "d3": self._fold(top10_pct=-1.0, all_pct=-2.0, ex_top3=-0.1),  # lift (less negative), not double-positive
            "d4": {"trained": False},  # untrained folds are excluded from the denominator
        }
        counts = consistency_counts(lodo)
        self.assertEqual(counts["n_days"], 3)
        self.assertEqual(counts["n_top10_beats_all"], 2)  # d1, d3
        self.assertEqual(counts["n_top10_positive_and_ex_top3_positive"], 2)  # d1, d2

    def test_all_untrained_gives_zero_denominator_not_a_crash(self) -> None:
        counts = consistency_counts({"d1": {"trained": False}})
        self.assertEqual(counts, {"n_days": 0, "n_top10_beats_all": 0, "n_top10_positive_and_ex_top3_positive": 0})


class LeaveOneDayOutDaysTests(unittest.TestCase):
    def test_too_few_rows_reports_not_trained_with_counts(self) -> None:
        rows = [{"day": "2026-09-19", "features": {}, "press": 1}]
        out = leave_one_day_out_days(rows, DAYS_A, SETTINGS_B2[0])
        for day in DAYS_A:
            self.assertFalse(out[day]["trained"])

    def test_rows_outside_the_requested_days_are_dropped_not_mixed_in(self) -> None:
        rows = [{"day": "2026-01-01", "features": {}, "press": 1}]
        out = leave_one_day_out_days(rows, DAYS_A, SETTINGS_B2[0])
        for day in DAYS_A:
            self.assertEqual(out[day]["n_train"], 0)


class DayConstantsTests(unittest.TestCase):
    def test_days_all_is_a_and_b_concatenated_six_unique_days(self) -> None:
        self.assertEqual(DAYS_ALL, DAYS_A + DAYS_B)
        self.assertEqual(len(set(DAYS_ALL)), 6)

    def test_settings_b2_is_medium_and_logreg_only(self) -> None:
        ids = {s["id"] for s in SETTINGS_B2}
        self.assertEqual(ids, {"lgb_medium", "logreg_l2"})


class PlanWorkersBTests(unittest.TestCase):
    def test_chunk_covers_every_hour_exactly_once_in_order(self) -> None:
        chunks = _chunk(list(POOL_B_HOURS), 3)
        flat = [h for c in chunks for h in c]
        self.assertEqual(flat, list(POOL_B_HOURS))
        self.assertLessEqual(len(chunks), 3)

    def test_plan_uses_at_most_three_workers_and_buffers_forward_only(self) -> None:
        plan = plan_workers_b(max_workers=3, buffer_hours=2)
        self.assertLessEqual(len(plan), 3)
        seen_home: list[str] = []
        for worker_id, home, buf in plan:
            seen_home.extend(home)
            if buf:
                # buffer hours must come strictly after this worker's home range
                self.assertGreater(buf[0], home[-1])
        self.assertEqual(seen_home, list(POOL_B_HOURS))

    def test_last_worker_has_no_buffer_past_the_pool_end(self) -> None:
        plan = plan_workers_b(max_workers=3, buffer_hours=2)
        _worker_id, home, buf = plan[-1]
        self.assertEqual(home[-1], POOL_B_HOURS[-1])
        self.assertEqual(buf, [])


class RunWorkerBStreamToDiskTests(unittest.TestCase):
    """run_worker_b(rows_out_path=...) must produce the exact same rows a
    fully in-memory run would (same fix as pool A's
    test_exploration_entry_model.StreamToDiskTests, ported to pool B's
    wrapper). Fakes out `_hour_info_b`/`iter_trade_rows_sorted` with a tiny
    synthetic tape -- no Oracle live tape read."""

    def _hour_info(self, t0_ms: int) -> dict:
        return {
            "hour": "test-hour",
            "day": "2026-09-25",
            "end": t0_ms // 1000 + 40 * 60,
            "trade": Path("unused"),
            "create": None,
        }

    def _rows(self, t0_ms: int) -> list[dict]:
        bonding = {
            "type": "trade",
            "venue": "pump_bonding",
            "side": "buy",
            "quote_reserve": 80_000_000_000,
            "base_reserve": 400_000_000_000_000,
            "slot": 100,
            "event_index": 1,
            "sol_lamports": 1_000_000_000,
            "trader": "walletA",
            "token_raw": 500_000,
            "mint": "mintZ",
            "t_recv_ms": t0_ms + 500,
            "block_time": t0_ms // 1000,
        }
        migrate = {**bonding, "venue": "pumpswap", "slot": 105, "t_recv_ms": t0_ms + 20_000, "trader": "walletB", "quote_is_wsol": True}
        later = {**migrate, "slot": 110, "t_recv_ms": t0_ms + 20_000 + 1_900_000}
        return [bonding, migrate, later]

    def _all_creates(self, t0_ms: int) -> dict:
        entry = _print(t0_ms, 100, 80_000_000_000, 400_000_000_000_000)
        mint = _Mint(100, t0_ms, 0, entry)
        mint.had_bond = True
        return {"mintZ": (mint, _Feat("creatorZ", t0_ms, entry.price_sol))}

    def test_rows_written_to_disk_match_the_in_memory_rows(self) -> None:
        t0 = 1_790_294_700_000  # 2026-09-25T00:05:00Z -- inside home_keys[0]'s window

        def hour_info_fn(_key: str) -> dict:
            return self._hour_info(t0)

        def row_iter_fn(_path) -> list[dict]:
            return self._rows(t0)

        with mock.patch("tools.exploration_entry_model_b2._hour_info_b", side_effect=hour_info_fn), mock.patch(
            "tools.exploration_entry_model_b2.iter_trade_rows_sorted", side_effect=row_iter_fn
        ):
            in_memory = run_worker_b(0, ["2026-09-25T00"], [], self._all_creates(t0), {})
            self.assertTrue(in_memory)

            with tempfile.TemporaryDirectory() as tmp:
                out_path = Path(tmp) / "rows.jsonl"
                returned = run_worker_b(0, ["2026-09-25T00"], [], self._all_creates(t0), {}, out_path)
                self.assertEqual(returned, [])
                from_disk = list(iter_rows_jsonl(out_path))

        self.assertEqual(len(from_disk), len(in_memory))
        key = lambda r: (r["spec"], r["status"], r["flat"], r["press"], r["mint"])
        self.assertEqual(sorted(map(key, from_disk)), sorted(map(key, in_memory)))


if __name__ == "__main__":
    unittest.main()
