"""Unit tests for EXP-007e instr/quote resolve helpers."""

from __future__ import annotations

import unittest

from tools.exp003_rpc_backfill import _PUMP_CURVE_ACCOUNT_DISC
from tools.exp007_instr_quote_resolve import (
    QUOTE_MINT_OFFSET,
    instr_from_transaction_and_logs,
    is_plausible_mint,
    quote_mint_from_curve_bytes,
    venue_from_transaction,
)
from tools.exp007_instr_quote_resolve import WSOL_MINT


def _build_extended_curve(*, quote_mint: bytes) -> bytes:
    data = bytearray(_PUMP_CURVE_ACCOUNT_DISC)
    data.extend((1).to_bytes(8, "little"))
    data.extend((1).to_bytes(8, "little"))
    data.extend((0).to_bytes(8, "little"))
    data.extend((0).to_bytes(8, "little"))
    data.extend((1).to_bytes(8, "little"))
    data.append(0)
    data.extend(bytes(32))
    data.append(0)
    data.append(0)
    data.extend(quote_mint)
    return bytes(data)


class QuoteMintOffsetTests(unittest.TestCase):
    def test_quote_mint_at_offset_83(self) -> None:
        from tools.exp003_rpc_backfill import _b58decode

        raw_mint = _b58decode(WSOL_MINT)
        self.assertIsNotNone(raw_mint)
        data = _build_extended_curve(quote_mint=raw_mint)
        self.assertGreaterEqual(len(data), QUOTE_MINT_OFFSET + 32)
        self.assertEqual(quote_mint_from_curve_bytes(data), WSOL_MINT)

    def test_invalid_system_program_mint_rejected(self) -> None:
        data = _build_extended_curve(quote_mint=bytes(32))
        self.assertIsNone(quote_mint_from_curve_bytes(data))


class VenueInstrTests(unittest.TestCase):
    def test_launchlab_venue_and_instr(self) -> None:
        tx = {
            "transaction": {
                "message": {
                    "instructions": [
                        {"programId": "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"}
                    ]
                }
            },
            "meta": {
                "logMessages": ["Program log: Instruction: InitializeWithToken2022"],
            },
        }
        self.assertEqual(venue_from_transaction(tx), "launchlab")
        self.assertEqual(
            instr_from_transaction_and_logs(tx, tx["meta"]["logMessages"]),
            "launchlab_init",
        )


if __name__ == "__main__":
    unittest.main()
