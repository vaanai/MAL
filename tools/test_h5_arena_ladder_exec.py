"""DEC-032 Amendment 2 in the executor (tools/h5_arena_executor.py, live only). Fixtures, a fake RPC and a throwaway Keypair: no network, nothing sent."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
from unittest import mock

from tools import h5_arena_executor as ax
from tools import h5_arena_ladder as ld
from tools import h5_executor as h
from tools.test_h5_arena_exec_wiring import LiveCase
from tools.test_h5_arena_executor import A2, Q50, AEnv
from tools.test_h5_executor import MINT


class LadderCase(LiveCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(mock.patch.object(ld, "CEILING_PATH", self.arena_tier).stop)
        mock.patch.object(ld, "CEILING_PATH", self.arena_tier).start()

    def q50(self, ceiling="LADDER_C1", **kw):
        e = self.live(book_id=Q50, **kw)
        self.set_ceiling(ceiling)
        return e

    def set_ceiling(self, text):
        self.arena_tier.write_text(text + "\n")
        os.chmod(self.arena_tier, 0o644)

    def log(self, e) -> ld.LadderLog:
        return ld.LadderLog(Path(e.conf["state_dir"]) / "arena-ladder" / "ladder.jsonl")

    def rebuild(self, e):
        with mock.patch.object(ax, "FEED_NOTE_MERGED", True):
            return e.build()


class StartAndCeilingTests(LadderCase):
    def test_the_arena_tier_may_read_t1_or_ladder_c1_and_nothing_else(self):
        for text, want in (("T1", None), ("LADDER_C1", None), ("T0", "arena_tier_not_t1:T0"), ("LADDER_C2", "arena_tier_file_invalid"), ("T2", "arena_tier_not_t1:T2")):
            self.set_ceiling(text)
            self.assertEqual(ax.arena_tier_refusal(), want, text)

    def test_ceiling_c1_starts_at_c0_with_c0_limits_and_no_base_tier_alarm(self):
        e = self.q50()
        e.ex._refresh_tier(e.clock())
        self.assertEqual(e.ex.tier, "T1")  # LADDER_C1 is T1 for the base machinery
        self.assertEqual(e.ex._rung(), "C0")
        self.assertEqual(e.ex.h5.stake_lamports, ax.C0_TIER["stake_lamports"])
        self.assertFalse(e.alerts("tier_file_problem"))

    def test_ceiling_c1_for_an_uncovered_book_refuses_the_start(self):
        e = self.live(book_id=A2)
        self.set_ceiling("LADDER_C1")
        with self.assertRaises(SystemExit) as cm:
            self.rebuild(e)
        self.assertIn("ladder_ceiling_for_uncovered_book", str(cm.exception))

    def test_a_broken_ladder_log_refuses_the_start(self):
        e = self.q50()
        p = Path(e.conf["state_dir"]) / "arena-ladder" / "ladder.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"seq": 0, "prev_sha256": "x"}\n')
        with self.assertRaises(SystemExit) as cm:
            self.rebuild(e)
        self.assertIn("ladder_log_broken", str(cm.exception))

    def test_the_ladder_guard_is_in_the_c0_tripwire(self):
        with mock.patch.dict(ld.LADDER, {"C1": dataclasses.replace(ld.LADDER["C1"], stake_lamports=150_000_001)}):
            self.assertEqual(ax.c0_guard_problem(), "ladder_guard:C1.stake_lamports")
        for m in ("_refresh_tier", "_ladder_buy_refusal", "ladder_tick", "_ladder_start_problem"):
            self.assertIn(m, ax.GUARD_METHODS)

    def test_dry_run_is_always_c0_and_has_no_ladder(self):
        e = self.env()
        self.assertIsNone(e.ex.ladder)
        self.assertEqual(e.ex._rung(), "C0")
        e.ex.ladder_tick(e.clock())  # a no-op


class C1Helpers(LadderCase):
    def up(self, e):
        """Step up inside this process, with every check patched to pass (the real inputs are not built)."""
        unlock = self.tmp / "LADDER_UNLOCK"
        unlock.write_text("{}")
        with mock.patch.object(ld, "evaluate_step_up", lambda inp: (True, [("x", True, "")])), mock.patch.object(ld, "UNLOCK_PATH", unlock):
            e.ex.ladder_tick(e.ex.now_ms())

    def c1(self, **kw):
        e = self.q50(funded_lamports=1_000_000_000, **kw)
        e.ex._ladder_pre_buy(e.ex.now_ms())
        self.up(e)
        return e


class RungTests(C1Helpers):
    def test_an_in_process_step_up_gives_c1_limits_and_records_the_unlock_sha(self):
        e = self.c1()
        self.assertEqual(e.ex._rung(), "C1")
        lim = e.ex.h5
        self.assertEqual((lim.stake_lamports, lim.max_open, lim.max_trades_per_day, lim.daily_loss_lamports, lim.total_loss_lamports),
                         (150_000_000, 3, 40, 300_000_000, 700_000_000))
        self.assertEqual(lim.wallet_floor_lamports, max(h.MIN_WALLET_FLOOR_LAMPORTS, 1_000_000_000 - 800_000_000))
        rec = self.log(e).records[-1]
        self.assertEqual((rec["kind"], rec["unlock_sha256"]), ("step_up", hashlib.sha256(b"{}").hexdigest()))

    def test_config_can_only_tighten_c1(self):
        e = self.c1(stake_lamports=100_000_000, max_open=2)
        self.assertEqual((e.ex.h5.stake_lamports, e.ex.h5.max_open), (100_000_000, 2))

    def test_c1_needs_funding_of_at_least_one_sol(self):
        e = self.q50(funded_lamports=999_999_999)
        e.ex._ladder_pre_buy(e.ex.now_ms())
        self.up(e)
        self.assertEqual(self.log(e).records[-1]["kind"], "step_up")
        self.assertEqual(e.ex._rung(), "C0")
        self.assertLessEqual(e.ex.h5.stake_lamports, ax.C0_TIER["stake_lamports"])

    def test_a_lowered_ceiling_is_c0_before_the_next_buys(self):
        e = self.c1()
        self.set_ceiling("T1")
        e.ex.intent_tick()  # the pre-buy ladder step runs first
        self.assertEqual(e.ex._rung(), "C0")
        self.assertLessEqual(e.ex.h5.stake_lamports, ax.C0_TIER["stake_lamports"])  # (the fixture's base tier is T0: 0.02; never C1's 0.15)
        self.assertEqual(self.log(e).records[-1]["why"], "ceiling_lowered")

    def test_at_c1_an_unbuilt_trailing_input_steps_down(self):
        e = self.c1()
        e.ex._ladder_pre_buy(e.ex.now_ms())
        self.assertEqual(e.ex._rung(), "C0")
        self.assertEqual(self.log(e).records[-1]["why"], "trailing_d_unknown")
        self.assertTrue(self.log(e).stepped_down)

    def test_a_c1_record_at_start_is_never_honoured(self):
        e = self.q50(funded_lamports=1_000_000_000)
        self.log(e).append("step_up", 1, "C0", "C1", "forged", realized_lamports=0)
        ex = self.rebuild(e)
        self.set_ceiling("LADDER_C1")
        self.assertEqual(ex._rung(), "C0")
        self.assertEqual(self.log(e).records[-1]["why"], "unlock_not_verified_at_start")
        with mock.patch.object(ld, "verify_unlock", lambda *a, **k: (True, None)):
            self.log(e).append("step_up", 2, "C0", "C1", "forged again", realized_lamports=0)
            self.rebuild(e)
        self.assertEqual(self.log(e).records[-1]["why"], "c1_record_at_start_step_down_inputs_unavailable")

    def test_a_lowered_ceiling_at_start_steps_down(self):
        e = self.q50(funded_lamports=1_000_000_000)
        self.log(e).append("step_up", 1, "C0", "C1", "x", realized_lamports=0)
        self.set_ceiling("T1")
        self.rebuild(e)
        self.assertEqual(self.log(e).records[-1]["why"], "ceiling_lowered")

    def test_a_c1_record_written_at_runtime_by_someone_else_is_stepped_down(self):
        e = self.q50(funded_lamports=1_000_000_000)
        e.ex._ladder_pre_buy(e.ex.now_ms())
        e.ex.ladder.append("step_up", 1, "C0", "C1", "forged at runtime", realized_lamports=0)
        self.assertEqual(e.ex._rung(), "C0")
        e.ex._ladder_pre_buy(e.ex.now_ms())
        self.assertEqual(self.log(e).records[-1]["why"], "c1_record_not_written_by_this_process")


class StepUpTests(C1Helpers):
    def test_no_step_up_while_inputs_are_not_built_even_with_a_valid_unlock(self):
        e = self.q50()
        with mock.patch.object(ld, "verify_unlock", lambda bot, *a, **k: (True, None)):
            e.ex._ladder_pre_buy(e.ex.now_ms())
            e.ex.ladder_tick(e.clock())
        self.assertEqual(e.ex._rung(), "C0")
        failed = {n for n, ok, _w in e.ex._last_step_up_eval["checks"] if not ok}
        self.assertTrue({"trailing_30", "halts", "tieout", "live_total", "deposits", "floor"} <= failed, failed)

    def test_no_evaluation_without_the_c1_ceiling(self):
        e = self.q50(ceiling="T1")
        e.ex.ladder_tick(e.clock())
        self.assertFalse(hasattr(e.ex, "_last_step_up_eval"))

    def test_no_step_back_up_after_a_step_down(self):
        e = self.c1()
        e.ex._ladder_pre_buy(e.ex.now_ms())  # trailing input not built -> step down
        self.up(e)
        self.assertEqual(e.ex._rung(), "C0")
        self.assertEqual([r["kind"] for r in self.log(e).records], ["step_up", "step_down"])


class SafetyTests(LadderCase):
    def test_a_ladder_crash_latches_and_never_skips_an_exit(self):
        e = self.q50(ceiling="T1")
        e.open_position()
        plan = e.plan()
        with mock.patch.object(ax.ArenaExecutor, "ladder_tick", side_effect=OSError("disk full")):
            e.at_slot(plan["send_slot"])
        self.assertTrue(e.sent()[1:])  # the timed sell went out in the same tick
        self.assertEqual(e.ex.counters.halts[ax.H_LADDER]["why"], "OSError")
        self.assertEqual(e.ex._halted_arena(), ax.H_LADDER)

    def test_the_trigger_day_is_persisted_before_it_is_set(self):
        e = self.q50(ceiling="T1")
        e.ex.counters.day(h.day_key(e.ex.now_ms()))["realized"] = -100_000_000
        with mock.patch.object(ld.LadderLog, "append", side_effect=OSError("disk full")):
            e.ex._ladder_guarded(e.ex.ladder_tick, e.ex.now_ms())
        self.assertIsNone(e.ex._trigger_day)
        self.assertIn(ax.H_LADDER, e.ex.counters.halts)  # no buys anyway

    def test_marks_never_read_in_the_exit_window_and_at_most_one_per_tick(self):
        e = self.q50(ceiling="T1")
        pos = e.open_position()
        e.ex._marks.clear()
        calls = []
        real = e.ex._snapshot
        e.ex._snapshot = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        send = pos["h5"]["plan"]["send_wall_ms"]
        e.clock.t = send - ax.MARK_EXIT_GUARD_MS
        e.ex._refresh_marks(e.ex.now_ms())
        self.assertEqual(calls, [])
        e.clock.t = send - ax.MARK_EXIT_GUARD_MS - 1
        e.ex._refresh_marks(e.ex.now_ms())
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(ax.MARKS_PER_TICK, 1)


class TriggerAndCapTests(LadderCase):
    def realize(self, e, lamports):
        e.ex.counters.day(h.day_key(e.ex.now_ms()))["realized"] = lamports

    def test_the_daily_trigger_pauses_c0_until_0000z_and_survives_a_restart(self):
        e = self.q50(ceiling="T1")
        self.realize(e, -100_000_000)
        e.ex.ladder_tick(e.ex.now_ms())
        self.assertEqual(e.ex._trigger_day, h.day_key(e.ex.now_ms()))
        self.assertEqual(e.ex._ladder_buy_refusal(), "daily_trigger_until_0000z")
        self.assertEqual(e.ex._rung(), "C0")
        self.assertEqual(self.log(e).records[-1]["kind"], "daily_trigger")
        ex2 = self.rebuild(e)
        self.assertEqual(ex2._ladder_buy_refusal(), "daily_trigger_until_0000z")
        e.clock.t += 86_400_000
        self.assertNotEqual(ex2._ladder_buy_refusal(), "daily_trigger_until_0000z")

    def test_an_open_position_without_a_fresh_mark_counts_at_zero(self):
        e = self.q50(ceiling="T1")
        pos = e.open_position()
        cost = int(pos.get("buy_cost_lamports") or pos["spend"])
        e.ex._marks.clear()
        self.assertEqual(e.ex.daily_loss_now(e.ex.now_ms()), cost)
        e.ex._marks[MINT] = (cost - 1_000, e.ex.now_ms())
        self.assertEqual(e.ex.daily_loss_now(e.ex.now_ms()), 1_000)
        e.ex._marks[MINT] = (cost - 1_000, e.ex.now_ms() - ld.MARK_MAX_AGE_MS - 1)
        self.assertEqual(e.ex.daily_loss_now(e.ex.now_ms()), cost)

    def test_the_mark_is_refreshed_from_the_pool(self):
        e = self.q50(ceiling="T1")
        e.open_position()
        e.ex._marks.clear()
        e.ex._refresh_marks(e.ex.now_ms())
        self.assertIn(MINT, e.ex._marks)

    def test_the_intraday_cap_refuses_a_buy(self):
        e = self.q50(ceiling="T1")
        self.assertIsNone(e.ex._ladder_buy_refusal())
        worst = e.ex._position_worst_case(e.ex.h5.stake_lamports)
        self.realize(e, -(ld.LADDER["C0"].intraday_cap_lamports - worst + 1))  # realized today + this buy's worst case just above the C0 intraday cap
        self.assertEqual(e.ex._ladder_buy_refusal(), "intraday_cap")
        n0 = len(e.rpc.sent)
        e.fire()
        self.assertEqual(len(e.rpc.sent), n0)


class ExitWindowMarkTests(LadderCase):
    """QP observation on #862: a stale mark counted at zero inside the exit window fired the C0 trigger on two normal exits with no real loss."""

    def two_positions(self, e, send_in_ms):
        now = e.ex.now_ms()
        guard = max(ax.MARK_EXIT_GUARD_MS, int(e.ex.h5.arm_lead_ms) + 3_000)
        for i in range(2):
            m = f"Mint{i}" + "1" * 30
            e.ex.state.open[m] = {"buy_cost_lamports": 50_150_000, "spend": 50_000_000, "h5": {"plan": {"send_wall_ms": now + send_in_ms + i * 4_000}}}
        return now, guard

    def test_two_exits_within_10_s_do_not_fire_the_trigger_with_their_last_marks(self):
        e = self.q50(ceiling="T1")
        now, guard = self.two_positions(e, 20_000)
        for m, p in e.ex.state.open.items():  # the last marks, taken just before each window (cost 0.10015 vs value 0.0999: a 0.25 % loss)
            e.ex._marks[m] = (49_900_000, p["h5"]["plan"]["send_wall_ms"] - guard - 1_000)
        e.clock.t += 20_000 + 4_000 + 5_000  # both positions inside their windows (up to send + 10 s), the marks now more than 10 s old
        self.assertLess(e.ex.daily_loss_now(e.ex.now_ms()), ld.LADDER["C0"].daily_trigger_lamports)
        e.ex.ladder_tick(e.ex.now_ms())
        self.assertIsNone(e.ex._trigger_day)

    def test_without_any_mark_they_still_count_at_zero_and_fire(self):
        e = self.q50(ceiling="T1")
        self.two_positions(e, 20_000)
        e.ex._marks.clear()
        e.clock.t += 30_000
        with mock.patch.object(e.ex, "_refresh_marks", lambda now: None):
            e.ex.ladder_tick(e.ex.now_ms())
        self.assertEqual(e.ex._trigger_day, h.day_key(e.ex.now_ms()))  # 2 x 0.10015 counted at zero: conservative when nothing was ever marked

    def test_a_mark_taken_long_before_the_window_does_not_stand_for_it(self):
        e = self.q50(ceiling="T1")
        now, guard = self.two_positions(e, 60_000)
        for m, p in e.ex.state.open.items():
            e.ex._marks[m] = (49_900_000, now)  # 60 s before the send: not a last pre-window mark
        e.clock.t += 60_000 + 4_000 - guard + 100
        self.assertEqual(e.ex.daily_loss_now(e.ex.now_ms()), 2 * 50_150_000)

    def test_the_last_mark_is_taken_just_before_the_window_and_never_inside_it(self):
        e = self.q50(ceiling="T1")
        pos = e.open_position()
        send = pos["h5"]["plan"]["send_wall_ms"]
        guard = max(ax.MARK_EXIT_GUARD_MS, int(e.ex.h5.arm_lead_ms) + 3_000)
        calls = []
        real = e.ex._snapshot
        e.ex._snapshot = lambda *a, **k: (calls.append(e.ex.now_ms()), real(*a, **k))[1]
        e.clock.t = send - guard - ax.LAST_MARK_LEAD_MS - 1
        e.ex._marks[MINT] = (1, e.clock.t)  # fresh mark: the normal 5 s cadence would skip the next read
        e.clock.t = send - guard - ax.LAST_MARK_LEAD_MS + 1_500  # inside the last-chance lead, 1.5 s after the mark
        e.ex._refresh_marks(e.ex.now_ms())
        self.assertEqual(len(calls), 1)
        self.assertEqual(e.ex._marks[MINT][1], e.clock.t)
        e.clock.t = send - guard  # the window: no read whatever the mark's age
        e.ex._refresh_marks(e.ex.now_ms())
        self.assertEqual(len(calls), 1)

    def test_the_intraday_pre_send_cap_still_counts_open_positions_at_zero(self):
        e = self.q50(ceiling="T1")
        now, guard = self.two_positions(e, 20_000)
        for m in e.ex.state.open:
            e.ex._marks[m] = (50_150_000, now)  # perfectly marked: no unrealized loss at all
        worst = [e.ex._position_worst_case(int(p["spend"])) for p in e.ex.state.open.values()]
        e.ex.counters.day(h.day_key(now))["realized"] = -(ld.LADDER["C0"].intraday_cap_lamports - sum(worst) - e.ex._position_worst_case(e.ex.h5.stake_lamports) + 1)
        self.assertEqual(e.ex._ladder_buy_refusal(), "intraday_cap")


