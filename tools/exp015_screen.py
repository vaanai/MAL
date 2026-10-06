#!/usr/bin/env python3
"""EXP-015 screen (Part 0, exploration): EXP-012's recipe re-fit on a pooled ~34-day set, judged under live-calibrated costs.

**EXPLORATION ONLY. NOT A PRE-REGISTRATION, NOT A PROMOTE, NOT GATE EVIDENCE.** Plan (follow it exactly):
EXP/EXP-015-pooled-retrain-plan-v2.md (pinned at 77b4582 plus seven post-pin edits). This module is the SCREEN of section 5
only. It does NOT read fresh-0808, fresh-0828, the forward walk or anything from 2026-10-02 onwards, and it does not
build the confirmation reader (Part 2).

What it does
  1. Guards (before any row is read): only the plan's blocks, by role; every reserved, holdout or forward root is
     refused; VIEW.sha256 / dedupe manifests verified; the frozen EXP-012 model md5 and threshold, and the pinned V map sha
     (pool_v_0814.json) asserted; the run refuses if any earlier exp015_* line is in a tries log.
  2. A pool-field-only V pre-pass per source (refuse if more than 1% of the migrating mints' PumpSwap prints lack V).
  3. Two tape passes per source: NV (EXP-012 section 3.1 pricing: slot+1, 0.5 SOL, no V, for the C1 label) and V
     (V pricing, k = 6 from the first PumpSwap print, 0.05 SOL, exit lag 2, plus the report-only k=4, k=8 and lag-0 cells).
  4. ONE fixed row universe (every k = 6 tp50_sl30 migration, filled AND MISS, in the plan's dates), its sha256 written
     BEFORE any fit and logged in the tries log as `started` lines.
  5. C1 / C2 / C3 (section 3), nested leave-one-UTC-date-out with the 35-minute purge in every outer and inner fold and a
     p90 nested out-of-fold threshold.
  6. Bars 1-6 of section 5 under both fail models at the deciding costs (V, k = 6, 0.05 SOL, fee 505,000 per side, exit lag 2,
     haircut 0.0042038 x proceeds on filled trades); the matched outcome is written into report.json / report.md.

Try-spend point (post-pin item 9): the `started` tries line is where tries are spent. Before it, tape rows are cached per (mode, source) under
scratch/cache with a sha256 manifest written when the source completes; a re-run with no `started` line reuses a cache only if its rows sha,
the clean repository head, the view shas, the V-map shas and the args all match (else that source's cache is discarded; old and new rows are
never mixed). Nothing derived from outcomes is printed or written outside scratch/ before `started` (row counts and hashes only); nobody inspects
scratch/ between a crash and a resume. `RUN.lock` (O_EXCL, head + args hash) is written with the `started` lines; a lock without a record refuses.
After `started` there is no resume.

MiScusi request: 48 GB memory, 8 CPUs. Tape passes use at most 4 workers (TAPE_WORKERS_CAP, until measured); the fits use up to --max-workers (8).

Run (mal-research-0; one heavy job at a time; as a MiScusi job; under nice):
  nice -n 19 /data/mal/venv/bin/python -m tools.exp015_screen \\
    --p1-fast-dir /data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00 \\
    --p1-oracle-insample-dir /data/mal/clean-view/oracle-insample-2026-09-22_25 \\
    --p1-oracle-live-dir /data/mal/clean-view/oracle-live-2026-09-25_27 \\
    --p2-view-dir /data/mal/clean-view/explore-0814/w1 ... --p2-view-dir /data/mal/clean-view/explore-0814/w7 \\
    --p3-root /data/mal/blocks-clean/fresh-0903 \\
    [--p4-view-dir <EXP-011 clean view> ...] \\
    (every --vmap-pN defaults to the pinned /data/mal/pumpswap-virtual/pool_v_0909.json) \\
    --out-dir /data/mal/exp015-screen --tries-log /data/mal/ops/tries-exp015-screen.jsonl --max-workers 8
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import multiprocessing as mp
import os
import random
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import tools.exp011_freeze as fz
import tools.exp011_score as e11
import tools.exp012_backcheck as bc
import tools.exp012_forward as ff12
import tools.exp012_operating_point as op
import tools.exp012_score as s12
import tools.exploration_entry_model as eem
from tools.exp012_latency_sensitivity import DEFAULT_ARTIFACT_DIR, _forbidden_hit
from tools.exp012_latency_virtual import DEFAULT_VMAP, set_env
from tools.latency_curve import FLAT_FAIL, MISS, mixed_net
from tools.paper_attention_promote import BookTrade, _pct, book_stats

TOOL = "tools.exp015_screen"
SCHEMA = "exp015_screen_v1"
REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_TRIES = REPO_ROOT / "data" / "tries.jsonl"
PLAN = REPO_ROOT / "EXP" / "EXP-015-pooled-retrain-plan-v2.md"
TARGET = fz.TARGET_SPEC_ID  # tpsl_tp50_sl30
LAMPORTS = 1_000_000_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000

BANNER = (
    "EXPLORATION SCREEN (EXP-015 plan v2 section 5). Not a pre-registration, not a promote, not gate evidence. The screen is a filter for "
    "'worth one confirmation read'; its numbers are upward-biased (recipe tuned on P1, configs chosen on the dates they are scored on, best-of-3 "
    "on top of 74 earlier tries on the 9-day pool). The nested leave-one-date-out trains on days both before and after each test day: it cannot "
    "see drift. Exit lag 2 is still optimistic against the measured live exit leak; entry noise and MEV are not added."
)

# --- pinned numbers (each is tied to the plan by tools/test_exp015_screen.py) ------------------------------------

FROZEN_THRESHOLD = op.FROZEN_THRESHOLD  # 0.8030766588450794
MODEL_MD5 = bc.MODEL_MD5  # a1810d219ed61db64a396f40dc302ce5
# ONE pool -> V map for every block (P1-P4): pool_v_0909.json (job #224: a superset of pool_v_0814, the EXP-011 block pools and the 30 pools
# the back-check #207 found without V). The pin replaces the P2 pin on pool_v_0814. The sha is filled by the manager, after #224, in a
# reviewed one-line commit; while it is the placeholder the tool refuses at startup (check_pin_ready).
VMAP_0909_SHA256 = "70914a1619e4cf6adbb1d1981cbd8a49483f559b230e7dcfc224335a0635b42e"  # job #232 final (226,073 pools, 321 null), pinned 2026-10-06
VMAP_0909_PATH = "/data/mal/pumpswap-virtual/pool_v_0909.json"
SCENARIO_WORDING = "one scenario (both sides' selected removed mints at total loss), not a worst-case bound"
BIAS_STATEMENT = ("Removal of unpriceable mints may bias the gate bars UPWARD (the removed mints could be rugs; a closed account is the likely class). "
                  "The direction for bar 3 is unknown. Report-only sensitivity below; the bars themselves are unchanged.")
UNPRICEABLE_MAX_FRACTION = 0.005  # refuse (before `started`) if more than 0.5% of the universe's mints sit on a pool with no V
V_MAX_MISSING_FRACTION = 0.01
K = 6
SIZE_SOL = 0.05
FEE = 505_000
EXIT_LAG = 2
ENTRY_GAP_BPS = bc.ENTRY_GAP_BPS  # 26.08
SELL_SHORTFALL_BPS = bc.SELL_SHORTFALL_BPS  # 16
HAIRCUT_FACTOR = 1.0 - (1.0 - ENTRY_GAP_BPS / 1e4) * (1.0 - SELL_SHORTFALL_BPS / 1e4)  # 0.0042038 x proceeds
PURGE_MIN = 35
PURGE_MS = PURGE_MIN * 60_000
THRESHOLD_PCT = 0.90
REPORT_PCTS = (0.80, 0.95)  # report-only
MIN_N = 100
MIN_DATES = 5
CONCENTRATION_MAX = 0.20
BOOT_DRAWS = 1000
BOOT_SEED = 1
SEED = 1
TRIES_CAP = 3
MAX_WORKERS_CAP = 8
TAPE_WORKERS_CAP = 4  # tape passes stay at 4 workers until measured; fits may use up to MAX_WORKERS_CAP
DEFAULT_WORKERS = 4
MDL_DEFAULT = 20
MDL_C3 = 75

# Blocks: counted window (UTC hours, inclusive start, exclusive end). P2's first 24 h are feature buffer only.
BLOCKS: dict[str, tuple[str, str]] = {
    "P1": ("2026-09-19T00", "2026-09-28T00"),
    "P2": ("2026-08-15T12", "2026-08-28T12"),
    "P3": ("2026-09-03T12", "2026-09-09T12"),
    "P4": ("2026-09-09T12", "2026-09-15T12"),
}
BLOCK_COUNTED_HOURS = {"P1": 216, "P2": 312, "P3": 144, "P4": 144}
P2_SEALED = ("2026-08-14T12", "2026-08-28T12")  # 336 h sealed, the first 24 h feature buffer only
P2_PREPASS_START = BLOCKS["P2"][0]
SOURCES = ("P1A", "P1C", "P1B", "P2", "P3", "P4")
SOURCE_BLOCK = {"P1A": "P1", "P1C": "P1", "P1B": "P1", "P2": "P2", "P3": "P3", "P4": "P4"}
FORWARD_FENCE = "2026-10-02T00"  # nothing at or after this hour is ever opened

BUFFER_HOURS = s12.BUFFER_HOURS  # 24
MAX_HOME_HOURS = s12.MAX_HOME_HOURS  # 12

# Reserved / holdout / forward fragments, refused anywhere in a given path (raw or resolved).
RESERVED_FRAGMENTS = (
    "fresh-0808",
    "fresh-0828",
    "forward-1002",
    "forward-1016",
    "forward-family",
    "exp012-forward",
    "truth-1001",
    "/var/lib/mal/backfill-fast-b",  # raw EXP-011 walkers; only a clean view with VIEW.sha256 is accepted
    "/var/lib/mal/backfill-fast-c",
    "/data/mal/blocks/",  # raw getBlock walkers (includes forward-1002, fresh-0808/0828 raw)
)
P2_BASE = "/data/mal/clean-view/explore-0814"
P3_BASE = "/data/mal/blocks-clean/fresh-0903"
P3_RANGES = s12.DEFAULT_RANGES
VMAP_DEFAULT = VMAP_0909_PATH

# Configs: the entire try budget (plan section 7).
CONFIGS: dict[str, dict[str, Any]] = {
    "c1": {"key": "exp015_c1", "name": "C1", "label": "c1", "min_data_in_leaf": MDL_DEFAULT, "desc": "EXP-012 section 3.1 label 1{pressure net > 0} at the EXP-012 operating point (slot+1, 0.5 SOL, 0.0005 SOL/side, no V, scale 1)"},
    "c2": {"key": "exp015_c2", "name": "C2", "label": "c2", "min_data_in_leaf": MDL_DEFAULT, "desc": "label = 1 iff E = (1-p_fail)*net' - p_fail*505000 > 0 at the deciding costs, p_fail pressure scale 1, MISS = 0"},
    "c3": {"key": "exp015_c3", "name": "C3", "label": "c2", "min_data_in_leaf": MDL_C3, "desc": "C2's label with min_data_in_leaf = 75"},
}
assert len(CONFIGS) == TRIES_CAP

# Report cells: (k, exit_lag). The first is the primary deciding cell; the rest are report-only.
PRIMARY_CELL = (K, EXIT_LAG)
CELL_KEYS = ((6, 2), (6, 0), (4, 2), (8, 2))
COMBOS = tuple((k, SIZE_SOL, lag) for k, lag in CELL_KEYS)
LEGS = ("flat", "press")

FAST_ONLY_LINE = "pool A cannot change; DEC-017 (a) fast-only was +0.0005 [−0.0137, +0.0137]"

OUT_REPORT = "report.json"
OUT_MD = "report.md"
OUT_UNIVERSE = "universe.jsonl"
OUT_UNIVERSE_SHA = "universe.sha256"
MARKER = "tries_logged.marker"
OUT_LOCK = "RUN.lock"
OUT_RECORD = "RUN.record.json"
CACHE_DIR = "cache"
CACHE_SCHEMA = "exp015_tape_cache_v1"
ENV_MODE = "MAL_EXP015_MODE"

OUTCOME_PASS = "SCREEN PASS: {name} goes to confirmation (largest pooled pressure mean on the non-P1 dates; no discretion). This means 'worth one confirmation read of fresh-0808', never 'has an edge'."
OUTCOME_NONE = (
    "SCREEN FAIL: no configuration passed bars 1-6. The family is closed: no fourth configuration, no recipe tweak, no new window or fraction. "
    "fresh-0808 goes back to 'reserved' with no assigned owner by a ledger edit, unread, and the PR says EXP-015 failed its screen."
)
OUTCOME_INCOMPLETE = "SCREEN NOT DECIDED: at least one configuration was refused or aborted after reading. No pass is claimed; the tries are spent (statuses are in the tries log)."


class Refused(Exception):
    code = 2


# --- dates, windows, folds -------------------------------------------------------------------------------------------


def hour_ms(key: str) -> int:
    return bc.hour_ms(key)


def utc_date(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))


def date_start_ms(date: str) -> int:
    return int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def block_hours(block: str) -> list[str]:
    return bc.hours_range(*BLOCKS[block])


def block_dates(block: str) -> list[str]:
    return sorted({h[:10] for h in block_hours(block)})


def active_blocks(with_p4: bool) -> list[str]:
    return ["P1", "P2", "P3", "P4"] if with_p4 else ["P1", "P2", "P3"]


def pool_dates(with_p4: bool) -> list[str]:
    """The UTC dates of migration time: 36 with P4, 30 without (plan section 3)."""
    out: set[str] = set()
    for b in active_blocks(with_p4):
        out |= set(block_dates(b))
    return sorted(out)


def non_p1_dates(with_p4: bool) -> list[str]:
    out: set[str] = set()
    for b in active_blocks(with_p4):
        if b != "P1":
            out |= set(block_dates(b))
    return sorted(out)


def p2_dates() -> list[str]:
    return block_dates("P2")


def in_block_window(block: str, mig_ms: int) -> bool:
    a, b = BLOCKS[block]
    return hour_ms(a) <= mig_ms < hour_ms(b)


def purge_mask(mig_ms: Any, start_ms: int, end_ms: int, purge_ms: int = PURGE_MS) -> Any:
    """Rows within 35 minutes before the START of a held-out date, or 35 minutes after its END, are dropped from that fold's
    training. Works on numpy arrays or lists of ints (returns the same kind)."""
    import numpy as np

    m = np.asarray(mig_ms)
    return ((m >= start_ms - purge_ms) & (m < start_ms)) | ((m >= end_ms) & (m < end_ms + purge_ms))


def pct_index(n: int, p: float = THRESHOLD_PCT) -> int:
    """Non-interpolating percentile index, as EXP-012 section 3.1: index = round(p * (n - 1))."""
    return int(round(p * (n - 1)))


def percentile(values: Sequence[float], p: float = THRESHOLD_PCT) -> float | None:
    if not len(values):
        return None
    return fz._percentile(sorted(values), p)


# --- guards (before any row is read) ---------------------------------------------------------------------------------


def refuse_reserved(path: Path | str, label: str = "path") -> str:
    """Refuse a raw or resolved path containing any reserved / holdout / forward fragment. Returns the realpath."""
    rp = os.path.realpath(str(path))
    for cand in (str(path), rp, rp + "/"):
        for frag in RESERVED_FRAGMENTS:
            if frag in cand:
                raise Refused(f"{label} {str(path)!r} (resolves to {rp!r}) is a reserved holdout / forward / raw location ({frag}); EXP-015 never reads it")
    bad = _forbidden_hit(rp)
    if bad and not (rp == P3_BASE or rp.startswith(P3_BASE + "/")):
        raise Refused(f"{label} {str(path)!r} resolves to {rp!r}, inside a reserved holdout location ({bad})")
    return rp


def assert_hours_allowed(hours: Sequence[str], with_p4: bool = True) -> None:
    """Every hour must lie in the plan's sealed windows (P1, P2 incl. its feature-buffer day, P3, and P4 if it is in the pool);
    anything from 2026-10-02T00 is refused by name."""
    ok: set[str] = set(bc.hours_range(*P2_SEALED)) | set(block_hours("P3")) | set(bc.hours_range("2026-09-18T23", "2026-09-28T00"))
    if with_p4:
        ok |= set(block_hours("P4"))
    for h in hours:
        if h >= FORWARD_FENCE:
            raise Refused(f"hour {h} is at or after {FORWARD_FENCE}: the forward walk and everything later is never opened")
        if h not in ok:
            raise Refused(f"hour {h} is outside the plan's pool windows")


def guard_p1(fast: Path | str, insample: Path | str, live: Path | str, verify: bool = True) -> dict[str, Any]:
    """P1: the three pinned EXP-012 clean views, by directory name and VIEW.sha256 pin."""
    want = {"fast": "fast-pool-2026-09-18T23_2026-09-22T00", "insample": "oracle-insample-2026-09-22_25", "live": "oracle-live-2026-09-25_27"}
    roots: dict[str, Path] = {}
    shas: dict[str, str] = {}
    for label, p in (("fast", fast), ("insample", insample), ("live", live)):
        rp = refuse_reserved(p, f"P1 {label} root")
        if Path(rp).name != want[label]:
            raise Refused(f"P1 {label} root {str(p)!r}: directory name {Path(rp).name!r} is not the pinned {want[label]!r}")
        if not (Path(rp) / "VIEW.sha256").is_file():
            raise Refused(f"P1 {label} root {rp}: VIEW.sha256 not found")
        roots[label] = Path(rp)
        if verify:
            try:
                fz.verify_view_sha256(Path(rp))
                fz.check_view_pin(Path(rp))
            except SystemExit as exc:
                raise Refused(str(exc)) from None
            shas[label] = hashlib.sha256((Path(rp) / "VIEW.sha256").read_bytes()).hexdigest()
    return {"roots": roots, "view_sha256": shas}


def guard_p2(view_dirs: Sequence[Path | str], verify: bool = True, enforce_base: bool = True) -> dict[str, Any]:
    for v in view_dirs:
        rp = refuse_reserved(v, "P2 view")
        if enforce_base and not (rp == P2_BASE or rp.startswith(P2_BASE + "/")):
            raise Refused(f"P2 view {str(v)!r} resolves to {rp!r}, not under {P2_BASE}")
    try:
        return bc.guard_views(list(view_dirs), P2_SEALED[0], P2_SEALED[1], verify=verify)
    except bc.Refused as exc:
        raise Refused(str(exc)) from None


def guard_p4(view_dirs: Sequence[Path | str], verify: bool = True) -> dict[str, Any] | None:
    """The EXP-011 block is accepted only through a clean view that has VIEW.sha256 and tiles [2026-09-09T12, 2026-09-15T12)
    exactly. No view given: returns None (the run proceeds on the 30-date pool and says so). A partial or unverified view refuses."""
    if not view_dirs:
        return None
    for v in view_dirs:
        refuse_reserved(v, "P4 view")
    try:
        return bc.guard_views(list(view_dirs), BLOCKS["P4"][0], BLOCKS["P4"][1], verify=verify)
    except bc.Refused as exc:
        raise Refused(f"P4 (EXP-011 block) view refused: {exc}") from None


def p3_walkers(root: Path | str) -> list[s12.Walker]:
    r = Path(root)
    return [s12.Walker(k, r / k, r / k, a, b) for k, (a, b) in sorted(P3_RANGES.items())]


def guard_p3(root: Path | str, verify: bool = True, enforce_base: bool = True) -> dict[str, Any]:
    """fresh-0903 (spent by EXP-012's one read, now exploration): the deduplicated copies and their sha256 manifests."""
    rp = refuse_reserved(root, "P3 root")
    if enforce_base and rp != P3_BASE:
        raise Refused(f"P3 root {str(root)!r} resolves to {rp!r}, not {P3_BASE}")
    walkers = p3_walkers(rp)
    errs = s12.check_tiling(walkers)
    pin, pin_errs = s12.expected_pin(walkers)
    errs += pin_errs
    if not errs and verify:
        errs += s12.check_dedupe_bytes(walkers)
    if errs:
        raise Refused("P3 (fresh-0903) refused: " + "; ".join(errs[:5]))
    return {"walkers": walkers, "pin_sha256": {k: v for k, v in pin.items() if k.endswith("manifest.json")}}


def check_pin_ready() -> None:
    """Refuses at startup while the map pin is the placeholder (so the tool cannot run before the real sha256 is filled in)."""
    if not re.fullmatch(r"[0-9a-f]{64}", VMAP_0909_SHA256):
        raise Refused(f"VMAP_0909_SHA256 is {VMAP_0909_SHA256!r}, not a sha256: the manager fills it in after job #224 in a reviewed one-line commit. Refusing to run")


def check_vmap(path: str | Path, pinned: str | None = None, label: str = "vmap") -> str:
    p = Path(path)
    if not p.is_file():
        raise Refused(f"{label} {str(path)!r} not found")
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    if pinned is not None and got != pinned:
        raise Refused(f"{label} {path}: sha256 {got} != the pinned {pinned}")
    return got


def check_frozen_model(artifact_dir: Path) -> dict[str, Any]:
    """The frozen EXP-012 model and threshold are used read-only (the paired bar's reference)."""
    try:
        _model, thr, names = e11.load_frozen_spec(artifact_dir)
        md5 = bc.check_model_md5(artifact_dir)
        bc.check_frozen_threshold(thr)
    except bc.Refused as exc:
        raise Refused(str(exc)) from None
    if list(names) != list(fz.FROZEN_FEATURE_NAMES):
        raise Refused("frozen features.json does not match tools.exp011_freeze.FROZEN_FEATURE_NAMES")
    return {"model_md5": md5, "threshold": thr}


def check_workers(n: int) -> int:
    if n < 1 or n > MAX_WORKERS_CAP:
        raise Refused(f"--max-workers must be between 1 and {MAX_WORKERS_CAP} (got {n})")
    return n


def prior_exp015_lines(log: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not Path(log).is_file():
        return out
    for line in Path(log).read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if str((rec.get("config") or {}).get("key", "")).startswith("exp015_"):
            out.append(rec)
    return out


def check_no_prior_tries(*logs: Path) -> None:
    for log in {str(Path(x).resolve()): Path(x) for x in logs}.values():
        found = prior_exp015_lines(log)
        if found:
            raise Refused(f"{log} already holds {len(found)} exp015_* line(s); the try budget (3 configurations) is spent or started. The run refuses (plan section 7)")


# --- tape pass: workers and patches ----------------------------------------------------------------------------------


class _AllScores:
    """Stands in for the OOF score map: every migration is simulated (the universe has no selection)."""

    def get(self, _key: str, default: Any = None) -> float:
        return 1.0


def _slim_cell(c: Mapping[str, Any]) -> dict[str, Any]:
    out = {"k": int(c["k"]), "lag": int(c.get("exit_lag", 0)), "size": int(c["size"]), "censored": bool(c.get("censored"))}
    if not out["censored"]:
        out.update({"filled": bool(c["filled"]), "status": int(c["status"]), "net0": int(c["net0"]), "sides": int(c["sides"]), "p_press": float(c["p_press"])})
    return out


@contextlib.contextmanager
def e15_v_patch() -> Any:
    """V-priced pass: wrap eem.score_one. Per migration: the frozen k=1 row gives the features; the operating point's multi_cell_patch
    simulates every report cell (k, size 0.05, exit lag) with NO selection; ONE record per migration is emitted. Restores on exit."""
    base = eem.score_one
    with op.multi_cell_patch(_AllScores(), 0.0, combos=COMBOS):
        inner = eem.score_one

        def v_score(mint_id: str, mint: Any, feat: Any, curve: Any, through_ms: int, creator_hist: Any, **kw: Any) -> list[dict[str, Any]]:
            if any(v is not None for v in kw.values()):
                raise RuntimeError(f"score_one was called with arguments this pass does not model: {sorted(kw)}")
            frozen = [r for r in base(mint_id, mint, feat, curve, through_ms, creator_hist) if r["spec"] == TARGET]
            if not frozen:
                return []
            row = frozen[0]
            cells = inner(mint_id, mint, feat, curve, through_ms, creator_hist)
            fills = eem._fills_for(mint, migrate=True)[0]
            gap = (int(fills[0].t_recv_ms) - int(mint.mig_ms)) if fills else None  # migration to the first PumpSwap print (report-only)
            return [{
                "mint": mint_id, "spec": TARGET, "day": row["day"], "mig_ms": int(mint.mig_ms), "gap_ms": gap,
                "features": [float(row["features"].get(n, 0.0)) for n in fz.FROZEN_FEATURE_NAMES],
                "cells": [_slim_cell(c) for c in cells],
            }]

        eem.score_one = v_score
        try:
            yield
        finally:
            eem.score_one = inner


@contextlib.contextmanager
def e15_nv_patch() -> Any:
    """EXP-012 section 3.1 pass (no V, frozen execution): the frozen tp50_sl30 row's pressure net, for the C1 label only."""
    base = eem.score_one

    def nv_score(mint_id: str, mint: Any, feat: Any, curve: Any, through_ms: int, creator_hist: Any, **kw: Any) -> list[dict[str, Any]]:
        out = []
        for r in base(mint_id, mint, feat, curve, through_ms, creator_hist, **kw):
            if r["spec"] == TARGET:
                out.append({"mint": mint_id, "spec": TARGET, "day": r["day"], "mig_ms": int(mint.mig_ms), "status": int(r["status"]), "filled": bool(r["filled"]), "press": float(r["press"])})
        return out

    eem.score_one = nv_score
    try:
        yield
    finally:
        eem.score_one = base


def _e15_run(orig: Callable[..., Any], tag: str, *args: Any, **kw: Any) -> Any:
    from tools import pumpswap_virtual_adapter as ad

    ad._VMAP_CACHE.clear()  # an in-process (single worker) pass must not reuse another source's map
    ctx = e15_nv_patch() if os.environ[ENV_MODE] == "nv" else e15_v_patch()
    with ctx:
        return ad._run_patched(orig, tag, *args, **kw)


# Module level so a spawn pool pickles them by reference.
def e15_worker_a(*args: Any, **kw: Any) -> Any:
    from tools import pumpswap_virtual_adapter as ad

    return _e15_run(ad._ORIG["a"], "a", *args, **kw)


def e15_worker_b(*args: Any, **kw: Any) -> Any:
    from tools import pumpswap_virtual_adapter as ad

    return _e15_run(ad._ORIG["b"], "b", *args, **kw)


def e15_worker_c(*args: Any, **kw: Any) -> Any:
    from tools import pumpswap_virtual_adapter as ad

    return _e15_run(ad._ORIG["c"], "c", *args, **kw)


def e15_holdout_worker(*args: Any, **kw: Any) -> Any:
    return _e15_run(s12._run_worker, "h", *args, **kw)


@contextlib.contextmanager
def patched_p1_workers() -> Any:
    import tools.exploration_entry_model_b2 as b2
    import tools.exploration_entry_model_b3 as b3

    saved = (eem.run_worker_a, b2.run_worker_b, b3.run_worker_c)
    eem.run_worker_a, b2.run_worker_b, b3.run_worker_c = e15_worker_a, e15_worker_b, e15_worker_c
    try:
        yield
    finally:
        eem.run_worker_a, b2.run_worker_b, b3.run_worker_c = saved


def set_pass_env(vmap: str, counts: Path, mode: str) -> None:
    from tools import pumpswap_virtual_adapter as ad

    check_vmap(vmap, VMAP_0909_SHA256, "V map for a tape pass")  # the adapter reads this path: re-check the pin before every pass
    set_env(vmap, counts)
    os.environ[ad.ENV_FROZEN] = "1" if mode == "nv" else "0"  # frozen = no V (EXP-012 section 3.1 pricing)
    os.environ[ENV_MODE] = mode


def run_p1_pass(tag: str, root: Path, mode: str, scratch: Path, vmap: str, max_workers: int) -> list[dict[str, Any]]:
    from tools.exploration_entry_model_b2 import run_all_features_b
    from tools.exploration_entry_model_b3 import run_all_features_c

    max_workers = min(max_workers, TAPE_WORKERS_CAP)
    set_pass_env(vmap, scratch / f"counts_{mode}_{tag}", mode)
    common = dict(max_workers=max_workers, buffer_hours=BUFFER_HOURS, max_home_hours=MAX_HOME_HOURS)
    out_dir = scratch / f"{mode}_{tag}"
    with patched_p1_workers():
        if tag == "P1A":
            rows = eem.run_all_features(out_dir=out_dir, backfill=root, **common)
        elif tag == "P1C":
            rows = run_all_features_c(out_dir=out_dir, root=root, **common)
        elif tag == "P1B":
            rows = run_all_features_b(out_dir=out_dir, root=root, **common)
        else:
            raise ValueError(tag)
    rows = [r for r in rows if r.get("spec") == TARGET]
    for r in rows:
        r["source"] = tag
    return rows


@dataclass(frozen=True)
class DedupedHours:
    """Picklable hour resolver over deduplicated copies (P3). Only the block's own hours resolve."""

    clean_by_hour: dict[str, str]
    has_create: dict[str, bool]
    allowed: frozenset[str]

    def __call__(self, key: str) -> dict[str, Any]:
        assert key in self.allowed, f"hour {key!r} is outside the plan's P3 block"
        root = Path(self.clean_by_hour[key])
        trade = root / "trades" / f"trades-{key}.deduped.jsonl.zst"
        if not trade.is_file():
            raise SystemExit(f"missing deduplicated trades file for hour {key} under {root}")
        create = root / "creates" / f"creates-{key}.deduped.jsonl.zst" if self.has_create.get(key) else None
        return {"hour": key, "day": key[:10], "end": int(bc.hour_dt(key).timestamp()) + 3600, "trade": trade, "create": create}


def make_p3_hours(walkers: Sequence[s12.Walker]) -> DedupedHours:
    clean_by_hour: dict[str, str] = {}
    has_create: dict[str, bool] = {}
    for w in walkers:
        entries, _ = s12.load_dedupe_manifest(w)
        created = {e.get("hour") for e in entries or [] if e.get("sub") == "creates"}
        for h in w.hours:
            clean_by_hour[h] = str(w.clean_dir)
            has_create[h] = h in created
    return DedupedHours(clean_by_hour, has_create, frozenset(clean_by_hour))


def run_holdout_pass(tag: str, hours: Any, pool_hours: Sequence[str], mode: str, scratch: Path, vmap: str, max_workers: int) -> list[dict[str, Any]]:
    max_workers = min(max_workers, TAPE_WORKERS_CAP)
    set_pass_env(vmap, scratch / f"counts_{mode}_{tag}", mode)
    plan = ff12.anchored_plan(list(pool_hours), MAX_HOME_HOURS, BUFFER_HOURS)
    rows = s12.load_rows(hours, max_workers, BUFFER_HOURS, MAX_HOME_HOURS, scratch / f"{mode}_{tag}", pool_hours=list(pool_hours), worker_fn=e15_holdout_worker, plan=plan)
    for r in rows:
        r["source"] = tag
    return rows


# --- V pre-pass (pool fields only, before any scoring) ---------------------------------------------------------------


def _tee_pools(prints: Any, sink: dict[str, set[Any]]) -> Any:
    """Pass the prints through unchanged while recording every pool each mint printed on, over EVERY scanned hour (the 24 h feature buffer and
    the trailing hours included, not only the counted window)."""
    for r in prints:
        if r.get("venue") == "pumpswap" and r.get("mint") and r.get("mint") != eem.WSOL:
            sink.setdefault(r["mint"], set()).add(r.get("pool") if isinstance(r.get("pool"), str) else None)
        yield r


def prepass(hours: Any, pool_hours: Sequence[str], vmap_path: str | Path, count_start: str, count_end: str, migrated: set[str] | None) -> tuple[dict[str, Any], dict[str, set[str]]]:
    """(coverage over the counted window, mint -> pools over ALL scanned hours). Pool fields only."""
    vmap = load_pinned_vmap(vmap_path)
    sink: dict[str, set[Any]] = {}
    cov, _window_pools = bc.vmap_coverage(_tee_pools(bc._iter_pool_prints(hours, pool_hours), sink), vmap, bc.hour_ms(count_start), bc.hour_ms(count_end), migrated, return_mint_pools=True)
    return cov, sink


def p1_hour_resolvers(roots: Mapping[str, Path]) -> dict[str, tuple[Any, list[str], set[str] | None]]:
    from tools.exploration_exits import POOL_HOURS as A_HOURS, _hour_info as hour_a
    from tools.oracle_insample_adapter import POOL_C_HOURS, _hour_info_c
    from tools.oracle_live_adapter import POOL_B_HOURS, _hour_info_b

    return {
        "P1A": (partial(hour_a, backfill=roots["fast"]), list(A_HOURS), bc.migrated_mints([str(roots["fast"])])),
        "P1C": (partial(_hour_info_c, root=roots["insample"]), list(POOL_C_HOURS), bc.migrated_mints([str(roots["insample"])])),
        "P1B": (partial(_hour_info_b, root=roots["live"]), list(POOL_B_HOURS), None),  # the Oracle live tape has no migrations/ dir
    }


# --- the row universe ------------------------------------------------------------------------------------------------


class Integrity(Exception):
    pass


class NoVInUniverse(RuntimeError):
    pass


def build_universe(v_rows: Mapping[str, Sequence[Mapping[str, Any]]], nv_rows: Mapping[str, Sequence[Mapping[str, Any]]], with_p4: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One record per migration with a k = 6, exit-lag-2 tp50_sl30 cell that is not censored (filled AND MISS), inside its block's
    counted window. `v_rows[source]`: V-pass records; `nv_rows[source]`: NV-pass records (C1 label). Migrations outside the counted
    window (the P2 feature-buffer day, a block's edge hours, a P3/P4 seam) are dropped and counted; a mint in two sources is an
    integrity failure."""
    nv: dict[str, Mapping[str, Any]] = {}
    for src, rows in nv_rows.items():
        for r in rows:
            nv[r["mint"]] = r
    universe: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    stats: dict[str, Any] = {"n_v_records": 0, "dropped_outside_window": 0, "dropped_no_primary_cell": 0, "dropped_censored_primary": 0, "c1_missing_label_0": 0, "by_source": {},
             "censored_primary_by_block": {}, "max_first_print_gap_ms_by_block": {}, "max_first_print_gap_ms": None}
    for src in SOURCES:
        if src == "P4" and not with_p4:
            continue
        blk = SOURCE_BLOCK[src]
        n = 0
        for r in v_rows.get(src, ()):
            stats["n_v_records"] += 1
            mint, mig = r["mint"], int(r["mig_ms"])
            if not in_block_window(blk, mig):
                stats["dropped_outside_window"] += 1
                continue
            cells = {(int(c["k"]), int(c["lag"])): c for c in r["cells"]}
            prim = cells.get(PRIMARY_CELL)
            if prim is None:
                stats["dropped_no_primary_cell"] += 1
                continue
            if prim["censored"]:
                stats["dropped_censored_primary"] += 1
                stats["censored_primary_by_block"][blk] = stats["censored_primary_by_block"].get(blk, 0) + 1
                continue
            gap = r.get("gap_ms")
            if gap is not None:
                cur = stats["max_first_print_gap_ms_by_block"].get(blk)
                stats["max_first_print_gap_ms_by_block"][blk] = gap if cur is None else max(cur, gap)
                stats["max_first_print_gap_ms"] = gap if stats["max_first_print_gap_ms"] is None else max(stats["max_first_print_gap_ms"], gap)
            if mint in seen:
                raise Integrity(f"integrity: mint {mint} is in both {seen[mint]} and {src}")
            seen[mint] = src
            nvr = nv.get(mint)
            if nvr is None:
                c1, c1_missing = 0, True
                stats["c1_missing_label_0"] += 1
            else:
                c1, c1_missing = (1 if float(nvr["press"]) > 0 else 0), False
            universe.append({"mint": mint, "date": utc_date(mig), "mig_ms": mig, "source": src, "block": blk, "features": [float(x) for x in r["features"]], "cells": cells, "c1": c1, "c1_missing": c1_missing})
            n += 1
        stats["by_source"][src] = n
    universe.sort(key=lambda u: (u["mig_ms"], u["mint"]))
    stats["n_universe"] = len(universe)
    return universe, stats


def universe_bytes(universe: Sequence[Mapping[str, Any]]) -> bytes:
    """The file of (mint, UTC migration date, features), sorted by (date, mint). Its sha256 is recorded before any fit."""
    lines = [json.dumps({"mint": u["mint"], "date": u["date"], "features": list(u["features"])}, sort_keys=True) for u in sorted(universe, key=lambda u: (u["date"], u["mint"]))]
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def universe_sha256(universe: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(universe_bytes(universe)).hexdigest()


# --- costs: haircut and per-leg nets ----------------------------------------------------------------------------------


def haircut_delta(cell: Mapping[str, Any]) -> float:
    """net0' = net0 - P * 0.0042038 on a filled trade, P = max(net0 + size, 0). Misses and censored cells unchanged (bc.haircut_delta)."""
    return bc.haircut_delta({"censored": cell.get("censored"), "filled": cell.get("filled"), "status": cell.get("status", 0), "net0": cell.get("net0", 0), "size": cell["size"]})


def cell_nets(cell: Mapping[str, Any] | None, haircut: bool = True) -> dict[str, float] | None:
    """{"flat", "press"} net lamports at fee 505,000 per side, or None for a censored / absent cell. A MISS pays one fee."""
    if cell is None or cell.get("censored"):
        return None
    if "fixed_nets" in cell:  # report-only sensitivity: a removed mint scored at -(SIZE + fees) on both legs
        return dict(cell["fixed_nets"])
    net0 = cell["net0"] + (haircut_delta(cell) if haircut else 0.0)
    return {"flat": mixed_net(net0, cell["sides"], cell["status"], FEE, FLAT_FAIL), "press": mixed_net(net0, cell["sides"], cell["status"], FEE, cell["p_press"])}


def label_c2(u: Mapping[str, Any]) -> int:
    """1 iff E = (1 - p_fail) * net' - p_fail * 505,000 > 0 (net' = haircut net of both fees; p_fail = pressure scale 1). A MISS is 0."""
    cell = u["cells"].get(PRIMARY_CELL)
    nets = cell_nets(cell)
    if nets is None or not cell["filled"] or cell["status"] == MISS:
        return 0
    return 1 if nets["press"] > 0 else 0


def labels(universe: Sequence[Mapping[str, Any]]) -> dict[str, list[int]]:
    return {"c1": [int(u["c1"]) for u in universe], "c2": [label_c2(u) for u in universe]}


# --- fit engine -------------------------------------------------------------------------------------------------------

_FIT: dict[str, Any] = {}


def _init_fit(npz_path: str | None, arrays: Mapping[str, Any] | None = None) -> None:
    import numpy as np

    _FIT.clear()
    if arrays is not None:
        _FIT.update(arrays)
    else:
        with np.load(npz_path) as z:
            _FIT.update({k: z[k] for k in z.files})


def fit_arrays(universe: Sequence[Mapping[str, Any]], lab: Mapping[str, Sequence[int]], dates: Sequence[str], removed: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """`removed` rows are appended flagged `extra`: they are NEVER in any training or inner-OOF set; the outer fold of their date only scores them
    (report-only sensitivity of the unpriceable-mint removal)."""
    import numpy as np

    n_real = len(universe)
    universe = list(universe) + list(removed)
    lab = {k: list(v) + [0] * len(removed) for k, v in lab.items()}
    didx = {d: i for i, d in enumerate(dates)}
    blk = np.asarray([["P1", "P2", "P3", "P4"].index(u["block"]) for u in universe], dtype=np.int8)
    src = np.asarray([SOURCES.index(u["source"]) for u in universe], dtype=np.int8)
    return {
        "X": np.asarray([u["features"] for u in universe], dtype=np.float64).reshape(len(universe), len(fz.FROZEN_FEATURE_NAMES)),
        "date_idx": np.asarray([didx[u["date"]] for u in universe], dtype=np.int32),
        "date_start_ms": np.asarray([date_start_ms(d) for d in dates], dtype=np.int64),
        "mig_ms": np.asarray([u["mig_ms"] for u in universe], dtype=np.int64),
        "block": blk,
        "source": src,
        "y_c1": np.asarray(lab["c1"], dtype=np.int8),
        "y_c2": np.asarray(lab["c2"], dtype=np.int8),
        "extra": np.asarray([False] * n_real + [True] * len(removed), dtype=bool),
    }


def fit_cfg(x: Any, y: Any, mdl: int) -> Any:
    """tools.exp011_freeze._fit unchanged; only min_data_in_leaf varies (C3 = 75). The module dict is restored on exit."""
    old = fz.LGB_PARAMS["min_data_in_leaf"]
    fz.LGB_PARAMS["min_data_in_leaf"] = mdl
    try:
        return fz._fit(x, y)
    finally:
        fz.LGB_PARAMS["min_data_in_leaf"] = old


def _scope(name: str) -> Any:
    import numpy as np

    blk, src = _FIT["block"], _FIT["source"]
    real = ~_FIT["extra"]  # a removed (extra) row is in no scope: never trained on, never an inner-OOF row
    if name == "all":
        return real
    if name == "p1a":
        return (src == SOURCES.index("P1A")) & real
    if name == "sept":  # P1 + P3 + P4: the September dates
        return (blk != 1) & real
    raise ValueError(name)


def inner_fold(train_mask: Any, e: int, y: Any, mdl: int) -> tuple[Any, list[float]] | None:
    """Score the rows of date e that are in `train_mask`, by a model trained on `train_mask` minus date e minus the 35-minute purge
    around date e. None when the fold cannot train (fewer than 20 rows, no test rows, one class), as tools.exp011_freeze."""
    import numpy as np

    X, di, mig, st = _FIT["X"], _FIT["date_idx"], _FIT["mig_ms"], _FIT["date_start_ms"]
    start = int(st[e])
    tr = train_mask & (di != e) & ~purge_mask(mig, start, start + DAY_MS)
    te = np.where(train_mask & (di == e))[0]
    if tr.sum() < 20 or len(te) == 0 or len(set(y[tr].tolist())) < 2:
        return None
    m = fit_cfg(X[tr], y[tr], mdl)
    return te, fz._predict(m, X[te])


def task_outer(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Outer held-out date d: the model and the threshold use only the other dates (and the purge around d). One inner fit per other
    date gives the inner OOF scores whose p90 is d's threshold; one fit on all other dates scores d."""
    import numpy as np

    cfg = CONFIGS[spec["cfg"]]
    y = _FIT["y_c1" if cfg["label"] == "c1" else "y_c2"]
    X, di, mig, st = _FIT["X"], _FIT["date_idx"], _FIT["mig_ms"], _FIT["date_start_ms"]
    scope, d = _scope(spec["scope"]), int(spec["date"])
    mdl = int(cfg["min_data_in_leaf"])
    start = int(st[d])
    train_mask = scope & (di != d) & ~purge_mask(mig, start, start + DAY_MS)
    te = np.where(scope & (di == d))[0]
    inner: list[float] = []
    for e in sorted(set(di[scope & (di != d)].tolist())):
        res = inner_fold(train_mask, int(e), y, mdl)
        if res is not None:
            inner.extend(res[1])
    xe = np.where(_FIT["extra"] & (di == d))[0] if spec["scope"] == "all" else np.zeros(0, dtype=int)
    out: dict[str, Any] = {"spec": dict(spec), "idx": te.tolist(), "scores": None, "thr": {}, "n_inner": len(inner), "trained": False, "x_idx": xe.tolist(), "x_scores": None}
    if inner:
        out["thr"] = {"p90": percentile(inner, THRESHOLD_PCT), **{f"p{int(p * 100)}": percentile(inner, p) for p in REPORT_PCTS}}
    if len(te) and train_mask.sum() >= 20 and len(set(y[train_mask].tolist())) >= 2:
        m = fit_cfg(X[train_mask], y[train_mask], mdl)
        out["scores"] = fz._predict(m, X[te])
        out["trained"] = True
        if len(xe):
            try:  # report-only: scoring the removed mints must never abort the config
                out["x_scores"] = fz._predict(m, X[xe])
            except Exception as exc:  # noqa: BLE001
                out["x_error"] = f"{type(exc).__name__}: {exc}"
    return out


def task_sept_inner(spec: Mapping[str, Any]) -> dict[str, Any]:
    cfg = CONFIGS[spec["cfg"]]
    y = _FIT["y_c1" if cfg["label"] == "c1" else "y_c2"]
    res = inner_fold(_scope("sept"), int(spec["date"]), y, int(cfg["min_data_in_leaf"]))
    return {"spec": dict(spec), "idx": None if res is None else res[0].tolist(), "scores": None if res is None else res[1]}


def task_sept_final(spec: Mapping[str, Any]) -> dict[str, Any]:
    import numpy as np

    cfg = CONFIGS[spec["cfg"]]
    y = _FIT["y_c1" if cfg["label"] == "c1" else "y_c2"]
    X = _FIT["X"]
    tr = _scope("sept")
    te = np.where((_FIT["block"] == 1) & ~_FIT["extra"])[0]  # P2
    if tr.sum() < 20 or len(te) == 0 or len(set(y[tr].tolist())) < 2:
        return {"spec": dict(spec), "idx": te.tolist(), "scores": None}
    m = fit_cfg(X[tr], y[tr], int(cfg["min_data_in_leaf"]))
    return {"spec": dict(spec), "idx": te.tolist(), "scores": fz._predict(m, X[te])}


def run_task(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {"outer": task_outer, "sept_inner": task_sept_inner, "sept_final": task_sept_final}[spec["kind"]](spec)


class Runner:
    """Runs fit tasks inline (max_workers == 1, tests) or in a spawn pool whose workers load the arrays once from an .npz."""

    def __init__(self, arrays: Mapping[str, Any], npz_path: Path | None, max_workers: int) -> None:
        self.max_workers = max_workers
        self.pool = None
        if max_workers <= 1:
            _init_fit(None, arrays)
        else:
            import numpy as np

            assert npz_path is not None
            npz_path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(npz_path, **arrays)
            self.pool = mp.get_context("spawn").Pool(processes=max_workers, initializer=_init_fit, initargs=(str(npz_path),))

    def map(self, tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if self.pool is None:
            return [run_task(t) for t in tasks]
        return list(self.pool.imap_unordered(run_task, tasks, chunksize=1))

    def close(self) -> None:
        if self.pool is not None:
            self.pool.terminate()
            self.pool.join()
            self.pool = None


def nested_oof(runner: Runner, cfg_id: str, n_rows: int, date_ids: Sequence[int], scope: str = "all") -> dict[str, Any]:
    """Nested leave-one-date-out out-of-fold scores for one config over `date_ids`. Per row: its outer-fold score and that outer
    fold's p80/p90/p95 thresholds. The final model's threshold is the p90 of the pooled outer OOF scores (index round(0.90*(n-1)))."""
    res = runner.map([{"kind": "outer", "cfg": cfg_id, "scope": scope, "date": int(d)} for d in date_ids])
    nan = float("nan")
    score = [nan] * n_rows
    thr = {k: [nan] * n_rows for k in ("p90", "p80", "p95")}
    folds = []
    pooled: list[float] = []
    x_score: dict[int, float] = {}
    x_thr: dict[int, float] = {}
    x_errors: list[str] = []
    for r in sorted(res, key=lambda x: x["spec"]["date"]):
        folds.append({"date_idx": r["spec"]["date"], "trained": r["trained"], "n_test": len(r["idx"]), "n_inner_oof": r["n_inner"], "thr_p90": r["thr"].get("p90")})
        if not r["trained"] or r["scores"] is None or not r["thr"]:
            continue
        for i, s in zip(r["idx"], r["scores"]):
            score[i] = s
            pooled.append(s)
            for k in thr:
                thr[k][i] = r["thr"][k]
        if r.get("x_error"):
            x_errors.append(r["x_error"])
        try:
            for i, s in zip(r.get("x_idx", []), r.get("x_scores") or []):  # removed mints: scored by this outer fold's model, never pooled
                x_score[i - n_rows] = s
                x_thr[i - n_rows] = r["thr"]["p90"]
        except Exception as exc:  # noqa: BLE001
            x_errors.append(f"{type(exc).__name__}: {exc}")
    if x_errors:  # the sensitivity is dropped for this config; the OOF scores above are untouched
        x_score, x_thr = {}, {}
    return {"x_error": "; ".join(sorted(set(x_errors))) or None, "x_score": x_score, "x_thr": x_thr, "score": score, "thr": thr, "folds": folds, "final_threshold_p90": percentile(pooled, THRESHOLD_PCT), "n_pooled_oof": len(pooled)}


def transfer_selection(runner: Runner, cfg_id: str, n_rows: int, sept_dates: Sequence[int]) -> dict[str, Any]:
    """Bar 6: train on the September dates only (inner purged LODO OOF p90 as the threshold, then one fit on all of them), score P2."""
    inner = runner.map([{"kind": "sept_inner", "cfg": cfg_id, "date": int(d)} for d in sept_dates])
    pooled = [s for r in inner if r["scores"] is not None for s in r["scores"]]
    thr = percentile(pooled, THRESHOLD_PCT)
    final = runner.map([{"kind": "sept_final", "cfg": cfg_id}])[0]
    sel = [False] * n_rows
    score = [float("nan")] * n_rows
    if final["scores"] is not None and thr is not None:
        for i, s in zip(final["idx"], final["scores"]):
            score[i] = s
            sel[i] = s >= thr
    return {"selected": sel, "score": score, "threshold_p90": thr, "n_inner_oof": len(pooled)}


# --- statistics, bars -------------------------------------------------------------------------------------------------


def book_trades(trades: Sequence[Mapping[str, Any]], leg: str) -> list[BookTrade]:
    return [BookTrade(mint=t["mint"], t_ms=e11._t_ms_for_day(t["day"]), pnl=int(round(t[leg]))) for t in trades]


def leg_stats(trades: Sequence[Mapping[str, Any]], leg: str) -> dict[str, Any]:
    st = book_stats(book_trades(trades, leg))
    ci = st["mean_ci90_sol"]
    by_date: dict[str, list[float]] = {}
    for t in trades:
        by_date.setdefault(t["day"], []).append(float(int(round(t[leg]))))
    dci = date_cluster_ci(by_date)  # book_stats resamples tokens; the date-cluster CI resamples whole UTC dates
    return {
        "n": st["n"], "filled": sum(1 for t in trades if t["filled"]),
        "mean_sol": st["mean_sol"], "ci90_sol": ci, "ci_lo": None if not ci else ci[0],
        "ci90_date_sol": dci, "ci_lo_date": None if not dci else dci[0],
        "total_sol": st["total_sol"], "ex_top3_sol": st["total_ex_top3_sol"],
        "dates": [{"day": d["day"], "n": d["n"], "total_sol": d["total_sol"], "mean_sol": d["mean_sol"]} for d in st["days"]],
        "dates_with_trades": st["n_days"], "dates_positive": st["days_positive"],
    }


def gate_bars(st: Mapping[str, Any], n_scope_dates: int) -> dict[str, Any]:
    """The gate bars with the screen's date rule: a UTC date with no trade is NOT positive, so the majority is over all the
    dates in scope, not only the dates with trades."""
    b = {
        "n_ge_100": st["n"] >= MIN_N,
        "dates_ge_5": st["dates_with_trades"] >= MIN_DATES,
        "majority_dates_positive": st["dates_positive"] * 2 > n_scope_dates,
        "ci_lower_gt_0_book_stats": st["ci_lo"] is not None and st["ci_lo"] > 0,
        "ci_lower_gt_0_date_cluster": st.get("ci_lo_date") is not None and st["ci_lo_date"] > 0,
        "ex_top3_gt_0": st["ex_top3_sol"] is not None and st["ex_top3_sol"] > 0,
    }
    b["ci_lower_gt_0"] = b["ci_lower_gt_0_book_stats"] and b["ci_lower_gt_0_date_cluster"]  # both resamplers must pass (post-pin item 8)
    b["all"] = all(v for k, v in b.items() if k not in ("ci_lower_gt_0_book_stats", "ci_lower_gt_0_date_cluster", "all"))
    return b


def concentration_bar(st: Mapping[str, Any]) -> dict[str, Any]:
    """No UTC date contributes more than 20% of the positive-date total SOL, AND total SOL excluding the best date is > 0."""
    days = st["dates"]
    pos = [d["total_sol"] for d in days if d["total_sol"] > 0]
    pos_total = sum(pos)
    best = max((d["total_sol"] for d in days), default=None)
    max_share = (max(pos) / pos_total) if pos and pos_total > 0 else None
    ex_best = None if best is None else st["total_sol"] - best
    b = {"max_share_of_positive_total": max_share, "total_ex_best_date_sol": ex_best,
         "share_le_20pct": max_share is not None and max_share <= CONCENTRATION_MAX, "ex_best_gt_0": ex_best is not None and ex_best > 0}
    b["all"] = b["share_le_20pct"] and b["ex_best_gt_0"]
    return b


def date_cluster_ci(values_by_date: Mapping[str, Sequence[float]], draws: int = BOOT_DRAWS, seed: int = BOOT_SEED) -> list[float] | None:
    """CI90 of the mean, resampling whole UTC dates with replacement (1,000 draws, seed 1; the book_stats 5th/95th percentile)."""
    keys = sorted(k for k, v in values_by_date.items() if len(v))
    if not keys:
        return None
    sums = [sum(values_by_date[k]) for k in keys]
    cnts = [len(values_by_date[k]) for k in keys]
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        pick = [rng.randrange(len(keys)) for _ in keys]
        n = sum(cnts[i] for i in pick)
        means.append(sum(sums[i] for i in pick) / n)
    means.sort()
    return [_pct(means, 0.05) / LAMPORTS, _pct(means, 0.95) / LAMPORTS]


def jaccard(a: set[str], b: set[str]) -> float | None:
    u = a | b
    return None if not u else len(a & b) / len(u)


def selected_trades(universe: Sequence[Mapping[str, Any]], sel: Sequence[bool], cell: tuple[int, int] = PRIMARY_CELL, haircut: bool = True, rows: Sequence[int] | None = None) -> tuple[list[dict[str, Any]], int]:
    """Trades (filled and MISS) of the selected universe rows at one cell; (trades, n_without_the_cell)."""
    out: list[dict[str, Any]] = []
    skipped = 0
    for i in (range(len(universe)) if rows is None else rows):
        if not sel[i]:
            continue
        u = universe[i]
        c = u["cells"].get(cell)
        nets = cell_nets(c, haircut)
        if nets is None:
            skipped += 1
            continue
        out.append({"mint": u["mint"], "day": u["date"], "filled": bool(c["filled"]), "flat": nets["flat"], "press": nets["press"], "source": u["source"], "block": u["block"]})
    return out, skipped


def scope_rows(universe: Sequence[Mapping[str, Any]], blocks: Sequence[str] | None = None, sources: Sequence[str] | None = None) -> list[int]:
    return [i for i, u in enumerate(universe) if (blocks is None or u["block"] in blocks) and (sources is None or u["source"] in sources)]


def scope_report(trades: Sequence[Mapping[str, Any]], n_scope_dates: int) -> dict[str, Any]:
    """Per-leg stats, gate bars and the concentration bar for one scope's selected trades."""
    out: dict[str, Any] = {"n_scope_dates": n_scope_dates}
    for leg in LEGS:
        st = leg_stats(trades, leg)
        out[leg] = {**st, "gate": gate_bars(st, n_scope_dates), "concentration": concentration_bar(st)}
    out["gate_all"] = all(out[leg]["gate"]["all"] for leg in LEGS)
    out["concentration_all"] = all(out[leg]["concentration"]["all"] for leg in LEGS)
    return out


def paired_bar(universe: Sequence[Mapping[str, Any]], new_sel: Sequence[bool], frozen_sel: Sequence[bool], rows: Sequence[int]) -> dict[str, Any]:
    """Bar 3: x_m = (s_new,m - s_frozen,m) * net_m over EVERY migration on the non-P1 dates; mean > 0 and the date-cluster CI90 lower
    bound > 0 under both fail models. Also reported, not gating: per-trade difference (mean net per entered trade, new minus frozen) and
    the Jaccard of the two selected sets."""
    out: dict[str, Any] = {"n_migrations": len(rows)}
    a = {universe[i]["mint"] for i in rows if new_sel[i]}
    b = {universe[i]["mint"] for i in rows if frozen_sel[i]}
    out["jaccard"] = jaccard(a, b)
    out["n_new"], out["n_frozen"] = len(a), len(b)
    ok = True
    for leg in LEGS:
        by_date: dict[str, list[float]] = {}
        new_nets: list[float] = []
        fr_nets: list[float] = []
        for i in rows:
            u = universe[i]
            nets = cell_nets(u["cells"][PRIMARY_CELL])
            net = nets[leg]
            by_date.setdefault(u["date"], []).append((int(bool(new_sel[i])) - int(bool(frozen_sel[i]))) * net)
            if new_sel[i]:
                new_nets.append(net)
            if frozen_sel[i]:
                fr_nets.append(net)
        allx = [v for xs in by_date.values() for v in xs]
        mean = (sum(allx) / len(allx) / LAMPORTS) if allx else None
        ci = date_cluster_ci(by_date)
        mn = (sum(new_nets) / len(new_nets) / LAMPORTS) if new_nets else None
        mf = (sum(fr_nets) / len(fr_nets) / LAMPORTS) if fr_nets else None
        leg_ok = mean is not None and mean > 0 and ci is not None and ci[0] > 0
        ok = ok and leg_ok
        out[leg] = {"mean_x_sol": mean, "ci90_sol": ci, "ci_lo": None if ci is None else ci[0], "pass": bool(leg_ok),
                    "per_trade_new_sol": mn, "per_trade_frozen_sol": mf, "per_trade_difference_sol": (None if mn is None or mf is None else mn - mf)}
    out["pass"] = bool(ok)
    return out


def evaluate_config(universe: Sequence[Mapping[str, Any]], nested: Mapping[str, Any], transfer: Mapping[str, Any], frozen_sel: Sequence[bool], with_p4: bool) -> dict[str, Any]:
    """Bars 1-6 for one configuration. A configuration passes only if all six hold under both fail models."""
    score, thr = nested["score"], nested["thr"]["p90"]
    sel = [(s == s and t == t and s >= t) for s, t in zip(score, thr)]  # NaN never selects
    n_all = len(pool_dates(with_p4))
    n_non = len(non_p1_dates(with_p4))
    all_rows = scope_rows(universe)
    non_rows = scope_rows(universe, ["P2", "P3", "P4"])
    p2p4_rows = scope_rows(universe, ["P2", "P4"])
    bars: dict[str, Any] = {}
    tr_all, _ = selected_trades(universe, sel, rows=all_rows)
    tr_non, _ = selected_trades(universe, sel, rows=non_rows)
    tr_p2p4, _ = selected_trades(universe, sel, rows=p2p4_rows)
    rep_all, rep_non = scope_report(tr_all, n_all), scope_report(tr_non, n_non)
    bars["bar1"] = {"scope": f"all {n_all} dates", "report": rep_all, "pass": rep_all["gate_all"]}
    bars["bar2"] = {"scope": f"non-P1 {n_non} dates", "report": rep_non, "pass": rep_non["gate_all"]}
    paired = paired_bar(universe, sel, frozen_sel, non_rows)
    bars["bar3"] = {"scope": f"non-P1 {n_non} dates, paired vs frozen EXP-012", "report": paired, "pass": paired["pass"]}
    bars["bar4"] = {"scope": f"all {n_all} dates and non-P1 {n_non} dates", "all_dates": {leg: rep_all[leg]["concentration"] for leg in LEGS}, "non_p1": {leg: rep_non[leg]["concentration"] for leg in LEGS},
                    "pass": bool(rep_all["concentration_all"] and rep_non["concentration_all"])}
    p2p4 = {leg: leg_stats(tr_p2p4, leg) for leg in LEGS}
    bars["bar5"] = {"scope": "P2+P4 only" if with_p4 else "P2 only (P4 not in the pool)", "mean_sol": {leg: p2p4[leg]["mean_sol"] for leg in LEGS}, "n": p2p4["flat"]["n"],
                    "pass": all(p2p4[leg]["mean_sol"] is not None and p2p4[leg]["mean_sol"] > 0 for leg in LEGS)}
    tsel = transfer["selected"]
    p2_rows = scope_rows(universe, ["P2"])
    tr_t, _ = selected_trades(universe, tsel, rows=p2_rows)
    n_p2 = len(p2_dates())
    t_stats = {leg: leg_stats(tr_t, leg) for leg in LEGS}
    t_ok = {leg: t_stats[leg]["mean_sol"] is not None and t_stats[leg]["mean_sol"] > 0 and t_stats[leg]["dates_positive"] * 2 > n_p2 for leg in LEGS}
    bars["bar6"] = {"scope": f"train September dates, score P2 ({n_p2} dates)", "threshold_p90": transfer["threshold_p90"], "n": t_stats["flat"]["n"],
                    "mean_sol": {leg: t_stats[leg]["mean_sol"] for leg in LEGS}, "dates_positive": {leg: t_stats[leg]["dates_positive"] for leg in LEGS}, "majority_needed_of": n_p2, "per_leg": t_ok, "pass": all(t_ok.values())}
    passes = all(bars[f"bar{i}"]["pass"] for i in range(1, 7))
    return {"selected": sel, "bars": bars, "passes": bool(passes), "pooled_press_mean_non_p1_sol": rep_non["press"]["mean_sol"]}


def report_only(universe: Sequence[Mapping[str, Any]], nested: Mapping[str, Any], sel: Sequence[bool], with_p4: bool) -> dict[str, Any]:
    """Never gating, never selected on."""
    n_all, n_non = len(pool_dates(with_p4)), len(non_p1_dates(with_p4))
    non_rows = scope_rows(universe, ["P2", "P3", "P4"])
    out: dict[str, Any] = {"selected_fraction": (sum(sel) / len(sel)) if len(sel) else None}
    variants = {}
    for name, cell, hc in (("k4", (4, 2), True), ("k8", (8, 2), True), ("lag0", (6, 0), True), ("raw_unhaircut", PRIMARY_CELL, False)):
        tr, skipped = selected_trades(universe, sel, cell, hc)
        trn, _ = selected_trades(universe, sel, cell, hc, rows=non_rows)
        variants[name] = {"n_without_cell": skipped, "all": scope_report(tr, n_all), "non_p1": scope_report(trn, n_non)}
    out["variants"] = variants
    pct_sel = {}
    for key in ("p80", "p95"):
        thr = nested["thr"][key]
        s2 = [(s == s and t == t and s >= t) for s, t in zip(nested["score"], thr)]
        tr, _ = selected_trades(universe, s2)
        trn, _ = selected_trades(universe, s2, rows=non_rows)
        pct_sel[key] = {"selected_fraction": sum(s2) / len(s2) if s2 else None, "all": scope_report(tr, n_all), "non_p1": scope_report(trn, n_non)}
    out["percentile_thresholds"] = pct_sel
    by_source = {}
    for src in SOURCES:
        rows = scope_rows(universe, sources=[src])
        if not rows:
            continue
        tr, _ = selected_trades(universe, sel, rows=rows)
        by_source[src] = {"n_rows": len(rows), "n_entered": len(tr), **{leg: {k: v for k, v in leg_stats(tr, leg).items() if k in ("mean_sol", "ci90_sol", "total_sol", "ex_top3_sol", "n")} for leg in LEGS}}
    out["per_source"] = by_source
    by_block = {}
    for blk in ("P1", "P2", "P3", "P4"):
        rows = scope_rows(universe, [blk])
        if not rows:
            continue
        tr, _ = selected_trades(universe, sel, rows=rows)
        by_block[blk] = {"n_rows": len(rows), "n_entered": len(tr), **{leg: {k: v for k, v in leg_stats(tr, leg).items() if k in ("mean_sol", "ci90_sol", "total_sol", "ex_top3_sol", "n")} for leg in LEGS}}
    out["per_block"] = by_block
    dates = pool_dates(with_p4)
    half = len(dates) // 2
    halves = {}
    for name, ds in (("first_half", set(dates[:half])), ("second_half", set(dates[half:]))):
        tr, _ = selected_trades(universe, sel, rows=[i for i, u in enumerate(universe) if u["date"] in ds])
        halves[name] = {"n": len(tr), **{leg: leg_stats(tr, leg)["mean_sol"] for leg in LEGS}}
    out["first_vs_second_half"] = halves
    return out


def fast_only_report(universe: Sequence[Mapping[str, Any]], runner: Runner, cfg_id: str, dates: Sequence[str]) -> dict[str, Any]:
    """Pool A (fast tape) only, its own nested LODO and p90, as DEC-017 (a) defined it. Report-only; never gating."""
    rows = scope_rows(universe, sources=["P1A"])
    ds = sorted({universe[i]["date"] for i in rows})
    didx = {d: i for i, d in enumerate(dates)}
    nested = nested_oof(runner, cfg_id, len(universe), [didx[d] for d in ds], scope="p1a")
    sel = [(s == s and t == t and s >= t) for s, t in zip(nested["score"], nested["thr"]["p90"])]
    tr, _ = selected_trades(universe, sel)
    rep = scope_report(tr, len(ds))
    return {"line": FAST_ONLY_LINE, "dates": ds, "n_rows": len(rows), "n_entered": len(tr),
            **{leg: {k: rep[leg][k] for k in ("mean_sol", "ci90_sol", "total_sol", "ex_top3_sol", "dates_positive", "dates_with_trades")} for leg in LEGS}}


def frozen_selection(universe: Sequence[Mapping[str, Any]], artifact_dir: Path | None = None, scorer: Callable[[Sequence[Sequence[float]]], Sequence[float]] | None = None) -> list[bool]:
    """s_frozen,m = frozen EXP-012 score >= 0.8030766588450794 (model never refit). `scorer` is for tests."""
    import numpy as np

    x = np.asarray([u["features"] for u in universe], dtype=np.float64).reshape(len(universe), len(fz.FROZEN_FEATURE_NAMES))
    if scorer is None:
        model, thr, names = e11.load_frozen_spec(artifact_dir or DEFAULT_ARTIFACT_DIR)
        assert list(names) == list(fz.FROZEN_FEATURE_NAMES)
        scores = model.predict(x, num_threads=1)
    else:
        scores = scorer(x)
    return [bool(float(s) >= FROZEN_THRESHOLD) for s in scores]


def knife_edge(cfg_result: Mapping[str, Any]) -> bool:
    """k = 6 passes and both k = 4 and k = 8 are negative (a k variant is negative if its mean is <= 0 under either leg, all dates)."""
    if not cfg_result["passes"]:
        return False
    v = cfg_result["report_only"]["variants"]
    return all(any((v[k]["all"][leg]["mean_sol"] or 0.0) <= 0 for leg in LEGS) for k in ("k4", "k8"))


def decide_outcome(results: Mapping[str, Mapping[str, Any]], statuses: Mapping[str, str]) -> dict[str, Any]:
    """The matched outcome (section 5, post-pin item 10). INCOMPLETE unless all three configurations completed (no pass is claimed).
    If more than one passes, the largest pooled pressure mean on the non-P1 dates goes to confirmation; no discretion; a tie goes to the
    lowest config index. None passes: the family is closed."""
    if not all(statuses.get(c) == "completed" for c in CONFIGS):
        return {"passing": [], "selected_for_confirmation": None, "family_closed": False, "outcome": OUTCOME_INCOMPLETE, "knife_edge": False}
    passing = [c for c, r in results.items() if r.get("passes")]
    if passing:
        winner = max(passing, key=lambda c: (results[c]["pooled_press_mean_non_p1_sol"], -list(CONFIGS).index(c)))
        text = OUTCOME_PASS.format(name=CONFIGS[winner]["name"])
        if len(passing) > 1:
            text += f" (passing: {', '.join(CONFIGS[c]['name'] for c in passing)}; chosen by the largest pooled pressure mean on the non-P1 dates, a tie to the lowest config index.)"
        return {"passing": passing, "selected_for_confirmation": winner, "family_closed": False, "outcome": text, "knife_edge": bool(knife_edge(results[winner]))}
    return {"passing": [], "selected_for_confirmation": None, "family_closed": True, "outcome": OUTCOME_NONE, "knife_edge": False}


def removed_bias(universe: Sequence[Mapping[str, Any]], removed: Sequence[Mapping[str, Any]], nested: Mapping[str, Any], transfer: Mapping[str, Any], frozen_sel: Sequence[bool],
                 frozen_removed_sel: Sequence[bool], ev: Mapping[str, Any], with_p4: bool) -> dict[str, Any]:
    """Report-only, after `started`. How many removed mints this config (and the frozen model) would have selected, and bars 1-5 recomputed with every
    such selected removed mint scored at -(SIZE + 2 fees) on both legs. The bars themselves are unchanged (bar 6's transfer selection never touches them)."""
    m = len(removed)
    loss = -float(op.size_lamports(SIZE_SOL) + 2 * FEE)
    out: dict[str, Any] = {"statement": BIAS_STATEMENT, "scenario": SCENARIO_WORDING, "n_removed": m, "loss_per_selected_removed_trade_lamports": loss}
    if not m:
        return {**out, "n_removed_selected_by_config": 0, "n_removed_selected_by_frozen": 0, "note": "nothing was removed"}
    nan = float("nan")
    xs = [nested["x_score"].get(j, nan) for j in range(m)]
    xt = [nested["x_thr"].get(j, nan) for j in range(m)]
    sel_x = [(a == a and b == b and a >= b) for a, b in zip(xs, xt)]
    aug_rows = []
    for r in removed:
        cell = {"censored": False, "filled": True, "status": 1, "size": op.size_lamports(SIZE_SOL), "fixed_nets": {"flat": loss, "press": loss}}
        aug_rows.append({**r, "cells": {PRIMARY_CELL: cell}})
    aug = list(universe) + aug_rows
    aug_nested = {"score": list(nested["score"]) + xs, "thr": {"p90": list(nested["thr"]["p90"]) + xt}}
    aug_transfer = {"selected": list(transfer["selected"]) + [False] * m, "threshold_p90": transfer["threshold_p90"]}
    ev2 = evaluate_config(aug, aug_nested, aug_transfer, list(frozen_sel) + list(frozen_removed_sel), with_p4)
    out.update({
        "n_removed_selected_by_config": int(sum(sel_x)), "n_removed_selected_by_frozen": int(sum(frozen_removed_sel)),
        "bars_pass_with_removed_at_total_loss": {f"bar{i}": ev2["bars"][f"bar{i}"]["pass"] for i in range(1, 7)},
        "bars_pass_as_reported": {f"bar{i}": ev["bars"][f"bar{i}"]["pass"] for i in range(1, 7)},
        "passes_with_removed_at_total_loss": ev2["passes"], "passes_as_reported": ev["passes"],
    })
    return out


def run_screen(universe: Sequence[Mapping[str, Any]], frozen_sel: Sequence[bool], runner: Runner, with_p4: bool, on_config_done: Callable[[str, dict[str, Any]], None] | None = None,
               on_config_refused: Callable[[str, str], None] | None = None, no_v_mints: set[str] | None = None, configs: Sequence[str] = tuple(CONFIGS),
               removed: Sequence[Mapping[str, Any]] = (), frozen_removed_sel: Sequence[bool] = ()) -> dict[str, Any]:
    """All configs, sequentially (each config's folds run in parallel). `no_v_mints`: mints on pools the adapter priced without V; a
    selected trade on one refuses that config (as the back-check's primary-cell rule). Returns {cfg: result, ...} plus statuses."""
    dates = pool_dates(with_p4)
    didx = {d: i for i, d in enumerate(dates)}
    n = len(universe)
    present = sorted({universe[i]["date"] for i in range(n)})
    date_ids = [didx[d] for d in present]
    sept = sorted({didx[universe[i]["date"]] for i in range(n) if universe[i]["block"] != "P2"})
    results: dict[str, dict[str, Any]] = {}
    statuses: dict[str, str] = {}
    lab = labels(universe)
    for cid in configs:
        nested = nested_oof(runner, cid, n, date_ids)
        transfer = transfer_selection(runner, cid, n, sept)
        ev = evaluate_config(universe, nested, transfer, frozen_sel, with_p4)
        nv_sel = sorted(universe[i]["mint"] for i in range(n) if no_v_mints and universe[i]["mint"] in no_v_mints and (ev["selected"][i] or (frozen_sel[i] and universe[i]["block"] != "P1")))
        transfer_nv = sorted(universe[i]["mint"] for i in range(n) if transfer["selected"][i] and no_v_mints and universe[i]["mint"] in no_v_mints)
        if nv_sel or transfer_nv:  # defensive: the unpriceable mints were removed before `started`, so this should never fire. If it does, abort (logged aborted_after_read)
            raise NoVInUniverse(f"{len(nv_sel)} nested-OOF or frozen non-P1 selected and {len(transfer_nv)} transfer-selected trade(s) are on pools priced without V although the unpriceable-pool rule removed them: {(nv_sel + transfer_nv)[:5]}")
        ro = report_only(universe, nested, ev["selected"], with_p4)
        fast = fast_only_report(universe, runner, cid, dates)
        res = {
            "name": CONFIGS[cid]["name"], "desc": CONFIGS[cid]["desc"], "min_data_in_leaf": CONFIGS[cid]["min_data_in_leaf"],
            "passes": ev["passes"], "bars": ev["bars"], "pooled_press_mean_non_p1_sol": ev["pooled_press_mean_non_p1_sol"],
            "n_selected": int(sum(ev["selected"])), "n_rows": n, "final_threshold_p90": nested["final_threshold_p90"], "n_pooled_oof": nested["n_pooled_oof"],
            "folds": nested["folds"], "report_only": ro, "fast_only": fast,
            "label_counts": {"positive": int(sum(lab["c1" if CONFIGS[cid]["label"] == "c1" else "c2"])), "n": n, "c1_missing_label_0": sum(1 for u in universe if u["c1_missing"])},
        }
        try:  # report-only: an error here is recorded and NEVER aborts the config or spends tries
            if nested.get("x_error"):
                raise RuntimeError(f"removed-row scoring failed: {nested['x_error']}")
            res["removed_bias"] = removed_bias(universe, removed, nested, transfer, frozen_sel, frozen_removed_sel, ev, with_p4)
        except Exception as exc:  # noqa: BLE001
            res["removed_bias"] = {}
            res["sensitivity_error"] = str(exc)
        res["knife_edge"] = knife_edge(res)
        results[cid] = res
        statuses[cid] = "completed"
        if on_config_done:
            on_config_done(cid, res)
    return {"results": results, "statuses": statuses}


# --- report -----------------------------------------------------------------------------------------------------------


def first_line(with_p4: bool) -> str:
    n = len(pool_dates(with_p4))
    if with_p4:
        return f"EXP-015 screen on the {n}-UTC-date pool (P1 + explore-0814 + fresh-0903 + the EXP-011 block, 816 counted hours = 34 days, {len(non_p1_dates(True))} non-P1 dates)."
    return f"EXP-015 screen on the SMALLER {n}-UTC-date pool: the EXP-011 block (P4) is NOT in it (no clean view with VIEW.sha256), 672 counted hours = 28 days, {len(non_p1_dates(False))} non-P1 dates. Every number scales accordingly."


def _f(v: float | None, d: int = 5) -> str:
    return "n/a" if v is None else f"{v:.{d}f}"


def _ci(ci: Sequence[float] | None) -> str:
    return "n/a" if not ci else f"[{ci[0]:.5f}, {ci[1]:.5f}]"


def render_md(rep: Mapping[str, Any]) -> str:
    L = []
    if rep.get("decision", {}).get("knife_edge"):
        L += ["**KNIFE-EDGE: the k = 6 cell passes and both k = 4 and k = 8 are negative.**", ""]
    L += [rep["first_line"], "", rep["banner"], "", f"## Matched outcome", "", f"**{rep['decision']['outcome']}**", ""]
    u = rep["universe"]
    ur = u.get("unpriceable_removed") or {}
    if ur:
        L += [f"- **Unpriceable mints removed (pool-based, outcome-blind, before `started`): {ur['count']} of {ur['n_universe_before']} ({ur['fraction']:.3%}; non-P1 {ur['non_p1_count']} = {ur['non_p1_fraction']:.3%}; limit {ur['max_fraction']:.1%} each).** Per block: {ur['per_block']}. Mint ids: {', '.join(ur['mints']) or 'none'}.",
              f"- **{ur['bias_statement']}**", ""]
    L += [f"- Universe: {u['n']} migrations (k = 6, exit lag 2, filled and MISS), sha256 `{u['sha256']}` (written before any fit). By source: {u['stats']['by_source']}. Dropped: {u['stats']['dropped_outside_window']} outside the counted window, {u['stats']['dropped_no_primary_cell']} with no primary cell, {u['stats']['dropped_censored_primary']} censored. C1 label 0 because no EXP-012-pricing row: {u['stats']['c1_missing_label_0']}.",
          f"- Costs: V pricing (ONE map for every block, sha256 pinned {rep['costs']['vmap_0909_pinned']}), k = {K} from the first PumpSwap print, {SIZE_SOL} SOL, fee {FEE} per side (a MISS pays it), exit lag {EXIT_LAG}, haircut {HAIRCUT_FACTOR:.7f} x proceeds on filled trades, both fail models.",
          f"- Code {rep.get('git_head', 'n/a')}; wall {rep.get('wall_s', 0):.0f} s; workers {rep.get('max_workers')}.", ""]
    for cid, r in rep["configs"].items():
        L += [f"## {CONFIGS[cid]['name']}: {CONFIGS[cid]['desc']} (min_data_in_leaf {CONFIGS[cid]['min_data_in_leaf']}); status {rep['statuses'].get(cid)}", ""]
        if r.get("refused"):
            L += [f"REFUSED: {r['refused']}", ""]
            continue
        L += [f"- **Screen {'PASS' if r['passes'] else 'FAIL'}.** selected {r['n_selected']} of {r['n_rows']}; final threshold (p90 of pooled OOF) {_f(r['final_threshold_p90'], 6)}; knife-edge {r['knife_edge']}.", "",
              "| bar | scope | pass | detail |", "| --- | --- | --- | --- |"]
        b = r["bars"]
        for i in (1, 2):
            rp = b[f"bar{i}"]["report"]
            det = "; ".join(f"{leg}: n {rp[leg]['n']}, mean {_f(rp[leg]['mean_sol'])}, CI90 book_stats {_ci(rp[leg]['ci90_sol'])} date-cluster {_ci(rp[leg]['ci90_date_sol'])}, ex-top3 {_f(rp[leg]['ex_top3_sol'], 3)}, dates+ {rp[leg]['dates_positive']}/{rp['n_scope_dates']} (with trades {rp[leg]['dates_with_trades']})" for leg in LEGS)
            L.append(f"| {i} | {b[f'bar{i}']['scope']} | {b[f'bar{i}']['pass']} | {det} |")
        p = b["bar3"]["report"]
        det = "; ".join(f"{leg}: mean x {_f(p[leg]['mean_x_sol'], 6)}, CI90 {_ci(p[leg]['ci90_sol'])}, per-trade diff {_f(p[leg]['per_trade_difference_sol'], 6)}" for leg in LEGS) + f"; Jaccard {_f(p['jaccard'], 4)}; n new {p['n_new']}, n frozen {p['n_frozen']}"
        L.append(f"| 3 | {b['bar3']['scope']} | {b['bar3']['pass']} | {det} |")
        c = b["bar4"]
        det = "; ".join(f"{scope} {leg}: max share {_f(c[scope][leg]['max_share_of_positive_total'], 3)}, ex-best {_f(c[scope][leg]['total_ex_best_date_sol'], 3)}" for scope in ("all_dates", "non_p1") for leg in LEGS)
        L.append(f"| 4 | {c['scope']} | {c['pass']} | {det} |")
        L.append(f"| 5 | {b['bar5']['scope']} | {b['bar5']['pass']} | n {b['bar5']['n']}, mean {b['bar5']['mean_sol']} |")
        L.append(f"| 6 | {b['bar6']['scope']} | {b['bar6']['pass']} | n {b['bar6']['n']}, mean {b['bar6']['mean_sol']}, dates+ {b['bar6']['dates_positive']} of {b['bar6']['majority_needed_of']} |")
        rb = r.get("removed_bias") or {}
        if rb:
            L += ["", f"- Report-only, removed mints: this config would have selected {rb.get('n_removed_selected_by_config')} of {rb['n_removed']}, the frozen model {rb.get('n_removed_selected_by_frozen')}; bars with those scored at -(SIZE + fees), {SCENARIO_WORDING}: {rb.get('bars_pass_with_removed_at_total_loss')} (as reported: {rb.get('bars_pass_as_reported')})."]
        if r.get("sensitivity_error"):
            L += ["", f"- Report-only removed-mint sensitivity FAILED and was skipped (the config is unaffected): {r['sensitivity_error']}"]
        fo = r["fast_only"]
        L += ["", f"- Report-only fast-only (pool A, dates {fo['dates']}): n entered {fo['n_entered']}; flat {_f(fo['flat']['mean_sol'])} {_ci(fo['flat']['ci90_sol'])}, pressure {_f(fo['press']['mean_sol'])} {_ci(fo['press']['ci90_sol'])}. **{FAST_ONLY_LINE}**", ""]
        ro = r["report_only"]
        L += ["| report-only variant | n | flat mean | press mean (all dates) |", "| --- | --- | --- | --- |"]
        for name, v in ro["variants"].items():
            L.append(f"| {name} | {v['all']['flat']['n']} | {_f(v['all']['flat']['mean_sol'])} | {_f(v['all']['press']['mean_sol'])} |")
        for name, v in ro["percentile_thresholds"].items():
            L.append(f"| {name} threshold | {v['all']['flat']['n']} | {_f(v['all']['flat']['mean_sol'])} | {_f(v['all']['press']['mean_sol'])} |")
        L += ["", "| source | rows | entered | flat mean | press mean |", "| --- | --- | --- | --- | --- |"]
        for s, v in ro["per_source"].items():
            L.append(f"| {s} | {v['n_rows']} | {v['n_entered']} | {_f(v['flat']['mean_sol'])} | {_f(v['press']['mean_sol'])} |")
        h = ro["first_vs_second_half"]
        L += ["", f"- First half vs second half of the dates (mean, flat / pressure): {_f(h['first_half']['flat'])} / {_f(h['first_half']['press'])} vs {_f(h['second_half']['flat'])} / {_f(h['second_half']['press'])}; selected fraction {_f(ro['selected_fraction'], 4)}.", ""]
    L += ["## Disclosures", "", *[f"- {c}" for c in rep["caveats"]], ""]
    return "\n".join(L) + "\n"


CAVEATS = (
    "The screen is a filter for 'worth one confirmation read'; its numbers are upward-biased (section 7). A pass is not evidence for an edge.",
    "A UTC date with no trade counts as NOT positive in every majority bar (bars 1, 2, 6). The gate's own majority is over dates with trades; this is stricter.",
    "The paired bar's CI resamples whole UTC dates (1,000 draws, seed 1, 5th percentile). The gate bars use tools.paper_attention_promote.book_stats unchanged (it resamples tokens).",
    "Purge: rows within 35 minutes before the start or after the end of a UTC calendar date held out are dropped from that fold's training, in the outer and in every inner fold.",
    "Migrations inside a block's edge hours whose creation lies in the neighbouring block (the P3/P4 seam) are not counted; counts are in the universe stats.",
    "The V pre-pass for P1's Oracle live pool has no migrations/ directory: its population is every mint with a first PumpSwap print in the window.",
    "No Helius credits are used. ONE V map (pool_v_0909.json, built by job #224) prices every block; its sha256 is pinned in VMAP_0909_SHA256 (post-pin item 11).",
    BIAS_STATEMENT,
    "Mints on a PumpSwap pool with no V in the pinned map are removed from the universe before `started`, for all configs and the frozen side (pool-based, outcome-blind); the count and mint ids are in report.json.",
    "Statuses in the tries log: completed (the config's fits and bars ran), refused_after_read (a selected trade on a no-V pool, or an integrity failure), aborted_after_read (an exception or SIGTERM).",
)


# --- tries -----------------------------------------------------------------------------------------------------------


def pool_group_blocks(with_p4: bool) -> dict[str, list[dict[str, str]]]:
    from tools.exp012_support import exploration_pool_blocks

    g = {
        "P1": exploration_pool_blocks(),
        "P2": bc.pool_blocks(),
        "P3": [{"start_hour": BLOCKS["P3"][0], "end_hour_exclusive": BLOCKS["P3"][1], "host": "mal-research-0", "ledger_owner": "exploration pool"}],
    }
    if with_p4:
        g["P4"] = [{"start_hour": BLOCKS["P4"][0], "end_hour_exclusive": BLOCKS["P4"][1], "host": "mal-research-0", "ledger_owner": "EXP-011 block (exploration pool only after the ledger edit)"}]
    return g


UNIVERSE_BLOCKS = [{"start_hour": BLOCKS["P2"][0], "end_hour_exclusive": BLOCKS["P4"][1], "host": "mal-research-0", "ledger_owner": "EXP-015 row universe (bookkeeping, not a pool try)"}]


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


def _append(log: Path, cfg_id: str, group: str, status: str, blocks: Sequence[Mapping[str, str]], out_dir: Path, extra: Mapping[str, Any]) -> bool:
    from tools import mal_result

    result_path = out_dir / OUT_REPORT
    if _in_log(log, CONFIGS[cfg_id]["key"], group, status, result_path):
        return False
    c = CONFIGS[cfg_id]
    mal_result.append_try(
        log, tool=TOOL,
        config={"key": c["key"], "experiment": "EXP-015 screen", "config": c["name"], "label": c["label"], "min_data_in_leaf": c["min_data_in_leaf"], "status": status, "pool_group": group,
                "k": K, "size_sol": SIZE_SOL, "fee_lamports": FEE, "exit_lag": EXIT_LAG, "pricing": "V", "selection": "nested LODO OOF p90", **extra},
        data_blocks=list(blocks), result_path=result_path, role="exploration",
    )
    return True


def log_tries(out_dir: Path, log: Path, marker_name: str, cfg_ids: Sequence[str], status: str, with_p4: bool, universe_sha: str | None = None) -> int:
    """One line per (config, pool group the config touches) with config.status in {started, completed, refused_after_read, aborted_after_read}.
    `started` lines go to the universe-bookkeeping block (not a pool), once per config, before any fit; the others go to each pool group so each config
    counts as a try on every pool it touches (plan section 7). Idempotent per log: a marker in out_dir plus a scan of the log. Never appends a
    non-completed line for a config already logged completed in this log."""
    from tools.exp012_exit_sensitivity import _read_marker, _write_marker

    marker = out_dir / marker_name
    done = _read_marker(marker)
    n = 0
    extra = {"universe_sha256": universe_sha, "with_p4": with_p4}
    groups = {"universe": UNIVERSE_BLOCKS} if status == "started" else pool_group_blocks(with_p4)
    for cid in cfg_ids:
        for g, blocks in groups.items():
            mk = f"{cid}:{g}:{status}"
            if mk in done:
                continue
            if status in ("refused_after_read", "aborted_after_read") and _in_log(log, CONFIGS[cid]["key"], g, "completed", out_dir / OUT_REPORT):
                continue
            if _append(log, cid, g, status, blocks, out_dir, extra):
                n += 1
            done.add(mk)
            out_dir.mkdir(parents=True, exist_ok=True)
            _write_marker(marker, done)
    return n


def log_all(out_dir: Path, tries_path: Path, canonical: Path, cfg_ids: Sequence[str], status: str, with_p4: bool, universe_sha: str | None = None) -> dict[str, Any]:
    out = {"logged": log_tries(out_dir, tries_path, MARKER, cfg_ids, status, with_p4, universe_sha), "log_path": str(tries_path), "status": status}
    if Path(canonical).resolve() != Path(tries_path).resolve():
        out["canonical_logged"] = log_tries(out_dir, canonical, "tries_logged_canonical.marker", cfg_ids, status, with_p4, universe_sha)
        out["canonical_path"] = str(canonical)
    return out


# --- CLI --------------------------------------------------------------------------------------------------------------


def git_state() -> dict[str, Any]:
    import subprocess

    def run(*a: str) -> str:
        try:
            return subprocess.run(["git", "-C", str(REPO_ROOT), *a], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return "unknown"

    return {"head": run("rev-parse", "HEAD"), "dirty_tools": bool(run("status", "--porcelain", "--", "tools"))}


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--p1-fast-dir", type=Path, required=True)
    ap.add_argument("--p1-oracle-insample-dir", type=Path, required=True)
    ap.add_argument("--p1-oracle-live-dir", type=Path, required=True)
    ap.add_argument("--p2-view-dir", type=Path, action="append", default=None, help="repeatable: explore-0814 clean views w1..w7")
    ap.add_argument("--p3-root", type=Path, default=Path(P3_BASE), help="fresh-0903 deduplicated copies (w1..w3 + manifests)")
    ap.add_argument("--p4-view-dir", type=Path, action="append", default=None, help="repeatable: EXP-011 block clean view(s) with VIEW.sha256, tiling [2026-09-09T12, 2026-09-15T12); omit to run the 30-date pool")
    for blk in ("p1", "p2", "p3", "p4"):
        ap.add_argument(f"--vmap-{blk}", default=VMAP_DEFAULT, help="pool -> V map; ONE map for every block, its sha256 asserted against VMAP_0909_SHA256 (pool_v_0909.json)")
    ap.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR, help="frozen EXP-012 artifacts (read-only; the paired bar's reference)")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--tries-log", default=None)
    ap.add_argument("--canonical-tries", type=Path, default=CANONICAL_TRIES)
    ap.add_argument("--max-workers", type=int, default=DEFAULT_WORKERS, help=f"default {DEFAULT_WORKERS}, at most {MAX_WORKERS_CAP}")
    ap.add_argument("--guards-only", action="store_true", help="run every guard that precedes a read (paths, VIEW.sha256, dedupe manifests, model, V map sha, prior tries) and exit 0")
    return ap


def run_guards(args: argparse.Namespace, enforce_base: bool = True, verify: bool = True) -> dict[str, Any]:
    check_pin_ready()
    check_workers(args.max_workers)
    g1 = guard_p1(args.p1_fast_dir, args.p1_oracle_insample_dir, args.p1_oracle_live_dir, verify)
    if not args.p2_view_dir:
        raise Refused("--p2-view-dir is required (explore-0814 w1..w7)")
    g2 = guard_p2(args.p2_view_dir, verify, enforce_base)
    g3 = guard_p3(args.p3_root, verify, enforce_base)
    g4 = guard_p4(args.p4_view_dir or [], verify)
    shas = {"P1": check_vmap(args.vmap_p1, VMAP_0909_SHA256, "--vmap-p1"), "P2": check_vmap(args.vmap_p2, VMAP_0909_SHA256, "--vmap-p2"), "P3": check_vmap(args.vmap_p3, VMAP_0909_SHA256, "--vmap-p3")}
    if g4 is not None:
        shas["P4"] = check_vmap(args.vmap_p4, VMAP_0909_SHA256, "--vmap-p4")
    frozen = check_frozen_model(args.artifact_dir)
    p3_hours = [h for w in g3["walkers"] for h in w.hours]
    assert_hours_allowed(list(g2["pool"]) + list(g4["pool"] if g4 else []) + p3_hours, with_p4=g4 is not None)
    return {"g1": g1, "g2": g2, "g3": g3, "g4": g4, "vmap_sha256": shas, "frozen": frozen, "with_p4": g4 is not None}


def prepass_hours(g: Mapping[str, Any]) -> dict[str, list[str]]:
    """The hours the V pre-pass scans, per source (vprepass_all uses exactly these lists)."""
    from tools.exploration_exits import POOL_HOURS as A_HOURS
    from tools.oracle_insample_adapter import POOL_C_HOURS
    from tools.oracle_live_adapter import POOL_B_HOURS

    out: dict[str, list[str]] = {"P1A": list(A_HOURS), "P1C": list(POOL_C_HOURS), "P1B": list(POOL_B_HOURS), "P2": list(g["g2"]["pool"]), "P3": sorted(make_p3_hours(g["g3"]["walkers"]).allowed)}
    if g["g4"] is not None:
        out["P4"] = list(g["g4"]["pool"])
    return out


def adapter_hours(g: Mapping[str, Any], max_workers: int) -> dict[str, set[str]]:
    """The hours each tape pass will read through the adapter, buffer hours included: the union of the home and buffer hours of the SAME chunk
    plans the passes use (P1: the loaders' plan_workers*, P2-P4: anchored_plan over the pass's pool hours)."""
    from tools.exploration_entry_model_b2 import plan_workers_b
    from tools.exploration_entry_model_b3 import plan_workers_c

    mw = min(max_workers, TAPE_WORKERS_CAP)

    def union(plan: Sequence[tuple[int, list[str], list[str]]]) -> set[str]:
        return {h for _i, home, buf in plan for h in (*home, *buf)}

    out = {
        "P1A": union(eem.plan_workers(mw, BUFFER_HOURS, MAX_HOME_HOURS)),
        "P1C": union(plan_workers_c(mw, BUFFER_HOURS, MAX_HOME_HOURS)),
        "P1B": union(plan_workers_b(mw, BUFFER_HOURS, MAX_HOME_HOURS)),
        "P2": union(ff12.anchored_plan(list(g["g2"]["pool"]), MAX_HOME_HOURS, BUFFER_HOURS)),
        "P3": union(ff12.anchored_plan(bc.hours_range(*BLOCKS["P3"]), MAX_HOME_HOURS, BUFFER_HOURS)),
    }
    if g["g4"] is not None:
        out["P4"] = union(ff12.anchored_plan(list(g["g4"]["pool"]), MAX_HOME_HOURS, BUFFER_HOURS))
    return out


def check_adapter_hours_subset(g: Mapping[str, Any], max_workers: int) -> dict[str, int]:
    """Item 11(f)'s argument as a check, before `started`: per source, every hour the adapter reads (buffer included) must be a scanned pre-pass
    hour. Refuses (Refused) otherwise. Returns {source: n adapter hours}."""
    pre = prepass_hours(g)
    ad = adapter_hours(g, max_workers)
    for tag, hrs in ad.items():
        missing = sorted(hrs - set(pre.get(tag, ())))
        if missing:
            raise Refused(f"{tag}: {len(missing)} hour(s) the adapter reads (buffer included) are not scanned by the V pre-pass (first {missing[0]}); the pool-to-mint attribution argument needs the pre-pass hours to include the adapter's. Refusing before `started`")
    return {tag: len(h) for tag, h in ad.items()}


def vprepass_all(g: Mapping[str, Any], args: argparse.Namespace, only: Sequence[str] | None = None) -> tuple[dict[str, Any], dict[str, set[str]]]:
    """Pool-field-only coverage per source. Refuses (before any scoring) if more than 1% of a source's migrating mints' prints lack V."""
    covs: dict[str, Any] = {}
    mint_pools: dict[str, set[str]] = {}

    def one(tag: str, hours: Any, pool: Sequence[str], vmap: str, a: str, b: str, migrated: Any) -> None:
        if only is not None and tag not in only:
            return
        if callable(migrated):
            migrated = migrated()
        cov, mp_ = prepass(hours, pool, vmap, a, b, migrated)
        print(f"{tag}: {bc.coverage_line(cov)}", file=sys.stderr, flush=True)
        try:
            bc.check_v_coverage(cov)
        except bc.Refused as exc:
            raise Refused(f"{tag}: {exc}") from None
        covs[tag] = cov
        for m, ps in mp_.items():  # merge per mint: a mint seen in two sources keeps every pool
            mint_pools.setdefault(m, set()).update(ps)

    ph = prepass_hours(g)
    res = p1_hour_resolvers(g["g1"]["roots"])
    for tag in ("P1A", "P1C", "P1B"):
        hours, _pool, mig = res[tag]
        one(tag, hours, ph[tag], args.vmap_p1, BLOCKS["P1"][0], BLOCKS["P1"][1], mig)
    r2 = g["g2"]
    one("P2", bc.MultiViewHours(dict(r2["roots"])), ph["P2"], args.vmap_p2, BLOCKS["P2"][0], BLOCKS["P2"][1], lambda: bc.migrated_mints(sorted(set(r2["roots"].values()))))
    h3 = make_p3_hours(g["g3"]["walkers"])

    def mig3() -> set[str]:
        from tools.exp012_virtual_rescore import _migrated_mints

        out: set[str] = set()
        for w in g["g3"]["walkers"]:
            out |= _migrated_mints(w.clean_dir, True)
        return out

    one("P3", h3, ph["P3"], args.vmap_p3, BLOCKS["P3"][0], BLOCKS["P3"][1], mig3)
    if g["g4"] is not None:
        r4 = g["g4"]
        one("P4", bc.MultiViewHours(dict(r4["roots"])), ph["P4"], args.vmap_p4, BLOCKS["P4"][0], BLOCKS["P4"][1], lambda: bc.migrated_mints(sorted(set(r4["roots"].values()))))
    return covs, mint_pools


def args_hash(args: argparse.Namespace) -> str:
    """sha256 over the args that decide the rows (not the worker count, the out dir or the log paths)."""
    skip = {"max_workers", "guards_only", "out_dir", "tries_log", "canonical_tries"}
    d = {k: str(v) for k, v in sorted(vars(args).items()) if k not in skip}
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode("utf-8")).hexdigest()


def source_meta(g: Mapping[str, Any], args: argparse.Namespace, tag: str, head: str) -> dict[str, Any]:
    """What a cached source must still match to be reused: head, args, the source's view shas and its V-map sha."""
    if tag.startswith("P1"):
        label = {"P1A": "fast", "P1C": "insample", "P1B": "live"}[tag]
        views, vk = {label: g["g1"]["view_sha256"].get(label)}, "P1"
    elif tag == "P2":
        views, vk = dict(g["g2"]["view_sha256"]), "P2"
    elif tag == "P3":
        views, vk = dict(g["g3"]["pin_sha256"]), "P3"
    else:
        views, vk = dict((g["g4"] or {}).get("view_sha256", {})), "P4"
    return {"tag": tag, "head": head, "args_hash": args_hash(args), "view_sha256": views, "vmap_sha256": g["vmap_sha256"].get(vk)}


def _cache_paths(cache: Path, mode: str, tag: str) -> tuple[Path, Path]:
    return cache / f"{mode}_{tag}.rows.jsonl", cache / f"{mode}_{tag}.manifest.json"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def discard_cache(cache: Path, mode: str, tag: str) -> None:
    for pth in _cache_paths(cache, mode, tag):
        pth.unlink(missing_ok=True)


def write_cache(cache: Path, mode: str, tag: str, rows: Sequence[Mapping[str, Any]], meta: Mapping[str, Any], extra: Mapping[str, Any]) -> str:
    """Rows first (atomic), then the manifest: a source without a manifest is incomplete and never reused. Returns the rows sha256."""
    cache.mkdir(parents=True, exist_ok=True)
    rp, mp_ = _cache_paths(cache, mode, tag)
    tmp = rp.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    os.replace(tmp, rp)
    sha = _file_sha256(rp)
    mtmp = mp_.with_suffix(".tmp")
    mtmp.write_text(json.dumps({"schema": CACHE_SCHEMA, "meta": dict(meta), "rows_sha256": sha, "n_rows": len(rows), "extra": dict(extra)}, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(mtmp, mp_)
    return sha


def load_cache(cache: Path, mode: str, tag: str, meta: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], str] | None:
    """Reuse a source's rows only if the manifest's meta equals `meta` and the rows file still hashes to the manifest. Any mismatch (or a
    missing/unreadable half) discards this source's cache and returns None: old and new rows are never mixed."""
    rp, mp_ = _cache_paths(cache, mode, tag)
    if not rp.exists() and not mp_.exists():
        return None
    try:
        man = json.loads(mp_.read_text(encoding="utf-8"))
        ok = man.get("schema") == CACHE_SCHEMA and man.get("meta") == json.loads(json.dumps(dict(meta))) and rp.is_file() and _file_sha256(rp) == man.get("rows_sha256")
        rows = [json.loads(line) for line in rp.read_text(encoding="utf-8").splitlines() if line.strip()] if ok else []
        ok = ok and len(rows) == man.get("n_rows")
    except (OSError, ValueError, KeyError, AttributeError):
        ok = False
    if not ok:
        discard_cache(cache, mode, tag)
        return None
    return rows, man.get("extra", {}), man["rows_sha256"]


def collect_all(g: Mapping[str, Any], args: argparse.Namespace, scratch: Path, head: str = "unknown") -> tuple[dict[str, list], dict[str, list], set[str]]:
    """The tape passes, cached per (mode, source). Prints only row counts and hashes."""
    import shutil

    v_rows: dict[str, list] = {}
    nv_rows: dict[str, list] = {}
    no_v_pools: set[str] = set()
    mw = min(args.max_workers, TAPE_WORKERS_CAP)
    cache = scratch / CACHE_DIR
    p1_roots = {"P1A": g["g1"]["roots"]["fast"], "P1C": g["g1"]["roots"]["insample"], "P1B": g["g1"]["roots"]["live"]}

    def run_source(mode: str, tag: str, fn: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        meta = source_meta(g, args, tag, head)
        hit = load_cache(cache, mode, tag, meta)
        if hit is not None:
            rows, extra, sha = hit
            print(f"EXP-015 pass {mode} {tag}: cache reused rows={len(rows)} sha256={sha[:16]}", file=sys.stderr, flush=True)
        else:
            shutil.rmtree(scratch / f"{mode}_{tag}", ignore_errors=True)  # a discarded cache never leaves rows behind to mix in
            shutil.rmtree(scratch / f"counts_{mode}_{tag}", ignore_errors=True)
            print(f"EXP-015 pass {mode} {tag}...", file=sys.stderr, flush=True)
            rows = fn()
            extra = {"no_v_pools": sorted(bc.adapter_no_v_pools(scratch / f"counts_{mode}_{tag}"))}
            sha = write_cache(cache, mode, tag, rows, meta, extra)
            print(f"EXP-015 pass {mode} {tag}: cached rows={len(rows)} sha256={sha[:16]}", file=sys.stderr, flush=True)
        if mode == "v":
            no_v_pools.update(extra.get("no_v_pools", []))
        return rows

    for mode, store in (("nv", nv_rows), ("v", v_rows)):
        vm = args.vmap_p1
        for tag in ("P1A", "P1C", "P1B"):
            store[tag] = run_source(mode, tag, lambda tag=tag: run_p1_pass(tag, p1_roots[tag], mode, scratch, vm, mw))
        r2 = g["g2"]
        store["P2"] = run_source(mode, "P2", lambda: run_holdout_pass("P2", bc.MultiViewHours(dict(r2["roots"])), r2["pool"], mode, scratch, args.vmap_p2, mw))
        store["P3"] = run_source(mode, "P3", lambda: run_holdout_pass("P3", make_p3_hours(g["g3"]["walkers"]), bc.hours_range(*BLOCKS["P3"]), mode, scratch, args.vmap_p3, mw))
        if g["g4"] is not None:
            r4 = g["g4"]
            store["P4"] = run_source(mode, "P4", lambda: run_holdout_pass("P4", bc.MultiViewHours(dict(r4["roots"])), r4["pool"], mode, scratch, args.vmap_p4, mw))
    return v_rows, nv_rows, no_v_pools


def unpriceable_mints(mint_pools: Mapping[str, set[str]], vmap: Mapping[str, int | None]) -> set[str]:
    """Pool-based and outcome-blind: a mint is unpriceable if any PumpSwap pool it printed on in the counted window has no V in the pinned map
    (absent, or present with null). An explicit V of 0 is a real V and is priceable."""
    return {m for m, ps in mint_pools.items() if any(vmap.get(p) is None for p in ps)}


def remove_unpriceable(universe: Sequence[Mapping[str, Any]], unpriceable: set[str]) -> tuple[list[Mapping[str, Any]], dict[str, Any], list[Mapping[str, Any]]]:
    """Remove the unpriceable mints from the universe for every config and the frozen side alike. Refuses (Refused) if more than 0.5% of the
    universe's mints, or more than 0.5% of its non-P1 rows, would go. Returns (kept universe, record for the report, the removed rows)."""
    removed_rows = [u for u in universe if u["mint"] in unpriceable]
    removed = sorted(u["mint"] for u in removed_rows)
    frac = (len(removed) / len(universe)) if universe else 0.0
    non_p1 = [u for u in universe if u["block"] != "P1"]
    n_non_removed = sum(1 for u in removed_rows if u["block"] != "P1")
    frac_non = (n_non_removed / len(non_p1)) if non_p1 else 0.0
    per_block = {}
    for blk in ("P1", "P2", "P3", "P4"):
        tot = sum(1 for u in universe if u["block"] == blk)
        if tot:
            n = sum(1 for u in removed_rows if u["block"] == blk)
            per_block[blk] = {"removed": n, "rows": tot, "fraction": n / tot}
    rec = {"rule": "pool-based, outcome-blind: a mint on a PumpSwap pool with no V (null or absent) in the pinned map is removed for all configs and the frozen side", "count": len(removed), "fraction": frac,
           "non_p1_count": n_non_removed, "non_p1_fraction": frac_non, "per_block": per_block, "max_fraction": UNPRICEABLE_MAX_FRACTION, "n_universe_before": len(universe), "mints": removed,
           "bias_statement": BIAS_STATEMENT}
    if frac > UNPRICEABLE_MAX_FRACTION or frac_non > UNPRICEABLE_MAX_FRACTION:
        raise Refused(f"{len(removed)} of {len(universe)} universe mints ({frac:.3%}; non-P1 {n_non_removed} of {len(non_p1)} = {frac_non:.3%}) sit on pools with no V in the pinned map; the limit is {UNPRICEABLE_MAX_FRACTION:.1%} "
                      f"overall and on the non-P1 rows. Extend the map first; no tries line was written")
    gone = set(removed)
    return [u for u in universe if u["mint"] not in gone], rec, removed_rows


def check_no_v_consistency(no_v_pools: set[str], mint_pools: Mapping[str, set[str]], unpriceable: set[str], universe_mints: set[str] | None = None) -> dict[str, Any]:
    """Every pool the adapter priced without V must be a pool of some scanned unpriceable mint, else the run refuses before `started`. Why the
    relaxed rule (any scanned unpriceable mint, not only a removed one) is sound: the adapter and the pre-pass attribute a pool to a mint by the same
    row-level pool and mint fields, and the pre-pass hours include the adapter's hours. So a pool the adapter saw without V was seen by the pre-pass on
    some mint; if that mint is not in the universe (buffer or edge hours, a dropped migration) no universe row touches the pool. Returns the counts
    for the report, including how many pools were accepted ONLY because of mints outside the universe."""
    explained = {p for m in unpriceable for p in mint_pools.get(m, ())}
    stray = sorted(p for p in no_v_pools if p not in explained)
    if stray:
        raise Refused(f"{len(stray)} pool(s) the adapter priced without V are on no unpriceable mint the pre-pass scanned (e.g. {stray[:3]}); the removal rule would miss them. Refusing before `started`")
    in_universe = unpriceable if universe_mints is None else (unpriceable & universe_mints)
    by_removed = {p for m in in_universe for p in mint_pools.get(m, ())}
    only_outside = sorted(p for p in no_v_pools if p not in by_removed)
    return {"n_adapter_no_v_pools": len(no_v_pools), "n_accepted_only_because_of_mints_outside_the_universe": len(only_outside), "pools_accepted_only_outside_the_universe": only_outside[:50]}


def load_pinned_vmap(path: str | Path) -> dict[str, int | None]:
    """The map is re-checked against the pin every time it is loaded."""
    from tools.pumpswap_virtual import load_map

    check_vmap(path, VMAP_0909_SHA256, "V map")
    return load_map(Path(path))


def no_v_mints_from(no_v_pools: set[str], mint_pools: Mapping[str, set[str]]) -> set[str]:
    return {m for m, ps in mint_pools.items() if ps & set(no_v_pools)}


def check_run_lock(out_dir: Path) -> None:
    """A RUN.lock with no record means a run was hard-killed after `started` (tries spent, state unknown): refuse. A lock whose record says
    the tries were never started is stale and is cleared."""
    lock, rec = out_dir / OUT_LOCK, out_dir / OUT_RECORD
    if not lock.exists():
        return
    if not rec.exists():
        raise Refused(f"{lock} exists with no completed/aborted/refused record in {out_dir}: a run was killed after taking the lock. Needs a manager ruling; no rerun")
    try:
        started = bool(json.loads(rec.read_text(encoding="utf-8")).get("started"))
    except (OSError, ValueError):
        started = True
    if started:
        raise Refused(f"{lock}: a run already spent its tries here (see {rec})")
    lock.unlink()
    rec.unlink()


def take_lock(out_dir: Path, head: str, ahash: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(out_dir / OUT_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"head": head, "args_hash": ahash, "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n")


def write_record(out_dir: Path, status: str, started: bool, statuses: Mapping[str, str]) -> None:
    tmp = out_dir / (OUT_RECORD + ".tmp")
    tmp.write_text(json.dumps({"status": status, "started": started, "config_statuses": dict(statuses), "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n", encoding="utf-8")
    os.replace(tmp, out_dir / OUT_RECORD)


def write_report(out_dir: Path, rep: Mapping[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / (OUT_REPORT + ".tmp")
    tmp.write_text(json.dumps(rep, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, out_dir / OUT_REPORT)
    (out_dir / OUT_MD).write_text(render_md(rep), encoding="utf-8")


def make_report(base: Mapping[str, Any], screen: Mapping[str, Any], with_p4: bool) -> dict[str, Any]:
    rep = dict(base)
    rep["configs"] = dict(screen["results"])
    rep["statuses"] = dict(screen["statuses"])
    rep["decision"] = decide_outcome(screen["results"], screen["statuses"])
    rep["matched_outcome"] = rep["decision"]["outcome"]
    rep["fast_only_required_line"] = FAST_ONLY_LINE
    return rep


def main(argv: Sequence[str] | None = None) -> int:
    from tools.exp012_exit_sensitivity import resolve_tries_path

    args = _parser().parse_args(argv)
    tries_path = resolve_tries_path(args.tries_log)
    canonical = Path(args.canonical_tries).resolve()
    out_dir = args.out_dir
    try:
        g = run_guards(args)
        check_no_prior_tries(tries_path, canonical)
        check_run_lock(out_dir)
        gs = git_state()
        if gs["dirty_tools"]:
            raise Refused("tools/ is dirty (uncommitted change): the run records one clean head and refuses otherwise")
        if args.guards_only:
            vprepass_all(g, args, only=("P1A", "P1C", "P1B", "P3"))  # the pinned map's coverage of P1 and P3 pools (pool fields only), printed for the manager
            print("guards OK (no outcome row was read; pool-field coverage of P1 and P3 above)", file=sys.stderr)
            return 0
        if not args.guards_only:
            check_adapter_hours_subset(g, args.max_workers)  # before any read and before `started`
        covs, mint_pools = vprepass_all(g, args)
    except Refused as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    head = gs["head"]
    ahash = args_hash(args)
    with_p4 = g["with_p4"]
    scratch = out_dir / "scratch"
    t0 = time.time()
    status = "aborted_after_read"
    locked = started = False
    universe_sha: str | None = None
    done_cfgs: set[str] = set()
    cfg_status: dict[str, str] = {}

    def _on_sigterm(signum: int, frame: Any) -> None:  # a MiScusi cancel: unwind through the finally below
        raise SystemExit(128 + signum)

    prev = signal.signal(signal.SIGTERM, _on_sigterm)
    base: dict[str, Any] = {}
    try:
        v_rows, nv_rows, no_v_pools = collect_all(g, args, scratch, head)
        try:
            universe, ustats = build_universe(v_rows, nv_rows, with_p4)
        except Integrity as exc:  # before `started`: nothing is logged
            print(f"refusing after read (before started, no tries line): {exc}", file=sys.stderr)
            return 2
        try:  # the unpriceable-pool rule: decided BEFORE `started` and before any fit
            unpr = unpriceable_mints(mint_pools, load_pinned_vmap(args.vmap_p1))
            consistency = check_no_v_consistency(no_v_pools, mint_pools, unpr, {u["mint"] for u in universe})
            universe, unpriceable_rec, removed_rows = remove_unpriceable(universe, unpr)
            unpriceable_rec["adapter_no_v_pools"] = consistency
        except Refused as exc:
            print(f"refusing (before started, no tries line): {exc}", file=sys.stderr)
            return 2
        no_v = no_v_mints_from(no_v_pools, mint_pools)
        frozen_sel = frozen_selection(universe, args.artifact_dir)
        frozen_removed_sel = frozen_selection(removed_rows, args.artifact_dir) if removed_rows else []  # features and frozen scores only; before `started`
        bad = [u["mint"] for u, f in zip(universe, frozen_sel) if f and u["block"] != "P1" and u["mint"] in no_v]
        if bad:  # the frozen selection on the non-P1 rows is priced without V on a no-V pool: refuse before `started`
            print(f"refusing (before started, no tries line): {len(bad)} frozen-selected non-P1 migration(s) are on pools priced without V", file=sys.stderr)
            return 2
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / OUT_UNIVERSE).write_bytes(universe_bytes(universe))
        universe_sha = universe_sha256(universe)
        (out_dir / OUT_UNIVERSE_SHA).write_text(universe_sha + "\n", encoding="utf-8")
        try:
            take_lock(out_dir, head, ahash)
        except FileExistsError:
            print(f"refusing: {out_dir / OUT_LOCK} appeared (concurrent run)", file=sys.stderr)
            return 2
        locked = True
        log_all(out_dir, tries_path, canonical, list(CONFIGS), "started", with_p4, universe_sha)  # the spend point, BEFORE any fit
        started = True
        base = {
            "schema": SCHEMA, "banner": BANNER, "first_line": first_line(with_p4), "with_p4": with_p4, "n_dates": len(pool_dates(with_p4)), "n_non_p1_dates": len(non_p1_dates(with_p4)), "dates": pool_dates(with_p4),
            "universe": {"n": len(universe), "sha256": universe_sha, "stats": ustats, "path": str(out_dir / OUT_UNIVERSE), "unpriceable_removed": unpriceable_rec},
            "costs": {"vmap_0909_pinned": VMAP_0909_SHA256, "vmap_sha256": g["vmap_sha256"], "k": K, "size_sol": SIZE_SOL, "fee_lamports": FEE, "exit_lag": EXIT_LAG, "haircut_factor": HAIRCUT_FACTOR},
            "guards": {"view_sha256": {"P1": g["g1"]["view_sha256"], "P2": g["g2"]["view_sha256"], "P3_manifests": g["g3"]["pin_sha256"], "P4": (g["g4"] or {}).get("view_sha256")}, "frozen": g["frozen"]},
            "v_prepass": covs, "no_v_mints_in_universe": len(no_v & {u["mint"] for u in universe}), "caveats": list(CAVEATS), "max_workers": args.max_workers, "git_head": head, "git_dirty_tools": False, "args_hash": ahash,
        }
        lab = labels(universe)
        dates = pool_dates(with_p4)
        arrays = fit_arrays(universe, lab, dates, removed_rows)
        runner = Runner(arrays, out_dir / "fit_arrays.npz", args.max_workers)
        partial_screen: dict[str, Any] = {"results": {}, "statuses": {}}

        def on_done(cid: str, res: dict[str, Any]) -> None:
            partial_screen["results"][cid] = res
            partial_screen["statuses"][cid] = "completed"
            rep = make_report(base, partial_screen, with_p4)
            rep["partial"] = True
            write_report(out_dir, rep)
            log_all(out_dir, tries_path, canonical, [cid], "completed", with_p4, universe_sha)
            done_cfgs.add(cid)
            cfg_status[cid] = "completed"

        def on_refused(cid: str, why: str) -> None:
            partial_screen["results"][cid] = {"passes": False, "refused": why}
            partial_screen["statuses"][cid] = "refused_after_read"
            log_all(out_dir, tries_path, canonical, [cid], "refused_after_read", with_p4, universe_sha)
            done_cfgs.add(cid)
            cfg_status[cid] = "refused_after_read"

        try:
            screen = run_screen(universe, frozen_sel, runner, with_p4, on_done, on_refused, no_v, removed=removed_rows, frozen_removed_sel=frozen_removed_sel)
        finally:
            runner.close()
        rep = make_report(base, screen, with_p4)
        rep["wall_s"] = time.time() - t0
        rep["partial"] = False
        write_report(out_dir, rep)
        status = "completed"
        print(render_md(rep))
        return 0
    finally:
        signal.signal(signal.SIGTERM, prev)
        if started and status != "completed":
            pending = [c for c in CONFIGS if c not in done_cfgs]
            log_all(out_dir, tries_path, canonical, pending, status, with_p4, universe_sha)
            for c in pending:
                cfg_status[c] = status
        if locked:
            write_record(out_dir, status, started, cfg_status)
        # before `started`, nothing is logged: the tape rows stay cached (checked on a re-run) and no try is spent


if __name__ == "__main__":
    raise SystemExit(main())
