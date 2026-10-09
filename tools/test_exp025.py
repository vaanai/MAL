"""Tests for the EXP-025 (C1-NF) bundle: pinned artifacts, the cap line, the deterministic wallet ledger, the power model, the EXP file's pinned line.
Fixtures only; nothing here opens /data/mal, a tape, a forward row or a sealed block."""

from __future__ import annotations

import hashlib
import importlib.util
import math
import os
import re
import tempfile
import unittest

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None
try:
    import duckdb
except ImportError:  # pragma: no cover
    duckdb = None

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ART = os.path.join(ROOT, "ARTIFACTS", "exp025")

# JUDGE-4 section 3.3.2 (verified 2026-10-09)
JUDGE4_PINS = {
    "RULE.md": "a22cebc481f36cda70fff07663e525b5b72ebee8741c843b7489038dbd49281c",
    "rule.json": "e158e0b9069a7db98a26c55b68f74055f44611330b2c636e68f37e4379c5c196",
    "scripts/common2.py": "39701e73a78a19e47f33eda05154efcf9095755bfe788e0d8762e83bd89e70bd",
    "scripts/10_meta.py": "5a25d0b122b5835ccea30922e73ce62a83e0a66af10d4f923495a09b7ba3346e",
    "scripts/11_passA.py": "24b1822e35a3b96337f8205370accc8eb9862d8411c9fda68296fb040ea79122",
    "scripts/12_passC.py": "22e6329415d40243ddcfab1b3f1793b25fd251cb9bf52be7e76e29300d6d0946",
    "scripts/14_export.py": "7dc2d151094defa349353fccdfdb92a6b542c9fa03828ad7dc77a3b3aecee868",
    "scripts/mlcommon.py": "bf783986e338cd924c2a99c5fbd3df2d5ec24d765772e654f44b5e63f4dca46f",
    "scripts/16_confirm.py": "c2fc497f141a9e650238beba26c4627eb51053a31e0e161ce6612cfc819ba0ae",
    "scripts/x07_posthoc_gate.py": "3daed3dd226f5288a4a391bc01a49976a15f2091fd1c0c1f448eaec41343812c",
    "ref/confirm_primary_trades.npz": "a5e27eb6035cef20534fbdd99e150b992aae8687d4b7f93f28f36c1812dedbf2",
}


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ART, rel))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class PinnedArtifacts(unittest.TestCase):
    def test_sha256sums_match_files(self):
        lines = [ln for ln in open(os.path.join(ART, "SHA256SUMS")).read().splitlines() if ln.strip()]
        self.assertGreaterEqual(len(lines), 20)
        for ln in lines:
            digest, rel = ln.split(None, 1)
            self.assertEqual(sha(os.path.join(ART, rel.strip())), digest, rel)

    def test_judge4_pins(self):
        sums = dict(reversed(ln.split(None, 1)) for ln in open(os.path.join(ART, "SHA256SUMS")).read().splitlines() if ln.strip())
        for rel, digest in JUDGE4_PINS.items():
            self.assertEqual(sums[rel], digest, rel)
            self.assertEqual(sha(os.path.join(ART, rel)), digest, rel)

    def test_every_artifact_file_is_pinned(self):
        listed = {ln.split(None, 1)[1].strip() for ln in open(os.path.join(ART, "SHA256SUMS")).read().splitlines() if ln.strip()}
        on_disk = set()
        for d, _, fs in os.walk(ART):
            for f in fs:
                rel = os.path.relpath(os.path.join(d, f), ART)
                if rel != "SHA256SUMS" and not rel.startswith("power" + os.sep) and "__pycache__" not in rel:
                    on_disk.add(rel)
        self.assertEqual(on_disk - listed, set(), "unpinned file in ARTIFACTS/exp025")


@unittest.skipIf(np is None, "numpy missing")
class CapLine(unittest.TestCase):
    def setUp(self):
        self.cap = load("c1nf_cap", "c1nf_cap.py")

    def test_threshold_and_nan(self):
        h = np.array([0.0, 0.0236, 0.5, 0.5000001, 0.7, 1.0, np.nan])
        self.assertEqual(self.cap.keep_mask(h).tolist(), [True, True, True, False, False, False, False])

    def test_applies_before_the_book(self):
        h = np.array([0.1, 0.9, np.nan, 0.3])
        sel = np.array([True, True, True, False])
        self.assertEqual(self.cap.apply_cap_before_book(h, sel).tolist(), [True, False, False, False])
        with self.assertRaises(ValueError):
            self.cap.apply_cap_before_book(h, sel[:3])


