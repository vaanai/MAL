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
        self.assertIsNone(row["t_event"])

    def test_seal_t_event_from_timestamp_string(self) -> None:
        row = seal_ingest_record(
            t_ws="2026-09-20T00:00:01+00:00",
            stream="subscribeNewToken",
            payload={
                "txType": "create",
                "mint": "M",
                "signature": "S",
                "timestamp": "2026-09-20T00:00:00.500+00:00",
            },
        )
        self.assertEqual(row["t_event"], "2026-09-20T00:00:00.500+00:00")

    def test_seal_t_event_from_timestamp_unix(self) -> None:
        row = seal_ingest_record(
            t_ws="2026-09-20T00:00:02+00:00",
            stream="subscribeMigration",
            payload={
                "txType": "migration",
                "mint": "M",
                "signature": "S",
                "timestamp": 1_700_000_000,
            },
        )
        self.assertIsNotNone(row["t_event"])
        self.assertIn("2023", row["t_event"])

    def test_seal_migration_tx_type_migrate_alias(self) -> None:
        for tx_type in ("migrate", "migration", "MIGRATE"):
            with self.subTest(tx_type=tx_type):
                row = seal_ingest_record(
                    t_ws="2026-09-20T00:00:03+00:00",
                    stream="subscribeMigration",
                    payload={"txType": tx_type, "mint": "M", "signature": "S"},
                )
                self.assertEqual(row["stage"], "migrating")
                self.assertIn("stage=migrating", row["regime_id"])
                self.assertEqual(row["txType"], tx_type)


if __name__ == "__main__":
    unittest.main()
