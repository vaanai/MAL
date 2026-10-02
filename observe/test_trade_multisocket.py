"""Redundant public logsSubscribe sockets. Fake websockets, no network."""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from websockets.exceptions import InvalidMessage

from observe import trade_source
from observe.fake_ws import (
    FIXTURES,
    T0,
    FakeNetwork,
    collect,
    fixture_frames,
    fixture_line,
    logs_frame,
    notice_dump,
)
from observe.trade_source import (
    LogsSubscribeSource,
    MergedStats,
    MultiSocketLogsSource,
    SeenSet,
    SocketStats,
)
from observe.trade_store import CompletenessMonitor
from observe.trade_tape import build_source

A, B = "wss://a.test", "wss://b.test"
LOGS = [fixture_line("pump_trade_event.b64")]


def msg(t_ms: int, sig: str, slot: int = 100):
    return ("msg", T0 + t_ms, logs_frame(sig, slot, LOGS))


def multi(urls=(A, B), sockets=2, **kw) -> MultiSocketLogsSource:
    kw.setdefault("stagger_s", 0.0)
    return MultiSocketLogsSource(ws_urls=urls, sockets=sockets, **kw)


class SingleSocketUnchangedTests(unittest.IsolatedAsyncioTestCase):
    async def test_n1_is_the_old_source_and_output_is_byte_identical(self) -> None:
        source = build_source("public_rpc_logs", A, "confirmed", "", ("P",), sockets=1, ws_urls=[A])
        self.assertIs(type(source), LogsSubscribeSource)
        net = FakeNetwork({A: [fixture_frames()]})
        with net.patched():
            notes = await collect(source, 0.3)
        lines = notice_dump(notes)
        lines.append(
            f"stats notes={source.stats.notes} failed={source.stats.failed_notes} "
            f"reconnects={source.stats.reconnects} jumps={source.stats.slot_jumps} "
            f"last={source.stats.last_slot}"
        )
        golden = (FIXTURES / "single_socket_golden.jsonl").read_text(encoding="utf-8")
        # Golden was produced by the pre-change code on the same fixture stream.
        self.assertEqual("\n".join(lines) + "\n", golden)

    def test_build_source_picks_multi_only_above_one(self) -> None:
        two = build_source("public_rpc_logs", A, "confirmed", "", ("P",), sockets=2, ws_urls=[A])
        self.assertIsInstance(two, MultiSocketLogsSource)
        self.assertEqual(two.ws_urls, [A, A])


class MergeTests(unittest.IsolatedAsyncioTestCase):
    async def test_dropped_window_on_one_socket_is_filled_by_the_other(self) -> None:
        sigs = [f"s{i}" for i in range(1, 7)]
        a_steps = []
        for i, sig in enumerate(sigs):
            if sig in ("s3", "s4"):  # socket A misses these
                continue
            a_steps += [msg(10 * i, sig, 100 + i), ("sleep", 0.01)]
        b_steps = [("sleep", 0.005)]
        for i, sig in enumerate(sigs):
            b_steps += [msg(10 * i + 5, sig, 100 + i), ("sleep", 0.01)]
        net = FakeNetwork({A: [a_steps], B: [b_steps]})
        source = multi()
        with net.patched():
            notes = await collect(source, 3.0)
        got = [n.signature for n in notes]
        self.assertEqual(sorted(got), sorted(sigs))
        self.assertEqual(len(got), len(set(got)))
        # s3 and s4 only B had; the rest were duplicates.
        a, b = source.socket_stats
        self.assertEqual(source.stats.dedup_dropped, 4)
        self.assertEqual(a.unique + b.unique, 6)
        self.assertEqual(b.unique, 2)
        self.assertEqual((a.notes, b.notes), (4, 6))

    async def test_same_notification_twice_keeps_earliest_receive_time(self) -> None:
        a_steps = [("sleep", 0.1), msg(100, "dup")]
        b_steps = [msg(40, "dup")]
        net = FakeNetwork({A: [a_steps], B: [b_steps]})
        source = multi()
        with net.patched():
            notes = await collect(source, 0.3)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].t_recv_ms, T0 + 40)
        self.assertEqual(source.stats.dedup_dropped, 1)
        self.assertEqual([s.unique for s in source.socket_stats], [0, 1])

    async def test_schema_matches_single_socket(self) -> None:
        net1 = FakeNetwork({A: [[msg(1, "x")]]})
        with net1.patched():
            one = await collect(LogsSubscribeSource(ws_url=A), 0.1)
        net2 = FakeNetwork({A: [[msg(1, "x")]], B: [[]]})
        with net2.patched():
            two = await collect(multi(), 0.1)
        self.assertEqual(notice_dump(one), notice_dump(two))

    async def test_one_url_serves_all_sockets_by_default(self) -> None:
        net = FakeNetwork({A: [[msg(1, "p")], [msg(2, "p"), msg(3, "q")]]})
        with net.patched():
            notes = await collect(multi(urls=(A,)), 0.2)
        self.assertEqual(net.connects[A], 2)
        self.assertEqual(sorted(n.signature for n in notes), ["p", "q"])


