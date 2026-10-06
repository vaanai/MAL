"""EXP-020 grid tests on the real row shapes (cache rows as exp015 writes them, universe rows as exp015.build_universe returns them)."""

from __future__ import annotations

import inspect
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp015_screen as e15
import tools.exp017_resim as r17
import tools.exp017_screen as x17
import tools.exp020_grid as g20

NF = len(fz.FROZEN_FEATURE_NAMES)
P2_START = e15.hour_ms(e15.BLOCKS["P2"][0])
LAM = 1_000_000_000
VIEWS = ["--p1-fast-dir", "a", "--p1-oracle-insample-dir", "b", "--p1-oracle-live-dir", "c"]


def sim_cell(k: int, sol: float, net0: int, censored: bool = False, miss: bool = False) -> dict:
    return {"k": k, "exit_lag": 2, "size": g20.lam(sol), "censored": censored, "filled": not miss, "status": e15.MISS if miss else 1, "net0": net0, "sides": 1 if miss else 2, "p_press": 0.15}


def grid_row(mint: str, mig_ms: int, nets: dict, feat0: float = 0.0, miss: set | None = None, censored: set | None = None) -> dict:
    """A sealed-cache row: every EXP-020 combo; nets maps (k, sol) -> net0."""
    cells = []
    for k, sol, _ in g20.COMBOS:
        cells.append(e15._slim_cell(sim_cell(k, sol, nets.get((k, sol), 1_000_000), censored=(k, sol) in (censored or set()), miss=(k, sol) in (miss or set()))))
    return {"mint": mint, "spec": e15.TARGET, "day": e15.utc_date(mig_ms), "mig_ms": mig_ms, "gap_ms": 400, "features": [feat0] * NF, "cells": cells}


def urow(mint: str, mig_ms: int, block: str = "P2") -> dict:
    return {"mint": mint, "date": e15.utc_date(mig_ms), "mig_ms": mig_ms, "source": block, "block": block, "features": [0.0] * NF, "cells": {}, "c1": 0, "c1_missing": True}


def make(rows_spec):
    rows = [urow(m, t) for m, t, _ in rows_spec]
    cache_rows = {m: grid_row(m, t, n) for m, t, n in rows_spec}
    return rows, g20.load_grid_cells(cache_rows), cache_rows


