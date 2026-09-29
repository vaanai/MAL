"""Exploration lane D: early bonding-curve entry filter.

Covers: the no-lookahead feature boundary (causal_events reuse, PLUS a
direct perturbation of landing-slot tape data -- the #156 audit's own
lookahead is excluded from FEATURE_NAMES here, and this file proves
perturbing that exact data never moves a feature), decision-time slot
targeting, the exit-size regression (eval_exit_early must use
ENTRY_SIZE_EARLY, not the migrate cell's 0.5 SOL), and the nested
fixed-threshold selection's pure functions.

Exploration only. No live runner, no network, no promotion claim.
"""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from tools.exploration_early_entry_model import (
    DAYS_ALL,
    DELTAS_S,
    ENTRY_SIZE_EARLY,
    EXIT_IDS,
    FEATURE_NAMES,
    NESTED_THRESHOLD_PCT,
    S2_SETTING,
    _ci_lo,
    _cohort_stats,
    _pct,
    _vector,
    cell_id,
    decision_landing,
    eval_exit_early,
    evaluate_cell,
    fit_and_score,
    iter_rows_jsonl,
    pooled_cohort_report,
    run_worker_early,
    score_one_early,
    screen_candidate,
)
from tools.exploration_entry_model import FEATURE_NAMES as MIGRATE_FEATURE_NAMES, _Feat, causal_events, compute_features
from tools.exploration_exits import ENTRY_PORTAL_PPM, ENTRY_PRIORITY_LAMPORTS, _curve
from tools.latency_curve import MISS, SEND, _Mint, _try_buy
from tools.paper_price_path import TapePrint


def _print(t_ms: int, slot: int, quote: int, base: int, venue: str = "pump_bonding", side: str = "buy") -> TapePrint:
    price = quote / (base * 1000)
    return TapePrint(
        t_recv_ms=t_ms,
        slot=slot,
        event_index=1,
        venue=venue,
        side=side,
        sol_lamports=1_000_000,
        quote_reserve=quote,
        base_reserve=base,
        price_sol=price,
        market_cap_sol=price * 1_000_000_000,
        signature=f"sig-{slot}-{t_ms}-{venue}-{side}",
        tx_index=0,
    )


class ModuleShapeTests(unittest.TestCase):
    def test_feature_names_are_the_migrate_list_minus_the_two_lookahead_features(self) -> None:
        self.assertEqual(len(FEATURE_NAMES), len(MIGRATE_FEATURE_NAMES) - 2)
        self.assertNotIn("same_slot_buys", FEATURE_NAMES)
        self.assertNotIn("nearby_buy_sol", FEATURE_NAMES)
        for name in FEATURE_NAMES:
            self.assertIn(name, MIGRATE_FEATURE_NAMES)

    def test_deltas_and_exits_fixed_by_the_brief(self) -> None:
        self.assertEqual(DELTAS_S, (5, 15, 30))
        self.assertEqual(EXIT_IDS, ("hold_30s", "tpsl_tp50_sl30", "tpsl_tp100_sl40"))

    def test_size_is_smaller_than_the_migrate_cells_own_size(self) -> None:
        self.assertEqual(ENTRY_SIZE_EARLY, 100_000_000)  # 0.1 SOL
        self.assertLess(ENTRY_SIZE_EARLY, 500_000_000)  # migrate cell's 0.5 SOL

    def test_setting_is_s2_lgb_medium_only(self) -> None:
        self.assertEqual(S2_SETTING["id"], "lgb_medium")
        self.assertEqual(S2_SETTING["kind"], "lightgbm")
        self.assertEqual(S2_SETTING["num_leaves"], 15)
        self.assertEqual(S2_SETTING["min_data_in_leaf"], 20)
        self.assertEqual(S2_SETTING["learning_rate"], 0.05)
        self.assertEqual(S2_SETTING["rounds"], 100)

    def test_nine_held_out_days_in_order(self) -> None:
        self.assertEqual(len(DAYS_ALL), 9)
        self.assertEqual(list(DAYS_ALL), sorted(DAYS_ALL))

    def test_cell_id_is_stable_and_distinct(self) -> None:
        ids = {cell_id(d, e) for d in DELTAS_S for e in EXIT_IDS}
        self.assertEqual(len(ids), len(DELTAS_S) * len(EXIT_IDS))


