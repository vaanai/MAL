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
ExecStart=/usr/bin/python3 -m tools.arena_live_bridge --config /etc/mal-arena-live/dry.json
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
        self.assertIn("FAIL credential_directive_in_unit", out)
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

    def assert_unit_fails(self, extra: str, label: str) -> None:
        self.unit.write_text(GOOD_UNIT + extra)
        rc, out = self.run_sh()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL " + label, out)
        self.assertNotIn("RESULT PASS", out)

    def test_each_unit_hazard_fails_closed(self):
        self.assert_unit_fails("EnvironmentFile=/etc/mal-arena-live/env\n", "environment_file_in_unit")
        self.assert_unit_fails("EnvironmentFile=-/etc/x\n", "environment_file_in_unit")
        self.assert_unit_fails("Environment=RPC_KEY=abc\n", "key_like_environment_in_unit")
        self.assert_unit_fails("Environment=HELIUS_API_KEY=abc\n", "key_like_environment_in_unit")
        self.assert_unit_fails("BindReadOnlyPaths=/etc/mal-h5\n", "bind_to_key_location_in_unit")
        self.assert_unit_fails("BindPaths=/etc/mal-probe:/k\n", "bind_to_key_location_in_unit")
        for d in ("LoadCredential=a:/b", "LoadCredentialEncrypted=a:/b", "SetCredential=a:b", "ImportCredential=a"):
            self.assert_unit_fails(d + "\n", "credential_directive_in_unit")

    def test_drop_in_header_in_unit_text_fails(self):
        self.assert_unit_fails("\n# /etc/systemd/system/mal-arena-live-bridge-dry.service.d/override.conf\n[Service]\nMemoryMax=1G\n", "drop_in_present_in_unit_text")

    def test_drop_in_dir_present_fails(self):
        dd = self.d / "x.service.d"
        dd.mkdir()
        rc, out = self.run_sh(BRIDGE_DROPIN_DIRS=str(dd))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL drop_in_dir_present", out)

    def test_tier_file_present_fails(self):
        (self.d / "live" / "TIER").write_text("T0")
        rc, out = self.run_sh()
        self.assertEqual(rc, 1)
        self.assertIn("FAIL TIER_exists", out)

    def test_hazard_values_never_echoed(self):
        self.unit.write_text(GOOD_UNIT + "Environment=RPC_KEY=hunter2value\n")
        self.assertNotIn("hunter2value", self.run_sh()[1])


class RunbookTemplateTests(unittest.TestCase):
    """The runbook's own unit template and section 4 verify commands must agree with each other and with the checklist."""

    RB = Path(__file__).resolve().parent.parent / "docs" / "runbooks" / "arena-live-bridge.md"

    def template(self) -> str:
        text = self.RB.read_text()
        i = text.index("```ini\n") + len("```ini\n")
        return text[i:text.index("```", i)]

    def verify_cmds(self) -> list[tuple[str, int]]:
        text = self.RB.read_text()
        i = text.index("## 4. How to verify DRY")
        blk = text[text.index("```sh\n", i) + 6:]
        blk = blk[:blk.index("```")]
        out = []
        for line in blk.splitlines():
            if line.startswith("systemctl cat") and "# expect 0" in line:
                out.append((line.split("|", 1)[1].split("#")[0].strip(), 0))
        return out

    def test_verify_greps_return_zero_on_template(self):
        cmds = self.verify_cmds()
        self.assertGreaterEqual(len(cmds), 4)
        tpl = self.template()
        for cmd, _ in cmds:
            p = subprocess.run(["sh", "-c", cmd], input=tpl, capture_output=True, text=True)
            self.assertEqual(p.stdout.strip(), "0", cmd + " -> " + p.stdout)

    def test_verify_greps_catch_hazards(self):
        tpl = self.template()
        for bad, idx in (("LoadCredential=a:/b\n", 0), ("Environment=X=1\n", 1), ("ExecStart=x --live\n", 2), ("# /etc/systemd/system/u.service.d/o.conf\n", 3)):
            cmd = self.verify_cmds()[idx][0]
            p = subprocess.run(["sh", "-c", cmd], input=tpl + bad, capture_output=True, text=True)
            self.assertNotEqual(p.stdout.strip(), "0", cmd)

    def test_checklist_passes_on_template(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "live").mkdir(), (d / "var").mkdir()
            (d / "unit.txt").write_text(self.template())
            (d / "dry.json").write_text(json.dumps({"mode": "dry", "stop_file": str(d / "var" / "STOP"), "halt_file": str(d / "var" / "HALT")}))
            e = {**os.environ, "BRIDGE_UNIT_TEXT": str(d / "unit.txt"), "BRIDGE_CONFIG": str(d / "dry.json"), "BRIDGE_LIVE_DIR": str(d / "live")}
            p = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, env=e)
            self.assertEqual(p.returncode, 0, p.stdout)


if __name__ == "__main__":
    unittest.main()
