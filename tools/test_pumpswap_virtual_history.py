"""Offline tests for tools/pumpswap_virtual_history.py using recorded event numbers."""

from __future__ import annotations

import unittest

from tools import pumpswap_virtual_history as h

# Recorded 2026-10-04 from getTransaction of 5wfLXiTg3k (2026-08-28, pool 2ZAiDf, `buy`).
AUG = {"quote_amount_in_with_lp_fee": 78_001_505, "lp_fee": 155_692, "user_quote_amount_in": 78_079_351,
       "pool_quote_token_reserves": 1_568_192_865_127, "pool_base_token_reserves": 11_109_994_483_702, "base_amount_out": 545_362_870}


class VirtualHistoryTests(unittest.TestCase):
    def test_pool_net_ignores_gross_user_amount(self):
        self.assertEqual(h.pool_net(AUG), 77_845_813)

    def test_aug_event_needs_virtual_quote(self):
        net = h.pool_net(AUG)
        out = AUG["base_amount_out"]
        v0 = h.cp_out(net, AUG["pool_quote_token_reserves"], AUG["pool_base_token_reserves"], 0)
        v = h.cp_out(net, AUG["pool_quote_token_reserves"], AUG["pool_base_token_reserves"], 17_584_000_000)
        self.assertGreater(abs(v0 - out) / out, 1e-2)  # V=0 is 112 bps off
        self.assertLess(abs(v - out) / out, 1e-5)  # V=17.584 SOL: 0.003 bps
        self.assertAlmostEqual(h.implied_virtual(AUG) / 1e9, 17.5845, places=2)


if __name__ == "__main__":
    unittest.main()
