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

    def test_cross_check(self):
        s0 = n4.parse_utc("2026-09-12T03:00:00Z"); ts = int(s0.timestamp())
        tape = n4.TAPE_SOURCE
        self.assertEqual(n4.s0_cross_check(s0, "rpc-oldest-signature", None, 5, False), "walk_capped_needs_tape_source_with_migration_slot")
        self.assertEqual(n4.s0_cross_check(s0, tape, None, 5, False), "walk_capped_needs_tape_source_with_migration_slot")
        self.assertIsNone(n4.s0_cross_check(s0, tape, 123456, 5, False))
        self.assertIsNone(n4.s0_cross_check(s0, "rpc-oldest-signature", None, ts + 100, True))
        self.assertTrue(n4.s0_cross_check(s0, tape, 1, ts + 121, True).startswith("s0_mismatch"))
        self.assertTrue(n4.s0_cross_check(s0, tape, 1, ts - 3600, True).startswith("s0_mismatch"))
        self.assertTrue(n4.s0_cross_check(s0, tape, 1, ts + 40 * 86400, True).startswith("rpc_oldest_not_september"))
        self.assertEqual(n4.s0_cross_check(s0, tape, 1, None, True), "rpc_no_signatures")

    def test_capped_walk_refused_before_simulate(self):
        page = [{"signature": f"s{i}", "blockTime": 1788000000 - i} for i in range(1000)]
        def rpc(m, p):
            assert m == "getSignaturesForAddress", m
            return page
        r = n4.check_one(rpc, "token", "M", "P", "2026-09-12T03:00:00Z", "rpc-oldest-signature", "Token", None)
        self.assertFalse(r["ok"]); self.assertTrue(r["refusal"].startswith("walk_capped"))


if __name__ == "__main__":
    unittest.main()
