#!/usr/bin/env python3
"""Outcome-free stop rule for the s1t class side feed (QP-NOTE-1102-tip-readers Note 2 section N2.3, rules W1 to W7). Paper only. Reads ONE file and prints a few lines.

W1 INPUT. Only `arena-feed-status.json` (--status, default /data/mal/arena/tape/arena-feed-status.json); a --status whose basename is anything else is
refused (exit 2). The tool opens that one file by name. It never lists a directory and never opens a `tape-*` file. Keys used: `updated_ms`,
`started_ms`, `pid`, `gaps` (a number, or a list/dict whose length is the number) and `sockets[prog][*].reconnects` (summed). Two series: G (gaps) and
R (reconnects). A sample is taken every --interval-s (default 60) s on the watcher's wall clock. A missing or unreadable file is UNREADABLE; a status
whose `updated_ms` is more than 300 s behind the wall clock is STALE.

W2 BASELINE. The watcher runs at least 24 h before the smoke start S (--smoke-start-ms, required). The baseline is the 24 disjoint hours
[S - 24 h + k h, S - 24 h + (k + 1) h) of the watcher's own samples. An hour is valid when all its samples share one pid, none is STALE or UNREADABLE,
its counters never go down, and its samples cover at least 50 of its 60 minutes. Its increment is the counter change from its first to its last
sample, scaled to a full hour. b = the mean of the valid hourly increments, m = their maximum, per series. With fewer than 18 valid hours the watcher
prints `S1T_SIDE_FEED_NO_BASELINE` and exits 6: as soon as 7 completed hours are invalid (before S), and at S in any case. The smoke does not start.
The baseline is fixed at S and never recomputed; at S the watcher prints `S1T_SIDE_FEED_BASELINE G b= m= T= R b= m= T=` (the arena feed's health
figures, not s1t counts).
W3 THRESHOLD, per series: T = max(2 x b, 1.25 x m, b + 20), per hour.
W4 CHECK. From S on, every sample is a check: the trailing-60-min increment against the newest sample at least 60 min old with the same pid, and never
across a pid change (scaled to a full hour when that sample is older than 60 min). A series is above its T when the increment exceeds it. STOP needs the
same series above T on 10 consecutive completed checks; then it prints `S1T_SIDE_FEED_STOP series=<G|R> inc=<x> T=<y>` and exits 4. A completed check at or
below T resets that series' run; a STALE or UNREADABLE sample is not a completed check and leaves the run as it is (it must not hide a rise).
A pid change (or a counter that went down) prints `S1T_SIDE_FEED_FEED_RESTART`, resets both runs, and is neither a stop nor a new baseline: checks resume
when a sample of the new pid is 60 min old.
W5 FAIL CLOSED. After S, `S1T_SIDE_FEED_BLIND` and exit 5 when no check has completed for 90 min (UNREADABLE, STALE, or no same-pid 60-min history after
restarts), and also, stricter, after 10 consecutive STALE or UNREADABLE samples.
W6 Exit 4 or 5 stops the smoke process (a slip, never a stop of s1t; the manager asks QP). Exit 6 means no smoke.
With --seconds N it exits 0 after N s (printing `S1T_SIDE_FEED_OK`). Exit codes: 0 done, 2 usage, 4 STOP, 5 BLIND, 6 NO_BASELINE.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Callable, Sequence

STATUS_NAME = "arena-feed-status.json"
STATUS_DEFAULT = "/data/mal/arena/tape/" + STATUS_NAME
EXIT_STOP = 4
EXIT_BLIND = 5
EXIT_NO_BASELINE = 6
HOUR_MS = 3_600_000
BASE_HOURS = 24
MIN_VALID_HOURS = 18
MAX_INVALID_HOURS = BASE_HOURS - MIN_VALID_HOURS  # 6: the 7th invalid hour ends the baseline before S
MIN_COVER_S = 3000.0  # 50 of 60 minutes
STOP_CHECKS = 10
BLIND_CHECKS = 10  # consecutive STALE or UNREADABLE samples
BLIND_MS = 90 * 60_000  # no completed check for 90 min
STALE_MS = 300_000
READ_MAX = 4 * 1024 * 1024
SERIES = ("G", "R")


def _n(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _count(v: Any) -> float | None:
    return float(len(v)) if isinstance(v, (list, dict)) else _n(v)


def extract(st: Any) -> dict | None:
    """The five keys, or None when one is missing or malformed."""
    if not isinstance(st, dict):
        return None
    upd, start, gaps = _n(st.get("updated_ms")), _n(st.get("started_ms")), _count(st.get("gaps"))
    socks = st.get("sockets")
    if upd is None or start is None or gaps is None or not isinstance(socks, dict) or "pid" not in st:
        return None
    rec = 0.0
    for per_prog in socks.values():
        for s in (per_prog.values() if isinstance(per_prog, dict) else per_prog if isinstance(per_prog, list) else []):
            if isinstance(s, dict):
                rec += _n(s.get("reconnects")) or 0.0
    return {"updated_ms": upd, "started_ms": start, "pid": st.get("pid"), "G": gaps, "R": rec}


def read_status(path: str) -> dict | None:
    """Open the one status file by name. None when it is missing, too large or not JSON."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(READ_MAX + 1)
        return json.loads(raw) if len(raw) <= READ_MAX else None
    except (OSError, ValueError):
        return None


