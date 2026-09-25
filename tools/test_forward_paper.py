"""Forward paper: packet parity, fill reconcile, risk gate. No live tape."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from tools.forward_paper import (
    HARD_MAX_POSITION_LAMPORTS,
    SCHEMA_DECISION,
    BookSpec,
    ForwardEngine,
    LatencyMeter,
    ModelSlot,
    _RankWindow,
    CEILING_DAILY_LOSS_LAMPORTS,
    CEILING_MAX_CONCURRENT,
    RiskConfigError,
    books_from_config,
    offline_packets,
    reload_risk_config,
    reconcile_baseline,
    replay_rows,
    window_creates,
)
from tools.laya_v0 import LADDER_RULES
from tools.laya_v0 import FEATURE_NAMES
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


if __name__ == "__main__":
    unittest.main()
