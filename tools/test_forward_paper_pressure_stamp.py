"""Tests for tools.forward_paper_pressure_stamp -- synthetic fixtures only."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from tools import forward_paper_pressure_stamp as pstamp
from tools import latency_curve as lc
from tools import migrate_direct_oos as mdo
from tools.paper_curve_math import PRIORITY_FEE_LAMPORTS
from tools.paper_price_path import CreateSignal

T0 = 1_700_000_000_000


def _create(mint: str, t_ms: int) -> CreateSignal:
    return CreateSignal(
        mint=mint,
        t_signal_ms=t_ms,
        creator="CreatorA",
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
    venue: str = "pumpswap",
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


def _write_tape(tmp: Path, creates: dict[str, CreateSignal], rows: list[dict[str, object]]) -> tuple[Path, Path]:
    tape_path = tmp / "trades-0.jsonl"
    tape_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    create_path = tmp / "observe-0.jsonl"
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
    return tape_path, create_path


def _migrate_tape() -> tuple[dict[str, CreateSignal], list[dict[str, object]]]:
    """Three same-slot(20) buys (same_slot_buys=3, nearby SOL=6) then a later
    print at slot 22 -- no print lands exactly at slot 21, so the frozen
    cell's `landing_ms` falls back to the entry state's own `t_recv_ms`
    (tools/latency_curve.py `_slot_time`), matching what this tool's
    `state_as_of(path, t_entry_ms=<that same ms>)` resolves to. This is what
    makes the equivalence test's entry_slot/pressure-window inputs agree
    with `tools.migrate_direct_oos.cell_rows`'s own `_pressure` call exactly.
    """
    creates = {"MintM": _create("MintM", T0)}
    rows = [
        _trade("MintM", T0 + 1000, slot=20, event_index=1, sol=1_000_000_000, quote=70_000_000_000, base=536_500_000_000_000),
        _trade("MintM", T0 + 1200, slot=20, event_index=2, sol=2_000_000_000, quote=71_000_000_000, base=536_400_000_000_000),
        _trade("MintM", T0 + 1400, slot=20, event_index=3, sol=3_000_000_000, quote=72_000_000_000, base=536_300_000_000_000),
        _trade("MintM", T0 + 6400, slot=22, event_index=4, sol=500_000_000, quote=72_500_000_000, base=536_200_000_000_000),
    ]
    return creates, rows


class EquivalenceTests(unittest.TestCase):
    """(a) Equivalence with tools.migrate_direct_oos's own pressure path,
    same tape, same synthetic migrate tp50_sl30 attempt (size_i=0, 0.05 SOL)."""

    def test_matches_migrate_direct_oos_pressure_path(self) -> None:
        creates, rows = _migrate_tape()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tape_path, create_path = _write_tape(tmp_path, creates, rows)

            paths = pstamp.rebuild_mint_paths(
                {"MintM"}, tape_paths=[tape_path], create_paths=[create_path], tape_end_ms=T0 + 10_000_000
            )
            fills = paths["MintM"].prints
            trigger_slot = 20
            ref = mdo._ref_migrate(fills, trigger_slot)
            produced = mdo.cell_rows(
                fills,
                trigger_slot=trigger_slot,
                trigger_block_ms=T0 + 1000,
                ref_price=ref,
                tape_through_ms=T0 + 10_000_000,
                size_indexes=(0,),
            )
            self.assertEqual(len(produced), 1)
            size_i, status, sides, net0, _gross, buys, nearby = produced[0]
            self.assertEqual(status, mdo.SEND if hasattr(mdo, "SEND") else 1)
            self.assertEqual(sides, 2)
            self.assertEqual(buys, 3)
            self.assertEqual(nearby, 6_000_000_000)

            curve = mdo._curve()  # frozen slope-scale-1 curve
            p_frozen = curve.p(lc.Pressure(buys, nearby))
            ground_truth = lc.mixed_net(net0, sides, status, PRIORITY_FEE_LAMPORTS, p_frozen)

            # This tool's own row: pnl_lamports is what a real forward-paper
            # close row would carry -- net0 with both priority sides already
            # charged (tools/forward_paper.py `priority_sides_for_event`
            # gives 2 sides for a realized/no_exit_liquidity close).
            pnl_lamports = net0 - PRIORITY_FEE_LAMPORTS * sides
            row = {
                "schema": "forward_paper_position_v1",
                "ledger": "shadow",
                "event": "close",
                "book": "migrate_tp50_sl30",
                "mint": "MintM",
                "decision_t_ms": T0 + 1400,
                "t_entry_ms": T0 + 1400,
                "exit_status": "realized",
                "pnl_lamports": pnl_lamports,
            }
            out, counts = pstamp.stamp_rows(
                [row], tape_paths=[tape_path], create_paths=[create_path], tape_end_ms=T0 + 10_000_000
            )
            self.assertEqual(counts, {"send": 1})
            self.assertEqual(len(out), 1)
            stamped = out[0]
            self.assertNotIn("pressure_error", stamped)
            self.assertEqual(stamped["same_slot_buys"], 3)
            self.assertEqual(stamped["nearby_buy_lamports"], 6_000_000_000)
            self.assertAlmostEqual(stamped["p_fail_scale_1"], p_frozen, places=12)
            self.assertEqual(stamped["pressure_scale_1_pnl_lamports"], int(round(ground_truth)))


class BookKindCoverageTests(unittest.TestCase):
    """(b) A ladder-book row and a hold-book row both get a value (or an
    explicit pressure_error) -- no crash. Book kind never branches the
    pressure treatment (see module docstring); a `close` row from any book
    kind is a plain "send"."""

    def _close_row(self, *, book: str, mint: str, t_ms: int, pnl: int, exit_status: str = "realized") -> dict[str, object]:
        return {
            "schema": "forward_paper_position_v1",
            "ledger": "shadow",
            "event": "close",
            "book": book,
            "mint": mint,
            "decision_t_ms": t_ms,
            "t_entry_ms": t_ms,
            "exit_status": exit_status,
            "pnl_lamports": pnl,
        }

    def test_ladder_and_hold_rows_both_resolve(self) -> None:
        creates, rows = _migrate_tape()  # any real tape works; kind-agnostic
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tape_path, create_path = _write_tape(tmp_path, creates, rows)

            ladder_row = self._close_row(book="ladder_1_5x_t25", mint="MintM", t_ms=T0 + 1400, pnl=-500_000)
            hold_row = self._close_row(book="hold_30s_all", mint="MintM", t_ms=T0 + 1400, pnl=250_000, exit_status="no_exit_liquidity")

            out, counts = pstamp.stamp_rows(
                [ladder_row, hold_row], tape_paths=[tape_path], create_paths=[create_path], tape_end_ms=T0 + 10_000_000
            )
            self.assertEqual(len(out), 2)
            for stamped in out:
                has_value = isinstance(stamped.get("pressure_scale_1_pnl_lamports"), int)
                has_error = isinstance(stamped.get("pressure_error"), str)
                self.assertTrue(has_value or has_error, stamped)

    def test_no_tape_for_mint_is_an_explicit_error_not_a_crash(self) -> None:
        row = self._close_row(book="hold_30s_all", mint="MintUnknown", t_ms=T0, pnl=1)
        out, counts = pstamp.stamp_rows([row], tape_paths=[], create_paths=[], tape_end_ms=T0 + 1000)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["pressure_error"], pstamp.ERROR_NO_TAPE)
        self.assertEqual(counts, {"pressure_error": 1})


class MissRowTests(unittest.TestCase):
    def test_unfilled_miss_copies_pnl_unchanged_both_scales(self) -> None:
        row = {
            "schema": "forward_paper_position_v1",
            "ledger": "shadow",
            "event": "miss",
            "book": "book_a",
            "mint": "MintX",
            "decision_t_ms": T0,
            "entry_status": "missed_no_liquidity",
            "reason": "priority_fee_on_unfilled_attempt",
            "pnl_lamports": -PRIORITY_FEE_LAMPORTS,
        }
        out, counts = pstamp.stamp_rows([row], tape_paths=[], create_paths=[], tape_end_ms=T0 + 1000)
        self.assertEqual(counts, {"miss_unfilled": 1})
        self.assertEqual(out[0]["pressure_scale_1_pnl_lamports"], -PRIORITY_FEE_LAMPORTS)
        self.assertEqual(out[0]["pressure_scale_2_pnl_lamports"], -PRIORITY_FEE_LAMPORTS)
        self.assertNotIn("pressure_error", out[0])

    def test_flat_fail_miss_is_not_reconstructable(self) -> None:
        row = {
            "schema": "forward_paper_position_v1",
            "ledger": "shadow",
            "event": "miss",
            "book": "book_a",
            "mint": "MintX",
            "decision_t_ms": T0,
            "entry_status": "filled",
            "reason": "flat_15pct_landing",
            "pnl_lamports": -PRIORITY_FEE_LAMPORTS,
        }
        out, counts = pstamp.stamp_rows([row], tape_paths=[], create_paths=[], tape_end_ms=T0 + 1000)
        self.assertEqual(counts, {"pressure_error": 1})
        self.assertEqual(out[0]["pressure_error"], pstamp.ERROR_UNRECONSTRUCTABLE)
        self.assertNotIn("pressure_scale_1_pnl_lamports", out[0])


class SnapshotGuardTests(unittest.TestCase):
    def test_refuses_a_fresh_positions_file_without_the_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            positions = tmp_path / "positions.jsonl"
            positions.write_text("", encoding="utf-8")
            config = tmp_path / "config.json"
            config.write_text(json.dumps({"books": [{"id": "b", "kind": "baseline"}]}), encoding="utf-8")
            out = tmp_path / "pressure.jsonl"
            with self.assertRaises(SystemExit):
                pstamp.main(
                    [
                        "--config", str(config),
                        "--positions", str(positions),
                        "--out", str(out),
                    ]
                )

    def test_accepts_a_fresh_file_with_the_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            positions = tmp_path / "positions.jsonl"
            positions.write_text("", encoding="utf-8")
            config = tmp_path / "config.json"
            config.write_text(json.dumps({"books": [{"id": "b", "kind": "baseline"}]}), encoding="utf-8")
            out = tmp_path / "pressure.jsonl"
            rc = pstamp.main(
                [
                    "--config", str(config),
                    "--positions", str(positions),
                    "--out", str(out),
                    "--i-know-its-a-snapshot",
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())


if __name__ == "__main__":
    unittest.main()
