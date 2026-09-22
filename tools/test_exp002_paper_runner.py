"""Unit tests for EXP-002 / EXP-002b paper runner (offline fixtures, no network)."""

from __future__ import annotations

import json
import io
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from observe.regime import STREAM_NEW_TOKEN, seal_ingest_record
from tools.exp002_paper_runner import (
    EvaluateRulesV0,
    EvaluateRulesV1,
    EvaluateRulesV2,
    MAX_RANDOM_SIGNATURE_SAMPLES,
    _gate_lift,
    _gate_parity,
    _print_summary,
    build_mint_price_series,
    compute_outcomes,
    draw_random_baseline,
    _regime_bonding_gate,
    is_bonding_create,
    knowable_at_t_honest,
    run_paper_book,
    _parse_iso_ts,
    BookRow,
)
from tools.exp001_mislabel import LoadedRow

T0 = "2026-09-20T12:00:00.000+00:00"
T1 = "2026-09-20T12:00:01.000+00:00"
T60 = "2026-09-20T12:01:00.000+00:00"


def _create_row(
    *,
    t_ws: str = T0,
    mint: str = "MintA",
    market_cap: float = 30.0,
    v_sol: float = 30.5,
    sol_amount: float = 1.0,
    initial_buy: float = 1_000_000.0,
    name: str = "Test Token",
    symbol: str = "TEST",
    uri: str = "https://example.com/meta.json",
    **extra: object,
) -> dict:
    payload = {
        "txType": "create",
        "signature": f"Sig-{mint}-{t_ws}",
        "mint": mint,
        "marketCapSol": market_cap,
        "vSolInBondingCurve": v_sol,
        "solAmount": sol_amount,
        "initialBuy": initial_buy,
        "name": name,
        "symbol": symbol,
        "uri": uri,
    }
    payload.update(extra)
    row = seal_ingest_record(t_ws=t_ws, stream=STREAM_NEW_TOKEN, payload=payload)
    row["marketCapSol"] = market_cap
    row["vSolInBondingCurve"] = v_sol
    return row


class PopulationTests(unittest.TestCase):
    def test_bonding_create_included(self) -> None:
        row = _create_row()
        self.assertTrue(is_bonding_create(row))

    def test_migration_excluded(self) -> None:
        from observe.regime import STREAM_MIGRATION

        row = seal_ingest_record(
            t_ws=T0,
            stream=STREAM_MIGRATION,
            payload={"txType": "migration", "signature": "S", "mint": "M"},
        )
        self.assertFalse(is_bonding_create(row))


class HonestyAndEvaluateTests(unittest.TestCase):
    def test_dishonest_quote_verified_rejects(self) -> None:
        row = _create_row()
        row["knowable_at_t"]["quote_verified"] = True
        ok, _ = knowable_at_t_honest(row)
        self.assertFalse(ok)
        label, reasons = EvaluateRulesV0().evaluate(row)
        self.assertEqual(label, "reject")
        self.assertTrue(any("quote_verified" in r for r in reasons))

    def test_v0_runner_when_honest_minimal(self) -> None:
        row = _create_row(market_cap=100.0)
        row.pop("vSolInBondingCurve", None)
        row["ws_payload"].pop("vSolInBondingCurve", None)
        label, reasons = EvaluateRulesV0().evaluate(row)
        self.assertEqual(label, "runner")
        self.assertEqual(reasons, [])

    def test_v1_rejects_missing_metadata(self) -> None:
        row = _create_row(uri="")
        label, reasons = EvaluateRulesV1().evaluate(row)
        self.assertEqual(label, "reject")
        self.assertIn("missing_metadata_uri", reasons)

    def test_v1_rejects_bonk_hint(self) -> None:
        row = _create_row(name="Bonk clone", symbol="BNK")
        label, reasons = EvaluateRulesV1().evaluate(row)
        self.assertEqual(label, "reject")
        self.assertIn("bonk_pool_excluded", reasons)

    def test_v1_rejects_zero_sol_amount(self) -> None:
        row = _create_row(sol_amount=0.0)
        label, reasons = EvaluateRulesV1().evaluate(row)
        self.assertEqual(label, "reject")
        self.assertIn("zero_sol_amount", reasons)

    def test_v1_runner_in_default_band(self) -> None:
        row = _create_row()
        label, reasons = EvaluateRulesV1().evaluate(row)
        self.assertEqual(label, "runner")
        self.assertEqual(reasons, [])

    def test_v2_runner_without_narrow_v1_band(self) -> None:
        row = _create_row(market_cap=40.0, v_sol=40.0, sol_amount=5.0)
        label, reasons = EvaluateRulesV2().evaluate(row)
        self.assertEqual(label, "runner")
        self.assertEqual(reasons, [])

    def test_v2_rejects_v1_sweet_spot_that_v1_accepted(self) -> None:
        row = _create_row(market_cap=30.0, v_sol=30.5, sol_amount=1.0)
        v1_label, _ = EvaluateRulesV1().evaluate(row)
        v2_label, v2_reasons = EvaluateRulesV2().evaluate(row)
        self.assertEqual(v1_label, "runner")
        self.assertEqual(v2_label, "runner")

    def test_v2_does_not_use_bonk_soft_exclude(self) -> None:
        row = _create_row(name="Bonk clone", symbol="BNK")
        label, reasons = EvaluateRulesV2().evaluate(row)
        self.assertEqual(label, "runner")
        self.assertNotIn("bonk_pool_excluded", reasons)

    def test_v2_regime_gate_rejects_non_bonding_regime_id(self) -> None:
        row = _create_row()
        row["regime_id"] = row["regime_id"].replace("stage=bonding", "stage=pumpswap")
        reasons = _regime_bonding_gate(row)
        self.assertIn("regime_id_stage_not_bonding", reasons)
        label, eval_reasons = EvaluateRulesV2().evaluate(row)
        self.assertEqual(label, "reject")

    def test_v2_rejects_extreme_high_cap(self) -> None:
        row = _create_row(market_cap=80.0)
        label, reasons = EvaluateRulesV2().evaluate(row)
        self.assertEqual(label, "reject")
        self.assertIn("above_extreme_market_cap_sol", reasons)

    def test_v2_rejects_missing_price_proxy(self) -> None:
        row = _create_row(market_cap=30.0)
        row.pop("marketCapSol", None)
        row["ws_payload"].pop("marketCapSol", None)
        row.pop("vSolInBondingCurve", None)
        row["ws_payload"].pop("vSolInBondingCurve", None)
        label, reasons = EvaluateRulesV2().evaluate(row)
        self.assertEqual(label, "reject")
        self.assertIn("missing_price_proxy", reasons)


