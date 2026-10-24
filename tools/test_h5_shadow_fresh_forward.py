"""--fresh-forward ARENA of tools/h5_shadow.py (hunt20, the forward-paper arena). Synthetic fixtures only; no network, no /data/mal reads."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import h5_shadow as h5
from tools.test_h5_shadow import SOL, Tape, make_engine, types

SHA = "0123456789abcdef0123456789abcdef01234567"


def pool_tape(pool: str, slot0: int, ts0: int, filler: bool = False) -> Tape:
    """One BOOST pool (the per-pool PDA buys 1 SOL every 25 slots from s0 + 5) that a dump at s0 + 200 drains to Q = 35 SOL: a trigger, with outcomes.
    `filler`: a 10,000-lamport buy every 25 slots from s0 + 32 on, so that no jump between prints reaches the engine's 30-slot gap threshold (the default tape has two)."""
    ev = [(slot0, "buy", "A", SOL // 10)] + [(slot0 + 5 + 25 * i, "buy", "PDA_" + pool, SOL) for i in range(18)]
    ev += [(slot0 + 200, "sell", "DUMP", None), (slot0 + 500, "sell", "Z", SOL // 2), (slot0 + 900, "buy", "W", SOL)]
    if filler:
        ev += [(slot0 + 7 + 25 * k, "buy", "FILL", 10_000) for k in range(1, 37)]
    t = Tape(pool=pool, slot0=slot0, ts0=ts0)
    for slot, side, trader, sol in sorted(ev, key=lambda e: e[0]):
        if sol is None:
            sol = int(t.q + t.v - 35 * SOL)
        t.row(slot, side, trader, sol, mint="Mint" + pool)
    return t


def run_pools(eng, *tapes: Tape) -> None:
    for t in tapes:
        eng.on_create_pool(t.pool, "Mint" + t.pool, h5.WSOL_MINT, t.slot0 - 1, t.rows[0]["t_recv_ms"])  # announced with its first print (a slot jump into it is detected on that print)
        for r in t.rows:
            eng.on_trade(r)
    eng.close_all("shutdown")


def fresh_engine(start_ms, **kw):
    """The Engine as run_live builds it with --fresh-forward ARENA."""
    kw.setdefault("seal_start_ms", None)
    kw.setdefault("h5_look2_start_ms", None)
    kw.setdefault("classifier", h5.StaticClassifier(False, "rpc"))  # a plain pool, classified by the RPC fallback: the class whose pool record keeps its price fields
    return make_engine(bx10_enabled=True, fresh_start_ms=start_ms, **kw)


class FlagTests(unittest.TestCase):
    def test_only_the_literal_token_turns_it_on(self):
        self.assertEqual(h5.FRESH_FORWARD_REF, "ARENA")
        self.assertTrue(h5.fresh_forward_flag("ARENA"))
        for bad in (None, "", "1", "true", "arena", "Arena", "ARENA ", " ARENA", "ARENAx", "EXP-026", "EXP-024-Am2"):
            self.assertFalse(h5.fresh_forward_flag(bad), repr(bad))

    def test_the_command_line_parses_any_value_and_defaults_to_absent(self):
        ap = h5.build_parser()
        self.assertIsNone(ap.parse_args([]).fresh_forward)
        for v in ("ARENA", "arena", ""):
            self.assertEqual(ap.parse_args(["--fresh-forward", v]).fresh_forward, v)  # never refused by the parser: the engine flag is fresh_forward_flag(v)


class OutDirTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir="/tmp", prefix="arena-root-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / "home"
        self.p = mock.patch.object(h5, "ARENA_ROOT", str(self.root / "arena"))
        self.p.start()
        self.addCleanup(self.p.stop)

    def ok(self, path):
        return h5.check_fresh_out_dir(str(path), home=str(self.home))

    def refused(self, path, *words):
        with self.assertRaises(ValueError) as cm:
            self.ok(path)
        for w in words:
            self.assertIn(w, str(cm.exception))

    def test_a_dir_under_the_arena_root_h5_star_is_accepted(self):
        a = self.root / "arena"
        self.assertEqual(self.ok(a / "h5-shadow-2026-10-18"), a / "h5-shadow-2026-10-18")
        self.assertEqual(self.ok(a / "h5-x" / "sub"), a / "h5-x" / "sub")
        with mock.patch.object(h5, "ARENA_ROOT", "/data/mal/arena"):  # the production root, a path that need not exist yet
            self.assertEqual(h5.check_fresh_out_dir("/data/mal/arena/h5-shadow-2026-10-18", home=str(self.home)), Path("/data/mal/arena/h5-shadow-2026-10-18"))

    def test_everything_else_is_refused(self):
        a = self.root / "arena"
        self.refused(a, "must resolve under")  # the root itself
        self.refused(a / "other", "must resolve under")  # not h5-*
        self.refused(a / "h5", "must resolve under")  # the prefix is "h5-"
        self.refused(self.root / "h5-shadow", "must resolve under")  # outside the root
        self.refused("/tmp/h5-elsewhere", "must resolve under")
        self.refused(a / "h5-x" / ".." / "other", "'..'")
        self.refused("", "empty")
        with self.assertRaises(ValueError):  # the production root, unpatched
            with mock.patch.object(h5, "ARENA_ROOT", "/data/mal/arena"):
                h5.check_fresh_out_dir("/data/mal/arena/other")

    def test_the_home_arena_root_of_fast_0_is_accepted_and_the_home_shadow_dir_is_not(self):
        h = self.home / "data"
        self.assertEqual(self.ok(h / "arena" / "h5-2026-10-18"), h / "arena" / "h5-2026-10-18")
        self.assertEqual(self.ok(h / "arena" / "h5-x" / "sub"), h / "arena" / "h5-x" / "sub")
        self.refused(h / "arena", "must resolve under")
        self.refused(h / "arena" / "other", "must resolve under")
        self.refused(h / "h5-2026-10-18", "must resolve under")
        self.refused(h / "h5-shadow", "never feeds the live executor")
        self.refused(h / "h5-shadow-2026-10-18", "never feeds the live executor")
        self.refused(h / "arena" / ".." / "h5-shadow", "'..'")

    def test_the_live_shadow_dirs_are_refused_explicitly(self):
        for bad in ("/srv/mal-h5-shadow", "/srv/mal-h5-shadow/x", "/var/lib/mal-live", "/var/lib/mal-live/ledger",
                    self.home / "data" / "h5-shadow", self.home / "data" / "h5-shadow" / "sub", self.home / "data" / "h5-shadow-test"):
            self.refused(bad, "never feeds the live executor")

    def test_a_symlink_into_a_live_dir_or_out_of_the_arena_is_refused(self):
        a = self.root / "arena"
        a.mkdir()
        (a / "h5-to-live").symlink_to("/srv/mal-h5-shadow")
        (a / "h5-to-home").symlink_to(self.home / "data" / "h5-shadow")
        (a / "h5-to-tmp").symlink_to(self.root)
        self.refused(a / "h5-to-live", "never feeds the live executor")
        self.refused(a / "h5-to-live" / "x", "never feeds the live executor")
        self.refused(a / "h5-to-home", "never feeds the live executor")
        self.refused(a / "h5-to-tmp", "must resolve under")
        (self.root / "real").mkdir()
        (self.root / "link").symlink_to(a)  # a symlinked root: the unresolved path is outside, the resolved one inside
        self.refused(self.root / "link" / "h5-x", "must resolve under")


class MainTests(unittest.TestCase):
    """h5.main() refuses a bad fresh-forward call before it starts anything; the flag absent is none of this."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir="/tmp", prefix="arena-root-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        p = mock.patch.object(h5, "ARENA_ROOT", str(self.root / "arena"))
        p.start()
        self.addCleanup(p.stop)

    def main(self, *argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), mock.patch.object(h5, "run_live", side_effect=AssertionError("run_live must not start")), \
                mock.patch.object(h5, "run_replay", side_effect=AssertionError("replay must not start")):
            with self.assertRaises(SystemExit) as cm:
                h5.main(list(argv))
        self.assertEqual(cm.exception.code, 2)
        return err.getvalue()

    def test_refusals(self):
        good = str(self.root / "arena" / "h5-shadow-x")
        self.assertIn("is empty", self.main("--fresh-forward", "ARENA"))  # --out-dir is required
        self.assertIn("never feeds the live executor", self.main("--fresh-forward", "ARENA", "--out-dir", "/srv/mal-h5-shadow"))
        self.assertIn("never feeds the live executor", self.main("--fresh-forward", "ARENA", "--out-dir", os.path.expanduser("~/data/h5-shadow")))
        self.assertIn("must resolve under", self.main("--fresh-forward", "ARENA", "--out-dir", "/tmp/x"))
        self.assertIn("--tip-dir", self.main("--fresh-forward", "ARENA", "--out-dir", good, "--tip-dir", "/tmp/tip"))
        self.assertIn("--h5-look2-observed", self.main("--fresh-forward", "ARENA", "--out-dir", good, "--h5-look2-observed", "EXP-024-Am2"))
        self.assertIn("live feed only", self.main("--fresh-forward", "ARENA", "--out-dir", good, "--replay-tape", "/x", "--replay-hours", "h"))

    def test_a_good_call_reaches_run_live_and_a_wrong_token_does_not_change_the_checks(self):
        good = str(self.root / "arena" / "h5-shadow-x")
        started = []

        async def fake_run_live(args):
            started.append(args)
            return 0

        with mock.patch.object(h5, "run_live", fake_run_live):
            self.assertEqual(h5.main(["--fresh-forward", "ARENA", "--out-dir", good]), 0)
            # the bx10 pattern: a wrong token is off, so the default checks apply (any out dir the default accepts)
            self.assertEqual(h5.main(["--fresh-forward", "arena", "--out-dir", "/tmp/h5-fresh-off"]), 0)
        self.assertEqual([a.fresh_forward for a in started], ["ARENA", "arena"])


class EngineTests(unittest.TestCase):
    T0 = 1_800_000_000  # pool A block time (2027: past every seal)
    T1 = T0 + 1_600  # pool B's, 1,600 s later (slot0 + 4,000 slots * 0.4 s)

    def tapes(self):
        return pool_tape("PoolAAAA", 1000, self.T0), pool_tape("PoolBBBB", 5000, self.T1)

    def test_the_seals_withhold_this_pool_without_the_mode_and_the_mode_writes_it_whole(self):
        a, _ = self.tapes()
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=h5.cap_pick_seal_oracle_stub, bx10_enabled=True)  # run_live's default engine
        run_pools(eng, a)
        self.assertEqual((types(out, "trigger"), types(out, "outcome"), types(out, "outcome_bx10")), ([], [], []))
        eng, out = fresh_engine(self.T0 * 1000 - 5_000)
        a, _ = self.tapes()
        run_pools(eng, a)
        self.assertEqual(sorted(r["variant"] for r in types(out, "trigger")), ["fv", "pv"])
        self.assertEqual(sorted(r["variant"] for r in types(out, "outcome")), ["fv", "pv"])
        self.assertEqual(sorted(r["variant"] for r in types(out, "outcome_bx10")), ["fv", "pv"])
        (pool,) = types(out, "pool")
        self.assertFalse(pool["sealed"])
        self.assertNotIn("h5_look2_sealed", pool)
        self.assertIsNotNone(pool["min_q_pv_sol"])
        self.assertTrue(types(out, "strip"))
        self.assertTrue(types(out, "boost_slice"))
        self.assertFalse([r for r in out if r["type"] == "sealed_hour"])
        for o in types(out, "outcome"):
            self.assertIsNotNone(o["legs"]["primary"]["net"])
        for o in types(out, "outcome_bx10"):
            self.assertIsNotNone(o["legs"]["binding"]["net"])
            self.assertNotIn("error", o)
        self.assertEqual(eng.counters["fresh_ignored_pre_start"], 0)

    def test_the_h5_look2_seal_is_off_also_inside_its_window_and_past_its_end(self):
        eng, out = fresh_engine(0)  # h5_look2_start_ms None, as run_live builds it; the synthetic tape is in 2027, past H5_LOOK2_END_MS
        a, _ = self.tapes()
        run_pools(eng, a)
        self.assertEqual(eng.counters["h5_look2_sealed_pools"], 0)
        self.assertEqual(len(types(out, "outcome")), 2)

    def test_a_pool_with_s0_before_the_start_is_ignored_entirely_and_a_later_one_is_written(self):
        a, b = self.tapes()
        eng, out = fresh_engine((self.T0 + 800) * 1000)  # between the two pools
        run_pools(eng, a, b)
        self.assertEqual({r["pool"] for r in out if "pool" in r and r["type"] != "gap"}, {"PoolBBBB"})
        self.assertEqual([r["type"] for r in out if r.get("pool") == "PoolAAAA"], [])  # no pool, trigger, outcome, outcome_bx10, strip or boost_slice record
        self.assertEqual(eng.counters["fresh_ignored_pre_start"], 1)
        self.assertEqual(eng.counters["pools_tracked"], 1)
        self.assertEqual(len(types(out, "trigger")), 2)
        self.assertEqual(len(types(out, "outcome")), 2)
        self.assertEqual(len(types(out, "outcome_bx10")), 2)

    def test_the_start_instant_boundary(self):
        a, _ = self.tapes()
        first = a.rows[0]  # block time T0 s, received at T0 s + 37 ms
        for start_ms, tracked in ((first["t_recv_ms"] + 1, False),  # received before the start
                                  (self.T0 * 1000 + 1, False),  # block time is whole seconds: the start's own second counts as before
                                  (self.T0 * 1000, True)):
            eng, out = fresh_engine(start_ms)
            run_pools(eng, pool_tape("PoolAAAA", 1000, self.T0))
            self.assertEqual(len(types(out, "pool")), 1 if tracked else 0, start_ms)
            self.assertEqual(eng.counters["fresh_ignored_pre_start"], 0 if tracked else 1, start_ms)

    def test_an_ignored_pool_does_not_come_back_on_a_later_print(self):
        a, _ = self.tapes()
        eng, out = fresh_engine((self.T0 + 5_000) * 1000)
        run_pools(eng, a)  # a pool's every print is checked once (seen), the pool is never tracked
        self.assertEqual((eng.counters["fresh_ignored_pre_start"], eng.counters["pools_tracked"], out and types(out, "pool")), (1, 0, []))

    def test_records_equal_the_default_engine_with_both_seals_off(self):
        a, b = self.tapes()
        eng1, out1 = make_engine(seal_start_ms=None, h5_look2_start_ms=None, bx10_enabled=True, classifier=h5.StaticClassifier(False, "rpc"))
        run_pools(eng1, a, b)
        a, b = self.tapes()
        eng2, out2 = fresh_engine(0)
        run_pools(eng2, a, b)
        out2 = [{k: v for k, v in r.items() if not k.startswith("buys")} if r["type"] == "strip" else r for r in out2]  # the fresh strip's only addition
        self.assertEqual([json.dumps(r, sort_keys=True) for r in out1], [json.dumps(r, sort_keys=True) for r in out2])
        c1, c2 = dict(eng1.counters), dict(eng2.counters)
        self.assertEqual(c1, {k: v for k, v in c2.items() if k != "fresh_ignored_pre_start"})

    def test_engine_default_has_no_start_gate(self):
        eng, _ = make_engine()
        self.assertIsNone(eng.fresh_start_ms)


class RunLiveTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir="/tmp", prefix="arena-root-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        p = mock.patch.object(h5, "ARENA_ROOT", str(self.root / "arena"))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {"H5_CODE_SHA": SHA})
        p.start()
        self.addCleanup(p.stop)

    def run_live(self, argv, out_dir):
        args = h5.build_parser().parse_args(["--out-dir", str(out_dir), "--sockets", "3", *argv])
        built, tips = [], []
        real = h5.Engine

        def spy(*a, **k):
            built.append(k)
            return real(*a, **k)

        class NoTip:
            def __init__(self, *a, **k):
                tips.append(1)

        async def no_feed(*a, **k):
            return None

        def no_oracle(*a, **k):
            raise AssertionError("the CAP-PICK environment is read in fresh-forward mode")

        with mock.patch.object(h5, "run_feed", no_feed), mock.patch.object(h5, "build_source", lambda *a, **k: None), mock.patch.object(h5, "Engine", spy), \
                mock.patch.object(h5, "TipTail", NoTip), mock.patch.object(h5, "cap_pick_oracle_from_env", no_oracle if "--fresh-forward" in argv and "ARENA" in argv
                                                                                                         else h5.cap_pick_oracle_from_env):
            before = h5.now_ms()
            self.assertEqual(asyncio.run(h5.run_live(args)), 0)
            after = h5.now_ms()
        rows = [json.loads(line) for f in sorted(Path(out_dir).glob("h5-shadow-*.jsonl")) for line in f.read_text().splitlines()]
        return [r for r in rows if r["type"] == "start"][0], built, tips, (before, after)

    def test_the_start_record_and_the_engine_of_a_fresh_forward_run(self):
        out = self.root / "arena" / "h5-shadow-2026-10-18"
        start, built, tips, (t0, t1) = self.run_live(["--fresh-forward", "ARENA"], out)
        self.assertEqual(start["fresh_forward"], "ARENA")
        self.assertEqual(start["code_sha"], SHA)
        self.assertTrue(t0 <= start["start_ms"] <= t1)
        self.assertEqual(start["t_ms"], start["start_ms"])  # "the start record's time" is start_ms
        self.assertIs(start["bx10"], True)
        self.assertEqual(start["tip_union"], "off")
        self.assertEqual((start["seal"]["applied"], start["seal"]["start_ms"], start["seal"]["oracle"]), (False, None, "not_applied"))
        self.assertEqual((start["h5_look2"]["applied"], start["h5_look2"]["observed"], start["h5_look2"]["start_ms"]), (False, False, None))
        self.assertEqual(start["h5_look2"]["end_only_ms"], h5.SUCC_END_MS)  # S*: the fresh end is SUCC_END_MS (F50: H5_LOOK2_END_MS)
        self.assertIs(start["dirty"], False)
        self.assertEqual(start["endpoints"], ["default"])
        self.assertIn("host", start)
        self.assertEqual((start["keys"], start["sends"]), ("none", "none"))
        self.assertEqual(start["out_dir"], str(out))
        (k,) = built
        self.assertIs(k["bx10_enabled"], True)
        self.assertEqual(k["fresh_start_ms"], start["start_ms"])
        self.assertIsNone(k["seal_start_ms"])
        self.assertEqual(k["h5_look2_start_ms"], h5.SUCC_END_MS)  # only an END applies, fail closed: at S* SUCC_END_MS (F50: H5_LOOK2_END_MS, 2026-11-06T00Z)
        self.assertIsNone(k["pick_oracle"])
        self.assertIsNone(k["suppress_outcome"])
        self.assertIs(k["h5_look2_observed"], False)
        self.assertEqual(tips, [])  # no TipTail was built

    def test_dirty_and_endpoints_are_recorded_and_no_key_reaches_the_record(self):
        out = self.root / "arena" / "h5-dirty"
        with mock.patch.dict(os.environ, {"H5_DIRTY": "1"}):
            start, _, _, _ = self.run_live(["--fresh-forward", "ARENA", "--ws-url", "wss://b.example/x", "--ws-url", "wss://a.example/?api-key=XYZ"], out)
        self.assertIs(start["dirty"], True)
        self.assertEqual(start["endpoints"], ["wss://a.example/?REDACTED", "wss://b.example/x"])
        self.assertNotIn("XYZ", json.dumps(start))

    def test_bx10_is_on_with_or_without_its_own_flag(self):
        for extra in ([], ["--bx10-enable", "EXP-026"], ["--bx10-enable", "1"]):
            out = self.root / "arena" / f"h5-bx-{len(extra)}{extra[-1] if extra else ''}"
            start, built, _, _ = self.run_live(["--fresh-forward", "ARENA", *extra], out)
            self.assertIs(start["bx10"], True)
            self.assertIs(built[0]["bx10_enabled"], True)

    def test_refusals_happen_before_anything_is_created(self):
        for bad_args, bad_out in ((["--fresh-forward", "ARENA"], "/srv/mal-h5-shadow"),
                                  (["--fresh-forward", "ARENA"], self.root / "elsewhere"),
                                  (["--fresh-forward", "ARENA", "--tip-dir", "/tmp"], self.root / "arena" / "h5-x"),
                                  (["--fresh-forward", "ARENA", "--h5-look2-observed", "EXP-024-Am2"], self.root / "arena" / "h5-y")):
            with self.assertRaises(ValueError):
                self.run_live(bad_args, bad_out)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [])  # no dir was made anywhere under the temp root

    def test_flag_absent_or_wrong_token_leaves_the_default_start_record_unchanged(self):
        for i, argv in enumerate(([], ["--fresh-forward", "arena"], ["--fresh-forward", "1"])):
            out = Path(tempfile.mkdtemp(dir="/tmp", prefix="h5-ff-off-"))
            self.addCleanup(shutil.rmtree, out, ignore_errors=True)
            if argv:
                with self.assertLogs("mal.h5_shadow", level="WARNING"):
                    start, built, _, _ = self.run_live(argv, out)
            else:
                start, built, _, _ = self.run_live(argv, out)
            self.assertNotIn("fresh_forward", start)
            self.assertNotIn("start_ms", start)
            self.assertNotIn("tip_union", start)
            self.assertEqual(start["seal"]["start_ms"], h5.SEAL_START_MS)
            self.assertIs(start["bx10"], False)
            self.assertNotIn("fresh_start_ms", built[0])
            self.assertEqual(built[0]["seal_start_ms"] if "seal_start_ms" in built[0] else h5.SEAL_START_MS, h5.SEAL_START_MS)


class FreshRecordTests(unittest.TestCase):
    """The three record-level additions of fresh mode: strip.buys, the synthetic-class embargo on pool records, the END of the Look-2 seal."""

    def test_the_strip_carries_the_buys_for_a_pressure_recomputation(self):
        eng, out = fresh_engine(0)
        run_pools(eng, pool_tape("PoolAAAA", 1000, 1_800_000_000))
        (strip,) = types(out, "strip")
        self.assertEqual(strip["buys_cols"], ["slot", "sol_lamports"])
        self.assertFalse(strip["buys_truncated"])
        from_slot = strip["from_slot"]
        buy_slots = [r[0] for r in strip["buys"]]
        self.assertTrue(all(s >= from_slot - h5.FRESH_BUYS_BACK_SLOTS for s in buy_slots))
        # trigger print at slot 1200: buys from slot 1150 on = BOOST slices 1155, 1180, ... 1430 (12 of the 18) and the closing buy at 1900; no sell, no earlier buy
        self.assertEqual(from_slot, 1200)
        self.assertEqual(buy_slots, [1155 + 25 * i for i in range(12)] + [1900])
        self.assertTrue(all(r[1] > 0 for r in strip["buys"]))
        # default engine: the strip is as before
        eng, out = make_engine(seal_start_ms=None, h5_look2_start_ms=None)
        run_pools(eng, pool_tape("PoolAAAA", 1000, 1_800_000_000))
        self.assertFalse([k for k in types(out, "strip")[0] if k.startswith("buys")])

    def test_pool_records_of_synthetic_unclassified_or_other_source_pools_carry_no_price_field(self):
        for clf in (h5.StaticClassifier(True, "rpc"), h5.StaticClassifier(None), h5.StaticClassifier(False, "ws"), h5.StaticClassifier(False, "pre_event_binary")):
            eng, out = fresh_engine(0, classifier=clf)
            run_pools(eng, pool_tape("PoolAAAA", 1000, 1_800_000_000))
            (pool,) = types(out, "pool")
            for k in ("min_q_pv_sol", "min_q_fv_sol", "pv_fv_disagree_sells"):
                self.assertIsNone(pool[k], (clf.syn, clf.src, k))
            self.assertEqual(pool["price_fields_withheld"], "synthetic_class_embargo")
            if clf.syn is not False:  # a synthetic or unclassified pool has no trigger, outcome or strip at all
                self.assertEqual([r["type"] for r in out if r["type"] in ("trigger", "outcome", "outcome_bx10", "strip")], [])
        eng, out = fresh_engine(0)  # the literal plain class keeps them
        run_pools(eng, pool_tape("PoolAAAA", 1000, 1_800_000_000))
        (pool,) = types(out, "pool")
        self.assertIsNotNone(pool["min_q_pv_sol"])
        self.assertNotIn("price_fields_withheld", pool)

    def test_the_default_engine_never_withholds_for_the_class(self):
        eng, out = make_engine(seal_start_ms=None, h5_look2_start_ms=None, classifier=h5.StaticClassifier(False, "ws"))
        run_pools(eng, pool_tape("PoolAAAA", 1000, 1_800_000_000))
        (pool,) = types(out, "pool")
        self.assertIsNotNone(pool["min_q_pv_sol"])
        self.assertNotIn("price_fields_withheld", pool)

    def test_a_pool_from_2026_11_06_on_is_decision_only_and_one_before_it_is_whole(self):
        end_s = h5.H5_LOOK2_END_MS // 1000
        for ts0, whole in ((end_s - 3_000, True), (end_s + 100, False)):
            eng, out = fresh_engine(0, h5_look2_start_ms=h5.H5_LOOK2_END_MS)  # as run_live builds it
            run_pools(eng, pool_tape("PoolAAAA", 1000, ts0))
            self.assertEqual(len(types(out, "trigger")), 2)  # the decision itself is written either way
            self.assertEqual(len(types(out, "outcome")), 2 if whole else 0)
            self.assertEqual(len(types(out, "outcome_bx10")), 2 if whole else 0)
            self.assertEqual(len(types(out, "strip")), 1 if whole else 0)
            if not whole:
                self.assertTrue(all(t["h5_look2_sealed"] for t in types(out, "trigger")))
                self.assertIsNone(types(out, "pool")[0]["min_q_pv_sol"])


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "research" / "arena-h5.sh"


class NoR4bTests(unittest.TestCase):
    """DEC-031 s3.1: no arena process uses tools/r4b_*.py. tools/fixtures/arena_h5/build_parity.py (dev time only) is the one place that imports r4b_book."""

    ROOT = Path(__file__).resolve().parent.parent

    def test_no_arena_module_imports_r4b_statically_or_at_runtime(self):
        import ast
        import subprocess
        import sys

        mods = ("arena_h5_cost", "arena_h5_score", "arena_ws_probe", "h5_shadow")
        for m in mods:
            for node in ast.walk(ast.parse((self.ROOT / "tools" / f"{m}.py").read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] + [a.name for a in node.names] if isinstance(node, ast.ImportFrom) else []
                self.assertFalse([n for n in names if "r4b" in n], (m, names))
        code = "import sys; sys.path.insert(0, %r); import tools.arena_h5_cost, tools.arena_h5_score, tools.arena_ws_probe, tools.h5_shadow; print(sorted(m for m in sys.modules if 'r4b' in m))" % str(self.ROOT)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd="/")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "[]"), r.stderr)

    def test_the_wrapper_never_invokes_the_parity_builder_and_the_builder_says_it_is_dev_time_only(self):
        text = SCRIPT.read_text()
        for word in ("build_parity", "r4b", "fixtures"):
            self.assertNotIn(word, text)
        builder = (self.ROOT / "tools" / "fixtures" / "arena_h5" / "build_parity.py").read_text()
        self.assertTrue(builder.startswith('"""DEV-TIME ONLY: imports tools.r4b_book to build the parity fixture; never run by any arena process (DEC-031 \u00a73.1)'))

class ArenaScriptTests(unittest.TestCase):
    """scripts/research/arena-h5.sh: POSIX sh, runs on mal-fast-0, refuses anything but $HOME/data/arena/h5-* and /data/mal/arena/h5-*, always passes --fresh-forward ARENA and
    nothing for the seals, and refuses (exit 5 / 7) a checkout whose commit it cannot name or that has tracked-file changes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp", prefix="arena-sh-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = self.tmp / "fakepy"
        self.fake.write_text('#!/bin/sh\nif [ "$1" = "-I" ]; then exit 0; fi\nprintf "ARGV:%s\\n" "$*"\nprintf "SHA:%s\\n" "$H5_CODE_SHA"\nprintf "DIRTY:%s\\n" "$H5_DIRTY"\n')
        self.fake.chmod(0o755)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "ARENA_H5_PYTHON": str(self.fake), "ARENA_H5_ALLOW_DIRTY": "1", "ARENA_H5_ALLOW_ANY_HOST": "1"}

    def sh(self, script=SCRIPT, **env):
        import subprocess

        e = {**self.env, **env}
        e = {k: v for k, v in e.items() if v is not None}
        return subprocess.run(["/bin/sh", str(script)], env=e, capture_output=True, text=True)

    def checkout(self, git=True, dirty=False):
        """A throwaway checkout holding a copy of the script (ROOT is two levels above it): a git repo with one commit, optionally with a modified tracked file."""
        import subprocess

        root = self.tmp / ("repo" if git else "nogit")
        (root / "scripts" / "research").mkdir(parents=True)
        shutil.copy(SCRIPT, root / "scripts" / "research" / "arena-h5.sh")
        (root / "tracked.txt").write_text("a\n")
        if git:
            run = lambda *a: subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a], check=True, capture_output=True)  # noqa: E731
            run("init", "-q")
            run("add", "-A")
            run("commit", "-q", "-m", "x")
            if dirty:
                (root / "tracked.txt").write_text("b\n")
        return root / "scripts" / "research" / "arena-h5.sh", root

    def test_it_is_posix_sh(self):
        import subprocess

        self.assertEqual(subprocess.run(["sh", "-n", str(SCRIPT)]).returncode, 0)
        text = SCRIPT.read_text()
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        for bashism in ("PIPESTATUS", "[[ ", "<<<", "local ", "echo -e", "source ", "declare ", "=(", "function "):
            self.assertNotIn(bashism, code, bashism)

    def test_refusals_come_before_anything_is_created(self):
        h = self.home
        for env in ({"ARENA_H5_OUT_DIR": "/tmp/x"}, {"ARENA_H5_OUT_DIR": "/srv/mal-h5-shadow"}, {"ARENA_H5_OUT_DIR": "/srv/mal-h5-shadow/h5-x"},
                    {"ARENA_H5_OUT_DIR": f"{h}/arena/h5-a/../x"}, {"ARENA_H5_OUT_DIR": f"{h}/data/arena/other"}, {"ARENA_H5_OUT_DIR": f"{h}/data/arena"},
                    {"ARENA_H5_OUT_DIR": f"{h}/data/h5-shadow"}, {"ARENA_H5_OUT_DIR": f"{h}/data/h5-shadow-2026-10-18"}, {"ARENA_H5_OUT_DIR": f"{h}/data/h5-shadow/h5-x"},
                    {"ARENA_H5_OUT_DIR": "/data/mal/arena/other"}, {"ARENA_H5_SOCKETS": "2"}, {"ARENA_H5_SOCKETS": "x"}, {"ARENA_H5_SYN_SOCKETS": "-1"},
                    {"ARENA_H5_MAX_SECONDS": "1;ls"}, {"ARENA_H5_WS_URLS": "ws://a.example/"}, {"ARENA_H5_WS_URLS": "wss://a.example/?api-key=x"},
                    {"ARENA_H5_WS_URLS": "wss://u:p@a.example/"}, {"ARENA_H5_WS_URLS": "wss://mainnet.helius-rpc.com/"}, {"ARENA_H5_WS_URLS": "wss://a.example/,http://b/"},
                    {"ARENA_H5_WS_URLS": "wss://a.example/key123"}, {"ARENA_H5_WS_URLS": "wss://a.example/v2/abcdef0123456789"}, {"ARENA_H5_WS_URLS": "wss://a.example/,wss://b.example/x"},
                    {"ARENA_H5_WS_URLS": "wss://a.example/#frag"}, {"ARENA_H5_WS_URLS": "wss://*.example/"}, {"ARENA_H5_WS_URLS": "wss://a b.example/"},
                    {"ARENA_H5_WS_URLS": "wss:///"}, {"ARENA_H5_WS_URLS": "wss://a.example//"}, {"ARENA_H5_WS_URLS": "wss://a.example,,wss://b.example"}):
            r = self.sh(**env)
            self.assertEqual(r.returncode, 2, (env, r.stderr))
            self.assertIn("refusing", r.stderr)
            self.assertNotIn("ARGV:", r.stdout)
        self.assertEqual(sorted(p.name for p in h.iterdir()), [])  # the wrapper creates nothing: the Python process makes the dir after its own check

    def test_it_runs_on_mal_fast_0_only(self):
        import socket

        if socket.gethostname() == "mal-fast-0":
            self.skipTest("this is mal-fast-0")
        r = self.sh(ARENA_H5_ALLOW_ANY_HOST=None)
        self.assertEqual(r.returncode, 6, r.stderr)
        self.assertIn("mal-fast-0", r.stderr)
        self.assertNotIn("ARGV:", r.stdout)

    def test_a_good_run_passes_the_flag_the_home_out_dir_two_endpoints_and_no_seal_or_tip_option(self):
        out = self.home / "data" / "arena" / "h5-2026-10-19"
        r = self.sh(ARENA_H5_OUT_DIR=str(out), ARENA_H5_MAX_SECONDS="120", H5_TIP_DIR="/var/lib/mal/sealed/fast-trades-tip", H5_LOOK2_OBSERVED="EXP-024-Am2",
                    H5_BX10_ENABLE="EXP-026", CAP_PICK_LIVE="/x")
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = [ln for ln in r.stdout.splitlines() if ln.startswith("ARGV:")][0][5:]
        self.assertIn("-m tools.h5_shadow", argv)
        self.assertIn("--fresh-forward ARENA", argv)
        self.assertIn(f"--out-dir {out}", argv)
        self.assertIn("--sockets 3", argv)
        self.assertIn("--ws-url wss://api.mainnet-beta.solana.com --ws-url wss://public.rpc.solanavibestation.com", argv)  # two distinct endpoints by default
        self.assertIn("--max-seconds 120", argv)
        for absent in ("--tip-dir", "--h5-look2-observed", "--bx10-enable"):
            self.assertNotIn(absent, argv)  # the old shadow's env is not forwarded
        self.assertFalse(out.exists())  # the wrapper did not create it

    def test_the_default_out_dir_is_dated_under_home_data_arena(self):
        import re

        r = self.sh()
        argv = [ln for ln in r.stdout.splitlines() if ln.startswith("ARGV:")][0][5:]
        self.assertRegex(argv, re.escape(f"--out-dir {self.home}/data/arena/h5-") + r"\d{4}-\d{2}-\d{2} ")

    def test_an_empty_endpoint_list_means_the_tools_single_default_and_a_custom_list_is_passed_through(self):
        argv = lambda r: [ln for ln in r.stdout.splitlines() if ln.startswith("ARGV:")][0][5:]  # noqa: E731
        self.assertNotIn("--ws-url", argv(self.sh(ARENA_H5_WS_URLS="")))
        self.assertIn("--ws-url wss://a.example --ws-url wss://b.example/ --ws-url wss://c.example:8900", argv(self.sh(ARENA_H5_WS_URLS="wss://a.example,wss://b.example/,wss://c.example:8900")))
        self.assertIn("set -f", SCRIPT.read_text().split("HERE=")[0])  # no pathname expansion while the list is split on commas

    def test_a_dirty_checkout_exits_5_a_clean_one_runs_and_names_its_commit(self):
        import subprocess

        script, root = self.checkout(dirty=True)
        r = self.sh(script, ARENA_H5_ALLOW_DIRTY=None)
        self.assertEqual(r.returncode, 5, r.stderr)
        self.assertIn("tracked-file changes", r.stderr)
        self.assertNotIn("ARGV:", r.stdout)
        r = self.sh(script, ARENA_H5_ALLOW_DIRTY="1")  # allowed: runs, and says so
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DIRTY:1", r.stdout)
        clean_script, clean_root = self.checkout_clean()
        r = self.sh(clean_script, ARENA_H5_ALLOW_DIRTY=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        head = subprocess.run(["git", "-C", str(clean_root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertIn(f"SHA:{head}", r.stdout)
        self.assertIn("DIRTY:\n", r.stdout)  # not set when clean

    def checkout_clean(self):
        import subprocess

        root = self.tmp / "clean"
        (root / "scripts" / "research").mkdir(parents=True)
        shutil.copy(SCRIPT, root / "scripts" / "research" / "arena-h5.sh")
        (root / "tracked.txt").write_text("a\n")
        g = lambda *a: subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a], check=True, capture_output=True)  # noqa: E731
        g("init", "-q")
        g("add", "-A")
        g("commit", "-q", "-m", "x")
        (root / "untracked.txt").write_text("u\n")  # untracked files are not changes
        return root / "scripts" / "research" / "arena-h5.sh", root

    def test_no_git_or_a_checkout_git_cannot_name_exits_7_unless_dirty_is_allowed(self):
        script, root = self.checkout(git=False)
        r = self.sh(script, ARENA_H5_ALLOW_DIRTY=None)
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertIn("cannot name the commit", r.stderr)
        self.assertNotIn("ARGV:", r.stdout)
        r = self.sh(script, ARENA_H5_ALLOW_DIRTY="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SHA:\n", r.stdout)  # empty: the start record's code_sha will be null and the scorer skips the dir
        self.assertIn("DIRTY:1", r.stdout)
        script, root = self.checkout()  # a real checkout, but git is not on the job's PATH
        bindir = self.tmp / "bin-without-git"
        bindir.mkdir()
        for tool in ("dirname", "date", "env", "hostname"):
            (bindir / tool).symlink_to(shutil.which(tool))
        r = self.sh(script, ARENA_H5_ALLOW_DIRTY=None, PATH=str(bindir))
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertNotIn("ARGV:", r.stdout)


if __name__ == "__main__":
    unittest.main()
