"""Tests for tools/kalshi_lip_sim.py.  Synthetic stores only: no network, no real recorder row, trade or
settled result is read.  Spec: hunt18/kalshi-lip SIM-SPEC.md (14 tests + mutation checks) on top of
DECLARE-FORWARD.md, DECLARE-FORWARD-CONDITIONS.md and DECLARE-FORWARD-VERIFY-1.md."""
import hashlib
import json
import math
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
import pyarrow.parquet as pq
import pytest

import kalshi_lip_sim as sim
import kalshi_public as kp
from kalshi_lip_sim import BP, US, make_snap, make_trade

T0 = sim.iso_to_us("2026-10-19T12:00:00Z")      # a unit period start
FRZ = sim.FREEZE_US


def sec(x):
    return int(round(x * US))


def mk_unit(*, s0=0.0, e=10_000.0, qs=None, e_eff=None, psets=((100.0, 1000.0, 0.5),), uid="u1", **kw):
    """A Unit with times in seconds after T0."""
    u = sim.Unit(unit_id=uid, ticker="KXAAAGASDNY-26OCT20-T3.5", series="KXAAAGASDNY", s0_us=T0 + sec(s0),
                 e_us=T0 + sec(e), first_poll_us=T0 + sec(s0), drift=False, freq="daily", fee_type="quadratic",
                 fee_mult=1.0, gas=True, event="KXAAAGASDNY-26OCT20", x_us=None, source="obrec-1019c")
    u.quote_start_us = T0 + sec(s0 if qs is None else qs)
    u.e_eff_us = T0 + sec(e if e_eff is None else e_eff)
    u.psets = list(psets)
    for k, v in kw.items():
        setattr(u, k, v)
    return u


def bsnap(t, yes=((0.48, 600), (0.47, 600)), no=((0.50, 600), (0.49, 600))):
    return make_snap(T0 / US + t, yes, no)


def run(u, snaps, trades=(), cell="U0", S=25, imax=50, **kw):
    tl = sim.TradeList(list(trades))
    return sim.run_unit(u, snaps, tl, cell, S, imax, keep_decisions=True, **kw)


def trade(t, tid, yes, count, taker, block=False):
    return make_trade(T0 / US + t, tid, yes, count, taker, block)


# ================================================================================ 1. reward arithmetic
def test_reward_arithmetic_hand_computed():
    lv = [(5000, 300.0), (4900, 400.0), (4800, 500.0)]          # 0.50: 300, 0.49: 400, 0.48: 500
    valid, phi, info = sim.side_phi(lv, 1000.0, 0.5, 5000, 0.0)
    assert valid and info["ref_bp"] == 5000                      # T/5 = 200 is reached at the first level
    assert info["raw_total"] == pytest.approx(300 * 1 + 400 * 0.5 + 300 * 0.25)   # counted 300, 400, 300
    assert phi == 0.0                                            # no order of ours
    valid, phi, info = sim.side_phi(lv, 1000.0, 0.5, 5000, 25.0)
    # with our 25 at 0.50: levels 325/400/500, counted 325/400/275, multipliers 1/0.5/0.25
    raw = 325 + 400 * 0.5 + 275 * 0.25
    assert info["raw_total"] == pytest.approx(raw) and raw == pytest.approx(593.75)
    assert phi == pytest.approx(25 / 593.75)


def test_reference_price_is_one_fifth_of_target_from_the_best_bid():
    # VERIFY-1: Reference = first level where cumulative size reaches T/5 (=200 for T=1000)
    for sizes, want in [((100, 99, 900), 4800), ((100, 100, 900), 4900), ((200, 5, 900), 5000), ((199, 1, 900), 4900)]:
        lv = [(5000, float(sizes[0])), (4900, float(sizes[1])), (4800, float(sizes[2]))]
        _v, _p, info = sim.side_phi(lv, 1000.0, 0.5, 5000, 0.0)
        assert info["ref_bp"] == want, sizes
    # a side that never reaches the Target qualifies nothing
    valid, phi, _ = sim.side_phi([(5000, 999.0)], 1000.0, 0.5, 5000, 0.0)
    assert not valid and phi == 0.0


