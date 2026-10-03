"""Tests for tools/exp014_m15_model.py and tools/exp014_m15_screen.py. Synthetic fixtures only; nothing reads real data."""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import tools.exp014_m15_model as mm
import tools.exp014_m15_screen as sc
from tools.exp013_fixtures import write_view_sha256

UTC = timezone.utc
LAM = 1_000_000_000
A_DAYS = ["2026-09-19", "2026-09-20", "2026-09-21"]
C_DAYS = ["2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
AUG_DAYS = [f"2026-08-{d}" for d in range(10, 16)]  # six August days
E12 = [f"2026-09-{d}" for d in range(19, 28)]
READ_DIR = Path(__file__).resolve().parent.parent / "ARTIFACTS" / "exp012" / "read"


def pool_runs(aug_last: str = "2026-08-15T23") -> dict:
    return {"A": [["2026-09-19T01", "2026-09-21T23"]], "C": [["2026-09-22T00", "2026-09-25T06"]], "X": [["2026-08-10T00", aug_last]]}


def day_ms(day: str, plus: int = 0) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000) + plus


def pool_of(day: str) -> str:
    return "X" if day.startswith("2026-08") else ("A" if day <= "2026-09-21" else "C")


def synth_rows(days, per_day=30, seed=5, ds=(1, 4, 8), excluded=()) -> list[dict]:
    """Rows in the exp014_m15 table schema. mint "m{di}-{i}"; every 7th mint migrated the previous UTC day."""
    rng = random.Random(seed)
    rows = []
    for di, day in enumerate(days):
        prev = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        for i in range(per_day):
            mint = f"m{di}-{i:03d}"
            feats = {n: rng.random() for n in mm.FEATURE_NAMES}
            signal = feats[mm.FEATURE_NAMES[0]] + 0.5 * rng.random()
            for d in ds:
                press = int(round((signal - 0.9) * 40_000_000 + rng.gauss(0, 5_000_000))) if i % 5 else -1_000_000
                rows.append(
                    {"mint": mint, "day": day, "mig_day": prev if i % 7 == 0 else day, "entry_land_k": d, "trigger_ms": day_ms(day, 60_000 + i * 1000), "mig_ms": day_ms(day, i * 1000),
                     "filled": bool(i % 5), "flat": press - 1000, "press": press, "label": 1 if press > 0 else 0, "excluded_by_time": mint in excluded, "pool": pool_of(day),
                     "edge_left": day == days[0], "edge_right": day == days[-1], "features": dict(feats)}
                )
    return rows


def trade(day: str, flat: float, press: float | None = None, mint: str | None = None, i: int = 0, pool: str = "X", mig_day: str | None = None) -> dict:
    return {"mint": mint or f"{day}-{i}", "day": day, "mig_day": mig_day or day, "trigger_ms": day_ms(day, 1000 * (i + 1)), "flat": int(flat * LAM),
            "press": int((flat if press is None else press) * LAM), "filled": True, "edge": False, "pool": pool}


def winners(days, per_day=40, mean=0.01, pool="X") -> list[dict]:
    rng = random.Random(1)
    return [trade(d, mean + rng.uniform(0.0, 0.01), mint=f"{d}-w{i}", i=i, pool=pool) for d in days for i in range(per_day)]


def manifest_for(runs: dict | None = None) -> dict:
    return {"pool_runs": runs or pool_runs(), "excluded_pools": ["B"]}


