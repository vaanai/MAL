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
F = op.FROZEN_THRESHOLD


def _row(mint, day, score, k, size_sol, net0, status=1, sides=2, p=0.3, filled=True, censored=False):
    r = {"mint": mint, "spec": TARGET_SPEC_ID, "day": day, "score": score, "k": k, "size": op.size_lamports(size_sol), "censored": censored, "pool": "A"}
    if not censored:
        r.update(filled=filled, status=status, gross=0, net0=net0, sides=sides, p_press=p)
    return r


def _fixture_rows(sizes=(0.05,), ks=(6,)):
    """Per day: a 0.90 winner (+0.02 net0), a 0.83 loser (-0.01), a 0.81 winner (+0.01). Size scales net0."""
    rows = []
    for i, d in enumerate(DAYS):
        for k in ks:
            for s in sizes:
                m = s / 0.05
                rows.append(_row(f"w{i}", d, 0.90, k, s, int(0.02 * SOL * m)))
                rows.append(_row(f"l{i}", d, 0.83, k, s, int(-0.01 * SOL * m)))
                rows.append(_row(f"z{i}", d, 0.81, k, s, int(0.01 * SOL * m)))
    return rows


def _cell(**kw):
    return dict(op.FROZEN_CELL, **kw)


class GridShapeTests(unittest.TestCase):
    def test_grid_is_narrow_and_has_no_drift_axis(self):
        prim = op.primary_cells()
        self.assertEqual(len(prim), 12)
        self.assertEqual({c["threshold"] for c in prim}, {F, 0.82, 0.85})
        self.assertEqual({c["k"] for c in prim}, {6, 8, 10, 12})
        self.assertEqual({(c["size_sol"], c["fee"]) for c in prim}, {(0.05, 505_000)})
        cells = op.all_cells()
        self.assertEqual(len(cells), 36)
        self.assertEqual(len({op.cell_id(c) for c in cells}), 36)
        for c in cells:
            self.assertEqual(set(c), {"threshold", "k", "size_sol", "fee"})  # no drift key anywhere
        self.assertFalse(hasattr(op, "DRIFTS"))
        self.assertNotIn("drift", op.cell_id(op.FROZEN_CELL))

    def test_sensitivity_axes(self):
        axes = [op.cell_axis(c) for c in op.all_cells()]
        self.assertEqual((axes.count("primary"), axes.count("fee_sensitivity"), axes.count("size_sensitivity")), (12, 12, 12))
        self.assertTrue(all(c["threshold"] == F and c["fee"] == 505_000 for c in op.size_sens_cells()))
        self.assertTrue(all(c["fee"] == 155_000 for c in op.fee_sens_cells()))


