"""EXP-011 freeze: no-lookahead proof for the frozen feature set, plus the
pure threshold/vector/holdout-fence helpers.

Exploration/freeze only. No live runner, no network, no promotion claim.
"""

from __future__ import annotations

import random
import unittest

from tools.exploration_entry_model import (
    FEATURE_NAMES,
    _Feat,
    causal_events,
    compute_features,
    score_one,
)
from tools.exploration_exits import _curve
from tools.latency_curve import _Mint
from tools.paper_price_path import TapePrint

from tools.exp011_freeze import (
    FROZEN_FEATURE_NAMES,
    HOLDOUT_END,
    HOLDOUT_START,
    _DROPPED_LOOKAHEAD_FEATURES,
    _assert_never_holdout,
    _ci_lo_pct,
    _label,
    _mean_pct,
    _percentile,
    _vector,
    compute_threshold,
    nested_fixed_threshold_lodo,
    nested_lodo_report,
    leave_one_day_out_oof,
)


def _event(t_ms: int, side: str, trader: str, sol_lamports: int, token_raw: int, price: float | None) -> tuple:
    return (t_ms, side, trader, sol_lamports, token_raw, price)


def _pr(t_ms: int, slot: int, sol_lamports: int, signature: str, venue: str = "pumpswap", side: str = "buy") -> TapePrint:
    quote = 80_000_000_000
    base = 400_000_000_000_000
    price = quote / (base * 1000)
    return TapePrint(
        t_recv_ms=t_ms,
        slot=slot,
        event_index=1,
        venue=venue,
        side=side,
        sol_lamports=sol_lamports,
        quote_reserve=quote,
        base_reserve=base,
        price_sol=price,
        market_cap_sol=price * 1_000_000_000,
        signature=signature,
        tx_index=0,
    )


class FrozenFeatureSetTests(unittest.TestCase):
    def test_frozen_set_excludes_the_two_lookahead_features(self) -> None:
        self.assertNotIn("same_slot_buys", FROZEN_FEATURE_NAMES)
        self.assertNotIn("nearby_buy_sol", FROZEN_FEATURE_NAMES)
        self.assertEqual(set(_DROPPED_LOOKAHEAD_FEATURES), {"same_slot_buys", "nearby_buy_sol"})

    def test_frozen_set_is_exactly_feature_names_minus_dropped(self) -> None:
        expected = [f for f in FEATURE_NAMES if f not in ("same_slot_buys", "nearby_buy_sol")]
        self.assertEqual(FROZEN_FEATURE_NAMES, expected)
        self.assertEqual(len(FROZEN_FEATURE_NAMES), 18)

    def test_frozen_set_matches_compute_features_output_exactly(self) -> None:
        feats = compute_features([], create_ms=0, first_price=None, mig_ms=1, creator_prior_mints_24h=0)
        self.assertEqual(set(FROZEN_FEATURE_NAMES), set(feats.keys()))


class EveryFrozenFeatureNoLookaheadTests(unittest.TestCase):
    """Every one of the 18 frozen features, individually, must be unmoved by
    events at or after the migrate cutoff. This mirrors tools/
    test_exploration_entry_model.py's whole-dict check but asserts
    feature-by-feature so a future refactor that quietly reintroduces
    lookahead into just one feature cannot slip past a dict-equality check
    that happens to still pass on the others."""

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

    def test_every_frozen_feature_unchanged_by_post_cutoff_events(self) -> None:
        baseline = self._features(self.pre)
        rng = random.Random(11)
        post = [
            _event(self.mig_ms + i * 91, "buy" if i % 2 == 0 else "sell", f"post{i}", 10_000_000 * (i + 1), 1_000 * (i + 1), 0.002 * (i + 1))
            for i in range(15)
        ]
        mixed = list(self.pre) + list(post)
        rng.shuffle(mixed)
        with_post = self._features(mixed)
        self.assertEqual(set(FROZEN_FEATURE_NAMES), set(baseline.keys()))
        for name in FROZEN_FEATURE_NAMES:
            with self.subTest(feature=name):
                self.assertEqual(baseline[name], with_post[name], f"{name} moved when future events were added")


