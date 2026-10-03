"""Downtime derivation from heartbeat samples. Synthetic fixtures only."""
from __future__ import annotations

import json
from pathlib import Path

from tools.exp012_runner_downtime import derive, is_fresh, load_samples
from tools.forward_exp012_replay import load_downtime

T0 = 1_800_000_000_000  # minute-aligned
M = 60_000


def s(t, *, pid=1, ok=True, age=1_000):
    return {"sampled_ms": t, "status_ts_ms": (t - age) if ok else None, "lag_ms": 1, "pid": pid, "ok": ok}


def steady(start, end, **kw):
    return [s(t, **kw) for t in range(start, end, 15_000)]


def test_freshness_rule():
    assert is_fresh(s(T0, age=59_999))
    assert not is_fresh(s(T0, age=60_000))
    assert not is_fresh(s(T0, ok=False))


def test_all_up_after_settle():
    rows = steady(T0, T0 + 30 * M)
    merged, summ = derive(rows, T0 + 10 * M, T0 + 30 * M)
    assert merged == [] and summ["down_minutes"] == 0 and summ["restarts"] == 0
    assert summ["sampler_coverage"] == 1.0


def test_initial_start_excluded_ten_minutes():
    merged, summ = derive(steady(T0, T0 + 30 * M), T0, T0 + 30 * M)
    assert merged == [(T0, T0 + 10 * M)]
    assert summ["restarts"] == 0 and summ["down_minutes"] == 10


def test_stale_status_minutes_are_down():
    rows = steady(T0, T0 + 12 * M) + steady(T0 + 12 * M, T0 + 15 * M, age=120_000) + steady(T0 + 15 * M, T0 + 40 * M)
    merged, summ = derive(rows, T0 + 11 * M, T0 + 40 * M)
    assert (T0 + 12 * M, T0 + 15 * M) in merged
    assert summ["restarts"] == 0  # stale but ok is not a restart


def test_restart_by_pid_change():
    a = steady(T0, T0 + 20 * M, pid=1)
    b = steady(T0 + 20 * M + 30_000, T0 + 60 * M, pid=2)
    merged, summ = derive(a + b, T0 + 11 * M, T0 + 60 * M)
    last_old = a[-1]["sampled_ms"]
    first_new = b[0]["sampled_ms"]
    assert summ["restarts"] == 1
    assert merged == [(last_old, first_new + 10 * M)]


def test_restart_by_ok_false_gap_same_pid():
    a = steady(T0, T0 + 20 * M)
    bad = [s(t, ok=False) for t in range(T0 + 20 * M, T0 + 22 * M, 15_000)]
    b = steady(T0 + 22 * M, T0 + 60 * M)
    merged, summ = derive(a + bad + b, T0 + 11 * M, T0 + 60 * M)
    assert summ["restarts"] == 1
    assert merged == [(a[-1]["sampled_ms"], b[0]["sampled_ms"] + 10 * M)]


def test_open_ok_false_run_at_end_is_down_to_window_end():
    a = steady(T0, T0 + 20 * M)
    bad = [s(t, ok=False) for t in range(T0 + 20 * M, T0 + 25 * M, 15_000)]
    merged, summ = derive(a + bad, T0 + 11 * M, T0 + 25 * M)
    assert merged == [(a[-1]["sampled_ms"], T0 + 25 * M)]
    assert summ["restarts"] == 1


def test_sampler_gap_counts_down_even_with_a_late_sample():
    # samples at 0:30 then 1:31 of minute grid: gap 61 s, minute 1 has a fresh sample at its end
    base = steady(T0, T0 + 20 * M)
    base = [r for r in base if not (T0 + 12 * M + 30_000 < r["sampled_ms"] < T0 + 13 * M + 31_000)]
    base.append(s(T0 + 13 * M + 31_000))
    base.sort(key=lambda r: r["sampled_ms"])
    merged, summ = derive(base, T0 + 11 * M, T0 + 20 * M)
    assert summ["sampler_gaps_over_60s"] == 1
    assert (T0 + 12 * M, T0 + 14 * M) in merged


def test_sampler_outage_minutes_down_and_coverage():
    rows = steady(T0, T0 + 20 * M) + steady(T0 + 25 * M, T0 + 50 * M)
    merged, summ = derive(rows, T0 + 11 * M, T0 + 50 * M)
    assert any(a <= T0 + 21 * M and b >= T0 + 24 * M for a, b in merged)
    assert summ["restarts"] == 0
    assert summ["sampler_coverage"] < 1.0


def test_output_round_trips_into_replay_loader(tmp_path):
    from tools.exp012_runner_downtime import main

    hb = tmp_path / "heartbeat.jsonl"
    rows = steady(T0, T0 + 20 * M, pid=1) + steady(T0 + 21 * M, T0 + 40 * M, pid=2)
    hb.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    out = tmp_path / "downtime.json"
    assert main(["--heartbeat", str(hb), "--from", str(T0), "--to", str(T0 + 40 * M), "--out", str(out)]) == 0
    loaded = load_downtime(Path(out))
    merged, _ = derive(load_samples(hb), T0, T0 + 40 * M)
    assert loaded == merged and loaded
    assert all(isinstance(a, int) and isinstance(b, int) and a < b for a, b in loaded)
