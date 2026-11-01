"""Tests for tools/h5_shadow_tip.py (the tip input mode of the S* shadow engine, L1 design G6) and tools/h5_shadow_tip_equiv.py (the md5 equivalence harness).
SYNTHETIC rows and tmp dirs only: no real tip file, receiver dir, arena tape, ledger or network is opened.

  /data/mal/venv/bin/python -m pytest tools/test_h5_shadow_tip.py -q
"""
from __future__ import annotations

import ast
import asyncio
import collections
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_tip_push as atp
from tools import h5_shadow as h5
from tools import h5_shadow_tip as tip
from tools import h5_shadow_tip_equiv as eq
from tools.test_h5_shadow import MINT, POOL, SPS, V0, Tape, make_engine
from tools.test_h5_shadow_fresh_forward import pool_tape

S_STAR = "5c2ab7c6"
SRC = Path(h5.__file__).resolve()
REPO = SRC.parent.parent
FEES = ("lp_fee", "pool_quote_amount")


def frozen_source() -> str:
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{S_STAR}:tools/h5_shadow.py"], capture_output=True, text=True)
    if r.returncode != 0:  # fail, never skip: a clone without the frozen object proves nothing
        raise AssertionError(f"object {S_STAR} is not in this clone: git fetch it")
    return r.stdout


def tip_rows_for(tape: Tape, *, announce: bool = True) -> list[dict]:
    """The arena-tape rows of one synthetic pool, through the REAL adapter row functions."""
    meta = {tape.pool: {"s0": tape.slot0, "v": V0, "mint": "Mint" + tape.pool}}
    rows = eq.prepare_rows(tape.rows)
    out = eq.to_tip_rows(rows, meta)
    return out if announce else [r for r in out if r["type"] != "pool_create"]


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="h5tip-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.dir = self.root / "hv7"
        self.dir.mkdir()
        self.roots = (str(self.root),)

    def source(self, problems=None, **kw) -> tip.TipRowSource:
        seq = list(problems or [None])
        fn = lambda: seq.pop(0) if len(seq) > 1 else seq[0]  # noqa: E731
        kw.setdefault("cache_ms", 0)
        return tip.TipRowSource(self.dir, start_ms=0, roots=self.roots, problem_fn=fn, **kw)


