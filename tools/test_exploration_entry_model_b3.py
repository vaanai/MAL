"""Tests for the pure pieces of tools/exploration_entry_model_b3.py: the
winsorization helper, the extended (both-fail-model) cohort stats, the
pooling-over-9-days helpers, and the pre-stated candidate screen. Also a
small end-to-end check (real lightgbm, tiny synthetic data) that the new
regression settings (S1, S3) actually fit and predict, not just parse.

Exploration only. See ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md.
"""

from __future__ import annotations

import random
import unittest

from tools.exploration_entry_model_b3 import (
    DAYS_ALL,
    SETTINGS_B3,
    _ci_lo,
    _cohort_stats_b3,
    _pool_rows,
    evaluate_fold_b3,
    leave_one_day_out_b3,
    pooled_cohort_report,
    pooled_split_by_source,
    screen_candidate,
    winsorize_train_labels,
)
from tools.paper_curve_math import LAMPORTS_PER_SOL


def _row(day: str, press_lamports: float, flat_lamports: float | None = None, pool: str = "A", n_features: int = 20) -> dict:
    if flat_lamports is None:
        flat_lamports = press_lamports
    return {
        "mint": f"mint-{random.random()}",
        "spec": "tpsl_tp50_sl30",
        "day": day,
        "status": 0,
        "filled": True,
        "gross": 0,
        "flat": flat_lamports,
        "press": press_lamports,
        "pool": pool,
        "features": {f"f{i}": float(i) for i in range(n_features)},
    }


class WinsorizeTests(unittest.TestCase):
    def test_empty_input_returns_empty_and_zero_bounds(self) -> None:
        clipped, p1, p99 = winsorize_train_labels([])
        self.assertEqual(clipped, [])
        self.assertEqual((p1, p99), (0.0, 0.0))

    def test_clips_extreme_values_to_the_1st_99th_percentile(self) -> None:
        vals = list(range(1, 101))  # 1..100
        clipped, p1, p99 = winsorize_train_labels([float(v) for v in vals])
        self.assertLess(max(clipped), 100.0)  # the max (100) got pulled down
        self.assertGreater(min(clipped), 1.0)  # the min (1) got pulled up
        self.assertAlmostEqual(max(clipped), p99)
        self.assertAlmostEqual(min(clipped), p1)

    def test_interior_values_are_unchanged(self) -> None:
        vals = [float(v) for v in range(1, 101)]
        clipped, _p1, _p99 = winsorize_train_labels(vals)
        self.assertAlmostEqual(clipped[49], 50.0)  # the 50th value, well inside 1st/99th pct

    def test_does_not_use_any_information_outside_its_own_argument(self) -> None:
        # Pure function: same input (even from a different fold) gives the
        # same clip bounds -- there is no hidden global state to leak a
        # held-out day's labels into a training fold's clip.
        a = winsorize_train_labels([1.0, 2.0, 3.0, 100.0])
        b = winsorize_train_labels([1.0, 2.0, 3.0, 100.0])
        self.assertEqual(a, b)


class CohortStatsB3Tests(unittest.TestCase):
    def test_reports_both_fail_models_total_and_ex_top3(self) -> None:
        rows = [_row("2026-09-19", v) for v in (100, 90, 80, 1, 1, 1)]
        out = _cohort_stats_b3(rows)
        self.assertEqual(out["n"], 6)
        self.assertAlmostEqual(out["press_net_total_sol"], sum(r["press"] for r in rows) / LAMPORTS_PER_SOL)
        self.assertAlmostEqual(out["flat_net_total_sol"], sum(r["flat"] for r in rows) / LAMPORTS_PER_SOL)
        self.assertAlmostEqual(out["press_ex_top3_sol"], 3 / LAMPORTS_PER_SOL)
        self.assertAlmostEqual(out["flat_ex_top3_sol"], 3 / LAMPORTS_PER_SOL)

    def test_empty_cohort_does_not_crash(self) -> None:
        out = _cohort_stats_b3([])
        self.assertEqual(out["n"], 0)
        self.assertIsNone(out["flat_net_total_sol"])
        self.assertIsNone(out["press_net_total_sol"])


class CiLoTests(unittest.TestCase):
    def test_deterministic_for_a_fixed_seed(self) -> None:
        vals = [1.0, -2.0, 3.0, 0.5, -0.5, 2.0, -1.0]
        self.assertEqual(_ci_lo(vals), _ci_lo(vals))

    def test_empty_is_zero(self) -> None:
        self.assertEqual(_ci_lo([]), 0.0)

    def test_all_positive_values_give_a_positive_lower_bound(self) -> None:
        vals = [10.0] * 50
        self.assertAlmostEqual(_ci_lo(vals), 10.0)


