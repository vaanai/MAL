"""Attention score helpers. No tape, no simulator."""

from __future__ import annotations

import unittest

from tools.paper_attention_score import earliest_by_kind, merge_kinds


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


if __name__ == "__main__":
    unittest.main()
