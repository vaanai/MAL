"""Unit tests for EXP-003 RPC backfill producer (mocked RPC, no network)."""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from observe.regime import STREAM_NEW_TOKEN, seal_ingest_record
from tools.exp003_rpc_backfill import (
    _PUMP_CREATE_EVENT_DISC,
    _PUMP_TRADE_EVENT_DISC,
    block_time_in_window,
    build_outcome_mark_row,
    collect_signatures_in_window,
    horizon_coverage_log,
    price_from_transaction,
    process_create,
    run_backfill,
    subsample_creates,
    SolanaRpcClient,
)
from tools.exp001_mislabel import LoadedRow
from tools.marks import mark_void_reason, select_last_as_of

T0 = "2026-09-20T12:00:00.000+00:00"
T_UNIX = int(datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc).timestamp())

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MAINNET_BUY_V1 = FIXTURES / "exp003_mainnet_buy_v1.json"


def _borsh_string(value: bytes) -> bytes:
    return len(value).to_bytes(4, "little") + value


def _build_create_program_data(
    *,
    virtual_token: int = 1_000_000_000_000_000,
    virtual_sol: int = 30_000_000_000,
    token_total_supply: int = 1_000_000_000_000_000,
) -> str:
    payload = bytearray(_PUMP_CREATE_EVENT_DISC)
    for part in (b"tok", b"SYM", b"https://x"):
        payload.extend(_borsh_string(part))
    payload.extend(bytes(32 * 4))
    payload.extend((0).to_bytes(8, "little", signed=True))
    payload.extend(virtual_token.to_bytes(8, "little"))
    payload.extend(virtual_sol.to_bytes(8, "little"))
    payload.extend((0).to_bytes(8, "little"))
    payload.extend(token_total_supply.to_bytes(8, "little"))
    return base64.b64encode(bytes(payload)).decode("ascii")


def _build_trade_program_data(
    *,
    virtual_sol: int = 30_089_524_961,
    virtual_token: int = 1_069_807_535_823_918,
    is_buy: bool = True,
) -> str:
    payload = bytearray(_PUMP_TRADE_EVENT_DISC)
    payload.extend(bytes(32))
    payload.extend((1_940_324).to_bytes(8, "little"))
    payload.extend((68_990_987_225).to_bytes(8, "little"))
    payload.append(1 if is_buy else 0)
    payload.extend(bytes(32))
    payload.extend((0).to_bytes(8, "little", signed=True))
    payload.extend(virtual_sol.to_bytes(8, "little"))
    payload.extend(virtual_token.to_bytes(8, "little"))
    return base64.b64encode(bytes(payload)).decode("ascii")


def _create_row(*, mint: str = "MintA", t_ws: str = T0) -> dict:
    payload = {
        "txType": "create",
        "signature": "SigCreate",
        "mint": mint,
        "marketCapSol": 100.0,
        "bondingCurveKey": "CurveAddr",
    }
    row = seal_ingest_record(t_ws=t_ws, stream=STREAM_NEW_TOKEN, payload=payload)
    row["marketCapSol"] = 100.0
    return row


def _tx_from_logs(sig: str, block_time: int, program_data_b64: str) -> dict[str, Any]:
    return {
        "blockTime": block_time,
        "version": 1,
        "meta": {
            "err": None,
            "logMessages": [
                "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                "Program log: Instruction: Buy",
                f"Program data: {program_data_b64}",
            ],
        },
        "transaction": {"signatures": [sig], "message": {"version": 1}},
    }


class WindowFilterTests(unittest.TestCase):
    def test_block_time_window(self) -> None:
        self.assertFalse(block_time_in_window(T_UNIX, T_UNIX, 60))
        self.assertTrue(block_time_in_window(T_UNIX + 1, T_UNIX, 60))
        self.assertTrue(block_time_in_window(T_UNIX + 60, T_UNIX, 60))
        self.assertFalse(block_time_in_window(T_UNIX + 61, T_UNIX, 60))


class SubsampleTests(unittest.TestCase):
    def test_subsample_reproducible(self) -> None:
        rows = [
            LoadedRow(source_path="x", line_no=i, row=_create_row(mint=f"M{i}"))
            for i in range(10)
        ]
        a = subsample_creates(rows, sample_n=4, seed=7)
        b = subsample_creates(rows, sample_n=4, seed=7)
        self.assertEqual([r.line_no for r in a], [r.line_no for r in b])


