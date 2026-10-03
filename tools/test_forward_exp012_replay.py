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


def _pop(n_days=5, per_day=40, delta=0.0, runner_entered=True, base=1_000_000):
    """Synthetic population: n_days * per_day mints, all entered by the scorer."""
    sco, run = [], []
    i = 0
    for d in range(n_days):
        for _ in range(per_day):
            m = f"m{i}"
            sco.append(_s(m, 0.9, True, mig=base + i, day=f"2026-10-{6 + d:02d}"))
            run.append(_r(m, 0.9 + delta, runner_entered, mig=base + i))
            i += 1
    return run, sco


class Amendment3BTests(unittest.TestCase):
    def test_all_pass(self) -> None:
        run, sco = _pop()
        a = compare(run, sco)["amendment3_b"]
        self.assertEqual(a["n_entered_by_both"], 200)
        self.assertEqual(a["n_utc_days_entered_by_both"], 5)
        self.assertEqual(a["row0_coverage"], 1.0)
        self.assertEqual(a["row1_jaccard_both_seen"], 1.0)
        self.assertTrue(a["row0_pass"] and a["row1_pass"] and a["row2_pass"])
        self.assertEqual(a["verdict"], "PASS")

    def test_not_decidable_thresholds(self) -> None:
        run, sco = _pop(per_day=39)  # 195 entered by both
        self.assertEqual(compare(run, sco)["amendment3_b"]["verdict"], "NOT_DECIDABLE")
        run, sco = _pop(n_days=4, per_day=60)  # 240 but 4 days
        a = compare(run, sco)["amendment3_b"]
        self.assertEqual(a["n_entered_by_both"], 240)
        self.assertEqual(a["verdict"], "NOT_DECIDABLE")
        self.assertFalse(a["decidable"])
        self.assertEqual(compare([], [])["amendment3_b"]["verdict"], "NOT_DECIDABLE")

    def test_coverage_row_counts_missing_and_stale(self) -> None:
        run, sco = _pop()
        stale = [r["mint"] for r in run[:10]]  # 5% of 200
        a = compare(run, sco, stale_mints=stale)["amendment3_b"]
        self.assertAlmostEqual(a["row0_coverage"], 0.95)
        self.assertTrue(a["row0_pass"])
        a = compare(run, sco, stale_mints=[r["mint"] for r in run[:11]])["amendment3_b"]
        self.assertAlmostEqual(a["row0_coverage"], 189 / 200)
        self.assertFalse(a["row0_pass"])
        self.assertEqual(a["verdict"], "FAIL")
        a = compare(run[10:], sco)["amendment3_b"]  # 10 mints with no runner gate row
        self.assertAlmostEqual(a["row0_coverage"], 0.95)

    def test_jaccard_on_both_seen_only(self) -> None:
        run, sco = _pop()
        # mints the runner never saw are not in the Jaccard (they hit row 0 instead)
        a = compare(run[:190], sco)["amendment3_b"]
        self.assertEqual(a["n_both_seen"], 190)
        self.assertEqual(a["row1_jaccard_both_seen"], 1.0)
        # runner skips 25 of 200 both-seen: 175/200 = 0.875 < 0.90
        run, sco = _pop()
        for r in run[:25]:
            r["entered"] = False
        a = compare(run, sco)["amendment3_b"]
        self.assertAlmostEqual(a["row1_jaccard_both_seen"], 175 / 200)
        self.assertFalse(a["row1_pass"])
        # the runner entering extra mints the scorer did not also counts in the union
        run, sco = _pop()
        for s_ in sco[:10]:
            s_["entered"] = False
        a = compare(run, sco)["amendment3_b"]
        self.assertAlmostEqual(a["row1_jaccard_both_seen"], 190 / 200)

    def test_score_delta_p95_p99_and_max_not_gated(self) -> None:
        run, sco = _pop()
        # 100 mints: the delta ladder. sorted idx round(0.95*199)=189, round(0.99*199)=197
        for i, r in enumerate(run):
            r["score"] = 0.5 + 0.0
            sco[i]["score"] = 0.5
        for r in run[:2]:  # two huge outliers: only max and p99 index 197 is untouched
            r["score"] = 0.5 + 0.9
        a = compare(run, sco)["amendment3_b"]
        self.assertAlmostEqual(a["row2_max_reported_only"], 0.9)
        self.assertEqual(a["row2_p95"], 0.0)
        self.assertEqual(a["row2_p99"], 0.0)
        self.assertTrue(a["row2_pass"])
        # 3 outliers push p99 (index 197 of 200) onto an outlier
        run[2]["score"] = 0.5 + 0.9
        a = compare(run, sco)["amendment3_b"]
        self.assertAlmostEqual(a["row2_p99"], 0.9)
        self.assertFalse(a["row2_pass"])

    def test_p95_boundary(self) -> None:
        run, sco = _pop()
        for r in run:
            r["score"] = 0.5
        for s_ in sco:
            s_["score"] = 0.5
        for r in run[:11]:  # 11 deltas of 0.03 -> sorted idx 189 is among the top 11 (189..199)
            r["score"] = 0.53
        a = compare(run, sco)["amendment3_b"]
        self.assertAlmostEqual(a["row2_p95"], 0.03)
        self.assertFalse(a["row2_pass"])
        for r in run[:10]:  # 10 outliers: idx 189 is just below them
            r["score"] = 0.53
        run[10]["score"] = 0.5
        a = compare(run, sco)["amendment3_b"]
        self.assertEqual(a["row2_p95"], 0.0)
        self.assertAlmostEqual(a["row2_p99"], 0.03)
        self.assertTrue(a["row2_pass"])  # p99 0.03 <= 0.05

    def test_downtime_and_window_exclusion(self) -> None:
        run, sco = _pop()
        # runner is down for the first 50 mig_ms ticks: those mints leave the population
        a = compare(run[50:], sco, downtime=[(1_000_000, 1_000_050)])["amendment3_b"]
        self.assertEqual(a["n_population_scorer_mints"], 150)
        self.assertEqual(a["row0_coverage"], 1.0)
        a2 = compare(run[50:], sco)["amendment3_b"]  # without the downtime file coverage falls
        self.assertAlmostEqual(a2["row0_coverage"], 150 / 200)
        # end of interval is exclusive; window is [from, to)
        a = compare(run, sco, downtime=[(1_000_000, 1_000_050)], from_ms=1_000_000, to_ms=1_000_100)["amendment3_b"]
        self.assertEqual(a["n_population_scorer_mints"], 50)

    def test_cli_downtime_file_and_latency_export(self) -> None:
        run, sco = _pop()
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "gate.jsonl").write_text("".join(json.dumps(r) + "\n" for r in run), encoding="utf-8")
            (d / "dec.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sco), encoding="utf-8")
            (d / "down.json").write_text(json.dumps([[1_000_000, 1_000_010], ["1970-01-01T00:00:00Z", 5]]), encoding="utf-8")
            (d / "lat.jsonl").write_text(json.dumps({"mint": "m100", "stale": True}) + "\n", encoding="utf-8")
            import contextlib
            import io

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main([str(d / "gate.jsonl"), str(d / "dec.jsonl"), "--downtime", str(d / "down.json"), "--latency-export", str(d / "lat.jsonl")])
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertEqual(out["amendment3_b"]["n_population_scorer_mints"], 190)
            self.assertAlmostEqual(out["amendment3_b"]["row0_coverage"], 189 / 190)
            self.assertIn("entered_jaccard", out)  # legacy keys kept


if __name__ == "__main__":
    unittest.main()
