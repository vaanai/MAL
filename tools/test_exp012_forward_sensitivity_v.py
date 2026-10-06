"""Tests for the V-priced book (B) in tools/exp012_forward_sensitivity.py (DEC-016 Amendment 4 section 2).
Synthetic fixtures only; nothing opens /data.

Run: PYTHONPATH=$PWD /data/mal/venv/bin/python -m pytest -q tools/test_exp012_forward_sensitivity_v.py
"""

from __future__ import annotations

import io
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


def ledger_of(out: Path) -> Path:
    return out.parent / "ledger.jsonl"


class VFx(tsens.Fx):
    @classmethod
    def setUpClass(cls) -> None:
        with mock.patch.object(tsens, "mint_tape", _pooled_tape):
            super().setUpClass()

    def vmap(self, vmap: dict | None = None) -> tuple[Path, str]:
        path = Path(tempfile.mkdtemp(dir=self._td.name)) / "pool_v.json"
        save_map(path, {p: V for p in POOLS} if vmap is None else vmap, 0)
        return path, fw._sha256_file(path)

    def final_for_vbook(self):
        out, ledger = self.sealed()
        return self.walk, self.art, out

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

    WIN = ("2026-10-06T00:00:00Z", "2026-10-16T00:00:00Z")

    def vbook_files(self, verdict="PASS", *, extra_started=0, rows_sha="r" * 64, line_vmap="a" * 64, test_window=False, with_done=True):
        win = self.WIN
        d = Path(tempfile.mkdtemp(dir=self._td.name))
        doc = {"schema": vb.SCHEMA_REPORT, "vmap": {"sha256": "a" * 64}, "b_verdict": verdict, "clean_clock": win[0], "read_end": win[1], "test_window": test_window}
        rp = d / "vbook_report.json"
        rp.write_text(json.dumps(doc))
        base = {"clean_clock": win[0], "read_end": win[1], "test_window": test_window}
        lines = [{**base, "state": "STARTED"}] * (1 + extra_started)
        if with_done:
            lines.append({**base, "state": "DONE", "vmap_sha256": line_vmap, "final_rows_sha256": rows_sha, "b_verdict": verdict, "report_sha256": fw._sha256_file(rp)})
        led = d / "VBOOK_RUNS.jsonl"
        led.write_text("".join(json.dumps(x) + "\n" for x in lines))
        return rp, led

    def check(self, rp, led, *, sha="a" * 64, rows="r" * 64, test_window=False):
        return sens.check_vbook_report(rp, sha, self.WIN, test_window, led, rows)

    def test_vbook_report_must_be_a_pass_bound_to_its_done_line(self) -> None:
        rp, led = self.vbook_files()
        got = self.check(rp, led)
        self.assertEqual((got["b_verdict"], got["sha256"], got["ledger_line"]["state"]), ("PASS", fw._sha256_file(rp), "DONE"))
        with self.assertRaises(fw.Refused):
            self.check(rp, led, sha="c" * 64)
        with self.assertRaises(fw.Refused):
            self.check(rp, led, rows="x" * 64)  # not the FINAL marker's rows
        for verdict in ("FAIL", vb.NOT_DECIDABLE, None):
            rp2, led2 = self.vbook_files(verdict)
            with self.assertRaises(fw.Refused) as cm:
                self.check(rp2, led2)
            self.assertIn("not PASS", str(cm.exception))
        for kw in ({"extra_started": 1}, {"with_done": False}, {"line_vmap": "b" * 64}):
            rp2, led2 = self.vbook_files(**kw)
            with self.assertRaises(fw.Refused):
                self.check(rp2, led2)
        with self.assertRaises(fw.Refused):
            self.check(rp, led.with_name("none.jsonl"))
        rp3, led3 = self.vbook_files(vb.NOT_DECIDABLE, test_window=True)
        self.assertEqual(self.check(rp3, led3, test_window=True)["b_verdict"], vb.NOT_DECIDABLE)  # fixture windows only

    def test_pinned_vbook_report_that_is_not_a_pass_refuses_before_the_claim(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap()
        rp, led = self.vbook_files("FAIL", line_vmap=sha)
        doc = json.loads(rp.read_text())
        doc["vmap"] = {"sha256": sha}
        doc["clean_clock"], doc["read_end"] = tsens.CLEAN_CLOCK, tsens.READ_END
        rp.write_text(json.dumps(doc))
        (out.parent / vb.RUNS_LEDGER_NAME).write_text(led.read_text())  # the derived path: beside the FINAL out dir
        with mock.patch.object(fw, "PINNED_CLEAN_CLOCK", tsens.CLEAN_CLOCK), mock.patch.object(fw, "PINNED_READ_END", tsens.READ_END), mock.patch.object(fw, "DEFAULT_LEDGER", ledger), mock.patch.object(sens, "check_sealed", return_value={"rows_sha256": "r"}), mock.patch.object(sens, "final_verdict", return_value="PASS"):
            with self.assertRaises(fw.Refused) as cm:
                self.run_sens(out, ledger, test_window=False, vmap=vmap, vmap_sha256=sha, mcap_mode="v", vbook_report=rp)
        self.assertIn("not PASS", str(cm.exception))
        self.assertEqual(self.lines(sens.runs_ledger_path(ledger)), [])

    def test_vbook_binding_is_embedded_end_to_end_on_a_test_window(self) -> None:
        walk, art, out = self.final_for_vbook()
        vmap, sha = self.vmap()
        with tsens.patched(), mock.patch("sys.stderr", io.StringIO()) as err:
            rc = vb.main(["--walk-dir", str(walk), "--final-out-dir", str(out), "--final-ledger", str(ledger_of(out)), "--vmap", str(vmap), "--vmap-sha256", sha, "--out-dir", str(out.parent / "vb"), "--artifact-dir", str(art), "--freeze-commit", tsens.FREEZE_COMMIT, "--test-window"])
        self.assertEqual(rc, 0, err.getvalue())
        report = out.parent / "vb" / "vbook_report.json"
        rep = self.run_sens(out, ledger_of(out), vmap=vmap, vmap_sha256=sha, mcap_mode="v", vbook_report=report)
        bind = rep["vbook_binding"]
        self.assertEqual(bind["sha256"], fw._sha256_file(report))
        self.assertEqual(bind["ledger_line"]["state"], "DONE")
        self.assertEqual(bind["b_verdict"], json.loads(report.read_text())["b_verdict"])

    def test_vbook_runs_ledger_override_is_refused_on_the_pinned_window(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap()
        rp, led = self.vbook_files("PASS", line_vmap=sha)
        with mock.patch.object(fw, "PINNED_CLEAN_CLOCK", tsens.CLEAN_CLOCK), mock.patch.object(fw, "PINNED_READ_END", tsens.READ_END), mock.patch.object(fw, "DEFAULT_LEDGER", ledger):
            with self.assertRaises(fw.Refused) as cm:
                self.run_sens(out, ledger, test_window=False, vmap=vmap, vmap_sha256=sha, mcap_mode="v", vbook_report=rp, vbook_runs_ledger=led)
        self.assertIn("--vbook-runs-ledger is refused", str(cm.exception))
        self.assertEqual(self.lines(sens.runs_ledger_path(ledger)), [])

    def test_compare_rows_treats_int_and_float_of_equal_value_as_equal(self) -> None:
        a = [{"mint": "m", "mig_ms": 1, "flat": 5, "press": 2.0}]
        b = [{"mint": "m", "mig_ms": 1, "flat": 5.0, "press": 2}]
        self.assertEqual(sens.compare_rows(a, b), [])
        self.assertTrue(sens.compare_rows(a, [{"mint": "m", "mig_ms": 1, "flat": 5.0000001, "press": 2}]))

    def test_exit_past_tape_in_b_refuses_before_the_claim(self) -> None:
        out, ledger = self.sealed()
        vmap, sha = self.vmap()
        with mock.patch.object(sens, "_group", side_effect=fw.Refused(["1 FINAL entered mint(s) have no row at k3"])):
            with self.assertRaises(fw.Refused):
                self.run_v(out, ledger, vmap, sha)
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
