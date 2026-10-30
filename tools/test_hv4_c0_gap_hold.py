"""DEC-031 Amendment 21 item 3 (merged, main 72066560) for the HV4 C0 bridge: the c0.FEED_GAP_MANUAL_REVIEW False value. The True value (DEC-032 s5 item 2 as
built) is pinned by tools/test_hv4_live_c0_pr5a.py Section52GapHaltTests / GapWindowTests. Synthetic only: no HV4 row, ledger, read record or job log."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from unittest import mock

from tools import arena_live_bridge as alb
from tools import hv4_live_c0 as c0
from tools.test_arena_live_bridge import TmpCase
from tools.test_hv4_live_c0 import Hv4Env, write_feed

DAY = 86_400_000


class _Am21(TmpCase):
    """Every test here runs with the Am.21 value (False). The shipped default is True (s5 item 2 as built) until Am.21 item 3 is complete, incl. 3.4 (#1106)."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(c0, "FEED_GAP_MANUAL_REVIEW", False)
        p.start()
        self.addCleanup(p.stop)


class Am21Constants(_Am21):
    def test_shipped_default_is_the_as_built_rule(self):
        import importlib, re
        src = (Path(c0.__file__)).read_text()
        self.assertRegex(src, r"(?m)^FEED_GAP_MANUAL_REVIEW = True\b")

    def test_values_follow_am21(self):
        self.assertIs(c0.FEED_GAP_MANUAL_REVIEW, False)
        self.assertEqual((c0.FEED_GAP_HOLD_MS, c0.FEED_GAP_HOLD_FLOOR_MS, c0.FEED_GAP_HOLD_CAP_MS), (60_000, 60_000, 600_000))
        self.assertEqual(c0.FEED_GAP_REVIEW_MS, DAY)
        self.assertEqual(c0.FEED_GAP_LATENCY_BOUND_MS, 180_000)
        self.assertEqual(set(c0.TRANSPORT_GAP_KINDS), {"stall", "resume", "disconnect", "reconnect", "error", "mismatch", "local_torn_trimmed"})  # Am.21 3.2 verbatim

    def test_classification_fails_closed(self):
        for k in c0.TRANSPORT_GAP_KINDS:
            self.assertFalse(c0.gap_loses_data(k), k)
        for k in ("gap", "data_loss", "silence", "process_restart", "feed_restart", "feed_start", "start", "takeover", "gap_silence", "gap_slots", None, 0, "",
                  "Stall", "stall ", "unknown"):
            self.assertTrue(c0.gap_loses_data(k), k)
        self.assertIsNone(c0.gap_rule_problem())

    def test_guard_tripwire(self):
        self.assertIsNone(c0.guard_problem())
        for name, val in (("FEED_GAP_HOLD_MS", 59_999), ("FEED_GAP_HOLD_MS", 600_001)):
            with mock.patch.object(c0, name, val):
                self.assertEqual(c0.guard_problem(), "c0_guard:gap_hold")
        with mock.patch.object(c0, "FEED_GAP_REVIEW_MS", DAY + 1):
            self.assertEqual(c0.guard_problem(), "c0_guard:gap_review")
        with mock.patch.object(c0, "TRANSPORT_GAP_KINDS", ("stall", "data_loss")):
            self.assertEqual(c0.guard_problem(), "c0_guard:gap_rule")
        with mock.patch.object(c0, "FEED_GAP_MANUAL_REVIEW", True), mock.patch.object(c0, "FEED_GAP_HOLD_MS", 0):
            self.assertIsNone(c0.guard_problem())

    def test_helpers(self):
        t = 1_800_000_000_000
        self.assertIsNone(c0.gap_hold_problem(0, t))
        self.assertEqual(c0.gap_hold_problem(t + 1, t), c0.FEED_GAP_HOLD)
        self.assertIsNone(c0.gap_hold_problem(t, t))
        self.assertIsNone(c0.gap_entry_problem(t, 0))
        self.assertEqual(c0.gap_entry_problem(t, t), c0.FEED_GAP_ENTRY)
        self.assertIsNone(c0.gap_entry_problem(t + 1, t))
        self.assertEqual(c0.gap_entry_problem(None, t), c0.FEED_GAP_ENTRY)
        self.assertEqual(c0.gap_unreviewed([t - DAY - 1, t - DAY, t - 5], 0, t), [t - DAY - 1])
        self.assertEqual(c0.gap_unreviewed([t - DAY - 1], t - DAY - 1, t), [])


class Am21BridgeTests(_Am21):
    def env(self):
        root = tempfile.mkdtemp(dir=self.tmp)
        e = Hv4Env(root)
        e.clear_dir = Path(root) / "gapclear"
        e.clear_dir.mkdir()
        b = self.bridge(e)
        return e, b

    @staticmethod
    def bridge(e):
        return alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False, gap_clear_dir=e.clear_dir)

    def write(self, e, name, *rows):
        d = Path(e.raw["root"]) / "transport"
        d.mkdir(exist_ok=True)
        p = d / f"{e.bot}.{name}.jsonl"
        tmp = d / f".{name}.tmp"
        tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
        os.replace(tmp, p)  # the relay and the puller replace atomically

    def review(self, e, ms):
        (e.clear_dir / f"{e.bot}.json").write_text(json.dumps({"bot_id": e.bot, "reviewed_gap_ms": ms}))

    def advance(self, e, ms):
        e.clock.t += ms
        e.refresh_halt_inputs()

    @staticmethod
    def log(b, kind=None):
        rows = [json.loads(x) for x in b.log_path.read_text().splitlines()]
        return [r for r in rows if kind is None or r.get("kind") == kind]

    def test_reviewer_case_a_record_61s_late_still_holds_60s_from_receipt(self):
        e, b = self.env()
        e.start()
        seen = e.clock.t
        self.write(e, "tapegaps", {"kind": "data_loss", "ms": seen - 61_000})  # it reaches the bridge 61 s after its instant
        b.tick()
        self.assertEqual(b.state.gap_hold_until_ms, seen + c0.FEED_GAP_HOLD_MS)
        self.assertEqual(b.feed_refusal(e.clock.t), c0.FEED_GAP_HOLD)
        e.entry_sz("o1")
        b.tick()
        self.assertNotIn("o1", b.state.orders)
        self.advance(e, c0.FEED_GAP_HOLD_MS - 1)
        self.assertEqual(b.feed_refusal(e.clock.t), c0.FEED_GAP_HOLD)
        self.advance(e, 1)
        self.assertIsNone(b.feed_refusal(e.clock.t))
        e.entry_sz("o2")
        b.tick()
        self.assertIn("o2", b.state.orders)  # no acknowledgement needed (Am.21 3.3)
        self.assertEqual(b.state.latches, {})

    def test_late_beyond_the_latency_bound_is_counted_and_still_holds(self):
        e, b = self.env()
        e.start()
        self.write(e, "tapegaps", {"kind": "feed_restart", "ms": e.clock.t - c0.FEED_GAP_LATENCY_BOUND_MS - 1})
        b.tick()
        self.assertEqual(b.feed_refusal(e.clock.t), c0.FEED_GAP_HOLD)
        self.assertEqual(b.state.counts.get("feed_gap_late"), 1)
        self.assertTrue(self.log(b, "feed_gap_hold")[0]["late"])

    def test_each_new_record_restarts_the_hold_and_an_old_one_does_not(self):
        e, b = self.env()
        e.start()
        t0 = e.clock.t
        self.write(e, "tapegaps", {"kind": "data_loss", "ms": t0})
        b.tick()
        self.advance(e, 50_000)
        t1 = e.clock.t
        self.write(e, "tapegaps", {"kind": "data_loss", "ms": t0}, {"kind": "gap_silence", "ms": t1 - 40_000})  # a NEW record with an older instant than now
        b.tick()
        self.assertEqual(b.state.gap_hold_until_ms, t1 + c0.FEED_GAP_HOLD_MS)
        self.advance(e, 30_000)
        b.tick()  # same file: nothing new
        self.assertEqual(b.state.gap_hold_until_ms, t1 + c0.FEED_GAP_HOLD_MS)
        b2 = self.bridge(e)  # restart: the hold and the known instants persist
        self.assertEqual((b2.state.gap_hold_until_ms, b2.state.gap_known_ms), (t1 + c0.FEED_GAP_HOLD_MS, [t1 - 40_000, t0]) if t1 - 40_000 < t0 else
                         (t1 + c0.FEED_GAP_HOLD_MS, sorted([t0, t1 - 40_000])))
        b2.tick()
        self.assertEqual(b2.state.gap_hold_until_ms, t1 + c0.FEED_GAP_HOLD_MS)

    def test_per_entry_refusal_on_entries_decided_at_or_before_the_gap(self):
        e, b = self.env()
        e.start()
        g = e.clock.t - 1_000
        self.write(e, "tapegaps", {"kind": "data_loss", "ms": g})
        b.tick()
        b.state.gap_hold_until_ms = 0  # isolate the per-entry rule from the hold
        self.assertEqual(b.gap_entry_refusal({"ms": g}), c0.FEED_GAP_ENTRY)
        self.assertEqual(b.gap_entry_refusal({"ms": g - 1}), c0.FEED_GAP_ENTRY)
        self.assertIsNone(b.gap_entry_refusal({"ms": g + 1}))
        e.row({"kind": "entry", "order_id": "o1", "pool": "P1", "meta": {}, "decision_slot": 1000, "stake_sol": 0.05, "attempt_key": None}, ms=g - 500)
        b.tick()
        self.assertNotIn("o1", b.state.orders)
        self.assertIn(c0.FEED_GAP_ENTRY, [r.get("why") for r in self.log(b, "entry_refused")])

    def test_transport_events_and_stopped_mismatch_leave_the_latch(self):
        e, b = self.env()
        e.start()
        t0 = e.clock.t
        self.write(e, "gaps", *({"kind": k, "ms": t0} for k in c0.TRANSPORT_GAP_KINDS))
        b.tick()
        self.assertEqual((b.state.latches, b.state.gap_noted_ms, b.state.gap_hold_until_ms), ({}, 0, 0))
        e.entry_sz("o1")
        b.tick()
        self.assertIn("o1", b.state.orders)
        write_feed(e.raw["root"], e.bot, e.clock.t, state="stopped_mismatch")
        b.tick()
        self.assertNotIn(c0.FEED_BREAK_LATCH, b.state.latches)
        self.assertTrue(str(b.feed_refusal(e.clock.t)).startswith("transport_stall:"))  # the guard that stays (Am.21 3.2)

    def test_decision_stream_gap_row_and_forwarder_unknown_kind_hold(self):
        e, b = self.env()
        e.start()
        e.row({"kind": "gap", "secret": "x"}, ms=e.clock.t - 10)
        b.tick()
        self.assertEqual(b.feed_refusal(e.clock.t), c0.FEED_GAP_HOLD)
        e2, b2 = self.env()
        e2.start()
        self.write(e2, "gaps", {"kind": "brand_new_kind", "ms": e2.clock.t})
        b2.tick()
        self.assertEqual(b2.feed_refusal(e2.clock.t), c0.FEED_GAP_HOLD)

    def test_open_trips_are_marked_gap(self):
        e, b = self.env()
        e.raw.update(parity_dir=str(Path(self.tmp) / "par"), twins_file=str(Path(self.tmp) / "twins.jsonl"), tieout_dir=str(Path(self.tmp) / "tie"))
        b = self.bridge(e)
        noted = []
        b.gates.recorder.note_gap = lambda ms: noted.append(ms)
        e.start()
        self.write(e, "tapegaps", {"kind": "data_loss", "ms": e.clock.t})
        b.tick()
        self.assertEqual(noted, [e.clock.t])

    def test_24h_unreviewed_record_latches_until_a_review_covers_it(self):
        e, b = self.env()
        e.start()
        g = e.clock.t
        self.write(e, "tapegaps", {"kind": "data_loss", "ms": g})
        b.tick()
        self.advance(e, DAY - 1)
        b.tick()
        self.assertNotIn(c0.FEED_BREAK_LATCH, b.state.latches)
        self.advance(e, 2)
        b.tick()
        self.assertEqual(b.state.latches.get(c0.FEED_BREAK_LATCH), f"{c0.FEED_GAP_UNREVIEWED}:{g}")
        e.entry_sz("o1")
        b.tick()
        self.assertNotIn("o1", b.state.orders)
        self.review(e, g - 1)  # does not cover it
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        self.review(e, g)
        b.tick()
        self.assertNotIn(c0.FEED_BREAK_LATCH, b.state.latches)
        self.advance(e, c0.GAP_KNOWN_KEEP_MS)
        b.tick()
        self.assertEqual(b.state.gap_known_ms, [])  # reviewed and older than the relay's retention: pruned

    def test_unreviewed_instants_are_never_pruned(self):
        e, b = self.env()
        e.start()
        g = e.clock.t
        self.write(e, "tapegaps", {"kind": "data_loss", "ms": g})
        b.tick()
        self.write(e, "tapegaps")  # the relay trimmed it away
        self.advance(e, c0.GAP_KNOWN_KEEP_MS + 1)
        b.tick()
        self.assertEqual(b.state.gap_known_ms, [g])
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)

    def test_bad_log_latch_clears_on_clean_log_connected_feed_and_review(self):
        e, b = self.env()
        e.start()
        g = e.clock.t - 5
        self.write(e, "tapegaps", {"kind": "data_loss", "ms": g})
        b.tick()
        d = Path(e.raw["root"]) / "transport"
        (d / f"{e.bot}.gaps.jsonl").write_text("{torn\n")
        b.tick()
        self.assertTrue(b.state.latches[c0.FEED_BREAK_LATCH].startswith(c0.FEED_GAP_LOG_LATCH + ":"))
        (d / f"{e.bot}.gaps.jsonl").write_text("")
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)  # clean log, but not reviewed
        self.review(e, g)
        b.tick()
        self.assertNotIn(c0.FEED_BREAK_LATCH, b.state.latches)  # no gap-free window needed

    def test_manual_value_restores_the_as_built_latch(self):
        with mock.patch.object(c0, "FEED_GAP_MANUAL_REVIEW", True):
            e, b = self.env()
            e.start()
            self.write(e, "gaps", {"kind": "stall", "ms": e.clock.t})
            b.tick()
            self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
            self.assertIsNone(b.gap_entry_refusal({"ms": 0}))