class PriceExtractTests(unittest.TestCase):
    def test_mainnet_fixture_trade_event_vsol(self) -> None:
        tx = json.loads(MAINNET_BUY_V1.read_text(encoding="utf-8"))
        parsed = price_from_transaction(tx)
        self.assertIsNotNone(parsed)
        price, field, extra = parsed
        self.assertEqual(field, "vSolInBondingCurve")
        self.assertAlmostEqual(price, 30.089524961, places=6)
        self.assertAlmostEqual(extra["vSolInBondingCurve"], price, places=6)
        self.assertEqual(extra.get("txType"), "buy")

    def test_create_event_market_cap_from_supply(self) -> None:
        b64 = _build_create_program_data()
        tx = _tx_from_logs("sigCreate", T_UNIX + 2, b64)
        parsed = price_from_transaction(tx)
        self.assertIsNotNone(parsed)
        price, field, extra = parsed
        self.assertEqual(field, "marketCapSol")
        self.assertAlmostEqual(price, 30.0, places=6)
        self.assertAlmostEqual(extra["marketCapSol"], 30.0, places=6)

    def test_trade_with_create_supply_prefers_market_cap(self) -> None:
        logs = [
            f"Program data: {_build_create_program_data()}",
            f"Program data: {_build_trade_program_data()}",
        ]
        tx = {
            "meta": {"err": None, "logMessages": logs},
            "transaction": {"signatures": ["s"]},
        }
        parsed = price_from_transaction(tx)
        self.assertIsNotNone(parsed)
        price, field, _extra = parsed
        self.assertEqual(field, "marketCapSol")
        self.assertGreater(price, 0)

    def test_no_logs_no_invented_price(self) -> None:
        tx = {
            "blockTime": T_UNIX + 2,
            "meta": {"err": None, "logMessages": ["Program log: noop"]},
            "transaction": {"signatures": ["s"]},
        }
        self.assertIsNone(price_from_transaction(tx))

    def test_failed_tx_skipped(self) -> None:
        b64 = _build_trade_program_data()
        tx = _tx_from_logs("s", T_UNIX + 2, b64)
        tx["meta"] = {"err": {"InstructionError": [0, "Custom"]}}
        self.assertIsNone(price_from_transaction(tx))


