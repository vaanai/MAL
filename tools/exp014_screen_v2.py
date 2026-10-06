#!/usr/bin/env python3
"""EXP-014 screen v2: the mig+15 PumpSwap selector, at the lab's current standard. EXPLORATION ONLY. NO EDGE CLAIM.

Plan: EXP/EXP-014-mig15-pumpswap-selector-plan.md, Amendment 7 (which supersedes the plan's data, cost and bar lines; the trigger, the 27 features, the
exit rule, the model recipe and the threshold are unchanged). A pass earns only a pre-registration on a block that is not yet reserved.

Modes
  --guards-only  every guard that precedes a read (view pins, P3 dedupe manifests, V-map sha, run lock, tries logs, hours present on disk); opens no row.
  --precount     outcome-blind, on the REAL layout: one count-only pass over every hour of P2, P3 and P4. No price, no fill, no net is computed.
                 Counts rows, bad lines, rows without a clock, PumpSwap rows with no pool or no V, migrations (tape order) per T day, creates.
                 Refuses (exit 2) on any missing hour, a zstd stream that does not end rc 0, or V coverage below the pins. Writes precount.json.
  (screen)       refuses unless precount.json is clean and was made by the same code and layout; takes RUN.lock (O_EXCL); writes a `started` line to BOTH
                 tries logs; runs the tape pass (V-priced, exit lag 2, stakes 0.05 and 0.5 SOL), the nested LODO, the bars and the Holm test (k = 1);
                 writes screen.json and screen.md and the `completed` lines. A second run is refused, before and after `started`.

Run (research host, one heavy job at a time, as a MiScusi job; see the PR body for the exact lines):
  nice -n 19 /data/mal/venv/bin/python -m tools.exp014_screen_v2 --precount --out-dir /data/mal/exp014-screen-v2 --p2-view-dir ... (x7) --p4-view-dir ... (x2)
  nice -n 19 /data/mal/venv/bin/python -m tools.exp014_screen_v2 --out-dir /data/mal/exp014-screen-v2 --tries-log /data/mal/ops/tries-exp014-screen-v2.jsonl ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import tools.exp011_freeze as fz
import tools.exp012_backcheck as bc
import tools.exp012_forward as ff12
import tools.exp012_score as s12
import tools.exp014_m15_model as mm
import tools.exp014_m15_trigger as mt
import tools.exp015_screen as e15
import tools.exp017_screen as e17
from tools import mal_result
from tools.latency_curve import _open_text

TOOL = "tools.exp014_screen_v2"
SCHEMA = "exp014_screen_v2_v1"
REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN = REPO_ROOT / "EXP" / "EXP-014-mig15-pumpswap-selector-plan.md"
CANONICAL_TRIES = REPO_ROOT / "data" / "tries.jsonl"
TRIES_KEY = "exp014_m15"  # the plan's key; ONE try
LAMPORTS = 1_000_000_000

# --- the deciding configuration (Amendment 7; every number fixed before any outcome is read) ---------------------------
KS = (4, 8)  # d = 4 primary; d = 8 is item 5 (d = 1 was reference only and is dropped)
PRIMARY_K = mt.PRIMARY_K
SIZES = (50_000_000, 500_000_000)  # 0.05 SOL decides (the EXP-015 cache bars); 0.5 SOL is report-only
DECIDING_SIZE = SIZES[0]
EXIT_LAG = e15.EXIT_LAG  # 2 slots
FEE = e15.FEE  # 505,000 lamports per side
HOLM_K = 1
HOLM_ALPHA = 0.05
JACCARD_MAX = 0.5
UNPRICEABLE_MAX_FRACTION = e15.UNPRICEABLE_MAX_FRACTION  # 0.5 % of migrating mints on a pool with no V
V_MAX_MISSING_FRACTION = e15.V_MAX_MISSING_FRACTION  # 1 % of PumpSwap prints with no V
BAD_LINE_MAX_FRACTION = 1e-4
NO_CREATE_HOURS_MAX_FRACTION = 0.05
SOURCES = ("P2", "P3", "P4")
LEGS = e15.LEGS
OUT_PRECOUNT, OUT_SCREEN, OUT_MD = "precount.json", "screen.json", "screen.md"
DEFAULT_OUT = Path("/data/mal/exp014-screen-v2")
CODE_FILES = ("tools/exp014_screen_v2.py", "tools/exp014_m15_trigger.py", "tools/exp014_m15_model.py")
BANNER = (
    "EXPLORATION, NO EDGE CLAIM. EXP-014 screen v2. Prior odds about 10 % (plan). One try, Holm k = 1. P1 is not read. "
    "Post-read disclosures: the explore-0814 days were read by DEC-017 (a) and by the EXP-012 backcheck; fresh-0903 was spent by EXP-012; exp011-0909 by EXP-011/015."
)


class Refused(e15.Refused):
    """A pre-declared refusal. Before `started`: nothing is logged and no try is spent."""


# --- layout and guards ------------------------------------------------------------------------------------------------


def block_of(tag: str) -> str:
    return tag  # P2 / P3 / P4 are their own blocks


def layout(g2: Mapping[str, Any], g3: Mapping[str, Any], g4: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """tag -> {hours (picklable resolver), pool (hour keys, in order)}. P3 reads the deduplicated `.deduped.jsonl.zst` copies (e15.make_p3_hours)."""
    return {
        "P2": {"hours": bc.MultiViewHours(dict(g2["roots"])), "pool": list(g2["pool"])},
        "P3": {"hours": e15.make_p3_hours(g3["walkers"]), "pool": bc.hours_range(*e15.BLOCKS["P3"])},
        "P4": {"hours": bc.MultiViewHours(dict(g4["roots"])), "pool": list(g4["pool"])},
    }


def hour_files(src: Mapping[str, Any]) -> tuple[list[str], list[str], int]:
    """(hours whose trades file is missing, hours with no creates file, n hours). Opens no file."""
    missing: list[str] = []
    no_create: list[str] = []
    for h in src["pool"]:
        try:
            info = src["hours"](h)
        except (SystemExit, AssertionError):
            missing.append(h)
            continue
        if not Path(info["trade"]).is_file():
            missing.append(h)
        if info.get("create") is None or not Path(info["create"]).is_file():
            no_create.append(h)
    return missing, no_create, len(src["pool"])


def check_hours_present(lay: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tag, src in lay.items():
        missing, no_create, n = hour_files(src)
        if missing:
            raise Refused(f"{tag}: {len(missing)} of {n} hour(s) have no trades file (first {missing[0]}); a missing hour refuses")
        if len(no_create) > NO_CREATE_HOURS_MAX_FRACTION * n:
            raise Refused(f"{tag}: {len(no_create)} of {n} hours have no creates file (> {NO_CREATE_HOURS_MAX_FRACTION:.0%})")
        out[tag] = {"hours": n, "hours_without_creates": len(no_create)}
    return out


def code_digest() -> str:
    h = hashlib.sha256()
    for rel in CODE_FILES:
        h.update(rel.encode())
        h.update((REPO_ROOT / rel).read_bytes())
    return h.hexdigest()


def layout_digest(g: Mapping[str, Any]) -> str:
    doc = {
        "pools": {t: [s["pool"][0], s["pool"][-1], len(s["pool"])] for t, s in g["layout"].items()},
        "view_sha256": g["view_sha256"], "vmap": g["vmap_sha256"], "code": code_digest(), "ks": KS, "sizes": SIZES, "exit_lag": EXIT_LAG,
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()


def prior_exp014_lines(log: Path) -> list[dict[str, Any]]:
    out = []
    if Path(log).is_file():
        for line in Path(log).read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if (rec.get("config") or {}).get("key") == TRIES_KEY:
                out.append(rec)
    return out


def check_no_prior_tries(*logs: Path) -> None:
    seen: set[Path] = set()
    for lg in logs:
        r = Path(lg).resolve()
        if r in seen:
            continue
        seen.add(r)
        prior = prior_exp014_lines(r)
        if prior:
            raise Refused(f"{len(prior)} earlier {TRIES_KEY} line(s) in {r}: the plan allows ONE try, so a second run is refused")


def check_tries_logs(arg: str | None, canonical: Path) -> list[Path]:
    if not arg:
        raise Refused("screen mode needs an absolute --tries-log (the ops log); the canonical repo log is written as well")
    if not Path(arg).is_absolute():
        raise Refused(f"--tries-log {arg} is relative: give an absolute path")
    e17.check_canonical_tries(canonical)
    return [Path(arg), Path(canonical).resolve()]


def run_guards(args: argparse.Namespace, verify: bool = True, enforce_base: bool = True) -> dict[str, Any]:
    e15.check_pin_ready()
    e15.check_workers(args.max_workers)
    if not args.p2_view_dir or not args.p4_view_dir:
        raise Refused("--p2-view-dir (explore-0814 w1..w7) and --p4-view-dir (exp011-0909 b, c) are required: the plan's views are the 27 non-P1 dates")
    g2 = e15.guard_p2(args.p2_view_dir, verify, enforce_base)
    g3 = e15.guard_p3(args.p3_root, verify, enforce_base)
    g4 = e15.guard_p4(args.p4_view_dir, verify)
    assert g4 is not None
    vsha = e15.check_vmap(args.vmap, e15.VMAP_0909_SHA256, "V map")
    lay = layout(g2, g3, g4)
    e15.assert_hours_allowed([h for s in lay.values() for h in s["pool"]], with_p4=True)
    presence = check_hours_present(lay)
    return {"g2": g2, "g3": g3, "g4": g4, "layout": lay, "hours_present": presence, "vmap_sha256": vsha, "view_sha256": {"P2": g2["view_sha256"], "P3": g3["pin_sha256"], "P4": g4["view_sha256"]}}


# --- readers: zstd rc checked -----------------------------------------------------------------------------------------


class ZstdFailed(RuntimeError):
    pass


def checked_lines(path: Path) -> Any:
    """Lines of a trades / creates file. A `.zst` stream that does not end rc 0 (a truncated or corrupt file) raises ZstdFailed at the end of the
    iteration, so a pass never returns a silently short hour."""
    if not path.name.endswith(".zst"):
        yield from _open_text(path)
        return
    proc = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE)
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            yield raw.decode("utf-8", "replace")
    finally:
        proc.stdout.close()
        rc = proc.wait()
    if rc != 0:
        raise ZstdFailed(f"zstd -dc exited {rc} on {path}")


class CheckedTrades(mt.CountingTrades):
    """CountingTrades with the zstd rc check. A CountingTrades subclass, so the worker counts its bad lines."""

    def __call__(self, path: Path) -> Any:
        for line in checked_lines(Path(path)):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                self.bad += 1
                continue
            if isinstance(row, dict):
                yield row
            else:
                self.bad += 1


# --- precount (outcome-blind) -----------------------------------------------------------------------------------------


def count_hour(spec: Mapping[str, Any]) -> dict[str, Any]:
    """One hour, count only. Returns small per-hour facts; no price, fill or net is computed."""
    from tools.pumpswap_virtual import load_map

    try:
        os.nice(19)
    except OSError:
        pass
    vmap = load_map(Path(spec["vmap"]))
    info = spec["hours"](spec["hour"])
    out: dict[str, Any] = {"hour": spec["hour"], "rows": 0, "bad": 0, "no_clock": 0, "non_trade": 0, "pumpswap": 0, "pumpswap_no_pool": 0, "pumpswap_no_v": 0, "bond": 0, "creates": 0, "create_mints": []}
    first: dict[str, list[Any]] = {}  # mint -> [min bond t, min pumpswap t, pool of that print]
    rd = CheckedTrades()
    for row in rd(Path(info["trade"])):
        out["rows"] += 1
        t = mt.row_clock_ms(row)
        if t is None:
            out["no_clock"] += 1
            continue
        if not mt.row_is_tape_print(row):
            out["non_trade"] += 1
            continue
        mint, venue = row.get("mint"), row.get("venue")
        if not isinstance(mint, str):
            continue
        rec = first.setdefault(mint, [None, None, None])
        if venue == "pump_bonding":
            out["bond"] += 1
            rec[0] = t if rec[0] is None else min(rec[0], t)
        elif venue == "pumpswap":
            out["pumpswap"] += 1
            pool = row.get("pool")
            if not isinstance(pool, str):
                out["pumpswap_no_pool"] += 1
            elif vmap.get(pool) is None:
                out["pumpswap_no_v"] += 1
            if rec[1] is None or t < rec[1]:
                rec[1], rec[2] = t, pool if isinstance(pool, str) else None
    out["bad"] = rd.bad
    if info.get("create") is not None:
        cr = CheckedTrades()
        for row in cr(Path(info["create"])):
            out["creates"] += 1
            if isinstance(row.get("mint"), str):
                out["create_mints"].append(row["mint"])
        out["bad"] += cr.bad
    # keep only mints with a pumpswap print (a migration candidate) or a bonding print (so the merge can order them)
    out["first"] = {m: v for m, v in first.items() if v[1] is not None or v[0] is not None}
    return out


def merge_counts(tag: str, parts: Sequence[Mapping[str, Any]], vmap: Mapping[str, int | None], expected_hours: int) -> dict[str, Any]:
    tot = {k: sum(p[k] for p in parts) for k in ("rows", "bad", "no_clock", "non_trade", "pumpswap", "pumpswap_no_pool", "pumpswap_no_v", "bond", "creates")}
    first: dict[str, list[Any]] = {}
    created: set[str] = set()
    for p in parts:
        created.update(p["create_mints"])
        for mint, (b, s, pool) in p["first"].items():
            rec = first.setdefault(mint, [None, None, None])
            if b is not None:
                rec[0] = b if rec[0] is None else min(rec[0], b)
            if s is not None and (rec[1] is None or s < rec[1]):
                rec[1], rec[2] = s, pool
    migrated = {m: v for m, v in first.items() if v[1] is not None and v[0] is not None and v[0] <= v[1]}  # bonding print at or before the first pumpswap print (tape order, approximate)
    block = block_of(tag)
    in_block = {m: v for m, v in migrated.items() if e15.in_block_window(block, v[1])}
    by_t_day: dict[str, int] = {}
    for m, v in in_block.items():
        d = mt._utc_day(v[1] + mt.OFFSET_MS)
        by_t_day[d] = by_t_day.get(d, 0) + 1
    homed = {m for m in in_block if m in created}
    unpriceable = sorted(m for m, v in homed.items() if v[2] is None or vmap.get(v[2]) is None)
    return {
        "tag": tag, "hours_found": len(parts), "hours_expected": expected_hours, **tot, "creates_distinct": len(created),
        "migrations_tape_order_in_block": len(in_block), "migrations_with_create": len(homed), "migrations_by_t_day": dict(sorted(by_t_day.items())),
        "unpriceable_migrations": len(unpriceable), "unpriceable_mints": unpriceable,
        "expected_row_ceiling": len(homed) * len(KS) * len(SIZES),
        "pumpswap_no_v_fraction": (tot["pumpswap_no_v"] / tot["pumpswap"]) if tot["pumpswap"] else None,
        "bad_fraction": (tot["bad"] / tot["rows"]) if tot["rows"] else None,
    }


def precount_refusals(counts: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Pre-declared refusals on the counts. An empty list means the real layout is usable."""
    out: list[str] = []
    for tag, c in counts.items():
        if c["hours_found"] != c["hours_expected"]:
            out.append(f"{tag}: {c['hours_found']} of {c['hours_expected']} hours counted")
        if c["rows"] == 0:
            out.append(f"{tag}: no trade rows")
        if c["bad_fraction"] is not None and c["bad_fraction"] > BAD_LINE_MAX_FRACTION:
            out.append(f"{tag}: bad-line fraction {c['bad_fraction']:.2e} > {BAD_LINE_MAX_FRACTION:.0e}")
        if c["pumpswap"] and c["pumpswap_no_pool"]:
            out.append(f"{tag}: {c['pumpswap_no_pool']} PumpSwap rows carry no pool (V needs the pool)")
        if c["pumpswap_no_v_fraction"] is not None and c["pumpswap_no_v_fraction"] > V_MAX_MISSING_FRACTION:
            out.append(f"{tag}: {c['pumpswap_no_v_fraction']:.2%} of PumpSwap prints have no V (> {V_MAX_MISSING_FRACTION:.0%})")
        if c["migrations_with_create"] == 0:
            out.append(f"{tag}: zero migrations with a create in the block (nothing to score)")
        elif c["unpriceable_migrations"] / c["migrations_with_create"] > UNPRICEABLE_MAX_FRACTION:
            out.append(f"{tag}: {c['unpriceable_migrations']} of {c['migrations_with_create']} migrating mints sit on a pool with no V (> {UNPRICEABLE_MAX_FRACTION:.1%})")
    return out


