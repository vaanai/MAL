"""LAYA v0 fixture tests. No live tape and no network."""

from __future__ import annotations

import calendar
import json
import random
import tempfile
import unittest
from pathlib import Path

from tools.laya_v0 import (
    FEATURE_NAMES,
    ENTRY_LATENCY_MS,
    PROMOTION_RULE,
    QUOTE_WSOL_LIVE_AT,
    BookTrade,
    DecisionRow,
    FlowPrint,
    LADDER_RULES,
    MintBook,
    Scored,
    _ladder_legs,
    barrier_outcome,
    causal_buyer_triggers,
    decision_times,
    _migrate_predeclared,
    _selection_table,
    book_stats,
    format_markdown,
    attach_labels,
    available_backend,
    build_feature_rows,
    choose_rule,
    evaluate_entry,
    flow_as_of,
    load_books,
    local_features,
    run_files,
    tape_day_tokens,
    walk_forward,
)
from tools.paper_curve_math import (
    DEFAULT_SIZE_LAMPORTS,
    PRIORITY_FEE_LAMPORTS,
    TOKEN_ACCOUNT_RENT_LAMPORTS,
)
from tools.paper_price_path import CreateSignal
from tools.paper_tape_scoreboard import EXIT_RULES, _stuck_loss

T0 = 10_000_000_000
Q0 = 35_000_000_000
B0 = 1_073_000_000_000_000
SIZE = DEFAULT_SIZE_LAMPORTS


