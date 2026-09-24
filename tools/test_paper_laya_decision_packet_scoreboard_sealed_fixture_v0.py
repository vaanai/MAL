"""Offline tests for paper-laya-decision-packet-scoreboard-sealed-fixture-v0. No network."""

from __future__ import annotations

import copy
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from tools.paper_laya_decision_packet_scoreboard_sealed_fixture_v0 import (
    EXAMPLES,
    HORIZON_JOIN_STATUS,
    HOST_ROOT,
    SOFT_WATCH_ITEMS,
    example_all_spines,
    example_mixed_spines_only,
    main,
    score_packets,
    sealed_day_expectation,
    validate_expectation,
    validate_scoreboard,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "paper_laya_decision_packet_scoreboard_sealed_fixture_v0"
PACKET_FIXTURES = ROOT / "fixtures" / "paper_laya_precompute_decision_packet_v0"
SCHEMA = ROOT / "ARTIFACTS" / "paper-laya-decision-packet-scoreboard-sealed-fixture-v0.schema.json"
DECISION_SCHEMA = ROOT / "ARTIFACTS" / "paper-laya-precompute-decision-packet-v0.schema.json"
EXPECTATION_NAME = "sealed_day_2026-09-20_expectation.json"
OBSERVE_CLIENT = ROOT / "observe" / "client.py"

FIXTURE_NAMES = {
    "all-spines": "all_spines.json",
    "non-fill-only": "non_fill_only.json",
    "fill-sim-only": "fill_sim_only.json",
    "mixed-spines-only": "mixed_spines_only.json",
}

PACKET_FILES = [
    "decision_non_fill_sim_surround.json",
    "decision_fill_sim_surround.json",
    "decision_mixed_spines.json",
]


def _registry() -> Registry:
    resources: dict[str, Resource] = {}
    for path in (SCHEMA, DECISION_SCHEMA):
        data = json.loads(path.read_text(encoding="utf-8"))
        resources[data["$id"]] = Resource.from_contents(data)
    return Registry().with_resources(resources.items())


def _schema_errors(doc: object) -> list[str]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, registry=_registry())
    return [err.message for err in validator.iter_errors(doc)]


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()):
        return main(argv)