class Fixture:
    def __init__(self, td: str, rows=None, days=None, runs=None) -> None:
        self.root = Path(td)
        self.runs = runs or pool_runs()
        self.rows = rows if rows is not None else synth_rows(days or (AUG_DAYS + A_DAYS + C_DAYS))
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
        self.extra = [{"root": str(xr), "hours": list(self.runs["X"][0]), "n_hours": 144, "days": AUG_DAYS,
                       "view_sha256_file_sha256": hashlib.sha256(write_view_sha256(xr).read_bytes()).hexdigest()}]
        d = self.root / "table"
        d.mkdir()
        data = "".join(json.dumps(r) + "\n" for r in self.rows).encode()
        (d / "table.jsonl").write_bytes(data)
        md5 = hashlib.md5(data).hexdigest()
        (d / "table.md5").write_text(md5 + "\n")
        (d / "manifest.json").write_text(json.dumps({"schema": mm.TABLE_SCHEMA, "n_rows": len(self.rows), "table_md5": md5, "pool_runs": self.runs, "roots": self.roots,
                                                      "view_sha256_file_sha256": self.shas, "extra_views": self.extra, "excluded_pools": ["B"]}))
        self.table = d
        self.view_manifest = self.root / "views.json"
        self.view_manifest.write_text(json.dumps(self.vm_doc()))
        self.e12 = self.root / "exp012"
        self.e12.mkdir()
        self.write_e12()
        self.ledger, self.tries, self.out = self.root / "ledger", self.root / "tries.jsonl", self.root / "out"

    def vm_doc(self) -> dict:
        mt = "2026-10-03T00:00:00Z"
        pools = {
            "A": {"root": self.roots["fast"], "hours": list(self.runs["A"][0]), "view_sha256_file_sha256": self.shas["fast"], "view_sha256_mtime_utc": mt},
            "C": {"root": self.roots["insample"], "hours": list(self.runs["C"][0]), "view_sha256_file_sha256": self.shas["insample"], "view_sha256_mtime_utc": mt},
            "B": {"root": self.roots["live"], "view_sha256_file_sha256": self.shas["live"], "view_sha256_mtime_utc": mt, "excluded": True},
        }
        return {"pools": pools, "extra_views": [dict(v, view_sha256_mtime_utc=mt) for v in self.extra]}

    def write_e12(self, threshold=0.5) -> None:
        rng = random.Random(3)
        k4 = [r for r in self.rows if r["entry_land_k"] == 4 and r["day"] in E12]
        oof = [{"day": r["day"], "mint": r["mint"], "score": rng.random(), "label": 0, "filled": True} for r in k4]
        (self.e12 / "oof_scores.json").write_text(json.dumps({"days": E12, "rows": oof}))
        (self.e12 / "threshold.json").write_text(json.dumps({"threshold": threshold}))
        per_day = [{"day": d, "n_entered": 10 + i, "flat_mean_pct": float(i - 3), "press_mean_pct": float(2 * i - 5)} for i, d in enumerate(E12)]
        (self.e12 / "nested_fixed_threshold_lodo.json").write_text(json.dumps({"per_day": per_day}))

    def run(self, **kw):
        kw.setdefault("ledger_dir", self.ledger)
        kw.setdefault("tries_log", self.tries)
        kw.setdefault("exp012_dir", self.e12)
        kw.setdefault("e12_expect", None)
        return sc.run(self.table, self.view_manifest, kw.pop("out_dir", self.out), **kw)


# --- model --------------------------------------------------------------------------


