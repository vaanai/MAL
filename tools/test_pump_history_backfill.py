"""Schema and decode tests for the public-RPC history loader. No network."""

from __future__ import annotations

import base64
import unittest
from pathlib import Path

from observe.trade_decode import WSOL_MINT, b58encode, records_from_logs
from tools.pump_history_backfill import (
    SOURCE,
    backfill_trade_row,
    budget_bytes,
    compare_trades,
    decode_complete_event,
    decode_create_event,
    decode_migration_event,
    lifecycle_from_logs,
    resolve_unresolved,
    rows_from_block,
)

FIXTURES = Path(__file__).resolve().parent.parent / "observe" / "fixtures"
SLOT = 450276100
SIG = "sig111111111111111111111111111111111111111111111111111111111111111111111111111111"
BLOCK_TIME = 1790318932


def _line(name: str) -> str:
    blob = (FIXTURES / name).read_text(encoding="utf-8").strip()
    return f"Program data: {blob}"


def _borsh_str(text: str) -> bytes:
    raw = text.encode("utf-8")
    return len(raw).to_bytes(4, "little") + raw


class BudgetTests(unittest.TestCase):
    def test_cap_and_headroom(self) -> None:
        total = 157_396_975_616
        avail = 147_819_425_792
        self.assertEqual(budget_bytes(total, avail), 40 * 1024**3)
        # Almost full: only the slack above 20% is usable, and it is under the cap.
        tight = budget_bytes(1000, 250, cap_bytes=10_000)
        self.assertEqual(tight, 50)
        self.assertEqual(budget_bytes(1000, 100, cap_bytes=10_000), 0)


class CreateAndMigrationTests(unittest.TestCase):
    def test_create_event_prefix(self) -> None:
        mint = bytes(range(32))
        user = bytes(range(32, 64))
        creator = bytes([7] * 32)
        curve = bytes([9] * 32)
        raw = b"".join(
            [
                bytes.fromhex("1b72a94ddeeb6376"),
                _borsh_str("Name"),
                _borsh_str("SYM"),
                _borsh_str("https://example.invalid/u"),
                mint,
                curve,
                user,
                creator,
                (1790318932).to_bytes(8, "little", signed=True),
                (1_000_000).to_bytes(8, "little"),
                (30_000_000_000).to_bytes(8, "little"),
                (800_000).to_bytes(8, "little"),
                (1_000_000_000_000_000).to_bytes(8, "little"),
            ]
        )
        ev = decode_create_event(raw)
        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev["mint"], b58encode(mint))
        self.assertEqual(ev["trader"], b58encode(user))
        self.assertEqual(ev["creator"], b58encode(creator))
        self.assertEqual(ev["event_ts"], 1790318932)
        self.assertEqual(ev["quote_reserve"], 30_000_000_000)
        self.assertEqual(ev["quote_mint"], WSOL_MINT)
        self.assertFalse(ev["is_mayhem_mode"])
        # Native SOL is the zero pubkey on current CreateEvent tails.
        usdc = bytes([4]) * 32
        with_tail = raw + bytes(32) + bytes([1, 0]) + bytes(32)
        ev2 = decode_create_event(with_tail)
        assert ev2 is not None
        self.assertTrue(ev2["is_mayhem_mode"])
        self.assertEqual(ev2["quote_mint"], WSOL_MINT)
        quoted = raw + bytes(32) + bytes([0, 0]) + usdc
        ev3 = decode_create_event(quoted)
        assert ev3 is not None
        self.assertEqual(ev3["quote_mint"], b58encode(usdc))

    def test_migration_and_complete_layouts(self) -> None:
        def pk(n: int) -> bytes:
            return bytes([n]) * 32

        mig = b"".join(
            [
                bytes.fromhex("bde95db95c94ea94"),
                pk(1),
                pk(2),
                (50).to_bytes(8, "little"),
                (2_000_000_000).to_bytes(8, "little"),
                (100).to_bytes(8, "little"),
                pk(3),
                (1790319000).to_bytes(8, "little", signed=True),
                pk(4),
                pk(5),
            ]
        )
        ev = decode_migration_event(mig)
        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev["type"], "migration")
        self.assertEqual(ev["mint"], b58encode(pk(2)))
        self.assertEqual(ev["trader"], b58encode(pk(1)))
        self.assertEqual(ev["sol_lamports"], 2_000_000_000)
        self.assertEqual(ev["token_raw"], 50)
        self.assertEqual(ev["pool"], b58encode(pk(4)))
        self.assertEqual(ev["event_ts"], 1790319000)

        comp = b"".join(
            [
                bytes.fromhex("5f72619cd42e9808"),
                pk(1),
                pk(2),
                pk(3),
                (1790319001).to_bytes(8, "little", signed=True),
                pk(5),
            ]
        )
        done = decode_complete_event(comp)
        self.assertIsNotNone(done)
        assert done is not None
        self.assertEqual(done["type"], "complete")
        self.assertEqual(done["mint"], b58encode(pk(2)))
        native = b"".join(
            [
                bytes.fromhex("5f72619cd42e9808"),
                pk(1),
                pk(2),
                pk(3),
                (1790319001).to_bytes(8, "little", signed=True),
                bytes(32),
            ]
        )
        native_ev = decode_complete_event(native)
        assert native_ev is not None
        self.assertEqual(native_ev["quote_mint"], WSOL_MINT)
        found_c, found_m = lifecycle_from_logs(
            [
                "Program data: " + base64.b64encode(mig).decode(),
                "Program data: " + base64.b64encode(comp).decode(),
            ]
        )
        self.assertEqual(found_c, [])
        self.assertEqual([row["type"] for row in found_m], ["migration", "complete"])


