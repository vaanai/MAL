"""Unit tests for EXP-001 local mislabel audit (offline fixtures, no network)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from observe.regime import STREAM_MIGRATION, STREAM_NEW_TOKEN, seal_ingest_record
from tools.exp001_mislabel import (
    DEC_REVIEW_THRESHOLD,
    WIRING_BLOCK_THRESHOLD,
    is_ingest_hot_create,
    judge_row,
    main,
    run_audit,
    t_ws_missing,
)

T_WS = "2026-09-20T12:00:00.000+00:00"


def _create_payload(**extra: object) -> dict:
    payload: dict = {
        "txType": "create",
        "signature": "SigCreate",
        "mint": "MintCreate",
    }
    payload.update(extra)
    return payload


def _sealed_create(**payload_extra: object) -> dict:
    return seal_ingest_record(
        t_ws=T_WS,
        stream=STREAM_NEW_TOKEN,
        payload=_create_payload(**payload_extra),
    )


class PopulationAndVoidTests(unittest.TestCase):
    def test_create_included_migration_excluded(self) -> None:
        create = _sealed_create()
        migration = seal_ingest_record(
            t_ws=T_WS,
            stream=STREAM_MIGRATION,
            payload={"txType": "migration", "signature": "S", "mint": "M"},
        )
        self.assertTrue(is_ingest_hot_create(create))
        self.assertFalse(is_ingest_hot_create(migration))
        self.assertFalse(is_ingest_hot_create({**create, "type": "regime_enrich"}))

    def test_create_on_other_stream_still_population(self) -> None:
        row = _sealed_create()
        row["stream"] = "subscribeSomethingElse"
        self.assertTrue(is_ingest_hot_create(row))

    def test_void_only_missing_t_ws(self) -> None:
        row = _sealed_create()
        self.assertFalse(t_ws_missing(row))
        self.assertTrue(t_ws_missing({**row, "t_ws": None}))
        self.assertTrue(t_ws_missing({**row, "t_ws": ""}))
        self.assertTrue(t_ws_missing({k: v for k, v in row.items() if k != "t_ws"}))
        self.assertFalse(t_ws_missing({**row, "t_event": None}))


class JudgmentTests(unittest.TestCase):
    def test_agree_default_create_null_t_event(self) -> None:
        row = _sealed_create()
        self.assertIsNone(row["t_event"])
        j = judge_row(row, source_path="fix.jsonl", line_no=1)
        self.assertEqual(j.verdict, "agree")
        self.assertEqual(j.expected_stage, "bonding")
        self.assertIn("stage=bonding", j.expected_regime_id or "")
        self.assertIsNone(j.dt_ws_minus_t_event_s)

    def test_null_t_event_never_disagree(self) -> None:
        row = _sealed_create()
        row["t_event"] = None
        j = judge_row(row, source_path="fix.jsonl", line_no=1)
        self.assertEqual(j.verdict, "agree")
        self.assertNotIn("t_event", j.notes)

    def test_missing_payload_inconclusive(self) -> None:
        row = _sealed_create()
        row["ws_payload"] = None
        j = judge_row(row, source_path="fix.jsonl", line_no=1)
        self.assertEqual(j.verdict, "inconclusive")
        self.assertEqual(j.notes, "missing_or_null_ws_payload")

    def test_stream_tx_conflict_inconclusive(self) -> None:
        row = _sealed_create()
        row["stream"] = STREAM_MIGRATION
        j = judge_row(row, source_path="fix.jsonl", line_no=1)
        self.assertEqual(j.verdict, "inconclusive")
        self.assertEqual(j.notes, "stream_txType_conflict")

    def test_wrong_stage_disagree(self) -> None:
        row = _sealed_create()
        row["stage"] = "migrating"
        row["regime_id"] = row["regime_id"].replace("stage=bonding", "stage=migrating")
        j = judge_row(row, source_path="fix.jsonl", line_no=1)
        self.assertEqual(j.verdict, "disagree")
        self.assertIn("stage_mismatch", j.failed_checks)

    def test_illegal_instr_backfill_disagree(self) -> None:
        row = _sealed_create()
        row["regime_id"] = row["regime_id"].replace("instr=pending_rpc", "instr=create_v2")
        j = judge_row(row, source_path="fix.jsonl", line_no=1)
        self.assertEqual(j.verdict, "disagree")
        self.assertIn("regime_id_instr_mismatch", j.failed_checks)

    def test_quote_wsol_without_ws_fact_disagree(self) -> None:
        row = _sealed_create()
        row["regime_id"] = row["regime_id"].replace("quote=wsol_assumed", "quote=wsol")
        j = judge_row(row, source_path="fix.jsonl", line_no=1)
        self.assertEqual(j.verdict, "disagree")
        self.assertIn("regime_id_quote_mismatch", j.failed_checks)

    def test_regime_id_stage_token_must_match_top_level(self) -> None:
        row = _sealed_create()
        row["regime_id"] = row["regime_id"].replace("stage=bonding", "stage=UNK")
        j = judge_row(row, source_path="fix.jsonl", line_no=1)
        self.assertEqual(j.verdict, "disagree")
        self.assertIn("regime_id_stage_ne_top_level", j.failed_checks)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _n_creates(n: int, *, mutate=None) -> list[dict]:
    rows = []
    for i in range(n):
        row = seal_ingest_record(
            t_ws=T_WS,
            stream=STREAM_NEW_TOKEN,
            payload={
                "txType": "create",
                "signature": f"S{i}",
                "mint": f"M{i}",
            },
        )
        if mutate:
            mutate(row, i)
        rows.append(row)
    return rows


class AuditRunTests(unittest.TestCase):
    def test_empty_population_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            migration = seal_ingest_record(
                t_ws=T_WS,
                stream=STREAM_MIGRATION,
                payload={"txType": "migration", "signature": "S", "mint": "M"},
            )
            jsonl = tmp_path / "mig.jsonl"
            _write_jsonl(jsonl, [migration])
            summary = run_audit([jsonl], n=100, seed=1, output_dir=tmp_path, prefix="_exp001")
            self.assertEqual(summary["n"], 0)
            self.assertIsNone(summary["hard_disagree_rate"])
            self.assertEqual(summary["overall"], "INCOMPLETE")
            self.assertEqual(summary["gates"]["dec_enum_review"]["result"], "INCOMPLETE")
            self.assertEqual(summary["gates"]["wiring_block"]["result"], "INCOMPLETE")

    def test_voided_excluded_from_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rows = _n_creates(5)
            rows[0]["t_ws"] = None
            jsonl = tmp_path / "obs.jsonl"
            _write_jsonl(jsonl, rows)
            summary = run_audit(
                [jsonl],
                n=100,
                seed=1,
                output_dir=tmp_path,
                prefix="_exp001",
            )
            self.assertEqual(summary["population_n"], 5)
            self.assertEqual(summary["void_n"], 1)
            self.assertEqual(summary["n"], 4)
            self.assertEqual(summary["agree_n"], 4)
            self.assertEqual(summary["overall"], "PASS")

    def test_seed_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rows = _n_creates(30)
            jsonl = tmp_path / "obs.jsonl"
            _write_jsonl(jsonl, rows)
            a = run_audit([jsonl], n=10, seed=1, output_dir=tmp_path / "a", prefix="_exp001")
            b = run_audit([jsonl], n=10, seed=1, output_dir=tmp_path / "b", prefix="_exp001")
            c = run_audit([jsonl], n=10, seed=2, output_dir=tmp_path / "c", prefix="_exp001")
            self.assertEqual(a["sample_signatures"], b["sample_signatures"])
            self.assertNotEqual(a["sample_signatures"], c["sample_signatures"])

    def test_gate_one_percent_is_pass_two_percent_is_dec_review(self) -> None:
        self.assertEqual(DEC_REVIEW_THRESHOLD, 0.01)
        self.assertEqual(WIRING_BLOCK_THRESHOLD, 0.05)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            def mutate_one(row: dict, i: int) -> None:
                if i == 0:
                    row["stage"] = "migrating"
                    row["regime_id"] = row["regime_id"].replace("stage=bonding", "stage=migrating")

            jsonl = tmp_path / "one.jsonl"
            _write_jsonl(jsonl, _n_creates(100, mutate=mutate_one))
            one = run_audit([jsonl], n=100, seed=1, output_dir=tmp_path / "one", prefix="_exp001")
            self.assertEqual(one["disagree_n"], 1)
            self.assertEqual(one["hard_disagree_rate"], 0.01)
            self.assertEqual(one["gates"]["dec_enum_review"]["result"], "PASS")
            self.assertEqual(one["overall"], "PASS")

            def mutate_two(row: dict, i: int) -> None:
                if i < 2:
                    row["stage"] = "migrating"
                    row["regime_id"] = row["regime_id"].replace("stage=bonding", "stage=migrating")

            jsonl2 = tmp_path / "two.jsonl"
            _write_jsonl(jsonl2, _n_creates(100, mutate=mutate_two))
            two = run_audit([jsonl2], n=100, seed=1, output_dir=tmp_path / "two", prefix="_exp001")
            self.assertEqual(two["disagree_n"], 2)
            self.assertGreater(two["hard_disagree_rate"], 0.01)
            self.assertEqual(two["gates"]["dec_enum_review"]["result"], "FAIL")
            self.assertEqual(two["gates"]["wiring_block"]["result"], "PASS")
            self.assertEqual(two["overall"], "FAIL_DEC_REVIEW")

    def test_gate_six_percent_wiring_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            def mutate(row: dict, i: int) -> None:
                if i < 6:
                    row["stage"] = "pumpswap"
                    row["regime_id"] = row["regime_id"].replace("stage=bonding", "stage=pumpswap")

            jsonl = tmp_path / "six.jsonl"
            _write_jsonl(jsonl, _n_creates(100, mutate=mutate))
            summary = run_audit([jsonl], n=100, seed=1, output_dir=tmp_path, prefix="_exp001")
            self.assertEqual(summary["disagree_n"], 6)
            self.assertEqual(summary["gates"]["wiring_block"]["result"], "FAIL")
            self.assertEqual(summary["overall"], "FAIL_WIRING_BLOCK")

    def test_inconclusive_excluded_from_rate_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            def mutate(row: dict, i: int) -> None:
                if i == 0:
                    row["ws_payload"] = None
                if i == 1:
                    row["stage"] = "migrating"
                    row["regime_id"] = row["regime_id"].replace("stage=bonding", "stage=migrating")

            jsonl = tmp_path / "mix.jsonl"
            _write_jsonl(jsonl, _n_creates(10, mutate=mutate))
            summary = run_audit([jsonl], n=10, seed=1, output_dir=tmp_path, prefix="_exp001")
            self.assertEqual(summary["inconclusive_n"], 1)
            self.assertEqual(summary["disagree_n"], 1)
            self.assertEqual(summary["agree_n"], 8)
            self.assertEqual(summary["hard_disagree_rate"], 1 / 9)

    def test_stratify_when_multiple_strata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rows = _n_creates(20)

            def other_stage(row: dict) -> dict:
                # Distinct stamped stratum but still a create (will disagree; allocation only).
                row = dict(row)
                row["stage"] = "UNK"
                row["regime_id"] = row["regime_id"].replace("stage=bonding", "stage=UNK")
                return row

            rows = rows[:16] + [other_stage(r) for r in rows[16:]]
            jsonl = tmp_path / "st.jsonl"
            _write_jsonl(jsonl, rows)
            summary = run_audit([jsonl], n=10, seed=1, output_dir=tmp_path, prefix="_exp001")
            self.assertEqual(summary["allocation_rule"], "proportional_stratified")
            self.assertGreaterEqual(len(summary["sample_stratum_counts"]), 2)

    def test_cli_writes_outputs_and_exits_zero_on_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            jsonl = tmp_path / "cli.jsonl"
            _write_jsonl(jsonl, _n_creates(8))
            out = tmp_path / "out"
            code = main(
                [
                    str(jsonl),
                    "--seed",
                    "1",
                    "--n",
                    "8",
                    "--output-dir",
                    str(out),
                    "--prefix",
                    "_exp001",
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue((out / "_exp001_summary.json").is_file())
            self.assertTrue((out / "_exp001_judgments.jsonl").is_file())
            self.assertTrue((out / "_exp001_judgments.csv").is_file())
            summary = json.loads((out / "_exp001_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall"], "PASS")
            self.assertEqual(summary["gates"]["dec_enum_review"]["result"], "PASS")
            self.assertEqual(summary["gates"]["wiring_block"]["result"], "PASS")


if __name__ == "__main__":
    unittest.main()
