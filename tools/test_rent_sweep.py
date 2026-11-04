"""Offline tests for tools/rent_sweep.py: no network, no key."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from solders.hash import Hash
from solders.keypair import Keypair

from tools import pumpswap_tx as tx
from tools import rent_sweep as rs

OWNER = str(Keypair().pubkey())
OTHER = str(Keypair().pubkey())
RENT = 2_039_280


def mint() -> str:
    return str(Keypair().pubkey())


def acct(*, amount="0", m=None, program=tx.TOKEN_PROGRAM, owner=OWNER, delegate=None, close_auth=None, native=False, state="initialized",
         extensions=None, lamports=RENT):
    info = {"mint": m or mint(), "owner": owner, "state": state, "isNative": native, "tokenAmount": {"amount": amount}}
    if delegate:
        info["delegate"] = delegate
    if close_auth:
        info["closeAuthority"] = close_auth
    if extensions is not None:
        info["extensions"] = extensions
    return {"pubkey": str(Keypair().pubkey()), "account": {"lamports": lamports, "owner": str(program), "data": {"parsed": {"info": info}}}}


FEE = lambda w: [{"extension": "transferFeeAmount", "state": {"withheldAmount": w}}]  # noqa: E731


@pytest.mark.parametrize("kw,reason", [
    ({}, "zero_balance"),
    ({"amount": "5"}, "nonzero_balance"),
    ({"native": True}, "native_wsol"),
    ({"state": "frozen"}, "state_frozen"),
    ({"delegate": OTHER}, "delegate_set"),
    ({"close_auth": OTHER}, "close_authority_not_owner"),
    ({"program": tx.TOKEN_2022_PROGRAM, "extensions": FEE(7)}, "withheld_transfer_fee"),
    ({"program": tx.TOKEN_2022_PROGRAM, "extensions": FEE(0)}, "zero_balance"),
    ({"delegate": OWNER, "close_auth": OWNER}, "zero_balance"),
    ({"owner": OTHER}, "owner_mismatch"),
])
def test_classify(kw, reason):
    r = rs.classify(acct(**kw), OWNER, set())
    assert r["reason"] == reason and r["closable"] == (reason == "zero_balance")


def test_classify_program_and_excluded():
    assert rs.classify(acct(program=tx.TOKEN_2022_PROGRAM), OWNER, set())["program"] == "token2022"
    m = mint()
    r = rs.classify(acct(m=m), OWNER, {m})
    assert not r["closable"] and r["reason"] == "excluded_mint"
    assert rs.classify(acct(amount="x"), OWNER, set())["reason"] == "amount_unreadable"


def test_exclude_file_and_summary(tmp_path):
    m = mint()
    p = tmp_path / "ex.txt"
    p.write_text(f"# c\n{m}  # note\n\n")
    assert rs.read_exclude(str(p)) == {m}
    rows = [rs.classify(acct(), OWNER, set()), rs.classify(acct(amount="1"), OWNER, set())]
    s = rs.summarize(rows)
    assert s["accounts"] == 2 and s["closable"] == 1 and s["closable_lamports"] == RENT and s["total_lamports"] == 2 * RENT


def test_batching():
    assert [len(b) for b in rs.batches(list(range(19)), 8)] == [8, 8, 3]
    assert [len(b) for b in rs.batches(list(range(19)), 50)] == [8, 8, 3]  # cap at 8
    assert [len(b) for b in rs.batches(list(range(3)), 0)] == [1, 1, 1]
    assert rs.batches([], 8) == []


@pytest.mark.parametrize("program,expected_pid", [(tx.TOKEN_PROGRAM, tx.TOKEN_PROGRAM), (tx.TOKEN_2022_PROGRAM, tx.TOKEN_2022_PROGRAM)])
def test_close_ix_layout(program, expected_pid):
    a, o = Keypair().pubkey(), Keypair().pubkey()
    ix = rs.close_ix(a, o, program)
    assert ix.program_id == expected_pid and bytes(ix.data) == bytes([9])
    metas = [(m.pubkey, m.is_signer, m.is_writable) for m in ix.accounts]
    assert metas == [(a, False, True), (o, False, True), (o, True, False)]  # account, destination = owner, owner signer


def test_close_ix_tokenkeg_matches_pumpswap_tx():
    a, o = Keypair().pubkey(), Keypair().pubkey()
    assert rs.close_ix(a, o) == tx.close_account(a, o, o)


# --- sweep ----------------------------------------------------------------------------------------------------------------------


class FakeRpc:
    def __init__(self, accounts, sim_err=None, status="confirmed"):
        self.accounts = {a["pubkey"]: a for a in accounts}
        self.calls = []
        self.sim_err = sim_err
        self.status = status
        self.sent = 0

    def __call__(self, method, params):
        self.calls.append(method)
        if method == "getTokenAccountsByOwner":
            pid = params[1]["programId"]
            return {"value": [a for a in self.accounts.values() if a["account"]["owner"] == pid]}
        if method == "getMultipleAccounts":
            return {"value": [(self.accounts[k]["account"] if k in self.accounts else None) for k in params[0]]}
        if method == "getLatestBlockhash":
            return {"value": {"blockhash": str(Hash.default()), "lastValidBlockHeight": 1}}
        if method == "simulateTransaction":
            return {"value": {"err": self.sim_err, "logs": [], "unitsConsumed": 1}}
        if method == "sendTransaction":
            self.sent += 1
            return "sig" + str(self.sent)
        if method == "getSignatureStatuses":
            return {"value": [{"err": None, "confirmationStatus": self.status}]}
        if method == "getTransaction":
            return {"meta": {"preBalances": [10_000], "postBalances": [10_000 + RENT - 5_000], "fee": 5_000}}
        raise AssertionError(method)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "WALLET_STOP", tmp_path / "wstop")
    monkeypatch.setattr(rs, "WALLET_HALT", tmp_path / "whalt")
    led = tmp_path / "ledger.jsonl"
    led.write_text("")
    cfg = {"owner": OWNER, "ledger_paths": [str(led)], "stop_paths": [str(tmp_path / "lane-stop")], "log_path": str(tmp_path / "log.jsonl"),
           "max_per_tx": 8}
    return cfg, tmp_path, led


def row(ev, m, bot="b1", **kw):
    return json.dumps({"ev": ev, "mint": m, "bot_id": bot, **kw}) + "\n"


def test_dry_never_signs_or_sends(env):
    cfg, _, _ = env
    rpc = FakeRpc([acct() for _ in range(10)] + [acct(amount="3")])
    log = rs.run_sweep(rpc, cfg, live=False, keypair=None)
    assert [r["ev"] for r in log] == ["dry_simulated", "dry_simulated"]
    assert [len(r["accounts"]) for r in log] == [8, 2]
    assert "sendTransaction" not in rpc.calls and "getLatestBlockhash" in rpc.calls
    assert all(r["live"] is False for r in log)
    assert len(Path(cfg["log_path"]).read_text().splitlines()) == 2


def test_dry_keypair_never_touched(env):
    class Boom:
        def pubkey(self):
            raise AssertionError("key used in dry mode")
    cfg, _, _ = env
    rs.run_sweep(FakeRpc([acct()]), cfg, live=False, keypair=Boom())


def test_live_sends_and_records_recovered(env):
    cfg, _, _ = env
    kp = Keypair()
    cfg["owner"] = str(kp.pubkey())
    rpc = FakeRpc([acct(owner=cfg["owner"])])
    log = rs.run_sweep(rpc, cfg, live=True, keypair=kp, sleep=lambda s: None)
    assert rpc.sent == 1 and log[0]["ev"] == "sent" and log[0]["status"] == "confirmed" and log[0]["recovered_lamports"] == RENT
    assert log[0]["sig"] == "sig1" and len(log[0]["accounts"]) == 1


def test_live_wrong_keypair_refused(env):
    cfg, _, _ = env
    rpc = FakeRpc([acct()])
    with pytest.raises(rs.Refused, match="keypair_not_owner"):
        rs.run_sweep(rpc, cfg, live=True, keypair=Keypair())
    assert rpc.calls == []


def test_live_ok_file_required(env):
    cfg, tmp, _ = env
    kp = Keypair()
    cfg["owner"] = str(kp.pubkey())
    cfg["live_ok_path"] = str(tmp / "LIVE_OK")
    with pytest.raises(rs.Refused, match="live_ok_missing"):
        rs.run_sweep(FakeRpc([]), cfg, live=True, keypair=kp)


def test_unconfirmed_stops_after_first_batch(env, monkeypatch):
    cfg, _, _ = env
    kp = Keypair()
    cfg["owner"] = str(kp.pubkey())
    cfg["max_per_tx"] = 1
    rpc = FakeRpc([acct(owner=cfg["owner"]) for _ in range(3)], status="processed")
    monkeypatch.setattr(rs, "CONFIRM_TIMEOUT_S", 0.0)
    log = rs.run_sweep(rpc, cfg, live=True, keypair=kp, sleep=lambda s: None)
    assert rpc.sent == 1 and log[0]["status"] == "unconfirmed"


def test_simulation_failure_not_sent(env):
    cfg, _, _ = env
    kp = Keypair()
    cfg["owner"] = str(kp.pubkey())
    rpc = FakeRpc([acct(owner=cfg["owner"])], sim_err={"InstructionError": [1, "Custom"]})
    log = rs.run_sweep(rpc, cfg, live=True, keypair=kp)
    assert log[0]["ev"] == "simulate_failed" and rpc.sent == 0


@pytest.mark.parametrize("which", ["lane-stop", "wstop", "whalt"])
def test_refuse_on_stop_or_halt(env, which):
    cfg, tmp, _ = env
    (tmp / which).write_text("x")
    rpc = FakeRpc([acct()])
    with pytest.raises(rs.Refused, match="stop_file_present"):
        rs.run_sweep(rpc, cfg, live=False)
    assert rpc.calls == []


def test_refuse_when_stop_path_unreadable(env, monkeypatch):
    cfg, _, _ = env
    def bad(p):
        raise PermissionError
    monkeypatch.setattr(rs.os, "stat", bad)
    with pytest.raises(rs.Refused, match="stop_file_present"):
        rs.run_sweep(FakeRpc([]), cfg, live=False)


def test_open_position_mint_is_skipped(env):
    cfg, _, led = env
    held, free = mint(), mint()
    led.write_text(row("send_intent", held) + row("fill", held, ok=True) + row("send_intent", free) + row("fill", free, ok=True) + row("exit", free))
    rpc = FakeRpc([acct(m=held), acct(m=free)])
    log = rs.run_sweep(rpc, cfg, live=False)
    assert [r["ev"] for r in log] == ["skip_open_position", "dry_simulated"]
    assert log[0]["mint"] == held and len(log[1]["accounts"]) == 1


def test_open_mints_fold(env):
    cfg, _, led = env
    a, b, c, d = mint(), mint(), mint(), mint()
    led.write_text(row("start", a) .replace(f'"mint": "{a}", ', "") + row("send_intent", a) + row("send_intent", b) + row("fill", b, ok=False)
                   + row("send_intent", c) + row("fill", c, ok=True) + row("writeoff", c) + row("send_intent", d) + row("fee", d))
    assert rs.open_mints(cfg["ledger_paths"]) == {a, d}


@pytest.mark.parametrize("content", ["{not json\n", '["x"]\n', '{"ev": "fill"}\n'])
def test_unreadable_ledger_fails_closed(env, content):
    cfg, _, led = env
    led.write_text(content)
    rpc = FakeRpc([acct()])
    with pytest.raises(rs.Refused, match="ledger"):
        rs.run_sweep(rpc, cfg, live=False)
    assert rpc.calls == []


def test_missing_ledger_and_backlog_and_unconfigured(env):
    cfg, tmp, led = env
    led.unlink()
    with pytest.raises(rs.Refused, match="ledger_unreadable"):
        rs.run_sweep(FakeRpc([]), cfg, live=False)
    led.write_text("")
    (tmp / "ledger.jsonl.backlog.b1").write_text(row("fill", mint()))
    with pytest.raises(rs.Refused, match="backlog"):
        rs.run_sweep(FakeRpc([]), cfg, live=False)
    cfg["ledger_paths"] = []
    with pytest.raises(rs.Refused, match="no_ledger"):
        rs.run_sweep(FakeRpc([]), cfg, live=False)


def test_recheck_drops_account_that_got_balance(env):
    cfg, _, _ = env
    a = acct()
    rpc = FakeRpc([a])
    orig = rpc.__call__

    class Flip(FakeRpc):
        def __call__(self, method, params):
            if method == "getMultipleAccounts":  # a buy landed between list and build
                self.accounts[a["pubkey"]] = acct(amount="9")  | {"pubkey": a["pubkey"]}
            return super().__call__(method, params)
    rpc = Flip([a])
    log = rs.run_sweep(rpc, cfg, live=False)
    assert [r["ev"] for r in log] == ["batch_empty_after_recheck"] and "simulateTransaction" not in rpc.calls
    assert orig  # keep flake8 quiet


def test_message_has_close_ixs_both_programs():
    o = Keypair().pubkey()
    rows = [{"address": str(Keypair().pubkey()), "program": "tokenkeg"}, {"address": str(Keypair().pubkey()), "program": "token2022"}]
    msg = rs.build_message(rows, o, Hash.default())
    pids = [str(msg.account_keys[i.program_id_index]) for i in msg.instructions]
    assert pids[1:] == [str(tx.TOKEN_PROGRAM), str(tx.TOKEN_2022_PROGRAM)]
    assert all(bytes(i.data) == bytes([9]) for i in msg.instructions[1:])


def test_config_validation(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"owner": OWNER, "ledger_paths": ["/x"], "log_path": "/y", "max_per_tx": 99}))
    assert rs.load_config(str(p))["max_per_tx"] == 8
    p.write_text(json.dumps({"owner": OWNER, "ledger_paths": ["/x"], "log_path": "/y", "keypair": "/k"}))
    with pytest.raises(SystemExit):
        rs.load_config(str(p))
    p.write_text(json.dumps({"owner": OWNER, "log_path": "/y"}))
    with pytest.raises(SystemExit):
        rs.load_config(str(p))


def test_list_cli(monkeypatch, capsys):
    rpc = FakeRpc([acct(), acct(amount="4")])
    import tools.pumpswap_simulate as sim
    monkeypatch.setattr(sim, "Rpc", lambda url: rpc)
    assert rs.main(["list", "--owner", OWNER]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3 and json.loads(lines[-1])["closable"] == 1
