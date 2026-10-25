"""Tests for tools/p30_laneg_rule.py and tools/p30_laneg_screen.py on synthetic hours. No real tape, no network."""
from __future__ import annotations

import copy
import json
import os

import numpy as np
import pytest

from tools import arena_paper as P
from tools import arena_screen as S
from tools import p30_laneg_rule as R
from tools import p30_laneg_screen as RUN

os.environ[P.TEST_ENV] = "1"
V = 17_584_505_288
T0 = 1_755_000_000 - (1_755_000_000 % 3600)  # an hour start


class VM:
    def get(self, pool):
        return float(V)


def make_hour(idx: int, pools: dict, T: int | None = None):
    """pools: name -> list of (dt_seconds_in_hour, side, sol, price_mult). Reserves are built so that the post-trade price follows price_mult."""
    T = T0 + 3600 * idx if T is None else T
    lines = []
    slot0 = 300_000_000 + idx * 9000
    for pool, prints in pools.items():
        for k, (dt, side, sol, mult) in enumerate(prints):
            q = 40e9
            b = (q + V) / (mult * 1e-3)  # price (q+V)/b = mult*1e-3
            lines.append(json.dumps(separators=(",", ":"), obj={"venue": "pumpswap", "pool": pool, "mint": "M" + pool, "slot": slot0 + int(dt * 2.5), "side": side, "sol_lamports": int(sol),
                                     "token_raw": int(sol / (mult * 1e-3)), "quote_reserve": int(q), "base_reserve": int(b), "block_time": T + int(dt), "event_ts": T + int(dt),
                                     "quote_is_wsol": True, "tx_index": k, "event_index": 0, "trader": f"w{k % 12}"}).encode())
    return S.build_hour(lines, block="t", T=T, idx=idx, fmt="backfill", vmap=VM())


def steady(mult_fn, n=120, sol=2e9, side_fn=lambda k: "buy" if k % 2 == 0 else "sell"):
    return [(k * 30, side_fn(k), sol, mult_fn(k)) for k in range(n)]


def run_rule(hours, rule):
    st = S.RunState(block="t")
    rule.start_block(st)
    prev, out = None, []
    for hr in hours:
        snap = (copy.deepcopy(st.scratch), dict(st.first_seen))
        st.prev, st.hour_index = prev, hr.idx
        out.append((hr, snap, rule.triggers(hr, st)))
        for i, nm in enumerate(hr.pools.tolist()):
            st.first_seen.setdefault(nm, int(hr.bt[hr.ptr[i]]))
        st.gap_before = False
        prev = hr
    return st, out


def test_grid_is_48_unique_cells():
    n = R.cell_names()
    assert len(n) == 48 == len(set(n))
    assert len(R.entry_names()) == 24


def test_legacy_pools_and_young_pools_do_not_trigger():
    # hour 0..4: "OLD" prints in hour 0 (legacy: unknown age), "NEW" first prints in hour 1. A +10% pump in the last half of every hour for both.
    def px(k):
        return 1.0 + (0.15 if (k % 120) > 90 else 0.0)

    hours = []
    for i in range(5):
        pools = {"OLD": steady(px)}
        if i >= 1:
            pools["NEW"] = steady(px)
        hours.append(make_hour(i, pools))
    rule = R.LanegRule()
    st, out = run_rule(hours, rule)
    trig = [(hr.idx, p) for hr, _, t in out for (p, _, _) in t]
    assert all(p == "NEW" for _, p in trig)  # OLD is legacy for the whole block
    assert not any(i <= 2 for i, p in trig)  # NEW first printed in hour 1 (age < 2 h before hour 3 start... marks need >= 2 h since first print)
    assert st.scratch["c"]["legacy_skipped"] > 0


