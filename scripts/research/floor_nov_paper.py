#!/usr/bin/env python3
"""floor_nov_paper: paper replay of l1-floor-v2 (HostV2, tools/arena_rules_v2) over a closed tip-tape slice, priced at stakes 0.10 and 0.25 with the kit's fee stack
(arena_stack_v2 fields) and send = 1,673,840 lamports (the live fixed send cost). PAPER only; scratch registry and ledger under --root (outside /data/mal/arena).
No table seed: the pool table is built from the slice itself (pools that graduated before the slice start are missing, disclosed in the report).
Usage: floor_nov_paper.py --root R --tape-dir T --from-ms A --to-ms B [--send 1673840]
"""
import argparse, dataclasses, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tools import arena_rules as R
from tools import floor_lane_host as H
from tools.arena_paper import ArenaEngine, canon
from tools.arena_rules_v2 import HostV2, load_rules_v2
from tools.arena_stack_v2 import stack_config_for
from tools.arena_state import WARM_MIN_DEFAULT, ArenaState
sys.path.insert(0, str(Path(__file__).resolve().parent))
from floor_replay_proof_reg import scratch_register  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True); ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--from-ms", type=int, required=True); ap.add_argument("--to-ms", type=int, required=True)
    ap.add_argument("--send", type=int, default=1_673_840)
    a = ap.parse_args()
    if R._inside(a.root, R.ARENA_OUTPUT_ROOT):
        raise SystemExit("refusing to write inside the arena output root")
    root = Path(a.root); root.mkdir(parents=True, exist_ok=True)
    ledger, registry = str(root / "ledger"), str(root / "bots.json")
    rules = load_rules_v2([H.BOT_ID])
    scratch_register(registry, a.from_ms)
    cfg = dataclasses.replace(stack_config_for(2, R.DEFAULT_FEED_DELAY_SLOTS), send=a.send, stakes_sol=(0.10, 0.25))
    state = ArenaState(a.tape_dir, start_ms=a.from_ms, warm_min=WARM_MIN_DEFAULT)
    engine = ArenaEngine(ledger, registry, cfg=cfg)
    hc = R.HostConfig(root=str(root), registry=registry, frozen_sha=H.FROZEN_SHA, clock_start_ms=a.from_ms, live=False, verbose=True)
    host = HostV2(hc, rules, state, engine)
    print(canon({"resumed": host.resume(), "send": cfg.send, "stakes": cfg.stakes_sol}), flush=True)
    host.run(a.from_ms, a.to_ms)
    print(canon({"done": host.status()}), flush=True)


main()
