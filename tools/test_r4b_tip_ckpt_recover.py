"""A recoverable checkpoint problem is retried on the next poll; the unsafe case stays latched (reviewer on #1155, non-blocking item before the long DRY run).
Real TipReader over synthetic files, a real TipLiveSource with a fake window factory. No network, no key, no tip file."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import r4b_feed_merge as fm
from tools import r4b_host_glue as hg
from tools import r4b_tip_reader as tr
from tools.test_r4b_tip_history import FW, Now, T, arm_window, write_status
from tools.test_r4b_tip_reader import B, H05, put, reader, slots_of, trade, write_state

NOW_MS = (T - 600) * 1000


def real_source(tmp_path: Path):
    now = Now(NOW_MS)
    out, state = tmp_path / "out", tmp_path / "state"
    put(out, "trades-" + tr.hour_key(NOW_MS // 1000) + ".jsonl", [])
    write_status(state, now.ms)
    tip = tr.TipReader(out, state, start_ms=NOW_MS, catch_up_hours=0, min_hour="2026-01-01T00", lookback_h=None, fadvise=False)
    src = hg.TipLiveSource(tip=tip, tip_state_dir=state, cap=fm.DailyCreditCap(50_000, tmp_path / "cap.json"), window_factory=lambda *a, **k: FW(T), spawn=lambda w: None,
                           now_ms=now, sleep=lambda s: None, require_ntp=False, io_hooks=(lambda: True, lambda: True))
    return src, now, tip, out, state


def good_checkpoint(state: Path, now: Now, D: int) -> None:
    write_state(state, last_done=D, last_t_recv_ms=now.ms)
    write_status(state, now.ms)


@pytest.mark.parametrize("bad", ["no_t", "unreadable", "missing"])
def test_a_recoverable_checkpoint_problem_is_retried_and_clears(tmp_path, bad):
    src, now, tip, out, state = real_source(tmp_path)
    put(out, "trades-" + tr.hour_key(NOW_MS // 1000) + ".jsonl", [trade(s) for s in (1, 2, 3)])
    src.start()
    if bad == "no_t":
        write_state(state, last_done=3, last_t_recv_ms=0)
    elif bad == "unreadable":
        (state / "checkpoint.json").write_text("{not json")
    else:
        (state / "checkpoint.json").unlink(missing_ok=True)
    src._read_public(now.ms)
    assert tip.problem in tr.RECOVERABLE_PROBLEMS and not src._pending
    now.ms += 6_000  # past the status TTL: the problem is cached as unhealthy
    write_status(state, now.ms)
    assert src._tip_problem() in tr.RECOVERABLE_PROBLEMS
    src._read_public(now.ms)  # still bad: retried, still closed
    assert not src._pending and src.public_hw_slot() is None
    good_checkpoint(state, now, 3)  # the file is good again
    src._read_public(now.ms)
    assert [r["w"]["slot"] for r in src._pending if r.get("type") == "tip"] == [1, 2, 3]
    assert tip.problem is None and src._tip_problem() is None  # no wait for the TTL once the poll recovered
    while src._pending:
        src._pending.popleft()
    assert src.public_hw_slot() == 3


def test_a_regressed_checkpoint_stays_latched_even_when_the_file_is_good_again(tmp_path):
    src, now, tip, out, state = real_source(tmp_path)
    put(out, "trades-" + tr.hour_key(NOW_MS // 1000) + ".jsonl", [trade(s) for s in range(1, 8)])
    src.start()
    good_checkpoint(state, now, 5)
    src._read_public(now.ms)
    while src._pending:
        src._pending.popleft()
    good_checkpoint(state, now, 3)  # backwards: the follower restarted from an older point
    src._read_public(now.ms)
    assert tip.problem == "checkpoint_regressed" and tip.latched == "checkpoint_regressed"
    for D in (5, 7):  # it passes the old D again, and the file is perfectly readable
        now.ms += 6_000
        good_checkpoint(state, now, D)
        src._read_public(now.ms)
        assert tip.problem == "checkpoint_regressed" and not src._pending and src._tip_problem() == "checkpoint_regressed"
    assert src.public_hw_slot() is None
    c = src.mark_complete(T, 1004) if T in src._wins else None
    assert c is None or not c.complete


def test_status_problems_still_stop_the_reads(tmp_path):
    src, now, tip, out, state = real_source(tmp_path)
    put(out, "trades-" + tr.hour_key(NOW_MS // 1000) + ".jsonl", [trade(1)])
    src.start()
    good_checkpoint(state, now, 1)
    write_status(state, now.ms, trade_event_v=False)
    now.ms += 6_000
    write_status(state, now.ms, trade_event_v=False)
    src._read_public(now.ms)
    assert not src._pending and tip.stats["rows_trade"] == 0  # not even read


# ------------------------------------------------------------------------------------------------------------------------------------ resets (reviewer on #1158)
def tip_file(tmp_path: Path) -> str:
    return f"trades-{H05}.jsonl"  # the reader-level tests start the reader in hour 05 (tools.test_r4b_tip_reader.reader)


def first_good(tmp_path: Path, **kw):
    put(tmp_path / "out", tip_file(tmp_path), [trade(s) for s in (1, 2, 3)])
    write_state(tmp_path / "state", last_done=3, last_t_recv_ms=(B - 100) * 1000, **kw)
    r = reader(tmp_path)
    assert slots_of(r.poll()) == [1, 2, 3] and r.D == 3
    return r


def test_reset_a_missing_checkpoint_then_a_fresh_one_latches_and_the_hole_never_certifies(tmp_path):
    r = first_good(tmp_path)
    (tmp_path / "state" / "checkpoint.json").unlink()  # the operator deletes a corrupt checkpoint and restarts the follower
    assert r.poll() == [] and r.problem == "checkpoint_reset" and r.latched == "checkpoint_reset" and not r.caught_up
    write_state(tmp_path / "state", last_done=9, last_t_recv_ms=0)  # the fresh follower: last_done = tip - 1, last_t_recv_ms still 0
    assert r.poll() == [] and r.problem == "checkpoint_reset"
    put(tmp_path / "out", tip_file(tmp_path), [trade(s) for s in (10, 11, 12)], mode="a")
    write_state(tmp_path / "state", last_done=12, last_t_recv_ms=(B - 50) * 1000)
    assert r.poll() == [] and r.problem == "checkpoint_reset" and r.released_D == 3  # slots 4..9 are a hole nobody recorded: the reader stays shut


@pytest.mark.parametrize("what", ["no_t_zero", "no_t_absent", "last_done_absent", "last_done_null"])
def test_reset_a_checkpoint_that_loses_last_t_or_last_done_after_a_good_one_latches(tmp_path, what):
    r = first_good(tmp_path)
    body = {"last_done": 9, "last_t_recv_ms": 0, "gaps": 0, "skipped": 0, "writing": None}
    if what == "no_t_absent":
        del body["last_t_recv_ms"]
    if what == "last_done_absent":
        del body["last_done"]
    if what == "last_done_null":
        body["last_done"] = None
    (tmp_path / "state" / "checkpoint.json").write_text(json.dumps(body))
    assert r.poll() == [] and r.problem == "checkpoint_reset" and r.latched


def test_a_torn_or_unparseable_read_is_retried_when_the_next_checkpoint_is_consistent(tmp_path):
    r = first_good(tmp_path, gaps=2, skipped=5)
    (tmp_path / "state" / "checkpoint.json").write_text('{"last_done": 3, "last_t_rec')  # present but unparseable
    assert r.poll() == [] and r.problem == "checkpoint_unreadable" and r.latched is None
    put(tmp_path / "out", tip_file(tmp_path), [trade(4)], mode="a")
    write_state(tmp_path / "state", last_done=4, last_t_recv_ms=(B - 50) * 1000, gaps=2, skipped=6)
    assert slots_of(r.poll()) == [4] and r.problem is None and r.caught_up


def test_unparseable_then_a_checkpoint_with_dropped_counters_is_a_reset(tmp_path):
    r = first_good(tmp_path, gaps=2, skipped=5)
    (tmp_path / "state" / "checkpoint.json").write_text("{not json")
    assert r.poll() == [] and r.latched is None
    write_state(tmp_path / "state", last_done=12, last_t_recv_ms=(B - 50) * 1000, gaps=0, skipped=0)  # a fresh follower zeroes both
    assert r.poll() == [] and r.problem == "checkpoint_reset" and r.latched


@pytest.mark.parametrize("gaps,skipped,latched", [(1, 6, True), (2, 2, True), (3, 3, True), (2, 5, False), (3, 4, False), (2, 9, False), (7, 0, False), (4, 0, True)])
def test_the_gap_counters_never_drop(tmp_path, gaps, skipped, latched):
    """gaps never drops; skipped + gaps never drops (`_verify_skips` moves one from skipped to gaps, so skipped alone may fall by one)."""
    r = first_good(tmp_path, gaps=2, skipped=5)
    write_state(tmp_path / "state", last_done=3, last_t_recv_ms=(B - 90) * 1000, gaps=gaps, skipped=skipped)
    r.poll()
    assert (r.latched == "checkpoint_reset") is latched and (r.problem == "checkpoint_reset") is latched


def test_before_the_first_good_checkpoint_everything_is_retried(tmp_path):
    put(tmp_path / "out", tip_file(tmp_path), [trade(1)])
    r = reader(tmp_path)
    assert r.poll() == [] and r.problem == "checkpoint_unreadable" and r.latched is None  # no file yet
    write_state(tmp_path / "state", last_done=1, last_t_recv_ms=0)
    assert r.poll() == [] and r.problem == "checkpoint_no_t" and r.latched is None
    (tmp_path / "state" / "checkpoint.json").write_text(json.dumps({"last_done": None, "last_t_recv_ms": 0}))
    assert r.poll() == [] and r.problem == "checkpoint_unreadable" and r.latched is None
    write_state(tmp_path / "state", last_done=1, last_t_recv_ms=(B - 50) * 1000)
    assert slots_of(r.poll()) == [1] and r.problem is None


def host_file() -> str:
    return "trades-" + tr.hour_key(NOW_MS // 1000) + ".jsonl"


def test_reset_through_the_host_reviewer_sequence_leaves_no_hole_to_certify(tmp_path):
    src, now, tip, out, state = real_source(tmp_path)
    put(out, host_file(), [trade(s) for s in (1, 2, 3)])
    src.start()
    good_checkpoint(state, now, 3)
    src._read_public(now.ms)
    while src._pending:
        src._pending.popleft()
    assert src.public_hw_slot() == 3
    (state / "checkpoint.json").unlink()
    write_state(state, last_done=9, last_t_recv_ms=0)
    put(out, host_file(), [trade(s) for s in (10, 11, 12)], mode="a")
    for D in (None, 12):
        now.ms += 6_000
        write_status(state, now.ms)
        if D:
            good_checkpoint(state, now, D)
        src._read_public(now.ms)
        assert tip.latched == "checkpoint_reset" and not src._pending and src._tip_problem() == "checkpoint_reset" and src.public_hw_slot() is None
    assert not src.merger.open_tip_gaps() and tip.released_D == 3


def test_reset_behind_a_stale_status_is_caught_by_the_counters(tmp_path):
    src, now, tip, out, state = real_source(tmp_path)
    put(out, host_file(), [trade(s) for s in (1, 2, 3)])
    src.start()
    write_state(state, last_done=3, last_t_recv_ms=now.ms, gaps=2, skipped=5)
    write_status(state, now.ms)
    src._read_public(now.ms)
    while src._pending:
        src._pending.popleft()
    now.ms += 120_000  # the follower is down: status.json goes stale and the host stops polling
    assert src._tip_problem() == "status_stale"
    src._read_public(now.ms)
    write_state(state, last_done=12, last_t_recv_ms=now.ms, gaps=0, skipped=0)  # restarted without its checkpoint, back up and fresh
    write_status(state, now.ms)
    now.ms += 6_000
    write_status(state, now.ms)
    src._read_public(now.ms)
    assert tip.latched == "checkpoint_reset" and not src._pending and src.public_hw_slot() is None


def test_a_recoverable_problem_does_not_fail_the_mark_at_once_but_a_latched_one_does(tmp_path):
    src, now, tip, out, state = real_source(tmp_path)
    put(out, host_file(), [trade(1)])
    src.start()
    arm_window(src, now)
    write_state(state, last_done=1, last_t_recv_ms=0)  # before the first good checkpoint: the start-up wait
    src._read_public(now.ms)
    assert tip.problem == "checkpoint_no_t" and src.failed(T) is None
    assert not src.mark_complete(T, 1004).complete
    good_checkpoint(state, now, 1)
    src._read_public(now.ms)
    (state / "checkpoint.json").unlink()
    src._read_public(now.ms)
    now.ms += 6_000
    write_status(state, now.ms)
    assert tip.latched == "checkpoint_reset" and src.failed(T) == "tip_checkpoint_reset"
