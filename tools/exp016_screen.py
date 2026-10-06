#!/usr/bin/env python3
"""EXP-016 screen (PR 2 of 2, exploration): a rug-risk entry veto layered on the frozen EXP-012 book.

**EXPLORATION ONLY. NOT A PRE-REGISTRATION, NOT A PROMOTE, NOT GATE EVIDENCE.** Plan: EXP/EXP-016-rug-veto-plan.md (the merged #381 plus
the section 13 post-pin edits, #393). Section numbers below are that plan's. The label, features, P2 filter and counters are
`tools/exp016_rug.py` (#392). This module is the SCREEN only (section 7). It does not build the confirmation scorer (Part 1 and 2).

Order of a run, and what may be printed when
  1. Guards (before any row): the plan's blocks by role, VIEW.sha256 and dedupe manifests, frozen model md5 and threshold, the V map
     sha256 (VMAP_EXP016_SHA256, a PLACEHOLDER until the manager fills it in a reviewed commit: the tool refuses at startup), no earlier
     `exp016_*` tries line, no RUN.lock without a record.
  2. ONE tape pass per source. Rows are admitted exactly as `latency_curve.run_holdout` admits them (int `block_time`), the simulator is
     fed migration-pool PumpSwap rows only (`restrict_rows_to_migration_pool`), and ONE merged V map (`merge_v_map`) goes to both the
     label and `make_wrapper`. The simulator is the frozen one (`eem.score_one` under `op.multi_cell_patch`), unedited.
  3. PRE-`started`, outcome-blind (ids and counts only): mints with no migration pool, closed vs parse-fail pools among the frozen-selected,
     censored cells (status only), foreign-first mints, slot inversions, the P2 gate, V coverage, the P1 V-constancy check, the prior
     tries per pool read from data/tries.jsonl. Nothing outcome-derived (no net, no label) is printed or written before `started`.
  4. The ONE `started` tries line (cap 6 = the candidates), with the row-universe and feature-table sha256 and the prior counts, under
     RUN.lock (O_EXCL). After `started` there is no resume.
  5. AFTER `started`, before any candidate is scored: G1 (label too broad), G2 (label too rare), and the silent-pool cell count.
  6. The 6 candidates and nested leave-one-date-out (no veto is always a candidate and wins ties), bars S1-S6, report-only items.

Run (mal-research-0, as a MiScusi job, one heavy job at a time, under nice): see `_parser`. Memory: the pass holds each source's rows in
memory (full rows for migrated mints, slimmed rows for the rest), not measured: request a measured run before the real job.
"""

from __future__ import annotations

import argparse
import collections
import gc
import hashlib
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import tools.exp011_freeze as fz
import tools.exp012_backcheck as bc
import tools.exp012_operating_point as op
import tools.exp015_screen as e15
import tools.exp016_rug as rug
import tools.exploration_entry_model as eem
import tools.exploration_exits as xx
from tools.exp012_latency_sensitivity import DEFAULT_ARTIFACT_DIR, load_oof
from tools.latency_curve import FLAT_FAIL, MISS
from tools.pumpswap_virtual_adapter import make_wrapper
from tools.paper_price_path import print_from_trade_row

TOOL = "tools.exp016_screen"
SCHEMA = "exp016_screen_v1"
REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_TRIES = REPO_ROOT / "data" / "tries.jsonl"
PLAN = REPO_ROOT / "EXP" / "EXP-016-rug-veto-plan.md"
LAMPORTS = 1_000_000_000

BANNER = (
    "EXPLORATION SCREEN (EXP-016 plan section 7). Not a pre-registration, not a promote, not gate evidence. A pass means 'worth Part 1 and one "
    "confirmation read', never 'has an edge'. The nested leave-one-date-out trains on days before and after each test day (no drift view); exit lag 2 is "
    "optimistic against the live exit leak; the label rests on a few dozen events."
)

# --- pinned numbers (each tied to the plan by tools/test_exp016_screen.py) -------------------------------------------------------
# ONE pool -> V map for P1-P4 (fixed parser, #383), pinned by sha256 in a reviewed commit BEFORE the first row is read (plan section 11 P1).
# While it is the placeholder the tool refuses at startup (check_pin_ready), as EXP-015's PENDING pattern.
VMAP_EXP016_SHA256 = "PENDING"
VMAP_EXP016_PATH = "/data/mal/pumpswap-virtual/pool_v_exp016.json"
K = e15.K  # 6
SIZE_SOL = e15.SIZE_SOL  # 0.05
FEE = e15.FEE  # 505,000 per side
EXIT_LAG = e15.EXIT_LAG  # 2
HAIRCUT_FACTOR = e15.HAIRCUT_FACTOR  # 0.0042038
PURGE_MIN = e15.PURGE_MIN  # 35
BOOT_DRAWS, BOOT_SEED = e15.BOOT_DRAWS, e15.BOOT_SEED  # 1,000 and seed 1
SEED = 1
TRIES_CAP = 6
R_THRESHOLD = 0.12  # R1-R3: 12% of supply sold into a seed pool is a label-sized step (section 2.3, 5.1)
R4_MIN_DUMPS = 1
VETO_FRACTIONS = {"l5": 0.05, "l10": 0.10}
MIN_VETOED_FILLED = 30  # eligibility floor (section 5.3 step 2)
MAX_VETO_FRACTION = 0.20  # eligibility cap and S5
G1_MAX_RATE = 0.15  # G1: LABEL_TOO_BROAD above this, all pool dates
G2_MIN_RUGS = 30  # G2: LABEL_TOO_RARE below this, non-P1 dates
LIFT_MIN = 2.0  # S3
TOP_AVOIDED = 3  # S4
V_RESERVE_SIZE = 20  # pre-declared reserve draw (plan 13 item 8): replaces primary pools whose constancy transaction is unreadable
V_SAMPLE_SIZE = 200  # P1 constancy sample (section 11 P1; "200 of 200" in the V investigation)
V_TOL_BPS = 1.0  # a pool disagrees if |V_implied - V_map| > max(1 bp of that print's quote reserve, 0.002 SOL)
V_TOL_LAMPORTS = 2_000_000
V_DISAGREE_MAX = 0.01  # refuse if more than 1% of the sample disagrees
V_COVERAGE_MIN = 0.99  # canonical traded pools with a readable stored V (screen refusal; the confirmation lock uses the same)
LOGREG = next(s for s in eem.SETTINGS if s["id"] == "logreg_l2")  # C = 0.5, L2, balanced, seed 1, imported unchanged
MIN_FIT_ROWS = 20
TARGET = fz.TARGET_SPEC_ID
LOG1P_FEATURES = ("n_launch_buyers", "n_create_slot_buyers", "creator_n_buys_after", "creator_n_sells", "creator_prior_dumps", "creator_buyer_recurrence", "n_buyers")
FEATURES18 = list(rug.FEATURE_NAMES)
assert len(FEATURES18) == 18
CELL_KEYS = e15.CELL_KEYS  # (6,2) primary; (6,0), (4,2), (8,2) report-only
PRIMARY_CELL = e15.PRIMARY_CELL
COMBOS = e15.COMBOS
LEGS = e15.LEGS
TL_NET = -float(op.size_lamports(SIZE_SOL) + 2 * FEE)  # total loss of a filled trade: -(size + both fees)

# Candidates: the whole try budget (section 5.1). Order is the tie-break order.
CANDIDATES: dict[str, dict[str, Any]] = {
    "r1": {"key": "exp016_r1", "name": "R1", "kind": "rule", "feature": "launch_supply_held", "desc": f"launch_supply_held >= {R_THRESHOLD}"},
    "r2": {"key": "exp016_r2", "name": "R2", "kind": "rule", "feature": "serial_launch_held", "desc": f"serial_launch_held >= {R_THRESHOLD}"},
    "r3": {"key": "exp016_r3", "name": "R3", "kind": "rule", "feature": "prior_dumper_held", "desc": f"prior_dumper_held >= {R_THRESHOLD}"},
    "r4": {"key": "exp016_r4", "name": "R4", "kind": "rule", "feature": "creator_prior_dumps", "desc": f"creator_prior_dumps >= {R4_MIN_DUMPS}"},
    "l5": {"key": "exp016_l5", "name": "L5", "kind": "logit", "fraction": VETO_FRACTIONS["l5"], "desc": "logistic (logreg_l2), veto the top 5%"},
    "l10": {"key": "exp016_l10", "name": "L10", "kind": "logit", "fraction": VETO_FRACTIONS["l10"], "desc": "logistic (logreg_l2), veto the top 10%"},
}
assert len(CANDIDATES) == TRIES_CAP
STARTED_KEY = "exp016_started"
EXTRA_RESERVED = ("fresh-0802",)  # the confirmation block (plan 4); EXP-015's fragment list does not carry it
NONE_ID = "none"  # the frozen book, always a candidate

OUT_REPORT, OUT_MD = "report.json", "report.md"
OUT_UNIVERSE, OUT_FEATURES = "universe.sha256", "feature_table.sha256"
MARKER = "tries_logged.marker"
OUTCOME_PASS = "SCREEN PASS: Part 1 and Part 2 are written and the section 8 confirmation follows. This means 'worth one read', never 'has an edge'."
OUTCOME_FAIL = ("SCREEN FAIL: EXP-016 failed its screen. The family is closed: no new label, rule, threshold, fraction or feature set on these pools. "
                "The confirmation block is released, unread, by a ledger edit.")
OUTCOME_G1 = "LABEL_TOO_BROAD (G1) = SCREEN FAIL. The family is closed (no second label, no threshold change, no new window)."
OUTCOME_G2 = "LABEL_TOO_RARE (G2) = SCREEN FAIL. The family is closed (no second label, no threshold change, no new window)."
OUTCOME_INCOMPLETE = "SCREEN NOT DECIDED: the run was refused or aborted after `started`. No pass is claimed; the tries are spent."


class Refused(Exception):
    code = 2


class SimulationError(Exception):
    """Any unexpected failure inside one mint's simulation, re-raised with the mint id and the exception TYPE only: the frozen helpers' messages can
    carry nets, and nothing outcome-derived may be printed before `started`."""


class SimulatorDrift(Refused):
    """The exit print this tool derived does not reproduce the frozen simulator's net: refuse rather than label the wrong window. A Refused (caught
    by `main` like any other), and its message names the mint id only: no net, no outcome number."""


# --- guards ------------------------------------------------------------------------------------------------------------------


def check_pin_ready() -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", VMAP_EXP016_SHA256):
        raise Refused(f"VMAP_EXP016_SHA256 is {VMAP_EXP016_SHA256!r}, not a sha256: the manager pins the fixed-parser map in a reviewed commit "
                      "before any row is read. Refusing to run")


def check_vmap(path: str | Path) -> str:
    try:
        return e15.check_vmap(path, VMAP_EXP016_SHA256, "V map")
    except e15.Refused as exc:
        raise Refused(str(exc)) from None


def load_pinned_vmap(path: str | Path) -> dict[str, int | None]:
    from tools.pumpswap_virtual import load_map

    check_vmap(path)
    return load_map(Path(path))


