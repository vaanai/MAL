"""Fixture tests for tape L2 wallet leaderboard. No network."""

from __future__ import annotations

import json
import tempfile
import unittest
from collections import deque
from pathlib import Path

from tools.wallet_leaderboard import (
    COPY_ROUND_TRIP_BPS,
    NOISY_V0,
    STRICT,
    Lot,
    Thresholds,
    VETO_BOT,
    VETO_CREATOR,
    VETO_FARM,
    VETO_SNIPER,
    VETO_TRANSFER_IN,
    VETO_WASH,
    follow_signals,
    follower_wave,
    index_mint_buy_full,
    load_creates,
    match_sell,
    parse_trade,
    run,
    write_outputs,
)

W = "Leader1111111111111111111111111111111111111111"
C = "Creator111111111111111111111111111111111111111"
O = "Other11111111111111111111111111111111111111111"


def trade(
    *,
    mint: str,
    trader: str,
    side: str,
    t_ms: int,
    slot: int,
    sol: int = 100_000_000,
    token: int = 1_000_000,
    signature: str | None = None,
    venue: str = "pump_bonding",
    event_index: int = 0,
) -> dict:
    return {
        "v": 1,
        "type": "trade",
        "venue": venue,
        "mint": mint,
        "mint_source": "event",
        "trader": trader,
        "side": side,
        "sol_lamports": sol,
        "token_raw": token,
        "slot": slot,
        "signature": signature or f"sig-{mint}-{trader}-{t_ms}-{side}",
        "event_index": event_index,
        "t_recv_ms": t_ms,
        "quote_is_wsol": True,
        "price_sol": 0.0001,
        "market_cap_sol": 80.0,
    }


