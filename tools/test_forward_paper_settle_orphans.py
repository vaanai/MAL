"""Offline orphan settlement: the equivalence proof plus orphan-detection unit tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.forward_paper import BookSpec, JsonlLog, replay_rows
from tools.forward_paper_settle_orphans import find_orphans, settle_orphans
from tools.paper_curve_math import DEFAULT_SLIPPAGE_CAP
from tools.paper_price_path import CreateSignal

T0 = 1_700_000_000_000


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
    quote: int = 40_000_000_000,
    base: int = 1_050_000_000_000_000,
    slot: int = 3,
    event_index: int = 1,
    trader: str = "Wallet",
    venue: str = "pump_bonding",
) -> dict[str, object]:
    price = quote / (base * 1000)
    return {
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


def _book() -> BookSpec:
    # kind=baseline enters on every create with no risk gate to route around
    # in the test; exit=hold_30s is a fixed-time exit, deterministic either
    # side of a restart.
    return BookSpec(
        "buy_all",
        "baseline",
        "hold_30s",
        max_concurrent=None,
        daily_loss_lamports=None,
        creator_cooldown_ms=0,
        token_cooldown_ms=0,
    )


def _tape() -> tuple[dict[str, CreateSignal], list[dict[str, object]]]:
    creates = {"MintA": _create("MintA", T0)}
    rows = [
        _trade("MintA", T0 + 5_000, quote=42_000_000_000, base=1_040_000_000_000_000, slot=2, event_index=1),
        # Exit-time (T0 + hold_30s) pool state: a different price than entry.
        _trade("MintA", T0 + 30_000, quote=48_000_000_000, base=1_010_000_000_000_000, slot=3, event_index=2),
        _trade("MintA", T0 + 60_000, quote=49_000_000_000, base=1_005_000_000_000_000, slot=4, event_index=3),
    ]
    return creates, rows


def _observe_rows(creates: dict[str, CreateSignal]) -> list[dict[str, object]]:
    return [
        {
            "stream": "subscribeNewToken",
            "mint": c.mint,
            "t_ws": c.t_signal_ms,
            "traderPublicKey": c.creator,
            "signature": c.signature,
            "vSolInBondingCurve": c.v_sol,
            "vTokensInBondingCurve": c.v_token_ui,
            "marketCapSol": c.mcap_sol,
            "initialBuy": c.initial_buy_ui,
            "solAmount": c.sol_amount,
        }
        for c in creates.values()
    ]


def _ladder_book() -> BookSpec:
    # kind=baseline enters on every create, same as _book(); exit=ladder_1_5x_t25
    # is a LadderRule, the branch settle_orphan used to crash on.
    return BookSpec(
        "ladder_all",
        "baseline",
        "ladder_1_5x_t25",
        max_concurrent=None,
        daily_loss_lamports=None,
        creator_cooldown_ms=0,
        token_cooldown_ms=0,
    )


def _ladder_tape() -> tuple[dict[str, CreateSignal], list[dict[str, object]]]:
    creates = {"MintL": _create("MintL", T0)}
    rows = [
        _trade("MintL", T0 + 5_000, quote=42_000_000_000, base=1_040_000_000_000_000, slot=2, event_index=1),
        # +100% off the first print: past ladder_1_5x_t25's scale_ret=0.50, scales out half.
        _trade("MintL", T0 + 30_000, quote=84_000_000_000, base=1_040_000_000_000_000, slot=3, event_index=2),
        # Drops back under peak*(1-trail=0.25): trails the remainder out.
        _trade("MintL", T0 + 60_000, quote=60_000_000_000, base=1_040_000_000_000_000, slot=4, event_index=3),
    ]
    return creates, rows


def _migrate_book() -> BookSpec:
    return BookSpec(
        "migrate_tp50_sl30",
        "migrate",
        "tp50_sl30",
        max_concurrent=None,
        daily_loss_lamports=None,
        creator_cooldown_ms=0,
        token_cooldown_ms=0,
    )


def _migrate_tape() -> tuple[dict[str, CreateSignal], list[dict[str, object]]]:
    creates = {"MintM": _create("MintM", T0)}
    rows = [
        # First pumpswap print after create: the migrate trigger itself, and
        # also the entry reference price.
        _trade("MintM", T0 + 3_000, venue="pumpswap", quote=70_000_000_000, base=536_500_000_000_000, slot=20, event_index=1),
        # +200%: comfortably past tp50_sl30's 50% take-profit.
        _trade("MintM", T0 + 40_000, venue="pumpswap", quote=210_000_000_000, base=536_500_000_000_000, slot=21, event_index=2),
    ]
    return creates, rows


class OrphanDetectionTests(unittest.TestCase):
    def test_open_with_no_close_is_an_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "positions.jsonl"
            rows = [
                {
                    "schema": "forward_paper_position_v1",
                    "ledger": "ceiling",
                    "event": "open",
                    "book": "buy_all",
                    "mint": "MintA",
                    "decision_t_ms": T0,
                    "t_entry_ms": T0,
                },
                {
                    "schema": "forward_paper_position_v1",
                    "ledger": "ceiling",
                    "event": "open",
                    "book": "buy_all",
                    "mint": "MintB",
                    "decision_t_ms": T0,
                    "t_entry_ms": T0,
                },
                {
                    "schema": "forward_paper_position_v1",
                    "ledger": "ceiling",
                    "event": "close",
                    "book": "buy_all",
                    "mint": "MintB",
                    "decision_t_ms": T0,
                },
            ]
            path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            orphans = find_orphans(path)
            self.assertEqual([o["mint"] for o in orphans], ["MintA"])

    def test_miss_rows_are_not_orphans(self) -> None:
        """A `miss` event never became an `open`; it must not be treated as one."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "positions.jsonl"
            row = {
                "schema": "forward_paper_position_v1",
                "ledger": "ceiling",
                "event": "miss",
                "book": "buy_all",
                "mint": "MintC",
                "decision_t_ms": T0,
            }
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            self.assertEqual(find_orphans(path), [])


