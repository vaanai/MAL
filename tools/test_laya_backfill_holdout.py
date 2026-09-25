"""Backward holdout: synthetic receive time and sealed backfill days. No live tape."""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

from tools.graduated_swing import synthetic_recv_ms
from tools.laya_backfill_holdout import (
    HOLDOUT_END_ISO,
    LagDraw,
    day_status,
    expected_hour_keys,
    holdout_end_s,
    live_train_tape,
    migration_times,
    sealed_holdout_hours,
    stamp_backfill_row,
)
from tools.laya_v0 import CANDIDATE_FREEZE_MS, DecisionRow, MintBook, load_books
from tools.paper_price_path import CreateSignal


BLOCK = 1_790_316_000  # 2026-09-25T06:00:00Z
HOP = 26


def _trade(mint: str = "MintA", **overrides: object) -> dict:
    row = {
        "type": "trade",
        "source": "backfill",
        "venue": "pump_bonding",
        "mint": mint,
        "quote_is_wsol": True,
        "side": "buy",
        "t_recv_ms": None,
        "block_time": BLOCK,
        "event_ts": BLOCK,
        "signature": "sig-" + mint,
        "event_index": 0,
        "slot": 3,
        "sol_lamports": 1_000_000,
        "token_raw": 10,
        "quote_reserve": 30_000_000_000,
        "base_reserve": 1_000_000_000_000,
        "trader": "Wallet",
    }
    row.update(overrides)
    return row


