"""launchlab_cpmm_fetch / launchlab_cpmm_screen: decode, account layout, guards, math on synthetic pools."""

from __future__ import annotations

import base64
import json
import struct
import subprocess

import numpy as np
import pytest

from tools import launchlab_cpmm_fetch as C
from tools import launchlab_cpmm_screen as S
from tools import launchlab_price as L
from tools import venue_tape as vt


def swap_event(pool: bytes, vin, vout, ain, aout, tf, cf, con, in_mint: bytes, out_mint: bytes, base_input=True) -> str:
    raw = C.SWAP_DISC + pool + struct.pack("<6Q", vin, vout, ain, aout, 0, 0) + bytes([1 if base_input else 0]) + in_mint + out_mint + struct.pack("<QQ", tf, cf) + bytes([1 if con else 0])
    return base64.b64encode(raw).decode()


def test_b58_roundtrip():
    for b in (b"\x00\x00abc", bytes(range(32)), b"\xff" * 32):
        assert C.b58decode(vt.b58(b)) == b


def test_decode_swap_170_bytes():
    pool, wsol, tok = bytes([1]) * 32, C.b58decode(C.WSOL), bytes([7]) * 32
    d = C.decode_swap(swap_event(pool, 85_000_000_062, 206_900_000_000_000, 50_000_000, 119_810_874_200, 125_000, 625_000, True, wsol, tok))
    assert d["event_len"] == 170 and d["input_mint"] == C.WSOL and d["trade_fee"] == 125_000 and d["creator_fee_on_input"] is True
    assert d["input_amount"] == 50_000_000 and "tail_undecoded_b64" not in d


def test_decode_rejects_other_disc():
    assert C.decode_swap(base64.b64encode(b"\0" * 170).decode()) is None


def test_pool_account_layout():
    keys = ["pay", "creator", "cfg", "perm", "POOL", "M0", "M1", "lp", "x8", "x9", "x10", "V0", "V1"] + [f"k{i}" for i in range(10)] + [C.CPMM, C.LAUNCHLAB, "sys"]
    n = len(keys)
    data58 = vt.b58(next(iter(C.INIT_DISCS)) + b"\x01")
    tx = {"transaction": {"message": {"accountKeys": keys, "instructions": [{"programIdIndex": n - 2, "accounts": [1, 2], "data": ""}]}},
          "meta": {"innerInstructions": [{"instructions": [{"programIdIndex": n - 3, "accounts": list(range(13)), "data": data58}]}]}}
    m = C.pool_from_migration_tx(tx)
    assert m["cpmm_pool"] == "POOL" and m["mint0"] == "M0" and m["mint1"] == "M1" and m["vault0"] == "V0" and m["vault1"] == "V1" and m["amm_config"] == "cfg" and not m["wsol_in_pool"]


def test_migration_outside_window_refused():
    with pytest.raises(L.Refused):
        C.check_migration({"signature": "s", "block_time": int(L.EXPLORE_END.timestamp())})
    C.check_migration({"signature": "s", "block_time": int(L.EXPLORE_END.timestamp()) - 1})


class FakeCredits:
    def __init__(self):
        self.used = 0

    def charge(self, n=None):
        self.used += n


def test_fetch_pool_clips_at_holdout_edge_and_caps_pages():
    end = int(L.EXPLORE_END.timestamp())
    t0 = end - 1000
    pages = {None: {"data": [{"transaction": {"signatures": [f"a{i}"]}, "blockTime": t0 + i * 100, "slot": 1, "meta": {"logMessages": []}} for i in range(15)], "paginationToken": "t"}}
    calls = []

    def call(method, params):
        calls.append(params[1].get("paginationToken"))
        return pages[None] if not params[1].get("paginationToken") else {"data": [], "paginationToken": None}

    rows, st = C.fetch_pool(call, FakeCredits(), "P", t0, t0 + 86400)
    assert st["dropped_outside"] == 5 and st["txs"] == 10 and not st["truncated"]  # 5 txs at or after the holdout start dropped
    rows, st = C.fetch_pool(call, FakeCredits(), "P", t0, t0 + 86400, max_pages=1)
    assert st["truncated"] and st["pages"] == 1


