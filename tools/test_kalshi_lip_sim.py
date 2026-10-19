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


# ============================================================================================= 6. fills
GOOD = dict(yes=[(0.48, 1000)], no=[(0.50, 1000)])


def fills_of(r):
    return [(f[0], f[1], f[2], f[3], round(f[4] / US - T0 / US, 6)) for f in r.fills]


def two_snaps(u=None, gap=220.0):
    return mk_unit(e=3000.0) if u is None else u, [bsnap(0, **GOOD), bsnap(gap, **GOOD)]


def test_fill_direction_price_block_edges_and_order():
    u, snaps = two_snaps()
    tr = [
        trade(10, "a1", 0.48, 10, "no"),        # YES bid fills: taker 'no' at yes_price == b
        trade(11, "a2", 0.49, 10, "no"),        # yes_price above our bid: no
        trade(12, "a3", 0.40, 10, "yes"),       # taker 'yes' does not hit a YES bid (no_price 0.60 > 0.50)
        trade(13, "a4", 0.30, 10, "yes", block=True),   # block trades ignored
        trade(14, "a5", 0.70, 8, "yes"),        # NO bid fills: taker 'yes', no_price 0.30 <= 0.50, at OUR price 0.50
        trade(15, "a6", 0.45, 4, "no"),         # through the YES bid: fills at OUR price 0.48, not 0.45
    ]
    r = run(u, snaps, tr)
    assert fills_of(r) == [("a1", "yes", 4800, 10.0, 10.0), ("a5", "no", 5000, 8.0, 14.0), ("a6", "yes", 4800, 4.0, 15.0)]


def test_fill_interval_is_open_closed_and_has_the_two_second_delay():
    u, snaps = two_snaps()
    # snapshot 0 at t=0 -> live (2, 222]; snapshot 1 at t=220 -> live (222, ...]
    tr = [trade(1.9, "p0", 0.48, 5, "no"), trade(2.0, "p1", 0.48, 5, "no"),    # at / before the start: no
          trade(2.000001, "p2", 0.48, 3, "no"),
          trade(222.0, "p3", 0.48, 2, "no"),                                   # exactly the end: this interval
          trade(222.000001, "p4", 0.48, 1, "no")]                              # next interval
    r = run(u, snaps, tr)
    assert [(f[0], f[4]) for f in fills_of(r)] == [("p2", 2.000001), ("p3", 222.0), ("p4", 222.000001)]
    # interval of snapshot 0 holds p2, p3 only: the quantity is capped per order, not double counted
    assert sum(f[3] for f in r.fills) == 6.0


def test_fill_quantity_is_min_remaining_and_count_consumed_in_time_then_trade_id_order():
    u, snaps = two_snaps()
    tr = [trade(5, "b", 0.48, 20, "no"), trade(5, "a", 0.48, 20, "no"), trade(6, "c", 0.48, 99, "no")]
    r = run(u, snaps, tr)
    # same created_time: trade_id order a, b ; remaining 25 -> a 20, b 5, c 0
    assert [(f[0], f[3]) for f in r.fills] == [("a", 20.0), ("b", 5.0)]


def test_remaining_size_after_a_fill_is_resized_at_the_next_decision_only_by_inventory():
    u = mk_unit(e=3000.0)
    snaps = [bsnap(0, **GOOD), bsnap(220, **GOOD), bsnap(440, **GOOD)]
    r = run(u, snaps, [trade(10, "a", 0.48, 25, "no")], imax=50)
    d = {round((x[0] - T0) / US): x[2] for x in r.decisions}
    assert d[0][2] == 25 and d[220][2] == 25 and d[220][3] == 25     # I_max 50: still 25 after one fill of 25


# =========================================================================================== 7. U15
def test_u15_hash_is_deterministic_and_about_15_percent():
    us = [sim.u15_u("u1", "yes", f"t{i}") for i in range(10_000)]
    assert us == [sim.u15_u("u1", "yes", f"t{i}") for i in range(10_000)]
    frac = sum(1 for x in us if x < 0.15) / len(us)
    assert 0.14 < frac < 0.16
    assert sim.u15_u("u1", "yes", "x") == int(hashlib.sha256(b"1|u1|yes|x").hexdigest()[:8], 16) / 2 ** 32
    assert sim.u15_u("u1", "yes", "x") != sim.u15_u("u1", "no", "x")


