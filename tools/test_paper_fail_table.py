"""Buy-all / migrate attempt builders stay on the shared fill and the rank window."""

from __future__ import annotations

import unittest

from tools.laya_v0 import ENTRY_LATENCY_MS, FlowPrint, MintBook
from tools.paper_curve_math import LAMPORTS_PER_SOL, PRIORITY_FEE_LAMPORTS
from tools.paper_fail_pressure import TARGET_FAIL_RATE, fit_curve, is_send, mean_p
from tools.paper_fail_table import (
    buy_all_attempts,
    buyers_8_attempts,
    format_markdown,
    migrate_attempts,
    pressure_at,
    take_mask,
)
from tools.paper_price_path import CreateSignal

Q0 = 35_000_000_000
B0 = 1_073_000_000_000_000


def _book(prints: list[FlowPrint], *, mint: str = "M", t0: int = 0) -> MintBook:
    return MintBook(
        create=CreateSignal(
            mint=mint,
            t_signal_ms=t0,
            creator="C",
            signature="sig",
            v_sol=Q0 / LAMPORTS_PER_SOL,
            v_token_ui=B0 / 1_000_000,
            mcap_sol=None,
            initial_buy_ui=None,
            sol_amount=None,
        ),
        flow=prints,
    )


def _flow(
    t_ms: int,
    *,
    slot: int = 3,
    side: str = "buy",
    sol: int = 2_000_000_000,
    venue: str = "pump_bonding",
    trader: str = "T",
    quote: int = Q0,
    base: int = B0,
) -> FlowPrint:
    return FlowPrint(
        t_recv_ms=t_ms,
        slot=slot,
        event_index=1,
        venue=venue,
        side=side,
        sol_lamports=sol,
        token_raw=1_000_000,
        trader=trader,
        quote_reserve=quote,
        base_reserve=base,
        price_sol=(quote / LAMPORTS_PER_SOL) / (base / 1_000_000),
        market_cap_sol=0.0,
    )


class TableTests(unittest.TestCase):
    def test_buy_all_send_sees_the_print_before_entry(self) -> None:
        book = _book([_flow(500, sol=2_000_000_000)])
        attempts = buy_all_attempts({"M": book}, tape_end_ms=100_000)
        self.assertEqual(len(attempts), 1)
        attempt = attempts[0]
        self.assertTrue(is_send(attempt))
        self.assertLess(attempt.pnl_lamports or 0, 0)
        pressure = pressure_at(book.path, ENTRY_LATENCY_MS)
        self.assertEqual(attempt.pressure, pressure)
        self.assertEqual(pressure.same_slot_buys, 1)
        self.assertEqual(pressure.nearby_buy_lamports, 2_000_000_000)
        curve = fit_curve([pressure], scale=1.0)
        fitted = mean_p(curve, [pressure])
        assert fitted is not None
        self.assertAlmostEqual(fitted, TARGET_FAIL_RATE, places=6)

    def test_slippage_miss_costs_priority_only(self) -> None:
        rich = _flow(500, quote=Q0 * 3, base=B0 // 2, sol=1_000_000_000)
        book = _book([rich])
        attempts = buy_all_attempts({"M": book}, tape_end_ms=100_000)
        self.assertEqual(attempts[0].entry_status, "missed_slippage")
        self.assertEqual(attempts[0].pnl_lamports, -PRIORITY_FEE_LAMPORTS)

    def test_migrate_is_the_first_pumpswap_print(self) -> None:
        book = _book(
            [
                _flow(1_000),
                _flow(50_000, venue="pumpswap", slot=9, trader="S", quote=40_000_000_000, base=B0 // 2),
                _flow(80_000, venue="pumpswap", slot=10, trader="S2"),
            ]
        )
        attempts = migrate_attempts({"M": book}, tape_end_ms=200_000)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].pressure.same_slot_buys, 1)

    def test_rank_window_takes_only_after_warmup(self) -> None:
        scores = [0.1] * 19 + [0.2]
        verdicts = take_mask(scores, 0.05)
        self.assertEqual(verdicts[:19], ["warmup"] * 19)
        self.assertEqual(verdicts[19], "take")

    def test_markdown_names_the_three_books(self) -> None:
        text = format_markdown(
            {
                "curves": [
                    {
                        "scale": 1.0,
                        "b_slot": 0.8,
                        "b_sol": 0.35,
                        "intercept": -0.9,
                        "nearby_ms": 2000,
                        "mean_p_buy_all_sends": 0.289,
                        "calibration_sends": 3,
                    }
                ],
                "slope_note": "slopes declared",
                "live_note": "live stays flat",
                "books": {
                    name: {
                        "exit": "x",
                        "by_scale": {
                            "0": _row(),
                            "0.5": _row(),
                            "1": _row(),
                            "2": _row(),
                        },
                    }
                    for name in ("buy_all", "buyers_8_top5", "migrate")
                },
            }
        )
        self.assertIn("buy_all", text)
        self.assertIn("buyers_8_top5", text)
        self.assertIn("migrate", text)
        self.assertIn("live stays flat", text)


def _row() -> dict[str, object]:
    return {
        "n": 1,
        "mean_p": 0.289,
        "mean_sol": -0.001,
        "total_sol": -0.001,
        "miss_n": 0,
        "no_exit_n": 0,
        "censored_n": 0,
    }


class _Booster:
    names = ["f_sig_buy_n"]

    def predict_one(self, row: list[float]) -> float:
        return 0.5


class BuyersTests(unittest.TestCase):
    def test_top_slice_needs_a_full_window(self) -> None:
        books = {}
        for i in range(25):
            # 8th buy sits between the 5s and 15s grid points, outside the 2s near-grid skip.
            traders = [_flow(8_000 + 10 * k, trader=f"u{i}-{k}", slot=4) for k in range(8)]
            books[f"m{i}"] = _book(traders, mint=f"m{i}", t0=0)
        taken, counts = buyers_8_attempts(books, _Booster(), tape_end_ms=2_000_000, frac=0.05)
        self.assertEqual(counts["signals"], 25)
        self.assertEqual(counts["warmup"], 19)
        self.assertEqual(counts["take"], 6)
        self.assertEqual(len(taken), 6)
        self.assertTrue(all(a.entry_status == "filled" and a.exit_status == "realized" for a in taken))
        self.assertTrue(all((a.pnl_lamports or 0) < 0 for a in taken))


if __name__ == "__main__":
    unittest.main()
