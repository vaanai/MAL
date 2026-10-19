"""Tests for tools/arena_h5_read.py, the H5 deciding-cell read. SYNTHETIC fixtures in tmp dirs only; no real H5 path is opened. The rows are written by the code that writes them
(arena_h5_score.price_trade / ledger_rows / write_ledger) and the read-instant record by arena_h5_score.record_read_instant; the exclusion and refusal rows have the shape score_book writes
(its inline dicts). The pools are the legs tests' (pool_records): no buys, so ssb = nb = 0 and the refit intercept has a closed form, c = logit(0.289), which the tests use as an
INDEPENDENT value for the refit (the tool gets it from arena_h5_score.refit_cells). Fixture dates are March 2026; `wall_ms` is patched so the clamp of --now-ms is deterministic.

Run: /home/claude/venvs/c1nf-ledger/bin/python3 -m pytest -q tools/test_arena_h5_read.py
"""

from __future__ import annotations

import collections
import contextlib
import copy
import io
import json
import math
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import numpy as np

from tools import arena_h5_cost as cost
from tools import arena_h5_legs
from tools import arena_h5_read as R
from tools import arena_h5_score as h5s
from tools import arena_score
from tools.test_arena_h5_legs import BASE_PRE, SELL_RAW, START, pool_records, r

V1, V2 = "h5v1-boostfloor-a1", "h5v2-boostclock-a1"
BT0 = int(datetime(2026, 3, 2, tzinfo=timezone.utc).timestamp())
WALL = 1_790_000_000_000  # the patched wall clock: after every fixture instant
LAM = 300_000_000
SPOT = 35.0e9 / (BASE_PRE + SELL_RAW)
QX = {"win": 46e9, "win2": 47e9, "win3": 45e9, "loss": 41e9, "flat": 40e9, "marg": 41.79e9, "rent0": 41.626e9, "small": 41.82e9}  # the exit Q of each pool kind (entry Q 40e9, base 2e14)
C_CLOSED = math.log(0.289 / 0.711)  # refit intercept when every row is filled and ssb = nb = 0: sigmoid(c) = 0.289
ATTEMPT_SENTENCE_PREFIX = "Attempt definition amended after the clock"


def nofail(kind: str, strand: str = "dec031") -> float:
    t = cost.deciding_round_trip(LAM, 40e9, 2.0e14, SPOT, QX[kind], 2.0e14, strand=strand)
    assert t["state"] == "filled", (kind, t)
    return t["pnl"]


def default_kind(d: int, i: int) -> str:
    return "loss" if i % 12 == 11 else ("win", "win2", "win3")[i % 3]


def make_pool(d: int, i: int, kind: str, bad13: bool = False) -> tuple[dict, dict, dict]:
    rows = [r(1200, 40e9, 2.0e14)] + ([r(1205, 55e9, 2.0e14)] if bad13 else []) + [r(1206, 40e9, 2.0e14), r(1803, QX[kind], 2.0e14)]
    return pool_records(f"P{d}x{i:02d}", rows, [1900, QX[kind], QX[kind], 2.0e14], bt=BT0 + d * 86400 + i * 60)


_ROWS: dict = {}


def trade_rows_for(bot: str, trig: dict, out: dict, strip: dict) -> list[dict]:
    """The rows of one pool as the scorer writes them (cached per pool and bot: the fixtures reprice the same pools many times)."""
    k = (bot, out["pool"], out["exit"]["trigger_slot"], tuple(map(tuple, strip["rows"])))
    if k not in _ROWS:
        priced = h5s.price_trade(bot, out, trig, strip, None, None, START)
        _ROWS[k] = h5s.ledger_rows(bot, out, trig, priced, START, None, None)
    return copy.deepcopy(_ROWS[k])