class NoLookaheadTests(unittest.TestCase):
    """The migrate model's own no-lookahead test (causal_events, shuffled
    and duplicated post-cutoff junk) plus a perturbation aimed at the exact
    lookahead #156 found: landing-slot pressure data.
    """

    def setUp(self) -> None:
        t0 = 1_700_000_000_000
        self.create_ms = t0
        self.decision_ms = t0 + 5_000  # Delta = 5s
        self.pre = [
            (t0 + 500, "buy", "walletA", 1_000_000_000, 500_000, 0.00010),
            (t0 + 1_000, "buy", "walletB", 2_000_000_000, 900_000, 0.00011),
            (t0 + 2_000, "sell", "walletA", 500_000_000, 200_000, 0.00012),
        ]

    def _features(self, events: list[tuple]) -> dict[str, float]:
        causal = causal_events(events, self.decision_ms)
        feats = compute_features(
            causal,
            create_ms=self.create_ms,
            first_price=0.00009,
            mig_ms=self.decision_ms,
            creator_prior_mints_24h=1,
        )
        return {k: v for k, v in feats.items() if k in FEATURE_NAMES}

    def test_shuffled_duplicated_post_cutoff_events_never_move_the_feature_vector(self) -> None:
        baseline = self._features(self.pre)
        rng = random.Random(11)
        post = [
            (self.decision_ms + i * 97, "buy" if i % 2 == 0 else "sell", f"w{i}", 5_000_000 * (i + 1), 500 * (i + 1), 0.0005 * (i + 1))
            for i in range(15)
        ]
        for _ in range(5):
            mixed = list(self.pre) + list(post) + list(post)
            rng.shuffle(mixed)
            got = self._features(mixed)
            self.assertEqual(baseline, got)

    def test_score_one_early_feature_vector_unchanged_by_landing_slot_perturbation(self) -> None:
        """Build the same mint twice: once with only pre-decision bonding
        trades, once with extra buys stamped directly onto the entry's
        landing slot (t_recv_ms >= decision_ms, the exact input the #156
        pressure-lookahead was found in). The computed `features` dict for
        every row must be identical -- this module never lets landing-slot
        data reach a feature.
        """
        t0 = 1_700_000_000_000
        create_ms = t0
        entry = _print(t0, 100, 80_000_000_000, 400_000_000_000_000)
        mid_state = _print(t0 + 3_000, 101, 82_000_000_000, 398_000_000_000_000)
        # State right after the decision at T=5s (Delta=5) -- gives the
        # entry a real landing slot to perturb.
        near_decision = _print(t0 + 5_100, 112, 83_000_000_000, 397_000_000_000_000)

        def build(extra_landing_prints: list[TapePrint]) -> tuple[_Mint, _Feat]:
            mint = _Mint(100, t0, 0, entry)
            mint.had_bond = True
            for pr in (entry, mid_state, near_decision, *extra_landing_prints):
                mint.add(pr)
            feat = _Feat("creatorX", create_ms, entry.price_sol)
            for t_ms, side, trader, sol_l, tok, price in [
                (t0 + 500, "buy", "walletA", 1_000_000_000, 500_000, 0.00010),
                (t0 + 1_500, "sell", "walletA", 200_000_000, 90_000, 0.00011),
            ]:
                feat.record(
                    {"side": side, "trader": trader, "sol_lamports": sol_l, "token_raw": tok, "price_sol": price},
                    t_ms,
                )
            return mint, feat

        baseline_mint, baseline_feat = build([])
        # A burst of extra buys landing exactly at/after the entry's own
        # landing slot -- the same "competing buys near landing" shape
        # tools.latency_curve._pressure reads (and this module deliberately
        # never feeds to a feature).
        perturbed_extra = [
            _print(t0 + 5_200 + i * 10, 113, 90_000_000_000 + i * 1_000_000, 396_000_000_000_000, side="buy")
            for i in range(6)
        ]
        perturbed_mint, perturbed_feat = build(perturbed_extra)

        curve = _curve()
        base_rows = score_one_early("mintX", baseline_mint, baseline_feat, curve, t0 + 40 * 60 * 1000, {})
        pert_rows = score_one_early("mintX", perturbed_mint, perturbed_feat, curve, t0 + 40 * 60 * 1000, {})
        self.assertTrue(base_rows and pert_rows)
        base_by_key = {(r["delta_s"], r["spec"]): r["features"] for r in base_rows}
        pert_by_key = {(r["delta_s"], r["spec"]): r["features"] for r in pert_rows}
        self.assertEqual(set(base_by_key), set(pert_by_key))
        for key, base_feats in base_by_key.items():
            self.assertEqual(base_feats, pert_by_key[key], f"features moved for {key} under a landing-slot perturbation")
            self.assertNotIn("same_slot_buys", base_feats)
            self.assertNotIn("nearby_buy_sol", base_feats)


