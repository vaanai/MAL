"""Tests for the V-priced book (B) in tools/exp012_forward_sensitivity.py (DEC-016 Amendment 4 section 2).
Synthetic fixtures only; nothing opens /data.

Run: PYTHONPATH=$PWD /data/mal/venv/bin/python -m pytest -q tools/test_exp012_forward_sensitivity_v.py
"""

from __future__ import annotations

import json
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
        self.assertEqual(base["verdict"], withv["a_priced_verdict"])
        self.assertEqual(withv["verdict"], withv["b_v"]["verdict"])


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
        self.assertEqual(rep["verdict"], b["verdict"])  # with V on the top-level verdict is (B)'s
        self.assertIn(rep["a_priced_verdict"], (sens.SUPPORTS, sens.DOES_NOT_SUPPORT))
        self.assertGreater(b["adapter_counts"]["corrected"], 0)
        self.assertEqual(b["adapter_counts"]["no_v"], 0)
        self.assertTrue(rep["reproduction"]["v_repro_variant_equals_forward_v_rows"])
        done = [m for m in self.lines(sens.runs_ledger_path(ledger)) if m["state"] == "DONE"]
        self.assertEqual(done[0]["verdict"], rep["verdict"])
        self.assertTrue((out / "sensitivity" / sens.DETAIL_V_NAME).is_file())
        self.assertIn("Book (B)", (out / "sensitivity" / sens.RESULT_MD).read_text())

    def test_not_decidable_propagates_to_the_top_level_verdict(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap({**{p: V for p in POOLS}, "pool-mC": None})
        rep = self.run_v(out, ledger, vmap, sha)
        self.assertEqual(rep["b_v"]["verdict"], vb.NOT_DECIDABLE)
        self.assertIn(rep["b_v"]["verdict_if_decidable"], (sens.SUPPORTS, sens.DOES_NOT_SUPPORT))
        self.assertEqual(rep["verdict"], vb.NOT_DECIDABLE)  # (B)'s verdict is the top-level one
        self.assertIn(rep["a_priced_verdict"], (sens.SUPPORTS, sens.DOES_NOT_SUPPORT))
        done = [m for m in self.lines(sens.runs_ledger_path(ledger)) if m["state"] == "DONE"]
        self.assertEqual(done[0]["verdict"], vb.NOT_DECIDABLE)
        self.assertTrue(any("pool-mC" in nv["null_v_pool_ids"] for nv in rep["b_v"]["null_v"].values()))

    def test_absent_pool_is_not_v_zero(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap({p: V for p in POOLS if p != "pool-mE"})
        rep = self.run_v(out, ledger, vmap, sha)
        self.assertEqual(rep["b_v"]["verdict"], vb.NOT_DECIDABLE)


class VRefusals(VFx):
    def test_missing_null_v_tags_fail_closed_before_the_claim(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap()
        real = sens.sensitivity_rows

        def strip(*a, **k):
            rows = real(*a, **k)
            for r in rows:
                r.pop("no_v_pools", None)
            return rows

        with mock.patch.object(sens, "sensitivity_rows", strip):
            with self.assertRaises(fw.Refused) as cm:
                self.run_v(out, ledger, vmap, sha)
        self.assertIn("fail closed", str(cm.exception))
        self.assertEqual(self.lines(sens.runs_ledger_path(ledger)), [])

    def test_repro_variant_mismatch_under_v_refuses_before_the_claim(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap()
        real = sens.sensitivity_rows

        def perturb(*a, **k):
            rows = real(*a, **k)
            for r in rows:
                if r["variant"] == "repro" and r["mint"] == "mC":
                    r["flat"] += 1.0
            return rows

        with mock.patch.object(sens, "sensitivity_rows", perturb):
            with self.assertRaises(fw.Refused) as cm:
                self.run_v(out, ledger, vmap, sha)
        self.assertIn("repro variant", str(cm.exception))
        self.assertEqual(self.lines(sens.runs_ledger_path(ledger)), [])
        self.assertFalse((out / "sensitivity" / sens.RESULT_JSON).exists())

    def test_pinned_window_requires_all_v_options_and_the_vbook_report(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap()
        with mock.patch.object(fw, "PINNED_CLEAN_CLOCK", tsens.CLEAN_CLOCK), mock.patch.object(fw, "PINNED_READ_END", tsens.READ_END), mock.patch.object(fw, "DEFAULT_LEDGER", ledger):
            for kw in ({}, {"vmap": vmap, "vmap_sha256": sha, "mcap_mode": "v"}):
                with self.assertRaises(fw.Refused) as cm:
                    self.run_sens(out, ledger, test_window=False, **kw)
                self.assertIn("--vbook-report", str(cm.exception))
        self.assertEqual(self.lines(sens.runs_ledger_path(ledger)), [])

    def test_vbook_report_must_be_finished_for_this_map_and_window(self) -> None:
        win = ("2026-10-06T00:00:00Z", "2026-10-16T00:00:00Z")
        good = {"schema": vb.SCHEMA_REPORT, "vmap": {"sha256": "a" * 64}, "b_verdict": "FAIL", "clean_clock": win[0], "read_end": win[1], "test_window": False}
        d = Path(tempfile.mkdtemp(dir=self._td.name))

        def check(doc, sha="a" * 64):
            f = d / "r.json"
            f.write_text(json.dumps(doc))
            return sens.check_vbook_report(f, sha, win, False)

        self.assertEqual(check(good)["b_verdict"], "FAIL")
        for bad, sha in (({**good, "vmap": {"sha256": "b" * 64}}, "a" * 64), ({**good, "b_verdict": None}, "a" * 64), ({**good, "schema": "x"}, "a" * 64), ({**good, "read_end": "z"}, "a" * 64), (good, "c" * 64)):
            with self.assertRaises(fw.Refused):
                check(bad, sha)
        with self.assertRaises(fw.Refused) as cm:
            check({**good, "b_verdict": vb.NOT_DECIDABLE})  # pinned: the single-use window stays unspent
        self.assertIn("single-use", str(cm.exception))
        f = d / "nd.json"
        f.write_text(json.dumps({**good, "b_verdict": vb.NOT_DECIDABLE, "test_window": True}))
        self.assertEqual(sens.check_vbook_report(f, "a" * 64, win, True)["b_verdict"], vb.NOT_DECIDABLE)  # test windows only
        with self.assertRaises(fw.Refused):
            sens.check_vbook_report(d / "missing.json", "a" * 64, win, False)

    def test_pinned_not_decidable_vbook_report_refuses_before_the_claim(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap()
        rep = {"schema": vb.SCHEMA_REPORT, "vmap": {"sha256": sha}, "b_verdict": vb.NOT_DECIDABLE, "clean_clock": fw.PINNED_CLEAN_CLOCK, "read_end": fw.PINNED_READ_END, "test_window": False}
        f = Path(tempfile.mkdtemp(dir=self._td.name)) / "vbook_report.json"
        f.write_text(json.dumps(rep))
        with mock.patch.object(fw, "PINNED_CLEAN_CLOCK", tsens.CLEAN_CLOCK), mock.patch.object(fw, "PINNED_READ_END", tsens.READ_END), mock.patch.object(fw, "DEFAULT_LEDGER", ledger), mock.patch.object(sens, "check_sealed", return_value={}), mock.patch.object(sens, "final_verdict", return_value="PASS"):
            rep["clean_clock"], rep["read_end"] = tsens.CLEAN_CLOCK, tsens.READ_END
            f.write_text(json.dumps(rep))
            with self.assertRaises(fw.Refused) as cm:
                self.run_sens(out, ledger, test_window=False, vmap=vmap, vmap_sha256=sha, mcap_mode="v", vbook_report=f)
        self.assertIn("single-use", str(cm.exception))
        self.assertEqual(self.lines(sens.runs_ledger_path(ledger)), [])
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
