"""Offline tests for tools/r4bt_c0_sell_simulate.py. The RPC is the probe tests' stub pool (the Stub of test_hv4_c0_sell_simulate); the holder search and balance read are
stubbed. No network, no key; the owner is an unsigned public key."""
from __future__ import annotations

import json
import unittest
from unittest import mock

from solders.keypair import Keypair

from tools import arena_sell_simulate as ss
from tools import r4bt_c0_sell_simulate as rs
from tools import pumpswap_tx as tx
from tools.test_hv4_c0_sell_simulate import BALANCE, Stub, sell_amount_and_payer
from tools.test_probe_executor import MINT

OWNER = str(Keypair().pubkey())


def ok_sim(b64):
    amt, payer, signed = sell_amount_and_payer(b64)
    assert payer == OWNER and not signed and amt == BALANCE  # fee payer is the holder, no signature, the whole balance
    return {"err": None, "logs": ["Program pAMM success"], "unitsConsumed": 123_456}


def patched():
    return mock.patch.multiple(ss, find_holder=lambda rpc, mint, pool, tp: {"owner": OWNER, "token_account": "TA"}, read_balance=lambda rpc, ta: (BALANCE, 7))


class SellSimTests(unittest.TestCase):
    def go(self, prog, sim_for):
        rpc = Stub(prog, sim_for=sim_for)
        with patched():
            return rs.run(rpc, MINT), rpc

    def test_success_reports_compute_units_for_both_token_programs(self):
        for prog in (str(tx.TOKEN_PROGRAM), str(tx.TOKEN_2022_PROGRAM)):
            with self.subTest(prog=prog):
                rec, rpc = self.go(prog, ok_sim)
                self.assertTrue(rec["built"] and rec["success"])
                self.assertEqual((rec["compute_units"], rec["err_class"], rec["sent"]), (123_456, None, 0))
                self.assertNotIn("sendTransaction", rpc.calls)  # captured locally, never forwarded
                self.assertEqual(rpc.calls.count("simulateTransaction"), 1)
                cfg = rpc.sim_params[0][1]
                self.assertIs(cfg["sigVerify"], False)
                self.assertIs(cfg["replaceRecentBlockhash"], True)

    def test_failure_is_reported_with_its_class(self):
        rec, _ = self.go(str(tx.TOKEN_2022_PROGRAM), lambda b64: {"err": {"InstructionError": [3, {"Custom": 3012}]}, "logs": [], "unitsConsumed": 9_000})
        self.assertFalse(rec["success"])
        self.assertEqual(rec["compute_units"], 9_000)
        self.assertEqual(rec["err_class"], "expected_no_tokens")

    def test_record_has_no_price_or_pnl_and_no_key(self):
        rec, _ = self.go(str(tx.TOKEN_2022_PROGRAM), ok_sim)
        text = json.dumps(rec)
        for word in ("pnl", "proceeds", "price", "secret", "private"):
            self.assertNotIn(word, text.lower())
        self.assertRegex(rec["probe_live_sha256"], r"^[0-9a-f]{64}$")

    def test_non_canonical_pool_is_refused_before_any_rpc(self):
        with self.assertRaises(rs.hs.Refused):
            rs.hs.check_pool(MINT, str(Keypair().pubkey()))


class CliTests(unittest.TestCase):
    def test_flag_is_required_and_keyed_url_refused(self):
        with self.assertRaises(SystemExit):
            rs.main(["--mint", MINT])
        self.assertEqual(rs.main(["--mint", MINT, "--public-non-r4bt-mint", "--rpc", "https://x.example/?api-key=1"]), 2)

    def test_main_exit_codes_and_line(self):
        for sim, rc, success in ((ok_sim, 0, True), (lambda b64: {"err": "AccountNotFound", "logs": [], "unitsConsumed": 1}, 1, False)):
            rpc = Stub(str(tx.TOKEN_2022_PROGRAM), sim_for=sim)
            printed = []
            with patched(), mock.patch.object(rs.ss, "RateLimitedRpc", lambda url: rpc), mock.patch("builtins.print", lambda s, **k: printed.append(s)):
                self.assertEqual(rs.main(["--mint", MINT, "--public-non-r4bt-mint"]), rc)
            line = json.loads(printed[-1])
            self.assertEqual(line["success"], success)
            self.assertEqual(set(line), {"mint", "token_program", "success", "err_class", "compute_units", "sent"})


if __name__ == "__main__":
    unittest.main()
