"""HV4 C0 recorded-read START GATE and its one switch, hv4_live_c0.LIVE_REFUSAL_ACTIVE (QP #959 removal items 3 and 15). Synthetic fixtures only: a stand-in
directory plays /data/mal/arena (SEALED_ROOT_PREFIX patched to it), a temp reads dir plays the canonical reads dir, no HV4 ledger, decision row, read record,
bots.json or job log is opened, no network, no key.

Both values of the switch are covered:
  * True  (the shipped default): every start outside the synthetic dry path refuses `hv4_c0_incomplete`, exactly as before the gate existed;
  * False (the removal): the gate decides. A start on a non-synthetic root, dry or live, needs a recorded PASS for THIS bot from the canonical reads dir
    (threaded in as an explicit argument), a root and decisions/<bot_id> that neither lie under nor alias the arena, an F or X arm, and for live every gate
    of main()."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_live_bridge as alb
from tools import hv4_live_c0 as c0
from tools.test_arena_live_bridge import TmpCase
from tools.test_hv4_live_c0 import ARM, Hv4Env, live_main_env, owner_fixture, write_read_record

DAY_FILE = "2026-10-30.jsonl"


class Base(TmpCase):
    def setUp(self):
        super().setUp()
        self.sealed = os.path.realpath(os.path.join(self.tmp, "sealed-standin"))  # a stand-in for /data/mal/arena: the real one is never touched
        os.makedirs(os.path.join(self.sealed, "decisions", ARM))
        Path(self.sealed, "decisions", ARM, DAY_FILE).write_text("")
        p = mock.patch.object(c0, "SEALED_ROOT_PREFIX", self.sealed)
        p.start()
        self.addCleanup(p.stop)

    def off(self):
        """LIVE_REFUSAL_ACTIVE False for the rest of this test."""
        p = mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", False)
        p.start()
        self.addCleanup(p.stop)

    def root(self, name="root", mark=False, day=True):
        root = os.path.realpath(os.path.join(self.tmp, name))
        d = os.path.join(root, "decisions", ARM)
        os.makedirs(d)
        if day:
            Path(d, DAY_FILE).write_text("")
        if mark:
            Path(root, c0.SYNTHETIC_MARKER).write_text("")
        return root

    def alias_stat(self, alias_path, to):
        """os.stat shows `alias_path` with the (st_dev, st_ino) of `to`: a bind mount or hard-linked directory under another name."""
        real = os.stat

        def fake(path, *a, **k):
            return real(to if os.fspath(path) == alias_path else path, *a, **k)

        return mock.patch("os.stat", fake)


class SwitchTrueTests(Base):
    """LIVE_REFUSAL_ACTIVE True (every test below patches it explicitly): unchanged behaviour. The gate is not consulted, whatever it is told."""

    def test_the_switch_is_a_plain_bool(self):
        # (This PR ships True; the later one-line flip to False must leave every test green, so no test pins the shipped value. The tests that assert
        # hv4_c0_incomplete patch True themselves, and the gate tests patch False themselves.)
        self.assertIsInstance(c0.LIVE_REFUSAL_ACTIVE, bool)

    def test_every_non_synthetic_start_refuses_even_with_a_pass_and_every_gate(self):
        with mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", True):
            clean = self.root()
            for root in (clean, os.path.join(self.tmp, "no-such-root"), self.sealed, os.path.join(self.sealed, "x")):
                for live in (False, True):
                    for bot in (ARM, None, "hv4-CON-L"):
                        self.assertEqual(c0.start_refusal(root, live, bot, True, True), c0.LIVE_REFUSAL, (root, live, bot))
                        self.assertEqual(c0.start_refusal(root, live, bot), c0.LIVE_REFUSAL, (root, live, bot))

    def test_a_non_false_value_keeps_the_refusal(self):
        clean = self.root()
        for v in (True, None, 0, "", "False", 1):
            with mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", v):
                self.assertEqual(c0.start_refusal(clean, False, ARM, True, True), c0.LIVE_REFUSAL, v)

    def test_synthetic_dry_path_is_the_same_under_both_values(self):
        marked = self.root("marked", mark=True)
        for active in (True, False):
            with mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", active):
                self.assertIsNone(c0.start_refusal(marked, False, ARM), active)  # no record needed, as before
                self.assertIsNone(c0.start_refusal(marked, False), active)
                self.assertIsNotNone(c0.start_refusal(marked, True, ARM, True, True), active)  # a synthetic root never starts live
        os.symlink(os.path.join(self.sealed, "decisions", ARM), os.path.join(marked, "decisions", "hv4-BIG-X"))
        for active in (True, False):
            with mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", active):
                self.assertEqual(c0.start_refusal(marked, False, "hv4-BIG-X"), c0.LIVE_REFUSAL, active)  # unchanged label on the unchanged path


class SwitchFalsePureTests(Base):
    def setUp(self):
        super().setUp()
        self.off()

    def test_dry_start_on_a_non_synthetic_root_needs_the_real_pass_fact(self):
        root = self.root()
        self.assertEqual(c0.start_refusal(root, False, ARM), c0.START_NO_REAL_PASS)
        self.assertEqual(c0.start_refusal(root, False, ARM, False), c0.START_NO_REAL_PASS)
        for not_true in (1, "yes", [True], object()):
            self.assertEqual(c0.start_refusal(root, False, ARM, not_true), c0.START_NO_REAL_PASS, not_true)  # an explicit True only
        self.assertIsNone(c0.start_refusal(root, False, ARM, True))

    def test_the_fact_is_about_this_bot_so_a_missing_or_foreign_bot_id_refuses(self):
        root = self.root()
        for bad in (None, "", "bot-x", "hv4-../x", ".hv4-x", 5, "hv4-CON-X\n"):
            self.assertEqual(c0.start_refusal(root, False, bad, True), c0.START_NO_REAL_PASS, bad)

    def test_l_p_r_and_non_candidate_arms_refuse_even_with_a_pass_dry_or_live(self):
        root = self.root()
        for bot in ("hv4-CON-L", "hv4-BIG-L", "hv4-CON-P", "hv4-BIG-P", "hv4-CON-R", "hv4-BIG-R"):
            for live in (False, True):
                self.assertEqual(c0.start_refusal(root, live, bot, True, True), "arm_not_canary_candidate", (bot, live))
        self.assertEqual(c0.start_refusal(root, False, "hv4-CON-Q", True), "arm_not_f_or_x")
        for ok in ("hv4-CON-F", "hv4-CON-X", "hv4-BIG-F", "hv4-BIG-X"):
            self.assertIsNone(c0.start_refusal(root, False, ok, True), ok)

    def test_a_root_at_under_or_aliasing_the_arena_refuses_even_with_a_pass(self):
        os.makedirs(os.path.join(self.sealed, "sub"))
        link = os.path.join(self.tmp, "link-to-arena")
        os.symlink(self.sealed, link)
        dotted = os.path.join(self.tmp, "x", "..", "sealed-standin", "sub")
        os.makedirs(os.path.join(self.tmp, "x"))
        for root in (self.sealed, os.path.join(self.sealed, "sub"), os.path.join(self.sealed, "not-created-yet"), link, dotted, os.path.join(link, "sub")):
            for live in (False, True):
                self.assertEqual(c0.start_refusal(root, live, ARM, True, True), c0.START_SEALED_ROOT, (root, live))
        # a marked root inside the arena is refused too (the marker never opens the arena)
        Path(self.sealed, "sub", c0.SYNTHETIC_MARKER).write_text("")
        self.assertEqual(c0.start_refusal(os.path.join(self.sealed, "sub"), False, ARM, True, True), c0.START_SEALED_ROOT)

    def test_a_root_that_is_an_inode_alias_of_the_arena_or_sits_under_one_refuses(self):
        root = self.root()
        with self.alias_stat(root, self.sealed):  # a bind mount of the arena shows the arena's (st_dev, st_ino) under another name
            for live in (False, True):
                self.assertEqual(c0.start_refusal(root, live, ARM, True, True), c0.START_SEALED_ROOT, live)
        bound = os.path.join(root, "bound")
        os.makedirs(bound)
        with self.alias_stat(root, self.sealed):  # an ANCESTOR of the root is the arena's inode
            self.assertEqual(c0.start_refusal(bound, False, ARM, True), c0.START_SEALED_ROOT)

    def test_decisions_paths_under_the_arena_refuse_even_with_a_pass(self):
        # decisions/<bot_id> is a symlink into the arena
        root = self.root("r1", day=False)
        os.rmdir(os.path.join(root, "decisions", ARM))
        os.symlink(os.path.join(self.sealed, "decisions", ARM), os.path.join(root, "decisions", ARM))
        self.assertEqual(c0.start_refusal(root, False, ARM, True), c0.START_SEALED_ROOT)
        # one day file is a symlink into the arena
        root = self.root("r2", day=False)
        os.symlink(os.path.join(self.sealed, "decisions", ARM, DAY_FILE), os.path.join(root, "decisions", ARM, DAY_FILE))
        Path(root, "decisions", ARM, "2026-10-31.jsonl").write_text("")
        self.assertEqual(c0.start_refusal(root, False, ARM, True), c0.START_SEALED_ROOT)
        # the decisions dir of the bot is a symlink to a clean directory: the Bridge's guard_dir would latch sealed_path on every read, so the start refuses
        root = self.root("r3", day=False)
        clean = os.path.join(self.tmp, "clean-elsewhere")
        os.makedirs(clean)
        os.rmdir(os.path.join(root, "decisions", ARM))
        os.symlink(clean, os.path.join(root, "decisions", ARM))
        self.assertEqual(c0.start_refusal(root, False, ARM, True), c0.START_SEALED_ROOT)
        # `decisions` itself is a symlink
        root = os.path.join(self.tmp, "r4")
        os.makedirs(os.path.join(clean, ARM), exist_ok=True)
        os.makedirs(root)
        os.symlink(clean, os.path.join(root, "decisions"))
        self.assertEqual(c0.start_refusal(root, False, ARM, True), c0.START_SEALED_ROOT)
        # decisions/<bot_id> is a file, not a directory
        root = os.path.join(self.tmp, "r5")
        os.makedirs(os.path.join(root, "decisions"))
        Path(root, "decisions", ARM).write_text("")
        self.assertEqual(c0.start_refusal(root, False, ARM, True), c0.START_SEALED_ROOT)

    def test_decisions_dir_that_aliases_the_arena_by_inode_refuses(self):
        root = self.root("r6")
        d = os.path.join(root, "decisions", ARM)
        with self.alias_stat(d, os.path.join(self.sealed, "decisions", ARM)):  # our bot dir has the arena bot dir's inode
            self.assertEqual(c0.start_refusal(root, False, ARM, True), c0.START_SEALED_ROOT)
        with self.alias_stat(os.path.join(root, "decisions"), os.path.join(self.sealed, "decisions")):  # our decisions dir has the arena decisions dir's inode
            self.assertEqual(c0.start_refusal(root, False, ARM, True), c0.START_SEALED_ROOT)
        self.assertIsNone(c0.start_refusal(root, False, ARM, True))  # control: without the alias it starts

    def test_a_root_with_nothing_forwarded_yet_may_start(self):
        root = os.path.join(self.tmp, "fresh")
        os.makedirs(root)
        self.assertIsNone(c0.start_refusal(root, False, ARM, True))  # nothing to read yet: the Bridge guards every later read

    def test_live_needs_main_s_gates_on_top_of_the_pass(self):
        root = self.root()
        self.assertEqual(c0.start_refusal(root, True, ARM, True), c0.START_LIVE_GATES)
        self.assertEqual(c0.start_refusal(root, True, ARM, True, False), c0.START_LIVE_GATES)
        for not_true in (1, "yes", None):
            self.assertEqual(c0.start_refusal(root, True, ARM, True, not_true), c0.START_LIVE_GATES, not_true)
        self.assertEqual(c0.start_refusal(root, True, ARM, False, True), c0.START_NO_REAL_PASS)  # the gates do not stand in for the pass
        self.assertIsNone(c0.start_refusal(root, True, ARM, True, True))
        self.assertIsNone(c0.start_refusal(root, False, ARM, True, False))  # dry does not need them

    def test_a_marked_root_never_starts_live_and_the_pass_does_not_open_it(self):
        marked = self.root("marked", mark=True)
        self.assertEqual(c0.start_refusal(marked, True, ARM, True, True), c0.START_LIVE_ON_SYNTHETIC_ROOT)
        self.assertIsNone(c0.start_refusal(marked, False, ARM))  # the unchanged synthetic dry path


class RealPassRecordTests(Base):
    def setUp(self):
        super().setUp()
        self.canon = os.path.realpath(os.path.join(self.tmp, "canon"))
        p = mock.patch.object(c0, "READS_DIR_CANON", self.canon)
        p.start()
        self.addCleanup(p.stop)

    def _ident(self, d, bot=ARM):
        write_read_record(d, bot)
        why, ident = c0.read_record_problem(d, bot, synthetic_ok=True)
        self.assertIsNone(why)
        return why, ident

    def test_a_pass_read_from_the_canonical_dir_is_real(self):
        why, ident = self._ident(self.canon)
        self.assertTrue(c0.real_pass_record(why, ident, ARM))
        self.assertTrue(c0.real_pass_record(*c0.read_record_problem(self.canon, ARM), ARM))  # strict (no synthetic exception) gives the same fact

    def test_a_pass_that_came_through_the_synthetic_exception_is_not_real(self):
        other = os.path.realpath(os.path.join(self.tmp, "forged"))
        why, ident = self._ident(other)
        self.assertFalse(c0.real_pass_record(why, ident, ARM))
        self.assertEqual(c0.read_record_problem(other, ARM)[0], "read_record_dir_not_canonical")  # the strict gate refuses it outright

    def test_a_problem_or_a_foreign_or_malformed_identity_is_not_real(self):
        why, ident = self._ident(self.canon)
        self.assertFalse(c0.real_pass_record("read_record_not_pass", ident, ARM))
        self.assertFalse(c0.real_pass_record(None, ident, "hv4-BIG-X"))  # the record is for another bot
        self.assertFalse(c0.real_pass_record(None, {}, ARM))
        self.assertFalse(c0.real_pass_record(None, None, ARM))
        self.assertFalse(c0.real_pass_record(None, {"path": os.path.join(self.canon, "..", "canon", ARM + ".json")}, ARM))  # not the realpath form
        self.assertFalse(c0.real_pass_record(None, ident, None))
        self.assertFalse(c0.real_pass_record(None, ident, "bot-x"))
        self.assertFalse(c0.real_pass_record(None, ident, 5))


class MainBase(Base):
    def setUp(self):
        super().setUp()
        self.spy_calls = []

    def run_main(self, e, live=False, drop=(), over=None, spy=True):
        full = {k: v for k, v in e.raw.items() if k not in ("stop_file", "halt_file")}
        paths, st = live_main_env(self.tmp, full["bot_id"])
        if live:
            full.update(paths)
        full.update(over or {})
        for k in drop:
            full.pop(k, None)
        cp = Path(self.tmp) / "cfg_gate.json"
        cp.write_text(json.dumps({**full, "mode": "live" if live else "dry"}))
        real = c0.start_refusal

        def watch(*a, **k):
            self.spy_calls.append((a, k))
            return real(*a, **k)

        buf = io.StringIO()
        no_key = {k: v for k, v in os.environ.items() if k != "HELIUS_API_KEY"}
        with contextlib.redirect_stdout(buf), st, mock.patch.dict(os.environ, no_key, clear=True), mock.patch.object(c0, "start_refusal", watch if spy else real):
            rc = alb.main(["--config", str(cp), "--once"] + (["--live"] if live else []))
        return rc, buf.getvalue()

    def reads(self, e):
        return os.path.join(self.tmp, "reads")


class MainSwitchTests(MainBase):
    def test_dry_start_on_an_unmarked_root_with_a_canonical_pass_under_both_values(self):
        for active, rc_want in ((True, 2), (False, 0)):
            sub = tempfile.mkdtemp(dir=self.tmp)
            e = Hv4Env(sub)
            with mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", active):
                rc, out = self.run_main(e)
            self.assertEqual(rc, rc_want, (active, out))
            if active:
                self.assertIn(c0.LIVE_REFUSAL, out)
            else:
                self.assertNotIn("startup_refused", out)
        args, _ = self.spy_calls[-1]
        self.assertEqual(args[2:], (ARM, True, False))  # (bot_id, record_real_pass, live_gates_passed): a real pass, no live gates on a dry start

    def test_the_real_pass_fact_is_threaded_in_and_is_what_decides(self):
        self.off()
        e = Hv4Env(self.tmp)
        with mock.patch.object(c0, "real_pass_record", lambda *a: False):
            rc, out = self.run_main(e)
        self.assertEqual((rc, c0.START_NO_REAL_PASS in out), (2, True), out)
        self.assertEqual(self.spy_calls[-1][0][3], False)

    def test_a_dry_start_on_a_marked_root_with_a_synthetic_record_is_unchanged_and_carries_no_real_pass(self):
        self.off()
        forged = os.path.realpath(os.path.join(self.tmp, "forge"))
        e = Hv4Env(self.tmp, reads_dir=forged)
        write_read_record(forged, ARM)
        c0.READS_DIR_CANON = os.path.join(self.tmp, "canon-elsewhere")
        Path(e.raw["root"]).mkdir(parents=True, exist_ok=True)
        Path(e.raw["root"], c0.SYNTHETIC_MARKER).write_text("")
        rc, out = self.run_main(e)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.spy_calls[-1][0][2:], (ARM, False, False))  # synthetic_ok let the record through; it is not a real pass
        self.spy_calls.clear()
        os.unlink(os.path.join(e.raw["root"], c0.SYNTHETIC_MARKER))  # the same config on an unmarked root: the forged dir is refused before the start gate
        rc, out = self.run_main(e)
        self.assertEqual(rc, 2)
        self.assertIn("read_record_dir_not_canonical", out)
        self.assertEqual(self.spy_calls, [])

    def test_the_synthetic_dry_start_still_works_with_the_switch_either_way(self):
        for active in (True, False):
            sub = tempfile.mkdtemp(dir=self.tmp)
            e = Hv4Env(sub)
            Path(e.raw["root"]).mkdir(parents=True, exist_ok=True)
            Path(e.raw["root"], c0.SYNTHETIC_MARKER).write_text("")
            with mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", active):
                rc, out = self.run_main(e)
            self.assertEqual(rc, 0, (active, out))


class MainRecordGateTests(MainBase):
    """With the switch False, a start on a non-synthetic root is refused by the recorded-read gate BEFORE the start gate, before any bridge object exists and
    before any decision-log path is listed or read (R2.1)."""

    def setUp(self):
        super().setUp()
        self.off()

    def _refused(self, e, want, live=False):
        with mock.patch.object(c0, "guarded_read", side_effect=AssertionError("a decision row was read")), \
                mock.patch.object(c0, "guarded_listdir", side_effect=AssertionError("a decisions dir was listed")), \
                mock.patch.object(alb, "Bridge", side_effect=AssertionError("a bridge was built")):
            rc, out = self.run_main(e, live=live)
        self.assertEqual(rc, 2, out)
        self.assertIn(want, out)
        self.assertEqual(self.spy_calls, [], out)  # refused before the start gate was reached
        self.assertFalse((Path(e.raw["state_dir"]) / "bridge-log-dry.jsonl").exists())

    def test_no_record_refuses_a_dry_start_on_a_real_root(self):
        e = Hv4Env(self.tmp)
        os.unlink(os.path.join(self.reads(e), ARM + ".json"))
        self._refused(e, "read_record_missing")

    def test_fail_not_decidable_forced_and_pending_records_refuse(self):
        cases = {"FAIL": dict(status="FAIL"), "FAIL labelled": dict(status="PASS", fail_label="FAIL (n < 100)"), "NOT_DECIDABLE": dict(status="NOT_DECIDABLE"),
                 "REPORT_ONLY": dict(status="REPORT_ONLY"), "PENDING": dict(status="PENDING"), "forced": dict(boundary_source="forced"),
                 "forced prefix": dict(status="FORCED_PASS"), "pending": dict(pending=2), "no am15": dict(am15=None)}
        e = Hv4Env(self.tmp)
        for name, over in cases.items():
            write_read_record(self.reads(e), ARM, **over)
            with self.subTest(name):
                self._refused(e, "read_record_not_pass")

    def test_wrong_frozen_sha_other_bot_and_junk_records_refuse(self):
        e = Hv4Env(self.tmp)
        write_read_record(self.reads(e), ARM, frozen_sha="deadbeef")
        self._refused(e, "read_record_frozen_sha")
        write_read_record(self.reads(e), ARM, bot_id="hv4-BIG-X")
        self._refused(e, "read_record_other_bot")
        Path(self.reads(e), ARM + ".json").write_text("not json")
        self._refused(e, "read_record_unreadable")

    def test_a_pass_for_another_arm_does_not_open_this_one(self):
        e = Hv4Env(self.tmp)
        os.unlink(os.path.join(self.reads(e), ARM + ".json"))
        write_read_record(self.reads(e), "hv4-BIG-X")
        self._refused(e, "read_record_missing")

    def test_a_record_in_a_forged_non_canonical_dir_refuses_on_a_real_root(self):
        forged = os.path.realpath(os.path.join(self.tmp, "forge"))
        e = Hv4Env(self.tmp, reads_dir=forged)
        write_read_record(forged, ARM)
        c0.READS_DIR_CANON = os.path.join(self.tmp, "canon-elsewhere")
        self._refused(e, "read_record_dir_not_canonical")

    def test_a_record_dir_under_the_arena_standin_other_than_the_canonical_one_refuses(self):
        sealed_reads = os.path.join(self.sealed, "reads-copy")
        e = Hv4Env(self.tmp, reads_dir=sealed_reads)
        write_read_record(sealed_reads, ARM)
        self._refused(e, "read_record_dir_sealed")

    def test_l_p_r_arms_refuse_through_main_even_with_a_canonical_pass(self):
        for suffix in ("L", "P", "R"):
            sub = tempfile.mkdtemp(dir=self.tmp)
            bot = f"hv4-CON-{suffix}"
            e = Hv4Env(sub, bot=bot)
            write_read_record(os.path.join(sub, "reads"), bot)
            with self.subTest(bot):
                rc, out = self.run_main(e)
                self.assertEqual(rc, 2, out)
                self.assertIn("arm_not_canary_candidate", out)
                self.assertEqual(self.spy_calls, [])


class MainSealedPathTests(MainBase):
    def setUp(self):
        super().setUp()
        self.off()

    def test_a_root_under_the_arena_standin_refuses_dry_and_live_even_with_a_canonical_pass(self):
        for live in (False, True):
            e = Hv4Env(tempfile.mkdtemp(dir=self.tmp))
            root = os.path.join(self.sealed, "forwarded")
            rc, out = self.run_main(e, live=live, over={"root": root})
            self.assertEqual(rc, 2, (live, out))
            self.assertIn(c0.START_SEALED_ROOT, out)
        self.assertFalse(os.path.exists(os.path.join(self.sealed, "forwarded")))  # refused without creating or touching anything in it
        self.assertEqual(sorted(os.listdir(os.path.join(self.sealed, "decisions", ARM))), [DAY_FILE])

    def test_a_decisions_symlink_into_the_arena_refuses_with_a_canonical_pass(self):
        e = Hv4Env(self.tmp)
        root = Path(e.raw["root"])
        (root / "decisions").mkdir(parents=True)
        os.symlink(os.path.join(self.sealed, "decisions", ARM), root / "decisions" / ARM)
        rc, out = self.run_main(e)
        self.assertEqual(rc, 2, out)
        self.assertIn(c0.START_SEALED_ROOT, out)
        os.unlink(root / "decisions" / ARM)  # control: the same config without the link starts
        rc, out = self.run_main(e)
        self.assertEqual(rc, 0, out)


class MainLiveGateTests(MainBase):
    def test_live_gates_precede_the_start_gate_under_both_values(self):
        for active in (True, False):
            for drop, want in ((("parity_dir", "twins_file", "tieout_dir"), "parity_path_missing:parity_dir"), (("a3_record_file",), "a3_record_file_missing")):
                sub = tempfile.mkdtemp(dir=self.tmp)
                e = Hv4Env(sub)
                with mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", active):
                    rc, out = self.run_main(e, live=True, drop=drop)
                self.assertEqual(rc, 2, (active, drop, out))
                self.assertIn(want, out)
                self.assertNotIn(c0.LIVE_REFUSAL, out)
                self.assertEqual(self.spy_calls, [])

    def test_true_a_live_start_with_every_gate_and_a_pass_still_refuses_hv4_c0_incomplete(self):
        e = Hv4Env(self.tmp)
        with mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", True):
            rc, out = self.run_main(e, live=True)
        self.assertEqual(rc, 2)
        self.assertIn(c0.LIVE_REFUSAL, out)
        self.assertEqual(self.spy_calls[-1][0][2:], (ARM, True, True))

    def test_false_a_live_start_with_every_gate_and_a_pass_passes_the_start_gate_and_stops_at_the_missing_rpc_key(self):
        self.off()
        e = Hv4Env(self.tmp)
        rc, out = self.run_main(e, live=True)
        self.assertEqual(rc, 2)
        self.assertIn("rpc_key_missing", out)  # the first check after the start gate; no key is read, no order is sent
        self.assertNotIn(c0.LIVE_REFUSAL, out)
        self.assertEqual(self.spy_calls[-1][0][2:], (ARM, True, True))

    def test_false_live_without_a_pass_refuses_at_the_record_gate(self):
        self.off()
        e = Hv4Env(self.tmp)
        os.unlink(os.path.join(self.reads(e), ARM + ".json"))
        rc, out = self.run_main(e, live=True)
        self.assertEqual(rc, 2)
        self.assertIn("read_record_missing", out)
        self.assertEqual(self.spy_calls, [])

    def test_false_live_on_a_marked_root_refuses(self):
        self.off()
        e = Hv4Env(self.tmp)
        Path(e.raw["root"]).mkdir(parents=True, exist_ok=True)
        Path(e.raw["root"], c0.SYNTHETIC_MARKER).write_text("")
        rc, out = self.run_main(e, live=True)
        self.assertEqual(rc, 2)
        self.assertIn(c0.START_LIVE_ON_SYNTHETIC_ROOT, out)


class PerTickRecheckTests(MainBase):
    """The Bridge repeats the read-record gate on every read, on a non-synthetic root too (strict canonical dir): a record rewritten after the start latches."""

    def test_a_rewritten_record_latches_read_record_and_no_further_row_is_read(self):
        e = Hv4Env(self.tmp)
        e.start()
        e.entry_sz("o1", pool="P1")
        b = e.bridge()
        self.assertFalse(c0.synthetic_root(e.raw["root"]))  # a real (unmarked) root: no synthetic exception applies
        b.tick()
        self.assertEqual(len([c for c in e.port.calls if c[0] == "buy"]), 1)
        self.assertNotIn("read_record", b.state.latches)
        write_read_record(self.reads(e), ARM, status="FAIL")  # the record changes under the running bridge
        e.entry_sz("o2", pool="P2")
        with mock.patch.object(c0, "guarded_read", side_effect=AssertionError("a row was read after the latch")):
            b.tick()
        self.assertIn("read_record", b.state.latches)
        self.assertEqual(len([c for c in e.port.calls if c[0] == "buy"]), 1)


class PerBotLiveOkTests(TmpCase):
    """hv4_c0 only: LIVE_OK and the owner confirmation are per bot, so two canaries (hv4-BIG-X and hv4-CON-X) can be confirmed independently."""

    def _bridge(self, e, **kw):
        raw = {k: v for k, v in e.raw.items() if k not in ("stop_file", "halt_file")}
        cfg = alb.Config.from_dict({**raw, "mode": "live"})
        cfg = alb.Config(**{**cfg.__dict__, "stop_file": e.raw["stop_file"], "halt_file": e.raw["halt_file"]})  # tests only: the pinned paths cannot be set via config
        return alb.Bridge(cfg, e.port, now_ms=e.clock, tier_path=e.tier, root_checks=False, **kw)

    def test_paths_are_per_bot_and_validated(self):
        self.assertEqual(c0.live_ok_path("hv4-CON-X"), "/etc/mal-arena-live/hv4-c0/hv4-CON-X/LIVE_OK")
        self.assertEqual(c0.live_ok_path("hv4-BIG-X"), "/etc/mal-arena-live/hv4-c0/hv4-BIG-X/LIVE_OK")
        for bad in (None, "", "../x", "a/b", ".h", 5):
            with self.assertRaises(ValueError):
                c0.live_ok_path(bad)

    def test_the_hv4_c0_default_is_the_per_bot_file_and_other_profiles_keep_the_shared_one(self):
        e = Hv4Env(self.tmp, live=True)
        self.assertEqual(self._bridge(e).live_ok_path, Path("/etc/mal-arena-live/hv4-c0/hv4-CON-X/LIVE_OK"))
        from tools.test_arena_live_bridge import Env
        plain = Env(tempfile.mkdtemp(dir=self.tmp))
        b = alb.Bridge(alb.Config.from_dict(plain.raw), plain.port, now_ms=plain.clock, tier_path=plain.tier, root_checks=False)
        self.assertEqual(b.live_ok_path, alb.LIVE_OK_PATH)  # /etc/mal-arena-live/LIVE_OK, unchanged
        explicit = self._bridge(e, live_ok_path=e.live_ok)
        self.assertEqual(explicit.live_ok_path, e.live_ok)  # an explicit path still wins

    def test_two_bots_are_confirmed_independently(self):
        etc = Path(self.tmp) / "etc-live"
        dec = Path(self.tmp) / "etc-c0"
        envs = {}
        for bot in ("hv4-CON-X", "hv4-BIG-X"):
            sub = tempfile.mkdtemp(dir=self.tmp)
            envs[bot] = Hv4Env(sub, live=True, bot=bot)
        with mock.patch.object(c0, "LIVE_OK_DIR", str(etc)), mock.patch.object(c0, "OWNER_DEC_DIR", str(dec)):
            bridges = {bot: self._bridge(e) for bot, e in envs.items()}
            for b in bridges.values():
                b.tier_path.write_text("T1\n")
            self.assertNotEqual(bridges["hv4-CON-X"].live_ok_path, bridges["hv4-BIG-X"].live_ok_path)
            self.assertEqual(bridges["hv4-CON-X"].owner_dec_path, dec / "hv4-CON-X" / "CANARY_DEC.md")
            self.assertEqual(bridges["hv4-BIG-X"].owner_dec_path, dec / "hv4-BIG-X" / "CANARY_DEC.md")
            # nothing installed: both stay dry
            self.assertEqual(bridges["hv4-CON-X"].effective_live(), (False, "live_ok_missing"))
            self.assertEqual(bridges["hv4-BIG-X"].effective_live(), (False, "live_ok_missing"))
            # CON-X is confirmed (LIVE_OK naming it + its own owner file): live; BIG-X is not
            (etc / "hv4-CON-X").mkdir(parents=True)
            (etc / "hv4-CON-X" / "LIVE_OK").write_text("hv4-CON-X\n")
            owner_fixture(self.tmp, "hv4-CON-X")
            self.assertEqual(bridges["hv4-CON-X"].effective_live(), (True, None))
            self.assertEqual(bridges["hv4-BIG-X"].effective_live(), (False, "live_ok_missing"))
            # BIG-X's LIVE_OK must name BIG-X; CON-X's name in it is another book
            (etc / "hv4-BIG-X").mkdir()
            (etc / "hv4-BIG-X" / "LIVE_OK").write_text("hv4-CON-X\n")
            self.assertEqual(bridges["hv4-BIG-X"].effective_live(), (False, "live_ok_other_book"))
            (etc / "hv4-BIG-X" / "LIVE_OK").write_text("hv4-BIG-X\n")
            self.assertEqual(bridges["hv4-BIG-X"].effective_live(), (False, "live_ok_owner_dec_missing"))  # LIVE_OK alone is not enough
            owner_fixture(self.tmp, "hv4-BIG-X")
            self.assertEqual(bridges["hv4-BIG-X"].effective_live(), (True, None))
            # CON-X's owner file does not confirm BIG-X (its line names CON-X), and removing it unconfirms only CON-X
            (dec / "hv4-BIG-X" / "CANARY_DEC.md").write_text(c0.OWNER_LINE_FMT.format(bot_id="hv4-CON-X") + "\n")
            self.assertEqual(bridges["hv4-BIG-X"].effective_live(), (False, "live_ok_owner_line_missing"))
            self.assertEqual(bridges["hv4-CON-X"].effective_live(), (True, None))


if __name__ == "__main__":
    unittest.main()
