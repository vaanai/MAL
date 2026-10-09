"""Tests for tools/cap_pick_score.py on tiny synthetic paths. Nothing here reads /data/mal."""

from __future__ import annotations

import argparse
import builtins
import contextlib
import csv
import dataclasses
import io
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from tools import cap_pick_score as cps
from tools.paper_price_path import pumpswap_post_trade_reserves

V = 17_600_000_000.0
Q0 = cps.SEED_Q + V  # seed pool quote incl. V
SEED_P = Q0 / cps.SEED_B
CFG = cps.Config()
SPH_HALF = {"d": 7200.0}  # 0.5 s per slot: k = round(1.3 / 0.5) = 3, 300 s = 600 slots
SPH_FAST = {"d": 18000.0}  # 0.2 s per slot: 1.3 s = 6.5 slots


def states(factors):
    """(qpre, bpre) with the quote fixed and the price = factor x the seed price. Entry i is the state BEFORE print i; the last entry is after the last print."""
    f = np.asarray(factors, float)
    return np.full(f.size, Q0), cps.SEED_B / f


def attempt(slot, factors, cfg=CFG, sph=SPH_HALF, s0=100, isbuy=None, sol=None, hour_s=None, bt=None, v=V):
    qpre, bpre = states(factors)
    slot = np.asarray(slot, np.int64)
    n = slot.size
    assert qpre.size == n + 1
    return cps.simulate_attempt(
        cfg, day="d", sph=sph, v=v, s0=s0, slot=slot,
        isbuy=np.ones(n, bool) if isbuy is None else np.asarray(isbuy, bool),
        sol=np.full(n, 1e9) if sol is None else np.asarray(sol, float), qpre=qpre, bpre=bpre,
        hour="d-hour", hour_s=hour_s, bt=None if bt is None else np.asarray(bt, np.int64),
    )


def buy_and_sell(factors, je, fi, cfg=CFG):
    qpre, bpre = states(factors)
    f = cps.fee_ppm(qpre[je], bpre[je])
    net = cfg.size_lamports * (1 - f / 1e6)
    tokens = bpre[je] * net / (qpre[je] + net)
    qa, ba = qpre[fi] + net, bpre[fi] - tokens
    gross = tokens * qa / (ba + tokens)
    return float(gross * (1 - cps.fee_ppm(qa, ba) / 1e6)) - cfg.size_lamports - 2 * cfg.fee_lamports


# --- k mapping -------------------------------------------------------------------------------------------------------------------------


def test_k_mapping_matches_the_prereg():
    sph = cps.SLOTS_PER_HOUR
    assert cps.k_for("2026-08-15", 1.3, sph) == 3  # ~416 ms
    assert cps.k_for("2026-08-21", 1.3, sph) == 4  # ~366 ms
    assert cps.k_for("2026-09-03", 1.3, sph) == 4  # ~316 ms
    assert cps.k_for("2026-09-23", 1.3, sph) == 5  # ~265 ms
    assert cps.k_for("2026-09-18", 1.3, sph) == 5  # 09-18 has its own entry (the single hour 23)
    assert cps.k_for("d", 1.3, SPH_FAST) == 6  # 6.5 slots: Python banker's rounding, the audit's rule (phase 2 pins an explicit rule)
    with pytest.raises(cps.Refused):
        cps.k_for("2026-01-01", 1.3, sph)


def test_k_sets_the_landing_slot():
    r = attempt([100, 600], [1.0, 1.0, 1.0])
    assert r["k"] == 3 and r["landing_slot"] == 103
    r2 = attempt([100, 600], [1.0, 1.0, 1.0], cfg=cps.Config(k_seconds=0.8))
    assert r2["k"] == 2 and r2["landing_slot"] == 102  # round(0.8 / 0.5) = round(1.6)


# --- guard -----------------------------------------------------------------------------------------------------------------------------


def test_guard_blocks_above_1_15_and_passes_below():
    ok = attempt([100], [1.10, 1.10])  # exec ratio ~1.106
    assert ok["status"] == "filled" and ok["exec_ratio"] < 1.15
    bad = attempt([100], [1.20, 1.20])  # exec ratio ~1.207
    assert bad["status"] == "guarded" and bad["exec_ratio"] > 1.15
    assert bad["pnl"] == -float(CFG.fee_lamports) and bad["exit_type"] == "guard" and bad["hold_slots"] == 0
    # the boundary is on the net execution price against the seed price x 1.15, not on spot
    for f in (1.140, 1.144, 1.150, 1.160):
        r = attempt([100], [f, f])
        assert (r["status"] == "guarded") == (r["exec_ratio"] > 1.15)
    assert attempt([100], [1.140, 1.140])["status"] == "filled" and attempt([100], [1.160, 1.160])["status"] == "guarded"
    # no guard: the same state fills
    nog = attempt([100], [1.20, 1.20], cfg=cps.Config(guard="none"))
    assert nog["status"] == "filled"
    # a looser ratio lets it through
    assert attempt([100], [1.20, 1.20], cfg=cps.Config(guard_ratio=1.30))["status"] == "filled"


def test_guard_uses_the_seed_price_with_v():
    # price at the seed = (67,405,853,863 + V) / 206.9e12; at factor 1.0 the exec ratio is just the buy's own impact (>1, well under 1.15)
    r = attempt([100], [1.0, 1.0])
    assert 1.0 < r["exec_ratio"] < 1.01
    assert r["spot_drift"] == pytest.approx(0.0, abs=1e-12)


# --- END bound -------------------------------------------------------------------------------------------------------------------------


def test_end_bound_prices_after_every_print_in_the_landing_slot():
    slot = [100, 103, 103, 110]
    # state before each print: 1.0 (s0), 1.0 (first print in slot 103), 1.0 (second print in slot 103), 1.0 (slot 110); after the last: 1.0
    # make the second print in slot 103 push the price: the state after it (= before slot 110) is 1.3 x seed
    factors = [1.0, 1.0, 1.0, 1.3, 1.3]
    end = attempt(slot, factors, cfg=cps.Config(bound="END"))
    start = attempt(slot, factors, cfg=cps.Config(bound="START"))
    assert end["status"] == "guarded"  # landing state = before the first print with slot >= 104 -> 1.3
    assert start["status"] == "filled"  # landing state = before the first print with slot >= 103 -> 1.0
    # START takes lag with no +1: the exit fill is the state before the first print with slot >= trigger + lag
    assert start["landing_slot"] == end["landing_slot"] == 103


# --- cap and exit lag ------------------------------------------------------------------------------------------------------------------


def test_wall_clock_cap_deadline_fill_is_the_state_before_the_first_print_after_cap_plus_lag():
    slot = [100, 600, 700, 800]
    factors = [1.0, 1.0, 1.0, 1.02, 1.02]  # no tp / sl anywhere
    r = attempt(slot, factors)
    # X = 103, D = X + round(300 / 0.5) = 703; trigger prints are those with slot < 703; deadline fill = first print with slot >= 703 + 2 + 1 -> index 3
    assert r["exit_type"] == "deadline" and r["hold_slots"] == 600
    assert r["pnl"] == pytest.approx(buy_and_sell(factors, 1, 3))
    assert r["pnl"] != pytest.approx(buy_and_sell(factors, 1, 2))
    # a 200 s cap: D = 103 + 400 = 503, fill at the first print with slot >= 506 -> index 1
    r2 = attempt(slot, factors, cfg=cps.Config(cap_seconds=200))
    assert r2["exit_type"] == "deadline" and r2["hold_slots"] == 400
    assert r2["pnl"] == pytest.approx(buy_and_sell(factors, 1, 1))


def test_a_trigger_after_the_cap_does_not_fire():
    slot = [100, 600, 700, 800]
    factors = [1.0, 1.0, 1.7, 1.7, 1.7]  # the print at slot 600 pushes the price +70%
    capped = attempt(slot, factors, cfg=cps.Config(cap_seconds=200))  # D = 503 < 600: the print is after the cap
    assert capped["exit_type"] == "deadline"
    full = attempt(slot, factors)  # D = 703: the print at 600 is inside the cap -> tp
    assert full["exit_type"] == "tp" and full["hold_slots"] == 600 - 103


def test_exit_lag_moves_the_fill_to_a_later_state():
    slot = [100, 600, 700, 800]
    factors = [1.0, 1.0, 1.7, 1.9, 1.9]  # tp triggers on the print at slot 600 (post-state = before print idx 2 = 1.7)
    lag2 = attempt(slot, factors, cfg=cps.Config(exit_lag=2))  # first print with slot >= 600 + 2 + 1 = 603 -> idx 2 (slot 700)
    lag100 = attempt(slot, factors, cfg=cps.Config(exit_lag=100))  # slot >= 701 -> idx 3 (slot 800)
    assert lag2["exit_type"] == lag100["exit_type"] == "tp"
    assert lag2["pnl"] == pytest.approx(buy_and_sell(factors, 1, 2))
    assert lag100["pnl"] == pytest.approx(buy_and_sell(factors, 1, 3))
    assert lag100["pnl"] > lag2["pnl"]
    # START bound: no +1
    s_lag2 = attempt(slot, factors, cfg=cps.Config(exit_lag=2, bound="START"))  # fill = first print with slot >= 600 + 2 = 602 -> idx 2; landing = before slot 103 -> idx 1
    assert s_lag2["exit_type"] == "tp" and s_lag2["pnl"] == pytest.approx(buy_and_sell(factors, 1, 2))


def test_stop_loss_fires_and_is_typed_sl():
    slot = [100, 600, 700]
    factors = [1.0, 1.0, 0.6, 0.6]  # -40% on the print at 600
    r = attempt(slot, factors)
    assert r["exit_type"] == "sl" and r["pnl"] < 0


# --- per-attempt accounting ------------------------------------------------------------------------------------------------------------


def test_fill_pnl_is_sell_minus_size_minus_two_fees():
    slot = [100, 600]
    factors = [1.0, 1.0, 1.0]
    r = attempt(slot, factors)
    assert r["status"] == "filled"
    assert r["pnl"] == pytest.approx(buy_and_sell(factors, 1, 2))
    # a flat market loses about the two pool fees and two send fees
    assert r["pnl"] < -2 * CFG.fee_lamports


def test_apply_legs_live_flat_pressure_and_guarded_rows():
    rows = []
    for i in range(40):
        rows.append(dict(status="filled", pnl=-1_000_000.0 + 50_000.0 * i, fee=55_000, ssb=i % 7, nearby_lamports=float(1e9 * (i % 5)), exit_type="tp"))
    rows.append(dict(status="guarded", pnl=-55_000.0, fee=55_000, ssb=3, nearby_lamports=2e9, exit_type="guard"))
    info = cps.apply_legs(rows, CFG)
    r = rows[3]
    assert r["pnl_nofail"] == r["pnl"]
    assert r["pnl_live"] == pytest.approx((1 - 1 / 62) * r["pnl"] + (1 / 62) * (-55_000))
    assert r["pnl_flat"] == pytest.approx(0.85 * r["pnl"] + 0.15 * (-55_000))
    fills = [x for x in rows if x["status"] == "filled"]
    assert sum(x["p_press"] for x in fills) / len(fills) == pytest.approx(0.289, abs=1e-9)  # intercept fitted on the guard-passed sends
    assert info["pressure_mean_p"] == pytest.approx(0.289, abs=1e-9)
    g = rows[-1]  # a guarded-out buy is -fee on every leg and has no fail mix
    assert g["pnl_live"] == g["pnl_flat"] == g["pnl_press"] == g["pnl_nofail"] == -55_000.0 and g["p_press"] == 0.0
    # more pressure (more same-slot buys, more nearby SOL) -> higher p
    assert rows[34]["p_press"] > rows[0]["p_press"]  # i=34: ssb 6, nearby 4e9 against i=0: ssb 0, nearby 0


