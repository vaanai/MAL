"""Offline tests for tools/probe_sim_calibration.py: fixture tape and fills, seal, build split."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import probe_executor as pe
from tools import probe_sim_calibration as psc

M1, M2 = "MintAAAA1111pump", "MintBBBB2222pump"
T_AFTER = 1791226000000  # after the build split
T_BEFORE = 1791220000000
V = 17_584_505_451
BASE, Q0 = 400_000_000 * 10**6, 70 * 10**9
SPEND = 50_000_000


def trow(mint, slot, quote, t_ms, base=BASE):
    return {"v": 2, "venue": "pumpswap", "mint": mint, "side": "buy", "pool": "P" + mint, "slot": slot, "tx_index": 0,
            "event_index": 0, "t_recv_ms": t_ms, "quote_reserve": quote, "base_reserve": base, "virtual_quote_reserve": V}


def fills(mint, t, landed_slot=102, reason="tp", pnl=1_000_000, sell_slot=106, snap=104):
    buy = {"schema": "probe_fill_v1", "mode": "live", "kind": "buy", "ts_ms": t, "mint": mint, "landed": True, "pool": "P" + mint,
           "landed_slot": landed_slot, "spend_lamports": SPEND, "tokens_received": 0, "fee_lamports": 505_000,
           "priority_fee_lamports": 500_000, "v_lamports": V, "snapshot_slot": 101, "entry_vs_quote_bps": -10}
    sell = {"schema": "probe_fill_v1", "mode": "live", "kind": "sell", "ts_ms": t + 5000, "mint": mint, "landed": True,
            "exit_reason": reason, "ret": 0.5, "sol_received_lamports": 70_000_000, "landed_slot": sell_slot,
            "snapshot_slot": snap, "fee_lamports": 505_000, "priority_fee_lamports": 500_000, "pnl_lamports": pnl,
            "hold_ms": 5000, "first_send_ms": t + 4000}
    return buy, sell


def tape_rows(mint, t):
    return [trow(mint, 100, Q0, t - 2000), trow(mint, 101, Q0, t - 1000),
            trow(mint, 103, Q0 + 5 * 10**9, t + 1000),
            trow(mint, 104, Q0 + 60 * 10**9, t + 2000),  # tp
            trow(mint, 106, Q0 + 60 * 10**9, t + 3000)]


def write_tape(d: Path, rows, t):
    d.mkdir(exist_ok=True)
    with (d / f"trades-{psc._hour_name(t)}.jsonl").open("a") as fh:
        fh.write("\n".join(json.dumps(r) for r in rows) + "\n")


def fills_file(tmp_path, items):
    p = tmp_path / "fills.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in items) + "\n")
    return p


def live_tokens(rows):
    return pe.entry_quote(psc.snap_of(rows[1]), SPEND)["tokens"]


def make(t=T_AFTER, mint=M1):
    rows = tape_rows(mint, t)
    b, s = fills(mint, t)
    b["tokens_received"] = live_tokens(rows) * 99 // 100  # live got 1% fewer
    return rows, b, s


def test_build_split():
    assert psc.build_of(psc.BUILD_SPLIT_MS - 1) == "8a6849b"
    assert psc.build_of(psc.BUILD_SPLIT_MS) == "a25eb17"


def test_pair_ignores_dryrun_and_failed(tmp_path):
    b, s = fills(M1, T_AFTER)
    b["tokens_received"] = 5
    dry = {**b, "mode": "dryrun", "mint": M2}
    failed = {**b, "landed": False, "mint": "M3"}
    trades = psc.pair_trades(psc.load_fills(fills_file(tmp_path, [b, s, dry, failed])))
    assert [t["buy"]["mint"] for t in trades] == [M1]


def test_end_to_end_entry_exit_and_seal(tmp_path):
    rows, b, s = make()
    d = tmp_path / "tape"
    write_tape(d, rows + tape_rows(M2, T_AFTER), T_AFTER)  # M2 is on tape but not in fills
    out = tmp_path / "out"
    agg = psc.run(fills_file(tmp_path, [b, s]), d, out)
    res = json.loads((out / "calibration.json").read_text())["trades"]
    assert [r["mint"] for r in res] == [M1]
    r = res[0]
    assert r["status"] == "ok" and r["build"] == "a25eb17"
    assert r["entry_row_slot"] == 101  # last row with slot < landed_slot
    assert r["entry_gap_bps"] == pytest.approx((1 / 0.99 - 1) * 1e4, abs=2)
    assert r["sim_exit_reason"] == "tp" and r["sim_trigger_slot"] == 104 and r["reason_agree"] is True
    assert r["live_sell_delay_slots"] == 2  # 106 - 104
    assert r["sim_pnl_delayed_lamports"] is not None
    assert r["pnl_gap_lamports"] == r["live_pnl_lamports"] - r["sim_pnl_delayed_lamports"]
    assert agg["a25eb17"]["n_trades"] == 1 and agg["8a6849b"]["n_trades"] == 0
    assert (out / "calibration.md").read_text().startswith("# Probe")


def test_priority_and_size_sensitivity():
    rows, b, s = make()
    r = psc.simulate_trade({"buy": b, "sell": s}, rows, {M1})
    base = r["sim_pnl_delayed_lamports"]
    assert r["sensitivity"]["priority_150k"]["sim_pnl_lamports"] == base + 700_000  # 2 sides * (500k - 150k)
    s1, s25 = r["sensitivity"]["size_0.1"], r["sensitivity"]["size_0.25"]
    # price impact is re-simulated with V, so it grows with size (not linear scaling)
    assert 0 < s1["entry_impact_bps_vs_spot"] < s25["entry_impact_bps_vs_spot"]


def test_seal_foreign_mint_rejected():
    rows, b, s = make(mint=M2)
    with pytest.raises(ValueError, match="seal"):
        psc.simulate_trade({"buy": b, "sell": s}, rows, {M1})


def test_tape_reader_drops_foreign_mints(tmp_path):
    d = tmp_path / "tape"
    write_tape(d, tape_rows(M1, T_AFTER) + tape_rows(M2, T_AFTER), T_AFTER)
    got = psc.read_tape_rows(d, [psc._hour_name(T_AFTER)], {M1})
    assert set(got) == {M1}


def test_tp_sl_disagree_and_build_aggregate(tmp_path):
    rows_a, ba, sa = make(T_AFTER, M1)
    sa["exit_reason"] = "sl"  # live says sl, tape says tp
    rows_b = tape_rows(M2, T_BEFORE)
    bb, sb = fills(M2, T_BEFORE)
    bb["tokens_received"] = live_tokens(rows_b)
    d = tmp_path / "tape"
    write_tape(d, rows_a, T_AFTER)
    write_tape(d, rows_b, T_BEFORE)
    agg = psc.run(fills_file(tmp_path, [ba, sa, bb, sb]), d, tmp_path / "o")
    assert agg["a25eb17"]["tp_sl_disagree"] == 1 and agg["8a6849b"]["tp_sl_disagree"] == 0
    assert agg["8a6849b"]["n_trades"] == 1 and agg["all"]["n_trades"] == 2
