"""Tests for the live-entry wiring of tools/h5_arena_executor.py (lane L1d-a, 2026-10-23): exit-gap feed, A3 future stamp, own TIER path, parity and tie-out gates,
V band. Fixtures and stubs only: no key material, no network, nothing is sent. LIVE_BUILT is patched on for the test only."""

from __future__ import annotations

import dataclasses
import json
import math
import os
from pathlib import Path
from unittest import mock

from tools import h5_arena_executor as ax
from tools import h5_executor as h
from tools.test_h5_arena_executor import A2, ACase, AEnv, a3_rec, write_canary_record
from tools.test_h5_executor import MINT


class LiveCase(ACase):
    def live(self, **kw) -> AEnv:
        write_canary_record(self.tmp / "repo")
        (self.tmp / "FREEZE.md").write_text("x")
        kw.setdefault("book_id", A2)
        kw.setdefault("funded_lamports", 500_000_000)
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = AEnv(self.tmp, live=True, replay_only=True, end_ms=ax.SEAL_END_MS, **kw)
        e.ex.feed_verified, e.ex._start_ms, e.ex.slices_taken = True, 0, 1
        e.ex.abook = dataclasses.replace(e.ex.abook, clock_start_ms=0)
        e.rpc.balance = 10 * 10**9
        return e

    def refused(self, why, **kw):
        with self.assertRaises(SystemExit) as cm:
            self.live(**kw)
        self.assertIn(why, str(cm.exception))


class ExitGapFeedTests(LiveCase):
    def test_a_live_start_requires_exit_gap_file(self):
        self.refused("exit_gap_file_missing", exit_gap_file=None)
        self.assertEqual(self.live().ex.abook.bot_id, A2)

    def feed(self, *lines):
        e = self.live()
        Path(e.conf["exit_gap_file"]).write_text("".join(x + "\n" for x in lines))
        e.ex.exit_gap_tick()
        return e

    def test_good_rows_do_not_latch(self):
        e = self.feed('{"mint": "m", "exit_gap_bps": 50}', '{"mint": "m", "exit_gap_bps": -10.5}')
        self.assertNotIn(ax.H_EXIT_GAP, e.ex.counters.halts)

    def test_every_kind_of_bad_row_latches(self):
        bad = ["not json", '{"mint": "m"}', '{"exit_gap_bps": "12"}', '{"exit_gap_bps": true}', '{"exit_gap_bps": null}', '{"exit_gap_bps": NaN}',
               '{"exit_gap_bps": Infinity}', "[1, 2]"]
        for line in bad:
            with self.subTest(line=line):
                e = self.feed(line)
                self.assertEqual(e.ex.counters.halts[ax.H_EXIT_GAP]["why"], "exit_gap_row_bad")
                self.assertTrue(e.alerts("exit_gap_row_unreadable"))

    def test_a_bad_row_among_good_ones_latches_and_a_blank_line_does_not(self):
        e = self.feed('{"exit_gap_bps": 1}', "", '{"exit_gap_bps": 2}')
        self.assertNotIn(ax.H_EXIT_GAP, e.ex.counters.halts)
        e = self.feed('{"exit_gap_bps": 1}', "garbage", '{"exit_gap_bps": 2}')
        self.assertIn(ax.H_EXIT_GAP, e.ex.counters.halts)

    def test_a_missing_file_latches(self):
        e = self.live()
        Path(e.conf["exit_gap_file"]).unlink()
        e.ex.exit_gap_tick()
        self.assertEqual(e.ex.counters.halts[ax.H_EXIT_GAP]["why"], "exit_gap_file_unreadable")

    def test_a_latch_refuses_the_buy(self):
        e = self.feed("garbage")
        e.fire()
        self.assertEqual(e.refusals(), [ax.H_EXIT_GAP])


class A3FutureTests(ACase):
    def test_future_run_utc_beyond_5_min_latches_and_inside_does_not(self):
        now = 1_791_300_000_000  # 2026-10-06T15:20:00Z
        f = self.tmp / "a3.jsonl"
        f.write_text(json.dumps(a3_rec("2026-10-06T15:24:00Z")) + "\n")
        self.assertEqual(ax.a3_record_problem(f, now), ([], None))  # 4 min ahead: inside the skew
        f.write_text(json.dumps(a3_rec("2026-10-06T15:26:00Z")) + "\n")
        self.assertEqual(ax.a3_record_problem(f, now), ([], "a3_record_future"))
        f.write_text(json.dumps(a3_rec("2099-01-01T00:00:00Z")) + "\n")
        self.assertEqual(ax.a3_record_problem(f, now), ([], "a3_record_future"))

    def test_the_tick_latches_on_it(self):
        f = self.tmp / "a3.jsonl"
        f.write_text(json.dumps(a3_rec("2099-01-01T00:00:00Z")) + "\n")
        e = self.env(a3_record_file=str(f))
        e.ex.a3_tick(e.clock())
        self.assertEqual(e.ex.counters.halts[ax.H_A3]["why"], "a3_record_future")


