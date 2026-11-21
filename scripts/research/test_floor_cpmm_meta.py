"""Tests for floor_cpmm_meta.py on SYNTHETIC account bytes and a fake RPC. No network, no key, no real account."""
import base64
import json
import struct
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

import floor_cpmm_meta as M  # noqa: E402
from tools import launchlab_cpmm_fetch as C  # noqa: E402
from tools import venue_backfill as vb  # noqa: E402

B58 = C._B58


def key(n: int) -> bytes:
    return bytes([n]) * 32


def addr(n: int) -> str:
    return M.vt.b58(key(n))


def pool_state(cfg=1, lp_mint=2, m0=None, m1=3, lp_supply=1_000_000):
    b = bytearray(341)
    b[8:40] = key(cfg)
    b[136:168] = key(lp_mint)
    b[168:200] = b58b(m0) if m0 else key(9)
    b[200:232] = key(m1)
    struct.pack_into("<Q", b, 333, lp_supply)
    return bytes(b)


def b58b(s: str) -> bytes:
    return C.b58decode(s)


def mint(supply=10**15, ma=None, fz=None, ext=()):
    b = bytearray(82)
    if ma is not None:
        struct.pack_into("<I", b, 0, 1)
        b[4:36] = key(ma)
    struct.pack_into("<Q", b, 36, supply)
    b[45] = 1
    if fz is not None:
        struct.pack_into("<I", b, 46, 1)
        b[50:82] = key(fz)
    if ext is None:
        return bytes(b)
    if ext:
        b += bytes(165 - 82) + bytes([1])
        for t in ext:
            b += struct.pack("<HH", t, 4) + bytes(4)
    return bytes(b)


def amm_cfg(trade=2500, prot=120000, fund=40000, creator=0, n=116):
    b = bytearray(n)
    struct.pack_into("<Q", b, 12, trade)
    struct.pack_into("<Q", b, 20, prot)
    struct.pack_into("<Q", b, 28, fund)
    if n >= 116:
        struct.pack_into("<Q", b, 108, creator)
    return bytes(b)


def test_decode_pool_state_amm_config_and_short():
    s = M.decode_pool_state(pool_state(cfg=5, lp_mint=6, m0=M.WSOL, m1=7, lp_supply=42))
    assert s == {"amm_config": addr(5), "lp_mint": addr(6), "mint0": M.WSOL, "mint1": addr(7), "lp_supply": 42}
    assert M.decode_amm_config(amm_cfg(2500, 1, 2, 300)) == {"trade_fee_rate": 2500, "protocol_fee_rate": 1, "fund_fee_rate": 2, "creator_fee_rate": 300}
    with pytest.raises(M.Unreadable):
        M.decode_pool_state(b"\0" * 100)
    with pytest.raises(M.Unreadable):
        M.decode_amm_config(amm_cfg(n=100))


def test_decode_mint_classic_authorities_and_supply():
    d = M.decode_mint(mint(supply=123, ma=4, fz=5, ext=None), M.TOKEN_PROGRAM)
    assert d["supply"] == 123 and d["mint_authority"] == addr(4) and d["freeze_authority"] == addr(5) and d["token_program"] == "token" and not d["transfer_fee"]
    d = M.decode_mint(mint(ext=None), M.TOKEN_PROGRAM)
    assert d["mint_authority"] is None and d["freeze_authority"] is None


def test_decode_mint_token2022_extensions():
    d = M.decode_mint(mint(ext=(M.EXT_TRANSFER_FEE_CONFIG, 12)), M.TOKEN_2022)
    assert d["transfer_fee"] and d["token_program"] == "token-2022" and d["extensions"] == [1, 12] and d["extensions_report_only"]["permanent_delegate"] == 1
    d = M.decode_mint(mint(ext=(14, 6)), M.TOKEN_2022)
    assert not d["transfer_fee"] and d["extensions_report_only"]["transfer_hook"] == 1 and d["extensions_report_only"]["default_account_state"] == 1
    for bad, own in ((mint(ext=None), "SomeOtherProgram"), (b"\0" * 10, M.TOKEN_PROGRAM), (mint(ext=None)[:45] + b"\0" + mint(ext=None)[46:], M.TOKEN_PROGRAM)):
        with pytest.raises(M.Unreadable):
            M.decode_mint(bad, own)
    trunc = mint(ext=(1,))[:-2]
    with pytest.raises(M.Unreadable):
        M.decode_mint(trunc, M.TOKEN_2022)


class FakeRpc:
    def __init__(self, accounts, lock_amounts):
        self.accounts, self.lock, self.calls = accounts, lock_amounts, []

    def __call__(self, method, params):
        self.calls.append(method)
        if method == "getMultipleAccounts":
            vals = []
            for a in params[0]:
                v = self.accounts.get(a)
                vals.append(None if v is None else {"data": [base64.b64encode(v[0]).decode(), "base64"], "owner": v[1]})
            return {"value": vals}
        assert method == "getTokenAccountsByOwner" and params[0] == M.LOCK_AUTH
        amt = self.lock.get(params[1]["mint"], [])
        return {"value": [{"account": {"data": {"parsed": {"info": {"mint": params[1]["mint"], "owner": M.LOCK_AUTH, "tokenAmount": {"amount": str(x)}}}}}} for x in amt]}