def expected_cells(kinds: list[str], days: list[str], rent0: bool = False) -> dict[str, dict]:
    """The four deciding sub-cells restated from the stack's functions at the closed-form refit intercept, scored with arena_score.cell_stats."""
    p = cost.press_p(C_CLOSED, 0, 0)
    out = {}
    for rule, strand in (("total", "dec031"), ("sc", "sc")):
        nf = np.array([nofail(k, strand) for k in kinds])
        flat = (1 - cost.FAIL_FLAT) * nf - cost.FAIL_FLAT * cost.SEND
        press = (1 - p) * nf - p * cost.SEND
        if rent0:
            flat, press = flat + (1 - cost.FAIL_FLAT) * cost.RENT, press + (1 - p) * cost.RENT
        out[f"flat_1p9_{rule}"], out[f"press_1p9_{rule}"] = arena_score.cell_stats(flat, days), arena_score.cell_stats(press, days)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="arena-h5-read-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.ledger = self.root / "arena" / "ledger"
        for p in (mock.patch.object(R, "wall_ms", return_value=WALL), mock.patch.object(h5s.os, "fsync")):  # fixture IO only: no fsync per row
            p.start()
            self.addCleanup(p.stop)

    def make_book(self, bot: str = V1, *, per_day: int = 24, days: int = 7, kind=default_kind, excluded=(), refused: int = 0, bad13: bool = False, open_before: int = 0,
                  mutate=None) -> dict:
        """Write a fixture book: `days` days of `per_day` pools, the pools in `excluded` ((day, idx) pairs) as exclusion rows, `refused` refusal rows spread over days 0 to 4, then the
        read-instant record via arena_h5_score.record_read_instant. Returns the read instant and the (day, idx, kind, counted) of every pool."""
        rows, excl, decisions, meta = [], [], [], []
        for d in range(days):
            for i in range(per_day):
                k = kind(d, i)
                trig, out, strip = make_pool(d, i, k, bad13)
                decisions.append(trig["t_detect_ms"])
                if (d, i) in set(excluded):
                    excl.append({"exclusion_id": f"{bot}:{out['pool']}", "bot_id": bot, "pool": out["pool"], "mint": trig["mint"], "reason": "gap_flag", "decision_ms": trig["t_detect_ms"],
                                 "frozen_sha": START["code_sha"]})
                else:
                    rows += trade_rows_for(bot, trig, out, strip)
                meta.append((d, i, k, (d, i) not in set(excluded)))
        refusals = [{"refusal_id": f"{bot}:REF{n}", "bot_id": bot, "pool": f"REF{n}", "mint": f"MintREF{n}", "reason": "bad_intent:base_breaks_unresolved_settled",
                     "decision_ms": (BT0 + (n % 5) * 86400 + 1800 + n) * 1000, "frozen_sha": START["code_sha"]} for n in range(refused)]
        if mutate:
            mutate(rows)
        h5s.write_ledger(bot, rows, excl, self.ledger, collections.Counter(), refusals=refusals)
        res = h5s.BookResult()
        res.decisions, res.refusals = decisions, refusals
        ri = h5s.read_instant(decisions, 2**62)
        res.open_decisions = [d for d in sorted(decisions) if d < ri][:open_before]
        h5s.record_read_instant(bot, ri, res, self.ledger, collections.Counter())
        return {"ri": ri, "pools": meta, "bot": bot}

    def cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = R.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def args(self, ri: int, *bots: str, n: str = "10", c: str = "3", extra: tuple = ()) -> list[str]:
        a = ["--ledger-root", str(self.ledger), "--read-instant", str(ri), "--i-am-the-read", "--now-ms", str(ri + 1000), "--n-books", n, "--c", c]
        for b in bots:
            a += ["--bot", b]
        return a + list(extra)

    def decide(self, ri: int, bot: str = V1, **kw) -> dict:
        return R.decide_book(bot, self.ledger, read_instant_ms=ri, i_am_the_read=True, now_ms=ri + 1000, **kw)

    def counted_days(self, book: dict) -> tuple[list[str], list[str]]:
        sel = [(k, h5s.utc_day((BT0 + d * 86400 + i * 60) * 1000)) for d, i, k, counted in book["pools"] if counted and (BT0 + d * 86400 + i * 60) * 1000 < book["ri"]]
        return [k for k, _ in sel], [d for _, d in sel]