class TestGrid(unittest.TestCase):
    def test_combos(self):
        self.assertEqual(len(g20.COMBOS), 13)
        self.assertEqual(g20.COMBOS[0], (6, 0.05, 2))
        self.assertEqual({(k, s) for k, s, _ in g20.GRID_COMBOS}, {(k, s) for k in (2, 3, 4, 6) for s in (0.25, 0.5, 1.0)})
        self.assertTrue(all(lag == 2 for _, _, lag in g20.COMBOS))

    def test_cell_report_values(self):
        t = [P2_START + i * 86_400_000 for i in range(3)]
        rows, cells, _ = make([(f"m{i}", t[i], {(6, 0.5): 2_000_000 * (i + 1)}) for i in range(3)])
        rep = g20.cell_report(rows, cells, 6, 0.5, 27)
        self.assertEqual(rep["n"], 3)
        self.assertEqual(rep["n_without_cell"], 0)
        exp = [e15.cell_nets(c[(6, g20.lam(0.5))])["flat"] for c in cells.values()]
        self.assertAlmostEqual(rep["flat"]["mean_sol"], sum(exp) / 3 / LAM)
        self.assertAlmostEqual(rep["flat"]["mean_pct_of_stake"], rep["flat"]["mean_sol"] / 0.5 * 100)
        self.assertEqual(rep["flat"]["n_scope_dates"], 27)
        self.assertEqual(rep["miss_share"], 0.0)
        self.assertIsNotNone(rep["flat"]["ci90_date_sol"])
        self.assertIsNotNone(rep["press"]["ci90_trade_sol"])

    def test_miss_share_and_missing_cells(self):
        t = P2_START
        rows = [urow("a", t), urow("b", t + 1000), urow("c", t + 2000)]
        cache = {"a": grid_row("a", t, {}, miss={(4, 0.25)}), "b": grid_row("b", t, {}), "c": grid_row("c", t, {}, censored={(4, 0.25)})}
        rep = g20.cell_report(rows, g20.load_grid_cells(cache), 4, 0.25, 27)
        self.assertEqual((rep["n"], rep["n_without_cell"]), (2, 1))
        self.assertEqual(rep["miss_share"], 0.5)
        self.assertEqual(rep["flat"]["filled"], 1)

    def test_paired_x_vs_k6_same_size(self):
        t = [P2_START + i * 86_400_000 for i in range(3)]
        rows, cells, _ = make([(f"m{i}", t[i], {(2, 1.0): 5_000_000, (6, 1.0): 3_000_000, (2, 0.25): 9_000_000}) for i in range(3)])
        p = g20.paired_vs_base(rows, cells, 2, 1.0)
        a = e15.cell_nets(cells["m0"][(2, g20.lam(1.0))])["flat"]
        b = e15.cell_nets(cells["m0"][(6, g20.lam(1.0))])["flat"]
        self.assertEqual(p["n_pairs"], 3)
        self.assertAlmostEqual(p["flat"]["mean_x_sol"], (a - b) / LAM)
        self.assertAlmostEqual(p["flat"]["mean_x_pct_of_stake"], (a - b) / LAM * 100)
        p25 = g20.paired_vs_base(rows, cells, 2, 0.25)  # the base is k6 at the SAME size
        c = e15.cell_nets(cells["m0"][(2, g20.lam(0.25))])["flat"]
        d = e15.cell_nets(cells["m0"][(6, g20.lam(0.25))])["flat"]
        self.assertAlmostEqual(p25["flat"]["mean_x_sol"], (c - d) / LAM)
        self.assertEqual(len(p["flat"]["ci90_date_sol"]), 2)

    def test_paired_drops_rows_lacking_either_cell(self):
        t = P2_START
        rows = [urow("a", t), urow("b", t + 1000)]
        cache = {"a": grid_row("a", t, {}, censored={(6, 0.5)}), "b": grid_row("b", t, {})}
        p = g20.paired_vs_base(rows, g20.load_grid_cells(cache), 3, 0.5)
        self.assertEqual((p["n_pairs"], p["n_dropped"]), (1, 1))

    def test_build_grid_shape_and_missing_refusal(self):
        rows, cells, _ = make([(f"m{i}", P2_START + i * 86_400_000, {}) for i in range(4)])
        out = g20.build_grid(rows, cells, 27)
        self.assertEqual(len(out["cells"]), 13)
        self.assertEqual(len(out["paired_vs_k6"]), 9)  # k in (2, 3, 4) x 3 sizes
        self.assertNotIn("k6_s0.5", out["paired_vs_k6"])
        cache = {r["mint"]: grid_row(r["mint"], r["mig_ms"], {}, censored={(2, 1.0)}) for r in rows}
        with self.assertRaises(g20.Refused):
            g20.build_grid(rows, g20.load_grid_cells(cache), 27)

    def test_render_md(self):
        rows, cells, _ = make([(f"m{i}", P2_START + i * 86_400_000, {}) for i in range(4)])
        md = g20.render_md({"head": "h", "equivalence": {"matched": 4, "mismatched": 0}, "n_rows": 4, "n_dates": 27, "grid": g20.build_grid(rows, cells, 27)})
        self.assertIn("REPORT-ONLY", md)
        self.assertIn("Paired x", md)


