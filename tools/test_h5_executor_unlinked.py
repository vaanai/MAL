"""H5 executor G5: a trigger whose own predecessor print may be missing (base_breaks_unresolved >= 1 with a settled count of 0) is traded, marked on
the decision row, counted per hour, and a halt when it is more than 15% of landed buys (from 20 landed). h5_reconcile leaves it out of the sim
match (tools/test_h5_reconcile.py). Fixtures: test_h5_executor, test_h5_executor_shadow (the vendored #477 records)."""

from __future__ import annotations

import unittest
from pathlib import Path

from tools import h5_executor as h
from tools.test_h5_executor import Case, Env
from tools.test_h5_executor_shadow import shadow_trigger

UNLINKED = {"base_breaks_unresolved": 1, "base_breaks_unresolved_settled": 0}
LINKED = {"base_breaks_unresolved": 0, "base_breaks_unresolved_settled": 0}


class PreUnlinkedTests(Case):
    def fire_shadow(self, e: Env, **kw) -> None:
        t, bad = h.parse_shadow_trigger(shadow_trigger(e.clock, **kw))
        self.assertIsNone(bad)
        e.ex.handle_trigger(t)

    def sub(self, name: str) -> Path:
        d = self.tmp / name
        d.mkdir()
        return d

    def land(self, e: Env, n: int, unlinked: int) -> None:
        for i in range(n):
            e.ex._note_landed_buy(UNLINKED if i < unlinked else LINKED)

    def test_the_helper(self):
        for unresolved, settled, want in ((1, 0, True), (2, 0, True), (0, 0, False), (None, 0, False), (1, 1, False), (1, None, False), (True, 0, False),
                                          (1, False, False), (1.0, 0, False)):
            self.assertIs(h.pre_unlinked(unresolved, settled), want, (unresolved, settled))

    def test_it_is_accepted_and_the_decision_row_says_so(self):
        e = self.env()
        self.fire_shadow(e, **UNLINKED)
        self.assertEqual((e.refusals(), len(e.rpc.sent)), ([], 1))  # manager ruling for the canary: still traded
        self.assertIs(e.ledger("decision")[0]["trigger_pre_unlinked"], True)
        e2 = Env(self.sub("linked"))
        self.fire_shadow(e2, **LINKED)
        self.assertIs(e2.ledger("decision")[0]["trigger_pre_unlinked"], False)

    def test_an_hourly_alert_carries_the_count(self):
        e = self.env()
        self.fire_shadow(e, **UNLINKED)
        self.assertEqual(e.alerts("trigger_pre_unlinked"), [])  # at the turn of the hour, not before
        hour_start = e.clock() // 3_600_000 * 3_600_000
        e.jump(3_600_000)
        e.ex.prewarm()  # the slow loop rolls the hour whether or not a trigger came
        alerts = e.alerts("trigger_pre_unlinked")
        self.assertEqual([(a["count"], a["accepted"], a["hour_start_ms"]) for a in alerts], [(1, 1, hour_start)])
        self.assertEqual(e.ledger("refusal_counts")[-1]["pre_unlinked"], 1)
        e.jump(3_600_000)
        e.ex.prewarm()
        self.assertEqual(len(e.alerts("trigger_pre_unlinked")), 1)  # an hour with none does not alert

    def test_a_landed_pre_unlinked_buy_is_counted_and_survives_a_restart(self):
        e = self.env()
        self.fire_shadow(e, **UNLINKED)
        e.land_buy()
        self.assertEqual((e.ex.counters.landed_buys, e.ex.counters.pre_unlinked_landed), (1, 1))
        e.ex = e.build()
        self.assertEqual((e.ex.counters.landed_buys, e.ex.counters.pre_unlinked_landed), (1, 1))
        self.assertEqual(e.ex.counters.halts, {})  # one landed buy is not "at least 20"

    def test_the_share_halt_needs_twenty_landed_buys_and_more_than_15_percent(self):
        e = self.env()
        self.land(e, 19, 19)  # every landed buy so far is pre-unlinked, but fewer than 20 have landed
        self.assertEqual(e.ex.counters.halts, {})
        e2 = Env(self.sub("fifteen"))
        self.land(e2, 20, 3)  # 15% exactly is not "more than 15%"
        self.assertEqual(e2.ex.counters.halts, {})
        self.land(e2, 1, 0)  # 3 of 21
        self.assertEqual(e2.ex.counters.halts, {})
        e3 = Env(self.sub("twenty"))
        self.land(e3, 20, 4)  # 20%
        self.assertEqual(list(e3.ex.counters.halts), ["pre_unlinked_share"])
        d = e3.ex.counters.halts["pre_unlinked_share"]
        self.assertEqual((d["landed"], d["pre_unlinked"], d["share"]), (20, 4, 0.2))
        self.assertEqual(len(e3.alerts("halt_pre_unlinked_share")), 1)

    def test_the_halt_is_latched_refuses_buys_and_survives_a_restart(self):
        e = self.env()
        self.land(e, 20, 5)
        self.fire_shadow(e, **LINKED)
        self.assertEqual((e.refusals(), e.rpc.sent), (["halt_latched:pre_unlinked_share"], []))
        e.ex = e.build()
        self.assertIn("pre_unlinked_share", e.ex.counters.halts)


if __name__ == "__main__":
    unittest.main()