def test_scores_are_normalised_per_side_not_across_sides():
    # the same recorded book on both sides: each side's phi is our_raw / that side's raw total
    snap = make_snap(0, yes=[(0.48, 300), (0.47, 400), (0.46, 500)], no=[(0.50, 975), (0.49, 400)])
    vy, py, iy = sim.side_phi(snap.yes, 1000.0, 0.5, 4800, 25.0)
    vn, pn, inn = sim.side_phi(snap.no, 1000.0, 0.5, 5000, 25.0)
    assert vy and vn
    assert py == pytest.approx(iy["our_raw"] / iy["raw_total"]) and pn == pytest.approx(inn["our_raw"] / inn["raw_total"])
    assert iy["raw_total"] != inn["raw_total"]
    v, val = sim.snapshot_value(snap, 4800, 5000, 25.0, 25.0, 1000.0, 0.5)
    assert v and val == pytest.approx(py + pn)                   # v_k = phi_YES + phi_NO, each normalised alone


# ====================================================================================== 2. help example
def test_help_article_example_16_dollars():
    # 20% share x $100 x 8,000/10,000 valid = $16.00 : 10,000 s, 8,000 valid, phi sum 0.4
    good = dict(yes=[(0.48, 100)], no=[(0.50, 100)])
    snaps = [bsnap(100 * k, **good) if k < 80 else bsnap(100 * k, yes=[(0.48, 100)], no=[]) for k in range(100)]
    u = mk_unit(psets=((100.0, 100.0, 1.0),))
    r = run(u, snaps)
    # each side: rival 100 + ours 25 = 125 at one level, counted min(125, 100) = 100, ours 20 -> phi 0.2
    assert sim.side_phi(snaps[0].yes, 100.0, 1.0, 4800, 25.0)[1] == pytest.approx(0.2)
    rew = sim.finalize_rewards(r, u)
    assert rew["c"] == 16.00
    assert sim.apply_min_payout(rew["c"]) == 16.00


# ===================================================================================== 3. validity / Q4
def test_validity_and_q4_boundary():
    # one side below T: v = 0
    snap = make_snap(0, yes=[(0.48, 900)], no=[(0.50, 1200)])
    assert sim.snapshot_value(snap, 4800, 5000, 0.0, 25.0, 1000.0, 0.5) == (False, 0.0)
    # our 100 lifts the side to exactly T: valid
    ok, v = sim.snapshot_value(snap, 4800, 5000, 100.0, 25.0, 1000.0, 0.5)
    assert ok and v > 0
    u = mk_unit()
    # Q4: rival depth >= T - s is the boundary (975 + 25 = 1000)
    edge = make_snap(T0 / US + 1, yes=[(0.48, 975)], no=[(0.50, 975)])
    q, why = sim.decide_quote(u, edge, 0.0, 0.0, 25, 50)
    assert q is not None and why == "ok"
    under = make_snap(T0 / US + 1, yes=[(0.48, 974.5)], no=[(0.50, 975)])
    assert sim.decide_quote(u, under, 0.0, 0.0, 25, 50) == (None, "Q4")
    # at +I_max the YES side posts 0, so the YES side needs the full T from rivals alone
    assert sim.decide_quote(u, edge, 50.0, 0.0, 25, 50) == (None, "Q4")
    full = make_snap(T0 / US + 1, yes=[(0.48, 1000)], no=[(0.50, 975)])
    q, _ = sim.decide_quote(u, full, 50.0, 0.0, 25, 50)
    assert q.sy == 0 and q.sn == 25


