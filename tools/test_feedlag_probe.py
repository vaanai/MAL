"""Offline tests for tools/feedlag_probe.py. Synthetic messages and a fake socket, no network."""

from __future__ import annotations

import asyncio
import json
import math
import unittest

from tools import feedlag_probe as p


def pub_msg(sig, slot, err=None):
    return {"method": "logsNotification", "params": {"result": {
        "context": {"slot": slot}, "value": {"signature": sig, "err": err, "logs": []}}}}


def hel_msg(sig, slot):
    return {"method": "transactionNotification", "params": {"result": {"signature": sig, "slot": slot}}}


class Parse(unittest.TestCase):
    def test_public(self):
        self.assertEqual(p.parse_public(pub_msg("a", 5)), ("a", 5))
        self.assertIsNone(p.parse_public(pub_msg("a", 5, err={"InstructionError": [0, "x"]})))
        self.assertIsNone(p.parse_public({"id": 1, "result": 7}))
        self.assertIsNone(p.parse_public({"method": "logsNotification", "params": {}}))

    def test_helius(self):
        self.assertEqual(p.parse_helius(hel_msg("b", 9)), ("b", 9))
        self.assertIsNone(p.parse_helius({"method": "transactionNotification", "params": {"result": {}}}))
        self.assertIsNone(p.parse_helius(pub_msg("a", 1)))

    def test_requests_are_pumpswap_only(self):
        for req in (p.public_sub_request(), p.helius_sub_request()):
            self.assertEqual(req["jsonrpc"], "2.0")
            self.assertIn(p.PUMPSWAP_PROGRAM, json.dumps(req))
        self.assertEqual(p.helius_sub_request()["params"][0]["accountInclude"], [p.PUMPSWAP_PROGRAM])
        self.assertEqual(p.public_sub_request()["params"][0]["mentions"], [p.PUMPSWAP_PROGRAM])

    def test_redact(self):
        self.assertNotIn("SECRETKEY", p.redact("bad wss://x/?api-key=SECRETKEY&a=1", "SECRETKEY"))
        self.assertNotIn("abc", p.redact("url ?api-key=abc", None))


class Stats(unittest.TestCase):
    def test_percentile(self):
        v = [float(i) for i in range(1, 101)]
        self.assertAlmostEqual(p.percentile(v, 50), 50.5)
        self.assertAlmostEqual(p.percentile(v, 90), 90.1)
        self.assertTrue(math.isnan(p.percentile([], 50)))

    def test_fit_recovers_line_through_quantised_anchors(self):
        # true time = 1000 + 0.4 * slot ; anchors floored to whole seconds
        pts = [(s, float(math.floor(1000 + 0.4 * s))) for s in range(1000, 1400, 7)]
        a, b, sd = p.fit_line(pts)
        self.assertAlmostEqual(b, 0.4, places=2)
        self.assertLess(abs((a + b * 1200) - (1000 + 0.4 * 1200 - 0.5)), 0.2)
        self.assertLess(sd, 0.5)
        self.assertIsNone(p.fit_line(pts[:2]))


