"""Offline tests for tools/pumpswap_simulate.py (no RPC)."""

from __future__ import annotations

import unittest

from tools import pumpswap_simulate as s


class SimulateHelperTests(unittest.TestCase):
    def test_token_amount_reads_offset_64(self):
        data = bytes(64) + (123_456).to_bytes(8, "little") + bytes(93)
        self.assertEqual(s.token_amount(data), 123_456)

    def test_summarize_logs_prefers_errors(self):
        logs = ["Program x invoke [1]", "Program log: AnchorError ... Error Code: X", "Program x failed: custom program error: 0x1"]
        self.assertEqual(s.summarize_logs(logs), logs[1:])
        self.assertEqual(s.summarize_logs(None), [])

    def test_defaults_are_the_trial_terms(self):
        self.assertEqual(s.DEFAULT_SOL, 0.5)
        self.assertEqual(s.tx.DEFAULT_PRIORITY_TOTAL_LAMPORTS, 500_000)

    def test_default_user_is_a_valid_pubkey(self):
        self.assertEqual(len(bytes(s.Pubkey.from_string(s.DEFAULT_USER))), 32)

    def test_explicit_rpc_wins_and_is_not_logged(self):
        self.assertEqual(s.load_rpc_url("http://localhost:8899", "/nonexistent"), "http://localhost:8899")



class LocalRpcHelpersMatchBackfill(unittest.TestCase):
    """pumpswap_simulate carries local copies so the pinned executor need not import observe.*."""

    def test_same_behaviour(self):
        from tools import pump_history_backfill as b

        self.assertEqual(s.HELIUS_HTTP, b.HELIUS_HTTP)
        self.assertEqual(s.helius_http_url(" k1 "), b.helius_http_url(" k1 "))
        for bad in ("", "a b", "a&b", "a\nb"):
            with self.assertRaises(ValueError):
                s.helius_http_url(bad)
        t = "failed https://x/?api-key=SECRET&y=1 'api-key=ZZ'"
        self.assertEqual(s.redact_rpc_url(t), b.redact_rpc_url(t))
        self.assertNotIn("SECRET", s.redact_rpc_url(t))


if __name__ == "__main__":
    unittest.main()
