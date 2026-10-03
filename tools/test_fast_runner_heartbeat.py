"""Heartbeat sampler and its units. Synthetic fixtures only; never reads /var/lib/mal."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from tools.fast_runner_heartbeat import ALLOWED_KEYS, append_record, build_record

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "scripts" / "mal-fast"
INSTALLER = KIT / "install-fast-forward-paper.sh"

SECRET_STATUS = {
    "schema": "forward_paper_runner_status_v1",
    "ts": "2026-10-06T00:00:00Z",
    "ts_ms": 1_000_000,
    "stale_cap_ms": 5000,
    "lag_ms": 420,
    "stale_dropped": 7,
    "fail_rate": 0.15,
    "promotion_live_ms": None,
    "newest_recv_ms": 999_580,
}


def test_allowlist_only(tmp_path, capsys):
    st = tmp_path / "runner-status.json"
    st.write_text(json.dumps(SECRET_STATUS))
    out = tmp_path / "hb" / "heartbeat.jsonl"
    rec = build_record(st, now_ms=1_005_000, pid_fn=lambda: 4242)
    append_record(out, rec)
    lines = out.read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert set(row) == set(ALLOWED_KEYS)
    assert row == {"sampled_ms": 1_005_000, "status_ts_ms": 1_000_000, "lag_ms": 420, "pid": 4242, "ok": True}
    for leaked in ("fail_rate", "stale_dropped", "newest_recv_ms", "promotion_live_ms"):
        assert leaked not in lines[0]
    assert capsys.readouterr().out == ""


def test_pid_from_status_wins(tmp_path):
    st = tmp_path / "s.json"
    st.write_text(json.dumps({**SECRET_STATUS, "pid": 7}))
    assert build_record(st, now_ms=1, pid_fn=lambda: 9)["pid"] == 7


def test_mtime_fallback_when_no_ts(tmp_path):
    st = tmp_path / "s.json"
    st.write_text(json.dumps({"lag_ms": 5}))
    rec = build_record(st, now_ms=1, pid_fn=lambda: None)
    assert rec["ok"] is True and rec["status_ts_ms"] == int(st.stat().st_mtime * 1000)


def test_missing_and_garbage_file_is_ok_false(tmp_path):
    rec = build_record(tmp_path / "nope.json", now_ms=5, pid_fn=lambda: 11)
    assert rec == {"sampled_ms": 5, "status_ts_ms": None, "lag_ms": None, "pid": 11, "ok": False}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert build_record(bad, now_ms=5, pid_fn=lambda: None)["ok"] is False
    out = tmp_path / "h.jsonl"
    append_record(out, rec)
    assert json.loads(out.read_text())["ok"] is False


def test_append_refuses_extra_key(tmp_path):
    rec = build_record(tmp_path / "nope.json", now_ms=5, pid_fn=lambda: None)
    rec["fail_rate"] = 0.1
    try:
        append_record(tmp_path / "h.jsonl", rec)
    except AssertionError:
        return
    raise AssertionError("extra key was written")


def test_units():
    svc = (KIT / "mal-fast-runner-heartbeat.service").read_text()
    tmr = (KIT / "mal-fast-runner-heartbeat.timer").read_text()
    assert re.search(r"^Type=oneshot$", svc, re.M)
    assert re.search(r"^Slice=mal-forward\.slice$", svc, re.M)
    assert re.search(r"^User=ubuntu$", svc, re.M)
    for line in ("ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true", "NoNewPrivileges=true",
                 "ProtectKernelTunables=true", "LockPersonality=true", "RestrictSUIDSGID=true"):
        assert re.search(rf"^{re.escape(line)}$", svc, re.M), line
    assert "ReadWritePaths=/var/lib/mal/fast-forward-heartbeat" in svc
    assert re.search(r"^OnUnitActiveSec=15s$", tmr, re.M)
    assert re.search(r"^AccuracySec=1s$", tmr, re.M)


def test_installer_wiring_and_fence():
    text = INSTALLER.read_text()
    subprocess.run(["bash", "-n", str(INSTALLER)], check=True)
    assert 'NOT_BEFORE="2026-10-05T05:00:00Z"' in text
    assert 'if [[ "${FILES_ONLY}" == 0 && "${NOW}" < "${NOT_BEFORE}" ]]' in text
    assert '"${HB_UNIT}" "${HB_TIMER}"' in text
    assert "mal-fast-runner-heartbeat.service" in text and "mal-fast-runner-heartbeat.timer" in text
    # the fence comes before any install action
    assert text.index("refusing before") < text.index("run sudo -n install")
    # still never enables anything
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(("#", "log ", "echo ", "printf ", "die ")):
            continue
        assert not re.search(r"\b(enable|--now)\b", line), line


def test_torn_read_retries_once(tmp_path):
    st = tmp_path / "s.json"
    st.write_text('{"ts_ms": 10, "lag_')  # torn
    sleeps = []

    def fix(d):
        sleeps.append(d)
        st.write_text(json.dumps(SECRET_STATUS))

    rec = build_record(st, now_ms=1_005_000, pid_fn=lambda: 3, sleep_fn=fix)
    assert sleeps == [0.2] and rec["ok"] is True and rec["status_ts_ms"] == 1_000_000


def test_empty_file_retries_then_fails_once(tmp_path):
    st = tmp_path / "s.json"
    st.write_text("")
    sleeps = []
    rec = build_record(st, now_ms=5, pid_fn=lambda: 3, sleep_fn=sleeps.append)
    assert len(sleeps) == 1 and rec["ok"] is False


def test_good_read_does_not_sleep(tmp_path):
    st = tmp_path / "s.json"
    st.write_text(json.dumps(SECRET_STATUS))
    sleeps = []
    build_record(st, now_ms=5, pid_fn=lambda: 3, sleep_fn=sleeps.append)
    assert sleeps == []


def test_service_hardening():
    svc = (KIT / "mal-fast-runner-heartbeat.service").read_text()
    for line in ("RestrictAddressFamilies=AF_UNIX", "CapabilityBoundingSet=", "PrivateDevices=true",
                 "ProtectKernelLogs=true"):
        assert re.search(rf"^{re.escape(line)}$", svc, re.M), line
    inacc = " ".join(re.findall(r"^InaccessiblePaths=(.*)$", svc, re.M))
    for p in ("/var/lib/mal/sealed", "positions.jsonl", "decisions.jsonl", "pnl-daily.jsonl"):
        assert p in inacc, p
