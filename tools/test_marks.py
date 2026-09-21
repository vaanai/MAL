"""Unit tests for EXP-003 mark schema and as-of-H join (offline)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tools.marks import (
    mark_void_reason,
    parse_mark_tick,
    select_last_as_of,
)


T0 = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


def _mark(
    *,
    t_mark: str,
    price: float = 110.0,
    mint: str = "MintA",
    parent: str = "SigCreate",
    source: str = "rpc_tx",
) -> dict:
    return {
        "schema_version": "observe_mark_v0",
        "type": "outcome_mark",
        "mint": mint,
        "parent_signature": parent,
        "t_mark": t_mark,
        "source": source,
        "price_proxy": price,
    }


class VoidRulesTests(unittest.TestCase):
    def test_ok_mark_parses(self) -> None:
        row = _mark(t_mark="2026-09-20T12:00:01.000+00:00")
        self.assertIsNone(mark_void_reason(row))
        parsed = parse_mark_tick(row)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed[0], "MintA")
        self.assertEqual(parsed[2], 110.0)

    def test_dexscreener_forbidden(self) -> None:
        row = _mark(t_mark="2026-09-20T12:00:01.000+00:00", source="dexscreener")
        self.assertEqual(mark_void_reason(row), "forbidden_source")

    def test_account_state_source_ok(self) -> None:
        row = _mark(
            t_mark="2026-09-20T12:00:01.000+00:00",
            source="account_state",
        )
        self.assertIsNone(mark_void_reason(row))

    def test_missing_parent(self) -> None:
        row = _mark(t_mark="2026-09-20T12:00:01.000+00:00")
        row["parent_signature"] = "UNK"
        self.assertEqual(mark_void_reason(row), "missing_parent_signature")


class AsOfJoinTests(unittest.TestCase):
    def test_last_at_or_before_not_after(self) -> None:
        t1 = datetime(2026, 9, 20, 12, 0, 1, tzinfo=timezone.utc)
        t3 = datetime(2026, 9, 20, 12, 0, 3, tzinfo=timezone.utc)
        ticks = [(t1, 101.0), (t3, 130.0)]
        chosen = select_last_as_of(ticks, T0, 1.0)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen[1], 101.0)
        self.assertEqual(chosen[0], t1)

    def test_tick_after_horizon_is_not_used_for_shorter_window(self) -> None:
        t3 = datetime(2026, 9, 20, 12, 0, 3, tzinfo=timezone.utc)
        chosen = select_last_as_of([(t3, 130.0)], T0, 1.0)
        self.assertIsNone(chosen)

    def test_entry_at_t_is_not_a_horizon_mark(self) -> None:
        chosen = select_last_as_of([(T0, 100.0)], T0, 60.0)
        self.assertIsNone(chosen)

    def test_tick_exactly_at_horizon_ok(self) -> None:
        t60 = datetime(2026, 9, 20, 12, 1, 0, tzinfo=timezone.utc)
        chosen = select_last_as_of([(t60, 120.0)], T0, 60.0)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen[1], 120.0)

    def test_sparse_tick_fills_later_horizons_not_earlier(self) -> None:
        t2 = datetime(2026, 9, 20, 12, 0, 2, tzinfo=timezone.utc)
        ticks = [(t2, 105.0)]
        self.assertIsNone(select_last_as_of(ticks, T0, 1.0))
        h5 = select_last_as_of(ticks, T0, 5.0)
        h60 = select_last_as_of(ticks, T0, 60.0)
        self.assertIsNotNone(h5)
        self.assertIsNotNone(h60)
        assert h5 is not None and h60 is not None
        self.assertEqual(h5[1], 105.0)
        self.assertEqual(h60[1], 105.0)


if __name__ == "__main__":
    unittest.main()
