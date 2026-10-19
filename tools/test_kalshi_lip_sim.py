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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # repo root: ``tools`` is a package
import numpy as np
import pyarrow.parquet as pq
import pytest

from tools import kalshi_lip_sim as sim          # the same module objects the simulator imports (one SealError class)
from tools import kalshi_public as kp
from tools.kalshi_lip_sim import BP, US, make_snap, make_trade

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
        trade(13, "a4", 0.48, 10, "no", block=True),    # block trades ignored (it would otherwise fill the YES bid)
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


# ======================================================================== synthetic stores (the "world")
def dt(us):
    return datetime.fromtimestamp(us / US, tz=timezone.utc)


def at(iso):
    return sim.iso_to_us(iso)


def book_run(snaps_by_ticker):
    """-> (orderbook rows, snaps rows) for kalshi_public tables from {ticker: [Snap]}."""
    ob, sn = [], []
    for tk, snaps in snaps_by_ticker.items():
        s = kp.series_of(tk)
        for x in snaps:
            for is_yes, lv in ((True, x.yes), (False, x.no)):
                for p, q in lv:
                    ob.append({"ts": dt(x.ts), "ticker": tk, "series_ticker": s, "side": "yes" if is_yes else "no",
                               "price": p / BP, "size": q})
            sn.append({"ts": dt(x.ts), "ticker": tk, "series_ticker": s, "n_yes_levels": len(x.yes),
                       "n_no_levels": len(x.no), "attempts": 1})
    return ob, sn


def ticks(t_start_iso, t_end_iso, step, book=None, jitter=0.0, **kw):
    """Snapshots every ``step`` s from t_start to t_end (exclusive)."""
    a, b = at(t_start_iso), at(t_end_iso)
    book = dict(GOOD, **kw) if book is None else book
    out, t = [], a
    while t < b:
        out.append(make_snap(t / US, **book))
        t += sec(step)
    return out


import contextlib
from unittest import mock

WRITE_AT = "2026-10-20T04:30:00Z"      # written_at of every store file the World writes (not the sandbox clock)


@contextlib.contextmanager
def fixed_now(iso):
    with mock.patch.object(kp, "now_utc", lambda: dt(at(iso))):
        yield


class _Client:
    requests_made, backoffs = 0, []


class World:
    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.kal = self.tmp / "kalshi"
        self.kal.mkdir(parents=True, exist_ok=True)
        self.books = []

    # -- recorders -------------------------------------------------------------------------------
    def add_run(self, name, snaps_by_ticker, series, seal=True, complete=True, registry=True, extra_unsealed=None):
        d = self.tmp / name
        st = kp.Store(d, "orderbook-snap", {})
        ob, sn = book_run(snaps_by_ticker)
        paths = [p for p in st.write_partitioned("orderbook", ob, "p0", "ts") if p]
        paths += [p for p in st.write_partitioned("orderbook_snaps", sn, "p0", "ts") if p]
        allts = sorted({r["ts"] for r in sn})
        runs = [{"run_id": "r1", "event": "run_start", "ts": allts[0] if allts else dt(FRZ), "series_json": json.dumps(series),
                 "every_s": 60.0}]
        if complete:
            runs.append({"run_id": "r1", "event": "run_end", "ts": allts[-1] if allts else dt(FRZ), "complete": True,
                         "stop_reason": "duration"})
        paths += [st.write("orderbook_runs", runs, "p0r")]
        n_open = len(snaps_by_ticker)
        rounds = [{"run_id": "r1", "round": i, "ts": t, "n_open": n_open, "n_snaps_ok": n_open, "n_errors": 0,
                   "every_eff": 60.0, "stretched": False} for i, t in enumerate(allts)]
        if rounds:
            paths.append(st.write("orderbook_rounds", rounds, "p0rd"))
        if seal:
            kp.seal_parts(d, "2026-10-19T12", paths)
        if extra_unsealed:
            ob2, sn2 = book_run(extra_unsealed)
            st.write_partitioned("orderbook", ob2, "p1", "ts")
            st.write_partitioned("orderbook_snaps", sn2, "p1", "ts")
        if registry:
            reg = self.tmp / f"obrec-REGISTRY-{name[len('obrec-'):]}.md"
            reg.write_text("registry: " + ",".join(series) + "\n")
            os.utime(reg, (1_600_000_000, 1_600_000_000))
        self.books.append(str(d))
        return d

    # -- LIP programs ----------------------------------------------------------------------------
    def add_polls(self, polls):
        """polls: [(poll_iso, ok, [program api dicts])]"""
        d = self.tmp / "lipsnap-1019"
        st = kp.Store(d, "incentive-snap", {})
        rows, prog, runs = [], [], []
        paths = []
        for i, (piso, ok, progs) in enumerate(polls, 1):
            pt = dt(at(piso))
            rows.append({"run_id": "r1", "poll": i, "poll_ts": pt, "ts_end": pt, "ok": ok,
                         "end_reason": "cursor_end" if ok else "http_error", "n_pages": 1,
                         "n_rows": len(progs) if ok else 0, "n_distinct_ids": len(progs)})
            if ok:
                for r in progs:
                    prog.append(kp.incentive_row(r, "r1", i, pt, pt, 1, pt))
        runs = [{"run_id": "r1", "event": "run_start", "ts": dt(FRZ), "every_s": 900.0},
                {"run_id": "r1", "event": "run_end", "ts": dt(FRZ), "complete": True, "stop_reason": "duration"}]
        paths.append(st.write("incentive_polls", rows, "p0"))
        paths.append(st.write("incentive_runs", runs, "p0r"))
        for date in sorted({r["poll_ts"].strftime("%Y-%m-%d") for r in prog}):
            paths.append(st.write("incentive_programs", [r for r in prog if r["poll_ts"].strftime("%Y-%m-%d") == date],
                                  "p0" + date, date=date))
        kp.seal_parts(d, "2026-10-19T12", paths)
        self.lip = str(d)
        return d

    # -- metadata, trades, results, series ------------------------------------------------------
    def add_meta(self, name_stamp, markets):
        d = self.tmp / "meta"
        d.mkdir(exist_ok=True)
        (d / f"markets-{name_stamp}.json").write_text(json.dumps({"markets": markets, "cursor": ""}))
        self.meta = str(d)

    def add_trades(self, rows, done, since="2026-10-19T03:33:16Z", until="2026-10-20T00:00:00Z", name="trades",
                   run_at="2026-10-20T04:41:00Z", run_record=True):
        """A full-history trades download.  ``until`` is explicit (an open-ended one never counts as coverage);
        the manifest gets the ``run`` record kalshi_public writes at the end of a run, stamped ``run_at``."""
        st = kp.Store(self.kal, "trades", {"series": name, "since": since, "until": until, "market": None})
        rr = [{"trade_id": r[0], "ticker": r[1], "series_ticker": kp.series_of(r[1]), "created_time": dt(at(r[2])),
               "yes_price": r[3], "no_price": round(1 - r[3], 6), "count": r[4], "taker_side": r[5],
               "is_block_trade": False, "source": "live"} for r in rows]
        with fixed_now(WRITE_AT):
            if rr:
                st.write_partitioned("trades", rr, "t-" + name, "created_time")
            for t in done:
                st.mark_done(t)
        if run_record:
            with fixed_now(run_at):
                st.run_record(_Client(), complete=True)

    def add_markets(self, rows):
        st = kp.Store(self.kal, "markets", {"series": "x"})
        rr = [{"ticker": t, "event_ticker": ev, "series_ticker": kp.series_of(t), "status": "finalized", "result": res,
               "settlement_value_dollars": val, "settlement_ts": dt(at(sts)), "fetched_at": dt(at(fetched))}
              for (t, ev, res, val, sts, fetched) in rows]
        with fixed_now(WRITE_AT):
            st.write_partitioned("markets", rr, "m0", "settlement_ts")

    def add_series(self, rows):
        st = kp.Store(self.kal, "series", {})
        rr = [{"ticker": t, "frequency": f, "fee_type": ft, "fee_multiplier": 1.0, "fetched_at": dt(FRZ)} for t, f, ft in rows]
        with fixed_now(WRITE_AT):
            st.write("series", rr, "s0", date="2026-10-19")

    def cfg(self, **kw):
        base = dict(books=self.books, programs=self.lip, meta=getattr(self, "meta", None), trades=str(self.kal),
                    markets=str(self.kal), series=str(self.kal), read_us=at("2026-10-24T00:00:00Z"),
                    units_evidence=self.evidence(), size=25.0)
        base.update(kw)
        return sim.Cfg(**base)

    def evidence(self):
        p = self.tmp / "evidence.txt"
        p.write_text("period_reward centi-cents https://docs.kalshi.com/api-reference/incentive-programs/get-incentives.md "
                     "fetched 2026-10-19 VERIFIED\n")
        return str(p)


