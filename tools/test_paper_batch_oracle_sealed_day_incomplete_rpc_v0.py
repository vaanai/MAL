"""Offline tests for paper-batch-oracle-sealed-day-incomplete-rpc-v0. No network."""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.paper_evaluate_hot_packet_v0 import SCHEMA_VERSION as STAMP_SCHEMA
from tools.paper_scoreboard_sealed_fixture_v0 import SCHEMA_VERSION as SCOREBOARD_SCHEMA
from tools.hot_packet_v0 import SCHEMA_VERSION as HOT_SCHEMA
from tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0 import (
    EXAMPLE_DAYS,
    SOFT_WATCH_ITEMS,
    assemble,
    example_two_day,
    main,
    project_day,
    read_jsonl,
    validate_batch,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "paper_batch_oracle_sealed_day_incomplete_rpc_v0"
SCHEMA = ROOT / "ARTIFACTS" / "paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json"
MODULE = ROOT / "tools" / "paper_batch_oracle_sealed_day_incomplete_rpc_v0.py"
BATCH_NAME = "two_day.json"


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()):
        return main(argv)


def _outside_source_rows(obj: object, key: str, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for child_key, value in obj.items():
            child = f"{path}.{child_key}" if path else str(child_key)
            if child_key == key and ".source_rows" not in child:
                found.append(child)
            found.extend(_outside_source_rows(value, key, child))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            found.extend(_outside_source_rows(value, key, f"{path}[{index}]"))
    return found


class PaperBatchOracleSealedDayIncompleteRpcV0Tests(unittest.TestCase):
    def test_example_validates_and_matches_fixture(self) -> None:
        batch = example_two_day()
        self.assertEqual(validate_batch(batch), [])
        on_disk = json.loads((FIXTURES / BATCH_NAME).read_text(encoding="utf-8"))
        self.assertEqual(on_disk, batch)
        self.assertEqual(_quiet(["validate", str(FIXTURES / BATCH_NAME)]), 0)

    def test_expectation_files_match_the_projection(self) -> None:
        batch = example_two_day()
        for day_input in batch["input"]["days"]:
            day = day_input["day"]
            on_disk = json.loads(
                (FIXTURES / f"sealed_day_{day}_expectation.json").read_text(encoding="utf-8")
            )
            self.assertEqual(on_disk, day_input["expectation"])
            self.assertEqual(on_disk["origin"], "synthetic")
            self.assertEqual(on_disk["sealed_book_rpc_slice"], "incomplete")
            self.assertIs(on_disk["closed_book_claim"], False)

    def test_schema_file_locks_the_honesty_caps(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["title"],
            "MAL paper-batch-oracle-sealed-day-incomplete-rpc v0 (Proposed paper batch)",
        )
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(
            schema["properties"]["days"]["items"]["$ref"],
            "#/$defs/dayResult",
        )
        self.assertEqual(
            schema["$defs"]["dayResult"]["properties"]["scoreboard"]["$ref"],
            "paper-scoreboard-sealed-fixture-v0.schema.json",
        )
        dual = schema["$defs"]["dualRead"]["properties"]
        self.assertEqual(dual["sealed_book_rpc_slice"]["const"], "incomplete")
        self.assertIs(dual["closed_book_claim"]["const"], False)
        measure = schema["$defs"]["measure"]["properties"]
        self.assertEqual(measure["kind"]["const"], "none")
        self.assertIs(measure["pass_fail_no_lift"]["const"], False)
        self.assertIs(measure["invented_ev"]["const"], False)
        self.assertEqual(schema["properties"]["graph_policy"]["const"], "cold")
        self.assertIs(schema["properties"]["graph_lift"]["const"], None)
        self.assertEqual(schema["$defs"]["fullBook"]["properties"]["reject_stamps_dropped"]["const"], 0)
        self.assertEqual(schema["$defs"]["fullBook"]["properties"]["policy"]["const"], "dec007_both_arms_retained")
        watches = schema["$defs"]["softWatches"]["properties"]
        self.assertIs(watches["blocking"]["const"], False)
        items = watches["items"]["items"]["enum"]
        for item in SOFT_WATCH_ITEMS:
            self.assertIn(item, items)
        self.assertIn("host_jsonl_not_readable_from_ci", items)
        self.assertIn("counts_are_not_returns", items)
        self.assertIn("sealed_book_rpc_slice_incomplete", items)
        self.assertIs(schema["$defs"]["honesty"]["properties"]["executed_host_sealed_book"]["const"], False)
        self.assertIs(schema["$defs"]["honesty"]["properties"]["pumpportal_trade_api"]["const"], False)

    def test_two_day_calendar_labels_keep_both_arms_and_an_open_book(self) -> None:
        batch = example_two_day()
        self.assertEqual([day["day"] for day in batch["days"]], list(EXAMPLE_DAYS))
        self.assertEqual(batch["rollup"]["days"], list(EXAMPLE_DAYS))
        self.assertEqual(batch["dual_read"]["sealed_book_rpc_slice"], "incomplete")
        self.assertIs(batch["dual_read"]["closed_book_claim"], False)
        self.assertEqual(batch["measure"]["kind"], "none")
        self.assertIs(batch["measure"]["pass_fail_no_lift"], False)
        self.assertIs(batch["measure"]["invented_ev"], False)
        self.assertIs(batch["measure"]["invented_lift"], False)
        self.assertIs(batch["measure"]["claims_alpha"], False)
        self.assertIs(batch["graph_lift"], None)
        self.assertEqual(batch["graph_policy"], "cold")
        self.assertIs(batch["full_book"]["reject_stamps_dropped"], 0)
        self.assertIs(batch["full_book"]["local_set_is_not_the_sealed_book"], True)
        self.assertEqual(batch["full_book"]["n"], 4)
        self.assertEqual(batch["full_book"]["runner_n"], 2)
        self.assertEqual(batch["full_book"]["reject_n"], 2)
        self.assertEqual(batch["rollup"]["share_kind"], "count_fraction_not_a_return")
        self.assertEqual(batch["rollup"]["rows"][1]["share"], {"numerator": 2, "denominator": 4})
        self.assertIs(batch["input"]["host_jsonl_read"], False)
        self.assertIs(batch["input"]["rpc"], False)
        self.assertIs(batch["honesty"]["executed_host_sealed_book"], False)
        self.assertIs(batch["honesty"]["scored_oracle_measure"], False)
        self.assertIs(batch["soft_watches"]["blocking"], False)
        self.assertEqual(batch["input"]["pipeline"][1:], [
            HOT_SCHEMA,
            STAMP_SCHEMA,
            SCOREBOARD_SCHEMA,
        ])

        reason_by_day = {}
        for day in batch["days"]:
            board = day["scoreboard"]
            self.assertEqual(board["schema_version"], SCOREBOARD_SCHEMA)
            self.assertEqual(board["id"], "paper-scoreboard-sealed-fixture-v0")
            self.assertEqual(board["dual_read"]["sealed_book_rpc_slice"], "incomplete")
            self.assertIs(board["dual_read"]["closed_book_claim"], False)
            self.assertIs(board["fixture_join"]["closed_book"], False)
            self.assertEqual(board["measure"]["kind"], "none")
            self.assertEqual(board["graph_lift_aggregate_status"], "not_used_graph_cold")
            self.assertIs(board["graph_lift"], None)
            self.assertEqual(board["full_book"]["runner_n"], 1)
            self.assertEqual(board["full_book"]["reject_n"], 1)
            self.assertEqual(board["full_book"]["reject_stamps_dropped"], 0)
            self.assertEqual(board["label_rates"]["rows"][0]["label"], "runner")
            self.assertEqual(board["label_rates"]["rows"][1]["label"], "reject")
            self.assertIs(board["horizons"]["null_is_not_zero_return"], True)
            self.assertTrue(all(value is None for value in board["horizons"]["values"].values()))
            self.assertIsNone(board["delta_exec"]["value"])
            for stamp in board["input"]["stamps"]:
                self.assertEqual(stamp["schema_version"], STAMP_SCHEMA)
                packet = stamp["input"]["packet"]
                self.assertEqual(packet["schema_version"], HOT_SCHEMA)
                self.assertEqual(packet["dual_read"]["sealed_book_rpc_slice"], "incomplete")
                self.assertEqual(packet["dual_read"]["overlay"], "sealed")
                self.assertIs(packet["graph"]["cold"], True)
                self.assertIsNone(packet["graph"]["slots"])
                self.assertNotIn("ws_payload", packet)
                self.assertEqual(packet["provenance"]["fixture_origin"], "synthetic")
                self.assertEqual(packet["provenance"]["source_day"], day["day"])
            hits = [row["reason"] for row in board["reason_histogram"]["rows"] if row["n"]]
            reason_by_day[day["day"]] = hits
        self.assertEqual(reason_by_day["2026-09-20"], ["missing_mint"])
        self.assertEqual(reason_by_day["2026-09-21"], ["missing_signature"])

    def test_skipped_mark_is_not_a_horizon(self) -> None:
        batch = example_two_day()
        day20 = batch["days"][0]
        self.assertEqual(day20["row_census"]["skipped_types"], ["outcome_mark"])
        self.assertEqual(day20["row_census"]["skipped_n"], 1)
        self.assertIs(day20["row_census"]["skip_is_not_a_dropped_evaluate_arm"], True)
        self.assertEqual(batch["days"][1]["row_census"]["skipped_types"], ["ingest_hot"])
        self.assertEqual(_outside_source_rows(batch, "price_proxy"), [])
        self.assertEqual(_outside_source_rows(batch, "ws_payload"), [])
        text = (FIXTURES / "observe-2026-09-20.jsonl").read_text(encoding="utf-8")
        self.assertIn("\n\n", text)
        self.assertEqual(day20["row_census"]["lines"], 3)

    def test_checked_in_batch_does_not_carry_proposed_enum_tags(self) -> None:
        text = json.dumps(example_two_day())
        self.assertNotIn("global_95bps", text)
        self.assertNotIn("launchlab_init", text)
        self.assertNotIn("FAIL_NO_LIFT", text)

    def test_example_and_batch_cli(self) -> None:
        example_buf = io.StringIO()
        with redirect_stdout(example_buf):
            self.assertEqual(main(["example", "--which", "two-day"]), 0)
        expected = example_two_day()
        self.assertEqual(json.loads(example_buf.getvalue()), expected)
        batch_buf = io.StringIO()
        with redirect_stdout(batch_buf):
            code = main(
                [
                    "batch",
                    "--jsonl",
                    str(FIXTURES / "observe-2026-09-21.jsonl"),
                    "--jsonl",
                    str(FIXTURES / "observe-2026-09-20.jsonl"),
                    "--expectation",
                    str(FIXTURES / "sealed_day_2026-09-21_expectation.json"),
                    "--expectation",
                    str(FIXTURES / "sealed_day_2026-09-20_expectation.json"),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(batch_buf.getvalue()), expected)

    def test_usage_does_not_use_a_measure_exit(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["batch"])
        self.assertEqual(ctx.exception.code, 1)

    def test_expectation_file_is_not_a_batch(self) -> None:
        path = FIXTURES / "sealed_day_2026-09-20_expectation.json"
        self.assertEqual(_quiet(["validate", str(path)]), 1)

    def test_label_disagree_stays_exit_zero(self) -> None:
        batch = example_two_day()
        day_inputs = copy.deepcopy(batch["input"]["days"])
        rows = day_inputs[0]["expectation"]["rows"]
        runner = next(row for row in rows if row["signature"] == "SigSynth20Runner")
        runner["evaluate_label"] = "reject"
        rebuilt, errors = assemble(day_inputs, fixture_origin="synthetic")
        self.assertEqual(errors, [])
        assert rebuilt is not None
        board = rebuilt["days"][0]["scoreboard"]
        self.assertGreaterEqual(board["fixture_join"]["label_disagree_n"], 1)
        self.assertEqual(rebuilt["measure"]["kind"], "none")
        self.assertIs(rebuilt["dual_read"]["closed_book_claim"], False)
        self.assertEqual(rebuilt["dual_read"]["sealed_book_rpc_slice"], "incomplete")
        self.assertEqual(rebuilt["full_book"]["reject_stamps_dropped"], 0)

    def test_projection_origin_cannot_close_the_book(self) -> None:
        batch = example_two_day()
        rebuilt, errors = assemble(batch["input"]["days"], fixture_origin="sealed_row_projection")
        self.assertEqual(errors, [])
        assert rebuilt is not None
        self.assertEqual(validate_batch(rebuilt), [])
        self.assertIs(rebuilt["input"]["host_jsonl_read"], True)
        self.assertIs(rebuilt["honesty"]["executed_host_sealed_book"], False)
        self.assertIs(rebuilt["honesty"]["scored_oracle_measure"], False)
        self.assertIs(rebuilt["dual_read"]["closed_book_claim"], False)
        self.assertEqual(rebuilt["dual_read"]["sealed_book_rpc_slice"], "incomplete")
        self.assertEqual(rebuilt["measure"]["kind"], "none")
        packet = rebuilt["days"][0]["scoreboard"]["input"]["stamps"][0]["input"]["packet"]
        self.assertEqual(packet["provenance"]["fixture_origin"], "sealed_row_projection")
        self.assertEqual(packet["dual_read"]["sealed_book_rpc_slice"], "incomplete")

    def test_day_misalignment_and_enriched_tag_are_refused(self) -> None:
        rows, errors = read_jsonl(FIXTURES / "observe-2026-09-20.jsonl")
        self.assertEqual(errors, [])
        shifted = copy.deepcopy(rows)
        shifted[0]["t_ws"] = "2026-09-22T12:00:00.000+00:00"
        _packets, _census, problems = project_day(shifted, day="2026-09-20", fixture_origin="synthetic")
        self.assertTrue(any("UTC calendar day" in item for item in problems))

        enriched = copy.deepcopy(rows)
        enriched[0]["regime_id"] = enriched[0]["regime_id"].replace("fee=unverified", "fee=global_95bps")
        _packets, _census, problems = project_day(enriched, day="2026-09-20", fixture_origin="synthetic")
        self.assertTrue(any("enriched overlay" in item for item in problems))

    def test_cli_refuses_a_misaligned_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = (FIXTURES / "observe-2026-09-20.jsonl").read_text(encoding="utf-8")
            bad = source.replace(
                "2026-09-20T12:00:00.000+00:00",
                "2026-09-22T12:00:00.000+00:00",
                1,
            )
            jsonl = root / "observe-2026-09-20.jsonl"
            jsonl.write_text(bad, encoding="utf-8")
            code = _quiet(
                [
                    "batch",
                    "--jsonl",
                    str(jsonl),
                    "--expectation",
                    str(FIXTURES / "sealed_day_2026-09-20_expectation.json"),
                ]
            )
            self.assertEqual(code, 1)

    def test_payload_numbers_copy_without_the_payload(self) -> None:
        row = json.loads((FIXTURES / "observe-2026-09-20.jsonl").read_text(encoding="utf-8").splitlines()[0])
        for key in ("initialBuy", "solAmount", "vTokensInBondingCurve", "vSolInBondingCurve", "marketCapSol"):
            row.pop(key, None)
        packets, census, errors = project_day([row], day="2026-09-20", fixture_origin="synthetic")
        self.assertEqual(errors, [])
        self.assertEqual(census["projected_n"], 1)
        spine = packets[0]["l1_spine"]
        self.assertEqual(spine["initialBuy"], 1.0)
        self.assertEqual(spine["solAmount"], 0.01)
        self.assertNotIn("ws_payload", packets[0])

    def test_closed_book_tamper_fails_validate(self) -> None:
        batch = example_two_day()
        tampered = copy.deepcopy(batch)
        tampered["dual_read"]["closed_book_claim"] = True
        tampered["dual_read"]["sealed_book_rpc_slice"] = "complete"
        tampered["measure"]["kind"] = "ev_lift"
        problems = validate_batch(tampered)
        self.assertTrue(problems)

    def test_module_does_not_import_the_observe_encoder(self) -> None:
        text = MODULE.read_text(encoding="utf-8")
        self.assertNotIn("observe.client", text)
        self.assertNotIn("import observe", text)
        self.assertNotIn("from observe", text)
        self.assertNotIn("import requests", text)


if __name__ == "__main__":
    unittest.main()
