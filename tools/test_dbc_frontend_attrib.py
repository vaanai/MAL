"""Unit tests for tools/dbc_frontend_attrib.py on synthetic data only (no tape, no RPC)."""
from __future__ import annotations

import json
from collections import Counter

import pytest

from tools import dbc_decode as dd
from tools import dbc_frontend_attrib as A

SOL = "So11111111111111111111111111111111111111112"
END = 1_000_000.0


def _create(pool, slot, t, sig, creator="CR", config="CFG", mint="MINTxxxxBAGS"):
    return {"type": "create", "pool": pool, "slot": slot, "block_time": t, "signature": sig, "creator": creator, "config": config, "base_mint": mint}


def _trade(pool, slot, t, signer, amt, side="buy", sig=None):
    return {"type": "trade", "pool": pool, "slot": slot, "block_time": t, "signer": signer, "amount_in": amt, "side": side, "signature": sig or f"s{slot}{signer}{amt}",
            "quote_mint": SOL}


def _hour(creates=(), trades=(), completes=(), incomplete=False):
    return {"creates": list(creates), "trades": list(trades), "completes": list(completes), "incomplete": incomplete}


def test_scan_and_metrics_window_slots_and_launcher():
    c = _create("P", 100, 1000.0, "CSIG")
    trades = [
        _trade("P", 100, 1000.0, "CR", 50, sig="CSIG"),  # in the create tx: excluded
        _trade("P", 100, 1000.0, "BOT1", 40),  # slot 0
        _trade("P", 101, 1000.4, "BOT2", 20),  # slot 1
        _trade("P", 101, 1000.4, "BOT2", 10),  # same wallet again
        _trade("P", 101, 1000.4, "PAYER", 99),  # create-tx payer: excluded when known
        _trade("P", 105, 1002.0, "CR", 500),  # creator later: excluded
        _trade("P", 110, 1010.0, "RET", 30),  # later buyer
        _trade("P", 112, 1011.0, "RET", 70, side="sell"),  # sells are not buy volume
        _trade("P", 200, 1031.0, "LATE", 1000),  # outside 30 s
        _trade("P", 99, 999.0, "PRE", 5),  # before the create slot
        _trade("Q", 101, 1000.4, "X", 7),  # another pool with no create
    ]
    res = A.scan_hours([_hour([c], trades, [{"pool": "P", "block_time": 1500.0, "signature": "G"}])], END)
    p = res["pools"]["P"]
    m = A.pool_metrics(p, END, payer="PAYER")
    assert m["vol01"] == 40 + 20 + 10
    assert m["vol_win"] == 40 + 20 + 10 + 30
    assert m["others01"] == 2
    assert m["buyers_win"] == 3
    assert m["n_buys_in_create"] == 1
    assert m["launcher_buys_win"] == 2
    assert m["grad24"] and not m["grad_in_create"] and m["win_ok"]
    m2 = A.pool_metrics(p, END, payer=None)  # payer unknown -> its buy counts as "other"
    assert m2["others01"] == 3 and m2["vol01"] == 40 + 20 + 10 + 99


def test_trades_in_next_hour_and_censoring():
    c = _create("P", 100, END - 20.0, "C")
    h1 = _hour([c], [_trade("P", 100, END - 20.0, "A", 1)])
    h2 = _hour([], [_trade("P", 120, END - 5.0, "B", 3)])
    res = A.scan_hours([h1, h2], END)
    m = A.pool_metrics(res["pools"]["P"], END)
    assert m["vol_win"] == 4  # trade in the next hour is kept
    assert not m["win_ok"]  # 30 s window crosses the end of the allowed data
    assert not m["grad_eligible"]


def test_bad_hour_excludes_window():
    c = _create("P", 100, 10.0, "C")
    res = A.scan_hours([_hour([c]), _hour(incomplete=True)], END)
    assert res["bad_hours"] == [1]
    assert not A.pool_metrics(res["pools"]["P"], END, bad_hour_idx={1})["win_ok"]
    assert A.pool_metrics(res["pools"]["P"], END, bad_hour_idx=set())["win_ok"]


