"""Tests for tools/p39_damm_lp_counts.py (counts only). Real-tx fixture: 3 migrated pools, 2026-10-14..16 (tools/fixtures). No network, no key."""
import base64
import gzip
import json
import math
import unittest
from pathlib import Path

from tools import damm_decode as dd
from tools import p39_damm_lp_counts as C

FIX = Path(__file__).parent / "fixtures" / "damm_v2_txs_2026-10-14_16.json.gz"


def fixture_pools():
    with gzip.open(FIX, "rt") as fh:
        data = json.load(fh)
    out = []
    for p in data:
        m = dd.init_pool_from_migration_tx(p["migtx"])
        bt = int(p["migtx"]["blockTime"])
        sig = p["migtx"]["transaction"]["signatures"][0]
        head = {"type": "pool_meta", "block_time": bt, "damm_pool": p["pool"], "signature": sig, "wsol_is_b": m["wsol_is_b"], "covered_to_bt": min(bt + 86400, C.END_TS), "truncated": False}
        txs = p["txs"] if any(t["transaction"]["signatures"][0] == sig for t in p["txs"]) else [p["migtx"], *p["txs"]]
        rows = [head] + [r for tx in txs for r in dd.decode_tx(tx, p["pool"], m["wsol_is_b"])]
        out.append(rows)
    return out


def trade(side, mode, F=(70, 20, 0, 0), amount_in=1_000_000, amount_in_excl=1_000_000, amount_out=500, sqrt=2 ** 64):
    return {"side": side, "collect_fee_mode": mode, "trading_fee": F[0], "protocol_fee": F[1], "compounding_fee": F[2], "referral_fee": F[3], "amount_in": amount_in,
            "amount_in_excl_fee": amount_in_excl, "amount_out": amount_out, "next_sqrt_price": str(sqrt), "reserve_b": 10 * 10 ** 9}


class TradeFields(unittest.TestCase):
    def test_buy_mode0_fee_in_base_valued_at_spot(self):
        x = C.trade_fields(trade("buy", 0, sqrt=2 * 2 ** 64))  # p = 4 quote atoms per base atom
        self.assertTrue(x["fee_in_base"])
        self.assertAlmostEqual(x["fee_lp_sol"], 70 * 4 / 1e9)
        self.assertAlmostEqual(x["fee_tot_sol"], 90 * 4 / 1e9)
        self.assertAlmostEqual(x["f"], 90 / (500 + 90))
        self.assertAlmostEqual(x["qvol_sol"], 1e-3)

    def test_buy_mode1_fee_on_input_quote(self):
        x = C.trade_fields(trade("buy", 1, amount_in=1_000_090, amount_in_excl=1_000_000))
        self.assertFalse(x["fee_in_base"])
        self.assertAlmostEqual(x["f"], 90 / 1_000_090)
        self.assertAlmostEqual(x["fee_lp_sol"], 70 / 1e9)

    def test_sell_fee_in_quote_and_volume_incl_fee(self):
        x = C.trade_fields(trade("sell", 0, amount_out=999_910))
        self.assertFalse(x["fee_in_base"])
        self.assertAlmostEqual(x["qvol_sol"], 1_000_000 / 1e9)
        self.assertAlmostEqual(x["f"], 90 / 1_000_000)
        self.assertAlmostEqual(x["protocol_share"], 20 / 90)

    def test_compounding_counts_as_lp(self):
        x = C.trade_fields(trade("sell", 2, F=(40, 20, 30, 0), amount_out=999_910))
        self.assertAlmostEqual(x["fee_lp_sol"], 70 / 1e9)


class BreakEven(unittest.TestCase):
    def test_flat_price_deep_pool_zero_fee_needs_only_sends(self):
        b = C.breakeven(1.0, 1e12, 0.0, 1e12, 0.0)
        self.assertAlmostEqual(b["c_buy"], 0.0, places=9)
        self.assertAlmostEqual(b["V0"], 0.25, places=9)
        self.assertAlmostEqual(b["y_be"], C.SENDS / 0.25, places=6)

    def test_price_quarter_loses_half(self):
        b = C.breakeven(0.25, 1e12, 0.0, 1e12, 0.0)
        self.assertAlmostEqual(b["y_be"], (0.25 + C.SENDS - 0.125) / 0.25, places=6)
        self.assertAlmostEqual(b["loss_vs_sol"], 0.5)
        self.assertAlmostEqual(b["directional"] + b["il"], b["loss_vs_sol"])

    def test_costs_raise_breakeven(self):
        self.assertGreater(C.breakeven(1.0, 5.0, 0.01, 5.0, 0.01)["y_be"], C.breakeven(1.0, 1e12, 0.0, 1e12, 0.0)["y_be"])


class Pools(unittest.TestCase):
    def test_fixture_pools_counts(self):
        for rows in fixture_pools():
            c = C.pool_counts(rows, strand=False)
            self.assertTrue(c["usable"])
            self.assertTrue(c["init"]["len_ok"])
            self.assertEqual(c["init"]["collect_fee_mode"], c["collect_fee_mode"])
            self.assertGreater(c["Q_mig"], 0)
            self.assertTrue(0.19 <= c["protocol_share_p50"] <= 0.21, c["protocol_share_p50"])
            self.assertIsNotNone(c["q_check_ratio"])
            self.assertEqual(c["day"][:8], "2026-10-")
            for w, x in c["win"].items():
                if x is not None:
                    self.assertGreaterEqual(x["y"], 0.0)
                    self.assertGreater(x["r"], 0.0)

    def test_row_at_or_after_1017_refused(self):
        rows = fixture_pools()[0]
        bad = [*rows, {**rows[-1], "block_time": C.END_TS}]
        with self.assertRaises(C.Refused):
            C.pool_counts(bad, strand=False)

    def test_migration_outside_window_refused(self):
        rows = fixture_pools()[0]
        rows[0] = {**rows[0], "block_time": C.END_TS + 10}
        with self.assertRaises(C.Refused):
            C.pool_counts(rows, strand=False)

    def test_init_decode_tail_matches_damm_decode(self):
        for rows in fixture_pools():
            ini = next(r for r in rows if r.get("event") == "EvtInitializePool")
            pl = base64.b64decode(ini["payload_b64"])
            d = C.decode_init(pl)
            self.assertIn(d["base_fee_mode"], range(0, 6))
            self.assertGreater(d["cliff_fee_numerator"], 0)
            self.assertLess(d["sqrt_min_price"], dd.init_state(pl)["sqrt_price"])


class K0(unittest.TestCase):
    def _p(self, day, strand, f, t):
        return {"day": day, "strand": strand, "f_e": f, "turnover_6h": t, "win": {"6": {"x": 1}}}

    def test_stratified_estimate(self):
        P = [self._p("2026-10-14", False, 0.003, 3), self._p("2026-10-14", False, 0.001, 3), self._p("2026-10-14", True, 0.01, 5)]
        N = {("2026-10-14", False): 40, ("2026-10-14", True): 4}
        r = C.k0(P, N)
        d = r["per_day"]["2026-10-14"]
        self.assertEqual(d["nonstrand"]["est"], 20.0)
        self.assertEqual(d["strand"]["est"], 4.0)
        self.assertEqual(r["mean_est_per_day"], 24.0)
        self.assertFalse(r["kill"])
        self.assertTrue(math.isfinite(d["est_total"]))


if __name__ == "__main__":
    unittest.main()
