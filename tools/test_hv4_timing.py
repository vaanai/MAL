"""Tests for the runner's timing rows (shakedown measurement: exits and scans share one thread).

scan_timing (one per scan, SCAN log): {"kind", "scan_wall_ms"}. tick_timing (one per minute, SCAN log): {"kind", "minute_ms", "n_ticks",
"exit_check_delay_ms": {"p50", "p99", "max"}, "n_scans", "scan_wall_ms": {"p99", "max"} | null}. Wall-clock counts only: no outcome field, and no decision reads them.
"""
from __future__ import annotations

import pytest

from tools import hv4_runner as R
from tools.test_hv4_runner import MIN, T_START, make, restart, rows_of

SPAN = 12 * MIN
CHAIN_KEYS = ("seq", "prev", "sha")
TIMING_KINDS = ("scan_timing", "tick_timing")


@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")
        yield


def drive(tmp_path, timing: bool):
    """Sim ticks on a fake wall clock: a tick runs 100 ms after its instant, 1 s late on every tenth second; a scan takes 500 ms of wall time."""
    run = make(tmp_path)
    run.timing = timing
    wall = {"ms": 0}
    run.now_fn = lambda: wall["ms"]
    orig = run.scan

    def scan(T):
        wall["ms"] += 250
        orig(T)
        wall["ms"] += 250

    run.scan = scan
    for t in range(T_START, T_START + SPAN, R.TICK_MS):
        wall["ms"] = t + 100 + (900 if t % 10_000 == 2000 else 0)
        run.step(t)
    if timing:
        run.flush_timing()
    return run


@pytest.fixture(scope="module")
def timed(tmp_path_factory):
    return drive(tmp_path_factory.mktemp("timed"), True)


def test_the_percentile_is_nearest_rank():
    v = list(range(1, 101))
    assert R.pct(v, 0.5) == 50 and R.pct(v, 0.99) == 99 and R.pct(v, 1.0) == 100 and R.pct([7], 0.99) == 7


def test_one_scan_timing_row_per_scan_with_the_wall_time(timed):
    scans = [r for r in rows_of(timed, "SCAN", "scan")]
    rows = rows_of(timed, "SCAN", "scan_timing")
    assert len(scans) >= 2 and len(rows) == len(scans)
    assert all(r["scan_wall_ms"] == 500 for r in rows)
    assert sorted(rows[0]) == sorted((*CHAIN_KEYS, "arm", "ms", "kind", "scan_wall_ms"))  # counts and timings only


def test_one_tick_timing_row_per_minute_with_the_exit_check_delay_percentiles(timed):
    rows = rows_of(timed, "SCAN", "tick_timing")
    assert [r["minute_ms"] for r in rows] == sorted({r["minute_ms"] for r in rows}) and len(rows) == SPAN // MIN + 1
    full = [r for r in rows if r["n_ticks"] == 60]
    assert full
    for r in full:
        assert r["exit_check_delay_ms"] == {"p50": 100, "p99": 1000, "max": 1000}  # 54 ticks at 100 ms, 6 at 1,000 ms
        assert sorted(r) == sorted((*CHAIN_KEYS, "arm", "ms", "kind", "minute_ms", "n_ticks", "exit_check_delay_ms", "n_scans", "scan_wall_ms"))
    with_scan = [r for r in rows if r["n_scans"]]
    assert with_scan and all(r["n_scans"] == 1 and r["scan_wall_ms"] == {"p99": 500, "max": 500} for r in with_scan)
    assert all(r["scan_wall_ms"] is None for r in rows if not r["n_scans"])
    assert sum(r["n_ticks"] for r in rows) == SPAN // R.TICK_MS


def test_the_delay_is_measured_at_the_exit_check_not_at_the_tick_start(tmp_path):
    run = make(tmp_path)
    run.timing = True
    wall = {"ms": T_START + 100}
    run.now_fn = lambda: wall["ms"]
    orig_poll = run.poll_to

    def slow_poll(T):  # a slow poll or version refresh before the exit check shows up as delay
        orig_poll(T)
        wall["ms"] += 700

    run.poll_to = slow_poll
    run.step(T_START)
    run.flush_timing()
    assert rows_of(run, "SCAN", "tick_timing")[0]["exit_check_delay_ms"]["max"] == 800


def test_timing_changes_no_decision_and_is_off_in_sim_by_default(tmp_path):
    on = drive(tmp_path / "on", True)
    off = drive(tmp_path / "off", False)
    assert off.timing is False and not any(r["kind"] in TIMING_KINDS for r in off.log.rows("SCAN"))  # a sim log is a pure function of the tape: the md5 replay holds
    for name in (*(a for a in R.arms.ARMS), "RISK"):
        assert [f.read_bytes() for f in sorted((on.log.dir / name).glob("*.jsonl"))] == [f.read_bytes() for f in sorted((off.log.dir / name).glob("*.jsonl"))], name
    strip = lambda rows: [{k: v for k, v in r.items() if k not in CHAIN_KEYS} for r in rows if r["kind"] not in TIMING_KINDS]  # noqa: E731
    assert strip(on.log.rows("SCAN")) == strip(off.log.rows("SCAN")) and strip(on.log.rows("SCAN"))
    on.log.verify("SCAN")  # the extra rows are chained like any other


def test_a_restart_over_a_log_with_timing_rows_resumes(tmp_path):
    run = drive(tmp_path, True)
    run.engine.flush()
    run2 = restart(tmp_path, T_START + SPAN)
    assert run2.timing is False and sorted(run2.books) == sorted(run.books)
