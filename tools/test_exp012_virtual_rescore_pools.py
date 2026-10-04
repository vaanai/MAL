"""collect_pools keeps every PumpSwap pool per mint (not just the first)."""

from __future__ import annotations

import unittest

from tools.exp012_virtual_rescore import collect_pools


def _row(mint: str, pool: str) -> str:
    return f'{{"venue":"pumpswap","mint":"{mint}","side":"buy","pool":"{pool}"}}'


class CollectPoolsTests(unittest.TestCase):
    def test_two_pools_one_mint(self) -> None:
        lines = [_row("M1", "P1"), _row("M1", "P2"), _row("M1", "P1"), _row("M2", "P3")]
        got = collect_pools(lines, {"M1", "M2"})
        self.assertEqual(got, {"M1": {"P1", "P2"}, "M2": {"P3"}})

    def test_mint_filter(self) -> None:
        got = collect_pools([_row("M1", "P1"), _row("X", "PX")], frozenset({"M1"}))
        self.assertEqual(got, {"M1": {"P1"}})

    def test_non_matching_line_ignored(self) -> None:
        self.assertEqual(collect_pools(["garbage"], {"M1"}), {})


if __name__ == "__main__":
    unittest.main()
