"""Schema-align proof for paper-laya-decision-packet-incomplete-rpc-honesty-schema-align-v0.

Honest checked-in decision-packet fixtures (#67–#71) must pass JSON Schema and the matching CLI.
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

from tools.paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 import (
    validate_receipt as validate_dry_run_receipt,
)
from tools.paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0 import (
    validate_batch,
)
from tools.paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0 import (
    DISHONEST_PROBES,
    _host_path_forms,
    validate_capture,
)
from tools.paper_laya_decision_packet_scoreboard_sealed_fixture_v0 import validate_scoreboard
from tools.paper_laya_precompute_decision_packet_v0 import (
    PREFIX_NON_FILL,
    validate_packet,
)

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "ARTIFACTS"
OBSERVE_CLIENT = ROOT / "observe" / "client.py"

DECISION_PACKET_SCHEMAS = (
    "paper-laya-precompute-decision-packet-v0.schema.json",
    "paper-laya-decision-packet-scoreboard-sealed-fixture-v0.schema.json",
    "paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json",
    "paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json",
    "paper-laya-decision-packet-host-local-dry-run-receipt-capture-v0.schema.json",
)

HONEST: tuple[tuple[str, Path, str], ...] = (
    (
        "paper-laya-precompute-decision-packet-v0.schema.json",
        ROOT / "fixtures/paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json",
        "packet",
    ),
    (
        "paper-laya-precompute-decision-packet-v0.schema.json",
        ROOT / "fixtures/paper_laya_precompute_decision_packet_v0/decision_fill_sim_surround.json",
        "packet",
    ),
    (
        "paper-laya-decision-packet-scoreboard-sealed-fixture-v0.schema.json",
        ROOT / "fixtures/paper_laya_decision_packet_scoreboard_sealed_fixture_v0/all_spines.json",
        "scoreboard",
    ),
    (
        "paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json",
        ROOT / "fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json",
        "batch",
    ),
    (
        "paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json",
        ROOT
        / "fixtures/paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/synthetic_replay.json",
        "dry_run_receipt",
    ),
    (
        "paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json",
        ROOT
        / "fixtures/paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/operator_declared.json",
        "dry_run_receipt",
    ),
    (
        "paper-laya-decision-packet-host-local-dry-run-receipt-capture-v0.schema.json",
        ROOT
        / "fixtures/paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0/receipt_synthetic_replay.json",
        "capture",
    ),
    (
        "paper-laya-decision-packet-host-local-dry-run-receipt-capture-v0.schema.json",
        ROOT / "fixtures/paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0/refuse_host_path.json",
        "capture",
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
    schema = _load(ART / ref)
    return Draft202012Validator(schema, registry=registry)


def _schema_errors(ref: str, doc: Any, registry: Registry) -> list[str]:
    return [err.json_path for err in _validator(ref, registry).iter_errors(doc)]


def _cli_errors(kind: str, doc: Any) -> list[str]:
    if kind == "packet":
        return validate_packet(doc)
    if kind == "scoreboard":
        return validate_scoreboard(doc)
    if kind == "batch":
        return validate_batch(doc)
    if kind == "dry_run_receipt":
        return validate_dry_run_receipt(doc)
    if kind == "capture":
        return validate_capture(doc)
    raise AssertionError(kind)


def _set_path(doc: dict[str, Any], path: tuple[Any, ...], value: Any) -> None:
    cursor: Any = doc
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value


class LayaDecisionPacketSchemaAlignV0Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = _registry()

    def test_honest_fixtures_pass_schema_and_cli(self) -> None:
        for ref, path, kind in HONEST:
            with self.subTest(path=str(path.relative_to(ROOT))):
                doc = _load(path)
                self.assertEqual(_schema_errors(ref, doc, self.registry), [])
                self.assertEqual(_cli_errors(kind, doc), [])

    def test_closed_book_and_incomplete_slice_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-decision-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json"
        doc = _load(path)
        doc = copy.deepcopy(doc)
        doc["dual_read"]["closed_book_claim"] = True
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("packet", doc))

        batch_ref = "paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json"
        batch_path = ROOT / "fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json"
        batch = copy.deepcopy(_load(batch_path))
        batch["dual_read"]["sealed_book_rpc_slice"] = "complete"
        self.assertTrue(_schema_errors(batch_ref, batch, self.registry))
        self.assertTrue(_cli_errors("batch", batch))

    def test_measure_kind_and_invented_returns_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-decision-packet-scoreboard-sealed-fixture-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_decision_packet_scoreboard_sealed_fixture_v0/all_spines.json"
        doc = copy.deepcopy(_load(path))
        doc["measure"]["kind"] = "ev_lift"
        doc["measure"]["invented_ev"] = True
        doc["measure"]["pass_fail_no_lift"] = True
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("scoreboard", doc))

    def test_laya_caps_and_risk_gate_lock_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-decision-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json"
        doc = copy.deepcopy(_load(path))
        doc["laya"]["authorize_run"] = True
        doc["risk_gate"]["unlock"] = True
        doc["risk_gate"]["decision"] = "unlocked"
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("packet", doc))

        dry_ref = (
            "paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json"
        )
        dry_path = (
            ROOT
            / "fixtures/paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/synthetic_replay.json"
        )
        dry = copy.deepcopy(_load(dry_path))
        dry["laya"]["live_trading"] = True
        self.assertTrue(_schema_errors(dry_ref, dry, self.registry))
        self.assertTrue(_cli_errors("dry_run_receipt", dry))

    def test_host_path_open_claims_fail_schema_and_cli(self) -> None:
        dry_ref = (
            "paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json"
        )
        dry_path = (
            ROOT
            / "fixtures/paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/operator_declared.json"
        )
        doc = copy.deepcopy(_load(dry_path))
        doc["honesty"]["var_lib_mal_read"] = True
        doc["input"]["var_lib_mal_opened"] = True
        self.assertTrue(_schema_errors(dry_ref, doc, self.registry))
        self.assertTrue(_cli_errors("dry_run_receipt", doc))

    def test_soft_watches_order_shuffle_and_duplicate_fail_schema_and_cli(self) -> None:
        cases: tuple[tuple[str, Path, str], ...] = (
            (
                "paper-laya-precompute-decision-packet-v0.schema.json",
                ROOT / "fixtures/paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json",
                "packet",
            ),
            (
                "paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json",
                ROOT
                / "fixtures/paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/synthetic_replay.json",
                "dry_run_receipt",
            ),
            (
                "paper-laya-decision-packet-host-local-dry-run-receipt-capture-v0.schema.json",
                ROOT
                / "fixtures/paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0/receipt_synthetic_replay.json",
                "capture",
            ),
        )
        for ref, path, kind in cases:
            doc = copy.deepcopy(_load(path))
            items = doc["soft_watches"]["items"]
            with self.subTest(ref=ref, mutation="shuffle"):
                shuffled = copy.deepcopy(doc)
                shuffled["soft_watches"]["items"] = [items[1], items[0]] + items[2:]
                self.assertTrue(_schema_errors(ref, shuffled, self.registry))
                self.assertTrue(_cli_errors(kind, shuffled))
            with self.subTest(ref=ref, mutation="duplicate"):
                duped = copy.deepcopy(doc)
                duped["soft_watches"]["items"] = [items[0]] * len(items)
                self.assertTrue(_schema_errors(ref, duped, self.registry))
                self.assertTrue(_cli_errors(kind, duped))
            with self.subTest(ref=ref, mutation="partial"):
                partial = copy.deepcopy(doc)
                partial["soft_watches"]["items"] = items[:5]
                self.assertTrue(_schema_errors(ref, partial, self.registry))
                self.assertTrue(_cli_errors(kind, partial))

    def test_soft_watch_unregistered_id_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json"
        doc = copy.deepcopy(_load(path))
        doc["soft_watches"]["items"].append("discovery_promoted_now")
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("batch", doc))

    def test_lexical_path_collapse_inside_fixture_prefix_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-decision-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json"
        base = f"{PREFIX_NON_FILL}mixed_scoreboard.json"
        for candidate in (
            base.replace("/mixed", "//mixed"),
            f"{PREFIX_NON_FILL}./mixed_scoreboard.json",
            f"{PREFIX_NON_FILL}mixed_scoreboard.json/",
        ):
            with self.subTest(path=candidate):
                doc = copy.deepcopy(_load(path))
                doc["input"]["assembly"]["surround_paths"][0] = candidate
                doc["surround_citations"][0]["fixture_path"] = candidate
                self.assertTrue(_schema_errors(ref, doc, self.registry))
                self.assertTrue(_cli_errors("packet", doc))

    def test_var_lib_mal_fixture_path_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-decision-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json"
        host = "/var/lib/mal/sealed.jsonl"
        doc = copy.deepcopy(_load(path))
        doc["input"]["assembly"]["surround_paths"][0] = host
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("packet", doc))

    def test_capture_refuse_subject_forms_probes_and_kind_coupling_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-decision-packet-host-local-dry-run-receipt-capture-v0.schema.json"
        capture_fixtures = ROOT / "fixtures/paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0"
        dishonest = copy.deepcopy(_load(capture_fixtures / "refuse_dishonest_receipt.json"))
        dishonest["subject"]["forms"] = _host_path_forms()
        self.assertTrue(_schema_errors(ref, dishonest, self.registry))
        self.assertTrue(_cli_errors("capture", dishonest))

        host_path = copy.deepcopy(_load(capture_fixtures / "refuse_host_path.json"))
        host_path["subject"]["probes"] = list(DISHONEST_PROBES)
        self.assertTrue(_schema_errors(ref, host_path, self.registry))
        self.assertTrue(_cli_errors("capture", host_path))

        wrong_kind = copy.deepcopy(dishonest)
        wrong_kind["subject"]["kind"] = "host_path_not_opened"
        self.assertTrue(_schema_errors(ref, wrong_kind, self.registry))
        self.assertTrue(_cli_errors("capture", wrong_kind))

    def test_scoreboard_forbidden_return_key_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-decision-packet-scoreboard-sealed-fixture-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_decision_packet_scoreboard_sealed_fixture_v0/all_spines.json"
        doc = copy.deepcopy(_load(path))
        doc["label_rates"]["rows"][0]["mean_return"] = 1.0
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("scoreboard", doc))

    def test_graph_revive_claims_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-decision-packet-scoreboard-sealed-fixture-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_decision_packet_scoreboard_sealed_fixture_v0/all_spines.json"
        doc = copy.deepcopy(_load(path))
        doc["honesty"]["graph_lane_revived"] = True
        doc["graph_lift"] = 1.0
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("scoreboard", doc))

    def test_registration_has_no_runtime_cli(self) -> None:
        self.assertFalse(
            (ROOT / "tools/paper_laya_decision_packet_incomplete_rpc_honesty_schema_align_v0.py").exists()
        )
        text = OBSERVE_CLIENT.read_text(encoding="utf-8")
        self.assertNotIn("decision_packet_incomplete_rpc_honesty", text)

    def test_decision_packet_schemas_are_in_the_align_set(self) -> None:
        for name in DECISION_PACKET_SCHEMAS:
            self.assertTrue((ART / name).is_file(), msg=name)


if __name__ == "__main__":
    unittest.main()
