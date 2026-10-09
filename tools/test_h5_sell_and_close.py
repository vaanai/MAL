"""Offline tests for tools/h5_sell_and_close.py. A fake RPC (the probe's recorded PumpSwap pool) and throwaway Keypairs. No network,
no real key, nothing root."""
from __future__ import annotations

import argparse
import base64
import json
import struct
from pathlib import Path

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from tools import h5_sell_and_close as sc
from tools import probe_withdraw as pw
from tools import pumpswap_tx as tx
from tools.test_probe_executor import MINT, FakeRpc

RENT = 2_039_280
TOKENS = 123_456_789_012
FEE = 5_000 + 150_000
PROCEEDS = 7_000_000
SIG_SECRET_HINT = "secret"


class SellRpc(FakeRpc):
    """The probe's FakeRpc plus what sell-and-close calls. Token balance, WSOL account and the transaction meta are scripted."""

    def __init__(self, user: Pubkey, tokens: int = TOKENS, wsol: int | None = None, token_acct: bool | None = None, **kw):
        super().__init__(**kw)
        self.user, self.tokens, self.wsol = user, tokens, wsol
        self.has_acct = (tokens > 0) if token_acct is None else token_acct
        self.base_ata = tx.ata(user, Pubkey.from_string(MINT), tx.TOKEN_2022_PROGRAM)
        self.wsol_ata = tx.ata(user, tx.WSOL_MINT, tx.TOKEN_PROGRAM)
        self.simulated: list[str] = []
        self.sent: list[str] = []
        self.foreign_acct: str | None = None
        self.send_err = False

    def __call__(self, method, params):
        if method == "getAccountInfo" and params[0] == str(self.wsol_ata):
            self.calls.append(method)
            return {"context": {"slot": 77}, "value": None if self.wsol is None else {"owner": str(tx.TOKEN_PROGRAM), "lamports": self.wsol}}
        if method == "getTokenAccountsByOwner":
            self.calls.append(method)
            if not self.has_acct:
                return {"value": []}
            pk = self.foreign_acct or str(self.base_ata)
            info = {"owner": str(self.user), "mint": MINT, "tokenAmount": {"amount": str(self.tokens)}}
            return {"value": [{"pubkey": pk, "account": {"lamports": RENT, "data": {"parsed": {"info": info}}}}]}
        if method == "getLatestBlockhash":
            self.calls.append(method)
            return {"context": {"slot": 1}, "value": {"blockhash": "11111111111111111111111111111111", "lastValidBlockHeight": 1000}}
        if method == "simulateTransaction":
            self.simulated.append(params[0])
            assert params[1]["sigVerify"] is True and params[1]["encoding"] == "base64"
            return super().__call__(method, params)
        if method == "sendTransaction":
            self.calls.append(method)
            assert params[1] == {"encoding": "base64", "skipPreflight": False}  # preflight on: a bad tx never lands
            self.sent.append(params[0])
            return str(VersionedTransaction.from_bytes(base64.b64decode(params[0])).signatures[0])
        if method == "getSignatureStatuses":
            self.calls.append(method)
            err = {"InstructionError": [3, {"Custom": 6004}]} if self.send_err else None
            return {"context": {"slot": 1}, "value": [{"err": err, "confirmationStatus": "confirmed"}]}
        if method == "getTransaction":
            self.calls.append(method)
            net = PROCEEDS + RENT - FEE
            return {"meta": {"err": None, "fee": FEE, "preBalances": [1_000_000_000, 1], "postBalances": [1_000_000_000 + net, 0]}}
        return super().__call__(method, params)


def make_args(tmp: Path, kp: Keypair, **kw) -> argparse.Namespace:
    tmp.mkdir(parents=True, exist_ok=True)
    keyfile = tmp / "key.json"
    keyfile.write_text(json.dumps(list(bytes(kp))))
    base = dict(mint=MINT, keyfile=str(keyfile), rpc_env="/none", slippage_bps=None, emergency=False,
                priority_lamports=sc.MAX_PRIORITY_LAMPORTS, send=False, force=False)
    return argparse.Namespace(**{**base, **kw})


def run(tmp: Path, rpc_kw=None, **kw):
    kp = Keypair()
    rpc = SellRpc(kp.pubkey(), **(rpc_kw or {}))
    lines: list[str] = []
    args = make_args(tmp, kp, **kw)
    rc = sc.run(args, rpc, is_active=lambda u: False, out=lines.append, sleep=lambda s: None, check_location=False)
    return rc, rpc, lines, kp


