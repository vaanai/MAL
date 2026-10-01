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


# === per-unit "running now" view (units list) ===================================

from datetime import datetime, timezone  # noqa: E402

UNIT_KEYS = {
    "name", "owner_user", "scope", "kind", "active_state", "sub_state", "since", "uptime_s",
    "memory", "last_log", "last_log_ts", "last_log_age_s",
}
NOW = datetime(2026, 10, 1, 2, 0, 0, tzinfo=timezone.utc)
B58_44 = "HdBmmZf41x3ffQ4rX1EWVd4q9wwTvfAEkj3oobNpump"  # a pump.fun-style mint
HEX_40 = "a3f1c2d4e5b60718293a4b5c6d7e8f9012345678"


# --- scrub_log_line ----------------------------------------------------------


@pytest.mark.parametrize(
    "line, secret",
    [
        ("connect api-key=abc123SECRET ok", "abc123SECRET"),
        ("connect api_key=abc123SECRET ok", "abc123SECRET"),
        ("connect apikey=abc123SECRET ok", "abc123SECRET"),
        ("connect API_KEY: abc123SECRET ok", "abc123SECRET"),
        ("retry token=tok_9f8e7d6c ok", "tok_9f8e7d6c"),
        ("retry access_token=tok_9f8e7d6c ok", "tok_9f8e7d6c"),
        ("sent Bearer eyJhbGciOi.payload.sig now", "eyJhbGciOi"),
        ("hdr Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("GET https://mainnet.helius-rpc.com/?foo=bar&x=y failed", "foo=bar"),
        ("ws wss://atlas-mainnet.helius-rpc.com/?zzz=1 closed", "zzz=1"),
        (f"mint {B58_44} seen", B58_44),
        (f"hash {HEX_40} seen", HEX_40),
        ("key mck_live_abcDEF123 used", "abcDEF123"),
        ("key sk-ant-api03-ABCdef123456 used", "ABCdef123456"),
    ],
)
def test_scrub_redacts_each_pattern(line, secret):
    out = mal_status.scrub_log_line(line)
    assert secret not in out
    assert "[redacted]" in out


def test_scrub_helius_url_query_gone():
    out = mal_status.scrub_log_line("GET https://mainnet.helius-rpc.com/?api-key=SEKRET failed")
    assert "SEKRET" not in out
    assert "api-key" not in out


def test_scrub_truncates_to_200_plus_ellipsis():
    long_line = "lorem ipsum " * 50  # spaces keep it from looking like a long id
    out = mal_status.scrub_log_line(long_line)
    assert out == long_line.strip()[:200] + "…" and len(out) == 201
    exact = ("lorem ipsum " * 17)[:200]
    assert mal_status.scrub_log_line(exact) == exact.strip()  # at the limit: no ellipsis
    assert not mal_status.scrub_log_line("x" * 500).endswith("…")  # a bare 500-char run is an id: redacted first


def test_scrub_redacts_before_truncating():
    # A secret straddling the cut must not survive as a partial prefix.
    line = "lorem ipsum " * 15 + "token=" + "S" * 40  # token starts just before char 200
    out = mal_status.scrub_log_line(line)
    assert "SSS" not in out and "token=" not in out


def test_scrub_benign_line_unchanged():
    line = "2026-10-01 01:27:04,199 INFO mal.attention heartbeat written=4090 genuine=4089 snapshot=1"
    assert mal_status.scrub_log_line(line) == line


def test_scrub_short_hex_id_unchanged():
    line = "ingest_sealed id=deadbeef slot=412345678 path=/var/lib/mal/sealed/jsonl/observe-2026-10-01.jsonl"
    assert mal_status.scrub_log_line(line) == line


def test_scrub_none_and_idempotent():
    assert mal_status.scrub_log_line(None) is None
    once = mal_status.scrub_log_line(f"{B58_44} " + "z" * 400)
    assert mal_status.scrub_log_line(once) == once


# --- parsers -----------------------------------------------------------------

SHOW_FIXTURE = """\
ControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/mal-fast-create.service
Id=mal-fast-create.service
ActiveState=active
SubState=running
ActiveEnterTimestamp=Tue 2026-09-29 04:27:17 UTC

ControlGroup=
Id=mal-fast-backfill.service
ActiveState=inactive
SubState=dead
ActiveEnterTimestamp=Tue 2026-09-29 14:20:57 UTC

Id=mal-status.timer
ActiveState=active
SubState=waiting
ActiveEnterTimestamp=
ControlGroup=
"""

LIST_FIXTURE = """\
mal-fast-backfill.service loaded    inactive dead    MAL fast-box Helius backfill
mal-fast-create.service   loaded    active   running MAL phase-1 create alarm
mal-status.timer          loaded    active   waiting MAL local status file
mal-backup.slice          loaded    active   active  Slice for MAL backup jobs
not-mal.service           loaded    active   running other
"""


def test_parse_systemctl_show_fixture():
    props = mal_status.parse_systemctl_show(SHOW_FIXTURE)
    assert set(props) == {"mal-fast-create.service", "mal-fast-backfill.service", "mal-status.timer"}
    create = props["mal-fast-create.service"]
    assert create["ActiveState"] == "active"
    assert create["SubState"] == "running"
    assert create["ControlGroup"].endswith("/mal-fast-create.service")
    assert props["mal-status.timer"]["ActiveEnterTimestamp"] == ""


def test_parse_list_units_keeps_only_mal_services_and_timers():
    assert mal_status.parse_list_units(LIST_FIXTURE) == [
        "mal-fast-backfill.service", "mal-fast-create.service", "mal-status.timer",
    ]


def test_parse_systemd_timestamp():
    assert mal_status.parse_systemd_timestamp("Tue 2026-09-29 04:27:17 UTC") == datetime(2026, 9, 29, 4, 27, 17, tzinfo=timezone.utc)
    assert mal_status.parse_systemd_timestamp("") is None
    assert mal_status.parse_systemd_timestamp("Tue 2026-09-29 04:27:17 CEST") is None  # never guess a zone
    assert mal_status.parse_systemd_timestamp("garbage in here now") is None


def test_parse_journal_line():
    msg, ts = mal_status.parse_journal_line("2026-09-29T04:27:17+00:00 mal-fast-0 systemd[965]: Started mal-fast-create.service - MAL.")
    assert msg == "Started mal-fast-create.service - MAL."
    assert ts == datetime(2026, 9, 29, 4, 27, 17, tzinfo=timezone.utc)
    assert mal_status.parse_journal_line("-- No entries --") == (None, None)
    assert mal_status.parse_journal_line("") == (None, None)


# --- cgroup memory -----------------------------------------------------------


def test_unit_cgroup_memory_read_from_tmp_file(tmp_path):
    cg = tmp_path / "user.slice" / "x.service"
    cg.mkdir(parents=True)
    (cg / "memory.current").write_text("55242752\n", encoding="utf-8")
    mem = mal_status.read_unit_cgroup_memory("/user.slice/x.service", str(tmp_path))
    assert mem == {"bytes": 55242752, "source": "cgroup memory.current (includes page cache)"}


def test_unit_cgroup_memory_missing_is_null(tmp_path):
    assert mal_status.read_unit_cgroup_memory("/nope/x.service", str(tmp_path)) is None
    assert mal_status.read_unit_cgroup_memory("", str(tmp_path)) is None
    assert mal_status.read_unit_cgroup_memory(None, str(tmp_path)) is None


def test_unit_cgroup_memory_garbage_or_traversal_is_null(tmp_path):
    cg = tmp_path / "x.service"
    cg.mkdir()
    (cg / "memory.current").write_text("max\n", encoding="utf-8")
    assert mal_status.read_unit_cgroup_memory("/x.service", str(tmp_path)) is None
    assert mal_status.read_unit_cgroup_memory("/../etc", str(tmp_path)) is None


# --- collect_units (mocked subprocess) ---------------------------------------


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


def _fake_systemd(calls):
    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        argv = [c for c in cmd if c not in ("sudo", "-n")]
        if "list-units" in argv:
            return _Proc(LIST_FIXTURE)
        if "show" in argv:
            return _Proc(SHOW_FIXTURE)
        if "journalctl" in argv:
            unit = argv[argv.index("-u") + 1]
            return _Proc(f"2026-10-01T01:59:30+00:00 mal-fast-0 py[1]: {unit} api-key=SEKRET {B58_44}\n")
        raise AssertionError(cmd)

    return fake_run


def test_collect_units_shape_scrub_and_call_budget(monkeypatch, tmp_path):
    cg = tmp_path / "user.slice/user-1000.slice/user@1000.service/app.slice/mal-fast-create.service"
    cg.mkdir(parents=True)
    (cg / "memory.current").write_text("1234\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(mal_status.subprocess, "run", _fake_systemd(calls))

    units, errors, meta = mal_status.collect_units(
        [{"scope": "system", "owner_user": "root", "prefix": []}, {"scope": "user", "owner_user": "claude", "prefix": []}],
        now=NOW, cgroup_root=str(tmp_path),
    )
    assert errors == []
    assert len(units) == 6  # 3 mal-* units per scope (slice and non-mal filtered out)
    # 2 sources x (1 list + 1 batched show) + 1 journalctl per unit
    assert meta["subprocesses"] == len(calls) == 2 * 2 + 6
    shows = [c for c in calls if "show" in c]
    assert len(shows) == 2 and all(c.count("mal-fast-create.service") == 1 for c in shows)  # batched, not per unit
    assert shows[0][shows[0].index("-p") + 1] == "Id,ActiveState,SubState,ActiveEnterTimestamp,ControlGroup"
    assert any(c[:2] == ["systemctl", "--user"] for c in calls)

    for u in units:
        assert set(u) >= UNIT_KEYS
        assert "SEKRET" not in u["last_log"] and B58_44 not in u["last_log"]
        assert u["last_log_ts"] == "2026-10-01T01:59:30Z"
        assert u["last_log_age_s"] == 30
    create = next(u for u in units if u["name"] == "mal-fast-create.service" and u["scope"] == "user")
    assert (create["kind"], create["active_state"], create["sub_state"]) == ("service", "active", "running")
    assert create["since"] == "2026-09-29T04:27:17Z"
    assert create["uptime_s"] == int((NOW - datetime(2026, 9, 29, 4, 27, 17, tzinfo=timezone.utc)).total_seconds())
    assert create["memory"] == {"bytes": 1234, "source": "cgroup memory.current (includes page cache)"}
    dead = next(u for u in units if u["name"] == "mal-fast-backfill.service")
    assert dead["uptime_s"] is None and dead["memory"] is None and dead["since"] == "2026-09-29T14:20:57Z"
    timer = next(u for u in units if u["name"] == "mal-status.timer")
    assert timer["kind"] == "timer" and timer["since"] is None and timer["uptime_s"] is None


def test_collect_units_sudo_prefix_applies_to_every_call(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(mal_status.subprocess, "run", _fake_systemd(calls))
    prefix = ["sudo", "-n", "-u", "ubuntu", "XDG_RUNTIME_DIR=/run/user/1000"]
    units, _, meta = mal_status.collect_units([{"scope": "user", "owner_user": "ubuntu", "prefix": prefix}], now=NOW)
    assert all(c[:5] == prefix for c in calls)
    assert {u["owner_user"] for u in units} == {"ubuntu"}
    assert meta["subprocesses"] == 2 + 3


def test_collect_units_subprocess_failures_degrade_to_null(monkeypatch):
    def fake_run(cmd, **kwargs):
        if "list-units" in cmd:
            return _Proc(LIST_FIXTURE)
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    monkeypatch.setattr(mal_status.subprocess, "run", fake_run)
    units, errors, _ = mal_status.collect_units([{"scope": "user", "owner_user": "claude", "prefix": []}], now=NOW)
    assert len(units) == 3
    assert any("show" in e for e in errors)
    for u in units:
        assert set(u) >= UNIT_KEYS
        assert u["active_state"] is None and u["memory"] is None and u["last_log"] is None


def test_collect_units_list_failure_is_recorded_not_raised(monkeypatch):
    monkeypatch.setattr(mal_status.subprocess, "run", lambda cmd, **kw: _Proc("", returncode=1))
    units, errors, meta = mal_status.collect_units([{"scope": "system", "owner_user": "root", "prefix": []}], now=NOW)
    assert units == [] and len(errors) == 1 and meta["subprocesses"] == 1


def test_collect_local_status_carries_units(monkeypatch, tmp_path):
    fake_units = [mal_status.empty_unit("mal-x.service", "user", "service", "claude")]
    monkeypatch.setattr(mal_status, "collect_units", lambda sources=None: (fake_units, ["units boom"], {"wall_ms": 1, "subprocesses": 3}))
    status = mal_status.collect_local_status(
        host="h", cgroups=[], mounts=[], services=[], walkers=[],
        pre_create_credits_path=str(tmp_path / "nope"), restart_log_path=str(tmp_path / "nope2"),
    )
    assert status["units"] == fake_units
    assert status["units_meta"] == {"wall_ms": 1, "subprocesses": 3}
    assert "units boom" in status["errors"]


# --- Oracle /proc parsing (fake /proc tree) ----------------------------------


def _remote_ns():
    ns: dict = {}
    exec(mal_status._REMOTE_LIB, ns)  # noqa: S102 - the embedded snippet, run in-process
    return ns


def _fake_proc(root: Path, pid: int, cmdline: str, start_ticks: int, rss_kb: int | None) -> None:
    d = root / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(cmdline.replace(" ", "\x00").encode())
    # comm contains a space and parens on purpose: parsing must split after the last ")".
    fields = ["S", "1", str(pid), "0", "0", "-1", "4194560", "1", "0", "0", "0", "10", "5", "0", "0", "20", "0", "1", "0"]
    (d / "stat").write_text(f"{pid} (my (py) proc) " + " ".join(fields) + f" {start_ticks} 0 0\n", encoding="utf-8")
    status = f"Name:\tpython3\nVmRSS:\t{rss_kb} kB\n" if rss_kb is not None else "Name:\tpython3\n"
    (d / "status").write_text(status, encoding="utf-8")


def test_oracle_proc_parsing_uptime_memory_and_log_tail(tmp_path):
    ns = _remote_ns()
    root = tmp_path / "proc"
    root.mkdir()
    (root / "stat").write_text("cpu  1 2 3\nbtime 1790000000\n", encoding="utf-8")
    _fake_proc(root, 101, "python3 -m observe.attention --x", start_ticks=500_000, rss_kb=100_000)
    _fake_proc(root, 102, "bash forward-paper.sh", start_ticks=300_000, rss_kb=2_000)
    _fake_proc(root, 103, "python3 -m tools.forward_paper serve", start_ticks=300_100, rss_kb=700_000)
    _fake_proc(root, 104, "sleep 1000", start_ticks=1, rss_kb=5)  # matches nothing
    (root / "self").mkdir()  # non-numeric entry is skipped

    log = tmp_path / "attention.log"
    log.write_text("old line\n" * 2000 + "2026-10-01 01:30:34,830 INFO mal.attention heartbeat token=SEKRET99\n\n", encoding="utf-8")
    now = 1790000000 + 500_000 / 100 + 3600  # one hour after the attention start

    pids = ns["scan_procs"](ns["MATCHERS"], str(root))
    assert pids["mal-attention"] == [101]
    assert sorted(pids["mal-forward-paper"]) == [102, 103]
    assert pids["mal-observe"] == []
    assert ns["proc_running"](ns["MATCHERS"], str(root))["mal-attention"] is True

    views = {v["name"]: v for v in ns["unit_views"](pids, {"mal-attention": str(log)}, root=str(root), now=now, hz=100)}
    att = views["mal-attention.service"]
    assert set(att) == UNIT_KEYS
    assert (att["active_state"], att["sub_state"]) == ("active", "running")
    assert att["uptime_s"] == 3600
    assert att["since"] == datetime.fromtimestamp(1790000000 + 5000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert att["memory"] == {"bytes": 100_000 * 1024, "source": mal_status.MEM_SOURCE_RSS}
    assert att["last_log"].endswith("token=SEKRET99")  # raw here; scrubbed by the parent
    assert att["last_log_age_s"] >= 0

    fwd = views["mal-forward-paper.service"]
    assert fwd["memory"]["bytes"] == (2_000 + 700_000) * 1024  # summed over matched pids
    assert fwd["uptime_s"] == int(now - (1790000000 + 300_000 / 100))
    assert fwd["last_log"] is None  # no log file configured -> null

    obs = views["mal-observe.service"]
    assert (obs["active_state"], obs["sub_state"]) == ("inactive", "dead")
    assert obs["since"] is None and obs["uptime_s"] is None and obs["memory"] is None


def test_oracle_missing_pieces_are_null_not_errors(tmp_path):
    ns = _remote_ns()
    root = tmp_path / "proc"
    root.mkdir()  # no /proc/stat: btime unreadable
    _fake_proc(root, 7, "python3 -m observe.trade_tape", start_ticks=1, rss_kb=None)
    pids = ns["scan_procs"](ns["MATCHERS"], str(root))
    views = {v["name"]: v for v in ns["unit_views"](pids, {"mal-trade-tape": str(tmp_path / "missing.log")}, root=str(root), now=1.0, hz=100)}
    tape = views["mal-trade-tape.service"]
    assert tape["active_state"] == "active"
    assert tape["since"] is None and tape["uptime_s"] is None and tape["memory"] is None
    assert tape["last_log"] is None and tape["last_log_ts"] is None


def test_oracle_tail_reads_only_last_4kb(tmp_path):
    ns = _remote_ns()
    path = tmp_path / "big.log"
    path.write_bytes(b"A" * 1_000_000 + b"\nlast good line\n")
    line, mtime = ns["tail_last_line"](str(path))
    assert line == "last good line" and mtime > 0
    assert ns["tail_last_line"](str(tmp_path / "nope.log")) == (None, None)


def test_collect_remote_core_status_units_normalised_and_scrubbed(monkeypatch):
    canned = {
        "load": [0.1, 0.1, 0.1],
        "processes": {"mal-attention": True},
        "units": [{"name": "mal-attention.service", "scope": "user", "kind": "service", "active_state": "active",
                   "last_log": "poll api_key=SEKRET " + "w" * 400}],
    }

    class FakeProc:
        returncode = 0
        stdout = json.dumps(canned)
        stderr = ""

    monkeypatch.setattr(mal_status.subprocess, "run", lambda cmd, **kw: FakeProc())
    status = mal_status.collect_remote_core_status()
    unit = status["units"][0]
    assert set(unit) == UNIT_KEYS  # every key present, null when the remote did not send it
    assert unit["memory"] is None and unit["since"] is None
    assert "SEKRET" not in unit["last_log"] and len(unit["last_log"]) <= 201


# --- mal-research-0 (--remote research) ---------------------------------------


def test_collect_remote_research_pipes_this_file_and_scrubs(monkeypatch):
    canned = {
        "uptime_s": 5.0, "load": [1.0, 2.0, 3.0], "memory": {"mem_total_mb": 1.0, "mem_available_mb": 1.0},
        "units": [{"name": "mal-walker-w1.service", "owner_user": "claude", "scope": "user", "kind": "service",
                   "active_state": "active", "sub_state": "running", "since": "2026-10-01T00:53:55Z", "uptime_s": 10,
                   "memory": {"bytes": 1, "source": "cgroup memory.current (includes page cache)"},
                   "last_log": f"progress {B58_44}", "last_log_ts": "2026-10-01T01:00:00Z", "last_log_age_s": 3}],
        "units_meta": {"wall_ms": 50, "subprocesses": 11}, "errors": [],
    }
    seen = {}

    class FakeProc:
        returncode = 0
        stdout = json.dumps(canned)
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["input"] = cmd, kwargs.get("input")
        return FakeProc()

    monkeypatch.setattr(mal_status.subprocess, "run", fake_run)
    status = mal_status.collect_remote_research_status()
    assert seen["cmd"][0] == "ssh" and "mal-research-0" in seen["cmd"]
    assert "--units-json" in seen["cmd"] and seen["cmd"][seen["cmd"].index("mal-research-0") + 1:][:2] == ["python3", "-"]
    assert "def collect_units" in seen["input"] and "def scrub_log_line" in seen["input"]  # same collector code
    assert status["reachable"] is True and status["load1"] == 1.0
    assert B58_44 not in status["units"][0]["last_log"]
    assert status["units"][0]["name"] == "mal-walker-w1.service"


@pytest.mark.parametrize("failure", ["exit", "timeout", "badjson"])
def test_collect_remote_research_failures_never_crash(monkeypatch, failure):
    class FakeProc:
        returncode = 255 if failure == "exit" else 0
        stdout = "not json" if failure == "badjson" else ""
        stderr = "ssh: connection refused"

    def fake_run(cmd, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)
        return FakeProc()

    monkeypatch.setattr(mal_status.subprocess, "run", fake_run)
    status = mal_status.collect_remote_research_status()
    assert status["reachable"] is False and status["error"]


def test_main_remote_research_writes_host_file(monkeypatch, tmp_path):
    monkeypatch.setattr(mal_status, "collect_remote_research_status", lambda host: {"host": host, "reachable": True, "units": []})
    assert mal_status.main(["--out-dir", str(tmp_path), "--remote", "research"]) == 0
    assert json.loads((tmp_path / "mal-research-0.json").read_text())["host"] == "mal-research-0"


def test_units_json_mode_prints_json_and_needs_no_out_dir(monkeypatch, capsys):
    monkeypatch.setattr(mal_status, "collect_units", lambda sources=None: ([mal_status.empty_unit("mal-a.service", "user", "service")], [], {"wall_ms": 1, "subprocesses": 2}))
    assert mal_status.main(["--units-json", "--unit-sources", "research"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["units"][0]["name"] == "mal-a.service" and out["units_meta"]["subprocesses"] == 2
    assert "load" in out and "memory" in out