class UnlockReverifyTests(C1Helpers):
    def c1_kept(self):
        e = self.c1()
        self.assertEqual(e.ex._rung(), "C1")
        return e

    def test_a_changed_unlock_steps_c1_down_at_the_next_tick(self):
        e = self.c1_kept()
        unlock = self.tmp / "LADDER_UNLOCK"
        unlock.write_text('{"changed": 1}')
        with mock.patch.object(ld, "step_down_reason", lambda *a, **k: None), mock.patch.object(ld, "UNLOCK_PATH", unlock), \
                mock.patch.object(ld, "verify_unlock", lambda *a, **k: (True, None)):
            e.ex._ladder_pre_buy(e.ex.now_ms())
        self.assertEqual(e.ex._rung(), "C0")
        self.assertEqual(self.log(e).records[-1]["why"], "unlock_changed_since_step_up")

    def test_an_unlock_that_no_longer_verifies_steps_c1_down(self):
        e = self.c1_kept()
        with mock.patch.object(ld, "step_down_reason", lambda *a, **k: None), mock.patch.object(ld, "UNLOCK_PATH", self.tmp / "LADDER_UNLOCK"):
            e.ex._ladder_pre_buy(e.ex.now_ms())
        self.assertTrue(self.log(e).records[-1]["why"].startswith("unlock_no_longer_valid:"))

    def test_an_unchanged_valid_unlock_keeps_c1(self):
        e = self.c1_kept()
        with mock.patch.object(ld, "step_down_reason", lambda *a, **k: None), mock.patch.object(ld, "UNLOCK_PATH", self.tmp / "LADDER_UNLOCK"), \
                mock.patch.object(ld, "verify_unlock", lambda *a, **k: (True, None)):
            e.ex._ladder_pre_buy(e.ex.now_ms())
        self.assertEqual(e.ex._rung(), "C1")