@unittest.skipIf(np is None or duckdb is None, "numpy or duckdb missing")
class LedgerDeterminism(unittest.TestCase):
    def setUp(self):
        self.det = load("wallet_det", "ledger/01_wallet_daily_det.py")

    def test_grid_is_exact_multiple(self):
        x = self.det.to_grid(np.array([1, 999_999_999, 123_456_789_012, -5_000_000_000], dtype=np.int64))
        self.assertTrue(np.all(x * 1048576 == np.rint(x * 1048576)))
        self.assertTrue(np.array_equal(x, self.det.to_grid(np.array([1, 999_999_999, 123_456_789_012, -5_000_000_000], dtype=np.int64))))

    def _fixture(self, d):
        # many wallets x many mints with awkward lamport sums, so a float-summing implementation would differ between runs
        con = duckdb.connect()
        rng = np.random.default_rng(5)
        n = 60_000
        rows = {
            "venue": np.where(rng.random(n) < 0.3, "pump_bonding", "pumpswap"),
            "mint": [f"m{int(i)}" for i in rng.integers(0, 40, n)],
            "trader": [f"w{int(i)}" for i in rng.integers(0, 300, n)],
            "side": np.where(rng.random(n) < 0.5, "buy", "sell"),
            "sol_lamports": rng.integers(1, 3_000_000_000, n),
        }
        import pandas as pd

        df = pd.DataFrame(rows)
        con.register("fx", df)
        for h in ("00", "01"):
            con.execute(f"COPY (SELECT * FROM fx WHERE random() < 0.5) TO '{d}/2026-01-01T{h}.parquet' (FORMAT parquet)")
        return [f"{d}/2026-01-01T00.parquet", f"{d}/2026-01-01T01.parquet"]

    def test_two_runs_bit_identical_across_thread_counts(self):
        with tempfile.TemporaryDirectory() as d:
            fs = self._fixture(d)
            outs = []
            for i, th in enumerate((4, 1)):
                c = duckdb.connect()
                c.execute(f"SET threads={th}; SET preserve_insertion_order=false")
                o = f"{d}/out{i}.parquet"
                self.det.build_day(c, fs, o)
                outs.append(sha(o))
            self.assertEqual(outs[0], outs[1])
            c = duckdb.connect()
            row = c.execute(f"SELECT count(*), sum(nwin) FROM '{d}/out0.parquet'").fetchone()
            self.assertGreater(row[0], 100)

    def test_matches_integer_reference(self):
        with tempfile.TemporaryDirectory() as d:
            fs = self._fixture(d)
            c = duckdb.connect()
            o = f"{d}/o.parquet"
            self.det.build_day(c, fs, o)
            ref = c.execute(
                f"""SELECT hash(trader) th, sum(CASE WHEN side='sell' THEN sol_lamports ELSE -sol_lamports END)::BIGINT cash_l
                    FROM read_parquet(['{fs[0]}','{fs[1]}']) WHERE venue IN ('pump_bonding','pumpswap') GROUP BY 1 ORDER BY 1"""
            ).df()
            got = c.execute(f"SELECT th, cash FROM '{o}' ORDER BY th").df()
            self.assertTrue(np.array_equal(got.th.to_numpy(), ref.th.to_numpy()))
            self.assertTrue(np.array_equal(got.cash.to_numpy(), self.det.to_grid(ref.cash_l.to_numpy(np.int64))))


@unittest.skipIf(np is None, "numpy missing")
class PowerModel(unittest.TestCase):
    def setUp(self):
        import tools.exp025_power as pw

        self.pw = pw

    def test_t_sf_known_values(self):
        self.assertAlmostEqual(self.pw.t_sf(0.0, 10), 0.5, places=9)
        self.assertAlmostEqual(self.pw.t_sf(2.0, 10), 0.03669, places=4)
        self.assertAlmostEqual(self.pw.t_sf(2.962430713628456, 20), 0.003850, places=4)  # VERIFY no-fail day-level t, 21 dates
        self.assertAlmostEqual(self.pw.t_sf(-2.0, 10), 1 - 0.03669, places=4)

    def test_model_matches_verify_numbers(self):
        m = self.pw.Model()
        s = m.summary(400_000)
        self.assertAlmostEqual(s["mean"], self.pw.SEPT_NOFAIL_MEAN, delta=0.004)
        self.assertAlmostEqual(s["median"], self.pw.MEDIAN, delta=0.004)
        self.assertAlmostEqual(s["win_rate"], self.pw.WIN_RATE, delta=0.005)
        self.assertAlmostEqual(s["p_lose90"], self.pw.P_LOSE90, delta=0.004)
        self.assertLess(abs(m.theta_for(1.0) - 1.0), 1e-9)
        self.assertLess(m.theta_for(0.5), m.theta_for(1.0))

    def test_pressure_p_reproduces_verify_pressure_mean(self):
        p = self.pw.pressure_p()
        got = (1 - p) * (self.pw.SEPT_NOFAIL_MEAN - self.pw.RENT) - p * self.pw.FAIL_COST
        self.assertAlmostEqual(got, self.pw.SEPT_PRESS_MEAN_RENT, places=9)
        flat = (1 - self.pw.FLAT_P) * (self.pw.SEPT_NOFAIL_MEAN - self.pw.RENT) - self.pw.FLAT_P * self.pw.FAIL_COST
        self.assertAlmostEqual(flat, 0.07458, places=4)  # VERIFY 5.2 rent leg, flat

    def test_power_is_monotone_in_window_and_edge(self):
        m = self.pw.Model()
        ds = self.pw.day_structure(m)
        rows = self.pw.simulate(m, ds, [7, 14], [1.0, 0.5], [20.0], [0.025], 120, seed=3)
        d = {(r["window_days"], r["edge_k"]): r["pass_alpha_0.025"] for r in rows}
        self.assertGreaterEqual(d[(14, 1.0)], d[(7, 1.0)] - 0.1)
        self.assertGreater(d[(14, 1.0)], d[(14, 0.5)])
        self.assertEqual(rows, self.pw.simulate(m, ds, [7, 14], [1.0, 0.5], [20.0], [0.025], 120, seed=3))


    def test_two_looks_sim_is_deterministic_and_bounded(self):
        m = self.pw.Model()
        ds = self.pw.day_structure(m)
        a = self.pw.simulate_looks(m, ds, [1.0, 0.25], [20.0], [(0.008, 0.017)], 80, seed=3)
        b = self.pw.simulate_looks(m, ds, [1.0, 0.25], [20.0], [(0.008, 0.017)], 80, seed=3)
        self.assertEqual(a, b)
        for r in a:
            c = r["0.008:0.017"]
            self.assertAlmostEqual(c["either"], c["look1"] + c["look2_given_look1_not_passed"], places=9)
            self.assertLessEqual(c["either"], 1.0)
        self.assertGreater(a[0]["0.008:0.017"]["either"], a[1]["0.008:0.017"]["either"])


