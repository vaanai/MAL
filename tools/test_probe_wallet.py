"""Offline tests for make-probe-wallet.sh and probe_withdraw.py (fake RPC, temp dirs only)."""
from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction

from tools import probe_withdraw as pw

ROOT = Path(__file__).resolve().parent.parent
MAKE = ROOT / "scripts/mal-fast/make-probe-wallet.sh"


def _make(tmp_path, *extra):
    env = {
        "PATH": os.environ["PATH"],
        "MAL_LIVE_DIR": str(tmp_path / "live"),
        "MAL_LIVE_PY": sys.executable,
        "MAL_LIVE_TEST": "1",
    }
    return subprocess.run(["bash", str(MAKE), *extra], env=env, capture_output=True, text=True)


def test_make_wallet_creates_key_and_prints_only_pubkey(tmp_path):
    r = _make(tmp_path)
    assert r.returncode == 0, r.stderr
    f = tmp_path / "live/probe-wallet.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o400
    assert stat.S_IMODE(f.parent.stat().st_mode) == 0o700
    raw = json.loads(f.read_text())
    assert len(raw) == 64 and all(isinstance(x, int) for x in raw)
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 2
    assert lines[1] == "Fund with 0.5 SOL. Never share the file."
    assert lines[0] == str(Keypair.from_bytes(bytes(raw)).pubkey())
    assert r.stderr == ""
    secret = bytes(raw)
    for needle in (json.dumps(raw), secret[:32].hex(), base64.b64encode(secret).decode()):
        assert needle not in r.stdout + r.stderr
    assert [p.name for p in tmp_path.iterdir()] == ["live"]


def test_make_wallet_refuses_overwrite(tmp_path):
    assert _make(tmp_path).returncode == 0
    f = tmp_path / "live/probe-wallet.json"
    before = f.read_bytes()
    r = _make(tmp_path)
    assert r.returncode != 0 and "already exists" in r.stderr
    assert f.read_bytes() == before


def test_make_wallet_dry_run_writes_nothing(tmp_path):
    r = _make(tmp_path, "--dry-run")
    assert r.returncode == 0 and "[dry-run]" in r.stdout
    assert not (tmp_path / "live").exists()