class DecidingCell(Base):
    def test_pass_matches_an_independent_restatement(self):
        b = self.make_book()
        res = self.decide(b["ri"])
        kinds, days = self.counted_days(b)
        self.assertEqual(len(kinds), 120)  # five days of 24; the two later days are after the recorded instant and never counted
        self.assertEqual(res["status"], "PASS", res["subcells"])
        self.assertEqual(sorted(res["subcells"]), sorted(R.SUBCELLS))
        want = expected_cells(kinds, days)
        for k, v in res["subcells"].items():
            self.assertEqual(v["n"], 120)
            self.assertEqual(v["days"], 5)
            for f in ("mean_sol", "total_sol", "ci90_lo_sol", "ex_top3_sol", "ex_best_day_sol"):
                self.assertAlmostEqual(v[f], want[k][f], places=12, msg=f"{k} {f}")
            self.assertTrue(v["passes"], (k, v["misses"]))
        self.assertAlmostEqual(res["refit"]["intercept"], C_CLOSED, places=9)  # the refit, from the closed form
        self.assertEqual(res["refit"]["refit_file"], "absent")
        self.assertEqual((res["counts"]["n_attempts"], res["counts"]["n_counted"], res["counts"]["n_excluded"]), (120, 120, 0))
        self.assertFalse(res["not_proposed_for_canary"])
        self.assertEqual(res["p"], 0.0)  # every bootstrap mean is positive

    def test_fail_cell_misses_are_named(self):
        b = self.make_book(kind=lambda d, i: "flat")
        res = self.decide(b["ri"])
        self.assertEqual(res["status"], "FAIL")
        self.assertNotIn("fail_label", res)
        for k, v in res["subcells"].items():
            self.assertFalse(v["passes"])
            joined = " ".join(v["misses"])
            self.assertIn("CI90 lo", joined)
            self.assertIn("ex-top-3", joined)
            self.assertIn("is not a majority", joined)
        self.assertEqual(res["p"], 1.0)

    def test_one_failing_sub_cell_fails_the_book(self):
        """The four sub-cells are a conjunction: a book whose flat cells pass and whose pressure cells fail is a FAIL (here by the refit intercept, next test)."""
        b = self.make_book(kind=lambda d, i: "marg")
        res = self.decide(b["ri"])
        self.assertTrue(res["subcells"]["flat_1p9_total"]["passes"] and res["subcells"]["flat_1p9_sc"]["passes"])
        self.assertFalse(res["subcells"]["press_1p9_total"]["passes"] or res["subcells"]["press_1p9_sc"]["passes"])
        self.assertEqual(res["status"], "FAIL")

    def test_the_refit_intercept_decides_not_the_frozen_one(self):
        """The ledger's own press_1p9 nets carry the FROZEN exploration intercept (provisional). On this book they would pass; at the refit intercept the pressure cells fail."""
        b = self.make_book(kind=lambda d, i: "marg")
        rows = [x for f in sorted((self.ledger / V1).glob("trades-*.jsonl")) for x in map(json.loads, f.read_text().splitlines())]
        stored = np.array([x["legs"]["press_1p9"]["net_lamports"] for x in rows if x["stake_sol"] == 0.3 and x["decision_ms"] < b["ri"]])
        self.assertEqual(len(stored), 120)
        self.assertGreater(stored.min(), 0)  # with the frozen intercept every trade is a win: the stored-net reading would pass
        self.assertEqual(arena_score.cell_verdict(arena_score.cell_stats(stored, [h5s.utc_day(x["decision_ms"]) for x in rows if x["stake_sol"] == 0.3 and x["decision_ms"] < b["ri"]]))["pass"], True)
        res = self.decide(b["ri"])
        v = res["subcells"]["press_1p9_total"]
        self.assertLess(v["mean_sol"], 0)
        self.assertNotAlmostEqual(v["total_sol"], stored.sum() / 1e9, places=3)
        self.assertAlmostEqual(res["refit"]["intercept"], C_CLOSED, places=9)
        self.assertNotAlmostEqual(res["refit"]["intercept"], cost.H5_PRESS_INTERCEPT["binding"], places=2)
        self.assertEqual(res["refit"]["frozen_exploration_intercept_not_used"], cost.H5_PRESS_INTERCEPT["binding"])
        self.assertAlmostEqual(v["intercept"], res["refit"]["intercept"])
        want = (1 - cost.press_p(C_CLOSED, 0, 0)) * nofail("marg") - cost.press_p(C_CLOSED, 0, 0) * cost.SEND
        self.assertAlmostEqual(v["mean_sol"], want / 1e9, places=12)

    def test_refit_file_must_match_the_recomputed_intercept(self):
        b = self.make_book()
        rows = [x for f in sorted((self.ledger / V1).glob("trades-*.jsonl")) for x in map(json.loads, f.read_text().splitlines()) if x["decision_ms"] < b["ri"]]
        cells = h5s.refit_cells(rows)
        target = self.ledger / V1 / f"refit-{h5s.utc_day(b['ri'])}.json"
        h5s._write_once_json(target, {"bot_id": V1, "read_instant_ms": b["ri"], "cells": cells})
        self.assertEqual(self.decide(b["ri"])["refit"]["refit_file"], "matched")
        target.unlink()
        bad = {k: dict(v, intercept=v["intercept"] + 0.01) for k, v in cells.items()}
        h5s._write_once_json(target, {"bot_id": V1, "read_instant_ms": b["ri"], "cells": bad})
        with self.assertRaisesRegex(R.Refused, "differs from the intercept recomputed"):
            self.decide(b["ri"])

    def test_the_one_point_three_second_leg_never_decides(self):
        """A print in slot 1205 makes the 1.3 s entry (END = that print) a guarded revert for every trade; the 1.9 s entry is unchanged, so the book still passes."""
        b = self.make_book(bad13=True)
        rows = [x for f in sorted((self.ledger / V1).glob("trades-*.jsonl")) for x in map(json.loads, f.read_text().splitlines())]
        self.assertTrue(all(x["legs"]["flat_1p3"]["state"] == "guarded" for x in rows))
        res = self.decide(b["ri"])
        self.assertEqual(res["status"], "PASS")
        self.assertTrue(all("_1p9_" in k for k in res["subcells"]))

    def test_rent_zero_leg_is_beside_and_never_decides(self):
        b = self.make_book()
        res = self.decide(b["ri"])
        kinds, days = self.counted_days(b)
        want0 = expected_cells(kinds, days, rent0=True)
        p = cost.press_p(C_CLOSED, 0, 0)
        for k, v in res["subcells"].items():
            z = v["rent_0"]
            self.assertIn("report-only, added after the clock, never deciding", z["label"])
            self.assertEqual(z["n_credited"], 120)
            self.assertAlmostEqual(z["mean_sol"], want0[k]["mean_sol"], places=12)
            credit = (1 - cost.FAIL_FLAT) * cost.RENT if k.startswith("flat") else (1 - p) * cost.RENT
            self.assertAlmostEqual(z["mean_sol"] - v["mean_sol"], credit / 1e9, places=11)

    def test_a_book_that_passes_only_at_rent_zero_has_failed(self):
        nf = nofail("rent0")
        self.assertTrue(-1_800_000 < nf < -300_000, nf)  # rent-charged negative, rent 0 positive on every sub-cell
        b = self.make_book(kind=lambda d, i: "rent0")
        res = self.decide(b["ri"])
        self.assertEqual(res["status"], "FAIL")
        self.assertTrue(res["passes_only_at_rent_0"])
        self.assertTrue(all(v["rent_0"]["criteria_met"] and not v["passes"] for v in res["subcells"].values()))
        rc, out, _ = self.cli(*self.args(b["ri"], V1))
        self.assertEqual(rc, 0)
        self.assertIn("STATUS FAIL", out)
        self.assertIn("This book has FAILED its read: it passes only at rent 0", out)

    def test_fail_n_below_100_is_labelled(self):
        """Exactly 100 attempts at the boundary and one exclusion: 99 priced trades, every miss is the count (final, not a loss)."""
        b = self.make_book(per_day=20, excluded={(0, 3)})
        res = self.decide(b["ri"])
        self.assertEqual((res["counts"]["n_attempts"], res["counts"]["n_counted"], res["counts"]["n_excluded"]), (100, 99, 1))
        self.assertEqual(res["status"], "FAIL")
        self.assertEqual(res["fail_label"], "FAIL (n < 100)")
        self.assertTrue(all(m.startswith("n ") for v in res["subcells"].values() for m in v["misses"]))

    def test_not_proposed_for_canary_when_ex_best_day_is_not_positive(self):
        """The gate passes (3 of 5 days positive, CI90 lo > 0, ex-top-3 > 0) but the profit is one day's: total ex best day is negative, so no canary is proposed."""
        self.assertGreater(nofail("small"), 300_000)
        b = self.make_book(kind=lambda d, i: "win" if d == 0 else ("small" if d in (1, 2) else "loss"))
        res = self.decide(b["ri"])
        self.assertEqual(res["status"], "PASS", {k: v["misses"] for k, v in res["subcells"].items()})
        self.assertTrue(all(v["ex_best_day_sol"] < 0 for v in res["subcells"].values()))
        self.assertTrue(res["not_proposed_for_canary"])
        rc, out, _ = self.cli(*self.args(b["ri"], V1))
        self.assertIn("NOT PROPOSED FOR CANARY", out)


