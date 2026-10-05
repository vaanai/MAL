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


DEADLINE = T_AFTER + 30 * 60_000


def quiet_rows(extra=()):
    return [trow(M1, 100, Q0, T_AFTER - 2000), trow(M1, 101, Q0, T_AFTER - 1000),
            trow(M1, 103, Q0 + 10**9, T_AFTER + 60_000), *extra]


def test_time_stop_quiet_pool_priced_before_deadline():
    _, b, s = make()
    s["exit_reason"] = "time_stop"
    rows = quiet_rows()  # nothing after slot 103: no row ever crosses the deadline
    r = psc.simulate_trade({"buy": b, "sell": s}, rows, {M1})
    assert r["sim_exit_reason"] == "time_stop" and r["sim_trigger_slot"] == 103 and r["sim_trigger_ms"] == DEADLINE
    assert r["reason_agree"] is True
    # an uncovered deadline hour emits no exit
    assert psc.simulate_trade({"buy": b, "sell": s}, rows, {M1}, deadline_covered=False)["status"] == "no_exit_in_tape"


def test_time_stop_not_priced_on_row_past_deadline():
    _, b, s = make()
    spike = trow(M1, 900, Q0 + 90 * 10**9, DEADLINE + 5000)  # first row after the deadline, would be a tp
    r = psc.simulate_trade({"buy": b, "sell": s}, quiet_rows([spike]), {M1})
    assert r["sim_exit_reason"] == "time_stop" and r["sim_trigger_slot"] == 103


def test_delayed_sell_not_priced_on_own_sell_row():
    rows, b, s = make()
    rows = rows[:4] + [trow(M1, 106, Q0 + 1 * 10**9, T_AFTER + 3000)]  # row at the landing slot, post-sell crash
    r = psc.simulate_trade({"buy": b, "sell": s}, rows, {M1})
    # target slot 104 + 2 = 106 -> last row with slot < 106 is the trigger row itself
    assert r["sim_pnl_delayed_lamports"] == r["sim_pnl_immediate_lamports"]


def test_double_count_variants():
    rows, b, s = make()
    r = psc.simulate_trade({"buy": b, "sell": s}, rows, {M1})
    ve, vc = r["variants"]["sim_executor"], r["variants"]["sim_correct"]
    assert ve["ret"] > vc["ret"]  # executor re-adds our buy: inflates the book ret
    assert r["ret_diff_executor_minus_correct"] > 0
    assert r["ret_diff_executor_minus_correct"] == pytest.approx(r["ret_executor_at_live_trigger"] - r["ret_correct_at_live_trigger"])
    assert r["primary_variant"] == "sim_correct" and r["sim_ret"] == vc["ret"]
    r2 = psc.simulate_trade({"buy": b, "sell": s}, rows, {M1}, own_trade_in_tape=False)
    assert r2["primary_variant"] == "sim_executor" and r2["sim_ret"] == ve["ret"]
    assert set(r["variants"]) == {"sim_executor", "sim_correct", "live_executor", "live_correct",
                                  "live_legacy_executor", "live_legacy_correct"}


def test_live_position_mark_at_landing_vs_legacy():
    rows, b, s = make()
    er = psc.last_before(rows, int(b["landed_slot"]))
    q = psc.sim_buy(er, int(b["spend_lamports"]))
    new = psc.live_position_landed(b, rows, er, q)
    old = psc.live_position(b, er, q)
    first = next(r for r in rows if r["slot"] >= int(b["landed_slot"]))
    snap = psc.snap_of(first)
    assert new["mark_source"] == "landed_snapshot"
    assert new["mark"] == pytest.approx(psc.pcm.spot_sol_per_ui(snap.quote_priced, snap.base_reserve))
    assert new["mark_send"] == old["mark"] and "mark_source" not in old  # legacy keeps the send-state model
    fb = psc.live_position_landed(b, [], er, q)
    assert fb["mark_source"] == "fill_price" and fb["mark"] == pytest.approx(old["net_in"] / (old["tokens"] * 1000))


def test_pnl_parity_extra_cost_and_rent():
    rows, b, s = make()
    base = psc.simulate_trade({"buy": b, "sell": s}, rows, {M1})["sim_pnl_delayed_lamports"]
    b2 = {**b, "rent_charged_lamports": 2_039_280}
    s2 = {**s, "failed_attempt_cost_lamports": 7_000, "rent_refunded_lamports": 2_039_000}
    r = psc.simulate_trade({"buy": b2, "sell": s2}, rows, {M1})
    assert r["rent_net_lamports"] == 280
    assert r["sim_pnl_delayed_lamports"] == base - 7_000 - 280


def test_cli_flag_default_on(tmp_path):
    rows, b, s = make()
    d = tmp_path / "tape"
    write_tape(d, rows, T_AFTER)
    f = fills_file(tmp_path, [b, s])
    assert psc.main(["--fills", str(f), "--tape-dir", str(d), "--out-dir", str(tmp_path / "o1")]) == 0
    assert json.loads((tmp_path / "o1/calibration.json").read_text())["own_trade_in_tape"] is True
    assert json.loads((tmp_path / "o1/calibration.json").read_text())["schema_version"] == psc.SCHEMA_VERSION == 2
    psc.main(["--fills", str(f), "--tape-dir", str(d), "--out-dir", str(tmp_path / "o2"), "--no-own-trade-in-tape"])
    assert json.loads((tmp_path / "o2/calibration.json").read_text())["own_trade_in_tape"] is False
    assert "Sim reproduces the executor's exit decisions on 1/1 (n=1)" in (tmp_path / "o1/calibration.md").read_text()
