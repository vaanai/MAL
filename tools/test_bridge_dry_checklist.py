"""Tests for scripts/ops/bridge-dry-checklist.sh against a fake tree (unit text file, config, key-like file). No host, no systemctl."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ops" / "bridge-dry-checklist.sh"
GOOD_UNIT = """[Service]
User=mal-arena-bridge
ExecStart=/usr/bin/python3 -I -m tools.arena_live_bridge --config /etc/mal-arena-live/dry.json
"""


class ChecklistTests(unittest.TestCase):
    def setUp(self) -> None:
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.d = Path(self._d.name)
        (self.d / "live").mkdir()
        (self.d / "var").mkdir()
        self.unit = self.d / "unit.txt"
        self.unit.write_text(GOOD_UNIT)
        self.cfg = self.d / "dry.json"
        self.write_cfg({})

    def write_cfg(self, over: dict) -> None:
        self.cfg.write_text(json.dumps({"mode": "dry", "stop_file": str(self.d / "var" / "STOP"), "halt_file": str(self.d / "var" / "HALT"), **over}))

    def run_sh(self, **env) -> tuple[int, str]:
        e = {**os.environ, "BRIDGE_UNIT_TEXT": str(self.unit), "BRIDGE_CONFIG": str(self.cfg), "BRIDGE_LIVE_DIR": str(self.d / "live"), **env}
        p = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, env=e)
        return p.returncode, p.stdout

    def test_clean_pass(self):
        rc, out = self.run_sh()
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT PASS", out)
        self.assertNotIn("FAIL", out)

    def test_credential_line_fails_and_is_not_echoed(self):
        self.unit.write_text(GOOD_UNIT + "LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json\n")
        rc, out = self.run_sh()
        self.assertEqual(rc, 1)
        self.assertIn("FAIL credential_lines_in_unit", out)
        self.assertNotIn("/etc/mal-probe/probe-wallet.json", out)

    def test_live_flag_and_live_config_fail(self):
        self.unit.write_text(GOOD_UNIT.replace("dry.json", "dry.json --live"))
        self.assertIn("FAIL execstart_has_--live", self.run_sh()[1])
        self.write_cfg({"mode": "live"})
        self.assertIn("FAIL config_mode_not_dry", self.run_sh()[1])

    def test_live_ok_present_fails(self):
        (self.d / "live" / "LIVE_OK").write_text("")
        rc, out = self.run_sh()
        self.assertEqual(rc, 1)
        self.assertIn("FAIL LIVE_OK_exists", out)

    def test_readable_key_file_fails(self):
        k = self.d / "var" / "wallet-test.json"
        k.write_text("[]")
        rc, out = self.run_sh(BRIDGE_KEY_PATHS=str(k))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL key_file_readable", out)

    def test_stop_path_missing_dir_fails(self):
        self.write_cfg({"stop_file": str(self.d / "nodir" / "STOP")})
        self.assertIn("FAIL stop_file_dir_missing", self.run_sh()[1])
        self.write_cfg({"stop_file": ""})
        self.assertIn("FAIL stop_file_not_set_in_config", self.run_sh()[1])


if __name__ == "__main__":
    unittest.main()
