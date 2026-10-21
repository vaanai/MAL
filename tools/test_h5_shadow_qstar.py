"""--q-star-sol: the trigger level is a parameter whose default is the frozen constant (40.0). Default path = byte-identical decisions."""
from __future__ import annotations

import hashlib
import json
import unittest

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
        # pv Q 45 / fv Q 47: both <= 50 agree; at 40 neither triggers
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


class QStarValidationTests(unittest.TestCase):
    def test_parser_accepts_positive_float(self):
        self.assertEqual(h5.build_parser().parse_args(["--q-star-sol", "50"]).q_star_sol, 50.0)
        self.assertEqual(h5.build_parser().parse_args(["--q-star-sol=42.5"]).q_star_sol, 42.5)

    def test_parser_refuses_nonsense(self):
        for bad in ("0", "-1", "nan", "inf", "-inf", "abc", "", "1e999"):
            with self.assertRaises(SystemExit, msg=bad):
                h5.build_parser().parse_args(["--q-star-sol", bad])

    def test_engine_refuses_nonsense(self):
        for bad in (0, -5, float("nan"), float("inf"), True, None, "50", [50]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                h5.Engine(lambda r: None, q_star_sol=bad)

    def test_check_q_star(self):
        self.assertEqual(h5.check_q_star(50), 50.0)
        self.assertIsInstance(h5.check_q_star(50), float)


if __name__ == "__main__":
    unittest.main()