def test_grad_in_create():
    c = _create("P", 100, 10.0, "C")
    res = A.scan_hours([_hour([c], [], [{"pool": "P", "block_time": 10.0, "signature": "C"}])], END)
    m = A.pool_metrics(res["pools"]["P"], END)
    assert m["grad_in_create"] and m["grad24"]


def test_r1_verdict_rules():
    assert A.r1_verdict(500, 0.30, 0) == "drop (bots are here)"
    assert A.r1_verdict(500, 0.05, 2) == "drop (bots are here)"
    assert A.r1_verdict(200, 0.0999, 1).startswith("candidate")
    assert A.r1_verdict(199.9, 0.01, 0).startswith("neither")
    assert A.r1_verdict(500, 0.10, 1).startswith("neither")
    assert A.r1_verdict(500, None, None) == "no data"


def test_build_table_pools_within_quote_and_label():
    ms = []
    for i in range(6):
        ms.append({"config": "A", "day": "2026-10-14", "win_ok": True, "vol_win": 100, "vol01": 5, "others01": i % 2, "grad_eligible": True, "grad24": i == 0,
                   "grad_in_create": False, "n_buys_in_create": 0, "quote_trade": SOL})
    ms.append({"config": "B", "day": "2026-10-15", "win_ok": True, "vol_win": 10, "vol01": 10, "others01": 3, "grad_eligible": False, "grad24": False,
               "grad_in_create": False, "n_buys_in_create": 1, "quote_trade": "USDCmint"})
    rows = A.build_table(ms, {"A": "Bags", "B": "Bags"}, {"A": SOL, "B": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"}, n_days=3.0)
    r = {(x["front_end"], x["quote"]): x for x in rows}
    a = r[("Bags", "SOL")]
    assert a["creates"] == 6 and a["share01"] == pytest.approx(30 / 600) and a["median_others01"] == 0.5
    assert a["grad24"] == pytest.approx(1 / 6) and a["per_day"] == 2.0
    assert r[("Bags", "USDC")]["share01"] == 1.0 and r[("Bags", "USDC")]["grad24"] is None


def test_partner_metadata_decode():
    fc = bytes(range(32))
    def s(x):
        b = x.encode()
        return len(b).to_bytes(4, "little") + b
    data = A.PARTNER_META_DISC + fc + b"\0" * 96 + s(" Bags ") + s("https://bags.fm") + s("logo")
    d = A.decode_partner_metadata(data)
    assert d == {"fee_claimer": dd.b58(fc), "name": "Bags", "website": "https://bags.fm", "logo": "logo"}
    with pytest.raises(ValueError):
        A.decode_partner_metadata(b"\0" * 200)


def test_fee_schedule_modes():
    def cfg(mode, cliff, n, freq, red):
        return {"activation_type": 1, "pool_fees": {"base_fee": {"base_fee_mode": mode, "cliff_fee_numerator": cliff, "first_factor": n, "second_factor": freq,
                                                                 "third_factor": red}, "dynamic_fee": {"initialized": 0}}}
    lin = A.fee_schedule(cfg(0, 500_000_000, 10, 6, 49_000_000))
    assert lin["start_bps"] == 5000 and lin["end_bps"] == pytest.approx(100) and lin["dur"] == 60
    ex = A.fee_schedule(cfg(1, 990_000_000, 2, 30, 5000))
    assert ex["end_bps"] == pytest.approx(9900 * 0.25) and ex["dur"] == 60
    rl = A.fee_schedule(cfg(2, 10_000_000, 5, 120, 1000))
    assert rl["start_bps"] == 100 and rl["end_bps"] == 100 and rl["dur"] == 120


def _key(i):
    return dd.b58(bytes([i]) * 32)


def test_parse_create_tx_wrapper_and_params():
    def s(x):
        b = x.encode()
        return len(b).to_bytes(4, "little") + b
    keys = [_key(1), _key(2), dd.PROGRAM, _key(9)] + [_key(20 + i) for i in range(12)]
    data = bytes([140, 85, 215, 176, 102, 54, 104, 79]) + s("Tok") + s("TOK") + s("https://storage.example.fm/m.json")
    accs = [4, 5, 1, 6, 7, 8, 9, 10, 11, 12, 0, 13, 14, 15]  # payer (index 10) -> key 0
    tx = {"blockTime": 5, "meta": {"err": None, "innerInstructions": [{"index": 0, "instructions": [{"programIdIndex": 2, "accounts": accs, "data": dd.b58(data), "stackHeight": 2}]}]},
          "transaction": {"signatures": ["SIG"], "message": {"header": {"numRequiredSignatures": 2}, "accountKeys": keys,
                                                             "instructions": [{"programIdIndex": 3, "accounts": [], "data": ""}]}}}
    out = A.parse_create_tx(tx, "SIG")
    assert out["sig_ok"] and out["fee_payer"] == _key(1) and out["signers"] == [_key(1), _key(2)]
    cr = out["create"]
    assert cr["ix"] == "initialize_virtual_pool_with_spl_token" and cr["depth"] == 2 and cr["wrapper"] == _key(9)
    assert cr["creator"] == _key(2) and cr["payer"] == _key(1) and cr["uri_host"] == "storage.example.fm" and cr["symbol"] == "TOK"


def test_uri_host():
    assert A.uri_host("ipfs://abc") == "ipfs://"
    assert A.uri_host("") == "(empty)"
    assert A.uri_host("https://IPFS.io/ipfs/x") == "ipfs.io"


def test_label_precedence():
    assert A.label_partner("F" * 44, {"name": "Prana", "website": "w"}, Counter({"x.fm": 3}), Counter({"BAGS": 3}), 3)[0] == "Prana"
    assert A.label_partner("F" * 44, None, Counter({"storage.bags.fm": 3, "ipfs.io": 1}), Counter(), 4)[0] == "uri:storage.bags.fm"
    assert A.label_partner("F" * 44, None, Counter({"ipfs.io": 3}), Counter({"BAGS": 2, "abcd": 1}), 3)[0] == "Bags (mint suffix BAGS)"
    assert A.label_partner("Fabcdefgh", None, Counter({"ipfs.io": 3}), Counter({"zzzz": 3}), 3)[0] == "unattributed partner Fabcde"


def test_choose_sample_seeded_and_budget():
    creates = [{"config": "big", "signature": f"b{i}"} for i in range(50)] + [{"config": "small", "signature": f"s{i}"} for i in range(5)]
    po = {"big": "PB", "small": "PS"}
    s1 = A.choose_sample(creates, po, k_big=4, big_min=20, budget=10, seed=1)
    assert s1 == A.choose_sample(creates, po, k_big=4, big_min=20, budget=10, seed=1)
    assert len(s1) == 10 and len(set(s1)) == 10 and sum(x.startswith("b") for x in s1[:4]) == 4


def test_public_rpc_budget_and_refuses_keyed(tmp_path):
    with pytest.raises(A.Refused):
        A.PublicRPC(str(tmp_path / "l.jsonl"), url="https://mainnet.helius-rpc.com/?api-key=x")
    calls = []
    r = A.PublicRPC(str(tmp_path / "l.jsonl"), max_gettx=2, max_total=3, spacing_s=0.0, opener=lambda b: calls.append(b) or {"result": 1})
    assert r.call("getTransaction", []) == 1 and r.call("getTransaction", []) == 1
    with pytest.raises(A.Refused):
        r.call("getTransaction", [])
    assert r.call("getMultipleAccounts", []) == 1
    with pytest.raises(A.Refused):
        r.call("getMultipleAccounts", [])
    assert len((tmp_path / "l.jsonl").read_text().splitlines()) == 3
    r2 = A.PublicRPC(str(tmp_path / "l.jsonl"), max_gettx=2, max_total=3, spacing_s=0.0, opener=lambda b: {"result": 1})
    assert (r2.n_total, r2.n_gettx) == (3, 2)  # ledger persists across instances


def test_guard_refuses_non_default_mode(monkeypatch):
    monkeypatch.setattr(A.L, "window", lambda: (A.L.FWD_START, A.L.FWD_END, A.L.FWD_ROOT))
    with pytest.raises(A.Refused):
        A.load_tape(log=lambda *a: None)
