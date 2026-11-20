"""Tests for scripts/research/floor_rotate_step1.py pure helpers (no tape, no kit run)."""
import importlib.util
import pathlib

import numpy as np

_p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "research" / "floor_rotate_step1.py"
_s = importlib.util.spec_from_file_location("floor_rotate_step1", _p)
R = importlib.util.module_from_spec(_s)
_s.loader.exec_module(R)


def _rows(sl, q, ib, so, v=17.6e9):
    n = len(sl)
    return {"sl": np.array(sl, np.int64), "q": np.array(q, float), "ib": np.array(ib, bool), "so": np.array(so, float), "v": np.full(n, v)}


def test_first_come_matches_cap_sim_semantics():
    td = np.array([0.0, 1.0, 2.0, 3.0, 100.0])
    dur = np.array([50.0, 50.0, 50.0, 50.0, 10.0])
    take, rot, sk = R.simulate(td, dur, 2)
    assert take.tolist() == [True, True, False, False, True] and rot == {} and sk == 0


def test_rotation_oldest_eligible_and_slot_reuse():
    td = np.array([0.0, 10.0, 20.0, 30.0])
    dur = np.array([1000.0] * 4)
    seen = []

    def cb(i, t):
        seen.append((i, t))
        return ("tok", i)

    take, rot, _ = R.simulate(td, dur, 2, h_min_s=15.0, rot_cb=cb)
    # at t=20 position 0 (age 20) is eligible and oldest; position 1 is age 10 (too young)
    assert rot[0] == (20.0, ("tok", 0)) and take[2]
    assert seen[0] == (0, 20.0)  # only the old one was offered at t=20
    # at t=30 the cap is full again (1 and 2 open): 1 has age 20 >= 15 -> rotated
    assert 1 in rot and take[3]


def test_skip_when_cb_refuses_and_count():
    td = np.array([0.0, 20.0])
    dur = np.array([1000.0, 1000.0])
    take, rot, sk = R.simulate(td, dur, 1, h_min_s=5.0, rot_cb=lambda i, t: None)
    assert take.tolist() == [True, False] and rot == {} and sk == 1


def test_no_rotation_of_young_positions():
    td = np.array([0.0, 5.0])
    take, rot, sk = R.simulate(td, np.array([1000.0, 1000.0]), 1, h_min_s=50.0, rot_cb=lambda i, t: "x")
    assert take.tolist() == [True, False] and rot == {} and sk == 0


def test_closed_position_is_not_a_candidate():
    td = np.array([0.0, 100.0])
    take, rot, _ = R.simulate(td, np.array([50.0, 50.0]), 1, h_min_s=10.0, rot_cb=lambda i, t: "x")
    assert take.tolist() == [True, True] and rot == {}


def test_at_floor_and_flow_qualifiers():
    # Q incl. V = 17.6e9 + 0.05e9 -> (Q/V)^2 = 1.0057 <= 1.03
    a = _rows([100, 200, 300], [0.05e9] * 3, [False, False, True], [1e9, 1e9, 1e9])
    # flows in the last hour window (rate 2.5 slots/s -> 9000 slots): -1e9 -1e9 +1e9 = -1e9 <= 0
    assert R.at_floor_no_net_buy(a, 300, 2.5)
    # net buy: +
    b = _rows([100, 200, 300], [0.05e9] * 3, [True, True, False], [1e9, 1e9, 1e9])
    assert not R.at_floor_no_net_buy(b, 300, 2.5)
    # above the 3% band: real quote 0.3e9 -> (17.9/17.6)^2 = 1.0343
    c = _rows([100], [0.3e9], [False], [1e9])
    assert not R.at_floor_no_net_buy(c, 100, 2.5)


def test_qualifier_never_reads_rows_after_the_instant():
    a = _rows([100, 400], [0.05e9, 5e9], [False, True], [1e9, 50e9])
    assert R.at_floor_no_net_buy(a, 300, 2.5)  # the later buy and the big quote are invisible at slot 300


def test_old_flow_outside_window_ignored():
    # buy at slot 10, instant at slot 20000 with 2.5 slots/s: window starts at 20000-9000=11000 -> buy ignored
    a = _rows([10, 15000], [0.05e9, 0.05e9], [True, False], [9e9, 1e9])
    assert R.at_floor_no_net_buy(a, 20000, 2.5)


def test_book_stats_ex3_and_days():
    lam = np.array([5e9, 4e9, 3e9, -1e9, 2e9])
    day = np.array([1, 1, 2, 2, 3])
    s = R.book_stats(lam, day)
    assert abs(s["total"] - 13.0) < 1e-9 and abs(s["ex3"] - 1.0) < 1e-9 and s["pos_days"] == 3 and s["days"] == 3


def test_judge_and_worse_variant():
    fc = {"sd": 0.1, "ex3": 0.5, "pos_days": 3}
    ok = R.judge({"sd": 0.2, "ex3": 0.6, "pos_days": 3}, fc, [1.0] * 16 + [-1.0] * 8)
    assert all(ok.values())
    bad = R.judge({"sd": 0.2, "ex3": 0.4, "pos_days": 2}, fc, [1.0] * 15 + [-1.0] * 9)
    assert bad == {"i1": True, "i2": False, "i3": False, "i4": False}
    assert R.worse_variant_pass({6: ok, 12: bad}) == {"i1": True, "i2": False, "i3": False, "i4": False}


def test_windows_24h_hold():
    w = R.windows(24.0)
    assert w["D"] == 7.0 and abs(w["C1"] - 6.0) < 1e-12


def test_paired_boot_seed_deterministic():
    r1 = R.paired_boot({1: 1.0, 2: 2.0, 3: -0.5}, {1: 0.5, 2: 1.0, 3: 0.0})
    r2 = R.paired_boot({1: 1.0, 2: 2.0, 3: -0.5}, {1: 0.5, 2: 1.0, 3: 0.0})
    assert r1 == r2 and r1["p2.5"] <= r1["p5"]


def test_capture_find_two_segments_same_pool():
    """Regression for 'mint without prints': a pool flushed in two segments of a block shares (block, crc); the later segment must not overwrite the first."""
    cap = R.Capture()
    cap.add((0, 7), {"a": {"sl": np.array([1, 2])}, "sds": {10, 20}, "seg_end": 5, "clk": {}})
    cap.add((0, 7), {"a": {"sl": np.array([900, 901])}, "sds": {1000}, "seg_end": 2000, "clk": {}})
    assert cap.find(0, 7, 20)["a"]["sl"].tolist() == [1, 2]
    assert cap.find(0, 7, 1000)["a"]["sl"].tolist() == [900, 901]
    try:
        cap.find(0, 7, 5)
        assert False
    except KeyError:
        pass