class PoolRowsTests(unittest.TestCase):
    def test_pools_only_trained_folds_top_cohort(self) -> None:
        lodo = {
            "d1": {"trained": True, "top10_rows": [_row("d1", 1)], "top20_rows": [_row("d1", 1), _row("d1", 2)]},
            "d2": {"trained": False},
        }
        pooled, days = _pool_rows(lodo, "top10")
        self.assertEqual(len(pooled), 1)
        self.assertEqual(days, ["d1"])
        pooled20, _days = _pool_rows(lodo, "top20")
        self.assertEqual(len(pooled20), 2)


class PooledCohortReportTests(unittest.TestCase):
    def _lodo_two_days(self, press_d1: list[float], press_d2: list[float]) -> dict:
        rows_d1 = [_row("d1", v) for v in press_d1]
        rows_d2 = [_row("d2", v) for v in press_d2]
        cohorts_d1 = {"top10": _cohort_stats_b3(rows_d1)}
        cohorts_d2 = {"top10": _cohort_stats_b3(rows_d2)}
        return {
            "d1": {"trained": True, "cohorts": cohorts_d1, "top10_rows": rows_d1},
            "d2": {"trained": True, "cohorts": cohorts_d2, "top10_rows": rows_d2},
        }

    def test_counts_days_positive_per_fail_model_independently(self) -> None:
        # d1 positive under both; d2 negative under both.
        lodo = self._lodo_two_days([10, 10], [-10, -10])
        out = pooled_cohort_report(lodo, "top10")
        self.assertEqual(out["n_days_total"], 2)
        self.assertEqual(out["n_days_flat_positive"], 1)
        self.assertEqual(out["n_days_press_positive"], 1)
        self.assertEqual(out["n"], 4)

    def test_pooled_n_is_the_sum_across_trained_days_only(self) -> None:
        lodo = self._lodo_two_days([10], [20, 30])
        lodo["d3"] = {"trained": False}
        out = pooled_cohort_report(lodo, "top10")
        self.assertEqual(out["n"], 3)
        self.assertEqual(out["n_days_total"], 2)


class SplitBySourceTests(unittest.TestCase):
    def test_splits_pooled_rows_by_pool_tag(self) -> None:
        lodo = {
            "d1": {
                "trained": True,
                "top10_rows": [_row("d1", 5, pool="A"), _row("d1", -5, pool="C"), _row("d1", 1, pool="B")],
            }
        }
        out = pooled_split_by_source(lodo, "top10")
        self.assertEqual(out["A"]["n"], 1)
        self.assertEqual(out["C"]["n"], 1)
        self.assertEqual(out["B"]["n"], 1)


class ScreenCandidateTests(unittest.TestCase):
    def _pooled(self, flat_mean, flat_ex3, flat_days, press_mean, press_ex3, press_days, n_days_total=9) -> dict:
        return {
            "flat_net_mean_pct": flat_mean,
            "flat_ex_top3_sol": flat_ex3,
            "n_days_flat_positive": flat_days,
            "press_net_mean_pct": press_mean,
            "press_ex_top3_sol": press_ex3,
            "n_days_press_positive": press_days,
            "n_days_total": n_days_total,
        }

    def test_passes_only_when_all_three_conditions_hold_under_both_models(self) -> None:
        p = self._pooled(1.0, 0.5, 5, 1.0, 0.5, 5)
        out = screen_candidate(p)
        self.assertTrue(out["flat_ok"])
        self.assertTrue(out["press_ok"])
        self.assertTrue(out["candidate"])

    def test_fails_if_only_flat_model_passes(self) -> None:
        p = self._pooled(1.0, 0.5, 5, -1.0, 0.5, 5)
        out = screen_candidate(p)
        self.assertTrue(out["flat_ok"])
        self.assertFalse(out["press_ok"])
        self.assertFalse(out["candidate"])

    def test_fails_on_ex_top3_even_if_mean_and_days_pass(self) -> None:
        p = self._pooled(1.0, -0.5, 5, 1.0, 0.5, 5)
        out = screen_candidate(p)
        self.assertFalse(out["flat_ok"])
        self.assertFalse(out["candidate"])

    def test_fails_if_majority_of_days_not_positive(self) -> None:
        p = self._pooled(1.0, 0.5, 4, 1.0, 0.5, 4)  # 4/9 is not a majority
        out = screen_candidate(p)
        self.assertFalse(out["flat_ok"])
        self.assertFalse(out["candidate"])

    def test_exactly_half_is_not_a_majority(self) -> None:
        p = self._pooled(1.0, 0.5, 1, 1.0, 0.5, 1, n_days_total=2)
        out = screen_candidate(p)
        self.assertFalse(out["flat_ok"])


