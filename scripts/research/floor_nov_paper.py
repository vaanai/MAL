#!/usr/bin/env python3
"""floor_nov_paper: paper replay of l1-floor-v2 (HostV2, tools/arena_rules_v2) over a closed tip-tape slice, priced at stakes 0.10 and 0.25 with the kit's fee stack
(arena_stack_v2 fields) and send = 160,000 lamports per send by default (p155 + base; the earlier 1,673,840 included 1,513,840 of refundable token-ATA rent and over-charged). PAPER only; scratch registry and ledger under --root (outside /data/mal/arena).
No table seed: the pool table is built from the slice itself (pools that graduated before the slice start are missing, disclosed in the report).
Usage: floor_nov_paper.py --root R --tape-dir T --from-ms A --to-ms B [--send 160000]
"""
import argparse, dataclasses, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tools import arena_rules as R
from tools import floor_lane_host as H
from tools.arena_paper import canon
from tools.arena_rules_v2 import HostV2, load_rules_v2
from tools.arena_stack_v2 import stack_config_for
from tools.arena_state import WARM_MIN_DEFAULT, ArenaState
sys.path.insert(0, str(Path(__file__).resolve().parent))
from floor_replay_proof_reg import scratch_register  # noqa: E402
from floor_tape_paths import require_files_ok  # noqa: E402
from floor_gap_range_engine import GapRangeEngine  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True); ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--from-ms", type=int, required=True); ap.add_argument("--to-ms", type=int, required=True)
    ap.add_argument("--send", type=int, default=160_000)
    ap.add_argument("--set", nargs="*", default=[], help="variant overrides on tools.arena_rule_l1 module constants, NAME=value (e.g. Q_STAR=0.75e9 AGE_LO=43200); the file is not edited")
    ap.add_argument("--gap-exclusion", choices=("on", "off"), default="on", help="research only. on (default) = engine behaviour, positions whose hold overlaps a data_loss gap are excluded (feed_gap). off = price them through the holes; rows carry gap_overlap/gap_holes either way")
    ap.add_argument("--ts-variant", action="store_true", help="research only: also price a 24 h time-stop shadow (ledger/<bot>-ts) for every gap-overlap position that exited by take-profit")
    a = ap.parse_args()
    require_files_ok(a.tape_dir)
    from tools import arena_rule_l1 as _L
    overrides = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        if not (hasattr(_L, k) and k in ("Q_STAR", "AGE_LO", "AGE_HI", "VOL_MIN_LAMPORTS", "R6_MAX")):  # explicit (an assert vanishes under python -O)
            raise SystemExit("refusing override of %r" % k)
        setattr(_L, k, float(v) if k != "AGE_LO" and k != "AGE_HI" else int(float(v)))
        overrides[k] = getattr(_L, k)
        print("override", k, getattr(_L, k), flush=True)
    if R._inside(a.root, R.ARENA_OUTPUT_ROOT):
        raise SystemExit("refusing to write inside the arena output root")
    root = Path(a.root); root.mkdir(parents=True, exist_ok=True)
    # the ledger is registered under the pinned FROZEN_SHA; a variant must not pass for the frozen rule, so the header says so (the reader prints it)
    hdr = {"variant": overrides, "frozen_rule": not overrides, "registered_frozen_sha_is_not_the_rule": bool(overrides), "send": a.send}
    (root / "variant.json").write_text(json.dumps(hdr, indent=1, sort_keys=True) + "\n")  # in --root, not in ledger/ (the engine owns that dir)
    ledger, registry = str(root / "ledger"), str(root / "bots.json")
    rules = load_rules_v2([H.BOT_ID])
    scratch_register(registry, a.from_ms)
    cfg = dataclasses.replace(stack_config_for(2, R.DEFAULT_FEED_DELAY_SLOTS), send=a.send, stakes_sol=(0.10, 0.25))
    state = ArenaState(a.tape_dir, start_ms=a.from_ms, warm_min=WARM_MIN_DEFAULT)
    engine = GapRangeEngine(ledger, registry, cfg=cfg, gap_exclusion=(a.gap_exclusion == "on"), ts_variant=a.ts_variant)
    hc = R.HostConfig(root=str(root), registry=registry, frozen_sha=H.FROZEN_SHA, clock_start_ms=a.from_ms, live=False, verbose=True)
    host = HostV2(hc, rules, state, engine)
    print(canon({"resumed": host.resume(), "send": cfg.send, "stakes": cfg.stakes_sol}), flush=True)
    host.run(a.from_ms, a.to_ms)
    print(canon({"done": host.status(), "ts_shadows_unpriced": engine.ts_open(), "excluded_feed_gap": engine.counters.get("excluded_feed_gap", 0)}), flush=True)


if __name__ == "__main__":
    main()
