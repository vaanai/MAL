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
from tools.forward_paper import BookSpec, ForwardEngine, _event_ts, flow_from_tape_row
from tools.paper_curve_math import DEFAULT_SLIPPAGE_CAP, PRIORITY_FEE_LAMPORTS
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


class MintChunksTests(unittest.TestCase):
    def test_chunked_output_byte_identical(self) -> None:
        mints = ["MintA", "MintB", "MintC", "MintD"]
        creates = {m: _create(m, T0) for m in mints}
        trades: list[dict[str, object]] = []
        for i, m in enumerate(mints):
            for j in range(3):
                trades.append(
                    _trade(m, T0 + 1000 + 200 * j + i, slot=20 + i, event_index=j + 1, sol=(j + 1) * 1_000_000_000,
                           quote=70_000_000_000 + j * 10**9, base=536_500_000_000_000 - j * 10**11)
                )
        rows = []
        for i, m in enumerate(["MintC", "MintA", "MintD", "MintB", "MintA"]):
            rows.append({
                "schema": "forward_paper_position_v1", "ledger": "shadow", "event": "close",
                "book": f"b{i % 2}", "mint": m, "decision_t_ms": T0 + 1400 + i, "t_entry_ms": T0 + 1400 + i,
                "exit_status": "realized", "pnl_lamports": 1000 * i,
            })
        with tempfile.TemporaryDirectory() as tmp:
            tape_path, create_path = _write_tape(Path(tmp), creates, trades)
            outs = []
            for n in (1, 2, 3, 7):
                out, counts = pstamp.stamp_rows(
                    rows, tape_paths=[tape_path], create_paths=[create_path], tape_end_ms=T0 + 10_000_000, mint_chunks=n
                )
                outs.append(("\n".join(json.dumps(r) for r in out), counts))
            self.assertEqual(len(outs[0][1]) > 0, True)
            for o in outs[1:]:
                self.assertEqual(o, outs[0])


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
        """(c) A flat-fail miss row from a runner build before #145
        (`counterfactual_fill` absent -- it never logged the discarded fill)
        still gets `pressure_error`, never a guessed number. `entry_status`
        is `"missed_landing"` here, the real value `tools/forward_paper.py`
        writes for this row shape (tools/forward_paper.py:2025) -- not
        `"filled"`."""
        row = {
            "schema": "forward_paper_position_v1",
            "ledger": "shadow",
            "event": "miss",
            "book": "book_a",
            "mint": "MintX",
            "decision_t_ms": T0,
            "entry_status": "missed_landing",
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


def _hold_book() -> BookSpec:
    return BookSpec(
        "buy_all",
        "baseline",
        "hold_30s",
        max_concurrent=None,
        daily_loss_lamports=None,
        creator_cooldown_ms=0,
        token_cooldown_ms=0,
    )


def _hold_tape() -> tuple[dict[str, CreateSignal], list[dict[str, object]]]:
    creates = {"MintF": _create("MintF", T0)}
    rows = [
        _trade("MintF", T0 - 1_000, slot=19, event_index=0, sol=1_000_000_000, quote=40_000_000_000, base=1_050_000_000_000_000, venue="pump_bonding"),
        # A second same-slot buy right before entry: same_slot_buys/nearby
        # SOL are nonzero on both legs of the equivalence.
        _trade("MintF", T0 - 800, slot=19, event_index=1, sol=2_000_000_000, quote=41_000_000_000, base=1_049_000_000_000_000, venue="pump_bonding"),
        # Exit-time (T0 + hold_30s) pool state: a different price than entry.
        _trade("MintF", T0 + 30_000, slot=25, event_index=2, sol=1_000_000_000, quote=48_000_000_000, base=1_010_000_000_000_000, venue="pump_bonding"),
        _trade("MintF", T0 + 60_000, slot=26, event_index=3, sol=1_000_000_000, quote=49_000_000_000, base=1_005_000_000_000_000, venue="pump_bonding"),
    ]
    return creates, rows


def _ladder_book() -> BookSpec:
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
        _trade("MintL", T0 - 1_000, slot=19, event_index=0, sol=1_000_000_000, quote=40_000_000_000, base=1_050_000_000_000_000, venue="pump_bonding"),
        _trade("MintL", T0 + 5_000, slot=2, event_index=1, quote=42_000_000_000, base=1_040_000_000_000_000, venue="pump_bonding"),
        # +100% off the first print: past ladder_1_5x_t25's scale_ret=0.50, scales out half.
        _trade("MintL", T0 + 30_000, slot=3, event_index=2, quote=84_000_000_000, base=1_040_000_000_000_000, venue="pump_bonding"),
        # Drops back under peak*(1-trail=0.25): trails the remainder out.
        _trade("MintL", T0 + 60_000, slot=4, event_index=3, quote=60_000_000_000, base=1_040_000_000_000_000, venue="pump_bonding"),
    ]
    return creates, rows


