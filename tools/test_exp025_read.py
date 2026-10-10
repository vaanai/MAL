"""Tests for tools/exp025_read.py (EXP-025 P4, the read tool). Fixtures only: nothing here opens /data/mal, a tape hour, a forward row,
walk 2, an October label or an outcome. The E0 on the real exploration day 2026-09-20 is `exp025_read.py e0` (a job), not a unit test."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None
try:
    import pandas as pd
    import duckdb
except ImportError:  # pragma: no cover
    pd = duckdb = None

if np is not None:
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    import exp025_read as R


@unittest.skipIf(np is None, "numpy missing")
class SealAndAllowlist(unittest.TestCase):
    def test_exploration_guard_refuses_october(self):
        g = R.ExplorationGuard()
        g.check_hour("exploration", "2026-09-20T05")
        for h in ("2026-10-01T00", "2026-10-09T03", "2026-10-16T01"):
            with self.assertRaises(R.Refusal) as c:
                g.check_hour("forward-1002ev", h)
            self.assertEqual(c.exception.code, "SEAL")

    def test_allowlist_look1_and_look2(self):
        self.assertTrue(R.hour_allowed(1, "forward-1002", "2026-10-02T15"))
        self.assertFalse(R.hour_allowed(1, "forward-1002", "2026-10-02T14"))
        self.assertFalse(R.hour_allowed(1, "forward-1002", "2026-10-09T00"))     # that hour is forward-1002ev's
        self.assertTrue(R.hour_allowed(1, "forward-1002ev", "2026-10-09T00"))
        self.assertTrue(R.hour_allowed(1, "forward-1016", "2026-10-17T01"))
        self.assertFalse(R.hour_allowed(1, "forward-1016", "2026-10-17T02"))     # EXP-022 counted hours past Look 1
        self.assertTrue(R.hour_allowed(2, "forward-1016", "2026-10-24T01"))
        self.assertFalse(R.hour_allowed(2, "forward-1016", "2026-10-24T02"))
        self.assertEqual(len(R.allowlisted_hours(1)), (R.ep("2026-10-17T02") - R.ep("2026-10-02T15")) // 3600)

    def test_look_guard_refuses_before_final_and_before_look_end(self):
        with tempfile.TemporaryDirectory() as d:
            mk, lg = os.path.join(d, "FINAL_WRITTEN"), os.path.join(d, "FINAL_READS.jsonl")
            with self.assertRaises(R.Refusal) as c:
                R.LookGuard(1, R.ep("2026-10-18T00"), mk, lg)
            self.assertEqual(c.exception.code, "SEAL")
            open(mk, "w").close()
            with self.assertRaises(R.Refusal):
                R.LookGuard(1, R.ep("2026-10-18T00"), mk, lg)                    # ledger empty
            open(lg, "w").write('{"x":1}\n')
            with self.assertRaises(R.Refusal):
                R.LookGuard(1, R.ep("2026-10-17T01"), mk, lg)                    # last allowlisted hour not ended
            g = R.LookGuard(1, R.ep("2026-10-17T02"), mk, lg)
            g.check_hour("forward-1016", "2026-10-17T01")
            with self.assertRaises(R.Refusal) as c:
                g.check_hour("forward-1016", "2026-10-17T02")
            self.assertEqual(c.exception.code, "R12")

    def test_closed_files(self):
        for p in ("/x/OUT/rows.jsonl", "/x/report.json", "/x/OUT/scratch/a.jsonl"):
            with self.assertRaises(R.Refusal):
                R.assert_not_closed(p)
        R.assert_not_closed("/x/tape/trades/2026-09-20T00.parquet")


@unittest.skipIf(np is None, "numpy missing")
class Preconditions(unittest.TestCase):
    def test_section0_lines_in_the_exp_file(self):
        txt = open(R.EXP_FILE).read()
        self.assertEqual(R.section0_lines(txt)["EXP025_ALPHA_LOOK1"], "0.005")
        with self.assertRaises(R.Refusal):
            R.section0_lines(txt + "\nEXP025_COUNT_START: 2026-10-10T00\n")     # duplicate
        with self.assertRaises(R.Refusal):
            R.section0_lines(txt.replace("EXP025_ALPHA_LOOK2: 0.020", "EXP025_ALPHA_LOOK2: 0.025"))

    def test_pins(self):
        got = R.check_pins()
        self.assertEqual(got["c1nf_cap.py"], "e0335aeba5a9abec509e5e70d1070c77fd9e524b31456cb6c9ebdd7fc4ba6ba6")

    @unittest.skipIf(shutil.which("patch") is None, "patch(1) missing")
    def test_apply_look_patch(self):
        for look in (1, 2):
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(R.apply_look_patch(look, d), R.LOOKS[look]["applied_sha"])
                self.assertTrue(os.path.exists(os.path.join(d, "scripts", "11_passA.py")))
                self.assertEqual(R.sha256(os.path.join(d, "scripts", "11_passA.py")), R.check_pins()["scripts/11_passA.py"])

    def test_decoder_blobs_and_e0_records(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.json")
            with self.assertRaises(R.Refusal):
                R.check_decoder_blobs(p)
            json.dump({"forward-1002ev": "a" * 40, "walk2": "b" * 40}, open(p, "w"))
            with self.assertRaises(R.Refusal) as c:
                R.check_decoder_blobs(p)
            self.assertEqual(c.exception.code, "R13")
            json.dump({"forward-1002ev": "a" * 40, "walk2": "a" * 40}, open(p, "w"))
            R.check_decoder_blobs(p)
            with self.assertRaises(R.Refusal):
                R.check_e0_records(d)
            for n in ("p3_e0.json", "p4_e0.json"):
                json.dump({"e0_pass": True}, open(os.path.join(d, n), "w"))
            R.check_e0_records(d)

    def test_lock_is_once(self):
        with tempfile.TemporaryDirectory() as d:
            old = R.LOOKS[1]["O"]
            R.LOOKS[1]["O"] = d
            try:
                R.take_lock(1, os.path.join(d, "LOOK_READS.jsonl"))
                with self.assertRaises(R.Refusal) as c:
                    R.take_lock(1, os.path.join(d, "LOOK_READS.jsonl"))
                self.assertEqual(c.exception.code, "LOCK")
            finally:
                R.LOOKS[1]["O"] = old


@unittest.skipIf(np is None, "numpy missing")
class OracleAndDecisions(unittest.TestCase):
    def test_oracle_boolean_only_and_scope(self):
        s = R.ep("2026-10-16T01") * 1000
        mints = ["a", "b", "c"]; fp = [s - 1, s, s + 5]
        keep, n = R.oracle_exclude(mints, fp, lambda m: m == "c")
        self.assertEqual(keep.tolist(), [True, True, False]); self.assertEqual(n, 1)
        for bad in (lambda m: 1, lambda m: None, lambda m: (_ for _ in ()).throw(RuntimeError())):
            with self.assertRaises(R.Refusal) as c:
                R.oracle_exclude(mints, fp, bad)
            self.assertEqual(c.exception.code, "R7")
        with self.assertRaises(R.Refusal):
            R.oracle_exclude(mints, fp, None)
        keep, _ = R.oracle_exclude(["a"], [s - 1], None)                       # nothing in scope: no oracle needed
        self.assertTrue(keep.all())

    def test_decisions_md5_is_order_free(self):
        self.assertEqual(R.decisions_md5(["a", "b"], [2, 1]), R.decisions_md5(["b", "a"], [1, 2]))
        self.assertNotEqual(R.decisions_md5(["a"], [1]), R.decisions_md5(["a"], [2]))

    def test_pipeline_commands(self):
        W, cmds = R.pipeline_commands(1)
        self.assertTrue(W.startswith("/data/mal/exp025/look1"))
        days = [c[2] for c in cmds if c[1].endswith("11_passA.py")]
        self.assertEqual(days[0], "2026-10-09"); self.assertEqual(days[-1], "2026-10-16")
        _, cmds2 = R.pipeline_commands(2)
        self.assertEqual([c[2] for c in cmds2 if c[1].endswith("11_passA.py")][-1], "2026-10-23")


@unittest.skipIf(np is None, "numpy missing")
class BookLegsStatsEqualV3(unittest.TestCase):
    """The tool's t_sf / stats / book / legs equal verify/v3_report.py's on the same inputs (P4: statistics equal v3_report's)."""

    def setUp(self):
        self.v3 = R.v3_functions()
        rng = np.random.default_rng(7)
        n = 400
        self.t = np.sort(rng.integers(R.ep("2026-09-03T12"), R.ep("2026-09-25T07"), n))
        self.mid = rng.integers(0, 60, n); self.xt = self.t + rng.integers(200, 900, n)
        self.sel = rng.random(n) < 0.7
        self.pnl = rng.normal(5e6, 4e7, n); self.g = rng.random(n) < 0.05
        self.ssb = rng.integers(0, 5, n).astype(float); self.nb = rng.random(n) * 3e9
        self.days = np.array([R.date_str(x) for x in self.t]); self.blk = np.where(self.t < R.ep("2026-09-15T12"), "a", "b")

    def test_book_equal(self):
        self.assertEqual(R.book(self.t, self.mid, self.xt, self.sel).tolist(), self.v3["book"](self.t, self.mid, self.xt, self.sel).tolist())

    def test_legs_and_stats_equal(self):
        for fee, rent in ((R.F505, R.RENT), (R.F55, 0)):
            a = R.legs(self.pnl, self.g, self.ssb, self.nb, fee, rent); b = self.v3["legs"](self.pnl, self.g, self.ssb, self.nb, fee, rent)
            for k in ("nofail", "flat", "press"):
                self.assertTrue(np.array_equal(a[k], b[k]))
                sa = R.stats(a[k], self.days, self.blk); sb = self.v3["stats"](b[k], self.days, self.blk)
                self.assertEqual(json.dumps(sa, sort_keys=True, default=float), json.dumps(sb, sort_keys=True, default=float))
        for tt, df in ((1.7, 6), (-0.4, 13), (3.2, 20)):
            self.assertEqual(R.t_sf(tt, df), self.v3["t_sf"](tt, df))

    def test_md5_uses_lamports(self):
        h1 = R.pnl_md5(["m"], [1], {"flat": np.array([1.4])}); h2 = R.pnl_md5(["m"], [1], {"flat": np.array([0.6])})
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, R.pnl_md5(["m"], [1], {"flat": np.array([2.0])}))