class PaperLayaDecisionPacketScoreboardSealedFixtureV0Tests(unittest.TestCase):
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
                self.assertEqual(_schema_errors(payload), [])
                self.assertEqual(_quiet(["validate", str(FIXTURES / filename)]), 0)

    def test_expectation_file_is_not_a_scoreboard(self) -> None:
        self.assertEqual(_quiet(["validate", str(FIXTURES / EXPECTATION_NAME)]), 1)

    def test_schema_file_is_json(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["title"],
            "MAL paper-laya-decision-packet-scoreboard-sealed-fixture v0 (Proposed paper scoreboard)",
        )
        self.assertEqual(
            schema["$defs"]["input"]["properties"]["packets"]["items"]["$ref"],
            "paper-laya-precompute-decision-packet-v0.schema.json",
        )
        self.assertEqual(schema["$defs"]["dualRead"]["properties"]["sealed_book_rpc_slice"]["const"], "incomplete")
        self.assertEqual(schema["$defs"]["fullBook"]["properties"]["reject_stamps_dropped"]["const"], 0)
        self.assertIn("fixture_joined_null_explicit", json.dumps(schema["$defs"]["horizons"]))
        self.assertEqual(schema["$defs"]["measure"]["properties"]["pass_fail_no_lift"]["const"], False)
        self.assertEqual(schema["$defs"]["measure"]["properties"]["kind"]["const"], "none")
        self.assertIs(schema["$defs"]["layaCaps"]["properties"]["authorize_run"]["const"], False)
        self.assertIs(schema["$defs"]["riskGate"]["properties"]["unlock"]["const"], False)
        items = [item["const"] for item in schema["$defs"]["softWatches"]["properties"]["items"]["prefixItems"]]
        for item in SOFT_WATCH_ITEMS:
            self.assertIn(item, items)
        self.assertEqual(schema["$defs"]["softWatches"]["properties"]["blocking"]["const"], False)

    def test_example_cli(self) -> None:
        self.assertEqual(_quiet(["example", "--which", "all-spines"]), 0)

    def test_usage_does_not_use_a_measure_exit(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["score"])
        self.assertEqual(ctx.exception.code, 1)

    def test_score_cli_on_checked_in_packets(self) -> None:
        paths = [str(PACKET_FIXTURES / name) for name in PACKET_FILES]
        code = _quiet(
            ["score", "--expectation", str(FIXTURES / EXPECTATION_NAME), *paths]
        )
        self.assertEqual(code, 0)
        packets = [
            json.loads((PACKET_FIXTURES / name).read_text(encoding="utf-8")) for name in PACKET_FILES
        ]
        board, errors = score_packets(packets, sealed_day_expectation())
        self.assertEqual(errors, [])
        self.assertEqual(board, example_all_spines())

    def test_score_cli_rejects_a_scoreboard_file(self) -> None:
        code = _quiet(
            [
                "score",
                "--expectation",
                str(FIXTURES / EXPECTATION_NAME),
                str(FIXTURES / "all_spines.json"),
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
                    "/tmp/packet.json",
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
                    str(PACKET_FIXTURES / "decision_non_fill_sim_surround.json"),
                ]
            ),
            1,
        )

    def test_host_absolute_path_refused_without_read_text(self) -> None:
        target = f"{HOST_ROOT}/paper/scoreboard.json"
        with mock.patch("pathlib.Path.read_text") as read_text:
            code = _quiet(["validate", target])
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_host_double_slash_path_refused_without_read_text(self) -> None:
        target = "//var/lib/mal/paper/scoreboard.json"
        with mock.patch("pathlib.Path.read_text") as read_text:
            code = _quiet(["validate", target])
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_host_triple_slash_path_refused_without_read_text(self) -> None:
        target = "///var/lib/mal/paper/scoreboard.json"
        with mock.patch("pathlib.Path.read_text") as read_text:
            code = _quiet(["validate", target])
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_relative_var_lib_mal_at_root_cwd_refused_without_read_text(self) -> None:
        target = "var/lib/mal/paper/scoreboard.json"
        with mock.patch("pathlib.Path.read_text") as read_text:
            with mock.patch("os.getcwd", return_value="/"):
                code = _quiet(["validate", target])
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_dishonest_closed_book_fails_schema_and_cli(self) -> None:
        board = copy.deepcopy(example_all_spines())
        board["dual_read"]["closed_book_claim"] = True
        self.assertTrue(_schema_errors(board))
        self.assertTrue(validate_scoreboard(board))

    def test_dishonest_measure_kind_fails_schema_and_cli(self) -> None:
        board = copy.deepcopy(example_all_spines())
        board["measure"]["kind"] = "oracle"
        self.assertTrue(_schema_errors(board))
        self.assertTrue(validate_scoreboard(board))

    def test_dishonest_risk_gate_unlock_fails_schema_and_cli(self) -> None:
        board = copy.deepcopy(example_all_spines())
        board["laya"]["risk_gate_unlock"] = True
        board["risk_gate"]["unlock"] = True
        board["risk_gate"]["decision"] = "unlocked"
        self.assertTrue(_schema_errors(board))
        self.assertTrue(validate_scoreboard(board))

    def test_dishonest_laya_authorize_run_fails_schema_and_cli(self) -> None:
        board = copy.deepcopy(example_all_spines())
        board["laya"]["authorize_run"] = True
        self.assertTrue(_schema_errors(board))
        self.assertTrue(validate_scoreboard(board))

    def test_dishonest_live_trading_fails_schema_and_cli(self) -> None:
        board = copy.deepcopy(example_all_spines())
        board["laya"]["live_trading"] = True
        self.assertTrue(_schema_errors(board))
        self.assertTrue(validate_scoreboard(board))

    def test_dishonest_numeric_horizon_fails_schema_and_cli(self) -> None:
        board = copy.deepcopy(example_all_spines())
        board["horizons"]["values"]["5s"] = 0.01
        self.assertTrue(_schema_errors(board))
        self.assertTrue(validate_scoreboard(board))

    def test_dishonest_delta_exec_zero_fails_schema_and_cli(self) -> None:
        board = copy.deepcopy(example_all_spines())
        board["delta_exec"]["value"] = 0
        self.assertTrue(_schema_errors(board))
        self.assertTrue(validate_scoreboard(board))

    def test_invented_ev_key_fails_cli(self) -> None:
        board = copy.deepcopy(example_all_spines())
        board["invented_ev"] = 1.0
        self.assertTrue(validate_scoreboard(board))

    def test_all_spines_keeps_zero_spine_rows(self) -> None:
        board = example_all_spines()
        self.assertEqual(board["full_book"]["packet_n"], 3)
        profiles = {row["spine_profile"]: row["n"] for row in board["spine_profile_counts"]}
        self.assertEqual(profiles["non_fill_sim_spine"], 1)
        self.assertEqual(profiles["fill_sim_spine"], 1)
        self.assertEqual(profiles["mixed_spines"], 1)

    def test_non_fill_only_has_expectation_gap(self) -> None:
        board = EXAMPLES["non-fill-only"]()
        self.assertEqual(board["fixture_join"]["expectation_rows_not_in_local_set"], 2)
        self.assertEqual(board["fixture_join"]["matched_n"], 1)

    def test_mixed_spines_only_digest_doubles(self) -> None:
        board = example_mixed_spines_only()
        self.assertEqual(board["full_book"]["digest_stamp_n"], 10)
        self.assertEqual(board["label_rates"]["runner_n"], 8)
        self.assertEqual(board["label_rates"]["reject_n"], 2)

    def test_observe_client_untouched(self) -> None:
        before = OBSERVE_CLIENT.read_text(encoding="utf-8")
        import tools.paper_laya_decision_packet_scoreboard_sealed_fixture_v0 as mod

        mod.example_all_spines()
        after = OBSERVE_CLIENT.read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_schema_rejects_board_missing_reject_spine_row(self) -> None:
        board = copy.deepcopy(example_all_spines())
        board["spine_profile_counts"] = [board["spine_profile_counts"][0]]
        self.assertTrue(_schema_errors(board))

    def test_schema_rejects_board_missing_reject_label_row(self) -> None:
        board = copy.deepcopy(example_all_spines())
        board["label_rates"]["rows"] = [board["label_rates"]["rows"][0]]
        self.assertTrue(_schema_errors(board))


if __name__ == "__main__":
    unittest.main()
