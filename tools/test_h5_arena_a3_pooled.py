"""DEC-032 Amendment 3 item 5b tests (Q50 / H5 path, tools/h5_arena_executor.py). Synthetic fixtures only: no host file, no A3 record, no applied log, no Q50 output.
Am.3 tests 1 to 12 and 13 to 14 are the H5 side only (tools/hv4_live_c0.py is a separate PR and carries the other side); 16, 17 and 17b are here."""
from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tools import a3_halt, a3_redeploy_check
from tools import h5_arena_executor as ax
from tools.test_h5_arena_executor import ACase, CommittedLossTests, a3_rec
from tools.test_h5_executor import trig

RUN = "2026-10-26T12:00:00Z"
RUN_MS = int(datetime.strptime(RUN, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
H = 3_600_000


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Fx:
    """A directory with a synthetic daily record and a synthetic applied log."""

    def __init__(self, tmp: Path):
        self.dir = tmp / "a3fx"
        self.dir.mkdir(exist_ok=True)
        self.rec = self.dir / "daily.jsonl"
        self.app = self.dir / ax.A3_APPLIED_NAME

    def record(self, run: str = RUN, mtime_ms: int | None = None, **halt) -> "Fx":
        self.rec.write_text(json.dumps(a3_rec(run_utc=run, **halt)) + "\n")
        t = (RUN_MS if mtime_ms is None else mtime_ms) / 1000
        os.utime(self.rec, (t, t))
        return self

    def applied(self, run: str = RUN, verdict: str = "NONE") -> "Fx":
        self.app.write_text(json.dumps({"run_utc": run, "verdict": verdict}) + "\n")
        return self

    def check(self, now_ms: int | None = None):
        return ax.a3_guard_problem(self.rec, RUN_MS + 60_000 if now_ms is None else now_ms)


# _hard_refusal for the fixture trigger set below, captured by running the pre-change tools/h5_arena_executor.py (origin/main 433fe06c) on the same fixture
PRE_CHANGE_VALUES = {"normal": None, "q_over_qstar": "q_above_book_q_star", "q_over_40": "q_above_book_q_star",
                     "before_window": "decision_outside_book_window", "after_end": "decision_outside_book_window"}


class GuardProblemTests(ACase):
    def fx(self) -> Fx:
        return Fx(self.tmp)

    def test_1_raw_synthetic_halt_with_guard_none_is_clear(self):
        f = self.fx().record(synthetic_share_high=True).applied(verdict="NONE")
        self.assertEqual(f.check(), (None, None))

    def test_2_raw_synthetic_not_evaluated_with_guard_none_is_clear(self):
        rec = a3_rec(run_utc=RUN)
        rec["halt"]["flags"]["synthetic_share_high"] = {"halt": False, "evaluated": False, "reason": "n<5"}
        f = self.fx()
        f.rec.write_text(json.dumps(rec) + "\n")
        os.utime(f.rec, (RUN_MS / 1000, RUN_MS / 1000))
        f.applied(verdict="NONE")
        self.assertEqual(ax.a3_record_problem(f.rec, RUN_MS + 60_000), ([], None))
        self.assertEqual(f.check(), (None, None))

    def test_3_guard_halt_fail_error_latch(self):
        for v in ("HALT", "FAIL", "ERROR"):
            f = self.fx().record().applied(verdict=v)
            self.assertEqual(f.check(), ("a3_guard:" + v, None), v)

    def test_3b_guard_pass_and_none_clear(self):
        for v in ("NONE", "PASS"):
            self.assertEqual(self.fx().record().applied(verdict=v).check(), (None, None), v)

    def test_4_raw_pins_changed_with_guard_pass_latches(self):
        latch, refuse = self.fx().record(pins_changed=True).applied(verdict="PASS").check()
        self.assertEqual((latch, refuse), ("a3_flags:pins_changed", None))

    def test_5_raw_program_changed_with_guard_none_latches(self):
        latch, refuse = self.fx().record(program_changed=True).applied(verdict="NONE").check()
        self.assertEqual((latch, refuse), ("a3_flags:program_changed", None))

    def test_5b_other_raw_flags_still_latch(self):
        for k in ("boost_disabled", "boost_share_low", "boost_budget_shift", "boost_slices_shift"):
            latch, _ = self.fx().record(**{k: True}).applied().check()
            self.assertEqual(latch, "a3_flags:" + k)

    def test_6_guard_behind_inside_the_bound_refuses_only(self):
        f = self.fx().record().applied(run=iso(RUN_MS - 12 * H))
        self.assertEqual(f.check(RUN_MS + ax.A3_GUARD_LAG_MS), (None, "a3_guard_pending"))
        self.assertEqual(f.check(RUN_MS + 60_000), (None, "a3_guard_pending"))

    def test_6b_pending_clears_when_the_guard_catches_up(self):
        f = self.fx().record().applied(run=iso(RUN_MS - 12 * H))
        self.assertEqual(f.check()[1], "a3_guard_pending")
        f.applied(verdict="NONE")
        self.assertEqual(f.check(), (None, None))

    def test_7_guard_behind_past_the_bound_latches(self):
        f = self.fx().record().applied(run=iso(RUN_MS - 12 * H))
        self.assertEqual(f.check(RUN_MS + ax.A3_GUARD_LAG_MS + 1), ("a3_guard_missing", None))

    def test_7b_the_clock_runs_from_the_later_of_run_utc_and_file_mtime(self):
        f = self.fx().record(mtime_ms=RUN_MS + 90 * 60_000).applied(run=iso(RUN_MS - 12 * H))
        self.assertEqual(f.check(RUN_MS + ax.A3_GUARD_LAG_MS + 1), (None, "a3_guard_pending"))  # written 90 min late: 2 h counts from 13:30
        self.assertEqual(f.check(RUN_MS + 90 * 60_000 + ax.A3_GUARD_LAG_MS + 1), ("a3_guard_missing", None))

    def test_8_guard_ahead_latches(self):
        f = self.fx().record().applied(run=iso(RUN_MS + 12 * H))
        self.assertEqual(f.check(), ("a3_guard_ahead", None))

    def test_9_guard_log_missing_latches(self):
        self.assertEqual(self.fx().record().check(), ("a3_guard_unreadable", None))

    def test_10_unknown_verdict_and_bad_run_utc_latch(self):
        f = self.fx().record()
        f.app.write_text(json.dumps({"run_utc": RUN, "verdict": "OK"}) + "\n")
        self.assertEqual(f.check(), ("a3_guard_malformed", None))
        f.app.write_text(json.dumps({"run_utc": "not a time", "verdict": "NONE"}) + "\n")
        self.assertEqual(f.check(), ("a3_guard_malformed", None))
        f.app.write_text(json.dumps({"verdict": "NONE"}) + "\n")
        self.assertEqual(f.check(), ("a3_guard_malformed", None))
        f.app.write_text(json.dumps(["NONE"]) + "\n")
        self.assertEqual(f.check(), ("a3_guard_malformed", None))
        f.app.write_text("{not json\n")
        self.assertEqual(f.check()[0], "a3_guard_unreadable")
        f.app.write_text("")
        self.assertEqual(f.check()[0], "a3_guard_unreadable")

    def test_11_raw_stale_or_future_latches_as_now(self):
        f = self.fx().record().applied()
        self.assertEqual(f.check(RUN_MS + ax.A3_MAX_AGE_MS + 1_000), ("a3_record_stale", None))
        self.assertEqual(f.check(RUN_MS - ax.A3_FUTURE_SKEW_MS - 1_000), ("a3_record_future", None))
        f.rec.write_text("")
        self.assertEqual(f.check(), ("a3_record_unreadable", None))
        f.rec.write_text(json.dumps({"run_utc": RUN}) + "\n")
        self.assertEqual(f.check(), ("a3_record_no_halt_flags", None))

    def test_12_symlink_hardlink_oversize_latch(self):
        f = self.fx().record()
        real = self.tmp / "real-applied.jsonl"
        real.write_text(json.dumps({"run_utc": RUN, "verdict": "NONE"}) + "\n")
        os.symlink(real, f.app)
        self.assertEqual(f.check(), ("a3_guard_unreadable", None))
        f.app.unlink()
        os.link(real, f.app)  # a second hard link
        self.assertEqual(f.check(), ("a3_guard_unreadable", None))
        f.app.unlink()
        real.unlink()
        f.applied()
        self.assertEqual(f.check(), (None, None))  # the same file as a plain regular file is clear
        with mock.patch.object(ax, "A3_APPLIED_MAX_BYTES", 10):
            self.assertEqual(f.check(), ("a3_guard_unreadable", None))
        f.app.unlink()
        f.app.mkdir()  # not a regular file
        self.assertEqual(f.check(), ("a3_guard_unreadable", None))

    def test_12b_only_the_newest_line_decides(self):
        f = self.fx().record()
        f.app.write_text(json.dumps({"run_utc": RUN, "verdict": "HALT"}) + "\n" + json.dumps({"run_utc": RUN, "verdict": "NONE"}) + "\n")
        self.assertEqual(f.check(), (None, None))

    def test_13_h5_record_problem_skips_exactly_the_synthetic_flag(self):
        names = ("pins_changed", "boost_disabled", "boost_share_low", "boost_budget_shift", "boost_slices_shift", "synthetic_share_high")
        for k in names:
            f = self.fx().record(**{k: True})
            why, problem = ax.a3_record_problem(f.rec, RUN_MS + 60_000)
            self.assertEqual((why, problem), ([] if k == "synthetic_share_high" else [k], None), k)
        self.assertEqual(ax.A3_GUARD_FLAGS, ("synthetic_share_high",))

    def test_14_constants_equal_the_guard_tool(self):
        self.assertEqual(ax.A3_GUARD_VERDICTS, a3_halt.VERDICTS)
        self.assertEqual((a3_redeploy_check.SYN_POOL_K, a3_redeploy_check.SYN_P0, a3_redeploy_check.SYN_PHI, a3_redeploy_check.SYN_Z_HALT,
                          a3_redeploy_check.SYN_MIN_RUN_CHECKED), (4, 75 / 339, 2.0, 3.0902, 10))
        self.assertEqual(ax.A3_GUARD_LAG_MS, 2 * 3_600_000)
        self.assertEqual(ax.A3_APPLIED_NAME, "a3-applied.jsonl")

    def test_14b_the_two_consecutive_unevaluated_rule_is_the_guards(self):
        """Per F2: n_checked < SYN_MIN_RUN_CHECKED (10) is unevaluated for the guard; the executor adds nothing of its own to that decision."""
        self.assertEqual(a3_redeploy_check.SYN_MIN_RUN_CHECKED, 10)
        self.assertEqual(ax.a3_guard_problem.__module__, "tools.h5_arena_executor")
        src = Path(ax.__file__).read_text()
        self.assertNotIn("n_checked", src)  # the executor never evaluates synthetic counts itself


class TickTests(ACase):
    live_env = CommittedLossTests.live_env

    def env_with(self, f: Fx):
        e = self.env(a3_record_file=str(f.rec))
        e.ex.cfg_a3_file = str(f.rec)
        return e

    def test_16a_a_guard_halt_latches_h_a3(self):
        f = Fx(self.tmp).record(run="2026-10-06T15:00:00Z").applied(run="2026-10-06T15:00:00Z", verdict="HALT")
        e = self.env_with(f)
        e.ex.a3_tick(e.clock())
        self.assertEqual(e.ex.counters.halts[ax.H_A3]["why"], "a3_guard:HALT")
        e.fire()
        self.assertEqual(e.refusals(), [ax.H_A3])

    def test_16b_a_pending_guard_refuses_buys_while_exits_keep_running(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            e.open_position()
            plan = e.plan()
            f = Fx(self.tmp)
            f.record(run="2026-10-06T15:00:00Z", mtime_ms=e.clock()).applied(run="2026-10-05T15:00:00Z")
            e.ex.cfg_a3_file = str(f.rec)
            e.ex._a3_polled_ms = -ax.A3_POLL_MS
            e.ex.a3_tick(e.clock())
            self.assertEqual(e.ex._a3_refuse, "a3_guard_pending")
            self.assertNotIn(ax.H_A3, e.ex.counters.halts)
            e.fire(mint="Other")
            self.assertEqual(e.refusals()[-1], "a3_guard_pending")
            n0, sent_at = len(e.rpc.sent), None
            while e.rpc.slot < plan["send_slot"] + 10 and sent_at is None:
                e.clock.t += int(e.rpc.slot_ms)
                e.ex.exit_tick(e.clock())
                if len(e.rpc.sent) > n0:
                    sent_at = e.rpc.slot
            self.assertIsNotNone(sent_at)  # the exit went out
            self.assertNotIn(ax.H_A3, e.ex.counters.halts)
            # the guard catches up (clear): the refusal goes away at the next poll
            f.applied(run="2026-10-06T15:00:00Z", verdict="NONE")
            e.ex.a3_tick(e.clock() + 2 * ax.A3_POLL_MS)
            self.assertIsNone(e.ex._a3_refuse)

    def test_16c_a_raw_latch_resets_the_refusal(self):
        f = Fx(self.tmp).record(run="2026-10-06T15:00:00Z", pins_changed=True).applied(run="2026-10-06T15:00:00Z")
        e = self.env_with(f)
        e.ex._a3_refuse = "a3_guard_pending"
        e.ex.a3_tick(e.clock())
        self.assertIsNone(e.ex._a3_refuse)
        self.assertEqual(e.ex.counters.halts[ax.H_A3]["flags"], ["pins_changed"])

    # the trigger set of tests 17 and 17b: value of _hard_refusal for each, captured from tools/h5_arena_executor.py at origin/main 433fe06c (before this change)
    def triggers(self, e):
        c = e.clock
        return {
            "normal": trig(c),
            "q_over_qstar": trig(c, q_lamports=e.ex.abook.q_star_lamports + 1),
            "q_over_40": trig(c, q_lamports=41 * 10**9),
            "before_window": dataclasses.replace(trig(c), decision_ms=e.ex.abook.clock_start_ms - 1),
            "after_end": dataclasses.replace(trig(c), decision_ms=e.ex.abook.end_ms_seal),
        }

    def values(self, e):
        return {k: e.ex._hard_refusal(t, e.clock()) for k, t in self.triggers(e).items()}

    def test_17_no_record_configured_reads_no_file_and_refusals_are_unchanged(self):
        e = self.env()
        self.assertIsNone(e.ex.cfg_a3_file)
        with mock.patch.object(ax, "a3_record_problem", side_effect=AssertionError("read")), \
                mock.patch.object(ax, "a3_guard_problem", side_effect=AssertionError("read")):
            e.ex.a3_tick(e.clock())
        self.assertIsNone(e.ex._a3_refuse)
        self.assertEqual(self.values(e), PRE_CHANGE_VALUES)

    def test_17b_record_set_raw_clear_guard_none_changes_nothing(self):
        f = Fx(self.tmp).record(run="2026-10-06T15:00:00Z").applied(run="2026-10-06T15:00:00Z", verdict="NONE")
        e = self.env_with(f)
        e.ex.a3_tick(e.clock())
        self.assertEqual(e.ex.counters.halts, {})
        self.assertIsNone(e.ex._a3_refuse)
        self.assertEqual(self.values(e), PRE_CHANGE_VALUES)

    def test_17c_raw_synthetic_flag_set_with_guard_none_changes_nothing(self):
        f = Fx(self.tmp).record(run="2026-10-06T15:00:00Z", synthetic_share_high=True).applied(run="2026-10-06T15:00:00Z", verdict="NONE")
        e = self.env_with(f)
        e.ex.a3_tick(e.clock())
        self.assertEqual(e.ex.counters.halts, {})
        self.assertEqual(self.values(e), PRE_CHANGE_VALUES)