def prior_exp016_lines(log: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not Path(log).is_file():
        return out
    for line in Path(log).read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if str((rec.get("config") or {}).get("key", "")).startswith("exp016_"):
            out.append(rec)
    return out


def check_no_prior_tries(*logs: Path) -> None:
    for log in {str(Path(x).resolve()): Path(x) for x in logs}.values():
        found = prior_exp016_lines(log)
        if found:
            raise Refused(f"{log} already holds {len(found)} exp016_* line(s); the try budget (6 candidates) is spent or started. The run refuses (plan section 5.4)")


def prior_tries_per_experiment(log: Path, with_p4: bool) -> dict[str, dict[str, int]]:
    """Per-experiment prior-try counts from the canonical log (plan 5.4), recorded in the `started` line: experiment -> {"total", pool: n}. The
    experiment is `config.experiment`, else the line's `tool`. EXP-016's own lines are excluded. A line counts on a pool as in `prior_tries_per_pool`."""
    pools = list(e15.active_blocks(with_p4))
    out: dict[str, dict[str, int]] = {}
    if not Path(log).is_file():
        return out
    for line in Path(log).read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        cfg = rec.get("config") or {}
        if str(cfg.get("key", "")).startswith("exp016_"):
            continue
        name = str(cfg.get("experiment") or rec.get("tool") or "unknown")
        d = out.setdefault(name, {"total": 0, **{b: 0 for b in pools}})
        d["total"] += 1
        for b in pools:
            a, z = e15.BLOCKS[b]
            if any(str(x.get("start_hour", "")) < z and str(x.get("end_hour_exclusive", "")) > a for x in rec.get("data_blocks") or []):
                d[b] += 1
    return dict(sorted(out.items()))


def prior_tries_per_pool(log: Path, with_p4: bool) -> dict[str, int]:
    """Lines already in the tries log per pool, read at the `started` line (plan 5.4): a line counts on a pool if one of its data blocks
    overlaps the pool's counted window. EXP-016's own lines are excluded. A line that touches two pools counts on both."""
    out = {b: 0 for b in e15.active_blocks(with_p4)}
    if not Path(log).is_file():
        return out
    for line in Path(log).read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if str((rec.get("config") or {}).get("key", "")).startswith("exp016_"):
            continue
        for b in out:
            a, z = e15.BLOCKS[b]
            if any(str(d.get("start_hour", "")) < z and str(d.get("end_hour_exclusive", "")) > a for d in rec.get("data_blocks") or []):
                out[b] += 1
    return out


def refuse_extra_reserved(args: argparse.Namespace) -> None:
    """EXP-015's fragment list does not carry the confirmation block fresh-0802; refuse it here, in every root argument (raw and resolved)."""
    for label, v in (("P1 fast", args.p1_fast_dir), ("P1 insample", args.p1_oracle_insample_dir), ("P1 live", args.p1_oracle_live_dir), ("P3", args.p3_root),
                     *[("P2 view", q) for q in (args.p2_view_dir or [])], *[("P4 view", q) for q in (args.p4_view_dir or [])]):
        for raw in (str(v), os.path.realpath(str(v))):
            if any(f in raw for f in EXTRA_RESERVED):
                raise Refused(f"{label} path {str(v)!r} is the confirmation block ({EXTRA_RESERVED}); EXP-016 never reads it before its lock")


def run_guards(args: argparse.Namespace, enforce_base: bool = True, verify: bool = True, pin_required: bool = True) -> dict[str, Any]:
    if pin_required:
        check_pin_ready()
    try:
        e15.check_workers(args.max_workers)
        g1 = e15.guard_p1(args.p1_fast_dir, args.p1_oracle_insample_dir, args.p1_oracle_live_dir, verify)
        if not args.p2_view_dir:
            raise Refused("--p2-view-dir is required (explore-0814 w1..w7)")
        g2 = e15.guard_p2(args.p2_view_dir, verify, enforce_base)
        g3 = e15.guard_p3(args.p3_root, verify, enforce_base)
        g4 = e15.guard_p4(args.p4_view_dir or [], verify)
        frozen = e15.check_frozen_model(args.artifact_dir)
        p3_hours = [h for w in g3["walkers"] for h in w.hours]
        e15.assert_hours_allowed(list(g2["pool"]) + list(g4["pool"] if g4 else []) + p3_hours, with_p4=g4 is not None)
    except e15.Refused as exc:
        raise Refused(str(exc)) from None
    refuse_extra_reserved(args)
    sha = check_vmap(args.vmap) if pin_required else None
    return {"g1": g1, "g2": g2, "g3": g3, "g4": g4, "vmap_sha256": sha, "frozen": frozen, "with_p4": g4 is not None}


# --- V constancy (P1, outcome-blind) ------------------------------------------------------------------------------------------


def sample_pools(pools: Iterable[str], n: int = V_SAMPLE_SIZE, seed: int = SEED) -> list[str]:
    """Deterministic sample (sorted, seeded) of pool ids for the constancy check."""
    import random

    ps = sorted(set(pools))
    if len(ps) <= n:
        return ps
    return sorted(random.Random(seed).sample(ps, n))


def reserve_pools(pools: Iterable[str], primary: Iterable[str], n: int = V_RESERVE_SIZE, seed: int = SEED + 1) -> list[str]:
    """Ordered reserve: a seeded draw (seed SEED+1, draw order kept) of `n` pools from the readable pools minus the primary sample."""
    import random

    pop = sorted(set(pools) - set(primary))
    return random.Random(seed).sample(pop, min(n, len(pop)))


def check_v_constancy(samples: Sequence[Mapping[str, Any]], vmap: Mapping[str, int | None], *, min_sample: int = V_SAMPLE_SIZE) -> dict[str, Any]:
    """P1's check. Each sample: {pool, v_implied (lamports, from the pool's own swaps, `tools.pumpswap_virtual_history`), quote_reserve (that
    print's vault quote)}. A pool DISAGREES if |v_implied - stored V| > max(1 bp of the quote reserve, 0.002 SOL); a pool with no readable stored V
    (None, or absent) is not a disagreement (it is priced by the section 4 rule). Refuses if the sample is smaller than `min_sample` or more than 1% disagree.
    A sample row whose `v_implied` is null or missing (the constancy tool could not read that pool's transaction) is NOT CHECKED: it is counted in
    `n_unreadable_implied`, never indexed, and does not count toward n_checked. Consequence, pinned and deliberate: the pre-declared reserve
    (plan 13 item 8(c)) replaces such a row first; the floor (n_checked < 200) refuses only when no replacement is possible. The floor is not lowered. Reports pool ids and differences only."""
    bad: list[dict[str, Any]] = []
    n_checked = 0
    n_unreadable_implied = 0
    for s in samples:
        if s.get("v_implied") is None:
            n_unreadable_implied += 1
            continue
        v = vmap.get(s["pool"])
        if v is None:
            continue
        n_checked += 1
        tol = max(int(s["quote_reserve"]) * V_TOL_BPS / 1e4, V_TOL_LAMPORTS)
        diff = float(s["v_implied"]) - int(v)
        if abs(diff) > tol:
            bad.append({"pool": s["pool"], "diff_lamports": diff, "tolerance_lamports": tol})
    if n_checked < min_sample:  # the floor and the rate both use the pools actually CHECKED (a readable stored V), never the sample size
        raise Refused(f"V-constancy check: only {n_checked} sampled pool(s) were checked (readable stored V and readable implied V; {n_unreadable_implied} unreadable implied), fewer than the pinned {min_sample}")
    rate = len(bad) / n_checked
    rec = {"n_sample": len(samples), "n_checked": n_checked, "n_unreadable_implied": n_unreadable_implied, "n_disagree": len(bad), "rate": rate, "max_rate": V_DISAGREE_MAX, "disagreeing": bad}
    if rate > V_DISAGREE_MAX:
        raise Refused(f"V-constancy check: {len(bad)} of {n_checked} checked pools disagree ({rate:.2%} of {n_checked} checked > {V_DISAGREE_MAX:.0%}); pools: {bad[:5]}")
    return rec


def readable_pools(pools: Iterable[str], vmap: Mapping[str, int | None]) -> list[str]:
    """The constancy population: P2 pools with a READABLE stored V (a pool without one cannot be checked, so it is not sampled)."""
    return sorted({p for p in pools if vmap.get(p) is not None})


def p2_eligible_pools(src: SourceData) -> list[str]:
    """Outcome-blind: the P2 pools the screen keeps (a migration pool, a create row and a usable migration slot), the same set process_source makes cells for."""
    pool_by_mint = rug.migration_pool_map(src.migrations.values())
    out = set()
    for m, p in pool_by_mint.items():
        ms = src.migrations[m].get("slot")
        if src.creates.get(m) is not None and isinstance(ms, int) and not isinstance(ms, bool) and ms > 0:
            out.add(p)
    return sorted(out)


def emit_constancy_sample(args: argparse.Namespace) -> int:
    """`--emit-constancy-sample OUT.json`: the exact seeded P2 sample for the separate constancy job. Runs the guards and reads only the P2 source to
    find its eligible pools (no simulation, no label, no price). Writes pool ids only. No lock, no tries line."""
    try:
        g = run_guards(args)
        vmap_raw = load_pinned_vmap(args.vmap)
    except (Refused, rug.PoolAttributionRefusal) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    pools: list[str] = []
    for tag, block, hours_fn, pool_hours, mig_roots in build_sources(g):
        if tag == "P2":
            pools = p2_eligible_pools(load_source_data(tag, block, hours_fn, pool_hours, mig_roots))
    readable = readable_pools(pools, vmap_raw)
    sample = sample_pools(readable)
    reserve = reserve_pools(readable, sample)
    Path(args.emit_constancy_sample).write_text(json.dumps({"primary": sample, "reserve": reserve}) + "\n", encoding="utf-8")
    print(f"wrote {len(sample)} sampled + {len(reserve)} reserve pool id(s) of {len(pools)} P2 pools ({len(readable_pools(pools, vmap_raw))} with a readable V); pool ids only", file=sys.stderr)
    return 0


def check_constancy_sample(samples: Sequence[Mapping[str, Any]], p2_pools: Iterable[str]) -> None:
    """After the tape pass, before `started`: the constancy file must hold exactly the seeded `sample_pools()` draw (primary) plus the first k pools of
    the pre-declared `reserve_pools()` order, where k = the number of primary rows with a null `v_implied` (every row present, nulls included)."""
    pools = list(p2_pools)
    got = [s.get("pool") for s in samples]
    primary = sample_pools(pools)
    reserve = reserve_pools(pools, primary)
    k = sum(1 for s in samples if s.get("pool") in set(primary) and s.get("v_implied") is None)
    want = primary + reserve[:k]
    if len(got) != len(set(got)) or set(got) != set(want):
        raise Refused(f"the --v-constancy-json pools ({len(set(got))}) are not the seeded sample_pools() draw plus its first {k} reserve pool(s) from the {len(set(pools))} P2 pools ({len(want)}); refusing")


def v_coverage(pools: Iterable[str], vmap: Mapping[str, int | None]) -> dict[str, Any]:
    """Share of canonical traded pools with a READABLE stored V (a value <= 0 is readable and means vault-only). Refuses at or below 99%."""
    ps = sorted(set(pools))
    readable = [p for p in ps if vmap.get(p) is not None]
    cov = (len(readable) / len(ps)) if ps else 1.0
    rec = {"n_pools": len(ps), "n_readable": len(readable), "coverage": cov, "min": V_COVERAGE_MIN, "unreadable": [p for p in ps if vmap.get(p) is None][:50]}
    if cov <= V_COVERAGE_MIN:
        raise Refused(f"V coverage {cov:.3%} of {len(ps)} canonical pools is not over {V_COVERAGE_MIN:.0%}")
    return rec


# --- tape: admission and the single-mint simulation ---------------------------------------------------------------------------


def admit_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Admit rows exactly as `latency_curve.run_holdout` does: an int `block_time` (else the row is skipped), `t_recv_ms` defaulting to
    block_time * 1000. Rows are copied (the tape rows are never edited)."""
    out = []
    for r in rows:
        block = r.get("block_time")
        if not isinstance(block, int):
            continue
        r = dict(r)
        if r.get("t_recv_ms") is None:
            r["t_recv_ms"] = block * 1000
        out.append(r)
    return out


def resolve_key(stamped: Sequence[tuple[Mapping[str, Any], rug.Key]], fill: Any, *, mint: str = "mint", endpoint: str = "fill") -> rug.Key:
    """Map a simulator fill (a collapsed `TapePrint`) to the label's stamp key. Exact (t, slot, position, event_index) if present, else the unique
    (slot, position, event_index) match (the simulator collapses a signature to its last event at the latest receive time, plan note 5 of #392)."""
    fk = (int(fill.t_recv_ms), int(fill.slot), int(fill.tx_index), int(fill.event_index))
    keys = [k for _r, k in stamped if r_is_pumpswap(_r)]
    if fk in keys:
        return fk
    hit = [k for k in keys if k[1:] == fk[1:]]
    if len(hit) != 1:
        raise rug.PoolAttributionRefusal(f"{mint}: the {endpoint} fill does not match exactly one migration-pool row (no time, slot or price is printed)")
    return hit[0]


def r_is_pumpswap(r: Mapping[str, Any]) -> bool:
    return r.get("venue") == "pumpswap"


def tpsl_exit(fills: Sequence[Any], entry_idx: int, buy: Any, venue: str, landing_ms: int, tape_through_ms: int, lag: int, spec: Mapping[str, Any]) -> dict[str, Any] | None:
    """Mirror of `exploration_exits._eval_tpsl` that also says WHICH print the exit fills against (the label's hold window end) and why
    (tp / sl / cap). None = the exit deadline is past the tape end (censored). The caller cross-checks the net against the simulator."""
    mark = xx.spot_sol_per_ui(buy.quote_after, buy.base_after)
    if mark <= 0:
        return None
    deadline = landing_ms + int(spec["cap_ms"])
    hit = None
    kind = "cap"
    for pr in fills[entry_idx + 1:]:
        if pr.t_recv_ms > deadline:
            break
        book = xx.reserves_with_our_buy(quote_lamports=pr.quote_reserve, base_raw=pr.base_reserve, net_in_lamports=buy.net_in_lamports,
                                        tokens_raw=buy.tokens_raw, same_venue=pr.venue == venue)
        if book is None:
            continue
        spot = xx.spot_sol_per_ui(book[0], book[1])
        if spot <= 0:
            continue
        ret = spot / mark - 1.0
        if ret >= spec["tp"] or ret <= -spec["sl"]:
            hit, kind = pr, ("tp" if ret >= spec["tp"] else "sl")
            break
    if hit is None:
        state_idx, t_exit = xx._deadline_fill(fills, deadline, lag)
    else:
        state_idx, t_exit = xx._exit_fill(fills, hit, lag)
    if t_exit > tape_through_ms:
        return None
    return {"state_idx": state_idx, "t_exit": t_exit, "kind": kind, "deadline_ms": deadline}


def _target_spec() -> dict[str, Any]:
    return next(s for s in xx.build_specs() if s["id"] == TARGET)


def make_mint(create_row: Mapping[str, Any]) -> tuple[Any, Any] | None:
    """(_Mint, _Feat) from a create row, as `eem._load_creates_full`."""
    mint_id, slot, block = create_row.get("mint"), create_row.get("slot"), create_row.get("block_time")
    if not isinstance(mint_id, str) or not isinstance(slot, int) or not isinstance(block, int):
        return None
    qm = create_row.get("quote_mint")
    if isinstance(qm, str) and qm and qm != eem.WSOL:
        return None
    block_ms = block * 1000
    sig = create_row.get("signature") if isinstance(create_row.get("signature"), str) else None
    anchor = eem._anchor(dict(create_row), slot, block_ms, sig)
    creator = create_row.get("creator")
    return eem._Mint(slot, block_ms, 0, anchor), eem._Feat(creator if isinstance(creator, str) else "", block_ms, anchor.price_sol if anchor is not None else None)


def simulate_mint(
    mint_id: str,
    create_row: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    pool: str,
    migration_slot: int,
    vmap: Mapping[str, int | None],
    tape_through_ms: int,
    creator_hist: Mapping[str, list[int]],
    history: rug.BlockHistory | None = None,
    curve: Any = None,
) -> dict[str, Any]:
    """One migrated mint with a migration pool -> one cell record (plan sections 2, 3, 6). The frozen simulator runs on migration-pool rows
    only, V-priced by `make_wrapper(print_from_trade_row, vmap)` with the SAME merged map the label prices with. Nothing frozen is edited."""
    admitted = admit_rows(rows)
    fed = list(rug.restrict_rows_to_migration_pool(admitted, pool))
    wrapped = make_wrapper(print_from_trade_row, vmap)
    made = make_mint(create_row)
    if made is None:
        return {"mint": mint_id, "pool": pool, "status": "NO_SIM", "why": "bad create row"}
    mint, feat = made
    curve = curve if curve is not None else xx._curve()
    for row in fed:
        if mint.mig_slot is None and row.get("venue") == "pump_bonding":  # feature accumulation, as run_worker_features
            feat.record(row, row["t_recv_ms"])
        parsed = wrapped(row)
        if parsed is None:
            continue
        mint.add(parsed[1])
    if mint.mig_slot is None or mint.mig_ms is None:
        return {"mint": mint_id, "pool": pool, "status": "NO_SIM", "why": "no canonical-pool print after a bonding print"}
    # the report cells under the multi-cell capture (as exp015), then the frozen k=1 row for the EXP-012 features (unedited score_one)
    with op.multi_cell_patch(e15._AllScores(), 0.0, combos=COMBOS):
        raw_cells = eem.score_one(mint_id, mint, feat, curve, tape_through_ms, creator_hist)
    frozen = [r for r in eem.score_one(mint_id, mint, feat, curve, tape_through_ms, creator_hist) if r["spec"] == TARGET]
    exp12 = [float(frozen[0]["features"].get(n, 0.0)) for n in fz.FROZEN_FEATURE_NAMES] if frozen else None
    cells = {(int(c["k"]), int(c.get("exit_lag", 0))): e15._slim_cell({**c, "exit_lag": c.get("exit_lag", 0)}) for c in raw_cells}
    prim = cells.get(PRIMARY_CELL)
    if exp12 is None and not (prim is not None and prim["censored"]):
        return {"mint": mint_id, "pool": pool, "status": "NO_SIM", "why": "no frozen row"}
    rec: dict[str, Any] = {
        "mint": mint_id, "pool": pool, "mig_ms": int(mint.mig_ms), "date": e15.utc_date(int(mint.mig_ms)), "exp012_features": exp12, "cells": cells,
        "slot_inversions": rug.count_slot_inversions(rows), "label191": None,
    }
    fills, trigger_slot, _tb, ref = eem._fills_for(mint, migrate=True)
    size = op.size_lamports(SIZE_SOL)
    target = trigger_slot + K
    idx = eem._state_index(fills, target, xx.ENTRY_BOUND)
    state = fills[idx] if idx >= 0 else None
    fallback = fills[idx].t_recv_ms if idx >= 0 else _tb
    landing_ms = eem._slot_time(fills, target, fallback)
    rec["landing_ms"] = landing_ms
    if prim is None:
        rec["status"] = "NO_SIM"
        rec["why"] = "no primary cell"
        return rec
    # plan 2.2 (manager ruling 2026-10-06): a cell is censored iff its exit deadline is beyond the block edge, whether or not tp/sl would hit first
    if prim["censored"] or landing_ms + int(_target_spec()["cap_ms"]) + EXIT_LAG * xx.SLOT_MS > tape_through_ms:
        rec["status"] = "CENSORED"
        return rec
    if not prim["filled"]:
        rec["status"] = "MISS"
    else:
        rec["status"] = "FILLED"
        buy = eem._try_buy(state, size, xx.ENTRY_PORTAL_PPM, ref)
        spec = _target_spec()
        ex = tpsl_exit(fills, idx, buy, state.venue, landing_ms, tape_through_ms, EXIT_LAG, spec)
        if ex is None:  # reachable only on a mirror/simulator disagreement: the deadline rule above already censored every edge case
            raise SimulatorDrift(f"{mint_id}: the simulator filled the primary cell but the mirrored exit is censored (no outcome value is printed)")
        closed = xx._one_sell_close(fills, ex["state_idx"], buy, state.venue, size, xx.ENTRY_PORTAL_PPM)
        if closed is None or int(closed[0]) != int(prim["net0"]):
            raise SimulatorDrift(f"{mint_id}: the mirrored exit does not reproduce the simulator's close (no outcome value is printed)")
        exit_fill = fills[ex["state_idx"]] if ex["state_idx"] >= 0 else state
        rug.check_migration_pool_only(fed, pool, [state, exit_fill], mint=mint_id, names=("entry", "exit"))  # P2: both endpoints are canonical-pool prints, or refuse
        stamped = rug.stamp_rows([r for r in fed if r.get("trader") != rug.PROBE_WALLET])  # the label drops our own wallet before stamping
        lab = rug.label_trade(admitted, migration_pool=pool, entry_key=resolve_key(stamped, state, mint=mint_id, endpoint="entry"),
                              exit_key=resolve_key(stamped, exit_fill, mint=mint_id, endpoint="exit"), vmap=vmap, mint=mint_id)
        rec.update({"exit_kind": ex["kind"], "deadline_ms": ex["deadline_ms"], "label": _label_dict(lab)})
        rec["label191"] = _label191(fills, idx, landing_ms)
    rec["features"] = rug.features(mint_id, create_row=create_row, rows=admitted, migration_slot=migration_slot, history=history)
    return rec


def _label_dict(lab: rug.LabelResult) -> dict[str, Any]:
    return {"status": lab.status, "rug": lab.rug, "event": lab.event, "drop": lab.drop, "rug70_1": lab.rug70_1, "n_prints": lab.n_prints,
            "n_drain": lab.n_drain, "n_noop": lab.n_noop, "n_foreign_in_window": lab.n_foreign_in_window, "v_source": lab.v_source}


def _label191(fills: Sequence[Any], idx: int, landing_ms: int) -> dict[str, Any] | None:
    from tools.exp012_rug_risk import rug_label

    return rug_label(fills, idx, landing_ms)


# --- sources ------------------------------------------------------------------------------------------------------------------


@dataclass
class SourceData:
    """One source's rows, in READ order. `rows_by_mint` holds trade rows (full rows for migrated mints). `migrations` is mint -> migration row."""

    tag: str
    block: str
    creates: dict[str, Mapping[str, Any]]
    migrations: dict[str, Mapping[str, Any]]
    rows_by_mint: dict[str, list[Mapping[str, Any]]]
    through_ms: int
    pool_print_ms: dict[str, list[int]] = field(default_factory=dict)  # canonical pool -> print times (ints only; for the silent-pool count)
    excluded_other: dict[str, list[str]] = field(default_factory=dict)  # P1B: pre_tape_create / pre_tape_migration / gap_over (plan 13 item 9)
    creator_hist: dict[str, list[int]] | None = None  # built from ALL creates before any exclusion (as EXP-015's build_creator_history_b)
    canonical_no_create: int = 0  # P1B: COUNT of mints with canonical-pool prints but no create row (pre-period tokens; no rows kept, outside every limit)
    excluded_no_bonding: list[str] = field(default_factory=list)  # creates with a placeholder slot and no pump_bonding print on the tape (plan 13 item 9)
    create_stats: dict[str, Any] | None = None  # outcome-blind counts from `apply_create_slot_rule`
    derived_pools: bool = False  # P1B only: no migrations/ dir, so `migrations` is derived from the tape with pool = canonical_pool(mint) (plan 13 item 6)


def creator_history(creates: Mapping[str, Mapping[str, Any]]) -> dict[str, list[int]]:
    hist: dict[str, list[int]] = {}
    for r in creates.values():
        c, b = r.get("creator"), r.get("block_time")
        if isinstance(c, str) and c and isinstance(b, int):
            hist.setdefault(c, []).append(b * 1000)
    for v in hist.values():
        v.sort()
    return hist


def progress(msg: str) -> None:
    """Counts-only progress line on stderr (rows read, mints kept, RSS MB): where memory goes if a run dies. No price, label or outcome."""
    try:
        rss = xx._rss_mb()
    except Exception:  # noqa: BLE001
        rss = -1
    print(f"[exp016] {msg} rss_mb={rss}", file=sys.stderr, flush=True)


def _real_slot(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


P1B_MAX_GAP_S = 600  # a create whose first bonding print is more than this many seconds after its create time is excluded (plan 13 item 9 (c))


def apply_create_slot_rule(creates: Mapping[str, Mapping[str, Any]], rows_by_mint: Mapping[str, Sequence[Mapping[str, Any]]], *,
                           migrations: Mapping[str, Mapping[str, Any]] | None = None, tape_start_s: int | None = None,
                           max_gap_s: int | None = None) -> tuple[dict[str, Mapping[str, Any]], list[str], dict[str, Any]]:
    """Manager rulings (pre-declared, plan 13 item 9). A create row whose slot is a placeholder (not a positive int; every P1B create carries slot 0)
    gets slot := the slot of the mint's FIRST `pump_bonding` print on the tape (outcome-blind; bonding prints precede migration). A create with a
    real slot is kept as is. A placeholder create is EXCLUDED (features, training, books) and counted, in this order, when:
      - `tape_start_s` is given and its `block_time` is before it (pre-tape create: its launch window is off the tape);
      - it has a migration (`migrations`, a pool and a slot) and no bonding print at or before that slot (pre-tape migration; NOT in the no-bonding numerator);
      - it has no bonding print at all (no-bonding);
      - `max_gap_s` is given and first bonding block_time - create block_time exceeds it (gap-over).
    Returns (creates, excluded no-bonding ids, counts); the other exclusions are lists under counts["excluded"]. `gap_s` = the remaining
    first-bonding minus create gap, seconds (p50/p90/max)."""
    out: dict[str, Mapping[str, Any]] = {}
    excl: dict[str, list[str]] = {"no_bonding": [], "pre_tape_create": [], "pre_tape_migration": [], "gap_over": []}
    gaps: list[int] = []
    n_placeholder = 0
    mig = migrations or {}
    for m, cr in creates.items():
        if _real_slot(cr.get("slot")):
            out[m] = cr
            continue
        n_placeholder += 1
        cbt = cr.get("block_time")
        if tape_start_s is not None and isinstance(cbt, int) and cbt < tape_start_s:
            excl["pre_tape_create"].append(m)
            continue
        bs = [(r["slot"], r.get("block_time")) for r in rows_by_mint.get(m, ()) if r.get("venue") == "pump_bonding" and _real_slot(r.get("slot"))]
        mr = mig.get(m)
        if mr and mr.get("pool") and _real_slot(mr.get("slot")) and not any(sl <= mr["slot"] for sl, _bt in bs):
            excl["pre_tape_migration"].append(m)
            continue
        if not bs:
            excl["no_bonding"].append(m)
            continue
        slot0, bt = min(bs, key=lambda t: (t[0], t[1] if isinstance(t[1], int) else 1 << 62))
        gap = bt - cbt if isinstance(bt, int) and isinstance(cbt, int) else None
        if max_gap_s is not None and gap is not None and gap > max_gap_s:
            excl["gap_over"].append(m)
            continue
        out[m] = {**cr, "slot": int(slot0)}
        if gap is not None:
            gaps.append(gap)
    n_ex = sum(len(v) for v in excl.values())
    n_den = sum(1 for m, cr in creates.items() if not _real_slot(cr.get("slot")) and (tape_start_s is None or not isinstance(cr.get("block_time"), int) or cr["block_time"] >= tape_start_s)
                and mig.get(m) and mig[m].get("pool"))
    stats = {"n_post_start_with_migration": n_den, "n_creates_loaded": len(creates), "n_placeholder_slot": n_placeholder, "n_with_bonding": n_placeholder - n_ex, "n_excluded_no_bonding": len(excl["no_bonding"]),
             "excluded": {k: sorted(v) for k, v in excl.items()},
             "gap_s": ({"n": len(gaps), "p50": _pctile(gaps, 0.5), "p90": _pctile(gaps, 0.9), "max": max(gaps)} if gaps else {"n": 0, "p50": None, "p90": None, "max": None})}
    return out, sorted(excl["no_bonding"]), stats


def canonical_pool_str(mint: str) -> str:
    """`tools.pumpswap_tx.canonical_pool` on a mint string: deterministic from the mint alone (outcome-blind); the pool the live probe executor trades."""
    from solders.pubkey import Pubkey

    from tools.pumpswap_tx import canonical_pool

    return str(canonical_pool(Pubkey.from_string(mint)))


def derive_p1b_migrations(prints: Iterable[tuple[str, Any, Any]], canonical_fn: Callable[[str], str] = canonical_pool_str,
                          only: Iterable[str] | None = None) -> dict[str, Mapping[str, Any]]:
    """P1B (the Oracle live tape has no migrations/ dir; manager decision 2026-10-06, plan 13 item 6). `prints` = (mint, pool, slot) per PumpSwap
    print. Every mint with a PumpSwap print gets a migration row whose pool is `canonical_pool(mint)` (never the pool a row happens to name) and whose
    slot is the first print on THAT pool. A mint with no print on its canonical pool (or an un-derivable pool) gets pool "" and counts as no-pool, in
    neither book. Outcome-blind: only pool ids and slots are read."""
    seen: dict[str, Any] = {}
    first: dict[str, int] = {}
    keep_only = None if only is None else set(only)  # the Oracle tap carries every PumpSwap pool: only pump.fun-created mints can be migrations
    for m, pool, slot in prints:
        if not isinstance(m, str) or (keep_only is not None and m not in keep_only):
            continue
        if m not in seen:
            try:
                seen[m] = canonical_fn(m)
            except Exception:  # noqa: BLE001 - an un-derivable pool is a no-pool mint
                seen[m] = ""
        if seen[m] and pool == seen[m] and isinstance(slot, int):
            first[m] = min(first.get(m, slot), slot)
    return {m: {"type": "migration", "mint": m, "slot": first[m], "pool": seen[m]} if m in first else {"type": "migration", "mint": m, "slot": 0, "pool": ""} for m in seen}


def count_pool_vs_canonical(migrations: Mapping[str, Mapping[str, Any]], canonical_fn: Callable[[str], str] = canonical_pool_str) -> dict[str, int]:
    """Report-only, before `started`, counts only: migration-row pools equal to canonical_pool(mint) vs not (rows with a pool). `underivable` = the
    canonical pool could not be computed from the mint string."""
    eq = ne = bad = 0
    for m, r in migrations.items():
        p = r.get("pool")
        if not p:
            continue
        try:
            c = canonical_fn(m)
        except Exception:  # noqa: BLE001
            bad += 1
            continue
        if c == p:
            eq += 1
        else:
            ne += 1
    return {"equal": eq, "not_equal": ne, "underivable": bad}


def _pctile(xs: Sequence[int], q: float) -> int:
    ys = sorted(xs)
    return ys[min(len(ys) - 1, max(0, int(-(-q * len(ys) // 1)) - 1))]


def p1b_gap_slots(src: SourceData) -> dict[str, Any]:
    """P1B disclosure (plan 13 item 6): slots between a mint's last bonding-curve print (at or before its first canonical-pool print) and that first
    canonical-pool print. Slots only; no price, no outcome. `n_no_bonding` = derived-migrated mints with no bonding print on the tape."""
    gaps: list[int] = []
    n_none = 0
    for m, mr in src.migrations.items():
        if not mr.get("pool") or not isinstance(mr.get("slot"), int):
            continue
        bs = [r["slot"] for r in src.rows_by_mint.get(m, ()) if r.get("venue") == "pump_bonding" and isinstance(r.get("slot"), int) and r["slot"] <= mr["slot"]]
        if bs:
            gaps.append(int(mr["slot"]) - max(bs))
        else:
            n_none += 1
    if not gaps:
        return {"n": 0, "n_no_bonding": n_none, "p50": None, "p90": None, "max": None}
    return {"n": len(gaps), "n_no_bonding": n_none, "p50": _pctile(gaps, 0.5), "p90": _pctile(gaps, 0.9), "max": max(gaps)}


def in_counted_window(block: str, migration_row: Mapping[str, Any]) -> bool:
    """The limit denominators' window is exactly `e15.in_block_window` (the counted window, not the read set with its buffer). A migration row with no
    time (P1B's derived rows: the tape read IS the counted hours) counts as inside."""
    bt = migration_row.get("block_time")
    return True if not isinstance(bt, int) else e15.in_block_window(block, bt * 1000)


def process_source(src: SourceData, vmap: Mapping[str, int | None], canonical_fn: Callable[[str], str] | None = None) -> dict[str, Any]:
    """The tape pass for one source: P2 gate, block history, one cell per migrated mint. Outcome-blind counters are returned beside the cells.
    `canonical_fn` (production: `canonical_pool_str`) adds the report-only pool-vs-canonical count for sources with migration rows."""
    pool_by_mint = rug.migration_pool_map(src.migrations.values())
    no_pool = rug.count_no_pool_mints(src.migrations.values())
    fed = {m: list(rug.restrict_rows_to_migration_pool(admit_rows(rs), pool_by_mint.get(m))) for m, rs in src.rows_by_mint.items() if m in src.migrations}
    raw = {m: admit_rows(rs) for m, rs in src.rows_by_mint.items() if m in src.migrations}
    gate = rug.pool_attribution_gate(raw, fed, pool_by_mint)  # raises PoolAttributionRefusal before any try
    records = [
        rug.build_mint_record(m, cr, admit_rows(src.rows_by_mint.get(m, ())), pool=pool_by_mint.get(m), vmap=vmap)
        for m, cr in src.creates.items()
    ]
    history = rug.BlockHistory(records)
    for m in [m for m in src.rows_by_mint if m not in src.migrations]:
        del src.rows_by_mint[m]  # the records hold what the block pass needs; only migrated mints' rows are needed from here
    progress(f"{src.tag}: block history built records={len(records)} migrated_mints_with_rows={len(src.rows_by_mint)}")
    hist = src.creator_hist if src.creator_hist is not None else creator_history(src.creates)  # ALL creates, before any exclusion
    cells: list[dict[str, Any]] = []
    no_create_row: list[str] = []
    no_migration_slot: list[str] = []
    no_bonding = sorted(m for m in src.excluded_no_bonding if m in src.migrations)
    other = {k: sorted(m for m in v if m in src.migrations) for k, v in src.excluded_other.items()}
    excluded_set = set(src.excluded_no_bonding).union(*[set(v) for v in src.excluded_other.values()])
    for m in sorted(src.migrations):
        pool, cr = pool_by_mint.get(m), src.creates.get(m)
        if not pool:
            continue
        if cr is None:
            if m not in excluded_set:
                no_create_row.append(m)  # a migrated mint whose create row is not on this tape: counted, excluded
            continue  # (a placeholder-slot create with no bonding print is counted as `no_bonding_excluded` instead)
        mslot = src.migrations[m].get("slot")
        if not isinstance(mslot, int) or isinstance(mslot, bool) or mslot <= 0:
            no_migration_slot.append(m)  # never defaulted to 0 (that would open the d-group cutoff to everything): counted, excluded
            continue
        try:
            cell = simulate_mint(m, cr, src.rows_by_mint.get(m, ()), pool=pool, migration_slot=mslot, vmap=vmap,
                                 tape_through_ms=src.through_ms, creator_hist=hist, history=history)
        except (Refused, rug.PoolAttributionRefusal):
            raise
        except Exception as exc:  # noqa: BLE001 - the message may hold a net; name the mint and the type only
            raise SimulationError(f"{m}: unexpected {type(exc).__name__} in the simulation (message withheld: it may carry outcome numbers)") from None
        cell.update({"source": src.tag, "block": src.block})
        cells.append(cell)
    progress(f"{src.tag}: cells built cells={len(cells)}")
    win = {m for m, mr in src.migrations.items() if in_counted_window(src.block, mr)}
    pool_vs_canonical = count_pool_vs_canonical(src.migrations, canonical_fn) if (canonical_fn is not None and not src.derived_pools) else None
    return {"tag": src.tag, "cells": cells, "no_pool_mints": no_pool, "no_create_row": no_create_row, "no_migration_slot": no_migration_slot,
            "no_bonding_excluded": no_bonding, "pre_tape_create_excluded": other.get("pre_tape_create", []),
            "pre_tape_migration_excluded": other.get("pre_tape_migration", []), "gap_excluded": other.get("gap_over", []),
            "p1b_canonical_no_create": src.canonical_no_create,
            "n_migrations_window": len(win), "no_create_window": len([m for m in no_create_row if m in win]), "no_pool_window": len([m for m in no_pool if m in win]),
            "p1b_cap_denominator": (src.create_stats or {}).get("n_post_start_with_migration"),
            "n_creates": len(src.creates) + len(excluded_set), "create_stats": src.create_stats,
            "n_creates_with_migration": len([m for m in src.migrations if m in src.creates or m in excluded_set]),
            "p1b_gap_slots": p1b_gap_slots(src) if src.derived_pools else None, "pool_vs_canonical": pool_vs_canonical, "gate": {k: v for k, v in gate.items() if k != "pools"}, "n_migrations": len(src.migrations),
            "slot_inversions": sum(c.get("slot_inversions", 0) for c in cells)}


def load_source_data(tag: str, block: str, hours_fn: Callable[[str], Mapping[str, Any]], pool_hours: Sequence[str], migration_roots: Sequence[Path],
                     canonical_fn: Callable[[str], str] = canonical_pool_str, progress_every_hour: bool = False, p1b_creates: Callable[[Path], Iterable[Mapping[str, Any]]] | None = None) -> SourceData:
    """Production reader over the same hour resolvers EXP-015 uses. NOT exercised against a real layout by this PR's tests (no real data read);
    the first `--precount` run on the host shows whether it matches. Full rows for migrated mints, slimmed rows for the rest."""
    from tools.exp012_virtual_rescore import _zcat_lines

    migrations: dict[str, Mapping[str, Any]] = {}
    creates: dict[str, Mapping[str, Any]] = {}
    adapt = (lambda r: r)
    canonical_no_create = 0
    pool_hours = sorted(pool_hours)  # time order: a create (read per hour, before that hour's trades) must precede its mint's trades
    dropped_no_create: set[str] = set()
    progress(f"{tag}: load start, {len(pool_hours)} hours")
    if tag == "P1B":
        # The Oracle live tape has no migrations/ dir, and `_hour_info_b` has no `create` key: creates come from the adapter's day files (with its
        # hard cutoff), rows get `block_time` and `quote_is_wsol` from `adapt_trade_row`, and pass 1 derives the migrations (plan 13 items 6, 9).
        from tools.oracle_live_adapter import adapt_trade_row, iter_adapted_creates

        adapt = adapt_trade_row
        live_root = Path(migration_roots[0]) if migration_roots else None
        for r in (p1b_creates(live_root) if p1b_creates is not None else iter_adapted_creates(root=live_root)):
            if isinstance(r.get("mint"), str):
                prev = creates.get(r["mint"])
                if prev is None or (isinstance(r.get("block_time"), int) and r["block_time"] < prev.get("block_time", 1 << 62)):
                    creates[r["mint"]] = r
        everything = derive_p1b_migrations(
            ((r.get("mint"), r.get("pool"), r.get("slot")) for h in pool_hours for r in eem._iter_trades(hours_fn(h)["trade"]) if r.get("venue") == "pumpswap"),
            canonical_fn,
        )
        # a mint stays if it has a create (pool "" = no-pool) or canonical-pool prints (no create: counted); a PumpSwap token with neither is not ours
        # RULING: only mints with a create row. A mint with canonical prints but no create is a pre-period token: counted, no row kept, outside every limit.
        canonical_no_create = sum(1 for m, r in everything.items() if r["pool"] and m not in creates)
        migrations = {m: r for m, r in everything.items() if m in creates}
        del everything
        progress(f"{tag}: P1B creates={len(creates)} migrations_derived={len(migrations)} (pass 1 over the tape done)")
        migration_roots = []
    for root in migration_roots:
        for f in sorted((Path(root) / "migrations").glob("migrations-*.jsonl.zst")):
            for line in _zcat_lines(f, '"migration"'):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("type") == "migration" and isinstance(r.get("mint"), str):
                    migrations.setdefault(r["mint"], r)
    rows_by_mint: dict[str, list[Mapping[str, Any]]] = {}
    through = 0
    n_read = n_kept = late_creates = 0
    keep = ("venue", "side", "trader", "slot", "token_raw", "sol_lamports", "quote_reserve", "base_reserve", "signature", "event_index", "tx_index", "block_time", "t_recv_ms", "mint", "pool", "quote_is_wsol")
    for h in pool_hours:
        info = hours_fn(h)
        if info.get("create") is not None:
            for r in eem._iter_trades(info["create"]):
                if r.get("type") == "create" and isinstance(r.get("mint"), str):
                    prev = creates.get(r["mint"])
                    if prev is None and r["mint"] in dropped_no_create:
                        late_creates += 1  # its trades were already read (and dropped): the ordering assumption failed
                    if prev is None or (isinstance(r.get("block_time"), int) and r["block_time"] < prev.get("block_time", 1 << 62)):
                        creates[r["mint"]] = r
        if progress_every_hour:
            progress(f"{tag}: hour {h} rows_read={n_read} rows_kept={n_kept} mints_kept={len(rows_by_mint)} creates={len(creates)}")
        for r in eem._iter_trades(info["trade"]):
            m = r.get("mint")
            if not isinstance(m, str) or r.get("venue") not in ("pump_bonding", "pumpswap"):
                continue
            n_read += 1
            if m not in creates:
                if tag != "P1B":
                    dropped_no_create.add(m)
                continue  # memory, every source: rows only for mints with a create row (a migration row alone does not keep rows)
            r = adapt(r)
            t = r.get("t_recv_ms") if r.get("t_recv_ms") is not None else (r["block_time"] * 1000 if isinstance(r.get("block_time"), int) else None)
            if isinstance(t, int) and t > through:
                through = t
            if m not in migrations:
                if r.get("venue") != "pump_bonding":
                    continue  # a never-migrated mint has no pool: its PumpSwap rows feed nothing (the block pass prices only its curve)
                r = {k: r[k] for k in keep if k in r}
            n_kept += 1
            rows_by_mint.setdefault(m, []).append(r)
    if late_creates:
        raise Refused(f"{tag}: {late_creates} create row(s) appear after their mint's trades (hours out of order, or a create outside the read set); counts only, refusing")
    progress(f"{tag}: tape pass done rows_read={n_read} rows_kept={n_kept} mints_kept={len(rows_by_mint)} creates={len(creates)} migrations={len(migrations)}")
    hist = creator_history(creates)  # ALL creates, before any exclusion (EXP-015's build_creator_history_b does the same)
    kw: dict[str, Any] = {}
    if tag == "P1B":
        from tools.oracle_live_adapter import POOL_B_START

        kw = {"migrations": migrations, "max_gap_s": P1B_MAX_GAP_S,
              "tape_start_s": int(datetime.strptime(POOL_B_START, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())}
    creates, excluded, stats = apply_create_slot_rule(creates, rows_by_mint, **kw)
    return SourceData(tag, block, creates, migrations, rows_by_mint, through, excluded_no_bonding=excluded, create_stats=stats, derived_pools=(tag == "P1B"),
                      excluded_other={k: v for k, v in stats["excluded"].items() if k != "no_bonding"}, creator_hist=hist,
                      canonical_no_create=canonical_no_create)


def pool_print_times(src: SourceData, pool_by_mint: Mapping[str, str]) -> dict[str, list[int]]:
    """Canonical-pool print times (ms) per pool, from rows only (no outcome)."""
    out: dict[str, list[int]] = {}
    for m, p in pool_by_mint.items():
        for r in src.rows_by_mint.get(m, ()):
            if r.get("venue") == "pumpswap" and r.get("pool") == p:
                t = rug._t_ms(r)
                if t is not None:
                    out.setdefault(p, []).append(t)
    return out


# --- the frozen book, the table, the counters ---------------------------------------------------------------------------------


def frozen_flags(cells: Sequence[Mapping[str, Any]], oof: Mapping[str, float] | None, artifact_dir: Path | None = None,
                 scorer: Callable[[Sequence[Sequence[float]]], Sequence[float]] | None = None) -> list[bool]:
    """Frozen EXP-012 selection: P1 rows by the stored OOF score (as #191), P2-P4 rows by the frozen model (never refit); score >= 0.8030766588450794."""
    out = [False] * len(cells)
    idx_model = [i for i, c in enumerate(cells) if c.get("block") != "P1" and c.get("exp012_features") is not None]  # NO_SIM / censored cells have no features: never scored
    for i, c in enumerate(cells):
        if c.get("block") == "P1" and oof is not None:
            s = oof.get(c["mint"])
            out[i] = s is not None and float(s) >= e15.FROZEN_THRESHOLD
    if idx_model:
        sel = e15.frozen_selection([{"features": cells[i]["exp012_features"]} for i in idx_model], artifact_dir, scorer)
        for i, s in zip(idx_model, sel):
            out[i] = bool(s)
    return out


def in_book(c: Mapping[str, Any]) -> bool:
    """Counted cells: inside the block's counted window, simulated, not censored."""
    return c.get("status") in ("FILLED", "MISS") and e15.in_block_window(c["block"], int(c["mig_ms"]))


def pre_started_counts(results: Sequence[Mapping[str, Any]], sel_by_mint: Mapping[str, bool], vmap: Mapping[str, int | None], closed: Iterable[str],
                       raw_by_source: Mapping[str, SourceData] | None = None, detail: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Everything the plan counts BEFORE `started`: ids and counts only, no net, no label. `sel_by_mint` is the frozen selection (model scores only)."""
    cells = [c for r in results for c in r["cells"]]
    sel_pools = sorted({c["pool"] for c in cells if sel_by_mint.get(c["mint"]) and in_book(c) and c.get("pool")})
    all_pools = sorted({c["pool"] for c in cells if c.get("pool")})
    unp = rug.count_unpriced_pools(sel_pools, vmap, closed=closed)
    n_cens = [c["mint"] for c in cells if c.get("status") == "CENSORED" and e15.in_block_window(c["block"], int(c["mig_ms"]))]
    return {
        "no_pool_mints": {r["tag"]: len(r["no_pool_mints"]) for r in results},
        "no_pool_mint_ids": {r["tag"]: r["no_pool_mints"] for r in results},
        "migration_pool_vs_canonical": {r["tag"]: r.get("pool_vs_canonical") for r in results if r.get("pool_vs_canonical") is not None},
        "closed_pools_frozen_selected": unp["closed"], "parse_fail_pools_frozen_selected": unp["parse_fail"], "unmapped_pools_frozen_selected": unp["unmapped"],
        "censored_cells": len(n_cens), "censored_mints": sorted(n_cens)[:200],
        "foreign_first_mints": {r["tag"]: len(r["gate"]["foreign_first_mints"]) for r in results},
        "mints_with_foreign_pool_prints": {r["tag"]: r["gate"]["mints_with_foreign_pool_prints"] for r in results},
        "slot_inversions": {r["tag"]: r["slot_inversions"] for r in results},
        "n_canonical_pools": len(all_pools), "n_cells": len(cells),
        "no_sim": sum(1 for c in cells if c.get("status") == "NO_SIM"),
        "no_sim_by_reason": dict(sorted(collections.Counter(str(c.get("why")) for c in cells if c.get("status") == "NO_SIM").items())),
        "no_migration_slot_mints": {r["tag"]: r.get("no_migration_slot", []) for r in results},
        "p1b_create_stats": {r["tag"]: r["create_stats"] for r in results if r.get("create_stats") is not None and r["tag"] == "P1B"},
        "create_slot_placeholders": {r["tag"]: (r["create_stats"] or {}).get("n_placeholder_slot") for r in results if r.get("create_stats") is not None},
        "no_bonding_excluded": {r["tag"]: len(r.get("no_bonding_excluded", [])) for r in results},
        "p1_frozen_selected_foreign_first": {r["tag"]: _selected_foreign_first(r, sel_by_mint) for r in results if r["cells"] and r["cells"][0].get("block") == "P1"},
        "lp_active_proxy": lp_active_proxy(cells, sel_by_mint, detail),
        "migrated_mints_without_create_row": {r["tag"]: len(r.get("no_create_row", [])) for r in results},
        "p1b_gap_slots": {r["tag"]: r["p1b_gap_slots"] for r in results if r.get("p1b_gap_slots") is not None},
    }


LIMIT_NO_CREATE = 0.02
LIMIT_NO_POOL = 0.02
LIMIT_P1B_PRE_TAPE_MIG = 0.05  # pre-tape migrations / P1B creates at or after POOL_B_START that have a migration (the honest rate is about 0)
LIMIT_P1B_GAP_OVER = 0.05  # gap_over (> P1B_MAX_GAP_S) / the same denominator
LIMIT_MIN_IN_BOOK = 30  # in-book cells (FILLED or MISS inside the window) per source
LIMIT_NO_SIM_SHARE = 0.25  # NO_SIM cells / all cells per source
LIMIT_P1_NO_OOF = 0.02  # in-book P1 cells with no stored OOF score / in-book P1 cells, per P1 source
LP_V_CLUSTER = (17_584_505_200, 17_584_505_699)  # the canonical creation cluster of v_base (V0); plan 13 item 10(c)
LP_V_MARGIN = 1_000


def _selected_foreign_first(r: Mapping[str, Any], sel_by_mint: Mapping[str, bool]) -> int:
    """P1 frozen-selected mints whose first PumpSwap print was foreign (EXP-012's stored OOF score used the old migration clock; plan 13 item 11). Count only."""
    ff = set(r["gate"]["foreign_first_mints"])
    return sum(1 for c in r["cells"] if c.get("block") == "P1" and sel_by_mint.get(c["mint"]) and c["mint"] in ff)


def _counts_only(obj: Any) -> Any:
    """Lists (mint or pool ids) become their length, recursively: `--precount` prints counts, never ids."""
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        return {k: _counts_only(v) for k, v in obj.items()}
    return obj


def in_book_cells(cells: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [c for c in cells if c.get("status") in ("FILLED", "MISS") and "mig_ms" in c and in_book(c)]


def oof_counts(results: Sequence[Mapping[str, Any]], oof: Mapping[str, float] | None) -> dict[str, dict[str, int]]:
    """Per P1 source: in-book P1 cells with and without a stored OOF score (counts only; plan 13 item 12)."""
    out: dict[str, dict[str, int]] = {}
    for r in results:
        ib = [c for c in in_book_cells(r["cells"]) if c.get("block") == "P1"]
        if ib:
            have = sum(1 for c in ib if oof is not None and oof.get(c["mint"]) is not None)
            out[r["tag"]] = {"in_book": len(ib), "with_score": have, "without_score": len(ib) - have}
    return out


def check_limits(results: Sequence[Mapping[str, Any]], oof: Mapping[str, float] | None = None) -> list[str]:
    """Pre-declared refusal limits (plan 13 items 9 and 12), outcome-blind, counts only. Returns the reasons (empty = none). Shares: no-create-row and
    no-pool over the source's migrations inside its counted pool hours; for P1B, no-bonding over its creates that have a migration; in-book
    cells and the NO_SIM share per source; the no-OOF share of in-book P1 cells per P1 source (when `oof` is given)."""
    why: list[str] = []
    for r in results:
        tag, n_mig = r["tag"], int(r.get("n_migrations") or 0)
        if not r["cells"]:
            why.append(f"{tag}: 0 cells")
        n_ib = len(in_book_cells(r["cells"]))
        if n_ib < LIMIT_MIN_IN_BOOK:
            why.append(f"{tag}: {n_ib} in-book cells (< {LIMIT_MIN_IN_BOOK})")
        n_ns = sum(1 for c in r["cells"] if c.get("status") == "NO_SIM")
        if r["cells"] and n_ns / len(r["cells"]) > LIMIT_NO_SIM_SHARE:
            why.append(f"{tag}: NO_SIM {n_ns} of {len(r['cells'])} cells (> {LIMIT_NO_SIM_SHARE:.0%})")
        n_mig = int(r.get("n_migrations_window", n_mig))  # migrations inside the counted window only
        n_nc = int(r.get("no_create_window", len(r.get("no_create_row", []))))
        n_np = int(r.get("no_pool_window", len(r.get("no_pool_mints", []))))
        if n_mig and n_nc / n_mig > LIMIT_NO_CREATE:
            why.append(f"{tag}: no-create-row {n_nc} of {n_mig} migrated mints in the counted window (> {LIMIT_NO_CREATE:.0%})")
        if n_mig and n_np / n_mig > LIMIT_NO_POOL:
            why.append(f"{tag}: no-pool {n_np} of {n_mig} migrated mints in the counted window (> {LIMIT_NO_POOL:.0%})")
        if tag == "P1B":
            den = int(r.get("p1b_cap_denominator") or 0)
            for key, lim, name in (("pre_tape_migration_excluded", LIMIT_P1B_PRE_TAPE_MIG, "pre-tape migrations"), ("gap_excluded", LIMIT_P1B_GAP_OVER, f"gap over {P1B_MAX_GAP_S} s")):
                k = len(r.get(key, []))
                if den and k / den > lim:
                    why.append(f"P1B: {k} of {den} creates at or after the tape start with a migration are {name} (> {lim:.0%})")
    if oof is not None:
        for tag, d in oof_counts(results, oof).items():
            if d["without_score"] / d["in_book"] > LIMIT_P1_NO_OOF:
                why.append(f"{tag}: no stored OOF score on {d['without_score']} of {d['in_book']} in-book P1 cells (> {LIMIT_P1_NO_OOF:.0%})")
    return why


def enforce_limits(results: Sequence[Mapping[str, Any]], oof: Mapping[str, float] | None = None) -> None:
    why = check_limits(results, oof)
    if why:
        raise Refused("pre-declared limit(s) exceeded (plan 13 item 9): " + "; ".join(why))


def lp_active_proxy(cells: Sequence[Mapping[str, Any]], sel_by_mint: Mapping[str, bool], detail: Mapping[str, Mapping[str, Any]] | None) -> dict[str, Any]:
    """Outcome-blind count (plan 13 item 10(c)): frozen-selected mints whose migration pool's `v_base` (the map's detail file) lies more than
    1,000 lamports outside the canonical creation cluster, per source. Counts only."""
    if detail is None:
        return {"available": False}
    lo, hi = LP_V_CLUSTER
    out: dict[str, dict[str, int]] = {}
    for c in cells:
        if not c.get("pool") or not sel_by_mint.get(c["mint"]):
            continue
        d = out.setdefault(str(c.get("source")), {"selected": 0, "outside": 0, "no_v_base": 0})
        d["selected"] += 1
        vb = (detail.get(c["pool"]) or {}).get("v_base")
        if not isinstance(vb, int):
            d["no_v_base"] += 1
        elif vb < lo - LP_V_MARGIN or vb > hi + LP_V_MARGIN:
            d["outside"] += 1
    return {"available": True, "cluster": f"{LP_V_CLUSTER[0]}..{LP_V_CLUSTER[1]}", "margin_lamports": LP_V_MARGIN, "by_source": out}


def v_coverage_report(pools: Iterable[str], vmap: Mapping[str, int | None]) -> dict[str, Any]:
    """`v_coverage` without the refusal (for `--precount`): the record plus `would_refuse`."""
    try:
        return {**v_coverage(pools, vmap), "would_refuse": False}
    except Refused:
        ps = sorted(set(pools))
        n = sum(1 for q in ps if vmap.get(q) is not None)
        return {"n_pools": len(ps), "n_readable": n, "coverage": n / len(ps), "min": V_COVERAGE_MIN, "would_refuse": True}


def total_loss_flags(cells: Sequence[Mapping[str, Any]], bad_pools: set[str], silent_ids: set[str]) -> dict[str, bool]:
    """mint -> scored at total loss in the sensitivity: its pool is closed or parse-fail (section 4), or its cell is a silent-pool cell (section 2.2)."""
    return {c["mint"]: (c.get("pool") in bad_pools or c["mint"] in silent_ids) for c in cells}


def build_table(cells: Sequence[Mapping[str, Any]], sel: Sequence[bool]) -> list[dict[str, Any]]:
    """The screen's table: every FILLED cell (the logistic's training universe) plus every frozen-selected MISS, in the counted windows. A
    censored cell, a no-pool mint and a no-sim mint are in no book (plan 2.2)."""
    out: list[dict[str, Any]] = []
    for c, s in zip(cells, sel):
        if not in_book(c):
            continue
        filled = c["status"] == "FILLED"
        if not filled and not s:
            continue
        cell = c["cells"][PRIMARY_CELL]
        nets = e15.cell_nets(cell)
        row = {
            "mint": c["mint"], "date": c["date"], "block": c["block"], "source": c["source"], "mig_ms": int(c["mig_ms"]), "pool": c["pool"], "filled": filled, "sel": bool(s),
            "feat": [float(c["features"][n]) for n in FEATURES18] if filled or s else None, "flat": nets["flat"], "press": nets["press"],
            "rug": bool(c["label"]["rug"]) if filled else None, "rug70_1": bool(c["label"]["rug70_1"]) if filled else None,
            "event": c["label"]["event"] if filled else None, "exit_kind": c.get("exit_kind"), "label191": (c.get("label191") or {}).get("rug") if filled else None,
            "alt": {f"{k}_{lag}": e15.cell_nets(cc) for (k, lag), cc in c["cells"].items() if (k, lag) != PRIMARY_CELL and not cc.get("censored")},
            "alt_filled": {f"{k}_{lag}": bool(cc.get("filled")) for (k, lag), cc in c["cells"].items() if (k, lag) != PRIMARY_CELL and not cc.get("censored")},
            "deadline_ms": c.get("deadline_ms"),
        }
        out.append(row)
    return out


def universe_sha256(table: Sequence[Mapping[str, Any]]) -> str:
    """Row universe: (mint, date, status, frozen-selected), sorted. No net, no label."""
    lines = [json.dumps([r["mint"], r["date"], "FILLED" if r["filled"] else "MISS", bool(r["sel"])]) for r in sorted(table, key=lambda r: (r["date"], r["mint"]))]
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def feature_table_sha256(table: Sequence[Mapping[str, Any]]) -> str:
    lines = [json.dumps([r["mint"], r["feat"]]) for r in sorted(table, key=lambda r: (r["date"], r["mint"]))]
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


# --- guards G1, G2 and the silent-pool count (AFTER `started`) ----------------------------------------------------------------


def silent_pool_ids(cells: Sequence[Mapping[str, Any]], pool_print_ms: Mapping[str, Sequence[int]]) -> list[str]:
    """Silent-pool cells among FILLED counted cells (plan 2.2): the cell's own pool has no print in the last 60 s before its deadline."""
    probe = [{"id": c["mint"], "pool": c["pool"], "deadline_ms": c["deadline_ms"]} for c in cells if in_book(c) and c["status"] == "FILLED" and c.get("deadline_ms") is not None]
    return [str(i) for i in rug.count_silent_pool_cells(probe, pool_print_ms)]


def label_guards(table: Sequence[Mapping[str, Any]], with_p4: bool) -> dict[str, Any]:
    """G1 and G2 (plan 2.4): RUG's rate among frozen-selected FILLED trades on all pool dates, and the RUG count on the non-P1 dates. They can end the family."""
    non_p1 = [r for r in table if r["sel"] and r["filled"] and r["block"] != "P1"]
    alls = [r for r in table if r["sel"] and r["filled"]]
    rate = (sum(1 for r in alls if r["rug"]) / len(alls)) if alls else None
    n_rug_non = sum(1 for r in non_p1 if r["rug"])
    g1 = rate is not None and rate > G1_MAX_RATE
    g2 = n_rug_non < G2_MIN_RUGS
    return {"rug_rate_all_dates": rate, "n_selected_filled_all": len(alls), "n_rug_selected_filled_non_p1": n_rug_non, "n_selected_filled_non_p1": len(non_p1),
            "g1_max_rate": G1_MAX_RATE, "g2_min_rugs": G2_MIN_RUGS, "g1_too_broad": bool(g1), "g2_too_rare": bool(g2), "stop": bool(g1 or g2),
            "outcome": OUTCOME_G1 if g1 else (OUTCOME_G2 if g2 else None)}


# --- candidates and nested leave-one-date-out ---------------------------------------------------------------------------------


def design(rows_feat: Sequence[Sequence[float]]) -> Any:
    import numpy as np

    x = np.asarray(rows_feat, dtype=np.float64).reshape(len(rows_feat), len(FEATURES18)).copy()
    for n in LOG1P_FEATURES:
        x[:, FEATURES18.index(n)] = np.log1p(np.maximum(x[:, FEATURES18.index(n)], 0.0))
    return x


def rule_veto(cid: str, feat: Sequence[float]) -> bool:
    c = CANDIDATES[cid]
    v = feat[FEATURES18.index(c["feature"])]
    return v >= (R4_MIN_DUMPS if cid == "r4" else R_THRESHOLD)


# TIES (manager ruling 2026-10-06, plan 13 item 11): the veto set is the rows whose probability is STRICTLY GREATER than the (1 - f) quantile
# threshold; rows tied at the threshold are not vetoed (deterministic, outcome-blind, fewer vetoes). All-tied probabilities give zero vetoes.
def quantile_threshold(values: Sequence[float], fraction: float) -> float | None:
    """The (1 - f) quantile, non-interpolating: index = round((1 - f) * (n - 1)) (plan 5.2)."""
    if not len(values):
        return None
    s = sorted(values)
    return s[int(round((1.0 - fraction) * (len(s) - 1)))]


def fit_logit(x: Any, y: Sequence[int]) -> Any | None:
    if len(y) < MIN_FIT_ROWS or len(set(int(v) for v in y)) < 2:
        return None
    return eem.fit_setting(x, list(y), LOGREG)


def purged(row: Mapping[str, Any], date: str) -> bool:
    """A row within 35 minutes before the start or after the end of `date` (EXP-015 section 3's purge)."""
    s = e15.date_start_ms(date)
    e = s + e15.DAY_MS
    return (s - e15.PURGE_MS <= row["mig_ms"] < s) or (e <= row["mig_ms"] < e + e15.PURGE_MS)


def gain(r: Mapping[str, Any], leg: str, tl: Mapping[str, bool] | None = None) -> float:
    """x_m for a vetoed row: the loss avoided, -net. A vetoed MISS scores 0 (it keeps its fee in both arms)."""
    if not r["filled"]:
        return 0.0
    return -(TL_NET if tl and tl.get(r["mint"]) else r[leg])


def veto_stats(rows: Sequence[Mapping[str, Any]], veto: Mapping[str, bool]) -> dict[str, Any]:
    sel = [r for r in rows if r["sel"]]
    vf = [r for r in sel if veto.get(r["mint"]) and r["filled"]]
    n_f = sum(1 for r in sel if r["filled"])
    return {"n_selected": len(sel), "n_selected_filled": n_f, "n_vetoed_filled": len(vf), "frac": (len(vf) / n_f) if n_f else 0.0,
            "press_sum": sum(gain(r, "press") for r in sel if veto.get(r["mint"])), "flat_sum": sum(gain(r, "flat") for r in sel if veto.get(r["mint"]))}


def eligible(st: Mapping[str, Any]) -> bool:
    return st["n_vetoed_filled"] >= MIN_VETOED_FILLED and st["frac"] <= MAX_VETO_FRACTION


def pick_candidate(stats: Mapping[str, Mapping[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Step 3: the best eligible candidate by the inner pooled PRESSURE paired mean, which must be > 0. No veto is always a candidate and wins ties
    (so a candidate must beat it strictly); among candidates, the lowest index wins a tie."""
    best, best_v = NONE_ID, 0.0
    table: dict[str, Any] = {}
    for cid in CANDIDATES:
        st = stats[cid]
        n = st["n_selected"]
        mean = (st["press_sum"] / n) if n else 0.0
        ok = eligible(st)
        table[cid] = {"eligible": ok, "inner_press_mean_lamports": mean, "n_vetoed_filled": st["n_vetoed_filled"], "frac": st["frac"]}
        if ok and mean > best_v:
            best, best_v = cid, mean
    return best, table


def outer_fold(table: Sequence[Mapping[str, Any]], d: str) -> dict[str, Any]:
    """One outer date d: only the other dates (and the 35-minute purge around d) are used. Returns the chosen candidate, its veto on d's
    frozen-selected rows, and each candidate's veto on d (report-only per-candidate table)."""
    import numpy as np

    inner = [r for r in table if r["date"] != d and not purged(r, d)]
    dates = sorted({r["date"] for r in inner})
    probs: dict[str, float] = {}
    for e in dates:  # inner LODO: one fit per inner date
        tr = [r for r in inner if r["date"] != e and r["filled"] and not purged(r, e)]
        te = [r for r in inner if r["date"] == e and r["sel"]]
        fit = fit_logit(design([r["feat"] for r in tr]), [int(bool(r["rug"])) for r in tr]) if tr and te else None
        if fit is None:
            continue
        for r, p in zip(te, eem.predict_setting(fit, design([r["feat"] for r in te]))):
            probs[r["mint"]] = p
    thr: dict[str, float | None] = {}
    for cid, c in CANDIDATES.items():
        if c["kind"] == "logit":
            thr[cid] = quantile_threshold([probs[r["mint"]] for r in inner if r["sel"] and r["filled"] and r["mint"] in probs], c["fraction"])
    inner_veto: dict[str, dict[str, bool]] = {}
    for cid, c in CANDIDATES.items():
        if c["kind"] == "rule":
            inner_veto[cid] = {r["mint"]: rule_veto(cid, r["feat"]) for r in inner if r["sel"]}
        else:
            inner_veto[cid] = {r["mint"]: (thr[cid] is not None and r["mint"] in probs and probs[r["mint"]] > thr[cid]) for r in inner if r["sel"]}
    stats = {cid: veto_stats(inner, v) for cid, v in inner_veto.items()}
    chosen, inner_table = pick_candidate(stats)
    test = [r for r in table if r["date"] == d and r["sel"]]
    train = [r for r in inner if r["filled"]]
    fit_all = fit_logit(design([r["feat"] for r in train]), [int(bool(r["rug"])) for r in train]) if train else None
    p_test: dict[str, float] = {}
    if fit_all is not None and test:
        for r, p in zip(test, eem.predict_setting(fit_all, design([r["feat"] for r in test]))):
            p_test[r["mint"]] = p
    per_cand: dict[str, dict[str, bool]] = {}
    for cid, c in CANDIDATES.items():
        if c["kind"] == "rule":
            per_cand[cid] = {r["mint"]: rule_veto(cid, r["feat"]) for r in test}
        else:
            per_cand[cid] = {r["mint"]: (thr[cid] is not None and r["mint"] in p_test and p_test[r["mint"]] > thr[cid]) for r in test}
    veto = {r["mint"]: False for r in test} if chosen == NONE_ID else dict(per_cand[chosen])
    return {"date": d, "chosen": chosen, "veto": veto, "per_candidate": per_cand, "inner": inner_table, "thresholds": thr, "n_inner_rows": len(inner)}


def nested_lodo(table: Sequence[Mapping[str, Any]], dates: Sequence[str] | None = None) -> dict[str, Any]:
    ds = sorted({r["date"] for r in table if r["sel"]}) if dates is None else list(dates)
    folds = [outer_fold(table, d) for d in ds]
    veto: dict[str, bool] = {}
    per_cand: dict[str, dict[str, bool]] = {cid: {} for cid in CANDIDATES}
    for f in folds:
        veto.update(f["veto"])
        for cid in CANDIDATES:
            per_cand[cid].update(f["per_candidate"][cid])
    return {"folds": folds, "veto": veto, "per_candidate": per_cand, "chosen_by_date": {f["date"]: f["chosen"] for f in folds}}


# --- bars S1-S6 -----------------------------------------------------------------------------------------------------------------


def _scope(table: Sequence[Mapping[str, Any]], blocks: Sequence[str] | None = None) -> list[Mapping[str, Any]]:
    return [r for r in table if r["sel"] and (blocks is None or r["block"] in blocks)]


def x_trades(rows: Sequence[Mapping[str, Any]], veto: Mapping[str, bool], tl: Mapping[str, bool] | None = None) -> list[dict[str, Any]]:
    """One paired unit per frozen-selected migration (filled and MISS): x_m = -v_m * net_m (0 for a MISS), in the shape `leg_stats` takes."""
    return [{"mint": r["mint"], "day": r["date"], "filled": r["filled"], **{leg: (gain(r, leg, tl) if veto.get(r["mint"]) else 0.0) for leg in LEGS}} for r in rows]


def kept_trades(rows: Sequence[Mapping[str, Any]], veto: Mapping[str, bool], tl: Mapping[str, bool] | None = None) -> list[dict[str, Any]]:
    """The kept book: frozen-selected entries minus the vetoed FILLED ones; a vetoed MISS stays with its fee. With `tl`, a kept FILLED trade on a flagged pool/cell is scored at total loss."""
    out = []
    for r in rows:
        if veto.get(r["mint"]) and r["filled"]:
            continue
        bad = bool(tl and tl.get(r["mint"]) and r["filled"])
        out.append({"mint": r["mint"], "day": r["date"], "filled": r["filled"], **{leg: (TL_NET if bad else r[leg]) for leg in LEGS}})
    return out


def _lstats(trades: Sequence[Mapping[str, Any]], leg: str) -> dict[str, Any]:
    if not trades:
        return {"mean_sol": None, "ci90_sol": None, "ci90_date_sol": None, "ci_lo": None, "ci_lo_date": None, "n": 0}
    return e15.leg_stats(trades, leg)


def _mean(xs: Sequence[float]) -> float | None:
    return (sum(xs) / len(xs) / LAMPORTS) if xs else None


def _pos(v: float | None) -> bool:
    return v is not None and v > 0


def bars(table: Sequence[Mapping[str, Any]], veto: Mapping[str, bool], with_p4: bool, tl: Mapping[str, bool] | None = None) -> dict[str, Any]:
    """S1-S6 on the OUTER results, under both fail models (plan section 7). `veto` maps mint -> removed (frozen-selected mints only)."""
    n_non = len(e15.non_p1_dates(with_p4))
    blocks_all = e15.active_blocks(with_p4)
    non = _scope(table, ["P2", "P3", "P4"])
    allr = _scope(table)
    out: dict[str, Any] = {"n_non_p1_dates": n_non}
    # S1, S2, S4 per leg
    for leg in LEGS:
        xn = x_trades(non, veto)
        st = _lstats(xn, leg)
        xa = x_trades(allr, veto)
        mean_all = _lstats(xa, leg)["mean_sol"]
        s1 = _pos(st["mean_sol"]) and _pos(st["ci_lo"]) and _pos(st["ci_lo_date"]) and _pos(mean_all)
        by_date: dict[str, float] = {}
        for t in xn:
            by_date[t["day"]] = by_date.get(t["day"], 0.0) + t[leg]
        pos_dates = sum(1 for v in by_date.values() if v > 0)
        s2 = pos_dates * 2 > n_non
        vf = sorted((t[leg] for t in xn if t["filled"] and veto.get(t["mint"])), reverse=True)
        total = sum(t[leg] for t in xn)
        ex_top = total - sum(vf[:TOP_AVOIDED])
        ex_best = total - max(by_date.values(), default=0.0)
        s4 = ex_top > 0 and ex_best > 0
        out[leg] = {
            "S1": {"pass": bool(s1), "mean_x_sol_non_p1": st["mean_sol"], "ci90_book_stats": st["ci90_sol"], "ci90_date_cluster": st["ci90_date_sol"], "mean_x_sol_all": mean_all},
            "S2": {"pass": bool(s2), "dates_positive": pos_dates, "of_dates": n_non},
            "S4": {"pass": bool(s4), "total_sol": total / LAMPORTS, "ex_top3_avoided_sol": ex_top / LAMPORTS, "ex_best_date_sol": ex_best / LAMPORTS},
        }
    # S3 (leg-independent): lift on the non-P1 dates
    sel_filled = [r for r in non if r["filled"]]
    vetoed = [r for r in sel_filled if veto.get(r["mint"])]
    n_rug_all = sum(1 for r in sel_filled if r["rug"])
    n_rug_v = sum(1 for r in vetoed if r["rug"])
    base = (n_rug_all / len(sel_filled)) if sel_filled else None
    prec = (n_rug_v / len(vetoed)) if vetoed else None
    lift = (prec / base) if prec is not None and base else None
    s3 = lift is not None and lift >= LIFT_MIN
    tp_v = [r for r in vetoed if r.get("exit_kind") == "tp"]
    out["S3"] = {"pass": bool(s3), "lift": lift, "rug_base_rate": base, "rug_precision": prec, "rug_recall": (n_rug_v / n_rug_all) if n_rug_all else None,
                 "n_vetoed_filled": len(vetoed), "n_rug_vetoed": n_rug_v, "n_vetoed_tp_exits": len(tp_v),
                 "vetoed_tp_total_sol": {leg: sum(r[leg] for r in tp_v) / LAMPORTS for leg in LEGS},
                 "vetoed_mean_ci90": {leg: {k: v for k, v in _lstats([{"mint": r["mint"], "day": r["date"], "filled": True, leg: r[leg]} for r in vetoed], leg).items() if k in ("mean_sol", "ci90_sol", "ci90_date_sol", "n")} if vetoed else None for leg in LEGS}}
    # S5: veto size on every pinned date set
    sets = {"all": allr, "non_p1": non, **{b: _scope(table, [b]) for b in blocks_all}}
    s5 = {}
    for name, rs in sets.items():
        f = [r for r in rs if r["filled"]]
        nv = sum(1 for r in f if veto.get(r["mint"]))
        s5[name] = {"n_selected_filled": len(f), "n_vetoed_filled": nv, "frac": (nv / len(f)) if f else 0.0, "pass": (nv / len(f) <= MAX_VETO_FRACTION) if f else True}
    out["S5"] = {"pass": all(v["pass"] for v in s5.values()), "by_set": s5}
    # S6: the kept book on the non-P1 dates, and again with total-loss flags
    for leg in LEGS:
        kn = kept_trades(non, veto)
        kt = kept_trades(non, veto, tl or {})
        m, mt = _mean([t[leg] for t in kn]), _mean([t[leg] for t in kt])
        out[leg]["S6"] = {"pass": bool(_pos(m) and _pos(mt)), "kept_mean_sol": m, "kept_mean_total_loss_sol": mt, "n_kept": len(kn)}
    for leg in LEGS:
        out[leg]["all"] = all(out[leg][k]["pass"] for k in ("S1", "S2", "S4", "S6"))
    out["passes"] = bool(all(out[leg]["all"] for leg in LEGS) and out["S3"]["pass"] and out["S5"]["pass"])
    return out


def report_only(table: Sequence[Mapping[str, Any]], veto: Mapping[str, bool], nested: Mapping[str, Any], with_p4: bool, tl: Mapping[str, bool], gates: Mapping[str, Any]) -> dict[str, Any]:
    """Never gating, never selected on (plan section 7, report-only list)."""
    non = _scope(table, ["P2", "P3", "P4"])
    allr = _scope(table)
    n_non, n_all = len(e15.non_p1_dates(with_p4)), len(e15.pool_dates(with_p4))
    out: dict[str, Any] = {}
    kept_all, kept_non = kept_trades(allr, veto), kept_trades(non, veto)
    out["kept_book"] = {"all": e15.scope_report(kept_all, n_all), "non_p1": e15.scope_report(kept_non, n_non)}
    out["s1_mean_with_total_loss"] = {leg: _lstats(x_trades(non, veto, tl), leg)["mean_sol"] for leg in LEGS}
    per_cand = {}
    for cid, v in nested["per_candidate"].items():
        vf = [r for r in non if r["filled"] and v.get(r["mint"])]
        per_cand[cid] = {"n_vetoed_filled": len(vf), "rug_precision": (sum(1 for r in vf if r["rug"]) / len(vf)) if vf else None,
                         **{f"mean_x_sol_{leg}": _lstats(x_trades(non, v), leg)["mean_sol"] for leg in LEGS}}
    out["per_candidate"] = per_cand
    out["chosen_by_date"] = nested["chosen_by_date"]
    out["rug70_1"] = {"n": sum(1 for r in allr if r["filled"] and r["rug70_1"]), "of": sum(1 for r in allr if r["filled"])}
    out["label191"] = {"n": sum(1 for r in allr if r["filled"] and r["label191"]), "of": sum(1 for r in allr if r["filled"])}
    out["rug_by_event"] = {ev: sum(1 for r in allr if r["filled"] and r["event"] == ev) for ev in ("A", "B", "AB")}
    for name, key in (("k4", "4_2"), ("k8", "8_2"), ("lag0", "6_0")):
        xs = [(-r["alt"][key]["press"] if veto.get(r["mint"]) and r["alt_filled"].get(key) and r["alt"].get(key) else 0.0) for r in non]
        out[f"paired_mean_{name}_press_sol"] = _mean(xs)
    dates = sorted({r["date"] for r in allr})
    half = len(dates) // 2
    out["first_vs_second_half"] = {nm: {leg: _lstats(x_trades([r for r in allr if r["date"] in ds], veto), leg)["mean_sol"] for leg in LEGS}
                                   for nm, ds in (("first_half", set(dates[:half])), ("second_half", set(dates[half:])))}
    out["guards"] = gates
    return out


# --- report ---------------------------------------------------------------------------------------------------------------------


def decide(bars_res: Mapping[str, Any] | None, guard: Mapping[str, Any]) -> dict[str, Any]:
    if guard.get("stop"):
        return {"passes": False, "outcome": guard["outcome"], "family_closed": True}
    if bars_res is None:
        return {"passes": False, "outcome": OUTCOME_INCOMPLETE, "family_closed": False}
    return {"passes": bool(bars_res["passes"]), "outcome": OUTCOME_PASS if bars_res["passes"] else OUTCOME_FAIL, "family_closed": not bars_res["passes"]}


def first_line(with_p4: bool) -> str:
    n = len(e15.pool_dates(with_p4))
    if with_p4:
        return f"EXP-016 screen on the {n}-UTC-date pool (P1 + explore-0814 + fresh-0903 + the EXP-011 block, {len(e15.non_p1_dates(True))} non-P1 dates)."
    return f"EXP-016 screen on the SMALLER {n}-UTC-date pool: the EXP-011 block (P4) is NOT in it, {len(e15.non_p1_dates(False))} non-P1 dates. Every number scales accordingly."


def _f(v: Any, d: int = 5) -> str:
    return "n/a" if v is None else f"{v:.{d}f}"


def render_md(rep: Mapping[str, Any]) -> str:
    L = [rep["first_line"], "", rep["banner"], "", "## Matched outcome", "", f"**{rep['decision']['outcome']}**", ""]
    pc = rep.get("pre_started") or {}
    L += [f"- Pre-`started` counts (ids and counts only): {json.dumps({k: v for k, v in pc.items() if k not in ('no_pool_mint_ids', 'censored_mints')}, default=str)}",
          f"- Prior tries per pool at `started`: {rep.get('prior_tries')}; universe sha256 `{rep.get('universe_sha256')}`; feature table sha256 `{rep.get('feature_table_sha256')}`.",
          f"- Costs: V pricing, ONE merged map (sha256 pinned {VMAP_EXP016_SHA256}), k = {K}, {SIZE_SOL} SOL, fee {FEE} per side (a MISS pays it), lag {EXIT_LAG}, haircut {HAIRCUT_FACTOR:.7f}.", ""]
    g = rep.get("guards_after_started")
    if g:
        L += ["## Guards after `started` (before any candidate was scored)", "", f"- G1/G2: {json.dumps(g['label'], default=str)}", f"- Silent-pool cells: {g['silent_pool_cells']}", ""]
    b = rep.get("bars")
    if b:
        L += ["## Bars (outer results of the nested procedure)", "", "| bar | pass | detail |", "| --- | --- | --- |"]
        for leg in LEGS:
            for k in ("S1", "S2", "S4", "S6"):
                L.append(f"| {k} {leg} | {b[leg][k]['pass']} | {json.dumps(b[leg][k], default=str)} |")
        L.append(f"| S3 | {b['S3']['pass']} | {json.dumps(b['S3'], default=str)} |")
        L.append(f"| S5 | {b['S5']['pass']} | {json.dumps(b['S5']['by_set'], default=str)} |")
        L += ["", f"- Chosen by date: {rep['report_only']['chosen_by_date']}", f"- Per candidate (report-only): {json.dumps(rep['report_only']['per_candidate'], default=str)}", ""]
    L += ["## Disclosures", "", *[f"- {c}" for c in CAVEATS], ""]
    return "\n".join(L) + "\n"


CAVEATS = (
    "Exploration. A pass is not evidence for an edge; the numbers rest on a few dozen RUG events and are upward-biased by the choice among six candidates.",
    "The frozen EXP-012 book here is NOT byte-comparable with earlier EXP-012 runs: the simulator is fed migration-pool rows only (P2) and prices with the fixed-parser map (plan 4, 13 item 4).",
    "The inner pick uses the pressure leg only; the flat leg is tested only through the outer bars (plan 5.3).",
    "A UTC date with no vetoed filled trade counts as NOT positive (S2). The date-cluster CI resamples whole dates; the book_stats CI resamples tokens. Neither is exact; both must pass.",
    "Silent-pool cells and closed/parse-fail pools enter only the total-loss sensitivities (S6 gates on it; the S1 mean with total loss is report-only).",
    "Purge: rows within 35 minutes before the start or after the end of the held-out date are dropped from every inner and outer training set and from the inner pick.",
)


def write_report(out_dir: Path, rep: Mapping[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / (OUT_REPORT + ".tmp")
    tmp.write_text(json.dumps(rep, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, out_dir / OUT_REPORT)
    (out_dir / OUT_MD).write_text(render_md(rep), encoding="utf-8")


# --- tries ------------------------------------------------------------------------------------------------------------------


def started_blocks(with_p4: bool) -> list[dict[str, str]]:
    """P2, P3 (and P4) separately, never one span (a span would also cover hours between the blocks)."""
    return [{"start_hour": e15.BLOCKS[b][0], "end_hour_exclusive": e15.BLOCKS[b][1], "host": "mal-research-0", "ledger_owner": "EXP-016 row universe (bookkeeping, not a pool try)"}
            for b in ("P2", "P3") + (("P4",) if with_p4 else ())]


def _in_log(log: Path, key: str, group: str, status: str, result_path: Path) -> bool:
    if not Path(log).is_file():
        return False
    for line in Path(log).read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        c = rec.get("config") or {}
        if rec.get("tool") == TOOL and c.get("key") == key and c.get("pool_group") == group and c.get("status") == status and rec.get("result_path") == str(result_path):
            return True
    return False


def _append(log: Path, key: str, group: str, status: str, blocks: Sequence[Mapping[str, str]], out_dir: Path, extra: Mapping[str, Any]) -> bool:
    from tools import mal_result

    result_path = out_dir / OUT_REPORT
    if _in_log(log, key, group, status, result_path):
        return False
    mal_result.append_try(
        log, tool=TOOL,
        config={"key": key, "experiment": "EXP-016 screen", "status": status, "pool_group": group, "k": K, "size_sol": SIZE_SOL, "fee_lamports": FEE, "exit_lag": EXIT_LAG,
                "pricing": "V", "selection": "nested LODO, six candidates", **extra},
        data_blocks=list(blocks), result_path=result_path, role="exploration",
    )
    return True


def log_tries(out_dir: Path, log: Path, marker_name: str, status: str, with_p4: bool, extra: Mapping[str, Any]) -> int:
    """`started`: ONE line (key exp016_started, keys = the six candidate keys) on the universe-bookkeeping block, before any fit. Any other
    status: one line per (candidate, pool group), so each candidate counts as a try on every pool it touches (+6 per pool, plan 5.4).
    Idempotent per log (marker + a scan). A non-completed line is never added for a candidate already logged completed."""
    from tools.exp012_exit_sensitivity import _read_marker, _write_marker

    marker = out_dir / marker_name
    done = _read_marker(marker)
    n = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[str, str, Sequence[Mapping[str, str]], Mapping[str, Any]]] = []
    if status == "started":
        jobs.append((STARTED_KEY, "universe", started_blocks(with_p4), {"keys": [c["key"] for c in CANDIDATES.values()], **extra}))
    else:
        for cid, c in CANDIDATES.items():
            for g, blocks in e15.pool_group_blocks(with_p4).items():
                jobs.append((c["key"], g, blocks, {"config": c["name"], **extra}))
    for key, g, blocks, ex in jobs:
        mk = f"{key}:{g}:{status}"
        if mk in done:
            continue
        if status != "completed" and status != "started" and _in_log(log, key, g, "completed", out_dir / OUT_REPORT):
            continue
        if _append(log, key, g, status, blocks, out_dir, ex):
            n += 1
        done.add(mk)
        _write_marker(marker, done)
    return n


def log_all(out_dir: Path, tries_path: Path, canonical: Path, status: str, with_p4: bool, extra: Mapping[str, Any]) -> dict[str, Any]:
    out = {"logged": log_tries(out_dir, tries_path, MARKER, status, with_p4, extra), "log_path": str(tries_path), "status": status}
    if Path(canonical).resolve() != Path(tries_path).resolve():
        out["canonical_logged"] = log_tries(out_dir, canonical, "tries_logged_canonical.marker", status, with_p4, extra)
    return out


# --- the screen after `started` -----------------------------------------------------------------------------------------------


def run_screen(table: Sequence[Mapping[str, Any]], cells: Sequence[Mapping[str, Any]], pool_print_ms: Mapping[str, Sequence[int]], bad_pools: set[str], with_p4: bool,
               on_guards: Callable[[Mapping[str, Any]], None] | None = None) -> dict[str, Any]:
    """AFTER `started`. G1/G2 and the silent-pool count are computed and handed to `on_guards` BEFORE any candidate is scored. A guard stop ends
    the run (no candidate is scored). Otherwise nested LODO, bars and report-only items."""
    label = label_guards(table, with_p4)
    silent = silent_pool_ids(cells, pool_print_ms)
    guards = {"label": label, "silent_pool_cells": len(silent), "silent_pool_mints": silent[:200]}
    if on_guards:
        on_guards(guards)
    if label["stop"]:
        return {"guards": guards, "decision": decide(None, label), "bars": None}
    tl = total_loss_flags(cells, bad_pools, set(silent))
    nested = nested_lodo(table)
    b = bars(table, nested["veto"], with_p4, tl)
    return {"guards": guards, "nested": {"chosen_by_date": nested["chosen_by_date"]}, "bars": b, "decision": decide(b, label),
            "report_only": report_only(table, nested["veto"], nested, with_p4, tl, guards)}


# --- CLI -----------------------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--p1-fast-dir", type=Path, required=True)
    ap.add_argument("--p1-oracle-insample-dir", type=Path, required=True)
    ap.add_argument("--p1-oracle-live-dir", type=Path, required=True)
    ap.add_argument("--p2-view-dir", type=Path, action="append", default=None)
    ap.add_argument("--p3-root", type=Path, default=Path(e15.P3_BASE))
    ap.add_argument("--p4-view-dir", type=Path, action="append", default=None, help="omit to run the 30-date pool (the report says so in its first line)")
    ap.add_argument("--vmap", default=VMAP_EXP016_PATH, help="ONE merged-source V map for P1-P4, sha256 asserted against VMAP_EXP016_SHA256")
    ap.add_argument("--v-fallback-json", type=Path, default=None, help="pool -> V for closed/parse-fail pools (section 4 rule: from pumpswap_virtual_history), optional")
    ap.add_argument("--closed-pools-json", type=Path, default=None, help="list of pool ids known closed; every other None-V pool counts as parse-fail")
    ap.add_argument("--v-constancy-json", type=Path, default=None, help=f"required unless --emit-constancy-sample. P1 check input: >= {V_SAMPLE_SIZE} sampled P2 pools with v_implied and quote_reserve (built by a separate job; needs getTransaction)")
    ap.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--tries-log", default=None)
    ap.add_argument("--canonical-tries", type=Path, default=CANONICAL_TRIES)
    ap.add_argument("--max-workers", type=int, default=4)
    ap.add_argument("--guards-only", action="store_true")
    ap.add_argument("--precount", action="store_true", help="full tape pass on every source; prints ONLY outcome-blind counts (no lock, no tries line, no label, no P&L); also reports the pre-declared limits as would-refuse")
    ap.add_argument("--emit-constancy-sample", type=Path, default=None, metavar="OUT.json", help="write the seeded P2 constancy sample (pool ids only) and exit; no tries, no lock")
    return ap


def git_state() -> dict[str, Any]:
    return e15.git_state()


def build_sources(g: Mapping[str, Any]) -> list[tuple[str, str, Any, list[str], list[Path]]]:
    """(tag, block, hours_fn, pool_hours, migration_roots) per source, from the SAME resolvers EXP-015 uses."""
    res = e15.p1_hour_resolvers(g["g1"]["roots"])
    roots = g["g1"]["roots"]
    out: list[tuple[str, str, Any, list[str], list[Path]]] = []
    for tag, root in (("P1A", roots["fast"]), ("P1C", roots["insample"]), ("P1B", roots["live"])):
        hours, pool, _mig = res[tag]
        out.append((tag, "P1", hours, list(e15.prepass_hours(g)[tag]), [Path(root)]))
    r2 = g["g2"]
    out.append(("P2", "P2", bc.MultiViewHours(dict(r2["roots"])), list(r2["pool"]), [Path(p) for p in sorted(set(r2["roots"].values()))]))
    out.append(("P3", "P3", e15.make_p3_hours(g["g3"]["walkers"]), e15.prepass_hours(g)["P3"], [Path(w.clean_dir) for w in g["g3"]["walkers"]]))
    if g["g4"] is not None:
        r4 = g["g4"]
        out.append(("P4", "P4", bc.MultiViewHours(dict(r4["roots"])), list(r4["pool"]), [Path(p) for p in sorted(set(r4["roots"].values()))]))
    return out


def source_counts(r: Mapping[str, Any], vmap: Mapping[str, int | None]) -> dict[str, Any]:
    """The whitelisted per-source counts of `--precount`. Counts only: no label, no net, no exit, no price."""
    cells = r["cells"]
    return {
        "creates": r.get("n_creates"), "migrations": r.get("n_migrations"), "cells": len(cells),
        "no_create_row": len(r.get("no_create_row", [])), "no_pool": len(r.get("no_pool_mints", [])), "no_migration_slot": len(r.get("no_migration_slot", [])),
        "no_bonding_excluded": len(r.get("no_bonding_excluded", [])), "foreign_first": len(r["gate"]["foreign_first_mints"]),
        "pre_tape_create_excluded": len(r.get("pre_tape_create_excluded", [])), "pre_tape_migration_excluded": len(r.get("pre_tape_migration_excluded", [])),
        "gap_excluded": len(r.get("gap_excluded", [])), "p1b_canonical_no_create": r.get("p1b_canonical_no_create", 0),
        "n_migrations_window": r.get("n_migrations_window"), "p1b_cap_denominator": r.get("p1b_cap_denominator"), "in_book": len(in_book_cells(cells)),
        "create_stats": r.get("create_stats"), "p1b_gap_slots": r.get("p1b_gap_slots"),
        "censored": sum(1 for c in cells if c.get("status") == "CENSORED"),  # the deadline rule (plan 13 item 7): a status, not an outcome
        "no_sim_by_reason": dict(sorted(collections.Counter(str(c.get("why")) for c in cells if c.get("status") == "NO_SIM").items())),
        "v_coverage": v_coverage_report([c["pool"] for c in cells if c.get("pool")], vmap), "pool_vs_canonical": r.get("pool_vs_canonical"),
    }


def precount(args: argparse.Namespace) -> int:
    """`--precount`: the same guards and tape pass as the real run on every source as given, then ONLY the counts. No lock, no tries line, no label
    and no P&L is written or printed; the counts JSON goes to stdout and `OUT_DIR/precount.json`. The pre-declared limits are reported as would-refuse."""
    try:
        refuse_extra_reserved(args)
        g = run_guards(args, pin_required=False)
        if args.vmap and re.fullmatch(r"[0-9a-f]{64}", VMAP_EXP016_SHA256):
            vmap_raw = load_pinned_vmap(args.vmap)
            vmap_note = "pinned"
        else:
            from tools.pumpswap_virtual import load_map

            vmap_raw = load_map(Path(args.vmap))  # the pin is PENDING: counts only, the map is read as given
            vmap_note = "UNPINNED (precount only)"
        try:
            from tools.exp012_forward_vmap import load_detail

            detail: Mapping[str, Mapping[str, Any]] | None = load_detail(Path(args.vmap))
        except Exception:  # noqa: BLE001
            detail = None
        fallback = {k: int(v) for k, v in json.loads(args.v_fallback_json.read_text()).items()} if args.v_fallback_json else {}
        closed = set(json.loads(args.closed_pools_json.read_text())) if args.closed_pools_json else set()
        vmap = rug.merge_v_map(vmap_raw, fallback)
        results = []
        for tag, block, hours_fn, pool_hours, mig_roots in build_sources(g):
            src = load_source_data(tag, block, hours_fn, pool_hours, mig_roots, progress_every_hour=True)
            results.append(process_source(src, vmap, canonical_pool_str))
            del src
            gc.collect()
            progress(f"{tag}: source done, rows freed, cells kept={len(results[-1]['cells'])}")
    except (Refused, rug.PoolAttributionRefusal, SimulationError) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"refusing: unexpected {type(exc).__name__} in the precount (message withheld)", file=sys.stderr)
        return 2
    cells = [c for r in results for c in r["cells"]]
    sel_by_mint: dict[str, bool] = {}
    oof: Mapping[str, float] | None = None
    try:
        oof = load_oof(args.artifact_dir)[0]
        sel_by_mint = {c["mint"]: s for c, s in zip(cells, frozen_flags(cells, oof, args.artifact_dir))}
    except (SystemExit, Exception):  # noqa: BLE001
        sel_by_mint = {}
    pre = pre_started_counts(results, sel_by_mint, vmap_raw, closed, detail=detail)
    pre["p1_oof_score_counts"] = oof_counts(results, oof)
    would = check_limits(results, oof)
    if oof is None:
        would.append("P1: stored OOF scores could not be loaded")
    # exactly what the real run checks before `started`: V coverage over ALL cells on the raw map, and the constancy file when given
    cov = v_coverage_report([c["pool"] for c in cells if c.get("pool")], vmap_raw)
    pre["v_coverage"] = cov
    if cov["would_refuse"]:
        would.append(f"V coverage {cov['coverage']:.3%} of {cov['n_pools']} canonical pools is not over {V_COVERAGE_MIN:.0%}")
    if args.v_constancy_json is not None:
        try:
            samples = json.loads(Path(args.v_constancy_json).read_text())
            check_v_constancy(samples, vmap_raw)
            check_constancy_sample(samples, readable_pools([c["pool"] for c in cells if c.get("block") == "P2" and c.get("pool")], vmap_raw))
        except Refused as exc:
            would.append(f"constancy: {exc}")
        except Exception as exc:  # noqa: BLE001
            would.append(f"constancy: unreadable file ({type(exc).__name__})")
    rec = {"mode": "precount", "vmap": vmap_note, "sources": {r["tag"]: source_counts(r, vmap) for r in results}, "pre_started": _counts_only(pre),
           "frozen_selection_available": bool(sel_by_mint), "would_refuse": would}
    text = json.dumps(rec, indent=2, default=str, sort_keys=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "precount.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    from tools.exp012_exit_sensitivity import resolve_tries_path

    args = _parser().parse_args(argv)
    if args.emit_constancy_sample:
        return emit_constancy_sample(args)
    if args.precount:
        return precount(args)
    if args.v_constancy_json is None:
        print("refusing: --v-constancy-json is required (or use --emit-constancy-sample)", file=sys.stderr)
        return 2
    tries_path = resolve_tries_path(args.tries_log)
    canonical = Path(args.canonical_tries).resolve()
    out_dir = args.out_dir
    try:
        g = run_guards(args)
        check_no_prior_tries(tries_path, canonical)
        try:
            e15.check_run_lock(out_dir)
        except e15.Refused as exc:
            raise Refused(str(exc)) from None
        gs = git_state()
        if gs["dirty_tools"]:
            raise Refused("tools/ is dirty (uncommitted change): the run records one clean head and refuses otherwise")
        vmap_raw = load_pinned_vmap(args.vmap)
        fallback = {k: int(v) for k, v in json.loads(args.v_fallback_json.read_text()).items()} if args.v_fallback_json else {}
        closed = set(json.loads(args.closed_pools_json.read_text())) if args.closed_pools_json else set()
        vmap = rug.merge_v_map(vmap_raw, fallback)  # ONE merged map, to the label and to make_wrapper
        constancy_samples = json.loads(args.v_constancy_json.read_text())
        constancy = check_v_constancy(constancy_samples, vmap_raw)
        input_shas = {k: (hashlib.sha256(Path(f).read_bytes()).hexdigest() if f else None)
                      for k, f in (("v_fallback_json_sha256", args.v_fallback_json), ("v_constancy_json_sha256", args.v_constancy_json), ("closed_pools_json_sha256", args.closed_pools_json))}
        print(f"input files: {json.dumps(input_shas)}", file=sys.stderr)
        if args.guards_only:
            print("guards OK (no outcome row was read)", file=sys.stderr)
            return 0
    except (Refused, rug.PoolAttributionRefusal) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - before any row: type only, no traceback
        print(f"refusing: unexpected {type(exc).__name__} in the guards (message withheld)", file=sys.stderr)
        return 2
    with_p4 = g["with_p4"]
    head, ahash = gs["head"], e15.args_hash(args)
    t0 = time.time()
    status, locked, started = "aborted_after_read", False, False

    def _on_sigterm(signum: int, frame: Any) -> None:
        raise SystemExit(128 + signum)

    prev = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        results, pool_print_ms = [], {}
        for tag, block, hours_fn, pool_hours, mig_roots in build_sources(g):
            src = load_source_data(tag, block, hours_fn, pool_hours, mig_roots, progress_every_hour=True)
            results.append(process_source(src, vmap, canonical_pool_str))
            for p_, ts_ in pool_print_times(src, rug.migration_pool_map(src.migrations.values())).items():
                pool_print_ms.setdefault(p_, []).extend(ts_)  # merge, never overwrite (a pool can appear in two sources)
            del src
            gc.collect()
            progress(f"{tag}: source done, rows freed, cells kept={len(results[-1]['cells'])}")
        cells = [c for r in results for c in r["cells"]]
        try:
            oof = load_oof(args.artifact_dir)[0]
        except SystemExit as exc:
            raise Refused(str(exc)) from None
        sel = frozen_flags(cells, oof, args.artifact_dir)
        try:
            from tools.exp012_forward_vmap import load_detail

            detail = load_detail(Path(args.vmap))
        except Exception:  # noqa: BLE001 - reported as unavailable
            detail = None
        pre = pre_started_counts(results, {c["mint"]: s for c, s in zip(cells, sel)}, vmap_raw, closed, detail=detail)
        pre["p1_oof_score_counts"] = oof_counts(results, oof)
        enforce_limits(results, oof)  # the pre-declared limits (plan 13 items 9 and 12), before `started`
        pre["v_coverage"] = v_coverage([c["pool"] for c in cells if c.get("pool")], vmap_raw)
        pre["v_constancy"] = constancy
        check_constancy_sample(constancy_samples, readable_pools([c["pool"] for c in cells if c.get("block") == "P2" and c.get("pool")], vmap_raw))  # before `started`
        table = build_table(cells, sel)
        usha, fsha = universe_sha256(table), feature_table_sha256(table)
        # Both count LOG LINES in the canonical data/tries.jsonl (not an alternate --tries-log), not tries: a line is one spent try or one status line.
        prior = prior_tries_per_pool(canonical, with_p4)
        prior_exp = prior_tries_per_experiment(canonical, with_p4)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / OUT_UNIVERSE).write_text(usha + "\n")
        (out_dir / OUT_FEATURES).write_text(fsha + "\n")
        try:
            e15.take_lock(out_dir, head, ahash)
        except FileExistsError:
            print(f"refusing: {out_dir / e15.OUT_LOCK} appeared (concurrent run)", file=sys.stderr)
            return 2
        locked = True
        check_no_prior_tries(tries_path, canonical)  # re-checked under the lock, right before the spend point
        extra = {"universe_sha256": usha, "feature_table_sha256": fsha, "prior_tries_per_pool": prior,
                 "prior_tries_per_experiment": prior_exp, "with_p4": with_p4, "vmap_sha256": g["vmap_sha256"], **input_shas}
        log_all(out_dir, tries_path, canonical, "started", with_p4, extra)  # the spend point
        started = True
        base = {"schema": SCHEMA, "banner": BANNER, "first_line": first_line(with_p4), "with_p4": with_p4, "pre_started": pre, "prior_tries": prior, "universe_sha256": usha,
                "feature_table_sha256": fsha, "git_head": head, "args_hash": ahash}
        holder: dict[str, Any] = {}

        def on_guards(gd: Mapping[str, Any]) -> None:  # G1/G2 and silent-pool counts are on disk before any candidate is scored
            holder["g"] = dict(gd)
            write_report(out_dir, {**base, "guards_after_started": gd, "decision": {"outcome": "guards computed; candidates not yet scored"}, "partial": True})

        bad = set(pre["closed_pools_frozen_selected"]) | set(pre["parse_fail_pools_frozen_selected"]) | set(pre["unmapped_pools_frozen_selected"])
        res = run_screen(table, cells, pool_print_ms, bad, with_p4, on_guards)
        rep = {**base, "guards_after_started": res["guards"], "bars": res["bars"], "decision": res["decision"], "report_only": res.get("report_only"), "wall_s": time.time() - t0, "partial": False}
        write_report(out_dir, rep)
        status = "guard_stop" if res["decision"].get("outcome") in (OUTCOME_G1, OUTCOME_G2) else "completed"
        print(render_md(rep))
        return 0
    except (Refused, rug.PoolAttributionRefusal, SimulationError) as exc:
        status = "refused_after_read" if started else "aborted_after_read"
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        if started:
            raise  # after `started` the traceback is allowed; the finally block logs the failure status as before
        print(f"refusing: unexpected {type(exc).__name__} before `started` (message withheld: it may carry outcome numbers)", file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGTERM, prev)
        if started and status != "completed":
            log_all(out_dir, tries_path, canonical, status, with_p4, {})
        elif started:
            log_all(out_dir, tries_path, canonical, "completed", with_p4, {})
        if locked:
            e15.write_record(out_dir, status, started, {c["key"]: status for c in CANDIDATES.values()})


if __name__ == "__main__":
    raise SystemExit(main())
