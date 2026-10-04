"""Offline tests: V map, correction adapter, restoration of monkeypatches."""

from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path

import tools.exploration_entry_model as eem
from tools import paper_curve_math as pcm
from tools import pumpswap_virtual as pv
from tools import pumpswap_virtual_adapter as ad
from tools.paper_price_path import TapePrint, print_from_trade_row

FIX = Path(__file__).parent / "fixtures" / "pumpswap"
V_FIX = 17_584_269_263  # pool 3ABzyq, from sell_a.json's pool account


def swap_row(pool="P1", side="buy"):
    return {"venue": "pumpswap", "mint": "M", "side": side, "sol_lamports": 500_000_000, "token_raw": 102_211_917_194, "quote_reserve": 276_506_663_183,
            "base_reserve": 60_822_600_309_479, "pool": pool, "slot": 1, "event_index": 0, "t_recv_ms": 1000, "quote_is_wsol": True, "signature": "s", "tx_index": 1}


class AdapterTests(unittest.TestCase):
    def test_v_from_fixture_pool_account(self):
        data = base64.b64decode(json.loads((FIX / "sell_a.json").read_text())["pool_account_b64"])
        self.assertEqual(pv.parse_virtual(data), V_FIX)
        self.assertIsNone(pv.parse_virtual(b"short"))

    def test_adds_v_only_to_pumpswap(self):
        pr = TapePrint(1, 1, 0, "pumpswap", "buy", 1, 100, 1000, 0.1, 100.0)
        out = ad.correct_print(pr, 50)
        self.assertEqual(out.quote_reserve, 150)
        self.assertAlmostEqual(out.price_sol, 150 / (1000 * 1000))
        bond = TapePrint(1, 1, 0, "pump_bonding", "buy", 1, 100, 1000, 0.1, 100.0)
        self.assertIs(ad.correct_print(bond, 50), bond)
        self.assertIs(ad.correct_print(pr, None), pr)

    def test_corrected_quote_reproduces_recorded_buy_event(self):
        # 2026-10-04 simulation, pool 3ABzyq, 0.5 SOL at 100 bps total fee: on-chain base_amount_out below.
        row = swap_row()
        w = ad.make_wrapper(print_from_trade_row, {"P1": V_FIX})
        ad.reset_counts()
        _, pr = w(row)
        # the print is post-trade; rebuild the pre-trade state the event used
        q, b = row["quote_reserve"], row["base_reserve"]
        net = 500_000_000 * 10_000 // 10_100
        tokens = net * b // (q + V_FIX + net)
        self.assertLess(abs(tokens - 102_211_917_194) / 102_211_917_194 * 1e4, 0.1)
        self.assertGreater(pr.quote_reserve, q + V_FIX)  # corrected, and the buy was posted
        tokens_novirt = net * b // (q + net)
        self.assertGreater(abs(tokens_novirt - 102_211_917_194) / 102_211_917_194 * 1e4, 50)
        self.assertEqual(ad.COUNTS["corrected"], 1)

    def test_august_event_with_v(self):
        ev = {"net": 77_845_813, "q": 1_568_192_865_127, "b": 11_109_994_483_702, "out": 545_362_870}
        got = ev["net"] * ev["b"] // (ev["q"] + 17_584_000_000 + ev["net"])
        self.assertLess(abs(got - ev["out"]) / ev["out"] * 1e4, 0.1)

    def test_missing_v_is_counted_not_zero(self):
        ad.reset_counts()
        w = ad.make_wrapper(print_from_trade_row, {"P1": None})
        _, pr = w(swap_row())
        _, ref = print_from_trade_row(swap_row())
        self.assertEqual(pr, ref)  # left on frozen pricing
        self.assertEqual(ad.COUNTS["no_v"], 1)
        w2 = ad.make_wrapper(print_from_trade_row, {})
        w2(swap_row("unknown"))
        self.assertEqual(ad.COUNTS["no_v"], 2)

    def test_bonding_row_untouched(self):
        row = {"venue": "pump_bonding", "mint": "M", "side": "buy", "sol_lamports": 1, "token_raw": 1, "quote_reserve": 30_000_000_000, "base_reserve": 1_000_000_000_000_000, "slot": 1, "t_recv_ms": 1}
        ad.reset_counts()
        w = ad.make_wrapper(print_from_trade_row, {})
        self.assertEqual(w(dict(row))[1], print_from_trade_row(dict(row))[1])
        self.assertEqual(ad.COUNTS["bonding_untouched"], 1)

    def test_patch_restored(self):
        orig = eem.print_from_trade_row
        with ad.virtual_reserve_patch({}):
            self.assertIsNot(eem.print_from_trade_row, orig)
        self.assertIs(eem.print_from_trade_row, orig)
        with self.assertRaises(RuntimeError):
            with ad.virtual_reserve_patch({}):
                raise RuntimeError("x")
        self.assertIs(eem.print_from_trade_row, orig)

    def test_pool_workers_restored(self):
        import tools.exploration_entry_model_b2 as b2
        import tools.exploration_entry_model_b3 as b3

        saved = (eem.run_worker_a, b2.run_worker_b, b3.run_worker_c)
        with ad.patched_pool_workers():
            self.assertIs(eem.run_worker_a, ad.worker_a)
        self.assertEqual((eem.run_worker_a, b2.run_worker_b, b3.run_worker_c), saved)

    def test_exit_capture_restored(self):
        import tools.exploration_exits as ee

        d, s = ee._delayed, eem.score_one
        with ad.exit_capture():
            self.assertIsNot(eem.score_one, s)
        self.assertIs(ee._delayed, d)
        self.assertIs(eem.score_one, s)

    def test_build_map_null_and_retry(self):
        calls = []

        def fetch(chunk):
            calls.append(list(chunk))
            return [None if p == "bad" else 17_584_000_000 for p in chunk]

        m, n = pv.build_map(["a", "bad"], fetch=fetch, rps=5)
        self.assertEqual(m, {"a": 17_584_000_000, "bad": None})
        self.assertEqual(pv.missing_pools(m), ["bad"])
        m2, _ = pv.build_map(["a", "bad"], existing=m, fetch=fetch, rps=5)
        self.assertEqual(calls[1], ["bad"])  # known pools are not refetched; nulls are retried

    def test_paper_fee_tier_follows_mcap_with_v(self):
        pr = TapePrint(1, 1, 0, "pumpswap", "buy", 1, 100_000_000_000, 250_000_000_000_000, 0.0, 0.0)
        self.assertNotEqual(pcm.pumpswap_sol_fee_ppm(100_000_000_000 / (250_000_000_000_000 * 1000) * 1e9), pcm.pumpswap_sol_fee_ppm(117_584_000_000 / (250_000_000_000_000 * 1000) * 1e9))
        out = ad.correct_print(pr, 17_584_000_000, "v")
        self.assertAlmostEqual(out.market_cap_sol, out.price_sol * 1e9)
        self.assertEqual(ad.correct_print(pr, 17_584_000_000, "vault").market_cap_sol, pr.market_cap_sol)


if __name__ == "__main__":
    unittest.main()
