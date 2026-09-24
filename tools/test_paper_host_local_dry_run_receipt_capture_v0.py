"""Schema and CLI parity for paper-host-local-dry-run-receipt-capture-v0.

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

from tools.paper_host_local_dry_run_receipt_capture_v0 import (
    SOFT_WATCH_ITEMS,
    main,
    validate_capture,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "paper_host_local_dry_run_receipt_capture_v0"
SCHEMA = ROOT / "ARTIFACTS" / "paper-host-local-dry-run-receipt-capture-v0.schema.json"
MODULE = ROOT / "tools" / "paper_host_local_dry_run_receipt_capture_v0.py"
PARENT_MODULE = ROOT / "tools" / "paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.py"
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


class PaperHostLocalDryRunReceiptCaptureV0Tests(unittest.TestCase):
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
        self.assertIs(measure["invented_ev"]["const"], False)
        self.assertIs(measure["invented_lift"]["const"], False)
        self.assertIs(schema["properties"]["graph_lift"]["const"], None)
        self.assertEqual(schema["properties"]["graph_policy"]["const"], "cold")
        self.assertIs(schema["$defs"]["softWatches"]["properties"]["blocking"]["const"], False)
        self.assertEqual(
            schema["$defs"]["softWatches"]["properties"]["items"]["items"]["enum"],
            list(SOFT_WATCH_ITEMS),
        )
        knowable = schema["$defs"]["knowableAtT"]["properties"]
        self.assertIs(knowable["host_file_bytes_required"]["const"], False)
        self.assertIs(knowable["numeric_horizon_known"]["const"], False)
        self.assertIs(knowable["delta_exec_known"]["const"], False)
        self.assertIs(knowable["return_known"]["const"], False)
        self.assertEqual(knowable["sealed_book_status"]["const"], "incomplete")
        self.assertIs(schema["$defs"]["recorded"]["properties"]["host_bytes_embedded"]["const"], False)
        self.assertIs(schema["$defs"]["recorded"]["properties"]["receipt_body_embedded"]["const"], False)
        self.assertIs(schema["$defs"]["outcome"]["properties"]["var_lib_mal_opened"]["const"], False)
        honesty = schema["$defs"]["honesty"]["properties"]
        self.assertIs(honesty["var_lib_mal_read"]["const"], False)
        self.assertIs(honesty["executed_host_sealed_book"]["const"], False)
        self.assertIs(honesty["ci_claimed_sealed_book_close"]["const"], False)
        self.assertEqual(schema["$defs"]["parentDryRun"]["properties"]["commit"]["const"], "f17b59f")
        self.assertEqual(schema["$defs"]["parentDryRun"]["properties"]["pull_request"]["const"], 53)
        self.assertIs(
            schema["$defs"]["parentDryRun"]["properties"]["rewritten_by_this_stamp"]["const"],
            False,
        )
        self.assertEqual(schema["$defs"]["honestyAlign"]["properties"]["commit"]["const"], "ca4e815")

    def test_receipt_citation_does_not_embed_a_body(self) -> None:
        for name in NAMES:
            text = (FIXTURES / name).read_text(encoding="utf-8")
            doc = json.loads(text)
            self.assertIs(doc["carries"]["receipt_body"], False)
            self.assertIs(doc["carries"]["host_bytes"], False)
            self.assertIs(doc["carries"]["horizons"], False)
            self.assertIs(doc["carries"]["delta_exec"], False)
            self.assertIs(doc["recorded"]["receipt_body_embedded"], False)
            self.assertEqual(doc["measure"]["kind"], "none")
            self.assertEqual(doc["dual_read"]["sealed_book_rpc_slice"], "incomplete")
            self.assertIs(doc["dual_read"]["closed_book_claim"], False)
            self.assertIs(doc["knowable_at_t"]["return_known"], False)
            self.assertNotIn("l1_spine", text)
            self.assertNotIn("SigSynth20Runner", text)
            self.assertNotIn("price_proxy", text)
            self.assertNotIn("ws_payload", text)
            self.assertNotIn("FAIL_NO_LIFT", text)
        declared = _load(FIXTURES / "receipt_operator_declared.json")
        self.assertIs(declared["recorded"]["host_jsonl_read_on_receipt"], True)
        self.assertIs(declared["outcome"]["path_opened"], False)
        self.assertIs(declared["outcome"]["var_lib_mal_opened"], False)
        self.assertIn(HOST_ROOT, json.dumps(_load(FIXTURES / "refuse_host_path.json")["subject"]))

    def test_closed_book_and_measure_fail_schema_and_cli(self) -> None:
        doc = _load(FIXTURES / "receipt_projection_on_synthetic.json")
        doc["dual_read"]["closed_book_claim"] = True
        doc["measure"]["kind"] = "alpha"
        doc["measure"]["invented_ev"] = True
        doc["honesty"]["claims_alpha"] = True
        doc["honesty"]["var_lib_mal_read"] = True
        doc["outcome"]["var_lib_mal_opened"] = True
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

        host = f"{HOST_ROOT}/paper/paper-host-local-dry-run-receipt-capture-v0/capture.json"
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
        self.assertNotIn("paper_host_local_dry_run_receipt_capture", OBSERVE_CLIENT.read_text(encoding="utf-8"))
        self.assertIn("rewritten_by_this_stamp", text)
        self.assertTrue(PARENT_MODULE.is_file())
        parent = PARENT_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("paper_host_local_dry_run_receipt_capture", parent)

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


if __name__ == "__main__":
    unittest.main()
