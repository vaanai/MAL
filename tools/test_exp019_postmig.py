"""Tests for tools/exp019_postmig.py. Fixtures use the REAL tape row shape (v:2 PumpSwap rows with pool, quote_reserve, base_reserve, price_sol, block_time,
t_recv_ms null on backfill rows, quote_is_wsol) and the REAL P3 file naming (trades-<hour>.deduped.jsonl.zst)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import tools.exp011_freeze as fz
import tools.exp015_screen as e15
import tools.exp017_screen as e17
import tools.exp019_postmig as x19

NF = len(fz.FROZEN_FEATURE_NAMES)
P2_START = e15.hour_ms(e15.BLOCKS["P2"][0])
WSOL = "So11111111111111111111111111111111111111112"
POOL = "4xpeQUoujxg79vgM5BenCc5VtjmAvqZB7FZfVoJ6RCfV"
V = 17_580_000_000
Q0 = 60_000_000_000  # vault quote before the first trade
B0 = 1_000_000_000_000_000


def prow(mint, slot, side, sol, bt, trader="w1", pool=POOL, q=Q0, b=B0, ev=0, tx=0, sig=None, wsol=True, venue="pumpswap"):
    return {"v": 2, "venue": venue, "mint": mint, "trader": trader, "side": side, "sol_lamports": sol, "token_raw": 1_000_000_000, "quote_reserve": q, "base_reserve": b,
            "price_sol": None, "pool": pool, "slot": slot, "signature": sig or f"sig-{slot}-{tx}-{ev}-{trader}-{side}", "event_index": ev, "t_recv_ms": None,
            "event_ts": bt, "quote_mint": WSOL if wsol else "other", "quote_is_wsol": wsol, "source": "backfill", "block_time": bt, "t_recv": None, "tx_index": tx}


MIG_BT = P2_START // 1000 + 3600 * 5 + 100
MIG_MS = MIG_BT * 1000
VMAP = {POOL: V}


def base_rows(mint="M"):
    return [prow(mint, 1000, "buy", 2_000_000_000, MIG_BT, "a"), prow(mint, 1001, "buy", 1_000_000_000, MIG_BT, "b", q=Q0 + 2_000_000_000),
            prow(mint, 1002, "sell", 500_000_000, MIG_BT + 1, "a", q=Q0 + 3_000_000_000)]


class TestFeatures(unittest.TestCase):
    def test_values(self):
        f = x19.features_from_rows(base_rows(), "M", MIG_MS, VMAP)
        self.assertEqual(f["status"], "ok")
        self.assertEqual(f["mig_slot"], 1000)
        self.assertEqual(f["net_flow_lamports"], 2_000_000_000 + 1_000_000_000 - 500_000_000)
        self.assertEqual(f["n_buyers"], 2)
        self.assertGreater(f["price_change"], 0)
        self.assertAlmostEqual(f["max_sell_share"], 500_000_000 / (Q0 + 3_000_000_000 + V))
        self.assertTrue(f["pool_has_v"])

    def test_causality_row_at_mig_slot_plus_5_ignored(self):
        a = x19.features_from_rows(base_rows(), "M", MIG_MS, VMAP)
        late = base_rows() + [prow("M", 1003, "sell", 40_000_000_000, MIG_BT + 2, "z", q=Q0 + 4_000_000_000), prow("M", 1004, "buy", 9_000_000_000, MIG_BT + 3, "y")]
        b = x19.features_from_rows(late, "M", MIG_MS, VMAP)
        self.assertEqual(a, b)
        edge = base_rows() + [prow("M", 1002, "sell", 1_000_000_000, MIG_BT + 2, "z2", q=Q0 + 4_000_000_000)]  # slot mig + 2 is inside; mig + 3 is not
        self.assertNotEqual(x19.features_from_rows(edge, "M", MIG_MS, VMAP), a)

    def test_other_pool_and_non_wsol_ignored(self):
        extra = base_rows() + [prow("M", 1001, "sell", 30_000_000_000, MIG_BT, "q", pool="OtherPool"), prow("M", 1001, "sell", 30_000_000_000, MIG_BT, "q", wsol=False)]
        self.assertEqual(x19.features_from_rows(extra, "M", MIG_MS, VMAP), x19.features_from_rows(base_rows(), "M", MIG_MS, VMAP))

    def test_no_mig_print_and_duplicates(self):
        self.assertEqual(x19.features_from_rows(base_rows(), "M", MIG_MS + 500, VMAP)["status"], "no_mig_print")
        dup = base_rows() + [base_rows()[0]]
        self.assertEqual(x19.features_from_rows(dup, "M", MIG_MS, VMAP), x19.features_from_rows(base_rows(), "M", MIG_MS, VMAP))

    def test_cells(self):
        ok = x19.features_from_rows(base_rows(), "M", MIG_MS, VMAP)
        self.assertTrue(x19.confirm_a(ok) and x19.confirm_b(ok))
        big = base_rows()[:2] + [prow("M", 1002, "sell", 10_000_000_000, MIG_BT + 1, "a", q=Q0 + 3_000_000_000)]
        fb = x19.features_from_rows(big, "M", MIG_MS, VMAP)
        self.assertFalse(x19.confirm_b(fb))
        self.assertFalse(x19.confirm_a(None))
        self.assertFalse(x19.confirm_b({"status": "no_mig_print"}))
        one = x19.features_from_rows(base_rows()[:1], "M", MIG_MS, VMAP)  # a single print: price change 0, not > 0
        self.assertFalse(x19.confirm_a(one))


def write_zst(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = ("\n".join(json.dumps(r) for r in rows) + "\n").encode()
    subprocess.run(["zstd", "-q", "-o", str(path), "-"], input=raw, check=True)


class TestTape(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fn(self, h):  # P3 naming
        return {"trade": self.root / "w1" / "trades" / f"trades-{h}.deduped.jsonl.zst"}

    def test_build_features_p3_naming(self):
        h0 = x19._hour_key(MIG_MS)
        hours = e15.block_hours("P2")[:12]
        self.assertEqual(h0, x19._hour_key(MIG_MS + x19.FETCH_MS))
        self.assertIn(h0, hours)
        write_zst(self.fn(h0)["trade"], base_rows() + [prow("OTHER", 1000, "buy", 1, MIG_BT)])
        for h in hours:
            if h != h0:
                write_zst(self.fn(h)["trade"], [])
        x19.check_hours_resolve("t", hours, self.fn)
        feats, _c = x19.build_features({"M": MIG_MS}, hours, self.fn, VMAP, workers=1)
        self.assertEqual(feats["M"]["status"], "ok")
        self.assertEqual(feats["M"]["n_buyers"], 2)
        feats2, _ = x19.build_features({"M": MIG_MS}, hours, self.fn, VMAP, workers=2)
        self.assertEqual(feats, feats2)

    def test_truncated_window_when_spill_hour_missing(self):
        h0 = x19._hour_key(MIG_MS)
        write_zst(self.fn(h0)["trade"], base_rows())
        late = MIG_MS - (MIG_MS % 3_600_000) + 3_600_000 - 2000  # 2 s before the end of the hour: the 10 s fetch spills into the next hour
        feats, _ = x19.build_features({"M": late}, [h0], self.fn, VMAP)
        self.assertEqual(feats["M"]["status"], "truncated_window")

    def test_missing_hour_refuses(self):
        with self.assertRaises(x19.Refused):
            x19.check_hours_resolve("t", ["2026-08-14T12"], self.fn)

    def test_zstd_failure_refuses(self):
        h0 = x19._hour_key(MIG_MS)
        p = self.fn(h0)["trade"]
        p.parent.mkdir(parents=True)
        p.write_bytes(b"not zstd data at all")
        with self.assertRaises(x19.Refused):
            list(x19.read_trade_file(p, h0, {}))


def cell(k, net0, censored=False, lag=2):
    return e15._slim_cell({"k": k, "exit_lag": lag, "size": e17.SIZE_1X, "censored": censored, "filled": True, "status": 1, "net0": net0, "sides": 2, "p_press": 0.15})


def urow(i, date_off, n6, n8, k8_censored=False):
    ms = P2_START + date_off * 86_400_000 + 3_600_000 * 13
    return {"mint": f"m{i}", "date": e15.utc_date(ms), "mig_ms": ms, "source": "P2", "block": "P2", "features": [0.0] * NF,
            "cells": {(6, 2): cell(6, n6), (8, 2): cell(8, n8, k8_censored)}, "c1": 0, "c1_missing": True}


OKF = {"status": "ok", "price_change": 0.1, "net_flow_lamports": 5, "max_sell_share": 0.0, "n_prints": 3, "n_buyers": 2, "pool_has_v": True}
BADF = {**OKF, "price_change": -0.1, "max_sell_share": 0.2}


class TestScreen(unittest.TestCase):
    def setUp(self):
        self.uni = [urow(i, i % 5, 1_000_000, 2_000_000) for i in range(8)] + [urow(9, 0, 1, 1)]
        self.scores = [0.9] * 8 + [0.1]
        self.feats = {f"m{i}": (OKF if i % 2 == 0 else BADF) for i in range(8)}

    def test_selection_and_shares(self):
        rows = x19.selected_rows(self.uni, self.scores)
        self.assertEqual(rows, list(range(8)))
        rep = x19.share_report(self.uni, rows, self.feats)
        self.assertEqual((rep["pass_A"], rep["pass_B"], rep["n_without_k8_cell"]), (4, 4, 0))
        x19.check_screen_ready({**rep, "coverage": 1.0})

    def test_share_refusals(self):
        rows = x19.selected_rows(self.uni, self.scores)
        for feats in ({f"m{i}": OKF for i in range(8)}, {f"m{i}": BADF for i in range(8)}):
            with self.assertRaises(x19.Refused):
                x19.check_shares(x19.share_report(self.uni, rows, feats))

    def test_k8_cell_refusal(self):
        self.uni[3] = urow(3, 3, 1_000_000, 2_000_000, k8_censored=True)
        rows = x19.selected_rows(self.uni, self.scores)
        rep = x19.share_report(self.uni, rows, self.feats)
        self.assertEqual(rep["n_without_k8_cell"], 1)
        with self.assertRaises(x19.Refused):
            x19.check_screen_ready({**rep, "coverage": 1.0})
        del self.uni[3]["cells"][(8, 2)]
        self.assertEqual(x19.k8_missing(self.uni, [3]), 1)

    def test_run_screen_enters_only_confirmed_at_k8(self):
        res = x19.run_screen(self.uni, self.scores, self.feats)
        self.assertEqual(res["n_scope_rows"], 8)
        self.assertEqual(res["cells"]["A"]["n_entered"], 4)
        self.assertEqual(res["cells"]["A"]["n_trades_non_p1"], 4)
        self.assertEqual(res["cells"]["B"]["n_trades_non_p1"], 4)
        self.assertEqual(res["report_only"]["frozen_all_at_k8"]["flat"]["n"], 8)
        self.assertEqual(set(res["holm"]), {"A", "B"})
        self.assertAlmostEqual(res["holm"]["A"]["threshold"] + res["holm"]["B"]["threshold"], 0.025 + 0.05)
        self.assertTrue(res["outcome"].startswith("SCREEN NONE"))  # n < 100: B1 fails

    def test_missing_features_never_enter(self):
        res = x19.run_screen(self.uni, self.scores, {})
        self.assertEqual(res["cells"]["A"]["n_entered"], 0)

    def test_second_run_and_missing_features_refused(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "t.jsonl"
            log.write_text(json.dumps({"tool": x19.TOOL, "config": {"key": "exp019_a"}}) + "\n")
            with self.assertRaises(x19.Refused):
                x19.check_no_prior_tries(log)
            self.assertIsNone(x19.check_no_prior_tries(Path(d) / "absent.jsonl"))
            with self.assertRaises(x19.Refused):
                x19.load_features(Path(d) / "features.jsonl", Path(d))


class TestPins(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(x19.K8_CELL, (8, 2))
        self.assertIn((8, 2), e15.CELL_KEYS)
        self.assertIn((8, e15.SIZE_SOL, 2), e15.COMBOS)
        self.assertEqual(e17.CACHE_MANIFEST_SHA256[:8], "72b9bd1a")
        self.assertEqual(x19.FEATURE_SLOTS, 2)


if __name__ == "__main__":
    unittest.main()
