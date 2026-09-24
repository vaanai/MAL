"""Schema and CLI parity for paper-laya-precompute-fill-sim-surround-packet-v0."""

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

from tools.paper_fill_sim_hot_packet_evaluate_v0 import FILL_SIM_REJECT, FILL_SIM_RUNNER
from tools.paper_laya_precompute_fill_sim_surround_packet_v0 import (
    HOST_ROOT,
    SOFT_WATCH_ITEMS,
    example_batch_two_day_with_digest,
    example_mixed_scoreboard,
    main,
    validate_surround,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "paper_laya_precompute_fill_sim_surround_packet_v0"
SCHEMA = ROOT / "ARTIFACTS" / "paper-laya-precompute-fill-sim-surround-packet-v0.schema.json"
MODULE = ROOT / "tools" / "paper_laya_precompute_fill_sim_surround_packet_v0.py"
OBSERVE_CLIENT = ROOT / "observe" / "client.py"

NAMES = (
    "mixed_scoreboard.json",
    "batch_two_day_with_digest.json",
)


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_errors(doc: object) -> list[str]:
    schema = _load(SCHEMA)
    validator = Draft202012Validator(schema)
    return [err.message for err in validator.iter_errors(doc)]


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return main(argv)


class PaperLayaPrecomputeFillSimSurroundPacketV0Tests(unittest.TestCase):
    def test_fixtures_pass_schema_and_cli(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                doc = _load(FIXTURES / name)
                self.assertEqual(_schema_errors(doc), [])
                self.assertEqual(validate_surround(doc), [])
                buf = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                    code = main(["validate", str(FIXTURES / name)])
                self.assertEqual(code, 0)
                self.assertTrue(buf.getvalue().startswith("ok "))

    def test_examples_match_fixtures(self) -> None:
        self.assertEqual(example_mixed_scoreboard(), _load(FIXTURES / "mixed_scoreboard.json"))
        self.assertEqual(
            example_batch_two_day_with_digest(),
            _load(FIXTURES / "batch_two_day_with_digest.json"),
        )

    def test_schema_locks_honesty_caps(self) -> None:
        schema = _load(SCHEMA)
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        dual = schema["$defs"]["dualRead"]["properties"]
        self.assertEqual(dual["sealed_book_rpc_slice"]["const"], "incomplete")
        self.assertIs(dual["closed_book_claim"]["const"], False)
        measure = schema["$defs"]["measure"]["properties"]
        self.assertEqual(measure["kind"]["const"], "none")
        laya = schema["$defs"]["layaCaps"]["properties"]
        self.assertIs(laya["authorize_run"]["const"], False)
        self.assertIs(laya["risk_gate_unlock"]["const"], False)
        self.assertIs(laya["live_trading"]["const"], False)
        self.assertIs(schema["properties"]["graph_lift"]["const"], None)
        self.assertEqual(schema["properties"]["graph_policy"]["const"], "cold")
        arms = schema["$defs"]["fillSimStatusCountRow"]["properties"]["status"]
        self.assertIn("type", arms)
        prefix = schema["properties"]["fill_sim_status_counts"]["prefixItems"]
        self.assertEqual(prefix[0]["allOf"][1]["properties"]["status"]["const"], FILL_SIM_RUNNER)
        self.assertEqual(prefix[1]["allOf"][1]["properties"]["status"]["const"], FILL_SIM_REJECT)
        self.assertEqual(
            schema["$defs"]["parentScoreboard"]["properties"]["commit"]["const"],
            "dfbab7a",
        )
        self.assertEqual(
            schema["$defs"]["parentBatch"]["properties"]["commit"]["const"],
            "4664363",
        )

    def test_dishonest_closed_book_fails_schema_and_cli(self) -> None:
        doc = copy.deepcopy(example_mixed_scoreboard())
        doc["dual_read"]["closed_book_claim"] = True
        self.assertTrue(_schema_errors(doc))
        self.assertTrue(validate_surround(doc))

    def test_dishonest_measure_kind_fails_schema_and_cli(self) -> None:
        doc = copy.deepcopy(example_mixed_scoreboard())
        doc["measure"]["kind"] = "oracle"
        self.assertTrue(_schema_errors(doc))
        self.assertTrue(validate_surround(doc))

    def test_dishonest_laya_authorize_run_fails_schema_and_cli(self) -> None:
        doc = copy.deepcopy(example_mixed_scoreboard())
        doc["laya"]["authorize_run"] = True
        self.assertTrue(_schema_errors(doc))
        self.assertTrue(validate_surround(doc))

    def test_invented_ev_key_fails_cli(self) -> None:
        doc = copy.deepcopy(example_mixed_scoreboard())
        doc["invented_ev"] = 1.0
        self.assertTrue(validate_surround(doc))

    def test_host_absolute_path_refused_without_stat(self) -> None:
        target = f"{HOST_ROOT}/paper/surround.json"
        with mock.patch("pathlib.Path.read_text") as read_text:
            code = _quiet(["validate", target])
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_host_double_slash_path_refused_without_stat(self) -> None:
        target = f"//var/lib/mal/paper/surround.json"
        with mock.patch("pathlib.Path.read_text") as read_text:
            code = _quiet(["validate", target])
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_relative_var_lib_mal_at_root_cwd_refused_without_stat(self) -> None:
        with mock.patch("os.getcwd", return_value="/"):
            with mock.patch("pathlib.Path.read_text") as read_text:
                code = _quiet(["validate", "var/lib/mal/paper/surround.json"])
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_assemble_refuses_host_scoreboard_path(self) -> None:
        with mock.patch("pathlib.Path.read_text") as read_text:
            code = _quiet(
                [
                    "assemble",
                    "--scoreboard",
                    f"{HOST_ROOT}/mixed.json",
                ]
            )
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_batch_fixture_includes_rollup_digest(self) -> None:
        doc = _load(FIXTURES / "batch_two_day_with_digest.json")
        self.assertTrue(doc["batch_digest"]["included"])
        rollup = doc["batch_digest"]["rollup"]
        self.assertEqual(rollup["n"], 4)
        self.assertEqual(rollup["runner_n"], 2)
        self.assertEqual(rollup["reject_n"], 2)
        self.assertEqual(len(doc["scoreboard_digests"]), 2)

    def test_soft_watch_items_listed(self) -> None:
        doc = example_mixed_scoreboard()
        for item in SOFT_WATCH_ITEMS:
            self.assertIn(item, doc["soft_watches"]["items"])

    def test_observe_client_untouched(self) -> None:
        before = OBSERVE_CLIENT.read_text(encoding="utf-8")
        _quiet(["example", "--which", "mixed-scoreboard"])
        after = OBSERVE_CLIENT.read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_module_docstring_mentions_no_measure_exit(self) -> None:
        text = MODULE.read_text(encoding="utf-8")
        self.assertIn("Not a measure", text)
        self.assertIn(HOST_ROOT, text)


if __name__ == "__main__":
    unittest.main()