class GateTests(unittest.TestCase):
    def test_lift_gate_incomplete_thin_arms(self) -> None:
        self.assertEqual(_gate_lift(1.0, 0.5, 5, 5), "INCOMPLETE")

    def test_parity_gate_fail_when_means_too_close(self) -> None:
        self.assertEqual(_gate_parity(10.0, 10.2, 20, 20), "FAIL")

    def test_parity_gate_pass_when_means_diverge(self) -> None:
        self.assertEqual(_gate_parity(10.0, 20.0, 20, 20), "PASS")


class OutcomeTests(unittest.TestCase):
    def test_horizon_mark_from_later_row(self) -> None:
        t0 = _parse_iso_ts(T0)
        assert t0 is not None
        marks = build_mint_price_series(
            [
                LoadedRow(row=_create_row(t_ws=T0, market_cap=100.0), source_path="a", line_no=1),
                LoadedRow(
                    row=_create_row(t_ws=T1, mint="MintA", market_cap=110.0),
                    source_path="a",
                    line_no=2,
                ),
            ]
        )["MintA"]
        out = compute_outcomes(t0=t0, p0=100.0, marks=marks)
        h1 = out["horizons"]["1s"]
        self.assertEqual(h1["status"], "ok")
        self.assertAlmostEqual(h1["return_pct"], 10.0)

    def test_missing_post_create_is_na(self) -> None:
        t0 = _parse_iso_ts(T0)
        assert t0 is not None
        out = compute_outcomes(t0=t0, p0=100.0, marks=[])
        self.assertEqual(out["horizons"]["60s"]["status"], "na")
        self.assertIsNone(out["delta_exec"])

    def test_tick_after_horizon_does_not_fill_shorter_window(self) -> None:
        t0 = _parse_iso_ts(T0)
        assert t0 is not None
        t3 = t0 + timedelta(seconds=3)
        from tools.exp002_paper_runner import PriceMark

        marks = [PriceMark(t=t3, price=130.0, source_path="m", line_no=1)]
        out = compute_outcomes(t0=t0, p0=100.0, marks=marks)
        self.assertEqual(out["horizons"]["1s"]["status"], "na")
        self.assertEqual(out["horizons"]["5s"]["status"], "ok")
        self.assertAlmostEqual(out["horizons"]["5s"]["return_pct"], 30.0)


