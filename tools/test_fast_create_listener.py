"""Phase-1 create listener. No network, no Helius socket."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from websockets.exceptions import InvalidStatusCode

from tools.fast_create_listener import (
    ACCOUNT_CAP,
    CREDIT_TRIP,
    NARROW_ACCOUNT_MAX,
    SUBSCRIBE_METHOD,
    CreditCounter,
    append_record,
    create_record,
    helius_filter_decision,
    phase1_helius_decision,
    run_client,
    subscribe_payload,
)

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "scripts" / "mal-core" / "mal-fast-create.service"
INSTALL = ROOT / "scripts" / "mal-core" / "install-fast-create-listener.sh"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


class SubscribeTests(unittest.TestCase):
    def test_only_new_token(self) -> None:
        self.assertEqual(subscribe_payload(), {"method": "subscribeNewToken"})
        self.assertEqual(SUBSCRIBE_METHOD, "subscribeNewToken")
        self.assertNotIn("subscribeMigration", json.dumps(subscribe_payload()))


class RecordTests(unittest.TestCase):
    def test_create_row(self) -> None:
        row = create_record(
            {
                "txType": "create",
                "mint": "MintA",
                "slot": 321,
                "timestamp": 1_758_000_000,
            },
            1_758_000_000_123,
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["mint"], "MintA")
        self.assertEqual(row["slot"], 321)
        self.assertEqual(row["vendor_time"], 1_758_000_000)
        self.assertEqual(row["t_recv_ms"], 1_758_000_000_123)
        self.assertEqual(row["stream"], "subscribeNewToken")

    def test_vendor_time_falls_back_to_block_time(self) -> None:
        row = create_record({"txType": "create", "mint": "MintB", "blockTime": 99}, 5)
        assert row is not None
        self.assertIsNone(row["slot"])
        self.assertEqual(row["vendor_time"], 99)

    def test_skips_trades_and_acks(self) -> None:
        self.assertIsNone(create_record({"txType": "buy", "mint": "MintA"}, 1))
        self.assertIsNone(create_record({"message": "Successfully subscribed"}, 1))

    def test_appends_one_line(self) -> None:
        row = create_record({"txType": "create", "mint": "MintC"}, 7)
        assert row is not None
        with tempfile.TemporaryDirectory() as tmp:
            append_record(Path(tmp), row)
            lines = list(Path(tmp).glob("*.jsonl"))
            self.assertEqual(len(lines), 1)
            loaded = json.loads(lines[0].read_text(encoding="utf-8").strip())
            self.assertEqual(loaded["mint"], "MintC")
            self.assertEqual(loaded["t_recv_ms"], 7)


class HeliusGuardTests(unittest.TestCase):
    def test_phase1_does_not_open(self) -> None:
        decision = phase1_helius_decision()
        self.assertFalse(decision["open_socket"])
        self.assertEqual(decision["credits_spent"], 0)
        self.assertEqual(decision["credit_trip"], 20_000)
        self.assertLess(NARROW_ACCOUNT_MAX, ACCOUNT_CAP / 10)

    def test_program_wide_and_wide_lists_refused(self) -> None:
        self.assertEqual(helius_filter_decision([PUMP])[1], "program_wide")
        self.assertEqual(helius_filter_decision([])[1], "empty")
        wide = [f"Acct{i:04d}" for i in range(NARROW_ACCOUNT_MAX + 1)]
        self.assertEqual(helius_filter_decision(wide)[1], "over_narrow_cap")

    def test_counter_trips_at_20000(self) -> None:
        counter = CreditCounter()
        self.assertFalse(counter.add(CREDIT_TRIP - 1))
        self.assertTrue(counter.add(1))
        self.assertEqual(counter.used, CREDIT_TRIP)
        self.assertTrue(counter.tripped)
        fresh = CreditCounter()
        self.assertTrue(fresh.add(CREDIT_TRIP))

    def test_counter_rejects_negative(self) -> None:
        with self.assertRaises(ValueError):
            CreditCounter().add(-1)


class ReconnectLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_status_code_retries_instead_of_raising(self) -> None:
        """A PumpPortal 502 during reconnect (InvalidStatusCode) must not
        propagate out of run_client. It should log and fall into the
        existing backoff/retry path, same as ConnectionClosed/OSError.
        """
        stop = asyncio.Event()
        attempts: list[int] = []

        class FakeWS:
            async def send(self, _payload: str) -> None:
                return None

            async def recv(self) -> str:
                stop.set()
                return json.dumps({"txType": "create", "mint": "MintZ"})

        @asynccontextmanager
        async def mock_connect(*_args, **_kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise InvalidStatusCode(502, {})
            yield FakeWS()

        sleep_calls: list[float] = []

        async def mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        # fast_create_listener imports websockets lazily inside run_client
        # (rather than at module scope), so the patch target is the real
        # websockets module, not tools.fast_create_listener.websockets.
        with tempfile.TemporaryDirectory() as tmp, (
            patch("websockets.connect", side_effect=mock_connect)
        ), patch("tools.fast_create_listener.asyncio.sleep", side_effect=mock_sleep):
            await run_client("wss://example.test/ws", Path(tmp), stop)

        self.assertGreaterEqual(len(attempts), 2, "expected a retry after the 502")
        self.assertTrue(sleep_calls, "expected a backoff sleep after the handshake failure")
        self.assertEqual(sleep_calls[0], 1.0)


class UnitFileTests(unittest.TestCase):
    def test_unit_is_capped_and_has_no_secrets(self) -> None:
        text = UNIT.read_text(encoding="utf-8")
        self.assertIn("MemoryMax=1G", text)
        self.assertIn("Nice=0", text)
        self.assertIn("Restart=always", text)
        self.assertNotIn("HELIUS", text)
        self.assertNotIn("api-key", text)
        self.assertNotIn("EnvironmentFile", text)
        self.assertNotIn("subscribeMigration", text)
        self.assertNotIn("preprocessedSubscribe", text)
        self.assertNotIn("logsSubscribe", text)

    def test_install_rebuilds_venv_and_skips_helius(self) -> None:
        text = INSTALL.read_text(encoding="utf-8")
        self.assertIn("x86_64", text)
        self.assertIn("mal-core-vnic", text)
        self.assertIn('rm -rf "${VENV}"', text)
        self.assertNotIn("HELIUS_API_KEY", text)
        self.assertNotIn("preprocessedSubscribe", text)


if __name__ == "__main__":
    unittest.main()
