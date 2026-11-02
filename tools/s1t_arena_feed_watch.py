#!/usr/bin/env python3
"""Outcome-free stop rule for the s1t class side feed (QP-NOTE-1102-tip-readers, binding). Paper only. Reads ONE file and prints at most a few lines.

INPUT. Only `arena-feed-status.json` (--status, default /data/mal/arena/tape/arena-feed-status.json); a --status whose basename is anything else is
refused (exit 2). The tool opens that one file by name. It never lists a directory and never opens a `tape-*` file. Keys used: `updated_ms`,
`started_ms`, `pid`, `gaps` (a number, or a list/dict whose length is the number) and `sockets[prog][*].reconnects` (summed).

SAMPLING every --interval-s (default 60) s. Rates are per hour.
BASELINE. Feed started 6 h or more before the status's `updated_ms`: gaps / hours since `started_ms` and summed reconnects / hours since
`started_ms`, at the first sample. Younger feed: the watcher's own first 60 min (counter change over the first 60 min of its samples). A baseline
is fixed once set. No check runs before it exists.
TRIGGER. The trailing-60-min gaps (counter change against the newest sample at least 60 min old, scaled to a rate) > max(2 x baseline, baseline + 30),
OR the trailing-60-min reconnects > max(2 x baseline, baseline + 10), on 2 consecutive checks. Then it prints
`S1T_SIDE_FEED_STOP gaps_h=<x> base=<y> reconnects_h=<x> base=<y>` and exits 4.
OTHER. A `pid` change (or a counter that went down) is a feed restart: the history and baseline are dropped and `S1T_SIDE_FEED_REBASELINE` is
printed; it is not a trigger. A status older than 300 s (wall clock against `updated_ms`) prints `STALE` and a missing or unreadable file prints
`UNREADABLE`; neither is a trigger, and either resets the consecutive-check count. With --seconds N it exits 0 after N s (printing
`S1T_SIDE_FEED_OK`).
BLIND (reviewer M5: the rule must not be off for the whole smoke while the job looks healthy). It prints `S1T_SIDE_FEED_BLIND ...` and exits 5 when
  (a) BLIND_CHECKS (10) consecutive checks are UNREADABLE or STALE (10 min at the 60 s sampling), or
  (b) REBASE_MAX (3) or more re-baselines (pid changes or counter resets) fall inside any REBASE_WINDOW_S (60 min).
Exit codes: 0 done (--seconds), 2 usage, 4 STOP, 5 BLIND.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from typing import Any, Callable, Sequence

STATUS_NAME = "arena-feed-status.json"
STATUS_DEFAULT = "/data/mal/arena/tape/" + STATUS_NAME
EXIT_STOP = 4
EXIT_BLIND = 5
BLIND_CHECKS = 10  # consecutive UNREADABLE or STALE checks
REBASE_MAX = 3  # re-baselines inside REBASE_WINDOW_S
REBASE_WINDOW_S = 3600.0
STALE_MS = 300_000
YOUNG_H = 6.0
WINDOW_S = 3600.0
READ_MAX = 4 * 1024 * 1024


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
    return {"updated_ms": upd, "started_ms": start, "pid": st.get("pid"), "gaps": gaps, "rec": rec}


def read_status(path: str) -> dict | None:
    """Open the one status file by name. None when it is missing, too large or not JSON."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(READ_MAX + 1)
        return json.loads(raw) if len(raw) <= READ_MAX else None
    except (OSError, ValueError):
        return None


