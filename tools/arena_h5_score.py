#!/usr/bin/env python3
"""Arena H5 scorer: prices the fresh-forward shadow's outcome records into the arena ledger. Paper only. Scoring only: no gate verdict, no P&L printed.

INPUT. One or more arena shadow dirs (tools/h5_shadow.py --fresh-forward ARENA via scripts/research/arena-h5.sh), each under /data/mal/arena/h5-*
(the same check as the shadow: /srv/mal-h5-shadow and $HOME/data/h5-shadow* are refused). Records read: start, trigger, outcome, outcome_bx10.

BOTS (two bots, one trigger rule, two exits).
  h5v1-boostfloor   the `outcome` records: the rule's exit at s0 + 330 s (+ 0.55 s lag).
  h5v2-boostclock   the `outcome_bx10` records: the v2 BOOSTCLOCK exit (cell C10, EXP-026); same trigger and entry legs, only the exit instant differs.
Only the `pv` variant (per-print V, the primary one) is a trade; `fv` (the fixed-V sensitivity) is not scored.

COST MODEL (frozen; the one of tools/boostfloor_score.py, the #476 port that matches the 32 exploration cells). Round trip: tools.boostfloor_score.
fill_round_trip (own impact on Q incl. V, canonical PumpSwap tier fee on both legs, 55,000 lamports priority on each send) priced from the entry and
exit pool states the shadow recorded. Two entry legs: 1.3 s ("1p3", the shadow's `primary`) and 1.9 s ("1p9", `binding`). Two fail models per leg:
  flat_*   15% of attempts fail: net = 0.85 * pnl + 0.15 * (-55,000)
  press_*  pressure-fail model at slope scale 1: p = sigmoid(intercept + 0.8 ln(1 + same-slot buys) + 0.35 ln(1 + nearby buy SOL)); net = (1 - p) * pnl + p * (-55,000)
The exploration scorer refits the press intercept on the whole book so that mean p = 0.289. A write-once ledger row cannot change when later trades arrive,
so the intercepts are FROZEN here (PRESS_INTERCEPT): fit_curve on the 77 `pv` outcomes of the shadow's replay of the exploration tape 2026-09-20T00..
2026-09-21T00 (the md5 proof window), per leg. Each leg also stores p_fail, ssb and nb_lamports, so a gate read can refit the intercept on the whole forward
book (refit_press_intercepts) and report the frozen and the refit numbers side by side.

STAKES. 0.10, 0.25 and 0.30 SOL, one ledger row per (trade, stake). 0.30 is the lab's impact limit for H5; a larger stake is refused in code.

LEDGER. <ledger-root>/<bot_id>/trades-<UTC day of decision_ms>.jsonl, strict JSON, one row per line, WRITE-ONCE per trade_id (trade_id = "<bot_id>:<pool>:<stake>";
a trade already in any of the bot's day files is skipped, never rewritten). Fields: trade_id, bot_id, frozen_sha (code_sha of the shadow's start record),
decision_ms (the trigger's detect instant), exit_ms (decision + the slot distance to the later leg's exit landing slot at the pool's sps), pool, mint, prog
"pumpswap", stake_sol, legs {flat_1p3, flat_1p9, press_1p3, press_1p9} (each net_lamports, nofail_lamports, p_fail; press also ssb and nb_lamports), meta.

WHAT IS SCORED. A trade is scored only if its trigger's detect instant is at or after the start record of the run that wrote it (the shadow already ignores pools with
s0 before its start; this is the second check), and only if its outcome is `complete`. Skips are counted, never hidden: skipped_incomplete, skipped_pre_start,
skipped_no_start, skipped_no_trigger, skipped_bx10_error, skipped_untradable (both legs without a trade), duplicate. Gap-flagged pools are scored and carry
meta.gap; the reader decides.

Run: python -m tools.arena_h5_score [--shadow-dir DIR ...] [--ledger-root /data/mal/arena/ledger] [--dry-run]   (needs numpy: the audit or c1nf venv)
Output: counts only.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/arena_h5_score.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import collections
import fcntl
import glob
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from tools import boostfloor_score as bf
from tools import h5_shadow as h5
from tools.h5_shadow import check_fresh_out_dir
from tools.latency_curve import B_SLOT, B_SOL, FailCurve, Pressure, fit_curve

SCHEMA = "arena_h5_trade_v1"
LEDGER_ROOT = "/data/mal/arena/ledger"
BOTS = {"h5v1-boostfloor": "outcome", "h5v2-boostclock": "outcome_bx10"}  # bot id -> the shadow record type that prices its exit
VARIANT = "pv"
LEGS = (("primary", "1p3"), ("binding", "1p9"))  # the shadow's leg name -> the ledger's suffix (entry latency in seconds)
STAKES = (("0.10", 100_000_000), ("0.25", 250_000_000), ("0.30", 300_000_000))
MAX_STAKE_LAMPORTS = 300_000_000  # the lab's impact limit for H5
# Frozen press intercepts: fit_curve (mean p = 0.289) per entry leg on the 77 pv outcomes of the replay of exploration tape 2026-09-20T00..2026-09-21T00.
PRESS_INTERCEPT = {"primary": -1.9478840529201729, "binding": -1.938758504543896}
PRESS_INTERCEPT_N = 77


def price_leg(entry_q_sol: float, entry_base: float, exit_q_sol: float, exit_base: float, stake_lamports: int, ssb: int, nb_lamports: int, shadow_leg: str) -> dict[str, Any]:
    """Both fail models of one entry leg at one stake. Entry and exit states are the shadow's (q in SOL incl. V, base in raw units)."""
    if stake_lamports > MAX_STAKE_LAMPORTS:
        raise ValueError(f"stake {stake_lamports} lamports is above the H5 impact limit of {MAX_STAKE_LAMPORTS}")
    nofail, gross = bf.fill_round_trip(entry_q_sol * 1e9, entry_base, exit_q_sol * 1e9, exit_base, float(stake_lamports))
    p_flat = bf.FLAT_FAIL
    p_press = FailCurve(PRESS_INTERCEPT[shadow_leg], B_SLOT, B_SOL, 1.0).p(Pressure(int(ssb), int(nb_lamports)))
    fail_cost = -float(bf.PRIO_LAMPORTS)  # a failed buy costs its send fee only
    return {
        "flat": {"net_lamports": (1 - p_flat) * nofail + p_flat * fail_cost, "nofail_lamports": nofail, "p_fail": p_flat},
        "press": {"net_lamports": (1 - p_press) * nofail + p_press * fail_cost, "nofail_lamports": nofail, "p_fail": p_press, "ssb": int(ssb),
                  "nb_lamports": int(nb_lamports)},
        "gross": gross,
    }


