"""HV4 C0 owner-file identity pin and the `owner_line` latch (DEC-031 Amendment 21 item 4; QP comment 6527456651 on #1100). Synthetic fixtures only: temp dirs,
the root checks off (the real root check is a separate, tested function), a stub order port, no network, no key. No HV4 ledger, fill, decision row of a real book,
read record, bots.json or job log is opened.

The rule under test: at the first good check the bridge pins (path, st_dev, st_ino, sha256) of /etc/mal-hv4-c0/<bot_id>/CANARY_DEC.md in its state. A later rewrite, a
replacement (even with identical bytes) or a removal latches `owner_line`: new buys stop (hard), exits continue, the latch survives a restart and only the manager clears it
with the bridge stopped. Before a pin exists a missing file keeps the old behaviour (entries DRY, start refused). A DRY bridge is unaffected.

The module-level switch hv4_live_c0.LIVE_REFUSAL_ACTIVE decides only start_refusal and main(); the Bridge tests below do not depend on it, and the main() tests run under both values."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_live_bridge as alb
from tools import hv4_c0_parity as c0p
from tools import hv4_live_c0 as c0
from tools.test_arena_live_bridge import TmpCase
from tools.test_hv4_live_c0 import ARM, Hv4Env, owner_fixture
from tools.test_hv4_live_c0_start_gate import MainBase

LINE = c0.OWNER_LINE_FMT.format(bot_id=ARM)


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PureCheckTests(TmpCase):
    """owner_line_check / owner_line_problem with and without a pin."""

    def fx(self, text=None):
        p = owner_fixture(self.tmp, ARM)
        if text is not None:
            p.write_text(text)
        return p

    def pinned(self, p):
        why, ident = c0.owner_line_check(p, ARM)
        self.assertIsNone(why)
        return ident

    def test_identity_is_path_dev_ino_and_the_sha256_of_the_bytes_searched(self):
        p = self.fx()
        why, ident = c0.owner_line_check(p, ARM)
        st = os.stat(p)
        self.assertIsNone(why)
        self.assertEqual(ident, {"path": str(p), "dev": st.st_dev, "ino": st.st_ino, "sha256": sha(p)})
        self.assertEqual(tuple(ident), c0.OWNER_PIN_KEYS)
        self.assertEqual(c0.owner_line_problem(p, ARM), None)  # the str wrapper agrees
        self.assertEqual(c0.owner_line_problem(p, ARM, None, ident), None)

    def test_unpinned_behaviour_is_unchanged(self):
        for pin in (None, {}):
            self.assertEqual(c0.owner_line_problem(Path(self.tmp) / "none.md", ARM, None, pin), "owner_dec_missing")
            self.assertEqual(c0.owner_line_problem(self.fx("x\n"), ARM, None, pin), "owner_line_missing")
        # a bad file still reports its identity, but the caller pins only on None
        why, ident = c0.owner_line_check(self.fx("x\n"), ARM)
        self.assertEqual(why, "owner_line_missing")
        self.assertEqual(ident["sha256"], hashlib.sha256(b"x\n").hexdigest())

    def test_same_file_passes_with_the_pin(self):
        p = self.fx()
        pin = self.pinned(p)
        for _ in range(3):
            self.assertEqual(c0.owner_line_check(p, ARM, None, pin), (None, pin))

    def test_a_rewrite_with_other_bytes_is_a_change_even_when_the_line_is_still_there(self):
        p = self.fx()
        pin = self.pinned(p)
        ino = os.stat(p).st_ino
        for text in (LINE + "\n# edited\n", "# edited\n" + LINE + "\n", LINE, "x\n", ""):
            p.write_text(text)  # in place: the same inode, other bytes
            self.assertEqual(os.stat(p).st_ino, ino)
            self.assertEqual(c0.owner_line_problem(p, ARM, None, pin), c0.OWNER_LINE_CHANGED, repr(text))  # also for the text that drops the line: a change, not owner_line_missing

    def test_a_replacement_with_identical_bytes_is_a_change(self):
        p = self.fx()
        pin = self.pinned(p)
        data = p.read_bytes()
        tmp = p.with_name("new.md")
        tmp.write_bytes(data)
        os.replace(tmp, p)  # a new inode, the same bytes
        self.assertEqual(sha(p), pin["sha256"])
        self.assertNotEqual(os.stat(p).st_ino, pin["ino"])
        self.assertEqual(c0.owner_line_problem(p, ARM, None, pin), c0.OWNER_LINE_CHANGED)

    def test_removal_is_a_change_but_only_once_pinned(self):
        p = self.fx()
        pin = self.pinned(p)
        os.unlink(p)
        self.assertEqual(c0.owner_line_problem(p, ARM, None, pin), c0.OWNER_LINE_CHANGED)
        self.assertEqual(c0.owner_line_problem(p, ARM), "owner_dec_missing")  # no pin: today's label
        os.rmdir(p.parent)
        self.assertEqual(c0.owner_line_problem(p, ARM, None, pin), c0.OWNER_LINE_CHANGED)  # the directory too

    def test_what_replaces_a_pinned_file_is_a_change(self):
        p = self.fx()
        pin = self.pinned(p)
        os.unlink(p)
        other = p.with_name("other.md")
        other.write_text(LINE + "\n")
        os.symlink(other, p)
        self.assertEqual(c0.owner_line_problem(p, ARM, None, pin), c0.OWNER_LINE_CHANGED)
        self.assertEqual(c0.owner_line_problem(p, ARM), "owner_dec_symlink")
        os.unlink(p)
        os.mkdir(p)
        self.assertEqual(c0.owner_line_problem(p, ARM, None, pin), c0.OWNER_LINE_CHANGED)
        self.assertEqual(c0.owner_line_problem(p, ARM), "owner_dec_not_regular")

    def test_a_file_grown_past_the_bound_is_a_change(self):
        p = self.fx()
        pin = self.pinned(p)
        p.write_text("x" * (c0.OWNER_DEC_MAX_BYTES + 1))
        self.assertEqual(c0.owner_line_problem(p, ARM, None, pin), c0.OWNER_LINE_CHANGED)

    def test_a_pin_for_another_path_or_a_damaged_pin_is_a_change(self):
        p = self.fx()
        pin = self.pinned(p)
        self.assertEqual(c0.owner_line_problem(p, ARM, None, {**pin, "path": "/etc/elsewhere"}), c0.OWNER_LINE_CHANGED)
        for k in c0.OWNER_PIN_KEYS:
            self.assertEqual(c0.owner_line_problem(p, ARM, None, {**pin, k: "bad"}), c0.OWNER_LINE_CHANGED, k)
            self.assertEqual(c0.owner_line_problem(p, ARM, None, {kk: v for kk, v in pin.items() if kk != k}), c0.OWNER_LINE_CHANGED, k)

    def test_the_inode_is_compared_before_the_link_count_and_the_root_check(self):
        p = self.fx()
        pin = self.pinned(p)
        seen = []
        new = p.with_name("new.md")
        new.write_text(LINE + "\n")  # created while the old file still exists, so its inode differs (a freed inode number can be reused by the next create)
        os.replace(new, p)
        self.assertEqual(c0.owner_line_problem(p, ARM, lambda x: seen.append(x) or "unsafe", pin), c0.OWNER_LINE_CHANGED)
        self.assertEqual(seen, [])  # the change was found before the root check ran

    def test_a_problem_that_keeps_the_identity_keeps_its_own_label_and_is_not_a_change(self):
        p = self.fx()
        pin = self.pinned(p)
        self.assertEqual(c0.owner_line_problem(p, ARM, lambda _p: "not_root_owned", pin), "owner_dec_not_root_owned")  # a root-check label: DRY, no latch
        hard = p.with_name("hard.md")
        os.link(p, hard)
        self.assertEqual(c0.owner_line_problem(p, ARM, None, pin), "owner_dec_hardlink")
        os.unlink(hard)
        self.assertIsNone(c0.owner_line_problem(p, ARM, None, pin))  # and it recovers by itself: the same inode, the same bytes

    def test_a_swap_between_the_check_and_the_open_is_a_change_when_pinned(self):
        p = self.fx()
        pin = self.pinned(p)
        other = p.with_name("other.md")
        other.write_text(LINE + "\n")
        real_open = os.open

        def swapping_open(path, flags, *a, **k):
            if os.fspath(path) == os.fspath(p):
                os.replace(other, p)
            return real_open(path, flags, *a, **k)

        with mock.patch("os.open", swapping_open):
            self.assertEqual(c0.owner_line_problem(p, ARM, None, pin), c0.OWNER_LINE_CHANGED)

    def test_the_latch_name_is_wired_as_a_hard_latch(self):
        self.assertEqual(c0.OWNER_LINE_LATCH, "owner_line")
        self.assertIn(c0.OWNER_LINE_LATCH, alb.LATCHES)
        self.assertIn(c0.OWNER_LINE_LATCH, c0p.HARD_LATCHES)
        st = c0p.parity_state(1, loss_ok=True, balance_ms=1, twins_file="/x", trip_ids=[], chain_ok=True, control_ok=True, hard_halts={"owner_line": "owner_line_changed"})
        self.assertEqual(st["unresolved_hard_halts"], ["owner_line"])  # the parity state counts it as an unresolved hard halt


class LiveBridgeBase(TmpCase):
    """A LIVE hv4_c0 bridge on a stub live port, LIVE_OK and TIER in place, the owner file a synthetic stand-in (root checks off)."""

    def setUp(self):
        super().setUp()
        self.e = Hv4Env(self.tmp, live=True)
        self.e.live_ok.write_text(ARM + "\n")
        self.e.tier.write_text("T1\n")
        self.owner = owner_fixture(self.tmp, ARM)

    def bridge(self):
        """A new Bridge process on the same state dir (a restart)."""
        self.e.refresh_halt_inputs()
        raw = {k: v for k, v in self.e.raw.items() if k not in ("stop_file", "halt_file")}
        cfg = alb.Config.from_dict({**raw, "mode": "live"})
        cfg = alb.Config(**{**cfg.__dict__, "stop_file": self.e.raw["stop_file"], "halt_file": self.e.raw["halt_file"]})  # tests only: the pinned paths cannot be set via config
        return alb.Bridge(cfg, self.e.port, now_ms=self.e.clock, live_ok_path=self.e.live_ok, tier_path=self.e.tier, root_checks=False, owner_dec_path=self.owner)

    def state_file(self):
        return json.loads((Path(self.e.raw["state_dir"]) / "bridge-state-live.json").read_text())

    def rows(self, kind):
        return self.e.log_rows(kind)

    def buys(self):
        return [c[1] for c in self.e.port.calls if c[0] == "buy"]

    def sells(self):
        return [c[1] for c in self.e.port.calls if c[0] == "sell"]

    def replace_with_same_bytes(self):
        data = self.owner.read_bytes()
        tmp = self.owner.with_name("new.md")
        tmp.write_bytes(data)
        os.replace(tmp, self.owner)


class PinTakenTests(LiveBridgeBase):
    def test_the_pin_is_taken_at_the_first_good_check_and_persisted(self):
        b = self.bridge()  # construction is a check: the file is good, so it is pinned
        st = os.stat(self.owner)
        want = {"path": str(self.owner), "dev": st.st_dev, "ino": st.st_ino, "sha256": sha(self.owner)}
        self.assertEqual(b.state.owner_pin, want)
        self.assertEqual(self.state_file()["owner_pin"], want)  # on disk
        pinned_rows = self.rows("owner_line_pinned")
        self.assertEqual(len(pinned_rows), 1)
        self.assertEqual((pinned_rows[0]["sha256"], pinned_rows[0]["ino"]), (want["sha256"], want["ino"]))
        self.assertEqual(b.effective_live(), (True, None))
        self.assertEqual(len(self.rows("owner_line_pinned")), 1)  # pinned once, not on every check
        self.assertEqual(b.state.latches, {})
        self.assertEqual(self.bridge().state.owner_pin, want)  # a restart loads the same pin and does not re-pin

    def test_the_pin_is_taken_at_the_first_good_effective_live_call_when_the_file_arrives_later(self):
        os.unlink(self.owner)
        b = self.bridge()
        self.assertIsNone(b.state.owner_pin)
        self.assertEqual(b.effective_live(), (False, "live_ok_owner_dec_missing"))  # before any pin: today's behaviour, DRY and no latch
        self.assertEqual(b.state.latches, {})
        self.assertIsNone(b.state.owner_pin)
        owner_fixture(self.tmp, ARM)
        self.assertEqual(b.effective_live(), (True, None))
        self.assertEqual(b.state.owner_pin["sha256"], sha(self.owner))
        self.assertEqual(self.state_file()["owner_pin"]["sha256"], sha(self.owner))

    def test_a_file_that_is_not_good_is_never_pinned(self):
        self.owner.write_text("not the line\n")
        b = self.bridge()
        self.assertEqual(b.effective_live(), (False, "live_ok_owner_line_missing"))
        self.assertIsNone(b.state.owner_pin)
        self.owner.write_text("another bot's line\n" + c0.OWNER_LINE_FMT.format(bot_id="hv4-BIG-X") + "\n")
        self.assertEqual(b.effective_live(), (False, "live_ok_owner_line_missing"))
        self.assertIsNone(b.state.owner_pin)
        self.assertEqual(b.state.latches, {})

    def test_a_pre_pin_removal_keeps_todays_behaviour_for_entries(self):
        b = self.bridge()
        b.state.owner_pin = None  # as if no first good check had happened yet
        os.unlink(self.owner)
        self.assertEqual(b.effective_live(), (False, "live_ok_owner_dec_missing"))
        self.assertEqual(b.state.latches, {})
        self.e.start()
        self.e.entry_sz("o1")
        b.tick()
        self.assertEqual(self.buys(), [])  # the stub port is live, so a live send would show here
        self.assertEqual(b.state.orders["o1"]["live"], False)  # routed DRY: logged, not sent
        self.assertEqual(b.state.orders["o1"]["dry_reason"], "live_ok_owner_dec_missing")


class ChangeLatchTests(LiveBridgeBase):
    def assert_latched(self, b, via_restart=False):
        self.assertEqual(b.effective_live(), (False, "live_ok_owner_line_changed"))
        self.assertEqual(b.state.latches.get("owner_line"), "owner_line_changed")
        self.assertEqual(self.state_file()["latches"].get("owner_line"), "owner_line_changed")  # on disk
        self.assertEqual(len(self.rows("latch")), 1)

    def test_a_rewrite_with_different_bytes_latches(self):
        b = self.bridge()
        self.assertEqual(b.effective_live(), (True, None))
        self.owner.write_text(LINE + "\n# edited by root\n")  # the line is still there
        self.assert_latched(b)

    def test_a_replacement_with_identical_bytes_on_a_new_inode_latches(self):
        b = self.bridge()
        before = sha(self.owner)
        self.replace_with_same_bytes()
        self.assertEqual(sha(self.owner), before)
        self.assert_latched(b)

    def test_a_removal_latches(self):
        b = self.bridge()
        os.unlink(self.owner)
        self.assert_latched(b)

    def test_a_tick_alone_latches_without_any_entry(self):
        b = self.bridge()
        b.tick()
        self.assertEqual(b.state.latches, {})
        os.unlink(self.owner)
        b.tick()  # no entry row: the latch comes from the tick, so the alert and the state do not wait for the next buy
        self.assertEqual(b.state.latches, {"owner_line": "owner_line_changed"})
        self.assertEqual(self.state_file()["latches"], {"owner_line": "owner_line_changed"})

    def test_it_latches_even_while_live_ok_is_bad(self):
        b = self.bridge()
        os.unlink(self.e.live_ok)
        os.unlink(self.owner)
        self.assertEqual(b.effective_live(), (False, "live_ok_missing"))  # LIVE_OK's label is kept, and the change is latched all the same
        self.assertIn("owner_line", b.state.latches)

    def test_a_restore_does_not_clear_the_latch(self):
        b = self.bridge()
        saved = self.owner.read_bytes()
        os.unlink(self.owner)
        self.assert_latched(b)
        self.owner.write_bytes(saved)  # the same bytes back, a new inode
        self.assertEqual(b.effective_live(), (False, "live_ok_owner_line_changed"))
        self.assertIn("owner_line", b.state.latches)

    def test_a_new_entry_after_the_change_is_refused_latched_and_nothing_is_sent(self):
        b = self.bridge()
        self.e.start()
        self.e.entry_sz("o1")
        b.tick()
        self.assertEqual(self.buys(), ["o1"])  # control: live with the pinned file
        self.assertTrue(b.state.orders["o1"]["live"])
        self.owner.write_text(LINE + "\n# edited\n")
        self.e.entry_sz("o2", pool="P2")
        b.tick()
        self.assertEqual(self.buys(), ["o1"])
        self.assertNotIn("o2", b.state.orders)
        ref = [r for r in self.rows("entry_refused") if r.get("order_id") == "o2"]
        self.assertEqual([r["why"] for r in ref], ["latched:owner_line"])
        self.assertIn("owner_line", b.state.latches)

    def test_exits_continue_while_latched(self):
        b = self.bridge()
        self.e.start()
        self.e.entry_sz("o1")
        b.tick()
        self.e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1002})
        b.tick()
        self.assertEqual(b.state.orders["o1"]["status"], "open")
        os.unlink(self.owner)  # the change, with a live position open
        self.e.entry_sz("o2", pool="P2")
        self.e.exit("o1")
        b.tick()
        self.assertIn("owner_line", b.state.latches)
        self.assertEqual(self.buys(), ["o1"])  # no new buy
        self.assertEqual(self.sells(), ["o1"])  # the exit ran on the live port
        self.e.port.events.append({"order_id": "o1", "event": "sell_landed", "landed_slot": 1015, "pnl_lamports": -3_000_000})
        b.tick()
        self.assertEqual(b.state.orders["o1"]["status"], "closed")
        self.assertEqual(b.state.realized_lamports, -3_000_000)

    def test_the_backstop_exit_runs_while_latched(self):
        b = self.bridge()
        self.e.start()
        self.e.entry_sz("o1")
        b.tick()
        self.e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1002})
        b.tick()
        os.unlink(self.owner)
        b.tick()  # latch
        self.assertIn("owner_line", b.state.latches)
        self.e.clock.t += self.e.raw["max_hold_ms"] + alb.BACKSTOP_EXTRA_MS + 1
        self.e.refresh_halt_inputs()
        b.tick()
        self.assertEqual(self.sells(), ["o1"])  # the time-stop backstop sold the open position

    def test_the_latch_survives_a_restart_and_a_restored_file_does_not_lift_it(self):
        b = self.bridge()
        saved = self.owner.read_bytes()
        os.unlink(self.owner)
        self.assertEqual(b.effective_live()[1], "live_ok_owner_line_changed")
        del b
        b2 = self.bridge()  # a new process: the state file carries the latch
        self.assertIn("owner_line", b2.state.latches)
        self.assertEqual(b2.effective_live(), (False, "live_ok_owner_line_changed"))
        self.owner.write_bytes(saved)
        self.assertEqual(self.bridge().effective_live(), (False, "live_ok_owner_line_changed"))
        self.e.start()
        self.e.entry_sz("o1")
        b2.tick()
        self.assertEqual(self.buys(), [])
        self.assertEqual([r["why"] for r in self.rows("entry_refused")], ["latched:owner_line"])

    def test_a_restart_with_a_changed_file_latches_at_construction_before_any_entry(self):
        b = self.bridge()
        self.assertEqual(b.state.latches, {})
        del b
        self.owner.write_text(LINE + "\n# edited while the bridge was down\n")
        b2 = self.bridge()
        self.assertIn("owner_line", b2.state.latches)  # no tick, no entry handled yet
        self.assertIn("owner_line", self.state_file()["latches"])
        self.e.start()
        self.e.entry_sz("o1")
        b2.tick()
        self.assertEqual(self.buys(), [])

    def test_the_manager_clear_is_the_latch_and_the_pin_removed_with_the_bridge_stopped(self):
        b = self.bridge()
        os.unlink(self.owner)
        self.assertIn("owner_line", self.bridge().state.latches)
        del b
        new = owner_fixture(self.tmp, ARM)
        new.write_text(LINE + "\n# new line from root\n")
        sp = Path(self.e.raw["state_dir"]) / "bridge-state-live.json"
        raw = json.loads(sp.read_text())
        # clearing only the latch is not enough: the old pin still names the old file
        raw2 = {**raw, "latches": {k: v for k, v in raw["latches"].items() if k != "owner_line"}}
        sp.write_text(json.dumps(raw2))
        self.assertIn("owner_line", self.bridge().state.latches)
        # the clear removes BOTH, and the next good check pins the file that is there then
        raw3 = {**raw2, "owner_pin": None}
        sp.write_text(json.dumps(raw3))
        b3 = self.bridge()
        self.assertEqual(b3.state.latches, {})
        self.assertEqual(b3.state.owner_pin["sha256"], sha(new))
        self.assertEqual(b3.effective_live(), (True, None))
        self.e.start()
        self.e.entry_sz("o1")
        b3.tick()
        self.assertEqual(self.buys(), ["o1"])

    def test_an_old_state_file_without_the_key_loads_unpinned(self):
        b = self.bridge()
        sp = Path(self.e.raw["state_dir"]) / "bridge-state-live.json"
        raw = json.loads(sp.read_text())
        raw.pop("owner_pin")
        sp.write_text(json.dumps(raw))
        del b
        b2 = self.bridge()  # construction pins the (unchanged) file now
        self.assertEqual(b2.state.owner_pin["sha256"], sha(self.owner))


class DryUnaffectedTests(TmpCase):
    def test_a_dry_bridge_never_pins_and_never_latches(self):
        e = Hv4Env(self.tmp, live=False)
        owner = owner_fixture(self.tmp, ARM)
        e.refresh_halt_inputs()
        b = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False, owner_dec_path=owner)
        self.assertEqual(b.effective_live(), (False, "mode_dry"))
        self.assertIsNone(b.state.owner_pin)
        owner.write_text("rewritten\n")
        os.unlink(owner)
        self.assertEqual(b.effective_live(), (False, "mode_dry"))
        self.assertIsNone(b._owner_gate())
        e.start()
        e.entry_sz("o1")
        b.tick()
        self.assertEqual(b.state.latches, {})
        self.assertIsNone(b.state.owner_pin)
        self.assertNotIn("owner_line", json.dumps(json.loads((Path(e.raw["state_dir"]) / "bridge-state-dry.json").read_text())["latches"]))
        self.assertEqual(len([c for c in e.port.calls if c[0] == "buy"]), 1)  # the dry book still enters

    def test_other_profiles_are_unaffected(self):
        from tools.test_arena_live_bridge import Env
        e = Env(self.tmp, live=True)
        e.live_ok.write_text("ok\n")
        e.tier.write_text("T0\n")
        b = e.bridge()
        self.assertIsNone(b._owner_gate())
        self.assertIsNone(b.state.owner_pin)


class MainStartTests(MainBase):
    """main(): with a pin in the state (or the latch set) a changed owner file does not refuse the start, so the bridge can run exits; every other owner label still
    refuses. Both values of LIVE_REFUSAL_ACTIVE."""

    def write_state(self, e, pin=None, latches=None):
        sd = Path(e.raw["state_dir"])
        sd.mkdir(parents=True, exist_ok=True)
        st = alb.BridgeState(mode="live", owner_pin=pin, latches=latches or {})
        st.save(sd / "bridge-state-live.json")

    def pin_of_a_different_file(self):
        p = owner_fixture(self.tmp, ARM)
        ident = c0.owner_line_check(p, ARM)[1]
        return {**ident, "sha256": "0" * 64}  # as if the pinned bytes were others than the ones on disk now

    def test_a_changed_owner_file_does_not_refuse_the_start_under_both_values(self):
        for active in (True, False):
            sub = tempfile.mkdtemp(dir=self.tmp)
            e = Hv4Env(sub)
            self.write_state(e, pin=self.pin_of_a_different_file())
            with mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", active):
                rc, out = self.run_main(e, live=True)
            self.assertEqual(rc, 2, (active, out))
            self.assertNotIn("owner_", out, (active, out))
            self.assertIn(c0.LIVE_REFUSAL if active else "rpc_key_missing", out)  # the next gate decides: the incomplete refusal, or (switch off) the missing rpc key
            if not active:
                self.assertNotIn(c0.LIVE_REFUSAL, out)

    def test_a_set_latch_skips_the_owner_check_at_start(self):
        for active in (True, False):
            sub = tempfile.mkdtemp(dir=self.tmp)
            e = Hv4Env(sub)
            self.write_state(e, pin=self.pin_of_a_different_file(), latches={"owner_line": "owner_line_changed"})
            owner_fixture(self.tmp, ARM).write_text("no line any more\n")  # run_main restores the fixture; the latch alone must carry it
            with mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", active):
                rc, out = self.run_main(e, live=True)
            self.assertNotIn("owner_", out, (active, out))
            self.assertNotIn("startup_refused owner", out)

    def test_without_a_pin_a_bad_owner_file_still_refuses_the_start(self):
        """Before the first good check nothing changes: the labels of owner_line_problem refuse the start. (A missing file is covered in the pr4 tests: run_main always
        re-creates the fixture, so the label is injected here.)"""
        for active in (True, False):
            for label in ("owner_line_missing", "owner_dec_missing"):
                sub = tempfile.mkdtemp(dir=self.tmp)
                e = Hv4Env(sub)
                self.write_state(e, pin=None)
                with mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", active), mock.patch.object(c0, "owner_line_problem", lambda *a, _l=label, **k: _l):
                    rc, out = self.run_main(e, live=True)
                self.assertEqual(rc, 2, (active, label, out))
                self.assertIn(label, out)
                self.assertNotIn(c0.LIVE_REFUSAL, out)

    def test_with_a_pin_another_owner_label_still_refuses_the_start(self):
        for active in (True, False):
            sub = tempfile.mkdtemp(dir=self.tmp)
            e = Hv4Env(sub)
            self.write_state(e, pin=self.pin_of_a_different_file())
            with mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", active), mock.patch.object(c0, "owner_line_problem", lambda *a, **k: "owner_dec_hardlink"):
                rc, out = self.run_main(e, live=True)
            self.assertEqual(rc, 2, (active, out))
            self.assertIn("owner_dec_hardlink", out)

    def test_an_unchanged_pinned_file_starts_as_before_under_both_values(self):
        for active in (True, False):
            sub = tempfile.mkdtemp(dir=self.tmp)
            e = Hv4Env(sub)
            p = owner_fixture(self.tmp, ARM)
            self.write_state(e, pin=c0.owner_line_check(p, ARM)[1])  # run_main rewrites the fixture to the same default bytes in place: the same inode and sha
            with mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", active):
                rc, out = self.run_main(e, live=True)
            self.assertEqual(rc, 2, (active, out))
            self.assertNotIn("owner_", out)
            self.assertIn(c0.LIVE_REFUSAL if active else "rpc_key_missing", out)


class DocTests(unittest.TestCase):
    def test_runbook_paths_and_modes_in_the_docs_match_the_code(self):
        """QP N1: the owner file and LIVE_OK paths, and the modes the code enforces (root:root 0644 in a root-owned dir that is not group/other writable)."""
        self.assertEqual(c0.owner_dec_path("hv4-CON-X"), "/etc/mal-hv4-c0/hv4-CON-X/CANARY_DEC.md")
        self.assertEqual(c0.live_ok_path("hv4-CON-X"), "/etc/mal-arena-live/hv4-c0/hv4-CON-X/LIVE_OK")
        from tools import h5_executor as h5
        self.assertEqual((h5.LIVE_OK_UID, h5.LIVE_OK_GID, h5.LIVE_OK_MODE), (0, 0, 0o644))  # what alb.root_ok_problem enforces on both files
        for doc in (alb.__doc__, c0.__doc__):
            self.assertNotIn("no content pin", doc)
        self.assertIn("0644", alb.__doc__)
        self.assertIn("0644", c0.__doc__)
        self.assertIn("/etc/mal-hv4-c0/<bot_id>/CANARY_DEC.md", alb.__doc__)
        self.assertIn("/etc/mal-arena-live/hv4-c0/<bot_id>/LIVE_OK", alb.__doc__)
        self.assertIn("owner_pin", alb.__doc__)
        self.assertIn("owner_line", c0.__doc__)


if __name__ == "__main__":
    unittest.main()