def prog(pid, ticker, start, end, reward=1_000_000, target="1000.00", bps=5000, typ="liquidity"):
    return {"id": pid, "market_ticker": ticker, "market_id": "m-" + pid, "incentive_type": typ,
            "start_date": start, "end_date": end, "period_reward": reward, "target_size_fp": target,
            "discount_factor_bps": bps, "paid_out": False}


GAS_A, GAS_B = "KXAAAGASDNY-26OCT19-T3.5", "KXAAAGASDFL-26OCT19-T3.5"
S0, E_ = "2026-10-19T12:00:00Z", "2026-10-19T14:00:00Z"


def basic_world(tmp, extra_prog=(), extra_snaps=None, meta=True):
    w = World(tmp)
    snaps = {GAS_A: ticks("2026-10-19T12:05:30Z", "2026-10-19T15:30:00Z", 60),
             GAS_B: ticks("2026-10-19T12:05:30Z", "2026-10-19T15:30:00Z", 60)}
    snaps.update(extra_snaps or {})
    w.add_run("obrec-1019c", snaps, sorted({kp.series_of(t) for t in snaps}))
    progs = [prog("pA", GAS_A, S0, E_), prog("pB", GAS_B, S0, E_)] + list(extra_prog)
    w.add_polls([("2026-10-19T03:20:00Z", True, []), ("2026-10-19T12:05:00Z", True, progs),
                 ("2026-10-19T12:20:00Z", True, progs), ("2026-10-19T14:05:00Z", True, [])])
    w.add_series([("KXAAAGASDNY", "daily", "quadratic"), ("KXAAAGASDFL", "daily", "quadratic_with_maker_fees"),
                  ("KXRT", "custom", "quadratic"), ("KXEOWEEK", "weekly", "quadratic")])
    rows = [("tA1", GAS_A, "2026-10-19T12:10:00Z", 0.48, 25, "no"), ("tA2", GAS_A, "2026-10-19T12:30:00Z", 0.50, 10, "yes"),
            ("tB1", GAS_B, "2026-10-19T12:11:00Z", 0.48, 7, "no")]
    w.add_trades(rows, [GAS_A, GAS_B] + [p["market_ticker"] for p in extra_prog])
    w.add_markets([(GAS_A, "KXAAAGASDNY-26OCT19", "yes", 1.0, "2026-10-19T15:00:00Z", "2026-10-20T04:41:00Z"),
                   (GAS_B, "KXAAAGASDFL-26OCT19", "no", 0.0, "2026-10-19T15:00:00Z", "2026-10-20T04:41:00Z")])
    if meta:
        w.add_meta("20261019T100000Z", [{"ticker": GAS_A, "event_ticker": "KXAAAGASDNY-26OCT19",
                                          "close_time": "2026-10-19T14:00:00Z", "expected_expiration_time": "2026-10-19T14:30:00Z"}])
    return w


# ========================================================================================= integration
@pytest.fixture
def covered(monkeypatch):
    """The synthetic worlds are a few hours long: neutralise the 70% coverage rule where it is not the subject."""
    monkeypatch.setattr(sim, "coverage", lambda *a, **k: {"not_decidable": False, "days": {}})


def test_end_to_end_units_cells_outputs(tmp_path, covered):
    w = basic_world(tmp_path)
    cfg = w.cfg(out=str(tmp_path / "out"))
    g = sim.run_sim(cfg)
    out = Path(cfg.out)
    for f in ("units.parquet", "fills.parquet", "days.parquet", "gate.json", "table.txt"):
        assert (out / f).exists(), f
    units = pq.read_table(out / "units.parquet").to_pylist()
    assert {u["cell"] for u in units} == {"U0", "U15", "BQ"} and {u["unit_id"] for u in units} == {"pA", "pB"}
    a0 = next(u for u in units if u["unit_id"] == "pA" and u["cell"] == "U0")
    assert a0["status"] == "opened" and a0["resolution"] == "settled" and a0["source"] == "obrec-1019c"
    assert a0["fills_YES"] == 25.0 and a0["fills_NO"] == 10.0 and a0["n_fills"] == 2      # tA1 hits YES, tA2 hits NO
    assert a0["settle_pnl"] == pytest.approx(25 * (1.0 - 0.48) + 10 * (0.0 - 0.50))        # result yes
    assert a0["reward_central"] > 0 and a0["fees_cent"] == 0.0 and a0["unit_day"] == "2026-10-19"
    b0 = next(u for u in units if u["unit_id"] == "pB" and u["cell"] == "U0")
    assert b0["fees_cent"] == 0.04 and b0["settle_pnl"] == pytest.approx(7 * (0.0 - 0.48))   # maker fee ceil to cent
    fills = pq.read_table(out / "fills.parquet").to_pylist()
    assert any(f["trade_id"] == "tA1" and f["price"] == 0.48 and f["cell"] == "U0" for f in fills)
    gate = json.loads((out / "gate.json").read_text())
    assert gate["freeze"] == sim.FREEZE_ISO and gate["declare_sha256"].startswith("977fa41c")
    assert gate["conditions_sha256"].startswith("35ee7e65") and set(gate["cells"]) == {"U0", "U15", "BQ"}
    assert gate["verdict"] == "COUNTS_NOT_MET" and gate["deciding"] is True and gate["spec_notes"]
    assert gate["cells"]["U0"]["gate"]["n_units"] == 2 and gate["exclusions"] == {}
    assert "formula screen" in (out / "table.txt").read_text()