def sim_tx(rpc: SellRpc) -> VersionedTransaction:
    assert len(rpc.simulated) == 1
    return VersionedTransaction.from_bytes(base64.b64decode(rpc.simulated[0]))


def decode(t: VersionedTransaction) -> list[tuple[Pubkey, bytes, list[Pubkey]]]:
    m = t.message
    keys = list(m.account_keys)
    return [(keys[ix.program_id_index], bytes(ix.data), [keys[i] for i in ix.accounts]) for ix in m.instructions]


def sell_args(t: VersionedTransaction) -> tuple[int, int]:
    sells = [d for pid, d, _ in decode(t) if pid == tx.PUMPSWAP_PROGRAM]
    assert len(sells) == 1 and sells[0][:8] == tx.DISC_SELL
    return struct.unpack("<QQ", sells[0][8:24])


# --- min_out: never 0, the flag lowers it, emergency is exactly 1 ----------------------------------------------------------

def test_choose_min_out_default_lowers_and_is_never_zero():
    assert sc.choose_min_out(1_000_000, 1500, False) == 850_000
    assert sc.choose_min_out(1_000_000, 5000, False) == 500_000 < sc.choose_min_out(1_000_000, 1500, False)
    assert sc.choose_min_out(1_000_000, 0, False) == 1_000_000
    assert sc.choose_min_out(1_000_000, 9500, False) == 50_000
    assert sc.choose_min_out(10**12, 9500, True) == sc.EMERGENCY_MIN_OUT == 1
    assert sc.choose_min_out(None, 1500, True) == 1  # emergency needs no quote
    for bad_quote in (None, 0, -5):
        with pytest.raises(pw.Refuse, match="never sells blind"):
            sc.choose_min_out(bad_quote, 1500, False)
    with pytest.raises(pw.Refuse, match="min_out would be 0"):
        sc.choose_min_out(1, 1500, False)  # floor(0.85) == 0: refused, not rounded to a guard of 0
    for bad_bps in (-1, 9501, 10_000, True, 1500.5):
        with pytest.raises(pw.Refuse, match="--slippage-bps"):
            sc.choose_min_out(1_000_000, bad_bps, False)


def test_parse_mint():
    assert sc.parse_mint(MINT) == Pubkey.from_string(MINT)
    for bad in ("", "abc", str(tx.WSOL_MINT), str(tx.SYSTEM_PROGRAM), MINT[:-1], MINT + "11", "0" + MINT[1:], " " + MINT):
        with pytest.raises(pw.Refuse):
            sc.parse_mint(bad)


# --- dry run is the default; the transaction sells everything and closes both accounts -------------------------------------

def test_dry_run_simulates_and_sends_nothing(tmp_path):
    rc, rpc, lines, kp = run(tmp_path)
    assert rc == 0 and rpc.sent == [] and "sendTransaction" not in rpc.calls
    assert any(l.startswith("simulated OK") for l in lines) and any("dry run: nothing sent" in l for l in lines)
    t = sim_tx(rpc)
    amount, min_out = sell_args(t)
    assert amount == TOKENS  # ALL of them
    snap_quote = next(int(l.split("quote:")[1].split()[0]) for l in lines if l.startswith("quote:"))
    assert min_out == snap_quote * 8500 // 10_000 and min_out >= 1
    ixs = decode(t)
    closes = [acc[0] for pid, d, acc in ixs if d == b"\x09"]
    assert rpc.base_ata in closes and rpc.wsol_ata in closes  # token account and WSOL account both closed
    assert t.message.header.num_required_signatures == 1 and list(t.message.account_keys)[0] == kp.pubkey()


def test_slippage_flag_lowers_min_out_and_emergency_is_one_lamport(tmp_path):
    _, rpc0, _, _ = run(tmp_path / "a")
    _, rpc1, _, _ = run(tmp_path / "b", slippage_bps=5000)
    _, rpc2, lines2, _ = run(tmp_path / "c", emergency=True)
    m0, m1, m2 = (sell_args(sim_tx(r))[1] for r in (rpc0, rpc1, rpc2))
    assert m1 < m0 and m2 == 1
    assert any("EMERGENCY" in l for l in lines2)


def test_emergency_and_slippage_flag_are_exclusive(tmp_path):
    with pytest.raises(pw.Refuse, match="exclusive"):
        run(tmp_path, emergency=True, slippage_bps=100)


