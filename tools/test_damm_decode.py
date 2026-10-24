"""Tests for tools/damm_decode.py on captured real DAMM v2 transactions (3 migrated pools, 2026-10-14..16). No network, no key."""
import gzip
import json
import unittest
from pathlib import Path

from tools import damm_decode as dd

FIX = Path(__file__).parent / "fixtures" / "damm_v2_txs_2026-10-14_16.json.gz"


def load():
    with gzip.open(FIX, "rt") as fh:
        return json.load(fh)


class Damm(unittest.TestCase):
    def test_pool_index_is_7_not_the_constant_authority(self):
        for p in load():
            m = dd.init_pool_from_migration_tx(p["migtx"])
            self.assertEqual(m["damm_pool"], p["pool"])
            self.assertTrue(m["index_ok"])  # equals the DBC instruction's accounts[4]
            self.assertNotEqual(m["damm_pool"], m["pool_authority"])
            self.assertTrue(m["wsol_is_b"])

    def test_swap_payload_exact_and_curve_reproduced(self):
        for p in load():
            m = dd.init_pool_from_migration_tx(p["migtx"])
            rows = [r for tx in p["txs"] for r in dd.decode_tx(tx, p["pool"], m["wsol_is_b"])]
            tr = [r for r in rows if r["type"] == "trade"]
            self.assertGreater(len(tr), 5)
            self.assertTrue(all("trailing_bytes" not in r for r in tr))
            last, ok, n = None, 0, 0
            for r in tr:  # fetch order = execution order
                if last is not None:
                    x = dd.liquidity_from_swap(last, r)
                    if x:
                        n += 1
                        ok += abs(x[0] / x[1] - 1) < 1e-2
                last = int(r["next_sqrt_price"])
            self.assertGreater(ok / n, 0.97)

    def test_sides(self):
        p = load()[0]
        rows = [r for tx in p["txs"] for r in dd.decode_tx(tx, p["pool"], True) if r["type"] == "trade"]
        for r in rows:
            self.assertEqual(r["side"], "buy" if r["trade_direction"] == 1 else "sell")


if __name__ == "__main__":
    unittest.main()
