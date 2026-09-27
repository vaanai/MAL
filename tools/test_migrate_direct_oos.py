"""Frozen migrate-direct cell. No live runner, no network."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools.latency_curve import EXITS, LANDS, ROUTES, SIZES, evaluate_path
from tools.migrate_direct_oos import (
    FROZEN_AT,
    FORWARD_START,
    PRESSURE_INTERCEPT,
    PRIORITY_LAMPORTS,
    SELECTION_START,
    cell_rows,
    forward_hours,
    oos_hours,
    prepare_row,
    summarize,
    update_forward,
    update_oos,
)
from tools.paper_curve_math import LAMPORTS_PER_SOL
from tools.paper_price_path import TapePrint


def _stamp(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def _print(t_ms: int, slot: int, quote: int, base: int) -> TapePrint:
    price = quote / (base * 1000)
    return TapePrint(
        t_recv_ms=t_ms,
        slot=slot,
        event_index=1,
        venue="pumpswap",
        side="buy",
        sol_lamports=1_000_000,
        quote_reserve=quote,
        base_reserve=base,
        price_sol=price,
        market_cap_sol=price * 1_000_000_000,
        signature=f"sig-{slot}",
        tx_index=0,
    )


class FrozenCellTests(unittest.TestCase):
    def test_constants_match_the_freeze(self) -> None:
        self.assertEqual(FROZEN_AT, "2026-09-27T13:06:36Z")
        self.assertEqual(SELECTION_START, "2026-09-22T10:00:00Z")
        self.assertEqual(FORWARD_START, "2026-09-28T00:00:00Z")
        self.assertEqual(PRIORITY_LAMPORTS, 500_000)
        self.assertEqual(PRESSURE_INTERCEPT, -1.4548727851312098)
        self.assertEqual(LANDS[0][1:], ("slot+1", "start", 1))
        self.assertEqual(EXITS[1], "tp50_sl30")
        self.assertEqual(ROUTES[0], ("direct", 0))
        self.assertEqual(SIZES, (50_000_000, 500_000_000))
        source = Path("tools/migrate_direct_oos.py").read_text(encoding="utf-8")
        for banned in ("forward_paper", "promotion_pnls", "HARD_MAX", "CEILING_", "ForwardEngine"):
            self.assertNotIn(banned, source)

    def test_cell_rows_match_evaluate_path(self) -> None:
        t0 = 1_700_000_000_000
        fills = [_print(t0, 100, 80_000_000_000, 400_000_000_000_000)]
        through = t0 + 31 * 60 * 1000
        direct = cell_rows(
            fills,
            trigger_slot=100,
            trigger_block_ms=t0,
            ref_price=fills[0].price_sol,
            tape_through_ms=through,
        )
        full = [
            row
            for row in evaluate_path(
                fills,
                trigger_slot=100,
                trigger_block_ms=t0,
                ref_price=fills[0].price_sol,
                tape_through_ms=through,
            )
            if row[0] == 0 and row[1] == 1 and row[2] == 0
        ]
        self.assertEqual(
            [(size_i, status, sides, net0, gross) for size_i, status, sides, net0, gross, _b, _n in direct],
            [(row[3], row[4], row[5], row[6], row[7]) for row in full],
        )

    def test_prepare_row_does_not_invent_a_forward_receive_time(self) -> None:
        block = {"block_time": 100, "mint": "M"}
        filled = prepare_row(dict(block), receive="block")
        self.assertEqual(filled["t_recv_ms"], 100_000)
        self.assertIsNone(prepare_row(dict(block), receive="tape"))
        self.assertEqual(prepare_row({"t_recv_ms": 5, "block_time": 1}, receive="tape")["t_recv_ms"], 5)

    def test_selection_hour_is_excluded(self) -> None:
        root = Path("/tmp/migrate-direct-oos-hours")
        # unittest tmp via TemporaryDirectory would be cleaner; this path is local.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            backfill = Path(tmp)
            trades = backfill / "trades"
            trades.mkdir()
            start = _stamp("2026-09-22T09:00:00Z")
            for key, offset in (("2026-09-22T09", 0), ("2026-09-22T10", 3600)):
                (trades / f"trades-{key}.jsonl").write_text("{}\n", encoding="utf-8")
                stats = {"block_time_start": start + offset, "block_time_end": start + offset + 3600}
                (backfill / f"stats-{key}.json").write_text(json.dumps(stats), encoding="utf-8")
            hours = oos_hours(backfill)
        self.assertEqual([hour["hour"] for hour in hours], ["2026-09-22T09"])

    def test_forward_hour_waits_until_it_is_sealed(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tape = Path(tmp)
            start = _stamp(FORWARD_START)
            (tape / "trades-2026-09-28T00.jsonl").write_text("{}\n", encoding="utf-8")
            (tape / "trades-2026-09-27T23.jsonl.zst").write_bytes(b"")
            open_hour = forward_hours(tape, start, start + 10)
            self.assertEqual(open_hour, [])
            (tape / "trades-2026-09-28T00.jsonl.zst").write_bytes(b"")
            sealed = forward_hours(tape, start, start + 10)
            self.assertEqual([hour["hour"] for hour in sealed], ["2026-09-28T00"])

    def test_second_pass_does_not_duplicate_an_hour(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backfill = root / "backfill"
            trades = backfill / "trades"
            trades.mkdir(parents=True)
            start = _stamp("2026-09-22T09:00:00Z")
            (trades / "trades-2026-09-22T09.jsonl").write_text("{}\n", encoding="utf-8")
            (backfill / "stats-2026-09-22T09.json").write_text(
                json.dumps({"block_time_start": start, "block_time_end": start + 3600}),
                encoding="utf-8",
            )
            out = root / "out"
            first = update_oos(backfill, out, None)
            second = update_oos(backfill, out, None)
            self.assertEqual(first["hours"], ["2026-09-22T09"])
            self.assertEqual(second["hours_added"], [])
            self.assertEqual(second["sizes"]["0.5"]["n"], 0)
            text = (out / "attempts.jsonl").read_text(encoding="utf-8")
            self.assertEqual(text, "")

    def test_older_hour_append_does_not_rescore_a_later_mint(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backfill = root / "backfill"
            trades = backfill / "trades"
            creates = backfill / "creates"
            trades.mkdir(parents=True)
            creates.mkdir()
            start = _stamp("2026-09-22T09:00:00Z")
            block = start + 30
            t_ms = block * 1000
            create = {
                "type": "create",
                "mint": "MintA",
                "slot": 100,
                "block_time": block,
                "quote_mint": "So11111111111111111111111111111111111111112",
                "signature": "create-a",
            }
            bond = {
                "type": "trade",
                "mint": "MintA",
                "venue": "pump_bonding",
                "quote_is_wsol": True,
                "t_recv_ms": t_ms,
                "block_time": block,
                "slot": 100,
                "signature": "bond-a",
                "quote_reserve": 30_000_000_000,
                "base_reserve": 1_000_000_000_000_000,
                "side": "buy",
                "sol_lamports": 1_000_000,
            }
            swap = dict(bond)
            swap.update(
                venue="pumpswap",
                t_recv_ms=t_ms + 800,
                block_time=block + 1,
                slot=101,
                signature="swap-a",
                quote_reserve=115_000_000_000,
                base_reserve=400_000_000_000_000,
            )
            (creates / "creates-2026-09-22T09.jsonl").write_text(json.dumps(create) + "\n", encoding="utf-8")
            (trades / "trades-2026-09-22T09.jsonl").write_text(
                json.dumps(bond) + "\n" + json.dumps(swap) + "\n",
                encoding="utf-8",
            )
            (backfill / "stats-2026-09-22T09.json").write_text(
                json.dumps({"block_time_start": start, "block_time_end": start + 3600}),
                encoding="utf-8",
            )
            out = root / "out"
            first = update_oos(backfill, out, None)
            first_n = first["sizes"]["0.5"]["n"]
            self.assertGreater(first_n, 0)
            older = start - 3600
            (creates / "creates-2026-09-22T08.jsonl").write_text(
                json.dumps({**create, "mint": "MintB", "signature": "create-b"}) + "\n",
                encoding="utf-8",
            )
            (trades / "trades-2026-09-22T08.jsonl").write_text("{}\n", encoding="utf-8")
            (backfill / "stats-2026-09-22T08.json").write_text(
                json.dumps({"block_time_start": older, "block_time_end": start}),
                encoding="utf-8",
            )
            second = update_oos(backfill, out, None)
            self.assertEqual(second["hours_added"], ["2026-09-22T08"])
            self.assertEqual(second["sizes"]["0.5"]["n"], first_n)

    def test_forward_update_uses_only_the_half_sol_size(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tape = root / "tape"
            tape.mkdir()
            start = _stamp(FORWARD_START)
            (tape / "trades-2026-09-28T00.jsonl").write_text("{}\n", encoding="utf-8")
            report = update_forward(tape, root / "out", None, now_s=start + 4000)
            self.assertEqual(report["hours"], ["2026-09-28T00"])
            self.assertIn("0.5", report["sizes"])
            self.assertNotIn("0.05", report["sizes"])
            self.assertEqual(report["sizes"]["0.5"]["n"], 0)

    def test_summary_applies_the_frozen_priority(self) -> None:
        rows = [
            {"day": "2026-09-21", "size_i": 1, "status": 0, "sides": 1, "net0": 0, "gross": 0, "buys": 0, "nearby": 0},
            {"day": "2026-09-21", "size_i": 1, "status": 1, "sides": 2, "net0": 1_000_000, "gross": 0, "buys": 0, "nearby": 0},
        ]
        stats = summarize(rows, size_i=1)
        self.assertEqual(stats["n"], 2)
        self.assertEqual(stats["fill_rate"], 0.5)
        miss = -PRIORITY_LAMPORTS
        send = (1.0 - 0.15) * (1_000_000 - 2 * PRIORITY_LAMPORTS) + 0.15 * (-PRIORITY_LAMPORTS)
        mean = (miss + send) / 2
        self.assertAlmostEqual(stats["mean_net_flat_sol"], mean / LAMPORTS_PER_SOL)
        self.assertFalse(stats["promotion_clear"])


if __name__ == "__main__":
    unittest.main()
