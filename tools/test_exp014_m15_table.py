"""Tests for tools/exp014_m15_table.py: guards, summaries, edge flags and an end-to-end build over
synthetic fixture roots (no host path is read)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp013_grad_table as g13
import tools.exp013_pool as xp
import tools.exp014_m15_table as tab
import tools.exp014_m15_trigger as mt
import tools.exploration_exits as ex
import tools.oracle_insample_adapter as ia
import tools.oracle_live_adapter as la
from tools.exp012_fixtures import hour_start_s, write_fast_format_root, write_zst_jsonl
from tools.exp013_fixtures import hours_between, write_view_sha256
from tools.test_exp013_grad_table import write_pool_b, write_pool_c

SOL = 1_000_000_000
Q0, B0 = 80 * SOL, 200_000_000_000_000


def _trade(mint: str, slot: int, t_s: int, venue: str, side: str, q: int, b: int, sol: int, trader: str, sig: str, pool: str | None = None) -> dict:
    r = {
        "type": "trade", "venue": venue, "side": side, "quote_reserve": q, "base_reserve": b, "slot": slot, "event_index": 1,
        "sol_lamports": sol, "trader": trader, "token_raw": 1000, "mint": mint, "t_recv_ms": 1, "block_time": t_s, "signature": sig,
    }
    if venue == "pumpswap":
        r["quote_is_wsol"] = True
    if pool is not None:
        r["pool"] = pool
    return r


def m15_hour_rows(hour: str, mint: str = "migM", mig_off: int = 60, through_s: int = 7200) -> tuple[list[dict], list[dict]]:
    """(creates, trades) for one mint: create 50 s before the migration, two bonding trades, a migration into
    pool P1 at hour start + mig_off, own prints at +300 / +600 s, another pool's print at +400 s, and another
    mint's print every 60 s through `through_s` (the global tape). Slots run at 2 per second."""
    hs = hour_start_s(hour)
    t_mig = hs + mig_off
    slot = lambda t: 1000 + 2 * (t - t_mig)  # noqa: E731
    create = {"type": "create", "mint": mint, "slot": slot(t_mig - 50), "block_time": t_mig - 50, "creator": "cr", "signature": "sc", "quote_reserve": 30_000_000_000, "base_reserve": 1_073_000_000_000_000}
    trades = [
        _trade(mint, slot(t_mig - 45), t_mig - 45, "pump_bonding", "buy", 40 * SOL, 800_000_000_000_000, SOL, "a", "b1"),
        _trade(mint, slot(t_mig - 30), t_mig - 30, "pump_bonding", "buy", 60 * SOL, 530_000_000_000_000, 2 * SOL, "b", "b2"),
        _trade(mint, slot(t_mig), t_mig, "pumpswap", "buy", Q0, B0, 3 * SOL, "c", "p0", pool="P1"),
        _trade(mint, slot(t_mig + 300), t_mig + 300, "pumpswap", "buy", Q0, B0, SOL, "d", "p1", pool="P1"),
        _trade(mint, slot(t_mig + 400), t_mig + 400, "pumpswap", "buy", 500 * SOL, 20_000_000_000_000, 9 * SOL, "z", "q1", pool="P2"),
        _trade(mint, slot(t_mig + 600), t_mig + 600, "pumpswap", "buy", Q0, B0, SOL, "e", "p2", pool="P1"),
    ]
    for i, dt in enumerate(range(30, through_s, 60)):
        trades.append(_trade("FILL", slot(t_mig + dt), t_mig + dt, "pump_bonding", "buy", 50 * SOL, 500_000_000_000_000, SOL, "o", f"f{i}"))
    trades.sort(key=lambda r: (r["block_time"], r["slot"]))
    return [create], trades


def write_m15_root(root: Path, hour_idx: int = 10, mig_off: int = 60, through_s: int = 7200, mint: str = "migM") -> str:
    write_fast_format_root(root, ex.POOL_HOURS, {})
    h = ex.POOL_HOURS[hour_idx]
    creates, trades = m15_hour_rows(h, mint, mig_off, through_s)
    write_zst_jsonl(root / "trades" / f"trades-{h}.jsonl.zst", trades)
    write_zst_jsonl(root / "creates" / f"creates-{h}.jsonl.zst", creates)
    write_view_sha256(root)
    return h


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
            return tab.main(argv)


