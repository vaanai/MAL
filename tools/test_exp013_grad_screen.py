"""Tests for tools/exp013_grad_screen.py. Synthetic fixtures outside /data/mal only; no real data is read."""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import tools.exp013_grad_model as gm
import tools.exp013_grad_screen as sc
from tools.exp013_fixtures import write_view_sha256

UTC = timezone.utc
POOL_DAYS = [f"2026-09-{d}" for d in range(19, 28)]
AUG_DAYS = ["2026-08-10", "2026-08-11", "2026-08-12"]
LAM = 1_000_000_000
READ_DIR = Path(__file__).resolve().parent.parent / "ARTIFACTS" / "exp012" / "read"
POOL_RUNS = {"A": [["2026-09-19T01", "2026-09-21T23"]], "C": [["2026-09-22T00", "2026-09-25T06"]], "B": [["2026-09-25T07", "2026-09-27T23"]],
             "X": [["2026-08-10T00", "2026-08-12T23"]]}


def day_ms(day: str, plus: int = 0) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000) + plus


def hour_ms(hour: str) -> int:
    return int(datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=UTC).timestamp()) * 1000


def pool_of(day: str) -> str:
    if day in AUG_DAYS:
        return "X"
    return "A" if day <= "2026-09-21" else ("C" if day <= "2026-09-25" else "B")


def synth_rows(days, per_day=30, seed=5, ks=(1, 4, 8), skip_k8=()) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for di, day in enumerate(days):
        for i in range(per_day):
            feats = {n: rng.random() for n in gm.FEATURE_NAMES}
            signal = feats["buy_sol"] + 0.5 * rng.random()
            for k in ks:
                if k == 8 and f"m{di}-{i:03d}" in skip_k8:
                    continue
                press = int(round((signal - 0.9) * 40_000_000 + rng.gauss(0, 5_000_000)))
                rows.append(
                    {"mint": f"m{di}-{i:03d}", "day": day, "pool": pool_of(day), "entry_land_k": k, "filled": i % 5 != 0, "flat": press - 1000, "press": press,
                     "trigger_ms": day_ms(day, 3_600_000 + i * 1000), "edge_left": day == days[0], "edge_right": day == days[-1], "features": dict(feats)}
                )
    return rows


class Fixture:
    """A table dir, its view manifest and an EXP-012 dir under one temp dir."""

    def __init__(self, td: str, rows=None, days=None, censored=None) -> None:
        self.root = Path(td)
        self.rows = rows if rows is not None else synth_rows(days or (AUG_DAYS + POOL_DAYS))
        self.roots, self.shas = {}, {}
        for name in ("fast", "insample", "live"):
            r = self.root / "views" / name
            r.mkdir(parents=True)
            (r / "a.txt").write_text(name)
            self.shas[name] = hashlib.sha256(write_view_sha256(r).read_bytes()).hexdigest()
            self.roots[name] = str(r)
        xr = self.root / "views" / "explore-0814-w1"
        xr.mkdir()
        (xr / "a.txt").write_text("x")
        sha_file = write_view_sha256(xr)
        self.extra = [{"root": str(xr), "hours": ["2026-08-10T00", "2026-08-12T23"], "n_hours": 72, "days": AUG_DAYS,
                       "view_sha256_file_sha256": hashlib.sha256(sha_file.read_bytes()).hexdigest()}]
        d = self.root / "table"
        d.mkdir()
        data = "".join(json.dumps(r) + "\n" for r in self.rows).encode()
        (d / "table.jsonl").write_bytes(data)
        md5 = hashlib.md5(data).hexdigest()
        (d / "table.md5").write_text(md5 + "\n")
        (d / "manifest.json").write_text(json.dumps({"schema": "x", "n_rows": len(self.rows), "table_md5": md5, "pool_runs": POOL_RUNS, "roots": self.roots,
                                                      "view_sha256_file_sha256": self.shas, "extra_views": self.extra}))
        if censored is not None:
            (d / "censored.jsonl").write_text("".join(json.dumps(c) + "\n" for c in censored))
        self.table = d
        self.view_manifest = self.root / "views.json"
        self.write_view_manifest()
        self.e12 = self.root / "exp012"
        self.e12.mkdir()
        self.write_e12()
        self.ledger, self.tries, self.out = self.root / "ledger", self.root / "tries.jsonl", self.root / "out"

    def vm_doc(self) -> dict:
        ranges = {"A": ("2026-09-19T01", "2026-09-21T23"), "C": ("2026-09-22T00", "2026-09-25T06"), "B": ("2026-09-25T07", "2026-09-27T23")}
        name = {"A": "fast", "C": "insample", "B": "live"}
        return {"pools": {t: {"root": self.roots[name[t]], "hours": list(ranges[t]), "view_sha256_file_sha256": self.shas[name[t]],
                              "view_sha256_mtime_utc": "2026-10-03T00:00:00Z"} for t in ranges},
                "extra_views": [dict(v, view_sha256_mtime_utc="2026-10-03T00:00:00Z") for v in self.extra]}

    def write_view_manifest(self, doc=None) -> None:
        self.view_manifest.write_text(json.dumps(doc or self.vm_doc()))

    def write_e12(self, threshold=0.5) -> None:
        rng = random.Random(3)
        k4 = [r for r in self.rows if r["entry_land_k"] == 4 and r["day"] in POOL_DAYS]
        oof = [{"day": r["day"], "mint": r["mint"], "score": rng.random(), "label": 0, "filled": True} for r in k4]
        (self.e12 / "oof_scores.json").write_text(json.dumps({"days": POOL_DAYS, "rows": oof}))
        (self.e12 / "threshold.json").write_text(json.dumps({"threshold": threshold}))
        per_day = [{"day": d, "n_entered": 10 + i, "flat_mean_pct": float(i - 3), "press_mean_pct": float(2 * i - 5)} for i, d in enumerate(POOL_DAYS)]
        (self.e12 / "nested_fixed_threshold_lodo.json").write_text(json.dumps({"per_day": per_day}))

    def run(self, **kw):
        kw.setdefault("ledger_dir", self.ledger)
        kw.setdefault("tries_log", self.tries)
        kw.setdefault("exp012_dir", self.e12)
        kw.setdefault("e12_expect", None)
        return sc.run(self.table, self.view_manifest, kw.pop("out_dir", self.out), **kw)


