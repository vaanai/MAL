"""Recorded pump.fun / PumpSwap program-data fixtures. No network."""

from __future__ import annotations

import base64
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from observe.trade_decode import (
    PUMP_BONDING_PROGRAM,
    PUMPSWAP_PROGRAM,
    WSOL_MINT,
    decode_pool_account,
    decode_program_data,
    iso_from_ms,
    records_from_logs,
)
from observe.trade_source import (
    SOURCE_HELIUS_TX,
    SOURCE_PUBLIC_RPC_LOGS,
    HeliusTransactionSource,
    LogsSubscribeSource,
    helius_subscribe_request,
    parse_helius_notification,
    parse_logs_notification,
)
from observe.trade_source import RawNotice
from observe.trade_tape import PoolMintCache, main, trades_from_notice

FIXTURES = Path(__file__).resolve().parent / "fixtures"
T_RECV_MS = 1_790_318_932_123
SLOT = 450276100
SIG = "sig111111111111111111111111111111111111111111111111111111111111111111111111111111"


def _b64(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8").strip()


def _line(name: str) -> str:
    return f"Program data: {_b64(name)}"


class RecordedTradeTests(unittest.TestCase):
    def test_bonding_trade_fields_and_receive_time(self) -> None:
        rows = records_from_logs(
            [_line("pump_trade_event.b64")],
            slot=SLOT,
            signature=SIG,
            t_recv_ms=T_RECV_MS,
            commitment="confirmed",
            feed=SOURCE_PUBLIC_RPC_LOGS,
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["type"], "trade")
        self.assertEqual(row["venue"], "pump_bonding")
        self.assertEqual(row["mint"], "5oqFhj53FQHJipb24tRB4GzAobW5VBwonh7qC5V5pump")
        self.assertEqual(row["mint_source"], "event")
        self.assertEqual(row["trader"], "s7epdqiFrL3jiPKx8eM6JEzJ2JbUpJA6kgCqDF1FfvQ")
        self.assertEqual(row["side"], "buy")
        self.assertEqual(row["sol_lamports"], 1000)
        self.assertEqual(row["token_raw"], 12728720)
        self.assertEqual(row["slot"], SLOT)
        self.assertEqual(row["signature"], SIG)
        self.assertEqual(row["event_index"], 0)
        self.assertEqual(row["t_recv_ms"], T_RECV_MS)
        self.assertTrue(row["t_recv"].endswith(".123Z"), row["t_recv"])
        self.assertEqual(row["event_ts"], 1790318932)
        self.assertAlmostEqual(row["market_cap_sol"], 78.562, places=2)
        self.assertGreater(row["price_sol"], 0)
        self.assertTrue(row["quote_is_wsol"])

    def test_receive_time_is_the_given_millisecond_not_wall_clock(self) -> None:
        self.assertEqual(iso_from_ms(T_RECV_MS), records_from_logs(
            [_line("pump_trade_event.b64")],
            slot=1,
            signature=SIG,
            t_recv_ms=T_RECV_MS,
            commitment="confirmed",
            feed=SOURCE_PUBLIC_RPC_LOGS,
        )[0]["t_recv"])
        self.assertNotEqual(iso_from_ms(T_RECV_MS), iso_from_ms(T_RECV_MS + 1))

    def test_two_events_in_one_tx_get_indexes(self) -> None:
        rows = records_from_logs(
            [_line("pump_trade_event.b64"), _line("pump_trade_event.b64")],
            slot=SLOT,
            signature=SIG,
            t_recv_ms=T_RECV_MS,
            commitment="confirmed",
            feed=SOURCE_PUBLIC_RPC_LOGS,
        )
        self.assertEqual([row["event_index"] for row in rows], [0, 1])

    def test_pumpswap_buy_and_sell_have_wallet_amounts_and_pool(self) -> None:
        buy = decode_program_data(base64.b64decode(_b64("pumpswap_buy_event.b64")))
        sell = decode_program_data(base64.b64decode(_b64("pumpswap_sell_event.b64")))
        assert buy is not None and sell is not None
        self.assertEqual(buy["side"], "buy")
        self.assertEqual(buy["trader"], "9LBD6hW7p5Ji6kHYyrJgD9Tr9HJXhLtzyAeKztdSV6jj")
        self.assertEqual(buy["pool"], "HwK2JkkHc5Ekt6umApmj5RerhugNSiMULioThvKvkGB9")
        self.assertEqual(buy["token_raw"], 964212027450)
        self.assertEqual(buy["sol_lamports"], 684782769)
        self.assertIsNone(buy["mint"])
        self.assertEqual(sell["side"], "sell")
        self.assertEqual(sell["trader"], "96G6qBb3ZkUk2pDVcBoxqYM7qNfScfzaJ4AsvzsxQjoZ")
        self.assertEqual(sell["pool"], "Bpxx3HGYu4tUw8uVXqpPssu78YMYpvrSfj9wfgWbicBB")
        self.assertEqual(sell["token_raw"], 2193706141)
        self.assertEqual(sell["sol_lamports"], 497306835397)
        sealed = records_from_logs(
            [_line("pumpswap_sell_event.b64")],
            slot=SLOT,
            signature=SIG,
            t_recv_ms=T_RECV_MS,
            commitment="confirmed",
            feed=SOURCE_PUBLIC_RPC_LOGS,
        )[0]
        self.assertEqual(sealed["slot"], SLOT)
        self.assertEqual(sealed["t_recv_ms"], T_RECV_MS)
        self.assertAlmostEqual(sealed["price_sol"], 0.229425, places=4)

    def test_create_v2_zero_sol_trade_is_kept_flagged(self) -> None:
        raw = bytearray(base64.b64decode(_b64("pump_trade_event.b64")))
        raw[40:48] = b"\x00" * 8
        raw[97:105] = b"\x00" * 8
        line = "Program data: " + base64.b64encode(raw).decode("ascii")
        rows = records_from_logs(
            [line],
            slot=SLOT,
            signature=SIG,
            t_recv_ms=T_RECV_MS,
            commitment="confirmed",
            feed=SOURCE_PUBLIC_RPC_LOGS,
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["mint"], "5oqFhj53FQHJipb24tRB4GzAobW5VBwonh7qC5V5pump")
        self.assertEqual(row["trader"], "s7epdqiFrL3jiPKx8eM6JEzJ2JbUpJA6kgCqDF1FfvQ")
        self.assertEqual(row["sol_lamports"], 0)
        self.assertEqual(row["token_raw"], 12728720)
        self.assertTrue(row["zero_sol"])
        self.assertIsNone(row["price_sol"])
        self.assertIsNone(row["market_cap_sol"])
        self.assertEqual(row["t_recv_ms"], T_RECV_MS)

    def test_zero_size_trade_with_reserves_keeps_curve_price(self) -> None:
        raw = bytearray(base64.b64decode(_b64("pump_trade_event.b64")))
        raw[40:48] = b"\x00" * 8
        line = "Program data: " + base64.b64encode(raw).decode("ascii")
        row = records_from_logs(
            [line],
            slot=SLOT,
            signature=SIG,
            t_recv_ms=T_RECV_MS,
            commitment="confirmed",
            feed=SOURCE_PUBLIC_RPC_LOGS,
        )[0]
        self.assertTrue(row["zero_sol"])
        self.assertGreater(row["price_sol"], 0)
        self.assertAlmostEqual(row["market_cap_sol"], 78.562, places=2)

    def test_trade_event_with_non_bool_flag_is_dropped(self) -> None:
        raw = bytearray(base64.b64decode(_b64("pump_trade_event.b64")))
        raw[56] = 35
        line = "Program data: " + base64.b64encode(raw).decode("ascii")
        rows = records_from_logs(
            [line],
            slot=SLOT,
            signature=SIG,
            t_recv_ms=T_RECV_MS,
            commitment="confirmed",
            feed=SOURCE_PUBLIC_RPC_LOGS,
        )
        self.assertEqual(rows, [])

    def test_unknown_program_data_is_ignored(self) -> None:
        self.assertIsNone(decode_program_data(b"\x00" * 16))
        rows = records_from_logs(
            ["Program log: Instruction: Buy", "Program data: YQ=="],
            slot=SLOT,
            signature=SIG,
            t_recv_ms=T_RECV_MS,
            commitment="confirmed",
            feed=SOURCE_PUBLIC_RPC_LOGS,
        )
        self.assertEqual(rows, [])


class SourceSwapTests(unittest.TestCase):
    def test_logs_and_helius_notices_decode_the_same_trade(self) -> None:
        line = _line("pump_trade_event.b64")
        logs_msg = {
            "jsonrpc": "2.0",
            "method": "logsNotification",
            "params": {
                "subscription": 1,
                "result": {
                    "context": {"slot": SLOT},
                    "value": {"signature": SIG, "err": None, "logs": [line]},
                },
            },
        }
        helius_msg = {
            "jsonrpc": "2.0",
            "method": "transactionNotification",
            "params": {
                "subscription": 1,
                "result": {
                    "slot": SLOT,
                    "signature": SIG,
                    "transaction": {"meta": {"err": None, "logMessages": [line]}},
                },
            },
        }
        logs_note = parse_logs_notification(logs_msg, T_RECV_MS, "confirmed")
        helius_note = parse_helius_notification(helius_msg, T_RECV_MS, "confirmed")
        assert logs_note is not None and helius_note is not None
        self.assertEqual(logs_note.feed, SOURCE_PUBLIC_RPC_LOGS)
        self.assertEqual(helius_note.feed, SOURCE_HELIUS_TX)
        self.assertFalse(logs_note.failed)
        a = trades_from_notice(logs_note, {})[0]
        b = trades_from_notice(helius_note, {})[0]
        for key in ("mint", "trader", "side", "sol_lamports", "token_raw", "slot", "t_recv_ms", "signature"):
            self.assertEqual(a[key], b[key], key)
        self.assertNotEqual(a["feed"], b["feed"])

    def test_failed_log_is_not_a_trade(self) -> None:
        note = parse_logs_notification(
            {
                "method": "logsNotification",
                "params": {
                    "result": {
                        "context": {"slot": SLOT},
                        "value": {"signature": SIG, "err": {"InstructionError": [0, "Custom"]}, "logs": [_line("pump_trade_event.b64")]},
                    }
                },
            },
            T_RECV_MS,
            "confirmed",
        )
        assert note is not None
        self.assertTrue(note.failed)
        self.assertEqual(trades_from_notice(note, {}), [])

    def test_subscribe_payloads_are_program_filtered(self) -> None:
        logs = LogsSubscribeSource(commitment="confirmed").subscribe_payloads()
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0]["method"], "logsSubscribe")
        self.assertEqual(logs[0]["params"][0]["mentions"], [PUMP_BONDING_PROGRAM])
        self.assertEqual(logs[1]["params"][0]["mentions"], [PUMPSWAP_PROGRAM])
        helius = helius_subscribe_request((PUMP_BONDING_PROGRAM, PUMPSWAP_PROGRAM), "confirmed")
        self.assertEqual(helius["method"], "transactionSubscribe")
        self.assertEqual(
            helius["params"][0]["accountInclude"],
            [PUMP_BONDING_PROGRAM, PUMPSWAP_PROGRAM],
        )
        self.assertFalse(helius["params"][0]["failed"])

    def test_helius_source_refuses_missing_key(self) -> None:
        with self.assertRaises(RuntimeError):
            HeliusTransactionSource("")
        with patch.dict(os.environ, {"HELIUS_API_KEY": ""}, clear=False):
            self.assertEqual(main(["--source", "helius_tx"]), 2)

    def test_pool_cache_fills_mint_without_rewriting_receive_time(self) -> None:
        note = RawNotice(
            slot=SLOT,
            signature=SIG,
            failed=False,
            logs=(_line("pumpswap_buy_event.b64"),),
            t_recv_ms=T_RECV_MS,
            commitment="confirmed",
            feed=SOURCE_PUBLIC_RPC_LOGS,
        )
        cache = PoolMintCache()
        ready = cache.accept(trades_from_notice(note, cache.mints), now=10.0)
        self.assertEqual(ready, [])
        self.assertEqual(cache.unknown_pools(), ["HwK2JkkHc5Ekt6umApmj5RerhugNSiMULioThvKvkGB9"])
        filled = cache.remember(
            "HwK2JkkHc5Ekt6umApmj5RerhugNSiMULioThvKvkGB9",
            "Mint111111111111111111111111111111111111111",
            WSOL_MINT,
        )
        self.assertEqual(filled[0]["mint"], "Mint111111111111111111111111111111111111111")
        self.assertEqual(filled[0]["t_recv_ms"], T_RECV_MS)
        self.assertTrue(filled[0]["quote_is_wsol"])
        self.assertEqual(filled[0]["mint_source"], "pool_account")
        again = cache.accept(trades_from_notice(note, {}), now=20.0)
        self.assertEqual(again[0]["mint_source"], "pool_account")
        self.assertEqual(again[0]["t_recv_ms"], T_RECV_MS)
        cold = PoolMintCache()
        self.assertEqual(cold.accept(trades_from_notice(note, {}), now=20.0), [])
        flushed = cold.flush_timeouts(now=22.0, timeout_s=1.5)
        self.assertEqual(flushed[0]["mint_source"], "unresolved")
        self.assertEqual(flushed[0]["t_recv_ms"], T_RECV_MS)

    def test_pool_account_layout(self) -> None:
        from observe.trade_decode import _POOL_ACCOUNT_DISC, b58encode

        base = bytes(range(1, 33))
        quote = bytes(range(33, 65))
        raw = _POOL_ACCOUNT_DISC + bytes([7, 1, 0]) + bytes(32) + base + quote
        decoded = decode_pool_account(raw)
        assert decoded is not None
        self.assertEqual(decoded["base_mint"], b58encode(base))
        self.assertEqual(decoded["quote_mint"], b58encode(quote))


if __name__ == "__main__":
    unittest.main()
