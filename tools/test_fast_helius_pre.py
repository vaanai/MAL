"""Mint-authority and early-trade preprocessed listeners. No network."""

from __future__ import annotations

import base64
import json
import os
import stat
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools.fast_helius_pre import (
    ACCOUNT_CAP,
    CREATE_DAILY_CAP,
    MIGRATION_ACCOUNT,
    MINT_AUTHORITY,
    PUMP_PROGRAM,
    TRADE_DAILY_CAP,
    CreditMeter,
    CurveBook,
    DailyCreditGate,
    account_filter_decision,
    b58decode,
    b58encode,
    create_record,
    credits_per_mint_summary,
    keep_early_trade,
    load_env_file,
    parse_preprocessed_frame,
    parse_wire_transaction,
    preprocessed_subscribe_request,
    project_daily_credits,
    pump_actions,
    redact,
    sealed_trades_from_logs,
    seen_records,
    stamp_fast_recv,
    trade_coverage,
)

ROOT = Path(__file__).resolve().parents[1]
UNIT_CREATE = ROOT / "scripts" / "mal-core" / "mal-fast-pre-create.service"
UNIT_TRADE = ROOT / "scripts" / "mal-core" / "mal-fast-early-trade.service"
INSTALL = ROOT / "scripts" / "mal-core" / "install-fast-helius-listener.sh"

_CREATE_V2 = bytes.fromhex("d6904cec5f8b31b4")
_BUY = bytes.fromhex("66063d1201daebea")
_MIGRATE_V2 = bytes.fromhex("bbcb121fceedfe29")


def _key(byte: int) -> bytes:
    return bytes([byte]) * 32


def _compact(value: int) -> bytes:
    if value < 0x80:
        return bytes([value])
    if value < 0x4000:
        return bytes([(value & 0x7F) | 0x80, value >> 7])
    raise ValueError("test compact")


def _legacy_tx(keys: list[bytes], program_index: int, accounts: list[int], data: bytes) -> bytes:
    sig = bytes([9]) * 64
    msg = bytes([1, 0, max(0, len(keys) - 1)])
    msg += _compact(len(keys))
    for key in keys:
        msg += key
    msg += _key(4)
    msg += _compact(1)
    msg += bytes([program_index])
    msg += _compact(len(accounts)) + bytes(accounts)
    msg += _compact(len(data)) + data
    return _compact(1) + sig + msg