class LandingSlotPerturbationTests(unittest.TestCase):
    """The two dropped features (same_slot_buys, nearby_buy_sol) come from
    the slot+1 landing state (`tools.latency_curve._pressure`, called from
    score_one AFTER `feats = compute_features(...)` has already been
    built) -- not from compute_features() itself. This proves it
    end-to-end through score_one: two mints share IDENTICAL pre-cutoff
    feat.events (the only input to every frozen feature) but DIFFERENT
    landing-slot fills (more/bigger competing buys at the trigger slot,
    the state `_pressure` reads). The frozen feature subset of the
    resulting row must be byte-identical while the two dropped features
    must actually move -- proving the test is not vacuous.
    """

    def _feat(self) -> _Feat:
        t0 = 1_700_000_000_000
        feat = _Feat(creator="creatorX", create_ms=t0 - 100_000, first_price=0.0001)
        feat.events = [
            _event(t0 - 80_000, "buy", "walletA", 1_000_000_000, 500_000, 0.00010),
            _event(t0 - 50_000, "buy", "walletB", 2_000_000_000, 900_000, 0.00011),
            _event(t0 - 10_000, "sell", "walletA", 500_000_000, 200_000, 0.00012),
        ]
        return feat

    def _mint(self, t0: int, mig_slot: int, extra_prints: list[TapePrint]) -> _Mint:
        entry = _pr(t0, mig_slot, 1_000_000, signature="sig-entry")
        mint = _Mint(mig_slot, t0, 0, None)
        mint.had_bond = True
        mint.add(entry)
        for pr in extra_prints:
            mint.add(pr)
        mint.mig_slot = mig_slot
        mint.mig_ms = t0
        return mint

    def test_frozen_features_identical_dropped_features_differ(self) -> None:
        t0 = 1_700_000_000_000
        mig_slot = 100
        feat = self._feat()

        # Both variants' extra prints land in the same slot as the trigger
        # (< target = mig_slot + ENTRY_LAND_K = 101, so _state_index's
        # "start" bound includes them) and within _pressure's nearby
        # window ([landing_ms - 2s, landing_ms], landing_ms falling back to
        # mig_ms = t0 here since neither variant has a print in slot 101
        # itself) -- i.e. exactly the landing-adjacent competing-buy data
        # same_slot_buys/nearby_buy_sol summarize.
        mint_low = self._mint(t0, mig_slot, extra_prints=[])
        mint_high = self._mint(
            t0,
            mig_slot,
            extra_prints=[
                _pr(t0 - 150, mig_slot, 50_000_000_000, signature="sig-extra1"),
                _pr(t0 - 100, mig_slot, 75_000_000_000, signature="sig-extra2"),
            ],
        )

        curve = _curve()
        rows_low = score_one("mintLOW", mint_low, feat, curve, t0 + 3_600_000, creator_hist={})
        rows_high = score_one("mintHIGH", mint_high, feat, curve, t0 + 3_600_000, creator_hist={})
        self.assertTrue(rows_low, "expected at least one scored row for the low-pressure mint")
        self.assertTrue(rows_high, "expected at least one scored row for the high-pressure mint")

        feats_low = rows_low[0]["features"]
        feats_high = rows_high[0]["features"]

        for name in FROZEN_FEATURE_NAMES:
            with self.subTest(feature=name):
                self.assertEqual(feats_low[name], feats_high[name], f"frozen feature {name} moved with landing-slot data")

        # Not vacuous: the two dropped, landing-derived features must
        # actually have moved between the two mints.
        self.assertNotEqual(feats_low["same_slot_buys"], feats_high["same_slot_buys"])
        self.assertNotEqual(feats_low["nearby_buy_sol"], feats_high["nearby_buy_sol"])
        self.assertGreater(feats_high["same_slot_buys"], feats_low["same_slot_buys"])
        self.assertGreater(feats_high["nearby_buy_sol"], feats_low["nearby_buy_sol"])


class HoldoutFenceTests(unittest.TestCase):
    def test_holdout_bounds_match_the_reserved_ledger_block(self) -> None:
        self.assertEqual(HOLDOUT_START, "2026-09-09T12")
        self.assertEqual(HOLDOUT_END, "2026-09-15T12")

    def test_assert_never_holdout_passes_outside_hours(self) -> None:
        _assert_never_holdout(["2026-09-19T01", "2026-09-27T23"], "test")  # no raise

    def test_assert_never_holdout_rejects_an_hour_inside_the_block(self) -> None:
        with self.assertRaises(AssertionError):
            _assert_never_holdout(["2026-09-12T00"], "test")

    def test_assert_never_holdout_rejects_the_inclusive_start(self) -> None:
        with self.assertRaises(AssertionError):
            _assert_never_holdout(["2026-09-09T12"], "test")

    def test_assert_never_holdout_allows_the_exclusive_end(self) -> None:
        _assert_never_holdout(["2026-09-15T12"], "test")  # no raise: end is exclusive