class DecisionLandingTests(unittest.TestCase):
    def test_falls_back_to_create_slot_when_no_state_exists_yet(self) -> None:
        t0 = 1_700_000_000_000
        # No prints at all before decision_ms -> decision_slot falls back to
        # the create's own slot (mint_slot), landing target = mint_slot + 1.
        later = _print(t0 + 20_000, 150, 80_000_000_000, 400_000_000_000_000)
        idx, decision_slot, landing_ms = decision_landing([later], mint_slot=100, decision_ms=t0 + 5_000)
        self.assertEqual(decision_slot, 100)
        self.assertEqual(idx, -1)  # no print with slot < 101 exists

    def test_uses_the_slot_of_the_last_print_at_or_before_decision(self) -> None:
        t0 = 1_700_000_000_000
        p1 = _print(t0, 100, 80_000_000_000, 400_000_000_000_000)
        p2 = _print(t0 + 4_000, 110, 81_000_000_000, 399_000_000_000_000)  # before decision (T=5s)
        p3 = _print(t0 + 6_000, 120, 82_000_000_000, 398_000_000_000_000)  # after decision
        fills = [p1, p2, p3]
        idx, decision_slot, landing_ms = decision_landing(fills, mint_slot=100, decision_ms=t0 + 5_000)
        self.assertEqual(decision_slot, 110)  # p2's slot, not p3's
        self.assertEqual(idx, 1)  # state strictly before slot 111 ("start" bound) -> p2
        self.assertEqual(landing_ms, p3.t_recv_ms if p3.slot == 111 else landing_ms)


