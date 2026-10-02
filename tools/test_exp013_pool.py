"""EXP-013 expanded-pool tooling: default byte-identity, guards, N-fold LODO. Fixtures only."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.exp011_build_table as build_table_mod
import tools.exp011_freeze as fz
import tools.exp013_pool as pool
import tools.exp013_refit as refit
from tools.exp012_fixtures import synthetic_table_rows, write_fast_format_root, write_table_fixture, write_zst_jsonl
from tools.exp013_fixtures import hours_between, synthetic_extra_rows, write_view_sha256
from tools.test_exp012_freeze_roots import GOLDEN_ARTIFACT_MD5

EXTRA_DAYS = ("2026-08-26", "2026-08-27")


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def make_view(root: Path, first: str, last: str, populated: dict[str, list[str]] | None = None) -> Path:
    write_fast_format_root(root, hours_between(first, last), populated or {})
    write_view_sha256(root)
    return root


def write_table_with_extra_days(table: Path, rows: list[dict], extra_days: tuple[str, ...]) -> None:
    write_table_fixture(table, rows)
    mpath = table.parent / "row_counts.json"
    doc = json.loads(mpath.read_text())
    doc["manifest"]["extra_days"] = list(extra_days)
    mpath.write_text(json.dumps(doc) + "\n")


class DefaultsByteIdenticalTests(unittest.TestCase):
    def test_no_extras_reproduces_the_pre_change_golden_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            table = tmp / "t" / "table.jsonl"
            write_table_fixture(table, synthetic_table_rows())
            model, thr, oof, man, wall, nested = fz.freeze(table_path=table)
            self.assertNotIn("extra_days", man)
            self.assertEqual(fz.pool_days(man), fz.DAYS_ALL)
            out = tmp / "out"
            fz.write_outputs(out, model, thr, oof, man, wall, nested_report=nested, days=fz.pool_days(man))
            for name, want in GOLDEN_ARTIFACT_MD5.items():
                with self.subTest(name):
                    self.assertEqual(_md5(out / name), want)

    def test_loader_without_extras_adds_no_manifest_keys(self) -> None:
        with mock.patch.object(fz, "run_all_features_a", lambda **kw: []), mock.patch.object(fz, "run_all_features_c", lambda **kw: []), mock.patch.object(
            fz, "run_all_features_b", lambda **kw: []
        ):
            _rows, man = fz.load_tp50_rows()
        self.assertEqual(sorted(man), ["days", "n_rows_total", "pools"])

    def test_freeze_main_default_does_not_touch_the_extra_machinery(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            table = Path(td) / "t" / "table.jsonl"
            write_table_fixture(table, synthetic_table_rows())
            with mock.patch.object(pool, "load_extra_views", side_effect=AssertionError("called")):
                fz.main(["--table", str(table), "--out-dir", str(Path(td) / "o"), "--skip-nested-lodo"])


class GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_forbidden_paths_are_refused_after_realpath(self) -> None:
        for bad in (
            "/data/mal/blocks/forward-1002",
            "/data/mal/blocks/fresh-0903/w1",
            "/data/mal/blocks/explore-0814/w1/../../fresh-0828/w2",
            "/data/mal/blocks-clean/fresh-0828/w1",
            "/data/mal/clean-view/fresh-0828/w3",
            "/var/lib/mal/backfill-fast-c",
        ):
            with self.subTest(bad), self.assertRaises(SystemExit) as cm:
                pool.load_extra_views([bad], fz.DAYS_ALL)
            self.assertIn("forbidden", str(cm.exception))

    def test_a_symlinked_root_into_a_forbidden_location_is_refused(self) -> None:
        link = self.tmp / "w1"
        os.symlink("/data/mal/clean-view/fresh-0828/w1", link)
        with self.assertRaises(SystemExit) as cm:
            pool.load_extra_views([link], fz.DAYS_ALL)
        self.assertIn("forbidden", str(cm.exception))

    def test_a_listed_file_symlinked_into_a_forbidden_location_is_refused(self) -> None:
        root = make_view(self.tmp / "v", "2026-08-26T12", "2026-08-26T13")
        victim = root / "trades" / "trades-2026-08-26T13.jsonl.zst"
        victim.unlink()
        os.symlink("/data/mal/blocks/fresh-0828/w1/trades/trades-2026-08-28T12.jsonl.zst", victim)
        with self.assertRaises(SystemExit) as cm:
            pool.load_extra_views([root], fz.DAYS_ALL)
        self.assertIn("forbidden", str(cm.exception))

    def test_missing_view_sha256_is_refused(self) -> None:
        root = self.tmp / "v"
        write_fast_format_root(root, hours_between("2026-08-26T12", "2026-08-26T13"), {})
        with self.assertRaises(SystemExit) as cm:
            pool.load_extra_views([root], fz.DAYS_ALL)
        self.assertIn("VIEW.sha256", str(cm.exception))

    def test_a_tampered_file_is_refused(self) -> None:
        root = make_view(self.tmp / "v", "2026-08-26T12", "2026-08-26T14")
        write_zst_jsonl(root / "creates" / "creates-2026-08-26T13.jsonl.zst", [{"type": "create", "mint": "evil"}])
        with self.assertRaises(SystemExit) as cm:
            pool.load_extra_views([root], fz.DAYS_ALL)
        self.assertIn("sha256 mismatch", str(cm.exception))

    def test_hours_outside_the_expansion_block_are_refused(self) -> None:
        for first, last in (("2026-09-04T00", "2026-09-04T02"), ("2026-08-27T22", "2026-08-28T12"), ("2026-08-14T11", "2026-08-14T13"), ("2026-09-20T00", "2026-09-20T02")):
            with self.subTest(first):
                root = make_view(self.tmp / f"v{first}", first, last)
                with self.assertRaises(SystemExit) as cm:
                    pool.load_extra_views([root], fz.DAYS_ALL)
                self.assertIn("expansion block", str(cm.exception))

    def test_a_hole_in_the_hours_is_refused(self) -> None:
        root = make_view(self.tmp / "v", "2026-08-26T12", "2026-08-26T16")
        for sub in ("trades", "creates", "migrations"):
            (root / sub / f"{sub}-2026-08-26T14.jsonl.zst").unlink()
        write_view_sha256(root)
        with self.assertRaises(SystemExit) as cm:
            pool.load_extra_views([root], fz.DAYS_ALL)
        self.assertIn("contiguous", str(cm.exception))

    def test_a_day_that_belongs_to_another_pool_is_refused(self) -> None:
        root = make_view(self.tmp / "v", "2026-08-26T12", "2026-08-26T14")
        with self.assertRaises(SystemExit) as cm:
            pool.load_extra_views([root], ("2026-08-26",))
        self.assertIn("built-in pool", str(cm.exception))
        self.assertEqual(pool.load_extra_views([root], fz.DAYS_ALL)[0].days, ["2026-08-26"])

    def test_two_views_sharing_an_hour_are_refused(self) -> None:
        a = make_view(self.tmp / "a", "2026-08-26T12", "2026-08-26T16")
        b = make_view(self.tmp / "b", "2026-08-26T16", "2026-08-26T20")
        with self.assertRaises(SystemExit) as cm:
            pool.load_extra_views([a, b], fz.DAYS_ALL)
        self.assertIn("hour 2026-08-26T16", str(cm.exception))

    def test_adjacent_views_may_share_a_day_but_one_root_twice_is_refused(self) -> None:
        a = make_view(self.tmp / "a", "2026-08-26T12", "2026-08-27T11")
        b = make_view(self.tmp / "b", "2026-08-25T12", "2026-08-26T11")
        views = pool.load_extra_views([a, b], fz.DAYS_ALL)
        self.assertEqual(sorted({d for v in views for d in v.days}), ["2026-08-25", "2026-08-26", "2026-08-27"])
        with self.assertRaises(SystemExit):
            pool.load_extra_views([a, a], fz.DAYS_ALL)

    def test_freeze_cli_refuses_forbidden_extra_root_and_artifacts_out_dir(self) -> None:
        with self.assertRaises(SystemExit):
            fz.main(["--out-dir", str(self.tmp / "o"), "--extra-fast-view", "/data/mal/blocks/forward-1002"])
        for sub in ("exp012", "exp013"):
            with self.assertRaises(SystemExit) as cm:
                fz.main(["--out-dir", str(fz._REPO_ROOT / "ARTIFACTS" / sub), "--extra-fast-view", "/x"])
            self.assertIn("ARTIFACTS", str(cm.exception))

    def test_extras_need_the_three_clean_roots(self) -> None:
        views = pool.load_extra_views([make_view(self.tmp / "v", "2026-08-26T12", "2026-08-26T14")], fz.DAYS_ALL)
        with self.assertRaises(SystemExit):
            build_table_mod.build_table(self.tmp / "t.jsonl", self.tmp / "s", extra_views=views)
        self.assertFalse((self.tmp / "t.jsonl").exists())


class ExtraLoaderTests(unittest.TestCase):
    def test_extra_view_tape_yields_pool_x_rows_with_the_recipe_row_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            hrs = hours_between("2026-08-26T12", "2026-08-27T11")
            a = Path(td) / "a"
            write_fast_format_root(a, hrs, {hrs[10]: ["mintX1", "mintX2"]})
            write_view_sha256(a)
            views = pool.load_extra_views([a], fz.DAYS_ALL)
            rows = pool.run_all_features_x(views, max_workers=1, buffer_hours=2, max_home_hours=12)
            tp = [r for r in rows if r["spec"] == fz.TARGET_SPEC_ID]
            self.assertEqual(len(tp), 2)
            self.assertEqual({r["day"] for r in tp}, {"2026-08-26"})
            self.assertTrue(set(fz.FROZEN_FEATURE_NAMES) <= set(tp[0]["features"]))
            rows2 = pool.run_all_features_x(views, max_workers=2, buffer_hours=2, max_home_hours=12)

            def key(rs: list[dict]) -> list[str]:
                return sorted(json.dumps(r, sort_keys=True) for r in rs)

            self.assertEqual(key(rows2), key(rows))

    def test_load_tp50_rows_adds_pool_x_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            hrs = hours_between("2026-08-26T12", "2026-08-27T11")
            a = Path(td) / "a"
            write_fast_format_root(a, hrs, {hrs[3]: ["mintX1"]})
            write_view_sha256(a)
            views = pool.load_extra_views([a], fz.DAYS_ALL)
            with mock.patch.object(fz, "run_all_features_a", lambda **kw: []), mock.patch.object(fz, "run_all_features_c", lambda **kw: []), mock.patch.object(
                fz, "run_all_features_b", lambda **kw: []
            ):
                rows, man = fz.load_tp50_rows(max_workers=1, buffer_hours=2, extra_views=views)
            self.assertEqual([r["pool"] for r in rows], ["X"])
            self.assertEqual(man["extra_days"], ["2026-08-26", "2026-08-27"])
            self.assertEqual(man["extra_pools"]["views"][0]["view_sha256_file_sha256"], views[0].view_sha256_file_sha256)
            self.assertEqual(fz.pool_days(man), tuple(sorted(fz.DAYS_ALL + ("2026-08-26", "2026-08-27"))))


class NFoldLodoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._td.name)
        cls.rows = sorted(synthetic_table_rows() + synthetic_extra_rows(EXTRA_DAYS), key=lambda r: (r["day"], r["mint"], r["spec"]))
        cls.table = cls.tmp / "t" / "table.jsonl"
        write_table_with_extra_days(cls.table, cls.rows, EXTRA_DAYS)
        cls.model, cls.thr, cls.oof, cls.man, cls.wall, cls.nested = fz.freeze(table_path=cls.table, run_nested_lodo=False)
        # a stand-in for ARTIFACTS/exp012: the 9-day freeze of the same fixture recipe
        cls.e12 = cls.tmp / "e12"
        m9, t9, o9, man9, w9, _n = fz.freeze(table_path=cls._write_nine(), run_nested_lodo=False)
        fz.write_outputs(cls.e12, m9, t9, o9, man9, w9)

    @classmethod
    def _write_nine(cls) -> Path:
        t = cls.tmp / "t9" / "table.jsonl"
        write_table_fixture(t, synthetic_table_rows())
        return t

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def test_eleven_folds_cover_the_nine_and_the_two_extra_days(self) -> None:
        days = fz.pool_days(self.man)
        self.assertEqual(len(days), 11)
        self.assertEqual({o["day"] for o in self.oof}, set(days))
        self.assertEqual(len(self.oof), len(self.rows))

    def test_oof_scores_come_from_the_model_that_never_saw_that_day(self) -> None:
        held = EXTRA_DAYS[0]
        train = [r for r in self.rows if r["day"] != held]
        model = fz._fit([fz._vector(r["features"]) for r in train], fz._label(train))
        test = [r for r in self.rows if r["day"] == held]
        want = fz._predict(model, [fz._vector(r["features"]) for r in test])
        self.assertEqual(sorted(o["score"] for o in self.oof if o["day"] == held), sorted(want))

    def test_threshold_is_the_non_interpolating_p90_of_pooled_oof(self) -> None:
        s = sorted(o["score"] for o in self.oof)
        self.assertEqual(self.thr["threshold"], s[int(round(0.9 * (len(s) - 1)))])
        self.assertEqual(self.thr["n_oof"], len(self.rows))

    def test_per_day_table(self) -> None:
        days = fz.pool_days(self.man)
        doc = refit.per_day_lodo_table(self.rows, self.oof, self.thr["threshold"], days)
        self.assertEqual([r["day"] for r in doc["per_day"]], list(days))
        self.assertEqual(sum(r["n"] for r in doc["per_day"]), len(self.rows))
        self.assertEqual(doc["pooled"]["selected"], self.thr["n_selected_at_or_above_threshold"])
        self.assertIn("promote", doc["gate_pooled"])
        self.assertIn("| ALL |", refit.per_day_markdown(doc))

    def test_run_writes_the_candidate_dir_manifest_and_screens(self) -> None:
        out_root = self.tmp / "cand"
        roots = {k: self.tmp / k for k in ("fast", "ins", "live")}
        for r in roots.values():
            r.mkdir(exist_ok=True)
            (r / "f.txt").write_text(r.name)
            write_view_sha256(r)
        ev = make_view(self.tmp / "ev", "2026-08-26T12", "2026-08-27T11")

        def fake_build(table, scratch, **kw):
            self.assertEqual((kw["max_workers"], kw["buffer_hours"], kw["max_home_hours"]), (2, 24, 12))
            self.assertEqual(len(kw["extra_views"]), 1)
            write_table_with_extra_days(table, self.rows, EXTRA_DAYS)
            return {"table_md5": _md5(table)}

        ap = argparse.ArgumentParser()
        ap.add_argument("--run-id")
        ap.add_argument("--out-root")
        ap.add_argument("--exp012-dir")
        fz.add_root_args(ap)
        pool.add_extra_view_arg(ap)
        args = ap.parse_args(
            ["--run-id", "r1", "--out-root", str(out_root), "--exp012-dir", str(self.e12), "--verify-view", "--fast-dir", str(roots["fast"]),
             "--oracle-insample-dir", str(roots["ins"]), "--oracle-live-dir", str(roots["live"]), "--extra-fast-view", str(ev)]
        )
        with mock.patch.object(fz, "check_view_pin", return_value="x"), mock.patch.object(fz, "verify_pool_files"), mock.patch.object(refit, "build_table", fake_build), mock.patch(
            "sys.stderr", new_callable=io.StringIO
        ):
            out = refit.run(args)
        self.assertEqual(out, out_root / "r1")
        for name in ("model.txt", "threshold.json", "features.json", "oof_scores.json", "per_day_lodo.json", "per_day_lodo.md", "screens.json", "screens.md", "manifest.json"):
            self.assertTrue((out / name).is_file(), name)
        self.assertEqual(json.loads((out / "oof_scores.json").read_text())["days"], list(fz.pool_days(self.man)))
        man = json.loads((out / "manifest.json").read_text())
        self.assertEqual(man["recipe"]["n_folds"], 11)
        self.assertEqual([d["day"] for d in man["days_used"]], list(fz.pool_days(self.man)))
        self.assertEqual(sum(d["n_rows"] for d in man["days_used"]), len(self.rows))
        self.assertIn("No row from ARTIFACTS/exp012/read/", man["no_holdout_statement"])
        self.assertEqual(len(man["no_holdout_assertions"]), 5)
        self.assertEqual(set(man["exp012_files_sha256"]), {"model.txt", "threshold.json", "features.json", "oof_scores.json"})
        self.assertEqual(man["extra_views"][0]["view_sha256_file_sha256"], hashlib.sha256((ev / "VIEW.sha256").read_bytes()).hexdigest())
        self.assertEqual(set(man["builtin_pool_views"]), {"fast", "insample", "live"})
        self.assertTrue(all(len(v["view_sha256_file_sha256"]) == 64 for v in man["builtin_pool_views"].values()))
        self.assertGreater(len(man["extra_views"][0]["files"]), 0)
        with self.assertRaises(SystemExit):  # the same non-empty dir
            refit.run(args)
        args.extra_fast_view = None
        args.run_id = "r2"
        with self.assertRaises(SystemExit):  # no extra view
            refit.run(args)

    def test_day_pinning_assertion_trips_on_a_stray_day(self) -> None:
        views = pool.load_extra_views([make_view(self.tmp / "evp", "2026-08-26T12", "2026-08-27T11")], fz.DAYS_ALL)
        days = fz.pool_days(self.man)
        stray = [*self.rows, dict(self.rows[0], day="2026-09-05")]
        with self.assertRaises(AssertionError):
            refit.assert_no_holdout(stray, days, views, {}, self.e12)
        self.assertEqual(len(refit.assert_no_holdout(self.rows, days, views, {}, self.e12)), 5)
        with self.assertRaises(AssertionError):
            refit.assert_no_holdout(self.rows, days, views, {}, self.tmp / "exp012" / "read")


if __name__ == "__main__":
    unittest.main()
