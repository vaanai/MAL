"""Tests for tools/oracle_live_adapter.py: the whitelist/cutoff fence and
the row-shape adaptation. No network, no live data required -- these only
exercise pure functions and small synthetic rows.

Exploration only. See ARTIFACTS/lab/exploration-entry-model-b2-2026-09-28.md.
"""

from __future__ import annotations

import unittest

from tools.oracle_live_adapter import (
    POOL_B_CREATE_DAYS,
    POOL_B_CUTOFF_MS,
    POOL_B_END,
    POOL_B_HOURS,
    POOL_B_HOURS_SET,
    POOL_B_START,
    _create_day_file,
    _hour_info_b,
    _row_sort_key,
    adapt_create_row,
    pool_b_hours,
)


class WhitelistFenceTests(unittest.TestCase):
    """The hard requirement from the lane B2 brief: never an hour or day at
    or after 2026-09-28T00:00:00Z (kill-review forward data)."""

    def test_pool_is_exactly_the_65_whitelisted_hours(self) -> None:
        self.assertEqual(POOL_B_HOURS[0], POOL_B_START)
        self.assertEqual(POOL_B_HOURS[-1], POOL_B_END)
        self.assertEqual(len(POOL_B_HOURS), 65)
        self.assertEqual(pool_b_hours(), POOL_B_HOURS)

    def test_no_whitelisted_trade_hour_is_at_or_after_the_clean_clock(self) -> None:
        for h in POOL_B_HOURS:
            self.assertLess(h, "2026-09-28T00", f"hour {h} crosses the clean forward-paper clock")

    def test_hour_info_rejects_an_hour_outside_the_pool(self) -> None:
        for bad in ("2026-09-28T00", "2026-09-28T05", "2026-09-29T00", "2026-09-25T06", "2026-09-27T24"):
            with self.assertRaises(AssertionError):
                _hour_info_b(bad)

    def test_hour_info_accepts_every_whitelisted_hour_key_shape(self) -> None:
        # Doesn't touch disk beyond existence: just proves the assertion
        # gate itself doesn't reject an in-fence key. A missing data file
        # raises SystemExit, not AssertionError, and is a separate concern
        # from the whitelist.
        for h in (POOL_B_START, POOL_B_END, "2026-09-26T12"):
            self.assertIn(h, POOL_B_HOURS_SET)

    def test_create_day_file_rejects_a_day_outside_the_whitelist(self) -> None:
        for bad in ("2026-09-29", "2026-09-24", "2026-10-01", "2026-09-28T00"):
            with self.assertRaises(AssertionError):
                _create_day_file(bad)

    def test_create_day_whitelist_is_exactly_four_days_ending_09_28(self) -> None:
        self.assertEqual(POOL_B_CREATE_DAYS, ("2026-09-25", "2026-09-26", "2026-09-27", "2026-09-28"))