class ArenaTierTests(LiveCase):
    def write_tier(self, text, mode=0o644):
        self.arena_tier.write_text(text)
        os.chmod(self.arena_tier, mode)

    def test_start_needs_exactly_t1(self):
        for text in ("T0", "T2", "T1x", "t1", "", "T1 T1"):
            with self.subTest(text=text):
                self.write_tier(text)
                with self.assertRaises(SystemExit) as cm:
                    self.live_noreset()
                self.assertIn("arena_tier", str(cm.exception))

    def live_noreset(self):
        """Like live(), but AEnv must not rewrite the tier file: patch its rewrite out by restoring the content after."""
        write_canary_record(self.tmp / "repo")
        (self.tmp / "FREEZE.md").write_text("x")
        content = self.arena_tier.read_text()
        real = Path.write_text
        with mock.patch.object(ax, "LIVE_BUILT", True), mock.patch.object(Path, "write_text", lambda p, t, *a, **k: real(p, content if p == self.arena_tier else t, *a, **k)):
            return AEnv(self.tmp, live=True, book_id=A2, replay_only=True, end_ms=ax.SEAL_END_MS, funded_lamports=500_000_000)

    def test_whitespace_around_t1_is_fine(self):
        self.write_tier("T1\n")
        self.assertEqual(self.live_noreset().ex.abook.bot_id, A2)

    def test_the_file_checks_are_the_bases(self):
        self.write_tier("T1", mode=0o666)
        with self.assertRaises(SystemExit) as cm:
            self.live_noreset()
        self.assertIn("arena_tier_file_unsafe", str(cm.exception))
        self.arena_tier.unlink()
        self.assertEqual(ax.arena_tier_refusal(self.arena_tier), "arena_tier_file_missing")
        self.write_tier("T1")
        link = self.etc / "link-TIER"
        link.symlink_to(self.arena_tier)
        self.assertEqual(ax.arena_tier_refusal(link), "arena_tier_file_unsafe")

    def test_the_shared_tier_file_is_ignored_for_the_arena_book(self):
        e = self.live()
        self.assertEqual(e.ex._tier_path(), self.arena_tier)
        self.assertNotEqual(e.ex._tier_path(), h.TIER_FILE_PATH)
        h.TIER_FILE_PATH.write_text("T2")
        os.chmod(h.TIER_FILE_PATH, 0o644)
        self.write_tier("T1")
        e.ex._refresh_tier(e.clock())
        self.assertEqual(e.ex.tier, "T1")
        self.write_tier("T0")
        e.ex._refresh_tier(e.clock())
        self.assertEqual(e.ex.tier, "T0")
        h.TIER_FILE_PATH.unlink()
        self.write_tier("T1")
        e.ex._refresh_tier(e.clock())
        self.assertEqual(e.ex.tier, "T1")

    def test_shared_t1_does_not_start_an_arena_book_whose_own_file_is_t0(self):
        h.TIER_FILE_PATH.write_text("T1")
        os.chmod(h.TIER_FILE_PATH, 0o644)
        self.write_tier("T0")
        with self.assertRaises(SystemExit) as cm:
            self.live_noreset()
        self.assertIn("arena_tier_not_t1:T0", str(cm.exception))

    def test_a_dry_run_keeps_the_state_dir_tier(self):
        e = self.env()
        self.assertEqual(e.ex._tier_path(), Path(e.conf["state_dir"]) / "TIER")

    def test_a_t1_run_buys_at_the_c0_ceiling(self):
        e = self.live()
        self.write_tier("T1")
        pos = e.open_position()
        self.assertEqual(e.ex.tier, "T1")
        self.assertLessEqual(int(pos["spend"]), ax.C0_TIER["stake_lamports"])


# ---------------------------------------------------------------------------------------------------------------- items 3 to 5
import time

from tools import arena_live_parity as alp
from tools import h5_arena_gates as gt
from tools.test_h5_executor import V as POOL_V


