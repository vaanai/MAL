from __future__ import annotations

import unittest

from tools.forward_exp012_replay import compare


def _r(mint, score, entered, mig=1000, feats=None, reason=None):
    row = {"mint": mint, "score": score, "entered": entered, "mig_ms": mig, "reason": reason}
    if feats is not None:
        row["features"] = feats
    return row


class CompareTests(unittest.TestCase):
    def test_overlap_scores_and_features(self) -> None:
        runner = [
            _r("a", 0.90, True, feats={"x": 1.0, "y": 2.0}),
            _r("b", 0.50, False, reason="below_threshold", feats={"x": 1.0, "y": 2.0}),
            _r("c", 0.95, True),
            _r("r_only", 0.1, False, reason="no_features"),
        ]
        scorer = [
            _r("a", 0.9000001, True, mig=1002, feats={"x": 1.0, "y": 2.5}),
            _r("a", 0.9000001, True, mig=1002),  # second exit spec, same mint: ignored
            _r("b", 0.85, True, feats={"x": 1.0, "y": 2.0}),
            _r("c", 0.95, True),
            _r("s_only", 0.99, True),
        ]
        rep = compare(runner, scorer)
        self.assertEqual(rep["n_both"], 3)
        self.assertEqual(rep["only_runner_mints"], ["r_only"])
        self.assertEqual(rep["only_scorer_mints"], ["s_only"])
        self.assertEqual(rep["entered_both"], 2)
        self.assertEqual(rep["entered_only_scorer"], ["b", "s_only"])
        self.assertEqual(rep["entered_only_runner"], [])
        self.assertAlmostEqual(rep["entered_jaccard"], 2 / 4)
        self.assertAlmostEqual(rep["score_max_abs_delta"], 0.35)
        self.assertEqual(rep["mig_ms_max_abs_delta"], 2)
        self.assertEqual(rep["feature_n_mints_compared"], 2)
        self.assertEqual(rep["feature_mismatch_counts"], {"y": 1})
        self.assertEqual(rep["runner_skip_reasons"], {"below_threshold": 1, "no_features": 1})

    def test_empty(self) -> None:
        rep = compare([], [])
        self.assertIsNone(rep["entered_jaccard"])
        self.assertIsNone(rep["score_max_abs_delta"])


if __name__ == "__main__":
    unittest.main()