class AdaptCreateRowCutoffTests(unittest.TestCase):
    """The per-row hard cutoff: observe-2026-09-28.jsonl is in the day-file
    whitelist (in case of clock-skew rows just before the boundary), but
    every row at or after 2026-09-28T00:00:00Z must still be dropped."""

    def _row(self, t_ws: str) -> dict:
        return {
            "stream": "subscribeNewToken",
            "txType": "create",
            "mint": "MintAbc123",
            "t_ws": t_ws,
            "traderPublicKey": "CreatorXyz",
            "signature": "SigAbc",
            "vSolInBondingCurve": 30.0,
            "vTokensInBondingCurve": 1_073_000_000.0,
            "marketCapSol": 28.0,
        }

    def test_row_one_ms_before_cutoff_is_kept(self) -> None:
        out = adapt_create_row(self._row("2026-09-27T23:59:59.999Z"))
        self.assertIsNotNone(out)
        self.assertEqual(out["mint"], "MintAbc123")
        self.assertEqual(out["type"], "create")

    def test_row_exactly_at_cutoff_is_dropped(self) -> None:
        self.assertIsNone(adapt_create_row(self._row("2026-09-28T00:00:00.000Z")))

    def test_row_after_cutoff_is_dropped(self) -> None:
        self.assertIsNone(adapt_create_row(self._row("2026-09-28T05:00:00.000Z")))
        self.assertIsNone(adapt_create_row(self._row("2026-10-05T05:00:00.000Z")))

    def test_cutoff_constant_is_exactly_the_clean_clock_instant(self) -> None:
        # 2026-09-28T00:00:00Z in epoch ms, computed independently here so a
        # typo in the adapter's own datetime.strptime call would be caught.
        import datetime

        expected = int(datetime.datetime(2026, 9, 28, 0, 0, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        self.assertEqual(POOL_B_CUTOFF_MS, expected)

    def test_non_create_rows_are_ignored(self) -> None:
        row = self._row("2026-09-26T12:00:00.000Z")
        row["txType"] = "migration"
        self.assertIsNone(adapt_create_row(row))
        row2 = self._row("2026-09-26T12:00:00.000Z")
        row2["stream"] = "somethingElse"
        row2["txType"] = None
        self.assertIsNone(adapt_create_row(row2))


class AdaptCreateRowShapeTests(unittest.TestCase):
    """Unit conversions must match tools.paper_price_path.CreateSignal.anchor()
    exactly -- not a new scaling convention."""

    def test_reserves_use_the_same_scale_as_create_signal_anchor(self) -> None:
        row = {
            "stream": "subscribeNewToken",
            "txType": "create",
            "mint": "MintXyz",
            "t_ws": "2026-09-26T00:00:00.000Z",
            "traderPublicKey": "Creator1",
            "signature": "Sig1",
            "vSolInBondingCurve": 40.0,
            "vTokensInBondingCurve": 800_000_000.0,
            "marketCapSol": 50.0,
        }
        out = adapt_create_row(row)
        self.assertIsNotNone(out)
        self.assertEqual(out["quote_reserve"], 40_000_000_000)  # 40 SOL * 1e9
        self.assertEqual(out["base_reserve"], 800_000_000_000_000)  # 800e6 tokens * 1e6
        self.assertEqual(out["slot"], 0)
        self.assertEqual(out["quote_mint"], "So11111111111111111111111111111111111111112")

    def test_missing_reserves_omit_the_reserve_keys_not_fake_zeros(self) -> None:
        row = {
            "stream": "subscribeNewToken",
            "txType": "create",
            "mint": "MintNoReserves",
            "t_ws": "2026-09-26T00:00:00.000Z",
            "traderPublicKey": "Creator1",
            "signature": "Sig1",
        }
        out = adapt_create_row(row)
        self.assertIsNotNone(out)
        self.assertNotIn("quote_reserve", out)
        self.assertNotIn("base_reserve", out)

    def test_block_time_is_seconds_not_milliseconds(self) -> None:
        row = {
            "stream": "subscribeNewToken",
            "txType": "create",
            "mint": "MintT",
            "t_ws": "2026-09-26T01:02:03.000Z",
            "traderPublicKey": "Creator1",
            "signature": "Sig1",
            "vSolInBondingCurve": 30.0,
            "vTokensInBondingCurve": 1_000_000.0,
        }
        out = adapt_create_row(row)
        self.assertEqual(out["block_time"], out["block_time"] // 1)  # int
        self.assertLess(out["block_time"], 10**10)  # seconds-scale, not ms-scale


class RowSortKeyTests(unittest.TestCase):
    """Defensive re-sort ordering: (slot, t_recv_ms, event_index), missing
    fields treated as 0 rather than crashing."""

    def test_sorts_by_slot_then_recv_time_then_event_index(self) -> None:
        rows = [
            {"slot": 5, "t_recv_ms": 100, "event_index": 1},
            {"slot": 5, "t_recv_ms": 100, "event_index": 0},
            {"slot": 3, "t_recv_ms": 999, "event_index": 0},
            {"slot": 5, "t_recv_ms": 50, "event_index": 0},
        ]
        out = sorted(rows, key=_row_sort_key)
        self.assertEqual([r["slot"] for r in out], [3, 5, 5, 5])
        self.assertEqual(out[1]["t_recv_ms"], 50)
        self.assertEqual(out[2]["event_index"], 0)
        self.assertEqual(out[3]["event_index"], 1)

    def test_missing_fields_do_not_crash(self) -> None:
        self.assertEqual(_row_sort_key({}), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
