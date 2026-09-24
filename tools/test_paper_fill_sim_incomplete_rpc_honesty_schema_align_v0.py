"""Schema-align proof for paper-fill-sim-incomplete-rpc-honesty-schema-align-v0.

Honest checked-in fill-sim fixtures must pass JSON Schema and the matching CLI.
Dishonest copies must fail both. No RPC. No host read. observe/client.py is not imported.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 import (
    validate_receipt,
)
from tools.paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0 import validate_batch
from tools.paper_fill_sim_hot_packet_evaluate_v0 import validate_fill_sim_stamp
from tools.paper_fill_sim_scoreboard_sealed_fixture_v0 import (
    validate_expectation,
    validate_scoreboard,
)

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "ARTIFACTS"
OBSERVE_CLIENT = ROOT / "observe" / "client.py"

FILL_SIM_SCHEMAS = (
    "paper-fill-sim-hot-packet-evaluate-v0.schema.json",
    "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json",
    "paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json",
    "paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json",
)

HONEST: tuple[tuple[str, Path, str], ...] = (
    (
        "paper-fill-sim-hot-packet-evaluate-v0.schema.json",
        ROOT / "fixtures/paper_fill_sim_hot_packet_evaluate_v0/sealed_cold_runner.json",
        "fill_sim_stamp",
    ),
    (
        "paper-fill-sim-hot-packet-evaluate-v0.schema.json",
        ROOT / "fixtures/paper_fill_sim_hot_packet_evaluate_v0/enriched_fee_runner.json",
        "fill_sim_stamp",
    ),
    (
        "paper-fill-sim-hot-packet-evaluate-v0.schema.json",
        ROOT / "fixtures/paper_fill_sim_hot_packet_evaluate_v0/enriched_launchlab_runner.json",
        "fill_sim_stamp",
    ),
    (
        "paper-fill-sim-hot-packet-evaluate-v0.schema.json",
        ROOT / "fixtures/paper_fill_sim_hot_packet_evaluate_v0/graph_slots_not_scored.json",
        "fill_sim_stamp",
    ),
    (
        "paper-fill-sim-hot-packet-evaluate-v0.schema.json",
        ROOT / "fixtures/paper_fill_sim_hot_packet_evaluate_v0/reject_missing_identity.json",
        "fill_sim_stamp",
    ),
    (
        "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json",
        ROOT / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/all_runner.json",
        "scoreboard",
    ),
    (
        "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json",
        ROOT / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/mixed_runner_reject.json",
        "scoreboard",
    ),
    (
        "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json",
        ROOT / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/identity_reject.json",
        "scoreboard",
    ),
    (
        "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json",
        ROOT / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/graph_cold.json",
        "scoreboard",
    ),
    (
        "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json",
        ROOT / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/slots_not_scored.json",
        "scoreboard",
    ),
    (
        "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json#/$defs/expectation",
        ROOT
        / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/sealed_day_2026-09-20_expectation.json",
        "expectation",
    ),
    (
        "paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json",
        ROOT / "fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json",
        "batch",
    ),
    (
        "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json#/$defs/expectation",
        ROOT
        / "fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-20_expectation.json",
        "expectation",
    ),
    (
        "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json#/$defs/expectation",
        ROOT
        / "fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-21_expectation.json",
        "expectation",
    ),
    (
        "paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json",
        ROOT
        / "fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/synthetic_replay.json",
        "receipt",
    ),
    (
        "paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json",
        ROOT
        / "fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/projection_on_synthetic.json",
        "receipt",
    ),
    (
        "paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json",
        ROOT
        / "fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/operator_declared.json",
        "receipt",
    ),
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> Registry:
    resources = []
    for path in sorted(ART.glob("*.schema.json")):
        schema = _load(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def _validator(ref: str, registry: Registry) -> Draft202012Validator:
    if "#/" in ref:
        name, pointer = ref.split("#", 1)
        schema = {"$ref": f"{name}#{pointer}"}
    else:
        schema = _load(ART / ref)
    return Draft202012Validator(schema, registry=registry)


def _schema_errors(ref: str, doc: Any, registry: Registry) -> list[str]:
    return [err.json_path for err in _validator(ref, registry).iter_errors(doc)]


def _cli_errors(kind: str, doc: Any) -> list[str]:
    if kind == "fill_sim_stamp":
        return validate_fill_sim_stamp(doc)
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


class FillSimSchemaAlignV0Tests(unittest.TestCase):
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
            / "fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/operator_declared.json"
        )
        joined = " ".join(receipt["invocation"]["jsonl"] + receipt["invocation"]["expectation"])
        self.assertIn("/var/lib/mal", joined)
        self.assertIs(receipt["input"]["var_lib_mal_opened"], False)
        self.assertIs(receipt["honesty"]["var_lib_mal_read"], False)
        self.assertIs(receipt["dual_read"]["closed_book_claim"], False)
        self.assertEqual(receipt["measure"]["kind"], "none")

    def test_closed_book_and_incomplete_slice_fail_schema_and_cli(self) -> None:
        stamp = _load(
            ROOT / "fixtures/paper_fill_sim_hot_packet_evaluate_v0/sealed_cold_runner.json"
        )
        for pointer, value in (
            (("dual_read", "closed_book_claim"), True),
            (("dual_read", "sealed_book_rpc_slice"), "complete"),
        ):
            with self.subTest(pointer=pointer):
                doc = copy.deepcopy(stamp)
                _set_path(doc, pointer, value)
                ref = "paper-fill-sim-hot-packet-evaluate-v0.schema.json"
                self.assertTrue(_schema_errors(ref, doc, self.registry))
                self.assertTrue(_cli_errors("fill_sim_stamp", doc))

        cases = (
            (
                "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json",
                ROOT / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/mixed_runner_reject.json",
                "scoreboard",
                ("dual_read", "closed_book_claim"),
                True,
            ),
            (
                "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json#/$defs/expectation",
                ROOT
                / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/sealed_day_2026-09-20_expectation.json",
                "expectation",
                ("closed_book_claim",),
                True,
            ),
            (
                "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json#/$defs/expectation",
                ROOT
                / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/sealed_day_2026-09-20_expectation.json",
                "expectation",
                ("sealed_book_rpc_slice",),
                "complete",
            ),
            (
                "paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json",
                ROOT / "fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json",
                "batch",
                ("dual_read", "closed_book_claim"),
                True,
            ),
            (
                "paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json",
                ROOT
                / "fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/synthetic_replay.json",
                "receipt",
                ("dual_read", "closed_book_claim"),
                True,
            ),
        )
        for ref, path, kind, pointer, value in cases:
            with self.subTest(kind=kind, pointer=pointer):
                doc = _load(path)
                _set_path(doc, pointer, value)
                self.assertTrue(_schema_errors(ref, doc, self.registry))
                self.assertTrue(_cli_errors(kind, doc))

    def test_measure_kind_and_invented_returns_fail_schema_and_cli(self) -> None:
        board = _load(
            ROOT / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/mixed_runner_reject.json"
        )
        board["measure"]["kind"] = "ev_lift"
        board["measure"]["invented_ev"] = True
        board["measure"]["invented_lift"] = True
        board["measure"]["claims_alpha"] = True
        board["measure"]["pass_fail_no_lift"] = True
        ref = "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json"
        self.assertTrue(_schema_errors(ref, board, self.registry))
        self.assertTrue(_cli_errors("scoreboard", board))

        receipt = _load(
            ROOT
            / "fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/projection_on_synthetic.json"
        )
        receipt["measure"]["kind"] = "alpha"
        receipt["honesty"]["claims_alpha"] = True
        href = "paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json"
        self.assertTrue(_schema_errors(href, receipt, self.registry))
        self.assertTrue(_cli_errors("receipt", receipt))

    def test_host_path_open_claims_fail_schema_and_cli(self) -> None:
        receipt = _load(
            ROOT
            / "fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/operator_declared.json"
        )
        receipt["input"]["var_lib_mal_opened"] = True
        receipt["honesty"]["var_lib_mal_read"] = True
        receipt["honesty"]["ci_claimed_sealed_book_close"] = True
        receipt["honesty"]["host_extract_checked_into_git"] = True
        ref = "paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json"
        self.assertTrue(_schema_errors(ref, receipt, self.registry))
        self.assertTrue(_cli_errors("receipt", receipt))

    def test_numeric_horizons_and_delta_exec_fail_schema_and_cli(self) -> None:
        stamp = _load(
            ROOT / "fixtures/paper_fill_sim_hot_packet_evaluate_v0/sealed_cold_runner.json"
        )
        stamp["horizons"]["values"]["60s"] = 0.4
        stamp["delta_exec"]["value"] = 0
        ref = "paper-fill-sim-hot-packet-evaluate-v0.schema.json"
        self.assertTrue(_schema_errors(ref, stamp, self.registry))
        self.assertTrue(_cli_errors("fill_sim_stamp", stamp))

        board = _load(ROOT / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/graph_cold.json")
        board["horizons"]["values"]["60s"] = 1.2
        board["delta_exec"]["value"] = 0.0
        bref = "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json"
        self.assertTrue(_schema_errors(bref, board, self.registry))
        self.assertTrue(_cli_errors("scoreboard", board))

    def test_fill_sim_outcomes_stay_null_on_stamp(self) -> None:
        stamp = _load(
            ROOT / "fixtures/paper_fill_sim_hot_packet_evaluate_v0/sealed_cold_runner.json"
        )
        stamp["fill_sim"]["fee_sol"] = 0.01
        ref = "paper-fill-sim-hot-packet-evaluate-v0.schema.json"
        self.assertTrue(_schema_errors(ref, stamp, self.registry))
        self.assertTrue(_cli_errors("fill_sim_stamp", stamp))

    def test_dec007_arm_rows_fail_schema_and_cli(self) -> None:
        board = _load(
            ROOT / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/mixed_runner_reject.json"
        )
        board["fill_sim_status_counts"] = [board["fill_sim_status_counts"][0]]
        ref = "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json"
        self.assertTrue(_schema_errors(ref, board, self.registry))
        self.assertTrue(_cli_errors("scoreboard", board))

        board = _load(
            ROOT / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/mixed_runner_reject.json"
        )
        board["label_rates"]["rows"] = [board["label_rates"]["rows"][0]]
        self.assertTrue(_schema_errors(ref, board, self.registry))
        self.assertTrue(_cli_errors("scoreboard", board))

        batch = _load(
            ROOT / "fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json"
        )
        batch["rollup"]["rows"] = [batch["rollup"]["rows"][0]]
        bref = "paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json"
        self.assertTrue(_schema_errors(bref, batch, self.registry))
        self.assertTrue(_cli_errors("batch", batch))

        batch = _load(
            ROOT / "fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json"
        )
        day_board = batch["days"][0]["scoreboard"]
        day_board["fill_sim_status_counts"] = [day_board["fill_sim_status_counts"][0]]
        self.assertTrue(_schema_errors(bref, batch, self.registry))
        self.assertTrue(_cli_errors("batch", batch))

        receipt = _load(
            ROOT
            / "fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/synthetic_replay.json"
        )
        receipt["parent_digest"]["fill_sim_status_arms"] = [
            receipt["parent_digest"]["fill_sim_status_arms"][0]
        ]
        rref = "paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json"
        self.assertTrue(_schema_errors(rref, receipt, self.registry))
        self.assertTrue(_cli_errors("receipt", receipt))

    def test_count_share_is_not_a_return_field(self) -> None:
        board = _load(
            ROOT / "fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/mixed_runner_reject.json"
        )
        board["label_rates"]["rows"][0]["mean_return"] = 1.0
        ref = "paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json"
        self.assertTrue(_schema_errors(ref, board, self.registry))
        self.assertTrue(_cli_errors("scoreboard", board))

    def test_graph_lift_status_must_match_embedded_packet_cold(self) -> None:
        cold = _load(
            ROOT / "fixtures/paper_fill_sim_hot_packet_evaluate_v0/sealed_cold_runner.json"
        )
        self.assertIs(cold["input"]["stamp"]["input"]["packet"]["graph"]["cold"], True)
        cold["graph_lift_status"] = "not_used_slots_not_scored"
        ref = "paper-fill-sim-hot-packet-evaluate-v0.schema.json"
        self.assertTrue(_schema_errors(ref, cold, self.registry))
        self.assertTrue(_cli_errors("fill_sim_stamp", cold))

        slots = _load(
            ROOT / "fixtures/paper_fill_sim_hot_packet_evaluate_v0/graph_slots_not_scored.json"
        )
        self.assertIs(slots["input"]["stamp"]["input"]["packet"]["graph"]["cold"], False)
        slots["graph_lift_status"] = "not_used_graph_cold"
        self.assertTrue(_schema_errors(ref, slots, self.registry))
        self.assertTrue(_cli_errors("fill_sim_stamp", slots))

    def test_case_variant_forbidden_names_fail_schema_and_cli(self) -> None:
        ref = "paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json"
        for key in ("Mean_Return", "EV", "burst_count", "outcome_mark"):
            with self.subTest(key=key):
                batch = _load(
                    ROOT
                    / "fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json"
                )
                batch["input"]["days"][0]["source_rows"][0][key] = 1
                self.assertTrue(_schema_errors(ref, batch, self.registry))
                self.assertTrue(_cli_errors("batch", batch))

    def test_batch_source_row_return_key_fails_schema_and_cli(self) -> None:
        batch = _load(
            ROOT / "fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json"
        )
        row = batch["input"]["days"][0]["source_rows"][0]
        row["mean_return"] = 1.5
        row["ws_payload"]["closed_book_claim"] = True
        ref = "paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json"
        self.assertTrue(_schema_errors(ref, batch, self.registry))
        self.assertTrue(_cli_errors("batch", batch))

    def test_registration_has_no_runtime_cli(self) -> None:
        self.assertFalse(
            (ROOT / "tools/paper_fill_sim_incomplete_rpc_honesty_schema_align_v0.py").exists()
        )
        text = OBSERVE_CLIENT.read_text(encoding="utf-8")
        self.assertNotIn("fill_sim_incomplete_rpc_honesty", text)
        self.assertNotIn("fill_sim_incomplete_rpc_honesty_schema_align", text)

    def test_fill_sim_schemas_are_in_the_align_set(self) -> None:
        for name in FILL_SIM_SCHEMAS:
            self.assertTrue((ART / name).is_file(), msg=name)


if __name__ == "__main__":
    unittest.main()
