"""Tests for the runner's refused-sell handling (shakedown #1219, CON-X: 5 refusals "sell decided before the slower entry leg lands").

The engine counts the entry leg in slots from its own wall clock; the runner's logical seconds run far ahead of it while the loop catches up after a stall, so
5 retries on 5 logical seconds all failed inside about 2 s of wall time and the book then dropped a position the engine still held. Now: such a refusal is
waited for in wall time, never counted, never an orphan; past 60 s of wall time an alert row is logged and the retries slow down.
"""
from __future__ import annotations

import pytest

from tools import hv4_runner as R
from tools.arena_paper import ArenaRefused
from tools.test_hv4_runner import X, make, restart, rows_of, T_START

EARLY = "sell decided before the slower entry leg lands"


@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")
        yield


def open_con_x(tmp_path):
    """A runner with one open CON-X position, an engine that refuses its sells with `reason` until told otherwise, and a wall clock the test moves."""
    run = make(tmp_path)
    T = T_START
    while not run.books["CON-X"].open:
        run.step(T)
        T += R.TICK_MS
        assert T < X + 10 * R.MIN_MS
    for _ in range(10):  # let the real entry leg land in the engine, so that a real sell is accepted once the test stops refusing
        run.step(T)
        T += R.TICK_MS
    state = {"refuse": EARLY, "attempts": []}
    real = run.engine.sell

    def sell(bot, pid, frac, decision_ms, decision_slot, reason=""):
        if bot == "hv4-CON-X":
            state["attempts"].append(decision_ms)
            if state["refuse"]:
                raise ArenaRefused(state["refuse"])
        return real(bot, pid, frac, decision_ms, decision_slot, reason)

    run.engine.sell = sell
    wall = {"ms": 5_000_000}
    run._now = lambda t: wall["ms"]  # the live clock, decoupled from the logical tick
    pos = next(iter(run.books["CON-X"].open.values()))
    return run, pos, state, wall, T


def test_an_entry_leg_that_has_not_landed_is_waited_for_in_wall_time_and_never_orphans(tmp_path):
    run, pos, st, wall, T = open_con_x(tmp_path)
    assert run._sell(pos, "stop", T, -0.08) is False and len(st["attempts"]) == 1
    # the loop catches up: 30 more logical seconds inside the same wall millisecond are not attempts (the old code burned its 5 tries here and dropped it)
    for k in range(1, 31):
        assert run._sell(pos, "stop", T + k * 1000, -0.08) is False
    assert len(st["attempts"]) == 1 and pos.pid in run.books["CON-X"].open
    for k in range(1, 11):  # then one try per wall second, 10 s in all
        wall["ms"] += R.SELL_EARLY_RETRY_MS
        run._sell(pos, "stop", T + 31_000 + k, -0.08)
    assert len(st["attempts"]) == 11
    assert pos.pid in run.books["CON-X"].open and pos.sell_tries == 0  # an early refusal is not counted as a try
    assert run.counters.get("sell_orphan", 0) == 0 and not rows_of(run, "CON-X", "sell_alert")
    # the engine reports the leg landed: the next attempt sells and the book lets go
    st["refuse"] = None
    wall["ms"] += R.SELL_EARLY_RETRY_MS
    assert run._sell(pos, "stop", T, -0.08) is True
    assert pos.pid not in run.books["CON-X"].open and pos.pid not in run._sell_next and not run._sell_first


def test_after_the_wall_clock_cap_an_alert_is_logged_once_and_the_retries_slow_down(tmp_path):
    run, pos, st, wall, T = open_con_x(tmp_path)
    run._sell(pos, "stop", T, -0.08)
    wall["ms"] += R.SELL_EARLY_CAP_MS - R.SELL_EARLY_RETRY_MS
    run._sell(pos, "stop", T + 1000, -0.08)
    assert not rows_of(run, "CON-X", "sell_alert")
    wall["ms"] += R.SELL_EARLY_RETRY_MS  # the cap: 60 s of wall time since the first early refusal
    n = len(st["attempts"])
    run._sell(pos, "stop", T + 2000, -0.08)
    alerts = rows_of(run, "CON-X", "sell_alert")
    assert len(alerts) == 1 and alerts[0]["pid"] == pos.pid and alerts[0]["waited_ms"] >= R.SELL_EARLY_CAP_MS and len(st["attempts"]) == n + 1
    wall["ms"] += R.SELL_EARLY_RETRY_MS  # the fast cadence is over
    run._sell(pos, "stop", T + 3000, -0.08)
    assert len(st["attempts"]) == n + 1
    for _ in range(3):  # slow cadence: still retried, still open, still one alert
        wall["ms"] += R.SELL_SLOW_RETRY_MS
        run._sell(pos, "stop", T + 4000, -0.08)
    assert len(st["attempts"]) == n + 4 and pos.pid in run.books["CON-X"].open
    assert len(rows_of(run, "CON-X", "sell_alert")) == 1 and run.counters.get("sell_orphan", 0) == 0
    st["refuse"] = None
    wall["ms"] += R.SELL_SLOW_RETRY_MS
    assert run._sell(pos, "stop", T, -0.08) is True and pos.pid not in run.books["CON-X"].open


def test_other_refusals_are_counted_then_slowed_with_an_alert_but_the_position_is_kept(tmp_path):
    run, pos, st, wall, T = open_con_x(tmp_path)
    st["refuse"] = "ledger_unreadable: disk"
    for k in range(R.SELL_RETRY_MAX):  # up to the cap they are retried at once, as before
        run._sell(pos, "stop", T + k * 1000, -0.08)
    assert pos.sell_tries == R.SELL_RETRY_MAX and len(st["attempts"]) == R.SELL_RETRY_MAX
    assert len(rows_of(run, "CON-X", "sell_alert")) == 1
    assert pos.pid in run.books["CON-X"].open and run.counters.get("sell_orphan", 0) == 0  # the old code dropped it here
    run._sell(pos, "stop", T + 9000, -0.08)
    assert len(st["attempts"]) == R.SELL_RETRY_MAX  # slowed
    wall["ms"] += R.SELL_SLOW_RETRY_MS
    run._sell(pos, "stop", T + 10_000, -0.08)
    assert len(st["attempts"]) == R.SELL_RETRY_MAX + 1


@pytest.mark.parametrize("text", ["no open position abc for bot hv4-CON-X", "position abc already has a sell"])
def test_a_position_the_engine_does_not_hold_is_still_closed_in_the_book(tmp_path, text):
    run, pos, st, wall, T = open_con_x(tmp_path)
    st["refuse"] = text
    run._sell(pos, "stop", T, -0.08)
    assert pos.pid not in run.books["CON-X"].open and run.counters["sell_orphan"] == 1 and pos.pid not in run._sell_next


def test_a_restart_keeps_a_position_whose_sell_was_refused_for_the_entry_leg(tmp_path):
    run, pos, st, wall, T = open_con_x(tmp_path)
    for k in range(R.SELL_RETRY_MAX + 3):
        wall["ms"] += R.SELL_EARLY_RETRY_MS
        run._sell(pos, "stop", T + k, -0.08)
    assert len(rows_of(run, "CON-X", "order_refused")) == R.SELL_RETRY_MAX + 3 and pos.pid in run.books["CON-X"].open
    run.engine.flush()
    run2 = restart(tmp_path, T + 100)
    assert pos.pid in run2.books["CON-X"].open and run2.books["CON-X"].open[pos.pid].sell_tries == 0
