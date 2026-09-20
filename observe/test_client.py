"""Unit tests for observe client reconnect (no network)."""

from __future__ import annotations

import asyncio
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from websockets.exceptions import InvalidStatusCode

from observe.client import _classify_stream, run_client
from observe.regime import STREAM_MIGRATION


class ClassifyStreamTests(unittest.TestCase):
    def test_migration_tx_type_aliases(self) -> None:
        for tx_type in ("migrate", "migration", "Migrate"):
            with self.subTest(tx_type=tx_type):
                self.assertEqual(
                    _classify_stream({"txType": tx_type, "mint": "M"}),
                    STREAM_MIGRATION,
                )


class ReconnectLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_status_code_retries_until_connected(self) -> None:
        stop = asyncio.Event()
        output_dir = Path("/tmp/mal-observe-test")
        attempts: list[int] = []

        class FakeWS:
            async def send(self, _payload: str) -> None:
                return None

            def __aiter__(self) -> "FakeWS":
                return self

            async def __anext__(self) -> str:
                stop.set()
                raise StopAsyncIteration

        @asynccontextmanager
        async def mock_connect(*_args, **_kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise InvalidStatusCode(502, {})
            yield FakeWS()

        sleep_calls: list[float] = []

        async def mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with (
            patch("observe.client.websockets.connect", side_effect=mock_connect),
            patch("observe.client.asyncio.sleep", side_effect=mock_sleep),
        ):
            await run_client("wss://example.test/ws", output_dir, stop)

        self.assertGreaterEqual(len(attempts), 2)
        self.assertTrue(sleep_calls, "expected backoff sleep after handshake failure")
        self.assertEqual(sleep_calls[0], 1.0)


if __name__ == "__main__":
    unittest.main()
