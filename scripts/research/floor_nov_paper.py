#!/usr/bin/env python3
"""floor_nov_paper: paper replay of l1-floor-v2 (HostV2, tools/arena_rules_v2) over a closed tip-tape slice, priced at stakes 0.10 and 0.25 (default; --stakes adds more, e.g. 0.5, 1.0) with the kit's fee stack
(arena_stack_v2 fields) and send = 160,000 lamports per send by default (p155 + base; the earlier 1,673,840 included 1,513,840 of refundable token-ATA rent and over-charged). PAPER only; scratch registry and ledger under --root (outside /data/mal/arena).
No table seed: the pool table is built from the slice itself (pools that graduated before the slice start are missing, disclosed in the report).
Usage: floor_nov_paper.py --root R --tape-dir T --from-ms A --to-ms B [--send 160000] [--stakes 0.10,0.25] [--pricing-lat-entry-s {3.010,4.085,5.590}]
--pricing-lat-entry-s (QP ruling 2026-11-14 osb replay screen, section 2; the engine option of PR #1390): prices the deciding entry leg at the declared TOTAL decision-to-landing time. Pricing only: the host's decisions are the strict run's.
Absent = byte-identical output to before. The value is recorded in variant.json and in the stack block of every row; a run refuses a --root that already holds a run with another value (or a strict run, and the reverse).
Label for any output: replay screen, paper, in-sample on 2026-11-02..11-11 (variants chosen there); not a gate read, not forward evidence; latency columns are model pricing of the same decisions at the declared live latencies (job #2702).
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


def parse_stakes(text):
    """--stakes 0.10,0.25,0.5,1.0 -> tuple of floats. 2-6 values, each in (0, 2.0], strictly ascending, must include 0.10 and 0.25 (the A/B table reads those two)."""
    try:
        v = tuple(float(x) for x in text.split(","))
    except ValueError:
        raise SystemExit("refusing --stakes %r: not a comma list of numbers" % text)
    if not 2 <= len(v) <= 6:
        raise SystemExit("refusing --stakes: need 2 to 6 values, got %d" % len(v))
    if any(not (0 < x <= 2.0) or x != x for x in v):
        raise SystemExit("refusing --stakes: every value must be in (0, 2.0]")
    if any(b <= a for a, b in zip(v, v[1:])):
        raise SystemExit("refusing --stakes: values must be strictly ascending")
    if 0.10 not in v or 0.25 not in v:
        raise SystemExit("refusing --stakes: must include 0.10 and 0.25")
    return v


LAT_CHOICES = (3.010, 4.085, 5.590)  # the three values declared before any output (QP ruling 2026-11-14 osb latency twin, section 3); no other value is accepted


def refuse_mixed_root(root, lat):
    """a run dir holds one pricing: refuse when its variant.json (written by an earlier run) names another pricing_lat_entry_s than this run's (absent = strict)"""
    vp = Path(root) / "variant.json"
    if not vp.exists():
        return
    try:
        old = json.loads(vp.read_text()).get("pricing_lat_entry_s")
    except ValueError:
        raise SystemExit("refusing --root %s: its variant.json is not readable" % root)
    if old != lat:
        raise SystemExit("refusing --root %s: it holds a run with pricing_lat_entry_s=%r, this run has %r (None = strict); use a separate --root" % (root, old, lat))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True); ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--from-ms", type=int, required=True); ap.add_argument("--to-ms", type=int, required=True)
    ap.add_argument("--send", type=int, default=160_000)
    ap.add_argument("--stakes", default="0.10,0.25", help="comma list of paper stakes in SOL (default 0.10,0.25; 2-6 values in (0, 2.0], ascending, must include 0.10 and 0.25)")
    ap.add_argument("--set", nargs="*", default=[], help="variant overrides on tools.arena_rule_l1 module constants, NAME=value (e.g. Q_STAR=0.75e9 AGE_LO=43200); the file is not edited")
    ap.add_argument("--gap-exclusion", choices=("on", "off"), default="on", help="research only. on (default) = engine behaviour, positions whose hold overlaps a data_loss gap are excluded (feed_gap). off = price them through the holes; rows carry gap_overlap/gap_holes either way")
    ap.add_argument("--ts-variant", action="store_true", help="research only: also price a 24 h time-stop shadow (ledger/<bot>-ts) for every gap-overlap position that exited by take-profit")
    ap.add_argument("--pricing-lat-entry-s", type=float, choices=LAT_CHOICES, default=None, help="research only: price the deciding entry leg at this total decision-to-landing time in seconds (3.010, 4.085 or 5.590); decisions unchanged; absent = strict pricing")
    a = ap.parse_args()
    lat = a.pricing_lat_entry_s
    stakes = parse_stakes(a.stakes)
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
    refuse_mixed_root(a.root, lat)
    root = Path(a.root); root.mkdir(parents=True, exist_ok=True)
    # the ledger is registered under the pinned FROZEN_SHA; a variant must not pass for the frozen rule, so the header says so (the reader prints it)
    hdr = {"variant": overrides, "frozen_rule": not overrides, "registered_frozen_sha_is_not_the_rule": bool(overrides), "send": a.send}
    if lat is not None:
        hdr["pricing_lat_entry_s"] = lat
    (root / "variant.json").write_text(json.dumps(hdr, indent=1, sort_keys=True) + "\n")  # in --root, not in ledger/ (the engine owns that dir)
    ledger, registry = str(root / "ledger"), str(root / "bots.json")
    rules = load_rules_v2([H.BOT_ID])
    scratch_register(registry, a.from_ms)
    cfg = dataclasses.replace(stack_config_for(2, R.DEFAULT_FEED_DELAY_SLOTS), send=a.send, stakes_sol=stakes)
    state = ArenaState(a.tape_dir, start_ms=a.from_ms, warm_min=WARM_MIN_DEFAULT)
    engine = GapRangeEngine(ledger, registry, cfg=cfg, gap_exclusion=(a.gap_exclusion == "on"), ts_variant=a.ts_variant, **({} if lat is None else {"pricing_lat_entry_s": lat}))
    hc = R.HostConfig(root=str(root), registry=registry, frozen_sha=H.FROZEN_SHA, clock_start_ms=a.from_ms, live=False, verbose=True)
    host = HostV2(hc, rules, state, engine)
    print(canon({"resumed": host.resume(), "send": cfg.send, "stakes": cfg.stakes_sol}), flush=True)
    host.run(a.from_ms, a.to_ms)
    print(canon({"done": host.status(), "ts_shadows_unpriced": engine.ts_open(), "excluded_feed_gap": engine.counters.get("excluded_feed_gap", 0)}), flush=True)


if __name__ == "__main__":
    main()
