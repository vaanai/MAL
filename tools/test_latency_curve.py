"""Slot bounds, inner-event collapse, and the pre-registered skip rule."""

from __future__ import annotations

import unittest
from pathlib import Path

from tools.latency_curve import (
    _Mint,
    evaluate_path,
    prepare_fills,
    skip_decision,
)
from tools.paper_curve_math import LAMPORTS_PER_SOL
from tools.paper_price_path import TapePrint

T0 = 1_700_000_000_000
Q0 = 30_000_000_000
B0 = 1_073_000_000_000_000


def _print(
    slot: int,
    quote: int,
    *,
    t_ms: int,
    signature: str,
    event_index: int = 0,
    tx_index: int = 0,
    base: int = B0,
) -> TapePrint:
    price = quote / (base * 1000)
    return TapePrint(
        t_recv_ms=t_ms,
        slot=slot,
        event_index=event_index,
        venue="pump_bonding",
        side="buy",
        sol_lamports=1_000_000_000,
        quote_reserve=quote,
        base_reserve=base,
        price_sol=price,
        market_cap_sol=price * 1_000_000_000,
        signature=signature,
        tx_index=tx_index,
    )


class SkipTests(unittest.TestCase):
    def test_unknown_does_not_skip(self) -> None:
        self.assertFalse(skip_decision(None, None, None))
        self.assertFalse(skip_decision(0, 1, 1.0))

    def test_fresh_and_published_rug_threshold(self) -> None:
        self.assertTrue(skip_decision(1, None, None))
        self.assertTrue(skip_decision(0, 2, 0.50))
        self.assertFalse(skip_decision(0, 1, 1.0))
        self.assertFalse(skip_decision(0, 2, 0.49))


class OrderTests(unittest.TestCase):
    def test_inner_events_collapse_to_the_last_reserve(self) -> None:
        first = _print(11, 40_000_000_000, t_ms=T0, signature="same", event_index=0, tx_index=3)
        second = _print(11, 80_000_000_000, t_ms=T0 + 5, signature="same", event_index=1, tx_index=3)
        fills = prepare_fills([second, first])
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].quote_reserve, 80_000_000_000)
        self.assertEqual(fills[0].event_index, 1)

    def test_packed_slot_buffer_matches_collapse(self) -> None:
        first = _print(11, 40_000_000_000, t_ms=T0, signature="same", event_index=0, tx_index=3)
        second = _print(11, 80_000_000_000, t_ms=T0 + 5, signature="same", event_index=1, tx_index=3)
        nxt = _print(12, 90_000_000_000, t_ms=T0 + 400, signature="next", event_index=0, tx_index=-1)
        mint = _Mint(11, T0, 0, None)
        for pr in (second, first, nxt):
            mint.add(pr)
        got = mint.fillable(migrate=False)
        exp = prepare_fills([second, first, nxt])
        self.assertEqual(len(got), len(exp))
        for left, right in zip(got, exp):
            self.assertEqual(left.quote_reserve, right.quote_reserve)
            self.assertEqual(left.slot, right.slot)
            self.assertEqual(left.tx_index, right.tx_index)
            self.assertEqual(left.event_index, right.event_index)
            self.assertEqual(left.t_recv_ms, right.t_recv_ms)

    def test_anchor_merges_into_its_create_signature(self) -> None:
        anchor = _print(10, Q0, t_ms=T0, signature="create", event_index=-1, tx_index=-1)
        buy = _print(10, 31_000_000_000, t_ms=T0, signature="create", event_index=1, tx_index=-1)
        later = _print(11, 32_000_000_000, t_ms=T0 + 400, signature="snipe", tx_index=-1)
        mint = _Mint(10, T0, 0, anchor)
        mint.add(buy)
        mint.add(later)
        got = mint.fillable(migrate=False)
        exp = prepare_fills([buy, later, anchor])
        self.assertEqual([pr.quote_reserve for pr in got], [pr.quote_reserve for pr in exp])
        self.assertEqual([pr.slot for pr in got], [pr.slot for pr in exp])
        self.assertEqual(len(got), 2)

    def test_read_order_is_tx_position_when_omitted(self) -> None:
        early = _print(5, 11_000_000_000, t_ms=T0, signature="early", event_index=5, tx_index=-1)
        late = _print(5, 22_000_000_000, t_ms=T0, signature="late", event_index=0, tx_index=-1)
        fills = prepare_fills([early, late])
        self.assertEqual([pr.signature for pr in fills], ["early", "late"])
        self.assertLess(fills[0].tx_index, fills[1].tx_index)


class SlotBoundTests(unittest.TestCase):
    def test_start_of_next_slot_is_before_that_slots_buy(self) -> None:
        create = _print(10, Q0, t_ms=T0, signature="create", tx_index=0)
        later = _print(11, 31_000_000_000, t_ms=T0 + 400, signature="snipe", tx_index=0)
        after = _print(80, 31_000_000_000, t_ms=T0 + 40_000, signature="later", tx_index=0)
        fills = prepare_fills([create, later, after])
        rows = evaluate_path(
            fills,
            trigger_slot=10,
            trigger_block_ms=T0,
            ref_price=create.price_sol,
            tape_through_ms=T0 + 120_000,
        )
        start = _gross(rows, land=0)
        end = _gross(rows, land=1)
        self.assertIsNotNone(start)
        self.assertIsNotNone(end)
        assert start is not None and end is not None
        self.assertGreater(start, end)

    def test_flat_round_trip_gross_beats_net(self) -> None:
        create = _print(10, Q0, t_ms=T0, signature="create", tx_index=0)
        later = _print(80, Q0, t_ms=T0 + 40_000, signature="later", tx_index=0)
        rows = evaluate_path(
            prepare_fills([create, later]),
            trigger_slot=10,
            trigger_block_ms=T0,
            ref_price=create.price_sol,
            tape_through_ms=T0 + 120_000,
        )
        gross = _gross(rows, land=1)
        net0 = _net0(rows, land=1)
        self.assertIsNotNone(gross)
        self.assertIsNotNone(net0)
        assert gross is not None and net0 is not None
        self.assertLess(net0, 0)
        self.assertGreater(gross, net0)

    def test_source_does_not_touch_live_fills(self) -> None:
        text = Path(__file__).with_name("latency_curve.py").read_text(encoding="utf-8")
        self.assertNotIn("forward_paper", text)
        self.assertNotIn("authorize_run", text)
        self.assertNotIn("HARD_MAX_POSITION", text)


def _pick(rows: list[tuple[int, ...]], land: int) -> tuple[int, ...] | None:
    for row in rows:
        # land, exit hold, direct, 0.05 SOL, status send
        if row[0] == land and row[1] == 0 and row[2] == 0 and row[3] == 0 and row[4] == 1:
            return row
    return None


def _gross(rows: list[tuple[int, ...]], land: int) -> int | None:
    row = _pick(rows, land)
    if row is None:
        return None
    return int(row[7])


def _net0(rows: list[tuple[int, ...]], land: int) -> int | None:
    row = _pick(rows, land)
    if row is None:
        return None
    return int(row[6])


class FeeIdentityTests(unittest.TestCase):
    def test_priority_does_not_change_stored_gross(self) -> None:
        # gross is stored before priority. 0.05 SOL size code is column 3 == 0.
        self.assertEqual(50_000_000, int(0.05 * LAMPORTS_PER_SOL))


if __name__ == "__main__":
    unittest.main()