def synth_pool(n=60, t0=1_791_000_000, drift=1.0, wsol=True):
    S0, T0 = 85e9, 206.9e12
    swaps = []
    slot = 1000
    for j in range(n):
        buy = j % 3 != 2
        amt = 0.2e9 if buy else 0.0
        if buy:
            tf, cf = amt * 0.0025, amt * 0.0005
            net = amt - tf - cf
            out = T0 * net / (S0 + net)
            swaps.append({"slot": slot, "block_time": t0 + 20 * j, "type": "swap", "input_vault_before": S0, "output_vault_before": T0, "input_amount": amt, "output_amount": out,
                          "trade_fee": tf, "creator_fee": cf, "creator_fee_on_input": True, "input_mint": C.WSOL, "output_mint": "TOK"})
            S0, T0 = S0 + net * drift, T0 - out
        else:
            tin = T0 * 0.0015
            tf = tin * 0.0025
            net = tin - tf
            gross = S0 * net / (T0 + net)
            swaps.append({"slot": slot, "block_time": t0 + 20 * j, "type": "swap", "input_vault_before": T0, "output_vault_before": S0, "input_amount": tin, "output_amount": gross,
                          "trade_fee": tf, "creator_fee": 0, "creator_fee_on_input": True, "input_mint": "TOK", "output_mint": C.WSOL})
            S0, T0 = S0 - gross, T0 + net
        slot += 90
    return {"block_time": t0, "cpmm_pool": "P", "wsol_in_pool": wsol}, swaps


def test_math_matches_synthetic_swaps():
    meta, sw = synth_pool()
    P = S.CPool(meta, sw)
    for j in range(P.n):
        vin = P.S0[j] if P.sol_in[j] else P.T0[j]
        vout = P.T0[j] if P.sol_in[j] else P.S0[j]
        pred, _ = S.swap_out(vin, vout, P.amt_in[j], P.tf[j], P.cf[j], P.con[j])
        assert abs(pred - P.amt_out[j]) < 1e-3 * max(1.0, P.amt_out[j]) * 1e-6 + 1.0


def test_attempt_and_round_trip():
    meta, sw = synth_pool(n=400)
    meta["block_time"] = sw[0]["block_time"]
    for s in sw:
        s["block_time"] += 0
    P = S.CPool(meta, sw)
    # force a 24 h window inside the data: shrink by pretending HIST_S small
    old = S.HIST_S
    S.HIST_S = 20 * 380
    try:
        r = S.attempt(P, 0.22)
    finally:
        S.HIST_S = old
    assert isinstance(r, dict) or r in ("no_swap_near_entry_time", "history_truncated")


def test_non_wsol_refused():
    meta, sw = synth_pool(wsol=False)
    assert S.attempt(S.CPool(meta, sw), 0.22) == "non_wsol"


def test_round_trip_flat_market_loses_fees():
    a = {"S_e": np.array([85e9]), "T_e": np.array([206.9e12]), "S_x": np.array([85e9]), "T_x": np.array([206.9e12]), "spot": np.array([85e9 / 206.9e12]),
         "r_in_buy": np.array([0.003]), "r_out_buy": np.array([0.0]), "r_in_sell": np.array([0.0025]), "r_out_sell": np.array([0.0005])}
    pnl, filled, st = S.round_trip(0.25e9, a)
    assert filled[0] and not st[0] and -0.25e9 * 0.02 < pnl[0] < 0


def test_pool_file_with_holdout_row_refused(tmp_path):
    f = tmp_path / "pool-x.jsonl"
    f.write_text(json.dumps({"type": "pool_meta", "block_time": 1}) + "\n" + json.dumps({"type": "swap", "block_time": int(L.EXPLORE_END.timestamp()) + 5}) + "\n")
    subprocess.run(["zstd", "-q", "-f", "--rm", str(f)], check=True)
    with pytest.raises(L.Refused):
        S.load_pool_file(str(f) + ".zst")