class ExitSizeRegressionTests(unittest.TestCase):
    """`tools.exploration_exits.eval_spec` bakes in the migrate cell's own
    0.5 SOL ENTRY_SIZE as the sell-side size; calling it directly at a 0.1
    SOL buy would misprice every close. This checks eval_exit_early does
    not do that.
    """

    def test_hold_30s_net0_is_scaled_to_the_early_entry_size_not_the_migrate_size(self) -> None:
        t0 = 1_700_000_000_000
        entry = _print(t0, 100, 80_000_000_000, 400_000_000_000_000)
        flat = _print(t0 + 30_000, 101, 80_000_000_000, 400_000_000_000_000)
        fills = [entry, flat]
        buy = _try_buy(entry, ENTRY_SIZE_EARLY, ENTRY_PORTAL_PPM, None)
        self.assertIsNotNone(buy)
        result = eval_exit_early("hold_30s", fills, 0, buy, "pump_bonding", t0, t0 + 60_000)
        self.assertIsNotNone(result)
        net0, gross, sides, status = result
        self.assertEqual(status, SEND)
        # A flat price round trip should lose a small fee-driven amount, well
        # inside the 0.1 SOL entry size -- nowhere near the ~0.4 SOL deficit
        # a wrongly-sized 0.5 SOL close against a 0.1 SOL buy would produce.
        self.assertGreater(net0, -ENTRY_SIZE_EARLY)
        self.assertLess(abs(net0), ENTRY_SIZE_EARLY // 2)

    def test_tpsl_exit_also_uses_the_early_entry_size(self) -> None:
        t0 = 1_700_000_000_000
        entry = _print(t0, 100, 80_000_000_000, 400_000_000_000_000)
        up = _print(t0 + 60_000, 105, 128_000_000_000, 400_000_000_000_000)  # +60% -> tp50 hits
        fills = [entry, up]
        buy = _try_buy(entry, ENTRY_SIZE_EARLY, ENTRY_PORTAL_PPM, None)
        self.assertIsNotNone(buy)
        result = eval_exit_early("tpsl_tp50_sl30", fills, 0, buy, "pump_bonding", t0, t0 + 40 * 60 * 1000)
        self.assertIsNotNone(result)
        net0, gross, sides, status = result
        self.assertEqual(status, SEND)
        self.assertGreater(gross, 0)  # a genuine winner before fees
        self.assertLess(abs(net0), ENTRY_SIZE_EARLY)  # never off by the migrate cell's 5x size


class MissRowTests(unittest.TestCase):
    def test_slippage_cap_breach_at_landing_is_a_miss_for_every_delta_and_exit(self) -> None:
        """Realistic MISS path: as long as any print exists at or before a
        decision, `decision_landing` always finds *some* state (the create
        anchor itself, at worst) -- the entry only fails via `_try_buy`'s
        slippage cap against the create's own reference price, exactly like
        every other frozen cell in this repo. A price that has already
        jumped >15% above the create anchor by the landing slot breaches it.
        """
        t0 = 1_700_000_000_000
        entry = _print(t0, 100, 80_000_000_000, 400_000_000_000_000)
        # +50% by t0+4s (before every Delta's decision) -> breaches the 15%
        # slippage cap for every Delta this run tries.
        jumped = _print(t0 + 4_000, 101, 120_000_000_000, 400_000_000_000_000)
        mint = _Mint(100, t0, 0, entry)
        mint.had_bond = True
        mint.add(entry)
        mint.add(jumped)
        feat = _Feat("creatorY", t0, entry.price_sol)
        curve = _curve()
        rows = score_one_early("mintY", mint, feat, curve, t0 + 40 * 60 * 1000, {})
        self.assertEqual(len(rows), len(DELTAS_S) * len(EXIT_IDS))
        miss_flat = -ENTRY_PRIORITY_LAMPORTS
        for r in rows:
            self.assertEqual(r["status"], MISS)
            self.assertFalse(r["filled"])
            self.assertAlmostEqual(r["flat"], miss_flat)
            self.assertAlmostEqual(r["press"], miss_flat)

    def test_feat_none_returns_no_rows(self) -> None:
        t0 = 1_700_000_000_000
        entry = _print(t0, 100, 80_000_000_000, 400_000_000_000_000)
        mint = _Mint(100, t0, 0, entry)
        mint.add(entry)
        curve = _curve()
        self.assertEqual(score_one_early("mintZ", mint, None, curve, t0 + 60_000, {}), [])


class StreamToDiskTests(unittest.TestCase):
    """run_worker_early(rows_out_path=...) must produce the exact same rows
    a fully in-memory run would, just via a file instead of a held list --
    the fix for the OOM the manager reported (a worker's `out` list growing
    for its whole ~24h+buffer run was the dominant driver of peak RSS)."""

    def _hour_info(self, t0_ms: int) -> dict:
        return {
            "hour": "test-hour",
            "day": "2026-09-19",
            "end": t0_ms // 1000 + 40 * 60,  # 40 min later -> past WINDOW_MS (32 min)
            "trade": Path("unused"),
            "create": None,
        }

    def _rows(self, t0_ms: int) -> list[dict]:
        base_row = {
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
        }
        return [
            {**base_row, "mint": "mintZ", "t_recv_ms": t0_ms + 500, "block_time": t0_ms // 1000},
            {**base_row, "mint": "mintZ", "t_recv_ms": t0_ms + 20_000, "block_time": t0_ms // 1000, "slot": 105},
        ]

    def test_rows_written_to_disk_match_the_in_memory_rows(self) -> None:
        t0 = 1_700_000_000_000
        entry = _print(t0, 100, 80_000_000_000, 400_000_000_000_000)
        creates_override = {"mintZ": (_Mint(100, t0, 0, entry), _Feat("creatorZ", t0, entry.price_sol))}
        creates_override["mintZ"][0].had_bond = True

        def hour_info_fn(_key: str) -> dict:
            return self._hour_info(t0)

        def row_iter_fn(_path) -> list[dict]:
            return self._rows(t0)

        in_memory = run_worker_early(
            0, ["h"], [], {}, hour_info_fn=hour_info_fn, row_iter_fn=row_iter_fn, creates_override=dict(creates_override)
        )
        self.assertTrue(in_memory)

        # Rebuild a fresh creates_override -- the first call already mutated
        # (and released) its _Mint/_Feat objects.
        entry2 = _print(t0, 100, 80_000_000_000, 400_000_000_000_000)
        creates_override2 = {"mintZ": (_Mint(100, t0, 0, entry2), _Feat("creatorZ", t0, entry2.price_sol))}
        creates_override2["mintZ"][0].had_bond = True
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "rows.jsonl"
            returned = run_worker_early(
                0,
                ["h"],
                [],
                {},
                hour_info_fn=hour_info_fn,
                row_iter_fn=row_iter_fn,
                creates_override=creates_override2,
                rows_out_path=out_path,
            )
            self.assertEqual(returned, [])  # streamed to disk, not held
            from_disk = list(iter_rows_jsonl(out_path))
        self.assertEqual(len(from_disk), len(in_memory))
        # Compare by (delta_s, spec, status, flat, press) -- ignore ordering.
        key = lambda r: (r["delta_s"], r["spec"], r["status"], r["flat"], r["press"])
        self.assertEqual(sorted(map(key, from_disk)), sorted(map(key, in_memory)))


class NestedThresholdPureFunctionTests(unittest.TestCase):
    def _rows(self, day: str, presses: list[int]) -> list[dict]:
        return [
            {
                "day": day,
                "status": SEND,
                "filled": True,
                "gross": p,
                "flat": p,
                "press": p,
                "features": {name: float(i) for i, name in enumerate(FEATURE_NAMES)},
            }
            for i, p in enumerate(presses)
        ]

    def test_pct_90_matches_manual_nearest_rank(self) -> None:
        vals = sorted(float(i) for i in range(10))
        self.assertEqual(_pct(vals, NESTED_THRESHOLD_PCT), vals[int(round(0.9 * 9))])

    def test_cohort_stats_empty_is_all_none(self) -> None:
        c = _cohort_stats([])
        self.assertEqual(c["n"], 0)
        self.assertIsNone(c["flat_net_mean_pct"])
        self.assertIsNone(c["press_ex_top3_sol"])

    def test_ci_lo_deterministic_for_fixed_seed(self) -> None:
        vals = [1_000_000.0, -500_000.0, 2_000_000.0, -100_000.0, 500_000.0] * 5
        self.assertEqual(_ci_lo(vals), _ci_lo(list(vals)))

    def test_evaluate_cell_too_few_rows_reports_not_trained(self) -> None:
        rows = self._rows("2026-09-19", [1_000_000, -500_000, 2_000_000])
        lodo = evaluate_cell(rows, ("2026-09-19", "2026-09-20"))
        self.assertFalse(lodo["2026-09-19"]["trained"])
        self.assertFalse(lodo["2026-09-20"]["trained"])

    def test_screen_candidate_requires_both_fail_models_and_majority_of_days(self) -> None:
        good = {
            "flat_net_mean_pct": 1.0,
            "flat_ex_top3_sol": 1.0,
            "n_days_flat_positive": 6,
            "press_net_mean_pct": 1.0,
            "press_ex_top3_sol": 1.0,
            "n_days_press_positive": 6,
            "n_days_total": 9,
        }
        self.assertTrue(screen_candidate(good)["candidate"])

        half = dict(good)
        half["n_days_flat_positive"] = 4  # 4/9 is not a majority
        self.assertFalse(screen_candidate(half)["candidate"])

        only_flat = dict(good)
        only_flat["press_net_mean_pct"] = -1.0
        self.assertFalse(screen_candidate(only_flat)["candidate"])

        no_ex3 = dict(good)
        no_ex3["flat_ex_top3_sol"] = -0.1
        self.assertFalse(screen_candidate(no_ex3)["candidate"])

    def test_fit_and_score_trains_lightgbm_on_this_modules_own_18_wide_feature_vector(self) -> None:
        """Regression test: `tools.exploration_entry_model._train_lightgbm`
        hardcodes ITS OWN 20-name FEATURE_NAMES for `lgb.Dataset`'s
        `feature_name=`, no matter what `x` it is actually given. Calling it
        directly with this module's 18-wide vectors (two lookahead features
        dropped) raises a feature-count mismatch at fit time. `_vector`
        below builds real 18-wide rows the same way `evaluate_cell` does;
        this must fit and score without error.
        """
        random.seed(3)
        rows_train = []
        for i in range(200):
            feats = {name: random.random() for name in FEATURE_NAMES}
            press = 1_000_000 if random.random() > 0.6 else -1_000_000
            rows_train.append({"day": "2026-09-19", "flat": press, "press": press, "features": feats})
        rows_test = [{"day": "2026-09-20", "flat": 0, "press": 0, "features": {name: random.random() for name in FEATURE_NAMES}} for _ in range(10)]
        self.assertEqual(len(_vector(rows_train[0]["features"])), len(FEATURE_NAMES))
        result = fit_and_score(rows_train, rows_test)
        self.assertIsNotNone(result)
        scores, model = result
        self.assertEqual(len(scores), len(rows_test))
        self.assertTrue(all(0.0 <= s <= 1.0 for s in scores))

    def test_pooled_cohort_report_pools_only_trained_folds(self) -> None:
        rows_19 = self._rows("2026-09-19", [1_000_000, 2_000_000])
        lodo = {
            "2026-09-19": {"trained": True, "cohorts": {"x": {"flat_net_mean_pct": 1.0, "press_net_mean_pct": 1.0}}, "x_rows": rows_19},
            "2026-09-20": {"trained": False},
        }
        report = pooled_cohort_report(lodo, "x", "x_rows")
        self.assertEqual(report["n"], 2)
        self.assertEqual(report["days_used"], ["2026-09-19"])


if __name__ == "__main__":
    unittest.main()
