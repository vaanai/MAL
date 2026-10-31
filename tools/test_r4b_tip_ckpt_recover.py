"""A recoverable checkpoint problem is retried on the next poll; the unsafe case stays latched (reviewer on #1155, non-blocking item before the long DRY run).
Real TipReader over synthetic files, a real TipLiveSource with a fake window factory. No network, no key, no tip file."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import r4b_feed_merge as fm
from tools import r4b_host_glue as hg
from tools import r4b_tip_reader as tr
from tools.test_r4b_tip_history import FW, Now, T, write_status
from tools.test_r4b_tip_reader import H05, put, trade, write_state

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
