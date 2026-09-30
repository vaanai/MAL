"""Tests for tools/mal_status.py and tools/mal_credit_log.py.

No `ssh` is invoked: the remote-core path is tested by monkeypatching
`subprocess.run` with canned stdout, exactly as the task requires.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tools import mal_credit_log, mal_status


# --- memory / cgroup ---------------------------------------------------------


def _write_cgroup_fixture(tmp_path: Path, *, anon_bytes: int, current_bytes: int, max_value: str) -> Path:
    cg_dir = tmp_path / "cgroup"
    cg_dir.mkdir()
    (cg_dir / "memory.stat").write_text(
        f"anon {anon_bytes}\nfile {anon_bytes * 3}\nkernel 1000\n", encoding="utf-8"
    )
    (cg_dir / "memory.current").write_text(f"{current_bytes}\n", encoding="utf-8")
    (cg_dir / "memory.max").write_text(f"{max_value}\n", encoding="utf-8")
    return cg_dir


def test_cgroup_memory_uses_anon_not_file(tmp_path):
    cg_dir = _write_cgroup_fixture(tmp_path, anon_bytes=100 * 1024 * 1024, current_bytes=700 * 1024 * 1024, max_value="max")
    result = mal_status.read_cgroup_memory("claude", str(cg_dir))
    assert result["anon_mb"] == 100.0
    # current_mb reflects cgroup current (which includes cache), anon_mb must not.
    assert result["current_mb"] == 700.0
    assert result["anon_mb"] != result["current_mb"]


def test_cgroup_memory_max_literal_becomes_none(tmp_path):
    cg_dir = _write_cgroup_fixture(tmp_path, anon_bytes=1024 * 1024, current_bytes=2 * 1024 * 1024, max_value="max")
    result = mal_status.read_cgroup_memory("claude", str(cg_dir))
    assert result["max_mb"] is None


def test_cgroup_memory_max_numeric(tmp_path):
    sixteen_gb = 16 * 1024 * 1024 * 1024
    cg_dir = _write_cgroup_fixture(tmp_path, anon_bytes=1024 * 1024, current_bytes=2 * 1024 * 1024, max_value=str(sixteen_gb))
    result = mal_status.read_cgroup_memory("claude", str(cg_dir))
    assert result["max_mb"] == 16384.0


def test_collect_memory_records_error_not_crash(tmp_path):
    errors: list[str] = []
    missing_dir = str(tmp_path / "does-not-exist")
    out = mal_status.collect_memory([{"name": "claude", "path": missing_dir}], errors)
    assert len(errors) == 1
    assert out["cgroups"][0]["error"]


# --- walkers / checkpoints ---------------------------------------------------


def _write_checkpoint(tmp_path: Path, name: str, hours: dict, credits_used: int) -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "checkpoint.json").write_text(
        json.dumps({"version": 1, "credits_used": credits_used, "credits_per_getblock": None, "hours": hours}),
        encoding="utf-8",
    )
    return d


def test_walker_checkpoint_counts_and_bounds(tmp_path):
    hours = {
        "2026-09-18T03": {"status": "sealed", "stop_reason": None},
        "2026-09-18T02": {"status": "sealed", "stop_reason": None},
        "2026-09-18T01": {"status": "partial", "stop_reason": None},
    }
    d = _write_checkpoint(tmp_path, "walker_1", hours, credits_used=1_307_207)
    result = mal_status.read_walker_checkpoint("walker_1", str(d))
    assert result["sealed"] == 2
    assert result["total_hours_in_checkpoint"] == 3
    assert result["oldest_sealed"] == "2026-09-18T02"
    assert result["newest_sealed"] == "2026-09-18T03"
    assert result["credits_used"] == 1_307_207
    assert "stop_reason" not in result


def test_walker_checkpoint_captures_stop_reason_if_present(tmp_path):
    hours = {
        "2026-09-18T03": {"status": "sealed", "stop_reason": None},
        "2026-09-18T04": {"status": "stopped", "stop_reason": "credit_cap_reached"},
    }
    d = _write_checkpoint(tmp_path, "walker_1", hours, credits_used=100)
    result = mal_status.read_walker_checkpoint("walker_1", str(d))
    assert result["stop_reason"] == "credit_cap_reached"


def test_walker_checkpoint_empty_hours(tmp_path):
    d = _write_checkpoint(tmp_path, "walker_x", {}, credits_used=0)
    result = mal_status.read_walker_checkpoint("walker_x", str(d))
    assert result["sealed"] == 0
    assert result["oldest_sealed"] is None
    assert result["newest_sealed"] is None


def test_missing_checkpoint_is_recorded_not_crash(tmp_path):
    errors: list[str] = []
    walkers = [{"name": "walker_missing", "checkpoint_dir": str(tmp_path / "nope")}]
    result = mal_status.collect_walkers(walkers, errors)
    assert len(errors) == 1
    assert result[0]["name"] == "walker_missing"
    assert "error" in result[0]


# --- services / systemctl ----------------------------------------------------


def test_query_service_state_timeout_is_unknown(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 5))

    monkeypatch.setattr(mal_status.subprocess, "run", fake_run)
    state = mal_status.query_service_state("mal-fast-create", "ubuntu")
    assert state == "unknown"


def test_query_service_state_missing_binary_is_unknown(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("no such file: sudo")

    monkeypatch.setattr(mal_status.subprocess, "run", fake_run)
    state = mal_status.query_service_state("mal-daily-review.timer", "claude")
    assert state == "unknown"


def test_query_service_state_reads_stdout_even_on_nonzero_exit(monkeypatch):
    class FakeProc:
        returncode = 3
        stdout = "inactive\n"
        stderr = ""

    def fake_run(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(mal_status.subprocess, "run", fake_run)
    state = mal_status.query_service_state("mal-fast-oos-score", "ubuntu")
    assert state == "inactive"


def test_collect_services_never_crashes_on_exception(monkeypatch):
    def fake_query(name, owner, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mal_status, "query_service_state", fake_query)
    errors: list[str] = []
    result = mal_status.collect_services([{"name": "svc", "owner": "claude"}], errors)
    assert result[0]["state"] == "unknown"
    assert len(errors) == 1


# --- pre-create credits -------------------------------------------------------


def test_pre_create_credits_normal_file(tmp_path):
    path = tmp_path / "pre-create-credits.json"
    path.write_text(json.dumps({"utc_date": "2026-09-30", "credits": 3147.5, "tripped": False, "reason": "", "cap": 10000.0}), encoding="utf-8")
    data = mal_status.read_pre_create_credits(str(path))
    assert data["credits"] == 3147.5
    assert data["cap"] == 10000.0


def test_pre_create_credits_skips_key_like_file(tmp_path):
    path = tmp_path / "pre-create-credits.json"
    path.write_text(json.dumps({"api_key": "shh", "credits": 1}), encoding="utf-8")
    data = mal_status.read_pre_create_credits(str(path))
    assert data is None


def test_pre_create_credits_missing_file(tmp_path):
    data = mal_status.read_pre_create_credits(str(tmp_path / "nope.json"))
    assert data is None


def test_pre_create_credits_permission_error_falls_back_to_sudo(tmp_path, monkeypatch):
    path = tmp_path / "pre-create-credits.json"
    path.write_text("irrelevant", encoding="utf-8")
    real_open = open

    def fake_builtin_open(p, *args, **kwargs):
        if str(p) == str(path):
            raise PermissionError("denied")
        return real_open(p, *args, **kwargs)

    class FakeProc:
        returncode = 0
        stdout = json.dumps({"credits": 42, "cap": 100})
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        assert cmd[:3] == ["sudo", "-n", "cat"]
        return FakeProc()

    monkeypatch.setattr("builtins.open", fake_builtin_open)
    monkeypatch.setattr(mal_status.subprocess, "run", fake_run)
    data = mal_status.read_pre_create_credits(str(path))
    assert data == {"credits": 42, "cap": 100}


# --- atomic write -------------------------------------------------------------


def test_write_status_atomic_creates_file(tmp_path):
    out_dir = tmp_path / "status"
    result_path = mal_status.write_status_atomic(str(out_dir), "mal-fast-0", {"a": 1})
    assert os.path.exists(result_path)
    assert json.loads(Path(result_path).read_text()) == {"a": 1}
    # no leftover tmp files
    leftovers = [p for p in out_dir.iterdir() if p.name != "mal-fast-0.json"]
    assert leftovers == []


def test_write_status_atomic_overwrites_cleanly(tmp_path):
    out_dir = tmp_path / "status"
    mal_status.write_status_atomic(str(out_dir), "mal-fast-0", {"a": 1})
    mal_status.write_status_atomic(str(out_dir), "mal-fast-0", {"a": 2})
    final = out_dir / "mal-fast-0.json"
    assert json.loads(final.read_text()) == {"a": 2}


# --- remote (Oracle) collection ----------------------------------------------


def test_collect_remote_core_status_parses_canned_stdout(monkeypatch):
    canned = {
        "load": [0.5, 0.4, 0.3],
        "memory": {"mem_total_mb": 24000.0, "mem_available_mb": 12000.0},
        "disk_var_lib_mal": {"mount": "/var/lib/mal", "total_gb": 100.0, "used_gb": 40.0, "free_gb": 60.0, "pct": 40.0},
        "runner_status": {"lag_ms": 12, "stale_cap_ms": 5000, "fail_rate": 0.0, "ts": "2026-09-30T12:00:00Z"},
        "health_latest": {"status": "ok"},
        "processes": {"mal-forward-paper": True, "mal-observe": True, "mal-trade-tape": True, "mal-attention": True, "mal-funding-graph": False},
    }

    class FakeProc:
        returncode = 0
        stdout = json.dumps(canned)
        stderr = ""

    def fake_run(cmd, **kwargs):
        assert cmd[0] == "ssh"
        assert "mal-core-0" in cmd
        assert kwargs.get("input")  # the embedded snippet was piped on stdin
        return FakeProc()

    monkeypatch.setattr(mal_status.subprocess, "run", fake_run)
    status = mal_status.collect_remote_core_status()
    assert status["reachable"] is True
    assert status["load1"] == 0.5
    assert status["runner_status"]["lag_ms"] == 12
    assert status["processes"]["mal-forward-paper"] is True
    assert status["disk"][0]["mount"] == "/var/lib/mal"


def test_collect_remote_core_status_ssh_failure_never_crashes(monkeypatch):
    class FakeProc:
        returncode = 255
        stdout = ""
        stderr = "ssh: connect to host mal-core-0 port 22: Connection refused"

    def fake_run(cmd, **kwargs):
        return FakeProc()

    monkeypatch.setattr(mal_status.subprocess, "run", fake_run)
    status = mal_status.collect_remote_core_status()
    assert status["reachable"] is False
    assert "error" in status


def test_collect_remote_core_status_timeout_never_crashes(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 20))

    monkeypatch.setattr(mal_status.subprocess, "run", fake_run)
    status = mal_status.collect_remote_core_status()
    assert status["reachable"] is False


def test_collect_remote_core_status_bad_json_never_crashes(monkeypatch):
    class FakeProc:
        returncode = 0
        stdout = "not json{{"
        stderr = ""

    def fake_run(cmd, **kwargs):
        return FakeProc()

    monkeypatch.setattr(mal_status.subprocess, "run", fake_run)
    status = mal_status.collect_remote_core_status()
    assert status["reachable"] is False


# --- last runner restart -------------------------------------------------------


def test_read_last_runner_restart(tmp_path):
    path = tmp_path / "runner-restarts.jsonl"
    lines = [
        {"restart_utc": "2026-09-29T00:00:00Z", "pre": {"head_sha": "abc"}, "post": {"lag_ms": 9}, "ok": True},
        {"restart_utc": "2026-09-30T00:00:00Z", "pre": {"head_sha": "def"}, "post": {"lag_ms": 16}, "ok": True},
    ]
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    result = mal_status.read_last_runner_restart(str(path))
    assert result["restart_utc"] == "2026-09-30T00:00:00Z"
    assert result["head_sha"] == "def"
    assert result["post_lag_ms"] == 16
    assert result["ok"] is True


# --- credit log ---------------------------------------------------------------


def test_credit_log_first_write_has_null_delta(tmp_path):
    log_path = str(tmp_path / "credits.jsonl")
    snaps = [{"job": "walker_1", "credits_used": 1000, "cap": None}]
    written = mal_credit_log.append(log_path, snaps, now_utc="2026-09-30T00:00:00Z")
    assert len(written) == 1
    assert written[0]["delta_since_last"] is None
    assert written[0]["credits_used"] == 1000


def test_credit_log_delta_on_change(tmp_path):
    log_path = str(tmp_path / "credits.jsonl")
    mal_credit_log.append(log_path, [{"job": "walker_1", "credits_used": 1000, "cap": None}], now_utc="2026-09-30T00:00:00Z")
    written = mal_credit_log.append(log_path, [{"job": "walker_1", "credits_used": 1500, "cap": None}], now_utc="2026-09-30T00:05:00Z")
    assert len(written) == 1
    assert written[0]["delta_since_last"] == 500


def test_credit_log_no_duplicate_when_unchanged_and_recent(tmp_path):
    log_path = str(tmp_path / "credits.jsonl")
    mal_credit_log.append(log_path, [{"job": "walker_1", "credits_used": 1000, "cap": None}], now_utc="2026-09-30T00:00:00Z")
    # Same value, only 1 minute later: should not append.
    written = mal_credit_log.append(log_path, [{"job": "walker_1", "credits_used": 1000, "cap": None}], now_utc="2026-09-30T00:01:00Z")
    assert written == []
    with open(log_path, encoding="utf-8") as fh:
        lines = [l for l in fh if l.strip()]
    assert len(lines) == 1


def test_credit_log_writes_when_unchanged_but_stale(tmp_path):
    log_path = str(tmp_path / "credits.jsonl")
    mal_credit_log.append(log_path, [{"job": "walker_1", "credits_used": 1000, "cap": None}], now_utc="2026-09-30T00:00:00Z")
    # Same value, more than 1h later: should append (heartbeat).
    written = mal_credit_log.append(log_path, [{"job": "walker_1", "credits_used": 1000, "cap": None}], now_utc="2026-09-30T01:00:01Z")
    assert len(written) == 1
    assert written[0]["delta_since_last"] == 0


def test_credit_log_multiple_jobs_independent(tmp_path):
    log_path = str(tmp_path / "credits.jsonl")
    mal_credit_log.append(
        log_path,
        [
            {"job": "walker_1", "credits_used": 1000, "cap": None},
            {"job": "walker_b", "credits_used": 500, "cap": None},
        ],
        now_utc="2026-09-30T00:00:00Z",
    )
    written = mal_credit_log.append(
        log_path,
        [
            {"job": "walker_1", "credits_used": 1000, "cap": None},  # unchanged, recent -> skip
            {"job": "walker_b", "credits_used": 900, "cap": None},  # changed -> write
        ],
        now_utc="2026-09-30T00:02:00Z",
    )
    assert len(written) == 1
    assert written[0]["job"] == "walker_b"
    assert written[0]["delta_since_last"] == 400


def test_snapshot_never_raises_on_missing_files(tmp_path):
    checkpoint_dirs = {"walker_1": str(tmp_path / "nope")}
    pre_create_path = str(tmp_path / "also-nope.json")
    result = mal_credit_log.snapshot(checkpoint_dirs, pre_create_path)
    by_job = {r["job"]: r for r in result}
    assert by_job["walker_1"]["credits_used"] is None
    assert by_job["pre_create"]["credits_used"] is None
