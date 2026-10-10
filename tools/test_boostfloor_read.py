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

from tools import boostfloor_inputs as bi
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


# ---- extractor contract (#562), content keys, producers (tools/boostfloor_inputs.py), section 13 --------------------------------


def keyed(p):
    p.keys = [br.content_key(*t) for t in zip(p.path.sl, [p.mint] * len(p.path.sl), p.path.sol, p.path.tok, p.path.q, p.path.b)]
    return p


def index_of(pools, ev=True, drop=()):
    return {k: br.PrintRef(k[0], f"sig-{k[1]}-{k[0]}-{i}", 0, int(tb.V) if ev and k not in drop else None)
            for p in pools for i, k in enumerate(p.keys)}


def write_extract(lay, pools, extra_col=None):
    import pandas as pd

    day = "2026-10-12"
    meta = pd.DataFrame([dict(mint=p.mint, pool=p.pool, mslot=p.s0 - 5, mbt=p.mbt, s0=p.s0, v=p.v0, npools=1, blk="forward-1002", cslot=None,
                              day=day, uncensored=True) for p in pools], columns=list(br.EXTRACT_META_COLUMNS))
    rows = []
    for p in pools:
        for i in range(len(p.path.sl)):
            rows.append(dict(mint=p.mint, slot=int(p.path.sl[i]), isbuy=bool(p.path.isb[i]), sol=float(p.path.sol[i]), tok=float(p.path.tok[i]),
                             q=float(p.path.q[i]), b=float(p.path.b[i]), th=np.uint64(p.path.th[i]), bt=int(p.path.bt[i])))
    paths = pd.DataFrame(rows, columns=list(br.EXTRACT_PATHS_COLUMNS))
    if extra_col:
        paths[extra_col] = "x"
    for part, df in (("meta", meta), ("paths", paths)):
        (lay.extract / part).mkdir(parents=True, exist_ok=True)
        df.to_parquet(lay.extract / part / f"{day}.parquet", index=False)


