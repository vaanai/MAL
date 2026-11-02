"""tools/s1t_register.py: the one-command registration of s1t in its own arena root (DEC-031 Amendment 24). Fixtures only (tmp s1t home, tmp real home); no host path and no real bots.json is touched."""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tools import a3_halt
from tools import arena_h5_s1t_instant as ti
from tools import arena_h5_score as sc
from tools import s1t_register as R
from tools.arena_score import Refused

S1T = ti.S1T_ID
C7, C10 = ti.ROW_1107, ti.ROW_1110
HOST = "mal-research-0"
REPO = Path(__file__).resolve().parent.parent
LOCK = "bots.json.lock"  # the registry lock file, created by every run (s1's tool does the same)
BOOKS_3KEY = '{"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v1q50-boostfloor-s1t"], "fees_check_bot_ids": ["h5v1q50-boostfloor-s1t"]}\n'  # Amendment 22 item 9 with the s1t id, written out
BOOKS_2KEY = '{"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v1q50-boostfloor-s1t"]}\n'
REFS = {"preclock_notebook_ref": "nb_Abc123-xyz", "smoke_pass_job": "2101", "equiv_job": "2099", "feedbar_note": "nb_feedbar-1", "exp024_note_ref": "nb_exp024-s1t",
        "a3_loop_job": "2105", "a3_guard_commit": "c" * 40}


