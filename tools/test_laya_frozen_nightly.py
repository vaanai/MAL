"""Frozen nightly bounds. No live tape and no network."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.laya_backfill_holdout import continue_take
from tools.laya_frozen_nightly import (
    REVIEW_ON,
    TakeLedger,
    chunk_by_bytes,
    nightly_mode,
    prefer_sealed,
    sealed_live_hours,
)
from tools.laya_v0 import FEATURE_NAMES, DecisionRow, RankWindow


class ModeTests(unittest.TestCase):
    def test_review_date_splits_the_job(self) -> None:
        self.assertEqual(REVIEW_ON, "2026-10-05")
        self.assertEqual(nightly_mode("2026-09-27"), "frozen")
        self.assertEqual(nightly_mode("2026-10-04"), "frozen")
        self.assertEqual(nightly_mode("2026-10-05"), "exploratory")

    def test_shell_skips_mig15_deploy_while_frozen(self) -> None:
        text = Path("scripts/mal-core/laya-v0.sh").read_text(encoding="utf-8")
        self.assertIn("tools.laya_frozen_nightly", text)
        self.assertLess(text.index("skip mig15 deploy until 2026-10-05"), text.index('"${SWING_SH}"'))
        self.assertIn('elif [[ -x "${SWING_SH}" ]]; then', text)


class WindowTests(unittest.TestCase):
    def test_prefer_sealed_drops_the_raw_twin(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "trades-2026-09-25T15.jsonl"
            zst = root / "trades-2026-09-25T15.jsonl.zst"
            raw.write_text("raw", encoding="utf-8")
            zst.write_text("zst", encoding="utf-8")
            later = root / "trades-2026-09-25T16.jsonl.zst"
            later.write_text("later", encoding="utf-8")
            got = [path.name for path in prefer_sealed([raw, zst, later])]
        self.assertEqual(got, ["trades-2026-09-25T15.jsonl.zst", "trades-2026-09-25T16.jsonl.zst"])

    def test_chunk_by_bytes_starts_a_new_group(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            for name, size in (("a", 40), ("b", 40), ("c", 80)):
                path = root / name
                path.write_bytes(b"x" * size)
                paths.append(path)
            groups = chunk_by_bytes(paths, 100)
        self.assertEqual([[p.name for p in group] for group in groups], [["a", "b"], ["c"]])

    def test_sealed_hours_skip_open_raw_and_the_freeze(self) -> None:
        freeze = int(datetime(2026, 9, 25, 15, 30, tzinfo=timezone.utc).timestamp() * 1000)
        now = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "trades-2026-09-25T14.jsonl.zst").write_text("early", encoding="utf-8")
            (root / "trades-2026-09-25T15.jsonl.zst").write_text("hold", encoding="utf-8")
            (root / "trades-2026-09-27T07.jsonl.zst").write_text("sealed", encoding="utf-8")
            (root / "trades-2026-09-27T08.jsonl").write_text("open-raw", encoding="utf-8")
            (root / "trades-2026-09-27T08.jsonl.zst").write_text("open-zst", encoding="utf-8")
            (root / "trades-2026-09-27.jsonl.zst").write_text("daily", encoding="utf-8")
            got = [path.name for path in sealed_live_hours(root, after_ms=freeze, now=now)]
        self.assertEqual(got, ["trades-2026-09-25T15.jsonl.zst", "trades-2026-09-27T07.jsonl.zst"])


class TakeTests(unittest.TestCase):
    def test_rank_window_continues_across_chunks(self) -> None:
        class Stub:
            def predict(self, rows: list[list[float]]) -> list[float]:
                return [row[0] for row in rows]

        def row(mint: str, t_ms: int, score: float) -> DecisionRow:
            feats = {name: 0.0 for name in FEATURE_NAMES}
            feats["f_n_buy"] = score
            return DecisionRow(
                mint=mint,
                creator=None,
                create_t_ms=t_ms - 30_000,
                decision_t_ms=t_ms,
                trigger="grid",
                features=feats,
                pnl_by_rule={"hold_30s": 1},
            )

        window = RankWindow(0.5)
        model = Stub()
        first = [row("a", 1_000, 0.0)]
        taken = continue_take(first, "30", 0.5, "hold_30s", model, ["f_n_buy"], window)
        self.assertEqual(taken, [])
        second = [row("b", 2_000, 10.0)]
        taken = continue_take(second, "30", 0.5, "hold_30s", model, ["f_n_buy"], window)
        self.assertEqual([item.mint for item in taken], ["b"])

    def test_ledger_round_trip_appends_hours(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = TakeLedger(root / "state.json", root / "takes.jsonl")
            window = RankWindow(1.0)
            window.scores = [0.2, 0.4]
            ledger.add_chunk(
                ["2026-09-25T16"],
                [{"id": "migrate_hold_30s", "mint": "M", "t_ms": 1, "rule": "hold_30s", "pnl": 5, "p1": None, "p2": None}],
                {"migrate_hold_30s": window},
                {"migrate_hold_30s": 3},
                4,
            )
            again = TakeLedger(root / "state.json", root / "takes.jsonl")
            self.assertEqual(again.hours, ["2026-09-25T16"])
            self.assertEqual(again.pool_n["migrate_hold_30s"], 3)
            self.assertEqual(again.windows["migrate_hold_30s"], [0.2, 0.4])
            self.assertEqual(json.loads((root / "takes.jsonl").read_text(encoding="utf-8"))["mint"], "M")
            again.add_chunk(["2026-09-25T16", "2026-09-25T17"], [], {}, {}, 0)
            self.assertEqual(again.hours, ["2026-09-25T16", "2026-09-25T17"])


if __name__ == "__main__":
    unittest.main()
