"""Fixtures only: no /data/mal read, no forward-1002 or forward-1002ev row. Tests of tools/boostfloor_read.py (EXP-024 Look 1 forward mode).

numpy only (pytest or `python -m unittest tools.test_boostfloor_read`). Pool fixtures reuse tools.test_boostfloor_score's POOL, shifted into
the Look 1 window. The E0-style test checks that the read tool's pricing core, in the rule's literal V0 pricing with no correction, gives
the same P&L as #476's score_pool on the same pool.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import numpy as np

from tools import boostfloor_read as br
from tools import boostfloor_score as bf
from tools import test_boostfloor_score as tb

S0 = 454_000_000
T0 = br._ts("2026-10-12T05")
GOOD = frozenset(br.READ_HOURS)
RBAR = -384_022.0  # the 10-06 faa3192 mean (DEC-021:54), a fixture value


def make_fwd(prints=tb.POOL, *, s0=S0, bt0=T0, v0=tb.V, vt=None, cls="non_synthetic", mint="M1", mbt=None) -> br.FwdPool:
    p = tb.make_pool(prints)
    path = bf.PoolPath(sl=p.sl - tb.S0 + s0, q=p.q, b=p.b, isb=p.isb, sol=p.sol, tok=p.tok, th=p.th, bt=p.bt - tb.BT0 + bt0)
    n = len(p.sl)
    vt = np.full(n, v0 if v0 == v0 else np.nan) if vt is None else np.asarray(vt, float)
    return br.FwdPool(mint=mint, pool="P" + mint, s0=s0, s0_bt=int(path.bt[0]), mbt=bt0 - 60 if mbt is None else mbt, v0=v0, v0_src="extract",
                      cls=cls, path=path, vt=vt, vt_src=["ev"] * n)


def score_rows(prints=tb.POOL):
    res = bf.score_pool(tb.make_pool(prints), tb.S0, tb.V)
    return {(r["leg"], r["stake_sol"]): r for r in res.rows}


class Constants(unittest.TestCase):
    def test_hours(self):
        self.assertEqual(len(br.READ_HOURS), 146)
        self.assertEqual((br.READ_HOURS[0], br.READ_HOURS[-1]), ("2026-10-09T23", "2026-10-16T00"))
        self.assertEqual(len(br.COUNT_HOURS), 144)
        for h in ("2026-10-09T23", "2026-10-16T00"):
            br.refuse_hour(h)
        for h in ("2026-10-09T22", "2026-10-16T01", "2026-10-03T00"):
            with self.assertRaises(br.Refused):
                br.refuse_hour(h)

    def test_cells_and_costs_are_the_prereg_values(self):
        self.assertEqual(br.CELLS, {"D": (1.9, 0.55, False), "B1": (3.0, 1.35, False), "B2": (1.9, 0.55, True)})
        self.assertEqual((br.STAKE, br.PRIO, br.EXIT_S, br.GUARD, br.FLAT_FAIL), (1e8, 55_000, 330.0, 0.15, 0.15))
        self.assertAlmostEqual(br.HAIRCUT, 1 - (1 - 0.002608) * (1 - 0.0016))
        self.assertEqual((br.LOOK1["alpha"], br.MIN_TRIGGERS, br.MIN_TRIGGER_DATES, br.SLOT_SWITCH), (0.020, 100, 5, 454_896_000))
        self.assertEqual(br.A3_FLAGS, ("pins_changed", "boost_disabled", "boost_share_low", "boost_last_slice_early", "boost_budget_or_slices_changed"))

    def test_no_cli_override(self):
        for bad in (["look", "--look", "1", "--q-star", "50"], ["look", "--look", "1", "--root", "/tmp"], ["look", "--look", "1", "--alpha", "0.05"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                br.main(bad)

    def test_look2_refused(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(br.main(["look", "--look", "2"]), 2)

    def test_forbidden_names(self):
        for bad in ("/data/mal/blocks/forward-1016/x", "/data/mal/walk2/x", "/var/lib/mal/fast-listener/helius.env", "/data/mal/h5-shadow/out"):
            with self.assertRaises(br.Refused):
                br.refuse_name(bad)
        br.refuse_name("/data/mal/exp024/look1/extract")


class Integrity(unittest.TestCase):
    def test_count_start_exactly_once(self):
        br.check_count_start("x\nEXP024_COUNT_START: 2026-10-10T00\ny\n")
        for t in ("", "EXP024_COUNT_START: 2026-10-10T00\nEXP024_COUNT_START: 2026-10-10T00\n", "EXP024_COUNT_START: 2026-10-11T00\n"):
            with self.assertRaises(br.Refused):
                br.check_count_start(t)

    def _line(self, **over):
        pins = {k: f"{br.P3_PIN_PATHS.get(k, 'tools/h5_extract.py')}@{'a' * 40}" for k in br.P3_PIN_KEYS}
        pins.update(over)
        return br.P3_PINS_PREFIX + " " + " ".join(f"{k}={v}" for k, v in pins.items())

    def test_p3_pins(self):
        pins = br.parse_p3_pins("a\n" + self._line() + "\n")
        self.assertEqual(set(pins), set(br.P3_PIN_KEYS))
        with self.assertRaises(br.Refused):
            br.parse_p3_pins("no pin line")
        with self.assertRaises(br.Refused):
            br.parse_p3_pins(self._line() + "\n" + self._line())
        with self.assertRaises(br.Refused):
            br.parse_p3_pins(self._line(read_tool=f"tools/other.py@{'a' * 40}"))
        with self.assertRaises(br.Refused):
            br.parse_p3_pins(self._line().replace(" extractor=", " extractorX="))

    def test_check_pins_blob(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "f.py").write_bytes(b"hello\n")
            br.check_pins({"x": ("f.py", "ce013625030ba8dba906f756967f9e9ca394464a")}, Path(d))  # `git hash-object` of "hello\n"
            with self.assertRaises(br.Refused):
                br.check_pins({"x": ("f.py", "0" * 40)}, Path(d))

    def test_frozen_files(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(br.Refused):
            br.check_frozen(Path(d))


class Statistics(unittest.TestCase):
    def test_t_sf_known_values(self):
        for t, df, p in ((2.015048, 5, 0.05), (2.228139, 10, 0.025), (3.364930, 5, 0.01), (0.0, 7, 0.5), (1.475884, 5, 0.1)):
            self.assertAlmostEqual(br.t_sf(t, df), p, places=5)
        self.assertAlmostEqual(br.t_sf(-2.015048, 5), 0.95, places=5)

    def test_t_sf_and_boot_match_cap_pick_score(self):
        try:
            from tools import cap_pick_score as cps
        except Exception:  # noqa: BLE001
            self.skipTest("cap_pick_score not importable here")
        for t, df in ((0.3, 2), (1.7, 4), (2.9, 26), (-1.1, 9)):
            self.assertAlmostEqual(br.t_sf(t, df), cps.t_sf(t, df), places=10)
        x = np.random.default_rng(3).normal(0.001, 0.01, 137)
        np.testing.assert_array_equal(br.boot_means(x, 1000, 1), cps.boot_means(x, 1000, 1))

    def test_day_level_by_hand(self):
        x = np.array([1.0, 3.0, 2.0, -1.0, 4.0, 0.0])
        d = ["a", "a", "b", "b", "c", "c"]
        m = np.array([2.0, 0.5, 2.0])
        t = m.mean() / (np.std(m, ddof=1) / math.sqrt(3))
        got = br.day_level(x, d)
        self.assertEqual(got["W"], 3)
        self.assertAlmostEqual(got["t"], t)
        self.assertAlmostEqual(got["p"], br.t_sf(t, 2))
        self.assertEqual(br.day_level(np.array([1.0]), ["a"])["p"], 1.0)

    def test_leg_stats_items(self):
        pnl = [2e7, -1e7, 3e7, 5e6, 1e8]
        st = br.leg_stats(pnl, ["d1", "d1", "d2", "d3", "d3"])
        self.assertEqual((st["n"], st["dates"], st["dates_pos"]), (5, 3, 3))
        self.assertAlmostEqual(st["ex_top3_sol"], (-1e7 + 5e6) / 1e9)
        self.assertAlmostEqual(st["ex_best_date_sol"], (2e7 - 1e7 + 3e7) / 1e9)
        bm = br.boot_means(np.array(pnl) / 1e9, 1000, 1)
        self.assertAlmostEqual(st["ci5_sol"], float(np.percentile(bm, 5)))

    def _cells(self, d_mean, b1_mean=0.01, b2_mean=0.01, n=120, dates=6):
        def leg(mean):
            rng = np.random.default_rng(7)
            x = (mean + rng.normal(0, 0.002, n)) * 1e9
            return br.leg_stats(list(x), [f"2026-10-1{i % dates}" for i in range(n)])
        return {"D": {"flat": leg(d_mean), "press": leg(d_mean)}, "B1": {"flat": leg(b1_mean), "press": leg(b1_mean)},
                "B2": {"flat": leg(b2_mean), "press": leg(b2_mean)}}

    def test_verdict_pass_fail_futility(self):
        self.assertEqual(br.verdict(self._cells(0.01), 0.02)["verdict"], "PASS")
        self.assertEqual(br.verdict(self._cells(0.01, b2_mean=-0.01), 0.02)["verdict"], "FAIL")  # a binding leg only removes a pass
        self.assertEqual(br.verdict(self._cells(-0.01), 0.02)["verdict"], "FAIL_FUTILITY")
        self.assertEqual(br.verdict(self._cells(0.01, n=60), 0.02)["verdict"], "FAIL")  # n < 100
        self.assertEqual(br.verdict(self._cells(0.01, dates=4), 0.02)["verdict"], "FAIL")  # < 5 dates


class Pricing(unittest.TestCase):
    def setUp(self):
        self.p = make_fwd()
        self.s = br.structure(self.p, GOOD)
        self.assertEqual(self.s.status, "trigger")

    def test_e0_literal_v0_no_correction_equals_score_pool(self):
        ref = score_rows()
        for leg, cell in (("primary", br.REPORT_LEGS["R_rule_1p3"]), ("binding", br.CELLS["D"])):
            for label, stake in bf.STAKES_LAMPORTS:
                got = br.price_cell(self.p, self.s, cell, rbar=RBAR, stake=stake, literal_v0=True, corr="none")
                self.assertEqual(round(got["pnl_lamports"]), round(ref[(leg, label)]["pnl_nofail_lamports"]))
                self.assertAlmostEqual(got["pnl_lamports"], ref[(leg, label)]["pnl_nofail_lamports"], places=4)
                self.assertEqual((got["ssb"], got["nb_lamports"]), (ref[(leg, label)]["ssb"], ref[(leg, label)]["nb_lamports"]))

    def test_vt_equal_v0_gives_literal(self):
        a = br.price_cell(self.p, self.s, br.CELLS["D"], rbar=RBAR, corr="none")
        b = br.price_cell(self.p, self.s, br.CELLS["D"], rbar=RBAR, literal_v0=True, corr="none")
        self.assertAlmostEqual(a["pnl_lamports"], b["pnl_lamports"], places=6)
        self.assertFalse(a["v_missing"])

    def test_vt_at_exit_is_used(self):
        lower = make_fwd(vt=[tb.V] * 11 + [tb.V - 0.5e9] * 2)  # prints at +828 and +1000 carry a lower V(t)
        s = br.structure(lower, GOOD)
        a = br.price_cell(lower, s, br.CELLS["D"], rbar=RBAR, corr="none")
        b = br.price_cell(self.p, self.s, br.CELLS["D"], rbar=RBAR, corr="none")
        self.assertLess(a["pnl_lamports"], b["pnl_lamports"])
        self.assertAlmostEqual(a["v_exit_minus_v0"], -0.5e9)
        self.assertEqual(a["v_entry_minus_v0"], 0.0)

    def test_missing_vt_takes_the_lower_case(self):
        vt = [tb.V] * 13
        vt[11] = np.nan  # the exit state is the pre-trade state of the print at +828 (index 11)
        p = make_fwd(vt=vt)
        s = br.structure(p, GOOD)
        got = br.price_cell(p, s, br.CELLS["D"], rbar=RBAR, corr="none")
        self.assertTrue(got["v_missing"])
        qx_t = p.path.q[11]
        want_vx = tb.V - 0.0383 * (qx_t + tb.V) / 1.0383
        self.assertAlmostEqual(got["v_exit_minus_v0"], want_vx - tb.V, places=3)

    def test_unknown_v0_is_the_lower_of_17p5_and_17p7(self):
        p = make_fwd(v0=float("nan"))
        s = br.structure(p, GOOD)
        self.assertTrue(s.v0_unknown)
        self.assertEqual(sorted(t.v0 for t in s.trigs), [17.5e9, 17.7e9])
        got = br.price_cell(p, s, br.CELLS["D"], rbar=RBAR, corr="none")
        each = []
        for v0 in (17.5e9, 17.7e9):
            q = make_fwd(v0=v0, vt=[np.nan] * 13)  # no source has V0, so none has V(t) either: the section 4 cases apply too
            each.append(br.price_cell(q, br.structure(q, GOOD), br.CELLS["D"], rbar=RBAR, corr="none")["pnl_lamports"])
        self.assertAlmostEqual(got["pnl_lamports"], min(each), places=6)
        self.assertTrue(got["v_missing"])

    def test_correction_is_the_larger_of_a_and_b(self):
        self.assertAlmostEqual(br.correction(1e8, 1e8, RBAR), 768_044.0)  # (b) = -2 r-bar at 0.1 SOL
        self.assertAlmostEqual(br.correction(1e8, 1e8, 0.0), 1e8 * br.HAIRCUT)  # r-bar >= 0: (b) = 0
        self.assertAlmostEqual(br.correction(1e8, 0.05e9, RBAR, "b"), 384_022.0)
        base = br.price_cell(self.p, self.s, br.CELLS["D"], rbar=RBAR, corr="none")
        cor = br.price_cell(self.p, self.s, br.CELLS["D"], rbar=RBAR)
        self.assertAlmostEqual(base["pnl_lamports"] - cor["pnl_lamports"], cor["correction_lamports"])
        self.assertGreaterEqual(cor["correction_lamports"], 768_044.0)

    def test_b1_lands_later_and_sells_later(self):
        d = br.price_cell(self.p, self.s, br.CELLS["D"], rbar=RBAR)
        b1 = br.price_cell(self.p, self.s, br.CELLS["B1"], rbar=RBAR)
        self.assertEqual(d["landing_slot"] - S0, 40 + 5)  # ceil(1.9 / 0.4)
        self.assertEqual(b1["landing_slot"] - S0, 40 + 8)  # ceil(3.0 / 0.4)
        self.assertEqual(d["exit_landing_slot"] - S0, 825 + 2)
        self.assertEqual(b1["exit_landing_slot"] - S0, 825 + 4)  # ceil(1.35 / 0.4)

    def test_b2_guard_fills_and_reverts(self):
        ok = br.price_cell(self.p, self.s, br.CELLS["B2"], rbar=RBAR)
        self.assertFalse(ok["reverted"])
        self.assertAlmostEqual(ok["pnl_lamports"], br.price_cell(self.p, self.s, br.CELLS["D"], rbar=RBAR)["pnl_lamports"])
        jump = list(tb.POOL)
        jump[8] = (47, False, 1.0, 28.0, 105)  # landing state: Q 28 + V against the trigger's 20 + V, about 21% dearer
        p = make_fwd(jump)
        s = br.structure(p, GOOD)
        rev = br.price_cell(p, s, br.CELLS["B2"], rbar=RBAR)
        self.assertTrue(rev["reverted"])
        rows = [dict(rev, mint="M", date="d")]
        br.apply_fail(rows)
        self.assertEqual((rows[0]["pnl_flat_lamports"], rows[0]["pnl_press_lamports"]), (-55_000.0, -55_000.0))
        self.assertFalse(br.price_cell(p, s, br.CELLS["D"], rbar=RBAR)["reverted"])


class Structure(unittest.TestCase):
    def test_statuses(self):
        self.assertEqual(br.structure(make_fwd(bt0=br._ts("2026-10-16T00")), GOOD).status, "outside")
        self.assertEqual(br.structure(make_fwd(bt0=br._ts("2026-10-09T23")), GOOD).status, "outside")
        self.assertEqual(br.structure(make_fwd(v0=17.8e9), GOOD).status, "v_range")
        self.assertEqual(br.structure(make_fwd(cls="synthetic"), GOOD).status, "synthetic")
        self.assertEqual(br.structure(make_fwd(cls="unclassified"), GOOD).status, "unclassified")
        self.assertEqual(br.structure(make_fwd(), GOOD - {"2026-10-12T05"}).status, "coverage")
        self.assertEqual(br.structure(make_fwd(mbt=br._ts("2026-10-09T22")), GOOD).status, "coverage")  # `complete` before the read hours
        self.assertEqual(br.structure(make_fwd(s0=br.SLOT_SWITCH - 100), GOOD).status, "slot_switch")
        self.assertEqual(br.structure(make_fwd(s0=br.SLOT_SWITCH), GOOD).status, "trigger")

    def test_classify_from(self):
        self.assertEqual(br.classify_from(True, "non_synthetic"), "synthetic")  # the tape can mark synthetic
        self.assertEqual(br.classify_from(False, None), "unclassified")  # the tape never settles non-synthetic
        self.assertEqual(br.classify_from(False, "non_synthetic"), "non_synthetic")
        self.assertEqual(br.classify_from(False, "unclassified"), "unclassified")

    def test_vsources_order(self):
        k = (1, "s", 0)
        vs = br.VSources(ev={k: 10}, gettx={k: 20, (2, "t", 0): 30}, account_v0={"P": 40})
        self.assertEqual(vs.v(k), (10.0, "ev"))
        self.assertEqual(vs.v((2, "t", 0)), (30.0, "gettx"))
        self.assertEqual(vs.v0((3, "u", 0), "P"), (40.0, "account"))
        self.assertEqual(vs.v((3, "u", 0))[1], "none")  # the account never gives V(t) at a past print
        self.assertEqual(br.VSources(ev=None, gettx={k: 20}, account_v0={}).v(k), (20.0, "gettx"))  # line A failed


def _pools(n_per_date=20, dates=6):
    out = []
    for d in range(dates):
        for j in range(n_per_date):
            out.append(make_fwd(bt0=br._ts(f"2026-10-{10 + d}T{j % 24:02d}") + 60, mbt=br._ts(f"2026-10-{10 + d}T{j % 24:02d}"), mint=f"M{d}_{j}",
                                s0=S0 + 10_000 * (d * 100 + j)))
    return out


class Precount(unittest.TestCase):
    def test_counts_only_never_prices(self):
        pools = _pools(3, 2) + [make_fwd(cls="synthetic", mint="S"), make_fwd(cls="unclassified", mint="U"), make_fwd(v0=float("nan"), mint="N")]
        with mock.patch.object(br, "round_trip", side_effect=AssertionError("priced")), mock.patch.object(br, "price_cell", side_effect=AssertionError("priced")):
            pc = br.precount(pools, GOOD, [])
        self.assertEqual(pc["triggers"], 7)
        self.assertEqual(pc["trigger_dates"], 3)
        self.assertEqual(pc["totals"]["synthetic"], 1)
        self.assertEqual(pc["totals"]["unclassified"], 1)
        self.assertEqual(pc["totals"]["null_v0"], 1)
        self.assertEqual(pc["vrange_pools_in_window"], 9)
        self.assertTrue(pc["refuse_p6"])
        self.assertAlmostEqual(pc["unclassified_share"], 1 / 9)


def _a3_rec(day, **flags):
    f = {k: {"halt": False, "evaluated": True} for k in br.A3_FLAGS + ("synthetic_share_high",)}
    for k, v in flags.items():
        f[k] = v
    return {"run_utc": f"{day}T06:41:00Z", "halt": {"flags": f}, "boost": {"last_slice_after_migrate_s": {"median": 345.0}}}


class Conditions(unittest.TestCase):
    def _a3(self, recs):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "daily.jsonl"
            p.write_text("".join(json.dumps(r) + "\n" for r in recs))
            return br.a3_conditions(p, lo="2026-10-10T00", run_date="2026-10-16")

    def test_a3(self):
        days = [f"2026-10-{d}" for d in range(10, 17)]
        a = self._a3([_a3_rec(d) for d in days] + [_a3_rec("2026-10-09", pins_changed={"halt": True, "evaluated": True})])
        self.assertEqual((a["halts"], a["unevaluated_two_runs"], a["last_slice_median_s"]), ([], [], 345.0))
        a = self._a3([_a3_rec(d) for d in days[:-1]] + [_a3_rec(days[-1], synthetic_share_high={"halt": True, "evaluated": True})])
        self.assertEqual(a["halts"], [])  # Am.4 C1: the sixth flag never decides
        a = self._a3([_a3_rec(d) for d in days[:3]] + [_a3_rec(days[3], boost_disabled={"halt": True})] + [_a3_rec(d) for d in days[4:]])
        self.assertEqual(len(a["halts"]), 1)
        ne = {"halt": False, "evaluated": False}
        a = self._a3([_a3_rec(d) for d in days[:2]] + [_a3_rec(days[2], boost_share_low=ne), _a3_rec(days[3], boost_share_low=ne)] + [_a3_rec(d) for d in days[4:]])
        self.assertEqual(len(a["unevaluated_two_runs"]), 1)
        with self.assertRaises(br.Refused):
            self._a3([_a3_rec(d) for d in days[:-1]])  # no 10-16 run yet

    def test_e1(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps({"aggregate": {"faa3192": {"pnl_gap_lamports_live_minus_sim": {"n": 19, "mean": -1.0}}}}))
            with self.assertRaises(br.Refused):
                br.read_e1(p)
            p.write_text(json.dumps({"aggregate": {"faa3192": {"pnl_gap_lamports_live_minus_sim": {"n": 20, "mean": -1.0}}}}))
            self.assertEqual(br.read_e1(p), (20, -1.0))

    def test_not_decidable_reasons(self):
        pc = {"refuse_p6": False, "triggers": 120, "trigger_dates": 6, "unclassified_share": 0.0, "unclassified": 0, "vrange_pools_in_window": 300,
              "bad_hour_share": 0.0, "bad_count_hours": 0, "count_hours": 144}
        a3 = {"halts": [], "unevaluated_two_runs": [], "last_slice_median_s": 340.0}
        self.assertEqual(br.not_decidable_reasons(pc, a3, True), [])
        self.assertEqual(len(br.not_decidable_reasons({**pc, "unclassified_share": 0.011}, a3, True)), 1)
        self.assertEqual(len(br.not_decidable_reasons({**pc, "bad_hour_share": 8 / 144}, a3, True)), 1)
        self.assertEqual(len(br.not_decidable_reasons(pc, {**a3, "last_slice_median_s": 329.9}, True)), 1)
        self.assertEqual(len(br.not_decidable_reasons(pc, a3, False)), 1)

    def test_missing_v_check(self):
        rows = [dict(mint=f"m{i}", v_missing=False, pnl_flat_lamports=float(i), pnl_press_lamports=float(i)) for i in range(200)]
        cells = {"D": rows, "B1": list(rows), "B2": list(rows)}
        self.assertEqual(br.missing_v_check(cells), [])
        top = [dict(r) for r in rows]
        top[199]["v_missing"] = True
        self.assertTrue(br.missing_v_check({"D": top, "B1": rows, "B2": rows}))
        many = [dict(r, v_missing=i < 3) for i, r in enumerate(rows)]  # 3 of 200 > 1%, none in the top 3
        self.assertTrue(br.missing_v_check({"D": many, "B1": rows, "B2": rows}))


class LockAndLedger(unittest.TestCase):
    def _inputs(self, pools, **kw):
        a3 = {"halts": [], "unevaluated_two_runs": [], "last_slice_median_s": 340.0, "runs": 7, "synthetic_share_high": []}
        a3.update(kw.pop("a3", {}))
        return br.LookInputs(pools, GOOD, [], RBAR, 25, a3, kw.pop("p7", True), None)

    def _run(self, lay, inp, now=datetime(2026, 10, 16, 8, tzinfo=timezone.utc)):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = br.run_look(lay, lambda: inp, ident={"head": "x"}, now=now, log=lambda s: print(s, file=__import__("sys").stderr))
        return rc, err.getvalue()

    def test_once_then_refused(self):
        with tempfile.TemporaryDirectory() as d:
            lay = br.Layout(Path(d), 1)
            rc, err = self._run(lay, self._inputs(_pools(20, 6)))
            self.assertEqual(rc, 0)
            self.assertIn("VERDICT", err)
            ev = br.ledger_events(lay.ledger)
            self.assertEqual([e["event"] for e in ev], ["started", "completed"])
            rep = json.loads((lay.out / "report.json").read_text())
            self.assertTrue(rep["outcomes_computed"])
            self.assertEqual(rep["n_rows"]["D"], 120)
            self.assertIn("observed in real time", rep["disclosure"])
            self.assertTrue((lay.out / "rows.csv").is_file())
            with self.assertRaises(br.Refused):
                self._run(lay, self._inputs(_pools(20, 6)))
            lay.ledger.unlink()
            with self.assertRaises(br.Refused):  # the O_EXCL lock still holds
                self._run(lay, self._inputs(_pools(20, 6)))

    def test_not_decidable_before_outcomes(self):
        with tempfile.TemporaryDirectory() as d:
            lay = br.Layout(Path(d), 1)
            with mock.patch.object(br, "price_cell", side_effect=AssertionError("priced")):
                rc, err = self._run(lay, self._inputs(_pools(20, 6), p7=False))
            rep = json.loads((lay.out / "report.json").read_text())
            self.assertEqual((rep["verdict"], rep["outcomes_computed"]), ("NOT_DECIDABLE", False))
            self.assertFalse((lay.out / "rows.csv").exists())
            self.assertEqual(br.ledger_events(lay.ledger)[-1]["verdict"], "NOT_DECIDABLE")

    def test_aborted_is_logged(self):
        with tempfile.TemporaryDirectory() as d:
            lay = br.Layout(Path(d), 1)
            with mock.patch.object(br, "compute_look", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
                self._run(lay, self._inputs([]))
            self.assertEqual([e["event"] for e in br.ledger_events(lay.ledger)], ["started", "aborted"])

    def test_deadline(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(br.Refused):
            self._run(br.Layout(Path(d), 1), self._inputs([]), now=datetime(2026, 10, 17, 12, tzinfo=timezone.utc))

    def test_precount_must_match(self):
        with tempfile.TemporaryDirectory() as d:
            inp = self._inputs(_pools(20, 6))
            inp.precount_file = {"triggers": 1}
            with self.assertRaises(br.Refused):
                self._run(br.Layout(Path(d), 1), inp)

    def test_ledger_allows(self):
        br.ledger_allows([], 1)
        with self.assertRaises(br.Refused):
            br.ledger_allows([{"experiment": "EXP-024", "look": 1, "event": "started"}], 1)
        with self.assertRaises(br.Refused):
            br.ledger_allows([{"experiment": "EXP-024", "look": 1, "event": "completed", "verdict": "PASS"}], 2)
        br.ledger_allows([{"experiment": "EXP-022", "look": 1, "event": "started"}], 1)


class E0AndClassify(unittest.TestCase):
    def test_e0_rows_equal_score_pool_to_the_lamport(self):
        path = tb.make_pool(tb.POOL)
        got = br.e0_rows([("M1", "fast-pool-0918", tb.S0, tb.V, path)])
        ref = [("M1", r["leg"], f"{float(r['stake_sol']):g}", int(round(r["pnl_nofail_lamports"])))
               for r in bf.score_pool(path, tb.S0, tb.V).rows]
        self.assertEqual(sorted(got), sorted(ref))
        self.assertEqual(len(got), 4)
        self.assertEqual(br.e0_md5(got), br.e0_md5(list(reversed(ref))))
        self.assertEqual(br.e0_rows([("M2", "x", tb.S0, tb.V, tb.make_pool(tb.POOL[:5] + [(1000, False, 0.1, 15.0, 109)]))]), [])

    def test_report_only_legs_are_not_deciding(self):
        self.assertTrue({"R_exit_340", "R_stake_0p25", "D_literal_v0", "D_only_a", "D_only_b"} <= set(br.REPORT_ONLY))
        self.assertFalse(set(br.REPORT_ONLY) & set(br.CELLS))
        p = make_fwd()
        s = br.structure(p, GOOD)
        a = br.price_cell(p, s, (1.9, 0.55, False), rbar=RBAR, exit_s=340.0)
        self.assertEqual(a["exit_landing_slot"] - S0, 850 + 2)  # round(340 / 0.4) + ceil(0.55 / 0.4)

    def test_classify_pools(self):
        calls = []

        def fake(rpc, **kw):
            calls.append(kw["pool"])
            if kw["pool"] == "PE":
                raise RuntimeError("rpc down")
            return {"class": "non_synthetic", "reason": "ok"}

        pools = [make_fwd(mint="A"), make_fwd(mint="T", cls="synthetic"), make_fwd(mint="E"),
                 make_fwd(mint="O", bt0=br._ts("2026-10-16T00")), make_fwd(mint="R", v0=17.9e9), make_fwd(mint="N", v0=float("nan"))]
        recs = {r["mint"]: r for r in br.classify_pools(object(), pools, classify=fake)}
        self.assertEqual(set(recs), {"A", "T", "E", "N"})  # outside the window and out of V range are not classified
        self.assertEqual((recs["T"]["class"], recs["E"]["class"], recs["A"]["class"]), ("synthetic", "unclassified", "non_synthetic"))
        self.assertNotIn("PT", calls)  # the tape already marked it synthetic

    def test_look_report_has_no_live_and_report_only(self):
        with tempfile.TemporaryDirectory() as d:
            lay = br.Layout(Path(d), 1)
            inp = LockAndLedger()._inputs(_pools(20, 6))
            with contextlib.redirect_stderr(io.StringIO()):
                br.run_look(lay, lambda: inp, ident={}, now=datetime(2026, 10, 16, 8, tzinfo=timezone.utc), log=lambda s: None)
            rep = json.loads((lay.out / "report.json").read_text())
            self.assertIn("R_exit_340", rep["report_only"])
            self.assertIn("winsor100_mean_sol", rep["report_only"]["D"]["flat"])
            self.assertIsInstance(rep["no_live"], list)
            self.assertEqual(len(rep["not_computed_section13"]), len(br.NOT_COMPUTED))


if __name__ == "__main__":
    unittest.main()