def test_send_prints_signature_and_fill_and_sends_once(tmp_path):
    rc, rpc, lines, kp = run(tmp_path, send=True)
    assert rc == 0 and len(rpc.sent) == 1
    sig = str(VersionedTransaction.from_bytes(base64.b64decode(rpc.sent[0])).signatures[0])
    assert f"signature: {sig}" in lines
    fill = next(l for l in lines if l.startswith("fill:"))
    assert f"tokens_sold={TOKENS}" in fill and f"proceeds_lamports={PROCEEDS}" in fill and f"fee_lamports={FEE}" in fill
    assert f"rent_refunded_lamports={RENT}" in fill


def test_nothing_in_the_output_is_key_material(tmp_path):
    rc, rpc, lines, kp = run(tmp_path, send=True)
    text = "\n".join(lines)
    secret = bytes(kp)
    assert str(kp) not in text and secret.hex() not in text and secret[:32].hex() not in text
    assert json.dumps(list(secret)) not in text and base64.b64encode(secret).decode() not in text
    assert str(kp.pubkey()) in text  # the public key is fine


def test_failed_transaction_is_reported_not_swallowed(tmp_path):
    with pytest.raises(pw.Refuse, match="failed"):
        _run_with(tmp_path, send_err=True)


def _run_with(tmp, send_err=False, **kw):
    kp = Keypair()
    rpc = SellRpc(kp.pubkey())
    rpc.send_err = send_err
    return sc.run(make_args(tmp, kp, send=True, **kw), rpc, is_active=lambda u: False, out=lambda s: None, sleep=lambda s: None, check_location=False)


def test_simulation_failure_refuses_and_sends_nothing(tmp_path):
    kp = Keypair()
    rpc = SellRpc(kp.pubkey(), sim_err={"InstructionError": [3, {"Custom": 6004}]})
    with pytest.raises(pw.Refuse, match="simulation failed"):
        sc.run(make_args(tmp_path, kp, send=True), rpc, is_active=lambda u: False, out=lambda s: None, sleep=lambda s: None, check_location=False)
    assert rpc.sent == []


# --- close-only, nothing to do, refusals -----------------------------------------------------------------------------------

def test_zero_balance_closes_without_a_sell(tmp_path):
    rc, rpc, lines, _ = run(tmp_path, rpc_kw={"tokens": 0, "token_acct": True, "wsol": 2_039_280})
    t = sim_tx(rpc)
    assert rc == 0 and all(pid != tx.PUMPSWAP_PROGRAM for pid, _, _ in decode(t))
    closes = [acc[0] for pid, d, acc in decode(t) if d == b"\x09"]
    assert closes == [rpc.base_ata, rpc.wsol_ata]
    assert any("close only, no sell" in l for l in lines)


def test_nothing_to_close_is_a_clean_no_op(tmp_path):
    rc, rpc, lines, _ = run(tmp_path, rpc_kw={"tokens": 0, "token_acct": False, "wsol": None})
    assert rc == 0 and rpc.simulated == [] and any("nothing to do" in l for l in lines)


def test_refuses_a_token_account_that_is_not_the_associated_one(tmp_path):
    kp = Keypair()
    rpc = SellRpc(kp.pubkey())
    rpc.foreign_acct = str(Keypair().pubkey())
    with pytest.raises(pw.Refuse, match="not the associated account"):
        sc.run(make_args(tmp_path, kp), rpc, is_active=lambda u: False, out=lambda s: None, check_location=False)
    assert rpc.simulated == []


def test_refuses_while_an_executor_unit_is_active_unless_forced(tmp_path):
    kp = Keypair()
    for unit in sc.UNITS:
        rpc = SellRpc(kp.pubkey())
        with pytest.raises(pw.Refuse, match=f"{unit} is not stopped"):
            sc.run(make_args(tmp_path, kp), rpc, is_active=lambda u, unit=unit: u == unit, out=lambda s: None, check_location=False)
        assert rpc.calls == []  # refused before any RPC
    rpc = SellRpc(kp.pubkey())
    assert sc.run(make_args(tmp_path, kp, force=True), rpc, is_active=lambda u: True, out=lambda s: None, check_location=False) == 0


