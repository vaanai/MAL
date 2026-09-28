"""Tests for tools/oracle_insample_adapter.py: the hour whitelist/fence and
a one-row schema check against the real on-disk copy (skipped if the copy
is not present on this box).

Exploration only. See ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from tools.oracle_insample_adapter import (
    BACKFILL_C,
    POOL_C_END,
    POOL_C_HOURS,
    POOL_C_HOURS_SET,
    POOL_C_START,
    _hour_info_c,
    pool_c_hours,
)


class WhitelistFenceTests(unittest.TestCase):
    """The hard requirement from the lane B3 brief: an explicit hour
    whitelist plus an assertion, never reaching hour 07 (owned by the
    Oracle live tape pool) or the clean forward-paper clock."""

    def test_pool_is_exactly_the_79_whitelisted_hours(self) -> None:
        self.assertEqual(POOL_C_HOURS[0], POOL_C_START)
        self.assertEqual(POOL_C_HOURS[-1], POOL_C_END)
        self.assertEqual(len(POOL_C_HOURS), 79)
        self.assertEqual(pool_c_hours(), POOL_C_HOURS)

    def test_no_whitelisted_hour_reaches_the_live_tape_pools_hour_07(self) -> None:
        for h in POOL_C_HOURS:
            self.assertLess(h, "2026-09-25T07", f"hour {h} crosses into the Oracle live tape pool")

    def test_no_whitelisted_hour_is_older_than_the_pool_start(self) -> None:
        for h in POOL_C_HOURS:
            self.assertGreaterEqual(h, "2026-09-22T00")

    def test_no_whitelisted_hour_reaches_the_clean_forward_paper_clock(self) -> None:
        for h in POOL_C_HOURS:
            self.assertLess(h, "2026-09-28T00")

    def test_hour_info_rejects_an_hour_outside_the_pool(self) -> None:
        for bad in ("2026-09-21T23", "2026-09-25T07", "2026-09-25T08", "2026-09-28T00", "2026-09-19T01"):
            with self.assertRaises(AssertionError):
                _hour_info_c(bad)

    def test_hour_info_accepts_every_whitelisted_hour_key_shape(self) -> None:
        for h in (POOL_C_START, POOL_C_END, "2026-09-23T12"):
            self.assertIn(h, POOL_C_HOURS_SET)

    def test_pool_c_and_pool_b_hours_never_overlap(self) -> None:
        from tools.oracle_live_adapter import POOL_B_HOURS

        self.assertEqual(set(POOL_C_HOURS) & set(POOL_B_HOURS), set())

    def test_pool_c_and_pool_a_hours_never_overlap(self) -> None:
        from tools.exploration_exits import POOL_HOURS

        self.assertEqual(set(POOL_C_HOURS) & set(POOL_HOURS), set())


@unittest.skipUnless(BACKFILL_C.is_dir(), "Oracle in-sample copy not present on this box")
class OnDiskSchemaTests(unittest.TestCase):
    """Verify the sha256-verified copy actually shares the fast-pool schema
    on one row of each file type, rather than assuming it from the brief."""

    def _first_row(self, path: Path) -> dict:
        proc = subprocess.run(["zstd", "-dc", "-q", str(path)], capture_output=True, check=True)
        first_line = proc.stdout.splitlines()[0]
        return json.loads(first_line)

    def test_trade_row_has_the_fast_pool_field_shape(self) -> None:
        info = _hour_info_c(POOL_C_START)
        row = self._first_row(info["trade"])
        for field in (
            "venue",
            "mint",
            "side",
            "sol_lamports",
            "token_raw",
            "quote_reserve",
            "base_reserve",
            "slot",
            "signature",
            "event_index",
            "t_recv_ms",
            "block_time",
            "quote_mint",
        ):
            self.assertIn(field, row, f"missing field {field!r} in a real trade row")

    def test_create_row_has_the_fast_pool_field_shape(self) -> None:
        info = _hour_info_c(POOL_C_START)
        self.assertIsNotNone(info["create"])
        row = self._first_row(info["create"])
        self.assertEqual(row.get("type"), "create")
        for field in ("mint", "creator", "slot", "block_time", "quote_mint", "signature"):
            self.assertIn(field, row, f"missing field {field!r} in a real create row")

    def test_every_whitelisted_hour_has_both_files_on_disk(self) -> None:
        for h in POOL_C_HOURS:
            info = _hour_info_c(h)
            self.assertTrue(info["trade"].is_file(), h)
            self.assertIsNotNone(info["create"], h)
            self.assertTrue(info["create"].is_file(), h)


if __name__ == "__main__":
    unittest.main()