def _run_engine_positions(
    book: BookSpec,
    creates: dict[str, CreateSignal],
    rows: list[dict[str, object]],
    *,
    fail_rate: float,
    tape_end_ms: int,
) -> list[dict[str, object]]:
    """Same push loop `tools.forward_paper.replay_rows` uses, with a
    `fail_rate` knob `replay_rows` does not expose (tests only)."""
    engine = ForwardEngine(
        [book],
        kill_file=Path("/tmp/forward-paper-pressure-stamp-cf-fail-absent"),
        retain_rows=True,
        tape_end_ms=tape_end_ms,
        slippage_cap=DEFAULT_SLIPPAGE_CAP,
        fail_rate=fail_rate,
    )
    for create in creates.values():
        engine.push_create(create)
    for row in rows:
        parsed = flow_from_tape_row(row)
        if parsed is None:
            continue
        mint, pr = parsed
        engine.push_print(mint, pr, _event_ts(row))
    engine.drain_until(tape_end_ms, final=True)
    return engine.positions


class CounterfactualEquivalenceTests(unittest.TestCase):
    """(a)/(b): a fail_rate=1.0 run's counterfactual miss, priced by the
    stamp tool through `reconstruct_fill`, equals the pressure pnl the same
    attempt gets when fail_rate=0.0 lets it open and close for real -- same
    tape, same book, same entry. Proves the reconstruction, not just that it
    runs without raising."""

    def _equivalence(
        self, book: BookSpec, creates: dict[str, CreateSignal], rows: list[dict[str, object]]
    ) -> tuple[dict, dict, dict, dict]:
        tape_end_ms = T0 + 200_000
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tape_path, create_path = _write_tape(tmp_path, creates, rows)

            zero = _run_engine_positions(book, creates, rows, fail_rate=0.0, tape_end_ms=tape_end_ms)
            one = _run_engine_positions(book, creates, rows, fail_rate=1.0, tape_end_ms=tape_end_ms)

            close_rows = [r for r in zero if r["event"] == "close" and r["ledger"] == "shadow"]
            miss_rows = [r for r in one if r["event"] == "miss" and r["ledger"] == "shadow"]
            self.assertEqual(len(close_rows), 1, close_rows)
            self.assertEqual(len(miss_rows), 1, miss_rows)
            close_row, miss_row = close_rows[0], miss_rows[0]
            self.assertIs(miss_row.get("counterfactual_fill"), True)

            stamp_tape_end = tape_end_ms + 1_000_000
            out_close, counts_close = pstamp.stamp_rows(
                [close_row],
                tape_paths=[tape_path],
                create_paths=[create_path],
                tape_end_ms=stamp_tape_end,
                slippage_cap=DEFAULT_SLIPPAGE_CAP,
            )
            out_miss, counts_miss = pstamp.stamp_rows(
                [miss_row],
                tape_paths=[tape_path],
                create_paths=[create_path],
                tape_end_ms=stamp_tape_end,
                slippage_cap=DEFAULT_SLIPPAGE_CAP,
            )
            self.assertEqual(counts_close, {"send": 1})
            self.assertEqual(counts_miss, {"miss_counterfactual": 1})
            stamped_close, stamped_miss = out_close[0], out_miss[0]
            self.assertNotIn("pressure_error", stamped_close)
            self.assertNotIn("pressure_error", stamped_miss)
            return close_row, miss_row, stamped_close, stamped_miss

    def test_hold_book_counterfactual_matches_a_real_send(self) -> None:
        """(a) The equivalence proof, on a non-ladder (`hold_30s`) exit."""
        creates, rows = _hold_tape()
        close_row, miss_row, stamped_close, stamped_miss = self._equivalence(_hold_book(), creates, rows)
        self.assertEqual(stamped_miss["counterfactual_exit_status"], close_row["exit_status"])
        self.assertEqual(stamped_miss["counterfactual_would_pnl_lamports"], close_row["pnl_lamports"])
        self.assertEqual(stamped_miss["same_slot_buys"], stamped_close["same_slot_buys"])
        self.assertEqual(stamped_miss["nearby_buy_lamports"], stamped_close["nearby_buy_lamports"])
        self.assertGreater(stamped_miss["same_slot_buys"], 0, "pressure inputs should be nonzero on this fixture")
        self.assertEqual(stamped_miss["pressure_scale_1_pnl_lamports"], stamped_close["pressure_scale_1_pnl_lamports"])
        self.assertEqual(stamped_miss["pressure_scale_2_pnl_lamports"], stamped_close["pressure_scale_2_pnl_lamports"])

    def test_ladder_book_counterfactual_matches_a_real_send(self) -> None:
        """(b) The same equivalence proof, on a `LadderRule` exit -- proves
        `reconstruct_fill`'s `simulate_ladder` branch, not just `simulate_exit`."""
        creates, rows = _ladder_tape()
        close_row, miss_row, stamped_close, stamped_miss = self._equivalence(_ladder_book(), creates, rows)
        self.assertEqual(close_row["exit_status"], "realized")
        self.assertEqual(stamped_miss["counterfactual_exit_status"], close_row["exit_status"])
        self.assertEqual(stamped_miss["counterfactual_would_pnl_lamports"], close_row["pnl_lamports"])
        self.assertEqual(stamped_miss["pressure_scale_1_pnl_lamports"], stamped_close["pressure_scale_1_pnl_lamports"])
        self.assertEqual(stamped_miss["pressure_scale_2_pnl_lamports"], stamped_close["pressure_scale_2_pnl_lamports"])


if __name__ == "__main__":
    unittest.main()
