"""Tests for tools.exp012_rug_risk (exploration only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import tools.exp012_operating_point as op
import tools.exp012_rug_risk as rr
from tools.exp011_freeze import TARGET_SPEC_ID

SOL = 1_000_000_000
TOK = 1_000_000_000_000  # 0.1% of supply
DAYS = [f"2026-09-{d}" for d in range(19, 28)]
MIG_MS = 10_000


def E(t, side, trader, sol=1, tok=TOK):
    return (t, side, trader, sol * SOL, tok, 1e-7)


def P(t_ms, price, side="sell", venue="pumpswap"):
    return SimpleNamespace(t_recv_ms=t_ms, price_sol=price, side=side, venue=venue)


class FeatureTests(unittest.TestCase):
    def base_events(self):
        return [E(1, "buy", "C", tok=50 * TOK), E(2, "buy", "A", tok=30 * TOK, sol=3), E(3, "buy", "B", tok=10 * TOK), E(4, "buy", "D", tok=5 * TOK), E(5, "buy", "F", tok=2 * TOK), E(6, "sell", "A", tok=10 * TOK)]

    def test_values(self):
        f = rr.rug_features(self.base_events(), "C", MIG_MS, 3)
        self.assertAlmostEqual(f["creator_share"], 0.05)
        self.assertAlmostEqual(f["top1_share"], 0.02)  # A net 20 TOK
        self.assertAlmostEqual(f["top3_share"], (20 + 10 + 5) * TOK / rr.SUPPLY_RAW)
        self.assertEqual(f["n_buyers"], 5)
        self.assertAlmostEqual(f["top_wallet_vol_share"], 3 / 7)
        self.assertEqual(f["creator_prior_mints_24h"], 3)

    def test_post_migration_events_do_not_change_features(self):
        base = rr.rug_features(self.base_events(), "C", MIG_MS)
        later = self.base_events() + [E(MIG_MS, "sell", "C", tok=50 * TOK), E(MIG_MS + 5, "buy", "Z", tok=900 * TOK, sol=50), E(MIG_MS + 999, "sell", "A", tok=20 * TOK)]
        self.assertEqual(rr.rug_features(later, "C", MIG_MS), base)
        self.assertEqual(rr.rug_features(list(reversed(later)), "C", MIG_MS), base)

    def test_post_migration_creator_sell_cannot_reach_features(self):
        f = rr.rug_features(self.base_events() + [E(MIG_MS + 1, "sell", "C", tok=50 * TOK)], "C", MIG_MS)
        self.assertAlmostEqual(f["creator_share"], 0.05)

    def test_empty_and_no_creator(self):
        f = rr.rug_features([], None, MIG_MS)
        self.assertEqual((f["creator_share"], f["top1_share"], f["n_buyers"], f["top_wallet_vol_share"]), (0.0, 0.0, 0.0, 0.0))
        self.assertFalse(rr.vetoed("n_buyers_lt_20", None))

    def test_declared_sizes(self):
        self.assertEqual(len(rr.FEATURES), 6)
        self.assertLessEqual(len(rr.RULE_IDS), 6)
        self.assertEqual(rr.EXISTING_TRIES_ON_POOL, 68)


class LabelTests(unittest.TestCase):
    def test_one_step_sell(self):
        lab = rr.rug_label([P(0, 1.0, "buy"), P(1000, 0.95), P(2000, 0.60)], 0, 0)
        self.assertTrue(lab["one_step"] and lab["rug"])
        self.assertAlmostEqual(lab["worst_sell_step"], 0.60 / 0.95 - 1)

    def test_gradual_decline_is_not_a_rug(self):
        self.assertFalse(rr.rug_label([P(0, 1.0, "buy"), P(1, 0.9), P(2, 0.8), P(3, 0.7), P(4, 0.6)], 0, 0)["rug"])

    def test_crash50_unless_tp_first(self):
        crash = rr.rug_label([P(0, 1.0), P(1, 0.8), P(2, 0.65), P(3, 0.49)], 0, 0)
        self.assertTrue(crash["crash50"] and not crash["one_step"])
        tp_first = rr.rug_label([P(0, 1.0), P(1, 1.6, "buy"), P(2, 1.2), P(3, 0.8), P(4, 0.45)], 0, 0)
        self.assertFalse(tp_first["crash50"])

    def test_window_and_venue(self):
        lab = rr.rug_label([P(0, 1.0), P(rr.WINDOW_MS + 1, 0.1), P(5, 0.1, venue="bonding")], 0, 0)
        self.assertFalse(lab["rug"])
        self.assertIsNone(rr.rug_label([], -1, 0))


def _row(mint, day, net0, feats, filled=True, rug=None, score=0.9):
    return {"mint": mint, "spec": TARGET_SPEC_ID, "day": day, "score": score, "k": 6, "size": op.size_lamports(0.05), "censored": False, "pool": "A",
            "filled": filled, "status": 1 if filled else 0, "gross": 0, "net0": net0, "sides": 2, "p_press": 0.3, "rugf": feats, "rug": rug}


def F(**kw):
    base = {"creator_share": 0.0, "top1_share": 0.0, "top3_share": 0.0, "n_buyers": 50.0, "top_wallet_vol_share": 0.1, "creator_prior_mints_24h": 0.0}
    base.update(kw)
    return base


def _fixture(n=12):
    """Per day: concentrated (creator_share high) mints lose, others win."""
    rows = []
    for i, d in enumerate(DAYS):
        for j in range(n):
            bad = j % 3 == 0
            rows.append(_row(f"m{i}_{j}", d, int((-0.01 if bad else 0.01) * SOL), F(creator_share=0.2 if bad else 0.0), rug={"rug": bad, "one_step": bad}))
    return rows


class AnalysisTests(unittest.TestCase):
    def test_rule_scores_filled_vetoes_only(self):
        rows = _fixture()
        scores = {r["mint"]: r["score"] for r in rows}
        rep = rr.analyze(rows, scores, None, {r["mint"]: r["day"] for r in rows}, min_train_trades=1)
        by = {r["rule"]: r for r in rep["rules"]}
        c = by["creator_share_ge_5pct"]
        self.assertEqual((c["n_vetoed_filled"], c["n_vetoed_rug_of_filled"], c["n_misses"]), (36, 36, 0))
        self.assertGreater(c["paired_vs_frozen"]["press"]["mean_sol"], 0)
        self.assertEqual(by["top1_share_ge_10pct"]["paired_vs_frozen"]["press"]["mean_sol"], 0)
        self.assertEqual((rep["n_rules_tried"], rep["cumulative_tries_on_pool"]), (6, 74))
        self.assertIn("NOT A PROMOTE", rep["status"])

    def test_skipped_miss_scores_the_same_in_both_arms(self):
        rows = _fixture(6)
        with_miss = list(rows)
        for i, d in enumerate(DAYS):
            with_miss.append(_row(f"miss{i}", d, 0, F(creator_share=0.9), filled=False))
        trades, feats, labs, _ = rr.frozen_trades(with_miss)
        d = rr.paired_filled(trades, feats, "creator_share_ge_5pct")
        self.assertFalse(any(r["mint"].startswith("miss") for r in d))  # misses are never paired rows
        before = rr.rule_stats(trades, feats, labs, "creator_share_ge_5pct")
        self.assertEqual((before["n_misses"], before["n_misses_vetoed"]), (9, 9))
        trades2, feats2, labs2, _ = rr.frozen_trades(rows)
        after = rr.rule_stats(trades2, feats2, labs2, "creator_share_ge_5pct")
        # identical with or without the misses: a vetoed miss is never a gain
        self.assertEqual(before["paired_vs_frozen"], after["paired_vs_frozen"])
        self.assertEqual(before["ex_top3_paired_press_sol"], after["ex_top3_paired_press_sol"])
        self.assertEqual(rr.nested_lodo(trades, feats, rr.RULE_IDS, DAYS, 10)["heldout_paired_vs_frozen"], rr.nested_lodo(trades2, feats2, rr.RULE_IDS, DAYS, 10)["heldout_paired_vs_frozen"])

    def test_vetoing_every_filled_trade_is_charged(self):
        rows = [_row(f"a{i}", DAYS[i % 9], int(0.01 * SOL), F(creator_share=0.5)) for i in range(18)]
        trades, feats, labs, _ = rr.frozen_trades(rows)
        self.assertLess(rr.rule_stats(trades, feats, labs, "creator_share_ge_5pct")["paired_vs_frozen"]["press"]["mean_sol"], 0)

    def test_nested_lodo_picks_separating_rule_and_includes_frozen(self):
        trades, feats, _l, _ = rr.frozen_trades(_fixture())
        n = rr.nested_lodo(trades, feats, rr.RULE_IDS, DAYS, min_train_trades=20)
        self.assertEqual(n["candidates"][0], rr.FROZEN_ID)
        self.assertEqual(n["times_chosen"]["creator_share_ge_5pct"], 9)
        self.assertGreater(n["heldout_paired_vs_frozen"]["press"]["mean_sol"], 0)

    def test_nested_lodo_selection_uses_training_days_only(self):
        rows = []
        for i, d in enumerate(DAYS):
            for j in range(12):
                bad = i == 0 and j % 2 == 0
                rows.append(_row(f"m{i}_{j}", d, int((-0.05 if bad else 0.01) * SOL), F(creator_share=0.2 if bad else 0.0)))
        trades, feats, _l, _ = rr.frozen_trades(rows)
        n = rr.nested_lodo(trades, feats, ("creator_share_ge_5pct",), DAYS, min_train_trades=20)
        self.assertEqual([f for f in n["folds"] if f["day"] == DAYS[0]][0]["chosen"], rr.FROZEN_ID)
        self.assertEqual(n["times_chosen"]["creator_share_ge_5pct"], 8)

    def test_training_floor_leaves_folds_unavailable(self):
        trades, feats, _l, _ = rr.frozen_trades(_fixture(2))
        n = rr.nested_lodo(trades, feats, rr.RULE_IDS, DAYS, min_train_trades=100)
        self.assertFalse(n["available"])
        self.assertEqual(n["n_unavailable_folds"], 9)

    def test_integrity_refuses_foreign_scores(self):
        rows = _fixture(2)
        with self.assertRaises(SystemExit):
            rr.analyze(rows, {r["mint"]: 0.1 for r in rows}, None, None)

    def test_quantile_bins_keep_ties_together(self):
        b = rr.quantile_bins([0.0] * 8 + [1.0, 2.0])
        self.assertEqual(len(set(b[:8])), 1)
        self.assertGreater(b[9], b[0])

    def test_render_has_banner_rules_and_features(self):
        rows = _fixture()
        scores = {r["mint"]: r["score"] for r in rows}
        rep = rr.analyze(rows, scores, None, {r["mint"]: r["day"] for r in rows}, min_train_trades=20)
        md = rr.render_md(rep)
        self.assertIn("BEST-OF-N", md)
        self.assertIn("cumulative tries", md)
        for r in rr.RULE_IDS:
            self.assertIn(f"| {r} |", md)
        for f in rr.FEATURES:
            self.assertIn(f"### {f}", md)


class GuardTests(unittest.TestCase):
    def test_shared_guard_and_forbidden_roots(self):
        from tools.exp012_latency_sensitivity import guarded_roots

        self.assertIs(rr.guarded_roots, guarded_roots)
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(SystemExit, "reserved holdout"):
            rr.main(["--out-dir", td, "--verify-view", "--fast-dir", "/data/mal/blocks-clean/x", "--oracle-insample-dir", td, "--oracle-live-dir", td, "--tries-log", str(Path(td) / "t.jsonl")])

    def test_more_than_two_workers_refused(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(SystemExit, "max-workers"):
            rr.main(["--out-dir", td, "--max-workers", "3", "--tries-log", str(Path(td) / "t.jsonl")])

    def test_roots_required(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(SystemExit):
            rr.main(["--out-dir", td, "--tries-log", str(Path(td) / "t.jsonl")])


class TriesTests(unittest.TestCase):
    def test_every_rule_logged_once_and_idempotent(self):
        rows = _fixture()
        scores = {r["mint"]: r["score"] for r in rows}
        rep = rr.analyze(rows, scores, None, {r["mint"]: r["day"] for r in rows}, min_train_trades=20)
        with tempfile.TemporaryDirectory() as td:
            out, log = Path(td), Path(td) / "tries.jsonl"
            self.assertEqual(rr.log_tries(rep, out, log), 6)
            lines = [json.loads(x) for x in log.read_text().splitlines()]
            self.assertTrue(all(x["tool"] == rr.TOOL and x["role"] == "exploration" for x in lines))
            self.assertEqual({x["config"]["rule"] for x in lines}, set(rr.RULE_IDS))
            self.assertEqual(rr.log_tries(rep, out, log), 0)
            (out / rr.TRIES_MARKER).unlink()
            self.assertEqual(rr.log_tries(rep, out, log), 0)
            self.assertEqual(len(log.read_text().splitlines()), 6)


class PatchTests(unittest.TestCase):
    def test_patch_attaches_features_label_and_restores(self):
        import tools.exploration_entry_model as eem

        fills = [SimpleNamespace(t_recv_ms=0, slot=1000, price_sol=1.0, side="buy", venue="pumpswap"),
                 SimpleNamespace(t_recv_ms=500, slot=1010, price_sol=0.5, side="sell", venue="pumpswap")]
        mint = SimpleNamespace(mig_ms=MIG_MS)
        feat = SimpleNamespace(creator="C", create_ms=0, events=[E(1, "buy", "C", tok=50 * TOK), E(MIG_MS + 1, "sell", "C", tok=50 * TOK)])

        def fake_fills_for(m, migrate=True):
            return fills, 1000, 0, 1.0

        def fake_score(mint_id, m, f, curve, through, hist, *, specs, size, priority, entry_land_k):
            eem._fills_for(m, migrate=True)
            flat = eem.mixed_net(5, 2, 1, priority, 0.15)
            press = eem.mixed_net(5, 2, 1, priority, 0.25)
            return [{"spec": specs[0]["id"], "day": "2026-09-20", "filled": True, "gross": 0, "flat": flat, "press": press}]

        saved = (eem.score_one, eem._fills_for, eem.mixed_net, eem._state_index, eem._slot_time, eem.count_prior_creates)
        try:
            eem.score_one, eem._fills_for = fake_score, fake_fills_for
            eem._state_index = lambda f, target, bound: 0
            eem._slot_time = lambda f, target, fb: 0
            eem.count_prior_creates = lambda h, c, t: 2
            before = (eem.score_one, eem._fills_for, eem.mixed_net)
            with rr.rug_patch({"m": 0.9, "low": 0.1}, 0.8):
                self.assertEqual(eem.score_one("low", mint, feat, None, 0, {}), [])
                rows = eem.score_one("m", mint, feat, None, 0, {})
            r = rows[0]
            self.assertAlmostEqual(r["rugf"]["creator_share"], 0.05)  # the post-migration creator sell is not read
            self.assertEqual(r["rugf"]["creator_prior_mints_24h"], 2.0)
            self.assertTrue(r["rug"]["one_step"])
            self.assertEqual((eem.score_one, eem._fills_for, eem.mixed_net), before)
        finally:
            eem.score_one, eem._fills_for, eem.mixed_net, eem._state_index, eem._slot_time, eem.count_prior_creates = saved


if __name__ == "__main__":
    unittest.main()