def check_shadow_dir(path: str) -> Path:
    return check_fresh_out_dir(path)  # under /data/mal/arena/h5-*; never the live executor's feed


def read_records(shadow_dir: Path, counts: collections.Counter) -> Iterator[dict]:
    """Records of the dir's hourly files, file order then line order. A line that is not strict JSON is counted and skipped."""
    for f in sorted(shadow_dir.glob("h5-shadow-????-??-??T??.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    yield json.loads(line, parse_constant=_no_constant)
                except ValueError:
                    counts["bad_lines"] += 1


def _no_constant(name: str) -> Any:
    raise ValueError(f"non-strict JSON constant {name}")


def _utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def trade_rows(shadow_dir: Path, counts: collections.Counter) -> Iterator[dict]:
    """Ledger rows of one shadow dir (every bot, every stake). Each record is judged against the latest start record before it."""
    start: dict | None = None
    triggers: dict[tuple[str, str], dict] = {}
    outcomes: dict[tuple[str, str], tuple[dict, dict | None, dict | None]] = {}  # (pool, variant) -> (v1 outcome, its start record, the bx10 record)
    pending_bx: dict[tuple[str, str], dict] = {}
    for rec in read_records(shadow_dir, counts):
        t = rec.get("type")
        if t == "start":
            start = rec
        elif t == "trigger" and rec.get("variant") == VARIANT:
            triggers.setdefault((rec["pool"], VARIANT), {**rec, "_start": start})
        elif t == "outcome" and rec.get("variant") == VARIANT:
            outcomes.setdefault((rec["pool"], VARIANT), (rec, start, None))
        elif t == "outcome_bx10" and rec.get("variant") == VARIANT:
            pending_bx.setdefault((rec["pool"], VARIANT), rec)
    for key, (o, _, _) in outcomes.items():
        trig = triggers.get(key)
        bx = pending_bx.get(key)
        for bot, rtype in BOTS.items():
            src = o if rtype == "outcome" else bx
            if src is None:
                if rtype == "outcome_bx10":
                    counts["skipped_no_bx10"] += 1
                continue
            row = _rows_for(bot, o, src, trig, counts)
            yield from row


def _rows_for(bot: str, o: dict, src: dict, trig: dict | None, counts: collections.Counter) -> list[dict]:
    if trig is None:
        counts["skipped_no_trigger"] += 1
        return []
    start = trig.get("_start")
    if not start or start.get("start_ms") is None:
        counts["skipped_no_start"] += 1
        return []
    if "error" in src and "legs" not in src:
        counts["skipped_bx10_error"] += 1
        return []
    if not src.get("complete") or not o.get("complete"):
        counts["skipped_incomplete"] += 1
        return []
    decision_ms = int(trig.get("t_detect_ms") or trig["t_ms"])
    if decision_ms < int(start["start_ms"]):
        counts["skipped_pre_start"] += 1
        return []
    sps = float(o["sps"])
    legs_priced: dict[str, dict] = {}
    exit_slots: list[int] = []
    for shadow_leg, sfx in LEGS:
        ent = o["legs"][shadow_leg]
        out = src["legs"][shadow_leg] if src is not o else None
        if src is o:
            exit_q, exit_b, exit_land = o["exit"]["q_sol"], o["exit"]["base"], o["exit"]["landing_slot"]
            tradable = ent.get("net") is not None
        else:
            tradable = out is not None and out.get("net") is not None and ent.get("net") is not None
            exit_q, exit_b, exit_land = (out.get("q_sol"), out.get("base"), out.get("exit_landing_slot")) if out else (None, None, None)
        legs_priced[shadow_leg] = {"ok": tradable, "entry": ent, "exit": (exit_q, exit_b), "exit_land": exit_land}
        if tradable:
            exit_slots.append(int(exit_land))
    if not exit_slots:
        counts["skipped_untradable"] += 1
        return []
    exit_ms = decision_ms + int(round((max(exit_slots) - int(o["trigger_slot"])) * sps * 1000))
    rows = []
    for label, lam in STAKES:
        legs: dict[str, Any] = {}
        for shadow_leg, sfx in LEGS:
            lp = legs_priced[shadow_leg]
            if not lp["ok"]:
                legs[f"flat_{sfx}"] = {"net_lamports": None}
                legs[f"press_{sfx}"] = {"net_lamports": None}
                continue
            e = lp["entry"]
            priced = price_leg(e["q_sol"], e["base"], lp["exit"][0], lp["exit"][1], lam, e["ssb"], e["nb_lamports"], shadow_leg)
            legs[f"flat_{sfx}"] = priced["flat"]
            legs[f"press_{sfx}"] = priced["press"]
        rows.append({
            "trade_id": f"{bot}:{o['pool']}:{label}", "bot_id": bot, "frozen_sha": start.get("code_sha"), "decision_ms": decision_ms, "exit_ms": exit_ms,
            "pool": o["pool"], "mint": o.get("mint"), "prog": "pumpswap", "stake_sol": lam / 1e9, "legs": legs,
            "meta": {"schema": SCHEMA, "variant": VARIANT, "start_ms": int(start["start_ms"]), "sps": sps, "gap": bool(o.get("gap")),
                     "chain_breaks": o.get("chain_breaks"), "base_breaks": o.get("base_breaks"),
                     "base_breaks_unresolved_settled": trig.get("base_breaks_unresolved_settled"), "boost_src": trig.get("boost_src"),
                     "exit_rule": "330s" if src is o else "bx10"},
        })
    return rows


def _strict(row: dict) -> str:
    return json.dumps(row, separators=(",", ":"), allow_nan=False, sort_keys=False)


def existing_ids(bot_dir: Path) -> set[str]:
    ids: set[str] = set()
    for f in sorted(bot_dir.glob("trades-????-??-??.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    try:
                        ids.add(json.loads(line)["trade_id"])
                    except (ValueError, KeyError):
                        pass
    return ids


def write_ledger(rows: Sequence[dict], ledger_root: Path, counts: collections.Counter, dry_run: bool = False) -> None:
    """Append the rows not yet in the ledger. Write-once: an existing trade_id is never touched. A row with a non-finite number is refused (strict JSON)."""
    by_bot: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        by_bot[r["bot_id"]].append(r)
    for bot, rs in by_bot.items():
        bot_dir = ledger_root / bot
        if not dry_run:
            bot_dir.mkdir(parents=True, exist_ok=True)
        lock = open(bot_dir / ".lock", "a") if not dry_run else None
        try:
            if lock is not None:
                fcntl.flock(lock, fcntl.LOCK_EX)
            seen = existing_ids(bot_dir) if bot_dir.exists() else set()
            for r in sorted(rs, key=lambda x: (x["decision_ms"], x["trade_id"])):
                if r["trade_id"] in seen:
                    counts["duplicate"] += 1
                    continue
                line = _strict(r)  # raises ValueError on NaN / inf: nothing is written for that row
                seen.add(r["trade_id"])
                counts[f"rows_{bot}"] += 1
                if dry_run:
                    continue
                with open(bot_dir / f"trades-{_utc_day(r['decision_ms'])}.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
        finally:
            if lock is not None:
                fcntl.flock(lock, fcntl.LOCK_UN)
                lock.close()


def refit_press_intercepts(rows: Sequence[dict]) -> dict[str, float]:
    """The exploration scorer's way (bf.apply_fail_legs): per entry leg, fit_curve (mean p = 0.289) on the book's 0.10 SOL sends. For a gate read over the
    whole forward book, next to the frozen ledger numbers. One book = one bot's rows."""
    out: dict[str, float] = {}
    for shadow_leg, sfx in LEGS:
        ps = [Pressure(r["legs"][f"press_{sfx}"]["ssb"], r["legs"][f"press_{sfx}"]["nb_lamports"]) for r in rows
              if r["stake_sol"] == 0.1 and r["legs"][f"press_{sfx}"].get("net_lamports") is not None]
        if ps:
            out[shadow_leg] = fit_curve(ps).intercept
    return out


def default_shadow_dirs() -> list[str]:
    return sorted(glob.glob(os.path.join(h5.ARENA_ROOT, "h5-shadow-*")))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="price the arena H5 shadow's outcomes into the arena ledger (counts only)")
    ap.add_argument("--shadow-dir", action="append", default=None, help=f"arena shadow dir under {h5.ARENA_ROOT}/h5-*; repeatable (default: every {h5.ARENA_ROOT}/h5-shadow-*)")
    ap.add_argument("--ledger-root", default=LEDGER_ROOT, help="must be under /data/mal/arena")
    ap.add_argument("--dry-run", action="store_true", help="count the rows that would be written; write nothing")
    a = ap.parse_args(argv)
    counts: collections.Counter = collections.Counter()
    try:
        root = os.path.realpath(a.ledger_root)
        if not (root == os.path.realpath(h5.ARENA_ROOT) + "/ledger" or root.startswith(os.path.realpath(h5.ARENA_ROOT) + "/ledger/")) or ".." in Path(a.ledger_root).parts:
            raise ValueError(f"--ledger-root {a.ledger_root!r} must be under {h5.ARENA_ROOT}/ledger")
        dirs = [check_shadow_dir(d) for d in (a.shadow_dir if a.shadow_dir is not None else default_shadow_dirs())]
    except ValueError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    rows: list[dict] = []
    for d in dirs:
        rows.extend(trade_rows(d, counts))
    counts["rows_scored"] = len(rows)
    write_ledger(rows, Path(a.ledger_root), counts, dry_run=a.dry_run)
    print(json.dumps({"schema": SCHEMA, "shadow_dirs": [str(d) for d in dirs], "dry_run": a.dry_run, "counts": dict(sorted(counts.items()))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
