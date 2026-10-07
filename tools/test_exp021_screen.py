"""Tests for tools/exp021_screen.py. Fixtures only; nothing here opens a real data path (no /data/mal read)."""

from __future__ import annotations

import json
import tempfile
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp015_screen as e15
import tools.exp016_rug as rug
import tools.exp016_screen as e16
import tools.exp021_screen as x

warnings.filterwarnings("ignore", category=FutureWarning)
NON_P1 = e15.non_p1_dates(True)
P1 = e15.block_dates("P1")
PLAN_TEXT = x.PLAN.read_text(encoding="utf-8")
HOUR = 3_600_000


def block_of(date):
    if date in P1:
        return "P1"
    if date in e15.p2_dates():
        return "P2"
    return "P3" if date <= "2026-09-08" else "P4"


def mk_row(i, date, *, net=1_000_000, filled=True, frozen=False, held=0.0, rugged=False, dumps=0.0, ef0=0.0):
    ef = [0.0] * 18
    ef[0] = ef0
    rf = [0.0] * len(rug.FEATURE_NAMES)
    rf[rug.FEATURE_NAMES.index("launch_supply_held")] = held
    rf[rug.FEATURE_NAMES.index("creator_prior_dumps")] = dumps
    return {"mint": f"m-{date}-{i:03d}", "date": date, "block": block_of(date), "source": "S", "mig_ms": e15.date_start_ms(date) + 12 * HOUR + i * 1000,
            "filled": filled, "frozen": frozen, "ef": ef, "rf": rf, "flat": float(net), "press": float(net), "rug": bool(rugged) if filled else None, "exit_kind": "cap", "alt": {}}


def cell_rec(i, date, *, status="FILLED", net0=1_000_000, frozen_feats=True, rugged=False):
    filled = status == "FILLED"
    blk = block_of(date)
    mig = next(e15.date_start_ms(date) + h * HOUR + i for h in (12, 6) if e15.in_block_window(blk, e15.date_start_ms(date) + h * HOUR + i))  # edge dates are half days
    prim = {"k": 6, "lag": 2, "size": 50_000_000, "censored": False, "filled": filled, "status": 1 if filled else 0, "net0": net0, "sides": 2, "p_press": 0.15}
    return {"mint": f"c-{date}-{i}", "date": date, "block": block_of(date), "source": "S", "mig_ms": mig, "status": status,
            "exp012_features": [float(j) for j in range(18)] if frozen_feats else None, "features": {n: 0.1 for n in rug.FEATURE_NAMES},
            "cells": {(6, 2): prim, (6, 0): dict(prim, lag=0)}, "label": {"rug": rugged} if filled else None, "exit_kind": "tp"}


class DesignTests(unittest.TestCase):
    def test_feature_sets_are_the_plan_sets(self):
        self.assertEqual(x.FEATURES_CONTROL, list(fz.FROZEN_FEATURE_NAMES))
        self.assertEqual(len(x.FEATURES_RUG), 34)
        self.assertEqual(len(set(x.FEATURES_RUG)), 34)
        self.assertEqual(x.FEATURES_RUG[:18], x.FEATURES_CONTROL)
        # a1-a5, b1-b5, c2, d1-d4, e1 (EXP-016 section 3); c1 and e2 are EXP-012's own; f1 is phase 2
        self.assertEqual(len(x.RUG_EXTRA), 5 + 5 + 1 + 4 + 1)
        for n in ("sniper_buy_share", "n_buyers"):
            self.assertIn(n, x.FEATURES_CONTROL)
            self.assertNotIn(n, x.RUG_EXTRA)
        self.assertFalse([n for n in x.RUG_EXTRA if "fund" in n or n == "f1"])
        for n in ("launch_sol_share", "top3_share", "serial_launch_held", "creator_sold_frac"):
            self.assertIn(n, x.RUG_EXTRA)

    def test_numbers_match_the_plan_text(self):
        for s in ("DEC-021", "ex-best-date", "20%", "27 non-P1 dates", "tp50_sl30", "exit lag 2"):
            self.assertIn(s, PLAN_TEXT)
        self.assertEqual(x.FAMILY_ALPHA, 0.025)
        self.assertEqual(x.CONCENTRATION_MAX, 0.20)
        self.assertEqual((x.e16.K, x.e16.EXIT_LAG, x.PRIMARY_CELL), (6, 2, (6, 2)))
        self.assertEqual(len(NON_P1), 27)
        self.assertEqual(x.TRIES_CAP, 1)

    def test_learner_is_exp015_c2(self):
        self.assertEqual(x.MDL, e15.CONFIGS["c2"]["min_data_in_leaf"])
        self.assertEqual(e15.CONFIGS["c2"]["label"], "c2")

    def test_label_is_exp015_c2(self):
        self.assertEqual(x.label_row(mk_row(0, NON_P1[0], net=5)), 1)
        self.assertEqual(x.label_row(mk_row(0, NON_P1[0], net=-5)), 0)
        self.assertEqual(x.label_row(mk_row(0, NON_P1[0], net=5, filled=False)), 0)

    def test_arm_vectors(self):
        r = mk_row(0, NON_P1[0], held=0.25, ef0=3.0)
        c, g = x.arm_vector(r, "control"), x.arm_vector(r, "rug")
        self.assertEqual((len(c), len(g)), (18, 34))
        self.assertEqual(g[:18], c)
        self.assertEqual(g[18 + x.RUG_EXTRA.index("launch_supply_held")], 0.25)

    def test_no_real_data_path_in_the_module_source(self):
        src = Path(x.__file__).read_text(encoding="utf-8")
        self.assertNotIn("/data/mal", src)