class FivePercentRule(Base):
    def test_exactly_five_percent_is_not_over_the_cap(self):
        b = self.make_book(excluded={(0, i) for i in range(6)})  # 6 of 120 = 5.000%
        res = self.decide(b["ri"])
        self.assertEqual((res["counts"]["n_attempts"], res["counts"]["n_excluded"]), (120, 6))
        self.assertEqual(res["status"], "PASS")

    def test_above_five_percent_is_not_decidable_and_prints_counts_only(self):
        b = self.make_book(excluded={(0, i) for i in range(7)})  # 7 of 120 = 5.83%
        res = self.decide(b["ri"])
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertNotIn("subcells", res)
        self.assertNotIn("p", res)
        self.assertEqual(res["counts"]["excluded_by_reason"], {"gap_flag": 7})
        self.assertTrue(any("above 5%" in x for x in res["reasons"]))
        rc, out, _ = self.cli(*self.args(b["ri"], V1))
        self.assertEqual(rc, 0)
        self.assertIn("STATUS NOT_DECIDABLE", out)
        self.assertNotIn("ci90lo", out)
        self.assertNotIn("DECIDING CELL (RENT-CHARGED)", out)

    def test_refusals_are_not_in_the_denominator(self):
        """7 exclusions among 120 non-refused attempts is NOT_DECIDABLE even with 200 refusals on the book (if refusals counted, 7 / 320 would pass)."""
        b = self.make_book(excluded={(0, i) for i in range(7)}, refused=200)
        res = self.decide(b["ri"])
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertEqual(res["counts"]["n_attempts"], 120)
        self.assertEqual(res["counts"]["n_refused"], 200)

    def test_refusals_alone_do_not_make_a_book_not_decidable_and_are_disclosed(self):
        b = self.make_book(refused=60)
        res = self.decide(b["ri"])
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["counts"]["n_refused"], 60)
        self.assertEqual(res["counts"]["refused_by_reason"], {"bad_intent:base_breaks_unresolved_settled": 60})
        self.assertTrue(res["attempt_definition"].startswith(ATTEMPT_SENTENCE_PREFIX))
        self.assertIn("refused 60 of 180 triggers", res["attempt_definition"])
        rc, out, _ = self.cli(*self.args(b["ri"], V1))
        self.assertIn("refused (not attempts, DEC-031 Amendment 1) 60", out)
        self.assertIn("Amendment 1 refusals h5v1-boostfloor-a1: 60", out)

    def test_a_counted_trade_without_its_deciding_leg_pnl_is_not_decidable(self):
        def blank(rows):
            for x in rows:
                if x["pool"] == "P1x05" and x["stake_sol"] == 0.3:
                    for leg in ("flat_1p9", "press_1p9"):
                        x["legs"][leg] = {"net_lamports": None, "state": "exit_before_landing"}
        b = self.make_book(mutate=blank)
        res = self.decide(b["ri"])
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertEqual(res["counts"]["n_unpriced_deciding_leg"], 1)
        self.assertNotIn("subcells", res)


