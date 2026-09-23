"""Offline tests for the host-local sealed JSONL dry-run receipt. No network."""

from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0 import (
    SOFT_WATCH_ITEMS as PARENT_SOFT_WATCH_ITEMS,
)
from tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 import (
    HOST_EXPECTATION,
    HOST_JSONL,
    HOST_ROOT,
    SOFT_WATCH_ITEMS,
    assemble_receipt,
    example_operator_declared,
    example_projection_on_synthetic,
    example_synthetic_replay,
    main,
    validate_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0"
PARENT_FIXTURES = ROOT / "fixtures" / "paper_batch_oracle_sealed_day_incomplete_rpc_v0"
SCHEMA = (
    ROOT
    / "ARTIFACTS"
    / "paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json"
)
MODULE = ROOT / "tools" / "paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.py"
PARENT_MODULE = ROOT / "tools" / "paper_batch_oracle_sealed_day_incomplete_rpc_v0.py"
PARENT_RUNBOOK = ROOT / "tools" / "paper_batch_oracle_run.md"
OBSERVE_CLIENT = ROOT / "observe" / "client.py"


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return main(argv)


class PaperBatchHostLocalSealedJsonlDryRunIncompleteRpcV0Tests(unittest.TestCase):
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
        self.assertEqual(
            schema["title"],
            "MAL paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc v0 (Proposed dry-run receipt)",
        )
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        dual = schema["$defs"]["dualRead"]["properties"]
        self.assertEqual(dual["sealed_book_rpc_slice"]["const"], "incomplete")
        self.assertIs(dual["closed_book_claim"]["const"], False)
        measure = schema["$defs"]["measure"]["properties"]
        self.assertEqual(measure["kind"]["const"], "none")
        self.assertIs(measure["pass_fail_no_lift"]["const"], False)
        self.assertIs(measure["invented_ev"]["const"], False)
        self.assertIs(measure["invented_lift"]["const"], False)
        self.assertEqual(schema["properties"]["graph_policy"]["const"], "cold")
        self.assertIs(schema["properties"]["graph_lift"]["const"], None)
        self.assertEqual(
            schema["$defs"]["fullBook"]["properties"]["reject_stamps_dropped"]["const"],
            0,
        )
        self.assertEqual(
            schema["$defs"]["fullBook"]["properties"]["policy"]["const"],
            "dec007_both_arms_retained",
        )
        self.assertIs(
            schema["$defs"]["fullBook"]["properties"]["both_arms_unchanged"]["const"],
            True,
        )
        watches = schema["$defs"]["softWatches"]["properties"]
        self.assertIs(watches["blocking"]["const"], False)
        items = watches["items"]["items"]["enum"]
        self.assertEqual(items, list(SOFT_WATCH_ITEMS))
        for item in PARENT_SOFT_WATCH_ITEMS:
            self.assertIn(item, items)
        self.assertIn("host_jsonl_read_is_not_a_closed_book", items)
        self.assertIn("operator_path_string_is_not_a_host_extract", items)
        self.assertIn("dry_run_does_not_ssh_or_open_var_lib_mal", items)
        self.assertIn("parent_stamp_not_rewritten", items)
        honesty = schema["$defs"]["honesty"]["properties"]
        self.assertIs(honesty["executed_host_sealed_book"]["const"], False)
        self.assertIs(honesty["scored_oracle_measure"]["const"], False)
        self.assertIs(honesty["ci_claimed_sealed_book_close"]["const"], False)
        self.assertIs(honesty["ssh_to_host"]["const"], False)
        self.assertIs(honesty["var_lib_mal_read"]["const"], False)
        self.assertIs(honesty["pumpportal_trade_api"]["const"], False)
        self.assertIs(schema["$defs"]["input"]["properties"]["var_lib_mal_opened"]["const"], False)
        self.assertIs(schema["$defs"]["input"]["properties"]["rpc"]["const"], False)
        present = schema["$defs"]["parentDigestPresent"]["properties"]
        self.assertEqual(present["sealed_book_rpc_slice"]["const"], "incomplete")
        self.assertIs(present["closed_book_claim"]["const"], False)
        self.assertEqual(present["measure_kind"]["const"], "none")
        self.assertIs(present["batch_embedded"]["const"], False)
        self.assertEqual(schema["$defs"]["parent"]["properties"]["commit"]["const"], "243e11b")
        self.assertEqual(schema["$defs"]["parent"]["properties"]["pull_request"]["const"], 52)
        self.assertIs(
            schema["$defs"]["parent"]["properties"]["rewritten_by_this_stamp"]["const"],
            False,
        )
        self.assertEqual(
            schema["$defs"]["invocation"]["properties"]["days"]["prefixItems"][0]["const"],
            "2026-09-20",
        )
        self.assertEqual(
            schema["$defs"]["invocation"]["properties"]["days"]["prefixItems"][1]["const"],
            "2026-09-21",
        )
        self.assertIs(
            schema["$defs"]["invocation"]["properties"]["host_contacted"]["const"],
            False,
        )

    def test_host_jsonl_read_true_does_not_close_the_book(self) -> None:
        projection = example_projection_on_synthetic()
        declared = example_operator_declared()
        for receipt in (projection, declared):
            self.assertIs(receipt["input"]["host_jsonl_read"], True)
            self.assertEqual(receipt["dual_read"]["sealed_book_rpc_slice"], "incomplete")
            self.assertIs(receipt["dual_read"]["closed_book_claim"], False)
            self.assertEqual(receipt["measure"]["kind"], "none")
            self.assertIs(receipt["measure"]["pass_fail_no_lift"], False)
            self.assertIs(receipt["measure"]["invented_ev"], False)
            self.assertIs(receipt["measure"]["invented_lift"], False)
            self.assertIs(receipt["measure"]["claims_alpha"], False)
            self.assertIs(receipt["honesty"]["executed_host_sealed_book"], False)
            self.assertIs(receipt["honesty"]["scored_oracle_measure"], False)
            self.assertIs(receipt["honesty"]["ci_claimed_sealed_book_close"], False)
            self.assertIs(receipt["input"]["var_lib_mal_opened"], False)
            self.assertIs(receipt["input"]["ssh_to_host"], False)
            self.assertIs(receipt["input"]["rpc"], False)
            self.assertIs(receipt["invocation"]["host_contacted"], False)
            self.assertEqual(receipt["invocation"]["host"], "mal-core-vnic")
            self.assertIs(receipt["graph_lift"], None)
            self.assertEqual(receipt["graph_policy"], "cold")
            self.assertIs(receipt["full_book"]["both_arms_unchanged"], True)
            self.assertEqual(receipt["full_book"]["reject_stamps_dropped"], 0)
            self.assertIs(receipt["soft_watches"]["blocking"], False)
            self.assertIs(receipt["carries"]["host_bytes"], False)
            self.assertIs(receipt["carries"]["rollup_counts"], False)
            self.assertIs(receipt["carries"]["horizons"], False)
            self.assertIs(receipt["carries"]["delta_exec"], False)
            self.assertIs(receipt["carries"]["parent_batch_body"], False)
            for item in PARENT_SOFT_WATCH_ITEMS:
                self.assertIn(item, receipt["soft_watches"]["items"])

        self.assertEqual(projection["receipt_kind"], "projection_on_synthetic")
        self.assertIs(projection["input"]["host_path_declared"], False)
        self.assertIs(projection["input"]["parent_invoked"], True)
        self.assertIs(projection["parent_digest"]["included"], True)
        self.assertEqual(projection["parent_digest"]["sealed_book_rpc_slice"], "incomplete")
        self.assertIs(projection["parent_digest"]["closed_book_claim"], False)
        self.assertEqual(projection["parent_digest"]["measure_kind"], "none")
        self.assertIs(projection["parent_digest"]["host_jsonl_read"], True)
        self.assertTrue(
            all(path.startswith("fixtures/") for path in projection["invocation"]["jsonl"])
        )
        self.assertFalse(any(HOST_ROOT in path for path in projection["invocation"]["jsonl"]))

        self.assertEqual(declared["receipt_kind"], "operator_declared")
        self.assertIs(declared["input"]["host_path_declared"], True)
        self.assertIs(declared["input"]["parent_invoked"], False)
        self.assertIs(declared["invocation"]["executed_by_this_process"], False)
        self.assertIs(declared["parent_digest"]["included"], False)
        self.assertEqual(declared["invocation"]["jsonl"], [HOST_JSONL[day] for day in ("2026-09-20", "2026-09-21")])
        self.assertEqual(
            declared["invocation"]["expectation"],
            [HOST_EXPECTATION[day] for day in ("2026-09-20", "2026-09-21")],
        )
        self.assertIs(declared["parent"]["rewritten_by_this_stamp"], False)
        self.assertEqual(declared["parent"]["commit"], "243e11b")

    def test_synthetic_replay_keeps_host_jsonl_read_false(self) -> None:
        receipt = example_synthetic_replay()
        self.assertEqual(receipt["receipt_kind"], "synthetic_replay")
        self.assertEqual(receipt["input"]["fixture_origin"], "synthetic")
        self.assertIs(receipt["input"]["host_jsonl_read"], False)
        self.assertIs(receipt["input"]["host_path_declared"], False)
        self.assertIs(receipt["parent_digest"]["included"], True)
        self.assertIs(receipt["parent_digest"]["host_jsonl_read"], False)
        self.assertEqual(receipt["parent_digest"]["measure_kind"], "none")
        self.assertIs(receipt["dual_read"]["closed_book_claim"], False)
        self.assertEqual(receipt["invocation"]["days"], ["2026-09-20", "2026-09-21"])

    def test_receipts_do_not_embed_a_batch_or_a_host_extract(self) -> None:
        for name in (
            "synthetic_replay.json",
            "projection_on_synthetic.json",
            "operator_declared.json",
        ):
            text = (FIXTURES / name).read_text(encoding="utf-8")
            self.assertNotIn("l1_spine", text)
            self.assertNotIn("SigSynth20Runner", text)
            self.assertNotIn("price_proxy", text)
            self.assertNotIn("ws_payload", text)
            self.assertNotIn("FAIL_NO_LIFT", text)
            self.assertNotIn("global_95bps", text)
            self.assertNotIn("launchlab_init", text)
            self.assertLess(len(text.encode("utf-8")), 20_000)
        self.assertFalse(any(FIXTURES.glob("*.jsonl")))
        self.assertTrue((PARENT_FIXTURES / "observe-2026-09-20.jsonl").is_file())

    def test_operator_declared_does_not_call_parent_or_realpath_host(self) -> None:
        def _boom(*_args, **_kwargs):
            raise AssertionError("parent CLI was called")

        def _realpath(path: str) -> str:
            if str(path).startswith(HOST_ROOT):
                raise AssertionError(f"realpath on host path {path}")
            return Path(path).resolve().as_posix()

        with (
            mock.patch(
                "tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.parent_main",
                _boom,
            ),
            mock.patch(
                "tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.os.path.realpath",
                _realpath,
            ),
        ):
            receipt = example_operator_declared()
        self.assertIs(receipt["input"]["var_lib_mal_opened"], False)
        self.assertIs(receipt["input"]["host_jsonl_read"], True)
        self.assertIs(receipt["dual_read"]["closed_book_claim"], False)

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
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload, example_projection_on_synthetic())
        self.assertNotIn("SigSynth20Runner", buf.getvalue())
        self.assertEqual(payload["measure"]["kind"], "none")

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
            "/var/lib/mal/paper/paper-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-20_expectation.json",
            "--expectation",
            "/var/lib/mal/paper/paper-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-21_expectation.json",
        ]
        with mock.patch(
            "tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.parent_main",
            _record,
        ):
            code = _quiet(argv)
        self.assertEqual(code, 1)
        self.assertEqual(called, [])

        lexical = str(ROOT / "fixtures" / ".." / ".." / "var" / "lib" / "mal" / "sealed" / "jsonl" / "observe-2026-09-20.jsonl")
        escaped = [
            "receipt",
            "--jsonl",
            lexical,
            "--jsonl",
            str(PARENT_FIXTURES / "observe-2026-09-21.jsonl"),
            "--expectation",
            str(PARENT_FIXTURES / "sealed_day_2026-09-20_expectation.json"),
            "--expectation",
            str(PARENT_FIXTURES / "sealed_day_2026-09-21_expectation.json"),
        ]
        with mock.patch(
            "tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0.parent_main",
            _record,
        ):
            code = _quiet(escaped)
        self.assertEqual(code, 1)
        self.assertEqual(called, [])

    def test_closed_book_tamper_fails_validate(self) -> None:
        receipt = example_synthetic_replay()
        tampered = copy.deepcopy(receipt)
        tampered["dual_read"]["closed_book_claim"] = True
        tampered["dual_read"]["sealed_book_rpc_slice"] = "complete"
        tampered["measure"]["kind"] = "ev_lift"
        tampered["honesty"]["executed_host_sealed_book"] = True
        tampered["honesty"]["scored_oracle_measure"] = True
        tampered["input"]["var_lib_mal_opened"] = True
        self.assertTrue(validate_receipt(tampered))

        flipped = copy.deepcopy(receipt)
        flipped["input"]["host_jsonl_read"] = True
        flipped["parent_digest"]["host_jsonl_read"] = True
        self.assertTrue(validate_receipt(flipped))

        hot = copy.deepcopy(example_operator_declared())
        hot["graph_policy"] = "hot"
        hot["soft_watches"]["blocking"] = True
        self.assertTrue(validate_receipt(hot))

    def test_usage_does_not_use_a_measure_exit(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["receipt"])
        self.assertEqual(ctx.exception.code, 1)

    def test_module_does_not_import_observe_ssh_or_rpc_clients(self) -> None:
        text = MODULE.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                self.assertNotIn("observe", stripped)
                self.assertNotIn("paramiko", stripped)
                self.assertNotIn("subprocess", stripped)
                self.assertNotIn("socket", stripped)
                self.assertNotIn("requests", stripped)
                self.assertNotIn("httpx", stripped)
        self.assertIn("243e11b", text)
        self.assertTrue(OBSERVE_CLIENT.is_file())
        self.assertNotIn("paper_batch_host_local", OBSERVE_CLIENT.read_text(encoding="utf-8"))
        self.assertTrue(PARENT_MODULE.is_file())
        self.assertTrue(PARENT_RUNBOOK.is_file())

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
