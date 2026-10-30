#!/usr/bin/env python3
"""hv4-c0-smoke-fixture: build the SYNTHETIC fixture for the keyless `--once` dry start of the HV4 C0 bridge (scripts/ops/hv4-c0-dry-checklist.sh). Stdlib only.

    python3 -I -B hv4-c0-smoke-fixture.py <pinned-tree> <out-dir>      # prints the config path

What it writes under <out-dir> (a fresh mktemp dir, never under /data/mal/arena): a root with the SYNTHETIC_FIXTURE_ROOT marker and an empty decisions dir, a reads dir
holding a SYNTHETIC recorded-read record for the synthetic bot, a state dir, and a dry config. It reads NO decision row, NO ledger, NO real record and NO real
bot's file. The bot is hv4-SYN-X: it passes the arm rule but is not a real arm, so the rehearsal can never start a real arm's 14-day clock (QP #965 N-C).
The window file for hv4-SYN-X must already exist (root creates it; the bridge unit cannot write the window dir): its first_start_ms sets the end_ms here.
"""
from __future__ import annotations

import json
import os
import sys
import time

BOT = "hv4-SYN-X"


def build(tree: str, out: str) -> str:
    sys.path.insert(0, tree)
    from tools import hv4_live_c0 as c0

    first = None
    try:
        with open(os.path.join(c0.WINDOW_DIR, BOT + ".json"), "rb") as fh:
            first = int(json.loads(fh.read())["first_start_ms"])
    except (OSError, ValueError, KeyError, TypeError):
        pass  # the start will refuse on the window file itself; end_ms only needs to be a positive number here
    end_ms = (first if first is not None else int(time.time() * 1000)) + 86_400_000
    root, reads, state = (os.path.join(out, x) for x in ("root", "reads", "state"))
    os.makedirs(os.path.join(root, "decisions", BOT), exist_ok=True)
    open(os.path.join(root, c0.SYNTHETIC_MARKER), "w").close()
    os.makedirs(reads, exist_ok=True)
    os.chmod(reads, 0o755)
    rec = {"bot_id": BOT, "status": "PASS", "read_boundary_ms": 1_700_000_000_000, "attempts": 0, "model_ids": [], "am15": {"rule": "synthetic"},
           "frozen_sha": c0.FROZEN_SHA}
    rp = os.path.join(reads, BOT + ".json")
    with open(rp, "w") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    os.chmod(rp, 0o644)
    os.makedirs(state, exist_ok=True)
    os.chmod(state, 0o700)
    cfg = {"profile": c0.PROFILE, "mode": "dry", "bot_id": BOT, "frozen_sha": c0.FROZEN_SHA, "root": root, "state_dir": state, "reads_dir": reads, "end_ms": end_ms,
           "max_hold_ms": 7_200_000, "stop_file": os.path.join(state, "STOP"), "halt_file": os.path.join(state, "HALT")}
    cp = os.path.join(out, "synthetic-dry.json")
    with open(cp, "w") as fh:
        fh.write(json.dumps(cfg, sort_keys=True) + "\n")
    return cp


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: hv4-c0-smoke-fixture.py <pinned-tree> <out-dir>")
    print(build(os.path.realpath(sys.argv[1]), os.path.realpath(sys.argv[2])))
