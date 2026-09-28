"""Learned migrate entry filter: no-lookahead feature test, plus the small
pure-function pieces (causal event filtering, creator prior-mint count,
cohort stats, best-setting selection).

Exploration only. No live runner, no network, no promotion claim.
"""

from __future__ import annotations

import random
import unittest

from tools.exploration_entry_model import (
    CREATOR_LOOKBACK_MS,
    DAYS,
    FEATURE_NAMES,
    SETTINGS,
    TARGET_SPEC_IDS,
    _cohort_stats,
    causal_events,
    choose_best_setting,
    compute_features,
    count_prior_creates,
)


def _event(t_ms: int, side: str, trader: str, sol_lamports: int, token_raw: int, price: float | None) -> tuple:
    return (t_ms, side, trader, sol_lamports, token_raw, price)


class NoLookaheadTests(unittest.TestCase):
    """The core exploration claim -- 'no future info' -- has to be provable,
    not asserted. causal_events is the single causal boundary every feature
    goes through; this shuffles and duplicates extra events at or after the
    cutoff into the input and checks the resulting features never move.
    """

    def setUp(self) -> None:
        t0 = 1_700_000_000_000
        self.create_ms = t0
        self.mig_ms = t0 + 45_000
        self.pre = [
            _event(t0 + 1_000, "buy", "walletA", 1_000_000_000, 500_000, 0.00010),
            _event(t0 + 2_000, "buy", "walletB", 2_000_000_000, 900_000, 0.00011),
            _event(t0 + 5_000, "sell", "walletA", 500_000_000, 200_000, 0.00012),
            _event(t0 + 30_000, "buy", "walletC", 3_000_000_000, 1_200_000, 0.00015),
        ]

    def _features(self, events: list[tuple]) -> dict[str, float]:
        causal = causal_events(events, self.mig_ms)
        return compute_features(
            causal,
            create_ms=self.create_ms,
            first_price=0.00009,
            mig_ms=self.mig_ms,
            creator_prior_mints_24h=2,
        )

    def test_events_at_or_after_cutoff_are_dropped(self) -> None:
        baseline = self._features(self.pre)
        post = [
            _event(self.mig_ms, "buy", "sniper_at_cutoff", 50_000_000_000, 9_000_000, 5.0),
            _event(self.mig_ms + 500, "sell", "walletA", 10_000_000_000, 3_000_000, 9.0),
            _event(self.mig_ms + 60_000, "buy", "late_buyer", 1_000_000_000, 100_000, 0.5),
        ]
        with_post = self._features(self.pre + post)
        self.assertEqual(baseline, with_post)

    def test_shuffled_and_duplicated_post_cutoff_events_still_dropped(self) -> None:
        baseline = self._features(self.pre)
        rng = random.Random(7)
        post = [
            _event(self.mig_ms + i * 137, "buy" if i % 2 == 0 else "sell", f"w{i}", 10_000_000 * (i + 1), 1_000 * (i + 1), 0.001 * (i + 1))
            for i in range(20)
        ]
        for _ in range(5):
            mixed = list(self.pre) + list(post) + list(post)  # duplicated too
            rng.shuffle(mixed)
            # re-insert the genuine pre-cutoff events at random positions
            # among the shuffled post-cutoff junk, worst case for a filter
            # that accidentally depends on input order.
            got = self._features(mixed)
            self.assertEqual(baseline, got)

    def test_an_event_exactly_at_the_cutoff_is_excluded(self) -> None:
        at_cutoff = _event(self.mig_ms, "buy", "exact", 999_000_000_000, 1, 50.0)
        kept = causal_events(self.pre + [at_cutoff], self.mig_ms)
        self.assertNotIn(at_cutoff, kept)
        self.assertEqual(len(kept), len(self.pre))

    def test_an_event_one_ms_before_the_cutoff_is_included(self) -> None:
        just_before = _event(self.mig_ms - 1, "sell", "late_but_legal", 1, 1, 0.2)
        kept = causal_events(self.pre + [just_before], self.mig_ms)
        self.assertIn(just_before, kept)