@unittest.skipIf(np is None, "numpy missing")
class Section7(unittest.TestCase):
    def _g(self, n=140, days=7, pos=5, mean=1.0, ciT=0.1, ciD=0.1, ex3=1.0, exd=1.0, p=0.001):
        return dict(n=n, days=days, days_pos=pos, mean_pct=mean, ciT=ciT, ciD=ciD, ex_top3=ex3, ex_best_day=exd, p_day=p, total=1.0)

    def _bd(self, v=1.0):
        return {d: (v, 20) for d in R.half_dates(1)[0] + R.half_dates(1)[1]}

    def test_pass_and_each_item_can_fail(self):
        ok = R.decide(1, self._g(), self._g(), self._bd(), self._bd(), self._g(), self._g(), 0.005)
        self.assertEqual(ok["verdict"], "PASS")
        for kw in (dict(n=99), dict(days=4), dict(pos=3), dict(ciT=-0.01), dict(ex3=-1), dict(exd=-1), dict(ciD=0.0), dict(p=0.0051)):
            r = R.decide(1, self._g(**kw), self._g(), self._bd(), self._bd(), self._g(), self._g(), 0.005)
            self.assertEqual(r["verdict"], "FAIL", kw)
        bd = self._bd(); bd["2026-10-15"] = (-100.0, 20)
        self.assertEqual(R.decide(1, self._g(), self._g(), self._bd(), bd, self._g(), self._g(), 0.005)["verdict"], "FAIL")   # item 7
        self.assertEqual(R.decide(1, self._g(), self._g(), self._bd(), self._bd(), self._g(), self._g(mean=-0.1), 0.005)["verdict"], "FAIL")  # B1
        # the larger p decides
        self.assertEqual(R.decide(1, self._g(p=0.001), self._g(p=0.006), self._bd(), self._bd(), self._g(), self._g(), 0.005)["verdict"], "FAIL")

    def test_halves(self):
        a, b = R.half_dates(1); self.assertEqual((a[0], a[-1], b[0], b[-1], len(a), len(b)), ("2026-10-10", "2026-10-13", "2026-10-14", "2026-10-16", 4, 3))
        a, b = R.half_dates(2); self.assertEqual((len(a), len(b), b[-1]), (7, 7, "2026-10-23"))