class TableTests(unittest.TestCase):
    def test_includes_filled_and_miss_drops_censored_and_no_sim(self):
        d = NON_P1[0]
        cells = [cell_rec(1, d), cell_rec(2, d, status="MISS"), cell_rec(3, d, status="CENSORED"), cell_rec(4, d, status="NO_SIM")]
        t = x.build_table(cells, [True, False, True, True])
        self.assertEqual([r["mint"] for r in t], [f"c-{d}-1", f"c-{d}-2"])
        self.assertEqual([r["filled"] for r in t], [True, False])
        self.assertEqual([r["frozen"] for r in t], [True, False])
        self.assertEqual(len(t[0]["ef"]), 18)
        self.assertEqual(len(t[0]["rf"]), len(rug.FEATURE_NAMES))
        self.assertIsNone(t[1]["rug"])

    def test_nets_come_from_exp015_cell_nets(self):
        d = NON_P1[0]
        t = x.build_table([cell_rec(1, d, net0=2_000_000)], [False])
        n = e15.cell_nets(cell_rec(1, d, net0=2_000_000)["cells"][(6, 2)])
        self.assertEqual((t[0]["flat"], t[0]["press"]), (n["flat"], n["press"]))
        self.assertIn("6_0", t[0]["alt"])

    def test_counts_and_limits(self):
        rows = [mk_row(i, d, frozen=(i < 3)) for d in NON_P1 for i in range(6)] + [mk_row(i, d) for d in P1 for i in range(3)]
        c = x.table_counts(rows, True)
        self.assertEqual(c["frozen_selected_non_p1"], 27 * 3)
        self.assertEqual(c["non_p1_dates_without_rows"], [])
        self.assertFalse(c["k2_cells_available"])
        self.assertEqual(x.check_table_limits(c, True), ["81 frozen-selected rows on the non-P1 dates (< 100)"])
        rows2 = [mk_row(i, d, frozen=True) for d in NON_P1 for i in range(6)]
        self.assertEqual(x.check_table_limits(x.table_counts(rows2, True), True), [])
        self.assertTrue(any("P4" in w for w in x.check_table_limits(x.table_counts(rows2, True), False)))
        with self.assertRaises(x.Refused):
            x.enforce_table_limits(c, True)

    def test_refuses_when_fewer_than_14_dates_have_a_frozen_pick(self):
        def rows_with(k):  # 27 non-P1 dates, 8 rows each; the first k dates carry frozen picks (enough rows overall: 8 per date)
            return [mk_row(i, d, frozen=(j < k)) for j, d in enumerate(NON_P1) for i in range(8)]

        c13, c14 = x.table_counts(rows_with(13), True), x.table_counts(rows_with(14), True)
        self.assertEqual((c13["non_p1_dates_with_frozen_pick"], c14["non_p1_dates_with_frozen_pick"]), (13, 14))
        self.assertTrue(any("13 of 27" in w and "B3" in w for w in x.check_table_limits(c13, True)))
        self.assertFalse(any("B3" in w for w in x.check_table_limits(c14, True)))
        with self.assertRaises(x.Refused):
            x.enforce_table_limits(c13, True)

    def test_limits_refuse_bad_features_duplicates_and_missing_dates(self):
        rows = [mk_row(i, d, frozen=True) for d in NON_P1[:-1] for i in range(6)]
        rows[0]["ef"][2] = float("nan")
        rows.append(dict(rows[1]))
        rows.append({**mk_row(9, NON_P1[0]), "rf": None})
        why = " | ".join(x.check_table_limits(x.table_counts(rows, True), True))
        for s in ("non-finite", "twice", "lack", "no universe row"):
            self.assertIn(s, why)

    def test_shas_are_outcome_blind_and_order_free(self):
        a = [mk_row(i, NON_P1[0], net=5) for i in range(4)]
        b = [dict(r, flat=-9.0, press=-9.0, rug=True) for r in reversed(a)]
        self.assertEqual(x.universe_sha256(a), x.universe_sha256(b))
        self.assertEqual(x.feature_table_sha256(a), x.feature_table_sha256(b))
        c = [dict(r) for r in a]
        c[0]["frozen"] = True
        self.assertNotEqual(x.universe_sha256(a), x.universe_sha256(c))


class FoldTests(unittest.TestCase):
    def test_feature_names_swap_is_restored(self):
        before = list(fz.FROZEN_FEATURE_NAMES)
        with x.feature_names(x.FEATURES_RUG):
            self.assertEqual(len(fz.FROZEN_FEATURE_NAMES), 34)
        self.assertEqual(fz.FROZEN_FEATURE_NAMES, before)
        with self.assertRaises(RuntimeError), x.feature_names(["a"]):
            raise RuntimeError
        self.assertEqual(fz.FROZEN_FEATURE_NAMES, before)

    def test_fit_predict_runs_for_both_widths_and_refuses_one_class(self):
        import random

        rng = random.Random(0)
        for w, names in ((18, x.FEATURES_CONTROL), (34, x.FEATURES_RUG)):
            xs = [[rng.random() for _ in range(w)] for _ in range(60)]
            ys = [1 if r[0] > 0.5 else 0 for r in xs]
            p = x.fit_predict(xs, ys, xs[:5], names)
            self.assertEqual(len(p), 5)
        self.assertIsNone(x.fit_predict(xs, [0] * 60, xs[:5], x.FEATURES_RUG))
        self.assertIsNone(x.fit_predict(xs[:10], ys[:10], xs[:5], x.FEATURES_RUG))

    def test_top_n_ties_by_mint_and_bounds(self):
        s = {"b": 1.0, "a": 1.0, "c": 0.5}
        self.assertEqual(x.top_n(s, 1), {"a"})
        self.assertEqual(x.top_n(s, 2), {"a", "b"})
        self.assertEqual(x.top_n(s, 0), set())
        self.assertEqual(x.top_n(s, 9), {"a", "b", "c"})

    def signal_table(self, dates):
        """Rows where the label is carried by launch_supply_held (a RUG-only feature): control is blind to it."""
        rows = []
        for d in dates:
            for i in range(40):
                bad = i % 2 == 0
                rows.append(mk_row(i, d, net=-5_000_000 if bad else 3_000_000, held=0.3 if bad else 0.0, frozen=(i % 4 == 1 or i == 0)))
        return rows

    def test_count_matched_per_date_and_rug_learns_the_signal(self):
        dates = NON_P1[:6]
        t = self.signal_table(dates)
        fr = x.run_folds(t, dates)
        for f in fr["folds"]:
            self.assertEqual(f["n_control"], f["n_frozen"])
            self.assertEqual(f["n_rug"], f["n_frozen"])
        for d in dates:
            n = sum(1 for r in t if r["date"] == d and r["frozen"])
            for a in x.ARMS:
                self.assertEqual(sum(1 for r in t if r["date"] == d and fr["sel"][a][r["mint"]]), n)
        rug_picks = [r for r in t if fr["sel"]["rug"][r["mint"]]]
        self.assertTrue(all(r["press"] > 0 for r in rug_picks))  # RUG avoids every bad row
        ctl_bad = sum(1 for r in t if fr["sel"]["control"][r["mint"]] and r["press"] < 0)
        self.assertGreater(ctl_bad, 0)

    def test_purge_and_held_out_date_are_excluded_from_training(self):
        dates = NON_P1[:3]
        t = self.signal_table(dates)
        d = dates[1]
        start = e15.date_start_ms(d)
        edge = dict(mk_row(77, d), mint="edge-before", mig_ms=start - 10 * 60_000, date=dates[0])
        edge2 = dict(mk_row(78, d), mint="edge-after", mig_ms=start + e15.DAY_MS + 10 * 60_000, date=dates[2])
        t = t + [edge, edge2]
        seen = []

        def fake(train_x, train_y, test_x, names):
            seen.append(len(train_x))
            return [0.5] * len(test_x)

        with mock.patch.object(x, "fit_predict", fake):
            x.run_folds(t, [d])
        self.assertEqual(seen, [len(t) - 40 - 2] * 2)  # two arms; held-out date and the two purged rows removed

    def test_untrainable_fold_refuses(self):
        t = [mk_row(i, NON_P1[0], net=1) for i in range(5)] + [mk_row(i, NON_P1[1], net=1) for i in range(5)]
        with self.assertRaises(x.Refused):
            x.run_folds(t, [NON_P1[0]])


def sel_of(table, pred):
    return {r["mint"]: bool(pred(r)) for r in table}


