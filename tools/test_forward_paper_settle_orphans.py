"""Offline orphan settlement: the equivalence proof plus orphan-detection unit tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
