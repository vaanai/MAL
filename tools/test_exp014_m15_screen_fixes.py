"""Tests for the quant-proof / reviewer fixes to tools/exp014_m15_screen.py (E1-E4, 6c coverage flags, real-root cutoff,
crashed ledger event). Synthetic fixtures only."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

import tools.exp014_m15_model as mm
import tools.exp014_m15_screen as sc
from tools.test_exp014_m15_screen import A_DAYS, AUG_DAYS, C_DAYS, E12, Fixture, day_ms, manifest_for, pool_runs, synth_rows, trade


class ItemFixTests(unittest.TestCase):
    def test_6b_is_gated_on_the_oof_migration_days_and_reports_any_day(self) -> None:
        b = {"in-b"}
        on = trade("2026-09-19", 0.05, mint="o1", pool="A")
        off = trade("2026-09-28", -0.50, mint="o2", pool="A", mig_day="2026-09-28")  # outside the 9 days, big loser
        r = sc.item_6b([on, off], b)
        self.assertTrue(r["pass"])  # the off-day loser does not gate
        self.assertEqual(r["n_outside_b"], 1)
        self.assertEqual(r["any_day_report_only"]["n"], 2)
        self.assertLess(r["any_day_report_only"]["legs"]["flat"]["mean_sol"], 0)
        self.assertFalse(sc.item_6b([off], b)["pass"])  # nothing gates: fails
        edge = trade("2026-09-19", 0.05, mint="o3", pool="A", mig_day="2026-09-18")  # T on an OOF day, migrated before the window
        self.assertFalse(sc.item_6b([edge], b)["pass"])

    def test_6a_reports_the_extra_jaccard_figures(self) -> None:
        a = [trade("2026-09-19", 0.01, mint=m, i=i, pool="A") for i, m in enumerate(["a1", "a2", "a3", "a4"])]
        b, unres = {"a1", "a2", "z1"}, {"a1", "a2", "z1", "z2", "z3"}
        r = sc.item_6a(a, b, unres, eligible_k4_mints={"a1", "z2", "a2"})
        self.assertAlmostEqual(r["jaccard"], 2 / 5)
        self.assertAlmostEqual(r["jaccard_b_eligible_d4_report_only"], 2 / 5)  # B' = {a1, a2, z2}: both 2, union 5
        self.assertAlmostEqual(r["jaccard_unrestricted_b_report_only"], 2 / 7)
        self.assertAlmostEqual(r["overlap_min_report_only"], 2 / 3)
        self.assertIsNone(sc.item_6a(a, b, unres)["jaccard_b_eligible_d4_report_only"])
        self.assertIsNone(sc.item_6a([], set(), set())["overlap_min_report_only"])

    def test_6c_flags_uncovered_days(self) -> None:
        tm = manifest_for()
        tm["pool_end_ms_by_pool"] = {"C": day_ms("2026-09-25", 6 * 3600_000 + 58 * 60_000), "A": day_ms("2026-09-22"), "X": day_ms("2026-08-16")}
        u = sc.uncovered_days(tm, E12)
        self.assertEqual(u["none"], ["2026-09-26", "2026-09-27"])
        self.assertEqual(u["partial"], ["2026-09-25"])
        c = sc.item_6c([], {leg: {} for leg in sc.LEGS}, tm=tm)
        self.assertEqual(c["uncovered_days"], u)


class CensoredReportTests(unittest.TestCase):
    def test_counts_by_reason_and_would_keep_under_the_014_rule(self) -> None:
        tm = manifest_for()
        tm["pool_end_ms_by_pool"] = {"A": day_ms("2026-09-22"), "C": day_ms("2026-09-25", 6 * 3600_000 + 58 * 60_000), "X": day_ms("2026-08-16")}
        rows = [{"mint": "ma", "pool": "A"}, {"mint": "mc", "pool": "C"}, {"mint": "mx", "pool": "X"}]
        t_far = day_ms("2026-09-20", 3600_000)
        edge = day_ms("2026-09-22") - 1_800_000 - 2 * 4 * 400 - 60_000
        cens = [
            {"mint": "ma", "entry_land_k": 4, "trigger_ms": t_far, "reason": "tape_end"},
            {"mint": "ma", "entry_land_k": 4, "trigger_ms": edge + 1, "reason": "touches_gap"},  # excluded at d=4
            {"mint": "ma", "entry_land_k": 4, "trigger_ms": edge - 1, "reason": "touches_gap"},  # just kept
            {"mint": "mc", "entry_land_k": 8, "trigger_ms": day_ms("2026-09-23", 3600_000), "reason": "tape_end"},
            {"mint": "ghost", "entry_land_k": 8, "trigger_ms": t_far, "reason": "tape_end"},  # no row: unknown pool
            {"mint": "ma", "entry_land_k": 1, "trigger_ms": t_far, "reason": "tape_end"},  # d=1 not reported
        ]
        r = sc.censored_report(cens, rows, tm)
        self.assertEqual(r["by_d"]["4"], {"n_censored": 3, "by_reason": {"tape_end": 1, "touches_gap": 2}, "n_would_be_kept_by_exclusion_rule": 2, "n_unknown_pool": 0})
        self.assertEqual(r["by_d"]["8"], {"n_censored": 2, "by_reason": {"tape_end": 2}, "n_would_be_kept_by_exclusion_rule": 1, "n_unknown_pool": 1})
        self.assertEqual(sc.censored_report(None, rows, tm), {"available": False})

    def test_gap_start_after_T_excludes(self) -> None:
        tm = {"pool_runs": {"X": [["2026-08-10T00", "2026-08-10T05"], ["2026-08-10T10", "2026-08-10T23"]]}}
        t = day_ms("2026-08-10", 6 * 3600_000 - 600_000)  # shortly before the missing hours start at 06:00
        r = sc.censored_report([{"mint": "m", "entry_land_k": 4, "trigger_ms": t, "reason": "touches_gap"}], [{"mint": "m", "pool": "X"}], tm)
        self.assertEqual(r["by_d"]["4"]["n_would_be_kept_by_exclusion_rule"], 0)

    def test_end_to_end_screen_json_carries_the_censored_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td)
            cens = [{"mint": "m0-001", "day": "2026-08-10", "entry_land_k": 4, "trigger_ms": day_ms("2026-08-10", 3600_000), "reason": "tape_end"}]
            (fx.table / "censored.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cens))
            doc = fx.run()
            self.assertEqual(doc["table_censored"]["by_d"]["4"]["n_censored"], 1)
            self.assertEqual(json.loads((fx.out / "screen.json").read_text())["table_censored"]["by_d"]["4"]["by_reason"], {"tape_end": 1})


class WriteOrderTests(unittest.TestCase):
    def test_screen_files_are_on_disk_before_result_bookkeeping(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td)
            seen = {}
            real_append = sc.mal_result.append_try

            def spy(path, *a, **k):
                if k["config"].get("event") == "result":
                    seen["on_disk"] = (fx.out / "screen.json").is_file() and (fx.out / "screen.md").is_file()
                return real_append(path, *a, **k)

            with mock.patch.object(sc.mal_result, "append_try", side_effect=spy):
                doc = fx.run()
            self.assertTrue(seen["on_disk"])
            sj = json.loads((fx.out / "screen.json").read_text())
            self.assertEqual(sj["verdict"], doc["verdict"])
            self.assertEqual(len(sj["selected"]), doc["n_selected"])
            self.assertIn("fold_info", sj)

    def test_crash_after_the_fit_keeps_the_out_dir_and_records_crashed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td)
            with mock.patch.object(sc, "evaluate", side_effect=RuntimeError("eval boom")):
                with self.assertRaises(RuntimeError):
                    fx.run()
            self.assertTrue(fx.out.is_dir())
            lines = [json.loads(x) for x in sc.ledger_path(fx.ledger).read_text().splitlines()]
            self.assertEqual([x["event"] for x in lines], ["started", "crashed"])
            self.assertIn("eval boom", lines[-1]["error"])
            self.assertEqual([json.loads(x)["config"]["event"] for x in fx.tries.read_text().splitlines()], ["started"])

    def test_crash_writing_screen_files_after_the_fit_records_crashed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td)
            with mock.patch.object(sc, "to_markdown", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    fx.run()
            self.assertTrue((fx.out / "screen.json").is_file())  # json is written before the markdown
            self.assertEqual([json.loads(x)["event"] for x in sc.ledger_path(fx.ledger).read_text().splitlines()], ["started", "crashed"])

    def test_result_v1_failure_leaves_screen_files_and_an_error_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td)
            with mock.patch.object(sc.mal_result, "build_result", side_effect=ValueError("v1 boom")):
                doc = fx.run()
            self.assertIn(doc["verdict"], ("PASS", "FAIL"))
            self.assertTrue((fx.out / "screen.json").is_file())
            self.assertFalse((fx.out / "result.json").exists())
            self.assertIn("v1 boom", (fx.out / "result_error.txt").read_text())
            events = [json.loads(x)["event"] for x in sc.ledger_path(fx.ledger).read_text().splitlines()]
            self.assertEqual(events, ["started", "finished", "result_v1_error"])

    def test_not_decidable_writes_screen_json_and_points_the_result_line_at_it(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:5] + A_DAYS + C_DAYS), runs=pool_runs("2026-08-14T23"))
            fx.run()
            tries = [json.loads(x) for x in fx.tries.read_text().splitlines()]
            self.assertTrue(tries[-1]["result_path"].endswith("screen.json"))
            self.assertTrue((fx.out / "screen.json").is_file())
            self.assertFalse((fx.out / "result.json").exists())


class RealRootTests(unittest.TestCase):
    def test_a_manifest_naming_real_view_roots_obeys_the_cutoff_wherever_the_table_is(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1], per_day=2))
            m = json.loads((fx.table / "manifest.json").read_text())
            m["roots"]["fast"] = "/data/mal/clean-view/fast-pool-x"
            (fx.table / "manifest.json").write_text(json.dumps(m))
            self.assertTrue(mm.manifest_names_real_roots(m))
            before = mm.REAL_DATA_CUTOFF - timedelta(seconds=1)
            with self.assertRaises(SystemExit) as cm:
                mm.load_table(fx.table, now=before)
            self.assertIn("real", str(cm.exception))
            with self.assertRaises(SystemExit):
                fx.run(now=before)
            self.assertFalse(fx.tries.exists())
            mm.load_table(fx.table, now=mm.REAL_DATA_CUTOFF)
            with self.assertRaises(SystemExit) as cm2:  # after the cutoff it still needs the default ledger dir
                fx.run(now=mm.REAL_DATA_CUTOFF)
            self.assertIn("default ledger dir", str(cm2.exception))
            self.assertFalse(fx.tries.exists())

    def test_fixture_roots_are_not_real(self) -> None:
        self.assertFalse(mm.manifest_names_real_roots({"roots": {"fast": "/tmp/x"}, "extra_views": [{"root": "/home/y"}]}))
        self.assertTrue(mm.manifest_names_real_roots({"roots": {}, "extra_views": [{"root": "/data/mal/clean-view/explore-0814/w1"}]}))


if __name__ == "__main__":
    unittest.main()
