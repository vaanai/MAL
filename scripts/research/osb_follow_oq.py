#!/usr/bin/env python3
"""osb_follow_oq: opt-in front end of scripts/research/osb_follow.py for the other-quote universe. PAPER only; in-sample research, not a gate read. Nothing here says any book makes money.

WHY A SEPARATE FILE: osb_follow.py's bytes are part of every running book's code.sha256 (a changed simulator is a new book); this file leaves them, tools/arena_rules.py, arena_paper.py,
floor_lane_host.py and every pin untouched. It runs osb_follow's own loop (manifest sha, pins, per-hour md5, restart check, lock, status) with three functions swapped for the duration of
one process: load_manifest (accepts the extra key `universe`), build_book, and the decision / count readers.

Manifest: osb_follow's keys plus `universe`: "pumpswap_wsol" (default, absent = same; the ORIGINAL build_book runs, the output is osb_follow's) or "pumpswap_other_quote" (requires host "hostv2",
rule_overrides {} (the FLOOR constants belong to the WSOL rule), no `lane`, gap_exclusion absent or "on"; pricing_lat_entry_s sets the entry pricing latency, default 4.085 s (the deciding cell; twins 3.010 / 5.590 via twin_of, decisions unchanged)). The book hosts
`l1-floor-oq-v1` on tools/arena_oq_engine.OQHost with OQEngine (pricing in the pool's quote units, conversion legs, per-pool fee; see that module). WSOL pools never enter it.
Usage: osb_follow_oq.py MANIFEST.json [--once] [--max-hours N]
"""
import argparse, hashlib, json, os, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import osb_follow as O  # noqa: E402
from tools import arena_rules as R  # noqa: E402
from tools import arena_rule_l1_oq as Q  # noqa: E402
from tools import floor_lane_host as H  # noqa: E402
from tools.arena_declog import DecisionLog  # noqa: E402
from tools.arena_oq_engine import OQEngine, OQHost  # noqa: E402
from tools.arena_state import WARM_MIN_DEFAULT, ArenaState  # noqa: E402

UNIVERSES = ("pumpswap_wsol", "pumpswap_other_quote")


def load_manifest(path):
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise O.Refused("manifest unreadable: %s" % e)
    if not isinstance(raw, dict):
        raise O.Refused("manifest must be a JSON object")
    u = raw.pop("universe", "pumpswap_wsol")
    if u not in UNIVERSES:
        raise O.Refused("universe must be one of %s" % (list(UNIVERSES),))
    with tempfile.TemporaryDirectory(prefix="osb-oq-manifest-") as td:
        tmp = Path(td) / "m.json"
        tmp.write_text(json.dumps(raw))
        m = _orig_load(str(tmp))
    if u == "pumpswap_other_quote":
        if m["host"] != "hostv2" or m["rule_overrides"] or "lane" in m or m.get("gap_exclusion", "on") != "on" or ("twin_of" in m and "pricing_lat_entry_s" not in m):
            raise O.Refused("universe pumpswap_other_quote: host 'hostv2', rule_overrides {}, no lane, twin_of only as a latency twin (pricing_lat_entry_s 3.010 / 4.085 / 5.590), gap_exclusion 'on' or absent")
        m["universe"] = u
    return m


def build_book(m, run_dir, *, table_mode=None, engine_kw=None):
    if m.get("universe", "pumpswap_wsol") != "pumpswap_other_quote":
        return _orig_build(m, run_dir, table_mode=table_mode, engine_kw=engine_kw)
    start_ms = H.parse_hour(m["start_hour"])
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    ledger, registry = str(run_dir / "ledger"), str(run_dir / "bots.json")
    rules = [Q.make_rule()]  # not in RULE_MODULES_V2: arena_rules_v2.py is pinned and unedited
    if table_mode is not None:
        for r in rules:
            r.table_mode = table_mode
    (run_dir / "variant.json").write_text(json.dumps({"variant": {}, "universe": m["universe"], "send": m["send_lamports"], "book_id": m["book_id"], "host": m["host"],
                                                       "lat_entry_s": m.get("pricing_lat_entry_s", Q.LAT_PRICING_DECIDING_S)}, indent=1, sort_keys=True) + "\n")
    os.environ["MAL_ARENA_TEST"] = "1"  # scratch registry only, as floor_replay_proof_reg.scratch_register
    try:
        R.register_rules(rules, registry, H.FROZEN_SHA, start_ms, now_ms=start_ms - 1, verify_sha=False)
    finally:
        os.environ.pop("MAL_ARENA_TEST", None)
    stakes = tuple(float(x) for x in m["stakes_sol"])
    engine = OQEngine(ledger, stakes, m["send_lamports"], float(m.get("pricing_lat_entry_s", Q.LAT_PRICING_DECIDING_S)))
    warm_ms = start_ms - WARM_MIN_DEFAULT * R.MIN_MS
    hc = R.HostConfig(root=str(run_dir), registry=registry, frozen_sha=H.FROZEN_SHA, clock_start_ms=start_ms, live=False, verbose=False)
    host = OQHost(hc, rules, ArenaState(m["tape_dir"], start_ms=warm_ms, warm_min=WARM_MIN_DEFAULT), engine)
    engine.table = host.table
    n = H.rebuild_table_since(host.table, m["tape_dir"], warm_ms, H.parse_hour(m["seed_from_hour"]), str(run_dir / "seed-tmp"))
    print(O.canon({"book": m["book_id"], "table_seeded_rows": n, "seed_from_hour": m["seed_from_hour"], "run": run_dir.name, "universe": m["universe"]}), flush=True)
    host.resume()
    cfg = type("Cfg", (), {"stakes_sol": stakes})()
    return host, engine, cfg


def decision_md5(run_dir):
    rows = DecisionLog(str(run_dir)).rows(Q.BOT_ID) or DecisionLog(str(run_dir)).rows(H.BOT_ID)
    return hashlib.md5(O.canon([r for r in rows if r.get("kind") in ("entry", "exit")]).encode()).hexdigest()


def count_rows(run_dir):
    rows = DecisionLog(str(run_dir)).rows(Q.BOT_ID) or DecisionLog(str(run_dir)).rows(H.BOT_ID)
    return {"n_decision_rows": len(rows), "n_entries": sum(1 for r in rows if r.get("kind") == "entry"), "n_exits": sum(1 for r in rows if r.get("kind") == "exit")}


_orig_load, _orig_build, _orig_dm, _orig_cr = O.load_manifest, O.build_book, O.decision_md5, O.count_rows


def follow(manifest_path, **kw):
    O.load_manifest, O.build_book, O.decision_md5, O.count_rows = load_manifest, build_book, decision_md5, count_rows
    try:
        return O.follow(manifest_path, **kw)
    finally:
        O.load_manifest, O.build_book, O.decision_md5, O.count_rows = _orig_load, _orig_build, _orig_dm, _orig_cr


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("manifest")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--max-hours", type=int, default=None)
    a = ap.parse_args(argv)
    return follow(a.manifest, once=a.once, max_hours=a.max_hours)


if __name__ == "__main__":
    raise SystemExit(main())
