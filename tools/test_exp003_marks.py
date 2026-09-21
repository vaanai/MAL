"""Unit tests for EXP-003 coverage CLI (offline fixtures, no network)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from observe.regime import STREAM_NEW_TOKEN, seal_ingest_record
from tools.exp003_marks import MIN_OK_FOR_READY, build_coverage

T0 = "2026-09-20T12:00:00.000+00:00"
T1 = "2026-09-20T12:00:01.000+00:00"
T60 = "2026-09-20T12:01:00.000+00:00"


def _create_row(*, mint: str, t_ws: str = T0, cap: float = 100.0) -> dict:
    payload = {
        "txType": "create",
        "signature": f"Sig-{mint}",
        "mint": mint,
        "marketCapSol": cap,
        "bondingCurveKey": f"Curve-{mint}",
    }
    row = seal_ingest_record(t_ws=t_ws, stream=STREAM_NEW_TOKEN, payload=payload)
    row["marketCapSol"] = cap
    return row


def _mark(*, mint: str, t_mark: str, price: float) -> dict:
    return {
        "schema_version": "observe_mark_v0",
        "type": "outcome_mark",
        "mint": mint,
        "parent_signature": f"Sig-{mint}",
        "t_mark": t_mark,
        "source": "rpc_tx",
        "price_proxy": price,
        "t_decision": T0,
    }


class CoverageTests(unittest.TestCase):
    def test_no_marks_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            observe = Path(tmp) / "observe.jsonl"
            with observe.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(_create_row(mint="MintA")) + "\n")
            summary = build_coverage(observe_paths=[observe], marks_paths=[])
            self.assertEqual(summary["overall"], "INCOMPLETE")
            self.assertEqual(summary["creates_n"], 1)
            self.assertEqual(summary["horizon_ok_n"]["60s"], 0)

    def test_future_tick_does_not_fill_1s(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            observe = Path(tmp) / "observe.jsonl"
            marks = Path(tmp) / "marks.jsonl"
            with observe.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(_create_row(mint="MintA")) + "\n")
            with marks.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(_mark(mint="MintA", t_mark=T60, price=150.0)) + "\n")
            summary = build_coverage(observe_paths=[observe], marks_paths=[marks])
            self.assertEqual(summary["horizon_ok_n"]["1s"], 0)
            self.assertEqual(summary["horizon_ok_n"]["60s"], 1)

    def test_ready_when_enough_60s(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            observe = Path(tmp) / "observe.jsonl"
            marks = Path(tmp) / "marks.jsonl"
            with observe.open("w", encoding="utf-8") as oh, marks.open(
                "w", encoding="utf-8"
            ) as mh:
                for i in range(MIN_OK_FOR_READY):
                    mint = f"Mint{i}"
                    oh.write(json.dumps(_create_row(mint=mint)) + "\n")
                    mh.write(json.dumps(_mark(mint=mint, t_mark=T60, price=110.0)) + "\n")
            summary = build_coverage(observe_paths=[observe], marks_paths=[marks])
            self.assertEqual(summary["overall"], "READY")
            self.assertEqual(summary["horizon_ok_n"]["60s"], MIN_OK_FOR_READY)


if __name__ == "__main__":
    unittest.main()
