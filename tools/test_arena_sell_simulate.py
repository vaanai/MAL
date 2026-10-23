"""Offline tests for tools/arena_sell_simulate.py. The RPC is mocked (the probe's recorded pool); no network, no real key."""
from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from solders.signature import Signature
from solders.transaction import VersionedTransaction

from tools import arena_sell_simulate as ss
from tools import h5_arena_executor as ax
from tools import pumpswap_tx as tx
from tools.test_probe_executor import MINT, FakeRpc

NOW = lambda: datetime(2026, 10, 23, 16, 0, 0, tzinfo=timezone.utc)  # noqa: E731
NO_TOKENS = {"InstructionError": [3, {"Custom": 3012}]}


class Rpc(FakeRpc):
    """FakeRpc + the user key getMultipleAccounts asks for + scripted simulate results (first call: throwaway, second: funded payer)."""

    def __init__(self, sims, mint_owner=None):
        super().__init__()
        if mint_owner:
            self.mint_owner = mint_owner
            if mint_owner == str(tx.TOKEN_PROGRAM):  # the recorded pool is a Token-2022 one: re-derive its base vault under the classic program
                raw = bytearray(base64.b64decode(self.pool_b64))
                pool = tx.parse_pool_account(bytes(raw))
                at = bytes(raw).index(bytes(pool["base_vault"]))
                raw[at:at + 32] = bytes(tx.ata(pool["pool"] if "pool" in pool else tx.canonical_pool(pool["base_mint"]), pool["base_mint"], tx.TOKEN_PROGRAM))
                self.pool_b64 = base64.b64encode(bytes(raw)).decode()
        self.sims, self.sim_params = list(sims), []

    def __call__(self, method, params):
        if method == "getMultipleAccounts":
            got = super().__call__(method, [params[0][:-1], *params[1:]])
            return {**got, "value": [*got["value"], None]}
        if method == "simulateTransaction":
            self.calls.append(method)
            self.sim_params.append(params)
            return {"context": {"slot": 5}, "value": self.sims.pop(0)}
        return super().__call__(method, params)


def val(err, logs=()):
    return {"err": err, "logs": list(logs), "unitsConsumed": 1}


