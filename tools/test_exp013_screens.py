"""EXP-013 derivation screens (DEC-017 section 5): period transfer, source screens, EXP-012 overlap. Fixtures only."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tools.exp011_freeze as fz
import tools.exp013_screens as sc
from tools.exp012_fixtures import synthetic_table_rows, write_table_fixture
from tools.exp013_fixtures import synthetic_extra_rows

AUG = ("2026-08-25", "2026-08-26", "2026-08-27")


class ScreensTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._td.name)
        cls.rows = sorted(synthetic_table_rows() + synthetic_extra_rows(AUG), key=lambda r: (r["day"], r["mint"], r["spec"]))
        cls.days = tuple(sorted({r["day"] for r in cls.rows}))
        cls.oof = fz.leave_one_day_out_oof(cls.rows, cls.days)
        cls.thr = fz.compute_threshold(cls.oof)["threshold"]
        t9 = cls.tmp / "t9" / "table.jsonl"
        write_table_fixture(t9, synthetic_table_rows())
        m, t, o, man, w, _n = fz.freeze(table_path=t9, run_nested_lodo=False)
        cls.e12 = cls.tmp / "e12"
        fz.write_outputs(cls.e12, m, t, o, man, w)
        cls.e12_thr = t["threshold"]
        cls.e12_oof = o

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def test_period_and_source_labels(self) -> None:
        self.assertEqual(sc.period_of("2026-08-31"), "august")
        self.assertEqual(sc.period_of("2026-09-01"), "september")
        self.assertIsNone(sc.period_of("2026-10-01"))
        self.assertEqual([sc.source_of({"pool": p}) for p in "ACBX"], ["fast", "oracle", "oracle", "getblock"])

    def test_period_transfer_threshold_is_cut_on_the_training_period_only(self) -> None:
        pt = sc.period_transfer(self.rows)
        aug = [r for r in self.rows if r["day"] in AUG]
        sep = [r for r in self.rows if r["day"] not in AUG]
        for name, train, test in (("august_to_september", aug, sep), ("september_to_august", sep, aug)):
            with self.subTest(name):
                t = pt[name]
                self.assertEqual(t["status"], "ok")
                days = sorted({r["day"] for r in train})
                want = fz.compute_threshold(fz.leave_one_day_out_oof(train, days))["threshold"]
                self.assertEqual(t["train_threshold"], want)
                self.assertEqual(t["train_days"], days)
                self.assertEqual(t["test_days"], sorted({r["day"] for r in test}))
                self.assertEqual(t["test"]["n"], len(test))
                self.assertEqual(t["test"]["selected_fraction"], t["test"]["n_selected"] / len(test))
                for leg in ("flat", "press"):
                    self.assertEqual(set(t["test"][leg]), {"mean_sol", "mean_ci90_sol", "total_sol", "ex_top3_sol"})

    def test_period_transfer_scores_the_test_period_with_the_model_fitted_on_training_only(self) -> None:
        aug = [r for r in self.rows if r["day"] in AUG]
        sep = [r for r in self.rows if r["day"] not in AUG]
        days = sorted({r["day"] for r in aug})
        thr = fz.compute_threshold(fz.leave_one_day_out_oof(aug, days))["threshold"]
        scores = fz._predict(fz.fit_frozen_model(aug), [fz._vector(r["features"]) for r in sep])
        want = [r for r, s in zip(sep, scores) if s >= thr]
        got = sc.transfer_one(aug, sep)["test"]
        self.assertEqual(got["n_selected"], len(want))
        self.assertAlmostEqual(got["flat"]["total_sol"], sum(r["flat"] for r in want) / 1e9)

    def test_a_period_with_one_day_is_reported_not_computable(self) -> None:
        one = [r for r in self.rows if r["day"] == "2026-08-26"]
        sep = [r for r in self.rows if r["day"] not in AUG]
        self.assertEqual(sc.transfer_one(one, sep)["status"], "not_computable")
        self.assertEqual(sc.transfer_one(sep, [])["status"], "not_computable")

    def test_source_screens(self) -> None:
        s = sc.source_screens(self.rows)
        self.assertEqual(set(s) - {"note"}, {"september_only", "fast_only", "oracle_only", "getblock_only"})
        sep_days = sorted({r["day"] for r in self.rows if r["day"] not in AUG})
        self.assertEqual(s["september_only"]["days"], sep_days)
        fast_days = sorted({r["day"] for r in self.rows if r["pool"] == "A"})
        self.assertEqual(s["fast_only"]["days"], fast_days)
        self.assertEqual(s["fast_only"]["status"], "ok")
        self.assertEqual(s["fast_only"]["cohort"]["n"], sum(1 for r in self.rows if r["pool"] == "A"))
        self.assertEqual(s["getblock_only"]["days"], list(AUG))
        # a source with no rows is reported, not silently dropped
        none = sc.source_screens([r for r in self.rows if r["pool"] != "X"])
        self.assertEqual(none["getblock_only"]["status"], "not_computable")

    def test_overlap_uses_exp012_oof_on_its_days_and_the_frozen_model_elsewhere(self) -> None:
        o = sc.overlap_with_exp012(self.rows, self.oof, self.thr, self.e12)
        e12_oof = {(r["day"], r["mint"]): r["score"] for r in self.e12_oof}
        cand = {(x["day"], x["mint"]) for x in self.oof if x["score"] >= self.thr}
        sep = {k for k in cand if k[0] not in AUG}
        e12_sep = {k for k, v in e12_oof.items() if v >= self.e12_thr}
        sp = o["by_period"]["september"]
        self.assertEqual(sp["n_candidate_selected"], len(sep))
        self.assertEqual(sp["n_both"], len(sep & e12_sep))
        self.assertEqual(sp["n_exp012_selected"], len(e12_sep))
        self.assertAlmostEqual(sp["share_of_candidate_also_in_exp012"], len(sep & e12_sep) / len(sep))
        # August rows are scored by EXP-012's frozen model, not by an OOF
        ag = o["by_period"]["august"]
        self.assertEqual(ag["n_scored_by_both"], sum(1 for r in self.rows if r["day"] in AUG))
        self.assertEqual(ag["n_candidate_selected_without_exp012_score"], 0)
        self.assertEqual(o["all"]["n_candidate_selected"], len(cand))
        self.assertEqual(set(o["exp012_dir_files_sha256"]), set(sc.E12_FILES))

    def test_the_exp012_read_directory_is_refused(self) -> None:
        bad = self.tmp / "ARTIFACTS" / "exp012" / "read"
        bad.mkdir(parents=True)
        with self.assertRaises(SystemExit):
            sc.overlap_with_exp012(self.rows, self.oof, self.thr, bad)

    def test_run_screens_and_markdown(self) -> None:
        doc = sc.run_screens(self.rows, self.oof, self.thr, self.e12)
        self.assertEqual(set(doc) - {"schema", "note", "bootstrap"}, {"period_transfer", "source_screens", "overlap_with_exp012"})
        md = sc.screens_markdown(doc)
        for needle in ("august_to_september", "september_to_august", "fast_only", "Overlap with EXP-012"):
            self.assertIn(needle, md)


if __name__ == "__main__":
    unittest.main()
