import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import p40_curve_floor as P  # noqa: E402

MIN = P.MIN
T0 = P.ms("2026-08-20T00")
SEG_END = P.SEGMENTS["S1"][1]


def price(q_sol):
    q = q_sol * 1e9
    return q * q / (1000.0 * P.K0)


def bar(minute, q_sol, hi_sol=None, lo_sol=None):
    q = q_sol * 1e9
    hi = price(hi_sol if hi_sol is not None else q_sol)
    lo = price(lo_sol if lo_sol is not None else q_sol)
    return (T0 + minute * MIN, hi, lo, q, P.K0 / q, price(q_sol))


def arrs(rows):
    a = list(zip(*rows))
    return [np.array(x) for x in a]


def path_basic():
    # create at 30, peak 60 SOL at minute 5, dump to 30.2 at minute 6, quiet after
    return [bar(0, 30.0), bar(2, 45.0), bar(5, 60.0), bar(6, 30.2, hi_sol=59.0, lo_sol=30.2), bar(20, 30.3)]


def test_q_eq_roundtrip():
    assert abs(P.q_eq(price(55.5)) - 55.5e9) < 1e3


def test_trigger_basic():
    m, hi, lo, q, b, cl = arrs(path_basic())
    T, why = P.find_trigger(m, hi, lo, q, b, SEG_END, None)
    # peak minute 5 ends at 6 -> +30 = minute 36; band since end of minute 6 -> +10 = 17; max = 36
    assert why == "trigger" and T == T0 + 36 * MIN


def test_quiet_broken_moves_trigger():
    rows = path_basic()[:-1] + [bar(30, 30.3, hi_sol=31.5)]
    m, hi, lo, q, b, cl = arrs(rows)
    T, why = P.find_trigger(m, hi, lo, q, b, SEG_END, None)
    # back in band at end of minute 30 -> +10 = 41 > 36
    assert why == "trigger" and T == T0 + 41 * MIN


def test_no_peak_no_trigger():
    rows = [bar(0, 30.0), bar(5, 50.0), bar(6, 30.2, hi_sol=49.0, lo_sol=30.2)]
    m, hi, lo, q, b, cl = arrs(rows)
    assert P.find_trigger(m, hi, lo, q, b, SEG_END, None) == (None, "no_trigger")


def test_dirty_bar_blocks():
    rows = path_basic()
    t, hi, lo, q, b, c = rows[3]
    rows[3] = (t, hi, lo, q, b * 1.05, c)  # k off by 5%
    m, hi, lo, q, b, cl = arrs(rows)
    assert P.find_trigger(m, hi, lo, q, b, SEG_END, None) == (None, "dirty_pre")


def test_unclean_start():
    rows = [bar(0, 33.0)] + path_basic()[1:]
    m, hi, lo, q, b, cl = arrs(rows)
    assert P.find_trigger(m, hi, lo, q, b, SEG_END, None)[1] == "unclean_start"


def test_close_above_band_then_drop():
    # minute 6 closes above band (35), minute 8 drops to 30.3: minute 8 not on floor (prev close above)
    rows = [bar(0, 30.0), bar(5, 60.0), bar(6, 35.0, hi_sol=59.0, lo_sol=35.0), bar(60, 30.3, hi_sol=34.0, lo_sol=30.3)]
    m, hi, lo, q, b, cl = arrs(rows)
    T, why = P.find_trigger(m, hi, lo, q, b, SEG_END, None)
    assert T == T0 + 71 * MIN


def test_labels_tp_and_cap_and_censor():
    rows = path_basic() + [bar(100, 45.0), bar(200, 60.0)]
    m, hi, lo, q, b, cl = arrs(rows)
    T, _ = P.find_trigger(m, hi, lo, q, b, SEG_END, None)
    L = P.labels(T, m, hi, lo, q, b, cl, SEG_END, None)
    assert L["tp"] == 100 - 36 and L["p30"] == 200 - 36 and L["rebound"] == 100 - 36
    assert not L["censored"] and not L["path_invalid"]
    # no revival -> time cap
    m, hi, lo, q, b, cl = arrs(path_basic())
    L = P.labels(T, m, hi, lo, q, b, cl, SEG_END, None)
    assert L["tp"] is None and L["rebound"] is None
    # censored near segment end
    L = P.labels(SEG_END - 60 * MIN, m, hi, lo, q, b, cl, SEG_END, None)
    assert L["censored"]


def test_tp_level_is_mark_doubling():
    rows = path_basic()
    m, hi, lo, q, b, cl = arrs(rows)
    T, _ = P.find_trigger(m, hi, lo, q, b, SEG_END, None)
    qT = 30.3e9
    t = (P.K0 / qT) - qT * (P.K0 / qT) / (qT + 0.9875 * P.X)
    q_tp = math.sqrt(0.5 / (t / 1e6) * 1000 * P.K0)
    below = rows + [bar(300, q_tp / 1e9 * 0.999)]
    m, hi, lo, q, b, cl = arrs(below)
    assert P.labels(T, m, hi, lo, q, b, cl, SEG_END, None)["tp"] is None
    above = rows + [bar(300, q_tp / 1e9 * 1.001)]
    m, hi, lo, q, b, cl = arrs(above)
    assert P.labels(T, m, hi, lo, q, b, cl, SEG_END, None)["tp"] == 300 - 36


def test_path_invalid():
    rows = path_basic() + [bar(100, 0.5e-9)]
    m, hi, lo, q, b, cl = arrs(rows)
    T, _ = P.find_trigger(m, hi, lo, q, b, SEG_END, None)
    assert P.labels(T, m, hi, lo, q, b, cl, SEG_END, None)["path_invalid"]


def test_guards():
    with pytest.raises(ValueError):
        P.guard_path("/data/mal/hunt-shared/bars_1m/fast-pool-0918/*.parquet")
    with pytest.raises(ValueError):
        P.guard_path("/data/mal/meta-capture/x")
    P.guard_path("/data/mal/hunt-shared/bars_1m/fresh-0903/*.parquet")
    with pytest.raises(ValueError):
        P.guard_ms(P.ms("2026-10-25T00"))
    with pytest.raises(ValueError):
        P.guard_ms(P.ms("2026-09-16T00"))
