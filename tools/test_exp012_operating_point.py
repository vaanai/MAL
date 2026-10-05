"""Tests for tools/exp012_operating_point.py. Fixtures only; the one real read is the committed OOF artifact."""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import tools.exploration_entry_model as eem
import tools.exp012_operating_point as op
from tools.exp011_freeze import TARGET_SPEC_ID
from tools.latency_curve import FLAT_FAIL, mixed_net

SOL = 1_000_000_000
DAYS = [f"2026-09-{d}" for d in range(19, 28)]


def _row(mint, day, score, k, size_sol, net0, drift=0.0, status=1, sides=2, p=0.3, filled=True, censored=False):
    r = {"mint": mint, "spec": TARGET_SPEC_ID, "day": day, "score": score, "k": k, "size": op.size_lamports(size_sol), "censored": censored, "drift": drift, "pool": "A"}
    if not censored:
        r.update(filled=filled, status=status, gross=0, net0=net0, sides=sides, p_press=p)
    return r


def _fixture_rows():
    """9 days x 2 mints: a high-score winner (+0.02 SOL net0) and a mid-score loser; k=6 only, 0.05 SOL."""
    rows = []
    for i, d in enumerate(DAYS):
        rows.append(_row(f"w{i}", d, 0.9, 6, 0.05, int(0.02 * SOL), drift=0.05 if i % 3 else 0.25))
        rows.append(_row(f"l{i}", d, 0.72, 6, 0.05, int(-0.01 * SOL), drift=0.0))
    return rows


def _cell(**kw):
    return dict(op.FROZEN_CELL, **kw)


class GridTests(unittest.TestCase):
    def setUp(self):
        self.rows = _fixture_rows()
        self.idx = op.index_rows(self.rows)

    def test_grid_size_and_ids_are_unique(self):
        cells = op.all_cells()
        self.assertEqual(len(cells), 5 * 4 * 4 * 2 * 4)
        self.assertEqual(len({op.cell_id(c) for c in cells}), len(cells))
        self.assertIn(op.cell_id(op.FROZEN_CELL), {op.cell_id(c) for c in cells})

    def test_threshold_subsets(self):
        hi, _, _ = op.cell_trades(self.idx, _cell())  # frozen 0.803: winners only
        lo, _, _ = op.cell_trades(self.idx, _cell(threshold=0.70))
        self.assertEqual((len(hi), len(lo)), (9, 18))
        self.assertTrue({t["mint"] for t in hi} <= {t["mint"] for t in lo})

    def test_fee_is_applied_offline_exactly_like_mixed_net(self):
        for fee in (155_000, 505_000):
            tr, _, _ = op.cell_trades(self.idx, _cell(fee=fee))
            self.assertAlmostEqual(tr[0]["flat"], mixed_net(int(0.02 * SOL), 2, 1, fee, FLAT_FAIL))
            self.assertAlmostEqual(tr[0]["press"], mixed_net(int(0.02 * SOL), 2, 1, fee, 0.3))
        lo, _, _ = op.cell_trades(self.idx, _cell(fee=155_000))
        hi, _, _ = op.cell_trades(self.idx, _cell(fee=505_000))
        self.assertGreater(lo[0]["flat"], hi[0]["flat"])

    def test_drift_skip_drops_trade_without_fee_and_none_drift_is_kept(self):
        tr, _, skipped = op.cell_trades(self.idx, _cell(drift=0.10))
        self.assertEqual((len(tr), skipped), (6, 3))  # i % 3 == 0 -> drift 0.25 skipped
        rows = _fixture_rows() + [_row("nodrift", DAYS[0], 0.95, 6, 0.05, 0, drift=None)]
        tr2, _, sk2 = op.cell_trades(op.index_rows(rows), _cell(drift=0.10))
        self.assertEqual((len(tr2), sk2), (7, 3))
        self.assertEqual(len(op.cell_trades(self.idx, _cell(drift=None))[0]), 9)

    def test_censored_rows_are_counted_not_traded(self):
        rows = _fixture_rows() + [_row("c", DAYS[0], 0.95, 6, 0.05, 0, censored=True)]
        tr, cen, _ = op.cell_trades(op.index_rows(rows), _cell())
        self.assertEqual((len(tr), cen), (9, 1))

    def test_cell_stats_fields(self):
        stats, _ = op.compute_grid(self.rows, [_cell()], DAYS)
        s = stats[0]
        self.assertEqual((s["n"], s["n_days_total"]), (9, 9))
        self.assertAlmostEqual(s["trades_per_day"], 1.0)
        want = mixed_net(int(0.02 * SOL), 2, 1, 505_000, FLAT_FAIL) / SOL
        self.assertAlmostEqual(s["flat"]["mean_sol"], want)
        self.assertAlmostEqual(s["flat"]["sol_per_day"], want)  # 1 trade/day
        self.assertEqual(s["flat"]["days_positive"], 9)
        self.assertEqual(len(s["press"]["ci90_sol"]), 2)
        self.assertAlmostEqual(s["flat"]["mean_bps_of_size"], want / 0.05 * 1e4)

    def test_empty_cell_does_not_crash(self):
        stats, _ = op.compute_grid(self.rows, [_cell(k=4)], DAYS)  # no k=4 rows
        self.assertEqual(stats[0]["n"], 0)
        self.assertIsNone(stats[0]["press"])


