"""Kill switch for the Kalshi LIP live lane (OFFLINE core: files and an injected canceller, no network).

* A ``STOP`` file: immediate cancel-all, then no new orders for as long as the file exists.
* A watchdog heartbeat older than ``max_age`` (90 s): cancel-all, no new orders until it is fresh again.
  A missing or unreadable heartbeat counts as stale (fail closed), so nothing is placed before the
  watchdog has beaten once.
* Every trip, refusal and cancel goes to the JSONL event log (``kalshi_live_log.EventLog``).

``check()`` is both the quoter's pre-trade gate and the body of the separate watchdog process's tick
(:meth:`watchdog_tick`): the watchdog needs no order state, only the two paths and a canceller.  A failed
cancel-all is retried on every later check until it succeeds once per episode.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

HEARTBEAT_MAX_AGE_S = 90.0
FUTURE_SKEW_S = 5.0  # a heartbeat stamped further ahead than this is invalid


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str | None = None  # stop_file | heartbeat_missing | heartbeat_invalid | heartbeat_stale
    cancel_ok: bool | None = None  # outcome of the cancel-all this episode (None when not tripped)


def write_heartbeat(path, clock=time.time) -> float:
    """Atomic heartbeat write (the watchdog's own beat, or the quoter's liveness)."""
    p = Path(path)
    ts = float(clock())
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({"ts": ts}))
    os.replace(tmp, p)
    return ts


def read_heartbeat(path) -> tuple[float | None, str | None]:
    """(ts, problem).  problem is None when ts is usable."""
    p = Path(path)
    try:
        d = json.loads(p.read_text())
        ts = d["ts"]
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            return None, "heartbeat_invalid"
        return float(ts), None
    except FileNotFoundError:
        return None, "heartbeat_missing"
    except (ValueError, KeyError, TypeError, OSError):
        return None, "heartbeat_invalid"


class KillSwitch:
    def __init__(self, stop_path, heartbeat_path, canceller, log, clock=time.time,
                 max_age: float = HEARTBEAT_MAX_AGE_S):
        self.stop_path, self.heartbeat_path = Path(stop_path), Path(heartbeat_path)
        self.canceller, self.log, self.clock, self.max_age = canceller, log, clock, max_age
        self._done: dict[str, bool] = {}  # reason -> cancel-all already succeeded in this episode

    def _cancel(self, reason: str) -> bool:
        if self._done.get(reason):
            return True
        try:
            self.canceller()
        except Exception as e:  # noqa: BLE001 - any failure is logged and retried on the next check
            self.log.emit("cancel_all_failed", reason=reason, error=type(e).__name__,
                          status=getattr(e, "status", None))
            return False
        self._done[reason] = True
        self.log.emit("cancel_all_ok", reason=reason)
        return True

    def _trip(self, reason: str, **detail) -> Verdict:
        if reason not in self._done:
            self.log.emit("kill_trip", reason=reason, **detail)
            self._done.setdefault(reason, False)
        ok = self._cancel(reason)
        self.log.emit("refusal", code=reason, kill=True, cancel_ok=ok)
        return Verdict(False, reason, ok)

    def check(self) -> Verdict:
        if os.path.lexists(self.stop_path):
            return self._trip("stop_file")
        self._done.pop("stop_file", None)  # STOP removed: a later STOP is a new episode
        ts, problem = read_heartbeat(self.heartbeat_path)
        now = float(self.clock())
        if problem is None and ts - now > FUTURE_SKEW_S:
            problem = "heartbeat_invalid"
        if problem is not None:
            return self._trip(problem)
        age = now - ts
        if age > self.max_age:
            return self._trip("heartbeat_stale", age=round(age, 3), max_age=self.max_age)
        for r in ("heartbeat_stale", "heartbeat_missing", "heartbeat_invalid"):
            self._done.pop(r, None)  # fresh again: a later lapse is a new episode
        return Verdict(True)

    watchdog_tick = check
