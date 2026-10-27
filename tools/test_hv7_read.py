"""Synthetic-only tests for tools/hv7_read.py (HV7 chunk C5). No tape, no drill or job output, no arena path."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tools import hv7_read as X
from tools import hv7_runner as R

DAY = 86_400_000
T0 = 1_792_000_000_000 // DAY * DAY  # an arbitrary synthetic 00:00Z
SEND = 1_000_000.0


def trade(ms, pool, pnl, *, kind="trade", pid=None):
    pid = pid or f"p{ms}-{pool}"
    by = {"pnl_nofail": float(pnl), "filled": True, "strand": False}
    return {"kind": kind, "position_id": pid, "trade_id": pid, "decision_ms": {"entry": ms}, "pool": pool, "stack": {"send": SEND},
            "calc": {"1p9": {"by_rule": {"sc": dict(by), "total": dict(by)}, "ssb": 5.0, "nearby_lamports": 1e9}}, "v_src": {"entry": "x", "exit": "x", "span": {}}}


def opened(ms, pool, pid=None):
    return {"kind": "open", "position_id": pid or f"p{ms}-{pool}", "decision_ms": {"entry": ms}}


def book(days, per_day, pnl_fn, arm_pool=lambda i: "a"):
    """rows + entry stamps: per_day periods a day at t_e = day + i hours."""
    rows, ems = [], []
    for d in range(days):
        for i in range(per_day):
            ms = T0 + d * DAY + (i + 1) * 3_600_000
            ems.append(ms)
            rows += [opened(ms, arm_pool(i)), trade(ms, arm_pool(i), pnl_fn(d, i))]
    return rows, ems


class SignFlip(unittest.TestCase):
    def test_d5_min_p(self):
        self.assertEqual(X.sign_flip_min_p(5), 0.03125)
        self.assertEqual(X.sign_flip_p([1.0] * 5), 0.03125)
        self.assertGreater(X.sign_flip_p([1.0] * 5), X.CONTRAST_ALPHA)  # power at B is exactly 0
        self.assertEqual(X.sign_flip_p([1.0] * 6), 0.015625)
        self.assertLessEqual(X.sign_flip_p([1.0] * 6), X.CONTRAST_ALPHA)

    def test_zero_days_do_not_change_p(self):
        self.assertEqual(X.sign_flip_p([1.0] * 6 + [0.0] * 4), 0.015625)


class Helpers(unittest.TestCase):
    def test_horizon(self):
        end = T0 + 10 * DAY
        self.assertEqual(X.horizon_days(20, T0, end), 10)  # ceil(150/20) = 8 -> max 14 -> capped by the window end
        self.assertEqual(X.horizon_days(20, T0, T0 + 100 * DAY), 14)
        self.assertEqual(X.horizon_days(1, T0, T0 + 500 * DAY), 90)
        self.assertEqual(X.horizon_days(None, T0, end, override=9), 9)
        with self.assertRaises(X.Refused):
            X.horizon_days(None, T0, end)

    def test_day_block_boot_constant_and_blocks(self):
        x = [0.01] * 12
        days = ["d1"] * 4 + ["d2"] * 4 + ["d3"] * 4
        self.assertAlmostEqual(X.day_block_boot_lo(x, days), 0.01)
        # one day carries all the profit: some resamples drop it, so the 5th percentile is below the plain mean
        x2 = [0.1] * 4 + [-0.001] * 8
        lo = X.day_block_boot_lo(x2, days)
        self.assertLess(lo, 0.0)
        self.assertEqual(lo, X.day_block_boot_lo(x2, days))  # seed 1, deterministic

    def test_mde_line(self):
        s = X.mde_line(10)
        self.assertIn("1.9%", s)
        self.assertIn("0.17", s)
        self.assertIn("no skill detected", s)
        self.assertIn("not tabulated", X.mde_line(9))
        self.assertAlmostEqual(X.mde_ic(1.9, 8), 0.1665, places=3)

    def test_holm_block(self):
        h = X.holm_block(30, {"a": 0.01, "b": 0.2})
        self.assertAlmostEqual(h["plain_line_1_minus_0.95_pow_N"], 1 - 0.95 ** 30)
        self.assertEqual(h["holm"]["a"]["rank"], 1)
        self.assertAlmostEqual(h["holm"]["a"]["holm_level"], 0.05 / 2)
        self.assertAlmostEqual(h["holm"]["b"]["holm_level"], 0.05)


class Gate(unittest.TestCase):
    def setUp(self):
        self.good_rows, self.ems = book(6, 20, lambda d, i: 5e7 + 1e6 * ((i * 7 + d) % 5))  # 120 attempts on 6 days, steady winners
        self.b = X.AS.blind_read_instant(self.ems, T0)

    def test_blind_boundary_is_known(self):
        # 20 a day: 100 attempts and 5 dates are first met at the end of day 5 -> B = T0 + 5 d
        self.assertEqual(self.b, T0 + 5 * DAY)

    def test_refused_before_b(self):
        few = [m for m in self.ems if m < T0 + 4 * DAY]
        with self.assertRaises(X.Refused):
            X.gate_read("L", now_ms=T0 + 10 * DAY, clock_start_ms=T0, entry_decisions_ms=few, rows=self.good_rows)

    def test_refused_before_b_plus_exit_window(self):
        with self.assertRaises(X.Refused):
            X.gate_read("L", now_ms=self.b + X.EXIT_WINDOW_MS - 1, clock_start_ms=T0, entry_decisions_ms=self.ems, rows=self.good_rows)
        X.gate_read("L", now_ms=self.b + X.EXIT_WINDOW_MS, clock_start_ms=T0, entry_decisions_ms=self.ems, rows=self.good_rows)

    def test_refused_with_open_attempt(self):
        rows = self.good_rows + [opened(T0 + 4 * DAY + 5, "z")]
        with self.assertRaises(X.Refused):
            X.gate_read("L", now_ms=self.b + DAY, clock_start_ms=T0, entry_decisions_ms=self.ems + [T0 + 4 * DAY + 5], rows=rows)

    def test_r_and_u_have_no_gate(self):
        with self.assertRaises(X.Refused):
            X.gate_read("U", now_ms=self.b + DAY, clock_start_ms=T0, entry_decisions_ms=self.ems, rows=self.good_rows)

    def test_pass_known_answer(self):
        r = X.gate_read("L", now_ms=self.b + DAY, clock_start_ms=T0, entry_decisions_ms=self.ems, rows=self.good_rows)
        self.assertEqual(r["status"], "PASS", r.get("cells"))
        self.assertEqual(set(r["cells"]), set(X.CELLS))
        c = r["cells"]["flat_1p9_sc"]
        self.assertEqual(c["n"], 100)
        self.assertEqual(c["days"], 5)
        self.assertEqual(c["days_pos"], 5)
        self.assertGreater(c["ci90_lo_sol"], 0)
        self.assertGreater(c["ex_top3_sol"], 0)
        self.assertTrue(c["day_block_ci90_lo_sol"]["report_only"])
        self.assertTrue(r["no_book_has_cleared_the_promotion_gate"])

    def test_only_attempts_before_b_count(self):
        r = X.gate_read("L", now_ms=self.b + DAY, clock_start_ms=T0, entry_decisions_ms=self.ems, rows=self.good_rows)
        self.assertEqual(r["priced_trades"], 100)  # the day-6 trades are after B

    def test_losing_book_fails(self):
        rows, ems = book(6, 20, lambda d, i: -2e7)
        r = X.gate_read("L", now_ms=T0 + 8 * DAY, clock_start_ms=T0, entry_decisions_ms=ems, rows=rows)
        self.assertEqual(r["status"], "FAIL")
        self.assertTrue(all(not c["pass"] for c in r["cells"].values()))

    def test_top3_dependence_fails(self):
        # three huge winners, everything else slightly negative: total > 0 but not after removing the top 3
        rows, ems = book(6, 20, lambda d, i: 3e9 if (d, i) in {(0, 0), (1, 1), (2, 2)} else -1e7)
        r = X.gate_read("L", now_ms=T0 + 8 * DAY, clock_start_ms=T0, entry_decisions_ms=ems, rows=rows)
        self.assertEqual(r["status"], "FAIL")
        self.assertTrue(any("ex-top-3" in m for c in r["cells"].values() for m in c["misses"]))

    def test_minority_positive_days_fail(self):
        # day 0 is a big win, days 1-4 small losses: positive total, positive mean, but 1 of 5 days positive
        rows, ems = book(6, 20, lambda d, i: 4e8 if d == 0 else -1e6)
        r = X.gate_read("L", now_ms=T0 + 8 * DAY, clock_start_ms=T0, entry_decisions_ms=ems, rows=rows)
        self.assertEqual(r["status"], "FAIL")
        self.assertTrue(any("not a majority" in m for c in r["cells"].values() for m in c["misses"]))

    def test_not_decidable_when_b_after_horizon(self):
        few = [m for m in self.ems if m < T0 + 3 * DAY]
        r = X.gate_read("L", now_ms=T0 + 12 * DAY, clock_start_ms=T0, entry_decisions_ms=few, rows=[], horizon_end_ms=T0 + 10 * DAY)
        self.assertEqual(r["status"], "NOT_DECIDABLE")

    def test_fallback_over_ten_percent_is_not_evaluable_for_l_only(self):
        fb = {"L_not_evaluable": True, "all_not_evaluable": False, "reason": "E_fallback"}
        self.assertEqual(X.gate_read("L", now_ms=self.b + DAY, clock_start_ms=T0, entry_decisions_ms=self.ems, rows=self.good_rows, fallback=fb)["status"], "NOT_EVALUABLE")
        self.assertEqual(X.gate_read("S", now_ms=self.b + DAY, clock_start_ms=T0, entry_decisions_ms=self.ems, rows=self.good_rows, fallback=fb)["status"], "PASS")


def arm_rows(days, n_per_day, pnl_fn, pools=("a",)):
    rows = []
    for d in range(days):
        for i in range(n_per_day):
            ms = T0 + d * DAY + (i + 1) * 3_600_000
            for pool in pools:
                rows += [opened(ms, pool), trade(ms, pool, pnl_fn(d, i, pool))]
    return rows


FB_OK = {"L_not_evaluable": False, "all_not_evaluable": False, "reason": None, "fallback_share": 0.0}


def rank_row(ms_te, order):
    return {"kind": "rank", "ms": ms_te - X.T_E_MINUS_T_D_MS, "fallback": False, "order": order}


class Contrast(unittest.TestCase):
    POOLS = ("a", "b", "c", "d")
    PNL = {"a": 3e7, "b": 1e7, "c": -1e7, "d": -3e7}

    def build(self, days, l_pick, noise=0.0):
        u = arm_rows(days, 4, lambda d, i, p: self.PNL[p] + noise * ((d + i) % 3), pools=self.POOLS)
        l = arm_rows(days, 4, lambda d, i, p: self.PNL[p] + noise * ((d + i) % 3), pools=(l_pick,))
        r = arm_rows(days, 4, lambda d, i, p: self.PNL[p] + noise * ((d + i) % 3), pools=("c",))
        s = arm_rows(days, 4, lambda d, i, p: self.PNL[p] + noise * ((d + i) % 3), pools=("b",))
        return {"L": l, "R": r, "S": s, "U": u}

    def read(self, rows, days=10, now=None, fb=FB_OK, ranks=()):
        h = T0 + days * DAY
        return X.contrast_read(now_ms=h + X.EXIT_WINDOW_MS if now is None else now, clock_start_ms=T0, horizon_end_ms=h, rows_by_arm=rows, l_rank_rows=ranks, fallback=fb, n_books=30)

    def test_refused_before_h_plus_exit_window(self):
        rows = self.build(10, "a")
        h = T0 + 10 * DAY
        with self.assertRaises(X.Refused):
            self.read(rows, now=h)
        with self.assertRaises(X.Refused):
            self.read(rows, now=h + X.EXIT_WINDOW_MS - 1)
        self.read(rows, now=h + X.EXIT_WINDOW_MS)

    def test_refused_with_unclosed_attempt(self):
        rows = self.build(10, "a")
        rows["L"].append(opened(T0 + 3 * DAY + 5, "a"))
        with self.assertRaises(X.Refused):
            self.read(rows)

    def test_skill_known_answer(self):
        # L always takes the best pool, U is the mean of all four: every daily difference is positive in all four sub-cells
        res = self.read(self.build(10, "a"))
        lu = res["contrasts"]["L-U"]
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["D"], 10)
        self.assertEqual(lu["min_nonzero_days"], 10)
        for s in lu["subcells"].values():
            self.assertEqual(s["p"], 2.0 ** -10)
            self.assertEqual(s["days_positive"], 10)
        self.assertTrue(res["a_skill_pass_is_not_a_promotion"])
        self.assertTrue(res["no_book_has_cleared_the_promotion_gate"])
        self.assertIn("not a promotion", res["verdict_text"])
        self.assertIn("MDE", res["mde"])

    def test_l_equals_u_expectation_is_not_a_pass(self):
        # L takes a pool whose pnl equals U's mean (-0.0) : differences are zero every day -> p = 1 -> NOT_PASS (10 days, but zero non-zero days)
        rows = {a: arm_rows(10, 4, lambda d, i, p: 0.0, pools=("a",) if a != "U" else self.POOLS) for a in X.ARMS}
        res = self.read(rows)
        self.assertIn(res["status"], ("NOT_DECIDABLE",))  # no non-zero days: contrast_days
        self.assertEqual(res["reason"], "contrast_days")

    def test_worse_than_random_not_pass(self):
        res = self.read(self.build(10, "d"))
        self.assertEqual(res["status"], "NOT_PASS")
        self.assertIn("no skill detected", res["verdict_text"])

    def test_fewer_than_8_nonzero_days_not_decidable(self):
        res = self.read(self.build(7, "a"), days=7)
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertEqual(res["reason"], "contrast_days")
        self.assertEqual(res["contrasts"]["L-U"]["min_nonzero_days"], 7)
        self.assertEqual(res["sign_flip_min_p_at_D"], 2.0 ** -7)

    def test_d5_cannot_pass(self):
        res = self.read(self.build(5, "a"), days=5)
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertEqual(X.sign_flip_p([1.0] * 5), 0.03125)

    def test_secondaries_and_fault_check(self):
        res = self.read(self.build(10, "a"))
        c = res["contrasts"]
        self.assertEqual(set(c), {"L-U", "L-R", "L-S", "S-U", "R-U"})
        self.assertGreater(c["L-S"]["subcells"]["flat_1p9_sc"]["mean_daily_diff_sol"], 0)  # a beats b
        self.assertLess(c["R-U"]["subcells"]["flat_1p9_sc"]["mean_daily_diff_sol"], 0)  # c is below the U mean (pnl -1e7 vs mean 0)
        self.assertEqual(set(res["holm"]["holm"]), {"L-R", "L-S", "S-U", "R-U"})  # no rank rows given: no IC in the family
        self.assertNotIn("L-U", res["holm"]["holm"])
        self.assertEqual(res["holm"]["N"], 30)

    def test_same_pool_same_price_all_arms_zero_difference(self):
        # an arm that buys exactly U's pool set prices to a zero daily difference (A1: one shared pressure intercept)
        rows = self.build(10, "a")
        rows["R"] = arm_rows(10, 4, lambda d, i, p: self.PNL[p], pools=self.POOLS)
        res = self.read(rows)
        for s in res["contrasts"]["R-U"]["subcells"].values():
            self.assertAlmostEqual(s["total_diff_sol"], 0.0, places=12)
        self.assertFalse(res["R_minus_U_fault_flag"])

    def test_rank_ic_perfect_order(self):
        rows = self.build(10, "a")
        ranks = [rank_row(T0 + d * DAY + (i + 1) * 3_600_000, ["a", "b", "c", "d"]) for d in range(10) for i in range(4)]
        res = self.read(rows, ranks=ranks)
        self.assertAlmostEqual(res["rank_ic"]["mean_daily_ic"], 1.0)
        self.assertEqual(res["rank_ic"]["periods"], 40)
        self.assertEqual(res["rank_ic"]["p"], 2.0 ** -10)

    def test_fallback_not_evaluable_blocks_l_contrasts(self):
        fb = {"L_not_evaluable": True, "all_not_evaluable": False, "reason": "E_fallback"}
        res = self.read(self.build(10, "a"), fb=fb)
        self.assertEqual(res["contrasts"]["L-U"]["status"], "NOT_EVALUABLE")
        self.assertEqual(res["contrasts"]["S-U"]["status"] in ("PASS", "NOT_PASS", "NOT_DECIDABLE"), True)
        self.assertEqual(res["status"], "NOT_EVALUABLE")


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = R.READ_ROOTS
        R.READ_ROOTS = tuple(R.READ_ROOTS) + (str(self.root),)
        self.addCleanup(lambda: setattr(R, "READ_ROOTS", self._old))
        self.addCleanup(self.tmp.cleanup)

    def write(self, arm, rows):
        d = self.root / "decisions" / arm
        d.mkdir(parents=True, exist_ok=True)
        by_day = {}
        for r in rows:
            by_day.setdefault(X.utc_day(r["ms"]), []).append(r)
        for day, rs in by_day.items():
            (d / f"{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rs) + "\n")

    def test_fallback_check_and_entry_ms(self):
        ents = [{"kind": "entry", "ms": T0 + 3_600_000 * (i + 1)} for i in range(20)]
        ranks = [{"kind": "rank", "ms": T0 + 3_600_000 * (i + 1) - 45_000, "fallback": i < 3} for i in range(20)]  # 3/20 = 15%
        self.write("L", ents + ranks)
        self.assertEqual(len(X.entry_ms(self.root, "L")), 20)
        fb = X.fallback_check(self.root, T0, T0 + DAY)
        self.assertEqual(fb["fallbacks"], 3)
        self.assertTrue(fb["L_not_evaluable"])
        self.assertEqual(fb["reason"], "E_fallback")
        ranks2 = [dict(r, fallback=i < 2) for i, r in enumerate(ranks)]  # 10% exactly is not over
        self.write("L", ents + ranks2)
        self.assertFalse(X.fallback_check(self.root, T0, T0 + DAY)["L_not_evaluable"])

    def test_record_once(self):
        X.record_once(self.root / "reads", "contrast", {"a": 1})
        with self.assertRaises(X.Refused):
            X.record_once(self.root / "reads", "contrast", {"a": 2})

    def test_cli_refuses_before_b(self):
        import io

        self.write("L", [{"kind": "entry", "ms": T0 + 3_600_000}])
        out = io.StringIO()
        rc = X.main(["gate", "--arm", "L", "--root", str(self.root), "--ledger-root", str(self.root / "led"), "--registry", str(self.root / "reg.json"),
                     "--clock-start-ms", str(T0), "--window-end-ms", str(T0 + 10 * DAY), "--rate", "20", "--now-ms", str(T0 + 9 * DAY), "--n-books", "30",
                     "--record-dir", str(self.root / "reads")], out=out)
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertFalse((self.root / "reads").exists())

    def test_cli_refuses_contrast_early(self):
        rc = X.main(["contrast", "--root", str(self.root), "--ledger-root", str(self.root / "led"), "--registry", str(self.root / "reg.json"),
                     "--clock-start-ms", str(T0), "--window-end-ms", str(T0 + 10 * DAY), "--rate", "20", "--now-ms", str(T0 + 10 * DAY + 1), "--n-books", "30",
                     "--record-dir", str(self.root / "reads")])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
