"""tools/exp012_exit_sensitivity_v.py: family, cell, guards, nested estimate, optimism gap. Fixtures only."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

import tools.exp012_exit_sensitivity_v as ev
import tools.exploration_entry_model as eem

SOL = 1_000_000_000
REF = ev.TARGET_SPEC_ID


def _row(mint: str, spec: str, flat: float, press: float, day: str, pool: str = "A") -> dict:
    return {"mint": mint, "day": day, "spec": spec, "filled": True, "status": 1, "gross": 0, "flat": flat * SOL, "press": press * SOL, "pool": pool}


def _variants(*ids: str) -> list[dict]:
    return [{"id": i, "family": "f", "desc": i, "reference": i == REF, "spec": {"id": i}} for i in ids]


class FamilyTests(unittest.TestCase):
    def test_family_is_small_and_predeclared(self) -> None:
        variants, skipped = ev.resolve_variants()
        ids = [v["id"] for v in variants]
        self.assertLessEqual(len(ev.REQUESTED), 8)
        self.assertEqual(
            ids,
            [REF, "tpsl_tp50_sl20", "tpsl_tp50_sl40", "tpsl_tp40_sl30", "tpsl_tp75_sl30", "timecap_10m_tp50_sl30", "timecap_5m_tp50_sl30"],
        )
        self.assertEqual([v["id"] for v in variants if v["reference"]], [REF])
        self.assertEqual([s["id"] for s in skipped], ["breakeven_after_tp25"])
        self.assertIn("not implemented", skipped[0]["reason"])
        self.assertFalse(any(("trail" in i or "ladder" in i) for i in ids))

    def test_tp40_is_an_ordinary_tpsl_spec(self) -> None:
        spec = next(v["spec"] for v in ev.resolve_variants()[0] if v["id"] == "tpsl_tp40_sl30")
        self.assertEqual((spec["type"], spec["tp"], spec["sl"], spec["cap_ms"]), ("tpsl", 0.40, 0.30, 1_800_000))


class CellTests(unittest.TestCase):
    def test_cell_constants(self) -> None:
        self.assertEqual((ev.ENTRY_K, ev.EXIT_LAND_K, ev.SIZE_SOL, ev.FEE_LAMPORTS), (6, 2, 0.05, 505_000))
        self.assertEqual(ev.EXISTING_TRIES_ON_POOL, 54)

    def test_patch_fixes_the_cell_and_restores(self) -> None:
        seen: dict = {}
        orig = eem.score_one

        def fake(*a, **kw):  # noqa: ANN001
            seen.update(kw)
            return []

        eem.score_one = fake
        try:
            with ev.entry_exit_patch():
                eem.score_one("m", None, None, None, 0, {}, specs=[], size=None, priority=None, entry_land_k=None)
                self.assertEqual((seen["size"], seen["priority"], seen["entry_land_k"], seen["exit_land_k"]), (50_000_000, 505_000, 6, 2))
                with self.assertRaises(RuntimeError):
                    eem.score_one("m", None, None, None, 0, {}, entry_land_k=1)
                with self.assertRaises(RuntimeError):
                    eem.score_one("m", None, None, None, 0, {}, exit_land_k=1)
            self.assertIs(eem.score_one, fake)
        finally:
            eem.score_one = orig


class GuardTests(unittest.TestCase):
    def test_holdout_and_missing_roots_are_refused(self) -> None:
        ns = argparse.Namespace(fast_dir=Path("/data/mal/blocks-clean/x"), oracle_insample_dir=Path("/tmp/a"), oracle_live_dir=Path("/tmp/b"), verify_view=True)
        with self.assertRaises(SystemExit):
            ev.guarded_roots(ns)
        ns2 = argparse.Namespace(fast_dir=None, oracle_insample_dir=None, oracle_live_dir=None, verify_view=True)
        with self.assertRaises(SystemExit):
            ev.guarded_roots(ns2)

    def test_integrity_refuses_duplicates_wrong_day_and_excess(self) -> None:
        thr = {"n_oof": 3, "n_selected_at_or_above_threshold": 1}
        scores = {"m1": 0.9, "m2": 0.1}
        days = {"m1": "d1", "m2": "d1"}
        ok = [_row("m1", REF, 0.1, 0.1, "d1"), _row("m2", REF, 0.1, 0.1, "d1")]
        info = ev.check_integrity_v(ok, scores, 0.8, thr, days)
        self.assertEqual(info["rows_by_variant"][REF], {"rows": 2, "selected": 1})
        with self.assertRaises(SystemExit):
            ev.check_integrity_v(ok + [_row("m1", REF, 0, 0, "d1")], scores, 0.8, thr, days)
        with self.assertRaises(SystemExit):
            ev.check_integrity_v([_row("m1", REF, 0, 0, "d2")], scores, 0.8, thr, days)
        with self.assertRaises(SystemExit):
            ev.check_integrity_v([_row("m1", "other", 0, 0, "d1")], scores, 0.8, thr, days)


class NestedAndReportTests(unittest.TestCase):
    def setUp(self) -> None:
        # 3 days, 4 selected mints per day. `good` beats the reference on every day; `bad` loses on every day.
        self.scores, rows = {}, []
        for d, day in enumerate(("2026-09-20", "2026-09-21", "2026-09-22")):
            for i in range(4):
                m = f"m{d}{i}"
                self.scores[m] = 0.9
                rows.append(_row(m, REF, 0.01, 0.01, day))
                rows.append(_row(m, "good", 0.03 + 0.001 * i, 0.02 + 0.001 * i, day))
                rows.append(_row(m, "bad", -0.02, -0.02, day))
        self.thr = {"n_oof": 12, "n_selected_at_or_above_threshold": 12}
        self.days = {r["mint"]: r["day"] for r in rows}
        skipped = [{"id": "breakeven_after_tp25", "family": "breakeven", "reason": "not implemented"}]
        self.rep = ev.analyze(rows, self.scores, 0.8, _variants(REF, "good", "bad"), skipped, self.thr, self.days)

    def test_nested_picks_the_consistent_winner(self) -> None:
        nl = self.rep["nested_lodo"]
        self.assertTrue(nl["available"])
        self.assertEqual(nl["times_chosen"]["good"], 3)
        self.assertEqual(nl["pooled"]["n"], 12)
        self.assertGreater(nl["pooled"]["press"]["mean_sol"], 0)

    def test_paired_increment_and_days(self) -> None:
        good = next(v for v in self.rep["variants"] if v["id"] == "good")["paired_vs_reference"]
        self.assertAlmostEqual(good["press"]["mean_sol"], 0.0115, places=6)
        self.assertEqual(good["flat"]["days_positive"], 3)
        self.assertEqual(len(good["press"]["ci90_sol"]), 2)

    def test_optimism_gap_and_tries_banner(self) -> None:
        g = self.rep["optimism_gap"]
        self.assertTrue(g["available"])
        self.assertEqual(g["best_variant"], "good")
        self.assertAlmostEqual(g["gap_press"], g["best_pooled_press_increment"] - g["nested_press_increment"])
        self.assertEqual(self.rep["cumulative_tries_on_pool"], 54 + 3)
        md = ev.render_md(self.rep)
        self.assertTrue(md.startswith("# **EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE EVIDENCE: BEST OF 3"))
        self.assertIn("54 existing + 3 = 57", md)
        self.assertIn("0.05 SOL entries", md)
        self.assertIn("Optimism gap", md)

    def test_log_tries_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out, log = Path(td) / "out", Path(td) / "tries.jsonl"
            out.mkdir()
            self.assertEqual(ev.log_tries(self.rep, out, log), 3)
            self.assertEqual(ev.log_tries(self.rep, out, log), 0)
            self.assertEqual(len(log.read_text().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