def run_precount(g: Mapping[str, Any], vmap_path: str, max_workers: int) -> dict[str, Any]:
    from tools.pumpswap_virtual import load_map

    vmap = load_map(Path(vmap_path))
    counts: dict[str, Any] = {}
    for tag, src in g["layout"].items():
        specs = [{"hours": src["hours"], "hour": h, "vmap": vmap_path} for h in src["pool"]]
        n = min(max_workers, e15.TAPE_WORKERS_CAP, len(specs))
        if n <= 1:
            parts = [count_hour(s) for s in specs]
        else:
            with mp.get_context("spawn").Pool(processes=n) as pool:
                parts = pool.map(count_hour, specs, chunksize=1)
        counts[tag] = merge_counts(tag, parts, vmap, len(src["pool"]))
        print(f"precount {tag}: rows={counts[tag]['rows']} migrations={counts[tag]['migrations_tape_order_in_block']} with_create={counts[tag]['migrations_with_create']}", file=sys.stderr, flush=True)
    refusals = precount_refusals(counts)
    return {"schema": "exp014_precount_v1", "outcome_blind": True, "layout_digest": layout_digest(g), "counts": counts, "refusals": refusals, "refused": bool(refusals),
            "date_scope": e15.non_p1_dates(True), "note": "migrations are counted in tape order (first PumpSwap print at or after a bonding print); the worker's own count in screen.json is authoritative"}


