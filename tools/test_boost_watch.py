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
    return {"slot": 5, "transaction": {"message": {"accountKeys": [ADMIN, program], "instructions": [] if inner else [ix]}},
            "meta": {"err": None, "innerInstructions": [{"index": 0, "instructions": [ix]}] if inner else []}}


class Fake:
    def __init__(self, account=RAW, sigs=None, txs=None, fail=False):
        self.account, self.sigs, self.txs, self.fail, self.calls = account, sigs or {}, txs or {}, fail, []

    def call(self, method, params):
        self.calls.append(method)
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


def test_new_config_write_alerts(tmp_path):
    assert bw.run(args(tmp_path), client=Fake(sigs={bw.CONFIG_ADDRESS: ["a"]})) == 0
    f = Fake(sigs={bw.CONFIG_ADDRESS: ["z", "a"]}, txs={"z": tx(b"\x02" * 9)})
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
                                       "new_txs", "change", "status"}
