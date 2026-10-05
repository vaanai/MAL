"""Tests for tools.exp012_entry_veto (exploration only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import tools.exploration_entry_model as eem
import tools.exp012_entry_veto as ev
import tools.exp012_operating_point as op
from tools.exp011_freeze import TARGET_SPEC_ID

SOL = 1_000_000_000
DAYS = [f"2026-09-{d}" for d in range(19, 28)]
MIG = 1000


def P(slot, side="buy", sol=0.1, price=1.0, venue="pumpswap"):
    return SimpleNamespace(slot=slot, side=side, sol_lamports=int(sol * SOL), price_sol=price, venue=venue)


class FeatureTests(unittest.TestCase):
    def test_window_counts_and_drift(self):
        fills = [P(MIG, "buy", 9, 1.0), P(MIG + 1, "buy", 1, 1.2), P(MIG + 1, "sell", 3, 1.1), P(MIG + 2, "sell", 0.5, 1.4)]
        f = ev.veto_features(fills, MIG, 1.0)
        self.assertEqual((f["n_buys"], f["n_sells"]), (1, 2))
        self.assertEqual((f["buy_sol"], f["sell_sol"], f["max_sell"]), (SOL, int(3.5 * SOL), 3 * SOL))
        self.assertAlmostEqual(f["drift"], 0.4)
        self.assertTrue(ev.vetoed("drift_gt_25", f))
        self.assertFalse(ev.vetoed("drift_gt_50", f))
        self.assertTrue(ev.vetoed("netflow_neg", f))
        self.assertTrue(ev.vetoed("maxsell_ge_2sol", f))
        self.assertFalse(ev.vetoed("maxsell_ge_5sol", f))
        self.assertTrue(ev.vetoed("sells_ge_buys", f))

    def test_later_slots_never_trigger_a_veto(self):
        calm = [P(MIG, "buy", 1, 1.0), P(MIG + 1, "buy", 1, 1.01)]
        later = calm + [P(MIG + 3, "sell", 50, 3.0), P(MIG + 4, "sell", 50, 0.2), P(MIG + 5, "sell", 50, 9.0)]
        base = ev.veto_features(calm, MIG, 1.0, [])
        f = ev.veto_features(later, MIG, 1.0, [MIG + 3, MIG + 5])
        self.assertEqual({k: v for k, v in f.items()}, {k: v for k, v in base.items()})
        for r in ev.RULE_IDS:
            self.assertFalse(ev.vetoed(r, f), r)

    def test_prints_at_or_before_the_migration_slot_are_not_read(self):
        fills = [P(MIG - 1, "sell", 50, 9.0), P(MIG, "sell", 50, 9.0)]
        f = ev.veto_features(fills, MIG, 1.0, [MIG])
        self.assertEqual((f["n_window"], f["max_sell"], f["drift"], f["creator_sold"]), (0, 0, 0.0, False))
        for r in ev.RULE_IDS:
            self.assertFalse(ev.vetoed(r, f), r)

    def test_bonding_prints_ignored_and_creator_in_window(self):
        f = ev.veto_features([P(MIG + 1, "sell", 9, 2.0, venue="bonding")], MIG, 1.0, [MIG + 2])
        self.assertEqual(f["n_window"], 0)
        self.assertTrue(f["creator_sold"])
        self.assertTrue(ev.vetoed("creator_sold", f))

    def test_empty_window_is_no_information(self):
        f = ev.veto_features([], MIG, 1.0)
        self.assertFalse(ev.vetoed("sells_ge_buys", f))
        self.assertFalse(ev.vetoed("netflow_neg", f))
        self.assertFalse(ev.vetoed("drift_gt_25", None))

    def test_family_size_and_cumulative(self):
        self.assertEqual(len(ev.RULE_IDS), 7)
        self.assertEqual(ev.EXISTING_TRIES_ON_POOL, 54)


def _row(mint, day, net0, veto, score=0.9):
    return {"mint": mint, "spec": TARGET_SPEC_ID, "day": day, "score": score, "k": 6, "size": op.size_lamports(0.05), "censored": False, "pool": "A",
            "filled": True, "status": 1, "gross": 0, "net0": net0, "sides": 2, "p_press": 0.3, "veto": veto}


def _feats(**kw):
    base = {"n_window": 0, "n_buys": 0, "n_sells": 0, "buy_sol": 0, "sell_sol": 0, "max_sell": 0, "drift": 0.0, "creator_sold": False, "window": [1, 2]}
    base.update(kw)
    return base


def _fixture(n_per_day=12):
    """Per day: stops (creator_sold, -0.01 SOL) and winners (+0.01). creator_sold perfectly separates them."""
    rows = []
    for i, d in enumerate(DAYS):
        for j in range(n_per_day):
            bad = j % 3 == 0
            rows.append(_row(f"m{i}_{j}", d, int((-0.01 if bad else 0.01) * SOL), _feats(creator_sold=bad)))
    return rows


class AnalysisTests(unittest.TestCase):
    def test_per_rule_counts_and_pairing(self):
        rows = _fixture()
        scores = {r["mint"]: r["score"] for r in rows}
        rep = ev.analyze(rows, scores, None, {r["mint"]: r["day"] for r in rows}, min_train_trades=1)
        by = {r["rule"]: r for r in rep["rules"]}
        cs = by["creator_sold"]
        self.assertEqual((cs["n_vetoed"], cs["n_kept"]), (36, 72))
        self.assertGreater(cs["paired_vs_frozen"]["press"]["mean_sol"], 0)
        self.assertEqual(by["drift_gt_25"]["n_vetoed"], 0)
        self.assertEqual(by["drift_gt_25"]["paired_vs_frozen"]["press"]["mean_sol"], 0)
        self.assertLess(cs["vetoed_press_mean_sol"], 0)
        self.assertEqual((rep["n_rules_tried"], rep["cumulative_tries_on_pool"]), (7, 61))
        self.assertIn("NOT A PROMOTE", rep["status"])

    def test_rule_vetoing_everything_does_not_crash(self):
        rows = [_row(f"a{i}", DAYS[i % 9], int(0.01 * SOL), _feats(creator_sold=True)) for i in range(18)]
        trades, feats, _ = ev.frozen_trades(rows)
        s = ev.rule_stats(trades, feats, "creator_sold", 9)
        self.assertEqual((s["n_vetoed"], s["n_kept"]), (18, 0))
        self.assertLess(s["paired_vs_frozen"]["press"]["mean_sol"], 0)

    def test_nested_lodo_picks_the_separating_rule_and_includes_frozen(self):
        rows = _fixture()
        trades, feats, _ = ev.frozen_trades(rows)
        n = ev.nested_lodo(trades, feats, ev.RULE_IDS, DAYS, min_train_trades=20)
        self.assertTrue(n["available"])
        self.assertEqual(n["candidates"][0], ev.FROZEN_ID)
        self.assertEqual(n["times_chosen"]["creator_sold"], 9)
        self.assertEqual(n["n_unavailable_folds"], 0)
        self.assertGreater(n["heldout_paired_vs_frozen"]["press"]["mean_sol"], 0)
        self.assertEqual(n["heldout_paired_vs_frozen"]["n"], 108)
        # in-sample best equals the nested pick here, so the optimism gap is ~0
        self.assertEqual(n["insample_best"], "creator_sold")
        self.assertAlmostEqual(n["optimism_gap_press_sol"], 0.0, places=9)

    def test_nested_lodo_selection_uses_training_days_only(self):
        # a rule that is good on day 0 only must not be chosen to score day 0 (picked on the other 8 days)
        rows = []
        for i, d in enumerate(DAYS):
            for j in range(12):
                bad = i == 0 and j % 2 == 0
                rows.append(_row(f"m{i}_{j}", d, int((-0.05 if bad else 0.01) * SOL), _feats(creator_sold=bad)))
        trades, feats, _ = ev.frozen_trades(rows)
        n = ev.nested_lodo(trades, feats, ("creator_sold",), DAYS, min_train_trades=20)
        fold0 = [f for f in n["folds"] if f["day"] == DAYS[0]][0]
        self.assertEqual(fold0["chosen"], ev.FROZEN_ID)
        # on days 1.. the day-0 gain is in the training set, so the rule is chosen there
        self.assertEqual(n["times_chosen"]["creator_sold"], 8)

    def test_training_floor_leaves_folds_unavailable(self):
        rows = _fixture(n_per_day=2)
        trades, feats, _ = ev.frozen_trades(rows)
        n = ev.nested_lodo(trades, feats, ev.RULE_IDS, DAYS, min_train_trades=100)
        self.assertFalse(n["available"])
        self.assertEqual(n["n_unavailable_folds"], 9)

    def test_integrity_refuses_foreign_scores(self):
        rows = _fixture(2)
        with self.assertRaises(SystemExit):
            ev.analyze(rows, {r["mint"]: 0.1 for r in rows}, None, None)

    def test_render_has_all_rules_and_banner(self):
        rows = _fixture()
        scores = {r["mint"]: r["score"] for r in rows}
        rep = ev.analyze(rows, scores, None, {r["mint"]: r["day"] for r in rows}, min_train_trades=20)
        md = ev.render_md(rep)
        self.assertIn("EXPLORATION, BEST-OF-N, NOT A PROMOTE", md)
        for r in ev.RULE_IDS:
            self.assertIn(f"| {r} |", md)
        self.assertIn("optimism gap", md)


class GuardTests(unittest.TestCase):
    def test_uses_the_shared_guard_and_refuses_forbidden_roots(self):
        from tools.exp012_latency_sensitivity import guarded_roots

        self.assertIs(ev.guarded_roots, guarded_roots)
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(SystemExit, "reserved holdout"):
            ev.main(["--out-dir", td, "--verify-view", "--fast-dir", "/data/mal/blocks-clean/x", "--oracle-insample-dir", td, "--oracle-live-dir", td, "--tries-log", str(Path(td) / "t.jsonl")])

    def test_more_than_two_workers_refused(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(SystemExit, "max-workers"):
            ev.main(["--out-dir", td, "--max-workers", "3", "--tries-log", str(Path(td) / "t.jsonl")])

    def test_roots_required(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(SystemExit):
            ev.main(["--out-dir", td, "--tries-log", str(Path(td) / "t.jsonl")])


class TriesTests(unittest.TestCase):
    def test_every_rule_logged_once_and_idempotent(self):
        rows = _fixture()
        scores = {r["mint"]: r["score"] for r in rows}
        rep = ev.analyze(rows, scores, None, {r["mint"]: r["day"] for r in rows}, min_train_trades=20)
        with tempfile.TemporaryDirectory() as td:
            out, log = Path(td), Path(td) / "tries.jsonl"
            self.assertEqual(ev.log_tries(rep, out, log), 7)
            lines = [json.loads(x) for x in log.read_text().splitlines()]
            self.assertEqual(len(lines), 7)
            self.assertTrue(all(x["tool"] == ev.TOOL and x["role"] == "exploration" for x in lines))
            self.assertEqual({x["config"]["rule"] for x in lines}, set(ev.RULE_IDS))
            self.assertEqual(ev.log_tries(rep, out, log), 0)
            (out / ev.TRIES_MARKER).unlink()
            self.assertEqual(ev.log_tries(rep, out, log), 0)
            self.assertEqual(len(log.read_text().splitlines()), 7)


class PatchTests(unittest.TestCase):
    def test_veto_patch_attaches_window_only_features_and_restores(self):
        fills = [P(MIG, "buy", 1, 1.0), P(MIG + 1, "sell", 1, 1.1), P(MIG + 4, "sell", 90, 5.0)]
        mint = SimpleNamespace(mig_ms=1_790_000_000_000)

        def fake_fills_for(m, migrate=True):
            return fills, MIG, 0, 1.0

        def fake_score(mint_id, m, feat, curve, through, hist, *, specs, size, priority, entry_land_k):
            eem._fills_for(m, migrate=True)
            net0 = 5
            flat = eem.mixed_net(net0, 2, 1, priority, 0.15)
            press = eem.mixed_net(net0, 2, 1, priority, 0.25)
            return [{"spec": specs[0]["id"], "day": "2026-09-20", "filled": True, "gross": 0, "flat": flat, "press": press}]

        row = {"mint": "m", "trader": "CREATOR", "venue": "pumpswap", "side": "sell", "slot": MIG + 1}
        late = dict(row, slot=MIG + 4)
        saved = (eem.score_one, eem._fills_for, eem.mixed_net, eem.print_from_trade_row)
        try:
            eem.score_one, eem._fills_for = fake_score, fake_fills_for
            plain = eem.print_from_trade_row = lambda r: None
            before = (eem.score_one, eem._fills_for, eem.mixed_net)
            with ev.veto_patch({"m": 0.9, "low": 0.1}, 0.8):
                eem.print_from_trade_row(row)
                eem.print_from_trade_row(late)
                eem.print_from_trade_row(dict(row, mint="low"))
                self.assertEqual(eem.score_one("low", mint, SimpleNamespace(creator="CREATOR"), None, 0, {}), [])
                rows = eem.score_one("m", mint, SimpleNamespace(creator="CREATOR"), None, 0, {})
            self.assertEqual(len(rows), 1)
            v = rows[0]["veto"]
            self.assertEqual((v["n_sells"], v["max_sell"]), (1, SOL))
            self.assertTrue(v["creator_sold"])  # the mig+1 sell, not the mig+4 one
            self.assertEqual(ev._SELLS, {})
            self.assertEqual((eem.score_one, eem._fills_for, eem.mixed_net), before)
            self.assertIs(eem.print_from_trade_row, plain)
        finally:
            eem.score_one, eem._fills_for, eem.mixed_net, eem.print_from_trade_row = saved

    def test_creator_sell_after_window_does_not_set_flag(self):
        mint = SimpleNamespace(mig_ms=1_790_000_000_000)
        fills = [P(MIG, "buy", 1, 1.0)]
        saved = (eem.score_one, eem._fills_for, eem.mixed_net, eem.print_from_trade_row)

        def fake_score(mint_id, m, feat, curve, through, hist, *, specs, size, priority, entry_land_k):
            eem._fills_for(m, migrate=True)
            flat = eem.mixed_net(5, 2, 1, priority, 0.15)
            press = eem.mixed_net(5, 2, 1, priority, 0.25)
            return [{"spec": specs[0]["id"], "day": "2026-09-20", "filled": True, "gross": 0, "flat": flat, "press": press}]

        try:
            eem.score_one, eem._fills_for = fake_score, lambda m, migrate=True: (fills, MIG, 0, 1.0)
            eem.print_from_trade_row = lambda r: None
            with ev.veto_patch({"m": 0.9}, 0.8):
                eem.print_from_trade_row({"mint": "m", "trader": "C", "venue": "pumpswap", "side": "sell", "slot": MIG + 3})
                eem.print_from_trade_row({"mint": "m", "trader": "C", "venue": "pumpswap", "side": "sell", "slot": MIG})
                rows = eem.score_one("m", mint, SimpleNamespace(creator="C"), None, 0, {})
            self.assertFalse(rows[0]["veto"]["creator_sold"])
        finally:
            eem.score_one, eem._fills_for, eem.mixed_net, eem.print_from_trade_row = saved


if __name__ == "__main__":
    unittest.main()
