"""Tests for tools/exp014_screen_v2.py. Real row shapes (tools.test_exp014_m15_table.m15_hour_rows) and the P3 `.deduped.jsonl.zst` naming; no host path is read."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

import tools.exp014_m15_trigger as mt
import tools.exp014_screen_v2 as sv
import tools.exp015_screen as e15
from tools.exp012_fixtures import write_zst_jsonl
from tools.test_exp014_m15_table import m15_hour_rows
from tools.test_exp014_m15_trigger import score

H = ["2026-09-20T10", "2026-09-20T11", "2026-09-20T12"]
V = 17_584_500_000


def write_p3_layout(root: Path, hours: list[str] = H, drop: str | None = None, creates_everywhere: bool = True) -> e15.DedupedHours:
    """The fresh-0903 deduplicated naming: <root>/trades/trades-<hour>.deduped.jsonl.zst and creates/creates-<hour>.deduped.jsonl.zst."""
    creates, trades = m15_hour_rows(hours[0], "migM", 60, 7200)
    by: dict[str, list[dict]] = {h: [] for h in hours}
    for r in trades:
        by.setdefault(datetime.fromtimestamp(r["block_time"], tz=timezone.utc).strftime("%Y-%m-%dT%H"), []).append(r)
    for h in hours:
        if h != drop:
            write_zst_jsonl(root / "trades" / f"trades-{h}.deduped.jsonl.zst", by.get(h, []))
        if h == hours[0] or creates_everywhere:
            write_zst_jsonl(root / "creates" / f"creates-{h}.deduped.jsonl.zst", creates if h == hours[0] else [])
    return e15.DedupedHours({h: str(root) for h in hours}, {h: (h == hours[0] or creates_everywhere) for h in hours}, frozenset(hours))


def write_vmap(path: Path, v: int | None) -> str:
    path.write_text(json.dumps({"v": {"P1": v, "P2": v}}), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LayoutTests(unittest.TestCase):
    def test_p3_naming_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            hours = write_p3_layout(Path(td))
            info = hours(H[0])
            self.assertTrue(info["trade"].name.endswith(".deduped.jsonl.zst"))
            self.assertEqual(sv.check_hours_present({"P3": {"hours": hours, "pool": H}})["P3"], {"hours": 3, "hours_without_creates": 0})

    def test_missing_hour_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            hours = write_p3_layout(Path(td), drop=H[1])
            with self.assertRaises(sv.Refused) as cm:
                sv.check_hours_present({"P3": {"hours": hours, "pool": H}})
            self.assertIn(H[1], str(cm.exception))

    def test_creates_missing_over_fraction_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            hours = write_p3_layout(Path(td), creates_everywhere=False)
            with self.assertRaises(sv.Refused):
                sv.check_hours_present({"P3": {"hours": hours, "pool": H}})


class ReaderTests(unittest.TestCase):
    def test_truncated_zstd_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trades-2026-09-20T10.deduped.jsonl.zst"
            write_zst_jsonl(p, [{"i": i, "pad": "x" * 200} for i in range(5000)])
            good = list(sv.checked_lines(p))
            self.assertEqual(len(good), 5000)
            data = p.read_bytes()
            p.write_bytes(data[: len(data) // 2])
            with self.assertRaises(sv.ZstdFailed):
                list(sv.checked_lines(p))

    def test_bad_lines_are_counted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.jsonl"
            p.write_text('{"a": 1}\nnot json\n[1]\n\n{"b": 2}\n', encoding="utf-8")
            rd = sv.CheckedTrades()
            self.assertEqual(list(rd(p)), [{"a": 1}, {"b": 2}])
            self.assertEqual(rd.bad, 2)


@mock.patch.object(e15, "in_block_window", lambda block, ms: True)  # the fixture is dated 2026-09-20, outside every real block
class PrecountTests(unittest.TestCase):
    def counts(self, v: int | None, td: str) -> tuple[dict, dict]:
        hours = write_p3_layout(Path(td) / "p3")
        vm = Path(td) / "vmap.json"
        write_vmap(vm, v)
        parts = [sv.count_hour({"hours": hours, "hour": h, "vmap": str(vm)}) for h in H]
        return parts, {"P1": v, "P2": v}

    def test_counts_on_real_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            parts, vmap = self.counts(V, td)
            c = sv.merge_counts("P3", parts, vmap, 3)
            self.assertEqual(c["hours_found"], 3)
            self.assertEqual(c["creates_distinct"], 1)
            self.assertEqual(c["pumpswap_no_pool"], 0)
            self.assertEqual(c["pumpswap_no_v"], 0)
            self.assertEqual(sum(c["migrations_by_t_day"].values()), c["migrations_tape_order_in_block"])
            self.assertEqual(c["bad"], 0)
            self.assertGreater(c["rows"], 100)
            for forbidden in ("net0", "press", "flat", "filled"):
                self.assertNotIn(forbidden, json.dumps(parts))  # outcome-blind: no price or net field is ever computed

    def test_no_v_is_counted_and_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            parts, vmap = self.counts(None, td)
            c = sv.merge_counts("P3", parts, vmap, 3)
            self.assertGreater(c["pumpswap_no_v"], 0)
            self.assertEqual(c["pumpswap_no_v_fraction"], 1.0)
            self.assertTrue(any("no V" in r for r in sv.precount_refusals({"P3": c})))

    def test_missing_hour_count_refuses(self) -> None:
        c = {"tag": "P3", "hours_found": 2, "hours_expected": 3, "rows": 5, "bad_fraction": 0.0, "pumpswap": 1, "pumpswap_no_pool": 0, "pumpswap_no_v_fraction": 0.0,
             "migrations_with_create": 4, "unpriceable_migrations": 0}
        self.assertEqual(len(sv.precount_refusals({"P3": c})), 1)
        self.assertEqual(sv.precount_refusals({"P3": {**c, "hours_found": 3}}), [])
        self.assertTrue(sv.precount_refusals({"P3": {**c, "hours_found": 3, "unpriceable_migrations": 1}}))  # 25 % of migrating mints on a no-V pool

    def test_screen_needs_a_current_clean_precount(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = {"layout": {"P3": {"pool": H}}, "view_sha256": {}, "vmap_sha256": "x"}
            with self.assertRaises(sv.Refused):
                sv.check_precount(Path(td), g)
            digest = sv.layout_digest(g)
            (Path(td) / sv.OUT_PRECOUNT).write_text(json.dumps({"refused": False, "layout_digest": digest, "counts": {}}), encoding="utf-8")
            self.assertEqual(sv.check_precount(Path(td), g)["layout_digest"], digest)
            (Path(td) / sv.OUT_PRECOUNT).write_text(json.dumps({"refused": False, "layout_digest": "stale", "counts": {}}), encoding="utf-8")
            with self.assertRaises(sv.Refused):
                sv.check_precount(Path(td), g)
            (Path(td) / sv.OUT_PRECOUNT).write_text(json.dumps({"refused": True, "refusals": ["x"], "layout_digest": digest}), encoding="utf-8")
            with self.assertRaises(sv.Refused):
                sv.check_precount(Path(td), g)


class WorkerTests(unittest.TestCase):
    def run_worker(self, td: str, v: int | None) -> tuple[dict, list[dict]]:
        hours = write_p3_layout(Path(td) / f"p3-{v}")
        vm = Path(td) / f"vmap-{v}.json"
        sha = write_vmap(vm, v)
        rows_path, cens_path = Path(td) / f"rows-{v}.jsonl", Path(td) / f"cens-{v}.jsonl"
        spec = {
            "tag": "P3", "worker_id": 0, "home": [H[0]], "buf": H[1:], "creator_hist": {}, "hours": hours, "vmap": str(vm), "vmap_sha256": sha, "ks": sv.KS, "sizes": sv.SIZES, "exit_lag": sv.EXIT_LAG,
            "pool_end_ms": 10**13, "pool_gap_starts_ms": [], "rows_path": rows_path, "cens_path": cens_path,
        }
        orig = mt.print_from_trade_row
        out = sv.worker_v(spec)
        self.assertIs(mt.print_from_trade_row, orig)  # restored
        rows = [json.loads(line) for line in rows_path.read_text().splitlines() if line.strip()]
        return out, rows

    def test_v_is_active_in_the_worker_and_rows_carry_costs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_v, rows_v = self.run_worker(td, V)
            out_n, rows_n = self.run_worker(td, None)
            self.assertGreater(out_v["v_counts"]["corrected"], 0)
            self.assertEqual(out_n["v_counts"]["corrected"], 0)
            self.assertGreater(out_n["v_counts"]["no_v"], 0)
            self.assertTrue(rows_v and rows_n)
            self.assertEqual({r["exit_lag"] for r in rows_v if r["entry_land_k"] == 4}, {sv.EXIT_LAG, 4})  # the deciding lag 2 and the report-only lag = d
            self.assertEqual({r["exit_lag"] for r in rows_v if r["entry_land_k"] == 8}, {sv.EXIT_LAG, 8})
            self.assertEqual({r["amm_pool"] for r in rows_v}, {"P1"})  # R2: the real migration pool, not the source tag
            self.assertEqual({r["pool"] for r in rows_v}, {"P3"})
            for r in rows_v:
                self.assertIn(r["size"], sv.SIZES)
                self.assertIn(r["entry_land_k"], sv.KS)
                self.assertIn("p_press", r)
                self.assertIn("sides", r)
            self.assertEqual({(r["entry_land_k"], r["size"], r["exit_lag"]) for r in rows_v}, {(d, s, lg) for d in sv.KS for s in sv.SIZES for lg in (sv.EXIT_LAG, d)})
            self.assertNotEqual(json.dumps(rows_v, sort_keys=True), json.dumps(rows_n, sort_keys=True))  # V changes the pricing


class ExitLagTests(unittest.TestCase):
    def test_default_exit_lag_is_d_and_override_moves_the_sell(self) -> None:
        from tools.test_exp014_m15_trigger import exit_pool

        pool = exit_pool(70 * 10**9)  # a sl print at slot 3000
        r_default, _ = score(pool, 4)
        r_lag2, _ = score(pool, 4, exit_lag=2)
        assert r_default is not None and r_lag2 is not None
        self.assertEqual(r_default["exit_lag"], 4)
        self.assertEqual(r_lag2["exit_lag"], 2)
        self.assertEqual(r_lag2["exit_ms"] - r_default["exit_ms"], -2 * 400)


class CostTests(unittest.TestCase):
    def row(self, **kw: object) -> dict:
        r = {"mint": "M", "entry_land_k": 4, "size": 50_000_000, "day": "2026-09-10", "mig_ms": e15.hour_ms("2026-09-10T00") + 60_000, "source": "P4", "filled": True, "status": 1,
             "net0": 1_000_000, "sides": 2, "p_press": 0.2, "excluded_by_time": False, "features": {}}
        r.update(kw)
        return r

    def test_deciding_costs_apply_haircut_and_fee(self) -> None:
        r = self.row()
        n = sv.row_nets(r)
        want = e15.cell_nets({"censored": False, "filled": True, "status": 1, "net0": 1_000_000, "sides": 2, "size": 50_000_000, "p_press": 0.2})
        self.assertEqual(n, want)
        self.assertEqual(sv.FEE, 505_000)
        self.assertEqual(sv.EXIT_LAG, 2)
        self.assertEqual(sv.SIZES, (50_000_000, 500_000_000))
        nohair = e15.cell_nets({"censored": False, "filled": True, "status": 1, "net0": 1_000_000, "sides": 2, "size": 50_000_000, "p_press": 0.2}, haircut=False)
        self.assertLess(n["flat"], nohair["flat"])  # the haircut costs something

    def test_prepare_rows_removals_are_counted(self) -> None:
        dates = ["2026-09-10"]
        rows = [self.row(amm_pool="ok"), self.row(mint="X", excluded_by_time=True), self.row(mint="U", amm_pool="NOV"), self.row(mint="D", day="2026-09-30"), self.row(mint="O", mig_ms=0)]
        out, drop = sv.prepare_rows(rows, dates, {"NOV"})
        self.assertEqual([r["mint"] for r in out], ["M"])
        self.assertEqual(drop, {"excluded_by_time": 1, "out_of_block_window": 1, "day_not_in_scope": 1, "no_v_amm_pool": 1})
        self.assertEqual(out[0]["press"], sv.row_nets(rows[0])["press"])  # the model label uses the deciding-cost press


class TriesTests(unittest.TestCase):
    def test_second_run_is_refused_and_both_logs_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ops, canon = Path(td) / "ops.jsonl", Path(td) / "canon.jsonl"
            sv.check_no_prior_tries(ops, canon)  # empty logs: ok
            sv.log_try([ops, canon], Path(td), "started")
            self.assertEqual(len(sv.prior_exp014_lines(ops)), 1)
            self.assertEqual(len(sv.prior_exp014_lines(canon)), 1)
            with self.assertRaises(sv.Refused):
                sv.check_no_prior_tries(ops, canon)
            with self.assertRaises(sv.Refused):
                sv.check_no_prior_tries(canon)

    def test_screen_mode_needs_an_absolute_ops_log(self) -> None:
        with self.assertRaises(sv.Refused):
            sv.check_tries_logs(None, Path("x"))
        with self.assertRaises(sv.Refused):
            sv.check_tries_logs("rel.jsonl", Path("x"))


class EvaluateTests(unittest.TestCase):
    def synth(self) -> tuple[list[dict], list[dict]]:
        dates = [f"2026-08-{d:02d}" for d in range(15, 29)] + [f"2026-09-{d:02d}" for d in range(3, 16)]
        rows: list[dict] = []
        for i, day in enumerate(dates):
            for j in range(12):
                for d in (4, 8):
                    for size in sv.SIZES:
                        win = j < 4
                        rows.append({"mint": f"{day}-{j}", "entry_land_k": d, "size": size, "day": day, "source": "P2" if day < "2026-09" else ("P3" if day < "2026-09-10" else "P4"),
                                     "filled": True, "status": 1, "net0": 40_000_000 if win else -9_000_000, "sides": 2, "p_press": 0.2, "flat": 1.0, "press": 1.0})
        for r in rows:
            n = sv.row_nets(r)
            r["flat"], r["press"] = n["flat"], n["press"]
        return rows, [{"day": r["day"], "mint": r["mint"], "score": 1.0} for r in rows if r["entry_land_k"] == 4 and r["size"] == sv.SIZES[0] and r["mint"].endswith(("-0", "-1", "-2", "-3"))]

    def test_pass_structure_and_holm_k1(self) -> None:
        rows, selected = self.synth()
        dates = sorted({r["day"] for r in rows})
        main = [r for r in rows if r["size"] == sv.DECIDING_SIZE]
        transfer = {"threshold_p90": 0.5, "selected": [r for r in main if r["entry_land_k"] == 4 and r["day"] < "2026-09" and r["mint"].endswith(("-0", "-1", "-2", "-3"))]}
        ev = sv.evaluate(rows, selected, transfer, frozen_mints=set(), dates=dates)
        self.assertEqual(list(ev["bars"]), ["bar1", "bar2", "bar3", "bar2b", "bar4", "bar5", "bar6", "item5_d8", "item6", "holm"])
        self.assertEqual(len(ev["bars"]["holm"]["report"]), sv.HOLM_K)
        self.assertEqual(ev["bars"]["holm"]["alpha"], 0.05)
        self.assertEqual(ev["n_selected"], len(selected))
        self.assertTrue(ev["bars"]["bar1"]["pass"])  # 27 dates, 28 winners x 27... the planted winners clear the gate
        self.assertFalse(ev["bars"]["item6"]["jaccard"]["pass"] and ev["bars"]["item6"]["jaccard"]["jaccard"] is None)
        self.assertIsNotNone(ev["report_only"]["stake_0.5_sol"]["report"])

    def test_empty_overlap_union_fails(self) -> None:
        j = sv.jaccard_vs_frozen(set(), {"a"}, set())
        self.assertFalse(j["pass"])
        self.assertTrue(sv.jaccard_vs_frozen({"a", "b"}, {"a", "b", "c"}, {"c"})["pass"])
        self.assertFalse(sv.jaccard_vs_frozen({"a"}, {"a"}, {"a"})["pass"])  # J = 1


class ModelPathTests(unittest.TestCase):
    def rows(self) -> list[dict]:
        import random

        rng = random.Random(1)
        out = []
        for di in range(8):
            day = f"2026-09-{3 + di:02d}"
            for j in range(40):
                f = {n: rng.random() for n in mt.FEATURE_NAMES}
                win = f[mt.FEATURE_NAMES[0]] > 0.7
                r = {"mint": f"{day}-{j}", "entry_land_k": 4, "size": sv.DECIDING_SIZE, "day": day, "source": "P3", "filled": True, "status": 1, "net0": 30_000_000 if win else -8_000_000,
                     "sides": 2, "p_press": 0.2, "excluded_by_time": False, "features": f, "pool": "P3"}
                n = sv.row_nets(r)
                r["flat"], r["press"] = n["flat"], n["press"]
                out.append(r)
        return out

    def test_nested_lodo_and_transfer_run_on_deciding_cost_labels(self) -> None:
        import tools.exp014_m15_model as mm

        rows = self.rows()
        days = sorted({r["day"] for r in rows})
        selected, folds, counts = mm.nested_lodo_select(rows, days, n_jobs=1)
        self.assertEqual(len(folds), len(days))
        self.assertTrue(selected)
        self.assertTrue(all(mm.label(r) == (1 if r["press"] > 0 else 0) for r in rows))
        tr = sv.transfer_selection(rows, days[:5], days[5:])
        self.assertEqual(tr["n_test"], 3 * 40)
        self.assertIsNotNone(tr["threshold_p90"])


class LockTests(unittest.TestCase):
    def test_a_spent_run_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            e15.check_run_lock(out)  # nothing there: fine
            e15.take_lock(out, "head", "h")
            e15.write_record(out, "completed", True, {sv.TRIES_KEY: "completed"})
            with self.assertRaises(e15.Refused):
                e15.check_run_lock(out)


class Item4bTests(unittest.TestCase):
    """R1: item 4b is a gating bar on the 14 August dates, n >= 100 waived."""

    def rows_for(self, aug_win: bool) -> tuple[list[dict], list[dict], list[str]]:
        dates = [f"2026-08-{d:02d}" for d in range(15, 29)] + [f"2026-09-{d:02d}" for d in range(3, 16)]
        rows, selected = [], []
        for day in dates:
            for j in range(12):
                k = 4 if day < "2026-09" else 8  # 4 a day in August (56 < 100), 8 a day in September (104 >= 100)
                good = j < k and (aug_win or day >= "2026-09")
                r = {"mint": f"{day}-{j}", "entry_land_k": 4, "size": sv.DECIDING_SIZE, "day": day, "source": "P2" if day < "2026-09" else "P3", "filled": True, "status": 1,
                     "net0": 40_000_000 if good else -9_000_000, "sides": 2, "p_press": 0.2}
                n = sv.row_nets(r)
                r["flat"], r["press"] = n["flat"], n["press"]
                rows.append(r)
                if j < k:
                    selected.append({"day": day, "mint": r["mint"], "score": 1.0})
        return rows, selected, dates

    def run_ev(self, aug_win: bool) -> dict:
        rows, selected, dates = self.rows_for(aug_win)
        return sv.evaluate(rows, selected, {"threshold_p90": 0.5, "selected": []}, set(), dates)

    def test_august_losers_fail_4b_even_when_september_passes(self) -> None:
        ev = self.run_ev(False)
        self.assertIn("bar2b", ev["bars"])
        self.assertTrue(ev["bars"]["bar2"]["pass"])  # the September gate passes
        self.assertFalse(ev["bars"]["bar2b"]["pass"])  # the August gate does not
        self.assertFalse(ev["passes"])

    def test_august_winners_pass_4b_without_n_100(self) -> None:
        ev = self.run_ev(True)
        rep = ev["bars"]["bar2b"]["report"]
        self.assertLess(rep["flat"]["n"], 100)  # 14 dates x 4 selected = 56: the n >= 100 bar is waived
        self.assertFalse(rep["flat"]["gate"]["n_ge_100"])
        self.assertTrue(ev["bars"]["bar2b"]["pass"])

    def test_report_only_legs_exist(self) -> None:
        ro = self.run_ev(True)["report_only"]
        self.assertIn("bars_without_edge_days", ro)
        self.assertIn("lag_equals_d", ro)


class OutcomeTests(unittest.TestCase):
    def test_outcome_lines(self) -> None:
        self.assertIn("never 'has an edge'", sv.outcome_line({"passes": True, "bars": {}}))
        self.assertIn("bar2", sv.outcome_line({"passes": False, "bars": {"bar1": {"pass": True}, "bar2": {"pass": False}}}))


if __name__ == "__main__":
    unittest.main()
