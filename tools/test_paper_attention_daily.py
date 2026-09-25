"""Genuine-arrival filter, event lag, LAYA join JSONL. No tape."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from observe.attention import (
    SNAPSHOT_GRACE_MS,
    ensure_startup,
    event_time_ms,
    is_genuine_arrival,
    laya_join_record,
    reconstruct_snapshot_keys,
)
from tools.paper_attention_daily import emit_laya_join, genuine_rows, lag_vs_event, list_hourly_tapes
from tools.paper_attention_promote import MIN_N, WATCH_N, BookTrade, book_stats


class SnapshotTests(unittest.TestCase):
    def test_reconstruct_keeps_startup_window_only(self) -> None:
        t0 = 1_000_000
        seen = {
            ("dex_boost", "A"): t0,
            ("pump_live", "B"): t0 + 60_000,
            ("pump_live", "C"): t0 + SNAPSHOT_GRACE_MS + 1,
        }
        snap = reconstruct_snapshot_keys(seen, t0)
        self.assertIn(("dex_boost", "A"), snap)
        self.assertIn(("pump_live", "B"), snap)
        self.assertNotIn(("pump_live", "C"), snap)

    def test_ensure_startup_reconstructs_existing_tape(self) -> None:
        from observe.attention import FirstSeenIndex

        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            idx = FirstSeenIndex()
            idx.seen[("dex_boost", "Old111111111111111111111111111111111pump")] = 50
            idx.seen[("pump_live", "New111111111111111111111111111111111pump")] = 50 + SNAPSHOT_GRACE_MS + 5
            t_start, snap, mark = ensure_startup(out, idx)
            self.assertEqual(t_start, 50)
            self.assertFalse(mark)
            self.assertIn(("dex_boost", "Old111111111111111111111111111111111pump"), snap)
            self.assertNotIn(("pump_live", "New111111111111111111111111111111111pump"), snap)
            # second call keeps the marker files
            t2, snap2, mark2 = ensure_startup(out, idx)
            self.assertEqual(t2, t_start)
            self.assertEqual(snap2, snap)
            self.assertFalse(mark2)

    def test_genuine_requires_after_start_and_not_snapshot(self) -> None:
        snap = {("pump_live", "A")}
        backlog = {"kind": "pump_live", "mint": "A", "t_first_ms": 200}
        flagged = {"kind": "pump_live", "mint": "B", "t_first_ms": 200, "snapshot": True}
        late = {"kind": "pump_live", "mint": "C", "t_first_ms": 50}
        ok = {"kind": "pump_live", "mint": "D", "t_first_ms": 200}
        self.assertFalse(is_genuine_arrival(backlog, t_start_ms=100, snapshot_keys=snap))
        self.assertFalse(is_genuine_arrival(flagged, t_start_ms=100, snapshot_keys=snap))
        self.assertFalse(is_genuine_arrival(late, t_start_ms=100, snapshot_keys=snap))
        self.assertTrue(is_genuine_arrival(ok, t_start_ms=100, snapshot_keys=snap))


class EventTimeTests(unittest.TestCase):
    def test_dex_uses_payment_timestamp(self) -> None:
        row = {"kind": "dex_paid_profile", "paid_at_ms": 1_758_800_000_010, "t_first_ms": 1_758_800_000_050}
        self.assertEqual(event_time_ms(row), 1_758_800_000_010)

    def test_live_prefers_stream_clock_not_coin_create(self) -> None:
        row = {
            "kind": "pump_live",
            "created_timestamp": 1_758_800_000_001,
            "playlist_updated_at": 1_758_800_000_040,
            "t_first_ms": 1_758_800_000_080,
        }
        self.assertEqual(event_time_ms(row), 1_758_800_000_040)

    def test_laya_join_clock_is_first_seen_not_event(self) -> None:
        row = {
            "kind": "dex_paid_profile",
            "mint": "Mint1111111111111111111111111111111111111pump",
            "source": "dex_orders",
            "t_first_ms": 1_758_800_000_080,
            "paid_at_ms": 1_758_800_000_040,
            "snapshot": False,
        }
        rec = laya_join_record(row, genuine=True)
        self.assertIsNotNone(rec)
        assert rec is not None
        self.assertEqual(rec["t_ms"], 1_758_800_000_080)
        self.assertNotEqual(rec["t_ms"], rec["event_t_ms"])
        self.assertNotEqual(rec["t_ms"], rec["paid_at_ms"])
        self.assertEqual(rec["event_t_ms"], 1_758_800_000_040)
        self.assertEqual(rec["paid_at_ms"], 1_758_800_000_040)
        self.assertEqual(rec["lag_ms"], 40)
        self.assertTrue(rec["genuine"])
        self.assertGreater(rec["t_ms"], rec["paid_at_ms"])


class JoinAndHourlyTests(unittest.TestCase):
    def test_join_sorted_and_strictly_first_seen(self) -> None:
        rows = [
            {
                "kind": "pump_live",
                "mint": "Bmint111111111111111111111111111111111pump",
                "t_first_ms": 300,
                "source": "pump_currently_live",
            },
            {
                "kind": "dex_boost",
                "mint": "Amint111111111111111111111111111111111pump",
                "t_first_ms": 100,
                "source": "dex_boosts_latest",
                "snapshot": True,
            },
        ]
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "laya_join.jsonl"
            n = emit_laya_join(rows, t_start_ms=50, snapshot_keys=set(), dest=dest)
            self.assertEqual(n, 2)
            out = [json.loads(line) for line in dest.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(out[0]["t_ms"], 100)
            self.assertEqual(out[1]["t_ms"], 300)
            self.assertFalse(out[0]["genuine"])
            self.assertTrue(out[0]["snapshot"])
            self.assertTrue(out[1]["genuine"])
            self.assertFalse(out[1]["snapshot"])
            self.assertTrue(out[0]["t_ms"] <= 100)

    def test_hourly_tapes_skip_daily_zst(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "trades-2026-09-25T15.jsonl").write_text("{}\n", encoding="utf-8")
            (d / "trades-2026-09-25.jsonl.zst").write_text("nope", encoding="utf-8")
            files = list_hourly_tapes(d)
            self.assertEqual([p.name for p in files], ["trades-2026-09-25T15.jsonl"])

    def test_genuine_rows_drops_snapshot_set(self) -> None:
        rows = [
            {"kind": "pump_live", "mint": "A", "t_first_ms": 10},
            {"kind": "pump_live", "mint": "B", "t_first_ms": 80},
        ]
        kept = genuine_rows(rows, t_start_ms=5, snapshot_keys={("pump_live", "A")})
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["mint"], "B")

    def test_lag_vs_event_uses_payment_clock(self) -> None:
        book = {
            "M": {
                "kind": "dex_paid_profile",
                "mint": "M",
                "t_first_ms": 1_758_800_000_080,
                "paid_at_ms": 1_758_800_000_050,
            }
        }
        lag = lag_vs_event(book)
        self.assertEqual(lag["n_with_event"], 1)
        self.assertEqual(lag["lag_vs_event_ms"]["median"], 30)


class PromoteTests(unittest.TestCase):
    def test_below_min_n_does_not_promote(self) -> None:
        trades = [BookTrade(mint=f"m{i}", t_ms=1_000, pnl=50_000) for i in range(10)]
        stats = book_stats(trades)
        self.assertGreater(stats["mean_ci90_sol"][0], 0)
        self.assertGreater(stats["total_ex_best_sol"], 0)
        self.assertIn("min_n", stats["promote_blockers"])
        self.assertFalse(stats["promote"])
        self.assertFalse(stats["watch"])
        self.assertEqual(stats["min_n"], MIN_N)
        self.assertEqual(stats["watch_n"], WATCH_N)

    def test_n30_is_watch_not_promote(self) -> None:
        trades = [BookTrade(mint=f"m{i}", t_ms=1_758_758_400_000, pnl=100_000) for i in range(WATCH_N)]
        stats = book_stats(trades)
        self.assertEqual(stats["n"], 30)
        self.assertTrue(stats["watch"])
        self.assertIn("min_n", stats["promote_blockers"])
        self.assertFalse(stats["promote"])

    def test_drop_best_blocks_promote(self) -> None:
        trades = [BookTrade(mint=f"m{i}", t_ms=1_000 + i, pnl=-1_000) for i in range(29)]
        trades.append(BookTrade(mint="best", t_ms=2_000, pnl=1_000_000))
        stats = book_stats(trades)
        self.assertIn("drop_top3", stats["promote_blockers"])
        self.assertFalse(stats["promote"])

    def test_min_n_positive_book_on_one_day_does_not_promote(self) -> None:
        trades = [BookTrade(mint=f"m{i}", t_ms=1_758_758_400_000, pnl=100_000) for i in range(MIN_N)]
        stats = book_stats(trades)
        self.assertEqual(stats["n"], 100)
        self.assertTrue(stats["watch"])
        self.assertGreater(stats["mean_ci90_sol"][0], 0)
        self.assertGreater(stats["total_ex_top3_sol"], 0)
        self.assertTrue(stats["majority_days_positive"])
        self.assertIn("min_days", stats["promote_blockers"])
        self.assertFalse(stats["promote"])

    def test_five_positive_days_can_promote(self) -> None:
        base = 1_758_758_400_000
        trades = []
        for day in range(5):
            for i in range(20):
                trades.append(BookTrade(mint=f"d{day}m{i}", t_ms=base + day * 86_400_000 + i, pnl=100_000))
        stats = book_stats(trades)
        self.assertEqual(stats["n_days"], 5)
        self.assertEqual(stats["promote_blockers"], [])
        self.assertTrue(stats["promote"])

    def test_bootstrap_is_deterministic(self) -> None:
        trades = [BookTrade(mint=f"m{i}", t_ms=1, pnl=1000 + i) for i in range(30)]
        a = book_stats(trades)
        b = book_stats(trades)
        self.assertEqual(a["mean_ci90_sol"], b["mean_ci90_sol"])


if __name__ == "__main__":
    unittest.main()
