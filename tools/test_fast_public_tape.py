"""Fast-host public tape unit. No network."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "scripts" / "mal-core" / "mal-fast-trade-tape.service"
INSTALL = ROOT / "scripts" / "mal-core" / "install-fast-trade-tape.sh"
ORACLE = ROOT / "scripts" / "mal-core" / "mal-trade-tape.service"


class FastPublicTapeTests(unittest.TestCase):
    def test_unit_is_public_tape_only(self) -> None:
        text = UNIT.read_text(encoding="utf-8")
        self.assertIn("MemoryMax=1G", text)
        self.assertIn("Nice=0", text)
        self.assertIn("Restart=always", text)
        self.assertIn("--max-keep-days 7", text)
        self.assertIn("--venues bonding,pumpswap", text)
        self.assertIn("--commitment confirmed", text)
        self.assertIn("--source public_rpc_logs", text)
        self.assertIn("/var/lib/mal/sealed/fast-trades", text)
        self.assertNotIn("/var/lib/mal/sealed/trades", text)
        self.assertNotIn("HELIUS", text)
        self.assertNotIn("EnvironmentFile", text)
        self.assertNotIn("preprocessedSubscribe", text)
        self.assertNotIn("fill_sim", text)
        self.assertNotIn("promotion", text)

    def test_install_does_not_touch_other_units(self) -> None:
        text = INSTALL.read_text(encoding="utf-8")
        self.assertIn("mal-fast-trade-tape.service", text)
        self.assertIn("zstd", text)
        self.assertNotIn("mal-trade-tape", text)
        self.assertNotIn("mal-observe", text)
        self.assertNotIn("mal-fast-pre-create", text)
        self.assertNotIn("mal-fast-early-trade", text)
        self.assertNotIn("mal-fast-create", text)
        self.assertNotIn("rm -rf", text)
        self.assertNotIn("HELIUS_API_KEY=", text)
        self.assertNotIn("fill_sim", text)
        self.assertNotIn("promotion", text)
        oracle = ORACLE.read_text(encoding="utf-8")
        self.assertIn("/var/lib/mal/sealed/trades", oracle)
        self.assertNotIn("max-keep-days", oracle)
        self.assertNotIn("fast-trades", oracle)


if __name__ == "__main__":
    unittest.main()
