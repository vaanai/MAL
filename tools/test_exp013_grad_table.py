"""Tests for tools/exp013_grad_table.py: guards, summaries, and an end-to-end build over
synthetic fixture roots (no host path is read)."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp013_grad_table as gtab
import tools.exploration_exits as ex
import tools.oracle_insample_adapter as ia
import tools.oracle_live_adapter as la
from tools.exp012_fixtures import hour_start_s, write_fast_format_root, write_zst_jsonl
from tools.exp013_fixtures import hours_between, write_view_sha256

SOL = 1_000_000_000
B0, Q0 = 438_000_000_000_000, 73_500_000_000


def _trade(mint: str, slot: int, t_s: int, venue: str, side: str, q: int, b: int, sol: int, trader: str, sig: str) -> dict:
    r = {
        "type": "trade", "venue": venue, "side": side, "quote_reserve": q, "base_reserve": b, "slot": slot, "event_index": 1,
        "sol_lamports": sol, "trader": trader, "token_raw": 1000, "mint": mint, "t_recv_ms": t_s * 1000, "block_time": t_s, "signature": sig,
    }
    if venue == "pumpswap":
        r["quote_is_wsol"] = True
    return r


def write_grad_root(root: Path, mint: str = "gradM", hour_idx: int = 10, with_late_print: bool = True) -> str:
    """A pool-A root: every hour present, one mint that creates, trades up to 80% progress, migrates."""
    write_fast_format_root(root, ex.POOL_HOURS, {})
    h = ex.POOL_HOURS[hour_idx]
    t0 = hour_start_s(h) + 60
    create = {"type": "create", "mint": mint, "slot": 900, "block_time": t0, "creator": "cr", "signature": "sc", "quote_reserve": 30_000_000_000, "base_reserve": 1_073_000_000_000_000}
    trades = [
        _trade(mint, 901, t0 + 5, "pump_bonding", "buy", 40_000_000_000, 800_000_000_000_000, SOL, "a", "s1"),
        _trade(mint, 1000, t0 + 100, "pump_bonding", "buy", Q0, B0, 3 * SOL, "c", "s3"),
        _trade(mint, 1002, t0 + 101, "pump_bonding", "buy", Q0, B0, SOL, "d", "s4"),
        _trade(mint, 1004, t0 + 102, "pump_bonding", "buy", 74_000_000_000, 437_000_000_000_000, SOL, "e", "s5"),
        _trade(mint, 1100, t0 + 140, "pumpswap", "buy", 85_000_000_000, 206_900_000_000_000, SOL, "f", "s6"),
    ]
    if with_late_print:
        trades.append(_trade(mint, 9000, t0 + 140 + 2000, "pumpswap", "sell", 86_000_000_000, 205_000_000_000_000, SOL, "g", "s7"))
    write_zst_jsonl(root / "trades" / f"trades-{h}.jsonl.zst", trades)
    write_zst_jsonl(root / "creates" / f"creates-{h}.jsonl.zst", [create])
    write_view_sha256(root)
    return h


def write_pool_c(root: Path) -> None:
    write_fast_format_root(root, ia.POOL_C_HOURS, {})
    write_view_sha256(root)


def write_pool_b(root: Path) -> None:
    for h in la.POOL_B_HOURS:
        write_zst_jsonl(root / "trades" / f"trades-{h}.jsonl.zst", [])
    (root / "creates").mkdir(parents=True, exist_ok=True)
    for d in la.POOL_B_CREATE_DAYS:
        (root / "creates" / f"observe-{d}.jsonl").write_text("")
    write_view_sha256(root)


def argv_for(td: Path, run_id: str, *extra: str) -> list[str]:
    return [
        "--run-id", run_id, "--out-root", str(td / "out"), "--verify-view", "--max-workers", "1",
        "--fast-dir", str(td / "fast"), "--oracle-insample-dir", str(td / "ins"), "--oracle-live-dir", str(td / "live"), *extra,
    ]


_REAL_VERIFY = fz.verify_view_sha256


def run_main(argv: list[str]) -> int:
    """The three built-in roots' VIEW.sha256 pins belong to the real data, so they are mocked;
    an extra view (a directory named xview) is verified for real."""

    def verify(root: Path) -> int:
        return _REAL_VERIFY(root) if Path(root).name == "xview" else 1

    with mock.patch.object(fz, "verify_view_sha256", side_effect=verify), mock.patch.object(fz, "check_view_pin", return_value="x"):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            return gtab.main(argv)


class FenceTests(unittest.TestCase):
    def test_builtin_pools_pass_and_count(self) -> None:
        n = gtab.assert_hour_fence(gtab.pools_hours(None))
        self.assertEqual(n, len(ex.POOL_HOURS) + len(ia.POOL_C_HOURS) + len(la.POOL_B_HOURS))

    def test_forbidden_blocks_are_refused(self) -> None:
        for h in ("2026-08-28T12", "2026-09-03T12", "2026-09-09T12", "2026-09-15T12", "2026-09-18T22", "2026-09-28T00", "2026-10-02T15"):
            with self.subTest(h), self.assertRaisesRegex(SystemExit, "forbidden range"):
                gtab.assert_hour_fence({"X": [h]})

    def test_expansion_hours_and_the_pool_edges_pass(self) -> None:
        self.assertEqual(gtab.assert_hour_fence({"X": ["2026-08-14T12", "2026-08-28T11"], "A": ["2026-09-18T23"]}), 3)

    def test_an_hour_in_two_pools_is_refused(self) -> None:
        with self.assertRaisesRegex(SystemExit, "both pool"):
            gtab.assert_hour_fence({"A": ["2026-09-20T00"], "X": ["2026-09-20T00"]})


class OutputGuardTests(unittest.TestCase):
    def test_bad_run_id(self) -> None:
        for rid in ("", "a", "../x", "x y", "-bad"):
            with self.subTest(rid), self.assertRaisesRegex(SystemExit, "run-id"):
                gtab.assert_out_dir_allowed("/tmp", rid)

    def test_forbidden_output_locations(self) -> None:
        for root in ("/data/mal/blocks", "/data/mal/blocks-clean", "/data/mal/clean-view/fresh-0903", "/var/lib/mal/backfill-fast-b", "/data/mal/exp012"):
            with self.subTest(root), self.assertRaisesRegex(SystemExit, "forbidden"):
                gtab.assert_out_dir_allowed(root, "run-001")

    def test_output_under_artifacts_is_refused(self) -> None:
        with self.assertRaisesRegex(SystemExit, "ARTIFACTS"):
            gtab.assert_out_dir_allowed(Path(gtab.__file__).resolve().parents[1] / "ARTIFACTS" / "x", "run-001")

    def test_symlink_into_a_forbidden_location_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            link = Path(td) / "link"
            link.symlink_to("/data/mal/blocks-clean")
            with self.assertRaisesRegex(SystemExit, "forbidden"):
                gtab.assert_out_dir_allowed(link, "run-001")

    def test_existing_run_dir_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "run-001").mkdir()
            (Path(td) / "run-001" / "table.jsonl").write_text("x")
            with self.assertRaisesRegex(SystemExit, "already holds files"):
                gtab.assert_out_dir_allowed(td, "run-001")

    def test_settings_are_fixed(self) -> None:
        for bad in ((3, 24, 12), (2, 2, 12), (2, 24, 0)):
            with self.subTest(bad), self.assertRaisesRegex(SystemExit, "fixed"):
                gtab.assert_recipe_settings(*bad)
        gtab.assert_recipe_settings(2, 24, 12)
        gtab.assert_recipe_settings(1, 24, 12)


class RootGuardTests(unittest.TestCase):
    def test_forbidden_root_is_refused_before_any_read(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ok = Path(td)
            for bad in ("/data/mal/blocks/fresh-0828/w1", "/data/mal/clean-view/fresh-0828/w2", "/data/mal/blocks/forward-1002", "/var/lib/mal/backfill-fast-c"):
                argv = ["--run-id", "run-001", "--out-root", str(ok / "o"), "--verify-view", "--fast-dir", bad, "--oracle-insample-dir", str(ok), "--oracle-live-dir", str(ok)]
                with self.subTest(bad), self.assertRaisesRegex(SystemExit, "reserved holdout"):
                    gtab.main(argv)

    def test_all_roots_and_verify_view_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(SystemExit, "all required"):
                gtab.main(["--run-id", "run-001", "--out-root", td, "--verify-view", "--fast-dir", td])
            with self.assertRaisesRegex(SystemExit, "verify-view"):
                gtab.main(["--run-id", "run-001", "--out-root", td, "--fast-dir", td, "--oracle-insample-dir", td, "--oracle-live-dir", td])

    def test_forbidden_extra_view_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fake = {"fast": Path(td), "insample": Path(td), "live": Path(td)}
            for bad in ("/data/mal/blocks-clean/fresh-0828/w1", "/data/mal/clean-view/fresh-0903/w2", "/data/mal/blocks/forward-1002"):
                with self.subTest(bad), mock.patch.object(gtab, "guarded_roots", return_value=fake), self.assertRaisesRegex(SystemExit, "forbidden"):
                    gtab.main(argv_for(Path(td), "run-001", "--extra-fast-view", bad))


class SummaryTests(unittest.TestCase):
    def test_triggers_equal_scored_plus_censored_at_every_k(self) -> None:
        def row(m: str, k: int, outcome: str, filled: bool = True, label: int = 1) -> dict:
            return {"mint": m, "day": "2026-09-20", "entry_land_k": k, "outcome": outcome, "filled": filled, "label": label}

        rows = [row("a", 4, "migrated"), row("b", 4, "stop", label=0), row("a", 8, "miss", filled=False, label=0)]
        cens = [{"mint": "b", "day": "2026-09-20", "entry_land_k": 8, "reason": "entry_past_tape"}]
        s = gtab.summarize(rows, cens, {"2026-09-20": 2, "2026-09-21": 1})
        d = s["by_day"]["2026-09-20"]
        self.assertEqual((s["triggers_total"], d["triggers"]), (3, 2))
        self.assertEqual(d["by_k"]["4"], {"scored": 2, "filled": 2, "label_press_pos": 1, "outcomes": {"migrated": 1, "stop": 1}, "censored": 0, "censored_reasons": {}})
        self.assertEqual(d["by_k"]["8"]["scored"] + d["by_k"]["8"]["censored"], d["triggers"])
        self.assertEqual(d["by_k"]["8"]["censored_reasons"], {"entry_past_tape": 1})
        self.assertEqual(s["by_day"]["2026-09-21"]["by_k"]["4"]["scored"], 0)


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        cls.hour = write_grad_root(td / "fast")
        write_pool_c(td / "ins")
        write_pool_b(td / "live")
        cls.td = td

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def test_dry_run_runs_the_guards_and_reads_no_tape(self) -> None:
        with mock.patch.object(gtab, "build_table", side_effect=AssertionError("dry run must not build")):
            self.assertEqual(run_main(argv_for(self.td, "dry-001", "--dry-run")), 0)
        self.assertFalse((self.td / "out" / "dry-001").exists())

    def test_build_writes_table_counts_censored_and_manifest(self) -> None:
        self.assertEqual(run_main(argv_for(self.td, "run-001")), 0)
        out = self.td / "out" / "run-001"
        rows = [json.loads(line) for line in (out / "table.jsonl").read_text().splitlines()]
        self.assertEqual(sorted(r["entry_land_k"] for r in rows), [1, 4, 8])
        self.assertEqual({r["mint"] for r in rows}, {"gradM"})
        self.assertEqual({r["pool"] for r in rows}, {"A"})
        self.assertEqual({r["outcome"] for r in rows}, {"migrated"})
        self.assertEqual(len(rows[0]["features"]), 22)
        counts = json.loads((out / "trigger_counts.json").read_text())
        day = self.hour[:10]
        self.assertEqual(counts["triggers_total"], 1)
        self.assertEqual(counts["by_day"][day]["triggers"], 1)
        self.assertEqual(counts["by_day"][day]["by_k"]["4"]["scored"], 1)
        man = json.loads((out / "manifest.json").read_text())
        self.assertEqual(man["n_rows"], 3)
        self.assertEqual(man["n_censored"], 0)
        self.assertEqual(man["table_md5"], (out / "table.md5").read_text().strip())
        self.assertEqual(set(man["view_sha256_file_sha256"]), {"fast", "insample", "live"})
        self.assertEqual(set(man["pinned_view_sha256"]), {"A", "C", "B"})
        self.assertEqual(man["settings"]["ks"], [1, 4, 8])
        self.assertEqual(man["settings"]["buffer_hours"], 24)
        self.assertIn("exp013_grad_trigger.py", man["code_sha256"])
        # a rerun is byte-identical and the run directory is never overwritten
        self.assertEqual(run_main(argv_for(self.td, "run-002")), 0)
        self.assertEqual((self.td / "out" / "run-002" / "table.md5").read_text(), (out / "table.md5").read_text())
        with self.assertRaisesRegex(SystemExit, "already holds files"):
            run_main(argv_for(self.td, "run-001"))

    def test_two_spawned_workers_give_the_same_table_as_one(self) -> None:
        argv = [a if a != "1" else "2" for a in argv_for(self.td, "run-w2")]
        self.assertEqual(argv[argv.index("--max-workers") + 1], "2")
        self.assertEqual(run_main(argv), 0)
        if not (self.td / "out" / "run-001" / "table.md5").exists():
            self.assertEqual(run_main(argv_for(self.td, "run-001")), 0)
        self.assertEqual((self.td / "out" / "run-w2" / "table.md5").read_text(), (self.td / "out" / "run-001" / "table.md5").read_text())

    def test_tape_ending_before_the_exit_is_counted_not_scored(self) -> None:
        with tempfile.TemporaryDirectory() as td2:
            t2 = Path(td2)
            write_grad_root(t2 / "fast", with_late_print=False)
            # without the migration print either, the cap runs past the tape
            h = ex.POOL_HOURS[10]
            rows = [json.loads(line) for line in _read_zst(t2 / "fast" / "trades" / f"trades-{h}.jsonl.zst")]
            write_zst_jsonl(t2 / "fast" / "trades" / f"trades-{h}.jsonl.zst", [r for r in rows if r["venue"] == "pump_bonding"])
            write_view_sha256(t2 / "fast")
            write_pool_c(t2 / "ins")
            write_pool_b(t2 / "live")
            self.assertEqual(run_main(argv_for(t2, "run-001")), 0)
            out = t2 / "out" / "run-001"
            self.assertEqual((out / "table.jsonl").read_text(), "")
            cens = [json.loads(line) for line in (out / "censored.jsonl").read_text().splitlines()]
            self.assertEqual({c["entry_land_k"]: c["reason"] for c in cens}, {1: "cap_past_tape", 4: "cap_past_tape", 8: "entry_past_tape"})
            counts = json.loads((out / "trigger_counts.json").read_text())
            self.assertEqual(counts["triggers_total"], 1)
            self.assertEqual(counts["by_day"][h[:10]]["by_k"]["4"]["censored"], 1)

    def test_extra_view_joins_as_pool_x(self) -> None:
        view = self.td / "xview"
        hours = hours_between("2026-08-20T00", "2026-08-20T05")
        write_fast_format_root(view, hours, {"2026-08-20T01": ["xm1"]})
        write_view_sha256(view)
        self.assertEqual(run_main(argv_for(self.td, "run-x", "--extra-fast-view", str(view))), 0)
        man = json.loads((self.td / "out" / "run-x" / "manifest.json").read_text())
        self.assertEqual(man["extra_views"][0]["n_hours"], 6)
        self.assertEqual(man["n_hours"], len(ex.POOL_HOURS) + len(ia.POOL_C_HOURS) + len(la.POOL_B_HOURS) + 6)


def _read_zst(path: Path) -> list[str]:
    import subprocess

    return subprocess.run(["zstd", "-dc", str(path)], check=True, capture_output=True, text=True).stdout.splitlines()


if __name__ == "__main__":
    unittest.main()