def _flow(
    t_ms: int,
    *,
    trader: str | None = "Wallet",
    side: str = "buy",
    sol: int = 1_000_000_000,
    token: int = 1_000_000,
    slot: int = 1,
    quote: int = Q0,
    base: int = B0,
    event_index: int = 0,
    venue: str = "pump_bonding",
) -> FlowPrint:
    price = quote / (base * 1000)
    return FlowPrint(
        t_recv_ms=t_ms,
        slot=slot,
        event_index=event_index,
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


def _book(
    flow: list[FlowPrint],
    mint: str = "MintA",
    t_ms: int = T0,
    creator: str | None = "CreatorA",
    sol_amount: float | None = 5.0,
    mcap: float | None = 32.6,
) -> MintBook:
    ordered = sorted(flow, key=lambda p: (p.t_recv_ms, p.slot, p.event_index, p.trader or ""))
    return MintBook(
        create=CreateSignal(
            mint=mint,
            t_signal_ms=t_ms,
            creator=creator,
            signature="sig-" + mint,
            v_sol=35.0,
            v_token_ui=1_073_000_000.0,
            mcap_sol=mcap,
            initial_buy_ui=1_000_000.0,
            sol_amount=sol_amount,
        ),
        flow=ordered,
    )


def _books(*books: MintBook) -> dict[str, MintBook]:
    return {book.create.mint: book for book in books}


def _same_features(a: dict[str, float], b: dict[str, float]) -> bool:
    keys = set(a) | set(b)
    for key in keys:
        left = a.get(key)
        right = b.get(key)
        if left is None or right is None:
            if left != right:
                return False
            continue
        if left != left and right != right:
            continue
        if left != right:
            return False
    return True


def _by_key(rows: list[DecisionRow]) -> dict[tuple[str, int, str], dict[str, float]]:
    return {(row.mint, row.decision_t_ms, row.trigger): row.features for row in rows}


class NoLookaheadTests(unittest.TestCase):
    def _fixture(self) -> dict[str, MintBook]:
        # Prior mint whose 30s outcome is knowable before MintA's T+15s decision.
        old_flow = [
            _flow(T0 - 60_000, trader="OldBuyer", sol=2_000_000_000, token=2_000_000, slot=1, event_index=1),
            _flow(
                T0 - 30_000,
                trader="OldSeller",
                side="sell",
                sol=500_000_000,
                token=2_000_000,
                slot=4,
                quote=31_000_000_000,
                base=B0,
                event_index=2,
            ),
        ]
        buyers = [
            _flow(T0 + 1_000, trader="W1", sol=2_000_000_000, token=2_000_000, slot=10, event_index=1),
            _flow(T0 + 10_000, trader="W2", sol=1_000_000_000, token=1_000_000, slot=11, event_index=2),
        ]
        # Eight buyers at T+8s, far enough from the 5s and 15s grid to be its own trigger.
        trigger = [
            _flow(T0 + 8_000, trader=f"T{i}", sol=500_000_000, token=500_000, slot=12, event_index=10 + i)
            for i in range(8)
        ]
        future = [
            _flow(T0 + 20_000, trader="FutureA", sol=9_000_000_000, token=9_000_000, slot=20, event_index=30),
            _flow(
                T0 + 40_000,
                trader="FutureB",
                side="sell",
                sol=50_000_000_000,
                token=50_000_000,
                slot=21,
                quote=80_000_000_000,
                base=B0 // 2,
                event_index=31,
            ),
        ]
        later = _book(
            [_flow(T0 + 50_000, trader="Later", sol=3_000_000_000, token=3_000_000, slot=1, event_index=1)],
            mint="MintLater",
            t_ms=T0 + 50_000,
            creator="CreatorA",
        )
        return _books(
            _book(old_flow, mint="MintOld", t_ms=T0 - 60_000, creator="CreatorA"),
            _book(buyers + trigger + future, mint="MintA", t_ms=T0, creator="CreatorA"),
            later,
        )

    def test_shuffle_future_rows_leaves_features_unchanged(self) -> None:
        books = self._fixture()
        tape_end = T0 + 180_000
        cut = T0 + 15_000
        rows = build_feature_rows(books, tape_end_ms=tape_end)[0]
        before = _by_key(rows)

        mutated: dict[str, MintBook] = {}
        rng = random.Random(7)
        for mint, book in books.items():
            past = [pr for pr in book.flow if pr.t_recv_ms <= cut]
            future = [pr for pr in book.flow if pr.t_recv_ms > cut]
            payloads = [
                (pr.trader, pr.side, pr.sol_lamports, pr.token_raw, pr.quote_reserve, pr.base_reserve, pr.slot)
                for pr in future
            ]
            rng.shuffle(payloads)
            rebuilt: list[FlowPrint] = list(past)
            for pr, payload in zip(future, payloads):
                trader, side, sol, token, quote, base, slot = payload
                # Keep the timestamp in the future. Shuffle everything else, then distort it.
                rebuilt.append(
                    _flow(
                        pr.t_recv_ms,
                        trader=("ZZ" + (trader or "anon"))[::-1],
                        side=side,
                        sol=sol * 3 + 123,
                        token=token + 99,
                        slot=slot + 50,
                        quote=quote + 5_000_000_000,
                        base=max(1, base // 3),
                        event_index=pr.event_index + 100,
                        venue=pr.venue,
                    )
                )
            mutated[mint] = _book(rebuilt, mint=mint, t_ms=book.create.t_signal_ms, creator=book.create.creator)
        after = _by_key(build_feature_rows(mutated, tape_end_ms=tape_end)[0])

        checked = 0
        for key, feats in before.items():
            _mint, t_ms, _trigger = key
            if t_ms > cut:
                continue
            self.assertIn(key, after)
            self.assertTrue(_same_features(feats, after[key]), key)
            checked += 1
        self.assertGreaterEqual(checked, 4)
        for name in FEATURE_NAMES:
            self.assertIn(name, next(iter(before.values())))

    def test_future_mutation_is_visible_once_the_row_is_not_future(self) -> None:
        book = _book([_flow(T0 + 1_000, trader="W1", sol=1_000_000_000)])
        t_ms = T0 + 5_000
        base = local_features(book, t_ms, "grid")
        moved = _book(
            [
                _flow(T0 + 1_000, trader="W1", sol=1_000_000_000),
                _flow(t_ms, trader="W2", sol=4_000_000_000),
            ]
        )
        changed = local_features(moved, t_ms, "grid")
        self.assertEqual(base["f_n_buy"], 1.0)
        self.assertEqual(changed["f_n_buy"], 2.0)
        self.assertGreater(changed["f_buy_sol"], base["f_buy_sol"])

    def test_print_at_decision_time_counts_and_the_next_millisecond_does_not(self) -> None:
        t_ms = T0 + 5_000
        book = _book(
            [
                _flow(t_ms, trader="At", sol=1_000_000_000, event_index=1),
                _flow(t_ms + 1, trader="After", sol=7_000_000_000, event_index=2),
            ]
        )
        feats = local_features(book, t_ms, "grid")
        self.assertEqual(feats["f_n_buy"], 1.0)
        self.assertEqual(feats["f_unique_buyers"], 1.0)
        self.assertEqual(flow_as_of(book.flow, t_ms)[-1].trader, "At")
        shuffled = _book(
            [
                _flow(t_ms, trader="At", sol=1_000_000_000, event_index=1),
                _flow(t_ms + 1, trader="Other", sol=1, token=1, slot=99, quote=Q0 + 9_000_000_000, event_index=2),
            ]
        )
        self.assertTrue(_same_features(feats, local_features(shuffled, t_ms, "grid")))

    def test_creator_outcome_ignores_a_later_mint_and_an_unfinished_horizon(self) -> None:
        books = self._fixture()
        rows, _diag = build_feature_rows(books, tape_end_ms=T0 + 180_000)
        early = next(row for row in rows if row.mint == "MintA" and row.decision_t_ms == T0 + 5_000 and row.trigger == "grid")
        self.assertEqual(early.features["f_creator_prior_mints"], 1.0)
        self.assertEqual(early.features["f_creator_prior_scored"], 1.0)
        self.assertGreaterEqual(early.features["f_creator_prior_rug_frac"], 0.0)
        # A mint created at T+50s is not a prior at T+5s. At T+120s it exists, but its
        # 30s horizon (T+80s) has elapsed and WOULD count. T+15s is still before it.
        mid = next(row for row in rows if row.mint == "MintA" and row.decision_t_ms == T0 + 15_000 and row.trigger == "grid")
        self.assertEqual(mid.features["f_creator_prior_mints"], 1.0)

    def test_wallet_flags_do_not_use_trades_after_the_decision(self) -> None:
        early_prints = []
        for mint_i in range(8):
            for buy_i in range(4):
                early_prints.append(
                    (
                        f"BotMint{mint_i}",
                        T0 - 20_000 + mint_i * 100 + buy_i,
                        _flow(
                            T0 - 20_000 + mint_i * 100 + buy_i,
                            trader="BotWallet",
                            sol=1_000_000,
                            token=1_000,
                            slot=1,
                            event_index=buy_i,
                        ),
                    )
                )
        books_list: dict[str, list[FlowPrint]] = {}
        creates: dict[str, int] = {}
        for mint, t_ms, pr in early_prints:
            books_list.setdefault(mint, []).append(pr)
            creates.setdefault(mint, t_ms)
        books_list["MintA"] = [
            _flow(T0 + 1_000, trader="BotWallet", sol=2_000_000_000, token=2_000_000, slot=3, event_index=1),
            _flow(T0 + 30_000, trader="BotWallet", sol=9_000_000_000, token=9_000_000, slot=4, event_index=2),
        ]
        creates["MintA"] = T0
        books = {
            mint: _book(flow, mint=mint, t_ms=creates[mint], creator=None)
            for mint, flow in books_list.items()
        }
        rows, diag = build_feature_rows(books, tape_end_ms=T0 + 60_000, offsets_ms=(5_000,))
        row = next(r for r in rows if r.mint == "MintA")
        self.assertGreater(row.features["f_bot_buy_sol_share"], 0.0)
        self.assertGreater(row.features["f_veto_buy_sol_share"], 0.0)
        # The T+30s buy is after the T+5s decision. Replacing it must not move the share.
        books["MintA"] = _book(
            [
                _flow(T0 + 1_000, trader="BotWallet", sol=2_000_000_000, token=2_000_000, slot=3, event_index=1),
                _flow(T0 + 30_000, trader="SomebodyElse", sol=1, token=1, slot=9, event_index=2),
            ],
            mint="MintA",
            t_ms=T0,
            creator=None,
        )
        rows2, _ = build_feature_rows(books, tape_end_ms=T0 + 60_000, offsets_ms=(5_000,))
        row2 = next(r for r in rows2 if r.mint == "MintA")
        self.assertEqual(row.features["f_bot_buy_sol_share"], row2.features["f_bot_buy_sol_share"])
        self.assertGreater(diag["bots_end"], 0)

    def test_leader_flag_uses_only_round_trips_already_closed(self) -> None:
        def trip(mint: str, t_buy: int, sell_sol: int) -> MintBook:
            return _book(
                [
                    _flow(t_buy, trader="Leader", sol=20_000_000, token=1_000_000, slot=5, event_index=1),
                    _flow(
                        t_buy + 20_000,
                        trader="Leader",
                        side="sell",
                        sol=sell_sol,
                        token=1_000_000,
                        slot=8,
                        event_index=2,
                    ),
                ],
                mint=mint,
                t_ms=t_buy,
                creator=None,
            )

        closed = [
            trip("L1", T0 - 120_000, 40_000_000),
            trip("L2", T0 - 90_000, 40_000_000),
            trip("L3", T0 - 60_000, 10_000_000),
        ]
        live = _book(
            [_flow(T0 + 1_000, trader="Leader", sol=5_000_000, token=100_000, slot=3, event_index=1)],
            mint="MintA",
            t_ms=T0,
            creator=None,
        )
        rows, diag = build_feature_rows(_books(*closed, live), tape_end_ms=T0 + 30_000, offsets_ms=(5_000,))
        row = next(r for r in rows if r.mint == "MintA")
        self.assertEqual(row.features["f_leader_present"], 1.0)
        self.assertEqual(row.features["f_sig_copyable"], 0.0)
        self.assertEqual(row.features["f_sig_delta_slot_from_create"], 0.0)
        self.assertAlmostEqual(row.features["f_sig_wallet_win_rate"], 2.0 / 3.0)
        self.assertEqual(row.features["f_sig_wallet_median_hold_ms"], 20_000.0)
        self.assertGreaterEqual(diag["leaders_peak"], 1)
        # Same trips, but they close after the decision. Not a leader yet.
        late = [
            trip("L1", T0 + 10_000, 40_000_000),
            trip("L2", T0 + 40_000, 40_000_000),
            trip("L3", T0 + 70_000, 10_000_000),
        ]
        rows_late, _ = build_feature_rows(_books(*late, live), tape_end_ms=T0 + 120_000, offsets_ms=(5_000,))
        row_late = next(r for r in rows_late if r.mint == "MintA")
        self.assertEqual(row_late.features["f_leader_present"], 0.0)

    def test_exit_tick_features_ignore_prints_after_the_tick(self) -> None:
        calm = _flow(T0 + 1_000, trader="W", sol=1_000_000_000)
        later = _flow(T0 + 20_000, trader="Later", sol=8_000_000_000, token=8_000_000, quote=90_000_000_000, base=B0 // 2)
        books = _books(_book([calm, later]))
        rows, _ = build_feature_rows(books, tape_end_ms=T0 + 180_000, offsets_ms=(5_000,))
        ticks = attach_labels(books, rows, tape_end_ms=T0 + 180_000)
        early = [tick for tick in ticks if tick.tick_t_ms < T0 + 15_000]
        self.assertTrue(early)
        distorted = _flow(
            T0 + 20_000,
            trader="Other",
            sol=1,
            token=1,
            slot=40,
            quote=30_000_000_000,
            base=B0,
            event_index=9,
        )
        books2 = _books(_book([calm, distorted]))
        rows2, _ = build_feature_rows(books2, tape_end_ms=T0 + 180_000, offsets_ms=(5_000,))
        ticks2 = attach_labels(books2, rows2, tape_end_ms=T0 + 180_000)
        early2 = [tick for tick in ticks2 if tick.tick_t_ms < T0 + 15_000]
        self.assertEqual(len(early), len(early2))
        for left, right in zip(early, early2):
            self.assertTrue(_same_features(left.features, right.features))

    def test_migration_decision_ignores_later_pool_prints(self) -> None:
        bond = _flow(T0 + 1_000, trader="W", venue="pump_bonding", slot=1)
        first_pool = _flow(
            T0 + 12_000,
            trader="Pool",
            venue="pumpswap",
            slot=20,
            quote=80_000_000_000,
            base=B0 // 2,
            sol=2_000_000_000,
        )
        later_pool = _flow(
            T0 + 40_000,
            trader="Later",
            venue="pumpswap",
            slot=30,
            quote=10_000_000_000,
            base=B0,
            sol=50_000_000_000,
            event_index=4,
        )
        books = _books(_book([bond, first_pool, later_pool]))
        rows, _diag = build_feature_rows(books, tape_end_ms=T0 + 180_000, offsets_ms=(5_000,))
        grid = next(row for row in rows if row.trigger == "grid" and row.decision_t_ms == T0 + 5_000)
        self.assertEqual(grid.features["f_migrated"], 0.0)
        self.assertLess(grid.features["f_curve_progress"], 1.0)
        mig = next(row for row in rows if row.trigger == "migrate")
        self.assertEqual(mig.decision_t_ms, T0 + 12_000)
        self.assertEqual(mig.features["f_migrated"], 1.0)
        self.assertEqual(mig.features["f_curve_progress"], 1.0)
        self.assertEqual(mig.features["f_ms_from_create"], 12_000.0)
        distorted = _flow(
            T0 + 40_000,
            trader="Other",
            venue="pumpswap",
            slot=90,
            quote=200_000_000_000,
            base=B0 // 5,
            sol=1,
            token=1,
            event_index=9,
        )
        rows2, _ = build_feature_rows(
            _books(_book([bond, first_pool, distorted])),
            tape_end_ms=T0 + 180_000,
            offsets_ms=(5_000,),
        )
        mig2 = next(row for row in rows2 if row.trigger == "migrate")
        self.assertTrue(_same_features(mig.features, mig2.features))


class LabelTests(unittest.TestCase):
    def test_rule_is_chosen_on_the_training_rows_only(self) -> None:
        def row(t_ms: int, hold_30: int, hold_5m: int) -> DecisionRow:
            pnl = {rule.rule_id: hold_30 for rule in EXIT_RULES}
            pnl["hold_30s"] = hold_30
            pnl["hold_5m"] = hold_5m
            return DecisionRow(
                mint=f"M{t_ms}",
                creator=None,
                create_t_ms=t_ms,
                decision_t_ms=t_ms,
                trigger="grid",
                features={"f_unique_buyers": 1.0},
                pnl_by_rule=pnl,
            )

        train = [row(i, 1_000 if i % 4 else -100, -5_000 if i % 4 else 1_000) for i in range(40)]
        self.assertEqual(choose_rule(train, min_n=10), "hold_30s")
        test = [row(100 + i, -100, 5_000) for i in range(40)]
        # The test slice would rather hold 5 minutes. That must not pick the label.
        self.assertEqual(choose_rule(test, min_n=10), "hold_5m")
        scored = evaluate_entry(train + test, n_folds=4, min_rule_n=10, backend="lightgbm")
        fold0 = [s for s in scored["scored"] if s.fold == 0]
        self.assertTrue(fold0)
        self.assertTrue(all(s.rule_id == "hold_30s" for s in fold0))
        self.assertTrue(all(s.pnl == -100 for s in fold0))

    def test_migrate_exit_stays_the_predeclared_stop(self) -> None:
        rows: list[DecisionRow] = []
        for i in range(16):
            trigger = "migrate" if i >= 12 else "grid"
            pnl = {rule.rule_id: -100 for rule in EXIT_RULES}
            pnl["tp50_sl30"] = 5_000 if trigger == "migrate" else -100
            pnl["hold_30s"] = -100
            rows.append(
                DecisionRow(
                    mint=f"M{i}",
                    creator=None,
                    create_t_ms=i * 1_000,
                    decision_t_ms=i * 1_000,
                    trigger=trigger,
                    features={},
                    pnl_by_rule=pnl,
                )
            )
        book = _migrate_predeclared(rows, n_folds=4)
        self.assertGreater(book["oos_tp50_sl30"]["n"], 0)
        self.assertGreater(book["oos_tp50_sl30"]["median_sol"], 0)
        self.assertLess(book["oos_hold_30s"]["median_sol"], 0)

    def test_walk_forward_test_is_strictly_later(self) -> None:
        times = [1_000 * i for i in range(50)]
        times[10] = times[9]
        folds = walk_forward(times, n_folds=4)
        self.assertGreaterEqual(len(folds), 2)
        for train, test in folds:
            self.assertLess(max(times[i] for i in train), min(times[i] for i in test))
            self.assertTrue(set(train).isdisjoint(test))

    def test_flat_book_loses_and_a_rug_stays_in_the_label(self) -> None:
        flat = _book([_flow(T0 + 1_000, trader="W", sol=1_000_000_000)])
        rug = _book(
            [
                _flow(T0 + 1_000, trader="W", sol=1_000_000_000),
                _flow(T0 + 10_000, trader="Rug", side="sell", sol=1_000, token=1, quote=30_000_000_000, base=B0),
            ],
            mint="MintRug",
        )
        books = _books(flat, rug)
        rows, _ = build_feature_rows(books, tape_end_ms=T0 + 180_000, offsets_ms=(5_000,))
        attach_labels(books, rows, tape_end_ms=T0 + 180_000)
        flat_row = next(r for r in rows if r.mint == "MintA")
        rug_row = next(r for r in rows if r.mint == "MintRug")
        self.assertEqual(flat_row.entry_t_ms, flat_row.decision_t_ms + ENTRY_LATENCY_MS)
        self.assertEqual(flat_row.entry_status, "filled")
        self.assertLess(flat_row.pnl_by_rule["hold_30s"], -2 * PRIORITY_FEE_LAMPORTS)
        self.assertEqual(rug_row.exit_status_by_rule["hold_30s"], "no_exit_liquidity")
        self.assertEqual(rug_row.pnl_by_rule["hold_30s"], _stuck_loss(SIZE))
        self.assertEqual(
            rug_row.pnl_by_rule["hold_30s"],
            -(SIZE + 2 * PRIORITY_FEE_LAMPORTS + TOKEN_ACCOUNT_RENT_LAMPORTS),
        )
        # The rug print is after the decision, so the packet still shows the calm book.
        self.assertEqual(rug_row.features["f_n_sell"], 0.0)

    def test_topk_keeps_rug_losses(self) -> None:
        scored = [
            Scored(0, "5", 0.99, _stuck_loss(SIZE), "hold_30s", "a", 1, 0),
            Scored(0, "5", 0.10, 1_000, "hold_30s", "b", 2, 1),
        ]
        table = _selection_table(scored)
        top = table[0]["top"][0]
        self.assertEqual(top["n"], 1)
        self.assertLess(top["total_sol"], 0)
        self.assertAlmostEqual(top["total_sol"], _stuck_loss(SIZE) / 1_000_000_000)


class ModelTests(unittest.TestCase):
    def _rows(self, n: int = 80) -> list[DecisionRow]:
        rows = []
        for i in range(n):
            win = i % 2 == 0
            feats = {name: 0.0 for name in FEATURE_NAMES}
            feats["f_unique_buyers"] = 10.0 if win else 0.0
            feats["f_offset_s"] = 5.0
            rows.append(
                DecisionRow(
                    mint=f"M{i}",
                    creator=None,
                    create_t_ms=i * 1_000,
                    decision_t_ms=i * 1_000 + 5_000,
                    trigger="grid",
                    features=feats,
                    pnl_by_rule={rule.rule_id: (2_000_000 if win else -1_000_000) for rule in EXIT_RULES},
                )
            )
        return rows

    def test_out_of_sample_top_slice_beats_buying_all(self) -> None:
        scored = evaluate_entry(self._rows(), n_folds=4, min_rule_n=5, backend="lightgbm")
        self.assertGreater(scored["oos_n"], 0)
        self.assertEqual(scored["scored"][0].__class__, Scored)
        point = next(row for row in scored["by_point"] if row["point"] == "5")
        base = point["baseline"]["win_rate"]
        top = next(take for take in point["top"] if take["fraction"] == 0.20)
        self.assertGreater(top["win_rate"], base)
        self.assertGreater(top["median_sol"], point["baseline"]["median_sol"])
        self.assertTrue(scored["importance"])
        self.assertEqual(scored["importance"][0]["name"], "f_unique_buyers")

    def test_sklearn_backend_fits_when_asked(self) -> None:
        self.assertIn(available_backend("sklearn"), ("sklearn",))
        scored = evaluate_entry(self._rows(40), n_folds=2, min_rule_n=4, backend="sklearn")
        self.assertGreater(scored["oos_n"], 0)
        self.assertTrue(any(fold.get("model") == "sklearn" for fold in scored["folds"]))


SOL = 1_000_000_000
DAY = calendar.timegm((2026, 9, 25, 12, 0, 0, 0, 0, 0)) * 1000


def _bt(mint: str, pnl_sol: float, t_ms: int = DAY) -> BookTrade:
    return BookTrade(mint, t_ms, int(round(pnl_sol * SOL)))


class RobustBookTests(unittest.TestCase):
    def test_token_bootstrap_cannot_split_a_mint(self) -> None:
        # Mint A is +1 and -1. Mint B is +0.5. Resampling whole tokens, the total cannot exceed 1.
        stats = book_stats([_bt("A", 1.0, DAY), _bt("A", -1.0, DAY + 1), _bt("B", 0.5, DAY + 2)])
        self.assertIsNotNone(stats["total_ci90_sol"])
        self.assertLessEqual(stats["total_ci90_sol"][1], 1.0 + 1e-9)
        self.assertGreaterEqual(stats["total_ci90_sol"][0], -1e-9)
        again = book_stats([_bt("A", 1.0, DAY), _bt("A", -1.0, DAY + 1), _bt("B", 0.5, DAY + 2)])
        self.assertEqual(stats["total_ci90_sol"], again["total_ci90_sol"])
        self.assertEqual(stats["mean_ci90_sol"], again["mean_ci90_sol"])

    def test_winsorized_mean_caps_a_single_outlier(self) -> None:
        trades = [_bt(f"m{i}", 0.0, DAY + i) for i in range(99)]
        trades.append(_bt("out", 1000.0, DAY + 99))
        stats = book_stats(trades)
        self.assertAlmostEqual(stats["mean_sol"], 10.0, places=6)
        self.assertAlmostEqual(stats["winsorized_mean_sol"], 0.1, places=6)
        self.assertLess(stats["winsorized_mean_sol"], stats["mean_sol"])

    def test_ex_best_drops_one_copy_of_the_max(self) -> None:
        stats = book_stats([_bt("a", 1.0), _bt("b", 1.0, DAY + 1), _bt("c", -3.0, DAY + 2)])
        self.assertAlmostEqual(stats["total_ex_best_sol"], -2.0, places=6)
        self.assertIsNone(book_stats([_bt("only", 1.0)])["total_ex_best_sol"])

    def test_max_drawdown_from_a_zero_peak(self) -> None:
        stats = book_stats([_bt("a", 1.0, DAY), _bt("b", -3.0, DAY + 1), _bt("c", 2.0, DAY + 2)])
        self.assertAlmostEqual(stats["max_drawdown_sol"], 3.0, places=6)

    def test_promotion_needs_all_three_clauses(self) -> None:
        steady = [_bt(f"m{i}", 0.001, DAY) for i in range(40)]
        good = book_stats(steady)
        self.assertGreater(good["mean_ci90_sol"][0], 0)
        self.assertGreater(good["total_ex_best_sol"], 0)
        self.assertTrue(good["majority_days_positive"])
        self.assertTrue(good["promote"])

        # Every token mean is positive, so the mean CI stays above 0, but the book
        # without its best trade is negative.
        carried = [_bt("A", 1.0, DAY), _bt("A", -0.4, DAY + 1)]
        carried.extend(_bt(f"s{i}", 0.01, DAY + 2 + i) for i in range(10))
        bad = book_stats(carried)
        self.assertGreater(bad["mean_ci90_sol"][0], 0)
        self.assertLess(bad["total_ex_best_sol"], 0)
        self.assertTrue(bad["majority_days_positive"])
        self.assertFalse(bad["promote"])

        day2 = DAY + 86_400_000
        split = [_bt(f"p{i}", 0.1, DAY) for i in range(3)]
        split.extend(_bt(f"n{i}", -0.2, day2) for i in range(3))
        days = book_stats(split)
        self.assertEqual(days["days_positive"], 1)
        self.assertEqual(days["n_days"], 2)
        self.assertFalse(days["majority_days_positive"])
        self.assertFalse(days["promote"])
        self.assertEqual([row["day"] for row in days["days"]], ["2026-09-25", "2026-09-26"])

    def test_scoreboard_states_the_rule_and_the_quote_fix(self) -> None:
        board = {
            "schema": "laya_v0",
            "entry_latency_ms": 1000,
            "data_needed": "note",
            "creates": 0,
            "decisions": 0,
            "scan": {"lines": 0, "kept": 0},
            "entry": {
                "oos_n": 0,
                "deploy": {"backend": None, "predict_latency": None},
                "folds": [],
                "by_point": [],
                "thresholds": [],
                "importance": [],
                "migrate_predeclared": {},
                "exit": {
                    "positions": 0,
                    "early_exits": 0,
                    "backend": None,
                    "policy": {"n": 0, "total_sol": 0.0},
                    "rule_hold": {"n": 0, "total_sol": 0.0},
                },
            },
            "wallet": {
                "wallets": 0,
                "bots_end": 0,
                "sniper_wallets_end": 0,
                "leaders_end": 0,
                "leaders_peak": 0,
            },
        }
        text = format_markdown(board)
        self.assertIn(PROMOTION_RULE, text)
        self.assertIn(QUOTE_WSOL_LIVE_AT, text)


class EventAndBarrierTests(unittest.TestCase):
    def test_curve_level_uses_the_crossing_print_only(self) -> None:
        from tools.paper_curve_math import INITIAL_REAL_TOKEN_UI, TOKEN_RAW_OFFSET, TOKEN_SCALE

        initial = INITIAL_REAL_TOKEN_UI * TOKEN_SCALE
        base_20 = initial * 75 // 100 + TOKEN_RAW_OFFSET
        base_50 = initial * 50 // 100 + TOKEN_RAW_OFFSET
        cross = _flow(T0 + 3_000, trader="Cross", base=base_20, event_index=1)
        later = _flow(T0 + 9_000, trader="Later", base=base_50, sol=9_000_000_000, event_index=2)
        book = _book([cross, later])
        marks = {trigger: t_ms for t_ms, trigger in decision_times(book, tape_end_ms=T0 + 180_000, offsets_ms=(5_000,))}
        self.assertEqual(marks["curve_20"], T0 + 3_000)
        self.assertEqual(marks["curve_40"], T0 + 9_000)
        self.assertNotIn("curve_60", marks)
        rows, _ = build_feature_rows(_books(book), tape_end_ms=T0 + 180_000, offsets_ms=(5_000,))
        row = next(r for r in rows if r.trigger == "curve_20")
        self.assertEqual(row.features["f_n_buy"], 1.0)
        self.assertEqual(row.features["f_unique_buyers"], 1.0)
        moved = _book(
            [
                cross,
                _flow(T0 + 9_000, trader="Other", base=base_50 // 2, sol=1, event_index=2),
            ]
        )
        rows2, _ = build_feature_rows(_books(moved), tape_end_ms=T0 + 180_000, offsets_ms=(5_000,))
        row2 = next(r for r in rows2 if r.trigger == "curve_20")
        self.assertEqual(row.features["f_n_buy"], row2.features["f_n_buy"])
        self.assertEqual(row.features["f_curve_progress"], row2.features["f_curve_progress"])

    def test_clean_buyer_count_skips_the_creator(self) -> None:
        flow = [_flow(T0 + 1_000, trader="CreatorA", event_index=1)]
        flow.extend(
            _flow(T0 + 1_000 + i, trader=f"B{i}", event_index=2 + i) for i in range(5)
        )
        book = _book(flow, creator="CreatorA")
        marks = causal_buyer_triggers(_books(book), tape_end_ms=T0 + 180_000, ns=(5, 10))
        got = {trigger: t_ms for t_ms, trigger in marks["MintA"]}
        self.assertEqual(got["buyers_nv5"], T0 + 1_000 + 4)
        self.assertNotIn("buyers_nv10", got)
        # The creator print alone is not a trigger.
        only = _book([_flow(T0 + 1_000, trader="CreatorA")], creator="CreatorA")
        self.assertEqual(causal_buyer_triggers(_books(only), tape_end_ms=T0 + 180_000, ns=(1,)), {})

    def test_barrier_is_up_before_down_inside_the_horizon(self) -> None:
        from tools.laya_v0 import BARRIER_HORIZON_MS as horizon
        from tools.paper_price_path import TapePrint

        entry_t = T0 + 1_000
        spot = 1.0
        up = TapePrint(entry_t + 5_000, 1, 1, "pump_bonding", "buy", 1, Q0, B0, 2.0, 2.0)
        down_first = TapePrint(entry_t + 4_000, 1, 1, "pump_bonding", "sell", 1, Q0, B0, 0.6, 0.6)
        late = TapePrint(entry_t + horizon + 1_000, 1, 1, "pump_bonding", "buy", 1, Q0, B0, 3.0, 3.0)

        self.assertEqual(
            barrier_outcome([up], entry_t_ms=entry_t, entry_spot=spot, tp=1.0, sl=0.30, horizon_ms=horizon, tape_end_ms=entry_t + horizon),
            1,
        )
        self.assertEqual(
            barrier_outcome(
                [down_first, up],
                entry_t_ms=entry_t,
                entry_spot=spot,
                tp=1.0,
                sl=0.30,
                horizon_ms=horizon,
                tape_end_ms=entry_t + horizon,
            ),
            0,
        )
        self.assertEqual(
            barrier_outcome([late], entry_t_ms=entry_t, entry_spot=spot, tp=1.0, sl=0.30, horizon_ms=horizon, tape_end_ms=entry_t + horizon),
            0,
        )
        self.assertIsNone(
            barrier_outcome([late], entry_t_ms=entry_t, entry_spot=spot, tp=1.0, sl=0.30, horizon_ms=horizon, tape_end_ms=entry_t + 10_000)
        )

    def test_ladder_scales_out_then_trails_the_rest(self) -> None:
        from tools.paper_price_path import TapePrint as TP

        entry_t = 1_000
        rule = next(r for r in LADDER_RULES if r.rule_id == "ladder_2x_t30")
        up = TP(entry_t + 2_000, 1, 1, "pump_bonding", "buy", 1, Q0, B0, 2.0, 2.0)
        trail = TP(entry_t + 3_000, 1, 2, "pump_bonding", "sell", 1, Q0, B0, 1.3, 1.3)
        legs = _ladder_legs([up, trail], entry_t_ms=entry_t, entry_spot=1.0, tokens=1000, rule=rule)
        self.assertEqual(legs, [(entry_t + 2_000, 500), (entry_t + 3_000, 500)])
        stop = TP(entry_t + 2_000, 1, 1, "pump_bonding", "sell", 1, Q0, B0, 0.7, 0.7)
        stopped = _ladder_legs([stop], entry_t_ms=entry_t, entry_spot=1.0, tokens=1000, rule=rule)
        self.assertEqual(stopped, [(entry_t + 2_000, 1000)])


class FileTests(unittest.TestCase):
    def test_loader_keeps_the_trader_and_refuses_the_tape_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_path = root / "observe-2026-09-25.jsonl"
            tape_path = root / "trades-2026-09-25.jsonl"
            t_ms = 1_758_000_000_000
            create_path.write_text(
                json.dumps(
                    {
                        "stream": "subscribeNewToken",
                        "txType": "create",
                        "mint": "MintA",
                        "t_ws": t_ms,
                        "traderPublicKey": "CreatorA",
                        "vSolInBondingCurve": 35,
                        "vTokensInBondingCurve": 1_073_000_000,
                        "marketCapSol": 32.6,
                        "solAmount": 5,
                        "initialBuy": 1_000_000,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            price = Q0 / (B0 * 1000)
            tape_path.write_text(
                json.dumps(
                    {
                        "type": "trade",
                        "mint": "MintA",
                        "venue": "pump_bonding",
                        "t_recv_ms": t_ms + 1_000,
                        "quote_reserve": Q0,
                        "base_reserve": B0,
                        "side": "buy",
                        "sol_lamports": 1_000_000_000,
                        "token_raw": 2_000_000,
                        "trader": "WalletA",
                        "slot": 10,
                        "event_index": 1,
                        "price_sol": price,
                        "market_cap_sol": price * 1_000_000_000,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            from tools.paper_price_path import load_creates

            books, stats = load_books(load_creates([create_path]), [tape_path])
            self.assertEqual(stats.kept, 1)
            self.assertEqual(books["MintA"].flow[0].trader, "WalletA")
            days = tape_day_tokens(
                [
                    Path("trades-2026-09-25.jsonl.zst"),
                    Path("trades-2026-09-25T08.jsonl"),
                    Path("pool-mints-2026-09-25.jsonl"),
                ]
            )
            self.assertEqual(days, {"2026-09-25"})
            sealed = root / "sealed" / "trades"
            sealed.mkdir(parents=True)
            with self.assertRaises(SystemExit):
                run_files(
                    tape=[tape_path],
                    creates=[create_path],
                    output_dir=sealed,
                    n_folds=2,
                    min_rule_n=1,
                    backend="lightgbm",
                    offsets_ms=(5_000,),
                    size_lamports=SIZE,
                    slippage_cap=0.15,
                )


if __name__ == "__main__":
    unittest.main()