class CounterTests(unittest.IsolatedAsyncioTestCase):
    async def test_per_socket_counters(self) -> None:
        # A: rejected 413, connects and gets 2, closes 1006, reconnects and gets 1.
        a_conns = [413, [msg(10, "s1"), msg(20, "s2"), ("close", 1006)], [msg(60, "s3")]]
        b_conns = [[("sleep", 0.15), msg(30, "s1"), msg(40, "s2")]]
        net = FakeNetwork({A: a_conns, B: b_conns})
        source = multi()
        with net.patched():
            notes = await collect(source, 3.0)
        self.assertEqual(sorted(n.signature for n in notes), ["s1", "s2", "s3"])
        a, b = source.socket_stats
        self.assertEqual(a.reconnects, 2)
        self.assertEqual(a.rejections, {413: 1})
        self.assertEqual(a.closes, {1006: 1})
        self.assertEqual((a.notes, a.unique), (3, 3))
        self.assertEqual(b.reconnects, 0)
        self.assertEqual(b.rejections, {})
        self.assertEqual((b.notes, b.unique), (2, 0))
        st = source.stats
        self.assertEqual(st.reconnects, 2)
        self.assertEqual(st.notes, 3)
        self.assertEqual(st.dedup_dropped, 2)
        self.assertIn("s0[reconnects=2 rejected=413x1 closes=1006x1 errors=0 notes=3 unique=3 mismatch=0]", st.summary())
        self.assertIn("s1[reconnects=0 rejected=0 closes=0 errors=0 notes=2 unique=0 mismatch=0]", st.summary())
        self.assertEqual(st.socket_rows()[0]["rejections"], {"413": 1})

    async def test_reconnect_on_one_socket_does_not_stall_the_other(self) -> None:
        a_conns = [[("close", 1006)], [msg(500, "late")]]
        b_steps = []
        for i in range(1, 4):
            b_steps += [("sleep", 0.01), msg(10 * i, f"b{i}")]
        net = FakeNetwork({A: a_conns, B: [b_steps]})
        source = multi()
        with net.patched(), patch.object(trade_source, "INITIAL_BACKOFF_S", 0.4), patch.object(
            trade_source, "MAX_BACKOFF_S", 0.4
        ):
            notes = await collect(source, 0.8)
        got = [n.signature for n in notes]
        self.assertEqual(got[:3], ["b1", "b2", "b3"])
        self.assertEqual(got[-1], "late")
        self.assertEqual(source.socket_stats[0].reconnects, 1)
        self.assertEqual(source.socket_stats[1].reconnects, 0)

    async def test_stagger_and_jitter(self) -> None:
        net = FakeNetwork({A: [[]], B: [[]]})
        source = multi(stagger_s=0.15)
        with net.patched():
            await collect(source, 0.4)
        self.assertGreaterEqual(net.connect_times[B][0] - net.connect_times[A][0], 0.14)
        delays = {round(source._jitter(10.0), 6) for _ in range(20)}
        self.assertGreater(len(delays), 1)
        self.assertTrue(all(5.0 <= d <= 10.0 for d in delays))


class ResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_pump_survives_unexpected_exception_and_other_socket_is_unaffected(self) -> None:
        bad = InvalidMessage("did not receive a valid HTTP response")
        net = FakeNetwork({A: [bad, bad, bad, [msg(70, "a1")]], B: [[msg(10, "b1"), msg(20, "b2")]]})
        source = multi()
        with net.patched(), self.assertLogs("mal.trade_tape", "WARNING") as logs:
            notes = await collect(source, 0.6)
        self.assertEqual(sorted(n.signature for n in notes), ["a1", "b1", "b2"])
        a, b = source.socket_stats
        self.assertEqual(a.errors, {"InvalidMessage": 3})
        self.assertEqual(b.errors, {})
        self.assertGreaterEqual(net.connects[A], 4)  # kept retrying until it got through
        self.assertTrue(any("ws_pump_error socket=0 error_class=InvalidMessage" in m for m in logs.output))
        self.assertIn("errors=InvalidMessagex3", source.stats.summary())

    async def test_all_sockets_ending_raises(self) -> None:
        source = multi()

        async def dies(*_a, **_k):
            return None

        with patch.object(source, "_pump", dies):
            with self.assertRaises(RuntimeError):
                await collect(source, 3.0)

    async def test_clean_stop_does_not_raise(self) -> None:
        net = FakeNetwork({A: [[msg(1, "x")]], B: [[]]})
        with net.patched():
            notes = await collect(multi(), 0.15)
        self.assertEqual(len(notes), 1)

    async def test_dedup_mismatch_counts_and_logs_once_per_minute(self) -> None:
        failed = ("msg", T0 + 30, logs_frame("x", 100, LOGS, err={"InstructionError": [0, "e"]}))
        failed2 = ("msg", T0 + 31, logs_frame("y", 101, LOGS, err={"InstructionError": [0, "e"]}))
        a_steps = [msg(10, "x"), msg(11, "y", 101), msg(12, "ok")]
        b_steps = [("sleep", 0.05), failed, failed2, msg(40, "ok")]
        net = FakeNetwork({A: [a_steps], B: [b_steps]})
        source = multi()
        with net.patched(), self.assertLogs("mal.trade_tape", "WARNING") as logs:
            notes = await collect(source, 0.3)
        self.assertEqual(sorted(n.signature for n in notes), ["ok", "x", "y"])
        self.assertEqual(source.stats.dedup_dropped, 3)
        self.assertEqual(source.stats.dedup_mismatch, 2)
        self.assertEqual([s.dedup_mismatch for s in source.socket_stats], [0, 2])
        lines = [m for m in logs.output if "dedup_mismatch" in m]
        self.assertEqual(len(lines), 1)  # rate limited
        self.assertTrue(lines[0].endswith("dedup_mismatch signature=x"))

    async def test_close_codes_counted_and_missing_frame_is_1006(self) -> None:
        net = FakeNetwork({A: [[("close", None)], [("close", 1001)], [("close", None)], []], B: [[]]})
        source = multi()
        with net.patched():
            await collect(source, 0.3)
        self.assertEqual(source.socket_stats[0].closes, {1006: 2, 1001: 1})
        self.assertEqual(source.socket_stats[1].closes, {})
        self.assertEqual(source.socket_stats[0].row()["closes"], {"1001": 1, "1006": 2})

    async def test_backpressure_loses_nothing_and_keeps_order(self) -> None:
        sigs = [f"q{i:02d}" for i in range(30)]
        net = FakeNetwork({A: [[msg(i, sig, 100 + i) for i, sig in enumerate(sigs)]], B: [[]]})
        source = multi(max_queue=2)
        got: list[str] = []
        stop = asyncio.Event()

        async def slow_consumer() -> None:
            async for note in source.notices(stop):
                got.append(note.signature)
                await asyncio.sleep(0.005)  # slower than the producer
                if len(got) == len(sigs):
                    return

        with net.patched():
            await asyncio.wait_for(slow_consumer(), 5)
        stop.set()
        self.assertEqual(got, sigs)
        self.assertGreater(source.stats.queue_full_waits, 0)
        self.assertEqual(source.stats.dedup_dropped, 0)


