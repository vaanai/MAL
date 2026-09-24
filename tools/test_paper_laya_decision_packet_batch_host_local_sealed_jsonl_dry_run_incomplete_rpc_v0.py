"""Offline tests for LAYA decision-packet batch host-local dry-run receipt. No network."""

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

from tools.paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0 import (
    SOFT_WATCH_ITEMS as PARENT_SOFT_WATCH_ITEMS,
)
from tools.paper_laya_decision_packet_scoreboard_sealed_fixture_v0 import HOST_ROOT
from tools.paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 import (
    HOST_EXPECTATION,
    HOST_JSONL,
    HOST_MANIFEST,
    PARENT_FIXTURE_PREFIX,
    SOFT_WATCH_ITEMS,
    SYNTHETIC_MANIFEST,
    assemble_receipt,
    example_operator_declared,
    example_projection_on_synthetic,
    example_synthetic_replay,
    main,
    validate_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = (
    ROOT
    / "fixtures"
    / "paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0"
)
PARENT_FIXTURES = (
    ROOT / "fixtures" / "paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0"
)
SCHEMA = (
    ROOT
    / "ARTIFACTS"
    / "paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json"
)
MODULE = (
    ROOT
    / "tools"
    / "paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.py"
)
PARENT_MODULE = ROOT / "tools" / "paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0.py"
OBSERVE_CLIENT = ROOT / "observe" / "client.py"


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return main(argv)


def _schema_errors(doc: object) -> list[str]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    return [err.message for err in validator.iter_errors(doc)]


def _set_path(doc: dict[str, object], path: tuple[str, ...], value: object) -> None:
    cursor: object = doc
    for key in path[:-1]:
        cursor = cursor[key]  # type: ignore[index]
    cursor[path[-1]] = value  # type: ignore[index]


class PaperLayaDecisionPacketBatchHostLocalSealedJsonlDryRunIncompleteRpcV0Tests(
    unittest.TestCase
):
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
            self.assertEqual(_schema_errors(receipt), [])
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
        arms = present["digest_label_arms"]["prefixItems"]
        self.assertEqual(arms[0]["const"], "runner")
        self.assertEqual(arms[1]["const"], "reject")
        self.assertIs(present["marks_joined"]["const"], False)
        self.assertEqual(schema["$defs"]["parent"]["properties"]["commit"]["const"], "adadaa8")
        self.assertEqual(schema["$defs"]["parent"]["properties"]["pull_request"]["const"], 69)
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
            self.assertEqual(receipt["risk_gate"]["decision"], "locked")
            self.assertIs(receipt["risk_gate"]["unlock"], False)
            self.assertIs(receipt["laya"]["authorize_run"], False)

        digest = projection["parent_digest"]
        self.assertEqual(digest["digest_label_arms"], ["runner", "reject"])
        self.assertIs(digest["host_jsonl_read"], False)

    def test_synthetic_replay_parent_digest_lists_both_digest_arms(self) -> None:
        receipt = example_synthetic_replay()
        self.assertIs(receipt["input"]["host_jsonl_read"], False)
        self.assertEqual(receipt["parent_digest"]["digest_label_arms"], ["runner", "reject"])

    def test_receipts_do_not_embed_batch_body_or_host_extract(self) -> None:
        for name in (
            "synthetic_replay.json",
            "projection_on_synthetic.json",
            "operator_declared.json",
        ):
            text = (FIXTURES / name).read_text(encoding="utf-8")
            self.assertNotIn('"packet_n"', text)
            self.assertNotIn('"digest_stamp_n"', text)
            self.assertNotIn("FAIL_NO_LIFT", text)
            self.assertLess(len(text.encode("utf-8")), 30_000)
        self.assertFalse(any(FIXTURES.glob("calendar_labels/*.jsonl")))

    def test_operator_declared_does_not_call_parent(self) -> None:
        def _boom(*_args, **_kwargs):
            raise AssertionError("parent CLI was called")

        with mock.patch(
            "tools.paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.parent_main",
            _boom,
        ):
            receipt = example_operator_declared()
        self.assertIs(receipt["input"]["parent_invoked"], False)
        self.assertEqual(
            receipt["invocation"]["manifest"],
            [HOST_MANIFEST[d] for d in ("2026-09-20", "2026-09-21")],
        )

    def test_receipt_cli_on_synthetic_fixtures(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(
                [
                    "receipt",
                    "--receipt-kind",
                    "projection_on_synthetic",
                    "--jsonl",
                    f"{PARENT_FIXTURE_PREFIX}observe-2026-09-21.jsonl",
                    "--jsonl",
                    f"{PARENT_FIXTURE_PREFIX}observe-2026-09-20.jsonl",
                    "--manifest",
                    SYNTHETIC_MANIFEST["2026-09-21"],
                    "--manifest",
                    SYNTHETIC_MANIFEST["2026-09-20"],
                    "--expectation",
                    f"{PARENT_FIXTURE_PREFIX}sealed_day_2026-09-21_expectation.json",
                    "--expectation",
                    f"{PARENT_FIXTURE_PREFIX}sealed_day_2026-09-20_expectation.json",
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
            "--receipt-kind",
            "projection_on_synthetic",
            "--jsonl",
            "/var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl",
            "--jsonl",
            "/var/lib/mal/sealed/jsonl/observe-2026-09-21.jsonl",
            "--manifest",
            "/var/lib/mal/paper/paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0/decision-day-2026-09-20.json",
            "--manifest",
            "/var/lib/mal/paper/paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0/decision-day-2026-09-21.json",
            "--expectation",
            "/var/lib/mal/paper/paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-20_expectation.json",
            "--expectation",
            "/var/lib/mal/paper/paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-21_expectation.json",
        ]
        with mock.patch(
            "tools.paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.parent_main",
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
            "--manifest",
            str(PARENT_FIXTURES / "decision-day-2026-09-20.json"),
            "--expectation",
            str(PARENT_FIXTURES / "sealed_day_2026-09-20_expectation.json"),
        ]
        with mock.patch(
            "tools.paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.parent_main",
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
                "--manifest",
                str(PARENT_FIXTURES / "decision-day-2026-09-20.json"),
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
            "paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0/"
            "receipt.json"
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

    def test_validate_refuses_fixture_collapse_without_fs_touch(self) -> None:
        def _guarded_read_text(self: Path, *args: object, **kwargs: object) -> str:
            raise AssertionError(f"read_text during lexical refuse test: {self}")

        def _guarded_open(self: Path, *args: object, **kwargs: object) -> object:
            raise AssertionError(f"open during lexical refuse test: {self}")

        def _guarded_stat(self: Path, *args: object, **kwargs: object) -> object:
            raise AssertionError(f"stat during lexical refuse test: {self}")

        base = (
            "fixtures/paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/"
            "synthetic_replay.json"
        )
        for candidate in (
            base.replace("/synthetic", "//synthetic"),
            f"./{base}",
            f"{base}/",
        ):
            with (
                mock.patch.object(Path, "read_text", _guarded_read_text),
                mock.patch.object(Path, "open", _guarded_open),
                mock.patch.object(Path, "stat", _guarded_stat),
            ):
                err = io.StringIO()
                with redirect_stderr(err):
                    code = main(["validate", candidate])
            self.assertEqual(code, 1)
            self.assertIn("non-canonical", err.getvalue())

    def test_validate_refuses_backslash_fixture_path_without_fs_touch(self) -> None:
        candidate = (
            "fixtures\\paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_"
            "incomplete_rpc_v0\\synthetic_replay.json"
        )
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["validate", candidate])
        self.assertEqual(code, 1)
        self.assertIn("backslash", err.getvalue())

    def test_receipt_refuses_collapsed_manifest_without_parent(self) -> None:
        called: list[object] = []

        def _record(argv=None):
            called.append(argv)
            return 0

        bad_manifest = SYNTHETIC_MANIFEST["2026-09-20"].replace(
            "/decision-day", "//decision-day"
        )
        argv = [
            "receipt",
            "--receipt-kind",
            "synthetic_replay",
            "--jsonl",
            f"{PARENT_FIXTURE_PREFIX}observe-2026-09-20.jsonl",
            "--jsonl",
            f"{PARENT_FIXTURE_PREFIX}observe-2026-09-21.jsonl",
            "--manifest",
            bad_manifest,
            "--manifest",
            SYNTHETIC_MANIFEST["2026-09-21"],
            "--expectation",
            f"{PARENT_FIXTURE_PREFIX}sealed_day_2026-09-20_expectation.json",
            "--expectation",
            f"{PARENT_FIXTURE_PREFIX}sealed_day_2026-09-21_expectation.json",
        ]
        with mock.patch(
            "tools.paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.parent_main",
            _record,
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(argv)
        self.assertEqual(code, 1)
        self.assertEqual(called, [])
        self.assertIn("non-canonical", err.getvalue())

    def test_checked_in_fixture_dir_has_only_receipt_json(self) -> None:
        names = sorted(path.name for path in FIXTURES.iterdir())
        self.assertEqual(
            names,
            [
                "operator_declared.json",
                "projection_on_synthetic.json",
                "synthetic_replay.json",
            ],
        )

    def test_operator_declared_invocation_collapse_rejected_by_schema_and_cli(
        self,
    ) -> None:
        day = "2026-09-20"
        manifest = HOST_MANIFEST[day]
        jsonl = HOST_JSONL[day]
        expectation = HOST_EXPECTATION[day]
        cases: tuple[tuple[tuple[str, ...], object], ...] = (
            (("invocation", "manifest", 0), "//" + manifest.lstrip("/")),
            (
                ("invocation", "manifest", 0),
                manifest.replace("/paper/", "/./paper/"),
            ),
            (("invocation", "jsonl", 0), jsonl + "/"),
            (("invocation", "expectation", 0), "./" + expectation.lstrip("/")),
            (
                ("invocation", "manifest", 0),
                manifest.replace("/", "\\", 1),
            ),
            (
                ("invocation", "manifest", 0),
                (
                    "fixtures/paper_laya_decision_packet_batch_host_local_sealed_jsonl_"
                    "dry_run_incomplete_rpc_v0/../../var/lib/mal/paper/"
                    "paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0/"
                    f"decision-day-{day}.json"
                ),
            ),
            (("invocation", "manifest", 0), "///" + manifest.lstrip("/")),
        )
        for path, value in cases:
            with self.subTest(path=path, value=value):
                doc = copy.deepcopy(example_operator_declared())
                _set_path(doc, path, value)
                self.assertTrue(_schema_errors(doc))
                self.assertTrue(validate_receipt(doc))

    def test_argv_shape_collapse_rejected_by_schema_and_cli(self) -> None:
        synthetic = copy.deepcopy(example_synthetic_replay())
        bad_syn_manifest = synthetic["invocation"]["argv_shape"][7].replace(
            "/decision-day", "//decision-day"
        )
        synthetic["invocation"]["argv_shape"][7] = bad_syn_manifest
        self.assertTrue(_schema_errors(synthetic))
        self.assertTrue(validate_receipt(synthetic))

        projection = copy.deepcopy(example_projection_on_synthetic())
        projection["invocation"]["argv_shape"][7] = (
            "./" + projection["invocation"]["argv_shape"][7]
        )
        self.assertTrue(_schema_errors(projection))
        self.assertTrue(validate_receipt(projection))

        operator = copy.deepcopy(example_operator_declared())
        operator["invocation"]["argv_shape"][7] = (
            "//var/lib/mal/paper/paper-laya-decision-packet-batch-oracle-sealed-day-"
            "incomplete-rpc-v0/decision-day-2026-09-20.json"
        )
        self.assertTrue(_schema_errors(operator))
        self.assertTrue(validate_receipt(operator))


if __name__ == "__main__":
    unittest.main()
