"""Offline tests for paper-evaluate-hot-packet-v0 fixtures. No network."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.hot_packet_v0 import example_enriched_global_95bps, example_sealed_cold
from tools.paper_evaluate_hot_packet_v0 import (
    EXAMPLES,
    GRAPH_LIFT_COLD,
    GRAPH_LIFT_SLOTS,
    HORIZONS,
    SOFT_WATCH_ITEMS,
    example_enriched_fee_runner,
    example_enriched_launchlab_runner,
    example_graph_slots_not_scored,
    example_reject_missing_mint,
    example_sealed_cold_runner,
    evaluate_reasons,
    main,
    stamp_from_packet,
    validate_stamp,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "paper_evaluate_hot_packet_v0"
HOT_FIXTURES = ROOT / "fixtures" / "hot_packet_v0"
SCHEMA = ROOT / "ARTIFACTS" / "paper-evaluate-hot-packet-v0.schema.json"

FIXTURE_NAMES = {
    "sealed-cold-runner": "sealed_cold_graph_runner.json",
    "enriched-fee-runner": "enriched_global_95bps_runner.json",
    "enriched-launchlab-runner": "enriched_launchlab_init_runner.json",
    "graph-slots-not-scored": "sealed_graph_slots_not_scored_runner.json",
    "reject-missing-identity": "reject_missing_identity.json",
}


class PaperEvaluateHotPacketV0Tests(unittest.TestCase):
    def test_examples_validate(self) -> None:
        for name, builder in EXAMPLES.items():
            with self.subTest(name=name):
                self.assertEqual(validate_stamp(builder()), [])

    def test_fixtures_match_examples_and_validate(self) -> None:
        for which, filename in FIXTURE_NAMES.items():
            with self.subTest(filename=filename):
                payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
                self.assertEqual(payload, EXAMPLES[which]())
                self.assertEqual(validate_stamp(payload), [])
                self.assertEqual(main(["validate", str(FIXTURES / filename)]), 0)

    def test_schema_file_is_json(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["title"], "MAL paper-evaluate-hot-packet v0 (Proposed paper stamp)")
        self.assertEqual(schema["$defs"]["input"]["properties"]["packet"]["$ref"], "hot-packet-v0.schema.json")
        self.assertIn("null_ok", json.dumps(schema["$defs"]["deltaExec"]))
        for item in SOFT_WATCH_ITEMS:
            self.assertIn(item, schema["$defs"]["softWatches"]["properties"]["items"]["items"]["enum"])

    def test_example_cli(self) -> None:
        self.assertEqual(main(["example", "--which", "sealed-cold-runner"]), 0)

    def test_evaluate_cli_on_hot_packet_fixture(self) -> None:
        path = HOT_FIXTURES / "sealed_create_cold_graph.json"
        self.assertEqual(main(["evaluate", str(path)]), 0)

    def test_evaluate_cli_rejects_a_stamp_file(self) -> None:
        path = FIXTURES / "sealed_cold_graph_runner.json"
        self.assertEqual(main(["evaluate", str(path)]), 1)

    def test_sealed_cold_is_runner_without_lift(self) -> None:
        stamp = example_sealed_cold_runner()
        self.assertEqual(stamp["evaluate_label"], "runner")
        self.assertEqual(stamp["reasons"], [])
        self.assertIsNone(stamp["graph_lift"])
        self.assertEqual(stamp["graph_lift_status"], GRAPH_LIFT_COLD)
        self.assertTrue(stamp["runner_stamp"]["pretend_buy"])
        self.assertEqual(stamp["runner_stamp"]["kind"], "paper_promotion")
        self.assertEqual(stamp["runner_stamp"]["T"], stamp["input"]["packet"]["clocks"]["T_decision"])
        self.assertTrue(stamp["full_book"]["arm_retained"])
        for name in HORIZONS:
            self.assertIsNone(stamp["runner_stamp"]["horizons"][name])
        self.assertEqual(stamp["runner_stamp"]["delta_exec"]["status"], "null_ok")
        self.assertIsNone(stamp["runner_stamp"]["delta_exec"]["value"])
        self.assertEqual(stamp["runner_stamp"]["delta_exec"]["also_called"], "Δ_exec")

    def test_enriched_fee_stays_proposed_and_runner(self) -> None:
        stamp = example_enriched_fee_runner()
        packet = stamp["input"]["packet"]
        self.assertEqual(stamp["evaluate_label"], "runner")
        self.assertEqual(packet["regime"]["components"]["fee"], "global_95bps")
        self.assertEqual(packet["regime"]["component_lock"]["fee"], "proposed")
        self.assertEqual(packet["dual_read"]["overlay"], "enriched")
        self.assertFalse(stamp["honesty"]["enum_production_lock"])

    def test_launchlab_shape_is_runner_not_a_score(self) -> None:
        stamp = example_enriched_launchlab_runner()
        packet = stamp["input"]["packet"]
        self.assertEqual(stamp["evaluate_label"], "runner")
        self.assertEqual(packet["regime"]["components"]["instr"], "launchlab_init")
        self.assertEqual(packet["regime"]["component_lock"]["instr"], "proposed")
        self.assertEqual(packet["regime"]["component_lock"]["venue"], "proposed")
        self.assertEqual(packet["regime"]["component_lock"]["market"], "proposed")
        self.assertEqual(packet["regime"]["components"]["fee"], "unverified")
        self.assertIsNone(stamp["graph_lift"])

    def test_filled_slot_does_not_score(self) -> None:
        stamp = example_graph_slots_not_scored()
        self.assertEqual(stamp["evaluate_label"], "runner")
        self.assertEqual(stamp["reasons"], [])
        self.assertIsNone(stamp["graph_lift"])
        self.assertEqual(stamp["graph_lift_status"], GRAPH_LIFT_SLOTS)
        self.assertFalse(stamp["input"]["packet"]["graph"]["cold"])

    def test_missing_mint_reject_keeps_the_arm(self) -> None:
        stamp = example_reject_missing_mint()
        self.assertEqual(stamp["evaluate_label"], "reject")
        self.assertEqual(stamp["reasons"], ["missing_mint"])
        self.assertFalse(stamp["runner_stamp"]["pretend_buy"])
        self.assertEqual(stamp["runner_stamp"]["kind"], "reject_arm_retained")
        self.assertTrue(stamp["full_book"]["arm_retained"])
        self.assertFalse(stamp["full_book"]["deletes_detect_history"])
        self.assertEqual(stamp["runner_stamp"]["delta_exec"]["status"], "null_ok")

    def test_unk_signature_is_missing_identity(self) -> None:
        packet = example_sealed_cold()
        packet["l1_spine"]["signature"] = "UNK"
        packet["provenance"]["parent_signature"] = "UNK"
        stamp, errors = stamp_from_packet(packet)
        self.assertEqual(errors, [])
        assert stamp is not None
        self.assertEqual(stamp["reasons"], ["missing_signature"])
        self.assertEqual(stamp["evaluate_label"], "reject")

    def test_decision_clock_copies_t_event(self) -> None:
        packet = example_sealed_cold()
        t_event = "2026-09-20T11:59:59.000+00:00"
        packet["clocks"]["t_event"] = t_event
        packet["clocks"]["T_decision"] = t_event
        packet["clocks"]["t_precompute_as_of"] = t_event
        stamp, errors = stamp_from_packet(packet)
        self.assertEqual(errors, [])
        assert stamp is not None
        self.assertEqual(stamp["runner_stamp"]["T"], t_event)

    def test_sealed_proposed_fee_does_not_stamp(self) -> None:
        packet = example_sealed_cold()
        packet["regime"]["components"]["fee"] = "global_95bps"
        packet["regime"]["component_lock"]["fee"] = "proposed"
        packet["knowable_at_t"]["fee"] = "global_95bps"
        packet["regime"]["regime_id"] = packet["regime"]["regime_id"].replace(
            "fee=unverified", "fee=global_95bps"
        )
        self.assertIn("overlay_discipline", evaluate_reasons(packet))
        stamp, errors = stamp_from_packet(packet)
        self.assertIsNone(stamp)
        self.assertTrue(errors)

    def test_honesty_flag_does_not_stamp(self) -> None:
        packet = example_enriched_global_95bps()
        packet["honesty"]["invented_lift"] = True
        self.assertIn("honesty_reject", evaluate_reasons(packet))
        stamp, errors = stamp_from_packet(packet)
        self.assertIsNone(stamp)
        self.assertTrue(errors)

    def test_valid_non_bonding_enriched_packet_rejects(self) -> None:
        packet = example_enriched_global_95bps()
        packet["l1_spine"]["stage"] = "pumpswap"
        packet["regime"]["components"]["stage"] = "pumpswap"
        packet["regime"]["regime_id"] = packet["regime"]["regime_id"].replace(
            "stage=bonding", "stage=pumpswap"
        )
        stamp, errors = stamp_from_packet(packet)
        self.assertEqual(errors, [])
        assert stamp is not None
        self.assertEqual(stamp["evaluate_label"], "reject")
        self.assertEqual(stamp["reasons"], ["stage_not_bonding"])
        self.assertTrue(stamp["full_book"]["arm_retained"])
        self.assertIsNone(stamp["runner_stamp"]["delta_exec"]["value"])

    def test_stage_drift_is_a_reason_and_does_not_stamp(self) -> None:
        packet = example_sealed_cold()
        packet["l1_spine"]["stage"] = "pumpswap"
        self.assertIn("stage_not_bonding", evaluate_reasons(packet))
        stamp, errors = stamp_from_packet(packet)
        self.assertIsNone(stamp)
        self.assertTrue(errors)

    def test_graph_lift_number_rejected(self) -> None:
        stamp = example_sealed_cold_runner()
        stamp["graph_lift"] = 1.5
        errors = validate_stamp(stamp)
        self.assertTrue(errors)

    def test_horizon_number_rejected(self) -> None:
        stamp = example_sealed_cold_runner()
        stamp["runner_stamp"]["horizons"]["60s"] = 0
        errors = validate_stamp(stamp)
        self.assertTrue(errors)

    def test_delta_exec_zero_rejected(self) -> None:
        stamp = example_sealed_cold_runner()
        stamp["runner_stamp"]["delta_exec"]["value"] = 0
        errors = validate_stamp(stamp)
        self.assertTrue(errors)

    def test_honesty_flags_cannot_flip(self) -> None:
        stamp = example_sealed_cold_runner()
        stamp["honesty"]["exp002c_retuned"] = True
        stamp["honesty"]["live_capital"] = True
        errors = validate_stamp(stamp)
        self.assertTrue(errors)

    def test_soft_watch_cannot_block(self) -> None:
        stamp = example_sealed_cold_runner()
        stamp["soft_watches"]["blocking"] = True
        errors = validate_stamp(stamp)
        self.assertTrue(errors)

    def test_label_cannot_be_flipped_without_reasons(self) -> None:
        stamp = example_reject_missing_mint()
        stamp["evaluate_label"] = "runner"
        stamp["runner_stamp"]["kind"] = "paper_promotion"
        stamp["runner_stamp"]["pretend_buy"] = True
        errors = validate_stamp(stamp)
        self.assertTrue(errors)

    def test_module_stays_off_observe_client(self) -> None:
        source = (ROOT / "tools" / "paper_evaluate_hot_packet_v0.py").read_text(encoding="utf-8")
        self.assertNotIn("observe.client", source)
        self.assertNotIn("observe/client", source)
        self.assertNotIn("from observe", source)
        self.assertNotIn("import observe", source)


if __name__ == "__main__":
    unittest.main()
