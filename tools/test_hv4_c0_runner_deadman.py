"""Tests for scripts/ops/hv4-c0-runner-deadman.sh: ALERT when the forwarder is connected and no forwarded decision row arrived for longer than the bound; UNKNOWN (never OK)
when the feed is not live; only names, sizes and mtimes of the decision files are looked at. Synthetic fixtures only: the status file is written by the REAL forwarder class
(tools.decision_forwarder.Puller.write_status) in a temp dir; decision files hold a sentinel and are made unreadable to show nothing opens them. /data/mal/arena appears
only as a string in the refusal test."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import decision_forwarder as df

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "ops" / "hv4-c0-runner-deadman.sh"
BOT = "hv4-BIG-X"
NOW = 1_800_000_000  # epoch seconds, the script's clock (DEADMAN_NOW_S)
SENTINEL = "ROW-CONTENT-SENTINEL-MUST-NEVER-BE-READ"


class Deadman(unittest.TestCase):
    def setUp(self) -> None:
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.d = Path(self._d.name)
        self.root = self.d / "root"
        self.out = self.d / "state" / "deadman.json"
        self.puller = df.Puller(str(self.root), BOT, ["true"], clock=lambda: NOW * 1_000_000_000)
        self.addCleanup(self.puller.close)
        self.dec = self.root / "decisions" / BOT
        self.status = self.root / "transport" / f"{BOT}.status.json"
        self.status_ok()

    def status_ok(self, state="connected", frame_age_ms=300, **over):
        p = self.puller
        p.state, p.last_frame_ms, p.last_remote_ms = state, NOW * 1000 - frame_age_ms, NOW * 1000 - frame_age_ms
        for k, v in over.items():
            setattr(p, k, v)
        p.write_status()

    def day_file(self, name="2026-10-30.jsonl", age_s=100, size=40):
        f = self.dec / name
        f.write_text((SENTINEL + "\n") * max(1, size // 40) if size else "")
        os.utime(f, (NOW - age_s, NOW - age_s))
        return f

    def run_sh(self, bound="1800", root=None, bot=BOT, **env):
        args = [str(root or self.root), bot, bound, str(self.out)]
        e = {"PATH": os.environ["PATH"], "HOME": str(self.d), "DEADMAN_NOW_S": str(NOW), **env}
        return subprocess.run(["sh", str(SCRIPT), *args], capture_output=True, text=True, env=e)

    def record(self):
        return json.loads(self.out.read_text())

    # --- the dead-man -------------------------------------------------------------------------------------------------------------------------------------------
    def test_a_recent_row_is_ok(self):
        self.day_file(age_s=100)
        p = self.run_sh()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("verdict=OK", p.stdout)
        r = self.record()
        self.assertEqual((r["verdict"], r["reason"], r["silence_s"], r["decision_files"], r["forwarder_state"]), ("OK", None, 100, 1, "connected"))
        self.assertEqual(r["schema"], "hv4_c0_deadman_v1")
        self.assertEqual(r["checked_s"], NOW)

    def test_no_row_for_longer_than_the_bound_while_connected_is_an_alert(self):
        self.day_file(age_s=1801)
        p = self.run_sh()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("verdict=ALERT reason=runner_silent", p.stdout)
        self.assertIn("ALERT hv4_c0 runner_silent: no forwarded decision row for 1801s (bound 1800s)", p.stdout)
        r = self.record()
        self.assertEqual((r["verdict"], r["reason"], r["silence_s"], r["bound_s"]), ("ALERT", "runner_silent", 1801, 1800))

    def test_the_bound_is_exclusive_and_configurable(self):
        self.day_file(age_s=1800)
        self.assertEqual(self.run_sh().returncode, 0)  # exactly the bound is not over it
        self.assertEqual(self.run_sh(bound="1799").returncode, 1)
        self.assertEqual(self.run_sh(bound="60").returncode, 1)

    def test_the_newest_non_empty_file_decides_across_day_files(self):
        self.day_file("2026-10-29.jsonl", age_s=90_000)
        self.day_file("2026-10-30.jsonl", age_s=50)
        self.assertEqual(self.run_sh().returncode, 0)
        self.day_file("2026-10-30.jsonl", age_s=5000)
        p = self.run_sh()
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.record()["decision_files"], 2)

    def test_an_empty_file_is_not_a_row(self):
        self.day_file("2026-10-29.jsonl", age_s=5000)
        self.day_file("2026-10-30.jsonl", age_s=1, size=0)  # created by the puller, no row yet
        p = self.run_sh()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(self.record()["silence_s"], 5000)

    def test_no_forwarded_row_at_all_is_unknown_not_ok(self):
        p = self.run_sh()  # the puller made the empty dir
        self.assertEqual((p.returncode, self.record()["reason"]), (3, "no_decision_rows"))
        self.day_file(size=0)
        self.assertEqual((self.run_sh().returncode, self.record()["reason"]), (3, "no_decision_rows"))

    # --- the feed is not live: no verdict about the runner, never OK ----------------------------------------------------------------------------------------------
    def test_a_feed_that_is_not_connected_is_unknown_even_with_old_rows(self):
        self.day_file(age_s=99_999)
        for state in ("stalled", "disconnected", "connecting", "stopped_mismatch", "init"):
            self.status_ok(state=state)
            p = self.run_sh()
            self.assertEqual(p.returncode, 3, state)
            self.assertEqual(self.record()["reason"], f"feed_state_{state}")
            self.assertEqual(self.record()["verdict"], "UNKNOWN")
            self.assertNotIn("ALERT hv4_c0", p.stdout)  # the feed's fault is not reported as a runner alert

    def test_a_stale_or_silent_or_future_status_is_unknown(self):
        self.day_file(age_s=99_999)
        self.status_ok(frame_age_ms=6_000)
        self.assertEqual(self.record_after()["reason"], "feed_silent")
        self.status_ok(frame_age_ms=300)
        p = self.run_sh(DEADMAN_NOW_S=str(NOW + 11))
        self.assertEqual((p.returncode, self.record()["reason"]), (3, "feed_status_stale"))
        p = self.run_sh(DEADMAN_NOW_S=str(NOW - 60))
        self.assertEqual((p.returncode, self.record()["reason"]), (3, "feed_status_future"))

    def record_after(self):
        self.run_sh()
        return self.record()

    def test_missing_foreign_malformed_oversize_or_linked_status_is_unknown(self):
        self.day_file(age_s=99_999)
        good = self.status.read_text()
        self.status.unlink()
        self.assertEqual((self.run_sh().returncode, self.record()["reason"]), (3, "feed_status_missing"))
        self.status.write_text(good.replace(BOT, "hv4-CON-X"))
        self.assertEqual(self.record_after()["reason"], "feed_status_foreign")
        self.status.write_text(good.replace("decision_forwarder_v1", "other"))
        self.assertEqual(self.record_after()["reason"], "feed_status_foreign")
        self.status.write_text('{"schema": "decision_forwarder_v1", "bot": "%s", "state": "connected", "ms": "x", "last_frame_ms": 1}\n' % BOT)
        self.assertEqual(self.record_after()["reason"], "feed_status_malformed")
        self.status.write_text("x" * 70_000)
        self.assertEqual(self.record_after()["reason"], "feed_status_oversize")
        self.status.unlink()
        real = self.d / "real.json"
        real.write_text(good)
        os.symlink(real, self.status)
        self.assertEqual(self.record_after()["reason"], "feed_status_not_regular")
        self.status.unlink()
        os.link(real, self.status)  # a hard link is not the puller's own file
        self.assertEqual(self.record_after()["reason"], "feed_status_not_regular")

    def test_a_linked_or_non_regular_decision_file_is_unknown(self):
        real = self.d / "elsewhere.jsonl"
        real.write_text(SENTINEL + "\n")
        os.symlink(real, self.dec / "2026-10-30.jsonl")
        self.assertEqual((self.run_sh().returncode, self.record()["reason"]), (3, "decision_file_not_regular"))
        os.unlink(self.dec / "2026-10-30.jsonl")
        (self.dec / "2026-10-30.jsonl").mkdir()
        self.assertEqual(self.record_after()["reason"], "decision_file_not_regular")
        (self.dec / "2026-10-30.jsonl").rmdir()
        shutil.rmtree(self.dec)
        self.assertEqual(self.record_after()["reason"], "no_decision_dir")

    # --- what it reads ---------------------------------------------------------------------------------------------------------------------------------------------
    @unittest.skipIf(os.geteuid() == 0, "root can read a mode-000 file")
    def test_it_never_opens_a_decision_file(self):
        f = self.day_file(age_s=100)
        os.chmod(f, 0)
        self.addCleanup(os.chmod, f, 0o644)
        p = self.run_sh()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn(SENTINEL, p.stdout + p.stderr + self.out.read_text())
        os.utime(f, (NOW - 5000, NOW - 5000))
        self.assertEqual(self.run_sh().returncode, 1)

    def test_source_has_no_read_of_the_decision_directory(self):
        code = "\n".join(ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#"))
        for ln in code.splitlines():
            if "$DD" in ln or '"$f"' in ln:
                for reader in ("cat ", "head ", "tail ", "grep ", "sed ", "awk ", "cut ", "< ", "wc ", "read "):
                    if reader in ln:
                        self.assertIn("stat -c", ln, f"a decision-path line reads something: {ln}")
        for ln in code.splitlines():
            if "cat " in ln:
                self.assertIn("$SF", ln)  # the one cat is of the status file

    # --- housekeeping ----------------------------------------------------------------------------------------------------------------------------------------------
    def test_status_file_is_written_atomically_and_replaced(self):
        self.day_file(age_s=100)
        self.run_sh()
        first = self.record()
        self.day_file(age_s=5000)
        self.run_sh()
        self.assertEqual((first["verdict"], self.record()["verdict"]), ("OK", "ALERT"))
        self.assertEqual(sorted(p.name for p in self.out.parent.iterdir()), ["deadman.json"])  # no tmp file left

    def test_an_unwritable_status_file_still_gives_the_verdict_on_stdout(self):
        self.day_file(age_s=5000)
        e = {"PATH": os.environ["PATH"], "HOME": str(self.d), "DEADMAN_NOW_S": str(NOW)}
        p = subprocess.run(["sh", str(SCRIPT), str(self.root), BOT, "1800", "/proc/nowhere/deadman.json"], capture_output=True, text=True, env=e)
        self.assertEqual(p.returncode, 1)
        self.assertIn("verdict=ALERT", p.stdout)
        self.assertIn("WARNING status file not written", p.stdout)

    def test_refusals(self):
        for args in ([], ["/tmp/x"], ["/data/mal/arena/x", BOT], ["/data/mal/arena", BOT], [str(self.root) + "/../x", BOT], [str(self.root), "bad/bot"], [str(self.root), ".hidden"],
                     [str(self.root), BOT, "0"], [str(self.root), BOT, "abc"], [str(self.root), BOT, "-5"]):
            p = subprocess.run(["sh", str(SCRIPT), *args], capture_output=True, text=True, env={"PATH": os.environ["PATH"], "HOME": str(self.d), "DEADMAN_NOW_S": str(NOW)})
            self.assertEqual(p.returncode, 2, (args, p.stdout))
            self.assertIn("REFUSED", p.stdout)
        self.assertFalse(self.out.exists())

    def test_a_link_that_resolves_into_the_sealed_prefix_is_refused(self):
        """Run on a COPY of the script whose SEALED line names a stand-in directory, so the real arena prefix is never touched by this test."""
        stand = self.d / "stand-in-arena"
        (stand / "decisions").mkdir(parents=True)
        src = SCRIPT.read_text()
        self.assertEqual(src.count("SEALED=/data/mal/arena\n"), 1)
        copy = self.d / "script-copy.sh"
        copy.write_text(src.replace("SEALED=/data/mal/arena\n", f"SEALED={stand}\n"))
        link = self.d / "lnk"
        os.symlink(stand, link)
        e = {"PATH": os.environ["PATH"], "HOME": str(self.d), "DEADMAN_NOW_S": str(NOW)}
        for root in (link, stand / "decisions", stand):
            p = subprocess.run(["sh", str(copy), str(root), BOT, "1800", str(self.out)], capture_output=True, text=True, env=e)
            self.assertEqual(p.returncode, 2, (root, p.stdout))
            self.assertIn("REFUSED", p.stdout)
        self.assertFalse(self.out.exists())
        p = subprocess.run(["sh", str(copy), str(self.root), BOT, "1800", str(self.out)], capture_output=True, text=True, env=e)  # the same copy still works on a normal root
        self.assertEqual(p.returncode, 3, p.stdout)  # no decision rows yet, but not refused

    def test_script_is_posix_sh_without_bashisms(self):
        text = SCRIPT.read_text()
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        for bad in ("[[", "PIPESTATUS", "<(", "local ", "function ", "source ", "echo -e", "declare ", "$'", "&>"):
            self.assertNotIn(bad, code)
        if shutil.which("dash"):
            self.assertEqual(subprocess.run(["dash", "-n", str(SCRIPT)], capture_output=True).returncode, 0)
            e = {"PATH": os.environ["PATH"], "HOME": str(self.d), "DEADMAN_NOW_S": str(NOW)}
            self.day_file(age_s=100)
            p = subprocess.run(["dash", str(SCRIPT), str(self.root), BOT, "1800", str(self.out)], capture_output=True, text=True, env=e)  # a strict POSIX shell
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_the_watchdog_pickup_fields_are_in_the_status_file(self):
        self.day_file(age_s=100)
        self.run_sh()
        self.assertEqual(sorted(self.record()), ["bot", "bound_s", "checked_s", "decision_bytes", "decision_files", "forwarder_state", "forwarder_status_age_s",
                                                  "newest_row_mtime_s", "reason", "schema", "silence_s", "verdict"])

    def test_defaults_match_the_bridges_own_bounds(self):
        from tools import hv4_live_c0 as c0
        text = SCRIPT.read_text()
        self.assertIn("BOUND=${3:-%d}" % (c0.RUNNER_SILENT_MS // 1000), text)
        self.assertIn(f"DEADMAN_STATUS_MAX_AGE_S:-{c0.FEED_STATUS_MAX_AGE_MS // 1000}", text)
        self.assertIn(f"DEADMAN_FRAME_MAX_S:-{c0.FEED_FRAME_MAX_MS // 1000}", text)


if __name__ == "__main__":
    unittest.main()
