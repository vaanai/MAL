"""scripts/ops/q50s1-ws-launch.py: the key is added inside the process only (QP on #1107). Fixtures only: a fixture key file and a fake tools/h5_shadow.py; the real key file is
never read and S*'s h5_shadow is never imported."""
from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCH = REPO / "scripts" / "ops" / "q50s1-ws-launch.py"
KEY = "fixture0key-ABCdef123"
BASE = "wss://mainnet.helius-rpc.com/"
WS1 = "wss://api.mainnet-beta.solana.com"
SHADOW_ARGS = ["--fresh-forward", "ARENA", "--q-star-sol", "50", "--out-dir", "/x/h5-q50s1-2026-11-05", "--sockets", "3", "--syn-sockets", "1", "--ws-url", WS1, "--ws-url", BASE]

spec = importlib.util.spec_from_file_location("q50s1_ws_launch", LAUNCH)
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

FAKE_SHADOW = r'''import logging, os, sys
cmd = open("/proc/self/cmdline", "rb").read().decode(errors="replace")
print("CMDLINE_HAS_KEY" if os.environ["FIXTURE_KEY"] in cmd else "CMDLINE_CLEAN")
print("ENVIRON_HAS_KEY" if any(os.environ["FIXTURE_KEY"] in v for v in os.environ.values() if v != os.environ["FIXTURE_KEY"]) else "ENVIRON_CLEAN")
print("ARGV_KEYED" if sys.argv[-1].endswith("?api-key=" + os.environ["FIXTURE_KEY"]) and sys.argv[-2] == "--ws-url" else "ARGV_NOT_KEYED")
print("ARGV0", os.path.basename(sys.argv[0]), "NAME", __name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logging.getLogger("mal.h5_shadow").warning("feed error X: %s", sys.argv[-1])
print("stdout copy " + sys.argv[-1])
if __name__ == "__main__":
    sys.exit(int(os.environ.get("FIXTURE_RC", "0")))
'''


class Fixture(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.d = Path(self._d.name)
        self.src = self.d / "sstar"
        (self.src / "tools").mkdir(parents=True)
        (self.src / "tools" / "__init__.py").write_text("")
        (self.src / "tools" / "h5_shadow.py").write_text(FAKE_SHADOW)
        self.keyfile = self.d / "key"
        self.keyfile.write_text(f"OTHER=1\nHELIUS_API_KEY={KEY}\n")

    def run_launch(self, *own, args=SHADOW_ARGS, rc="0"):
        env = dict(os.environ, FIXTURE_KEY=KEY, FIXTURE_RC=rc)
        return subprocess.run([sys.executable, "-I", str(LAUNCH), "--src", str(self.src), "--key-file", str(self.keyfile), "--keyed-url", BASE, *own, "--", *args],
                              capture_output=True, text=True, env=env, timeout=60)


class ReadKey(Fixture):
    def test_forms(self):
        for text, want in ((f"HELIUS_API_KEY={KEY}\n", KEY), (f'export HELIUS_API_KEY="{KEY}"\n', KEY), (f"HELIUS_API_KEY='{KEY}'\r\n", KEY), ("HELIUS_API_KEY=\n", None),
                           ("HELIUS_API_KEY=a b c d e f g h\n", None), ("HELIUS_API_KEY=ab$(id)xyz\n", None), ("HELIUS_API_KEY=short\n", None), ("OTHER=1\n", None)):
            with self.subTest(text=text):
                self.keyfile.write_text(text)
                self.assertEqual(L.read_key(str(self.keyfile)), want)
        self.assertIsNone(L.read_key(str(self.d / "missing")))


class KeyedArgv(unittest.TestCase):
    def test_exactly_one_ws_url_value_is_keyed(self):
        out = L.keyed_argv(SHADOW_ARGS, BASE, KEY)
        self.assertEqual(out[:-1], SHADOW_ARGS[:-1])
        self.assertEqual(out[-1], f"{BASE}?api-key={KEY}")

    def test_refusals(self):
        for args, url in ((SHADOW_ARGS + ["--ws-url", BASE], BASE), ([a for a in SHADOW_ARGS if a != BASE][:-1], BASE), ([BASE] + SHADOW_ARGS[:-2], BASE),
                          (SHADOW_ARGS[:-1] + ["wss://x/?api-key=1"], BASE), (SHADOW_ARGS, BASE + "?api-key=k"), (SHADOW_ARGS, "ws://x/"), (SHADOW_ARGS + ["--rpc-url", "https://u:p@x"], BASE)):
            with self.subTest(args=args, url=url):
                with self.assertRaises(ValueError):
                    L.keyed_argv(args, url, KEY)


class Redact(unittest.TestCase):
    def test_wrapper(self):
        buf = io.StringIO()
        w = L.Redacting(buf)
        w.write(f"url={BASE}?api-key={KEY}&x=1 and API_KEY={KEY}\n")
        self.assertNotIn(KEY, buf.getvalue())
        self.assertIn("api-key=REDACTED&x=1", buf.getvalue())


class Run(Fixture):
    def test_dry_prints_redacted_and_runs_nothing(self):
        r = self.run_launch("--dry")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"--ws-url {BASE}?api-key=REDACTED", r.stdout)
        self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertNotIn("CMDLINE", r.stdout)

    def test_real_run_keys_only_the_in_process_argv_and_redacts_every_output(self):
        r = self.run_launch(rc="7")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 7, out)  # h5_shadow's own exit code
        self.assertIn("CMDLINE_CLEAN", r.stdout)  # /proc/self/cmdline (ps, systemctl status) has no key
        self.assertIn("ENVIRON_CLEAN", r.stdout)
        self.assertIn("ARGV_KEYED", r.stdout)  # sys.argv inside the process does
        self.assertIn("ARGV0 h5_shadow.py NAME __main__", r.stdout)  # run as `python -m tools.h5_shadow` runs it
        self.assertNotIn(KEY, out)
        self.assertIn("feed error X: wss://mainnet.helius-rpc.com/?api-key=REDACTED", r.stderr)
        self.assertIn("stdout copy wss://mainnet.helius-rpc.com/?api-key=REDACTED", r.stdout)

    def test_bad_key_or_src_refuses_before_running(self):
        self.keyfile.write_text("HELIUS_API_KEY=\n")
        r = self.run_launch()
        self.assertEqual(r.returncode, 11)
        self.assertNotIn("CMDLINE", r.stdout)
        self.keyfile.write_text(f"HELIUS_API_KEY={KEY}\n")
        (self.src / "tools" / "h5_shadow.py").unlink()
        self.assertEqual(self.run_launch().returncode, 2)

    def test_usage(self):
        for argv in ([], ["--src", "x"], ["--src", "x", "--key-file", "y", "--"], ["--bogus", "--"]):
            with self.subTest(argv=argv):
                r = subprocess.run([sys.executable, "-I", str(LAUNCH), *argv], capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
