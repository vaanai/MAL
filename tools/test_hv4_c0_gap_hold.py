"""DEC-032 Am.5 (DRAFT) feed-gap hold for the HV4 C0 bridge, the c0.FEED_GAP_MANUAL_REVIEW False value. Mirrors the H5 executor change on claude/q50-c0-gap-hold
(PR #1085): a DATA-LOSS gap record holds new buys for 60 s and marks open trips `gap`; a forwarder TRANSPORT event loses no row and neither holds nor latches.
The True value (DEC-032 s5 item 2 as written) is covered by tools/test_hv4_live_c0_pr5a.py Section52GapHaltTests / GapWindowTests, which pin it.
Synthetic only: no HV4 row, ledger, read record or job log."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

from tools import arena_live_bridge as alb
from tools import hv4_live_c0 as c0
from tools.test_arena_live_bridge import TmpCase
from tools.test_hv4_live_c0 import Hv4Env, write_feed


class Am5Constants(TmpCase):
    def test_relaxed_value_is_the_default_and_matches_the_h5_rule(self):
        self.assertIs(c0.FEED_GAP_MANUAL_REVIEW, False)
        self.assertEqual(c0.FEED_GAP_HOLD_MS, 60_000)
        self.assertEqual(c0.FEED_GAP_HOLD_FLOOR_MS, 60_000)
        self.assertEqual(c0.TRANSPORT_GAP_KINDS, ("stall", "disconnect", "reconnect", "local_torn_trimmed"))
        self.assertTrue(set(c0.TRANSPORT_GAP_KINDS) <= set(c0.GAP_BREAK_KINDS))

    def test_classification_fails_closed(self):
        for k in c0.TRANSPORT_GAP_KINDS:
            self.assertFalse(c0.gap_loses_data(k), k)
        for k in (*[x for x in c0.GAP_BREAK_KINDS if x not in c0.TRANSPORT_GAP_KINDS], None, 0, "", "Stall", "stall ", "unknown", "gap_slots"):
            self.assertTrue(c0.gap_loses_data(k), k)
        self.assertIn("mismatch", c0.GAP_BREAK_KINDS)
        self.assertTrue(c0.gap_loses_data("mismatch"))
        self.assertTrue(c0.gap_loses_data("error"))
        self.assertIsNone(c0.gap_rule_problem())

    def test_guard_tripwire(self):
        self.assertIsNone(c0.guard_problem())
        with mock.patch.object(c0, "FEED_GAP_HOLD_MS", 59_999):
            self.assertEqual(c0.guard_problem(), "c0_guard:gap_hold")
        with mock.patch.object(c0, "FEED_GAP_HOLD_FLOOR_MS", 30_000), mock.patch.object(c0, "FEED_GAP_HOLD_MS", 30_000):
            self.assertEqual(c0.guard_problem(), "c0_guard:gap_hold")
        with mock.patch.object(c0, "TRANSPORT_GAP_KINDS", ("stall", "mismatch")):
            self.assertEqual(c0.guard_problem(), "c0_guard:gap_rule")
        with mock.patch.object(c0, "FEED_GAP_MANUAL_REVIEW", True), mock.patch.object(c0, "FEED_GAP_HOLD_MS", 0):
            self.assertIsNone(c0.guard_problem())  # the hold length does not apply in the s5-as-written mode

    def test_hold_window(self):
        t = 1_800_000_000_000
        self.assertIsNone(c0.gap_hold_problem(0, t))
        self.assertEqual(c0.gap_hold_problem(t, t), c0.FEED_GAP_HOLD)
        self.assertEqual(c0.gap_hold_problem(t, t + c0.FEED_GAP_HOLD_MS - 1), c0.FEED_GAP_HOLD)
        self.assertIsNone(c0.gap_hold_problem(t, t + c0.FEED_GAP_HOLD_MS))
        self.assertEqual(c0.gap_hold_problem(t + c0.FEED_SKEW_MS + 1, t), c0.FEED_GAP_HOLD)  # a gap instant from the future holds

    def test_gap_log_entries_and_gap_log_ms_agree(self):
        p = Path(self.tmp) / "g.jsonl"
        rows = [{"kind": "stall", "ms": 5}, {"kind": "resume", "ms": 6}, {"kind": "data_loss", "ms": 7, "x": "never read"}, {"kind": "not_a_gap", "ms": 8}]
        p.write_text("".join(json.dumps(r) + "\n" for r in rows) + '{"torn')
        self.assertEqual(c0.gap_log_entries(str(p)), ([("stall", 5), ("data_loss", 7)], [6], None))
        self.assertEqual(c0.gap_log_ms(str(p)), ([5, 7], [6], None))


class Am5BridgeTests(TmpCase):
    def env(self):
        root = tempfile.mkdtemp(dir=self.tmp)
        e = Hv4Env(root)
        e.clear_dir = Path(root) / "gapclear"
        e.clear_dir.mkdir()
        b = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False, gap_clear_dir=e.clear_dir)
        return e, b

    def write(self, e, name, *rows):
        d = Path(e.raw["root"]) / "transport"
        d.mkdir(exist_ok=True)
        (d / f"{e.bot}.{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def advance(self, e, ms):
        e.clock.t += ms
        e.refresh_halt_inputs()

    def test_transport_events_neither_hold_nor_latch(self):
        e, b = self.env()
        e.start()
        t0 = e.clock.t
        self.write(e, "gaps", *({"kind": k, "ms": t0} for k in c0.TRANSPORT_GAP_KINDS), {"kind": "resume", "ms": t0})
        b.tick()
        self.assertEqual(b.state.latches, {})
        self.assertEqual(b.state.gap_noted_ms, 0)
        self.assertIsNone(b.feed_refusal(e.clock.t))
        e.entry_sz("o1")
        b.tick()
        self.assertIn("o1", b.state.orders)  # buys continue: the forwarder is connected again and no row was lost

    def test_data_loss_tape_gap_holds_60s_marks_trips_and_clears_by_itself(self):
        e, b = self.env()
        e.start()
        e.entry_sz("o1")
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1002})
        b.tick()
        t0 = e.clock.t
        self.write(e, "tapegaps", {"kind": "data_loss", "ms": t0})
        noted = []
        if b.gates is not None:
            orig = b.gates.recorder.note_gap
            b.gates.recorder.note_gap = lambda ms: (noted.append(ms), orig(ms))[1]
        b.tick()
        self.assertEqual(b.state.latches, {})
        self.assertEqual(b.state.gap_noted_ms, t0)
        if b.gates is not None:
            self.assertEqual(noted, [t0])  # open trips are marked `gap` for parity, as under s5.2
        self.assertEqual(b.feed_refusal(e.clock.t), c0.FEED_GAP_HOLD)
        e.exit("o1")
        b.tick()
        self.assertIn(("sell", "o1"), [c[:2] for c in e.port.calls])  # exits keep running during the hold
        e.entry_sz("o2")
        b.tick()
        self.assertNotIn("o2", b.state.orders)
        self.assertIn({"order_id": "o2", "why": c0.FEED_GAP_HOLD}, [{k: r.get(k) for k in ("order_id", "why")} for r in self.log(b) if r.get("kind") == "entry_refused"])
        self.advance(e, c0.FEED_GAP_HOLD_MS)
        self.assertIsNone(b.feed_refusal(e.clock.t))
        self.assertEqual(b.state.latches, {})
        self.assertFalse((e.clear_dir / f"{e.bot}.json").exists())  # no review file was needed

    def test_buys_resume_after_the_hold_without_a_review(self):
        e, b = self.env()
        e.start()
        self.write(e, "tapegaps", {"kind": "data_loss", "ms": e.clock.t})
        b.tick()
        e.entry_sz("o2")
        b.tick()
        self.assertNotIn("o2", b.state.orders)
        self.advance(e, c0.FEED_GAP_HOLD_MS)
        e.entry_sz("o3")
        b.tick()
        self.assertIn("o3", b.state.orders)
        kinds = [r.get("kind") for r in self.log(b)]
        self.assertEqual(kinds.count("feed_gap_hold"), 1)

    @staticmethod
    def log(b):
        return [json.loads(x) for x in b.log_path.read_text().splitlines()]

    def test_forwarder_data_loss_kinds_and_decision_stream_gap_row_hold(self):
        for rows in ([{"kind": "mismatch", "ms": None}], [{"kind": "error", "ms": None}], [{"kind": "silence", "ms": None}]):
            with self.subTest(kind=rows[0]["kind"]):
                e, b = self.env()
                e.start()
                t0 = e.clock.t
                self.write(e, "gaps", *({**r, "ms": t0} for r in rows))
                b.tick()
                self.assertEqual(b.state.gap_noted_ms, t0)
                self.assertNotIn(c0.FEED_BREAK_LATCH, b.state.latches)
                self.assertEqual(b.feed_refusal(e.clock.t), c0.FEED_GAP_HOLD)
        e, b = self.env()
        e.start()
        e.row({"kind": "gap", "secret": "x"}, ms=e.clock.t - 10)
        b.tick()
        self.assertEqual(b.state.gap_noted_ms, e.clock.t - 10)
        self.assertNotIn(c0.FEED_BREAK_LATCH, b.state.latches)
        self.assertEqual(b.feed_refusal(e.clock.t), c0.FEED_GAP_HOLD)

    def test_newer_gap_extends_the_hold_and_the_hold_survives_a_restart(self):
        e, b = self.env()
        e.start()
        t0 = e.clock.t
        self.write(e, "tapegaps", {"kind": "gap_silence", "ms": t0})
        b.tick()
        self.advance(e, 50_000)
        self.write(e, "tapegaps", {"kind": "gap_silence", "ms": t0}, {"kind": "feed_restart", "ms": e.clock.t})
        b.tick()
        t1 = e.clock.t
        self.assertEqual(b.state.gap_noted_ms, t1)
        b2 = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False, gap_clear_dir=e.clear_dir)
        self.assertEqual(b2.state.gap_noted_ms, t1)  # persisted: a restart does not end the hold
        self.advance(e, c0.FEED_GAP_HOLD_MS - 1)
        self.assertEqual(b2.feed_refusal(e.clock.t), c0.FEED_GAP_HOLD)
        self.advance(e, 1)
        self.assertIsNone(b2.feed_refusal(e.clock.t))

    def test_integrity_latches_are_unchanged(self):
        e, b = self.env()
        d = Path(e.raw["root"]) / "transport"
        d.mkdir(exist_ok=True)
        (d / f"{e.bot}.gaps.jsonl").write_text("{torn\n")
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)  # a malformed log is not a clear, in either mode
        e2 = Hv4Env(tempfile.mkdtemp(dir=self.tmp))
        b2 = e2.bridge()
        write_feed(e2.raw["root"], e2.bot, e2.clock.t, state="stopped_mismatch")
        b2.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b2.state.latches)

    def test_manual_value_restores_the_latch(self):
        with mock.patch.object(c0, "FEED_GAP_MANUAL_REVIEW", True):
            e, b = self.env()
            e.start()
            self.write(e, "gaps", {"kind": "stall", "ms": e.clock.t})
            b.tick()
            self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
            self.assertIsNone(c0.gap_hold_problem(0, e.clock.t))