def test_pressure_inputs_count_buys_in_the_landing_slot_and_two_seconds_before():
    slot = [100, 101, 103, 103, 104, 700]
    isbuy = [True, True, True, False, True, True]
    sol = [1e9, 2e9, 4e9, 8e9, 16e9, 32e9]
    factors = [1.0] * 7
    r = attempt(slot, factors, isbuy=isbuy, sol=sol)
    # X = 103, 2 s = 4 slots -> window starts at slot 99; slots <= 103 count: buys 1+2+4 (the sell at 103 is not a buy, the print at 104 is after X)
    assert r["ssb"] == 1  # buys inside the landing slot only (idx 2); the sell in the same slot is not a buy
    assert r["nearby_lamports"] == 7e9


# --- reserve conventions ---------------------------------------------------------------------------------------------------------------


def test_state_before_print_i_is_its_pre_trade_reserves_and_v_is_added_to_the_quote():
    slot = np.array([1, 2], np.int64)
    isbuy = np.array([True, False])
    sol = np.array([1e9, 5e8])
    tok = np.array([1e12, 4e11])
    q = np.array([30e9, 31e9])
    b = np.array([1.0e14, 0.99e14])
    qpre, bpre, fb = cps.build_states(slot, isbuy, sol, tok, q, b, V, "lab")
    assert fb == 0 and qpre.size == bpre.size == 3
    assert qpre[0] == 30e9 + V and bpre[0] == 1.0e14  # pre-trade of print 0, plus V on the quote only
    assert qpre[1] == 31e9 + V and bpre[1] == 0.99e14  # the state after print 0 is the NEXT print's pre-trade reserves (observed chain)
    # the state after the last (sell) print is derived with the lab's function: quote down, base up
    assert qpre[2] < qpre[1] and bpre[2] == 0.99e14 + 4e11


def test_final_state_uses_the_lab_pumpswap_handling_and_g_keeps_its_constant():
    q, b, sol, tok = 30e9, 1.0e14, 1e9, 1e12
    lab = cps.final_state(True, q, b, sol, tok, "lab")
    fee_ppm = 12_500  # price_sol = q / (b * 1000) = 3e-7 -> mcap 300 SOL -> the 0..420 tier
    ref = pumpswap_post_trade_reserves(side="buy", quote_reserve=int(q), base_reserve=int(b), sol_lamports=int(sol), token_raw=int(tok), fee_ppm=fee_ppm)
    assert lab == (float(ref[0]), float(ref[1]))
    assert lab[0] == q + sol * (1 - 0.0125) and lab[1] == b - tok  # at the 1.25% tier the two agree
    g = cps.final_state(True, q, b, sol, tok, "g")
    assert g == (q + sol * (1 - 0.0125), b - tok)
    gs = cps.final_state(False, q, b, sol, tok, "g")
    assert gs == (q - sol / (1 - 0.0125), b + tok)


def test_within_slot_order_is_tx_then_event_and_file_order_when_tx_is_null():
    rows = [(7, 5, 1, 1), (7, 3, 2, 2), (7, 3, 0, 3), (6, 9, 9, 4)]
    assert sorted(rows, key=lambda r: cps.order_key(*r)) == [(6, 9, 9, 4), (7, 3, 0, 3), (7, 3, 2, 2), (7, 5, 1, 1)]
    # oracle-insample-0922: tx_index is null -> (slot, file order); event_index is ignored, as the audit did
    nul = [(7, None, 5, 1), (7, None, 0, 2), (7, None, 9, 3)]
    assert sorted(nul, key=lambda r: cps.order_key(*r)) == nul
    # the vectorised permutation used by the loader agrees with the reference key (tx = -1 is a null tx_index)
    mixed = [(7, 5, 1, 1), (7, 3, 2, 2), (7, 3, 0, 3), (6, 9, 9, 4), (8, -1, 4, 5), (8, -1, 0, 6), (7, 3, 0, 7)]
    cols = [np.array(c, dtype=np.int64) for c in zip(*mixed)]
    perm = cps.order_perm(*cols)
    ref = sorted(range(len(mixed)), key=lambda i: cps.order_key(mixed[i][0], None if mixed[i][1] < 0 else mixed[i][1], mixed[i][2], mixed[i][3]))
    assert perm.tolist() == ref


# --- hard limits -----------------------------------------------------------------------------------------------------------------------


def test_forbidden_paths_and_hours_are_refused():
    for p in ("/data/mal/clean-view/fresh-0802/w1", "/data/mal/blocks-clean/fresh-0808", "/x/fresh-0828/y", "/data/mal/forward/walk", "/data/mal/clean-view/oracle-live-2026-09-25_27"):
        with pytest.raises(cps.Refused):
            cps.check_path_allowed(p)
    cps.check_path_allowed("/data/mal/clean-view/explore-0814/w1")
    for h in ("2026-09-15T12", "2026-09-16T00", "2026-09-18T22", "2026-10-02T10", "2026-10-03T00"):
        with pytest.raises(cps.Refused):
            cps.check_hour_allowed(h)
    for h in ("2026-09-15T11", "2026-09-18T23", "2026-10-02T09", "2026-08-15T00"):
        cps.check_hour_allowed(h)


def _write_zst(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.with_suffix("")  # .jsonl
    raw.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))
    subprocess.run(["zstd", "-q", "-f", "--rm", "-o", str(path), str(raw)], check=True)


needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None or shutil.which("zstdcat") is None, reason="zstd not installed")


@needs_zstd
def test_index_hours_refuses_an_exp009_hour_and_a_forbidden_dir(tmp_path):
    view = tmp_path / "viewA"
    _write_zst(view / "trades" / "trades-2026-09-16T00.jsonl.zst", [{"venue": "pumpswap"}])
    with pytest.raises(cps.Refused):
        cps.index_hours([cps.Source("explore-0814", (view,))])
    bad = tmp_path / "fresh-0802" / "w1"
    (bad / "trades").mkdir(parents=True)
    with pytest.raises(cps.Refused):
        cps.expand_view_dirs(bad)


@needs_zstd
def test_oracle_insample_hours_stop_at_0925t06(tmp_path):
    view = tmp_path / "orc"
    for h in ("2026-09-25T06", "2026-09-25T07"):
        _write_zst(view / "trades" / f"trades-{h}.jsonl.zst", [{"venue": "pumpswap"}])
    idx = cps.index_hours([cps.Source(cps.BLOCK_P1C, (view,))])
    assert sorted(idx["trades"]) == ["2026-09-25T06"]


# --- end to end on a tiny lab-layout tree ----------------------------------------------------------------------------------------------


def _trade(mint, pool, slot, side, q, b, sol, tok, tx, ev=0, venue="pumpswap", bt=1):
    return {"v": 2, "venue": venue, "mint": mint, "trader": "t", "side": side, "sol_lamports": sol, "token_raw": tok, "quote_reserve": q, "base_reserve": b,
            "pool": pool, "slot": slot, "event_index": ev, "block_time": bt, "tx_index": tx}


@needs_zstd
def test_end_to_end_one_attempt_per_mint_and_filters(tmp_path):
    view = tmp_path / "viewA"
    q0, b0 = cps.SEED_Q, cps.SEED_B
    mints = {"MintOK": "PoolOK", "MintBand": "PoolOutOfBand", "MintLate": "PoolLate", "MintNoComplete": "PoolOK2"}
    mig = [{"type": "complete", "mint": m, "slot": 1000, "block_time": 10} for m in ("MintOK", "MintBand", "MintLate")]
    mig += [{"type": "create", "mint": "MintNoComplete", "slot": 1000}]
    _write_zst(view / "migrations" / "migrations-2026-08-15T00.jsonl.zst", mig)
    _write_zst(view / "migrations" / "migrations-2026-08-15T01.jsonl.zst", [{"type": "create", "mint": "Other", "slot": 9000}])  # every trade hour needs a migrations file
    t00 = [
        # MintOK: s0 = 1001; prints out of order on purpose (tx_index decides inside the slot)
        _trade("MintOK", "PoolOK", 1001, "buy", q0, b0, 10**9, 10**12, tx=1),
        _trade("MintOK", "PoolOK", 1005, "buy", q0 + 10**9, b0 - 10**12, 10**9, 10**12, tx=9),
        _trade("MintOK", "PoolOK", 1005, "sell", q0 + 2 * 10**9, b0 - 2 * 10**12, 5 * 10**8, 4 * 10**11, tx=3),
        _trade("MintOK", "PoolOK", 1700, "buy", q0 + 2 * 10**9, b0 - 2 * 10**12, 10**9, 10**12, tx=1),
        _trade("MintBand", "PoolOutOfBand", 1001, "buy", q0, b0, 10**9, 10**12, tx=1),
        _trade("MintLate", "PoolLate", 8000, "buy", q0, b0, 10**9, 10**12, tx=1),  # s0 - mslot > 400: censored
        _trade("MintNoComplete", "PoolOK2", 1001, "buy", q0, b0, 10**9, 10**12, tx=1),
        _trade("MintOK", None, 1002, "buy", q0, b0, 10**9, 10**12, tx=1, venue="pump_bonding"),
    ]
    _write_zst(view / "trades" / "trades-2026-08-15T00.jsonl.zst", t00)
    # a later hour only sets the max slot of the day's tape
    _write_zst(view / "trades" / "trades-2026-08-15T01.jsonl.zst", [_trade("Other", "X", 1001 + 6900 + 5, "buy", q0, b0, 1, 1, tx=1, venue="pump_bonding")])
    vmap = tmp_path / "v.json"
    vmap.write_text(json.dumps({"v": {"PoolOK": 17_600_000_000, "PoolOutOfBand": 18_000_000_000, "PoolLate": 17_600_000_000, "PoolOK2": 17_600_000_000}}))
    args = argparse.Namespace(p1_fast_dir=None, p1_oracle_insample_dir=None, p2_view_dir=[str(view)], p3_root=None, p4_view_dir=None, vmap=str(vmap), picks=None,
                              only_day=["2026-08-15"], sph_json=None)
    s = cps.run(args, cps.Config(), lambda m: None)
    rows = s.pop("_rows")
    # only the canonical in-band mint with a `complete` row and a first print within 400 slots of it is an attempt
    assert [r["mint"] for r in rows] == ["MintOK"]
    r = rows[0]
    assert r["day"] == "2026-08-15" and r["block"] == "explore-0814" and r["s0"] == 1001 and r["mslot"] == 1000 and r["k"] == 3 and r["landing_slot"] == 1004
    assert r["status"] in ("filled", "guarded")
    assert s["counts"]["attempts"] == 1 and s["counts"]["censored"] == 1
    # within-slot order is (tx_index): the slot-1005 sell (tx 3) is before the buy (tx 9)
    meta, _ = cps.read_day("2026-08-15", cps.index_hours(cps.build_sources(args)), cps.load_vband(vmap, cps.V_LO, cps.V_HI), cps.Config(), lambda m: None)
    assert meta["MintOK"]["buy"].tolist() == [True, False, True, True]
    assert meta["MintOK"]["v"] == 17_600_000_000.0
    out = tmp_path / "out"
    s["_rows"] = rows
    cps.write_outputs(s, out)
    assert (out / "rows.csv").read_text().splitlines()[0].startswith("day,block,mint")
    assert json.loads((out / "summary.json").read_text())["schema"] == cps.SCHEMA


