"""Unit tests for EXP-007c fee-at-T resolution."""

from __future__ import annotations

import unittest

from tools.exp003_rpc_backfill import _PUMP_CURVE_ACCOUNT_DISC
from tools.exp007_fee_resolve import (
    _GLOBAL_FEE_BPS_OFFSET,
    _PUMP_GLOBAL_ACCOUNT_DISC,
    classify_fee_tag,
    parse_bonding_curve_fee_extension,
    parse_global_fee_bps,
)


def _build_global_account_bytes(*, fee_bps: int = 100) -> bytes:
    data = bytearray(_PUMP_GLOBAL_ACCOUNT_DISC)
    data.append(1)  # initialized
    data.extend(bytes(32))  # authority
    data.extend(bytes(32))  # fee_recipient
    for _ in range(4):
        data.extend((0).to_bytes(8, "little"))
    data.extend(fee_bps.to_bytes(8, "little"))
    return bytes(data)


def _build_extended_curve(*, holder: bool = False, creator_bps: int = 0) -> bytes:
    data = bytearray(_PUMP_CURVE_ACCOUNT_DISC)
    data.extend((1).to_bytes(8, "little"))
    data.extend((1).to_bytes(8, "little"))
    data.extend((0).to_bytes(8, "little"))
    data.extend((0).to_bytes(8, "little"))
    data.extend((1).to_bytes(8, "little"))
    data.append(0)  # complete
    data.extend(bytes(32))  # creator
    data.append(0)  # mayhem
    data.append(0)  # cashback
    data.extend(bytes(32))  # quote_mint
    data.extend(creator_bps.to_bytes(8, "little"))
    data.append(0)  # can_edit
    data.append(1 if holder else 0)
    return bytes(data)


class GlobalParseTests(unittest.TestCase):
    def test_parse_global_100bps(self) -> None:
        raw = _build_global_account_bytes(fee_bps=100)
        self.assertEqual(parse_global_fee_bps(raw), 100)
        self.assertGreaterEqual(len(raw), _GLOBAL_FEE_BPS_OFFSET + 8)


class ClassifyFeeTests(unittest.TestCase):
    def test_global_100bps_standard(self) -> None:
        res = classify_fee_tag(
            global_fee_bps=100,
            global_ok=True,
            is_holder_reward=False,
            creator_fee_bps=0,
        )
        self.assertEqual(res.fee, "global_100bps")

    def test_holder_reward_dynamic(self) -> None:
        raw = _build_extended_curve(holder=True)
        holder, bps = parse_bonding_curve_fee_extension(raw)
        self.assertTrue(holder)
        res = classify_fee_tag(
            global_fee_bps=100,
            global_ok=True,
            is_holder_reward=holder,
            creator_fee_bps=bps,
        )
        self.assertEqual(res.fee, "creator_dynamic")

    def test_global_95bps_proposed_enum(self) -> None:
        res = classify_fee_tag(
            global_fee_bps=95,
            global_ok=True,
            is_holder_reward=False,
            creator_fee_bps=0,
        )
        self.assertEqual(res.fee, "global_95bps")
        self.assertEqual(res.reason, "global.fee_basis_points")

    def test_other_nonstandard_global_stays_unverified(self) -> None:
        res = classify_fee_tag(
            global_fee_bps=90,
            global_ok=True,
            is_holder_reward=False,
            creator_fee_bps=0,
        )
        self.assertEqual(res.fee, "unverified")
        self.assertIn("nonstandard_90", res.reason)


if __name__ == "__main__":
    unittest.main()
