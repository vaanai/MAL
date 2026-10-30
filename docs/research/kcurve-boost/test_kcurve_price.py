"""Tests for docs/research/kcurve-boost/kcurve_price.py (PREREG section 7 parity (i)-(vi), C4, C5). Synthetic pools only, parsed by the kit's own
build_hour; no tape file is opened. Run: /data/mal/venv/bin/python -m pytest -q docs/research/kcurve-boost/test_kcurve_price.py"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

import kcurve_price as K  # noqa: E402
from tools import arena_paper as P  # noqa: E402
from tools import arena_screen as S  # noqa: E402
from tools import arena_stack as F  # noqa: E402

V = 17_585_000_000
T0 = int(datetime(2026, 8, 20, 10, tzinfo=timezone.utc).timestamp())
SLOT0 = 440_000_000
RATE = 2.5  # slots per second (0.4 s a slot)


def pool_lines(pool: str, mint: str, m: int, rng: np.random.Generator, n_slots: int = 1100, q0: float = 67.4e9, seed_b: float = K.SEED_B,
               shuffle_first: bool = True) -> list[dict]:
    """A reserve-consistent print chain from the opening state (q0 real, seed base): pre-trade reserves, as the tape has them."""
    q, b = q0, float(seed_b)
    rows = []
    slot = m
    while slot < m + n_slots:
        k = int(rng.integers(1, 4)) if slot - m < 20 else int(rng.integers(0, 2))
        slot_rows = []
        for _ in range(k):
            buy = bool(rng.random() < 0.6)
            if buy:
                s = float(rng.uniform(0.05, 2.0)) * 1e9
                tok = math.floor(b * s / (q + V + s))
                slot_rows.append({"side": "buy", "sol_lamports": int(s), "token_raw": int(tok), "quote_reserve": int(q), "base_reserve": int(b)})
                q, b = q + s, b - tok
            else:
                tok = math.floor(b * float(rng.uniform(0.001, 0.01)))
                s = (q + V) * tok / (b + tok)
                slot_rows.append({"side": "sell", "sol_lamports": int(s), "token_raw": int(tok), "quote_reserve": int(q), "base_reserve": int(b)})
                q, b = q - s, b + tok
        if slot == m and shuffle_first:
            rng.shuffle(slot_rows)
        for e, r in enumerate(slot_rows):
            r.update(venue="pumpswap", quote_is_wsol=True, pool=pool, mint=mint, slot=slot, tx_index=e, event_index=0,
                     block_time=T0 + int((slot - SLOT0) / RATE))
            rows.append(r)
        slot += int(rng.integers(1, 4))
    return rows


def hours_of(rows: list[dict], vmap: dict) -> list:
    by = {}
    for r in rows:
        h = (r["block_time"] - T0) // 3600
        by.setdefault(h, []).append(json.dumps(r, separators=(",", ":")).encode())
    out = []
    for i, h in enumerate(sorted(by)):
        out.append(S.build_hour(by[h], block="explore-0814", T=T0 + 3600 * h, idx=i, fmt="backfill", vmap=S.VMap([vmap])))
    return out


def make_pools(n: int, seed: int = 1):
    rng = np.random.default_rng(seed)
    rows, vmap, creates = [], {}, {}
    for i in range(n):
        pool, mint = f"POOL{i}", f"MINT{i}"
        m = SLOT0 + 50 + i * 37
        rows += pool_lines(pool, mint, m, rng)
        vmap[pool] = V
        creates[mint] = (T0 + int((m - SLOT0) / RATE) - 300, False, f"CR{i}")
    return rows, vmap, creates


def run_pass(rows, vmap, creates, price=True):
    bp = K.BlockPass(0, "explore-0814", creates, price=price)
    prev = None
    for hr in hours_of(rows, vmap):
        bp.hour(hr, prev)
        prev = hr.T
    bp.finish()
    return bp


@pytest.fixture(scope="module")
def priced():
    rows, vmap, creates = make_pools(30)
    bp = run_pass(rows, vmap, creates)
    pools = {}
    for r in rows:
        pools.setdefault(r["pool"], []).append(r)
    return bp, pools, vmap


def test_root_is_found_without_within_slot_order(priced):
    bp, pools, vmap = priced
    assert len(bp.trips) == 30 * len(K.CELLS)
    rng = np.random.default_rng(7)
    rows = pool_lines("PX", "MX", SLOT0 + 5, rng)
    hr = hours_of(rows, {"PX": V})[0]
    o = K.opening(hr, 0)
    assert o["unique"] and o["b0"] == K.SEED_B and o["q0"] == 67.4e9


def test_two_roots_are_not_unique():
    rows = [{"venue": "pumpswap", "quote_is_wsol": True, "pool": "P2", "mint": "M2", "slot": SLOT0 + 1, "tx_index": i, "event_index": 0, "block_time": T0 + 1,
             "side": "buy", "sol_lamports": 10**9, "token_raw": 10**12, "quote_reserve": 6e10, "base_reserve": K.SEED_B - i * 7} for i in range(2)]
    o = K.opening(hours_of(rows, {"P2": V})[0], 0)
    assert o["unique"] is False and o["seed_cand"] is True


def _one(t: dict) -> dict:
    return {"ref": t["ref"], "Qe": {K.LAT: t["Qe"]}, "Be": {K.LAT: t["Be"]}, "ssb": {K.LAT: t["ssb"]}, "nearby": {K.LAT: t["nearby"]}, "Qx": t["Qx"], "Bx": t["Bx"]}


def test_parity_i_legs_equal_price_leg_to_the_lamport(priced):
    bp, _, _ = priced
    trips = bp.trips
    assert len(trips) >= 200
    st = K.kit_st(trips)
    cfg = S.screen_cfg(S.RENT_DECIDING)
    for rule in S.STRAND_RULES:
        L = S.legs(st, 0.25e9, K.LAT, rule, cfg, intercept=cfg.press_intercept)
        for i, t in enumerate(trips):
            ref = P.price_leg("pumpswap", 0.25e9, _one(t), K.LAT, cfg, rule)
            for k in ("pnl", "flat", "press"):
                assert abs(float(L[k][i]) - ref[k]) < 1e-6, (rule, i, k)


def _arrays(rows):
    hrs = hours_of(rows, {rows[0]["pool"]: V})
    return {k: np.concatenate([K._cols(h, 0)[k] for h in hrs]) for k in ("sl", "ib", "so", "tk", "q", "b", "v")}


def test_parity_i_iii_iv_entry_exit_and_worst_in_slot_equal_the_kit(priced):
    bp, pools, _ = priced
    by_pool = {}
    for t in bp.trips:
        by_pool.setdefault(t["pool"], []).append(t)
    import zlib
    checked = 0
    for name, rows in list(pools.items())[:12]:
        a = _arrays(rows)
        trips = {t["cell"]: t for t in by_pool[zlib.crc32(name.encode())]}
        m = int(a["sl"][0])
        sps = 1.0 / RATE
        xs = m + int(np.rint(K.HOLD_S * RATE))
        ex = S.pool_states(a, np.array([m]), np.array([sps]), np.array([xs]), np.array([sps]))  # (iii)
        assert trips["K-1"]["Qx"] == float(ex["Qx"][0]) and trips["K-1"]["Bx"] == float(ex["Bx"][0])
        for k in K.KS:
            sdp = m + k - S.D - int(F.k_slots_vec(S.LAT_DECIDING, np.array([sps]), "ceil")[0])  # the kit decision slot whose landing is m + k
            kst = S.pool_states(a, np.array([sdp]), np.array([sps]), np.array([xs]), np.array([sps]))
            t = trips[f"K{k}"]
            assert t["Qe"] == float(kst["Qe"][S.LAT_DECIDING][0]) and t["Be"] == float(kst["Be"][S.LAT_DECIDING][0])  # (i) second half
            need = np.array([max(xs + S.D + int(F.exit_lag_vec(np.array([sps]))[0]) + 1, sdp + S.D + int(F.k_slots_vec(S.LAT_DECIDING, np.array([sps]), "ceil")[0]) + 1)])
            w = S.worst_in_slot_states(name, a, np.array([sdp]), np.array([sps]), np.array([xs]), np.array([sps]), need, np.array([False]), np.array([False]), kst)
            assert t["wQe"] == float(w["wQe"][S.LAT_DECIDING][0]) and t["wBe"] == float(w["wBe"][S.LAT_DECIDING][0])  # (iv)
            assert t["wQx"] == float(w["wQx"][0]) and t["wBx"] == float(w["wBx"][0])
            assert not w["w_mismatch"][0] and not t["w_mismatch"]
            checked += 1
    assert checked == 12 * len(K.KS)


def test_k_minus_1_is_the_opening_state_with_k0_pressure_inputs(priced):
    bp, _, _ = priced
    by = {}
    for t in bp.trips:
        by.setdefault(t["pool"], {})[t["cell"]] = t
    for cells in by.values():
        k1, k0 = cells["K-1"], cells["K0"]
        assert k1["Qe"] == 67.4e9 + V and k1["Be"] == K.SEED_B and k1["ref"] == k1["Qe"] / k1["Be"]
        assert (k1["wQe"], k1["wBe"]) == (k1["Qe"], k1["Be"])  # C4: worst-in-slot entry = opening state
        assert (k1["ssb"], k1["nearby"]) == (k0["ssb"], k0["nearby"])  # C4: K0's arrays at x = m
        assert all(c["ref"] == k1["ref"] for c in cells.values())  # Q7: one guard reference, the opening price


def test_universe_counts_and_exclusions():
    rows, vmap, creates = make_pools(6, seed=3)
    creates["MINT1"] = (creates["MINT1"][0] + 290, False, "CR1")  # instant: 10 s before s0 -> not organic
    creates["MINT2"] = (creates["MINT2"][0], True, "CR2")  # Mayhem
    del creates["MINT3"]  # missing create
    vmap["POOL4"] = 0  # V out of band
    bp = run_pass(rows, vmap, creates)
    c = bp.counts[next(iter(bp.counts))]
    assert c["seed_opening"] == 6 and c["seed_opening_vband"] == 5 and c["v_out_of_band"] == 1
    assert c["create_missing"] == 1 and c["mayhem_or_unknown"] == 1 and c["u_all"] == 3 and c["u_organic"] == 2
    org = {t["pool"]: t["organic"] for t in bp.trips}
    assert sorted(org.values()) == [False, True, True]


def test_precount_prices_nothing():
    rows, vmap, creates = make_pools(4, seed=5)
    bp = run_pass(rows, vmap, creates, price=False)
    assert bp.trips == [] and sum(c["u_organic"] for c in bp.counts.values()) == 4


def test_refusal_lines():
    ok = {"explore-0814": {"d": {"u_organic": 150, "seed_candidates": 200, "root_not_unique": 1, "seed_opening": 199, "v_unknown": 2,
                                 "seed_opening_vband": 190, "create_missing": 10}}}
    assert K.refusals(ok) == []
    bad = {"explore-0814": {"d": dict(ok["explore-0814"]["d"], create_missing=60, u_organic=50)}}
    why = K.refusals(bad)
    assert any(w.startswith("STOP_RETURN_TO_QP") for w in why) and any("U pools 50 < 100" in w for w in why)


def test_cell_result_runs_and_is_stamped(priced):
    bp, _, _ = priced
    trips = [t for t in bp.trips if t["cell"] == "K-1" and not t["excl"]]
    r = K.cell_result("K-1", trips, 0, ["explore-0814"], 1)
    assert r["stamp"] == K.STAMP and r["verdict"] and "remainder_margin" in r["report_only"]
    assert r["report_only"]["remainder_margin"]["M_bar_sol"] == pytest.approx(
        np.mean([0.25 * (0.25 / 115.0054 + 0.25 / (t["Qe"] / 1e9) + 0.25 / (t["Qx"] / 1e9)) for t in trips]))


# --- C5 (v) and (vi): refusals -------------------------------------------------------------------------------------------------------------------
def test_v_kit_pin_mismatch_refuses():
    with pytest.raises(K.Refused, match="kit_pin_mismatch"):
        K.check_kit_pins({"tools/arena_screen.py": "0" * 64})
    K.check_kit_pins()  # the pinned files at this checkout


def test_vi_refusals(tmp_path):
    with pytest.raises(S.Refused, match="not_an_exploration_block"):
        K.check_blocks([str(tmp_path)])
    with pytest.raises(S.Refused, match="arena_path_refused"):
        K.check_blocks(["/data/mal/arena/tape"])
    with pytest.raises(K.Refused, match="2026-10-01"):
        K.check_hours([(datetime(2026, 10, 1, tzinfo=timezone.utc), "x")])
    K.check_hours([(datetime(2026, 9, 30, 23, tzinfo=timezone.utc), "x")])
    with pytest.raises(K.Refused, match="deciding stake"):
        K.check_stake(0.5)
    with pytest.raises(K.Refused, match="forbidden"):
        K.check_out(tmp_path / "probe-out")
    assert K.main(["--out", str(tmp_path / "o"), "--stake-sol", "1.0"]) == 2


def test_vii_adapter_defines_no_pricing_or_statistics_function():
    import ast
    src = (HERE / "kcurve_price.py").read_text()
    defs = {n.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}
    for banned in ("round_trip", "price_leg", "legs", "boot_lo", "stat", "kill_verdict", "fail_legs", "book_fail_legs", "press_intercept", "fee_ppm",
                   "mint_states", "worst_index", "slot_states", "screen_p_cell"):
        assert banned not in defs


def test_pool_across_an_hour_boundary_keeps_its_rows():
    rng = np.random.default_rng(11)
    m = SLOT0 + int(3450 * RATE)  # opens 150 s before the hour ends; its 330 s exit is in the next hour
    rows = pool_lines("PH", "MH", m, rng)
    assert len({(r["block_time"] - T0) // 3600 for r in rows}) == 2
    bp = run_pass(rows, {"PH": V}, {"MH": (T0 + int((m - SLOT0) / RATE) - 300, False, "C")})
    t = {x["cell"]: x for x in bp.trips}
    a = _arrays(rows)
    hrs = hours_of(rows, {"PH": V})
    r0 = hrs[0].slots_per_s  # the adapter uses each hour's own slot rate, as the kit does
    xs = m + int(np.rint(K.HOLD_S * r0))
    rx = hrs[1].slots_per_s if xs >= hrs[1].min_slot else r0
    ex = S.pool_states(a, np.array([m]), np.array([1 / r0]), np.array([xs]), np.array([1 / rx]))
    assert t["K-1"]["Qx"] == float(ex["Qx"][0]) and not t["K-1"]["excl"]
    assert xs >= hrs[1].min_slot  # the exit really is in the second hour
