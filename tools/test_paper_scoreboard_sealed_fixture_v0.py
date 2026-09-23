"""Offline tests for paper-scoreboard-sealed-fixture-v0. No network."""

from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.paper_evaluate_hot_packet_v0 import (
    GRAPH_LIFT_COLD,
    GRAPH_LIFT_SLOTS,
    SOFT_WATCH_ITEMS as EVALUATE_SOFT_WATCHES,
    example_reject_missing_mint,
    example_sealed_cold_runner,
    validate_stamp,
)
from tools.paper_scoreboard_sealed_fixture_v0 import (
    EXAMPLES,
    GRAPH_AGGREGATE_MIXED,
    HORIZON_JOIN_STATUS,
    SOFT_WATCH_ITEMS,
    example_all_runner,
    example_graph_cold,
    example_identity_reject,
    example_mixed,
    example_slots_not_scored,
    main,
    score_stamps,
    sealed_day_expectation,
    validate_expectation,
    validate_scoreboard,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "paper_scoreboard_sealed_fixture_v0"
STAMP_FIXTURES = ROOT / "fixtures" / "paper_evaluate_hot_packet_v0"
SCHEMA = ROOT / "ARTIFACTS" / "paper-scoreboard-sealed-fixture-v0.schema.json"
EXPECTATION_NAME = "sealed_day_2026-09-20_expectation.json"

FIXTURE_NAMES = {
    "all-runner": "all_runner.json",
    "mixed": "mixed_runner_reject.json",
    "identity-reject": "identity_reject.json",
    "graph-cold": "graph_cold.json",
    "slots-not-scored": "slots_not_scored.json",
}

STAMP_FILES = [
    "enriched_global_95bps_runner.json",
    "enriched_launchlab_init_runner.json",
    "reject_missing_identity.json",
    "sealed_cold_graph_runner.json",
    "sealed_graph_slots_not_scored_runner.json",
]


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()):
        return main(argv)