class OctoberPatches(unittest.TestCase):
    """Quant-proof R1: the pinned October segment patches (a pinned patch, never a rule change)."""

    EXPECT = {
        1: ("2026-10-17T02", "7060cbd537571dc8a2da6249d4443adf2453224b8bca6aa46c68dbfbee803a11", "404669118447557f42bad7aa9cdb0b41e48ce6916b0cc7e003a7d643ece45722"),
        2: ("2026-10-24T02", "ca6d4b75790de653410df985e8598e8a336d0016d40c0535b5a73b2d0bd0ac7e", "885ce0d89f50c82562c2edeb572491080ef80d30ef1a328a67a3b7d6d7e32fb6"),
    }
    LATE = {  # the late-merge variants for C = 2026-10-11T00 (quant-proof S3): segment 3 ends one day later
        1: ("2026-10-18T02", "c87809cf3052999063ebd8c871539b05f1252ac9421ecdf29e0b0396a67120d2", "0a4ca896595c963f4964319ab0c51338ab82b56d3076e0f986cf3b434bfb8d40"),
        2: ("2026-10-25T02", "e5eb33d76524d57ea7f0a396e965fb5c233b1e6a55be388628b05d409e682c11", "58b24f71c7e8565a06c6734e19bffeb77a18ab4855a1760a2154644c51bcbb89"),
    }

    def test_late_merge_variants_are_pinned_and_differ_only_in_the_segment_end(self):
        txt = open(os.path.join(ROOT, "EXP", "EXP-025-c1nf-part1-prereg.md")).read()
        for look, (end, applied_sha, patch_sha) in self.LATE.items():
            pf = os.path.join(ART, "patches", "late_merge_C1011", f"common2_look{look}.patch")
            self.assertEqual(sha(pf), patch_sha)
            self.assertIn(patch_sha, txt)
            self.assertIn(applied_sha, txt)
            a = open(os.path.join(ART, "patches", f"common2_look{look}.patch")).read().splitlines()
            b = open(pf).read().splitlines()
            self.assertEqual(len(a), len(b))
            diff = [(x, y) for x, y in zip(a, b) if x != y]
            self.assertEqual(len(diff), 1)
            self.assertIn(end, diff[0][1])
            self.assertIn(self.EXPECT[look][0], diff[0][0])

    @unittest.skipIf(np is None, "numpy missing")
    def test_late_variants_apply_and_move_the_segment_end_one_day(self):
        import shutil
        import subprocess

        if shutil.which("patch") is None:
            self.skipTest("patch(1) missing")
        for look, (end, applied_sha, _) in self.LATE.items():
            with tempfile.TemporaryDirectory() as d:
                out = os.path.join(d, "common2.py")
                r = subprocess.run(["patch", "-o", out, os.path.join(ART, "scripts", "common2.py"),
                                    os.path.join(ART, "patches", "late_merge_C1011", f"common2_look{look}.patch")], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(sha(out), applied_sha)
                spec = importlib.util.spec_from_file_location(f"late{look}", out)
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                # Look 1 under C = 10-11 ends at 10-18T00; its last decision clips at SEGS[seg][1] - 3900 s and must not lose the last date
                self.assertEqual(m.seg_of(m.ep(end)), -1)
                self.assertEqual(m.seg_of(m.ep("2026-10-17T23")), 3)

    def test_patch_files_hash_and_are_named_in_the_exp(self):
        txt = open(os.path.join(ROOT, "EXP", "EXP-025-c1nf-part1-prereg.md")).read()
        for look, (_, applied_sha, patch_sha) in self.EXPECT.items():
            self.assertEqual(sha(os.path.join(ART, "patches", f"common2_look{look}.patch")), patch_sha)
            self.assertIn(patch_sha, txt)
            self.assertIn(applied_sha, txt)

    @unittest.skipIf(np is None, "numpy missing")
    def test_patches_apply_to_the_pinned_file_and_set_the_october_segment(self):
        import shutil
        import subprocess

        if shutil.which("patch") is None:
            self.skipTest("patch(1) missing")
        for look, (end, applied_sha, _) in self.EXPECT.items():
            with tempfile.TemporaryDirectory() as d:
                out = os.path.join(d, "common2.py")
                r = subprocess.run(["patch", "-o", out, os.path.join(ART, "scripts", "common2.py"), os.path.join(ART, "patches", f"common2_look{look}.patch")],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(sha(out), applied_sha)
                spec = importlib.util.spec_from_file_location(f"common2_look{look}", out)
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                ep = m.ep
                self.assertEqual(m.seg_of(ep("2026-10-12T00")), 3)
                self.assertEqual(m.seg_of(ep("2026-10-02T15")), 3)
                self.assertEqual(m.seg_of(ep("2026-10-02T14")), -1)
                self.assertEqual(m.seg_of(ep(end)), -1)  # the segment end is exclusive
                self.assertEqual(m.seg_of(ep("2026-10-24T02" if look == 1 else "2026-10-25T00")), -1)
                self.assertEqual([m.seg_of(ep(x)) for x in ("2026-08-20T00", "2026-09-10T00", "2026-09-20T00")], [0, 1, 2])
                self.assertEqual(len(m.SEGS), 4)
                self.assertEqual(m.O, f"/data/mal/exp025/look{look}")
                self.assertEqual(m.TAPE, f"/data/mal/exp025/look{look}/tape")
                self.assertEqual(m.SH, f"/data/mal/exp025/look{look}/hunt-shared")


class EventVMapping(unittest.TestCase):
    """Quant-proof R2 and S2: quote_reserve := vault + V(t) - V0, from the SAME print's own PRE-trade values (never chained)."""

    def setUp(self):
        self.m = load("event_v_map", "event_v_map.py")

    def test_total_quote_identity(self):
        for vault, v_t, v0 in ((100_000_000_000, 17_585_000_000, 17_585_000_000), (123_456_789_012, 17_540_000_000, 17_585_000_000), (20_000_000_000, 17_700_000_000, 17_500_000_000)):
            q = self.m.map_quote_reserve(vault, v_t, v0)
            self.assertEqual(q + v0, vault + v_t)

    def test_constant_v_is_the_september_behaviour(self):
        self.assertEqual(self.m.map_quote_reserve(55_000_000_000, 17_585_000_000, 17_585_000_000), 55_000_000_000)

    def test_mapping_uses_each_prints_own_vault_and_v(self):
        # print 1 follows an LP deposit of 7 that no trade explains: its own pre-trade vault is 107, not print 0's post-trade 101
        prints = [(100, 17_585), (107, 17_600), (109, 17_500)]
        self.assertEqual(self.m.mapped_series(prints, v0=17_585), [100, 107 + 17_600 - 17_585, 109 + 17_500 - 17_585])
        chained_wrong = 101 + 17_600 - 17_585
        self.assertNotEqual(self.m.mapped_series(prints, v0=17_585)[1], chained_wrong)

    def test_a_fee_sweep_keeps_vault_plus_v(self):
        # a sweep removes `swept` from the vault and the same amount from stored V's deficit: vault + V is unchanged, so the mapped total quote is too
        vault, v_t, swept = 50_000_000_000, 17_000_000_000, 123_456
        before = self.m.map_quote_reserve(vault, v_t, 17_585_000_000) + 17_585_000_000
        after = self.m.map_quote_reserve(vault - swept, v_t + swept, 17_585_000_000) + 17_585_000_000
        self.assertEqual(before, after)

    def test_p7_constant_product_thresholds_and_rule(self):
        self.assertEqual((self.m.P7_CP_SELL_MIN, self.m.P7_CP_BUY_MIN, self.m.P7_CP_TOLERANCE_BP), (0.99, 0.99, 1.0))
        self.assertTrue(self.m.p7_cp_pass(99, 100, 99, 100))
        self.assertFalse(self.m.p7_cp_pass(98, 100, 99, 100))
        self.assertFalse(self.m.p7_cp_pass(99, 100, 98, 100))
        self.assertFalse(self.m.p7_cp_pass(0, 0, 99, 100))
        self.assertFalse(self.m.p7_cp_pass(99, 100, 0, 0))

    def test_p7_needs_both_lines(self):
        ok_cp, bad_cp = (99, 100, 99, 100), (50, 100, 99, 100)
        ok_fee, bad_fee = (75, 100, 90, 100), (74, 100, 90, 100)
        self.assertTrue(self.m.p7_all_pass(ok_cp, ok_fee))
        self.assertFalse(self.m.p7_all_pass(bad_cp, ok_fee))
        self.assertFalse(self.m.p7_all_pass(ok_cp, bad_fee))

    def test_within_bp(self):
        self.assertTrue(self.m.within_bp(1_000_000, 1_000_100, 1.0))   # exactly 1 bp
        self.assertFalse(self.m.within_bp(1_000_000, 1_000_101, 1.0))
        self.assertTrue(self.m.within_bp(0, 0, 1.0))
        self.assertFalse(self.m.within_bp(0, 1, 1.0))

    def test_constant_product_separates_the_pinned_mapping_from_one_that_ignores_pending_fees(self):
        v0, base, base_in, qin = 17_585_000_000, 1_000_000_000_000_000, 3_000_000_000_000, 400_000_000
        vault_pre = 60_000_000_000
        pending = 2_300_000_000                      # about 3% of the effective quote, unswept in the vault
        v_t = v0 - pending                           # stored V net of the pending fees (the effective quote is vault + V(t))
        true_total = vault_pre + v_t
        gross_actual = true_total * base_in // (base + base_in)
        token_actual = base * qin // (true_total + qin)
        q_ok = self.m.map_quote_reserve(vault_pre, v_t, v0)
        self.assertEqual(self.m.cp_sell_gross_quote_out(q_ok, v0, base, base_in), gross_actual)
        self.assertEqual(self.m.cp_buy_token_out(q_ok, v0, base, qin), token_actual)
        # the naive column (gross vault, constant V0) overstates the quote by the pending amount
        q_naive = vault_pre
        g_naive = self.m.cp_sell_gross_quote_out(q_naive, v0, base, base_in)
        t_naive = self.m.cp_buy_token_out(q_naive, v0, base, qin)
        self.assertTrue(self.m.within_bp(gross_actual, self.m.cp_sell_gross_quote_out(q_ok, v0, base, base_in), self.m.P7_CP_TOLERANCE_BP))
        self.assertFalse(self.m.within_bp(gross_actual, g_naive, self.m.P7_CP_TOLERANCE_BP))
        self.assertFalse(self.m.within_bp(token_actual, t_naive, self.m.P7_CP_TOLERANCE_BP))
        self.assertGreater(abs(g_naive - gross_actual) * 10_000 / gross_actual, 100)  # at least 100 bp: orders of magnitude over the 1 bp bar

    def test_p7_decision_rule(self):
        self.assertEqual((self.m.P7_SAMPLE, self.m.P7_SELL_MIN, self.m.P7_BUY_MIN), (1000, 0.75, 0.90))
        self.assertTrue(self.m.p7_pass(75, 100, 90, 100))
        self.assertFalse(self.m.p7_pass(74, 100, 90, 100))
        self.assertFalse(self.m.p7_pass(75, 100, 89, 100))
        self.assertFalse(self.m.p7_pass(0, 0, 90, 100))
        self.assertFalse(self.m.p7_pass(75, 100, 0, 0))


class RawEventP7(unittest.TestCase):
    """Amendment 1: P7 line 1 runs on RAW events, because stored tape rows drop `pool_quote_amount` (the gross quote out and `qin`).

    Synthetic events use the integer laws of tools/test_walk2_event_v.py (test_constant_product_uses_vault_plus_event_v). The real-fixture tests
    decode public getTransaction results kept in the repo; no network, no /data/mal, no forward or walk row."""

    V0 = 17_585_000_000
    BASE = 1_000_000_000_000_000
    VAULT = 60_000_000_000
    PENDING = 2_300_000_000          # about 3% of the effective quote, kept in the vault and subtracted from stored V

    def setUp(self):
        self.m = load("event_v_map", "event_v_map.py")

    # -- synthetic raw events -------------------------------------------------------------------------------------------------------------
    def _base(self, side, i=0, pending=None):
        pending = self.PENDING if pending is None else pending
        v_t = self.V0 - pending
        return {"venue": "pumpswap", "pool": "POOL", "side": side, "slot": 1000 + i, "signature": f"sig{i}", "event_index": i % 3,
                "quote_reserve": self.VAULT, "base_reserve": self.BASE, "virtual_quote_reserves": v_t, "_total": self.VAULT + v_t}

    def _sell(self, i=0, pending=None):
        r = self._base("sell", i, pending)
        base_in = 3_000_000_000_000 + i * 1_000_000_000
        gross = r.pop("_total") * base_in // (self.BASE + base_in)
        r.update(token_raw=base_in, pool_quote_amount=gross, sol_lamports=gross * 9_925 // 10_000)
        return r

    def _buy(self, i=0, pending=None, ix_name="buy"):
        r = self._base("buy", i, pending)
        qin = 400_000_000 + i * 1_000
        out = self.BASE * qin // (r.pop("_total") + qin)
        r.update(token_raw=out, pool_quote_amount=qin, sol_lamports=qin * 10_125 // 10_000, ix_name=ix_name)
        return r

    @staticmethod
    def _tape(raw):
        """What stored_trade keeps: the raw fields minus the ones it drops."""
        return {k: v for k, v in raw.items() if k not in ("pool_quote_amount", "lp_fee", "protocol_fee", "creator_fee")}

    def _check(self, raw, v0=None, **kw):
        return self.m.p7_raw_check(self._tape(raw), raw, self.V0 if v0 is None else v0, **kw)

    # -- the finding, on the real decoder and the real store ------------------------------------------------------------------------------
    def _real(self, name):
        import json

        from observe.trade_decode import records_from_logs
        from observe.trade_store import stored_trade

        with open(os.path.join(ROOT, "tools", "fixtures", "walk2_event_v", name), encoding="utf-8") as fh:
            d = json.load(fh)
        (rec,) = records_from_logs(d["meta"]["logMessages"], slot=d["slot"], signature=d["signature"], t_recv_ms=0, commitment="confirmed", feed="t", event_v=True)
        return rec, stored_trade(rec)

    def test_stored_trade_drops_what_line_one_needs_and_the_raw_event_has_it(self):
        for name in ("sell_v2_kept.json", "buy_v1_481_wsol.json"):
            rec, tape = self._real(name)
            for k in ("pool_quote_amount", "lp_fee", "protocol_fee", "creator_fee"):
                self.assertNotIn(k, tape, (name, k))
            self.assertIsInstance(rec["pool_quote_amount"], int, name)
            for k in ("slot", "signature", "event_index", "pool", "side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve", "virtual_quote_reserves"):
                self.assertIn(k, tape, (name, k))

    def test_real_events_hit_with_the_pinned_mapping_whatever_v0_is(self):
        for name, line in (("sell_v2_kept.json", "sell"), ("buy_v1_481_wsol.json", "buy")):
            rec, tape = self._real(name)
            for v0 in (self.V0, 17_500_000_000, 17_700_000_000):
                self.assertEqual(self.m.p7_raw_check(tape, rec, v0), (line, "hit", None), (name, v0))
                self.assertEqual(self.m.raw_event_mapped(rec, v0) + v0, rec["quote_reserve"] + rec["virtual_quote_reserves"])
            # a mapping that ignores V(t) (constant V0) is right only if V0 happens to equal V(t)
            blind = lambda vault, v_pre, v0: vault  # noqa: E731
            self.assertEqual(self.m.p7_raw_check(tape, rec, 16_000_000_000, mapping=blind)[1], "miss", name)

    def test_real_exact_quote_in_buys_are_excluded_by_ix_name_of_the_tape_row(self):
        for name in ("buy_exact_quote_in_496.json", "buy_exact_quote_in_v2_499_kept.json", "boost_buy_and_burn.json"):
            rec, tape = self._real(name)
            self.assertTrue(tape["ix_name"].startswith("buy_exact_quote_in"), name)
            self.assertEqual(self.m.p7_raw_check(tape, rec, self.V0), (None, "excluded", "buy_exact_quote_in"), name)
        rec, tape = self._real("buy_v1_481_wsol.json")
        self.assertEqual(self.m.p7_raw_line(tape), "buy")

    # -- decision rules on synthetic raw events -------------------------------------------------------------------------------------------
    def test_pinned_mapping_hits_and_a_pending_blind_mapping_misses(self):
        blind = lambda vault, v_pre, v0: vault  # noqa: E731  (the September column: gross vault, constant V0)
        for raw, line in ((self._sell(), "sell"), (self._buy(), "buy")):
            self.assertEqual(self._check(raw), (line, "hit", None))
            self.assertEqual(self._check(raw, mapping=blind), (line, "miss", None))
            # the miss is not marginal: it is above 100 bp of the raw field
            q = blind(raw["quote_reserve"], raw["virtual_quote_reserves"], self.V0)
            if line == "sell":
                actual, model = raw["pool_quote_amount"], self.m.cp_sell_gross_quote_out(q, self.V0, self.BASE, raw["token_raw"])
            else:
                actual, model = raw["token_raw"], self.m.cp_buy_token_out(q, self.V0, self.BASE, raw["pool_quote_amount"])
            self.assertGreater(abs(model - actual) * 10_000 / actual, 100, line)
        # with no pending fees the two mappings agree: V(t) = V0 and the vault is the effective quote
        for raw in (self._sell(pending=0), self._buy(pending=0)):
            self.assertEqual(self._check(raw)[1], "hit")
            self.assertEqual(self._check(raw, mapping=blind)[1], "hit")

    def test_a_stale_vault_from_a_chain_across_an_lp_deposit_misses(self):
        # the print's own pre-trade vault is what the law holds on; a vault chained from the previous print misses it by the LP deposit
        lp_deposit = 1_000_000_000
        for raw, line in ((self._sell(), "sell"), (self._buy(), "buy")):
            self.assertEqual(self.m.raw_event_mapped(raw, self.V0), raw["quote_reserve"] + raw["virtual_quote_reserves"] - self.V0)
            self.assertTrue(self.m.p7_raw_hit(line, raw, self.V0))
            self.assertFalse(self.m.p7_raw_hit(line, dict(raw, quote_reserve=raw["quote_reserve"] - lp_deposit), self.V0))

    def test_one_bp_on_the_raw_field(self):
        for make, field in ((self._sell, "pool_quote_amount"), (self._buy, "token_raw")):
            raw = make()
            actual = raw[field]
            for delta_bp, want in ((0.0, "hit"), (0.9, "hit"), (-0.9, "hit"), (2.0, "miss"), (-2.0, "miss")):
                moved = dict(raw)
                moved[field] = actual + int(round(actual * delta_bp / 10_000))
                self.assertEqual(self._check(moved)[1], want, (field, delta_bp))

    def _dust(self, line, delta):
        """A dust print whose raw field is `delta` units away from the integer law. Returns the check outcome."""
        v_t = self.V0 - self.PENDING
        total = self.VAULT + v_t
        raw = self._sell() if line == "sell" else self._buy()
        if line == "sell":
            raw["token_raw"] = 1_000_000                                   # base_in: a dust sell
            model = total * raw["token_raw"] // (self.BASE + raw["token_raw"])
            raw["pool_quote_amount"] = model + delta
        else:
            raw["pool_quote_amount"] = 1                                   # qin: a dust buy
            model = self.BASE * 1 // (total + 1)
            raw["token_raw"] = model + delta
        field = "pool_quote_amount" if line == "sell" else "token_raw"
        self.assertEqual(self._check(dict(raw, **{field: model}))[1], "hit")   # the law itself is exact on the dust print
        return raw, model, self._check(raw)[1]

    def test_two_units_are_a_hit_when_one_bp_is_less_than_a_unit(self):
        self.assertEqual(self.m.P7_CP_TOLERANCE_UNITS, 2)
        for line, field in (("sell", "pool_quote_amount"), ("buy", "token_raw")):
            for delta, want in ((0, "hit"), (1, "hit"), (-1, "hit"), (2, "hit"), (-2, "hit"), (3, "miss"), (-3, "miss")):
                raw, model, got = self._dust(line, delta)
                self.assertEqual(got, want, (line, delta))
                if abs(delta) == 2:
                    # the unit alternative is what rescues it: 1 bp of the dust amount is under one unit, so within_bp alone fails
                    self.assertFalse(self.m.within_bp(raw[field], model, self.m.P7_CP_TOLERANCE_BP), (line, delta))
                    self.assertTrue(self.m.within_tolerance(raw[field], model))
        # the alternative does not widen the bar on a normal print: 2 bp off a large amount is still a miss
        self.assertEqual(self._check(dict(self._sell(), pool_quote_amount=self._sell()["pool_quote_amount"] * 10_002 // 10_000))[1], "miss")
        self.assertTrue(self.m.within_tolerance(0, 2))
        self.assertFalse(self.m.within_tolerance(0, 3))

    def test_a_pending_blind_mapping_still_misses_on_a_non_dust_print_with_the_unit_alternative(self):
        blind = lambda vault, v_pre, v0: vault  # noqa: E731
        for raw, line in ((self._sell(), "sell"), (self._buy(), "buy")):
            self.assertEqual(self._check(raw, mapping=blind), (line, "miss", None))

    def test_not_comparable_rows_leave_every_denominator(self):
        for name in ("buy_exact_quote_in", "buy_exact_quote_in_v2"):
            self.assertEqual(self._check(self._buy(ix_name=name)), (None, "excluded", "buy_exact_quote_in"))
        # a buy with no ix_name (missing, None or empty) is not comparable
        no_name = self._buy()
        del no_name["ix_name"]
        self.assertEqual(self._check(no_name), (None, "excluded", "no_ix_name"))
        self.assertEqual(self._check(self._buy(ix_name=None)), (None, "excluded", "no_ix_name"))
        self.assertEqual(self._check(self._buy(ix_name="")), (None, "excluded", "no_ix_name"))
        self.assertEqual(self.m.p7_raw_line(self._tape(no_name)), None)
        # every row with zero_sol, either side, even a buy_exact_quote_in or a nameless one (zero_sol is tested first)
        for raw in (self._sell(), self._buy(), self._buy(ix_name="buy_exact_quote_in"), no_name):
            self.assertEqual(self.m.p7_raw_check(dict(self._tape(raw), zero_sol=True), raw, self.V0), (None, "excluded", "zero_sol"))
        self.assertEqual(self.m.p7_raw_check({"side": "transfer"}, None, self.V0), (None, "excluded", "not_buy_or_sell"))
        self.assertEqual(self.m.p7_raw_exclusion({"side": "sell"}), None)                 # a sell has no ix_name and needs none
        # every other ix_name is comparable, including a name this file has not seen
        for name in ("buy", "buy_v2", "something_new"):
            self.assertEqual(self._check(self._buy(ix_name=name))[:2], ("buy", "hit"), name)
        self.assertEqual(self.m.P7_RAW_EXCLUSIONS, ("zero_sol", "not_buy_or_sell", "buy_exact_quote_in", "no_ix_name"))

    def test_a_raw_event_with_zero_sol_is_not_the_comparable_tape_print(self):
        raw = self._sell()
        self.assertEqual(self.m.p7_raw_check(self._tape(raw), dict(raw, zero_sol=True), self.V0), ("sell", "unresolved", "identity_mismatch"))

    def test_each_unresolved_reason_is_a_miss_on_its_line(self):
        raw = self._sell()
        tape = self._tape(raw)
        cases = {
            "fetch_failed": self.m.p7_raw_check(tape, None, self.V0, fetch_failed=True),
            "no_record": self.m.p7_raw_check(tape, None, self.V0),
            "no_v0": self.m.p7_raw_check(tape, raw, None),
            "slot_mismatch": self.m.p7_raw_check(tape, dict(raw, slot=raw["slot"] + 1), self.V0),
            "identity_mismatch": self.m.p7_raw_check(tape, dict(raw, base_reserve=raw["base_reserve"] + 1), self.V0),
            "field_missing": self.m.p7_raw_check(dict(tape, virtual_quote_reserves=None), {k: v for k, v in raw.items() if k != "virtual_quote_reserves"}, self.V0),
        }
        self.assertEqual(set(cases), set(self.m.P7_RAW_REASONS))
        for reason, out in cases.items():
            self.assertEqual(out, ("sell", "unresolved", reason), reason)
        # a record at another event_index or signature is not the print
        self.assertEqual(self.m.p7_raw_check(tape, dict(raw, event_index=raw["event_index"] + 1), self.V0)[2], "no_record")
        self.assertEqual(self.m.p7_raw_check(tape, dict(raw, signature="other"), self.V0)[2], "no_record")
        t = self.m.p7_raw_tally(cases.values())
        self.assertEqual((t["sell_n"], t["sell_ok"], t["buy_n"]), (len(cases), 0, 0))
        self.assertEqual(t["unresolved"], {r: 1 for r in self.m.P7_RAW_REASONS})

    def _sample(self, n_sell, n_buy, bad_sell=0, bad_buy=0, unresolved_sell=0, exact=0, zero_sol=0, no_name=0):
        results = []
        for i in range(n_sell):
            raw = self._sell(i)
            if i < bad_sell:
                raw["pool_quote_amount"] = raw["pool_quote_amount"] * 101 // 100        # 100 bp off the law
            if bad_sell <= i < bad_sell + unresolved_sell:
                results.append(self.m.p7_raw_check(self._tape(raw), None, self.V0, fetch_failed=True))
            else:
                results.append(self._check(raw))
        for i in range(n_buy):
            raw = self._buy(i)
            if i < bad_buy:
                raw["token_raw"] = raw["token_raw"] * 101 // 100
            results.append(self._check(raw))
        results += [self._check(self._buy(i, ix_name="buy_exact_quote_in_v2")) for i in range(exact)]
        results += [self.m.p7_raw_check(dict(self._tape(self._sell(i)), zero_sol=True), None, self.V0, fetch_failed=True) for i in range(zero_sol)]
        results += [self._check({k: v for k, v in self._buy(i).items() if k != "ix_name"}) for i in range(no_name)]
        return self.m.p7_raw_tally(results)

    def test_decision_rule_99_percent_per_side(self):
        self.assertEqual((self.m.P7_CP_SELL_MIN, self.m.P7_CP_BUY_MIN, self.m.P7_CP_TOLERANCE_BP), (0.99, 0.99, 1.0))
        t = self._sample(100, 100)
        self.assertEqual((t["sell_n"], t["sell_ok"], t["buy_n"], t["buy_ok"], t["excluded"]), (100, 100, 100, 100, 0))
        self.assertTrue(self.m.p7_raw_pass(t))
        self.assertTrue(self.m.p7_raw_pass(self._sample(100, 100, bad_sell=1)))       # 99 of 100
        self.assertFalse(self.m.p7_raw_pass(self._sample(100, 100, bad_sell=2)))      # 98 of 100
        self.assertTrue(self.m.p7_raw_pass(self._sample(100, 100, bad_buy=1)))
        self.assertFalse(self.m.p7_raw_pass(self._sample(100, 100, bad_buy=2)))
        self.assertTrue(self.m.p7_raw_pass(self._sample(200, 200, bad_sell=2, bad_buy=2)))     # 99% exactly, per side
        self.assertFalse(self.m.p7_raw_pass(self._sample(200, 200, bad_sell=3)))

    def test_unresolved_prints_stay_in_the_denominator(self):
        t = self._sample(100, 100, unresolved_sell=1)
        self.assertEqual((t["sell_n"], t["sell_ok"], t["unresolved"]["fetch_failed"]), (100, 99, 1))
        self.assertTrue(self.m.p7_raw_pass(t))
        t = self._sample(100, 100, unresolved_sell=2)
        self.assertEqual((t["sell_n"], t["sell_ok"]), (100, 98))
        self.assertFalse(self.m.p7_raw_pass(t))
        # a miss and an unresolved print add up against the same 1%
        self.assertFalse(self.m.p7_raw_pass(self._sample(100, 100, bad_sell=1, unresolved_sell=1)))

    def test_not_comparable_rows_change_no_denominator_and_a_side_with_none_fails(self):
        a = self._sample(100, 100)
        b = self._sample(100, 100, exact=40, zero_sol=7, no_name=5)
        skip = ("excluded", "excluded_by")
        self.assertEqual({k: v for k, v in b.items() if k not in skip}, {k: v for k, v in a.items() if k not in skip})
        self.assertEqual(b["excluded"], 52)
        self.assertEqual(b["excluded_by"], {"zero_sol": 7, "not_buy_or_sell": 0, "buy_exact_quote_in": 40, "no_ix_name": 5})
        self.assertTrue(self.m.p7_raw_pass(b))
        # a side with no comparable event fails that side, whatever the other side shows
        self.assertFalse(self.m.p7_raw_pass(self._sample(100, 0, exact=40)))                # only buy_exact_quote_in buys
        self.assertFalse(self.m.p7_raw_pass(self._sample(100, 0, no_name=40)))              # only nameless buys
        self.assertFalse(self.m.p7_raw_pass(self._sample(0, 100, zero_sol=40)))             # only zero_sol sells
        self.assertFalse(self.m.p7_raw_pass(self._sample(0, 100)))
        self.assertFalse(self.m.p7_raw_pass(self._sample(0, 0)))
        # a not-comparable row is excluded before fetch_failed is looked at, so it never counts as unresolved
        self.assertEqual(self._sample(100, 100, zero_sol=3)["unresolved"], {r: 0 for r in self.m.P7_RAW_REASONS})


class ExpFile(unittest.TestCase):
    def _text(self):
        p = os.path.join(ROOT, "EXP", "EXP-025-c1nf-part1-prereg.md")
        if not os.path.exists(p):
            self.skipTest("EXP-025 not written yet")
        return open(p).read()

    def _line(self, txt, key):
        m = re.findall(r"^" + key + r": (\S+)$", txt, flags=re.M)
        self.assertEqual(len(m), 1, key)
        return m[0]

    def test_pinned_lines_once_each(self):
        txt = self._text()
        self.assertEqual(self._line(txt, "EXP025_COUNT_START"), "2026-10-10T00")
        self.assertEqual(self._line(txt, "EXP025_LOOK1_END"), "2026-10-17T00")
        self.assertEqual(self._line(txt, "EXP025_COUNT_END"), "2026-10-24T00")

    def test_look_windows_are_7_and_14_dates(self):
        import datetime as dt

        txt = self._text()
        f = lambda k: dt.datetime.strptime(self._line(txt, k), "%Y-%m-%dT%H")
        self.assertEqual((f("EXP025_LOOK1_END") - f("EXP025_COUNT_START")).days, 7)
        self.assertEqual((f("EXP025_COUNT_END") - f("EXP025_COUNT_START")).days, 14)

    @unittest.skipIf(np is None, "numpy missing")
    def test_alpha_pair_matches_power_constants_and_sums_to_slot(self):
        import tools.exp025_power as pw

        txt = self._text()
        a1 = float(self._line(txt, "EXP025_ALPHA_LOOK1"))
        a2 = float(self._line(txt, "EXP025_ALPHA_LOOK2"))
        self.assertEqual((a1, a2), (pw.ALPHA_LOOK1, pw.ALPHA_LOOK2))
        self.assertAlmostEqual(a1 + a2, 0.025, places=9)
        self.assertEqual((pw.LOOK1_DATES, pw.TOTAL_DATES), (7, 14))

    def test_rule_block_is_rule_md_byte_for_byte(self):
        txt = self._text()
        m = re.search(r"^~~~rule\n(.*?)^~~~$", txt, flags=re.M | re.S)
        self.assertIsNotNone(m)
        self.assertEqual(hashlib.sha256(m.group(1).encode()).hexdigest(), JUDGE4_PINS["RULE.md"])

    def test_dec025_records_owner_decisions_and_dec026_scope(self):
        p = os.path.join(ROOT, "DEC", "DEC-025-c1nf-family.md")
        if not os.path.exists(p):
            self.skipTest("DEC-025 not written yet")
        txt = open(p).read()
        self.assertGreaterEqual(len(re.findall(r"OWNER_DECISION_CONFIRMED: 2026-10-09", txt)), 2)
        self.assertIn("DEC-026", txt)
        self.assertIn("TWO LOOKS", txt)

    def _amendment_1(self):
        txt = self._text()
        head = "### Amendment 1 (2026-10-09, before any counted hour): P7 line 1 runs on raw events"
        self.assertEqual(txt.count(head), 1)
        body = txt.split(head, 1)[1]
        self.assertEqual(body.count("\n## "), 1)           # the next H2 is "## Sources": the amendment is the last thing before it
        body, rest = body.split("\n## Sources", 1)
        self.assertTrue(txt.index("## Amendments") < txt.index(head) < txt.index("## Sources"))
        self.assertLess(txt.index("## 12. What is not decided here"), txt.index("## Amendments"))
        return body

    def test_amendment_1_agrees_with_the_helpers(self):
        m = load("event_v_map", "event_v_map.py")
        body = self._amendment_1()
        for reason in m.P7_RAW_REASONS:
            self.assertIn(f"`{reason}`", body, reason)
        for field in m.P7_RAW_IDENTITY_FIELDS:
            self.assertIn(f"`{field}`", body, field)
        self.assertEqual(m.P7_RAW_TX_ATTEMPTS, 3)
        self.assertIn("Up to 3 attempts per transaction (`P7_RAW_TX_ATTEMPTS`)", body)
        self.assertIn(f"begins with `{m.P7_EXACT_QUOTE_IN_PREFIX}`", body)
        # thresholds restated, not changed: two 99% bars and 1 bp, in the file's own P7 text and in the helpers
        self.assertEqual((m.P7_CP_SELL_MIN, m.P7_CP_BUY_MIN, m.P7_CP_TOLERANCE_BP), (0.99, 0.99, 1.0))
        self.assertEqual(body.count("at least **99%**"), 2)
        self.assertEqual(body.count("within **1 bp**"), 2)
        # EXP-024 alignment: 2 units as an alternative, and the not-comparable causes, named as the helpers name them
        self.assertEqual(m.P7_CP_TOLERANCE_UNITS, 2)
        self.assertIn("within **1 bp** or **2 lamports**", body)
        self.assertIn("within **1 bp** or **2 base units**", body)
        self.assertIn("`P7_CP_TOLERANCE_UNITS` = 2", body)
        for cause in m.P7_RAW_EXCLUSIONS:
            self.assertIn(f"`{cause}`", body, cause)
        self.assertIn("**A side with no comparable event fails that side.**", body)
        self.assertIn("R14 fires", body)
        self.assertIn("`pool_quote_amount`", body)

    def test_amendment_1_pins_the_decoder_blob_of_the_checkout(self):
        body = self._amendment_1()
        path = os.path.join(ROOT, "observe", "trade_decode.py")
        data = open(path, "rb").read()
        blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()   # the git blob id
        self.assertIn(f"blob `{blob}`", body)

    def test_amendment_1_changes_no_pinned_line(self):
        txt = self._text()
        for key in ("EXP025_COUNT_START", "EXP025_LOOK1_END", "EXP025_COUNT_END", "EXP025_ALPHA_LOOK1", "EXP025_ALPHA_LOOK2"):
            self._line(txt, key)                           # exactly once each, whole file including the amendment
        self.assertEqual(txt.count("EXP025_ALPHA_LOOK1: 0.005"), 1)
        self.assertEqual(txt.count("EXP025_ALPHA_LOOK2: 0.020"), 1)


if __name__ == "__main__":
    unittest.main()
