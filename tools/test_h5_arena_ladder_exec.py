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


class RungTests(LadderCase):
    def test_rung_c1_has_c1_limits(self):
        e = self.q50()
        self.log(e).append("step_up", 1, "C0", "C1", "test", realized_lamports=0)
        ex = self.rebuild(e)
        self.set_ceiling("LADDER_C1")
        lim = ex.h5
        self.assertEqual((lim.stake_lamports, lim.max_open, lim.max_trades_per_day, lim.daily_loss_lamports, lim.total_loss_lamports),
                         (150_000_000, 3, 40, 300_000_000, 700_000_000))

    def test_a_lowered_ceiling_steps_c1_down_at_start_and_latches(self):
        e = self.q50()
        self.log(e).append("step_up", 1, "C0", "C1", "test", realized_lamports=0)
        self.set_ceiling("T1")
        ex = self.rebuild(e)
        self.assertEqual(ex._rung(), "C0")
        lg = self.log(e)
        self.assertEqual((lg.records[-1]["kind"], lg.records[-1]["why"]), ("step_down", "ceiling_lowered"))
        self.assertTrue(lg.stepped_down)

    def test_at_c1_an_unbuilt_trailing_input_steps_down_at_once(self):
        e = self.q50()
        self.log(e).append("step_up", 1, "C0", "C1", "test", realized_lamports=0)
        ex = self.rebuild(e)
        self.set_ceiling("LADDER_C1")
        ex.ladder_tick(ex.now_ms())
        self.assertEqual(ex._rung(), "C0")
        self.assertEqual(self.log(e).records[-1]["why"], "trailing_d_unknown")


class StepUpTests(LadderCase):
    def test_no_step_up_while_inputs_are_not_built_even_with_a_valid_unlock(self):
        e = self.q50()
        with mock.patch.object(ld, "verify_unlock", lambda bot, *a, **k: (True, None)):
            e.ex.ladder_tick(e.clock())
        self.assertEqual(e.ex._rung(), "C0")
        failed = {n for n, ok, _w in e.ex._last_step_up_eval["checks"] if not ok}
        self.assertTrue({"trailing_30", "halts", "tieout", "live_total", "deposits", "floor"} <= failed, failed)

    def test_no_evaluation_without_the_c1_ceiling(self):
        e = self.q50(ceiling="T1")
        e.ex.ladder_tick(e.clock())
        self.assertFalse(hasattr(e.ex, "_last_step_up_eval"))

    def test_when_every_check_passes_it_steps_up_once_and_never_back_up_after_a_step_down(self):
        e = self.q50()
        with mock.patch.object(ld, "evaluate_step_up", lambda inp: (True, [("x", True, "")])):
            e.ex.ladder_tick(e.clock())
        self.assertEqual(e.ex._rung(), "C1")
        self.assertEqual(self.log(e).records[-1]["kind"], "step_up")
        e.ex.ladder_tick(e.clock())  # at C1: trailing input not built -> step down
        self.assertEqual(e.ex._rung(), "C0")
        with mock.patch.object(ld, "evaluate_step_up", lambda inp: (True, [("x", True, "")])):
            e.ex.ladder_tick(e.clock())
        self.assertEqual(e.ex._rung(), "C0")  # latched: no automatic step back up
        self.assertEqual([r["kind"] for r in self.log(e).records], ["step_up", "step_down"])


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
