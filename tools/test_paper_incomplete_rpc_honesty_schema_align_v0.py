"""Schema-align proof for paper-incomplete-rpc-honesty-schema-align-v0.

Honest checked-in fixtures must pass JSON Schema and the matching CLI.
Dishonest copies must fail both. No RPC. No host read. observe/client.py is not imported.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from jsonschema.validators import extend
from referencing import Registry, Resource

from tools.hot_packet_v0 import validate_packet
from tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 import (
    validate_receipt,
)
from tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0 import validate_batch
from tools.paper_evaluate_hot_packet_v0 import validate_stamp
from tools.paper_scoreboard_sealed_fixture_v0 import (
    validate_expectation,
    validate_scoreboard,
)

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "ARTIFACTS"
OBSERVE_CLIENT = ROOT / "observe" / "client.py"

SCHEMA_FILES = (
    "hot-packet-v0.schema.json",
    "paper-evaluate-hot-packet-v0.schema.json",
    "paper-scoreboard-sealed-fixture-v0.schema.json",
    "paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json",
    "paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json",
)

HONEST: tuple[tuple[str, Path, str], ...] = (
    ("hot-packet-v0.schema.json", ROOT / "fixtures/hot_packet_v0/sealed_create_cold_graph.json", "packet"),
    ("hot-packet-v0.schema.json", ROOT / "fixtures/hot_packet_v0/enriched_global_95bps_cold_graph.json", "packet"),
    ("hot-packet-v0.schema.json", ROOT / "fixtures/hot_packet_v0/enriched_launchlab_init_cold_graph.json", "packet"),
    ("hot-packet-v0.schema.json", ROOT / "fixtures/hot_packet_v0/sealed_graph_slots_nullable.json", "packet"),
    ("paper-evaluate-hot-packet-v0.schema.json", ROOT / "fixtures/paper_evaluate_hot_packet_v0/sealed_cold_graph_runner.json", "stamp"),
    ("paper-evaluate-hot-packet-v0.schema.json", ROOT / "fixtures/paper_evaluate_hot_packet_v0/enriched_global_95bps_runner.json", "stamp"),
    ("paper-evaluate-hot-packet-v0.schema.json", ROOT / "fixtures/paper_evaluate_hot_packet_v0/enriched_launchlab_init_runner.json", "stamp"),
    ("paper-evaluate-hot-packet-v0.schema.json", ROOT / "fixtures/paper_evaluate_hot_packet_v0/sealed_graph_slots_not_scored_runner.json", "stamp"),
    ("paper-evaluate-hot-packet-v0.schema.json", ROOT / "fixtures/paper_evaluate_hot_packet_v0/reject_missing_identity.json", "stamp"),
    ("paper-scoreboard-sealed-fixture-v0.schema.json", ROOT / "fixtures/paper_scoreboard_sealed_fixture_v0/all_runner.json", "scoreboard"),
    ("paper-scoreboard-sealed-fixture-v0.schema.json", ROOT / "fixtures/paper_scoreboard_sealed_fixture_v0/mixed_runner_reject.json", "scoreboard"),
    ("paper-scoreboard-sealed-fixture-v0.schema.json", ROOT / "fixtures/paper_scoreboard_sealed_fixture_v0/identity_reject.json", "scoreboard"),
    ("paper-scoreboard-sealed-fixture-v0.schema.json", ROOT / "fixtures/paper_scoreboard_sealed_fixture_v0/graph_cold.json", "scoreboard"),
    ("paper-scoreboard-sealed-fixture-v0.schema.json", ROOT / "fixtures/paper_scoreboard_sealed_fixture_v0/slots_not_scored.json", "scoreboard"),
    ("paper-scoreboard-sealed-fixture-v0.schema.json#/$defs/expectation", ROOT / "fixtures/paper_scoreboard_sealed_fixture_v0/sealed_day_2026-09-20_expectation.json", "expectation"),
    ("paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json", ROOT / "fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json", "batch"),
    ("paper-scoreboard-sealed-fixture-v0.schema.json#/$defs/expectation", ROOT / "fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-20_expectation.json", "expectation"),
    ("paper-scoreboard-sealed-fixture-v0.schema.json#/$defs/expectation", ROOT / "fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-21_expectation.json", "expectation"),
    ("paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json", ROOT / "fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/synthetic_replay.json", "receipt"),
    ("paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json", ROOT / "fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/projection_on_synthetic.json", "receipt"),
    ("paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json", ROOT / "fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/operator_declared.json", "receipt"),
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _mal_strict_json_integer(validator, enabled, instance, schema):
    """Draft 2020-12 type integer accepts 1.0. The CLI does not."""
    if not enabled:
        return
    if isinstance(instance, bool) or not isinstance(instance, int):
        yield ValidationError(
            "integer slot rejects a whole-number float; JSON integer only"
        )


PaperValidator = extend(
    Draft202012Validator,
    {"malStrictJsonInteger": _mal_strict_json_integer},
)


def _registry() -> Registry:
    resources = []
    for name in SCHEMA_FILES:
        schema = _load(ART / name)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def _validator(ref: str, registry: Registry) -> Draft202012Validator:
    if "#/" in ref:
        name, pointer = ref.split("#", 1)
        schema = {"$ref": f"{name}#{pointer}"}
    else:
        name = ref
        schema = _load(ART / name)
    return PaperValidator(schema, registry=registry)


def _schema_errors(ref: str, doc: Any, registry: Registry) -> list[str]:
    return [err.json_path for err in _validator(ref, registry).iter_errors(doc)]


def _cli_errors(kind: str, doc: Any) -> list[str]:
    if kind == "packet":
        return validate_packet(doc)
    if kind == "stamp":
        return validate_stamp(doc)
    if kind == "scoreboard":
        return validate_scoreboard(doc)
    if kind == "expectation":
        return validate_expectation(doc)
    if kind == "batch":
        return validate_batch(doc)
    if kind == "receipt":
        return validate_receipt(doc)
    raise AssertionError(kind)


def _set_path(doc: dict[str, Any], path: tuple[Any, ...], value: Any) -> None:
    cursor: Any = doc
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value


class SchemaAlignV0Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = _registry()

    def test_honest_fixtures_pass_schema_and_cli(self) -> None:
        for ref, path, kind in HONEST:
            with self.subTest(path=str(path.relative_to(ROOT))):
                doc = _load(path)
                self.assertEqual(_schema_errors(ref, doc, self.registry), [])
                self.assertEqual(_cli_errors(kind, doc), [])

    def test_operator_declared_path_strings_are_not_an_open_claim(self) -> None:
        receipt = _load(
            ROOT
            / "fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/operator_declared.json"
        )
        joined = " ".join(receipt["invocation"]["jsonl"] + receipt["invocation"]["expectation"])
        self.assertIn("/var/lib/mal", joined)
        self.assertIs(receipt["input"]["var_lib_mal_opened"], False)
        self.assertIs(receipt["honesty"]["var_lib_mal_read"], False)
        self.assertIs(receipt["dual_read"]["closed_book_claim"], False)
        self.assertEqual(receipt["measure"]["kind"], "none")

    def test_closed_book_claim_fails_schema_and_cli(self) -> None:
        cases = (
            (
                "paper-scoreboard-sealed-fixture-v0.schema.json",
                ROOT / "fixtures/paper_scoreboard_sealed_fixture_v0/mixed_runner_reject.json",
                "scoreboard",
                ("dual_read", "closed_book_claim"),
            ),
            (
                "paper-scoreboard-sealed-fixture-v0.schema.json#/$defs/expectation",
                ROOT / "fixtures/paper_scoreboard_sealed_fixture_v0/sealed_day_2026-09-20_expectation.json",
                "expectation",
                ("closed_book_claim",),
            ),
            (
                "paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json",
                ROOT / "fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json",
                "batch",
                ("dual_read", "closed_book_claim"),
            ),
            (
                "paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json",
                ROOT / "fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/synthetic_replay.json",
                "receipt",
                ("dual_read", "closed_book_claim"),
            ),
        )
        for ref, path, kind, pointer in cases:
            with self.subTest(kind=kind):
                doc = _load(path)
                _set_path(doc, pointer, True)
                self.assertTrue(_schema_errors(ref, doc, self.registry))
                self.assertTrue(_cli_errors(kind, doc))

    def test_measure_kind_and_invented_returns_fail_schema_and_cli(self) -> None:
        board = _load(ROOT / "fixtures/paper_scoreboard_sealed_fixture_v0/mixed_runner_reject.json")
        board["measure"]["kind"] = "ev_lift"
        board["measure"]["invented_ev"] = True
        board["measure"]["invented_lift"] = True
        board["measure"]["claims_alpha"] = True
        board["measure"]["pass_fail_no_lift"] = True
        ref = "paper-scoreboard-sealed-fixture-v0.schema.json"
        self.assertTrue(_schema_errors(ref, board, self.registry))
        self.assertTrue(_cli_errors("scoreboard", board))

        receipt = _load(
            ROOT
            / "fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/projection_on_synthetic.json"
        )
        receipt["measure"]["kind"] = "alpha"
        receipt["honesty"]["claims_alpha"] = True
        href = "paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json"
        self.assertTrue(_schema_errors(href, receipt, self.registry))
        self.assertTrue(_cli_errors("receipt", receipt))

    def test_host_path_open_claims_fail_schema_and_cli(self) -> None:
        receipt = _load(
            ROOT
            / "fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/operator_declared.json"
        )
        receipt["input"]["var_lib_mal_opened"] = True
        receipt["honesty"]["var_lib_mal_read"] = True
        receipt["honesty"]["ci_claimed_sealed_book_close"] = True
        receipt["honesty"]["host_extract_checked_into_git"] = True
        ref = "paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json"
        self.assertTrue(_schema_errors(ref, receipt, self.registry))
        self.assertTrue(_cli_errors("receipt", receipt))

    def test_numeric_horizons_under_null_status_fail_schema_and_cli(self) -> None:
        stamp = _load(ROOT / "fixtures/paper_evaluate_hot_packet_v0/sealed_cold_graph_runner.json")
        self.assertEqual(stamp["runner_stamp"]["horizon_status"], "null_ok_no_marks_on_this_stamp")
        self.assertEqual(stamp["runner_stamp"]["delta_exec"]["status"], "null_ok")
        stamp["runner_stamp"]["horizons"]["60s"] = 0.4
        stamp["runner_stamp"]["delta_exec"]["value"] = 0
        ref = "paper-evaluate-hot-packet-v0.schema.json"
        self.assertTrue(_schema_errors(ref, stamp, self.registry))
        self.assertTrue(_cli_errors("stamp", stamp))

        board = _load(ROOT / "fixtures/paper_scoreboard_sealed_fixture_v0/graph_cold.json")
        self.assertEqual(board["horizons"]["status"], "fixture_joined_null_explicit")
        self.assertEqual(board["delta_exec"]["status"], "fixture_joined_null_explicit")
        board["horizons"]["values"]["60s"] = 1.2
        board["delta_exec"]["value"] = 0.0
        bref = "paper-scoreboard-sealed-fixture-v0.schema.json"
        self.assertTrue(_schema_errors(bref, board, self.registry))
        self.assertTrue(_cli_errors("scoreboard", board))

    def test_whole_number_float_on_integer_slot_fails_schema_and_cli(self) -> None:
        ref = "hot-packet-v0.schema.json"
        for number in (1.0, 2.0):
            with self.subTest(number=number):
                packet = _load(ROOT / "fixtures/hot_packet_v0/sealed_graph_slots_nullable.json")
                slot = packet["graph"]["slots"][0]
                self.assertEqual(slot["slot_id"], "prior_mint_count")
                self.assertIsInstance(slot["value"], int)
                slot["value"] = number
                self.assertIsInstance(slot["value"], float)
                self.assertTrue(_schema_errors(ref, packet, self.registry))
                self.assertTrue(_cli_errors("packet", packet))

    def test_unfilled_graph_cold_false_fails_schema_and_cli(self) -> None:
        ref = "hot-packet-v0.schema.json"
        null_slots = _load(ROOT / "fixtures/hot_packet_v0/sealed_create_cold_graph.json")
        self.assertIsNone(null_slots["graph"]["slots"])
        null_slots["graph"]["cold"] = False
        self.assertTrue(_schema_errors(ref, null_slots, self.registry))
        self.assertTrue(_cli_errors("packet", null_slots))

        empty = _load(ROOT / "fixtures/hot_packet_v0/sealed_create_cold_graph.json")
        empty["graph"]["slots"] = [None, None]
        empty["graph"]["cold"] = False
        self.assertTrue(_schema_errors(ref, empty, self.registry))
        self.assertTrue(_cli_errors("packet", empty))

    def test_slot_kind_and_cold_claim_fail_schema_and_cli(self) -> None:
        packet = _load(ROOT / "fixtures/hot_packet_v0/sealed_graph_slots_nullable.json")
        early = packet["graph"]["slots"][1]
        self.assertEqual(early["slot_id"], "early_wallet_dt_min_seconds")
        early["value"] = 1.5
        ref = "hot-packet-v0.schema.json"
        self.assertTrue(_schema_errors(ref, packet, self.registry))
        self.assertTrue(_cli_errors("packet", packet))

        cold = _load(ROOT / "fixtures/hot_packet_v0/sealed_graph_slots_nullable.json")
        cold["graph"]["cold"] = True
        self.assertTrue(_schema_errors(ref, cold, self.registry))
        self.assertTrue(_cli_errors("packet", cold))

    def test_graph_lift_status_must_match_cold(self) -> None:
        stamp = _load(ROOT / "fixtures/paper_evaluate_hot_packet_v0/sealed_cold_graph_runner.json")
        self.assertIs(stamp["input"]["packet"]["graph"]["cold"], True)
        stamp["graph_lift_status"] = "not_used_slots_not_scored"
        ref = "paper-evaluate-hot-packet-v0.schema.json"
        self.assertTrue(_schema_errors(ref, stamp, self.registry))
        self.assertTrue(_cli_errors("stamp", stamp))

    def test_case_variant_forbidden_names_fail_schema_and_cli(self) -> None:
        ref = "paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json"
        for key in ("Mean_Return", "EV", "burst_count", "outcome_mark"):
            with self.subTest(key=key):
                batch = _load(
                    ROOT / "fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json"
                )
                batch["input"]["days"][0]["source_rows"][0][key] = 1
                self.assertTrue(_schema_errors(ref, batch, self.registry))
                self.assertTrue(_cli_errors("batch", batch))

    def test_batch_source_row_return_key_fails_schema_and_cli(self) -> None:
        batch = _load(ROOT / "fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json")
        row = batch["input"]["days"][0]["source_rows"][0]
        row["mean_return"] = 1.5
        row["ws_payload"]["closed_book_claim"] = True
        ref = "paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json"
        self.assertTrue(_schema_errors(ref, batch, self.registry))
        self.assertTrue(_cli_errors("batch", batch))

    def test_registration_has_no_runtime_cli(self) -> None:
        self.assertFalse(
            (ROOT / "tools/paper_incomplete_rpc_honesty_schema_align_v0.py").exists()
        )
        text = OBSERVE_CLIENT.read_text(encoding="utf-8")
        self.assertNotIn("schema_align", text)
        self.assertNotIn("paper_incomplete_rpc_honesty", text)

    def test_align_does_not_copy_honest_fixtures(self) -> None:
        """Dishonest cases are in-memory copies. Git keeps the honest fixtures."""
        original = _load(ROOT / "fixtures/paper_scoreboard_sealed_fixture_v0/mixed_runner_reject.json")
        tampered = copy.deepcopy(original)
        tampered["dual_read"]["closed_book_claim"] = True
        self.assertIs(original["dual_read"]["closed_book_claim"], False)
        self.assertTrue(tampered["dual_read"]["closed_book_claim"])


if __name__ == "__main__":
    unittest.main()