def test_u15_drops_leave_the_order_resting_and_change_the_inventory_path():
    drop = next(f"d{i}" for i in range(500) if sim.u15_u("u1", "yes", f"d{i}") < 0.15)
    keep = next(f"k{i}" for i in range(500) if sim.u15_u("u1", "yes", f"k{i}") >= 0.15)
    u = mk_unit(e=3000.0)
    snaps = [bsnap(0, **GOOD), bsnap(220, **GOOD)]
    tr = [trade(10, drop, 0.48, 25, "no"), trade(20, keep, 0.48, 25, "no")]
    r0 = run(u, snaps, tr, "U0", S=25, imax=25)
    r15 = run(u, snaps, tr, "U15", S=25, imax=25)
    assert [f[0] for f in r0.fills] == [drop]                    # U0: the first print fills all 25, nothing is left
    assert [f[0] for f in r15.fills] == [keep] and r15.dropped == 1   # U15: the dropped fill leaves the order resting
    assert r0.decisions[1][2][2] == 0 and r15.decisions[1][2][2] == 0   # both end long 25 (via different prints)
    # the dropped set is the same across runs (hash), so a re-run is identical
    r15b = run(u, snaps, tr, "U15", S=25, imax=25)
    assert r15b.fills == r15.fills


# ====================================================================================== 8. inventory
def test_inventory_limits_sizes_and_paired_fills_net_the_spread():
    u = mk_unit()
    s = bsnap(0, **GOOD)
    q, _ = sim.decide_quote(u, s, 50.0, 0.0, 25, 50)
    assert (q.sy, q.sn) == (0, 25)                               # net +I_max: YES posts 0
    q, _ = sim.decide_quote(u, s, 0.0, 50.0, 25, 50)
    assert (q.sy, q.sn) == (25, 0)
    q, _ = sim.decide_quote(u, s, 40.0, 0.0, 25, 50)
    assert (q.sy, q.sn) == (10, 25)                              # min(S, I_max - net)
    q, _ = sim.decide_quote(u, s, 30.0, 10.0, 25, 50)
    assert (q.sy, q.sn) == (25, 25)
    paired = [("a", "yes", 4800, 25.0, 0), ("b", "no", 5000, 25.0, 0)]
    for yes_value in (1.0, 0.0):                                 # S x spread whichever way it settles
        assert sim.settle_pnl(paired, yes_value) == pytest.approx(25 * 0.02)
    assert sim.collateral(paired) == pytest.approx(25 * 0.48 + 25 * 0.50)   # no netting credit


def test_decision_uses_only_fills_known_at_the_snapshot_time():
    u = mk_unit(e=3000.0)
    snaps = [bsnap(0, **GOOD), bsnap(220, **GOOD)]
    # a fill at 221.5 (inside (ts_1, ts_1 + L]) belongs to the OLD order and is unknown at decision 1
    r = run(u, snaps, [trade(221.5, "late", 0.48, 25, "no")], imax=25)
    assert r.decisions[1][2][2] == 25                            # sy still 25 at the decision at t=220
    assert [f[0] for f in r.fills] == ["late"]


# ============================================================================== 9. settlement and fees
def test_settlement_pnl_yes_no_scalar_and_worst_cases():
    f = [("a", "yes", 4800, 25.0, 0)]
    assert sim.settle_pnl(f, 1.0) == pytest.approx(25 * 0.52)
    assert sim.settle_pnl(f, 0.0) == pytest.approx(-12.0)
    assert sim.settle_pnl(f, 0.3) == pytest.approx(25 * (0.3 - 0.48))      # scalar: settlement value per side
    n = [("a", "no", 5000, 10.0, 0)]
    assert sim.settle_pnl(n, 0.3) == pytest.approx(10 * (0.7 - 0.5))
    assert sim.settle_worst_structural(f) == pytest.approx(-12.0)
    assert sim.settle_worst_case(f + n) == pytest.approx(-(12.0 + 5.0))


def test_fees_quadratic_zero_maker_fee_ceil_cent_and_assumed_flag():
    assert sim.fee_for_fill(25, 5000, "quadratic", 1.0, "cent") == (0.0, False)
    fee, assumed = sim.fee_for_fill(25, 5000, "quadratic_with_maker_fees", 1.0, "cent")
    assert (fee, assumed) == (0.11, False)                                  # 0.109375 -> next cent
    assert sim.fee_for_fill(25, 5000, "quadratic_with_maker_fees", 1.0, "micro")[0] == pytest.approx(0.109375)
    for ft in ("flat", None, "weird"):
        assert sim.fee_for_fill(25, 5000, ft, 1.0, "cent") == (0.11, True)    # 0.25 x taker rate, flagged
    assert sim.unit_fees([("a", "yes", 5000, 25.0, 0)], "quadratic", 1.0) == (0.0, 0.0, False)
    assert sim.unit_fees([("a", "yes", 5000, 25.0, 0)] * 2, "flat", None) == (0.22, 0.21875, True)


