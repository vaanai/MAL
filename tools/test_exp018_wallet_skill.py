from __future__ import annotations

import json
import random
import unittest
from collections import deque
from pathlib import Path

import tools.exp018_wallet_skill as w
from tools.wallet_leaderboard import Lot, match_sell


def row(slot, mint, trader, side, sol, tok, venue="pump_bonding", tx=0, ev=0):
    """The real trade-row shape (bonding v1 / PumpSwap v2 keys the loaders read)."""
    r = {"type": "trade", "venue": venue, "mint": mint, "trader": trader, "side": side, "sol_lamports": sol, "token_raw": tok, "slot": slot,
         "tx_index": tx, "event_index": ev, "signature": f"sig{slot}{tx}{ev}", "block_time": 1_790_000_000 + slot // 2, "t_recv_ms": 1_790_000_000_000 + slot * 400,
         "pool": None if venue == "pump_bonding" else "P", "quote_reserve": 1, "base_reserve": 1, "price_sol": 1e-7, "market_cap_sol": 30.0}
    if venue == "pumpswap":
        r["quote_is_wsol"] = True
    return r


def run(rows_by_hour, cutoffs, min_trips=1):
    hours = sorted(rows_by_hour)
    return w.run_series(hours, lambda h: rows_by_hour.get(h, ()), cutoffs, min_trips=min_trips)


def trip(slot0, mint, who, pnl_sign=1):
    """buy 1000 tokens for 1e9 lamports, sell all for 1.5e9 (win) or 0.5e9 (loss): two rows, closed at slot0+1."""
    return [row(slot0, mint, who, "buy", 10**9, 1000), row(slot0 + 1, mint, who, "sell", 15 * 10**8 if pnl_sign > 0 else 5 * 10**8, 1000)]


class TestParse(unittest.TestCase):
    def test_admission(self):
        self.assertIsNotNone(w.parse_row(row(5, "M", "A", "buy", 10, 10)))
        self.assertIsNone(w.parse_row({**row(5, "M", "A", "buy", 10, 10), "venue": "pumpswap"}))  # no quote_is_wsol
        self.assertIsNone(w.parse_row(row(5, "M", "A", "buy", 0, 10)))
        self.assertIsNone(w.parse_row({**row(5, "M", "A", "buy", 10, 10), "type": "create"}))
        self.assertIsNone(w.parse_row({**row(5, "M", "A", "buy", 10, 10), "mint_source": "unresolved"}))


class TestEngine(unittest.TestCase):
    def test_pnl_equals_fifo_match_sell(self):
        rnd = random.Random(3)
        for _ in range(200):
            lots, st = deque(), w.SkillState(1)
            fifo_total, n_sells = 0, 0
            rows = []
            for i in range(rnd.randrange(2, 8)):
                rows.append(("buy", rnd.randrange(10**8, 10**10), rnd.randrange(10**3, 10**6)))
            held = sum(r[2] for r in rows)
            rows.append(("sell", rnd.randrange(10**8, 10**10), held))  # sells everything (one close)
            for i, (side, sol, tok) in enumerate(rows):
                st.feed(100 + i, "M", "A", side, sol, tok, True, 0)
                if side == "buy":
                    lots.append(Lot(token_raw=tok, cost_lamports=sol, t_ms=i, slot=100 + i))
                else:
                    pnl, *_ = match_sell(lots, tok, sol, i, "s")
                    fifo_total += pnl
                    n_sells += 1
            self.assertEqual(st.trips[0], 1)
            self.assertEqual(st.pnl[0], fifo_total - w.TX_FEE_LAMPORTS * len(rows))

    def test_partial_sells_do_not_close(self):
        st = w.SkillState(1)
        st.feed(1, "M", "A", "buy", 10**9, 1000, True, 0)
        st.feed(2, "M", "A", "sell", 10**9, 500, True, 0)
        self.assertEqual(st.trips[0], 0)
        st.feed(3, "M", "A", "sell", 10**9, 500, True, 0)
        self.assertEqual(st.trips[0], 1)
        self.assertEqual(st.pnl[0], 10**9 + 10**9 - 10**9 - 3 * w.TX_FEE_LAMPORTS)

    def test_sell_without_inventory_is_unmatched(self):
        st = w.SkillState(1)
        st.feed(1, "M", "A", "sell", 10**9, 500, True, 0)
        self.assertEqual(st.trips[0], 0)
        self.assertEqual(st.stats["unmatched_sells"], 1)