def test_every_exclusion_reason_is_counted(tmp_path, covered):
    extra = [
        prog("pLong", "KXAAAGASDNC-26OCT19-T3.5", "2026-10-19T12:00:00Z", "2026-10-26T12:00:00Z"),     # 168 h
        prog("pLate", "KXAAAGASDOH-26OCT19-T3.5", "2026-10-19T10:00:00Z", "2026-10-19T14:00:00Z"),     # first poll 2h05 after start
        prog("pWeek", "KXEOWEEK-26OCT19-T1", S0, E_),                                                  # weekly, X > E + 24 h
        prog("pNoMeta", "KXEOWEEK-26OCT19-T2", S0, E_),                                                # weekly, no capture
        prog("pRT", "KXRT-26OCT19-T3", S0, E_),                                                        # custom freq, X <= E + 24h : kept
        prog("pNoTr", "KXAAAGASDGA-26OCT19-T3.5", S0, E_),                                             # trades not complete
        prog("pTyp", "KXAAAGASDMI-26OCT19-T3.5", S0, E_, typ="volume"),
        prog("pZero", "KXAAAGASDPA-26OCT19-T3.5", S0, E_, reward=0),
        prog("pPre", "KXAAAGASDIL-26OCT19-T3.5", S0, E_),                                              # listed in a pre-freeze poll
    ]
    tickers = [p["market_ticker"] for p in extra]
    snaps = {t: ticks("2026-10-19T12:05:30Z", "2026-10-19T13:30:00Z", 60) for t in tickers}
    w = World(tmp_path)
    allsnaps = dict(snaps)
    allsnaps[GAS_A] = ticks("2026-10-19T12:05:30Z", "2026-10-19T15:30:00Z", 60)
    w.add_run("obrec-1019c", allsnaps, sorted({kp.series_of(t) for t in allsnaps}))
    progs = [prog("pA", GAS_A, S0, E_)] + extra
    pre = [p for p in extra if p["id"] == "pPre"]
    w.add_polls([("2026-10-19T03:20:00Z", True, pre), ("2026-10-19T12:05:00Z", True, progs), ("2026-10-19T14:05:00Z", True, [])])
    w.add_series([("KXRT", "custom", "quadratic"), ("KXEOWEEK", "weekly", "quadratic")] + [(f"KXAAAGASD{s}", "daily", "quadratic") for s in
                 ("NY", "FL", "IL", "PA", "OH", "GA", "MI", "NC")])
    done = [t for t in tickers if "NoTr" not in t and "GASDGA" not in t] + [GAS_A]
    w.add_trades([], done)
    w.add_markets([])
    w.add_meta("20261019T100000Z", [
        {"ticker": "KXEOWEEK-26OCT19-T1", "event_ticker": "KXEOWEEK-26OCT19", "close_time": E_, "expected_expiration_time": "2026-10-22T14:00:00Z"},
        {"ticker": "KXRT-26OCT19-T3", "event_ticker": "KXRT-26OCT19", "close_time": E_, "expected_expiration_time": "2026-10-19T14:30:00Z"}])
    g = sim.run_sim(w.cfg(out=str(tmp_path / "out")))
    ex = g["exclusions"]
    assert ex["excl_long_period"] == 1 and ex["excl_late_poll"] == 1 and ex["excl_not_daily"] == 1
    assert ex["excl_no_meta"] == 1 and ex["excl_no_trades"] == 1 and ex["excl_type"] == 1
    assert ex["excl_program_fields"] == 1 and ex["excl_pre_freeze"] == 1
    units = {u["unit_id"]: u for u in pq.read_table(tmp_path / "out" / "units.parquet").to_pylist()}
    assert units["pRT"]["status"] == "opened" and units["pA"]["status"] == "opened" and units["pWeek"]["status"] == "excl_not_daily"
    # excluded units with a quotable book count against the 10% rule (2 would-open of 4 here)
    assert g["exclusion_share"]["excluded_would_open"] == 2 and g["verdict"] == "NOT_DECIDABLE"
    assert any("excluded units" in r for r in g["nd_reasons"])


def test_dry_run_prints_structure_only_and_never_opens_trades_or_results(tmp_path, monkeypatch):
    w = basic_world(tmp_path)
    monkeypatch.setattr(sim, "load_trades", lambda *a, **k: pytest.fail("dry run opened the trades parquet"))
    monkeypatch.setattr(sim, "read_results", lambda *a, **k: pytest.fail("dry run opened settled results"))
    monkeypatch.setattr(sim, "run_unit", lambda *a, **k: pytest.fail("dry run ran the pass"))
    rep = sim.run_sim(w.cfg(dry_run=True, read_us=None))
    assert rep["mode"] == "dry_run" and rep["structurally_opened_units"] == 2
    assert rep["events"] == 2 and rep["unit_days"] == 1 and rep["snapshots_in_window"] > 100
    assert rep["carry_forward_gaps"]["gaps_over_cap"] == 0 and rep["units_by_source"] == {"obrec-1019c": 2}
    blob = json.dumps(rep).lower()
    for word in ("reward", "net", "pnl", "p&l", "markout", "fill"):
        assert word not in blob.replace("no reward, fill, markout or p&l computed", "").replace("trades parquet", "")


def test_runs_are_byte_identical(tmp_path, covered):
    w = basic_world(tmp_path)
    digests = []
    for i in (1, 2):
        sim.run_sim(w.cfg(out=str(tmp_path / f"o{i}")))
        digests.append({f.name: hashlib.md5(f.read_bytes()).hexdigest() for f in sorted((tmp_path / f"o{i}").iterdir())})
    assert digests[0] == digests[1] and "units.parquet" in digests[0] and "gate.json" in digests[0]


def test_units_evidence_missing_makes_the_read_not_decidable_and_computes_nothing(tmp_path, monkeypatch):
    w = basic_world(tmp_path)
    monkeypatch.setattr(sim, "run_unit", lambda *a, **k: pytest.fail("computed without verified units"))
    g = sim.run_sim(w.cfg(out=str(tmp_path / "o"), units_evidence=None))
    assert g["verdict"] == "NOT_DECIDABLE" and "units" in g["nd_reasons"][0]
    assert json.loads((tmp_path / "o" / "gate.json").read_text())["cells"] == {}


# ============================================================================== sealed / registry / sources
def test_only_sealed_chain_verified_parts_are_read(tmp_path):
    late = {GAS_A: ticks("2026-10-19T15:30:00Z", "2026-10-19T15:40:00Z", 60)}
    w = World(tmp_path)
    d = w.add_run("obrec-1019c", {GAS_A: ticks("2026-10-19T12:05:30Z", "2026-10-19T12:15:30Z", 60)}, ["KXAAAGASDNY"],
                  extra_unsealed=late)
    src = sim.BookSource(d)
    assert len(src.unsealed) >= 2                                   # the open hour is listed, never read
    got, bad = src.window_snaps(GAS_A, at("2026-10-19T00:00:00Z"), at("2026-10-20T00:00:00Z"))
    assert len(got) == 10 and bad == 0 and max(s.ts for s in got) < at("2026-10-19T12:16:00Z")
    with pytest.raises(kp.SealError, match="not covered"):
        kp.verify_parts(d)                                          # the repo's strict check refuses what we tolerate
    # a fully sealed store: our tolerant check equals the repo's
    w2 = World(tmp_path / "w2")
    d2 = w2.add_run("obrec-1019c", {GAS_A: ticks("2026-10-19T12:05:30Z", "2026-10-19T12:10:30Z", 60)}, ["KXAAAGASDNY"])
    e, unsealed = sim.sealed_entries(d2)
    assert e == kp.verify_parts(d2) and unsealed == []


def test_altered_or_broken_sealed_parts_are_refused(tmp_path):
    w = World(tmp_path)
    d = w.add_run("obrec-1019c", {GAS_A: ticks("2026-10-19T12:05:30Z", "2026-10-19T12:10:30Z", 60)}, ["KXAAAGASDNY"])
    part = sorted((d / "orderbook_snaps").rglob("*.parquet"))[0]
    good = part.read_bytes()
    part.write_bytes(good + b"x")
    with pytest.raises(kp.SealError, match="mismatch"):
        sim.sealed_entries(d)
    part.write_bytes(good)
    chain = d / kp.CHAIN_NAME
    os.chmod(chain, 0o640)
    chain.write_text(chain.read_text().replace('"prev":"' + "0" * 64, '"prev":"' + "1" * 64))
    with pytest.raises(kp.SealError, match="prev mismatch"):
        sim.sealed_entries(d)


def test_strict_runs_refuses_a_run_without_run_end(tmp_path):
    w = World(tmp_path)
    d = w.add_run("obrec-1019c", {GAS_A: ticks("2026-10-19T12:05:30Z", "2026-10-19T12:10:30Z", 60)}, ["KXAAAGASDNY"],
                  complete=False)
    assert sim.BookSource(d).entries                                 # default: sealed parts of a live run are readable
    with pytest.raises(kp.SealError, match="run_end"):
        sim.BookSource(d, strict_runs=True)