class ParserTests(unittest.TestCase):
    def test_create_v2_static_accounts(self) -> None:
        user, mint, curve, pump = _key(2), _key(3), _key(4), b58decode(PUMP_PROGRAM)
        raw = _legacy_tx([user, mint, curve, pump], 3, [1, 0, 2], _CREATE_V2 + b"\x00")
        tx = parse_wire_transaction(raw)
        actions = pump_actions(tx, [])
        self.assertEqual(actions[0]["instr"], "create_v2")
        self.assertEqual(actions[0]["mint"], b58encode(mint))
        self.assertEqual(actions[0]["bonding_curve"], b58encode(curve))
        record = create_record("sig", 9, 1_000, actions)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["schema"], "fast_pre_create_v0")
        self.assertNotIn("sol_lamports", record)

    def test_buy_hint_does_not_copy_sol_limit(self) -> None:
        user, mint, curve, pump = _key(2), _key(3), _key(4), b58decode(PUMP_PROGRAM)
        data = _BUY + (50_000_000_000).to_bytes(8, "little") + (99_000_000_000).to_bytes(8, "little")
        # buy layout: global, fee, mint, curve at instruction indexes 2 and 3.
        keys = [_key(8), _key(7), user, mint, curve, pump]
        raw = _legacy_tx(keys, 5, [0, 1, 3, 4, 2], data)
        actions = pump_actions(parse_wire_transaction(raw), [])
        self.assertEqual(actions[0]["kind"], "buy")
        self.assertEqual(actions[0]["mint"], b58encode(mint))
        rows = seen_records("sig", 3, 50, actions)
        self.assertEqual(rows[0]["side"], "buy")
        self.assertEqual(rows[0]["ix_event_index"], 0)
        self.assertNotIn("sol_lamports", rows[0])
        self.assertNotIn("quote_reserve", rows[0])

    def test_preprocessed_frame_roundtrip(self) -> None:
        user, mint, curve, pump = _key(2), _key(3), _key(4), b58decode(PUMP_PROGRAM)
        wire = _legacy_tx([user, mint, curve, pump], 3, [1, 0, 2], _CREATE_V2)
        sig = wire[1:65]
        frame = bytes([1]) + (450).to_bytes(8, "little") + sig + wire
        parsed = parse_preprocessed_frame(frame)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["slot"], 450)
        self.assertEqual(parsed["signature"], b58encode(sig))
        self.assertEqual(parse_wire_transaction(parsed["wire"])["signatures"][0], b58encode(sig))

    def test_v1_create_accounts(self) -> None:
        user, mint, curve, pump = _key(2), _key(3), _key(4), b58decode(PUMP_PROGRAM)
        keys = [user, mint, curve, pump]
        ix_accounts = [1, 0, 2]
        data = _CREATE_V2 + b"\x00"
        body = bytes([0x81, 1, 0, 3])
        body += (0).to_bytes(4, "little")
        body += bytes(32)
        body += bytes([1, len(keys)])
        for key in keys:
            body += key
        body += bytes([3, len(ix_accounts)]) + len(data).to_bytes(2, "little")
        body += bytes(ix_accounts) + data
        body += bytes([9]) * 64
        actions = pump_actions(parse_wire_transaction(body), [])
        self.assertEqual(actions[0]["instr"], "create_v2")
        self.assertEqual(actions[0]["mint"], b58encode(mint))
        self.assertEqual(actions[0]["bonding_curve"], b58encode(curve))

    def test_migrate_v2_accounts(self) -> None:
        mint, curve, pump = _key(3), _key(4), b58decode(PUMP_PROGRAM)
        keys = [_key(1), _key(6), mint, _key(7), curve, pump]
        # migrate_v2: base mint at 2, bonding curve at 4.
        raw = _legacy_tx(keys, 5, [0, 1, 2, 3, 4], _MIGRATE_V2)
        actions = pump_actions(parse_wire_transaction(raw), [])
        self.assertEqual(actions[0]["kind"], "migrate")
        self.assertEqual(actions[0]["mint"], b58encode(mint))
        self.assertEqual(actions[0]["bonding_curve"], b58encode(curve))


class FilterTests(unittest.TestCase):
    def test_create_request_is_mint_authority_only(self) -> None:
        req = preprocessed_subscribe_request([MINT_AUTHORITY], 1)
        self.assertEqual(req["method"], "preprocessedSubscribe")
        self.assertEqual(req["params"]["accountInclude"], [MINT_AUTHORITY])
        self.assertIsInstance(req["params"], dict)

    def test_rejects_program_and_empty_and_over_cap(self) -> None:
        self.assertEqual(account_filter_decision([])[1], "empty")
        self.assertEqual(account_filter_decision([PUMP_PROGRAM])[1], "program_wide")
        wide = [f"curve{i}" for i in range(ACCOUNT_CAP + 1)]
        self.assertEqual(account_filter_decision(wide)[1], "over_account_cap")
        ok, reason = account_filter_decision([MIGRATION_ACCOUNT, "curve111"])
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_curve_book_ttl_and_cap(self) -> None:
        book = CurveBook([MIGRATION_ACCOUNT], cap=2, ttl_s=120)
        self.assertEqual(book.add("curveA", "mintA", 10), "added")
        self.assertEqual(book.add("curveA", "mintA", 50), "present")
        self.assertEqual(book.curves["curveA"][1], 130)
        self.assertEqual(book.add("curveB", "mintB", 10), "cap")
        self.assertEqual(book.add(PUMP_PROGRAM, "mintB", 10), "rejected")
        self.assertEqual(book.expire(130), ["curveA"])
        self.assertEqual(book.accounts(), [MIGRATION_ACCOUNT])


