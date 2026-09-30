#!/usr/bin/env python3
"""Helius credit log for the MAL Console's Spend screen (console-plan.md §9.6).

``snapshot()`` reads the walker checkpoints and the pre-create counter file
(read-only, same sources as ``tools/mal_status.py``) and returns one dict per
job. ``append()`` writes new lines to a JSONL log, one per job, under an
``fcntl`` lock, skipping a job when nothing changed and less than an hour has
passed since its last line (so a once-a-minute collector doesn't spam the
log with duplicate credit counts).
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from typing import Any

from tools.mal_status import read_pre_create_credits, read_walker_checkpoint, utc_now_iso

DEFAULT_LOG_PATH = "/home/claude/data/credits/helius-credits.jsonl"

WALKER_JOB_NAMES = {
    "walker_1": "/var/lib/mal/backfill-fast",
    "walker_b": "/var/lib/mal/backfill-fast-b",
    "walker_c": "/var/lib/mal/backfill-fast-c",
}

MIN_INTERVAL_S = 3600.0


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def snapshot(
    checkpoint_dirs: dict[str, str] | None = None,
    pre_create_path: str | None = None,
) -> list[dict[str, Any]]:
    """Read current credit counters for each job. Never raises.

    Returns a list of {"job": ..., "credits_used": int|None, "cap": int|None}.
    A job whose source file can't be read gets ``credits_used: None`` rather
    than being dropped, so a caller can still see it errored.
    """
    checkpoint_dirs = checkpoint_dirs if checkpoint_dirs is not None else WALKER_JOB_NAMES
    pre_create_path = pre_create_path if pre_create_path is not None else "/var/lib/mal/fast-listener/pre-create-credits.json"

    results: list[dict[str, Any]] = []
    for job, checkpoint_dir in checkpoint_dirs.items():
        try:
            data = read_walker_checkpoint(job, checkpoint_dir)
            results.append({"job": job, "credits_used": data.get("credits_used"), "cap": None})
        except Exception:  # noqa: BLE001
            results.append({"job": job, "credits_used": None, "cap": None})

    try:
        pre_create = read_pre_create_credits(pre_create_path)
    except Exception:  # noqa: BLE001
        pre_create = None
    if pre_create is not None:
        results.append(
            {
                "job": "pre_create",
                "credits_used": pre_create.get("credits"),
                "cap": pre_create.get("cap"),
            }
        )
    else:
        results.append({"job": "pre_create", "credits_used": None, "cap": None})

    return results


def _last_lines_by_job(log_path: str) -> dict[str, dict[str, Any]]:
    last: dict[str, dict[str, Any]] = {}
    if not os.path.exists(log_path):
        return last
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            job = record.get("job")
            if job:
                last[job] = record
    return last


def append(log_path: str, snapshots: list[dict[str, Any]], *, now_utc: str | None = None) -> list[dict[str, Any]]:
    """Append one JSONL line per job that changed or is stale (>= 1h old).

    Returns the list of records actually written. Locks the log file with
    ``fcntl.flock`` (exclusive) for the read-modify-append so two collectors
    racing each other don't interleave partial lines or double-append.
    """
    now_utc = now_utc or utc_now_iso()
    now_dt = _parse_ts(now_utc)

    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    written: list[dict[str, Any]] = []

    # Open in append+read mode so the same fd can be locked and then used to
    # read prior lines before writing new ones.
    fd = os.open(log_path, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            last_by_job = _last_lines_by_job(log_path)
            for entry in snapshots:
                job = entry["job"]
                credits_used = entry.get("credits_used")
                cap = entry.get("cap")
                prior = last_by_job.get(job)

                prior_credits = prior.get("credits_used") if prior else None
                changed = prior is None or credits_used != prior_credits

                stale_enough = True
                if prior is not None and now_dt is not None:
                    prior_dt = _parse_ts(prior.get("ts_utc", ""))
                    if prior_dt is not None:
                        stale_enough = (now_dt - prior_dt).total_seconds() >= MIN_INTERVAL_S

                if not changed and not stale_enough:
                    continue

                delta = None
                if credits_used is not None and prior_credits is not None:
                    try:
                        delta = credits_used - prior_credits
                    except TypeError:
                        delta = None

                record = {
                    "ts_utc": now_utc,
                    "job": job,
                    "credits_used": credits_used,
                    "delta_since_last": delta,
                    "cap": cap,
                }
                with os.fdopen(os.dup(fd), "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
                written.append(record)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

    return written


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-path", default=DEFAULT_LOG_PATH)
    parser.add_argument("--pre-create-credits-path", default="/var/lib/mal/fast-listener/pre-create-credits.json")
    args = parser.parse_args(argv)

    snaps = snapshot(pre_create_path=args.pre_create_credits_path)
    written = append(args.log_path, snaps)
    print(f"wrote {len(written)} line(s) to {args.log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