def test_rows_before_freeze_ignored_and_inconsistent_snapshots_dropped(tmp_path):
    pre = [make_snap(FRZ / US - 600 + 60 * i, **GOOD) for i in range(3)]
    post = ticks("2026-10-19T12:05:30Z", "2026-10-19T12:08:30Z", 60)
    w = World(tmp_path)
    d = w.add_run("obrec-1019c", {GAS_A: pre + post}, ["KXAAAGASDNY"])
    src = sim.BookSource(d)
    got, _ = src.window_snaps(GAS_A, at("2026-10-19T00:00:00Z"), at("2026-10-20T00:00:00Z"))
    assert [s.ts for s in got] == [s.ts for s in post] and src.stats["rows_before_freeze_ignored"] > 0
    # a snapshot whose level rows do not match its recorded level count is a gap, never a guess
    st = kp.Store(tmp_path / "obrec-bad", "orderbook-snap", {})
    ob, sn = book_run({GAS_A: post})
    sn[1]["n_yes_levels"] = 3
    paths = [p for p in st.write_partitioned("orderbook", ob, "p", "ts") if p]
    paths += [p for p in st.write_partitioned("orderbook_snaps", sn, "p", "ts") if p]
    kp.seal_parts(tmp_path / "obrec-bad", "2026-10-19T12", paths)
    got, bad = sim.BookSource(tmp_path / "obrec-bad").window_snaps(GAS_A, at("2026-10-19T00:00:00Z"), at("2026-10-20T00:00:00Z"))
    assert bad == 1 and len(got) == len(post) - 1


def test_registry_must_precede_the_run_and_list_its_series(tmp_path):
    w = World(tmp_path)
    snaps = {GAS_A: ticks("2026-10-19T12:05:30Z", "2026-10-19T12:10:30Z", 60)}
    d = w.add_run("obrec-1019c", snaps, ["KXAAAGASDNY"])
    ents, _ = sim.sealed_entries(d)
    assert sim.check_registry(d, ents) == []
    reg = tmp_path / "obrec-REGISTRY-1019c.md"
    reg.write_text("nothing relevant\n")
    os.utime(reg, (1_600_000_000, 1_600_000_000))
    assert "not in" in sim.check_registry(d, ents)[0]
    reg.write_text("KXAAAGASDNY\n")                                   # written AFTER the first row
    os.utime(reg, (2_000_000_000, 2_000_000_000))
    assert "modified after the first row" in sim.check_registry(d, ents)[0]
    reg.unlink()
    assert "missing" in sim.check_registry(d, ents)[0]
    w2 = World(tmp_path / "x")
    w2.add_run("obrec-1019d", snaps, ["KXAAAGASDNY"], registry=False)
    w2.add_polls([("2026-10-19T12:05:00Z", True, [])])
    with pytest.raises(sim.InputError, match="registry"):
        sim.load_inputs(w2.cfg())


def two_run_world(tmp, gap_d=45.0, hole=False, gap_c=60.0):
    w = World(tmp)
    c_snaps = {GAS_A: ticks("2026-10-19T12:05:30Z", "2026-10-19T14:00:00Z", gap_c),
               "KXRT-26OCT19-T3": ticks("2026-10-19T12:05:30Z", "2026-10-19T14:00:00Z", gap_c)}
    d_snaps = ticks("2026-10-19T12:05:40Z", "2026-10-19T14:00:00Z", gap_d)
    if hole:
        d_snaps = [s for s in d_snaps if not (at("2026-10-19T13:00:00Z") < s.ts < at("2026-10-19T13:10:00Z"))]
    w.add_run("obrec-1019c", c_snaps, ["KXAAAGASDNY", "KXRT"])
    w.add_run("obrec-1019d", {GAS_A: d_snaps}, ["KXAAAGASDNY"])
    return w


def pick(w, **kw):
    srcs = [sim.BookSource(b) for b in w.books]
    cands = [(s, *s.window_snaps(GAS_A, at(S0), at(E_) + 4000 * US)) for s in srcs]
    return sim.choose_source(cands, at("2026-10-19T12:05:00Z"), at(E_), **kw)


def test_source_choice_smaller_median_gap_ties_to_1019c_and_holes_fall_back(tmp_path):
    src, snaps, info = pick(two_run_world(tmp_path / "a"))
    assert src.name == "obrec-1019d" and len(snaps) > 100 and info[1]["median_gap_s"] == 45.0
    src, _s, info = pick(two_run_world(tmp_path / "b", hole=True))
    assert src.name == "obrec-1019c" and info[1]["blind"] is True          # a 600 s hole in 1019d: fall back
    src, _s, _i = pick(two_run_world(tmp_path / "c", gap_d=60.0))
    assert src.name == "obrec-1019c"                                       # equal medians: ties go to obrec-1019c
    src, _s, _i = pick(two_run_world(tmp_path / "d"), force="obrec-1019c")
    assert src.name == "obrec-1019c"                                       # the 1019c-for-gas sensitivity


# ================================================================================ programs (conditions 9, 10)
def read_cls(w, **kw):
    cfg = w.cfg(**kw)
    inp = {"programs": sim.read_programs(w.lip), "series_tab": {"KXAAAGASDNY": {"frequency": "daily", "fee_type": "quadratic",
                                                                                 "fee_multiplier": 1.0}}}
    units, early = sim.classify_programs(inp, cfg)
    return inp, {u.unit_id: u for u in units}, {u.unit_id: u for u in early}


def test_pre_freeze_polls_are_read_for_program_ids_only(tmp_path):
    w = World(tmp_path)
    pa_, pb_ = prog("pA", GAS_A, S0, E_), prog("pB", GAS_B, S0, E_)
    w.add_polls([("2026-10-19T03:23:52Z", True, [pa_]),                 # the pre-freeze poll lists pA only
                 ("2026-10-19T12:05:00Z", True, [pa_, pb_]), ("2026-10-19T12:20:00Z", True, [pa_, pb_])])
    _inp, units, early = read_cls(w)
    assert "pA" in early and early["pA"].status == "excl_pre_freeze" and "pB" in units
    assert units["pB"].first_poll_us == at("2026-10-19T12:05:00Z")
    assert units["pB"].quote_start_us == at(S0) + 5 * 60 * US


def test_incomplete_polls_are_gaps_not_listings(tmp_path):
    w = World(tmp_path)
    p = prog("pC", GAS_A, S0, E_)
    w.add_polls([("2026-10-19T12:05:00Z", False, [p]), ("2026-10-19T12:20:00Z", True, [p])])
    ps = sim.read_programs(w.lip)
    assert ps.by_id["pC"]["first_poll"] == at("2026-10-19T12:20:00Z") and ps.polls == [at("2026-10-19T12:20:00Z")]


def test_program_parameter_change_takes_the_minimum_reward(tmp_path):
    w = World(tmp_path)
    p1, p2 = prog("pA", GAS_A, S0, E_), prog("pA", GAS_A, S0, E_, reward=500_000)
    w.add_polls([("2026-10-19T12:05:00Z", True, [p1]), ("2026-10-19T12:20:00Z", True, [p2])])
    _inp, units, _e = read_cls(w)
    u = units["pA"]
    assert u.drift is True and u.psets == [(100.0, 1000.0, 0.5), (50.0, 1000.0, 0.5)]
    u.quote_start_us, u.e_eff_us = at("2026-10-19T12:05:00Z"), at(E_)
    snaps = ticks("2026-10-19T12:05:30Z", "2026-10-19T13:55:00Z", 100)
    r = sim.run_unit(u, snaps, sim.TradeList(), "U0", 25, 50)
    both = sim.finalize_rewards(r, u)["c"]
    u.psets = [(100.0, 1000.0, 0.5)]
    one = sim.finalize_rewards(sim.run_unit(u, snaps, sim.TradeList(), "U0", 25, 50), u)["c"]
    assert both == pytest.approx(one / 2, abs=0.011) and both < one                 # the minimum over the sets seen