class VectorAndThresholdTests(unittest.TestCase):
    def test_vector_orders_by_frozen_feature_names_and_defaults_missing_to_zero(self) -> None:
        feats = {name: float(i) for i, name in enumerate(FROZEN_FEATURE_NAMES)}
        del feats[FROZEN_FEATURE_NAMES[0]]
        vec = _vector(feats)
        self.assertEqual(len(vec), len(FROZEN_FEATURE_NAMES))
        self.assertEqual(vec[0], 0.0)
        self.assertEqual(vec[1], 1.0)

    def test_vector_never_reads_a_dropped_feature(self) -> None:
        feats = {name: 0.0 for name in FROZEN_FEATURE_NAMES}
        feats["same_slot_buys"] = 999.0
        feats["nearby_buy_sol"] = 999.0
        vec = _vector(feats)
        self.assertNotIn(999.0, vec)

    def test_label_is_press_positive(self) -> None:
        rows = [{"press": 5}, {"press": -5}, {"press": 0}]
        self.assertEqual(_label(rows), [1, 0, 0])

    def test_percentile_empty_is_zero(self) -> None:
        self.assertEqual(_percentile([], 0.9), 0.0)

    def test_percentile_ninetieth_on_a_known_array(self) -> None:
        vals = [float(i) for i in range(10)]  # 0..9, n=10
        # index = round(0.9 * 9) = round(8.1) = 8
        self.assertEqual(_percentile(vals, 0.9), 8.0)

    def test_compute_threshold_selects_about_ten_percent(self) -> None:
        oof = [{"score": float(i), "label": 1, "mint": f"m{i}", "day": "2026-09-19", "filled": True} for i in range(100)]
        info = compute_threshold(oof)
        self.assertEqual(info["n_oof"], 100)
        self.assertAlmostEqual(info["selected_fraction"], 0.10, delta=0.02)
        self.assertGreater(info["n_selected_at_or_above_threshold"], 0)


