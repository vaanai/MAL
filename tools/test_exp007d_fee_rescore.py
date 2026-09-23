"""Unit tests for EXP-007d decode-only fee rescore."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.exp007d_fee_rescore import rescore_enrich_jsonl


def _minimal_007c_row(*, global_bps: int, holder: bool = False) -> dict:
    fee = "unverified" if global_bps == 95 else "global_100bps"
    return {
        "type": "regime_enrich",
        "parent_signature": "SigTest",
        "regime_id_enriched": "fee=unverified|instr=create|quote=wsol|stage=bonding|venue=pump_program|market=bonding_curve|stream=subscribeNewToken",
        "knowable_at_t_enriched": {"fee": fee},
        "platform_resolved": {"fee": fee, "instr": "create", "quote": "wsol", "stage": "bonding", "market": "bonding_curve"},
        "rpc_meta": {
            "fee_resolve": {
                "global_fee_bps": global_bps,
                "is_holder_reward": holder,
                "creator_fee_bps": 0,
            }
        },
    }


class RescoreTests(unittest.TestCase):
    def test_95_maps_to_global_95bps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "enrich.jsonl"
            path.write_text(json.dumps(_minimal_007c_row(global_bps=95)) + "\n", encoding="utf-8")
            out = Path(tmp) / "out.jsonl"
            summary = rescore_enrich_jsonl(path, output_path=out)
            self.assertEqual(summary["fee_counts"].get("global_95bps"), 1)
            self.assertEqual(summary["fee_unverified_rate"], 0.0)
            row = json.loads(out.read_text(encoding="utf-8").strip())
            self.assertEqual(row["platform_resolved"]["fee"], "global_95bps")
            self.assertIn("fee=global_95bps", row["regime_id_enriched"])


if __name__ == "__main__":
    unittest.main()