@unittest.skipIf(np is None, "numpy missing")
class WalkForward(unittest.TestCase):
    """Daily expanding retrain: the train mask per date (fake lgb records it)."""

    def test_train_masks(self):
        seen = []

        class FakeLGB:
            class Dataset:
                def __init__(self, X, y):
                    self.X = X

            @staticmethod
            def train(params, ds, num_boost_round):
                seen.append(len(ds.X))

                class M:
                    def predict(self, X):
                        return np.zeros(len(X))
                return M()
        ex_t = [R.ep("2026-09-20T00")] * 3
        oc_t = [R.ep("2026-10-09T05"), R.ep("2026-10-09T23"), R.ep("2026-10-10T05"), R.ep("2026-10-11T05")]
        t = np.array(ex_t + oc_t); is_oct = np.array([0, 0, 0, 1, 1, 1, 1], bool)
        gtime = np.array([0, 0, 0, R.ep("2026-10-08T20"), R.ep("2026-10-09T10"), R.ep("2026-10-09T10"), R.ep("2026-10-10T10")])
        F = np.zeros((7, 2)); y = np.zeros(7); s1 = np.ones(7, bool)
        pred = R.walk_forward(F, y, t, s1, is_oct, gtime, ["2026-10-10", "2026-10-11"], {}, 1, lgb=FakeLGB)
        # 10-10: exploration 3 only (10-09T05 graduated before the floor; 10-09T23 is after the 10-09T23:00 cutoff)
        self.assertEqual(seen, [3, 5])   # 10-11: + the 10-09T23 and 10-10T05 rows (graduated 10-09T10)
        self.assertTrue(np.isfinite(pred[5]) and np.isfinite(pred[6]) and np.isnan(pred[3]))
        seen.clear()
        R.walk_forward(F, y, t, s1, is_oct, gtime, ["2026-10-10", "2026-10-11"], {}, 1, exploration_only=True, lgb=FakeLGB)
        self.assertEqual(seen, [3, 3])