def trade(day: str, flat: float, press: float | None = None, mint: str | None = None, i: int = 0, pool: str = "X") -> dict:
    return {"mint": mint or f"{day}-{i}", "day": day, "trigger_ms": day_ms(day, 1000 * (i + 1)), "flat": int(flat * LAM), "press": int((flat if press is None else press) * LAM),
            "filled": True, "edge": False, "pool": pool}


def winners(days, per_day=40, mean=0.01, pool="X") -> list[dict]:
    rng = random.Random(1)
    return [trade(d, mean + rng.uniform(0.0, 0.01), mint=f"{d}-w{i}", i=i, pool=pool) for d in days for i in range(per_day)]


class BarTests(unittest.TestCase):
    DAYS = [f"2026-08-{d}" for d in range(10, 16)]

    def test_all_bars_pass(self) -> None:
        t = winners(self.DAYS)
        for item in (sc.item_1, sc.item_2, sc.item_3):
            self.assertTrue(item(t)["pass"], item.__name__)
            self.assertTrue(item(t, set(self.DAYS))["pass"], item.__name__)

    def test_bar1_fails_on_noise(self) -> None:
        rng = random.Random(2)
        t = [trade(d, rng.gauss(0, 0.05), mint=f"{d}-{i}", i=i) for d in self.DAYS for i in range(10)]
        self.assertFalse(sc.item_1(t)["pass"])

    def test_bar1_needs_positive_mean_too(self) -> None:
        t = [trade(d, -0.01, mint=f"{d}-{i}", i=i) for d in self.DAYS for i in range(10)]
        self.assertFalse(sc.item_1(t)["pass"])

    def test_bar2_fails_when_top3_carry(self) -> None:
        t = [trade(d, -0.01, mint=f"{d}-{i}", i=i) for d in self.DAYS for i in range(10)]
        t += [trade(self.DAYS[0], 5.0, mint=f"big{i}", i=50 + i) for i in range(3)]
        self.assertGreater(sum(x["flat"] for x in t), 0)
        self.assertFalse(sc.item_2(t)["pass"])

    def test_bar2_none_fails(self) -> None:
        self.assertFalse(sc.item_2([])["pass"])

    def test_bar3_fails_when_minority_days_positive(self) -> None:
        t = [trade(d, -0.01, mint=f"{d}-{i}", i=i) for d in self.DAYS[:4] for i in range(5)]
        t += [trade(d, 1.0, mint=f"{d}-p{i}", i=i) for d in self.DAYS[4:] for i in range(5)]
        r = sc.item_3(t)
        self.assertFalse(r["pass"])
        self.assertEqual(r["legs"]["flat"]["days_positive"], 2)

    def test_bar3_denominator_is_every_manifest_day(self) -> None:
        t = winners(self.DAYS[:3])  # 3 positive days
        self.assertTrue(sc.item_3(t)["pass"])  # default N = days with a trade
        wide = set(self.DAYS) | {"2026-08-20", "2026-08-21", "2026-08-22"}  # N = 9: empty days are not positive
        r = sc.item_3(t, wide)
        self.assertFalse(r["pass"])
        self.assertEqual((r["legs"]["flat"]["days_positive"], r["legs"]["flat"]["n_manifest_days"]), (3, 9))
        self.assertTrue(r["legs"]["flat"]["book_stats_majority_days_positive"])  # the gate's own rule is reported alongside

    def test_one_leg_failing_fails_the_item(self) -> None:
        t = [dict(x, press=-int(0.01 * LAM)) for x in winners(self.DAYS)]
        self.assertFalse(sc.item_1(t)["pass"])
        self.assertTrue(sc.leg_bars(t, "flat")["pass"])

    def test_book_stats_called_once_per_leg_without_pressure_kw(self) -> None:
        with mock.patch.object(sc, "book_stats", wraps=sc.book_stats) as bs:
            sc.item_1(winners(self.DAYS, per_day=3))
        self.assertEqual(bs.call_count, 2)
        for call in bs.call_args_list:
            self.assertEqual(len(call.args), 1)
            self.assertEqual(call.kwargs, {})

    def test_trade_time_is_trigger_time_and_day_asserted(self) -> None:
        t = [trade("2026-08-10", 0.1, i=1)]
        self.assertEqual(sc.book(t, "flat")[0].t_ms, day_ms("2026-08-10", 2000))
        self.assertEqual(sc.book(t, "flat")[0].pnl, int(0.1 * LAM))
        t[0]["trigger_ms"] = day_ms("2026-08-11", 5000)  # day != UTC day of trigger_ms
        with self.assertRaises(SystemExit):
            sc.book(t, "flat")

    def test_gate_counts_report_only(self) -> None:
        b = sc.bars(winners(self.DAYS, per_day=10))
        self.assertFalse(b["legs"]["flat"]["report_only_n_ge_100"])
        self.assertTrue(b["legs"]["flat"]["report_only_days_ge_5"])
        self.assertTrue(b["pass"])  # n < 100 does not fail the screen

    def test_miss_rows_stay_as_trades(self) -> None:
        rows = synth_rows(["2026-08-10"], per_day=5, ks=(4,))
        sel = [{"day": "2026-08-10", "mint": f"m0-00{i}", "score": 1.0, "threshold": 0.5} for i in range(5)]
        t = sc.selected_trades(sel, rows)
        self.assertEqual(len(t), 5)
        self.assertTrue(any(not x["filled"] for x in t))


