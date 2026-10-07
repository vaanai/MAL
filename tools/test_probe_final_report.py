"""Offline test for probe_final_report on a tiny synthetic ledger. No RPC, no key."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_final_report as pf  # noqa: E402

T1 = 1791241796000 + 3_600_000  # faa3192 era
T0 = 1791211794564               # 8a6849b era


def buy(ts, mint, slot=100, between=2):
    return {"mode": "live", "kind": "buy", "ts_ms": ts, "mint": mint, "landed": True, "spend_lamports": 50_000_000,
            "base_fee_lamports": 5000, "priority_fee_lamports": 500_000, "pool_fee_est_lamports": 600_000,
            "rent_charged_lamports": 1_513_840, "landed_slot": slot + between, "slots_between": between,
            "snapshot_slot": slot, "decision_t_ms": ts - 1500, "confirm_seen_ms": ts - 250,
            "ms_decision_to_send": 200, "ms_send_to_confirm": 1100}


def sell(ts, mint, reason, pnl, snap, landed, ret, recv=50_000_000):
    return {"mode": "live", "kind": "sell", "ts_ms": ts, "mint": mint, "landed": True, "exit_reason": reason,
            "pnl_lamports": pnl, "snapshot_slot": snap, "landed_slot": landed, "ret": ret,
            "ret_exit_vs_entry_actual": ret * 10000, "exit_vs_quote_bps": -10.0, "sol_received_lamports": recv,
            "base_fee_lamports": 5000, "priority_fee_lamports": 500_000, "rent_refunded_lamports": 1_513_840,
            "failed_attempt_cost_lamports": 0, "ms_decision_to_send": 30, "ms_send_to_confirm": 500}


ROWS = [
    buy(T0, "A"), sell(T0 + 30_000, "A", "sl", -20_000_000, 200, 204, -0.35),
    buy(T1, "B"), sell(T1 + 30_000, "B", "tp", 25_000_000, 300, 301, 0.5),
    buy(T1 + 60_000, "C"), sell(T1 + 90_000, "C", "sl", -30_000_000, 400, 402, -0.6),
    {"mode": "live", "kind": "buy", "ts_ms": T1 + 100_000, "mint": "D", "landed": False, "err": {"x": 1}, "cost_lamports": 505_000},
    {"mode": "live", "kind": "skip", "ts_ms": T1 + 200_000, "mint": "E", "reason": "limit:stop_file"},
    {"mode": "dryrun", "kind": "buy", "ts_ms": T1, "mint": "Z"},
]


def test_report(tmp_path):
    p = tmp_path / "f.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n")
    rows, sha = pf.load(str(p))
    rep = pf.build_report(rows, sha256=sha)
    assert rep["n_round_trips"] == 3 and rep["failed_buys"] == 1
    pb = rep["per_build"]
    assert pb["8a6849b"]["n_round_trips"] == 1 and pb["8a6849b"]["sl"] == 1
    assert pb["faa3192"]["tp"] == 1 and pb["faa3192"]["realized_lamports"] == -5_000_000
    assert pb["faa3192"]["realized_incl_failed_buys_lamports"] == -5_505_000
    assert pb[pf.ALL]["realized_lamports"] == -25_000_000
    assert rep["skips"] == {"limit:stop_file": 1}
    # exit lag in slots = landed - snapshot
    el = rep["exit_latency"]["faa3192"]
    assert el["exit_lag_slots_all"]["p50"] == 1 and el["exit_lag_slots_all"]["max"] == 2
    assert el["sl_filled_worse_than_minus_50pct"] == 1
    # 8a6849b has no failed buy: fixed 1.01M + pool buy 0.6M + sell est (0.6M/50M * 50M = 0.6M)
    fs = rep["fee_split"]["8a6849b"]
    assert fs["rent_net_unrefunded"] == 0 and fs["round_trip_cost"] == 1_010_000 + 600_000 + 600_000
    assert abs(fs["pct_of_stake"]["round_trip_cost"] - 4.42) < 1e-9
    proj = fs["projection_pct_of_stake"]
    assert abs(proj["0.5"] - (1_010_000 * 0.1 + 1_200_000) / 50_000_000 * 100) < 1e-9
    assert "| faa3192 |" in pf.markdown(rep)
    assert len(sha) == 64


def test_percentile_nearest_rank():
    assert pf.pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert pf.pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) == 9
    assert pf.pct([], 50) is None


def test_build_of():
    assert pf.build_of(0) == "8a6849b" and pf.build_of(1791241796000) == "faa3192"


def test_boundary_a25eb17_is_1753_utc():
    assert pf.build_of(1791222783000 - 1) == "8a6849b" and pf.build_of(1791222783000) == "a25eb17"


def test_fee_vs_price_and_true_lag():
    rows = [dict(r) for r in ROWS]
    for r in rows:
        if r["kind"] == "buy" and r.get("landed"):
            r["fee_lamports"] = 505_000
        if r["kind"] == "sell":
            r["fee_lamports"] = 505_000
    trips, _, _ = pf.pair_trips(rows)
    fp = pf.fee_vs_price(trips, pf.BUILDS, cut=1)
    assert fp["faa3192"]["realized_lamports"] == -5_000_000 and fp["faa3192"]["tx_fees_lamports"] == 2_020_000
    assert fp["faa3192"]["before_tx_fees_lamports"] == -2_980_000
    assert fp["faa3192 first 1"]["n"] == 1 and fp["faa3192 after the first 1"]["n"] == 1
    cal = {"trades": [{"mint": "B", "buy_ts_ms": T1, "build": "faa3192",
                       "variants": {"sim_correct": {"trigger_slot": 299, "reason_agree": True},
                                    "live_correct": {"trigger_slot": 300, "reason_agree": True}}}]}
    tl = pf.true_exit_lag(cal, rows)
    assert tl["sim_correct"]["faa3192"]["lag_all"]["p50"] == 2     # landed 301 - 299
    assert tl["live_mark"]["faa3192"]["lag_all"]["p50"] == 1
    assert tl["live_mark"]["faa3192"]["trigger_slot_diff"]["p50"] == 0
