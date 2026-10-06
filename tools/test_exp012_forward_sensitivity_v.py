"""Tests for the V-priced book (B) in tools/exp012_forward_sensitivity.py (DEC-016 Amendment 4 section 2).
Synthetic fixtures only; nothing opens /data.

Run: PYTHONPATH=$PWD /data/mal/venv/bin/python -m pytest -q tools/test_exp012_forward_sensitivity_v.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

import tools.exp012_forward as fw
import tools.exp012_forward_sensitivity as sens
import tools.exp012_forward_vbook as vb
import tools.exp012_fixtures as fx
import tools.test_exp012_forward_sensitivity as tsens
from tools.pumpswap_virtual import save_map

V = 17_584_269_263
POOLS = tuple(f"pool-{m}" for m in ("mC", "mC2", "mD", "mE", "mF"))


def _pooled_tape(mint, block_s, **kw):
    creates, trades = fx.mint_tape(mint, block_s, **kw)
    return creates, [{**t, "pool": f"pool-{mint}"} if t.get("venue") == "pumpswap" else t for t in trades]


class VFx(tsens.Fx):
    @classmethod
    def setUpClass(cls) -> None:
        with mock.patch.object(tsens, "mint_tape", _pooled_tape):
            super().setUpClass()

    def vmap(self, vmap: dict | None = None) -> tuple[Path, str]:
        path = Path(tempfile.mkdtemp(dir=self._td.name)) / "pool_v.json"
        save_map(path, {p: V for p in POOLS} if vmap is None else vmap, 0)
        return path, fw._sha256_file(path)

    def run_v(self, out, ledger, vmap, sha, **kw):
        return self.run_sens(out, ledger, vmap=vmap, vmap_sha256=sha, mcap_mode="v", **kw)


class DefaultUnchanged(VFx):
    def test_default_path_has_no_v_block_files_or_tags(self) -> None:
        out, ledger = self.sealed()
        rep = self.run_sens(out, ledger)
        self.assertNotIn("b_v", rep)
        rd = out / "sensitivity"
        self.assertFalse((rd / sens.DETAIL_V_NAME).exists())
        self.assertNotIn("entered_set_under_v_equals_a", rep["reproduction"])
        self.assertEqual(sorted(rep), sorted(["schema", "label", "verdict", "rule", "test_window", "clean_clock", "read_end", "k_p50", "k_p90", "trial_terms", "latency", "entry_bound", "exit_bound", "reproduction", "books", "generated_at_utc", "window_note"]))
        pool = fw.e11._hours_range("2026-10-05T01", "2026-10-05T09")
        with tsens.patched():
            rows = sens.sensitivity_rows(self.walk, pool, [("k1", 1, "end", 500_000_000, 500_000)], 100_000.0, Path(self._td.name) / "plain")
        self.assertTrue(rows)
        self.assertTrue(all("no_v_pools" not in r and "pumpswap_pools" not in r for r in rows))

    def test_a_priced_books_are_identical_with_and_without_the_v_option(self) -> None:
        out, ledger = self.sealed()
        base = self.run_sens(out, ledger)
        out2, ledger2 = self.sealed()
        vmap, sha = self.vmap()
        withv = self.run_v(out2, ledger2, vmap, sha)
        self.assertEqual(base["books"], withv["books"])
        self.assertEqual(base["verdict"], withv["verdict"])


class VBook(VFx):
    def test_v_changes_pnl_in_the_expected_direction(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap()
        rep = self.run_v(out, ledger, vmap, sha)
        b = rep["b_v"]
        self.assertTrue(rep["reproduction"]["entered_set_under_v_equals_a"])
        self.assertEqual(b["vmap"]["sha256"], sha)
        for name in ("p50", "p90"):
            a_tot, b_tot = rep["books"][name]["flat_15"]["total_sol"], b["books"][name]["flat_15"]["total_sol"]
            self.assertNotEqual(a_tot, b_tot)
            self.assertLess(b_tot, a_tot)  # vault + V prices the same fills worse than the vault alone
            self.assertFalse(b["null_v"][name]["not_decidable"])
        self.assertIn(b["verdict"], (sens.SUPPORTS, sens.DOES_NOT_SUPPORT))
        self.assertTrue((out / "sensitivity" / sens.DETAIL_V_NAME).is_file())
        self.assertIn("Book (B)", (out / "sensitivity" / sens.RESULT_MD).read_text())

    def test_not_decidable_propagates_and_leaves_the_a_verdict_alone(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap({**{p: V for p in POOLS}, "pool-mC": None})
        rep = self.run_v(out, ledger, vmap, sha)
        self.assertEqual(rep["b_v"]["verdict"], vb.NOT_DECIDABLE)
        self.assertIn(rep["b_v"]["verdict_if_decidable"], (sens.SUPPORTS, sens.DOES_NOT_SUPPORT))
        self.assertIn(rep["verdict"], (sens.SUPPORTS, sens.DOES_NOT_SUPPORT))
        self.assertTrue(any("pool-mC" in nv["null_v_pool_ids"] for nv in rep["b_v"]["null_v"].values()))

    def test_absent_pool_is_not_v_zero(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap({p: V for p in POOLS if p != "pool-mE"})
        rep = self.run_v(out, ledger, vmap, sha)
        self.assertEqual(rep["b_v"]["verdict"], vb.NOT_DECIDABLE)


class VRefusals(VFx):
    def test_wrong_sha_refuses_and_records_nothing(self) -> None:
        out, ledger = self.sealed()
        vmap, _sha = self.vmap()
        with self.assertRaises(fw.Refused) as cm:
            self.run_v(out, ledger, vmap, "0" * 64)
        self.assertIn("--vmap-sha256", str(cm.exception))
        self.assertEqual(self.lines(sens.runs_ledger_path(ledger)), [])
        self.assertFalse((out / "sensitivity" / sens.RESULT_JSON).exists())

    def test_partial_v_options_and_other_modes_refuse(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap()
        with self.assertRaises(fw.Refused):
            self.run_sens(out, ledger, vmap=vmap)
        with self.assertRaises(fw.Refused):
            self.run_sens(out, ledger, vmap=vmap, vmap_sha256=sha, mcap_mode="vault")
        self.assertEqual(self.lines(sens.runs_ledger_path(ledger)), [])

    def test_entered_set_mismatch_refuses_before_the_claim(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap()
        with mock.patch.object(vb, "check_entered_set", return_value=["mcap_mode v, k = 1: entered set differs from (A)'s"]):
            with self.assertRaises(fw.Refused) as cm:
                self.run_v(out, ledger, vmap, sha)
        self.assertIn("entered set differs", str(cm.exception))
        self.assertEqual(self.lines(sens.runs_ledger_path(ledger)), [])