def test_program_that_drops_out_stops_rewarding_at_the_last_listing_plus_900s(tmp_path):
    w = World(tmp_path)
    p = prog("pA", GAS_A, S0, E_)
    w.add_polls([("2026-10-19T12:05:00Z", True, [p]), ("2026-10-19T12:20:00Z", True, [p]),
                 ("2026-10-19T12:35:00Z", True, [p]), ("2026-10-19T12:50:00Z", True, []), ("2026-10-19T13:05:00Z", True, [])])
    ps = sim.read_programs(w.lip)
    assert sim.program_reward_end(ps.by_id["pA"], ps.polls) == at("2026-10-19T12:35:00Z") + 900 * US
    w2 = World(tmp_path / "w2")
    w2.add_polls([("2026-10-19T12:05:00Z", True, [p]), ("2026-10-19T13:50:00Z", True, [p]), ("2026-10-19T14:05:00Z", True, [])])
    ps2 = sim.read_programs(w2.lip)
    assert sim.program_reward_end(ps2.by_id["pA"], ps2.polls) is None            # polls at/after E do not count
    # the pass: weight stops at rew_end, quoting (and fills) continue
    u = mk_unit(e=1000.0)
    s0 = bsnap(0, **GOOD)
    full = run(u, [s0]).acc[0]["c"]
    u.rew_end_us = u.quote_start_us + sec(102)
    cut = run(u, [s0]).acc[0]["c"]
    assert cut == pytest.approx(full * 100 / 450)                  # (2, 102] of the (2, 452] interval


# =================================================================================== metadata (condition 8)
def test_metadata_earliest_capture_before_quoting_never_substituted(tmp_path):
    d = tmp_path / "meta"
    d.mkdir()
    (d / "m-20261019T100000Z.json").write_text(json.dumps({"markets": [
        {"ticker": GAS_A, "event_ticker": "EV", "close_time": "2026-10-19T14:00:00Z", "expected_expiration_time": "2026-10-19T14:30:00Z"}]}))
    (d / "m-20261019T110000Z.json").write_text(json.dumps({"fetched_at": "2026-10-19T11:00:00Z", "response": {"markets": [
        {"ticker": GAS_A, "event_ticker": "EV", "close_time": "2026-10-19T13:00:00Z", "expected_expiration_time": "2026-10-19T13:30:00Z"},
        {"ticker": GAS_B, "event_ticker": "EV2", "close_time": "2026-10-19T13:00:00Z"}]}}))
    m = sim.MetaIndex(d)
    first_quote = at("2026-10-19T12:05:30Z")
    assert m.usable(GAS_A, first_quote)["close"] == at("2026-10-19T14:00:00Z")       # the earliest, not the later 13:00
    assert m.usable(GAS_A, at("2026-10-19T09:00:00Z")) is None                       # nothing before quoting: unknown
    assert m.usable(GAS_B, at("2026-10-19T10:30:00Z")) is None                       # first capture (11:00) is after
    assert m.usable(GAS_B, first_quote)["close"] == at("2026-10-19T13:00:00Z")
    assert len(m.files) == 2 and all(len(h) == 64 for _n, h in m.files)
    (d / "no-stamp.json").write_text(json.dumps({"markets": []}))
    with pytest.raises(sim.InputError, match="no fetched_at"):
        sim.MetaIndex(d)
    (d / "no-stamp.json").write_text(json.dumps({"fetched_at": "2026-10-19T09:00:00Z", "markets": [{"ticker": "KXMVE-1"}]}))
    assert sim.MetaIndex(d).mve == 1


def test_e_eff_comes_from_the_capture_and_never_from_the_settled_close_time(tmp_path, covered):
    w = basic_world(tmp_path)                                  # capture says GAS_A closes 14:00 == E ; GAS_B has no capture
    w.add_markets([(GAS_A, "KXAAAGASDNY-26OCT19", "yes", 1.0, "2026-10-19T15:00:00Z", "2026-10-20T04:41:00Z")])
    g = sim.run_sim(w.cfg(out=str(tmp_path / "o")))
    u = {r["unit_id"]: r for r in pq.read_table(tmp_path / "o" / "units.parquet").to_pylist() if r["cell"] == "U0"}
    assert u["pA"]["E_eff"] == "2026-10-19T14:00:00Z" and u["pB"]["E_eff"] == "2026-10-19T14:00:00Z"
    w2 = basic_world(tmp_path / "early", meta=False)
    w2.add_meta("20261019T100000Z", [{"ticker": GAS_A, "event_ticker": "X", "close_time": "2026-10-19T13:00:00Z"}])
    sim.run_sim(w2.cfg(out=str(tmp_path / "o2")))
    u2 = {r["unit_id"]: r for r in pq.read_table(tmp_path / "o2" / "units.parquet").to_pylist() if r["cell"] == "U0"}
    assert u2["pA"]["E_eff"] == "2026-10-19T13:00:00Z" and u2["pA"]["unit_day"] == "2026-10-19"
    late = World(tmp_path / "late")
    late.add_meta("20261019T123000Z", [{"ticker": "KXEOWEEK-X", "event_ticker": "X", "expected_expiration_time": "2026-10-19T14:30:00Z"}])
    assert sim.MetaIndex(late.meta).usable("KXEOWEEK-X", at("2026-10-19T12:05:30Z")) is None    # captured after the first quote


# ================================================================================= coverage and exclusions
def _stamp(us):
    return sim.us_to_iso(us)


def coverage_world(tmp, rounds_day2=1.0, bad_days=()):
    """Two full UTC days 2026-10-19 and -20 recorded every 222.7 s (1019c) and polled every 900 s."""
    w = World(tmp)
    t0, t1 = at("2026-10-19T00:00:00Z"), at("2026-10-21T00:00:00Z")
    snaps, t = [], t0
    while t < t1:
        day = _stamp(t)[:10]
        keep = 0.4 if day in bad_days else 1.0
        if (len(snaps) * 7919) % 100 < keep * 100:
            snaps.append(make_snap(t / US, **GOOD))
        t += int(222.7 * US)
    w.add_run("obrec-1019c", {GAS_A: snaps}, ["KXAAAGASDNY"])
    polls, t = [], t0
    while t < t1:
        polls.append((_stamp(t), True, []))
        t += 900 * US
    w.add_polls(polls)
    return w


def test_coverage_uses_the_fixed_baselines_and_two_bad_days_make_the_read_not_decidable(tmp_path):
    kw = dict(freeze_us=at("2026-10-19T00:00:00Z"), series=None)
    for name, bad, want in (("ok", (), False), ("one", ("2026-10-20",), False), ("two", ("2026-10-19", "2026-10-20"), True)):
        w = coverage_world(tmp_path / name, bad_days=bad)
        cfg = w.cfg(**kw)
        cov = sim.coverage(sim.load_inputs(cfg), cfg, at("2026-10-21T00:00:00Z"))
        assert cov["not_decidable"] is want, (name, cov)
        assert cov["baselines_s"] == {"obrec-1019c": 222.7, "lipsnap": 900.0}
        if name == "ok":
            assert all(not d["below_70pct"] for d in cov["days"].values()) and set(cov["days"]) == {"2026-10-19", "2026-10-20"}


