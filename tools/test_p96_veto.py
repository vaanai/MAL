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
S1_END = m.SUCC_END_MS  # E = 2026-12-01T00Z for every s1 clock (DEC-031 Amendment 22 item 8.1), not clock + 14 d
S1_BOOK_END = S1_CLOCK + 14 * DAY  # the synthetic s1 trades span 14 UTC dates from the clock; an A3 end before it cuts rows
NOW = S1_END + 2 * 3_600_000  # after every seal of the tool
Q50_RI = m.Q50_CLOCK_MS + 5 * DAY  # 2026-10-30T00Z
S1_RI = S1_CLOCK + 5 * DAY  # 2026-11-11T00Z
INTERCEPT = {m.Q50_ID: -1.25, m.S1_ID: -1.5}


def start_rec(q: float = 50.0) -> dict:
    return {"type": "start", "rule": Q50_RULE, "rule_sha256": Q50_RULE_SHA256, "q_star_sol": q, "start_ms": 0, "t_ms": 0}


def trade(pool: str, d_ms: int, slot: int, *, sell_frac: float, rng: np.random.Generator, q_post: float | None = None) -> dict:
    """One synthetic trade: its pv trigger record (features drawn around fixed levels) and an outcome-bearing strip the tool must never read."""
    qpre = 45.0
    trig = {"type": "trigger", "variant": "pv", "pool": pool, "slot": slot, "t_detect_ms": d_ms, "t_since_s0_s": float(rng.uniform(5, 290)),
            "q_pv_post_sol": float(rng.uniform(20, 50)) if q_post is None else q_post, "q_pv_pre_sol": qpre, "sell_user_out": int(sell_frac * qpre * 1e9),
            "boost_remaining_sol": float(rng.uniform(0, 17.585)), "prints_seen": int(rng.integers(5, 400))}
    strip = {"type": "strip", "pool": pool, "from_slot": slot, "buys": [[slot - 1, 10**9]], "buys_truncated": False, "rows": [[slot, 1, 1, 1]]}
    return {"trigger": trig, "strip": strip}


def book(bot: str, n: int, clock: int, days: int, rng: np.random.Generator, *, skill: bool = True, test_shift: float = 0.0, bad_rate: float = 0.2,
         q_driven: bool = False) -> list[dict]:
    """n trades over `days` UTC dates from clock. skill: bad trades have a large trigger sell (sell_frac) and lose; otherwise values are independent of the features."""
    out = []
    for i in range(n):
        d = clock + (i % days) * DAY + 3_600_000 + i * 1000
        bad = rng.random() < bad_rate
        if q_driven:  # bad trades are the ones with the highest post-trade Q: a tighter Q* would find them
            sf, qp = float(rng.uniform(0.01, 0.05)), (float(rng.uniform(47, 50)) if bad else float(rng.uniform(18, 40)))
        else:
            sf, qp = (float(rng.uniform(0.08, 0.12)) if bad else float(rng.uniform(0.01, 0.05))) + test_shift, None
        t = trade(f"{bot[-2:]}pool{i:04d}", d, 400_000_000 + i * 1000, sell_frac=sf, rng=rng, q_post=qp)
        if skill:
            base = -1.5e8 if bad else 3e7
            v = base + float(rng.normal(0, 5e6))
        else:
            v = float(rng.normal(-5e6, 6e7))
        t["v"] = {k: v + (j * 1000.0) for j, k in enumerate(SUBCELLS)}
        out.append(t)
    return out