class ReceiveClockTests(unittest.TestCase):
    def test_stamp_is_block_plus_lag_plus_hop_not_block_time(self) -> None:
        stamped = stamp_backfill_row(_trade(), 1_400, HOP)
        assert stamped is not None
        self.assertEqual(stamped["t_recv_ms"], BLOCK * 1000 + 1_400 + HOP)
        self.assertNotEqual(stamped["t_recv_ms"], BLOCK * 1000)
        self.assertNotEqual(stamped["t_recv_ms"], BLOCK)
        self.assertTrue(stamped["recv_synthetic"])

    def test_negative_chain_draw_cannot_cancel_the_hop(self) -> None:
        self.assertEqual(synthetic_recv_ms(BLOCK, -5_000, HOP), BLOCK * 1000 + HOP)
        stamped = stamp_backfill_row(_trade(), -5_000, HOP)
        assert stamped is not None
        self.assertEqual(stamped["t_recv_ms"], BLOCK * 1000 + HOP)

    def test_missing_block_time_is_dropped(self) -> None:
        row = _trade()
        row.pop("block_time")
        row.pop("event_ts")
        self.assertIsNone(stamp_backfill_row(row, 1_400, HOP))

    def test_live_row_keeps_its_receive_time(self) -> None:
        live = _trade(source="live", t_recv_ms=BLOCK * 1000 + 50)
        stamped = stamp_backfill_row(live, 1_400, HOP)
        assert stamped is not None
        self.assertEqual(stamped["t_recv_ms"], BLOCK * 1000 + 50)
        self.assertNotIn("recv_synthetic", stamped)

    def test_loader_does_not_invent_a_clock_from_block_time(self) -> None:
        create = CreateSignal(
            mint="MintA",
            t_signal_ms=BLOCK * 1000,
            creator=None,
            signature=None,
            v_sol=30.0,
            v_token_ui=1_000_000.0,
            mcap_sol=30.0,
            initial_buy_ui=None,
            sol_amount=None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades-2026-09-25T06.jsonl"
            path.write_text(json.dumps(_trade()) + "\n", encoding="utf-8")
            books, stats = load_books({"MintA": create}, [path])
        self.assertEqual(stats.kept, 0)
        self.assertEqual(books["MintA"].flow, [])
        self.assertEqual(list(getattr(stats, "chain_lags_ms", [])), [])

    def test_stamped_row_is_kept_and_does_not_join_the_lag_pool(self) -> None:
        create = CreateSignal(
            mint="MintA",
            t_signal_ms=BLOCK * 1000 + HOP,
            creator=None,
            signature=None,
            v_sol=30.0,
            v_token_ui=1_000_000.0,
            mcap_sol=30.0,
            initial_buy_ui=None,
            sol_amount=None,
        )
        draw = LagDraw([1_400], HOP, seed=1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades-2026-09-25T06.jsonl"
            path.write_text(json.dumps(_trade()) + "\n", encoding="utf-8")
            books, stats = load_books({"MintA": create}, [path], prepare_row=draw)
        self.assertEqual(stats.kept, 1)
        self.assertEqual(books["MintA"].flow[0].t_recv_ms, BLOCK * 1000 + 1_400 + HOP)
        self.assertEqual(list(getattr(stats, "chain_lags_ms", [])), [])
        self.assertEqual(draw.stamped, 1)

    def test_one_draw_per_signature(self) -> None:
        lags = list(range(1000))
        draw = LagDraw(lags, HOP, seed=0)
        probe = random.Random(0)
        first = lags[probe.randrange(len(lags))]
        second = lags[probe.randrange(len(lags))]
        first_buy = draw(_trade(signature="sigA", event_index=0))
        second_buy = draw(_trade(signature="sigA", event_index=1))
        other = draw(_trade(signature="sigB", event_index=0))
        assert first_buy is not None and second_buy is not None and other is not None
        self.assertEqual(first_buy["t_recv_ms"], BLOCK * 1000 + first + HOP)
        self.assertEqual(second_buy["t_recv_ms"], first_buy["t_recv_ms"])
        self.assertEqual(other["t_recv_ms"], BLOCK * 1000 + second + HOP)
        self.assertNotEqual(first_buy["t_recv_ms"], other["t_recv_ms"])
        self.assertEqual(draw.stamped, 3)

    def test_out_of_order_rows_share_one_draw_across_slots(self) -> None:
        lags = list(range(1000))
        draw = LagDraw(lags, HOP, seed=2)
        probe = random.Random(2)
        first = lags[probe.randrange(len(lags))]
        second = lags[probe.randrange(len(lags))]
        third = lags[probe.randrange(len(lags))]
        # Later inner event is seen first, then another slot, then the earlier event.
        late = draw(_trade(signature="sigA", event_index=1, slot=11))
        other = draw(_trade(signature="sigB", event_index=0, slot=4))
        early = draw(_trade(signature="sigA", event_index=0, slot=11))
        assert late is not None and other is not None and early is not None
        self.assertEqual(late["t_recv_ms"], BLOCK * 1000 + first + HOP)
        self.assertEqual(early["t_recv_ms"], late["t_recv_ms"])
        self.assertEqual(other["t_recv_ms"], BLOCK * 1000 + second + HOP)
        self.assertNotEqual(other["t_recv_ms"], late["t_recv_ms"])
        draw.begin_file("hour-2")
        again = draw(_trade(signature="sigA", event_index=0, slot=11))
        assert again is not None
        self.assertEqual(again["t_recv_ms"], BLOCK * 1000 + third + HOP)
        self.assertNotEqual(again["t_recv_ms"], late["t_recv_ms"])

    def test_missing_signature_draws_per_row(self) -> None:
        lags = list(range(1000))
        draw = LagDraw(lags, HOP, seed=1)
        probe = random.Random(1)
        first = lags[probe.randrange(len(lags))]
        second = lags[probe.randrange(len(lags))]
        a = draw(_trade(signature=None, event_index=0))
        b = draw(_trade(signature="UNK", event_index=1))
        assert a is not None and b is not None
        self.assertEqual(a["t_recv_ms"], BLOCK * 1000 + first + HOP)
        self.assertEqual(b["t_recv_ms"], BLOCK * 1000 + second + HOP)
        self.assertNotEqual(a["t_recv_ms"], b["t_recv_ms"])


class SealedDayTests(unittest.TestCase):
    def test_boundary_day_stops_at_the_gap_hour(self) -> None:
        keys = expected_hour_keys("2026-09-25", holdout_end_s())
        self.assertEqual(keys[0], "2026-09-25T00")
        self.assertEqual(keys[-1], "2026-09-25T06")
        self.assertNotIn("2026-09-25T07", keys)
        self.assertEqual(HOLDOUT_END_ISO, "2026-09-25T06:58:00Z")

    def test_partial_day_is_not_complete_and_live_hour_is_ignored(self) -> None:
        end_s = holdout_end_s()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trades = root / "trades"
            trades.mkdir()
            for hour, end in (
                ("2026-09-25T03", end_s - 3 * 3600),
                ("2026-09-25T06", end_s),
                ("2026-09-25T07", end_s + 3600),
            ):
                (trades / f"trades-{hour}.jsonl").write_text("{}\n", encoding="utf-8")
                (root / f"stats-{hour}.json").write_text(
                    json.dumps(
                        {
                            "block_time_start": end - 3600,
                            "block_time_end": end,
                        }
                    ),
                    encoding="utf-8",
                )
            # Open hour: jsonl only, no stats file.
            (trades / "trades-2026-09-25T02.jsonl").write_text("{}\n", encoding="utf-8")
            hours = sealed_holdout_hours(root, end_s)
        got = [hour["hour"] for hour in hours]
        self.assertEqual(got, ["2026-09-25T03", "2026-09-25T06"])
        status = day_status(hours, end_s)
        self.assertEqual(len(status), 1)
        self.assertFalse(status[0]["complete"])
        self.assertEqual(status[0]["sealed_hours"], ["2026-09-25T03", "2026-09-25T06"])
        self.assertIn("2026-09-25T00", status[0]["expected_hours"])
        self.assertIn("2026-09-25T02", status[0]["expected_hours"])

    def test_full_holdout_day_is_sealed(self) -> None:
        end_s = holdout_end_s()
        keys = expected_hour_keys("2026-09-25", end_s)
        hours = [
            {"hour": key, "day": "2026-09-25", "block_time_start": 0, "block_time_end": 1}
            for key in keys
        ]
        status = day_status(hours, end_s)
        self.assertTrue(status[0]["complete"])


class TrainWindowTests(unittest.TestCase):
    def test_live_fit_keeps_the_freeze_pad_and_drops_later_hours(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            keep = root / "trades-2026-09-25T15.jsonl"
            pad = root / "trades-2026-09-25T16.jsonl"
            late = root / "trades-2026-09-25T17.jsonl"
            for path in (keep, pad, late):
                path.write_text("", encoding="utf-8")
            found = {path.name for path in live_train_tape(root, CANDIDATE_FREEZE_MS)}
        self.assertIn(keep.name, found)
        self.assertIn(pad.name, found)
        self.assertNotIn(late.name, found)

    def test_migration_requires_an_earlier_bonding_print(self) -> None:
        from tools.laya_v0 import FlowPrint

        def pr(t_ms: int, venue: str) -> FlowPrint:
            return FlowPrint(
                t_recv_ms=t_ms,
                slot=1,
                event_index=0,
                venue=venue,
                side="buy",
                sol_lamports=1,
                token_raw=1,
                trader="W",
                quote_reserve=30_000_000_000,
                base_reserve=1_000_000_000_000,
                price_sol=1e-8,
                market_cap_sol=10.0,
            )

        create = CreateSignal("MintA", 1_000, None, None, None, None, None, None, None)
        bonded = MintBook(create=create, flow=[pr(1_000, "pump_bonding"), pr(2_000, "pumpswap")])
        swap_only = MintBook(create=CreateSignal("MintB", 1_000, None, None, None, None, None, None, None), flow=[pr(2_000, "pumpswap")])
        found = migration_times({"MintA": bonded, "MintB": swap_only}, window_start_ms=0)
        self.assertEqual(found, {"MintA": 2_000})


class FreezeSplitTests(unittest.TestCase):
    def test_backfill_rows_are_not_a_training_row(self) -> None:
        train = [
            DecisionRow("A", None, 1, CANDIDATE_FREEZE_MS, "grid", {}),
            DecisionRow("B", None, 1, CANDIDATE_FREEZE_MS + 1, "grid", {}),
        ]
        kept = [row for row in train if row.decision_t_ms <= CANDIDATE_FREEZE_MS]
        self.assertEqual([row.mint for row in kept], ["A"])


if __name__ == "__main__":
    unittest.main()
