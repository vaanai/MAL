"""EXP-012 part (a): the freeze / table builder take the three pool roots as
CLI args and write to a given out dir. The recipe must not move.

Decision-equivalence proof (md5 before == md5 after). The GOLDEN_* constants
below were computed on the code at `main` a920437, BEFORE this change (no root
arguments existed; the loaders were pointed at fixtures by patching their
module-level default paths), from the deterministic fixtures in
tools/exp012_fixtures.py. They must still hold now:

  - the freeze outputs (model.txt, threshold.json, features.json,
    oof_scores.json, nested_fixed_threshold_lodo.json) from a fixed table;
  - the pool rows `load_tp50_rows` returns from a fixed synthetic tape, both
    with the default paths (module constants patched, the old way) and with
    the roots passed explicitly (the new way).

Fixtures only: no real data root is ever opened.
"""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.exp011_build_table as build_table_mod
import tools.exp011_freeze as fz
import tools.exploration_exits as ex
import tools.oracle_insample_adapter as ia
import tools.oracle_live_adapter as la
from tools.exp012_fixtures import synthetic_table_rows, write_fast_format_root, write_table_fixture, write_zst_jsonl

# Captured on main a920437 (pre-change) -- see module docstring.
GOLDEN_ARTIFACT_MD5 = {
    "model.txt": "f09d96fbc47941690f861c391dc06d8a",
    "threshold.json": "bd1f48e428e8576b23f124a9266d2440",
    "features.json": "5dec821ed0f4098b71b1c544de23a0ab",
    "oof_scores.json": "3a6121b374a31242f289224cf7c65fde",
    "nested_fixed_threshold_lodo.json": "3513e04d32f522f8c1df38712dcc45fa",
}
GOLDEN_LOADER_ROWS_N = 3
GOLDEN_LOADER_ROWS_MD5 = "9574a8accc6139b5d63361c8b2af0a42"


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _rows_md5(rows: list[dict]) -> str:
    return hashlib.md5(json.dumps(rows, sort_keys=True).encode()).hexdigest()


class FreezeOutputsUnchangedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._td.name)
        cls.table = cls.tmp / "t" / "table.jsonl"
        write_table_fixture(cls.table, synthetic_table_rows())
        cls.out = cls.tmp / "out"
        model, thr, oof, man, wall, nested = fz.freeze(table_path=cls.table)
        fz.write_outputs(cls.out, model, thr, oof, man, wall, nested_report=nested)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def test_artifact_md5s_equal_the_pre_change_golden(self) -> None:
        for name, want in GOLDEN_ARTIFACT_MD5.items():
            with self.subTest(name):
                self.assertEqual(_md5(self.out / name), want)

    def test_default_write_outputs_adds_no_new_files(self) -> None:
        self.assertEqual(
            sorted(p.name for p in self.out.iterdir()),
            sorted(["model.txt", "model.md5", "threshold.json", "features.json", "train_manifest.json", "oof_scores.json", "nested_fixed_threshold_lodo.json"]),
        )

    def test_main_with_frozen_manifest_writes_the_same_artifacts_plus_manifest(self) -> None:
        out2 = self.tmp / "out2"
        with mock.patch.object(fz, "_git_state", return_value=("c" * 40, False)), mock.patch("sys.stderr", new_callable=io.StringIO):
            fz.main(["--table", str(self.table), "--out-dir", str(out2), "--frozen-manifest", "--expect-commit", "c" * 40])
        for name, want in GOLDEN_ARTIFACT_MD5.items():
            with self.subTest(name):
                self.assertEqual(_md5(out2 / name), want)
        listed = fz.parse_md5_manifest(out2 / fz.FROZEN_MANIFEST_NAME)
        for name in fz.FROZEN_MANIFEST_REQUIRED:
            self.assertEqual(listed[name], _md5(out2 / name))
        self.assertEqual(listed["table.md5"], _md5(out2 / "table.md5"))
        self.assertEqual(listed["table_row_counts.json"], _md5(out2 / "table_row_counts.json"))
        self.assertNotIn(fz.FROZEN_MANIFEST_NAME, listed)
        self.assertNotIn("model.md5", listed)
        # every listed file matches
        for name, md5 in listed.items():
            self.assertEqual(_md5(out2 / name), md5)

    def test_frozen_manifest_refusals(self) -> None:
        base = ["--table", str(self.table), "--out-dir", str(self.tmp / "o4"), "--frozen-manifest"]
        ok_state = ("c" * 40, False)
        cases = {
            "skip nested": (base + ["--expect-commit", "c" * 40, "--skip-nested-lodo"], ok_state),
            "no expect-commit": (base, ok_state),
            "wrong commit": (base + ["--expect-commit", "d" * 40], ok_state),
            "dirty tree": (base + ["--expect-commit", "c" * 40], ("c" * 40, True)),
            "repo state unknown": (base + ["--expect-commit", "unknown"], ("unknown", True)),
        }
        for name, (argv, state) in cases.items():
            with self.subTest(name), mock.patch.object(fz, "_git_state", return_value=state), self.assertRaises(SystemExit):
                fz.main(argv)
        self.assertFalse((self.tmp / "o4").exists())

    def test_the_first_completed_freeze_is_binding(self) -> None:
        out = self.tmp / "o5"
        out.mkdir()
        (out / fz.FROZEN_MANIFEST_NAME).write_text("x\n")
        with mock.patch.object(fz, "_git_state", return_value=("c" * 40, False)), self.assertRaises(SystemExit) as cm:
            fz.main(["--table", str(self.table), "--out-dir", str(out), "--frozen-manifest", "--expect-commit", "c" * 40])
        self.assertIn("binding", str(cm.exception))

    def test_main_without_the_flag_writes_no_manifest(self) -> None:
        out3 = self.tmp / "out3"
        fz.main(["--table", str(self.table), "--out-dir", str(out3), "--skip-nested-lodo"])
        self.assertFalse((out3 / fz.FROZEN_MANIFEST_NAME).exists())
        self.assertFalse((out3 / "table.md5").exists())

    def test_table_with_roots_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            fz.main(["--table", str(self.table), "--out-dir", str(self.tmp / "o"), "--fast-dir", str(self.tmp / "x")])


class LoaderRootsEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        cls.fast, cls.ins, cls.live = td / "fast", td / "ins", td / "live"
        write_fast_format_root(cls.fast, ex.POOL_HOURS, {ex.POOL_HOURS[10]: ["mintA1", "mintA2"]})
        write_fast_format_root(cls.ins, ia.POOL_C_HOURS, {ia.POOL_C_HOURS[10]: ["mintC1"]})
        for h in la.POOL_B_HOURS:
            write_zst_jsonl(cls.live / "trades" / f"trades-{h}.jsonl.zst", [])
        (cls.live / "creates").mkdir(parents=True, exist_ok=True)
        for d in la.POOL_B_CREATE_DAYS:
            (cls.live / "creates" / f"observe-{d}.jsonl").write_text("")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def test_default_paths_still_give_the_golden_rows(self) -> None:
        # The old way: no root arguments; the module-level default paths point at the fixture.
        with mock.patch.object(ex, "BACKFILL", self.fast), mock.patch.object(ia, "BACKFILL_C", self.ins), mock.patch.object(la, "BACKFILL_B", self.live):
            rows, manifest = fz.load_tp50_rows(max_workers=1, buffer_hours=2)
        self.assertEqual(len(rows), GOLDEN_LOADER_ROWS_N)
        self.assertEqual(_rows_md5(rows), GOLDEN_LOADER_ROWS_MD5)
        self.assertNotIn("roots", manifest)

    def test_explicit_roots_give_the_same_rows_without_patching(self) -> None:
        rows, manifest = fz.load_tp50_rows(max_workers=1, buffer_hours=2, fast_dir=self.fast, insample_dir=self.ins, live_dir=self.live)
        self.assertEqual(len(rows), GOLDEN_LOADER_ROWS_N)
        self.assertEqual(_rows_md5(rows), GOLDEN_LOADER_ROWS_MD5)
        self.assertEqual(manifest["roots"], {"A": str(self.fast), "C": str(self.ins), "B": str(self.live)})

    def test_build_table_cli_threads_roots_and_writes_to_out_dir(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            argv = [
                "--out-dir", out, "--max-workers", "1", "--buffer-hours", "2", "--max-home-hours", "0",
                "--fast-dir", str(self.fast), "--oracle-insample-dir", str(self.ins), "--oracle-live-dir", str(self.live),
            ]
            # explicit roots force the recipe's table settings and --verify-view
            with self.assertRaises(SystemExit):
                build_table_mod.main(argv + ["--verify-view"])
            self.assertFalse((Path(out) / "table.jsonl").exists())
            argv = [a for a in argv if a not in ("1", "2", "0", "--max-workers", "--buffer-hours", "--max-home-hours")]
            argv = [a for a in argv] + ["--max-workers", "2", "--buffer-hours", "24", "--max-home-hours", "12", "--verify-view"]
            # the fixture roots are not the pinned clean views: stub the content/pin checks (covered separately)
            with mock.patch.object(fz, "verify_view_sha256", return_value=1), mock.patch.object(fz, "check_view_pin", return_value="x"):
                build_table_mod.main(argv)
            table = Path(out) / "table.jsonl"
            lines = [json.loads(x) for x in table.read_text().splitlines() if x.strip()]
            self.assertEqual(len(lines), GOLDEN_LOADER_ROWS_N)
            self.assertEqual(_rows_md5(lines), GOLDEN_LOADER_ROWS_MD5)
            self.assertEqual((Path(out) / "table.md5").read_text().strip(), _md5(table))
            counts = json.loads((Path(out) / "row_counts.json").read_text())
            self.assertEqual(counts["manifest"]["roots"]["A"], str(self.fast))
            self.assertIn("view_sha256_file_sha256", counts["manifest"])
            self.assertIs(counts["manifest"]["verify_view"], True)
            self.assertEqual((counts["max_workers"], counts["buffer_hours"], counts["max_home_hours"]), (2, 24, 12))
            self.assertTrue((Path(out) / "scratch").is_dir())

    def test_roots_without_verify_view_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            argv = ["--out-dir", out, "--fast-dir", str(self.fast), "--oracle-insample-dir", str(self.ins), "--oracle-live-dir", str(self.live)]
            with self.assertRaises(SystemExit) as cm:
                build_table_mod.main(argv)
            self.assertIn("--verify-view", str(cm.exception))
            self.assertFalse((Path(out) / "table.jsonl").exists())

    def test_build_table_function_refuses_wrong_settings_or_missing_verify_view(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            kw = dict(fast_dir=self.fast, insample_dir=self.ins, live_dir=self.live)
            for settings in ({"max_workers": 1}, {"buffer_hours": 2}, {"max_home_hours": None}):
                with self.subTest(settings), self.assertRaises(SystemExit):
                    build_table_mod.build_table(Path(out) / "t.jsonl", Path(out) / "s", verify_view=True, **kw, **settings)
            with self.assertRaises(SystemExit):
                build_table_mod.build_table(Path(out) / "t.jsonl", Path(out) / "s", **kw)

    def test_build_table_defaults_call_the_loaders_with_no_override(self) -> None:
        seen: dict = {}

        def fake_load(**kw):
            seen.update(kw)
            return [], {"pools": {}, "n_rows_total": 0, "days": []}

        with tempfile.TemporaryDirectory() as out, mock.patch.object(build_table_mod, "load_tp50_rows", fake_load):
            build_table_mod.main(["--out", str(Path(out) / "t.jsonl"), "--scratch-dir", str(Path(out) / "s")])
        self.assertIsNone(seen["fast_dir"])
        self.assertIsNone(seen["insample_dir"])
        self.assertIsNone(seen["live_dir"])
        self.assertEqual(seen["max_home_hours"], 12)
        self.assertEqual(seen["buffer_hours"], 24)


class ForwardingTests(unittest.TestCase):
    def test_roots_reach_the_three_pool_loaders(self) -> None:
        seen: dict = {}

        def rec(name):
            def fn(**kw):
                seen[name] = kw
                return []

            return fn

        with mock.patch.object(fz, "run_all_features_a", rec("a")), mock.patch.object(fz, "run_all_features_c", rec("c")), mock.patch.object(
            fz, "run_all_features_b", rec("b")
        ):
            fz.load_tp50_rows(fast_dir=Path("/x/f"), insample_dir=Path("/x/i"), live_dir=Path("/x/l"))
            self.assertEqual(seen["a"]["backfill"], Path("/x/f"))
            self.assertEqual(seen["c"]["root"], Path("/x/i"))
            self.assertEqual(seen["b"]["root"], Path("/x/l"))
            fz.load_tp50_rows()
            self.assertIsNone(seen["a"]["backfill"])
            self.assertIsNone(seen["c"]["root"])
            self.assertIsNone(seen["b"]["root"])


class RootFenceTests(unittest.TestCase):
    def test_holdout_locations_are_refused_lexically(self) -> None:
        for bad in (
            "/data/mal/blocks",
            "/data/mal/blocks/fresh-0903/w1",
            "/data/mal/blocks/fresh-0903/../fresh-0903/w2",
            "/var/lib/mal/backfill-fast-b",
            "/var/lib/mal/backfill-fast-c/trades",
        ):
            with self.subTest(bad), self.assertRaises(SystemExit):
                fz.assert_root_allowed(bad, "fast")

    def test_ordinary_roots_pass(self) -> None:
        for ok in ("/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00", "/var/lib/mal/backfill-fast", "/data/mal/blocks-not"):
            with self.subTest(ok):
                self.assertEqual(fz.assert_root_allowed(ok, "fast"), Path(ok))

    def test_fence_does_not_touch_the_filesystem(self) -> None:
        with mock.patch("os.stat", side_effect=AssertionError("stat")), mock.patch("os.lstat", side_effect=AssertionError("lstat")), mock.patch(
            "os.listdir", side_effect=AssertionError("listdir")
        ):
            with self.assertRaises(SystemExit):
                fz.assert_root_allowed("/data/mal/blocks/x", "fast")

    def test_pools_never_reach_the_fresh_block(self) -> None:
        fz._assert_never_block(["2026-09-19T01", "2026-09-28T00"], "pool")
        with self.assertRaises(AssertionError):
            fz._assert_never_block(["2026-09-03T12"], "pool")
        with self.assertRaises(AssertionError):
            fz._assert_never_block(["2026-09-08T23"], "pool")
        fz._assert_never_block(["2026-09-09T12"], "pool")  # end is exclusive
        fz._assert_never_block(["2026-09-03T11"], "pool")


class ViewShaTests(unittest.TestCase):
    def test_dot_slash_lines_verify_and_escaping_paths_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "t").mkdir(parents=True)
            (root / "t" / "a.zst").write_bytes(b"alpha")
            (Path(td) / "outside").write_bytes(b"alpha")
            h = hashlib.sha256(b"alpha").hexdigest()
            (root / "VIEW.sha256").write_text(f"{h}  ./t/a.zst\n")
            self.assertEqual(fz.verify_view_sha256(root), 1)
            for bad in ("../outside", "t/../../outside", "/etc/hostname", ".."):
                (root / "VIEW.sha256").write_text(f"{h}  {bad}\n")
                with self.subTest(bad), self.assertRaises(SystemExit) as cm:
                    fz.verify_view_sha256(root)
                self.assertIn("escapes the root", str(cm.exception))

    def test_view_pins_are_the_preregistered_hashes(self) -> None:
        self.assertEqual(
            fz.VIEW_SHA256_PINS,
            {
                "fast-pool-2026-09-18T23_2026-09-22T00": "05486f70f53c7ef848b151f40d310ecc16e3ef517ff98ed7d4348250a32effe8",
                "oracle-insample-2026-09-22_25": "ab4fa8b058a1a3b35c7b090b89840b6d9135aede3cd08cc64516a9446d05b2c3",
                "oracle-live-2026-09-25_27": "a765603e535cb6757e7fe9227355f315f82fb603239f99fa200d8d9abae09251",
            },
        )

    def test_check_view_pin_refuses_a_mismatch_and_an_unknown_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "oracle-live-2026-09-25_27"
            root.mkdir()
            (root / "VIEW.sha256").write_text("not the pinned file\n")
            with self.assertRaises(SystemExit) as cm:
                fz.check_view_pin(root)
            self.assertIn("pins", str(cm.exception))
            other = Path(td) / "some-other-root"
            other.mkdir()
            (other / "VIEW.sha256").write_text("x\n")
            with self.assertRaises(SystemExit) as cm:
                fz.check_view_pin(other)
            self.assertIn("not one of the pinned", str(cm.exception))

    def test_check_view_pin_accepts_the_pinned_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "oracle-insample-2026-09-22_25"
            root.mkdir()
            (root / "VIEW.sha256").write_bytes(b"x")
            with mock.patch.dict(fz.VIEW_SHA256_PINS, {"oracle-insample-2026-09-22_25": hashlib.sha256(b"x").hexdigest()}):
                self.assertEqual(fz.check_view_pin(root), hashlib.sha256(b"x").hexdigest())

    def test_partial_roots_are_refused(self) -> None:
        import argparse

        ap = argparse.ArgumentParser()
        fz.add_root_args(ap)
        for argv in (["--fast-dir", "/x/f", "--verify-view"], ["--oracle-insample-dir", "/x/i", "--oracle-live-dir", "/x/l", "--verify-view"]):
            with self.subTest(argv), self.assertRaises(SystemExit) as cm:
                fz.resolve_roots(ap.parse_args(argv))
            self.assertIn("all three", str(cm.exception))

    def _make(self, root: Path) -> None:
        (root / "trades").mkdir(parents=True)
        (root / "trades" / "a.zst").write_bytes(b"alpha")
        (root / "trades" / "b.zst").write_bytes(b"beta")
        lines = [f"{hashlib.sha256(b'alpha').hexdigest()}  trades/a.zst", f"{hashlib.sha256(b'beta').hexdigest()} *trades/b.zst"]
        (root / "VIEW.sha256").write_text("\n".join(lines) + "\n")

    def test_good_view_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._make(Path(td))
            self.assertEqual(fz.verify_view_sha256(Path(td)), 2)

    def test_mismatch_missing_and_absent_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make(root)
            (root / "trades" / "a.zst").write_bytes(b"tampered")
            with self.assertRaises(SystemExit) as cm:
                fz.verify_view_sha256(root)
            self.assertIn("mismatch", str(cm.exception))
            (root / "trades" / "b.zst").unlink()
            with self.assertRaises(SystemExit) as cm:
                fz.verify_view_sha256(root)
            self.assertIn("missing file", str(cm.exception))
            (root / "VIEW.sha256").unlink()
            with self.assertRaises(SystemExit):
                fz.verify_view_sha256(root)

    def test_unparsable_line_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "VIEW.sha256").write_text("not a hash line\n")
            with self.assertRaises(SystemExit):
                fz.verify_view_sha256(root)

    def test_resolve_roots_checks_completeness_when_all_three_given(self) -> None:
        import argparse

        ap = argparse.ArgumentParser()
        fz.add_root_args(ap)
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "empty"
            args = ap.parse_args(["--fast-dir", str(empty), "--oracle-insample-dir", str(empty), "--oracle-live-dir", str(empty), "--verify-view"])
            with self.assertRaises(SystemExit) as cm:
                fz.resolve_roots(args)
            self.assertIn("required file(s) missing", str(cm.exception))

    def test_roots_without_verify_view_refused_by_resolve_roots(self) -> None:
        import argparse

        ap = argparse.ArgumentParser()
        fz.add_root_args(ap)
        with self.assertRaises(SystemExit) as cm:
            fz.resolve_roots(ap.parse_args(["--fast-dir", "/x/f", "--oracle-insample-dir", "/x/i", "--oracle-live-dir", "/x/l"]))
        self.assertIn("--verify-view", str(cm.exception))

    def test_resolve_roots_default_is_all_none(self) -> None:
        import argparse

        ap = argparse.ArgumentParser()
        fz.add_root_args(ap)
        self.assertEqual(fz.resolve_roots(ap.parse_args([])), {"fast": None, "insample": None, "live": None})

    def test_resolve_roots_verify_view_needs_a_root(self) -> None:
        import argparse

        ap = argparse.ArgumentParser()
        fz.add_root_args(ap)
        with self.assertRaises(SystemExit):
            fz.resolve_roots(ap.parse_args(["--verify-view"]))


