"""H5 executor: the review's must-fix items on the exit and the entry (A1 pending sells, A2 buy rebroadcast, A3 slot rate).
Fixtures from test_h5_executor: the chain follows the fake clock, so a test can change the slot time and watch the plan survive it."""

from __future__ import annotations

import unittest
from pathlib import Path

from tools import h5_executor as h
from tools.test_h5_executor import MINT, QREAL, RENT, S0, SPS, T0, TRIG_SLOT, V, Case, Env, buy_args, sell_args
from tools.test_h5_executor import tx
from tools.test_probe_live import meta_result
from tools.test_h5_executor_rules import quote_out


def kinds(e: Env, *names: str) -> list[str]:
    return [r["kind"] for r in e.ledger() if r["kind"] in names]


class PendingSellLadderTests(Case):
    def test_a_sell_that_never_gets_a_status_is_superseded_at_escalation_then_at_the_deadline(self):
        e = self.env()
        e.open_position()
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        self.assertEqual(len(e.rpc.sent), 2)  # the timed sell; no status ever arrives for anything
        first = e.sent()[1]
        self.assertEqual(first["priority"], 55_000)
        e.at_slot(plan["escalate_slot"])
        esc = e.sent()[2]
        self.assertEqual(esc["priority"], 150_000)  # a 150k sell at the escalation, though the first is still pending
        self.assertEqual(sell_args(esc)[1], quote_out(sell_args(esc)[0]) * 6500 // 10_000)
        self.assertNotEqual(esc["signature"], first["signature"])
        e.at_slot(plan["deadline_slot"])
        emg = e.sent()[3]
        self.assertEqual((sell_args(emg)[1], emg["priority"]), (h.EMERGENCY_MIN_OUT, 150_000))  # then the min_out = 1 sell
        self.assertEqual(kinds(e, "sell_sent", "halt_latched"), ["sell_sent", "sell_sent", "sell_sent", "halt_latched"])  # and the latch, last
        self.assertEqual(e.ledger("halt_latched")[0]["reason"], "stuck_position")
        p = e.ex.state.pending[MINT]
        self.assertEqual([x["signature"] for x in p["prior"]], [first["signature"], esc["signature"]])  # every live signature is kept

    def test_a_sell_with_no_status_is_superseded_after_two_seconds_up_the_rungs(self):
        e = self.env()
        e.open_position()
        e.at_slot(e.plan()["send_slot"])
        e.clock.t += 1_900
        e.ex.exit_tick(e.clock())
        self.assertEqual(len(e.rpc.sent), 2)  # not yet
        e.clock.t += 200
        e.ex.exit_tick(e.clock())
        d1 = e.sent()[2]
        self.assertEqual((d1["priority"], sell_args(d1)[1]), (55_000, quote_out(sell_args(d1)[0]) * 8500 // 10_000))  # rung 1: fresh blockhash
        e.clock.t += 2_100
        e.ex.exit_tick(e.clock())
        d2 = e.sent()[3]
        self.assertEqual((d2["priority"], sell_args(d2)[1]), (150_000, quote_out(sell_args(d2)[0]) * 6500 // 10_000))  # rung 2
        e.clock.t += 2_100
        e.ex.exit_tick(e.clock())
        self.assertEqual(len(e.rpc.sent), 4)  # the top rung is not re-signed forever; it is rebroadcast
        self.assertEqual(len({x["signature"] for x in e.sent()[1:]}), 3)

    def test_a_status_seen_stops_the_timed_supersede(self):
        e = self.env()
        e.open_position()
        e.at_slot(e.plan()["send_slot"])
        e.ex.state.pending[MINT]["confirm_seen_ms"] = e.clock()  # the network has it
        e.clock.t += 2_500
        e.ex.exit_tick(e.clock())
        self.assertEqual(len(e.rpc.sent), 2)

    def test_a_superseded_sell_that_lands_is_the_one_resolved(self):
        e = self.env()
        pos = e.open_position()
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        first_sig = e.sent()[1]["signature"]
        e.at_slot(plan["escalate_slot"])
        self.assertEqual(len(e.ex.state.pending[MINT]["prior"]), 1)
        fee = 60_000
        res = meta_result(e.ex, MINT, delta=21_000_000 - fee + RENT, fee=fee, tok_delta=-pos["tokens"], ata_pre=RENT, ata_post=0, slot=plan["land_slot"] + 1)
        res["transaction"]["signatures"] = [first_sig]
        e.rpc.txs[first_sig] = res
        e.rpc.statuses[first_sig] = {"slot": plan["land_slot"] + 1, "confirmationStatus": "confirmed", "err": None}
        e.clock.t += e.ex.h5.status_poll_ms
        e.ex.housekeeping(e.clock())
        self.assertNotIn(MINT, e.ex.state.open)
        self.assertEqual(e.ledger("sell")[-1]["signature"], first_sig)
        self.assertEqual(e.ledger("sell_superseded_landed")[0]["signature"], first_sig)

    def test_a_superseded_sell_that_failed_on_chain_is_dropped(self):
        e = self.env()
        e.open_position()
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        first_sig = e.sent()[1]["signature"]
        e.at_slot(plan["escalate_slot"])
        e.rpc.statuses[first_sig] = {"slot": plan["land_slot"], "confirmationStatus": "confirmed", "err": {"InstructionError": [3, {"Custom": 6004}]}}
        e.clock.t += e.ex.h5.status_poll_ms
        e.ex.housekeeping(e.clock())
        self.assertEqual(e.ex.state.pending[MINT]["prior"], [])
        self.assertIn(MINT, e.ex.state.open)

    def test_a_sell_whose_status_shows_an_error_moves_to_the_next_rung_without_waiting_for_meta(self):
        e = self.env()
        e.open_position()
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        p = e.ex.state.pending[MINT]
        e.rpc.statuses[p["signature"]] = {"slot": plan["land_slot"], "confirmationStatus": "confirmed", "err": {"InstructionError": [3, {"Custom": 6004}]}}
        e.ex.advance_pending()  # getTransaction has nothing yet (no entry in rpc.txs)
        self.assertNotIn(MINT, e.ex.state.pending)
        self.assertEqual(e.ex.state.open[MINT]["sell_attempts"], 1)
        row = e.ledger("sell")[-1]
        self.assertEqual((row["landed"], row["fail_class"], row["cost_lamports"]), (False, "slippage_exceeded", 60_000))  # the estimated fee: base + 55k
        e.at_slot(plan["send_slot"] + 4)
        self.assertEqual(len(e.rpc.sent), 3)  # the next rung is out at once

    def test_the_deadline_fires_on_the_wall_clock_when_the_slot_clock_is_unknown(self):
        e = self.env()
        e.open_position()
        plan = e.plan()
        e.ex.slots = h.SlotClock()  # no getSlot has ever succeeded
        e.rpc.slot_fails = True
        e.clock.t = plan["send_wall_ms"] - 1_000
        e.ex.exit_tick(e.clock())
        self.assertEqual(len(e.rpc.sent), 1)  # before the wall stage: nothing
        e.clock.t = plan["send_wall_ms"]
        e.ex.exit_tick(e.clock())
        self.assertEqual(len(e.rpc.sent), 2)  # the timed exit goes out on the wall clock
        e.clock.t = plan["deadline_wall_ms"]
        e.ex.exit_tick(e.clock())
        self.assertEqual(sell_args(e.sent()[-1])[1], h.EMERGENCY_MIN_OUT)
        self.assertIn("stuck_position", e.ex.counters.halts)

    def test_no_quote_from_the_escalation_sells_at_a_floor_of_65_percent_of_the_last_good_quote(self):
        e = self.env()
        pos = e.open_position()
        plan = e.plan()
        pos["last_quote_out"] = 25_000_000  # an arm-time quote from earlier
        e.rpc.state_fails = True  # the pool cannot be read now
        e.ex.pool_cache[MINT] = (e.ex.pool_cache[MINT][0], e.clock())
        e.at_slot(plan["send_slot"])
        self.assertEqual(len(e.rpc.sent), 1)  # before the escalation: still never blind
        e.at_slot(plan["escalate_slot"])
        d = e.sent()[1]
        self.assertEqual((sell_args(d)[1], d["priority"]), (25_000_000 * 6500 // 10_000, 150_000))

    def test_a_stuck_position_is_not_held_back_by_the_back_off_at_the_escalation(self):
        e = self.env()
        pos = e.open_position()
        plan = e.plan()
        pos.update(stuck=True, sell_attempts=9, last_sell_fail_ms=e.clock() + 10**9)  # a long back-off is running
        e.at_slot(plan["send_slot"])
        self.assertEqual(len(e.rpc.sent), 1)  # before the escalation the back-off holds
        e.at_slot(plan["escalate_slot"])
        self.assertEqual(len(e.rpc.sent), 2)  # at the escalation it is ignored
        pos["last_sell_fail_ms"] = e.clock() + 10**9
        e.ex.state.pending.pop(MINT)
        e.at_slot(plan["deadline_slot"])
        self.assertEqual(sell_args(e.sent()[-1])[1], h.EMERGENCY_MIN_OUT)


class BuyRebroadcastTests(Case):
    def test_a_buy_is_rebroadcast_for_three_seconds_and_then_left_to_expire(self):
        e = self.env()
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)
        e.clock.t += 450
        e.ex.housekeeping(e.clock())
        self.assertEqual(len(e.rpc.sent), 2)
        self.assertEqual(e.rpc.sent[-1][1], e.rpc.sent[0][1])  # the same signed tx
        e.clock.t += 3_000
        e.ex.housekeeping(e.clock())
        n = len(e.rpc.sent)
        e.clock.t += 2_500  # the base class's own 2 s rebroadcast in advance_pending passes through the same gate
        e.ex.advance_pending()
        e.ex.housekeeping(e.clock())
        self.assertEqual(len(e.rpc.sent), n)

    def test_no_buy_rebroadcast_once_stop_exists_or_live_ok_is_gone(self):
        e = self.env()
        e.fire()
        Path(e.conf["stop_file"]).write_text("")
        e.clock.t += 450
        e.ex.housekeeping(e.clock())
        self.assertEqual(len(e.rpc.sent), 1)
        Path(e.conf["stop_file"]).unlink()
        e.ex.housekeeping(e.clock())
        self.assertEqual(len(e.rpc.sent), 2)
        Path(e.conf["live_ok_file"]).unlink()
        e.clock.t += 450
        e.ex.housekeeping(e.clock())
        e.ex.advance_pending()
        self.assertEqual(len(e.rpc.sent), 2)

    def test_a_late_fill_is_out_of_rule_sold_at_once_and_halts_buys(self):
        e = self.env()
        e.fire()
        e.land_buy(slot=TRIG_SLOT + 30)  # 6 s after the trigger print: past ceil(5 s / 0.2)
        row = e.ledger("out_of_rule_entry")[0]
        self.assertEqual((row["landed_slot"], row["slots_after_trigger"]), (TRIG_SLOT + 30, 30))
        self.assertIn("out_of_rule_entry", e.ex.counters.halts)
        e.ex.exit_tick(e.clock())  # long before the plan's send slot
        d = e.sent()[1]
        self.assertEqual(sell_args(d)[1], quote_out(sell_args(d)[0]) * 8500 // 10_000)  # level 0
        e.ex.state.pending.pop(MINT)
        e.fire(mint="Other" + "1" * 38)
        self.assertEqual(e.refusals()[-1], "halt_latched:out_of_rule_entry")

    def test_an_on_time_fill_and_one_at_the_limit_are_not_out_of_rule(self):
        e = self.env()
        e.fire()
        e.land_buy(slot=TRIG_SLOT + 25)  # exactly ceil(5 s / 0.2) slots after the trigger print
        self.assertEqual((e.ledger("out_of_rule_entry"), e.ex.counters.halts), ([], {}))

    def test_a_plan_is_durable_before_the_first_send_and_recovered_for_an_open_position_without_one(self):
        e = self.env()
        seen = {}
        e.rpc.send_hook = lambda _p: seen.setdefault("plans", dict(__import__("json").loads(e.ex.counters_path.read_text())["plans"]))
        e.fire()
        self.assertIn(MINT, seen["plans"])  # on disk before the first sendTransaction
        e.land_buy()
        pos = e.ex.state.open[MINT]
        pos.pop("h5")  # as if a crash had cut the position's plan off
        e.ex.exit_tick(e.clock())
        self.assertEqual(pos["h5"]["plan"]["send_slot"], e.ex.counters.plans.get(MINT, pos["h5"])["plan"]["send_slot"] if MINT in e.ex.counters.plans else pos["h5"]["plan"]["send_slot"])
        self.assertTrue(e.ledger("plan_recovered"))

    def test_an_open_position_with_no_plan_anywhere_is_an_incident_and_is_sold_at_the_escalated_level(self):
        e = self.env()
        e.open_position()
        e.ex.state.open[MINT].pop("h5")
        e.ex.counters.plans.clear()
        e.ex.exit_tick(e.clock())
        self.assertTrue(any(r.get("alert") == "position_without_plan" for r in e.ledger("alert")))
        d = e.sent()[1]
        self.assertEqual((d["priority"], sell_args(d)[1]), (150_000, quote_out(sell_args(d)[0]) * 6500 // 10_000))


class MoneyLimitTests(Case):
    def test_the_loss_stops_use_worst_case_exposure(self):
        stake = 20_000_000
        day = h.day_key(T0)
        # total 0.12 SOL: refused when realized - open spend - in-flight buy spend - this stake <= -0.12
        for opens, pend, realized, refused in ((0, 0, -99_999_999, False), (0, 0, -100_000_000, True),
                                               (1, 0, -79_999_999, False), (1, 0, -80_000_000, True),
                                               (1, 1, -59_999_999, False), (1, 1, -60_000_000, True)):
            sub = self.tmp / f"t{opens}{pend}{realized}"
            sub.mkdir()
            e = Env(sub)
            for i in range(opens):
                e.ex.state.open[f"o{i}"] = {"spend": stake}
            for i in range(pend):
                e.ex.state.pending[f"p{i}"] = {"kind": "buy", "spend": stake}
            e.ex.state.realized_lamports = realized
            self.assertEqual(e.ex._budget_stop(e.clock()), "total_loss_stop" if refused else None, (opens, pend, realized))
        # daily 0.08 SOL
        for opens, realized, refused in ((0, -59_999_999, False), (0, -60_000_000, True), (1, -39_999_999, False), (1, -40_000_000, True)):
            sub = self.tmp / f"d{opens}{realized}"
            sub.mkdir()
            e = Env(sub)
            for i in range(opens):
                e.ex.state.open[f"o{i}"] = {"spend": stake}
            e.ex.counters.day(day)["realized"] = realized
            self.assertEqual(e.ex._budget_stop(e.clock()), "daily_loss_stop" if refused else None, (opens, realized))

    def test_a_failed_sells_extra_cost_counts_as_at_risk(self):
        e = self.env()
        e.ex.state.open["o"] = {"spend": 20_000_000, "extra_cost": 60_000}
        e.ex.state.realized_lamports = -79_940_000  # 79.94M + 20.06M open + 20M stake = 120M
        self.assertEqual(e.ex._budget_stop(e.clock()), "total_loss_stop")

    def test_config_clamps(self):
        l = h.H5Limits.from_config({"stake_lamports": 50_000_000, "max_open": 3, "max_attempts": 300, "entry_tolerance_bps": 3000,
                                    "buy_priority_lamports": 150_000, "wallet_floor_lamports": 1})
        d = h.H5_DEFAULT
        self.assertEqual((l.stake_lamports, l.max_open, l.max_attempts, l.entry_tolerance_bps, l.buy_priority_lamports, l.sell_priority_lamports),
                         (d["stake_lamports"], d["max_open"], d["max_attempts"], d["entry_tolerance_bps"], d["buy_priority_lamports"], d["sell_priority_lamports"]))
        self.assertEqual(l.wallet_floor_lamports, 50_000_000)  # a floor config can only raise
        self.assertEqual(h.H5_MAX, h.H5_DEFAULT)

    def test_sell_retries_are_clamped_and_the_variant_is_fixed(self):
        for given, want in ((99, 10), (0, 1), (-3, 1), (5, 5)):
            sub = self.tmp / f"r{given}"
            sub.mkdir()
            e = Env(sub, sell_retries=given)
            self.assertEqual(e.ex.sell_retries, want, given)
        with self.assertRaises(ValueError):
            h.H5Limits.from_config({"trigger_variant": "fv"})
        h.H5Limits.from_config({"trigger_variant": "pv"})
        self.assertEqual(self.env().ex.trigger_variant, "pv")

    def test_the_boost_median_needs_30_pools(self):
        self.assertEqual(h.BOOST_MEDIAN_MIN_POOLS, 30)


class SlotRateTests(Case):
    def test_measured_sps_needs_60_seconds_between_points_and_a_fresh_newest_point(self):
        c = h.SlotClock()
        self.assertIsNone(c.measured_sps(0))
        for k in range(0, 31):
            c.observe(1_000 + k * 5, k * 1_000)  # 5 slots per second = 0.2 s
        self.assertIsNone(c.measured_sps(30_000))  # 30 s of history only
        for k in range(31, 91):
            c.observe(1_000 + k * 5, k * 1_000)
        self.assertAlmostEqual(c.measured_sps(90_000), 0.2, places=6)
        self.assertIsNone(c.measured_sps(90_000 + 31_000))  # the newest point is more than 30 s old

    def test_measured_sps_uses_only_the_trailing_300_seconds(self):
        c = h.SlotClock()
        for k in range(0, 400):
            wall = k * 1_000
            c.observe(int(wall / 400) if wall < 100_000 else 250 + int((wall - 100_000) / 200), wall)  # 0.4 s slots, then 0.2 s after 100 s
        self.assertAlmostEqual(c.measured_sps(399_000), 0.2, delta=0.001)

    def test_a_trigger_is_refused_without_a_measurement_and_when_its_sps_is_off_by_more_than_1_percent(self):
        e = self.env()
        e.ex.slots = h.SlotClock()
        e.fire()
        self.assertEqual(e.refusals(), ["sps_unmeasured"])
        e.seed_clock()
        e.fire(sps=0.2 * 1.02)  # the detector's rate is 2% off the one we see
        self.assertEqual(e.refusals()[-1], "sps_mismatch")
        e.fire(sps=0.2 * 1.009)
        self.assertEqual(len(e.rpc.sent), 1)  # inside 1%

    def test_a_trigger_is_refused_when_the_chain_has_moved_on_since_the_trigger_print(self):
        e = self.env()
        e.fire(trigger_slot=e.rpc.slot - 51, s0_slot=e.rpc.slot - 551)  # 51 slots = 10.2 s old on the chain, though the detector's clock is fresh
        self.assertEqual(e.refusals(), ["stale_trigger_chain"])
        e.fire(trigger_slot=e.rpc.slot - 50, s0_slot=e.rpc.slot - 550)
        self.assertEqual(len(e.rpc.sent), 1)

    def switch_world(self, old: float, new: float, switch_at: int) -> Env:
        e = self.env()
        base = TRIG_SLOT + 8

        def slot_fn(wall: int) -> int:
            if wall < switch_at:
                return base + int((wall - T0) / (old * 1000))
            return base + int((switch_at - T0) / (old * 1000)) + int((wall - switch_at) / (new * 1000))

        e.rpc.slot_fn = slot_fn
        e.seed_clock()
        return e

    def run_to_first_sell(self, e: Env, until_ms: int) -> None:
        step = 0
        while e.clock() < until_ms and len(e.rpc.sent) < 2:
            e.clock.t += 200
            step += 1
            if step % 25 == 0:
                e.ex.prewarm()
            e.ex.housekeeping(e.clock())
            e.ex.exit_tick(e.clock())

    def check_switch(self, old: float, new: float):
        e = self.switch_world(old, new, T0 + 100_000)
        trig_slot = e.rpc.slot - 8
        s0 = trig_slot - int(round(100 / old))  # the trigger is 100 s after s0, on the old slot time
        e.fire(sps=old, trigger_slot=trig_slot, s0_slot=s0)
        self.assertEqual(len(e.rpc.sent), 1, e.refusals())
        e.land_buy(slot=trig_slot + 8)
        w = T0  # the chain's true s0 wall time: walk back until the chain's slot is s0 (it is not T0 - 100 s: the trigger print is a few slots old)
        while e.rpc.slot_fn(w) > s0:
            w -= 100
        s0_wall = w
        self.run_to_first_sell(e, s0_wall + 340_000)
        self.assertEqual(len(e.rpc.sent), 2)
        sent_after_s0 = (e.ledger("sell_sent")[0]["sent_ms"] - s0_wall) / 1000.0
        return e, sent_after_s0

    def test_the_exit_survives_the_400_to_200_ms_slot_switch(self):
        e, t = self.check_switch(0.4, 0.2)
        # in slots the exit would be reached 66 s early; the wall stage holds it to about 330 s after s0 (the rule's exit, less the 0.5 s lead)
        self.assertTrue(328.5 <= t <= 330.5, t)
        self.assertTrue(e.ledger("plan_recomputed"))  # the measured rate moved more than 1% from the plan's

    def test_the_exit_survives_a_200_to_400_ms_slot_switch(self):
        e, t = self.check_switch(0.2, 0.4)
        self.assertTrue(328.5 <= t <= 330.5, t)  # in slots it would be 125 s late

    def test_an_unchanged_slot_rate_exits_where_the_slot_plan_says(self):
        e, t = self.check_switch(0.2, 0.2)
        self.assertTrue(328.5 <= t <= 330.5, t)
        self.assertEqual(e.ledger("plan_recomputed"), [])


if __name__ == "__main__":
    unittest.main()
