"""DEC-032 Amendment 3 (HV4/HV6 C0 half): tools/hv4_live_c0.a3_problem and its bridge wiring. Synthetic fixtures only: no host file, no A3 record, no applied log,
no a3-books.json, no HV4 or Q50 output. Am.3 tests 1 to 12, the c0 half of 13, 14 (constants) and 15 (bridge). The equality test is against tools.h5_arena_executor (PR #972, merged), imported from the tree."""
from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools import a3_halt, a3_redeploy_check
from tools import h5_arena_executor as h5x
from tools import hv4_live_c0 as c0
from tools.test_arena_live_bridge import TmpCase
from tools.test_hv4_live_c0 import Hv4Env, write_a3, write_feed

RUN = "2026-10-26T12:00:00Z"
RUN_MS = int(datetime.strptime(RUN, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
H = 3_600_000


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Fx:
    def __init__(self, tmp):
        self.dir = Path(tmp) / "a3fx"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.rec = self.dir / "daily.jsonl"
        self.app = self.dir / c0.A3_APPLIED_NAME

    def record(self, run=RUN, mtime_ms=None, halt=None, syn=None, program=False, **flags):
        fl = {k: {"halt": False, "evaluated": True} for k in ("pins_changed", "boost_disabled", "boost_share_low", "boost_budget_shift", "boost_slices_shift")}
        fl["synthetic_share_high"] = {"halt": False, "evaluated": True}
        for k, v in flags.items():
            fl[k] = {"halt": bool(v), "evaluated": True}
        if syn == "halt":
            fl["synthetic_share_high"] = {"halt": True, "evaluated": True}
        if syn == "unevaluated":
            fl["synthetic_share_high"] = {"halt": False, "evaluated": False}
        rec = {"run_utc": run, "halt": {"flags": fl}, "warn": {"flags": {"program_changed": {"warn": program}}}}
        self.rec.write_text(json.dumps(rec) + "\n")
        t = (RUN_MS if mtime_ms is None else mtime_ms) / 1000
        os.utime(self.rec, (t, t))
        return self

    def applied(self, run=RUN, verdict="NONE"):
        self.app.write_text(json.dumps({"run_utc": run, "verdict": verdict}) + "\n")
        return self

    def check(self, now_ms=None):
        return c0.a3_problem(str(self.rec), RUN_MS + 60_000 if now_ms is None else now_ms)


class PooledGuardTests(TmpCase):
    def fx(self):
        return Fx(self.tmp)

    def test_1_raw_synthetic_halt_with_guard_none_is_clear(self):
        self.assertEqual(self.fx().record(syn="halt").applied(verdict="NONE").check(), (None, None))

    def test_2_raw_synthetic_not_evaluated_with_guard_none_is_clear(self):
        self.assertEqual(self.fx().record(syn="unevaluated").applied(verdict="NONE").check(), (None, None))

    def test_3_guard_halt_fail_error_latch_none_pass_clear(self):
        for v in ("HALT", "FAIL", "ERROR"):
            self.assertEqual(self.fx().record().applied(verdict=v).check(), ("a3_guard:" + v, None), v)
        for v in ("NONE", "PASS"):
            self.assertEqual(self.fx().record().applied(verdict=v).check(), (None, None), v)

    def test_4_raw_pins_changed_with_guard_pass_latches(self):
        self.assertEqual(self.fx().record(pins_changed=True).applied(verdict="PASS").check(), ("a3_flags:pins_changed", None))

    def test_5_raw_program_changed_with_guard_none_latches(self):
        self.assertEqual(self.fx().record(program=True).applied().check(), ("a3_flags:program_changed", None))

    def test_5b_other_raw_flags_still_latch(self):
        for k in ("boost_disabled", "boost_share_low", "boost_budget_shift", "boost_slices_shift"):
            self.assertEqual(self.fx().record(**{k: True}).applied().check(), ("a3_flags:" + k, None))

    def test_6_guard_behind_inside_the_bound_refuses_only(self):
        f = self.fx().record().applied(run=iso(RUN_MS - 12 * H))
        self.assertEqual(f.check(RUN_MS + c0.A3_GUARD_LAG_MS), (None, "a3_guard_pending"))
        self.assertEqual(f.check(RUN_MS + 60_000), (None, "a3_guard_pending"))
        f.applied()
        self.assertEqual(f.check(), (None, None))  # clears when the guard line for the run arrives clear

    def test_7_guard_behind_past_the_bound_latches_clock_from_later_of_run_and_mtime(self):
        f = self.fx().record().applied(run=iso(RUN_MS - 12 * H))
        self.assertEqual(f.check(RUN_MS + c0.A3_GUARD_LAG_MS + 1), ("a3_guard_missing", None))
        f = self.fx().record(mtime_ms=RUN_MS + 90 * 60_000).applied(run=iso(RUN_MS - 12 * H))
        self.assertEqual(f.check(RUN_MS + c0.A3_GUARD_LAG_MS + 1), (None, "a3_guard_pending"))
        self.assertEqual(f.check(RUN_MS + 90 * 60_000 + c0.A3_GUARD_LAG_MS + 1), ("a3_guard_missing", None))

    def test_8_guard_ahead_latches(self):
        self.assertEqual(self.fx().record().applied(run=iso(RUN_MS + 12 * H)).check(), ("a3_guard_ahead", None))

    def test_9_guard_log_missing_latches(self):
        self.assertEqual(self.fx().record().check(), ("a3_guard_unreadable", None))

    def test_10_unknown_verdict_and_bad_run_utc_latch(self):
        f = self.fx().record()
        for body, want in (({"run_utc": RUN, "verdict": "OK"}, "a3_guard_malformed"), ({"run_utc": "not a time", "verdict": "NONE"}, "a3_guard_malformed"),
                           ({"verdict": "NONE"}, "a3_guard_malformed"), (["NONE"], "a3_guard_malformed")):
            f.app.write_text(json.dumps(body) + "\n")
            self.assertEqual(f.check(), (want, None), body)
        for raw in ("{not json\n", ""):
            f.app.write_text(raw)
            self.assertEqual(f.check(), ("a3_guard_unreadable", None), raw)

    def test_11_raw_stale_or_future_latches_as_now(self):
        f = self.fx().record().applied()
        self.assertEqual(f.check(RUN_MS + c0.A3_MAX_AGE_MS + 1_000), ("a3_record_stale", None))
        self.assertEqual(f.check(RUN_MS - c0.A3_FUTURE_SKEW_MS - 1_000), ("a3_record_future", None))
        f.rec.write_text("")
        self.assertEqual(f.check(), ("a3_record_unreadable", None))
        f.rec.write_text(json.dumps({"run_utc": RUN}) + "\n")
        self.assertEqual(f.check(), ("a3_record_no_halt_flags", None))

    def test_12_symlink_hardlink_oversize_latch(self):
        f = self.fx().record()
        real = Path(self.tmp) / "real-applied.jsonl"
        real.write_text(json.dumps({"run_utc": RUN, "verdict": "NONE"}) + "\n")
        os.symlink(real, f.app)
        self.assertEqual(f.check(), ("a3_guard_unreadable", None))
        f.app.unlink()
        os.link(real, f.app)
        self.assertEqual(f.check(), ("a3_guard_unreadable", None))
        f.app.unlink()
        real.unlink()
        f.applied()
        self.assertEqual(f.check(), (None, None))
        with open(f.app, "ab") as fh:
            fh.truncate(c0.A3_APPLIED_MAX_BYTES + 1)
        self.assertEqual(f.check(), ("a3_guard_unreadable", None))

    def test_12b_only_the_newest_line_decides(self):
        f = self.fx().record()
        f.app.write_text(json.dumps({"run_utc": RUN, "verdict": "HALT"}) + "\n" + json.dumps({"run_utc": RUN, "verdict": "NONE"}) + "\n")
        self.assertEqual(f.check(), (None, None))
        f.app.write_text(json.dumps({"run_utc": RUN, "verdict": "NONE"}) + "\n" + json.dumps({"run_utc": RUN, "verdict": "HALT"}) + "\n")
        self.assertEqual(f.check(), ("a3_guard:HALT", None))

    def test_13_raw_skips_exactly_the_synthetic_flag(self):
        for k in ("pins_changed", "boost_disabled", "boost_share_low", "boost_budget_shift", "boost_slices_shift", "synthetic_share_high"):
            f = self.fx().record(**{k: True})
            want = None if k == "synthetic_share_high" else "a3_flags:" + k
            self.assertEqual(c0.a3_halt_problem(str(f.rec), RUN_MS + 60_000), want, k)
        self.assertEqual(c0.A3_GUARD_FLAGS, ("synthetic_share_high",))

    def test_14_constants_equal_the_guard_tool(self):
        self.assertEqual(c0.A3_GUARD_VERDICTS, a3_halt.VERDICTS)
        self.assertEqual((a3_redeploy_check.SYN_POOL_K, a3_redeploy_check.SYN_P0, a3_redeploy_check.SYN_PHI, a3_redeploy_check.SYN_Z_HALT,
                          a3_redeploy_check.SYN_MIN_RUN_CHECKED), (4, 75 / 339, 2.0, 3.0902, 10))
        self.assertEqual((c0.A3_GUARD_LAG_MS, c0.A3_APPLIED_NAME, c0.A3_GUARD_CLEAR), (2 * 3_600_000, "a3-applied.jsonl", ("NONE", "PASS")))
        self.assertNotIn("n_checked", Path(c0.__file__).read_text())  # the executor never evaluates synthetic counts itself


class Q50EquivalenceTests(TmpCase):
    def test_same_answer_as_the_q50_implementation_on_a_fixture_corpus(self):
        q = h5x  # the Q50 implementation on this tree (PR #972, merged): no skip path
        n = 0
        recs = [dict(), dict(syn="halt"), dict(syn="unevaluated"), dict(pins_changed=True), dict(program=True), dict(boost_disabled=True), dict(boost_share_low=True),
                dict(run=iso(RUN_MS - 40 * H)), dict(run=iso(RUN_MS + 2 * H)), dict(mtime_ms=RUN_MS + 90 * 60_000)]
        applied = [None, dict(), dict(verdict="HALT"), dict(verdict="FAIL"), dict(verdict="ERROR"), dict(verdict="PASS"), dict(verdict="BOGUS"),
                   dict(run=iso(RUN_MS - 12 * H)), dict(run=iso(RUN_MS + 12 * H)), dict(run="nope")]
        nows = [RUN_MS + 60_000, RUN_MS + 2 * H, RUN_MS + 2 * H + 1, RUN_MS + 3 * H, RUN_MS + 37 * H, RUN_MS - 400_000]
        for ri, r in enumerate(recs):
            for ai, a in enumerate(applied):
                f = Fx(Path(self.tmp) / f"{ri}-{ai}")
                f.record(**r)
                if a is not None:
                    f.applied(**a)
                for now in nows:
                    n += 1
                    self.assertEqual(c0.a3_problem(str(f.rec), now), q.a3_guard_problem(f.rec, now), (r, a, now))
        self.assertEqual(n, 600)
        for k in ("A3_GUARD_FLAGS", "A3_APPLIED_NAME", "A3_GUARD_VERDICTS", "A3_GUARD_CLEAR", "A3_GUARD_LAG_MS", "A3_APPLIED_MAX_BYTES"):
            self.assertEqual(getattr(c0, k), getattr(q, k), k)


class BridgeWiringTests(TmpCase):
    """Am.3 test 15."""

    def open_position(self, e, b):
        e.start()
        e.entry_sz("o1")
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1002})
        b.tick()
        self.assertEqual(b.state.orders["o1"]["status"], "open")

    def advance(self, e):
        e.clock.t += c0.A3_POLL_MS + 1
        write_feed(e.raw["root"], e.bot, e.clock.t)

    def test_15a_a_pending_guard_refuses_buys_while_exits_keep_running_then_clears(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        self.open_position(e, b)
        self.advance(e)
        write_a3(e.a3_file, e.clock.t, guard=None)
        (Path(e.a3_file).parent / c0.A3_APPLIED_NAME).write_text(json.dumps({"run_utc": iso(e.clock.t - 12 * H), "verdict": "NONE"}) + "\n")
        e.entry_sz("o2")
        e.exit("o1")
        b.tick()
        self.assertNotIn(c0.A3_LATCH, b.state.latches)
        self.assertEqual(b._a3_refuse, "a3_guard_pending")
        self.assertNotIn("o2", b.state.orders)
        self.assertEqual(e.log_rows("entry_refused")[-1]["why"], "a3_guard_pending")
        self.assertGreaterEqual(b.state.counts.get("a3_guard_pending_refusal", 0), 1)
        self.assertEqual([c[:2] for c in e.port.calls], [("buy", "o1"), ("sell", "o1")])  # the exit ran
        self.advance(e)
        write_a3(e.a3_file, e.clock.t)  # the guard line for the run arrives clear
        e.entry_sz("o3", pool="P3")
        b.tick()
        self.assertIsNone(b._a3_refuse)
        self.assertIn("o3", b.state.orders, e.log_rows("entry_refused")[-1:])

    def test_15b_a_guard_halt_latches_a3_pins_changed(self):
        for v in ("HALT", "FAIL", "ERROR"):
            e = Hv4Env(Path(self.tmp) / v)
            Path(e.raw["root"]).parent.mkdir(parents=True, exist_ok=True)
            b = e.bridge()
            e.start()
            write_a3(e.a3_file, e.clock.t, guard=v)
            e.entry_sz("o1")
            b.tick()
            self.assertEqual(b.state.latches[c0.A3_LATCH], "a3_guard:" + v)
            self.assertNotIn("o1", b.state.orders)

    def test_15c_raw_synthetic_halt_with_guard_none_does_not_latch_in_the_bridge(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        e.start()
        write_a3(e.a3_file, e.clock.t)
        rec = json.loads(Path(e.a3_file).read_text())
        rec["halt"]["flags"]["synthetic_share_high"] = {"halt": True, "evaluated": True}
        Path(e.a3_file).write_text(json.dumps(rec) + "\n")
        e.entry_sz("o1")
        b.tick()
        self.assertNotIn(c0.A3_LATCH, b.state.latches)
        self.assertIn("o1", b.state.orders)

    def test_15d_a_missing_guard_log_latches_in_the_bridge(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        e.start()
        os.unlink(Path(e.a3_file).parent / c0.A3_APPLIED_NAME)
        b.tick()
        self.assertEqual(b.state.latches[c0.A3_LATCH], "a3_guard_unreadable")


if __name__ == "__main__":
    unittest.main()