class TestEquivalenceAndPins(unittest.TestCase):
    def test_control_matches_exp015_cache_cell(self):
        _, _, sized = make([("m0", P2_START, {})])
        cache = {"m0": json.loads(json.dumps(sized["m0"]))}
        self.assertEqual(x17.equivalence_check(cache, sized), {"matched": 1, "mismatched": 0})
        bad = json.loads(json.dumps(sized["m0"]))
        c = [c for c in bad["cells"] if (c["k"], c["size"]) == (6, g20.lam(0.05))][0]
        c["net0"] += 1
        with self.assertRaises(x17.Refused):
            x17.equivalence_check(cache, {"m0": bad})

    def test_pin_from_plan_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "plan.md"
            p.write_text("text\n")
            self.assertIsNone(g20.grid_pin(p))
            p.write_text("GRID_MANIFEST_SHA256 = " + "a" * 64 + "\n")
            self.assertEqual(g20.grid_pin(p), "a" * 64)
            p.write_text("GRID_MANIFEST_SHA256 = " + "a" * 64 + "\nGRID_MANIFEST_SHA256 = " + "b" * 64 + "\n")
            with self.assertRaises(g20.Refused):
                g20.grid_pin(p)

    def test_one_tries_line_per_log_and_second_run_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ops, canon = Path(d) / "ops.jsonl", Path(d) / "canon.jsonl"
            g20.check_no_prior(ops, canon)
            g20.log_try([ops, ops, canon], Path(d))
            for lg in (ops, canon):
                lines = lg.read_text().splitlines()
                self.assertEqual(len(lines), 1)
                self.assertEqual(json.loads(lines[0])["config"]["key"], "exp020_grid")
            with self.assertRaises(g20.Refused):
                g20.check_no_prior(ops, Path(d) / "none.jsonl")
            with self.assertRaises(g20.Refused):
                g20.check_no_prior(Path(d) / "none.jsonl", canon)

    def test_report_refuses_without_pin_and_needs_absolute_log(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(x17, "check_canonical_tries", lambda p: None), mock.patch.object(g20, "grid_pin", lambda *a: None):
                rc = g20.main(["--report", *VIEWS, "--out-dir", d, "--tries-log", str(Path(d) / "ops.jsonl"), "--canonical-tries", str(Path(d) / "canon.jsonl")])
            self.assertEqual(rc, 2)
            self.assertEqual(g20.main(["--report", *VIEWS, "--out-dir", d, "--tries-log", "rel.jsonl"]), 2)

    def test_exactly_one_mode(self):
        self.assertEqual(g20.main([*VIEWS, "--out-dir", "/tmp/x"]), 2)

    def test_resim_run_pass_default_is_exp017_combos(self):
        self.assertIs(inspect.signature(r17.run_pass).parameters["combos"].default, r17.COMBOS)
        self.assertEqual(r17.COMBOS, x17.SIZED_COMBOS)


class TestPrecountBlind(unittest.TestCase):
    def test_precount_reads_no_nets_and_no_tape(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "cache"
            cache.mkdir()
            for src in x17.SOURCES:
                rows = [grid_row(f"{src}{i}", P2_START + (30 + i) * 3_600_000, {}) for i in range(4)] if src == "P2" else []
                for r in rows:
                    for c in r["cells"]:
                        for k in x17.NET_KEYS:
                            c[k] = "POISON"
                (cache / f"v_{src}.rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

            def boom(*a, **k):
                raise AssertionError("touched")

            buf = io.StringIO()
            with mock.patch.object(x17, "check_manifests", lambda *a, **k: {}), mock.patch.object(x17, "check_cache_heads", lambda *a, **k: {}), \
                    mock.patch.object(x17, "frozen_scores", lambda u, a=None: [0.9] * len(u)), mock.patch.object(e15, "run_guards", boom), mock.patch.object(r17, "run_pass", boom), \
                    mock.patch.object(e15, "cell_nets", boom), mock.patch.object(e15, "leg_stats", boom), mock.patch.object(g20, "build_grid", boom), redirect_stdout(buf):
                rc = g20.main([*VIEWS, "--out-dir", str(Path(d) / "o"), "--scratch", d, "--precount"])
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertTrue(out["outcome_blind"])
            self.assertEqual(out["n_selected"], 4)
            self.assertEqual(out["n_selected_non_p1"], 4)
            self.assertEqual(out["n_combos"], 13)
            self.assertEqual(out["n_cells_to_simulate"], 4 * 13)
            self.assertFalse((Path(d) / "o").exists())


if __name__ == "__main__":
    unittest.main()
