"""Schema and CLI parity for paper-laya-risk-gate-lock-receipt-v0."""

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

from tools.paper_laya_risk_gate_lock_receipt_v0 import (
    FILL_SIM_SURROUND_COMMIT,
    FILL_SIM_SURROUND_ID,
    HOST_ROOT,
    NON_FILL_SURROUND_COMMIT,
    NON_FILL_SURROUND_ID,
    SOFT_WATCH_ITEMS,
    example_fill_sim_surround,
    example_mixed_spines,
    example_non_fill_surround,
    main,
    validate_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "paper_laya_risk_gate_lock_receipt_v0"
SCHEMA = ROOT / "ARTIFACTS" / "paper-laya-risk-gate-lock-receipt-v0.schema.json"
MODULE = ROOT / "tools" / "paper_laya_risk_gate_lock_receipt_v0.py"
OBSERVE_CLIENT = ROOT / "observe" / "client.py"

NAMES = (
    "receipt_non_fill_sim_surround.json",
    "receipt_fill_sim_surround.json",
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


class PaperLayaRiskGateLockReceiptV0Tests(unittest.TestCase):
    def test_fixtures_pass_schema_and_cli(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                doc = _load(FIXTURES / name)
                self.assertEqual(_schema_errors(doc), [])
                self.assertEqual(validate_receipt(doc), [])
                buf = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                    code = main(["validate", str(FIXTURES / name)])
                self.assertEqual(code, 0)
                self.assertTrue(buf.getvalue().startswith("ok "))

    def test_examples_match_fixtures(self) -> None:
        self.assertEqual(
            example_non_fill_surround(),
            _load(FIXTURES / "receipt_non_fill_sim_surround.json"),
        )
        self.assertEqual(
            example_fill_sim_surround(),
            _load(FIXTURES / "receipt_fill_sim_surround.json"),
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
        rg = schema["$defs"]["riskGate"]["properties"]
        self.assertEqual(rg["decision"]["const"], "locked")
        self.assertIs(rg["unlock"]["const"], False)
        self.assertIs(schema["properties"]["graph_lift"]["const"], None)
        self.assertEqual(schema["properties"]["graph_policy"]["const"], "cold")
        self.assertEqual(
            schema["$defs"]["softWatches"]["properties"]["items"]["items"]["enum"],
            list(SOFT_WATCH_ITEMS),
        )

    def test_citations_name_parent_surround_registrations(self) -> None:
        non_fill = _load(FIXTURES / "receipt_non_fill_sim_surround.json")
        reg = non_fill["surround_citations"][0]["registration"]
        self.assertEqual(reg["id"], NON_FILL_SURROUND_ID)
        self.assertEqual(reg["commit"], NON_FILL_SURROUND_COMMIT)
        fill_sim = _load(FIXTURES / "receipt_fill_sim_surround.json")
        reg2 = fill_sim["surround_citations"][0]["registration"]
        self.assertEqual(reg2["id"], FILL_SIM_SURROUND_ID)
        self.assertEqual(reg2["commit"], FILL_SIM_SURROUND_COMMIT)

    def test_surround_body_not_embedded(self) -> None:
        for name in NAMES:
            doc = _load(FIXTURES / name)
            self.assertIs(doc["carries"]["surround_body"], False)
            self.assertIs(doc["surround_citations"][0]["surround_body_embedded"], False)

    def test_dishonest_closed_book_fails_schema_and_cli(self) -> None:
        doc = copy.deepcopy(example_non_fill_surround())
        doc["dual_read"]["closed_book_claim"] = True
        self.assertTrue(_schema_errors(doc))
        self.assertTrue(validate_receipt(doc))

    def test_dishonest_measure_kind_fails_schema_and_cli(self) -> None:
        doc = copy.deepcopy(example_non_fill_surround())
        doc["measure"]["kind"] = "oracle"
        self.assertTrue(_schema_errors(doc))
        self.assertTrue(validate_receipt(doc))

    def test_dishonest_risk_gate_unlock_fails_schema_and_cli(self) -> None:
        doc = copy.deepcopy(example_non_fill_surround())
        doc["laya"]["risk_gate_unlock"] = True
        doc["risk_gate"]["unlock"] = True
        doc["risk_gate"]["decision"] = "unlocked"
        self.assertTrue(_schema_errors(doc))
        self.assertTrue(validate_receipt(doc))

    def test_dishonest_laya_authorize_run_fails_schema_and_cli(self) -> None:
        doc = copy.deepcopy(example_non_fill_surround())
        doc["laya"]["authorize_run"] = True
        self.assertTrue(_schema_errors(doc))
        self.assertTrue(validate_receipt(doc))

    def test_dishonest_live_trading_fails_schema_and_cli(self) -> None:
        doc = copy.deepcopy(example_non_fill_surround())
        doc["laya"]["live_trading"] = True
        self.assertTrue(_schema_errors(doc))
        self.assertTrue(validate_receipt(doc))

    def test_invented_ev_key_fails_cli(self) -> None:
        doc = copy.deepcopy(example_non_fill_surround())
        doc["invented_ev"] = 1.0
        self.assertTrue(validate_receipt(doc))

    def test_host_absolute_path_refused_without_read_text(self) -> None:
        target = f"{HOST_ROOT}/paper/lock-receipt.json"
        with mock.patch("pathlib.Path.read_text") as read_text:
            code = _quiet(["validate", target])
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_host_double_slash_path_refused_without_read_text(self) -> None:
        target = f"//var/lib/mal/paper/lock-receipt.json"
        with mock.patch("pathlib.Path.read_text") as read_text:
            code = _quiet(["validate", target])
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_host_triple_slash_path_refused_without_read_text(self) -> None:
        target = f"///var/lib/mal/paper/lock-receipt.json"
        with mock.patch("pathlib.Path.read_text") as read_text:
            code = _quiet(["validate", target])
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_relative_var_lib_mal_at_root_cwd_refused_without_read_text(self) -> None:
        with mock.patch("os.getcwd", return_value="/"):
            with mock.patch("pathlib.Path.read_text") as read_text:
                code = _quiet(["validate", "var/lib/mal/paper/lock-receipt.json"])
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_dot_slash_var_lib_mal_refused_without_read_text(self) -> None:
        with mock.patch("os.getcwd", return_value="/"):
            with mock.patch("pathlib.Path.read_text") as read_text:
                code = _quiet(["validate", "./var/lib/mal/paper/lock-receipt.json"])
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_parent_dotdot_var_lib_mal_refused_without_read_text(self) -> None:
        with mock.patch("os.getcwd", return_value="/var/lib"):
            with mock.patch("pathlib.Path.read_text") as read_text:
                code = _quiet(["validate", "../lib/mal/paper/lock-receipt.json"])
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_assemble_refuses_host_surround_path(self) -> None:
        with mock.patch("pathlib.Path.read_text") as read_text:
            code = _quiet(
                [
                    "assemble",
                    "--surround",
                    f"{HOST_ROOT}/mixed_scoreboard.json",
                ]
            )
        self.assertEqual(code, 1)
        read_text.assert_not_called()

    def test_mixed_spines_cites_both_registrations(self) -> None:
        doc = example_mixed_spines()
        ids = {item["registration"]["id"] for item in doc["surround_citations"]}
        self.assertEqual(
            ids,
            {NON_FILL_SURROUND_ID, FILL_SIM_SURROUND_ID},
        )
        self.assertEqual(_schema_errors(doc), [])
        self.assertEqual(validate_receipt(doc), [])

    def test_soft_watch_items_listed(self) -> None:
        doc = example_non_fill_surround()
        for item in SOFT_WATCH_ITEMS:
            self.assertIn(item, doc["soft_watches"]["items"])

    def test_observe_client_untouched(self) -> None:
        before = OBSERVE_CLIENT.read_text(encoding="utf-8")
        _quiet(["example", "--which", "non-fill-surround"])
        after = OBSERVE_CLIENT.read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_module_docstring_mentions_locked_risk_gate(self) -> None:
        text = MODULE.read_text(encoding="utf-8")
        self.assertIn("locked", text)
        self.assertIn(HOST_ROOT, text)


if __name__ == "__main__":
    unittest.main()
