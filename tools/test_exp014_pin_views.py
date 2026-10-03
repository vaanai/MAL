"""Tests for tools/exp014_pin_views.py and scripts/research/exp014-screen-run.sh. Fixtures only."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import timedelta
from pathlib import Path
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp013_grad_table as gtab
import tools.exp014_m15_screen as sc
import tools.exp014_m15_table as t14
import tools.exp014_pin_views as pv
import tools.exploration_exits as ex
import tools.oracle_insample_adapter as ia
from tools.exp013_fixtures import hours_between, write_view_sha256
from tools.exp013_pool import load_extra_views
from tools.exp012_fixtures import write_fast_format_root
from tools.test_exp013_grad_table import write_grad_root, write_pool_b, write_pool_c

REPO = Path(__file__).resolve().parent.parent
AFTER = sc.CUTOFF + timedelta(hours=1)
_FMT = "%Y-%m-%dT%H:%M:%SZ"
_REAL_VERIFY = fz.verify_view_sha256


def _verify(root: Path) -> int:
    return _REAL_VERIFY(root) if Path(root).name.startswith("x") else 1


def _view(root: Path, first: str, last: str) -> Path:
    write_fast_format_root(root, hours_between(first, last), {})
    write_view_sha256(root)
    return root


class PinViewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        td = cls.td = Path(cls._td.name)
        write_grad_root(td / "fast")
        write_pool_c(td / "ins")
        write_pool_b(td / "live")
        cls.v1 = _view(td / "x1", "2026-08-20T00", "2026-08-20T05")
        cls.v2 = _view(td / "x2", "2026-08-20T06", "2026-08-20T11")
        cls.late = _view(td / "xlate", "2026-08-21T00", "2026-08-21T05")
        cls.missing = td / "xmissing"
        cls.roots = {"A": td / "fast", "B": td / "live", "C": td / "ins"}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def _mtime(self, p: Path) -> float:
        late = p.parent.name == "xlate"
        return (AFTER if late else sc.CUTOFF - timedelta(days=1)).timestamp()

    def _pin(self, cands: list[Path], now=AFTER):
        with mock.patch.object(fz, "verify_view_sha256", side_effect=_verify), redirect_stderr(io.StringIO()):
            return pv.pin_views(self.roots, cands, now=now, mtime_fn=self._mtime)

    def test_cutoff_is_the_1005_cutoff(self) -> None:
        self.assertEqual(pv.CUTOFF, sc.CUTOFF)
        self.assertEqual(sc.CUTOFF.strftime(_FMT), "2026-10-05T12:00:00Z")

    def test_refuses_before_the_cutoff(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            self._pin([self.v1], now=sc.CUTOFF - timedelta(seconds=1))
        self.assertIn("before the view cutoff", str(cm.exception))

    def test_cli_refuses_before_the_cutoff_and_writes_nothing(self) -> None:
        out = self.td / "cli" / "m.json"
        argv = ["--fast-dir", str(self.roots["A"]), "--oracle-insample-dir", str(self.roots["C"]), "--oracle-live-dir", str(self.roots["B"]), "--out", str(out)]
        with mock.patch.object(pv, "_utc_now", return_value=sc.CUTOFF - timedelta(seconds=1)):
            with self.assertRaises(SystemExit):
                pv.main(argv)
        self.assertFalse(out.parent.exists())

    def test_late_and_missing_candidates_are_excluded_not_errors(self) -> None:
        m, args = self._pin([self.v1, self.late, self.missing, self.v2])
        r1, r2 = os.path.realpath(self.v1), os.path.realpath(self.v2)
        self.assertEqual([v["root"] for v in m["extra_views"]], [r1, r2])
        self.assertEqual(args, ["--extra-fast-view", r1, "--extra-fast-view", r2])
        self.assertEqual([e["root"] for e in m["excluded"]], [str(self.late), str(self.missing)])

    def test_pool_b_is_listed_as_guarded_but_excluded(self) -> None:
        m, _ = self._pin([self.v1, self.v2])
        self.assertEqual(sorted(m["pools"]), ["A", "B", "C"])
        self.assertEqual(m["excluded_pools"], ["B"])
        b = m["pools"]["B"]
        self.assertEqual(set(b), {"root", "view_sha256_file_sha256", "view_sha256_mtime_utc", "excluded"})
        self.assertTrue(b["excluded"])
        for tag in ("A", "C"):
            self.assertEqual(set(m["pools"][tag]), {"root", "hours", "view_sha256_file_sha256", "view_sha256_mtime_utc"})
        self.assertEqual(m["pools"]["A"]["hours"], [ex.POOL_HOURS[0], ex.POOL_HOURS[-1]])
        self.assertEqual(m["pools"]["C"]["hours"], [ia.POOL_C_HOURS[0], ia.POOL_C_HOURS[-1]])
        self.assertEqual(m["extra_views"][0]["hours"], ["2026-08-20T00", "2026-08-20T05"])

    def test_manifest_is_accepted_by_the_screen_against_a_table_manifest(self) -> None:
        m, args = self._pin([self.v1, self.late, self.v2])
        with mock.patch.object(fz, "verify_view_sha256", side_effect=_verify):
            extra = load_extra_views([Path(a) for a in args[1::2]], fz.DAYS_ALL, allow_gap=True)
        roots = {"fast": m["pools"]["A"]["root"], "insample": m["pools"]["C"]["root"], "live": m["pools"]["B"]["root"]}
        shas = {"fast": m["pools"]["A"]["view_sha256_file_sha256"], "insample": m["pools"]["C"]["view_sha256_file_sha256"], "live": m["pools"]["B"]["view_sha256_file_sha256"]}
        tman = {"pool_runs": {t: [list(x) for x in v] for t, v in t14.pool_runs(extra).items()}, "roots": roots, "view_sha256_file_sha256": shas,
                "extra_views": [v.describe() for v in extra], "excluded_pools": list(t14.EXCLUDED_POOLS)}
        sc.assert_view_manifest(m, tman, check_files=True, mtime_fn=self._mtime)
        self.assertNotIn("B", tman["pool_runs"])

    def test_cli_refuses_existing_output(self) -> None:
        out = self.td / "exists.json"
        out.write_text("{}")
        argv = ["--fast-dir", "a", "--oracle-insample-dir", "b", "--oracle-live-dir", "c", "--out", str(out)]
        with self.assertRaises(SystemExit):
            pv.main(argv)


class ScriptTests(unittest.TestCase):
    SCRIPT = REPO / "scripts" / "research" / "exp014-screen-run.sh"

    def _env(self, td: str, now: str) -> dict:
        return {**os.environ, "EXP014_NOW_OVERRIDE": now, "EXP014_DATA_ROOT": td, "PYTHONPATH": str(REPO)}

    def test_syntax(self) -> None:
        subprocess.run(["bash", "-n", str(self.SCRIPT)], check=True)

    def test_before_cutoff_refuses_and_touches_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run(["bash", str(self.SCRIPT), "run-test"], capture_output=True, text=True, env=self._env(td, "2026-10-05T11:59:59Z"), cwd=REPO)
            self.assertEqual(r.returncode, 3)
            self.assertIn("before cutoff", r.stderr)
            self.assertEqual(list(Path(td).iterdir()), [])

    def test_debug_dir_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "exp014-m15" / "debug-whatever").mkdir(parents=True)
            r = subprocess.run(["bash", str(self.SCRIPT), "run-test"], capture_output=True, text=True, env=self._env(td, "2026-10-05T12:00:00Z"), cwd=REPO)
            self.assertEqual(r.returncode, 4)
            self.assertIn("debug", r.stderr)
            self.assertEqual([p.name for p in (Path(td) / "exp014-m15").iterdir()], ["debug-whatever"])

    def test_missing_run_id_fails(self) -> None:
        r = subprocess.run(["bash", str(self.SCRIPT)], capture_output=True, text=True, cwd=REPO)
        self.assertEqual(r.returncode, 2)

    def test_script_wiring(self) -> None:
        text = self.SCRIPT.read_text()
        for needle in ("tools.exp014_pin_views", "tools.exp014_m15_table", "--allow-gap", "tools.exp014_m15_screen", "--n-jobs 8",
                       "MAL_TRIES_LOG=\"$DATA/ops/tries/tries.jsonl\"", "2026-10-05T12:00:00Z", "debug-*"):
            self.assertIn(needle, text)


class Exp013PinViewsUntouchedTests(unittest.TestCase):
    def test_exp013_pin_views_keeps_its_own_cutoff(self) -> None:
        import tools.exp013_pin_views as pv13

        self.assertEqual(pv13.VIEW_CUTOFF.strftime(_FMT), "2026-10-04T12:00:00Z")
        self.assertEqual(sorted(pv13.POOL_ARG), ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
