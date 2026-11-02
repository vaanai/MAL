"""Tests of tools/s1t_arena_feed_watch.py (QP-NOTE-1102-tip-readers). Synthetic status dicts and files in tmp_path only; no arena path is opened."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from tools import s1t_arena_feed_watch as aw

BASE_MS = 1_800_000_000_000
H = 3_600_000


def st(i, gaps, rec, *, pid=1, started_h=20.0, base_ms=BASE_MS):
    return {"updated_ms": base_ms + i * 60_000, "started_ms": base_ms - started_h * H, "pid": pid, "gaps": gaps,
            "sockets": {"pumpswap": [{"reconnects": rec - 1}, {"reconnects": 1}], "pumpfun": {"a": {"reconnects": 0}}}}


def step(w, i, gaps, rec, **kw):
    s = st(i, gaps, rec, **{k: v for k, v in kw.items() if k != "stale_s"})
    return w.step(s, i * 60.0, s["updated_ms"] + 1000 * kw.get("stale_s", 0))


def first_stop(rows, **kw):
    w = aw.Watcher()
    for i, (g, r) in enumerate(rows):
        out = step(w, i, g, r, **kw)
        if any(x.startswith("S1T_SIDE_FEED_STOP") for x in out):
            return i, out
    return None, []


def ramp(extra_gaps=0, per_min_gaps=0.0, per_min_rec=0, n=140):
    """Old feed: 100 gaps and 40 reconnects at the first sample (baselines 5.00/h and 2.00/h), flat to minute 60, then the change from minute 61."""
    rows = []
    for i in range(n):
        late = max(0, i - 60)
        rows.append((100 + int(late * per_min_gaps) + (extra_gaps if i >= 61 else 0), 40 + late * per_min_rec))
    return rows


def test_gap_trigger_two_consecutive_checks():
    i, out = first_stop(ramp(per_min_gaps=1))  # trailing delta = i - 60; > 35 first at 96, second consecutive check at 97
    assert i == 97
    assert out == ["S1T_SIDE_FEED_STOP gaps_h=37.00 base=5.00 reconnects_h=0.00 base=2.00"]


def test_reconnect_trigger():
    i, out = first_stop(ramp(per_min_rec=1))  # threshold max(4, 12) = 12: first above at minute 73, stop at 74
    assert i == 74
    assert out == ["S1T_SIDE_FEED_STOP gaps_h=0.00 base=5.00 reconnects_h=14.00 base=2.00"]


def test_threshold_is_strict_and_needs_a_trailing_hour():
    assert first_stop(ramp(extra_gaps=35))[0] is None  # exactly baseline + 30 over 2 x baseline: 35 is not above max(10, 35)
    i, out = first_stop(ramp(extra_gaps=36))
    assert i == 62 and "gaps_h=36.00" in out[0]
    rows = [(100 + 500 * k, 40) for k in range(59)]  # hot from the start, but no full hour of history yet: no check
    assert first_stop(rows)[0] is None


def test_young_feed_baseline_is_the_first_60_minutes():
    rows = [(i // 6, 0) for i in range(61)] + [(51, 0)] * 80  # 10 gaps in the first hour, then a jump of 41
    w = aw.Watcher()
    for i, (g, r) in enumerate(rows[:60]):
        assert step(w, i, g, r, started_h=2.0) == [] and w.base is None  # nothing is checked before the baseline exists
    i, out = first_stop(rows, started_h=2.0)
    assert i == 62
    assert out == ["S1T_SIDE_FEED_STOP gaps_h=51.00 base=10.00 reconnects_h=0.00 base=0.00"]


def test_pid_change_rebaselines_and_is_not_a_trigger():
    w = aw.Watcher()
    rows = ramp(per_min_gaps=1)
    for i in range(97):
        step(w, i, *rows[i])
    assert w.streak == 1
    out = step(w, 97, 0, 0, pid=2, started_h=0.1)  # a new feed process, counters back near zero
    assert out == ["S1T_SIDE_FEED_REBASELINE"] and w.base is None and w.streak == 0
    for i in range(98, 160):
        assert not any("STOP" in x for x in step(w, i, 0, 0, pid=2, started_h=0.1))
    assert w.base == (0.0, 0.0)


def test_counter_going_down_is_a_restart_too():
    w = aw.Watcher()
    step(w, 0, 100, 40)
    assert step(w, 1, 5, 40) == ["S1T_SIDE_FEED_REBASELINE"]


def test_stale_and_unreadable_are_not_triggers_and_reset_the_streak():
    w = aw.Watcher()
    rows = ramp(per_min_gaps=1)
    for i in range(97):
        step(w, i, *rows[i])
    assert w.streak == 1
    assert step(w, 97, *rows[97], stale_s=301) == ["STALE"] and w.streak == 0
    assert step(w, 98, *rows[98]) == [] and w.streak == 1  # one hot check only
    assert any("STOP" in x for x in step(w, 99, *rows[99]))
    w2 = aw.Watcher()
    assert step(w2, 0, 100, 40, stale_s=300) == []  # exactly 300 s is not stale
    assert w2.step(None, 0.0, 0) == ["UNREADABLE"]
    assert w2.step({"updated_ms": 1}, 0.0, 0) == ["UNREADABLE"]


def test_extract_shapes():
    base = {"updated_ms": 1, "started_ms": 0, "pid": 7}
    ex = aw.extract({**base, "gaps": [1, 2, 3], "sockets": {"a": [{"reconnects": 2}, {"reconnects": 3}], "b": {"x": {"reconnects": 4}}, "c": []}})
    assert ex["gaps"] == 3 and ex["rec"] == 9
    assert aw.extract({**base, "gaps": {"k": 1}, "sockets": {}})["gaps"] == 1
    assert aw.extract({**base, "gaps": 12, "sockets": {"a": [{"reconnects": "x"}, {}, 5]}})["rec"] == 0
    assert aw.extract({**base, "gaps": "n", "sockets": {}}) is None and aw.extract([1]) is None


def drive(rows, seconds=None):
    clock = {"t": 0.0}
    n = {"i": 0}
    out = []

    def read():
        i = n["i"] = min(n["i"], len(rows) - 1)
        s = st(i, *rows[i])
        clock["wall"] = s["updated_ms"]
        return s

    def sleep(s):
        clock["t"] += s
        n["i"] += 1

    rc = aw.run_watch(read, seconds, 60.0, clock=lambda: clock["t"], sleep=sleep, wall_ms=lambda: clock["wall"], emit=out.append)
    return rc, out, n["i"]


def test_run_watch_stops_with_exit_4_and_the_stop_line():
    rc, out, i = drive(ramp(per_min_gaps=1))
    assert rc == aw.EXIT_STOP == 4 and i == 97
    assert out == ["S1T_SIDE_FEED_STOP gaps_h=37.00 base=5.00 reconnects_h=0.00 base=2.00"]


def test_run_watch_seconds_exits_zero():
    rc, out, _ = drive(ramp(), seconds=300)
    assert rc == 0 and out == ["S1T_SIDE_FEED_OK"]


@pytest.mark.parametrize("name", ["other.json", "tape-2026-11-01T00.jsonl", "arena-feed-status.json.bak", "x-arena-feed-status.json", "status.json"])
def test_status_path_refusal(tmp_path, capsys, name):
    p = tmp_path / name
    p.write_text("{}")
    assert aw.main(["--status", str(p), "--seconds", "0"]) == 2
    cap = capsys.readouterr()
    assert cap.out == "" and "refused" in cap.err


def test_main_reads_only_the_named_file(tmp_path, capsys):
    p = tmp_path / "arena-feed-status.json"
    p.write_text(json.dumps(st(0, 100, 40, base_ms=int(time.time() * 1000))))
    (tmp_path / "tape-2026-11-01T00.jsonl").write_text("DO NOT OPEN\n")
    assert aw.main(["--status", str(p), "--seconds", "0", "--interval-s", "0.01"]) == 0
    assert capsys.readouterr().out.strip() == "S1T_SIDE_FEED_OK"
    p.unlink()
    assert aw.main(["--status", str(p), "--seconds", "0", "--interval-s", "0.01"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "UNREADABLE"


def test_ten_consecutive_stale_or_unreadable_checks_are_blind():
    w = aw.Watcher()
    for i in range(9):
        out = step(w, i, 100, 40, stale_s=301) if i % 2 == 0 else w.step(None, i * 60.0, 0)
        assert len(out) == 1 and out[0] in ("STALE", "UNREADABLE")  # nine bad checks, mixed: no BLIND yet
    out = step(w, 9, 100, 40, stale_s=301)
    assert out == ["STALE", "S1T_SIDE_FEED_BLIND reason=unreadable_or_stale consecutive_checks=10"]


def test_a_good_check_resets_the_blind_count():
    w = aw.Watcher()
    for i in range(9):
        step(w, i, 100, 40, stale_s=301)
    assert step(w, 9, 100, 40) == []  # fresh
    for i in range(10, 19):
        assert not any("BLIND" in x for x in step(w, i, 100, 40, stale_s=301))  # nine more bad checks: still not blind
    assert any("BLIND" in x for x in step(w, 19, 100, 40, stale_s=301))


def test_three_rebaselines_inside_60_minutes_are_blind():
    w = aw.Watcher()
    step(w, 0, 100, 40, pid=1)
    assert step(w, 1, 100, 40, pid=2) == ["S1T_SIDE_FEED_REBASELINE"]
    assert step(w, 2, 100, 40, pid=3) == ["S1T_SIDE_FEED_REBASELINE"]
    out = step(w, 3, 100, 40, pid=4)
    assert out == ["S1T_SIDE_FEED_REBASELINE", "S1T_SIDE_FEED_BLIND reason=rebaselines count=3 window_s=3600"]


def test_rebaselines_spread_over_more_than_60_minutes_are_not_blind():
    w = aw.Watcher()
    step(w, 0, 100, 40, pid=1)
    for i, pid in ((100, 2), (200, 3), (300, 4)):  # 100 minutes apart
        assert step(w, i, 100, 40, pid=pid) == ["S1T_SIDE_FEED_REBASELINE"]
    w2 = aw.Watcher()  # two inside the window are not enough
    step(w2, 0, 100, 40, pid=1)
    step(w2, 1, 100, 40, pid=2)
    assert not any("BLIND" in x for x in step(w2, 2, 100, 40, pid=3))


def test_counter_resets_count_as_rebaselines_too():
    w = aw.Watcher()
    step(w, 0, 100, 40)
    step(w, 1, 50, 40)
    step(w, 2, 20, 40)
    assert any("BLIND" in x for x in step(w, 3, 5, 40))


def test_run_watch_exits_5_when_blind():
    clock = {"t": 0.0}

    def sleep(s):
        clock["t"] += s

    out = []
    rc = aw.run_watch(lambda: None, None, 60.0, clock=lambda: clock["t"], sleep=sleep, wall_ms=lambda: 0, emit=out.append)
    assert rc == aw.EXIT_BLIND == 5 and out[-1].startswith("S1T_SIDE_FEED_BLIND") and out.count("UNREADABLE") == 10
    pids = iter([1, 2, 3, 4, 5])
    n = {"i": 0}

    def read():
        n["i"] += 1
        return st(n["i"], 100, 40, pid=next(pids))

    out2 = []
    rc = aw.run_watch(read, None, 60.0, clock=lambda: clock["t"], sleep=sleep, wall_ms=lambda: BASE_MS + n["i"] * 60_000, emit=out2.append)
    assert rc == 5 and out2[-1].startswith("S1T_SIDE_FEED_BLIND reason=rebaselines")


def test_source_never_lists_a_directory_or_names_a_tape_file():
    src = Path(aw.__file__).read_text()
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r"\b(listdir|scandir|glob|iterdir|walk|rglob)\b", code.split('"""', 2)[2])