def check_precount(out_dir: Path, g: Mapping[str, Any]) -> dict[str, Any]:
    p = out_dir / OUT_PRECOUNT
    if not p.is_file():
        raise Refused(f"{p} not found: run --precount first (outcome-blind, on the real layout)")
    rec = json.loads(p.read_text(encoding="utf-8"))
    if rec.get("refused"):
        raise Refused(f"the precount refused: {rec.get('refusals')}")
    if rec.get("layout_digest") != layout_digest(g):
        raise Refused("precount.json was made with different code, views or V map: rerun --precount")
    return rec


# --- the tape pass (V-priced, exit lag 2, two stakes) -----------------------------------------------------------------


def worker_v(spec: Mapping[str, Any]) -> dict[str, Any]:
    """One chunk, V-priced. Module level so a spawn pool pickles it. The adapter wraps `print_from_trade_row` as the trigger module bound it; the
    V map's sha is re-checked in the worker; the original is restored on exit."""
    from tools import pumpswap_virtual_adapter as ad
    from tools.pumpswap_virtual import load_map

    e15.check_vmap(spec["vmap"], e15.VMAP_0909_SHA256, "worker V map")
    vmap = load_map(Path(spec["vmap"]))
    ad.reset_counts()
    orig = mt.print_from_trade_row
    mt.print_from_trade_row = ad.make_wrapper(orig, vmap, "v")
    try:
        out = mt.run_worker_m15(
            spec["worker_id"], spec["home"], spec["buf"], spec["creator_hist"], spec["rows_path"], spec["cens_path"], spec["hours"], row_iter_fn=CheckedTrades(),
            ks=spec["ks"], pool_tag=spec["tag"], pool_end_ms=spec["pool_end_ms"], pool_gap_starts_ms=spec["pool_gap_starts_ms"], sizes=spec["sizes"], exit_lag=spec["exit_lag"],
        )
    finally:
        mt.print_from_trade_row = orig
    out["tag"] = spec["tag"]
    out["v_counts"] = dict(ad.COUNTS)
    out["no_v_pools"] = sorted(ad._NO_V_POOLS)
    out.pop("rows", None)
    out.pop("censored", None)
    return out