def test_momentum_trigger_causal_and_throttled():
    # pool born in hour 1; flat price then a +12% jump (many prints) late in hour 4: MOM/MOM2 fire; throttle limits to one per 30 min per entry rule
    def px(k):
        return 1.0 if k < 100 else 1.12

    hours = []
    for i in range(6):
        pools = {"OLD": steady(lambda k: 1.0), "NEW": steady(lambda k: (px(k) if i == 4 else 1.0))}
        if i == 0:
            pools = {"OLD": pools["OLD"]}
        hours.append(make_hour(i, pools))
    rule = R.LanegRule()
    st, out = run_rule(hours, rule)
    fired = [(hr.idx, p, sp.cell) for hr, _, t in out for (p, _, sp) in t if p == "NEW" and hr.idx == 4]
    cells = {c for _, _, c in fired}
    assert any(c.startswith("MOM-F0-A0|") for c in cells) and any(c.startswith("MOM2-F0-A0|") for c in cells)
    assert not any(c.startswith(("PULL", "KNIFE", "BNC")) for c in cells)
    per_entry = {}
    for _, _, c in fired:
        per_entry.setdefault(c.split("|")[0], 0)
        per_entry[c.split("|")[0]] += 1
    assert per_entry["MOM-F0-A0"] == 2  # one trigger x two exit cells (X1, X2) in the 30 min throttle window
    # causal: each trigger refires when the hour is cut after its decision slot
    for hr, snap, t in out:
        for (p, slot, sp) in t[:3]:
            state = S.RunState(block="t", scratch=copy.deepcopy(snap[0]), first_seen=dict(snap[1]))
            prev = [h for h, _, _ in out if h.idx == hr.idx - 1]
            state.prev = prev[0] if prev else None
            state.hour_index = hr.idx
            assert S.causal_probe(R.LanegRule(), hr, state, p, slot)


def test_inactive_pool_blocked_by_filters():
    # 5 prints / 15 min: fails the prints filter whatever the price does
    def sparse(k):
        return [(k2 * 400, "buy" if k2 % 2 == 0 else "sell", 2e9, 1.0 if k2 < 6 else 1.3) for k2 in range(9)]

    hours = [make_hour(0, {"OLD": steady(lambda k: 1.0)})] + [make_hour(i, {"NEW": sparse(0)} if i else {}) for i in range(1, 6)]
    hours[1] = make_hour(1, {"NEW": steady(lambda k: 1.0)})
    st, out = run_rule(hours, R.LanegRule())
    assert not [1 for hr, _, t in out if hr.idx >= 3 for _ in t]


def test_one_cell_rule_and_unknown_cell():
    r = R.LanegRule(only=["PULL-F1-A0|X1"])
    assert [c.name for c in r.cells()] == ["PULL-F1-A0|X1"]
    with pytest.raises(ValueError):
        R.LanegRule(only=["nope|X1"])


def test_reality_check_null_and_signal():
    rng = np.random.default_rng(5)
    days, cells = 14, 48
    s = np.zeros((cells, days))
    n = np.full((cells, days), 20.0)
    for c in range(cells):
        s[c] = rng.normal(0.0, 0.5, days)  # zero-mean cells
    p_null = RUN.reality_check(s, n, B=2000)["p"]
    assert p_null > 0.05  # uniform under the null; seed fixed
    s2 = s.copy()
    s2[7] += 3.0  # one cell with a strong, every-day edge
    p_sig = RUN.reality_check(s2, n, B=2000)["p"]
    assert p_sig < 0.05
    assert RUN.reality_check(np.zeros((0, 3)), np.zeros((0, 3)))["p"] is None


def test_block_and_validation_refusals(tmp_path):
    with pytest.raises(S.Refused):
        RUN.check_blocks(["/data/mal/clean-view/oracle-insample-2026-09-22_25"], True)
    with pytest.raises(S.Refused):
        RUN.check_blocks(["/data/mal/arena/tape"], True)
    ns = type("A", (), {})()
    ns.out, ns.hours, ns.count_only, ns.only, ns.validation, ns.block = str(tmp_path), None, False, None, False, [RUN.VALID]
    with pytest.raises(S.Refused):  # fresh-0903 only with --validation and one frozen cell
        RUN.run(ns)
    ns.validation, ns.only = True, ["a", "b"]
    with pytest.raises(S.Refused):
        RUN.run(ns)