class ExclusionTests(unittest.TestCase):
    END = hour_ms("2026-09-21T23") + 3_600_000

    def row(self, k: int, trigger_ms: int, pool: str = "A") -> dict:
        return {"mint": "m", "entry_land_k": k, "trigger_ms": trigger_ms, "pool": pool}

    def test_boundary_at_or_after_run_end(self) -> None:
        for k in (1, 4, 8):
            reach = sc.EXCLUDE_CAP_MS + (k + max(4, k)) * sc.SLOT_MS
            self.assertEqual(reach, 1_800_000 + (k + max(4, k)) * 400)
            self.assertTrue(sc.trigger_excluded(self.row(k, self.END - reach), POOL_RUNS))  # lands exactly at the end: excluded
            self.assertFalse(sc.trigger_excluded(self.row(k, self.END - reach - 1), POOL_RUNS))

    def test_k8_reaches_further_than_k4(self) -> None:
        t = self.END - (sc.EXCLUDE_CAP_MS + 8 * 400) - 1  # k=4 reach is 8*400, k=8 is 16*400
        self.assertFalse(sc.trigger_excluded(self.row(4, t), POOL_RUNS))
        self.assertTrue(sc.trigger_excluded(self.row(8, t), POOL_RUNS))

    def test_first_gap_start_after_trigger(self) -> None:
        runs = {"X": [["2026-08-10T00", "2026-08-10T05"], ["2026-08-10T08", "2026-08-12T23"]]}
        gap = hour_ms("2026-08-10T05") + 3_600_000  # gap starts when the first run ends
        self.assertEqual(sc.exit_bound_ms("X", gap - 10_000_000, runs), gap)
        self.assertTrue(sc.trigger_excluded(self.row(4, gap - 1_000_000, "X"), runs))
        self.assertFalse(sc.trigger_excluded(self.row(4, gap + 4 * 3_600_000, "X"), runs))
        self.assertTrue(sc.trigger_excluded(self.row(4, 0, "nowhere"), runs))

    def test_trigger_must_be_inside_a_run(self) -> None:
        runs = {"X": [["2026-08-10T00", "2026-08-10T05"], ["2026-08-10T08", "2026-08-12T23"]]}
        in_gap = hour_ms("2026-08-10T06")
        self.assertIsNone(sc.exit_bound_ms("X", in_gap, runs))
        self.assertTrue(sc.trigger_excluded(self.row(4, in_gap, "X"), runs))
        before = hour_ms("2026-08-09T23")
        self.assertIsNone(sc.exit_bound_ms("X", before, runs))
        self.assertTrue(sc.trigger_excluded(self.row(4, before, "X"), runs))
        self.assertIsNone(sc.exit_bound_ms("X", hour_ms("2026-08-13T00"), runs))
        self.assertEqual(sc.exit_bound_ms("X", hour_ms("2026-08-11T00"), runs), hour_ms("2026-08-12T23") + 3_600_000)
        self.assertFalse(sc.trigger_excluded(self.row(4, hour_ms("2026-08-11T00"), "X"), runs))
        self.assertEqual(sc.exit_bound_ms("X", hour_ms("2026-08-10T00"), runs), hour_ms("2026-08-10T05") + 3_600_000)

    def test_whatever_the_realized_exit(self) -> None:
        r = dict(self.row(4, self.END - 1000), flat=5, press=5, filled=True, outcome="tp")
        eligible, info = sc.split_eligible([r, dict(r, entry_land_k=8)], POOL_RUNS)
        self.assertEqual((len(eligible), info["n_excluded_by_trigger_time"], info["excluded_by_k"]), (0, 2, {"4": 1, "8": 1}))

    def test_censored_report_counts_would_keep(self) -> None:
        rows = [dict(self.row(4, hour_ms("2026-09-20T00")), mint="a"), dict(self.row(4, self.END - 1000), mint="b")]
        cens = [{"mint": "a", "entry_land_k": 8, "reason": "x"}, {"mint": "b", "entry_land_k": 8, "reason": "x"}, {"mint": "zz", "entry_land_k": 4, "reason": "y"}]
        r = sc.censored_report(cens, rows, POOL_RUNS)
        self.assertEqual((r["n_censored"], r["n_trigger_time_rule_would_keep"], r["n_trigger_time_unknown"], r["by_reason"]), (3, 1, 1, {"x": 2, "y": 1}))
        self.assertEqual(r["n_trigger_time_rule_would_keep_by_k"], {"8": 1})
        self.assertEqual(sc.censored_report(None, rows, POOL_RUNS), {"available": False})

    def test_excluded_rows_never_reach_selection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1] + POOL_DAYS[:1], per_day=3))
            seen = {}

            def fake(rows, days, n_jobs=1):
                seen["rows"] = list(rows)
                raise RuntimeError("stop")

            for r in fx.rows:  # push one mint to the very end of pool X
                if r["mint"] == "m0-000":
                    r["trigger_ms"] = hour_ms("2026-08-12T23") + 3_600_000 - 5000
            data = "".join(json.dumps(r) + "\n" for r in fx.rows).encode()
            (fx.table / "table.jsonl").write_bytes(data)
            (fx.table / "table.md5").write_text(hashlib.md5(data).hexdigest() + "\n")
            m = json.loads((fx.table / "manifest.json").read_text())
            m["table_md5"] = hashlib.md5(data).hexdigest()
            (fx.table / "manifest.json").write_text(json.dumps(m))
            with mock.patch.object(gm, "nested_lodo_select", fake), self.assertRaises(RuntimeError):
                fx.run()
        self.assertFalse(any(r["mint"] == "m0-000" for r in seen["rows"]))
        self.assertEqual(len(seen["rows"]), len(fx.rows) - 3)