def test_make_wallet_requires_root_without_test_flag(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("running as root")
    env = {"PATH": os.environ["PATH"], "MAL_LIVE_DIR": str(tmp_path / "live"), "MAL_LIVE_PY": sys.executable}
    r = subprocess.run(["bash", str(MAKE)], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "root" in r.stderr
    assert not (tmp_path / "live").exists()


# ---------------------------------------------------------------- withdraw

FEE = 5000
BLOCKHASH = str(Hash.default())


class FakeRpc:
    def __init__(self, wallet, balance, accounts=(), send_status="confirmed"):
        self.wallet = wallet
        self.balance = balance
        self.accounts = list(accounts)  # (pubkey, program, mint, amount, lamports)
        self.sent: list[Transaction] = []
        self.simulated = 0
        self.methods: list[str] = []
        self.send_status = send_status

    def __call__(self, method, params):
        self.methods.append(method)
        if method == "getTokenAccountsByOwner":
            prog = params[1]["programId"]
            val = [
                {
                    "pubkey": str(a[0]),
                    "account": {
                        "lamports": a[4],
                        "data": {"parsed": {"info": {"mint": a[2], "tokenAmount": {"amount": str(a[3])}}}},
                    },
                }
                for a in self.accounts
                if a[1] == prog
            ]
            return {"value": val}
        if method == "getBalance":
            return {"value": self.balance}
        if method == "getLatestBlockhash":
            return {"value": {"blockhash": BLOCKHASH}}
        if method == "getFeeForMessage":
            return {"value": FEE}
        if method == "simulateTransaction":
            self.simulated += 1
            return {"value": {"err": None}}
        if method == "sendTransaction":
            tx = Transaction.from_bytes(base64.b64decode(params[0]))
            assert params[1]["skipPreflight"] is False
            self.sent.append(tx)
            return str(tx.signatures[0])
        if method == "getSignatureStatuses":
            if self.send_status is None:
                return {"value": [None]}
            return {"value": [{"err": None, "confirmationStatus": self.send_status}]}
        raise AssertionError(method)


def _setup(tmp_path, balance=100_000_000, accounts_fn=None):
    kp = Keypair()
    keyfile = tmp_path / "k.json"
    keyfile.write_text(json.dumps(list(bytes(kp))))
    dest = Keypair().pubkey()
    accounts = accounts_fn(kp) if accounts_fn else []
    args = SimpleNamespace(
        to=str(dest),
        keyfile=str(keyfile),
        state_file=str(tmp_path / "state-live.json"),
        dry_run=False,
        yes=True,
        force=False,
    )
    return kp, dest, args, FakeRpc(kp.pubkey(), balance, accounts)


def _run(args, rpc, **kw):
    lines: list[str] = []
    kw.setdefault("is_active", lambda: False)
    kw.setdefault("sleep", lambda s: None)
    rc = pw.run(args, rpc, out=lines.append, **kw)
    return rc, "\n".join(lines)


def _tok(prog="spl-token"):
    return str(pw.TOKEN_PROGRAMS[prog])


def test_transfer_amount_is_balance_minus_fee(tmp_path):
    kp, dest, args, rpc = _setup(tmp_path, balance=123_456_789)
    rc, _ = _run(args, rpc)
    assert rc == 0 and len(rpc.sent) == 1
    ix = rpc.sent[0].message.instructions[0]
    assert int.from_bytes(bytes(ix.data)[4:12], "little") == 123_456_789 - FEE
    keys = rpc.sent[0].message.account_keys
    assert dest in keys and keys[0] == kp.pubkey()


def test_closes_only_zero_accounts_and_lists_nonzero(tmp_path):
    z1, z2, nz = Keypair().pubkey(), Keypair().pubkey(), Keypair().pubkey()
    mint = str(Keypair().pubkey())

    def accts(kp):
        return [
            (z1, _tok(), mint, 0, 2_039_280),
            (z2, _tok("token-2022"), mint, 0, 2_100_000),
            (nz, _tok(), mint, 777, 2_039_280),
        ]

    kp, dest, args, rpc = _setup(tmp_path, accounts_fn=accts)
    rc, out = _run(args, rpc)
    assert rc == 0
    assert f"mint={mint} amount=777" in out and "NOT TOUCHED" in out
    assert len(rpc.sent) == 2  # one close tx (2 accounts) + transfer
    close_keys = set(rpc.sent[0].message.account_keys)
    assert z1 in close_keys and z2 in close_keys and nz not in close_keys
    assert len(rpc.sent[0].message.instructions) == 2
    assert all(bytes(i.data) == bytes([9]) for i in rpc.sent[0].message.instructions)


def test_dry_run_sends_nothing(tmp_path):
    kp, dest, args, rpc = _setup(tmp_path)
    args.dry_run = True
    rc, out = _run(args, rpc)
    assert rc == 0 and rpc.sent == [] and rpc.simulated == 1
    assert "sendTransaction" not in rpc.methods and "Nothing sent" in out


def test_refuses_open_positions_and_executor_unless_force(tmp_path):
    kp, dest, args, rpc = _setup(tmp_path)
    Path(args.state_file).write_text(json.dumps({"open_positions": 2}))
    with pytest.raises(SystemExit, match="open position"):
        _run(args, rpc)
    assert rpc.sent == []
    Path(args.state_file).write_text(json.dumps({"positions": []}))
    with pytest.raises(SystemExit, match="mal-probe-executor is active"):
        _run(args, rpc, is_active=lambda: True)
    Path(args.state_file).write_text("not json")
    with pytest.raises(SystemExit, match="cannot read"):
        _run(args, rpc)
    args.force = True
    rc, _ = _run(args, rpc, is_active=lambda: True)
    assert rc == 0 and len(rpc.sent) == 1


def test_destination_validation(tmp_path):
    kp, dest, args, rpc = _setup(tmp_path)
    args.to = "not-an-address"
    with pytest.raises(SystemExit, match="valid base58"):
        _run(args, rpc)
    args.to = str(kp.pubkey())
    with pytest.raises(SystemExit, match="must differ"):
        _run(args, rpc)
    assert rpc.methods == []


def test_bad_keyfile_does_not_echo_contents(tmp_path):
    kp, dest, args, rpc = _setup(tmp_path)
    Path(args.keyfile).write_text("[1, 2, 3, SECRETMARKER")
    with pytest.raises(SystemExit) as e:
        _run(args, rpc)
    assert "SECRETMARKER" not in str(e.value.code)


def test_confirmation_prompt(tmp_path):
    kp, dest, args, rpc = _setup(tmp_path)
    args.yes = False
    rc, out = _run(args, rpc, input_fn=lambda p: "wrong")
    assert rc == 1 and rpc.sent == [] and "aborted" in out
    rc, _ = _run(args, rpc, input_fn=lambda p: str(dest)[:6])
    assert rc == 0 and len(rpc.sent) == 1


def test_confirmation_timeout(tmp_path):
    kp, dest, args, rpc = _setup(tmp_path)
    rpc.send_status = None
    t = iter(range(0, 10_000, 30))
    orig = pw.time.monotonic
    pw.time.monotonic = lambda: next(t)
    try:
        with pytest.raises(SystemExit, match="timeout"):
            _run(args, rpc)
    finally:
        pw.time.monotonic = orig


def test_rpc_url_redacted_in_errors(tmp_path, monkeypatch, capsys):
    kp, dest, args, rpc = _setup(tmp_path)
    secret = "abcd1234-ef56-7890-abcd-1234567890ab"
    monkeypatch.setenv("HELIUS_API_KEY", secret)

    def boom(self, method, params):
        raise SystemExit(f"rpc {method} failed: URLError: https://x.helius-rpc.com/?api-key={secret}")

    import tools.pumpswap_simulate as ps

    monkeypatch.setattr(ps.Rpc, "__call__", boom)
    rc = pw.main(["--to", str(dest), "--keyfile", args.keyfile, "--state-file", args.state_file])
    err = capsys.readouterr()
    assert rc == 1 and secret not in err.err + err.out and "REDACTED" in err.err


def test_wrapper_script_is_executable_and_syntax_ok():
    sh = ROOT / "scripts/mal-fast/probe-withdraw.sh"
    assert os.access(sh, os.X_OK) and os.access(MAKE, os.X_OK)
    assert subprocess.run(["bash", "-n", str(sh)]).returncode == 0
