#!/usr/bin/env python3
"""r4b-c0-smoke-fixture: build the SYNTHETIC fixture for the keyless smoke of the r4-b C0 executor (scripts/ops/r4b-c0-dry-checklist.sh).

    python3 -I -B r4b-c0-smoke-fixture.py <pinned-tree> <out-dir>      # prints the config path (last line)

What it writes under <out-dir> (a fresh mktemp dir; never /var/lib/mal-r4b-c0, never /data, never a real decision root): a SYNTHETIC marker file, a decision root
holding a hand-written r4-b decision log (bot r4b-exp027, written with the PINNED tree's own tools.arena_declog so the hash chain is the real format), a state dir
and a dry config. The mints are named SYNmint*: no real mint, pool, decision, ledger or record is read or written. The log: a `start` row repeating the synthetic
frozen sha, three entries in rank order at one mark, one rank exit at the next mark (the r4b_live row format of tools/test_r4b_arena_executor.py).
The executor's `replay-check` on it must print ok true (orders equal the log's decisions, no refusal, no latch) and `run --once` must print mode=dry.
Stdlib only, plus the pinned tree's tools.arena_declog (stdlib only itself).
"""
from __future__ import annotations

import json
import os
import sys

BOT = "r4b-exp027"
SYN_SHA = "5e" * 20          # a synthetic install sha: 40 hex, not a commit of this repo
MARKER = "SYNTHETIC_R4B_C0_FIXTURE"
TM0 = 1_786_003_200          # 2026-08-06T08:00:00Z, a mark (multiple of 3,600 s) far from every live window
V0 = 17_600_000_000          # inside the PumpSwap canonical V band [17.5e9, 17.7e9]
SD0 = 438_000_000


def build(tree: str, out: str) -> str:
    sys.path.insert(0, tree)
    from tools.arena_declog import DecisionLog  # noqa: E402  (the pinned tree's own writer)

    root, state = os.path.join(out, "declog"), os.path.join(out, "state")
    os.makedirs(root, exist_ok=True)
    os.makedirs(state, exist_ok=True)
    os.chmod(state, 0o700)
    open(os.path.join(out, MARKER), "w").close()
    d = DecisionLog(root)
    d.append(BOT, TM0 * 1000 - 5000, {"kind": "start", "book": "r4-b", "exp": "EXP-027", "mode": "dry", "schema": "r4b_live_v1", "frozen_sha": SYN_SHA})
    for rank, m in enumerate(("SYNmintA", "SYNmintB", "SYNmintC"), start=1):
        d.append(BOT, TM0 * 1000 + 1200 + rank, {"kind": "entry", "order_id": f"{m}:{TM0}", "pool": "PDA_" + m, "meta": {"mint": m, "v0": V0, "mbt": TM0 - 7200, "s0": 1},
                                                 "stake_sol": 0.25, "decision_slot": SD0, "t_mark": TM0, "rank": rank, "score": 0.5})
    d.append(BOT, (TM0 + 3600) * 1000 + 1100, {"kind": "exit", "order_id": f"SYNmintA:{TM0}", "mint": "SYNmintA", "reason": "rank", "t_mark": TM0 + 3600,
                                               "decision_slot": SD0 + 9000})
    cfg = {"mode": "dry", "bot_id": BOT, "frozen_sha": SYN_SHA, "root": root, "state_dir": state, "end_ms": (TM0 + 30 * 86400) * 1000, "slot_s": 0.4,
           "funded_lamports": 350_000_000, "dry_balance_lamports": 350_000_000, "stop_file": os.path.join(state, "STOP"), "halt_file": os.path.join(state, "HALT")}
    cp = os.path.join(out, "synthetic-dry.json")
    with open(cp, "w") as fh:
        fh.write(json.dumps(cfg, sort_keys=True) + "\n")
    return cp


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: r4b-c0-smoke-fixture.py <pinned-tree> <out-dir>")
    print(build(os.path.realpath(sys.argv[1]), os.path.realpath(sys.argv[2])))