class SeenSetTests(unittest.TestCase):
    def test_window_expires_keys(self) -> None:
        now = [0.0]
        seen = SeenSet(window_s=600, max_keys=100, clock=lambda: now[0])
        self.assertTrue(seen.add("a"))
        now[0] = 599
        self.assertFalse(seen.add("a"))
        now[0] = 601
        self.assertTrue(seen.add("a"))  # expired, new again
        self.assertEqual(len(seen), 1)

    def test_size_cap_bounds_memory(self) -> None:
        now = [0.0]
        seen = SeenSet(window_s=10_000, max_keys=50, clock=lambda: now[0])
        for i in range(10_000):
            now[0] += 0.001
            seen.add(f"k{i}")
            self.assertLessEqual(len(seen), 50)
        self.assertFalse(seen.add("k9999"))  # newest still remembered
        self.assertTrue(seen.add("k0"))  # oldest evicted

    def test_steady_stream_stays_at_window_size(self) -> None:
        now = [0.0]
        seen = SeenSet(window_s=600, clock=lambda: now[0])
        for i in range(5000):
            now[0] += 1.0  # 1 key per second
            seen.add(f"k{i}")
        self.assertLessEqual(len(seen), 601)

    def test_fingerprint_mismatch_is_flagged_on_duplicates_only(self) -> None:
        seen = SeenSet(clock=lambda: 0.0)
        self.assertTrue(seen.add("a", (False, 3)))
        self.assertFalse(seen.last_mismatch)
        self.assertFalse(seen.add("a", (False, 3)))
        self.assertFalse(seen.last_mismatch)
        self.assertFalse(seen.add("a", (True, 3)))
        self.assertTrue(seen.last_mismatch)

    def test_source_wires_window_and_cap(self) -> None:
        source = multi(seen_window_s=5.0, max_seen=3)
        self.assertEqual((source.seen.window_s, source.seen.max_keys), (5.0, 3))


class StatsRowTests(unittest.TestCase):
    def _flush(self, stats) -> dict:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "observe").mkdir()
            clock = lambda: datetime(2026, 9, 25, 8, 10, tzinfo=timezone.utc)  # noqa: E731
            monitor = CompletenessMonitor(root / "tape", root / "observe", stats, clock=clock)
            monitor.start(0.0)
            return monitor.flush(600.0, {})

    def test_multi_row_carries_socket_counters(self) -> None:
        socks = [SocketStats(0, A), SocketStats(1, B)]
        socks[0].reconnects = 3
        socks[0].rejections = {413: 2}
        stats = MergedStats(socks)
        stats.dedup_dropped = 7
        row = self._flush(stats)
        self.assertEqual(row["dedup_dropped"], 7)
        self.assertEqual(row["sockets"][0]["rejections"], {"413": 2})
        self.assertEqual(row["sockets"][1]["reconnects"], 0)

    def test_single_socket_row_is_unchanged(self) -> None:
        row = self._flush(trade_source._ReconnectStats())
        self.assertNotIn("sockets", row)
        self.assertNotIn("dedup_dropped", row)
        json.dumps(row)


if __name__ == "__main__":
    unittest.main()