class IdentityToSStar(unittest.TestCase):
    def test_h5_shadow_is_byte_identical_to_s_star(self):
        self.assertEqual(SRC.read_text(), frozen_source())  # S*' adds NO byte to tools/h5_shadow.py: the public mode, the engine and every decision function are S*'s own

    def test_importing_the_tip_module_changes_nothing_in_h5_shadow(self):
        before = {k: v for k, v in vars(h5).items() if callable(v)}
        import importlib

        importlib.reload(tip)
        self.assertTrue(all(vars(h5)[k] is v for k, v in before.items()))
        self.assertNotIn("tip_input", h5.build_parser().parse_args([]).__dict__)  # the default parser has no tip flag
        self.assertNotIn("--tip-input", h5.build_parser().format_help())

    def test_the_tip_module_defines_no_decision_function(self):
        names = {n.name for n in ast.parse((REPO / "tools" / "h5_shadow_tip.py").read_text()).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        for banned in ("post_state", "tier_fee", "fill_round_trip", "decode_notice", "Engine", "Pr", "Pool", "SlotClock", "SynClassifier"):
            self.assertNotIn(banned, names)


class DirChecks(TmpCase):
    def test_refusals(self):
        for bad in ("/data/mal/arena/tape", "/data/mal/arena/tape/x", "/var/lib/mal/sealed/fast-trades-tip", "/data2/mal/tip-tape-archive/x", "relative/dir", "",
                    "/data2/mal/arena-tip-feed", "/data2/mal/arena-tip-feed/hv7/tip-tape-archive", "/data2/mal/arena-tip-feed/../x", "/tmp/not-allowlisted/hv7",
                    "/data2/mal/arena-tip-feed/forward-walk"):
            with self.assertRaises(ValueError, msg=bad):
                tip.check_tip_input_dir(bad)

    def test_a_child_of_an_allowed_root_passes_and_the_root_itself_does_not(self):
        self.assertEqual(tip.check_tip_input_dir(self.dir, self.roots), self.dir)
        with self.assertRaises(ValueError):
            tip.check_tip_input_dir(self.root, self.roots)
        with self.assertRaises(ValueError):
            tip.check_tip_input_dir(self.root / "missing", self.roots)  # not a dir
        with self.assertRaises(ValueError):
            tip.check_tip_input_dir(self.dir)  # tmp is not a production root

    def test_a_symlink_out_of_the_root_is_judged_by_realpath(self):
        link = self.root / "link"
        link.symlink_to("/data/mal")
        with self.assertRaises(ValueError):
            tip.check_tip_input_dir(link, self.roots)


class FailClosed(TmpCase):
    def test_start_refuses_on_any_problem_and_poll_before_start_raises(self):
        for why in ("decoder_pin", "status_stale", "not_caught_up", "writer_relay_missing"):
            s = self.source([why])
            with self.assertRaises(tip.TipInputUnhealthy) as cm:
                s.start()
            self.assertEqual(str(cm.exception), why)
        with self.assertRaises(tip.TipInputUnhealthy):
            self.source().poll()

    def test_the_real_push_problem_on_an_empty_dir_refuses(self):
        s = tip.TipRowSource(self.dir, start_ms=0, roots=self.roots)
        with self.assertRaises(tip.TipInputUnhealthy) as cm:
            s.start()
        self.assertEqual(str(cm.exception), "status_missing")

    def test_a_raising_status_check_is_a_problem_not_a_crash(self):
        def boom():
            raise RuntimeError("x")

        s = tip.TipRowSource(self.dir, start_ms=0, roots=self.roots, problem_fn=boom, cache_ms=0)
        with self.assertRaises(tip.TipInputUnhealthy):
            s.start()

    def test_permanent_problems(self):
        for why in ("decoder_pin", "state_stopped_pin_mismatch", "state_stopped_mismatch", "pin_mismatch"):
            self.assertTrue(tip.is_permanent(why), why)
        for why in ("status_stale", "not_caught_up", "state_catching_up", "writer_relay_stale", None):
            self.assertFalse(tip.is_permanent(why), why)

    def test_an_unhealthy_status_gives_no_rows_and_a_pin_problem_halts_for_good(self):
        eq.write_tape(self.dir, tip_rows_for(pool_tape("PX", 1000, 1_800_000_000, filler=True)))
        s = self.source([None, "status_stale", None, "decoder_pin", None])
        s.start()
        self.assertEqual(s.poll(), [])  # stale: nothing, not halted
        self.assertEqual(s.problem, "status_stale")
        self.assertFalse(s.halted)
        self.assertGreater(len(s.poll(5)), 0)  # healthy again: rows resume from the file
        self.assertEqual(s.poll(), [])
        self.assertTrue(s.halted)
        self.assertEqual(s.poll(), [])  # halted: nothing even if the check would pass again
        self.assertEqual(s.problem, "decoder_pin")

    def test_a_reader_exception_is_a_problem(self):
        s = self.source()
        s.start()
        with mock.patch.object(tip._RowTap, "poll", side_effect=OSError("disk")):
            self.assertEqual(s.poll(), [])
        self.assertEqual(s.problem, "reader_oserror")

    def test_the_driver_flags_open_pools_on_onset_and_records_the_resume(self):
        t = pool_tape("PD", 1000, 1_800_000_000, filler=True)
        eng, out = make_engine(fresh_start_ms=None)
        rows = tip_rows_for(t)
        eq.write_tape(self.dir, rows)
        s = self.source([None, None, "status_stale", None])
        s.start()
        drv = tip.TipDriver(eng, s)
        drv.step(8)
        self.assertTrue(eng.pools)  # the pool is open
        self.assertEqual(drv.step(8), 0)  # check 3 = stale
        self.assertTrue(any(r["type"] == "tip_input_problem" and r["problem"] == "status_stale" and not r["permanent"] for r in out))
        gaps = [r for r in out if r["type"] == "gap" and r["kind"] == "feed_restart"]
        self.assertEqual(len(gaps), 1)
        self.assertTrue(gaps[0]["flags_pools"])
        self.assertTrue(all(any(g["kind"] == "feed_restart" for g in p.gaps) for p in eng.pools.values()))
        self.assertGreater(drv.step(8), 0)
        self.assertTrue(any(r["type"] == "tip_input_resumed" for r in out))


class Mapping(unittest.TestCase):
    def setUp(self):
        self.eng, self.out = make_engine(fresh_start_ms=None)
        self.c = collections.Counter()

    def apply(self, row):
        tip.apply_row(self.eng, row, self.c)

    def test_pool_create_announces_requests_the_class_and_moves_the_clock(self):
        calls = []
        self.eng.syn_request = lambda mint, sig=None: calls.append((mint, sig))
        self.eng.classifier = mock.Mock(lookup=lambda m, s0=None: (None, None))
        self.apply({"type": "pool_create", "prog": "pumpswap", "slot": 998, "t_recv_ms": 5_000, "pool": POOL, "mint": MINT, "quote_mint": h5.WSOL_MINT, "signature": "mig1"})
        self.assertEqual(self.eng.announced[POOL][:3], (998, 5_000, MINT))
        self.assertEqual(calls, [(MINT, "mig1")])
        self.assertEqual(self.eng.hw_slot, 998)
        self.assertEqual(self.eng.counters["create_pool_events"], 1)

    def test_bonding_rows_do_not_touch_the_engine(self):
        for row in ({"type": "trade", "prog": "pumpfun", "slot": 5, "t_recv_ms": 1, "mint": "m"}, {"type": "create", "prog": "pumpfun", "slot": 5, "t_recv_ms": 1},
                    {"type": "hb", "prog": "all", "slot": 5, "t_recv_ms": 1}):
            self.apply(row)
        self.assertIsNone(self.eng.hw_slot)
        self.assertEqual(self.eng.counters["events"], 0)
        self.assertEqual((self.c["rows_ignored_bonding"], self.c["rows_ignored_create"], self.c["rows_ignored_hb"]), (1, 1, 1))

    def test_complete_row_hints_the_classifier_signature(self):
        clf = h5.SynClassifier()
        self.eng.classifier = clf
        self.apply({"type": "complete", "prog": "pumpfun", "slot": 5, "t_recv_ms": 1, "mint": MINT, "signature": "compsig"})
        self.assertEqual(clf.sig_for(MINT, "comp"), "compsig")
        self.assertEqual(clf.lookup(MINT), (None, None))  # a hint is not a verdict
        self.assertEqual(self.eng.counters["events"], 0)

    def test_gap_rows_are_mapped_as_the_adapter_makes_them(self):
        t = pool_tape("PG", 1000, 1_800_000_000, filler=True)
        self.eng.on_create_pool("PG", "MintPG", h5.WSOL_MINT, 999, 0)
        for r in t.rows[:3]:
            self.eng.on_trade(r)
        self.assertIn("PG", self.eng.pools)
        base = {"type": "gap", "slot": 1002, "t_recv_ms": 9_000, "from_ms": 8_000, "to_ms": 9_000, "from_slot": 1001, "to_slot": 1010, "missed_slots": 10, "src": "tip"}
        self.apply({**base, "prog": "pumpfun", "kind": "tip_gap_bad_row_fields", "data_loss": True, "reason": "bad_row_fields"})
        self.assertEqual(self.eng.pools["PG"].gaps, [])  # a bad bonding row cannot have cost a PumpSwap print
        self.apply({**base, "prog": "pumpswap", "kind": "tip_gap_unresolved_dropped", "data_loss": True, "reason": "unresolved_dropped"})
        self.apply({**base, "prog": "all", "kind": "tip_gap_backlog_jump", "data_loss": True, "reason": "backlog_jump"})
        self.apply({**base, "prog": "all", "kind": "tip_gap_unfetchable"})  # no data_loss key: loss (fail closed)
        self.apply({**base, "prog": "all", "kind": "tip_gap_note", "data_loss": False})
        kinds = [g["kind"] for g in self.eng.pools["PG"].gaps]
        self.assertEqual(kinds, ["tip_gap_unresolved_dropped", "tip_gap_backlog_jump", "tip_gap_unfetchable"])
        recs = [r for r in self.out if r["type"] == "gap"]
        self.assertEqual([r["flags_pools"] for r in recs], [False, True, True, True, False])
        self.assertEqual(self.eng.last_flag_gap_ms, 9_000)  # at_ms = to_ms
        self.assertEqual(recs[1]["from_slot"], 1001)
        self.assertEqual(self.c["gap_rows"], 5)

    def test_a_bad_pumpswap_row_is_marked_not_applied(self):
        self.apply({"type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 7, "t_recv_ms": 3, "pool": POOL, "side": "buy"})  # no amounts
        self.apply({"type": "trade", "prog": "pumpswap", "venue": "pump_bonding", "slot": 7, "t_recv_ms": 3, "pool": POOL, "side": "buy", "signature": "s", "sol_lamports": 1,
                    "token_raw": 1, "quote_reserve": 1, "base_reserve": 1})
        self.apply({"type": "pool_create", "prog": "pumpswap", "slot": 7, "t_recv_ms": 3, "pool": None, "mint": MINT})
        self.assertEqual(self.c["rows_bad"], 3)
        self.assertEqual(self.eng.counters["pumpswap_prints"], 0)
        self.assertEqual(self.eng.counters["create_pool_events"], 0)
        recs = [r for r in self.out if r["type"] == "gap"]
        self.assertEqual([(r["kind"], r["flags_pools"]) for r in recs], [("tip_input_bad_row", True)] * 3)

    def test_a_trade_without_the_venue_key_gets_it(self):
        t = Tape(slot0=1000)
        r = t.row(1000, "buy", "A", 10**8)
        r.pop("venue")
        r.update({"prog": "pumpswap", "type": "trade"})
        self.eng.on_create_pool(POOL, MINT, h5.WSOL_MINT, 999, 0)
        self.apply(r)
        self.assertEqual(self.eng.counters["pumpswap_prints"], 1)


class Stamp(TmpCase):
    def run_pool(self, offset):
        t = pool_tape("PS", 1000, 1_800_000_000, filler=True)
        eng, out = make_engine(bx10_enabled=True)  # wall: the fixed fake host clock of make_engine (1_800_000_000_000), unrelated to the rows
        eq.write_tape(self.dir, tip_rows_for(t))
        s = self.source()
        s.start()
        drv = tip.TipDriver(eng, s, wall_offset_ms=offset)
        while drv.step(50):
            pass
        eng.close_all("end")
        return [r for r in out if r["type"] == "trigger"], eng

    def test_live_t_detect_is_the_host_wall_never_the_row_stamp(self):
        trig, eng = self.run_pool(None)
        self.assertTrue(trig)
        for r in trig:
            self.assertEqual(r["t_detect_ms"], 1_800_000_000_000)  # the engine's wall()
            self.assertNotEqual(r["t_detect_ms"], r["t_recv_ms"])
            self.assertEqual(r["detect_lag_ms"], r["t_detect_ms"] - r["t_recv_ms"])

    def test_replay_mode_uses_the_fixed_offset(self):
        trig, _ = self.run_pool(2500)
        self.assertTrue(trig)
        for r in trig:
            self.assertEqual(r["t_detect_ms"], r["t_recv_ms"] + 2500)
            self.assertEqual(r["detect_lag_ms"], 2500)

    def test_run_feed_replacement_records_the_stamp_mode_and_applies_rows(self):
        t = pool_tape("PF", 1000, 1_800_000_000, filler=True)
        eq.write_tape(self.dir, tip_rows_for(t))
        eng, out = make_engine(bx10_enabled=True)
        s = self.source()
        s.start()
        state: dict = {}
        stop = asyncio.Event()
        asyncio.run(asyncio.wait_for(tip.make_run_feed(s, state, poll_s=0.01)(None, eng, stop, max_seconds=0.3), 5))
        st = [r for r in out if r["type"] == "tip_input_start"]
        self.assertEqual(len(st), 1)
        self.assertEqual((st[0]["stamp"], st[0]["public_trade_feed"], st[0]["wall_offset_ms"]), ("host_wall", "off", None))
        self.assertEqual(eng.counters["pools_tracked"], 1)
        self.assertTrue(stop.is_set())
        self.assertNotIn("halted", state)


class Cli(TmpCase):
    def test_parser_extends_the_public_parser_by_one_flag(self):
        pub, tipp = h5.build_parser(), tip.build_parser()
        names = lambda ap: {a.dest for a in ap._actions}  # noqa: E731
        self.assertEqual(names(tipp) - names(pub), {"tip_input", "smoke"})
        self.assertEqual(names(pub) - names(tipp), set())

    def test_refusals(self):
        with self.assertRaises(SystemExit):
            tip.main(["--tip-input", str(self.dir)])  # no --fresh-forward ARENA
        with self.assertRaises(SystemExit):
            tip.main(["--tip-input", str(self.dir), "--fresh-forward", "arena"])
        with self.assertRaises(SystemExit):
            tip.main(["--tip-input", str(self.dir), "--fresh-forward", "ARENA", "--tip-dir", "/x"])
        with self.assertRaises(SystemExit):
            tip.main(["--tip-input", str(self.dir), "--fresh-forward", "ARENA", "--replay-tape", "/x", "--replay-hours", "h"])
        with self.assertRaises(SystemExit):
            tip.main(["--tip-input", str(self.dir), "--fresh-forward", "ARENA", "--h5-look2-observed", h5.H5_LOOK2_AMENDMENT_REF])

    def test_a_dir_outside_the_roots_or_an_unhealthy_status_exits_2_before_run_live(self):
        with mock.patch.object(h5, "run_live", side_effect=AssertionError("must not start")):
            self.assertEqual(tip.main(["--tip-input", str(self.dir), "--fresh-forward", "ARENA"]), tip.EXIT_REFUSED)  # not allowlisted
            with mock.patch.object(atp, "DST_ROOTS", self.roots):
                self.assertEqual(tip.main(["--tip-input", str(self.dir), "--fresh-forward", "ARENA"]), tip.EXIT_REFUSED)  # status_missing

    def test_run_feed_is_swapped_only_for_run_live_and_restored(self):
        orig = h5.run_feed
        seen = {}

        async def fake_run_live(args):
            seen["swapped"] = h5.run_feed is not orig
            return 0

        with mock.patch.object(atp, "DST_ROOTS", self.roots), mock.patch.object(atp, "push_problem", return_value=None), mock.patch.object(h5, "run_live", fake_run_live):
            self.assertEqual(tip.main(["--tip-input", str(self.dir), "--fresh-forward", "ARENA"]), 0)
        self.assertTrue(seen["swapped"])
        self.assertIs(h5.run_feed, orig)

    def test_a_pin_problem_while_running_stops_the_book_with_exit_3(self):
        eq.write_tape(self.dir, tip_rows_for(pool_tape("PH", 1000, 1_800_000_000, filler=True)))
        calls = []

        def push_problem(dst, *a, **k):
            calls.append(1)
            return None if len(calls) == 1 else "decoder_pin"

        async def fake_run_live(args):
            eng, out = make_engine()
            await h5.run_feed(None, eng, asyncio.Event(), max_seconds=5)
            self.assertTrue(any(r["type"] == "tip_input_halted" for r in out))
            self.assertTrue(any(r["type"] == "tip_input_problem" and r["permanent"] for r in out))
            return 0

        with mock.patch.object(atp, "DST_ROOTS", self.roots), mock.patch.object(atp, "push_problem", push_problem), mock.patch.object(tip, "PROBLEM_CACHE_MS", 0), \
                mock.patch.object(h5, "run_live", fake_run_live):
            self.assertEqual(tip.main(["--tip-input", str(self.dir), "--fresh-forward", "ARENA"]), tip.EXIT_HALTED)


class Equivalence(TmpCase):
    def make_engine(self, emit):
        return h5.Engine(emit, sps_fn=lambda p: SPS, pda_fn=lambda pool: "PDA_" + pool, classifier=h5.StaticClassifier(False, "rpc"), seal_start_ms=None,
                         h5_look2_start_ms=None, bx10_enabled=True)

    def rows_meta(self, *tapes):
        rows, meta = [], {}
        for t in tapes:
            meta[t.pool] = {"s0": t.slot0, "v": V0, "mint": "Mint" + t.pool}
            rows += t.rows
        return sorted(rows, key=lambda r: r["slot"]), meta

    def two_pools(self):
        """PA at 08:00:00Z; PB's first print is 43 slots (> GAP_SLOTS = 30) after PA's last row and 61 minutes later: the rows cross an hour mark (an hour closes and is compacted)."""
        return self.rows_meta(pool_tape("PA", 1000, 1_800_000_000, filler=True), pool_tape("PB", 1950, 1_800_003_700, filler=True))

    def test_both_arms_decide_the_same_as_the_public_path_on_the_same_rows(self):
        rows, meta = self.two_pools()
        res = eq.run_equivalence(rows, meta, self.make_engine, scratch_root=str(self.root), min_triggers=2)
        self.assertEqual(set(res["arms"]), {"writer", "adapter"})
        for name, arm in res["arms"].items():
            self.assertTrue(arm["equal"], (name, arm["first_difference"]))
            self.assertEqual(arm["public"]["md5"], arm["tip"]["md5"])
            self.assertEqual(arm["public"]["md5_all"], arm["tip"]["md5_all"])
            self.assertTrue(arm["detect_lag_offset_ok"])
            self.assertEqual(arm["gap_records"]["public"], arm["gap_records"]["tip"])
            self.assertGreaterEqual(arm["triggers"], 2)
            self.assertIn("outcome", arm["public"]["n"])
        self.assertTrue(res["equal"])
        self.assertTrue(res["floor_met"])
        json.dumps(res)  # plain json
        dumped = json.dumps(res)
        for content in ("MintPA", "MintPB", "sig1000", "PDA_", "BOOST"):
            self.assertNotIn(content, dumped)  # md5s, field names and counts only: no record content in the output

    def test_k1_the_writer_arm_runs_the_pinned_writer_on_a_follower_format_dir_with_s0_from_migration_rows(self):
        from tools import arena_tip_tape_writer as aw

        rows, meta = self.two_pools()
        res = eq.run_equivalence(rows, meta, self.make_engine, scratch_root=str(self.root), min_triggers=2, arms=("writer",))
        w = res["arms"]["writer"]
        self.assertEqual(set(res["arms"]), {"writer"})
        pins = aw.code_pins()
        self.assertEqual(res["writer_pins"], {"writer_sha256": pins["writer_sha256"][:8], "adapter_sha256": pins["adapter_sha256"][:8], "reader_sha256": pins["reader_sha256"][:8],
                                              "decoder_blobs": pins["decoder_blobs"]})  # the pins the harness ran with are the repo's
        self.assertEqual(w["built"]["migrations"], 2)  # one migration row per pool, from which the writer's adapter makes the pool_create rows
        self.assertEqual((w["writer"]["zst_files"], w["writer"]["jsonl_files"]), (2, 0))  # both hours were closed and compacted by the writer and are read back through the .zst path
        self.assertEqual(w["writer"]["writer_problem"], None)
        self.assertEqual(w["tape_row_types"].get("pool_create"), 2)  # the pool_create rows the pinned ADAPTER made from the migration rows
        self.assertGreater(w["tape_row_types"].get("trade", 0), 100)
        self.assertGreater(w["tip_info"]["driver"]["rows_pool_create"], 0)
        self.assertEqual(w["tip_info"]["driver"]["rows_pool_create"], 2)
        self.assertTrue(w["equal"], w["first_difference"])

    def test_k1_the_writer_arm_fails_when_the_migration_row_is_wrong(self):
        rows, meta = self.two_pools()
        real = eq.follower_rows

        def wrong_slot(rs, mt):
            migs, trades = real(rs, mt)
            return [{**m, "slot": m["slot"] - 5} for m in migs], trades  # created 5 slots earlier than the public path's announce point

        with mock.patch.object(eq, "follower_rows", wrong_slot):
            res = eq.run_equivalence(rows, meta, self.make_engine, scratch_root=str(self.root), min_triggers=2, arms=("writer",))
        self.assertFalse(res["equal"])  # announced_slot / s0_minus_announced_slots are decision-record fields

    def test_b1_a_first_print_more_than_30_slots_after_the_previous_row_is_equal_in_both_paths_only_with_the_announce_clock_move(self):
        rows, meta = self.two_pools()
        prep = eq.prepare_rows(rows)
        last_a = max(r["slot"] for r in prep if r["pool"] == "PA")
        first_b = min(r["slot"] for r in prep if r["pool"] == "PB")
        self.assertGreater(first_b - last_a, h5.GAP_SLOTS)  # the regression case: a jump of 31+ slots into the new pool's first print
        work = self.root / "b1"
        eq.write_tape(work, eq.to_tip_rows(prep, meta))
        tipr, _ = eq.run_tip(work, self.make_engine, wall_offset_ms=2500, roots=self.roots)
        fixed = eq.run_public(prep, meta, self.make_engine)
        self.assertTrue(eq.compare(fixed, tipr, wall_offset_ms=2500)["equal"])
        legacy: list[dict] = []
        h5.replay(prep, meta, self.make_engine(legacy.append))  # h5_shadow.replay does not move the clock at the announcement, decode_notice does
        self.assertFalse(eq.compare(legacy, tipr, wall_offset_ms=2500)["equal"])
        self.assertEqual(sum(1 for r in fixed if r["type"] == "gap" and r["kind"] == "slot_jump"), 1)  # the jump is seen once, at s0 - 1, in the public path

    def test_the_harness_catches_a_decision_change(self):
        rows, meta = self.two_pools()
        prep = eq.prepare_rows(rows)
        work = self.root / "m"
        eq.write_tape(work, eq.to_tip_rows(prep, meta))
        public = eq.run_public(prep, meta, self.make_engine)
        mk50 = lambda emit: h5.Engine(emit, sps_fn=lambda p: SPS, pda_fn=lambda pool: "PDA_" + pool, classifier=h5.StaticClassifier(False, "rpc"), seal_start_ms=None,  # noqa: E731
                                      h5_look2_start_ms=None, bx10_enabled=False, q_star_sol=50.0)
        tipr, _ = eq.run_tip(work, mk50, wall_offset_ms=2500, roots=self.roots)
        self.assertFalse(eq.compare(public, tipr, wall_offset_ms=2500)["equal"])

    def test_the_harness_catches_a_wrong_stamp(self):
        rows, meta = self.two_pools()
        prep = eq.prepare_rows(rows)
        work = self.root / "s"
        eq.write_tape(work, eq.to_tip_rows(prep, meta))
        public = eq.run_public(prep, meta, self.make_engine)
        tipr, _ = eq.run_tip(work, self.make_engine, wall_offset_ms=2500, roots=self.roots)
        self.assertTrue(eq.compare(public, tipr, wall_offset_ms=2500)["equal"])
        self.assertFalse(eq.compare(public, tipr, wall_offset_ms=1)["equal"])  # the recorded offset must be the one the triggers carry

    def test_the_floor_is_30_by_default_and_enforced_by_the_summary(self):
        import inspect

        self.assertEqual(eq.MIN_TRIGGERS_DEFAULT, 30)
        self.assertEqual(inspect.signature(eq.run_equivalence).parameters["min_triggers"].default, 30)
        rows, meta = self.two_pools()
        res = eq.run_equivalence(rows, meta, self.make_engine, scratch_root=str(self.root), arms=("adapter",))
        self.assertEqual(res["min_triggers"], 30)
        self.assertTrue(res["equal"])
        self.assertFalse(res["floor_met"])  # 2 triggers < 30: exit 4, not a PASS

    def test_only_stamp_keys_are_stripped(self):
        rec = {"type": "trigger", "t_recv_ms": 1, "t_detect_ms": 2, "detect_lag_ms": 3, "t_ms": 4, "t_recv": "x", "slot": 9, "gaps": [{"t_recv_ms": 1, "kind": "k"}]}
        self.assertEqual(eq.strip_stamps(rec), {"type": "trigger", "slot": 9, "gaps": [{"kind": "k"}]})

    def test_the_cli_refuses_forbidden_paths_before_reading(self):
        for args in (["--tape-dir", "/data/mal/forward-paper/x", "--hours", "2026-08-14T12"], ["--hours", "2026-10-03T00"], ["--hours", "2026-09-16T00"]):
            self.assertEqual(eq.main([*args, "--scratch-root", str(self.root)]), 2)
        self.assertEqual(eq.main(["--hours", "2026-08-14T12", "--scratch-root", str(self.root)]), 2)  # scratch root outside /data2/mal/scratch


SSTAR_QP_MODULES = {"observe/trade_decode.py", "observe/link_state.py", "observe/trade_source.py", "tools/cap_pick_oracle.py", "tools/paper_curve_math.py", "tools/pump_structure_monitor.py"}


def first_party_closure(entry: Path) -> set[str]:
    """Repo-relative paths of every first-party module (tools.*, observe.*) that `entry` imports, transitively, lazy imports inside functions included."""
    seen: set[str] = set()
    todo = [entry]
    while todo:
        f = todo.pop()
        for n in ast.walk(ast.parse(f.read_text())):
            names: list[str] = []
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                names = [n.module] + [f"{n.module}.{a.name}" for a in n.names]
            for m in names:
                if m.split(".")[0] not in ("tools", "observe"):
                    continue
                for cand in (REPO / (m.replace(".", "/") + ".py"), REPO / m.replace(".", "/") / "__init__.py"):
                    rel = str(cand.relative_to(REPO))
                    if cand.exists() and rel not in seen:
                        seen.add(rel)
                        todo.append(cand)
    return seen


class TransitiveIdentity(unittest.TestCase):
    def test_k2_every_first_party_module_the_engine_imports_has_the_s_star_blob(self):
        closure = first_party_closure(SRC) | {"tools/h5_shadow.py"}
        self.assertTrue(SSTAR_QP_MODULES <= closure, sorted(SSTAR_QP_MODULES - closure))  # the modules QP checked by hand are in the walk
        bad = {}
        for rel in sorted(closure):
            here = subprocess.run(["git", "-C", str(REPO), "hash-object", rel], capture_output=True, text=True).stdout.strip()
            then = subprocess.run(["git", "-C", str(REPO), "rev-parse", f"{S_STAR}:{rel}"], capture_output=True, text=True)
            if then.returncode != 0 or then.stdout.strip() != here:
                bad[rel] = (here[:8], then.stdout.strip()[:8] or "absent at S*")
        self.assertEqual(bad, {})  # "S*'s decision path" rests on these blobs, not on one file
        self.assertFalse({"tools/arena_tip_push.py", "tools/h5_shadow_tip.py"} & closure)  # the wrapper's own imports are not in the engine's closure


class WrapperStart(TmpCase):
    """K3: through tip.main -> S*'s run_live -> the tip loop, with a fresh-forward start F = now."""

    def run_main(self, announce_offset_ms: int, extra: tuple = (), expect_rc: int = 0) -> list[dict]:
        now = h5.now_ms()
        t = pool_tape("PK", 1000, now // 1000 + 120, filler=True)  # prints from F + 120 s on
        prep = eq.prepare_rows(t.rows)
        meta = {"PK": {"s0": 1000, "v": V0, "mint": "MintPK"}}
        rows = [r for r in eq.to_tip_rows(prep, meta) if r["type"] != "pool_create"]
        create = ta_pool_create(slot=999, t_recv_ms=now + announce_offset_ms)
        eq.write_tape(self.dir, [create] + rows)
        arena = self.root / "arena"
        out_dir = arena / "h5-tip-start"

        async def noop(*a, **k):
            return None

        def no_oracle(*a, **k):
            raise AssertionError("the CAP-PICK environment is read in fresh-forward mode")

        with mock.patch.object(atp, "DST_ROOTS", self.roots), mock.patch.object(atp, "push_problem", return_value=None), mock.patch.object(h5, "ARENA_ROOT", str(arena)), \
                mock.patch.dict(os.environ, {"H5_CODE_SHA": "0123456789abcdef0123456789abcdef01234567"}), mock.patch.object(h5, "SynClassifier", lambda: h5.StaticClassifier(False, "rpc")), \
                mock.patch.object(h5, "run_pump_feed", noop), mock.patch.object(h5, "cap_pick_oracle_from_env", no_oracle):
            rc = tip.main(["--tip-input", str(self.dir), "--fresh-forward", "ARENA", "--out-dir", str(out_dir), "--max-seconds", "1", "--sockets", "1", *extra])
        self.assertEqual(rc, expect_rc)
        return [json.loads(ln) for fp in sorted(out_dir.glob("h5-shadow-*.jsonl")) for ln in fp.read_text().splitlines()]

    def test_k3_a_pool_announced_before_the_fresh_start_gets_no_trigger_record(self):
        recs = self.run_main(-10_000)  # CreatePool 10 s before F; the prints come after F
        self.assertEqual([r["type"] for r in recs if r["type"] == "start"], ["start"])
        stop = [r for r in recs if r["type"] == "tip_input_stop"][0]
        dropped = stop["driver"].get("rows_before_start", 0) + stop["source"].get("tap_rows_before_start", 0)  # the reader's start filter or the driver's, whichever saw it first
        self.assertGreaterEqual(dropped, 1)  # the announcement was dropped, as the public feed never saw it
        self.assertEqual([r for r in recs if r["type"] in ("trigger", "pool", "outcome") and r.get("pool") == "PK"], [])
        self.assertEqual([r for r in recs if r["type"] == "trigger"], [])

    def test_k3_control_the_same_pool_announced_after_the_fresh_start_triggers(self):
        recs = self.run_main(+30_000)
        self.assertGreaterEqual(len([r for r in recs if r["type"] == "trigger" and r.get("pool") == "PK"]), 1)
        stop = [r for r in recs if r["type"] == "tip_input_stop"][0]
        self.assertEqual(stop["driver"].get("rows_before_start", 0) + stop["source"].get("tap_rows_before_start", 0), 0)


class Smoke(TmpCase):
    """The early s1t smoke: S*'s Look-2 seal on every pool; no Q50-rule outcome is ever priced."""

    OUTCOMES = ("outcome", "outcome_bx10", "strip")
    run_main = WrapperStart.run_main

    def test_control_without_smoke_prices_outcomes(self):
        recs = self.run_main(+30_000)
        self.assertGreaterEqual(len([r for r in recs if r["type"] == "outcome"]), 1)  # fresh mode, a pool before SUCC_END_MS: outcomes are computed

    def test_smoke_computes_no_outcome_and_flags_every_pool(self):
        recs = self.run_main(+30_000, extra=("--smoke",))
        self.assertEqual([r for r in recs if r["type"] in self.OUTCOMES], [])
        trig = [r for r in recs if r["type"] == "trigger" and r.get("pool") == "PK"]
        self.assertGreaterEqual(len(trig), 1)  # decision-time records are still written
        for r in trig:
            self.assertTrue(r["h5_look2_sealed"])
            self.assertNotIn("exit_ladder_trigger_slots", r)
        pools = [r for r in recs if r["type"] == "pool" and r.get("pool") == "PK"]
        self.assertEqual(len(pools), 1)
        self.assertTrue(pools[0]["h5_look2_sealed"])
        self.assertEqual((pools[0]["min_q_pv_sol"], pools[0]["min_q_fv_sol"]), (None, None))  # min_q withheld on a sealed pool
        start = [r for r in recs if r["type"] == "tip_input_start"][0]
        self.assertEqual((start["smoke"], start["look2_seal"]), (True, "every pool, no outcome priced (smoke)"))
        stop = [r for r in recs if r["type"] == "tip_input_stop"][0]
        self.assertEqual(stop["smoke_outcome_blocked"], 0)  # the seal prevented them: the second wall never fired

    def test_the_seal_is_per_pool_before_and_after_the_end_constant(self):
        eng, out = make_engine(fresh_start_ms=1)
        guard = tip.arm_smoke(eng)
        for ts in (1_800_000_000, 1_790_000_000):  # a pool after SUCC_END_MS and one before it, both sealed
            p = mock.Mock(s0_ts=ts, s0_recv_ms=ts * 1000, h5_sealed_cache=None)
            self.assertTrue(eng._h5_sealed(p), ts)
        self.assertEqual(guard["blocked"], 0)
        eng2, _ = make_engine(fresh_start_ms=1, h5_look2_start_ms=h5.SUCC_END_MS)  # as run_live builds it, without smoke: a pool before SUCC_END_MS is not sealed
        self.assertFalse(eng2._h5_sealed(mock.Mock(s0_ts=1_790_000_000, s0_recv_ms=1_790_000_000_000, h5_sealed_cache=None)))

    def test_arm_smoke_refuses_after_the_first_row(self):
        eng, _ = make_engine(fresh_start_ms=None)
        eng.on_create_pool("PQ", "MintPQ", h5.WSOL_MINT, 1, 5)
        eng.advance(5, 5)
        with self.assertRaises(RuntimeError):
            tip.arm_smoke(eng)

    def test_the_second_wall_drops_an_outcome_and_halts_with_exit_3_if_the_seal_ever_fails(self):
        with mock.patch.object(h5.Engine, "_h5_sealed_now", lambda self, p: False):  # a broken seal: the engine would price the outcome
            recs = self.run_main(+30_000, extra=("--smoke",), expect_rc=tip.EXIT_HALTED)
        self.assertEqual([r for r in recs if r["type"] in self.OUTCOMES], [])  # nothing priced reached the output
        halted = [r for r in recs if r["type"] == "tip_input_halted"]
        self.assertEqual([h["problem"] for h in halted], ["smoke_outcome_computed"])
        self.assertGreaterEqual(halted[0]["blocked"], 1)


def ta_pool_create(*, slot: int, t_recv_ms: int) -> dict:
    from tools import arena_tip_adapter as ta

    return ta.pool_create_row({"slot": slot, "t_recv_ms": t_recv_ms, "pool": "PK", "mint": "MintPK", "quote_mint": h5.WSOL_MINT, "signature": "create-PK", "event_index": 0, "tx_index": 0,
                               "block_time": t_recv_ms // 1000})


class ReaderLossAndCounters(TmpCase):
    def test_s1_a_bad_json_line_is_marked_as_loss_and_flags_open_pools(self):
        t = pool_tape("PL", 1000, 1_800_000_000, filler=True)
        rows = tip_rows_for(t)
        eq.write_tape(self.dir, rows)
        fp = next(self.dir.glob("tape-*.jsonl"))
        lines = fp.read_bytes().split(b"\n")
        lines.insert(40, b"{this is not json")  # a corrupt line in the middle of the hour
        fp.write_bytes(b"\n".join(lines))
        eng, out = make_engine(fresh_start_ms=None)
        s = self.source()
        s.start()
        drv = tip.TipDriver(eng, s)
        while drv.step(100):
            pass
        self.assertEqual(s.stats["reader_loss_bad_lines"], 1)
        probs = [r for r in out if r["type"] == "tip_input_problem"]
        self.assertEqual([r["problem"] for r in probs], ["reader_bad_lines"])
        self.assertTrue(any(r["type"] == "gap" and r["kind"] == "feed_restart" and r["flags_pools"] for r in out))
        self.assertTrue(any(r["type"] == "tip_input_resumed" for r in out))
        self.assertGreater(drv.counters["rows_trade_pumpswap"], 40)  # the other rows were applied

    def test_s1_a_zst_error_is_marked_as_loss(self):
        eq.write_tape(self.dir, tip_rows_for(pool_tape("PZ", 1000, 1_800_000_000, filler=True)))
        fp = next(self.dir.glob("tape-*.jsonl"))
        fp.rename(fp.with_suffix(".jsonl.zst"))  # a closed hour that cannot be decompressed
        fp.with_suffix(".jsonl.zst").write_bytes(b"not a zstd frame")
        eng, out = make_engine(fresh_start_ms=None)
        s = self.source()
        s.start()
        drv = tip.TipDriver(eng, s)
        for _ in range(3):
            drv.step(100)
        self.assertGreaterEqual(s.stats["reader_loss_zst_errors"], 1)
        self.assertTrue(any(r["type"] == "tip_input_problem" and r["problem"] == "reader_zst_errors" for r in out))

    def test_s2_the_input_counters_are_written_in_live_mode(self):
        eq.write_tape(self.dir, tip_rows_for(pool_tape("PC", 1000, 1_800_000_000, filler=True)))
        eng, out = make_engine(bx10_enabled=True)
        s = self.source()
        s.start()
        stop = asyncio.Event()
        with mock.patch.object(tip, "STATS_EVERY_S", 0.0):
            asyncio.run(asyncio.wait_for(tip.make_run_feed(s, {}, poll_s=0.01)(None, eng, stop, max_seconds=0.2), 5))
        stats = [r for r in out if r["type"] == "tip_input_stats"]
        self.assertGreaterEqual(len(stats), 1)
        end = [r for r in out if r["type"] == "tip_input_stop"]
        self.assertEqual(len(end), 1)
        self.assertGreater(end[0]["driver"]["rows_trade_pumpswap"], 40)
        self.assertGreater(end[0]["source"]["rows"], 40)
        self.assertEqual(end[0]["driver"]["rows_pool_create"], 1)
        self.assertEqual(end[0]["halted"], False)

    def test_s2_the_stop_record_is_written_when_the_book_halts(self):
        eq.write_tape(self.dir, tip_rows_for(pool_tape("PY", 1000, 1_800_000_000, filler=True)))
        eng, out = make_engine()
        s = self.source([None, "decoder_pin"])
        s.start()
        state: dict = {}
        asyncio.run(asyncio.wait_for(tip.make_run_feed(s, state, poll_s=0.01)(None, eng, asyncio.Event(), max_seconds=5), 5))
        self.assertEqual(state["halted"], "decoder_pin")
        self.assertEqual([r["halted"] for r in out if r["type"] == "tip_input_stop"], [True])


if __name__ == "__main__":
    unittest.main()
