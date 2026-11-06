#!/usr/bin/env python3
"""Write s1t's window end after a B2 HALT (DEC-031 Amendment 29 item 5), through tools/a3_halt.py's explicit-id end write.

usage: python3 -m tools.s1t_b2_apply --arena-root ROOT --halt-run-utc ISO [--bot-id h5v1q50-boostfloor-s1t]

Sets window_end_ms and stopped_ms of that ONE bot in ROOT/bots.json to the epoch ms of --halt-run-utc, with
a3_halt.halt_books semantics (same registry lock and atomic write as the scorer; set once, an earlier end is never moved later,
no line removed). Idempotent. Only the id h5v1q50-boostfloor-s1t is accepted. Prints one json line.
Exit codes: 0 written or already ended at or before that instant; 2 refused (bad argument, bot not registered, no registry);
12 write failed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools import a3_halt

BOT_ID = "h5v1q50-boostfloor-s1t"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arena-root", required=True)
    ap.add_argument("--halt-run-utc", required=True)
    ap.add_argument("--bot-id", default=BOT_ID)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if a.bot_id != BOT_ID:
        print("S1T_B2_REFUSED only %s may be ended here" % BOT_ID, file=sys.stderr)
        return 2
    try:
        t = a.halt_run_utc[:-1] + "+00:00" if a.halt_run_utc.endswith("Z") else a.halt_run_utc
        d = datetime.fromisoformat(t)
        if d.tzinfo is None:
            raise ValueError("no zone")
        at_ms = int(d.astimezone(timezone.utc).timestamp() * 1000)
    except ValueError as e:
        print("S1T_B2_REFUSED bad --halt-run-utc: %s" % e, file=sys.stderr)
        return 2
    registry = Path(a.arena_root) / "bots.json"
    if not registry.exists():
        print("S1T_B2_REFUSED no registry at %s" % registry, file=sys.stderr)
        return 2
    try:
        out = a3_halt.halt_books(registry, at_ms, [a.bot_id])
    except ValueError as e:  # a3_halt.Invalid
        print("S1T_B2_REFUSED %s" % str(e)[:200], file=sys.stderr)
        return 2
    except OSError as e:
        print("S1T_B2_WRITE_FAILED %s" % type(e).__name__, file=sys.stderr)
        return 12
    if out["unregistered"]:
        print("S1T_B2_REFUSED %s is not registered" % a.bot_id, file=sys.stderr)
        return 2
    print(json.dumps({"rule": "s1t_b2", "at_ms": at_ms, **out}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
