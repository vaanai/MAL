"""Tests for tools/exp017_screen.py. Fixtures follow the REAL shapes: cache rows are built with exp015_screen._slim_cell and the record layout of
e15_v_patch (mint, spec, day, mig_ms, gap_ms, features, cells); create rows use the field set of the clean-view `creates/` rows."""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp015_screen as e15
import tools.exp017_screen as x17
from tools import mal_result

NF = len(fz.FROZEN_FEATURE_NAMES)
P2_START = e15.hour_ms(e15.BLOCKS["P2"][0])
P3_START = e15.hour_ms(e15.BLOCKS["P3"][0])
P4_START = e15.hour_ms(e15.BLOCKS["P4"][0])


def sim_cell(size: int, net0: int, censored: bool = False) -> dict:
    """A cell dict as the simulator emits it (inputs of e15._slim_cell)."""
    return {"k": 6, "exit_lag": 2, "size": size, "censored": censored, "filled": True, "status": 1, "net0": net0, "sides": 2, "p_press": 0.15}


def cache_row(mint: str, mig_ms: int, net0: int = 1_000_000, feat0: float = 0.0) -> dict:
    cells = [e15._slim_cell(sim_cell(x17.SIZE_1X, net0)), e15._slim_cell(sim_cell(x17.SIZE_1X, net0) | {"k": 4})]
    return {"mint": mint, "spec": e15.TARGET, "day": e15.utc_date(mig_ms), "mig_ms": mig_ms, "gap_ms": 400, "features": [feat0] * NF, "cells": cells}


def create_row(mint: str, block_time: int, mayhem=False) -> dict:
    return {"type": "create", "mint": mint, "trader": "t", "creator": "c", "event_ts": block_time, "quote_mint": "So11111111111111111111111111111111111111112",
            "name": "n", "symbol": "s", "is_mayhem_mode": mayhem, "v": 1, "source": "x", "feed": "f", "venue": "pump_bonding", "slot": 5, "signature": "sig",
            "event_index": 0, "block_time": block_time, "commitment": "confirmed", "tx_index": 0}


def urow(mint, mig_ms, block="P2", net0=1_000_000, source=None):
    c = e15._slim_cell(sim_cell(x17.SIZE_1X, net0))
    return {"mint": mint, "date": e15.utc_date(mig_ms), "mig_ms": mig_ms, "source": source or block, "block": block, "features": [0.0] * NF,
            "cells": {(6, 2): c}, "c1": 0, "c1_missing": True}


class TestShapes(unittest.TestCase):
    def test_cache_row_builds_universe_and_nets(self):
        rows = {"P2": [cache_row("m1", P2_START + 3_600_000)]}
        uni, stats = e15.build_universe(rows, {}, True)
        self.assertEqual(len(uni), 1)
        self.assertEqual(stats["by_source"]["P2"], 1)
        nets = x17.row_net(uni[0])
        self.assertIn("flat", nets)
        self.assertIn("press", nets)

    def test_pins(self):
        self.assertEqual(x17.THR90, 0.8030766588450794)
        root = Path(__file__).resolve().parent.parent
        rows = json.loads((root / "ARTIFACTS" / "exp012" / "oof_scores.json").read_text())["rows"]
        s = sorted(float(r["score"]) for r in rows)
        n = len(s)
        self.assertEqual(s[int(round(0.90 * (n - 1)))], x17.THR90)
        self.assertEqual(s[int(round(0.95 * (n - 1)))], x17.THR95)


