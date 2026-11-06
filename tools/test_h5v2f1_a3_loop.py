import os, stat, subprocess, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOOP = Path("scripts/ops/h5v2f1-a3-loop.sh").resolve()
G = "1" * 40
BOOKS = b'{"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v2-bx10-f1"], "fees_check_bot_ids": ["h5v2-bx10-f1"]}'
SHA = "63b7267c2130c007c6a25a39000947c39dbab88bda135b69c2862bf2c3ab0000"


def ex(p, t):
    p.write_text(t)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)


class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        d = Path(self.t.name)
        self.d = d
        (d / "bin").mkdir()
        ex(d / "bin" / "hostname", "#!/bin/sh\necho mal-research-0\n")
        ex(d / "bin" / "git", '#!/bin/sh\ncase "$*" in "rev-parse HEAD") echo "${FAKE_HEAD:-$FAKE_GUARD}";; "rev-parse a3-guard-pin-v2f1^{commit}") echo "$FAKE_GUARD";; "status --porcelain --untracked-files=no") ;; *) exit 1;; esac\n')
        ex(d / "bin" / "py", "#!/bin/sh\nexit 0\n")
        (d / "home").mkdir()
        self.arena = d / "h5" / "data" / "arena"
        self.arena.mkdir(parents=True)

    def run_loop(self, home=None, **extra):
        e = dict(os.environ, HOME=str(self.d / "home"), PATH=f"{self.d/'bin'}:{os.environ['PATH']}", H5V2F1_HOME=str(home or self.d / "h5"),
                 S1T_A3_PY=str(self.d / "bin" / "py"), S1T_A3_ENUM_ENV_FILE=str(self.d / "bin" / "py"), FAKE_GUARD=G)
        e.update(extra)
        end = (datetime.now(timezone.utc) + timedelta(seconds=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return subprocess.run(["sh", str(LOOP), "loop", end], capture_output=True, text=True, env=e, timeout=30)

    def test_sha_is_books_sha(self):
        import hashlib
        self.assertEqual(hashlib.sha256(BOOKS).hexdigest(), SHA)
        self.assertIn(f"BOOKS_SHA={SHA}", LOOP.read_text())

    def test_wrong_tag_exit7(self):
        self.assertEqual(self.run_loop(FAKE_HEAD="2" * 40).returncode, 7)

    def test_s1t_root_refused(self):
        r = self.run_loop(home="/home/claude/s1thome")
        self.assertEqual(r.returncode, 2)
        self.assertIn("never h5v2-bx10-f1's own", r.stderr)

    def test_wrong_books_sha_skips(self):
        (self.arena / "a3-books.json").write_bytes(BOOKS + b"\n")
        r = self.run_loop()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("A3_RUN", r.stdout)

    def test_books_ok_helper_skip_message(self):
        # books_ok is reached only at a slot; assert the SKIP branch text is the sha-compare branch
        t = LOOP.read_text()
        self.assertIn('= "$BOOKS_SHA" ] || { echo "== A3_SKIP', t)


if __name__ == "__main__":
    unittest.main()
