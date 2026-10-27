"""Tests for tools/p96_veto.py (docs/research/p96/PREREG.md). SYNTHETIC fixtures only: synthetic shadow records, scored rows and read markers in temp dirs. No real Q50, s1 or
H5 path is opened, and no tape is read.

Run: /data/mal/venv/bin/python3 -m pytest -q tools/test_p96_veto.py
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from tools import p96_veto as m
from tools.arena_h5_read import SUBCELLS
from tools.arena_h5_score_q50 import Q50_RULE, Q50_RULE_SHA256
from tools.arena_score import Refused

DAY = m.DAY_MS
S1_CLOCK = m.S1_MIN_CLOCK_MS
S1_END = S1_CLOCK + 14 * DAY
NOW = S1_END + 2 * 3_600_000  # after every seal of the tool
Q50_RI = m.Q50_CLOCK_MS + 5 * DAY  # 2026-10-30T00Z
S1_RI = S1_CLOCK + 5 * DAY  # 2026-11-11T00Z
INTERCEPT = {m.Q50_ID: -1.25, m.S1_ID: -1.5}


def start_rec(q: float = 50.0) -> dict:
    return {"type": "start", "rule": Q50_RULE, "rule_sha256": Q50_RULE_SHA256, "q_star_sol": q, "start_ms": 0, "t_ms": 0}


def trade(pool: str, d_ms: int, slot: int, *, sell_frac: float, rng: np.random.Generator, nb_pre: int = 300_000_000, boost_pre: int = 0) -> dict:
    """One synthetic trade: its pv trigger, strip and boost slices (features drawn around fixed levels)."""
    qpre = 45.0
    trig = {"type": "trigger", "variant": "pv", "pool": pool, "slot": slot, "t_detect_ms": d_ms, "t_since_s0_s": float(rng.uniform(5, 290)),
            "q_pv_post_sol": float(rng.uniform(20, 50)), "q_pv_pre_sol": qpre, "sell_user_out": int(sell_frac * qpre * 1e9),
            "boost_remaining_sol": float(rng.uniform(0, 17.585)), "prints_seen": int(rng.integers(5, 400))}
    buys = [[slot - 51, 10**9], [slot - 50, nb_pre], [slot - 1, 0], [slot, 7 * 10**9]]
    slices = []
    if boost_pre:
        buys.append([slot - 10, boost_pre])
        slices.append({"type": "boost_slice", "pool": pool, "slot": slot - 10, "lamports": boost_pre})
    strip = {"type": "strip", "pool": pool, "from_slot": slot, "buys": buys, "buys_truncated": False, "rows": [[slot, 1, 1, 1]]}
    return {"trigger": trig, "strip": strip, "slices": slices}


def book(bot: str, n: int, clock: int, days: int, rng: np.random.Generator, *, skill: bool = True, test_shift: float = 0.0, bad_rate: float = 0.2) -> list[dict]:
    """n trades over `days` UTC dates from clock. skill: bad trades have a large trigger sell (sell_frac) and lose; otherwise values are independent of the features."""
    out = []
    for i in range(n):
        d = clock + (i % days) * DAY + 3_600_000 + i * 1000
        bad = rng.random() < bad_rate
        sf = (float(rng.uniform(0.08, 0.12)) if bad else float(rng.uniform(0.01, 0.05))) + test_shift
        t = trade(f"{bot[-2:]}pool{i:04d}", d, 400_000_000 + i * 1000, sell_frac=sf, rng=rng)
        if skill:
            base = -1.5e8 if bad else 3e7
            v = base + float(rng.normal(0, 5e6))
        else:
            v = float(rng.normal(-5e6, 6e7))
        t["v"] = {k: v + (j * 1000.0) for j, k in enumerate(SUBCELLS)}
        out.append(t)
    return out


def write_dir(root: Path, name: str, trades: list[dict], *, q: float = 50.0, drop_strip: set[str] | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    with open(d / "h5-shadow-2026-10-26T00.jsonl", "w") as fh:
        fh.write(json.dumps(start_rec(q)) + "\n")
        for t in trades:
            fh.write(json.dumps(t["trigger"]) + "\n")
            for s in t["slices"]:
                fh.write(json.dumps(s) + "\n")
            if not (drop_strip and t["trigger"]["pool"] in drop_strip):
                fh.write(json.dumps(t["strip"]) + "\n")
    return d


def write_rows(path: Path, books: dict[str, list[dict]], ri: dict[str, int], intercept: dict[str, float] | None = None) -> None:
    ic = intercept or INTERCEPT
    with open(path, "w") as fh:
        for bot, trades in books.items():
            fh.write(json.dumps({"type": "p96_rows_header", "bot_id": bot, "read_instant_ms": ri[bot], "intercept": ic[bot], "stake_sol": "0.30", "leg": "1p9", "rent": "charged"}) + "\n")
            for t in trades:
                p = t["trigger"]["pool"]
                fh.write(json.dumps({"type": "p96_row", "bot_id": bot, "trade_id": f"{bot}:{p}:0.30", "pool": p, "decision_ms": t["trigger"]["t_detect_ms"], "v": t["v"]}) + "\n")


def read_doc(bot: str, trades: list[dict], ri: int, status: str = "FAIL", intercept: float | None = None, prov: bool = True) -> dict:
    before = [t for t in trades if t["trigger"]["t_detect_ms"] < ri]
    sub = {k: {"n": len(before), "total_sol": sum(t["v"][k] for t in before) / 1e9} for k in SUBCELLS}
    b = {"bot_id": bot, "status": status, "read_instant_ms": ri, "refit": {"intercept": INTERCEPT[bot] if intercept is None else intercept}, "subcells": sub,
         "provenance": {"q50-read-instant.json": "ab" * 32} if prov else {}}
    if status == "NOT_DECIDABLE":
        b = {"bot_id": bot, "status": status, "read_instant_ms": None, "final": True, "provenance": b["provenance"]}
    return {"books": {bot: b}, "refused": {}}


class Fixture:
    def __init__(self, root: Path, *, n_fit: int = 160, n_test: int = 170, skill: bool = True, test_shift: float = 0.0, seed: int = 7, test_bad_rate: float = 0.2):
        rng = np.random.default_rng(seed)
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.fit = book(m.Q50_ID, n_fit, m.Q50_CLOCK_MS, 12, rng, skill=skill)
        self.test = book(m.S1_ID, n_test, S1_CLOCK, 14, rng, skill=skill, test_shift=test_shift, bad_rate=test_bad_rate)
        self.ri = {m.Q50_ID: Q50_RI, m.S1_ID: S1_RI}
        self.rows = root / "rows.jsonl"
        write_rows(self.rows, {m.Q50_ID: self.fit, m.S1_ID: self.test}, self.ri)
        self.dirs = {m.Q50_ID: [str(write_dir(root, "h5-q50-a", self.fit))], m.S1_ID: [str(write_dir(root, "h5-q50s1-a", self.test))]}
        self.reads = {}
        for bot, tr in ((m.Q50_ID, self.fit), (m.S1_ID, self.test)):
            p = root / f"read-{bot}.json"
            p.write_text(json.dumps(read_doc(bot, tr, self.ri[bot])))
            self.reads[bot] = str(p)

    def set_read(self, bot: str, doc: dict) -> None:
        Path(self.reads[bot]).write_text(json.dumps(doc))

    def run(self, **kw):
        args = dict(rows_path=str(self.rows), trigger_dirs=self.dirs, read_paths=self.reads, confirm_paths={}, s1_clock_ms=S1_CLOCK, after_read=True, now_ms=NOW)
        args.update(kw)
        return m.run(**args)


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()


class TestConstants(Base):
    def test_instants(self):
        self.assertEqual(m.Q50_CLOCK_MS, 1792886400000)
        self.assertEqual(m.Q50_END_MS, 1793923200000)
        self.assertEqual(m.LOOK2_END_MS, 1794614400000)
        self.assertEqual(m.SUCC_END_MS, 1796083200000)
        self.assertEqual(m.s1_window(1793923200000), (1793923200000, 1795132800000))
        self.assertEqual(m.s1_window(m.SUCC_END_MS - 5 * DAY)[1], m.SUCC_END_MS)

    def test_pre_slots_is_the_shadow_constant(self):
        from tools import h5_shadow
        self.assertEqual(m.PRE_SLOTS, h5_shadow.FRESH_BUYS_BACK_SLOTS)

    def test_s1_clock_must_be_a_boundary_in_range(self):
        for bad in (S1_CLOCK + 1, S1_CLOCK - DAY, m.SUCC_END_MS, True):
            with self.assertRaises(Refused):
                m.s1_window(bad)

    def test_six_features(self):
        self.assertEqual(len(m.FEATURES), 6)
        self.assertEqual(set(m.PRIOR_SIGN), set(m.FEATURES))


class TestSeal(Base):
    def test_after_read_flag(self):
        f = Fixture(self.root)
        with self.assertRaisesRegex(Refused, "--after-read"):
            f.run(after_read=False)

    def test_look2_seal(self):
        f = Fixture(self.root)
        with self.assertRaisesRegex(Refused, "2026-11-14"):
            f.run(now_ms=m.LOOK2_END_MS - 1)

    def test_s1_window_closed(self):
        f = Fixture(self.root)
        with self.assertRaisesRegex(Refused, "s1's end"):
            f.run(now_ms=S1_END + 3_600_000 - 1)
        self.assertIn(f.run(now_ms=S1_END + 3_600_000)["verdict"], ("PASS", "KILL", "NOT_DECIDABLE"))

    def test_no_marker(self):
        f = Fixture(self.root)
        with self.assertRaisesRegex(Refused, "does not exist"):
            f.run(read_paths={m.Q50_ID: str(self.root / "nope.json"), m.S1_ID: f.reads[m.S1_ID]})
        with self.assertRaisesRegex(Refused, "required"):
            f.run(read_paths={m.Q50_ID: f.reads[m.Q50_ID]})

    def test_marker_must_be_final_and_carry_the_instant_file(self):
        f = Fixture(self.root)
        doc = read_doc(m.Q50_ID, f.fit, Q50_RI)
        doc["books"][m.Q50_ID]["status"] = "PENDING"
        f.set_read(m.Q50_ID, doc)
        with self.assertRaisesRegex(Refused, "not a final verdict"):
            f.run()
        f.set_read(m.Q50_ID, read_doc(m.Q50_ID, f.fit, Q50_RI, prov=False))
        with self.assertRaisesRegex(Refused, "read-instant"):
            f.run()
        f.set_read(m.Q50_ID, {"books": {}, "refused": {m.Q50_ID: "seal"}})
        with self.assertRaisesRegex(Refused, "no verdict"):
            f.run()

    def test_pass_needs_a_later_confirmation(self):
        f = Fixture(self.root)
        f.set_read(m.S1_ID, read_doc(m.S1_ID, f.test, S1_RI, status="PASS"))
        with self.assertRaisesRegex(Refused, "confirm-read-json"):
            f.run()
        c = self.root / "confirm.json"
        c.write_text(json.dumps(read_doc(m.S1_ID, f.test, S1_RI, status="FAIL")))
        with self.assertRaisesRegex(Refused, "not after"):
            f.run(confirm_paths={m.S1_ID: str(c)})
        c.write_text(json.dumps(read_doc(m.S1_ID, f.test, S1_RI + 3 * DAY, status="FAIL")))
        res = f.run(confirm_paths={m.S1_ID: str(c)})
        self.assertEqual(res["markers"][m.S1_ID]["confirm"]["status"], "FAIL")

    def test_not_decidable_read_ends_the_lane_before_rows(self):
        f = Fixture(self.root)
        f.set_read(m.Q50_ID, read_doc(m.Q50_ID, f.fit, Q50_RI, status="NOT_DECIDABLE"))
        res = f.run(rows_path=str(self.root / "no-rows-file.jsonl"))  # the rows file is never opened
        self.assertEqual(res["verdict"], "NOT_DECIDABLE")
        self.assertNotIn("rows_sha256", res)


class TestParity(Base):
    def test_rows_must_reproduce_the_read(self):
        f = Fixture(self.root)
        doc = read_doc(m.Q50_ID, f.fit, Q50_RI)
        doc["books"][m.Q50_ID]["subcells"]["press_1p9_sc"]["total_sol"] += 1e-6
        f.set_read(m.Q50_ID, doc)
        with self.assertRaisesRegex(Refused, "not the read's trades"):
            f.run()

    def test_count_must_match(self):
        f = Fixture(self.root)
        doc = read_doc(m.Q50_ID, f.fit, Q50_RI)
        doc["books"][m.Q50_ID]["subcells"]["flat_1p9_total"]["n"] += 1
        f.set_read(m.Q50_ID, doc)
        with self.assertRaisesRegex(Refused, "not the read's trades"):
            f.run()

    def test_intercept_and_instant(self):
        f = Fixture(self.root)
        f.set_read(m.S1_ID, read_doc(m.S1_ID, f.test, S1_RI, intercept=-1.4))
        with self.assertRaisesRegex(Refused, "intercept"):
            f.run()
        f.set_read(m.S1_ID, read_doc(m.S1_ID, f.test, S1_RI + DAY))
        with self.assertRaisesRegex(Refused, "read_instant_ms"):
            f.run()

    def test_row_window_and_uniqueness(self):
        f = Fixture(self.root)
        bad = [dict(t) for t in f.test]
        bad[0] = json.loads(json.dumps(bad[0]))
        bad[0]["trigger"]["t_detect_ms"] = S1_END
        write_rows(f.rows, {m.Q50_ID: f.fit, m.S1_ID: bad}, f.ri)
        with self.assertRaisesRegex(Refused, "outside the book's window"):
            f.run()
        write_rows(f.rows, {m.Q50_ID: f.fit + f.fit[:1], m.S1_ID: f.test}, f.ri)
        with self.assertRaisesRegex(Refused, "twice"):
            f.run()

    def test_trigger_pairing(self):
        f = Fixture(self.root)
        moved = json.loads(json.dumps(f.fit))
        moved[3]["trigger"]["t_detect_ms"] += 1
        f.dirs[m.Q50_ID] = [str(write_dir(self.root, "h5-q50-b", moved))]
        with self.assertRaisesRegex(Refused, "wrong pairing"):
            f.run()

    def test_start_record_must_be_q50_rule(self):
        f = Fixture(self.root)
        f.dirs[m.S1_ID] = [str(write_dir(self.root, "h5-q40", f.test, q=40.0))]
        with self.assertRaisesRegex(Refused, "q_star_mismatch"):
            f.run()

    def test_first_dir_by_name_wins(self):
        f = Fixture(self.root)
        other = json.loads(json.dumps(f.fit))
        for t in other:
            t["trigger"]["t_detect_ms"] += 5  # a different run's record of the same pools
        f.dirs[m.Q50_ID] = [str(write_dir(self.root, "h5-q50-z", other)), f.dirs[m.Q50_ID][0]]  # "h5-q50-a" sorts first and wins
        self.assertIn(f.run()["verdict"], ("PASS", "KILL"))


class TestFeatures(Base):
    def test_formula_and_strictly_before(self):
        rng = np.random.default_rng(1)
        t = trade("p", 0, 1000, sell_frac=0.04, rng=rng, nb_pre=250_000_000, boost_pre=600_000_000)
        x, why = m.features(t)
        self.assertIsNone(why)
        tr = t["trigger"]
        self.assertAlmostEqual(x[0], tr["t_since_s0_s"])
        self.assertAlmostEqual(x[1], tr["q_pv_post_sol"])
        self.assertAlmostEqual(x[2], tr["sell_user_out"] / (tr["q_pv_pre_sol"] * 1e9))
        self.assertAlmostEqual(x[3], tr["boost_remaining_sol"])
        self.assertAlmostEqual(x[4], math.log1p(tr["prints_seen"]))
        # buys: slot-51 excluded, slot-50 included, slot-1 included (0), slot (the trigger's own slot) excluded; the BOOST slice at slot-10 is netted out
        self.assertAlmostEqual(x[5], 250_000_000 / (45.0 * 1e9))

    def test_orientation(self):
        rng = np.random.default_rng(2)
        small = m.features(trade("a", 0, 1000, sell_frac=0.01, rng=np.random.default_rng(3)))[0]
        big = m.features(trade("a", 0, 1000, sell_frac=0.10, rng=np.random.default_rng(3)))[0]
        self.assertGreater(big[2], small[2])  # larger sell_frac = a larger dump relative to pre-trade depth
        lo = m.features(trade("b", 0, 1000, sell_frac=0.02, rng=rng, nb_pre=10**8))[0]
        hi = m.features(trade("b", 0, 1000, sell_frac=0.02, rng=np.random.default_rng(2), nb_pre=10**9))[0]
        self.assertGreater(hi[5], lo[5])

    def test_unscorable(self):
        rng = np.random.default_rng(4)
        t = trade("p", 0, 1000, sell_frac=0.03, rng=rng)
        self.assertEqual(m.features(None)[1], "no_trigger")
        self.assertEqual(m.features(dict(t, strip=None))[1], "no_strip")
        self.assertEqual(m.features(dict(t, strip=dict(t["strip"], buys_truncated=True)))[1], "no_buys")
        self.assertEqual(m.features(dict(t, strip={k: v for k, v in t["strip"].items() if k != "buys"}))[1], "no_buys")
        self.assertEqual(m.features(dict(t, trigger=dict(t["trigger"], q_pv_pre_sol=0.0)))[1], "trigger_field")
        self.assertEqual(m.features(dict(t, trigger=dict(t["trigger"], prints_seen=None)))[1], "trigger_field")

    def test_no_outcome_field_is_read(self):
        rng = np.random.default_rng(5)
        t = trade("p", 0, 1000, sell_frac=0.03, rng=rng)
        x0 = m.features(t)[0]
        t2 = json.loads(json.dumps(t))
        t2["strip"]["rows"] = [[1, 2, 3, 4]] * 9
        t2["strip"]["post_last"] = [9, 9, 9, 9]
        t2["trigger"]["exit_landing_slot"] = 1
        np.testing.assert_array_equal(x0, m.features(t2)[0])


class TestModel(Base):
    def test_fit_recovers_the_sign_and_is_deterministic(self):
        rng = np.random.default_rng(11)
        X = rng.normal(size=(300, 6))
        y = (X[:, 2] + 0.3 * rng.normal(size=300) > 0.7).astype(float)
        a, b = m.fit_logistic(X, y), m.fit_logistic(X, y)
        self.assertTrue(a["converged"])
        np.testing.assert_array_equal(a["w"], b["w"])
        self.assertEqual(int(np.argmax(np.abs(a["w"][1:]))), 2)
        self.assertGreater(a["w"][3], 0)

    def test_constant_feature(self):
        X = np.ones((50, 6))
        self.assertFalse(m.fit_logistic(X, np.zeros(50))["converged"])

    def test_placebo_sets_are_seeded(self):
        a, b = m.placebo_sets(20, 4), m.placebo_sets(20, 4)
        self.assertEqual(len(a), 1000)
        for x, y in zip(a, b):
            np.testing.assert_array_equal(x, y)
            self.assertEqual(len(set(x.tolist())), 4)

    def test_auc(self):
        self.assertEqual(m.auc(np.array([3.0, 2.0, 1.0]), np.array([1.0, 0.0, 0.0])), 1.0)
        self.assertEqual(m.auc(np.array([1.0, 1.0]), np.array([1.0, 0.0])), 0.5)
        self.assertIsNone(m.auc(np.array([1.0]), np.array([1.0])))


class TestVerdicts(Base):
    def test_pass_on_a_veto_with_skill(self):
        f = Fixture(self.root)
        res = f.run()
        self.assertEqual(res["verdict"], "PASS", res.get("reasons"))
        self.assertEqual(res["model"]["learned_sign"]["sell_frac"], "+")
        self.assertLessEqual(res["test_veto"]["share"], 0.5)
        for c in res["cells"].values():
            self.assertGreater(c["delta_vs_placebo_sol"], 0)
            self.assertLessEqual(c["placebo_p"], 0.10)
            self.assertGreaterEqual(c["kept"]["n"], 100)
        self.assertIn("new forward book", res["license"])
        self.assertIn("c + 1", res["multiplicity"])
        json.dumps(res, allow_nan=False)

    def test_kill_without_skill(self):
        f = Fixture(self.root, skill=False)
        res = f.run()
        self.assertEqual(res["verdict"], "KILL")
        self.assertTrue(res["reasons"])

    def test_k4_veto_fires_on_most_test_trades(self):
        f = Fixture(self.root, test_shift=0.08)
        res = f.run()
        self.assertEqual(res["verdict"], "KILL")
        self.assertTrue(res["reasons"][0].startswith("K4"))
        self.assertNotIn("cells", res)  # decided on the count, before any test value

    def test_k3_count_after_veto(self):
        f = Fixture(self.root, n_test=126, test_bad_rate=0.35)  # about 44 vetoed: under 50%, but fewer than 100 kept
        res = f.run()
        self.assertEqual(res["verdict"], "KILL")
        self.assertTrue(any("K3" in r and "n " in r for r in res["reasons"]))

    def test_minimum_counts_are_not_decidable(self):
        res = Fixture(self.root, n_fit=99).run()
        self.assertEqual(res["verdict"], "NOT_DECIDABLE")
        self.assertIn("fit set", res["reasons"][0])

    def test_minimum_test_count(self):
        res = Fixture(self.root, n_test=124).run()
        self.assertEqual(res["verdict"], "NOT_DECIDABLE")
        self.assertIn("test set", res["reasons"][0])
        self.assertNotIn("model", res)

    def test_unscorable_share(self):
        f = Fixture(self.root)
        drop = {t["trigger"]["pool"] for t in f.test[:10]}  # 10 of 170 > 5%
        f.dirs[m.S1_ID] = [str(write_dir(self.root, "h5-q50s1-b", f.test, drop_strip=drop))]
        res = f.run()
        self.assertEqual(res["verdict"], "NOT_DECIDABLE")
        self.assertIn("unscorable", res["reasons"][0])

    def test_unscorable_test_trade_is_never_vetoed(self):
        f = Fixture(self.root)
        bad_pool = max(f.test, key=lambda t: t["trigger"]["sell_user_out"])["trigger"]["pool"]  # the trade the veto would surely remove
        f.dirs[m.S1_ID] = [str(write_dir(self.root, "h5-q50s1-c", f.test, drop_strip={bad_pool}))]
        a = Fixture(self.root / "x").run()
        res = f.run()
        self.assertEqual(res["counts"]["test_unscorable_by_reason"], {"no_strip": 1})
        self.assertEqual(res["test_veto"]["n_vetoed"], a["test_veto"]["n_vetoed"] - 1)


class TestMain(Base):
    def _argv(self, f: Fixture, out: Path) -> list[str]:
        a = ["--after-read", "--scored-rows", str(f.rows), "--s1-clock-ms", str(S1_CLOCK), "--out", str(out)]
        for b in m.BOOKS:
            a += ["--trigger-dir", f"{b}={f.dirs[b][0]}", "--read-json", f"{b}={f.reads[b]}"]
        return a

    def test_refused_today_by_the_wall_clock(self):
        f = Fixture(self.root)
        out = self.root / "res.json"
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc = m.main(self._argv(f, out) + ["--now-ms", str(NOW)])  # --now-ms cannot move the clock past the wall clock
        self.assertEqual(rc, 3)
        self.assertIn("REFUSED", err.getvalue())
        self.assertFalse(out.exists())

    def test_write_once(self):
        f = Fixture(self.root)
        out = self.root / "res.json"
        with mock.patch.object(m, "wall_ms", return_value=NOW), contextlib.redirect_stdout(io.StringIO()) as so:
            self.assertEqual(m.main(self._argv(f, out)), 0)
        self.assertIn("VERDICT", so.getvalue())
        doc = json.loads(out.read_text())
        self.assertIn(doc["verdict"], ("PASS", "KILL", "NOT_DECIDABLE"))
        self.assertEqual(doc["now_ms"], NOW)
        with mock.patch.object(m, "wall_ms", return_value=NOW), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(m.main(self._argv(f, out)), 3)
        self.assertIn("runs once", err.getvalue())

    def test_bad_bot_name(self):
        f = Fixture(self.root)
        argv = self._argv(f, self.root / "r.json") + ["--trigger-dir", "h5v1-boostfloor-a1=/tmp"]
        with mock.patch.object(m, "wall_ms", return_value=NOW), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(m.main(argv), 3)


if __name__ == "__main__":
    unittest.main()
