"""Unit tests for regime stamping (no network)."""

import unittest

from observe.regime import build_regime_id, seal_ingest_record


class RegimeIdTests(unittest.TestCase):
    def test_create_regime_id_order(self) -> None:
        rid = build_regime_id(stream="subscribeNewToken", stage="bonding")
        self.assertIn("env=mainnet", rid)
        self.assertTrue(rid.index("env=") < rid.index("stream=") < rid.index("stage="))
        self.assertEqual(rid.count("|"), 9)

    def test_seal_create(self) -> None:
        row = seal_ingest_record(
            t_ws="2026-09-20T00:00:00+00:00",
            stream="subscribeNewToken",
            payload={"txType": "create", "mint": "M", "signature": "S"},
        )
        self.assertEqual(row["stage"], "bonding")
        self.assertIn("stage=bonding", row["regime_id"])
        self.assertFalse(row["knowable_at_t"]["quote_verified"])


if __name__ == "__main__":
    unittest.main()