def test_gaps_in_1019d_never_trigger_the_coverage_rule(tmp_path):
    w = coverage_world(tmp_path)
    sparse = [make_snap(at("2026-10-19T00:00:00Z") / US + 3600 * i, **GOOD) for i in range(40)]      # a very patchy 1019d
    w.add_run("obrec-1019d", {GAS_A: sparse}, ["KXAAAGASDNY"])
    cfg = w.cfg(freeze_us=at("2026-10-19T00:00:00Z"), series=None)
    assert sim.coverage(sim.load_inputs(cfg), cfg, at("2026-10-21T00:00:00Z"))["not_decidable"] is False


def test_excluded_share_over_ten_percent_is_not_decidable():
    def rec(status, quot=0):
        return {"status": status, "structural": {"quotable": quot}}
    ok = [rec("opened")] * 20 + [rec("excl_no_trades", 5)] * 2 + [rec("excl_no_meta", 0)] * 5
    assert sim.exclusion_share(ok, [])["not_decidable"] is False and sim.exclusion_share(ok, [])["excluded_would_open"] == 2
    bad = [rec("opened")] * 20 + [rec("excl_no_trades", 5)] * 2 + [rec("excl_no_meta", 1)]
    s = sim.exclusion_share(bad, [])
    assert s["not_decidable"] is True and s["share"] == pytest.approx(3 / 23)
    assert sim.exclusion_share([], [])["not_decidable"] is False


# ====================================================================================== markouts, CLI
def test_markouts_snapshot_then_trades_then_settlement_then_none():
    f = [("a", "yes", 4800, 25.0, T0 + sec(100)), ("b", "no", 5000, 10.0, T0 + sec(100))]
    one_sided = make_snap(T0 / US + 400, yes=[(0.50, 100)], no=[])
    two_sided = make_snap(T0 / US + 450, yes=[(0.60, 100)], no=[(0.30, 100)])               # mid = (0.60 + 1 - 0.30)/2 = 0.65
    snaps = [one_sided, two_sided]
    mk = sim.markouts_for(f, snaps, [s.ts for s in snaps], sim.TradeList(), None, None)
    assert mk[0][1] == "snap" and mk[0][0] == pytest.approx(0.65 - 0.48)
    assert mk[1][0] == pytest.approx((1 - 0.65) - 0.50)
    assert mk[0][3] == "none"                                                                # no 60 min info
    tl = sim.TradeList([make_trade(T0 / US + 100 + 3600 + 10, f"t{i}", p, 1, "yes") for i, p in enumerate((0.40, 0.50, 0.90))])
    mk = sim.markouts_for(f, [], [], tl, None, None)
    assert mk[0][2] == pytest.approx(0.50 - 0.48) and mk[0][3] == "trade" and mk[0][1] == "none"   # median print price
    mk = sim.markouts_for(f, [], [], sim.TradeList(), T0 + sec(200), 1.0)
    assert mk[0][1] == "settle" and mk[0][0] == pytest.approx(1.0 - 0.48)                    # closed first: settlement, flagged


def test_cli_refuses_wrong_freeze_missing_pins_and_runs_a_dry_run(tmp_path, capsys, covered):
    assert sim.main(["--freeze", "2026-10-19T00:00:00Z", "--dry-run", "--books", "x", "--programs", "y"]) == 2
    assert sim.main(["--freeze", sim.FREEZE_ISO, "--books", "x", "--programs", "y", "--out", str(tmp_path)]) == 2
    bogus = tmp_path / "bogus.md"
    bogus.write_text("not the declare")
    assert sim.main(["--freeze", sim.FREEZE_ISO, "--books", "x", "--programs", "y", "--declare", str(bogus),
                     "--conditions", str(bogus), "--out", str(tmp_path)]) == 2
    w = basic_world(tmp_path / "w")
    rc = sim.main(["--freeze", sim.FREEZE_ISO, "--dry-run", "--books", *w.books, "--programs", w.lip, "--meta", w.meta,
                   "--trades", str(w.kal), "--series", str(w.kal)])
    out = capsys.readouterr().out
    assert rc == 0 and json.loads(out)["mode"] == "dry_run"


# ============================================================================ trades / results stores
def test_trades_completeness_is_the_union_of_full_checkpoint_windows(tmp_path):
    w = World(tmp_path)
    t = "KXAAAGASDNY-26OCT19-T3.5"
    w.add_trades([], [t], since="2026-10-19T03:33:16Z", until="2026-10-19T13:00:00Z", name="a")
    w.add_trades([], [t], since="2026-10-19T13:00:00Z", until="2026-10-20T00:00:00Z", name="b")
    kp.Store(w.kal, "trades", {"series": "tail", "since": "2026-10-19T03:33:16Z", "until": "2026-10-20T00:00:00Z",
                               "tail_s": 600}).mark_done("TAILONLY-1")
    win = sim.trades_windows(w.kal, at("2026-10-24T00:00:00Z"))
    assert sim.covers(win[t], at("2026-10-19T12:05:00Z"), at("2026-10-19T14:00:00Z"))          # a + b
    assert not sim.covers(win[t], at("2026-10-19T12:05:00Z"), at("2026-10-20T01:00:00Z"))      # b ends first
    assert "TAILONLY-1" not in win                                                              # tail runs never prove completeness
    assert not sim.covers(win.get("MISSING"), 0, 1)
    assert not sim.covers([(at("2026-10-19T13:00:00Z"), None)], at("2026-10-19T12:05:00Z"), at("2026-10-19T14:00:00Z"))


def test_trades_and_results_are_manifest_checked_and_cut_at_the_read_instant(tmp_path):
    w = basic_world(tmp_path)
    r, _l, skipped = sim.read_results(w.kal, [GAS_A, GAS_B], at("2026-10-24T00:00:00Z"))
    assert r[GAS_A]["value"] == 1.0 and r[GAS_B]["value"] == 0.0 and r[GAS_A]["event"] == "KXAAAGASDNY-26OCT19"
    r, _l, _s = sim.read_results(w.kal, [GAS_A], at("2026-10-19T20:00:00Z"))
    assert r == {}                                                    # fetched 10-20T04:41Z, after this read instant
    m, _s, _l, tainted = sim.load_trades(w.kal, [GAS_A], FRZ, at("2026-10-20T00:00:00Z"), at("2026-10-24T00:00:00Z"))
    assert tainted == set()
    assert [x for x in m[GAS_A].tid] == ["tA1", "tA2"]
    part = sorted((w.kal / "trades" / "series=KXAAAGASDNY").rglob("*.parquet"))[0]
    part.write_bytes(part.read_bytes() + b"x")
    with pytest.raises(sim.InputError, match="altered"):
        sim.load_trades(w.kal, [GAS_A], FRZ, at("2026-10-20T00:00:00Z"), None)


def test_unresolved_units_enter_at_the_worst_case_after_48h_and_young_ones_stay_out():
    u = mk_unit(e=1000.0)
    u.ticker = "T-1"
    assert sim.resolution_at(u, {}, u.e_us + 47 * 3600 * US)[0] == "young"
    assert sim.resolution_at(u, {}, u.e_us + sec(86_400) + 48 * 3600 * US)[0] == "worst_case"     # X = E + 24 h
    u.x_us = u.e_us + sec(3600)
    assert sim.resolution_at(u, {}, u.e_us + 3600 * US + 48 * 3600 * US)[0] == "worst_case"
    res = {"T-1": {"value": 1.0, "settled_us": u.e_us + sec(100), "result": "yes", "event": "E"}}
    assert sim.resolution_at(u, res, u.e_us + sec(50))[0] == "young"                              # not yet settled at R
    assert sim.resolution_at(u, res, u.e_us + sec(100))[0] == "settled"
    core = {"fills": [("a", "yes", 4800, 25.0, 0)], "rew": {"c": 3.0, "lower": 2.0, "upper": 4.0, "r2": 3.0}, "fees_cent": 0.1}
    sc = sim.score_unit_cell(u, core, "worst_case", None)
    assert sc["net"] == pytest.approx(3.0 - 0.1 - 12.0) and sc["worst_u"] == pytest.approx(3.0 - 0.1 - 12.0)
    sc = sim.score_unit_cell(u, core, "settled", 1.0)
    assert sc["net"] == pytest.approx(3.0 - 0.1 + 13.0) and sc["worst_u"] == pytest.approx(3.0 - 0.1 - 12.0)   # structural worst
    core["rew"]["c"] = 0.99                                                                       # P1: under $1.00 pays nothing
    assert sim.score_unit_cell(u, core, "settled", 1.0)["net"] == pytest.approx(-0.1 + 13.0)
    assert sim.score_unit_cell(u, core, "settled", 1.0)["net_nofloor"] == pytest.approx(0.99 - 0.1 + 13.0)


