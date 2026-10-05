"""Offline tests for tools/migration_stream_probe.py. Fabricated notifications, fake socket, no network."""

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
from tools import migration_stream_probe as p

KEY = "SECRETKEY-abc123"
T0 = 1_790_319_000_000  # ms, inside the event-ts bounds
SIG = "5" * 88


def _pk(n: int) -> bytes:
    return bytes([n]) * 32


def _migration_log(mint: bytes = _pk(2)) -> str:
    raw = b"".join(
        [
            bytes.fromhex("bde95db95c94ea94"),
            _pk(1), mint, (50).to_bytes(8, "little"), (2_000_000_000).to_bytes(8, "little"),
            (100).to_bytes(8, "little"), _pk(3), (1790319000).to_bytes(8, "little", signed=True),
            _pk(4), _pk(5),
        ]
    )
    return "Program data: " + base64.b64encode(raw).decode()


def _notif(logs, slot=100, sig=SIG, err=None) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "transactionNotification",
        "params": {
            "subscription": 7,
            "result": {
                "signature": sig,
                "slot": slot,
                "transaction": {
                    "transaction": {"signatures": [sig], "message": {}},
                    "meta": {"err": err, "logMessages": logs},
                },
            },
        },
    }


class FakeWs:
    def __init__(self, frames, end_with=ConnectionError("closed")):
        self.frames, self.sent, self.end_with = list(frames), [], end_with

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def recv(self):
        if self.frames:
            f = self.frames.pop(0)
            return f if isinstance(f, str) else json.dumps(f)
        raise self.end_with


class DecodeTests(unittest.TestCase):
    def test_migration_row(self) -> None:
        row = p.row_from_notification(_notif(["x", _migration_log()]), T0)
        self.assertTrue(row["is_migration"])
        self.assertEqual(row["mint"], b58encode(_pk(2)))
        self.assertEqual(row["pool"], b58encode(_pk(4)))
        self.assertEqual(row["slot"], 100)
        self.assertEqual(row["signature"], SIG)
        self.assertEqual(row["t_recv_ms"], T0)
        self.assertEqual(row["canonical_pool"], p.canonical_pool_str(row["mint"]))

    def test_non_migration_and_err_and_other_methods(self) -> None:
        row = p.row_from_notification(_notif(["Program log: hi"], err={"InstructionError": [0, "x"]}), T0)
        self.assertFalse(row["is_migration"])
        self.assertIsNone(row["mint"])
        self.assertIsNotNone(row["err"])
        self.assertIsNone(p.row_from_notification({"jsonrpc": "2.0", "id": 1, "result": 5}, T0))

    def test_subscribe_request_params(self) -> None:
        req = p.subscribe_request("json")
        self.assertEqual(req["method"], "transactionSubscribe")
        flt, opts = req["params"]
        self.assertEqual(flt, {"accountInclude": [p.MIGRATION_ACCOUNT], "failed": False, "vote": False})
        self.assertEqual(opts["commitment"], "processed")
        self.assertEqual(opts["transactionDetails"], "full")
        self.assertEqual(opts["maxSupportedTransactionVersion"], 0)
        self.assertFalse(opts["showRewards"])


class RedactionTests(unittest.TestCase):
    def test_env_file_key_and_url(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / "h.env"
            env.write_text(f"export HELIUS_API_KEY='{KEY}'\n")
            key = p.load_api_key(str(env), environ={})
        url = p.helius_ws_url(key)
        self.assertTrue(url.startswith("wss://mainnet.helius-rpc.com/?api-key="))
        self.assertNotIn(KEY, p.redact_rpc_url(url))

    def test_unsafe_key_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            p.load_api_key("/nonexistent", environ={"HELIUS_API_KEY": "a&b"})

    def test_run_never_logs_key(self) -> None:
        url = p.helius_ws_url(KEY)

        def connect(u):
            raise OSError(f"cannot reach {u}")

        stream = io.StringIO()
        h = logging.StreamHandler(stream)
        p.log.addHandler(h)
        p.log.setLevel(logging.INFO)
        try:
            with tempfile.TemporaryDirectory() as d:
                async def go():
                    async def nosleep(_):
                        return None
                    return await p.run(url, "json", p.Writer(Path(d)), asyncio.Event(), connect=connect,
                                       sleep=nosleep, max_sessions=2)
                asyncio.run(go())
        finally:
            p.log.removeHandler(h)
        self.assertIn("ws_down", stream.getvalue())
        self.assertNotIn(KEY, stream.getvalue())


class ReconnectTests(unittest.TestCase):
    def test_reconnects_resubscribes_and_writes(self) -> None:
        sockets = [
            FakeWs([{"jsonrpc": "2.0", "result": 1, "id": 1}, _notif(["a", _migration_log()]), "not json"]),
            FakeWs([_notif(["noise"], slot=101)]),
        ]
        sleeps: list[float] = []
        times = iter(range(T0, T0 + 100))

        async def sleep(s):
            sleeps.append(s)

        with tempfile.TemporaryDirectory() as d:
            async def go():
                return await p.run("wss://x/?api-key=k", "json", p.Writer(Path(d)), asyncio.Event(),
                                   connect=lambda u: sockets.pop(0), clock_ms=lambda: next(times),
                                   sleep=sleep, max_sessions=2)
            counts = asyncio.run(go())
            files = list(Path(d).glob("migrations-*.jsonl"))
            rows = [json.loads(line) for f in files for line in f.read_text().splitlines()]
        self.assertEqual(counts.notifications, 2)
        self.assertEqual(counts.migrations, 1)
        self.assertEqual(counts.reconnects, 2)
        self.assertEqual(len(sleeps), 2)
        self.assertGreater(sleeps[1], sleeps[0] * 1.2)  # backoff grows
        self.assertEqual([r["slot"] for r in rows], [100, 101])
        self.assertTrue(files[0].name.startswith("migrations-2026-"))

    def test_backoff_capped(self) -> None:
        b = 1.0
        for _ in range(20):
            b = p.next_backoff(b, jitter=lambda: 0.5)
        self.assertAlmostEqual(b, 60.0)

    def test_subscribe_error_raises(self) -> None:
        ws = FakeWs([{"jsonrpc": "2.0", "id": 1, "error": {"message": "nope api-key=SECRET"}}])
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ConnectionError) as cm:
                asyncio.run(p.session(ws, "json", p.Writer(Path(d)), p.Counts(), lambda: T0, asyncio.Event()))
        self.assertEqual(ws.sent[0]["method"], "transactionSubscribe")