class NestedTests(unittest.TestCase):
    def _per_cell(self):
        # "good" wins every day; "bad" loses; "lucky" loses 8 days and wins big on the last.
        def trades(vals):
            return [{"mint": f"{i}", "day": d, "filled": True, "status": 1, "gross": 0, "flat": v, "press": v} for i, (d, v) in enumerate(zip(DAYS, vals))]

        return {"bad": trades([-1e6] * 9), "good": trades([2e6] * 9), "lucky": trades([-1e6] * 8 + [5e7])}

    def test_picks_on_other_days_and_scores_held_out(self):
        res = op.nested_lodo(self._per_cell(), ["bad", "good", "lucky"], DAYS, "per_day", min_train_trades=1)
        self.assertTrue(res["available"])
        by = {c["day"]: c["chosen"] for c in res["choices_by_day"]}
        # held out = the lucky day: lucky's other days are all losses, so good is chosen
        self.assertEqual(by[DAYS[-1]], "good")
        # held out = any other day: lucky's other-days sum includes the +5e7 spike, so it is chosen and scores -1e6
        self.assertEqual(by[DAYS[0]], "lucky")
        self.assertEqual(res["pooled"]["n"], 9)
        self.assertAlmostEqual(res["pooled"]["press"]["mean_sol"], (8 * -1e6 + 2e6) / 9 / SOL)

    def test_per_trade_needs_min_train_trades(self):
        res = op.nested_lodo(self._per_cell(), ["bad", "good", "lucky"], DAYS, "per_trade", min_train_trades=100)
        self.assertTrue(all(c["chosen"] == "bad" for c in res["choices_by_day"]))  # nothing qualifies: first in order

    def test_needs_two_days_and_valid_objective(self):
        self.assertFalse(op.nested_lodo({"a": []}, ["a"], ["d"], "per_day")["available"])
        with self.assertRaises(ValueError):
            op.nested_lodo({"a": []}, ["a"], DAYS, "x")


class AnalyzeTests(unittest.TestCase):
    def test_analyze_reports_n_reference_and_nested(self):
        rows = _fixture_rows()
        scores = {r["mint"]: r["score"] for r in rows}
        days = {r["mint"]: r["day"] for r in rows}
        cells = [_cell(), _cell(threshold=0.70), _cell(fee=155_000)]
        rep = op.analyze(rows, scores, {"n_selected_at_or_above_threshold": 9}, days, cells)
        self.assertEqual(rep["n_cells_tried"], 3)
        self.assertIn("NOT A PROMOTE", rep["status"])
        self.assertEqual(rep["reference_frozen"]["stats"]["id"], op.cell_id(op.FROZEN_CELL))
        self.assertTrue(rep["nested_lodo"]["per_day"]["available"])
        md = op.render_md(rep)
        self.assertIn("**EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE EVIDENCE.**", md)
        self.assertIn("N = 3 cells", md)

    def test_integrity_refuses_score_mismatch_and_selected_count(self):
        rows = _fixture_rows()
        scores = {r["mint"]: r["score"] for r in rows}
        with self.assertRaisesRegex(SystemExit, "stored OOF score"):
            op.check_integrity(rows, dict(scores, w0=0.1), None, None)
        with self.assertRaisesRegex(SystemExit, "threshold file says"):
            op.check_integrity(rows, scores, {"n_selected_at_or_above_threshold": 5}, None)

    def test_duplicate_rows_refused(self):
        r = _fixture_rows()
        with self.assertRaisesRegex(SystemExit, "duplicate"):
            op.index_rows(r + [r[0]])


