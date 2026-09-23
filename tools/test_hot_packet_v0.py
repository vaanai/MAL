"""Offline tests for hot-packet v0 fixtures. No network."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.hot_packet_v0 import (
    EXAMPLES,
    example_enriched_global_95bps,
    example_enriched_launchlab,
    example_graph_slots_nullable,
    example_sealed_cold,
    main,
    validate_packet,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "hot_packet_v0"
SCHEMA = ROOT / "ARTIFACTS" / "hot-packet-v0.schema.json"

FIXTURE_NAMES = {
    "sealed-cold": "sealed_create_cold_graph.json",
    "enriched-fee": "enriched_global_95bps_cold_graph.json",
    "enriched-launchlab": "enriched_launchlab_init_cold_graph.json",
    "graph-slots": "sealed_graph_slots_nullable.json",
}


class HotPacketV0Tests(unittest.TestCase):
    def test_examples_validate(self) -> None:
        for name, builder in EXAMPLES.items():
            with self.subTest(name=name):
                self.assertEqual(validate_packet(builder()), [])

    def test_fixtures_match_examples_and_validate(self) -> None:
        for which, filename in FIXTURE_NAMES.items():
            with self.subTest(filename=filename):
                payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
                self.assertEqual(payload, EXAMPLES[which]())
                self.assertEqual(validate_packet(payload), [])
                self.assertEqual(main(["validate", str(FIXTURES / filename)]), 0)

    def test_schema_file_is_json(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["title"], "MAL hot-packet v0 (Proposed paper)")
        self.assertIn("global_95bps", schema["$defs"]["fee"]["enum"])
        self.assertIn("launchlab_init", schema["$defs"]["instr"]["enum"])
        self.assertIn("proposed", schema["$defs"]["lock"]["enum"])

    def test_example_cli(self) -> None:
        self.assertEqual(main(["example", "--which", "sealed-cold"]), 0)

    def test_sealed_rejects_proposed_fee(self) -> None:
        packet = example_sealed_cold()
        packet["regime"]["components"]["fee"] = "global_95bps"
        packet["regime"]["component_lock"]["fee"] = "proposed"
        packet["knowable_at_t"]["fee"] = "global_95bps"
        packet["regime"]["regime_id"] = packet["regime"]["regime_id"].replace(
            "fee=unverified", "fee=global_95bps"
        )
        errors = validate_packet(packet)
        self.assertTrue(any("enriched" in err for err in errors))
        self.assertTrue(any("sealed overlay" in err for err in errors))

    def test_proposed_fee_cannot_lock_as_production(self) -> None:
        packet = example_enriched_global_95bps()
        packet["regime"]["component_lock"]["fee"] = "production"
        errors = validate_packet(packet)
        self.assertTrue(any("component_lock.fee" in err for err in errors))

    def test_launchlab_init_stays_proposed(self) -> None:
        packet = example_enriched_launchlab()
        self.assertEqual(packet["regime"]["component_lock"]["instr"], "proposed")
        self.assertEqual(packet["regime"]["component_lock"]["venue"], "proposed")
        self.assertEqual(packet["regime"]["component_lock"]["market"], "proposed")
        self.assertEqual(packet["regime"]["component_lock"]["fee"], "production")
        packet["regime"]["component_lock"]["instr"] = "production"
        errors = validate_packet(packet)
        self.assertTrue(any("launchlab_init" in err for err in errors))

    def test_clock_uses_t_ws_when_t_event_null(self) -> None:
        packet = example_sealed_cold()
        packet["clocks"]["T_decision"] = "2026-09-20T12:00:01.000+00:00"
        errors = validate_packet(packet)
        self.assertTrue(any("T_decision" in err for err in errors))

    def test_clock_uses_t_event_when_present(self) -> None:
        packet = example_sealed_cold()
        packet["clocks"]["t_event"] = "2026-09-20T11:59:59.000+00:00"
        errors = validate_packet(packet)
        self.assertTrue(any("T_decision" in err for err in errors))
        packet["clocks"]["T_decision"] = packet["clocks"]["t_event"]
        packet["clocks"]["t_precompute_as_of"] = packet["clocks"]["t_event"]
        self.assertEqual(validate_packet(packet), [])

    def test_precompute_after_t_rejected(self) -> None:
        packet = example_graph_slots_nullable()
        later = "2026-09-20T12:00:02.000+00:00"
        packet["clocks"]["t_precompute_as_of"] = later
        for slot in packet["graph"]["slots"]:
            if isinstance(slot, dict):
                slot["t_precompute_as_of"] = later
        errors = validate_packet(packet)
        self.assertTrue(any("t_precompute_as_of" in err for err in errors))

    def test_burst_slot_rejected(self) -> None:
        packet = example_graph_slots_nullable()
        packet["graph"]["slots"][2] = {
            "slot_id": "burst_count",
            "value": 3,
            "evidence_tier": "weak_ws",
            "hypothesis_status": "directional_watch_not_promoted",
            "t_precompute_as_of": packet["clocks"]["t_precompute_as_of"],
        }
        errors = validate_packet(packet)
        self.assertTrue(any("burst" in err for err in errors))

    def test_early_wallet_non_null_rejected(self) -> None:
        packet = example_graph_slots_nullable()
        packet["graph"]["slots"][1]["value"] = 0.4
        errors = validate_packet(packet)
        self.assertTrue(any("densify" in err for err in errors))

    def test_sixth_slot_rejected(self) -> None:
        packet = example_sealed_cold()
        packet["graph"]["slots"] = [None, None, None, None, None, None]
        packet["graph"]["cold"] = True
        errors = validate_packet(packet)
        self.assertTrue(any("cap is 5" in err for err in errors))

    def test_empty_slots_are_cold(self) -> None:
        packet = example_sealed_cold()
        packet["graph"]["slots"] = []
        self.assertEqual(validate_packet(packet), [])
        packet["graph"]["cold"] = False
        packet["clocks"]["precompute_features_merged"] = True
        errors = validate_packet(packet)
        self.assertTrue(any("graph.cold" in err for err in errors))

    def test_source_day_must_match_t_ws(self) -> None:
        packet = example_sealed_cold()
        packet["provenance"]["source_day"] = "2026-09-21"
        errors = validate_packet(packet)
        self.assertTrue(any("source_day" in err for err in errors))

    def test_honesty_flags_cannot_flip(self) -> None:
        packet = example_enriched_global_95bps()
        packet["honesty"]["enum_production_lock"] = True
        packet["honesty"]["continuous_observe_wiring"] = True
        errors = validate_packet(packet)
        self.assertTrue(any("enum_production_lock" in err for err in errors))
        self.assertTrue(any("continuous_observe_wiring" in err for err in errors))

    def test_forbidden_enrich_keys(self) -> None:
        packet = example_sealed_cold()
        packet["l1_spine"]["dexscreener"] = {"pair": "nope"}
        errors = validate_packet(packet)
        self.assertTrue(any("dexscreener" in err for err in errors))

    def test_bonding_pool_rejected(self) -> None:
        packet = example_sealed_cold()
        packet["l1_spine"]["pool"] = "PoolExample"
        errors = validate_packet(packet)
        self.assertTrue(any("pool" in err for err in errors))

    def test_delta_exec_stays_null(self) -> None:
        packet = example_sealed_cold()
        packet["delta_exec"]["value"] = 1.5
        errors = validate_packet(packet)
        self.assertTrue(any("delta_exec.value" in err for err in errors))


if __name__ == "__main__":
    unittest.main()