class IntegrationTests(unittest.TestCase):
    def test_run_writes_summary_v0(self) -> None:
        row1 = _create_row(t_ws=T0, mint="MintA", market_cap=100.0)
        row2 = _create_row(t_ws=T1, mint="MintA", market_cap=120.0)
        row3 = _create_row(t_ws=T0, mint="MintB", market_cap=50.0)
        t_end = _parse_iso_ts(T0)
        assert t_end is not None
        t_late = (t_end + timedelta(seconds=60)).isoformat(timespec="milliseconds")
        row4 = _create_row(t_ws=t_late, mint="MintB", market_cap=60.0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "observe.jsonl"
            with path.open("w", encoding="utf-8") as fh:
                for row in (row1, row2, row3, row4):
                    fh.write(json.dumps(row) + "\n")
            out_dir = Path(tmp) / "out"
            summary = run_paper_book(
                [path],
                seed=1,
                output_dir=out_dir,
                prefix="_exp002",
                rules=EvaluateRulesV0(),
            )
            self.assertEqual(summary["exp"], "EXP-002")
            self.assertGreaterEqual(summary["population_n"], 2)
            self.assertIn("overall", summary)
            self.assertTrue((out_dir / "_exp002_summary.json").is_file())

    def test_v1_labels_mixed_cohort(self) -> None:
        good = _create_row(mint="Good")
        bad = _create_row(mint="Bad", sol_amount=0.01)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "observe.jsonl"
            with path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(good) + "\n")
                fh.write(json.dumps(bad) + "\n")
            summary = run_paper_book(
                [path],
                seed=1,
                output_dir=Path(tmp) / "out",
                prefix="_exp002b",
                rules=EvaluateRulesV1(),
            )
            counts = summary["evaluate_label_counts"]
            self.assertEqual(counts.get("runner", 0), 1)
            self.assertEqual(counts.get("reject", 0), 1)
            self.assertEqual(summary["exp"], "EXP-002b")

    def test_print_summary_ascii_safe(self) -> None:
        buf = io.StringIO()
        _print_summary(
            {
                "exp": "EXP-002c",
                "rules_version": "v2",
                "population_n": 1,
                "evaluate_label_counts": {"runner": 1},
                "primary_horizon": "60s",
                "runner_cohort": {"mean_return_pct": None, "priced_n": 0},
                "reject_cohort": {"mean_return_pct": None, "priced_n": 0},
                "random_baseline": {"mean_return_pct": None, "priced_n": 0},
                "gates": {
                    "no_lift_vs_random": {"result": "INCOMPLETE"},
                    "reject_runner_parity_after_costs": {"result": "INCOMPLETE"},
                },
                "overall": "INCOMPLETE",
                "output_paths": {},
            },
            out=buf,
        )
        text = buf.getvalue()
        self.assertIn("evaluate->runner", text)
        self.assertIn("+---", text)
        self.assertNotIn("\u2192", text)

    def test_v2_integration_exp_id(self) -> None:
        row = _create_row(mint="V2")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "observe.jsonl"
            with path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            summary = run_paper_book(
                [path],
                seed=1,
                output_dir=Path(tmp) / "out",
                prefix="_exp002c",
                rules=EvaluateRulesV2(),
            )
            self.assertEqual(summary["exp"], "EXP-002c")
            self.assertEqual(summary["rules_version"], "v2")

    def test_random_baseline_caps_signatures(self) -> None:
        book = [
            BookRow(
                source_path="a",
                line_no=i,
                signature=f"sig-{i}",
                mint=f"m{i}",
                t_ws=T0,
                regime_id="env=mainnet|stage=bonding|market=bonding_curve",
                evaluate_label="runner",
                evaluate_reasons=[],
                entry_price=30.0,
                outcomes={"horizons": {"60s": {"status": "ok", "return_pct": 1.0}}},
            )
            for i in range(30)
        ]
        baseline = draw_random_baseline(book, horizon="60s", seed=1)
        self.assertEqual(len(baseline["sample_signatures"]), MAX_RANDOM_SIGNATURE_SAMPLES)
        self.assertEqual(baseline["sample_signatures_total"], 30)
        self.assertTrue(baseline["sample_signatures_truncated"])

    def test_marks_file_last_at_or_before(self) -> None:
        row = _create_row(t_ws=T0, mint="MintA", market_cap=100.0)
        mark = {
            "schema_version": "observe_mark_v0",
            "type": "outcome_mark",
            "mint": "MintA",
            "parent_signature": row["signature"],
            "t_mark": T60,
            "source": "rpc_tx",
            "price_proxy": 150.0,
        }
        leak = {
            **mark,
            "t_mark": "2026-09-20T12:00:03.000+00:00",
            "price_proxy": 999.0,
            "parent_signature": row["signature"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "observe.jsonl"
            marks_path = Path(tmp) / "marks.jsonl"
            with path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            with marks_path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(leak) + "\n")
                fh.write(json.dumps(mark) + "\n")
            out_dir = Path(tmp) / "out"
            summary = run_paper_book(
                [path],
                seed=1,
                output_dir=out_dir,
                prefix="_exp002",
                marks_paths=[marks_path],
            )
            self.assertEqual(summary["marks_paths"], [str(marks_path)])
            book_path = out_dir / "_exp002_book.jsonl"
            book_row = json.loads(book_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(book_row["horizons"]["1s"]["status"], "na")
            self.assertEqual(book_row["horizons"]["60s"]["status"], "ok")
            self.assertAlmostEqual(book_row["horizons"]["60s"]["return_pct"], 50.0)


if __name__ == "__main__":
    unittest.main()
