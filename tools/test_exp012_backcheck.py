"""Tests for tools/exp012_backcheck.py. Fixtures only; no real data."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tools.exploration_entry_model as eem
import tools.exp012_backcheck as bc
import tools.exp012_operating_point as op
from tools.exp011_freeze import TARGET_SPEC_ID
from tools.exp011_score import _book_trades
from tools.exp012_fixtures import write_fast_format_root
from tools.exp012_latency_sensitivity import load_oof
from tools.paper_attention_promote import book_stats

SOL = 1_000_000_000


def _view(root: Path, hours, sha=True):
    write_fast_format_root(root, hours, {})
    if sha:
        lines = []
        for f in sorted(root.rglob("*.zst")):
            lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  ./{f.relative_to(root)}")
        (root / "VIEW.sha256").write_text("\n".join(lines) + "\n")


def _row(mint, mig_ms, k=6, size=0.05, net0=int(0.01 * SOL), day="2026-08-16", filled=True, exit="trigger", gross=1, lag=2, p=0.3, status=1):
    r = {"mint": mint, "spec": TARGET_SPEC_ID, "day": day, "score": 0.9, "k": k, "size": op.size_lamports(size), "censored": False, "filled": filled,
         "status": status, "gross": gross, "net0": net0, "sides": 2, "p_press": p, "exit": exit, "mig_ms": mig_ms, "rug": {"one_step": False, "crash50": False, "rug": False}}
    if lag:
        r["exit_lag"] = lag
    return r


class GuardTests(unittest.TestCase):
    def test_reserved_roots_refused(self):
        for p in ("/data/mal/clean-view/fresh-0903/w1", "/data/mal/clean-view/fresh-0828/x", "/data/mal/clean-view/fresh-0808/x", "/data/mal/blocks/x", "/data/mal/clean-view/forward-1002/x", "/data/mal/blocks-clean/x"):
            with self.assertRaises(bc.Refused, msg=p):
                bc.guard_views([p])

    def test_missing_view_sha_refused(self):
        with tempfile.TemporaryDirectory() as d:
            _view(Path(d) / "w", ["2026-08-14T12"], sha=False)
            with self.assertRaises(bc.Refused):
                bc.guard_views([Path(d) / "w"], "2026-08-14T12", "2026-08-14T13")

    def test_hash_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as d:
            _view(Path(d) / "w", ["2026-08-14T12"])
            f = next((Path(d) / "w" / "trades").glob("*.zst"))
            f.write_bytes(b"tamper")
            with self.assertRaises(bc.Refused):
                bc.guard_views([Path(d) / "w"], "2026-08-14T12", "2026-08-14T13")

    def test_hour_outside_pool_refused(self):
        with tempfile.TemporaryDirectory() as d:
            for h in ("2026-09-10T12", "2026-10-02T00", "2026-08-28T12"):
                _view(Path(d) / h, [h])
                with self.assertRaises(bc.Refused, msg=h):
                    bc.guard_views([Path(d) / h])

    def test_ok_overlap_and_gap(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a", Path(d) / "b"
            _view(a, ["2026-08-14T12", "2026-08-14T13"])
            _view(b, ["2026-08-14T14"])
            g = bc.guard_views([a, b], "2026-08-14T12", "2026-08-14T15")
            self.assertEqual(len(g["roots"]), 3)
            self.assertEqual(len(g["view_sha256"]), 2)
            with self.assertRaises(bc.Refused):  # gap
                bc.guard_views([a], "2026-08-14T12", "2026-08-14T15")
            _view(b, ["2026-08-14T13"])
            with self.assertRaises(bc.Refused):  # overlap
                bc.guard_views([a, b], "2026-08-14T12", "2026-08-14T15")

    def test_main_refuses_with_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(bc.main(["--view-dir", "/data/mal/clean-view/fresh-0903/w1", "--out-dir", d]), 2)
            self.assertEqual(bc.main(["--out-dir", d]), 2)


class ConstantsTests(unittest.TestCase):
    def test_cells_match_docstring(self):
        self.assertEqual(len(bc.CELLS), 6)
        for c in bc.CELLS:
            self.assertIn(bc.cell_label(c), bc.__doc__)
        self.assertEqual(bc.cell_label(bc.PRIMARY), "thr 0.8031 k=6 size=0.05 fee=505000 exit_lag=2")
        self.assertEqual(bc.PRIMARY["exit_lag"], 2)
        self.assertEqual([c["exit_lag"] for c in bc.CELLS].count(0), 1)  # lag 0 only for the one optimistic cell
        self.assertEqual(bc.SENSITIVITY[0], {**bc.PRIMARY, "exit_lag": 0})
        self.assertEqual(len({bc.cell_id(c) for c in bc.CELLS}), 6)
        self.assertEqual(bc.PRIMARY["fee"], op.PRIMARY_FEE)

    def test_frozen_threshold_assertion(self):
        _s, thr, _d, _days = load_oof(bc.DEFAULT_ARTIFACT_DIR)
        bc.check_frozen_threshold(thr)
        self.assertEqual(round(thr, 4), 0.8031)
        with self.assertRaises(bc.Refused):
            bc.check_frozen_threshold(0.82)


class AnalysisTests(unittest.TestCase):
    T0 = bc.hour_ms(bc.COUNT_START)

    def _rows(self):
        buf = self.T0 - 3_600_000
        rows = [_row("buf", buf, net0=SOL)]  # huge winner in the buffer: must not count
        for i in range(12):
            rows.append(_row(f"m{i}", self.T0 + i * 86_400_000 // 2, net0=int((0.02 if i % 3 else -0.01) * SOL), exit="trigger" if i % 3 else "cap", gross=1 if i % 3 else -1, day=f"2026-08-{15 + i // 2:02d}"))
        rows.append({"mint": "u", "spec": TARGET_SPEC_ID, "day": "2026-08-16", "score": 0.1, "mig_ms": self.T0 + 5, "unselected": True})
        return rows

    def test_buffer_hours_excluded(self):
        rep = bc.analyze(self._rows())
        self.assertEqual(rep["n_migrations_buffer_only"], 1)
        prim = rep["cells"][0]
        self.assertEqual(prim["n_entered"], 12)
        self.assertLess(prim["flat"]["total_sol"], 0.5)  # the 1 SOL buffer winner is not in it
        self.assertEqual(rep["n_selected_counted"], 12)
        self.assertEqual(rep["n_migrations_scored_counted"], 13)

    def test_counts_and_ci_match_gate_helper(self):
        rows = self._rows()
        rep = bc.analyze(rows)
        prim = rep["cells"][0]
        self.assertEqual(prim["n_filled"] + prim["n_miss"], prim["n_entered"])
        self.assertEqual(prim["tp"] + prim["sl"] + prim["time_stop"], prim["n_filled"])
        trades, _c, _b = bc.cell_trades(bc.counted(rows), bc.PRIMARY)
        want = book_stats(_book_trades(trades, "press"))
        self.assertEqual(prim["press"]["ci90_sol"], want["mean_ci90_sol"])
        self.assertEqual(prim["press"]["mean_sol"], want["mean_sol"])
        flat = book_stats(_book_trades(trades, "flat"))
        self.assertEqual(prim["flat"]["ci90_sol"], flat["mean_ci90_sol"])

    def test_exit_lag_cells_are_separate(self):
        rows = self._rows() + [_row("x", self.T0 + 1, k=6, size=0.05, lag=0, net0=SOL // 100), _row("y", self.T0 + 1, k=6, size=0.25, lag=2, net0=SOL // 50)]
        rep = bc.analyze(rows)
        by = {c["label"]: c for c in rep["cells"]}
        self.assertEqual(by[bc.cell_label(bc.SENSITIVITY[0])]["n_entered"], 1)  # the lag-0 cell sees only the lag-0 row
        self.assertEqual(by[bc.cell_label(bc.PRIMARY)]["n_entered"], 12)
        self.assertEqual(by[bc.cell_label(bc.SENSITIVITY[1])]["n_entered"], 1)

    def test_miss_and_week_split_and_per_day(self):
        rows = self._rows() + [_row("miss", self.T0 + 7, filled=False, exit="none", status=0, net0=0)]
        rep = bc.analyze(rows)
        self.assertEqual(rep["cells"][0]["n_miss"], 1)
        halves = rep["primary_week_split"]
        self.assertEqual(halves["first_7_days"]["n"] + halves["last_6_days"]["n"], rep["cells"][0]["n_entered"])
        self.assertTrue(rep["primary_per_day"])

    def test_banner_exact_and_wording(self):
        rep = bc.analyze(self._rows())
        md = bc.render_md(rep)
        self.assertTrue(md.startswith(bc.BANNER))
        self.assertTrue(bc.BANNER.startswith("EXPLORATION, best-of-N context, not a promote, not gate evidence. Frozen EXP-012 (model md5 a1810d21…, thr 0.8031)"))
        self.assertTrue(bc.BANNER.endswith("the promotion gate on a fresh holdout."))
        self.assertIn("Cumulative tries on explore-0814: 7.", bc.BANNER)
        self.assertIn("Primary exit lag 2 slots; still optimistic versus the measured live exit leak (a25eb17 stops \u22120.3039 to \u22120.4338); the lag-0 cell is an upper bound.", bc.BANNER)
        self.assertIn("Outcome rules are read net of the measured sell shortfall (\u221216 bps) and sim-vs-live entry gap (+26.08 bps, job #175); MEV is not added.", bc.BANNER)
        self.assertIn("Context only for DEC-020; Option A remains the recommendation.", bc.BANNER)
        self.assertNotIn("Exit lag 0/2 slots", bc.BANNER)
        self.assertIn("Not unread: w1 days were outcome-read by DEC-017 candidate (a)", bc.BANNER)
        self.assertIn("N cells = 6", md)
        self.assertIn("Sharp-drop rate (#336 label", md)
        self.assertEqual(rep["cumulative_tries_on_pool"], 1 + len(bc.CELLS))

    def test_thirteen_counted_days_and_window_labels(self):
        self.assertEqual(bc.n_counted_days(), 13)
        self.assertEqual(bc.analyze(self._rows())["n_days"], 13)
        t0 = self.T0
        self.assertEqual(bc.day_label(t0), "2026-08-15")
        self.assertEqual(bc.day_label(t0 + 86_400_000 - 1), "2026-08-15")
        self.assertEqual(bc.day_label(t0 + 86_400_000), "2026-08-16")
        self.assertEqual(bc.day_label(bc.hour_ms(bc.POOL_END) - 1), "2026-08-27")

    def test_without_w1_and_size_notes_and_pct(self):
        w1 = bc.hour_ms(bc.W1_START)
        rows = self._rows() + [_row("inw1", w1 + 5, net0=SOL // 10)]
        rep = bc.analyze(rows)
        self.assertEqual(rep["primary_without_w1"]["n"], rep["cells"][0]["n_entered"] - 1)
        by = {c["label"]: c for c in rep["cells"]}
        self.assertIn("cannot support any live size (DEC-020", by[bc.cell_label(bc.SENSITIVITY[1])]["note"])
        self.assertIn("optimistic exit (upper bound)", by[bc.cell_label(bc.SENSITIVITY[0])]["note"])
        self.assertNotIn("optimistic exit (upper bound)", by[bc.cell_label(bc.SENSITIVITY[-1])]["note"])
        self.assertEqual(rep["cells"][0]["note"], "")
        md = bc.render_md(rep)
        self.assertIn("without w1's hours", md)
        self.assertIn("% size", md)


class VCoverageTests(unittest.TestCase):
    T0 = bc.hour_ms(bc.COUNT_START)

    def _prints(self, n_mints, missing_every, vmap):
        rows = []
        for i in range(n_mints):
            pool = f"pool{i}"
            vmap[pool] = None if (missing_every and i % missing_every == 0) else 17_000_000_000
            for j in range(5):
                rows.append({"venue": "pumpswap", "mint": f"m{i}", "pool": pool, "t_recv_ms": self.T0 + 1000 + j})
        return rows

    def test_null_is_missing_zero_is_covered_and_buffer_mints_ignored(self):
        vmap = {"pa": 0, "pb": None, "pc": 5}
        rows = [{"venue": "pumpswap", "mint": "a", "pool": "pa", "t_recv_ms": self.T0 + 1}, {"venue": "pumpswap", "mint": "b", "pool": "pb", "t_recv_ms": self.T0 + 1},
                {"venue": "pumpswap", "mint": "c", "pool": "pzz", "t_recv_ms": self.T0 + 1},
                {"venue": "pumpswap", "mint": "buf", "pool": "pb", "t_recv_ms": self.T0 - 5},
                {"venue": "pump_bonding", "mint": "d", "pool": "pb", "t_recv_ms": self.T0 + 1}]
        cov = bc.vmap_coverage(rows, vmap, self.T0)
        self.assertEqual((cov["prints"], cov["covered"], cov["missing"], cov["missing_pools"]), (3, 1, 2, 2))

    def test_two_percent_missing_refuses_before_analyze(self):
        vmap = {}
        cov = bc.vmap_coverage(self._prints(100, 50, vmap), vmap, self.T0)  # 2 of 100 mints missing = 2% of prints
        self.assertAlmostEqual(cov["missing_fraction"], 0.02)
        with self.assertRaises(bc.Refused):
            bc.check_v_coverage(cov)
        vmap2 = {}
        bc.check_v_coverage(bc.vmap_coverage(self._prints(200, 200, vmap2), vmap2, self.T0))  # 1 of 200 = 0.5%
        self.assertIn("prints=", bc.coverage_line(cov))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(bc, "guard_views", return_value={"roots": {}, "pool": [], "view_sha256": {}}), \
                mock.patch.object(bc, "check_frozen_threshold"), mock.patch.object(bc, "check_model_md5", return_value="x"), \
                mock.patch.object(bc.e11, "load_frozen_spec", return_value=(None, bc.FROZEN_THRESHOLD, [])), \
                mock.patch.object(bc, "check_vmap_sha", return_value="s"), mock.patch.object(bc, "v_prepass", return_value=(cov, {})), mock.patch.object(bc, "collect_rows") as collect, mock.patch.object(bc, "analyze") as analyze:
            self.assertEqual(bc.main(["--view-dir", "/x", "--out-dir", d]), 2)
            collect.assert_not_called()
            analyze.assert_not_called()


class VPopulationTests(unittest.TestCase):
    T0 = bc.hour_ms(bc.COUNT_START)
    END = bc.hour_ms(bc.POOL_END)

    def _migrating(self, n, bad, vmap):
        rows = []
        for i in range(n):
            pool = f"mp{i}"
            vmap[pool] = None if i < bad else 17_000_000_000
            rows += [{"venue": "pumpswap", "mint": f"mig{i}", "pool": pool, "t_recv_ms": self.T0 + 10 + j} for j in range(5)]
        return rows

    def test_non_migrating_and_excluded_pools_without_v_still_pass(self):
        vmap = {}
        rows = self._migrating(100, 0, vmap)
        rows += [{"venue": "pumpswap", "mint": f"never{i}", "pool": f"np{i}", "t_recv_ms": self.T0 + 10} for i in range(50)]  # not in migrations/
        rows += [{"venue": "pumpswap", "mint": f"old{i}", "pool": f"op{i}", "t_recv_ms": self.T0 - 10} for i in range(50)]  # migrated before the window
        rows += [{"venue": "pumpswap", "mint": f"late{i}", "pool": f"lp{i}", "t_recv_ms": self.END + 10} for i in range(5)]  # after the pool
        rows += [{"venue": "pumpswap", "mint": bc.eem.WSOL, "pool": "wsolpool", "t_recv_ms": self.T0 + 10}]  # wSOL-mint pool
        migrated = {f"mig{i}" for i in range(100)} | {f"old{i}" for i in range(50)} | {f"late{i}" for i in range(5)}
        cov = bc.vmap_coverage(rows, vmap, self.T0, self.END, migrated)
        self.assertEqual((cov["n_counted_mints"], cov["prints"], cov["missing"]), (100, 500, 0))
        bc.check_v_coverage(cov)

    def test_two_percent_of_migrating_pool_prints_missing_refuses(self):
        vmap = {}
        rows = self._migrating(100, 2, vmap)
        migrated = {f"mig{i}" for i in range(100)}
        cov = bc.vmap_coverage(rows, vmap, self.T0, self.END, migrated)
        self.assertAlmostEqual(cov["missing_fraction"], 0.02)
        with self.assertRaises(bc.Refused):
            bc.check_v_coverage(cov)

    def test_null_pool_of_migrating_mint_is_missing(self):
        cov = bc.vmap_coverage([{"venue": "pumpswap", "mint": "m", "pool": "p", "t_recv_ms": self.T0 + 1}], {"p": None}, self.T0, self.END, {"m"})
        self.assertEqual(cov["missing"], 1)

    def test_primary_trades_on_no_v_pools_counted(self):
        rows = [_row("a", self.T0 + 1), _row("b", self.T0 + 2)]
        self.assertEqual(bc.primary_no_v_trades(rows, {"a": {"p1"}, "b": {"p2"}}, {"p1"}), 1)
        self.assertEqual(bc.primary_no_v_trades(rows, {"a": {"p1"}, "b": {"p2"}}, set()), 0)

    def _run_main(self, d, collect, no_v):
        cov = {"prints": 10, "covered": 10, "missing": 0, "missing_fraction": 0.0, "missing_pools": 0, "max_missing_fraction": 0.01}
        with mock.patch.object(bc, "guard_views", return_value={"roots": {}, "pool": [], "view_sha256": {}}), \
                mock.patch.object(bc, "check_frozen_threshold"), mock.patch.object(bc, "check_model_md5", return_value="x"), mock.patch.object(bc, "check_vmap_sha", return_value="s"), \
                mock.patch.object(bc.e11, "load_frozen_spec", return_value=(None, bc.FROZEN_THRESHOLD, [])), \
                mock.patch.object(bc, "v_prepass", return_value=(cov, {"a": {"p1"}})), mock.patch.object(bc, "collect_rows", **collect), \
                mock.patch.object(bc, "adapter_no_v_pools", return_value=no_v):
            return bc.main(["--view-dir", "/x", "--out-dir", str(Path(d) / "out"), "--tries-log", str(Path(d) / "t.jsonl"), "--canonical-tries", str(Path(d) / "canon.jsonl")])

    def _statuses(self, d):
        out = []
        for name in ("t.jsonl", "canon.jsonl"):
            lines = [json.loads(x) for x in (Path(d) / name).read_text().splitlines()]
            self.assertEqual(len(lines), len(bc.CELLS))
            self.assertTrue(all(x["config"]["pool"] == "explore-0814" and x["role"] == "exploration" for x in lines))
            out.append({x["config"]["status"] for x in lines})
        return out

    def test_main_refuses_when_a_primary_trade_is_on_a_no_v_pool_and_logs_tries(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run_main(d, {"return_value": [_row("a", self.T0 + 1)]}, {"p1"}), 2)
            self.assertEqual(self._statuses(d), [{"refused_after_read"}, {"refused_after_read"}])
            self.assertFalse((Path(d) / "out" / "report.json").exists())
            self.assertFalse((Path(d) / "out" / "report.md").exists())

    def test_exception_after_rows_start_logs_aborted_after_read(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                self._run_main(d, {"side_effect": RuntimeError("boom")}, set())
            self.assertEqual(self._statuses(d), [{"aborted_after_read"}, {"aborted_after_read"}])
            self.assertFalse((Path(d) / "out" / "report.json").exists())

    def test_completed_run_logs_completed_and_writes_report(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(bc, "git_head", return_value="h"), mock.patch.object(bc, "v_coverage", return_value={"corrected": 0, "no_v": 0, "pumpswap_prints": 0, "no_v_pools": 0}):
                self.assertEqual(self._run_main(d, {"return_value": [_row("a", self.T0 + 1)]}, set()), 0)
            self.assertEqual(self._statuses(d), [{"completed"}, {"completed"}])
            self.assertTrue((Path(d) / "out" / "report.json").exists())

    def test_vmap_sha_pinned_and_canonical_path_absolute(self):
        self.assertEqual(bc.VMAP_SHA256, "2506f7d2d8475e44ca70a8c536dbb7405930b1092edca331dbbe611236b4d2f8")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.json").write_text("{}")
            with self.assertRaises(bc.Refused):
                bc.check_vmap_sha(Path(d) / "m.json")
        self.assertTrue(bc.CANONICAL_TRIES.is_absolute())
        self.assertEqual(bc.CANONICAL_TRIES.name, "tries.jsonl")
        rep = bc.analyze([_row("a", self.T0 + 1)])
        self.assertEqual(rep["day_definition"], "counted day = 24 h window from 12:00Z, not the gate's UTC day; bar 2 is gate-shaped, not the gate")



def _leg(mean, lo=0.001, ex3=0.5, dpos=9, dtot=13):
    return {"mean_sol": mean, "ci90_sol": [lo, mean + 0.01], "ex_top3_sol": ex3, "days_positive": dpos, "days_with_trades": dtot, "total_sol": mean * 100}


def _prim(n=150, flat=None, press=None):
    return {"n_entered": n, "flat": flat if flat is not None else _leg(0.002), "press": press if press is not None else _leg(0.002)}


def _halves(a=(0.001, 0.001), b=(0.001, 0.001)):
    h = lambda m: {"flat": {"mean_sol": m[0]}, "press": {"mean_sol": m[1]}}
    return {"first_7_days": h(a), "last_6_days": h(b)}


def _wo(flat=0.001, press=0.001):
    return {"flat": {"mean_sol": flat}, "press": {"mean_sol": press}}


class HaircutTests(unittest.TestCase):
    T0 = bc.hour_ms(bc.COUNT_START)

    def test_formula_on_a_filled_trade(self):
        r = _row("a", self.T0 + 1, net0=int(0.02 * SOL))  # size 0.05 SOL, proceeds 0.07 SOL
        want = -70_000_000 * (1 - (1 - 0.002608) * (1 - 0.0016))
        self.assertAlmostEqual(bc.haircut_delta(r), want, places=3)
        self.assertAlmostEqual(bc.haircut_delta(r), -294_267.9, places=0)
        self.assertEqual(bc.haircut_row(r)["net0"], r["net0"] + bc.haircut_delta(r))

    def test_misses_unquotable_sells_and_censored_untouched(self):
        self.assertEqual(bc.haircut_delta(_row("m", self.T0, filled=False, exit="none", status=0, net0=0)), 0.0)
        self.assertEqual(bc.haircut_delta(_row("f", self.T0, net0=-(op.size_lamports(0.05) + 2_039_280))), 0.0)  # sell could not be quoted: no proceeds
        self.assertEqual(bc.haircut_delta({"censored": True, "mint": "c"}), 0.0)

    def test_both_fail_models_get_the_haircut_and_raw_is_unchanged(self):
        rows = [_row("a", self.T0 + 1, net0=int(0.02 * SOL), p=0.3)]
        raw, _c, _b = bc.cell_trades(rows, bc.PRIMARY, haircut=False)
        net, _c2, _b2 = bc.cell_trades(rows, bc.PRIMARY)
        d = bc.haircut_delta(rows[0])
        self.assertAlmostEqual(net[0]["flat"] - raw[0]["flat"], 0.85 * d, places=3)
        self.assertAlmostEqual(net[0]["press"] - raw[0]["press"], 0.70 * d, places=3)
        rep = bc.analyze(rows)
        self.assertLess(rep["cells"][0]["flat"]["mean_sol"], rep["cells_raw"][0]["flat"]["mean_sol"])
        self.assertEqual(rep["cells_raw"][0]["flat"]["mean_sol"], raw[0]["flat"] / SOL)
        self.assertIn("RAW", bc.render_md(rep))


class ReadingTests(unittest.TestCase):
    def read(self, prim=None, halves=None, wo=None, status="completed"):
        return bc.read_outcome(prim or _prim(), halves or _halves(), wo or _wo(), status)

    def test_rule0_refused_or_aborted_gives_no_reading(self):
        for st in ("refused_after_read", "aborted_after_read"):
            r = self.read(status=st)
            self.assertEqual((r["matched_rule"], r["final_reading"]), (0, bc.RULES[0]))

    def test_rule1_n_below_100_is_inconclusive_even_with_negative_means(self):
        r = self.read(_prim(n=99, flat=_leg(-0.01), press=_leg(-0.01)))
        self.assertEqual(r["matched_rule"], 1)
        self.assertIn("inconclusive", r["final_reading"])

    def test_rule2_either_leg_mean_negative_is_strong_caution(self):
        self.assertEqual(self.read(_prim(flat=_leg(-0.0001), press=_leg(0.002)))["matched_rule"], 2)
        self.assertEqual(self.read(_prim(flat=_leg(0.002), press=_leg(-0.0001)))["matched_rule"], 2)
        self.assertIn("strong caution against any size step", self.read(_prim(flat=_leg(-1), press=_leg(-1)))["final_reading"])

    def test_rule3_all_bars_both_legs_and_wording(self):
        r = self.read()
        self.assertEqual(r["matched_rule"], 3)
        self.assertFalse(r["modifier"]["fired"])
        self.assertIn("consistent with EXP-012 still working on older days at a 2-slot exit lag", r["final_reading"])
        self.assertNotIn("realistic exit", r["final_reading"])
        self.assertIn("Option A (after the 10-16 forward read) remains the recommendation", r["final_reading"])
        self.assertTrue(r["bars"]["flat"]["all"] and r["bars"]["press"]["all"])

    def test_rule4_every_failing_bar_is_reachable(self):
        cases = {
            "flat ci lower <= 0 (pressure passes)": _prim(flat=_leg(0.002, lo=-0.001)),
            "press ci lower <= 0": _prim(press=_leg(0.002, lo=0.0)),
            "ex-top3 <= 0": _prim(flat=_leg(0.002, ex3=0.0)),
            "fewer than 5 days": _prim(press=_leg(0.002, dpos=3, dtot=4)),
            "minority of days positive": _prim(flat=_leg(0.002, dpos=6, dtot=13)),
        }
        for name, prim in cases.items():
            r = self.read(prim)
            self.assertEqual((r["matched_rule"], r["final_reading"]), (4, "does not support"), name)

    def test_modifier_downgrades_only_a_rule3_match(self):
        r = self.read(halves=_halves(a=(0.001, 0.001), b=(-0.001, 0.001)))
        self.assertEqual(r["matched_rule"], 3)
        self.assertTrue(r["modifier"]["fired"])
        self.assertEqual(r["final_reading"], "does not support")
        self.assertTrue(any("halves disagree" in x for x in r["modifier"]["reasons"]))
        r = self.read(wo=_wo(flat=-0.001))
        self.assertTrue(r["modifier"]["fired"] and r["final_reading"] == "does not support")
        self.assertTrue(any("without w1" in x for x in r["modifier"]["reasons"]))
        r = self.read(halves={"first_7_days": {"flat": None, "press": None}, "last_6_days": _halves()["last_6_days"]})  # an empty half cannot confirm the sign
        self.assertTrue(r["modifier"]["fired"])
        r = self.read(_prim(flat=_leg(-0.1)), halves=_halves(b=(-0.001, 0.001)))  # rule 2 stays rule 2
        self.assertEqual((r["matched_rule"], r["final_reading"]), (2, bc.RULES[2]))
        r = self.read(_prim(n=10), halves=_halves(b=(-0.001, 0.001)))
        self.assertEqual(r["matched_rule"], 1)

    def test_analyze_writes_the_matched_rule_into_report_and_markdown(self):
        rep = bc.analyze(AnalysisTests()._rows())
        self.assertEqual(rep["reading"]["matched_rule"], 1)  # 12 trades
        md = bc.render_md(rep)
        self.assertIn("Matched rule 1", md)
        self.assertIn("Final reading", md)
        self.assertIn("Modifier (rule 5", md)
        json.dumps(rep)


class CancelTests(unittest.TestCase):
    T0 = bc.hour_ms(bc.COUNT_START)

    def test_sigterm_after_rows_start_logs_aborted_and_restores_handler(self):
        import os
        import signal

        before = signal.getsignal(signal.SIGTERM)

        def cancel(*a, **k):
            os.kill(os.getpid(), signal.SIGTERM)
            raise AssertionError("handler should have unwound")

        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as cm:
                VPopulationTests()._run_main(d, {"side_effect": cancel}, set())
            self.assertEqual(cm.exception.code, 128 + signal.SIGTERM)
            self.assertEqual(VPopulationTests()._statuses(d), [{"aborted_after_read"}, {"aborted_after_read"}])
            self.assertFalse((Path(d) / "out" / "report.json").exists())
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    def test_completed_only_after_report_files_are_written(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(bc, "render_md", side_effect=RuntimeError("md failed")), mock.patch.object(bc, "git_head", return_value="h"), \
                    mock.patch.object(bc, "v_coverage", return_value={"corrected": 0, "no_v": 0, "pumpswap_prints": 0, "no_v_pools": 0}):
                with self.assertRaises(RuntimeError):
                    VPopulationTests()._run_main(d, {"return_value": [_row("a", self.T0 + 1)]}, set())
            self.assertEqual(VPopulationTests()._statuses(d), [{"aborted_after_read"}, {"aborted_after_read"}])  # no "completed" line was written

    def test_per_log_guard_no_aborted_line_for_a_cell_already_completed(self):
        with tempfile.TemporaryDirectory() as d:
            out, log = Path(d) / "out", Path(d) / "t.jsonl"
            full = bc.planned_report()
            part = dict(full, cells=full["cells"][:3])
            self.assertEqual(bc.log_tries(part, out, log, status="completed"), 3)
            self.assertEqual(bc.log_tries(full, out, log, status="aborted_after_read"), 3)
            lines = [json.loads(x)["config"] for x in log.read_text().splitlines()]
            self.assertEqual(len(lines), 6)
            by = {c["cell"]: c["status"] for c in lines}
            self.assertEqual([by[c["id"]] for c in full["cells"]], ["completed"] * 3 + ["aborted_after_read"] * 3)


class Md5Tests(unittest.TestCase):
    def test_model_md5(self):
        self.assertEqual(bc.check_model_md5(bc.DEFAULT_ARTIFACT_DIR), "a1810d219ed61db64a396f40dc302ce5")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "model.txt").write_text("not the model")
            with self.assertRaises(bc.Refused):
                bc.check_model_md5(Path(d))

    def test_head_recorded(self):
        self.assertRegex(bc.git_head(), r"^[0-9a-f]{40}$")


class PatchTests(unittest.TestCase):
    def test_backcheck_patch_scores_frozen_model_then_simulates_combos_and_restores(self):
        import numpy as np

        from tools.latency_curve import FLAT_FAIL

        fills = [SimpleNamespace(price_sol=1.0, venue="pumpswap", t_recv_ms=1000, side="buy"), SimpleNamespace(price_sol=0.5, venue="pumpswap", t_recv_ms=2000, side="sell")]
        mint = SimpleNamespace(mig_ms=bc.hour_ms(bc.COUNT_START) + 5)
        seen = []

        class Model:
            def predict(self, x, num_threads=1):
                return np.asarray([float(r[0]) for r in x])

        def fake_score(mint_id, m, feat, curve, through, hist, *, specs=None, size=None, priority=None, entry_land_k=None, exit_land_k=0):
            eem._fills_for(m, migrate=True)
            seen.append((entry_land_k, size, exit_land_k))
            net0 = (size or 50_000_000) // 10 - 7
            pri = priority or 505_000
            flat = eem.mixed_net(net0, 2, 1, pri, FLAT_FAIL)
            press = eem.mixed_net(net0, 2, 1, pri, 0.25)
            return [{"mint": mint_id, "spec": TARGET_SPEC_ID, "day": "2026-08-16", "status": 1, "filled": True, "gross": 5, "flat": flat, "press": press, "features": {"f": 0.9 if mint_id == "hi" else 0.1}}]

        saved = (eem.score_one, eem._fills_for, eem.mixed_net)
        try:
            eem.score_one, eem._fills_for = fake_score, lambda m, migrate=True: (fills, 0, 0, None)
            before = (eem.score_one, eem._fills_for, eem.mixed_net)
            with mock.patch.object(eem, "_state_index", lambda f, t, b: 0), mock.patch.object(eem, "_slot_time", lambda f, t, fb: 1000):
                with bc.backcheck_patch(Model(), bc.FROZEN_THRESHOLD, ["f"]):
                    low = eem.score_one("lo", mint, object(), None, 0, {})
                    hi = eem.score_one("hi", mint, object(), None, 0, {})
            self.assertEqual((eem.score_one, eem._fills_for, eem.mixed_net), before)
        finally:
            eem.score_one, eem._fills_for, eem.mixed_net = saved
        self.assertEqual([r.get("unselected") for r in low], [True])
        self.assertEqual(len(hi), len(bc.CELLS))
        self.assertEqual({(r["k"], r["size"], r.get("exit_lag", 0)) for r in hi}, {(k, op.size_lamports(s), lag) for k, s, lag in bc.COMBOS})
        self.assertTrue(all(r["mig_ms"] == mint.mig_ms and r["rug"] is not None for r in hi))
        self.assertIn((None, None, 0), seen)  # the frozen k=1 default call scored the model
        self.assertIn((6, op.size_lamports(0.25), 2), seen)
        rep = bc.analyze(low + hi)
        self.assertEqual(rep["n_selected_counted"], 1)


class RugLabelTests(unittest.TestCase):
    def _p(self, t, price, side="sell", venue="pumpswap"):
        return SimpleNamespace(venue=venue, t_recv_ms=t, price_sol=price, side=side)

    def test_label_is_the_imported_336_label_on_a_shared_fixture(self):
        import tools.exp012_rug_risk as rr

        self.assertIs(bc.rug_label, rr.rug_label)
        self.assertFalse(hasattr(bc, "RUG_ONE_STEP"))  # the local copy is gone
        shared = [[self._p(0, 1.0, "buy"), self._p(1000, 0.95), self._p(2000, 0.60)], [self._p(0, 1.0), self._p(1, 0.8), self._p(2, 0.65), self._p(3, 0.49)],
                  [self._p(0, 1.0), self._p(rr.WINDOW_MS + 1, 0.1), self._p(5, 0.1, venue="bonding")]]
        got = [bc.rug_label(f, 0, 0) for f in shared]
        self.assertEqual(got, [rr.rug_label(f, 0, 0) for f in shared])
        self.assertEqual([x["rug"] for x in got], [True, True, False])
        self.assertIsNone(bc.rug_label([], -1, 0))
        self.assertEqual(bc.RUG_K, rr.K)


class TriesTests(unittest.TestCase):
    def test_idempotent(self):
        rep = bc.analyze(AnalysisTests()._rows())
        with tempfile.TemporaryDirectory() as d:
            out, log = Path(d) / "out", Path(d) / "tries.jsonl"
            out.mkdir()
            self.assertEqual(bc.log_tries(rep, out, log), 6)
            self.assertEqual(bc.log_tries(rep, out, log), 0)
            (out / bc.MARKER).unlink()  # marker lost: the log scan still prevents doubles
            self.assertEqual(bc.log_tries(rep, out, log), 0)
            lines = [json.loads(x) for x in log.read_text().splitlines()]
            self.assertEqual(len(lines), 6)
            self.assertTrue(all(x["config"]["pool"] == "explore-0814" for x in lines))


if __name__ == "__main__":
    unittest.main()