class VBandTests(LiveCase):
    def test_the_band_is_the_dec_one(self):
        self.assertEqual(gt.V_BAND, (17_500_000_000, 17_700_000_000))  # DEC-031 Am.2 item 6 / DEC-032 s3
        from tools.arena_live_bridge import UNIVERSE_V_RANGE
        self.assertEqual(gt.V_BAND, UNIVERSE_V_RANGE)

    def test_edges_and_bad_values(self):
        lo, hi = gt.V_BAND
        for v in (lo, hi, 17_584_505_288):
            self.assertIsNone(gt.v_band_refusal(v, gt.WSOL))
        for v in (lo - 1, hi + 1, 0, -5, None, True, 17.6e9, "17600000000"):
            self.assertTrue(gt.v_band_refusal(v, gt.WSOL).startswith("v_band:"), v)
        self.assertIn("quote_mint_not_wsol", gt.v_band_refusal(17_600_000_000, "USDC"))

    def test_a_live_buy_outside_the_band_is_refused_and_inside_goes(self):
        e = self.live()
        self.assertTrue(gt.V_BAND[0] <= POOL_V <= gt.V_BAND[1])
        with mock.patch.object(ax, "v_band_refusal", return_value="v_band:test"):
            e.fire()
        self.assertEqual(e.refusals(), ["v_band:test"])
        self.assertEqual(e.rpc.sent, [])
        e2 = self.live()
        e2.fire()
        self.assertIn(MINT, e2.ex.state.pending)


class RecorderSchemaTests(LiveCase):
    def run_trip(self, e):
        e.open_position()
        e.at_slot(e.plan()["send_slot"] + 2)
        p = e.ex.state.pending.get(MINT)
        if p:
            e.land_sell(e.rpc.slot + 1)

    def test_files_pass_the_parity_tools_own_validator_and_the_tool_runs(self):
        e = self.live()
        e.open_position()
        rec = e.ex.gates.recorder
        trips, atts = alp.load_jsonl(rec.trips_path), alp.load_jsonl(rec.attempts_path)
        self.assertEqual(len(trips), 1)
        alp.validate_inputs(trips, [], atts)  # BadInput would raise
        t, a = trips[0], atts[0]
        self.assertEqual((t["trip_id"], t["exit_landed"], t["gap"]), (MINT, False, False))
        self.assertGreater(t["live_buy_price"], 0)
        self.assertEqual(a["trip_id"], MINT)
        self.assertTrue(a["landed"] and not a["failed_or_expired"])
        self.assertAlmostEqual(a["p_fail_press"], 0.289)

    def test_exit_bound_is_the_books_late_instant_not_input(self):
        e = self.live()
        e.open_position()
        plan = e.ex.counters.plans[MINT]["plan"]
        t = e.ex.gates.recorder.trips[MINT]
        self.assertEqual(t["exit_bound_ms"], plan["late_wall_ms"])
        self.assertEqual(gt.LATE_SELL_MS, int(h.LATE_SELL_S * 1000))

    def test_a_closed_trip_has_the_exited_fields(self):
        e = self.live()
        self.run_trip(e)
        self.assertNotIn(MINT, e.ex.state.open)
        trips = alp.load_jsonl(e.ex.gates.recorder.trips_path)
        alp.validate_inputs(trips, [], alp.load_jsonl(e.ex.gates.recorder.attempts_path))
        t = trips[0]
        self.assertTrue(t["exit_landed"])
        for k in ("live_net_lamports", "live_sell_price", "exit_landed_ms", "exit_reason", "closed_ms"):
            self.assertIn(k, t)
        self.assertGreater(e.ex.gates.recorder.seq, 1)

    def test_failed_and_expired_buys_are_failed_attempts_without_a_trip(self):
        e = self.live()
        rec = e.ex.gates.recorder
        rec.on_buy("M1", {"landed": False, "signature": "s1", "decision_t_ms": 5, "fail_class": "expired"}, 100, None)
        rec.on_buy("M2", {"landed": True, "tokens_received": 0, "spend_lamports": 5, "signature": "s2", "decision_t_ms": 6}, 100, None)
        rec.on_buy("M2", {"landed": True, "tokens_received": 0, "spend_lamports": 5, "signature": "s2", "decision_t_ms": 6}, 100, None)  # never twice
        self.assertEqual(rec.trips, {})
        self.assertEqual([(a["landed"], a["failed_or_expired"]) for a in rec.attempts.values()], [(False, True), (False, True)])
        alp.validate_inputs([], [], list(rec.attempts.values()))

    def test_a_gap_marks_open_trips_and_a_restart_reloads(self):
        e = self.live()
        e.open_position()
        e.ex.gates.recorder.note_gap(e.clock())
        self.assertTrue(e.ex.gates.recorder.trips[MINT]["gap"])
        again = gt.ParityRecorder(e.ex.gates.dir)
        self.assertTrue(again.trips[MINT]["gap"])
        self.assertEqual(again.last_landed_ms, e.ex.gates.recorder.last_landed_ms)


