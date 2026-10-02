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


class StoredArtifactTests(unittest.TestCase):
    def test_committed_oof_scores_load_and_match_threshold(self) -> None:
        scores, thr, thr_doc = ls.load_oof(ls.DEFAULT_ARTIFACT_DIR)
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
            (art / "oof_scores.json").write_text(json.dumps({"rows": [{"day": "x", "mint": "mintL1", "score": 0.9, "label": 1, "filled": True}]}))
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
