"""Tests for tools/boost_watch.py. No network. The GlobalConfig account is the recorded mainnet account (tools/fixtures/boost_watch/, copied from
the 2026-10-08 pump_structure_monitor recording, boost_enabled true). Transactions are CONSTRUCTED (no toggle_boost tx was recorded)."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from tools import boost_watch as bw
from tools import pump_structure_monitor as m

FX = Path(__file__).parent / "fixtures" / "boost_watch"
ACC = json.loads((FX / "global_config_account.json").read_text())["value"]
RAW = base64.b64decode(ACC["data"][0])
WATCHED = bw.load_idl_discriminators(bw.DEFAULT_IDL) if Path(bw.DEFAULT_IDL).exists() else dict(bw.PINNED)
ADMIN = m.b58encode(RAW[8:40])
KEEPER = "HTVZVEQMBsNanubDPTs3CxDAEGNFQHJY8c1441iy2S5r"
TOGGLE = bytes([117, 161, 160, 74, 223, 137, 118, 99])


def acct(raw: bytes) -> dict:
    return {"value": {"data": [base64.b64encode(raw).decode(), "base64"]}}


def with_flag(v: int) -> bytes:
    b = bytearray(RAW)
    b[939] = v
    return bytes(b)


def tx(data: bytes, inner: bool = False, program: str = bw.PROGRAM) -> dict:
    ix = {"programIdIndex": 1, "accounts": [0], "data": m.b58encode(data)}
    hdr = {"numRequiredSignatures": 1, "numReadonlySignedAccounts": 0, "numReadonlyUnsignedAccounts": 1}
    return {"slot": 5, "transaction": {"message": {"header": hdr, "accountKeys": [ADMIN, program], "instructions": [] if inner else [ix]}},
            "meta": {"err": None, "innerInstructions": [{"index": 0, "instructions": [ix]}] if inner else []}}


class Fake:
    def __init__(self, account=RAW, sigs=None, txs=None, fail=False):
        self.account, self.sigs, self.txs, self.fail, self.log, self.calls = account, sigs or {}, txs or {}, fail, [], 0

    def call(self, method, params):
        self.log.append(method)
        self.calls += 1
        if self.fail:
            raise m.RpcUnreachable("down")
        if method == "getAccountInfo":
            return acct(self.account)
        if method == "getSignaturesForAddress":
            return [{"signature": s} for s in self.sigs.get(params[0], [])]
        if method == "getTransaction":
            return self.txs.get(params[0])
        raise AssertionError(method)


def args(tmp_path, *extra):
    return ["--log-dir", str(tmp_path), "--idl", bw.DEFAULT_IDL, *extra]


def lines(tmp_path):
    return [json.loads(x) for x in (tmp_path / "boost_watch.log").read_text().splitlines()]


def test_fixture_decodes():
    c = bw.decode_config(RAW)
    assert c["layout_ok"] and c["boost_enabled"] is True and c["boost_authority"] == KEEPER and len(c["admin"]) >= 32


def test_idl_discriminators_match_pins():
    assert bw.PINNED.items() <= WATCHED.items()


def test_baseline_then_ok(tmp_path):
    f = Fake(sigs={bw.CONFIG_ADDRESS: ["a"], ADMIN: ["b"], KEEPER: ["c"]})
    assert bw.run(args(tmp_path, "--max-polls", "2", "--loop", "--interval", "0"), client=f, sleep=lambda s: None) == 0
    ls = lines(tmp_path)
    assert [x["status"] for x in ls] == ["OK", "OK"] and ls[0]["baseline"] == "set" and ls[1]["baseline"] == "kept" and ls[0]["boost_enabled"] is True


def test_boost_off_exits_3_and_latches(tmp_path):
    assert bw.run(args(tmp_path), client=Fake(), sleep=lambda s: None) == 0
    assert bw.run(args(tmp_path), client=Fake(account=with_flag(0))) == 3
    last = lines(tmp_path)[-1]
    assert last["boost_enabled"] is False and "boost_enabled_not_true" in last["change"] and "sha256_changed" in last["change"]
    # flag restored, but the alert stays latched until --ack
    assert bw.run(args(tmp_path), client=Fake()) == 3
    assert "unacked_alert" in lines(tmp_path)[-1]["change"]
    assert bw.run(args(tmp_path, "--ack")) == 0
    assert bw.run(args(tmp_path), client=Fake()) == 0


def test_already_off_at_first_run_alerts(tmp_path):
    assert bw.run(args(tmp_path), client=Fake(account=with_flag(0))) == 3


def test_toggle_tx_top_level_and_inner(tmp_path):
    for inner in (False, True):
        d = tmp_path / str(inner)
        base = Fake(sigs={bw.CONFIG_ADDRESS: ["a"], ADMIN: ["b"], KEEPER: ["c"]})
        assert bw.run(args(d), client=base) == 0
        f = Fake(sigs={bw.CONFIG_ADDRESS: ["a"], ADMIN: ["new", "b"], KEEPER: ["c"]}, txs={"new": tx(TOGGLE + b"\x00", inner)})
        assert bw.run(args(d), client=f) == 3
        last = lines(d)[-1]
        assert "watched_ix:toggle_boost" in last["change"] and last["new_txs"][0]["hits"][0]["enabled"] is False


def test_unrelated_admin_tx_is_logged_not_alerted(tmp_path):
    assert bw.run(args(tmp_path), client=Fake(sigs={ADMIN: ["b"]})) == 0
    f = Fake(sigs={ADMIN: ["x", "b"]}, txs={"x": tx(b"\x01" * 16)})
    assert bw.run(args(tmp_path), client=f) == 0
    assert lines(tmp_path)[-1]["new_txs"][0]["sig"] == "x"


def test_other_program_with_same_bytes_ignored():
    assert bw.scan_tx(tx(TOGGLE + b"\x00", program=m.PUMP_PROGRAM), WATCHED) == []


def cfg_tx(writable: bool, loaded: bool = False) -> dict:
    """Constructed in the shape of a PumpSwap swap: payer (signer, writable), config, program (readonly unsigned). Static layout puts config
    at index 1; writable=True moves it into the writable-unsigned region (header readonly-unsigned count 1 = program only)."""
    t = tx(b"\x02" * 9)
    msg = t["transaction"]["message"]
    if loaded:
        msg["accountKeys"] = [ADMIN, bw.PROGRAM]
        msg["header"]["numReadonlyUnsignedAccounts"] = 1
        t["meta"]["loadedAddresses"] = {"writable": [bw.CONFIG_ADDRESS] if writable else [], "readonly": [] if writable else [bw.CONFIG_ADDRESS]}
    else:
        msg["accountKeys"] = [ADMIN, bw.CONFIG_ADDRESS, bw.PROGRAM] if writable else [ADMIN, bw.PROGRAM, bw.CONFIG_ADDRESS]
        msg["header"]["numReadonlyUnsignedAccounts"] = 1 if writable else 2
        msg["instructions"][0]["programIdIndex"] = 2 if writable else 1
    return t


def test_writable_keys():
    assert bw.CONFIG_ADDRESS in bw.writable_keys(cfg_tx(True)) and bw.CONFIG_ADDRESS not in bw.writable_keys(cfg_tx(False))
    assert bw.CONFIG_ADDRESS in bw.writable_keys(cfg_tx(True, loaded=True)) and bw.CONFIG_ADDRESS not in bw.writable_keys(cfg_tx(False, loaded=True))


@pytest.mark.parametrize("loaded", [False, True])
def test_readonly_swap_mention_does_not_alert(tmp_path, loaded):
    d = tmp_path / str(loaded)
    assert bw.run(args(d), client=Fake(sigs={bw.CONFIG_ADDRESS: ["a"]})) == 0
    f = Fake(sigs={bw.CONFIG_ADDRESS: ["s2", "s1", "a"]}, txs={"s1": cfg_tx(False, loaded), "s2": cfg_tx(False, loaded)})
    assert bw.run(args(d), client=f) == 0
    last = lines(d)[-1]
    assert last["status"] == "OK" and len(last["new_txs"]) == 2


def test_config_fetch_is_capped(tmp_path):
    assert bw.run(args(tmp_path), client=Fake(sigs={bw.CONFIG_ADDRESS: ["a"]})) == 0
    ids = [f"s{i}" for i in range(20)]
    f = Fake(sigs={bw.CONFIG_ADDRESS: ids[::-1] + ["a"]}, txs={i: cfg_tx(False) for i in ids})
    assert bw.run(args(tmp_path), client=f) == 0
    assert f.log.count("getTransaction") == bw.MAX_TX_FETCH


@pytest.mark.parametrize("loaded", [False, True])
def test_new_config_write_alerts(tmp_path, loaded):
    assert bw.run(args(tmp_path), client=Fake(sigs={bw.CONFIG_ADDRESS: ["a"]})) == 0
    f = Fake(sigs={bw.CONFIG_ADDRESS: ["z", "a"]}, txs={"z": cfg_tx(True, loaded)})
    assert bw.run(args(tmp_path), client=f) == 3
    assert "config_account_tx" in lines(tmp_path)[-1]["change"]


def test_unfetchable_tx_retried_not_lost(tmp_path):
    assert bw.run(args(tmp_path), client=Fake(sigs={ADMIN: ["b"]})) == 0
    assert bw.run(args(tmp_path), client=Fake(sigs={ADMIN: ["x", "b"]})) == 0  # tx None: skipped
    f = Fake(sigs={ADMIN: ["x", "b"]}, txs={"x": tx(TOGGLE + b"\x00")})
    assert bw.run(args(tmp_path), client=f) == 3


def test_authority_change_alerts(tmp_path):
    assert bw.run(args(tmp_path), client=Fake()) == 0
    b = bytearray(RAW)
    b[907:939] = bytes(range(32))
    assert bw.run(args(tmp_path), client=Fake(account=bytes(b))) == 3
    assert "boost_authority_changed" in lines(tmp_path)[-1]["change"]


def test_bad_layout_alerts(tmp_path):
    assert bw.run(args(tmp_path), client=Fake(account=RAW + b"\0")) == 3


def test_read_failures_exit_4_not_3(tmp_path):
    rc = bw.run(args(tmp_path, "--loop", "--interval", "0", "--max-fail", "3"), client=Fake(fail=True), sleep=lambda s: None)
    assert rc == 4 and [x["status"] for x in lines(tmp_path)] == ["READ_FAIL"] * 3


def test_log_has_no_secret_fields(tmp_path):
    bw.run(args(tmp_path), client=Fake())
    assert set(lines(tmp_path)[0]) == {"schema", "utc", "boost_enabled", "config_sha256", "admin", "boost_authority", "layout_ok", "baseline",
                                       "new_txs", "change", "status", "rpc", "calls_per_poll"}


# ---- RPC client: backoff, budget, key hygiene (fake transport, no network) ----
SECRET = "SEKRET-KEY-12345"


def transport_script(statuses, ok_result=None):
    """Fake transport returning scripted HTTP statuses, then a 200 with ok_result. Answers getSignaturesForAddress with an empty list."""
    seq = list(statuses)

    def t(url, body, timeout):
        if json.loads(body)["method"] == "getSignaturesForAddress" and not seq:
            return 200, {}, json.dumps({"result": []}).encode()
        st = seq.pop(0) if seq else 200
        return st, {}, json.dumps({"jsonrpc": "2.0", "id": 1, "result": ok_result}).encode()
    return t


ACCOUNT_OK = {"value": {"data": [ACC["data"][0], "base64"]}}


def test_429_then_success_is_not_a_fail():
    sleeps = []
    c = bw.WatchRpc("https://x/?k=" + SECRET, transport=transport_script([429, 503], {"value": 1}), sleep=sleeps.append, rand=lambda: 0.5)
    assert c.call("getAccountInfo", ["a"]) == {"value": 1}
    assert c.calls == 3 and sleeps == [1.0, 2.0]  # exponential: base*2^(n-1), jitter factor 0.5+0.5 = 1


def test_backoff_is_exponential_with_jitter_and_capped():
    sleeps = []
    c = bw.WatchRpc("u", retries=6, cap=5.0, transport=transport_script([429] * 7), sleep=sleeps.append, rand=lambda: 1.0)
    with pytest.raises(m.RpcUnreachable):
        c.call("x", [])
    assert sleeps == [1.5, 3.0, 6.0, 7.5, 7.5, 7.5] and c.calls == 7


def test_429_recovered_inside_poll_counts_no_fail_in_run(tmp_path):
    c = bw.WatchRpc("https://x/?k=" + SECRET, transport=transport_script([429], ACCOUNT_OK), sleep=lambda s: None)
    assert bw.run(args(tmp_path, "--max-fail", "1"), client=c) == 0
    last = lines(tmp_path)[-1]
    assert last["status"] == "OK" and last["rpc"] == "public"
    assert last["calls_per_poll"] == 5  # 1 retry + 1 account + 3 signature calls (no new txs on the first poll)


def test_exhausted_retries_count_one_fail(tmp_path):
    c = bw.WatchRpc("https://x/?k=" + SECRET, retries=2, transport=transport_script([429] * 9), sleep=lambda s: None)
    assert bw.run(args(tmp_path, "--max-fail", "1"), client=c) == 4
    assert lines(tmp_path)[-1]["status"] == "READ_FAIL"


def test_key_never_in_log_stdout_or_repr(tmp_path, capsys):
    env = tmp_path / "helius.env"
    env.write_text(f"# comment\nHELIUS_API_KEY={SECRET}\n")
    url = bw.rpc_from_env_file(env)
    assert url.endswith(SECRET)
    c = bw.WatchRpc(url, retries=1, transport=transport_script([429] * 9), sleep=lambda s: None)
    assert SECRET not in repr(c)
    assert bw.run(args(tmp_path, "--rpc-env", str(env), "--max-fail", "1"), client=c) == 4
    out = capsys.readouterr()
    log = (tmp_path / "boost_watch.log").read_text()
    assert SECRET not in log + out.out + out.err and '"rpc": "helius"' in log


def test_key_not_logged_on_success(tmp_path, capsys):
    env = tmp_path / "helius.env"
    env.write_text(f"HELIUS_API_KEY={SECRET}\n")
    c = bw.WatchRpc(bw.rpc_from_env_file(env), transport=transport_script([], ACCOUNT_OK), sleep=lambda s: None)
    assert bw.run(args(tmp_path, "--rpc-env", str(env)), client=c) == 0
    assert SECRET not in (tmp_path / "boost_watch.log").read_text() + (tmp_path / "state.json").read_text()


def test_exception_text_has_no_url():
    c = bw.WatchRpc("https://x/?api-key=" + SECRET, retries=0, transport=transport_script([500]), sleep=lambda s: None)
    with pytest.raises(m.RpcUnreachable) as e:
        c.call("getAccountInfo", [])
    assert SECRET not in str(e.value)


def test_env_file_url_form_and_bad_file(tmp_path, capsys):
    f = tmp_path / "e"
    f.write_text("export RPC_URL='https://h.example/?api-key=" + SECRET + "'\n")
    assert bw.rpc_from_env_file(f) == "https://h.example/?api-key=" + SECRET
    f.write_text("NOTHING=1\n")
    with pytest.raises(ValueError):
        bw.rpc_from_env_file(f)
    assert bw.run(args(tmp_path, "--rpc-env", str(f))) == 2
    assert SECRET not in capsys.readouterr().err


def test_admin_backlog_deferred_not_lost(tmp_path):
    assert bw.run(args(tmp_path), client=Fake(sigs={ADMIN: ["b"]})) == 0
    ids = [f"a{i}" for i in range(5)]
    f = Fake(sigs={ADMIN: ids[::-1] + ["b"]}, txs={i: tx(b"\x01" * 16) for i in ids})
    assert bw.run(args(tmp_path), client=f) == 0
    assert f.log.count("getTransaction") == bw.MAX_TX_FETCH
    assert bw.run(args(tmp_path), client=f) == 0
    assert f.log.count("getTransaction") == 5  # the remaining two are fetched next poll