class GridTests(unittest.TestCase):
    def test_threshold_subsets_and_fee(self):
        idx = op.index_rows(_fixture_rows())
        hi, _ = op.cell_trades(idx, _cell(threshold=0.85))
        mid, _ = op.cell_trades(idx, _cell(threshold=0.82))
        fz, _ = op.cell_trades(idx, _cell())
        self.assertEqual((len(hi), len(mid), len(fz)), (9, 18, 27))
        lo_fee, _ = op.cell_trades(idx, _cell(fee=155_000))
        self.assertAlmostEqual(lo_fee[0]["flat"], mixed_net(int(0.02 * SOL), 2, 1, 155_000, FLAT_FAIL))
        self.assertGreater(lo_fee[0]["press"], op.cell_trades(idx, _cell(threshold=0.85))[0][0]["press"])

    def test_cell_never_mixes_sizes(self):
        rows = _fixture_rows(sizes=(0.05, 0.5))
        idx = op.index_rows(rows)
        t05, _ = op.cell_trades(idx, _cell())
        t5, _ = op.cell_trades(idx, _cell(size_sol=0.5))
        self.assertEqual((len(t05), len(t5)), (27, 27))
        self.assertAlmostEqual(t5[0]["flat"] - mixed_net(int(0.2 * SOL), 2, 1, 505_000, FLAT_FAIL), 0.0)

    def test_censored_rows_counted_not_traded(self):
        rows = _fixture_rows() + [_row("c", DAYS[0], 0.95, 6, 0.05, 0, censored=True)]
        tr, cen = op.cell_trades(op.index_rows(rows), _cell())
        self.assertEqual((len(tr), cen), (27, 1))

    def test_cell_stats_fields(self):
        stats, _ = op.compute_grid(_fixture_rows(), [_cell(threshold=0.85)], DAYS)
        s = stats[0]
        want = mixed_net(int(0.02 * SOL), 2, 1, 505_000, FLAT_FAIL) / SOL
        self.assertEqual((s["n"], s["n_days_total"]), (9, 9))
        self.assertAlmostEqual(s["flat"]["mean_sol"], want)
        self.assertAlmostEqual(s["flat"]["sol_per_day"], want)
        self.assertEqual(s["flat"]["days_positive"], 9)
        self.assertEqual(len(s["press"]["ci90_sol"]), 2)

    def test_duplicate_rows_refused(self):
        r = _fixture_rows()
        with self.assertRaisesRegex(SystemExit, "duplicate"):
            op.index_rows(r + [r[0]])


class PairedNestedTests(unittest.TestCase):
    def _per_cell(self, rows=None):
        rows = rows or _fixture_rows()
        cells = [{"threshold": t, "k": 6, "size_sol": 0.05, "fee": 505_000} for t in (F, 0.82, 0.85)]
        _stats, per_cell = op.compute_grid(rows, cells, DAYS)
        return per_cell

    def test_paired_vs_frozen_charges_skipped_trades(self):
        pc = self._per_cell()
        ref = pc[op.cell_id(_cell())]
        d85 = op.paired_vs_reference(ref, pc[op.cell_id(_cell(threshold=0.85))])
        self.assertEqual(len(d85), 27)  # the common set is the frozen cell's mints
        by = {r["mint"]: r for r in d85}
        self.assertEqual(by["w0"]["dpress"], 0.0)  # entered in both
        self.assertFalse(by["l0"]["entered"])
        loser = next(t for t in ref if t["mint"] == "l0")
        self.assertAlmostEqual(by["l0"]["dpress"], -loser["press"])  # candidate scores 0, difference = -frozen's pnl
        self.assertTrue(all(r["dpress"] == 0.0 for r in op.paired_vs_reference(ref, ref)))

    def test_nested_is_paired_vs_frozen_with_ci_and_days(self):
        pc = self._per_cell()
        res = op.nested_threshold_lodo(pc, 6, [F, 0.82, 0.85], DAYS, min_train_trades=1)
        self.assertTrue(res["available"])
        self.assertEqual(res["reference"], op.cell_id(_cell()))
        p = res["heldout_paired_vs_frozen"]
        for leg in ("flat", "press"):
            self.assertEqual(len(p[leg]["ci90_sol"]), 2)
            self.assertEqual(p[leg]["n_days"], 9)
            self.assertIn("days_positive", p[leg])
        self.assertEqual(p["n"], 27)
        self.assertEqual(sum(res["times_chosen"].values()), 9)
        self.assertEqual(res["common_set_size"], 27)
        self.assertIsNotNone(res["optimism_gap_press_sol"])

    def test_no_cross_size_selection(self):
        # candidates are thresholds at ONE (k, size, fee); a 10x larger size's rows must not change the choice or the result
        small = op.nested_threshold_lodo(self._per_cell(), 6, [F, 0.82, 0.85], DAYS, min_train_trades=1)
        both = op.nested_threshold_lodo(self._per_cell(_fixture_rows(sizes=(0.05, 0.5))), 6, [F, 0.82, 0.85], DAYS, min_train_trades=1)
        self.assertEqual(small["folds"], both["folds"])
        self.assertEqual(small["heldout_paired_vs_frozen"]["press"]["mean_sol"], both["heldout_paired_vs_frozen"]["press"]["mean_sol"])
        ids = small["candidates"]
        self.assertEqual({i.split("_s")[1] for i in ids}, {"0.05_f505000"})

    def test_fold_unavailable_when_training_floor_not_met(self):
        res = op.nested_threshold_lodo(self._per_cell(), 6, [F, 0.82, 0.85], DAYS, min_train_trades=10_000)
        self.assertFalse(res["available"])
        self.assertEqual(res["n_unavailable_folds"], 9)
        self.assertTrue(all(f["chosen"] is None and not f["available"] for f in res["folds"]))
        self.assertEqual(res["heldout_paired_vs_frozen"]["n"], 0)  # no fallback to the reference

    def test_default_floor_is_100(self):
        self.assertEqual(op.NESTED_MIN_TRAIN_TRADES, 100)
        res = op.nested_threshold_lodo(self._per_cell(), 6, [F, 0.82, 0.85], DAYS)  # only 24 training trades per fold
        self.assertEqual(res["n_unavailable_folds"], 9)

    def test_floor_filters_candidates_not_folds(self):
        # floor of 24 entered trades on 8 training days: met by the frozen cell (3 per day x 8) but not by 0.85 (8)
        res = op.nested_threshold_lodo(self._per_cell(), 6, [F, 0.82, 0.85], DAYS, min_train_trades=24)
        self.assertEqual(res["n_unavailable_folds"], 0)
        self.assertEqual(res["times_chosen"][op.cell_id(_cell(threshold=0.85))], 0)

    def test_needs_two_days(self):
        self.assertFalse(op.nested_threshold_lodo(self._per_cell(), 6, [F, 0.82, 0.85], ["d"], min_train_trades=1)["available"])