def write_dir(root: Path, name: str, trades: list[dict], *, q: float = 50.0, drop_trigger: set[str] | None = None, bad_field: set[str] | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    with open(d / "h5-shadow-2026-10-26T00.jsonl", "w") as fh:
        fh.write(json.dumps(start_rec(q)) + "\n")
        for t in trades:
            tr = t["trigger"]
            if bad_field and tr["pool"] in bad_field:
                tr = dict(tr, prints_seen=None)
            if not (drop_trigger and tr["pool"] in drop_trigger):
                fh.write(json.dumps(tr) + "\n")
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
    def __init__(self, root: Path, *, n_fit: int = 160, n_test: int = 170, skill: bool = True, test_shift: float = 0.0, seed: int = 7, test_bad_rate: float = 0.2,
                 s1_clock: int = S1_CLOCK, q_driven: bool = False):
        rng = np.random.default_rng(seed)
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.fit = book(m.Q50_ID, n_fit, m.Q50_CLOCK_MS, 12, rng, skill=skill, q_driven=q_driven)
        self.test = book(m.S1_ID, n_test, s1_clock, 14, rng, skill=skill, test_shift=test_shift, bad_rate=test_bad_rate, q_driven=q_driven)
        self.ri = {m.Q50_ID: Q50_RI, m.S1_ID: s1_clock + 5 * DAY}
        self.rows = root / "rows.jsonl"
        write_rows(self.rows, {m.Q50_ID: self.fit, m.S1_ID: self.test}, self.ri)
        self.dirs = {m.Q50_ID: [str(write_dir(root, "h5-q50-a", self.fit))], m.S1_ID: [str(write_dir(root, "h5-q50s1-a", self.test))]}
        self.bots = root / "bots.json"  # Q50's registry
        self.s1_bots = root / "s1-root" / "bots.json"  # s1's own registry (Amendment 22 item 9)
        self.s1_bots.parent.mkdir()
        self.write_bots(s1_clock=s1_clock)
        self.reads = {}
        for bot, tr in ((m.Q50_ID, self.fit), (m.S1_ID, self.test)):
            p = root / f"read-{bot}.json"
            p.write_text(json.dumps(read_doc(bot, tr, self.ri[bot])))
            self.reads[bot] = str(p)

    def write_bots(self, s1_clock: int = S1_CLOCK, s1_end: int | None = None, q50_clock: int = m.Q50_CLOCK_MS) -> None:
        self.bots.write_text(json.dumps({"bots": {m.Q50_ID: {"clock_start_ms": q50_clock, "window_end_ms": None, "frozen_sha": "a" * 40},
                                                  "h5v1-boostfloor-a2": {"clock_start_ms": 1, "window_end_ms": 2}}}))
        self.s1_bots.write_text(json.dumps({"bots": {m.S1_ID: {"clock_start_ms": s1_clock, "window_end_ms": s1_end, "frozen_sha": "b" * 40}}}))

    def set_read(self, bot: str, doc: dict) -> None:
        Path(self.reads[bot]).write_text(json.dumps(doc))

    def run(self, **kw):
        args = dict(rows_path=str(self.rows), trigger_dirs=self.dirs, read_paths=self.reads, confirm_paths={}, bots_path=str(self.bots), s1_bots_path=str(self.s1_bots), s1_clock_ms=S1_CLOCK,
                    after_read=True, now_ms=NOW)
        args.update(kw)
        return m.run(**args)


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self._wall = mock.patch.object(m, "wall_ms", return_value=NOW)  # the tests run after every seal; TestMain checks the real wall clock separately
        self._wall.start()

    def tearDown(self):
        self._wall.stop()
        self._td.cleanup()


class TestConstants(Base):
    def test_instants(self):
        self.assertEqual(m.Q50_CLOCK_MS, 1792886400000)
        self.assertEqual(m.Q50_END_MS, 1793923200000)
        self.assertEqual(m.LOOK2_END_MS, 1794614400000)
        self.assertEqual(m.SUCC_END_MS, 1796083200000)
        self.assertEqual(m.s1_window(1793923200000), (1793923200000, 1796083200000))  # E = 2026-12-01T00Z, no longer clock + 14 d (2026-11-20T00Z)
        self.assertEqual(m.s1_window(1793923200000 + DAY), (1793923200000 + DAY, 1796083200000))  # the 11-07 slip keeps E: 24 d
        self.assertEqual(m.s1_window(m.SUCC_END_MS - 5 * DAY)[1], m.SUCC_END_MS)
        self.assertEqual(m.S1_BOTS_JSON_DEFAULT, "/home/claude/s1home/data/arena/bots.json")
        self.assertFalse(hasattr(m, "S1_HORIZON_MS"))

    def test_rule_gate_features(self):
        self.assertTrue(set(m.RULE_GATE_FEATURES) <= set(m.FEATURES))
        self.assertIn(m.QSTAR_FEATURE, m.RULE_GATE_FEATURES)

    def test_s1_clock_must_be_a_boundary_in_range(self):
        for bad in (S1_CLOCK + 1, S1_CLOCK - DAY, m.SUCC_END_MS, True):
            with self.assertRaises(Refused):
                m.s1_window(bad)

    def test_five_features(self):  # F6 (strip.buys) dropped on QP B1, #1060
        self.assertEqual(len(m.FEATURES), 5)
        self.assertNotIn("nb_pre_frac", m.FEATURES)
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

    def test_s1_is_not_closed_at_its_old_14_day_end(self):
        f = Fixture(self.root)
        old_end = S1_CLOCK + 14 * DAY  # 2026-11-20T00Z, E_s1 under Amendment 18 item 3.2
        with mock.patch.object(m, "wall_ms", return_value=old_end + 3_600_000):
            with self.assertRaisesRegex(Refused, "s1's end"):
                f.run(now_ms=old_end + 3_600_000)

    def test_run_clamps_to_the_wall_clock(self):
        f = Fixture(self.root)
        with mock.patch.object(m, "wall_ms", return_value=m.LOOK2_END_MS - 1):
            with self.assertRaisesRegex(Refused, "2026-11-14"):
                f.run(now_ms=NOW)  # a direct call cannot move the clock forward
            with self.assertRaisesRegex(Refused, "2026-11-14"):
                f.run(now_ms=None)

    def test_s1_clock_bound_to_bots_json(self):
        f = Fixture(self.root)
        f.write_bots(s1_clock=S1_CLOCK + DAY)  # s1's registry says 11-07 (slip); the operator passes 11-06
        with self.assertRaisesRegex(Refused, "clock_start_ms"):
            f.run()
        f.write_bots()
        f.s1_bots.write_text(json.dumps({"bots": {}}))
        with self.assertRaisesRegex(Refused, "no line"):
            f.run()
        f.write_bots(q50_clock=m.Q50_CLOCK_MS + DAY)
        with self.assertRaisesRegex(Refused, "Q50 clock"):
            f.run()
        with self.assertRaisesRegex(Refused, "does not exist"):
            f.run(bots_path=str(self.root / "none.json"))
        with self.assertRaisesRegex(Refused, "s1's bots.json .* does not exist"):
            f.run(s1_bots_path=str(self.root / "none.json"))

    def test_each_book_is_read_from_its_own_registry(self):
        f = Fixture(self.root)
        q = json.loads(f.bots.read_text())
        s1 = json.loads(f.s1_bots.read_text())
        # decoys: an s1 line with another clock in Q50's registry, and a Q50 line with another clock in s1's registry. Neither is read.
        q["bots"][m.S1_ID] = {"clock_start_ms": S1_CLOCK + 3 * DAY, "window_end_ms": S1_CLOCK + 4 * DAY}
        s1["bots"][m.Q50_ID] = {"clock_start_ms": m.Q50_CLOCK_MS + 3 * DAY, "window_end_ms": 1}
        f.bots.write_text(json.dumps(q))
        f.s1_bots.write_text(json.dumps(s1))
        res = f.run()
        self.assertEqual(res["windows"], {m.Q50_ID: [m.Q50_CLOCK_MS, m.Q50_END_MS], m.S1_ID: [S1_CLOCK, S1_END]})
        # s1 only in Q50's registry (the old shared layout): s1's own registry has no line, so it is refused
        f.s1_bots.write_text(json.dumps({"bots": {}}))
        with self.assertRaisesRegex(Refused, "s1's bots.json has no line"):
            f.run()
        # Q50 only in s1's registry: refused for Q50's registry
        f.write_bots()
        f.bots.write_text(json.dumps({"bots": {}}))
        with self.assertRaisesRegex(Refused, "Q50's bots.json has no line"):
            f.run()

    def test_one_file_holding_both_lines_may_be_passed_for_both(self):
        f = Fixture(self.root)
        both = {"bots": {**json.loads(f.bots.read_text())["bots"], **json.loads(f.s1_bots.read_text())["bots"]}}
        f.bots.write_text(json.dumps(both))
        res = f.run(s1_bots_path=str(f.bots))
        self.assertEqual(res["bots_json"], res["s1_bots_json"])

    def test_both_registries_are_recorded_by_sha256(self):
        f = Fixture(self.root)
        res = f.run()
        self.assertEqual(res["bots_json"], {"sha256": m.sha256_file(f.bots), "path": str(f.bots)})
        self.assertEqual(res["s1_bots_json"], {"sha256": m.sha256_file(f.s1_bots), "path": str(f.s1_bots)})
        self.assertNotEqual(res["bots_json"]["sha256"], res["s1_bots_json"]["sha256"])

    def test_slip_to_1107_keeps_the_end(self):
        slip = S1_CLOCK + DAY
        f = Fixture(self.root, s1_clock=slip)
        f.write_bots(s1_clock=slip)
        with self.assertRaisesRegex(Refused, "s1's end"):
            f.run(s1_clock_ms=slip, now_ms=S1_END + 3_600_000 - 1)
        res = f.run(s1_clock_ms=slip, now_ms=S1_END + 3_600_000)
        self.assertEqual(res["windows"][m.S1_ID], [slip, S1_END])  # E is 2026-12-01T00Z on the slip too (24 d), not slip + 14 d
        self.assertEqual(len(res["bots_json"]["sha256"]), 64)

    def test_registry_window_end(self):
        f = Fixture(self.root)
        f.write_bots(s1_end=S1_END + DAY)  # never past E
        with self.assertRaisesRegex(Refused, "window_end_ms"):
            f.run()
        f.write_bots(s1_end=S1_END)  # the registration pin: E itself
        self.assertEqual(f.run()["windows"][m.S1_ID], [S1_CLOCK, S1_END])
        f.write_bots(s1_end=S1_BOOK_END)  # an A3 halt at the old 11-20 end moved E earlier: the window follows it
        res = f.run(now_ms=S1_BOOK_END + 3_600_000)
        self.assertEqual(res["windows"][m.S1_ID], [S1_CLOCK, S1_BOOK_END])
        with self.assertRaisesRegex(Refused, "s1's end"):
            f.run(now_ms=S1_BOOK_END + 3_600_000 - 1)
        f.write_bots(s1_end=S1_BOOK_END - DAY)  # an A3 halt before the last synthetic trades: rows after it are outside the window
        with self.assertRaisesRegex(Refused, "outside the book's window"):
            f.run()
        f.write_bots(s1_end=S1_CLOCK)  # not after the clock
        with self.assertRaisesRegex(Refused, "window_end_ms"):
            f.run()

    def test_read_instant_inside_window(self):
        f = Fixture(self.root)
        f.set_read(m.S1_ID, read_doc(m.S1_ID, f.test, S1_END + DAY))
        with self.assertRaisesRegex(Refused, "not inside the book's window"):
            f.run()

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
    def test_formula(self):
        rng = np.random.default_rng(1)
        t = trade("p", 0, 1000, sell_frac=0.04, rng=rng)
        x, why = m.features(t)
        self.assertIsNone(why)
        self.assertEqual(len(x), 5)
        tr = t["trigger"]
        self.assertAlmostEqual(x[0], tr["t_since_s0_s"])
        self.assertAlmostEqual(x[1], tr["q_pv_post_sol"])
        self.assertAlmostEqual(x[2], tr["sell_user_out"] / (tr["q_pv_pre_sol"] * 1e9))
        self.assertAlmostEqual(x[3], tr["boost_remaining_sol"])
        self.assertAlmostEqual(x[4], math.log1p(tr["prints_seen"]))

    def test_orientation(self):
        small = m.features(trade("a", 0, 1000, sell_frac=0.01, rng=np.random.default_rng(3)))[0]
        big = m.features(trade("a", 0, 1000, sell_frac=0.10, rng=np.random.default_rng(3)))[0]
        self.assertGreater(big[2], small[2])  # larger sell_frac = a larger dump relative to pre-trade depth

    def test_unscorable(self):
        rng = np.random.default_rng(4)
        t = trade("p", 0, 1000, sell_frac=0.03, rng=rng)
        self.assertEqual(m.features(None)[1], "no_trigger")
        self.assertEqual(m.features(dict(t, trigger=dict(t["trigger"], q_pv_pre_sol=0.0)))[1], "trigger_field")
        self.assertEqual(m.features(dict(t, trigger=dict(t["trigger"], prints_seen=None)))[1], "trigger_field")
        self.assertEqual(m.features(dict(t, trigger=dict(t["trigger"], boost_remaining_sol=True)))[1], "trigger_field")

    def test_only_the_trigger_record_is_read(self):
        rng = np.random.default_rng(5)
        t = trade("p", 0, 1000, sell_frac=0.03, rng=rng)
        x0 = m.features({"trigger": t["trigger"]})[0]
        t2 = json.loads(json.dumps(t))
        t2["strip"] = {"buys": [[999, 10**12]], "rows": [[1, 2, 3, 4]] * 9, "post_last": [9, 9, 9, 9]}  # post-decision data: no effect
        t2["trigger"]["exit_landing_slot"] = 1
        np.testing.assert_array_equal(x0, m.features(t2)[0])

    def test_loader_keeps_no_strip_or_slice(self):
        rng = np.random.default_rng(6)
        tr = [trade(f"p{i}", 1000 + i, 1000 + i, sell_frac=0.03, rng=rng) for i in range(3)]
        d = write_dir(self.root, "h5-q50-x", tr)
        with open(d / "h5-shadow-2026-10-26T00.jsonl", "a") as fh:
            fh.write(json.dumps({"type": "boost_slice", "pool": "p0", "slot": 999, "lamports": 5}) + "\n")
        sh = m.load_shadow([d], {"p0", "p1", "p2"})
        self.assertEqual(set(sh), {"p0", "p1", "p2"})
        for v in sh.values():
            self.assertEqual(set(v), {"trigger", "dir"})


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

    def test_qstar_comparison_is_report_only(self):
        res = Fixture(self.root).run()  # the veto loads on sell_frac, not on q_post
        q = res["qstar_compare_report_only"]
        self.assertTrue(q["report_only"])
        self.assertEqual(q["k"], res["test_veto"]["n_vetoed"])
        self.assertFalse(q["re_threshold_of_rule"])
        self.assertEqual(q["largest_abs_weight"], "sell_frac")
        self.assertEqual(set(q["cells"]), set(SUBCELLS))
        for c in q["cells"].values():
            self.assertGreater(c["model_minus_qstar_sol"], 0)

    def test_qstar_dominated_veto_is_labelled(self):
        res = Fixture(self.root, q_driven=True).run()
        q = res["qstar_compare_report_only"]
        self.assertEqual(q["largest_abs_weight"], "q_post_sol")
        self.assertTrue(q["re_threshold_of_rule"])
        self.assertIn("re-threshold of the rule", q["statement"])
        self.assertGreaterEqual(q["overlap_share"], 0.5)
        self.assertIn(res["verdict"], ("PASS", "KILL"))  # the label never decides

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
        f.dirs[m.S1_ID] = [str(write_dir(self.root, "h5-q50s1-b", f.test, bad_field=drop))]
        res = f.run()
        self.assertEqual(res["verdict"], "NOT_DECIDABLE")
        self.assertIn("unscorable", res["reasons"][0])

    def test_unscorable_test_trade_is_never_vetoed(self):
        f = Fixture(self.root)
        bad_pool = max(f.test, key=lambda t: t["trigger"]["sell_user_out"])["trigger"]["pool"]  # the trade the veto would surely remove
        f.dirs[m.S1_ID] = [str(write_dir(self.root, "h5-q50s1-c", f.test, bad_field={bad_pool}))]
        a = Fixture(self.root / "x").run()
        res = f.run()
        self.assertEqual(res["counts"]["test_unscorable_by_reason"], {"trigger_field": 1})
        self.assertEqual(res["test_veto"]["n_vetoed"], a["test_veto"]["n_vetoed"] - 1)


class TestMain(Base):
    def _argv(self, f: Fixture, out: Path) -> list[str]:
        a = ["--after-read", "--scored-rows", str(f.rows), "--bots-json", str(f.bots), "--s1-bots-json", str(f.s1_bots), "--s1-clock-ms", str(S1_CLOCK), "--out", str(out)]
        for b in m.BOOKS:
            a += ["--trigger-dir", f"{b}={f.dirs[b][0]}", "--read-json", f"{b}={f.reads[b]}"]
        return a

    def test_refused_today_by_the_wall_clock(self):
        f = Fixture(self.root)
        out = self.root / "res.json"
        self._wall.stop()  # the real wall clock (2026-10-27 at writing): before the Look-2 floor
        try:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                rc = m.main(self._argv(f, out) + ["--now-ms", str(NOW)])  # --now-ms cannot move the clock past the wall clock
        finally:
            self._wall.start()
        self.assertEqual(rc, 3)
        self.assertIn("REFUSED", err.getvalue())
        self.assertFalse(out.exists())

    def test_write_once(self):
        f = Fixture(self.root)
        out = self.root / "res.json"
        with contextlib.redirect_stdout(io.StringIO()) as so:
            self.assertEqual(m.main(self._argv(f, out)), 0)
        self.assertIn("VERDICT", so.getvalue())
        doc = json.loads(out.read_text())
        self.assertIn(doc["verdict"], ("PASS", "KILL", "NOT_DECIDABLE"))
        self.assertEqual(doc["now_ms"], NOW)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(m.main(self._argv(f, out)), 3)
        self.assertIn("runs once", err.getvalue())

    def test_s1_bots_json_defaults_to_s1s_own_root_and_the_default_is_never_opened_here(self):
        f = Fixture(self.root)
        argv = [x for x in self._argv(f, self.root / "r.json") if x not in ("--s1-bots-json", str(f.s1_bots))]
        self.assertNotIn("--s1-bots-json", argv)
        with mock.patch.object(m, "run", return_value={"verdict": "NOT_DECIDABLE", "reasons": ["x"]}) as r, mock.patch.object(m, "render", return_value="R"), \
                mock.patch.object(m, "write_once"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(m.main(argv), 0)  # run() is mocked: the real s1 registry is not read
        self.assertEqual((r.call_args.kwargs["s1_bots_path"], r.call_args.kwargs["bots_path"]), ("/home/claude/s1home/data/arena/bots.json", str(f.bots)))

    def test_s1_bots_json_is_forwarded_and_a_missing_one_is_refused(self):
        f = Fixture(self.root)
        argv = self._argv(f, self.root / "r.json")
        argv[argv.index("--s1-bots-json") + 1] = str(self.root / "no-such" / "bots.json")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(m.main(argv), 3)
        self.assertIn("s1's bots.json", err.getvalue())
        self.assertIn("does not exist", err.getvalue())

    def test_result_records_both_registries(self):
        f = Fixture(self.root)
        out = self.root / "res.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(m.main(self._argv(f, out)), 0)
        doc = json.loads(out.read_text())
        self.assertEqual(doc["s1_bots_json"], {"sha256": m.sha256_file(f.s1_bots), "path": str(f.s1_bots)})
        self.assertEqual(doc["bots_json"]["sha256"], m.sha256_file(f.bots))
        self.assertEqual(doc["windows"][m.S1_ID], [S1_CLOCK, S1_END])

    def test_bad_bot_name(self):
        f = Fixture(self.root)
        argv = self._argv(f, self.root / "r.json") + ["--trigger-dir", "h5v1-boostfloor-a1=/tmp"]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(m.main(argv), 3)


if __name__ == "__main__":
    unittest.main()