# ===================================================================== conditions: BQ, r(t), interval start
def test_bq_at_price_prints_fill_only_beyond_the_depth_ahead_through_prints_fill_fully():
    book = dict(yes=[(0.48, 100), (0.47, 900)], no=[(0.50, 1000)])
    u = mk_unit(e=3000.0)
    snaps = [bsnap(0, **book), bsnap(220, **book)]
    tr = [trade(10, "p1", 0.48, 60, "no"),      # at price: 100 ahead -> 40 left, no fill
          trade(20, "p2", 0.48, 60, "no"),      # at price: 20 beyond the 40 ahead -> fills 20
          trade(30, "p3", 0.47, 10, "no")]      # through: fills min(remaining 5, 10) = 5
    r = run(u, snaps, tr, "BQ")
    assert [(f[0], f[3]) for f in r.fills] == [("p2", 20.0), ("p3", 5.0)]
    r0 = run(u, snaps, tr, "U0")
    assert [(f[0], f[3]) for f in r0.fills] == [("p1", 25.0)]      # U0: front of queue, the first print fills it all
    # the queue ahead resets at the next decision (cancel and re-post puts us last again)
    tr2 = [trade(230, "p4", 0.48, 99, "no")]
    assert run(u, snaps, tr2, "BQ").fills == []
    assert [(f[0], f[3]) for f in run(u, snaps, [trade(230, "p4", 0.48, 101, "no")], "BQ").fills] == [("p4", 1.0)]


def test_bq_reward_counts_us_last_at_our_level():
    lv = [(4800, 990.0), (4700, 500.0)]
    _v, pro, ip = sim.side_phi(lv, 1000.0, 0.5, 4800, 25.0, mode="prorata")
    _v, back, ib = sim.side_phi(lv, 1000.0, 0.5, 4800, 25.0, mode="back")
    assert ip["our_raw"] == pytest.approx(1000 * 25 / 1015)        # pro rata inside the counted level
    assert ib["our_raw"] == pytest.approx(10.0)                    # only the 10 left under the Target count
    assert back < pro


def test_reward_uses_remaining_size_after_a_fill_and_starts_at_ts_plus_l():
    u = mk_unit(e=3000.0)
    snaps = [bsnap(0, **GOOD), bsnap(220, **GOOD)]
    r = run(u, snaps[:1], [trade(112, "f", 0.48, 25, "no")], e_eff=None) if False else None
    u = mk_unit(e=222.0)                                           # E_eff = 222 -> one live interval (2, 222]
    s0 = bsnap(0, **GOOD)
    r = run(u, [s0], [trade(112, "f", 0.48, 25, "no")])
    v_full = sim.snapshot_value(s0, 4800, 5000, 25.0, 25.0, 1000.0, 0.5)[1]
    v_rest = sim.snapshot_value(s0, 4800, 5000, 0.0, 25.0, 1000.0, 0.5)[1]
    assert 0 < v_rest < v_full
    assert r.acc[0]["c"] == pytest.approx(110 * v_full + 110 * v_rest)      # (2,112] on 25, (112,222] on 0
    assert r.acc[0]["c"] < 220 * v_full                                    # not "s" for the whole interval
    # the weight starts at ts+L: with E_eff = 100 only (2, 100] = 98 s
    u2 = mk_unit(e=100.0)
    r2 = run(u2, [s0])
    assert r2.acc[0]["c"] == pytest.approx(98 * v_full) and r2.quoted_us == sec(98)


# =========================================================================== 11. no look-ahead (property)
def _random_world(rnd):
    snaps, t = [], 0.0
    for _ in range(18):
        t += rnd.choice([120, 220, 230, 600])
        yes = [(round(0.48 - 0.01 * i, 2), rnd.choice([300, 500, 700])) for i in range(rnd.randint(0, 3))]
        no = [(round(0.50 - 0.01 * i, 2), rnd.choice([300, 500, 700])) for i in range(rnd.randint(0, 3))]
        snaps.append(bsnap(t, yes=yes, no=no))
    trades = []
    for i in range(60):
        trades.append(trade(rnd.uniform(0, t), f"t{i:03d}", rnd.choice([0.44, 0.46, 0.48, 0.5, 0.52, 0.55]),
                            rnd.choice([3, 10, 40]), rnd.choice(["yes", "no"]), rnd.random() < 0.1))
    return snaps, trades


