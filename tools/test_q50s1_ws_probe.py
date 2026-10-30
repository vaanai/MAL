"""tools/q50s1_ws_probe.py: pure parts only (no network, no websockets import). Fixture key files only."""
from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import q50s1_ws_probe as P

REPO = Path(__file__).resolve().parent.parent


class Probe(unittest.TestCase):
    def test_subscription_is_s_stars_and_the_program_is_the_decoder_constant(self):
        src = subprocess.run(["git", "-C", str(REPO), "show", "5c2ab7c64b21358619e37068c6a53e4d183512ea:observe/trade_source.py"], capture_output=True, text=True, check=True).stdout
        fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "logs_subscribe_request")
        ns: dict = {"Any": object}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "s_star", "exec"), ns)  # S*'s own builder, run on fixed inputs
        self.assertEqual(P.subscribe_request(), ns["logs_subscribe_request"](P.PUMPSWAP_PROGRAM, "confirmed", 1))
        self.assertIn(f'PUMPSWAP_PROGRAM = "{P.PUMPSWAP_PROGRAM}"', (REPO / "observe" / "trade_decode.py").read_text())

    def test_summary_arithmetic(self):
        s = P.summary(3600.0, 1000, 36_000_000, 0, True)
        self.assertEqual(s["mb_per_hour"], 36.0)
        self.assertEqual(s["credits_per_hour_at_2_per_0.1MB"], 720.0)
        self.assertEqual(s["credits_per_hour_at_3_per_0.1MB"], 1080.0)
        self.assertEqual(s["credits_per_day_at_2_per_0.1MB"], 17280.0)
        self.assertEqual(s["credits_to_2026-12-01_at_2_per_0.1MB"], 720.0 * 605)
        self.assertNotIn("api-key", str(s))

    def test_key_and_usage_refusals_without_network(self):
        with tempfile.TemporaryDirectory() as d:
            kf = Path(d) / "k"
            kf.write_text("HELIUS_API_KEY=\n")
            r = subprocess.run([sys.executable, "-I", str(REPO / "tools" / "q50s1_ws_probe.py"), "--key-file", str(kf), "--seconds", "60"], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 11)
            kf.write_text("HELIUS_API_KEY=fixture0key-1\n")
            self.assertEqual(P.read_key(str(kf)), "fixture0key-1")
            r = subprocess.run([sys.executable, "-I", str(REPO / "tools" / "q50s1_ws_probe.py"), "--key-file", str(kf), "--seconds", "5"], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 2)
            self.assertNotIn("fixture0key-1", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