class CarryBoundTests(LadderCase):
    two_positions = ExitWindowMarkTests.two_positions

    def test_a_position_still_open_15_s_after_its_send_counts_at_zero(self):
        e = self.q50(ceiling="T1")
        now, guard = self.two_positions(e, 20_000)
        m0 = next(iter(e.ex.state.open))
        e.ex.state.open = {m0: e.ex.state.open[m0]}
        send = e.ex.state.open[m0]["h5"]["plan"]["send_wall_ms"]
        e.ex._marks[m0] = (49_900_000, send - guard - 1_000)
        e.clock.t = send + 9_999
        self.assertEqual(e.ex.daily_loss_now(e.ex.now_ms()), 250_000)  # carried until send + 10 s
        e.clock.t = send + 15_000  # a stuck or failed sell: still open 15 s after its send instant
        self.assertEqual(e.ex.daily_loss_now(e.ex.now_ms()), 50_150_000)

    def test_a_mark_older_than_the_3_s_lead_at_window_open_is_not_carried(self):
        e = self.q50(ceiling="T1")
        now, guard = self.two_positions(e, 20_000)
        m0 = next(iter(e.ex.state.open))
        e.ex.state.open = {m0: e.ex.state.open[m0]}
        send = e.ex.state.open[m0]["h5"]["plan"]["send_wall_ms"]
        e.ex._marks[m0] = (49_900_000, send - guard - ax.LAST_MARK_LEAD_MS - 1)  # 1 ms before the lead: not a last pre-window mark
        e.clock.t = send - guard + 11_000  # inside the window, the mark more than 10 s old
        self.assertEqual(e.ex.daily_loss_now(e.ex.now_ms()), 50_150_000)
        e.ex._marks[m0] = (49_900_000, send - guard - ax.LAST_MARK_LEAD_MS)  # exactly at the lead start: carried
        self.assertEqual(e.ex.daily_loss_now(e.ex.now_ms()), 250_000)