class SyntheticLodoOofTests(unittest.TestCase):
    """Small synthetic dataset, real lightgbm, no I/O: proves the LODO/OOF
    plumbing actually fits and scores, and that a feature informative of
    the label produces OOF scores whose top decile beats its bottom decile
    -- a smoke test, not a statistical claim."""

    def _rows(self, day: str, n: int, seed: int) -> list[dict]:
        rng = random.Random(seed)
        out = []
        for i in range(n):
            x = rng.uniform(-1, 1)
            noise = rng.uniform(-0.2, 0.2)
            press_pct = x * 10 + noise
            feats = {name: 0.0 for name in FROZEN_FEATURE_NAMES}
            feats[FROZEN_FEATURE_NAMES[0]] = x
            out.append(
                {
                    "mint": f"m{day}-{i}",
                    "spec": "tpsl_tp50_sl30",
                    "day": day,
                    "filled": True,
                    "flat": press_pct * 5_000_000 / 100.0,
                    "press": press_pct * 5_000_000 / 100.0,
                    "features": feats,
                }
            )
        return out

    def test_lodo_oof_covers_every_test_row_once(self) -> None:
        days = ("2026-09-19", "2026-09-20", "2026-09-21")
        rows = []
        for i, d in enumerate(days):
            rows.extend(self._rows(d, 40, seed=i))
        oof = leave_one_day_out_oof(rows, days=days)
        self.assertEqual(len(oof), len(rows))
        self.assertEqual({r["mint"] for r in oof}, {r["mint"] for r in rows})

    def test_threshold_top_decile_beats_bottom_decile_on_informative_synthetic_data(self) -> None:
        days = ("2026-09-19", "2026-09-20", "2026-09-21")
        rows = []
        for i, d in enumerate(days):
            rows.extend(self._rows(d, 80, seed=i + 100))
        oof = leave_one_day_out_oof(rows, days=days)
        by_mint = {r["mint"]: r for r in rows}
        ranked = sorted(oof, key=lambda r: r["score"], reverse=True)
        n = len(ranked)
        top = ranked[: n // 10]
        bottom = ranked[-(n // 10) :]
        top_mean = sum(by_mint[r["mint"]]["press"] for r in top) / len(top)
        bottom_mean = sum(by_mint[r["mint"]]["press"] for r in bottom) / len(bottom)
        self.assertGreater(top_mean, bottom_mean)

    def test_compute_threshold_states_its_role_is_the_frozen_holdout_threshold(self) -> None:
        oof = [{"score": float(i), "label": 1, "mint": f"m{i}", "day": "2026-09-19", "filled": True} for i in range(50)]
        info = compute_threshold(oof)
        self.assertIn("role", info)
        self.assertIn("frozen holdout threshold", info["role"])


class NestedFixedThresholdLodoTests(unittest.TestCase):
    """Owner scope addition (2026-09-29): a fixed-threshold nested LODO,
    REPORT ONLY -- never gating, never fed back into the frozen threshold/
    features/params. Small synthetic dataset, real lightgbm, no I/O."""

    DAYS = ("2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22")

    def _rows(self, day: str, n: int, seed: int, pool: str = "A") -> list[dict]:
        rng = random.Random(seed)
        out = []
        for i in range(n):
            x = rng.uniform(-1, 1)
            noise = rng.uniform(-0.2, 0.2)
            press_pct = x * 10 + noise
            feats = {name: 0.0 for name in FROZEN_FEATURE_NAMES}
            feats[FROZEN_FEATURE_NAMES[0]] = x
            out.append(
                {
                    "mint": f"m{day}-{i}",
                    "spec": "tpsl_tp50_sl30",
                    "day": day,
                    "filled": i % 3 != 0,
                    "flat": press_pct * 5_000_000 / 100.0,
                    "press": press_pct * 5_000_000 / 100.0,
                    "pool": pool,
                    "features": feats,
                }
            )
        return out

    def _all_rows(self) -> list[dict]:
        rows = []
        pools = ("A", "A", "C", "B")
        for i, (d, p) in enumerate(zip(self.DAYS, pools)):
            rows.extend(self._rows(d, 60, seed=i, pool=p))
        return rows

    def test_every_outer_day_gets_a_fold_and_entries_all_meet_their_own_threshold(self) -> None:
        rows = self._all_rows()
        entries, fold_info = nested_fixed_threshold_lodo(rows, days=self.DAYS)
        self.assertEqual(len(fold_info), len(self.DAYS))
        self.assertEqual({f["outer_day"] for f in fold_info}, set(self.DAYS))
        for e in entries:
            self.assertGreaterEqual(e["score"], e["threshold"])

    def test_entries_never_leak_the_outer_days_own_rows_into_its_threshold(self) -> None:
        # Each fold's threshold comes from an 8(here 3)-day inner LODO over
        # the days that exclude the outer day -- i.e. the inner OOF count
        # feeding the threshold must never exceed the inner pool's own size.
        rows = self._all_rows()
        _entries, fold_info = nested_fixed_threshold_lodo(rows, days=self.DAYS)
        n_per_day = {d: sum(1 for r in rows if r["day"] == d) for d in self.DAYS}
        for f in fold_info:
            if not f["trained"]:
                continue
            inner_days = [d for d in self.DAYS if d != f["outer_day"]]
            self.assertEqual(f["n_inner_oof"], sum(n_per_day[d] for d in inner_days))

    def test_report_is_marked_report_only_and_covers_both_fail_models(self) -> None:
        rows = self._all_rows()
        entries, fold_info = nested_fixed_threshold_lodo(rows, days=self.DAYS)
        report = nested_lodo_report(entries, fold_info, days=self.DAYS)
        self.assertIn("REPORT ONLY", report["note"])
        self.assertIn("flat", report)
        self.assertIn("press", report)
        self.assertEqual(len(report["per_day"]), len(self.DAYS))
        self.assertIn("fill_conditional", report)
        self.assertIn("source_split", report)
        self.assertEqual(set(report["source_split"].keys()), {"A", "C", "B"})

    def test_fill_conditional_splits_filled_from_all_entered(self) -> None:
        rows = self._all_rows()
        entries, fold_info = nested_fixed_threshold_lodo(rows, days=self.DAYS)
        report = nested_lodo_report(entries, fold_info, days=self.DAYS)
        self.assertLessEqual(report["fill_conditional"]["filled_only"]["n"], report["fill_conditional"]["all_entered"]["n"])

    def test_mean_pct_and_ci_lo_pct_pure_helpers(self) -> None:
        self.assertIsNone(_mean_pct([]))
        self.assertIsNone(_ci_lo_pct([]))
        from tools.exploration_exits import ENTRY_SIZE

        vals = [ENTRY_SIZE * 0.1] * 20  # +10% every trade
        self.assertAlmostEqual(_mean_pct(vals), 10.0)
        self.assertAlmostEqual(_ci_lo_pct(vals), 10.0)  # zero variance -> CI collapses to the mean


if __name__ == "__main__":
    unittest.main()