def test_main_refuses_without_a_source(tmp_path, capsys):
    assert cps.main(["--out-dir", str(tmp_path / "o")]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_config_defaults_are_g_p_primary():
    c = cps.Config()
    assert (c.k_seconds, c.bound, c.exit_lag, c.guard, c.guard_ratio, c.cap_seconds) == (1.3, "END", 2, "min_out", 1.15, 300.0)
    assert (c.tp, c.sl, c.size_lamports, c.fee_lamports, c.target_fail) == (0.5, 0.3, 500_000_000, 55_000, 0.289)
    assert c.live_fail == 1 / 62 and c.flat_fail == 0.15 and c.final_state == "lab"


# =====================================================================================================================================
# Phase 2: the judge's spec switches (capv_JUDGE.md section 4). Every switch defaults to G.
# =====================================================================================================================================

# --- P2-1: per-hour ms/slot, rounding rule, k mode ---------------------------------------------------------------------------------------


def test_slots_for_round_is_python_round_and_ceil_is_explicit():
    assert cps.slots_for(1.3, 0.2, "round") == 6  # 6.5 slots: Python round is half to even, G's rule
    assert cps.slots_for(1.3, 0.2, "ceil") == 7
    assert cps.slots_for(1.3, 0.4, "round") == 3 and cps.slots_for(1.3, 0.4, "ceil") == 4  # 3.25
    assert cps.slots_for(1.2, 0.4, "ceil") == 3  # 3.0000000000000004 in floats: the 1e-9 tolerance keeps it at 3
    assert cps.slots_for(1.3, 0.26, "round") == 5 and cps.slots_for(1.3, 0.26, "ceil") == 5  # exactly 5
    assert cps.slots_for(0.0, 0.4, "ceil") == 0
    with pytest.raises(cps.Refused):
        cps.slots_for(1.3, 0.4, "floor")
    # the day-mode k is G's k_for, bit for bit
    for day in ("2026-08-15", "2026-09-03", "2026-09-23"):
        assert cps.slots_for(1.3, cps.sec_per_slot(day, cps.SLOTS_PER_HOUR)) == cps.k_for(day, 1.3, cps.SLOTS_PER_HOUR)


def test_k_mode_hour_uses_the_hours_clock_and_day_mode_ignores_it():
    slot, factors = [100, 110, 700], [1.0, 1.0, 1.0, 1.0]
    day = attempt(slot, factors)  # SPH_HALF: 0.5 s/slot -> k = round(2.6) = 3
    assert day["k"] == 3 and day["landing_slot"] == 103
    assert attempt(slot, factors, hour_s=0.2)["k"] == 3  # --k-mode day never looks at the hour
    hr = attempt(slot, factors, cfg=cps.Config(k_mode="hour"), hour_s=0.2)  # 1.3 / 0.2 = 6.5 -> round -> 6
    assert hr["k"] == 6 and hr["landing_slot"] == 106
    hc = attempt(slot, factors, cfg=cps.Config(k_mode="hour", k_rounding="ceil"), hour_s=0.2)
    assert hc["k"] == 7 and hc["landing_slot"] == 107
    # day mode with ceil: 2.6 -> 3, and 1.3 s at 0.4 s/slot is 3.25 -> 4
    assert attempt(slot, factors, cfg=cps.Config(k_rounding="ceil"))["k"] == 3
    assert attempt(slot, factors, cfg=cps.Config(k_rounding="ceil"), sph={"d": 9000.0})["k"] == 4
    # hour mode needs a measured hour
    with pytest.raises(cps.Refused):
        attempt(slot, factors, cfg=cps.Config(k_mode="hour"))
    # hour mode does not need the day table (the cap is day-mean, so only that day entry is read)
    with pytest.raises(cps.Refused):
        attempt(slot, factors, cfg=cps.Config(k_mode="hour"), sph={}, hour_s=0.2)
    ok = attempt(slot, factors, cfg=cps.Config(k_mode="hour", cap_anchor="block-time"), sph={}, hour_s=0.2, bt=[1000, 1010, 1300])
    assert ok["k"] == 6 and ok["cap_anchor_used"] == "block-time"


def test_the_entry_anchor_is_s0_not_the_migrate_slot():
    r = attempt([100, 110, 700], [1.0] * 4, s0=100)
    assert r["landing_slot"] == 103  # s0 + k; the migrate slot (mslot) never enters the landing slot
    r2 = attempt([100, 110, 700], [1.0] * 4, s0=99)
    assert r2["landing_slot"] == 102


def test_hour_sph_from_points_and_sanity_bounds():
    assert cps.hour_sph_from_points(1000, 5000, 1000 + 9000 * 3550 // 3600, 5000 + 3550) == pytest.approx(9000.0, rel=1e-3)
    assert cps.hour_sph_from_points(1000, 5000, 2000, 5000 + 1799) is None  # under 1,800 s of block_time between the points
    assert cps.hour_sph_from_points(1000, 5000, 1000, 8000) is None
    assert cps.hour_sph_from_points(1000, 5000, 2000, 8600) is None  # 1,000 slots in 3,600 s is not a slot clock


def _hour_rows(n, s_per_s=2.5, t0=1_786_791_600, s0=439_420_961, with_bt=True, pad=100):
    rows = []
    for i in range(n):
        dt = i * 3599.0 / (n - 1)
        r = {"venue": "pumpswap", "mint": "M", "slot": s0 + int(s_per_s * dt), "pad": "x" * pad}
        if with_bt:
            r["block_time"] = t0 + int(dt)
        rows.append(r)
    return rows


@needs_zstd
def test_measure_hour_sph_reads_the_head_and_the_tail_of_the_hour_file(tmp_path):
    f = tmp_path / "trades-2026-08-15T11.jsonl.zst"
    _write_zst(f, _hour_rows(3500))  # about 500 kB: the tail window cuts a row in half
    assert f.exists() and cps.HOUR_TAIL_BYTES < 3500 * 140
    m = cps.measure_hour_sph(f)
    assert m == pytest.approx(9000.0, rel=0.002)
    # 200 ms slots: 5 slots a second
    g = tmp_path / "trades-2026-08-15T12.jsonl.zst"
    _write_zst(g, _hour_rows(800, s_per_s=5.0))
    assert cps.measure_hour_sph(g) == pytest.approx(18000.0, rel=0.002)
    # no block_time on the rows, or a file that spans too little of the hour: not measurable
    h = tmp_path / "trades-2026-08-15T13.jsonl.zst"
    _write_zst(h, _hour_rows(300, with_bt=False))
    assert cps.measure_hour_sph(h) is None
    i = tmp_path / "trades-2026-08-15T14.jsonl.zst"
    _write_zst(i, [dict(r, block_time=1_786_791_600 + (j % 60)) for j, r in enumerate(_hour_rows(300))])
    assert cps.measure_hour_sph(i) is None


# --- P2-2: exit lag in ms ----------------------------------------------------------------------------------------------------------------

LAG_SLOTS = [100, 110, 600, 603, 604, 700]
LAG_FACTORS = [1.0, 1.0, 1.0, 1.7, 1.8, 1.9, 2.0]  # the tp triggers on the print at 600 (its post-state is the pre-state of the print at 603: +70%)


def test_exit_lag_ms_maps_to_slots_with_ceil_per_hour():
    def lag(ms, hour_s, **kw):
        return attempt(LAG_SLOTS, LAG_FACTORS, cfg=cps.Config(exit_lag_ms=ms, **kw), hour_s=hour_s)

    assert lag(550, 0.2)["exit_lag_slots"] == 3  # 2.75 -> 3
    assert lag(550, 0.4)["exit_lag_slots"] == 2  # 1.375 -> 2
    assert lag(550, 0.55)["exit_lag_slots"] == 1  # exactly 1.0: the tolerance keeps it at 1
    assert lag(1350, 0.2)["exit_lag_slots"] == 7  # 6.75 -> 7
    assert lag(0, 0.2)["exit_lag_slots"] == 0
    # the fill state moves with the lag: tp at slot 600, fill = first print with slot >= 600 + lag + 1
    for ms, hour_s, fi in ((550, 0.4, 3), (550, 0.2, 4), (1350, 0.2, 5)):  # lag 2 -> slot 603 (idx 3), lag 3 -> 604 (idx 4), lag 7 -> 608 -> slot 700 (idx 5)
        r = lag(ms, hour_s)
        assert r["exit_type"] == "tp" and r["pnl"] == pytest.approx(buy_and_sell(LAG_FACTORS, 1, fi))
    assert lag(550, 0.4)["pnl"] < lag(550, 0.2)["pnl"] < lag(1350, 0.2)["pnl"]  # the price keeps rising in this path


def test_exit_lag_in_slots_is_kept_when_ms_is_not_given():
    base = attempt(LAG_SLOTS, LAG_FACTORS)
    assert base["exit_lag_slots"] == 2 and base["pnl"] == pytest.approx(buy_and_sell(LAG_FACTORS, 1, 3))
    assert attempt(LAG_SLOTS, LAG_FACTORS, cfg=cps.Config(exit_lag=3))["exit_lag_slots"] == 3
    # with --exit-lag-ms the slot lag is ignored, and the hour clock is required (day mode k does not need it otherwise)
    assert attempt(LAG_SLOTS, LAG_FACTORS, cfg=cps.Config(exit_lag=9, exit_lag_ms=550), hour_s=0.2)["exit_lag_slots"] == 3
    with pytest.raises(cps.Refused):
        attempt(LAG_SLOTS, LAG_FACTORS, cfg=cps.Config(exit_lag_ms=550))


def test_the_deadline_sell_lands_at_cap_plus_the_exit_lag():
    slot = [100, 110, 400, 700, 703, 704, 900]
    factors = [1.0, 1.0, 1.0, 1.01, 1.02, 1.03, 1.04, 1.05]
    # day 0.5 s/slot: X = 103, D = 103 + 600 = 703; lag 3 (550 ms at 0.2 s) -> first print with slot >= 703 + 3 + 1 = 707 -> idx 6; lag 2 -> >= 706 -> idx 6
    r = attempt(slot, factors, cfg=cps.Config(exit_lag_ms=550), hour_s=0.2)
    assert r["exit_type"] == "deadline" and r["exit_lag_slots"] == 3 and r["hold_slots"] == 600
    assert r["pnl"] == pytest.approx(buy_and_sell(factors, 1, 6))
    r0 = attempt(slot, factors, cfg=cps.Config(exit_lag_ms=0), hour_s=0.2)  # lag 0 -> >= 704 -> idx 5
    assert r0["pnl"] == pytest.approx(buy_and_sell(factors, 1, 5))
    r1 = attempt(slot, factors, cfg=cps.Config(exit_lag=0))  # lag 0 in slots: same
    assert r1["pnl"] == pytest.approx(buy_and_sell(factors, 1, 5))


# =====================================================================================================================================
# A multi-mint, two-day fixture on the lab layout. Used by the defaults-are-phase-1 golden test and the switch tests below.
# =====================================================================================================================================

FIX_DAYS = ("2026-08-15", "2026-08-16")  # both in the day table: 8657.5 / 8657.0 slots per hour -> 0.416 s per slot, k = 3, 300 s = 721 slots
FIX_BASE_SLOT = {"2026-08-15": 1_000_000, "2026-08-16": 1_400_000}
FIX_BT0 = 1_786_000_000
FIX_BT_S_PER_SLOT = 0.4  # the fixture's chain clock: 300 s = 750 slots here against 721 in the day table


def _fixture_paths():
    """{mint: (day, pool, v, [(slot, side, factor, sol, tok)])}: a seeded random walk of the price factor against the seed price, per mint."""
    import random

    rng = random.Random(20261008)
    out = {}
    n = 0
    for day in FIX_DAYS:
        for _i in range(8):
            n += 1
            mint, pool = f"Mint{n:02d}", f"Pool{n:02d}"
            v = 17_550_000_000 + 10_000_000 * ((n * 7) % 10)
            base = FIX_BASE_SLOT[day]
            slot = base + 1 + rng.randrange(0, 3)  # s0: the first PumpSwap print
            f = 0.95 + 0.25 * rng.random()
            sigma = 0.02 + 0.05 * rng.random()
            drift = rng.choice((-0.004, 0.0, 0.004, 0.01))
            prints = []
            while slot < base + 1100:
                side = rng.choice(("buy", "buy", "sell"))
                prints.append((slot, side, f, int((0.05 + 3.0 * rng.random()) * 1e9), int((1.0 + 400.0 * rng.random()) * 1e9)))
                f = max(0.2, f * (1.0 + drift + sigma * rng.gauss(0, 1)))
                slot += rng.choice((0, 0, 1, 2, 5, 11, 19))
            prints.append((base + 7500, "buy", f, 10**9, 10**9))  # a late print: s0 + 6900 <= the day's max slot, so the mint is uncensored
            out[mint] = (day, pool, v, prints)
    return out


def write_fixture(tmp_path, with_bt=True):
    """The lab layout under tmp_path/viewA: migrations + trades hour files for 2026-08-15 and 08-16 (hours 00 and 01; hour 01 only sets the day's max slot).
    Returns (view dir, v-map path)."""
    view = tmp_path / "viewA"
    paths = _fixture_paths()
    vmap = {}
    for day in FIX_DAYS:
        base = FIX_BASE_SLOT[day]
        mig, trades = [], []
        for mint, (d, pool, v, prints) in paths.items():
            if d != day:
                continue
            vmap[pool] = v
            mig.append({"type": "complete", "mint": mint, "slot": base, "block_time": FIX_BT0 + int(base * FIX_BT_S_PER_SLOT)})
            for tx, (slot, side, f, sol, tok) in enumerate(prints, 1):
                q = (cps.SEED_Q + v) * f - v  # quote reserve without V; the price factor f is against the seed price
                bt = FIX_BT0 + int(slot * FIX_BT_S_PER_SLOT) if with_bt else None
                trades.append(_trade(mint, pool, slot, side, int(q), cps.SEED_B, sol, tok, tx=tx, bt=bt))
        _write_zst(view / "migrations" / f"migrations-{day}T00.jsonl.zst", mig)
        _write_zst(view / "migrations" / f"migrations-{day}T01.jsonl.zst", [{"type": "create", "mint": "Other", "slot": 1}])
        _write_zst(view / "trades" / f"trades-{day}T00.jsonl.zst", trades)
        _write_zst(view / "trades" / f"trades-{day}T01.jsonl.zst", [_trade("Other", "X", base + 7600, "buy", 1, 1, 1, 1, tx=1, venue="pump_bonding")])
    vpath = tmp_path / "v.json"
    vpath.write_text(json.dumps({"v": vmap}))
    return view, vpath


def fix_args(view, vpath, **kw):
    d = dict(p1_fast_dir=None, p1_oracle_insample_dir=None, p2_view_dir=[str(view)], p3_root=None, p4_view_dir=None, vmap=str(vpath), picks=None, only_day=None,
             sph_json=None, hour_sph_json=None)
    d.update(kw)
    return argparse.Namespace(**d)


def run_fix(view, vpath, cfg=None, **kw):
    s = cps.run(fix_args(view, vpath, **kw), cfg or cps.Config(), lambda m: None)
    return s, s.pop("_rows")


# --- P2-3: guard basis -----------------------------------------------------------------------------------------------------------------

GROSS = cps.Config(guard_basis="gross")


def _tokens_out(f):
    """tokens out of the landing buy at price factor f (the same state before and after the one print), computed independently of simulate_attempt."""
    qpre, bpre = states([f, f])
    fee = float(cps.fee_ppm(qpre[0], bpre[0])) / 1e6
    net = CFG.size_lamports * (1 - fee)
    return float(bpre[0] * net / (qpre[0] + net)), fee


def test_guard_basis_defaults_to_net_and_gross_is_stricter_by_the_pool_fee():
    assert cps.Config().guard_basis == "net"
    band = []
    for i in range(0, 120):
        f = 1.100 + 0.0005 * i
        a, b = attempt([100], [f, f]), attempt([100], [f, f], cfg=GROSS)
        assert a["exec_ratio"] == b["exec_ratio"] and a["exec_ratio_gross"] == b["exec_ratio_gross"]
        fee = _tokens_out(f)[1]
        assert fee > 0 and a["exec_ratio_gross"] == pytest.approx(a["exec_ratio"] / (1 - fee), rel=1e-12)
        assert (a["status"] == "guarded") == (a["exec_ratio"] > 1.15)  # net: G's test
        if a["status"] == "filled" and b["status"] == "guarded":
            band.append(f)
        assert not (a["status"] == "guarded" and b["status"] == "filled")  # gross rejects everything net rejects
    assert len(band) > 5 and min(band) < 1.14 and max(band) > 1.14  # a visible band the net guard passes and the executor's gross min_out refuses
    # the band is exactly where  exec_ratio * (1 - 0) <= 1.15 < exec_ratio_gross
    for f in band:
        r = attempt([100], [f, f])
        assert r["exec_ratio"] <= 1.15 < r["exec_ratio_gross"]


def test_gross_guard_is_the_integer_min_out_test():
    seed_p = SEED_P
    for i in range(0, 120):
        f = 1.100 + 0.0005 * i
        tokens, _fee = _tokens_out(f)
        min_out = math.ceil(CFG.size_lamports / (1.15 * seed_p))  # base units, rounded UP: the executor never accepts fewer than the ratio allows
        filled = math.floor(tokens) >= min_out  # tokens out are rounded DOWN
        r = attempt([100], [f, f], cfg=GROSS)
        assert (r["status"] == "filled") == filled
        assert filled == (CFG.size_lamports / math.floor(tokens) <= 1.15 * seed_p)  # the same test as size / floor(tokens) <= ratio x seed price


def test_guarded_out_buys_pay_one_send_fee_in_both_bases_and_a_synthetic_migration_pool_is_a_reject():
    for cfg in (cps.Config(), GROSS):
        r = attempt([100, 700], [1.5, 1.5, 1.5], cfg=cfg)  # a pool already 50% above the seed at landing: a synthetic migration
        assert r["status"] == "guarded" and r["exit_type"] == "guard" and r["hold_slots"] == 0
        assert r["pnl"] == -float(CFG.fee_lamports) and r["rent"] == 0
        ok = attempt([100, 700], [1.0, 1.0, 1.0], cfg=cfg)
        assert ok["status"] == "filled"
        # guard none: nothing is rejected in either basis
        assert attempt([100, 700], [1.5, 1.5, 1.5], cfg=cps.Config(guard="none", guard_basis=cfg.guard_basis))["status"] == "filled"
    with pytest.raises(cps.Refused):
        cps.Config(guard_basis="gross-ish").validate()


def test_the_seed_price_in_the_guard_uses_the_pool_v():
    # V0 is the pool's own V: a pool with a larger V has a higher seed price, so the same landing price is a smaller ratio against it
    lo, hi = 17_500_000_000.0, 17_700_000_000.0
    assert (cps.SEED_Q + hi) / cps.SEED_B > (cps.SEED_Q + lo) / cps.SEED_B
    a = attempt([100], [1.0, 1.0], v=lo)
    b = attempt([100], [1.0, 1.0], v=hi)
    assert a["exec_ratio"] != b["exec_ratio"]
    assert a["exec_ratio"] / b["exec_ratio"] == pytest.approx(((cps.SEED_Q + hi) / (cps.SEED_Q + lo)), rel=1e-6)


# --- P2-4: cap anchor ------------------------------------------------------------------------------------------------------------------

CAP_SLOTS = [100, 110, 400, 700, 760, 850, 900]
CAP_BT = [1000, 1004, 1120, 1240, 1264, 1300, 1320]  # 0.4 s per slot from slot 100: 300 s = 750 slots (the day table of this test is 0.5 s: 600 slots)
BTC = cps.Config(cap_anchor="block-time")


def test_cap_anchor_defaults_to_the_day_mean_and_is_recorded():
    f = [1.0, 1.0, 1.0, 1.01, 1.02, 1.03, 1.04, 1.05]
    r = attempt(CAP_SLOTS, f, bt=CAP_BT)
    assert cps.Config().cap_anchor == "day-mean" and r["cap_anchor_used"] == "day-mean"
    # X = 103, D = 103 + round(300 / 0.5) = 703; the deadline fill is the first print with slot >= 703 + 2 + 1 = 706 (idx 4, slot 760)
    assert r["exit_type"] == "deadline" and r["hold_slots"] == 600 and r["pnl"] == pytest.approx(buy_and_sell(f, 1, 4))
    assert attempt(CAP_SLOTS, f)["pnl"] == r["pnl"]  # block_time is never read in day-mean mode


def test_block_time_anchor_puts_the_deadline_on_the_chain_clock():
    f = [1.0, 1.0, 1.0, 1.01, 1.02, 1.03, 1.04, 1.05]
    r = attempt(CAP_SLOTS, f, cfg=BTC, bt=CAP_BT)
    # landing print = the last print at slot <= 103 = slot 100 (block_time 1000); deadline = first print with block_time >= 1300 = idx 5 (slot 850);
    # the deadline sell lands at slot 850 + lag 2 + 1 -> the first print with slot >= 853 = idx 6
    assert r["cap_anchor_used"] == "block-time" and r["exit_type"] == "deadline"
    assert r["hold_slots"] == 850 - 103 and r["pnl"] == pytest.approx(buy_and_sell(f, 1, 6))
    # the exit lag applies to the timer-fired deadline sell too
    r0 = attempt(CAP_SLOTS, f, cfg=cps.Config(cap_anchor="block-time", exit_lag=0), bt=CAP_BT)
    assert r0["pnl"] == pytest.approx(buy_and_sell(f, 1, 6))  # first print with slot >= 851 is still idx 6
    r9 = attempt(CAP_SLOTS, f, cfg=cps.Config(cap_anchor="block-time", exit_lag=60), bt=CAP_BT)
    assert r9["pnl"] == pytest.approx(buy_and_sell(f, 1, 7))  # >= 911: past the last print -> the state after it


def test_block_time_anchor_scans_triggers_up_to_its_own_deadline():
    # a +70% spot after the print at slot 760: after the day-mean deadline (703), before the chain-clock deadline (850)
    f = [1.0, 1.0, 1.0, 1.01, 1.02, 1.7, 1.04, 1.05]
    dm = attempt(CAP_SLOTS, f, bt=CAP_BT)
    assert dm["exit_type"] == "deadline" and dm["pnl"] == pytest.approx(buy_and_sell(f, 1, 4))
    bt = attempt(CAP_SLOTS, f, cfg=BTC, bt=CAP_BT)
    # trigger on the print at slot 760 (idx 4, post-state 1.7); fill = first print with slot >= 760 + 2 + 1 = idx 5
    assert bt["exit_type"] == "tp" and bt["hold_slots"] == 760 - 103 and bt["pnl"] == pytest.approx(buy_and_sell(f, 1, 5))


def test_block_time_anchor_without_a_print_at_the_deadline_fills_at_the_last_state():
    slot, bt = [100, 110, 400, 700], [1000, 1004, 1120, 1240]  # the 300 s deadline (block_time 1300) is never printed
    f = [1.0, 1.0, 1.0, 1.01, 1.02]
    r = attempt(slot, f, cfg=BTC, bt=bt)
    assert r["cap_anchor_used"] == "block-time" and r["exit_type"] == "deadline" and r["pnl"] == pytest.approx(buy_and_sell(f, 1, 4))
    assert r["hold_slots"] == 700 - 103


def test_block_time_anchor_falls_back_to_the_day_mean_when_block_time_is_unusable():
    f = [1.0, 1.0, 1.0, 1.01, 1.02, 1.03, 1.04, 1.05]
    day = attempt(CAP_SLOTS, f)
    for bt in (None, [-1] * 7, [-1, -1, 1120, 1240, 1264, 1300, 1320]):  # none; all null; null on the landing print (and every print before it)
        r = attempt(CAP_SLOTS, f, cfg=BTC, bt=bt)
        assert r["cap_anchor_used"] == "day-mean-fallback"
        assert r["pnl"] == day["pnl"] and r["hold_slots"] == day["hold_slots"]
    # the day-mean fallback needs the day table
    with pytest.raises(cps.Refused):
        attempt(CAP_SLOTS, f, cfg=cps.Config(cap_anchor="block-time", k_mode="hour"), sph={}, hour_s=0.5, bt=None)
    with pytest.raises(cps.Refused):
        cps.Config(cap_anchor="wall").validate()


def test_bt_deadline_index_carries_nulls_forward_and_reports_unusable_landings():
    slot = np.array([10, 20, 30, 40, 50], np.int64)
    bt = np.array([100, -1, 130, 200, 210], np.int64)
    assert cps.bt_deadline_index(slot, bt, 25, 30.0) == 2  # landing print idx 1 (null) -> carried 100; first >= 130 is idx 2
    assert cps.bt_deadline_index(slot, bt, 25, 95.0) == 3  # first >= 195 is idx 3
    assert cps.bt_deadline_index(slot, bt, 25, 500.0) == 5  # never reached: len(slot)
    assert cps.bt_deadline_index(slot, bt, 5, 30.0) is None  # no print at or before the landing slot
    assert cps.bt_deadline_index(slot, np.array([-1, -1, 130, 200, 210], np.int64), 25, 30.0) is None  # no valid block_time at the landing print
    assert cps.bt_deadline_index(slot, None, 25, 30.0) is None


# --- P2-5: rent and dust ---------------------------------------------------------------------------------------------------------------

RENT = 2_039_280


def test_rent_none_is_g_and_always_charges_every_filled_trip_before_the_fail_mix():
    f = [1.0, 1.0, 1.0, 1.01, 1.02, 1.03, 1.04, 1.05]
    base = attempt(CAP_SLOTS, f)
    stress = attempt(CAP_SLOTS, f, cfg=cps.Config(rent_mode="always", rent_lamports=RENT))
    assert cps.Config().rent_mode == "none" and cps.Config().rent_lamports == 0
    assert base["rent"] == 0 and stress["rent"] == RENT
    assert stress["pnl"] == pytest.approx(base["pnl"] - RENT) and stress["status"] == base["status"] == "filled"
    # a guarded-out buy creates no token account: no rent, one send fee
    g = attempt([100, 700], [1.5, 1.5, 1.5], cfg=cps.Config(rent_mode="always", rent_lamports=RENT))
    assert g["status"] == "guarded" and g["rent"] == 0 and g["pnl"] == -float(CFG.fee_lamports)
    # the rent is part of the filled trip's pnl, so it passes through the fail mix like the rest of the pnl
    rows = [dict(base, mint="a"), dict(stress, mint="a")]
    cps.apply_legs(rows[:1], CFG)
    cps.apply_legs(rows[1:], CFG)
    assert rows[1]["pnl_nofail"] - rows[0]["pnl_nofail"] == pytest.approx(-RENT)
    assert rows[1]["pnl_flat"] - rows[0]["pnl_flat"] == pytest.approx(-(1 - CFG.flat_fail) * RENT)


def test_rent_flags_must_agree():
    cps.Config(rent_mode="always", rent_lamports=RENT).validate()
    cps.Config().validate()
    for bad in (cps.Config(rent_mode="always"), cps.Config(rent_lamports=RENT), cps.Config(rent_mode="sometimes"), cps.Config(rent_mode="always", rent_lamports=-1)):
        with pytest.raises(cps.Refused):
            bad.validate()


# --- P2-6: gate statistics -------------------------------------------------------------------------------------------------------------

SCIPY_T_SF = [(2.0, 4, 0.05805826175840778), (-1.3, 7, 0.8826160823038114), (0.0, 3, 0.5), (3.1, 1, 0.09932609219911853), (0.5, 30, 0.3103615024425636),
              (-4.0, 2, 0.9714045207910317), (12.0, 9, 3.8499431114928265e-07), (1.0, 100, 0.15986207789206167)]  # scipy.stats.t.sf, 1.18.1


def test_t_sf_matches_scipy_reference_values():
    for t, df, ref in SCIPY_T_SF:
        assert cps.t_sf(t, df) == pytest.approx(ref, rel=1e-9, abs=1e-15)
    assert cps.t_sf(float("inf"), 5) == 0.0 and cps.t_sf(float("-inf"), 5) == 1.0


def _boot_lo_and_p(x, p_draws=10_000):
    """Independent reference, one `integers` call per statistic: the CI90 lower bound on 1,000 draws (the gate) and p on `p_draws` draws (DEC-021 section 5), seed 1."""
    rng = np.random.default_rng(1)
    boot = x[rng.integers(0, len(x), size=(1000, len(x)))].mean(1)
    rng_p = np.random.default_rng(1)
    boot_p = x[rng_p.integers(0, len(x), size=(p_draws, len(x)))].mean(1)
    return float(np.percentile(boot, 5)), float((boot_p <= 0).mean())


def test_gate_stats_hand_case():
    sol = np.array([0.2, -0.1, 0.3, 0.1, -0.2, -0.1])
    dates = ["d1", "d1", "d2", "d2", "d3", "d3"]  # date sums: 0.1, 0.4, -0.3
    fills = [True, True, True, True, False, False]
    g = cps.gate_stats(sol * 1e9, fills, dates, 5e8)
    assert (g["n_attempts"], g["n_fills"], g["dates"], g["dates_pos"]) == (6, 4, 3, 2)
    assert g["total_sol"] == pytest.approx(0.2) and g["mean_per_attempt_sol"] == pytest.approx(0.2 / 6) and g["mean_per_attempt_pct"] == pytest.approx(100 * 0.2 / 6 / 0.5)
    assert g["mean_per_fill_sol"] == pytest.approx(0.125) and g["mean_per_fill_pct"] == pytest.approx(25.0)  # (0.2 - 0.1 + 0.3 + 0.1) / 4 fills
    assert g["ex_top3_sol"] == pytest.approx(0.2 - (0.3 + 0.2 + 0.1))  # the three best attempts removed
    assert g["ex_best_day_sol"] == pytest.approx(0.2 - 0.4)
    # day means 0.05, 0.2, -0.15: t = 0.3287979746107146 on 2 df, one-sided p from scipy
    assert g["t_date"] == pytest.approx(0.3287979746107146, rel=1e-12) and g["p_date_t"] == pytest.approx(0.3867722965855404, rel=1e-9)
    lo, p = _boot_lo_and_p(sol)  # CI: 1,000 draws, seed 1, 5th percentile; p: share of 10,000 bootstrap means <= 0 (report-only)
    assert g["ci_trade_lo_sol"] == pytest.approx(lo, rel=1e-12) and g["p_trade_boot"] == p and g["p_trade_boot_draws"] == 10_000
    assert g["ci_trade_lo_pct"] == pytest.approx(100 * lo / 0.5)
    # date cluster: resample the 3 dates (seed 1), pooled mean of the resampled dates
    dsum, dn = np.array([0.1, 0.4, -0.3]), np.array([2, 2, 2])
    di = np.random.default_rng(1).integers(0, 3, size=(1000, 3))
    assert g["ci_date_lo_sol"] == pytest.approx(float(np.percentile(dsum[di].sum(1) / dn[di].sum(1), 5)), rel=1e-12)
    assert g["ci_date_lo_sol"] <= g["mean_per_attempt_sol"]


def test_gate_stats_second_hand_case_p_date():
    day_means = [0.2, 0.1, 0.3, -0.05, 0.15]  # one attempt per date
    g = cps.gate_stats(np.array(day_means) * 1e9, [True] * 5, [f"d{i}" for i in range(5)], 5e8)
    assert g["t_date"] == pytest.approx(2.4188315916278085, rel=1e-12) and g["p_date_t"] == pytest.approx(0.03642752980512784, rel=1e-9)
    assert g["dates"] == 5 and g["dates_pos"] == 4 and g["ex_best_day_sol"] == pytest.approx(0.7 - 0.3)


def test_gate_stats_edges():
    assert cps.gate_stats([], [], [], 5e8) == {"n_attempts": 0, "n_fills": 0}
    one = cps.gate_stats(np.array([1e8, 2e8]), [True, False], ["d", "d"], 5e8)  # a single date: no t statistic
    assert one["dates"] == 1 and one["p_date_t"] is None and one["t_date"] is None
    assert cps.gate_stats(np.array([-1e8, -2e8]), [False, False], ["a", "b"], 5e8)["mean_per_fill_sol"] is None  # no fills
    flat = cps.gate_stats(np.array([1e8, 1e8, 1e8, 1e8]), [True] * 4, ["a", "a", "b", "b"], 5e8)  # equal date means: sd = 0
    assert flat["p_date_t"] == 0.0 and flat["p_trade_boot"] == 0.0 and flat["ci_trade_lo_sol"] == pytest.approx(0.1)
    neg = cps.gate_stats(np.array([-1e8, -1e8, -1e8, -1e8]), [True] * 4, ["a", "a", "b", "b"], 5e8)
    assert neg["p_date_t"] == 1.0 and neg["p_trade_boot"] == 1.0
    few = cps.gate_stats(np.array([1e8, 2e8]), [True, True], ["a", "b"], 5e8)  # fewer than 3 attempts: ex-top-3 removes everything
    assert few["ex_top3_sol"] == pytest.approx(0.0)


def _row(block, day, pnl, status="filled"):
    d = {"block": block, "day": day, "status": status}
    d.update({"pnl_" + leg: pnl for leg in cps.LEGS})
    return d


def test_book_stats_has_a_gate_block_per_scope_and_leg():
    rows = [_row(cps.BLOCK_P2, "2026-08-15", 1e8), _row(cps.BLOCK_P2, "2026-08-16", -5e7), _row(cps.BLOCK_P4, "2026-09-09", 2e8, "guarded"),
            _row(cps.BLOCK_P1A, "2026-09-19", 3e8), _row(cps.BLOCK_P1C, "2026-09-23", -1e8)]
    out = cps.book_stats(rows, 5e8)
    assert set(out) == {cps.BLOCK_P2, cps.BLOCK_P4, cps.BLOCK_P1A, cps.BLOCK_P1C, "P2-P4", "P1", "all"}  # a scope with no rows is not reported
    for scope, cell in out.items():
        assert set(cell["gate"]) == set(cps.LEGS)
        assert {leg: cell["gate"][leg]["role"] for leg in cps.LEGS} == {"flat": "binding", "press": "binding", "live": "report-only", "nofail": "report-only"}
    a = out["all"]["gate"]["flat"]
    assert a["n_attempts"] == 5 and a["n_fills"] == 4 and a["dates"] == 5
    assert out["P2-P4"]["gate"]["flat"]["n_attempts"] == 3 and out["P2-P4"]["gate"]["flat"]["n_fills"] == 2
    assert out["P1"]["gate"]["flat"]["n_attempts"] == 2 and out[cps.BLOCK_P2]["gate"]["flat"]["dates"] == 2
    # the date cluster is the UTC date alone: two blocks on one date are one cluster
    two = cps.book_stats([_row(cps.BLOCK_P2, "2026-09-09", 1e8), _row(cps.BLOCK_P4, "2026-09-09", -3e8)], 5e8)
    assert two["all"]["gate"]["flat"]["dates"] == 1


# --- P2-7: pick-set input --------------------------------------------------------------------------------------------------------------


def test_load_picks_csv(tmp_path):
    p = tmp_path / "scores.csv"
    p.write_text("mint,score\nA,0.9\nB,0.5\nC,0.8030766588450794\n")
    ps = cps.load_picks(p)
    assert ps.kind == "csv" and len(ps) == 3
    assert [ps.label(m, cps.PICK_THRESHOLD)[0] for m in "ABCD"] == ["pick", "non_pick", "pick", "unscored"]  # a pick is score >= threshold
    assert ps.label("A", 0.95)[0] == "non_pick"
    bad = tmp_path / "bad.csv"
    bad.write_text("mint,prob\nA,0.9\n")
    with pytest.raises(cps.Refused):
        cps.load_picks(bad)


def _gate_replay_jsonl(tmp_path, lines):
    p = tmp_path / "decisions.jsonl"
    p.write_text("".join((json.dumps(x) if not isinstance(x, str) else x) + "\n" for x in lines))
    return p


def test_load_picks_jsonl_in_the_gate_replay_format(tmp_path):
    # the output of tools/cap_pick_gate_replay.py (claude/cap-pick-gate-replay): a meta line, `kind: decision` rows (decision = pick | below | <reason>),
    # `kind: dead` rows (decision = pre_restart)
    S = "cap_pick_gate_replay_v1"
    p = _gate_replay_jsonl(tmp_path, [
        {"schema": S, "kind": "meta", "view": "v1", "days": ["2026-08-15"]},
        {"schema": S, "kind": "decision", "view": "v1", "mint": "A", "score": 0.91, "decision": "pick", "entered": True},
        {"schema": S, "kind": "decision", "view": "v1", "mint": "B", "score": 0.40, "decision": "below", "entered": False},
        {"schema": S, "kind": "decision", "view": "v1", "mint": "C", "score": None, "decision": "no_features", "entered": False},
        {"schema": S, "kind": "dead", "view": "v1", "mint": "D", "decision": "pre_restart"},
        {"schema": S, "kind": "dead", "view": "v1", "mint": "A", "decision": "pre_restart"},  # a dead row never overrides a decision
        {"schema": S, "kind": "dead", "view": "v1", "mint": "E", "decision": "pre_restart"},
        {"schema": S, "kind": "decision", "view": "v1", "mint": "E", "score": 0.95, "decision": "pick", "entered": True},  # a decision row replaces an earlier dead row
        {"schema": S, "kind": "decision", "view": "v2", "mint": "B", "score": 0.99, "decision": "pick", "entered": True},  # the first decision row of a mint wins
        "",
    ])
    ps = cps.load_picks(p)
    assert ps.kind == "jsonl" and len(ps) == 5  # A B C D E (the meta line has no mint)
    lab = {m: ps.label(m, 0.5)[0] for m in "ABCDEF"}
    assert lab == {"A": "pick", "B": "non_pick", "C": "non_pick", "D": "non_pick", "E": "pick", "F": "unscored"}
    assert ps.label("C", 0.5) == ("non_pick", "", "no_features") and ps.label("D", 0.5)[2] == "pre_restart"
    assert ps.label("A", 0.0)[0] == "pick" and ps.label("A", 0.999)[0] == "pick"  # the JSONL decision ignores the CSV threshold
    assert ps.label("A", 0.5)[1] == repr(0.91)


def test_load_picks_jsonl_refuses_bad_lines(tmp_path):
    with pytest.raises(cps.Refused):
        cps.load_picks(_gate_replay_jsonl(tmp_path, [{"mint": "A", "decision": "pick"}, "{not json"]))
    with pytest.raises(cps.Refused):
        cps.load_picks(_gate_replay_jsonl(tmp_path, [{"mint": "A", "decision": 1}]))
    with pytest.raises(cps.Refused):
        cps.load_picks(_gate_replay_jsonl(tmp_path, [{"mint": 7, "decision": "pick"}]))
    ps = cps.load_picks(_gate_replay_jsonl(tmp_path, [{"mint": "A", "decision": "pick"}]))  # a bare {mint, decision} line is enough
    assert ps.label("A", 0.5)[0] == "pick"


def test_book_must_be_all_or_picks():
    assert cps.Config().book == "all"
    cps.Config(book="picks").validate()
    with pytest.raises(cps.Refused):
        cps.Config(book="pickz").validate()


# --- P2-8: end to end on the fixture: defaults are phase 1, every switch flows through run() and the CLI ----------------------------------

GOLD = Path(__file__).parent / "fixtures" / "cap_pick_score"  # written by the PHASE-1 scorer (commit 06ee68e) on write_fixture()
PICKED = ("Mint01", "Mint02", "Mint03", "Mint06", "Mint09", "Mint12")  # Mint03 is guarded out at the defaults; Mint16 is left unscored


def read_rows_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_out(tmp_path, s, rows, name="out"):
    s = dict(s, _rows=rows)
    out = tmp_path / name
    cps.write_outputs(s, out)
    return out, json.loads((out / "summary.json").read_text()), read_rows_csv(out / "rows.csv")


@needs_zstd
def test_defaults_reproduce_phase_1_byte_for_byte(tmp_path):
    view, vpath = write_fixture(tmp_path)
    s, rows = run_fix(view, vpath)
    out, summary, new_rows = write_out(tmp_path, s, rows)
    gold = (GOLD / "phase1_rows.csv").read_text().splitlines()
    got = (out / "rows.csv").read_text().splitlines()
    n = len(gold[0].split(","))
    assert n == 25 and len(gold) == len(got) == 17
    assert [",".join(line.split(",")[:n]) for line in got] == gold  # the phase-1 columns, row for row, byte for byte
    assert got[0].split(",")[n:] == ["s0_minus_mslot", "hour", "ms_per_slot_hour", "exit_lag_slots", "exec_ratio_gross", "cap_anchor_used", "rent", "pick_decision"]
    gs = json.loads((GOLD / "phase1_summary_part.json").read_text())
    for scope, cell in gs["books"]["all"].items():
        for key, val in cell.items():
            assert summary["books"]["all"][scope][key] == val, (scope, key)  # the per-leg stats of phase 1 are unchanged
    for key, val in gs["counts"].items():
        assert summary["counts"][key] == val, key
    for key, val in gs["fail_legs"].items():
        assert summary["fail_legs"][key] == val, key
    # what the new columns say under the defaults: the day-mean clock, slot lag 2, no hour measurement, no rent, no cap fallback
    assert {r["cap_anchor_used"] for r in new_rows if r["status"] == "filled"} == {"day-mean"} and {r["cap_anchor_used"] for r in new_rows if r["status"] == "guarded"} == {""} and {r["exit_lag_slots"] for r in new_rows} == {"2"} and {r["rent"] for r in new_rows} == {"0"}
    assert {r["ms_per_slot_hour"] for r in new_rows} == {""} and summary["hour_sph"] == {} and summary["counts"]["hour_sph_fallback"] == 0
    assert set(summary["books"]) == {"all"} and summary["picks"] is None and summary["phase"] == 2
    assert all(int(r["s0_minus_mslot"]) == int(r["s0"]) - FIX_BASE_SLOT[r["day"]] for r in new_rows)


@needs_zstd
def test_s0_minus_migrate_slot_is_recorded_per_attempt(tmp_path):
    view, vpath = write_fixture(tmp_path)
    s, rows = run_fix(view, vpath)
    assert len(rows) == 16
    for r in rows:
        assert r["mslot"] == FIX_BASE_SLOT[r["day"]] and r["s0_minus_mslot"] == r["s0"] - r["mslot"] and 1 <= r["s0_minus_mslot"] <= 3
        assert r["landing_slot"] == r["s0"] + r["k"]  # the anchor is s0 (the pool-create slot), never the migrate slot


@needs_zstd
def test_k_mode_hour_and_exit_lag_ms_use_the_hour_measured_on_the_tape(tmp_path):
    view, vpath = write_fixture(tmp_path)
    s, rows = run_fix(view, vpath, cps.Config(k_mode="hour", exit_lag_ms=550))
    # the fixture's chain clock is 0.4 s per slot: (7500 - 1) slots over ~3,000 s of block_time -> ~9,000 slots per hour
    hs = s["hour_sph"]
    assert set(hs) == {"2026-08-15T00", "2026-08-16T00"} and {v["source"] for v in hs.values()} == {"tape"}
    assert all(v["slots_per_hour"] == pytest.approx(9000.0, rel=0.002) and v["ms_per_slot"] == pytest.approx(400.0, rel=0.002) for v in hs.values())
    assert {r["k"] for r in rows} == {3} and {r["exit_lag_slots"] for r in rows} == {2}  # round(3.25) = 3; ceil(550 / 400) = 2
    assert all(r["ms_per_slot_hour"] == pytest.approx(400.0, rel=0.002) for r in rows) and s["counts"]["hour_sph_fallback"] == 0
    s2, rows2 = run_fix(view, vpath, cps.Config(k_mode="hour", k_rounding="ceil", exit_lag_ms=1350))
    assert {r["k"] for r in rows2} == {4} and {r["exit_lag_slots"] for r in rows2} == {4}  # ceil(3.25) = 4; ceil(1350 / 400) = ceil(3.37) = 4
    # a json override (200 ms slots): 1.3 s is 6.5 slots, round -> 6 (half to even), ceil -> 7
    hj = tmp_path / "hours.json"
    hj.write_text(json.dumps({"2026-08-15T00": 18000.0, "2026-08-16T00": 18000.0}))
    s3, rows3 = run_fix(view, vpath, cps.Config(k_mode="hour"), hour_sph_json=str(hj))
    assert {r["k"] for r in rows3} == {6} and {v["source"] for v in s3["hour_sph"].values()} == {"json"}
    assert {r["k"] for r in run_fix(view, vpath, cps.Config(k_mode="hour", k_rounding="ceil"), hour_sph_json=str(hj))[1]} == {7}
    # the day-mode default ignores both the hour json and the tape
    assert {r["k"] for r in run_fix(view, vpath, hour_sph_json=str(hj))[1]} == {3}
    # an hour that cannot be measured falls back to the day table, and is counted
    bare = tmp_path / "bare"
    bare.mkdir()
    view2, vpath2 = write_fixture(bare, with_bt=False)
    s4, rows4 = run_fix(view2, vpath2, cps.Config(k_mode="hour"))
    assert s4["counts"]["hour_sph_fallback"] == 2 and {v["source"] for v in s4["hour_sph"].values()} == {"day-table-fallback"}
    assert {r["k"] for r in rows4} == {3} and rows4[0]["ms_per_slot_hour"] == pytest.approx(3.6e6 / cps.SLOTS_PER_HOUR[rows4[0]["day"]])


@needs_zstd
def test_cap_anchor_block_time_flows_through_run_and_falls_back_without_block_time(tmp_path):
    view, vpath = write_fixture(tmp_path)
    base_s, base = run_fix(view, vpath)
    s, rows = run_fix(view, vpath, cps.Config(cap_anchor="block-time"))
    assert {r["cap_anchor_used"] for r in rows if r["status"] == "filled"} == {"block-time"} and s["counts"]["cap_bt_fallback"] == 0
    assert all(r["hold_slots"] >= 750 for r in rows if r["exit_type"] == "deadline")  # 300 s on a 0.4 s chain clock; the day table says 721 slots
    assert any(r["exit_type"] == "deadline" for r in base) and all(r["hold_slots"] == 721 for r in base if r["exit_type"] == "deadline")
    assert [r["status"] for r in rows] == [r["status"] for r in base]  # the cap anchor never touches the guard
    # block_time absent from the tape: every attempt uses G's day-mean slots, and the result is the default book
    bare = tmp_path / "bare"
    bare.mkdir()
    view2, vpath2 = write_fixture(bare, with_bt=False)
    s2, rows2 = run_fix(view2, vpath2, cps.Config(cap_anchor="block-time"))
    assert {r["cap_anchor_used"] for r in rows2 if r["status"] == "filled"} == {"day-mean-fallback"} and s2["counts"]["cap_bt_fallback"] == 14  # 14 fills, 2 guarded (no deadline)
    assert [r["pnl_nofail"] for r in rows2] == [r["pnl_nofail"] for r in base]


@needs_zstd
def test_rent_and_gross_guard_flow_through_run(tmp_path):
    view, vpath = write_fixture(tmp_path)
    _, base = run_fix(view, vpath)
    _, rent = run_fix(view, vpath, cps.Config(rent_mode="always", rent_lamports=RENT))
    for a, b in zip(base, rent):
        assert a["mint"] == b["mint"] and a["status"] == b["status"]
        if a["status"] == "filled":
            assert b["pnl_nofail"] == pytest.approx(a["pnl_nofail"] - RENT) and b["rent"] == RENT
        else:
            assert b["pnl_nofail"] == a["pnl_nofail"] == -55000.0 and b["rent"] == 0
    _, gross = run_fix(view, vpath, GROSS)
    assert sum(r["status"] == "guarded" for r in gross) >= sum(r["status"] == "guarded" for r in base) == 2
    assert all(g["status"] == "guarded" for a, g in zip(base, gross) if a["status"] == "guarded")
    assert all(r["exec_ratio_gross"] >= r["exec_ratio"] for r in gross)
    # the gross guard is the integer test on the recorded landing price: guarded iff the gross execution ratio is above 1.15 (up to the integer rounding)
    assert all((r["status"] == "guarded") == (r["exec_ratio_gross"] > 1.15) for r in gross if abs(r["exec_ratio_gross"] - 1.15) > 1e-9)


def _write_picks(tmp_path, kind):
    mints = [f"Mint{i:02d}" for i in range(1, 16)]  # Mint16 stays unscored
    if kind == "csv":
        p = tmp_path / "scores.csv"
        p.write_text("mint,score\n" + "".join(f"{m},{0.9 if m in PICKED else 0.1}\n" for m in mints))
    else:
        p = tmp_path / "decisions.jsonl"
        lines = [{"schema": "x", "kind": "meta", "view": "v", "days": list(FIX_DAYS)}]
        lines += [{"schema": "x", "kind": "decision", "view": "v", "mint": m, "decision": "pick" if m in PICKED else "below", "score": 0.5} for m in mints]
        p.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return p


@needs_zstd
def test_picks_book_all_reports_the_pick_subset_and_picks_book_keeps_only_the_picks(tmp_path):
    view, vpath = write_fixture(tmp_path)
    for kind in ("csv", "jsonl"):
        picks = _write_picks(tmp_path, kind)
        s, rows = run_fix(view, vpath, picks=str(picks))
        assert s["counts"]["attempts"] == 16 and (s["counts"]["pick_attempts"], s["counts"]["non_pick_attempts"], s["counts"]["unscored_attempts"]) == (6, 9, 1)
        assert s["picks"]["format"] == kind and set(s["books"]) == {"all", "picks", "non_picks"}
        assert sorted(r["mint"] for r in rows if r["pick"] == "pick") == sorted(PICKED)
        pk = s["books"]["picks"]["all"]["gate"]["flat"]
        assert pk["n_attempts"] == 6 and pk["n_fills"] == 5  # Mint03 is guarded out
        assert s["books"]["all"]["all"]["gate"]["flat"]["n_attempts"] == 16
        assert s["books"]["non_picks"]["all"]["gate"]["flat"]["n_attempts"] == 9  # unscored mints are in the all-book only
        # --book picks: only the picks are attempts, the pressure intercept is refit on their own fills
        sp, rp = run_fix(view, vpath, cps.Config(book="picks"), picks=str(picks))
        assert sorted(r["mint"] for r in rp) == sorted(PICKED) and sp["counts"]["attempts"] == 6 and sp["counts"]["attempts_before_book_filter"] == 16
        assert set(sp["books"]) == {"picks"} and sp["books"]["picks"]["all"]["gate"]["flat"]["n_attempts"] == 6
        fills = [r for r in rp if r["status"] == "filled"]
        assert sum(r["p_press"] for r in fills) / len(fills) == pytest.approx(cps.TARGET_FAIL_RATE, abs=1e-9)
        assert sp["fail_legs"]["pressure_intercept"] != s["fail_legs"]["pressure_intercept"]
        # the simulated path of a pick does not depend on the book
        by = {r["mint"]: r for r in rows}
        assert all(by[r["mint"]]["pnl_nofail"] == r["pnl_nofail"] and by[r["mint"]]["pnl_flat"] == r["pnl_flat"] for r in rp)
    with pytest.raises(cps.Refused):
        run_fix(view, vpath, cps.Config(book="picks"))  # needs --picks
    # no --picks: no pick books at all
    s0, _ = run_fix(view, vpath)
    assert set(s0["books"]) == {"all"}


@needs_zstd
def test_picks_book_with_no_pick_is_an_empty_book_not_a_crash(tmp_path):
    view, vpath = write_fixture(tmp_path)
    p = tmp_path / "nopick.jsonl"
    p.write_text(json.dumps({"mint": "Mint01", "decision": "below"}) + "\n")
    s, rows = run_fix(view, vpath, cps.Config(book="picks"), picks=str(p))
    assert rows == [] and s["counts"]["attempts"] == 0 and s["counts"]["attempts_before_book_filter"] == 16 and s["counts"]["pick_attempts"] == 0
    assert s["books"]["picks"] == {} and s["fail_legs"]["pressure_intercept"] is None and s["fail_legs"]["pressure_mean_p"] is None
    out, summary, new_rows = write_out(tmp_path, s, rows)  # the empty book still writes both outputs
    assert new_rows == [] and summary["books"] == {"picks": {}}


@needs_zstd
def test_cli_flags_reach_the_config_and_the_outputs(tmp_path, capsys):
    view, vpath = write_fixture(tmp_path)
    hj = tmp_path / "hours.json"
    hj.write_text(json.dumps({"2026-08-15T00": 18000.0, "2026-08-16T00": 18000.0}))
    picks = _write_picks(tmp_path, "jsonl")
    out = tmp_path / "cli-out"
    argv = ["--p2-view-dir", str(view), "--vmap", str(vpath), "--out-dir", str(out), "--hour-sph-json", str(hj), "--picks", str(picks), "--book", "picks",
            "--entry-latency-ms", "1300", "--k-mode", "hour", "--k-rounding", "ceil", "--exit-lag-ms", "550", "--guard-basis", "gross", "--cap-anchor", "block-time",
            "--rent-mode", "always", "--rent-lamports", str(RENT)]
    assert cps.main(argv) == 0
    summary = json.loads((out / "summary.json").read_text())
    c = summary["config"]
    assert (c["k_seconds"], c["k_mode"], c["k_rounding"], c["exit_lag_ms"], c["guard_basis"], c["cap_anchor"], c["rent_mode"], c["rent_lamports"], c["book"]) == (
        1.3, "hour", "ceil", 550.0, "gross", "block-time", "always", RENT, "picks")
    rows = read_rows_csv(out / "rows.csv")
    assert {r["k"] for r in rows} == {"7"} and {r["exit_lag_slots"] for r in rows} == {"3"} and {r["cap_anchor_used"] for r in rows if r["status"] == "filled"} == {"block-time"}
    assert {r["pick"] for r in rows} == {"pick"} and {r["pick_decision"] for r in rows} == {"pick"} and len(rows) == 6
    assert {r["ms_per_slot_hour"] for r in rows} == {"200.0"}
    assert {r["rent"] for r in rows if r["status"] == "filled"} == {str(RENT)}
    # defaults on the CLI are G's
    out2 = tmp_path / "cli-out2"
    assert cps.main(["--p2-view-dir", str(view), "--vmap", str(vpath), "--out-dir", str(out2)]) == 0
    c2 = json.loads((out2 / "summary.json").read_text())["config"]
    assert c2 == dataclasses.asdict(cps.Config())
    # contradictory or incomplete flags are refused (exit 2)
    for bad in (["--k-seconds", "1.3", "--entry-latency-ms", "1300"], ["--exit-lag", "2", "--exit-lag-ms", "550"], ["--rent-mode", "always"], ["--rent-lamports", "5"],
                ["--book", "picks"], ["--k-mode", "hour", "--k-seconds", "0"]):
        capsys.readouterr()
        assert cps.main(["--p2-view-dir", str(view), "--vmap", str(vpath), "--out-dir", str(tmp_path / "bad")] + bad) == 2
        assert "REFUSED" in capsys.readouterr().err


def test_config_from_args_maps_every_flag():
    ns = cps._parser().parse_args(["--out-dir", "o", "--exit-lag", "4", "--k-seconds", "0.9", "--guard-ratio", "1.2", "--cap-seconds", "200", "--tp", "0.4", "--sl", "0.2",
                                   "--size-lamports", "100000000", "--fee-lamports", "10000", "--live-fail", "0.05", "--flat-fail", "0.2", "--final-state", "g"])
    c = cps.config_from_args(ns)
    assert (c.exit_lag, c.k_seconds, c.guard_ratio, c.cap_seconds, c.tp, c.sl, c.size_lamports, c.fee_lamports, c.live_fail, c.flat_fail, c.final_state) == (
        4, 0.9, 1.2, 200.0, 0.4, 0.2, 100_000_000, 10_000, 0.05, 0.2, "g")
    assert c.exit_lag_ms is None
    d = cps.config_from_args(cps._parser().parse_args(["--out-dir", "o"]))
    assert d == cps.Config()


# =====================================================================================================================================
# Input path guard (PR #461 follow-up): --picks, --hour-sph-json, --sph-json, --vmap and the source dirs are checked before anything is opened.
# =====================================================================================================================================

LEGIT_INPUTS = (
    "/data/mal/pumpswap-virtual/pool_v_0909.json",  # the V map
    "/data/mal/audit-1008/work/capv/judge/p_primary_picks.jsonl",  # the audit's files
    "/data/mal/audit-1008/reports/capv_JUDGE.md",
    "/data/mal/cap-pick-score/gate-replay/replay_explore-0814.jsonl",  # gate-replay outputs
    "/data/mal/cap-pick-score/phase1-repro/rows.csv",
)
# one refused path per class (the class is the key)
REFUSED_INPUTS = {
    "sealed block fresh-0802": "/data/mal/clean-view/fresh-0802/scores.csv",
    "sealed block fresh-0808": "/data/mal/blocks-clean/fresh-0808/scores.csv",
    "sealed block fresh-0828": "/x/fresh-0828/scores.csv",
    "forward walk dir": "/data/mal/exp012-forward/decisions.jsonl",
    "forward walk dir, sibling": "/data/mal/exp012-forward-1016/decisions.jsonl",
    "forward walk block": "/data/mal/blocks/forward-1002/hours.json",
    "forward-paper name": "/data/mal/cap-pick-score/gate-replay/forward-paper-picks.jsonl",
    "oracle live": "/data/mal/clean-view/oracle-live-2026-09-25_27/scores.csv",
    "arm-audit name": "/data/mal/audit-1008/arm-audit.jsonl",
    "exp012-gate name": "/data/mal/cap-pick-score/exp012-gate-decisions.jsonl",
    "positions name": "/data/mal/cap-pick-score/positions.jsonl",
    "heartbeat name": "/data/mal/cap-pick-score/heartbeat.jsonl",
    "runner name": "/data/mal/cap-pick-score/runner-decisions.jsonl",
    "runner dir": "/var/lib/mal/exp012-runner/decisions.jsonl",
    "mal-live": "/var/lib/mal-live/state.json",
    "mal-live, nested": "/var/lib/mal-live/x/y.json",
    "upper case": "/data/mal/cap-pick-score/Forward-Paper-Picks.JSONL",
    "dot-dot into a forward dir": "/data/mal/cap-pick-score/../exp012-forward/decisions.jsonl",
}


def test_the_legitimate_inputs_are_allowed():
    roots = [v for v in cps.DEFAULT_ROOTS.values() if isinstance(v, str)] + cps.DEFAULT_ROOTS["p2_view_dir"] + cps.DEFAULT_ROOTS["p4_view_dir"]
    for p in (*LEGIT_INPUTS, cps.DEFAULT_VMAP, *roots):
        cps.check_path_allowed(p)
    cps.check_path_allowed(Path("/data/mal/pumpswap-virtual/pool_v_0909.json"))


@pytest.mark.parametrize("why", sorted(REFUSED_INPUTS))
def test_each_refusal_class_is_refused(why):
    with pytest.raises(cps.Refused):
        cps.check_path_allowed(REFUSED_INPUTS[why])


def test_the_refusal_list_only_grew():
    assert {"fresh-0802", "fresh-0808", "fresh-0828", "forward", "oracle-live"} <= set(cps.FORBIDDEN_PATH_PARTS)  # phase 1's list is still there
    assert {"forward-paper", "arm-audit", "exp012-gate", "positions", "heartbeat", "runner"} <= set(cps.FORBIDDEN_PATH_PARTS)
    assert set(cps.FORBIDDEN_PATH_PREFIXES) == {"/data/mal/exp012-forward", "/var/lib/mal-live"}
    assert set(cps.INPUT_FILE_ARGS) == {"picks", "hour_sph_json", "sph_json", "vmap"}


def test_a_symlink_cannot_hide_a_refused_target(tmp_path):
    hidden = tmp_path / "arm-audit"
    hidden.mkdir()
    (hidden / "scores.csv").write_text("mint,score\n")
    link = tmp_path / "innocent.csv"
    link.symlink_to(hidden / "scores.csv")
    assert "arm-audit" not in str(link)  # the given name is clean; only the target is not
    with pytest.raises(cps.Refused):
        cps.check_path_allowed(link)
    dirlink = tmp_path / "view"
    dirlink.symlink_to(hidden, target_is_directory=True)
    with pytest.raises(cps.Refused):
        cps.check_path_allowed(dirlink)
    ok = tmp_path / "scores.csv"
    ok.write_text("mint,score\n")
    cps.check_path_allowed(ok)


def test_a_relative_path_is_judged_by_its_own_name_not_the_checkout_dir(tmp_path, monkeypatch):
    cwd = tmp_path / "runner-checkout"  # a working directory whose own name would be refused
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    cps.check_path_allowed("scores.csv")
    cps.check_path_allowed("data/picks.jsonl")
    with pytest.raises(cps.Refused):
        cps.check_path_allowed("data/positions.jsonl")
    with pytest.raises(cps.Refused):
        cps.check_path_allowed("../heartbeat.jsonl")


@contextlib.contextmanager
def no_file_access(monkeypatch):
    """Every way the scorer could open, list or read a file raises; `calls` records which was tried. Undone on exit (before pytest's own tmp cleanup)."""
    calls: list[str] = []

    def deny(name):
        def f(*a, **k):
            calls.append(name)
            raise AssertionError(f"{name} called with {a[:1]}")
        return f

    with monkeypatch.context() as m:
        m.setattr(builtins, "open", deny("builtins.open"))
        m.setattr(io, "open", deny("io.open"))
        m.setattr(os, "open", deny("os.open"))
        m.setattr(os, "scandir", deny("os.scandir"))
        m.setattr(os, "listdir", deny("os.listdir"))
        m.setattr(subprocess, "Popen", deny("subprocess.Popen"))
        for n in ("open", "read_text", "read_bytes", "iterdir", "glob"):
            m.setattr(Path, n, deny(f"Path.{n}"))
        yield calls


def _guard_args(tmp_path, **kw):
    """Valid inputs that WOULD be opened (a view dir with a trades/ dir, a V map), so a late check would show up as a call."""
    view = tmp_path / "view"
    (view / "trades").mkdir(parents=True)
    vpath = tmp_path / "v.json"
    vpath.write_text(json.dumps({"v": {}}))
    return fix_args(view, vpath, **kw)


def test_the_detector_sees_a_real_open(tmp_path, monkeypatch):  # positive control for the next tests
    args = _guard_args(tmp_path)
    with no_file_access(monkeypatch) as calls:
        with pytest.raises(AssertionError):
            cps.run(args, cps.Config(), lambda m: None)
    assert calls  # without a refusal the run does try to open something


@pytest.mark.parametrize("flag", cps.INPUT_FILE_ARGS)
@pytest.mark.parametrize("why", ["sealed block fresh-0828", "forward walk dir", "forward-paper name", "arm-audit name", "exp012-gate name", "positions name",
                                 "heartbeat name", "runner name", "mal-live"])
def test_a_refused_input_is_refused_before_anything_is_opened(tmp_path, monkeypatch, flag, why):
    args = _guard_args(tmp_path, **{flag: REFUSED_INPUTS[why]})
    with no_file_access(monkeypatch) as calls:
        with pytest.raises(cps.Refused):
            cps.run(args, cps.Config(), lambda m: None)
    assert calls == []


@pytest.mark.parametrize("flag,val", [("p1_fast_dir", "/data/mal/blocks/forward-1002"), ("p3_root", "/x/fresh-0808"), ("p1_oracle_insample_dir", "/var/lib/mal-live/tape"),
                                      ("p2_view_dir", ["/ok/view", "/data/mal/exp012-forward/tape"]), ("p4_view_dir", ["/x/runner-view"])])
def test_a_refused_source_dir_is_refused_before_anything_is_opened(tmp_path, monkeypatch, flag, val):
    args = _guard_args(tmp_path, **{flag: val})
    with no_file_access(monkeypatch) as calls:
        with pytest.raises(cps.Refused):
            cps.run(args, cps.Config(), lambda m: None)
    assert calls == []


def test_main_exits_2_on_a_refused_input_and_writes_nothing(tmp_path, capsys):
    view = tmp_path / "view"
    (view / "trades").mkdir(parents=True)
    out = tmp_path / "out"
    assert cps.main(["--p2-view-dir", str(view), "--picks", REFUSED_INPUTS["forward-paper name"], "--out-dir", str(out)]) == 2
    assert "REFUSED" in capsys.readouterr().err and not out.exists()


# =====================================================================================================================================
# Bootstrap p draws (DEC-021 section 5): 10,000 draws, seed 1, report-only. The CI90 keeps the gate's 1,000 draws.
# =====================================================================================================================================


class _RngSpy:
    """np.random.default_rng, logging (seed, size) of every `integers` call."""

    def __init__(self, log, real, seed):
        self.log, self.seed, self.g = log, seed, real(seed)

    def integers(self, lo, hi, size=None, **kw):
        self.log.append((self.seed, tuple(size)))
        return self.g.integers(lo, hi, size=size, **kw)


def test_boot_p_draws_default_is_10000_seed_1_and_the_ci_stays_at_1000(monkeypatch):
    assert cps.Config().boot_p_draws == cps.BOOT_P_DRAWS == 10_000 and (cps.BOOT_DRAWS, cps.BOOT_SEED) == (1000, 1)
    assert cps._parser().parse_args(["--out-dir", "o"]).boot_p_draws == 10_000
    sol = np.array([0.2, -0.1, 0.3, 0.1, -0.2, -0.1, 0.05, -0.05])
    dates = ["d1", "d1", "d2", "d2", "d3", "d3", "d4", "d4"]
    log, real = [], np.random.default_rng
    with monkeypatch.context() as m:
        m.setattr(np.random, "default_rng", lambda seed=None: _RngSpy(log, real, seed))
        g = cps.gate_stats(sol * 1e9, [True] * 8, dates, 5e8)
    assert {seed for seed, _ in log} == {1}  # every stream is seeded 1
    n, w = len(sol), 4
    assert sum(size[0] for _, size in log if size[1] == n) == 1000 + 10_000  # the CI90's 1,000 resamples of n, plus the p's 10,000
    assert sum(size[0] for _, size in log if size[1] == w) == 1000  # the date-cluster CI90: 1,000 resamples of the 4 dates
    assert g["p_trade_boot_draws"] == 10_000
    lo, p = _boot_lo_and_p(sol)
    assert g["p_trade_boot"] == p and g["ci_trade_lo_sol"] == pytest.approx(lo, rel=1e-12)


def test_boot_p_draws_changes_only_p_trade_boot():
    sol = np.array([0.2, -0.1, 0.3, 0.1, -0.2, -0.1, 0.05, -0.05]) * 1e9
    dates = ["d1", "d1", "d2", "d2", "d3", "d3", "d4", "d4"]
    skip = ("p_trade_boot", "p_trade_boot_draws")
    base = cps.gate_stats(sol, [True] * 8, dates, 5e8, p_draws=cps.BOOT_DRAWS)
    for draws in (200, 2500, 10_000):
        g = cps.gate_stats(sol, [True] * 8, dates, 5e8, p_draws=draws)
        assert g["p_trade_boot_draws"] == draws
        assert {k: v for k, v in g.items() if k not in skip} == {k: v for k, v in base.items() if k not in skip}
    x = sol / 1e9
    ref = np.random.default_rng(1).integers(0, len(x), size=(2500, len(x)))
    assert cps.gate_stats(sol, [True] * 8, dates, 5e8, p_draws=2500)["p_trade_boot"] == float((x[ref].mean(1) <= 0).mean())


def test_boot_means_chunks_read_the_generator_in_one_pass_order():
    x = np.random.default_rng(7).normal(size=37)
    one = x[np.random.default_rng(1).integers(0, 37, size=(1234, 37))].mean(1)
    for chunk_cells in (1, 36, 37, 38, 500, 10**9):  # odd and tiny chunk sizes: PCG64 keeps a half-used 64-bit word across calls
        assert np.array_equal(cps.boot_means(x, 1234, chunk_cells=chunk_cells), one)
    assert np.array_equal(cps.boot_means(x, 10_000)[:1000], cps.boot_means(x, 1000))  # the first 1,000 of the p's draws are the CI's draws


def test_boot_p_draws_must_be_a_positive_integer():
    for bad in (0, -1, 2.5, True, None):
        with pytest.raises(cps.Refused):
            cps.Config(boot_p_draws=bad).validate()
    cps.Config(boot_p_draws=1).validate()


@needs_zstd
def test_boot_p_draws_flows_through_the_cli_and_leaves_rows_csv_unchanged(tmp_path):
    view, vpath = write_fixture(tmp_path)
    base = ["--p2-view-dir", str(view), "--vmap", str(vpath)]
    outs = {}
    for name, extra in (("default", []), ("p1000", ["--boot-p-draws", "1000"]), ("p3000", ["--boot-p-draws", "3000"])):
        out = tmp_path / name
        assert cps.main(base + ["--out-dir", str(out)] + extra) == 0
        outs[name] = (out / "rows.csv").read_bytes(), json.loads((out / "summary.json").read_text())
    assert outs["default"][0] == outs["p1000"][0] == outs["p3000"][0]  # rows.csv: byte for byte
    sd, s1, s3 = (outs[k][1] for k in ("default", "p1000", "p3000"))
    assert (sd["config"]["boot_p_draws"], s1["config"]["boot_p_draws"], s3["config"]["boot_p_draws"]) == (10_000, 1000, 3000)
    assert sd["bootstrap"] == {"ci90_draws": 1000, "ci90_seed": 1, "p_trade_boot_draws": 10_000, "p_trade_boot_seed": 1, "p_trade_boot_role": "report-only"}
    assert s3["bootstrap"]["p_trade_boot_draws"] == 3000 and s3["bootstrap"]["ci90_draws"] == 1000
    skip = ("p_trade_boot", "p_trade_boot_draws")

    def cells(s):
        return [(scope, leg, c) for scope, cell in s["books"]["all"].items() for leg, c in cell["gate"].items()]

    assert cells(sd) and {c["p_trade_boot_draws"] for _, _, c in cells(sd) if "p_trade_boot_draws" in c} == {10_000}
    assert {c["p_trade_boot_draws"] for _, _, c in cells(s3) if "p_trade_boot_draws" in c} == {3000}
    for (sc, lg, a), (_, _, b) in zip(cells(sd), cells(s3)):  # every gate statistic except the p and its draw count is the same
        assert {k: v for k, v in a.items() if k not in skip} == {k: v for k, v in b.items() if k not in skip}, (sc, lg)
    assert sd["books"]["all"]["all"]["live"] == s3["books"]["all"]["all"]["live"]  # the older per-leg stats too
    assert cps.main(base + ["--out-dir", str(tmp_path / "bad"), "--boot-p-draws", "0"]) == 2