class ParityGateTests(LiveCase):
    def setUp(self):
        super().setUp()
        self.calls = []

    def stub(self, rc):
        def run(argv):
            self.calls.append(argv)
            return rc
        return run

    def test_nonzero_rc_blocks_the_buy_zero_lets_it_through(self):
        for rc in (10, 11, 2, 3, 4, 5, 99):
            with self.subTest(rc=rc):
                e = self.live()
                e.ex.gates.runner = self.stub(rc)
                e.fire()
                self.assertEqual(e.refusals()[-1], f"parity_gate:rc_{rc}")  # (the ledger dir is shared by the sub-cases)
                self.assertEqual(e.rpc.sent, [])
        e = self.live()
        e.ex.gates.runner = self.stub(0)
        e.fire()
        self.assertIn(MINT, e.ex.state.pending)

    def test_the_command_line_is_the_tools_and_uses_the_stop_path(self):
        e = self.live()
        e.ex.gates.runner = self.stub(0)
        e.fire()
        argv = self.calls[0]
        self.assertEqual(argv[0], "tools.arena_live_parity")
        self.assertEqual(argv[argv.index("--stop-file") + 1], e.ex.paths["stop"])
        for flag in ("--trips", "--twins", "--attempts", "--state", "--now-ms"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--twins") + 1], e.conf["parity_twins_file"])

    def test_rerun_after_a_closed_trip_and_every_interval(self):
        e = self.live()
        g = e.ex.gates
        g.runner = self.stub(0)
        now = e.clock()
        g.maybe_run_parity(now)
        g.maybe_run_parity(now + 1000)
        self.assertEqual(len(self.calls), 1)
        g.recorder.seq += 1  # a landed buy or a closed trip
        g.maybe_run_parity(now + 2000)
        self.assertEqual(len(self.calls), 2)
        g.maybe_run_parity(now + 2000 + g.interval_ms)
        self.assertEqual(len(self.calls), 3)
        self.assertLessEqual(g.interval_ms, 60_000)  # DEC-032 s3: stale after 60 s (QP 10-23 W1)

    def test_a_result_is_fresh_for_at_most_60_s_even_with_a_long_config_interval(self):
        e = self.live(parity_interval_s=300)
        g = e.ex.gates
        self.assertEqual(g.interval_ms, 60_000)
        g.runner = self.stub(0)
        now = e.clock()
        g.run_parity(now)
        self.assertTrue(g.parity_fresh(now + 59_999))
        self.assertTrue(g.parity_fresh(now + 60_000))
        self.assertFalse(g.parity_fresh(now + 60_001))
        n = len(self.calls)
        self.assertIsNone(g.parity_refusal(now + 61_000))  # stale: rerun now, rc 0 lets it through
        self.assertEqual(len(self.calls), n + 1)

    def test_a_shorter_config_interval_still_wins(self):
        e = self.live(parity_interval_s=20)
        self.assertEqual(e.ex.gates.interval_ms, 20_000)

    def test_a_result_older_than_the_last_landed_event_is_not_fresh(self):
        e = self.live()
        g = e.ex.gates
        g.runner = self.stub(0)
        e.fire()
        e.land_buy()  # the landed buy bumps seq
        n = len(self.calls)
        e.ex.state.open.clear()
        self.assertIsNotNone(g.parity_run)
        self.assertFalse(g.parity_fresh(e.clock()))
        self.assertIsNone(g.parity_refusal(e.clock()))
        self.assertEqual(len(self.calls), n + 1)

    def test_a_crashing_state_builder_or_unwritable_files_is_no_buy(self):
        e = self.live()
        e.ex.gates.runner = self.stub(0)
        e.ex.gates.state_builder = lambda now: 1 / 0
        e.fire()
        self.assertEqual(e.refusals(), ["parity_gate:rc_99"])
        e2 = self.live()
        e2.ex.gates.runner = self.stub(0)
        e2.ex.gates.recorder.broken = "parity_files_unwritable:OSError"
        e2.fire()
        self.assertEqual(e2.refusals()[-1], "parity_gate:rc_99")

    def test_a_failed_run_is_not_retried_on_every_trigger(self):
        e = self.live()
        e.ex.gates.runner = self.stub(10)
        e.fire()
        e.fire(mint="Other")
        self.assertEqual(len(self.calls), 1)

    def test_start_needs_the_twins_file_and_the_tieout_dir(self):
        self.refused("parity_twins_file_missing", parity_twins_file=None)
        self.refused("tieout_dir_missing", tieout_dir=None)

    def test_state_stamps_only_reads_that_succeeded(self):
        e = self.live()
        e.rpc.balance = 10**10
        st = e.ex._parity_state(e.clock())
        self.assertEqual(set(st["at_ms"]), set(alp.STATE_NAMES))
        Path(e.conf["parity_twins_file"]).write_text("not json\n")
        self.assertNotIn("twin", e.ex._parity_state(e.clock())["at_ms"])
        e.ex.feed_verified = False
        self.assertNotIn("decision_chain", e.ex._parity_state(e.clock())["at_ms"])
        e.ex._latch(ax.H_STRAND, mint="m")
        self.assertIn(ax.H_STRAND, e.ex._parity_state(e.clock())["unresolved_hard_halts"])

    def test_the_real_tool_runs_end_to_end_and_a_halt_writes_stop(self):
        e = self.live()
        e.ex.gates.runner = gt.default_runner  # the real subprocess on fixture files
        e.rpc.balance = 10**10
        e.ex.gates.state_builder = lambda now: {"at_ms": {n: now for n in alp.STATE_NAMES}, "unresolved_hard_halts": []}
        now = int(time.time() * 1000)
        self.assertEqual(e.ex.gates.run_parity(now), 0)
        e.ex.gates.state_builder = lambda now: {"at_ms": {}, "unresolved_hard_halts": []}  # P7: stamps missing
        rc = e.ex.gates.run_parity(now)
        self.assertEqual(rc, alp.EXIT_SOFT)
        self.assertTrue(Path(e.ex.paths["stop"]).exists())
        self.assertEqual(e.ex.gates.parity_refusal(now), f"parity_gate:rc_{rc}")


class TieoutGateTests(LiveCase):
    DAY = 86_400_000

    def gates(self):
        e = self.live()
        e.ex.gates.runner = lambda argv: 0 if argv[0] == 'tools.arena_live_parity' else (self.calls.append(argv) or self.rc)
        return e, e.ex.gates

    def setUp(self):
        super().setUp()
        self.calls, self.rc = [], 0

    def test_no_check_before_the_first_send_or_while_its_day_is_open(self):
        e, g = self.gates()
        now = 20_000 * self.DAY + 5_000
        self.assertIsNone(g.tieout_refusal(now, None))
        self.assertIsNone(g.tieout_refusal(now, now - 1000))
        self.assertEqual(self.calls, [])

    def test_once_a_day_has_closed_the_check_runs_and_nonzero_blocks(self):
        e, g = self.gates()
        first = 20_000 * self.DAY + 1000
        now = first + self.DAY
        self.rc = 1
        self.assertEqual(g.tieout_refusal(now, first), "tieout_gate:rc_1")
        argv = self.calls[0]
        self.assertEqual(argv[:3], ["tools.arena_tieout", "--check-last-day", e.conf["tieout_dir"]])
        self.assertEqual(argv[argv.index("--stop-file") + 1], e.ex.paths["stop"])
        self.rc = 0
        self.assertIsNone(g.tieout_refusal(now, first))
        n = len(self.calls)
        self.assertIsNone(g.tieout_refusal(now + 1000, first))  # cached success for 60 s
        self.assertEqual(len(self.calls), n)
        self.assertIsNone(g.tieout_refusal(now + gt.TIEOUT_CACHE_MS + 1, first))
        self.assertEqual(len(self.calls), n + 1)

    def test_the_real_check_last_day_blocks_without_a_result_and_passes_with_one(self):
        e, g = self.gates()
        g.runner = lambda argv: gt.default_runner(argv) if argv[0] == 'tools.arena_tieout' else 0
        first = 20_000 * self.DAY + 1000
        now = first + self.DAY
        self.assertEqual(g.tieout_refusal(now, first), "tieout_gate:rc_1")
        self.assertTrue(Path(e.ex.paths["stop"]).exists())
        Path(e.conf["tieout_dir"]).mkdir()
        day = gt.utc_day(first)
        (Path(e.conf["tieout_dir"]) / f"tieout-{day}.json").write_text(json.dumps({"day": day, "signed_off": True}))
        self.assertIsNone(g.tieout_refusal(now, first))

    def test_the_buy_path_refuses_on_a_failed_tieout(self):
        e, g = self.gates()
        self.rc = 1
        e.ex.state.first_attempt_ms = e.clock() - 2 * self.DAY
        e.fire()
        self.assertEqual(e.refusals(), ["tieout_gate:rc_1"])
