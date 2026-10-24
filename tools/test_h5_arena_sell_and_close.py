"""Offline tests for tools/h5_arena_sell_and_close.py. The probe's recorded PumpSwap pool (Token-2022 mint) via the sell-and-close fake RPC, throwaway
Keypairs, a tmp state file. No network, no real key, nothing root, nothing sent anywhere."""
from __future__ import annotations

import argparse
import base64
import json
import os
import struct
from pathlib import Path

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from tools import h5_arena_executor as ax
from tools import h5_arena_sell_and_close as rs
from tools import h5_executor as h
from tools import probe_withdraw as pw
from tools import pumpswap_tx as tx
from tools.test_h5_sell_and_close import TOKENS, SellRpc, decode
from tools.test_probe_executor import MINT

Q50 = "h5v1q50-boostfloor-a1"
S0_WALL = 1_793_000_000_000
LATE, DEADLINE = S0_WALL + 335_000, S0_WALL + 400_000


def position(user: Pubkey, **kw) -> dict:
    pool = str(tx.canonical_pool(Pubkey.from_string(MINT)))
    pos = {"mint": MINT, "pool": pool, "base_ata": str(tx.ata(user, Pubkey.from_string(MINT), tx.TOKEN_2022_PROGRAM)), "base_tp": str(tx.TOKEN_2022_PROGRAM),
           "spend": 50_000_000, "tokens": TOKENS, "mark": 1e-9, "t_entry_ms": S0_WALL, "net_in": 49_000_000,
           "h5": {"plan": {"late_wall_ms": LATE, "deadline_wall_ms": DEADLINE, "send_wall_ms": S0_WALL + 329_500}}}
    pos.update(kw)
    return pos


def setup(tmp: Path, kp: Keypair, pos: dict | None = None, *, open_: bool = True, raw: str | None = None, **kw) -> argparse.Namespace:
    sd = tmp / "state"
    (sd / "live").mkdir(parents=True, exist_ok=True)
    st = rs.live_state_path(str(sd))
    if raw is not None:
        st.write_text(raw)
    else:
        p = pos or position(kp.pubkey())
        st.write_text(json.dumps({"open": {MINT: p} if open_ else {}, "pending": {} if open_ else {MINT: {"kind": "buy"}}}))
    keyfile = tmp / "key.json"
    keyfile.write_text(json.dumps(list(bytes(kp))))
    base = dict(book_id=Q50, mint=MINT, state_dir=str(sd), keyfile=str(keyfile), rpc_env="/none", last_level=False, emergency=False, send=False)
    return argparse.Namespace(**{**base, **kw})


def go(tmp: Path, now: int = LATE, rpc_kw=None, active=(), pin=None, pos=None, kp=None, **kw):
    kp = kp or Keypair()
    args = setup(tmp, kp, pos, **{k: v for k, v in kw.items() if k in ("open_", "raw")}, **{k: v for k, v in kw.items() if k not in ("open_", "raw")})
    rpc = SellRpc(kp.pubkey(), **(rpc_kw or {}))
    lines: list[str] = []
    rc = rs.run(args, rpc, is_active=lambda u: u in active, out=lines.append, now_ms=lambda: now, sleep=lambda s: None, check_location=False,
                sell_pin=lambda: pin)
    return rc, rpc, lines, kp


def refused(tmp, match, **kw):
    with pytest.raises(pw.Refuse, match=match):
        go(tmp, **kw)


def built_tx(rpc: SellRpc) -> VersionedTransaction:
    assert len(rpc.simulated) == 1
    return VersionedTransaction.from_bytes(base64.b64decode(rpc.simulated[0]))


def min_out_of(t: VersionedTransaction) -> tuple[int, int]:
    sells = [d for pid, d, _ in decode(t) if pid == tx.PUMPSWAP_PROGRAM]
    assert len(sells) == 1 and sells[0][:8] == tx.DISC_SELL
    return struct.unpack("<QQ", sells[0][8:24])


# --- the happy path: dry by default; full balance; sell + base-ATA close in one tx under the mint's program -------------------------------

def test_dry_run_builds_the_pinned_shape_and_sends_nothing(tmp_path):
    rc, rpc, lines, kp = go(tmp_path)
    assert rc == 0 and rpc.sent == []
    t = built_tx(rpc)
    ix = decode(t)
    amount, min_out = min_out_of(t)
    assert amount == TOKENS  # the full balance from the chain
    assert min_out >= 1
    closes = [(pid, accts) for pid, d, accts in ix if pid in (tx.TOKEN_PROGRAM, tx.TOKEN_2022_PROGRAM) and d[:1] == bytes([9])]
    assert (tx.TOKEN_2022_PROGRAM, tx.ata(kp.pubkey(), Pubkey.from_string(MINT), tx.TOKEN_2022_PROGRAM)) in [(p, a[0]) for p, a in closes]  # base ATA, mint's own program
    assert any("dry run: nothing sent" in x for x in lines)
    assert t.message.account_keys[0] == kp.pubkey()


def test_send_confirms_and_prints_the_reconcile_step(tmp_path):
    rc, rpc, lines, _ = go(tmp_path, send=True)
    assert rc == 0 and len(rpc.sent) == 1 and rpc.sent[0] == rpc.simulated[0]
    assert any(x.startswith("signature: ") for x in lines)
    assert any("--mark-closed" in x for x in lines)


def test_levels_set_min_out_from_the_executors_ladder(tmp_path):
    _, r0, _, _ = go(tmp_path / "a")
    _, r2, _, _ = go(tmp_path / "b", last_level=True)
    m0, m2 = min_out_of(built_tx(r0))[1], min_out_of(built_tx(r2))[1]
    assert m2 < m0  # 0.65 x quote vs 0.85 x quote
    assert abs(m2 / m0 - 0.65 / 0.85) < 1e-3
    _, re, _, _ = go(tmp_path / "c", emergency=True, now=DEADLINE)
    assert min_out_of(built_tx(re))[1] == h.EMERGENCY_MIN_OUT == 1


