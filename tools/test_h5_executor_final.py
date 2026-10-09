"""H5 executor: the final review of ab0b1cd (R1 wall anchor, R4-R6 sell bookkeeping, R8 wall-time lateness, R9 buy window).
Fixtures: test_h5_executor (the consistent fake chain), test_h5_executor_shadow (the vendored #477 records)."""

from __future__ import annotations

from pathlib import Path

from tools import h5_executor as h
from tools.test_h5_executor import BASE0, MINT, RENT, S0, SPS, T0, TRIG_SLOT, Case, Env
from tools.test_h5_executor_shadow import shadow_trigger
from tools.test_probe_live import meta_result

TRUE_S0_WALL = T0 - (TRIG_SLOT + 8 - S0) * 200  # the fake chain: 200 ms slots, TRIG_SLOT + 8 at T0 (101.6 s after s0)
ERR = {"InstructionError": [3, {"Custom": 6004}]}


def parse(c, **kw):
    t, bad = h.parse_shadow_trigger(shadow_trigger(c, **kw))
    assert t is not None, bad
    return t


class WallAnchorTests(Case):
    def run_to_first_sell(self, e: Env) -> float:
        step = 0
        while len(e.rpc.sent) < 2 and e.clock() < TRUE_S0_WALL + 345_000:
            e.clock.t += 200
            step += 1
            if step % 25 == 0:
                e.ex.prewarm()
            e.ex.housekeeping(e.clock())
            e.ex.exit_tick(e.clock())
        self.assertEqual(len(e.rpc.sent), 2)
        return (e.ledger("sell_sent")[0]["sent_ms"] - TRUE_S0_WALL) / 1000.0

    def test_the_fixture_s0_time_is_the_true_one(self):
        t = parse(Env(self.tmp).clock)
        self.assertEqual(t.s0_wall_ms, TRUE_S0_WALL)

    def test_a_late_s0_receive_time_is_refused(self):
        for lag in (10_000, 1_501):
            sub = self.tmp / f"l{lag}"
            sub.mkdir()
            e = Env(sub)
            e.ex.handle_trigger(parse(e.clock, s0_t_recv_ms=TRUE_S0_WALL + lag))
            self.assertEqual((e.refusals(), e.rpc.sent), (["s0_recv_late"], []), lag)

    def test_the_exit_is_anchored_on_our_own_mapping_not_on_the_detectors_claim(self):
        for lag in (0, 1_400, -3_000):  # up to 1.5 s late is believed (and ignored); early is just wrong in the safe direction
            sub = self.tmp / f"a{lag}"
            sub.mkdir()
            e = Env(sub)
            e.ex.handle_trigger(parse(e.clock, s0_t_recv_ms=TRUE_S0_WALL + lag))
            self.assertEqual(len(e.rpc.sent), 1, (lag, e.refusals()))
            e.land_buy()
            plan = e.plan()
            self.assertAlmostEqual(plan["s0_wall_ms"], TRUE_S0_WALL, delta=200)  # our mapping, whatever the claim
            self.assertTrue(328.5 <= self.run_to_first_sell(e) <= 330.5, lag)  # the send stays at about 329.5 s

    def test_when_our_history_does_not_reach_s0_the_block_time_stands_in_and_a_claim_with_nothing_to_check_it_against_is_refused(self):
        e = self.env()
        now = e.clock()
        e.ex.slots = h.SlotClock()
        for k in range(35, -1, -1):  # 70 s of history: measured sps is fine, s0 (101.6 s back) is not covered
            e.ex.slots.observe(e.rpc.slot_at(now - k * 2_000), now - k * 2_000)
        self.assertIsNone(e.ex.slots.wall_of_slot(S0, SPS))
        e.ex.handle_trigger(parse(e.clock))  # block_time says s0 was 100.0 s back: the claim (101.6 s) is not late
        self.assertEqual(len(e.rpc.sent), 1)
        sub = self.tmp / "b"
        sub.mkdir()
        e2 = Env(sub)
        e2.ex.slots = h.SlotClock()
        for k in range(35, -1, -1):
            e2.ex.slots.observe(e2.rpc.slot_at(now - k * 2_000), now - k * 2_000)
        e2.ex.handle_trigger(parse(e2.clock, s0_t_recv_ms=now - 96_000))  # 4 s later than block_time allows
        self.assertEqual(e2.refusals(), ["s0_recv_late"])
        sub = self.tmp / "c"
        sub.mkdir()
        e3 = Env(sub)
        e3.ex.slots = h.SlotClock()
        for k in range(35, -1, -1):
            e3.ex.slots.observe(e3.rpc.slot_at(now - k * 2_000), now - k * 2_000)
        e3.ex.handle_trigger(parse(e3.clock, block_time=None))
        self.assertEqual(e3.refusals(), ["s0_unverifiable"])

    def test_wall_of_slot_interpolates_extends_and_knows_what_it_does_not_know(self):
        c = h.SlotClock()
        for k in range(10):
            c.observe(1_000 + k * 10, 100_000 + k * 2_000)  # 5 slots per second
        self.assertEqual(c.wall_of_slot(1_000), 100_000)
        self.assertEqual(c.wall_of_slot(1_025), 105_000)  # between two points
        self.assertEqual(c.wall_of_slot(999), None)  # before what we have seen
        self.assertEqual(c.wall_of_slot(1_100), None)  # beyond it, with no rate to extend by
        self.assertEqual(c.wall_of_slot(1_100, 0.2), 118_000 + 10 * 200)  # newest point (1_090 at 118_000) plus 10 slots at 0.2 s


