"""Tests for tools/cap_pick_score.py on tiny synthetic paths. Nothing here reads /data/mal."""

from __future__ import annotations

import argparse
import json
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


def attempt(slot, factors, cfg=CFG, sph=SPH_HALF, s0=100, isbuy=None, sol=None):
    qpre, bpre = states(factors)
    slot = np.asarray(slot, np.int64)
    n = slot.size
    assert qpre.size == n + 1
    return cps.simulate_attempt(
        cfg, day="d", sph=sph, v=V, s0=s0, slot=slot,
        isbuy=np.ones(n, bool) if isbuy is None else np.asarray(isbuy, bool),
        sol=np.full(n, 1e9) if sol is None else np.asarray(sol, float), qpre=qpre, bpre=bpre,
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


def _trade(mint, pool, slot, side, q, b, sol, tok, tx, ev=0, venue="pumpswap"):
    return {"v": 2, "venue": venue, "mint": mint, "trader": "t", "side": side, "sol_lamports": sol, "token_raw": tok, "quote_reserve": q, "base_reserve": b,
            "pool": pool, "slot": slot, "event_index": ev, "block_time": 1, "tx_index": tx}


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
