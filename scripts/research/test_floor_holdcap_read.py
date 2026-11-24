"""Tests for floor_holdcap_read and floor_holdcap_run.sh on synthetic data only (no real ledger, tape or book dir is opened)."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import floor_holdcap_read as H  # noqa: E402

RUN_SH = str(Path(__file__).resolve().parent / "floor_holdcap_run.sh")
LIM = {"R": 3_860_000, "max_open": 2, "budget": 5_000_000_000, "daily": 800_000_000}
HR = H.HOUR_MS
T0 = H.COUNT_FROM
_seq = [0]


def tr(entry_h, hold_h, flat=0, press=None, day=None, seq=None):
    e = T0 + int(entry_h * HR)
    x = e + int(hold_h * HR)
    _seq[0] += 1
    return {"seq": _seq[0] if seq is None else seq, "entry_ms": e, "exit_ms": x, "flat": flat, "press": flat if press is None else press, "close_day": day or "2026-11-12"}


def test_window_constants():
    assert (H.END_MS - H.COUNT_FROM) // HR == H.MAX_HOURS == 168
    assert H.START_HOUR == "2026-11-11T12" and H.SEED_HOUR == "2026-11-03T03"
    assert H.COMPLETE_DAYS[0] == "2026-11-12" and H.COMPLETE_DAYS[-1] == "2026-11-16" and len(H.COMPLETE_DAYS) == 5 == H.MIN_DAYS
    assert (H.COUNT_FROM - H._ms("2026-11-03T03:00:00Z")) // HR >= 201


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
    assert w["taken"] == 3 and w["skipped"] == 1 and w["skip_why"]["max_open"] == 1  # third entry blocked; entry 4 at h11 sees the exits at h10 and h11
    assert abs(w["realized_sol"] - 3e-3) < 1e-12


def test_wallet_sim_capital_binds_and_scales_linearly():
    ts = [tr(0, 10, 100_000_000), tr(1, 10, 100_000_000)]
    lim = dict(LIM, max_open=8)
    w = H.wallet_sim(ts, "flat", 0.25, 0.40, lim, 1_000_000_000, 750_000_000, 1.0)  # 1.0 - 0.40 - R < 0.75: skipped for the wallet
    assert w["taken"] == 0 and w["skipped"] == 2 and w["skip_why"]["wallet"] == 2
    w = H.wallet_sim(ts, "flat", 0.25, 0.40, lim, 1_500_000_000, 750_000_000, 1.0)  # one fits, the second does not while the first is open
    assert w["taken"] == 1 and w["skipped"] == 1
    assert abs(w["realized_sol"] - 0.1 * 0.40 / 0.25) < 1e-12
    ts2 = [tr(0, 10, 100_000_000), tr(10, 10, 100_000_000)]  # capital back at exit (equal instant): both taken
    assert H.wallet_sim(ts2, "flat", 0.25, 0.40, lim, 1_500_000_000, 750_000_000, 1.0)["taken"] == 2


def test_wallet_realized_raises_free_capital_after_exit():
    ts = [tr(0, 1, 500_000_000), tr(2, 1, 0)]
    lim = dict(LIM, max_open=8)
    assert H.wallet_sim(ts, "flat", 0.25, 0.25 * 1.6, lim, 1_200_000_000, 750_000_000, 1.0)["taken"] == 2


def test_budget_full_wipe_formula():
    lim = dict(LIM, max_open=8, budget=1_000_000_000)
    cost = 400_000_000 + lim["R"]
    ts = [tr(0, 10, 0), tr(1, 10, 0), tr(2, 10, 0)]
    w = H.wallet_sim(ts, "flat", 0.25, 0.40, lim, 100_000_000_000, 0, 1.0)
    assert w["taken"] == 2 and w["skip_why"]["budget"] == 1
    assert not (0 - 3 * cost > -lim["budget"]) and (0 - 2 * cost > -lim["budget"])
    lim2 = dict(lim, budget=cost)  # exactly -budget is refused
    w = H.wallet_sim([tr(0, 1, 0)], "flat", 0.25, 0.40, lim2, 100_000_000_000, 0, 1.0)
    assert w["taken"] == 0 and w["skip_why"]["budget"] == 1


def test_tie_break_is_entry_then_seq_not_exit_time():
    # two same-ms entries, max_open=1: the one with the lower seq is taken even though the other exits sooner
    a = {**tr(0, 10, 7_000_000), "seq": 1}
    b = {**tr(0, 1, 9_000_000), "seq": 2}
    lim = dict(LIM, max_open=1)
    for order in ([a, b], [b, a]):
        w = H.wallet_sim(order, "flat", 0.25, 0.25, lim, 10_000_000_000, 0, 1.0)
        assert w["taken"] == 1 and abs(w["realized_sol"] - 7e-3) < 1e-12 and w["skip_why"]["max_open"] == 1


def test_daily_stop_realized_arm():
    lim = dict(LIM, max_open=8, daily=100_000_000)
    day0 = T0 // H.DAY_MS
    e0 = day0 * H.DAY_MS + 12 * HR  # inside the day of T0 or the next; use explicit instants in one UTC day
    loser = {"seq": 1, "entry_ms": e0, "exit_ms": e0 + HR, "flat": -100_000_000, "press": -100_000_000, "close_day": "x"}
    later = {"seq": 2, "entry_ms": e0 + 2 * HR, "exit_ms": e0 + 3 * HR, "flat": 5, "press": 5, "close_day": "x"}
    nextday = {"seq": 3, "entry_ms": (day0 + 1) * H.DAY_MS + HR, "exit_ms": (day0 + 1) * H.DAY_MS + 2 * HR, "flat": 5, "press": 5, "close_day": "x"}
    w = H.wallet_sim([loser, later, nextday], "flat", 0.25, 0.25, lim, 10_000_000_000, 0, 1.0)
    assert w["taken"] == 2 and w["skip_why"]["daily_stop"] == 1  # blocked until the next 00:00Z


def test_limits_read_from_executor():
    lim = H.limits()
    assert lim["R"] == 3_860_000 and lim["max_open"] == 16 and lim["budget"] == 5_000_000_000 and lim["daily"] == 800_000_000
    assert H.limits(4)["max_open"] == 4


def _sim(v):
    return {"sol_per_day": v}


def _st(top3, days, n=100):
    return {"ex_top3": top3, "per_day": days, "n": n}


CD = H.COMPLETE_DAYS


def test_kill_adopt_all_three_hold():
    var = _st(2.0, {d: 1.0 for d in CD})
    ctl = _st(1.0, {d: 0.5 for d in CD})
    r = H.cell_pass(_sim(1.3), _sim(1.0), var, ctl)
    assert r["i"] and r["ii"] and r["iii"] and r["all"] and r["days"] == 5
    assert H.verdict({"flat": r, "press": r}) == "WINS CLEARLY"


def test_kill_adopt_each_failure():
    var = _st(2.0, {d: 1.0 for d in CD})
    ctl = _st(1.0, {d: 0.5 for d in CD})
    assert not H.cell_pass(_sim(1.29), _sim(1.0), var, ctl)["i"]
    assert not H.cell_pass(_sim(2.0), _sim(1.0), _st(1.0, var["per_day"]), ctl)["ii"]  # equal ex-top-3 is not above
    assert not H.cell_pass(_sim(2.0), _sim(1.0), _st(None, var["per_day"]), ctl)["ii"]
    worse = {d: (1.0 if i < 2 else 0.0) for i, d in enumerate(CD)}  # 2 of 5 days at or above: not a majority
    assert not H.cell_pass(_sim(2.0), _sim(1.0), _st(2.0, worse), ctl)["iii"]
    tie = {d: 0.5 for d in CD}
    assert H.cell_pass(_sim(2.0), _sim(1.0), _st(2.0, tie), ctl)["iii"]  # ties count as at or above
    # only the fixed complete days count; a variant with no entry on a day books 0
    part = _st(2.0, {"2026-11-11": 5.0, "2026-11-17": 5.0})
    assert H.cell_pass(_sim(2.0), _sim(1.0), part, _st(1.0, {}))["days"] == 5


def test_kill_adopt_needs_both_legs_and_negative_control():
    var = _st(2.0, {d: 1.0 for d in CD})
    ctl = _st(1.0, {d: 0.5 for d in CD})
    good = H.cell_pass(_sim(2.0), _sim(1.0), var, ctl)
    bad = H.cell_pass(_sim(1.0), _sim(1.0), var, ctl)
    assert H.verdict({"flat": good, "press": bad}) == "NO CLEAR WIN"
    assert H.cell_pass(_sim(-0.1), _sim(-1.0), var, ctl)["i"] is False  # must be positive
    assert H.cell_pass(_sim(0.5), _sim(-1.0), var, ctl)["i"] is True


def test_not_decidable_floor():
    days = {d: 1.0 for d in CD}
    assert H.decidable(_st(1.0, days, n=40), _st(1.0, days, n=500))
    assert not H.decidable(_st(1.0, days, n=39))  # too few trades
    assert not H.decidable(_st(1.0, {d: 1.0 for d in CD[:4]}, n=100))  # 4 days with a trade
    assert H.decidable(_st(1.0, {d: 1.0 for d in CD[:5]}, n=100))
    good = H.cell_pass(_sim(2.0), _sim(1.0), _st(2.0, days), _st(1.0, {d: 0.5 for d in CD}))
    assert H.verdict({"flat": good, "press": good}, ok=False) == "NOT_DECIDABLE"


def _pos(pid, t0, t1, status, net=0):
    return {"pid": pid, "t0": t0, "t1": t1, "status": {"0.25": status}, "net": {"0.25:flat": net, "0.25:press": net}, "exit_reason": {"0.25": "time"}, "reason": {"0.25": "feed_gap"}, "leg_state": {"0.25": "filled"}}


def test_split_priced_window_counts_and_booking():
    end = H.END_MS
    inwin = T0 + HR
    pnl = {"positions": [
        _pos("a", T0 - 1, T0 + HR, "closed", 5),  # before the window: not counted
        _pos("b", inwin, inwin + HR, "closed", 7),
        _pos("c", inwin + 1, end + 1, "closed", 9),  # closes after the pricing end: open_at_end
        _pos("d", inwin + 2, None, "engine_excluded"),
        _pos("e", inwin + 3, None, "open_at_tape_end"),
        _pos("f", H.COUNT_TO, H.COUNT_TO + HR, "closed", 11),  # after the window: not counted
    ]}
    r = H.split_priced(pnl, end)
    assert [t["flat"] for t in r["trades"][0.25]] == [7]
    c = r["counts"][0.25]
    assert c["open_at_end"] == 2 and c["unpriced"] == 1 and c["excluded"]["feed_gap"] == 1
    assert r["trades"][0.25][0]["seq"] == 1 and r["trades"][0.25][0]["close_day"]


def test_report_prints_header_counts_and_sensitivity():
    pnl = {"positions": [_pos("b%d" % i, T0 + i * HR, T0 + i * HR + 6 * HR, "closed", 1_000) for i in range(3)]}
    pb = H.split_priced(pnl)
    books = [("control24", pb), ("h12", pb), ("h6", pb)]
    a = argparse.Namespace(wallet_sim=True, start_wallet_sol=3.96808, topup_sol=2.0, board_best_of="7", wallet_floor_sol=0.75, sim_stake=0.40)
    out = "\n".join(H.report(books, a, dict(LIM, max_open=16)))
    assert "best-of-N = 7" in out and "NOT BLIND" in out and "DEC-035 exit-rule amendment" in out
    assert "open_at_end=0 unpriced=0 excluded=" in out
    assert "sensitivity_topup_report_only_does_not_decide start_wallet_sol=5.96808" in out
    assert "NOT_DECIDABLE" in out  # 3 trades is below the floor
    assert "realized day arm only" in out


def _run_check(tmp_path, counts):
    for id_, n in counts.items():
        d = tmp_path / id_
        d.mkdir()
        (d / "md5-by-hour.jsonl").write_text("".join('{"hour": "h%d", "decision_md5": "abc"}\n' % i for i in range(n)))
    env = dict(os.environ, HOLDCAP_CHECK_ONLY="1", HOLDCAP_BASE=str(tmp_path), PY=sys.executable)
    return subprocess.run(["sh", RUN_SH], env=env, capture_output=True, text=True)


def test_runner_incomplete_guard(tmp_path):
    r = _run_check(tmp_path, {"holdcap-control24": 168, "holdcap-h12": 167, "holdcap-h6": 168})
    assert r.returncode == 1
    assert "hours_processed=168 of 168" in r.stdout and "INCOMPLETE book holdcap-h12 hours_processed=167 < 168" in r.stdout
    assert "reader not run" in r.stdout and "INCOMPLETE book holdcap-h6" not in r.stdout


def test_runner_complete_and_missing_log(tmp_path):
    r = _run_check(tmp_path, {"holdcap-control24": 168, "holdcap-h12": 168, "holdcap-h6": 168})
    assert r.returncode == 0 and "INCOMPLETE" not in r.stdout and "last_decision_md5=abc" in r.stdout
    t2 = tmp_path / "x"
    t2.mkdir()
    r = _run_check(t2, {"holdcap-control24": 168, "holdcap-h12": 168})  # h6 has no log at all
    assert r.returncode == 1 and "INCOMPLETE book holdcap-h6 hours_processed=0" in r.stdout


def test_runner_refuses_wrong_follower_checkout(tmp_path):
    env = dict(os.environ, HOLDCAP_BASE=str(tmp_path / "b"), SIM_REPO=str(tmp_path), BOARD_BEST_OF="1", PY=sys.executable)
    r = subprocess.run(["sh", RUN_SH], env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "REFUSED" in r.stdout


def test_runner_default_out_roots_pass_the_follower_guard():
    import osb_follow as O
    base = O.OSB_ROOT + "/holdcap"
    assert 'BASE="${HOLDCAP_BASE:-%s}"' % base in open(RUN_SH).read()
    for id_ in ("holdcap-control24", "holdcap-h12", "holdcap-h6"):
        O.guard_paths({"tape_dir": O.OSB_ROOT + "/keep", "out_root": "%s/%s" % (base, id_)}, (O.OSB_ROOT,))  # raises Refused on a bad root