class SellBookkeepingTests(Case):
    def setUp(self):
        super().setUp()
        self.e = self.env()
        self.pos = self.e.open_position()
        self.plan = self.e.plan()
        self.e.at_slot(self.plan["send_slot"])
        self.delta = 21_000_000 - 60_000 + RENT

    def landed_meta(self, sig: str, slot: int) -> None:
        res = meta_result(self.e.ex, MINT, delta=self.delta, fee=60_000, tok_delta=-self.pos["tokens"], ata_pre=RENT, ata_post=0, slot=slot)
        res["transaction"]["signatures"] = [sig]
        self.e.rpc.txs[sig] = res
        self.e.rpc.statuses[sig] = {"slot": slot, "confirmationStatus": "confirmed", "err": None}

    def poll(self):
        self.e.clock.t += self.e.ex.h5.status_poll_ms
        self.e.ex.housekeeping(self.e.clock())

    def test_a_processed_status_ends_the_timed_supersede(self):
        sig = self.e.sent()[1]["signature"]
        self.e.rpc.statuses[sig] = {"slot": self.plan["land_slot"], "confirmationStatus": "processed", "err": None}
        self.poll()
        self.assertIn("status_seen_ms", self.e.ex.state.pending[MINT])
        self.e.clock.t += 3_000
        self.e.ex.exit_tick(self.e.clock())
        self.assertEqual({d["signature"] for d in self.e.sent()[1:]}, {sig})  # no rung 1: only rebroadcasts of the one sell

    def test_an_error_on_the_current_sell_with_a_superseded_one_live_books_the_fee_and_promotes_it(self):
        first = self.e.sent()[1]["signature"]
        self.e.at_slot(self.plan["escalate_slot"])
        second = self.e.sent()[2]["signature"]
        self.assertEqual([x["signature"] for x in self.e.ex.state.pending[MINT]["prior"]], [first])
        self.e.rpc.statuses[second] = {"slot": self.plan["land_slot"], "confirmationStatus": "processed", "err": ERR}  # the first one landed, so this fails
        before = self.e.ex.state.realized_lamports
        self.e.ex.advance_pending()
        p = self.e.ex.state.pending[MINT]  # NOT finished: the record and the position are intact
        self.assertEqual((p["signature"], p["prior"]), (first, []))
        self.assertIn(MINT, self.e.ex.state.open)
        self.assertEqual(self.e.ex.state.realized_lamports, before - (5_000 + 150_000))
        self.assertEqual(self.e.ex.state.open[MINT]["extra_cost"], 155_000)
        self.assertEqual(self.e.ledger("sell"), [])  # _finish_sell was not called
        self.landed_meta(first, self.plan["land_slot"] + 1)  # now the first one confirms
        self.e.ex.advance_pending()
        self.assertNotIn(MINT, self.e.ex.state.open)
        self.assertEqual(self.e.ledger("sell")[-1]["signature"], first)
        # total realized = what the wallet gained less the buy cost, less the failed rung's fee once (booked when it failed, not again at the close)
        self.assertEqual(self.e.ex.state.realized_lamports, before - 155_000 + (self.delta - self.pos["buy_cost_lamports"]))

    def test_every_losing_supersede_pays_its_fee_into_realized_exactly_once(self):
        first = self.e.sent()[1]["signature"]
        self.e.at_slot(self.plan["escalate_slot"])
        second = self.e.sent()[2]["signature"]
        self.e.at_slot(self.plan["deadline_slot"])
        third = self.e.sent()[3]["signature"]
        self.assertEqual([x["signature"] for x in self.e.ex.state.pending[MINT]["prior"]], [first, second])
        before = self.e.ex.state.realized_lamports
        self.landed_meta(first, self.plan["land_slot"] + 1)  # the first rung lands ...
        self.e.rpc.statuses[second] = {"slot": self.plan["land_slot"] + 1, "confirmationStatus": "confirmed", "err": ERR}  # ... the others fail
        self.e.rpc.statuses[third] = {"slot": self.plan["land_slot"] + 1, "confirmationStatus": "confirmed", "err": ERR}
        self.poll()
        self.assertNotIn(MINT, self.e.ex.state.open)
        self.assertEqual(self.e.ledger("sell")[-1]["signature"], first)
        fees = (5_000 + 150_000) + (5_000 + 150_000)  # the 150k rung and the 150k emergency
        self.assertEqual(self.e.ex.state.realized_lamports, before + (self.delta - self.pos["buy_cost_lamports"]) - fees)
        booked = [r["fee_lamports"] for r in self.e.ledger("sell_fee_booked")]
        self.assertEqual(sorted(booked), [155_000, 155_000])
        self.assertEqual(self.e.ex.counters.days[h.day_key(self.e.clock())]["realized"], self.e.ex.state.realized_lamports)  # the day counter agrees

    def test_a_prior_with_no_status_at_resolution_is_ledgered_unresolved_not_booked(self):
        first = self.e.sent()[1]["signature"]
        self.e.at_slot(self.plan["escalate_slot"])
        second = self.e.sent()[2]["signature"]
        before = self.e.ex.state.realized_lamports
        self.landed_meta(second, self.plan["land_slot"] + 1)  # the current one lands; the first has no status at all
        self.poll()
        self.assertNotIn(MINT, self.e.ex.state.open)
        self.assertEqual([r["signature"] for r in self.e.ledger("sell_prior_unresolved")], [first])
        self.assertEqual(self.e.ex.state.realized_lamports, before + (self.delta - self.pos["buy_cost_lamports"]))

    def test_late_sells_and_the_boost_pairing_are_judged_in_wall_time(self):
        # A chain that went from 0.4 s to 0.2 s slots 100 s after s0; the plan still carries sps 0.4. In slots a landing at 334 s after s0 looks
        # very late (slot 1420 against a late_slot of 838); in wall time it is on time.
        e = self.env()
        s0, s0_wall = S0, T0 - 100_000

        def slot(w):
            return s0 + (250 + int((w - (s0_wall + 100_000)) / 200) if w >= s0_wall + 100_000 else int((w - s0_wall) / 400))

        e.ex.slots = h.SlotClock()
        for w in range(s0_wall, s0_wall + 340_000, 2_000):
            e.ex.slots.observe(slot(w), w)
        plan = h.exit_plan(s0, 0.4, e.ex.h5, s0_wall).public()
        on_time = slot(s0_wall + 334_000)
        self.assertGreater(on_time, plan["late_slot"])  # the slot arithmetic alone would call it late
        e.ex._note_sell_landing(MINT, plan, on_time, False)
        self.assertEqual((e.ex.counters.sells_late, round(e.ex.counters.sell_land_s[MINT], 1)), (0, 334.0))
        sub = self.tmp / "late"
        sub.mkdir()
        e2 = Env(sub)
        e2.ex.slots = e.ex.slots
        e2.ex._note_sell_landing(MINT, plan, slot(s0_wall + 336_000), False)
        self.assertEqual((e2.ex.counters.sells_late, list(e2.ex.counters.halts)), (1, ["late_sells_gt_5pct"]))


class BuyWindowTests(Case):
    def test_the_three_second_rebroadcast_window_runs_from_the_decision_not_from_the_send(self):
        e = self.env()
        e.fire(decision_ms=T0 - 2_000)  # decided 2 s before it was handled
        self.assertEqual(len(e.rpc.sent), 1)
        e.clock.t += 450
        e.ex.housekeeping(e.clock())
        self.assertEqual(len(e.rpc.sent), 2)  # 2.45 s after the decision
        e.clock.t += 700
        e.ex.housekeeping(e.clock())
        e.ex.advance_pending()
        self.assertEqual(len(e.rpc.sent), 2)  # 3.15 s after the decision: left to expire
        self.assertIsNone(e.ex.state.pending[MINT].get("expired_seen_ms"))


if __name__ == "__main__":
    import unittest

    unittest.main()
