"""Schema-align proof for paper-laya-incomplete-rpc-honesty-schema-align-v0.

Honest checked-in LAYA fixtures (#63–#65) must pass JSON Schema and the matching CLI.
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

from tools.paper_laya_precompute_fill_sim_surround_packet_v0 import validate_surround as validate_fill_sim_surround
from tools.paper_laya_precompute_surround_packet_v0 import validate_surround as validate_non_fill_surround
from tools.paper_laya_risk_gate_lock_receipt_v0 import validate_receipt

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "ARTIFACTS"
OBSERVE_CLIENT = ROOT / "observe" / "client.py"

LAYA_SCHEMAS = (
    "paper-laya-precompute-fill-sim-surround-packet-v0.schema.json",
    "paper-laya-precompute-surround-packet-v0.schema.json",
    "paper-laya-risk-gate-lock-receipt-v0.schema.json",
)

HONEST: tuple[tuple[str, Path, str], ...] = (
    (
        "paper-laya-precompute-surround-packet-v0.schema.json",
        ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json",
        "non_fill_surround",
    ),
    (
        "paper-laya-precompute-surround-packet-v0.schema.json",
        ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/batch_two_day_with_digest.json",
        "non_fill_surround",
    ),
    (
        "paper-laya-precompute-fill-sim-surround-packet-v0.schema.json",
        ROOT / "fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/mixed_scoreboard.json",
        "fill_sim_surround",
    ),
    (
        "paper-laya-precompute-fill-sim-surround-packet-v0.schema.json",
        ROOT
        / "fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/batch_two_day_with_digest.json",
        "fill_sim_surround",
    ),
    (
        "paper-laya-risk-gate-lock-receipt-v0.schema.json",
        ROOT / "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_non_fill_sim_surround.json",
        "lock_receipt",
    ),
    (
        "paper-laya-risk-gate-lock-receipt-v0.schema.json",
        ROOT / "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_fill_sim_surround.json",
        "lock_receipt",
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
    if kind == "non_fill_surround":
        return validate_non_fill_surround(doc)
    if kind == "fill_sim_surround":
        return validate_fill_sim_surround(doc)
    if kind == "lock_receipt":
        return validate_receipt(doc)
    raise AssertionError(kind)


def _set_path(doc: dict[str, Any], path: tuple[Any, ...], value: Any) -> None:
    cursor: Any = doc
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value


class LayaSchemaAlignV0Tests(unittest.TestCase):
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
        cases: tuple[tuple[str, Path, str, tuple[Any, ...], Any], ...] = (
            (
                "paper-laya-precompute-surround-packet-v0.schema.json",
                ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json",
                "non_fill_surround",
                ("dual_read", "closed_book_claim"),
                True,
            ),
            (
                "paper-laya-precompute-surround-packet-v0.schema.json",
                ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json",
                "non_fill_surround",
                ("dual_read", "sealed_book_rpc_slice"),
                "complete",
            ),
            (
                "paper-laya-precompute-fill-sim-surround-packet-v0.schema.json",
                ROOT
                / "fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/mixed_scoreboard.json",
                "fill_sim_surround",
                ("dual_read", "closed_book_claim"),
                True,
            ),
            (
                "paper-laya-risk-gate-lock-receipt-v0.schema.json",
                ROOT / "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_non_fill_sim_surround.json",
                "lock_receipt",
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
        for ref, path, kind in (
            (
                "paper-laya-precompute-surround-packet-v0.schema.json",
                ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json",
                "non_fill_surround",
            ),
            (
                "paper-laya-precompute-fill-sim-surround-packet-v0.schema.json",
                ROOT
                / "fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/mixed_scoreboard.json",
                "fill_sim_surround",
            ),
            (
                "paper-laya-risk-gate-lock-receipt-v0.schema.json",
                ROOT / "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_non_fill_sim_surround.json",
                "lock_receipt",
            ),
        ):
            with self.subTest(ref=ref):
                doc = _load(path)
                doc["measure"]["kind"] = "ev_lift"
                doc["measure"]["invented_ev"] = True
                doc["measure"]["invented_lift"] = True
                doc["measure"]["claims_alpha"] = True
                doc["measure"]["pass_fail_no_lift"] = True
                self.assertTrue(_schema_errors(ref, doc, self.registry))
                self.assertTrue(_cli_errors(kind, doc))

    def test_top_level_forbidden_return_keys_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-surround-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json"
        for key in ("mean_return", "EV", "invented_ev"):
            with self.subTest(key=key):
                doc = _load(path)
                doc[key] = 1.0
                self.assertTrue(_schema_errors(ref, doc, self.registry))
                self.assertTrue(_cli_errors("non_fill_surround", doc))

    def test_numeric_horizons_and_delta_exec_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-surround-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json"
        doc = _load(path)
        doc["horizons"]["values"]["60s"] = 0.5
        doc["delta_exec"]["value"] = 0
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", doc))

        fs_ref = "paper-laya-precompute-fill-sim-surround-packet-v0.schema.json"
        fs_path = ROOT / "fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/mixed_scoreboard.json"
        fs_doc = _load(fs_path)
        fs_doc["precompute"]["horizons"]["values"]["30s"] = 1.0
        fs_doc["precompute"]["delta_exec"]["value"] = 0.0
        self.assertTrue(_schema_errors(fs_ref, fs_doc, self.registry))
        self.assertTrue(_cli_errors("fill_sim_surround", fs_doc))

    def test_laya_caps_and_risk_gate_lock_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-risk-gate-lock-receipt-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_non_fill_sim_surround.json"
        doc = _load(path)
        doc["laya"]["authorize_run"] = True
        doc["laya"]["risk_gate_unlock"] = True
        doc["laya"]["live_trading"] = True
        doc["risk_gate"]["unlock"] = True
        doc["risk_gate"]["decision"] = "unlocked"
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("lock_receipt", doc))

        surround = _load(ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json")
        surround["laya"]["authorize_run"] = True
        sref = "paper-laya-precompute-surround-packet-v0.schema.json"
        self.assertTrue(_schema_errors(sref, surround, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", surround))

    def test_surround_body_embedded_and_carries_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-risk-gate-lock-receipt-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_fill_sim_surround.json"
        doc = _load(path)
        doc["carries"]["surround_body"] = True
        doc["surround_citations"][0]["surround_body_embedded"] = True
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("lock_receipt", doc))

    def test_graph_revive_claims_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-surround-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json"
        doc = _load(path)
        doc["honesty"]["graph_lane_revived"] = True
        doc["graph_policy"] = "warm"
        doc["graph_lift"] = 1.5
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", doc))

        doc = _load(path)
        doc["scoreboard_digests"][0]["graph_lift_aggregate_status"] = "graph_scored_lift"
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", doc))

    def test_graph_lift_aggregate_status_in_enum_swap_fail_schema_and_cli(self) -> None:
        batch_ref = "paper-laya-precompute-surround-packet-v0.schema.json"
        batch_path = ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/batch_two_day_with_digest.json"
        batch_doc = _load(batch_path)
        batch_doc["scoreboard_digests"][0]["graph_lift_aggregate_status"] = "not_scored_mixed"
        self.assertTrue(_schema_errors(batch_ref, batch_doc, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", batch_doc))

        mixed_path = ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json"
        mixed_doc = _load(mixed_path)
        mixed_doc["scoreboard_digests"][0]["graph_lift_aggregate_status"] = "not_used_graph_cold"
        self.assertTrue(_schema_errors(batch_ref, mixed_doc, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", mixed_doc))

        fs_ref = "paper-laya-precompute-fill-sim-surround-packet-v0.schema.json"
        fs_batch = _load(
            ROOT / "fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/batch_two_day_with_digest.json"
        )
        fs_batch["scoreboard_digests"][0]["graph_lift_aggregate_status"] = "not_scored_mixed"
        self.assertTrue(_schema_errors(fs_ref, fs_batch, self.registry))
        self.assertTrue(_cli_errors("fill_sim_surround", fs_batch))

        fs_mixed = _load(
            ROOT / "fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/mixed_scoreboard.json"
        )
        fs_mixed["scoreboard_digests"][0]["graph_lift_aggregate_status"] = "not_used_graph_cold"
        self.assertTrue(_schema_errors(fs_ref, fs_mixed, self.registry))
        self.assertTrue(_cli_errors("fill_sim_surround", fs_mixed))

    def test_lock_receipt_host_paths_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-risk-gate-lock-receipt-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_fill_sim_surround.json"
        host = "/var/lib/mal/sealed.jsonl"
        doc = _load(path)
        doc["surround_citations"][0]["fixture_path"] = host
        doc["input"]["assembly"]["surround_paths"] = [host]
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("lock_receipt", doc))

    def test_non_fill_citation_fill_sim_status_counts_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-risk-gate-lock-receipt-v0.schema.json"
        non_fill = _load(
            ROOT / "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_non_fill_sim_surround.json"
        )
        fill_sim = _load(
            ROOT / "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_fill_sim_surround.json"
        )
        non_fill["surround_citations"][0]["digest"]["fill_sim_status_counts"] = fill_sim[
            "surround_citations"
        ][0]["digest"]["fill_sim_status_counts"]
        self.assertTrue(_schema_errors(ref, non_fill, self.registry))
        self.assertTrue(_cli_errors("lock_receipt", non_fill))

    def test_host_path_open_claims_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-surround-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json"
        doc = _load(path)
        doc["input"]["host_jsonl_read"] = True
        doc["input"]["rpc"] = True
        doc["input"]["marks_joined"] = True
        doc["honesty"]["var_lib_mal_read"] = True
        doc["honesty"]["host_extract_checked_into_git"] = True
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", doc))

    def test_soft_watch_items_must_match_registration_enum(self) -> None:
        ref = "paper-laya-precompute-surround-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json"
        doc = _load(path)
        doc["soft_watches"]["items"].append("not_a_registered_watch")
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", doc))

        doc = _load(path)
        doc["soft_watches"]["items"][0] = "discovery_promoted_now"
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", doc))

    def test_fill_sim_status_arm_rows_on_lock_receipt_digest_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-risk-gate-lock-receipt-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_fill_sim_surround.json"
        doc = _load(path)
        doc["surround_citations"][0]["digest"]["fill_sim_status_counts"] = doc["surround_citations"][0][
            "digest"
        ]["fill_sim_status_counts"][:1]
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("lock_receipt", doc))

    def test_fill_sim_status_counts_omitted_on_lock_receipt_digest_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-risk-gate-lock-receipt-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_fill_sim_surround.json"
        doc = _load(path)
        del doc["surround_citations"][0]["digest"]["fill_sim_status_counts"]
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("lock_receipt", doc))

    def test_partial_soft_watches_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-surround-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json"
        doc = _load(path)
        doc["soft_watches"]["items"] = doc["soft_watches"]["items"][:1]
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", doc))

        fs_ref = "paper-laya-precompute-fill-sim-surround-packet-v0.schema.json"
        fs_path = ROOT / "fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/mixed_scoreboard.json"
        fs_doc = _load(fs_path)
        fs_doc["soft_watches"]["items"] = fs_doc["soft_watches"]["items"][:1]
        self.assertTrue(_schema_errors(fs_ref, fs_doc, self.registry))
        self.assertTrue(_cli_errors("fill_sim_surround", fs_doc))

        lr_ref = "paper-laya-risk-gate-lock-receipt-v0.schema.json"
        lr_path = ROOT / "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_non_fill_sim_surround.json"
        lr_doc = _load(lr_path)
        lr_doc["soft_watches"]["items"] = lr_doc["soft_watches"]["items"][:1]
        self.assertTrue(_schema_errors(lr_ref, lr_doc, self.registry))
        self.assertTrue(_cli_errors("lock_receipt", lr_doc))

    def test_stamp_bodies_not_embedded_on_surround_digest(self) -> None:
        ref = "paper-laya-precompute-surround-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json"
        doc = _load(path)
        doc["scoreboard_digests"][0]["stamp_bodies_embedded"] = True
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", doc))

    def test_batch_rollup_label_row_return_key_fails_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-surround-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/batch_two_day_with_digest.json"
        doc = _load(path)
        doc["batch_digest"]["rollup"]["rows"][0]["lift_vs_random"] = 1.0
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", doc))

    def test_fill_sim_surround_soft_watches_enum_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-fill-sim-surround-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/mixed_scoreboard.json"
        doc = _load(path)
        doc["soft_watches"]["items"].append("exp006_promoted_now")
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("fill_sim_surround", doc))

    def test_precompute_features_ready_stays_false(self) -> None:
        for ref, path, kind in (
            (
                "paper-laya-precompute-surround-packet-v0.schema.json",
                ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json",
                "non_fill_surround",
            ),
            (
                "paper-laya-precompute-fill-sim-surround-packet-v0.schema.json",
                ROOT
                / "fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/mixed_scoreboard.json",
                "fill_sim_surround",
            ),
        ):
            with self.subTest(ref=ref):
                doc = _load(path)
                doc["precompute"]["features_ready"] = True
                doc["precompute"]["graph_scores_ready"] = True
                self.assertTrue(_schema_errors(ref, doc, self.registry))
                self.assertTrue(_cli_errors(kind, doc))

    def test_lock_receipt_carries_flags_stay_false(self) -> None:
        ref = "paper-laya-risk-gate-lock-receipt-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_non_fill_sim_surround.json"
        for key in ("host_bytes", "horizons", "delta_exec"):
            with self.subTest(key=key):
                doc = _load(path)
                doc["carries"][key] = True
                self.assertTrue(_schema_errors(ref, doc, self.registry))
                self.assertTrue(_cli_errors("lock_receipt", doc))

    def test_nested_digest_closed_book_fails_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-surround-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json"
        doc = _load(path)
        doc["scoreboard_digests"][0]["dual_read"]["closed_book_claim"] = True
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", doc))

    def test_fixture_join_closed_book_flag_fails_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-surround-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json"
        doc = _load(path)
        doc["scoreboard_digests"][0]["fixture_join"]["closed_book"] = True
        self.assertTrue(_schema_errors(ref, doc, self.registry))
        self.assertTrue(_cli_errors("non_fill_surround", doc))

    def test_case_variant_forbidden_names_fail_schema_and_cli(self) -> None:
        ref = "paper-laya-precompute-fill-sim-surround-packet-v0.schema.json"
        path = ROOT / "fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/mixed_scoreboard.json"
        for key in ("Mean_Return", "burst_count", "alpha"):
            with self.subTest(key=key):
                doc = _load(path)
                doc["label_rates"]["rows"][0][key] = 1
                self.assertTrue(_schema_errors(ref, doc, self.registry))
                self.assertTrue(_cli_errors("fill_sim_surround", doc))

    def test_registration_has_no_runtime_cli(self) -> None:
        self.assertFalse(
            (ROOT / "tools/paper_laya_incomplete_rpc_honesty_schema_align_v0.py").exists()
        )
        text = OBSERVE_CLIENT.read_text(encoding="utf-8")
        self.assertNotIn("laya_incomplete_rpc_honesty", text)
        self.assertNotIn("laya_incomplete_rpc_honesty_schema_align", text)

    def test_laya_schemas_are_in_the_align_set(self) -> None:
        for name in LAYA_SCHEMAS:
            self.assertTrue((ART / name).is_file(), msg=name)


if __name__ == "__main__":
    unittest.main()
