"""Offline tests for tools/h5_reconcile.py. The ledger is produced by the real executor against the probe's fake RPC."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

from tools import h5_executor as h5
from tools import h5_reconcile as rc
from tools import paper_curve_math as pcm
from tools.test_h5_executor import BASE0, MINT, QREAL, S0, SPS, STAKE, T0, TRIG_SLOT, V, Case
from tools.test_h5_executor_rules import SLIPPAGE_ERR

PROCEEDS = 21_000_000


class CostModelTests(unittest.TestCase):
    Q, B = 30 * 10**9, 150 * 10**12

    def test_entry_and_exit_follow_the_rule_formulas(self):
        stake = 100_000_000
        qe, be, qx, bx = self.Q, self.B, int(self.Q * 1.25), int(self.B * 0.85)
        f = pcm.pumpswap_sol_fee_ppm(pcm.market_cap_sol(qe, be)) / 1e6
        net = stake * (1 - f)
        tk = be * net / (qe + net)
        q2, b2 = qx + net, bx - tk
        f2 = pcm.pumpswap_sol_fee_ppm(pcm.market_cap_sol(q2, b2 + tk)) / 1e6
        proceeds = tk * q2 / (b2 + tk) * (1 - f2)
        out = rc.sim_trade(stake, qe, be, qx, bx)
        self.assertAlmostEqual(out["tokens"], tk, places=3)
        self.assertAlmostEqual(out["proceeds"], proceeds, places=3)
        self.assertAlmostEqual(out["pnl"], proceeds - stake - 2 * 55_000, places=3)

    def test_our_own_impact_is_in_the_exit_state(self):
        # at an unchanged pool the round trip loses fees and impact: pnl < -(2 x priority)
        out = rc.sim_trade(100_000_000, self.Q, self.B, self.Q, self.B)
        self.assertLess(out["pnl"], -2 * 55_000)

    def test_a_flat_book_with_no_priority_loses_about_two_pool_fees(self):
        out = rc.sim_trade(100_000_000, self.Q, self.B, self.Q, self.B, prio=0)
        f = pcm.pumpswap_sol_fee_ppm(pcm.market_cap_sol(self.Q, self.B)) / 1e6
        self.assertAlmostEqual(out["pnl"] / 100_000_000, -2 * f, delta=0.003)


class ReconcileTests(Case):
    def run_trade(self, *, land_buy_slot=None, sell_slot=None, proceeds=PROCEEDS, fail_first_sell=False, sub=None):
        e = Env2(self, sub) if sub else self.env()
        e.fire()
        e.land_buy(slot=land_buy_slot)
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        if fail_first_sell:
            e.land_sell(plan["land_slot"], err=SLIPPAGE_ERR)
            e.at_slot(plan["send_slot"] + 2)  # past the 250 ms retry throttle
        e.land_sell(sell_slot or plan["land_slot"], proceeds=proceeds)
        return e, plan

    def sim_row(self, plan, **kw):
        return {"mint": MINT, "sim_pnl_lamports": PROCEEDS - STAKE - 2 * 55_000, **kw}

    def test_live_minus_sim_is_zero_when_live_prices_exactly_as_the_model(self):
        e, plan = self.run_trade()
        trades = rc.build_trades(rc.read_jsonl(Path(e.ex.fills.path)), {MINT: self.sim_row(plan)})
        t = trades[0]
        # live pnl pays 5,000 base fee per tx that the rule's 2 x 55,000 does not include; the comparable figure adds it back
        self.assertEqual(t["live_pnl"] - t["live_pnl_comparable"], -10_000)
        self.assertEqual(t["gap_lamports"], 0)
        self.assertEqual(t["gap_pct_of_stake"], 0.0)

    def test_gap_is_live_minus_sim(self):
        e, plan = self.run_trade(proceeds=PROCEEDS - 400_000)
        t = rc.build_trades(rc.read_jsonl(Path(e.ex.fills.path)), {MINT: self.sim_row(plan)})[0]
        self.assertEqual(t["gap_lamports"], -400_000)
        self.assertAlmostEqual(t["gap_pct_of_stake"], -2.0)

    def test_gap_from_states_uses_the_cost_model(self):
        e, plan = self.run_trade()
        q, b = QREAL + V, BASE0
        states = {"mint": MINT, "q_entry": q, "b_entry": b, "q_exit": q, "b_exit": b}
        t = rc.build_trades(rc.read_jsonl(Path(e.ex.fills.path)), {MINT: states})[0]
        self.assertAlmostEqual(t["sim"]["pnl"], rc.sim_trade(STAKE, q, b, q, b)["pnl"])
        self.assertAlmostEqual(t["gap_lamports"], t["live_pnl_comparable"] - t["sim"]["pnl"])

    def test_landing_delay_in_slots_and_ms(self):
        e, _plan = self.run_trade(land_buy_slot=TRIG_SLOT + 8)
        t = rc.build_trades(rc.read_jsonl(Path(e.ex.fills.path)), {})[0]
        self.assertEqual((t["landing_slots"], t["landing_ms_est"]), (8, 1600.0))
        s = rc.summarize([t])
        self.assertEqual((s["landing_slots_median"], s["landing_ms_median"], s["n_closed"]), (8, 1600.0, 1))

    def test_exit_landing_error_against_the_plan_and_the_late_flag(self):
        e, plan = self.run_trade(sell_slot=None)
        t = rc.build_trades(rc.read_jsonl(Path(e.ex.fills.path)), {})[0]
        self.assertEqual((t["exit_error_slots"], t["exit_error_ms_est"], t["exit_late"]), (0, 0.0, False))
        e2 = Env2(self, "late")
        e2.fire()
        e2.land_buy()
        p2 = e2.plan()
        e2.at_slot(p2["send_slot"])
        e2.land_sell(p2["late_slot"] + 5)
        t2 = rc.build_trades(rc.read_jsonl(Path(e2.ex.fills.path)), {})[0]
        self.assertTrue(t2["exit_late"])
        self.assertEqual(t2["exit_error_slots"], p2["late_slot"] + 5 - p2["land_slot"])
        self.assertEqual(rc.summarize([t, t2])["exit_late_share"], 0.5)

    def test_exit_against_boost_last_slice(self):
        e, plan = self.run_trade()
        row = self.sim_row(plan, boost_last_slice_slot=plan["land_slot"] + 60)  # BOOST ends 60 slots (12 s) after we land
        t = rc.build_trades(rc.read_jsonl(Path(e.ex.fills.path)), {MINT: row})[0]
        self.assertEqual((t["boost_margin_slots"], t["boost_margin_ms_est"]), (60, 12_000.0))
        e2 = Env2(self, "after")
        e2.fire()
        e2.land_buy()
        p2 = e2.plan()
        e2.at_slot(p2["send_slot"])
        e2.land_sell(p2["land_slot"])
        t2 = rc.build_trades(rc.read_jsonl(Path(e2.ex.fills.path)), {MINT: {"mint": MINT, "boost_last_slice_slot": p2["land_slot"] - 5}})[0]
        self.assertEqual(t2["boost_margin_slots"], -5)
        self.assertEqual(rc.summarize([t, t2])["exit_after_boost_end_share"], 0.5)

    def test_boost_last_slice_seconds_logged_by_the_executor_is_used(self):
        e, plan = self.run_trade()
        e.ex.on_boost_row(MINT, S0, SPS, None, 341.5)
        t = rc.build_trades(rc.read_jsonl(Path(e.ex.fills.path)), {})[0]
        # landed at s0 + 330 s; BOOST's last slice at +341.5 s: 11.5 s of margin
        self.assertAlmostEqual(t["boost_margin_ms_est"], 11_500.0, delta=1.0)

    def test_fail_rates(self):
        e = self.env()
        e.fire()
        p = e.ex.state.pending[MINT]
        e.rpc.statuses[p["signature"]] = {"slot": TRIG_SLOT + 9, "confirmationStatus": "confirmed", "err": SLIPPAGE_ERR}
        from tools.test_probe_live import meta_result

        e.rpc.txs[p["signature"]] = meta_result(e.ex, MINT, delta=-60_000, fee=60_000, err=SLIPPAGE_ERR, slot=TRIG_SLOT + 9, logs=["Program log: slippage"])
        e.ex.advance_pending()
        s = rc.summarize(rc.build_trades(rc.read_jsonl(Path(e.ex.fills.path)), {}))
        self.assertEqual((s["n_buys_resolved"], s["n_buys_landed"], s["buy_fail_rate"], s["buy_fail_classes"]), (1, 0, 1.0, {"slippage_exceeded": 1}))
        e2, _ = self.run_trade(fail_first_sell=True, sub='second')
        s2 = rc.summarize(rc.build_trades(rc.read_jsonl(Path(e2.ex.fills.path)), {}))
        self.assertEqual((s2["n_closed"], s2["sell_fail_rate"]), (1, 0.5))  # one failed attempt, one landed

    def test_entry_against_the_trigger_state(self):
        e, _plan = self.run_trade()
        t = rc.build_trades(rc.read_jsonl(Path(e.ex.fills.path)), {})[0]
        self.assertIn("entry_vs_trigger_state_bps", t)
        self.assertGreater(t["trigger_state_tokens"], 0)

    def test_no_sim_file_still_reports_the_live_side(self):
        e, _plan = self.run_trade()
        s = rc.summarize(rc.build_trades(rc.read_jsonl(Path(e.ex.fills.path)), {}))
        self.assertEqual((s["n_closed"], s["n_with_sim"], s["gap_lamports_mean"]), (1, 0, None))
        self.assertIn("never gate evidence", s["caveat"])

    def test_dry_run_decisions_are_not_trades(self):
        e = self.env(live=False)
        e.fire()
        self.assertEqual(rc.build_trades(rc.read_jsonl(Path(e.ex.fills.path)), {}), [])

    def test_cli_default_prints_aggregates_only(self):
        e, plan = self.run_trade()
        sim = self.tmp / "sim.jsonl"
        sim.write_text(json.dumps(self.sim_row(plan)) + "\n")
        out = self.tmp / "out.json"
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = rc.main(["--ledger", str(e.ex.fills.path), "--sim", str(sim), "--json", str(out)], now_ms=T0)
        self.assertEqual(code, 0)
        text = buf.getvalue()
        self.assertIn("gap_lamports_mean: 0", text)
        self.assertNotIn(MINT, text)
        self.assertNotIn("live_pnl", text)
        self.assertEqual(json.loads(out.read_text())["n_closed"], 1)

    def test_per_trade_rows_are_refused_inside_the_seal_window(self):
        e, _plan = self.run_trade()
        with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()) as out:
            code = rc.main(["--ledger", str(e.ex.fills.path), "--per-trade"], now_ms=h5.SEAL_START_MS + 1)
        self.assertEqual(code, 2)
        self.assertIn("seal window", err.getvalue())
        self.assertEqual(out.getvalue(), "")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rc.main(["--ledger", str(e.ex.fills.path), "--per-trade"], now_ms=h5.SEAL_END_DEFAULT_MS), 0)
        self.assertIn(MINT, out.getvalue())
        self.assertFalse(rc.in_seal_window(h5.SEAL_START_MS - 1))
        self.assertTrue(rc.in_seal_window(h5.SEAL_START_MS))


class Env2:
    """A second, independent live executor in its own subdirectory (the Case's tmp holds the first)."""

    def __new__(cls, case: Case, name: str):
        from tools.test_h5_executor import Env

        d = case.tmp / name
        d.mkdir()
        return Env(d)


if __name__ == "__main__":
    unittest.main()