class BarTests(unittest.TestCase):
    def book(self, n=10):
        """27 non-P1 dates x n rows; 'good' rows earn, 'bad' rows lose. Control picks 2 per date (one good, one bad); RUG picks the 2 good."""
        rows = []
        for d in NON_P1:
            for i in range(n):
                rows.append(mk_row(i, d, net=3_000_000 if i % 2 == 0 else -3_000_000, rugged=(i % 2 == 1)))
        return rows

    def sels(self, rows, rug_pick=lambda r: int(r["mint"][-3:]) in (0, 2), ctl_pick=lambda r: int(r["mint"][-3:]) in (0, 1)):
        return {"control": sel_of(rows, ctl_pick), "rug": sel_of(rows, rug_pick)}

    def test_all_bars_pass_on_a_clean_gain(self):
        rows = self.book()
        # vary the gain by date so no date is above 20%: same gain per date (6 mSOL x 1 swapped pick) -> each date is 1/27
        b = x.bars(rows, self.sels(rows), True)
        for leg in x.LEGS:
            for k in ("B1_paired_mean_p", "B3_majority_dates_positive", "B4_ex_top3", "B5_ex_best_date", "B6_paired_concentration"):
                self.assertTrue(b[leg][k]["pass"], (leg, k, b[leg][k]))
        self.assertTrue(b["passes"])
        self.assertLess(b["flat"]["B1_paired_mean_p"]["p_one_sided"], 0.025)

    def test_kept_book_is_report_only_and_never_gates(self):
        """Amendment 1: a negative kept book (fixed fees dominate) does not fail a clean paired gain, and the kept-book numbers are still reported."""
        rows = self.book()
        for r in rows:  # the RUG kept book loses on its own (the fee-dominated case), yet RUG beats CONTROL by a clean margin on every date
            n = int(r["mint"][-3:])
            r["flat"] = r["press"] = {0: -1_000_000.0, 2: -100_000.0, 1: -3_000_000.0}.get(n, -1.0)
        b = x.bars(rows, self.sels(rows), True)
        for leg in x.LEGS:
            self.assertNotIn("B2_kept_ci_lo", b[leg])
            self.assertFalse(b[leg]["kept_book_report_only"]["ci_lo_gt_0"])
            self.assertLess(b[leg]["kept_book_report_only"]["total_sol"], 0)
            self.assertLess(b[leg]["kept_book_report_only"]["ex_top3_sol"], 0)
            self.assertTrue(b[leg]["all"], b[leg])
        self.assertTrue(b["passes"])

    def test_b6_divides_by_the_net_paired_total(self):
        """Amendment 2: 20 dates at +1 and 7 at -2.5 net to 2.5; the best date is 40% of it, so B6 fails (share of the positive total would be 5%)."""
        rows = [mk_row(0, d, net=(1_000_000_000 if i < 20 else -2_500_000_000)) for i, d in enumerate(NON_P1)]
        s = {"control": sel_of(rows, lambda r: False), "rug": sel_of(rows, lambda r: True)}
        b = x.bars(rows, s, True)
        for leg in x.LEGS:
            b6 = b[leg]["B6_paired_concentration"]
            self.assertAlmostEqual(b6["paired_total_sol"], 2.5)
            self.assertAlmostEqual(b6["max_date_share_of_net_total"], 0.4)
            self.assertAlmostEqual(b6["max_date_share_of_positive_total"], 0.05)
            self.assertFalse(b6["pass"])
            self.assertTrue(b[leg]["B5_ex_best_date"]["pass"])  # B5 is unchanged: 1.5 > 0
        self.assertFalse(b["passes"])

    def test_b6_fails_on_a_non_positive_net_total(self):
        rows = [mk_row(0, d, net=(1_000_000_000 if i < 10 else -1_000_000_000)) for i, d in enumerate(NON_P1)]
        s = {"control": sel_of(rows, lambda r: False), "rug": sel_of(rows, lambda r: True)}
        b6 = x.bars(rows, s, True)["flat"]["B6_paired_concentration"]
        self.assertFalse(b6["pass"])
        self.assertIsNone(b6["max_date_share_of_net_total"])

    def test_bars_3_to_5_are_paired_not_kept_book(self):
        rows = self.book()
        b = x.bars(rows, self.sels(rows), True)
        pr = b["flat"]["paired_report"]
        self.assertEqual(b["flat"]["B3_majority_dates_positive"]["dates_positive"], pr["dates_positive"])
        self.assertEqual(b["flat"]["B4_ex_top3"]["ex_top3_sol"], pr["ex_top3_sol"])
        self.assertEqual(b["flat"]["B5_ex_best_date"]["ex_best_date_sol"], pr["ex_best_date_sol"])

    def test_equal_arms_fail_the_paired_bars(self):
        rows = self.book()
        s = self.sels(rows, rug_pick=lambda r: int(r["mint"][-3:]) in (0, 1))
        b = x.bars(rows, s, True)
        self.assertFalse(b["flat"]["B1_paired_mean_p"]["pass"])
        self.assertFalse(b["flat"]["B6_paired_concentration"]["pass"])
        self.assertFalse(b["passes"])

    def test_p_not_below_alpha_fails_even_with_positive_mean(self):
        rows = self.book()
        by_date = {}
        # gain only on 3 dates, tiny elsewhere negative: positive mean but a high p
        s = self.sels(rows)
        for r in rows:
            if r["date"] not in NON_P1[:2] and int(r["mint"][-3:]) == 2:
                r["flat"] = r["press"] = -3_000_000.0
        b = x.bars(rows, s, True)
        self.assertEqual(b["flat"]["B1_paired_mean_p"]["pass"], bool(b["flat"]["B1_paired_mean_p"]["mean_x_sol"] > 0 and b["flat"]["B1_paired_mean_p"]["p_one_sided"] < 0.025))
        self.assertFalse(b["passes"])
        del by_date

    def test_ex_best_date_catches_a_one_date_book(self):
        rows = self.book()
        for r in rows:
            if int(r["mint"][-3:]) == 2:
                r["flat"] = r["press"] = 3_000_000.0 if r["date"] == NON_P1[5] else -1.0
            if int(r["mint"][-3:]) == 0:
                r["flat"] = r["press"] = 1.0
        s = {"control": sel_of(rows, lambda r: False), "rug": sel_of(rows, lambda r: int(r["mint"][-3:]) in (0, 2))}
        b = x.bars(rows, s, True)
        self.assertFalse(b["flat"]["B5_ex_best_date"]["pass"])
        self.assertFalse(b["flat"]["B6_paired_concentration"]["pass"])

    def test_a_date_with_no_pick_is_not_positive(self):
        rows = self.book()
        s = self.sels(rows, rug_pick=lambda r: int(r["mint"][-3:]) in (0, 2) and r["date"] in NON_P1[:13])
        b = x.bars(rows, s, True)
        self.assertEqual(b["flat"]["B3_majority_dates_positive"]["dates_positive"], 13)
        self.assertFalse(b["flat"]["B3_majority_dates_positive"]["pass"])  # 13 of 27 is not a majority

    def test_ex_top3_and_ci_lower_bound_fail_on_three_lucky_trades(self):
        rows = self.book(n=4)
        for r in rows:
            r["flat"] = r["press"] = -1_000.0
        for r in rows[:3]:
            r["flat"] = r["press"] = 900_000_000.0
        s = {"control": sel_of(rows, lambda r: False), "rug": sel_of(rows, lambda r: True)}
        b = x.bars(rows, s, True)
        self.assertFalse(b["flat"]["B4_ex_top3"]["pass"])

    def test_p1_rows_never_decide(self):
        rows = self.book() + [mk_row(i, d, net=-9_000_000) for d in P1 for i in range(10)]
        s = self.sels(rows)
        s["rug"].update({r["mint"]: True for r in rows if r["block"] == "P1"})
        b1 = x.bars(rows, s, True)
        keep = {r["mint"] for r in rows if r["block"] != "P1"}
        b0 = x.bars([r for r in rows if r["mint"] in keep], {a: {m: v for m, v in s[a].items() if m in keep} for a in s}, True)
        self.assertEqual(b1["flat"]["B1_paired_mean_p"]["mean_x_sol"], b0["flat"]["B1_paired_mean_p"]["mean_x_sol"])
        self.assertEqual(b1["n_rows"], b0["n_rows"])