class Watcher:
    def __init__(self, window_s: float = WINDOW_S, stale_ms: int = STALE_MS, young_h: float = YOUNG_H) -> None:
        self.window_s, self.stale_ms, self.young_h = window_s, stale_ms, young_h
        self.pid: Any = None
        self.seen = False
        self.samples: collections.deque = collections.deque()  # (t, gaps, reconnects)
        self.base: tuple[float, float] | None = None
        self.streak = 0
        self.bad_run = 0  # consecutive UNREADABLE / STALE checks
        self.rebases: collections.deque = collections.deque()  # watcher times of the re-baselines in the last REBASE_WINDOW_S

    def _reset(self) -> None:
        self.samples.clear()
        self.base, self.streak = None, 0

    def _bad(self, word: str) -> list[str]:
        self.streak = 0
        self.bad_run += 1
        out = [word]
        if self.bad_run >= BLIND_CHECKS:
            out.append(f"S1T_SIDE_FEED_BLIND reason=unreadable_or_stale consecutive_checks={self.bad_run}")
        return out

    def step(self, st: Any, t: float, wall_ms: float) -> list[str]:
        """One check at watcher time t (s). Returns the lines to print; S1T_SIDE_FEED_STOP means stop (exit 4), S1T_SIDE_FEED_BLIND means blind (exit 5)."""
        ex = extract(st)
        if ex is None:
            return self._bad("UNREADABLE")
        if wall_ms - ex["updated_ms"] > self.stale_ms:
            return self._bad("STALE")
        self.bad_run = 0
        out: list[str] = []
        went_down = bool(self.samples) and (ex["gaps"] < self.samples[-1][1] or ex["rec"] < self.samples[-1][2])
        if self.seen and (ex["pid"] != self.pid or went_down):
            self._reset()
            out.append("S1T_SIDE_FEED_REBASELINE")
            self.rebases.append(t)
            while self.rebases and self.rebases[0] <= t - REBASE_WINDOW_S:
                self.rebases.popleft()
            if len(self.rebases) >= REBASE_MAX:
                out.append(f"S1T_SIDE_FEED_BLIND reason=rebaselines count={len(self.rebases)} window_s={int(REBASE_WINDOW_S)}")
                self.pid = ex["pid"]
                return out
        self.pid, self.seen = ex["pid"], True
        self.samples.append((t, ex["gaps"], ex["rec"]))
        if self.base is None:
            age_h = (ex["updated_ms"] - ex["started_ms"]) / 3.6e6
            first = self.samples[0]
            if age_h >= self.young_h:
                self.base = (ex["gaps"] / age_h, ex["rec"] / age_h)
            elif t - first[0] >= self.window_s:
                dt_h = (t - first[0]) / 3600.0
                self.base = ((ex["gaps"] - first[1]) / dt_h, (ex["rec"] - first[2]) / dt_h)
            if self.base is None:
                return out
        ref = None
        while len(self.samples) > 1 and self.samples[1][0] <= t - self.window_s:  # keep the newest sample that is at least a window old
            self.samples.popleft()
        if self.samples[0][0] <= t - self.window_s:
            ref = self.samples[0]
        if ref is None:
            self.streak = 0
            return out
        dt_h = (t - ref[0]) / 3600.0
        g_h, r_h = (ex["gaps"] - ref[1]) / dt_h, (ex["rec"] - ref[2]) / dt_h
        g0, r0 = self.base
        hot = g_h > max(2 * g0, g0 + 30) or r_h > max(2 * r0, r0 + 10)
        self.streak = self.streak + 1 if hot else 0
        if self.streak >= 2:
            out.append(f"S1T_SIDE_FEED_STOP gaps_h={g_h:.2f} base={g0:.2f} reconnects_h={r_h:.2f} base={r0:.2f}")
        return out


def run_watch(read: Callable[[], Any], seconds: float | None, interval_s: float, *, clock: Callable[[], float] = time.monotonic,
              sleep: Callable[[float], None] = time.sleep, wall_ms: Callable[[], float] = lambda: time.time() * 1000, emit: Callable[[str], None] = print) -> int:
    w = Watcher()
    t0 = clock()
    while True:
        lines = w.step(read(), clock(), wall_ms())
        for ln in lines:
            emit(ln)
        if any(ln.startswith("S1T_SIDE_FEED_STOP") for ln in lines):
            return EXIT_STOP
        if any(ln.startswith("S1T_SIDE_FEED_BLIND") for ln in lines):
            return EXIT_BLIND
        if seconds is not None and clock() - t0 >= seconds:
            emit("S1T_SIDE_FEED_OK")
            return 0
        sleep(interval_s)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Outcome-free stop rule for the s1t side feed: watches arena-feed-status.json only.")
    ap.add_argument("--status", default=STATUS_DEFAULT, help=f"path of {STATUS_NAME} (any other basename is refused)")
    ap.add_argument("--seconds", type=float, default=None, help="exit 0 after this many seconds (default: run until a stop)")
    ap.add_argument("--interval-s", type=float, default=60.0)
    args = ap.parse_args(argv)
    if os.path.basename(args.status) != STATUS_NAME:
        print(f"refused: --status must be a file named {STATUS_NAME}", file=sys.stderr)
        return 2
    if not args.interval_s > 0:
        print("refused: --interval-s must be positive", file=sys.stderr)
        return 2
    return run_watch(lambda: read_status(args.status), args.seconds, args.interval_s)


if __name__ == "__main__":
    sys.exit(main())
