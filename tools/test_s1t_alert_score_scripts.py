"""scripts/ops/s1t-a3-alert.sh and scripts/ops/s1t-score.sh: POSIX sh, refusals, pins, and the happy paths with PATH stubs for hostname, git, python3 and a fake python. Fixtures only; nothing real is started,
no shadow output or ledger is read, no worktree is added to the real repo."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OPS = REPO / "scripts" / "ops"
ALERT, SCORE = OPS / "s1t-a3-alert.sh", OPS / "s1t-score.sh"
COMMIT = "5" * 40
SEG1, SEG2 = "h5-s1t-book-20261106T225500Z", "h5-s1t-book-20261120T210000Z"
INHERITED = ("tools/arena_h5_score.py", "tools/arena_h5_score_q50.py", "tools/arena_h5_score_q50s.py", "tools/arena_h5_q50s_instant.py", "tools/arena_h5_q50_instant.py", "tools/arena_h5_score_a2.py")
S1T_FILES = ("tools/arena_h5_score_s1t.py", "tools/arena_h5_s1t_instant.py")
GIT_STUB = ('#!/bin/sh\nif [ "$1" = "-C" ]; then shift 2; fi\ncase "$1" in cat-file) exit 0 ;; rev-parse) echo "${FAKE_HEAD:-' + COMMIT + '}" ;; status) printf "%s" "${FAKE_DIRTY:-}" ;; '
            'worktree) exit 1 ;; esac\nexit 0\n')


def write_exec(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def utc(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Syntax(unittest.TestCase):
    def test_posix_sh_no_bashisms_no_forbidden_words(self):
        for p in (ALERT, SCORE):
            subprocess.run(["sh", "-n", str(p)], check=True)
            text = p.read_text()
            self.assertTrue(text.startswith("#!/bin/sh"), p)
            self.assertTrue(os.access(p, os.X_OK) or p == SCORE, p)
            for bad in ("PIPESTATUS", "[[", "<<<", "declare ", "local ", "source ", "function ", "mapfile", "${BASH", "set -o pipefail"):
                self.assertNotIn(bad, text, f"{p.name}: {bad}")
            self.assertIsNone(re.search(r"\$\{[A-Za-z_]+/", text), p)
            low = text.lower()
            for bad in ("ufw", "sshd", "cloudflared", "private", ".env", "api-key", "helius"):
                self.assertNotIn(bad, low, f"{p.name}: {bad}")
            self.assertIn("mal-research-0", text)
            self.assertNotIn("= mal-fast-0 ]", text)

    def test_the_inherited_pins_equal_the_files_of_this_checkout(self):
        pins = dict(re.findall(r"(tools/[a-z0-9_]+\.py)=([0-9a-f]{64})", SCORE.read_text()))
        self.assertEqual(sorted(pins), sorted(INHERITED))
        for f, want in pins.items():
            self.assertEqual(sha(REPO / f), want, f)

    def test_the_alert_tool_pin_is_the_alert_1540_tool(self):
        self.assertIn(f"TOOL_SHA={sha(REPO / 'tools/a3_applied_alert.py')}", ALERT.read_text())


class Base(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.d = Path(self._d.name)
        self.bin = self.d / "bin"
        write_exec(self.bin / "hostname", '#!/bin/sh\necho "${FAKE_HOST:-mal-research-0}"\n')
        self.home = self.d / "home"
        self.s1thome = self.d / "s1thome"
        self.arena = self.s1thome / "data" / "arena"
        self.arena.mkdir(parents=True)

    def env(self, **extra) -> dict:
        e = dict(os.environ, HOME=str(self.home), PATH=f"{self.bin}:{os.environ['PATH']}", S1T_HOME=str(self.s1thome))
        e.update({k: str(v) for k, v in extra.items()})
        return e

    def sh(self, script: Path, *args, timeout=60, **env) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", str(script), *args], capture_output=True, text=True, env=self.env(**env), timeout=timeout)


class Alert(Base):
    def test_arguments_host_and_own_files(self):
        soon = utc(time.time() + 120)
        for a in ((), ("2026-11-17 12:00",), (soon, "x"), ("2026-01-01T00:00:00Z",), ("2026-12-02T00:00:01Z",)):
            with self.subTest(a=a):
                self.assertEqual(self.sh(ALERT, *a).returncode, 2)
        self.assertEqual(self.sh(ALERT, soon, FAKE_HOST="mal-fast-0").returncode, 6)
        for other in ("structure-monitor", "structure-monitor-s1"):
            self.assertEqual(self.sh(ALERT, soon, S1T_A3_OUT=self.home / "data" / other).returncode, 2, other)
        for other in ("a3-alert/state.json", "a3-alert-s1/state.json"):
            self.assertEqual(self.sh(ALERT, soon, S1T_A3_ALERT_STATE=self.home / "data" / other).returncode, 2, other)
        self.assertEqual(self.sh(ALERT, soon, S1T_ALERT_EVERY="x").returncode, 2)

    def test_clean_run_ends_with_exit_0_and_the_first_alert_with_exit_3(self):
        out = self.home / "data" / "structure-monitor-s1t"
        out.mkdir(parents=True)
        (out / "a3-applied.jsonl").write_text("")
        write_exec(self.bin / "python3", "#!/bin/sh\nexit 0\n")
        r = self.sh(ALERT, utc(time.time() + 3), S1T_ALERT_EVERY=1)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("end reached", r.stdout)
        write_exec(self.bin / "python3", "#!/bin/sh\nexit 3\n")
        r = self.sh(ALERT, utc(time.time() + 30), S1T_ALERT_EVERY=1)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("A3_ALERT_JOB exit 3", r.stdout)


class Score(Base):
    def setUp(self):
        super().setUp()
        write_exec(self.bin / "git", GIT_STUB)
        self.src = self.home / "data" / "arena-src" / f"s1t-score-{COMMIT[:8]}"
        for f in (*INHERITED, *S1T_FILES):
            (self.src / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / f, self.src / f)
        self.py = write_exec(self.d / "fakepy", '#!/bin/sh\ncase "$*" in *--dry-run*) echo "COUNTS 123"; exit "${FAKE_DRY_RC:-0}" ;; esac\nexit "${FAKE_PY_RC:-0}"\n')
        for n in (SEG1, SEG2):
            (self.arena / n).mkdir()

    def score(self, mode, *dirs, **env):
        base = dict(S1T_TOOL_COMMIT=COMMIT, S1T_PIN_WRAPPER_SHA256=sha(REPO / S1T_FILES[0]), S1T_PIN_INSTANT_SHA256=sha(REPO / S1T_FILES[1]), S1T_PY=self.py, S1T_REPO=REPO)
        base.update(env)
        args = [x for n in (dirs or (SEG1, SEG2)) for x in ("--shadow-dir", str(self.arena / n) if "/" not in n else n)]
        return self.sh(SCORE, mode, *args, **base)

    def test_check_passes_with_both_segment_dirs_and_runs_nothing(self):
        r = self.score("check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("pinned sha256 ok", r.stdout)
        self.assertIn(f"-m tools.arena_h5_score_s1t score --arena-root {self.arena} --shadow-dir {self.arena / SEG1} --shadow-dir {self.arena / SEG2}", r.stdout)
        self.assertFalse((self.home / "data" / "s1t-ops").exists())

    def test_the_three_pins_are_required_and_format_checked(self):
        for k, bad in (("S1T_TOOL_COMMIT", ""), ("S1T_TOOL_COMMIT", "abc"), ("S1T_TOOL_COMMIT", "Z" * 40), ("S1T_PIN_WRAPPER_SHA256", ""), ("S1T_PIN_WRAPPER_SHA256", "0" * 63),
                       ("S1T_PIN_INSTANT_SHA256", "g" * 64)):
            self.assertEqual(self.score("check", **{k: bad}).returncode, 2, f"{k}={bad!r}")

    def test_a_pin_that_differs_or_a_bad_worktree_is_refused(self):
        self.assertEqual(self.score("check", S1T_PIN_WRAPPER_SHA256="0" * 64).returncode, 2)
        (self.src / INHERITED[0]).write_text("tampered\n")
        self.assertEqual(self.score("check").returncode, 2)
        shutil.copy(REPO / INHERITED[0], self.src / INHERITED[0])
        self.assertEqual(self.score("check", FAKE_HEAD="6" * 40).returncode, 7)
        self.assertEqual(self.score("check", FAKE_DIRTY=" M tools/x.py").returncode, 7)
        self.assertEqual(self.score("check", FAKE_PY_RC=1).returncode, 2)  # the interpreter cannot import the wrapper

    def test_only_book_out_dirs_under_the_s1t_root(self):
        smoke = "/home/claude/s1tsmoke/data/arena/h5-smoke-s1t-20261105T114500Z"
        for d in (smoke, str(self.arena / "h5-smoke-s1t-20261105T114500Z"), str(self.arena / "h5-q50s1-2026-11-05"), str(self.arena / "h5-s1t-book-2026"), str(self.d / SEG1),
                  str(self.arena / ".." / SEG1)):
            self.assertEqual(self.score("check", d).returncode, 2, d)
        self.assertEqual(self.sh(SCORE, "check").returncode, 2)  # no dir
        real_home = subprocess.run(["sh", "-c", 'getent passwd "$(id -u)" | cut -d: -f6'], capture_output=True, text=True).stdout.strip()
        self.assertEqual(self.score("check", S1T_HOME=real_home).returncode, 2)
        self.assertEqual(self.score("check", S1T_HOME="/home/claude/s1home").returncode, 2)

    def test_host_modes_and_end_bounds(self):
        self.assertEqual(self.score("dry", FAKE_HOST="mal-fast-0").returncode, 6)
        self.assertEqual(self.score("bogus").returncode, 2)
        self.assertEqual(self.score("loop", S1T_SCORE_END=1796086801).returncode, 2)
        self.assertEqual(self.score("loop").returncode, 2)  # the default END is more than 14 d away

    def test_dry_discards_the_output_and_reports_only_the_exit_code(self):
        r = self.score("dry")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("COUNTS", r.stdout + r.stderr)
        self.assertIn("dry score rc=0", r.stdout)
        self.assertEqual(self.score("dry", FAKE_DRY_RC=3).returncode, 1)

    def test_loop_ends_itself_with_exit_0_at_its_end_and_waits_for_a_missing_dir(self):
        end = time.time() + 4
        r = self.score("loop", S1T_SCORE_END=int(end))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("end reached", r.stdout)
        self.assertGreaterEqual(time.time(), int(end))
        shutil.rmtree(self.arena / SEG2)
        r = self.score("loop", S1T_SCORE_END=int(time.time() + 3), S1T_DIR_WAIT_S=0)
        self.assertEqual(r.returncode, 2)
        self.assertIn("does not exist", r.stderr)


if __name__ == "__main__":
    unittest.main()
