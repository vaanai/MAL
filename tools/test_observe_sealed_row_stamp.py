"""Tests for the sealed-row stamp CLI (stdlib, no network)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from observe_sealed_row_stamp import (  # noqa: E402
    LEGAL_STAGES,
    REGIME_KEYS,
    main,
    parse_iso,
    parse_regime_id,
    stamp_paths,
)

FIXTURE = SCRIPT_DIR / "fixtures" / "observe_sealed_row_stamp_mini.jsonl"

PASS_REGIME_CREATE = (
    "env=mainnet|source=pumpportal_ws|stream=subscribeNewToken|"
    "stage=bonding|quote=wsol_assumed|commitment=processed|"
    "venue=pump_program|instr=pending_rpc|fee=unverified|market=bonding_curve"
)

KNOWABLE_HONEST = {
    "quote": "wsol_assumed",
    "quote_verified": False,
    "instr": "pending_rpc",
    "fee": "unverified",
    "venue": "pump_program",
    "venue_verified": False,
    "creator_verified": False,
    "reserves_source": "ws",
}


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def _base_create(**overrides: object) -> dict:
    row = {
        "schema_version": "observe_hot_v0",
        "type": "ingest_hot",
        "t_ws": "2026-09-21T12:00:00.000+00:00",
        "t_event": None,
        "stream": "subscribeNewToken",
        "source": "pumpportal_ws",
        "commitment": "processed",
        "stage": "bonding",
        "regime_id": PASS_REGIME_CREATE,
        "txType": "create",
        "signature": "S",
        "mint": "M",
        "knowable_at_t": dict(KNOWABLE_HONEST),
        "ws_payload": {"txType": "create", "signature": "S", "mint": "M"},
        "ws_fields_unknown": [],
    }
    row.update(overrides)
    return row


class ParseHelpersTests(unittest.TestCase):
    def test_parse_iso_z_and_offset(self) -> None:
        self.assertIsNotNone(parse_iso("2026-09-21T00:00:00.123+00:00"))
        self.assertIsNotNone(parse_iso("2026-09-21T00:00:02.000Z"))
        self.assertIsNone(parse_iso("not-a-date"))
        self.assertIsNone(parse_iso(None))

    def test_parse_regime_id_order(self) -> None:
        parsed, err = parse_regime_id(PASS_REGIME_CREATE)
        self.assertIsNone(err)
        assert parsed is not None
        self.assertEqual(list(parsed.keys()), list(REGIME_KEYS))
        self.assertEqual(parsed["stage"], "bonding")

    def test_parse_regime_id_rejects_spaces_and_wrong_order(self) -> None:
        _, err = parse_regime_id(PASS_REGIME_CREATE.replace("|", " | "))
        self.assertIsNotNone(err)
        shuffled = (
            "source=pumpportal_ws|env=mainnet|stream=subscribeNewToken|"
            "stage=bonding|quote=wsol_assumed|commitment=processed|"
            "venue=pump_program|instr=pending_rpc|fee=unverified|market=bonding_curve"
        )
        _, err = parse_regime_id(shuffled)
        self.assertIsNotNone(err)

    def test_legal_stages_include_unk(self) -> None:
        self.assertIn("UNK", LEGAL_STAGES)
        self.assertIn("migrating", LEGAL_STAGES)


class FixtureStampTests(unittest.TestCase):
    def test_mini_fixture_passes_with_soft_inventory(self) -> None:
        result = stamp_paths([FIXTURE])
        self.assertEqual(result["stamp"], "PASS", result.get("fail_reasons"))
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["row_count"], 3)
        self.assertEqual(result["hard_fail_rows"], 0)
        soft = result["soft_inventory"]
        self.assertGreaterEqual(soft["non_pump_pool_still_bonding"]["count"], 1)
        unk = soft["ws_fields_unknown_listed_present_in_payload"]
        self.assertGreater(unk["count"], 0)
        self.assertIn("newVendorField", unk["keys"])
        self.assertGreaterEqual(soft["is_mayhem_mode"]["rows_with_payload_flag"], 1)
        self.assertGreaterEqual(soft["t_event"]["null_or_empty"], 1)

    def test_cli_fixture_exit_zero_writes_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "stamp.json"
            with patch("sys.stdout", new=StringIO()):
                code = main([str(FIXTURE), "--artifact", str(artifact)])
            self.assertEqual(code, 0)
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(payload["stamp"], "PASS")
            self.assertEqual(payload["schema_version"], "observe_sealed_row_stamp_v0")


class HardGateFailTests(unittest.TestCase):
    def _stamp_one(self, row: dict) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "row.jsonl"
            _write_jsonl(path, [row])
            return stamp_paths([path])

    def test_missing_t_ws_fails_dual_clocks(self) -> None:
        row = _base_create()
        del row["t_ws"]
        result = self._stamp_one(row)
        self.assertEqual(result["stamp"], "FAIL")
        self.assertGreater(result["hard_gates"]["dual_clocks"]["fail_count"], 0)

    def test_null_t_event_does_not_fail(self) -> None:
        result = self._stamp_one(_base_create(t_event=None))
        self.assertEqual(result["stamp"], "PASS")

    def test_illegal_stage_fails_stage_map(self) -> None:
        result = self._stamp_one(_base_create(stage="migrate"))
        self.assertEqual(result["stamp"], "FAIL")
        self.assertGreater(result["hard_gates"]["stage_map"]["fail_count"], 0)

    def test_migration_tx_not_migrating_fails(self) -> None:
        row = _base_create(
            stream="subscribeMigration",
            stage="bonding",
            txType="migration",
            ws_payload={"txType": "migration", "signature": "S", "mint": "M"},
        )
        result = self._stamp_one(row)
        self.assertEqual(result["stamp"], "FAIL")
        self.assertGreater(result["hard_gates"]["stage_map"]["fail_count"], 0)

    def test_migrate_alias_with_stage_migrating_passes(self) -> None:
        regime = (
            "env=mainnet|source=pumpportal_ws|stream=subscribeMigration|"
            "stage=migrating|quote=wsol_assumed|commitment=processed|"
            "venue=pump_program|instr=pending_rpc|fee=unverified|market=pumpswap_pending"
        )
        knowable = dict(KNOWABLE_HONEST)
        knowable["reserves_source"] = "UNK"
        row = _base_create(
            stream="subscribeMigration",
            stage="migrating",
            txType="migrate",
            regime_id=regime,
            knowable_at_t=knowable,
            ws_payload={"txType": "migrate", "signature": "S", "mint": "M"},
        )
        result = self._stamp_one(row)
        self.assertEqual(result["stamp"], "PASS", result.get("fail_reasons"))

    def test_regime_stage_mismatch_fails(self) -> None:
        row = _base_create(stage="UNK")
        result = self._stamp_one(row)
        self.assertEqual(result["stamp"], "FAIL")
        self.assertGreater(result["hard_gates"]["regime_id"]["fail_count"], 0)

    def test_dishonest_quote_verified_fails(self) -> None:
        knowable = dict(KNOWABLE_HONEST)
        knowable["quote_verified"] = True
        result = self._stamp_one(_base_create(knowable_at_t=knowable))
        self.assertEqual(result["stamp"], "FAIL")
        self.assertGreater(result["hard_gates"]["knowable_at_t"]["fail_count"], 0)

    def test_knowable_quote_mismatch_vs_pipe_fails(self) -> None:
        knowable = dict(KNOWABLE_HONEST)
        knowable["quote"] = "wsol"
        result = self._stamp_one(_base_create(knowable_at_t=knowable))
        self.assertEqual(result["stamp"], "FAIL")
        self.assertGreater(result["hard_gates"]["knowable_at_t"]["fail_count"], 0)

    def test_missing_knowable_fails(self) -> None:
        row = _base_create()
        del row["knowable_at_t"]
        result = self._stamp_one(row)
        self.assertEqual(result["stamp"], "FAIL")
        self.assertGreater(result["hard_gates"]["knowable_at_t"]["fail_count"], 0)

    def test_bad_regime_does_not_double_fail_honest_knowable(self) -> None:
        result = self._stamp_one(_base_create(regime_id="not-a-pipe"))
        self.assertEqual(result["stamp"], "FAIL")
        self.assertGreater(result["hard_gates"]["regime_id"]["fail_count"], 0)
        self.assertEqual(result["hard_gates"]["knowable_at_t"]["fail_count"], 0)
        knowable = dict(KNOWABLE_HONEST)
        knowable["quote"] = "wsol"
        result = self._stamp_one(_base_create(knowable_at_t=knowable))
        self.assertEqual(result["stamp"], "FAIL")
        self.assertGreater(result["hard_gates"]["knowable_at_t"]["fail_count"], 0)

    def test_soft_bonk_pool_does_not_fail(self) -> None:
        row = _base_create(
            pool="letsbonk",
            ws_payload={
                "txType": "create",
                "signature": "S",
                "mint": "M",
                "pool": "letsbonk",
            },
            ws_fields_unknown=["pool"],
        )
        result = self._stamp_one(row)
        self.assertEqual(result["stamp"], "PASS")
        self.assertGreaterEqual(
            result["soft_inventory"]["non_pump_pool_still_bonding"]["count"], 1
        )

    def test_empty_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.jsonl"
            path.write_text("", encoding="utf-8")
            result = stamp_paths([path])
            self.assertEqual(result["stamp"], "FAIL")
            self.assertIn("no ingest_hot rows", result["fail_reasons"])

    def test_missing_file_cli_exit_2(self) -> None:
        with patch("sys.stderr", new=StringIO()):
            code = main(["/no/such/observe.jsonl"])
        self.assertEqual(code, 2)

    def test_invalid_json_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.jsonl"
            path.write_text("{not json\n", encoding="utf-8")
            result = stamp_paths([path])
            self.assertEqual(result["stamp"], "FAIL")
            self.assertGreater(result["parse_errors"], 0)


if __name__ == "__main__":
    unittest.main()
