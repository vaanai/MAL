"""Synthetic tests for tools/tip_migration_probe.py. No network: the RPC function is a mock."""
from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from observe.trade_decode import ANCHOR_EVENT_IX_TAG, PUMP_BONDING_PROGRAM, b58encode
from tools import tip_migration_probe as tmp
from tools.pump_history_backfill import _MIGRATE_DISC

T0 = tmp.FLOOR_TS + 3600
SIG_MARK = "SIGMARKERzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"


def pk(n: int) -> bytes:
    return bytes([n]) * 32


def mig_blob(ts: int = T0) -> bytes:
    raw = bytearray(200)
    raw[:8] = _MIGRATE_DISC
    raw[8:40] = pk(1)
    raw[40:72] = pk(2)
    raw[72:80] = (123456789).to_bytes(8, "little")
    raw[80:88] = (987654321).to_bytes(8, "little")
    raw[96:128] = pk(3)
    raw[128:136] = ts.to_bytes(8, "little", signed=True)
    raw[136:168] = pk(4)
    raw[168:200] = pk(5)
    return bytes(raw)


def pdata(blob: bytes) -> str:
    return "Program data: " + base64.b64encode(blob).decode()


def tx(logs, inner_blob=None, sig=SIG_MARK):
    keys = [b58encode(pk(9)), PUMP_BONDING_PROGRAM]
    inner = []
    if inner_blob is not None:
        data = b58encode(bytes(ANCHOR_EVENT_IX_TAG) + inner_blob)
        inner = [{"index": 0, "instructions": [{"programIdIndex": 1, "accounts": [], "data": data}]}]
    return {
        "slot": 1000,
        "blockTime": T0,
        "transaction": {"signatures": [sig], "message": {"accountKeys": keys, "instructions": []}},
        "meta": {"err": None, "logMessages": logs, "innerInstructions": inner},
    }


TX_LOG = tx(["Program log: hi", pdata(mig_blob()), "Program log: Instruction: InitBoost"])
TX_INNER = tx(["Program log: a", "Log truncated"], inner_blob=mig_blob())
TX_NEITHER = tx(["Program log: a"])


class CountTests(unittest.TestCase):
    def test_counts(self):
        c = tmp.count_txs([TX_LOG, TX_INNER, TX_NEITHER])
        self.assertEqual(c["n_tx"], 3)
        self.assertEqual(c["n_log_truncated"], 1)
        self.assertEqual((c["n_disc_in_logs"], c["n_disc_inner_only"], c["n_disc_neither"]), (1, 1, 1))
        self.assertEqual(c["n_init_boost"], 1)
        self.assertEqual(c["log_lines_max"], 3)
        self.assertEqual(c["log_lines_p50"], 2)
        self.assertGreater(c["log_bytes_max"], c["log_bytes_p50"])
        # legacy sees the log-path tx only; event_v also recovers the inner-only one
        self.assertEqual((c["legacy_migration_rows"], c["legacy_txs_with_row"]), (1, 1))
        self.assertEqual((c["event_v_migration_rows"], c["event_v_txs_with_row"]), (2, 2))

    def test_empty(self):
        c = tmp.count_txs([])
        self.assertEqual(c["n_tx"], 0)
        self.assertEqual(c["log_bytes_max"], 0)


def make_mock(n_ok=3, n_old=2):
    sigs = [{"signature": f"ok{i}", "slot": 1, "blockTime": T0 + 100 - i, "err": None} for i in range(n_ok)]
    sigs.insert(1, {"signature": "bad", "slot": 1, "blockTime": T0, "err": {"x": 1}})
    sigs += [{"signature": f"old{i}", "slot": 1, "blockTime": tmp.FLOOR_TS - 10 - i, "err": None} for i in range(n_old)]
    calls = []

    def rpc(method, params):
        calls.append((method, params))
        if method == "getSignaturesForAddress":
            before = params[1].get("before")
            if before:
                idx = [s["signature"] for s in sigs].index(before)
                return sigs[idx + 1:idx + 3]
            return sigs[:2]
        if method == "getTransaction":
            return TX_LOG if params[0] != "ok1" else TX_INNER
        raise AssertionError(method)

    return rpc, calls


class FlowTests(unittest.TestCase):
    def test_paginates_filters_floor_and_err(self):
        rpc, calls = make_mock()
        c = tmp.run(rpc, 50, tmp.FLOOR_TS)
        self.assertEqual(c["n_tx"], 3)  # err and pre-floor skipped
        self.assertEqual(sum(1 for m, _ in calls if m == "getTransaction"), 3)

    def test_max_tx_stops(self):
        rpc, _ = make_mock()
        self.assertEqual(tmp.run(rpc, 2, tmp.FLOOR_TS)["n_tx"], 2)

    def test_v1_fallback(self):
        seen = []

        def rpc(method, params):
            if method == "getSignaturesForAddress":
                return [] if "before" in params[1] else [{"signature": "a", "slot": 1, "blockTime": T0, "err": None}]
            seen.append(params[1]["maxSupportedTransactionVersion"])
            if params[1]["maxSupportedTransactionVersion"] == 0:
                raise RuntimeError("x")
            return TX_LOG

        self.assertEqual(tmp.run(rpc, 5, tmp.FLOOR_TS)["n_tx"], 1)
        self.assertEqual(seen, [0, 1])


