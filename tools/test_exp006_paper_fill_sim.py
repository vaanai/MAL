"""Unit tests for EXP-006 paper fill-sim harness."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools.exp006_paper_fill_sim import (
    FILL_LATENCY_MS,
    _gate_fill_leak_audit,
    _last_tick_at_or_before,
    run_single_day,
    simulate_paper_fill,
)
from tools.exp002_paper_runner import PriceMark
from tools.test_exp004_graph_discovery import T0, T1, T2, _create_row, _outcome_mark


class FillSimLawTests(unittest.TestCase):
    def test_last_tick_at_or_before_respects_cutoff(self) -> None:
        t0 = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
        t1 = t0.replace(microsecond=500_000)
        marks = [
            PriceMark(t=t0, price=30.0, source_path="x", line_no=1),
            PriceMark(t=t1, price=31.0, source_path="x", line_no=2),
        ]
        cut = t0.replace(microsecond=250_000)
        tick = _last_tick_at_or_before(marks, cut)
        self.assertIsNotNone(tick)
        self.assertEqual(tick.price, 30.0)

    def test_simulate_fill_uses_mark_before_t_fill(self) -> None:
        t_dec = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
        t_mark = t_dec.replace(microsecond=200_000)
        row = _create_row(t_ws=t_dec, mint="M1", signature="Sig1")
        marks = [PriceMark(t=t_mark, price=32.0, source_path="m", line_no=1)]
        fill = simulate_paper_fill(row=row, marks=marks, t_decision=t_dec)
        self.assertEqual(fill.status, "filled")
        self.assertIsNotNone(fill.t_fill)
        assert fill.t_fill is not None
        self.assertEqual(fill.t_fill - t_dec, __import__("datetime").timedelta(milliseconds=FILL_LATENCY_MS))

    def test_fill_leak_gate_passes_when_mark_before_fill(self) -> None:
        rows = [
            {
                "fill": {
                    "status": "filled",
                    "t_fill": "2026-09-20T12:00:00.500+00:00",
                    "fill_mark_t": "2026-09-20T12:00:00.200+00:00",
                }
            }
        ]
        self.assertEqual(_gate_fill_leak_audit(rows), "PASS")


class Exp006IntegrationTests(unittest.TestCase):
    def test_run_single_day_emits_cohort_scores(self) -> None:
        r0 = _create_row(t_ws=T0, mint="M1", signature="Sig1")
        r1 = _create_row(t_ws=T1, mint="M2", signature="Sig2", trader="CreatorPk1")
        r2 = _create_row(t_ws=T2, mint="M3", signature="Sig3", trader="CreatorPk1")
        marks = [
            _outcome_mark(parent_signature="Sig1", mint="M1", t_mark=T1, price=33.0),
            _outcome_mark(parent_signature="Sig2", mint="M2", t_mark=T2, price=36.0),
            _outcome_mark(
                parent_signature="Sig3",
                mint="M3",
                t_mark="2026-09-20T12:01:05.000+00:00",
                price=39.0,
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "observe.jsonl"
            mk = Path(tmp) / "marks.jsonl"
            with obs.open("w", encoding="utf-8") as fh:
                for row in (r0, r1, r2):
                    fh.write(json.dumps(row) + "\n")
            with mk.open("w", encoding="utf-8") as fh:
                for row in marks:
                    fh.write(json.dumps(row) + "\n")
            summary = run_single_day(obs, [mk], seed=1, output_dir=Path(tmp), prefix="_exp006_test")
            self.assertEqual(summary["exp"], "EXP-006-paper-would-have-happened-harness-v0")
            scores = summary.get("cohort_scores") or {}
            self.assertIn("L3_minus_v2", scores)
            self.assertIn("60s", scores["L3_minus_v2"])


if __name__ == "__main__":
    unittest.main()