class TestCausality(unittest.TestCase):
    def setUp(self):
        # A: a winner over 1 trip on X, then holds M (the migrating mint). Cutoff of M = slot 100.
        self.base = trip(10, "X", "A") + [row(50, "M", "A", "buy", 2 * 10**9, 5000)]
        self.cut = {"M": 100}

    def feats(self, extra):
        rows = {"h0": self.base + extra}
        out, _ = run(rows, self.cut)
        return out["M"]

    def test_skilled_holder_counts_prior_trip(self):
        f = self.feats([row(100, "Z", "Q", "buy", 1, 1)])  # a row at the cutoff slot triggers the snapshot
        self.assertEqual(f["skilled_holder_lamports"], 2 * 10**9)
        self.assertEqual(f["skilled_buyer_count"], 1)

    def test_trip_closing_at_or_after_cutoff_does_not_count(self):
        # B holds M and has an open position on Y; B's trip on Y closes at slot 100 (== cutoff) and slot 101: neither may count.
        extra = [row(60, "M", "B", "buy", 10**9, 3000), row(61, "Y", "B", "buy", 10**9, 1000),
                 row(100, "Y", "B", "sell", 3 * 10**9, 1000), row(101, "Y", "B", "buy", 1, 1)]
        f = self.feats(extra)
        self.assertEqual(f["skilled_holder_lamports"], 2 * 10**9)  # only A
        self.assertEqual(f["skilled_buyer_count"], 1)
        # and the same trip closing one slot earlier DOES count
        extra2 = [row(60, "M", "B", "buy", 10**9, 3000), row(61, "Y", "B", "buy", 10**9, 1000), row(99, "Y", "B", "sell", 3 * 10**9, 1000), row(100, "Z", "Q", "buy", 1, 1)]
        f2 = self.feats(extra2)
        self.assertEqual(f2["skilled_holder_lamports"], 3 * 10**9)
        self.assertEqual(f2["skilled_buyer_count"], 2)

    def test_future_rows_do_not_change_features(self):
        rnd = random.Random(9)
        a = self.feats([row(100, "Z", "Q", "buy", 1, 1)])
        junk = [row(rnd.randrange(100, 400), rnd.choice("MXYZ"), rnd.choice("ABCQ"), rnd.choice(["buy", "sell"]), rnd.randrange(1, 10**10), rnd.randrange(1, 10**5)) for _ in range(300)]
        b = self.feats(junk + [row(100, "Z", "Q", "buy", 1, 1)])
        self.assertEqual({k: v for k, v in a.items() if k not in ("n_wallets", "n_skilled_wallets")}, {k: v for k, v in b.items() if k not in ("n_wallets", "n_skilled_wallets")})

    def test_unsorted_hour_is_sorted_by_slot(self):
        rows = {"h0": list(reversed(self.base)) + [row(100, "Z", "Q", "buy", 1, 1)]}
        out, _ = run(rows, self.cut)
        self.assertEqual(out["M"]["skilled_holder_lamports"], 2 * 10**9)

    def test_min_trips_and_negative_skill(self):
        rows = {"h0": trip(10, "X", "A") + trip(20, "X2", "L", -1) + [row(50, "M", "A", "buy", 10**9, 10), row(51, "M", "L", "buy", 10**9, 10), row(100, "Z", "Q", "buy", 1, 1)]}
        f, _ = run(rows, {"M": 100}, min_trips=1)
        self.assertEqual(f["M"]["skilled_holder_lamports"], 10**9)  # A only: L's trip lost
        f2, _ = run(rows, {"M": 100}, min_trips=2)
        self.assertEqual(f2["M"]["skilled_holder_lamports"], 0)

    def test_window_share(self):
        rows = {"h0": trip(10, "X", "A") + [row(40, "M", "A", "buy", 10**9, 10),  # outside the 150-slot window? cutoff 300 -> window starts 150
                                              row(200, "M", "A", "buy", 10**9, 10), row(210, "M", "B", "buy", 3 * 10**9, 10), row(300, "Z", "Q", "buy", 1, 1)]}
        f, _ = run(rows, {"M": 300}, min_trips=1)
        self.assertAlmostEqual(f["M"]["skilled_share"], 0.25)

    def test_idle_eviction_spares_pending_mints(self):
        st = w.SkillState(1)
        st.track("M", 10**9)
        st.feed(1, "M", "A", "buy", 10, 10, True, 0)
        st.feed(1, "N", "A", "buy", 10, 10, True, 0)
        st.evict_idle(w.IDLE_EVICT_HOURS + 5)
        self.assertIn(st.mid["M"], st.pos)
        self.assertNotIn(st.mid["N"], st.pos)


