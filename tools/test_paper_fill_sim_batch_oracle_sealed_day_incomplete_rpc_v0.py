"""Offline tests for paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0. No network."""

from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from tools.hot_packet_v0 import SCHEMA_VERSION as HOT_SCHEMA
from tools.paper_evaluate_hot_packet_v0 import SCHEMA_VERSION as EVAL_SCHEMA
from tools.paper_fill_sim_hot_packet_evaluate_v0 import (
    FILL_SIM_REJECT,
    FILL_SIM_RUNNER,
    SCHEMA_VERSION as FILL_SIM_SCHEMA,
)
from tools.paper_fill_sim_scoreboard_sealed_fixture_v0 import (
    HOST_ROOT,
    SCHEMA_VERSION as SCOREBOARD_SCHEMA,
)
from tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0 import project_day, read_jsonl
from tools.paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0 import (
    EXAMPLE_DAYS,
    PIPELINE,
    SOFT_WATCH_ITEMS,
    assemble,
    example_two_day,
    main,
    validate_batch,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0"
SCHEMA = ROOT / "ARTIFACTS" / "paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json"
MODULE = ROOT / "tools" / "paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0.py"
BATCH_NAME = "two_day.json"


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()):
        return main(argv)


class PaperFillSimBatchOracleSealedDayIncompleteRpcV0Tests(unittest.TestCase):
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
            for row in on_disk["rows"]:
                self.assertIn(
                    row["fill_sim_status"],
                    (FILL_SIM_RUNNER, FILL_SIM_REJECT),
                )

    def test_schema_file_locks_the_honesty_caps(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["title"],
            "MAL paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc v0 (Proposed paper batch)",
        )
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(
            schema["$defs"]["dayResult"]["properties"]["scoreboard"]["$ref"],
            "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json",
        )
        dual = schema["$defs"]["dualRead"]["properties"]
        self.assertEqual(dual["sealed_book_rpc_slice"]["const"], "incomplete")
        self.assertIs(dual["closed_book_claim"]["const"], False)
        self.assertEqual(
            dual["incomplete_reason"]["const"],
            "fill_sim_batch_projection_does_not_close_the_sealed_book",
        )
        measure = schema["$defs"]["measure"]["properties"]
        self.assertEqual(measure["kind"]["const"], "none")
        self.assertIs(measure["pass_fail_no_lift"]["const"], False)
        pipeline = schema["$defs"]["input"]["properties"]["pipeline"]
        self.assertEqual(
            [item["const"] for item in pipeline["prefixItems"]],
            list(PIPELINE),
        )
        items = schema["$defs"]["softWatches"]["properties"]["items"]["items"]["enum"]
        for item in SOFT_WATCH_ITEMS:
            self.assertIn(item, items)
        self.assertIs(
            schema["$defs"]["honesty"]["properties"]["exp006_promoted"]["const"],
            False,
        )
        self.assertIs(
            schema["$defs"]["honesty"]["properties"]["var_lib_mal_read"]["const"],
            False,
        )

    def test_schema_rejects_scoreboard_missing_reject_fill_sim_status_row(self) -> None:
        batch = example_two_day()
        board = copy.deepcopy(batch["days"][0]["scoreboard"])
        board["fill_sim_status_counts"] = [board["fill_sim_status_counts"][0]]
        fs_schema = json.loads(
            (ROOT / "ARTIFACTS" / "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json").read_text(
                encoding="utf-8"
            )
        )
        fragment = {
            "$schema": fs_schema["$schema"],
            "$defs": {
                "share": fs_schema["$defs"]["share"],
                "fillSimStatusCountRow": fs_schema["$defs"]["fillSimStatusCountRow"],
            },
            "type": "object",
            "required": ["fill_sim_status_counts"],
            "properties": {
                "fill_sim_status_counts": fs_schema["properties"]["fill_sim_status_counts"],
            },
        }
        errors = list(Draft202012Validator(fragment).iter_errors(board))
        self.assertTrue(errors)

    def test_two_day_keeps_both_arms_fill_sim_and_open_book(self) -> None:
        batch = example_two_day()
        self.assertEqual([day["day"] for day in batch["days"]], list(EXAMPLE_DAYS))
        self.assertEqual(batch["dual_read"]["sealed_book_rpc_slice"], "incomplete")
        self.assertIs(batch["dual_read"]["closed_book_claim"], False)
        self.assertEqual(batch["measure"]["kind"], "none")
        self.assertIs(batch["input"]["marks_joined"], False)
        self.assertIs(batch["honesty"]["exp006_promoted"], False)
        self.assertIs(batch["honesty"]["marks_joined_on_this_stamp"], False)
        self.assertEqual(batch["full_book"]["n"], 4)

        for day in batch["days"]:
            board = day["scoreboard"]
            self.assertEqual(board["schema_version"], SCOREBOARD_SCHEMA)
            statuses = [row["status"] for row in board["fill_sim_status_counts"]]
            self.assertEqual(statuses[0], FILL_SIM_RUNNER)
            self.assertEqual(statuses[1], FILL_SIM_REJECT)
            for stamp in board["input"]["stamps"]:
                self.assertEqual(stamp["schema_version"], FILL_SIM_SCHEMA)
                evaluate = stamp["input"]["stamp"]
                self.assertEqual(evaluate["schema_version"], EVAL_SCHEMA)
                packet = evaluate["input"]["packet"]
                self.assertEqual(packet["schema_version"], HOT_SCHEMA)
                self.assertIs(packet["graph"]["cold"], True)

    def test_pipeline_spine(self) -> None:
        batch = example_two_day()
        self.assertEqual(batch["input"]["pipeline"], list(PIPELINE))

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

    def test_batch_refuses_var_lib_mal_jsonl_without_stat(self) -> None:
        real_read_text = Path.read_text

        def _guarded_read_text(self: Path, *args: object, **kwargs: object) -> str:
            text = os.fspath(self)
            collapsed = "/" + text.lstrip("/") if text.startswith("//") else text
            if collapsed == HOST_ROOT or collapsed.startswith(HOST_ROOT + os.sep):
                raise AssertionError(f"read_text called on host path: {text}")
            return real_read_text(self, *args, **kwargs)

        argv = [
            "batch",
            "--jsonl",
            "//var/lib/mal/observe-2026-09-20.jsonl",
            "--expectation",
            str(FIXTURES / "sealed_day_2026-09-20_expectation.json"),
        ]
        with mock.patch.object(Path, "read_text", _guarded_read_text):
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(argv)
        self.assertEqual(code, 1)
        self.assertIn("does not open", err.getvalue())

    def _assert_refuses_without_host_fs_touch(
        self,
        argv: list[str],
        *,
        cwd: str = "/",
    ) -> None:
        real_read_text = Path.read_text
        real_open = Path.open
        real_stat = Path.stat
        real_resolve = Path.resolve

        def _is_host_path(text: str) -> bool:
            collapsed = "/" + text.lstrip("/") if text.startswith("//") else text
            norm = os.path.normpath(collapsed)
            return norm == HOST_ROOT or norm.startswith(HOST_ROOT + os.sep)

        def _guarded_read_text(self: Path, *args: object, **kwargs: object) -> str:
            text = os.fspath(self)
            if _is_host_path(text):
                raise AssertionError(f"read_text called on host path: {text}")
            return real_read_text(self, *args, **kwargs)

        def _guarded_open(self: Path, *args: object, **kwargs: object) -> object:
            text = os.fspath(self)
            if _is_host_path(text):
                raise AssertionError(f"open called on host path: {text}")
            return real_open(self, *args, **kwargs)

        def _guarded_stat(self: Path, *args: object, **kwargs: object) -> object:
            text = os.fspath(self)
            if _is_host_path(text):
                raise AssertionError(f"stat called on host path: {text}")
            return real_stat(self, *args, **kwargs)

        def _guarded_resolve(self: Path, *args: object, **kwargs: object) -> Path:
            text = os.fspath(self)
            if _is_host_path(text):
                raise AssertionError(f"resolve called on host path: {text}")
            return real_resolve(self, *args, **kwargs)

        with (
            mock.patch.object(Path, "read_text", _guarded_read_text),
            mock.patch.object(Path, "open", _guarded_open),
            mock.patch.object(Path, "stat", _guarded_stat),
            mock.patch.object(Path, "resolve", _guarded_resolve),
            mock.patch("os.getcwd", return_value=cwd),
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(argv)
        self.assertEqual(code, 1)
        self.assertIn("does not open", err.getvalue())
        self.assertIn(HOST_ROOT, err.getvalue())

    def test_batch_refuses_relative_var_lib_mal_jsonl_at_root_cwd_without_fs_touch(
        self,
    ) -> None:
        self._assert_refuses_without_host_fs_touch(
            [
                "batch",
                "--jsonl",
                "var/lib/mal/observe-2026-09-20.jsonl",
                "--expectation",
                str(FIXTURES / "sealed_day_2026-09-20_expectation.json"),
            ],
        )

    def test_batch_refuses_relative_var_lib_mal_expectation_at_root_cwd_without_fs_touch(
        self,
    ) -> None:
        self._assert_refuses_without_host_fs_touch(
            [
                "batch",
                "--jsonl",
                str(FIXTURES / "observe-2026-09-20.jsonl"),
                "--expectation",
                "var/lib/mal/sealed_day_2026-09-20_expectation.json",
            ],
        )

    def test_validate_refuses_relative_var_lib_mal_at_root_cwd_without_fs_touch(
        self,
    ) -> None:
        self._assert_refuses_without_host_fs_touch(["validate", "var/lib/mal/two_day.json"])

    def test_batch_refuses_dot_slash_var_lib_mal_at_root_cwd_without_fs_touch(
        self,
    ) -> None:
        self._assert_refuses_without_host_fs_touch(
            [
                "batch",
                "--jsonl",
                "./var/lib/mal/observe-2026-09-20.jsonl",
                "--expectation",
                str(FIXTURES / "sealed_day_2026-09-20_expectation.json"),
            ],
        )

    def test_validate_refuses_double_slash_batch_path_without_open(self) -> None:
        real_open = Path.read_text

        def _guarded_read_text(self: Path, *args: object, **kwargs: object) -> str:
            text = os.fspath(self)
            collapsed = "/" + text.lstrip("/") if text.startswith("//") else text
            if collapsed == HOST_ROOT or collapsed.startswith(HOST_ROOT + os.sep):
                raise AssertionError(f"read_text called on host path: {text}")
            return real_open(self, *args, **kwargs)

        with mock.patch.object(Path, "read_text", _guarded_read_text):
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(["validate", "//var/lib/mal/two_day.json"])
        self.assertEqual(code, 1)
        self.assertIn("does not open", err.getvalue())

    def test_closed_book_tamper_fails_validate(self) -> None:
        batch = example_two_day()
        tampered = copy.deepcopy(batch)
        tampered["dual_read"]["closed_book_claim"] = True
        tampered["measure"]["kind"] = "ev_lift"
        self.assertTrue(validate_batch(tampered))

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

    def test_module_does_not_import_the_observe_encoder(self) -> None:
        text = MODULE.read_text(encoding="utf-8")
        self.assertNotIn("observe.client", text)
        self.assertNotIn("import observe", text)

    def test_day_misalignment_is_refused(self) -> None:
        rows, errors = read_jsonl(FIXTURES / "observe-2026-09-20.jsonl")
        self.assertEqual(errors, [])
        shifted = copy.deepcopy(rows)
        shifted[0]["t_ws"] = "2026-09-22T12:00:00.000+00:00"
        _packets, _census, problems = project_day(shifted, day="2026-09-20", fixture_origin="synthetic")
        self.assertTrue(any("UTC calendar day" in item for item in problems))


if __name__ == "__main__":
    unittest.main()
