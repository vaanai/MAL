"""Tests for tools.kill_review -- synthetic fixtures only, no live tape."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import kill_review
from tools.paper_attention_promote import BookTrade, _cluster_bootstrap

WINDOW_START_MS = kill_review.WINDOW_START_MS


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _config(books: list[dict]) -> dict:
    return {"books": books}


def _day_ms(day_index: int, offset_ms: int = 0) -> int:
    return WINDOW_START_MS + day_index * 86_400_000 + offset_ms


def _gate_passing_rows(book_id: str, *, n_days: int = 5, per_day: int = 20, include_pressure: bool = True) -> list[dict]:
    """n_days * per_day close rows, one mint each, uniform positive pnl.

    Hand-computable: every flat pnl is exactly 1_000_000 lamports (0.001
    SOL) and every pressure_scale_1 pnl is exactly 800_000 lamports. Since
    every trade is the same value, a mint-clustered bootstrap resample can
    only ever draw that same constant -- the 90% CI collapses to a point at
    the mean, so the lower bound equals the mean exactly, and it is > 0
    without needing to run the resample to check.
    """
    rows = []
    mint_i = 0
    for day in range(n_days):
        for k in range(per_day):
            mint_i += 1
            row = {
                "schema": "forward_paper_position_v1",
                "ledger": "shadow",
                "event": "close",
                "book": book_id,
                "mint": f"mint-{mint_i}",
                "decision_t_ms": _day_ms(day, k * 1000),
                "pnl_lamports": 1_000_000,
            }
            if include_pressure:
                row["pressure_scale_1_pnl_lamports"] = 800_000
            rows.append(row)
    return rows


class GateMathTests(unittest.TestCase):
    """(a) The gate math on a hand-computable case."""

    def test_uniform_positive_book_clears_gate_under_both_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            positions = tmp_path / "positions.jsonl"
            config = tmp_path / "config.json"
            _write_jsonl(positions, _gate_passing_rows("book_a"))
            config.write_text(json.dumps(_config([{"id": "book_a", "kind": "migrate"}])), encoding="utf-8")

            result = kill_review.run_kill_review(
                config_path=config,
                positions_path=positions,
                settlements_path=None,
                restarts_log_path=None,
                manual_restarts=[],
            )

            block = result["books"]["book_a"]
            gf = block["gate_flat"]
            gp = block["gate_pressure_scale_1"]

            # Hand-computed: 100 trades * 1_000_000 lamports = 0.1 SOL total;
            # mean = 0.001 SOL; every value identical so the CI collapses to
            # a point at the mean; ex-top-3 leaves 97 trades at 1_000_000 =
            # 0.097 SOL, still positive; 5 days, all 5 positive (majority).
            self.assertEqual(gf["n"], 100)
            self.assertAlmostEqual(gf["mean_sol"], 0.001, places=12)
            self.assertAlmostEqual(gf["total_sol"], 0.1, places=12)
            self.assertEqual(gf["mean_ci90_sol"], [0.001, 0.001])
            self.assertAlmostEqual(gf["total_ex_top3_sol"], 0.097, places=12)
            self.assertEqual(gf["n_days"], 5)
            self.assertEqual(gf["days_positive"], 5)
            self.assertTrue(gf["promote"])

            self.assertEqual(gp["n"], 100)
            self.assertAlmostEqual(gp["mean_sol"], 0.0008, places=12)
            self.assertTrue(gp["promote"])

            self.assertTrue(block["gate_clears_both_models"])
            # k = 1 candidate book: Holm degenerates to a plain alpha test.
            # p <= 0 share is 0 (every draw is positive) so it clears.
            self.assertEqual(block["bootstrap_p_le_zero"]["flat"], 0.0)
            self.assertEqual(block["bootstrap_p_le_zero"]["pressure_scale_1"], 0.0)
            self.assertTrue(block["holm_flat"]["pass"])
            self.assertTrue(block["holm_pressure_scale_1"]["pass"])
            self.assertTrue(block["promote"])
            self.assertEqual(block["status"], "PROMOTE")
            self.assertEqual(result["verdict"]["passing"], ["book_a"])
            self.assertIn("VERDICT: PROMOTE=book_a", kill_review.verdict_line(result))

    def test_below_min_n_fails_on_min_n_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            positions = tmp_path / "positions.jsonl"
            config = tmp_path / "config.json"
            _write_jsonl(positions, _gate_passing_rows("book_a", n_days=5, per_day=10))  # n=50 < 100
            config.write_text(json.dumps(_config([{"id": "book_a", "kind": "migrate"}])), encoding="utf-8")

            result = kill_review.run_kill_review(
                config_path=config,
                positions_path=positions,
                settlements_path=None,
                restarts_log_path=None,
                manual_restarts=[],
            )
            block = result["books"]["book_a"]
            self.assertEqual(block["gate_flat"]["n"], 50)
            self.assertIn("min_n", block["gate_flat"]["promote_blockers"])
            self.assertFalse(block["promote"])
            # Pressure coverage is complete (every one of the 50 rows carries
            # a pressure_scale_1_pnl_lamports) -- this book was measured on
            # both legs and found wanting, not left undecided.
            self.assertEqual(block["status"], "KILL")
            self.assertEqual(result["verdict"]["passing"], [])
            self.assertEqual(kill_review.verdict_line(result), "VERDICT: PROMOTE=NONE; NOT_DECIDABLE=NONE; KILL=book_a")


class HolmStepDownTests(unittest.TestCase):
    """(b) Holm ordering and step-down stopping."""

    def test_ranking_and_thresholds(self) -> None:
        # alpha=0.05, k=4: thresholds by rank are alpha/4, alpha/3, alpha/2, alpha/1.
        p_values = {"a": 0.001, "b": 0.3, "c": 0.001, "d": 0.001}
        out = kill_review.holm_step_down(p_values, alpha=0.05)
        ranks = {book: info["rank"] for book, info in out.items()}
        # a, c, d tie at p=0.001 and sort before b (p=0.3) by ascending p.
        self.assertEqual(sorted([ranks["a"], ranks["c"], ranks["d"]]), [1, 2, 3])
        self.assertEqual(ranks["b"], 4)
        self.assertAlmostEqual(out["a" if ranks["a"] == 1 else "c" if ranks["c"] == 1 else "d"]["threshold"], 0.0125)
        self.assertAlmostEqual(out["b"]["threshold"], 0.05)

    def test_a_middle_failure_stops_every_rank_after_it(self) -> None:
        # Deliberately not monotone against thresholds: rank 2 fails its own
        # (tighter) threshold, while ranks 3 and 4 would individually clear
        # their own (looser) thresholds -- Holm must still fail them because
        # rank 2, ranked before them, failed.
        p_values = {"r1": 0.001, "r2": 0.02, "r3": 0.024, "r4": 0.04}
        out = kill_review.holm_step_down(p_values, alpha=0.05)
        # thresholds: r1=0.0125, r2=0.016667, r3=0.025, r4=0.05
        self.assertAlmostEqual(out["r2"]["threshold"], 0.05 / 3, places=9)
        self.assertAlmostEqual(out["r3"]["threshold"], 0.025, places=9)
        self.assertAlmostEqual(out["r4"]["threshold"], 0.05, places=9)

        self.assertTrue(out["r1"]["pass"])
        self.assertFalse(out["r2"]["pass"])  # 0.02 > 0.016667
        # r3's own p (0.024) is <= its own threshold (0.025) -- but it must
        # still fail because r2 (ranked before it) already failed.
        self.assertLessEqual(0.024, out["r3"]["threshold"])
        self.assertFalse(out["r3"]["pass"])
        self.assertLessEqual(0.04, out["r4"]["threshold"])
        self.assertFalse(out["r4"]["pass"])

    def test_missing_p_value_never_passes(self) -> None:
        out = kill_review.holm_step_down({"only": None}, alpha=0.05)
        self.assertFalse(out["only"]["pass"])


class SettlementDedupTests(unittest.TestCase):
    """(c) Dedup of a settlement against a real close."""

    def test_settlement_loses_to_a_live_close_on_the_same_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            positions = tmp_path / "positions.jsonl"
            settlements = tmp_path / "settlements.jsonl"
            key_t_ms = _day_ms(0, 0)
            _write_jsonl(
                positions,
                [
                    {
                        "schema": "forward_paper_position_v1",
                        "ledger": "shadow",
                        "event": "close",
                        "book": "book_a",
                        "mint": "mint-dup",
                        "decision_t_ms": key_t_ms,
                        "pnl_lamports": 500_000,
                    }
                ],
            )
            _write_jsonl(
                settlements,
                [
                    {
                        "schema": "forward_paper_settlement_v1",
                        "settled_offline": True,
                        "ledger": "shadow",
                        "event": "close",
                        "book": "book_a",
                        "mint": "mint-dup",
                        "decision_t_ms": key_t_ms,
                        "pnl_lamports": 999_999,
                    }
                ],
            )

            rows, n_settled, n_dupes, settle_failed = kill_review.load_window_rows(
                positions,
                settlements,
                window_start_ms=WINDOW_START_MS,
                window_end_ms=kill_review.WINDOW_END_MS,
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["pnl_lamports"], 500_000)
            self.assertEqual(n_settled, 0)
            self.assertEqual(n_dupes, 1)
            self.assertEqual(settle_failed, [])

    def test_orphan_settlement_with_no_live_close_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            positions = tmp_path / "positions.jsonl"
            settlements = tmp_path / "settlements.jsonl"
            positions.write_text("", encoding="utf-8")
            _write_jsonl(
                settlements,
                [
                    {
                        "schema": "forward_paper_settlement_v1",
                        "settled_offline": True,
                        "ledger": "shadow",
                        "event": "close",
                        "book": "book_a",
                        "mint": "mint-orphan",
                        "decision_t_ms": _day_ms(0, 0),
                        "pnl_lamports": 42,
                    }
                ],
            )
            rows, n_settled, n_dupes, settle_failed = kill_review.load_window_rows(
                positions,
                settlements,
                window_start_ms=WINDOW_START_MS,
                window_end_ms=kill_review.WINDOW_END_MS,
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(n_settled, 1)
            self.assertEqual(n_dupes, 0)
            self.assertEqual(settle_failed, [])


class VoidWindowTests(unittest.TestCase):
    """(d) Void-window assertion."""

    def test_window_start_inside_the_void_raises(self) -> None:
        with self.assertRaises(AssertionError):
            kill_review.assert_window_clear_of_void(kill_review.VOID_FROM_MS)

    def test_window_start_at_the_void_edge_is_accepted(self) -> None:
        # No exception.
        kill_review.assert_window_clear_of_void(kill_review.VOID_UNTIL_MS)

    def test_default_window_start_clears_the_void(self) -> None:
        kill_review.assert_window_clear_of_void(kill_review.WINDOW_START_MS)

    def test_run_kill_review_refuses_a_void_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            positions = tmp_path / "positions.jsonl"
            config = tmp_path / "config.json"
            positions.write_text("", encoding="utf-8")
            config.write_text(json.dumps(_config([{"id": "book_a", "kind": "migrate"}])), encoding="utf-8")
            with self.assertRaises(AssertionError):
                kill_review.run_kill_review(
                    config_path=config,
                    positions_path=positions,
                    settlements_path=None,
                    restarts_log_path=None,
                    manual_restarts=[],
                    window_start_ms=kill_review.VOID_FROM_MS,
                )


class BothFailModelsRequiredTests(unittest.TestCase):
    """(e) Both fail models are required."""

    def test_flat_only_data_never_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            positions = tmp_path / "positions.jsonl"
            config = tmp_path / "config.json"
            # Strong flat-only book: no pressure_scale_1_pnl_lamports field
            # on any row at all.
            _write_jsonl(positions, _gate_passing_rows("book_a", include_pressure=False))
            config.write_text(json.dumps(_config([{"id": "book_a", "kind": "migrate"}])), encoding="utf-8")

            result = kill_review.run_kill_review(
                config_path=config,
                positions_path=positions,
                settlements_path=None,
                restarts_log_path=None,
                manual_restarts=[],
            )
            block = result["books"]["book_a"]
            self.assertTrue(block["gate_flat"]["promote"])
            self.assertFalse(block["pressure_data_available"])
            self.assertEqual(block["gate_pressure_scale_1"]["n"], 0)
            self.assertIn("min_n", block["gate_pressure_scale_1"]["promote_blockers"])
            self.assertFalse(block["gate_clears_both_models"])
            self.assertIsNone(block["bootstrap_p_le_zero"]["pressure_scale_1"])
            self.assertFalse(block["holm_pressure_scale_1"]["pass"])
            self.assertFalse(block["promote"])
            self.assertEqual(result["verdict"]["passing"], [])
            self.assertEqual(kill_review.verdict_line(result), "VERDICT: NONE")


class SettleFailedTests(unittest.TestCase):
    """(f) A settled_offline: false settlement row is a settle_failed record,
    not a trade, and flags its book incomplete."""

    def test_settle_failed_row_is_not_a_trade_and_marks_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            positions = tmp_path / "positions.jsonl"
            settlements = tmp_path / "settlements.jsonl"
            config = tmp_path / "config.json"

            _write_jsonl(positions, _gate_passing_rows("book_a"))
            _write_jsonl(
                settlements,
                [
                    {
                        "schema": "forward_paper_settlement_v1",
                        "settled_offline": False,
                        "settle_error": "censored_tape_too_short",
                        "ledger": "shadow",
                        "book": "book_a",
                        "mint": "mint-orphan-failed",
                        "decision_t_ms": _day_ms(0, 500),
                    }
                ],
            )
            config.write_text(json.dumps(_config([{"id": "book_a", "kind": "migrate"}])), encoding="utf-8")

            result = kill_review.run_kill_review(
                config_path=config,
                positions_path=positions,
                settlements_path=settlements,
                restarts_log_path=None,
                manual_restarts=[],
            )
            block = result["books"]["book_a"]
            # The gate-passing 100 live trades are untouched by the failed
            # settlement -- it must not be counted as a trade.
            self.assertEqual(block["n_trades"], 100)
            self.assertEqual(block["n_settle_failed"], 1)
            self.assertEqual(block["settle_errors"], ["censored_tape_too_short"])
            self.assertTrue(block["incomplete"])
            self.assertIn("book_a", result["incomplete_books"])
            self.assertEqual(result["n_settle_failed_total"], 1)
            # The gate itself still clears (100 clean trades pass on both
            # models) but the book still must not promote: it is missing
            # data, not a zero.
            self.assertTrue(block["gate_clears_both_models"])
            self.assertFalse(block["promote"])
            self.assertEqual(result["verdict"]["passing"], [])

    def test_settle_failed_row_does_not_double_count_when_a_live_close_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            positions = tmp_path / "positions.jsonl"
            settlements = tmp_path / "settlements.jsonl"
            key_t_ms = _day_ms(0, 0)
            _write_jsonl(
                positions,
                [
                    {
                        "schema": "forward_paper_position_v1",
                        "ledger": "shadow",
                        "event": "close",
                        "book": "book_a",
                        "mint": "mint-x",
                        "decision_t_ms": key_t_ms,
                        "pnl_lamports": 1,
                    }
                ],
            )
            _write_jsonl(
                settlements,
                [
                    {
                        "schema": "forward_paper_settlement_v1",
                        "settled_offline": False,
                        "settle_error": "stale_retry_record",
                        "ledger": "shadow",
                        "book": "book_a",
                        "mint": "mint-x",
                        "decision_t_ms": key_t_ms,
                    }
                ],
            )
            rows, n_settled, n_dupes, settle_failed = kill_review.load_window_rows(
                positions,
                settlements,
                window_start_ms=WINDOW_START_MS,
                window_end_ms=kill_review.WINDOW_END_MS,
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(settle_failed, [])


class BootstrapEquivalenceTests(unittest.TestCase):
    """Verify kill_review's bootstrap matches the existing promotion code's
    bootstrap at 1,000 draws, seed 1, on the same data."""

    def test_matches_paper_attention_promote_cluster_bootstrap(self) -> None:
        trades = [
            BookTrade(mint=f"mint-{i}", t_ms=_day_ms(i % 5, 0), pnl=pnl)
            for i, pnl in enumerate(
                [1_000_000, -500_000, 2_000_000, -100_000, 300_000, 0, -750_000, 1_500_000, 400_000, -50_000] * 5
            )
        ]
        expected_mean_ci, _expected_total_ci = _cluster_bootstrap(trades)

        means = kill_review.bootstrap_means_lamports(trades, draws=1000, seed=1)
        self.assertEqual(len(means), 1000)
        ordered = sorted(means)
        lower = kill_review._pct(ordered, 0.05) / kill_review.LAMPORTS_PER_SOL
        upper = kill_review._pct(ordered, 0.95) / kill_review.LAMPORTS_PER_SOL

        self.assertAlmostEqual(lower, expected_mean_ci[0], places=12)
        self.assertAlmostEqual(upper, expected_mean_ci[1], places=12)


if __name__ == "__main__":
    unittest.main()
