"""Unit tests for EXP-005 L3 follow packet runner."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.exp005_smart_wallet_follow import l3_packet_selected, run_exp005
from tools.test_exp004_graph_discovery import (
    T0,
    T1,
    T2,
    _create_row,
    _outcome_mark,
)


class L3PacketRuleTests(unittest.TestCase):
    def test_select_prior_mint_or_recurrence_vetoes_burst(self) -> None:
        from tools.exp004_graph_discovery import GraphFeatures
        from datetime import datetime, timezone

        t = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        base = GraphFeatures(
            regime_id="r1",
            regime_gate_key="r1",
            trader_public_key="pk",
            t_decision=t,
            t_precompute_as_of=t,
            book_t0=t,
            creator_age_seconds=0.0,
            prior_mint_count=0,
            burst_count=0,
            min_gap_seconds=None,
            recurrence_weak=False,
            early_wallet_dt_min_seconds=None,
            cluster_tier="weak_creator_only",
        )
        self.assertFalse(l3_packet_selected(base))
        with_prior = GraphFeatures(**{**base.__dict__, "prior_mint_count": 1})
        self.assertTrue(l3_packet_selected(with_prior))
        with_burst = GraphFeatures(**{**with_prior.__dict__, "burst_count": 2})
        self.assertFalse(l3_packet_selected(with_burst))


class Exp005IntegrationTests(unittest.TestCase):
    def test_run_produces_l3_gates_and_overlap(self) -> None:
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
            mpath = Path(tmp) / "marks.jsonl"
            with obs.open("w") as fh:
                for row in (r0, r1, r2):
                    fh.write(json.dumps(row) + "\n")
            with mpath.open("w") as fh:
                for row in marks:
                    fh.write(json.dumps(row) + "\n")
            out_dir = Path(tmp) / "out"
            summary = run_exp005(
                [obs],
                marks_paths=[mpath],
                output_dir=out_dir,
                prefix="_exp005_test",
            )
            self.assertIn("l3_packet", summary)
            self.assertIn("exp002c_runner_overlap", summary)
            self.assertGreaterEqual(summary["l3_packet_arm_n"], 1)
            report = (out_dir / "_exp005_test_report.md").read_text()
            self.assertIn("L3 packet rule", report)


if __name__ == "__main__":
    unittest.main()
