"""Tests for the B3 data-root flags (--fast-dir, --oracle-insample-dir,
--oracle-live-dir, --no-checkpoint). Tmp-dir fixtures only: no real data is
ever read, and no model is run.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.exploration_entry_model_b3 as b3
from tools.exploration_entry_model_b2 import build_creator_history_b
from tools.exploration_exits import POOL_HOURS as POOL_A_HOURS, _hour_info
from tools.oracle_insample_adapter import POOL_C_HOURS, _hour_info_c
from tools.oracle_live_adapter import POOL_B_CREATE_DAYS, POOL_B_HOURS, _create_day_file, _hour_info_b


class _Stop(Exception):
    pass


def _touch(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _build_fixture(tmp: Path) -> tuple[Path, Path, Path]:
    fast, insample, live = tmp / "fast", tmp / "insample", tmp / "live"
    for root, hours in ((fast, POOL_A_HOURS), (insample, POOL_C_HOURS)):
        for h in hours:
            for sub in ("trades", "creates", "migrations"):
                _touch(root / sub / f"{sub}-{h}.jsonl.zst")
    for h in POOL_B_HOURS:
        _touch(live / "trades" / f"trades-{h}.jsonl.zst")
    for d in POOL_B_CREATE_DAYS:
        _touch(live / "creates" / f"observe-{d}.jsonl")
    return fast, insample, live


class DefaultsTest(unittest.TestCase):
    def test_defaults_are_the_old_paths(self) -> None:
        self.assertEqual(b3.FAST_DIR_DEFAULT, Path("/var/lib/mal/backfill-fast"))
        self.assertEqual(b3.INSAMPLE_DIR_DEFAULT, Path("/home/claude/data/oracle-insample-2026-09-22_25"))
        self.assertEqual(b3.LIVE_DIR_DEFAULT, Path("/home/claude/data/oracle-live-2026-09-25_27"))
        self.assertIsNone(b3._root_or_none(b3.FAST_DIR_DEFAULT, b3.FAST_DIR_DEFAULT))

    def test_main_default_args_pass_no_override(self) -> None:
        seen: dict[str, dict] = {}

        def rec(name: str, ret):
            def fn(**kw):
                seen[name] = kw
                if isinstance(ret, Exception):
                    raise ret
                return ret

            return fn

        with mock.patch.object(sys, "argv", ["b3"]), mock.patch.object(b3, "run_all_features_a", rec("a", [])), mock.patch.object(
            b3, "run_all_features_c", rec("c", [])
        ), mock.patch.object(b3, "run_all_features_b", rec("b", _Stop())):
            with self.assertRaises(_Stop):
                b3.main()
        self.assertIsNone(seen["a"]["backfill"])
        self.assertIsNone(seen["c"]["root"])
        self.assertIsNone(seen["b"]["root"])


class FlagsReachLoadersTest(unittest.TestCase):
    def test_main_threads_flags_to_all_three_pools(self) -> None:
        seen: dict[str, dict] = {}

        def rec(name: str, ret):
            def fn(**kw):
                seen[name] = kw
                if isinstance(ret, Exception):
                    raise ret
                return ret

            return fn

        argv = ["b3", "--fast-dir", "/x/fast", "--oracle-insample-dir", "/x/ins", "--oracle-live-dir", "/x/live"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(b3, "run_all_features_a", rec("a", [])), mock.patch.object(
            b3, "run_all_features_c", rec("c", [])
        ), mock.patch.object(b3, "run_all_features_b", rec("b", _Stop())):
            with self.assertRaises(_Stop):
                b3.main()
        self.assertEqual(seen["a"]["backfill"], Path("/x/fast"))
        self.assertEqual(seen["c"]["root"], Path("/x/ins"))
        self.assertEqual(seen["b"]["root"], Path("/x/live"))

    def test_resolvers_read_from_given_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fast, insample, live = _build_fixture(Path(td))
            a = _hour_info(POOL_A_HOURS[0], fast)
            self.assertEqual(a["trade"], fast / "trades" / f"trades-{POOL_A_HOURS[0]}.jsonl.zst")
            self.assertEqual(a["create"], fast / "creates" / f"creates-{POOL_A_HOURS[0]}.jsonl.zst")
            c = _hour_info_c(POOL_C_HOURS[0], insample)
            self.assertEqual(c["trade"], insample / "trades" / f"trades-{POOL_C_HOURS[0]}.jsonl.zst")
            self.assertEqual(c["create"], insample / "creates" / f"creates-{POOL_C_HOURS[0]}.jsonl.zst")
            b = _hour_info_b(POOL_B_HOURS[0], live)
            self.assertEqual(b["trade"], live / "trades" / f"trades-{POOL_B_HOURS[0]}.jsonl.zst")
            self.assertEqual(_create_day_file(POOL_B_CREATE_DAYS[0], live), live / "creates" / f"observe-{POOL_B_CREATE_DAYS[0]}.jsonl")

    def test_creator_history_reads_given_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _fast, insample, live = _build_fixture(Path(td))
            # Plain .jsonl is accepted by the same _hour_file resolver.
            row = {"type": "create", "creator": "CrEaToR", "block_time": 1_790_000_000}
            (insample / "creates" / f"creates-{POOL_C_HOURS[0]}.jsonl.zst").unlink()
            _touch(insample / "creates" / f"creates-{POOL_C_HOURS[0]}.jsonl", json.dumps(row) + "\n")
            self.assertEqual(b3.build_creator_history_c(insample), {"CrEaToR": [1_790_000_000_000]})
            # Pool B: PumpPortal observe rows are empty in the fixture -> no creators.
            self.assertEqual(build_creator_history_b(live), {})

    def test_missing_root_fails_loudly_not_silently(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit):
                _hour_info_c(POOL_C_HOURS[0], Path(td) / "nope")
            with self.assertRaises(SystemExit):
                _hour_info_b(POOL_B_HOURS[0], Path(td) / "nope")
            with self.assertRaises(SystemExit):
                _hour_info(POOL_A_HOURS[0], Path(td) / "nope")


class NoCheckpointTest(unittest.TestCase):
    def test_complete_fixture_passes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            b3.verify_pool_files(*_build_fixture(Path(td)))

    def test_missing_hour_fails_loudly_in_each_pool(self) -> None:
        cases = (
            ("fast", "creates", f"creates-{POOL_A_HOURS[5]}.jsonl.zst"),
            ("insample", "migrations", f"migrations-{POOL_C_HOURS[7]}.jsonl.zst"),
            ("live", "trades", f"trades-{POOL_B_HOURS[9]}.jsonl.zst"),
        )
        for root_name, sub, fname in cases:
            with self.subTest(root_name):
                with tempfile.TemporaryDirectory() as td:
                    fast, insample, live = _build_fixture(Path(td))
                    root = {"fast": fast, "insample": insample, "live": live}[root_name]
                    (root / sub / fname).unlink()
                    with self.assertRaises(SystemExit) as cm:
                        b3.verify_pool_files(fast, insample, live)
                    self.assertIn(fname, str(cm.exception))
                    self.assertIn("1 required file(s) missing", str(cm.exception))

    def test_missing_observe_day_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fast, insample, live = _build_fixture(Path(td))
            (live / "creates" / f"observe-{POOL_B_CREATE_DAYS[1]}.jsonl").unlink()
            with self.assertRaises(SystemExit):
                b3.verify_pool_files(fast, insample, live)

    def test_main_flag_fails_before_any_pool_runs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "empty"
            argv = ["b3", "--fast-dir", str(empty), "--oracle-insample-dir", str(empty), "--oracle-live-dir", str(empty), "--no-checkpoint"]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(b3, "run_all_features_a") as run_a:
                with self.assertRaises(SystemExit):
                    b3.main()
                run_a.assert_not_called()


if __name__ == "__main__":
    unittest.main()