class Pairing(unittest.TestCase):
    def test_pairing_and_first_seen(self):
        col = p.Collector()
        # block time t = 100 + 0.4*slot  (slot 10 -> 104.0)
        col.add("public", "s1", 10, 105.0)
        col.add("helius", "s1", 10, 104.5)   # helius first by 0.5
        col.add("helius", "s2", 11, 104.9)
        col.add("public", "s2", 11, 104.7)   # public first by 0.2
        col.add("public", "s3", 12, 106.0)   # public only
        col.add("helius", "s4", 12, 105.0)   # helius only
        col.add("helius", "s4", 12, 105.1)   # duplicate
        anchors = [(0, 100.0), (50, 120.0), (100, 140.0)]
        rep = p.build_report(col, anchors, {})
        sg = rep["signatures"]
        self.assertEqual((sg["seen_by_both"], sg["public_only"], sg["helius_only"]), (2, 1, 1))
        fs = rep["first_seen_share_of_both"]
        self.assertAlmostEqual(fs["helius_first"], 0.5)
        self.assertAlmostEqual(fs["public_first"], 0.5)
        pd = rep["paired_public_minus_helius_receive_s"]
        self.assertEqual(pd["n"], 2)
        self.assertAlmostEqual(pd["p50"], 0.15, places=9)
        self.assertAlmostEqual(rep["block_time_fit"]["slot_seconds"], 0.4, places=9)
        # lag of public_both: s1 105-104 = 1.0 ; s2 104.7-104.4 = 0.3
        self.assertEqual(rep["lag_vs_block_time_s"]["public_both"]["n"], 2)
        self.assertEqual(rep["lag_vs_block_time_s"]["public_all"]["n"], 3)
        self.assertEqual(rep["lag_vs_block_time_s"]["helius_all"]["n"], 3)
        self.assertEqual(rep["duplicates"]["helius"], 1)

    def test_ttl_expiry_finalises_and_bounds_memory(self):
        col = p.Collector(ttl_s=10.0, max_pending=5, max_rows=3)
        for i in range(20):
            col.add("public", f"x{i}", i, 100.0 + i)
        self.assertLessEqual(len(col.pending), 5)
        col.expire(10_000.0)
        self.assertEqual(len(col.pending), 0)
        self.assertLessEqual(len(col.rows), 9)
        self.assertGreater(col.rows_dropped, 0)

    def test_no_anchors_gives_no_lag_but_pairs(self):
        col = p.Collector()
        col.add("public", "a", 1, 2.0)
        col.add("helius", "a", 1, 1.0)
        rep = p.build_report(col, [], {})
        self.assertIsNone(rep["block_time_fit"])
        self.assertEqual(rep["lag_vs_block_time_s"]["public_all"]["n"], 0)
        self.assertEqual(rep["signatures"]["seen_by_both"], 1)


class FakeWS:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, s):
        self.sent.append(s)

    async def recv(self):
        if self.frames:
            return self.frames.pop(0)
        await asyncio.sleep(3600)


class Network(unittest.TestCase):
    def test_subscribe_error_is_fatal_and_recorded(self):
        err = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "plan does not support"}})
        ws = FakeWS([err])
        col, stats, stop = p.Collector(), p.Stats(), asyncio.Event()

        async def go():
            await p.run_source("helius", "wss://x", p.helius_sub_request(), p.parse_helius, col, stats, stop,
                               None, connect=lambda u: ws)

        with self.assertRaises(p.FatalSubscribeError) as cm:
            asyncio.run(go())
        self.assertIn("plan does not support", str(cm.exception))
        self.assertIn("plan does not support", stats.helius_error)

    def test_messages_counted_and_credits_estimated(self):
        frames = [json.dumps({"jsonrpc": "2.0", "id": 1, "result": 77}),
                  json.dumps(hel_msg("a", 3)), json.dumps(hel_msg("b", 3))]
        ws = FakeWS(frames)
        col, stats, stop = p.Collector(), p.Stats(), asyncio.Event()

        async def go():
            task = asyncio.create_task(p.run_source("helius", "wss://x", p.helius_sub_request(), p.parse_helius,
                                                    col, stats, stop, None, connect=lambda u: ws))
            await asyncio.sleep(0.3)
            stop.set()
            await task

        asyncio.run(go())
        self.assertEqual(col.msgs["helius"], 2)
        self.assertEqual(stats.helius_connections, 1)
        self.assertGreater(stats.helius_bytes, 0)
        stats.rpc_calls = 2
        self.assertAlmostEqual(stats.helius_credits_estimate(),
                               stats.helius_bytes / 100_000 * 2 + 1 + 2)

    def test_anchor_loop_samples_old_slots(self):
        col, stats, stop = p.Collector(), p.Stats(), asyncio.Event()
        col.max_slot_seen = 1000
        anchors = p.deque(maxlen=10)
        calls = []

        def fetch(slot, key):
            calls.append(slot)
            return 5000.0 + slot * 0.4

        async def go():
            task = asyncio.create_task(p.anchor_loop(col, stats, anchors, stop, "k", every_s=0.05, fetch=fetch))
            await asyncio.sleep(0.3)
            stop.set()
            await task

        asyncio.run(go())
        self.assertEqual(calls, [1000 - p.ANCHOR_MIN_AGE_SLOTS])  # same slot not re-fetched
        self.assertEqual(len(anchors), 1)


if __name__ == "__main__":
    unittest.main()