class SettleEquivalenceTests(unittest.TestCase):
    def test_offline_settlement_matches_uninterrupted_replay(self) -> None:
        """The key proof: settling an orphan offline gives the same pnl_lamports
        as the same tape run straight through in one process, never restarted."""
        creates, rows = _tape()
        book = _book()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tape_path = tmp_path / "trades-0.jsonl"
            tape_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            create_path = tmp_path / "observe-0.jsonl"
            create_rows = [
                {
                    "stream": "subscribeNewToken",
                    "mint": c.mint,
                    "t_ws": c.t_signal_ms,
                    "traderPublicKey": c.creator,
                    "signature": c.signature,
                    "vSolInBondingCurve": c.v_sol,
                    "vTokensInBondingCurve": c.v_token_ui,
                    "marketCapSol": c.mcap_sol,
                    "initialBuy": c.initial_buy_ui,
                    "solAmount": c.sol_amount,
                }
                for c in creates.values()
            ]
            create_path.write_text("\n".join(json.dumps(r) for r in create_rows) + "\n", encoding="utf-8")

            # 1) Reference: one uninterrupted process runs the full tape past the
            # hold_30s exit and books the real close.
            ref_positions = tmp_path / "positions_ref.jsonl"
            replay_rows(
                creates.values(),
                rows,
                [book],
                tape_end_ms=T0 + 90_000,
                kill_file=tmp_path / "KILL",
                logs={"decisions": JsonlLog(tmp_path / "decisions_ref.jsonl"), "positions": JsonlLog(ref_positions)},
            )
            # Both the ceiling (execution) and shadow (uncapped scoring) ledgers
            # enter on a baseline book; compare the ceiling ledger, the one the
            # promotion gate reads.
            ref_closes = [
                json.loads(line)
                for line in ref_positions.read_text(encoding="utf-8").splitlines()
                if json.loads(line).get("event") == "close" and json.loads(line).get("ledger") == "ceiling"
            ]
            self.assertEqual(len(ref_closes), 1)
            ref_pnl = ref_closes[0]["pnl_lamports"]
            self.assertIsInstance(ref_pnl, int)

            # 2) "Restart": a second process only sees the tape up through the
            # entry fill, then stops -- same as a runner killed mid-hold. Its
            # positions.jsonl has an `open` row and no `close` row: an orphan.
            orphan_positions = tmp_path / "positions_orphan.jsonl"
            replay_rows(
                creates.values(),
                rows,
                [book],
                tape_end_ms=T0 + 1_000,
                kill_file=tmp_path / "KILL",
                logs={
                    "decisions": JsonlLog(tmp_path / "decisions_orphan.jsonl"),
                    "positions": JsonlLog(orphan_positions),
                },
            )
            orphans = find_orphans(orphan_positions)
            self.assertEqual({o["ledger"] for o in orphans}, {"ceiling", "shadow"})
            for orphan in orphans:
                self.assertEqual(orphan["mint"], "MintA")
                self.assertEqual(orphan["entry_status"], "filled")

            # 3) Offline settlement, from the orphan rows plus the sealed tape only
            # -- no live engine, no risk gate, no wallet/creator state.
            config_path = tmp_path / "config.json"
            config_path.write_text(json.dumps({"slippage_cap": DEFAULT_SLIPPAGE_CAP, "books": []}), encoding="utf-8")
            settled_rows, skip_reasons = settle_orphans(
                orphans,
                tape_paths=[tape_path],
                create_paths=[create_path],
                slippage_cap=DEFAULT_SLIPPAGE_CAP,
                tape_end_ms=T0 + 90_000,
            )
            self.assertEqual(skip_reasons, {})
            self.assertEqual(len(settled_rows), 2)
            settled = next(r for r in settled_rows if r["ledger"] == "ceiling")
            self.assertTrue(settled["settled_offline"])
            self.assertEqual(settled["exit_status"], "realized")
            self.assertEqual(settled["mint"], "MintA")
            # The key proof: same pnl the uninterrupted run booked.
            self.assertEqual(settled["pnl_lamports"], ref_pnl)
            self.assertEqual(settled["exit_t_ms"], ref_closes[0]["exit_t_ms"])

    def test_ladder_settlement_matches_uninterrupted_replay(self) -> None:
        """Same proof as above, for a LadderRule book. Before this fix,
        settle_orphan always called simulate_exit and raised on a LadderRule
        (settle_orphan asserted an ExitRule shape it never had), which
        aborted the whole settlement run -- not just this book's orphans."""
        creates, rows = _ladder_tape()
        book = _ladder_book()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tape_path = tmp_path / "trades-0.jsonl"
            tape_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            create_path = tmp_path / "observe-0.jsonl"
            create_path.write_text("\n".join(json.dumps(r) for r in _observe_rows(creates)) + "\n", encoding="utf-8")

            ref_positions = tmp_path / "positions_ref.jsonl"
            replay_rows(
                creates.values(),
                rows,
                [book],
                tape_end_ms=T0 + 90_000,
                kill_file=tmp_path / "KILL",
                logs={"decisions": JsonlLog(tmp_path / "decisions_ref.jsonl"), "positions": JsonlLog(ref_positions)},
            )
            ref_closes = [
                json.loads(line)
                for line in ref_positions.read_text(encoding="utf-8").splitlines()
                if json.loads(line).get("event") == "close" and json.loads(line).get("ledger") == "ceiling"
            ]
            self.assertEqual(len(ref_closes), 1)
            ref_pnl = ref_closes[0]["pnl_lamports"]
            self.assertIsInstance(ref_pnl, int)
            self.assertEqual(ref_closes[0]["exit_status"], "realized")

            orphan_positions = tmp_path / "positions_orphan.jsonl"
            replay_rows(
                creates.values(),
                rows,
                [book],
                tape_end_ms=T0 + 1_000,
                kill_file=tmp_path / "KILL",
                logs={
                    "decisions": JsonlLog(tmp_path / "decisions_orphan.jsonl"),
                    "positions": JsonlLog(orphan_positions),
                },
            )
            orphans = find_orphans(orphan_positions)
            self.assertEqual({o["ledger"] for o in orphans}, {"ceiling", "shadow"})
            for orphan in orphans:
                self.assertEqual(orphan["mint"], "MintL")
                self.assertEqual(orphan["entry_status"], "filled")

            config_path = tmp_path / "config.json"
            config_path.write_text(json.dumps({"slippage_cap": DEFAULT_SLIPPAGE_CAP, "books": []}), encoding="utf-8")
            settled_rows, skip_reasons = settle_orphans(
                orphans,
                tape_paths=[tape_path],
                create_paths=[create_path],
                slippage_cap=DEFAULT_SLIPPAGE_CAP,
                tape_end_ms=T0 + 90_000,
            )
            self.assertEqual(skip_reasons, {})
            self.assertEqual(len(settled_rows), 2)
            settled = next(r for r in settled_rows if r["ledger"] == "ceiling")
            self.assertTrue(settled["settled_offline"])
            self.assertEqual(settled["exit_status"], "realized")
            self.assertEqual(settled["mint"], "MintL")
            self.assertEqual(settled["pnl_lamports"], ref_pnl)
            self.assertEqual(settled["exit_t_ms"], ref_closes[0]["exit_t_ms"])

    def test_migrate_tp50_sl30_settlement_matches_uninterrupted_replay(self) -> None:
        """Same proof again, for a migrate-kind book on the exit_rule=tp50_sl30
        in-sample cell (LAB_STATE / migrate-direct-prereg.md), not just
        hold_30s."""
        creates, rows = _migrate_tape()
        book = _migrate_book()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tape_path = tmp_path / "trades-0.jsonl"
            tape_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            create_path = tmp_path / "observe-0.jsonl"
            create_path.write_text("\n".join(json.dumps(r) for r in _observe_rows(creates)) + "\n", encoding="utf-8")

            ref_positions = tmp_path / "positions_ref.jsonl"
            replay_rows(
                creates.values(),
                rows,
                [book],
                tape_end_ms=T0 + 90_000,
                kill_file=tmp_path / "KILL",
                logs={"decisions": JsonlLog(tmp_path / "decisions_ref.jsonl"), "positions": JsonlLog(ref_positions)},
            )
            ref_closes = [
                json.loads(line)
                for line in ref_positions.read_text(encoding="utf-8").splitlines()
                if json.loads(line).get("event") == "close" and json.loads(line).get("ledger") == "ceiling"
            ]
            self.assertEqual(len(ref_closes), 1)
            ref_pnl = ref_closes[0]["pnl_lamports"]
            self.assertIsInstance(ref_pnl, int)
            self.assertEqual(ref_closes[0]["exit_status"], "realized")

            orphan_positions = tmp_path / "positions_orphan.jsonl"
            replay_rows(
                creates.values(),
                rows,
                [book],
                tape_end_ms=T0 + 4_000,
                kill_file=tmp_path / "KILL",
                logs={
                    "decisions": JsonlLog(tmp_path / "decisions_orphan.jsonl"),
                    "positions": JsonlLog(orphan_positions),
                },
            )
            orphans = find_orphans(orphan_positions)
            self.assertEqual({o["ledger"] for o in orphans}, {"ceiling", "shadow"})
            for orphan in orphans:
                self.assertEqual(orphan["mint"], "MintM")
                self.assertEqual(orphan["entry_status"], "filled")

            config_path = tmp_path / "config.json"
            config_path.write_text(json.dumps({"slippage_cap": DEFAULT_SLIPPAGE_CAP, "books": []}), encoding="utf-8")
            settled_rows, skip_reasons = settle_orphans(
                orphans,
                tape_paths=[tape_path],
                create_paths=[create_path],
                slippage_cap=DEFAULT_SLIPPAGE_CAP,
                tape_end_ms=T0 + 90_000,
            )
            self.assertEqual(skip_reasons, {})
            self.assertEqual(len(settled_rows), 2)
            settled = next(r for r in settled_rows if r["ledger"] == "ceiling")
            self.assertTrue(settled["settled_offline"])
            self.assertEqual(settled["exit_status"], "realized")
            self.assertEqual(settled["mint"], "MintM")
            self.assertEqual(settled["pnl_lamports"], ref_pnl)
            self.assertEqual(settled["exit_t_ms"], ref_closes[0]["exit_t_ms"])


