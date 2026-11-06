"""Tests for tools/s1t_boost_bar.py with synthetic jsonl fixtures (no real monitor data)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import s1t_boost_bar as bar  # noqa: E402


def rec(run, med, n=10, status="ok", field=True):
    r = {"run_utc": run, "status": status, "boost": {}}
    if field:
        r["boost"]["last_slice_after_migrate_s"] = {"n": n, "min": 300, "median": med, "max": 360}
    return r


class Base(unittest.TestCase):
    def run_cmd(self, rows, *args, raw=None):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "daily.jsonl")
            with open(p, "w") as f:
                f.write(raw if raw is not None else "".join(json.dumps(r) + "\n" for r in rows))
            return bar.main([args[0], *args[1:], "--daily", p])

    def b1(self, rows, reg="2026-11-09T14:00:00Z"):
        return self.run_cmd(rows, "b1", "--registration-utc", reg)

    def b2(self, rows, after="2026-11-10T00:00:00Z"):
        return self.run_cmd(rows, "b2", "--after-run-utc", after)


class B1(Base):
    def test_pass(self):
        self.assertEqual(self.b1([rec("2026-11-08T07:11:00Z", 335), rec("2026-11-09T07:11:00Z", 340)]), 0)

    def test_exactly_330_passes(self):
        self.assertEqual(self.b1([rec("2026-11-08T07:11:00Z", 330), rec("2026-11-09T07:11:00Z", 330.0)]), 0)

    def test_just_below_fails(self):
        self.assertEqual(self.b1([rec("2026-11-08T07:11:00Z", 340), rec("2026-11-09T07:11:00Z", 329.5)]), 3)

    def test_n_below_6_fails_and_6_passes(self):
        self.assertEqual(self.b1([rec("2026-11-08T07:11:00Z", 340), rec("2026-11-09T07:11:00Z", 340, n=5)]), 3)
        self.assertEqual(self.b1([rec("2026-11-08T07:11:00Z", 340, n=6), rec("2026-11-09T07:11:00Z", 340, n=6)]), 0)

    def test_missing_field_fails(self):
        self.assertEqual(self.b1([rec("2026-11-08T07:11:00Z", 340), rec("2026-11-09T07:11:00Z", 0, field=False)]), 3)

    def test_null_median_fails(self):
        self.assertEqual(self.b1([rec("2026-11-08T07:11:00Z", 340), rec("2026-11-09T07:11:00Z", None)]), 3)

    def test_partial_counts_as_record(self):
        self.assertEqual(self.b1([rec("2026-11-08T07:11:00Z", 340, status="partial"),
                                  rec("2026-11-09T07:11:00Z", 340, status="partial")]), 0)

    def test_other_status_ignored(self):
        # the stopped row is not a record, so the two latest records are the two good ones
        rows = [rec("2026-11-07T07:11:00Z", 340), rec("2026-11-08T07:11:00Z", 340),
                rec("2026-11-09T07:11:00Z", 100, status="stopped")]
        self.assertEqual(self.b1(rows), 0)

    def test_two_latest_only_and_file_order_irrelevant(self):
        rows = [rec("2026-11-09T07:11:00Z", 340), rec("2026-11-05T07:11:00Z", 319), rec("2026-11-08T07:11:00Z", 340)]
        self.assertEqual(self.b1(rows), 0)
        rows = [rec("2026-11-07T07:11:00Z", 340), rec("2026-11-08T07:11:00Z", 340), rec("2026-11-09T07:11:00Z", 319)]
        self.assertEqual(self.b1(rows), 3)

    def test_records_at_or_after_registration_ignored(self):
        rows = [rec("2026-11-08T07:11:00Z", 340), rec("2026-11-09T07:11:00Z", 340),
                rec("2026-11-09T14:00:00Z", 100), rec("2026-11-09T19:10:00Z", 100)]
        self.assertEqual(self.b1(rows), 0)

    def test_registration_before_any_record_and_single_record_fail(self):
        self.assertEqual(self.b1([rec("2026-11-08T07:11:00Z", 340)], reg="2026-11-01T00:00:00Z"), 3)
        self.assertEqual(self.b1([rec("2026-11-08T07:11:00Z", 340)]), 3)
        self.assertEqual(self.b1([]), 3)

    def test_zone_forms(self):
        rows = [rec("2026-11-08T07:11:00Z", 340), rec("2026-11-09T07:11:00Z", 340)]
        self.assertEqual(self.b1(rows, reg="2026-11-09T15:00:00+01:00"), 0)  # = 14:00Z
        self.assertEqual(self.b1(rows, reg="2026-11-09T07:11:00Z"), 3)  # the 11-09 record is not before it


class B2(Base):
    def test_no_halt_when_high(self):
        self.assertEqual(self.b2([rec("2026-11-10T07:11:00Z", 340), rec("2026-11-10T19:10:00Z", 335)]), 0)

    def test_one_low_is_ok(self):
        self.assertEqual(self.b2([rec("2026-11-10T07:11:00Z", 320), rec("2026-11-10T19:10:00Z", 335)]), 0)

    def test_two_consecutive_low_halts(self):
        self.assertEqual(self.b2([rec("2026-11-10T07:11:00Z", 329), rec("2026-11-10T19:10:00Z", 320)]), 3)

    def test_exactly_330_is_not_low(self):
        self.assertEqual(self.b2([rec("2026-11-10T07:11:00Z", 330), rec("2026-11-10T19:10:00Z", 330)]), 0)

    def test_low_high_low_resets(self):
        rows = [rec("2026-11-10T07:11:00Z", 320), rec("2026-11-10T19:10:00Z", 340), rec("2026-11-11T07:11:00Z", 320)]
        self.assertEqual(self.b2(rows), 0)

    def test_unevaluable_row_skipped_not_reset(self):
        rows = [rec("2026-11-10T07:11:00Z", 320), rec("2026-11-10T19:10:00Z", 0, field=False),
                rec("2026-11-11T07:11:00Z", 320)]
        self.assertEqual(self.b2(rows), 3)

    def test_partial_counts_other_status_ignored(self):
        rows = [rec("2026-11-10T07:11:00Z", 320, status="partial"), rec("2026-11-10T13:00:00Z", 340, status="stopped"),
                rec("2026-11-10T19:10:00Z", 320, status="partial")]
        self.assertEqual(self.b2(rows), 3)

    def test_only_records_after_instant(self):
        rows = [rec("2026-11-09T07:11:00Z", 320), rec("2026-11-09T19:10:00Z", 320), rec("2026-11-10T07:11:00Z", 340)]
        self.assertEqual(self.b2(rows), 0)
        rows = [rec("2026-11-10T00:00:00Z", 320), rec("2026-11-10T07:11:00Z", 320)]  # first is AT the instant: not after
        self.assertEqual(self.b2(rows), 0)

    def test_halt_instant_is_second_record(self):
        rows = [rec("2026-11-10T07:11:00Z", 320), rec("2026-11-10T19:10:00Z", 320), rec("2026-11-11T07:11:00Z", 320)]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "d.jsonl")
            with open(p, "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in rows))
            halt, at, _ = bar.b2(bar.load_records(p), bar.parse_utc("2026-11-10T00:00:00Z"))
        self.assertTrue(halt)
        self.assertEqual(at, "2026-11-10T19:10:00Z")

    def test_no_n_floor(self):
        self.assertEqual(self.b2([rec("2026-11-10T07:11:00Z", 320, n=1), rec("2026-11-10T19:10:00Z", 320, n=2)]), 3)


class InputErrors(Base):
    def test_malformed_line(self):
        self.assertEqual(self.run_cmd(None, "b1", "--registration-utc", "2026-11-09T14:00:00Z", raw="{not json\n"), 2)

    def test_non_object_line(self):
        self.assertEqual(self.run_cmd(None, "b2", "--after-run-utc", "2026-11-09T14:00:00Z", raw="[1]\n"), 2)

    def test_bad_run_utc_on_a_record(self):
        self.assertEqual(self.b1([rec("yesterday", 340)]), 2)

    def test_bad_instant_arg(self):
        self.assertEqual(self.b1([], reg="2026-11-09T14:00:00"), 2)  # no zone
        self.assertEqual(self.b2([], after="nope"), 2)

    def test_missing_file_and_bad_usage(self):
        self.assertEqual(bar.main(["b1", "--registration-utc", "2026-11-09T14:00:00Z", "--daily", "/nonexistent/x.jsonl"]), 2)
        self.assertEqual(bar.main(["b1"]), 2)
        self.assertEqual(bar.main([]), 2)

    def test_blank_lines_ok_and_non_ok_status_with_bad_run_utc_ignored(self):
        raw = "\n" + json.dumps(rec("2026-11-08T07:11:00Z", 340)) + "\n" + json.dumps({"status": "stopped", "run_utc": "x"}) + "\n" \
            + json.dumps(rec("2026-11-09T07:11:00Z", 340)) + "\n"
        self.assertEqual(self.run_cmd(None, "b1", "--registration-utc", "2026-11-09T14:00:00Z", raw=raw), 0)


class Cli(Base):
    def test_exit_code_and_output_via_subprocess(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "d.jsonl")
            with open(p, "w") as f:
                f.write(json.dumps(rec("2026-11-08T07:11:00Z", 340)) + "\n" + json.dumps(rec("2026-11-09T07:11:00Z", 341)) + "\n")
            r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "s1t_boost_bar.py"), "b1",
                                "--registration-utc", "2026-11-09T14:00:00Z", "--daily", p], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("B1 PASS", r.stdout)
        self.assertIn("run_utc=2026-11-08T07:11:00Z median=340 n=10", r.stdout)
        self.assertIn("run_utc=2026-11-09T07:11:00Z median=341 n=10", r.stdout)


if __name__ == "__main__":
    unittest.main()
