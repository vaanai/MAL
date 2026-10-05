"""Forward paper: packet parity, fill reconcile, risk gate. No live tape."""

from __future__ import annotations

import json
import math
import tempfile
import time
import unittest
from pathlib import Path

from tools.forward_paper import (
    CHAIN_SAMPLE_CAP,
    DEFAULT_FAIL_RATE,
    GC_FREEZE_INTERVAL_S,
    GC_THRESHOLD,
    HARD_MAX_POSITION_LAMPORTS,
    LATENCY_SAMPLE_CAP,
    PREBOOT_DEAD_MARGIN_MS,
    PRUNE_AFTER_MS,
    READ_CHUNK_BYTES,
    SCHEMA_DECISION,
    STALE_ACTION_MS,
    SWING_FREEZE_AT,
    SWING_FREEZE_MS,
    TX_ORDER_PRUNE_MS,
    VOID_FROM_MS,
    BookSpec,
    DirectoryTail,
    ForwardEngine,
    GcStats,
    LatencyMeter,
    LIVE_IDLE_RETAIN_MS,
    MemCensus,
    ModelSlot,
    _Follower,
    _RankWindow,
    _event_ts,
    CEILING_DAILY_LOSS_LAMPORTS,
    CEILING_MAX_CONCURRENT,
    RiskConfigError,
    books_from_config,
    build_shadow_state,
    clean_clock,
    decision_counts_for_promotion,
    flow_from_tape_row,
    install_gc_mitigation,
    maybe_gc_freeze,
    migrate_fee_sensitivity_summary,
    offline_packets,
    _preboot_dead_mints,
    _safe_preboot_dead_mints,
    promotion_pnls_by_book,
    reload_risk_config,
    reconcile_baseline,
    replay_rows,
    splice_state,
    window_creates,
    write_mem_census,
)
from tools.paper_curve_math import PORTAL_FEE_PPM, PRIORITY_FEE_LAMPORTS
from tools.paper_tape_scoreboard import priority_grid, priority_sides_for_event
from tools.laya_v0 import LADDER_RULES
from tools.laya_v0 import FEATURE_NAMES
from tools.laya_v0 import _dedupe_key
from tools.paper_price_path import CreateSignal, _ZstdText

T0 = 1_700_000_000_250
Q0 = 35_000_000_000
B0 = 1_073_000_000_000_000
OFFSETS = (5_000, 15_000)
TAPE_END = T0 + 45_000


def _create(mint: str, t_ms: int, creator: str = "CreatorA") -> CreateSignal:
    return CreateSignal(
        mint=mint,
        t_signal_ms=t_ms,
        creator=creator,
        signature="sig-" + mint,
        v_sol=35.0,
        v_token_ui=1_073_000_000.0,
        mcap_sol=32.6,
        initial_buy_ui=1_000_000.0,
        sol_amount=1.5,
    )


def _trade(
    mint: str,
    t_ms: int,
    *,
    side: str = "buy",
    sol: int = 1_000_000_000,
    token: int = 1_000_000,
    quote: int = Q0,
    base: int = B0,
    slot: int = 3,
    event_index: int = 1,
    trader: str = "Wallet",
    venue: str = "pump_bonding",
    event_ts: bool = True,
) -> dict[str, object]:
    price = quote / (base * 1000)
    row: dict[str, object] = {
        "type": "trade",
        "mint": mint,
        "venue": venue,
        "quote_is_wsol": True,
        "t_recv_ms": t_ms,
        "quote_reserve": quote,
        "base_reserve": base,
        "price_sol": price,
        "market_cap_sol": price * 1_000_000_000,
        "side": side,
        "sol_lamports": sol,
        "token_raw": token,
        "slot": slot,
        "event_index": event_index,
        "trader": trader,
    }
    if event_ts:
        row["event_ts"] = t_ms // 1000
    return row


class _FakeBooster:
    def predict_proba(self, rows):
        return [[0.2, 0.8] for _ in rows]


def _same(left: dict[str, float], right: dict[str, float]) -> bool:
    keys = set(left) | set(right)
    for key in keys:
        a = left.get(key)
        b = right.get(key)
        if a is None or b is None:
            if a != b:
                return False
            continue
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            continue
        if a != b:
            return False
    return True


def _fixture() -> tuple[dict[str, CreateSignal], list[dict[str, object]]]:
    creates = {
        "MintOld": _create("MintOld", T0 - 60_000),
        "MintA": _create("MintA", T0),
    }
    rows = [
        _trade("MintOld", T0 - 70_000, trader="Early", sol=1_000_000_000, token=1_000_000, slot=1, event_index=0),
        _trade("MintOld", T0 - 50_000, trader="OldBuyer", sol=2_000_000_000, token=2_000_000, slot=1, event_index=1),
        _trade(
            "MintOld",
            T0 - 30_000,
            trader="OldSeller",
            side="sell",
            sol=200_000_000,
            token=2_000_000,
            quote=20_000_000_000,
            slot=2,
            event_index=2,
        ),
        _trade("MintA", T0 + 1_500, trader="W1", sol=1_000_000_000, token=1_000_000, quote=36_000_000_000, slot=4, event_index=1),
        _trade(
            "MintA",
            T0 + 400,
            trader="Probe",
            sol=5_000_000_000,
            token=1_000_000,
            quote=80_000_000_000,
            slot=5,
            event_index=2,
            event_ts=False,
        ),
        _trade("MintA", T0 + 2_000, trader="W2", sol=1_500_000_000, token=1_200_000, quote=36_000_000_000, slot=6, event_index=3),
        _trade(
            "MintA",
            T0 + 12_000,
            trader="Pool",
            venue="pumpswap",
            sol=2_000_000_000,
            token=5_000_000,
            quote=70_000_000_000,
            base=B0 // 2,
            slot=20,
            event_index=4,
        ),
        _trade("MintA", T0 + 16_000, trader="Later", sol=9_000_000_000, token=9_000_000, quote=90_000_000_000, slot=21, event_index=5),
        _trade("Other", T0 + 1_000, trader="Nope"),
        {"type": "trade", "mint": "MintA", "venue": "pumpswap", "quote_is_wsol": False, "t_recv_ms": T0 + 1_500, "quote_reserve": 1, "base_reserve": 1},
    ]
    return creates, rows


def _books() -> list[BookSpec]:
    return [
        BookSpec("buy_all", "baseline", "hold_30s", max_concurrent=None, daily_loss_lamports=None, creator_cooldown_ms=0, token_cooldown_ms=0),
        BookSpec("laya_0.6", "laya", "deploy", threshold=0.6),
        BookSpec("migrate_tp50_sl30", "migrate", "tp50_sl30"),
    ]


class ParityTests(unittest.TestCase):
    def test_online_packets_match_the_offline_builder(self) -> None:
        creates, rows = _fixture()
        engine = replay_rows(
            creates.values(),
            rows,
            _books(),
            tape_end_ms=TAPE_END,
            kill_file=Path("/tmp/forward-paper-kill-absent"),
            offsets_ms=OFFSETS,
            record_packets=True,
        )
        offline = offline_packets(creates, rows, tape_end_ms=TAPE_END, offsets_ms=OFFSETS)
        online = {(mint, t_ms, trigger): feats for mint, t_ms, trigger, feats in engine.packets}
        self.assertEqual(set(online), set(offline))
        self.assertGreaterEqual(len(online), 4)
        for key in offline:
            self.assertTrue(_same(online[key], offline[key]), key)
            for name in FEATURE_NAMES:
                self.assertIn(name, online[key])

    def test_baseline_fill_matches_same_latency_and_not_the_one_second_seed(self) -> None:
        creates, rows = _fixture()
        engine = replay_rows(
            creates.values(),
            rows,
            _books(),
            tape_end_ms=TAPE_END,
            kill_file=Path("/tmp/forward-paper-kill-absent-2"),
            offsets_ms=OFFSETS,
        )
        opens = [row for row in engine.positions if row["book"] == "buy_all" and row["event"] == "open"]
        mina = [row for row in opens if row["mint"] == "MintA"]
        self.assertTrue(mina)
        self.assertEqual(mina[0]["applied_latency_ms"], 250)
        self.assertNotEqual(mina[0]["applied_latency_ms"], 1000)
        report = reconcile_baseline(engine, creates, rows, tape_end_ms=TAPE_END, book_id="buy_all")
        same = report["online_vs_same_latency"]
        self.assertEqual(same["pnl_mismatches"], 0)
        self.assertGreater(same["online"]["n"], 0)
        gap = report["online_vs_constant_1s"]
        self.assertNotEqual(gap["total_sol_gap"], 0.0)
        self.assertIsNotNone(engine.latency.report()["chain_to_recv"]["p50_ms"])
        kept = window_creates(creates, T0, TAPE_END)
        self.assertIn("MintA", kept)
        self.assertNotIn("MintOld", kept)


