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


class ExpFile(unittest.TestCase):
    def test_pinned_count_start_line_once(self):
        p = os.path.join(ROOT, "EXP", "EXP-025-c1nf-part1-prereg.md")
        if not os.path.exists(p):
            self.skipTest("EXP-025 not written yet")
        txt = open(p).read()
        self.assertEqual(len(re.findall(r"^EXP025_COUNT_START: 2026-10-10T00$", txt, flags=re.M)), 1)
        self.assertEqual(len(re.findall(r"^EXP025_COUNT_END: ", txt, flags=re.M)), 1)


if __name__ == "__main__":
    unittest.main()