class ExtractorContract(unittest.TestCase):
    def test_columns_are_562s(self):
        # tools/h5_forward_extract.py (#562, f71a420): CREATE TABLE meta AS SELECT m.mint, cp1.pool, m.mslot, m.mbt, cp1.s0, cp1.v, cp1.npools,
        # m.blk, cr.cslot, day, uncensored; paths: t.mint, t.slot, isbuy, sol, tok, q, b, th, bt (no signature, no event_index)
        self.assertEqual(br.EXTRACT_META_COLUMNS, ("mint", "pool", "mslot", "mbt", "s0", "v", "npools", "blk", "cslot", "day", "uncensored"))
        self.assertEqual(br.EXTRACT_PATHS_COLUMNS, ("mint", "slot", "isbuy", "sol", "tok", "q", "b", "th", "bt"))

    def test_load_pools_on_562_columns_with_content_keyed_v(self):
        with tempfile.TemporaryDirectory() as d:
            lay = br.Layout(Path(d), 1)
            p = keyed(make_fwd())
            write_extract(lay, [p])
            ev = {k: 17_600_000_000 + i for i, k in enumerate(p.keys)}
            got = br.load_pools(lay, br.VSources(ev, {}, {}), {"PM1": "non_synthetic"})
            self.assertEqual(len(got), 1)
            g = got[0]
            self.assertEqual(g.keys, p.keys)
            self.assertEqual((g.v0, g.v0_src), (17.6e9, "ev"))  # Am.1: the s0 print's ev V comes before the extractor's vmap V0
            self.assertTrue(np.array_equal(g.vt, np.array([ev[k] for k in p.keys], float)))
            self.assertEqual(g.cls, "non_synthetic")
            g2 = br.load_pools(lay, br.VSources(None, {}, {}), {})[0]
            self.assertEqual((g2.v0, g2.v0_src, g2.cls), (tb.V, "extract_vmap", "unclassified"))
            self.assertTrue(np.isnan(g2.vt).all())

    def test_refuses_other_columns(self):
        with tempfile.TemporaryDirectory() as d:
            lay = br.Layout(Path(d), 1)
            write_extract(lay, [keyed(make_fwd())], extra_col="sig")
            with self.assertRaises(br.Refused):
                br.load_pools(lay, br.VSources(None, {}, {}), {})

    def test_index_prints(self):
        from tools import forward_v_join as J

        base = dict(venue="pumpswap", mint="M", sol_lamports=5, token_raw=7, quote_reserve=11, base_reserve=13, event_index=0)
        hours = {"2026-10-12T05": [dict(base, slot=1, signature="a", virtual_quote_reserves=17), dict(base, slot=2, signature="b"),
                                   dict(base, slot=3, signature="c"), dict(base, slot=3, signature="d"),  # same content: ambiguous
                                   dict(base, slot=4, signature="e", mint="X"), dict(base, slot=5, signature="f", venue="pump")]}

        def rows_of(h):
            if h not in hours:
                raise J.HourReadError("base_not_walked")
            yield from hours[h]

        idx, skipped = br.index_prints(Path("/data/mal/blocks/forward-1002"), Path("/data/mal/exp024/p5/vjoin"), {"M"},
                                       hours=("2026-10-12T05", "2026-10-12T06"), rows_of=rows_of)
        self.assertEqual(skipped, ["2026-10-12T06"])
        self.assertEqual(len(idx), 3)
        self.assertEqual(idx[(1, "M", 5, 7, 11, 13)], br.PrintRef(1, "a", 0, 17))
        self.assertIsNone(idx[(2, "M", 5, 7, 11, 13)].ev_v)
        self.assertIsNone(idx[(3, "M", 5, 7, 11, 13)].signature)
        self.assertEqual(br.ev_map(idx), {(1, "M", 5, 7, 11, 13): 17})
        with self.assertRaises(br.Refused):
            br.index_prints(Path("/data/mal/blocks/forward-1016"), Path("/x"), {"M"}, hours=("2026-10-12T05",), rows_of=rows_of)
        with self.assertRaises(br.Refused):
            br.index_prints(Path("/b"), Path("/x"), {"M"}, hours=("2026-10-16T01",), rows_of=rows_of)

    def test_load_gettx_needs_ok_and_equal_fields(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "g.jsonl"
            recs = [{"key": [1, "M", 5, 7, 11, 13], "status": "ok", "fields_equal": True, "decoded": {"virtual_quote_reserves": 9}},
                    {"key": [2, "M", 5, 7, 11, 13], "status": "ok", "fields_equal": False, "decoded": {"virtual_quote_reserves": 9}},
                    {"key": [3, "M", 5, 7, 11, 13], "status": "absent"}]
            f.write_text("".join(json.dumps(r) + "\n" for r in recs))
            self.assertEqual(br.load_gettx(f), {(1, "M", 5, 7, 11, 13): 9})


class FakeCurve:
    def __init__(self, p):
        self._p = p

    def p(self, _pressure):
        return self._p


class Section13(unittest.TestCase):
    def test_start_bound_reads_no_later_state(self):
        p = make_fwd()
        s = br.structure(p, GOOD)
        te, ts = set(), set()
        br.price_cell(p, s, br.CELLS["D"], rbar=RBAR, touch=te)
        br.price_cell(p, s, br.CELLS["D"], rbar=RBAR, bound="start", touch=ts)
        self.assertTrue(te and ts)
        self.assertLessEqual(max(ts), max(te))
        self.assertIsNotNone(br.price_cell(p, s, br.CELLS["D"], rbar=RBAR, bound="start"))

    def test_touch_prices_nothing(self):
        p = make_fwd()
        s = br.structure(p, GOOD)
        with mock.patch.object(br, "round_trip", side_effect=AssertionError("priced")):
            self.assertIsNone(br.price_cell(p, s, br.CELLS["B2"], rbar=RBAR, touch=set()))

    def test_sell_retry(self):
        p = make_fwd()
        s = br.structure(p, GOOD)
        d = br.price_cell(p, s, br.CELLS["D"], rbar=RBAR)
        r0 = br.sell_retry(p, s, FakeCurve(0.0), RBAR)
        self.assertAlmostEqual(r0["pnl_lamports"], d["pnl_lamports"])
        self.assertAlmostEqual(r0["sell_attempts_expected"], 1.0)
        step = math.ceil(2.0 / s.sps - 1e-9)
        last = br.price_cell(p, s, br.CELLS["D"], rbar=RBAR, sell_delay_slots=(br.SELL_RETRY_MAX - 1) * step)
        r1 = br.sell_retry(p, s, FakeCurve(1.0), RBAR)  # every attempt fails until the last, priced there
        self.assertAlmostEqual(r1["pnl_lamports"], last["pnl_lamports"] - (br.SELL_RETRY_MAX - 1) * br.PRIO)
        self.assertAlmostEqual(r1["sell_attempts_expected"], br.SELL_RETRY_MAX)

    def test_post_boost_control(self):
        p = make_fwd()
        s = br.structure(p, GOOD)
        self.assertEqual(s.status, "trigger")
        i = s.trigs[0].i
        t = (p.path.sl[i] - p.s0) * s.sps
        q = make_fwd()
        q.s0 -= int(round((450 - t) / s.sps))  # the same prints, 450 - t seconds later after s0
        sq = br.structure(q, GOOD)
        c = br.control_struct(q, sq, GOOD)
        self.assertIsNotNone(c)
        tc = (q.path.sl[c.trigs[0].i] - q.s0) * sq.sps
        self.assertTrue(360 <= tc <= 600)
        self.assertLessEqual(c.trigs[0].i, i)
        self.assertIsNone(br.control_struct(q, sq, GOOD - {"2026-10-12T05"}))  # coverage to the control's exit
        r = br.price_cell(q, c, br.CELLS["D"], rbar=RBAR, exit_s=br.CONTROL_EXIT_S)
        self.assertEqual(r["exit_landing_slot"] - q.s0, round(840 / sq.sps) + math.ceil(0.55 / sq.sps - 1e-9))

    def test_detector_vs_pda_and_mechanism(self):
        p = make_fwd(mint="A")
        s = br.structure(p, GOOD)
        self.assertEqual(br.detector_vs_pda(p), "pda_unknown")
        det = bf.detect_boost_wallet(p.path.sl, p.s0, p.path.th, np.asarray(p.path.sol, float), p.path.isb)
        mine = sorted(int(x) for x in p.path.sl[(p.path.th == np.uint64(det)) & p.path.isb]) if det is not None else []
        p.pda = {"slice_slots": mine + [p.s0 - 1]}
        self.assertEqual(br.detector_vs_pda(p), "agree" if det is not None else "pda_only")
        if det is not None:
            p.pda = {"slice_slots": mine[1:]}
            self.assertEqual(br.detector_vs_pda(p), "disagree")
        d = br.price_cell(p, s, br.CELLS["D"], rbar=RBAR)
        exit_bt = p.s0_bt + (d["exit_landing_slot"] - p.s0) * d["sps"]
        pools, rows = {}, []
        for m, pda in (("E", {"last_bt": exit_bt - 5, "slice_slots": []}), ("R", {"last_bt": exit_bt + 5, "slice_slots": []}),
                       ("Z", {"no_slices": True, "last_bt": None, "slice_slots": []}), ("U", None), ("F", {"error": "RpcError"})):
            q = make_fwd(mint=m)
            q.pda = pda
            pools[m] = q
            rows.append({**d, "mint": m, "s0_bt": q.s0_bt, "pnl_flat_lamports": 1e6, "pnl_press_lamports": 2e6})
        mech = br.mechanism(rows, pools)
        self.assertEqual((mech["ended_before_exit"], mech["running_at_exit"], mech["unknown"]), (2, 1, 2))
        self.assertAlmostEqual(mech["share_ended_known"], 2 / 3)
        self.assertAlmostEqual(mech["share_ended_upper"], 4 / 5)
        self.assertAlmostEqual(mech["group_mean_sol"]["ended"]["flat"], 0.001)

    def test_look_has_section13_rows_and_no_not_computed(self):
        with tempfile.TemporaryDirectory() as d:
            lay = br.Layout(Path(d), 1)
            inp = LockAndLedger()._inputs(_pools(20, 6))
            with contextlib.redirect_stderr(io.StringIO()):
                br.run_look(lay, lambda: inp, ident={}, now=datetime(2026, 10, 16, 8, tzinfo=timezone.utc), log=lambda s: None)
            rep = json.loads((lay.out / "report.json").read_text())
            for k in ("D_start", "D_worse_start_end", "D_sell_retry"):
                self.assertEqual(rep["report_only"][k]["flat"]["n"], 120)
            self.assertLessEqual(rep["report_only"]["D_worse_start_end"]["flat"]["mean_sol"], rep["report_only"]["D"]["flat"]["mean_sol"] + 1e-12)
            self.assertEqual(rep["not_computed_section13"], [])
            self.assertEqual(rep["mechanism"]["unknown"], 120)  # no PDA file: every pool unknown, so no live support
            self.assertTrue(any("BOOST ended" in x for x in rep["no_live"]))
            self.assertEqual(rep["decision"], json.loads(json.dumps(rep["decision"])))


def _decoded_for(k, v, side="sell", **kw):
    d = dict(side=side, ix_name="sell" if side == "sell" else "buy", sol_lamports=k[2], token_raw=k[3], quote_reserve=k[4],
             base_reserve=k[5], virtual_quote_reserves=v, pool_quote_amount=None, zero_sol=None, pool="P", mint=k[1])
    d.update(kw)
    return d


class Producers(unittest.TestCase):
    def _setup(self, ev=True, drop=()):
        pools = [keyed(p) for p in _pools(3, 2)]
        return pools, index_of(pools, ev, drop)

    def test_plan_is_outcome_blind_and_deterministic(self):
        pools, idx = self._setup()
        with mock.patch.object(br, "round_trip", side_effect=AssertionError("priced")):
            a = bi.plan_p5(pools, idx, GOOD)
            b = bi.plan_p5(list(reversed(pools)), idx, GOOD)
        self.assertEqual((a["line_a"], a["p7"]), (b["line_a"], b["p7"]))
        self.assertEqual(a["triggers"], 6)
        self.assertTrue(all(p.keys[0] in a["needed"] for p in pools))
        self.assertLessEqual(len(a["line_a"]), 3 * 6)
        n = sum(len(p.keys) for p in pools)
        self.assertEqual(len(a["p7"]), min(1000, n))
        self.assertEqual(len(set(k for k, _ in a["p7"])), min(1000, n))

    def test_line_a(self):
        pools, idx = self._setup()
        sample = bi.plan_p5(pools, idx, GOOD)["line_a"]
        recs = {k: {"status": "ok", "decoded": _decoded_for(k, int(tb.V))} for k in sample}
        self.assertTrue(bi.line_a(sample, idx, recs)["line_a_pass"])
        bad = dict(recs)
        bad[sample[0]] = {"status": "ok", "decoded": _decoded_for(sample[0], int(tb.V) + 1)}
        r = bi.line_a(sample, idx, bad)
        self.assertFalse(r["line_a_pass"])
        self.assertEqual(r["differing_per_field"]["virtual_quote_reserves"], 1)
        nc = {k: v for k, v in recs.items() if k != sample[0]}
        self.assertFalse(bi.line_a(sample, idx, nc)["line_a_pass"])  # 1 not comparable > floor(1% of 18) = 0
        idx2 = index_of(pools, ev=True, drop={sample[0]})
        r2 = bi.line_a(sample, idx2, recs)
        self.assertEqual((r2["from_fallback"], r2["line_a_pass"]), (1, True))
        self.assertFalse(bi.line_a(sample, index_of(pools, ev=False), recs)["line_a_pass"])  # nothing compared

    def test_fetch_prints(self):
        pools, idx = self._setup()
        ks = pools[0].keys[:3]
        amb = (1, "M", 1, 1, 1, 1)
        idx = dict(idx)
        idx[amb] = br.PrintRef(1, None, None, None)

        def decode(_rpc, sig):
            if sig == idx[ks[1]].signature:
                raise RuntimeError("down")
            k = next(k for k in ks if idx[k].signature == sig)
            return [dict(_decoded_for(k, 5, sol_lamports=k[2] + (sig == idx[ks[2]].signature)), event_index=0)]

        recs = {tuple(r["key"]): r for r in bi.fetch_prints(None, ks + [amb], idx, {}, decode)}
        self.assertEqual(recs[amb]["status"], "no_raw_ref")
        self.assertEqual(recs[ks[1]]["status"], "fetch_failed:RuntimeError")
        self.assertEqual((recs[ks[0]]["status"], recs[ks[0]]["fields_equal"]), ("ok", True))
        self.assertFalse(recs[ks[2]]["fields_equal"])

    def test_run_p5_once_and_line_a_fail_fetches_the_rest(self):
        pools, idx = self._setup()
        calls = []

        def decode(_rpc, sig, v=int(tb.V)):
            calls.append(sig)
            k = next(k for k, r in idx.items() if r.signature == sig)
            return [dict(_decoded_for(k, v), event_index=0)]

        with tempfile.TemporaryDirectory() as d:
            lay = br.Layout(Path(d), 1)
            out = bi.run_p5(lay, None, GOOD, (pools, idx), decode)
            self.assertTrue(out["line_a_pass"])
            self.assertEqual(out["fetch_status"], {"ok": out["fetched_prints"]})
            cs = json.loads(lay.cross_source.read_text())
            self.assertEqual(cs["p7_sampled"], len(bi.plan_p5(pools, idx, GOOD)["p7"]))
            self.assertFalse(any(isinstance(v, str) and v.startswith("sig-") for v in cs.values()))  # ids stay in the id files
            with self.assertRaises(br.Refused):
                bi.run_p5(lay, None, GOOD, (pools, idx), decode)
            p7 = bi.p7_check(lay)
            self.assertIn("pass", p7)
            self.assertEqual(p7["v_source"], "ev_then_gettx")
            first = len(calls)
        with tempfile.TemporaryDirectory() as d:
            lay = br.Layout(Path(d), 1)
            calls.clear()
            out = bi.run_p5(lay, None, GOOD, (pools, idx), lambda r, s: decode(r, s, v=1))
            self.assertFalse(out["line_a_pass"])
            self.assertEqual(out["fetched_prints"], len(set(bi.plan_p5(pools, idx, GOOD)["needed"]) | {k for k, _ in bi.plan_p5(pools, idx, GOOD)["p7"]}
                                                         | set(bi.plan_p5(pools, idx, GOOD)["line_a"])))
            self.assertGreaterEqual(len(calls), first)

    def test_tier_lines(self):
        V = 17.58e9
        q, b = 30e9, 5e14
        sell_tok = 1e12
        Q = q + V
        sell_sol = Q * sell_tok / (b + sell_tok) * (1 - bf.tier_fee(Q, b))
        buy_sol = 1e8
        net = buy_sol * (1 - bf.tier_fee(Q, b))
        buy_tok = b * net / (Q + net)
        good_s = {"key": [1, "M", sell_sol, sell_tok, q, b], "isbuy": False}
        good_b = {"key": [2, "M", buy_sol, buy_tok, q, b], "isbuy": True}
        bad_s = {"key": [3, "M", sell_sol * 1.001, sell_tok, q, b], "isbuy": False}
        zero_b = {"key": [4, "M", 0, 0, q, b], "isbuy": True}
        for r in (good_s, good_b, bad_s, zero_b):
            r["key"] = [int(round(x)) if not isinstance(x, str) else x for x in r["key"]]
        res = bi.tier_lines([good_s, good_b, bad_s, zero_b], lambda k, rec: V)
        self.assertEqual((res["sell"]["n"], res["sell"]["match"]), (2, 1))
        self.assertEqual((res["buy"]["n"], res["buy"]["match"], res["buy"]["skipped"]), (1, 1, 1))
        self.assertFalse(res["sell"]["pass"])  # 50% < 75%
        self.assertTrue(res["buy"]["pass"])
        self.assertEqual(bi.tier_lines([good_s], lambda k, rec: None)["sell"]["no_v"], 1)
        self.assertFalse(bi.tier_lines([good_b], lambda k, rec: V)["sell"]["pass"])  # no sell at all fails the side

    def test_line_b(self):
        q, b, v = 30_000_000_000, 500_000_000_000_000, 17_580_000_000
        base_in = 1_000_000_000_000
        sell = {"status": "ok", "decoded": dict(side="sell", quote_reserve=q, base_reserve=b, virtual_quote_reserves=v, token_raw=base_in,
                                                 pool_quote_amount=(q + v) * base_in // (b + base_in), ix_name="sell")}
        qin = 100_000_000
        buy = {"status": "ok", "decoded": dict(side="buy", quote_reserve=q, base_reserve=b, virtual_quote_reserves=v, pool_quote_amount=qin,
                                                token_raw=b * qin // (q + v + qin), ix_name="buy")}
        off = json.loads(json.dumps(buy))
        off["decoded"]["token_raw"] += 10**9
        exact_in = json.loads(json.dumps(buy))
        exact_in["decoded"]["ix_name"] = "buy_exact_quote_in_v2"
        no_ix = json.loads(json.dumps(buy))
        no_ix["decoded"]["ix_name"] = None
        r = bi.line_b([sell, buy, off, exact_in, no_ix, {"status": "absent"}])
        self.assertEqual((r["sell"]["comparable"], r["sell"]["match"], r["sell"]["pass"]), (1, 1, True))
        self.assertEqual((r["buy"]["comparable"], r["buy"]["match"], r["buy"]["excluded"], r["buy"]["not_comparable"]), (2, 1, 1, 1))
        self.assertFalse(r["buy"]["pass"])
        two = json.loads(json.dumps(sell))
        two["decoded"]["pool_quote_amount"] += 2  # within 2 units
        self.assertTrue(bi.line_b([two])["sell"]["pass"])
        self.assertFalse(bi.line_b([two])["buy"]["pass"])  # no comparable buy fails that side

    def test_e1_copy(self):
        with tempfile.TemporaryDirectory() as d:
            src, dest = Path(d) / "cal.json", Path(d) / "e1" / "calibration.json"
            src.write_text(json.dumps({"aggregate": {"faa3192": {"pnl_gap_lamports_live_minus_sim": {"n": 25, "mean": -384022.0}}}}))
            r = bi.e1_copy(src, dest)
            self.assertEqual((r["n"], r["n_ge_20"]), (25, True))
            self.assertEqual(dest.read_bytes(), src.read_bytes())
            self.assertEqual(br.read_e1(dest), (25, -384022.0))
            with self.assertRaises(br.Refused):
                bi.e1_copy(src, dest)  # written once
            src.write_text(json.dumps({"aggregate": {"faa3192": {"pnl_gap_lamports_live_minus_sim": {"n": None, "mean": 1}}}}))
            with self.assertRaises(br.Refused):
                bi.e1_copy(src, Path(d) / "e1b.json")
            with self.assertRaises(br.Refused):
                bi.e1_copy(Path("/var/lib/mal/fast-listener/helius.env"), Path(d) / "x.json")

    def test_pda_record(self):
        from tools import pump_structure_monitor as M

        class Rpc:
            def call(self, method, params):
                assert method == "getSignaturesForAddress"
                return [{"signature": "s1", "slot": 10, "blockTime": 100, "err": None}, {"signature": "s2", "slot": 20, "blockTime": 200, "err": None},
                        {"signature": "s3", "slot": 30, "blockTime": 300, "err": None}, {"signature": "x", "slot": 40, "blockTime": 400, "err": {"e": 1}}]

        txs = {"s3": {"who": "other"}, "s2": {"who": "AUTH"}}
        with mock.patch.object(M, "fetch_tx", side_effect=lambda c, s: txs.get(s)), \
                mock.patch.object(M, "tx_signers", side_effect=lambda tx: [tx["who"]]), \
                mock.patch.object(M, "tx_logs", side_effect=lambda tx: []), \
                mock.patch.object(M, "program_instructions", return_value=["BoostBuyAndBurn"]):
            r = bi.pda_record(Rpc(), "11111111111111111111111111111111", "M", "AUTH")
        self.assertEqual((r["n_slices"], r["slice_slots"], r["last_bt"], r["no_slices"]), (3, [10, 20, 30], 200, False))

    def test_cli_refuses_before_final_and_look2(self):
        from tools import forward_v_join as J

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bi.main(["p5", "--look", "2"]), 2)
            with mock.patch.object(J, "final_marker", side_effect=J.Refused("no FINAL")):
                for argv in (["p5", "--look", "1"], ["p7", "--look", "1"], ["account", "--look", "1"], ["boost-pda", "--look", "1"],
                             ["e1", "--source", "/tmp/x.json"]):
                    self.assertEqual(bi.main(argv), 2)
            with self.assertRaises(SystemExit):
                bi.main(["p5", "--look", "1", "--n", "50"])


if __name__ == "__main__":
    unittest.main()
