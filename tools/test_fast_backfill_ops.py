"""Ops bounds for the fast-box backfill. No network."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "mal-core"


class FastBackfillOpsTests(unittest.TestCase):
    def test_fast_unit_stays_off_the_live_runner(self) -> None:
        text = (SCRIPTS / "mal-fast-backfill.service").read_text(encoding="utf-8")
        score = (SCRIPTS / "mal-fast-oos-score.service").read_text(encoding="utf-8")
        script = (SCRIPTS / "fast-backfill.sh").read_text(encoding="utf-8")
        blob = text + score + script
        for banned in ("forward_paper", "promotion_pnls", "HARD_MAX", "CEILING_", "ForwardEngine"):
            self.assertNotIn(banned, blob)
        self.assertIn("CPUQuota=400%", text)
        self.assertIn("Nice=10", text)
        self.assertIn("MemoryMax=6G", text)
        self.assertIn("--until 2026-09-22T00:00:00Z", script)
        self.assertIn("--credit-cap", script)
        self.assertIn("2000000", script)
        self.assertIn("--rps 22", script)
        self.assertIn("--lookup-rps 3", script)
        self.assertIn("--max-bytes", script)
        self.assertIn("42949672960", script)
        self.assertIn("0.30", script)

    def test_oracle_stops_where_the_fast_box_starts(self) -> None:
        boundary = (
            SCRIPTS / "host-limits" / "mal-pump-backfill.service.d" / "zz-oos-boundary.conf"
        ).read_text(encoding="utf-8")
        self.assertIn("MAL_BACKFILL_HOURS=79", boundary)
        self.assertIn("2026-09-22T00:00Z", boundary)

    def test_scorer_uses_the_frozen_module(self) -> None:
        script = (SCRIPTS / "fast-oos-score.sh").read_text(encoding="utf-8")
        self.assertIn("tools.migrate_direct_oos oos", script)
        self.assertIn("MemoryMax=4G", (SCRIPTS / "mal-fast-oos-score.service").read_text(encoding="utf-8"))
        self.assertIn("CPUQuota=100%", (SCRIPTS / "mal-fast-oos-score.service").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