class Seal(Base):
    def setUp(self):
        super().setUp()
        self.b = self.make_book()
        p = mock.patch.object(h5s, "ledger_attempts_before", side_effect=AssertionError("a ledger row was opened behind the seal"))
        self.opened = p.start()
        self.addCleanup(p.stop)

    def refused(self, argv, pattern):
        rc, out, err = self.cli(*argv)
        self.assertEqual(rc, 3, (out, err))
        self.assertRegex(err, pattern)
        self.assertEqual(out, "")

    def test_the_seal_is_arena_h5_legs_own_and_not_a_copy(self):
        self.assertIs(R.L, arena_h5_legs)
        src = Path(R.__file__).read_text()
        for name in ("def gate", "def check_path", "def load_book_rows", "def check_ledger_rows", "def cell_values", "def items_from_rows", "FORBIDDEN_PARTS = "):
            self.assertNotIn(name, src)
        for flag in ("--i-am-the-read", "--read-instant", "--now-ms"):
            self.assertIn(flag, src)

    def test_refusals_before_any_row_is_opened(self):
        ri = self.b["ri"]
        a = self.args(ri, V1)
        self.refused([x for x in a if x != "--i-am-the-read"], "--i-am-the-read is required")
        i = a.index("--read-instant")
        self.refused(a[:i] + a[i + 2:], "--read-instant is required")
        self.refused(self.args(ri - 1, V1), "before the book's recorded read instant")
        j = a.index("--now-ms")
        self.refused(a[:j + 1] + [str(ri - 1)] + a[j + 2:], "now .* is before the book's recorded read instant")
        self.opened.assert_not_called()

    def test_now_ms_is_clamped_to_the_wall_clock(self):
        ri = self.b["ri"]
        with mock.patch.object(R, "wall_ms", return_value=ri - 1):
            a = self.args(ri, V1)
            j = a.index("--now-ms")
            self.refused(a[:j + 1] + [str(ri + 10**12)] + a[j + 2:], "now .* is before the book's recorded read instant")
        self.opened.assert_not_called()

    def test_no_recorded_instant_and_open_attempts(self):
        (self.ledger / V1 / h5s.READ_INSTANT_FILE).rename(self.ledger / V1 / "moved.json")
        self.refused(self.args(self.b["ri"], V1), "no recorded read instant")
        (self.ledger / V1 / "moved.json").rename(self.ledger / V1 / h5s.READ_INSTANT_FILE)
        doc = json.loads((self.ledger / V1 / h5s.READ_INSTANT_FILE).read_text())
        (self.ledger / V1 / h5s.READ_INSTANT_FILE).write_text(json.dumps(dict(doc, open_before_read=1)))
        self.refused(self.args(self.b["ri"], V1), "still open")
        self.opened.assert_not_called()

    def test_closed_names_in_ids_and_paths(self):
        ri = self.b["ri"]
        with self.assertRaisesRegex(R.Refused, "contains 'probe'"):
            R.decide_book("h5v1-probe", self.ledger, read_instant_ms=ri, i_am_the_read=True, now_ms=ri + 1)
        with self.assertRaisesRegex(R.Refused, "is not an H5 arena book"):
            R.decide_book("hv4-CON-L", self.ledger, read_instant_ms=ri, i_am_the_read=True, now_ms=ri + 1)
        with self.assertRaisesRegex(R.Refused, "contains 'canary'"):
            R.decide_book(V1, self.root / "canary" / "ledger", read_instant_ms=ri, i_am_the_read=True, now_ms=ri + 1)
        self.refused(self.args(ri, V1, extra=("--json", str(self.root / "out" / "sealed-read.json"))), "contains 'sealed'")
        self.refused(self.args(ri, V1, extra=("--json", "/data/h5-shadow/x.json")), "contains '/data/h5-shadow'")
        self.opened.assert_not_called()

    def test_no_default_for_any_path(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                R.main(["--read-instant", "1", "--i-am-the-read", "--n-books", "2", "--c", "1"])  # --ledger-root is required

    def test_the_multiplicity_parameters_are_required_before_anything_opens(self):
        ri = self.b["ri"]
        a = self.args(ri, V1)
        i = a.index("--n-books")
        self.refused(a[:i] + a[i + 2:], "--n-books \\(N\\) and --c are required")
        j = a.index("--c")
        self.refused(a[:j] + a[j + 2:], "--n-books \\(N\\) and --c are required")
        self.opened.assert_not_called()


class Damage(Base):
    def test_a_torn_ledger_line_is_refused_not_ruled(self):
        b = self.make_book()
        f = sorted((self.ledger / V1).glob("trades-*.jsonl"))[0]
        f.write_text(f.read_text() + '{"trade_id": "torn\n')
        rc, out, err = self.cli(*self.args(b["ri"], V1))
        self.assertEqual(rc, 3)
        self.assertIn("the ledger cannot be read", err + out)

    def test_refusals_by_reason_must_match_the_record(self):
        b = self.make_book(refused=4)
        p = self.ledger / V1 / h5s.READ_INSTANT_FILE
        doc = json.loads(p.read_text())
        p.write_text(json.dumps(dict(doc, refused_by_reason_before={"bad_intent:ids": 4})))
        with self.assertRaisesRegex(R.Refused, "refusals by reason"):
            self.decide(b["ri"])

    def test_the_recorded_instant_is_recomputed_from_decision_timestamps(self):
        b = self.make_book()
        p = self.ledger / V1 / h5s.READ_INSTANT_FILE
        doc = json.loads(p.read_text())
        later = b["ri"] + h5s.DAY_MS  # a record that is consistent with the ledger's counts but is not the first qualifying boundary
        p.write_text(json.dumps(dict(doc, read_instant_ms=later, attempts_before=144, dates=6, triggers_before=144)))
        with self.assertRaisesRegex(R.Refused, "recomputed from the ledger's decision timestamps differs"):
            R.decide_book(V1, self.ledger, read_instant_ms=later, i_am_the_read=True, now_ms=later + 1)

    def test_a_row_the_scorer_did_not_write_is_refused(self):
        def bump(rows):
            rows[0]["legs"]["flat_1p9"]["net_lamports"] += 5_000
        b = self.make_book(mutate=bump)
        with self.assertRaisesRegex(R.Refused, "not the scorer's rows"):
            self.decide(b["ri"])


class CliAndBlock(Base):
    def two_books(self):
        b1 = self.make_book(V1)
        b2 = self.make_book(V2, kind=lambda d, i: "flat")
        return b1, b2

    def test_both_books_by_default_with_json_provenance_and_the_block(self):
        b1, b2 = self.two_books()
        ri = max(b1["ri"], b2["ri"])
        js = self.root / "out" / "read.json"
        js.parent.mkdir()
        rc, out, err = self.cli(*self.args(ri, extra=("--cells-total", "123", "--prior-read", "hv4-CON-L=FAIL", "--other-p", "hv4-F=0.3", "--other-p", "j3-a1=0.02", "--json", str(js))))
        self.assertEqual((rc, err), (0, ""), out)
        self.assertIn(f"BOT {V1}  STATUS PASS", out)
        self.assertIn(f"BOT {V2}  STATUS FAIL", out)
        rep = json.loads(js.read_text())
        self.assertEqual(sorted(rep["books"]), [V1, V2])
        m = rep["multiplicity"]
        self.assertEqual((m["N"], m["c"], m["cells_in_total"]), (10, 3, 123))
        self.assertEqual(m["books_read_with_verdicts"], {"hv4-CON-L": "FAIL", V1: "PASS", V2: "FAIL"})
        self.assertIn("1 - 0.95^N = 40% at N = 10", m["plain_line"])
        self.assertAlmostEqual(m["confirmation_threshold_0.05_over_c"], 0.05 / 3)
        self.assertEqual(m["p_by_book"], {V1: 0.0, V2: 1.0})
        h = m["screen_read_line"]
        self.assertEqual((h[V1]["rank"], h[V1]["m"]), (1, 4))  # p 0.0 < 0.02 < 0.3 < 1.0
        self.assertAlmostEqual(h[V1]["holm_level"], 0.05 / 4)
        self.assertEqual((h[V2]["rank"], h[V2]["m"]), (4, 4))
        self.assertAlmostEqual(h[V2]["holm_level"], 0.05)
        self.assertEqual(sorted(m["holm_over_reads"]), sorted([V1, V2, "hv4-F", "j3-a1"]))
        self.assertIn("(e) siblings forward", m["taint_line"])
        self.assertIn("report-only, added after the clock, never deciding", m["rent_line"])
        self.assertIn("MULTIPLICITY BLOCK", out)
        self.assertIn("1 - 0.95^N = 40% at N = 10", out)
        self.assertIn("Holm rank 1 of 4", out)
        for name, sha in rep["books"][V1]["provenance"].items():
            if name.startswith("tools/"):
                self.assertEqual(sha, R.sha256_file(Path(R.__file__).parent.parent / name))
        self.assertIn("read-instant.json", rep["books"][V1]["provenance"])
        self.assertEqual(json.loads(js.read_text()), rep)  # the atomic write is complete JSON

    def test_n_must_cover_the_books_read(self):
        b1, b2 = self.two_books()
        rc, _, err = self.cli(*self.args(max(b1["ri"], b2["ri"]), n="1"))
        self.assertEqual(rc, 3)
        self.assertIn("smaller than the", err)

    def test_bad_parameters_are_refused(self):
        b1, _ = self.two_books()
        for extra, pat in ((("--other-p", "x=1.5"), "a p-value is in"), (("--other-p", "x"), "is not BOT=VALUE"), (("--other-p", "x=a"), "does not parse"),
                           (("--prior-read", f"{V1}=PASS"), "is in this run"), (("--cells-total", "0"), "must be positive")):
            rc, _, err = self.cli(*self.args(b1["ri"], V1, extra=extra))
            self.assertEqual(rc, 3, extra)
            self.assertRegex(err, pat)

    def test_one_book_not_yet_at_its_instant_is_refused_and_the_other_is_read(self):
        b1 = self.make_book(V1)
        rc, out, err = self.cli(*self.args(b1["ri"]))  # V2 has no record
        self.assertEqual(rc, 3)
        self.assertIn(f"BOT {V1}  STATUS PASS", out)
        self.assertIn(f"REFUSED {V2}", out)

    def test_not_decidable_book_has_no_p_and_is_not_ranked(self):
        b = self.make_book(excluded={(0, i) for i in range(7)})
        js = self.root / "nd.json"
        rc, out, _ = self.cli(*self.args(b["ri"], V1, extra=("--other-p", "x=0.5", "--json", str(js))))
        self.assertEqual(rc, 0)
        m = json.loads(js.read_text())["multiplicity"]
        self.assertEqual(m["p_by_book"], {V1: None})
        self.assertEqual(m["screen_read_line"], {})
        self.assertEqual(m["books_read_with_verdicts"], {V1: "NOT_DECIDABLE"})

    def test_p_is_reproducible_and_the_stack_is_untouched(self):
        self.assertEqual(R.boot_p_le_zero([0.1, -0.2, 0.3, 0.05, -0.01] * 20), R.boot_p_le_zero([0.1, -0.2, 0.3, 0.05, -0.01] * 20))
        self.assertIsNone(R.boot_p_le_zero([]))
        self.assertEqual((R.P_DRAWS, R.P_SEED), (10_000, 1))
        self.assertEqual((arena_score.MIN_N, arena_score.MIN_DAYS, arena_score.MAX_EXCLUDED_SHARE), (100, 5, 0.05))
        self.assertEqual(R.DECIDING_STAKE, "0.30")
        self.assertEqual(cost.RENT, 2_039_280)  # the deciding rent of the H5 books is not changed here

    def test_holm_rank_ties_and_levels(self):
        h = R.holm_rank({"b": 0.01, "a": 0.01, "c": 0.5})
        self.assertEqual([(k, h[k]["rank"]) for k in ("a", "b", "c")], [("a", 1), ("b", 2), ("c", 3)])
        self.assertAlmostEqual(h["a"]["holm_level"], 0.05 / 3)
        self.assertAlmostEqual(h["c"]["holm_level"], 0.05)


if __name__ == "__main__":
    unittest.main()
