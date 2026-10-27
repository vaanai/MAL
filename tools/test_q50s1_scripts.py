"""scripts/ops/q50s1-register.sh and scripts/ops/q50s1-shadow-start.sh: POSIX sh, refusals, and the --check rehearsal with stubbed host tools. Fixtures only; nothing is started."""
from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REG = REPO / "scripts" / "ops" / "q50s1-register.sh"
SHD = REPO / "scripts" / "ops" / "q50s1-shadow-start.sh"
WIN_FROM, WIN_TO = 1793908800, 1793916900  # 2026-11-05T20:00:00Z, 22:30:00Z
GIB = 1024 ** 3


class Syntax(unittest.TestCase):
    def test_posix_sh_no_bashisms(self):
        for p in (REG, SHD):
            subprocess.run(["sh", "-n", str(p)], check=True)
            text = p.read_text()
            self.assertTrue(text.startswith("#!/bin/sh"), p)
            for bad in ("PIPESTATUS", "[[", "<<<", "declare ", "local ", "source ", "function ", "mapfile", "${BASH"):
                self.assertNotIn(bad, text, f"{p.name}: {bad}")
            self.assertIsNone(re.search(r"\$\{[A-Za-z_]+/", text), p)
            self.assertIsNone(re.search(r"\bset -o pipefail\b", text), p)

    def test_neither_script_touches_a_pinned_unit_a_firewall_or_a_key(self):
        for p in (REG, SHD):
            t = p.read_text()
            for bad in ("ufw", "sshd", "cloudflared", "private", ".env", "api-key"):
                self.assertNotIn(bad, t.lower(), f"{p.name}: {bad}")

    def test_shadow_pins_s_star_and_the_window_and_never_f50(self):
        t = SHD.read_text()
        self.assertIn("S_SHA=5c2ab7c64b21358619e37068c6a53e4d183512ea", t)
        self.assertNotIn("a5f41370", t.replace("NOT F50 a5f41370", "").replace("never main, never F50 a5f41370", ""))
        self.assertIn("WIN_FROM=1793908800", t)
        self.assertIn("WIN_TO=1793916900", t)
        self.assertIn("--q-star-sol 50", t)
        self.assertIn("MemoryMax=", t)
        self.assertIn("MemorySwapMax=0", t)


class Register(unittest.TestCase):
    def run_(self, *args, arena="/nonexistent-arena"):
        env = dict(os.environ, HOME="/nonexistent-home", Q50S1_ARENA=arena)
        return subprocess.run(["sh", str(REG), *args], capture_output=True, text=True, env=env, cwd=REPO)

    def test_bad_arguments_refuse(self):
        for a in (("--bogus",), ("--apply", "--now-ms", "1"), ("x",)):
            r = self.run_(*a)
            self.assertEqual(r.returncode, 2, a)
            self.assertIn("q50s1-register", r.stderr)

    def test_an_unknown_arena_root_is_refused_by_the_tool_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_(arena=d)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("REFUSED", r.stderr)
            self.assertEqual(list(Path(d).iterdir()), [])


