"""PR 5a offline tests: the exit snapshot builder inside the sell pin (QP removal precondition 2). Synthetic only: no HV4 row, ledger, read record or job log."""

from __future__ import annotations

import dataclasses
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


class EditedClass:
    """Replaces a pinned class: different source."""


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
        for k in [k for k in c0.SELL_BUILDER_PIN if k.startswith(("pe.", "bridge.", "x."))]:
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

    def test_editing_any_extra_callee_breaks_the_pin(self):
        """The decoders and quote helpers (sim._b64, token_amount, parse_pool_account, parse_global_config, pool_state_from_accounts, Snapshot, _prio, check_sell ...)."""
        for spec in c0.PIN_EXTRA:
            mod, qual = spec.split(":")
            from tools import probe_live  # noqa: F401
            import importlib
            m = importlib.import_module(f"tools.{mod}")
            parts = qual.split(".")
            holder = m if len(parts) == 1 else getattr(m, parts[0])
            attr = parts[-1]
            is_cls = isinstance(getattr(holder, attr), type)
            with self.subTest(spec=spec), mock.patch.object(holder, attr, EditedClass if is_cls else edited):
                if is_cls:  # a replaced class also breaks the keys that read its members, so only "some pin item fails" is asserted
                    self.assertTrue((c0.sell_pin_problem() or "").startswith("sell_builder_pin_"))
                    continue
                self.assertEqual(c0.sell_pin_problem(), f"sell_builder_pin_mismatch:{c0.extra_key(spec)[:-len('_sha256')]}_sha256")
        for must in ("pumpswap_simulate:_b64", "pumpswap_simulate:token_amount", "pumpswap_tx:parse_pool_account", "pumpswap_tx:parse_global_config",
                     "pumpswap_tx:pool_state_from_accounts", "probe_executor:Snapshot", "probe_executor:Executor._prio", "probe_executor:Executor._exit_prio",
                     "probe_executor:check_sell"):
            self.assertIn(must, c0.PIN_EXTRA)

    def test_changing_a_constant_breaks_the_pin(self):
        from tools import probe_executor as pe
        for n in c0.PE_CONSTANTS:
            old = getattr(pe, n)
            new = dataclasses.replace(old, tp=old.tp + 0.01) if dataclasses.is_dataclass(old) else old + 1
            with self.subTest(const=n), mock.patch.object(pe, n, new):
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
        for bad in ({"state": "stalled"}, {"last_frame_ms": 1}, {"stall_ms": 5000}):
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
                self.assertTrue(ref and ref[0]["why"].startswith("transport_stall:"), ref)
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

    def test_missing_status_refuses_everywhere_except_a_marked_synthetic_dry_root(self):
        e = Hv4Env(self.tmp)
        status = Path(e.raw["root"]) / "transport" / f"{e.bot}.status.json"
        os.unlink(status)
        br = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False)
        self.assertEqual(br.feed_refusal(e.clock.t), "transport_stall:feed_status_missing")  # dry, no marker: refused
        (Path(e.raw["root"]) / c0.SYNTHETIC_MARKER).write_text("")
        self.assertIsNone(br.feed_refusal(e.clock.t))  # dry, synthetic marker: allowed
        live = Hv4Env(tempfile.mkdtemp(dir=self.tmp), live=True)
        lb = live.bridge()
        os.unlink(Path(live.raw["root"]) / "transport" / f"{live.bot}.status.json")
        (Path(live.raw["root"]) / c0.SYNTHETIC_MARKER).write_text("")
        self.assertEqual(lb.feed_refusal(live.clock.t), "transport_stall:feed_status_missing")  # live never takes the marker exemption

    def test_presend_rechecks_the_feed_after_the_rpcs(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        e.start()
        e.entry_sz("o1")
        real = e.port.balance

        def balance_then_stall():
            v = real()
            write_feed(e.raw["root"], e.bot, e.clock.t, state="stalled")  # the feed stalls while the RPCs run
            return v
        e.port.balance = balance_then_stall
        b.tick()
        self.assertEqual([c for c in e.port.calls if c[0] == "buy"], [])
        self.assertTrue(any(r.get("presend") and r["why"].startswith("transport_stall:") for r in e.log_rows("entry_refused")), e.log_rows("entry_refused"))

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


class TwinRealpathTests(TmpCase):
    def test_the_realpath_that_was_checked_is_what_the_writer_gets(self):
        from tools import arena_twin_writer as W
        from tools import hv4_c0_twin as tw
        real = Path(self.tmp) / "real-ledger"
        real.mkdir()
        link = Path(self.tmp) / "link-ledger"
        os.symlink(real, link)
        trips = Path(self.tmp) / "t.jsonl"
        trips.write_text("")
        seen = []
        with mock.patch.object(W, "main", side_effect=lambda a: seen.append(a) or 0):
            rc = tw.main(["--trips", str(trips), f"--ledger-dir={link}", "--bot-id", ARM, "--out", str(Path(self.tmp) / "o.jsonl")])
        self.assertEqual(rc, 0)
        opts = dict(x.split("=", 1) for x in seen[0])
        self.assertEqual(opts["--ledger-dir"], os.path.realpath(real))
        self.assertNotEqual(opts["--ledger-dir"], str(link))
        self.assertEqual(sum(x.startswith("--ledger-dir=") for x in seen[0]), 1)

    def test_a_dir_swapped_after_the_parse_cannot_redirect_the_writer(self):
        from tools import arena_twin_writer as W
        from tools import hv4_c0_twin as tw
        good, evil = Path(self.tmp) / "good", Path(self.tmp) / "evil"
        good.mkdir(), evil.mkdir()
        link = Path(self.tmp) / "ledger"
        os.symlink(good, link)
        trips = Path(self.tmp) / "t.jsonl"
        trips.write_text("")

        def swap(argv):  # the swap happens at the writer's call, after the check; the forwarded path is already the real one
            link.unlink()
            os.symlink(evil, link)
            seen.append(argv)
            return 0
        seen = []
        with mock.patch.object(W, "main", side_effect=swap):
            tw.main(["--trips", str(trips), "--ledger-dir", str(link), "--bot-id", ARM, "--out", str(Path(self.tmp) / "o.jsonl")])
        self.assertIn(f"--ledger-dir={os.path.realpath(good)}", seen[0])


class BotIdTests(TmpCase):
    BAD = ("hv4-../x-F", "hv4-a/b-F", "../hv4-CON-F", "hv4-CON-F/", "hv4-a\\b-F", ".hv4-CON-F", "hv4-CON-F\x00", "", " hv4-CON-F", "hv4-" + "a" * 130 + "-F", None, 5, ["hv4-CON-F"])

    def test_problem_function(self):
        for b in self.BAD:
            self.assertEqual(c0.bot_id_problem(b), "bot_id_invalid", repr(b))
        for ok in (ARM, "hv4-CON-F", "rule1", "h5v1q50-boostfloor-a1", "hv4-A.b_c-X"):
            self.assertIsNone(c0.bot_id_problem(ok), ok)

    def test_wrapper_refuses_first(self):
        from tools import hv4_c0_twin as tw
        d = Path(self.tmp) / "led"
        d.mkdir()
        for b in ("hv4-../x-F", "hv4-a/b-F", "hv4-CON-F/.."):
            self.assertEqual(tw.refusal(b, str(d)), "bot_id_invalid", b)
            self.assertEqual(tw.main(["--trips", "t", "--out", str(Path(self.tmp) / "o"), "--ledger-dir", str(d), "--bot-id", b]), 2, b)

    def test_bridge_entry_refuses(self):
        e = Hv4Env(self.tmp)
        for b in ("hv4-../x-F", "hv4-a/b-F"):
            with self.assertRaises(ValueError) as cm:
                alb.Config.from_dict({**e.raw, "bot_id": b})
            self.assertIn("bot_id_invalid", str(cm.exception))
        plain = {"bot_id": "../x", "frozen_sha": "a" * 40, "root": "/r", "state_dir": "/s", "end_ms": 1, "max_hold_ms": 1}
        with self.assertRaises(ValueError):
            alb.Config.from_dict(plain)  # not only the hv4_c0 profile
        cfg = alb.Config.from_dict(e.raw)
        bad = alb.Config(**{**cfg.__dict__, "bot_id": "hv4-a/b-F"})  # a Config built without from_dict
        with self.assertRaises(SystemExit):
            alb.Bridge(bad, e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False)


class WindowManualRecoveryTests(TmpCase):
    def test_labels_and_text(self):
        for lab in ("window_file_unsafe", "window_file_unwritable"):
            self.assertIn(lab, c0.WINDOW_MANUAL_RECOVERY)
            self.assertIn("MANUAL RECOVERY", c0.refusal_text(lab))
            self.assertTrue(c0.refusal_text(lab).startswith(lab))
        self.assertEqual(c0.refusal_text("window_dir_missing"), "window_dir_missing")
        doc = (Path(c0.__file__).resolve().parent.parent / "tools" / "hv4_live_c0.py").read_text()
        self.assertIn("window_file_unsafe", doc)

    def test_bridge_start_prints_the_recovery_text(self):
        e = Hv4Env(self.tmp)
        self.assertEqual(c0.window_start_ms(ARM, 5, create=True), (5, None))
        os.chmod(Path(c0.WINDOW_DIR) / f"{ARM}.json", 0o666)
        with self.assertRaises(SystemExit) as cm:
            e.bridge()
        self.assertIn("window_file_unsafe", str(cm.exception))
        self.assertIn("MANUAL RECOVERY", str(cm.exception))

    def test_stale_tmp_is_unwritable_and_left_for_the_owner(self):
        Hv4Env(self.tmp)
        stale = Path(c0.WINDOW_DIR) / f".{ARM}.{os.getpid()}.tmp"
        stale.write_text("old")
        self.assertEqual(c0.window_start_ms(ARM, 5, create=True), (None, "window_file_unwritable"))
        self.assertEqual(stale.read_text(), "old")  # not ours: not removed, not trusted
        self.assertFalse((Path(c0.WINDOW_DIR) / f"{ARM}.json").exists())

    def test_tmp_is_removed_when_write_or_fsync_fails(self):
        Hv4Env(self.tmp)
        tmp = Path(c0.WINDOW_DIR) / f".{ARM}.{os.getpid()}.tmp"
        with mock.patch.object(os, "fsync", side_effect=OSError("disk")):
            self.assertEqual(c0.window_start_ms(ARM, 5, create=True), (None, "window_file_unwritable"))
        self.assertFalse(tmp.exists())
        self.assertFalse((Path(c0.WINDOW_DIR) / f"{ARM}.json").exists())
        self.assertEqual(c0.window_start_ms(ARM, 7, create=True), (7, None))  # the next start works: nothing stale was left
        self.assertFalse(tmp.exists())


class Section52GapHaltTests(TmpCase):
    """DEC-032 s5.2: the book's own gap records latch feed_break (hard); restart needs 10 gap-free minutes AND the manager's root-owned review file.
    These tests pin the s5 item 2 behaviour as written (c0.FEED_GAP_MANUAL_REVIEW True); tools/test_hv4_c0_gap_hold.py covers the Am.5 (DRAFT) value."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(c0, "FEED_GAP_MANUAL_REVIEW", True)
        p.start()
        self.addCleanup(p.stop)

    def env(self):
        e = Hv4Env(self.tmp)
        e.clear_dir = Path(self.tmp) / "gapclear"
        e.clear_dir.mkdir()
        b = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False, gap_clear_dir=e.clear_dir)
        return e, b

    def gaps(self, e, *rows):
        d = Path(e.raw["root"]) / "transport"
        d.mkdir(exist_ok=True)
        (d / f"{e.bot}.gaps.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def review(self, e, ms):
        (e.clear_dir / f"{e.bot}.json").write_text(json.dumps({"bot_id": e.bot, "reviewed_gap_ms": ms}))

    def test_gap_record_latches_hard_marks_open_trips_and_exits_run(self):
        from tools import hv4_c0_parity as c0p
        e, b = self.env()
        e.start()
        e.entry_sz("o1")
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1002})
        b.tick()
        e.exit("o1")
        self.gaps(e, {"kind": "stall", "ms": e.clock.t, "bot": e.bot, "f": "secret-field"})
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        self.assertIn(c0.FEED_BREAK_LATCH, c0p.HARD_LATCHES)
        self.assertEqual([c[:2] for c in e.port.calls], [("buy", "o1"), ("sell", "o1")])
        e.entry_sz("o2")
        b.tick()
        self.assertNotIn("o2", b.state.orders)

    def test_resume_row_is_not_a_gap_and_only_kind_and_ms_are_read(self):
        e, b = self.env()
        self.gaps(e, {"kind": "resume", "ms": e.clock.t, "gap_ms": 5})
        b.tick()
        self.assertNotIn(c0.FEED_BREAK_LATCH, b.state.latches)
        ms, bad = c0.gap_records_ms(str(Path(e.raw["root"]) / "transport" / f"{e.bot}.gaps.jsonl"))
        self.assertEqual((ms, bad), ([], None))

    def test_clear_needs_ten_gap_free_minutes_and_a_review_file(self):
        e, b = self.env()
        t0 = e.clock.t
        self.gaps(e, {"kind": "disconnect", "ms": t0})
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        e.clock.t = t0 + c0.GAP_FREE_MS + 1  # 10 minutes pass, no review file
        e.refresh_halt_inputs()
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        self.review(e, t0 - 1)  # a review that does not cover the gap
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        self.review(e, t0)
        e.clock.t = t0 + c0.GAP_FREE_MS - 1  # reviewed, but the window has not elapsed
        e.refresh_halt_inputs()
        b.gap_tick(e.clock.t)
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        e.clock.t = t0 + c0.GAP_FREE_MS + 1
        e.refresh_halt_inputs()
        b.tick()
        self.assertNotIn(c0.FEED_BREAK_LATCH, b.state.latches)
        b.tick()
        self.assertNotIn(c0.FEED_BREAK_LATCH, b.state.latches)  # the old gap does not relatch

    def test_a_newer_gap_after_the_review_latches_again(self):
        e, b = self.env()
        t0 = e.clock.t
        self.gaps(e, {"kind": "stall", "ms": t0})
        self.review(e, t0)
        e.clock.t = t0 + c0.GAP_FREE_MS + 1
        e.refresh_halt_inputs()
        b.tick()
        self.assertNotIn(c0.FEED_BREAK_LATCH, b.state.latches)
        self.gaps(e, {"kind": "stall", "ms": t0}, {"kind": "reconnect", "ms": e.clock.t})
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)

    def test_gap_row_in_the_decision_stream_and_forwarder_mismatch_latch(self):
        e, b = self.env()
        e.start()
        e.row({"kind": "gap", "secret": "x"}, ms=e.clock.t - 10)
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        e2 = Hv4Env(tempfile.mkdtemp(dir=self.tmp))
        b2 = e2.bridge()
        write_feed(e2.raw["root"], e2.bot, e2.clock.t, state="stopped_mismatch")
        b2.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b2.state.latches)

    def test_unreadable_gap_log_and_review_file_checks_fail_closed(self):
        e, b = self.env()
        d = Path(e.raw["root"]) / "transport"
        d.mkdir(exist_ok=True)
        (d / f"{e.bot}.gaps.jsonl").write_text("{torn\n")  # a complete malformed line latches
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        self.assertEqual(c0.gap_clear_problem(str(e.clear_dir / "x.json"), e.bot, 0, 10 ** 12), "gap_clear_missing")
        self.assertEqual(c0.gap_clear_problem(str(e.clear_dir / "x.json"), e.bot, 0, 10 ** 12, lambda _p: "not_root_owned"), "gap_clear_not_root_owned")

    def test_transport_stall_is_only_an_extra_guard_and_is_named_so(self):
        e, b = self.env()
        write_feed(e.raw["root"], e.bot, e.clock.t, state="stalled")
        self.assertEqual(b.feed_refusal(e.clock.t), "transport_stall:feed_state_stalled")
        self.assertFalse(b.state.latches)


class DeadRunnerTests(TmpCase):
    def test_runner_silence_refuses_buys_after_the_bound(self):
        self.assertIsNone(c0.runner_silent_problem(None, 1000, 1000 + c0.RUNNER_SILENT_MS))
        self.assertEqual(c0.runner_silent_problem(None, 1000, 1001 + c0.RUNNER_SILENT_MS), "runner_silent")
        self.assertIsNone(c0.runner_silent_problem(5000, 1000, 5000 + c0.RUNNER_SILENT_MS))
        self.assertEqual(c0.runner_silent_problem(5000, 1000, 5001 + c0.RUNNER_SILENT_MS), "runner_silent")
        self.assertIsNone(c0.runner_silent_problem(None, 5000, 1000))  # backward clock: no verdict

    def test_bridge_refuses_after_silence_and_a_new_row_proves_life(self):
        e = Hv4Env(self.tmp)
        b = e.bridge()
        e.start()
        b.tick()
        e.clock.t += c0.RUNNER_SILENT_MS + 1
        e.refresh_halt_inputs()
        self.assertEqual(b.feed_refusal(e.clock.t), "runner_silent")
        e.entry_sz("o1")  # a row arrives: the runner is alive
        b.tick()
        self.assertEqual([c[1] for c in e.port.calls if c[0] == "buy"], ["o1"])

    def test_open_position_is_closed_by_the_time_stop_backstop_with_no_runner_exit_row(self):
        """Dead runner: the feed still reads connected (the forwarder is alive), no exit row ever comes. The bridge's own backstop sells."""
        e = Hv4Env(self.tmp)
        b = e.bridge()
        e.start()
        e.entry_sz("o1")
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1002})
        b.tick()
        self.assertEqual(b.state.orders["o1"]["status"], "open")
        e.clock.t += b.cfg.max_hold_ms + alb.BACKSTOP_EXTRA_MS + 1  # no row of any kind since; the transport keeps saying connected
        write_feed(e.raw["root"], e.bot, e.clock.t)
        write_a3(e.a3_file, e.clock.t)
        b.tick()
        self.assertEqual(e.port.calls[-1], ("sell", "o1", "time_stop_backstop"))
        self.assertEqual(c0.feed_gap_problem(str(Path(e.raw["root"]) / "transport" / f"{e.bot}.status.json"), e.bot, e.clock.t), None)