class RefusalTests(unittest.TestCase):
    def run_main(self, argv, rpc=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = tmp.main(argv, rpc=rpc)
        return rc, out.getvalue(), err.getvalue()

    def no_call(self, m, p):
        self.fail("rpc must not be called on a refusal")

    def test_helius_refused(self):
        for u in ("https://mainnet.helius-rpc.com", "https://x.example/?api-key=abc",
                  "http://api.mainnet-beta.solana.com"):
            rc, out, _ = self.run_main(["--url", u], rpc=self.no_call)
            self.assertEqual((rc, out), (2, ""))

    def test_out_refused(self):
        for w in ("probe", "shadow", "sealed", "tape", "arena", "tip"):
            rc, out, _ = self.run_main(["--out", f"/tmp/x/{w.upper()}/o.json"], rpc=self.no_call)
            self.assertEqual((rc, out), (2, ""))

    def test_max_tx_over_50_refused(self):
        rc, _, _ = self.run_main(["--max-tx", "51"], rpc=self.no_call)
        self.assertEqual(rc, 2)

    def test_floor_refused(self):
        rc, _, _ = self.run_main(["--floor", "2026-10-31T23:59:59Z"], rpc=self.no_call)
        self.assertEqual(rc, 2)
        rc, _, _ = self.run_main(["--floor", "garbage"], rpc=self.no_call)
        self.assertEqual(rc, 2)

    def test_default_url_ok(self):
        tmp.check_url(tmp.DEFAULT_URL)


class OutputTests(unittest.TestCase):
    def test_only_count_keys_and_no_identifiers(self):
        rpc, _ = make_mock()
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "counts.json")
            out = io.StringIO()
            with redirect_stdout(out):
                rc = tmp.main(["--out", path], rpc=rpc)
            self.assertEqual(rc, 0)
            written = Path(path).read_text()
        self.assertEqual(out.getvalue().strip(), written.strip())
        data = json.loads(written)
        self.assertEqual(tuple(data), tmp.COUNT_KEYS)
        self.assertTrue(all(isinstance(v, (int, dict)) for v in data.values()))
        text = out.getvalue() + written
        forbidden = [SIG_MARK, "ok0", tmp.MIGRATION_ACCOUNT, b58encode(pk(2)), b58encode(pk(4)),
                     "123456789", "987654321", "mint", "pool", "signature", "price", "reserve", "amount"]
        for f in forbidden:
            self.assertNotIn(f, text)

    def test_rpc_error_withholds_message(self):
        def rpc(method, params):
            raise RuntimeError(f"{method} boom {SIG_MARK}")

        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            rc = tmp.main([], rpc=rpc)
        self.assertEqual(rc, 1)
        self.assertNotIn(SIG_MARK, err.getvalue())

    def test_make_rpc_strips_message(self):
        with mock.patch.object(tmp, "rpc_call", side_effect=RuntimeError(f"m {SIG_MARK}")):
            with self.assertRaises(RuntimeError) as cm:
                tmp.make_rpc("https://api.mainnet-beta.solana.com")("getTransaction", [])
        self.assertNotIn(SIG_MARK, str(cm.exception))


def comp_blob(mint_b: bytes, ts: int = T0) -> bytes:
    raw = bytearray(144)
    raw[:8] = tmp._COMPLETE_DISC
    raw[8:40] = pk(1)
    raw[40:72] = mint_b
    raw[72:104] = pk(3)
    raw[104:112] = ts.to_bytes(8, "little", signed=True)
    return bytes(raw)


MIGRATE_IX = bytes.fromhex("9beae792ec9ea21e") + b"\0" * 8
OTHER_IX = bytes.fromhex("1122334455667788") + b"\0" * 4


def creation_tx(logs, ixs, signer=tmp.MIGRATION_ACCOUNT, bt=T0 + 3, sig="creationsig"):
    keys = [signer, PUMP_BONDING_PROGRAM, tmp.PUMPSWAP_PROGRAM, b58encode(pk(9))]
    return {
        "slot": 1003, "blockTime": bt,
        "transaction": {"signatures": [sig], "message": {
            "header": {"numRequiredSignatures": 1}, "accountKeys": keys,
            "instructions": [{"programIdIndex": i, "accounts": [], "data": b58encode(d)} for i, d in ixs]}},
        "meta": {"err": None, "logMessages": logs, "innerInstructions": []},
    }