class WarmStartTests(unittest.TestCase):
    """build_shadow_state()/splice_state(): a restart's warm start should not
    change what the engine decides after it, versus one uninterrupted run.
    """

    def _feed_second_half(self, engine, creates, rows, mid_ms: int) -> None:
        for create in creates.values():
            if mid_ms < create.t_signal_ms <= TAPE_END:
                engine.push_create(create)
        for row in rows:
            parsed = flow_from_tape_row(row)
            if parsed is None:
                continue
            mint, pr = parsed
            if pr.t_recv_ms <= mid_ms or pr.t_recv_ms > TAPE_END:
                continue
            engine.push_print(mint, pr, _event_ts(row))
        engine.drain_until(TAPE_END, final=True)

    def test_shadow_emits_no_decisions_or_positions(self) -> None:
        creates, rows = _fixture()
        mid_ms = T0 + 6_000
        shadow = build_shadow_state(creates.values(), rows, _books(), until_ms=mid_ms, offsets_ms=OFFSETS)
        self.assertEqual(shadow.decisions, [])
        self.assertEqual(shadow.positions, [])
        self.assertEqual(shadow.logs, {})

    def test_splice_reproduces_the_second_half_of_an_uninterrupted_replay(self) -> None:
        creates, rows = _fixture()
        books = _books()
        mid_ms = T0 + 6_000
        kill_file = Path("/tmp/forward-paper-warm-start-kill-absent")

        # A: one continuous replay across the whole fixture, no restart.
        engine_a = replay_rows(
            creates.values(),
            rows,
            books,
            tape_end_ms=TAPE_END,
            kill_file=kill_file,
            offsets_ms=OFFSETS,
        )
        a_decisions = [row for row in engine_a.decisions if row["decision_t_ms"] > mid_ms]
        a_positions = [row for row in engine_a.positions if row["decision_t_ms"] > mid_ms]
        # decision_t_ms is always the *open* decision's time, including on a
        # "close" row (see `_fill_one`/`_try_exit`), so this filter also
        # correctly drops any position opened before mid_ms even if it
        # closes after it -- engine B has no ledger memory of that open
        # (books/ledgers/positions are not spliced; see splice_state's
        # docstring), so it could never reproduce that close either.
        self.assertTrue(a_decisions, "fixture should produce at least one post-split decision")

        # B: shadow-replay the first half only, splice its cross-mint state
        # onto a fresh engine, then feed the second half.
        shadow = build_shadow_state(creates.values(), rows, books, until_ms=mid_ms, offsets_ms=OFFSETS)
        engine_b = ForwardEngine(
            books,
            kill_file=kill_file,
            latency=LatencyMeter(),
            offsets_ms=OFFSETS,
            tape_end_ms=TAPE_END,
            retain_rows=True,
        )
        splice_state(shadow, engine_b)
        # Sanity: the spliced containers hold the mints registered before the
        # split (MintOld's create is before T0 - 60_000 < mid_ms; MintA's is
        # at T0 < mid_ms), so B did not need to re-see them post-splice.
        self.assertIn("MintA", engine_b.library)
        self.assertIn("MintOld", engine_b.library)
        self.assertTrue(engine_b.grids, "second grid (T0+15_000) should still be pending post-splice")

        self._feed_second_half(engine_b, creates, rows, mid_ms)

        b_decisions = [row for row in engine_b.decisions if row["decision_t_ms"] > mid_ms]
        b_positions = [row for row in engine_b.positions if row["decision_t_ms"] > mid_ms]
        self.assertEqual(a_decisions, b_decisions)
        self.assertEqual(a_positions, b_positions)

    def test_prune_keeps_a_still_open_mint_busy_inside_the_shadow(self) -> None:
        """Regression for a real bug: an earlier `build_shadow_state` ran only
        the `swing`/`mig_15` subset of `books` inside the shadow (enough to
        keep `_schedule_mig15`'s gate correct), which meant a mint with a
        real, still-open baseline/laya/migrate position never showed up in
        `_prune`'s `busy` set (~2330: `for run in self.books: ...`) -- so
        once it looked idle, `_prune` truncated its `book.flow`/`self.seen`
        entry, and that truncated history is exactly what got spliced onto
        the real engine. This fails against that subset-only version and
        passes against the current one, which runs the caller's full book
        list inside the shadow so `busy` is complete.
        """
        creates, rows = _fixture()
        tb0 = T0 - 5_000
        creates = dict(creates)
        creates["MintBusy"] = _create("MintBusy", tb0, creator="CreatorBusy")
        rows = list(rows) + [
            _trade(
                "MintBusy",
                tb0 + 400,
                trader="BusyProbe",
                sol=5_000_000_000,
                token=1_000_000,
                quote=80_000_000_000,
                slot=50,
                event_index=1,
                event_ts=False,
            ),
            _trade(
                "MintBusy",
                tb0 + 1_500,
                trader="BusyW1",
                sol=1_000_000_000,
                token=1_000_000,
                quote=36_000_000_000,
                slot=51,
                event_index=1,
            ),
        ]
        books = _books()
        mid_ms = T0 + 6_000
        kill_file = Path("/tmp/forward-paper-warm-start-busy-kill-absent")

        # A: one continuous replay, no restart. MintBusy never trades again
        # after tb0 + 1_500, but its baseline position stays open (exit_rule
        # "hold_30s" only fires ~30s after fill, well past TAPE_END here), so
        # an uninterrupted engine never prunes it -- it is always busy.
        engine_a = replay_rows(
            creates.values(),
            rows,
            books,
            tape_end_ms=TAPE_END,
            kill_file=kill_file,
            offsets_ms=OFFSETS,
        )
        busy_opens = [
            row
            for row in engine_a.positions
            if row["mint"] == "MintBusy" and row["event"] == "open" and row["book"] == "buy_all"
        ]
        self.assertTrue(busy_opens, "fixture should open a baseline position on MintBusy")
        expected_flow = [pr for pr in engine_a.library["MintBusy"].flow if pr.t_recv_ms <= mid_ms]
        self.assertEqual(len(expected_flow), 2, "both MintBusy prints are before mid_ms")

        # B: shadow-replay the first half. Confirm the shadow's own baseline
        # book actually opened MintBusy (so `busy` has something real to see).
        shadow = build_shadow_state(creates.values(), rows, books, until_ms=mid_ms, offsets_ms=OFFSETS)
        busy_run = next(run for run in shadow.books if run.spec.book_id == "buy_all")
        self.assertIn(
            "MintBusy",
            busy_run.ceiling.open,
            "shadow must run the real baseline book for _prune's busy set to see this open position",
        )

        # Force `_prune` to see every mint as long idle. `_prune` reads
        # `live_now` off `self.latency.now_ms()` whenever that callable is
        # set (always true for the shadow's tape-clock LatencyMeter) and
        # ignores the `now_ms` argument entirely in that case, so the only
        # way to push `live_now` forward here is to replace the callable --
        # this is `_prune` actually running inside the shadow's own window,
        # not a different code path.
        far_future = shadow._clock_ms + LIVE_IDLE_RETAIN_MS + PRUNE_AFTER_MS + 10_000
        shadow.latency.now_ms = lambda: far_future
        shadow._prune(far_future)

        self.assertEqual(shadow.library["MintBusy"].flow, expected_flow)
        self.assertEqual(shadow.seen["MintBusy"], {_dedupe_key(pr) for pr in expected_flow})

        engine_b = ForwardEngine(
            books,
            kill_file=kill_file,
            latency=LatencyMeter(),
            offsets_ms=OFFSETS,
            tape_end_ms=TAPE_END,
            retain_rows=True,
        )
        splice_state(shadow, engine_b)
        self.assertEqual(engine_b.library["MintBusy"].flow, expected_flow)

        self._feed_second_half(engine_b, creates, rows, mid_ms)

        a_decisions = [row for row in engine_a.decisions if row["decision_t_ms"] > mid_ms]
        a_positions = [row for row in engine_a.positions if row["decision_t_ms"] > mid_ms]
        b_decisions = [row for row in engine_b.decisions if row["decision_t_ms"] > mid_ms]
        b_positions = [row for row in engine_b.positions if row["decision_t_ms"] > mid_ms]
        self.assertEqual(a_decisions, b_decisions)
        self.assertEqual(a_positions, b_positions)