class GroupTests(unittest.TestCase):
    MAN = {"pool_runs": {"A": [["2026-09-19T01", "2026-09-21T23"]], "C": [["2026-09-22T00", "2026-09-25T06"]], "B": [["2026-09-25T07", "2026-09-27T23"]],
                         "X": [["2026-08-10T00", "2026-08-11T23"], ["2026-08-13T00", "2026-08-13T23"]]}}
    X = ["2026-08-10", "2026-08-11", "2026-08-13"]
    A = ["2026-09-19", "2026-09-20", "2026-09-21"]

    def setUp(self) -> None:
        self.g, self.pd = sc.day_groups(self.MAN), sc.pool_days(self.MAN)

    def test_membership_from_manifest_pools(self) -> None:
        self.assertEqual(self.g["4b"], set(self.X))
        self.assertEqual(self.g["4a"], set(self.X) | set(self.A))
        self.assertNotIn("2026-09-22", self.g["4a"])
        self.assertEqual(len(sc.all_manifest_days(self.MAN)), 12)  # A 3 + C 4 + B 3 (2026-09-25 is in both C and B: counted once, 9 pool days) + X 3

    def test_items_restrict_by_pool_tag(self) -> None:
        t = winners(self.X, pool="X") + winners(self.A, pool="A") + [trade("2026-09-23", -9.0, i=1, pool="C")]
        a, b = sc.item_4a(t, self.g, self.pd), sc.item_4b(t, self.g, self.pd)
        self.assertEqual((a["legs"]["flat"]["n"], b["legs"]["flat"]["n"]), (240, 120))
        self.assertTrue(a["pass"] and b["pass"])
        self.assertEqual((a["item"], b["item"]), ("4a", "4b"))

    def test_pool_tag_must_agree_with_day_list(self) -> None:
        with self.assertRaises(SystemExit):
            sc.item_4b([trade("2026-09-19", 0.1, pool="X")], self.g, self.pd)  # X tag on an A day
        with self.assertRaises(SystemExit):
            sc.item_4a([trade("2026-08-10", 0.1, pool="C")], self.g, self.pd)  # C tag on an X day

    def test_4b_fails_when_august_loses(self) -> None:
        t = winners(self.A, pool="A") + [trade(d, -0.02, mint=f"{d}-{i}", i=i) for d in self.X for i in range(10)]
        self.assertFalse(sc.item_4b(t, self.g, self.pd)["pass"])

    def test_empty_group_fails(self) -> None:
        self.assertFalse(sc.item_4b(winners(self.A, pool="A"), self.g, self.pd)["pass"])

    def test_4b_bar3_counts_empty_manifest_days(self) -> None:
        t = winners(self.X[:1], pool="X")  # 1 of 3 August days positive: 1*2 > 3 is false
        self.assertFalse(sc.item_4b(t, self.g, self.pd)["pass"])
        self.assertTrue(sc.item_4b(winners(self.X[:2], pool="X"), self.g, self.pd)["pass"])


class Item5Tests(unittest.TestCase):
    def test_pass_fail_and_missing_k8(self) -> None:
        rows = synth_rows(["2026-08-10"], per_day=6, skip_k8={"m0-002"})
        for r in rows:
            if r["entry_land_k"] == 8:
                r["flat"], r["press"] = 1000, 2000
        sel = [{"day": "2026-08-10", "mint": f"m0-00{i}", "score": 1.0, "threshold": 0.5} for i in range(4)]
        r = sc.item_5(sel, rows)
        self.assertTrue(r["pass"])
        self.assertEqual((r["n_selected"], r["n_joined"], r["n_missing_k8"]), (4, 3, 1))
        for r8 in rows:
            if r8["entry_land_k"] == 8 and r8["mint"] == "m0-000":
                r8["press"] = -10_000_000
        self.assertFalse(sc.item_5(sel, rows)["pass"])  # one leg negative fails

    def test_missing_split_trigger_excluded_vs_table_censored(self) -> None:
        full = synth_rows(["2026-08-10"], per_day=4, skip_k8={"m0-003"})  # m0-003: table-censored at k=8
        eligible = [r for r in full if not (r["entry_land_k"] == 8 and r["mint"] == "m0-002")]  # m0-002: excluded by trigger time
        sel = [{"day": "2026-08-10", "mint": f"m0-00{i}", "score": 1.0, "threshold": 0.5} for i in range(4)]
        r = sc.item_5(sel, eligible, full)
        self.assertEqual((r["n_missing_k8"], r["n_missing_k8_trigger_time_excluded"], r["n_missing_k8_table_censored"]), (2, 1, 1))

    def test_nothing_joined_fails(self) -> None:
        rows = synth_rows(["2026-08-10"], per_day=2, ks=(4,))
        r = sc.item_5([{"day": "2026-08-10", "mint": "m0-000", "score": 1.0, "threshold": 0.5}], rows)
        self.assertFalse(r["pass"])
        self.assertEqual(r["n_missing_k8"], 1)


