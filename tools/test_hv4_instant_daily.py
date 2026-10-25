"""scripts/ops/hv4-instant-daily.sh: dry-run plan and a stub-python run on a synthetic tmp tree. Never touches /data/mal/arena; the ledger is never read
(the stub python prints canned lines). PAPER ONLY."""
from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "ops" / "hv4-instant-daily.sh"
BOTS = [f"hv4-{g}-{a}" for g, a in (("CON", "L"), ("CON", "F"), ("CON", "P"), ("CON", "X"), ("CON", "R"), ("BIG", "L"), ("BIG", "F"), ("BIG", "X"), ("BIG", "R"))]


def run(env_extra, tmp):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp), **env_extra}
    return subprocess.run(["sh", str(SCRIPT)], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)


def test_script_is_posix_sh_and_lists_the_nine_bots():
    txt = SCRIPT.read_text()
    assert txt.startswith("#!/bin/sh")
    assert "[[" not in txt and "PIPESTATUS" not in txt
    for b in BOTS:
        assert b in txt
    assert subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0


def test_dry_run_plan(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "hv4-CON-L.instant.json").write_text("{}")
    restarts = tmp_path / "restarts.jsonl"
    restarts.write_text("")
    r = run({"DRY": "1", "RESTARTS": str(restarts), "SHAS_FILE": str(tmp_path / "nosuch"), "RECORD_DIR": str(rec), "LEDGER": "/fake/ledger",
             "TABLE_DIR": "/fake/tables", "PY": "/fake/python"}, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert out.count("== plan run") == 10
    assert "== plan run 2026-10-30" in out and "== plan run 2026-11-08" in out
    assert out.count("SKIP hv4-CON-L: record exists") == 10
    cmds = [ln for ln in out.splitlines() if ln.startswith("/fake/python -m tools.hv4_read_instant")]
    assert len(cmds) == 10 * 8
    first = cmds[0]
    assert "--mode screen --bot hv4-CON-F --ledger-root /fake/ledger" in first
    assert "--health /fake/tables/hour-table-2026-10-25-final.jsonl --health-sha <recorded-sha-2026-10-25>" in first
    assert first.count("--health ") == 5 and "hour-table-2026-10-29-final" in first and "hour-table-2026-10-30-final" not in first
    last = cmds[-1]
    assert last.count("--health ") == 14 and "hour-table-2026-11-07-final" in last
    assert f"--restarts {restarts} --as-of-ms <now-ms> --out {rec}/hv4-BIG-R.instant.json" in last
    assert not (rec / "hv4-CON-F.instant.json").exists()


def test_refuses_without_restarts(tmp_path):
    r = run({"DRY": "1"}, tmp_path)
    assert r.returncode == 2 and "RESTARTS" in r.stdout


def test_default_run_time_is_0130():
    assert "HM=${HM:-01:30}" in SCRIPT.read_text()


def test_stub_run_prints_status_only_and_no_counts(tmp_path):
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    yday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    hm = (now - timedelta(minutes=1)).strftime("%H:%M")
    tables = tmp_path / "tables"
    tables.mkdir()
    t = tables / f"hour-table-{yday}-final.jsonl"
    t.write_text('{"fake":1}\n')
    good = hashlib.sha256(t.read_bytes()).hexdigest()
    restarts = tmp_path / "r.jsonl"
    restarts.write_text("")
    rec = tmp_path / "rec"
    rec.mkdir()
    # stub python: CON-F writes a record (with counts on stdout), everything else is NOT_READY
    stub = tmp_path / "py"
    stub.write_text('#!/bin/sh\nshift 2\nwhile [ $# -gt 0 ]; do case "$1" in --bot) B=$2;; --out) O=$2;; esac; shift; done\n'
                    'if [ "$B" = hv4-CON-F ]; then echo "HV4 READ INSTANT head"; echo "READ_INSTANT 2026-11-01T00:00:00Z (1)"; echo "O 120  R 5  margin 114"; '
                    'echo "recorded $O sha256 abc123"; else echo "HV4 READ INSTANT head"; echo "NOT_READY: no boundary yet (last tested none)"; echo "NOT_READY: nothing written"; fi\n')
    stub.chmod(0o755)
    env = {"FIRST": today, "LAST": today, "HM": hm, "TABLE_FIRST": yday, "RESTARTS": str(restarts), "TABLE_DIR": str(tables),
           "RECORD_DIR": str(rec), "LEDGER": str(tmp_path / "ledger"), "PY": str(stub)}
    r = run(env, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RECORD bot=hv4-CON-F READ_INSTANT 2026-11-01T00:00:00Z (1) record_sha256=abc123" in r.stdout
    assert "margin" not in r.stdout and "O 120" not in r.stdout
    assert r.stdout.count("NOT_READY: no boundary yet") == 8
    # the job itself recorded the table sha (no session step), once
    shas = rec / "health-shas.txt"
    assert shas.read_text() == f"{yday} {good}\n"
    # a second run does not append again and still passes
    assert run(env, tmp_path).returncode == 0
    assert shas.read_text() == f"{yday} {good}\n"
    # a table with a leftover .tmp is not complete: exit 3
    tmpf = tables / f"hour-table-{yday}-final.jsonl.tmp"
    tmpf.write_text("x")
    assert run({**env, "TABLE_WAIT_S": "0"}, tmp_path).returncode == 3
    tmpf.unlink()
    # a table changed after its sha was recorded is refused
    t.write_text('{"fake":2}\n')
    r = run(env, tmp_path)
    assert r.returncode == 2 and "differs from the recorded sha" in r.stdout
    t.write_text('{"fake":1}\n')
    # the SHAS_FILE override is honoured
    alt = tmp_path / "alt-shas.txt"
    assert run({**env, "SHAS_FILE": str(alt)}, tmp_path).returncode == 0
    assert alt.read_text() == f"{yday} {good}\n"
    # a tool exit 2 fails the job
    stub.write_text("#!/bin/sh\necho 'REFUSED: x' >&2\nexit 2\n")
    r = run(env, tmp_path)
    assert r.returncode == 2 and "tool exit 2" in r.stdout
    # a missing table fails the job (exit 3) after the wait
    t.unlink()
    r = run({**env, "TABLE_WAIT_S": "0"}, tmp_path)
    assert r.returncode == 3
