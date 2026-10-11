"""Offline tests for tools/c1nf_sell_and_close.py (DEC-026 section 7 rule 4): C1-NF custody on top of H5's sell-and-close code. The fake RPC and
throwaway Keypairs are test_h5_sell_and_close's. No network, no real key, nothing root."""
from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

import pytest
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from tools import c1nf_sell_and_close as cs
from tools import h5_sell_and_close as sc
from tools import probe_withdraw as pw
from tools.test_h5_sell_and_close import TOKENS, SellRpc, make_args, sell_args, sim_tx

ROOT = Path(__file__).resolve().parent.parent


def run(tmp: Path, kp: Keypair | None = None, pinned: str | None = "", active=lambda u: False, **kw):
    kp = kp or Keypair()
    rpc = SellRpc(kp.pubkey())
    lines: list[str] = []
    args = make_args(tmp, kp, **kw)
    rc = cs.run(args, rpc, pinned_wallet=str(kp.pubkey()) if pinned == "" else pinned, is_active=active, out=lines.append,
                sleep=lambda s: None, check_location=False)
    return rc, rpc, lines, kp


def test_dry_run_and_send_reuse_h5s_plan(tmp_path):
    rc, rpc, lines, kp = run(tmp_path / "a")
    assert rc == 0 and rpc.sent == [] and any("dry run: nothing sent" in x for x in lines)
    amount, min_out = sell_args(sim_tx(rpc))
    assert amount == TOKENS and min_out >= 1
    assert lines[0] == f"wallet:  {kp.pubkey()} (C1-NF)"
    rc, rpc, lines, kp = run(tmp_path / "b", send=True)
    sig = str(VersionedTransaction.from_bytes(base64.b64decode(rpc.sent[0])).signatures[0])
    assert rc == 0 and len(rpc.sent) == 1 and f"signature: {sig}" in lines
    assert any(x.startswith("next:") and "--mark-closed" in x and f"--sig {sig}" in x for x in lines)


def test_guard_is_the_c1nf_unit_only(tmp_path):
    asked: list[str] = []

    def active(unit):
        asked.append(unit)
        return False

    run(tmp_path / "a", active=active)
    assert asked == ["mal-c1nf-executor"]  # H5's units never hold this key and are not looked at
    with pytest.raises(pw.Refuse, match="mal-c1nf-executor is not stopped"):
        run(tmp_path / "b", active=lambda u: u == "mal-c1nf-executor")
    rc, *_ = run(tmp_path / "c", active=lambda u: u in sc.UNITS)  # H5 running does not block a C1-NF rescue
    assert rc == 0


def test_refuses_h5s_wallet_and_any_unpinned_key_before_any_chain_read(tmp_path):
    kp = Keypair()
    for pinned, msg in ((None, "no C1-NF wallet is pinned"), (str(Keypair().pubkey()), "not the C1-NF wallet pinned")):
        rpc = SellRpc(kp.pubkey())
        with pytest.raises(pw.Refuse, match=msg):
            cs.run(make_args(tmp_path / "x", kp), rpc, pinned_wallet=pinned, is_active=lambda u: False, out=lambda s: None, check_location=False)
        assert rpc.calls == [] and rpc.simulated == []  # refused before the plan read anything
    assert "H5's wallet" in (cs.wallet_refusal(cs.H5_WALLET_PUBKEY, cs.H5_WALLET_PUBKEY) or "")


def test_no_keyfile_option_and_the_key_path_is_c1nfs():
    with pytest.raises(SystemExit):
        cs.build_parser().parse_args(["--mint", "x", "--keyfile", "/etc/mal-probe/probe-wallet.json"])
    assert cs.KEYFILE == "/etc/mal-c1nf-key/c1nf-wallet.json" and cs.KEYFILE != sc.DEFAULT_KEYFILE
    src = (ROOT / "tools/c1nf_sell_and_close.py").read_text()
    assert "args.keyfile = KEYFILE" in src and "probe-wallet" not in src


def test_h5_wallet_constant_matches_the_executor_once_it_is_here():
    p = ROOT / "tools/c1nf_executor.py"
    if not p.is_file():
        pytest.skip("tools/c1nf_executor.py is not on this branch yet (executor PR claude/c1nf-executor-v2)")
    from tools import c1nf_executor as cx
    assert cs.H5_WALLET_PUBKEY == cx.H5_WALLET_PUBKEY
    assert cs.pinned_c1nf_wallet() == (cx.C1NF_WALLET_PUBKEY or None)


def test_not_root_refuses():
    r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); from tools import c1nf_sell_and_close as cs; "
                        "raise SystemExit(cs.main(['--mint', 'x']))", str(ROOT)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 1 and "root-only" in r.stderr
