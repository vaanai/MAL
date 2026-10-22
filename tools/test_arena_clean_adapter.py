"""Tests for tools/arena_clean_adapter.py: ordering, synthetic t_recv_ms, refusals, field mapping. Synthetic rows only."""
import json
import subprocess

import pytest

from tools import arena_clean_adapter as A
from tools import arena_screen as S

H = "2026-08-14T12"
T = A.hour_epoch(H)
W = S.WSOL


def raw(pool, slot, bt, tx=0, ev=0, side="sell", wsol=True, venue="pumpswap", **kw):
    r = {"v": 2, "venue": venue, "mint": "M" + pool, "trader": "t", "side": side, "sol_lamports": 5, "token_raw": 7, "quote_reserve": 11, "base_reserve": 13, "pool": pool, "slot": slot,
         "signature": f"s{slot}{tx}{ev}", "event_index": ev, "t_recv_ms": None, "event_ts": bt, "quote_is_wsol": wsol, "block_time": bt, "tx_index": tx}
    r.update(kw)
    return r


def zst(path, rows):
    p = subprocess.run(["zstd", "-q", "-f", "-o", str(path)], input="".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows).encode(), check=True)
    return str(path)


def test_trade_rows_keeps_wsol_pumpswap_only_and_maps_v(tmp_path):
    f = zst(tmp_path / "t.jsonl.zst", [raw("A", 1, T), raw("B", 2, T, wsol=False), raw("C", 3, T, venue="pump_bonding"), raw("D", 4, T, side="x"), raw("E", 5, T)])
    rows, cnt = A.trade_rows(f, S.VMap([{"A": 17_600_000_000.0}]))
    assert [r["pool"] for r in rows] == ["A", "E"] and cnt["non_wsol"] == 1 and cnt["kept"] == 2
    a, e = rows
    assert a["virtual_quote_reserves"] == 17_600_000_000 and e["virtual_quote_reserves"] is None  # unknown V: null, the kit's NaN
    assert a["quote_mint"] == W and a["prog"] == "pumpswap" and a["type"] == "trade" and a["event_ts"] == T


def test_stamp_orders_like_the_kit_and_spreads_inside_the_second():
    rows = [raw("A", 20, T + 5, tx=2), raw("B", 20, T + 5, tx=1), raw("C", 10, T + 4), raw("D", 20, T + 5, tx=1, ev=1)]
    for r in rows:
        r["event_ts"] = r["block_time"]
    out = A.stamp(rows, H)
    assert [r["pool"] for r in out] == ["C", "B", "D", "A"]  # slot, then tx_index, then event_index
    ts = [r["t_recv_ms"] for r in out]
    assert ts == sorted(ts) and ts[0] == (T + 4) * 1000 and ts[1] == (T + 5) * 1000 and ts[2] == (T + 5) * 1000 + 333 and ts[3] == (T + 5) * 1000 + 666
    assert all("tx_index" not in r for r in out)


def test_stamp_refuses_outside_hour_and_non_monotone_and_counts_overfull_seconds():
    with pytest.raises(A.AdapterRefused, match="outside_hour"):
        A.stamp([raw("A", 1, T + 3600)], H)
    with pytest.raises(A.AdapterRefused, match="monotone"):
        A.stamp([raw("A", 1, T + 9), raw("B", 2, T + 3)], H)
    before = A.STATS["seconds_over_1000_rows"]
    out = A.stamp([raw("A", 1, T + 3, ev=i) for i in range(1001)], H)
    assert A.STATS["seconds_over_1000_rows"] == before + 1 and [r["event_index"] for r in out] == list(range(1001))


def test_real_t_recv_rows_keep_their_clock_and_order():
    a, b = raw("A", 2, T + 1), raw("B", 1, T + 2)
    a["_real_t"], b["_real_t"] = T * 1000 + 500, T * 1000 + 100
    out = A.stamp([a, b], H)
    assert [r["pool"] for r in out] == ["B", "A"] and out[0]["t_recv_ms"] == T * 1000 + 100
    c = raw("C", 3, T)
    c["_real_t"] = (T + 3600) * 1000
    with pytest.raises(A.AdapterRefused, match="file_hour"):
        A.stamp([c], H)


def test_create_and_complete_mapping():
    c = A.to_create({"mint": "M", "block_time": T, "slot": 4, "signature": "x", "is_mayhem_mode": True, "tx_index": 3})
    k = A.to_complete({"mint": "M", "event_ts": T + 9, "slot": 5, "signature": "y"})
    assert c["type"] == "create" and c["is_mayhem_mode"] is True and c["event_ts"] == T
    assert k["type"] == "complete" and k["event_ts"] == T + 9 and k["mint"] == "M"


def test_refuses_sealed_and_arena_paths(tmp_path):
    with pytest.raises(A.AdapterRefused):
        A.refuse_path("/data/mal/clean-view/fresh-0802")
    with pytest.raises(A.AdapterRefused):
        A.run(["/data/mal/clean-view/explore-0814/w1"], "/data/mal/arena/x", [], False)
