"""H5 executor: a trigger too old to land inside the 5 s entry window is refused before any buy is sent (PRESEND_MAX_TRIGGER_AGE_MS = 3_500).
The three stale checks (detector decision clock, block_time with its +2 s slack, the chain's own estimate) tighten together.
Fixtures from test_h5_executor."""

from __future__ import annotations

from tools import h5_executor as h
from tools.test_h5_executor import T0, Case, Env


class ConstantTests(Case):
    def test_the_limit_is_derived_from_the_entry_window_and_the_send_allowance(self):
        self.assertEqual((h.ENTRY_LATE_S, h.ENTRY_SEND_ALLOWANCE_S), (5.0, 1.5))  # the window itself is not moved
        self.assertEqual(h.PRESEND_MAX_TRIGGER_AGE_MS, 3_500)
        self.assertEqual(h.H5Limits.from_config({}).max_trigger_age_ms, 3_500)

    def test_config_above_the_limit_is_clamped_and_below_it_is_honoured(self):
        for v in (3_501, 5_000, 10_000, 10**9):
            self.assertEqual(h.H5Limits.from_config({"max_trigger_age_ms": v}).max_trigger_age_ms, 3_500, v)
        for v in (1_000, 2_000, 3_500):
            self.assertEqual(h.H5Limits.from_config({"max_trigger_age_ms": v}).max_trigger_age_ms, v, v)

    def test_a_shortened_config_value_binds_the_executor(self):
        e = self.env(max_trigger_age_ms=2_000)
        e.fire(decision_ms=T0 - 2_100)
        self.assertEqual(e.refusals(), ["stale_trigger"])
        e.fire(decision_ms=T0 - 1_900)
        self.assertEqual(len(e.rpc.sent), 1)


class PresendAgeTests(Case):
    def fresh(self, name: str) -> Env:
        sub = self.tmp / name
        sub.mkdir()
        return Env(sub)

    def test_the_event_a_trigger_25_slots_old_on_the_chain_at_sps_0_2187_is_refused_before_send(self):
        e = self.env()
        e.fire(sps=0.2187173840025174, trigger_slot=e.rpc.slot - 25, s0_slot=e.rpc.slot - 525)
        self.assertEqual(e.refusals(), ["stale_trigger_chain"])  # a skip row, and
        self.assertEqual(e.rpc.sent, [])  # no buy
        self.assertEqual((e.ex.state.attempts, e.ledger("decision"), e.ledger("buy_sent")), (0, [], []))

    def test_decision_clock_3_4_s_passes_and_3_6_s_is_refused(self):
        e = self.fresh("d34")
        e.fire(decision_ms=T0 - 3_400)
        self.assertEqual((e.refusals(), len(e.rpc.sent)), ([], 1))
        e = self.fresh("d35")
        e.fire(decision_ms=T0 - 3_500)  # exactly the limit passes (the check is strictly greater)
        self.assertEqual((e.refusals(), len(e.rpc.sent)), ([], 1))
        e = self.fresh("d36")
        e.fire(decision_ms=T0 - 3_600)
        self.assertEqual((e.refusals(), e.rpc.sent), (["stale_trigger"], []))

    def test_block_time_3_4_s_passes_and_3_6_s_is_refused_after_its_2_s_slack(self):
        # block_time is whole seconds, the check is now - block_time > 3.5 + 2.0 s. Put the clock .400 s and .600 s into a second: 5.4 s and 5.6 s.
        base = T0 - T0 % 1000
        e = self.fresh("bt400")
        e.set_time(base + 400)
        e.fire(block_time=base // 1000 - 5)  # 5.4 s old = 3.4 s plus the 2 s slack
        self.assertEqual((e.refusals(), len(e.rpc.sent)), ([], 1))
        e = self.fresh("bt600")
        e.set_time(base + 600)
        e.fire(block_time=base // 1000 - 5)  # 5.6 s old = 3.6 s plus the 2 s slack
        self.assertEqual((e.refusals(), e.rpc.sent), (["stale_trigger"], []))

    def test_chain_estimate_3_4_s_passes_and_is_refused_past_the_rounded_up_slot_limit(self):
        # The limit is ceil(3.5 s / sps) slots: 18 at sps 0.2 (3.6 s). A slot count cannot split a slot, so the chain check admits up to one slot over 3.5 s.
        self.assertEqual(h.ceil_slots(3.5, 0.2), 18)
        e = self.fresh("c17")
        e.fire(trigger_slot=e.rpc.slot - 17, s0_slot=e.rpc.slot - 517)  # 3.4 s
        self.assertEqual((e.refusals(), len(e.rpc.sent)), ([], 1))
        e = self.fresh("c18")
        e.fire(trigger_slot=e.rpc.slot - 18, s0_slot=e.rpc.slot - 518)  # 3.6 s: the rounded-up limit itself passes
        self.assertEqual((e.refusals(), len(e.rpc.sent)), ([], 1))
        e = self.fresh("c19")
        e.fire(trigger_slot=e.rpc.slot - 19, s0_slot=e.rpc.slot - 519)  # 3.8 s
        self.assertEqual((e.refusals(), e.rpc.sent), (["stale_trigger_chain"], []))

    def test_chain_boundary_at_the_events_sps_is_17_slots_pass_18_refused(self):
        sps = 0.2187173840025174
        self.assertEqual((h.ceil_slots(3.5, sps), h.ceil_slots(5.0, sps)), (17, 23))  # the pre-send limit and the entry window, in slots
        e = self.fresh("e17")
        e.fire(sps=sps, trigger_slot=e.rpc.slot - 17, s0_slot=e.rpc.slot - 517)
        self.assertNotIn("stale_trigger_chain", e.refusals())  # (an sps_mismatch against the fixture's 0.2 chain may follow; the stale check is what is pinned)
        e = self.fresh("e18")
        e.fire(sps=sps, trigger_slot=e.rpc.slot - 18, s0_slot=e.rpc.slot - 518)
        self.assertEqual((e.refusals(), e.rpc.sent), (["stale_trigger_chain"], []))