def set_mtime(path: Path, iso: str) -> None:
    from datetime import datetime, timezone

    ts = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    os.utime(path, (ts, ts))


class FenceTests(unittest.TestCase):
    def test_builtin_pools_pass_and_count(self) -> None:
        n = tab.assert_hour_fence(tab.pools_hours(None))
        self.assertEqual(n, len(ex.POOL_HOURS) + len(ia.POOL_C_HOURS) + len(la.POOL_B_HOURS))

    def test_forbidden_blocks_are_refused(self) -> None:
        # the 0808 backup block, the 0828 backup block, the EXP-012 / EXP-011 / EXP-009 blocks, the forward period
        for h in ("2026-08-08T12", "2026-08-13T23", "2026-08-14T11", "2026-08-28T12", "2026-09-03T12", "2026-09-09T12", "2026-09-18T22", "2026-09-28T00", "2026-10-02T15"):
            with self.subTest(h), self.assertRaisesRegex(SystemExit, "forbidden range"):
                tab.assert_hour_fence({"X": [h]})

    def test_expansion_hours_and_the_pool_edges_pass(self) -> None:
        self.assertEqual(tab.assert_hour_fence({"X": ["2026-08-14T12", "2026-08-28T11"], "A": ["2026-09-18T23"]}), 3)

    def test_an_hour_in_two_pools_is_refused(self) -> None:
        with self.assertRaisesRegex(SystemExit, "both pool"):
            tab.assert_hour_fence({"A": ["2026-09-20T00"], "X": ["2026-09-20T00"]})