class MockRpcTests(unittest.TestCase):
    def test_get_transaction_requests_version_one(self) -> None:
        seen: list[Any] = []

        def post(method: str, params: list[Any]) -> Any:
            if method == "getTransaction":
                seen.append(params)
                return {"meta": {"err": None, "logMessages": []}}
            return []

        client = SolanaRpcClient(url="http://mock", _post_fn=post)
        client.get_transaction("sigZ")
        self.assertEqual(len(seen), 1)
        cfg = seen[0][1]
        self.assertEqual(cfg.get("maxSupportedTransactionVersion"), 1)

    def test_collect_signatures_paginates_until_before_t(self) -> None:
        pages = {
            None: [
                {"signature": "sig3", "blockTime": T_UNIX + 30, "err": None},
                {"signature": "sig2", "blockTime": T_UNIX + 5, "err": None},
            ],
            "sig2": [
                {"signature": "sig1", "blockTime": T_UNIX - 1, "err": None},
            ],
        }

        def post(method: str, params: list[Any]) -> Any:
            self.assertEqual(method, "getSignaturesForAddress")
            cfg = params[1]
            before = cfg.get("before")
            return pages.get(before, [])

        client = SolanaRpcClient(url="http://mock", _post_fn=post)
        sigs = collect_signatures_in_window(
            client, "CurveAddr", t_unix=T_UNIX, window_s=60, page_limit=2
        )
        self.assertEqual(len(sigs), 2)
        self.assertEqual({s["signature"] for s in sigs}, {"sig3", "sig2"})

    def test_process_create_writes_valid_marks(self) -> None:
        trade_b64 = _build_trade_program_data()
        sig_infos = [
            {"signature": "sigA", "blockTime": T_UNIX + 1, "err": None},
            {"signature": "sigB", "blockTime": T_UNIX + 10, "err": None},
            {"signature": "sigC", "blockTime": T_UNIX + 70, "err": None},
        ]
        txs = {
            "sigA": _tx_from_logs("sigA", T_UNIX + 1, trade_b64),
            "sigB": _tx_from_logs(
                "sigB",
                T_UNIX + 10,
                _build_trade_program_data(virtual_sol=31_000_000_000),
            ),
            "sigC": _tx_from_logs("sigC", T_UNIX + 70, trade_b64),
        }

        def post(method: str, params: list[Any]) -> Any:
            if method == "getSignaturesForAddress":
                return sig_infos
            if method == "getTransaction":
                return txs[params[0]]
            raise AssertionError(method)

        client = SolanaRpcClient(url="http://mock", _post_fn=post)
        create = _create_row()
        marks, stats = process_create(
            client, create, window_s=60, commitment="confirmed"
        )
        self.assertEqual(stats["marks_n"], 2)
        self.assertEqual(len(marks), 2)
        for row in marks:
            self.assertIsNone(mark_void_reason(row))
            self.assertEqual(row["price_field"], "vSolInBondingCurve")
        t_decision = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
        ticks = [
            (datetime.fromtimestamp(T_UNIX + 1, tz=timezone.utc), marks[0]["price_proxy"]),
            (datetime.fromtimestamp(T_UNIX + 10, tz=timezone.utc), marks[1]["price_proxy"]),
        ]
        chosen_1s = select_last_as_of(ticks, t_decision, 1.0)
        self.assertIsNotNone(chosen_1s)
        assert chosen_1s is not None
        self.assertEqual(chosen_1s[1], marks[0]["price_proxy"])
        self.assertIn("1s=ok", stats.get("horizons", ""))

    def test_process_create_counts_no_price_without_logs(self) -> None:
        sig_infos = [{"signature": "sigA", "blockTime": T_UNIX + 1, "err": None}]
        txs = {
            "sigA": {
                "blockTime": T_UNIX + 1,
                "meta": {"err": None, "logMessages": []},
                "transaction": {"signatures": ["sigA"]},
            }
        }

        def post(method: str, params: list[Any]) -> Any:
            if method == "getSignaturesForAddress":
                return sig_infos
            if method == "getTransaction":
                return txs[params[0]]
            raise AssertionError(method)

        client = SolanaRpcClient(url="http://mock", _post_fn=post)
        marks, stats = process_create(
            client, _create_row(), window_s=60, commitment="confirmed"
        )
        self.assertEqual(marks, [])
        self.assertEqual(stats["no_price_n"], 1)

    def test_run_backfill_appends_jsonl(self) -> None:
        trade_b64 = _build_trade_program_data()
        sig_infos = [{"signature": "sigA", "blockTime": T_UNIX + 3, "err": None}]
        txs = {"sigA": _tx_from_logs("sigA", T_UNIX + 3, trade_b64)}

        def post(method: str, params: list[Any]) -> Any:
            if method == "getSignaturesForAddress":
                return sig_infos
            if method == "getTransaction":
                return txs[params[0]]
            raise AssertionError(method)

        client = SolanaRpcClient(url="http://mock", _post_fn=post)
        with tempfile.TemporaryDirectory() as tmp:
            observe = Path(tmp) / "observe.jsonl"
            with observe.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(_create_row()) + "\n")
            out_dir = Path(tmp) / "out"
            summary = run_backfill(
                observe_paths=[observe],
                output_dir=out_dir,
                sample_n=0,
                seed=1,
                window_s=60,
                commitment="confirmed",
                rpc_url="http://mock",
                client=client,
            )
            marks_path = out_dir / "marks-2026-09-20.jsonl"
            self.assertTrue(marks_path.is_file())
            self.assertEqual(summary["marks_written_n"], 1)
            line = marks_path.read_text(encoding="utf-8").strip()
            row = json.loads(line)
            self.assertEqual(row["type"], "outcome_mark")
            self.assertEqual(row["parent_signature"], "SigCreate")
            self.assertGreater(row["price_proxy"], 0)


class MarkRowTests(unittest.TestCase):
    def test_build_row_matches_schema(self) -> None:
        create = _create_row()
        row = build_outcome_mark_row(
            create=create,
            tx_signature="sigX",
            block_time=T_UNIX + 5,
            price=99.5,
            price_field="vSolInBondingCurve",
            commitment="confirmed",
        )
        self.assertIsNone(mark_void_reason(row))

    def test_horizon_log_sparse(self) -> None:
        t0 = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
        t5 = datetime(2026, 9, 20, 12, 0, 5, tzinfo=timezone.utc)
        msg = horizon_coverage_log(t0, [(t5, 120.0)])
        self.assertIn("1s=na", msg)
        self.assertIn("5s=ok", msg)


if __name__ == "__main__":
    unittest.main()
