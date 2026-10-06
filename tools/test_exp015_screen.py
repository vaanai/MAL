"""Tests for tools/exp015_screen.py. Fixtures only; nothing here opens a real data path."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import signal
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp012_backcheck as bc
import tools.exp012_operating_point as op
import tools.exp015_screen as x
import tools.exploration_entry_model as eem
from tools.exp012_fixtures import write_fast_format_root
from tools.latency_curve import FLAT_FAIL, MISS, mixed_net

SOL = 1_000_000_000
NF = len(fz.FROZEN_FEATURE_NAMES)


# --- fixtures -----------------------------------------------------------------------------------------------------


def mk_cell(net0, filled=True, status=1, p=0.1, size=50_000_000, k=6, lag=2, sides=2, censored=False):
    if censored:
        return {"k": k, "lag": lag, "size": size, "censored": True}
    return {"k": k, "lag": lag, "size": size, "censored": False, "filled": filled, "status": status, "net0": net0, "sides": sides, "p_press": p}


def block_of(date):
    if date in x.block_dates("P1"):
        return "P1"
    if date in x.p2_dates():
        return "P2"
    return "P3" if date <= "2026-09-08" else "P4"


def source_of(date):
    b = block_of(date)
    if b != "P1":
        return b
    return "P1A" if date <= "2026-09-21" else ("P1C" if date <= "2026-09-24" else "P1B")


def mk_urow(i, date, good, rng, net_good=20_000_000, net_bad=-20_000_000, informative=True):
    feats = [rng.random() for _ in range(NF)]
    if informative:
        feats[0] = (0.8 if good else 0.2) + 0.1 * rng.random()
    net0 = net_good if good else net_bad
    cells = {(k, lag): mk_cell(net0, k=k, lag=lag) for k, lag in x.CELL_KEYS}
    mig = x.date_start_ms(date) + 12 * 3_600_000 + i * 1000
    return {"mint": f"m-{date}-{i}", "date": date, "mig_ms": mig, "source": source_of(date), "block": block_of(date), "features": feats, "cells": cells, "c1": 1 if good else 0, "c1_missing": False, "good": good}


def mk_universe(with_p4=True, n_good=5, n_bad=3, seed=3, **kw):
    rng = random.Random(seed)
    out = []
    for d in x.pool_dates(with_p4):
        for i in range(n_good + n_bad):
            out.append(mk_urow(i, d, i < n_good, rng, **kw))
    out.sort(key=lambda u: (u["mig_ms"], u["mint"]))
    return out


def sel_good(universe):
    return [u["good"] for u in universe]


def nested_of(universe, sel):
    return {"score": [1.0 if s else 0.0 for s in sel], "thr": {"p90": [0.5] * len(universe)}}


def transfer_of(universe, sel):
    return {"selected": [bool(s) and u["block"] == "P2" for s, u in zip(sel, universe)], "threshold_p90": 0.5}


def passing_eval(universe=None, frozen_sel=None, new_sel=None, transfer=None, with_p4=True):
    u = universe or mk_universe(with_p4)
    new = new_sel if new_sel is not None else sel_good(u)
    fr = frozen_sel if frozen_sel is not None else [not s for s in sel_good(u)]
    return x.evaluate_config(u, nested_of(u, new), transfer or transfer_of(u, new), fr, with_p4)


def make_view(root: Path, hours, sha=True):
    write_fast_format_root(root, hours, {})
    if sha:
        lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  ./{f.relative_to(root)}" for f in sorted(root.rglob("*.zst"))]
        (root / "VIEW.sha256").write_text("\n".join(lines) + "\n")


# --- doc <-> code ---------------------------------------------------------------------------------------------------


class DocCodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = x.PLAN.read_text(encoding="utf-8")

    def test_plan_phrases_present(self):
        for s in (
            "505,000 lamports per side", "0.0042038", "`min_data_in_leaf = 75`", "index = round(0.90 * (n-1))", "35 minutes", "no UTC date contributes more than 20%",
            "1,000 draws, seed 1", "26.08 bps", "16 bps of proceeds", "0.8030766588450794", "a1810d219ed61db64a396f40dc302ce5", "at most 3 configurations (C1-C3)",
            "**36 UTC dates, of which 27 are non-P1**", "9 + 14 + 7 = 30 dates, 21 non-P1", "816 counted hours", "A MISS (no fill) is labelled 0", "exit lag 2", "k = 6", "0.05 SOL",
            "n entered ≥ 100", "≥ 5 UTC dates", "p80 and p95 are reported and never selected on", "The screen counts a UTC date with no trade as not positive",
            "pool A cannot change; DEC-017 (a) fast-only was +0.0005 [−0.0137, +0.0137]", "fresh-0808",
        ):
            self.assertIn(s, self.plan, s)

    def test_constants_match_the_plan(self):
        self.assertEqual(x.FEE, 505_000)
        self.assertEqual(x.K, 6)
        self.assertEqual(x.SIZE_SOL, 0.05)
        self.assertEqual(x.EXIT_LAG, 2)
        self.assertEqual((x.ENTRY_GAP_BPS, x.SELL_SHORTFALL_BPS), (26.08, 16.0))
        self.assertAlmostEqual(x.HAIRCUT_FACTOR, 0.0042038, places=7)
        self.assertEqual(x.PURGE_MIN, 35)
        self.assertEqual(x.THRESHOLD_PCT, 0.90)
        self.assertEqual((x.MIN_N, x.MIN_DATES, x.CONCENTRATION_MAX), (100, 5, 0.20))
        self.assertEqual((x.BOOT_DRAWS, x.BOOT_SEED), (1000, 1))
        self.assertEqual(x.FROZEN_THRESHOLD, 0.8030766588450794)
        self.assertEqual(x.MODEL_MD5, "a1810d219ed61db64a396f40dc302ce5")
        self.assertEqual(x.VMAP_0909_SHA256, "PENDING_JOB_224")  # the manager fills the sha in after job #224; a test change then belongs in the same reviewed commit
        self.assertEqual(x.VMAP_0909_PATH, "/data/mal/pumpswap-virtual/pool_v_0909.json")
        self.assertFalse(hasattr(x, "VMAP_SHA256"))  # the P2 pin on pool_v_0814 is gone
        self.assertEqual(x.UNPRICEABLE_MAX_FRACTION, 0.005)
        self.assertEqual(x.V_MAX_MISSING_FRACTION, 0.01)
        self.assertEqual(x.TRIES_CAP, 3)
        self.assertEqual(x.MDL_C3, 75)
        self.assertEqual({c: v["min_data_in_leaf"] for c, v in x.CONFIGS.items()}, {"c1": 20, "c2": 20, "c3": 75})
        self.assertEqual(x.CONFIGS["c3"]["label"], x.CONFIGS["c2"]["label"])  # C3 = C2's label
        self.assertNotEqual(x.CONFIGS["c1"]["label"], x.CONFIGS["c2"]["label"])
        self.assertEqual((x.MAX_WORKERS_CAP, x.DEFAULT_WORKERS), (8, 4))
        self.assertEqual(x.FAST_ONLY_LINE, "pool A cannot change; DEC-017 (a) fast-only was +0.0005 [−0.0137, +0.0137]")

    def test_pool_hours_and_dates_match_the_plan(self):
        self.assertEqual(x.BLOCK_COUNTED_HOURS, {"P1": 216, "P2": 312, "P3": 144, "P4": 144})
        self.assertEqual({b: len(x.block_hours(b)) for b in x.BLOCKS}, x.BLOCK_COUNTED_HOURS)
        self.assertEqual(sum(x.BLOCK_COUNTED_HOURS.values()), 816)
        self.assertEqual(sum(x.BLOCK_COUNTED_HOURS.values()) // 24, 34)
        self.assertEqual((len(x.pool_dates(True)), len(x.non_p1_dates(True))), (36, 27))
        self.assertEqual((len(x.pool_dates(False)), len(x.non_p1_dates(False))), (30, 21))
        self.assertEqual((len(x.block_dates("P1")), len(x.block_dates("P2")), len(x.p2_dates())), (9, 14, 14))
        self.assertEqual(len(set(x.block_dates("P3")) | set(x.block_dates("P4"))), 13)
        self.assertEqual(sorted(set(x.block_dates("P3")) & set(x.block_dates("P4"))), ["2026-09-09"])  # one fold
        self.assertEqual(x.block_dates("P2")[0], "2026-08-15")
        self.assertEqual(x.BLOCKS["P2"][0], "2026-08-15T12")  # migrations before this are excluded
        self.assertEqual(len(x.septembers() if hasattr(x, "septembers") else [d for d in x.pool_dates(True) if d not in x.p2_dates()]), 22)
        self.assertEqual(len([d for d in x.pool_dates(False) if d not in x.p2_dates()]), 16)

    def test_numbers_in_the_plan_table(self):
        rows = re.findall(r"^\| P([1-4]) \| [^|]*\| [^|]*\| (\d+)(?: counted)?[^|]*\| (\d+) \|", self.plan, re.M)
        self.assertEqual({f"P{a}": int(b) for a, b, _ in rows}, {"P1": 216, "P2": 312, "P3": 144, "P4": 144})
        self.assertEqual({f"P{a}": int(c) for a, _, c in rows}, {"P1": 9, "P2": 13, "P3": 6, "P4": 6})  # the plan's "days" column


# --- guards ----------------------------------------------------------------------------------------------------------


class GuardTests(unittest.TestCase):
    def test_every_reserved_holdout_or_forward_root_is_refused(self):
        for p in (
            "/data/mal/clean-view/fresh-0808/w1", "/data/mal/blocks/fresh-0808/w1", "/data/mal/clean-view/fresh-0828/w2", "/data/mal/blocks-clean/fresh-0828/w1",
            "/data/mal/blocks/forward-1002", "/data/mal/clean-view/forward-1002/x", "/data/mal/blocks/forward-1016", "/data/mal/forward-family/x", "/data/mal/exp012-forward/x",
            "/var/lib/mal/backfill-fast-b", "/var/lib/mal/backfill-fast-c/trades", "/data/mal/blocks/fresh-0903/w1", "/data/mal/blocks/truth-1001",
        ):
            with self.assertRaises(x.Refused, msg=p):
                x.refuse_reserved(p)
            with self.assertRaises(x.Refused, msg=p):
                x.guard_p2([p], verify=False)
            with self.assertRaises(x.Refused, msg=p):
                x.guard_p4([p], verify=False)

    def test_hours_from_2026_10_02_and_outside_the_windows_are_refused(self):
        for h in ("2026-10-02T00", "2026-10-02T10", "2026-10-06T00", "2026-11-01T00"):
            with self.assertRaises(x.Refused, msg=h):
                x.assert_hours_allowed([h])
        for h in ("2026-08-14T11", "2026-08-28T12", "2026-08-08T12", "2026-08-13T23", "2026-08-30T00", "2026-09-02T23", "2026-09-28T00", "2026-09-17T00"):
            with self.assertRaises(x.Refused, msg=h):
                x.assert_hours_allowed([h])
        x.assert_hours_allowed(["2026-08-14T12", "2026-08-28T11", "2026-09-03T12", "2026-09-09T11", "2026-09-19T00", "2026-09-27T23", "2026-09-09T12", "2026-09-15T11"])
        with self.assertRaises(x.Refused):  # P4 hours are only allowed when P4 is in the pool
            x.assert_hours_allowed(["2026-09-10T00"], with_p4=False)

    def test_p2_only_the_explore_0814_base_and_pool_window(self):
        with tempfile.TemporaryDirectory() as d:
            v = Path(d) / "w1"
            make_view(v, ["2026-08-14T12"])
            with self.assertRaises(x.Refused):  # not under the explore-0814 base
                x.guard_p2([v], verify=False, enforce_base=True)
            with mock.patch.object(x, "P2_SEALED", ("2026-08-14T12", "2026-08-14T13")):
                g = x.guard_p2([v], verify=True, enforce_base=False)
                self.assertEqual(g["pool"], ["2026-08-14T12"])
            for h in ("2026-09-10T12", "2026-10-02T00", "2026-08-28T12"):  # the EXP-011 block, forward, one past the pool
                v2 = Path(d) / h
                make_view(v2, [h])
                with self.assertRaises(x.Refused, msg=h):
                    x.guard_p2([v2], verify=False, enforce_base=False)

    def test_p4_absent_is_the_30_date_pool_and_present_needs_a_full_verified_view(self):
        self.assertIsNone(x.guard_p4([]))
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "a"
            make_view(a, ["2026-09-09T12", "2026-09-09T13"])
            with self.assertRaises(x.Refused):  # does not tile the block: a gap
                x.guard_p4([a])
            b = Path(d) / "b"
            make_view(b, ["2026-09-09T12"], sha=False)
            with self.assertRaises(x.Refused):  # no VIEW.sha256
                x.guard_p4([b])
            c = Path(d) / "c"
            make_view(c, ["2026-09-09T11"])  # P3's last hour is not P4's
            with self.assertRaises(x.Refused):
                x.guard_p4([c])
            with mock.patch.object(x, "BLOCKS", {**x.BLOCKS, "P4": ("2026-09-09T12", "2026-09-09T14")}):
                self.assertIsNotNone(x.guard_p4([a]))
                f = next((a / "trades").glob("*.zst"))
                f.write_bytes(b"tamper")
                with self.assertRaises(x.Refused):
                    x.guard_p4([a])  # hash mismatch

    def test_p3_only_the_clean_copy_base_and_manifests(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(x.Refused):
                x.guard_p3(d, verify=False, enforce_base=True)
            with self.assertRaises(x.Refused):  # no dedupe manifests
                x.guard_p3(d, verify=False, enforce_base=False)
        with self.assertRaises(x.Refused):
            x.guard_p3("/data/mal/blocks/fresh-0903", verify=False)

    def test_p1_pinned_names_and_view_files(self):
        names = ("fast-pool-2026-09-18T23_2026-09-22T00", "oracle-insample-2026-09-22_25", "oracle-live-2026-09-25_27")
        with tempfile.TemporaryDirectory() as d:
            roots = [Path(d) / n for n in names]
            for r in roots:
                r.mkdir()
                (r / "VIEW.sha256").write_text("x\n")
            g = x.guard_p1(*roots, verify=False)
            self.assertEqual(set(g["roots"]), {"fast", "insample", "live"})
            with self.assertRaises(x.Refused):  # wrong order: names are pinned per role
                x.guard_p1(roots[1], roots[0], roots[2], verify=False)
            (roots[0] / "VIEW.sha256").unlink()
            with self.assertRaises(x.Refused):
                x.guard_p1(*roots, verify=False)
            (roots[0] / "VIEW.sha256").write_text("x\n")
            with self.assertRaises(x.Refused):  # verify=True: unparsable VIEW.sha256
                x.guard_p1(*roots, verify=True)

    def test_vmap_pin_workers_and_prior_tries(self):
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "m.json"
            m.write_text("{}")
            self.assertEqual(x.check_vmap(m), hashlib.sha256(b"{}").hexdigest())
            with self.assertRaises(x.Refused):
                x.check_vmap(m, "0" * 64)
            with self.assertRaises(x.Refused):
                x.check_vmap(Path(d) / "nope.json")
            for n in (0, 9, 64):
                with self.assertRaises(x.Refused):
                    x.check_workers(n)
            self.assertEqual((x.check_workers(1), x.check_workers(8)), (1, 8))
            log = Path(d) / "t.jsonl"
            log.write_text(json.dumps({"config": {"key": "exp012_other"}}) + "\n")
            x.check_no_prior_tries(log, Path(d) / "absent.jsonl")
            log.write_text(json.dumps({"config": {"key": "exp015_c2", "status": "started"}}) + "\n")
            with self.assertRaises(x.Refused):
                x.check_no_prior_tries(Path(d) / "absent.jsonl", log)

    def test_frozen_artifacts_checked(self):
        self.assertEqual(x.check_frozen_model(x.DEFAULT_ARTIFACT_DIR)["model_md5"], "a1810d219ed61db64a396f40dc302ce5")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(Exception):
                x.check_frozen_model(Path(d))


# --- folds, purge, threshold -------------------------------------------------------------------------------------------


class FoldTests(unittest.TestCase):
    def test_purge_is_35_minutes_each_side_of_the_date(self):
        import numpy as np

        start = x.date_start_ms("2026-09-20")
        end = start + x.DAY_MS
        p = x.PURGE_MS
        m = np.array([start - p - 1, start - p, start - 1, start, end - 1, end, end + p - 1, end + p])
        self.assertEqual(x.purge_mask(m, start, end).tolist(), [False, True, True, False, False, True, True, False])
        self.assertEqual(x.PURGE_MS, 35 * 60 * 1000)

    def test_threshold_index_is_non_interpolating_round(self):
        for n in (1, 2, 10, 11, 101, 1000):
            self.assertEqual(x.pct_index(n), int(round(0.90 * (n - 1))))
        vals = [float(i) for i in range(11)]  # sorted 0..10 -> index round(9.0) = 9
        self.assertEqual(x.percentile(vals), 9.0)
        self.assertEqual(x.percentile(list(reversed(vals))), 9.0)
        self.assertEqual(x.percentile([float(i) for i in range(10)]), 8.0)  # round(8.1) = 8, not interpolated
        self.assertIsNone(x.percentile([]))
        self.assertEqual(x.percentile(vals), fz._percentile(sorted(vals), 0.90))

    def _tiny(self, with_p4=False):
        u = mk_universe(with_p4, n_good=2, n_bad=2, seed=5)
        lab = x.labels(u)
        dates = x.pool_dates(with_p4)
        return u, x.fit_arrays(u, lab, dates), dates

    def test_inner_and_outer_folds_drop_the_held_out_date_and_both_purge_windows(self):
        import numpy as np

        u, arrays, dates = self._tiny()
        x._init_fit(None, arrays)
        # plant rows 10 and 20 minutes before / after date 5's boundaries, and at 40 minutes (kept)
        di = arrays["date_idx"].copy()
        mig = arrays["mig_ms"].copy()
        d = 5
        start = int(arrays["date_start_ms"][d])
        end = start + x.DAY_MS
        a, b, c, e = 0, 1, 2, 3  # row indices on other dates, re-timed next to date d
        for i, t in ((a, start - 10 * 60_000), (b, end + 20 * 60_000), (c, start - 40 * 60_000), (e, end + 36 * 60_000)):
            mig[i] = t
            di[i] = d - 1 if t < start else d + 1
        arrays["mig_ms"], arrays["date_idx"] = mig, di
        x._init_fit(None, arrays)
        seen = []

        def fake_fit(xm, y, mdl):
            seen.append((len(xm), mdl))
            return object()

        trained_rows = []
        orig_fit = x.fit_cfg

        def spy_fit(xm, y, mdl):
            # recover the rows by matching feature rows
            idx = [int(np.where((arrays["X"] == r).all(axis=1))[0][0]) for r in xm]
            trained_rows.append(idx)
            return orig_fit(xm, y, mdl)

        with mock.patch.object(x, "fit_cfg", spy_fit):
            out = x.task_outer({"kind": "outer", "cfg": "c2", "scope": "all", "date": d})
        self.assertTrue(out["trained"])
        outer_fit_rows = trained_rows[-1]  # the last fit is the outer one
        self.assertTrue(all(arrays["date_idx"][i] != d for i in outer_fit_rows))
        for purged in (a, b):
            self.assertNotIn(purged, outer_fit_rows)
        for kept in (c, e):
            self.assertIn(kept, outer_fit_rows)
        # inner folds: every fit excludes the outer date, the outer purge rows, and its own held-out date + purge
        for rows in trained_rows[:-1]:
            self.assertTrue(all(arrays["date_idx"][i] != d for i in rows))
            self.assertNotIn(a, rows)
            self.assertNotIn(b, rows)
        # the inner purge around another date e0: plant a row 5 minutes before its start
        e0 = 9
        s0 = int(arrays["date_start_ms"][e0])
        arrays["mig_ms"][c] = s0 - 5 * 60_000
        arrays["date_idx"][c] = e0 - 1
        x._init_fit(None, arrays)
        trained_rows.clear()
        with mock.patch.object(x, "fit_cfg", spy_fit):
            res = x.inner_fold(np.ones(len(arrays["X"]), dtype=bool), e0, arrays["y_c2"], 20)
        self.assertIsNotNone(res)
        self.assertNotIn(c, trained_rows[0])  # inside the 35-minute inner purge of date e0
        self.assertTrue(all(arrays["date_idx"][i] != e0 for i in trained_rows[0]))
        self.assertTrue(all(arrays["date_idx"][i] == e0 for i in res[0]))

    def test_nested_oof_final_threshold_is_p90_of_the_pooled_outer_scores_and_each_fold_uses_its_own(self):
        u, arrays, dates = self._tiny()
        r = x.Runner(arrays, None, 1)
        ids = sorted({dates.index(v["date"]) for v in u})
        n = x.nested_oof(r, "c2", len(u), ids)
        scores = [s for s in n["score"] if s == s]
        self.assertEqual(len(scores), len(u))
        self.assertEqual(n["final_threshold_p90"], fz._percentile(sorted(scores), 0.90))
        self.assertEqual(n["n_pooled_oof"], len(u))
        for d in ids:  # a row carries its own fold's p90 (built only from the other dates)
            thr = {n["thr"]["p90"][i] for i, v in enumerate(u) if dates.index(v["date"]) == d}
            self.assertEqual(len(thr), 1)
            self.assertEqual(thr.pop(), next(f["thr_p90"] for f in n["folds"] if f["date_idx"] == d))
        self.assertTrue(all(n["thr"]["p80"][i] <= n["thr"]["p90"][i] <= n["thr"]["p95"][i] for i in range(len(u))))
        self.assertEqual(sum(f["n_test"] for f in n["folds"]), len(u))

    def test_c3_min_data_in_leaf_is_75_only_for_c3_and_params_are_restored(self):
        seen = {}

        def fake_fit(xm, y):
            seen["mdl"] = fz.LGB_PARAMS["min_data_in_leaf"]
            return "m"

        with mock.patch.object(fz, "_fit", fake_fit):
            self.assertEqual(x.fit_cfg([[0.0]], [0], 75), "m")
            self.assertEqual(seen["mdl"], 75)
            self.assertEqual(fz.LGB_PARAMS["min_data_in_leaf"], 20)
            x.fit_cfg([[0.0]], [0], 20)
            self.assertEqual(seen["mdl"], 20)
        u, arrays, dates = self._tiny()
        x._init_fit(None, arrays)
        used = {}
        for cid in ("c1", "c2", "c3"):
            with mock.patch.object(x, "fit_cfg", lambda xm, y, mdl, cid=cid: used.setdefault(cid, set()).add(mdl) or fz._fit(xm, y)):
                x.task_outer({"kind": "outer", "cfg": cid, "scope": "all", "date": 3})
        self.assertEqual(used, {"c1": {20}, "c2": {20}, "c3": {75}})

    def test_spawn_pool_runs_the_same_tasks(self):
        u, arrays, dates = self._tiny()
        ids = [dates.index(v["date"]) for v in u][:0] or sorted({dates.index(v["date"]) for v in u})[:3]
        inline = x.nested_oof(x.Runner(arrays, None, 1), "c2", len(u), ids)
        with tempfile.TemporaryDirectory() as d:
            r = x.Runner(arrays, Path(d) / "a.npz", 2)
            try:
                pooled = x.nested_oof(r, "c2", len(u), ids)
            finally:
                r.close()
        a = [s for s in inline["score"] if s == s]
        b = [s for s in pooled["score"] if s == s]
        self.assertEqual(len(a), len(b))
        self.assertTrue(all(abs(p - q) < 1e-12 for p, q in zip(a, b)))
        self.assertEqual(inline["final_threshold_p90"], pooled["final_threshold_p90"])


# --- labels, universe ------------------------------------------------------------------------------------------------


class LabelTests(unittest.TestCase):
    def _u(self, cell):
        return {"cells": {x.PRIMARY_CELL: cell}}

    def test_c2_is_the_sign_of_expected_net_under_p_fail_with_miss_zero(self):
        win = mk_cell(20_000_000)
        self.assertEqual(x.label_c2(self._u(win)), 1)
        self.assertEqual(x.label_c2(self._u(mk_cell(-20_000_000))), 0)
        self.assertEqual(x.label_c2(self._u(mk_cell(0, filled=False, status=MISS, sides=1))), 0)  # MISS = 0
        self.assertEqual(x.label_c2(self._u(mk_cell(0, censored=True))), 0)
        # E = (1 - p) * net' - p * 505000 at the haircut net, both fees in net'
        net0 = 2_000_000
        for p, want in ((0.0, 1), (0.9, 0)):
            c = mk_cell(net0, p=p)
            e = (1 - p) * (net0 + x.haircut_delta(c) - 2 * x.FEE) - p * x.FEE
            self.assertEqual(x.label_c2(self._u(c)), 1 if e > 0 else 0)
            self.assertEqual(1 if e > 0 else 0, want)
        # a trade that is positive before the haircut and fees but negative after is labelled 0
        self.assertEqual(x.label_c2(self._u(mk_cell(1_000_000))), 0)

    def test_c1_is_the_exp012_label_and_missing_rows_are_zero_and_counted(self):
        v = {"P2": [{"mint": f"m{i}", "mig_ms": x.hour_ms("2026-08-16T00") + i, "features": [0.0] * NF, "cells": [{"k": 6, "lag": 2, "size": 50_000_000, "censored": False, "filled": True, "status": 1, "net0": 1, "sides": 2, "p_press": 0.1}]} for i in range(3)]}
        nv = {"P2": [{"mint": "m0", "press": 5.0}, {"mint": "m1", "press": -5.0}]}  # m2 has no EXP-012-pricing row
        u, st = x.build_universe(v, nv, with_p4=True)
        self.assertEqual([r["c1"] for r in u], [1, 0, 0])
        self.assertEqual(st["c1_missing_label_0"], 1)
        self.assertEqual([r["c1_missing"] for r in u], [False, False, True])
        # C1 and C2 can differ on the same mint (different pricing)
        self.assertEqual(x.labels(u)["c1"], [1, 0, 0])
        self.assertEqual(x.labels(u)["c2"], [0, 0, 0])  # net0 = 1 lamport cannot cover the fees

    def test_universe_windows_dedupe_drops_and_sha(self):
        def rec(mint, mig, cells=None):
            c = cells if cells is not None else [{"k": 6, "lag": 2, "size": 50_000_000, "censored": False, "filled": False, "status": MISS, "net0": 0, "sides": 1, "p_press": 0.0}]
            return {"mint": mint, "mig_ms": mig, "features": [float(len(mint))] * NF, "cells": c}

        h = x.hour_ms
        v = {
            "P2": [rec("buf", h("2026-08-15T11")), rec("in", h("2026-08-15T12")), rec("late", h("2026-08-28T12")), rec("cens", h("2026-08-20T00"), [{"k": 6, "lag": 2, "size": 50_000_000, "censored": True}]), rec("nocell", h("2026-08-20T00"), [])],
            "P3": [rec("seam", h("2026-09-09T12")), rec("p3", h("2026-09-09T11"))],
            "P4": [rec("p4", h("2026-09-09T12"))],
        }
        u, st = x.build_universe(v, {}, with_p4=True)
        self.assertEqual(sorted(r["mint"] for r in u), ["in", "p3", "p4"])  # MISS rows are kept (filled AND MISS)
        self.assertEqual((st["dropped_outside_window"], st["dropped_censored_primary"], st["dropped_no_primary_cell"]), (3, 1, 1))
        u2, _ = x.build_universe(v, {}, with_p4=False)  # no P4: its source is skipped
        self.assertEqual(sorted(r["mint"] for r in u2), ["in", "p3"])
        with self.assertRaises(x.Integrity):
            x.build_universe({"P2": [rec("dup", h("2026-08-20T00"))], "P3": [rec("dup", h("2026-09-04T00"))]}, {}, True)
        a, b = x.universe_sha256(u), x.universe_sha256(list(reversed(u)))
        self.assertEqual(a, b)  # order-free
        self.assertEqual(a, hashlib.sha256(x.universe_bytes(u)).hexdigest())
        first = json.loads(x.universe_bytes(u).splitlines()[0])
        self.assertEqual(set(first), {"mint", "date", "features"})  # (mint, UTC migration date, features), no labels
        u3 = [dict(r, c1=1 - r["c1"]) for r in u]
        self.assertEqual(x.universe_sha256(u3), a)  # labels are per config and not in the universe hash


class CostTests(unittest.TestCase):
    def test_haircut_formula_and_exclusions(self):
        c = mk_cell(10_000_000)
        proceeds = 10_000_000 + 50_000_000
        self.assertAlmostEqual(x.haircut_delta(c), -proceeds * 0.0042038, delta=5)
        self.assertAlmostEqual(x.haircut_delta(c), bc.haircut_delta({**c, "censored": False}))
        self.assertEqual(x.haircut_delta(mk_cell(0, filled=False, status=MISS, sides=1)), 0.0)
        self.assertEqual(x.haircut_delta(mk_cell(0, censored=True)), 0.0)
        self.assertAlmostEqual(x.haircut_delta(mk_cell(-70_000_000)), 0.0)  # P = max(net0 + size, 0) = 0
        n = x.cell_nets(c)
        net0 = 10_000_000 + x.haircut_delta(c)
        self.assertAlmostEqual(n["flat"], mixed_net(net0, 2, 1, 505_000, FLAT_FAIL))
        self.assertAlmostEqual(n["press"], mixed_net(net0, 2, 1, 505_000, 0.1))
        raw = x.cell_nets(c, haircut=False)
        self.assertAlmostEqual(raw["flat"], mixed_net(10_000_000, 2, 1, 505_000, FLAT_FAIL))
        self.assertLess(n["flat"], raw["flat"])
        miss = x.cell_nets(mk_cell(0, filled=False, status=MISS, sides=1))
        self.assertEqual((miss["flat"], miss["press"]), (-505_000.0, -505_000.0))  # a MISS pays the per-side fee
        self.assertIsNone(x.cell_nets(mk_cell(0, censored=True)))
        self.assertIsNone(x.cell_nets(None))


# --- bars --------------------------------------------------------------------------------------------------------------


def trades_on(dates_totals, per_date=4, mint_prefix="t"):
    """Per-date trades whose per-trade pnl is total / per_date lamports (same on both legs)."""
    out = []
    for d, tot in dates_totals.items():
        for i in range(per_date):
            v = tot * SOL / per_date
            out.append({"mint": f"{mint_prefix}-{d}-{i}", "day": d, "filled": True, "flat": v, "press": v, "source": "P2", "block": "P2"})
    return out


class BarTests(unittest.TestCase):
    def test_gate_bars_each_condition(self):
        days = x.p2_dates()[:10]
        good = x.leg_stats(trades_on({d: 0.01 for d in days}, per_date=12), "flat")
        b = x.gate_bars(good, 10)
        self.assertTrue(b["all"], b)
        # n < 100
        self.assertFalse(x.gate_bars(x.leg_stats(trades_on({d: 0.01 for d in days}, per_date=9), "flat"), 10)["n_ge_100"])
        # fewer than 5 dates with trades
        s = x.leg_stats(trades_on({d: 0.01 for d in days[:4]}, per_date=30), "flat")
        self.assertFalse(x.gate_bars(s, 4)["dates_ge_5"])
        # a date with no trade is NOT positive: 6 positive dates with trades is a majority of 10 but not of 36
        s6 = x.leg_stats(trades_on({d: 0.01 for d in days[:6]}, per_date=20), "flat")
        self.assertTrue(x.gate_bars(s6, 10)["majority_dates_positive"])
        self.assertFalse(x.gate_bars(s6, 36)["majority_dates_positive"])
        self.assertFalse(x.gate_bars(s6, 12)["majority_dates_positive"])  # exactly half is not a majority
        self.assertTrue(x.gate_bars(s6, 11)["majority_dates_positive"])
        # CI lower bound <= 0
        rng = random.Random(1)
        noisy = [{"mint": f"n{i}", "day": days[i % 10], "filled": True, "flat": rng.gauss(0, 5e6), "press": 0.0, "source": "P2", "block": "P2"} for i in range(200)]
        self.assertFalse(x.gate_bars(x.leg_stats(noisy, "flat"), 10)["ci_lower_gt_0"])
        # ex-top-3 <= 0
        t = trades_on({d: 0.001 for d in days}, per_date=12)
        for i in range(3):
            t[i]["flat"] = 5 * SOL
        for i in range(3, len(t)):
            t[i]["flat"] = -1.0
        self.assertFalse(x.gate_bars(x.leg_stats(t, "flat"), 10)["ex_top3_gt_0"])
        self.assertFalse(x.gate_bars(x.leg_stats([], "flat"), 10)["all"])

    def test_concentration_bar(self):
        days = x.p2_dates()[:5]
        even = x.concentration_bar(x.leg_stats(trades_on({d: 1.0 for d in days}), "flat"))
        self.assertTrue(even["all"])  # exactly 20% each is not "more than 20%"
        self.assertAlmostEqual(even["max_share_of_positive_total"], 0.2)
        skew = x.concentration_bar(x.leg_stats(trades_on({days[0]: 5.0, days[1]: 1.0, days[2]: 1.0, days[3]: 1.0, days[4]: 1.0}), "flat"))
        self.assertFalse(skew["share_le_20pct"])
        self.assertTrue(skew["ex_best_gt_0"])
        # ex-best <= 0: a big win date carries a total that the rest do not cover
        neg = x.concentration_bar(x.leg_stats(trades_on({days[0]: 1.0, days[1]: -2.0, days[2]: -2.0}), "flat"))
        self.assertFalse(neg["ex_best_gt_0"])
        self.assertFalse(x.concentration_bar(x.leg_stats(trades_on({d: -1.0 for d in days}), "flat"))["all"])  # no positive dates
        six = x.concentration_bar(x.leg_stats(trades_on({d: 1.0 for d in x.p2_dates()[:6]}), "flat"))
        self.assertTrue(six["all"])

    def test_date_cluster_ci_resamples_dates_with_seed_1(self):
        by = {"a": [1.0, 1.0], "b": [3.0], "c": [-1.0, -1.0, -1.0], "d": [2.0, 2.0]}
        ci = x.date_cluster_ci(by)
        self.assertEqual(ci, x.date_cluster_ci(by))
        # reference implementation
        rng = random.Random(1)
        keys = sorted(by)
        means = []
        for _ in range(1000):
            pick = [rng.randrange(4) for _ in keys]
            vals = [v for i in pick for v in by[keys[i]]]
            means.append(sum(vals) / len(vals))
        means.sort()
        from tools.paper_attention_promote import _pct

        self.assertAlmostEqual(ci[0], _pct(means, 0.05) / SOL)
        self.assertAlmostEqual(ci[1], _pct(means, 0.95) / SOL)
        self.assertIsNone(x.date_cluster_ci({}))

    def test_all_six_bars_pass_on_the_synthetic_winner(self):
        ev = passing_eval()
        for i in range(1, 7):
            self.assertTrue(ev["bars"][f"bar{i}"]["pass"], (i, ev["bars"][f"bar{i}"]))
        self.assertTrue(ev["passes"])
        self.assertGreater(ev["pooled_press_mean_non_p1_sol"], 0)

    def test_bar1_fails_when_only_a_few_trades_are_entered(self):
        u = mk_universe()
        few = [g and i % 4 == 0 for i, g in enumerate(sel_good(u))]
        ev = passing_eval(u, new_sel=few)
        self.assertFalse(ev["bars"]["bar1"]["pass"])
        self.assertFalse(ev["passes"])

    def test_bar2_non_p1_alone_cannot_be_carried_by_p1(self):
        u = mk_universe()
        only_p1 = [g and v["block"] == "P1" for g, v in zip(sel_good(u), u)]
        ev = passing_eval(u, new_sel=only_p1)
        self.assertTrue(ev["bars"]["bar1"]["report"]["flat"]["n"] > 0)
        self.assertFalse(ev["bars"]["bar2"]["pass"])
        self.assertFalse(ev["passes"])

    def test_bar3_paired_vs_frozen(self):
        u = mk_universe()
        same = sel_good(u)
        ev = passing_eval(u, frozen_sel=same)  # frozen picks the same mints: x = 0
        self.assertFalse(ev["bars"]["bar3"]["pass"])
        self.assertEqual(ev["bars"]["bar3"]["report"]["jaccard"], 1.0)
        ev = passing_eval(u)  # frozen picks the losers
        b3 = ev["bars"]["bar3"]["report"]
        self.assertEqual(b3["jaccard"], 0.0)
        self.assertGreater(b3["flat"]["per_trade_difference_sol"], 0)
        self.assertTrue(b3["flat"]["pass"] and b3["press"]["pass"])
        # frozen better than the new model: new picks the losers, frozen the winners -> x < 0
        ev = passing_eval(u, new_sel=[not g for g in sel_good(u)], frozen_sel=sel_good(u))
        self.assertFalse(ev["bars"]["bar3"]["pass"])
        self.assertLess(ev["bars"]["bar3"]["report"]["flat"]["mean_x_sol"], 0)
        # only P1 is excluded from the paired scope
        self.assertEqual(ev["bars"]["bar3"]["report"]["n_migrations"], sum(1 for v in u if v["block"] != "P1"))
        # a new model that does not beat frozen by a margin the CI sees fails even with a positive mean
        mixed = [(g or (i % 7 == 0)) for i, g in enumerate(sel_good(u))]
        fr = [g or (i % 6 == 0) for i, g in enumerate(sel_good(u))]
        ev = passing_eval(u, new_sel=mixed, frozen_sel=fr)
        self.assertFalse(ev["bars"]["bar3"]["pass"])

    def test_bar4_concentration_on_all_and_non_p1(self):
        u = mk_universe()
        for v in u:  # make 2026-09-20 hold most of the profit
            if v["date"] == "2026-09-20" and v["good"]:
                v["cells"] = {k: mk_cell(2_000_000_000, k=k[0], lag=k[1]) for k in x.CELL_KEYS}
        ev = passing_eval(u)
        self.assertFalse(ev["bars"]["bar4"]["pass"])
        self.assertFalse(ev["bars"]["bar4"]["all_dates"]["flat"]["share_le_20pct"])
        self.assertTrue(ev["bars"]["bar4"]["non_p1"]["flat"]["all"])  # the concentrated date is a P1 date
        u2 = mk_universe()
        for v in u2:
            if v["date"] == "2026-08-20" and v["good"]:
                v["cells"] = {k: mk_cell(2_000_000_000, k=k[0], lag=k[1]) for k in x.CELL_KEYS}
        ev2 = passing_eval(u2)
        self.assertFalse(ev2["bars"]["bar4"]["non_p1"]["flat"]["share_le_20pct"])
        self.assertFalse(ev2["bars"]["bar4"]["pass"])

    def test_bar5_p2_p4_mean_positive_under_both_legs(self):
        u = mk_universe()
        sel = [g and v["block"] not in ("P2", "P4") for g, v in zip(sel_good(u), u)]
        ev = passing_eval(u, new_sel=sel)
        self.assertFalse(ev["bars"]["bar5"]["pass"])
        # P2+P4 selected but losing: select the bad rows there
        sel2 = [(g if v["block"] not in ("P2", "P4") else (not g)) for g, v in zip(sel_good(u), u)]
        self.assertFalse(passing_eval(u, new_sel=sel2)["bars"]["bar5"]["pass"])
        self.assertEqual(passing_eval(mk_universe(False), with_p4=False)["bars"]["bar5"]["scope"], "P2 only (P4 not in the pool)")
        self.assertEqual(passing_eval()["bars"]["bar5"]["scope"], "P2+P4 only")

    def test_bar6_september_to_august_transfer(self):
        u = mk_universe()
        sel = sel_good(u)
        p2 = [i for i, v in enumerate(u) if v["block"] == "P2"]
        none = {"selected": [False] * len(u), "threshold_p90": 0.5}
        self.assertFalse(passing_eval(u, transfer=none)["bars"]["bar6"]["pass"])
        # positive mean but only 7 of 14 dates positive: majority needs more than half of P2's dates
        first7 = set(x.p2_dates()[:7])
        t7 = {"selected": [v["good"] and v["date"] in first7 for v in u], "threshold_p90": 0.5}
        b = passing_eval(u, transfer=t7)["bars"]["bar6"]
        self.assertGreater(b["mean_sol"]["flat"], 0)
        self.assertEqual(b["dates_positive"]["flat"], 7)
        self.assertFalse(b["pass"])
        first8 = set(x.p2_dates()[:8])
        t8 = {"selected": [v["good"] and v["date"] in first8 for v in u], "threshold_p90": 0.5}
        self.assertTrue(passing_eval(u, transfer=t8)["bars"]["bar6"]["pass"])
        # negative mean fails under one leg
        bad = {"selected": [(not v["good"]) and v["block"] == "P2" for v in u], "threshold_p90": 0.5}
        self.assertFalse(passing_eval(u, transfer=bad)["bars"]["bar6"]["pass"])
        self.assertEqual(len(p2), 14 * 8)
        self.assertEqual(passing_eval(u)["bars"]["bar6"]["majority_needed_of"], 14)

    def test_both_fail_models_are_required(self):
        u = mk_universe()
        for v in u:  # pressure model makes every good trade lose, flat model keeps it positive
            if v["good"]:
                v["cells"] = {k: mk_cell(20_000_000, p=0.999, k=k[0], lag=k[1]) for k in x.CELL_KEYS}
        ev = passing_eval(u)
        self.assertFalse(ev["bars"]["bar1"]["pass"])
        self.assertFalse(ev["passes"])
        self.assertTrue(ev["bars"]["bar1"]["report"]["flat"]["gate"]["all"])
        self.assertFalse(ev["bars"]["bar1"]["report"]["press"]["gate"]["all"])


# --- patches ----------------------------------------------------------------------------------------------------------


class PatchTests(unittest.TestCase):
    def _fixture(self):
        fills = [SimpleNamespace(price_sol=1.0, venue="pumpswap", t_recv_ms=1000, side="buy"), SimpleNamespace(price_sol=0.5, venue="pumpswap", t_recv_ms=2000, side="sell")]
        mint = SimpleNamespace(mig_ms=x.hour_ms("2026-08-16T00") + 5)
        seen = []

        def fake_score(mint_id, m, feat, curve, through, hist, *, specs=None, size=None, priority=None, entry_land_k=None, exit_land_k=0):
            eem._fills_for(m, migrate=True)
            seen.append((entry_land_k, size, exit_land_k))
            net0 = (size or 500_000_000) // 10 - 7
            pri = priority or 500_000
            flat = eem.mixed_net(net0, 2, 1, pri, FLAT_FAIL)
            press = eem.mixed_net(net0, 2, 1, pri, 0.25)
            feats = {n: float(i) for i, n in enumerate(fz.FROZEN_FEATURE_NAMES)}
            feats["same_slot_buys"] = 99.0
            return [
                {"mint": mint_id, "spec": fz.TARGET_SPEC_ID, "day": "2026-08-16", "status": 1, "filled": True, "gross": 5, "flat": flat, "press": press, "features": feats},
                {"mint": mint_id, "spec": "trail_30_act20", "day": "2026-08-16", "status": 1, "filled": True, "gross": 5, "flat": flat, "press": press, "features": feats},
            ]

        return fills, mint, seen, fake_score

    def test_v_patch_emits_one_record_per_migration_with_every_cell_and_restores(self):
        fills, mint, seen, fake_score = self._fixture()
        saved = (eem.score_one, eem._fills_for, eem.mixed_net)
        try:
            eem.score_one, eem._fills_for = fake_score, lambda m, migrate=True: (fills, 0, 0, None)
            before = (eem.score_one, eem._fills_for, eem.mixed_net)
            with mock.patch.object(eem, "_state_index", lambda f, t, b: 0), mock.patch.object(eem, "_slot_time", lambda f, t, fb: 1000):
                with x.e15_v_patch():
                    rows = eem.score_one("m1", mint, object(), None, 0, {})
                    rows2 = eem.score_one("m2", mint, object(), None, 0, {})
            self.assertEqual((eem.score_one, eem._fills_for, eem.mixed_net), before)
        finally:
            eem.score_one, eem._fills_for, eem.mixed_net = saved
        self.assertEqual(len(rows), 1)  # no selection: every migration is simulated
        self.assertEqual(len(rows2), 1)
        r = rows[0]
        self.assertEqual(r["mig_ms"], mint.mig_ms)
        self.assertEqual(r["features"], [float(i) for i, _ in enumerate(fz.FROZEN_FEATURE_NAMES)])  # frozen 18 only, no lookahead feature
        self.assertEqual(len(r["features"]), 18)
        self.assertEqual({(c["k"], c["lag"]) for c in r["cells"]}, set(x.CELL_KEYS))
        self.assertTrue(all(c["size"] == op.size_lamports(0.05) for c in r["cells"]))
        self.assertIn((6, op.size_lamports(0.05), 2), seen)
        self.assertIn((None, None, 0), seen)  # the frozen k=1 default call gave the features
        json.dumps(r)  # streams to disk as JSON

    def test_nv_patch_keeps_the_frozen_row_only_and_restores(self):
        fills, mint, seen, fake_score = self._fixture()
        saved = (eem.score_one, eem._fills_for)
        try:
            eem.score_one, eem._fills_for = fake_score, lambda m, migrate=True: (fills, 0, 0, None)
            with x.e15_nv_patch():
                rows = eem.score_one("m1", mint, object(), None, 0, {})
            self.assertIs(eem.score_one, fake_score)
        finally:
            eem.score_one, eem._fills_for = saved
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {"mint", "spec", "day", "mig_ms", "status", "filled", "press"})
        self.assertEqual(rows[0]["spec"], fz.TARGET_SPEC_ID)
        self.assertEqual(seen, [(None, None, 0)])  # the frozen execution: defaults

    def test_pass_env_modes(self):
        from tools import pumpswap_virtual_adapter as ad

        with tempfile.TemporaryDirectory() as d, mock.patch.object(x, "check_vmap"):
            x.set_pass_env("/m.json", Path(d), "nv")
            self.assertEqual((os.environ[ad.ENV_FROZEN], os.environ[x.ENV_MODE], os.environ[ad.ENV_MAP]), ("1", "nv", "/m.json"))
            x.set_pass_env("/v.json", Path(d), "v")
            self.assertEqual((os.environ[ad.ENV_FROZEN], os.environ[x.ENV_MODE], os.environ[ad.ENV_MAP]), ("0", "v", "/v.json"))


# --- full screen on a tiny fixture ---------------------------------------------------------------------------------


class ScreenTests(unittest.TestCase):
    def test_run_screen_end_to_end_inline(self):
        u = mk_universe(False, n_good=3, n_bad=3, seed=11)
        arrays = x.fit_arrays(u, x.labels(u), x.pool_dates(False))
        r = x.Runner(arrays, None, 1)
        done = []
        out = x.run_screen(u, [False] * len(u), r, False, on_config_done=lambda c, res: done.append(c), configs=("c2", "c3"))
        self.assertEqual(done, ["c2", "c3"])
        self.assertEqual(out["statuses"], {"c2": "completed", "c3": "completed"})
        res = out["results"]["c2"]
        self.assertEqual(set(res["bars"]), {f"bar{i}" for i in range(1, 7)})
        self.assertEqual(res["n_rows"], len(u))
        self.assertEqual(len(res["folds"]), 30)
        self.assertEqual(res["fast_only"]["line"], x.FAST_ONLY_LINE)
        self.assertEqual(res["fast_only"]["dates"], ["2026-09-19", "2026-09-20", "2026-09-21"])
        for key in ("k4", "k8", "lag0", "raw_unhaircut"):
            self.assertIn(key, res["report_only"]["variants"])
        self.assertEqual(set(res["report_only"]["percentile_thresholds"]), {"p80", "p95"})
        self.assertEqual(res["min_data_in_leaf"], 20)
        self.assertEqual(out["results"]["c3"]["min_data_in_leaf"], 75)
        self.assertEqual(res["label_counts"]["n"], len(u))
        json.dumps(res, default=str)

    def test_no_v_selected_trade_refuses_the_config(self):
        u = mk_universe(False, n_good=3, n_bad=3, seed=11)
        arrays = x.fit_arrays(u, x.labels(u), x.pool_dates(False))
        r = x.Runner(arrays, None, 1)
        with self.assertRaises(x.NoVInUniverse):  # defensive: the universe already excludes unpriceable mints
            x.run_screen(u, [False] * len(u), r, False, no_v_mints={v["mint"] for v in u}, configs=("c2",))

    def test_frozen_selection_uses_the_frozen_threshold(self):
        u = mk_universe(False, n_good=1, n_bad=1)
        sel = x.frozen_selection(u, scorer=lambda m: [0.9 if r[0] > 0.5 else 0.1 for r in m])
        self.assertEqual(sel, [v["good"] for v in u])
        sel = x.frozen_selection(u, scorer=lambda m: [0.8030766588450794] * len(m))
        self.assertTrue(all(sel))  # score >= threshold enters
        sel = x.frozen_selection(u, scorer=lambda m: [0.8030766588450793] * len(m))
        self.assertFalse(any(sel))


# --- outcome, report --------------------------------------------------------------------------------------------------


def fake_result(passes, mean=0.01, k4=(0.01, 0.01), k8=(0.01, 0.01)):
    def v(m):
        return {"all": {"flat": {"mean_sol": m[0]}, "press": {"mean_sol": m[1]}}}

    return {"passes": passes, "pooled_press_mean_non_p1_sol": mean, "report_only": {"variants": {"k4": v(k4), "k8": v(k8)}}}


class OutcomeTests(unittest.TestCase):
    def test_none_pass_closes_the_family(self):
        d = x.decide_outcome({c: fake_result(False) for c in x.CONFIGS}, {c: "completed" for c in x.CONFIGS})
        self.assertTrue(d["family_closed"])
        self.assertIsNone(d["selected_for_confirmation"])
        self.assertIn("The family is closed", d["outcome"])
        self.assertIn("fresh-0808 goes back to 'reserved'", d["outcome"])
        self.assertIn("EXP-015 failed its screen", d["outcome"])
        self.assertIn("no fourth configuration", d["outcome"])

    def test_largest_pooled_pressure_mean_wins_with_no_discretion(self):
        res = {"c1": fake_result(True, 0.002), "c2": fake_result(True, 0.004), "c3": fake_result(False, 0.9)}
        d = x.decide_outcome(res, {c: "completed" for c in x.CONFIGS})
        self.assertEqual((d["passing"], d["selected_for_confirmation"]), (["c1", "c2"], "c2"))
        self.assertIn("C2", d["outcome"])
        self.assertFalse(d["family_closed"])
        res["c1"]["pooled_press_mean_non_p1_sol"] = 0.004  # a tie goes to the earlier config
        self.assertEqual(x.decide_outcome(res, {c: "completed" for c in x.CONFIGS})["selected_for_confirmation"], "c1")

    def test_knife_edge_needs_a_k6_pass_and_both_neighbours_negative(self):
        self.assertTrue(x.knife_edge(fake_result(True, k4=(-0.01, 0.01), k8=(0.01, -0.01))))
        self.assertFalse(x.knife_edge(fake_result(True, k4=(0.01, 0.01), k8=(-0.01, -0.01))))
        self.assertFalse(x.knife_edge(fake_result(False, k4=(-1, -1), k8=(-1, -1))))
        res = {"c1": dict(fake_result(True, k4=(-1, -1), k8=(-1, -1)))}
        d = x.decide_outcome(res, {c: "completed" for c in x.CONFIGS})
        self.assertTrue(d["knife_edge"])

    def test_an_aborted_or_refused_config_is_not_a_closed_family(self):
        st = {"c1": "completed", "c2": "refused_after_read", "c3": "aborted_after_read"}
        d = x.decide_outcome({"c1": fake_result(False)}, st)
        self.assertFalse(d["family_closed"])
        self.assertEqual(d["outcome"], x.OUTCOME_INCOMPLETE)

    def test_first_line_names_the_pool(self):
        self.assertIn("36-UTC-date pool", x.first_line(True))
        self.assertIn("27 non-P1", x.first_line(True))
        f = x.first_line(False)
        self.assertIn("SMALLER 30-UTC-date pool", f)
        self.assertIn("P4", f)
        self.assertIn("21 non-P1", f)

    def test_report_json_and_md_carry_the_matched_outcome_and_the_fast_only_line(self):
        u = mk_universe(False, n_good=3, n_bad=3, seed=11)
        arrays = x.fit_arrays(u, x.labels(u), x.pool_dates(False))
        screen = x.run_screen(u, [False] * len(u), x.Runner(arrays, None, 1), False, configs=("c2",))
        base = {"schema": x.SCHEMA, "banner": x.BANNER, "first_line": x.first_line(False), "universe": {"n": len(u), "sha256": "ab", "stats": {"by_source": {}, "dropped_outside_window": 0, "dropped_no_primary_cell": 0, "dropped_censored_primary": 0, "c1_missing_label_0": 0}},
                "costs": {"vmap_0909_pinned": x.VMAP_0909_SHA256}, "caveats": list(x.CAVEATS)}
        rep = x.make_report(base, screen, False)
        self.assertEqual(rep["matched_outcome"], rep["decision"]["outcome"])
        self.assertEqual(rep["fast_only_required_line"], x.FAST_ONLY_LINE)
        md = x.render_md(rep)
        self.assertTrue(md.splitlines()[0].startswith("EXP-015 screen on the SMALLER 30-UTC-date pool"))
        self.assertIn(x.FAST_ONLY_LINE, md)
        self.assertIn(rep["decision"]["outcome"], md)
        self.assertIn("haircut 0.0042038", md)
        with tempfile.TemporaryDirectory() as d:
            x.write_report(Path(d), rep)
            self.assertEqual(json.loads((Path(d) / "report.json").read_text())["matched_outcome"], rep["matched_outcome"])
            self.assertIn(x.FAST_ONLY_LINE, (Path(d) / "report.md").read_text())


# --- tries --------------------------------------------------------------------------------------------------------------


def lines(log):
    return [json.loads(x_) for x_ in Path(log).read_text().splitlines()] if Path(log).is_file() else []


class TriesTests(unittest.TestCase):
    def test_started_lines_once_per_config_on_a_bookkeeping_block(self):
        with tempfile.TemporaryDirectory() as d:
            out, log = Path(d) / "out", Path(d) / "t.jsonl"
            self.assertEqual(x.log_tries(out, log, "m", list(x.CONFIGS), "started", True, "abc"), 3)
            self.assertEqual(x.log_tries(out, log, "m", list(x.CONFIGS), "started", True, "abc"), 0)  # idempotent
            ls = lines(log)
            self.assertEqual([l["config"]["key"] for l in ls], ["exp015_c1", "exp015_c2", "exp015_c3"])
            self.assertTrue(all(l["config"]["status"] == "started" and l["config"]["universe_sha256"] == "abc" for l in ls))
            self.assertEqual(len({l["data_key"] for l in ls}), 1)
            self.assertTrue(all(l["role"] == "exploration" and l["tool"] == x.TOOL for l in ls))

    def test_completed_lines_count_on_every_pool_the_config_touches(self):
        with tempfile.TemporaryDirectory() as d:
            out, log = Path(d) / "out", Path(d) / "t.jsonl"
            self.assertEqual(x.log_tries(out, log, "m", ["c1"], "completed", True), 4)  # P1, P2, P3, P4
            self.assertEqual(x.log_tries(out, log, "m", ["c1"], "completed", True), 0)
            groups = [l["config"]["pool_group"] for l in lines(log)]
            self.assertEqual(groups, ["P1", "P2", "P3", "P4"])
            self.assertEqual(len({l["data_key"] for l in lines(log)}), 4)
            self.assertEqual(x.log_tries(Path(d) / "o2", Path(d) / "t2.jsonl", "m", ["c1"], "completed", False), 3)  # no P4
            # the P2 key equals the back-check's, so cumulative counts on explore-0814 add up
            self.assertEqual(lines(log)[1]["data_key"], __import__("tools.mal_result", fromlist=["x"]).data_key(bc.pool_blocks()))

    def test_statuses_and_the_completed_guard(self):
        with tempfile.TemporaryDirectory() as d:
            out, log = Path(d) / "out", Path(d) / "t.jsonl"
            x.log_tries(out, log, "m", ["c1"], "completed", False)
            self.assertEqual(x.log_tries(out, log, "m", ["c1", "c2", "c3"], "aborted_after_read", False), 6)  # c1 already completed: no aborted line
            self.assertEqual(x.log_tries(out, log, "m", ["c2"], "refused_after_read", False), 3)  # refused is its own status
            by = {}
            for l in lines(log):
                by.setdefault(l["config"]["key"], set()).add(l["config"]["status"])
            self.assertEqual(by, {"exp015_c1": {"completed"}, "exp015_c2": {"aborted_after_read", "refused_after_read"}, "exp015_c3": {"aborted_after_read"}})
            self.assertEqual(x.log_tries(out, log, "m", ["c1", "c2", "c3"], "aborted_after_read", False), 0)  # idempotent

    def test_both_logs_and_same_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            a, b = Path(d) / "a.jsonl", Path(d) / "b.jsonl"
            r = x.log_all(out, a, b, ["c1"], "completed", False)
            self.assertEqual((r["logged"], r["canonical_logged"]), (3, 3))
            r = x.log_all(out, a, b, ["c1"], "completed", False)
            self.assertEqual((r["logged"], r["canonical_logged"]), (0, 0))
            c = Path(d) / "c.jsonl"
            r = x.log_all(Path(d) / "o2", c, c, ["c1"], "completed", False)
            self.assertNotIn("canonical_logged", r)
            self.assertEqual(len(lines(c)), 3)


# --- main orchestration --------------------------------------------------------------------------------------------------

CLEAN = {"head": "h" * 40, "dirty_tools": False}
RES0 = {"passes": False, "bars": {}, "pooled_press_mean_non_p1_sol": None, "n_selected": 0, "n_rows": 0, "final_threshold_p90": None, "folds": [], "report_only": {}, "fast_only": {}, "knife_edge": False, "name": "C", "desc": "", "min_data_in_leaf": 20, "label_counts": {}}


class MainTests(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(x, "git_state", return_value=dict(CLEAN)), mock.patch.object(x, "load_pinned_vmap", return_value={})):
            p.start()
            self.addCleanup(p.stop)

    def _args(self, d, extra=()):
        return ["--p1-fast-dir", "/x/a", "--p1-oracle-insample-dir", "/x/b", "--p1-oracle-live-dir", "/x/c", "--p2-view-dir", "/x/p2", "--vmap-p3", "/x/v3",
                "--out-dir", str(Path(d) / "out"), "--tries-log", str(Path(d) / "t.jsonl"), "--canonical-tries", str(Path(d) / "canon.jsonl"), "--max-workers", "1", *extra]

    def _synthetic_rows(self, n_good=2, n_bad=2):
        u = mk_universe(False, n_good=n_good, n_bad=n_bad, seed=2)
        v_rows, nv_rows = {s: [] for s in x.SOURCES}, {s: [] for s in x.SOURCES}
        for r in u:
            v_rows[r["source"]].append({"mint": r["mint"], "mig_ms": r["mig_ms"], "features": r["features"], "cells": list(r["cells"].values())})
            nv_rows[r["source"]].append({"mint": r["mint"], "press": 1.0 if r["good"] else -1.0})
        for s in list(v_rows):
            if s == "P4":
                v_rows.pop(s), nv_rows.pop(s)
        return u, v_rows, nv_rows

    def _guards(self):
        return {"g1": {"roots": {"fast": Path("/x/a"), "insample": Path("/x/b"), "live": Path("/x/c")}, "view_sha256": {}}, "g2": {"roots": {}, "pool": bc.hours_range(*x.P2_SEALED), "view_sha256": {}}, "g3": {"walkers": x.p3_walkers("/nonexistent-p3"), "pin_sha256": {}},
                "g4": None, "vmap_sha256": {}, "frozen": {"model_md5": x.MODEL_MD5, "threshold": x.FROZEN_THRESHOLD}, "with_p4": False}

    def _patches(self, collect, frozen=None, screen=None, n=None):
        ps = [mock.patch.object(x, "run_guards", return_value=self._guards()), mock.patch.object(x, "vprepass_all", return_value=({}, {})), mock.patch.object(x, "collect_all", **collect),
              mock.patch.object(x, "render_md", return_value="md")]
        if frozen is not None:
            ps.append(mock.patch.object(x, "frozen_selection", return_value=frozen))
        if screen is not None:
            ps.append(mock.patch.object(x, "run_screen", screen))
        return ps

    def _run(self, d, collect, extra=(), **kw):
        import contextlib

        with contextlib.ExitStack() as st:
            for p in self._patches(collect, **kw):
                st.enter_context(p)
            return x.main(self._args(d, extra))

    def test_guard_refusal_exits_2_and_logs_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(x.main(self._args(d)), 2)  # the paths do not exist
            self.assertEqual(x.main(self._args(d, ("--max-workers", "9"))), 2)
            self.assertFalse((Path(d) / "t.jsonl").exists())
            self.assertFalse((Path(d) / "canon.jsonl").exists())

    def test_prior_exp015_line_refuses_before_any_read(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "t.jsonl").write_text(json.dumps({"config": {"key": "exp015_c1"}}) + "\n")
            with mock.patch.object(x, "run_guards", return_value=self._guards()), mock.patch.object(x, "vprepass_all") as pp:
                self.assertEqual(x.main(self._args(d)), 2)
            pp.assert_not_called()

    def test_guards_only(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(x, "run_guards", return_value=self._guards()), mock.patch.object(x, "vprepass_all", return_value=({}, {})) as pp:
                self.assertEqual(x.main(self._args(d, ("--guards-only",))), 0)
            self.assertEqual(pp.call_args.kwargs["only"], ("P1A", "P1C", "P1B", "P3"))  # pool-field coverage of P1 and P3 for the manager
            self.assertFalse((Path(d) / "t.jsonl").exists())

    def test_dirty_tools_refuses_before_any_read(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(x, "run_guards", return_value=self._guards()), mock.patch.object(x, "vprepass_all") as pp, \
                    mock.patch.object(x, "git_state", return_value={"head": "h", "dirty_tools": True}):
                self.assertEqual(x.main(self._args(d)), 2)
            pp.assert_not_called()
            self.assertFalse((Path(d) / "t.jsonl").exists())

    def test_v_coverage_refusal_before_reading_logs_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(x, "run_guards", return_value=self._guards()), mock.patch.object(x, "vprepass_all", side_effect=x.Refused("P2: V coverage 2%")), mock.patch.object(x, "collect_all") as ca:
                self.assertEqual(x.main(self._args(d)), 2)
            ca.assert_not_called()
            self.assertFalse((Path(d) / "t.jsonl").exists())

    def test_sigterm_before_started_leaves_no_tries_line_no_lock_and_restores_the_handler(self):
        before = signal.getsignal(signal.SIGTERM)

        def cancel(*a, **k):
            os.kill(os.getpid(), signal.SIGTERM)
            raise AssertionError("handler should have unwound")

        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as cm:
                self._run(d, {"side_effect": cancel})
            self.assertEqual(cm.exception.code, 128 + signal.SIGTERM)
            self.assertFalse((Path(d) / "t.jsonl").exists())  # a crash before `started` spends no try
            self.assertFalse((Path(d) / "canon.jsonl").exists())
            self.assertFalse((Path(d) / "out" / "RUN.lock").exists())
            self.assertFalse((Path(d) / "out" / "report.json").exists())
            self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    def test_any_exception_before_started_logs_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                self._run(d, {"side_effect": RuntimeError("tape crashed")})
            self.assertFalse((Path(d) / "t.jsonl").exists())
            self.assertFalse((Path(d) / "out" / "RUN.lock").exists())

    def test_integrity_failure_before_started_logs_nothing(self):
        u, v_rows, nv_rows = self._synthetic_rows()
        v_rows["P2"].append(dict(v_rows["P3"][0]))  # a mint in two sources
        v_rows["P2"][-1]["mig_ms"] = x.hour_ms("2026-08-20T00")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run(d, {"return_value": (v_rows, nv_rows, set())}), 2)
            self.assertFalse((Path(d) / "t.jsonl").exists())
            self.assertFalse((Path(d) / "out" / "RUN.lock").exists())

    def test_frozen_selected_non_p1_mint_on_a_no_v_pool_refuses_before_started(self):
        u, v_rows, nv_rows = self._synthetic_rows()
        target = next(v["mint"] for v in u if v["block"] != "P1")
        sel = [v["mint"] == target for v in u]
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(x, "no_v_mints_from", return_value={target}), mock.patch.object(x, "check_no_v_consistency"):
                self.assertEqual(self._run(d, {"return_value": (v_rows, nv_rows, {"poolX"})}, frozen=sel), 2)
            self.assertFalse((Path(d) / "t.jsonl").exists())
            self.assertFalse((Path(d) / "out" / "RUN.lock").exists())
            # a frozen-selected P1 mint on a no-V pool does not trip the pre-started check
            p1 = next(v["mint"] for v in u if v["block"] == "P1")
            sel1 = [v["mint"] == p1 for v in u]

            def fake_screen(universe, frozen_sel, runner, with_p4, on_done, on_refused, no_v, **kw):
                for c in x.CONFIGS:
                    on_done(c, dict(RES0))
                return {"results": {c: dict(RES0) for c in x.CONFIGS}, "statuses": {c: "completed" for c in x.CONFIGS}}

            with mock.patch.object(x, "no_v_mints_from", return_value={p1}), mock.patch.object(x, "check_no_v_consistency"):
                self.assertEqual(self._run(d, {"return_value": (v_rows, nv_rows, {"poolX"})}, frozen=sel1, screen=fake_screen), 0)

    def test_run_screen_refuses_a_config_whose_frozen_non_p1_selection_is_on_a_no_v_pool(self):
        u = mk_universe(False, n_good=3, n_bad=3, seed=11)
        arrays = x.fit_arrays(u, x.labels(u), x.pool_dates(False))
        target = next(v["mint"] for v in u if v["block"] == "P2")
        frozen = [v["mint"] == target for v in u]
        # the new model selects nothing on that mint (it is not "good" informatively), so only the frozen selection can trip the check
        with self.assertRaises(x.NoVInUniverse):
            x.run_screen(u, frozen, x.Runner(arrays, None, 1), False, no_v_mints={target}, configs=("c2",))
        out = x.run_screen(u, [v["mint"] == next(w["mint"] for w in u if w["block"] == "P1") for v in u], x.Runner(arrays, None, 1), False, no_v_mints={target}, configs=("c2",))
        self.assertIn(out["statuses"]["c2"], ("completed",))  # a P1 frozen pick alone never trips it

    def test_started_lines_lock_and_the_universe_sha_precede_any_fit_then_completed_and_aborted_statuses(self):
        u, v_rows, nv_rows = self._synthetic_rows()
        order = {}

        def fake_screen(universe, frozen_sel, runner, with_p4, on_done, on_refused, no_v, **kw):
            ls = lines(Path(self.d) / "t.jsonl")
            order["started_at_first_fit"] = [l["config"]["status"] for l in ls]
            order["sha_at_first_fit"] = {l["config"]["universe_sha256"] for l in ls}
            order["universe_file"] = (Path(self.d) / "out" / "universe.jsonl").read_bytes()
            order["sha_file"] = (Path(self.d) / "out" / "universe.sha256").read_text().strip()
            order["lock"] = json.loads((Path(self.d) / "out" / "RUN.lock").read_text())
            on_done("c1", dict(RES0))
            raise RuntimeError("fit crashed")

        with tempfile.TemporaryDirectory() as d:
            self.d = d
            with self.assertRaises(RuntimeError):
                self._run(d, {"return_value": (v_rows, nv_rows, set())}, frozen=[False] * len(u), screen=fake_screen)
            self.assertEqual(order["started_at_first_fit"], ["started"] * 3)
            sha = hashlib.sha256(order["universe_file"]).hexdigest()
            self.assertEqual(order["sha_file"], sha)
            self.assertEqual(order["sha_at_first_fit"], {sha})
            self.assertEqual(order["lock"]["head"], CLEAN["head"])
            self.assertEqual(len(order["lock"]["args_hash"]), 64)
            ls = lines(Path(d) / "t.jsonl")
            st = {}
            for l in ls:
                st.setdefault(l["config"]["key"], []).append(l["config"]["status"])
            self.assertEqual(st["exp015_c1"], ["started", "completed", "completed", "completed"])  # P1, P2, P3
            self.assertEqual(st["exp015_c2"], ["started", "aborted_after_read", "aborted_after_read", "aborted_after_read"])
            self.assertEqual(st["exp015_c3"], ["started", "aborted_after_read", "aborted_after_read", "aborted_after_read"])
            rep = json.loads((Path(d) / "out" / "report.json").read_text())
            self.assertTrue(rep["partial"])
            self.assertEqual(rep["universe"]["sha256"], sha)
            rec = json.loads((Path(d) / "out" / "RUN.record.json").read_text())
            self.assertEqual((rec["status"], rec["started"]), ("aborted_after_read", True))
            # after `started` there is no resume: the prior-tries check and the lock both refuse a second run
            self.assertEqual(self._run(d, {"return_value": (v_rows, nv_rows, set())}), 2)

    def test_run_lock_without_a_record_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            out.mkdir()
            (out / "RUN.lock").write_text("{}")
            with mock.patch.object(x, "run_guards", return_value=self._guards()), mock.patch.object(x, "vprepass_all") as pp:
                self.assertEqual(x.main(self._args(d)), 2)
            pp.assert_not_called()
            with self.assertRaises(x.Refused):
                x.check_run_lock(out)
            (out / "RUN.record.json").write_text(json.dumps({"status": "aborted_after_read", "started": True}))
            with self.assertRaises(x.Refused):  # a record, but the tries were spent
                x.check_run_lock(out)
            (out / "RUN.record.json").write_text(json.dumps({"status": "aborted_after_read", "started": False}))
            x.check_run_lock(out)  # stale lock from a run that never reached `started`: cleared
            self.assertFalse((out / "RUN.lock").exists())

    def test_lock_is_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            x.take_lock(Path(d), "h", "a")
            with self.assertRaises(FileExistsError):
                x.take_lock(Path(d), "h", "a")

    def test_a_completed_run_writes_report_and_completed_lines_once(self):
        u, v_rows, nv_rows = self._synthetic_rows()

        def fake_screen(universe, frozen_sel, runner, with_p4, on_done, on_refused, no_v, **kw):
            for c in x.CONFIGS:
                on_done(c, dict(RES0))
            return {"results": {c: dict(RES0) for c in x.CONFIGS}, "statuses": {c: "completed" for c in x.CONFIGS}}

        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run(d, {"return_value": (v_rows, nv_rows, set())}, frozen=[False] * len(u), screen=fake_screen), 0)
            rep = json.loads((Path(d) / "out" / "report.json").read_text())
            self.assertFalse(rep["partial"])
            self.assertTrue(rep["decision"]["family_closed"])
            self.assertEqual(rep["matched_outcome"], x.OUTCOME_NONE)
            ls = lines(Path(d) / "t.jsonl")
            self.assertEqual(sum(1 for l in ls if l["config"]["status"] == "completed"), 9)
            self.assertEqual(sum(1 for l in ls if l["config"]["status"] == "started"), 3)
            self.assertEqual(sum(1 for l in ls if l["config"]["status"] == "aborted_after_read"), 0)
            self.assertEqual(json.loads((Path(d) / "out" / "RUN.record.json").read_text())["status"], "completed")


# the 30 pools without V in the EXP-012 back-check (job #207); a fixture copy of /data/mal/ops/no-v-pools-explore0814.json
NO_V_POOLS_207 = [
    "21qFBBA8Tbs7FhfkSKEjt7irkgrringbjw2fJpV2fYDh",
    "2caYbuiD7KFVxCiS6nPE2m45bMnap6yqzD56CRYqkdtH",
    "2eY8jRBDgND9aJMMTBXU2UfgWbuzp9CxH2hp2wTuyHEw",
    "2vgwVFz2VcsaePd2NWVGcGXo4w3dpShpTyeZUtxZP1Cu",
    "3mSUEhHYQYktGukCNpVKh4zYtNL8QKVa11pRDQHBZR29",
    "3v2UtryCPcev11qECcvCsvMJebSYHM1Y7dt3diiZaJ2c",
    "4FuQra9YqDSo2VdneHLd5u1iPvb4yG1jxCazKAu2EM48",
    "4QVieu3SZb8ZoyxfRsjAqKAhiffpmeN1T6zEZxdGvxJU",
    "4Zg9xj7fbWwy5WHDchLCC1uzmei1fLFyw6QqgzkaFoGi",
    "4zgAcGAi9jFVrvAMY6jgN4n4ZeacPxJzfPs3WqUwTx2R",
    "5GroaHN8mzCPqvbJvoBjzC8gq2cs8r8B6FNnpGvspAw7",
    "5fdWCJ3yvM8Wii37qeCSazz5FfXus7pJrL7F3sngwwE1",
    "6Dvu141DcEqA1jHWLTwTXjQgTF29vDiZ8kLFCVYLkoTs",
    "6XNc6T6mDq5HanKewaYfMQsSHXa46saoGWt2pNRMfBT",
    "6hcMXyPttkSRcuVgeKhammvCeGkFLa7SyrC7G1A4HHtv",
    "79pEfSNB8ahVKn3K64hb3keiMwbD5o8AiZpwTpTEheqs",
    "7BCX3GNQ75powrXMdG5EuRh6ginjf9y7KXDcKibyBcoE",
    "7WQAs8wAj2JjbVCgrz88MLuKVggMBgzTHaUD7QjbsLPa",
    "8kRTkps2m5kYqMGisnxroSkL4Tphv4MKHPmzH9jUkg6e",
    "9hNv1xVrXSdRGUCeUka1mAq9ie7ExGqMrao6PKzsYpUz",
    "9vyccZKMXA1RaczznHgik2wVg5D2DMvM4TBn6RaNd1C7",
    "CA87zn6iFi5REuBdGLMA9AQBTLYrGLXEV6FYwa6yPJMk",
    "D738H6zvgAcVcxPJRayTD6k4jvEwQ5XwyS4ALFP6sP5o",
    "DM91KnzKcgqG9FPXBZTjF55oK5CABN5sktJwaSbQiEzV",
    "DT358iZkMY526gHCci5zeNVWfLE3nWWB3489nmRhN3Mc",
    "ERw3w5rsKw7Rkeontwqmtnc4LB9g3CD1zPN2BFfDYZtP",
    "GUh5ha93c7ycqH2uxPqS4Y7PbEgiFKN6V184STXz51p9",
    "H15x9qMhQ6rDMChZiA6kKd27XsA9Qisdehs1vh8ZPPiw",
    "HN1cRZtCfijmVfVUsJKkPFHE3hT3YBYCyHM2Na4Spzgf",
    "HkkGHUs4hMdDb9bR15YiXpvSVPT4aa7F1LT32Tv9gQnv",]


class UnpriceablePoolTests(unittest.TestCase):
    """Post-pin item 11: one pinned map, and the pool-based removal rule decided before `started`."""

    def test_the_pin_is_pending_so_the_tool_refuses_at_startup(self):
        with self.assertRaises(x.Refused):
            x.check_pin_ready()
        with self.assertRaises(x.Refused):
            x.run_guards(SimpleNamespace(max_workers=4))  # refuses before anything else
        with mock.patch.object(x, "VMAP_0909_SHA256", "ab" * 32):
            x.check_pin_ready()
        with mock.patch.object(x, "VMAP_0909_SHA256", "PENDING_JOB_224"), self.assertRaises(x.Refused):
            x.check_pin_ready()

    def test_every_vmap_arg_defaults_to_pool_v_0909_and_is_asserted_against_the_pin(self):
        a = x._parser().parse_args(["--p1-fast-dir", "a", "--p1-oracle-insample-dir", "b", "--p1-oracle-live-dir", "c", "--out-dir", "o"])
        self.assertEqual({a.vmap_p1, a.vmap_p2, a.vmap_p3, a.vmap_p4}, {"/data/mal/pumpswap-virtual/pool_v_0909.json"})
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "pool_v_0909.json"
            m.write_text("{}")
            good = hashlib.sha256(b"{}").hexdigest()
            names = ("P1", "P2", "P3", "P4")
            roots = [Path(d) / n for n in ("fast-pool-2026-09-18T23_2026-09-22T00", "oracle-insample-2026-09-22_25", "oracle-live-2026-09-25_27")]
            for r in roots:
                r.mkdir()
                (r / "VIEW.sha256").write_text("x\n")
            ns = dict(max_workers=4, p1_fast_dir=roots[0], p1_oracle_insample_dir=roots[1], p1_oracle_live_dir=roots[2], p2_view_dir=[Path(d) / "v"], p3_root=Path(d), p4_view_dir=None, artifact_dir=x.DEFAULT_ARTIFACT_DIR)
            g2 = {"roots": {}, "pool": [], "view_sha256": {}}
            g3 = {"walkers": [], "pin_sha256": {}}
            for bad_one in names[:3]:  # a map that does not hash to the pin is refused for each block
                vm = {f"vmap_{n.lower()}": str(m) for n in names}
                other = Path(d) / "other.json"
                other.write_text("{ }")
                vm[f"vmap_{bad_one.lower()}"] = str(other)
                with mock.patch.object(x, "VMAP_0909_SHA256", good), mock.patch.object(x, "guard_p1", return_value={"roots": {}, "view_sha256": {}}), mock.patch.object(x, "guard_p2", return_value=g2), \
                        mock.patch.object(x, "guard_p3", return_value=g3), mock.patch.object(x, "guard_p4", return_value=None):
                    with self.assertRaises(x.Refused, msg=bad_one):
                        x.run_guards(SimpleNamespace(**ns, **vm), verify=False)
            vm = {f"vmap_{n.lower()}": str(m) for n in names}
            with mock.patch.object(x, "VMAP_0909_SHA256", good), mock.patch.object(x, "guard_p1", return_value={"roots": {}, "view_sha256": {}}), mock.patch.object(x, "guard_p2", return_value=g2), \
                    mock.patch.object(x, "guard_p3", return_value=g3), mock.patch.object(x, "guard_p4", return_value=None), mock.patch.object(x, "check_frozen_model", return_value={}):
                g = x.run_guards(SimpleNamespace(**ns, **vm), verify=False)
            self.assertEqual(g["vmap_sha256"], {"P1": good, "P2": good, "P3": good})

    def test_unpriceable_is_pool_based_null_or_absent_not_zero(self):
        vmap = {"pa": 17_000_000_000, "pz": 0, "pn": None}
        mp_ = {"ok": {"pa"}, "zero": {"pz"}, "null": {"pn"}, "absent": {"pq"}, "mixed": {"pa", "pq"}, "nopool": set()}
        self.assertEqual(x.unpriceable_mints(mp_, vmap), {"null", "absent", "mixed"})

    def test_all_30_back_check_pools_are_unpriceable_when_null_or_absent(self):
        self.assertEqual(len(NO_V_POOLS_207), 30)
        self.assertEqual(len(set(NO_V_POOLS_207)), 30)
        vmap = {"p-ok": 1}
        vmap[NO_V_POOLS_207[0]] = None  # the closed account: present with null
        mp_ = {f"mint{i}": {p} for i, p in enumerate(NO_V_POOLS_207)}
        mp_["fine"] = {"p-ok"}
        self.assertEqual(x.unpriceable_mints(mp_, vmap), {f"mint{i}" for i in range(30)})  # 29 absent + 1 null
        vmap0909 = {**vmap, **{p: 5 for p in NO_V_POOLS_207}}  # pool_v_0909 prices them
        self.assertEqual(x.unpriceable_mints(mp_, vmap0909), set())

    def test_removal_keeps_the_rest_and_records_count_fraction_and_ids(self):
        u = mk_universe(False, n_good=5, n_bad=5)
        gone = {u[0]["mint"]}
        kept, rec, rows = x.remove_unpriceable(u, gone)
        self.assertEqual([r["mint"] for r in rows], sorted(gone))
        self.assertEqual((len(kept), rec["count"], rec["mints"]), (len(u) - 1, 1, sorted(gone)))
        self.assertAlmostEqual(rec["fraction"], 1 / len(u))
        self.assertEqual(rec["max_fraction"], 0.005)
        self.assertNotIn(u[0]["mint"], {k["mint"] for k in kept})
        kept, rec, rows = x.remove_unpriceable(u, set())
        self.assertEqual(rows, [])
        self.assertEqual((len(kept), rec["count"]), (len(u), 0))
        # exactly 0.5% passes, one more refuses
        big = [dict(u[0], mint=f"b{i}") for i in range(200)]
        x.remove_unpriceable(big, {f"b{i}" for i in range(1)})
        with self.assertRaises(x.Refused):
            x.remove_unpriceable(big, {f"b{i}" for i in range(2)})

    def _setup_main(self, mint_pools, n_good=5, n_bad=5):
        t = MainTests()
        t.setUp()
        self.addCleanup(t.doCleanups)
        u, v_rows, nv_rows = t._synthetic_rows(n_good, n_bad)
        return t, u, v_rows, nv_rows

    def _run_main(self, t, d, u, v_rows, nv_rows, mint_pools, seen, screen=None):
        import contextlib

        vmap = {p: 1 for ps in mint_pools.values() for p in ps}
        for p in NO_V_POOLS_207[1:]:
            vmap.pop(p, None)
        vmap[NO_V_POOLS_207[0]] = None

        def frozen(universe, artifact_dir=None, scorer=None):
            if "frozen_mints" not in seen:  # the first call is the pre-`started` selection on the kept universe
                seen["frozen_mints"] = {r["mint"] for r in universe}
                seen["tries_exist_at_frozen"] = (Path(d) / "t.jsonl").exists()
                seen["lock_exists_at_frozen"] = (Path(d) / "out" / "RUN.lock").exists()
            else:
                seen["frozen_removed"] = [r["mint"] for r in universe]  # the report-only call on the removed mints, after `started`
            return [False] * len(universe)

        def fake_screen(universe, frozen_sel, runner, with_p4, on_done, on_refused, no_v, **kw):
            seen["screen_mints"] = {r["mint"] for r in universe}
            seen["frozen_len"] = len(frozen_sel)
            for c in x.CONFIGS:
                on_done(c, dict(RES0))
            return {"results": {c: dict(RES0) for c in x.CONFIGS}, "statuses": {c: "completed" for c in x.CONFIGS}}

        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(x, "run_guards", return_value=t._guards()))
            st.enter_context(mock.patch.object(x, "vprepass_all", return_value=({}, mint_pools)))
            st.enter_context(mock.patch.object(x, "collect_all", return_value=(v_rows, nv_rows, set())))
            st.enter_context(mock.patch.object(x, "load_pinned_vmap", return_value=vmap))
            st.enter_context(mock.patch.object(x, "frozen_selection", frozen))
            st.enter_context(mock.patch.object(x, "run_screen", screen or fake_screen))
            st.enter_context(mock.patch.object(x, "render_md", return_value="md"))
            return x.main(t._args(d))

    def test_mints_on_the_207_pools_are_removed_before_started_for_both_sides_and_no_tries_line_exists_then(self):
        t, u, v_rows, nv_rows = self._setup_main({}, 8, 8)
        target = u[7]["mint"]
        mint_pools = {target: {NO_V_POOLS_207[3]}, **{v["mint"]: {f"pool-{i}"} for i, v in enumerate(u) if v["mint"] != target}}
        seen = {}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run_main(t, d, u, v_rows, nv_rows, mint_pools, seen), 0)
            self.assertNotIn(target, seen["frozen_mints"])  # the frozen side
            self.assertNotIn(target, seen["screen_mints"])  # every config (one universe)
            self.assertEqual(len(seen["screen_mints"]), len(x.build_universe(v_rows, nv_rows, False)[0]) - 1)
            self.assertFalse(seen["tries_exist_at_frozen"])  # decided before `started`: no tries line, no lock yet
            self.assertFalse(seen["lock_exists_at_frozen"])
            self.assertNotIn(target.encode(), (Path(d) / "out" / "universe.jsonl").read_bytes())
            rep = json.loads((Path(d) / "out" / "report.json").read_text())
            rec = rep["universe"]["unpriceable_removed"]
            self.assertEqual((rec["count"], rec["mints"]), (1, [target]))
            self.assertEqual(rep["universe"]["n"], len(seen["screen_mints"]))
            self.assertEqual(seen["frozen_removed"], [target])
            self.assertIn("UPWARD", rep["universe"]["unpriceable_removed"]["bias_statement"])
            # the universe sha is taken after removal
            self.assertEqual(rep["universe"]["sha256"], (Path(d) / "out" / "universe.sha256").read_text().strip())

    def test_more_than_half_a_percent_removed_refuses_before_started_with_no_tries_line_or_lock(self):
        t, u, v_rows, nv_rows = self._setup_main({})
        mint_pools = {v["mint"]: {NO_V_POOLS_207[i % 30]} for i, v in enumerate(u[:30])}  # 30 mints of 300 on the 30 pools (10%)
        seen = {}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run_main(t, d, u, v_rows, nv_rows, mint_pools, seen), 2)
            self.assertNotIn("frozen_mints", seen)  # never reached the frozen side or any fit
            self.assertFalse((Path(d) / "t.jsonl").exists())
            self.assertFalse((Path(d) / "canon.jsonl").exists())
            self.assertFalse((Path(d) / "out" / "RUN.lock").exists())

    def test_the_defensive_no_v_assert_aborts_and_logs_aborted_after_read(self):
        t, u, v_rows, nv_rows = self._setup_main({})
        mint_pools = {v["mint"]: {f"pool-{i}"} for i, v in enumerate(u)}

        def boom(universe, frozen_sel, runner, with_p4, on_done, on_refused, no_v, **kw):
            raise x.NoVInUniverse("a no-V trade survived the removal")

        seen = {}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(x.NoVInUniverse):
                self._run_main(t, d, u, v_rows, nv_rows, mint_pools, seen, screen=boom)
            ls = lines(Path(d) / "t.jsonl")
            self.assertEqual({l["config"]["status"] for l in ls if l["config"]["status"] != "started"}, {"aborted_after_read"})
            self.assertEqual(sum(1 for l in ls if l["config"]["status"] == "started"), 3)


class QuantProofFixesTests(unittest.TestCase):
    def test_consistency_refusal_pure(self):
        mp_ = {"a": {"p1"}, "b": {"p2"}, "buf": {"p3"}}
        x.check_no_v_consistency({"p1"}, mp_, {"a"})
        x.check_no_v_consistency({"p3"}, mp_, {"a", "buf"})  # a pool seen only on a buffer-hour mint is explained once the scan covers the buffer
        x.check_no_v_consistency(set(), mp_, set())
        with self.assertRaises(x.Refused):
            x.check_no_v_consistency({"p1", "pZ"}, mp_, {"a"})  # pZ is on no scanned mint: the silent case
        with self.assertRaises(x.Refused):
            x.check_no_v_consistency({"p2"}, mp_, {"a"})  # p2's mint is not unpriceable per the map: the map and the adapter disagree

    def test_main_refuses_before_started_on_a_stray_adapter_no_v_pool(self):
        import contextlib

        t = MainTests()
        t.setUp()
        self.addCleanup(t.doCleanups)
        u, v_rows, nv_rows = t._synthetic_rows(8, 8)
        mint_pools = {v["mint"]: {f"pool-{i}"} for i, v in enumerate(u)}
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(x, "run_guards", return_value=t._guards()))
            st.enter_context(mock.patch.object(x, "vprepass_all", return_value=({}, mint_pools)))
            st.enter_context(mock.patch.object(x, "collect_all", return_value=(v_rows, nv_rows, {"pool-never-seen"})))
            fs = st.enter_context(mock.patch.object(x, "frozen_selection"))
            self.assertEqual(x.main(t._args(d)), 2)
            fs.assert_not_called()
            self.assertFalse((Path(d) / "t.jsonl").exists())
            self.assertFalse((Path(d) / "canon.jsonl").exists())
            self.assertFalse((Path(d) / "out" / "RUN.lock").exists())

    def test_prepass_scans_buffer_hours_and_records_every_pool(self):
        def prints():
            yield {"venue": "pumpswap", "mint": "buf", "pool": "pb", "t_recv_ms": x.hour_ms("2026-08-14T13"), "block_time": 1}  # first print in the feature-buffer day
            yield {"venue": "pumpswap", "mint": "cnt", "pool": "pc", "t_recv_ms": x.hour_ms("2026-08-16T00"), "block_time": 1}
            yield {"venue": "pumpswap", "mint": "cnt", "pool": "pc2", "t_recv_ms": x.hour_ms("2026-08-16T01"), "block_time": 1}
            yield {"venue": "pumpswap", "mint": "nopool", "t_recv_ms": x.hour_ms("2026-08-16T01"), "block_time": 1}
            yield {"venue": "pump_bonding", "mint": "bond", "pool": "pq", "t_recv_ms": 1}

        with mock.patch.object(bc, "_iter_pool_prints", return_value=prints()), mock.patch.object(x, "load_pinned_vmap", return_value={"pb": 1, "pc": 1}):
            cov, pools = x.prepass(object(), [], "m", "2026-08-15T12", "2026-08-28T12", None)
        self.assertEqual(pools, {"buf": {"pb"}, "cnt": {"pc", "pc2"}, "nopool": {None}})  # buffer mint included, bonding print ignored
        self.assertEqual(x.unpriceable_mints(pools, {"pb": 1, "pc": 1}), {"cnt", "nopool"})
        self.assertEqual(cov["n_counted_mints"], 2)  # coverage still counts only the window's mints

    def test_vprepass_merges_pools_per_mint_instead_of_overwriting(self):
        cov = {"n": 1, "prints": 1, "missing_fraction": 0.0, "covered": 1, "missing": 0, "missing_pools": 0, "max_missing_fraction": 0.01}
        results = iter([(cov, {"m": {"p1"}, "x": {"q"}}), (cov, {"m": {"p2"}})])
        g = {"g1": {"roots": {"fast": Path("/a"), "insample": Path("/b"), "live": Path("/c")}}, "g2": {"roots": {}, "pool": []}, "g3": {"walkers": []}, "g4": None}
        args = SimpleNamespace(vmap_p1="/m", vmap_p2="/m", vmap_p3="/m", vmap_p4="/m")
        fake = {"P1A": (object(), [], set()), "P1C": (object(), [], set()), "P1B": (object(), [], None)}
        with mock.patch.object(x, "prepass", side_effect=lambda *a, **k: next(results)), mock.patch.object(x, "p1_hour_resolvers", return_value=fake):
            _covs, mp_ = x.vprepass_all(g, args, only=("P1A", "P1C"))
        self.assertEqual(mp_, {"m": {"p1", "p2"}, "x": {"q"}})

    def test_load_pinned_vmap_rechecks_the_pin(self):
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "m.json"
            m.write_text("{}")
            with self.assertRaises(x.Refused):  # pending pin
                x.load_pinned_vmap(m)
            with mock.patch.object(x, "VMAP_0909_SHA256", "0" * 64), self.assertRaises(x.Refused):
                x.load_pinned_vmap(m)
            with mock.patch.object(x, "VMAP_0909_SHA256", hashlib.sha256(b"{}").hexdigest()), mock.patch("tools.pumpswap_virtual.load_map", return_value={"p": 1}):
                self.assertEqual(x.load_pinned_vmap(m), {"p": 1})
            with mock.patch.object(x, "VMAP_0909_SHA256", "0" * 64), self.assertRaises(x.Refused):
                x.set_pass_env(str(m), Path(d), "v")  # the adapter env path is re-checked too

    def test_cap_applies_to_the_non_p1_rows_and_is_reported_per_block(self):
        rows = [{"mint": f"a{i}", "block": "P1"} for i in range(900)] + [{"mint": f"b{i}", "block": "P2"} for i in range(150)]
        with self.assertRaises(x.Refused) as cm:  # 4/1050 = 0.38% overall but 1/150 = 0.67% of the non-P1 rows
            x.remove_unpriceable(rows, {"a0", "a1", "a2", "b0"})
        self.assertIn("non-P1", str(cm.exception))
        rows2 = [{"mint": f"a{i}", "block": "P1"} for i in range(900)] + [{"mint": f"b{i}", "block": "P2"} for i in range(250)]
        kept, rec, removed = x.remove_unpriceable(rows2, {"a0", "b0"})  # 0.17% overall, 0.4% non-P1
        self.assertEqual(rec["per_block"], {"P1": {"removed": 1, "rows": 900, "fraction": 1 / 900}, "P2": {"removed": 1, "rows": 250, "fraction": 0.004}})
        self.assertAlmostEqual(rec["non_p1_fraction"], 0.004)
        self.assertEqual(len(removed), 2)

    def test_removed_rows_are_never_trained_on_but_are_scored_by_their_dates_outer_fold(self):
        u = mk_universe(False, n_good=2, n_bad=2, seed=5)
        removed = [mk_urow(900 + i, "2026-09-20", True, random.Random(i)) for i in range(2)]
        dates = x.pool_dates(False)
        arrays = x.fit_arrays(u, x.labels(u), dates, removed)
        self.assertEqual(arrays["extra"].tolist(), [False] * len(u) + [True] * 2)
        x._init_fit(None, arrays)
        trained = []
        orig = x.fit_cfg

        def spy(xm, y, mdl):
            trained.append(len(xm))
            return orig(xm, y, mdl)

        d = dates.index("2026-09-20")
        with mock.patch.object(x, "fit_cfg", spy):
            out = x.task_outer({"kind": "outer", "cfg": "c2", "scope": "all", "date": d})
        self.assertLessEqual(max(trained), len(u) - len([v for v in u if v["date"] == "2026-09-20"]))  # no removed row in any training set
        self.assertEqual(out["x_idx"], [len(u), len(u) + 1])
        self.assertEqual(len(out["x_scores"]), 2)
        self.assertEqual(len(out["idx"]), len([v for v in u if v["date"] == "2026-09-20"]))  # test rows exclude the extras
        n = x.nested_oof(x.Runner(arrays, None, 1), "c2", len(u), [d])
        self.assertEqual(sorted(n["x_score"]), [0, 1])
        self.assertEqual(n["n_pooled_oof"], len(out["idx"]))  # removed mints never enter the pooled OOF

    def test_removed_bias_counts_and_recomputes_bars_at_total_loss(self):
        u = mk_universe()
        sel = sel_good(u)
        nested = nested_of(u, sel)
        transfer = transfer_of(u, sel)
        fr = [not s for s in sel]
        ev = x.evaluate_config(u, nested, transfer, fr, True)
        self.assertTrue(ev["passes"])
        rng = random.Random(9)
        removed = [mk_urow(500 + i, "2026-08-20", True, rng) for i in range(40)]
        nested["x_score"] = {j: 1.0 for j in range(40)}
        nested["x_thr"] = {j: 0.5 for j in range(40)}
        b = x.removed_bias(u, removed, nested, transfer, fr, [True] * 40, ev, True)
        self.assertEqual((b["n_removed"], b["n_removed_selected_by_config"], b["n_removed_selected_by_frozen"]), (40, 40, 40))
        self.assertEqual(b["loss_per_selected_removed_trade_lamports"], -(50_000_000 + 2 * 505_000))
        self.assertIn("UPWARD", b["statement"])
        self.assertTrue(b["passes_as_reported"])
        self.assertEqual(set(b["bars_pass_with_removed_at_total_loss"]), {f"bar{i}" for i in range(1, 7)})
        self.assertEqual(b["bars_pass_as_reported"], {f"bar{i}": True for i in range(1, 7)})  # the bars themselves are unchanged
        nested0 = dict(nested, x_score={j: 0.0 for j in range(40)})
        b0 = x.removed_bias(u, removed, nested0, transfer, fr, [False] * 40, ev, True)
        self.assertEqual((b0["n_removed_selected_by_config"], b0["n_removed_selected_by_frozen"]), (0, 0))
        self.assertTrue(b0["passes_with_removed_at_total_loss"])
        heavy = [mk_urow(700 + i, "2026-08-20", True, rng) for i in range(400)]
        nested["x_score"] = {j: 1.0 for j in range(400)}
        nested["x_thr"] = {j: 0.5 for j in range(400)}
        b2 = x.removed_bias(u, heavy, nested, transfer, fr, [False] * 400, ev, True)
        self.assertFalse(b2["passes_with_removed_at_total_loss"])  # the sensitivity can fail while the reported bars pass
        self.assertTrue(b2["passes_as_reported"])
        self.assertEqual(x.removed_bias(u, [], nested, transfer, fr, [], ev, True)["n_removed"], 0)

    def test_run_screen_reports_the_removed_mint_sensitivity_per_config_and_md_states_the_bias(self):
        u = mk_universe(False, n_good=3, n_bad=3, seed=11)
        removed = [mk_urow(800 + i, "2026-08-20", True, random.Random(i)) for i in range(2)]
        arrays = x.fit_arrays(u, x.labels(u), x.pool_dates(False), removed)
        out = x.run_screen(u, [False] * len(u), x.Runner(arrays, None, 1), False, configs=("c2",), removed=removed, frozen_removed_sel=[True, False])
        rb = out["results"]["c2"]["removed_bias"]
        self.assertEqual(rb["n_removed"], 2)
        self.assertEqual(rb["n_removed_selected_by_frozen"], 1)
        self.assertIn(rb["n_removed_selected_by_config"], (0, 1, 2))
        ur = {"count": 2, "n_universe_before": len(u) + 2, "fraction": 0.01, "non_p1_count": 2, "non_p1_fraction": 0.01, "max_fraction": 0.005, "per_block": {}, "mints": ["a", "b"], "bias_statement": x.BIAS_STATEMENT}
        base = {"schema": x.SCHEMA, "banner": x.BANNER, "first_line": x.first_line(False), "caveats": list(x.CAVEATS), "costs": {"vmap_0909_pinned": "p"},
                "universe": {"n": len(u), "sha256": "ab", "unpriceable_removed": ur,
                             "stats": {"by_source": {}, "dropped_outside_window": 0, "dropped_no_primary_cell": 0, "dropped_censored_primary": 0, "c1_missing_label_0": 0}}}
        md = x.render_md(x.make_report(base, out, False))
        self.assertIn("bias the gate bars UPWARD", md)
        self.assertIn("direction for bar 3 is unknown", md)
        self.assertIn("removed mints: this config would have selected", md)
        self.assertIn(x.BIAS_STATEMENT, x.CAVEATS)


class SensitivityIsolationTests(unittest.TestCase):
    def test_a_raising_removed_bias_leaves_the_config_completed_with_sensitivity_error(self):
        u = mk_universe(False, n_good=3, n_bad=3, seed=11)
        arrays = x.fit_arrays(u, x.labels(u), x.pool_dates(False))
        done = []
        with mock.patch.object(x, "removed_bias", side_effect=ValueError("sensitivity blew up")):
            out = x.run_screen(u, [False] * len(u), x.Runner(arrays, None, 1), False, on_config_done=lambda c, r: done.append(c), configs=("c2",))
        self.assertEqual(out["statuses"], {"c2": "completed"})  # never aborts the config
        self.assertEqual(done, ["c2"])
        res = out["results"]["c2"]
        self.assertEqual(res["sensitivity_error"], "sensitivity blew up")
        self.assertEqual(res["removed_bias"], {})
        self.assertIn("bars", res)
        base = {"schema": x.SCHEMA, "banner": x.BANNER, "first_line": x.first_line(False), "caveats": list(x.CAVEATS), "costs": {"vmap_0909_pinned": "p"},
                "universe": {"n": len(u), "sha256": "ab", "stats": {"by_source": {}, "dropped_outside_window": 0, "dropped_no_primary_cell": 0, "dropped_censored_primary": 0, "c1_missing_label_0": 0}}}
        self.assertIn("sensitivity FAILED and was skipped", x.render_md(x.make_report(base, out, False)))

    def test_main_a_raising_sensitivity_spends_no_extra_try_and_the_run_completes(self):
        t = MainTests()
        t.setUp()
        self.addCleanup(t.doCleanups)
        u, v_rows, nv_rows = t._synthetic_rows(2, 2)
        real = x.removed_bias
        calls = []

        def fake_screen(universe, frozen_sel, runner, with_p4, on_done, on_refused, no_v, **kw):
            res = dict(RES0, sensitivity_error="boom", removed_bias={})
            for c in x.CONFIGS:
                on_done(c, dict(res))
            return {"results": {c: dict(res) for c in x.CONFIGS}, "statuses": {c: "completed" for c in x.CONFIGS}}

        import contextlib

        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as st:
            for pt in t._patches({"return_value": (v_rows, nv_rows, set())}, frozen=[False] * len(u), screen=fake_screen):
                st.enter_context(pt)
            self.assertEqual(x.main(t._args(d)), 0)
            ls = lines(Path(d) / "t.jsonl")
            self.assertEqual(sum(1 for l in ls if l["config"]["status"] == "completed"), 9)
            self.assertEqual(sum(1 for l in ls if l["config"]["status"] == "aborted_after_read"), 0)
        self.assertIs(x.removed_bias, real)

    def test_frozen_selection_of_the_removed_mints_is_computed_before_started(self):
        t = MainTests()
        t.setUp()
        self.addCleanup(t.doCleanups)
        u, v_rows, nv_rows = t._synthetic_rows(8, 8)
        target = u[7]["mint"]
        mint_pools = {target: {NO_V_POOLS_207[3]}, **{v["mint"]: {f"pool-{i}"} for i, v in enumerate(u) if v["mint"] != target}}
        seen = {}
        helper = UnpriceablePoolTests()
        with tempfile.TemporaryDirectory() as d:
            order = []

            def frozen(universe, artifact_dir=None, scorer=None):
                order.append(((Path(d) / "t.jsonl").exists(), (Path(d) / "out" / "RUN.lock").exists(), [r["mint"] for r in universe][:1]))
                return [False] * len(universe)

            def fake_screen(universe, frozen_sel, runner, with_p4, on_done, on_refused, no_v, **kw):
                for c in x.CONFIGS:
                    on_done(c, dict(RES0))
                return {"results": {c: dict(RES0) for c in x.CONFIGS}, "statuses": {c: "completed" for c in x.CONFIGS}}

            import contextlib

            vmap = {p: 1 for ps in mint_pools.values() for p in ps if p != NO_V_POOLS_207[3]}
            with contextlib.ExitStack() as st:
                st.enter_context(mock.patch.object(x, "run_guards", return_value=t._guards()))
                st.enter_context(mock.patch.object(x, "vprepass_all", return_value=({}, mint_pools)))
                st.enter_context(mock.patch.object(x, "collect_all", return_value=(v_rows, nv_rows, set())))
                st.enter_context(mock.patch.object(x, "load_pinned_vmap", return_value=vmap))
                st.enter_context(mock.patch.object(x, "frozen_selection", frozen))
                st.enter_context(mock.patch.object(x, "run_screen", fake_screen))
                st.enter_context(mock.patch.object(x, "render_md", return_value="md"))
                self.assertEqual(x.main(t._args(d)), 0)
            self.assertEqual(len(order), 2)
            self.assertEqual(order[1][2], [target])  # the second call is on the removed mints
            self.assertEqual([(a, b) for a, b, _ in order], [(False, False), (False, False)])  # both before any tries line or lock

    def test_consistency_reports_pools_accepted_only_through_mints_outside_the_universe(self):
        mp_ = {"in": {"p1"}, "out": {"p2"}, "both": {"p3"}}
        r = x.check_no_v_consistency({"p1", "p2"}, mp_, {"in", "out"}, {"in"})
        self.assertEqual((r["n_adapter_no_v_pools"], r["n_accepted_only_because_of_mints_outside_the_universe"], r["pools_accepted_only_outside_the_universe"]), (2, 1, ["p2"]))
        r = x.check_no_v_consistency({"p1"}, mp_, {"in"}, {"in"})
        self.assertEqual(r["n_accepted_only_because_of_mints_outside_the_universe"], 0)
        self.assertEqual(x.check_no_v_consistency({"p3"}, mp_, {"both"})["n_accepted_only_because_of_mints_outside_the_universe"], 0)  # no universe given: all count as inside

    def test_report_records_the_count_and_the_scenario_wording(self):
        t = MainTests()
        t.setUp()
        self.addCleanup(t.doCleanups)
        u, v_rows, nv_rows = t._synthetic_rows(8, 8)
        out_of_u = "zz-out-of-universe"
        mint_pools = {v["mint"]: {f"pool-{i}"} for i, v in enumerate(u)}
        mint_pools[out_of_u] = {"pool-out"}
        vmap = {p: 1 for ps in mint_pools.values() for p in ps if p != "pool-out"}

        def fake_screen(universe, frozen_sel, runner, with_p4, on_done, on_refused, no_v, **kw):
            for c in x.CONFIGS:
                on_done(c, dict(RES0))
            return {"results": {c: dict(RES0) for c in x.CONFIGS}, "statuses": {c: "completed" for c in x.CONFIGS}}

        import contextlib

        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(x, "run_guards", return_value=t._guards()))
            st.enter_context(mock.patch.object(x, "vprepass_all", return_value=({}, mint_pools)))
            st.enter_context(mock.patch.object(x, "collect_all", return_value=(v_rows, nv_rows, {"pool-out"})))
            st.enter_context(mock.patch.object(x, "load_pinned_vmap", return_value=vmap))
            st.enter_context(mock.patch.object(x, "frozen_selection", lambda universe, artifact_dir=None, scorer=None: [False] * len(universe)))
            st.enter_context(mock.patch.object(x, "run_screen", fake_screen))
            st.enter_context(mock.patch.object(x, "render_md", return_value="md"))
            self.assertEqual(x.main(t._args(d)), 0)
            rep = json.loads((Path(d) / "out" / "report.json").read_text())
        adapter = rep["universe"]["unpriceable_removed"]["adapter_no_v_pools"]
        self.assertEqual(adapter["n_adapter_no_v_pools"], 1)
        self.assertEqual(adapter["n_accepted_only_because_of_mints_outside_the_universe"], 1)
        self.assertEqual(x.SCENARIO_WORDING, "one scenario (both sides' selected removed mints at total loss), not a worst-case bound")
        plan = x.PLAN.read_text(encoding="utf-8")
        for phrase in (x.SCENARIO_WORDING, "same row-level pool-to-mint attribution", "the pre-pass hours include the adapter's hours", "`sensitivity_error`"):
            self.assertIn(phrase, plan)
        b = x.removed_bias(mk_universe(), [], {"x_score": {}, "x_thr": {}, "score": [], "thr": {"p90": []}}, {"selected": [], "threshold_p90": 0}, [], [], {}, True)
        self.assertEqual(b["scenario"], x.SCENARIO_WORDING)


class HardeningTests(unittest.TestCase):
    def _g(self, p4=False):
        g = {"g2": {"pool": bc.hours_range(*x.P2_SEALED)}, "g3": {"walkers": x.p3_walkers("/nonexistent-p3")}, "g4": None}
        if p4:
            g["g4"] = {"pool": x.block_hours("P4")}
        return g

    def test_the_real_plans_pass_the_subset_check_for_every_source_with_and_without_p4(self):
        for p4 in (False, True):
            counts = x.check_adapter_hours_subset(self._g(p4), 8)
            self.assertEqual(set(counts), {"P1A", "P1C", "P1B", "P2", "P3"} | ({"P4"} if p4 else set()))
            ad, pre = x.adapter_hours(self._g(p4), 8), x.prepass_hours(self._g(p4))
            for tag in ad:
                self.assertTrue(ad[tag] <= set(pre[tag]), tag)
        # buffer hours are part of the adapter's hours: P2's first-chunk buffer reads past its home hours
        self.assertIn("2026-08-15T11", x.adapter_hours(self._g(), 4)["P2"])

    def test_an_adapter_hour_outside_the_scanned_hours_refuses_per_source(self):
        for tag in ("P1A", "P1C", "P1B", "P2", "P3"):
            ad = x.adapter_hours(self._g(), 4)
            ad[tag] = ad[tag] | {"2026-10-02T00"}
            with mock.patch.object(x, "adapter_hours", return_value=ad), self.assertRaises(x.Refused, msg=tag) as cm:
                x.check_adapter_hours_subset(self._g(), 4)
            self.assertIn(tag, str(cm.exception))
        pre = x.prepass_hours(self._g())
        pre["P3"] = pre["P3"][:-1]  # the pre-pass drops the last hour the adapter will read
        with mock.patch.object(x, "prepass_hours", return_value=pre), self.assertRaises(x.Refused):
            x.check_adapter_hours_subset(self._g(), 4)

    def test_vprepass_scans_exactly_prepass_hours(self):
        cov = {"n": 1, "prints": 1, "missing_fraction": 0.0, "covered": 1, "missing": 0, "missing_pools": 0, "max_missing_fraction": 0.01}
        seen = {}
        g = {"g1": {"roots": {"fast": Path("/a"), "insample": Path("/b"), "live": Path("/c")}}, **self._g(False)}
        g["g2"]["roots"] = {}
        args = SimpleNamespace(vmap_p1="/m", vmap_p2="/m", vmap_p3="/m", vmap_p4="/m")
        fake = {t: (object(), ["WRONG"], set()) for t in ("P1A", "P1C", "P1B")}

        def fake_prepass(hours, pool_hours, *a, **k):
            seen[len(seen)] = list(pool_hours)
            return cov, {}

        with mock.patch.object(x, "prepass", fake_prepass), mock.patch.object(x, "p1_hour_resolvers", return_value=fake), mock.patch.object(bc, "migrated_mints", return_value=set()):
            x.vprepass_all(g, args)
        ph = x.prepass_hours(g)
        self.assertEqual([seen[i] for i in range(5)], [ph["P1A"], ph["P1C"], ph["P1B"], ph["P2"], ph["P3"]])

    def test_main_refuses_before_started_when_the_adapter_reads_an_unscanned_hour(self):
        t = MainTests()
        t.setUp()
        self.addCleanup(t.doCleanups)
        ad = x.adapter_hours(t._guards(), 1)
        ad["P2"] = ad["P2"] | {"2026-08-14T11"}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(x, "run_guards", return_value=t._guards()), mock.patch.object(x, "adapter_hours", return_value=ad), \
                mock.patch.object(x, "vprepass_all") as pp, mock.patch.object(x, "collect_all") as ca:
            self.assertEqual(x.main(t._args(d)), 2)
            pp.assert_not_called()
            ca.assert_not_called()
            self.assertFalse((Path(d) / "t.jsonl").exists())
            self.assertFalse((Path(d) / "canon.jsonl").exists())
            self.assertFalse((Path(d) / "out" / "RUN.lock").exists())

    def test_removed_row_scoring_error_in_task_outer_drops_the_sensitivity_and_the_config_completes(self):
        u = mk_universe(False, n_good=2, n_bad=2, seed=5)
        removed = [mk_urow(900 + i, "2026-09-20", True, random.Random(i)) for i in range(2)]
        dates = x.pool_dates(False)
        arrays = x.fit_arrays(u, x.labels(u), dates, removed)
        real = fz._predict

        def flaky(m, xm):
            if len(xm) == 2:  # the removed rows only (every real date has 4 rows)
                raise ValueError("cannot score removed rows")
            return real(m, xm)

        done = []
        with mock.patch.object(fz, "_predict", flaky):
            out = x.run_screen(u, [False] * len(u), x.Runner(arrays, None, 1), False, on_config_done=lambda c, r: done.append(c), configs=("c2",), removed=removed, frozen_removed_sel=[False, False])
        self.assertEqual(out["statuses"], {"c2": "completed"})
        self.assertEqual(done, ["c2"])
        res = out["results"]["c2"]
        self.assertIn("cannot score removed rows", res["sensitivity_error"])
        self.assertEqual(res["removed_bias"], {})
        self.assertIn("bars", res)  # the real config result is intact
        self.assertEqual(len(res["folds"]), 30)

    def test_removed_row_loop_error_in_nested_oof_is_recorded_not_raised(self):
        class FakeRunner:
            def map(self, tasks):
                return [{"spec": {"date": 0}, "idx": [0, 1], "scores": [0.9, 0.1], "thr": {"p90": 0.5, "p80": 0.4, "p95": 0.6}, "n_inner": 5, "trained": True, "x_idx": ["not-an-int"], "x_scores": [0.7]}]

        n = x.nested_oof(FakeRunner(), "c2", 2, [0])
        self.assertIn("TypeError", n["x_error"])
        self.assertEqual((n["x_score"], n["x_thr"]), ({}, {}))  # dropped for this config
        self.assertEqual(n["score"], [0.9, 0.1])  # the OOF scores are untouched
        self.assertEqual(n["final_threshold_p90"], 0.9)
        class OkRunner(FakeRunner):
            def map(self, tasks):
                r = super().map(tasks)
                r[0]["x_idx"] = [2]
                return r

        ok = x.nested_oof(OkRunner(), "c2", 2, [0])
        self.assertIsNone(ok["x_error"])
        self.assertEqual(ok["x_score"], {0: 0.7})

    def test_run_screen_with_a_nested_x_error_never_aborts_and_label_counts_line_is_in_the_result(self):
        u = mk_universe(False, n_good=3, n_bad=3, seed=11)
        arrays = x.fit_arrays(u, x.labels(u), x.pool_dates(False))
        real = x.nested_oof

        def with_error(*a, **k):
            r = real(*a, **k)
            r["x_error"] = "ValueError: injected"
            return r

        with mock.patch.object(x, "nested_oof", with_error):
            out = x.run_screen(u, [False] * len(u), x.Runner(arrays, None, 1), False, configs=("c2",))
        self.assertEqual(out["statuses"], {"c2": "completed"})
        self.assertIn("injected", out["results"]["c2"]["sensitivity_error"])
        self.assertEqual(out["results"]["c2"]["label_counts"]["n"], len(u))


# --- tape cache, worker cap, report-only stats, E1/E2 -------------------------------------------------------------------------


class CacheTests(unittest.TestCase):
    META = {"tag": "P2", "head": "h", "args_hash": "a", "view_sha256": {"w1": "v"}, "vmap_sha256": "m"}

    def test_reuse_only_when_every_part_matches_else_discard(self):
        rows = [{"mint": "a", "net": 1}, {"mint": "b", "net": 2}]
        with tempfile.TemporaryDirectory() as d:
            c = Path(d)
            sha = x.write_cache(c, "v", "P2", rows, self.META, {"no_v_pools": ["p"]})
            got = x.load_cache(c, "v", "P2", self.META)
            self.assertEqual((got[0], got[1], got[2]), (rows, {"no_v_pools": ["p"]}, sha))
            for key, val in (("head", "h2"), ("args_hash", "a2"), ("view_sha256", {"w1": "other"}), ("vmap_sha256", "m2")):
                x.write_cache(c, "v", "P2", rows, self.META, {})
                self.assertIsNone(x.load_cache(c, "v", "P2", {**self.META, key: val}), key)
                for pth in x._cache_paths(c, "v", "P2"):
                    self.assertFalse(pth.exists(), key)  # a mismatch discards the source's cache
            x.write_cache(c, "v", "P2", rows, self.META, {})
            rp, mp_ = x._cache_paths(c, "v", "P2")
            rp.write_text(rp.read_text() + '{"mint": "c"}\n')  # rows changed after the manifest
            self.assertIsNone(x.load_cache(c, "v", "P2", self.META))
            self.assertFalse(rp.exists() or mp_.exists())
            x.write_cache(c, "v", "P2", rows, self.META, {})
            mp_.unlink()  # rows without a manifest = an incomplete source
            self.assertIsNone(x.load_cache(c, "v", "P2", self.META))
            self.assertFalse(rp.exists())
            self.assertIsNone(x.load_cache(c, "v", "P2", self.META))  # nothing cached

    def _g(self, sha="s1", vm="m1"):
        return {"g1": {"roots": {"fast": Path("/x/a"), "insample": Path("/x/b"), "live": Path("/x/c")}, "view_sha256": {"fast": sha, "insample": "i", "live": "l"}}, "g2": {"roots": {}, "pool": [], "view_sha256": {"w1": "p2"}},
                "g3": {"walkers": [], "pin_sha256": {"w1/manifest.json": "p3"}}, "g4": None, "vmap_sha256": {"P1": vm, "P2": "m2", "P3": "m3"}}

    def _args(self, **kw):
        ns = dict(p1_fast_dir="a", vmap_p1="/v1", vmap_p2="/v2", vmap_p3="/v3", p2_view_dir=["w"], max_workers=4, out_dir="o", tries_log=None, canonical_tries="c", guards_only=False)
        ns.update(kw)
        return SimpleNamespace(**ns)

    def test_collect_all_resumes_from_cache_and_never_mixes_old_and_new_rows(self):
        import itertools

        ctr = itertools.count()
        calls = []

        def fake_p1(tag, root, mode, scratch, vmap, mw):
            calls.append((mode, tag, mw))
            (scratch / f"counts_{mode}_{tag}").mkdir(parents=True, exist_ok=True)
            return [{"mint": f"{tag}-{next(ctr)}", "mode": mode}]

        def fake_hold(tag, hours, pool, mode, scratch, vmap, mw):
            calls.append((mode, tag, mw))
            return [{"mint": f"{tag}-{next(ctr)}", "mode": mode}]

        with tempfile.TemporaryDirectory() as d, mock.patch.object(x, "run_p1_pass", fake_p1), mock.patch.object(x, "run_holdout_pass", fake_hold):
            scratch = Path(d)
            v1, nv1, _ = x.collect_all(self._g(), self._args(), scratch, "head1")
            self.assertEqual(len(calls), 10)  # 5 sources x 2 modes
            self.assertTrue(all(mw <= x.TAPE_WORKERS_CAP for _m, _t, mw in calls))  # max_workers 4 -> tape workers 4
            calls.clear()
            v2, nv2, _ = x.collect_all(self._g(), self._args(max_workers=8), scratch, "head1")  # same head, shas, args: reused
            self.assertEqual(calls, [])
            self.assertEqual((v2, nv2), (v1, nv1))
            # a different head discards everything and re-runs it; no old row survives
            v3, nv3, _ = x.collect_all(self._g(), self._args(), scratch, "head2")
            self.assertEqual(len(calls), 10)
            self.assertTrue(all(r["mint"] not in {q["mint"] for rs in v1.values() for q in rs} for rs in v3.values() for r in rs))
            calls.clear()
            # a changed view sha for pool A only discards P1A's cache (both modes)
            x.collect_all(self._g(sha="s2"), self._args(), scratch, "head2")
            self.assertEqual(sorted((m, t) for m, t, _ in calls), [("nv", "P1A"), ("v", "P1A")])
            calls.clear()
            x.collect_all(self._g(), self._args(vmap_p1="/other"), scratch, "head2")  # args differ: all discarded
            self.assertEqual(len(calls), 10)

    def test_tape_passes_are_capped_at_4_workers(self):
        seen = {}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(x, "set_pass_env"), mock.patch.object(x, "patched_p1_workers"), \
                mock.patch.object(eem, "run_all_features", side_effect=lambda **kw: seen.setdefault("p1", kw["max_workers"]) and []):
            x.run_p1_pass("P1A", Path("/x"), "v", Path(d), "/m", 8)
        self.assertEqual(seen["p1"], 4)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(x, "set_pass_env"), mock.patch.object(x.s12, "load_rows", side_effect=lambda *a, **kw: seen.setdefault("h", a[1]) and []):
            x.run_holdout_pass("P2", object(), ["2026-08-15T12"], "v", Path(d), "/m", 8)
        self.assertEqual(seen["h"], 4)
        self.assertEqual((x.TAPE_WORKERS_CAP, x.MAX_WORKERS_CAP), (4, 8))
        self.assertIn("48 GB", x.__doc__)
        self.assertIn("8 CPUs", x.__doc__)

    def test_args_hash_ignores_workers_and_paths_but_not_the_inputs(self):
        a = x.args_hash(self._args())
        self.assertEqual(a, x.args_hash(self._args(max_workers=8, out_dir="zzz", tries_log="q")))
        self.assertNotEqual(a, x.args_hash(self._args(vmap_p3="/other")))


class ReportOnlyStatsTests(unittest.TestCase):
    def test_gap_and_censored_by_block(self):
        def rec(mint, mig, gap, censored=False):
            cell = {"k": 6, "lag": 2, "size": 50_000_000, "censored": True} if censored else {"k": 6, "lag": 2, "size": 50_000_000, "censored": False, "filled": False, "status": MISS, "net0": 0, "sides": 1, "p_press": 0.0}
            return {"mint": mint, "mig_ms": mig, "gap_ms": gap, "features": [0.0] * NF, "cells": [cell]}

        h = x.hour_ms
        v = {"P2": [rec("a", h("2026-08-20T00"), 4000), rec("b", h("2026-08-20T01"), 9000), rec("c", h("2026-08-20T02"), 100, censored=True)],
             "P3": [rec("d", h("2026-09-04T00"), 12000), rec("e", h("2026-09-04T01"), None), rec("f", h("2026-09-04T02"), 1, censored=True)]}
        _u, st = x.build_universe(v, {}, True)
        self.assertEqual(st["max_first_print_gap_ms_by_block"], {"P2": 9000, "P3": 12000})
        self.assertEqual(st["max_first_print_gap_ms"], 12000)
        self.assertEqual(st["censored_primary_by_block"], {"P2": 1, "P3": 1})

    def test_v_patch_records_the_gap_from_migration_to_the_first_pumpswap_print(self):
        t = PatchTests()
        fills, mint, seen, fake_score = t._fixture()
        mint.mig_ms = 400
        saved = (eem.score_one, eem._fills_for, eem.mixed_net)
        try:
            eem.score_one, eem._fills_for = fake_score, lambda m, migrate=True: (fills, 0, 0, None)
            with mock.patch.object(eem, "_state_index", lambda f, tt, b: 0), mock.patch.object(eem, "_slot_time", lambda f, tt, fb: 1000):
                with x.e15_v_patch():
                    rows = eem.score_one("m1", mint, object(), None, 0, {})
        finally:
            eem.score_one, eem._fills_for, eem.mixed_net = saved
        self.assertEqual(rows[0]["gap_ms"], 1000 - 400)


class DateClusterBarTests(unittest.TestCase):
    def test_both_resamplers_must_have_a_positive_lower_bound(self):
        days = x.p2_dates()[:10]
        trades = []
        for i, d in enumerate(days):
            v = (1.1 if i < 5 else -0.9) * SOL / 1000  # big date effects, small token noise: mean +0.1 per unit
            for j in range(100):
                trades.append({"mint": f"{d}-{j}", "day": d, "filled": True, "flat": v, "press": v, "source": "P2", "block": "P2"})
        st = x.leg_stats(trades, "flat")
        self.assertGreater(st["ci_lo"], 0)  # book_stats resamples tokens
        self.assertLessEqual(st["ci_lo_date"], 0)  # whole dates: no edge
        b = x.gate_bars(st, 10)
        self.assertTrue(b["ci_lower_gt_0_book_stats"])
        self.assertFalse(b["ci_lower_gt_0_date_cluster"])
        self.assertFalse(b["ci_lower_gt_0"])
        self.assertFalse(b["all"])
        # reported both ways
        self.assertIsNotNone(st["ci90_date_sol"])
        good = x.leg_stats(trades_on({d: 0.01 for d in days}, per_date=12), "flat")
        self.assertTrue(x.gate_bars(good, 10)["ci_lower_gt_0"])

    def test_report_shows_both_cis(self):
        u = mk_universe(False, n_good=3, n_bad=3, seed=11)
        arrays = x.fit_arrays(u, x.labels(u), x.pool_dates(False))
        screen = x.run_screen(u, [False] * len(u), x.Runner(arrays, None, 1), False, configs=("c2",))
        rp = screen["results"]["c2"]["bars"]["bar1"]["report"]["flat"]
        self.assertIn("ci90_sol", rp)
        self.assertIn("ci90_date_sol", rp)
        self.assertIn("ci_lower_gt_0_date_cluster", rp["gate"])


class IncompleteOutcomeTests(unittest.TestCase):
    def test_a_pass_is_not_claimed_unless_all_three_completed(self):
        res = {c: fake_result(True, 0.01) for c in x.CONFIGS}
        for bad in ("refused_after_read", "aborted_after_read"):
            st = {"c1": "completed", "c2": "completed", "c3": bad}
            d = x.decide_outcome(res, st)
            self.assertEqual(d["outcome"], x.OUTCOME_INCOMPLETE)
            self.assertIsNone(d["selected_for_confirmation"])
            self.assertFalse(d["family_closed"])
        d = x.decide_outcome(res, {"c1": "completed", "c2": "completed"})  # c3 missing
        self.assertEqual(d["outcome"], x.OUTCOME_INCOMPLETE)
        self.assertEqual(x.decide_outcome(res, {c: "completed" for c in x.CONFIGS})["selected_for_confirmation"], "c1")  # tie: lowest config index


if __name__ == "__main__":
    unittest.main()
