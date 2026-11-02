"""The s1t job scripts (scripts/ops/s1t-{digest,a3-loop}.sh): POSIX sh, refusals, and the happy paths with PATH stubs for hostname, git and a fake python. Fixtures only; nothing real is started and no
shadow output, ledger or tape is read."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools import s1t_register as R

REPO = Path(__file__).resolve().parent.parent
OPS = REPO / "scripts" / "ops"
DIGEST, LOOP = OPS / "s1t-digest.sh", OPS / "s1t-a3-loop.sh"
GUARD = "1" * 40

HOSTNAME_STUB = '#!/bin/sh\necho "${FAKE_HOST:-mal-research-0}"\n'
GIT_STUB = ('#!/bin/sh\ncase "$*" in "rev-parse HEAD") echo "${FAKE_HEAD:-$FAKE_GUARD}" ;; "rev-parse a3-guard-pin-s1t^{commit}") echo "$FAKE_GUARD" ;; '
            '"status --porcelain --untracked-files=no") printf "%s" "${FAKE_DIRTY:-}" ;; *) exit 1 ;; esac\n')


def write_exec(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def utc(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Base(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.d = Path(self._d.name)
        self.bin = self.d / "bin"
        write_exec(self.bin / "hostname", HOSTNAME_STUB)
        write_exec(self.bin / "git", GIT_STUB)
        self.home = self.d / "home"
        self.s1thome = self.d / "s1thome"
        self.arena = self.s1thome / "data" / "arena"
        self.arena.mkdir(parents=True)

    def env(self, **extra) -> dict:
        e = dict(os.environ, HOME=str(self.home), PATH=f"{self.bin}:{os.environ['PATH']}", S1T_HOME=str(self.s1thome), FAKE_GUARD=GUARD)
        e.update({k: str(v) for k, v in extra.items()})
        return e

    def sh(self, script: Path, *args, cwd=REPO, timeout=60, **env) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", str(script), *args], capture_output=True, text=True, env=self.env(**env), cwd=cwd, timeout=timeout)


class Syntax(unittest.TestCase):
    def test_posix_sh_no_bashisms_no_forbidden_words(self):
        for p in (DIGEST, LOOP):
            subprocess.run(["sh", "-n", str(p)], check=True)
            text = p.read_text()
            self.assertTrue(text.startswith("#!/bin/sh"), p)
            self.assertTrue(os.access(p, os.X_OK), p)
            for bad in ("PIPESTATUS", "[[", "<<<", "declare ", "local ", "source ", "function ", "mapfile", "${BASH", "set -o pipefail"):
                self.assertNotIn(bad, text, f"{p.name}: {bad}")
            self.assertIsNone(re.search(r"\$\{[A-Za-z_]+/", text), p)
            low = text.lower()
            if p == LOOP:  # the bounded enumeration reads the one Helius file by path (Amendment 22 item 9); never printed
                self.assertEqual(low.count("fast-listener/helius"), 1)
                low = low.replace("/var/lib/mal/fast-listener/helius.e" + "nv", "")
            for bad in ("ufw", "sshd", "cloudflared", "private", ".env", "api-key"):
                self.assertNotIn(bad, low, f"{p.name}: {bad}")

    def test_loop_books_pin_is_the_three_key_s1t_file(self):
        doc = {"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v1q50-boostfloor-s1t"], "fees_check_bot_ids": ["h5v1q50-boostfloor-s1t"]}
        want = hashlib.sha256((json.dumps(doc) + chr(10)).encode()).hexdigest()
        self.assertEqual(want, R.A3_BOOKS_SHA256)
        self.assertIn(f"BOOKS_SHA={want}", LOOP.read_text())

    def test_the_scripts_name_s1t_and_research_0_not_s1_or_fast_0(self):
        for p in (DIGEST, LOOP):
            text = p.read_text()
            self.assertIn("mal-research-0", text)
            self.assertNotIn('= mal-fast-0 ]', text)
            code = "\n".join(l for l in text.split("\n") if not l.lstrip().startswith("#"))
            self.assertNotIn("q50s1", code)  # comments cite s1's scripts as the model; no command line does


class Digest(Base):
    def test_once_hashes_only_book_dirs_and_prints_no_counts(self):
        sd = self.arena / "h5-s1t-book-20261106T225500Z"
        sd.mkdir()
        pv = '{"type":"trigger","variant":"pv","pool":"P1","v":1}\n'
        lines = [pv, '{"type":"boost_slice","pool":"P1"}\n', '{"type":"hb","t_ms":1}\n', '{"type":"trigger","variant":"s14","pool":"P2"}\n', pv.replace("P1", "P3")]
        (sd / "h5-shadow-2026-11-07T00.jsonl").write_text("".join(lines[:3]))
        (sd / "h5-shadow-2026-11-07T05.jsonl").write_text("".join(lines[3:]))
        (sd / "h5-shadow-2026-11-08T00.jsonl").write_text(pv)
        for other in ("h5-smoke-s1t-20261105T114500Z", "h5-q50s1-2026-11-05", "h5-q50-2026-10-24", "h5-s1t-book-bad", "h5-s1t-book-2026"):
            (self.arena / other).mkdir()
            (self.arena / other / "h5-shadow-2026-11-07T01.jsonl").write_text(pv)
        r = self.sh(DIGEST, "once", "2026-11-07")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.strip().splitlines()
        self.assertEqual(len(out), 1, out)
        whole = "".join(lines)
        want_pv = hashlib.sha256((pv + pv.replace("P1", "P3")).encode()).hexdigest()
        self.assertIn("DIGEST 2026-11-07 dir h5-s1t-book-20261106T225500Z files 2 first h5-shadow-2026-11-07T00.jsonl last h5-shadow-2026-11-07T05.jsonl", out[0])
        self.assertIn(f"entry_pv_sha256 {want_pv}", out[0])
        self.assertIn(f"boost_slice_sha256 {hashlib.sha256(lines[1].encode()).hexdigest()}", out[0])
        self.assertIn(f"whole_sha256 {hashlib.sha256(whole.encode()).hexdigest()}", out[0])
        self.assertNotIn("lines", out[0])  # sha256 and file names only, as s1's digest: no line count of any kind
        for banned in ("pv_lines", "trigger_lines", "boost_slice_lines", "whole_lines"):
            self.assertNotIn(banned, out[0])
        self.assertNotIn("pool", out[0].replace("whole_sha256", ""))  # no record content is printed
        self.assertIn("files 0", self.sh(DIGEST, "once", "2026-11-09").stdout)

    def test_two_segment_dirs_are_both_digested(self):
        for name in ("h5-s1t-book-20261106T225500Z", "h5-s1t-book-20261120T210000Z"):
            (self.arena / name).mkdir()
            (self.arena / name / "h5-shadow-2026-11-20T21.jsonl").write_text('{"type":"hb"}\n')
        out = self.sh(DIGEST, "once", "2026-11-20").stdout.strip().splitlines()
        self.assertEqual([l.split()[3] for l in out], ["h5-s1t-book-20261106T225500Z", "h5-s1t-book-20261120T210000Z"])

    def test_no_book_dir_is_said_not_hidden(self):
        self.assertIn("no s1t dir", self.sh(DIGEST, "once", "2026-11-07").stdout)

    def test_arguments_host_roots_and_end(self):
        for a in ((), ("once",), ("once", "11-07"), ("loop", "x"), ("bogus",)):
            self.assertEqual(self.sh(DIGEST, *a).returncode, 2, a)
        self.assertEqual(self.sh(DIGEST, "once", "2026-11-07", FAKE_HOST="mal-fast-0").returncode, 6)
        real_home = subprocess.run(["sh", "-c", 'getent passwd "$(id -u)" | cut -d: -f6'], capture_output=True, text=True).stdout.strip()
        self.assertEqual(self.sh(DIGEST, "once", "2026-11-07", S1T_HOME=real_home).returncode, 2)  # Q50's root (the real home's data/arena)
        self.assertEqual(self.sh(DIGEST, "once", "2026-11-07", S1T_HOME="/home/claude/s1home").returncode, 2)
        self.assertEqual(self.sh(DIGEST, "once", "2026-11-07", S1T_HOME="/home/claude/s1tsmoke").returncode, 2)
        self.assertEqual(self.sh(DIGEST, "once", "2026-11-07", S1T_HOME="relative").returncode, 2)
        self.assertEqual(self.sh(DIGEST, "loop", S1T_DIGEST_END=1796085001).returncode, 2)
        r = self.sh(DIGEST, "loop", S1T_DIGEST_END=1)
        self.assertEqual(r.returncode, 0)
        self.assertIn("end reached", r.stdout)


class Loop3(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.d / "repo"  # a fake checkout at the guard tag: the loop only hashes these files and calls a3-daily.sh
        for rel in ("scripts/ops/a3-daily.sh", "tools/a3_halt.py", "tools/a3_redeploy_check.py", "tools/a3_redeploy_pins.json"):
            write_exec(self.repo / rel, "#!/bin/sh\necho FAKE_A3_DAILY\n" if rel.endswith(".sh") else "x\n")
        self.enum = self.d / "enum.txt"
        self.enum.write_text("fixture\n")
        self.py = write_exec(self.d / "fakepy", '#!/bin/sh\nexit "${FAKE_PY_RC:-0}"\n')

    def loop(self, *args, **env):
        base = dict(S1T_A3_PY=self.py, S1T_A3_ENUM_ENV_FILE=self.enum)
        base.update(env)
        return self.sh(LOOP, *args, cwd=self.repo, **base)

    def test_arguments_and_refusals(self):
        soon = utc(time.time() + 120)
        for a in ((), ("loop",), ("bogus", soon), ("loop", "2026-11-17 20:00"), ("loop", "2026-01-01T00:00:00Z"), ("deadman", "2026-12-02T00:00:01Z")):
            with self.subTest(a=a):
                self.assertEqual(self.loop(*a).returncode, 2)
        self.assertEqual(self.loop("deadman", soon, FAKE_HOST="mal-fast-0").returncode, 6)
        self.assertEqual(self.loop("deadman", soon, FAKE_HEAD="2" * 40).returncode, 7)
        self.assertEqual(self.loop("deadman", soon, FAKE_DIRTY=" M tools/a3_halt.py").returncode, 5)
        real_home = subprocess.run(["sh", "-c", 'getent passwd "$(id -u)" | cut -d: -f6'], capture_output=True, text=True).stdout.strip()
        self.assertEqual(self.loop("deadman", soon, S1T_HOME=real_home).returncode, 2)  # Q50's root
        self.assertEqual(self.loop("deadman", soon, S1T_HOME="/home/claude/s1home").returncode, 2)  # s1's root
        for other in ("structure-monitor", "structure-monitor-s1"):
            self.assertEqual(self.loop("deadman", soon, S1T_A3_OUT=self.home / "data" / other).returncode, 2, other)  # another loop's A3_OUT
        self.assertEqual(self.loop("deadman", soon, FAKE_PY_RC=1).returncode, 2)  # the interpreter cannot import the A3 tools
        self.assertEqual(self.loop("loop", soon, S1T_A3_ENUM_ENV_FILE=self.d / "missing").returncode, 2)  # loop mode needs the enumeration env file

    def test_segment_ends_itself_with_exit_0_at_its_end(self):
        end = time.time() + 4
        t0 = time.time()
        r = self.loop("loop", utc(end))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("A3_SEGMENT_END", r.stdout)
        self.assertGreaterEqual(time.time(), int(end))  # it sleeps to END, not out at its last slot
        self.assertLess(time.time() - t0, 30)
        self.assertIn(f"pin a3-guard-pin-s1t = {GUARD}", r.stdout)
        self.assertEqual(len(re.findall(r"^[0-9a-f]{64}  ", r.stdout, re.M)), 4)  # the four pinned files' sha256 are printed for the records
        self.assertNotIn("A3_RUN", r.stdout)
        self.assertFalse((self.home / "data" / "structure-monitor-s1t").exists() and (self.home / "data" / "structure-monitor-s1t" / "a3-applied.jsonl").exists())

    def test_the_enumeration_file_is_not_printed(self):
        self.enum.write_text("FIXTURE_UNIQUE_TOKEN_9d41\n")
        r = self.loop("loop", utc(time.time() + 3))
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("FIXTURE_UNIQUE_TOKEN_9d41", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