def test_unit_running_is_false_only_for_inactive_or_failed_and_fails_closed(monkeypatch):
    import subprocess as sp

    def fake(state, rc=0):
        calls = []

        def run_(argv, **kw):
            calls.append(argv)
            return sp.CompletedProcess(argv, rc, state, "")

        monkeypatch.setattr(sc.subprocess, "run", run_)
        return calls

    for state in ("inactive\n", "failed\n"):
        calls = fake(state)
        assert sc.unit_running("mal-h5-executor") is False
        assert calls == [["/usr/bin/systemctl", "show", "-p", "ActiveState", "--value", "mal-h5-executor"]]
    for state in ("active\n", "activating\n", "deactivating\n", "reloading\n", "maintenance\n", "\n", "", "unknown\n", "inactive extra\n"):
        fake(state)
        assert sc.unit_running("mal-probe-executor") is True, repr(state)
    fake("inactive\n", rc=1)  # an error exit with a plausible stdout is still no answer
    assert sc.unit_running("x") is True

    def boom(argv, **kw):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(sc.subprocess, "run", boom)  # no systemctl at all: fail closed
    assert sc.unit_running("x") is True

    def slow(argv, **kw):
        raise sp.TimeoutExpired(argv, 15)

    monkeypatch.setattr(sc.subprocess, "run", slow)
    assert sc.unit_running("x") is True
    assert "pw.executor_active" not in Path(sc.__file__).read_text().split("def unit_running")[1].split("def parse_mint")[0].replace("probe_withdraw.executor_active, which", "")
    assert sc.run.__kwdefaults__["is_active"] is sc.unit_running  # the default guard is the strict one


def test_priority_is_capped(tmp_path):
    for bad in (0, -1, sc.MAX_PRIORITY_LAMPORTS + 1):
        with pytest.raises(pw.Refuse, match="--priority-lamports"):
            run(tmp_path / str(bad), priority_lamports=bad)


def test_allowlist_refuses_a_foreign_program_before_signing(tmp_path):
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction

    kp = Keypair()
    rpc = SellRpc(kp.pubkey())
    plan = sc.build_plan(rpc, kp.pubkey(), Pubkey.from_string(MINT), slippage_bps=1500, emergency=False, priority=150_000)
    evil = Instruction(Pubkey.from_string("Stake11111111111111111111111111111111111111"), b"\x01", [AccountMeta(kp.pubkey(), True, True)])
    plan["ixs"] = [*plan["ixs"], evil]
    with pytest.raises(pw.Refuse, match="allowlist refused"):
        sc.sign_plan(plan, kp, Hash.default())
    # a transfer of SOL out of the wallet is not in the allowlist either
    from solders.system_program import TransferParams, transfer

    plan["ixs"] = [*plan["ixs"][:-1], transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=Keypair().pubkey(), lamports=1))]
    with pytest.raises(pw.Refuse, match="allowlist refused"):
        sc.sign_plan(plan, kp, Hash.default())


def test_pool_must_be_canonical(tmp_path):
    kp = Keypair()
    rpc = SellRpc(kp.pubkey(), owner=str(tx.TOKEN_PROGRAM))  # the "pool" account is not owned by PumpSwap
    with pytest.raises(pw.Refuse, match="not usable"):
        sc.build_plan(rpc, kp.pubkey(), Pubkey.from_string(MINT), slippage_bps=1500, emergency=False, priority=150_000)


# --- key custody: location check, errors without contents, root-only CLI ----------------------------------------------------

def test_key_location_is_enforced_when_not_testing(tmp_path):
    kp = Keypair()
    args = make_args(tmp_path, kp)
    with pytest.raises(pw.Refuse, match="root:root"):  # a test user's own file in its own dir is not root:root 0400/0700
        sc.run(args, SellRpc(kp.pubkey()), is_active=lambda u: False, out=lambda s: None, check_location=True)


def test_malformed_key_error_has_no_contents(tmp_path):
    kp = Keypair()
    args = make_args(tmp_path, kp)
    Path(args.keyfile).write_text("[1, 2, 3, SECRETSECRET]")
    with pytest.raises(pw.Refuse) as e:
        sc.run(args, SellRpc(kp.pubkey()), is_active=lambda u: False, out=lambda s: None, check_location=False)
    assert "SECRETSECRET" not in str(e.value.code) and "cannot load keyfile" in str(e.value.code)


def test_cli_refuses_non_root_without_test_mode(monkeypatch, capsys):
    import os

    if os.geteuid() == 0:
        pytest.skip("test needs a non-root user")
    monkeypatch.delenv("MAL_LIVE_TEST", raising=False)
    assert sc.main(["--mint", MINT]) == 1
    assert "root-only" in capsys.readouterr().err


def test_cli_dry_run_is_the_default_and_send_is_opt_in():
    import re

    src = Path(sc.__file__).read_text()
    assert re.search(r'add_argument\("--send", action="store_true"', src)
    assert "if not args.send:" in src
    assert 'default=None, help=f"lower the min_out guard' in src  # the flag is optional; the default guard is 1500 bps
