"""Offline tests for paper-fill-sim-scoreboard-sealed-fixture-v0. No network."""

from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.paper_fill_sim_hot_packet_evaluate_v0 import (
    FILL_SIM_REJECT,
    FILL_SIM_RUNNER,
    SOFT_WATCH_ITEMS as FILL_SIM_SOFT_WATCHES,
    example_reject_identity_bind,
    example_sealed_cold_runner_bind,
)
from tools.paper_fill_sim_scoreboard_sealed_fixture_v0 import (
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
from tools.paper_evaluate_hot_packet_v0 import GRAPH_LIFT_COLD, GRAPH_LIFT_SLOTS

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "paper_fill_sim_scoreboard_sealed_fixture_v0"
STAMP_FIXTURES = ROOT / "fixtures" / "paper_fill_sim_hot_packet_evaluate_v0"
SCHEMA = ROOT / "ARTIFACTS" / "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json"
EXPECTATION_NAME = "sealed_day_2026-09-20_expectation.json"

FIXTURE_NAMES = {
    "all-runner": "all_runner.json",
    "mixed": "mixed_runner_reject.json",
    "identity-reject": "identity_reject.json",
    "graph-cold": "graph_cold.json",
    "slots-not-scored": "slots_not_scored.json",
}

STAMP_FILES = [
    "enriched_fee_runner.json",
    "enriched_launchlab_runner.json",
    "reject_missing_identity.json",
    "sealed_cold_runner.json",
    "graph_slots_not_scored.json",
]


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()):
        return main(argv)


