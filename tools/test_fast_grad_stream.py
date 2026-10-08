"""Tests for tools/fast_grad_stream.py and tools/grad_stream_compare.py. No network."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path

from observe.trade_decode import b58encode
from tools import fast_grad_stream as g
from tools import grad_stream_compare as c
from tools.paper_curve_math import INITIAL_REAL_TOKEN_UI, TOKEN_SCALE, TOKEN_RAW_OFFSET

KEY = "SECRETKEY-abc123"
T0 = 1_790_319_000_000
SIG = "5" * 88


def _pk(n: int) -> bytes:
    return bytes([n]) * 32


def base_for_progress(p: float) -> int:
    return int(TOKEN_RAW_OFFSET + (1 - p) * INITIAL_REAL_TOKEN_UI * TOKEN_SCALE)


def trade_row(mint, p, t, venue="pump_bonding"):
    return {"venue": venue, "mint": mint, "base_reserve": base_for_progress(p), "t_recv_ms": t}


def _trade_log(mint: bytes, buy=True) -> str:
    raw = (bytes.fromhex("bddb7fd34ee661ee") + mint + (2_000_000_000).to_bytes(8, "little")
           + (5_000_000).to_bytes(8, "little") + bytes([1 if buy else 0]) + _pk(9)
           + (1790319000).to_bytes(8, "little", signed=True) + (80_000_000_000).to_bytes(8, "little")
           + (200_000_000_000_000).to_bytes(8, "little"))
    return "Program data: " + base64.b64encode(raw).decode()


def _complete_log(mint: bytes) -> str:
    raw = (bytes.fromhex("5f72619cd42e9808") + _pk(1) + mint + _pk(3)
           + (1790319000).to_bytes(8, "little", signed=True) + _pk(0))
    return "Program data: " + base64.b64encode(raw).decode()


def _notif(logs, slot=100, sig=SIG, err=None, keys=()):
    return {"method": "transactionNotification", "params": {"subscription": 7, "result": {
        "signature": sig, "slot": slot,
        "transaction": {"transaction": {"signatures": [sig], "message": {"accountKeys": list(keys)}},
                        "meta": {"err": err, "logMessages": logs}}}}}


class WatchSetTests(unittest.TestCase):
    def test_threshold_cap_and_order(self) -> None:
        t = g.WatchTracker(threshold=0.90, cap=2)
        for m, p in (("A", 0.95), ("B", 0.50), ("C", 0.91), ("D", 0.99), ("E", 0.899)):
            t.feed(trade_row(m, p, T0))
        self.assertEqual(t.select(T0 + 1000), ["D", "A"])  # capped, highest first
        t.cap = 10
        self.assertEqual(t.select(T0 + 1000), ["D", "A", "C"])  # B and E below threshold

    def test_idle_drop_and_latest_progress(self) -> None:
        t = g.WatchTracker(threshold=0.90)
        t.feed(trade_row("A", 0.95, T0))
        t.feed(trade_row("B", 0.95, T0 + 500_000))
        self.assertEqual(t.select(T0 + 700_000), ["B"])  # A idle > 10 min
        t.feed(trade_row("B", 0.5, T0 + 600_000))  # progress falls (sell): drops out
        self.assertEqual(t.select(T0 + 700_000), [])

    def test_completed_dropped_and_not_readded(self) -> None:
        t = g.WatchTracker(threshold=0.90)
        t.feed(trade_row("A", 0.95, T0))
        t.feed(trade_row("A", 1.0, T0 + 10, venue="pumpswap"))
        t.feed(trade_row("A", 0.95, T0 + 20))
        self.assertEqual(t.select(T0 + 100), [])

    def test_tracking_is_bounded(self) -> None:
        t = g.WatchTracker(max_tracked=5)
        for i in range(50):
            t.feed(trade_row(f"M{i}", 0.1, T0 + i))
        self.assertLessEqual(len(t.state), 5)

    def test_tape_tail_reads_incrementally(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            hour = g.hour_of_ms(T0)
            p = Path(d) / f"trades-{hour}.jsonl"
            p.write_text(json.dumps(trade_row("A", 0.95, T0)) + "\n" + '{"partial"')
            tr = g.WatchTracker()
            tail = g.TapeTail(Path(d), tr)
            tail.poll(T0)
            self.assertEqual(tr.select(T0), ["A"])
            with open(p, "a") as fh:
                fh.write(': 1}\n' + json.dumps(trade_row("B", 0.97, T0 + 5)) + "\n")
            tail.poll(T0 + 10)
            self.assertEqual(tr.select(T0 + 10), ["B", "A"])

    def test_previous_hour_polled_once_after_rollover(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            h0, h1 = g.hour_of_ms(T0), g.hour_of_ms(T0 + 3_600_000)
            p0 = Path(d) / f"trades-{h0}.jsonl"
            p0.write_text(json.dumps(trade_row("A", 0.95, T0)) + "\n")
            tr = g.WatchTracker(idle_ms=10**12)
            tail = g.TapeTail(Path(d), tr)
            tail.poll(T0)
            with open(p0, "a") as fh:
                fh.write(json.dumps(trade_row("B", 0.96, T0 + 1)) + "\n")
            tail.poll(T0 + 3_600_000)  # rollover: late rows of the old hour are picked up once
            self.assertIn("B", tr.state)
            with open(p0, "a") as fh:
                fh.write(json.dumps(trade_row("C", 0.96, T0 + 2)) + "\n")
            tail.poll(T0 + 3_600_001)
            self.assertNotIn("C", tr.state)

    def test_tape_tail_bootstrap_skips_partial_first_line(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            hour = g.hour_of_ms(T0)
            lines = [json.dumps(trade_row(f"M{i}", 0.95, T0 + i)) for i in range(20)]
            (Path(d) / f"trades-{hour}.jsonl").write_text("\n".join(lines) + "\n")
            tr = g.WatchTracker()
            g.TapeTail(Path(d), tr, tail_bytes=300).poll(T0 + 100)
            self.assertTrue(0 < len(tr.state) < 20)
            self.assertIn("M19", tr.state)


class FakeWs:
    def __init__(self, frames=()):
        self.frames, self.sent = list(frames), []

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def recv(self):
        if self.frames:
            f = self.frames.pop(0)
            return f if isinstance(f, str) else json.dumps(f)
        await asyncio.sleep(3600)


class SubscriberTests(unittest.TestCase):
    def test_new_subscription_before_old_unsubscribe(self) -> None:
        async def go():
            ws = FakeWs()
            s = g.Subscriber(ws)
            await s.request(["a", "b"])
            self.assertEqual([m["method"] for m in ws.sent], ["transactionSubscribe"])
            self.assertTrue(await s.on_response({"id": 1, "result": 11}))
            self.assertEqual(len(ws.sent), 1)
            await s.request(["a", "b"])  # unchanged: nothing sent
            self.assertEqual(len(ws.sent), 1)
            await s.request(["a", "c"])
            self.assertEqual([m["method"] for m in ws.sent], ["transactionSubscribe"] * 2)
            self.assertEqual(s.active_id, 11)  # old still live until the new one is acked
            await s.on_response({"id": 2, "result": 12})
            self.assertEqual([m["method"] for m in ws.sent],
                             ["transactionSubscribe", "transactionSubscribe", "transactionUnsubscribe"])
            self.assertEqual(ws.sent[-1]["params"], [11])
            self.assertEqual(s.active_id, 12)
            self.assertTrue(await s.on_response({"id": ws.sent[-1]["id"], "result": True}))
            self.assertEqual(ws.sent[1]["params"][0]["accountInclude"], ["a", "c"])
            self.assertEqual(ws.sent[1]["params"][1]["commitment"], "processed")
        asyncio.run(go())

    def test_unsubscribe_false_result_or_error_raises(self) -> None:
        async def go(reply):
            ws = FakeWs()
            s = g.Subscriber(ws)
            await s.request(["a"])
            await s.on_response({"id": 1, "result": 11})
            await s.request(["b"])
            await s.on_response({"id": 2, "result": 12})
            uid = ws.sent[-1]["id"]
            await s.on_response({"id": uid, **reply})
        for reply in ({"result": False}, {"error": {"code": -32602, "message": "bad"}}):
            with self.assertRaises(ConnectionError):
                asyncio.run(go(reply))

    def test_too_many_pending_or_stale_ack_raises(self) -> None:
        async def go():
            now = [0.0]
            ws = FakeWs()
            s = g.Subscriber(ws, lambda: now[0])
            await s.request(["a"])
            await s.on_response({"id": 1, "result": 11})  # active_id is set
            await s.request(["b"])
            now[0] = 5.0
            s.check_stale(30.0)  # young: fine
            now[0] = 40.0
            with self.assertRaises(ConnectionError):
                s.check_stale(30.0)
            s.pending.clear()
            s.pending_at.clear()
            for x in "cde"[: g.MAX_PENDING_SUBS]:
                await s.request([x])
            with self.assertRaises(ConnectionError):
                await s.request(["z"])
        asyncio.run(go())

    def test_notification_is_not_a_response(self) -> None:
        async def go():
            s = g.Subscriber(FakeWs())
            self.assertFalse(await s.on_response(_notif([])))
        asyncio.run(go())

    def test_session_refreshes_when_watch_set_changes(self) -> None:
        async def go():
            tr = g.WatchTracker()
            tr.feed(trade_row("A", 0.95, T0))
            clock = [T0 + 1000]
            with tempfile.TemporaryDirectory() as d:
                eng = g.Engine(tr, None, g.PdaCache(fn=lambda m: "PDA_" + m), g.GradWriter(Path(d)),
                               g.Stats(), None, refresh_s=0.05, clock_ms=lambda: clock[0])
                ws = FakeWs([{"jsonrpc": "2.0", "id": 1, "result": 5}])
                stop = asyncio.Event()
                task = asyncio.create_task(g.session(ws, eng, stop))
                await asyncio.sleep(0.1)
                tr.feed(trade_row("B", 0.96, T0 + 1500))
                clock[0] += 1000
                await asyncio.sleep(0.2)
                stop.set()
                await asyncio.wait_for(task, 3)
            subs = [m for m in ws.sent if m["method"] == "transactionSubscribe"]
            self.assertEqual(len(subs), 2)
            self.assertIn("PDA_B", subs[1]["params"][0]["accountInclude"])
            self.assertIn(g.MIGRATION_ACCOUNT, subs[0]["params"][0]["accountInclude"])
        asyncio.run(go())


# Header of a real version-1 transaction (mainnet slot 454508506, 2026-10-08, public getBlock with
# maxSupportedTransactionVersion 1). v1 has no ComputeBudget instructions in effect, no
# addressTableLookups, and the entry carries "version": 1.
REAL_V1_CONFIG = {"computeUnitLimit": 81700, "heapSize": None, "loadedAccountsDataSizeLimit": 16777216, "priorityFee": 2341}


def v1_notif(logs, keys, instructions=(), slot=100, sig=SIG):
    msg = {"accountKeys": list(keys),
           "header": {"numRequiredSignatures": 1, "numReadonlySignedAccounts": 0, "numReadonlyUnsignedAccounts": 1},
           "instructions": list(instructions), "recentBlockhash": "11111111111111111111111111111111",
           "transactionConfig": dict(REAL_V1_CONFIG)}
    meta = {"err": None, "logMessages": list(logs), "loadedAddresses": {"writable": [], "readonly": []}}
    return {"method": "transactionNotification", "params": {"subscription": 7, "result": {
        "signature": sig, "slot": slot,
        "transaction": {"transaction": {"signatures": [sig], "message": msg}, "meta": meta, "version": 1}}}}


class VersionOneTests(unittest.TestCase):
    def test_subscribe_asks_for_version_1(self) -> None:
        # 0 errors (-32015) or drops version-1 transactions; 1 returns legacy, v0 and v1
        opts = g.subscribe_request(["A", "B"], 5)["params"][1]
        self.assertEqual(opts["maxSupportedTransactionVersion"], 1)
        self.assertEqual(g.MAX_SUPPORTED_TX_VERSION, 1)
        self.assertEqual((opts["commitment"], opts["transactionDetails"]), ("processed", "full"))

    def test_v1_trade_and_complete_rows(self) -> None:
        mint = _pk(2)
        m = b58encode(mint)
        rows = g.rows_from_notification(v1_notif([_trade_log(mint), _complete_log(mint)], ["S", "X"]), T0,
                                        g.PdaCache(fn=str), {m})
        self.assertEqual(sorted(r["kind"] for r in rows), ["complete", "trade"])
        self.assertTrue(all(r["signature"] == SIG and r["mint"] == m for r in rows))

    def test_v1_migrate_by_instruction_without_compute_budget(self) -> None:
        keys = [b58encode(_pk(n)) for n in (10, 11, 12, 13)] + [g.PUMP_PROGRAM]
        for disc in ("9beae792ec9ea21e", "bbcb121fceedfe29"):
            ix = {"programIdIndex": 4, "accounts": [0, 1, 2, 3], "data": b58encode(bytes.fromhex(disc) + b"\x01")}
            rows = g.rows_from_notification(v1_notif(["Program log: Instruction: Migrate"], keys, [ix]), T0,
                                            g.PdaCache(fn=str), set())
            self.assertEqual([(r["kind"], r["mint"]) for r in rows], [("migrate", keys[2])], disc)

    def test_v1_keys_and_other_row(self) -> None:
        entry = v1_notif(["noise"], ["CURVE", "S"])["params"]["result"]["transaction"]
        self.assertEqual(g.tx_keys(entry), ["CURVE", "S"])  # empty loadedAddresses, no addressTableLookups
        pdas = g.PdaCache(fn=lambda m: "CURVE")
        pdas.curve("MINTX")
        rows = g.rows_from_notification(v1_notif(["noise"], ["CURVE", "S"]), T0, pdas, set())
        self.assertEqual([(r["kind"], r["mint"]) for r in rows], [("other", "MINTX")])
        failed = v1_notif([_trade_log(_pk(2))], ["S"])
        failed["params"]["result"]["transaction"]["meta"]["err"] = {"InstructionError": [0, "x"]}
        self.assertEqual(g.rows_from_notification(failed, T0, pdas, {b58encode(_pk(2))}), [])


class DecodeTests(unittest.TestCase):
    def test_trade_and_complete_rows(self) -> None:
        mint = _pk(2)
        m = b58encode(mint)
        rows = g.rows_from_notification(_notif([_trade_log(mint), _complete_log(mint)]), T0, g.PdaCache(fn=str), {m})
        kinds = sorted(r["kind"] for r in rows)
        self.assertEqual(kinds, ["complete", "trade"])
        tr = next(r for r in rows if r["kind"] == "trade")
        self.assertEqual((tr["side"], tr["sol_lamports"], tr["token_raw"]), ("buy", 2_000_000_000, 5_000_000))
        self.assertEqual(tr["commitment"], "processed")
        self.assertEqual(tr["mint"], m)
        self.assertEqual(next(r for r in rows if r["kind"] == "complete")["mint"], m)

    def test_unwatched_trade_dropped_failed_tx_and_other(self) -> None:
        mint = _pk(2)
        self.assertEqual(g.rows_from_notification(_notif([_trade_log(mint)]), T0, g.PdaCache(fn=str), set()), [])
        self.assertEqual(g.rows_from_notification(_notif([_trade_log(mint)], err={"x": 1}), T0,
                                                  g.PdaCache(fn=str), {b58encode(mint)}), [])
        pdas = g.PdaCache(fn=lambda m: "CURVE")
        pdas.curve("MINTX")
        rows = g.rows_from_notification(_notif(["noise"], keys=["CURVE"]), T0, pdas, set())
        self.assertEqual([(r["kind"], r["mint"]) for r in rows], [("other", "MINTX")])
        self.assertIsNone(g.rows_from_notification({"method": "x"}, T0, pdas, set()))

    def test_engine_dedupes_overlap_counts_and_marks_done(self) -> None:
        mint = _pk(2)
        m = b58encode(mint)
        tr = g.WatchTracker()
        tr.feed(trade_row(m, 0.95, T0))
        with tempfile.TemporaryDirectory() as d:
            eng = g.Engine(tr, None, g.PdaCache(fn=str), g.GradWriter(Path(d)), g.Stats(), None,
                           clock_ms=lambda: T0)
            eng.accounts()
            msg = _notif([_complete_log(mint)])
            eng.handle(json.dumps(msg), msg, T0)
            eng.handle(json.dumps(msg), msg, T0 + 5)  # same sig from the overlapping subscription
            eng.writer.close()
            lines = (Path(d) / f"grad-{g.hour_of_ms(T0)}.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(eng.stats.rows_by_kind, {"complete": 1})
        self.assertEqual(eng.stats.msgs, 2)
        self.assertEqual(tr.select(T0 + 10), [])  # completed mint dropped
        snap = eng.stats.snapshot(T0)
        self.assertGreater(snap["hourly"][g.hour_of_ms(T0)]["bytes"], 0)


class RedactionTests(unittest.TestCase):
    def test_redact(self) -> None:
        url = g.ws_url(KEY)
        out = g.redact_secrets(f"connect failed {url} and {KEY}", KEY)
        self.assertNotIn(KEY, out)
        self.assertIn("api-key=<redacted>", out)

    def test_run_never_logs_key(self) -> None:
        url = g.ws_url(KEY)

        def connect(u):
            raise OSError(f"cannot reach {u}")

        stream = io.StringIO()
        h = logging.StreamHandler(stream)
        g.log.addHandler(h)
        g.log.setLevel(logging.INFO)
        try:
            with tempfile.TemporaryDirectory() as d:
                eng = g.Engine(g.WatchTracker(), None, g.PdaCache(fn=str), g.GradWriter(Path(d)), g.Stats(), None)

                async def nosleep(_):
                    return None
                asyncio.run(g.run(url, eng, asyncio.Event(), connect=connect, sleep=nosleep,
                                  max_sessions=2, key=KEY))
        finally:
            g.log.removeHandler(h)
        self.assertIn("ws_down", stream.getvalue())
        self.assertNotIn(KEY, stream.getvalue())

    def test_key_from_env_only(self) -> None:
        self.assertEqual(g.api_key_from_env({"HELIUS_API_KEY": f" {KEY} "}), KEY)
        with self.assertRaises(SystemExit):
            g.api_key_from_env({})
        with self.assertRaises(SystemExit):
            g.api_key_from_env({"HELIUS_API_KEY": "a&b"})


def _s(kind, mint, t, sig, **kw):
    return {"kind": kind, "mint": mint, "t_recv_ms": t, "signature": sig, **kw}


class CompareTests(unittest.TestCase):
    def test_compare_math(self) -> None:
        stream = [
            _s("complete", "A", 1000, "sA"), _s("complete", "B", 5000, "sB"),
            _s("complete", "C", 9000, "sC"),  # tip never saw C complete
            _s("trade", "A", 900, "tA1"), _s("trade", "A", 950, "ghost"),  # ghost: rolled back
            _s("complete", "D", 100, "sD"),
        ]
        tip_mig = [
            {"type": "complete", "mint": "A", "t_recv_ms": 2800, "signature": "sA"},
            {"type": "complete", "mint": "B", "t_recv_ms": 6800, "signature": "sB"},
            {"type": "complete", "mint": "X", "t_recv_ms": 7000, "signature": "sX"},  # missed by stream
            {"type": "migration", "mint": "A", "t_recv_ms": 3000, "signature": "mA"},
        ]
        tip_trades = [
            {"venue": "pumpswap", "mint": "A", "t_recv_ms": 3500, "signature": "p1"},
            {"venue": "pumpswap", "mint": "B", "t_recv_ms": 5000, "signature": "p2"},  # not after stream
            {"venue": "pump_bonding", "mint": "A", "t_recv_ms": 890, "signature": "tA1"},
        ]
        r = c.compare(stream, tip_mig, tip_trades)
        self.assertEqual(r["n_tip_complete"], 3)
        self.assertAlmostEqual(r["coverage"], 2 / 3)
        self.assertEqual(r["lead_ms"]["p50"], 1800.0)
        self.assertEqual(r["before_first_pumpswap"]["n"], 2)
        self.assertEqual(r["before_first_pumpswap"]["share"], 0.5)  # A yes, B (0 ms) no
        self.assertEqual(r["rollback"]["trade"], {"processed": 2, "not_confirmed": 1, "rate": 0.5})
        # C and D completes were never confirmed on the tip tape; A and B were
        self.assertEqual(r["rollback"]["complete"]["not_confirmed"], 2)
        self.assertIn("coverage", c.summary_text(r))

    def test_rollback_excluding_skipped_and_gap_slots(self) -> None:
        stream = [_s("trade", "A", 10, "ok", slot=1), _s("trade", "A", 11, "g1", slot=50),
                  _s("trade", "A", 12, "g2", slot=120), _s("trade", "A", 13, "g3", slot=7)]
        tip_trades = [{"venue": "pump_bonding", "mint": "A", "t_recv_ms": 20, "signature": "ok"}]
        sk = c.SlotSet()
        sk.add_row({"slot": 50, "kind": "skipped"})
        sk.add_row({"kind": "gap", "reason": "backlog_jump", "from_slot": 100, "to_slot": 130})
        r = c.compare(stream, [], tip_trades, sk)
        self.assertEqual(r["rollback"]["trade"]["rate"], 0.75)
        ex = r["rollback_excl_tip_skipped_or_gap_slots"]
        self.assertEqual(ex["excluded_rows"], 2)
        self.assertEqual(ex["trade"], {"processed": 2, "not_confirmed": 1, "rate": 0.5})

    def test_load_skipped_from_follower_files(self) -> None:
        h = g.hour_of_ms(T0)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / f"skipped-slots-{h}.jsonl").write_text(
                json.dumps({"slot": 5, "kind": "skipped", "t_recv_ms": T0 + 1}) + "\n")
            (Path(d) / "gaps.jsonl").write_text(
                json.dumps({"kind": "gap", "reason": "backlog_jump", "from_slot": 10, "to_slot": 20,
                            "t_recv_ms": T0 + 2}) + "\n")
            sk = c.load_skipped(Path(d), T0, T0 + 1000)
        self.assertTrue(sk.contains(5) and sk.contains(15) and not sk.contains(6))

    def test_empty(self) -> None:
        r = c.compare([], [], [])
        self.assertIsNone(r["coverage"])
        self.assertIsNone(r["lead_ms"]["p50"])
        c.summary_text(r)

    def test_compare_dirs_window(self) -> None:
        h = g.hour_of_ms(T0)
        with tempfile.TemporaryDirectory() as d:
            sd, td = Path(d) / "s", Path(d) / "t"
            sd.mkdir()
            td.mkdir()
            (sd / f"grad-{h}.jsonl").write_text(
                json.dumps(_s("complete", "A", T0 + 1000, "sA")) + "\n"
                + json.dumps(_s("complete", "OUT", T0 + 9_999_999, "sO")) + "\n")
            (td / f"migrations-{h}.jsonl").write_text(
                json.dumps({"type": "complete", "mint": "A", "t_recv_ms": T0 + 2500, "signature": "sA"}) + "\n")
            (td / f"trades-{h}.jsonl").write_text(
                json.dumps({"venue": "pumpswap", "mint": "A", "t_recv_ms": T0 + 3000, "signature": "p"}) + "\n")
            r = c.compare_dirs(sd, td, T0, T0 + 60_000)
        self.assertEqual(r["n_stream_complete"], 1)
        self.assertEqual(r["lead_ms"]["p50"], 1500.0)
        self.assertEqual(r["before_first_pumpswap"]["share"], 1.0)
        self.assertEqual(r["rollback"]["complete"]["rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