class ModelTests(unittest.TestCase):
    def test_27_features_and_label(self) -> None:
        self.assertEqual(len(mm.FEATURE_NAMES), 27)
        self.assertEqual(mm.label({"press": 1}), 1)
        self.assertEqual(mm.label({"press": 0}), 0)
        self.assertEqual(mm.label({"press": -5}), 0)
        with self.assertRaises(ValueError):
            mm.vector({n: 0.0 for n in mm.FEATURE_NAMES[:-1]})

    def test_excluded_rows_are_removed_and_counted(self) -> None:
        rows = synth_rows(AUG_DAYS[:2], per_day=10, excluded={"m0-001", "m1-002"})
        keep, info = mm.eligible_rows(rows)
        self.assertEqual(info["n_excluded_by_time"], 6)  # two mints x three d
        self.assertEqual(info["excluded_by_d"], {"1": 2, "4": 2, "8": 2})
        self.assertFalse(any(r["excluded_by_time"] for r in keep))
        self.assertEqual(len(keep), len(rows) - 6)

    def test_an_excluded_row_reaching_the_model_is_an_error(self) -> None:
        rows = synth_rows(AUG_DAYS[:1], per_day=5, excluded={"m0-001"})
        with self.assertRaises(ValueError):
            mm.training_rows(rows)

    def test_training_rows_are_d4_and_keep_miss_rows(self) -> None:
        tr = mm.training_rows(synth_rows(AUG_DAYS[:1], per_day=10))
        self.assertEqual({r["entry_land_k"] for r in tr}, {4})
        self.assertTrue(any(not r["filled"] for r in tr))

    def test_nested_lodo_never_selects_an_excluded_mint_and_is_deterministic(self) -> None:
        excl = {f"m{di}-{i:03d}" for di in range(4) for i in range(0, 30, 6)}
        rows = synth_rows(AUG_DAYS[:4], per_day=30, excluded=excl)
        keep, _ = mm.eligible_rows(rows)
        sel, info, counts = mm.nested_lodo_select(keep, AUG_DAYS[:4])
        self.assertTrue(sel)
        self.assertFalse({s["mint"] for s in sel} & excl)
        self.assertEqual(counts["n_k4_rows"], 4 * 30 - len(excl))
        self.assertEqual(len(info), 4)
        sel2, _, _ = mm.nested_lodo_select(keep, AUG_DAYS[:4], n_jobs=2)
        self.assertEqual(sel, sel2)

    def test_day_is_the_day_of_T_not_the_migration_day(self) -> None:
        rows = synth_rows(AUG_DAYS[:4], per_day=30)
        sel, _, _ = mm.nested_lodo_select(rows, AUG_DAYS[:4])
        by_mint = {r["mint"]: r for r in rows if r["entry_land_k"] == 4}
        self.assertTrue(sel)
        for s in sel:
            self.assertEqual(s["day"], by_mint[s["mint"]]["day"])
        self.assertTrue(any(r["mig_day"] != r["day"] for r in rows))


# --- days, items ----------------------------------------------------------------------