def threshold(b: float, m: float) -> float:
    return max(2 * b, 1.25 * m, b + 20)


class Watcher:
    def __init__(self, smoke_start_ms: int, interval_s: float = 60.0, stale_ms: int = STALE_MS) -> None:
        self.S = int(smoke_start_ms)
        self.interval_s, self.stale_ms = interval_s, stale_ms
        self.t0 = self.S - BASE_HOURS * HOUR_MS
        self.good: list[tuple[float, Any, float, float]] = []  # (wall_ms, pid, gaps, reconnects) of every good sample kept
        self.bad_times: list[float] = []  # wall_ms of the STALE / UNREADABLE samples before S
        self.hours: dict[int, tuple[float, float] | None] = {}  # hour index -> hourly increments (G, R), None when invalid
        self.baseline: dict[str, tuple[float, float, float]] | None = None  # series -> (b, m, T)
        self.pid: Any = None
        self.pid_since: float | None = None
        self.bad_run = 0
        self.runs = {s: 0 for s in SERIES}
        self.last_completed: float | None = None

    # ---- baseline -----------------------------------------------------------------------------------------------------------------------
    def _hour(self, k: int) -> tuple[float, float] | None:
        hs = self.t0 + k * HOUR_MS
        he = hs + HOUR_MS
        ss = [x for x in self.good if hs <= x[0] < he]
        if any(hs <= t < he for t in self.bad_times) or len(ss) * self.interval_s < MIN_COVER_S or len({x[1] for x in ss}) != 1:
            return None
        first, last = ss[0], ss[-1]
        if last[0] <= first[0] or last[2] < first[2] or last[3] < first[3]:
            return None
        scale = HOUR_MS / (last[0] - first[0])
        return (last[2] - first[2]) * scale, (last[3] - first[3]) * scale

    def _baseline_step(self, wall: float) -> list[str]:
        done = int(min(BASE_HOURS, max(0, (wall - self.t0) // HOUR_MS)))
        for k in range(len(self.hours), done):
            self.hours[k] = self._hour(k)
        valid = [v for v in self.hours.values() if v is not None]
        if len(self.hours) - len(valid) > MAX_INVALID_HOURS or (wall >= self.S and len(valid) < MIN_VALID_HOURS):
            return ["S1T_SIDE_FEED_NO_BASELINE"]
        if wall < self.S:
            return []
        base = {}
        for i, s in enumerate(SERIES):
            vals = [v[i] for v in valid]
            b, m = sum(vals) / len(vals), max(vals)
            base[s] = (b, m, threshold(b, m))
        self.baseline = base
        self.last_completed = float(self.S)
        return ["S1T_SIDE_FEED_BASELINE " + " ".join(f"{s} b={base[s][0]:.2f} m={base[s][1]:.2f} T={base[s][2]:.2f}" for s in SERIES)]

    # ---- live check ---------------------------------------------------------------------------------------------------------------------
    def _check(self, wall: float, g: float, r: float) -> list[str]:
        ref = None
        for x in reversed(self.good):
            if x[0] <= wall - HOUR_MS:
                ref = x
                break
        if ref is None or self.pid_since is None or ref[0] < self.pid_since:
            return []  # no same-pid history 60 min old: not a completed check
        self.last_completed = wall
        scale = HOUR_MS / (wall - ref[0])
        out = []
        for s, inc in (("G", (g - ref[2]) * scale), ("R", (r - ref[3]) * scale)):
            T = self.baseline[s][2]
            self.runs[s] = self.runs[s] + 1 if inc > T else 0
            if self.runs[s] >= STOP_CHECKS:
                out.append(f"S1T_SIDE_FEED_STOP series={s} inc={inc:.2f} T={T:.2f}")
        return out

    def step(self, st: Any, wall_ms: float) -> list[str]:
        """One sample at wall-clock `wall_ms`. Returns the lines to print: S1T_SIDE_FEED_STOP (exit 4), _BLIND (5) or _NO_BASELINE (6) end the run."""
        ex = extract(st)
        word = "UNREADABLE" if ex is None else ("STALE" if wall_ms - ex["updated_ms"] > self.stale_ms else None)
        out: list[str] = []
        if word is not None:
            self.bad_run += 1
            if wall_ms < self.S:
                self.bad_times.append(wall_ms)
            out.append(word)
        else:
            self.bad_run = 0
            last = self.good[-1] if self.good else None
            if self.pid_since is None:
                self.pid_since = wall_ms
            elif ex["pid"] != self.pid or (last is not None and (ex["G"] < last[2] or ex["R"] < last[3])):
                self.pid_since = wall_ms
                self.runs = {s: 0 for s in SERIES}
                if wall_ms >= self.S:
                    out.append("S1T_SIDE_FEED_FEED_RESTART")
            self.pid = ex["pid"]
            self.good.append((wall_ms, ex["pid"], ex["G"], ex["R"]))
        if self.baseline is None:
            out += self._baseline_step(wall_ms)
            if self.baseline is None:
                return out
        if word is None:
            out += self._check(wall_ms, ex["G"], ex["R"])
            while self.good and self.good[0][0] < wall_ms - 2 * HOUR_MS:
                self.good.pop(0)
        if self.bad_run >= BLIND_CHECKS:
            out.append(f"S1T_SIDE_FEED_BLIND reason=unreadable_or_stale consecutive_samples={self.bad_run}")
        elif wall_ms - self.last_completed >= BLIND_MS:
            out.append("S1T_SIDE_FEED_BLIND reason=no_completed_check_90min")
        return out


def run_watch(read: Callable[[], Any], seconds: float | None, interval_s: float, smoke_start_ms: int, *, clock: Callable[[], float] = time.monotonic,
              sleep: Callable[[float], None] = time.sleep, wall_ms: Callable[[], float] = lambda: time.time() * 1000,
              emit: Callable[[str], None] = print) -> int:
    w = Watcher(smoke_start_ms, interval_s)
    t0 = clock()
    while True:
        lines = w.step(read(), wall_ms())
        for ln in lines:
            emit(ln)
        for prefix, code in (("S1T_SIDE_FEED_STOP", EXIT_STOP), ("S1T_SIDE_FEED_BLIND", EXIT_BLIND), ("S1T_SIDE_FEED_NO_BASELINE", EXIT_NO_BASELINE)):
            if any(ln.startswith(prefix) for ln in lines):
                return code
        if seconds is not None and clock() - t0 >= seconds:
            emit("S1T_SIDE_FEED_OK")
            return 0
        sleep(interval_s)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Outcome-free stop rule for the s1t side feed: watches arena-feed-status.json only (QP-NOTE-1102 Note 2 N2.3).")
    ap.add_argument("--status", default=STATUS_DEFAULT, help=f"path of {STATUS_NAME} (any other basename is refused)")
    ap.add_argument("--smoke-start-ms", required=True, type=int, help="S, the smoke's start (epoch ms); the watcher must be running at least 24 h before it")
    ap.add_argument("--seconds", type=float, default=None, help="exit 0 after this many seconds (default: run until a stop)")
    ap.add_argument("--interval-s", type=float, default=60.0)
    args = ap.parse_args(argv)
    if os.path.basename(args.status) != STATUS_NAME:
        print(f"refused: --status must be a file named {STATUS_NAME}", file=sys.stderr)
        return 2
    if not args.interval_s > 0:
        print("refused: --interval-s must be positive", file=sys.stderr)
        return 2
    return run_watch(lambda: read_status(args.status), args.seconds, args.interval_s, args.smoke_start_ms)


if __name__ == "__main__":
    sys.exit(main())