# ==================================================================================== scale / memory
def _scale_store(tmp, n_markets, n_days, step=222.7, levels=24):
    """Numpy-built sealed store at obrec-1019c scale (about 23.6 levels per snapshot): no python row dicts."""
    import pyarrow as pa
    d = Path(tmp) / "obrec-1019c"
    paths = []
    t0 = at("2026-10-19T04:00:00Z")
    n_snaps = int(n_days * 86_400 / step)
    ts = (t0 + (np.arange(n_snaps) * step * US).astype(np.int64))
    tickers = [f"KXRT-26OCT19-M{i:03d}" for i in range(n_markets)]
    for day in sorted({sim.us_to_date(int(x)) for x in ts}):
        sel = np.array([sim.us_to_date(int(x)) == day for x in ts])
        tday = ts[sel]
        nT, nS = n_markets, len(tday)
        ob_ts = np.repeat(tday, 2 * levels)
        for tk in tickers:
            pass
        cols = {}
        rep = nS * 2 * levels
        tick = np.repeat(np.array(tickers, dtype=object), rep)
        ts_all = np.tile(ob_ts, nT)
        side = np.tile(np.repeat(np.array(["yes", "no"], dtype=object), levels), nT * nS)
        lvl = np.tile(np.arange(levels), nT * nS * 2)
        price = np.where(side == "yes", 0.48 - 0.01 * (lvl % 12), 0.50 - 0.01 * (lvl % 12)).round(2)
        ob = pa.table({"ts": pa.array(ts_all, pa.timestamp("us", tz="UTC")), "ticker": pa.array(tick),
                       "series_ticker": pa.array(["KXRT"] * len(ts_all)), "side": pa.array(side),
                       "price": pa.array(price), "size": pa.array(np.full(len(ts_all), 120.0))})
        # 2 x 12 distinct price levels per side would repeat prices: keep `levels` <= 12 per side instead
        sn_ts = np.tile(tday, nT)
        sn = pa.table({"ts": pa.array(sn_ts, pa.timestamp("us", tz="UTC")),
                       "ticker": pa.array(np.repeat(np.array(tickers, dtype=object), nS)),
                       "series_ticker": pa.array(["KXRT"] * (nT * nS)),
                       "n_yes_levels": pa.array(np.full(nT * nS, levels, np.int32)),
                       "n_no_levels": pa.array(np.full(nT * nS, levels, np.int32))})
        for kind, tab in (("orderbook", ob), ("orderbook_snaps", sn)):
            p = d / kind / "series=KXRT" / f"date={day}" / "part-scale.parquet"
            p.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(tab, p)
            paths.append(p)
    runs = kp.table_of("orderbook_runs", [
        {"run_id": "r1", "event": "run_start", "ts": dt(int(ts[0])), "series_json": json.dumps(["KXRT"])},
        {"run_id": "r1", "event": "run_end", "ts": dt(int(ts[-1])), "complete": True, "stop_reason": "duration"}])
    pr = d / "orderbook_runs" / "part-r.parquet"
    pr.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(runs, pr)
    kp.seal_parts(d, "2026-10-19T04", paths + [pr])
    reg = Path(tmp) / "obrec-REGISTRY-1019c.md"
    reg.write_text("KXRT\n")
    os.utime(reg, (1_600_000_000, 1_600_000_000))
    return d, tickers


def test_memory_stays_under_the_cap_at_recorder_scale(tmp_path):
    """Default: 30 markets x 1 day x 24 levels.  LIP_SIM_SCALE_MARKETS=521 LIP_SIM_SCALE_DAYS=7 runs the
    obrec-1019c scale (about 203k snapshot rows and 4.8M level rows per day) with the same cap."""
    n_m = int(os.environ.get("LIP_SIM_SCALE_MARKETS", "30"))
    n_d = float(os.environ.get("LIP_SIM_SCALE_DAYS", "1"))
    d, tickers = _scale_store(tmp_path, n_m, n_d, levels=12)
    w = World(tmp_path / "w")
    w.books.append(str(d))
    s0, e = "2026-10-19T04:00:00Z", sim.us_to_iso(at("2026-10-19T04:00:00Z") + int(n_d * 86_400 * US) - 3600 * US)
    w.add_polls([("2026-10-19T04:00:00Z", True, [prog(f"p{i}", t, s0, e) for i, t in enumerate(tickers)])])
    w.add_series([("KXRT", "custom", "quadratic")])
    cap = 1500.0
    rep = sim.run_sim(w.cfg(dry_run=True, read_us=None, mem_cap_mb=cap, meta=None, trades=None))
    assert rep["units_by_status"].get("excl_no_meta", 0) == len(tickers)       # KXRT is not daily: needs a capture
    w.add_meta("20261019T030000Z", [{"ticker": t, "event_ticker": t.rsplit("-", 1)[0], "close_time": e,
                                     "expected_expiration_time": e} for t in tickers])
    rep = sim.run_sim(w.cfg(dry_run=True, read_us=None, mem_cap_mb=cap, trades=None))
    assert rep["structurally_opened_units"] == len(tickers) and rep["snapshots_in_window"] > 0
    assert rss_peak_ok(cap)


def rss_peak_ok(cap_mb):
    return sim.rss_mb() < cap_mb


def test_memory_cap_is_enforced():
    with pytest.raises(MemoryError):
        sim.MemCap(1.0).check("unit")


# ======================================================================================== mutation checks
def _mut_flip_fill_inequality(mp):
    mp.setattr(sim, "yes_bid_fills", lambda tk, y, b: tk == "no" and y is not None and y >= b)
    mp.setattr(sim, "no_bid_fills", lambda tk, n, b: tk == "yes" and n is not None and n >= b)


def _mut_drop_delay(mp):
    mp.setattr(sim, "L_US", 0)


def _mut_drop_cap(mp):
    mp.setattr(sim, "CAP_US", 10 ** 15)


def _mut_count_more_than_t(mp):
    orig = sim.side_phi
    mp.setattr(sim, "side_phi", lambda *a, **k: orig(*a, **dict(k, capped=False)))


def _mut_skip_floor(mp):
    mp.setattr(sim, "floor_cent", lambda x: x)


def _mut_print_price(mp):
    mp.setattr(sim, "fill_price", lambda b, p: p)


def _mut_read_result(mp):
    orig = sim.decide_quote

    def peek(unit, snap, Y, N, S, imax):
        q, why = orig(unit, snap, Y, N, S, imax)
        return (None, "Q4") if getattr(unit, "result", None) == "no" else (q, why)
    mp.setattr(sim, "decide_quote", peek)


def _mut_block_trades_count(mp):
    orig = sim.TradeList.__init__

    def init(self, rows=()):
        orig(self, [r[:6] + (False,) for r in rows])
    mp.setattr(sim.TradeList, "__init__", init)


