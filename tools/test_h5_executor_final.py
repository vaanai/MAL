"""H5 executor: the final review of ab0b1cd (R1 wall anchor, R4-R6 sell bookkeeping, R8 wall-time lateness, R9 buy window).
Fixtures: test_h5_executor (the consistent fake chain), test_h5_executor_shadow (the vendored #477 records)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from tools import h5_executor as h
from tools import probe_executor as pe
from tools.test_h5_executor import BASE0, MINT, POOL, Q, RENT, S0, SPS, T0, TRIG_SLOT, V, Case, Env
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

    def test_an_s0_that_the_feed_delivered_more_than_1500_ms_later_than_the_trigger_print_is_refused(self):
        trig_true = T0 - 1_600  # the trigger print's true wall time on the fake chain
        for extra, ok in ((10_000, False), (1_501, False), (1_500, True), (0, True)):
            sub = self.tmp / f"l{extra}"
            sub.mkdir()
            e = Env(sub)
            # both prints were heard 2 s late by the feed (its own lag); the s0 print `extra` ms later still: a backlog relative to the same feed
            e.ex.handle_trigger(parse(e.clock, s0_t_recv_ms=TRUE_S0_WALL + 2_000 + extra, t_recv_ms=trig_true + 2_000))
            self.assertEqual(len(e.rpc.sent) == 1, ok, (extra, e.refusals()))
            if not ok:
                self.assertEqual((e.refusals(), e.rpc.sent), (["s0_recv_late"], []))

    def test_a_constant_feed_lag_on_every_print_is_accepted(self):
        trig_true = T0 - 1_600
        for lag in (1_200, 1_900, 2_500, 2_600, 4_000):  # the shadow hears at `confirmed`, we map `processed`: p10 of the lag is about 2.6 s
            sub = self.tmp / f"c{lag}"
            sub.mkdir()
            e = Env(sub)
            e.ex.handle_trigger(parse(e.clock, s0_t_recv_ms=TRUE_S0_WALL + lag, t_recv_ms=trig_true + lag))
            self.assertEqual((len(e.rpc.sent), e.refusals()), (1, []), lag)
            row = e.ledger("decision")[0]["anchor"]
            self.assertEqual(row["anchor_source"], "own_getslot_history")
            self.assertAlmostEqual(row["s0_feed_lag_ms"], lag, delta=200)
            self.assertAlmostEqual(row["s0_relative_lag_ms"], 0, delta=200)

    def test_an_s0_lagging_10_s_more_than_the_trigger_is_refused_even_with_a_big_constant_lag(self):
        trig_true = T0 - 1_600
        e = self.env()
        e.ex.handle_trigger(parse(e.clock, s0_t_recv_ms=TRUE_S0_WALL + 12_500, t_recv_ms=trig_true + 2_500))
        self.assertEqual(e.refusals(), ["s0_recv_late"])

    def test_the_exit_is_anchored_on_our_own_mapping_not_on_the_detectors_claim(self):
        for lag in (0, 1_400, -3_000):  # up to 1.5 s late is believed (and ignored); early is just wrong in the safe direction
            sub = self.tmp / f"a{lag}"
            sub.mkdir()
            e = Env(sub)
            e.ex.handle_trigger(parse(e.clock, s0_t_recv_ms=TRUE_S0_WALL + lag, t_recv_ms=T0 - 1_600))
            self.assertEqual(len(e.rpc.sent), 1, (lag, e.refusals()))
            e.land_buy()
            plan = e.plan()
            self.assertAlmostEqual(plan["s0_wall_ms"], TRUE_S0_WALL, delta=200)  # our mapping, whatever the claim
            self.assertTrue(328.5 <= self.run_to_first_sell(e) <= 330.5, lag)  # the send stays at about 329.5 s

    def test_an_s0_older_than_our_getslot_history_is_refused_not_anchored_on_block_time(self):
        e = self.env()
        now = e.clock()
        e.ex.slots = h.SlotClock()
        for k in range(35, -1, -1):  # 70 s of history: the measured rate is fine, s0 (101.6 s back) is not covered
            e.ex.slots.observe(e.rpc.slot_at(now - k * 2_000), now - k * 2_000)
        self.assertIsNone(e.ex.slots.wall_of_slot(S0, SPS))
        for kw in ({}, {"block_time": None}, {"s0_t_recv_ms": now - 100_000}):  # block time or a plausible claim does not rescue it
            e.ex.handle_trigger(parse(e.clock, **kw))
        self.assertEqual((e.refusals(), e.rpc.sent), (["s0_before_history"] * 3, []))
        # a flat row with no claim at all is refused the same way
        flat = {"schema": "h5_intent_v1", "synthetic": False, "mint": MINT, "pool": POOL, "s0_slot": S0, "sps": SPS, "trigger_slot": TRIG_SLOT, "q_lamports": Q,
                "base_reserve": BASE0, "v_lamports": V, "decision_ms": now}
        t, bad = h.parse_trigger(flat)
        self.assertIsNone(bad)
        e.ex.handle_trigger(t)
        self.assertEqual(e.refusals()[-1], "s0_before_history")

    def test_the_decision_row_says_which_anchor_was_used(self):
        e = self.env()
        e.ex.handle_trigger(parse(e.clock))
        self.assertEqual(e.ledger("decision")[0]["anchor"]["anchor_source"], "own_getslot_history")

    def test_more_than_three_anchor_refusals_in_an_hour_raise_an_alert_once(self):
        e = self.env()
        now = e.clock()
        e.ex.slots = h.SlotClock()
        for k in range(35, -1, -1):
            e.ex.slots.observe(e.rpc.slot_at(now - k * 2_000), now - k * 2_000)
        for _ in range(3):
            e.ex.handle_trigger(parse(e.clock))
        alerts = lambda: [r for r in e.ledger("alert") if r.get("alert") == "s0_anchor_refusals"]  # noqa: E731
        self.assertEqual(alerts(), [])  # three is not more than three
        for _ in range(3):
            e.ex.handle_trigger(parse(e.clock))
        self.assertEqual((len(alerts()), alerts()[0]["count"]), (1, 4))  # the fourth raised it; not repeated within the hour

    def test_one_refusal_counts_row_per_hour_makes_the_refusal_rate_visible(self):
        e = self.env()
        e.ex._bad_intent(shadow_trigger(e.clock), "bad_intent:base_breaks_unresolved")  # refused before it is a trigger
        e.fire(decision_ms=T0 - 10_001)  # refused: stale_trigger
        e.fire(decision_ms=T0 - 10_002)  # refused: stale_trigger
        e.fire()  # accepted
        self.assertEqual(e.ledger("refusal_counts"), [])  # nothing yet: the hour has not turned
        e.jump(3_600_000)
        e.ex.prewarm()
        rows = e.ledger("refusal_counts")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["accepted"], rows[0]["refused"], rows[0]["by_reason"]),
                         (1, 3, {"bad_intent:base_breaks_unresolved": 1, "stale_trigger": 2}))
        e.ex.prewarm()
        self.assertEqual(len(e.ledger("refusal_counts")), 1)  # once per hour, not once per look

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

    def second_rung(self) -> tuple[str, str]:
        """Rung 0 is out (setUp); 2.1 s with no status supersedes it with rung 1. Returns (rung 0, rung 1) signatures."""
        first = self.e.sent()[1]["signature"]
        self.e.clock.t += 2_100
        self.e.ex.exit_tick(self.e.clock())
        second = [d["signature"] for d in self.e.sent()[1:] if d["signature"] != first][0]
        p = self.e.ex.state.pending[MINT]
        self.assertEqual((p["signature"], [x["signature"] for x in p["prior"]]), (second, [first]))
        return first, second

    def test_a_superseded_sell_that_failed_does_not_disable_the_timed_supersede_of_the_current_one(self):
        first, second = self.second_rung()
        self.e.rpc.statuses[first] = {"slot": self.plan["land_slot"] + 3, "confirmationStatus": "confirmed", "err": ERR}  # rung 0 fails late
        before = self.e.ex.state.realized_lamports
        self.poll()
        p = self.e.ex.state.pending[MINT]
        self.assertEqual(p["prior"], [])  # its fee is booked and it is dropped ...
        self.assertEqual(self.e.ex.state.realized_lamports, before - (5_000 + 55_000))
        self.assertNotIn("status_seen_ms", p)  # ... and the record's flag is not set by an error
        self.e.clock.t += 2_500  # rung 1 has had no status for 3 s
        self.e.ex.exit_tick(self.e.clock())
        new = {d["signature"] for d in self.e.sent()[1:]} - {first, second}
        self.assertEqual(len(new), 1)  # so the ladder goes on: rung 2

    def test_a_non_error_status_on_a_superseded_sell_does_block_the_timed_supersede(self):
        first, second = self.second_rung()
        self.e.rpc.statuses[first] = {"slot": self.plan["land_slot"], "confirmationStatus": "processed", "err": None}  # rung 0 is landing
        self.poll()
        self.assertIn("status_seen_ms", self.e.ex.state.pending[MINT])
        self.e.clock.t += 3_000
        self.e.ex.exit_tick(self.e.clock())
        self.assertEqual({d["signature"] for d in self.e.sent()[1:]}, {first, second})  # no rung 2

    def test_a_status_that_disappears_on_a_later_poll_clears_the_flag_and_the_timed_supersede_goes_on(self):
        first, second = self.second_rung()
        self.e.rpc.statuses[first] = {"slot": self.plan["land_slot"], "confirmationStatus": "processed", "err": None}  # landing on a fork ...
        self.poll()
        self.assertIn("status_seen_ms", self.e.ex.state.pending[MINT])
        self.e.clock.t += 3_000
        self.e.ex.exit_tick(self.e.clock())
        self.assertEqual({d["signature"] for d in self.e.sent()[1:]}, {first, second})  # blocked while the status is there
        del self.e.rpc.statuses[first]  # ... the fork is dropped: the status is gone
        self.poll()
        p = self.e.ex.state.pending[MINT]
        self.assertNotIn("status_seen_ms", p)
        self.assertEqual(len(self.e.ledger("sell_status_vanished")), 1)
        self.e.ex.exit_tick(self.e.clock())
        new = {d["signature"] for d in self.e.sent()[1:]} - {first, second}
        self.assertEqual(len(new), 1)  # rung 2 goes out: the ladder is not stuck until the escalation

    def test_the_flag_stays_while_any_polled_signature_still_has_a_non_error_status(self):
        first, second = self.second_rung()
        self.e.rpc.statuses[first] = {"slot": self.plan["land_slot"], "confirmationStatus": "processed", "err": None}
        self.poll()
        self.e.rpc.statuses[second] = {"slot": self.plan["land_slot"], "confirmationStatus": "processed", "err": None}  # the current one shows up too
        del self.e.rpc.statuses[first]  # the old one vanishes
        self.poll()
        self.assertIn("status_seen_ms", self.e.ex.state.pending[MINT])  # one live signature still has a status
        self.assertEqual(self.e.ledger("sell_status_vanished"), [])

    def test_a_failed_poll_does_not_clear_the_flag(self):
        first, _second = self.second_rung()
        self.e.rpc.statuses[first] = {"slot": self.plan["land_slot"], "confirmationStatus": "processed", "err": None}
        self.poll()
        with mock.patch.object(self.e.ex, "rpc", side_effect=pe.RpcError("timeout")):
            self.e.ex._poll_priors()  # no answer is not "the status is gone"
        self.assertIn("status_seen_ms", self.e.ex.state.pending[MINT])

    def test_promotion_clears_the_flags_until_the_promoted_signature_shows_its_own_status(self):
        first, second = self.second_rung()
        self.e.rpc.statuses[first] = {"slot": self.plan["land_slot"], "confirmationStatus": "processed", "err": None}
        self.poll()
        self.assertIn("status_seen_ms", self.e.ex.state.pending[MINT])  # rung 0 has a non-error status
        del self.e.rpc.statuses[first]  # (the promoted one's status is looked at again below)
        self.e.rpc.statuses[second] = {"slot": self.plan["land_slot"], "confirmationStatus": "processed", "err": ERR}  # the current one fails
        self.e.ex.advance_pending()
        p = self.e.ex.state.pending[MINT]
        self.assertEqual(p["signature"], first)  # promoted
        self.assertNotIn("status_seen_ms", p)  # the flags belonged to the signature that failed
        self.assertNotIn("confirm_seen_ms", p)
        self.e.rpc.statuses[first] = {"slot": self.plan["land_slot"], "confirmationStatus": "processed", "err": None}  # its own non-error status
        self.poll()
        self.assertIn("status_seen_ms", self.e.ex.state.pending[MINT])

    def test_a_confirmed_superseded_sell_swapped_in_carries_its_own_status(self):
        first, second = self.second_rung()
        self.e.rpc.statuses[first] = {"slot": self.plan["land_slot"], "confirmationStatus": "confirmed", "err": None}  # (no meta served yet)
        self.poll()
        p = self.e.ex.state.pending[MINT]
        self.assertEqual((p["signature"], [x["signature"] for x in p["prior"]]), (first, [second]))
        self.assertIn("status_seen_ms", p)

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
