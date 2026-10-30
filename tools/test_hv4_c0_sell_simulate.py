"""Offline tests for tools/hv4_c0_sell_simulate.py and scripts/ops/hv4-c0-sell-simulate-job.sh. The RPC is the probe tests' stub pool (FakeRpc); no network, no real key,
no HV4 ledger, decision row or job log. The throwaway Keypair is in memory; the tests check it is never written into a record."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from tools import arena_sell_simulate as ss
from tools import hv4_c0_sell_simulate as hs
from tools import hv4_live_c0 as c0
from tools import pumpswap_tx as tx
from tools.test_probe_executor import MINT, FakeRpc

ROOT = Path(__file__).resolve().parent.parent
JOB = ROOT / "scripts" / "ops" / "hv4-c0-sell-simulate-job.sh"
FIXTURE = ROOT / "tools" / "fixtures" / "pumpswap" / "t22_mayhem_accounts.json"
NOW = lambda: datetime(2026, 10, 30, 16, 0, 0, tzinfo=timezone.utc)  # noqa: E731
NO_TOKENS = {"InstructionError": [3, {"Custom": 3012}]}
BALANCE = 5_000_000_000


def sell_amount_and_payer(b64: str) -> tuple[int, str, bool]:
    t = VersionedTransaction.from_bytes(base64.b64decode(b64))
    keys = list(t.message.account_keys)
    for ix in t.message.instructions:
        if keys[ix.program_id_index] == tx.PUMPSWAP_PROGRAM:
            return int.from_bytes(bytes(ix.data)[8:16], "little"), str(keys[0]), str(t.signatures[0]) != "1" * 64
    raise AssertionError("no sell")


class Stub(FakeRpc):
    """FakeRpc (the recorded Token-2022 pool; re-derived for the classic program) + scripted simulate + balances. `sim_for(b64)` returns the simulate value."""

    def __init__(self, token_program: str, sim_for=None):
        super().__init__()
        self.mint_owner = token_program
        if token_program == str(tx.TOKEN_PROGRAM):
            raw = bytearray(base64.b64decode(self.pool_b64))
            pool = tx.parse_pool_account(bytes(raw))
            at = bytes(raw).index(bytes(pool["base_vault"]))
            raw[at:at + 32] = bytes(tx.ata(tx.canonical_pool(pool["base_mint"]), pool["base_mint"], tx.TOKEN_PROGRAM))
            self.pool_b64 = base64.b64encode(bytes(raw)).decode()
        self.sim_for, self.sim_params, self.token_balance = sim_for or self.default_sim, [], BALANCE

    @staticmethod
    def default_sim(b64: str) -> dict:
        _amt, _payer, signed = sell_amount_and_payer(b64)
        return {"err": "AccountNotFound" if signed else NO_TOKENS, "logs": [], "unitsConsumed": 1}  # throwaway is signed; the funded payer is not

    def __call__(self, method, params):
        if method == "simulateTransaction":
            self.calls.append(method)
            self.sim_params.append(params)
            return {"context": {"slot": 5}, "value": self.sim_for(params[0])}
        if method == "getTokenAccountBalance":
            self.calls.append(method)
            return {"context": {"slot": 6}, "value": {"amount": str(self.token_balance)}}
        return super().__call__(method, params)


T_PROGRAMS = ((str(tx.TOKEN_PROGRAM), "token"), (str(tx.TOKEN_2022_PROGRAM), "token-2022"))


class DefaultModeTests(unittest.TestCase):
    def run_one(self, prog, **kw):
        rpc = Stub(prog, **kw)
        return hs.run(rpc, MINT, None, 0, now=NOW, git_sha="a" * 40, rpc_host="stub"), rpc

    def test_both_rungs_both_token_programs_through_the_bridge_builder(self):
        for prog, name in T_PROGRAMS:
            with self.subTest(token_program=name):
                rec, rpc = self.run_one(prog)
                self.assertTrue(rec["verdict"]["pass"], rec["verdict"]["problems"])
                self.assertIsNone(rec["builder"]["pin_problem"])
                self.assertEqual([v["variant"] for v in rec["variants"]], ["throwaway", "funded_public_payer_unsigned"])
                for v in rec["variants"]:
                    self.assertEqual(v["token_program"], name)
                    self.assertEqual(list(v["rungs"]), ["first", "escalated"])
                    first, esc = v["rungs"]["first"], v["rungs"]["escalated"]
                    self.assertEqual((first["sell_attempts"], esc["sell_attempts"]), (0, 2))
                    self.assertEqual((first["balance_source"], esc["balance_source"]), ("buy_meta", "rpc"))
                    for r, prio, keep in ((first, 55_000, 8500), (esc, 150_000, 6500)):
                        sh = r["shape"]
                        self.assertTrue(r["built"] and r["rung_ok"], r["checks"])
                        self.assertEqual(sh["priority_lamports"], prio)
                        self.assertGreater(sh["sell_min_out"], 0)
                        self.assertEqual(sh["sell_min_out"], r["q_out"] * keep // 10_000)
                        self.assertEqual(sh["roles"], list(ss.IX_ROLES))
                        self.assertTrue(sh["base_ata_closed_in_tx"] and sh["base_close_under_mint_token_program"] and sh["base_close_after_swap"])
                        self.assertEqual(sh["instructions"][-1]["program"], prog)  # closed under the mint's own program
                        self.assertEqual(sh["program_ids"][0], hs.COMPUTE_BUDGET)
                        self.assertIn(str(tx.PUMPSWAP_PROGRAM), sh["program_ids"])
                        self.assertTrue(sh["wsol_closed_in_tx"])
                self.assertEqual(rec["variants"][0]["rungs"]["first"]["verdict"]["class"], "expected_unfunded_owner")
                self.assertEqual(rec["variants"][1]["rungs"]["escalated"]["verdict"]["class"], "expected_no_tokens")
                self.assertEqual(rec["variants"][0]["signed"], True)
                self.assertEqual(rec["variants"][1]["signed"], False)

    def test_nothing_is_sent_and_only_allowlisted_reads_are_forwarded(self):
        rec, rpc = self.run_one(str(tx.TOKEN_2022_PROGRAM))
        self.assertNotIn("sendTransaction", rpc.calls)
        self.assertEqual(rec["sent"], 0)
        self.assertEqual(rec["sends_captured_not_forwarded"], 4)  # 2 variants x 2 rungs, captured and dropped
        self.assertEqual(rec["rpc_answered_locally"]["sendTransaction"], 4)
        self.assertEqual(rec["rpc_refused"], [])
        self.assertTrue(set(rec["rpc_calls_forwarded"]) <= hs.READ_METHODS)
        for p in rpc.sim_params:
            self.assertEqual(p[1], {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": "processed"})
        self.assertIn("getTokenAccountBalance", rec["rpc_answered_locally"])  # the escalated rung's balance read is the stub balance
        self.assertNotIn("getTokenAccountBalance", rpc.calls)

    def test_throwaway_key_is_never_in_the_record_or_the_output(self):
        made = []

        class Spy(Keypair):
            def __new__(cls, *a, **k):
                kp = super().__new__(cls, *a, **k)
                made.append(kp)
                return kp

        out = io.StringIO()
        with mock.patch.object(hs, "Keypair", Spy), contextlib.redirect_stdout(out):
            rec, _ = self.run_one(str(tx.TOKEN_PROGRAM))
        self.assertEqual(len(made), 1)
        text = json.dumps(rec, sort_keys=True) + out.getvalue()
        self.assertNotIn(str(made[0]), text)
        self.assertNotIn(bytes(made[0]).hex(), text)
        self.assertIn(str(made[0].pubkey()), text)  # the public key only

    def test_unsigned_variant_carries_the_zero_signature(self):
        rec, rpc = self.run_one(str(tx.TOKEN_PROGRAM))
        self.assertFalse(rec["variants"][1]["rungs"]["first"]["shape"]["first_signature_is_zero"] is False)
        self.assertFalse(rec["variants"][0]["rungs"]["first"]["shape"]["first_signature_is_zero"])

    def test_default_amount_is_the_c0_stake_entry_quote_and_override_wins(self):
        rec, _ = self.run_one(str(tx.TOKEN_PROGRAM))
        a = rec["variants"][0]["sell_amount_raw"]
        self.assertGreater(a, 1_000_000)
        rec2 = hs.run(Stub(str(tx.TOKEN_PROGRAM)), MINT, None, 123_456, now=NOW)
        self.assertEqual(rec2["variants"][0]["sell_amount_raw"], 123_456)
        self.assertEqual(rec2["variants"][0]["rungs"]["first"]["shape"]["sell_amount_in"], 123_456)

    def test_classification_gates_the_verdict(self):
        cases = ((None, "ok_unexpected"), ({"InstructionError": [3, {"Custom": 3007}]}, "structural"), ({"InstructionError": [3, {"Custom": 6004}]}, "unclassified"))
        for err, cls in cases:
            with self.subTest(cls=cls):
                rec, _ = self.run_one(str(tx.TOKEN_PROGRAM), sim_for=lambda b64, e=err: {"err": e, "logs": [], "unitsConsumed": 1})
                self.assertFalse(rec["verdict"]["pass"])
                self.assertTrue(any(f":class:{cls}" in p for p in rec["verdict"]["problems"]), rec["verdict"]["problems"])
        self.assertEqual(ss.classify({"err": "AccountNotFound", "logs": []})["class"], "expected_unfunded_owner")
        self.assertEqual(ss.classify({"err": NO_TOKENS, "logs": []})["class"], "expected_no_tokens")

    def test_min_out_zero_is_refused_by_the_bridge_sign_and_fails_the_run(self):
        from tools import probe_executor as pe
        real = pe.exit_check
        # exit_check is inside the pin, so the pin check is stubbed out here: this test is about the bridge's _sign refusing a decoded min_out of 0
        with mock.patch.object(c0, "sell_pin_problem", return_value=None), mock.patch.object(pe, "exit_check", lambda *a, **k: {**real(*a, **k), "quote_out": 1}), \
                contextlib.redirect_stdout(io.StringIO()):
            rec, rpc = self.run_one(str(tx.TOKEN_PROGRAM))
        self.assertFalse(rec["verdict"]["pass"])
        r = rec["variants"][0]["rungs"]["first"]
        self.assertFalse(r["built"])
        self.assertTrue(any("c0_min_out_zero" in e for e in r["sign_errors"]), r["sign_errors"])
        self.assertEqual(rec["sends_captured_not_forwarded"], 0)  # nothing signed, nothing captured
        self.assertEqual(rpc.sim_params, [])

    def test_pin_mismatch_refuses_before_any_rpc(self):
        rpc = Stub(str(tx.TOKEN_PROGRAM))
        with mock.patch.dict(c0.SELL_BUILDER_PIN, {"ptx.sell_instructions_sha256": "0" * 64}):
            self.assertIn("sell_builder_pin_mismatch:ptx.sell_instructions_sha256", hs.pin_refusal())
            with self.assertRaises(hs.Refused) as cm:
                hs.run(rpc, MINT, None, 0, now=NOW)
            with self.assertRaises(hs.Refused):
                hs.holder_run(rpc, MINT, None, now=NOW)
        self.assertIn("sell_builder_pin", str(cm.exception.code))
        self.assertEqual(rpc.calls, [])
        with mock.patch.object(c0, "sell_pin_problem", return_value="sell_builder_pin_mismatch:x"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = hs.main(["--mint", MINT, "--out", "/nonexistent/never-written.json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue())["verdict"], "REFUSED")

    def test_non_canonical_pool_is_refused(self):
        rpc = Stub(str(tx.TOKEN_PROGRAM))
        with self.assertRaises(hs.Refused):
            hs.run(rpc, MINT, str(Keypair().pubkey()), 0, now=NOW)
        self.assertEqual(rpc.calls, [])

    def test_record_file_and_sha(self):
        rec, _ = self.run_one(str(tx.TOKEN_PROGRAM))
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "r.json"
            sha = hs.write_record(rec, out)
            self.assertEqual(sha, ss.record_sha256(rec))
            self.assertEqual(out.with_name("r.json.sha256").read_text(), f"{sha}  r.json\n")
            self.assertEqual(json.loads(out.read_text())["kind"], hs.KIND_DEFAULT)


class GateTests(unittest.TestCase):
    def test_send_is_never_forwarded_and_unknown_methods_are_refused(self):
        calls = []
        gate = hs.SimRpc(lambda m, p: calls.append(m) or {"value": None})
        from solders.hash import Hash
        from solders.message import Message
        from solders.null_signer import NullSigner
        payer = Keypair().pubkey()
        raw = bytes(VersionedTransaction(Message.new_with_blockhash([], payer, Hash.default()), [NullSigner(payer)]))
        self.assertEqual(gate("sendTransaction", [base64.b64encode(raw).decode(), {}]), "1" * 64)
        self.assertEqual(gate.captured_sends, [{"signature": "1" * 64, "tx_len_bytes": len(raw)}])
        self.assertEqual(calls, [])  # not forwarded
        with self.assertRaises(hs.SimRefused):
            gate("getBalance", ["x"])
        with self.assertRaises(hs.SimRefused):
            gate("requestAirdrop", ["x", 1])
        self.assertEqual(gate.refused, ["getBalance", "requestAirdrop"])
        self.assertEqual(calls, [])
        v = gate("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]
        self.assertEqual(v["blockhash"], "1" * 32)
        self.assertEqual(calls, [])
        gate.balances["acct"] = 7
        self.assertEqual(gate("getTokenAccountBalance", ["acct", {}])["value"]["amount"], "7")
        gate("getAccountInfo", ["other", {}])
        self.assertEqual(calls, ["getAccountInfo"])
        self.assertEqual(repr(gate), "SimRpc(<redacted>)")


class HolderModeTests(unittest.TestCase):
    OWNER = Keypair().pubkey()

    def holder_sim(self, prog):
        base_tp = prog

        def sim_for(b64):
            amount, payer, signed = sell_amount_and_payer(b64)
            assert payer == str(self.OWNER) and not signed  # unsigned, fee payer is the holder
            if amount == BALANCE:
                logs = [f"Program {tx.PUMPSWAP_PROGRAM} invoke [1]", f"Program {tx.PUMPSWAP_PROGRAM} success", f"Program {tx.TOKEN_PROGRAM} invoke [1]",
                        f"Program {tx.TOKEN_PROGRAM} success", f"Program {base_tp} invoke [1]", f"Program {base_tp} success"]
                return {"err": None, "logs": logs, "unitsConsumed": 99, "accounts": [None, None]}
            return {"err": {"InstructionError": [5, {"Custom": 11}]}, "unitsConsumed": 99, "accounts": [None, None],
                    "logs": [f"Program {base_tp} invoke [1]", "Program log: Non-native account can only be closed if its balance is zero"]}

        return sim_for

    def run_holder(self, prog, **kw):
        rpc = Stub(prog, sim_for=self.holder_sim(prog))
        ata = str(tx.ata(self.OWNER, Pubkey.from_string(MINT), Pubkey.from_string(prog)))
        holder = {"owner": str(self.OWNER), "token_account": ata, "owner_lamports": 1, "candidate_source": "stub", "candidates_checked": 1, "skipped": {}}
        with mock.patch.object(ss, "find_holder", lambda *a, **k: holder):
            return hs.holder_run(rpc, MINT, None, now=NOW, git_sha="b" * 40, rpc_host="stub", **kw), rpc

    def test_full_sell_and_balance_minus_one_control_on_both_rungs_and_programs(self):
        for prog, name in T_PROGRAMS:
            with self.subTest(token_program=name):
                rec, rpc = self.run_holder(prog)
                self.assertTrue(rec["verdict"]["pass"], rec["verdict"]["problems"])
                self.assertFalse(rec["signed"])
                for rung, prio in (("first", 55_000), ("escalated", 150_000)):
                    r = rec["rungs"][rung]
                    self.assertTrue(r["verdict"]["full_sell_ok"] and r["verdict"]["control_reverts_at_base_close"], r["verdict"])
                    full, ctl = r["runs"]["full_balance"], r["runs"]["control_balance_minus_1"]
                    self.assertEqual((full["rec"]["shape"]["sell_amount_in"], ctl["rec"]["shape"]["sell_amount_in"]), (BALANCE, BALANCE - 1))
                    self.assertEqual(full["rec"]["shape"]["priority_lamports"], prio)
                    self.assertGreater(full["rec"]["shape"]["sell_min_out"], 0)
                    self.assertTrue(full["rec"]["shape"]["first_signature_is_zero"])
                    self.assertEqual(ctl["checks"]["base_ata_closed_in_post_state"], "not_reported_on_error")
                    self.assertEqual(full["balance_read_before_simulate"]["amount"], BALANCE)
                self.assertNotIn("sendTransaction", rpc.calls)
                self.assertEqual(rec["rpc_refused"], [])

    def test_a_control_that_does_not_revert_at_the_close_fails_the_run(self):
        prog = str(tx.TOKEN_PROGRAM)
        rpc = Stub(prog, sim_for=lambda b64: {"err": {"InstructionError": [3, {"Custom": 6004}]}, "logs": [], "unitsConsumed": 1, "accounts": [None, None]})
        ata = str(tx.ata(self.OWNER, Pubkey.from_string(MINT), Pubkey.from_string(prog)))
        holder = {"owner": str(self.OWNER), "token_account": ata, "owner_lamports": 1, "candidate_source": "stub", "candidates_checked": 1, "skipped": {}}
        with mock.patch.object(ss, "find_holder", lambda *a, **k: holder):
            rec = hs.holder_run(rpc, MINT, None, now=NOW)
        self.assertFalse(rec["verdict"]["pass"])


class CliTests(unittest.TestCase):
    def cli(self, argv, rpc, holder=None):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ss, "RateLimitedRpc", lambda url: rpc), contextlib.redirect_stdout(out):
            patches = [mock.patch.object(ss, "find_holder", lambda *a, **k: holder)] if holder else []
            with contextlib.ExitStack() as st:
                for p in patches:
                    st.enter_context(p)
                code = hs.main(["--mint", MINT, "--git-sha", "c" * 40, "--out", str(Path(d) / "r.json"), *argv])
            files = sorted(p.name for p in Path(d).iterdir())
            rec = json.loads((Path(d) / "r.json").read_text()) if (Path(d) / "r.json").exists() else None
        line = [x for x in out.getvalue().splitlines() if x.startswith("{") and x.endswith("}") and '"verdict"' in x][-1]
        return code, json.loads(line), files, rec

    def test_default_mode_cli_prints_the_verdict_line_and_writes_record_and_sha(self):
        code, line, files, rec = self.cli([], Stub(str(tx.TOKEN_2022_PROGRAM)))
        self.assertEqual(code, 0)
        self.assertEqual((line["verdict"], line["mode"], line["token_program"]), ("PASS", "default", "token-2022"))
        self.assertEqual(files, ["r.json", "r.json.sha256"])
        self.assertEqual(line["sha256"], ss.record_sha256(rec))
        self.assertEqual(rec["code_git_sha"], "c" * 40)
        self.assertEqual(line["rungs"]["throwaway"]["escalated"]["priority_lamports"], 150_000)

    def test_holder_mode_cli(self):
        prog = str(tx.TOKEN_PROGRAM)
        t = HolderModeTests()
        ata = str(tx.ata(t.OWNER, Pubkey.from_string(MINT), Pubkey.from_string(prog)))
        holder = {"owner": str(t.OWNER), "token_account": ata, "owner_lamports": 1, "candidate_source": "stub", "candidates_checked": 1, "skipped": {}}
        code, line, files, rec = self.cli(["--holder"], Stub(prog, sim_for=t.holder_sim(prog)), holder)
        self.assertEqual((code, line["verdict"], line["mode"]), (0, "PASS", "holder"))
        self.assertTrue(line["rungs"]["first"]["full_sell_ok"] and line["rungs"]["escalated"]["control_reverts_at_base_close"])
        self.assertEqual(rec["kind"], hs.KIND_HOLDER)

    def test_failed_verdict_exits_1_and_bad_args_are_refused(self):
        code, line, _files, _rec = self.cli([], Stub(str(tx.TOKEN_PROGRAM), sim_for=lambda b64: {"err": None, "logs": [], "unitsConsumed": 1}))
        self.assertEqual((code, line["verdict"]), (1, "FAIL"))
        for bad in (["--rpc", "https://x.example/?api-key=1"], ["--git-sha", "nothex"]):
            with self.assertRaises(SystemExit):
                hs.main(["--mint", MINT, "--out", "/nonexistent/never-written.json", *bad])


class MintsAndJobScriptTests(unittest.TestCase):
    def script_vars(self) -> dict[str, str]:
        text = JOB.read_text()
        return dict(re.findall(r"^([A-Z0-9_]+MINT|[A-Z0-9_]+POOL)=([1-9A-HJ-NP-Za-km-z]{32,44})\s*$", text, re.M))

    def test_named_mints_match_the_recorded_pools_and_programs(self):
        v = self.script_vars()
        self.assertEqual(set(v), {"TOKEN_MINT", "TOKEN_POOL", "T22_MINT", "T22_POOL"})
        fx = json.loads(FIXTURE.read_text())
        for mint, pool in ((v["TOKEN_MINT"], v["TOKEN_POOL"]), (v["T22_MINT"], v["T22_POOL"])):
            self.assertEqual(str(tx.canonical_pool(Pubkey.from_string(mint))), pool)  # the pool is the mint's canonical pump.fun pool
        self.assertEqual(fx["pool_a_mayhem"], v["T22_POOL"])
        self.assertEqual(fx["pool_b_plain"], v["TOKEN_POOL"])
        a, b = (tx.parse_pool_account(bytes.fromhex(fx[k])) for k in ("pool_a_hex", "pool_b_hex"))
        self.assertEqual(str(a["base_mint"]), v["T22_MINT"])
        self.assertEqual(str(b["base_mint"]), v["TOKEN_MINT"])
        self.assertTrue(tx.pool_is_mayhem(bytes.fromhex(fx["pool_a_hex"])))
        self.assertFalse(tx.pool_is_mayhem(bytes.fromhex(fx["pool_b_hex"])))

    def test_script_is_posix_sh_and_has_no_bashisms(self):
        text = JOB.read_text()
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        for bad in ("[[", "PIPESTATUS", "local ", "declare ", "<<<", "set -o pipefail", "function "):
            self.assertNotIn(bad, text)
        self.assertEqual(subprocess.run(["sh", "-n", str(JOB)], capture_output=True).returncode, 0)
        for need in ("MISCUSI_OUTPUT_DIR", "sha256sum", "git archive", "--holder", "hv4-c0-sell-simulate"):
            self.assertIn(need, text)
        self.assertNotRegex(text, r"/data/mal/arena|meta-capture")

    def test_script_refuses_bad_arguments_before_any_network_or_git_fetch(self):
        for args, env in (([], {}), (["nothex"], {"MISCUSI_OUTPUT_DIR": "/tmp/x"}), (["a" * 40], {})):
            r = subprocess.run(["sh", str(JOB), *args], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **env})
            self.assertNotEqual(r.returncode, 0, (args, r.stdout, r.stderr))
            self.assertIn("REFUSED", r.stdout + r.stderr)

    def test_plan_only_prints_four_commands_for_an_exported_commit(self):
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        has = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{head}:tools/hv4_c0_sell_simulate.py"], capture_output=True).returncode == 0
        if not re.fullmatch(r"[0-9a-f]{40}", head) or not has:
            self.skipTest("HEAD does not hold the tool yet")
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run(["sh", str(JOB), head], capture_output=True, text=True,
                               env={"PATH": "/usr/bin:/bin", "MISCUSI_OUTPUT_DIR": d, "PLAN_ONLY": "1", "MAL_REPO": str(ROOT)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("tools.hv4_c0_sell_simulate"), 4)
        self.assertEqual(r.stdout.count("--holder"), 2)
        v = self.script_vars()
        for m in v.values():
            self.assertIn(m, r.stdout)


if __name__ == "__main__":
    unittest.main()