def _mut_no_min_payout(mp):
    mp.setattr(sim, "apply_min_payout", lambda r: r)


MUTANTS = {"flip fill inequality": _mut_flip_fill_inequality, "drop L = 2 s delay": _mut_drop_delay,
           "drop CAP": _mut_drop_cap, "count more than T": _mut_count_more_than_t, "skip the floor": _mut_skip_floor,
           "print price instead of ours": _mut_print_price, "read result in Q decisions": _mut_read_result,
           "count block trades": _mut_block_trades_count, "skip the $1.00 minimum": _mut_no_min_payout}


def _killers():
    return [test_reward_arithmetic_hand_computed, test_help_article_example_16_dollars, test_validity_and_q4_boundary,
            test_prorata_at_crossing_level_reference_boundary_and_df_ticks, test_gap_cut_at_cap_last_snapshot_at_e_eff_and_floor,
            test_no_weight_before_quote_start_and_floor_rules, test_fill_direction_price_block_edges_and_order,
            test_fill_interval_is_open_closed_and_has_the_two_second_delay,
            test_fill_quantity_is_min_remaining_and_count_consumed_in_time_then_trade_id_order,
            test_reward_uses_remaining_size_after_a_fill_and_starts_at_ts_plus_l,
            lambda: test_no_look_ahead_result_and_later_trades_do_not_move_decisions(0),
            test_unresolved_units_enter_at_the_worst_case_after_48h_and_young_ones_stay_out]


def _survivors(patch):
    with pytest.MonkeyPatch.context() as mp:
        patch(mp)
        failed = []
        for t in _killers():
            try:
                t()
            except AssertionError:
                failed.append(getattr(t, "__name__", "lambda"))
        return failed


def test_the_unmutated_module_passes_every_killer():
    assert _survivors(lambda mp: None) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_each_mutation_is_caught_by_a_test(name):
    assert _survivors(MUTANTS[name]), f"mutant survived: {name}"


# ===================================================== review round 1 (PR #730): CONDITIONS-2 items, one block each
def _opened_rec(i, day, fills, x_us=None, e_off=0.0):
    u = mk_unit(e=1000.0 + e_off, uid=f"w{i}")
    u.ticker, u.event, u.day, u.day_e, u.gas, u.x_us = f"T-{i}", f"E{i}", day, day, False, x_us
    core = {"fills": fills, "rew": {"c": 0.0, "lower": 0.0, "upper": 0.0}, "fees_cent": 0.0, "quoted_s": 4000.0,
            "collateral": 0.0}
    return {"u": u, "status": "opened", "cores": {c: core for c in sim.CELLS}}


def test_b1_a_kill_at_an_earlier_midnight_makes_the_verdict_dead_as_frozen():
    kw = dict(scan=False, chosen=None, asof_us=None, nd=[], counts_met=True, all_pass=True)
    assert sim.decide_verdict(dead=False, **kw) == "FORMULA_SCREEN_PASSED"
    assert sim.decide_verdict(dead=True, **kw) == "DEAD_AS_FROZEN"           # even though every gate later passes
    assert sim.decide_verdict(dead=True, **dict(kw, nd=["coverage"])) == "DEAD_AS_FROZEN"


def test_b1_b_kill_sums_include_worst_case_units_once_48h_past_max_e_x():
    recs = [_opened_rec(i, f"2026-10-{20 + i % 3}", [("a", "yes", 9999, 25.0, 0)]) for i in range(10)]
    u0 = recs[0]["u"]
    worst_at = max(u0.e_us, u0.e_us + sec(86_400)) + 48 * 3600 * US           # X defaults to E + 24 h
    R = u0.e_us + 30 * 86_400 * US
    rows, cnt = sim.population_rows(recs, {}, "U0", R)
    assert cnt == {"worst_case": 10} and all(r["resolved_us"] == worst_at for r in rows)
    early = sim.kill_checks(rows, 25, [worst_at - 3600 * US])
    late = sim.kill_checks(rows, 25, [worst_at + 3600 * US])
    assert early["checks"] == [] and not early["dead_as_frozen"]
    assert late["checks"][0]["K1"] and late["dead_as_frozen"]                   # -249.97 <= -200


def _edit_manifest(root, fn):
    p = Path(root) / "manifest.jsonl"
    p.write_text("".join(fn(json.loads(ln)) for ln in p.read_text().splitlines()))


def test_b2_a_open_ended_or_unrecorded_or_late_checkpoints_never_count_as_coverage(tmp_path):
    t = "KXAAAGASDNY-26OCT19-T3.5"
    R = at("2026-10-24T00:00:00Z")
    lo, hi = at("2026-10-19T12:05:00Z"), at("2026-10-19T14:00:00Z")
    cases = {"open_ended": dict(until=None), "no_run_record": dict(run_record=False),
             "run_after_R": dict(run_at="2026-10-25T04:41:00Z"), "ok": {}}
    got = {}
    for name, kw in cases.items():
        w = World(tmp_path / name)
        w.add_trades([], [t], **kw)
        got[name] = sim.covers(sim.trades_windows(w.kal, R).get(t), lo, hi)
    assert got == {"open_ended": False, "no_run_record": False, "run_after_R": False, "ok": True}
    # a window cannot reach past the run that downloaded it: until in the future is capped at the run's time
    w = World(tmp_path / "future")
    w.add_trades([], [t], until="2026-11-30T00:00:00Z", run_at="2026-10-19T13:00:00Z")
    assert not sim.covers(sim.trades_windows(w.kal, R).get(t), lo, hi)
    # a second run of the same query after R (more tickers done after R) makes the checkpoint unprovable at R
    w = World(tmp_path / "rerun")
    w.add_trades([], [t])
    with fixed_now("2026-10-25T04:41:00Z"):
        kp.Store(w.kal, "trades", {"series": "trades", "since": "2026-10-19T03:33:16Z", "until": "2026-10-20T00:00:00Z",
                                   "market": None}).run_record(_Client(), complete=True)
    assert not sim.covers(sim.trades_windows(w.kal, R).get(t), lo, hi)


def test_b2_b_trades_files_written_after_R_or_overwritten_make_the_unit_excl_no_trades(tmp_path, covered):
    w = basic_world(tmp_path)
    g0 = sim.run_sim(w.cfg(out=str(tmp_path / "o0")))
    assert g0["exclusions"] == {} and g0["population"]["U0"] == {"settled": 2}
    ga = [p for p in (w.kal / "trades").rglob("*.parquet")]
    assert len(ga) == 2                                                  # one file per series partition
    after = [p for p in ga if "KXAAAGASDNY" in str(p)][0]
    over = [p for p in ga if "KXAAAGASDFL" in str(p)][0]

    def edit(rec):
        if rec.get("type") == "file" and rec["path"].endswith(after.name) and "KXAAAGASDNY" in rec["path"]:
            rec["written_at"] = "2026-10-30T00:00:00+00:00"                # downloaded after R
        line = json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n"
        if rec.get("type") == "file" and "KXAAAGASDFL" in rec["path"] and rec.get("kind") == "trades":
            earlier = dict(rec, sha256="0" * 64)                          # an earlier, different file at the same path
            return json.dumps(earlier, sort_keys=True, separators=(",", ":")) + "\n" + line
        return line
    _edit_manifest(w.kal, edit)
    g = sim.run_sim(w.cfg(out=str(tmp_path / "o1")))
    ex = pq.read_table(tmp_path / "o1" / "units.parquet").to_pylist()
    status = {r["unit_id"]: r["status"] for r in ex}
    assert status == {"pA": "excl_no_trades", "pB": "excl_no_trades"}, status
    assert g["exclusions"] == {"excl_no_trades": 2}