class GraduationTests(unittest.TestCase):
    def setUp(self):
        self.mint = b58encode(pk(2))
        self.ctx = tx([pdata(comp_blob(pk(2)))], sig="completesig")
        self.creation = creation_tx(
            [pdata(mig_blob()), pdata(bytes.fromhex("aabbccddeeff0011") + b"\0" * 8)],
            [(1, MIGRATE_IX), (1, OTHER_IX), (3, b"")])
        self.rpc, self.calls = self._mock(self.creation)

    def _mock(self, creation):
        calls = []
        pool = tmp.canonical_pool(self.mint)
        block = {"blockTime": T0, "transactions": [self.ctx, TX_NEITHER]}

        def rpc(method, params):
            calls.append((method, params))
            if method == "getSlot":
                return 5000
            if method == "getBlock":
                return block if params[0] == 5000 else None
            if method == "getSignaturesForAddress":
                assert params[0] == pool  # canonical derivation, nothing else
                return [{"signature": "later", "blockTime": T0 + 9, "err": None},
                        {"signature": "creationsig", "blockTime": T0 + 3, "err": None}]
            if method == "getTransaction":
                return creation
            raise AssertionError(method)

        return rpc, calls

    def test_completes_in_block(self):
        got = tmp.completes_in_block({"blockTime": T0, "transactions": [self.ctx, TX_NEITHER]})
        self.assertEqual(got, [("completesig", self.mint, T0)])

    def test_counts(self):
        r = tmp.run_graduations(self.rpc, 50, tmp.FLOOR_TS, 3)
        self.assertEqual(tuple(r), tmp.GRAD_KEYS)
        self.assertEqual((r["n_complete_events"], r["n_creation_tx"], r["n_no_creation_tx"]), (1, 1, 0))
        self.assertEqual((r["n_top_pump"], r["n_top_pumpswap"], r["n_top_other"]), (1, 0, 1))
        self.assertEqual(r["pump_ix_class_hist"], {"migrate": 1, "unknown": 1})
        self.assertEqual((r["n_disc_in_logs"], r["n_disc_inner_only"], r["n_disc_neither"]), (1, 0, 0))
        self.assertEqual(r["other_program_data_disc_hist"], {"aabbccddeeff0011": 1})
        self.assertEqual(r["n_signer_is_migration_account"], 1)
        self.assertEqual(r["n_same_tx_as_complete"], 0)
        self.assertEqual((r["secs_complete_to_creation_p50"], r["secs_complete_to_creation_max"]), (3, 3))
        self.assertEqual(r["legacy_migration_row_yield"], 1)
        self.assertEqual(r["decode_migration_event_reason_hist"], {"ok": 1})

    def test_reason_classes_and_same_tx(self):
        short = bytearray(mig_blob())[:150]
        badts = bytearray(mig_blob())
        badts[128:136] = (5).to_bytes(8, "little", signed=True)
        creation = creation_tx([pdata(bytes(short)), pdata(bytes(badts))], [(1, OTHER_IX)],
                               signer=b58encode(pk(7)), sig="completesig")
        rpc, _ = self._mock(creation)
        r = tmp.run_graduations(rpc, 50, tmp.FLOOR_TS, 3)
        self.assertEqual(r["decode_migration_event_reason_hist"], {"len_lt_200": 1, "ts_out_of_window": 1})
        self.assertEqual((r["n_signer_is_migration_account"], r["n_same_tx_as_complete"]), (0, 1))
        self.assertEqual(r["legacy_migration_row_yield"], 0)

    def test_no_creation_tx(self):
        def rpc(method, params):
            if method == "getSlot":
                return 5000
            if method == "getBlock":
                return {"blockTime": T0, "transactions": [self.ctx]}
            return []
        r = tmp.run_graduations(rpc, 50, tmp.FLOOR_TS, 1)
        self.assertEqual((r["n_complete_events"], r["n_no_creation_tx"], r["n_creation_tx"]), (1, 1, 0))

    def test_refusals_and_output(self):
        with self.assertRaises(tmp.Refusal):
            tmp.run_graduations(self.rpc, 51, tmp.FLOOR_TS, 3)
        with self.assertRaises(tmp.Refusal):
            tmp.run_graduations(self.rpc, 50, tmp.FLOOR_TS - 1, 3)
        with self.assertRaises(tmp.Refusal):
            tmp.run_graduations(self.rpc, 50, tmp.FLOOR_TS, tmp.MAX_BLOCKS_CAP + 1)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = tmp.main(["--mode", "graduations", "--max-blocks", "3"], rpc=self.rpc)
        self.assertEqual(rc, 0)
        text = out.getvalue()
        data = json.loads(text)
        self.assertEqual(tuple(data), tmp.GRAD_KEYS)
        for f in ["completesig", "creationsig", self.mint, tmp.canonical_pool(self.mint), tmp.MIGRATION_ACCOUNT,
                  b58encode(pk(4)), "123456789", "mint", "pool", "signature", "price", "reserve", "amount"]:
            self.assertNotIn(f, text)
        rc = tmp.main(["--mode", "graduations", "--max-blocks", "99999"], rpc=self.rpc)
        self.assertEqual(rc, 2)

    def test_neither_hist_in_migrations_mode(self):
        t = creation_tx(["Program log: x"], [(1, OTHER_IX), (1, OTHER_IX), (1, MIGRATE_IX)])
        c = tmp.count_txs([t, TX_LOG])
        self.assertEqual(c["neither_pump_ix_disc_hist"], {"top:1122334455667788": 2, "top:9beae792ec9ea21e": 1})


if __name__ == "__main__":
    unittest.main()