@unittest.skipIf(pd is None or np is None, "pandas/duckdb missing")
class PricingLayerFixture(unittest.TestCase):
    """The pricing layer on a two-hour synthetic exploration tape: the guard, the fill, the clock exit and the missing-V rule."""

    def _tape(self, d, v_ok=None):
        os.makedirs(d, exist_ok=True)
        t0 = R.ep("2026-09-20T00"); rows = []
        q, b = 85_000_000_000, 200_000_000_000_000
        for i in range(0, 7200, 2):
            slot = 1000 + int(i / 0.4)
            side = "buy" if i % 4 == 0 else "sell"
            rows.append(dict(venue="pumpswap", mint="M", trader=f"w{i % 7}", side=side, sol_lamports=100_000_000, token_raw=200_000_000_000,
                             quote_reserve=q, base_reserve=b, pool="P", slot=slot, tx_index=0, event_index=0, block_time=t0 + i,
                             lp_fee=0, protocol_fee=0, creator_fee=0))
            q += 100_000_000 if side == "buy" else -95_000_000
            b += -200_000_000_000 if side == "buy" else 200_000_000_000
        df = pd.DataFrame(rows)
        if v_ok is not None:
            df["v_ok"] = v_ok(df)
        for h, g in df.groupby(df.block_time // 3600):
            g.drop(columns=[]).to_parquet(os.path.join(d, R.hour_str(int(h) * 3600) + ".parquet"), index=False)

    def _rows(self):
        return pd.DataFrame(dict(idx=[1], t=[R.ep("2026-09-20T00") + 1200], mint=["M"], pool=["P"], v=[17.6e9], g=[R.ep("2026-09-19T23")], gday=["2026-09-20"]))

    def test_prices_and_flags(self):
        with tempfile.TemporaryDirectory() as d:
            self._tape(d)
            out = R.price_rows(self._rows(), d, R.ExplorationGuard())
            r = out.iloc[0]
            self.assertEqual(r.err, ""); self.assertEqual(int(r.g_p_END_l055), 0)
            self.assertGreater(r.xt_p_END_l055, r.t + 299)
            self.assertFalse(bool(r.vmiss_p_END_l055))
            d2 = os.path.join(d, "vm")
            self._tape(d2, v_ok=lambda df: df.block_time < R.ep("2026-09-20T00") + 1300)
            r2 = R.price_rows(self._rows(), d2, R.ExplorationGuard()).iloc[0]
            self.assertTrue(bool(r2.vmiss_p_END_l055))
            self.assertLess(r2.pnl_p_END_l055, r.pnl_p_END_l055)                 # the lower of the two pending assumptions

    def test_guard_blocks_october_group(self):
        rows = self._rows().assign(t=[R.ep("2026-10-10T01")], gday=["2026-10-10"])
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(R.Refusal):
                R.price_rows(rows, d, R.ExplorationGuard())


if __name__ == "__main__":
    unittest.main()