class GapLogCacheTests(TmpCase):
    def fresh(self, p):
        c0._GAP_LOG_CACHE.pop(str(p), None)
        return c0.gap_log_entries(str(p))

    def test_cached_and_incremental_reads_equal_a_fresh_parse(self):
        p = Path(self.tmp) / "g.jsonl"
        rows = [{"kind": "stall", "ms": i} for i in range(5)]
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        first = c0.gap_log_entries(str(p))
        self.assertEqual(c0.gap_log_entries(str(p)), first)  # unchanged: cache
        with open(p, "a") as fh:  # append on the same inode: incremental
            fh.write(json.dumps({"kind": "data_loss", "ms": 9}) + "\n" + '{"kind": "resume", "ms": 10}\n{"torn')
        got = c0.gap_log_entries(str(p))
        self.assertEqual(got, self.fresh(p))
        self.assertEqual(got[0][-1], ("data_loss", 9))
        self.assertEqual(got[1], [10])
        with open(p, "r+") as fh:  # same-size in-place rewrite of an early row (same inode, maybe the same mtime tick)
            data = fh.read()
            fh.seek(0)
            fh.write(data.replace('"ms": 1}', '"ms": 7}', 1))
        self.assertEqual(c0.gap_log_entries(str(p)), self.fresh(p))
        q = Path(self.tmp) / "q.jsonl"
        q.write_text(json.dumps({"kind": "gap", "ms": 1}) + "\n")
        os.replace(q, p)  # a new inode
        self.assertEqual(c0.gap_log_entries(str(p)), ([("gap", 1)], [], None))
        with open(p, "a") as fh:
            fh.write("{bad\n")
        self.assertEqual(c0.gap_log_entries(str(p))[2], "gap_log_malformed")
        os.unlink(p)
        self.assertEqual(c0.gap_log_entries(str(p)), ([], [], None))

    def test_unchanged_large_stream_is_not_reparsed(self):
        p = Path(self.tmp) / "big.jsonl"
        p.write_text("".join(json.dumps({"kind": "data_loss", "ms": 1_800_000_000_000 + i}) + "\n" for i in range(20_000)))
        t0 = time.perf_counter()
        a = c0.gap_log_entries(str(p))
        cold = time.perf_counter() - t0
        t0 = time.perf_counter()
        for _ in range(20):
            b = c0.gap_log_entries(str(p))
        warm = (time.perf_counter() - t0) / 20
        self.assertEqual(a, b)
        self.assertEqual(len(a[0]), 20_000)
        self.assertLess(warm, cold / 5)  # the reviewer's 53-82 ms per tick at 20,000 rows; a cache hit stats and reads one 512-byte tail
        self.assertLess(warm, 0.02)


