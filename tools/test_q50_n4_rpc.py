"""Offline tests: RPC hardening of the Q50 N4 rerun (retry/backoff, status+signature-only errors, getTransaction version, --rpc-env key hygiene)."""
import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tools import arena_sell_simulate as ss
from tools import q50_n4_rerun as n4

KEY = "SECRETKEY-0123456789abcdef"
SIG = "5iUVjzmzSigSigSigSig46KU"


def http_err(code, url="https://mainnet.helius-rpc.com/?api-key=" + KEY):
    return urllib.error.HTTPError(url, code, "x", {}, None)


def mk(post, **kw):
    sleeps = []
    r = ss.RateLimitedRpc("https://mainnet.helius-rpc.com/?api-key=" + KEY, post=post, sleep=sleeps.append, clock=lambda: 0.0, **kw)
    return r, sleeps


class RetryTest(unittest.TestCase):
    def test_429_then_ok(self):
        seq = [http_err(429), http_err(503), {"result": {"ok": 1}}]

        def post(u, p):
            x = seq.pop(0)
            if isinstance(x, Exception):
                raise x
            return x
        r, sleeps = mk(post)
        self.assertEqual(r("getTransaction", [SIG, {"maxSupportedTransactionVersion": 1}]), {"ok": 1})
        self.assertEqual(len(r.calls), 3)
        self.assertIn(2.0, sleeps)
        self.assertIn(4.0, sleeps)  # exponential backoff 2, 4

    def test_bounded_retries_and_label(self):
        n = [0]

        def post(u, p):
            n[0] += 1
            raise http_err(429)
        r, _ = mk(post, retries=3)
        with self.assertRaises(SystemExit) as cm:
            r("getTransaction", [SIG, {}])
        self.assertEqual(n[0], 4)  # 1 + 3 retries, then stop
        msg = str(cm.exception)
        self.assertIn("HTTP 429", msg)
        self.assertIn(f"sig={SIG}", msg)
        self.assertNotIn(KEY, msg)
        self.assertNotIn("http", msg.replace("HTTP", ""))

    def test_non_retryable_fails_at_once(self):
        n = [0]

        def post(u, p):
            n[0] += 1
            raise http_err(400)
        r, _ = mk(post)
        with self.assertRaises(SystemExit) as cm:
            r("getSlot", [])
        self.assertEqual(n[0], 1)
        self.assertIn("HTTP 400", str(cm.exception))
        self.assertNotIn("sig=", str(cm.exception))

    def test_network_error_retried_without_leak(self):
        n = [0]

        def post(u, p):
            n[0] += 1
            raise urllib.error.URLError("fail https://x/?api-key=" + KEY)
        r, _ = mk(post, retries=2)
        with self.assertRaises(SystemExit) as cm:
            r("getSlot", [])
        self.assertEqual(n[0], 3)
        self.assertNotIn(KEY, str(cm.exception))
        self.assertIn("URLError", str(cm.exception))
        self.assertNotIn(KEY, repr(r))

    def test_get_transaction_version_always_set(self):
        seen = []

        def post(u, p):
            seen.append(p["params"])
            return {"result": None}
        r, _ = mk(post)
        r("getTransaction", [SIG])
        r("getTransaction", [SIG, {"encoding": "json"}])
        r("getTransaction", [SIG, {"maxSupportedTransactionVersion": 1}])
        r("getTransaction", [SIG, {"maxSupportedTransactionVersion": -1}])
        for params in seen:
            self.assertGreaterEqual(params[1]["maxSupportedTransactionVersion"], 0)
        self.assertEqual(seen[1][1]["encoding"], "json")
        self.assertEqual(seen[2][1]["maxSupportedTransactionVersion"], 1)

    def test_candidate_fallback_getTransaction_has_version(self):
        calls = []

        def rpc(m, p):
            calls.append((m, p))
            if m == "getTokenLargestAccounts":
                raise SystemExit("x")
            if m == "getSignaturesForAddress":
                return [{"signature": "s1"}]
            return None
        mint = "So11111111111111111111111111111111111111112"
        ss.candidate_token_accounts(rpc, mint, ss.Pubkey.from_string(mint), ss.Pubkey.from_string(mint))
        tx = [p for m, p in calls if m == "getTransaction"]
        self.assertTrue(tx)
        self.assertGreaterEqual(tx[0][1]["maxSupportedTransactionVersion"], 0)


class RpcEnvTest(unittest.TestCase):
    def _env(self, d, text):
        p = Path(d) / "helius.env"
        p.write_text(text)
        return str(p)

    def test_key_never_in_output(self):
        urls = []

        def factory(url):
            urls.append(url)
            return lambda m, p: [{"signature": "a", "blockTime": 1}]
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d, f"HELIUS_API_KEY={KEY}\n")
            argv = ["--token-mint", "M", "--token-pool", "P", "--token-s0-utc", "2026-10-23T16:12:43Z", "--token-s0-source", "x",
                    "--token2022-mint", "M2", "--token2022-pool", "P2", "--token2022-s0-utc", "2026-10-23T16:12:43Z", "--token2022-s0-source", "x",
                    "--rpc-env", env, "--out-dir", d]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = n4.main(argv, rpc_factory=factory)
            self.assertEqual(rc, 1)  # october s0 refused before any RPC: FAIL, not an error
            self.assertEqual(urls, ["https://mainnet.helius-rpc.com/?api-key=" + KEY])  # built in-process
            texts = [out.getvalue(), err.getvalue()] + [f.read_text() for f in Path(d).iterdir() if f.name != "helius.env"]
            self.assertTrue(any("helius" in t for t in texts))
            for t in texts:
                self.assertNotIn(KEY, t)
            summ = json.loads((Path(d) / "q50-n4-summary.json").read_text())
            self.assertEqual(summ["rpc_host"], "helius")

    def test_key_not_in_argv_error_path(self):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            argv = ["--token-mint", "M", "--token-pool", "P", "--token-s0-utc", "2026-09-12T03:00:00Z", "--token-s0-source", "x",
                    "--token2022-mint", "M2", "--token2022-pool", "P2", "--token2022-s0-utc", "2026-09-12T03:00:00Z", "--token2022-s0-source", "x",
                    "--rpc-env", str(Path(d) / "missing.env"), "--out-dir", d]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = n4.main(argv)
            self.assertEqual(rc, 2)
            self.assertIn("rpc-env unusable: FileNotFoundError", err.getvalue())
            self.assertNotIn("missing.env", out.getvalue() + err.getvalue())

    def test_http_failure_through_main_has_no_key(self):
        def post(u, p):
            raise http_err(429, u)
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            env = str(Path(d) / "h.env")
            Path(env).write_text(f"HELIUS_API_KEY={KEY}\n")
            argv = ["--token-mint", "M", "--token-pool", "P", "--token-s0-utc", "2026-09-12T03:00:00Z", "--token-s0-source", "x",
                    "--token2022-mint", "M2", "--token2022-pool", "P2", "--token2022-s0-utc", "2026-09-12T03:00:00Z", "--token2022-s0-source", "x",
                    "--rpc-env", env, "--out-dir", d]
            fac = lambda url: ss.RateLimitedRpc(url, post=post, sleep=lambda s: None, clock=lambda: 0.0, retries=1)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as cm:
                    n4.main(argv, rpc_factory=fac)
            for t in (out.getvalue(), err.getvalue(), str(cm.exception)):
                self.assertNotIn(KEY, t)
            self.assertIn("HTTP 429", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