class GapWindowTests(Section52GapHaltTests):
    """QP R4-R7 on 4c4c41a8."""

    def test_r1_the_critical_wrapper_is_pinned(self):
        try:
            from tools import probe_executor as pe
        except ModuleNotFoundError as e:
            self.skipTest(str(e))
        self.assertIn("probe_executor:critical", c0.PIN_EXTRA)
        with mock.patch.object(pe, "critical", edited):
            self.assertEqual(c0.sell_pin_problem(), "sell_builder_pin_mismatch:x.probe_executor.critical_sha256")

    def test_r4_the_ten_minutes_count_from_the_end_of_a_long_stall(self):
        e, b = self.env()
        t0 = e.clock.t
        self.gaps(e, {"kind": "stall", "ms": t0})
        b.tick()
        self.review(e, t0)
        e.clock.t = t0 + 15 * 60_000  # a 15-minute stall; the feed comes back now
        self.gaps(e, {"kind": "stall", "ms": t0}, {"kind": "resume", "ms": e.clock.t, "gap_ms": 15 * 60_000})
        e.refresh_halt_inputs()
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)  # gap-free for 0 minutes
        e.clock.t += c0.GAP_FREE_MS - 1
        e.refresh_halt_inputs()
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        e.clock.t += 2
        e.refresh_halt_inputs()
        b.tick()
        self.assertNotIn(c0.FEED_BREAK_LATCH, b.state.latches)

    def test_r5_the_review_must_equal_the_newest_gap_instant(self):
        e, b = self.env()
        t0 = e.clock.t
        self.gaps(e, {"kind": "stall", "ms": t0})
        b.tick()
        e.clock.t = t0 + c0.GAP_FREE_MS + 1
        e.refresh_halt_inputs()
        for ms in (t0 + 10 ** 12, t0 + 1, t0 - 1):
            self.review(e, ms)
            b.tick()
            self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches, ms)
        self.review(e, t0)
        b.tick()
        self.assertNotIn(c0.FEED_BREAK_LATCH, b.state.latches)

    def test_r6_the_noted_gap_is_persisted_and_a_restart_cannot_clear_on_an_older_review(self):
        e, b = self.env()
        t0 = e.clock.t
        self.gaps(e, {"kind": "stall", "ms": t0 - 20 * 60_000})
        self.review(e, t0 - 20 * 60_000)  # an older, matching review of the gap-log gap
        e.start()
        e.row({"kind": "gap"}, ms=t0 - 1000)  # a newer gap in the decision stream
        b.tick()
        self.assertEqual(b.state.gap_noted_ms, t0 - 1000)
        e.clock.t = t0 + c0.GAP_FREE_MS + 1
        e.refresh_halt_inputs()
        b2 = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False, gap_clear_dir=e.clear_dir)
        self.assertEqual(b2.state.gap_noted_ms, t0 - 1000)
        self.assertIn(c0.FEED_BREAK_LATCH, b2.state.latches)
        b2.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b2.state.latches)  # the review names an older instant: no clear
        self.review(e, t0 - 1000)
        b2.tick()
        self.assertNotIn(c0.FEED_BREAK_LATCH, b2.state.latches)

    def test_r7_an_oversize_gap_log_is_refused_not_truncated(self):
        e, b = self.env()
        d = Path(e.raw["root"]) / "transport"
        d.mkdir(exist_ok=True)
        f = d / f"{e.bot}.gaps.jsonl"
        with mock.patch.object(c0, "GAP_LOG_MAX_BYTES", 100):
            f.write_text((json.dumps({"kind": "resume", "ms": 1}) + "\n") * 20)
            self.assertEqual(c0.gap_log_ms(str(f)), ([], [], "gap_log_too_large"))
            b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        self.assertEqual(b.state.latches[c0.FEED_BREAK_LATCH], "gap_log_too_large")

    def test_module_doc_says_precondition_7_is_not_met_for_tape_gaps(self):
        self.assertIn("PRECONDITION 7 IS NOT MET FOR TAPE GAPS", c0.__doc__)
        self.assertNotIn("`feed_gap:<why>`", c0.__doc__)