class RepoStateQueryTests(unittest.TestCase):
    def test_state_query_covers_untracked_files_under_tools_schemas_and_out_dir(self) -> None:
        calls: list[list[str]] = []

        def fake(cmd, **kw):
            calls.append(list(cmd))
            return b"abc\n" if cmd[1] == "rev-parse" else b""

        repo = Path(fz.__file__).resolve().parents[1]
        with mock.patch.object(fz.subprocess, "check_output", fake):
            self.assertEqual(fz._git_state(repo / "ARTIFACTS" / "exp012"), ("abc", False))
        status = [c for c in calls if c[1] == "status" and "--untracked-files=all" in c]
        self.assertEqual(len(status), 1)
        self.assertEqual(status[0][status[0].index("--") + 1 :], ["tools", "schemas", "ARTIFACTS/exp012"])

    def test_untracked_output_reports_dirty(self) -> None:
        def fake(cmd, **kw):
            if cmd[1] == "rev-parse":
                return b"abc\n"
            return b"?? tools/shadow.py\n" if "--untracked-files=all" in cmd else b""

        with mock.patch.object(fz.subprocess, "check_output", fake):
            self.assertEqual(fz._git_state(), ("abc", True))


class ManifestParseTests(unittest.TestCase):
    def test_round_trip_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m"
            p.write_text("d41d8cd98f00b204e9800998ecf8427e  a.json\n")
            self.assertEqual(fz.parse_md5_manifest(p), {"a.json": "d41d8cd98f00b204e9800998ecf8427e"})
            p.write_text("garbage\n")
            with self.assertRaises(ValueError):
                fz.parse_md5_manifest(p)
            p.write_text("d41d8cd98f00b204e9800998ecf8427e  a\nd41d8cd98f00b204e9800998ecf8427e  a\n")
            with self.assertRaises(ValueError):
                fz.parse_md5_manifest(p)


if __name__ == "__main__":
    unittest.main()
