"""Offline tests for paper-fill-sim-hot-packet-evaluate-v0. No network."""

from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.paper_evaluate_hot_packet_v0 import (
    example_reject_missing_mint,
    example_sealed_cold_runner,
)
from tools.paper_fill_sim_hot_packet_evaluate_v0 import (
    EXAMPLES,
    FILL_SIM_REJECT,
    FILL_SIM_RUNNER,
    HORIZON_STATUS,
    SOFT_WATCH_ITEMS,
    bind_from_evaluate_stamp,
    example_reject_identity_bind,
    example_sealed_cold_runner_bind,
    main,
    validate_fill_sim_stamp,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "paper_fill_sim_hot_packet_evaluate_v0"
EVAL_FIXTURES = ROOT / "fixtures" / "paper_evaluate_hot_packet_v0"
SCHEMA = ROOT / "ARTIFACTS" / "paper-fill-sim-hot-packet-evaluate-v0.schema.json"

FIXTURE_NAMES = {
    "sealed-cold-runner": "sealed_cold_runner.json",
    "enriched-fee-runner": "enriched_fee_runner.json",
    "enriched-launchlab-runner": "enriched_launchlab_runner.json",
    "graph-slots-not-scored": "graph_slots_not_scored.json",
    "reject-missing-identity": "reject_missing_identity.json",
}


def _quiet(argv: list[str]) -> int:
    with redirect_stdout(io.StringIO()):
        return main(argv)


class PaperFillSimHotPacketEvaluateV0Tests(unittest.TestCase):
    def test_examples_validate(self) -> None:
        for name, builder in EXAMPLES.items():
            with self.subTest(name=name):
                self.assertEqual(validate_fill_sim_stamp(builder()), [])

    def test_fixtures_match_examples_and_validate(self) -> None:
        for which, filename in FIXTURE_NAMES.items():
            with self.subTest(filename=filename):
                payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
                self.assertEqual(payload, EXAMPLES[which]())
                self.assertEqual(validate_fill_sim_stamp(payload), [])
                self.assertEqual(_quiet(["validate", str(FIXTURES / filename)]), 0)

    def test_schema_file_is_json(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["title"],
            "MAL paper-fill-sim-hot-packet-evaluate v0 (Proposed paper stamp)",
        )
        self.assertEqual(
            schema["$defs"]["input"]["properties"]["stamp"]["$ref"],
            "paper-evaluate-hot-packet-v0.schema.json",
        )
        self.assertEqual(schema["$defs"]["measure"]["properties"]["kind"]["const"], "none")
        self.assertEqual(schema["$defs"]["dualRead"]["properties"]["sealed_book_rpc_slice"]["const"], "incomplete")
        items = schema["$defs"]["softWatches"]["properties"]["items"]["items"]["enum"]
        for item in SOFT_WATCH_ITEMS:
            self.assertIn(item, items)
        self.assertEqual(schema["$defs"]["softWatches"]["properties"]["blocking"]["const"], False)

    def test_sim_cli_on_evaluate_fixture(self) -> None:
        path = EVAL_FIXTURES / "sealed_cold_graph_runner.json"
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["sim", str(path)])
        self.assertEqual(code, 0)
        out = json.loads(buf.getvalue())
        self.assertEqual(out, example_sealed_cold_runner_bind())

    def test_sim_refuses_outside_fixtures(self) -> None:
        self.assertEqual(_quiet(["sim", "/tmp/evaluate.json"]), 1)

    def test_sim_refuses_var_lib_mal(self) -> None:
        self.assertEqual(
            _quiet(["sim", "/var/lib/mal/paper/stamp.json"]),
            1,
        )

    def test_bind_preserves_evaluate_label_both_arms(self) -> None:
        runner, _ = bind_from_evaluate_stamp(example_sealed_cold_runner())
        assert runner is not None
        self.assertEqual(runner["evaluate_label"], "runner")
        self.assertEqual(runner["fill_sim"]["status"], FILL_SIM_RUNNER)
        reject, _ = bind_from_evaluate_stamp(example_reject_missing_mint())
        assert reject is not None
        self.assertEqual(reject["evaluate_label"], "reject")
        self.assertEqual(reject["fill_sim"]["status"], FILL_SIM_REJECT)
        self.assertTrue(reject["full_book"]["arm_retained"])

    def test_dishonest_closed_book_refused(self) -> None:
        evaluate = copy.deepcopy(example_sealed_cold_runner())
        evaluate["input"]["packet"]["dual_read"]["closed_book_claim"] = True
        stamp, errors = bind_from_evaluate_stamp(evaluate)
        self.assertIsNone(stamp)
        self.assertTrue(errors)

    def test_numeric_horizon_on_parent_refused(self) -> None:
        evaluate = example_sealed_cold_runner()
        evaluate["runner_stamp"]["horizons"]["60s"] = 1.0
        stamp, errors = bind_from_evaluate_stamp(evaluate)
        self.assertIsNone(stamp)
        self.assertTrue(errors)

    def test_invented_fill_price_refused(self) -> None:
        stamp = example_sealed_cold_runner_bind()
        stamp["fill_sim"]["fill_price_proxy"] = 0.001
        self.assertTrue(validate_fill_sim_stamp(stamp))

    def test_measure_kind_refused(self) -> None:
        stamp = example_sealed_cold_runner_bind()
        stamp["measure"]["kind"] = "oracle"
        self.assertTrue(validate_fill_sim_stamp(stamp))

    def test_delta_exec_zero_refused(self) -> None:
        stamp = example_sealed_cold_runner_bind()
        stamp["delta_exec"]["value"] = 0
        self.assertTrue(validate_fill_sim_stamp(stamp))

    def test_horizon_number_refused(self) -> None:
        stamp = example_sealed_cold_runner_bind()
        stamp["horizons"]["values"]["5s"] = 0
        self.assertTrue(validate_fill_sim_stamp(stamp))

    def test_honesty_exp006_promoted_cannot_flip(self) -> None:
        stamp = example_sealed_cold_runner_bind()
        stamp["honesty"]["exp006_promoted"] = True
        self.assertTrue(validate_fill_sim_stamp(stamp))

    def test_soft_watch_cannot_block(self) -> None:
        stamp = example_sealed_cold_runner_bind()
        stamp["soft_watches"]["blocking"] = True
        self.assertTrue(validate_fill_sim_stamp(stamp))

    def test_invalid_evaluate_stamp_does_not_bind(self) -> None:
        evaluate = example_sealed_cold_runner()
        evaluate["evaluate_label"] = "reject"
        stamp, errors = bind_from_evaluate_stamp(evaluate)
        self.assertIsNone(stamp)
        self.assertTrue(errors)

    def test_observe_client_untouched(self) -> None:
        source = (ROOT / "tools" / "paper_fill_sim_hot_packet_evaluate_v0.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("observe/client", source)
        self.assertNotIn("observe.client", source)

    def test_fill_model_constants(self) -> None:
        stamp = example_sealed_cold_runner_bind()
        model = stamp["fill_model"]
        self.assertEqual(model["latency_ms"], 500)
        self.assertEqual(model["fee_model_id"], "pump_assumed_bps_v0")
        self.assertFalse(model["fitted_to_lift"])
        self.assertEqual(stamp["delta_exec"]["status"], HORIZON_STATUS)
        self.assertIsNone(stamp["delta_exec"]["value"])


if __name__ == "__main__":
    unittest.main()