class ReportOnlyTests(unittest.TestCase):
    def test_veto_rug_rate_and_k2(self):
        rows = []
        for d in NON_P1[:3] + P1[:1]:
            for i in range(6):
                rows.append(mk_row(i, d, net=-4_000_000 if i < 2 else 2_000_000, held=0.2 if i < 2 else 0.0, rugged=i < 2, frozen=i in (0, 3)))
        rows[2]["alt"] = {"4_2": {"flat": 1.0, "press": 1.0}}
        sel = {"control": sel_of(rows, lambda r: int(r["mint"][-3:]) in (0, 3)), "rug": sel_of(rows, lambda r: int(r["mint"][-3:]) in (2, 3))}
        ro = x.report_only(rows, sel, True)
        rr = ro["rug_label_rate"]
        self.assertEqual(rr["control"]["non_p1"], {"n_filled_picks": 6, "n_rug": 3, "rate": 0.5})
        self.assertEqual(rr["rug"]["non_p1"]["n_rug"], 0)
        v = ro["veto_on_rug_picks"]["non_p1"]
        self.assertEqual(set(v), {"r1", "r2", "r3", "r4"})
        self.assertEqual(v["r1"]["n_vetoed_filled"], 0)  # RUG's picks carry no launch_supply_held
        v2 = x.veto_report(rows, sel["control"])
        self.assertEqual(v2["r1"]["n_vetoed_filled"], 4)  # 2 of 3 dates x ... control picks index 0 (held 0.2) on 4 dates
        self.assertEqual(v2["r1"]["n_rug_vetoed"], 4)
        self.assertGreater(v2["r1"]["kept_mean_sol_press"], v2["r1"]["picks_mean_sol_press"])
        self.assertIn("not available", ro["alt_cells_rug_minus_control"]["k2"])
        self.assertIn("4_2", ro["alt_cells_rug_minus_control"]["cells"])
        self.assertIn("p1_report_only_paired_rug_minus_control", ro)

    def test_k2_reported_when_cached(self):
        rows = [mk_row(i, NON_P1[0]) for i in range(3)]
        for r in rows:
            r["alt"] = {"2_2": {"flat": 1.0, "press": 1.0}}
        s = {"control": sel_of(rows, lambda r: False), "rug": sel_of(rows, lambda r: True)}
        self.assertEqual(x.alt_cell_report(rows, s)["k2"], "available")

    def test_veto_thresholds_are_exp016s(self):
        r = mk_row(0, NON_P1[0], held=0.12)
        self.assertTrue(e16.rule_veto("r1", r["rf"]))
        r2 = mk_row(0, NON_P1[0], held=0.119)
        self.assertFalse(e16.rule_veto("r1", r2["rf"]))
        self.assertTrue(e16.rule_veto("r4", mk_row(0, NON_P1[0], dumps=1.0)["rf"]))


class RunScreenTests(unittest.TestCase):
    def test_run_screen_end_to_end_small(self):
        dates = NON_P1[:4]
        t = FoldTests.signal_table(None, dates)
        res = x.run_screen(t, True, dates)
        self.assertEqual(len(res["folds"]), 4)
        self.assertIn(res["decision"]["outcome"], (x.OUTCOME_PASS, x.OUTCOME_FAIL))
        self.assertFalse(res["bars"]["passes"])  # 4 of 27 dates cannot be a majority
        self.assertEqual(x.decide(None)["outcome"], x.OUTCOME_INCOMPLETE)

    def test_render_md_has_every_bar(self):
        dates = NON_P1[:3]
        res = x.run_screen(FoldTests.signal_table(None, dates), True, dates)
        md = x.render_md({"first_line": x.first_line(True), "banner": x.BANNER, "decision": res["decision"], "bars": res["bars"], "report_only": res["report_only"], "table_counts": {}})
        for k in ("B1_paired_mean_p", "B3_majority_dates_positive", "B4_ex_top3", "B5_ex_best_date", "B6_paired_concentration"):
            self.assertIn(k, md)