class ItemTests(unittest.TestCase):
    TM = manifest_for()

    def test_1x_day_set_drops_pool_a_and_0925_in_pool_c(self) -> None:
        days = sc.days_1x(self.TM)
        self.assertEqual(days, set(AUG_DAYS) | {"2026-09-22", "2026-09-23", "2026-09-24"})
        self.assertFalse(set(A_DAYS) & days)
        self.assertNotIn("2026-09-25", days)

    def test_1x_pass_and_fail(self) -> None:
        good = winners(AUG_DAYS + ["2026-09-22", "2026-09-23", "2026-09-24"], pool="X")
        self.assertTrue(sc.item_1x(good, self.TM)["pass"])
        # only the dropped days win: 1x sees nothing and fails, though bars 1-3 on all days pass.
        only_dropped = winners(A_DAYS + ["2026-09-25"], pool="A")
        self.assertFalse(sc.item_1x(only_dropped, self.TM)["pass"])
        self.assertTrue(sc.sc.bars(only_dropped, sc.sc.all_manifest_days(self.TM))["legs"]["flat"]["bar1_mean_gt_0_and_ci_lo_gt_0"])
        # winners everywhere, but 1x's N is only its own days: losers on them fail the majority bar.
        mixed = winners(A_DAYS + ["2026-09-25"], pool="A") + [trade(d, -0.02, mint=f"{d}-l{i}", i=i) for d in AUG_DAYS for i in range(40)]
        self.assertFalse(sc.item_1x(mixed, self.TM)["pass"])

    def test_1x_n_counts_every_1x_day_with_no_trade_not_positive(self) -> None:
        days = AUG_DAYS + ["2026-09-22", "2026-09-23", "2026-09-24"]
        only_four = winners(days[:4])  # 4 of 9 days positive
        self.assertFalse(sc.item_1x(only_four, self.TM)["legs"]["flat"]["pass"])
        five = winners(days[:5])  # 5 of 9
        self.assertTrue(sc.item_1x(five, self.TM)["legs"]["flat"]["bar3_majority_of_manifest_days_positive"])

    def test_items_1_to_3_fail_on_losers_and_pass_on_winners(self) -> None:
        alldays = sc.sc.all_manifest_days(self.TM)
        win = winners(sorted(alldays), per_day=40)
        for fn in (sc.sc.item_1, sc.sc.item_2, sc.sc.item_3):
            self.assertTrue(fn(win, alldays)["pass"])
        lose = [dict(t, flat=-abs(t["flat"]), press=-abs(t["press"])) for t in win]
        for fn in (sc.sc.item_1, sc.sc.item_2, sc.sc.item_3):
            self.assertFalse(fn(lose, alldays)["pass"])

    def test_4a_4b_by_pool_tag(self) -> None:
        groups, pd = sc.sc.day_groups(self.TM), sc.sc.pool_days(self.TM)
        win = winners(AUG_DAYS, pool="X") + winners(A_DAYS, pool="A")
        self.assertTrue(sc.sc.item_4a(win, groups, pd)["pass"])
        self.assertTrue(sc.sc.item_4b(win, groups, pd)["pass"])
        aug_lose = [trade(d, -0.02, mint=f"{d}-l{i}", i=i) for d in AUG_DAYS for i in range(40)] + winners(A_DAYS, pool="A")
        self.assertFalse(sc.sc.item_4b(aug_lose, groups, pd)["pass"])
        self.assertEqual(groups["4a"], set(AUG_DAYS) | set(A_DAYS))

    def test_4b_precondition_counts_august_days(self) -> None:
        self.assertEqual(len(sc.august_days(self.TM)), 6)
        self.assertEqual(len(sc.august_days(manifest_for(pool_runs("2026-08-14T23")))), 5)
        non_aug = {"A": [["2026-09-19T01", "2026-09-21T23"]], "X": [["2026-07-10T00", "2026-07-20T23"]]}
        self.assertEqual(sc.august_days(manifest_for(non_aug)), set())

    def test_6a_cases(self) -> None:
        b = {f"b{i}" for i in range(10)}
        low = [trade("2026-09-19", 0.01, mint=f"a{i}", i=i, pool="A") for i in range(30)] + [trade("2026-09-20", 0.01, mint="b0", i=40, pool="A")]
        self.assertTrue(sc.item_6a(low, b, b)["pass"])
        # J exactly 0.5: |A and B| = 5, |A or B| = 10 -> pass; one fewer union member (J = 5/9) -> fail
        a5 = [trade("2026-09-19", 0.01, mint=f"b{i}", i=i, pool="A") for i in range(5)]
        b5 = {f"b{i}" for i in range(5)} | {f"z{i}" for i in range(5)}
        r = sc.item_6a(a5, b5, b5)
        self.assertEqual(r["jaccard"], 0.5)
        self.assertTrue(r["pass"])
        self.assertFalse(sc.item_6a(a5, b5 - {"z4"}, b5)["pass"])
        # empty A and empty B: empty union fails
        self.assertFalse(sc.item_6a([], set(), set())["pass"])
        self.assertIsNone(sc.item_6a([], set(), set())["jaccard"])

    def test_6a_a_is_by_migration_day(self) -> None:
        t = trade("2026-09-19", 0.01, mint="x", mig_day="2026-09-18")  # T on a 6a day, migrated the day before
        self.assertEqual(sc.item_6a([t], {"x"}, {"x"})["n_a"], 0)
        t2 = trade("2026-09-28", 0.01, mint="y", mig_day="2026-09-27")
        self.assertEqual(sc.item_6a([t2], {"y"}, {"y"})["n_a"], 1)

    def test_6b_cases(self) -> None:
        b = {"in-b"}
        self.assertFalse(sc.item_6b([trade("2026-09-19", 0.05, mint="in-b", pool="A")], b)["pass"])  # none outside B
        self.assertFalse(sc.item_6b([], b)["pass"])
        out_win = [trade("2026-09-19", 0.05, mint="o1", pool="A"), trade("2026-09-20", -0.01, mint="o2", pool="A")]
        self.assertTrue(sc.item_6b(out_win, b)["pass"])
        one_leg = [trade("2026-09-19", 0.05, press=-0.05, mint="o1", pool="A")]
        r = sc.item_6b(one_leg, b)
        self.assertFalse(r["pass"])
        self.assertTrue(r["legs"]["flat"]["pass"])
        self.assertFalse(r["legs"]["press"]["pass"])

    def test_6c_reports_by_migration_day_and_undefined_on_zero_variance(self) -> None:
        daily = {leg: {d: float(i) for i, d in enumerate(E12)} for leg in sc.LEGS}
        trades = [trade(d, 0.1 * (i + 1), mint=f"t{i}", i=i, pool="A", mig_day=d) for i, d in enumerate(E12)]
        c = sc.item_6c(trades, daily)
        self.assertAlmostEqual(c["pearson"]["flat"], 1.0, places=9)
        self.assertAlmostEqual(c["spearman"]["flat"], 1.0, places=9)
        # grouping is by migration day: a trade with T on E12[0] but migration day E12[1] counts on E12[1]
        shifted = [trade(E12[0], 0.7, mint="s", mig_day=E12[1])]
        c2 = sc.item_6c(shifted, daily)
        self.assertIsNotNone(c2["pearson"]["flat"])
        # no trades at all: all-zero series, zero variance, undefined
        c3 = sc.item_6c([], daily)
        self.assertIsNone(c3["pearson"]["flat"])
        self.assertIsNone(c3["spearman"]["press"])
        self.assertEqual(sc.item_6c(trades, None)["pearson"], {})

    def test_spearman_ranks_with_ties(self) -> None:
        self.assertEqual(sc.ranks([3, 1, 1, 2]), [4.0, 1.5, 1.5, 3.0])
        self.assertAlmostEqual(sc.spearman([1, 2, 3, 4], [10, 20, 25, 100]), 1.0)
        self.assertAlmostEqual(sc.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)

    def test_verdict_needs_every_item(self) -> None:
        items = [{"item": k, "pass": True} for k in sc.ITEM_ORDER]
        self.assertEqual(sc.verdict_of(items), "PASS")
        for i in range(len(items)):
            bad = [dict(x) for x in items]
            bad[i]["pass"] = False
            self.assertEqual(sc.verdict_of(bad), "FAIL")
        self.assertEqual(sc.verdict_of(items[:-1]), "FAIL")


