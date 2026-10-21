"""--q-star-sol (QP ruling 2026-10-22, s2): the trigger level is a parameter whose default is the frozen constant (40.0). Default path = byte-identical decisions."""
from __future__ import annotations

import hashlib
import json
import unittest

import asyncio
import contextlib
import io
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from tools import h5_shadow as h5
from tools.test_h5_shadow import SOL, Tape, announce, boost_buys, drain, make_engine, run, types


def decisions(q_target, **kw):
    eng, out = make_engine(**kw)
    announce(eng)
    t = Tape()
    t.row(1000, "buy", "SNIPER", SOL // 5)
    boost_buys(t, 1000)
    drain(t, 1200, q_target)
    run(eng, t)
    return out


def md5(out):
    return hashlib.md5(json.dumps(out, sort_keys=True, default=str).encode()).hexdigest()


class QStarDefaultTests(unittest.TestCase):
    def test_constant_and_default(self):
        self.assertEqual(h5.Q_STAR_SOL, 40.0)
        self.assertEqual(h5.build_parser().parse_args([]).q_star_sol, h5.Q_STAR_SOL)
        self.assertEqual(make_engine()[0].q_star_sol, 40.0)

    def test_explicit_40_is_md5_identical_to_default(self):
        for q in (30.0, 35.0, 39.999, 40.5, 45.0, 55.0):
            a, b = decisions(q), decisions(q, q_star_sol=40.0)
            self.assertEqual(md5(a), md5(b), q)
            self.assertEqual(md5(a), md5(decisions(q, q_star_sol=40)), q)  # an int 40 is the same level

    def test_default_fixture_still_triggers_as_before(self):
        self.assertEqual(len(types(decisions(39.999), "trigger")), 2)
        self.assertEqual(types(decisions(40.5), "trigger"), [])


class QStarFiftyTests(unittest.TestCase):
    def test_50_triggers_where_40_does_not(self):
        self.assertEqual(types(decisions(45.0), "trigger"), [])
        trig = types(decisions(45.0, q_star_sol=50.0), "trigger")
        self.assertEqual({r["variant"] for r in trig}, {"pv", "fv"})
        self.assertLessEqual(trig[0]["q_pv_post_sol"], 50.0)

    def test_50_does_not_trigger_above_50(self):
        self.assertEqual(types(decisions(50.5, q_star_sol=50.0), "trigger"), [])

    def test_50_still_triggers_below_40(self):
        self.assertEqual(len(types(decisions(35.0, q_star_sol=50.0), "trigger")), 2)

    def test_disagree_flag_uses_the_level(self):
        eng, out = make_engine(q_star_sol=50.0)
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        t.v -= 2 * SOL
        t.q += 2 * SOL
        drain(t, 1200, 49.0)  # pv 49.0, fv 51.0: disagree at 50
        run(eng, t)
        (trig,) = types(out, "trigger")
        self.assertEqual(trig["variant"], "pv")
        self.assertTrue(trig["pv_fv_disagree_at_trigger"])


class QStarRecordsTests(unittest.TestCase):
    def test_trigger_carries_q_star_only_off_the_default(self):
        for t in types(decisions(35.0), "trigger"):
            self.assertNotIn("q_star_sol", t)
        for t in types(decisions(35.0, q_star_sol=40.0), "trigger"):
            self.assertNotIn("q_star_sol", t)
        trig = types(decisions(45.0, q_star_sol=50.0), "trigger")
        self.assertTrue(trig and all(t["q_star_sol"] == 50.0 for t in trig))

    def test_a_q50_engine_never_writes_bx10_even_when_asked(self):
        out = decisions(45.0, q_star_sol=50.0, bx10_enabled=True, seal_start_ms=None)
        self.assertEqual(types(out, "outcome_bx10"), [])
        eng, _ = make_engine(q_star_sol=50.0, bx10_enabled=True)
        self.assertFalse(eng.bx10_enabled)
        eng, _ = make_engine(bx10_enabled=True)
        self.assertTrue(eng.bx10_enabled)  # default path unchanged


class QStarValidationTests(unittest.TestCase):
    def test_parser_accepts_exactly_40_and_50(self):
        self.assertEqual(h5.build_parser().parse_args(["--q-star-sol", "50"]).q_star_sol, 50.0)
        self.assertEqual(h5.build_parser().parse_args(["--q-star-sol=40"]).q_star_sol, 40.0)

    def test_parser_refuses_everything_else(self):
        for bad in ("0", "-1", "nan", "inf", "-inf", "abc", "", "1e999", "42.5", "45", "60", "50.0001", "0.4"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit, msg=bad):
                h5.build_parser().parse_args(["--q-star-sol", bad])

    def test_engine_refuses_everything_else(self):
        for bad in (0, -5, float("nan"), float("inf"), True, None, "50", [50], 45, 60.0):
            with self.assertRaises(ValueError, msg=repr(bad)):
                h5.Engine(lambda r: None, q_star_sol=bad)
        for ok in (40, 40.0, 50, 50.0):
            self.assertEqual(h5.Engine(lambda r: None, q_star_sol=ok).q_star_sol, float(ok))

    def test_check_q_star(self):
        self.assertEqual(h5.check_q_star(50), 50.0)
        self.assertIsInstance(h5.check_q_star(50), float)
        self.assertEqual(h5.Q_STAR_ALLOWED, (40.0, 50.0))


class QStarMainTests(unittest.TestCase):
    """main() refuses the bad combinations before anything starts; run_live refuses what main cannot see."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir="/tmp", prefix="arena-root-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        p = mock.patch.object(h5, "ARENA_ROOT", str(self.root / "arena"))
        p.start()
        self.addCleanup(p.stop)
        self.good = str(self.root / "arena" / "h5-q50-x")

    def main(self, *argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), mock.patch.object(h5, "run_live", side_effect=AssertionError("run_live must not start")), \
                mock.patch.object(h5, "run_replay", side_effect=AssertionError("replay must not start")):
            with self.assertRaises(SystemExit) as cm:
                h5.main(list(argv))
        self.assertEqual(cm.exception.code, 2)
        return err.getvalue()

    def test_q50_live_needs_fresh_forward(self):
        self.assertIn("requires --fresh-forward", self.main("--q-star-sol", "50", "--out-dir", str(self.root / "o")))
        self.assertIn("requires --fresh-forward", self.main("--q-star-sol", "50"))

    def test_q50_refuses_look2_and_bx10_enable(self):
        for extra in (["--h5-look2-observed", "EXP-024-Am2"], ["--bx10-enable", "EXP-026"]):
            self.assertIn("does not apply", self.main("--q-star-sol", "50", "--fresh-forward", "ARENA", "--out-dir", self.good, *extra))
            self.assertIn("does not apply", self.main("--q-star-sol", "50", *extra))

    def test_q50_good_call_reaches_run_live_and_40_is_unchanged(self):
        started = []

        async def fake(args):
            started.append(args.q_star_sol)
            return 0

        with mock.patch.object(h5, "run_live", fake):
            self.assertEqual(h5.main(["--q-star-sol", "50", "--fresh-forward", "ARENA", "--out-dir", self.good]), 0)
            self.assertEqual(h5.main(["--out-dir", str(self.root / "o")]), 0)
            self.assertEqual(h5.main(["--q-star-sol", "40", "--bx10-enable", "EXP-026", "--out-dir", str(self.root / "o")]), 0)
        self.assertEqual(started, [50.0, 40.0, 40.0])

    def test_q50_replay_is_allowed_on_exploration_tape(self):
        ran = []
        with mock.patch.object(h5, "run_replay", lambda args: ran.append(args.q_star_sol) or 0):
            self.assertEqual(h5.main(["--q-star-sol", "50", "--replay-tape", "/x", "--replay-hours", "2026-09-20T00"]), 0)
        self.assertEqual(ran, [50.0])

    # ---- run_live ----
    def run_live(self, extra, sha="a" * 64):
        d = self.good
        args = h5.build_parser().parse_args(["--out-dir", d, "--sockets", "3", *extra])
        built = []
        real = h5.Engine

        def spy(*a, **k):
            built.append((k.get("q_star_sol"), k.get("bx10_enabled")))
            return real(*a, **k)

        async def no_feed(*a, **k):
            return None

        with mock.patch.object(h5, "run_feed", no_feed), mock.patch.object(h5, "build_source", lambda *a, **k: None), mock.patch.object(h5, "Engine", spy), \
                mock.patch.object(h5, "Q50_RULE_SHA256", sha):
            self.assertEqual(asyncio.run(h5.run_live(args)), 0)
        rows = [json.loads(line) for f in sorted(Path(d).glob("h5-shadow-*.jsonl")) for line in f.read_text().splitlines()]
        shutil.rmtree(d, ignore_errors=True)
        return [r for r in rows if r["type"] == "start"][0], built

    def test_start_record_q50(self):
        start, built = self.run_live(["--fresh-forward", "ARENA", "--q-star-sol", "50"])
        self.assertEqual((start["rule"], start["rule_sha256"], start["q_star_sol"], start["bx10"]), (h5.Q50_RULE_ID, "a" * 64, 50.0, False))
        self.assertEqual(built, [(50.0, False)])

    def test_start_record_default_is_unchanged(self):
        start, built = self.run_live(["--fresh-forward", "ARENA"])
        self.assertEqual((start["rule"], start["rule_sha256"], start["bx10"]), (h5.RULE_ID, h5.RULE_SHA256, True))
        self.assertNotIn("q_star_sol", start)
        self.assertEqual(built, [(40.0, True)])
        start40, _ = self.run_live(["--fresh-forward", "ARENA", "--q-star-sol", "40"])
        self.assertNotIn("q_star_sol", start40)
        self.assertEqual(start40["rule"], h5.RULE_ID)

    def test_run_live_refusals(self):
        with self.assertRaisesRegex(ValueError, "requires --fresh-forward"):
            self.run_live(["--q-star-sol", "50"])
        with self.assertRaisesRegex(ValueError, "bx10"):
            self.run_live(["--fresh-forward", "ARENA", "--q-star-sol", "50", "--bx10-enable", "EXP-026"])
        with self.assertRaisesRegex(ValueError, "look2|Look-2|h5-look2"):
            self.run_live(["--fresh-forward", "ARENA", "--q-star-sol", "50", "--h5-look2-observed", "EXP-024-Am2"])
        with self.assertRaisesRegex(ValueError, "not pinned"):
            self.run_live(["--fresh-forward", "ARENA", "--q-star-sol", "50"], sha=None)


class QStarRulePinTests(unittest.TestCase):
    def test_pin_is_the_rule_file_sha_and_the_code_reads_no_file_for_it(self):
        self.assertEqual(h5.Q50_RULE_SHA256, "b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4")
        self.assertEqual(h5.Q50_RULE_ID, "H5-BOOSTFLOOR-Q50 v1")
        self.assertNotIn("RULE-Q50", __import__("inspect").getsource(h5.run_live))
        self.assertNotIn("RULE-Q50.md", Path(h5.__file__).read_text().split("Q50_RULE_SHA256", 1)[1].split("\n", 1)[1])


class QStarCompareFrozenTests(unittest.TestCase):
    def test_compare_frozen_filters_on_the_level(self):
        src = __import__("inspect").getsource(h5.compare_frozen)
        self.assertIn("f.D == q_star_sol", src)
        self.assertNotIn("40.0", src)
        self.assertEqual(__import__("inspect").signature(h5.compare_frozen).parameters["q_star_sol"].default, 40.0)


if __name__ == "__main__":
    unittest.main()