def world():
    acc = {}
    pools = []
    for i, (fz, tf, lp_sup, burned, locked) in enumerate([(None, False, 1000, 600, 350), (7, False, 1000, 1000, 0), (None, True, 1000, 0, 0)]):
        p, tokm, lpm, cfg = addr(20 + i), addr(30 + i), addr(40 + i), addr(50)
        acc[p] = (pool_state(cfg=50, lp_mint=40 + i, m0=M.WSOL, m1=30 + i, lp_supply=lp_sup), M.CPMM)
        acc[tokm] = (mint(ma=None, fz=fz, ext=(1,) if tf else None), M.TOKEN_2022 if tf else M.TOKEN_PROGRAM)
        acc[lpm] = (mint(supply=lp_sup - burned, ext=None), M.TOKEN_PROGRAM)
        pools.append(p)
    acc[addr(50)] = (amm_cfg(2500, creator=0), M.CPMM)
    return acc, pools


def test_build_meta_fields_lp_locked_and_flags():
    acc, pools = world()
    lock = {addr(40): [200, 150]}
    out = M.build_meta(FakeRpc(acc, lock), vb.Credits(100, 1), pools)
    a, b, c = (out[p] for p in pools)
    assert a["lp_locked_frac"] == pytest.approx((600 + 350) / 1000) and a["freeze_authority"] is None and a["transfer_fee"] is False
    assert a["trade_fee_rate"] == 2500 and a["creator_fee_rate"] == 0 and a["supply"] == 10**15 and a["token_mint"] == addr(30) and a["amm_config"] == addr(50)
    assert b["freeze_authority"] == addr(7) and b["lp_locked_frac"] == 1.0
    assert c["transfer_fee"] is True and c["token_program"] == "token-2022" and c["lp_locked_frac"] == 0.0


def test_unreadable_is_excluded_never_assumed():
    acc, pools = world()
    del acc[pools[0]]  # pool state missing
    acc[addr(31)] = (b"\0" * 10, M.TOKEN_PROGRAM)  # token mint undecodable
    out = M.build_meta(FakeRpc(acc, {}), vb.Credits(100, 1), pools)
    assert out[pools[0]] == {"unreadable": "pool_state_missing"} and out[pools[1]] == {"unreadable": "mint_undecodable"} and "unreadable" not in out[pools[2]]
    acc2, pools2 = world()
    del acc2[addr(50)]  # AmmConfig missing: every pool of that config is unreadable
    assert all(v == {"unreadable": "amm_config_missing"} for v in M.build_meta(FakeRpc(acc2, {}), vb.Credits(100, 1), pools2).values())


def test_non_wsol_quote_and_lp_supply_guards():
    acc, pools = world()
    acc[pools[0]] = (pool_state(cfg=50, lp_mint=40, m0=addr(77), m1=30, lp_supply=1000), M.CPMM)
    acc[addr(41)] = (mint(supply=5000, ext=None), M.TOKEN_PROGRAM)  # LP mint supply above PoolState.lp_supply
    out = M.build_meta(FakeRpc(acc, {}), vb.Credits(100, 1), pools)
    assert out[pools[0]] == {"unreadable": "non_wsol_quote"} and out[pools[1]] == {"unreadable": "lp_mint_supply_above_pool_state"}


def test_call_cap_stops_before_the_call():
    acc, pools = world()
    rpc = FakeRpc(acc, {})
    with pytest.raises(vb.CapReached):
        M.build_meta(rpc, vb.Credits(2, 1), pools)
    assert len(rpc.calls) == 2


def test_cli_writes_file_with_sha_and_never_prints_key(tmp_path, capsys, monkeypatch):
    acc, pools = world()
    rpc = FakeRpc(acc, {addr(40): [350]})
    mig = tmp_path / "m.json"
    mig.write_text(json.dumps([{"pool": p, "bt": C.WIN_START} for p in pools]))
    env = tmp_path / "h.env"
    env.write_text("HELIUS_API_KEY=SECRETKEY123\n")
    monkeypatch.setattr(M.vb, "read_key", lambda p: "SECRETKEY123")
    monkeypatch.setattr(M.vb, "make_url", lambda k: "http://invalid.example/" + k)
    monkeypatch.setattr(M.vb, "http_rpc", lambda url, pacer: rpc)
    monkeypatch.setattr(M.C, "acquire_slot", lambda d: open(tmp_path / "lock", "a"))
    out = tmp_path / "meta.json"
    assert M.main(["--migrations", str(mig), "--out", str(out), "--env-file", str(env), "--lockdir", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "SECRETKEY123" not in printed and json.loads(printed)["pools"] == 3
    doc = json.loads(out.read_text())
    assert doc["schema"] == M.SCHEMA and "look-ahead" in doc["note"] and set(doc["pools"]) == set(pools)
    import hashlib
    assert json.loads(printed)["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert M.main(["--migrations", str(mig), "--out", str(out), "--rps", "9"]) == 2