@pytest.mark.parametrize("seed", range(8))
def test_no_look_ahead_result_and_later_trades_do_not_move_decisions(seed):
    rnd = random.Random(seed)
    snaps, trades = _random_world(rnd)
    for cell in sim.CELLS:
        u = mk_unit(e=4000.0, psets=((100.0, 600.0, 0.5),))
        base = run(u, snaps, trades, cell, imax=50)
        for res in ("yes", "no", "scalar", None):
            u.result = res                                       # an outcome the pass must never read
            assert run(u, snaps, trades, cell, imax=50).decisions == base.decisions
        t_cut = snaps[9].ts / US - T0 / US
        cut = [x for x in trades if x[0] / US - T0 / US <= t_cut]
        part = run(u, snaps, cut, cell, imax=50)
        early = [d for d in base.decisions if (d[0] - T0) / US <= t_cut]
        assert [d for d in part.decisions if (d[0] - T0) / US <= t_cut] == early
        assert len(early) >= 8


def test_rows_before_the_freeze_are_never_scored():
    u = mk_unit(s0=-30_000.0)
    pre = sim.Snap(FRZ - sec(5), [(4800, 1000.0)], [(5000, 1000.0)])
    u.quote_start_us = FRZ + sec(10)
    assert sim.decide_quote(u, pre, 0, 0, 25, 50) == (None, "Q1")


# ===================================================================================== 12. gate maths
def row(i, event=None, day="2026-10-24", net=1.0, full=True, worst=None, lower=None, gas=False, day_e=None, **kw):
    return dict(unit_id=f"u{i}", event=event or f"E{i}", day=day, day_e=day_e or day, gas=gas, full=full, net=net,
                net_lower=net if lower is None else lower, worst_u=net if worst is None else worst, **kw)


def test_bootstrap_matches_sequential_draws_under_seed_1_and_a_pinned_value():
    s = [1.0, -2.0, 3.0, 0.5, -1.0, 2.0, 0.25]
    n = [1, 2, 1, 1, 1, 2, 1]
    got = sim.cluster_bootstrap(s, n)
    rs = np.random.RandomState(1)
    stats = []
    for _ in range(1000):
        idx = rs.randint(0, len(s), len(s))
        stats.append(sum(s[i] for i in idx) / sum(n[i] for i in idx))
    assert got["p05"] == pytest.approx(float(np.percentile(stats, 5)))
    assert got["p95"] == pytest.approx(float(np.percentile(stats, 95)))
    assert sim.cluster_bootstrap(s, n) == got                    # deterministic
    assert got["p05"] == pytest.approx(PINNED_P05, abs=1e-12)


PINNED_P05 = -0.325


def test_gate_g1_g2_g3_use_full_units_stubs_enter_the_statistics_not_the_counts():
    rows = [row(i, day=f"2026-10-{24 + i % 5}") for i in range(100)]
    g = sim.evaluate_gate(rows)
    assert g["G1"]["pass"] and g["G2"]["pass"] and g["G3"]["pass"] and g["counts_met"]
    stubs = rows[:-1] + [row(99, full=False, net=1.0)]
    g2 = sim.evaluate_gate(stubs)
    assert g2["n_units"] == 100 and g2["n_full"] == 99 and not g2["G1"]["pass"]    # stubs padded n, not the count
    # stubs' losses are in the totals
    losing = rows + [row(200 + i, full=False, net=-5.0, event="EX") for i in range(3)]
    gl = sim.evaluate_gate(losing)
    assert gl["total_net"] == pytest.approx(100 - 15) and gl["n_full"] == 100


def test_gate_g3_day_majority_needs_five_days_and_a_strict_majority():
    def rows_for(day_nets):
        out, i = [], 0
        for d, ns in day_nets.items():
            for x in ns:
                out.append(row(i, day=d, net=x))
                i += 1
        return out
    five = {f"2026-10-2{d}": [1.0, 1.0] for d in range(4, 9)}
    assert sim.evaluate_gate(rows_for(five))["G3"]["pass"]
    four = dict(list(five.items())[:4])
    assert not sim.evaluate_gate(rows_for(four))["G3"]["pass"]
    three_pos = dict(five, **{"2026-10-27": [-1.0, -1.0], "2026-10-28": [-1.0, -1.0]})
    g = sim.evaluate_gate(rows_for(three_pos))["G3"]
    assert g["days_full"] == 5 and g["positive_days"] == 3 and g["pass"]
    two_pos = dict(three_pos, **{"2026-10-26": [-1.0, -1.0]})
    assert not sim.evaluate_gate(rows_for(two_pos))["G3"]["pass"]


