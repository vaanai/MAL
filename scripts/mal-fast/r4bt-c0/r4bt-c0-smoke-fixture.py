#!/usr/bin/env python3
"""r4bt-c0-smoke-fixture: build the SYNTHETIC fixture for the keyless smoke of the r4-b-T C0 executor (scripts/ops/r4bt-c0-dry-checklist.sh).

    python3 -I -B r4bt-c0-smoke-fixture.py <pinned-tree> <out-dir>      # prints the config path (last line)

What it writes under <out-dir> (a fresh mktemp dir; never /var/lib/mal-r4b-c0, never /data, never a real declog): a SYNTHETIC_FIXTURE_ROOT marker in the decision root (the
executor's synthetic-root exemption from the read-record gate, DRY only), a hand-written decision log (bot r4bt-a1, written with the PINNED tree's own tools.arena_declog so
the hash chain is the real format: a `start` row repeating W and the change_id, three SYNmint entries, one rank exit), a state dir and a dry config in the executor's
r4-b-T profile shape (W and the registered change_id; the caps; the 505,000 fee; read_result_sha256 / qp_comment_id synthetic). The mints are named SYNmint*: no real
mint, pool, decision, ledger or record is read or written, and nothing here touches the real declog or the tape.
The executor's config check on it (`python -m tools.r4bt_c0_executor --config <cfg>`) must print ok true with the live gates open (none of them exist), and with --live it
must exit 2 with live_not_built. Stdlib only, plus the pinned tree's tools.arena_declog (stdlib only itself).
"""
from __future__ import annotations

import json
import os
import sys

BOT = "r4bt-a1"
W = "17e3b05a7696900cda7fcf93e4af4008d8fa0184"
CHANGE_ID = "r4bt-a1-1107a"
MARKER = "SYNTHETIC_FIXTURE_ROOT"      # tools.hv4_live_c0.SYNTHETIC_MARKER (a test compares)
TM0 = 1_786_003_200          # 2026-08-06T08:00:00Z, a mark (multiple of 3,600 s) far from every live window
V0 = 17_600_000_000
SD0 = 438_000_000


def build(tree: str, out: str) -> str:
    sys.path.insert(0, tree)
    from tools.arena_declog import DecisionLog  # noqa: E402  (the pinned tree's own writer)

    root, state = os.path.join(out, "declog"), os.path.join(out, "state")
    os.makedirs(root, exist_ok=True)
    os.makedirs(state, exist_ok=True)
    os.chmod(state, 0o700)
    open(os.path.join(root, MARKER), "w").close()
    d = DecisionLog(root)
    d.append(BOT, TM0 * 1000 - 5000, {"kind": "start", "book": "r4-b-T", "mode": "dry", "frozen_sha": W, "change_id": CHANGE_ID})
    for rank, m in enumerate(("SYNmintA", "SYNmintB", "SYNmintC"), start=1):
        d.append(BOT, TM0 * 1000 + 1200 + rank, {"kind": "entry", "order_id": f"{m}:{TM0}", "pool": "PDA_" + m, "meta": {"mint": m, "v0": V0, "mbt": TM0 - 7200, "s0": 1},
                                                 "stake_sol": 0.05, "decision_slot": SD0, "t_mark": TM0, "rank": rank, "score": 0.5})
    d.append(BOT, (TM0 + 3600) * 1000 + 1100, {"kind": "exit", "order_id": f"SYNmintA:{TM0}", "mint": "SYNmintA", "reason": "rank", "t_mark": TM0 + 3600,
                                               "decision_slot": SD0 + 9000})
    cfg = {"mode": "dry", "bot_id": BOT, "frozen_sha": W, "change_id": CHANGE_ID, "root": root, "state_dir": state, "end_ms": (TM0 + 14 * 86400) * 1000, "slot_s": 0.4,
           "funded_lamports": 350_000_000, "dry_balance_lamports": 350_000_000, "stop_file": os.path.join(state, "STOP"), "halt_file": os.path.join(state, "HALT"),
           "stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 100_000_000, "total_loss_lamports": 200_000_000,
           "send_fee_lamports": 505_000, "twin_dir": state, "twin_interval_s": 300, "twin_replay_s": 300, "twin_heartbeat_max_ms": 900_000,
           "a3_carry_path": os.path.join(out, "a3-carry.json"), "read_record_path": os.path.join(out, "read-record.json"),
           "read_result_sha256": "5e" * 32, "qp_comment_id": "000000000",
           "owner_file": os.path.join(out, "CANARY_DEC.md"), "live_ok_path": os.path.join(out, "LIVE_OK"), "tier_path": os.path.join(out, "TIER")}
    cp = os.path.join(out, "synthetic-dry.json")
    with open(cp, "w") as fh:
        fh.write(json.dumps(cfg, sort_keys=True) + "\n")
    return cp


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: r4bt-c0-smoke-fixture.py <pinned-tree> <out-dir>")
    print(build(os.path.realpath(sys.argv[1]), os.path.realpath(sys.argv[2])))
