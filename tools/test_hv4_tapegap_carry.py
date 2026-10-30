"""HV4 C0 tape-gap carry: QP removal precondition 7 items P7-c (fail closed on a missing or stale relay stream) and the carriage half of P7-e (#969 comment 6424766650),
and the "#967 write_status cross-test" of precondition 14. SYNTHETIC only: temp dirs, synthetic tapegaps rows, synthetic decision rows. No HV4 or arena path, ledger, read
record, tape or job output is opened; no arena path is used in this file. The 'remote' side of every pull runs locally (a subprocess), no ssh, no network, no key.

The delays this module measures are IN-PROCESS, SYNTHETIC figures (a local subprocess serve, a local puller thread, a bridge polled every few ms). They are not a host or
network measurement and say nothing about the real research-0 -> mal-fast-0 path (P46 DESIGN s4 has that hop)."""

from __future__ import annotations

import ast
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_live_bridge as alb
from tools import decision_forwarder as df
from tools import hv4_live_c0 as c0
from tools.arena_declog import DecisionLog
from tools.test_arena_live_bridge import TmpCase
from tools.test_hv4_live_c0 import ARM, Hv4Env, write_feed, write_tapegaps

REPO = Path(__file__).resolve().parents[1]
SERVE = str(REPO / "tools" / "decision_forwarder.py")


def manual_review():
    """DEC-032 s5 item 2 as written: a gap record latches `feed_break`. On a tree with PR #1090 (DEC-032 Am.5 DRAFT) that is c0.FEED_GAP_MANUAL_REVIEW True and the default
    there is False (a 60 s hold instead of a latch); create=True lets these tests run on main (which has only the latch) and on main + #1090 alike."""
    return mock.patch.object(c0, "FEED_GAP_MANUAL_REVIEW", True, create=True)


needs_am5 = unittest.skipUnless(hasattr(c0, "gap_hold_problem"), "needs PR #1090 (DEC-032 Am.5 DRAFT gap hold)")


def wall_ms() -> int:
    return time.time_ns() // 1_000_000


def tg_path(root: str) -> Path:
    return Path(root) / "transport" / f"{ARM}.tapegaps.jsonl"


# --- P7-c: the pure check ----------------------------------------------------------------------------------------------------------------------------


class TapeGapStreamProblemTests(TmpCase):
    def f(self, mtime_ms=None, data=b""):
        p = Path(self.tmp) / "transport" / f"{ARM}.tapegaps.jsonl"
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(data)
        if mtime_ms is not None:
            os.utime(p, ns=(mtime_ms * 1_000_000, mtime_ms * 1_000_000))
        return str(p)

    def test_the_bound_is_pinned_and_sits_above_the_relay_cadence_ceiling(self):
        self.assertEqual(c0.TAPE_GAP_STREAM_MAX_AGE_MS, 180_000)
        # the relay must pass inside the compactor's grace (P7-b), so its cadence is under it; the bound is 1.5x that ceiling. arena_feed is read as source (its import needs
        # network libraries this venv lacks), never run.
        grace = [n.value.value for n in ast.parse((REPO / "tools" / "arena_feed.py").read_text()).body
                 if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "COMPACT_GRACE_MS" for t in n.targets) and isinstance(n.value, ast.Constant)]
        self.assertEqual(grace, [120_000])
        self.assertEqual(c0.TAPE_GAP_STREAM_MAX_AGE_MS * 2, grace[0] * 3)
        self.assertGreater(c0.TAPE_GAP_STREAM_MAX_AGE_MS, df.STALL_MS_DEFAULT * 10)

    def test_missing(self):
        self.assertEqual(c0.tape_gap_stream_problem(str(Path(self.tmp) / "nope"), 10 ** 12), "tape_gap_stream_missing")
        self.assertEqual(c0.tape_gap_stream_path("/r", "b"), "/r/transport/b.tapegaps.jsonl")

    def test_age_boundaries(self):
        now = 1_791_300_000_000
        b = c0.TAPE_GAP_STREAM_MAX_AGE_MS
        self.assertIsNone(c0.tape_gap_stream_problem(self.f(now), now))
        self.assertIsNone(c0.tape_gap_stream_problem(self.f(now - b), now))  # exactly the bound is still fresh (> only)
        self.assertEqual(c0.tape_gap_stream_problem(self.f(now - b - 1), now), "tape_gap_stream_stale")
        self.assertEqual(c0.tape_gap_stream_problem(self.f(0), now), "tape_gap_stream_stale")
        self.assertIsNone(c0.tape_gap_stream_problem(self.f(now + c0.FEED_SKEW_MS), now))  # a few seconds of clock skew is fine
        self.assertEqual(c0.tape_gap_stream_problem(self.f(now + c0.FEED_SKEW_MS + 1), now), "tape_gap_stream_future")  # a far-future stamp would never age out
        self.assertEqual(c0.tape_gap_stream_problem(self.f(now + 10 ** 9), now), "tape_gap_stream_future")

    def test_content_is_not_judged_here_an_empty_fresh_file_is_a_live_relay(self):
        now = 1_791_300_000_000
        self.assertIsNone(c0.tape_gap_stream_problem(self.f(now, b""), now))
        self.assertIsNone(c0.tape_gap_stream_problem(self.f(now, b"not even json\n"), now))  # gap_tick's gap_log_ms latches that one

    def test_not_a_plain_file(self):
        now = 1_791_300_000_000
        real = self.f(now)
        link = Path(self.tmp) / "transport" / "link"
        os.symlink(real, link)
        self.assertEqual(c0.tape_gap_stream_problem(str(link), now), "tape_gap_stream_not_regular")
        os.link(real, Path(self.tmp) / "second-name")
        self.assertEqual(c0.tape_gap_stream_problem(real, now), "tape_gap_stream_not_regular")
        d = Path(self.tmp) / "transport" / "dir"
        d.mkdir()
        self.assertEqual(c0.tape_gap_stream_problem(str(d), now), "tape_gap_stream_not_regular")