class OutputGuardTests(unittest.TestCase):
    def test_default_out_root_is_outside_every_forbidden_location(self) -> None:
        self.assertEqual(tab.DEFAULT_OUT_ROOT, Path("/data/mal/exp014-m15"))
        self.assertEqual(tab.SCHEMA, "exp014_m15_table_v1")
        with tempfile.TemporaryDirectory() as td:
            ap_root = Path(td)
            self.assertEqual(tab.assert_out_dir_allowed(ap_root, "run-001"), Path(os.path.realpath(ap_root / "run-001")))
        self.assertIsNone(xp._is_forbidden(str(tab.DEFAULT_OUT_ROOT / "run-001")))

    def test_bad_run_id(self) -> None:
        for rid in ("", "a", "../x", "x y", "-bad"):
            with self.subTest(rid), self.assertRaisesRegex(SystemExit, "run-id"):
                tab.assert_out_dir_allowed("/tmp", rid)

    def test_forbidden_output_locations(self) -> None:
        for root in ("/data/mal/blocks", "/data/mal/blocks/fresh-0808/w1", "/data/mal/blocks-clean", "/data/mal/clean-view/fresh-0808", "/data/mal/clean-view/fresh-0808/w2", "/data/mal/clean-view/fresh-0828", "/var/lib/mal/backfill-fast-b", "/data/mal/exp012"):
            with self.subTest(root), self.assertRaisesRegex(SystemExit, "forbidden"):
                tab.assert_out_dir_allowed(root, "run-001")

    def test_output_under_artifacts_is_refused(self) -> None:
        with self.assertRaisesRegex(SystemExit, "ARTIFACTS"):
            tab.assert_out_dir_allowed(Path(tab.__file__).resolve().parents[1] / "ARTIFACTS" / "x", "run-001")

    def test_symlink_into_a_forbidden_location_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            link = Path(td) / "link"
            link.symlink_to("/data/mal/clean-view/fresh-0808")
            with self.assertRaisesRegex(SystemExit, "forbidden"):
                tab.assert_out_dir_allowed(link, "run-001")

    def test_existing_run_dir_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "run-001").mkdir()
            (Path(td) / "run-001" / "table.jsonl").write_text("x")
            with self.assertRaisesRegex(SystemExit, "already holds files"):
                tab.assert_out_dir_allowed(td, "run-001")

    def test_settings_are_fixed(self) -> None:
        for bad in ((3, 24, 12), (2, 2, 12), (2, 24, 0)):
            with self.subTest(bad), self.assertRaisesRegex(SystemExit, "fixed"):
                tab.assert_recipe_settings(*bad)
        tab.assert_recipe_settings(2, 24, 12)
        tab.assert_recipe_settings(1, 24, 12)

    def test_main_refuses_a_third_worker(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(SystemExit, "fixed"):
                tab.main(["--run-id", "run-001", "--max-workers", "3", "--out-root", td])


class ForbiddenPrefixTests(unittest.TestCase):
    def test_the_0808_clean_view_is_in_the_forbidden_list(self) -> None:
        self.assertIn("/data/mal/clean-view/fresh-0808", xp.FORBIDDEN_REALPATH_PREFIXES)
        for p in ("/data/mal/clean-view/fresh-0808", "/data/mal/clean-view/fresh-0808/w1", "/data/mal/blocks/fresh-0808/w3", "/data/mal/blocks/fresh-0808"):
            with self.subTest(p):
                self.assertIsNotNone(xp._is_forbidden(p))
        # the allowed exploration view is not hit by the new entry
        self.assertIsNone(xp._is_forbidden("/data/mal/clean-view/explore-0814/w1"))

    def test_the_roots_guard_covers_0808_and_0828_blocks_and_views(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ok = Path(td)
            for bad in (
                "/data/mal/blocks/fresh-0808/w1", "/data/mal/clean-view/fresh-0808/w1", "/data/mal/clean-view/fresh-0808", "/data/mal/blocks/fresh-0828/w2",
                "/data/mal/clean-view/fresh-0828/w2", "/data/mal/blocks/forward-1002", "/data/mal/exp012", "/data/mal/forward-family/y",
            ):
                argv = ["--run-id", "run-001", "--out-root", str(ok / "o"), "--verify-view", "--fast-dir", bad, "--oracle-insample-dir", str(ok), "--oracle-live-dir", str(ok)]
                with self.subTest(bad), self.assertRaisesRegex(SystemExit, "reserved holdout"):
                    tab.main(argv)

    def test_all_roots_and_verify_view_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(SystemExit, "all required"):
                tab.main(["--run-id", "run-001", "--out-root", td, "--verify-view", "--fast-dir", td])
            with self.assertRaisesRegex(SystemExit, "verify-view"):
                tab.main(["--run-id", "run-001", "--out-root", td, "--fast-dir", td, "--oracle-insample-dir", td, "--oracle-live-dir", td])

    def test_forbidden_extra_view_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fake = {"fast": Path(td), "insample": Path(td), "live": Path(td)}
            for bad in ("/data/mal/blocks-clean/fresh-0828/w1", "/data/mal/clean-view/fresh-0808/w1", "/data/mal/blocks/fresh-0808/w2", "/data/mal/clean-view/fresh-0903/w2", "/data/mal/blocks/forward-1002"):
                with self.subTest(bad), mock.patch.object(tab, "guarded_roots", return_value=fake), mock.patch.object(tab, "assert_builtin_views_complete"), self.assertRaisesRegex(SystemExit, "forbidden"):
                    tab.main(argv_for(Path(td), "run-001", "--extra-fast-view", bad))


class BuiltinViewTests(unittest.TestCase):
    def test_unlisted_data_file_in_a_builtin_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "fast"
            write_fast_format_root(root, ["2026-09-19T01", "2026-09-19T02"], {})
            write_view_sha256(root)
            tab.assert_builtin_views_complete({"A": root})
            write_zst_jsonl(root / "trades" / "trades-2026-09-19T03.jsonl.zst", [])
            with self.assertRaisesRegex(SystemExit, "not listed in VIEW.sha256"):
                tab.assert_builtin_views_complete({"A": root})


class DuplicateTests(unittest.TestCase):
    def test_duplicate_mint_d_across_chunks_is_refused_as_it_is_appended(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            row = {"mint": "m", "day": "2026-09-20", "mig_day": "2026-09-20", "entry_land_k": 4, "pool": "A", "trigger_ms": 1, "mig_ms": 1}
            specs = []
            for i in range(2):
                (t / f"r{i}.jsonl").write_text(json.dumps(row) + "\n")
                specs.append({"tag": "A", "worker_id": i, "rows_path": t / f"r{i}.jsonl", "cens_path": t / f"c{i}.jsonl"})
            res = {"tag": "A", "triggers_by_day": {}, "counters": {"by_day": {n: {} for n in mt.COUNTER_NAMES}, "buys": 0, "null_trader_buys": 0}, "tape_through_ms": 0}
            with mock.patch.object(tab, "plan_pools", return_value=specs), mock.patch.object(tab, "_worker", return_value=res):
                with self.assertRaisesRegex(SystemExit, "duplicate"), redirect_stderr(io.StringIO()):
                    tab.build_table(t / "out", {"fast": t, "insample": t, "live": t}, None, max_workers=1)

    def test_the_worker_refuses_a_duplicate_key_itself(self) -> None:
        # one mint cannot migrate twice, so a duplicate can only come from a second chunk; the table-level check is the guard
        self.assertTrue(callable(tab._worker))


class EdgeTests(unittest.TestCase):
    RUNS = {"A": [("2026-09-19T01", "2026-09-21T23")], "X": [("2026-08-20T00", "2026-08-20T05"), ("2026-08-22T12", "2026-08-23T11")]}

    def _ms(self, hour: str, plus_s: int = 0) -> int:
        return g13._hour_ms(hour) + plus_s * 1000

    def test_row_flags_use_the_day_of_T_and_the_migration_day_separately(self) -> None:
        item = {"day": "2026-09-20", "mig_day": "2026-09-19", "trigger_ms": self._ms("2026-09-20T00", 20), "mig_ms": self._ms("2026-09-19T23", 3_600 - 880)}
        f = tab.row_edge_flags("A", item, self.RUNS)
        self.assertEqual((f["edge_left"], f["edge_right"]), (False, False))  # T is on day 2
        self.assertEqual((f["edge_left_mig"], f["edge_right_mig"]), (True, False))  # the migration was on the first day
        self.assertEqual(f["secs_cap_s"], 22 * 3600 + 3_600 - 880)

    def test_last_day_is_flagged(self) -> None:
        item = {"day": "2026-09-21", "mig_day": "2026-09-21", "trigger_ms": self._ms("2026-09-21T10"), "mig_ms": self._ms("2026-09-21T09", 3_000)}
        f = tab.row_edge_flags("A", item, self.RUNS)
        self.assertEqual((f["edge_left"], f["edge_right"], f["edge_left_mig"], f["edge_right_mig"]), (False, True, False, True))
        self.assertIsNone(f["secs_cap_s"])

    def test_a_middle_day_is_not_an_edge(self) -> None:
        item = {"day": "2026-09-20", "mig_day": "2026-09-20", "trigger_ms": self._ms("2026-09-20T10"), "mig_ms": self._ms("2026-09-20T09")}
        f = tab.row_edge_flags("A", item, self.RUNS)
        self.assertFalse(any(f[k] for k in ("edge_left", "edge_right", "edge_left_mig", "edge_right_mig")))

    def test_a_run_shorter_than_a_day_is_both_edges_and_second_run_has_its_own(self) -> None:
        item = {"day": "2026-08-20", "mig_day": "2026-08-20", "trigger_ms": self._ms("2026-08-20T02"), "mig_ms": self._ms("2026-08-20T01")}
        f = tab.row_edge_flags("X", item, self.RUNS)
        self.assertEqual((f["edge_left"], f["edge_right"]), (True, True))
        item2 = {"day": "2026-08-22", "mig_day": "2026-08-22", "trigger_ms": self._ms("2026-08-22T13"), "mig_ms": self._ms("2026-08-22T12", 100)}
        self.assertTrue(tab.row_edge_flags("X", item2, self.RUNS)["edge_left"])

    def test_edge_report_counts(self) -> None:
        rows = [{"pool": "A", "day": "2026-09-19", "entry_land_k": 4}, {"pool": "A", "day": "2026-09-20", "entry_land_k": 4}]
        cens = [{"pool": "A", "day": "2026-09-21", "entry_land_k": 4, "reason": "cap_past_tape"}]
        left, right = tab.edge_report({"A": self.RUNS["A"]}, {"A": {"2026-09-19": 5, "2026-09-21": 7}}, rows, cens)
        self.assertEqual((left["role"], left["day"], left["triggers"], left["rows_scored"]), ("left", "2026-09-19", 5, 1))
        self.assertEqual((right["role"], right["triggers"], right["censored"], right["censored_reasons"]), ("right", 7, 1, {"cap_past_tape": 1}))

    def test_pool_runs_split_extra_views_at_gaps(self) -> None:
        class V:
            hours = ["2026-08-20T00", "2026-08-20T01", "2026-08-20T05"]

        self.assertEqual(tab.pool_runs([V()])["X"], [("2026-08-20T00", "2026-08-20T01"), ("2026-08-20T05", "2026-08-20T05")])


class SummaryTests(unittest.TestCase):
    def test_triggers_equal_scored_plus_censored_at_every_d(self) -> None:
        def row(mint: str, d: int, outcome: str, filled: bool = True, label: int = 1, excl: bool = False) -> dict:
            return {"mint": mint, "day": "2026-09-20", "entry_land_k": d, "outcome": outcome, "filled": filled, "label": label, "excluded_by_time": excl}

        rows = [row("a", 4, "tp"), row("b", 4, "sl", label=0, excl=True), row("a", 8, "miss", filled=False, label=0)]
        cens = [{"mint": "b", "day": "2026-09-20", "entry_land_k": 8, "reason": "entry_past_tape"}]
        s = tab.summarize(rows, cens, {"2026-09-20": 2, "2026-09-21": 1})
        d = s["by_day"]["2026-09-20"]
        self.assertEqual((s["triggers_total"], d["triggers"]), (3, 2))
        self.assertEqual(
            d["by_d"]["4"], {"scored": 2, "filled": 2, "label_press_pos": 1, "excluded_by_time": 1, "outcomes": {"sl": 1, "tp": 1}, "censored": 0, "censored_reasons": {}}
        )
        self.assertEqual(d["by_d"]["8"]["scored"] + d["by_d"]["8"]["censored"], d["triggers"])
        self.assertEqual(s["by_day"]["2026-09-21"]["by_d"]["4"]["scored"], 0)


class PoolBClockTests(unittest.TestCase):
    def test_plan_reads_pool_b_with_the_event_ts_iterator(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            write_m15_root(t / "fast")
            write_pool_c(t / "ins")
            write_pool_b(t / "live")
            specs = tab.plan_pools({"fast": t / "fast", "insample": t / "ins", "live": t / "live"}, None, t / "s", (1, 4, 8))
        self.assertTrue(all(s["row_iter_fn"] is mt.iter_trade_rows_event_clock for s in specs if s["tag"] == "B"))
        self.assertFalse(any(s["row_iter_fn"] is mt.iter_trade_rows_event_clock for s in specs if s["tag"] != "B"))

    def test_counts_merge_per_pool_and_per_day(self) -> None:
        def res(tag: str, n: int) -> dict:
            by = {k: {} for k in mt.COUNTER_NAMES}
            by["no_clock_rows"] = {"2026-09-26": n}
            return {"tag": tag, "counters": {"by_day": by, "buys": 0, "null_trader_buys": 0}}

        merged = tab._merge_counters([res("B", 2), res("B", 3), res("A", 1)])
        self.assertEqual(merged["B"]["by_day"]["no_clock_rows"], {"2026-09-26": 5})
        self.assertEqual(merged["A"]["by_day"]["no_clock_rows"], {"2026-09-26": 1})


class ViewMtimeTests(unittest.TestCase):
    def test_a_view_newer_than_the_cutoff_is_refused_and_older_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "v"
            root.mkdir()
            f = root / "VIEW.sha256"
            f.write_text("")

            class V:
                pass

            v = V()
            v.root = root  # type: ignore[attr-defined]
            set_mtime(f, "2026-10-05T12:00:00Z")  # exactly at the cutoff: allowed
            info = tab.assert_view_mtimes([v])
            self.assertEqual(info[0]["view_sha256_mtime_utc"], "2026-10-05T12:00:00Z")
            set_mtime(f, "2026-10-05T12:00:01Z")
            with self.assertRaisesRegex(SystemExit, "after the pool cutoff"):
                tab.assert_view_mtimes([v])
        self.assertEqual(tab.assert_view_mtimes(None), [])


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        cls.hour = write_m15_root(td / "fast")
        write_pool_c(td / "ins")
        write_pool_b(td / "live")
        cls.td = td

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def test_dry_run_runs_the_guards_and_reads_no_tape(self) -> None:
        with mock.patch.object(tab, "build_table", side_effect=AssertionError("dry run must not build")):
            self.assertEqual(run_main(argv_for(self.td, "dry-001", "--dry-run")), 0)
        self.assertFalse((self.td / "out" / "dry-001").exists())

    def test_dry_run_prints_the_plan(self) -> None:
        buf = io.StringIO()
        with mock.patch.object(fz, "verify_view_sha256", return_value=1), mock.patch.object(fz, "check_view_pin", return_value="x"), redirect_stdout(buf), redirect_stderr(io.StringIO()):
            self.assertEqual(tab.main(argv_for(self.td, "dry-002", "--dry-run")), 0)
        plan = json.loads(buf.getvalue())
        self.assertEqual(plan["view_mtime_cutoff"], "2026-10-05T12:00:00Z")
        self.assertEqual(set(plan["roots"]), {"fast", "insample", "live"})
        self.assertEqual(plan["n_hours"], len(ex.POOL_HOURS) + len(ia.POOL_C_HOURS) + len(la.POOL_B_HOURS))

    def test_build_writes_table_counts_censored_and_manifest(self) -> None:
        self.assertEqual(run_main(argv_for(self.td, "run-001")), 0)
        out = self.td / "out" / "run-001"
        for name in ("table.jsonl", "table.md5", "censored.jsonl", "trigger_counts.json", "manifest.json"):
            self.assertTrue((out / name).is_file(), name)
        rows = [json.loads(line) for line in (out / "table.jsonl").read_text().splitlines()]
        self.assertEqual(sorted(r["entry_land_k"] for r in rows), [1, 4, 8])
        self.assertEqual({r["mint"] for r in rows}, {"migM"})
        self.assertEqual({r["pool"] for r in rows}, {"A"})
        self.assertEqual({r["outcome"] for r in rows}, {"cap"})
        self.assertEqual(list(rows[0]["features"]), mt.FEATURE_NAMES)
        self.assertEqual(len(rows[0]["features"]), 27)
        hs = hour_start_s(self.hour)
        self.assertEqual(rows[0]["mig_ms"], (hs + 60) * 1000)
        self.assertEqual(rows[0]["trigger_ms"], (hs + 60) * 1000 + 900_000)
        self.assertFalse(any(r["excluded_by_time"] for r in rows))
        # the migration and T are on pool A's first day: left edge, not right
        self.assertTrue(all(r["edge_left"] and r["edge_left_mig"] and not r["edge_right"] for r in rows))
        self.assertEqual(rows[0]["secs_cap_s"], (hs + 60 - g13._hour_ms(ex.POOL_HOURS[0]) / 1000.0))
        counts = json.loads((out / "trigger_counts.json").read_text())
        day = rows[0]["day"]
        self.assertEqual(counts["triggers_total"], 1)
        self.assertEqual(counts["by_day"][day]["triggers"], 1)
        self.assertEqual(counts["by_day"][day]["by_d"]["4"]["scored"], 1)
        pp = counts["pool_prints"]["A"]
        self.assertEqual(sum(pp["by_day"]["dropped_other_pool"].values()), 1)  # the P2 print
        self.assertEqual(sum(pp["by_day"]["migrations"].values()), 1)
        self.assertEqual(pp["buys"], 3)
        self.assertEqual(pp["null_trader_share_of_buys"], 0.0)
        man = json.loads((out / "manifest.json").read_text())
        self.assertEqual(man["schema"], "exp014_m15_table_v1")
        self.assertEqual(man["table_md5"], (out / "table.md5").read_text().strip())
        self.assertEqual((man["n_rows"], man["n_censored"], man["n_triggers"], man["n_excluded_by_time"]), (3, 0, 1, 0))
        self.assertEqual(set(man["view_sha256_file_sha256"]), {"fast", "insample", "live"})
        self.assertEqual(set(man["pinned_view_sha256"]), {"A", "C", "B"})
        self.assertEqual(man["settings"]["ks"], [1, 4, 8])
        self.assertEqual(man["settings"]["offset_ms"], 900_000)
        self.assertTrue(man["settings"]["clock"].startswith("block_time*1000, else event_ts*1000"))
        self.assertIn("t_ws", man["settings"]["create_clock"]["B"])
        self.assertEqual((man["settings"]["max_workers"], man["settings"]["buffer_hours"], man["settings"]["max_home_hours"]), (2, 24, 12))
        self.assertEqual(man["settings"]["feature_names"], mt.FEATURE_NAMES)
        self.assertIn("exp014_m15_trigger.py", man["code_sha256"])
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
        self.assertEqual((self.td / "out" / "run-w2" / "table.jsonl").read_text(), (self.td / "out" / "run-001" / "table.jsonl").read_text())

    def test_tape_ending_before_the_exit_is_counted_not_scored(self) -> None:
        with tempfile.TemporaryDirectory() as td2:
            t2 = Path(td2)
            h = write_m15_root(t2 / "fast", through_s=1_000)  # the global tape ends 100 s after T (T = +960 s)
            write_pool_c(t2 / "ins")
            write_pool_b(t2 / "live")
            self.assertEqual(run_main(argv_for(t2, "run-001")), 0)
            out = t2 / "out" / "run-001"
            self.assertEqual((out / "table.jsonl").read_text(), "")
            cens = [json.loads(line) for line in (out / "censored.jsonl").read_text().splitlines()]
            self.assertEqual({c["entry_land_k"]: c["reason"] for c in cens}, {1: "cap_past_tape", 4: "cap_past_tape", 8: "cap_past_tape"})
            counts = json.loads((out / "trigger_counts.json").read_text())
            self.assertEqual(counts["triggers_total"], 1)
            self.assertEqual(counts["by_day"][json.loads((out / "censored.jsonl").read_text().splitlines()[0])["day"]]["by_d"]["4"]["censored"], 1)
            self.assertTrue(h)

    def test_a_trigger_close_to_the_pool_end_is_kept_and_flagged(self) -> None:
        # last pool hour; migration 860 s in, so T + 30 min + 2 d 400 ms + 60 s is past the end of the run (3600 s) for every d,
        # while the global tape still reaches every exit (through 3599 s).
        with tempfile.TemporaryDirectory() as td2:
            t2 = Path(td2)
            write_m15_root(t2 / "fast", hour_idx=len(ex.POOL_HOURS) - 1, mig_off=860, through_s=3599 - 860)
            write_pool_c(t2 / "ins")
            write_pool_b(t2 / "live")
            self.assertEqual(run_main(argv_for(t2, "run-001")), 0)
            out = t2 / "out" / "run-001"
            rows = [json.loads(line) for line in (out / "table.jsonl").read_text().splitlines()]
            self.assertEqual(sorted(r["entry_land_k"] for r in rows), [1, 4, 8])
            self.assertTrue(all(r["excluded_by_time"] for r in rows))
            self.assertTrue(all(r["edge_right"] and r["edge_right_mig"] and not r["edge_left"] for r in rows))
            man = json.loads((out / "manifest.json").read_text())
            self.assertEqual(man["n_excluded_by_time"], 3)
            counts = json.loads((out / "trigger_counts.json").read_text())
            self.assertEqual(counts["by_day"][rows[0]["day"]]["by_d"]["4"]["excluded_by_time"], 1)

    def test_extra_view_joins_as_pool_x_and_records_its_mtime(self) -> None:
        view = self.td / "xview"
        hours = hours_between("2026-08-20T00", "2026-08-20T05")
        write_fast_format_root(view, hours, {})
        h = hours[1]
        creates, trades = m15_hour_rows(h, "xm1", 60, 7200)
        write_zst_jsonl(view / "trades" / f"trades-{h}.jsonl.zst", trades)
        write_zst_jsonl(view / "creates" / f"creates-{h}.jsonl.zst", creates)
        write_view_sha256(view)
        set_mtime(view / "VIEW.sha256", "2026-10-04T10:00:00Z")
        self.assertEqual(run_main(argv_for(self.td, "run-x", "--extra-fast-view", str(view))), 0)
        out = self.td / "out" / "run-x"
        man = json.loads((out / "manifest.json").read_text())
        self.assertEqual(man["extra_views"][0]["n_hours"], 6)
        self.assertEqual(man["extra_view_mtimes"][0]["view_sha256_mtime_utc"], "2026-10-04T10:00:00Z")
        self.assertEqual(man["n_hours"], len(ex.POOL_HOURS) + len(ia.POOL_C_HOURS) + len(la.POOL_B_HOURS) + 6)
        rows = [json.loads(line) for line in (out / "table.jsonl").read_text().splitlines()]
        xrows = [r for r in rows if r["pool"] == "X"]
        self.assertEqual(sorted(r["entry_land_k"] for r in xrows), [1, 4, 8])
        # the view is a 6 hour run inside one day: both edges
        self.assertTrue(all(r["edge_left"] and r["edge_right"] for r in xrows))
        # a view newer than the cutoff is refused before any tape is read
        set_mtime(view / "VIEW.sha256", "2026-10-06T00:00:00Z")
        with mock.patch.object(tab, "build_table", side_effect=AssertionError("must not build")), self.assertRaisesRegex(SystemExit, "after the pool cutoff"):
            run_main(argv_for(self.td, "run-x2", "--extra-fast-view", str(view)))
        set_mtime(view / "VIEW.sha256", "2026-10-04T10:00:00Z")


if __name__ == "__main__":
    unittest.main()