class CreditTests(unittest.TestCase):
    def test_probe_trip_near_20000(self) -> None:
        meter = CreditMeter(trip=20_000, rate_max=10_000_000)
        tripped = False
        for i in range(200_000):
            tripped = meter.note(float(i))
            if tripped:
                break
        self.assertTrue(tripped)
        self.assertEqual(meter.reason, "credit_trip")
        self.assertGreaterEqual(meter.credits, 20_000 - 1e-4)

    def test_daily_gate_holds_until_next_utc_day(self) -> None:
        clock = {"now": datetime(2026, 9, 27, 23, 0, tzinfo=timezone.utc)}

        def now() -> datetime:
            return clock["now"]

        with self._tmp() as path:
            gate = DailyCreditGate(path, cap=0.2, clock=now)
            self.assertFalse(gate.note())
            self.assertTrue(gate.note())
            self.assertTrue(gate.tripped)
            again = DailyCreditGate(path, cap=0.2, clock=now)
            self.assertTrue(again.tripped)
            self.assertGreaterEqual(again.credits, 0.2 - 1e-6)
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)
            clock["now"] = datetime(2026, 9, 28, 0, 1, tzinfo=timezone.utc)
            resumed = DailyCreditGate(path, cap=0.2, clock=now)
            self.assertFalse(resumed.tripped)
            self.assertEqual(resumed.credits, 0)

    def test_env_file_mode(self) -> None:
        with self._tmp() as path:
            path.write_text("HELIUS_API_KEY=not-a-real-key\n", encoding="utf-8")
            os.chmod(path, 0o644)
            os.environ.pop("HELIUS_API_KEY", None)
            with self.assertRaises(PermissionError):
                load_env_file(path)
            os.chmod(path, 0o600)
            self.assertTrue(load_env_file(path))
            self.assertEqual(os.environ.pop("HELIUS_API_KEY"), "not-a-real-key")

    def _tmp(self) -> "TempPath":
        return TempPath()


class TempPath:
    def __enter__(self) -> Path:
        import tempfile

        self._dir = tempfile.TemporaryDirectory()
        return Path(self._dir.name) / "state.json"

    def __exit__(self, *exc: object) -> None:
        self._dir.cleanup()


class DecodeTests(unittest.TestCase):
    def test_seal_matches_tape_fields_and_stamp_keeps_amounts(self) -> None:
        mint = _key(3)
        trader = _key(6)
        raw = bytearray(113)
        raw[:8] = bytes.fromhex("bddb7fd34ee661ee")
        raw[8:40] = mint
        raw[40:48] = (2_000_000).to_bytes(8, "little")
        raw[48:56] = (5_000_000).to_bytes(8, "little")
        raw[56] = 1
        raw[57:89] = trader
        raw[89:97] = (1_750_000_000).to_bytes(8, "little", signed=True)
        raw[97:105] = (30_000_000_000).to_bytes(8, "little")
        raw[105:113] = (1_000_000_000_000).to_bytes(8, "little")
        line = "Program data: " + base64.b64encode(bytes(raw)).decode("ascii")
        rows = sealed_trades_from_logs([line], slot=4, signature="sig", t_recv_ms=1_000)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        for key in (
            "v",
            "type",
            "side",
            "sol_lamports",
            "token_raw",
            "quote_reserve",
            "base_reserve",
            "signature",
            "event_index",
            "t_recv_ms",
            "slot",
        ):
            self.assertIn(key, row)
        self.assertEqual(row["side"], "buy")
        self.assertEqual(row["event_index"], 0)
        self.assertEqual(row["sol_lamports"], 2_000_000)
        stamped = stamp_fast_recv(row, 900, slot=4)
        self.assertEqual(stamped["sol_lamports"], 2_000_000)
        self.assertEqual(stamped["t_recv_ms"], 900)
        self.assertEqual(stamped["t_logs_ms"], 1_000)
        self.assertEqual(stamped["t_recv_source"], "preprocessed")