class RiskTests(unittest.TestCase):
    def _two_creates(self) -> tuple[dict[str, CreateSignal], list[dict[str, object]]]:
        creates = {
            "MintOld": _create("MintOld", T0 - 60_000, creator="CreatorOld"),
            "MintA": _create("MintA", T0, creator="CreatorA"),
            "MintB": _create("MintB", T0 + 31_000, creator="CreatorA"),
        }
        rows = [
            _trade("MintOld", T0 - 50_000, trader="Old", slot=1),
            _trade("MintA", T0 + 1_000, trader="A", slot=2),
            _trade("MintB", T0 + 32_000, trader="B", slot=3),
        ]
        return creates, rows

    def test_kill_switch_skips_entries(self) -> None:
        creates, rows = self._two_creates()
        with tempfile.TemporaryDirectory() as tmp:
            kill = Path(tmp) / "KILL"
            kill.write_text("stop\n", encoding="utf-8")
            engine = replay_rows(
                creates.values(),
                rows,
                [BookSpec("buy_all", "baseline", "hold_30s", max_concurrent=None, daily_loss_lamports=None, creator_cooldown_ms=0, token_cooldown_ms=0)],
                tape_end_ms=T0 + 90_000,
                kill_file=kill,
                offsets_ms=(5_000,),
            )
        reasons = [row["reason"] for row in engine.decisions if row["book"] == "buy_all"]
        self.assertIn("kill_switch", reasons)
        self.assertFalse([row for row in engine.positions if row["event"] == "open"])

    def test_max_concurrent_and_creator_cooldown(self) -> None:
        creates, rows = self._two_creates()
        engine = replay_rows(
            creates.values(),
            rows,
            [
                BookSpec(
                    "gated",
                    "baseline",
                    "hold_30s",
                    max_concurrent=1,
                    daily_loss_lamports=None,
                    creator_cooldown_ms=60_000,
                    token_cooldown_ms=0,
                )
            ],
            tape_end_ms=T0 + 90_000,
            kill_file=Path("/tmp/forward-paper-no-kill"),
            offsets_ms=(5_000,),
        )
        actions = [(row["mint"], row["action"], row["reason"]) for row in engine.decisions if row["book"] == "gated"]
        self.assertIn(("MintA", "enter", None), actions)
        self.assertTrue(any(row[0] == "MintB" and row[1] == "skip" for row in actions))

    def test_daily_loss_cap_is_a_skip_reason(self) -> None:
        spec = BookSpec("gated", "laya", "hold_30s", threshold=0.5, daily_loss_lamports=1000)
        engine = ForwardEngine([spec], kill_file=Path("/tmp/forward-paper-no-kill-2"))
        run = engine.books[0]
        import time

        run.day = time.strftime("%Y-%m-%d", time.gmtime(T0 / 1000))
        run.day_pnl = -1000
        reason = engine._risk_reason(run, "Mint", "Creator", T0, spec.size_lamports)
        self.assertEqual(reason, "daily_loss_cap")
        self.assertLessEqual(spec.size_lamports, HARD_MAX_POSITION_LAMPORTS)

    def test_shipped_config_stays_inside_the_ceilings(self) -> None:
        import json

        raw = json.loads(Path("scripts/mal-core/forward-paper.json").read_text(encoding="utf-8"))
        books = books_from_config(raw)
        self.assertGreaterEqual(len(books), 1)
        for book in books:
            assert book.max_concurrent is not None
            assert book.daily_loss_lamports is not None
            self.assertLessEqual(book.max_concurrent, CEILING_MAX_CONCURRENT)
            self.assertLessEqual(book.daily_loss_lamports, CEILING_DAILY_LOSS_LAMPORTS)
            self.assertLessEqual(book.size_lamports, HARD_MAX_POSITION_LAMPORTS)

    def test_config_refuses_a_size_above_the_cap(self) -> None:
        with self.assertRaises(RiskConfigError):
            books_from_config({"books": [{"id": "big", "kind": "baseline", "exit": "hold_30s", "size_sol": 1.0}]})

    def test_config_can_only_tighten_risk_ceilings(self) -> None:
        tight = books_from_config(
            {
                "books": [
                    {
                        "id": "tight",
                        "kind": "baseline",
                        "exit": "hold_30s",
                        "size_sol": 0.01,
                        "max_concurrent": 1,
                        "daily_loss_sol": 0.05,
                    }
                ]
            }
        )
        self.assertEqual(tight[0].size_lamports, 10_000_000)
        self.assertEqual(tight[0].max_concurrent, 1)
        self.assertEqual(tight[0].daily_loss_lamports, 50_000_000)
        omitted = books_from_config({"books": [{"id": "b", "kind": "baseline", "exit": "hold_30s"}]})
        self.assertEqual(omitted[0].max_concurrent, CEILING_MAX_CONCURRENT)
        self.assertEqual(omitted[0].daily_loss_lamports, CEILING_DAILY_LOSS_LAMPORTS)
        wider = [
            {"max_concurrent": 4},
            {"max_concurrent": None},
            {"daily_loss_sol": 1.0},
            {"daily_loss_sol": None},
            {"size_sol": 0.06},
        ]
        for extra in wider:
            with self.subTest(extra=extra):
                with self.assertRaises(RiskConfigError):
                    books_from_config({"books": [{"id": "b", "kind": "baseline", "exit": "hold_30s", **extra}]})
        with self.assertRaises(RiskConfigError):
            books_from_config({"kill_switch": False, "books": [{"id": "b", "kind": "baseline", "exit": "hold_30s"}]})
        with self.assertRaises(RiskConfigError):
            books_from_config({"kill_file": "", "books": [{"id": "b", "kind": "baseline", "exit": "hold_30s"}]})

    def test_runtime_ceiling_holds_when_the_spec_was_loosened(self) -> None:
        spec = BookSpec(
            "loose",
            "baseline",
            "hold_30s",
            max_concurrent=50,
            daily_loss_lamports=10**15,
            size_lamports=10**15,
        )
        engine = ForwardEngine([spec], kill_file=Path("/tmp/forward-paper-ceilings"))
        run = engine.books[0]
        run.open = {f"m{i}": None for i in range(CEILING_MAX_CONCURRENT)}  # type: ignore[assignment]
        reason = engine._risk_reason(run, "new", None, T0, HARD_MAX_POSITION_LAMPORTS)
        self.assertEqual(reason, "max_concurrent")
        run.open.clear()
        run.day = __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime(T0 / 1000))
        run.day_pnl = -CEILING_DAILY_LOSS_LAMPORTS
        reason = engine._risk_reason(run, "new", None, T0, HARD_MAX_POSITION_LAMPORTS)
        self.assertEqual(reason, "daily_loss_cap")
        run.day_pnl = 0
        reason = engine._risk_reason(run, "new", None, T0, HARD_MAX_POSITION_LAMPORTS + 1)
        self.assertEqual(reason, "max_position_size")

    def test_hot_reload_refuses_a_wider_config(self) -> None:
        current = books_from_config(
            {"books": [{"id": "b", "kind": "baseline", "exit": "hold_30s", "max_concurrent": 1}]}
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "forward-paper.json"
            path.write_text(
                '{"books":[{"id":"b","kind":"baseline","exit":"hold_30s","max_concurrent":9}]}',
                encoding="utf-8",
            )
            kept, kill = reload_risk_config(path, current)
            self.assertIs(kept, current)
            self.assertIsNone(kill)
            self.assertEqual(current[0].max_concurrent, 1)
            path.write_text(
                '{"kill_switch": false, "books":[{"id":"b","kind":"baseline","exit":"hold_30s","max_concurrent":1}]}',
                encoding="utf-8",
            )
            kept, _kill = reload_risk_config(path, current)
            self.assertIs(kept, current)
            path.write_text(
                '{"books":[{"id":"b","kind":"baseline","exit":"hold_30s","max_concurrent":1,"daily_loss_sol":0.05}]}',
                encoding="utf-8",
            )
            fresh, _kill = reload_risk_config(path, current)
            self.assertEqual(fresh[0].max_concurrent, 1)
            self.assertEqual(fresh[0].daily_loss_lamports, 50_000_000)


class MemoryBoundTests(unittest.TestCase):
    """Cooldown-map pruning and orphan-print eviction change no decision."""

    def test_cooldown_pruning_matches_an_engine_that_never_prunes(self) -> None:
        spec = BookSpec(
            "buy_all",
            "baseline",
            "hold_30s",
            max_concurrent=None,
            daily_loss_lamports=None,
            creator_cooldown_ms=2_000,
            token_cooldown_ms=2_000,
        )

        def _build() -> ForwardEngine:
            engine = ForwardEngine(
                [spec],
                kill_file=Path("/tmp/forward-paper-cooldown-prune"),
                tape_end_ms=T0 + 120_000,
                retain_rows=True,
            )
            engine.push_create(_create("MintA", T0, creator="CreatorA"))
            engine.push_print(*_parsed("MintA", T0 + 1_000))
            # Unrelated filler print: advances the clock past MintA's hold_30s
            # exit the same way, at the same instant, in both engines below --
            # not a forced/early finalize, just the tape moving forward.
            engine.push_print(*_parsed("Filler", T0 + 40_000))
            engine.push_create(_create("MintB", T0 + 60_000, creator="CreatorA"))
            engine.push_print(*_parsed("MintB", T0 + 60_500))
            return engine

        # Both engines see the exact same events at the exact same watermarks.
        # The only difference is that `pruned` also calls `_prune` in between.
        checkpoints = (T0 + 10_000, T0 + 25_000, T0 + 41_000, T0 + 55_000)
        pruned = _build()
        for cp in checkpoints:
            pruned.drain_until(cp)
            pruned._prune(cp)
        pruned.drain_until(T0 + 120_000, final=True)

        baseline = _build()
        for cp in checkpoints:
            baseline.drain_until(cp)
        baseline.drain_until(T0 + 120_000, final=True)

        self.assertEqual(pruned.decisions, baseline.decisions)
        self.assertEqual(pruned.positions, baseline.positions)
        mintb_ceiling = [row for row in pruned.decisions if row["mint"] == "MintB" and row.get("ledger") == "ceiling"]
        self.assertTrue(any(row["action"] == "enter" for row in mintb_ceiling))
        # And confirm pruning actually removed something along the way.
        ledger = baseline.books[0].ceiling
        self.assertIn("MintA", ledger.token_ready)
        self.assertIn("CreatorA", ledger.creator_ready)

    def test_prune_drops_stale_cooldowns_under_a_long_stream(self) -> None:
        spec = BookSpec(
            "buy_all",
            "baseline",
            "hold_30s",
            max_concurrent=None,
            daily_loss_lamports=None,
            creator_cooldown_ms=1_000,
            token_cooldown_ms=1_000,
        )
        n = 30
        engine = ForwardEngine(
            [spec],
            kill_file=Path("/tmp/forward-paper-cooldown-bound"),
            tape_end_ms=T0 + n * 60_000 + 60_000,
            retain_rows=False,
        )
        for i in range(n):
            base = T0 + i * 60_000
            mint = f"Mint{i}"
            creator = f"Creator{i}"
            engine.push_create(_create(mint, base, creator=creator))
            engine.push_print(*_parsed(mint, base + 1_000))
            engine.drain_until(base + 35_000, final=True)
            engine._prune(base + 35_000)
        ledger = engine.books[0].ceiling
        self.assertLess(len(ledger.token_ready), 3)
        self.assertLess(len(ledger.creator_ready), 3)
        self.assertEqual(ledger.closed_n, n)

    def test_prune_drops_orphan_early_prints_with_no_decision_either_way(self) -> None:
        spec = BookSpec("buy_all", "baseline", "hold_30s")
        engine = ForwardEngine([spec], kill_file=Path("/tmp/forward-paper-early-prune"), retain_rows=True)
        engine.push_print(*_parsed("GhostMint", T0 + 100))
        engine.flush()
        self.assertIn("GhostMint", engine.early)
        engine._prune(T0 + 100 + PRUNE_AFTER_MS - 1)
        self.assertIn("GhostMint", engine.early)
        engine._prune(T0 + 100 + PRUNE_AFTER_MS)
        self.assertNotIn("GhostMint", engine.early)
        self.assertEqual(engine.decisions, [])
        self.assertEqual(engine.positions, [])

    def test_preboot_dead_mints_excludes_creates_inside_the_safety_margin(self) -> None:
        """`_preboot_dead_mints` (the fix for the ~115 MB/h `self.early` leak:
        see PREBOOT_DEAD_MARGIN_MS's comment) must find a create dated well
        before boot in either today's or yesterday's observe file, but must
        leave alone a create inside the margin -- that one could still be a
        normal, in-flight create a real restart's offset replay would
        legitimately deliver moments later, so treating it as dead would be
        the unsafe (decision-changing) direction.
        """
        boot_ms = 1_700_100_000_000
        boot_day = time.strftime("%Y-%m-%d", time.gmtime(boot_ms / 1000))
        prior_day = time.strftime("%Y-%m-%d", time.gmtime(boot_ms / 1000 - 86_400))

        def _row(mint: str, t_ms: int, creator: str) -> str:
            return json.dumps({"stream": "subscribeNewToken", "mint": mint, "t_ws": t_ms, "traderPublicKey": creator})

        with tempfile.TemporaryDirectory() as tmp:
            creates_dir = Path(tmp)
            old_ms = boot_ms - PREBOOT_DEAD_MARGIN_MS - 60_000
            recent_ms = boot_ms - 5_000
            yesterday_ms = boot_ms - 86_400_000 - 1_000
            (creates_dir / f"observe-{boot_day}.jsonl").write_text(
                _row("OldMint", old_ms, "C1") + "\n" + _row("RecentMint", recent_ms, "C2") + "\n",
                encoding="utf-8",
            )
            (creates_dir / f"observe-{prior_day}.jsonl").write_text(
                _row("YesterdayMint", yesterday_ms, "C3") + "\n",
                encoding="utf-8",
            )
            dead = _preboot_dead_mints(creates_dir, boot_ms)
        self.assertIn("OldMint", dead)
        self.assertIn("YesterdayMint", dead)
        self.assertNotIn("RecentMint", dead)

    def test_preboot_dead_mints_empty_when_creates_dir_has_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dead = _preboot_dead_mints(Path(tmp), 1_700_100_000_000)
        self.assertEqual(dead, frozenset())

    def test_safe_preboot_dead_mints_survives_a_raising_load_creates(self) -> None:
        """`_safe_preboot_dead_mints` runs once at `serve()`'s boot, before
        the main loop or the kill switch is live -- a corrupt observe line,
        a `zstd` failure, or a permissions error inside `load_creates` must
        not crash `serve()` and restart-loop the live runner. An empty
        result is always safe: rule B (`EARLY_BUFFER_DEAD_MS`) still catches
        the same mints within its own timeout, same as before this PR.
        """
        from unittest import mock

        boot_ms = 1_700_100_000_000
        boot_day = time.strftime("%Y-%m-%d", time.gmtime(boot_ms / 1000))
        with tempfile.TemporaryDirectory() as tmp:
            creates_dir = Path(tmp)
            # A file must exist so `_preboot_dead_mints` actually calls
            # `load_creates` (an empty dir already short-circuits to
            # frozenset() without exercising the try/except at all).
            (creates_dir / f"observe-{boot_day}.jsonl").write_text("{}\n", encoding="utf-8")
            with mock.patch(
                "tools.forward_paper.load_creates",
                side_effect=RuntimeError("boom: corrupt observe line"),
            ):
                dead = _safe_preboot_dead_mints(creates_dir, boot_ms)
        self.assertEqual(dead, frozenset())

    def test_dead_mint_prints_are_dropped_not_buffered(self) -> None:
        """A mint `serve()` knows can never get a create this run (see
        `_preboot_dead_mints`) must have its prints dropped outright, not
        buffered in `self.early` -- that buffer growing without bound for a
        createless mint that keeps trading is the leak this fix targets. A
        mint NOT flagged dead keeps the existing, unchanged behavior.
        """
        spec = BookSpec("buy_all", "baseline", "hold_30s")
        engine = ForwardEngine(
            [spec],
            kill_file=Path("/tmp/forward-paper-dead-mint-drop"),
            dead_mints=frozenset({"DeadMint"}),
        )
        engine.push_print(*_parsed("DeadMint", T0 + 100))
        engine.flush()
        self.assertNotIn("DeadMint", engine.early)
        self.assertEqual(engine.dead_prints_dropped, 1)
        self.assertEqual(engine.decisions, [])
        self.assertEqual(engine.positions, [])
        # A mint not on the dead list is unaffected: existing orphan-print
        # buffering behavior stays exactly as it was.
        engine.push_print(*_parsed("GhostMint", T0 + 100))
        engine.flush()
        self.assertIn("GhostMint", engine.early)

    def test_dead_mint_buffer_never_grows_even_while_continuously_trading(self) -> None:
        """Before this fix, a createless mint that never idles a full 45
        minutes (`PRUNE_AFTER_MS`) grows `self.early` without bound -- the
        ~115 MB/h leak the lab note measured. A mint correctly identified as
        dead at boot must cost O(1) memory no matter how long or how often
        it keeps trading.
        """
        spec = BookSpec("buy_all", "baseline", "hold_30s")
        engine = ForwardEngine(
            [spec],
            kill_file=Path("/tmp/forward-paper-dead-mint-bounded"),
            dead_mints=frozenset({"DeadMint"}),
        )
        n = 2_000
        for i in range(n):
            # A print every 5s for hours straight: never a 45-minute idle
            # gap, so the pre-fix code path would never evict this either.
            mint, pr, ts = _parsed("DeadMint", T0 + i * 5_000)
            engine.push_print(mint, pr, ts)
            engine.drain_until(T0 + i * 5_000)
        engine.flush()
        self.assertEqual(engine.early, {})
        self.assertEqual(engine.dead_prints_dropped, n)

    def test_early_timeout_marks_a_stuck_createless_mint_dead(self) -> None:
        """Rule B (`EARLY_BUFFER_DEAD_MS`): a mint that has been sitting in
        `self.early` (no create, however briefly or long it has traded) for
        longer than `early_timeout_ms`, measured from its OLDEST buffered
        print, is declared dead outright -- added to `dead_mints` so future
        prints are dropped too, not just this buffer -- rather than waiting
        for `PRUNE_AFTER_MS`'s full-idle eviction, which an actively-trading
        createless mint (rule A misses, e.g. an old already-migrated token
        with no create in any retained observe file) would never reach.
        """
        timeout_ms = 600_000
        spec = BookSpec("buy_all", "baseline", "hold_30s")
        engine = ForwardEngine(
            [spec],
            kill_file=Path("/tmp/forward-paper-early-timeout"),
            early_timeout_ms=timeout_ms,
        )
        engine.push_print(*_parsed("StuckMint", T0 + 100))
        engine.flush()
        self.assertIn("StuckMint", engine.early)
        self.assertNotIn("StuckMint", engine.dead_mints)

        # Not yet past the threshold: still buffered, not yet declared dead.
        engine._prune(T0 + 100 + timeout_ms - 1)
        self.assertIn("StuckMint", engine.early)
        self.assertNotIn("StuckMint", engine.dead_mints)
        self.assertEqual(engine.early_timeout_mints_dropped, 0)

        # Past the threshold: buffer dropped, mint marked dead.
        engine._prune(T0 + 100 + timeout_ms)
        self.assertNotIn("StuckMint", engine.early)
        self.assertIn("StuckMint", engine.dead_mints)
        self.assertEqual(engine.early_timeout_mints_dropped, 1)
        self.assertEqual(engine.dead_prints_dropped, 1)

        # A later print for the now-dead mint is dropped, not re-buffered.
        engine.push_print(*_parsed("StuckMint", T0 + 100 + timeout_ms + 5_000))
        engine.flush()
        self.assertNotIn("StuckMint", engine.early)
        self.assertEqual(engine.dead_prints_dropped, 2)
        self.assertEqual(engine.decisions, [])
        self.assertEqual(engine.positions, [])

    def test_early_timeout_is_off_by_default(self) -> None:
        """`early_timeout_ms` defaults to `None` (rule B disabled) so every
        caller except `serve()` -- `replay_rows()`, ParityTests, promotion
        backtests -- keeps today's unchanged full-history behavior. A mint
        stuck well past what a real timeout would use, but short of
        `PRUNE_AFTER_MS`'s full idle window, must stay buffered.
        """
        spec = BookSpec("buy_all", "baseline", "hold_30s")
        engine = ForwardEngine([spec], kill_file=Path("/tmp/forward-paper-early-timeout-off"))
        self.assertIsNone(engine.early_timeout_ms)
        engine.push_print(*_parsed("StuckMint", T0 + 100))
        engine.flush()
        engine._prune(T0 + 100 + 1_000_000)  # well under PRUNE_AFTER_MS (45 min)
        self.assertIn("StuckMint", engine.early)
        self.assertEqual(engine.dead_mints, set())
        self.assertEqual(engine.early_timeout_mints_dropped, 0)

    def test_wallet_pos_dict_does_not_grow_with_closed_round_trips(self) -> None:
        """`_Wallet.pos` (tools/laya_v0.py) holds one entry per mint with a
        currently open position, popped on full close. A single busy trader
        that round-trips 200 distinct mints should leave `pos` empty at the
        end, not accumulate one stale entry per mint -- the growth this PR's
        `_Wallet` compaction (merging pos_tokens/pos_cost/pos_open_t/
        pos_invested into `pos`) does not change, only makes each entry
        cheaper. `mint_pnl` and `mints` are expected to keep every mint by
        design (read by `_leader_ok` / `is_bot`); this test only bounds `pos`.
        """
        n = 200
        creates: dict[str, CreateSignal] = {}
        rows: list[dict[str, object]] = []
        for i in range(n):
            mint = f"BusyMint{i:04d}"
            t0 = T0 + i * 200
            creates[mint] = _create(mint, t0, creator=f"Creator{i:04d}")
            rows.append(_trade(mint, t0 + 10, trader="Busy", slot=i + 2, token=1_000_000, sol=1_000_000_000))
            rows.append(_trade(mint, t0 + 20, trader="Busy", side="sell", slot=i + 2, token=1_000_000, sol=1_000_000_000))
        engine = replay_rows(
            creates.values(),
            rows,
            [BookSpec("buy_all", "baseline", "hold_30s", max_concurrent=None, daily_loss_lamports=None, creator_cooldown_ms=0, token_cooldown_ms=0)],
            tape_end_ms=T0 + n * 200 + 1_000,
            kill_file=Path("/tmp/forward-paper-wallet-pos-bound"),
            offsets_ms=(5_000,),
        )
        wallet = engine.wallets.wallets["Busy"]
        self.assertEqual(wallet.pos, {})
        self.assertEqual(wallet.closed, n)
        self.assertEqual(len(wallet.mint_pnl), n)

    def test_gc_mitigation_raises_thresholds_freezes_and_times_collections(self) -> None:
        """`install_gc_mitigation()` (tools/forward_paper.py) is the fix for the
        multi-hundred-ms to multi-second `gc.collect(2)` stop-the-world pauses
        measured on a large `WalletState` (see the lab note) -- it must not
        touch any application value, only GC scheduling, which this pins by
        restoring gc's real state around the test.
        """
        import gc

        old_threshold = gc.get_threshold()
        old_enabled = gc.isenabled()
        old_callbacks = list(gc.callbacks)
        try:
            stats = install_gc_mitigation()
            self.assertIsInstance(stats, GcStats)
            self.assertEqual(gc.get_threshold(), GC_THRESHOLD)
            self.assertTrue(gc.isenabled(), "raising thresholds keeps automatic collection as a safety net")
            self.assertGreater(gc.get_freeze_count(), 0, "freeze() should have moved the current heap to the permanent generation")
            # A manual collection after install is still timed by the callback.
            before = stats.collections
            gc.collect()
            self.assertGreater(stats.collections, before)
            report = stats.report()
            self.assertEqual(report["collections"], stats.collections)
            self.assertGreaterEqual(report["max_pause_ms"], 0.0)
            self.assertEqual(report["thresholds"]["gen0"], GC_THRESHOLD[0])
            self.assertTrue(report["enabled"])
        finally:
            gc.callbacks[:] = old_callbacks
            gc.set_threshold(*old_threshold)
            gc.unfreeze()
            if old_enabled:
                gc.enable()
            else:
                gc.disable()

    def test_maybe_gc_freeze_seeds_then_waits_then_fires(self) -> None:
        """`maybe_gc_freeze()` (tools/forward_paper.py) is `serve()`'s
        periodic follow-up to `install_gc_mitigation()`'s one startup
        `gc.freeze()` -- it must seed its clock on the first call instead of
        firing immediately, then fire at most once per `interval_s`,
        recording the freeze in `GcStats` (see `GC_THRESHOLD`'s comment for
        why a periodic freeze is needed at all).
        """
        import gc

        old_freeze_count = gc.get_freeze_count()
        try:
            stats = GcStats()
            self.assertIsNone(stats.last_freeze_at)
            fired = maybe_gc_freeze(stats, 0.0, interval_s=10.0)
            self.assertFalse(fired, "the first call only seeds the clock")
            self.assertEqual(stats.freeze_count, 0)
            self.assertEqual(gc.get_freeze_count(), old_freeze_count)

            fired = maybe_gc_freeze(stats, 5.0, interval_s=10.0)
            self.assertFalse(fired, "interval has not elapsed yet")
            self.assertEqual(stats.freeze_count, 0)

            fired = maybe_gc_freeze(stats, 10.0, interval_s=10.0)
            self.assertTrue(fired)
            self.assertEqual(stats.freeze_count, 1)
            self.assertGreater(gc.get_freeze_count(), old_freeze_count)
            self.assertGreaterEqual(stats.last_freeze_ms, 0.0)
            self.assertGreaterEqual(stats.total_freeze_ms, stats.last_freeze_ms)
            report = stats.report()
            self.assertEqual(report["freeze_count"], 1)
            self.assertEqual(report["gc_freeze_count"], gc.get_freeze_count())

            fired = maybe_gc_freeze(stats, 11.0, interval_s=10.0)
            self.assertFalse(fired, "clock was reset by the freeze that just ran")
            self.assertEqual(stats.freeze_count, 1)
        finally:
            gc.unfreeze()

    def test_maybe_gc_freeze_failure_does_not_propagate(self) -> None:
        """Same invariant `write_mem_census` is held to: a `gc.freeze()`
        surprise, or any error updating `GcStats`'s fields, must never be
        able to kill `serve()`'s main loop.
        """
        import gc
        from unittest import mock

        stats = GcStats()
        stats.last_freeze_at = 0.0  # already seeded; the next call should try to freeze.
        with mock.patch.object(gc, "freeze", side_effect=RuntimeError("boom: freeze failed")):
            fired = maybe_gc_freeze(stats, 1_000.0, interval_s=1.0)
        self.assertFalse(fired)
        self.assertEqual(stats.freeze_count, 0)

        class _BoomOnSet:
            def __init__(self) -> None:
                object.__setattr__(self, "last_freeze_at", 0.0)
                object.__setattr__(self, "freeze_count", 0)

            def __setattr__(self, name: str, value: object) -> None:
                raise RuntimeError("boom: stats field assignment failed")

        try:
            fired = maybe_gc_freeze(_BoomOnSet(), 2_000.0, interval_s=1.0)
            self.assertFalse(fired)
        finally:
            gc.unfreeze()

    def test_periodic_gc_freeze_does_not_change_decisions_or_positions(self) -> None:
        """`maybe_gc_freeze()` only moves objects between GC generations; it
        must never change what a book decides. Same event stream, same
        checkpoints as `test_cooldown_pruning_matches_an_engine_that_never_prunes`
        above -- the only difference is that `freezing` also calls
        `maybe_gc_freeze` at each checkpoint with `interval_s=0` so a real
        `gc.freeze()` actually fires mid-stream, more aggressively than
        `serve()`'s 10-minute default.
        """
        import gc

        spec = BookSpec(
            "buy_all",
            "baseline",
            "hold_30s",
            max_concurrent=None,
            daily_loss_lamports=None,
            creator_cooldown_ms=0,
            token_cooldown_ms=0,
        )

        def _build() -> ForwardEngine:
            engine = ForwardEngine(
                [spec],
                kill_file=Path("/tmp/forward-paper-gc-freeze-neutral"),
                tape_end_ms=T0 + 120_000,
                retain_rows=True,
            )
            engine.push_create(_create("MintA", T0, creator="CreatorA"))
            engine.push_print(*_parsed("MintA", T0 + 1_000))
            engine.push_print(*_parsed("Filler", T0 + 40_000))
            engine.push_create(_create("MintB", T0 + 60_000, creator="CreatorB"))
            engine.push_print(*_parsed("MintB", T0 + 60_500))
            return engine

        checkpoints = (T0 + 10_000, T0 + 25_000, T0 + 41_000, T0 + 55_000)
        old_freeze_count = gc.get_freeze_count()
        try:
            freezing = _build()
            gc_stats = GcStats()
            t = 0.0
            for cp in checkpoints:
                freezing.drain_until(cp)
                maybe_gc_freeze(gc_stats, t, interval_s=0.0)
                t += 1.0
            freezing.drain_until(T0 + 120_000, final=True)
            self.assertGreater(gc_stats.freeze_count, 0, "the test should actually exercise a mid-stream freeze")
            self.assertGreater(gc.get_freeze_count(), old_freeze_count)

            baseline = _build()
            for cp in checkpoints:
                baseline.drain_until(cp)
            baseline.drain_until(T0 + 120_000, final=True)

            self.assertEqual(freezing.decisions, baseline.decisions)
            self.assertEqual(freezing.positions, baseline.positions)
        finally:
            gc.unfreeze()

    def test_mem_census_is_read_only_and_reports_every_container(self) -> None:
        """`MemCensus.snapshot()` (tools/forward_paper.py) is the live
        per-container census the #123 PR adds since the offline harness
        (#122) could not cleanly reproduce Oracle's ~460 MB/h. It must not
        mutate engine state -- checked here by snapshotting twice and
        confirming the engine's own containers are byte-for-byte the same
        Python objects (same id, same contents) after two census calls.
        """
        n = 50
        creates: dict[str, CreateSignal] = {}
        rows: list[dict[str, object]] = []
        for i in range(n):
            mint = f"CensusMint{i:04d}"
            t0 = T0 + i * 200
            creates[mint] = _create(mint, t0, creator=f"CensusCreator{i:04d}")
            rows.append(_trade(mint, t0 + 10, trader="CensusWallet", slot=i + 2, token=1_000_000, sol=1_000_000_000))
            rows.append(_trade(mint, t0 + 20, trader="CensusWallet", side="sell", slot=i + 2, token=1_000_000, sol=1_000_000_000))
        engine = replay_rows(
            creates.values(),
            rows,
            [BookSpec("buy_all", "baseline", "hold_30s", max_concurrent=None, daily_loss_lamports=None, creator_cooldown_ms=0, token_cooldown_ms=0)],
            tape_end_ms=T0 + n * 200 + 1_000,
            kill_file=Path("/tmp/forward-paper-mem-census"),
            offsets_ms=(5_000,),
        )
        census_maker = MemCensus()
        library_before = dict(engine.library)
        wallets_before = dict(engine.wallets.wallets)
        row1 = census_maker.snapshot(engine)
        row2 = census_maker.snapshot(engine)
        self.assertEqual(engine.library, library_before)
        self.assertEqual(engine.wallets.wallets.keys(), wallets_before.keys())
        self.assertEqual(row1["library"], n)
        self.assertEqual(row1["by_creator"], n)
        self.assertEqual(row1["early_mints"], 0)
        self.assertEqual(row1["early_prints_buffered"], 0)
        self.assertEqual(row1["wallets"]["n_wallets"], 1)
        # Every position round-tripped and closed: pos entries bounded, same
        # invariant MemoryBoundTests.test_wallet_pos_dict_does_not_grow_with_closed_round_trips checks.
        self.assertEqual(row1["wallets"]["pos_entries"], 0)
        self.assertEqual(row1["wallets"]["mint_pnl_entries"], n)
        self.assertIsInstance(row1["rss_kb_proc"], (int, type(None)))
        self.assertEqual(row2["library"], row1["library"])

    def test_write_mem_census_never_propagates_a_failure(self) -> None:
        """`write_mem_census()` is called from `serve()`'s main loop every 5
        minutes; this diagnostics-only helper must never be able to kill the
        process. Exercise every failure point a raising `mem_census`,
        `gc_stats`, or `logs["mem_census"]` could hit -- the call must return
        an incremented failure count, not raise.
        """

        class _RaisingCensus:
            def snapshot(self, engine: object) -> dict[str, object]:
                raise RuntimeError("boom: census walk failed")

        class _RaisingGcStats:
            def report(self) -> dict[str, object]:
                raise RuntimeError("boom: gc report failed")

        class _RaisingLog:
            def write(self, row: dict[str, object]) -> None:
                raise OSError("boom: disk full")

        class _QuietLog:
            def write(self, row: dict[str, object]) -> None:
                pass

        spec = BookSpec("buy_all", "baseline", "hold_30s")
        engine = ForwardEngine([spec], kill_file=Path("/tmp/forward-paper-census-fail"))
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            good_census = MemCensus()
            good_stats = GcStats()

            # 1. The snapshot itself raises.
            failures = write_mem_census(
                engine, _RaisingCensus(), good_stats, output_dir, {"mem_census": _RaisingLog()}, 0, 0
            )
            self.assertEqual(failures, 1)

            # 2. The snapshot succeeds but gc_stats.report() raises.
            failures = write_mem_census(
                engine, good_census, _RaisingGcStats(), output_dir, {"mem_census": _RaisingLog()}, 0, 0
            )
            self.assertEqual(failures, 1)

            # 3. Both succeed but the jsonl write raises (e.g. disk full).
            failures = write_mem_census(
                engine, good_census, good_stats, output_dir, {"mem_census": _RaisingLog()}, 0, 0
            )
            self.assertEqual(failures, 1)

            # 4. A bad output_dir (file write fails) also does not propagate.
            bogus_dir = output_dir / "does" / "not" / "exist-and-is-not-created"
            failures = write_mem_census(engine, good_census, good_stats, bogus_dir, {"mem_census": _RaisingLog()}, 0, 0)
            self.assertEqual(failures, 1)

            # 5. Repeated failures accumulate the counter across calls.
            failures = 0
            for _ in range(5):
                failures = write_mem_census(
                    engine, _RaisingCensus(), good_stats, output_dir, {"mem_census": _RaisingLog()}, 0, failures
                )
            self.assertEqual(failures, 5)

            # 6. A healthy call after prior failures succeeds and leaves the
            # failure count unchanged (the counter only increments on failure).
            failures = write_mem_census(engine, good_census, good_stats, output_dir, {"mem_census": _QuietLog()}, 0, failures)
            self.assertEqual(failures, 5)
            self.assertTrue((output_dir / "mem-census.json").is_file())

    def test_latency_meter_ring_buffers_stay_bounded(self) -> None:
        meter = LatencyMeter(extra_ms=1)
        base = 1_700_000_000
        for i in range(CHAIN_SAMPLE_CAP + 5_000):
            meter.note_print(base * 1000 + i, base + i // 1000)
        self.assertEqual(len(meter.chain_to_recv), CHAIN_SAMPLE_CAP)
        self.assertIsNotNone(meter.chain_median_ms())
        for i in range(LATENCY_SAMPLE_CAP + 1_000):
            meter.measure(i * 10)
        self.assertEqual(len(meter.recv_to_decision), LATENCY_SAMPLE_CAP)
        self.assertEqual(len(meter.decision_to_send), LATENCY_SAMPLE_CAP)
        self.assertEqual(len(meter.applied), LATENCY_SAMPLE_CAP)
        report = meter.report()
        self.assertEqual(report["chain_to_recv"]["n"], CHAIN_SAMPLE_CAP)


class ModelAndLogTests(unittest.TestCase):
    def test_hot_reload_picks_up_a_new_file(self) -> None:
        import os
        import pickle

        with tempfile.TemporaryDirectory() as tmp:
            saved = Path(tmp) / "entry_model.pkl"
            with saved.open("wb") as fh:
                pickle.dump({"backend": "sklearn", "names": ["f_n_buy"], "model": _FakeBooster()}, fh)
            slot = ModelSlot(saved, None)
            slot.maybe_reload(force=True)
            self.assertEqual(slot.loads, 1)
            self.assertEqual(slot.booster.names, ["f_n_buy"])
            os.utime(saved, (saved.stat().st_mtime + 5, saved.stat().st_mtime + 5))
            slot.maybe_reload()
            self.assertEqual(slot.loads, 2)

    def test_decision_log_schema_and_no_signing_surface(self) -> None:
        source = Path("tools/forward_paper.py").read_text(encoding="utf-8")
        for banned in ("Keypair", "sendTransaction", "solders", "nacl", "HELIUS_API_KEY", "private_key"):
            self.assertNotIn(banned, source)
        self.assertNotIn("ENTRY_LATENCY_MS", source)
        unit = Path("scripts/mal-core/mal-forward-paper.service").read_text(encoding="utf-8")
        self.assertNotIn("mal-trade-tape", unit)
        self.assertIn("Nice=19", unit)
        creates, rows = _fixture()
        engine = replay_rows(
            [creates["MintA"], creates["MintOld"]],
            rows,
            [BookSpec("buy_all", "baseline", "hold_30s", max_concurrent=None, daily_loss_lamports=None, creator_cooldown_ms=0, token_cooldown_ms=0)],
            tape_end_ms=TAPE_END,
            kill_file=Path("/tmp/forward-paper-no-kill-3"),
            offsets_ms=OFFSETS,
        )
        self.assertTrue(engine.decisions)
        self.assertEqual(engine.decisions[0]["schema"], SCHEMA_DECISION)
        live = replay_rows(
            [creates["MintA"]],
            rows,
            _books(),
            tape_end_ms=TAPE_END,
            kill_file=Path("/tmp/forward-paper-no-retain"),
            offsets_ms=OFFSETS,
            retain_rows=False,
        )
        self.assertEqual(live.packets, [])
        self.assertEqual(live.decisions, [])
        self.assertEqual(live.positions, [])
        self.assertGreater(live.books[0].closed_n + len(live.books[0].open) + len(live.books[0].pending), 0)
        hops = engine.latency.report()
        self.assertGreater(hops["chain_to_recv"]["n"], 0)
        self.assertIsNotNone(hops["chain_to_recv"]["p99_ms"])

    def test_latency_percentiles(self) -> None:
        meter = LatencyMeter()
        for lag in (100, 200, 300):
            meter.note_print(1_000_000 + lag, 1)
        # event_ts 1 is rejected (< 1e9). Feed real seconds.
        meter.chain_to_recv.clear()
        base = 1_700_000_000
        for extra in (100, 500, 2_000):
            meter.note_print(base * 1000 + extra, base)
        summary = meter.report()["chain_to_recv"]
        self.assertEqual(summary["n"], 3)
        self.assertEqual(summary["p50_ms"], 500)

    def test_candidate_books_parse_and_rank_causally(self) -> None:
        raw = {
            "size_sol": 0.05,
            "books": [
                {"id": "buy_all", "kind": "baseline", "exit": "hold_30s", "max_concurrent": 3, "daily_loss_sol": 0.2, "creator_cooldown_s": 0, "token_cooldown_s": 0},
                {"id": "buyers8_top5_ladder2x", "kind": "laya", "point": "buyers_8", "top_frac": 0.05, "model": "barrier", "exit": "ladder_2x_t30"},
                {"id": "t30_top1_hold30", "kind": "laya", "point": "30", "top_frac": 0.01, "model": "entry", "exit": "hold_30s"},
                {"id": "migrate_hold_30s", "kind": "migrate", "exit": "hold_30s"},
            ],
        }
        books = books_from_config(raw)
        by_id = {book.book_id: book for book in books}
        self.assertEqual(by_id["buyers8_top5_ladder2x"].resolved_exit("hold_30s").rule_id, "ladder_2x_t30")
        self.assertIs(by_id["buyers8_top5_ladder2x"].resolved_exit("hold_30s"), LADDER_RULES[0])
        self.assertEqual(by_id["t30_top1_hold30"].point, "30")
        self.assertEqual(by_id["migrate_hold_30s"].exit_rule, "hold_30s")
        window = _RankWindow(0.05, cap=100)
        self.assertTrue(all(window.consider(0.1) == "warmup" for _ in range(19)))
        self.assertEqual(window.consider(0.9), "take")
        self.assertEqual(window.consider(0.05), "below")
        narrow = _RankWindow(0.01, cap=200)
        self.assertTrue(all(narrow.consider(0.2) == "warmup" for _ in range(99)))
        self.assertEqual(narrow.consider(0.99), "take")
        creates, rows = _fixture()
        engine = replay_rows(
            [creates["MintA"]],
            rows,
            [by_id["t30_top1_hold30"]],
            tape_end_ms=TAPE_END,
            kill_file=Path("/tmp/forward-paper-point"),
            offsets_ms=(30_000,),
        )
        clocks = {row["decision_t_ms"] - T0 for row in engine.decisions}
        self.assertEqual(clocks, {30_000})
        self.assertTrue(all(row["reason"] == "no_model" for row in engine.decisions))

    def test_early_zstd_close_is_not_a_failure(self) -> None:
        class _Proc:
            def wait(self, timeout: int = 0) -> int:
                return -13

            def kill(self) -> None:
                raise AssertionError("sigpipe should not be killed")

        stream = _ZstdText.__new__(_ZstdText)
        stream._proc = _Proc()
        stream._text = tempfile.TemporaryFile()
        stream.close()


def _attn(mint: str, t_ms: int, kind: str = "dex_boost", *, snapshot: bool = False) -> dict[str, object]:
    return {"type": "attention", "mint": mint, "kind": kind, "t_first_ms": t_ms, "rank": 4, "snapshot": snapshot}


class SwingBookTests(unittest.TestCase):
    def test_freeze_is_the_sample_end_and_config_cannot_move_it_earlier(self) -> None:
        import time

        self.assertEqual(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(SWING_FREEZE_MS / 1000)), SWING_FREEZE_AT)
        raw = {
            "books": [
                {"id": "attn_first_hold_60m", "kind": "swing", "point": "attn", "model": "none", "exit": "hold_60m"},
                {
                    "id": "mig15_top20_tp50_sl30",
                    "kind": "swing",
                    "point": "mig_15",
                    "top_frac": 0.2,
                    "model": "swing",
                    "exit": "tp50_sl30",
                },
            ]
        }
        books = books_from_config(raw)
        by_id = {book.book_id: book for book in books}
        self.assertEqual(by_id["attn_first_hold_60m"].freeze_ms, SWING_FREEZE_MS)
        self.assertEqual(by_id["attn_first_hold_60m"].resolved_exit("hold_30s").hold_ms, 3_600_000)
        swing_tp = by_id["mig15_top20_tp50_sl30"].resolved_exit("hold_30s")
        curve_tp = BookSpec("migrate_tp50_sl30", "migrate", "tp50_sl30").resolved_exit("hold_30s")
        self.assertEqual(swing_tp.rule_id, "tp50_sl30")
        self.assertEqual(swing_tp.max_hold_ms, 4 * 60 * 60 * 1000)
        self.assertEqual(curve_tp.max_hold_ms, 30 * 60 * 1000)
        self.assertIsNot(swing_tp, curve_tp)
        later = books_from_config({**raw, "swing_freeze_ms": SWING_FREEZE_MS + 1000, "swing_freeze_at": "2026-09-25T18:25:58Z"})
        self.assertEqual(later[0].freeze_ms, SWING_FREEZE_MS + 1000)
        with self.assertRaises(RiskConfigError):
            books_from_config({**raw, "swing_freeze_ms": SWING_FREEZE_MS - 1})
        with self.assertRaises(RiskConfigError):
            books_from_config({**raw, "swing_freeze_ms": None})
        shipped = books_from_config(
            __import__("json").loads(Path("scripts/mal-core/forward-paper.json").read_text(encoding="utf-8"))
        )
        ids = {book.book_id for book in shipped}
        self.assertIn("attn_first_hold_60m", ids)
        self.assertIn("mig15_top20_tp50_sl30", ids)
        window = _RankWindow(0.20)
        self.assertTrue(all(window.consider(0.1) == "warmup" for _ in range(4)))
        self.assertEqual(window.consider(0.9), "take")

    def test_attention_is_after_migration_and_after_the_freeze(self) -> None:
        specs = books_from_config(
            {
                "books": [
                    {"id": "attn_first_hold_60m", "kind": "swing", "point": "attn", "model": "none", "exit": "hold_60m", "max_concurrent": 3, "daily_loss_sol": 0.2},
                    {"id": "mig15_top20_tp50_sl30", "kind": "swing", "point": "mig_15", "top_frac": 0.2, "model": "swing", "exit": "tp50_sl30", "max_concurrent": 3, "daily_loss_sol": 0.2},
                ]
            }
        )
        base = SWING_FREEZE_MS + 60_000
        creates = [
            _create("MintFreeze", SWING_FREEZE_MS - 120_000, creator="CreatorF"),
            _create("MintEarly", base, creator="CreatorE"),
            _create("MintLive", base + 20_000, creator="CreatorL"),
        ]
        rows = [
            _trade("MintFreeze", SWING_FREEZE_MS - 60_000, venue="pumpswap", trader="MigF", quote=70_000_000_000, base=B0 // 2, slot=2),
            _trade("MintEarly", base + 8_000, venue="pumpswap", trader="MigE", quote=70_000_000_000, base=B0 // 2, slot=3),
            _trade("MintLive", base + 21_000, venue="pumpswap", trader="MigL", quote=70_000_000_000, base=B0 // 2, slot=4),
        ]
        attention = [
            _attn("MintFreeze", SWING_FREEZE_MS),
            _attn("MintEarly", base + 1_000),
            _attn("MintEarly", base + 1_000, "pump_live", snapshot=True),
            _attn("MintLive", base + 25_000),
        ]
        engine = replay_rows(
            creates,
            rows,
            specs,
            tape_end_ms=base + 20 * 60_000,
            kill_file=Path("/tmp/forward-paper-swing-freeze"),
            attention_rows=attention,
            offsets_ms=(5_000,),
        )
        attn = [row for row in engine.decisions if row["book"] == "attn_first_hold_60m"]
        reasons = {(row["mint"], row["reason"], row["trigger"]) for row in attn}
        self.assertIn(("MintFreeze", "before_freeze", "attn:dex_boost"), reasons)
        self.assertTrue(all(row["freeze_ms"] == SWING_FREEZE_MS and row["freeze_at"] == SWING_FREEZE_AT for row in attn))
        self.assertFalse(any(row["mint"] == "MintEarly" and row["trigger"] == "attn:dex_boost" for row in attn))
        self.assertFalse(any(row["trigger"] == "attn:pump_live" for row in attn))
        live = [row for row in attn if row["mint"] == "MintLive"]
        ceiling_live = [row for row in live if row.get("ledger") == "ceiling"]
        shadow_live = [row for row in live if row.get("ledger") == "shadow"]
        self.assertEqual([(row["action"], row["reason"]) for row in ceiling_live], [("enter", None)])
        self.assertEqual([(row["action"], row["reason"]) for row in shadow_live], [("enter", None)])
        mig = [row for row in engine.decisions if row["book"] == "mig15_top20_tp50_sl30"]
        self.assertTrue(mig)
        self.assertTrue(all(row["trigger"] == "mig_15" for row in mig))
        self.assertTrue(all(row["reason"] == "no_model" for row in mig))
        self.assertNotIn("mig_15", {row["trigger"] for row in attn})
        self.assertGreater(mig[0]["decision_t_ms"], SWING_FREEZE_MS)

    def test_hold_60m_keeps_the_slot_and_logs_max_concurrent(self) -> None:
        spec = books_from_config(
            {
                "books": [
                    {
                        "id": "attn_first_hold_60m",
                        "kind": "swing",
                        "point": "attn",
                        "model": "none",
                        "exit": "hold_60m",
                        "max_concurrent": 1,
                        "daily_loss_sol": 0.2,
                        "creator_cooldown_s": 0,
                        "token_cooldown_s": 0,
                    }
                ]
            }
        )[0]
        base = SWING_FREEZE_MS + 120_000
        engine = ForwardEngine(
            [spec],
            kill_file=Path("/tmp/forward-paper-swing-hold"),
            tape_end_ms=base + 80 * 60_000,
            retain_rows=True,
        )
        engine.push_create(_create("MintA", base, creator="CreatorA"))
        engine.push_print(*_parsed("MintA", base + 1_000))
        engine.push_attention(_attn("MintA", base + 5_000))
        engine.drain_until(base + 50 * 60_000)
        run = engine.books[0]
        self.assertIn("MintA", run.open)
        self.assertEqual(run.open["MintA"].rule.hold_ms, 3_600_000)
        self.assertEqual(run.open["MintA"].rule.rule_id, "hold_60m")
        engine.push_create(_create("MintB", base + 50 * 60_000, creator="CreatorB"))
        engine.push_print(*_parsed("MintB", base + 50 * 60_000 + 1_000))
        engine.push_attention(_attn("MintB", base + 50 * 60_000 + 2_000))
        engine.drain_until(base + 51 * 60_000)
        skipped = [row for row in engine.decisions if row["mint"] == "MintB"]
        self.assertTrue(skipped)
        self.assertEqual(skipped[0]["reason"], "max_concurrent")
        self.assertEqual(skipped[0]["action"], "skip")
        self.assertIn("MintA", run.open)
        engine.drain_until(base + 70 * 60_000, final=True)
        self.assertNotIn("MintA", run.open)
        closed = [row for row in engine.positions if row["mint"] == "MintA" and row["event"] == "close" and row.get("ledger") == "ceiling"]
        self.assertTrue(closed)
        self.assertEqual(closed[0]["exit_rule"], "hold_60m")
        shadow_closed = [row for row in engine.positions if row["mint"] == "MintA" and row["event"] == "close" and row.get("ledger") == "shadow"]
        self.assertTrue(shadow_closed)

    def test_shadow_fills_past_the_concurrent_and_daily_loss_caps(self) -> None:
        spec = BookSpec(
            "gated",
            "baseline",
            "hold_30s",
            max_concurrent=1,
            daily_loss_lamports=CEILING_DAILY_LOSS_LAMPORTS,
            creator_cooldown_ms=0,
            token_cooldown_ms=0,
        )
        engine = ForwardEngine([spec], kill_file=Path("/tmp/forward-paper-shadow"), tape_end_ms=T0 + 10_000, retain_rows=True)
        run = engine.books[0]
        run.open = {f"m{i}": None for i in range(CEILING_MAX_CONCURRENT)}  # type: ignore[assignment]
        self.assertEqual(
            engine._risk_reason(run, "new", None, T0, HARD_MAX_POSITION_LAMPORTS, ledger=run.ceiling, capped=True),
            "max_concurrent",
        )
        self.assertIsNone(
            engine._risk_reason(run, "new", None, T0, HARD_MAX_POSITION_LAMPORTS, ledger=run.shadow, capped=False)
        )
        self.assertEqual(
            engine._risk_reason(run, "new", None, T0, HARD_MAX_POSITION_LAMPORTS + 1, ledger=run.shadow, capped=False),
            "max_position_size",
        )
        run.open.clear()
        engine.push_create(_create("MintA", T0, creator="CreatorA"))
        engine.push_create(_create("MintB", T0 + 1_000, creator="CreatorB"))
        engine.push_print(*_parsed("MintA", T0 - 1_000))
        engine.drain_until(T0 + 10_000, final=True)
        ceiling = [row for row in engine.decisions if row.get("ledger") == "ceiling" and row["book"] == "gated"]
        shadow = [row for row in engine.decisions if row.get("ledger") == "shadow" and row["book"] == "gated"]
        self.assertTrue(any(row["mint"] == "MintB" and row["reason"] == "max_concurrent" for row in ceiling))
        self.assertTrue(any(row["mint"] == "MintB" and row["action"] == "enter" for row in shadow))
        self.assertEqual(len(run.open), 1)
        self.assertEqual(len(run.shadow.open), 2)
        fresh = ForwardEngine([spec], kill_file=Path("/tmp/forward-paper-shadow-loss"), tape_end_ms=T0 + 10_000, retain_rows=True)
        blocked = fresh.books[0]
        blocked.day = __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime(T0 / 1000))
        blocked.day_pnl = -CEILING_DAILY_LOSS_LAMPORTS
        fresh.push_create(_create("MintC", T0, creator="CreatorC"))
        fresh.push_print(*_parsed("MintC", T0 - 1_000))
        fresh.drain_until(T0 + 5_000, final=True)
        self.assertTrue(any(row.get("ledger") == "ceiling" and row["reason"] == "daily_loss_cap" for row in fresh.decisions))
        self.assertTrue(any(row.get("ledger") == "shadow" and row["action"] == "enter" for row in fresh.decisions))
        snap = fresh.summary(T0)
        book = snap["books"]["gated"]
        self.assertEqual(snap["promotion"], "shadow")
        self.assertEqual(snap["capacity"], "ceiling")
        self.assertEqual(book["promote_ledger"], "shadow")
        self.assertEqual(book["capacity_ledger"], "ceiling")
        self.assertGreater(book["shadow"]["open"] + book["shadow"]["closed"], book["open"] + book["closed"])
        with self.assertRaises(RiskConfigError):
            books_from_config({"books": [{"id": "wide", "kind": "baseline", "exit": "hold_30s", "max_concurrent": 9}]})

    def test_stale_decision_is_dropped_and_a_fresh_one_can_fill(self) -> None:
        spec = BookSpec("buy_all", "baseline", "hold_30s", creator_cooldown_ms=0, token_cooldown_ms=0)
        late = ForwardEngine(
            [spec],
            kill_file=Path("/tmp/forward-paper-stale"),
            latency=LatencyMeter(now_ms=lambda: T0 + 60_000),
            retain_rows=True,
            tape_end_ms=T0 + 120_000,
        )
        late.push_create(_create("MintA", T0))
        late.push_print(*_parsed("MintA", T0))
        late.drain_until(T0 + 5_000, final=True)
        self.assertGreater(late.stale_dropped, 0)
        self.assertTrue(any(row["reason"] == "stale_recv" for row in late.decisions))
        self.assertFalse([row for row in late.positions if row["event"] == "open"])
        self.assertEqual(STALE_ACTION_MS, 5_000)

        fresh = ForwardEngine(
            [spec],
            kill_file=Path("/tmp/forward-paper-fresh"),
            latency=LatencyMeter(now_ms=lambda: T0 + 100),
            retain_rows=True,
            tape_end_ms=T0 + 120_000,
            promotion_live_ms=T0,
        )
        fresh.push_create(_create("MintA", T0))
        fresh.push_print(*_parsed("MintA", T0 - 1_000))
        fresh.drain_until(T0 + 5_000, final=True)
        self.assertEqual(fresh.stale_dropped, 0)
        self.assertTrue(any(row["action"] == "enter" for row in fresh.decisions))

    def test_flat_fail_rate_books_a_miss_instead_of_a_fill(self) -> None:
        self.assertEqual(DEFAULT_FAIL_RATE, 0.15)
        spec = BookSpec("buy_all", "baseline", "hold_30s", creator_cooldown_ms=0, token_cooldown_ms=0)
        engine = ForwardEngine(
            [spec],
            kill_file=Path("/tmp/forward-paper-fail"),
            retain_rows=True,
            tape_end_ms=T0 + 120_000,
            fail_rate=1.0,
        )
        engine.push_create(_create("MintA", T0))
        engine.push_print(*_parsed("MintA", T0 - 1_000))
        engine.drain_until(T0 + 5_000, final=True)
        self.assertFalse([row for row in engine.positions if row["event"] == "open"])
        misses = [row for row in engine.positions if row["event"] == "miss"]
        self.assertTrue(misses)
        self.assertEqual(misses[0]["entry_status"], "missed_landing")
        source = Path("tools/forward_paper.py").read_text(encoding="utf-8")
        self.assertIn("fail_rate=DEFAULT_FAIL_RATE", source)
        self.assertNotIn("paper_fail_pressure", source)

    def test_flat_fail_miss_logs_the_discarded_fill_like_an_open_row(self) -> None:
        """The coin-flipped miss carries the fill it discarded, with the same
        values an `open` row gets when the flip lands, so the pressure stamp
        can price it offline. Reporting only: pnl stays the priority cost."""
        spec = BookSpec("buy_all", "baseline", "hold_30s", creator_cooldown_ms=0, token_cooldown_ms=0)

        def run(fail_rate: float) -> list[dict]:
            engine = ForwardEngine(
                [spec],
                kill_file=Path("/tmp/forward-paper-fail"),
                retain_rows=True,
                tape_end_ms=T0 + 120_000,
                fail_rate=fail_rate,
            )
            engine.push_create(_create("MintA", T0))
            engine.push_print(*_parsed("MintA", T0 - 1_000))
            engine.drain_until(T0 + 5_000, final=True)
            return engine.positions

        opens = [r for r in run(0.0) if r["event"] == "open"]
        misses = [r for r in run(1.0) if r["event"] == "miss"]
        self.assertTrue(opens and misses)
        opened, miss = opens[0], misses[0]
        self.assertIs(miss["counterfactual_fill"], True)
        for key in ("applied_latency_ms", "size_lamports", "exit_rule", "entry_venue", "entry_spot_sol", "entry_tokens_raw"):
            self.assertEqual(miss[key], opened[key], key)
        self.assertEqual(miss["entry_t_ms"], opened["t_entry_ms"])
        self.assertEqual(miss["pnl_lamports"], miss["attempt_cost_lamports"])

    def test_void_window_excludes_post_95_until_guard_live(self) -> None:
        live = VOID_FROM_MS + 86_400_000
        self.assertTrue(decision_counts_for_promotion(VOID_FROM_MS - 1, live))
        self.assertFalse(decision_counts_for_promotion(VOID_FROM_MS, live))
        self.assertFalse(decision_counts_for_promotion(live - 1, None))
        self.assertTrue(decision_counts_for_promotion(live, live))
        clock = clean_clock(VOID_FROM_MS)
        self.assertTrue(clock["clean_start"].endswith("T00:00:00Z"))
        self.assertTrue(clock["kill_review_at"].endswith("T05:00:00Z"))

    def test_promotion_score_excludes_the_void_window(self) -> None:
        live = VOID_FROM_MS + 86_400_000
        spec = BookSpec("buy_all", "baseline", "hold_30s", creator_cooldown_ms=0, token_cooldown_ms=0)
        engine = ForwardEngine(
            [spec],
            kill_file=Path("/tmp/forward-paper-promo-void"),
            promotion_live_ms=live,
        )
        run = engine.books[0]
        run.shadow.realized = [10, 999, 20]
        run.shadow.realized_decision_t_ms = [VOID_FROM_MS - 1, VOID_FROM_MS, live]
        run.ceiling.realized = [999]
        run.ceiling.realized_decision_t_ms = [VOID_FROM_MS]
        memory = engine.summary(live)
        book = memory["books"]["buy_all"]
        self.assertEqual(book["shadow"]["realized"]["n"], 2)
        self.assertEqual(book["shadow"]["realized"]["total_lamports"], 30)
        self.assertEqual(book["realized"]["n"], 1)
        self.assertEqual(book["realized"]["total_lamports"], 999)

        rows = [
            {"event": "close", "ledger": "shadow", "book": "buy_all", "decision_t_ms": VOID_FROM_MS - 1, "pnl_lamports": 10},
            {"event": "close", "ledger": None, "book": "buy_all", "decision_t_ms": VOID_FROM_MS - 5_000, "pnl_lamports": 5},
            {"event": "close", "ledger": "shadow", "book": "buy_all", "decision_t_ms": VOID_FROM_MS, "pnl_lamports": 999},
            {"event": "miss", "ledger": "shadow", "book": "buy_all", "decision_t_ms": live - 1, "pnl_lamports": 888},
            {"event": "close", "ledger": "shadow", "book": "buy_all", "decision_t_ms": live, "pnl_lamports": 20},
            {"event": "close", "ledger": "ceiling", "book": "buy_all", "decision_t_ms": VOID_FROM_MS - 1, "pnl_lamports": 777},
            {"event": "open", "ledger": "shadow", "book": "buy_all", "decision_t_ms": VOID_FROM_MS - 1, "pnl_lamports": 111},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "positions.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            before = path.read_bytes()
            engine.positions_path = path
            scored = engine.summary(live)
            shadow = scored["books"]["buy_all"]["shadow"]["realized"]
            self.assertEqual(shadow["n"], 3)
            self.assertEqual(shadow["total_lamports"], 35)
            self.assertEqual(scored["books"]["buy_all"]["realized"]["total_lamports"], 999)
            self.assertEqual(path.read_bytes(), before)

    def test_follower_does_not_read_a_whole_hour_at_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.jsonl"
            line = b'{"t_recv_ms":1}\n'
            path.write_bytes(line * (READ_CHUNK_BYTES // len(line) + 10))
            follower = _Follower(path, 0)
            first = follower.read_exact()
            self.assertTrue(first)
            self.assertLess(follower.offset, path.stat().st_size)
            self.assertLessEqual(follower.offset, READ_CHUNK_BYTES)

    def test_attention_tail_starts_at_eof_and_reads_new_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tape = root / "trades"
            creates = root / "jsonl"
            attn = root / "attention"
            tape.mkdir()
            creates.mkdir()
            attn.mkdir()
            hour = __import__("time").strftime("%Y-%m-%dT%H", __import__("time").gmtime())
            path = attn / f"attention-{hour}.jsonl"
            path.write_text('{"mint":"Old","kind":"dex_boost","t_first_ms":1}\n', encoding="utf-8")
            tail = DirectoryTail(tape, creates, {}, attn)
            self.assertEqual(tail.poll(), [])
            with path.open("a", encoding="utf-8") as fh:
                fh.write('{"mint":"New","kind":"dex_boost","t_first_ms":2}\n')
            got = tail.poll()
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0][0], "attention")
            self.assertEqual(got[0][1]["mint"], "New")


class FeeSensitivityTests(unittest.TestCase):
    def test_priority_grid_keeps_the_booked_priority(self) -> None:
        self.assertEqual(priority_sides_for_event("miss", None), 1)
        self.assertEqual(priority_sides_for_event("close", "realized"), 2)
        self.assertEqual(priority_sides_for_event("close", "no_exit_liquidity"), 2)
        self.assertIsNone(priority_sides_for_event("open", None))
        miss = priority_grid(-PRIORITY_FEE_LAMPORTS, 1)
        close = priority_grid(-2 * PRIORITY_FEE_LAMPORTS, 2)
        self.assertEqual(miss["0.001"], -PRIORITY_FEE_LAMPORTS)
        self.assertEqual(miss["0.0001"], -100_000)
        self.assertEqual(miss["0.0003"], -300_000)
        self.assertEqual(close["0.001"], -2 * PRIORITY_FEE_LAMPORTS)
        self.assertEqual(close["0.0001"], -200_000)

    def test_promotion_ignores_the_sensitivity_field(self) -> None:
        rows = [
            {
                "event": "close",
                "ledger": "shadow",
                "book": "migrate_hold_30s",
                "decision_t_ms": VOID_FROM_MS - 1,
                "pnl_lamports": -50_000,
                "exit_status": "realized",
                "fee_sensitivity": {"direct": {"0.0001": 9_000_000_000}, "portal": {"0.001": 9_000_000_000}},
            }
        ]
        scored = promotion_pnls_by_book(rows, None)
        self.assertEqual(scored["migrate_hold_30s"], [-50_000])

    def test_migrate_scoreboard_columns_do_not_change_fills_or_ceilings(self) -> None:
        self.assertEqual(HARD_MAX_POSITION_LAMPORTS, 50_000_000)
        self.assertEqual(CEILING_DAILY_LOSS_LAMPORTS, 200_000_000)
        self.assertEqual(CEILING_MAX_CONCURRENT, 3)
        self.assertEqual(PORTAL_FEE_PPM, 5_000)
        creates, rows = _fixture()
        books = [
            BookSpec("buy_all", "baseline", "hold_30s", max_concurrent=None, daily_loss_lamports=None, creator_cooldown_ms=0, token_cooldown_ms=0),
            BookSpec("migrate_hold_30s", "migrate", "hold_30s", creator_cooldown_ms=0, token_cooldown_ms=0),
            BookSpec("migrate_tp50_sl30", "migrate", "tp50_sl30", creator_cooldown_ms=0, token_cooldown_ms=0),
        ]
        engine = replay_rows(
            creates.values(),
            rows,
            books,
            tape_end_ms=TAPE_END,
            kill_file=Path("/tmp/forward-paper-fee-sens"),
            offsets_ms=OFFSETS,
        )
        buy_rows = [row for row in engine.positions if row["book"] == "buy_all" and row["event"] in ("close", "miss")]
        self.assertTrue(buy_rows)
        self.assertTrue(all("fee_sensitivity" not in row for row in buy_rows))
        migrate = [
            row
            for row in engine.positions
            if row["book"] in ("migrate_hold_30s", "migrate_tp50_sl30") and row["event"] in ("close", "miss") and row["ledger"] == "shadow"
        ]
        self.assertTrue(migrate)
        for row in migrate:
            pnl = row["pnl_lamports"]
            sens = row["fee_sensitivity"]
            self.assertTrue(sens["reporting_only"])
            self.assertEqual(sens["portal"]["0.001"], pnl)
            self.assertIn("0.0001", sens["portal"])
            self.assertIn("0.0003", sens["portal"])
            if row["event"] == "miss":
                self.assertEqual(sens["direct"], sens["portal"])
            elif row["event"] == "close":
                self.assertIsInstance(sens["direct"], dict)
                self.assertIn("0.001", sens["direct"])
        summary = engine.summary(TAPE_END)
        self.assertNotIn("fee_sensitivity", summary["books"]["buy_all"])
        held = summary["books"]["migrate_hold_30s"]["fee_sensitivity"]
        self.assertEqual(held["promotion_pnl"], "pnl_lamports")
        self.assertEqual(held["portal_fee_ppm"], {"direct": 0, "portal": PORTAL_FEE_PPM})
        portal_001 = held["routes"]["portal"]["0.001"]
        shadow = summary["books"]["migrate_hold_30s"]["shadow"]["realized"]
        self.assertEqual(portal_001["n"], shadow["n"])
        self.assertAlmostEqual(portal_001["mean_sol"], shadow["mean_sol"])
        self.assertEqual(summary["books"]["migrate_hold_30s"]["realized"]["total_lamports"], sum(engine.books[1].realized))
        historical = [
            {"event": "miss", "ledger": "shadow", "book": "migrate_hold_30s", "decision_t_ms": VOID_FROM_MS - 1, "pnl_lamports": -1_000_000},
            {
                "event": "close",
                "ledger": "shadow",
                "book": "migrate_hold_30s",
                "decision_t_ms": VOID_FROM_MS - 2,
                "exit_status": "realized",
                "pnl_lamports": -2_000_000,
            },
        ]
        report = migrate_fee_sensitivity_summary(historical, None, ["migrate_hold_30s"])
        route = report["migrate_hold_30s"]["routes"]
        self.assertEqual(route["portal"]["0.001"]["n"], 2)
        self.assertEqual(route["portal"]["0.0001"]["mean_sol"], (-100_000 + -200_000) / 2 / 1_000_000_000)
        self.assertEqual(route["direct"]["0.001"]["n"], 0)


def _parsed(mint: str, t_ms: int):
    row = _trade(mint, t_ms, venue="pumpswap", trader="Pool", quote=70_000_000_000, base=B0 // 2, slot=20, sol=2_000_000_000, token=5_000_000)
    parsed = flow_from_tape_row(row)
    assert parsed is not None
    pr_mint, pr = parsed
    return pr_mint, pr, t_ms // 1000


if __name__ == "__main__":
    unittest.main()


class IntentsFileTests(unittest.TestCase):
    """DEC-019: decision-time intents for the probe executor. A new file, nothing else may change."""

    def _run(self, tmp: Path, intents: bool) -> tuple[bytes, bytes, Path]:
        from tools.forward_paper import JsonlLog

        creates, rows = _fixture()
        books = [
            BookSpec("buy_all", "baseline", "hold_30s", max_concurrent=None, daily_loss_lamports=None, creator_cooldown_ms=0, token_cooldown_ms=0),
            BookSpec("migrate_hold_30s", "migrate", "hold_30s", creator_cooldown_ms=0, token_cooldown_ms=0),
            BookSpec("migrate_tp50_sl30", "migrate", "tp50_sl30", creator_cooldown_ms=0, token_cooldown_ms=0),
        ]
        logs = {"decisions": JsonlLog(tmp / "decisions.jsonl"), "positions": JsonlLog(tmp / "positions.jsonl")}
        if intents:
            logs["intents"] = JsonlLog(tmp / "intents.jsonl", fsync=True)
        replay_rows(
            creates.values(), rows, books, tape_end_ms=TAPE_END, kill_file=tmp / "KILL", offsets_ms=OFFSETS, logs=logs
        )
        for log in logs.values():
            log.close()
        return (tmp / "decisions.jsonl").read_bytes(), (tmp / "positions.jsonl").read_bytes(), tmp / "intents.jsonl"

    def test_intents_do_not_change_decisions_or_positions_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            off_dec, off_pos, off_int = self._run(Path(a), False)
            on_dec, on_pos, on_int = self._run(Path(b), True)
            self.assertTrue(off_dec and off_pos)
            self.assertEqual(off_dec, on_dec)
            self.assertEqual(off_pos, on_pos)
            self.assertFalse(off_int.exists())  # default off: no file

    def test_intent_rows_are_migrate_ceiling_only_and_carry_no_pnl(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dec_b, _pos, path = self._run(Path(d), True)
            rows = [json.loads(x) for x in path.read_text().splitlines()]
            self.assertTrue(rows)
            self.assertEqual({r["schema"] for r in rows}, {"forward_paper_intent_v1"})
            self.assertEqual({r["ledger"] for r in rows}, {"ceiling"})
            self.assertTrue({r["book"] for r in rows} <= {"migrate_hold_30s", "migrate_tp50_sl30"})
            for r in rows:
                self.assertEqual(
                    set(r), {"schema", "book", "ledger", "mint", "creator", "decision_t_ms", "written_ms", "trigger", "score"}
                )
                self.assertIsInstance(r["written_ms"], int)
            decs = [json.loads(x) for x in dec_b.decode().splitlines()]
            for r in rows:
                self.assertTrue(
                    any(
                        x["book"] == r["book"] and x["mint"] == r["mint"] and x["decision_t_ms"] == r["decision_t_ms"] and x["ledger"] == "ceiling"
                        for x in decs
                    )
                )

    def test_shipped_runner_config_enables_intents(self) -> None:
        cfg = json.loads((Path(__file__).resolve().parents[1] / "scripts/mal-fast/fast-forward-paper.json").read_text())
        self.assertIs(cfg["intents_file"], True)