class RootCheckPathTypeTests(TmpCase):
    """Reviewer blocker on 2519c0f5: root_ok_problem got a str and h5.root_file_problem does p.parent. Every call site, with root_checks=True and a real tmp dir.
    The gap-clear call sites exist under s5 item 2 as written (c0.FEED_GAP_MANUAL_REVIEW True), so that value is set here."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(c0, "FEED_GAP_MANUAL_REVIEW", True)
        p.start()
        self.addCleanup(p.stop)
        try:
            from tools import h5_executor  # noqa: F401
        except ModuleNotFoundError as e:
            self.skipTest(str(e))

    def test_root_ok_problem_takes_str_and_path(self):
        d = Path(self.tmp) / "x"
        d.mkdir()
        for arg in (str(d / "nope"), d / "nope"):
            self.assertEqual(alb.root_ok_problem(arg), "missing")
        (d / "f").write_text("x")
        for arg in (str(d / "f"), d / "f"):
            self.assertIn(alb.root_ok_problem(arg), ("unsafe",))  # not root-owned

    def test_gap_clear_with_real_root_checks_never_raises(self):
        e = Hv4Env(self.tmp)
        cd = Path(self.tmp) / "gc"
        cd.mkdir()
        b = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=True, gap_clear_dir=cd)
        t0 = e.clock.t
        d = Path(e.raw["root"]) / "transport"
        d.mkdir(exist_ok=True)
        (d / f"{e.bot}.gaps.jsonl").write_text(json.dumps({"kind": "stall", "ms": t0}) + "\n")
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        e.clock.t = t0 + c0.GAP_FREE_MS + 1
        e.refresh_halt_inputs()
        b.tick()  # the newest gap is > 10 min old: the clear is evaluated (file missing) and must not raise
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        self.assertEqual(c0.gap_clear_problem(str(cd / f"{e.bot}.json"), e.bot, t0, e.clock.t, alb.root_ok_problem), "gap_clear_missing")
        (cd / f"{e.bot}.json").write_text(json.dumps({"bot_id": e.bot, "reviewed_gap_ms": t0}))
        self.assertEqual(c0.gap_clear_problem(str(cd / f"{e.bot}.json"), e.bot, t0, e.clock.t, alb.root_ok_problem), "gap_clear_unsafe")  # not root-owned
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)

    def test_a_raising_root_check_refuses_the_clear_and_never_propagates(self):
        e = Hv4Env(self.tmp)
        b = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=True, gap_clear_dir=Path(self.tmp))
        with mock.patch.object(alb, "root_ok_problem", side_effect=AttributeError("boom")):
            self.assertFalse(b._gap_cleared(1, 10 ** 13))
        t0 = e.clock.t
        d = Path(e.raw["root"]) / "transport"
        d.mkdir(exist_ok=True)
        (d / f"{e.bot}.gaps.jsonl").write_text(json.dumps({"kind": "stall", "ms": t0}) + "\n")
        e.clock.t = t0 + c0.GAP_FREE_MS + 1
        e.refresh_halt_inputs()
        with mock.patch.object(alb, "root_ok_problem", side_effect=AttributeError("boom")):
            b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)

    def test_owner_line_with_real_root_checks_takes_a_str_path(self):
        p = Path(self.tmp) / "dec.md"
        p.write_text(c0.OWNER_LINE_FMT.format(bot_id=ARM) + "\n")
        why = c0.owner_line_problem(str(p), ARM, None, alb.root_ok_problem)
        self.assertNotIn("AttributeError", str(why))
        self.assertTrue(why)  # refused (not root-owned / pin unset), never an exception

    def test_live_ok_and_tier_call_sites_take_paths(self):
        e = Hv4Env(self.tmp, live=True)
        b = alb.Bridge(alb.Config(**{**alb.Config.from_dict({**{k: v for k, v in e.raw.items() if k not in ("stop_file", "halt_file")}, "mode": "live"}).__dict__,
                                     "stop_file": e.raw["stop_file"], "halt_file": e.raw["halt_file"]}), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=True)
        self.assertEqual(b.effective_live()[0], False)  # missing / unsafe, no exception
        self.assertTrue(b.tier_limits()[2])


class GapLogParsingTests(TmpCase):
    def test_torn_tail_is_ignored_complete_malformed_line_is_a_problem(self):
        f = Path(self.tmp) / "g.jsonl"
        f.write_text(json.dumps({"kind": "stall", "ms": 5}) + "\n" + '{"kind": "dis')
        self.assertEqual(c0.gap_records_ms(str(f)), ([5], None))
        f.write_text(json.dumps({"kind": "stall", "ms": 5}) + "\n{bad\n")
        self.assertEqual(c0.gap_records_ms(str(f)), ([5], "gap_log_malformed"))

    def test_gap_kinds_equal_the_forwarders(self):
        import re
        import subprocess
        try:
            src = subprocess.run(["git", "show", "origin/claude/p46-transport:tools/decision_forwarder.py"], capture_output=True, text=True, check=True,
                                 cwd=str(Path(c0.__file__).resolve().parent.parent)).stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.skipTest("origin/claude/p46-transport is not available (merged or not fetched)")
        kinds = set(re.findall(r'self\.gap\("(\w+)"', src)) - {"resume"}
        self.assertTrue(kinds)
        self.assertTrue(kinds <= set(c0.GAP_BREAK_KINDS), kinds - set(c0.GAP_BREAK_KINDS))


class A3EquivalenceTests(TmpCase):
    # The equality test against h5_arena_executor.a3_record_problem moved to tools/test_hv4_a3_pooled.py (DEC-032 Am.3: equality with the Q50 pooled-guard path).

    def test_link_and_owner_checks_on_the_record_file(self):
        now = 1_800_000_000_000
        real = Path(self.tmp) / "real.jsonl"
        write_a3(real, now)
        link = Path(self.tmp) / "link.jsonl"
        os.symlink(real, link)
        self.assertEqual(c0.a3_halt_problem(str(link), now), "a3_record_not_regular")
        hard = Path(self.tmp) / "hard.jsonl"
        os.link(real, hard)
        self.assertEqual(c0.a3_halt_problem(str(real), now), "a3_record_not_regular")
        hard.unlink()
        os.chmod(real, 0o666)
        self.assertEqual(c0.a3_halt_problem(str(real), now), "a3_record_owner_mode")
        os.chmod(real, 0o644)
        self.assertIsNone(c0.a3_halt_problem(str(real), now))


class QuotePathRegressionTests(unittest.TestCase):
    """QP edited one statement of each of these and sell_pin_problem() stayed None (#969 B2)."""

    def test_a_body_edit_of_each_cross_module_callee_trips_the_pin(self):
        try:
            from tools import probe_live  # noqa: F401
        except ModuleNotFoundError as e:
            self.skipTest(str(e))
        import tools.paper_curve_math as pcm
        import tools.pumpswap_tx as ptx
        for mod, name, key in ((ptx, "cp_sell_out", "x.pumpswap_tx.cp_sell_out_sha256"), (ptx, "parse_pool_account", "x.pumpswap_tx.parse_pool_account_sha256"),
                               (pcm, "pumpswap_sol_fee_ppm", "x.paper_curve_math.pumpswap_sol_fee_ppm_sha256"),
                               (ptx, "parse_global_config", "x.pumpswap_tx.parse_global_config_sha256"), (ptx, "canonical_pool", "x.pumpswap_tx.canonical_pool_sha256"),
                               (ptx, "pool_state_from_accounts", "x.pumpswap_tx.pool_state_from_accounts_sha256")):
            with self.subTest(fn=name), mock.patch.object(mod, name, edited):
                self.assertEqual(c0.sell_pin_problem(), f"sell_builder_pin_mismatch:{key}")


class StallMsTests(TmpCase):
    def test_a_status_with_a_larger_stall_ms_is_refused(self):
        now = 10_000_000
        for sm in (2001, 5000, 0, True, "2000", None):
            f = write_feed(self.tmp, ARM, now, stall_ms=sm)
            self.assertEqual(c0.feed_gap_problem(str(f), ARM, now), "feed_stall_ms_not_pinned", sm)
        f = write_feed(self.tmp, ARM, now, stall_ms=2000)
        self.assertIsNone(c0.feed_gap_problem(str(f), ARM, now))


class N_E_Tests(TmpCase):
    def test_refusal_is_given_the_resolved_path(self):
        from tools import hv4_c0_twin as tw
        real = Path(self.tmp) / "real"
        real.mkdir()
        link = Path(self.tmp) / "link"
        os.symlink(real, link)
        seen = []
        orig = tw.refusal
        with mock.patch.object(tw, "refusal", side_effect=lambda b, l, r=None: seen.append(l) or orig(b, l, r)):
            tw.main(["--trips", "t", "--ledger-dir", str(link), "--bot-id", ARM, "--out", str(Path(self.tmp) / "o")])
        self.assertEqual(seen, [os.path.realpath(real)])


if __name__ == "__main__":
    unittest.main()