class Item6Tests(unittest.TestCase):
    def test_jaccard_known_set(self) -> None:
        self.assertEqual(sc.jaccard({1, 2, 3}, {2, 3, 4}), 0.5)
        self.assertIsNone(sc.jaccard(set(), set()))

    def rows(self):
        return synth_rows(["2026-08-10", "2026-09-19", "2026-09-20", "2026-09-21"], per_day=10)

    def test_known_overlap_restricted_b_and_unrestricted(self) -> None:
        rows = self.rows()
        trades = [trade("2026-09-19", 0.1, mint="m1-000"), trade("2026-09-19", 0.1, mint="m1-001"), trade("2026-09-20", 0.1, mint="m2-000"), trade("2026-08-10", 0.1, mint="m0-000")]
        e12_sel = {("2026-09-19", "m1-001"), ("2026-09-20", "m2-000"), ("2026-09-20", "m2-001"), ("2026-09-21", "m3-000"), ("2026-09-22", "not-in-table")}
        r = sc.item_6(trades, rows, e12_sel)
        self.assertEqual((r["n_a"], r["n_b"], r["n_b_unrestricted"], r["n_both"]), (3, 4, 5, 2))
        self.assertAlmostEqual(r["jaccard"], 2 / 5)  # the August trade is not in A
        self.assertAlmostEqual(r["jaccard_unrestricted_b_report_only"], 2 / 6)
        self.assertAlmostEqual(r["overlap_min_report_only"], 2 / 3)
        self.assertTrue(r["pass"])

    def test_high_overlap_fails_and_boundary(self) -> None:
        rows = self.rows()
        trades = [trade("2026-09-19", 0.1, mint=f"m1-00{i}") for i in range(4)]
        r = sc.item_6(trades, rows, {("2026-09-19", f"m1-00{i}") for i in range(4)})
        self.assertEqual(r["jaccard"], 1.0)
        self.assertFalse(r["pass"])
        r = sc.item_6(trades[:2], rows, {("2026-09-19", "m1-000"), ("2026-09-19", "m1-002")})  # J = 1/3
        self.assertTrue(r["pass"])
        r = sc.item_6(trades[:2], rows, {("2026-09-19", "m1-000"), ("2026-09-19", "m1-001")} | {("2026-09-19", "m1-00%d" % i) for i in (2, 3)})
        self.assertAlmostEqual(r["jaccard"], 0.5)
        self.assertTrue(r["pass"])  # exactly 0.5 passes

    def test_b_from_full_table_post_exclusion_is_report_only(self) -> None:
        full = self.rows()
        eligible = [r for r in full if r["mint"] != "m1-001"]  # m1-001 is excluded by trigger time
        trades = [trade("2026-09-19", 0.1, mint="m1-000"), trade("2026-09-19", 0.1, mint="m1-002")]
        sel = {("2026-09-19", "m1-001"), ("2026-09-19", "m1-002"), ("2026-09-19", "m1-003")}
        r = sc.item_6(trades, full, sel, eligible_rows=eligible)
        self.assertEqual(r["n_b"], 3)  # the gate's B counts the excluded mint
        self.assertAlmostEqual(r["jaccard"], 1 / 4)
        self.assertAlmostEqual(r["jaccard_post_exclusion_b_report_only"], 1 / 3)  # B shrinks to 2 mints

    def test_threshold_and_count_asserted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1] + POOL_DAYS[:1], per_day=3))
            with self.assertRaises(SystemExit):
                sc.exp012_selected(fx.e12)  # default expectation: 0.8030766588450794 and 881
            n = len(sc.exp012_selected(fx.e12, None)[1])
            sc.exp012_selected(fx.e12, (0.5, n))
            with self.assertRaises(SystemExit):
                sc.exp012_selected(fx.e12, (0.5, n + 1))
            with self.assertRaises(SystemExit):
                sc.exp012_selected(fx.e12, (0.6, n))
        self.assertEqual((sc.E12_THRESHOLD, sc.E12_N_SELECTED), (0.8030766588450794, 881))

    def test_empty_union_fails(self) -> None:
        self.assertFalse(sc.item_6([], self.rows(), set())["pass"])

    def test_daily_corr_report_only(self) -> None:
        days = ["2026-09-19", "2026-09-20", "2026-09-21"]
        trades = [trade(d, 0.1 * (i + 1), mint=f"m{i + 1}-000") for i, d in enumerate(days)]
        daily = {leg: {d: float(i + 1) for i, d in enumerate(days)} for leg in sc.LEGS}
        r = sc.item_6(trades, self.rows(), set(), daily, e12_days=days)
        self.assertAlmostEqual(r["daily_pnl_corr_report_only"]["flat"], 1.0)

    def test_pearson(self) -> None:
        self.assertAlmostEqual(sc.pearson([1, 2, 3], [2, 4, 6]), 1.0)
        self.assertIsNone(sc.pearson([1, 2], [1, 2]))
        self.assertIsNone(sc.pearson([1, 1, 1], [1, 2, 3]))

    def test_exp012_daily_pnl_from_per_day(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            Fixture(td, rows=synth_rows(AUG_DAYS[:1], per_day=2))
            d = sc.exp012_daily_pnl(Path(td) / "exp012")
            self.assertAlmostEqual(d["flat"]["2026-09-19"], 10 * -3.0 / 100 * 0.5)
            self.assertAlmostEqual(d["press"]["2026-09-20"], 11 * -3.0 / 100 * 0.5)

    def test_exp012_read_refused(self) -> None:
        for fn in (sc.exp012_selected, sc.exp012_daily_pnl, sc.assert_exp012_dir):
            with self.assertRaises(SystemExit):
                fn(READ_DIR)
        with self.assertRaises(SystemExit):
            sc.assert_exp012_dir("/x/ARTIFACTS/exp012/read/sub")


class VerdictTests(unittest.TestCase):
    def test_and_across_items(self) -> None:
        ok = [{"item": k, "pass": True} for k in sc.ITEM_ORDER]
        self.assertEqual(sc.verdict_of(ok), "PASS")
        for i in range(len(ok)):
            bad = [dict(x) for x in ok]
            bad[i]["pass"] = False
            self.assertEqual(sc.verdict_of(bad), "FAIL", sc.ITEM_ORDER[i])
        self.assertEqual(sc.verdict_of(ok[:-1]), "FAIL")


class GuardTests(unittest.TestCase):
    def small(self, td: str) -> Fixture:
        return Fixture(td, rows=synth_rows(AUG_DAYS[:1] + POOL_DAYS[:1], per_day=3))

    def test_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit):
                sc.run("/data/mal/exp013-grad/r1", "m.json", Path(td) / "o", now=datetime(2026, 10, 3, tzinfo=UTC), ledger_dir=td, tries_log=Path(td) / "t")
            self.assertFalse((Path(td) / "SCREEN_RUNS.jsonl").exists())

    def test_manifest_sha_mismatch_and_unlisted_view(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = self.small(td)
            tm = json.loads((fx.table / "manifest.json").read_text())
            sc.assert_view_manifest(fx.vm_doc(), tm)
            bad = fx.vm_doc()
            bad["pools"]["A"]["view_sha256_file_sha256"] = "0" * 64
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(bad, tm)
            bad = fx.vm_doc()
            bad["extra_views"][0]["view_sha256_file_sha256"] = "0" * 64
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(bad, tm)
            bad = fx.vm_doc()
            bad["extra_views"] = []
            with self.assertRaises(SystemExit) as cm:
                sc.assert_view_manifest(bad, tm)
            self.assertIn("not listed", str(cm.exception))
            bad = fx.vm_doc()
            bad["extra_views"].append({"root": "/nowhere", "hours": ["2026-08-20T00", "2026-08-20T23"], "view_sha256_file_sha256": "1" * 64, "view_sha256_mtime_utc": "2026-10-03T00:00:00Z"})
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(bad, tm)
            del bad["pools"]["B"]
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(bad, tm)

    def test_recorded_mtime_after_cutoff_or_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = self.small(td)
            tm = json.loads((fx.table / "manifest.json").read_text())
            doc = fx.vm_doc()
            doc["extra_views"][0]["view_sha256_mtime_utc"] = "2026-10-04T12:00:00Z"  # equal to the cutoff is allowed
            sc.assert_view_manifest(doc, tm)
            doc["extra_views"][0]["view_sha256_mtime_utc"] = "2026-10-04T12:00:01Z"
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(doc, tm)
            sc.assert_view_manifest(doc, tm, cutoff=datetime(2026, 10, 5, tzinfo=UTC))  # injectable
            del doc["extra_views"][0]["view_sha256_mtime_utc"]
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(doc, tm)

    def test_file_mtime_before_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = self.small(td)
            tm = json.loads((fx.table / "manifest.json").read_text())
            early = lambda p: datetime(2026, 10, 3, tzinfo=UTC).timestamp()  # noqa: E731
            late = lambda p: datetime(2026, 10, 4, 12, 0, 1, tzinfo=UTC).timestamp()  # noqa: E731
            sc.assert_view_manifest(fx.vm_doc(), tm, check_files=True, mtime_fn=early)
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(fx.vm_doc(), tm, check_files=True, mtime_fn=late)
            (Path(fx.extra[0]["root"]) / "VIEW.sha256").write_text("tampered\n")
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(fx.vm_doc(), tm, check_files=True, mtime_fn=early)

    def test_out_dir_guards(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            Path(td, "exists").mkdir()
            with self.assertRaises(SystemExit):
                sc.assert_out_dir_fresh(Path(td) / "exists")
            with self.assertRaises(SystemExit):
                sc.assert_out_dir_fresh(Path(td) / "ARTIFACTS" / "o")
            sc.assert_out_dir_fresh(Path(td) / "new")

    def test_ledger_runs_once(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sc.ledger_start(td, {"a": 1})
            with self.assertRaises(SystemExit):
                sc.ledger_start(td, {"a": 2})
            sc.ledger_finish(td, {"verdict": "FAIL"})
            lines = [json.loads(x) for x in Path(td, "SCREEN_RUNS.jsonl").read_text().splitlines()]
            self.assertEqual([x["event"] for x in lines], ["started", "finished"])
            with self.assertRaises(SystemExit):
                sc.ledger_start(td, {})
        with tempfile.TemporaryDirectory() as td:
            Path(td, "SCREEN_RUNS.jsonl").write_text(json.dumps({"tool": "other"}) + "\n")
            sc.ledger_start(td, {})

    def test_any_prior_tries_entry_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "t.jsonl"
            log.write_text(json.dumps({"tool": "other"}) + "\n")
            sc.assert_no_prior_try(log)
            sc.assert_no_prior_try(Path(td) / "absent.jsonl")
            log.write_text(json.dumps({"tool": "exp013_grad", "data_key": "whatever"}) + "\n")
            with self.assertRaises(SystemExit):
                sc.assert_no_prior_try(log)
            log.write_text('{"tool": "exp013_grad", "data_ke\n')  # a damaged line that names the tool is a prior try
            with self.assertRaises(SystemExit):
                sc.assert_no_prior_try(log)
            log.write_text('not json at all\n\n{"tool": "other"}\n')
            sc.assert_no_prior_try(log)

    def test_guards_before_any_fit_ledger_or_try(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = self.small(td)
            with mock.patch.object(gm, "nested_lodo_select") as nl:
                fx.tries.write_text(json.dumps({"tool": "exp013_grad", "data_key": "other-manifest"}) + "\n")
                with self.assertRaises(SystemExit):
                    fx.run()
                fx.tries.write_text("")
                bad = fx.vm_doc()
                bad["extra_views"] = []
                fx.write_view_manifest(bad)
                with self.assertRaises(SystemExit):
                    fx.run()
                fx.write_view_manifest()
                with self.assertRaises(SystemExit):
                    fx.run(exp012_dir=READ_DIR)
                fx.out.mkdir()
                with self.assertRaises(SystemExit):
                    fx.run()
            nl.assert_not_called()
            self.assertFalse((fx.ledger / "SCREEN_RUNS.jsonl").exists())
            self.assertEqual(fx.tries.read_text(), "")

    def test_kill_between_the_two_start_lines_leaves_no_rerun_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = self.small(td)
            with mock.patch.object(sc, "ledger_start", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                fx.run()
            self.assertEqual(sc.count_tries(fx.tries), 1)  # the tries line was written first
            self.assertFalse((fx.ledger / "SCREEN_RUNS.jsonl").exists())
            with self.assertRaises(SystemExit):
                fx.run(out_dir=fx.root / "out2")

    def test_real_run_refuses_non_default_ledger_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit) as cm:
                sc.run("/data/mal/exp013-grad/r1", "m.json", Path(td) / "o", now=datetime(2026, 10, 5, tzinfo=UTC), ledger_dir=Path(td) / "led", tries_log=Path(td) / "t")
            self.assertIn("default ledger dir", str(cm.exception))

    def test_pool_views_get_the_extra_view_checks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = self.small(td)
            tm = json.loads((fx.table / "manifest.json").read_text())
            doc = fx.vm_doc()
            doc["pools"]["B"]["view_sha256_mtime_utc"] = "2026-10-04T12:00:01Z"
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(doc, tm)
            doc = fx.vm_doc()
            del doc["pools"]["A"]["view_sha256_mtime_utc"]
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(doc, tm)
            late = lambda p: datetime(2026, 10, 4, 12, 0, 1, tzinfo=UTC).timestamp()  # noqa: E731
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(fx.vm_doc(), tm, check_files=True, mtime_fn=late)
            (Path(fx.roots["insample"]) / "VIEW.sha256").write_text("tampered\n")  # re-hash of a pool view
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(fx.vm_doc(), tm, check_files=True, mtime_fn=lambda p: 0.0)

    def test_crash_after_start_uses_the_try(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = self.small(td)
            with mock.patch.object(gm, "nested_lodo_select", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
                fx.run()
            tries = [json.loads(x) for x in fx.tries.read_text().splitlines()]
            self.assertEqual([t["config"]["event"] for t in tries], ["started"])
            self.assertEqual(set(tries[0]["config"]), {"k", "table_md5", "view_manifest_sha256", "code_commit", "event"})
            with self.assertRaises(SystemExit):  # the try is spent: a second run is refused, whatever the manifest
                fx.run(out_dir=fx.root / "out2", ledger_dir=fx.root / "ledger2")


class OrderAndReportTests(unittest.TestCase):
    def test_result_line_and_ledger_finish_before_screen_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1] + POOL_DAYS[:1], per_day=30))
            with mock.patch.object(sc, "to_markdown", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
                fx.run()
            tries = [json.loads(x) for x in fx.tries.read_text().splitlines()]
            self.assertEqual([t["config"]["event"] for t in tries], ["started", "result"])
            self.assertEqual([json.loads(x)["event"] for x in (fx.ledger / "SCREEN_RUNS.jsonl").read_text().splitlines()], ["started", "finished"])
            self.assertFalse((fx.out / "screen.md").exists())  # the lines were written before the report files

    def test_edge_days_from_pool_runs(self) -> None:
        runs = {"A": [["2026-09-19T01", "2026-09-21T23"]], "X": [["2026-08-10T00", "2026-08-11T23"], ["2026-08-13T00", "2026-08-13T23"]]}
        self.assertEqual(sc.edge_days(runs), {"2026-09-19", "2026-09-21", "2026-08-10", "2026-08-11", "2026-08-13"})

    def test_book_stats_once_per_fail_model_shared_across_items_1_to_3(self) -> None:
        man = {"pool_runs": POOL_RUNS}
        rows = synth_rows(AUG_DAYS + POOL_DAYS, per_day=3, ks=(1, 4, 8))
        sel = [{"day": r["day"], "mint": r["mint"], "score": 1.0, "threshold": 0.5} for r in rows if r["entry_land_k"] == 4]
        with mock.patch.object(sc, "book_stats", wraps=sc.book_stats) as bs:
            doc = sc.evaluate(rows, man, sel, [], {}, set())
        # full set (2) + 4a (2) + 4b (2) + the edge split's without-edge set (2); items 1-3, the gate counts and the with-edge side reuse the full set
        self.assertEqual(bs.call_count, 8)
        self.assertEqual(doc["items"][0]["legs"], doc["gate_report_only"]["legs"])

    def test_result_note_and_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1] + POOL_DAYS[:1], per_day=30), censored=[{"mint": "m0-001", "entry_land_k": 4, "reason": "x"}])
            doc = fx.run()
            res = json.loads((fx.out / "result.json").read_text())
            self.assertIn("NOT the screen verdict", res["gate_note"])
            self.assertIn("NOT the screen verdict", res["notes"])
            self.assertEqual(doc["tries_log"], os.path.realpath(str(fx.tries)))
            self.assertTrue(os.path.isabs(doc["tries_log"]))
            self.assertIn("Table-censored k=4 rows the trigger-time rule would have kept", (fx.out / "screen.md").read_text())

    def test_env_tries_log_resolved_absolute(self) -> None:
        with mock.patch.dict(os.environ, {"MAL_TRIES_LOG": "rel/tries.jsonl"}):
            self.assertEqual(sc.resolved_tries_log(None), os.path.realpath("rel/tries.jsonl"))
            self.assertTrue(os.path.isabs(sc.resolved_tries_log(None)))


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.td = tempfile.TemporaryDirectory()
        cls.fx = Fixture(cls.td.name, censored=[{"mint": "m0-001", "day": "2026-08-10", "entry_land_k": 8, "reason": "tape_end"}])
        cls.sink = Path(cls.td.name) / "sink.json"
        with mock.patch.dict(os.environ, {"MISCUSI_RESULT": str(cls.sink)}):
            cls.doc = cls.fx.run()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.td.cleanup()

    def test_outputs(self) -> None:
        for name in ("screen.json", "screen.md", "result.json"):
            self.assertTrue((self.fx.out / name).is_file(), name)
        res = json.loads((self.fx.out / "result.json").read_text())
        self.assertEqual(res["schema_version"], "result.v1")
        self.assertEqual(res["verdict"], self.doc["verdict"])
        self.assertIn(res["verdict"], ("PASS", "FAIL"))
        sink = json.loads(self.sink.read_text())
        self.assertTrue(sink["ok"])
        self.assertEqual(sink["verdict"], self.doc["verdict"])

    def test_items_and_extras(self) -> None:
        self.assertEqual([i["item"] for i in self.doc["items"]], list(sc.ITEM_ORDER))
        self.assertEqual(self.doc["verdict"], sc.verdict_of(self.doc["items"]))
        for key in ("gate_report_only", "edge_split_report_only", "per_day", "fold_info", "fold_fraction_by_source", "counts", "slot1_reference", "freeze_reference",
                    "trigger_time_exclusion", "table_censored"):
            self.assertIn(key, self.doc)
        self.assertEqual(len(self.doc["per_day"]), 12)
        self.assertEqual(self.doc["freeze_reference"]["label"], "freeze reference, not screen")
        self.assertEqual(self.doc["day_groups"]["4b"], AUG_DAYS)
        self.assertEqual(self.doc["table_censored"]["n_censored"], 1)
        self.assertEqual({f["source"] for f in self.doc["fold_fraction_by_source"]}, {"X", "A", "C", "B"})
        self.assertEqual(self.doc["trigger_time_exclusion"]["n_excluded_by_trigger_time"], 0)

    def test_ledger_and_tries(self) -> None:
        lines = [json.loads(x) for x in (self.fx.ledger / "SCREEN_RUNS.jsonl").read_text().splitlines()]
        self.assertEqual([x["event"] for x in lines], ["started", "finished"])
        self.assertEqual(lines[1]["verdict"], self.doc["verdict"])
        tries = [json.loads(x) for x in self.fx.tries.read_text().splitlines()]
        self.assertEqual([t["config"]["event"] for t in tries], ["started", "result"])

    def test_second_run_refused(self) -> None:
        with self.assertRaises(SystemExit):
            self.fx.run(out_dir=self.fx.root / "out2", ledger_dir=self.fx.root / "ledger2")
        self.assertFalse((self.fx.root / "out2").exists())

    def test_cli_guards_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1] + POOL_DAYS[:1], per_day=3))
            with self.assertRaises(SystemExit):
                sc.main(["--table-run-dir", str(fx.table), "--view-manifest", str(fx.view_manifest), "--out-dir", str(fx.out)])  # default exp012 / expectation refuse the fixture
            for flag in ("--tries-log", "--ledger-dir", "--exp012-dir"):
                with self.assertRaises(SystemExit) as cm:  # the flags are gone: argparse refuses them
                    sc.main(["--table-run-dir", "d", "--view-manifest", "m", "--out-dir", "o", flag, "x"])
                self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
