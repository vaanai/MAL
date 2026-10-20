"""HV4 observability rows: an append-only JSONL side file, outside the decision log (HV4 v2, the shakedown checks measured directly).

Paper only. Nothing here is read by a decision. The file is <root>/hv4-obs.jsonl; one canonical-JSON line per row, one O_APPEND write per call, no rewrite, no
truncate. A failed write is counted in `ObsLog.write_errors` and never raised, so a full disk cannot stop the runner. The decision logs, the cards, the ledger and
`Runner.counters` are NOT touched by anything in this module, which is what the B equivalence proof pins (decision logs md5-identical with the rows on and off; the
switch is `RunnerConfig.obs`, default from the environment variable MAL_HV4_OBS, "0" = off).

Rows (every row has `schema`, `kind`, `wall_ms` and `pid`, the writing process's os.getpid() at write time):
  runner_start         pid, frozen_sha, the head binding: one per process start.
  prefetch_start       the spend file and the chain counter as the process loaded them (check 8: reload with no reset), and `prior_minutes` / `prior_calls_total`: the
                       previous process's last 61 per-minute counts [minute_ms, calls, credits] and the running total of all earlier processes, read from the counter's
                       state file, kept apart from this process's own chain_hour record (check 5, the hour that crosses a restart; QP 6279836164 R3).
  first_seen_by_scanner  one row per pool each time it ENTERS the prefetcher's watch set, which at d1dbcd6 is the scanner's filter-pass set (hv4_runner.Runner.scan
                       passes `cand` as the items): pool, scan_ms (T of the scan that saw it), in_filter_pass, entry "first" (never seen by this process) or
                       "re_entry" (it had left the set for DROP_AFTER_MS and was dropped), since_last_seen_ms for a re-entry, n_filter_pass, hot. Check 3.
  chain_stale_served   a card read that was served a mint fact older than the card's limit (it will be refused chain_stale): pool, scan_ms, age_ms, hot, tier,
                       fails, next_try_in_ms. Check 2 diagnosis.
  chain_hour           one row per CLOSED UTC hour, written by the prefetch thread: calls in the hour and the 60 per-minute call counts from the chain counter's own
                       record (so any rolling hour is exact), credits, the counter's running totals, the prefetcher's error counters (spend_write_errors,
                       spend_state_reset, ...), budget_deferred by class, the counter's state_write_errors. Check 5.
  runner_hour          one row per closed UTC hour, written by the runner thread: hook_errors (by type, cumulative), housekeep errors, the runner counters,
                       RSS, obs write errors. Checks 6/8, QP-M1.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

OBS_FILE = "hv4-obs.jsonl"
OBS_SCHEMA = "hv4_obs_v1"
HOUR_MS = 3_600_000
MIN_MS = 60_000


def obs_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Default for RunnerConfig.obs: on unless MAL_HV4_OBS is "0" (the switch the B equivalence proof flips; it changes no decision either way)."""
    return str((os.environ if env is None else env).get("MAL_HV4_OBS", "1")).strip() != "0"


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class ObsLog:
    """Thread-safe append-only writer. `enabled=False` makes every call a no-op that returns False. `scan_ms` is set by the runner thread before each scan."""

    def __init__(self, root: str | os.PathLike | None, enabled: bool = True, wall_ms=None) -> None:
        self.path = Path(root) / OBS_FILE if root is not None else None
        self.enabled = bool(enabled) and self.path is not None
        self.write_errors = 0
        self.build_errors = 0  # a row that could not be BUILT (the writers' callers count it here, apart from write_errors)
        self.rows_written = 0
        self.scan_ms: int | None = None
        self._lock = threading.Lock()
        self._wall_ms = wall_ms or (lambda: time.time_ns() // 1_000_000)

    def write_many(self, rows: list[Mapping[str, Any]]) -> bool:
        """Append rows in one write. Never raises."""
        if not self.enabled or not rows:
            return False
        try:
            # `pid` is taken at WRITE time (a forked child never inherits its parent's) and set after the row's own keys, so every row carries exactly one pid and a
            # row cannot override it. CHECKS-3 joins first_seen_by_scanner and the decision-log cards on it.
            pid = os.getpid()
            data = b"".join(canon({"schema": OBS_SCHEMA, "wall_ms": self._wall_ms(), **r, "pid": pid}).encode() + b"\n" for r in rows)
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
                fd = os.open(str(self.path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
                try:
                    os.write(fd, data)
                finally:
                    os.close(fd)
                self.rows_written += len(rows)
            return True
        except Exception:  # noqa: BLE001 - observability must never stop the runner
            self.write_errors += 1
            return False

    def write(self, kind: str, **fields: Any) -> bool:
        return self.write_many([{"kind": kind, **fields}])


def rss_mb() -> float | None:
    """Resident set size of this process in MB from /proc/self/status (None where unavailable)."""
    try:
        for line in open("/proc/self/status", encoding="ascii"):
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024.0, 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def read_rows(path: str | os.PathLike) -> list[dict[str, Any]]:
    """The rows of an obs file (for the report tool and the tests). A torn last line is skipped."""
    out: list[dict[str, Any]] = []
    try:
        for line in Path(path).read_bytes().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out
