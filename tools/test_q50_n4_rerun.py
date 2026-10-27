"""Offline tests for tools/q50_n4_rerun.py (no network, no key)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock
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
        self.assertIsNone(n4.s0_cross_check(s0, tape, 123456, ts - 10, False, ts + 5))
        self.assertIsNone(n4.s0_cross_check(s0, n4.CHAIN_SOURCE, 123456, ts - 120, False, ts - 120))
        self.assertEqual(n4.s0_cross_check(s0, tape, 123456, ts - 10, False), "slot_blocktime_missing")
        self.assertTrue(n4.s0_cross_check(s0, tape, 1, ts, False, ts + 121).startswith("slot_blocktime_mismatch"))
        self.assertTrue(n4.s0_cross_check(s0, tape, 1, ts - 121, False, ts).startswith("capped_oldest_before_s0"))
        self.assertTrue(n4.s0_cross_check(s0, tape, 1, int(n4.SEPT_END.timestamp()), False, ts).startswith("capped_oldest_not_september"))
        self.assertEqual(n4.s0_cross_check(s0, tape, 1, None, False, ts), "rpc_no_signatures")
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

    def _stub_run(self, prog, full=True, ctrl=True):
        def holder_run(rpc, mint, pool, prio):
            return {"token_program": prog, "verdict": {"full_sell_ok": full, "control_reverts_at_base_close": ctrl},
                    "runs": {"full_balance": {"shape": "stub"}}}
        return holder_run

    def _check(self, kind, prog, expect, **kw):
        s0 = n4.parse_utc("2026-09-12T03:00:00Z"); ts = int(s0.timestamp())
        rpc = lambda m, p: [{"signature": "a", "blockTime": ts}]
        with tempfile.TemporaryDirectory() as d, mock.patch.object(n4.ss, "holder_run", self._stub_run(prog, **kw)):
            return n4.check_one(rpc, kind, "M" * 8, "P", "2026-09-12T03:00:00Z", "rpc-oldest-signature", expect, Path(d))

    def test_pass_path_both_programs(self):
        self.assertTrue(self._check("token", "token", "Token")["ok"])
        self.assertTrue(self._check("token2022", "token-2022", "Token-2022")["ok"])

    def test_fail_paths(self):
        self.assertFalse(self._check("token", "token-2022", "Token")["ok"])
        self.assertFalse(self._check("token2022", "token", "Token-2022")["ok"])
        self.assertFalse(self._check("token", "token", "Token", full=False)["ok"])
        self.assertFalse(self._check("token", "token", "Token", ctrl=False)["ok"])

    def test_capped_walk_uses_getblocktime(self):
        s0 = n4.parse_utc("2026-09-12T03:00:00Z"); ts = int(s0.timestamp())
        page = [{"signature": f"s{i}", "blockTime": ts + 1000 - i} for i in range(1000)]
        def mk(slot_time):
            def rpc(m, p):
                if m == "getBlockTime":
                    return slot_time
                return page
            return rpc
        with tempfile.TemporaryDirectory() as d, mock.patch.object(n4.ss, "holder_run", self._stub_run("token")):
            ok = n4.check_one(mk(ts + 30), "token", "M" * 8, "P", "2026-09-12T03:00:00Z", n4.TAPE_SOURCE, "Token", Path(d), 99)
            self.assertTrue(ok["ok"], ok)
            bad = n4.check_one(mk(ts + 500), "token", "M" * 8, "P", "2026-09-12T03:00:00Z", n4.TAPE_SOURCE, "Token", Path(d), 99)
            self.assertFalse(bad["ok"]); self.assertTrue(bad["refusal"].startswith("slot_blocktime_mismatch"))
            none = n4.check_one(mk(None), "token", "M" * 8, "P", "2026-09-12T03:00:00Z", n4.TAPE_SOURCE, "Token", Path(d), 99)
            self.assertEqual(none["refusal"], "slot_blocktime_missing")


if __name__ == "__main__":
    unittest.main()
