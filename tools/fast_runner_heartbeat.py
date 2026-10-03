#!/usr/bin/env python3
"""One-shot heartbeat sampler for the fast-0 EXP-012 runner (DEC-016 Amendment 3 (b)).

Reads runner-status.json and appends ONE line to heartbeat.jsonl with allowlisted fields only:
sampled_ms, status_ts_ms, lag_ms, pid, ok. It never prints the status and never writes any
other key. The runner is not changed. The pid is the systemd MainPID of the runner unit,
because write_runner_status carries no pid or boot field.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ALLOWED_KEYS = ("sampled_ms", "status_ts_ms", "lag_ms", "pid", "ok")
UNIT = "mal-fast-forward-paper.service"  # a system unit (installed to /etc/systemd/system)
DEFAULT_STATUS = Path("/var/lib/mal/paper/fast-forward-paper/runner-status.json")
DEFAULT_OUT = Path("/var/lib/mal/fast-forward-heartbeat/heartbeat.jsonl")


def _int_or_none(v: Any) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def main_pid(unit: str = UNIT) -> int | None:
    try:
        r = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value", unit],
            capture_output=True, text=True, timeout=5, check=False,
        )
        pid = int(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return pid if pid > 0 else None


def build_record(
    status_path: Path,
    *,
    now_ms: int,
    pid_fn: Callable[[], int | None] = main_pid,
) -> dict[str, Any]:
    status_ts_ms: int | None = None
    lag_ms: int | None = None
    ok = False
    pid: int | None = None
    try:
        raw = json.loads(status_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            status_ts_ms = _int_or_none(raw.get("ts_ms"))
            lag_ms = _int_or_none(raw.get("lag_ms"))
            if status_ts_ms is None:
                status_ts_ms = int(status_path.stat().st_mtime * 1000)
            pid = _int_or_none(raw.get("pid"))
            ok = True
    except (OSError, ValueError):
        ok = False
    if pid is None:
        pid = pid_fn()
    rec = {
        "sampled_ms": now_ms,
        "status_ts_ms": status_ts_ms,
        "lag_ms": lag_ms,
        "pid": pid,
        "ok": ok,
    }
    assert set(rec) == set(ALLOWED_KEYS)
    return rec


def append_record(out_path: Path, rec: dict[str, Any]) -> None:
    assert set(rec) == set(ALLOWED_KEYS), "heartbeat record has a non-allowlisted key"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    rec = build_record(args.status, now_ms=int(time.time() * 1000))
    append_record(args.out, rec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
