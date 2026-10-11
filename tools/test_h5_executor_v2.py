"""Offline tests for the H5-BOOSTCLOCK v2 exit in tools/h5_executor.py (DEC-028 Option B; EXP-026 rule C10). OFF in the shipped build.

- the rule is pinned to tools/h5_shadow.py's bx10 and to the frozen bc_rule.py (when this host has it);
- with EXIT_MODE "v1" (the default) nothing changes: slice rows are ignored, no v2 field is written, sells go at v1's 330 s;
- with EXIT_MODE "v2" (patched here): the exit lands at the rule's trigger slot on synthetic BOOST slice sequences, never after v1's 330 s,
  missing or late rows fall back to v1's timer, and the DEC-028 s4 halts latch.
Fixtures come from test_h5_executor (fake RPC, fake clock, throwaway Keypairs). No network, no real key."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import random
from pathlib import Path
from unittest import mock

from tools import h5_executor as h
from tools import h5_shadow as shadow
from tools.test_h5_executor import MINT, POOL, S0, SPS, TRIG_SLOT, Case

BC_RULE = Path("/data/mal/hunt-1008/iter-r2/h5-boostclock-exit/bc_rule.py")
BC_RULE_SHA = "6b3de49e6ae6fda3ca868bc4d77cd9078c3e04706cf12be112dc97718382651d"
X = TRIG_SLOT + 8  # where Env.land_buy lands the buy
C = S0 + int(round(330 / SPS))  # v1's exit trigger slot
LAG = 2  # slots between a slice's slot and its arrival here (0.4 s)


def boost_schedule(sol: float, every_slots: int = 60, total: float = 17.585) -> list[tuple[int, int]]:
    """(slot, lamports): a steady BOOST TWAP from s0, `sol` per slice, the remainder in the last one."""
    out, spent, i = [], 0, 0
    budget = int(round(total * 1e9))
    lam = int(round(sol * 1e9))
    while spent < budget:
        a = min(lam, budget - spent)
        out.append((S0 + i * every_slots, a))
        spent += a
        i += 1
    return out


def slice_row(e, slot: int, lam: int, idx: int = 0, src: str = "pda", lag_ms: int = LAG * 200) -> dict:
    wall = e.clock() - (e.rpc.slot - slot) * 200  # the chain follows the fake clock at 200 ms
    return {"type": "boost_slice", "mint": MINT, "pool": POOL, "slot": slot, "tx_index": idx, "event_index": 0, "lamports": lam,
            "block_time_ms": wall, "t_recv_ms": wall + lag_ms, "boost_src": src}


def expected_trigger(sched: list[tuple[int, int]], x: int = X) -> tuple[int, float, bool]:
    t = [(s - S0) * SPS for s, _ in sched]
    a = [lam for _, lam in sched]
    tau, fired, _k, _p = h.v2_cad_exit(t, a, (x - S0) * SPS)
    return h.v2_exit_trigger_slot(S0, SPS, x, tau, fired), tau, fired


class V2Case(Case):
    mode = h.EXIT_MODE_V2

    def setUp(self):
        super().setUp()
        p = mock.patch.object(h, "EXIT_MODE", self.mode)
        p.start()
        self.addCleanup(p.stop)

    def run_until_sell(self, e, sched: list[tuple[int, int]], until: int = C + 20, deliver_from: int | None = None, lag: int = LAG) -> int | None:
        """Step the chain one slot at a time, delivering each slice `lag` slots after its slot (or not before `deliver_from`), running the
        exit scheduler; returns the slot at which the sell went out (None if none by `until`)."""
        feed = sorted(sched)
        n0 = len(e.rpc.sent)
        cur = e.rpc.slot
        while feed and feed[0][0] + lag <= cur and (deliver_from is None or cur >= deliver_from):
            e.ex.on_boost_slice(slice_row(e, *feed.pop(0)))
        while e.rpc.slot < until:
            e.clock.t += int(e.rpc.slot_ms)
            cur = e.rpc.slot
            while feed and feed[0][0] + lag <= cur and (deliver_from is None or cur >= deliver_from):
                e.ex.on_boost_slice(slice_row(e, *feed.pop(0)))
            e.ex.exit_tick(e.clock())
            if len(e.rpc.sent) > n0:
                return cur
        return None


# --- the rule: one set of steps in three places -------------------------------------------------------------------------------


class RuleTests(Case):
    def test_constants_are_the_shadows_bx10(self):
        self.assertEqual((h.V2_OFF_S, h.V2_OBS_S, h.V2_KMIN), (shadow.BX10_OFF_S, shadow.BX10_OBS_S, shadow.BX10_KMIN))
        self.assertEqual((h.V2_DONE_LAMPORTS, h.V2_BUDGET_LAMPORTS), (shadow.BX10_DONE_LAMPORTS, shadow.BOOST_BUDGET))
        self.assertEqual((h.V2_CAP_S, h.HOLD_S, shadow.EXIT_AFTER_S0_S), (330.0, 330.0, 330.0))

    @staticmethod
    def random_case(rng: random.Random) -> tuple[list[float], list[float], float]:
        n = rng.randint(0, 40)
        t, x = [], rng.uniform(-2, 30)
        for _ in range(n):
            x += rng.choice([rng.uniform(0.2, 30), 12.0, 12.06, 0.0 if rng.random() < 0.05 else 6.0])
            t.append(round(x / 0.2) * 0.2)
        t.sort()
        a = [float(rng.choice([rng.randint(1, 2_000_000_000), 700_000_000, 50_000_000])) for _ in t]
        return t, a, rng.uniform(0, 340)

    def test_exit_equals_the_shadows_bx10_exactly(self):
        rng = random.Random(1)
        for _ in range(3000):
            t, a, tmin = self.random_case(rng)
            self.assertEqual(h.v2_cad_exit(t, a, tmin), shadow.bx10_exit(t, a, tmin, shadow.EXIT_AFTER_S0_S), (t, a, tmin))

    def test_exit_equals_the_frozen_bc_rule(self):
        if not BC_RULE.is_file() or hashlib.sha256(BC_RULE.read_bytes()).hexdigest() != BC_RULE_SHA:
            self.skipTest("frozen bc_rule.py not on this host (or not the frozen bytes)")
        spec = importlib.util.spec_from_file_location("bc_rule_frozen", BC_RULE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        rng = random.Random(2)
        for _ in range(1500):
            t, a, tmin = self.random_case(rng)
            tau, fired, _k, _p = h.v2_cad_exit(t, a, tmin)
            ref_tau, ref_fired = mod.cad_exit(t, a, off=10.0, cap=330.0, tmin=tmin)
            self.assertEqual((fired, tau), (bool(ref_fired), float(ref_tau)), (t, a, tmin))

    def test_projection_and_trigger_slot_by_hand(self):
        # 0.7 SOL every 12 s: P = 12 (k - 1) + floor((17.585 - 0.7 k) / 0.7) * 12 = 288 s for every k; tau* = 278 s with k = 24 usable
        sched = boost_schedule(0.7)
        e_slot, tau, fired = expected_trigger(sched)
        self.assertTrue(fired)
        self.assertAlmostEqual(tau, 278.0, places=9)
        self.assertEqual(e_slot, S0 + 1390)
        self.assertEqual(h.v2_v1_exit_slot(S0, SPS, X), C)
        self.assertEqual(h.v2_exit_trigger_slot(S0, SPS, X, 278.0, False), C)  # no early exit -> C
        self.assertEqual(h.v2_exit_trigger_slot(S0, SPS, X, 329.99, True), C)  # ceil lands on C: clamped (not an early exit)
        self.assertEqual(h.v2_exit_trigger_slot(S0, SPS, X, 50.0, True), X + 1)  # never at or before our own landing
        self.assertEqual(h.v2_exit_trigger_slot(S0, SPS, C + 5, 50.0, True), C + 6)  # C = max(s0 + 1650, X + 1)
        # 0.5 SOL every 12 s: P = 408 s -> tau* above the cap: no early exit
        self.assertEqual(expected_trigger(boost_schedule(0.5))[2], False)
        self.assertEqual(h.v2_cad_exit([10.0, 22.0], [1e9, 1e9], 100.0), (330.0, False, 2, None))  # fewer than 3 slices


# --- off: v1 unchanged --------------------------------------------------------------------------------------------------------


class OffTests(V2Case):
    mode = h.EXIT_MODE_V1

    def test_shipped_build_is_v1(self):
        src = Path(h.__file__).read_text()
        self.assertIn('\nEXIT_MODE = EXIT_MODE_V1  #', src)

    def test_exit_mode_is_never_config(self):
        with self.assertRaisesRegex(ValueError, "exit_mode is not configurable"):
            h.H5Limits.from_config({"exit_mode": "v2"})

    def test_slices_ignored_and_v1_exit_unchanged(self):
        e = self.env()
        pos = e.open_position()
        self.assertNotIn("exit_rule", pos["h5"])
        self.assertNotIn("exit_rule", e.ex.counters.plans[MINT])
        sent_at = self.run_until_sell(e, boost_schedule(0.7))
        self.assertEqual(sent_at, e.plan()["send_slot"])  # v1's send slot (s0 + 1647 to land at s0 + 1650)
        self.assertEqual(e.ex._v2_slices, {})
        self.assertFalse([r for r in e.ledger() if str(r.get("kind", "")).startswith("v2_")])
        self.assertNotIn("exit_rule", e.ledger("sell_sent")[0])
        self.assertEqual(e.ex.state.pending[MINT]["reason"], "h5_timed_exit")
        e.land_sell(C)
        self.assertNotIn("v2_late", e.ledger("exit_landing")[0])
        self.assertNotIn('"v2"', Path(e.ex.counters_path).read_text())  # the counters file has no v2 key in v1

    def test_v1_start_conditions_unchanged(self):
        e = self.env(end_ms=e_end())
        self.assertIsNone(h.start_refusal({k: v for k, v in e.conf.items()}, e.root))  # no EXP-026, no DEC-028 needed in v1

    def test_v1_halts_still_latch(self):
        e = self.env()
        for i in range(h.BOOST_MEDIAN_MIN_POOLS):
            e.ex.on_boost_row(f"m{i}", None, None, None, 320.0)
        self.assertIn("boost_median_lt_335", e.ex.counters.halts)


def e_end() -> int:
    return 1_900_000_000_000


# --- on: the v2 exit ----------------------------------------------------------------------------------------------------------


class V2ExitTests(V2Case):
    def test_position_is_stamped_and_the_sell_goes_at_the_rule_trigger_slot(self):
        e = self.env()
        pos = e.open_position()
        self.assertEqual(pos["h5"]["exit_rule"], h.RULE_ID_V2)
        self.assertEqual(e.ex.counters.plans[MINT]["exit_rule"], h.RULE_ID_V2)
        self.assertIsNotNone(e.plan()["s0_wall_ms"])
        sched = boost_schedule(0.7)
        want, tau, _ = expected_trigger(sched)
        sent_at = self.run_until_sell(e, sched)
        self.assertEqual(sent_at, want)  # s0 + 1390 (278 s), not v1's s0 + 1647
        self.assertLess(sent_at, C)
        d = e.ledger("v2_exit_decision")
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]["exit_trigger_slot"], d[0]["k_slices"], d[0]["landing_slot"], d[0]["v1_exit_slot"]), (want, 24, X, C))
        self.assertAlmostEqual(d[0]["tau_s"], tau, places=3)
        ss = e.ledger("sell_sent")[0]
        self.assertEqual((ss["exit_rule"], ss["v2_fired"], ss["v2_trigger_slot"], ss["level"]), (h.RULE_ID_V2, True, want, 0))
        self.assertEqual(e.ex.state.pending[MINT]["reason"], "h5_v2_exit")
        e.land_sell(want + 3)  # 0.6 s after the trigger: not late
        land = e.ledger("exit_landing")[0]
        self.assertEqual((land["v2_fired"], land["v2_late"], land["late"]), (True, False, False))
        self.assertEqual(e.ex.counters.sells_late, 0)
        self.assertIn("v2_pool_closed", {r["kind"] for r in e.ledger()})
        self.assertNotIn(MINT, e.ex._v2_slices)

    def test_sell_is_armed_before_the_trigger(self):
        e = self.env()
        e.open_position()
        sched = boost_schedule(0.7)
        want = expected_trigger(sched)[0]
        self.run_until_sell(e, sched, until=want - 1)
        self.assertIn(MINT, e.ex.armed)  # built and signed ahead (arm lead 2 s), not sent
        self.assertEqual(len(e.rpc.sent), 1)

    def test_330_s_cap_when_the_projection_is_later(self):
        e = self.env()
        e.open_position()
        sent_at = self.run_until_sell(e, boost_schedule(0.5))  # projected last slice 408 s
        self.assertEqual(sent_at, e.plan()["send_slot"])
        self.assertEqual(e.ledger("v2_exit_decision"), [])
        self.assertEqual(e.ex.state.pending[MINT]["reason"], "h5_timed_exit")

    def test_no_boost_rows_falls_back_to_330(self):
        e = self.env()
        e.open_position()
        self.assertEqual(self.run_until_sell(e, []), e.plan()["send_slot"])
        self.assertEqual(e.ledger("v2_exit_decision"), [])

    def test_fewer_than_three_slices_falls_back_to_330(self):
        e = self.env()
        e.open_position()
        self.assertEqual(self.run_until_sell(e, boost_schedule(0.7)[:2]), e.plan()["send_slot"])

    def test_rows_arriving_after_330_fall_back(self):
        e = self.env()
        e.open_position()
        self.assertEqual(self.run_until_sell(e, boost_schedule(0.7), deliver_from=C), e.plan()["send_slot"])
        self.assertEqual(e.ledger("v2_exit_decision"), [])

    def test_late_rows_before_330_sell_on_arrival_never_before(self):
        e = self.env()
        e.open_position()
        sent_at = self.run_until_sell(e, boost_schedule(0.7), deliver_from=S0 + 1500)  # all held back to 300 s
        self.assertEqual(sent_at, S0 + 1500)  # tau* (278 s) had passed: out at once, not before the rows came in, still before 330 s
        self.assertGreater(e.ledger("v2_exit_decision")[0]["ms_after_trigger_wall"], 20_000)

    def test_vault_identity_required(self):
        e = self.env()
        e.open_position()
        for s, lam in boost_schedule(0.7):
            e.ex.on_boost_slice(slice_row(e, s, lam, src="behavioural"))
        self.assertEqual(e.ex._v2_slices, {})
        self.assertEqual({r["why"] for r in e.ledger("v2_slice_ignored")}, {"not_vault_identity"})

    def test_duplicate_slices_count_once(self):
        e = self.env()
        e.open_position()
        r = slice_row(e, S0, 700_000_000)
        e.ex.on_boost_slice(r)
        e.ex.on_boost_slice(dict(r))
        self.assertEqual(len(e.ex._v2_slices[MINT]["sl"]), 1)

    def test_slices_through_the_intents_file(self):
        e = self.env()
        path = Path(e.conf["intents_file"])
        path.write_text("")
        e.ex.intent_tick()
        with path.open("a") as fh:
            for i, (s, lam) in enumerate(boost_schedule(0.7)[:3]):
                fh.write(json.dumps({**slice_row(e, s, lam), "tx_index": i}) + "\n")
            fh.write(json.dumps({**slice_row(e, S0 + 1, 1), "schema": h.SCHEMA_BOOST_SLICE, "type": None}) + "\n")
        e.ex.intent_tick()
        self.assertEqual(len(e.ex._v2_slices[MINT]["sl"]), 4)

    def test_feed_gap_after_s0_falls_back_to_330(self):
        e = self.env()
        e.open_position()
        path = Path(e.conf["intents_file"])
        path.write_text("")
        e.ex.intent_tick()
        with path.open("a") as fh:
            fh.write(json.dumps({"type": "gap", "kind": "ws_reconnect", "flags_pools": True}) + "\n")
        e.ex.intent_tick()
        self.assertEqual(self.run_until_sell(e, boost_schedule(0.7)), e.plan()["send_slot"])
        self.assertEqual([r["why"] for r in e.ledger("v2_fallback_330")], ["feed_gap"])

    def test_restart_with_an_open_position_falls_back_to_330(self):
        e = self.env()
        e.open_position()
        e.ex = e.build()  # the slices read before the restart are gone
        self.assertEqual(self.run_until_sell(e, boost_schedule(0.7)), e.plan()["send_slot"])
        self.assertEqual([r["why"] for r in e.ledger("v2_fallback_330")], ["restart_after_s0"])

    def test_a_v1_position_in_a_v2_build_leaves_by_v1(self):
        e = self.env()
        pos = e.open_position()
        pos["h5"].pop("exit_rule")  # opened by a v1 build before the reinstall
        self.assertEqual(self.run_until_sell(e, boost_schedule(0.7)), e.plan()["send_slot"])
        self.assertEqual(e.ledger("v2_exit_decision"), [])

    def test_dry_run_logs_the_v2_exit(self):
        e = self.env(live=False)
        e.fire()
        self.assertEqual(e.ex.state.open[MINT]["h5"]["exit_rule"], h.RULE_ID_V2)
        sched = boost_schedule(0.7)
        want = expected_trigger(sched, x=TRIG_SLOT)[0]  # a dry run has no landing: X is the trigger slot
        for s, lam in sched:
            if s + LAG > e.rpc.slot:
                break
            e.ex.on_boost_slice(slice_row(e, s, lam))
        while MINT in e.ex.state.open and e.rpc.slot < C + 5:
            e.clock.t += 200
            for s, lam in sched:
                if s + LAG == e.rpc.slot:
                    e.ex.on_boost_slice(slice_row(e, s, lam))
            e.ex.exit_tick(e.clock())
        row = e.ledger("dry_sell")[0]
        self.assertEqual((row["exit_rule"], row["v2_fired"], row["v2_trigger_slot"]), (h.RULE_ID_V2, True, want))


# --- on: the DEC-028 s4 halts -------------------------------------------------------------------------------------------------


def pool_row(mint: str, last: float, total: float = 17.585, first: float = 0.4, n: int = 26, rem: float = 0.0, synthetic=False) -> dict:
    return {"mint": mint, "synthetic": synthetic, "boost_last_slice_s": last, "boost_first_slice_s": first, "boost_slices": n,
            "boost_total_sol": total, "boost_vault_remaining_sol": rem}


class V2HaltTests(V2Case):
    DAY = 86_400_000

    def test_sell_after_boost_rolling_20(self):
        e = self.env()
        for after in [True] * 3 + [False] * 17:  # 15%: not above
            e.ex._v2_pair("m", after)
        self.assertNotIn("v2_sell_after_boost_gt_15pct", e.ex.counters.halts)
        for _ in range(17):  # the three early ones roll out of the window
            e.ex._v2_pair("m", False)
        for _ in range(4):  # 4 of the last 20
            e.ex._v2_pair("m", True)
        self.assertIn("v2_sell_after_boost_gt_15pct", e.ex.counters.halts)

    def test_pairing_feeds_the_rolling_window(self):
        e = self.env()
        e.ex.counters.sell_land_s["m"] = 300.0
        e.ex.on_boost_row("m", None, None, None, 290.0)
        self.assertEqual(e.ex.counters.v2["pairs"], [True])

    def test_plain_median_below_330_on_a_completed_day(self):
        e = self.env()
        for i in range(h.V2_PLAIN_MIN_POOLS):
            e.ex._v2_pool_row(pool_row(f"p{i}", 325.0))
            e.ex._v2_pool_row(pool_row(f"s{i}", 360.0, synthetic=True))  # synthetic pools are not counted
        self.assertEqual(e.ex.counters.day(h.day_key(e.clock()))["v2"]["plain_median"], 325.0)
        self.assertNotIn("v2_plain_median_lt_330", e.ex.counters.halts)  # the running day does not decide
        e.jump(self.DAY)
        e.ex._v2_pool_row(pool_row("next", 340.0))
        self.assertIn("v2_plain_median_lt_330", e.ex.counters.halts)

    def test_plain_median_at_330_and_29_pools_do_not_halt(self):
        e = self.env()
        for i in range(h.V2_PLAIN_MIN_POOLS):
            e.ex._v2_pool_row(pool_row(f"p{i}", 330.0))
        e.jump(self.DAY)
        for i in range(h.V2_PLAIN_MIN_POOLS - 1):
            e.ex._v2_pool_row(pool_row(f"q{i}", 300.5))
        e.jump(self.DAY)
        e.ex._v2_pool_row(pool_row("next", 340.0))
        self.assertNotIn("v2_plain_median_lt_330", e.ex.counters.halts)

    def test_projection_inputs_total(self):
        e = self.env()
        for i in range(h.V2_PLAIN_MIN_POOLS):
            e.ex._v2_pool_row(pool_row(f"p{i}", 340.0, total=17.0))
        e.jump(self.DAY)
        e.ex._v2_pool_row(pool_row("next", 340.0))
        self.assertIn("v2_projection_inputs", e.ex.counters.halts)

    def test_projection_inputs_interval_and_incomplete_pools(self):
        e = self.env()
        for i in range(h.V2_PLAIN_MIN_POOLS):
            e.ex._v2_pool_row(pool_row(f"p{i}", 340.0, first=0.4, n=12))  # (340 - 0.4) / 11 = 30.9 s between slices
            e.ex._v2_pool_row(pool_row(f"i{i}", 340.0, total=5.0, rem=12.0))  # not completed: not counted
        e.jump(self.DAY)
        e.ex._v2_pool_row(pool_row("next", 340.0))
        self.assertEqual(self.halt("v2_projection_inputs")["interval_median_s"], 30.8727)
        self.assertEqual(self.halt("v2_projection_inputs")["total_median_sol"], 17.585)

    def test_projection_inputs_in_range_do_not_halt(self):
        e = self.env()
        for i in range(h.V2_PLAIN_MIN_POOLS):
            e.ex._v2_pool_row(pool_row(f"p{i}", 340.0, first=0.4, n=29))  # 12.13 s
        e.jump(self.DAY)
        e.ex._v2_pool_row(pool_row("next", 340.0))
        self.assertNotIn("v2_projection_inputs", e.ex.counters.halts)

    def halt(self, name: str) -> dict:
        return self._e.ex.counters.halts[name]

    def env(self, **kw):
        self._e = super().env(**kw)
        return self._e

    def test_late_sell_more_than_3_s_after_the_v2_trigger(self):
        e = self.env()
        e.open_position()
        want = self.run_until_sell(e, boost_schedule(0.7))
        e.land_sell(want + 16)  # 3.2 s after the trigger, long before s0 + 335 s
        land = e.ledger("exit_landing")[0]
        self.assertEqual((land["v2_late"], land["late"]), (True, True))
        self.assertIn("late_sells_gt_5pct", e.ex.counters.halts)

    def test_late_vs_trigger_on_a_timer_exit(self):
        e = self.env()
        e.open_position()
        self.run_until_sell(e, [])
        e.land_sell(C + 16)  # 333.2 s: under v1's 335 s line, but 3.2 s after the timer trigger at 330 s
        land = e.ledger("exit_landing")[0]
        self.assertEqual((land["v2_fired"], land["v2_late"]), (False, True))

    def test_stale_slice_stream_on_three_positions(self):
        e = self.env()
        e.clock.t += 50_000
        for i in range(3):
            pos = {"h5": {"v2": {}}, "t_entry_ms": e.clock() - 46_000}
            e.ex._v2_stale_check(f"m{i}", pos, None, [], e.clock())
            self.assertTrue(pos["h5"]["v2"]["stale"])
            self.assertEqual("v2_slice_stream_stale" in e.ex.counters.halts, i == 2)

    def test_not_stale_when_boost_is_done_or_recent(self):
        e = self.env()
        e.clock.t += 50_000
        done = [((0, 0, 0), (h.V2_BUDGET_LAMPORTS, 0, 0, 0))]
        for i in range(3):
            pos = {"h5": {"v2": {}}, "t_entry_ms": e.clock() - 46_000}
            e.ex._v2_stale_check(f"d{i}", pos, None, done, e.clock())
            pos2 = {"h5": {"v2": {}}, "t_entry_ms": e.clock() - 46_000}
            e.ex._v2_stale_check(f"r{i}", pos2, {"seen_ms": e.clock() - 44_000}, [], e.clock())
        self.assertNotIn("v2_slice_stream_stale", e.ex.counters.halts)

    def test_slice_receive_lag_median(self):
        e = self.env()
        e.ex._v2_slices["a"] = {"sl": {(1, 0, 0): (1, 1_500, 0, 0), (2, 0, 0): (1, 1_200, 0, 0), (3, 0, 0): (1, 500, 0, 0)}}
        e.ex._v2_close("a")
        self.assertNotIn("v2_slice_lag_gt_1_35s", e.ex.counters.halts)  # median 1.2 s
        e.ex._v2_slices["b"] = {"sl": {(1, 0, 0): (1, 2_000, 0, 0), (2, 0, 0): (1, 1_400, 0, 0)}}
        e.ex._v2_close("b")  # day median of 1.5, 1.2, 0.5, 2.0, 1.4 = 1.4 s
        self.assertEqual(e.ex.counters.halts["v2_slice_lag_gt_1_35s"]["median_s"], 1.4)

    def test_v1_median_lines_are_report_only_in_v2(self):
        e = self.env()
        for i in range(h.BOOST_MEDIAN_MIN_POOLS):
            e.ex.on_boost_row(f"m{i}", None, None, None, 320.0)
        self.assertNotIn("boost_median_lt_335", e.ex.counters.halts)
        self.assertEqual([r["reason"] for r in e.ledger("v2_report_only")], ["boost_median_lt_335"])
        self.assertIn("boost_structure_lt_300_x3", h.V2_REPORT_ONLY ^ {"boost_structure_lt_300_x3"})  # the 300 s floor stays a halt

    def test_live_v2_start_needs_exp026_and_the_owner_line(self):
        e = self.env(end_ms=e_end())
        cfg = {k: v for k, v in e.conf.items()}
        self.assertEqual(h.start_refusal(cfg, e.root), "exp026_missing")
        (e.root / h.EXP026_FILE).write_text("# EXP-026 (test stub)\n")
        (e.root / "DEC").mkdir(exist_ok=True)
        (e.root / h.DEC028_FILE).write_text("  ```\n  OWNER_OVERRIDE_V2_CONFIRMED:\n  ```\n")  # the draft's empty line
        self.assertEqual(h.start_refusal(cfg, e.root), "dec028_override_missing")
        (e.root / h.DEC028_FILE).write_text("  OWNER_OVERRIDE_V2_CONFIRMED: 2026-10-11 (owner, in session) ...\n")
        self.assertIsNone(h.start_refusal(cfg, e.root))

    def test_start_record_names_the_v2_rule(self):
        e = self.env()
        st = e.ledger("start")[-1]
        self.assertEqual((st["rule"], st["exit_mode"]), (h.RULE_ID_V2, "v2"))