class PaperScoreboardSealedFixtureV0Tests(unittest.TestCase):
    def test_examples_validate(self) -> None:
        for name, builder in EXAMPLES.items():
            with self.subTest(name=name):
                self.assertEqual(validate_scoreboard(builder()), [])

    def test_fixtures_match_examples_and_validate(self) -> None:
        expectation = json.loads((FIXTURES / EXPECTATION_NAME).read_text(encoding="utf-8"))
        self.assertEqual(expectation, sealed_day_expectation())
        self.assertEqual(validate_expectation(expectation), [])
        for which, filename in FIXTURE_NAMES.items():
            with self.subTest(filename=filename):
                payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
                self.assertEqual(payload, EXAMPLES[which]())
                self.assertEqual(validate_scoreboard(payload), [])
                self.assertEqual(_quiet(["validate", str(FIXTURES / filename)]), 0)

    def test_expectation_file_is_not_a_scoreboard(self) -> None:
        self.assertEqual(_quiet(["validate", str(FIXTURES / EXPECTATION_NAME)]), 1)

    def test_schema_file_is_json(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["title"],
            "MAL paper-scoreboard-sealed-fixture v0 (Proposed paper scoreboard)",
        )
        self.assertEqual(
            schema["$defs"]["input"]["properties"]["stamps"]["items"]["$ref"],
            "paper-evaluate-hot-packet-v0.schema.json",
        )
        self.assertEqual(schema["$defs"]["dualRead"]["properties"]["sealed_book_rpc_slice"]["const"], "incomplete")
        self.assertEqual(schema["$defs"]["fullBook"]["properties"]["reject_stamps_dropped"]["const"], 0)
        self.assertIn("fixture_joined_null_explicit", json.dumps(schema["$defs"]["horizons"]))
        self.assertIn("null_is_not_zero_return", json.dumps(schema["$defs"]["horizons"]))
        self.assertEqual(schema["$defs"]["measure"]["properties"]["pass_fail_no_lift"]["const"], False)
        self.assertEqual(schema["$defs"]["measure"]["properties"]["kind"]["const"], "none")
        items = schema["$defs"]["softWatches"]["properties"]["items"]["items"]["enum"]
        for item in SOFT_WATCH_ITEMS:
            self.assertIn(item, items)
        for item in EVALUATE_SOFT_WATCHES:
            self.assertIn(item, items)
        self.assertEqual(schema["$defs"]["softWatches"]["properties"]["blocking"]["const"], False)

    def test_example_cli(self) -> None:
        self.assertEqual(_quiet(["example", "--which", "mixed"]), 0)

    def test_usage_does_not_use_a_measure_exit(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["score"])
        self.assertEqual(ctx.exception.code, 1)

    def test_score_cli_on_checked_in_stamps(self) -> None:
        paths = [str(STAMP_FIXTURES / name) for name in STAMP_FILES]
        code = _quiet(
            ["score", "--expectation", str(FIXTURES / EXPECTATION_NAME), *paths]
        )
        self.assertEqual(code, 0)
        stamps = [json.loads((STAMP_FIXTURES / name).read_text(encoding="utf-8")) for name in STAMP_FILES]
        board, errors = score_stamps(stamps, sealed_day_expectation())
        self.assertEqual(errors, [])
        self.assertEqual(board, example_mixed())

    def test_score_cli_rejects_a_scoreboard_file(self) -> None:
        code = _quiet(
            [
                "score",
                "--expectation",
                str(FIXTURES / EXPECTATION_NAME),
                str(FIXTURES / "mixed_runner_reject.json"),
            ]
        )
        self.assertEqual(code, 1)

    def test_score_cli_rejects_a_directory_of_scoreboards(self) -> None:
        self.assertEqual(
            _quiet(
                [
                    "score",
                    "--expectation",
                    str(FIXTURES / EXPECTATION_NAME),
                    "--dir",
                    str(FIXTURES),
                ]
            ),
            1,
        )

    def test_checked_in_stamp_names(self) -> None:
        names = sorted(path.name for path in STAMP_FIXTURES.glob("*.json"))
        self.assertEqual(names, sorted(STAMP_FILES))

    def test_all_runner_keeps_a_zero_reject_row(self) -> None:
        board = example_all_runner()
        self.assertEqual(board["full_book"]["n"], 4)
        self.assertEqual(board["full_book"]["runner_n"], 4)
        self.assertEqual(board["full_book"]["reject_n"], 0)
        self.assertEqual(board["full_book"]["reject_stamps_dropped"], 0)
        self.assertEqual(board["label_rates"]["rows"][1]["label"], "reject")
        self.assertEqual(board["label_rates"]["rows"][1]["n"], 0)
        self.assertEqual(board["label_rates"]["rows"][1]["share"], {"numerator": 0, "denominator": 4})
        self.assertEqual(board["fixture_join"]["expectation_rows_not_in_local_set"], 1)
        self.assertEqual(board["fixture_join"]["matched_n"], 4)
        self.assertEqual(board["graph_lift_aggregate_status"], GRAPH_AGGREGATE_MIXED)
        self.assertIsNone(board["graph_lift"])
        self.assertTrue(board["full_book"]["local_set_is_not_the_sealed_book"])

    def test_mixed_keeps_the_identity_reject(self) -> None:
        board = example_mixed()
        self.assertEqual(board["full_book"]["n"], 5)
        self.assertEqual(board["full_book"]["runner_n"], 4)
        self.assertEqual(board["full_book"]["reject_n"], 1)
        self.assertEqual(board["label_rates"]["rows"][0]["share"], {"numerator": 4, "denominator": 5})
        self.assertEqual(board["label_rates"]["rows"][1]["share"], {"numerator": 1, "denominator": 5})
        histogram = {row["reason"]: row["n"] for row in board["reason_histogram"]["rows"]}
        self.assertEqual(histogram["missing_mint"], 1)
        self.assertEqual(histogram["missing_signature"], 0)
        self.assertEqual(board["fixture_join"]["expectation_rows_not_in_local_set"], 0)
        self.assertEqual(board["fixture_join"]["label_disagree_n"], 0)
        labels = [row["evaluate_label"] for row in board["stamp_rows"]]
        self.assertEqual(labels.count("reject"), 1)
        self.assertEqual(board["label_rates"]["share_kind"], "count_fraction_not_a_return")

    def test_identity_reject_arm_retained(self) -> None:
        board = example_identity_reject()
        self.assertEqual(board["full_book"]["runner_n"], 0)
        self.assertEqual(board["full_book"]["reject_n"], 1)
        self.assertEqual(board["stamp_rows"][0]["reasons"], ["missing_mint"])
        self.assertEqual(board["stamp_rows"][0]["mint"], "UNK")
        self.assertEqual(board["graph_lift_aggregate_status"], GRAPH_LIFT_COLD)
        self.assertEqual(board["fixture_join"]["matched_n"], 1)
        self.assertFalse(board["full_book"]["deletes_detect_history"])

    def test_graph_cold_and_slots_are_not_scores(self) -> None:
        cold = example_graph_cold()
        slots = example_slots_not_scored()
        self.assertEqual(cold["graph_lift_aggregate_status"], GRAPH_LIFT_COLD)
        self.assertEqual(cold["graph_status_counts"][0]["n"], 1)
        self.assertEqual(cold["graph_status_counts"][1]["n"], 0)
        self.assertIsNone(cold["graph_lift"])
        self.assertEqual(slots["graph_lift_aggregate_status"], GRAPH_LIFT_SLOTS)
        self.assertEqual(slots["graph_status_counts"][1]["status"], GRAPH_LIFT_SLOTS)
        self.assertEqual(slots["graph_status_counts"][1]["n"], 1)
        self.assertIsNone(slots["graph_lift"])
        self.assertEqual(slots["stamp_rows"][0]["signature"], "SigExampleGraphSlots")

    def test_horizons_and_delta_stay_null_with_status(self) -> None:
        board = example_mixed()
        for name, value in board["horizons"]["values"].items():
            self.assertIsNone(value, name)
        self.assertEqual(board["horizons"]["status"], HORIZON_JOIN_STATUS)
        self.assertTrue(board["horizons"]["null_is_not_zero_return"])
        self.assertIsNone(board["delta_exec"]["value"])
        self.assertEqual(board["delta_exec"]["status"], HORIZON_JOIN_STATUS)
        self.assertEqual(board["delta_exec"]["also_called"], "Δ_exec")
        self.assertTrue(board["delta_exec"]["null_is_not_zero_cost"])
        self.assertEqual(board["stamp_rows"][0]["delta_exec_status"], "null_ok")
        self.assertIsNone(board["stamp_rows"][0]["delta_exec_value"])

    def test_sealed_book_stays_incomplete(self) -> None:
        board = example_mixed()
        self.assertEqual(board["dual_read"]["sealed_book_rpc_slice"], "incomplete")
        self.assertFalse(board["dual_read"]["closed_book_claim"])
        self.assertFalse(board["fixture_join"]["closed_book"])
        self.assertEqual(board["sealed_days"][0]["day"], "2026-09-20")
        self.assertEqual(board["sealed_days"][0]["sealed_book_rpc_slice"], "incomplete")
        self.assertEqual(board["sealed_days"][0]["fixture_origin"], "synthetic")
        for stamp in board["input"]["stamps"]:
            self.assertEqual(stamp["input"]["packet"]["dual_read"]["sealed_book_rpc_slice"], "incomplete")

    def test_measure_flags_stay_false(self) -> None:
        board = example_all_runner()
        self.assertEqual(board["measure"]["kind"], "none")
        self.assertFalse(board["measure"]["claims_alpha"])
        self.assertFalse(board["measure"]["invented_ev"])
        self.assertFalse(board["measure"]["invented_lift"])
        self.assertFalse(board["measure"]["pass_fail_no_lift"])
        self.assertFalse(board["honesty"]["invented_ev"])
        self.assertFalse(board["honesty"]["claims_alpha"])
        self.assertFalse(board["honesty"]["exp002c_retuned"])
        self.assertFalse(board["honesty"]["enum_production_lock"])
        self.assertEqual(board["id"], "paper-scoreboard-sealed-fixture-v0")

    def test_disagreement_does_not_relabel_or_drop(self) -> None:
        expectation = sealed_day_expectation()
        for row in expectation["rows"]:
            if row["signature"] == "SigExampleSealed":
                row["evaluate_label"] = "reject"
                row["reasons"] = ["missing_mint"]
        board, errors = score_stamps([example_sealed_cold_runner()], expectation)
        self.assertEqual(errors, [])
        assert board is not None
        self.assertEqual(validate_scoreboard(board), [])
        self.assertEqual(board["full_book"]["runner_n"], 1)
        self.assertEqual(board["full_book"]["reject_n"], 0)
        self.assertEqual(board["full_book"]["reject_stamps_dropped"], 0)
        self.assertEqual(board["fixture_join"]["label_disagree_n"], 1)
        self.assertEqual(board["stamp_rows"][0]["evaluate_label"], "runner")
        self.assertEqual(board["stamp_rows"][0]["fixture_join"], "label_disagree")
        self.assertEqual(board["measure"]["kind"], "none")

    def test_reason_disagreement_keeps_the_stamp_reasons(self) -> None:
        expectation = sealed_day_expectation()
        for row in expectation["rows"]:
            if row["signature"] == "SigExampleSealed":
                row["reasons"] = ["missing_mint"]
        board, errors = score_stamps([example_sealed_cold_runner()], expectation)
        self.assertEqual(errors, [])
        assert board is not None
        self.assertEqual(board["fixture_join"]["reason_disagree_n"], 1)
        self.assertEqual(board["stamp_rows"][0]["reasons"], [])
        self.assertEqual(board["stamp_rows"][0]["fixture_join"], "reason_disagree")

    def test_unk_mint_is_not_a_wildcard(self) -> None:
        stamp = example_reject_missing_mint()
        stamp["input"]["packet"]["l1_spine"]["signature"] = "SigOtherUnk"
        stamp["input"]["packet"]["provenance"]["parent_signature"] = "SigOtherUnk"
        self.assertEqual(validate_stamp(stamp), [])
        board, errors = score_stamps([stamp], sealed_day_expectation())
        self.assertEqual(errors, [])
        assert board is not None
        self.assertEqual(board["fixture_join"]["unmatched_stamp_n"], 1)
        self.assertEqual(board["full_book"]["reject_n"], 1)
        self.assertEqual(board["stamp_rows"][0]["fixture_join"], "unmatched")

    def test_duplicate_reject_is_kept(self) -> None:
        reject = example_reject_missing_mint()
        board, errors = score_stamps([reject, copy.deepcopy(reject)], sealed_day_expectation())
        self.assertEqual(errors, [])
        assert board is not None
        self.assertEqual(board["full_book"]["n"], 2)
        self.assertEqual(board["full_book"]["reject_n"], 2)
        self.assertEqual(board["full_book"]["reject_stamps_dropped"], 0)
        self.assertEqual(board["fixture_join"]["duplicate_identity_n"], 1)

    def test_numeric_horizon_on_expectation_is_refused(self) -> None:
        expectation = sealed_day_expectation()
        expectation["horizon_join"]["values_supplied"] = True
        expectation["horizon_join"]["values"] = {"60s": 0}
        board, errors = score_stamps([example_sealed_cold_runner()], expectation)
        self.assertIsNone(board)
        self.assertTrue(errors)

    def test_zero_delta_on_expectation_is_refused(self) -> None:
        expectation = sealed_day_expectation()
        expectation["delta_exec_join"]["value"] = 0
        board, errors = score_stamps([example_sealed_cold_runner()], expectation)
        self.assertIsNone(board)
        self.assertTrue(any("delta_exec_join.value" in item for item in errors))

    def test_closed_book_expectation_is_refused(self) -> None:
        expectation = sealed_day_expectation()
        expectation["sealed_book_rpc_slice"] = "complete"
        expectation["closed_book_claim"] = True
        board, errors = score_stamps([example_sealed_cold_runner()], expectation)
        self.assertIsNone(board)
        self.assertTrue(errors)

    def test_host_extract_expectation_is_refused(self) -> None:
        expectation = sealed_day_expectation()
        expectation["origin"] = "sealed_host_extract"
        board, errors = score_stamps([example_sealed_cold_runner()], expectation)
        self.assertIsNone(board)
        self.assertTrue(errors)

    def test_empty_local_set_is_refused(self) -> None:
        board, errors = score_stamps([], sealed_day_expectation())
        self.assertIsNone(board)
        self.assertTrue(errors)

    def test_rate_tamper_rejected(self) -> None:
        board = example_mixed()
        board["full_book"]["reject_n"] = 0
        board["full_book"]["reject_stamps_dropped"] = 1
        self.assertTrue(validate_scoreboard(board))

    def test_horizon_number_rejected(self) -> None:
        board = example_graph_cold()
        board["horizons"]["values"]["60s"] = 0
        self.assertTrue(validate_scoreboard(board))

    def test_delta_exec_zero_rejected(self) -> None:
        board = example_graph_cold()
        board["delta_exec"]["value"] = 0
        self.assertTrue(validate_scoreboard(board))

    def test_graph_lift_number_rejected(self) -> None:
        board = example_slots_not_scored()
        board["graph_lift"] = 0
        self.assertTrue(validate_scoreboard(board))

    def test_honesty_and_measure_flags_cannot_flip(self) -> None:
        board = example_all_runner()
        board["honesty"]["invented_ev"] = True
        board["honesty"]["claims_alpha"] = True
        board["measure"]["pass_fail_no_lift"] = True
        board["dual_read"]["closed_book_claim"] = True
        board["dual_read"]["sealed_book_rpc_slice"] = "complete"
        self.assertTrue(validate_scoreboard(board))

    def test_soft_watch_cannot_block(self) -> None:
        board = example_all_runner()
        board["soft_watches"]["blocking"] = True
        self.assertTrue(validate_scoreboard(board))

    def test_input_order_does_not_change_the_board(self) -> None:
        stamps = [builder() for builder in (
            example_sealed_cold_runner,
            example_reject_missing_mint,
        )]
        forward, errors_f = score_stamps(stamps, sealed_day_expectation())
        backward, errors_b = score_stamps(list(reversed(stamps)), sealed_day_expectation())
        self.assertEqual(errors_f, [])
        self.assertEqual(errors_b, [])
        self.assertEqual(forward, backward)

    def test_module_stays_off_observe_and_measure_exits(self) -> None:
        source = (ROOT / "tools" / "paper_scoreboard_sealed_fixture_v0.py").read_text(encoding="utf-8")
        self.assertNotIn("observe.client", source)
        self.assertNotIn("observe/client", source)
        self.assertNotIn("from observe", source)
        self.assertNotIn("import observe", source)
        self.assertNotIn("FAIL_NO_LIFT", source)
        self.assertNotIn("requests", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("socket", source)


if __name__ == "__main__":
    unittest.main()
