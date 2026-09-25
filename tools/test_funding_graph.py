"""Funding graph: causal join, polite RPC, tightened promotion. No network."""

from __future__ import annotations

import calendar
import json
import tempfile
import unittest
from pathlib import Path

from tools.funding_graph import (
    BOT_MIN_BUYS,
    BOT_MIN_GAPS,
    BOT_MIN_MINTS,
    EARLY_BUYERS,
    EXCHANGE_WALLETS,
    PUBLIC_RPC,
    BACKFILL_PRIORITY,
    BUYER_PRIORITY,
    CREATOR_PRIORITY,
    CreditCap,
    CreditLedger,
    Enricher,
    FundingGraph,
    JobQueue,
    _BotWallet,
    JsonlAppender,
    RpcClient,
    RpcError,
    WalletRecord,
    choose_rps,
    cluster_hash,
    describe_rpc,
    empty_funding_features,
    fill_funding_features,
    inbound_sol,
    maybe_switch_rpc,
    resolve_rpc_url,
    resolve_wallet,
    rug_veto,
    score_rows,
)
from tools.laya_v0 import (
    BOT_MIN_BUYS as LAYA_BOT_MIN_BUYS,
    BOT_MIN_GAPS as LAYA_BOT_MIN_GAPS,
    BOT_MIN_MINTS as LAYA_BOT_MIN_MINTS,
    PROMOTION_DROP_N,
    PROMOTION_MIN_DAYS,
    PROMOTION_MIN_N,
    PROMOTION_RULE,
    BookTrade,
    FlowPrint,
    MintBook,
    book_stats,
    build_feature_rows,
)
from tools.paper_curve_math import LAMPORTS_PER_SOL
from tools.paper_price_path import CreateSignal

T0 = 10_000_000_000
DAY = calendar.timegm((2026, 9, 25, 12, 0, 0, 0, 0, 0)) * 1000
DAY_MS = 86_400_000


def _flow(t_ms: int, *, trader: str, price: float, side: str = "buy", slot: int = 1) -> FlowPrint:
    return FlowPrint(
        t_recv_ms=t_ms,
        slot=slot,
        event_index=0,
        venue="pump_bonding",
        side=side,
        sol_lamports=1_000_000,
        token_raw=1_000,
        trader=trader,
        quote_reserve=35_000_000_000,
        base_reserve=1_073_000_000_000_000,
        price_sol=price,
        market_cap_sol=price * 1_000_000_000,
    )


def _book(mint: str, t_ms: int, creator: str, flow: list[FlowPrint]) -> MintBook:
    return MintBook(
        create=CreateSignal(
            mint=mint,
            t_signal_ms=t_ms,
            creator=creator,
            signature="sig-" + mint,
            v_sol=35.0,
            v_token_ui=1_073_000_000.0,
            mcap_sol=30.0,
            initial_buy_ui=1.0,
            sol_amount=1.0,
        ),
        flow=sorted(flow, key=lambda pr: (pr.t_recv_ms, pr.slot, pr.event_index)),
    )


def _rec(wallet: str, funder: str | None, seen: int, *, exchange: bool = False) -> WalletRecord:
    return WalletRecord(
        wallet=wallet,
        funder=funder,
        amount_lamports=1_000_000_000,
        wallet_first_tx_ms=seen - 86_400_000,
        funded_at_ms=seen - 86_400_000,
        exchange=exchange,
        exchange_name="coinbase" if exchange else None,
        history_capped=False,
        status="resolved" if funder else "no_inbound",
        first_seen_ms=seen,
        role_hint="creator",
        rpc_pages=1,
    )


