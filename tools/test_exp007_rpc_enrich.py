"""Unit tests for EXP-007b RPC platform enrich (fixtures + mocks)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from observe.regime import STREAM_NEW_TOKEN, seal_ingest_record
from tools.exp007_rpc_enrich import (
    MAX_SAMPLE_PER_DAY,
    apply_enrich_overlay,
    build_enrich_row,
    cap_sample_n,
    knowable_at_t_ok,
    resolve_platform_at_t,
    run_single_day,
    sample_creates_for_day,
    PlatformResolve,
)
from tools.exp001_mislabel import LoadedRow
from tools.exp003_rpc_backfill import SolanaRpcClient

T_WS = "2026-09-20T12:00:00.000+00:00"


def _sealed_create(**payload_extra: object) -> dict:
    payload: dict = {
        "txType": "create",
        "signature": "SigCreate007b",
        "mint": "MintCreate007b",
        "bondingCurveKey": "CurveKey007b",
    }
    payload.update(payload_extra)
    return seal_ingest_record(t_ws=T_WS, stream=STREAM_NEW_TOKEN, payload=payload)


class CapSampleTests(unittest.TestCase):
    def test_hard_cap_500(self) -> None:
        self.assertEqual(cap_sample_n(1000, 10_000), MAX_SAMPLE_PER_DAY)
        self.assertEqual(cap_sample_n(100, 10_000), 100)
        self.assertEqual(cap_sample_n(0, 42), 42)

    def test_sample_respects_cap(self) -> None:
        creates = [
            LoadedRow(row=_sealed_create(signature=f"Sig{i}"), source_path="f", line_no=i)
            for i in range(800)
        ]
        sampled = sample_creates_for_day(creates, sample_n=900, seed=1)
        self.assertEqual(len(sampled), MAX_SAMPLE_PER_DAY)


class KnowableAtTTests(unittest.TestCase):
    def test_block_time_after_t_ws_rejected(self) -> None:
        t_ws = 1_700_000_000
        self.assertFalse(knowable_at_t_ok(t_ws, t_ws + 10_000, slack_s=120))

    def test_block_time_before_t_ws_ok(self) -> None:
        t_ws = 1_700_000_000
        self.assertTrue(knowable_at_t_ok(t_ws, t_ws - 5, slack_s=120))


class OverlayTests(unittest.TestCase):
    def test_apply_enrich_overlay_updates_regime_id(self) -> None:
        row = _sealed_create()
        resolved = PlatformResolve(
            instr="create_v2",
            fee="unverified",
            quote="wsol",
            quote_verified=True,
            venue="pump_program",
            stage="bonding",
            market="bonding_curve",
            rpc_block_time=1_700_000_000,
            rpc_slot=123,
            quote_mint=None,
            leak_reject=False,
            leak_reason=None,
        )
        enrich = build_enrich_row(row, resolved, source_path="f", line_no=1)
        merged = apply_enrich_overlay(row, enrich)
        self.assertIn("create_v2", merged["regime_id"])
        self.assertEqual(merged["knowable_at_t"]["instr"], "create_v2")
        self.assertTrue(merged["knowable_at_t"]["quote_verified"])


class ResolvePlatformTests(unittest.TestCase):
    def test_resolve_uses_create_logs(self) -> None:
        create = _sealed_create()
        tx = {
            "blockTime": 1_699_000_000,
            "slot": 999,
            "meta": {"logMessages": ["Program log: Instruction: CreateV2"]},
        }

        def fake_post(method: str, params: list[Any]) -> Any:
            if method == "getTransaction":
                return tx
            if method == "getAccountInfo":
                return {"value": None}
            return None

        client = SolanaRpcClient(url="http://mock", _post_fn=fake_post)
        with mock.patch("tools.exp007_rpc_enrich._t_ws_unix", return_value=1_699_000_100):
            resolved = resolve_platform_at_t(client, create)
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.instr, "create_v2")
        self.assertFalse(resolved.leak_reject)


class RunSingleDayDryRun(unittest.TestCase):
    def test_dry_run_writes_summary_only(self) -> None:
        row = _sealed_create()
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "observe-2026-09-20.jsonl"
            obs.write_text(json.dumps(row) + "\n", encoding="utf-8")
            out = Path(tmp) / "out"
            summary = run_single_day(obs, output_dir=out, dry_run=True, sample_n=10)
            self.assertEqual(summary["eligible_n"], 1)
            self.assertEqual(summary["enrich_written_n"], 0)
            self.assertTrue(Path(summary["summary_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