class TestWarmupAndCells(unittest.TestCase):
    def test_warmup(self):
        t0 = w.series_start_ms("S_P2")
        self.assertFalse(w.scored_flag({"source": "P2", "mig_ms": t0 + 71 * 3_600_000}))
        self.assertTrue(w.scored_flag({"source": "P2", "mig_ms": t0 + 72 * 3_600_000}))
        self.assertEqual(w.series_of("P4"), "S_P34")

    def test_nested_threshold_uses_other_dates_only(self):
        def u(date, ms):
            return {"date": date, "mig_ms": ms}
        d = 86_400_000
        base = w.e15.date_start_ms("2026-08-20")
        uni = [u("2026-08-20", base + 6 * 3_600_000), u("2026-08-21", base + d + 6 * 3_600_000), u("2026-08-22", base + 2 * d + 6 * 3_600_000)]
        share = [0.9, 0.1, 0.3]
        taus = w.nested_thresholds(uni, [True, True, True], share, [0, 1, 2])
        self.assertAlmostEqual(taus["2026-08-20"], 0.2)  # median of 0.1, 0.3
        self.assertAlmostEqual(taus["2026-08-21"], 0.6)  # median of 0.9, 0.3

    def test_masks_do_not_read_nets(self):
        uni = [{"mint": "A", "source": "P2", "block": "P2", "date": "2026-08-20", "mig_ms": w.series_start_ms("S_P2") + 100 * 3_600_000, "cells": {}},
               {"mint": "B", "source": "P2", "block": "P2", "date": "2026-08-21", "mig_ms": w.series_start_ms("S_P2") + 125 * 3_600_000, "cells": {}},
               {"mint": "C", "source": "P2", "block": "P2", "date": "2026-08-15", "mig_ms": w.series_start_ms("S_P2") + 3_600_000, "cells": {}}]
        feats = {m: {"skilled_holder_lamports": h, "skilled_share": s} for m, h, s in (("A", 5, 0.5), ("B", 0, 0.1), ("C", 5, 0.9))}
        m = w.build_masks(uni, [1.0, 1.0, 1.0], feats)
        self.assertEqual(m["scope"], [0, 1])  # C is inside the warm-up
        self.assertEqual(m["W1"], [True, False, False])
        self.assertEqual(m["frozen"], [True, True, False])

    def test_holm_k2(self):
        h = w.holm({"W1": 0.02, "W2": 0.06})
        self.assertTrue(h["W1"]["reject"] and not h["W2"]["reject"])  # 0.02 <= 0.025; 0.06 > 0.05
        h2 = w.holm({"W1": 0.02, "W2": 0.04})
        self.assertTrue(h2["W1"]["reject"] and h2["W2"]["reject"])
        h3 = w.holm({"W1": 0.03, "W2": 0.04})
        self.assertFalse(h3["W1"]["reject"] or h3["W2"]["reject"])  # step-down stops at 0.03 > 0.025


class TestRefusals(unittest.TestCase):
    def test_features_sha_mismatch(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "f.jsonl").write_text("x\n")
            with self.assertRaises(w.Refused):
                w.check_features_file(d / "f.jsonl", d)  # no precount.json
            (d / w.OUT_PRECOUNT).write_text(json.dumps({"features_sha256": "0" * 64}))
            with self.assertRaises(w.Refused):
                w.check_features_file(d / "f.jsonl", d)
            (d / w.OUT_PRECOUNT).write_text(json.dumps({"features_sha256": w._file_sha256(d / "f.jsonl")}))
            w.check_features_file(d / "f.jsonl", d)

    def test_hour_in_two_views_refused(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            for v in ("a", "b"):
                (Path(d) / v / "trades").mkdir(parents=True)
                (Path(d) / v / "trades" / "trades-2026-08-20T00.jsonl").write_text("")
            with self.assertRaises(w.Refused):
                w.hour_files([str(Path(d) / "a"), str(Path(d) / "b")], ["2026-08-20T00"])

    def test_cache_pin_refuses_wrong_manifest(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "cache").mkdir()
            with self.assertRaises(w.Refused):
                w.check_cache(d)

    def test_blind_cache_drops_nets(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.jsonl"
            p.write_text(json.dumps({"mint": "M", "cells": [{"k": 6, "lag": 2, "net0": 123, "status": "FILLED", "sides": 1, "p_press": 0.5, "censored": False}]}) + "\n")
            r = w.read_cache_rows(p, blind=True)[0]
            self.assertEqual(set(r["cells"][0]), {"k", "lag", "censored"})


if __name__ == "__main__":
    unittest.main()
