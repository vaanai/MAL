"""Tests for scripts/research/kill-review-1005-{snapshot,score}.sh.

Synthetic fixtures only. Nothing here touches Oracle or any live file: the ssh hops are replaced by
`bash -c` through the KR_TEST_MODE hooks, and every path lives under a temp dir.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tools.kill_review import WINDOW_START_MS
from tools.test_kill_review import _config, _gate_passing_rows

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts" / "research"
SNAP = SCRIPTS / "kill-review-1005-snapshot.sh"
SCORE = SCRIPTS / "kill-review-1005-score.sh"
COMMON = SCRIPTS / "kill-review-1005-common.sh"
INSTANT = 1791176400  # 2026-10-05T05:00:00Z


def _run(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if not k.startswith(("KR_", "MISCUSI_"))}
    full.update(env)
    return subprocess.run(["bash", str(script)], env=full, capture_output=True, text=True, cwd=REPO, timeout=300)


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


class SyntaxTests(unittest.TestCase):
    def test_bash_n(self) -> None:
        for script in (SNAP, SCORE, COMMON):
            r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{script.name}: {r.stderr}")


class BeforeInstantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "kr"
        self.trace = self.tmp / "ssh-trace"
        # A "ssh" that records any call; the guard must fire before it is ever used.
        self.fake = self.tmp / "fakessh"
        self.fake.write_text(f'#!/bin/sh\necho "$@" >> {self.trace}\n')
        self.fake.chmod(0o755)
        self.out = self.tmp / "out"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _env(self, now: int) -> dict[str, str]:
        return {
            "KR_TEST_MODE": "1",
            "KR_NOW_EPOCH": str(now),
            "KR_ROOT": str(self.root),
            "KR_CORE_CMD": str(self.fake),
            "KR_RESEARCH_CMD": str(self.fake),
            "MISCUSI_OUTPUT_DIR": str(self.out),
        }

    def test_both_refuse_one_second_early_and_touch_nothing(self) -> None:
        for script in (SNAP, SCORE):
            r = _run(script, self._env(INSTANT - 1))
            self.assertNotEqual(r.returncode, 0, script.name)
            self.assertIn("before the review instant", r.stderr)
        self.assertFalse(self.root.exists())
        self.assertFalse(self.trace.exists())
        self.assertFalse(self.out.exists())

    def test_test_hooks_ignored_outside_test_mode(self) -> None:
        if time.time() >= INSTANT:
            self.skipTest("real clock is past the review instant")
        env = self._env(INSTANT + 10)
        del env["KR_TEST_MODE"]
        for script in (SNAP, SCORE):
            r = _run(script, env)
            self.assertEqual(r.returncode, 3, script.name)
        self.assertFalse(self.root.exists())
        self.assertFalse(self.trace.exists())


class EndToEndTests(unittest.TestCase):
    """Snapshot (local `bash -c` hops) then score, on the kill_review synthetic fixtures."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.paper = self.tmp / "paper"
        self.tape = self.tmp / "tape"
        self.creates = self.tmp / "creates"
        for d in (self.paper, self.tape, self.creates):
            d.mkdir()
        rows = _gate_passing_rows("bk", n_days=5, per_day=20)
        (self.paper / "positions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + '{"partial": ')
        (self.paper / "forward-paper.json").write_text(json.dumps(_config([{"id": "bk", "kind": "laya"}])))
        (self.tape / "trades-2026-09-28T00.jsonl").write_text("")
        if shutil.which("zstd"):
            z = subprocess.run(["zstd", "-q", "-c"], input=b"", capture_output=True, check=True)
            (self.tape / "trades-2026-09-28T01.jsonl.zst").write_bytes(z.stdout)
        else:  # hash/copy path is the same; the scorer needs a real zstd frame
            (self.tape / "trades-2026-09-28T01.jsonl").write_text("")
        (self.creates / "observe-2026-09-28.jsonl").write_text("")
        self.restarts = self.tmp / "restarts.jsonl"
        self.restarts.write_text(json.dumps({"t": "2026-09-29T00:00:20Z"}) + "\n")
        self.root = self.tmp / "kr"
        self.out = self.tmp / "published"
        self.env = {
            "KR_TEST_MODE": "1",
            "KR_NOW_EPOCH": str(INSTANT + 60),
            "KR_ROOT": str(self.root),
            "KR_CORE_CMD": "bash -c",
            "KR_RESEARCH_CMD": "bash -c",
            "KR_SRC_PAPER": str(self.paper),
            "KR_SRC_TAPE": str(self.tape),
            "KR_SRC_CREATES": str(self.creates),
            "KR_SRC_RESTARTS": str(self.restarts),
            "KR_MIN_FREE_GB": "0",
            "KR_TAPE_FIRST_HOUR": "2026-09-28T00",
            "KR_TAPE_LAST_HOUR": "2026-09-28T02",
            "KR_CREATES_FIRST_DAY": "2026-09-28",
            "KR_CREATES_LAST_DAY": "2026-09-28",
            "KR_PY": sys.executable,
            "PYTHONPATH": str(REPO),
            "MISCUSI_OUTPUT_DIR": str(self.out),
        }

    def tearDown(self) -> None:
        for p in self.root.rglob("*"):
            try:
                p.chmod(0o755 if p.is_dir() else 0o644)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_snapshot_then_score(self) -> None:
        r = _run(SNAP, self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        snap = self.root / "snap"
        manifest = (snap / "MANIFEST.sha256").read_text()
        for name in ("positions.jsonl", "forward-paper.json", "runner-restarts.jsonl",
                     "tape/trades-2026-09-28T00.jsonl", "tape/trades-2026-09-28T00.jsonl",
                     "creates/observe-2026-09-28.jsonl"):
            self.assertIn("./" + name, manifest)
        # The partial trailing line of the live file was dropped.
        copied = (snap / "positions.jsonl").read_text()
        self.assertTrue(copied.endswith("\n"))
        self.assertNotIn("partial", copied)
        self.assertIn("missing tape hours", r.stdout)  # hour 02 has no file
        self.assertTrue((self.out / "snapshot-MANIFEST.sha256").is_file())

        # A second snapshot must refuse (immutable).
        again = _run(SNAP, self.env)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("already exists", again.stderr)

        # Score.
        s = _run(SCORE, self.env)
        self.assertEqual(s.returncode, 0, s.stdout + s.stderr)
        out = self.root / "out"
        result = json.loads((out / "kill_review.json").read_text())
        self.assertIn("RC=0", s.stdout)
        self.assertTrue((out / "kill_review.md").is_file())
        self.assertEqual((out / "kill_review.rc").read_text().strip(), "0")
        self.assertIn("settlements.jsonl", (out / "OUTPUTS.sha256").read_text())
        self.assertIn("pressure.jsonl", (out / "OUTPUTS.sha256").read_text())
        for name in ("kill_review.json", "kill_review.md", "OUTPUTS.sha256", "snapshot-MANIFEST.sha256"):
            self.assertTrue((self.out / name).is_file(), name)
        self.assertEqual(json.loads((self.out / "kill_review.json").read_text()), result)

        # Single read: a second score refuses.
        again = _run(SCORE, self.env)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("single read", again.stderr)

    def test_score_rejects_tampered_snapshot(self) -> None:
        self.assertEqual(_run(SNAP, self.env).returncode, 0)
        pos = self.root / "snap" / "positions.jsonl"
        pos.parent.chmod(0o755)
        pos.chmod(0o644)
        pos.write_text(pos.read_text() + "\n")
        s = _run(SCORE, self.env)
        self.assertNotEqual(s.returncode, 0)
        self.assertIn("MANIFEST", s.stderr)
        self.assertFalse((self.root / "out" / "kill_review.json").exists())

    def test_score_rejects_added_file(self) -> None:
        self.assertEqual(_run(SNAP, self.env).returncode, 0)
        d = self.root / "snap" / "tape"
        d.chmod(0o755)
        (d / "trades-extra.jsonl").write_text("")
        s = _run(SCORE, self.env)
        self.assertNotEqual(s.returncode, 0)
        self.assertIn("differ from the MANIFEST", s.stderr)


if __name__ == "__main__":
    unittest.main()
