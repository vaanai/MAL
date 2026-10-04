"""Offline tests for tools/pumpswap_decompose.py (event decode, mint extensions, corrected quote)."""

from __future__ import annotations

import base64
import json
import struct
import unittest
from pathlib import Path

from tools import pumpswap_decompose as d
from tools import pumpswap_tx as tx

FIX = Path(__file__).parent / "fixtures" / "pumpswap"


def synth_event(**kw) -> bytes:
    vals = dict(timestamp=1, base_amount_out=102_211_917_194, max_quote_amount_in=500_000_000, user_base_token_reserves=0,
                user_quote_token_reserves=7, pool_base_token_reserves=60_822_600_309_479, pool_quote_token_reserves=276_506_663_183,
                quote_amount_in=500_000_000, lp_fee_basis_points=20, lp_fee=990_100, protocol_fee_basis_points=5, protocol_fee=247_525,
                quote_amount_in_with_lp_fee=496_039_603, user_quote_amount_in=495_049_503)
    vals.update(kw)
    b = d.BUY_EVENT_DISC
    for n in d.EVENT_FIELDS_U64:
        b += struct.pack("<q" if n == "timestamp" else "<Q", vals[n])
    b += bytes(32 * len(d.EVENT_PUBKEYS))
    b += struct.pack("<QQ", 75, 3_712_872)
    return b


class DecomposeTests(unittest.TestCase):
    def test_decode_buy_event(self):
        ev = d.decode_buy_event(synth_event())
        self.assertEqual(ev["base_amount_out"], 102_211_917_194)
        self.assertEqual(ev["tail_u64"][:2], [75, 3_712_872])
        with self.assertRaises(ValueError):
            d.decode_buy_event(bytes(300))

    def test_find_buy_event_in_logs(self):
        line = "Program data: " + base64.b64encode(synth_event()).decode()
        self.assertIsNotNone(d.find_buy_event(["x", line]))
        self.assertIsNone(d.find_buy_event(["Program data: AAAA"]))

    def test_corrected_quote_reproduces_recorded_event(self):
        # Recorded 2026-10-04 simulation on pool 3ABzyq, 0.5 SOL; virtual quote from the pool account.
        v = tx.parse_pool_account(base64.b64decode(json.loads((FIX / "sell_a.json").read_text())["pool_account_b64"]))["virtual_quote_reserves"]
        self.assertEqual(v, 17_584_269_263)
        q = d.corrected_quote(500_000_000, 276_506_663_183, 60_822_600_309_479, v, 100)
        self.assertLess(abs(q["tokens"] - 102_211_917_194) / 102_211_917_194, 1e-6)
        paper_like = 495_000_000 * 60_822_600_309_479 // (276_506_663_183 + 495_000_000)
        self.assertGreater(paper_like, q["tokens"] * 1.05)  # without the virtual quote the gap is >5%

    def test_virtual_quote_in_every_fixture_pool(self):
        for f in FIX.glob("*.json"):
            j = json.loads(f.read_text())
            if "pool_account_b64" in j:
                v = tx.parse_pool_account(base64.b64decode(j["pool_account_b64"]))["virtual_quote_reserves"]
                self.assertTrue(17_000_000_000 < v < 18_000_000_000, f.name)

    def test_mint_extensions_no_transfer_fee(self):
        data = bytes(165) + bytes([1]) + struct.pack("<HH", 18, 64) + bytes(64) + struct.pack("<HH", 19, 8) + bytes(8)
        ext = d.parse_mint_extensions(data)
        self.assertEqual([e["type"] for e in ext["extensions"]], [18, 19])
        self.assertFalse(any(e.get("name") == "TransferFeeConfig" for e in ext["extensions"]))


if __name__ == "__main__":
    unittest.main()
