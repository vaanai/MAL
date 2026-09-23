"""Unit tests for EXP-007 platform-regime coverage audit (fixtures only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from observe.regime import STREAM_NEW_TOKEN, seal_ingest_record
from tools.exp002_paper_runner import is_bonding_create
from tools.exp007_regime_audit import (
    audit_row,
    run_single_day,
    _gate_k_blank,
    _gate_k_kat,
    _gate_k_platform_resolved,
)
from tools.exp007_rpc_enrich import build_enrich_row, PlatformResolve

T_WS = "2026-09-20T12:00:00.000+00:00"


def _sealed_create(**payload_extra: object) -> dict:
    payload: dict = {
        "txType": "create",
        "signature": "SigCreate",
        "mint": "MintCreate",
    }
    payload.update(payload_extra)
    return seal_ingest_record(t_ws=T_WS, stream=STREAM_NEW_TOKEN, payload=payload)


class AuditRowTests(unittest.TestCase):
    def test_default_create_passes_structural_gates(self) -> None:
        row = _sealed_create()
        a = audit_row(row, source_path="f.jsonl", line_no=1)
        self.assertTrue(a.regime_parse_ok)
        self.assertTrue(a.kat_present)
        self.assertEqual(a.blank_platform_keys, [])
        self.assertEqual(a.id_kat_mismatch, [])

    def test_blank_instr_fails_k_blank(self) -> None:
        row = _sealed_create()
        row["regime_id"] = row["regime_id"].replace("instr=pending_rpc", "instr=")
        a = audit_row(row, source_path="f.jsonl", line_no=1)
        self.assertIn("regime_id.instr", a.blank_platform_keys)
        gates = [_gate_k_blank([a])]
        self.assertEqual(gates[0]["result"], "FAIL")

    def test_missing_kat_fails_k_kat(self) -> None:
        row = _sealed_create()
        del row["knowable_at_t"]
        a = audit_row(row, source_path="f.jsonl", line_no=1)
        self.assertFalse(a.kat_present)
        self.assertEqual(_gate_k_kat([a])["result"], "FAIL")

    def test_id_kat_mismatch_detected(self) -> None:
        row = _sealed_create()
        row["knowable_at_t"]["quote"] = "other_mint"
        a = audit_row(row, source_path="f.jsonl", line_no=1)
        self.assertIn("quote", a.id_kat_mismatch)


class RunSingleDayTests(unittest.TestCase):
    def test_bonding_create_population(self) -> None:
        row = _sealed_create()
        self.assertTrue(is_bonding_create(row))

    def test_run_on_fixture_file(self) -> None:
        row = _sealed_create()
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "observe-2026-09-20.jsonl"
            obs.write_text(json.dumps(row) + "\n", encoding="utf-8")
            out = Path(tmp) / "out"
            summary = run_single_day(obs, output_dir=out, prefix="_exp007-test")
            self.assertEqual(summary["eligible_n"], 1)
            self.assertEqual(summary["gates"]["K-blank"]["result"], "PASS")
            self.assertEqual(summary["gates"]["K-knowable-at-t"]["result"], "PASS")
            self.assertEqual(summary["gates"]["K-platform-rpc-resolved"]["result"], "INCOMPLETE")

    def test_enriched_sample_gate_can_pass(self) -> None:
        row = _sealed_create()
        resolved = PlatformResolve(
            instr="create",
            fee="unverified",
            quote="wsol",
            quote_verified=True,
            venue="pump_program",
            stage="bonding",
            market="bonding_curve",
            rpc_block_time=1_700_000_000,
            rpc_slot=1,
            quote_mint=None,
            leak_reject=False,
            leak_reason=None,
        )
        enrich = build_enrich_row(row, resolved, source_path="f", line_no=1)
        coverage = {
            "population_n": 1,
            "instr_pending_or_unknown_rate": 0.0,
            "fee_unverified_rate": 1.0,
            "quote_assumed_or_unk_rate": 0.0,
            "quote_verified_true_rate": 1.0,
        }
        gate = _gate_k_platform_resolved(coverage, scope="enriched_sample")
        self.assertEqual(gate["result"], "PASS")

    def test_run_with_enrich_overlay(self) -> None:
        row = _sealed_create()
        resolved = PlatformResolve(
            instr="create",
            fee="unverified",
            quote="wsol",
            quote_verified=True,
            venue="pump_program",
            stage="bonding",
            market="bonding_curve",
            rpc_block_time=1_700_000_000,
            rpc_slot=1,
            quote_mint=None,
            leak_reject=False,
            leak_reason=None,
        )
        enrich = build_enrich_row(row, resolved, source_path="f", line_no=1)
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "observe-2026-09-20.jsonl"
            obs.write_text(json.dumps(row) + "\n", encoding="utf-8")
            enrich_path = Path(tmp) / "enrich.jsonl"
            enrich_path.write_text(json.dumps(enrich) + "\n", encoding="utf-8")
            out = Path(tmp) / "out"
            summary = run_single_day(
                obs, output_dir=out, prefix="_exp007-test", enrich_paths=[enrich_path]
            )
            b = summary.get("exp007b") or {}
            self.assertEqual(b.get("overall"), "PASS")


if __name__ == "__main__":
    unittest.main()