class MixedFailureTests(unittest.TestCase):
    def test_one_forced_failure_does_not_stop_the_others(self) -> None:
        """The regression this PR fixes: one orphan raising inside settlement
        (a LadderRule going through the ExitRule-only path was the real
        instance) used to abort the whole run, so NO book got settlements --
        including books with nothing wrong with them. Force a raise for one
        orphan's book and confirm the rest still settle, and the failed one
        gets a `settled_offline: false` row with a `settle_error`, not a
        crash."""
        hold_creates, hold_rows = _tape()
        hold_book = _book()
        ladder_creates, ladder_rows = _ladder_tape()
        ladder_book = _ladder_book()
        creates = {**hold_creates, **ladder_creates}
        rows = hold_rows + ladder_rows
        books = [hold_book, ladder_book]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tape_path = tmp_path / "trades-0.jsonl"
            tape_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            create_path = tmp_path / "observe-0.jsonl"
            create_path.write_text("\n".join(json.dumps(r) for r in _observe_rows(creates)) + "\n", encoding="utf-8")

            orphan_positions = tmp_path / "positions_orphan.jsonl"
            replay_rows(
                creates.values(),
                rows,
                books,
                tape_end_ms=T0 + 1_000,
                kill_file=tmp_path / "KILL",
                logs={
                    "decisions": JsonlLog(tmp_path / "decisions_orphan.jsonl"),
                    "positions": JsonlLog(orphan_positions),
                },
            )
            # Both books enter both mints (baseline enters on every create);
            # narrow to the one pairing each mint's tape was designed for
            # (MintA's tape is a hold_30s fixture, MintL's is a ladder fixture
            # -- the ladder rule on MintA's tape would never hit its scale_ret
            # and would run out to its 30-minute deadline, censored on this
            # short tape, unrelated to what this test is proving).
            wanted = {("buy_all", "MintA"), ("ladder_all", "MintL")}
            orphans = [
                o
                for o in find_orphans(orphan_positions)
                if o["ledger"] == "ceiling" and (o["book"], o["mint"]) in wanted
            ]
            self.assertEqual({o["book"] for o in orphans}, {"buy_all", "ladder_all"})

            def _boom(*_args: object, **_kwargs: object) -> dict[str, object]:
                raise RuntimeError("forced failure for test")

            # Force only the hold_30s (ExitRule) path to blow up; the ladder
            # book's simulate_ladder call is untouched and should still settle.
            with mock.patch("tools.forward_paper_settle_orphans.simulate_exit", side_effect=_boom):
                settled_rows, skip_reasons = settle_orphans(
                    orphans,
                    tape_paths=[tape_path],
                    create_paths=[create_path],
                    slippage_cap=DEFAULT_SLIPPAGE_CAP,
                    tape_end_ms=T0 + 90_000,
                )

            self.assertEqual(skip_reasons, {})
            self.assertEqual(len(settled_rows), 2)
            by_book = {row["book"]: row for row in settled_rows}
            failed = by_book["buy_all"]
            self.assertFalse(failed["settled_offline"])
            self.assertIn("forced failure for test", failed["settle_error"])
            self.assertEqual(failed["mint"], "MintA")
            settled = by_book["ladder_all"]
            self.assertTrue(settled["settled_offline"])
            self.assertEqual(settled["exit_status"], "realized")
            self.assertEqual(settled["mint"], "MintL")


if __name__ == "__main__":
    unittest.main()