class CoverageTests(unittest.TestCase):
    def test_later_trades_ignore_create_signature(self) -> None:
        creates = [{"mint": "m", "signature": "create", "t_recv_ms": 1_000_000}]
        seen = [{"signature": "buy1", "t_recv_ms": 1_000_100}]
        tape = [
            {"mint": "m", "signature": "create", "t_recv_ms": 1_000_050},
            {"mint": "m", "signature": "buy1", "t_recv_ms": 1_000_180},
            {"mint": "m", "signature": "late", "t_recv_ms": 1_000_000 + 130_000},
        ]
        report = trade_coverage(creates, seen, tape)
        later = report["later_trades"]
        self.assertEqual(later["oracle_signatures"], 1)
        self.assertEqual(later["covered"], 1)
        self.assertEqual(later["coverage"], 1)
        self.assertEqual(later["median_oracle_minus_fast_ms"], 80)
        self.assertEqual(report["including_create_tx"]["oracle_signatures"], 2)
        self.assertEqual(report["including_create_tx"]["covered"], 1)
        self.assertFalse(keep_early_trade(100_001, 0.99))
        self.assertFalse(keep_early_trade(10_000, 0.94))
        self.assertTrue(keep_early_trade(100_000, 0.95))
        self.assertFalse(keep_early_trade(1, None))

    def test_projection_and_per_mint(self) -> None:
        self.assertAlmostEqual(project_daily_credits(63.2, 1800.823), 63.2 * 86400 / 1800.823)
        summary = credits_per_mint_summary({"a": 2, "b": 4})
        self.assertEqual(summary["mints"], 2)
        self.assertAlmostEqual(summary["mean_credits"], 0.3)


class UnitFileTests(unittest.TestCase):
    def test_units_and_install_are_listener_only(self) -> None:
        create = UNIT_CREATE.read_text(encoding="utf-8")
        trade = UNIT_TRADE.read_text(encoding="utf-8")
        install = INSTALL.read_text(encoding="utf-8")
        for text in (create, trade):
            self.assertIn("MemoryMax=1G", text)
            self.assertIn("Nice=0", text)
            self.assertIn("EnvironmentFile=/var/lib/mal/fast-listener/helius.env", text)
            self.assertIn("Restart=on-failure", text)
            self.assertNotIn("Restart=always", text)
            self.assertNotIn("HELIUS_API_KEY=", text)
        self.assertIn("--daily-cap 10000", create)
        self.assertIn("--daily-cap 100000", trade)
        self.assertIn("--credit-trip 0", trade)
        self.assertNotIn("mal-fast-create.service", install)
        self.assertNotIn("mal-observe", install)
        self.assertNotIn("mal-trade-tape", install)
        self.assertNotIn("rm -rf", install)
        self.assertIn("mal-fast-pre-create.service", install)
        joined = create + trade + install
        self.assertNotIn("fill_sim", joined)
        self.assertNotIn("promotion", joined)


class RedactTests(unittest.TestCase):
    def test_redact(self) -> None:
        os.environ["HELIUS_API_KEY"] = "secret-value"
        try:
            self.assertNotIn("secret-value", redact("url api-key=secret-value&x=1"))
        finally:
            os.environ.pop("HELIUS_API_KEY", None)


class CapConstantTests(unittest.TestCase):
    def test_caps(self) -> None:
        self.assertEqual(CREATE_DAILY_CAP, 10_000)
        self.assertEqual(TRADE_DAILY_CAP, 100_000)
        self.assertEqual(ACCOUNT_CAP, 5_000)
        self.assertEqual(stat.S_IMODE(0o100600), 0o600)
