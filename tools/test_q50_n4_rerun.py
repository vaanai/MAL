"""Offline tests for tools/q50_n4_rerun.py (no network, no key)."""
import unittest
from datetime import datetime, timezone

from tools import q50_n4_rerun as n4


class N4Test(unittest.TestCase):
    def test_s0_window(self):
        self.assertIsNone(n4.s0_problem(n4.parse_utc("2026-09-12T03:00:00Z")))
        self.assertTrue(n4.s0_problem(n4.parse_utc("2026-10-01T00:00:00Z")))
        self.assertTrue(n4.s0_problem(n4.parse_utc("2026-10-23T16:12:43Z")))
        self.assertTrue(n4.s0_problem(n4.parse_utc("2026-08-31T23:59:59Z")))

    def test_oldest_walk(self):
        pages = [[{"signature": f"s{i}", "blockTime": 100 - i} for i in range(1000)], [{"signature": "last", "blockTime": 5}]]
        it = iter(pages)
        self.assertEqual(n4.oldest_block_time(lambda m, p: next(it), "pool"), (5, True))
        self.assertEqual(n4.oldest_block_time(lambda m, p: pages[0], "pool", max_pages=2), (100 - 999, False))

    def test_october_refused_before_any_rpc(self):
        def boom(*a, **k):
            raise AssertionError("rpc used")
        r = n4.check_one(boom, "token", "M", "P", "2026-10-23T16:12:43Z", "x", "Token", None)
        self.assertFalse(r["ok"])
        self.assertTrue(r["refusal"].startswith("s0_not_september"))


if __name__ == "__main__":
    unittest.main()
