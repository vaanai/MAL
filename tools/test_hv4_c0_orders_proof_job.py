"""Tests for scripts/ops/hv4-c0-orders-proof-job.sh: the sha checks, the private export, the exit codes and the clean-up, against a throwaway git repo with a stub proof
tool, plus one run of the real tool at this checkout's HEAD. No host, no network, no key, no arena path."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "ops" / "hv4-c0-orders-proof-job.sh"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid"]

STUB = """import os, sys
print("git_sha=" + os.environ.get("PROOF_GIT_SHA", "unset"))
print("bridge_orders_md5=" + "a" * 32)
print("expected_orders_md5=" + "a" * 32)
print("bridge_outcomes_md5=" + "b" * 32)
print("expected_outcomes_md5=" + "b" * 32)
print("RESULT " + os.environ.get("STUB_RESULT", "EQUAL") + " (stub)")
sys.exit(int(os.environ.get("STUB_RC", "0")))
"""


def sh(args, **env):
    e = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"), "NO_FETCH": "1", **env}
    return subprocess.run(["sh", str(SCRIPT), *args], capture_output=True, text=True, env=e)


class JobScript(unittest.TestCase):
    def setUp(self) -> None:
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.d = Path(self._d.name)
        self.repo = self.d / "repo"
        (self.repo / "tools").mkdir(parents=True)
        (self.repo / "observe").mkdir()
        (self.repo / "tools" / "__init__.py").write_text("")
        (self.repo / "observe" / "__init__.py").write_text("")
        (self.repo / "tools" / "hv4_c0_orders_proof.py").write_text(STUB)
        (self.repo / "README.md").write_text("not exported\n")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run([*GIT, "add", "-A"], cwd=self.repo, check=True)
        subprocess.run([*GIT, "commit", "-q", "-m", "stub"], cwd=self.repo, check=True)
        self.sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True).stdout.strip()
        (self.repo / "tools" / "other.py").write_text("x = 1\n")  # a commit without the proof tool
        subprocess.run([*GIT, "rm", "-q", "tools/hv4_c0_orders_proof.py"], cwd=self.repo, check=True)
        subprocess.run([*GIT, "add", "-A"], cwd=self.repo, check=True)
        subprocess.run([*GIT, "commit", "-q", "-m", "no tool"], cwd=self.repo, check=True)
        self.sha_no_tool = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True).stdout.strip()
        self.tmp = self.d / "tmp"
        self.tmp.mkdir()

    def run_job(self, args=None, **env):
        return sh([self.sha] if args is None else args, **{"MAL_REPO": str(self.repo), "PY": sys.executable, "TMPDIR": str(self.tmp), **env})

    def test_pass_prints_the_md5s_and_exports_the_sha(self):
        p = self.run_job()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("git_sha=" + self.sha, p.stdout)  # PROOF_GIT_SHA reaches the tool
        self.assertRegex(p.stdout, r"(?m)^JOB bridge_orders_md5=a{32}$")
        self.assertRegex(p.stdout, r"(?m)^JOB expected_orders_md5=a{32}$")
        self.assertRegex(p.stdout, r"(?m)^JOB RESULT EQUAL")
        blob = subprocess.run(["git", "rev-parse", f"{self.sha}:tools/hv4_c0_orders_proof.py"], cwd=self.repo, capture_output=True, text=True).stdout.strip()
        self.assertIn(f"tool_blob={blob}", p.stdout)
        self.assertEqual(list(self.tmp.iterdir()), [])  # the private export is removed

    def test_only_tools_and_observe_are_exported_and_the_repo_is_untouched(self):
        probe = self.repo / "tools" / "hv4_c0_orders_proof.py"
        probe.write_text("import os as _o\nprint('ls=' + ','.join(sorted(_o.listdir('.'))))\n" + STUB)
        subprocess.run([*GIT, "add", "-A"], cwd=self.repo, check=True)
        subprocess.run([*GIT, "commit", "-q", "-m", "probe"], cwd=self.repo, check=True)
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True).stdout.strip()
        before = subprocess.run([*GIT, "status", "--porcelain"], cwd=self.repo, capture_output=True, text=True).stdout
        p = self.run_job([sha])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ls=observe,tools", p.stdout)
        self.assertEqual(subprocess.run([*GIT, "status", "--porcelain"], cwd=self.repo, capture_output=True, text=True).stdout, before)

    def test_a_diff_exit_code_passes_through(self):
        p = self.run_job(STUB_RC="1", STUB_RESULT="DIFF")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("JOB RESULT DIFF", p.stdout)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_refusals(self):
        for args in ([], ["abc"], [self.sha.upper()], [self.sha[:39]], [self.sha + "0"], ["0" * 40]):
            p = self.run_job(args)
            self.assertEqual(p.returncode, 2, (args, p.stdout))
            self.assertIn("REFUSED", p.stdout)
        p = self.run_job([self.sha_no_tool])
        self.assertEqual(p.returncode, 2)
        self.assertIn("lacks tools/hv4_c0_orders_proof.py", p.stdout)
        p = sh([self.sha], MAL_REPO=str(self.d / "not-a-repo"), PY=sys.executable, TMPDIR=str(self.tmp))
        self.assertEqual(p.returncode, 2)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_an_interpreter_without_numpy_is_refused(self):
        p = self.run_job(PY="/nonexistent/python")
        self.assertEqual(p.returncode, 2)
        self.assertIn("cannot import numpy", p.stdout)

    def test_expected_md5_gate(self):
        self.assertEqual(self.run_job(EXPECT_ORDERS_MD5="a" * 32).returncode, 0)
        p = self.run_job(EXPECT_ORDERS_MD5="c" * 32)
        self.assertEqual(p.returncode, 3)
        self.assertIn("EXPECT_ORDERS_MD5 mismatch", p.stdout)

    def test_script_is_posix_sh_without_bashisms(self):
        text = SCRIPT.read_text()
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        src = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))  # code only, not the header prose
        for bad in ("[[", "PIPESTATUS", "<(", "local ", "function ", "source ", "echo -e"):
            self.assertNotIn(bad, src)
        if shutil.which("dash"):
            self.assertEqual(subprocess.run(["dash", "-n", str(SCRIPT)], capture_output=True).returncode, 0)

    @unittest.skipUnless((REPO / "tools" / "hv4_c0_orders_proof.py").exists(), "tool not in this checkout")
    def test_real_tool_at_head(self):
        have = subprocess.run(["git", "cat-file", "-e", "HEAD:tools/hv4_c0_orders_proof.py"], cwd=REPO, capture_output=True)
        if have.returncode != 0:
            self.skipTest("the tool is not committed at HEAD yet")
        try:
            import numpy  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("numpy missing")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
        p = sh([head], MAL_REPO=str(REPO), PY=sys.executable, TMPDIR=str(self.tmp))
        self.assertEqual(p.returncode, 0, p.stdout[-800:] + p.stderr[-400:])
        self.assertRegex(p.stdout, r"(?m)^JOB bridge_orders_md5=[0-9a-f]{32}$")
        m = re.findall(r"(?m)^JOB (bridge|expected)_orders_md5=([0-9a-f]{32})$", p.stdout)
        self.assertEqual(len({v for _k, v in m}), 1)
        self.assertEqual(list(self.tmp.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