# --- guards ------------------------------------------------------------------------------


class GuardTests(unittest.TestCase):
    def test_real_data_cutoff_is_1005_12z(self) -> None:
        self.assertEqual(mm.REAL_DATA_CUTOFF, datetime(2026, 10, 5, 12, tzinfo=UTC))
        self.assertEqual(sc.CUTOFF, mm.REAL_DATA_CUTOFF)
        with self.assertRaises(SystemExit):
            mm.assert_run_dir_allowed("/data/mal/exp014-m15/x", now=mm.REAL_DATA_CUTOFF - timedelta(seconds=1))
        mm.assert_run_dir_allowed("/data/mal/exp014-m15/x", now=mm.REAL_DATA_CUTOFF)

    def test_view_mtime_after_cutoff_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1], per_day=2))
            tm = json.loads((fx.table / "manifest.json").read_text())
            doc = fx.vm_doc()
            doc["extra_views"][0]["view_sha256_mtime_utc"] = "2026-10-05T12:00:01Z"
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(doc, tm)
            doc["extra_views"][0]["view_sha256_mtime_utc"] = "2026-10-05T12:00:00Z"
            sc.assert_view_manifest(doc, tm)
            late = lambda p: (sc.CUTOFF + timedelta(seconds=1)).timestamp()  # noqa: E731
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(doc, tm, check_files=True, mtime_fn=late)
            ok = lambda p: (sc.CUTOFF - timedelta(days=1)).timestamp()  # noqa: E731
            sc.assert_view_manifest(doc, tm, check_files=True, mtime_fn=ok)

    def test_view_manifest_pool_b_and_membership(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1], per_day=2))
            tm = json.loads((fx.table / "manifest.json").read_text())
            doc = fx.vm_doc()
            sc.assert_view_manifest(doc, tm)
            bad = fx.vm_doc()
            bad["pools"]["B"].pop("excluded")
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(bad, tm)
            bad = fx.vm_doc()
            bad["pools"]["B"]["hours"] = ["2026-09-25T07", "2026-09-27T23"]
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(bad, tm)
            bad = fx.vm_doc()
            bad["pools"]["B"]["view_sha256_file_sha256"] = "0" * 64
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(bad, tm)
            bad = fx.vm_doc()
            bad["extra_views"] = []
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(bad, tm)
            bad = fx.vm_doc()
            bad["extra_views"].append(dict(bad["extra_views"][0], root="/elsewhere"))
            with self.assertRaises(SystemExit):
                sc.assert_view_manifest(bad, tm)
            bad = fx.vm_doc()
            bad["pools"]["A"]["view_sha256_mtime_utc"] = None
            with self.assertRaises(Exception):
                sc.assert_view_manifest(bad, tm)
            with self.assertRaises(SystemExit):  # a table manifest that still plans pool B
                sc.assert_view_manifest(doc, dict(tm, excluded_pools=[]))

    def test_out_dir_must_not_exist_and_not_under_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit):
                sc.sc.assert_out_dir_fresh(td)
            with self.assertRaises(SystemExit):
                sc.sc.assert_out_dir_fresh(Path(td) / "ARTIFACTS" / "o")
            sc.sc.assert_out_dir_fresh(Path(td) / "fresh")

    def test_exp012_read_dir_refused(self) -> None:
        with self.assertRaises(SystemExit):
            sc.sc.assert_exp012_dir(READ_DIR)

    def test_any_prior_tries_entry_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.jsonl"
            sc.assert_no_prior_try(p)
            p.write_text(json.dumps({"tool": "exp013_grad"}) + "\n")
            sc.assert_no_prior_try(p)  # another tool's entry does not count
            p.write_text(json.dumps({"tool": "exp014_m15", "config": {"event": "result"}}) + "\n")
            with self.assertRaises(SystemExit):
                sc.assert_no_prior_try(p)
            p.write_text("{damaged exp014_m15\n")
            with self.assertRaises(SystemExit):
                sc.assert_no_prior_try(p)

    def test_ledger_runs_once(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sc.assert_ledger_empty(td)
            sc.ledger_start(td, {"x": 1})
            with self.assertRaises(SystemExit):
                sc.ledger_start(td, {"x": 2})
            with self.assertRaises(SystemExit):
                sc.assert_ledger_empty(td)
            self.assertEqual(sc.DEFAULT_LEDGER_DIR, Path("/data/mal/exp014-m15"))
            self.assertEqual(sc.ledger_path("/q").name, "SCREEN_RUNS.jsonl")

    def test_damaged_ledger_counts_as_prior(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sc.ledger_path(td).write_text("{oops\n")
            with self.assertRaises(SystemExit):
                sc.ledger_start(td, {})

    def test_real_run_requires_default_ledger_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1], per_day=2))
            fake_real = Path(td) / "real"
            with mock.patch.object(mm, "assert_run_dir_allowed", return_value=Path("/data/mal/exp014-m15/x")), \
                    mock.patch.object(mm, "load_table", side_effect=AssertionError("must refuse first")):
                with self.assertRaises(SystemExit) as cm:
                    sc.run(fake_real, fx.view_manifest, fx.out, ledger_dir=fx.ledger, tries_log=fx.tries, exp012_dir=fx.e12)
                self.assertIn("default ledger dir", str(cm.exception))

    def test_cli_has_no_path_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1], per_day=3))
            with mock.patch.object(sc, "run", return_value={"verdict": "FAIL", "items": [], "n_selected": 0}) as run:
                sc.main(["--table-run-dir", str(fx.table), "--view-manifest", str(fx.view_manifest), "--out-dir", str(fx.out)])
            self.assertEqual(set(run.call_args.kwargs), {"n_jobs", "command"})
            for flag in ("--tries-log", "--ledger-dir", "--exp012-dir", "--cutoff"):
                with self.assertRaises(SystemExit) as cm:
                    sc.main(["--table-run-dir", "d", "--view-manifest", "m", "--out-dir", "o", flag, "x"])
                self.assertEqual(cm.exception.code, 2)

    def test_guards_fire_before_any_fit_ledger_or_try(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1], per_day=2))
            fx.out.mkdir()
            with mock.patch.object(mm, "fit", side_effect=AssertionError("fit")):
                with self.assertRaises(SystemExit):
                    fx.run()  # out-dir exists
            self.assertFalse(fx.tries.exists())
            self.assertFalse(fx.ledger.exists())

    def test_prior_try_refuses_before_a_new_line(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1], per_day=2))
            fx.tries.write_text(json.dumps({"tool": "exp014_m15"}) + "\n")
            with self.assertRaises(SystemExit):
                fx.run()
            self.assertEqual(len(fx.tries.read_text().splitlines()), 1)
            self.assertFalse(fx.ledger.exists())

    def test_crash_after_start_uses_the_try_and_the_started_line_precedes_the_fit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td)
            seen = {}

            def boom(*a, **k):
                seen["tries"] = fx.tries.read_text()
                seen["ledger"] = sc.ledger_path(fx.ledger).read_text()
                raise RuntimeError("crash")

            with mock.patch.object(mm, "nested_lodo_select", side_effect=boom):
                with self.assertRaises(RuntimeError):
                    fx.run()
            self.assertIn('"event": "started"', seen["tries"])
            self.assertIn("started", seen["ledger"])
            self.assertTrue(fx.out.is_dir())  # created before `started`
            self.assertEqual([json.loads(x)["event"] for x in sc.ledger_path(fx.ledger).read_text().splitlines()], ["started", "crashed"])
            self.assertIn("crash", json.loads(sc.ledger_path(fx.ledger).read_text().splitlines()[-1])["error"])
            with self.assertRaises(SystemExit):  # the try is spent
                fx.run(out_dir=fx.root / "out2", ledger_dir=fx.root / "ledger2")

    def test_table_md5_and_schema_checked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1], per_day=2))
            mm.load_table(fx.table)
            (fx.table / "table.md5").write_text("0" * 32 + "\n")
            with self.assertRaises(SystemExit):
                mm.load_table(fx.table)
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:1], per_day=2))
            m = json.loads((fx.table / "manifest.json").read_text())
            m["schema"] = "exp013_grad_table_v1"
            (fx.table / "manifest.json").write_text(json.dumps(m))
            with self.assertRaises(SystemExit):
                mm.load_table(fx.table)


