from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import tools.exp012_forward as fw
from tools.forward_exp012_replay import ExportRefused, compare, main


def _s(mint, score, entered, mig=1000, day="2026-10-06"):
    return {"mint": mint, "mig_ms": mig, "score": score, "entered": entered, "day": day}


def _r(mint, score, entered, mig=1000, reason=None, feats=None):
    return {"mint": mint, "score": score, "entered": entered, "mig_ms": mig, "reason": reason, "features": feats}


class CompareTests(unittest.TestCase):
    def test_overlap_and_scores(self) -> None:
        runner = [
            _r("a", 0.90, True),
            _r("b", 0.50, False, reason="below_threshold"),
            _r("c", 0.95, True),
            _r("r_only", 0.1, False, reason="no_features"),
        ]
        scorer = [_s("a", 0.9000001, True, mig=1002), _s("b", 0.85, True), _s("c", 0.95, True), _s("s_only", 0.99, True)]
        rep = compare(runner, scorer)
        self.assertEqual(rep["n_both"], 3)
        self.assertEqual(rep["only_runner_mints"], ["r_only"])
        self.assertEqual(rep["only_scorer_mints"], ["s_only"])
        self.assertEqual(rep["entered_both"], 2)
        self.assertEqual(rep["entered_only_scorer"], ["b", "s_only"])
        self.assertAlmostEqual(rep["entered_jaccard"], 2 / 4)
        self.assertAlmostEqual(rep["score_max_abs_delta"], 0.35)
        self.assertEqual(rep["mig_ms_max_abs_delta"], 2)
        self.assertEqual(rep["runner_skip_reasons"], {"below_threshold": 1, "no_features": 1})
        self.assertNotIn("feature_mismatch_counts", rep)

    def test_empty(self) -> None:
        rep = compare([], [])
        self.assertIsNone(rep["entered_jaccard"])
        self.assertIsNone(rep["score_max_abs_delta"])


class AllowlistTests(unittest.TestCase):
    def test_replay_refuses_a_row_with_any_other_key(self) -> None:
        for extra in ("flat", "press", "gross", "status", "filled", "flat_sol", "spec"):
            with self.assertRaises(ExportRefused, msg=extra):
                compare([], [dict(_s("a", 0.9, True), **{extra: 1})])
        with self.assertRaises(ExportRefused):
            compare([], [{"mint": "a", "score": 0.9}])  # missing keys

    def test_replay_cli_refuses_a_rows_jsonl_shaped_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "gate.jsonl").write_text(json.dumps(_r("a", 0.9, True)) + "\n", encoding="utf-8")
            (d / "rows.jsonl").write_text(json.dumps({**_s("a", 0.9, True), "flat": 5, "press": 4, "spec": "x"}) + "\n", encoding="utf-8")
            self.assertEqual(main([str(d / "gate.jsonl"), str(d / "rows.jsonl")]), 2)

    def test_export_writes_only_allowlisted_keys_one_row_per_mint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            full = {
                "schema": fw.SCHEMA_ROW, "mint": "a", "mig_ms": 1000, "day": "2026-10-06", "spec": "s1", "score": 0.9,
                "entered": True, "filled": True, "status": 0, "gross": 5, "flat": 3.0, "press": 2.0, "flat_sol": 3e-9, "press_sol": 2e-9,
            }
            rows = [full, dict(full, spec="s2"), dict(full, mint="b", score=0.1, entered=False)]
            (out / fw.ROWS_NAME).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            self.assertEqual(fw.main(["export-decisions", "--out-dir", str(out)]), 0)
            written = [json.loads(x) for x in (out / fw.DECISIONS_NAME).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(written), 2)
            for r in written:
                self.assertEqual(set(r), set(fw.DECISION_EXPORT_KEYS))
            self.assertEqual(fw.DECISION_EXPORT_KEYS, ("mint", "mig_ms", "score", "entered", "day"))
            text = (out / fw.DECISIONS_NAME).read_text(encoding="utf-8")
            for banned in ("flat", "press", "gross", "status", "filled"):
                self.assertNotIn(banned, text)
            # and the export round-trips through the replay allowlist
            self.assertEqual(compare([], written)["n_scorer_mints"], 2)


if __name__ == "__main__":
    unittest.main()