def dump_jsonl(rows: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def seed_mint(mint: str, t0: int, slot0: int) -> list[dict]:
    """Creator prints first so later organic buyers are not snipers."""
    return [trade(mint=mint, trader=C, side="buy", t_ms=t0, slot=slot0, sol=1_000_000, token=10_000)]


def closed_rt(
    mint: str,
    trader: str,
    t_buy: int,
    slot_buy: int,
    hold_ms: int,
    buy_sol: int,
    sell_sol: int,
    token: int = 1_000_000,
) -> list[dict]:
    return [
        trade(mint=mint, trader=trader, side="buy", t_ms=t_buy, slot=slot_buy, sol=buy_sol, token=token),
        trade(
            mint=mint,
            trader=trader,
            side="sell",
            t_ms=t_buy + hold_ms,
            slot=slot_buy + max(1, hold_ms // 400),
            sol=sell_sol,
            token=token,
        ),
    ]


def profitable_organic_book(n_mints: int, wins: int, hold_ms: int = 180_000) -> list[dict]:
    rows: list[dict] = []
    for i in range(n_mints):
        mint = f"Mint{i:04d}pump"
        t0 = 1_000_000 + i * 90_000
        slot0 = 1000 + i * 200
        rows.extend(seed_mint(mint, t0, slot0))
        win = i < wins
        buy_sol = 200_000_000
        sell_sol = 320_000_000 if win else 140_000_000
        rows.extend(closed_rt(mint, W, t0 + 8_000, slot0 + 12, hold_ms, buy_sol, sell_sol))
    return rows


class ParseAndFifoTests(unittest.TestCase):
    def test_parse_skips_unresolved_and_non_wsol(self) -> None:
        base = trade(mint="M", trader=W, side="buy", t_ms=1, slot=1)
        self.assertIsNotNone(parse_trade(base))
        bad = dict(base, mint_source="unresolved", mint=None)
        self.assertIsNone(parse_trade(bad))
        nowsol = dict(base, quote_is_wsol=False)
        self.assertIsNone(parse_trade(nowsol))
        zero = dict(base, sol_lamports=0)
        self.assertIsNone(parse_trade(zero))

    def test_fifo_full_round_trip_pnl(self) -> None:
        lots = deque([Lot(token_raw=1_000_000, cost_lamports=1_000_000_000, t_ms=1000, slot=1, signature="b")])
        pnl, cost, proceeds, holds, unmatched, same_tx = match_sell(
            lots, 1_000_000, 1_100_000_000, 121_000, "s"
        )
        self.assertEqual(unmatched, 0)
        self.assertEqual(same_tx, 0)
        self.assertEqual(cost, 1_000_000_000)
        self.assertEqual(proceeds, 1_100_000_000)
        self.assertEqual(pnl, 100_000_000)
        self.assertEqual(holds, [120_000])
        self.assertEqual(len(lots), 0)

    def test_fifo_partial_then_close(self) -> None:
        lots = deque([Lot(token_raw=1_000, cost_lamports=1_000_000, t_ms=0, slot=1, signature="b")])
        pnl1, cost1, proc1, _, unmatched1, _ = match_sell(lots, 400, 500_000, 10_000, "s1")
        self.assertEqual(unmatched1, 0)
        self.assertEqual(cost1, 400_000)
        self.assertEqual(proc1, 500_000)
        self.assertEqual(pnl1, 100_000)
        self.assertEqual(lots[0].token_raw, 600)
        pnl2, cost2, proc2, _, _, _ = match_sell(lots, 600, 900_000, 20_000, "s2")
        self.assertEqual(cost2, 600_000)
        self.assertEqual(proc2, 900_000)
        self.assertEqual(pnl2, 300_000)
        self.assertEqual(len(lots), 0)

    def test_unmatched_sell_without_inventory(self) -> None:
        lots: deque[Lot] = deque()
        pnl, cost, proceeds, holds, unmatched, _ = match_sell(lots, 100, 50_000, 1, "s")
        self.assertEqual(pnl, 0)
        self.assertEqual(cost, 0)
        self.assertEqual(proceeds, 0)
        self.assertEqual(holds, [])
        self.assertEqual(unmatched, 50_000)

    def test_same_tx_round_trip_flag(self) -> None:
        lots = deque([Lot(token_raw=100, cost_lamports=10_000, t_ms=5, slot=1, signature="same")])
        _pnl, _c, _p, holds, _u, same_tx = match_sell(lots, 100, 11_000, 5, "same")
        self.assertEqual(same_tx, 1)
        self.assertEqual(holds, [0])


class LeaderboardFixtureTests(unittest.TestCase):
    def test_noisy_board_ranks_organic_wallet(self) -> None:
        rows = profitable_organic_book(n_mints=6, wins=3, hold_ms=180_000)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            dump_jsonl(rows, path)
            result = run([path], thresholds=(NOISY_V0,))
        board = result["boards"]["noisy_v0"]
        wallets = [row["wallet"] for row in board]
        self.assertIn(W, wallets)
        lead = next(row for row in board if row["wallet"] == W)
        self.assertEqual(lead["closed_mints"], 6)
        self.assertAlmostEqual(lead["win_rate"], 0.5, places=4)
        self.assertGreater(lead["realized_pnl_sol"], 0)
        self.assertLessEqual(lead["top_mint_pnl_share"], 0.70)
        self.assertEqual(lead["vetoes"], [])
        self.assertGreaterEqual(lead["median_hold_ms"], 15_000)
        self.assertEqual(lead["copy_round_trip_bps"], COPY_ROUND_TRIP_BPS)
        self.assertLess(lead["copy_haircut_pnl_sol"], lead["realized_pnl_sol"])

    def test_strict_empty_on_short_book(self) -> None:
        rows = profitable_organic_book(n_mints=6, wins=3)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            dump_jsonl(rows, path)
            result = run([path], thresholds=(STRICT, NOISY_V0))
        self.assertEqual(result["boards"]["strict"], [])
        self.assertTrue(result["noisy_short_window"])
        self.assertGreater(result["board_sizes"]["noisy_v0"], 0)

    def test_strict_keeps_twenty_closed_mints(self) -> None:
        rows = profitable_organic_book(n_mints=20, wins=10, hold_ms=180_000)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            dump_jsonl(rows, path)
            result = run([path], thresholds=(STRICT,))
        board = result["boards"]["strict"]
        self.assertEqual([row["wallet"] for row in board], [W])
        self.assertEqual(board[0]["closed_mints"], 20)

    def test_sniper_veto(self) -> None:
        rows = []
        for i in range(4):
            mint = f"Snipe{i}pump"
            t0 = 2_000_000 + i * 1000
            slot0 = 5000 + i
            # leader is first print (create tx / slot 0-2)
            rows.extend(closed_rt(mint, W, t0, slot0, 180_000, 200_000_000, 280_000_000))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            dump_jsonl(rows, path)
            result = run([path], thresholds=(NOISY_V0,))
        lead = next(r for r in result["top_unfiltered_by_score"] if r["wallet"] == W)
        self.assertIn(VETO_SNIPER, lead["vetoes"])
        self.assertEqual(result["boards"]["noisy_v0"], [])

    def test_bot_veto_subsecond_holds(self) -> None:
        rows: list[dict] = []
        for i in range(4):
            mint = f"Bot{i}pump"
            t0 = 3_000_000 + i * 1000
            slot0 = 8000 + i * 20
            rows.extend(seed_mint(mint, t0, slot0))
            rows.extend(closed_rt(mint, W, t0 + 8_000, slot0 + 12, 200, 200_000_000, 250_000_000))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            dump_jsonl(rows, path)
            result = run([path], thresholds=(NOISY_V0,))
        lead = next(r for r in result["top_unfiltered_by_score"] if r["wallet"] == W)
        self.assertIn(VETO_BOT, lead["vetoes"])
        self.assertEqual(result["boards"]["noisy_v0"], [])

    def test_wash_veto(self) -> None:
        rows: list[dict] = []
        for i in range(5):
            mint = f"Wash{i}pump"
            t0 = 4_000_000 + i * 1000
            slot0 = 9000 + i * 20
            rows.extend(seed_mint(mint, t0, slot0))
            # ~0 pnl, 2s hold
            rows.extend(closed_rt(mint, W, t0 + 8_000, slot0 + 12, 2_000, 200_000_000, 201_000_000))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            dump_jsonl(rows, path)
            result = run([path], thresholds=(NOISY_V0,))
        lead = next(r for r in result["top_unfiltered_by_score"] if r["wallet"] == W)
        self.assertIn(VETO_WASH, lead["vetoes"])

    def test_transfer_in_veto(self) -> None:
        rows = [
            trade(mint="DumpMintpump", trader=W, side="sell", t_ms=10, slot=10, sol=5_000_000_000, token=9_000_000),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            dump_jsonl(rows, path)
            result = run([path], thresholds=(NOISY_V0,))
        lead = next(r for r in result["top_unfiltered_by_score"] if r["wallet"] == W)
        self.assertIn(VETO_TRANSFER_IN, lead["vetoes"])
        self.assertGreater(lead["unmatched_sell_sol"], 0)

    def test_one_mint_concentration_fails_strict_share(self) -> None:
        rows = profitable_organic_book(n_mints=20, wins=10, hold_ms=180_000)
        # extra monster mint
        mint = "HugeMintpump"
        rows.extend(seed_mint(mint, 9_000_000, 50_000))
        rows.extend(closed_rt(mint, W, 9_008_000, 50_012, 180_000, 200_000_000, 20_000_000_000))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            dump_jsonl(rows, path)
            result = run([path], thresholds=(STRICT,))
        self.assertEqual(result["boards"]["strict"], [])
        lead = next(r for r in result["top_unfiltered_by_score"] if r["wallet"] == W)
        self.assertGreater(lead["top_mint_pnl_share"], 0.40)

    def test_win_rate_band_rejects_100pct(self) -> None:
        rows = profitable_organic_book(n_mints=6, wins=6, hold_ms=180_000)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            dump_jsonl(rows, path)
            result = run([path], thresholds=(NOISY_V0,))
        # 100% WR is outside 30-75 noisy band
        self.assertEqual(result["boards"]["noisy_v0"], [])

    def test_creator_overlay_vetoes_creator(self) -> None:
        rows = profitable_organic_book(n_mints=6, wins=3)
        with tempfile.TemporaryDirectory() as tmp:
            trades = Path(tmp) / "trades.jsonl"
            creates = Path(tmp) / "creates.jsonl"
            dump_jsonl(rows, trades)
            dump_jsonl([{"mint": f"Mint{i:04d}pump", "creator": W, "slot": 1000, "signature": "c"} for i in range(6)], creates)
            result = run([trades], creates_path=creates, thresholds=(NOISY_V0,))
            self.assertEqual(load_creates(creates)["Mint0000pump"]["creator"], W)
        lead = next(r for r in result["top_unfiltered_by_score"] if r["wallet"] == W)
        self.assertIn(VETO_CREATOR, lead["vetoes"])

    def test_follower_farm_veto(self) -> None:
        mint = "FarmMintpump"
        t0, slot0 = 5_000_000, 12_000
        rows = seed_mint(mint, t0, slot0)
        rows.extend(closed_rt(mint, W, t0 + 8_000, slot0 + 12, 60_000, 200_000_000, 400_000_000))
        sell_t = t0 + 8_000 + 60_000
        for i in range(10):
            other = f"Fol{i:02d}1111111111111111111111111111111111111"
            rows.append(
                trade(
                    mint=mint,
                    trader=other,
                    side="buy",
                    t_ms=sell_t - 5_000 + i * 200,
                    slot=slot0 + 100 + i,
                    sol=50_000_000,
                )
            )
        # enough other closed mints so farm_frac is about the farmed one... need farm_frac >= 0.4
        # only 1 closed mint => farm_frac=1
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            dump_jsonl(rows, path)
            result = run([path], thresholds=(NOISY_V0,))
        lead = next(r for r in result["top_unfiltered_by_score"] if r["wallet"] == W)
        self.assertIn(VETO_FARM, lead["vetoes"])

    def test_follow_signal_schema_and_wave(self) -> None:
        rows = profitable_organic_book(n_mints=6, wins=3, hold_ms=180_000)
        mint0 = "Mint0000pump"
        t_lead = 1_000_000 + 8_000
        for i in range(6):
            other = f"Wave{i:02d}111111111111111111111111111111111111"
            rows.append(
                trade(
                    mint=mint0,
                    trader=other,
                    side="buy",
                    t_ms=t_lead + 800 + i * 100,
                    slot=1012 + i,
                    sol=20_000_000,
                )
            )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            out = Path(tmp) / "out"
            dump_jsonl(rows, path)
            result = run([path], thresholds=(NOISY_V0,))
            write_outputs(result, out)
            signals = result["follow_signals"]["noisy_v0"]
            self.assertGreaterEqual(len(signals), 6)
            sig = next(s for s in signals if s["mint"] == mint0)
            self.assertEqual(sig["v"], 1)
            self.assertEqual(sig["type"], "follow_signal")
            for key in ("mint", "signal_t_ms", "slot", "wallet", "features"):
                self.assertIn(key, sig)
            self.assertEqual(sig["wallet"], W)
            self.assertEqual(sig["features"]["signal_kind"], "entry")
            self.assertEqual(sig["features"]["board"], "noisy_v0")
            wave = next(w for w in result["follower_waves"]["noisy_v0"] if w["mint"] == mint0)
            self.assertGreaterEqual(wave["unique_wallets_by_ms"]["2000"], 6)
            self.assertIsNotNone(wave["first_follower_dt_ms"])
            self.assertLessEqual(wave["first_follower_dt_ms"], 1000)
            text = (out / "follow-signals-noisy_v0.jsonl").read_text(encoding="utf-8").strip().splitlines()
            parsed = json.loads(text[0])
            self.assertEqual(parsed["type"], "follow_signal")
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertIn("follower_wave_summary", summary)
            self.assertTrue(summary["paper_only"])

    def test_cap_50(self) -> None:
        rows: list[dict] = []
        for w_i in range(60):
            trader = f"W{w_i:03d}11111111111111111111111111111111111111"
            for m_i in range(4):
                mint = f"C{w_i:03d}M{m_i}pump"
                t0 = 6_000_000 + w_i * 10_000 + m_i * 100
                slot0 = 20_000 + w_i * 30 + m_i
                rows.extend(seed_mint(mint, t0, slot0))
                win = m_i < 2
                rows.extend(
                    closed_rt(
                        mint,
                        trader,
                        t0 + 8_000,
                        slot0 + 12,
                        180_000,
                        200_000_000,
                        260_000_000 if win else 150_000_000,
                    )
                )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            dump_jsonl(rows, path)
            result = run([path], thresholds=(NOISY_V0,))
        self.assertLessEqual(len(result["boards"]["noisy_v0"]), 50)
        ranks = [row["rank"] for row in result["boards"]["noisy_v0"]]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))

    def test_threshold_defaults(self) -> None:
        self.assertEqual(STRICT.min_closed_mints, 20)
        self.assertEqual(STRICT.cap, 50)
        self.assertEqual(NOISY_V0.name, "noisy_v0")
        self.assertLess(NOISY_V0.min_closed_mints, STRICT.min_closed_mints)

    def test_follower_wave_unit(self) -> None:
        from tools.wallet_leaderboard import Trade

        leader = Trade(t_ms=1000, slot=10, mint="M", trader=W, side="buy", sol_lamports=1, token_raw=1, signature="a", venue="pump_bonding", event_index=0)
        later = [
            Trade(t_ms=1300, slot=11, mint="M", trader="A", side="buy", sol_lamports=10, token_raw=1, signature="b", venue="pump_bonding", event_index=0),
            Trade(t_ms=4000, slot=18, mint="M", trader="B", side="buy", sol_lamports=20, token_raw=1, signature="c", venue="pump_bonding", event_index=0),
        ]
        wave = follower_wave(leader, later)
        self.assertEqual(wave["first_follower_dt_ms"], 300)
        self.assertEqual(wave["unique_wallets_by_ms"]["400"], 1)
        self.assertEqual(wave["unique_wallets_by_ms"]["2000"], 1)
        self.assertEqual(wave["unique_wallets_by_ms"]["5000"], 2)

    def test_follow_signals_entry_only(self) -> None:
        from tools.wallet_leaderboard import Trade, WalletReport, build_mint_meta

        trades = [
            Trade(t_ms=1, slot=1, mint="M", trader=C, side="buy", sol_lamports=1, token_raw=1, signature="c", venue="pump_bonding", event_index=0),
            Trade(t_ms=5000, slot=10, mint="M", trader=W, side="buy", sol_lamports=2, token_raw=1, signature="l1", venue="pump_bonding", event_index=0),
            Trade(t_ms=6000, slot=12, mint="M", trader=W, side="buy", sol_lamports=2, token_raw=1, signature="l2", venue="pump_bonding", event_index=0),
        ]
        metas = build_mint_meta(trades, {})
        rep = WalletReport(
            wallet=W,
            trade_count=2,
            buy_count=2,
            sell_count=0,
            closed_mints=3,
            open_mints=0,
            win_count=1,
            loss_count=1,
            win_rate=0.5,
            realized_pnl_sol=0.1,
            copy_haircut_pnl_sol=0.09,
            invested_sol=0.2,
            proceeds_sol=0.3,
            unmatched_sell_sol=0.0,
            top_mint="M",
            top_mint_pnl_sol=0.1,
            top_mint_pnl_share=0.3,
            median_hold_ms=180000,
            p25_hold_ms=100000,
            p75_hold_ms=200000,
            median_lot_hold_ms=180000,
            profit_factor=1.5,
            avg_r=0.2,
            sniper_buy_frac=0.0,
            organic_early_frac=0.5,
            first_print_mints=0,
            same_create_tx_buys=0,
            subsecond_lot_hold_frac=0.0,
            farm_closed_frac=0.0,
            wash_closed_frac=0.0,
            buyer_rank_median=3.0,
            median_delta_slot=12.0,
            vetoes=[],
            score=1.0,
            reasons=["ok"],
            hold_ms_n=3,
            window_span_ms=1000,
            trades_per_min=1.0,
        )
        sigs, _waves = follow_signals(
            trades, metas, {W: rep}, [{"wallet": W}], index_mint_buy_full(trades), "noisy_v0"
        )
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["signal_t_ms"], 5000)
        self.assertEqual(Thresholds().cap, 50)


if __name__ == "__main__":
    unittest.main()