class ComputeFeaturesTests(unittest.TestCase):
    def test_empty_events_give_zeroed_features_not_a_crash(self) -> None:
        feats = compute_features([], create_ms=1_000, first_price=None, mig_ms=2_000, creator_prior_mints_24h=0)
        self.assertEqual(feats["n_bonding_trades"], 0.0)
        self.assertEqual(feats["buy_sol"], 0.0)
        self.assertEqual(feats["top_holder_share"], 0.0)
        for name in FEATURE_NAMES:
            if name in ("same_slot_buys", "nearby_buy_sol"):
                continue  # added by the caller, not compute_features
            self.assertIn(name, feats)

    def test_top_holder_share_is_the_largest_net_positive_balance(self) -> None:
        events = [
            _event(1_100, "buy", "whale", 10_000_000_000, 8_000_000, 0.001),
            _event(1_200, "buy", "minnow", 1_000_000_000, 500_000, 0.0011),
            _event(1_300, "sell", "whale", 1_000_000_000, 1_000_000, 0.0012),
        ]
        feats = compute_features(events, create_ms=1_000, first_price=0.0009, mig_ms=2_000, creator_prior_mints_24h=0)
        # whale net = 8,000,000 - 1,000,000 = 7,000,000; minnow net = 500,000
        self.assertAlmostEqual(feats["top_holder_share"], 7_000_000 / 7_500_000)

    def test_sniper_share_only_counts_buys_inside_the_window(self) -> None:
        events = [
            _event(1_500, "buy", "fast", 1_000_000_000, 100, 0.001),  # 500ms after create, inside 3s window
            _event(10_000, "buy", "slow", 1_000_000_000, 100, 0.001),  # 9s after create, outside window
        ]
        feats = compute_features(events, create_ms=1_000, first_price=0.0009, mig_ms=20_000, creator_prior_mints_24h=0)
        self.assertAlmostEqual(feats["sniper_buy_share"], 0.5)


class CreatorPriorMintsTests(unittest.TestCase):
    def test_counts_only_the_trailing_24h_and_excludes_self(self) -> None:
        day_ms = 24 * 3_600 * 1000
        t0 = 10 * day_ms
        hist = {"creatorA": sorted([t0 - day_ms - 1, t0 - 1000, t0 - 500, t0])}
        # t0 itself is this mint's own create; the entry at t0-day_ms-1 is
        # just outside the trailing 24h window.
        self.assertEqual(count_prior_creates(hist, "creatorA", t0), 2)

    def test_unknown_or_missing_creator_is_zero(self) -> None:
        self.assertEqual(count_prior_creates({}, "nobody", 1_000_000), 0)
        self.assertEqual(count_prior_creates({"x": [1, 2]}, None, 1_000_000), 0)

    def test_window_matches_the_module_constant(self) -> None:
        self.assertEqual(CREATOR_LOOKBACK_MS, 24 * 3_600 * 1000)


class CohortAndSelectionTests(unittest.TestCase):
    def test_cohort_stats_empty_is_none_not_a_crash(self) -> None:
        stats = _cohort_stats([])
        self.assertEqual(stats["n"], 0)
        self.assertIsNone(stats["press_net_mean_pct"])

    def test_choose_best_setting_prefers_higher_top10_lift(self) -> None:
        from tools.exploration_exits import ENTRY_SIZE

        def fold(all_pct: float, top10_pct: float) -> dict:
            return {
                "trained": True,
                "cohorts": {
                    "all": {"n": 10, "press_net_mean_pct": all_pct},
                    "top10": {"n": 1, "press_net_mean_pct": top10_pct},
                },
            }

        lodo = {
            "weak": {"tpsl_tp50_sl30": {"2026-09-19": fold(-1.0, -0.5)}},
            "strong": {"tpsl_tp50_sl30": {"2026-09-19": fold(-1.0, 5.0)}},
        }
        best_id, lift = choose_best_setting(lodo)
        self.assertEqual(best_id, "strong")
        self.assertAlmostEqual(lift, 6.0)
        self.assertGreater(ENTRY_SIZE, 0)  # sanity: frozen size constant is importable


class ModuleShapeTests(unittest.TestCase):
    def test_two_frozen_exits_and_three_days(self) -> None:
        self.assertEqual(set(TARGET_SPEC_IDS), {"tpsl_tp50_sl30", "trail_30_act20"})
        self.assertEqual(DAYS, ("2026-09-19", "2026-09-20", "2026-09-21"))

    def test_at_most_three_settings_all_have_an_id_and_kind(self) -> None:
        self.assertLessEqual(len(SETTINGS), 3)
        for s in SETTINGS:
            self.assertIn("id", s)
            self.assertIn(s["kind"], ("lightgbm", "logreg"))


if __name__ == "__main__":
    unittest.main()
