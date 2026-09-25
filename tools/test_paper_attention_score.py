"""Attention score helpers. No tape, no simulator."""

from __future__ import annotations

import unittest

from tools.paper_attention_score import causal_entry_ms, earliest_by_kind, merge_kinds


class GroupTests(unittest.TestCase):
    def test_earliest_wins(self) -> None:
        rows = [
            {"kind": "dex_boost", "mint": "A", "t_first_ms": 20},
            {"kind": "dex_boost", "mint": "A", "t_first_ms": 10},
            {"kind": "pump_live", "mint": "A", "t_first_ms": 5},
        ]
        by_kind = earliest_by_kind(rows)
        self.assertEqual(by_kind["dex_boost"]["A"]["t_first_ms"], 10)
        merged = merge_kinds(by_kind, ("dex_boost", "pump_live"))
        self.assertEqual(merged["A"]["kind"], "pump_live")
        self.assertEqual(merged["A"]["t_first_ms"], 5)


class CausalEntryTests(unittest.TestCase):
    def test_first_seen_after_print_keeps_first_seen(self) -> None:
        self.assertEqual(causal_entry_ms(200, 100), 200)

    def test_never_enters_before_first_print(self) -> None:
        paid_at = 50
        t_first = 80
        first_print = 120
        # Vendor paid_at is not an argument; using it as t_first would still clamp to print.
        self.assertEqual(causal_entry_ms(paid_at, first_print), first_print)
        self.assertEqual(causal_entry_ms(t_first, first_print), first_print)

    def test_no_print_uses_first_seen_not_vendor(self) -> None:
        self.assertEqual(causal_entry_ms(80, None), 80)


if __name__ == "__main__":
    unittest.main()
