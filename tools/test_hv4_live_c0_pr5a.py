"""PR 5a offline tests: the exit snapshot builder inside the sell pin (QP removal precondition 2). Synthetic only: no HV4 row, ledger, read record or job log."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_live_bridge as alb
from tools import hv4_live_c0 as c0
from tools.test_arena_live_bridge import TmpCase
from tools.test_hv4_live_c0 import ARM, Hv4Env, write_a3, write_feed


def edited(*a, **k):  # source differs from every pinned function
    return "edited"


class ExitSnapshotPinTests(unittest.TestCase):
    def setUp(self):
        try:
            from tools import probe_executor  # noqa: F401
        except ModuleNotFoundError as e:
            self.skipTest(str(e))

    def test_pin_has_every_listed_piece(self):
        keys = set(c0.SELL_BUILDER_PIN)
        for n in c0.PE_FUNCS:
            self.assertIn(f"pe.{n}_sha256", keys)
        for cn, mn in c0.PE_METHODS:
            self.assertIn(f"pe.{cn}.{mn}_sha256", keys)
        self.assertIn("pe.constants_sha256", keys)
        for n in c0.BRIDGE_METHODS:
            self.assertIn(f"bridge.{n}_sha256", keys)
        for must in ("pe.fetch_snapshot_sha256", "pe.Executor._snapshot_sha256", "pe.Executor._batched_exit_snapshots_sha256", "pe.Executor._exit_snapshot_sha256",
                     "pe.Snapshot.quote_priced_sha256", "bridge.poll_positions_sha256", "bridge.refresh_warm_sha256"):
            self.assertIn(must, keys)
        self.assertIsNone(c0.sell_pin_problem())

    def test_every_new_key_mismatch_is_named(self):
        for k in [k for k in c0.SELL_BUILDER_PIN if k.startswith(("pe.", "bridge."))]:
            with self.subTest(k=k):
                self.assertEqual(c0.sell_pin_problem({**c0.SELL_BUILDER_PIN, k: "0" * 64}), f"sell_builder_pin_mismatch:{k}")
                self.assertEqual(c0.sell_pin_problem({kk: v for kk, v in c0.SELL_BUILDER_PIN.items() if kk != k}), f"sell_builder_pin_mismatch:{k}")

    def test_editing_a_probe_executor_function_breaks_the_pin(self):
        from tools import probe_executor as pe
        for n in c0.PE_FUNCS:
            with self.subTest(fn=n), mock.patch.object(pe, n, edited):
                self.assertEqual(c0.sell_pin_problem(), f"sell_builder_pin_mismatch:pe.{n}_sha256")
        for cn, mn in c0.PE_METHODS:
            with self.subTest(method=f"{cn}.{mn}"), mock.patch.object(getattr(pe, cn), mn, property(edited) if cn == "Snapshot" else edited):
                self.assertEqual(c0.sell_pin_problem(), f"sell_builder_pin_mismatch:pe.{cn}.{mn}_sha256")
        self.assertIsNone(c0.sell_pin_problem())

    def test_changing_a_constant_breaks_the_pin(self):
        from tools import probe_executor as pe
        for n in c0.PE_CONSTANTS:
            with self.subTest(const=n), mock.patch.object(pe, n, getattr(pe, n) + 1):
                self.assertEqual(c0.sell_pin_problem(), "sell_builder_pin_mismatch:pe.constants_sha256")
        for n in ("BATCH_MAX_AGE_MS", "STATIC_TTL_MS"):
            self.assertIn(n, c0.PE_CONSTANTS)

    def test_editing_a_bridge_method_breaks_the_pin(self):
        real = Path(c0.__file__).with_name("arena_live_bridge.py")
        for n in c0.BRIDGE_METHODS:
            src = real.read_text()
            seg = c0.bridge_method_source(n)
            self.assertIn(seg, src)
            with tempfile.TemporaryDirectory() as d:
                alt = Path(d) / "arena_live_bridge.py"
                alt.write_text(src.replace(seg, seg + "\n            pass  # edit", 1))
                real_fn = c0.bridge_method_source
                with mock.patch.object(c0, "bridge_method_source", lambda name, path=None, f=real_fn, a=str(alt): f(name, a)):
                    self.assertEqual(c0.sell_pin_problem(), f"sell_builder_pin_mismatch:bridge.{n}_sha256", n)
        self.assertIsNone(c0.sell_pin_problem())

    def test_missing_bridge_method_is_unreadable(self):
        with mock.patch.object(c0, "bridge_method_source", side_effect=KeyError("poll_positions")):
            self.assertEqual(c0.sell_pin_problem(), "sell_builder_pin_unreadable:bridge.poll_positions_sha256")


class FeedProblemTests(TmpCase):
    def prob(self, now=10_000_000, **over):
        f = write_feed(self.tmp, ARM, now, **over)
        return c0.feed_gap_problem(str(f), ARM, now)

    def test_fresh_connected_feed_is_clear(self):
        self.assertIsNone(self.prob())

    def test_stall_states_and_ages_refuse(self):
        now = 10_000_000
        for state in ("stalled", "disconnected", "stopped_mismatch", "stopped", "init", "connecting"):
            self.assertEqual(self.prob(state=state), f"feed_state_{state}", state)
        self.assertEqual(self.prob(last_frame_ms=now - c0.FEED_FRAME_MAX_MS - 1), "feed_silent")
        self.assertIsNone(self.prob(last_frame_ms=now - c0.FEED_FRAME_MAX_MS))
        self.assertEqual(self.prob(ms=now - c0.FEED_STATUS_MAX_AGE_MS - 1), "feed_status_stale")
        self.assertEqual(self.prob(last_frame_ms=0), "feed_silent")
        self.assertEqual(self.prob(ms=now + c0.FEED_SKEW_MS + 1), "feed_status_future")

    def test_malformed_foreign_missing_and_linked_fail_closed(self):
        now = 10_000_000
        self.assertEqual(self.prob(bot="hv4-CON-F"), "feed_status_foreign")
        self.assertEqual(self.prob(schema="other"), "feed_status_foreign")
        self.assertEqual(self.prob(ms=True), "feed_status_malformed")
        self.assertEqual(self.prob(last_frame_ms="1"), "feed_status_malformed")
        f = Path(self.tmp) / "transport" / f"{ARM}.status.json"
        f.write_text("{not json")
        self.assertEqual(c0.feed_gap_problem(str(f), ARM, now), "feed_status_malformed")
        f.write_text("x" * (c0.FEED_STATUS_MAX_BYTES + 1))
        self.assertEqual(c0.feed_gap_problem(str(f), ARM, now), "feed_status_oversize")
        f.unlink()
        self.assertEqual(c0.feed_gap_problem(str(f), ARM, now), "feed_status_missing")
        real = write_feed(self.tmp, "other-bot", now)
        os.symlink(real, f)
        self.assertEqual(c0.feed_gap_problem(str(f), ARM, now), "feed_status_not_regular")
        f.unlink()
        os.link(real, f)
        self.assertEqual(c0.feed_gap_problem(str(f), ARM, now), "feed_status_not_regular")


class A3ProblemTests(TmpCase):
    def test_clear_and_pins_changed(self):
        now = 1_800_000_000_000
        p = str(Path(self.tmp) / "a3.jsonl")
        write_a3(p, now)
        self.assertIsNone(c0.a3_halt_problem(p, now))
        write_a3(p, now, halt=True)
        self.assertEqual(c0.a3_halt_problem(p, now), "a3_flags:pins_changed")

    def test_newest_line_is_the_state(self):
        now = 1_800_000_000_000
        p = Path(self.tmp) / "a3.jsonl"
        write_a3(p, now, halt=True)
        old = p.read_text()
        write_a3(p, now)
        p.write_text(old + p.read_text())
        self.assertIsNone(c0.a3_halt_problem(str(p), now))
        p.write_text(p.read_text() + old)
        self.assertEqual(c0.a3_halt_problem(str(p), now), "a3_flags:pins_changed")

    def test_fail_closed_cases(self):
        now = 1_800_000_000_000
        p = Path(self.tmp) / "a3.jsonl"
        self.assertEqual(c0.a3_halt_problem(str(p), now), "a3_record_unreadable")
        p.write_text("")
        self.assertEqual(c0.a3_halt_problem(str(p), now), "a3_record_unreadable")
        p.write_text("{}\n")
        self.assertEqual(c0.a3_halt_problem(str(p), now), "a3_record_no_halt_flags")
        write_a3(p, now, halt=False)
        self.assertEqual(c0.a3_halt_problem(str(p), now + c0.A3_MAX_AGE_MS + 1000), "a3_record_stale")
        self.assertEqual(c0.a3_halt_problem(str(p), now - c0.A3_FUTURE_SKEW_MS - 1000), "a3_record_future")
        write_a3(p, now, halt=False, halt_override=1)
        rec = json.loads(p.read_text())
        rec["halt"]["flags"]["boost_disabled"] = {"halt": False, "evaluated": False}  # not evaluated is unknown, not clear
        p.write_text(json.dumps(rec) + "\n")
        self.assertEqual(c0.a3_halt_problem(str(p), now), "a3_flags:boost_disabled")
        rec["halt"]["flags"]["boost_disabled"] = "x"
        p.write_text(json.dumps(rec) + "\n")
        self.assertEqual(c0.a3_halt_problem(str(p), now), "a3_flags:boost_disabled")
        rec["halt"]["flags"]["boost_disabled"] = {"halt": False, "evaluated": True}
        rec["warn"]["flags"]["program_changed"] = {"warn": True}
        p.write_text(json.dumps(rec) + "\n")
        self.assertEqual(c0.a3_halt_problem(str(p), now), "a3_flags:program_changed")


class HaltBridgeTests(TmpCase):
    def open_position(self, e, b):
        e.start()
        e.entry_sz("o1")
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1002})
        b.tick()
        self.assertEqual(b.state.orders["o1"]["status"], "open")

    def test_latch_name_is_a_parity_hard_halt(self):
        from tools import hv4_c0_parity as c0p
        self.assertIn(c0.A3_LATCH, alb.LATCHES)
        self.assertIn(c0.A3_LATCH, c0p.HARD_LATCHES)

    def test_feed_gap_stops_new_buys_but_exits_run(self):
        for bad in ({"state": "stalled"}, {"last_frame_ms": 1}, {"state": "stopped_mismatch"}):
            with self.subTest(bad=bad):
                e = Hv4Env(tempfile.mkdtemp(dir=self.tmp), live=False)
                b = e.bridge()
                self.open_position(e, b)
                e.entry_sz("o2")
                e.exit("o1")
                write_feed(e.raw["root"], e.bot, e.clock.t, **bad)
                b.tick()
                self.assertEqual([c[:2] for c in e.port.calls], [("buy", "o1"), ("sell", "o1")])  # the exit ran, the new entry did not
                ref = [r for r in e.log_rows("entry_refused") if r.get("order_id") == "o2" or r.get("why", "").startswith("feed_gap")]
                self.assertTrue(ref and ref[0]["why"].startswith("feed_gap:"), ref)
                self.assertNotIn("o2", b.state.orders)
                self.assertFalse(b.state.latches)  # not a latch

    def test_feed_resumes_and_buys_again(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        e.start()
        write_feed(e.raw["root"], e.bot, e.clock.t, state="stalled")
        e.declog.append(e.bot, e.clock.t - 100, {"kind": "entry", "order_id": "o1", "pool": "P1", "meta": {}, "decision_slot": 1000, "stake_sol": 0.05, "attempt_key": None})
        write_feed(e.raw["root"], e.bot, e.clock.t, state="stalled")
        b.tick()
        self.assertEqual([c for c in e.port.calls if c[0] == "buy"], [])
        e.entry_sz("o2")  # the row writer refreshes the feed
        b.tick()
        self.assertEqual([c[1] for c in e.port.calls if c[0] == "buy"], ["o2"])

    def test_silent_stream_beyond_the_bound_refuses(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        e.start()
        e.entry_sz("o1")
        e.clock.t += c0.FEED_FRAME_MAX_MS + 1  # the status was written at the old time: stale frame
        write_feed(e.raw["root"], e.bot, e.clock.t, last_frame_ms=e.clock.t - c0.FEED_FRAME_MAX_MS - 1)
        b.tick()
        self.assertEqual([c for c in e.port.calls if c[0] == "buy"], [])

    def test_live_requires_a_status_file_dry_does_not(self):
        e = Hv4Env(self.tmp)
        os.unlink(Path(e.raw["root"]) / "transport" / f"{e.bot}.status.json")
        br = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False)
        self.assertIsNone(br.feed_refusal(e.clock.t))
        live = Hv4Env(tempfile.mkdtemp(dir=self.tmp), live=True)
        lb = live.bridge()
        os.unlink(Path(live.raw["root"]) / "transport" / f"{live.bot}.status.json")
        self.assertEqual(lb.feed_refusal(live.clock.t), "feed_gap:feed_status_missing")

    def test_a3_pins_changed_latches_stops_buys_exits_run(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        self.open_position(e, b)
        e.entry_sz("o2")
        e.exit("o1")
        write_a3(e.a3_file, e.clock.t, halt=True)
        e.clock.t += c0.A3_POLL_MS + 1
        write_feed(e.raw["root"], e.bot, e.clock.t)
        write_a3(e.a3_file, e.clock.t, halt=True)
        b.tick()
        self.assertIn(c0.A3_LATCH, b.state.latches)
        self.assertIn("pins_changed", b.state.latches[c0.A3_LATCH])
        self.assertEqual([c[:2] for c in e.port.calls], [("buy", "o1"), ("sell", "o1")])
        self.assertNotIn("o2", b.state.orders)
        e.entry_sz("o3")
        b.tick()
        self.assertNotIn("o3", b.state.orders)
        self.assertEqual(e.log_rows("entry_refused")[-1]["why"], f"latched:{c0.A3_LATCH}")

    def test_a3_latch_persists_across_a_clean_record_and_a_restart(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        e.start()
        write_a3(e.a3_file, e.clock.t, halt=True)
        b.tick()
        self.assertIn(c0.A3_LATCH, b.state.latches)
        write_a3(e.a3_file, e.clock.t + c0.A3_POLL_MS + 1)  # the flag clears: the latch stays until the manager clears it
        e.clock.t += c0.A3_POLL_MS + 1
        write_feed(e.raw["root"], e.bot, e.clock.t)
        b.tick()
        self.assertIn(c0.A3_LATCH, b.state.latches)
        self.assertIn(c0.A3_LATCH, e.bridge().state.latches)

    def test_a3_poll_is_throttled(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        e.start()
        b.tick()
        write_a3(e.a3_file, e.clock.t, halt=True)
        e.clock.t += 1000
        write_feed(e.raw["root"], e.bot, e.clock.t)
        b.tick()
        self.assertNotIn(c0.A3_LATCH, b.state.latches)

    def test_unreadable_a3_record_latches(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        e.start()
        os.unlink(e.a3_file)
        b.tick()
        self.assertEqual(b.state.latches[c0.A3_LATCH], "a3_record_unreadable")

    def test_live_without_an_a3_record_file_is_refused_at_start_and_latched(self):
        e = Hv4Env(self.tmp, live=True, a3_record_file="")
        b = e.bridge()
        e.start()
        b.tick()
        self.assertEqual(b.state.latches[c0.A3_LATCH], "a3_record_file_missing")

    def test_a3_record_file_must_be_absolute_and_c0_only(self):
        with self.assertRaises(ValueError):
            alb.Config.from_dict({**Hv4Env(self.tmp).raw, "a3_record_file": "rel.jsonl"})
        from tools.test_arena_live_bridge import Env
        with self.assertRaises(ValueError):
            alb.Config.from_dict({**Env(tempfile.mkdtemp(dir=self.tmp)).raw, "a3_record_file": "/x/a3.jsonl"})


if __name__ == "__main__":
    unittest.main()