# --- the book's rule decides when -------------------------------------------------------------------------------------------------------------

def test_before_the_books_late_instant_is_refused(tmp_path):
    refused(tmp_path, "before the book's exit was late", now=LATE - 1)


def test_emergency_before_the_deadline_is_refused(tmp_path):
    refused(tmp_path, "before the book's emergency instant", emergency=True, now=DEADLINE - 1)


def test_plan_without_integer_instants_is_refused(tmp_path):
    for plan in ({}, {"late_wall_ms": LATE}, {"late_wall_ms": str(LATE), "deadline_wall_ms": DEADLINE}, {"late_wall_ms": True, "deadline_wall_ms": DEADLINE}):
        kp = Keypair()
        refused(tmp_path / str(len(str(plan))), "no integer", kp=kp, pos=position(kp.pubkey(), h5={"plan": plan}))


# --- every other refusal path -------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("unit", rs.UNITS)
def test_any_unit_not_stopped_refuses(tmp_path, unit):
    refused(tmp_path, f"{unit} is not stopped", active=(unit,))


def test_unknown_book_and_bad_mint(tmp_path):
    refused(tmp_path, "--book-id must be one of", book_id="h5v1-boostfloor-a1")
    refused(tmp_path, "not a valid base58", mint="abc")


def test_level_flags_are_exclusive(tmp_path):
    refused(tmp_path, "exclusive", emergency=True, last_level=True, now=DEADLINE)


def test_sell_pin_mismatch_refuses_before_anything_is_read(tmp_path):
    with pytest.raises(pw.Refuse, match="pinned sell builder differs"):
        go(tmp_path, pin="sell_builder_pin_mismatch:symbol_sha256")


def test_real_sell_pin_is_clean_at_this_head():
    assert ax.sell_pin_problem() is None


def test_state_problems_refuse(tmp_path):
    refused(tmp_path / "a", "not an open position", open_=False)
    refused(tmp_path / "b", "cannot be read as JSON", raw="{not json")
    kp = Keypair()
    args = setup(tmp_path / "c", kp)
    st = rs.live_state_path(args.state_dir)
    real = st.with_name("real.json")
    st.rename(real)
    os.symlink(real, st)
    with pytest.raises(pw.Refuse, match="cannot be opened"):
        rs.run(args, SellRpc(kp.pubkey()), is_active=lambda u: False, out=lambda s: None, now_ms=lambda: LATE, check_location=False, sell_pin=lambda: None)


def test_pending_record_is_named(tmp_path):
    kp = Keypair()
    args = setup(tmp_path, kp, open_=False)
    with pytest.raises(pw.Refuse, match="PENDING"):
        rs.run(args, SellRpc(kp.pubkey()), is_active=lambda u: False, out=lambda s: None, now_ms=lambda: LATE, check_location=False, sell_pin=lambda: None)


def test_another_wallets_position_is_refused(tmp_path):
    other = Keypair()
    refused(tmp_path, "another wallet", pos=position(other.pubkey()))


def test_wrong_pool_is_refused(tmp_path):
    kp = Keypair()
    refused(tmp_path, "not the mint's canonical pool|not usable", kp=kp, pos=position(kp.pubkey(), pool=str(Keypair().pubkey())))


def test_no_balance_is_refused(tmp_path):
    refused(tmp_path, "no token balance", rpc_kw={"tokens": 0, "token_acct": True})
    refused(tmp_path / "x", "no token balance", rpc_kw={"tokens": 0, "token_acct": False})


def test_simulate_error_sends_nothing(tmp_path):
    kp = Keypair()
    args = setup(tmp_path, kp, send=True)
    rpc = SellRpc(kp.pubkey(), sim_err={"InstructionError": [3, {"Custom": 6004}]})
    with pytest.raises(pw.Refuse, match="simulation failed"):
        rs.run(args, rpc, is_active=lambda u: False, out=lambda s: None, now_ms=lambda: LATE, check_location=False, sell_pin=lambda: None)
    assert rpc.sent == []


def test_failed_send_is_reported(tmp_path):
    kp = Keypair()
    args = setup(tmp_path, kp, send=True)
    rpc = SellRpc(kp.pubkey())
    rpc.send_err = True
    with pytest.raises(pw.Refuse, match="failed"):
        rs.run(args, rpc, is_active=lambda u: False, out=lambda s: None, now_ms=lambda: LATE, sleep=lambda s: None, check_location=False, sell_pin=lambda: None)


def test_key_location_is_checked_by_default(tmp_path):
    kp = Keypair()
    args = setup(tmp_path, kp)
    with pytest.raises(pw.Refuse, match="must be root:root 0700|root:root 0400|cannot stat"):
        rs.run(args, SellRpc(kp.pubkey()), is_active=lambda u: False, out=lambda s: None, now_ms=lambda: LATE, sell_pin=lambda: None)


def test_main_refuses_non_root(monkeypatch, capsys):
    monkeypatch.delenv("MAL_LIVE_TEST", raising=False)
    if os.geteuid() == 0:
        pytest.skip("running as root")
    assert rs.main(["--book-id", Q50, "--mint", MINT]) == 1
    assert "root-only" in capsys.readouterr().err


def test_runs_as_a_file_from_the_pinned_root_without_tools_on_the_path(tmp_path):
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-I", "-B", str(Path(rs.__file__)), "--help"], capture_output=True, text=True, cwd="/", timeout=120)
    assert r.returncode == 0, r.stderr
    assert "--book-id" in r.stdout