# --- end to end -----------------------------------------------------------------------------


class NotDecidableTests(unittest.TestCase):
    def test_fewer_than_six_august_days_exits_without_fitting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runs = pool_runs("2026-08-14T23")
            fx = Fixture(td, rows=synth_rows(AUG_DAYS[:5] + A_DAYS + C_DAYS), runs=runs)
            with mock.patch.object(mm, "fit", side_effect=AssertionError("fit")), mock.patch.object(mm, "nested_lodo_select", side_effect=AssertionError("select")):
                doc = fx.run()
            self.assertEqual(doc["verdict"], "NOT_DECIDABLE")
            self.assertEqual(doc["n_august_days"], 5)
            self.assertEqual(json.loads((fx.out / "screen.json").read_text())["verdict"], "NOT_DECIDABLE")
            self.assertIn("NOT_DECIDABLE", (fx.out / "screen.md").read_text())
            self.assertEqual([json.loads(x)["event"] for x in sc.ledger_path(fx.ledger).read_text().splitlines()], ["started", "finished"])
            self.assertEqual([json.loads(x)["config"]["event"] for x in fx.tries.read_text().splitlines()], ["started", "result"])
            with self.assertRaises(SystemExit):  # the try is spent
                fx.run(out_dir=fx.root / "out2", ledger_dir=fx.root / "ledger2")


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.td = tempfile.TemporaryDirectory()
        excl = {f"m{di}-{i:03d}" for di in range(len(AUG_DAYS + A_DAYS + C_DAYS)) for i in (28, 29)}
        cls.excl = excl
        cls.fx = Fixture(cls.td.name, rows=synth_rows(AUG_DAYS + A_DAYS + C_DAYS, excluded=excl))
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
        self.assertEqual(res["tool"], "exp014_m15")
        sink = json.loads(self.sink.read_text())
        self.assertTrue(sink["ok"])

    def test_items_and_extras(self) -> None:
        self.assertEqual([i["item"] for i in self.doc["items"]], list(sc.ITEM_ORDER))
        self.assertEqual(self.doc["verdict"], sc.verdict_of(self.doc["items"]))
        self.assertIn(self.doc["verdict"], ("PASS", "FAIL"))
        for key in ("item_6c_report_only", "gate_report_only", "edge_split_report_only", "per_day", "fold_info", "counts", "slot1_reference", "excluded_by_time", "day_groups"):
            self.assertIn(key, self.doc)
        self.assertEqual(self.doc["day_groups"]["4b"], AUG_DAYS)
        self.assertEqual(self.doc["day_groups"]["1x"], sorted(set(AUG_DAYS) | {"2026-09-22", "2026-09-23", "2026-09-24"}))
        self.assertEqual(len(self.doc["per_day"]), 13)
        self.assertEqual(set(self.doc["item_6c_report_only"]["pearson"]), {"flat", "press"})

    def test_excluded_rows_never_trained_or_selected(self) -> None:
        self.assertEqual(self.doc["excluded_by_time"]["n_excluded_by_time"], len(self.excl) * 3)
        self.assertEqual(self.doc["counts"]["n_k4_rows"], 13 * 30 - len(self.excl))
        # the per-day table counts eligible rows only
        self.assertEqual(sum(d["n_rows"] for d in self.doc["per_day"]), 13 * 30 - len(self.excl))

    def test_ledger_and_tries(self) -> None:
        lines = [json.loads(x) for x in sc.ledger_path(self.fx.ledger).read_text().splitlines()]
        self.assertEqual([x["event"] for x in lines], ["started", "finished"])
        self.assertEqual(lines[1]["verdict"], self.doc["verdict"])
        tries = [json.loads(x) for x in self.fx.tries.read_text().splitlines()]
        self.assertEqual([t["tool"] for t in tries], ["exp014_m15", "exp014_m15"])
        self.assertEqual([t["config"]["event"] for t in tries], ["started", "result"])

    def test_second_run_refused(self) -> None:
        with self.assertRaises(SystemExit):
            self.fx.run(out_dir=self.fx.root / "out2", ledger_dir=self.fx.root / "ledger2")
        self.assertFalse((self.fx.root / "out2").exists())


if __name__ == "__main__":
    unittest.main()
