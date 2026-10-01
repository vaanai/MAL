"""EXP-012: the proceed screen and the result.v1 records (schema test).

Synthetic fixtures only. The freeze's record is role "exploration"; the
scorer's is "confirmation-oneshot". Both are validated against
schemas/result.v1.schema.json by tools.mal_result.validate_result.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp012_score as s12
import tools.exp012_support as sup
from tools import mal_result
from tools.exp012_fixtures import synthetic_table_rows, write_table_fixture
from tools.test_exp012_score import Base, argv_for


def _nested(*, flat_mean=1.0, flat_ex3=1.0, flat_days=9, press_mean=1.0, press_ex3=1.0, press_days=9, n_days=9) -> dict:
    return {
        "n_days_total": n_days,
        "n_days_flat_positive": flat_days,
        "n_days_press_positive": press_days,
        "flat": {"mean_pct": flat_mean, "ex_top3_sol": flat_ex3},
        "press": {"mean_pct": press_mean, "ex_top3_sol": press_ex3},
    }


class ProceedScreenTests(unittest.TestCase):
    def test_passes_when_both_models_clear_all_three_legs(self) -> None:
        s = sup.proceed_screen(_nested())
        self.assertTrue(s["proceed"])
        self.assertTrue(s["flat_ok"] and s["press_ok"])

    def test_each_leg_can_fail_under_each_model(self) -> None:
        cases = {
            "flat mean": _nested(flat_mean=-0.1),
            "flat ex-top-3": _nested(flat_ex3=-0.01),
            "flat days": _nested(flat_days=4),  # 4 of 9 is not "more than 4 of 9"
            "press mean": _nested(press_mean=0.0),
            "press ex-top-3": _nested(press_ex3=0.0),
            "press days": _nested(press_days=4),
        }
        for name, nested in cases.items():
            with self.subTest(name):
                self.assertFalse(sup.proceed_screen(nested)["proceed"])

    def test_five_of_nine_days_is_enough(self) -> None:
        self.assertTrue(sup.proceed_screen(_nested(flat_days=5, press_days=5))["proceed"])

    def test_missing_values_do_not_pass(self) -> None:
        self.assertFalse(sup.proceed_screen(_nested(flat_mean=None))["proceed"])


class FreezeResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._td.name)
        cls.table = cls.tmp / "t" / "table.jsonl"
        write_table_fixture(cls.table, synthetic_table_rows())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def test_freeze_writes_a_valid_exploration_record_and_the_screen(self) -> None:
        out = self.tmp / "out"
        res = self.tmp / "r" / "result.json"
        log = self.tmp / "tries.jsonl"
        for expected_variant in (1, 2):
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                with mock.patch.object(fz, "_git_state", return_value=("c" * 40, False)):
                    (out / fz.FROZEN_MANIFEST_NAME).unlink(missing_ok=True)  # a same-dir re-run is otherwise refused (binding first run)
                    fz.main(["--table", str(self.table), "--out-dir", str(out), "--frozen-manifest", "--expect-commit", "c" * 40, "--result-out", str(res), "--tries-log", str(log)])
            doc = json.loads(res.read_text())
            self.assertEqual(mal_result.validate_result(doc), [])
            self.assertEqual(doc["schema_version"], "result.v1")
            self.assertEqual(doc["role"], "exploration")
            self.assertEqual(doc["stage"], "exploring")
            self.assertEqual(len(doc["data_blocks"]), 3)
            self.assertTrue(all(b["ledger_owner"] == "exploration pool" for b in doc["data_blocks"]))
            self.assertEqual(doc["tries"]["variant_n"], expected_variant)
            self.assertEqual(doc["tries"]["of_m"], expected_variant)
            self.assertEqual(set(doc["metrics"]["flat"]["by_segment"]), {"A", "B", "C"})
            self.assertGreater(doc["metrics"]["flat"]["n_trades"], 0)
        screen = json.loads((out / "proceed_screen.json").read_text())
        self.assertEqual(screen["schema"], "exp012_proceed_screen_v1")
        self.assertIn("proceed", screen)
        listed = fz.parse_md5_manifest(out / fz.FROZEN_MANIFEST_NAME)
        self.assertIn("proceed_screen.json", listed)
        self.assertEqual(listed["proceed_screen.json"], fz._md5_of_file(out / "proceed_screen.json"))

    def test_result_out_with_skip_nested_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            fz.main(["--table", str(self.table), "--out-dir", str(self.tmp / "o2"), "--skip-nested-lodo", "--result-out", str(self.tmp / "x.json")])


class ScorerResultTests(Base):
    def test_scorer_writes_a_valid_confirmation_oneshot_record(self) -> None:
        root = self.fresh()
        res = root / "res" / "result.json"
        rc, _, _ = self.run_main(root, "--result-out", str(res), "--tries-log", str(root / "tries.jsonl"))
        self.assertEqual(rc, 0)
        doc = json.loads(res.read_text())
        self.assertEqual(mal_result.validate_result(doc), [])
        self.assertEqual(doc["role"], "confirmation-oneshot")
        self.assertEqual(doc["stage"], "failed")  # 3 trades: min_n fails, same as the report
        self.assertFalse(doc["gate"]["pass_both"])
        self.assertEqual(doc["data_blocks"], sup.holdout_block())
        self.assertEqual(doc["data_blocks"][0]["ledger_owner"], "EXP-012")
        self.assertEqual(doc["metrics"]["flat"]["n_trades"], 3)
        self.assertEqual(doc["tries"]["variant_n"], 1)
        report = json.loads((root / "out" / "holdout_report.json").read_text())
        self.assertEqual(report["verdict"], "FAIL")

    def test_no_result_flag_writes_no_record(self) -> None:
        root = self.fresh()
        self.assertEqual(self.run_main(root)[0], 0)
        self.assertFalse(list(root.rglob("result.json")))

    def test_a_result_failure_leaves_the_verdict_in_place(self) -> None:
        root = self.fresh()
        with mock.patch("tools.exp012_support.write_scorer_result", side_effect=ValueError("schema")):
            rc, _, _ = self.run_main(root, "--result-out", str(root / "res.json"))
        self.assertEqual(rc, 4)
        self.assertTrue((root / "out" / "holdout_report.json").exists())

    def test_empty_entered_set_still_validates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = {"threshold": 0.9, "verdict": "FAIL", "gate": {"promote": False}, "code_commit": "abc"}
            doc = sup.write_scorer_result(Path(td) / "r.json", [], [], report, command="x", runtime_s=1.0, tries_log=Path(td) / "t.jsonl")
            self.assertEqual(mal_result.validate_result(doc), [])
            self.assertEqual(doc["metrics"]["flat"]["n_trades"], 0)


if __name__ == "__main__":
    unittest.main()