class BuildAndRun(unittest.TestCase):
    def run_one(self, owner_prog, sims, **kw):
        rpc = Rpc(sims, mint_owner=owner_prog)
        return ss.run(rpc, MINT, None, 1000, ax.PRIORITY_P155, now=NOW, **kw), rpc

    def test_both_token_programs_close_under_mints_own_program(self):
        for prog, name in ((str(tx.TOKEN_PROGRAM), "token"), (str(tx.TOKEN_2022_PROGRAM), "token-2022")):
            rec, _ = self.run_one(prog, [val("AccountNotFound"), val(NO_TOKENS)])
            self.assertEqual(rec["token_program"], name)
            sh = rec["shape"]
            self.assertEqual([i["role"] for i in sh["instructions"]], list(ss.IX_ROLES))
            self.assertTrue(sh["fee_payer_is_owner"])
            self.assertTrue(sh["close_base_program_is_mint_program"])
            self.assertEqual(sh["instructions"][-1]["program"], prog)
            self.assertEqual(sh["instructions"][3]["program"], str(tx.PUMPSWAP_PROGRAM))
            self.assertIsNone(rec["builder_pin_problem"])

    def test_simulate_flags_and_classes(self):
        rec, rpc = self.run_one(str(tx.TOKEN_PROGRAM), [val("AccountNotFound"), val(NO_TOKENS)])
        for p in rpc.sim_params:
            self.assertEqual(p[1], {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": "processed"})
        self.assertEqual(rec["verdict"]["class"], "expected_unfunded_owner")
        self.assertFalse(rec["verdict"]["structural_checked_by_chain"])
        self.assertEqual(rec["funded_payer_probe"]["verdict"]["class"], "expected_no_tokens")
        self.assertNotIn("on-chain account checks of the swap (stopped before it)", rec["not_measured"])  # the probe reached the swap

    def test_priority_p155_and_emergency_min_out(self):
        rec, _ = self.run_one(str(tx.TOKEN_PROGRAM), [val("AccountNotFound"), val(NO_TOKENS)])
        self.assertEqual(rec["priority"], ax.PRIORITY_P155.as_dict())
        self.assertEqual(rec["min_out"], 1)

    def test_funded_probe_unsigned_throwaway_signed(self):
        rpc = Rpc([val("AccountNotFound"), val(NO_TOKENS)])
        ss.run(rpc, MINT, None, 1000, ax.PRIORITY_DEFAULT, now=NOW)
        sigs = [VersionedTransaction.from_bytes(base64.b64decode(p[0])).signatures[0] for p in rpc.sim_params]
        self.assertNotEqual(sigs[0], Signature.default())
        self.assertEqual(sigs[1], Signature.default())

    def test_no_funded_probe(self):
        rec, rpc = self.run_one(str(tx.TOKEN_PROGRAM), [val("AccountNotFound")], funded_probe=False)
        self.assertIsNone(rec["funded_payer_probe"])
        self.assertIn("on-chain account checks of the swap (stopped before it)", rec["not_measured"])
        self.assertEqual(rpc.calls.count("simulateTransaction"), 1)

    def test_record_has_no_key_material(self):
        rec, _ = self.run_one(str(tx.TOKEN_PROGRAM), [val("AccountNotFound"), val(NO_TOKENS)])
        text = json.dumps(rec).lower()
        for bad in ("secret", "private", "keypair", "seed"):
            self.assertNotIn(bad, text)

    def test_pool_mint_mismatch_refused(self):
        with self.assertRaises(SystemExit):
            ss.build_sell(Rpc([]), "11111111111111111111111111111112", None, ss.Keypair(), 5, ax.PRIORITY_P155)


class Classify(unittest.TestCase):
    def test_table(self):
        c = ss.classify
        self.assertEqual(c(val("AccountNotFound"))["class"], "expected_unfunded_owner")
        self.assertEqual(c(val(None))["class"], "ok_unexpected")
        self.assertEqual(c(val(NO_TOKENS))["class"], "expected_no_tokens")
        self.assertEqual(c(val(NO_TOKENS))["ix_role"], "pumpswap_sell")
        self.assertEqual(c(val({"InstructionError": [3, {"Custom": 2006}]}))["class"], "structural")  # ConstraintSeeds
        self.assertEqual(c(val({"InstructionError": [3, {"Custom": 2012}]}))["class"], "structural")  # ConstraintAddress
        self.assertEqual(c(val({"InstructionError": [3, "IncorrectProgramId"]}))["class"], "structural")
        self.assertEqual(c(val({"InstructionError": [5, "InvalidAccountData"]}))["class"], "structural")
        self.assertEqual(c(val("ProgramAccountNotFound"))["class"], "structural")
        self.assertEqual(c(val({"InstructionError": [3, {"Custom": 6004}]}))["class"], "unclassified")
        self.assertEqual(c(val("BlockhashNotFound"))["class"], "unclassified")
        self.assertEqual(c(val("InsufficientFundsForRent"))["class"], "expected_unfunded_owner")

    def test_structural_not_masked_by_no_tokens_log(self):
        v = val({"InstructionError": [3, {"Custom": 2006}]}, ["insufficient funds"])
        self.assertEqual(ss.classify(v)["class"], "structural")


class RateAndRecord(unittest.TestCase):
    def test_one_request_per_second(self):
        t = [0.0]
        slept = []

        def sleep(s):
            slept.append(s)
            t[0] += s
        rpc = ss.RateLimitedRpc("https://x.invalid", post=lambda u, p: {"result": 1}, sleep=sleep, clock=lambda: t[0])
        for _ in range(4):
            rpc("getSlot", [])
        self.assertEqual(len(slept), 3)  # the first call is free
        self.assertTrue(all(abs(s - 1.0) < 1e-9 for s in slept))

    def test_failure_does_not_leak_url(self):
        def boom(u, p):
            raise OSError("boom https://leak.invalid/KEY")
        rpc = ss.RateLimitedRpc("https://leak.invalid/KEY", post=boom, sleep=lambda s: None, clock=lambda: 0.0)
        with self.assertRaises(SystemExit) as cm:
            rpc("getSlot", [])
        self.assertNotIn("KEY", str(cm.exception))

    def test_sha_is_of_the_written_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "r.json"
            sha = ss.write_record({"b": 1, "a": [2]}, out)
            self.assertEqual(sha, hashlib.sha256(out.read_bytes()).hexdigest())
            self.assertEqual(out.with_name("r.json.sha256").read_text().split()[0], sha)

    def test_main_refuses_rpc_url_with_key(self):
        with self.assertRaises(SystemExit):
            ss.main(["--mint", MINT, "--rpc", "https://x.invalid/?api-key=abc", "--out", "/nonexistent/o.json"])

    def test_main_rejects_abbrev(self):
        with self.assertRaises(SystemExit):
            ss.main(["--mi", MINT, "--out", "/nonexistent/o.json"])


if __name__ == "__main__":
    unittest.main()