# --- P7-c: in the bridge ----------------------------------------------------------------------------------------------------------------------------


class BridgeTapeGapRefusalTests(TmpCase):
    def open_position(self, e, b):
        e.start()
        e.entry_sz("o1")
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1002})
        b.tick()
        self.assertEqual(b.state.orders["o1"]["status"], "open")

    def test_missing_or_stale_stream_stops_new_buys_exits_run_nothing_latches_it_is_counted_and_logged(self):
        for label, spoil, why in (
            ("missing", lambda e: os.unlink(tg_path(e.raw["root"])), "tape_gap_stream_missing"),
            ("stale", lambda e: write_tapegaps(e.raw["root"], e.bot, e.clock.t - c0.TAPE_GAP_STREAM_MAX_AGE_MS - 1), "tape_gap_stream_stale"),
            ("future", lambda e: write_tapegaps(e.raw["root"], e.bot, e.clock.t + c0.FEED_SKEW_MS + 1), "tape_gap_stream_future"),
        ):
            with self.subTest(label=label):
                e = Hv4Env(tempfile.mkdtemp(dir=self.tmp), live=False)
                b = e.bridge()
                self.open_position(e, b)
                e.entry_sz("o2")
                e.exit("o1")
                spoil(e)  # after the rows are written (the row writer refreshes the stream)
                b.tick()
                self.assertEqual([c[:2] for c in e.port.calls], [("buy", "o1"), ("sell", "o1")])  # the exit ran, the new entry did not
                ref = [r for r in e.log_rows("entry_refused") if r.get("order_id") == "o2"]
                self.assertEqual([r["why"] for r in ref], [why])
                self.assertNotIn("o2", b.state.orders)
                self.assertEqual(b.state.counts.get("tape_gap_stream_refusal"), 1)
                self.assertEqual(b.state.counts.get(f"refused_{why}"), 1)
                self.assertFalse(b.state.latches)  # not a latch: the DEC-032 s5.2 halt is feed_break, this is the guard that its input is alive

    def test_a_fresh_pass_clears_it_by_itself(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        e.start()
        os.unlink(tg_path(e.raw["root"]))
        e.declog.append(e.bot, e.clock.t - 100, {"kind": "entry", "order_id": "o1", "pool": "P1", "meta": {}, "decision_slot": 1000, "stake_sol": 0.05, "attempt_key": None})
        b.tick()
        self.assertEqual([c for c in e.port.calls if c[0] == "buy"], [])
        e.entry_sz("o2")  # the row writer refreshes the stream, as a relay pass + carry would
        b.tick()
        self.assertEqual([c[1] for c in e.port.calls if c[0] == "buy"], ["o2"])
        self.assertFalse(b.state.latches)

    def test_the_stream_aging_out_while_the_forwarder_stays_up_is_a_dead_relay(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        self.assertIsNone(b.feed_refusal(e.clock.t))
        e.clock.t += c0.TAPE_GAP_STREAM_MAX_AGE_MS + 1
        write_feed(e.raw["root"], e.bot, e.clock.t, tapegaps=False)  # the decision feed is alive and fresh; the relay's file is not rewritten
        self.assertEqual(b.feed_refusal(e.clock.t), "tape_gap_stream_stale")
        write_tapegaps(e.raw["root"], e.bot, e.clock.t)  # the relay is back
        self.assertIsNone(b.feed_refusal(e.clock.t))

    def test_transport_stall_is_reported_first_and_does_not_count_as_a_tape_gap_refusal(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        os.unlink(tg_path(e.raw["root"]))
        write_feed(e.raw["root"], e.bot, e.clock.t, state="stalled", tapegaps=False)
        self.assertEqual(b.feed_refusal(e.clock.t), "transport_stall:feed_state_stalled")
        self.assertNotIn("tape_gap_stream_refusal", b.state.counts)

    def test_only_a_dry_synthetic_marked_root_may_run_without_the_file_live_never(self):
        e = Hv4Env(self.tmp)
        br = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False)
        os.unlink(tg_path(e.raw["root"]))
        self.assertEqual(br.feed_refusal(e.clock.t), "tape_gap_stream_missing")  # dry, no marker: refused
        (Path(e.raw["root"]) / c0.SYNTHETIC_MARKER).write_text("")
        self.assertIsNone(br.feed_refusal(e.clock.t))  # dry, synthetic marker, no file at all: allowed (as for the forwarder status)
        write_tapegaps(e.raw["root"], e.bot, e.clock.t - c0.TAPE_GAP_STREAM_MAX_AGE_MS - 1)
        self.assertEqual(br.feed_refusal(e.clock.t), "tape_gap_stream_stale")  # but a file that is there and old always refuses
        live = Hv4Env(tempfile.mkdtemp(dir=self.tmp), live=True)
        lb = live.bridge()
        os.unlink(tg_path(live.raw["root"]))
        (Path(live.raw["root"]) / c0.SYNTHETIC_MARKER).write_text("")
        self.assertEqual(lb.feed_refusal(live.clock.t), "tape_gap_stream_missing")  # live never takes the marker exemption

    def test_a_synthetic_dry_root_without_a_forwarder_status_still_gets_the_stream_check(self):
        e = Hv4Env(self.tmp)
        br = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False)
        (Path(e.raw["root"]) / c0.SYNTHETIC_MARKER).write_text("")
        os.unlink(Path(e.raw["root"]) / "transport" / f"{e.bot}.status.json")  # no forwarder status at all: exempt on a marked dry root ...
        self.assertIsNone(br.feed_refusal(e.clock.t))
        write_tapegaps(e.raw["root"], e.bot, e.clock.t - c0.TAPE_GAP_STREAM_MAX_AGE_MS - 1)  # ... but an old stream is not exempt
        self.assertEqual(br.feed_refusal(e.clock.t), "tape_gap_stream_stale")

    def test_presend_rechecks_the_stream_after_the_rpcs(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        e.start()
        e.entry_sz("o1")
        real = e.port.balance

        def balance_then_relay_dies():
            v = real()
            os.unlink(tg_path(e.raw["root"]))  # the stream disappears while the RPCs run
            return v
        e.port.balance = balance_then_relay_dies
        b.tick()
        self.assertEqual([c for c in e.port.calls if c[0] == "buy"], [])
        self.assertTrue(any(r.get("presend") and r["why"] == "tape_gap_stream_missing" for r in e.log_rows("entry_refused")), e.log_rows("entry_refused"))

    def test_other_profiles_are_unaffected(self):
        from tools.test_arena_live_bridge import Env
        e = Env(self.tmp)
        self.assertIsNone(e.bridge().feed_refusal(e.clock.t))  # no hv4_c0 profile, no transport directory, no refusal

    def test_a_gap_row_in_the_carried_stream_still_latches_feed_break_and_a_malformed_one_too(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        write_tapegaps(e.raw["root"], e.bot, e.clock.t, rows=[{"kind": "gap_silence", "ms": e.clock.t - 5}])
        with manual_review():
            b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        e2 = Hv4Env(tempfile.mkdtemp(dir=self.tmp))
        b2 = e2.bridge()
        tg_path(e2.raw["root"]).write_text("{torn\n")
        os.utime(tg_path(e2.raw["root"]), ns=(e2.clock.t * 1_000_000,) * 2)
        b2.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b2.state.latches)  # an unreadable / malformed gap log latches in both modes

    @needs_am5
    def test_under_am5_a_carried_gap_row_holds_buys_instead_of_latching_and_the_stream_guard_still_applies(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        write_tapegaps(e.raw["root"], e.bot, e.clock.t, rows=[{"kind": "gap_silence", "ms": e.clock.t - 5}])
        with mock.patch.object(c0, "FEED_GAP_MANUAL_REVIEW", False):
            b.tick()
            self.assertEqual(b.state.latches, {})
            self.assertEqual(b.state.gap_noted_ms, e.clock.t - 5)
            self.assertEqual(b.feed_refusal(e.clock.t), c0.FEED_GAP_HOLD)  # the self-clearing hold
            e.clock.t += c0.FEED_GAP_HOLD_MS + 1
            e.refresh_halt_inputs()  # the relay is alive; its rows (the old gap) are still in the stream
            self.assertIsNone(b.feed_refusal(e.clock.t))
            os.unlink(tg_path(e.raw["root"]))  # a dead relay is not "no gaps" in this mode either
            self.assertEqual(b.feed_refusal(e.clock.t), "tape_gap_stream_missing")


# --- precondition 14: the #967 write_status cross-test ---------------------------------------------------------------------------------------------------


class WriteStatusCrossTests(TmpCase):
    """The REAL puller writer (decision_forwarder.Puller.write_status / .gap) produces the files; the bridge's REAL readers (feed_gap_problem, gap_log_ms, gap_tick) judge them.
    Nothing here writes a status file by hand."""

    NOW = 1_791_300_000_000

    def puller(self, dst, **kw):
        return df.Puller(str(dst), ARM, ["true"], clock=lambda: self.NOW * 1_000_000, **kw)

    def status_problem(self, pl, **set_):
        pl.last_frame_ms = self.NOW
        for k, v in set_.items():
            setattr(pl, k, v)
        pl.write_status()
        return c0.feed_gap_problem(str(pl.status_path), ARM, self.NOW)

    def test_every_state_the_puller_can_write_is_judged_connected_clear_all_others_refuse(self):
        src = Path(df.__file__).read_text()
        tree = ast.parse(src)
        written = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "state" for t in node.targets) and isinstance(node.value, ast.Constant):
                written.add(node.value.value)
        states = written | {"init"}
        self.assertEqual(states, {"init", "connecting", "connected", "stalled", "disconnected", "stopped", "stopped_mismatch", "stopped_error"})  # a new state needs a decision here
        pl = self.puller(Path(self.tmp) / "dst")
        for state in sorted(states):
            with self.subTest(state=state):
                why = self.status_problem(pl, state=state)
                self.assertEqual(why, None if state == "connected" else f"feed_state_{state}")
        pl.close()

    def test_the_puller_writes_the_pinned_stall_bound_and_a_wider_one_is_refused(self):
        pl = self.puller(Path(self.tmp) / "dst")
        self.assertIsNone(self.status_problem(pl, state="connected"))
        pl.close()
        wide = self.puller(Path(self.tmp) / "dst2", stall_ms=c0.FEED_MAX_STALL_MS + 1)
        self.assertEqual(self.status_problem(wide, state="connected"), "feed_stall_ms_not_pinned")
        wide.close()

    def test_age_and_silence_are_judged_on_the_writers_own_fields(self):
        pl = self.puller(Path(self.tmp) / "dst")
        pl.state = "connected"
        pl.last_frame_ms = self.NOW - c0.FEED_FRAME_MAX_MS - 1
        pl.write_status()
        self.assertEqual(c0.feed_gap_problem(str(pl.status_path), ARM, self.NOW), "feed_silent")
        pl.last_frame_ms = self.NOW
        pl.write_status()
        self.assertEqual(c0.feed_gap_problem(str(pl.status_path), ARM, self.NOW + c0.FEED_STATUS_MAX_AGE_MS + 1), "feed_status_stale")  # the writer died: the file ages out
        self.assertEqual(c0.feed_gap_problem(str(pl.status_path), "hv4-CON-F", self.NOW), "feed_status_foreign")
        pl.close()

    def test_a_live_run_writes_connected_and_the_bridge_reads_it_clear(self):
        src, dst = Path(self.tmp) / "src", Path(self.tmp) / "dst"
        log = DecisionLog(str(src))
        for i in range(2):
            log.append(ARM, wall_ms() + i, {"kind": "entry", "synthetic": True, "i": i})
        seen = []

        def until(p):
            if p.state == "connected" and p.rows >= 2:
                p.write_status()  # the real writer, at the real state, mid-run
                seen.append(c0.feed_gap_problem(str(p.status_path), ARM, wall_ms()))
                return True
            return False
        pl = df.Puller(str(dst), ARM, [sys.executable, "-u", SERVE, "serve", "--root", str(src), "--bot-id", ARM, "--hb-ms", "100", "--start-file", "", "--start-offset", "0"])
        self.assertEqual(pl.run(max_s=20, until=until), df.EXIT_OK)
        self.assertEqual(seen, [None])
        # once the run has ended the writer says `stopped`, and the bridge's reader refuses it
        self.assertEqual(c0.feed_gap_problem(str(pl.status_path), ARM, wall_ms()), "feed_state_stopped")

    def test_every_gap_kind_the_puller_writes_is_known_to_the_bridge_resume_excepted(self):
        src = Path(df.__file__).read_text()
        kinds = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "gap" and node.args and isinstance(node.args[0], ast.Constant):
                kinds.add(node.args[0].value)
        self.assertEqual(kinds, {"stall", "resume", "disconnect", "reconnect", "mismatch", "local_torn_trimmed", "error"})
        self.assertEqual(kinds - {"resume"} - set(c0.GAP_BREAK_KINDS), set())  # a kind the puller adds without the bridge knowing it would be fail-open
        pl = self.puller(Path(self.tmp) / "dst")
        for k in sorted(kinds):
            pl.gap(k, why="x") if k != "resume" else pl.gap(k, gap_ms=3)
        gaps, resumes, bad = c0.gap_log_ms(str(pl.gaps_path))
        self.assertIsNone(bad)
        self.assertEqual((len(gaps), len(resumes)), (len(kinds) - 1, 1))  # every break kind is a gap instant, `resume` is the one that is not
        pl.close()

    def test_stopped_mismatch_from_a_real_tamper_latches_feed_break_in_the_bridge(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        src = Path(self.tmp) / "src"
        log = DecisionLog(str(src))
        for i in range(3):
            log.append(ARM, wall_ms() + i, {"kind": "entry", "synthetic": True, "i": i})
        f = next((src / "decisions" / ARM).glob("*.jsonl"))
        lines = f.read_bytes().splitlines(keepends=True)
        row = json.loads(lines[1])
        row["i"] = 99  # the sha no longer matches
        f.write_bytes(lines[0] + (df.canon(row) + "\n").encode() + lines[2])
        pl = df.Puller(e.raw["root"], ARM, [sys.executable, "-u", SERVE, "serve", "--root", str(src), "--bot-id", ARM, "--hb-ms", "100", "--start-file", "", "--start-offset", "0"])
        self.assertEqual(pl.run(max_s=20, until=lambda p: p.seq + 1 >= 3), df.EXIT_MISMATCH)
        sp = pl.status_path
        self.assertEqual(json.loads(sp.read_text())["state"], "stopped_mismatch")
        self.assertEqual(c0.feed_gap_problem(str(sp), ARM, wall_ms()), "feed_state_stopped_mismatch")
        gaps, _res, bad = c0.gap_log_ms(str(pl.gaps_path))
        self.assertEqual((len(gaps), bad), (1, None))  # the real `mismatch` row
        self.assertEqual(b.state.latches, {})
        b.gap_tick(wall_ms())
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)

    def test_stopped_mismatch_status_alone_latches_without_a_gap_row(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        pl = self.puller(Path(e.raw["root"]))
        pl.state, pl.last_frame_ms = "stopped_mismatch", self.NOW
        pl.write_status()
        b.gap_tick(e.clock.t)
        self.assertEqual(b.state.latches.get(c0.FEED_BREAK_LATCH), "forwarder_stopped_mismatch")
        pl.close()

    def test_every_other_non_connected_state_refuses_buys_but_does_not_latch(self):
        for state in ("init", "connecting", "stalled", "disconnected", "stopped", "stopped_error"):
            with self.subTest(state=state):
                e = Hv4Env(tempfile.mkdtemp(dir=self.tmp))
                b = e.bridge()
                pl = df.Puller(e.raw["root"], ARM, ["true"], clock=lambda: e.clock.t * 1_000_000)
                pl.state, pl.last_frame_ms = state, e.clock.t
                pl.write_status()
                self.assertEqual(b.feed_refusal(e.clock.t), f"transport_stall:feed_state_{state}")
                b.gap_tick(e.clock.t)
                self.assertNotIn(c0.FEED_BREAK_LATCH, b.state.latches)
                pl.close()


# --- P7-e carriage: serve -> pull -> bridge, synthetic, with the in-process delay ---------------------------------------------------------------------------


class EndToEnd:
    """One pipeline: a synthetic relay file (written the way the relay writes it: tmp + fsync + rename) -> a real `serve --tapegaps-file` subprocess -> a real Puller thread
    -> the Hv4Env root -> a Bridge on the WALL clock."""

    def __init__(self, tmp: str, mtime_ms: int | None = None, write_first: bool = True) -> None:
        self.tmp = tmp
        self.e = Hv4Env(tmp)
        root = self.e.raw["root"]
        for p in Path(root, "transport").iterdir():
            p.unlink()  # the fixture's hand-written status / stream are gone: from here only the real puller writes
        self.src = os.path.join(tmp, "src")  # the research-0 side root; no decisions exist
        os.makedirs(self.src)
        self.relay_file = os.path.join(tmp, "relay", "transport", f"{ARM}.tapegaps.jsonl")
        os.makedirs(os.path.dirname(self.relay_file))
        gc = Path(tmp) / "gapclear"
        gc.mkdir()
        self.bridge = alb.Bridge(alb.Config.from_dict(self.e.raw), self.e.port, now_ms=wall_ms, live_ok_path=self.e.live_ok, tier_path=self.e.tier, root_checks=False,
                                 gap_clear_dir=gc)
        if write_first:
            self.relay_pass([], mtime_ms)
        cmd = [sys.executable, "-u", SERVE, "serve", "--root", self.src, "--bot-id", ARM, "--start-file", "", "--start-offset", "0", "--hb-ms", "100", "--poll-ms", "5",
               "--tapegaps-file", self.relay_file]
        self.puller = df.Puller(root, ARM, cmd)
        self.stop = threading.Event()
        self.rc: list[int] = []
        self.thread = threading.Thread(target=lambda: self.rc.append(self.puller.run(max_s=60, stop=self.stop)), daemon=True)

    def relay_pass(self, rows: list[tuple[str, int]], mtime_ms: int | None = None) -> int:
        """One relay pass: the whole file rewritten atomically; its mtime is the pass instant. Returns the instant (ms) the new file became visible."""
        tmp = self.relay_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for k, m in rows:
                fh.write(json.dumps({"kind": k, "ms": m}, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        if mtime_ms is not None:
            os.utime(tmp, ns=(mtime_ms * 1_000_000,) * 2)
        os.replace(tmp, self.relay_file)
        return wall_ms()

    def __enter__(self) -> "EndToEnd":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop.set()
        self.thread.join(timeout=30)
        self.puller.close()

    def wait(self, cond, timeout: float = 20.0, step: float = 0.002) -> float:
        """Polls cond() every `step` s; returns the elapsed ms. Fails the run on timeout."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if cond():
                return (time.monotonic() - t0) * 1000
            time.sleep(step)
        raise AssertionError(f"timeout waiting for {cond}")

    def refusal(self) -> str | None:
        return self.bridge.feed_refusal(wall_ms())


class EndToEndTests(TmpCase):
    def test_missing_until_the_first_pass_then_clear_and_the_carried_copy_is_exact(self):
        with EndToEnd(self.tmp, write_first=False) as p:
            p.wait(lambda: c0.feed_gap_problem(c0.feed_status_path(p.e.raw["root"], ARM), ARM, wall_ms()) is None)  # the real puller is connected and fresh
            self.assertEqual(p.refusal(), "tape_gap_stream_missing")  # connected, but the relay has produced nothing: not "no gaps"
            sent_ms = p.relay_pass([("data_loss", wall_ms() - 1)], wall_ms())
            p.wait(lambda: p.refusal() is None)
            dst = tg_path(p.e.raw["root"])
            self.assertEqual(dst.read_bytes(), Path(p.relay_file).read_bytes())
            self.assertEqual(os.lstat(dst).st_mtime_ns // 1_000_000, os.lstat(p.relay_file).st_mtime_ns // 1_000_000)
            self.assertEqual(list((Path(p.e.raw["root"]) / "decisions" / ARM).iterdir()), [])  # nothing under decisions/
            self.assertGreaterEqual(wall_ms(), sent_ms)

    def test_a_carried_gap_row_latches_feed_break_and_the_in_process_delay(self):
        delays_ms, vis_ms = [], []
        with manual_review(), EndToEnd(self.tmp) as p:
            b = p.bridge
            p.wait(lambda: p.refusal() is None)  # an established, healthy pipeline with an empty relay stream
            b.gap_tick(wall_ms())
            self.assertEqual(b.state.latches, {})
            rows: list[tuple[str, int]] = []
            for i in range(6):
                gap_at = wall_ms()
                rows.append(("data_loss" if i % 2 == 0 else "gap_silence", gap_at))
                t_write = p.relay_pass(list(rows), gap_at)  # source write complete
                t0 = time.perf_counter()
                p.wait(lambda: tg_path(p.e.raw["root"]).exists() and tg_path(p.e.raw["root"]).read_bytes().count(b"\n") == len(rows), step=0.001)
                vis_ms.append((time.perf_counter() - t0) * 1000)

                def noted() -> bool:
                    b.gap_tick(wall_ms())  # the bridge's own poll, here every ~1 ms (production tick: every 50 ms)
                    return b.state.gap_noted_ms == gap_at
                p.wait(noted, step=0.001)
                delays_ms.append(wall_ms() - t_write)
                if i == 0:
                    self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)  # the carried row latched the hard halt
                    self.assertRegex(b.state.latches[c0.FEED_BREAK_LATCH], r"^gap record at \d+$")
                self.assertIsNone(p.refusal())  # the pipeline stays healthy: a gap row is a latch (feed_break), not a stale-stream refusal
        self.assertEqual(len(delays_ms), 6)
        print(f"\n[synthetic in-process, NOT a host measurement] source write -> bridge gap_tick noted the carried row: min {min(delays_ms)} ms, "
              f"median {statistics.median(delays_ms)} ms, max {max(delays_ms)} ms (n={len(delays_ms)}; wall-clock ms resolution, bridge polled every ~1 ms, serve --poll-ms 5); "
              f"file visible on the bridge side within {max(vis_ms):.1f} ms of the first poll", file=sys.stderr)
        self.assertLess(max(delays_ms), 5_000)  # a sanity bound for a loaded CI box, not a performance claim

    @needs_am5
    def test_under_am5_the_carried_gap_row_holds_new_buys_for_the_hold_window_no_latch(self):
        with mock.patch.object(c0, "FEED_GAP_MANUAL_REVIEW", False), EndToEnd(self.tmp) as p:
            b = p.bridge
            p.wait(lambda: p.refusal() is None)
            gap_at = wall_ms()
            p.relay_pass([("data_loss", gap_at)], gap_at)

            def noted() -> bool:
                b.gap_tick(wall_ms())
                return b.state.gap_noted_ms == gap_at
            p.wait(noted, step=0.001)
            self.assertEqual(b.state.latches, {})
            self.assertEqual(p.refusal(), c0.FEED_GAP_HOLD)

    def test_a_stale_source_mtime_is_stale_on_the_bridge_side_and_a_new_pass_clears_it(self):
        old = wall_ms() - c0.TAPE_GAP_STREAM_MAX_AGE_MS - 5_000
        with EndToEnd(self.tmp, mtime_ms=old) as p:
            p.wait(lambda: c0.feed_gap_problem(c0.feed_status_path(p.e.raw["root"], ARM), ARM, wall_ms()) is None)
            p.wait(lambda: tg_path(p.e.raw["root"]).exists())
            self.assertEqual(os.lstat(tg_path(p.e.raw["root"])).st_mtime_ns // 1_000_000, old)  # the SOURCE instant, not the time of the copy
            self.assertEqual(p.refusal(), "tape_gap_stream_stale")
            p.bridge.gap_tick(wall_ms())
            self.assertEqual(p.bridge.state.latches, {})  # a refusal, not a latch
            p.relay_pass([], wall_ms())  # the relay is back
            p.wait(lambda: p.refusal() is None)

    def test_a_relay_that_stops_rewriting_goes_stale_on_the_bridge_side_by_the_clock_alone(self):
        pass_at = wall_ms() - c0.TAPE_GAP_STREAM_MAX_AGE_MS + 1_000  # the last pass was 179 s ago
        with EndToEnd(self.tmp, mtime_ms=pass_at) as p:
            p.wait(lambda: c0.feed_gap_problem(c0.feed_status_path(p.e.raw["root"], ARM), ARM, wall_ms()) is None)
            p.wait(lambda: tg_path(p.e.raw["root"]).exists())
            self.assertIsNone(p.refusal())  # still inside the bound
            waited = p.wait(lambda: p.refusal() == "tape_gap_stream_stale", timeout=10, step=0.05)  # no new frame: only the age moves
            self.assertGreater(waited, 0)
            self.assertEqual(os.lstat(tg_path(p.e.raw["root"])).st_mtime_ns // 1_000_000, pass_at)

    def test_a_relay_rewrite_that_drops_rows_stops_the_pull_leaves_the_copy_and_latches_via_the_status(self):
        with EndToEnd(self.tmp, write_first=False) as p:
            p.relay_pass([("data_loss", wall_ms() - 10), ("gap_silence", wall_ms() - 5)], wall_ms())
            p.wait(lambda: p.refusal() is None)
            before = tg_path(p.e.raw["root"]).read_bytes()
            p.relay_pass([("data_loss", wall_ms() - 10)], wall_ms())  # a row went missing
            p.wait(lambda: bool(p.rc))
            self.assertEqual(p.rc, [df.EXIT_MISMATCH])
            self.assertEqual(tg_path(p.e.raw["root"]).read_bytes(), before)
            p.bridge.gap_tick(wall_ms())
            self.assertIn(c0.FEED_BREAK_LATCH, p.bridge.state.latches)
            self.assertIn("mismatch", [json.loads(x)["kind"] for x in (Path(p.e.raw["root"]) / "transport" / f"{ARM}.gaps.jsonl").read_text().splitlines()])
            # buys are refused; which reason is reported first depends on the gap-hold ordering of #1090 (feed_gap_hold when it is in the tree)
            self.assertIn(p.refusal(), {"transport_stall:feed_state_stopped_mismatch", "feed_gap_hold"})


class RelayContractTest(TmpCase):
    def test_the_real_relays_output_is_accepted_by_the_carry_and_serve_sends_it(self):
        """The one test that runs the real tools/hv4_gap_relay.py (on a SYNTHETIC tape in a temp dir): its stream passes the carry's validator and goes out as a tg frame."""
        from tools import hv4_gap_relay as R
        tape = Path(self.tmp) / "tape"
        tape.mkdir()
        t = 1_791_300_000_000
        (tape / "2026-10-06.jsonl").write_text(
            json.dumps({"type": "gap", "kind": "process_restart", "t_recv_ms": t}) + "\n" + json.dumps({"type": "trade", "t_recv_ms": t + 1}) + "\n"
            + json.dumps({"type": "trade", "t_recv_ms": t + 1 + 60_001}) + "\n")
        out = Path(self.tmp) / "relay-out"
        self.assertEqual(R.main(["--tape-dir", str(tape), "--bot-id", ARM, "--out-dir", str(out)]), 0)
        f = out / "transport" / f"{ARM}.tapegaps.jsonl"
        self.assertIsNone(df.tapegaps_problem(f.read_bytes()))
        self.assertEqual([json.loads(x) for x in f.read_text().splitlines()], [{"kind": "process_restart", "ms": t}, {"kind": "gap_silence", "ms": t + 1 + 60_001}])
        frames: list[bytes] = []
        srv = df.Server(str(Path(self.tmp) / "src"), ARM, write=frames.append, tapegaps_file=str(f))
        self.assertEqual(srv.poll_tapegaps(), 1)
        self.assertEqual(json.loads(frames[0])["b"], f.read_text())


if __name__ == "__main__":
    unittest.main()