class AnalyzeTests(unittest.TestCase):
    def _rep(self):
        rows = _fixture_rows(sizes=(0.05, 0.1, 0.25, 0.5), ks=(6, 8, 10, 12))
        scores = {r["mint"]: r["score"] for r in rows}
        days = {r["mint"]: r["day"] for r in rows}
        n_sel = len({r["mint"] for r in rows if r["score"] >= F})
        return op.analyze(rows, scores, {"n_selected_at_or_above_threshold": n_sel}, days, min_train_trades=1)

    def test_report_contents(self):
        rep = self._rep()
        self.assertEqual((rep["n_cells_tried"], rep["n_primary_cells"], rep["cumulative_tries_on_pool"]), (36, 12, 18 + 36))
        self.assertEqual(set(rep["nested_threshold_lodo_by_k"]), {"6", "8", "10", "12"})
        md = op.render_md(rep)
        self.assertIn("**EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE EVIDENCE.**", md)
        self.assertIn("cumulative tries", md)
        self.assertIn("optimism gap", md)
        self.assertIn(op.SIZE_LABEL, md)
        self.assertIn("UPPER BOUND", md)
        self.assertNotIn("drift", md.lower())

    def test_integrity(self):
        rows = _fixture_rows()
        scores = {r["mint"]: r["score"] for r in rows}
        with self.assertRaisesRegex(SystemExit, "stored OOF score"):
            op.check_integrity(rows, dict(scores, w0=0.1), None, None)
        with self.assertRaisesRegex(SystemExit, "threshold file says"):
            op.check_integrity(rows, scores, {"n_selected_at_or_above_threshold": 5}, None)


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
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(SystemExit, "reserved holdout"):
            op.main(["--out-dir", td, "--verify-view", "--fast-dir", "/data/mal/blocks-clean/x", "--oracle-insample-dir", td, "--oracle-live-dir", td, "--tries-log", str(Path(td) / "t.jsonl")])

    def test_more_than_two_workers_is_a_real_error(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(SystemExit, "max-workers"):
            op.main(["--out-dir", td, "--max-workers", "3", "--tries-log", str(Path(td) / "t.jsonl")])


class TriesTests(unittest.TestCase):
    def test_every_cell_is_logged_once_with_axis(self):
        rows = _fixture_rows(sizes=(0.05, 0.1, 0.25, 0.5), ks=(6, 8, 10, 12))
        scores = {r["mint"]: r["score"] for r in rows}
        rep = op.analyze(rows, scores, None, {r["mint"]: r["day"] for r in rows}, min_train_trades=1)
        with tempfile.TemporaryDirectory() as td:
            out, log = Path(td), Path(td) / "tries.jsonl"
            self.assertEqual(op.log_tries(rep, out, log), 36)
            lines = [json.loads(x) for x in log.read_text().splitlines()]
            self.assertEqual(len(lines), 36)
            self.assertTrue(all(x["tool"] == op.TOOL and x["role"] == "exploration" for x in lines))
            self.assertEqual(sum(1 for x in lines if x["config"]["reference"]), 1)
            self.assertEqual({x["config"]["axis"] for x in lines}, {"primary", "fee_sensitivity", "size_sensitivity"})
            self.assertEqual(lines[-1]["variant_n"], 36)
            self.assertEqual(op.log_tries(rep, out, log), 0)
            # an earlier attempt that logged lines but lost its marker is not doubled
            (out / op.TRIES_MARKER).unlink()
            self.assertEqual(op.log_tries(rep, out, log), 0)
            self.assertEqual(len(log.read_text().splitlines()), 36)


class PatchTests(unittest.TestCase):
    def test_multi_cell_patch_emits_rows_per_k_size_without_drift_and_restores(self):
        fills = [SimpleNamespace(price_sol=1.0 + 0.1 * i) for i in range(10)]
        mint = SimpleNamespace(mig_ms=1_790_000_000_000)

        def fake_fills_for(m, migrate=True):
            return fills, 0, 0, None

        def fake_score(mint_id, m, feat, curve, through, hist, *, specs, size, priority, entry_land_k):
            eem._fills_for(m, migrate=True)
            net0 = size // 10 - 7
            flat = eem.mixed_net(net0, 2, 1, priority, FLAT_FAIL)
            press = eem.mixed_net(net0, 2, 1, priority, 0.25)
            return [{"spec": specs[0]["id"], "day": "2026-09-20", "filled": True, "gross": 0, "flat": flat, "press": press}]

        saved = (eem.score_one, eem._fills_for, eem.mixed_net)
        try:
            eem.score_one, eem._fills_for = fake_score, fake_fills_for
            before = (eem.score_one, eem._fills_for, eem.mixed_net)
            with op.multi_cell_patch({"m": 0.9, "low": 0.5}, 0.70):
                self.assertEqual(eem.score_one("low", mint, object(), None, 0, {}), [])
                rows = eem.score_one("m", mint, object(), None, 0, {})
            self.assertEqual((eem.score_one, eem._fills_for, eem.mixed_net), before)
        finally:
            eem.score_one, eem._fills_for, eem.mixed_net = saved
        self.assertEqual(len(rows), len(op.KS) * len(op.SIZES_SOL))
        self.assertTrue(all("drift" not in r for r in rows))
        self.assertTrue(all(abs(r["p_press"] - 0.25) < 1e-12 for r in rows))
        self.assertEqual({r["size"] for r in rows}, {op.size_lamports(s) for s in op.SIZES_SOL})


class ArtifactTests(unittest.TestCase):
    def test_frozen_threshold_matches_committed_artifact(self):
        _s, thr, _d, days = op.load_oof(op.DEFAULT_ARTIFACT_DIR)
        self.assertEqual(thr, op.FROZEN_THRESHOLD)
        self.assertEqual(len(set(days.values())), 9)


if __name__ == "__main__":
    unittest.main()