class ModuleShapeTests(unittest.TestCase):
    def test_exactly_three_settings_three_distinct_kinds(self) -> None:
        self.assertEqual(len(SETTINGS_B3), 3)
        kinds = {s["kind"] for s in SETTINGS_B3}
        self.assertEqual(kinds, {"lightgbm_reg", "lightgbm", "lightgbm_reg_winsor"})

    def test_settings_share_lgb_medium_hyperparameters(self) -> None:
        leaves = {s["num_leaves"] for s in SETTINGS_B3}
        self.assertEqual(leaves, {15})
        rates = {s["learning_rate"] for s in SETTINGS_B3}
        self.assertEqual(rates, {0.05})

    def test_nine_held_out_days_in_order(self) -> None:
        self.assertEqual(len(DAYS_ALL), 9)
        self.assertEqual(list(DAYS_ALL), sorted(DAYS_ALL))


class EndToEndFoldTests(unittest.TestCase):
    """Small synthetic dataset (real lightgbm, no I/O): proves each of the
    three settings actually fits and produces a ranking, not just that the
    dispatch code parses. Uses a feature that is linearly related to the
    label so a trained model's top decile should beat its bottom decile in
    expectation, though this is a smoke test, not a statistical claim."""

    def _rows(self, day: str, n: int, seed: int) -> list[dict]:
        rng = random.Random(seed)
        out = []
        for i in range(n):
            x = rng.uniform(-1, 1)
            noise = rng.uniform(-0.2, 0.2)
            press_pct = x * 10 + noise  # informative feature -> label relationship
            feats = {name: 0.0 for name in _feature_names()}
            first_feat = next(iter(feats))
            feats[first_feat] = x
            out.append(
                {
                    "mint": f"m{day}-{i}",
                    "spec": "tpsl_tp50_sl30",
                    "day": day,
                    "status": 0,
                    "filled": True,
                    "gross": 0,
                    "flat": press_pct * 5_000_000 / 100.0,
                    "press": press_pct * 5_000_000 / 100.0,  # ENTRY_SIZE(0.5 SOL)=5e8 lamports; /100*pct
                    "pool": "A",
                    "features": feats,
                }
            )
        return out

    def test_all_three_settings_train_and_rank_on_synthetic_data(self) -> None:
        days = ("2026-09-19", "2026-09-20", "2026-09-21")
        rows = []
        for i, d in enumerate(days):
            rows.extend(self._rows(d, 60, seed=i))
        for setting in SETTINGS_B3:
            lodo = leave_one_day_out_b3(rows, days, setting)
            trained_any = False
            for day in days:
                fold = lodo[day]
                if fold.get("trained"):
                    trained_any = True
                    self.assertIn("top10", fold["cohorts"])
                    self.assertIn("top20", fold["cohorts"])
                    self.assertGreater(fold["n_test"], 0)
            self.assertTrue(trained_any, f"setting {setting['id']} never trained on synthetic data")

    def test_winsor_setting_records_its_own_train_clip_bounds(self) -> None:
        days = ("2026-09-19", "2026-09-20", "2026-09-21")
        rows = []
        for i, d in enumerate(days):
            rows.extend(self._rows(d, 60, seed=i))
        winsor_setting = next(s for s in SETTINGS_B3 if s["kind"] == "lightgbm_reg_winsor")
        lodo = leave_one_day_out_b3(rows, days, winsor_setting)
        fold = lodo[days[0]]
        self.assertTrue(fold["trained"])
        self.assertIn("train_p1_pct", fold)
        self.assertIn("train_p99_pct", fold)
        self.assertLess(fold["train_p1_pct"], fold["train_p99_pct"])

    def test_evaluate_fold_b3_reports_not_trained_below_20_rows(self) -> None:
        rows_train = self._rows("2026-09-19", 5, seed=0)
        rows_test = self._rows("2026-09-20", 5, seed=1)
        for setting in SETTINGS_B3:
            out = evaluate_fold_b3(rows_train, rows_test, setting)
            self.assertFalse(out["trained"])
            self.assertEqual(out["n_train"], 5)


def _feature_names() -> list[str]:
    from tools.exploration_entry_model import FEATURE_NAMES

    return list(FEATURE_NAMES)


if __name__ == "__main__":
    unittest.main()
