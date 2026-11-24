"""Tests for floor_holdcap_read on synthetic ledgers only (no real data is opened)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import floor_holdcap_read as H  # noqa: E402

LIM = {"R": 3_860_000, "max_open": 2, "budget": 5_000_000_000}
HR = H.HOUR_MS
T0 = H.COUNT_FROM


def tr(entry_h, hold_h, flat=0, press=None, day=None):
    e = T0 + int(entry_h * HR)
    x = e + int(hold_h * HR)
    return {"entry_ms": e, "exit_ms": x, "flat": flat, "press": flat if press is None else press, "close_day": day or "2026-11-05"}


def test_counted_window_boundaries():
    ts = [tr(-1, 1), tr(0, 1), tr(1, 1)]
    ts.append({**tr(0, 1), "entry_ms": H.COUNT_TO - 1})
    ts.append({**tr(0, 1), "entry_ms": H.COUNT_TO})
    got = H.counted(ts)
    assert [t["entry_ms"] for t in got] == [T0, T0 + HR, H.COUNT_TO - 1]


def test_sol_days_and_mean_hold():
    ts = [tr(0, 24, 1_000_000_000), tr(1, 12, -500_000_000)]
    assert abs(H.sol_days(ts, 0.25) - 0.25 * 1.5) < 1e-12
    r = H.stats(ts, 0.25, "flat")
    assert r["n"] == 2 and abs(r["total"] - 0.5) < 1e-12 and abs(r["mean_hold_h"] - 18) < 1e-9
    assert abs(r["net_per_sol_day"] - 0.5 / 0.375) < 1e-12
    assert r["ex_top3"] is None


def test_ex_top3():
    ts = [tr(i, 1, v) for i, v in enumerate([10, 9, 8, 1, -2])]
    assert abs(H.stats(ts, 0.1, "flat")["ex_top3"] - (-1e-9)) < 1e-15


def test_wallet_sim_skips_on_max_open_and_returns_capital_at_exit():
    ts = [tr(0, 10, 1_000_000), tr(1, 10, 1_000_000), tr(2, 10, 1_000_000), tr(11, 1, 1_000_000)]
    w = H.wallet_sim(ts, "flat", 0.25, 0.25, LIM, 2_000_000_000, 750_000_000, 1.0)
    assert w["taken"] == 3 and w["skipped"] == 1 and w["skip_why"]["max_open"] == 1  # third entry blocked; entry 4 at h11 sees exits at h10, h11: first freed
    assert abs(w["realized_sol"] - 3e-3) < 1e-12


def test_wallet_sim_capital_binds_and_scales_linearly():
    ts = [tr(0, 10, 100_000_000), tr(1, 10, 100_000_000)]
    lim = dict(LIM, max_open=8)
    # free 1.0, floor 0.75: first entry 0.40 + R leaves 0.596 < 0.75 -> skipped for wallet
    w = H.wallet_sim(ts, "flat", 0.25, 0.40, lim, 1_000_000_000, 750_000_000, 1.0)
    assert w["taken"] == 0 and w["skipped"] == 2 and w["skip_why"]["wallet"] == 2
    w = H.wallet_sim(ts, "flat", 0.25, 0.40, lim, 1_500_000_000, 750_000_000, 1.0)  # one fits, the second does not while the first is open
    assert w["taken"] == 1 and w["skipped"] == 1
    assert abs(w["realized_sol"] - 0.1 * 0.40 / 0.25) < 1e-12
    ts2 = [tr(0, 10, 100_000_000), tr(10, 10, 100_000_000)]  # capital back at exit (equal instant): both taken
    w = H.wallet_sim(ts2, "flat", 0.25, 0.40, lim, 1_500_000_000, 750_000_000, 1.0)
    assert w["taken"] == 2


def test_wallet_realized_raises_free_capital_after_exit():
    ts = [tr(0, 1, 500_000_000), tr(2, 1, 0)]
    lim = dict(LIM, max_open=8)
    # free 1.2: 1.2 - 0.40 - R = 0.796 ok (>= 0.75). After the win +0.8 SOL (scaled), the second also fits.
    w = H.wallet_sim(ts, "flat", 0.25, 0.25 * 1.6, lim, 1_200_000_000, 750_000_000, 1.0)
    assert w["taken"] == 2


def test_budget_full_wipe_formula():
    lim = dict(LIM, max_open=8, budget=1_000_000_000)
    cost = 400_000_000 + lim["R"]
    # open: 2 x cost = 0.80772; a third needs realized - 3*cost > -budget -> -1.21158 > -1.0 false -> skipped by budget
    ts = [tr(0, 10, 0), tr(1, 10, 0), tr(2, 10, 0)]
    w = H.wallet_sim(ts, "flat", 0.25, 0.40, lim, 100_000_000_000, 0, 1.0)
    assert w["taken"] == 2 and w["skip_why"]["budget"] == 1
    assert not (0 - 3 * cost > -lim["budget"]) and (0 - 2 * cost > -lim["budget"])
    # strict inequality: exactly -budget is refused
    lim2 = dict(lim, budget=cost)
    w = H.wallet_sim([tr(0, 1, 0)], "flat", 0.25, 0.40, lim2, 100_000_000_000, 0, 1.0)
    assert w["taken"] == 0 and w["skip_why"]["budget"] == 1


def test_limits_read_from_executor():
    lim = H.limits()
    assert lim["R"] == 3_860_000 and lim["max_open"] >= 1 and lim["budget"] == 5_000_000_000


def _sim(v):
    return {"sol_per_day": v}


def _st(top3, days):
    return {"ex_top3": top3, "per_day": days}


CD = ["2026-11-%02d" % d for d in range(4, 9)]


def test_kill_adopt_all_three_hold():
    var = _st(2.0, {d: 1.0 for d in CD})
    ctl = _st(1.0, {d: 0.5 for d in CD})
    r = H.cell_pass(_sim(1.3), _sim(1.0), var, ctl)
    assert r["i"] and r["ii"] and r["iii"] and r["all"]
    assert H.verdict({"flat": r, "press": r}) == "WINS CLEARLY"


def test_kill_adopt_each_failure():
    var = _st(2.0, {d: 1.0 for d in CD})
    ctl = _st(1.0, {d: 0.5 for d in CD})
    assert not H.cell_pass(_sim(1.29), _sim(1.0), var, ctl)["i"]
    assert not H.cell_pass(_sim(2.0), _sim(1.0), _st(1.0, var["per_day"]), ctl)["ii"]  # equal ex-top-3 is not above
    assert not H.cell_pass(_sim(2.0), _sim(1.0), _st(None, var["per_day"]), ctl)["ii"]
    worse = {d: (1.0 if i < 2 else 0.0) for i, d in enumerate(CD)}  # 2 of 5 days at or above
    assert not H.cell_pass(_sim(2.0), _sim(1.0), _st(2.0, worse), ctl)["iii"]
    tie = {d: 0.5 for d in CD}
    assert H.cell_pass(_sim(2.0), _sim(1.0), _st(2.0, tie), ctl)["iii"]  # ties count as at or above
    # incomplete days are not counted
    part = _st(2.0, {"2026-11-03": 5.0, "2026-11-17": 5.0})
    assert H.cell_pass(_sim(2.0), _sim(1.0), part, _st(1.0, {}))["days"] == 0


def test_kill_adopt_needs_both_legs_and_negative_control():
    var = _st(2.0, {d: 1.0 for d in CD})
    ctl = _st(1.0, {d: 0.5 for d in CD})
    good = H.cell_pass(_sim(2.0), _sim(1.0), var, ctl)
    bad = H.cell_pass(_sim(1.0), _sim(1.0), var, ctl)
    assert H.verdict({"flat": good, "press": bad}) == "NO CLEAR WIN"
    assert H.cell_pass(_sim(-0.1), _sim(-1.0), var, ctl)["i"] is False  # must be positive
    assert H.cell_pass(_sim(0.5), _sim(-1.0), var, ctl)["i"] is True
