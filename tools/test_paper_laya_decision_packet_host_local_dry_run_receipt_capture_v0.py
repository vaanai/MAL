"""Schema and CLI parity for paper-laya-decision-packet-host-local-dry-run-receipt-capture-v0.

Honest captures pass JSON Schema and the capture CLI. Dishonest copies fail both.
No RPC. No host read. observe/client.py is not imported.
"""

from __future__ import annotations

import copy
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock

from jsonschema import Draft202012Validator

from tools.paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 import (
    DIGEST_LABEL_ARMS,
    HOST_JSONL,
    HOST_MANIFEST,
    RECEIPT_ID as PARENT_DRY_RUN_ID,
)
from tools.paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0 import (
    RECEIPT_PATHS,
    SOFT_WATCH_ITEMS,
    main,
    validate_capture,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0"
SCHEMA = (
    ROOT
    / "ARTIFACTS"
    / "paper-laya-decision-packet-host-local-dry-run-receipt-capture-v0.schema.json"
)
MODULE = ROOT / "tools" / "paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0.py"
PARENT_MODULE = (
    ROOT
    / "tools"
    / "paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.py"
)
OBSERVE_CLIENT = ROOT / "observe" / "client.py"
HOST_ROOT = "/var/lib/mal"

NAMES = (
    "receipt_synthetic_replay.json",
    "receipt_projection_on_synthetic.json",
    "receipt_operator_declared.json",
    "refuse_host_path.json",
    "refuse_dishonest_receipt.json",
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_errors(doc: Any) -> list[str]:
    schema = _load(SCHEMA)
    validator = Draft202012Validator(schema)
    return [err.json_path for err in validator.iter_errors(doc)]


class PaperLayaDecisionPacketHostLocalDryRunReceiptCaptureV0Tests(unittest.TestCase):
    def test_fixtures_pass_schema_and_cli(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                doc = _load(FIXTURES / name)
                self.assertEqual(_schema_errors(doc), [])
                self.assertEqual(validate_capture(doc), [])
                buf = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                    code = main(["validate", str(FIXTURES / name)])
                self.assertEqual(code, 0)
                self.assertTrue(buf.getvalue().startswith("ok "))

    def test_schema_locks_honesty_caps(self) -> None:
        schema = _load(SCHEMA)
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        dual = schema["$defs"]["dualRead"]["properties"]
        self.assertEqual(dual["sealed_book_rpc_slice"]["const"], "incomplete")
        self.assertIs(dual["closed_book_claim"]["const"], False)
        measure = schema["$defs"]["measure"]["properties"]
        self.assertEqual(measure["kind"]["const"], "none")
        self.assertIs(schema["properties"]["graph_lift"]["const"], None)
        self.assertEqual(schema["properties"]["graph_policy"]["const"], "cold")
        self.assertIs(schema["$defs"]["softWatches"]["properties"]["blocking"]["const"], False)
        self.assertEqual(
            schema["$defs"]["softWatches"]["properties"]["items"]["items"]["enum"],
            list(SOFT_WATCH_ITEMS),
        )
        arms = schema["$defs"]["fullBook"]["properties"]["digest_label_arms"]["prefixItems"]
        self.assertEqual(arms[0]["const"], "runner")
        self.assertEqual(arms[1]["const"], "reject")
        self.assertEqual(schema["$defs"]["parentDryRun"]["properties"]["commit"]["const"], "d020fc9")
        self.assertEqual(schema["$defs"]["parentDryRun"]["properties"]["pull_request"]["const"], 70)
        self.assertEqual(schema["$defs"]["honestyAlign"]["properties"]["commit"]["const"], "3128128")
        risk = schema["$defs"]["riskGate"]["properties"]
        self.assertEqual(risk["decision"]["const"], "locked")
        self.assertIs(risk["unlock"]["const"], False)
        laya = schema["$defs"]["laya"]["properties"]
        self.assertIs(laya["authorize_run"]["const"], False)
        self.assertIs(laya["risk_gate_unlock"]["const"], False)
        self.assertIs(laya["live_trading"]["const"], False)

    def test_receipt_citation_does_not_embed_a_body(self) -> None:
        for name in NAMES:
            text = (FIXTURES / name).read_text(encoding="utf-8")
            doc = json.loads(text)
            self.assertIs(doc["carries"]["receipt_body"], False)
            self.assertIs(doc["carries"]["host_bytes"], False)
            self.assertEqual(doc["measure"]["kind"], "none")
            self.assertEqual(doc["dual_read"]["sealed_book_rpc_slice"], "incomplete")
            self.assertIs(doc["dual_read"]["closed_book_claim"], False)
            self.assertEqual(doc["risk_gate"]["decision"], "locked")
            self.assertIs(doc["risk_gate"]["unlock"], False)
            self.assertIs(doc["laya"]["authorize_run"], False)
            self.assertNotIn("FAIL_NO_LIFT", text)
        declared = _load(FIXTURES / "receipt_operator_declared.json")
        self.assertIs(declared["recorded"]["host_jsonl_read_on_receipt"], True)
        self.assertIs(declared["outcome"]["path_opened"], False)
        self.assertIsNone(declared["recorded"]["digest_label_arms_on_receipt"])
        replay = _load(FIXTURES / "receipt_synthetic_replay.json")
        self.assertEqual(
            replay["recorded"]["digest_label_arms_on_receipt"],
            list(DIGEST_LABEL_ARMS),
        )
        self.assertIn(HOST_ROOT, json.dumps(_load(FIXTURES / "refuse_host_path.json")["subject"]))

    def test_closed_book_and_measure_fail_schema_and_cli(self) -> None:
        doc = _load(FIXTURES / "receipt_projection_on_synthetic.json")
        doc["dual_read"]["closed_book_claim"] = True
        doc["measure"]["kind"] = "alpha"
        doc["measure"]["invented_ev"] = True
        doc["honesty"]["claims_alpha"] = True
        doc["honesty"]["var_lib_mal_read"] = True
        doc["outcome"]["var_lib_mal_opened"] = True
        doc["risk_gate"]["unlock"] = True
        doc["laya"]["authorize_run"] = True
        self.assertTrue(_schema_errors(doc))
        self.assertTrue(validate_capture(doc))

    def test_refuse_cannot_claim_a_recorded_host_extract(self) -> None:
        doc = _load(FIXTURES / "refuse_host_path.json")
        doc["outcome"]["recorded"] = True
        doc["outcome"]["path_opened"] = True
        doc["recorded"]["host_bytes_embedded"] = True
        doc["recorded"]["receipt_body_embedded"] = True
        doc["knowable_at_t"]["host_file_bytes_required"] = True
        doc["knowable_at_t"]["sealed_book_status"] = "complete"
        self.assertTrue(_schema_errors(doc))
        self.assertTrue(validate_capture(doc))

    def test_validate_refuses_var_lib_mal_before_read_text(self) -> None:
        reads: list[str] = []

        def _read_text(self: Path, *args: object, **kwargs: object) -> str:
            reads.append(os.fspath(self))
            return "{}"

        host = f"{HOST_ROOT}/paper/{PARENT_DRY_RUN_ID}/capture.json"
        err = io.StringIO()
        with (
            mock.patch.object(Path, "read_text", _read_text),
            redirect_stdout(io.StringIO()),
            redirect_stderr(err),
        ):
            code = main(["validate", host])
        self.assertEqual(code, 1)
        self.assertEqual(reads, [])
        self.assertIn("does not open", err.getvalue())

    def test_validate_refuses_double_slash_var_lib_mal_before_read_text(self) -> None:
        reads: list[str] = []

        def _read_text(self: Path, *args: object, **kwargs: object) -> str:
            reads.append(os.fspath(self))
            return "{}"

        host = f"//{HOST_ROOT}/paper/capture.json"
        err = io.StringIO()
        with (
            mock.patch.object(Path, "read_text", _read_text),
            redirect_stdout(io.StringIO()),
            redirect_stderr(err),
        ):
            code = main(["validate", host])
        self.assertEqual(code, 1)
        self.assertEqual(reads, [])
        self.assertIn("does not open", err.getvalue())

    def _assert_refuses_without_host_fs_touch(self, path: str, *, cwd: str = "/") -> None:
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
                code = main(["validate", path])
        self.assertEqual(code, 1)
        self.assertTrue(
            "does not open" in err.getvalue()
            or "non-canonical" in err.getvalue()
            or "parent-segment" in err.getvalue()
        )

    def test_validate_refuses_relative_var_lib_mal_at_root_cwd_without_fs_touch(self) -> None:
        self._assert_refuses_without_host_fs_touch("var/lib/mal/capture.json")

    def test_validate_refuses_fixture_collapse_without_fs_touch(self) -> None:
        def _guarded_read_text(self: Path, *args: object, **kwargs: object) -> str:
            raise AssertionError(f"read_text during lexical refuse test: {self}")

        base = (
            "fixtures/paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0/"
            "receipt_synthetic_replay.json"
        )
        for candidate in (
            base.replace("/receipt", "//receipt"),
            f"./{base}",
            f"{base}/",
        ):
            with mock.patch.object(Path, "read_text", _guarded_read_text):
                err = io.StringIO()
                with redirect_stderr(err):
                    code = main(["validate", candidate])
            self.assertEqual(code, 1)
            self.assertIn("non-canonical", err.getvalue())

    def test_validate_refuses_backslash_fixture_path_without_fs_touch(self) -> None:
        candidate = (
            "fixtures\\paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0\\"
            "receipt_synthetic_replay.json"
        )
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["validate", candidate])
        self.assertEqual(code, 1)
        self.assertIn("backslash", err.getvalue())

    def test_module_does_not_import_observe(self) -> None:
        text = MODULE.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                self.assertNotIn("observe", stripped)
                self.assertNotIn("paramiko", stripped)
                self.assertNotIn("subprocess", stripped)
                self.assertNotIn("socket", stripped)
        self.assertTrue(OBSERVE_CLIENT.is_file())
        self.assertNotIn(
            "paper_laya_decision_packet_host_local_dry_run_receipt_capture",
            OBSERVE_CLIENT.read_text(encoding="utf-8"),
        )
        self.assertIn("rewritten_by_this_stamp", text)
        self.assertTrue(PARENT_MODULE.is_file())
        parent = PARENT_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("paper_laya_decision_packet_host_local_dry_run_receipt_capture", parent)

    def test_usage_exits_1(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["example"])
        self.assertEqual(ctx.exception.code, 1)

    def test_copy_stays_a_copy(self) -> None:
        original = _load(FIXTURES / "refuse_dishonest_receipt.json")
        tampered = copy.deepcopy(original)
        tampered["subject"]["probe_document_checked_in"] = True
        self.assertTrue(_schema_errors(tampered))
        self.assertIs(original["subject"]["probe_document_checked_in"], False)

    def test_cited_receipt_path_collapse_rejected_by_schema_and_cli(self) -> None:
        operator_path = RECEIPT_PATHS["operator_declared"]
        cases: tuple[tuple[str, tuple[str, ...], object], ...] = (
            (
                "receipt_operator_declared.json",
                ("subject", "receipt_path"),
                "//" + operator_path.lstrip("/"),
            ),
            (
                "receipt_operator_declared.json",
                ("subject", "receipt_path"),
                "./" + operator_path,
            ),
            (
                "receipt_operator_declared.json",
                ("recorded", "receipt_path"),
                operator_path + "/",
            ),
            (
                "receipt_synthetic_replay.json",
                ("subject", "receipt_path"),
                "//" + RECEIPT_PATHS["synthetic_replay"].lstrip("/"),
            ),
        )
        for name, path, value in cases:
            with self.subTest(name=name, path=path, value=value):
                doc = copy.deepcopy(_load(FIXTURES / name))
                cursor: Any = doc
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor[path[-1]] = value
                self.assertTrue(_schema_errors(doc))
                self.assertTrue(validate_capture(doc))

    def test_host_path_catalog_collapse_rejected_by_schema_and_cli(self) -> None:
        day = "2026-09-20"
        cases: tuple[tuple[int, tuple[str, ...], object], ...] = (
            (0, ("path",), "//var/lib/mal/x"),
            (1, ("jsonl", 0), HOST_JSONL[day] + "/"),
            (1, ("jsonl", 0), "./" + HOST_JSONL[day].lstrip("/")),
            (1, ("manifest", 0), HOST_MANIFEST[day].replace("/", "\\", 1)),
            (
                2,
                ("path",),
                (
                    "fixtures/paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0/"
                    "../../var/lib/mal/paper/receipt.json"
                ),
            ),
        )
        for form_index, path, value in cases:
            with self.subTest(form_index=form_index, path=path, value=value):
                doc = copy.deepcopy(_load(FIXTURES / "refuse_host_path.json"))
                cursor: Any = doc["subject"]["forms"][form_index]
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor[path[-1]] = value
                self.assertTrue(_schema_errors(doc))
                self.assertTrue(validate_capture(doc))


if __name__ == "__main__":
    unittest.main()