class ShadowCheck(unittest.TestCase):
    """--check with PATH stubs for hostname, systemctl and git: every refusal path, and the printed command on success."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.d = Path(self._d.name)
        self.bin = self.d / "bin"
        self.bin.mkdir()
        self.home = self.d / "home"
        (self.home / "data" / "arena").mkdir(parents=True)
        (self.home / "data" / "arena-src").mkdir()
        self.arena = self.home / "data" / "arena"
        self.py = self.bin / "fakepy"  # the import check of websockets / certifi succeeds; every other call runs the real interpreter
        self.py.write_text('#!/bin/sh\ncase "$*" in *"import websockets"*) exit 0 ;; esac\nexec python3 "$@"\n')
        self.py.chmod(0o755)
        self.stub("hostname", "echo mal-fast-0")
        self.set_mem(cur=2 * GIB, smax=20 * GIB, running=[("a.service", 1 * GIB), ("b.service", "infinity")])
        S = "5c2ab7c64b21358619e37068c6a53e4d183512ea"
        self.stub("git", f'''
case "$*" in
  *"fetch"*) exit 0 ;;
  *"rev-parse q50-succ-base-1"*) echo {S} ;;
  *"rev-parse HEAD"*) echo {S} ;;
  *"status --porcelain"*) printf '%s' "$FAKE_GIT_STATUS" ;;
  *) exit 0 ;;
esac''')
        self.src = self.home / "data" / "arena-src" / "q50s1-5c2ab7c6"
        self.src.mkdir()
        self.write_line()

    def stub(self, name, body):
        p = self.bin / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)

    def set_mem(self, *, cur, smax, running):
        units = "\n".join(u for u, _ in running)
        maxes = "\n".join(f'    "{u}") echo {m} ;;' for u, m in running)
        self.stub("systemctl", f'''
case "$*" in
  *"is-active"*) exit 3 ;;
  *"show user-1002.slice -p MemoryCurrent"*) echo {cur} ;;
  *"show user-1002.slice -p MemoryMax"*) echo {smax} ;;
  *"list-units"*) printf '%s\\n' {" ".join(repr(u + " loaded active running x") for u, _ in running) or "''"} ;;
  *"show"*)
    for a in "$@"; do last="$a"; done
    case "$3" in
{maxes}
    *) echo infinity ;;
    esac ;;
esac''')

    def write_line(self, **over):
        import json
        e = {"frozen_sha": "5c2ab7c64b21358619e37068c6a53e4d183512ea", "clock_start_ms": 1793923200000, "window_end_ms_pin": 1795132800000, "registered_at_ms": 1793800000000}
        e.update(over)
        (self.arena / "bots.json").write_text(json.dumps({"bots": {"h5v1q50-boostfloor-s1": e}}))

    def run_(self, now=WIN_FROM + 60, git_status="", extra_env=None):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_NOW_EPOCH": str(now), "Q50S1_REPO": str(self.d), "ARENA_H5_PYTHON": str(self.py),
               "FAKE_GIT_STATUS": git_status, **(extra_env or {})}
        return subprocess.run(["sh", str(SHD), "--check"], capture_output=True, text=True, env=env, cwd=REPO)

    def test_happy_path_prints_the_command_and_starts_nothing(self):
        r = self.run_()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("(check) would run", r.stdout)
        self.assertIn("--fresh-forward ARENA --q-star-sol 50", r.stdout)
        self.assertIn("MemoryMax=1500M", r.stdout)
        self.assertIn(f"{self.arena}/h5-q50s1-2026-11-05", r.stdout)
        self.assertIn("uncapped running units: 1", r.stdout)
        self.assertFalse((self.arena / "h5-q50s1-2026-11-05").exists())

    def test_check_outside_the_window_reports_but_a_real_run_would_exit_4(self):
        for now in (WIN_FROM - 1, WIN_TO + 1):
            r = self.run_(now=now)
            self.assertIn("outside the start window", r.stderr)
            self.assertEqual(r.returncode, 0)  # --check reports; the real run exits 4 (below)
        t = SHD.read_text()
        self.assertRegex(t, r'outside the start window.*\n\s*\[ "\$MODE" = "check" \] \|\| exit 4')

    def test_dirty_worktree_wrong_line_existing_out_dir_exit_codes(self):
        self.assertEqual(self.run_(git_status=" M tools/h5_shadow.py").returncode, 5)
        for over in ({"frozen_sha": "a5f41370" + "0" * 32}, {"clock_start_ms": 1793923200001}, {"window_end_ms_pin": 1}, {"registered_at_ms": 1793999999999999}):
            self.write_line(**over)
            r = self.run_()
            self.assertEqual(r.returncode, 8, (over, r.stderr))
        self.write_line()
        (self.arena / "h5-q50s1-2026-11-05").mkdir()
        self.assertEqual(self.run_().returncode, 9)
        (self.arena / "h5-q50s1-2026-11-05").rmdir()
        (self.arena / "bots.json").unlink()
        self.assertEqual(self.run_().returncode, 8)

    def test_memory_that_does_not_fit_exits_10_and_never_starts(self):
        self.set_mem(cur=18 * GIB, smax=20 * GIB, running=[("a.service", 1 * GIB)])  # 18 + 1 + 1.46 GiB > 20
        r = self.run_()
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("NOT starting", r.stderr)
        self.set_mem(cur=2 * GIB, smax=20 * GIB, running=[("a.service", 17 * GIB)])  # the sum of caps counts, not only the current use
        self.assertEqual(self.run_().returncode, 10)

    def test_wrong_tag_refuses_before_anything_else(self):
        self.stub("git", 'case "$*" in *fetch*) exit 0 ;; *"rev-parse q50-succ-base-1"*) echo a5f41370' + "0" * 32 + ' ;; *) exit 0 ;; esac')
        self.assertEqual(self.run_().returncode, 7)

    def test_bad_arguments_and_clock(self):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home)}
        for a in (["--run"], ["--check", "x"], ["x"]):
            r = subprocess.run(["sh", str(SHD), *a], capture_output=True, text=True, env=env, cwd=REPO)
            self.assertEqual(r.returncode, 2, a)
        self.assertEqual(self.run_(now="abc").returncode, 2)


if __name__ == "__main__":
    unittest.main()