# =============================================================== 4. pro-rata, Reference boundary, DF ticks
def test_prorata_at_crossing_level_reference_boundary_and_df_ticks():
    # crossing level: 600 + 600 on the second level; room 1000-600=400 counted pro rata among rivals+ours
    lv = [(5000, 600.0), (4900, 500.0)]
    valid, phi, info = sim.side_phi(lv, 1000.0, 0.5, 4900, 100.0)
    # level 2 = 600 (500 rival + 100 ours), room 400 -> counted 400, ours 400*100/600
    assert valid and info["raw_total"] == pytest.approx(600 + 400 * 0.5)
    assert info["our_raw"] == pytest.approx(400 * 100 / 600 * 0.5)
    # Reference exactly at a level boundary: cum == T/5 -> that level is the Reference
    _v, _p, i1 = sim.side_phi([(5000, 200.0), (4900, 900.0)], 1000.0, 0.5, 5000, 0.0)
    assert i1["ref_bp"] == 5000
    _v, _p, i2 = sim.side_phi([(5000, 199.99), (4900, 900.0)], 1000.0, 0.5, 5000, 0.0)
    assert i2["ref_bp"] == 4900
    # DF^ticks on the 1-cent grid: 0.50 ref, 0.49 -> DF^1, 0.47 -> DF^3
    lv = [(5000, 300.0), (4900, 300.0), (4700, 400.0)]
    _v, _p, info = sim.side_phi(lv, 1000.0, 0.5, 5000, 0.0)
    assert info["raw_total"] == pytest.approx(300 + 300 * 0.5 + 400 * 0.5 ** 3)
    assert sim._ticks(5000, 4700) == 3 and sim._ticks(5000, 5000) == 0


def test_crossing_level_alternative_reading_is_reported_not_central():
    lv = [(5000, 600.0), (4900, 900.0)]
    _v, _p, a = sim.side_phi(lv, 1000.0, 0.5, 5000, 25.0)
    _v, _p, b = sim.side_phi(lv, 1000.0, 0.5, 5000, 25.0, cross_full=True)
    assert b["raw_total"] > a["raw_total"]                       # crossing level counted in full lowers phi
    assert "xw" in sim.VARIANTS and "xw" not in ("c",)


# ======================================================================================= 5. time weights
def test_gap_cut_at_cap_last_snapshot_at_e_eff_and_floor():
    good = dict(yes=[(0.48, 1000)], no=[(0.50, 1000)])
    u = mk_unit(e=2000.0, e_eff=1000.0, psets=((100.0, 1000.0, 0.5),))
    s = [bsnap(0, **good), bsnap(900, **good)]                   # gap 900 > CAP 450: cut at CAP
    r = run(u, s)
    v = sim.snapshot_value(s[0], 4800, 5000, 25.0, 25.0, 1000.0, 0.5)[1]
    # first interval (2, 452]: 450 s; last snapshot at 900: min(CAP, E_eff-900=100)-2 live: (902, 1000] = 98 s
    assert r.acc[0]["c"] == pytest.approx(450 * v + 98 * v)
    assert r.blind_gaps == 1 and r.blind_us == sec(450)
    assert r.quoted_us == sec(450 + 98)


def test_no_weight_before_quote_start_and_floor_rules():
    good = dict(yes=[(0.48, 1000)], no=[(0.50, 1000)])
    u = mk_unit(qs=500.0)
    r = run(u, [bsnap(0, **good), bsnap(100, **good), bsnap(500, **good)])
    assert r.n_snaps_window == 1 and r.n_snaps_quoted == 1       # snapshots before quote_start never count
    assert sim.floor_cent(0.999) == 0.99 and sim.floor_cent(1.0) == 1.0 and sim.floor_cent(16.0) == 16.0
    assert sim.floor_cent(1.0099) == 1.0 and sim.floor_cent(16 * 0.999999999999) == 16.0   # float noise tolerance
    assert sim.apply_min_payout(0.99) == 0.0 and sim.apply_min_payout(1.00) == 1.00


def test_lower_central_upper_ordering_property():
    rnd = random.Random(7)
    for _ in range(25):
        u = mk_unit(e=3000.0)
        snaps = []
        for k in range(14):
            yes = [(0.48, 960 + rnd.choice([0, 40, 300])), (0.47, 400)]
            no = [(0.50, rnd.choice([100, 600])), (0.49, rnd.choice([0, 500]))]
            snaps.append(bsnap(200 * k + rnd.randint(0, 30), yes=yes, no=[x for x in no if x[1] > 0]))
        r = run(u, snaps)
        rew = sim.finalize_rewards(r, u)
        assert rew["lower"] <= rew["c"] <= rew["upper"]