class CompareTests(unittest.TestCase):
    def test_pct(self) -> None:
        self.assertEqual(p.pct([1, 2, 3, 4, 5], 50), 3)
        self.assertAlmostEqual(p.pct([0, 100], 90), 90.0)
        self.assertIsNone(p.pct([], 50))

    def test_compare_math(self) -> None:
        stream = [
            {"mint": "A", "t_recv_ms": 1000, "slot": 1, "signature": "s1", "is_migration": True, "err": None},
            {"mint": "B", "t_recv_ms": 2000, "slot": 2, "signature": "s2", "is_migration": True, "err": None},
            {"mint": "C", "t_recv_ms": 3000, "slot": 3, "signature": "s3", "is_migration": True, "err": None},
            {"mint": None, "t_recv_ms": 3500, "slot": 9, "signature": "x", "is_migration": False, "err": None},
        ]
        tip = [
            {"mint": "A", "t_recv_ms": 1600, "slot": 1, "signature": "s1", "type": "migration"},
            {"mint": "B", "t_recv_ms": 2800, "slot": 3, "signature": "s2", "type": "migration"},
            {"mint": "D", "t_recv_ms": 4000, "slot": 4, "signature": "s4", "type": "migration"},
            {"mint": "A", "t_recv_ms": 1700, "slot": 1, "signature": "s1", "type": "complete"},
        ]
        out = p.compare_rows(stream, tip)
        self.assertEqual((out["n_matched"], out["n_only_stream"], out["n_only_tip"]), (2, 1, 1))
        self.assertEqual(out["delta_ms_p50"], 700.0)  # deltas 600, 800
        self.assertAlmostEqual(out["delta_ms_p90"], 780.0)
        self.assertEqual(out["slot_equal"], 1)
        self.assertEqual(out["signature_equal"], 2)

    def test_compare_dirs_window(self) -> None:
        start = p.parse_time_ms("2026-10-05T10:00:00Z")
        end = p.parse_time_ms("2026-10-05T11:00:00Z")
        with tempfile.TemporaryDirectory() as d:
            sd, td = Path(d) / "s", Path(d) / "t"
            sd.mkdir()
            td.mkdir()
            rows_s = [{"mint": "A", "t_recv_ms": start + 1000, "slot": 5, "signature": "s", "is_migration": True, "err": None},
                      {"mint": "OLD", "t_recv_ms": start - 7_200_000, "slot": 1, "signature": "o", "is_migration": True, "err": None}]
            (sd / f"migrations-{p.hour_of_ms(start)}.jsonl").write_text("\n".join(json.dumps(r) for r in rows_s))
            (sd / f"migrations-{p.hour_of_ms(start - 7_200_000)}.jsonl").write_text(json.dumps(rows_s[1]))
            # tip row lands just past the window end: still joined thanks to the slack
            tip = {"mint": "A", "t_recv_ms": start + 1500, "slot": 5, "signature": "s", "type": "migration"}
            (td / f"migrations-{p.hour_of_ms(start)}.jsonl").write_text(json.dumps(tip))
            out = p.compare_dirs(sd, td, start, end)
        self.assertEqual(out["n_matched"], 1)
        self.assertEqual(out["n_only_stream"], 0)
        self.assertEqual(out["delta_ms_p50"], 500.0)


if __name__ == "__main__":
    unittest.main()
