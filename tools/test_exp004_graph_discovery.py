"""Unit tests for EXP-004 graph Discovery runner (offline fixtures)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from observe.regime import STREAM_NEW_TOKEN, seal_ingest_record
from tools.exp004_graph_discovery import (
    BURST_LOOKBACK_S,
    precompute_graph_book,
    regime_gate_key,
    run_exp004,
)
from tools.exp001_mislabel import LoadedRow

T0 = "2026-09-20T12:00:00.000+00:00"
T1 = "2026-09-20T12:00:05.000+00:00"
T2 = "2026-09-20T12:00:10.000+00:00"


def _create_row(
    *,
    t_ws: str,
    mint: str,
    trader: str = "CreatorPk1",
    initial_buy: float = 1_000_000.0,
    sol_amount: float = 1.0,
    signature: str | None = None,
) -> dict:
    sig = signature or f"Sig-{mint}-{t_ws}"
    payload = {
        "txType": "create",
        "signature": sig,
        "mint": mint,
        "traderPublicKey": trader,
        "initialBuy": initial_buy,
        "solAmount": sol_amount,
        "marketCapSol": 30.0,
        "vSolInBondingCurve": 30.5,
        "name": "Tok",
        "symbol": "TOK",
        "uri": "https://example.com/m.json",
    }
    row = seal_ingest_record(t_ws=t_ws, stream=STREAM_NEW_TOKEN, payload=payload)
    row["marketCapSol"] = 30.0
    return row


def _outcome_mark(
    *,
    parent_signature: str,
    mint: str,
    t_mark: str,
    price: float,
) -> dict:
    return {
        "schema_version": "observe_mark_v0",
        "type": "outcome_mark",
        "mint": mint,
        "parent_signature": parent_signature,
        "t_mark": t_mark,
        "source": "rpc_tx",
        "price_proxy": price,
    }


class RegimeGateKeyTests(unittest.TestCase):
    def test_missing_regime_stub(self) -> None:
        self.assertTrue(regime_gate_key(None).startswith("regime_gate=unknown"))


class PrecomputeTests(unittest.TestCase):
    def test_prior_mint_count_increments_per_regime(self) -> None:
        rows = [
            LoadedRow(row=_create_row(t_ws=T0, mint="M1"), source_path="f", line_no=1),
            LoadedRow(row=_create_row(t_ws=T1, mint="M2"), source_path="f", line_no=2),
        ]
        pairs, meta = precompute_graph_book(rows, burst_lookback_s=BURST_LOOKBACK_S)
        self.assertEqual(meta["graph_scored_n"], 2)
        self.assertEqual(pairs[0][1].prior_mint_count, 0)
        self.assertEqual(pairs[1][1].prior_mint_count, 1)

    def test_recurrence_weak_when_pattern_repeats(self) -> None:
        rows = [
            LoadedRow(
                row=_create_row(t_ws=T0, mint="M1", initial_buy=100.0, sol_amount=2.0),
                source_path="f",
                line_no=1,
            ),
            LoadedRow(
                row=_create_row(t_ws=T1, mint="M2", initial_buy=100.0, sol_amount=2.0),
                source_path="f",
                line_no=2,
            ),
        ]
        pairs, _ = precompute_graph_book(rows)
        self.assertFalse(pairs[0][1].recurrence_weak)
        self.assertTrue(pairs[1][1].recurrence_weak)


class RunnerIntegrationTests(unittest.TestCase):
    def test_run_produces_gates_and_report(self) -> None:
        r0 = _create_row(t_ws=T0, mint="M1", signature="Sig1")
        r1 = _create_row(t_ws=T1, mint="M2", signature="Sig2")
        r2 = _create_row(t_ws=T2, mint="M3", signature="Sig3")
        marks = [
            _outcome_mark(parent_signature="Sig1", mint="M1", t_mark=T1, price=33.0),
            _outcome_mark(parent_signature="Sig2", mint="M2", t_mark=T2, price=36.0),
            _outcome_mark(parent_signature="Sig3", mint="M3", t_mark="2026-09-20T12:01:05.000+00:00", price=39.0),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "observe.jsonl"
            mpath = Path(tmp) / "marks.jsonl"
            with obs.open("w") as fh:
                for row in (r0, r1, r2):
                    fh.write(json.dumps(row) + "\n")
            with mpath.open("w") as fh:
                for row in marks:
                    fh.write(json.dumps(row) + "\n")
            out_dir = Path(tmp) / "out"
            summary = run_exp004(
                [obs],
                marks_paths=[mpath],
                output_dir=out_dir,
                prefix="_exp004_test",
            )
            self.assertIn("hypotheses", summary)
            self.assertEqual(summary["population_n"], 3)
            hg4 = summary["hypotheses"]["H-G4"][ "60s"]
            self.assertEqual(hg4["overall"], "INCOMPLETE")
            self.assertIn("empty by construction", hg4["sparse_note"] or "")
            report = (out_dir / "_exp004_test_report.md").read_text()
            self.assertIn("Proof GATE checklist", report)
            self.assertIn("H-G4", report)

    def test_no_observe_paths_lists_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = run_exp004([], output_dir=Path(tmp))
            self.assertTrue(summary["data_blockers"])
            self.assertEqual(summary["overall"], "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
