"""Paper tape scoreboard tests, including the recorded program-data fixtures."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from observe.trade_decode import decode_program_data, records_from_logs
from tools.paper_curve_math import (
    BONDING_FEE_PPM,
    DEFAULT_SIZE_LAMPORTS,
    PRIORITY_FEE_LAMPORTS,
    TOKEN_ACCOUNT_RENT_LAMPORTS,
    TOKEN_RAW_OFFSET,
    after_fee,
    pumpswap_sol_fee_ppm,
    quote_buy,
    quote_sell,
    venue_fee_ppm,
)
from tools.paper_price_path import (
    CreateSignal,
    MintPath,
    TapePrint,
    build_paths,
    create_from_observe_row,
    load_creates,
    price_path_records,
    print_from_trade_row,
    stream_paths,
)
from tools.paper_tape_scoreboard import (
    EXIT_RULES,
    _exit_fail_only_ev,
    _stuck_loss,
    _symmetric_ev,
    build_scoreboard,
    features_at_t,
    random_mints,
    simulate_book,
)

FIXTURES = Path(__file__).resolve().parent.parent / "observe" / "fixtures"
Q0 = 35_000_000_000
B0 = 1_073_000_000_000_000
T0 = 10_000_000_000
SIZE = DEFAULT_SIZE_LAMPORTS


def _b64(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8").strip()


def _line(name: str) -> str:
    return f"Program data: {_b64(name)}"


def _print(
    t_ms: int,
    quote: int = Q0,
    base: int = B0,
    *,
    venue: str = "pump_bonding",
    side: str = "buy",
    slot: int = 1,
    event_index: int = 0,
    sol_lamports: int = 1_000_000,
) -> TapePrint:
    price = quote / (base * 1000)
    return TapePrint(
        t_recv_ms=t_ms,
        slot=slot,
        event_index=event_index,
        venue=venue,
        side=side,
        sol_lamports=sol_lamports,
        quote_reserve=quote,
        base_reserve=base,
        price_sol=price,
        market_cap_sol=price * 1_000_000_000,
    )


def _create(mint: str = "MintA", t_ms: int = T0, **kwargs: object) -> CreateSignal:
    fields: dict[str, object] = dict(
        mint=mint,
        t_signal_ms=t_ms,
        creator="CreatorA",
        signature="sigA",
        v_sol=35.0,
        v_token_ui=1_073_000_000.0,
        mcap_sol=35.0 / 1_073_000_000.0 * 1_000_000_000,
        initial_buy_ui=1_000_000.0,
        sol_amount=5.0,
    )
    fields.update(kwargs)
    return CreateSignal(**fields)  # type: ignore[arg-type]


def _path(prints: list[TapePrint], mint: str = "MintA", t_ms: int = T0, **kwargs: object) -> MintPath:
    return MintPath(create=_create(mint, t_ms, **kwargs), prints=list(prints))


class CurveMathTests(unittest.TestCase):
    def test_buy_locks_integer_constant_product(self) -> None:
        buy = quote_buy(
            venue="pump_bonding",
            size_lamports=SIZE,
            quote_lamports=30_000_000_000,
            base_raw=B0,
            market_cap=28.0,
        )
        self.assertIsNotNone(buy)
        assert buy is not None
        after_portal = SIZE * 995_000 // 1_000_000
        self.assertEqual(after_portal, 49_750_000)
        net = after_portal * 987_500 // 1_000_000
        self.assertEqual(net, 49_128_125)
        self.assertEqual(buy.net_in_lamports, net)
        self.assertEqual(buy.tokens_raw, 1_754_276_460_392)
        self.assertEqual(buy.venue_fee_ppm, BONDING_FEE_PPM)
        self.assertLess(buy.tokens_raw, bonding_real := B0 - TOKEN_RAW_OFFSET)
        self.assertGreater(bonding_real, 0)

    def test_full_stack_is_harsher_than_protocol_95bps(self) -> None:
        buy = quote_buy(
            venue="pump_bonding",
            size_lamports=SIZE,
            quote_lamports=Q0,
            base_raw=B0,
            market_cap=40.0,
        )
        assert buy is not None
        net95 = after_fee(SIZE, 9_500)
        tokens95 = net95 * B0 // (Q0 + net95)
        self.assertLess(buy.tokens_raw, tokens95)

    def test_fee_tiers(self) -> None:
        self.assertEqual(pumpswap_sol_fee_ppm(0), 12_500)
        self.assertEqual(pumpswap_sol_fee_ppm(419.9), 12_500)
        self.assertEqual(pumpswap_sol_fee_ppm(420), 12_000)
        self.assertEqual(pumpswap_sol_fee_ppm(98_240), 3_000)
        self.assertEqual(pumpswap_sol_fee_ppm(1_000_000), 3_000)
        self.assertEqual(venue_fee_ppm("pump_bonding", 100_000), BONDING_FEE_PPM)

    def test_completing_the_curve_is_not_a_partial(self) -> None:
        buy = quote_buy(
            venue="pump_bonding",
            size_lamports=SIZE,
            quote_lamports=40_000_000_000,
            base_raw=TOKEN_RAW_OFFSET + 500,
            market_cap=80.0,
        )
        self.assertIsNone(buy)

    def test_sell_reverts_when_real_sol_cannot_pay(self) -> None:
        tokens = 1_754_276_460_392
        self.assertIsNone(
            quote_sell(
                venue="pump_bonding",
                tokens_raw=tokens,
                quote_lamports=30_000_000_000,
                base_raw=B0,
                market_cap=28.0,
            )
        )


class RecordedFixtureTests(unittest.TestCase):
    def test_recorded_bonding_trade_is_a_path_point(self) -> None:
        rows = records_from_logs(
            [_line("pump_trade_event.b64")],
            slot=450276100,
            signature="sig",
            t_recv_ms=1_790_318_932_123,
            commitment="confirmed",
            feed="public_rpc_logs",
        )
        self.assertEqual(len(rows), 1)
        parsed = print_from_trade_row(rows[0])
        self.assertIsNotNone(parsed)
        assert parsed is not None
        mint, pr = parsed
        self.assertEqual(mint, "5oqFhj53FQHJipb24tRB4GzAobW5VBwonh7qC5V5pump")
        self.assertEqual(pr.quote_reserve, rows[0]["quote_reserve"])
        self.assertEqual(pr.base_reserve, rows[0]["base_reserve"])
        self.assertAlmostEqual(pr.price_sol, rows[0]["price_sol"])
        self.assertAlmostEqual(pr.market_cap_sol, 78.562, places=2)
        create = _create(mint, t_ms=pr.t_recv_ms - 500, v_sol=None, v_token_ui=None, mcap_sol=None)
        path = build_paths({mint: create}, rows)[mint]
        records = price_path_records(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["schema"], "paper_price_path_v1")
        self.assertEqual(records[0]["mint"], mint)
        self.assertEqual(records[0]["t_recv_ms"], pr.t_recv_ms)
        self.assertEqual(records[0]["venue"], "pump_bonding")
        self.assertNotIn("pnl_sol", records[0])

    def test_recorded_pumpswap_without_a_mint_is_not_a_path_point(self) -> None:
        ev = decode_program_data(base64.b64decode(_b64("pumpswap_sell_event.b64")))
        assert ev is not None
        self.assertIsNone(ev.get("mint"))
        self.assertEqual(ev["venue"], "pumpswap")


class FillAndExitTests(unittest.TestCase):
    def test_rule_grid_covers_the_holds_and_stops(self) -> None:
        ids = [rule.rule_id for rule in EXIT_RULES]
        self.assertEqual(
            ids,
            [
                "hold_30s",
                "hold_1m",
                "hold_2m",
                "hold_5m",
                "hold_10m",
                "hold_15m",
                "hold_30m",
                "tp50_sl30",
                "tp100_sl50",
                "tp200_sl50",
                "trail30",
                "trail50",
            ],
        )

    def test_unchanged_book_round_trip_loses_fees_and_stays_realized(self) -> None:
        path = _path([_print(T0)])
        tape_end = T0 + 60_000
        rows = simulate_book([path], latencies=(1.0,), tape_end_ms=tape_end, rules=EXIT_RULES[:1])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["entry_status"], "filled")
        self.assertEqual(row["exit_status"], "realized")
        self.assertEqual(row["exit_venue"], "pump_bonding")
        pnl = row["pnl_lamports"]
        self.assertLess(pnl, -2 * PRIORITY_FEE_LAMPORTS)
        self.assertGreater(pnl, -5_000_000)
        self.assertEqual(row["pnl_sol"], pnl / 1_000_000_000)

    def test_rug_is_a_realized_loss_and_stays_in_the_book(self) -> None:
        calm = _print(T0)
        rugged = _print(T0 + 5_000, quote=30_000_000_000, base=B0)
        path = _path([calm, rugged])
        rows = simulate_book([path], latencies=(1.0,), tape_end_ms=T0 + 120_000, rules=EXIT_RULES[:1])
        row = rows[0]
        self.assertEqual(row["exit_status"], "no_exit_liquidity")
        self.assertEqual(row["pnl_lamports"], _stuck_loss(SIZE))
        self.assertEqual(
            row["pnl_lamports"],
            -(SIZE + 2 * PRIORITY_FEE_LAMPORTS + TOKEN_ACCOUNT_RENT_LAMPORTS),
        )
        board = build_scoreboard(
            rows,
            creates_n=1,
            tape_end_ms=T0 + 120_000,
            tape_start_ms=T0,
            size_lamports=SIZE,
            slippage_cap=0.15,
            latencies=(1.0,),
        )
        stats = board["books"]["buy_every_create"]["by_exit"]["hold_30s"]
        self.assertEqual(stats["n"], 1)
        self.assertEqual(stats["no_exit_n"], 1)
        self.assertAlmostEqual(stats["total_sol"], row["pnl_sol"])
        self.assertEqual(stats["win_rate"], 0.0)

    def test_slippage_miss_is_not_counted_as_a_loss(self) -> None:
        calm = _print(T0)
        hot = _print(T0 + 400, quote=80_000_000_000, base=B0)
        path = _path([calm, hot])
        rows = simulate_book([path], latencies=(1.0,), tape_end_ms=T0 + 120_000, rules=EXIT_RULES[:1])
        self.assertEqual(rows[0]["entry_status"], "missed_slippage")
        self.assertIsNone(rows[0]["pnl_lamports"])
        board = build_scoreboard(
            rows,
            creates_n=1,
            tape_end_ms=T0 + 120_000,
            tape_start_ms=T0,
            size_lamports=SIZE,
            slippage_cap=0.15,
            latencies=(1.0,),
        )
        stats = board["books"]["buy_every_create"]["by_exit"]["hold_30s"]
        self.assertEqual(stats["n"], 0)
        self.assertEqual(stats["miss_n"], 1)
        self.assertEqual(stats["total_sol"], 0.0)

    def test_censored_hold_is_outside_realized_n(self) -> None:
        path = _path([_print(T0)])
        rows = simulate_book([path], latencies=(1.0,), tape_end_ms=T0 + 2_000, rules=EXIT_RULES[:1])
        self.assertEqual(rows[0]["entry_status"], "filled")
        self.assertEqual(rows[0]["exit_status"], "censored")
        self.assertIsNone(rows[0]["pnl_lamports"])
        board = build_scoreboard(
            rows,
            creates_n=1,
            tape_end_ms=T0 + 2_000,
            tape_start_ms=T0,
            size_lamports=SIZE,
            slippage_cap=0.15,
            latencies=(1.0,),
        )
        stats = board["books"]["buy_every_create"]["by_exit"]["hold_30s"]
        self.assertEqual(stats["n"], 0)
        self.assertEqual(stats["censored_n"], 1)

    def test_take_profit_stop_and_trail_use_prints_after_entry(self) -> None:
        entry_px = _print(T0)
        # +43% then a drop to 69% of that peak. TP 50% does not fire. SL 30% does
        # not fire on the way up. Trail 30% fires on the drop; trail 50% does not.
        up = _print(T0 + 2_000, quote=50_000_000_000, base=B0)
        base_down = int(B0 * 32 / (0.69 * 50))
        down = _print(T0 + 10_000, quote=32_000_000_000, base=base_down)
        path = _path([entry_px, up, down])
        rules = [rule for rule in EXIT_RULES if rule.rule_id in ("tp50_sl30", "tp100_sl50", "trail30", "trail50")]
        rows = {
            row["exit_rule"]: row
            for row in simulate_book([path], latencies=(1.0,), tape_end_ms=T0 + 40 * 60_000, rules=rules)
        }
        self.assertEqual(rows["trail30"]["trigger"], "trail")
        self.assertEqual(rows["trail30"]["exit_status"], "realized")
        self.assertEqual(rows["trail50"]["trigger"], "time_stop")
        self.assertIn(rows["tp50_sl30"]["trigger"], ("tp", "sl", "time_stop"))
        # The up move is +43%, under the 50% take-profit, and the down move is
        # the trail. Stop fires only if a print is 30% under the entry spot.
        down_vs_entry = down.price_sol / entry_px.price_sol - 1.0
        if down_vs_entry <= -0.30:
            self.assertEqual(rows["tp50_sl30"]["trigger"], "sl")
        else:
            self.assertEqual(rows["tp50_sl30"]["trigger"], "time_stop")
        self.assertNotEqual(rows["tp100_sl50"]["trigger"], "tp")

    def test_stop_loss_when_spot_drops_and_the_pool_can_still_pay(self) -> None:
        entry_px = _print(T0)
        base_down = int(B0 * 32 / (0.70 * 35))
        down = _print(T0 + 2_000, quote=32_000_000_000, base=base_down)
        self.assertLess(down.price_sol / entry_px.price_sol - 1.0, -0.30)
        path = _path([entry_px, down])
        rules = [rule for rule in EXIT_RULES if rule.rule_id == "tp50_sl30"]
        row = simulate_book([path], latencies=(1.0,), tape_end_ms=T0 + 40 * 60_000, rules=rules)[0]
        self.assertEqual(row["trigger"], "sl")
        self.assertEqual(row["exit_status"], "realized")
        self.assertLess(row["pnl_lamports"], 0)
        self.assertGreater(row["pnl_lamports"], _stuck_loss(SIZE))

    def test_migration_sell_uses_pumpswap(self) -> None:
        bond = _print(T0)
        pool_base = 200_000_000_000_000
        pool_quote = 20_000_000_000
        swap = _print(T0 + 5_000, pool_quote, pool_base, venue="pumpswap")
        path = _path([bond, swap])
        row = simulate_book([path], latencies=(1.0,), tape_end_ms=T0 + 120_000, rules=EXIT_RULES[:1])[0]
        self.assertEqual(row["entry_venue"], "pump_bonding")
        self.assertEqual(row["exit_venue"], "pumpswap")
        self.assertEqual(row["exit_status"], "realized")

    def test_completed_curve_without_a_pool_is_a_loss(self) -> None:
        bond = _print(T0)
        closed = _print(T0 + 5_000, quote=80_000_000_000, base=TOKEN_RAW_OFFSET)
        path = _path([bond, closed])
        row = simulate_book([path], latencies=(1.0,), tape_end_ms=T0 + 120_000, rules=EXIT_RULES[:1])[0]
        self.assertEqual(row["exit_status"], "no_exit_liquidity")
        self.assertEqual(row["pnl_lamports"], _stuck_loss(SIZE))

    def test_features_at_t_ignore_prints_after_the_signal(self) -> None:
        first = _print(T0, quote=Q0, base=B0)
        mid = _print(T0 + 500, quote=40_000_000_000, base=B0)
        later = _print(T0 + 5_000, quote=60_000_000_000, base=B0)
        path = _path([later, first, mid])  # unsorted on purpose
        path.prints = [later, first, mid]
        # features_at_t assumes time order, as the builder sorts. Sort here.
        path.prints.sort(key=lambda p: (p.t_recv_ms, p.slot, p.event_index))
        feats = features_at_t(path)
        self.assertEqual(feats["f_tape_n"], 1)
        self.assertEqual(feats["f_tape_buy_n"], 1)
        self.assertEqual(feats["f_tape_last_price_sol"], first.price_sol)
        self.assertEqual(feats["f_tape_last_quote_lamports"], Q0)
        self.assertNotEqual(feats["f_tape_last_price_sol"], later.price_sol)
        rows = simulate_book([path], latencies=(1.0,), tape_end_ms=T0 + 120_000, rules=EXIT_RULES)
        prices = {row["f_tape_last_price_sol"] for row in rows}
        counts = {row["f_tape_n"] for row in rows}
        self.assertEqual(prices, {first.price_sol})
        self.assertEqual(counts, {1})
        hold = next(row for row in rows if row["exit_rule"] == "hold_30s")
        self.assertEqual(hold["entry_quote_reserve"], mid.quote_reserve)
        self.assertEqual(hold["entry_status"], "filled")
        self.assertNotEqual(hold["exit_spot_sol"], first.price_sol)

    def test_fail_rate_zero_is_the_tape_and_thirty_percent_can_flatter_a_rug(self) -> None:
        calm = _print(T0)
        # Small winner. A large winner makes a 30% entry-fail rate look worse,
        # which hides the flattering case: failed entries skipping rugs.
        winner_path = _path([calm, _print(T0 + 5_000, quote=38_000_000_000, base=B0)], mint="Win")
        rug_path = _path([calm, _print(T0 + 5_000, quote=30_000_000_000, base=B0)], mint="Rug")
        rows = simulate_book(
            [winner_path, rug_path],
            latencies=(1.0,),
            tape_end_ms=T0 + 120_000,
            rules=EXIT_RULES[:1],
        )
        by_mint = {row["mint"]: row for row in rows}
        win = by_mint["Win"]
        rug = by_mint["Rug"]
        self.assertEqual(win["exit_status"], "realized")
        self.assertGreater(win["pnl_lamports"], 0)
        self.assertEqual(rug["exit_status"], "no_exit_liquidity")
        self.assertEqual(_symmetric_ev(win, 0.0, SIZE), float(win["pnl_lamports"]))
        self.assertLess(_symmetric_ev(win, 0.30, SIZE), win["pnl_lamports"])
        self.assertGreater(_symmetric_ev(rug, 0.30, SIZE), rug["pnl_lamports"])
        self.assertEqual(_exit_fail_only_ev(rug, 0.30, SIZE), float(rug["pnl_lamports"]))
        self.assertLess(_exit_fail_only_ev(win, 0.30, SIZE), win["pnl_lamports"])
        board = build_scoreboard(
            rows,
            creates_n=2,
            tape_end_ms=T0 + 120_000,
            tape_start_ms=T0,
            size_lamports=SIZE,
            slippage_cap=0.15,
            latencies=(1.0,),
        )
        stats = board["books"]["buy_every_create"]["by_exit"]["hold_30s"]
        self.assertEqual(stats["n"], 2)
        self.assertEqual(stats["no_exit_n"], 1)
        self.assertAlmostEqual(stats["total_lamports"], win["pnl_lamports"] + rug["pnl_lamports"])
        sens = stats["fail_sensitivity"]
        self.assertAlmostEqual(sens["0"]["symmetric_total_sol"], stats["total_sol"])
        self.assertGreater(sens["0.3"]["symmetric_total_sol"], stats["total_sol"])
        self.assertLess(sens["0.3"]["exit_fail_only_total_sol"], stats["total_sol"])

    def test_random_subsample_is_seeded(self) -> None:
        first = random_mints([f"m{i}" for i in range(10)], fraction=0.2, seed=1)
        second = random_mints([f"m{i}" for i in range(10)], fraction=0.2, seed=1)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertEqual(len(set(first)), 2)


class CreateAndTapeLoaderTests(unittest.TestCase):
    def test_observe_create_parses_and_migration_does_not(self) -> None:
        create = create_from_observe_row(
            {
                "stream": "subscribeNewToken",
                "txType": "create",
                "mint": "MintA",
                "t_ws": "2026-09-25T06:58:37.000+00:00",
                "traderPublicKey": "CreatorA",
                "signature": "sigA",
                "vSolInBondingCurve": 31.97530864,
                "vTokensInBondingCurve": 1006714285.776477,
                "marketCapSol": 31.76,
                "initialBuy": 66285714.22,
                "solAmount": 1.97530864,
            }
        )
        self.assertIsNotNone(create)
        assert create is not None
        self.assertEqual(create.mint, "MintA")
        self.assertEqual(create.creator, "CreatorA")
        self.assertAlmostEqual(create.v_sol or 0, 31.97530864)
        self.assertIsNone(
            create_from_observe_row(
                {
                    "stream": "subscribeMigration",
                    "txType": "migrate",
                    "mint": "MintA",
                    "t_ws": "2026-09-25T07:00:00.000+00:00",
                }
            )
        )

    def test_stream_keeps_only_create_mints_and_sorts(self) -> None:
        t_ms = 1_790_000_000_000
        create_row = {
            "stream": "subscribeNewToken",
            "txType": "create",
            "mint": "MintA",
            "t_ws": "2026-09-25T06:58:37.000+00:00",
            "vSolInBondingCurve": 35.0,
            "vTokensInBondingCurve": 1_073_000_000.0,
        }
        trade_late = {
            "type": "trade",
            "mint": "MintA",
            "venue": "pump_bonding",
            "quote_is_wsol": True,
            "t_recv_ms": t_ms + 20,
            "quote_reserve": Q0,
            "base_reserve": B0,
            "price_sol": Q0 / (B0 * 1000),
            "market_cap_sol": 40.0,
            "side": "sell",
            "sol_lamports": 10,
            "slot": 2,
            "event_index": 0,
        }
        trade_early = dict(trade_late, t_recv_ms=t_ms + 10, slot=1, side="buy", sol_lamports=20)
        other = dict(trade_late, mint="Other")
        non_sol = dict(trade_late, mint="MintA", quote_is_wsol=False, venue="pumpswap")
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            creates = root / "observe.jsonl"
            tape = root / "trades.jsonl"
            creates.write_text(json.dumps(create_row) + "\n", encoding="utf-8")
            tape.write_text(
                "\n".join(
                    [
                        "{not json",
                        json.dumps(trade_late),
                        json.dumps(other),
                        json.dumps(non_sol),
                        json.dumps(trade_early),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            loaded = load_creates([creates])
            self.assertIn("MintA", loaded)
            paths, stats = stream_paths(loaded, [tape])
            self.assertEqual(stats.bad_json, 1)
            self.assertEqual(stats.other_mint, 1)
            self.assertEqual(stats.non_wsol, 1)
            self.assertEqual(stats.kept, 2)
            prints = paths["MintA"].prints
            self.assertEqual([p.t_recv_ms for p in prints], [t_ms + 10, t_ms + 20])
            self.assertEqual(prints[0].side, "buy")

    def test_duplicate_print_across_files_is_kept_once(self) -> None:
        row = {
            "type": "trade",
            "mint": "MintA",
            "venue": "pump_bonding",
            "quote_is_wsol": True,
            "t_recv_ms": T0,
            "quote_reserve": Q0,
            "base_reserve": B0,
            "price_sol": Q0 / (B0 * 1000),
            "market_cap_sol": 40.0,
            "side": "buy",
            "sol_lamports": 10,
            "slot": 1,
            "event_index": 0,
        }
        paths = build_paths({"MintA": _create()}, [row, dict(row)])
        self.assertEqual(len(paths["MintA"].prints), 1)

    def test_zst_tape_round_trip(self) -> None:
        if shutil.which("zstd") is None:
            self.skipTest("zstd CLI not installed")
        t_ms = T0 + 10
        create_row = {
            "stream": "subscribeNewToken",
            "txType": "create",
            "mint": "MintA",
            "t_ws": "2026-09-25T06:58:37.000+00:00",
            "vSolInBondingCurve": 35.0,
            "vTokensInBondingCurve": 1_073_000_000.0,
        }
        trade = {
            "type": "trade",
            "mint": "MintA",
            "venue": "pump_bonding",
            "quote_is_wsol": True,
            "t_recv_ms": t_ms,
            "quote_reserve": Q0,
            "base_reserve": B0,
            "price_sol": Q0 / (B0 * 1000),
            "market_cap_sol": 40.0,
            "side": "buy",
            "sol_lamports": 10,
            "slot": 1,
            "event_index": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = root / "trades.jsonl"
            plain.write_text(json.dumps(trade) + "\n", encoding="utf-8")
            zst = root / "trades.jsonl.zst"
            subprocess.run(["zstd", "-q", "-f", "-o", str(zst), str(plain)], check=True)
            creates = root / "observe.jsonl"
            creates.write_text(json.dumps(create_row) + "\n", encoding="utf-8")
            loaded = load_creates([creates])
            paths, stats = stream_paths(loaded, [zst])
            self.assertEqual(stats.kept, 1)
            self.assertEqual(paths["MintA"].prints[0].t_recv_ms, t_ms)


if __name__ == "__main__":
    unittest.main()