def test_gate_g5_top3_removed_and_best_day_reported():
    rows = [row(i, net=x, day=f"2026-10-2{4 + i % 6}") for i, x in enumerate([11, 9, 8, 0.5, 0.5, -1, -1])]
    g = sim.evaluate_gate(rows)
    assert g["G5"]["top3"] == 28 and g["G5"]["total_ex_top3"] == pytest.approx(-1.0) and not g["G5"]["pass"]
    assert g["G5"]["best_day"] == "2026-10-24" and "total_ex_best_day" in g["G5"]
    ok = sim.evaluate_gate([row(i, net=x) for i, x in enumerate([5, 4, 3, 1, 1])])
    assert ok["G5"]["pass"]


def test_gate_g6_structural_worst_with_and_without_a_negative_minimum():
    # worst_u = R - fees + min over {yes, no}; G6 = total + 3 x min_u worst_u > 0
    rows = [row(i, net=2.0, worst=-1.0) for i in range(10)]
    g = sim.evaluate_gate(rows)
    assert g["G6"]["value"] == pytest.approx(20 - 3) and g["G6"]["pass"]
    rows[0]["worst_u"] = -30.0
    g = sim.evaluate_gate(rows)
    assert g["G6"]["value"] == pytest.approx(20 - 90) and not g["G6"]["pass"]
    nonneg = sim.evaluate_gate([row(i, net=2.0, worst=0.5) for i in range(10)])
    assert nonneg["G6"]["value"] == pytest.approx(20.0) and nonneg["G6"]["pass"]    # no credit for a positive minimum


def test_gate_g7_uses_the_cells_own_lower_reward():
    rows = [row(i, net=2.0, lower=-0.5) for i in range(10)]
    assert not sim.evaluate_gate(rows)["G7"]["pass"]
    assert sim.evaluate_gate([row(i, net=2.0, lower=0.5) for i in range(10)])["G7"]["pass"]


def test_g4b_clusters_gas_units_by_date_e():
    # 40 gas units over 2 dates: G4 treats each as its own event, G4b has 2 gas clusters
    rows = [row(i, event=f"G{i}", net=(1.0 if i < 20 else 0.2), gas=True, day_e="2026-10-24" if i < 20 else "2026-10-25")
            for i in range(40)] + [row(100 + i, event=f"X{i}", net=0.5) for i in range(10)]
    g = sim.evaluate_gate(rows)
    assert g["G4"]["clusters"] == 50 and g["G4b"]["clusters"] == 12
    assert g["G4b"]["p05"] < g["G4"]["p05"]
    assert sim.key_gas_day(rows[0]) == "GAS|2026-10-24" and sim.key_gas_day(rows[-1]) == "X9"
    bad = [row(i, event=f"G{i}", net=-1.0 if i < 20 else 3.0, gas=True, day_e="2026-10-24" if i < 20 else "2026-10-25")
           for i in range(40)]
    g2 = sim.evaluate_gate(bad)
    assert g2["G4b"]["pass"] is False                                # both G4 and G4b must be > 0


def test_kill_checks_k1_k2():
    base = 1_791_000_000 * US
    day = 86_400 * US
    losing = [dict(row(i, event=f"E{i}", day=f"d{i % 4}", net=-3.5), resolved_us=base + (i % 4) * day) for i in range(60)]
    ks = sim.kill_checks(losing, 25, [base + 5 * day])
    assert ks["dead_as_frozen"] and ks["checks"][0]["K1"] and ks["checks"][0]["K2"]        # -210 <= -200 ; p95 < 0
    mild = [dict(row(i, event=f"E{i}", day=f"d{i % 4}", net=-0.5), resolved_us=base) for i in range(20)]
    ks = sim.kill_checks(mild, 25, [base + day])
    assert not ks["dead_as_frozen"] and not ks["checks"][0]["K2"]                          # < 60 units: no K2
    few_days = [dict(row(i, event=f"E{i}", day="d0", net=-50.0), resolved_us=base) for i in range(10)]
    assert sim.kill_checks(few_days, 25, [base + day])["checks"] == []                    # needs >= 3 unit-days
