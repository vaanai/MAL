#!/usr/bin/env python3
"""floor_replay_proof helper (called by scripts/research/floor-replay-proof.sh). PAPER / replay only: no key, no wallet, no send.

  paper   run the arena paper host (tools/arena_rules_v2.HostV2, bot l1-floor-v2, stack v2) on a closed tape slice, with the pool table seeded from the same tape hours the lane
          replay uses (the CLI's replay mode does not seed a table; the live arena book builds its table from the whole tape, so a seed from the same hour is the like-for-like
          start). Writes only inside --root, which must be outside /data/mal/arena.
  compare read the lane and paper decision roots and write the counts, the md5s and the verdict as JSON on stdout. Counts, md5s and structural checks only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools import arena_rules as R  # noqa: E402
from tools import floor_lane_host as H  # noqa: E402
from tools.arena_declog import DecisionLog  # noqa: E402
from tools.arena_paper import ArenaEngine, canon  # noqa: E402
from tools.arena_rules_v2 import HostV2, load_rules_v2  # noqa: E402
from tools.arena_stack_v2 import stack_config_for  # noqa: E402
from tools.arena_state import WARM_MIN_DEFAULT, ArenaState  # noqa: E402

BOT = H.BOT_ID
CHAIN = ("seq", "prev", "sha")


def scratch_register(registry: str, from_ms: int) -> None:
    """Register l1-floor-v2 in a SCRATCH registry under the output root, with the registration instant set just before the replayed clock (a replay cannot register in the real
    clock's past; the test-only override is allowed only when MAL_ARENA_TEST=1, which is set here for this scratch registry alone; the real registry is never opened)."""
    import os
    if R._inside(registry, R.ARENA_OUTPUT_ROOT):
        raise R.HostError("refusing to register inside the arena output root")
    os.environ["MAL_ARENA_TEST"] = "1"
    try:
        R.register_rules(load_rules_v2([BOT]), registry, H.FROZEN_SHA, from_ms, now_ms=from_ms - 1, verify_sha=False)
    finally:
        os.environ.pop("MAL_ARENA_TEST", None)


def paper(a: argparse.Namespace) -> int:
    for p in (a.root,):
        if R._inside(p, R.ARENA_OUTPUT_ROOT):
            raise R.HostError(f"refusing to write inside {R.ARENA_OUTPUT_ROOT}")
    if H.parse_hour(a.seed_from_hour) > a.from_ms - H.SEED_MIN_H * H.HOUR_MS_:
        raise R.HostError(f"--seed-from-hour must be at least {H.SEED_MIN_H} h before --from-ms")
    root = Path(a.root)
    ledger, registry = str(root / "ledger"), str(root / "bots.json")
    rules = load_rules_v2([BOT])
    scratch_register(registry, a.from_ms)
    warm = WARM_MIN_DEFAULT
    state = ArenaState(a.tape_dir, start_ms=a.from_ms - warm * R.MIN_MS, warm_min=warm)
    engine = ArenaEngine(ledger, registry, cfg=stack_config_for(2, R.DEFAULT_FEED_DELAY_SLOTS))
    cfg = R.HostConfig(root=str(root), registry=registry, frozen_sha=H.FROZEN_SHA, clock_start_ms=a.from_ms, live=False, verbose=True)
    host = HostV2(cfg, rules, state, engine)
    n = H.rebuild_table_since(host.table, a.tape_dir, a.from_ms - warm * R.MIN_MS, H.parse_hour(a.seed_from_hour))
    print(canon({"table_seeded_rows": n, "seed_from_hour": a.seed_from_hour}), flush=True)
    print(canon({"resumed": host.resume()}), flush=True)
    host.run(a.from_ms, a.to_ms)
    print(canon({"done": host.status(), "decision_md5": H.decision_digest(str(root))}), flush=True)
    return 0


def _strip(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in CHAIN}


def _md5(rows: list) -> str:
    return hashlib.md5(canon(rows).encode()).hexdigest()


def compare(a: argparse.Namespace) -> int:
    lane_rows = DecisionLog(a.lane).rows(BOT)
    pap_rows = DecisionLog(a.paper).rows(BOT)
    l_ent = {r["order_id"]: r for r in lane_rows if r.get("kind") == "entry"}
    p_ent = {r["order_id"]: r for r in pap_rows if r.get("kind") == "entry"}
    l_exit = {r["order_id"]: r for r in lane_rows if r.get("kind") == "exit"}
    p_exit = {r["order_id"]: r for r in pap_rows if r.get("kind") == "exit"}
    refs = [r for r in lane_rows if r.get("kind") == H.KIND_GAP_REFUSED]
    gap_ref = [r for r in refs if r.get("reason") == "feed_gap"]
    blk_ref = [r for r in refs if str(r.get("reason", "")).startswith("pool_blocked")]
    # (c) lane entries are paper entries: same order id (a hash of bot, pool, decision ms, decision slot), and the same pool / ms / slot fields
    lane_not_in_paper = sorted(o for o in l_ent if o not in p_ent)
    field_diff = sorted(o for o in l_ent if o in p_ent and _strip(l_ent[o]) != _strip(p_ent[o]))
    # (d) every paper entry the lane does not hold must be explained by a lane refusal on the same pool at the same trigger slot or instant
    ref_keys = {(r["pool"], r.get("trigger_slot")) for r in refs} | {(r["pool"], r.get("trigger_ms")) for r in refs}
    missing = [o for o in p_ent if o not in l_ent]
    unexplained = sorted(o for o in missing if (p_ent[o]["pool"], p_ent[o].get("decision_slot")) not in ref_keys and (p_ent[o]["pool"], p_ent[o]["ms"]) not in ref_keys)
    # the clean part: paper entries with no lane refusal at their trigger; on it the two sets must be equal
    explained = {o for o in missing if o not in set(unexplained)}
    clean_paper = {o for o in p_ent if o not in explained}
    clean_equal = clean_paper == set(l_ent)
    both_ent = sorted(o for o in l_ent if o in p_ent)
    exit_diff = sorted(o for o in both_ent if (o in l_exit) != (o in p_exit) or (o in l_exit and _strip(l_exit[o]) != _strip(p_exit[o])))
    lane_md5, paper_md5 = H.decision_digest(a.lane), H.decision_digest(a.paper)
    sub = [_strip(p_ent[o]) for o in sorted(l_ent)] + [_strip(p_exit[o]) for o in sorted(l_ent) if o in p_exit]
    sub_l = [_strip(l_ent[o]) for o in sorted(l_ent)] + [_strip(l_exit[o]) for o in sorted(l_ent) if o in l_exit]
    sub_equal = _md5(sub) == _md5(sub_l)
    lane_chain = DecisionLog(a.lane).verify(BOT)
    paper_chain = DecisionLog(a.paper).verify(BOT)
    ok = not lane_not_in_paper and not field_diff and not unexplained and clean_equal and not exit_diff and sub_equal
    m = {"lane_entries": len(l_ent), "lane_exits": len(l_exit), "paper_entries": len(p_ent), "paper_exits": len(p_exit),
         "lane_decision_md5": lane_md5, "paper_decision_md5": paper_md5, "paper_restricted_to_lane_md5": _md5(sub), "lane_restricted_md5": _md5(sub_l),
         "restricted_equal": sub_equal, "gap_refusals": len(gap_ref), "blocked_pool_refusals": len(blk_ref),
         "paper_entries_not_in_lane": len(missing), "unexplained_paper_entries": len(unexplained), "lane_entries_not_in_paper": len(lane_not_in_paper),
         "entry_field_diffs": len(field_diff), "exit_diffs": len(exit_diff), "clean_part_sets_equal": clean_equal, "lane_chain_seq": lane_chain[1], "paper_chain_seq": paper_chain[1]}
    print(canon({"ok": ok, "verdict": "PASS" if ok else "FAIL", "metrics": m}))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("paper")
    p.add_argument("--root", required=True)
    p.add_argument("--tape-dir", required=True)
    p.add_argument("--from-ms", type=int, required=True)
    p.add_argument("--to-ms", type=int, required=True)
    p.add_argument("--seed-from-hour", required=True)
    g = sp.add_parser("register")
    g.add_argument("--registry", required=True)
    g.add_argument("--from-ms", type=int, required=True)
    c = sp.add_parser("compare")
    c.add_argument("--lane", required=True)
    c.add_argument("--paper", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "register":
            scratch_register(a.registry, a.from_ms)
            return 0
        return paper(a) if a.cmd == "paper" else compare(a)
    except R.HostError as e:
        print(f"floor_replay_proof: HostError: {str(e)[:300]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