def plan_chunks(g: Mapping[str, Any], scratch: Path, vmap: str) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for tag, src in g["layout"].items():
        pool = src["pool"]
        hist = s12.build_creator_history(src["hours"], pool)
        end_ms = bc.hour_ms(pool[-1]) + 3_600_000
        gaps = mt.missing_hour_starts_ms(pool)
        for i, home, buf in ff12.anchored_plan(pool, e15.MAX_HOME_HOURS, e15.BUFFER_HOURS):
            specs.append({
                "tag": tag, "worker_id": i, "home": home, "buf": buf, "creator_hist": hist, "hours": src["hours"], "vmap": vmap, "ks": KS, "sizes": SIZES, "exit_lag": EXIT_LAG,
                "pool_end_ms": end_ms, "pool_gap_starts_ms": gaps, "rows_path": scratch / f"{tag}-w{i}.rows.jsonl", "cens_path": scratch / f"{tag}-w{i}.censored.jsonl",
            })
    return specs


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def tape_pass(g: Mapping[str, Any], scratch: Path, vmap: str, max_workers: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scratch.mkdir(parents=True, exist_ok=True)
    specs = plan_chunks(g, scratch, vmap)
    n = min(max_workers, e15.TAPE_WORKERS_CAP, len(specs))
    if n <= 1:
        results = [worker_v(s) for s in specs]
    else:
        with mp.get_context("spawn").Pool(processes=n) as pool:
            results = pool.map(worker_v, specs, chunksize=1)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for s in specs:
        for r in read_jsonl(s["rows_path"]):
            ident = (r["mint"], r["entry_land_k"], r["size"])
            if ident in seen:
                raise SystemExit(f"duplicate (mint, d, size) {ident}")
            seen.add(ident)
            r["source"] = s["tag"]
            rows.append(r)
    stats = {
        "n_chunks": len(specs),
        "v_counts": {k: sum(r["v_counts"].get(k, 0) for r in results) for k in ("pumpswap_prints", "corrected", "no_v", "bonding_untouched")},
        "no_v_pools": sorted({p for r in results for p in r["no_v_pools"]}),
        "rows_after_scored_within_bound": sum(sum(r["counters"]["by_day"]["rows_after_scored_within_bound"].values()) for r in results),
        "n_censored": sum(r["n_censored"] for r in results),
    }
    return rows, stats


# --- costs, rows, selection -------------------------------------------------------------------------------------------


def row_nets(r: Mapping[str, Any]) -> dict[str, float]:
    """Net lamports per leg at the deciding costs: 505,000 per side, the haircut (sell -16 bps, entry +26.08 bps), both fail models. A MISS pays one fee."""
    nets = e15.cell_nets({"censored": False, "filled": r["filled"], "status": r["status"], "net0": r["net0"], "sides": r["sides"], "size": r["size"], "p_press": r["p_press"]})
    assert nets is not None
    return nets


def prepare_rows(rows: Sequence[Mapping[str, Any]], dates: Sequence[str], unpriceable: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Table rows -> screen rows: the deciding-cost `flat` / `press` replace the table's (the model label is 1{press > 0} on them); `excluded_by_time`,
    out-of-block-window, out-of-scope-day and no-V mints are removed and counted."""
    dset = set(dates)
    out: list[dict[str, Any]] = []
    drop = {"excluded_by_time": 0, "out_of_block_window": 0, "day_not_in_scope": 0, "unpriceable_mint": 0}
    for r in rows:
        if r["excluded_by_time"]:
            drop["excluded_by_time"] += 1
        elif not e15.in_block_window(block_of(r["source"]), int(r["mig_ms"])):
            drop["out_of_block_window"] += 1
        elif r["day"] not in dset:
            drop["day_not_in_scope"] += 1
        elif r["mint"] in unpriceable:
            drop["unpriceable_mint"] += 1
        else:
            n = row_nets(r)
            out.append({**r, "flat": n["flat"], "press": n["press"], "pool": r["source"]})
    return out, drop


def sel_trades(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{"mint": r["mint"], "day": r["day"], "filled": bool(r["filled"]), "flat": r["flat"], "press": r["press"], "source": r["source"]} for r in rows]


def index_rows(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, int, int], Mapping[str, Any]]:
    return {(r["mint"], r["entry_land_k"], r["size"]): r for r in rows}


def selected_rows(selected: Sequence[Mapping[str, Any]], idx: Mapping[tuple[str, int, int], Mapping[str, Any]], d: int, size: int) -> tuple[list[Mapping[str, Any]], int]:
    """The rows of the selected mints at (d, size); the second value counts selected mints with no row there (censored at that d or size)."""
    out, miss = [], 0
    for s in selected:
        r = idx.get((s["mint"], d, size))
        if r is None:
            miss += 1
        else:
            out.append(r)
    return out, miss


def transfer_selection(rows: Sequence[Mapping[str, Any]], train_days: Sequence[str], score_days: Sequence[str]) -> dict[str, Any]:
    """Bar 6: fit on the September days only, threshold = p90 of the pooled inner LODO scores over those days, score the August (P2) rows."""
    by_day, _ = mm._by_day(rows, list(train_days))
    pairs, _skipped = mm._lodo_scores(by_day, list(train_days))
    scores = sorted(s for _, s in pairs)
    thr = fz._percentile(scores, 0.90) if scores else None
    train = [r for d in train_days for r in by_day.get(d, [])]
    test = [r for r in mm.training_rows(rows) if r["day"] in set(score_days)]
    selected: list[Mapping[str, Any]] = []
    if thr is not None and len(train) >= mm.MIN_TRAIN_ROWS and len({mm.label(r) for r in train}) == 2:
        selected = [r for r, s in zip(test, mm.predict(mm.fit(train), test)) if s >= thr]
    return {"threshold_p90": thr, "n_train": len(train), "n_test": len(test), "selected": selected}


def jaccard_vs_frozen(sel_mints: set[str], eligible_mints: set[str], frozen_mints: set[str]) -> dict[str, Any]:
    """Item 6(a): A = EXP-014 selected mints; B = the frozen EXP-012 selection, restricted to mints that have an EXP-014 d = 4 row. An empty union fails."""
    b = frozen_mints & eligible_mints
    j = e15.jaccard(sel_mints, b)
    return {"n_a": len(sel_mints), "n_b": len(b), "n_overlap": len(sel_mints & b), "jaccard": j, "overlap_over_min": (len(sel_mints & b) / min(len(sel_mints), len(b))) if sel_mints and b else None, "pass": j is not None and j <= JACCARD_MAX}


def frozen_selected_mints(scratch: str) -> set[str]:
    """The frozen EXP-012 model's selected mints on the P2/P3/P4 cached EXP-015 rows (features only; the net fields are dropped at parse time)."""
    universe, _ = e17.load_universe(scratch, blind=True)
    scores = e17.frozen_scores(universe)
    mask = e17.frozen_mask(scores)
    return {u["mint"] for u, m in zip(universe, mask) if m and u["source"] in SOURCES}


def mean_pass(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(trades)}
    ok = bool(trades)
    for leg in LEGS:
        m = (sum(t[leg] for t in trades) / len(trades) / LAMPORTS) if trades else None
        out[leg] = {"mean_sol": m, "pass": m is not None and m > 0}
        ok = ok and out[leg]["pass"]
    out["pass"] = ok
    return out


def evaluate(rows: Sequence[dict[str, Any]], selected: Sequence[Mapping[str, Any]], transfer: Mapping[str, Any], frozen_mints: set[str], dates: Sequence[str]) -> dict[str, Any]:
    """Bars 1-6 (EXP-015 bars adapted; P1 is out of scope), item 5 (d = 8), item 6 (Jaccard, non-B mean), Holm k = 1."""
    idx = index_rows(rows)
    n_dates = len(dates)
    sept = [d for d in dates if d >= "2026-09-01"]
    main, _ = selected_rows(selected, idx, PRIMARY_K, DECIDING_SIZE)
    elig = [r for r in rows if r["entry_land_k"] == PRIMARY_K and r["size"] == DECIDING_SIZE]
    bars: dict[str, Any] = {}
    rep_all = e15.scope_report(sel_trades(main), n_dates)
    bars["bar1"] = {"scope": f"all {n_dates} non-P1 dates", "report": rep_all, "pass": rep_all["gate_all"]}
    rep_sept = e15.scope_report(sel_trades([r for r in main if r["day"] in set(sept)]), len(sept))
    bars["bar2"] = {"scope": f"September only (P3 + P4), {len(sept)} dates", "report": rep_sept, "pass": rep_sept["gate_all"]}
    sel_keys = {(s["mint"]) for s in selected}
    uni = [{"date": r["day"]} for r in elig]
    nets = [(row_nets(r) if r["mint"] in sel_keys else None) for r in elig]
    allnets = [row_nets(r) for r in elig]
    paired = e17.paired(uni, nets, allnets, list(range(len(elig))))
    bars["bar3"] = {"scope": "paired vs entering every mig+15 trigger (same costs), all dates", "report": paired, "pass": paired["pass_bar"]}
    bars["bar4"] = {"scope": "concentration, all dates", "report": {leg: rep_all[leg]["concentration"] for leg in LEGS}, "pass": bool(rep_all["concentration_all"])}
    p2p4 = mean_pass(sel_trades([r for r in main if r["source"] in ("P2", "P4")]))
    bars["bar5"] = {"scope": "P2 + P4 only", "report": p2p4, "pass": p2p4["pass"]}
    august = [d for d in dates if d < "2026-09-01"]
    tr_t = sel_trades(transfer["selected"])
    t_stats = {leg: e15.leg_stats(tr_t, leg) for leg in LEGS}
    t_ok = {leg: t_stats[leg]["mean_sol"] is not None and t_stats[leg]["mean_sol"] > 0 and t_stats[leg]["dates_positive"] * 2 > len(august) for leg in LEGS}
    bars["bar6"] = {"scope": f"fit on September dates, score P2 ({len(august)} dates)", "threshold_p90": transfer["threshold_p90"], "n": t_stats["flat"]["n"], "per_leg": t_ok,
                    "mean_sol": {leg: t_stats[leg]["mean_sol"] for leg in LEGS}, "pass": all(t_ok.values())}
    d8, d8_miss = selected_rows(selected, idx, 8, DECIDING_SIZE)
    item5 = mean_pass(sel_trades(d8))
    item5["selected_without_d8_row"] = d8_miss
    bars["item5_d8"] = {"scope": "same selected mints at d = 8, pooled mean > 0 both legs", "report": item5, "pass": item5["pass"]}
    eligible_mints = {r["mint"] for r in elig}
    sel_mints = {s["mint"] for s in selected}
    jac = jaccard_vs_frozen(sel_mints, eligible_mints, frozen_mints)
    non_b = [r for r in main if r["mint"] not in frozen_mints]
    nb = mean_pass(sel_trades(non_b))
    bars["item6"] = {"scope": "overlap with the frozen EXP-012 selection on the same dates", "jaccard": jac, "non_b_trades": nb, "pass": bool(jac["pass"] and nb["pass"])}
    ps = []
    for leg in LEGS:
        bd: dict[str, list[float]] = {}
        for t in sel_trades(main):
            bd.setdefault(t["day"], []).append(float(t[leg]))
        ps.append(e17.boot_p(bd))
    p = None if any(x is None for x in ps) else max(ps)
    hm = e17.holm({TRIES_KEY: p}, HOLM_ALPHA)
    assert len(hm) == HOLM_K
    bars["holm"] = {"scope": "date-cluster one-sided bootstrap p of mean > 0, max over legs, 10,000 draws, seed 1, Holm k = 1", "p": p, "alpha": HOLM_ALPHA, "report": hm, "pass": bool(hm[TRIES_KEY]["reject"])}
    order = ("bar1", "bar2", "bar3", "bar4", "bar5", "bar6", "item5_d8", "item6", "holm")
    passes = all(bars[k]["pass"] for k in order)
    big, _ = selected_rows(selected, idx, PRIMARY_K, SIZES[1])
    report_only = {
        "stake_0.5_sol": {"n": len(big), "report": e15.scope_report(sel_trades(big), n_dates) if big else None},
        "enter_all_mig15": {leg: (sum(n[leg] for n in allnets) / len(allnets) / LAMPORTS) if allnets else None for leg in LEGS},
        "p1": "not read",
    }
    return {"bars": bars, "passes": bool(passes), "n_selected": len(selected), "n_eligible_rows": len(elig), "report_only": report_only}


def outcome_line(rep: Mapping[str, Any]) -> str:
    if rep["passes"]:
        return "SCREEN PASS: EXP-014 may get a pre-registration on a not-yet-reserved block. This means 'worth one confirmation read', never 'has an edge'."
    failed = [k for k, v in rep["bars"].items() if not v["pass"]]
    return f"SCREEN FAIL: the family is closed (plan item 14). Failed: {', '.join(failed)}."


def render_md(rep: Mapping[str, Any]) -> str:
    lines = [f"# EXP-014 screen v2", "", BANNER, "", rep["outcome"], "", "| bar | scope | pass |", "| --- | --- | --- |"]
    for k, v in rep["evaluation"]["bars"].items():
        lines.append(f"| {k} | {v['scope']} | {v['pass']} |")
    lines += ["", f"Selected {rep['evaluation']['n_selected']} of {rep['evaluation']['n_eligible_rows']} eligible d = 4 rows (0.05 SOL).", ""]
    return "\n".join(lines) + "\n"


# --- tries (both logs) ------------------------------------------------------------------------------------------------


def data_blocks() -> list[dict[str, str]]:
    return [{"start_hour": e15.BLOCKS[b][0], "end_hour_exclusive": e15.BLOCKS[b][1], "host": "mal-research-0", "ledger_owner": "EXP-014 screen v2 (exploration pool)"} for b in SOURCES]


def log_try(logs: Sequence[Path], out_dir: Path, status: str) -> list[dict[str, Any]]:
    out, seen = [], set()
    for lg in logs:
        if Path(lg).resolve() in seen:
            continue
        seen.add(Path(lg).resolve())
        out.append(mal_result.append_try(lg, tool=TOOL, config={"key": TRIES_KEY, "experiment": "EXP-014 screen v2", "status": status, "k": list(KS), "exit_lag": EXIT_LAG,
                                                                 "fee_lamports": FEE, "pricing": "V", "stake_sol": 0.05, "holm_k": HOLM_K},
                                         data_blocks=data_blocks(), result_path=out_dir / OUT_SCREEN, role="exploration"))
    return out


# --- main ---------------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--p2-view-dir", type=Path, action="append", default=None, help="repeatable: explore-0814 clean views w1..w7")
    ap.add_argument("--p3-root", type=Path, default=Path(e15.P3_BASE))
    ap.add_argument("--p4-view-dir", type=Path, action="append", default=None, help="repeatable: exp011-0909 clean views b, c")
    ap.add_argument("--vmap", default=e15.VMAP_0909_PATH)
    ap.add_argument("--exp015-scratch", default=e17.DEFAULT_SCRATCH, help="cached EXP-015 rows (features only are read), for the frozen EXP-012 selection")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--tries-log", default=None)
    ap.add_argument("--canonical-tries", type=Path, default=CANONICAL_TRIES)
    ap.add_argument("--max-workers", type=int, default=e15.DEFAULT_WORKERS)
    ap.add_argument("--precount", action="store_true")
    ap.add_argument("--guards-only", action="store_true")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out_dir: Path = args.out_dir
    try:
        e15.refuse_reserved(out_dir, "--out-dir")
        mode_screen = not (args.precount or args.guards_only)
        logs = check_tries_logs(args.tries_log, Path(args.canonical_tries)) if mode_screen else []
        g = run_guards(args)
        if mode_screen:
            check_no_prior_tries(*logs)
            e15.check_run_lock(out_dir)
        if args.guards_only:
            print(json.dumps({"guards": "ok", "hours_present": g["hours_present"], "vmap_sha256": g["vmap_sha256"], "layout_digest": layout_digest(g)}, indent=2, default=str))
            return 0
        if args.precount:
            out_dir.mkdir(parents=True, exist_ok=True)
            rec = run_precount(g, args.vmap, args.max_workers)
            (out_dir / OUT_PRECOUNT).write_text(json.dumps(rec, indent=2, default=str) + "\n", encoding="utf-8")
            print(json.dumps({"mode": "precount", "refused": rec["refused"], "refusals": rec["refusals"], "outcome_blind": True}, indent=2))
            return 2 if rec["refused"] else 0
        pre = check_precount(out_dir, g)
        dates = e15.non_p1_dates(True)
        frozen_mints = frozen_selected_mints(args.exp015_scratch)
        head = e15.git_state()["head"]
        e15.take_lock(out_dir, head, layout_digest(g))
    except (Refused, e15.Refused) as exc:
        print(f"refusing (before started, no tries line): {exc}", file=sys.stderr)
        return 2
    t0 = time.time()
    started, status = False, "aborted_after_read"
    try:
        log_try(logs, out_dir, "started")  # before any outcome is computed: a crash still spends the try
        started = True
        rows, stats = tape_pass(g, out_dir / "scratch", args.vmap, args.max_workers)
        if stats["rows_after_scored_within_bound"] > 0:
            raise RuntimeError(f"rows_after_scored_within_bound = {stats['rows_after_scored_within_bound']}: a mint was scored on incomplete tape")
        nopr = stats["v_counts"]["no_v"] / max(1, stats["v_counts"]["pumpswap_prints"])
        if nopr > V_MAX_MISSING_FRACTION:
            raise RuntimeError(f"{nopr:.2%} of PumpSwap prints had no V in the tape pass (> {V_MAX_MISSING_FRACTION:.0%})")
        unpriceable = {m for c in pre["counts"].values() for m in c["unpriceable_mints"]}
        srows, dropped = prepare_rows(rows, dates, unpriceable)
        main_rows = [r for r in srows if r["size"] == DECIDING_SIZE]
        selected, folds, row_counts = mm.nested_lodo_select(main_rows, dates, n_jobs=min(args.max_workers, e15.MAX_WORKERS_CAP))
        sept = [d for d in dates if d >= "2026-09-01"]
        transfer = transfer_selection(main_rows, sept, [d for d in dates if d < "2026-09-01"])
        ev = evaluate(srows, selected, transfer, frozen_mints, dates)
        rep = {"schema": SCHEMA, "head": head, "banner": BANNER, "evaluation": ev, "tape_stats": stats, "dropped_rows": dropped, "row_counts": row_counts, "folds": folds,
               "precount_digest": pre["layout_digest"], "wall_s": time.time() - t0}
        rep["outcome"] = outcome_line(ev)
        (out_dir / OUT_SCREEN).write_text(json.dumps(rep, indent=2, default=str) + "\n", encoding="utf-8")
        (out_dir / OUT_MD).write_text(render_md(rep), encoding="utf-8")
        log_try(logs, out_dir, "completed")
        status = "completed"
        print(rep["outcome"])
        return 0
    except Exception as exc:  # noqa: BLE001 - the try is spent after `started`; record and stop
        print(f"aborted after started: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    finally:
        e15.write_record(out_dir, status, started, {TRIES_KEY: status})


if __name__ == "__main__":
    sys.exit(main())