def ms(s: str) -> int:
    return int(dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


NOW7 = ms("2026-11-06T14:30:00Z")  # inside the 11-07 row's window, outside the A3 windows
NOW10 = ms("2026-11-09T15:30:00Z")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Setup(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        base = Path(self._d.name)
        self.real_home = base / "real-home"
        self.q50_root = self.real_home / "data" / "arena"
        self.q50_root.mkdir(parents=True)
        self.home = base / "s1thome"
        self.arena = self.home / "data" / "arena"
        self.arena.mkdir(parents=True)
        for p in (mock.patch.dict(os.environ, {"HOME": str(self.home)}), mock.patch.object(R.R.pwd, "getpwuid", return_value=types.SimpleNamespace(pw_dir=str(self.real_home)))):
            p.start()
            self.addCleanup(p.stop)
        self.books, self.read_books, self.rec, self.reg = (self.arena / n for n in ("a3-books.json", "a3-books-read.json", "s1t-registration.json", "bots.json"))
        self.hh_thr, self.hh_pins = base / "hh-thresholds.json", base / "hh-pins.json"  # fixture config files (not the real thresholds or pins)
        self.hh_thr.write_text(json.dumps({"writer_hour_complete": {"value": 1, "label": "price-path"}, "max_loss_span_s": {"value": 330, "label": "price-path"}}))
        self.hh_pins.write_text(json.dumps({"adapter_sha256": "a" * 64, "reader_sha256": "b" * 64, "writer_sha256": "c" * 64}))

    def run_(self, apply=False, now=NOW7, row=C7, root=None, **kw):
        kw.setdefault("refs", REFS)
        kw.setdefault("host", HOST)
        kw.setdefault("hh_thresholds", self.hh_thr)
        kw.setdefault("hh_pins", self.hh_pins)
        return R.run(self.arena if root is None else root, row, now_ms=now, apply=apply, **kw)

    def snapshot(self):
        return {p.name: (p.read_bytes(), p.stat().st_ino) for p in self.arena.iterdir() if p.name != LOCK}

    def names(self):
        return sorted(p.name for p in self.arena.iterdir() if p.name != LOCK)


class Pins(unittest.TestCase):
    def test_the_texts_are_the_amendment_text_byte_for_byte(self):
        self.assertEqual(R.A3_BOOKS_TEXT, BOOKS_3KEY)
        self.assertEqual(R.A3_BOOKS_READ_TEXT, BOOKS_2KEY)
        self.assertEqual((sha(BOOKS_3KEY), sha(BOOKS_2KEY)), (R.A3_BOOKS_SHA256, R.A3_BOOKS_READ_SHA256))
        self.assertEqual(R.S1T_ID, "h5v1q50-boostfloor-s1t")

    def test_the_roots_and_clocks(self):
        self.assertEqual((R.S1T_HOME, R.S1T_ROOT, R.SMOKE_ROOT), ("/home/claude/s1thome", "/home/claude/s1thome/data/arena", "/home/claude/s1tsmoke/data/arena"))
        self.assertEqual(R.CLOCKS, {"2026-11-07": C7, "2026-11-10": C10})

    def test_the_pinned_tools_are_the_registered_files_of_this_checkout(self):
        self.assertEqual(R.check_tools(REPO), R.PINNED_TOOLS)
        self.assertEqual(R.PINNED_TOOLS["tools/s1t_smoke_judge.py"][:8], "a91c29bf")
        self.assertEqual(R.PINNED_TOOLS["tools/s1t_arena_feed_watch.py"][:8], "b9941f82")
        self.assertEqual(R.PINNED_TOOLS["tools/s1t_side_feed_probe.py"][:8], "68f0c513")

    def test_the_books_files_pass_the_a3_validator_for_s1t_only(self):
        self.assertEqual(a3_halt.validate_books(json.loads(R.A3_BOOKS_TEXT), [S1T]), ([], [S1T]))
        with self.assertRaises(a3_halt.Invalid):  # an unregistered / missing s1t line is refused
            a3_halt.validate_books(json.loads(R.A3_BOOKS_TEXT), [S1T, "h5v1q50-boostfloor-a1"])


class DryRun(Setup):
    def test_dry_run_writes_nothing_and_reports_the_row(self):
        before = self.snapshot()
        rep = self.run_()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(rep["status"], "dry_run")
        self.assertEqual((rep["clock_start_ms"], rep["screen_cap_ms"], rep["window_end_ms"], rep["frozen_sha"]), (1_794_009_600_000, 1_794_873_600_000, 1_796_083_200_000, C7["frozen_sha"]))
        self.assertEqual((rep["new_rule"], rep["legacy"], rep["window_ok"], rep["forbidden_minute"], rep["endpoints"]), ([S1T], [], True, False, ["wss://rpc.solanatracker.io"]))

    def test_dry_run_outside_the_window_still_runs_the_other_checks(self):
        rep = self.run_(now=C7["register_not_before_ms"] - 1)
        self.assertEqual((rep["status"], rep["window_ok"]), ("dry_run", False))
        with self.assertRaises(Refused):  # ... and a structural refusal is still a refusal
            self.run_(now=C7["register_not_before_ms"] - 1, root=self.q50_root)

    def test_dry_run_inside_a3_windows_reports_it(self):
        rep = self.run_(now=ms("2026-11-06T13:10:00Z"))
        self.assertTrue(rep["forbidden_minute"])


class Apply(Setup):
    def test_apply_writes_books_line_and_record(self):
        rep = self.run_(apply=True)
        self.assertEqual(rep["status"], "applied")
        self.assertEqual(self.books.read_text(), BOOKS_3KEY)
        self.assertEqual(self.read_books.read_text(), BOOKS_2KEY)
        for p in (self.books, self.read_books, self.rec):
            self.assertEqual(p.stat().st_mode & 0o777, 0o444, p.name)
        bots = json.loads(self.reg.read_text())["bots"]
        self.assertEqual(set(bots), {S1T})
        e = bots[S1T]
        self.assertEqual((e["frozen_sha"], e["clock_start_ms"], e["window_end_ms"], e["window_end_ms_pin"], e["registered_at_ms"], e["sockets"], e["host"]),
                         (C7["frozen_sha"], C7["clock_start_ms"], ti.SUCC_END_MS, ti.SUCC_END_MS, NOW7, 1, HOST))
        self.assertEqual((e["rule"], e["q_star_sol"], e["exit_rule"], e["endpoints"]), ("H5-BOOSTFLOOR-Q50 v1", 50.0, "v1", ["wss://rpc.solanatracker.io"]))
        self.assertEqual(sha(self.rec.read_text()), rep["record_sha256"])
        self.assertEqual(self.rec.read_text(), rep["record_text"])

    def test_the_record_carries_what_amendment_24_requires(self):
        self.run_(apply=True)
        r = json.loads(self.rec.read_text())
        self.assertEqual((r["bot_id"], r["clock_start_ms"], r["screen_cap_ms"], r["window_end_ms"], r["h_days"]), (S1T, 1_794_009_600_000, 1_794_873_600_000, 1_796_083_200_000, 24))
        self.assertEqual((r["p0_per_day"], r["p0_rule"]), (35.333333333333336, "reused, fixed"))
        self.assertIn("#1413", r["p0_ref"])
        self.assertEqual((r["frozen_sha"], r["tag"], r["tag_object"]), ("afa4971d9dd8b0bca2e73a758c22f4539f675d63", "q50-succ-tip-1", "c8cb9a8e31419fcce03b9fe3947a474ebdd7f47a"))
        self.assertEqual((r["shadow_blob"][:8], r["wrapper_blob"][:8], r["s_star"][:8]), ("7f859b12", "05c5489a", "5c2ab7c6"))
        self.assertEqual((r["tip_input"], r["syn_sockets"], r["side_feed_ws"], r["fresh_forward"]), ("/data2/mal/arena-tip-feed/hv7", 1, "wss://rpc.solanatracker.io/public", "ARENA"))
        self.assertEqual((r["a3_books_sha256"], r["a3_books_read_sha256"]), (R.A3_BOOKS_SHA256, R.A3_BOOKS_READ_SHA256))
        self.assertEqual(r["pinned_tools"], R.PINNED_TOOLS)
        self.assertEqual(r["refs"], REFS)
        self.assertEqual((r["screen_n"], r["confirm_n"], r["instant_file"]), (200, 300, ti.INSTANT_FILE))
        self.assertNotIn("key", " ".join(r["endpoints"]).lower())

    def test_the_record_pins_the_tip_hour_health_tool_thresholds_pins_programs_and_t_max(self):
        self.run_(apply=True)
        hh = json.loads(self.rec.read_text())["tip_hour_health"]
        self.assertEqual(hh["tool"], "tools/tip_hour_health.py")
        self.assertEqual(hh["sha256"], hashlib.sha256((REPO / "tools" / "tip_hour_health.py").read_bytes()).hexdigest())
        self.assertEqual((hh["programs"], hh["t_max_s"], hh["schema"]), (["pumpswap", "pumpfun"], 333.0, "tip_hour_health_v1"))
        self.assertEqual(hh["thresholds_sha256"], hashlib.sha256(self.hh_thr.read_bytes()).hexdigest())
        self.assertEqual(hh["pins_sha256"], hashlib.sha256(self.hh_pins.read_bytes()).hexdigest())
        self.assertEqual(hh["thresholds"]["max_loss_span_s"], {"value": 330, "label": "price-path"})
        self.assertEqual(hh["pins"], {"adapter_sha256": "a" * 64, "reader_sha256": "b" * 64, "writer_sha256": "c" * 64})

    def test_hour_health_files_are_required_and_validated(self):
        for kw in ({"hh_thresholds": None}, {"hh_pins": None}, {"hh_thresholds": Path(self._d.name) / "missing.json"}):
            with self.assertRaises(Refused, msg=str(kw)):
                self.run_(apply=True, **kw)
        bad_thr = Path(self._d.name) / "bad-thr.json"
        for text in ("not json", "[]", "{}", json.dumps({"nope": {"value": 1, "label": "price-path"}}), json.dumps({"max_loss_span_s": {"value": -1, "label": "price-path"}}),
                     json.dumps({"max_loss_span_s": {"value": 1, "label": "other"}})):
            bad_thr.write_text(text)
            with self.assertRaises(Refused, msg=text):
                self.run_(apply=True, hh_thresholds=bad_thr)
        bad_pins = Path(self._d.name) / "bad-pins.json"
        for text in ("not json", "{}", json.dumps({"adapter_sha256": "a" * 64, "reader_sha256": "b" * 64}), json.dumps({"adapter_sha256": "a" * 64, "reader_sha256": "b" * 64, "writer_sha256": "short"})):
            bad_pins.write_text(text)
            with self.assertRaises(Refused, msg=text):
                self.run_(apply=True, hh_pins=bad_pins)
        self.assertEqual(self.names(), [])

    def test_the_slip_row_needs_and_records_its_own_p0(self):
        for kw in ({}, {"p0_per_day": 30.0}, {"p0_ref": "nb_p0_count"}):
            with self.assertRaises(Refused, msg=str(kw)):
                self.run_(apply=True, now=NOW10, row=C10, **kw)
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(Refused, msg=str(bad)):
                self.run_(apply=True, now=NOW10, row=C10, p0_per_day=bad, p0_ref="nb_p0_count")
        self.assertEqual(self.names(), [])
        self.run_(apply=True, now=NOW10, row=C10, p0_per_day=31.5, p0_ref="nb_p0_count")
        r = json.loads(self.rec.read_text())
        self.assertEqual((r["clock_start_ms"], r["screen_cap_ms"], r["h_days"], r["p0_per_day"], r["p0_ref"]), (1_794_268_800_000, 1_795_132_800_000, 21, 31.5, "notebook nb_p0_count"))

    def test_the_11_07_row_refuses_a_p0_argument(self):
        for kw in ({"p0_per_day": 30.0}, {"p0_ref": "nb_p0_count"}):
            with self.assertRaises(Refused):
                self.run_(apply=True, **kw)
        self.assertEqual(self.names(), [])

    def test_refused_outside_the_window_at_the_clock_and_in_a3_windows(self):
        for now in (C7["register_not_before_ms"] - 1, C7["register_not_after_ms"] + 1, C7["clock_start_ms"], C7["clock_start_ms"] + 5):
            with self.assertRaises(ValueError, msg=str(now)):
                self.run_(apply=True, now=now)
        for hh in ("2026-11-06T13:10:00Z", "2026-11-06T19:04:00Z", "2026-11-06T19:25:00Z"):
            with self.assertRaises(Refused, msg=hh):
                self.run_(apply=True, now=ms(hh))
        self.run_(apply=False, now=ms("2026-11-06T13:10:00Z"))  # a dry run is allowed there
        self.assertEqual(self.names(), [])

    def test_refused_off_host_and_with_now_ms(self):
        with self.assertRaises(Refused):
            self.run_(apply=True, host="mal-fast-0")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = R.main(["--arena-root", str(self.arena), "--apply", "--now-ms", str(NOW7), "--hour-health-thresholds", str(self.hh_thr), "--hour-health-pins", str(self.hh_pins)]
                        + [x for k, v in REFS.items() for x in ("--" + k.replace("_", "-"), v)])
        self.assertEqual(rc, 2)
        self.assertIn("--now-ms", err.getvalue())

    def test_the_other_roots_are_refused_even_through_a_symlink(self):
        base = Path(self._d.name)
        others = {"q50": self.q50_root, "s1": base / "s1home" / "data" / "arena", "smoke": base / "smoke" / "data" / "arena"}
        for name, o in others.items():
            o.mkdir(parents=True, exist_ok=True)
        link = base / "link"
        link.symlink_to(self.q50_root)
        with mock.patch.object(R, "S1_ROOT", str(others["s1"])), mock.patch.object(R, "SMOKE_ROOT", str(others["smoke"])), mock.patch.object(R, "HV4_ROOT", str(base / "hv4")):
            (base / "hv4").mkdir()
            for o in (*others.values(), link, base / "hv4"):
                with self.assertRaises(Refused, msg=str(o)):
                    self.run_(apply=True, root=o)
        with self.assertRaises(Refused):
            self.run_(root=base / "does-not-exist")
        self.assertEqual(os.listdir(self.q50_root), [])

    def test_a_registry_with_another_bot_is_refused(self):
        sc.register_books(self.reg, frozen_sha="1" * 40, sockets=1, endpoints=["e"], host="h", now_ms=NOW7 - 5 * 86_400_000, clock_start_ms=NOW7 - 4 * 86_400_000)
        before = self.snapshot()
        with self.assertRaises(Refused):
            self.run_(apply=True)
        self.assertEqual(self.snapshot(), before)

    def test_each_confirmation_is_required_and_format_checked(self):
        for k in REFS:
            for bad in (None, "", "bad ref!", "x" * 90 if k.endswith("ref") else "12a", "../x"):
                refs = {**REFS, k: bad}
                with self.assertRaises(Refused, msg=f"{k}={bad!r}"):
                    self.run_(apply=True, refs=refs)
        with self.assertRaises(Refused):
            self.run_(apply=True, refs={k: v for k, v in REFS.items() if k != "equiv_job"})
        self.assertEqual(self.names(), [])

    def test_a_pinned_tool_that_differs_is_refused(self):
        fake = Path(self._d.name) / "repo"
        (fake / "tools").mkdir(parents=True)
        for rel in R.PINNED_TOOLS:
            (fake / rel).write_text("x\n")
        with self.assertRaises(Refused):
            self.run_(apply=True, repo_root=fake)
        (fake / "tools" / "s1t_smoke_judge.py").unlink()
        with self.assertRaises(Refused):
            self.run_(apply=True, repo_root=fake)
        self.assertEqual(self.names(), [])

    def test_a_different_books_file_or_record_is_not_overwritten(self):
        self.books.write_text("{}\n")
        with self.assertRaises(Refused):
            self.run_(apply=True)
        self.assertEqual((self.books.read_text(), self.reg.exists()), ("{}\n", False))
        self.books.unlink()
        self.rec.write_text("{}\n")
        with self.assertRaises(Refused):  # a record without a line
            self.run_(apply=True)
        self.assertFalse(self.reg.exists())


class Rerun(Setup):
    def test_a_finished_registration_is_a_no_op(self):
        first = self.run_(apply=True)
        snap = self.snapshot()
        again = self.run_(apply=True, now=NOW7 + 3_600_000)
        self.assertEqual((again["status"], again["registered_at_ms"], again["record_sha256"]), ("already_done", NOW7, first["record_sha256"]))
        self.assertEqual(self.snapshot(), snap)

    def test_a_rerun_with_other_confirmations_or_a_different_clock_is_refused(self):
        self.run_(apply=True)
        snap = self.snapshot()
        self.hh_thr.write_text(json.dumps({"max_loss_span_s": {"value": 999, "label": "price-path"}}))
        with self.assertRaises(Refused):  # other thresholds than the recorded ones
            self.run_(apply=True, now=NOW7 + 500)
        self.hh_thr.write_text(json.dumps({"writer_hour_complete": {"value": 1, "label": "price-path"}, "max_loss_span_s": {"value": 330, "label": "price-path"}}))
        with self.assertRaises(Refused):
            self.run_(apply=True, now=NOW7 + 1000, refs={**REFS, "smoke_pass_job": "9999"})
        with self.assertRaises(Refused):
            self.run_(apply=True, now=NOW10, row=C10, p0_per_day=30.0, p0_ref="nb_p0_count")
        self.assertEqual(self.snapshot(), snap)

    def test_a_crash_after_the_line_is_completed_by_a_rerun(self):
        self.run_(apply=True)
        self.rec.chmod(0o644)
        self.rec.unlink()
        again = self.run_(apply=True, now=NOW7 + 60_000)
        self.assertEqual((again["status"], again["registered_at_ms"]), ("completed_record", NOW7))
        self.assertEqual(json.loads(self.rec.read_text())["registered_at_ms"], NOW7)  # the line's instant, not the rerun's

    def test_a_line_with_a_missing_books_file_is_a_manual_state(self):
        self.run_(apply=True)
        self.read_books.chmod(0o644)
        self.read_books.unlink()
        with self.assertRaises(Refused):
            self.run_(apply=True)

    def test_a_changed_line_is_a_new_bot_id(self):
        self.run_(apply=True)
        bots = json.loads(self.reg.read_text())["bots"]
        bots[S1T]["frozen_sha"] = "a" * 40
        self.reg.write_text(json.dumps({"bots": bots}))
        with self.assertRaises(ValueError):
            self.run_(apply=True)


class Cli(Setup):
    def argv(self, *extra):
        return ["--arena-root", str(self.arena), "--host", HOST, "--hour-health-thresholds", str(self.hh_thr), "--hour-health-pins", str(self.hh_pins), *extra] + [x for k, v in REFS.items() for x in ("--" + k.replace("_", "-"), v)]

    def test_cli_dry_run_prints_json_then_the_texts(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = R.main(self.argv("--now-ms", str(NOW7)))
        self.assertEqual(rc, 0, err.getvalue())
        lines = out.getvalue().split("\n")
        rep = json.loads(lines[0])
        self.assertEqual((rep["status"], rep["a3_books_sha256"]), ("dry_run", R.A3_BOOKS_SHA256))
        self.assertNotIn("record_text", rep)
        self.assertIn(BOOKS_3KEY, out.getvalue())
        self.assertIn(f"# s1t-registration.json sha256 {rep['record_sha256']}", out.getvalue())
        self.assertEqual(self.names(), [])

    def test_cli_refuses_an_uncommitted_clock_and_a_missing_confirmation(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            R.main(self.argv("--clock", "2026-11-08"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            R.main(["--arena-root", str(self.arena)])
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = R.main(self.argv("--now-ms", str(NOW7), "--p0-per-day", "30"))  # the 11-07 row takes no P0 argument
        self.assertEqual(rc, 2)
        self.assertIn("REFUSED", err.getvalue())


if __name__ == "__main__":
    unittest.main()
