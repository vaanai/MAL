"""Offline tests for fill-sim batch host-local sealed JSONL dry-run receipt. No network."""

from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tools.paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0 import (
    SOFT_WATCH_ITEMS as PARENT_SOFT_WATCH_ITEMS,
)
from tools.paper_fill_sim_hot_packet_evaluate_v0 import FILL_SIM_REJECT, FILL_SIM_RUNNER
from tools.paper_fill_sim_scoreboard_sealed_fixture_v0 import HOST_ROOT
from tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 import (
    HOST_EXPECTATION,
    HOST_JSONL,
    SOFT_WATCH_ITEMS,
    assemble_receipt,
    example_operator_declared,
    example_projection_on_synthetic,
    example_synthetic_replay,
    main,
    validate_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = (
    ROOT / "fixtures" / "paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0"
)
PARENT_FIXTURES = ROOT / "fixtures" / "paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0"
SCHEMA = (
    ROOT
    / "ARTIFACTS"
    / "paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json"
)
MODULE = ROOT / "tools" / "paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.py"
PARENT_MODULE = ROOT / "tools" / "paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0.py"
OBSERVE_CLIENT = ROOT / "observe" / "client.py"


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return main(argv)


class PaperFillSimBatchHostLocalSealedJsonlDryRunIncompleteRpcV0Tests(unittest.TestCase):
    def test_examples_validate_and_match_fixtures(self) -> None:
        cases = {
            "synthetic-replay": example_synthetic_replay,
            "projection-on-synthetic": example_projection_on_synthetic,
            "operator-declared": example_operator_declared,
        }
        names = {
            "synthetic-replay": "synthetic_replay.json",
            "projection-on-synthetic": "projection_on_synthetic.json",
            "operator-declared": "operator_declared.json",
        }
        for which, builder in cases.items():
            receipt = builder()
            self.assertEqual(validate_receipt(receipt), [])
            on_disk = json.loads((FIXTURES / names[which]).read_text(encoding="utf-8"))
            self.assertEqual(on_disk, receipt)
            self.assertEqual(_quiet(["validate", str(FIXTURES / names[which])]), 0)
            self.assertEqual(_quiet(["example", "--which", which]), 0)

    def test_schema_file_locks_the_honesty_caps(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        dual = schema["$defs"]["dualRead"]["properties"]
        self.assertEqual(dual["sealed_book_rpc_slice"]["const"], "incomplete")
        self.assertIs(dual["closed_book_claim"]["const"], False)
        measure = schema["$defs"]["measure"]["properties"]
        self.assertEqual(measure["kind"]["const"], "none")
        present = schema["$defs"]["parentDigestPresent"]["properties"]
        arms = present["fill_sim_status_arms"]["prefixItems"]
        self.assertEqual(arms[0]["const"], FILL_SIM_RUNNER)
        self.assertEqual(arms[1]["const"], FILL_SIM_REJECT)
        self.assertIs(present["marks_joined"]["const"], False)
        self.assertEqual(schema["$defs"]["parent"]["properties"]["commit"]["const"], "4664363")
        self.assertEqual(schema["$defs"]["parent"]["properties"]["pull_request"]["const"], 59)
        watches = schema["$defs"]["softWatches"]["properties"]
        self.assertIs(watches["blocking"]["const"], False)
        items = watches["items"]["items"]["enum"]
        self.assertEqual(items, list(SOFT_WATCH_ITEMS))
        for item in PARENT_SOFT_WATCH_ITEMS:
            self.assertIn(item, items)

    def test_host_jsonl_read_true_does_not_close_the_book(self) -> None:
        projection = example_projection_on_synthetic()
        declared = example_operator_declared()
        for receipt in (projection, declared):
            self.assertIs(receipt["input"]["host_jsonl_read"], True)
            self.assertEqual(receipt["dual_read"]["sealed_book_rpc_slice"], "incomplete")
            self.assertIs(receipt["dual_read"]["closed_book_claim"], False)
            self.assertEqual(receipt["measure"]["kind"], "none")
            self.assertIs(receipt["input"]["marks_joined"], False)
            self.assertIs(receipt["input"]["var_lib_mal_opened"], False)

        digest = projection["parent_digest"]
        self.assertEqual(digest["fill_sim_status_arms"], [FILL_SIM_RUNNER, FILL_SIM_REJECT])
        self.assertIs(digest["marks_joined"], False)

    def test_synthetic_replay_parent_digest_lists_both_fill_sim_arms(self) -> None:
        receipt = example_synthetic_replay()
        self.assertIs(receipt["input"]["host_jsonl_read"], False)
        self.assertEqual(
            receipt["parent_digest"]["fill_sim_status_arms"],
            [FILL_SIM_RUNNER, FILL_SIM_REJECT],
        )

    def test_receipts_do_not_embed_batch_body_or_host_extract(self) -> None:
        for name in (
            "synthetic_replay.json",
            "projection_on_synthetic.json",
            "operator_declared.json",
        ):
            text = (FIXTURES / name).read_text(encoding="utf-8")
            self.assertNotIn("l1_spine", text)
            self.assertNotIn("FAIL_NO_LIFT", text)
            self.assertLess(len(text.encode("utf-8")), 25_000)
        self.assertFalse(any(FIXTURES.glob("*.jsonl")))

    def test_operator_declared_does_not_call_parent(self) -> None:
        def _boom(*_args, **_kwargs):
            raise AssertionError("parent CLI was called")

        with mock.patch(
            "tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.parent_main",
            _boom,
        ):
            receipt = example_operator_declared()
        self.assertIs(receipt["input"]["parent_invoked"], False)
        self.assertEqual(receipt["invocation"]["jsonl"], [HOST_JSONL[d] for d in ("2026-09-20", "2026-09-21")])

    def test_receipt_cli_on_synthetic_fixtures(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(
                [
                    "receipt",
                    "--fixture-origin",
                    "sealed_row_projection",
                    "--jsonl",
                    str(PARENT_FIXTURES / "observe-2026-09-21.jsonl"),
                    "--jsonl",
                    str(PARENT_FIXTURES / "observe-2026-09-20.jsonl"),
                    "--expectation",
                    str(PARENT_FIXTURES / "sealed_day_2026-09-21_expectation.json"),
                    "--expectation",
                    str(PARENT_FIXTURES / "sealed_day_2026-09-20_expectation.json"),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buf.getvalue()), example_projection_on_synthetic())

    def test_refuses_var_lib_mal_without_calling_parent(self) -> None:
        called: list[object] = []

        def _record(argv=None):
            called.append(argv)
            return 0

        argv = [
            "receipt",
            "--fixture-origin",
            "sealed_row_projection",
            "--jsonl",
            "/var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl",
            "--jsonl",
            "/var/lib/mal/sealed/jsonl/observe-2026-09-21.jsonl",
            "--expectation",
            "/var/lib/mal/paper/paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-20_expectation.json",
            "--expectation",
            "/var/lib/mal/paper/paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-21_expectation.json",
        ]
        with mock.patch(
            "tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.parent_main",
            _record,
        ):
            self.assertEqual(_quiet(argv), 1)
        self.assertEqual(called, [])

    def test_receipt_refuses_double_slash_var_lib_mal_without_parent(self) -> None:
        called: list[object] = []

        def _record(argv=None):
            called.append(argv)
            return 0

        argv = [
            "receipt",
            "--jsonl",
            "//var/lib/mal/observe-2026-09-20.jsonl",
            "--expectation",
            str(PARENT_FIXTURES / "sealed_day_2026-09-20_expectation.json"),
        ]
        with mock.patch(
            "tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.parent_main",
            _record,
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(argv)
        self.assertEqual(code, 1)
        self.assertEqual(called, [])
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

        def _is_host_path(text: str) -> bool:
            collapsed = "/" + text.lstrip("/") if text.startswith("//") else text
            norm = os.path.normpath(collapsed)
            return norm == HOST_ROOT or norm.startswith(HOST_ROOT + os.sep)

        def _guarded_read_text(self: Path, *args: object, **kwargs: object) -> str:
            if _is_host_path(os.fspath(self)):
                raise AssertionError(f"read_text on host path: {self}")
            return real_read_text(self, *args, **kwargs)

        def _guarded_open(self: Path, *args: object, **kwargs: object) -> object:
            if _is_host_path(os.fspath(self)):
                raise AssertionError(f"open on host path: {self}")
            return real_open(self, *args, **kwargs)

        def _guarded_stat(self: Path, *args: object, **kwargs: object) -> object:
            if _is_host_path(os.fspath(self)):
                raise AssertionError(f"stat on host path: {self}")
            return real_stat(self, *args, **kwargs)

        with (
            mock.patch.object(Path, "read_text", _guarded_read_text),
            mock.patch.object(Path, "open", _guarded_open),
            mock.patch.object(Path, "stat", _guarded_stat),
            mock.patch("os.getcwd", return_value=cwd),
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(argv)
        self.assertEqual(code, 1)
        self.assertIn("does not open", err.getvalue())

    def test_validate_refuses_relative_var_lib_mal_at_root_cwd_without_fs_touch(self) -> None:
        self._assert_refuses_without_host_fs_touch(["validate", "var/lib/mal/receipt.json"])

    def test_receipt_refuses_relative_var_lib_mal_at_root_cwd_without_fs_touch(self) -> None:
        self._assert_refuses_without_host_fs_touch(
            [
                "receipt",
                "--jsonl",
                "var/lib/mal/observe-2026-09-20.jsonl",
                "--expectation",
                str(PARENT_FIXTURES / "sealed_day_2026-09-20_expectation.json"),
            ],
        )

    def test_validate_refuses_absolute_var_lib_mal_before_read_text(self) -> None:
        reads: list[str] = []

        def _read_text(self: Path, *args: object, **kwargs: object) -> str:
            reads.append(os.fspath(self))
            return json.dumps(example_operator_declared())

        host = (
            "/var/lib/mal/paper/"
            "paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0/receipt.json"
        )
        with mock.patch.object(Path, "read_text", _read_text):
            code = _quiet(["validate", host])
        self.assertEqual(code, 1)
        self.assertEqual(reads, [])

    def test_validate_refuses_symlink_realpath_before_read_text(self) -> None:
        reads: list[str] = []

        def _read_text(self: Path, *args: object, **kwargs: object) -> str:
            reads.append(os.fspath(self))
            return json.dumps(example_operator_declared())

        tail = "link/../var/lib/mal/paper/receipt.json"
        with tempfile.TemporaryDirectory() as tmp:
            os.symlink("/var", os.path.join(tmp, "link"))
            absolute = os.path.join(tmp, tail)
            with mock.patch.object(Path, "read_text", _read_text):
                code = _quiet(["validate", absolute])
            self.assertEqual(code, 1)
            self.assertEqual(reads, [])

    def test_closed_book_tamper_fails_validate(self) -> None:
        receipt = example_synthetic_replay()
        tampered = copy.deepcopy(receipt)
        tampered["dual_read"]["closed_book_claim"] = True
        tampered["measure"]["kind"] = "ev_lift"
        self.assertTrue(validate_receipt(tampered))

    def test_module_does_not_import_observe_client(self) -> None:
        text = MODULE.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                self.assertNotIn("observe.client", stripped)
        self.assertTrue(OBSERVE_CLIENT.is_file())
        self.assertTrue(PARENT_MODULE.is_file())

    def test_checked_in_fixture_dir_has_no_observe_jsonl(self) -> None:
        names = sorted(path.name for path in FIXTURES.iterdir())
        self.assertEqual(
            names,
            [
                "operator_declared.json",
                "projection_on_synthetic.json",
                "synthetic_replay.json",
            ],
        )


if __name__ == "__main__":
    unittest.main()
