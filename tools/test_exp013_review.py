"""EXP-013 PR #239 review fixes: gaps across views, exact files, edge censoring, lazy import, read guard."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp013_pool as pool
import tools.exp013_screens as sc
from tools.exp012_fixtures import synthetic_table_rows, write_fast_format_root, write_table_fixture
from tools.exp013_fixtures import hours_between, synthetic_extra_rows, write_view_sha256


def make_view(root: Path, first: str, last: str, populated: dict[str, list[str]] | None = None) -> Path:
    write_fast_format_root(root, hours_between(first, last), populated or {})
    write_view_sha256(root)
    return root


class _Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()


class GapTests(_Tmp):
    def _two(self) -> list[Path]:
        return [make_view(self.tmp / "a", "2026-08-20T12", "2026-08-21T11"), make_view(self.tmp / "b", "2026-08-23T12", "2026-08-24T11")]

    def test_a_gap_between_views_is_refused_and_named(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            pool.load_extra_views(self._two(), fz.DAYS_ALL)
        self.assertIn("2026-08-21T12..2026-08-23T11 (48 h)", str(cm.exception))
        self.assertIn("--allow-gap", str(cm.exception))

    def test_allow_gap_accepts_and_records_it(self) -> None:
        views = pool.load_extra_views(self._two(), fz.DAYS_ALL, allow_gap=True)
        self.assertEqual(pool.union_gaps(views), [{"from": "2026-08-21T12", "to": "2026-08-23T11", "n_hours": 48}])
        self.assertEqual(pool.extra_manifest(views, 0)["gaps"], pool.union_gaps(views))
        md = sc.screens_markdown({"note": "n", "period_transfer": {}, "source_screens": {}, "overlap_with_exp012": {"all": {"scope": "all", "n_candidate_selected": 0}, "by_period": {}}, "extra_pool_gaps": pool.union_gaps(views)})
        self.assertIn("missing 2026-08-21T12..2026-08-23T11 (48 h)", md)

    def test_contiguous_views_have_no_gap_and_the_runs_are_split_at_a_gap(self) -> None:
        a = make_view(self.tmp / "a", "2026-08-20T12", "2026-08-21T11")
        b = make_view(self.tmp / "b2", "2026-08-21T12", "2026-08-22T11")
        self.assertEqual(pool.union_gaps(pool.load_extra_views([a, b], fz.DAYS_ALL)), [])
        views = pool.load_extra_views(self._two(), fz.DAYS_ALL, allow_gap=True)
        hours = sorted(h for v in views for h in v.hours)
        plan = pool.plan_extra(hours, 2, 24, 12)
        for _i, home, buf in plan:  # a buffer never reads across the gap
            self.assertTrue(all(h[:10] <= "2026-08-21" for h in home + buf) or all(h[:10] >= "2026-08-23" for h in home + buf))

    def test_cli_flag(self) -> None:
        import argparse

        ap = argparse.ArgumentParser()
        fz.add_extra_view_arg(ap)
        self.assertFalse(ap.parse_args([]).allow_gap)
        self.assertTrue(ap.parse_args(["--allow-gap", "--extra-fast-view", "a", "--extra-fast-view", "b"]).allow_gap)


class ExactFilesTests(_Tmp):
    def test_zst_listed_with_an_unlisted_jsonl_present_is_refused(self) -> None:
        root = make_view(self.tmp / "v", "2026-08-26T12", "2026-08-26T13")
        (root / "trades" / "trades-2026-08-26T13.jsonl").write_text("")
        with self.assertRaises(SystemExit) as cm:
            pool.load_extra_views([root], fz.DAYS_ALL)
        self.assertIn("trades/trades-2026-08-26T13.jsonl", str(cm.exception))
        self.assertIn("not listed", str(cm.exception))

    def test_gz_listed_with_an_unlisted_zst_present_is_refused(self) -> None:
        root = make_view(self.tmp / "v", "2026-08-26T12", "2026-08-26T13")
        z = root / "creates" / "creates-2026-08-26T13.jsonl.zst"
        gz = root / "creates" / "creates-2026-08-26T13.jsonl.gz"
        shutil.copy(z, gz)
        z.rename(self.tmp / "keep.zst")  # the view now lists only the .gz ...
        write_view_sha256(root)
        shutil.copy(self.tmp / "keep.zst", z)  # ... and an unlisted .zst appears afterwards
        with self.assertRaises(SystemExit) as cm:
            pool.load_extra_views([root], fz.DAYS_ALL)
        self.assertIn("creates/creates-2026-08-26T13.jsonl.zst", str(cm.exception))

    def test_the_loader_opens_exactly_the_listed_paths(self) -> None:
        root = make_view(self.tmp / "v", "2026-08-26T12", "2026-08-26T13")
        (view,) = pool.load_extra_views([root], fz.DAYS_ALL)
        info = pool._hour_info_x("2026-08-26T13", hour_roots={h: view.files[h] for h in view.hours})
        self.assertEqual(Path(info["trade"]), view.root / "trades" / "trades-2026-08-26T13.jsonl.zst")
        self.assertEqual(Path(info["create"]), view.root / "creates" / "creates-2026-08-26T13.jsonl.zst")

    def test_a_clean_view_is_unaffected(self) -> None:
        self.assertEqual(len(pool.load_extra_views([make_view(self.tmp / "v", "2026-08-26T12", "2026-08-26T14")], fz.DAYS_ALL)), 1)


class EdgeCensoringTests(_Tmp):
    def test_edge_days_and_row_counts(self) -> None:
        root = make_view(self.tmp / "v", "2026-08-20T12", "2026-08-22T11")
        views = pool.load_extra_views([root], fz.DAYS_ALL)
        rows = synthetic_extra_rows(["2026-08-20", "2026-08-21", "2026-08-22"], per_day=5)
        ec = pool.edge_censoring(views, rows)
        got = {(e["role"], e["day"]): (e["zone_hours_on_day"], e["n_rows_on_day"]) for e in ec["edge_days"]}
        self.assertEqual(
            got,
            {
                ("left_censored", "2026-08-20"): (12, 5),
                ("left_censored", "2026-08-21"): (12, 5),
                ("right_truncated", "2026-08-21"): (12, 5),
                ("right_truncated", "2026-08-22"): (12, 5),
            },
        )
        self.assertEqual(ec["n_rows_left_censored_days"], 10)
        self.assertEqual(ec["n_rows_right_truncated_days"], 10)
        self.assertEqual(ec["runs"], [["2026-08-20T12", "2026-08-22T11"]])
        self.assertIn("2026-08-28T11", ec["statement"])
        self.assertIn("2026-08-14T12", ec["statement"])
        md = sc.screens_markdown({"note": "n", "period_transfer": {}, "source_screens": {}, "overlap_with_exp012": {"all": {"scope": "all", "n_candidate_selected": 0}, "by_period": {}}, "edge_censoring": ec})
        self.assertIn("Edge censoring (pool X)", md)
        self.assertIn("| right_truncated | 2026-08-22 | 12 | 5 |", md.replace("2026-08-20T12..2026-08-22T11 | ", ""))

    def test_non_x_rows_are_not_counted(self) -> None:
        root = make_view(self.tmp / "v", "2026-08-20T12", "2026-08-21T11")
        views = pool.load_extra_views([root], fz.DAYS_ALL)
        rows = synthetic_extra_rows(["2026-08-20"], per_day=3) + synthetic_extra_rows(["2026-08-20"], per_day=4, pool="A")
        ec = pool.edge_censoring(views, rows)
        self.assertEqual(sum(e["n_rows_on_day"] for e in ec["edge_days"] if e["role"] == "left_censored"), 3)

    def test_manifest_carries_gaps_and_edge_censoring(self) -> None:
        import tools.exp013_refit as refit

        root = make_view(self.tmp / "v", "2026-08-20T12", "2026-08-21T11")
        views = pool.load_extra_views([root], fz.DAYS_ALL)
        rows = synthetic_table_rows()[:5] + synthetic_extra_rows(["2026-08-20"], per_day=3)
        e12 = self.tmp / "e12"
        t = self.tmp / "t" / "table.jsonl"
        write_table_fixture(t, synthetic_table_rows())
        m, th, o, man, w, _n = fz.freeze(table_path=t, run_nested_lodo=False)
        fz.write_outputs(e12, m, th, o, man, w)
        out = self.tmp / "out"
        out.mkdir()
        doc = refit.write_manifest(out, {}, {}, views, "md5", fz.pool_days({"extra_days": ["2026-08-20", "2026-08-21"]}), 1.0, rows=rows, e12_dir=e12)
        self.assertEqual(doc["extra_pool_gaps"], [])
        self.assertIn("censored", doc["edge_censoring"]["statement"])
        self.assertTrue(doc["edge_censoring"]["edge_days"])
        self.assertTrue(json.loads((out / "manifest.json").read_text())["edge_censoring"]["edge_days"])


class LazyImportTests(_Tmp):
    def test_the_exp012_table_build_and_freeze_do_not_need_exp013_pool(self) -> None:
        import tools.exp011_build_table as bt

        table = self.tmp / "t" / "table.jsonl"
        write_table_fixture(table, synthetic_table_rows())
        with mock.patch.dict(sys.modules, {"tools.exp013_pool": None}):
            with self.assertRaises(ImportError):
                importlib.import_module("tools.exp013_pool")
            bt2 = importlib.reload(bt)
            seen: dict = {}

            def fake_load(**kw):
                seen.update(kw)
                return [], {"pools": {}, "n_rows_total": 0, "days": []}

            with mock.patch.object(bt2, "load_tp50_rows", fake_load):
                bt2.main(["--out", str(self.tmp / "t2.jsonl"), "--scratch-dir", str(self.tmp / "s")])
            self.assertIsNone(seen["extra_views"])
            fz.main(["--table", str(table), "--out-dir", str(self.tmp / "o"), "--skip-nested-lodo"])
        importlib.reload(bt)

    def test_the_module_level_imports_of_the_exp012_clis_exclude_exp013(self) -> None:
        for name in ("tools/exp011_build_table.py", "tools/exp011_freeze.py"):
            src = (Path(__file__).resolve().parents[1] / name).read_text()
            top = src.split("def ", 1)[0]
            self.assertNotIn("exp013", top.replace("EXP-013", ""), name)


class ReadGuardTests(_Tmp):
    def test_the_exp012_read_dir_is_a_forbidden_extra_root_by_realpath(self) -> None:
        read = Path(fz._REPO_ROOT) / "ARTIFACTS" / "exp012" / "read"
        for bad in (read, read / "x", Path(fz._REPO_ROOT) / "ARTIFACTS" / "exp012" / "read" / ".." / "read" / "y"):
            with self.subTest(str(bad)), self.assertRaises(SystemExit) as cm:
                pool.load_extra_views([bad], fz.DAYS_ALL)
            self.assertIn("forbidden", str(cm.exception))
        link = self.tmp / "lnk"
        os.symlink(read, link)
        with self.assertRaises(SystemExit) as cm:
            pool.load_extra_views([link], fz.DAYS_ALL)
        self.assertIn("forbidden", str(cm.exception))

    def test_other_exp012_artifacts_are_not_blocked_as_roots_by_the_read_prefix(self) -> None:
        self.assertIsNone(pool._is_forbidden(os.path.realpath(str(Path(fz._REPO_ROOT) / "ARTIFACTS" / "exp012" / "model.txt"))))

    def test_the_screens_refuse_a_read_dir_given_as_the_exp012_dir_by_realpath(self) -> None:
        link = self.tmp / "e12"
        os.symlink(Path(fz._REPO_ROOT) / "ARTIFACTS" / "exp012" / "read", link)
        with self.assertRaises(SystemExit):
            sc.e12_file_sha256(link)


if __name__ == "__main__":
    unittest.main()