class Am21CostAndRearmTests(_Am21):
    """Reviewer part A on c3f0109c: (1) a reviewed gap older than 73 h still in the stream re-armed the hold on every tick; (2) one save and one log line per
    new record (33 s for 2,000 records inside a tick); (3) list membership (11.9 ms per tick at 2,000 known gaps)."""

    env = Am21BridgeTests.env
    bridge = staticmethod(Am21BridgeTests.bridge)
    write = Am21BridgeTests.write
    review = Am21BridgeTests.review
    advance = Am21BridgeTests.advance
    log = staticmethod(Am21BridgeTests.log)

    def test_a_reviewed_gap_older_than_73h_never_rearms_the_hold_10_of_10_ticks(self):
        e, b = self.env()
        e.start()
        g = e.clock.t
        self.write(e, "tapegaps", {"kind": "data_loss", "ms": g})
        b.tick()
        first_hold = b.state.gap_hold_until_ms
        self.review(e, g)
        self.advance(e, c0.GAP_KNOWN_KEEP_MS + 1)
        b.tick()
        self.assertEqual(b.state.gap_known_ms, [])  # reviewed and aged out: pruned
        held = 0
        for _ in range(10):  # the row is still in the stream
            self.advance(e, 1_000)
            b.tick()
            held += b.feed_refusal(e.clock.t) == c0.FEED_GAP_HOLD
        self.assertEqual(held, 0)
        self.assertEqual(b.state.gap_hold_until_ms, first_hold)
        self.assertEqual(b.state.gap_known_ms, [])
        self.assertEqual(len(self.log(b, "feed_gap_hold")), 1)

    def test_an_aged_out_unreviewed_gap_is_tracked_for_the_24h_latch_but_never_arms_the_hold(self):
        e, b = self.env()
        e.start()
        g = e.clock.t - c0.GAP_KNOWN_KEEP_MS - 1
        self.write(e, "tapegaps", {"kind": "data_loss", "ms": g})
        for _ in range(10):
            b.tick()
            self.assertNotEqual(b.feed_refusal(e.clock.t), c0.FEED_GAP_HOLD)
            self.advance(e, 1_000)
        self.assertEqual(b.state.gap_hold_until_ms, 0)
        self.assertEqual(b.state.gap_known_ms, [g])
        self.assertEqual(b.state.latches.get(c0.FEED_BREAK_LATCH), f"{c0.FEED_GAP_UNREVIEWED}:{g}")  # unreviewed for more than 24 h: the latch, not a hold

    def test_2000_new_records_cost_one_save_one_log_line_and_do_not_block_an_exit(self):
        e, b = self.env()
        e.start()
        e.entry_sz("o1")
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1002})
        b.tick()
        t = e.clock.t
        self.write(e, "tapegaps", *({"kind": "data_loss", "ms": t - 2_000 + i} for i in range(2_000)))
        e.exit("o1")
        saves = []
        orig = b._save
        b._save = lambda: (saves.append(1), orig())[1]
        t0 = time.perf_counter()
        b.gap_tick(e.clock.t)
        gap_s = time.perf_counter() - t0
        self.assertEqual(len(saves), 1)
        rows = self.log(b, "feed_gap_hold")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["n"], rows[0]["newest_ms"]), (2_000, t - 1))
        self.assertLess(gap_s, 1.0)  # the reviewer measured 33 s
        t0 = time.perf_counter()
        b.tick()
        self.assertLess(time.perf_counter() - t0, 2.0)
        self.assertIn(("sell", "o1"), [c[:2] for c in e.port.calls])  # the exit went out in the same pass
        self.assertEqual(len(b.state.gap_known_ms), 2_000)

    def test_membership_is_a_set_and_a_steady_tick_with_2000_known_gaps_is_cheap(self):
        e, b = self.env()
        e.start()
        t = e.clock.t
        self.write(e, "tapegaps", *({"kind": "data_loss", "ms": t - 2_000 + i} for i in range(2_000)))
        b.tick()
        self.assertIsInstance(b._gap_known(), set)
        self.assertEqual(len(b._gap_known()), 2_000)
        b.gap_tick(e.clock.t)
        n = 20
        t0 = time.perf_counter()
        for _ in range(n):
            b.gap_tick(e.clock.t)
        per = (time.perf_counter() - t0) / n
        self.assertLess(per, 0.01)  # the reviewer measured 11.9 ms for the list scan alone
