"""Tests for tools/exp013_pin_views.py and scripts/research/exp013-screen-run.sh. Fixtures only."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp013_grad_screen as gs
import tools.exp013_grad_table as gtab
import tools.exp013_pin_views as pv
import tools.exploration_exits as ex
import tools.oracle_insample_adapter as ia
import tools.oracle_live_adapter as la
from tools.exp012_fixtures import write_fast_format_root
from tools.exp013_fixtures import hours_between, write_view_sha256
from tools.test_exp013_grad_table import argv_for, write_grad_root, write_pool_b, write_pool_c

REPO = Path(__file__).resolve().parent.parent
AFTER = gs.VIEW_CUTOFF + timedelta(hours=1)
_REAL_VERIFY = fz.verify_view_sha256
_FMT = "%Y-%m-%dT%H:%M:%SZ"


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
        return (AFTER if late else gs.VIEW_CUTOFF - timedelta(days=1)).timestamp()

    def _pin(self, cands: list[Path], now: datetime = AFTER) -> tuple[dict, list[str]]:
        with mock.patch.object(fz, "verify_view_sha256", side_effect=_verify), redirect_stderr(io.StringIO()):
            return pv.pin_views(self.roots, cands, now=now, mtime_fn=self._mtime)

    def test_refuses_before_the_cutoff(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            self._pin([self.v1], now=gs.VIEW_CUTOFF - timedelta(seconds=1))
        self.assertIn("before the view cutoff", str(cm.exception))

    def test_cli_refuses_before_the_cutoff_and_writes_nothing(self) -> None:
        out = self.td / "cli" / "m.json"
        argv = ["--fast-dir", str(self.roots["A"]), "--oracle-insample-dir", str(self.roots["C"]), "--oracle-live-dir", str(self.roots["B"]), "--out", str(out)]
        with mock.patch.object(pv, "_utc_now", return_value=gs.VIEW_CUTOFF - timedelta(seconds=1)):
            with self.assertRaises(SystemExit):
                pv.main(argv)
        self.assertFalse(out.parent.exists())

    def test_late_and_missing_candidates_are_excluded_not_errors(self) -> None:
        m, args = self._pin([self.v1, self.late, self.missing, self.v2])
        r1, r2 = os.path.realpath(self.v1), os.path.realpath(self.v2)
        self.assertEqual([v["root"] for v in m["extra_views"]], [r1, r2])
        self.assertEqual(args, ["--extra-fast-view", r1, "--extra-fast-view", r2])
        self.assertEqual([e["root"] for e in m["excluded"]], [str(self.late), str(self.missing)])
        self.assertIn("after", m["excluded"][0]["reason"])
        self.assertIn("missing", m["excluded"][1]["reason"])

    def test_exact_keys_and_hours(self) -> None:
        m, _ = self._pin([self.v1, self.v2])
        self.assertEqual(sorted(m["pools"]), ["A", "B", "C"])
        keys = {"root", "hours", "view_sha256_file_sha256", "view_sha256_mtime_utc"}
        for v in [*m["pools"].values(), *m["extra_views"]]:
            self.assertEqual(set(v), keys)
        self.assertEqual(m["pools"]["A"]["hours"], [ex.POOL_HOURS[0], ex.POOL_HOURS[-1]])
        self.assertEqual(m["pools"]["C"]["hours"], [ia.POOL_C_HOURS[0], ia.POOL_C_HOURS[-1]])
        self.assertEqual(m["pools"]["B"]["hours"], [la.POOL_B_HOURS[0], la.POOL_B_HOURS[-1]])
        self.assertEqual(m["extra_views"][0]["hours"], ["2026-08-20T00", "2026-08-20T05"])
        self.assertEqual(m["pools"]["A"]["view_sha256_mtime_utc"], (gs.VIEW_CUTOFF - timedelta(days=1)).strftime(_FMT))

    def test_manifest_matches_the_table_the_builder_writes(self) -> None:
        m, args = self._pin([self.v1, self.late, self.v2])
        argv = argv_for(self.td, "run-pin", *args)
        with mock.patch.object(fz, "verify_view_sha256", side_effect=_verify), mock.patch.object(fz, "check_view_pin", return_value="x"):
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(gtab.main(argv), 0)
        tman = json.loads((self.td / "out" / "run-pin" / "manifest.json").read_text())
        exp = gs.expected_views(tman)

        def strip(v: dict) -> dict:
            return {k: v[k] for k in ("root", "hours", "view_sha256_file_sha256")}

        self.assertEqual({t: strip(v) for t, v in m["pools"].items()}, {t: strip(v) for t, v in exp["pools"].items()})
        self.assertEqual([strip(v) for v in m["extra_views"]], [strip(v) for v in exp["extra_views"]])
        gs.assert_view_manifest(m, tman, check_files=True, mtime_fn=self._mtime)

    def test_gap_between_included_views_pins_and_matches_the_table(self) -> None:
        w1 = _view(self.td / "xw1", "2026-08-22T00", "2026-08-22T05")
        w3 = _view(self.td / "xw3", "2026-08-22T12", "2026-08-22T17")
        w4 = _view(self.td / "xw4", "2026-08-22T18", "2026-08-22T23")
        m, args = self._pin([w1, w3, w4])
        self.assertEqual(m["gaps"], [{"from": "2026-08-22T06", "to": "2026-08-22T11", "n_hours": 6}])
        argv = argv_for(self.td, "run-gap", *args, "--allow-gap")
        with mock.patch.object(fz, "verify_view_sha256", side_effect=_verify), mock.patch.object(fz, "check_view_pin", return_value="x"):
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(gtab.main(argv), 0)
        tman = json.loads((self.td / "out" / "run-gap" / "manifest.json").read_text())
        exp = gs.expected_views(tman)

        def key(v: dict) -> tuple:
            return (v["root"], v["hours"], v["view_sha256_file_sha256"])

        self.assertEqual([key(v) for v in m["extra_views"]], [key(v) for v in exp["extra_views"]])
        gs.assert_view_manifest(m, tman, check_files=True, mtime_fn=self._mtime)


class ScriptTests(unittest.TestCase):
    SCRIPT = REPO / "scripts" / "research" / "exp013-screen-run.sh"

    def test_syntax(self) -> None:
        subprocess.run(["bash", "-n", str(self.SCRIPT)], check=True)

    def test_before_cutoff_refuses_and_touches_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env = {**os.environ, "EXP013_NOW_OVERRIDE": "2026-10-02T00:00:00Z", "EXP013_DATA_ROOT": td, "PYTHONPATH": str(REPO)}
            r = subprocess.run(["bash", str(self.SCRIPT), "run-test"], capture_output=True, text=True, env=env, cwd=REPO)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("before cutoff", r.stderr)
            self.assertEqual(list(Path(td).iterdir()), [])

    def test_missing_run_id_fails(self) -> None:
        r = subprocess.run(["bash", str(self.SCRIPT)], capture_output=True, text=True, cwd=REPO)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
