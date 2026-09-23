"""Unit tests for EXP-004b NH-G1a ordinal runner."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.exp004b_nh_g1a import prior_mint_bucket, run_exp004b
from tools.test_exp004_graph_discovery import (
    T0,
    T1,
    T2,
    _create_row,
    _outcome_mark,
)


class PriorMintBucketTests(unittest.TestCase):
    def test_bucket_mapping(self) -> None:
        self.assertEqual(prior_mint_bucket(0), "bucket_0")
        self.assertEqual(prior_mint_bucket(1), "bucket_1")
        self.assertEqual(prior_mint_bucket(2), "bucket_2")
        self.assertEqual(prior_mint_bucket(3), "bucket_3plus")
        self.assertEqual(prior_mint_bucket(99), "bucket_3plus")


class Exp004bIntegrationTests(unittest.TestCase):
    def test_run_produces_ordinal_buckets(self) -> None:
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
            summary = run_exp004b(
                [obs],
                marks_paths=[mpath],
                output_dir=out_dir,
                prefix="_exp004b_test",
            )
            self.assertIn("ordinal_buckets", summary)
            self.assertEqual(summary["ordinal_arm_n_by_bucket"]["bucket_0"], 1)
            self.assertEqual(summary["ordinal_arm_n_by_bucket"]["bucket_1"], 1)
            self.assertEqual(summary["parent_h_g2_reference_60s"]["overall"], "INCOMPLETE")
            report = (out_dir / "_exp004b_test_report.md").read_text()
            self.assertIn("NH-G1a", report)


if __name__ == "__main__":
    unittest.main()
