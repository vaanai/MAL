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