class PaperFillSimScoreboardSealedFixtureV0Tests(unittest.TestCase):
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
            "MAL paper-fill-sim-scoreboard-sealed-fixture v0 (Proposed paper scoreboard)",
        )
        self.assertEqual(
            schema["$defs"]["input"]["properties"]["stamps"]["items"]["$ref"],
            "paper-fill-sim-hot-packet-evaluate-v0.schema.json",
        )
        self.assertEqual(schema["$defs"]["dualRead"]["properties"]["sealed_book_rpc_slice"]["const"], "incomplete")
        self.assertEqual(schema["$defs"]["fullBook"]["properties"]["reject_stamps_dropped"]["const"], 0)
        self.assertIn("fixture_joined_null_explicit", json.dumps(schema["$defs"]["horizons"]))
        self.assertEqual(schema["$defs"]["measure"]["properties"]["pass_fail_no_lift"]["const"], False)
        self.assertEqual(schema["$defs"]["measure"]["properties"]["kind"]["const"], "none")
        items = schema["$defs"]["softWatches"]["properties"]["items"]["items"]["enum"]
        for item in SOFT_WATCH_ITEMS:
            self.assertIn(item, items)
        for item in FILL_SIM_SOFT_WATCHES:
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
        stamps = [
            json.loads((STAMP_FIXTURES / name).read_text(encoding="utf-8")) for name in STAMP_FILES
        ]
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

    def test_score_cli_rejects_outside_fixtures(self) -> None:
        self.assertEqual(
            _quiet(
                [
                    "score",
                    "--expectation",
                    str(FIXTURES / EXPECTATION_NAME),
                    "/tmp/stamp.json",
                ]
            ),
            1,
        )

    def test_score_refuses_var_lib_mal(self) -> None:
        self.assertEqual(
            _quiet(
                [
                    "score",
                    "--expectation",
                    "/var/lib/mal/expectation.json",
                    str(STAMP_FIXTURES / "sealed_cold_runner.json"),
                ]
            ),
            1,
        )
        self.assertEqual(
            _quiet(
                [
                    "score",
                    "--expectation",
                    str(FIXTURES / EXPECTATION_NAME),
                    "/var/lib/mal/stamp.json",
                ]
            ),
            1,
        )

    def test_all_runner_keeps_a_zero_reject_row(self) -> None:
        board = example_all_runner()
        self.assertEqual(board["full_book"]["n"], 4)
        self.assertEqual(board["full_book"]["reject_n"], 0)
        self.assertEqual(board["label_rates"]["rows"][1]["n"], 0)
        self.assertEqual(board["fill_sim_status_counts"][1]["status"], FILL_SIM_REJECT)
        self.assertEqual(board["fill_sim_status_counts"][1]["n"], 0)
        self.assertEqual(board["fixture_join"]["expectation_rows_not_in_local_set"], 1)
        self.assertEqual(board["graph_lift_aggregate_status"], GRAPH_AGGREGATE_MIXED)

    def test_mixed_keeps_both_arms_and_fill_sim_status(self) -> None:
        board = example_mixed()
        self.assertEqual(board["full_book"]["runner_n"], 4)
        self.assertEqual(board["full_book"]["reject_n"], 1)
        fill_counts = {row["status"]: row["n"] for row in board["fill_sim_status_counts"]}
        self.assertEqual(fill_counts[FILL_SIM_RUNNER], 4)
        self.assertEqual(fill_counts[FILL_SIM_REJECT], 1)
        self.assertEqual(board["fixture_join"]["fill_sim_disagree_n"], 0)

    def test_identity_reject_arm_retained(self) -> None:
        board = example_identity_reject()
        self.assertEqual(board["full_book"]["reject_n"], 1)
        self.assertEqual(board["stamp_rows"][0]["fill_sim_status"], FILL_SIM_REJECT)
        self.assertEqual(board["graph_lift_aggregate_status"], GRAPH_LIFT_COLD)

    def test_horizons_and_delta_stay_null_with_status(self) -> None:
        board = example_mixed()
        for value in board["horizons"]["values"].values():
            self.assertIsNone(value)
        self.assertEqual(board["horizons"]["status"], HORIZON_JOIN_STATUS)
        self.assertIsNone(board["delta_exec"]["value"])

    def test_sealed_book_stays_incomplete(self) -> None:
        board = example_mixed()
        self.assertEqual(board["dual_read"]["sealed_book_rpc_slice"], "incomplete")
        self.assertFalse(board["dual_read"]["closed_book_claim"])

    def test_measure_flags_stay_false(self) -> None:
        board = example_all_runner()
        self.assertEqual(board["measure"]["kind"], "none")
        self.assertFalse(board["measure"]["pass_fail_no_lift"])
        self.assertFalse(board["honesty"]["exp006_promoted"])
        self.assertFalse(board["honesty"]["marks_joined_on_this_stamp"])

    def test_fill_sim_disagreement_does_not_relabel(self) -> None:
        expectation = sealed_day_expectation()
        for row in expectation["rows"]:
            if row["signature"] == "SigExampleSealed":
                row["fill_sim_status"] = FILL_SIM_REJECT
        board, errors = score_stamps([example_sealed_cold_runner_bind()], expectation)
        self.assertEqual(errors, [])
        assert board is not None
        self.assertEqual(board["fixture_join"]["fill_sim_disagree_n"], 1)
        self.assertEqual(board["stamp_rows"][0]["fill_sim_status"], FILL_SIM_RUNNER)

    def test_label_disagreement_keeps_stamp_label(self) -> None:
        expectation = sealed_day_expectation()
        for row in expectation["rows"]:
            if row["signature"] == "SigExampleSealed":
                row["evaluate_label"] = "reject"
        board, errors = score_stamps([example_sealed_cold_runner_bind()], expectation)
        assert board is not None
        self.assertEqual(board["fixture_join"]["label_disagree_n"], 1)
        self.assertEqual(board["stamp_rows"][0]["evaluate_label"], "runner")

    def test_numeric_horizon_on_expectation_is_refused(self) -> None:
        expectation = sealed_day_expectation()
        expectation["horizon_join"]["values_supplied"] = True
        expectation["horizon_join"]["values"] = {"60s": 0}
        board, errors = score_stamps([example_sealed_cold_runner_bind()], expectation)
        self.assertIsNone(board)
        self.assertTrue(errors)

    def test_closed_book_expectation_is_refused(self) -> None:
        expectation = sealed_day_expectation()
        expectation["sealed_book_rpc_slice"] = "complete"
        board, errors = score_stamps([example_sealed_cold_runner_bind()], expectation)
        self.assertIsNone(board)
        self.assertTrue(errors)

    def test_rate_tamper_rejected(self) -> None:
        board = example_mixed()
        board["full_book"]["reject_stamps_dropped"] = 1
        self.assertTrue(validate_scoreboard(board))

    def test_graph_lift_number_rejected(self) -> None:
        board = example_slots_not_scored()
        board["graph_lift"] = 0
        self.assertTrue(validate_scoreboard(board))

    def test_honesty_exp006_promoted_cannot_flip(self) -> None:
        board = example_all_runner()
        board["honesty"]["exp006_promoted"] = True
        self.assertTrue(validate_scoreboard(board))

    def test_soft_watch_cannot_block(self) -> None:
        board = example_all_runner()
        board["soft_watches"]["blocking"] = True
        self.assertTrue(validate_scoreboard(board))

    def test_duplicate_reject_is_kept(self) -> None:
        reject = example_reject_identity_bind()
        board, errors = score_stamps([reject, copy.deepcopy(reject)], sealed_day_expectation())
        self.assertEqual(errors, [])
        assert board is not None
        self.assertEqual(board["full_book"]["reject_n"], 2)
        self.assertEqual(board["fixture_join"]["duplicate_identity_n"], 1)

    def test_module_stays_off_observe_and_measure_exits(self) -> None:
        source = (
            ROOT / "tools" / "paper_fill_sim_scoreboard_sealed_fixture_v0.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("observe.client", source)
        self.assertNotIn("observe/client", source)
        self.assertNotIn("requests", source)
        self.assertIn("/var/lib/mal", source)


if __name__ == "__main__":
    unittest.main()