class TriesTests(unittest.TestCase):
    def test_started_and_completed_in_both_logs_and_refusal_after(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            ops, canon, out = td / "ops.jsonl", td / "canon.jsonl", td / "out"
            r = x.log_all(out, ops, canon, "started", True, {"universe_sha256": "u"})
            self.assertEqual((r["logged"], r["canonical_logged"]), (1, 1))
            for log in (ops, canon):
                (rec,) = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(rec["config"]["key"], x.STARTED_KEY)
                self.assertEqual(rec["config"]["status"], "started")
            self.assertEqual(x.log_all(out, ops, canon, "started", True, {})["logged"], 0)  # idempotent
            with self.assertRaises(x.Refused):
                x.check_no_prior_tries(ops, canon)
            r = x.log_all(out, ops, canon, "completed", True, {})
            groups = set(e15.pool_group_blocks(True))
            self.assertEqual(r["logged"], len(groups))
            lines = [json.loads(line) for line in ops.read_text().splitlines()]
            self.assertEqual({ln["config"]["pool_group"] for ln in lines if ln["config"]["key"] == x.CONFIG_KEY}, groups)
            self.assertTrue(all(ln["tool"] == x.TOOL for ln in lines))
            # a non-completed status is never added after `completed`
            self.assertEqual(x.log_all(out, ops, canon, "aborted_after_read", True, {})["logged"], 0)

    def test_check_no_prior_tries_ignores_other_experiments(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "t.jsonl"
            log.write_text(json.dumps({"config": {"key": "exp016_started"}}) + "\n" + json.dumps({"config": {"key": "exp015_c1"}}) + "\n")
            x.check_no_prior_tries(log)
            log.write_text(json.dumps({"config": {"key": "exp021_rug"}}) + "\n")
            with self.assertRaises(x.Refused):
                x.check_no_prior_tries(log)


class CliTests(unittest.TestCase):
    def setUp(self):
        for name, kw in (("part1_pins", {"return_value": "b" * 40}), ("check_fallback_pin", {})):  # the guards have their own tests (PreregTests)
            p = mock.patch.object(x, name, **kw)
            p.start()
            self.addCleanup(p.stop)

    def test_parser_has_the_modes_and_max_workers(self):
        a = x._parser().parse_args(["--p1-fast-dir", "a", "--p1-oracle-insample-dir", "b", "--out-dir", "o", "--max-workers", "2", "--precount"])
        self.assertEqual((a.max_workers, a.precount, a.guards_only), (2, True, False))
        a = x._parser().parse_args(["--p1-fast-dir", "a", "--p1-oracle-insample-dir", "b", "--out-dir", "o", "--guards-only"])
        self.assertTrue(a.guards_only)
        self.assertEqual(a.max_workers, 4)

    def argv(self, td, *extra):
        return ["--p1-fast-dir", "a", "--p1-oracle-insample-dir", "b", "--p2-view-dir", "d", "--out-dir", str(Path(td) / "o"), "--frozen-dir", "fd",
                "--tries-log", str(Path(td) / "ops.jsonl"), "--canonical-tries", str(Path(td) / "canon.jsonl"), *extra]

    def test_screen_refuses_before_any_row_while_the_pin_is_pending(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(e16, "VMAP_EXP016_SHA256", "PENDING"), mock.patch.object(x, "collect") as col:
            rc = x.main(self.argv(td, "--v-constancy-json", str(Path(td) / "c.json")))
            self.assertEqual(rc, 2)
            col.assert_not_called()
            self.assertFalse((Path(td) / "ops.jsonl").exists())
            self.assertFalse((Path(td) / "o" / "RUN.lock").exists())

    def test_screen_refuses_without_the_constancy_file(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(x, "collect") as col:
            self.assertEqual(x.main(self.argv(td)), 2)
            col.assert_not_called()

    def test_guards_only_reads_no_row_and_spends_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            samples = Path(td) / "c.json"
            samples.write_text("[]")
            g = {"with_p4": True, "vmap_sha256": "x", "g1": {}, "g2": {}, "g3": {}, "g4": {}}
            with mock.patch.object(x, "run_guards", return_value=g), mock.patch.object(e16, "load_pinned_vmap", return_value={}), \
                    mock.patch.object(e16, "check_v_constancy", return_value={}), mock.patch.object(e16, "git_state", return_value={"head": "h", "dirty_tools": False}), \
                    mock.patch.object(x, "collect") as col:
                rc = x.main(self.argv(td, "--v-constancy-json", str(samples), "--guards-only"))
            self.assertEqual(rc, 0)
            col.assert_not_called()
            self.assertFalse((Path(td) / "ops.jsonl").exists())

    def test_guards_refuse_when_a_prior_exp021_line_exists(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "canon.jsonl").write_text(json.dumps({"config": {"key": "exp021_started"}}) + "\n")
            with mock.patch.object(x, "run_guards", return_value={"with_p4": True}), mock.patch.object(x, "collect") as col:
                rc = x.main(self.argv(td, "--v-constancy-json", str(Path(td) / "c.json")))
            self.assertEqual(rc, 2)
            col.assert_not_called()

    def test_full_run_on_fixtures_logs_started_and_completed(self):
        """The whole screen path on a fake `collect`: tries in both logs, a lock record, a report, and a refusal of a second run."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            samples = td / "c.json"
            samples.write_text("[]")
            cells = []
            for d in NON_P1 + P1:
                for i in range(8):
                    c = cell_rec(i, d, status="FILLED" if i % 2 == 0 else "MISS", net0=2_000_000 if i % 2 == 0 else 0)
                    cells.append(c)
            results = [{"tag": "S", "cells": cells}]
            g = {"with_p4": True, "vmap_sha256": "v", "g1": {}, "g2": {}, "g3": {}, "g4": {}}
            sel = [i % 2 == 0 for i in range(len(cells))]
            patches = [
                mock.patch.object(x, "run_guards", return_value=g), mock.patch.object(e16, "load_pinned_vmap", return_value={}),
                mock.patch.object(e16, "check_v_constancy", return_value={}), mock.patch.object(e16, "git_state", return_value={"head": "h", "dirty_tools": False}),
                mock.patch.object(x, "collect", return_value=(results, {})), mock.patch.object(x, "load_oof", return_value=({}, 0, 0, {})),
                mock.patch.object(e16, "frozen_flags", return_value=sel), mock.patch.object(e16, "pre_started_counts", return_value={}),
                mock.patch.object(e16, "oof_without_cell", return_value={}), mock.patch.object(x, "enforce_limits"),
                mock.patch.object(e16, "v_coverage", return_value={}), mock.patch.object(e16, "check_constancy_sample"),
                mock.patch.object(e16, "set_process_workers"),
            ]
            for p in patches:
                p.start()
            try:
                rc = x.main(self.argv(td, "--v-constancy-json", str(samples)))
                self.assertEqual(rc, 0)
                rep = json.loads((td / "o" / "report.json").read_text())
                self.assertFalse(rep["partial"])
                self.assertEqual(rep["schema"], x.SCHEMA)
                self.assertEqual(len(rep["folds"]), len(NON_P1) + len(P1))
                for log in (td / "ops.jsonl", td / "canon.jsonl"):
                    st = [json.loads(line)["config"]["status"] for line in log.read_text().splitlines()]
                    self.assertEqual(st.count("started"), 1)
                    self.assertIn("completed", st)
                self.assertTrue((td / "o" / "RUN.record.json").exists())
                self.assertEqual(x.main(self.argv(td, "--v-constancy-json", str(samples))), 2)  # the try is spent
            finally:
                for p in patches:
                    p.stop()

    def test_refusal_after_started_logs_the_failure_status(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            samples = td / "c.json"
            samples.write_text("[]")
            cells = [cell_rec(i, d, status="FILLED") for d in NON_P1 for i in range(6)]  # P1 dates missing: the screen runs; force the refusal in run_screen
            g = {"with_p4": True, "vmap_sha256": "v", "g1": {}, "g2": {}, "g3": {}, "g4": {}}
            with mock.patch.object(x, "run_guards", return_value=g), mock.patch.object(e16, "load_pinned_vmap", return_value={}), mock.patch.object(e16, "check_v_constancy", return_value={}), \
                    mock.patch.object(e16, "git_state", return_value={"head": "h", "dirty_tools": False}), mock.patch.object(x, "collect", return_value=([{"tag": "S", "cells": cells}], {})), \
                    mock.patch.object(x, "load_oof", return_value=({}, 0, 0, {})), mock.patch.object(e16, "frozen_flags", return_value=[True] * len(cells)), \
                    mock.patch.object(e16, "pre_started_counts", return_value={}), mock.patch.object(e16, "oof_without_cell", return_value={}), mock.patch.object(x, "enforce_limits"), \
                    mock.patch.object(e16, "v_coverage", return_value={}), mock.patch.object(e16, "check_constancy_sample"), mock.patch.object(e16, "set_process_workers"), \
                    mock.patch.object(x, "run_screen", side_effect=x.Refused("fold cannot train")):
                rc = x.main(self.argv(td, "--v-constancy-json", str(samples)))
            self.assertEqual(rc, 2)
            st = [json.loads(line)["config"]["status"] for line in (td / "ops.jsonl").read_text().splitlines()]
            self.assertIn("started", st)
            self.assertIn("refused_after_read", st)
            self.assertNotIn("completed", st)


class PrecountTests(unittest.TestCase):
    def test_precount_prints_counts_only_and_writes_no_tries_or_lock(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cells = [cell_rec(i, d, status="FILLED" if i % 2 == 0 else "MISS", net0=777_000 + i, rugged=True) for d in NON_P1 + P1 for i in range(6)]
            res = {"tag": "P2", "cells": cells, "n_migrations": 100, "no_create_row": [], "no_pool_mints": [], "no_migration_slot": [], "no_bonding_excluded": [],
                   "gate": {"foreign_first_mints": [], "mints_with_foreign_pool_prints": 0}, "slot_inversions": 0, "pool_vs_canonical": None,
                   "pre_tape_migration_excluded": [], "gap_excluded": [], "create_stats": None, "p1b_gap_slots": None}
            g = {"with_p4": True, "vmap_sha256": None, "g1": {}, "g2": {}, "g3": {}, "g4": {}}
            args = SimpleNamespace(vmap=str(td / "v.json"), v_fallback_json=None, closed_pools_json=None, artifact_dir=td, out_dir=td / "o", v_constancy_json=None, max_workers=2,
                                   p1_fast_dir="a", p1_oracle_insample_dir="b", p1_oracle_live_dir=None, p3_root="d", p2_view_dir=["e"], p4_view_dir=None)
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with mock.patch.object(e16, "VMAP_EXP016_SHA256", "PENDING"), mock.patch.object(x, "run_guards", return_value=g), mock.patch.object(e16, "set_process_workers"), mock.patch.object(x, "collect", return_value=([res], {})), \
                    mock.patch("tools.pumpswap_virtual.load_map", return_value={}), mock.patch.object(x, "load_oof", return_value=({}, 0, 0, {})), \
                    mock.patch.object(e16, "frozen_flags", return_value=[i % 2 == 0 for i in range(len(cells))]), mock.patch.object(e16, "oof_without_cell", return_value={}), \
                    mock.patch.object(x, "check_limits", return_value=[]), mock.patch.object(e16, "source_counts", return_value={}), \
                    mock.patch.object(e16, "pre_started_counts", return_value={"n_cells": len(cells)}), redirect_stdout(buf):
                rc = x.precount(args)
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            rec = json.loads(out)
            self.assertEqual(rec["mode"], "precount")
            self.assertEqual(rec["table_counts"]["n_rows"], len(cells))
            self.assertEqual(rec["table_counts"]["frozen_selected_non_p1"], 27 * 3)
            self.assertEqual(rec["table_counts"]["non_p1_dates_with_frozen_pick"], 27)
            self.assertTrue(any("frozen-selected" in w for w in rec["would_refuse"]))  # 81 < 100, pre-declared
            self.assertNotIn("777", out)  # no net value is printed
            for banned in ('"press"', '"flat"', '"rug"', '"net'):
                self.assertNotIn(banned, out)
            self.assertTrue((td / "o" / "precount.json").exists())
            self.assertFalse((td / "o" / "RUN.lock").exists())
            self.assertEqual([p.name for p in td.iterdir() if p.suffix == ".jsonl"], [])


def _res(tag, *, n_mig=100, no_create=0, first=(100, 10), after=(100, 1), span_h=72, cells=40):
    """A source result in the shape `check_limits` reads: windowed counts and the censoring profile ((n, no_create) per side)."""
    mig_ms = e15.date_start_ms(P1[0]) + 12 * HOUR
    return {"tag": tag, "cells": [{"status": "FILLED", "pool": f"p{i}", "mint": f"m{tag}{i}", "block": "P1", "mig_ms": mig_ms} for i in range(cells)], "n_migrations": n_mig,
            "n_migrations_window": n_mig, "no_create_row": ["a"] * no_create, "no_create_window": no_create, "no_pool_mints": [], "no_pool_window": 0, "no_migration_slot": [],
            "no_bonding_excluded": [], "n_creates_with_migration": n_mig, "n_creates": n_mig, "create_stats": None, "p1b_gap_slots": None,
            "gate": {"foreign_first_mints": [], "mints_with_foreign_pool_prints": 0}, "slot_inversions": 0, "pool_vs_canonical": None, "pre_tape_migration_excluded": [],
            "gap_excluded": [], "p1b_cap_denominator": 100, "p1b_canonical_no_create": 0, "n_cells_window": n_mig, "n_window_with_create": n_mig - no_create,
            "censoring": {"start_ms": 0, "span_h": span_h, "first": {"n": first[0], "no_create": first[1]}, "after": {"n": after[0], "no_create": after[1]}}}


class Amendment3Tests(unittest.TestCase):
    def test_no_create_limit_is_8_percent_for_exp021_only(self):
        self.assertEqual(x.LIMIT_NO_CREATE_021, 0.08)
        self.assertEqual(x.check_limits([_res("P3", no_create=8)]), [])  # 8% is at the limit
        (why,) = x.check_limits([_res("P3", no_create=9)])
        self.assertIn("no-create-row 9 of 100", why)
        self.assertIn("8%", why)

    def test_job_349_shares_pass(self):
        for tag, k, n in (("P1A", 194, 3415), ("P1C", 104, 3590), ("P2", 408, 15278), ("P3", 414, 6423), ("P4", 312, 6588)):
            self.assertEqual([w for w in x.check_limits([_res(tag, n_mig=n, no_create=k)]) if "no-create" in w], [], tag)

    def test_exp016_limit_is_unchanged(self):
        self.assertEqual(e16.LIMIT_NO_CREATE, 0.02)
        x.check_limits([_res("P3", no_create=5)])
        self.assertEqual(e16.LIMIT_NO_CREATE, 0.02)  # restored after the call
        (why,) = e16.check_limits([_res("P3", no_create=3)])  # EXP-016's own check still refuses at 3%
        self.assertIn("no-create-row", why)
        with self.assertRaises(RuntimeError), x.no_create_limit(0.5):
            raise RuntimeError
        self.assertEqual(e16.LIMIT_NO_CREATE, 0.02)

    def test_signature_passes_when_the_first_day_is_higher(self):
        self.assertEqual(x.check_censoring_signature([_res("P3", first=(100, 16), after=(500, 25))]), [])

    def test_signature_refuses_when_not_above(self):
        (w,) = x.check_censoring_signature([_res("P3", first=(100, 5), after=(500, 25))])  # 5% vs 5%
        self.assertIn("not a censoring signature", w)
        self.assertTrue(x.check_censoring_signature([_res("P2", first=(100, 2), after=(500, 25))]))

    def test_signature_is_wired_into_check_limits(self):
        why = x.check_limits([_res("P3", no_create=4, first=(100, 1), after=(100, 9))])
        self.assertEqual(len(why), 1)
        self.assertIn("P3", why[0])

    def test_short_p1_source_is_exempt_from_the_signature_only(self):
        short = _res("P1C", first=(50, 0), after=(0, 0), span_h=40)
        self.assertEqual(x.check_censoring_signature([short]), [])
        self.assertTrue(x.check_censoring_signature([_res("P1C", first=(50, 0), after=(50, 5), span_h=72)]))  # long enough: checked
        self.assertTrue(x.check_censoring_signature([_res("P2", first=(0, 0), after=(0, 0), span_h=40)]))  # not P1: never exempt
        self.assertTrue(any("no-create-row" in w for w in x.check_limits([dict(short, no_create_window=20, no_create_row=["a"] * 20)])))  # the 8% limit still applies

    def test_censoring_profile_counts_first_24h_and_after(self):
        a = e15.hour_ms(e15.BLOCKS["P3"][0])
        bt = lambda h: (a + h * HOUR) // 1000 + 5
        migs = {f"m{i}": {"mint": f"m{i}", "pool": "p", "block_time": bt(h)} for i, h in enumerate((1, 2, 3, 30, 31, 40, 50, 60))}
        src = SimpleNamespace(block="P3", migrations=migs, creates={"m3": {}, "m4": {}, "m5": {}, "m6": {}, "m7": {}}, excluded_no_bonding=[], excluded_other={})
        pr = x.censoring_profile(src, [e15.BLOCKS["P3"][0], e15.BLOCKS["P3"][1]])
        self.assertEqual((pr["first"], pr["after"]), ({"n": 3, "no_create": 3}, {"n": 5, "no_create": 0}))
        src.excluded_other = {"pre_tape_create": ["m0"]}
        self.assertEqual(x.censoring_profile(src, [e15.BLOCKS["P3"][0]])["first"]["no_create"], 2)  # an excluded mint is not a no-create

    def test_p1b_is_refused_and_not_a_source(self):
        args = SimpleNamespace(p1_oracle_live_dir="/x/oracle-live-2026-09-25_27", max_workers=4)
        with self.assertRaises(x.Refused) as cm:
            x.run_guards(args)
        self.assertIn("P1B excluded (Amendment 3)", str(cm.exception))
        a = x._parser().parse_args(["--p1-fast-dir", "a", "--p1-oracle-insample-dir", "b", "--out-dir", "o"])  # no live dir needed
        self.assertIsNone(a.p1_oracle_live_dir)

    def test_main_refuses_a_given_live_dir_before_any_row(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(x, "collect") as col:
            argv = ["--p1-fast-dir", "a", "--p1-oracle-insample-dir", "b", "--p1-oracle-live-dir", "c", "--p2-view-dir", "d", "--out-dir", str(Path(td) / "o"),
                    "--tries-log", str(Path(td) / "ops.jsonl"), "--canonical-tries", str(Path(td) / "canon.jsonl"), "--v-constancy-json", str(Path(td) / "c.json")]
            self.assertEqual(x.main(argv), 2)
            self.assertEqual(x.main(argv + ["--precount"]), 2)
            col.assert_not_called()
            self.assertFalse((Path(td) / "ops.jsonl").exists())

    def test_build_sources_has_no_p1b(self):
        g1 = {"roots": {"fast": Path("/f"), "insample": Path("/i")}}
        with mock.patch.object(e16, "build_sources", side_effect=lambda g: [(t, "P1", None, [], []) for t in ("P1A", "P1C", "P1B")] if "live" in g["g1"]["roots"] else []):
            self.assertEqual([s[0] for s in x.build_sources({"g1": g1})], ["P1A", "P1C"])

    def test_kept_oof_dates_exclude_p1b_only_dates(self):
        d = x.p1_kept_dates()
        self.assertTrue(set(d) <= set(P1))
        self.assertIn(P1[0], d)
        self.assertNotIn(P1[-1], d)  # the last P1 date is P1B's (oracle-live-2026-09-25_27)

    def test_report_records_the_exclusion(self):
        md = x.render_md({"first_line": "f", "banner": "b", "decision": {"outcome": "o"}, "p1b": x.P1B_EXCLUDED})
        self.assertIn("P1B excluded (Amendment 3)", md)

    def test_real_vmap_pin_is_in_the_code(self):
        self.assertEqual(e16.VMAP_EXP016_SHA256, "1f3e772d12cedbdb2dd860f619361fc0fdc88872fd5fa68639a11f91945162ec")


class FreezeTests(unittest.TestCase):
    HEAD = "a" * 40
    VSHA = "1f3e772d12cedbdb2dd860f619361fc0fdc88872fd5fa68639a11f91945162ec"

    @staticmethod
    def fixture():
        rows = []
        dates = NON_P1[:6] + P1[:2]
        for di, d in enumerate(dates):
            for i in range(12):
                r = mk_row(i, d, net=(1_000_000 if (i + di) % 3 else -1_000_000), filled=(i % 5 != 0), held=(i % 4) / 4, dumps=float(i % 2), ef0=float(i))
                r["source"] = "P1A" if d in P1 else "P2"
                rows.append(r)
        return rows

    INPUTS = {"v_fallback_json_sha256": None, "args_hash": "d" * 64, "oof_scores_sha256": "e" * 64, "view_sha256": {"P1": {"fast": "f" * 64}}}

    def freeze(self, d, **kw):
        a = dict(head=self.HEAD, vmap_sha256=self.VSHA, with_p4=True, inputs=self.INPUTS)
        a.update(kw)
        return x.freeze_models(self.fixture(), Path(d), **a)

    def test_two_runs_are_bit_identical(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ma, mb = self.freeze(a), self.freeze(b)
            self.assertEqual(ma, mb)
            for n in ("model.txt", "control-model.txt", "model.md5", "control-model.md5", "features.json", "control-features.json", x.FREEZE_MANIFEST):
                self.assertEqual((Path(a) / n).read_bytes(), (Path(b) / n).read_bytes(), n)
            for arm, f in (("rug", "model.txt"), ("control", "control-model.txt")):
                md5 = __import__("hashlib").md5((Path(a) / f).read_bytes()).hexdigest()
                self.assertEqual(ma["models"][arm]["model_md5"], md5)
            self.assertNotEqual(ma["models"]["rug"]["model_md5"], ma["models"]["control"]["model_md5"])

    def test_artifacts_describe_the_recipe(self):
        with tempfile.TemporaryDirectory() as a:
            m = self.freeze(a)
            self.assertEqual(json.loads((Path(a) / "features.json").read_text())["feature_names"], x.FEATURES_RUG)
            self.assertEqual(json.loads((Path(a) / "control-features.json").read_text())["feature_names"], x.FEATURES_CONTROL)
            self.assertEqual((m["code_head"], m["vmap_sha256"]), (self.HEAD, self.VSHA))
            self.assertEqual(m["n_rows_total"], 96)
            self.assertEqual(m["n_rows_by_source"], {"P1A": 24, "P2": 72})
            self.assertEqual((m["learner"]["seed"], m["learner"]["deterministic"]), (1, True))
            self.assertEqual(len(m["universe_sha256"]), 64)
            self.assertIn("num_features=34", (Path(a) / "model.txt").read_text().replace("max_feature_idx=33", "num_features=34"))
            self.assertIn("max_feature_idx=17", (Path(a) / "control-model.txt").read_text())

    def test_manifest_records_inputs_and_environment(self):
        import platform

        with tempfile.TemporaryDirectory() as a:
            m = self.freeze(a)
            for k in ("v_fallback_json_sha256", "args_hash", "oof_scores_sha256", "view_sha256", "lightgbm_version", "numpy_version", "machine", "python_version"):
                self.assertIn(k, m)
            self.assertIsNone(m["v_fallback_json_sha256"])
            self.assertEqual(m["machine"], platform.machine())
            self.assertNotIn("n_positive_label", m)
        with tempfile.TemporaryDirectory() as a:
            with self.assertRaises(x.Refused):
                self.freeze(a, inputs=None)
            with self.assertRaises(x.Refused):
                self.freeze(a, inputs={"args_hash": "x"})

    def test_refuses_without_the_pin(self):
        with tempfile.TemporaryDirectory() as a, mock.patch.object(e16, "VMAP_EXP016_SHA256", "PENDING"):
            with self.assertRaises(x.Refused):
                self.freeze(a)
            self.assertFalse((Path(a) / "model.txt").exists())

    def test_refuses_overwrite_missing_p4_bad_source_and_one_class(self):
        with tempfile.TemporaryDirectory() as a:
            self.freeze(a)
            with self.assertRaises(x.Refused):
                self.freeze(a)
        with tempfile.TemporaryDirectory() as a:
            with self.assertRaises(x.Refused):
                self.freeze(a, with_p4=False)
            with self.assertRaises(x.Refused):
                self.freeze(a, vmap_sha256=None)
            bad = self.fixture()
            bad[0]["source"] = "P1B"
            with self.assertRaises(x.Refused):
                x.freeze_models(bad, Path(a), head=self.HEAD, vmap_sha256=self.VSHA, inputs=self.INPUTS)
            one = [dict(r, press=1.0, filled=True) for r in self.fixture()]
            with self.assertRaises(x.Refused):
                x.freeze_models(one, Path(a), head=self.HEAD, vmap_sha256=self.VSHA, inputs=self.INPUTS)

    def test_writes_no_tries_line(self):
        with tempfile.TemporaryDirectory() as a, mock.patch("tools.mal_result.append_try") as ap:
            self.freeze(a)
            ap.assert_not_called()

    def test_confirm_is_a_refusing_stub(self):
        with mock.patch.object(x, "collect") as col, mock.patch.object(x, "run_guards") as gd:
            self.assertEqual(x.main(["--confirm"]), 2)
            col.assert_not_called()
            gd.assert_not_called()

    def test_freeze_main_refuses_p1b_argument(self):
        with tempfile.TemporaryDirectory() as a:
            rc = x.main(["--freeze", a, "--p1-fast-dir", "x", "--p1-oracle-insample-dir", "y", "--p1-oracle-live-dir", "z", "--p2-view-dir", "w"])
            self.assertEqual(rc, 2)
            self.assertEqual(list(Path(a).iterdir()), [])

    def test_freeze_source_has_no_data_path_or_tries_call(self):
        src = Path(x.__file__).read_text(encoding="utf-8")
        body = src[src.index("def freeze_models"):src.index("def confirm_main")]
        self.assertNotIn("log_all", body)
        self.assertNotIn("append_try", body)


class PreregTests(unittest.TestCase):
    TEXT = (x.REPO_ROOT / "EXP" / "EXP-021-part1-prereg.md").read_text(encoding="utf-8")

    def test_pins_match_the_code(self):
        for sv in ("fresh-0802", "[2026-08-02T12, 2026-08-08T12)", "10,000 draws, seed 1", "p < 0.025", "exit lag 2", "0.05, 0.25 and 0.5 SOL", "tp50_sl30",
                   "0.8030766588450794", "8%", "100 frozen picks", "refusing stub", "No k2 cell", "+22.9 bps at 0.25 SOL and +51.6 bps at 0.5 SOL", "p < 0.025 / 11", "0.00227", "DEC-014", "EXP021_V_FALLBACK_SHA256: none", "mal-research-0", "x86_64", "1/128", "EXP021_FROZEN_MD5: PENDING"):
            self.assertIn(sv, self.TEXT)
        self.assertEqual(x.FAMILY_ALPHA, 0.025)
        self.assertEqual(x.LIMIT_NO_CREATE_021, 0.08)
        self.assertEqual(x.MIN_FROZEN_NON_P1, 100)
        for f in sum(x.FREEZE_FILES.values(), ()) + (x.FREEZE_MANIFEST,):
            self.assertIn(f, self.TEXT)
        thr = json.loads((x.REPO_ROOT / "ARTIFACTS" / "exp012" / "threshold.json").read_text())
        self.assertIn(repr(float(next(v for k, v in thr.items() if "threshold" in k and isinstance(v, float)))), self.TEXT)


    @staticmethod
    def pin_text(m5r, m5c, msha, fb="none"):
        return f"EXP021_FROZEN_MD5: {m5r}\nEXP021_CONTROL_MD5: {m5c}\nEXP021_TRAIN_MANIFEST_SHA256: {msha}\n{x.FALLBACK_KEY}: {fb}\n"

    def frozen(self, d):
        FreezeTests().freeze(d)
        h = __import__("hashlib")
        return (h.md5((Path(d) / "model.txt").read_bytes()).hexdigest(), h.md5((Path(d) / "control-model.txt").read_bytes()).hexdigest(),
                h.sha256((Path(d) / x.FREEZE_MANIFEST).read_bytes()).hexdigest())

    def test_shipped_pins_are_pending_and_refuse(self):
        with tempfile.TemporaryDirectory() as d:
            self.frozen(d)
            with self.assertRaises(x.Refused):
                x.check_freeze_pins(self.TEXT, d)

    def test_each_pin_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            r, c, m = self.frozen(d)
            ok = self.pin_text(r, c, m)
            self.assertEqual(x.check_freeze_pins(ok, d)["EXP021_FROZEN_MD5"], r)
            for bad in (ok.replace(r, "PENDING"), ok + f"EXP021_FROZEN_MD5: {r}\n", ok.replace(c, c.upper()), ok.replace(f"EXP021_CONTROL_MD5: {c}\n", ""),
                        self.pin_text("0" * 32, c, m), self.pin_text(r, "0" * 32, m), self.pin_text(r, c, "0" * 64)):
                with self.assertRaises(x.Refused, msg=bad):
                    x.check_freeze_pins(bad, d)
            with self.assertRaises(x.Refused):
                x.check_freeze_pins(ok, None)
            with self.assertRaises(x.Refused):
                x.check_freeze_pins(ok, Path(d) / "nope")
            # a manifest edited after its pin was taken
            (Path(d) / x.FREEZE_MANIFEST).write_text("{}\n")
            with self.assertRaises(x.Refused):
                x.check_freeze_pins(ok, d)

    def test_manifest_with_wrong_recorded_md5_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            r, c, _m = self.frozen(d)
            mp = Path(d) / x.FREEZE_MANIFEST
            man = json.loads(mp.read_text())
            man["models"]["rug"]["model_md5"] = "0" * 32
            mp.write_text(json.dumps(man, indent=2, sort_keys=True) + "\n")
            msha = __import__("hashlib").sha256(mp.read_bytes()).hexdigest()
            with self.assertRaises(x.Refused):
                x.check_freeze_pins(self.pin_text(r, c, msha), d)

    def test_part1_must_be_clean_against_head(self):
        fake = lambda out: mock.Mock(stdout=out)  # noqa: E731
        with mock.patch("subprocess.run", return_value=fake(" M EXP/EXP-021-part1-prereg.md\n")):
            with self.assertRaises(x.Refused):
                x.part1_git_blob()
        with mock.patch("subprocess.run", side_effect=[fake(""), fake("EXP/EXP-021-part1-prereg.md"), fake("c" * 40)]):
            self.assertEqual(x.part1_git_blob(), "c" * 40)
        with mock.patch("subprocess.run", side_effect=[fake(""), fake("")]):  # not tracked
            with self.assertRaises(x.Refused):
                x.part1_git_blob()

    def test_fallback_pin(self):
        t = f"{x.FALLBACK_KEY}: none\n"
        x.check_fallback_pin(None, t)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "fb.json"
            f.write_text("{}")
            with self.assertRaises(x.Refused):
                x.check_fallback_pin(f, t)
            sha = __import__("hashlib").sha256(b"{}").hexdigest()
            x.check_fallback_pin(f, f"{x.FALLBACK_KEY}: {sha}\n")
            with self.assertRaises(x.Refused):
                x.check_fallback_pin(None, f"{x.FALLBACK_KEY}: {sha}\n")
        for bad in ("", t + t, f"{x.FALLBACK_KEY}: PENDING\n"):
            with self.assertRaises(x.Refused):
                x.check_fallback_pin(None, bad)
        x.check_fallback_pin(None, self.TEXT)  # Part 1 ships `none`

    def test_screen_main_refuses_before_guards_without_pins(self):
        with mock.patch.object(x, "run_guards") as gd:
            rc = x.main(["--p1-fast-dir", "a", "--p1-oracle-insample-dir", "b", "--p2-view-dir", "c", "--out-dir", "o", "--v-constancy-json", "v"])
            self.assertEqual(rc, 2)
            gd.assert_not_called()

    def test_power_figures_in_the_text_are_what_boot_p_gives(self):
        import tools.exp017_screen as e17

        def p(vals):
            return e17.boot_p({f"d{i}": [v] for i, v in enumerate(vals)})

        for vals, want in (([1] * 7, 0.0001), ([1] * 6 + [-1], 0.0119), ([1] * 6 + [-2], 0.0657), ([1] * 5 + [-0.5] * 2, 0.0207)):
            self.assertAlmostEqual(p(vals), want, places=4)
            self.assertIn(f"{want}", self.TEXT)

    def test_signflip_p_is_exact(self):
        self.assertEqual(x.signflip_p([1.0] * 7), 1 / 128)
        self.assertEqual(x.signflip_p([1.0, -1.0]), 3 / 4)  # totals 2, 0, 0, -2: three are >= 0
        self.assertIsNone(x.signflip_p([1.0] * 21))
        self.assertIn("signflip_p_one_sided", x.paired_stats({"d1": [1.0], "d2": [2.0]}, 2))


if __name__ == "__main__":
    unittest.main()