class TestPrecountIsBlind(unittest.TestCase):
    def _write_scratch(self, tmp: Path, poison: bool) -> Path:
        cache = tmp / "cache"
        cache.mkdir()
        for src in x17.SOURCES:
            rows = [] if src != "P2" else [cache_row(f"m{i}", P2_START + (30 + i) * 3_600_000, feat0=float(i)) for i in range(5)]
            if poison:  # net fields the precount must never use: non-numeric garbage would crash any arithmetic on them
                for r in rows:
                    for c in r["cells"]:
                        for k in x17.NET_KEYS:
                            c[k] = "POISON"
            (cache / f"v_{src}.rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return tmp

    def test_blind_reader_drops_net_fields(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_scratch(Path(d), poison=False)
            rows = x17.read_cache_rows(Path(d) / "cache" / "v_P2.rows.jsonl", blind=True)
            for r in rows:
                for c in r["cells"]:
                    self.assertFalse(set(c) & x17.NET_KEYS)
            full = x17.read_cache_rows(Path(d) / "cache" / "v_P2.rows.jsonl", blind=False)
            self.assertIn("net0", full[0]["cells"][0])

    def test_precount_never_touches_nets(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_scratch(Path(d), poison=True)

            def boom(*a, **k):
                raise AssertionError("net field touched in precount")

            with mock.patch.object(e15, "cell_nets", boom), mock.patch.object(e15, "leg_stats", boom), mock.patch.object(e15, "scope_report", boom), \
                    mock.patch.object(x17, "row_net", boom), mock.patch.object(x17, "h3_gate", boom), mock.patch.object(x17, "paired", boom):
                uni, stats = x17.load_universe(d, blind=True)
                scores = [0.9 if i % 2 else 0.5 for i in range(len(uni))]
                flags = [None] * len(uni)
                klass = ["canonical"] * len(uni)
                counts = x17.precount(uni, stats, scores, flags, klass)
            self.assertEqual(counts["n_universe"], 5)
            self.assertEqual(counts["n_selected"]["frozen"]["P2"], 2)
            self.assertEqual(counts["h3_left_censored_rows"]["P2"], 0)

    def test_precount_source_has_no_outcome_reading_name(self):
        src = Path(x17.__file__).read_text(encoding="utf-8")
        body = src.split("def precount(")[1].split("def zero_cell_check")[0]
        for forbidden in ("cell_nets", "row_net", "net0", "p_press", "leg_stats", "h3_gate", "paired("):
            self.assertNotIn(forbidden, body)


class TestCreatesAndMayhem(unittest.TestCase):
    def test_scan_and_flags(self):
        with tempfile.TemporaryDirectory() as d:
            cdir = Path(d) / "creates"
            cdir.mkdir()
            bt = P2_START // 1000
            rows = [create_row("a", bt, True), create_row("b", bt, False), create_row("late", bt + 100_000, True), {"type": "trade", "mint": "a"}]
            nofield = create_row("nf", bt)
            del nofield["is_mayhem_mode"]
            rows.append(nofield)
            (cdir / "creates-x.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            got = x17.scan_creates({"P2": [d]}, {"P2": {"a", "b", "late", "nf", "gone"}})
            self.assertEqual(set(got), {"a", "b", "late", "nf"})
            uni = [urow(m, P2_START + 3_600_000) for m in ("a", "b", "late", "nf", "gone")]
            flags = x17.mayhem_flags(uni, got)
            self.assertEqual(flags, [True, False, None, None, None])  # "late": create not strictly before the migration -> not usable
            cov = x17.coverage_by_source(uni, flags)
            self.assertEqual(cov["P2"]["with_field"], 2)
            self.assertEqual(cov["P2"]["mayhem_true"], 1)

    def test_coverage_refusal(self):
        cov = {s: {"n": 10, "coverage": 0.95} for s in x17.SOURCES}
        x17.check_coverage(cov)
        cov["P3"]["coverage"] = 0.5
        with self.assertRaises(x17.Refused):
            x17.check_coverage(cov)
        cov["P3"]["coverage"] = 0.95
        cov["P1B"]["coverage"] = 0.0  # P1 coverage never refuses
        x17.check_coverage(cov)


class TestMasks(unittest.TestCase):
    def test_h1_h2_h4(self):
        scores = [0.5, 0.81, 0.9, 0.9]
        self.assertEqual(x17.frozen_mask(scores), [False, True, True, True])
        self.assertEqual(x17.h1_mask(scores, [None, True, False, None]), [False, False, True, True])
        self.assertEqual(x17.h2_mask(scores, ["canonical", "zero", "canonical", "none"]), [False, False, True, False])
        self.assertEqual(x17.h4_tier(scores), [0, 1, 2, 2])

    def test_v0_class(self):
        v = {"p_c": 17_584_505_288, "p_z": 0, "p_o": 25_000_000_000}
        uni = [urow(m, P2_START) for m in ("c", "z", "o", "n")]
        got = x17.v0_class(uni, v, pool_fn=lambda m: "p_" + m)
        self.assertEqual(got, ["canonical", "zero", "other", "none"])


class TestH3Causal(unittest.TestCase):
    def test_window_excludes_young_migrations_and_self(self):
        base = P3_START + 30 * 3_600_000
        # 40 old winners (2 h before), then a target row; and 40 losers only 10 min before the target (must not count)
        uni = [urow(f"w{i}", base - 7_200_000 + i, "P3", net0=2_000_000) for i in range(40)]
        uni += [urow(f"l{i}", base - 600_000 + i, "P3", net0=-1_000_000_000) for i in range(40)]
        target = urow("t", base, "P3", net0=-5_000_000_000)
        uni.append(target)
        gate, wn = x17.h3_gate(uni)
        ti = len(uni) - 1
        self.assertTrue(gate[ti])  # the young losers and the row's own huge loss are outside the window
        self.assertEqual(wn[ti], 40)

    def test_min_rows_and_level(self):
        base = P3_START + 30 * 3_600_000
        uni = [urow(f"w{i}", base - 7_200_000 + i, "P3", net0=2_000_000) for i in range(10)] + [urow("t", base, "P3")]
        gate, _ = x17.h3_gate(uni)
        self.assertFalse(gate[-1])  # fewer than 30 rows in the window

    def test_left_censor_first_24h_of_each_block(self):
        uni = [urow("a", P3_START + 3_600_000, "P3"), urow("b", P3_START + 25 * 3_600_000, "P3"), urow("c", P4_START + 23 * 3_600_000, "P4", source="P4")]
        self.assertEqual(x17.left_censored(uni), [True, False, True])


class TestStats(unittest.TestCase):
    def test_holm(self):
        h = x17.holm({"H1": 0.001, "H2": 0.02, "H3": 0.5, "H4": 0.9})
        self.assertTrue(h["H1"]["reject"])  # 0.001 <= 0.05 / 4
        self.assertFalse(h["H2"]["reject"])  # 0.02 > 0.05 / 3
        self.assertFalse(h["H3"]["reject"])
        h2 = x17.holm({"H1": 0.01, "H2": 0.02, "H3": 0.03, "H4": 0.04})
        self.assertTrue(all(v["reject"] for v in h2.values()) is False)
        self.assertTrue(h2["H1"]["reject"])

    def test_boot_p_deterministic_and_directional(self):
        pos = {f"d{i}": [1.0, 2.0] for i in range(8)}
        neg = {f"d{i}": [-1.0, -2.0] for i in range(8)}
        self.assertLess(x17.boot_p(pos, draws=500), 0.01)
        self.assertGreater(x17.boot_p(neg, draws=500), 0.9)
        self.assertEqual(x17.boot_p(pos, draws=500), x17.boot_p(pos, draws=500))


class TestManifestAndPins(unittest.TestCase):
    def test_manifest_and_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "cache").mkdir()
            (Path(d) / "v_P1A").mkdir()
            (Path(d) / "cache" / "v_P1A.rows.jsonl").write_text("x\n")
            (Path(d) / "cache" / "v_P1A.manifest.json").write_text("{}\n")
            (Path(d) / "v_P1A" / "poolA-w0.jsonl").write_text("y\n")
            _, rawsha = x17.manifest(d, x17.RAW_PATTERNS)
            _, cachesha = x17.manifest(d, x17.CACHE_PATTERNS)
            out = x17.check_manifests(d, raw=rawsha, cache=cachesha)
            self.assertEqual(out["cache"]["n_files"], 2)
            (Path(d) / "cache" / "v_P1A.rows.jsonl").write_text("changed\n")
            with self.assertRaises(x17.Refused):
                x17.check_manifests(d, raw=rawsha, cache=cachesha)

    def test_pins_shape(self):
        for v in (x17.RAW_MANIFEST_SHA256, x17.CACHE_MANIFEST_SHA256, x17.CACHE_CODE_SHA[:0] or "0" * 64):
            self.assertEqual(len(v), 64)
        self.assertEqual(len(x17.CACHE_CODE_SHA), 40)

    def test_zero_cell_refusal(self):
        counts = {"n_selected": {c: {"non_p1": 5} for c in ("H1", "H2", "H4")}}
        x17.zero_cell_check(counts)
        counts["n_selected"]["H1"]["non_p1"] = 0  # H1 / H2 are report-only: never a refusal
        x17.zero_cell_check(counts)
        counts["n_selected"]["H4"]["non_p1"] = 0
        with self.assertRaises(x17.Refused):
            x17.zero_cell_check(counts)


class TestDroppedAndPin(unittest.TestCase):
    def test_family_is_h3_h4_and_holm_k2(self):
        self.assertEqual(x17.HCELLS, ("H3", "H4"))
        h = x17.holm({"H3": 0.02, "H4": 0.9})
        self.assertEqual(h["H3"]["threshold"], 0.025)  # alpha / 2

    def test_sized_pin_from_plan_line(self):
        with tempfile.TemporaryDirectory() as d:
            plan = Path(d) / "plan.md"
            plan.write_text("text\n", encoding="utf-8")
            self.assertIsNone(x17.sized_pin(plan))
            sha = "ab" * 32
            plan.write_text(f"## Amendment\nSIZED_MANIFEST_SHA256 = {sha}\n", encoding="utf-8")
            self.assertEqual(x17.sized_pin(plan), sha)
            plan.write_text(f"SIZED_MANIFEST_SHA256 = {sha}\nSIZED_MANIFEST_SHA256 = {'cd' * 32}\n", encoding="utf-8")
            with self.assertRaises(x17.Refused):
                x17.sized_pin(plan)


class TestResim(unittest.TestCase):
    def test_v_patch_env_selects_and_overrides_combos(self):
        import os

        import tools.exploration_entry_model as eem
        import tools.exp017_resim as rs

        with tempfile.TemporaryDirectory() as d:
            sel = Path(d) / "sel.json"
            sel.write_text(json.dumps(["keep"]))
            env = {e15.ENV_COMBOS: json.dumps([[k, s, lag] for k, s, lag in rs.COMBOS]), e15.ENV_SELECTED: str(sel)}
            before = eem.score_one
            with mock.patch.dict(os.environ, env):
                with e15.e15_v_patch():
                    # a non-selected mint returns before any scoring (base score_one is never reached with these None arguments)
                    self.assertEqual(eem.score_one("other", None, None, None, 0, None), [])
            self.assertIs(eem.score_one, before)
        self.assertEqual(rs.COMBOS, ((6, 0.05, 2), (6, 0.10, 2), (6, 0.25, 2), (6, 0.5, 2)))
        self.assertEqual([x17.SIZE_2X, *x17.C0_SIZES], [int(round(s * 1e9)) for s in rs.SIZES_SOL])

    def test_precount_mode_reads_no_net_and_no_tape(self):
        import io
        from contextlib import redirect_stdout

        import tools.exp017_resim as rs

        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "cache"
            cache.mkdir()
            for src in x17.SOURCES:
                rows = [cache_row(f"{src}{i}", P2_START + (30 + i) * 3_600_000) for i in range(4)] if src == "P2" else []
                for r in rows:
                    for c in r["cells"]:
                        for k in x17.NET_KEYS:
                            c[k] = "POISON"
                (cache / f"v_{src}.rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

            def boom(*a, **k):
                raise AssertionError("touched")

            buf = io.StringIO()
            with mock.patch.object(x17, "check_manifests", lambda *a, **k: {}), mock.patch.object(x17, "check_cache_heads", lambda *a, **k: {}), \
                    mock.patch.object(x17, "frozen_scores", lambda u, a=None: [0.9, 0.5, 0.9, 0.5][: len(u)]), mock.patch.object(e15, "run_guards", boom), \
                    mock.patch.object(e15, "cell_nets", boom), mock.patch.object(rs, "run_pass", boom), redirect_stdout(buf):
                rc = rs.main(["--p1-fast-dir", "a", "--p1-oracle-insample-dir", "b", "--p1-oracle-live-dir", "c", "--out-dir", str(Path(d) / "o"), "--scratch", d, "--precount"])
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertEqual(out["n_selected"], 2)
            self.assertEqual(out["n_cells_to_simulate"], 8)
            self.assertTrue(out["outcome_blind"])
            self.assertFalse((Path(d) / "o").exists())


class TestQuantProofFixes(unittest.TestCase):
    def test_h3_min_age_is_35_minutes(self):
        self.assertEqual(x17.H3_MIN_AGE_MS, 35 * 60_000)
        base = P3_START + 30 * 3_600_000
        old = [urow(f"w{i}", base - 7_200_000 + i, "P3", net0=2_000_000) for i in range(30)]
        mid = [urow(f"m{i}", base - 32 * 60_000 + i, "P3", net0=-1_000_000_000) for i in range(30)]  # 32 min old: still inside the purge, must not count
        uni = old + mid + [urow("t", base, "P3")]
        gate, wn = x17.h3_gate(uni)
        self.assertEqual(wn[-1], 30)
        self.assertTrue(gate[-1])

    def test_h4_missing_2x_cells_refuse_blind(self):
        uni = [urow(f"a{i}", P2_START + 3_600_000 * (i + 1)) for i in range(3)]
        scores = [0.9, 0.9, 0.82]  # two rows in the 2x tier (>= THR95), one in 1x
        for u in uni:
            u["sized"] = {x17.SIZE_2X: {"censored": False}}  # blind keys only: no net field is needed
        self.assertEqual(x17.h4_missing_cells(uni, scores), 0)
        uni[1]["sized"] = {}
        self.assertEqual(x17.h4_missing_cells(uni, scores), 1)
        uni[0]["sized"] = {x17.SIZE_2X: {"censored": True}}
        self.assertEqual(x17.h4_missing_cells(uni, scores), 2)
        uni[2]["sized"] = {}  # a 1x row never needs the 0.10 cell
        self.assertEqual(x17.h4_missing_cells(uni, scores), 2)

    def test_equivalence_check(self):
        cache = {"m": cache_row("m", P2_START + 3_600_000)}
        same = {"m": json.loads(json.dumps(cache["m"]))}
        self.assertEqual(x17.equivalence_check(cache, same), {"matched": 1, "mismatched": 0})
        diff = json.loads(json.dumps(cache["m"]))
        diff["cells"][0]["net0"] += 1
        with self.assertRaises(x17.Refused):
            x17.equivalence_check(cache, {"m": diff})
        feat = json.loads(json.dumps(cache["m"]))
        feat["features"][0] = 1.5
        with self.assertRaises(x17.Refused):
            x17.equivalence_check(cache, {"m": feat})
        with self.assertRaises(x17.Refused):
            x17.equivalence_check(cache, {"zz": same["m"]})
        with self.assertRaises(x17.Refused):
            x17.equivalence_check(cache, {})

    def test_sized_meta_checks(self):
        sel = "ab" * 32
        with tempfile.TemporaryDirectory() as d:
            def write(meta_over=None):
                for src in x17.SOURCES:
                    rp = Path(d) / f"v_{src}.rows.jsonl"
                    rp.write_text("", encoding="utf-8")
                    meta = {"tag": src, "head": "c" * 40, "combos": [list(c) for c in x17.SIZED_COMBOS], "selected_sha256": sel, "vmap_sha256": e15.VMAP_0909_SHA256}
                    meta.update(meta_over or {})
                    (Path(d) / f"v_{src}.manifest.json").write_text(json.dumps({"meta": meta, "rows_sha256": x17._file_sha256(rp)}), encoding="utf-8")

            write()
            self.assertEqual(x17.check_sized_meta(d, sel)["head"], "c" * 40)
            for over in ({"vmap_sha256": "0" * 64}, {"combos": [[6, 0.1, 2]]}, {"selected_sha256": "0" * 64}, {"head": "short"}):
                write(over)
                with self.assertRaises(x17.Refused):
                    x17.check_sized_meta(d, sel)

    def test_selected_text_matches_resim_file_format(self):
        uni = [urow("b", P2_START), urow("a", P2_START), urow("c", P2_START)]
        self.assertEqual(x17.selected_text(uni, [0.9, 0.95, 0.1]), json.dumps(["a", "b"]) + "\n")

    def test_h4_trade_uses_0p10_cell_filled_flag(self):
        u = urow("m", P2_START + 3_600_000)
        u["cells"][(6, 2)]["filled"] = True
        u["sized"] = {x17.SIZE_2X: {**e15._slim_cell(sim_cell(x17.SIZE_2X, 1_000_000)), "filled": False}}
        nets = [{"flat": 1.0, "press": 1.0}]
        self.assertFalse(x17.trades_of([u], nets, [0], [2])[0]["filled"])
        self.assertTrue(x17.trades_of([u], nets, [0], [1])[0]["filled"])

    def test_majority_denominator_is_dates_with_eligible_rows(self):
        uni = [urow("a", P3_START + 3_600_000, "P3"), urow("b", P3_START + 30 * 3_600_000, "P3")]  # day 09-03 row is left-censored
        scope = [i for i, c in enumerate(x17.left_censored(uni)) if not c]
        self.assertEqual(scope, [1])
        self.assertEqual(len({uni[i]["date"] for i in scope}), 1)

    def test_c0_has_uniform_0p10_row(self):
        uni = [urow("m", P2_START + 3_600_000)]
        uni[0]["sized"] = {sz: e15._slim_cell(sim_cell(sz, 1_000_000)) for sz in (x17.SIZE_2X, *x17.C0_SIZES)}
        out = x17.c0_report(uni, [True], True)
        self.assertIn("0.1", out)

    def test_e15_main_refuses_resim_env(self):
        import os

        with mock.patch.dict(os.environ, {e15.ENV_SELECTED: "/x"}):
            self.assertEqual(e15.main(["--out-dir", "/nonexistent"]), 2)
        with mock.patch.dict(os.environ, {e15.ENV_COMBOS: "[]"}):
            self.assertEqual(e15.main(["--out-dir", "/nonexistent"]), 2)


class TestH4VsUniform(unittest.TestCase):
    def _cells(self, h4_flat, h4_press):
        b1 = {"report": {"flat": {"total_sol": h4_flat, "mean_sol": 0.0}, "press": {"total_sol": h4_press, "mean_sol": 0.0}}}
        return {"H3": {"bars_all": False, "bars": {"B1": b1}}, "H4": {"bars_all": True, "bars": {"B1": b1}}}

    def _c0(self, flat, press):
        return {str(x17.SIZE_2X / x17.LAMPORTS): {"report": {"flat": {"total_sol": flat}, "press": {"total_sol": press}}}}

    HM = {"H3": {"reject": False}, "H4": {"reject": True}}

    def test_score_effect_branch_passes(self):
        cells = self._cells(1.0, 1.0)
        u = x17.h4_vs_uniform(cells, self._c0(0.5, 0.5))
        self.assertTrue(u["score_effect"])
        self.assertTrue(x17.decide(cells, self.HM, u).startswith("SCREEN PASS: H4"))

    def test_size_effect_branch_earns_nothing(self):
        cells = self._cells(1.0, 1.0)
        for c0 in (self._c0(2.0, 2.0), self._c0(0.5, 2.0), self._c0(2.0, 0.5), self._c0(1.0, 1.0)):  # not beating uniform on either leg (ties fail)
            u = x17.h4_vs_uniform(cells, c0)
            self.assertFalse(u["score_effect"])
            out = x17.decide(cells, self.HM, u)
            self.assertNotIn("SCREEN PASS", out)
            self.assertIn("H4: size effect, not score -- earns nothing", out)

    def test_missing_reference_is_not_a_score_effect(self):
        cells = self._cells(1.0, 1.0)
        u = x17.h4_vs_uniform(cells, {"status": "NOT_RUN"})
        self.assertFalse(u["score_effect"])
        self.assertNotIn("SCREEN PASS", x17.decide(cells, self.HM, u))


class TestFullRunSynthetic(unittest.TestCase):
    def _universe(self):
        rng = random.Random(3)
        uni = []
        dates = e15.non_p1_dates(True)
        for di, d in enumerate(dates):
            blk = "P2" if d in e15.block_dates("P2") else "P3" if d in e15.block_dates("P3") else "P4"
            day0 = e15.date_start_ms(d)
            for j in range(12):
                u = urow(f"{d}-{j}", day0 + (j + 1) * 3_600_000, blk, net0=rng.randint(-2_000_000, 3_000_000))
                u["sized"] = {x17.SIZE_2X: e15._slim_cell(sim_cell(x17.SIZE_2X, rng.randint(-4_000_000, 6_000_000))),
                              250_000_000: e15._slim_cell(sim_cell(250_000_000, 1_000_000)), 500_000_000: e15._slim_cell(sim_cell(500_000_000, 1_000_000))}
                uni.append(u)
        return uni

    def test_run_full_structure_and_results(self):
        uni = self._universe()
        scores = [0.9 if i % 3 == 0 else 0.84 if i % 3 == 1 else 0.1 for i in range(len(uni))]
        flags = [True if i % 10 == 0 else False for i in range(len(uni))]
        klass = ["canonical" if i % 4 else "zero" for i in range(len(uni))]
        rep = x17.run_full(uni, scores, flags, klass, with_sized=True)
        self.assertEqual(set(rep["cells"]), set(x17.HCELLS))
        for c in x17.HCELLS:
            self.assertEqual(set(rep["cells"][c]["bars"]), {f"B{i}" for i in range(1, 7)})
            self.assertIn(c, rep["holm"])
        self.assertTrue(rep["outcome"].startswith("SCREEN"))
        self.assertEqual(rep["c0"]["status"], "report-only")
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "tries.jsonl"
            info = x17.log_cell_tries(log, Path(d), x17.HCELLS, "started")
            self.assertEqual(len(log.read_text().splitlines()), len(x17.HCELLS))  # one started try per H cell
            self.assertEqual(len(x17.prior_exp017_lines(log)), len(x17.HCELLS))  # a second run is refused on these
            x17.write_results(Path(d), rep["trades"], info, log, "abc", 1.0)
            r = json.loads((Path(d) / "result_H4.json").read_text())
            self.assertEqual(mal_result.validate_result(r), [])

    def test_h4_without_sized_cache_refuses(self):
        uni = self._universe()
        with self.assertRaises(x17.Refused):
            x17.run_full(uni, [0.9] * len(uni), [False] * len(uni), ["canonical"] * len(uni), with_sized=False)

    def test_c0_not_run_without_sized(self):
        self.assertTrue(x17.c0_report([], [], False)["status"].startswith("NOT_RUN"))


if __name__ == "__main__":
    unittest.main()
