"""Tests for tools/exp012_latency_sensitivity.py. Fixtures only; the one real
file read is the committed freeze artifact ARTIFACTS/exp012 (OOF scores)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import tools.exp012_latency_sensitivity as ls
import tools.exploration_exits as ex
import tools.oracle_insample_adapter as ia
import tools.oracle_live_adapter as la
from tools.exp012_fixtures import write_fast_format_root, write_zst_jsonl
from tools.test_exp012_latency_entry import write_root_with_midprint

SOL = 1_000_000_000


def _row(mint: str, k: int, flat: float, press: float, filled: bool = True, day: str = "2026-09-20") -> dict:
    return {"mint": mint, "day": day, "entry_land_k": k, "filled": filled, "status": 1, "gross": 0, "flat": flat, "press": press, "pool": "A"}


class AnalyzeTests(unittest.TestCase):
    def setUp(self) -> None:
        # 3 mints, scores .9/.5/.95; threshold .8 selects m1, m3. Edge decays with k.
        self.scores = {"m1": 0.9, "m2": 0.5, "m3": 0.95}
        rows = []
        for k, (a, b) in {1: (0.10, 0.06), 2: (0.02, 0.01), 4: (-0.04, -0.05)}.items():
            rows += [_row("m1", k, a * SOL, b * SOL), _row("m2", k, -0.1 * SOL, -0.1 * SOL, filled=(k == 1)), _row("m3", k, a * SOL, b * SOL, day="2026-09-21")]
        self.rep = ls.analyze(rows, self.scores, 0.8)

    def test_selection_is_by_oof_score_and_baseline_is_everything(self) -> None:
        for k in ("1", "2", "4"):
            self.assertEqual(self.rep["by_k"][k]["entered"]["n"], 2)
            self.assertEqual(self.rep["by_k"][k]["baseline_unfiltered"]["n"], 3)

    def test_fill_rate_and_means(self) -> None:
        k1, k2 = self.rep["by_k"]["1"], self.rep["by_k"]["2"]
        self.assertEqual(k1["entered"]["fill_rate"], 1.0)
        self.assertAlmostEqual(k1["baseline_unfiltered"]["fill_rate"], 1.0)
        self.assertAlmostEqual(k2["baseline_unfiltered"]["fill_rate"], 2 / 3)
        self.assertAlmostEqual(k1["entered"]["flat"]["mean_sol"], 0.10)
        self.assertAlmostEqual(k1["entered"]["press"]["mean_sol"], 0.06)
        self.assertEqual(len(k1["entered"]["press"]["ci90_sol"]), 2)

    def test_zero_crossing_is_interpolated_between_measured_ks(self) -> None:
        z = self.rep["press_zero_crossing"]
        self.assertTrue(z["crosses"])
        self.assertEqual(z["first_k_nonpositive"], 4)
        # press mean: k2 = +0.01, k4 = -0.05 -> 2 + 2 * 0.01/0.06
        self.assertAlmostEqual(z["interpolated_k"], 2 + 2 * 0.01 / 0.06)

    def test_no_crossing_is_not_extrapolated(self) -> None:
        rows = [_row("m1", k, 0.1 * SOL, 0.05 * SOL) for k in (1, 2)]
        z = ls.analyze(rows, {"m1": 0.9}, 0.8)["press_zero_crossing"]
        self.assertFalse(z["crosses"])
        self.assertIn("no extrapolation", z["note"])

    def test_row_without_oof_score_is_counted_not_entered(self) -> None:
        rep = ls.analyze([_row("zz", 1, 0.1 * SOL, 0.1 * SOL)], {}, 0.5)
        self.assertEqual(rep["by_k"]["1"]["n_without_oof_score"], 1)
        self.assertEqual(rep["by_k"]["1"]["entered"]["n"], 0)

    def test_render_marks_exploration(self) -> None:
        md = ls.render_md(self.rep)
        self.assertIn("EXPLORATION, NOT EVIDENCE", md)
        self.assertIn("OUT-OF-FOLD", md)


class IntegrityTests(unittest.TestCase):
    THR = {"n_oof": 3, "n_selected_at_or_above_threshold": 2}
    SC = {"m1": 0.9, "m2": 0.5, "m3": 0.95}
    DAYS = {"m1": "2026-09-20", "m2": "2026-09-20", "m3": "2026-09-20"}

    def _rows(self, ks=(1, 2)):
        return [_row(m, k, SOL, SOL) for k in ks for m in ("m1", "m2", "m3")]

    def test_clean_rows_pass(self) -> None:
        rep = ls.analyze(self._rows(), self.SC, 0.8, self.THR, self.DAYS)
        self.assertFalse(rep["by_k"]["2"]["censored_vs_smallest_k"])

    def test_row_count_must_equal_n_oof_at_every_k(self) -> None:
        rows = [r for r in self._rows() if not (r["entry_land_k"] == 2 and r["mint"] == "m3")]
        with self.assertRaisesRegex(SystemExit, "k=2 has 2 rows"):
            ls.analyze(rows, self.SC, 0.8, self.THR, self.DAYS)

    def test_selected_count_must_equal_threshold_file(self) -> None:
        with self.assertRaisesRegex(SystemExit, "selected at k=1"):
            ls.analyze(self._rows(), self.SC, 0.8, {"n_oof": 3, "n_selected_at_or_above_threshold": 881}, self.DAYS)

    def test_repeated_mint_within_a_k_is_refused(self) -> None:
        rows = self._rows()
        rows[1] = dict(rows[0])  # m1 twice at k=1, count still 3
        with self.assertRaisesRegex(SystemExit, "repeats at k=1"):
            ls.analyze(rows, self.SC, 0.8, self.THR, self.DAYS)

    def test_day_must_match_the_oof_day(self) -> None:
        rows = self._rows()
        rows[0]["day"] = "2026-09-21"
        with self.assertRaisesRegex(SystemExit, "OOF day"):
            ls.analyze(rows, self.SC, 0.8, self.THR, self.DAYS)

    def test_censoring_is_flagged_and_shown(self) -> None:
        rows = [r for r in self._rows() if not (r["entry_land_k"] == 2 and r["mint"] == "m3")]
        rep = ls.analyze(rows, self.SC, 0.8)  # no thr_doc: no hard assert, flag only
        self.assertTrue(rep["by_k"]["2"]["censored_vs_smallest_k"])
        self.assertFalse(rep["by_k"]["1"]["censored_vs_smallest_k"])
        self.assertIn("CENSORED", ls.render_md(rep))
        self.assertIn("n_rows", ls.render_md(rep))


class PressureLegTests(unittest.TestCase):
    def test_pressure_leg_equals_the_forward_scorers_swap_handling(self) -> None:
        from tools.exp011_score import compute_gate
        from tools.exp012_forward import _swap

        rows = [_row(f"m{i}", 1, (i % 5 - 1) * 0.05 * SOL, (i % 7 - 3) * 0.04 * SOL, day=f"2026-09-{19 + i % 3}") for i in range(40)]
        side = ls._side(rows)["press"]
        g = compute_gate(_swap(rows))
        self.assertEqual(side["mean_sol"], g["mean_sol"])
        self.assertEqual(side["ci90_sol"], g["mean_ci90_sol"])
        self.assertEqual(side["ex_top3_sol"], g["total_ex_top3_sol"])
        self.assertEqual(side["total_sol"], g["total_sol"])


class GuardTests(unittest.TestCase):
    def _args(self, **kw):
        import argparse

        base = dict(fast_dir=None, oracle_insample_dir=None, oracle_live_dir=None, verify_view=True)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_missing_roots_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            for kw in ({}, {"fast_dir": Path(td)}, {"fast_dir": Path(td), "oracle_insample_dir": Path(td)}):
                with self.subTest(kw), self.assertRaisesRegex(SystemExit, "all required"):
                    ls.guarded_roots(self._args(**kw))

    def test_verify_view_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(SystemExit, "verify-view"):
            ls.guarded_roots(self._args(fast_dir=Path(td), oracle_insample_dir=Path(td), oracle_live_dir=Path(td), verify_view=False))

    def test_forbidden_roots_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ok = Path(td)
            for bad in ("/data/mal/blocks/fresh-0903/w1", "/data/mal/blocks-clean/fresh-0903", "/data/mal/clean-view/fresh-0903", "/var/lib/mal/backfill-fast-b"):
                for slot in ("fast_dir", "oracle_insample_dir", "oracle_live_dir"):
                    kw = {"fast_dir": ok, "oracle_insample_dir": ok, "oracle_live_dir": ok, slot: Path(bad)}
                    with self.subTest(bad=bad, slot=slot), self.assertRaisesRegex(SystemExit, "reserved holdout"):
                        ls.guarded_roots(self._args(**kw))

    def test_symlink_into_a_forbidden_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            link = Path(td) / "innocent"
            link.symlink_to("/data/mal/blocks-clean")
            ok = Path(td)
            with self.assertRaisesRegex(SystemExit, "reserved holdout"):
                ls.guarded_roots(self._args(fast_dir=link, oracle_insample_dir=ok, oracle_live_dir=ok))


class StoredArtifactTests(unittest.TestCase):
    def test_committed_oof_scores_load_and_match_threshold(self) -> None:
        scores, thr, thr_doc, days = ls.load_oof(ls.DEFAULT_ARTIFACT_DIR)
        self.assertEqual(len(scores), thr_doc["n_oof"])
        self.assertEqual(sum(1 for s in scores.values() if s >= thr), thr_doc["n_selected_at_or_above_threshold"])

    def test_missing_oof_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td, self.assertRaises(SystemExit):
            ls.load_oof(Path(td))


class EndToEndFixtureTests(unittest.TestCase):
    def test_main_on_fixture_roots_writes_report_with_every_k(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            fast, ins, live = td_p / "fast", td_p / "ins", td_p / "live"
            write_root_with_midprint(fast)
            write_fast_format_root(ins, ia.POOL_C_HOURS, {})
            for h in la.POOL_B_HOURS:
                write_zst_jsonl(live / "trades" / f"trades-{h}.jsonl.zst", [])
            (live / "creates").mkdir(parents=True, exist_ok=True)
            for d in la.POOL_B_CREATE_DAYS:
                (live / "creates" / f"observe-{d}.jsonl").write_text("")
            art = td_p / "art"
            art.mkdir()
            (art / "oof_scores.json").write_text(json.dumps({"rows": [{"day": ex.POOL_HOURS[10][:10], "mint": "mintL1", "score": 0.9, "label": 1, "filled": True}]}))
            (art / "threshold.json").write_text(json.dumps({"threshold": 0.8, "n_oof": 1, "n_selected_at_or_above_threshold": 1}))
            out = td_p / "out"
            from unittest import mock

            import tools.exp011_freeze as fz

            argv = [
                "--out-dir", str(out), "--artifact-dir", str(art), "--ks", "1,4,12", "--verify-view",
                "--fast-dir", str(fast), "--oracle-insample-dir", str(ins), "--oracle-live-dir", str(live),
            ]
            with mock.patch.object(fz, "verify_view_sha256", return_value=1), mock.patch.object(fz, "check_view_pin", return_value="x"):
                self.assertEqual(ls.main(argv), 0)
            rep = json.loads((out / "latency_sensitivity.json").read_text())
            self.assertEqual(sorted(rep["by_k"]), ["1", "12", "4"])
            for k in ("1", "4", "12"):
                self.assertEqual(rep["by_k"][k]["entered"]["n"], 1)
            self.assertEqual(rep["checks"]["n_selected_at_smallest_k"], 1)
            self.assertTrue((out / "latency_sensitivity.md").is_file())
            # the heavy pass is skipped on --reuse-rows and gives the same report
            with mock.patch.object(ls, "collect_rows", side_effect=AssertionError("must not re-run the tape pass")):
                self.assertEqual(ls.main(["--out-dir", str(out), "--artifact-dir", str(art), "--ks", "1,4,12", "--reuse-rows"]), 0)
            rep2 = json.loads((out / "latency_sensitivity.json").read_text())
            self.assertEqual(rep["by_k"], rep2["by_k"])


if __name__ == "__main__":
    unittest.main()