class _Wallets:
    def __init__(self) -> None:
        self.bots: set[str] = set()
        self.snipers: set[str] = set()


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def time(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class _Row:
    def __init__(self, mint: str, t_ms: int, pnl: int, features: dict[str, float]) -> None:
        self.mint = mint
        self.decision_t_ms = t_ms
        self.pnl = pnl
        self.features = features


def _tx(kind: str, source: str, dest: str, lamports: int, *, err: str | None = None) -> dict:
    info = {"source": source, "lamports": lamports}
    if kind == "transfer":
        info["destination"] = dest
    else:
        info["newAccount"] = dest
    return {
        "meta": {"err": err, "innerInstructions": []},
        "transaction": {"message": {"instructions": [{"parsed": {"type": kind, "info": info}}]}},
    }


class FundingGraphTests(unittest.TestCase):
    def test_bot_thresholds_match_laya(self) -> None:
        self.assertEqual(BOT_MIN_BUYS, LAYA_BOT_MIN_BUYS)
        self.assertEqual(BOT_MIN_MINTS, LAYA_BOT_MIN_MINTS)
        self.assertEqual(BOT_MIN_GAPS, LAYA_BOT_MIN_GAPS)
        self.assertEqual(EARLY_BUYERS, 4)

    def test_inbound_sol_skips_failed_and_reads_create_account(self) -> None:
        failed = _tx("transfer", "Funder", "Wallet", 5, err="boom")
        self.assertIsNone(inbound_sol(failed, "Wallet"))
        created = _tx("createAccount", "Funder", "Wallet", 9)
        self.assertEqual(inbound_sol(created, "Wallet"), ("Funder", 9))

    def test_resolve_walks_to_the_oldest_page(self) -> None:
        calls: list[str] = []
        pages = {"n": 0}

        def transport_full(_url: str, body: dict) -> dict:
            method = body["method"]
            calls.append(method)
            if method == "getSignaturesForAddress":
                pages["n"] += 1
                if pages["n"] == 1:
                    rows = [{"signature": f"s{i}", "blockTime": 500 - i} for i in range(1000)]
                    return {"result": rows}
                return {"result": [{"signature": "oldest", "blockTime": 10}]}
            self.assertEqual(body["params"][0], "oldest")
            return {"result": _tx("transfer", "FunderA", "Wallet", 42)}

        clock = _Clock()
        client = RpcClient("http://rpc.example", rps=1000, transport=transport_full, clock=clock.time, sleep=clock.sleep)
        rec = resolve_wallet(client, "Wallet", now_ms=1_000, exchanges={})
        self.assertEqual(rec.funder, "FunderA")
        self.assertEqual(rec.amount_lamports, 42)
        self.assertEqual(rec.wallet_first_tx_ms, 10_000)
        self.assertEqual(rec.status, "resolved")
        self.assertFalse(rec.exchange)

    def test_oversized_transaction_does_not_guess_a_later_funder(self) -> None:
        def transport(_url: str, body: dict) -> dict:
            method = body["method"]
            if method == "getSignaturesForAddress":
                return {"result": [{"signature": "later", "blockTime": 11}, {"signature": "big", "blockTime": 10}]}
            if body["params"][0] == "big":
                raise RpcError("http 413")
            return {"result": _tx("transfer", "LaterFunder", "Wallet", 7)}

        client = RpcClient("http://rpc.example", rps=1000, transport=transport, clock=lambda: 0.0, sleep=lambda _s: None)
        rec = resolve_wallet(client, "Wallet", now_ms=5_000, exchanges={})
        self.assertIsNone(rec.funder)
        self.assertEqual(rec.status, "tx_unavailable")
        self.assertIsNone(rec.wallet_first_tx_ms)
        self.assertTrue(rec.history_capped)

    def test_oversized_signature_page_is_written_once(self) -> None:
        def transport(_url: str, body: dict) -> dict:
            limit = body["params"][1]["limit"]
            if limit > 200:
                raise RpcError("http 413")
            raise RpcError("http 413")

        client = RpcClient("http://rpc.example", rps=1000, transport=transport, clock=lambda: 0.0, sleep=lambda _s: None)
        rec = resolve_wallet(client, "Wallet", now_ms=5_000, exchanges={})
        self.assertEqual(rec.status, "rpc_rejected")
        self.assertIsNone(rec.funder)
        self.assertEqual(client.calls, 2)

    def test_rate_limit_backs_off_without_wall_sleep(self) -> None:
        clock = _Clock()
        n = {"calls": 0}

        def transport(_url: str, _body: dict) -> dict:
            n["calls"] += 1
            if n["calls"] == 1:
                raise RpcError("429", limited=True)
            return {"result": []}

        client = RpcClient("http://rpc.example", rps=1000, transport=transport, clock=clock.time, sleep=clock.sleep)
        with self.assertRaises(RpcError):
            client.call("getSignaturesForAddress", ["W", {}])
        self.assertEqual(client.limited, 1)
        self.assertEqual(clock.t, 0.0)
        client.call("getSignaturesForAddress", ["W", {}])
        self.assertGreaterEqual(clock.t, 5.0)

    def test_creators_leave_the_queue_before_buyers(self) -> None:
        queue = JobQueue()
        queue.push(BUYER_PRIORITY, "buyer", "early_buyer")
        queue.push(CREATOR_PRIORITY, "creator", "creator")
        self.assertEqual(queue.pop()[:2], ("creator", "creator"))
        self.assertEqual(queue.pop()[:2], ("buyer", "early_buyer"))
        queued = JobQueue()
        queued.push(BUYER_PRIORITY, "buyer", "early_buyer", 1)
        queued.push(BACKFILL_PRIORITY, "seed_creator", "creator", 1)
        self.assertEqual(queued.pop()[0], "seed_creator")

    def test_append_only_first_row_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            writer = JsonlAppender(root)
            writer.append(_rec("W", "First", 1_000))
            writer.append(_rec("W", "Second", 2_000))
            loaded = FundingGraph.load(root)
            self.assertEqual(loaded.by_wallet["W"].funder, "First")
            text = (root / "funding-1970-01-01.jsonl").read_text(encoding="utf-8")
            self.assertEqual(text.count("\n"), 2)
            self.assertIn("First", text)
            self.assertNotIn("rewrite", text)

    def test_future_first_seen_is_invisible(self) -> None:
        book = _book("MintA", T0, "Creator", [_flow(T0 + 100, trader="B1", price=1.0)])
        graph = FundingGraph([_rec("Creator", "Funder", T0 + 10_000)])
        feats: dict[str, float] = {}
        fill_funding_features(book, T0 + 1_000, feats, _Wallets(), graph, {"MintA": book})
        self.assertEqual(feats["f_funder_known"], 0.0)
        self.assertFalse(rug_veto(feats))
        early = FundingGraph([_rec("Creator", "Funder", T0 - 1)])
        fill_funding_features(book, T0 + 1_000, feats, _Wallets(), early, {"MintA": book})
        self.assertEqual(feats["f_funder_known"], 1.0)
        self.assertEqual(feats["f_funder_cluster_hash"], cluster_hash("Funder"))

    def test_exchange_is_not_a_same_funder_bundle(self) -> None:
        exchange = next(iter(EXCHANGE_WALLETS))
        book = _book(
            "MintA",
            T0,
            "Creator",
            [
                _flow(T0 + 100, trader="B1", price=1.0),
                _flow(T0 + 200, trader="B2", price=1.0),
            ],
        )
        graph = FundingGraph(
            [
                _rec("Creator", exchange, T0 - 5, exchange=True),
                _rec("B1", exchange, T0 - 4, exchange=True),
                _rec("B2", exchange, T0 - 3, exchange=True),
            ]
        )
        feats: dict[str, float] = {}
        fill_funding_features(book, T0 + 1_000, feats, _Wallets(), graph, {"MintA": book})
        self.assertEqual(feats["f_creator_funded_by_exchange"], 1.0)
        self.assertEqual(feats["f_same_funder_early_n"], 0.0)
        self.assertEqual(feats["f_creator_fresh_wallet"], 0.0)
        self.assertFalse(rug_veto(feats))

    def test_same_funder_and_loop_are_causal(self) -> None:
        book = _book(
            "MintA",
            T0,
            "Creator",
            [
                _flow(T0 + 100, trader="Funder", price=1.0),
                _flow(T0 + 200, trader="B2", price=1.1),
                _flow(T0 + 50_000, trader="Late", price=0.2),
            ],
        )
        graph = FundingGraph(
            [
                _rec("Creator", "Funder", T0 - 10),
                _rec("B2", "Funder", T0 - 9),
                _rec("Late", "Funder", T0 - 8),
            ]
        )
        feats: dict[str, float] = {}
        fill_funding_features(book, T0 + 1_000, feats, _Wallets(), graph, {"MintA": book})
        self.assertEqual(feats["f_same_funder_early_n"], 1.0)
        self.assertEqual(feats["f_funder_creator_buyer_loop"], 1.0)
        self.assertTrue(rug_veto(feats))
        hidden = FundingGraph(
            [
                _rec("Creator", "Funder", T0 - 10),
                _rec("B2", "Funder", T0 + 50_000),
            ]
        )
        fill_funding_features(book, T0 + 1_000, feats, _Wallets(), hidden, {"MintA": book})
        self.assertEqual(feats["f_same_funder_early_n"], 0.0)

    def test_cluster_rug_ignores_an_unfinished_horizon_and_a_later_mint(self) -> None:
        old = _book(
            "Old",
            T0,
            "C1",
            [_flow(T0, trader="C1", price=1.0, side="buy"), _flow(T0 + 20_000, trader="S", price=0.2, side="sell")],
        )
        new = _book("New", T0 + 10_000, "C2", [_flow(T0 + 10_100, trader="B", price=1.0)])
        later = _book("Later", T0 + 90_000, "C3", [_flow(T0 + 90_100, trader="B", price=0.1)])
        graph = FundingGraph(
            [
                _rec("C1", "Ring", T0 - 100),
                _rec("C2", "Ring", T0 - 100),
                _rec("C3", "Ring", T0 - 100),
            ]
        )
        books = {"Old": old, "New": new, "Later": later}
        feats: dict[str, float] = {}
        fill_funding_features(new, T0 + 40_000, feats, _Wallets(), graph, books)
        self.assertEqual(feats["f_funder_prior_creates"], 1.0)
        self.assertEqual(feats["f_funder_prior_scored"], 1.0)
        self.assertEqual(feats["f_funder_prior_rug_frac"], 1.0)
        too_soon: dict[str, float] = {}
        fill_funding_features(new, T0 + 15_000, too_soon, _Wallets(), graph, books)
        self.assertEqual(too_soon["f_funder_prior_creates"], 1.0)
        self.assertEqual(too_soon["f_funder_prior_scored"], 0.0)
        self.assertNotEqual(too_soon["f_funder_prior_rug_frac"], too_soon["f_funder_prior_rug_frac"])

    def test_build_feature_rows_hides_a_graph_row_after_the_decision(self) -> None:
        book = _book("MintA", T0, "Creator", [_flow(T0 + 100, trader="B", price=1.0)])
        graph = FundingGraph([_rec("Creator", "Funder", T0 + 60_000)])
        rows, _diag = build_feature_rows({"MintA": book}, tape_end_ms=T0 + 180_000, offsets_ms=(5_000,), graph=graph)
        early = next(row for row in rows if row.trigger == "grid" and row.decision_t_ms == T0 + 5_000)
        self.assertEqual(early.features["f_funder_known"], 0.0)
        for name in empty_funding_features():
            self.assertIn(name, early.features)

    def test_unknown_features_do_not_veto(self) -> None:
        self.assertFalse(rug_veto(empty_funding_features()))

    def test_one_utc_day_does_not_clear_the_tight_bar(self) -> None:
        lamports = int(0.001 * LAMPORTS_PER_SOL)
        one_day = [BookTrade(f"m{i}", DAY, lamports) for i in range(PROMOTION_MIN_N)]
        stats = book_stats(one_day)
        self.assertEqual(stats["n_days"], 1)
        self.assertFalse(stats["promote"])
        self.assertGreater(stats["total_ex_top3_sol"], 0)

    def test_five_days_and_drop_top_three(self) -> None:
        lamports = int(0.001 * LAMPORTS_PER_SOL)
        spread = []
        for day in range(PROMOTION_MIN_DAYS):
            for i in range(20):
                spread.append(BookTrade(f"d{day}-{i}", DAY + day * DAY_MS, lamports))
        good = book_stats(spread)
        self.assertGreaterEqual(good["n"], PROMOTION_MIN_N)
        self.assertGreaterEqual(good["n_days"], PROMOTION_MIN_DAYS)
        self.assertTrue(good["majority_days_positive"])
        self.assertGreater(good["mean_ci90_sol"][0], 0)
        self.assertGreater(good["total_ex_top3_sol"], 0)
        self.assertTrue(good["promote"])

        heavy = int(10 * LAMPORTS_PER_SOL)
        tiny = int(-0.02 * LAMPORTS_PER_SOL)
        tailed = [BookTrade(f"w{i}", DAY + (i % PROMOTION_MIN_DAYS) * DAY_MS, heavy) for i in range(PROMOTION_DROP_N)]
        tailed.extend(
            BookTrade(f"s{i}", DAY + (i % PROMOTION_MIN_DAYS) * DAY_MS, tiny) for i in range(PROMOTION_MIN_N - PROMOTION_DROP_N)
        )
        bad = book_stats(tailed)
        self.assertLess(bad["total_ex_top3_sol"], 0)
        self.assertFalse(bad["promote"])

    def test_stale_buyers_drop_and_unresolved_creators_stay(self) -> None:
        now = 1_000_000_000
        queue = JobQueue()
        queue.push(BUYER_PRIORITY, "old_buyer", "early_buyer", now - 6 * 60_000)
        queue.push(CREATOR_PRIORITY, "old_creator", "creator", now - 6 * 60_000)
        queue.push(BUYER_PRIORITY, "fresh_buyer", "early_buyer", now - 1000)
        dropped = queue.drop_stale(now, 5 * 60_000)
        self.assertEqual(dropped, 1)
        left = {queue.pop()[0] for _ in range(len(queue))}
        self.assertEqual(left, {"old_creator", "fresh_buyer"})

    def test_queue_cap_drops_buyers_before_creators(self) -> None:
        queue = JobQueue()
        for i in range(5):
            queue.push(BUYER_PRIORITY, f"b{i}", "early_buyer", 1000 + i)
        for i in range(3):
            queue.push(CREATOR_PRIORITY, f"c{i}", "creator", 2000 + i)
        dropped = queue.drop_to_cap(4)
        self.assertEqual(dropped, 4)
        self.assertEqual(len(queue), 4)
        left = {queue.pop()[0] for _ in range(len(queue))}
        self.assertEqual(left, {"b4", "c0", "c1", "c2"})
        again = JobQueue()
        for i in range(3):
            again.push(CREATOR_PRIORITY, f"c{i}", "creator", 1000 + i)
        self.assertEqual(again.drop_to_cap(2), 1)
        self.assertEqual({again.pop()[0] for _ in range(2)}, {"c1", "c2"})

    def test_helius_key_switches_rate_without_logging_the_key(self) -> None:
        missing = Path("/tmp/does-not-exist-helius.env")
        public = resolve_rpc_url({}, missing)
        self.assertEqual(public, PUBLIC_RPC)
        self.assertEqual(describe_rpc(public), "public")
        self.assertEqual(choose_rps(public, None), 1.0)
        with self.assertRaises(ValueError):
            resolve_rpc_url({"HELIUS_API_KEY": "bad key"}, missing)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "helius.env"
            path.write_text("# note\nHELIUS_API_KEY=unit-test-key\n", encoding="utf-8")
            url = resolve_rpc_url({}, path)
            self.assertEqual(describe_rpc(url), "helius")
            self.assertNotIn("unit-test-key", describe_rpc(url))
            self.assertEqual(choose_rps(url, None), 5.0)
            self.assertEqual(choose_rps(url, 1.0), 1.0)
            client = RpcClient(PUBLIC_RPC, rps=1)
            self.assertIs(maybe_switch_rpc(client, None, {}, missing), client)
            nxt = maybe_switch_rpc(client, None, {}, path)
            self.assertIsNot(nxt, client)
            self.assertEqual(describe_rpc(nxt.url), "helius")
            self.assertAlmostEqual(nxt.min_interval, 0.2)
            self.assertIs(maybe_switch_rpc(nxt, None, {}, path), nxt)
            held = maybe_switch_rpc(client, 1.0, {}, path)
            self.assertAlmostEqual(held.min_interval, 1.0)

    def test_helius_credit_cap_persists_and_falls_back_to_public(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "helius-credits.json"
            ledger = CreditLedger(path, 2)
            self.assertTrue(ledger.charge())
            self.assertTrue(ledger.charge())
            self.assertFalse(ledger.charge())
            again = CreditLedger(path, 2)
            self.assertEqual(again.used, 2)
            self.assertNotIn("api-key", path.read_text(encoding="utf-8"))
            sent = {"n": 0}

            def transport(_url: str, _body: dict) -> dict:
                sent["n"] += 1
                return {"result": []}

            helius = RpcClient(
                "https://mainnet.helius-rpc.com/?api-key=unit-test-key",
                rps=1000,
                transport=transport,
                clock=lambda: 0.0,
                sleep=lambda _s: None,
                budget=again,
            )
            with self.assertRaises(CreditCap):
                helius.call("getTransaction", ["sig"])
            self.assertEqual(sent["n"], 0)
            public = RpcClient(
                PUBLIC_RPC,
                rps=1000,
                transport=transport,
                clock=lambda: 0.0,
                sleep=lambda _s: None,
                budget=again,
            )
            public.call("getSignaturesForAddress", ["W", {}])
            self.assertEqual(sent["n"], 1)
            self.assertEqual(again.used, 2)
            missing = Path(tmp) / "missing.env"
            fallen = maybe_switch_rpc(
                helius,
                2.0,
                {"HELIUS_API_KEY": "unit-test-key"},
                missing,
                again,
            )
            self.assertEqual(describe_rpc(fallen.url), "public")
            self.assertAlmostEqual(fallen.min_interval, 1.0)

    def test_score_is_preliminary_and_not_a_promote(self) -> None:
        rows = [
            _Row(f"m{i}", DAY + i * 1000, int(0.001 * LAMPORTS_PER_SOL), empty_funding_features())
            for i in range(16)
        ]
        report = score_rows(rows)
        self.assertTrue(report["preliminary"])
        self.assertIn("Re-run", report["preliminary_reason"])
        self.assertFalse(report["promote"])
        self.assertEqual(report["promotion_rule"], PROMOTION_RULE)
        self.assertIn("_RankWindow", report["selection"])
        self.assertGreater(report["oos_n"], 0)

    def test_enricher_skips_bots_and_retries_empty_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trades = root / "trades"
            creates = root / "creates"
            graph = root / "graph"
            trades.mkdir()
            creates.mkdir()
            calls = {"n": 0}

            def transport(_url: str, body: dict) -> dict:
                calls["n"] += 1
                if body["method"] == "getSignaturesForAddress":
                    wallet = body["params"][0]
                    return {"result": [{"signature": f"old-{wallet}", "blockTime": 10}]}
                wallet = str(body["params"][0]).removeprefix("old-")
                return {"result": _tx("transfer", "Funder", wallet, 7)}

            clock = _Clock()
            client = RpcClient("http://rpc.example", rps=1000, transport=transport, clock=clock.time, sleep=clock.sleep)
            enricher = Enricher(
                graph_dir=graph,
                trades_dir=trades,
                creates_dir=creates,
                client=client,
                now_ms=lambda: 5_000,
            )
            enricher.note_create("MintA", "Creator")
            bot = _BotWallet()
            for i in range(8):
                for k in range(4):
                    bot.observe(mint=f"m{i}", t_ms=i * 10 + k, slot=1, first_slot=1)
            self.assertTrue(bot.is_bot)
            enricher.bots["Bot"] = bot
            enricher.note_trade({"mint": "MintA", "side": "buy", "trader": "Bot", "slot": 1, "t_recv_ms": 10})
            enricher.note_trade({"mint": "MintA", "side": "buy", "trader": "Human", "slot": 5, "t_recv_ms": 11})
            self.assertNotIn("Bot", enricher.early["MintA"])
            self.assertIn("Human", enricher.early["MintA"])
            first = enricher.drain_one()
            self.assertIsNotNone(first)
            assert first is not None
            self.assertEqual(first.wallet, "Creator")
            self.assertEqual(first.funder, "Funder")
            path = graph / "funding-1970-01-01.jsonl"
            self.assertTrue(path.is_file())
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["first_seen_ms"], 5_000)
            again = enricher.drain_one()
            self.assertIsNotNone(again)
            assert again is not None
            self.assertEqual(again.wallet, "Human")


if __name__ == "__main__":
    unittest.main()
