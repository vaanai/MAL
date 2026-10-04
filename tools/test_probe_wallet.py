"""Offline tests for make-probe-wallet.sh and probe_withdraw.py (fake RPC, temp dirs only)."""
from __future__ import annotations

import base64
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from solders.hash import Hash
from solders.keypair import Keypair
from solders.transaction import Transaction

from tools import probe_withdraw as pw

ROOT = Path(__file__).resolve().parent.parent
MAKE = ROOT / "scripts/mal-fast/make-probe-wallet.sh"
WRAP = ROOT / "scripts/mal-fast/probe-withdraw.sh"


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


def test_make_wallet_ignores_overrides_without_test_flag(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("running as root")
    env = {"PATH": os.environ["PATH"], "MAL_LIVE_DIR": str(tmp_path / "live"), "MAL_LIVE_PY": sys.executable}
    r = subprocess.run(["bash", str(MAKE)], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "root" in r.stderr
    assert not (tmp_path / "live").exists()


def test_make_wallet_refuses_symlinked_dir(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "live").symlink_to(real)
    r = _make(tmp_path)
    assert r.returncode != 0 and "symlink" in r.stderr
    assert list(real.iterdir()) == []


def test_make_wallet_fsync_readback_before_print_and_hardening():
    text = MAKE.read_text()
    assert text.index("os.fsync(f.fileno())") < text.index("os.fsync(dfd)") < text.index("back.pubkey()") < text.index("print(pub)")
    assert "ulimit -c 0" in text and "RLIMIT_CORE" in text and "install -d -m 0700" in text
    assert "--user-group" in text and "nologin" in text and "-I -c" in text


def test_make_wallet_readback_failure_removes_file_and_prints_no_pubkey(tmp_path):
    # Fake python: runs the real script text with Keypair.from_bytes patched to return a wrong key.
    shim = tmp_path / "py"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "from solders.keypair import Keypair as K\n"
        "K.from_bytes = staticmethod(lambda b: K())\n"
        "code = sys.argv[sys.argv.index('-c') + 1]\n"
        "sys.argv = ['c'] + sys.argv[sys.argv.index('-c') + 2:]\n"
        "exec(compile(code, 'c', 'exec'))\n"
    )
    shim.chmod(0o755)
    env = {"PATH": os.environ["PATH"], "MAL_LIVE_DIR": str(tmp_path / "live"), "MAL_LIVE_PY": str(shim), "MAL_LIVE_TEST": "1"}
    r = subprocess.run(["bash", str(MAKE)], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "write/verify failed" in r.stderr
    assert not (tmp_path / "live/probe-wallet.json").exists()
    assert "Fund with" not in r.stdout


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
        self.fail_if = None
        self.bad_owner = False

    def __call__(self, method, params):
        self.methods.append(method)
        if method == "getTokenAccountsByOwner":
            prog = params[1]["programId"]
            owner = str(Keypair().pubkey()) if self.bad_owner else str(self.wallet)
            return {
                "value": [
                    {
                        "pubkey": str(a[0]),
                        "account": {
                            "lamports": a[4],
                            "owner": prog,
                            "data": {"parsed": {"info": {"mint": a[2], "owner": owner, "tokenAmount": {"amount": str(a[3])}}}},
                        },
                    }
                    for a in self.accounts
                    if a[1] == prog
                ]
            }
        if method == "getAccountInfo":
            return {"value": None}
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
            if self.fail_if and self.fail_if(tx):
                raise SystemExit("rpc sendTransaction error: simulated failure")
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
        fill_log=str(tmp_path / "probe-fills.jsonl"),
        dry_run=False,
        yes=True,
        force=False,
        allow_stranded=False,
        skip_close=False,
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


def test_nonzero_token_refuses_by_default_and_override_closes_zero_only(tmp_path):
    z1, z2, nz = Keypair().pubkey(), Keypair().pubkey(), Keypair().pubkey()
    mint = str(Keypair().pubkey())

    def accts(kp):
        return [
            (z1, _tok(), mint, 0, 2_039_280),
            (z2, _tok("token-2022"), mint, 0, 2_100_000),
            (nz, _tok(), mint, 777, 2_039_280),
        ]

    kp, dest, args, rpc = _setup(tmp_path, accounts_fn=accts)
    with pytest.raises(SystemExit, match="stranded"):
        _run(args, rpc)
    assert rpc.sent == []
    args.allow_stranded = True
    rc, out = _run(args, rpc)
    assert rc == 0
    assert f"mint={mint} amount=777" in out and "not touched" in out
    assert len(rpc.sent) == 2  # one close tx (2 accounts) + transfer
    close_keys = set(rpc.sent[0].message.account_keys)
    assert z1 in close_keys and z2 in close_keys and nz not in close_keys
    assert all(bytes(i.data) == bytes([9]) for i in rpc.sent[0].message.instructions)


def test_wsol_account_is_closed_not_stranded(tmp_path):
    w = Keypair().pubkey()
    kp, dest, args, rpc = _setup(
        tmp_path, balance=1_000_000, accounts_fn=lambda kp: [(w, _tok(), pw.WSOL_MINT, 50_000_000, 52_039_280)]
    )
    rc, _ = _run(args, rpc)
    assert rc == 0 and len(rpc.sent) == 2
    assert w in rpc.sent[0].message.account_keys


def test_wrong_account_owner_aborts(tmp_path):
    z = Keypair().pubkey()
    kp, dest, args, rpc = _setup(tmp_path, accounts_fn=lambda kp: [(z, _tok(), "m", 0, 1)])
    rpc.bad_owner = True
    with pytest.raises(SystemExit, match="owner mismatch"):
        _run(args, rpc)
    assert rpc.sent == []


def test_dry_run_sends_nothing(tmp_path):
    kp, dest, args, rpc = _setup(tmp_path)
    args.dry_run = True
    rc, out = _run(args, rpc)
    assert rc == 0 and rpc.sent == [] and rpc.simulated == 1
    assert "sendTransaction" not in rpc.methods and "Nothing sent" in out


def test_refuses_open_positions_and_executor_unless_force(tmp_path):
    kp, dest, args, rpc = _setup(tmp_path)
    Path(args.state_file).write_text(json.dumps({"open": {"m1": {}, "m2": {}}}))
    with pytest.raises(SystemExit, match="open position"):
        _run(args, rpc)
    assert rpc.sent == []
    Path(args.state_file).write_text(json.dumps({"open": {}}))
    with pytest.raises(SystemExit, match="mal-probe-executor is active"):
        _run(args, rpc, is_active=lambda: True)
    args.force = True
    rc, _ = _run(args, rpc, is_active=lambda: True)
    assert rc == 0 and len(rpc.sent) == 1


def test_state_check_fails_closed(tmp_path):
    kp, dest, args, rpc = _setup(tmp_path)
    assert _run(args, rpc)[0] == 0  # no state, no fill log: probe never traded
    Path(args.fill_log).write_text('{"kind":"buy","mode":"live"}\n')
    with pytest.raises(SystemExit, match="live rows"):
        _run(args, rpc)
    Path(args.fill_log).write_text('{"kind":"buy","mode":"dryrun"}\n')
    assert _run(args, rpc)[0] == 0
    Path(args.fill_log).write_text('{"mode": "live"}\n')
    Path(args.state_file).write_text("garbage")
    with pytest.raises(SystemExit, match="live rows"):
        _run(args, rpc)
    args.force = True
    assert _run(args, rpc)[0] == 0


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


def test_confirmation_requires_full_address_and_prints_no_hint(tmp_path):
    kp, dest, args, rpc = _setup(tmp_path)
    args.yes = False
    prompts = []

    def ask(p):
        prompts.append(p)
        return answers.pop(0)

    for wrong in ("wrong", str(dest)[:6]):
        answers = [wrong]
        rc, out = _run(args, rpc, input_fn=ask)
        assert rc == 1 and rpc.sent == [] and "aborted" in out
    answers = [str(dest)]
    rc, _ = _run(args, rpc, input_fn=ask)
    assert rc == 0 and len(rpc.sent) == 1
    assert all(str(dest)[:6] not in p for p in prompts)


def test_blockhash_fetched_after_prompt(tmp_path):
    kp, dest, args, rpc = _setup(tmp_path)
    args.yes = False
    seen = {}

    def prompt(p):
        seen["n"] = rpc.methods.count("getLatestBlockhash")
        return str(dest)

    rc, _ = _run(args, rpc, input_fn=prompt)
    assert rc == 0 and rpc.methods.count("getLatestBlockhash") > seen["n"]


def test_close_failure_falls_back_to_singles_and_still_transfers(tmp_path):
    z1, z2 = Keypair().pubkey(), Keypair().pubkey()
    kp, dest, args, rpc = _setup(
        tmp_path, accounts_fn=lambda kp: [(z1, _tok(), "m", 0, 2_000_000), (z2, _tok(), "m", 0, 2_000_000)]
    )
    rpc.fail_if = lambda tx: z2 in tx.message.account_keys
    rc, out = _run(args, rpc)
    assert rc == 0 and f"NOT CLOSED: account={z2}" in out
    assert len(rpc.sent) == 2  # z1 single close + transfer
    assert z1 in rpc.sent[0].message.account_keys and z2 not in rpc.sent[0].message.account_keys


def test_skip_close(tmp_path):
    z = Keypair().pubkey()
    kp, dest, args, rpc = _setup(tmp_path, accounts_fn=lambda kp: [(z, _tok(), "m", 0, 2_000_000)])
    args.skip_close = True
    rc, _ = _run(args, rpc)
    assert rc == 0 and len(rpc.sent) == 1


def test_confirmation_timeout(tmp_path, monkeypatch):
    kp, dest, args, rpc = _setup(tmp_path)
    rpc.send_status = None
    t = iter(range(0, 10_000, 30))
    monkeypatch.setattr(pw.time, "monotonic", lambda: next(t))
    with pytest.raises(SystemExit, match="timeout"):
        _run(args, rpc)


def test_rpc_url_redacted_and_process_env_ignored(tmp_path, monkeypatch, capsys):
    kp, dest, args, rpc = _setup(tmp_path)
    secret = "abcd1234-ef56-7890-abcd-1234567890ab"
    envf = tmp_path / "helius.env"
    envf.write_text(f"HELIUS_API_KEY={secret}\n")
    monkeypatch.setenv("HELIUS_API_KEY", "FROMENV")
    assert pw.load_rpc_url(str(envf)).endswith(secret)

    def boom(self, method, params):
        raise SystemExit(f"rpc {method} failed: URLError: https://x/?api-key={secret}")

    monkeypatch.setattr(pw.Rpc, "__call__", boom)
    rc = pw.main(
        ["--to", str(dest), "--keyfile", args.keyfile, "--state-file", args.state_file,
         "--fill-log", args.fill_log, "--rpc-env", str(envf)]
    )
    cap = capsys.readouterr()
    assert rc == 1 and secret not in cap.err + cap.out and "REDACTED" in cap.err


def test_probe_withdraw_is_self_contained():
    src = (ROOT / "tools/probe_withdraw.py").read_text()
    assert not re.search(r"^\s*(from|import)\s+tools", src, re.M)
    r = subprocess.run(
        [sys.executable, "-I", str(ROOT / "tools/probe_withdraw.py"), "--help"], capture_output=True, text=True, cwd="/"
    )
    assert r.returncode == 0 and "--allow-stranded" in r.stdout and "--skip-close" in r.stdout


def test_withdraw_wrapper_ignores_overrides_without_test_flag():
    if os.geteuid() == 0:
        pytest.skip("running as root")
    env = {"PATH": os.environ["PATH"], "MAL_LIVE_PY": sys.executable}
    r = subprocess.run(["bash", str(WRAP), "--help"], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "root" in r.stderr


def test_withdraw_wrapper_test_mode_runs_and_prints_sha():
    env = {
        "PATH": os.environ["PATH"],
        "MAL_LIVE_TEST": "1",
        "MAL_LIVE_PY": sys.executable,
        "MAL_PROBE_WITHDRAW_PY": str(ROOT / "tools/probe_withdraw.py"),
    }
    r = subprocess.run(["bash", str(WRAP), "--help"], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "sha256=" in r.stderr


def test_scripts_syntax_modes_and_core_dump_limit():
    for n in ("make-probe-wallet.sh", "probe-withdraw.sh", "install-probe-tools.sh"):
        sh = ROOT / "scripts/mal-fast" / n
        assert os.access(sh, os.X_OK)
        assert subprocess.run(["bash", "-n", str(sh)]).returncode == 0
    assert "ulimit -c 0" in WRAP.read_text()
    assert "RLIMIT_CORE" in (ROOT / "tools/probe_withdraw.py").read_text()