class TapeSchemaTests(unittest.TestCase):
    def test_bonding_row_nulls_receive_time(self) -> None:
        decoded = records_from_logs(
            [_line("pump_trade_event.b64")],
            slot=SLOT,
            signature=SIG,
            t_recv_ms=0,
            commitment="confirmed",
            feed="public_rpc_getblock",
        )
        row = backfill_trade_row(decoded[0], BLOCK_TIME)
        self.assertEqual(row["source"], SOURCE)
        self.assertIsNone(row["t_recv"])
        self.assertIsNone(row["t_recv_ms"])
        self.assertEqual(row["block_time"], BLOCK_TIME)
        self.assertEqual(row["v"], 1)
        self.assertEqual(row["venue"], "pump_bonding")
        self.assertEqual(row["sol_lamports"], 1000)
        self.assertEqual(row["token_raw"], 12728720)
        self.assertEqual(row["slot"], SLOT)
        self.assertEqual(row["event_ts"], 1790318932)
        self.assertEqual(row["feed"], "public_rpc_getblock")

    def test_block_decode_and_pumpswap_lookup(self) -> None:
        block = {
            "slot": SLOT,
            "blockTime": BLOCK_TIME,
            "transactions": [
                {
                    "meta": {"err": None, "logMessages": [_line("pump_trade_event.b64")]},
                    "transaction": {"signatures": [SIG]},
                },
                {
                    "meta": {"err": {"InstructionError": [0, "Custom"]}, "logMessages": [_line("pump_trade_event.b64")]},
                    "transaction": {"signatures": ["failed"]},
                },
            ],
        }
        out = rows_from_block(block, {})
        self.assertEqual(len(out["trades"]), 1)
        self.assertEqual(out["trades"][0]["signature"], SIG)
        self.assertIsNone(out["trades"][0]["t_recv_ms"])
        pending = records_from_logs(
            [_line("pumpswap_sell_event.b64")],
            slot=SLOT,
            signature=SIG,
            t_recv_ms=0,
            commitment="confirmed",
            feed="public_rpc_getblock",
        )
        self.assertIsNone(pending[0]["mint"])

        def lookup(pools: list[str]) -> dict[str, tuple[str, str]]:
            self.assertEqual(len(pools), 1)
            return {pools[0]: ("Mint111", WSOL_MINT)}

        ready, dropped = resolve_unresolved(pending, {}, BLOCK_TIME, lookup)
        self.assertEqual(dropped, 0)
        self.assertEqual(ready[0]["mint"], "Mint111")
        self.assertEqual(ready[0]["quote_is_wsol"], True)
        self.assertEqual(ready[0]["source"], SOURCE)
        self.assertIsNone(ready[0]["t_recv_ms"])
        self.assertEqual(ready[0]["v"], 2)

    def test_compare_matches_amounts(self) -> None:
        live = [
            {
                "signature": "a",
                "event_index": 0,
                "venue": "pump_bonding",
                "mint": "m",
                "trader": "t",
                "side": "buy",
                "sol_lamports": 5,
                "token_raw": 9,
                "slot": 1,
                "event_ts": 2,
                "quote_reserve": 3,
                "base_reserve": 4,
            }
        ]
        back = [dict(live[0]), dict(live[0], signature="b", zero_sol=True, sol_lamports=0)]
        report = compare_trades(live, back)
        self.assertEqual(report["match"], 1)
        self.assertEqual(report["only_backfill"], 1)
        self.assertEqual(report["only_backfill_zero_sol"], 1)
        self.assertEqual(report["only_live"], 0)


if __name__ == "__main__":
    unittest.main()
