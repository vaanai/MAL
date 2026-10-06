"""EXP-013 Amendment 7: V pricing in the table workers, the coverage refusal, and the screen's record of it. Synthetic data only."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import tools.exp013_grad_screen as sc
import tools.exp013_grad_table as gtab
import tools.exp013_grad_trigger as gt
import tools.exploration_exits as ex
import tools.pumpswap_virtual_adapter as ad
from tools.exp013_fixtures import write_view_sha256
from tools.test_exp013_grad_table import V_PATH, V_SHA, argv_for, run_main, write_grad_root, write_pool_b, write_pool_c

V_FIX = 17_584_269_263


def _write_map(path: Path, v: dict) -> str:
    path.write_text(json.dumps({"v": v}), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _specs(rows: list[dict]) -> list[dict]:
    return [{"tag": "A", "home": ["h0"], "buf": [], "hour_info_fn": lambda k: {"trade": rows}, "row_iter_fn": lambda trade: iter(trade)}]


def _bond(mint: str, base: int) -> dict:
    return {"venue": "pump_bonding", "mint": mint, "base_reserve": base}


def _swap(mint: str, pool, n: int = 1) -> list[dict]:
    return [{"venue": "pumpswap", "mint": mint, "pool": pool} for _ in range(n)]


class PrepassTests(unittest.TestCase):
    TRIG_BASE = 438_000_000_000_000  # >= 80% progress (the fixture's trigger print)

    def cov(self, vmap: dict, swaps: list[dict]) -> dict:
        rows = [_bond("m", self.TRIG_BASE), _bond("other", 1_000_000_000_000_000), *swaps, *_swap("other", "Unmapped", 50)]
        return gtab.v_prepass(_specs(rows), vmap)

    def test_null_and_absent_are_missing_and_zero_is_covered(self) -> None:
        c = self.cov({"Pn": None, "Pz": 0, "Pv": 5}, _swap("m", "Pn", 2) + _swap("m", "Pz", 3) + _swap("m", "Pv", 4) + _swap("m", "Pabsent", 5) + _swap("m", None, 1))
        self.assertEqual((c["prints"], c["covered"], c["missing"]), (15, 7, 8))  # Pz (explicit 0) and Pv are covered
        self.assertEqual(c["missing_pools"], 3)  # Pn, Pabsent and the print with no pool field
        self.assertEqual(c["n_triggered_mints"], 1)  # "other" never reaches the trigger, its prints are not in the population

    def test_refusal_threshold_is_one_percent(self) -> None:
        ok = self.cov({"Pv": 5}, _swap("m", "Pv", 99) + _swap("m", "Pn", 1))  # exactly 1%
        gtab.check_v_coverage(ok)
        bad = self.cov({"Pv": 5}, _swap("m", "Pv", 98) + _swap("m", "Pn", 2))
        with self.assertRaisesRegex(gtab.VRefused, "limit 1%"):
            gtab.check_v_coverage(bad)

    def test_no_pumpswap_prints_refuses(self) -> None:
        with self.assertRaisesRegex(gtab.VRefused, "no PumpSwap prints"):
            gtab.check_v_coverage(gtab.v_prepass(_specs([_bond("m", self.TRIG_BASE)]), {}))


class FixtureBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        cls.td = Path(cls._td.name)
        write_grad_root(cls.td / "fast")
        write_pool_c(cls.td / "ins")
        write_pool_b(cls.td / "live")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def rows(self, run_id: str) -> list[dict]:
        return [json.loads(line) for line in (self.td / "out" / run_id / "table.jsonl").read_text().splitlines()]


class AdapterActiveInWorkersTests(FixtureBase):
    def test_spawned_workers_price_pumpswap_on_vault_plus_v(self) -> None:
        argv = [a if a != "1" else "2" for a in argv_for(self.td, "v-w2")]
        self.assertEqual(argv[argv.index("--max-workers") + 1], "2")
        self.assertEqual(run_main(argv), 0)
        man = json.loads((self.td / "out" / "v-w2" / "manifest.json").read_text())
        v = man["v_adapter"]
        self.assertEqual((v["vmap_sha256"], v["mcap_mode"]), (V_SHA, "v"))
        c = v["adapter_counts"]
        self.assertGreaterEqual(c["corrected"], 2)  # the fixture's two PumpSwap prints (migration and late)
        self.assertEqual(c["no_v"], 0)
        self.assertGreater(c["n_worker_files"], 1)  # one counts file per worker chunk, written by the spawned processes
        pids = {int(p.name.split("-")[1]) for p in (self.td / "out" / "v-w2" / "scratch" / "counts_virtual").glob("counts-*.json")}
        self.assertNotIn(os.getpid(), pids)
        self.assertEqual(v["prepass"]["missing"], 0)
        self.assertEqual(v["prepass"]["n_triggered_mints_with_pumpswap"], 1)
        self.assertNotIn(ad.ENV_MAP, os.environ)  # nothing leaks into the parent after the build

    def test_v_changes_the_priced_rows_and_one_worker_equals_two(self) -> None:
        self.assertEqual(run_main(argv_for(self.td, "v-w1")), 0)
        if not (self.td / "out" / "v-w2").exists():
            self.assertEqual(run_main([a if a != "1" else "2" for a in argv_for(self.td, "v-w2")]), 0)
        self.assertEqual((self.td / "out" / "v-w1" / "table.md5").read_text(), (self.td / "out" / "v-w2" / "table.md5").read_text())
        # the same tape priced without V (build_table's test-only path) differs on the migration sale
        from tools.exp013_grad_table import build_table
        import tools.exp011_freeze as fz  # noqa: F401
        from tools.exploration_entry_model import _rows_out_path  # noqa: F401

        roots = {"fast": self.td / "fast", "insample": self.td / "ins", "live": self.td / "live"}
        with redirect_stderr(io.StringIO()):
            build_table(self.td / "out" / "frozen", roots, None, max_workers=1, run_id="frozen")
        frozen = [json.loads(line) for line in (self.td / "out" / "frozen" / "table.jsonl").read_text().splitlines()]
        withv = self.rows("v-w1")
        self.assertEqual([r["mint"] for r in frozen], [r["mint"] for r in withv])
        self.assertNotEqual([r["press"] for r in frozen], [r["press"] for r in withv])
        self.assertNotEqual([r["flat"] for r in frozen], [r["flat"] for r in withv])

    def test_wrapper_gives_a_fixture_print_vault_plus_v(self) -> None:
        row = {"type": "trade", "venue": "pumpswap", "side": "buy", "quote_reserve": 85_000_000_000, "base_reserve": 206_900_000_000_000, "slot": 1100, "event_index": 1,
               "sol_lamports": 10**9, "trader": "f", "token_raw": 1000, "mint": "gradM", "t_recv_ms": 5_000, "block_time": 5, "signature": "s", "quote_is_wsol": True, "pool": "PoolG"}
        plain = gt.print_from_trade_row(dict(row))[1]
        with gtab.v_pricing_patch({"PoolG": V_FIX}):
            patched = gt.print_from_trade_row(dict(row))[1]
        # the pre-trade fee tier is read on vault + V as well (the post-trade vault differs by the fee tier's share), so allow 0.01 SOL
        self.assertLess(abs(patched.quote_reserve - (plain.quote_reserve + V_FIX)), 10_000_000)
        self.assertGreater(patched.quote_reserve, plain.quote_reserve + 17 * 10**9)
        self.assertAlmostEqual(patched.price_sol, patched.quote_reserve / (patched.base_reserve * 1000))
        self.assertEqual(gt.print_from_trade_row(dict(row))[1].quote_reserve, plain.quote_reserve)  # restored


class RefusalTests(FixtureBase):
    def test_pool_without_v_refuses_with_exit_2_before_any_outcome(self) -> None:
        p = self.td / "null_map.json"
        sha = _write_map(p, {"PoolG": None})  # account fetch returned null: missing, never V = 0
        argv = argv_for(self.td, "refuse-1", "--vmap", str(p))
        with mock.patch.object(gt, "run_worker_grad", side_effect=AssertionError("no worker may run")), mock.patch.object(gtab, "v_prepass", wraps=gtab.v_prepass) as pp:
            self.assertEqual(run_main(argv, pin=sha), 2)
        pp.assert_called_once()  # refused by the 1% pre-pass, not by the pin
        self.assertFalse((self.td / "out" / "refuse-1").exists())  # no table, no manifest, empty dirs removed

    def test_absent_pool_refuses_too(self) -> None:
        p = self.td / "absent_map.json"
        sha = _write_map(p, {"SomeOtherPool": V_FIX})
        with mock.patch.object(gtab, "v_prepass", wraps=gtab.v_prepass) as pp:
            self.assertEqual(run_main(argv_for(self.td, "refuse-2", "--vmap", str(p)), pin=sha), 2)
        pp.assert_called_once()

    def test_wrong_sha_refuses_before_any_row_is_read(self) -> None:
        p = self.td / "other_map.json"
        _write_map(p, {"PoolG": V_FIX + 1})  # a valid map, but not the pinned file
        with mock.patch.object(gtab, "plan_pools", side_effect=AssertionError("no row may be read")):
            self.assertEqual(run_main(argv_for(self.td, "refuse-3", "--vmap", str(p))), 2)
        with mock.patch.object(gtab, "plan_pools", side_effect=AssertionError("no row may be read")):
            self.assertEqual(run_main(argv_for(self.td, "refuse-4", "--vmap", str(self.td / "nope.json"))), 2)

    def test_default_pin_is_pending_and_refuses_at_startup(self) -> None:
        self.assertEqual(gtab.VMAP_0909_SHA256, "PENDING_JOB_228")  # the manager's reviewed one-line commit fills it in after #228
        self.assertEqual(sc.V_SHA256, gtab.VMAP_0909_SHA256)
        self.assertEqual(gtab.DEFAULT_VMAP, "/data/mal/pumpswap-virtual/pool_v_0909.json")
        self.assertEqual(gtab.V_MAX_MISSING_FRACTION, 0.01)
        with self.assertRaisesRegex(gtab.VRefused, "PENDING_JOB_228"):
            gtab.check_pin_ready()
        with mock.patch.object(gtab, "plan_pools", side_effect=AssertionError("no row may be read")):
            self.assertEqual(run_main(argv_for(self.td, "pend-1"), pin=gtab.VMAP_0909_SHA256), 2)  # the real default pin, a valid fixture map
            self.assertEqual(run_main(argv_for(self.td, "pend-2", "--dry-run"), pin=gtab.VMAP_0909_SHA256), 2)
        with self.assertRaises(gtab.VRefused):
            gtab.check_vmap_sha(V_PATH)

    def test_a_filled_pin_accepts_only_that_map(self) -> None:
        with mock.patch.object(gtab, "VMAP_0909_SHA256", V_SHA):
            self.assertEqual(gtab.check_vmap_sha(V_PATH), V_SHA)
        other = self.td / "other_pin.json"
        _write_map(other, {"PoolG": 1})
        with mock.patch.object(gtab, "VMAP_0909_SHA256", V_SHA), self.assertRaisesRegex(gtab.VRefused, "!= the pinned"):
            gtab.check_vmap_sha(other)
        with mock.patch.object(gtab, "VMAP_0909_SHA256", "2506f7d2d8475e44ca70a8c536dbb7405930b1092edca331dbbe611236b4d2f8"), self.assertRaisesRegex(gtab.VRefused, "!= the pinned"):
            gtab.check_vmap_sha(V_PATH)  # the retired 0814 pin does not match the fixture map


class ScreenGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        p = mock.patch.object(sc, "V_SHA256", V_SHA)
        p.start()
        self.addCleanup(p.stop)

    def test_pending_screen_pin_refuses(self) -> None:
        with mock.patch.object(sc, "V_SHA256", "PENDING_JOB_228"), self.assertRaisesRegex(SystemExit, "not a sha256"):
            sc.assert_v_adapter(self.good())

    def good(self) -> dict:
        return {"v_adapter": {"vmap_sha256": V_SHA, "mcap_mode": "v", "prepass": {"prints": 100, "missing": 1, "missing_fraction": 0.01, "max_missing_fraction": 0.01},
                              "adapter_counts": {"corrected": 99}}}

    def test_a_table_priced_through_the_adapter_passes(self) -> None:
        self.assertEqual(sc.assert_v_adapter(self.good())["mcap_mode"], "v")

    def test_a_table_without_the_record_or_with_a_wrong_map_or_gap_is_refused(self) -> None:
        with self.assertRaisesRegex(SystemExit, "no v_adapter"):
            sc.assert_v_adapter({"v_adapter": None})
        g = self.good()
        g["v_adapter"]["vmap_sha256"] = "0" * 64
        with self.assertRaisesRegex(SystemExit, "not the pinned map"):
            sc.assert_v_adapter(g)
        g = self.good()
        g["v_adapter"]["prepass"]["missing_fraction"] = 0.02
        with self.assertRaisesRegex(SystemExit, "outside the limit"):
            sc.assert_v_adapter(g)
        g = self.good()
        g["v_adapter"]["adapter_counts"]["corrected"] = 0
        with self.assertRaisesRegex(SystemExit, "no corrected"):
            sc.assert_v_adapter(g)

    def test_real_run_refuses_a_table_without_v_before_the_try_is_spent(self) -> None:
        with mock.patch.object(sc.gm, "load_table", return_value=([], {"table_md5": "x"})), mock.patch.object(sc.gm, "assert_run_dir_allowed", return_value=Path("/data/mal/exp013-grad/r1")), \
             mock.patch.object(sc, "assert_view_manifest"), mock.patch.object(sc, "assert_exp012_dir"), tempfile.TemporaryDirectory() as td:
            vm = Path(td) / "vm.json"
            vm.write_text("{}")
            with mock.patch.object(sc.mal_result, "append_try", side_effect=AssertionError("the try must not be spent")), self.assertRaisesRegex(SystemExit, "no v_adapter"):
                sc.run("/data/mal/exp013-grad/r1", vm, Path(td) / "o", ledger_dir=sc.DEFAULT_LEDGER_DIR, tries_log=Path(td) / "t")


if __name__ == "__main__":
    unittest.main()