class GuardTests(unittest.TestCase):
    def _args(self, **kw):
        base = dict(fast_dir=None, oracle_insample_dir=None, oracle_live_dir=None, verify_view=True)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_uses_the_latency_tools_guard_and_refuses_forbidden_roots(self):
        from tools.exp012_latency_sensitivity import guarded_roots

        self.assertIs(op.guarded_roots, guarded_roots)
        with tempfile.TemporaryDirectory() as td:
            ok = Path(td)
            for bad in ("/data/mal/blocks/fresh-0903/w1", "/data/mal/blocks-clean/fresh-0903", "/data/mal/clean-view/fresh-0903", "/var/lib/mal/backfill-fast-b"):
                with self.subTest(bad=bad), self.assertRaisesRegex(SystemExit, "reserved holdout"):
                    op.guarded_roots(self._args(fast_dir=Path(bad), oracle_insample_dir=ok, oracle_live_dir=ok))

    def test_missing_roots_and_verify_view_refused(self):
        with self.assertRaisesRegex(SystemExit, "all required"):
            op.guarded_roots(self._args())
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(SystemExit, "verify-view"):
            op.guarded_roots(self._args(fast_dir=Path(td), oracle_insample_dir=Path(td), oracle_live_dir=Path(td), verify_view=False))

    def test_main_refuses_forbidden_root_before_any_pass(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(SystemExit, "reserved holdout"):
                op.main(["--out-dir", td, "--verify-view", "--fast-dir", "/data/mal/blocks-clean/x", "--oracle-insample-dir", td, "--oracle-live-dir", td, "--tries-log", str(Path(td) / "t.jsonl")])


class TriesTests(unittest.TestCase):
    def test_every_cell_is_logged_once(self):
        rows = _fixture_rows()
        scores = {r["mint"]: r["score"] for r in rows}
        cells = [_cell(), _cell(threshold=0.70), _cell(fee=155_000)]
        rep = op.analyze(rows, scores, None, {r["mint"]: r["day"] for r in rows}, cells)
        with tempfile.TemporaryDirectory() as td:
            out, log = Path(td), Path(td) / "tries.jsonl"
            self.assertEqual(op.log_tries(rep, out, log), 3)
            lines = [json.loads(x) for x in log.read_text().splitlines()]
            self.assertEqual(len(lines), 3)
            self.assertTrue(all(x["tool"] == op.TOOL and x["role"] == "exploration" for x in lines))
            self.assertEqual(sum(1 for x in lines if x["config"]["reference"]), 1)
            self.assertEqual({x["config"]["cell"] for x in lines}, {op.cell_id(c) for c in cells})
            self.assertEqual(lines[-1]["variant_n"], len(lines))
            self.assertEqual(op.log_tries(rep, out, log), 0)  # idempotent
            self.assertEqual(len(log.read_text().splitlines()), 3)


class PatchTests(unittest.TestCase):
    def test_multi_cell_patch_emits_rows_per_k_size_and_restores(self):
        fills = [SimpleNamespace(price_sol=1.0 + 0.1 * i) for i in range(10)]
        mint = SimpleNamespace(mig_ms=1_790_000_000_000)

        def fake_state_index(f, slot, bound):
            return min(slot, len(f) - 1)

        def fake_fills_for(m, migrate=True):
            return fills, 0, 0, None

        def fake_score(mint_id, m, feat, curve, through, hist, *, specs, size, priority, entry_land_k):
            eem._fills_for(m, migrate=True)
            net0 = size // 10 - 7
            flat = eem.mixed_net(net0, 2, 1, priority, FLAT_FAIL)
            press = eem.mixed_net(net0, 2, 1, priority, 0.25)
            return [{"spec": specs[0]["id"], "day": "2026-09-20", "filled": True, "gross": 0, "flat": flat, "press": press}]

        saved = (eem.score_one, eem._fills_for, eem.mixed_net, eem._state_index)
        try:
            eem.score_one, eem._fills_for, eem._state_index = fake_score, fake_fills_for, fake_state_index
            before = (eem.score_one, eem._fills_for, eem.mixed_net)
            with op.multi_cell_patch({"m": 0.9, "low": 0.5}, 0.70):
                self.assertEqual(eem.score_one("low", mint, object(), None, 0, {}), [])  # below t_min: nothing
                rows = eem.score_one("m", mint, object(), None, 0, {})
            self.assertEqual((eem.score_one, eem._fills_for, eem.mixed_net), before)
        finally:
            eem.score_one, eem._fills_for, eem.mixed_net, eem._state_index = saved
        self.assertEqual(len(rows), (1 + len(op.KS)) * len(op.SIZES_SOL))
        k6 = [r for r in rows if r["k"] == 6]
        self.assertTrue(all(abs(r["drift"] - (1.6 / 1.1 - 1)) < 1e-9 for r in k6))
        self.assertTrue(all(r["drift"] == 0.0 for r in rows if r["k"] == 1))
        self.assertTrue(all(abs(r["p_press"] - 0.25) < 1e-12 for r in rows))
        self.assertEqual({r["size"] for r in rows}, {op.size_lamports(s) for s in op.SIZES_SOL})


class ArtifactTests(unittest.TestCase):
    def test_frozen_threshold_matches_committed_artifact(self):
        _s, thr, _d, days = op.load_oof(op.DEFAULT_ARTIFACT_DIR)
        self.assertEqual(thr, op.FROZEN_THRESHOLD)
        self.assertEqual(len(set(days.values())), 9)


if __name__ == "__main__":
    unittest.main()
