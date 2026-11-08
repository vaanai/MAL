"""Tests for tools/forge (events + evaluator). Synthetic tape only; no real tape is read."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from tools.forge import common as C
from tools.forge import evaluate as E
from tools.forge import events as EV

HOUR = "2026-10-25T10"
HS = C.hour_ms(HOUR)
V = 17_584_324_954
POOL = "PoolAAAA"
MINT = "MintAAAA"
WS = C.WSOL


def ps_row(t_ms, side="buy", b=1_000_000_000_000_000.0, q=10e9, sol=1_000_000, pool=POOL, mint=MINT, trader="T1", v=V):
    return {"type": "trade", "prog": "pumpswap", "t_recv_ms": t_ms, "venue": "pumpswap", "mint": mint, "trader": trader, "side": side, "sol_lamports": sol,
            "token_raw": 1000, "quote_reserve": q, "base_reserve": b, "quote_mint": WS, "pool": pool, "virtual_quote_reserves": v}


def curve_row(t_ms, side="buy", q=30e9, b=1.073e15, mint="CurveM", trader="T1", rq=1e9, rb=7.0e14):
    return {"type": "trade", "prog": "pumpfun", "t_recv_ms": t_ms, "venue": "pump_bonding", "mint": mint, "trader": trader, "side": side, "sol_lamports": 1_000_000,
            "token_raw": 1000, "quote_reserve": q, "base_reserve": b, "quote_mint": WS, "pool": None, "real_quote_reserve": rq, "real_base_reserve": rb}


def write_tape(d: Path, hour: str, rows: list[dict]):
    with open(d / f"tape-{hour}.jsonl", "w") as fh:
        for r in sorted(rows, key=lambda r: r["t_recv_ms"]):
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")


def build(tmp_path, rows, hour=HOUR, extra_hours=()):
    tape = tmp_path / "tape"
    tape.mkdir(exist_ok=True)
    out = str(tmp_path / "out")
    write_tape(tape, hour, rows)
    for h, rr in extra_hours:
        write_tape(tape, h, rr)
    for h in [hour] + [h for h, _ in extra_hours]:
        EV.ingest_hour(h, str(tape), out)
    fs = EV.first_seen(out, [hour] + [h for h, _ in extra_hours])
    return out, fs


def flat_pool_rows(n=200, step_ms=1000, b_fn=None, **kw):
    rows = []
    for k in range(n):
        b = b_fn(k) if b_fn else 1_000_000_000_000_000.0
        rows.append(ps_row(HS + k * step_ms, "buy" if k % 2 == 0 else "sell", b=b, **kw))
    return rows


# ---------------------------------------------------------------------------------------------- events
def test_thinning_and_fill_delay(tmp_path):
    rows = flat_pool_rows(200)
    out, fs = build(tmp_path, rows)
    r = EV.hour_events(HOUR, out, fs)
    t = r["t"]
    assert (np.diff(t) >= 30_000).all()
    assert list(t[:3]) == [HS, HS + 30_000, HS + 60_000]
    # prints every 1 s: the first print at least 2.0 s later is exactly +2000
    assert (r["t_fill"] - r["t"] == 2000).all()
    assert (r["fill_dt"] == 2000).all()


def test_fill_skips_prints_inside_delay(tmp_path):
    rows = [ps_row(HS), ps_row(HS + 500), ps_row(HS + 1999), ps_row(HS + 2300)]
    out, fs = build(tmp_path, rows)
    r = EV.hour_events(HOUR, out, fs)
    assert list(r["t"]) == [HS] and list(r["t_fill"]) == [HS + 2300]


def test_no_fill_no_event(tmp_path):
    out, fs = build(tmp_path, [ps_row(HS), ps_row(HS + 1000)])
    assert EV.hour_events(HOUR, out, fs) is None


def test_features_causal(tmp_path):
    a = flat_pool_rows(200)
    b = [dict(r) for r in a]
    for r in b:  # change everything after +100 s: size, side, reserves
        if r["t_recv_ms"] > HS + 100_000:
            r["sol_lamports"] = 9_000_000_000
            r["side"] = "sell"
            r["base_reserve"] = 5e14
    oa, fa = build(tmp_path / "a", a) if (tmp_path / "a").mkdir() is None else None
    ob, fb = build(tmp_path / "b", b) if (tmp_path / "b").mkdir() is None else None
    ra, rb = EV.hour_events(HOUR, oa, fa), EV.hour_events(HOUR, ob, fb)
    m = ra["t"] <= HS + 98_000
    assert m.sum() >= 3
    xa, xb = ra["X"][m], rb["X"][rb["t"] <= HS + 98_000]
    np.testing.assert_array_equal(np.nan_to_num(xa, nan=-7), np.nan_to_num(xb, nan=-7))


def test_window_features_values(tmp_path):
    rows = [ps_row(HS + k * 1000, "buy" if k % 4 else "sell", sol=(k + 1) * 1_000_000, trader=f"T{k % 3}") for k in range(100)]
    out, fs = build(tmp_path, rows)
    r = EV.hour_events(HOUR, out, fs)
    i = int(np.flatnonzero(r["t"] == HS + 60_000)[0])
    x = r["X"][i]
    fi = C.FIDX
    # window (t-60s, t]: rows k = 1..60 (61 rows would include k=0 at exactly t-60 s: excluded)
    ks = np.arange(1, 61)
    assert x[fi["n_1m"]] == 60
    assert x[fi["uniq_1m"]] == 3
    buys = ks % 4 != 0
    assert x[fi["buy_sol_1m"]] == pytest.approx(((ks + 1)[buys] * 1e6).sum() / 1e9, rel=1e-5)
    assert x[fi["max_sell_1m"]] == pytest.approx(((ks + 1)[~buys]).max() / 1e3, rel=1e-5)
    assert x[fi["ev_sol"]] == pytest.approx(61e-3, rel=1e-5)


def test_gap_exclusion(tmp_path):
    rows = flat_pool_rows(200)
    rows.append({"type": "gap", "prog": "pumpswap", "t_recv_ms": HS + 80_000, "data_loss": True, "from_ms": HS + 70_000, "to_ms": HS + 80_000, "kind": "slot_jump"})
    out, fs = build(tmp_path, rows)
    r = EV.hour_events(HOUR, out, fs)
    t = r["t"]
    assert not ((t >= HS + 70_000) & (t <= HS + 140_000)).any()
    assert (t == HS + 150_000).any() and (t == HS + 60_000).any()


def test_non_wsol_and_low_v_dropped(tmp_path):
    rows = flat_pool_rows(100)
    rows += [dict(ps_row(HS + k * 1000, pool="BadV", v=16_000_000_000)) for k in range(50)]
    r2 = [dict(ps_row(HS + k * 1000, pool="UsdcP")) for k in range(50)]
    for r in r2:
        r["quote_mint"] = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    out, fs = build(tmp_path, rows + r2)
    res = EV.hour_events(HOUR, out, fs)
    assert len(np.unique(res["pool"])) == 1


def test_path_codes_ratio_gap_and_censor(tmp_path):
    # price doubles at +60 s (b halves); data only for hour H -> after the hour end the next hour file is missing: gap
    rows = flat_pool_rows(3600, b_fn=lambda k: 1e15 if k < 60 else 5e14)
    out, fs = build(tmp_path, rows)
    res = EV.hour_events(HOUR, out, fs)
    i = 0
    p = res["path"][i]
    g = C.GRID_S
    k30 = int(np.flatnonzero(g == 30)[0])
    k120 = int(np.flatnonzero(g == 120)[0]) if (g == 120).any() else int(np.flatnonzero(g >= 120)[0])
    r30 = C.DECODE[p[k30]]
    r120 = C.DECODE[p[k120]]
    assert r30 == pytest.approx(1.0, abs=0.01)
    assert r120 == pytest.approx(2.0, rel=0.02)
    # beyond the hour: next hour file missing -> gap codes
    kend = int(np.flatnonzero(g >= 3600 + 600)[0])
    assert p[kend] == C.C_GAP


def test_curve_complete_marks_migrated(tmp_path):
    rows = [curve_row(HS + k * 1000, q=30e9 + k * 1e7, b=1.073e15 - k * 1e9) for k in range(120)]
    rows.append({"type": "complete", "t_recv_ms": HS + 100_000, "mint": "CurveM"})
    out, fs = build(tmp_path, rows)
    res = EV.hour_events(HOUR, out, fs)
    p = res["path"][0]
    kk = int(np.flatnonzero(C.GRID_S >= 100)[0])
    assert p[kk] == C.C_MIGRATED and p[kk - 3] not in (C.C_MIGRATED, C.C_CENS)


# ---------------------------------------------------------------------------------------------- evaluator: exits
def codes(ratios):
    r = np.full(C.NG, np.nan)
    r[: len(ratios)] = ratios
    last = ratios[-1]
    r[len(ratios):] = last
    return C.encode_ratio(r)


def sim(path_ratios, **ex):
    exit_ = {"tp": None, "sl": None, "trail": None, "max_hold_s": 86400, **ex}
    P = np.stack([codes(path_ratios)])
    kx, r, reason, cen = E.simulate_paths(P, exit_)
    return int(kx[0]), float(r[0]), int(reason[0]), bool(cen[0])


def test_exit_tp_executes_next_grid_point():
    kx, r, why, cen = sim([1.0, 1.1, 1.6, 1.9, 1.9], tp=0.5)
    assert why == 3 and kx == 3 and r == pytest.approx(1.9, rel=0.01) and not cen


def test_exit_sl():
    kx, r, why, _ = sim([1.0, 0.9, 0.65, 0.5, 0.5], sl=0.3)
    assert why == 1 and kx == 3 and r == pytest.approx(0.5, rel=0.01)


def test_exit_trail_uses_running_high():
    kx, r, why, _ = sim([1.0, 1.5, 2.0, 1.7, 1.6, 1.6], trail=0.2)
    # 1.6 <= 2.0 * 0.8 first at grid index 4 (1.7 is above 1.6); executes one grid step later, index 5
    assert why == 2 and kx == 5


def test_exit_time_when_no_trigger():
    kx, r, why, _ = sim([1.0, 1.0, 1.0], max_hold_s=60)
    assert why == 0 and C.GRID_S[kx] >= 60 and C.GRID_S[kx - 1] < 60


def test_stop_beats_tp_at_same_point_and_first_event_wins():
    # a single jump point that satisfies both is impossible for tp+sl; check trail vs tp tie: trail needs a high first
    kx, r, why, _ = sim([1.0, 1.0, 0.6, 0.6], sl=0.3, tp=0.5)
    assert why == 1
    kx, r, why, _ = sim([1.0, 1.6, 0.6, 0.6], sl=0.3, tp=0.5)
    assert why == 3  # tp came first (index 1), exit at index 2 prices 0.6


def test_gap_points_do_not_trigger():
    c = codes([1.0, 1.0, 0.5, 0.5])
    c[2] = C.C_GAP
    kx, r, why, _ = E.simulate_paths(np.stack([c]), {"tp": None, "sl": 0.3, "trail": None, "max_hold_s": 86400})
    # gap at 2 is skipped; 0.5 at index 3 triggers
    assert int(why[0]) == 1 and int(kx[0]) == 4


def test_censored_and_migrated():
    c = codes([1.0, 1.0, 1.0])
    c[5:] = C.C_CENS
    kx, r, why, cen = E.simulate_paths(np.stack([c]), {"tp": None, "sl": None, "trail": None, "max_hold_s": 86400})
    assert bool(cen[0])
    c = codes([1.0, 1.2, 1.3])
    c[3:] = C.C_MIGRATED
    kx, r, why, cen = E.simulate_paths(np.stack([c]), {"tp": None, "sl": None, "trail": None, "max_hold_s": 86400})
    assert int(why[0]) == 4 and int(kx[0]) == 2 and not bool(cen[0]) and float(r[0]) == pytest.approx(1.3, rel=0.01)


def test_dead_pool_triggers_sl_and_loses_stake():
    kx, r, why, _ = sim([1.0, 0.0, 0.0], sl=0.5)
    assert why == 1 and r == 0.0
    pnl = E.trade_pnl(np.array([0.0]), np.array([27e9]), np.array([10e9]), np.array([27e9]), np.array([1e15]), np.array([0.0125]), np.array([1]), 1e8)
    assert pnl[0] == -1e8 - C.SEND_LAMPORTS


# ---------------------------------------------------------------------------------------------- cost math
def test_cost_math_roundtrip_curve():
    Qf, Bf, S, fee = 30e9, 1.073e15, 1e8, 0.0125
    pnl = E.trade_pnl(np.array([1.0]), np.array([Qf]), np.array([Qf - 1e9]), np.array([Qf]), np.array([Bf]), np.array([fee]), np.array([0]), S)[0]
    net = S * (1 - fee)
    tok = Bf * net / (Qf + net)
    Qa, Ba = Qf + net, Bf - tok  # r = 1: organic state = fill state; own buy re-injected
    gross = tok * Qa / (Ba + tok)
    assert gross == pytest.approx(Qa * tok / (Ba + tok))
    assert pnl == pytest.approx(gross * (1 - 0.0125) - S - 2 * 160_000, rel=1e-9)
    assert pnl < 0
    assert -S * 0.05 < pnl < -S * 0.02  # two ~1.25% fees plus impact plus 0.32 mSOL sends


def test_cost_math_pumpswap_uses_fee_tier():
    from tools import arena_stack as F
    Qf, Bf = 28e9, 1e15
    f = F.fee_ppm(np.array([Qf]), np.array([Bf]))[0] / 1e6
    assert C.fee_frac(Qf, Bf, 1) == pytest.approx(f)
    assert C.fee_frac(Qf, Bf, 0) == C.CURVE_FEE


def test_pnl_scales_with_ratio():
    def pn(r):  # consistent constant-product reserves at ratio r
        Qf, Bf = 27e9, 1e15
        Qx = np.sqrt(Qf * Bf * r * Qf / Bf)
        return E.trade_pnl(np.array([r]), np.array([Qx]), np.array([Qx - 17.5e9]), np.array([Qf]), np.array([Bf]), np.array([0.0125]), np.array([1]), 1e8)[0]
    up, flat, dn = pn(2.0), pn(1.0), pn(0.5)
    assert up > flat > dn


# ---------------------------------------------------------------------------------------------- DSL, passes, fitness
GOOD = {"entry": [{"f": "ret_5m", "op": "<", "v": -0.1}], "venue": "any", "cooldown_s": 30, "one_per_pool": True,
        "exit": {"tp": 0.5, "sl": 0.3, "trail": None, "max_hold_s": 3600}, "stake_sol": 0.1}


def test_validate_ok_and_errors():
    assert E.validate_spec(GOOD)["exit"]["tp"] == 0.5
    bad = []
    bad.append({**GOOD, "entry": [{"f": "nope", "op": "<", "v": 1}]})
    bad.append({**GOOD, "entry": [{"f": "ret_5m", "op": "==", "v": 1}]})
    bad.append({**GOOD, "entry": [{"f": "ret_5m", "op": "<", "v": float("nan")}]})
    bad.append({**GOOD, "entry": [{"f": "ret_5m", "op": "<", "v": 1}] * 7})
    bad.append({**GOOD, "venue": 2})
    bad.append({**GOOD, "exit": {"tp": 11.0, "max_hold_s": 60}})
    bad.append({**GOOD, "exit": {"sl": 1.0, "max_hold_s": 60}})
    bad.append({**GOOD, "exit": {"max_hold_s": 86401}})
    bad.append({**GOOD, "exit": {"tp": 1}})
    bad.append({**GOOD, "stake_sol": 5})
    bad.append({**GOOD, "surprise": 1})
    bad.append({**GOOD, "cooldown_s": 1.5})
    for s in bad:
        with pytest.raises(E.SpecError):
            E.validate_spec(s)


def test_cooldown_and_one_per_pool_passes():
    pool = np.array([1, 1, 1, 2, 1])
    t = np.array([0, 10_000, 40_000, 5_000, 50_000])
    keep = E._cooldown(pool, t, 30_000)
    assert sorted(t[keep].tolist()) == [0, 5_000, 40_000]  # 10 s dropped, 40 s kept (>=30 s after 0), 50 s dropped (10 s after 40)
    exit_t = np.array([45_000, 0, 60_000, 0, 0])
    kp = E._one_per_pool(pool, t, exit_t)
    assert sorted(zip(pool[kp].tolist(), t[kp].tolist())) == [(1, 0), (1, 50_000), (2, 5_000)]  # (1,10k) open, (1,40k) open until 60k? 40k < 45k open -> dropped


def mk(n, mean, days=8, std=0.0, top=0.0):
    pnl = np.full(n, mean)
    t = np.arange(n, dtype=np.int64) % days * 86_400_000
    return pnl, t


def test_fitness_edge_cases():
    # r = ex_top3_total / (n - 3) = 0.97 / 97 = 0.01; f = r * sqrt(4) * positive_days / n_days = 0.01 * 2 * 6/8 = 0.015
    base = {"n": 100, "n_days": 8, "positive_days": 6, "ex_top3_total": 0.97, "ci90_low": 0.01, "trades_per_day": 4.0, "best_day_share": 0.2}
    assert E.fitness(base) == pytest.approx(0.015)
    assert E.fitness({**base, "n": 39}) == -math.inf
    assert E.fitness({**base, "n_days": 5}) == -math.inf
    assert E.fitness({**base, "best_day_share": 0.6}) == pytest.approx(0.0075)
    assert E.fitness({**base, "best_day_share": 0.5}) == pytest.approx(0.015)  # exactly 50% is not over
    # ordered (finite) when the robust mean is <= 0: less negative is better, so islands have a gradient
    a = E.fitness({**base, "ex_top3_total": -0.97})
    b = E.fitness({**base, "ex_top3_total": -9.7})
    assert a == pytest.approx(-0.01) and b == pytest.approx(-0.1) and a > b
    assert E.fitness({**base, "ex_top3_total": 0.0}) == 0.0
    # glitch outliers in the top 3 do not move fitness
    assert E.fitness({**base, "total": 300.0}) == E.fitness(base)


def test_metrics_of():
    rng = np.random.default_rng(3)
    n = 200
    pnl = rng.normal(0.01, 0.02, n)
    t = (np.arange(n) % 8) * 86_400_000 + 1000
    m = E.metrics_of(pnl, t, t, t + 60_000, np.zeros(n, int), 15.0)
    assert m["n"] == 200 and m["n_days"] == 8
    assert m["ex_top3_total"] == pytest.approx(pnl.sum() - np.sort(pnl)[-3:].sum())
    assert m["ci90_low"] == pytest.approx(__import__("tools.arena_stack", fromlist=["x"]).boot_lo(pnl))
    assert m["trades_per_day"] == pytest.approx(200 / 15.0)
    assert m["positive_days"] <= 8 and m["max_concurrent"] >= 1
    assert m["fitness"] == pytest.approx(E.fitness(m))
    assert E.metrics_of(np.zeros(0), np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, int), 15.0)["fitness"] == -math.inf


def test_max_concurrent_touching_intervals():
    assert E.max_concurrent(np.array([0, 10]), np.array([10, 20])) == 1
    assert E.max_concurrent(np.array([0, 5]), np.array([10, 20])) == 2


# ---------------------------------------------------------------------------------------------- end to end
def test_end_to_end_evaluate(tmp_path):
    rows = flat_pool_rows(3000, b_fn=lambda k: 1e15 * (1 - 0.0001 * k))  # price rises ~ steadily
    out, fs = build(tmp_path, rows)
    res = EV.write_hour(HOUR, out, fs)
    assert res["events"] > 0
    d = E.Split(out, [HOUR])
    spec = {"entry": [{"f": "venue", "op": ">=", "v": 1}], "venue": 1, "cooldown_s": 0, "one_per_pool": True, "exit": {"tp": 0.05, "sl": None, "trail": None, "max_hold_s": 600}, "stake_sol": 0.1}
    m = E.evaluate(spec, data=d)
    assert m["n"] >= 1 and m["n_candidates"] >= m["n"]
    assert m["exit_reasons"].get("tp", 0) + m["exit_reasons"].get("time", 0) == m["n"]
    # one position per pool: concurrent positions never exceed 1 (single pool)
    assert m["max_concurrent"] == 1
    m2 = E.evaluate({**spec, "one_per_pool": False}, data=d)
    assert m2["n"] >= m["n"] and m2["max_concurrent"] >= 1
    assert E.evaluate(spec, data=d) == m  # deterministic


# ---------------------------------------------------------------------------------------------- reserve pricing (artifact fix)
def _old_pnl(r, Qf, Bf, fee_in, venue, S):
    """the pre-fix formula: reserves rebuilt from the ratio alone"""
    net = S * (1 - fee_in)
    tok = Bf * net / (Qf + net)
    s_ = r * Qf / Bf
    k = Qf * Bf
    Qx, Bx = np.sqrt(k * s_), np.sqrt(k / s_)
    Qa, Ba = Qx + net, Bx - tok
    gross = tok * Qa / (Ba + tok)
    return gross * (1 - C.fee_frac(Qa, Ba, venue)) - S - 2 * C.SEND_LAMPORTS


def test_artifact_tiny_reserve_huge_ratio_is_bounded():
    Qf, Bf, S = 17.6e9, 1e15, 1e8  # a pool with ~0.02 SOL real quote
    real_now = 5e7
    pnl = E.trade_pnl(np.array([1000.0]), np.array([17.6e9 + real_now]), np.array([real_now]), np.array([Qf]), np.array([Bf]), np.array([0.0125]), np.array([1]), S)[0]
    net = S * (1 - 0.0125)
    assert pnl <= real_now + net - S - 2 * C.SEND_LAMPORTS + 1e-6  # proceeds never exceed the real quote after our deposit
    assert pnl < 0.5 * S  # nowhere near the +100x the ratio alone would imply
    # the old formula was not bounded
    assert _old_pnl(1000.0, Qf, Bf, 0.0125, 1, S) > 10 * S


@pytest.mark.parametrize("r", [0.6, 1.0, 1.3, 2.0])
def test_normal_pool_matches_old_numbers_within_1pct(r):
    Qf, Bf, S, fee = 27.5e9, 1.2e15, 1e8, 0.0125
    k = Qf * Bf
    Qx = np.sqrt(k * r * Qf / Bf)  # consistent constant-product reserves at ratio r
    new = E.trade_pnl(np.array([r]), np.array([Qx]), np.array([Qx - 17.58e9]), np.array([Qf]), np.array([Bf]), np.array([fee]), np.array([1]), S)[0]
    old = _old_pnl(np.array([r]), Qf, Bf, fee, np.array([1]), S)[0]
    assert abs(new - old) <= 0.01 * S  # 1% of the stake


def test_real_quote_floor_refuses_entries(tmp_path):
    rows = flat_pool_rows(300, q=1e9)  # real quote 1 SOL < 2 SOL
    out, fs = build(tmp_path, rows)
    assert EV.write_hour(HOUR, out, fs)["events"] > 0
    d = E.Split(out, [HOUR])
    spec = {"entry": [], "venue": "any", "cooldown_s": 0, "one_per_pool": False, "exit": {"max_hold_s": 60}, "stake_sol": 0.1}
    assert E.evaluate(spec, data=d)["n_candidates"] == 0
    assert (d.Rr < C.MIN_REAL_QUOTE).all()


def test_reserve_paths_stored_and_liq_real_feature(tmp_path):
    rows = flat_pool_rows(300)
    out, fs = build(tmp_path, rows)
    r = EV.hour_events(HOUR, out, fs)
    assert r["qpath"].shape == r["path"].shape == r["rpath"].shape
    qt, qr = C.DECODE_RES[r["qpath"][0, 0]], C.DECODE_RES[r["rpath"][0, 0]]
    assert qr == pytest.approx(10e9 + 1e6 * 0.99, rel=1e-3) or qr == pytest.approx(10e9, rel=1e-3)
    assert qt - qr == pytest.approx(V, rel=1e-3)
    assert r["X"][0, C.FIDX["liq_real_sol"]] == pytest.approx(10.0, rel=1e-6)
    assert r["Rr"][0] == pytest.approx(10e9)


def test_schema_mismatch_rebuilds(tmp_path):
    out, fs = build(tmp_path, flat_pool_rows(100))
    done = Path(out) / "ingest" / HOUR / "DONE"
    d = json.loads(done.read_text()); d["schema"] = 1; done.write_text(json.dumps(d))
    r = EV.ingest_hour(HOUR, str(tmp_path / "tape"), out)
    assert "skipped" not in r and json.loads(done.read_text())["schema"] == C.SCHEMA


def test_censoring_is_decided_from_the_entry_not_the_outcome():
    """Near the split end a tp-hitting trade and a time-exit trade with the same entry time are both dropped (old code kept the tp one)."""
    win = codes([1.0, 1.2, 1.8, 1.8]); win[20:] = C.C_CENS
    lose = codes([1.0, 0.9, 0.6, 0.6]); lose[20:] = C.C_CENS
    ex = {"tp": 0.5, "sl": None, "trail": None, "max_hold_s": 86400}
    kx, r, why, cen = E.simulate_paths(np.stack([win, lose]), ex)
    assert list(cen) == [True, True]
    ex2 = {**ex, "max_hold_s": 60}  # a hold that fits before the split end: both kept
    kx, r, why, cen = E.simulate_paths(np.stack([win, lose]), ex2)
    assert list(cen) == [False, False] and int(why[0]) == 3 and int(why[1]) == 0
