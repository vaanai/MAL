#!/usr/bin/env python3
"""dbc_p18_gate: carry "LOOK CONSUMED" from research-0 (where the m10 read writes its one-look marker) to mal-fast-0 (where the P18 process runs). PAPER ONLY.

Problem: tools.dbc_p18_run.start_guard lstat()s the marker /data/mal/hunt-1008/push-1016/hunt29/dbc/HOLDOUT_READ_DONE_v3, which exists only on research-0. On fast-0 the guard would
refuse forever. start_guard is NOT changed. It already takes a `marker` argument, so the P18 live entry (tools.dbc_p18_live) passes a LOCAL FLAG file as that marker, and this
script is the only thing that writes the flag.

    # research-0 job J1 (exit 0 = marker present; never opens the marker, lstat only; polls until --deadline-min, then exits 1)
    python3 -m tools.dbc_p18_gate probe --deadline-min 30
    # fast-0 job J2, chained after J1 (see the PR body for the cross-machine question): writes the flag only if now >= 2026-11-01T00:10Z
    python3 -m tools.dbc_p18_gate arm --flag ~/p18/LOOK_CONSUMED.flag --probe-job j_XXXX
    # fast-0, read by the live entry
    python3 -m tools.dbc_p18_gate check --flag ~/p18/LOOK_CONSUMED.flag

The flag proves only that a job claiming a successful marker probe ran on fast-0 after the time floor. It carries no outcome of the read and nothing is read from the marker.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tools import dbc_p18_run as P

KIND = "p18-look-consumed-flag"
EXIT_NO_MARKER, EXIT_REFUSED = 1, 2


def marker_probe(*, path: str = P.MARKER, lexists: Callable[[str], bool] = os.path.lexists, deadline_s: float = 0.0, poll_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Existence test of the one-look marker by lstat only (the file is never opened). Polls until `deadline_s` seconds have passed. Only P.MARKER is accepted."""
    if path != P.MARKER:
        raise ValueError("only the one-look marker path is probed")
    t0 = clock()
    n = 0
    while True:
        n += 1
        if lexists(path):
            return {"marker_exists": True, "polls": n, "waited_s": round(clock() - t0, 1)}
        if clock() - t0 >= deadline_s:
            return {"marker_exists": False, "polls": n, "waited_s": round(clock() - t0, 1)}
        sleep(poll_s)


def arm(flag: str, probe_job: str, now: datetime) -> dict[str, Any]:
    """Write the local flag atomically. Refused before the start guard's own time floor (the marker cannot exist before it)."""
    if now < P.EARLIEST_LIVE:
        raise P.StartRefused(f"arm refused before {P.EARLIEST_LIVE:%Y-%m-%dT%H:%MZ}")
    if not probe_job or any(c in probe_job for c in " \n\r\t/"):
        raise P.StartRefused("--probe-job must be the MiScusi job id of the research-0 marker probe")
    if flag == P.MARKER or os.path.realpath(flag) == os.path.realpath(P.MARKER):
        raise P.StartRefused("the flag path must not be the marker")
    body = {"kind": KIND, "probe_job": probe_job, "armed_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "marker_path": P.MARKER, "marker_opened": False}
    p = Path(flag)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(body, sort_keys=True))
    os.replace(tmp, p)
    return body


def flag_valid(flag: str) -> bool:
    """True if `flag` is the marker itself (research-0 use: lstat only, never opened) or a flag file written by `arm` (structure checked; opens only the flag)."""
    if flag == P.MARKER:
        return os.path.lexists(flag)
    if os.path.realpath(flag) == os.path.realpath(P.MARKER):
        return os.path.lexists(flag)
    try:
        d = json.loads(Path(flag).read_text())
    except (OSError, ValueError):
        return False
    if not (isinstance(d, dict) and d.get("kind") == KIND and d.get("marker_path") == P.MARKER and d.get("marker_opened") is False and d.get("probe_job")):
        return False
    try:
        armed = datetime.strptime(d["armed_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return False
    return armed >= P.EARLIEST_LIVE


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--deadline-min", type=float, default=0.0)
    p.add_argument("--poll-s", type=float, default=30.0)
    a = sub.add_parser("arm")
    a.add_argument("--flag", required=True)
    a.add_argument("--probe-job", required=True)
    c = sub.add_parser("check")
    c.add_argument("--flag", required=True)
    ns = ap.parse_args(argv)
    if ns.cmd == "probe":
        res = marker_probe(deadline_s=ns.deadline_min * 60.0, poll_s=ns.poll_s)
        print(json.dumps(res))
        return 0 if res["marker_exists"] else EXIT_NO_MARKER
    if ns.cmd == "arm":
        try:
            print(json.dumps(arm(os.path.expanduser(ns.flag), ns.probe_job, datetime.now(timezone.utc))))
        except P.StartRefused as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return EXIT_REFUSED
        return 0
    ok = flag_valid(os.path.expanduser(ns.flag))
    print(json.dumps({"flag_valid": ok}))
    return 0 if ok else EXIT_NO_MARKER


if __name__ == "__main__":
    sys.exit(main())
