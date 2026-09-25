"""Graduated PumpSwap swing: fees, causal clocks, backfill receive time."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.graduated_swing import (
    GRADUATION_MCAP_SOL,
    SIZE_LAMPORTS,
    SIZE_LAMPORTS_LARGE,
    WINDOW_START_MS,
    AttentionEvent,
    decision_points,
    features_at,
    fee_only_round_trip,
    flat_mix,
    immediate_round_trip,
    load_graduated_books,
    planning_fee_table,
    synthetic_recv_ms,
)
from tools.laya_v0 import BookTrade, MintBook, book_stats
from tools.paper_curve_math import (
    PORTAL_FEE_PPM,
    PRIORITY_FEE_LAMPORTS,
    pumpswap_sol_fee_ppm,
    pumpswap_sol_fee_split,
)
from tools.paper_price_path import CreateSignal
def _flow(t_ms: int, *, venue: str, side: str, quote: int, base: int, sol: int, token: int, trader: str, slot: int):
    from tools.laya_v0 import FlowPrint

    price = quote / (base * 1000)
    return FlowPrint(
        t_recv_ms=t_ms,
        slot=slot,
        event_index=0,
        venue=venue,
        side=side,
        sol_lamports=sol,
        token_raw=token,
        trader=trader,
        quote_reserve=quote,
        base_reserve=base,
        price_sol=price,
        market_cap_sol=price * 1_000_000_000,
    )


class FeeTests(unittest.TestCase):
    def test_split_matches_total_and_graduation_is_still_125bps(self) -> None:
        self.assertLess(GRADUATION_MCAP_SOL, 420)
        self.assertGreater(GRADUATION_MCAP_SOL, 400)
        self.assertEqual(pumpswap_sol_fee_ppm(GRADUATION_MCAP_SOL), 12_500)
        creator, protocol, lp = pumpswap_sol_fee_split(GRADUATION_MCAP_SOL)
        self.assertEqual((creator, protocol, lp), (3_000, 9_300, 200))
        self.assertEqual(creator + protocol + lp, 12_500)
        for mcap in (0, 420, 1470, 98_240, 200_000):
            c, p, l = pumpswap_sol_fee_split(mcap)
            self.assertEqual(c + p + l, pumpswap_sol_fee_ppm(mcap))

    def test_portal_round_trip_is_not_cheaper_than_the_curve_at_graduation(self) -> None:
        table = planning_fee_table()
        by_route = {}
        for row in table["round_trips"]:
            by_route[(row["route"], row["fee_only"]["size_sol"])] = row
        portal = by_route[("portal_local", 0.05)]["fee_only"]["loss_frac_fees_and_impact"]
        direct = by_route[("direct", 0.05)]["fee_only"]["loss_frac_fees_and_impact"]
        # 1.25% twice plus 0.5% twice is about 3.46%. Direct drops the portal 1.0pp.
        self.assertAlmostEqual(portal, 0.03457, places=3)
        self.assertAlmostEqual(direct, 0.02484, places=3)
        self.assertGreater(portal - direct, 0.009)
        self.assertLess(portal - direct, 0.011)
        # Priority dominates a 0.05 SOL ticket and is a small slice of 0.5 SOL.
        small = by_route[("portal_local", 0.05)]["graduation_pool"]["loss_frac_plus_priority"]
        large = by_route[("portal_local", 0.5)]["graduation_pool"]["loss_frac_plus_priority"]
        self.assertGreater(small, 0.07)
        self.assertLess(large, 0.05)

    def test_direct_route_buys_more_tokens_than_portal(self) -> None:
        from tools.paper_curve_math import quote_buy

        kw = dict(venue="pumpswap", size_lamports=SIZE_LAMPORTS, quote_lamports=85_000_000_000, base_raw=200_000_000_000_000, market_cap=410.0)
        portal = quote_buy(**kw, portal_fee_ppm=PORTAL_FEE_PPM)
        direct = quote_buy(**kw, portal_fee_ppm=0)
        assert portal is not None and direct is not None
        self.assertGreater(direct.tokens_raw, portal.tokens_raw)
        # Default stays on Portal Local.
        self.assertEqual(quote_buy(**kw).tokens_raw, portal.tokens_raw)

    def test_entry_impact_scales_with_size_on_the_graduation_pool(self) -> None:
        table = planning_fee_table()
        small = next(r for r in table["round_trips"] if r["route"] == "direct" and r["fee_only"]["size_sol"] == 0.05)
        large = next(r for r in table["round_trips"] if r["route"] == "direct" and r["fee_only"]["size_sol"] == 0.5)
        # Unwinding into the post-buy pool is a constant-product identity: fees, not impact.
        self.assertAlmostEqual(
            small["graduation_pool"]["loss_frac_fees_and_impact"],
            small["fee_only"]["loss_frac_fees_and_impact"],
            places=5,
        )
        impact_small = small["graduation_pool"]["entry_impact_frac"]
        impact_large = large["graduation_pool"]["entry_impact_frac"]
        self.assertLess(impact_small, 0.001)
        self.assertGreater(impact_large, 0.005)
        self.assertGreater(impact_large, impact_small * 5)


class ClockTests(unittest.TestCase):
    def _book(self) -> tuple[MintBook, int]:
        from tools.laya_v0 import FlowPrint

        mig = WINDOW_START_MS + 60_000
        create = CreateSignal("MintA", mig - 120_000, "Dev", None, None, None, None, None, None)
        quote, base = 80_000_000_000, 200_000_000_000_000
        prints = [
            _flow(mig - 10_000, venue="pump_bonding", side="buy", quote=40_000_000_000, base=base, sol=1_000_000, token=10, trader="Sniper", slot=10),
            _flow(mig, venue="pumpswap", side="buy", quote=quote, base=base, sol=2_000_000, token=10, trader="Dev", slot=20),
            _flow(mig + 30_000, venue="pumpswap", side="sell", quote=quote - 1_000_000, base=base, sol=5_000_000_000, token=10, trader="Dev", slot=21),
            _flow(mig + 120_000, venue="pumpswap", side="buy", quote=quote + 5_000_000_000, base=base, sol=3_000_000, token=10, trader="Human", slot=22),
            # After the +1 minute decision. Must not enter features at mig+1m.
            _flow(mig + 90_000, venue="pumpswap", side="buy", quote=quote + 50_000_000_000, base=base, sol=9_000_000_000, token=10, trader="Future", slot=23),
        ]
        prints.sort(key=lambda p: p.t_recv_ms)
        book = MintBook(create=create, flow=prints)
        return book, mig

    def test_decisions_are_after_migration_and_attention_before_migration_is_dropped(self) -> None:
        book, mig = self._book()
        events = [
            AttentionEvent("MintA", "dex_boost", mig - 1, None, True),
            AttentionEvent("MintA", "dex_boost", mig + 10_000, 3, True),
            AttentionEvent("MintA", "pump_live", mig + 20_000, 1, False),
        ]
        points = decision_points(book, mig, events, tape_end_ms=mig + 4 * 60 * 60 * 1000)
        names = [name for _t, name, _attn in points]
        self.assertIn("mig_1", names)
        self.assertIn("mig_60", names)
        self.assertIn("attn:dex_boost", names)
        self.assertNotIn("attn:pump_live", names)
        self.assertTrue(all(t >= mig for t, _n, _a in points))

    def test_features_ignore_prints_after_the_decision(self) -> None:
        book, mig = self._book()
        feats = features_at(
            book,
            decision_t_ms=mig + 60_000,
            migration_t_ms=mig,
            migration_price=book.flow[1].price_sol,
            attention=[AttentionEvent("MintA", "dex_boost", mig + 10_000, 4, True)],
            is_attention=False,
        )
        self.assertEqual(feats["f_pm_unique_buyers"], 0.0)
        self.assertEqual(feats["f_pm_n_sell"], 1.0)
        self.assertEqual(feats["f_creator_sell_n"], 1.0)
        self.assertGreater(feats["f_pm_creator_sell_sol"], 0)
        self.assertEqual(feats["f_attn_n"], 1.0)
        self.assertEqual(feats["f_attn_dex"], 1.0)
        self.assertGreater(feats["f_sniper_buy_sol_share"], 0.0)
        self.assertLessEqual(feats["f_sniper_buy_sol_share"], 1.0)
        # Future buyer is not in the post-migration buyer count.
        later = features_at(
            book,
            decision_t_ms=mig + 120_000,
            migration_t_ms=mig,
            migration_price=book.flow[1].price_sol,
            attention=[],
            is_attention=False,
        )
        self.assertEqual(later["f_pm_unique_buyers"], 2.0)

    def test_flat_fail_keeps_a_miss_at_priority_only(self) -> None:
        self.assertEqual(flat_mix(-PRIORITY_FEE_LAMPORTS, "missed_slippage"), -PRIORITY_FEE_LAMPORTS)
        mixed = flat_mix(-2_000_000, "filled")
        self.assertGreater(mixed, -2_000_000)
        self.assertLess(mixed, 0)

    def test_one_day_book_does_not_promote(self) -> None:
        day = WINDOW_START_MS
        trades = [BookTrade(f"m{i}", day, 1_000_000) for i in range(120)]
        summary = book_stats(trades)
        self.assertGreater(summary["mean_sol"], 0)
        self.assertFalse(summary["promote"])
        self.assertLess(summary["n_days"], 5)


class BackfillRecvTests(unittest.TestCase):
    def test_synthetic_receive_is_block_time_plus_lag_not_block_time(self) -> None:
        block = 1_790_320_000
        self.assertEqual(synthetic_recv_ms(block, 1_400), block * 1000 + 1_400)
        self.assertNotEqual(synthetic_recv_ms(block, 1_400), block * 1000)
        self.assertNotEqual(synthetic_recv_ms(block, 1_400), block)

    def test_loader_prefers_live_receive_time_and_stamps_backfill(self) -> None:
        mig = WINDOW_START_MS + 30_000
        quote, base = 80_000_000_000, 200_000_000_000_000
        live = {
            "type": "trade",
            "venue": "pumpswap",
            "mint": "MintA",
            "quote_is_wsol": True,
            "side": "buy",
            "t_recv_ms": mig,
            "event_ts": mig // 1000 - 1,
            "signature": "sig-live",
            "event_index": 0,
            "slot": 5,
            "sol_lamports": 1_000_000,
            "token_raw": 10,
            "quote_reserve": quote,
            "base_reserve": base,
            "trader": "A",
            "price_sol": quote / (base * 1000),
            "market_cap_sol": 410,
        }
        # Same signature, null receive time. Must not replace the live clock.
        backfill_dupe = dict(live)
        backfill_dupe["source"] = "backfill"
        backfill_dupe["t_recv_ms"] = None
        backfill_dupe["block_time"] = (mig // 1000) - 5
        backfill_dupe["event_ts"] = backfill_dupe["block_time"]
        # Unique backfill print before the live one. Receive time is synthetic.
        earlier = dict(live)
        earlier.update(
            {
                "source": "backfill",
                "t_recv_ms": None,
                "block_time": (mig // 1000) - 20,
                "event_ts": (mig // 1000) - 20,
                "signature": "sig-bf",
                "side": "sell",
                "trader": "B",
                "venue": "pump_bonding",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live_path = root / "live.jsonl"
            bf_path = root / "bf.jsonl"
            old = dict(live)
            old.update({"mint": "OldPool", "signature": "sig-old", "trader": "Z"})
            live_path.write_text(json.dumps(live) + "\n" + json.dumps(old) + "\n", encoding="utf-8")
            bf_path.write_text(json.dumps(backfill_dupe) + "\n" + json.dumps(earlier) + "\n", encoding="utf-8")
            books, migration, stats, reservoir = load_graduated_books(
                [live_path],
                [bf_path],
                {},
                window_start_ms=WINDOW_START_MS,
            )
        self.assertEqual(migration["MintA"], mig)
        self.assertNotIn("OldPool", books)
        self.assertGreater(stats.lags_seen, 0)
        self.assertGreater(len(reservoir.data), 0)
        times = [pr.t_recv_ms for pr in books["MintA"].flow]
        self.assertIn(mig, times)
        self.assertTrue(any(t != mig and t > earlier["block_time"] * 1000 for t in times))
        self.assertNotIn(earlier["block_time"] * 1000, times)
        self.assertEqual(len(books["MintA"].flow), 2)


class RoundTripSizeTests(unittest.TestCase):
    def test_both_sizes_quote(self) -> None:
        for size in (SIZE_LAMPORTS, SIZE_LAMPORTS_LARGE):
            row = fee_only_round_trip(size_lamports=size, mcap_sol=GRADUATION_MCAP_SOL, portal_fee_ppm=PORTAL_FEE_PPM)
            assert row is not None
            self.assertGreater(row["loss_frac_fees_and_impact"], 0.03)
            impacted = immediate_round_trip(
                size_lamports=size,
                quote_lamports=85_000_000_000,
                base_raw=200_000_000_000_000,
                portal_fee_ppm=0,
            )
            assert impacted is not None
            self.assertGreater(impacted["loss_frac_fees_and_impact"], 0.02)


if __name__ == "__main__":
    unittest.main()
