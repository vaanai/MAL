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
        self.assertTrue(all(isinstance(v, int) for v in data.values()))
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


if __name__ == "__main__":
    unittest.main()
