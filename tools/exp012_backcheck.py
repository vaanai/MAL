#!/usr/bin/env python3
"""EXP-012 back-check on the 14-day exploration expansion explore-0814. **EXPLORATION, NOT A PROMOTE, NOT GATE
EVIDENCE.** Pre-declaration: EXP/EXP-012-backcheck-0814.md.

Question: the frozen EXP-012 entry model (threshold 0.8031, never refit) was frozen on a 9-day pool. What does the
frozen strategy earn, at the LIVE operating point (V-priced, k = 6 slots counted from the first PumpSwap print, 0.05 SOL, per-side fee
505,000 lamports, tp50_sl30, 30-minute exit cap), on 14 days of tape the model never trained on? NOT unread:
explore-0814/w1 [2026-08-26T12, 2026-08-28T12) was outcome-read by DEC-017 candidate (a). Exploration evidence only; it informs the live probe's size step (DEC-020), nothing else.

Data: the seven clean views /data/mal/clean-view/explore-0814/w1..w7 (pool [2026-08-14T12, 2026-08-28T12), 336 h).
The first 24 h of the pool (to 2026-08-15T12) are FEATURE BUFFER ONLY: migrations before 2026-08-15T12 are scored but
not counted, so creator_prior_mints_24h is complete for every counted mint. The table is built by the frozen code path
(tools.exp012_score.load_rows, anchored chunk plan, creator history, BUFFER_HOURS = 24, MAX_HOME_HOURS = 12). Every
migration is scored with the FROZEN model (ARTIFACTS/exp012/model.txt, tools.exp011_score.score_rows), not OOF.

PRE-DECLARED CELLS (6; constants CELLS in this module; the labels below are asserted equal to the code by a test).
All at the frozen threshold, fee 505000, exit tp50_sl30. Fail models: flat 15% and pressure (slope scale 1).
  PRIMARY (Amendment 1, 2026-10-06, before any outcome was computed: moved to exit lag 2, the realistic exit of the #329/#339
  exit re-check, job #180; still optimistic versus the measured live exit leak)
    thr 0.8031 k=6 size=0.05 fee=505000 exit_lag=2
  SENSITIVITY (report-only, never selected among, none can change the primary)
    thr 0.8031 k=6 size=0.05 fee=505000 exit_lag=0
    thr 0.8031 k=6 size=0.25 fee=505000 exit_lag=2
    thr 0.8031 k=6 size=0.5 fee=505000 exit_lag=2
    thr 0.8031 k=4 size=0.05 fee=505000 exit_lag=2
    thr 0.8031 k=8 size=0.05 fee=505000 exit_lag=2
The lag-0 cell is labelled "optimistic exit (upper bound)"; it is the only cell reported at lag 0. exit_lag uses
exploration_exits' exit_land_k (slots the sell lands after the trigger; score_one exposes it).
Sizes above 0.05 are mechanical (fee arithmetic + modelled AMM impact) and cannot support any live size (DEC-020 section 1).

Simulation is tools.exp012_operating_point's, by import (multi_cell_patch with explicit combos): V pricing through the
PumpSwap virtual-reserve adapter, latency_curve buy at k slots with its 15% cap (a MISS pays the per-side fee), the
frozen tp50_sl30 exit, 30-minute exit cap. The deciding read nets the measured live costs out of every filled trade (sell
shortfall 16 bps of proceeds, sim-vs-live entry gap 26.08 bps in tokens received; haircut_delta); the raw result is
report-only (cells_raw). The pre-declared outcome rules (read_outcome) are computed and written into the report. CIs: the gate's cluster bootstrap (tools.paper_attention_promote.book_stats,
1,000 draws, seed 1; 5th-95th percentile of the mean). Sharp-drop rate (report-only; NOT a rug rate, it measures post-migration volatility) is the #336 label (one-step drop >= 30%
vs the previous print, or a print < 0.5x entry before 1.5x, within 15 min), imported from tools.exp012_rug_risk.

Refuses (exit 2, before reading any row): a view under a holdout/reserved root (fresh-0903, fresh-0828, fresh-0808,
forward-1002, /data/mal/blocks*, the EXP-011 spent walkers), a view with no VIEW.sha256 or a hash mismatch, a view with
any hour outside [2026-08-14T12, 2026-08-28T12) (that also excludes the EXP-011 block and 2026-10-02 onwards), overlapping
views, or a gap in the pool.

Run (mal-research-0; at most 2 workers):
  nice -n 19 /data/mal/venv/bin/python -m tools.exp012_backcheck \
    --view-dir /data/mal/clean-view/explore-0814/w1 ... --view-dir /data/mal/clean-view/explore-0814/w7 \
    --out-dir /data/mal/exp012-backcheck-0814 --tries-log /data/mal/ops/tries-exp012-backcheck.jsonl
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import tools.exp011_freeze as fz
import tools.exp011_score as e11
import tools.exploration_entry_model as eem
import tools.exp012_forward as ff12
import tools.exp012_operating_point as op
import tools.exp012_score as s12
from tools.exp012_exit_sensitivity import side
from tools.exp012_latency_sensitivity import DEFAULT_ARTIFACT_DIR, _forbidden_hit
from tools.exp012_latency_virtual import DEFAULT_VMAP, set_env
from tools.exp012_rug_risk import K as RUG_K, rug_label  # the #336 label and its entry k (6)
from tools.latency_curve import MISS, _hour_file

TOOL = "tools.exp012_backcheck"
POOL_NAME = "explore-0814"
POOL_START = "2026-08-14T12"
POOL_END = "2026-08-28T12"  # exclusive
COUNT_START = "2026-08-15T12"  # first 24 h of the pool are feature buffer only
WEEK_SPLIT = "2026-08-22T12"  # first 7 counted days | last 6
HOUR_FMT = "%Y-%m-%dT%H"
FROZEN_THRESHOLD = op.FROZEN_THRESHOLD
PRIMARY_FEE = op.PRIMARY_FEE  # 505,000 = 500,000 priority + 5,000 base per side (DEC-019 Am.1), the operating-point convention
LAMPORTS = 1_000_000_000
MODEL_MD5 = "a1810d219ed61db64a396f40dc302ce5"  # ARTIFACTS/exp012/train_manifest.json
EXISTING_TRIES_ON_POOL = 1  # DEC-017 candidate (a) read explore-0814/w1
W1_START = "2026-08-26T12"  # w1 = [2026-08-26T12, 2026-08-28T12): outcome-read by DEC-017 candidate (a)
BANNER = (
    "EXPLORATION, best-of-N context, not a promote, not gate evidence. Frozen EXP-012 (model md5 a1810d21\u2026, thr 0.8031) on explore-0814 "
    "[2026-08-14T12, 2026-08-28T12), counted from 2026-08-15T12. Model never trained on these days. Not unread: w1 days were outcome-read by "
    "DEC-017 candidate (a), and EXP-013/EXP-014 August bars are no longer on unread data after this run. Cumulative tries on explore-0814: 7. "
    "k counts from the first PumpSwap print, not migration (live a25eb17 k(migrate) = 5\u20136). Primary exit lag 2 slots; still optimistic versus "
    "the measured live exit leak (a25eb17 stops \u22120.3039 to \u22120.4338); the lag-0 cell is an upper bound. Outcome rules are read net of the "
    "measured sell shortfall (\u221216 bps) and sim-vs-live entry gap (+26.08 bps, job #175); MEV is not added. Sizes above 0.05 SOL add only fee "
    "arithmetic and modelled AMM impact; they cannot support any live size (DEC-020 \u00a71). Context only for DEC-020; Option A remains the "
    "recommendation. The only evidence for EXP-012 is the 10-16 forward read and the promotion gate on a fresh holdout."
)
SELL_SHORTFALL_BPS = 16.0  # worst of a25eb17's -11..-16 bps, taken on the sell proceeds
ENTRY_GAP_BPS = 26.08  # mean sim-vs-live entry gap, job #175: the sim got more tokens than live; a cut in tokens received
SIZE_NOTE = "mechanical (fee arithmetic + modelled AMM impact), not evidence; cannot support any live size (DEC-020 §1)"
LAG_NOTE = "optimistic exit (upper bound)"
V_MAX_MISSING_FRACTION = 0.01
VMAP_SHA256 = "2506f7d2d8475e44ca70a8c536dbb7405930b1092edca331dbbe611236b4d2f8"  # pool_v_0814.json (job #196: 34,945 pools, 1 null of the 16,346 August pools)
DEFAULT_VMAP_0814 = "/data/mal/pumpswap-virtual/pool_v_0814.json"
DAY_DEFINITION = "counted day = 24 h window from 12:00Z, not the gate's UTC day; bar 2 is gate-shaped, not the gate"
REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_TRIES = REPO_ROOT / "data" / "tries.jsonl"  # absolute: resolved from this file, never from the cwd
MARKER = "tries_logged.marker"
RESULT_NAME = "report.json"
ROWS_NAME = "backcheck_rows.jsonl"
ENV_ARTIFACT = "MAL_BACKCHECK_ARTIFACT"
V_INCOMPLETE_FRACTION = 0.01

# Extra holdout / reserved name fragments refused anywhere in a resolved view path (beyond op's guarded prefixes).
FORBIDDEN_NAME_FRAGMENTS = ("fresh-0903", "fresh-0828", "fresh-0808", "forward-1002")


def _c(k: int, size_sol: float, exit_lag: int = 0) -> dict[str, Any]:
    return {"threshold": FROZEN_THRESHOLD, "k": k, "size_sol": size_sol, "fee": PRIMARY_FEE, "exit_lag": exit_lag}


PRIMARY = _c(6, 0.05, 2)
SENSITIVITY = (_c(6, 0.05, 0), _c(6, 0.25, 2), _c(6, 0.5, 2), _c(4, 0.05, 2), _c(8, 0.05, 2))
CELLS = (PRIMARY, *SENSITIVITY)


def cell_label(c: Mapping[str, Any]) -> str:
    return f"thr {c['threshold']:.4f} k={c['k']} size={c['size_sol']} fee={c['fee']} exit_lag={c['exit_lag']}"


def cell_id(c: Mapping[str, Any]) -> str:
    return f"t{c['threshold']:.4f}_k{c['k']}_s{c['size_sol']}_f{c['fee']}_l{c['exit_lag']}"


def cell_role(c: Mapping[str, Any]) -> str:
    return "primary" if c == PRIMARY else "sensitivity"


COMBOS = tuple((c["k"], c["size_sol"], c["exit_lag"]) for c in CELLS)


class Refused(Exception):
    code = 2


# --- time helpers ----------------------------------------------------------------------------------------------


def hour_dt(key: str) -> datetime:
    return datetime.strptime(key, HOUR_FMT).replace(tzinfo=timezone.utc)


def hour_ms(key: str) -> int:
    return int(hour_dt(key).timestamp() * 1000)


def hours_range(start: str, end: str) -> list[str]:
    out, cur, stop = [], hour_dt(start), hour_dt(end)
    while cur < stop:
        out.append(cur.strftime(HOUR_FMT))
        cur += timedelta(hours=1)
    return out


# --- guards (run before any row is read) -----------------------------------------------------------------------


def _view_hours(root: Path) -> set[str]:
    """Hour keys named by the view's trades/ and creates/ file names (a directory listing, no row read)."""
    hours: set[str] = set()
    for sub in ("trades", "creates"):
        d = root / sub
        if not d.is_dir():
            continue
        for name in os.listdir(d):
            stem = name.split(".")[0]
            if "-" in stem:
                key = stem.split("-", 1)[1]
                if len(key) == 13:
                    hours.add(key)
    return hours


def guard_views(view_dirs: Sequence[Path | str], pool_start: str = POOL_START, pool_end: str = POOL_END, verify: bool = True) -> dict[str, Any]:
    """Refuse (Refused, exit 2) before any row is read. Returns {"roots": {hour: root}, "pool": [hours], "view_sha256": {...}}."""
    if not view_dirs:
        raise Refused("at least one --view-dir is required")
    pool = hours_range(pool_start, pool_end)
    pool_set = set(pool)
    roots: dict[str, str] = {}
    shas: dict[str, str] = {}
    reals: list[Path] = []
    for v in view_dirs:
        rp = os.path.realpath(str(v))
        bad = _forbidden_hit(rp)
        if bad:
            raise Refused(f"view {str(v)!r} resolves to {rp!r}, inside a reserved holdout location ({bad})")
        for frag in FORBIDDEN_NAME_FRAGMENTS:
            if frag in rp:
                raise Refused(f"view {str(v)!r} resolves to {rp!r}, a reserved holdout view ({frag})")
        reals.append(Path(rp))
    for root in reals:
        if not (root / "VIEW.sha256").is_file():
            raise Refused(f"{root}: VIEW.sha256 not found")
        hours = _view_hours(root)
        if not hours:
            raise Refused(f"{root}: no trades/creates hour files")
        outside = sorted(hours - pool_set)
        if outside:
            raise Refused(f"{root}: hour {outside[0]} is outside the exploration pool [{pool_start}, {pool_end}) ({len(outside)} hours); holdout, EXP-011 and 2026-10-02+ hours are refused")
        for h in hours:
            if h in roots:
                raise Refused(f"overlap: hour {h} is in both {roots[h]} and {root}")
            roots[h] = str(root)
    missing = [h for h in pool if h not in roots]
    if missing:
        raise Refused(f"gap: {len(missing)} pool hours are in no view (first {missing[0]})")
    if verify:
        for root in reals:
            try:
                fz.verify_view_sha256(root)
            except SystemExit as exc:  # the freeze's verifier raises SystemExit with the message
                raise Refused(str(exc)) from None
            shas[str(root)] = hashlib.sha256((root / "VIEW.sha256").read_bytes()).hexdigest()
    return {"roots": roots, "pool": pool, "view_sha256": shas}


def check_frozen_threshold(threshold: float) -> None:
    if abs(threshold - FROZEN_THRESHOLD) > 1e-15 or round(threshold, 4) != 0.8031:
        raise Refused(f"artifact threshold {threshold!r} != the frozen {FROZEN_THRESHOLD!r} (0.8031)")


# --- hours resolver over several views ----------------------------------------------------------------------------


@dataclass(frozen=True)
class MultiViewHours:
    """Picklable hour resolver: each pool hour resolves under the view that holds it."""

    root_by_hour: dict[str, str]

    def __call__(self, key: str) -> dict[str, Any]:
        assert key in self.root_by_hour, f"hour {key!r} is outside the explore-0814 views"
        root = Path(self.root_by_hour[key])
        trade = _hour_file(root / "trades", "trades", key)
        if trade is None:
            raise SystemExit(f"missing trades file for hour {key} under {root}")
        create = _hour_file(root / "creates", "creates", key)
        return {"hour": key, "day": key[:10], "end": int(hour_dt(key).timestamp()) + 3600, "trade": trade, "create": create}


# --- tape pass (worker side) ---------------------------------------------------------------------------------------------


_MODEL_CACHE: dict[str, Any] = {}


def _frozen() -> tuple[Any, float, list[str]]:
    if not _MODEL_CACHE:
        _MODEL_CACHE["x"] = e11.load_frozen_spec(Path(os.environ[ENV_ARTIFACT]))
    return _MODEL_CACHE["x"]


@contextlib.contextmanager
def backcheck_patch(model: Any, threshold: float, names: Sequence[str]) -> Iterator[None]:
    """Wrap eem.score_one. Per migration: (1) the frozen k=1 row gives the features; the FROZEN model scores them;
    (2) below the threshold a light marker row is emitted (counted, not simulated); (3) at or above it, the
    operating point's multi_cell_patch simulates every pre-declared combo, each row tagged with mig_ms, the exit
    class (adapter exit_capture) and, for k=6, the rug label. Restores everything on exit."""
    from tools import pumpswap_virtual_adapter as ad

    scores: dict[str, float] = {}
    with ad.exit_capture():
        base = eem.score_one
        with op.multi_cell_patch(scores, threshold, combos=COMBOS):
            inner = eem.score_one

            def backcheck(mint_id: str, mint: Any, feat: Any, curve: Any, through_ms: int, creator_hist: Any, **kw: Any) -> list[dict[str, Any]]:
                if any(v is not None for v in kw.values()):
                    raise RuntimeError(f"score_one was called with arguments this pass does not model: {sorted(kw)}")
                frozen_rows = [r for r in base(mint_id, mint, feat, curve, through_ms, creator_hist) if r["spec"] == op.TARGET_SPEC_ID]
                if not frozen_rows:
                    return []
                row = frozen_rows[0]
                e11.score_rows(model, [row], names)
                sc = float(row["score"])
                mig_ms = int(mint.mig_ms)
                if sc < threshold:
                    return [{"mint": mint_id, "spec": op.TARGET_SPEC_ID, "day": row["day"], "score": sc, "mig_ms": mig_ms, "unselected": True}]
                scores[mint_id] = sc
                rows = inner(mint_id, mint, feat, curve, through_ms, creator_hist)
                fills, trig_slot, trig_ms, _ref = eem._fills_for(mint, migrate=True)
                target = trig_slot + RUG_K
                idx = eem._state_index(fills, target, eem.ENTRY_BOUND)
                fallback = fills[idx].t_recv_ms if idx >= 0 else trig_ms
                lab = rug_label(fills, idx, eem._slot_time(fills, target, fallback))
                for r in rows:
                    r["mig_ms"], r["rug"] = mig_ms, lab
                scores.pop(mint_id, None)
                return rows

            eem.score_one = backcheck
            try:
                yield
            finally:
                eem.score_one = inner


def _bc_worker(worker_id: int, home: list[str], buf: list[str], creator_hist: dict[str, list[int]], rows_out_path: Path | None, hours: Any) -> list[dict[str, Any]]:
    """Module level (spawn-picklable). `exp012_score._run_worker` under the V adapter and the back-check patch."""
    from tools import pumpswap_virtual_adapter as ad

    model, thr, names = _frozen()
    with backcheck_patch(model, thr, names):
        return ad.holdout_worker(worker_id, home, buf, creator_hist, rows_out_path, hours)


def collect_rows(roots: Mapping[str, str], pool: Sequence[str], artifact_dir: Path, vmap: str, scratch: Path, max_workers: int = 2) -> list[dict[str, Any]]:
    if max_workers > 2:
        raise Refused("keep --max-workers <= 2")
    set_env(vmap, scratch / "counts_virtual")
    os.environ[ENV_ARTIFACT] = str(artifact_dir)
    hours = MultiViewHours(dict(roots))
    plan = ff12.anchored_plan(pool, s12.MAX_HOME_HOURS, s12.BUFFER_HOURS)
    return s12.load_rows(hours, max_workers, s12.BUFFER_HOURS, s12.MAX_HOME_HOURS, scratch, pool_hours=list(pool), worker_fn=_bc_worker, plan=plan)


def vmap_coverage(prints: Any, vmap: Mapping[str, int | None], count_ms: int, end_ms: int | None = None, migrated: Any = None, return_mint_pools: bool = False) -> Any:
    """Pool-field-only coverage over the PumpSwap prints of mints that MIGRATE in [count_ms, end_ms): the mint's first PumpSwap
    print is in the window, the mint is not the wSOL mint (a wSOL pool is never a scored migration), and, when `migrated` is
    given, the mint is in the views' migrations/ set. Prints of mints that migrated before the window, of non-migrating
    pools and of wSOL pools are not read into the population. `prints` yields trade-row dicts (non-PumpSwap rows ignored).
    A pool is COVERED iff it is a key of the map with a non-null value (an explicit 0 is a real V = 0 and is covered).
    A pool absent from the map, or present with null (account missing), is MISSING; never V-less, never V = 0."""
    first: dict[str, int] = {}
    counts: dict[str, dict[Any, int]] = {}
    for row in prints:
        if row.get("venue") != "pumpswap":
            continue
        mint = row.get("mint")
        t = row.get("t_recv_ms")
        if t is None and isinstance(row.get("block_time"), int):
            t = row["block_time"] * 1000
        if mint is None or t is None or mint == eem.WSOL:
            continue
        t = int(t)
        first[mint] = t if mint not in first else min(first[mint], t)
        pool = row.get("pool") if isinstance(row.get("pool"), str) else None
        per = counts.setdefault(mint, {})
        per[pool] = per.get(pool, 0) + 1
    n_prints = covered = 0
    missing_pools: set[Any] = set()
    n_mints = 0
    mint_pools: dict[str, set[str]] = {}
    for mint, t in first.items():
        if t < count_ms or (end_ms is not None and t >= end_ms):
            continue
        if migrated is not None and mint not in migrated:
            continue
        n_mints += 1
        mint_pools[mint] = {p for p in counts[mint] if p is not None}
        for pool, n in counts[mint].items():
            n_prints += n
            if pool is not None and vmap.get(pool) is not None:
                covered += n
            else:
                missing_pools.add(pool)
    missing = n_prints - covered
    cov = {
        "population": "PumpSwap prints of mints migrating in [counted_from, pool end): first print in window, not wSOL, in the views' migrations/ set",
        "n_counted_mints": n_mints,
        "prints": n_prints,
        "covered": covered,
        "missing": missing,
        "missing_fraction": (missing / n_prints) if n_prints else None,
        "missing_pools": len(missing_pools),
        "missing_pool_examples": sorted(str(x) for x in missing_pools)[:5],
        "max_missing_fraction": V_MAX_MISSING_FRACTION,
    }
    return (cov, mint_pools) if return_mint_pools else cov


def check_v_coverage(cov: Mapping[str, Any]) -> None:
    if not cov["prints"]:
        raise Refused("V coverage: no PumpSwap prints for the counted migrated mints")
    if cov["missing_fraction"] > V_MAX_MISSING_FRACTION:
        raise Refused(f"V coverage: {cov['missing']} of {cov['prints']} PumpSwap prints ({cov['missing_fraction']:.2%}, {cov['missing_pools']} pools) have no V in the map; limit {V_MAX_MISSING_FRACTION:.0%}. Extend the map first; no P&L was computed")


def coverage_line(cov: Mapping[str, Any]) -> str:
    return f"V coverage (pre-pass, counted window): prints={cov['prints']} covered={cov['covered']} missing={cov['missing']} missing_pools={cov['missing_pools']} (limit {cov['max_missing_fraction']:.0%})"


def _iter_pool_prints(hours: Any, pool: Sequence[str]) -> Iterator[dict[str, Any]]:
    from tools.exploration_entry_model import _iter_trades

    for h in pool:
        for row in _iter_trades(hours(h)["trade"]):
            if row.get("venue") == "pumpswap":
                yield {"venue": "pumpswap", "mint": row.get("mint"), "pool": row.get("pool"), "t_recv_ms": row.get("t_recv_ms"), "block_time": row.get("block_time")}


def migrated_mints(roots: Sequence[str]) -> set[str]:
    """The views' migrations/ set (same reader as tools.exp012_virtual_rescore, the V-map precedent)."""
    from tools.exp012_virtual_rescore import _migrated_mints

    out: set[str] = set()
    for r in roots:
        out |= _migrated_mints(Path(r), False)
    return out


def v_prepass(roots: Mapping[str, str], pool: Sequence[str], vmap_path: str | Path, count_start: str = COUNT_START, pool_end: str = POOL_END) -> tuple[dict[str, Any], dict[str, set[str]]]:
    from tools.pumpswap_virtual import load_map

    mig = migrated_mints(sorted(set(roots.values())))
    return vmap_coverage(_iter_pool_prints(MultiViewHours(dict(roots)), pool), load_map(Path(vmap_path)), hour_ms(count_start), hour_ms(pool_end), mig, return_mint_pools=True)


def check_vmap_sha(path: str | Path) -> str:
    got = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if got != VMAP_SHA256:
        raise Refused(f"--vmap {path}: sha256 {got} != the pinned {VMAP_SHA256} (pool_v_0814.json)")
    return got


def primary_no_v_trades(rows: Sequence[Mapping[str, Any]], mint_pools: Mapping[str, set[str]], no_v_pools: set[str], c: Mapping[str, Any] = PRIMARY) -> int:
    """Primary-cell trades whose pool is one the adapter priced without V."""
    trades, _cen, _bm = cell_trades(counted(rows), c)
    return sum(1 for t in trades if mint_pools.get(t["mint"], set()) & no_v_pools)


def adapter_no_v_pools(counts_dir: Path) -> set[str]:
    out: set[str] = set()
    if counts_dir.is_dir():
        for p in sorted(counts_dir.glob("counts-*.json")):
            out.update(json.loads(p.read_text(encoding="utf-8")).get("no_v_pools", []))
    return out


def check_model_md5(artifact_dir: Path) -> str:
    got = fz._md5_of_file(artifact_dir / "model.txt")
    if got != MODEL_MD5:
        raise Refused(f"model.txt md5 {got} != the frozen {MODEL_MD5} (ARTIFACTS/exp012/train_manifest.json)")
    return got


def git_head() -> str:
    import subprocess

    try:
        return subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def v_coverage(counts_dir: Path) -> dict[str, Any]:
    tot = {"pumpswap_prints": 0, "corrected": 0, "no_v": 0}
    pools: set[str] = set()
    if counts_dir.is_dir():
        for p in sorted(counts_dir.glob("counts-*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            for k in tot:
                tot[k] += int(d.get(k, 0))
            pools.update(d.get("no_v_pools", []))
    frac = tot["no_v"] / tot["pumpswap_prints"] if tot["pumpswap_prints"] else None
    return {**tot, "no_v_pools": len(pools), "no_v_fraction": frac, "incomplete": bool(frac is not None and frac > V_INCOMPLETE_FRACTION)}


# --- analysis (pure; tested on fixtures) ---------------------------------------------------------------------------------


def exit_class(row: Mapping[str, Any]) -> str:
    """tp / sl / time / miss from the cached row: a trigger exit is tp when its gross is positive, else sl."""
    if not row.get("filled"):
        return "miss"
    if row.get("exit") == "trigger":
        return "tp" if row["gross"] > 0 else "sl"
    return "time" if row.get("exit") == "cap" else "unknown"


def counted(rows: Sequence[Mapping[str, Any]], count_start: str = COUNT_START) -> list[Mapping[str, Any]]:
    t0 = hour_ms(count_start)
    return [r for r in rows if int(r["mig_ms"]) >= t0]


def cell_rows(rows: Sequence[Mapping[str, Any]], c: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    size = op.size_lamports(c["size_sol"])
    lag = int(c["exit_lag"])
    return [r for r in rows if not r.get("unselected") and int(r["k"]) == c["k"] and int(r["size"]) == size and int(r.get("exit_lag", 0)) == lag]


def haircut_delta(r: Mapping[str, Any]) -> float:
    """Change to a FILLED trade's net0 (lamports, before the per-side fee) for the measured live costs. Exit proceeds are
    P = max(net0 + size, 0) (a sell that could not be quoted has no proceeds and is left alone). Entry gap: g = 26.08 bps fewer
    tokens received, so the proceeds scale by (1 - g). Sell shortfall: s = 16 bps off the sell proceeds, so by (1 - s).
    net0' = net0 - P * (1 - (1 - g)(1 - s)). Misses and censored rows are unchanged. The fee, the fail models and every
    other term are applied afterwards by the operating point's own pricing."""
    if r.get("censored") or not r.get("filled") or int(r.get("status", 0)) == MISS:
        return 0.0
    proceeds = max(int(r["net0"]) + int(r["size"]), 0)
    g, sh = ENTRY_GAP_BPS / 1e4, SELL_SHORTFALL_BPS / 1e4
    return -proceeds * (1.0 - (1.0 - g) * (1.0 - sh))


def haircut_row(r: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(r)
    if not r.get("censored") and "net0" in r:
        out["net0"] = r["net0"] + haircut_delta(r)
    return out


def cell_trades(rows: Sequence[Mapping[str, Any]], c: Mapping[str, Any], haircut: bool = True) -> tuple[list[dict[str, Any]], int, dict[str, Mapping[str, Any]]]:
    """(trades at the cell's fee, n_censored, {mint: cached row}); the operating point's cell_trades does the pricing.
    haircut=True (default, the deciding read) nets the measured live costs out of each filled trade first; False is the raw
    simulator result (report-only)."""
    sel = cell_rows(rows, c)
    by_mint = {r["mint"]: r for r in sel}
    if len(by_mint) != len(sel):
        raise SystemExit("integrity: duplicate (mint, cell) rows")
    priced = [haircut_row(r) for r in sel] if haircut else sel
    trades, cen = op.cell_trades({(c["k"], op.size_lamports(c["size_sol"])): priced}, c)
    for t in trades:  # the counted day: a 24 h window anchored at the count start (12:00Z), labelled by its start date
        t["day"] = day_label(int(by_mint[t["mint"]]["mig_ms"]))
    return trades, cen, by_mint


DAY_MS = 86_400_000


def day_label(mig_ms: int, count_start: str = COUNT_START) -> str:
    """Counted day = the 24 h window [count_start + i d, count_start + (i+1) d), labelled by its start date. There are 13 of
    them from 2026-08-15T12 to 2026-08-28T12; UTC calendar dates would make 14 partial days, so no partial date is counted."""
    t0 = hour_ms(count_start)
    start = t0 + ((int(mig_ms) - t0) // DAY_MS) * DAY_MS
    return time.strftime("%Y-%m-%d", time.gmtime(start / 1000.0))


def n_counted_days(count_start: str = COUNT_START, pool_end: str = POOL_END) -> int:
    return (hour_ms(pool_end) - hour_ms(count_start)) // DAY_MS


def day_labels(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted({day_label(int(r["mig_ms"])) for r in rows})


def cell_report(rows: Sequence[Mapping[str, Any]], c: Mapping[str, Any], n_days: int, haircut: bool = True) -> dict[str, Any]:
    trades, cen, by_mint = cell_trades(rows, c, haircut)
    st = op.cell_stats(trades, c, n_days, cen)
    classes = [exit_class(by_mint[t["mint"]]) for t in trades]
    n_filled = sum(1 for t in trades if t["filled"])
    n_tp, n_sl, n_time = classes.count("tp"), classes.count("sl"), classes.count("time")
    st.update(
        {
            "id": cell_id(c),
            "label": cell_label(c),
            "role": cell_role(c),
            "exit_lag": c["exit_lag"],
            "n_entered": len(trades),
            "n_filled": n_filled,
            "n_miss": len(trades) - n_filled,
            "n_censored": cen,
            "tp": n_tp,
            "sl": n_sl,
            "time_stop": n_time,
            "tp_rate_filled": (n_tp / n_filled) if n_filled else None,
            "flat_mean_pct_of_size": None if not st["flat"] else st["flat"]["mean_sol"] / c["size_sol"] * 100.0,
            "press_mean_pct_of_size": None if not st["press"] else st["press"]["mean_sol"] / c["size_sol"] * 100.0,
            "note": " ; ".join(x for x in (SIZE_NOTE if c["size_sol"] != PRIMARY["size_sol"] else "", LAG_NOTE if c["exit_lag"] == 0 else "") if x),
        }
    )
    st.pop("axis", None)
    return st


def per_day_table(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    s = side(trades)
    if not s["flat"]:
        return []
    flat = {d["day"]: d for d in s["flat"]["days"]}
    press = {d["day"]: d for d in s["press"]["days"]}
    return [
        {"day": d, "n": flat[d]["n"], "flat_mean_sol": flat[d]["mean_sol"], "flat_total_sol": flat[d]["mean_sol"] * flat[d]["n"], "press_mean_sol": press[d]["mean_sol"], "press_total_sol": press[d]["mean_sol"] * press[d]["n"]}
        for d in sorted(flat)
    ]


def _leg(g: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return None if g is None else {"mean_sol": g["mean_sol"], "ci90_sol": g["ci90_sol"], "total_sol": g["total_sol"], "ex_top3_sol": g["ex_top3_sol"], "days_positive": g["days_positive"], "n_days": g["n_days"]}


def week_split(rows: Sequence[Mapping[str, Any]], c: Mapping[str, Any], split: str = WEEK_SPLIT) -> dict[str, Any]:
    trades, _cen, by_mint = cell_trades(rows, c)
    cut = hour_ms(split)
    out = {}
    for name, keep in (("first_7_days", lambda m: m < cut), ("last_6_days", lambda m: m >= cut)):
        sub = [t for t in trades if keep(int(by_mint[t["mint"]]["mig_ms"]))]
        s = side(sub)
        out[name] = {"n": s["n"], "filled": s["filled"], "flat": _leg(s["flat"]), "press": _leg(s["press"])}
    return out


def primary_without_w1(rows: Sequence[Mapping[str, Any]], c: Mapping[str, Any] = PRIMARY, w1_start: str = W1_START) -> dict[str, Any]:
    """Report-only: the primary cell without w1's hours (migrations at or after w1's start), because w1 was outcome-read
    by DEC-017 candidate (a)."""
    trades, _cen, by_mint = cell_trades(rows, c)
    cut = hour_ms(w1_start)
    s = side([t for t in trades if int(by_mint[t["mint"]]["mig_ms"]) < cut])
    return {"excluded_from": w1_start, "n": s["n"], "filled": s["filled"], "flat": _leg(s["flat"]), "press": _leg(s["press"])}


def sharp_drop_context(rows: Sequence[Mapping[str, Any]], c: Mapping[str, Any] = PRIMARY) -> dict[str, Any]:
    trades, _cen, by_mint = cell_trades(rows, c)
    filled = [by_mint[t["mint"]] for t in trades if t["filled"]]
    labelled = [r["rug"] for r in filled if r.get("rug")]
    n = len(labelled)
    return {
        "note": "report-only; measures post-migration volatility, NOT rugs (label base rate 0.5664 in #191); the #336 label (tools.exp012_rug_risk.rug_label, imported)",
        "n_filled": len(filled),
        "n_labelled": n,
        "sharp_drop_fraction": (sum(1 for x in labelled if x["rug"]) / n) if n else None,
        "one_step_fraction": (sum(1 for x in labelled if x["one_step"]) / n) if n else None,
        "crash50_fraction": (sum(1 for x in labelled if x["crash50"]) / n) if n else None,
    }


RULES = {
    0: "no reading (the run was refused or aborted)",
    1: "inconclusive (n entered < 100)",
    2: "strong caution against any size step (either leg's mean < 0)",
    3: "consistent with EXP-012 still working on older days at a 2-slot exit lag. Context only for DEC-020: no simulation can say whether 0.25 SOL works (DEC-020 \u00a71), and Option A (after the 10-16 forward read) remains the recommendation",
    4: "does not support",
}
MIN_N = 100
MIN_DAYS = 5


def leg_bars(leg: Mapping[str, Any] | None, n_entered: int) -> dict[str, bool]:
    if not leg:
        return {"n": n_entered >= MIN_N, "days": False, "ci_lower_gt_0": False, "ex_top3_gt_0": False, "all": False}
    days_ok = leg["days_with_trades"] >= MIN_DAYS and leg["days_positive"] * 2 > leg["days_with_trades"]
    ci_ok = bool(leg["ci90_sol"]) and leg["ci90_sol"][0] > 0
    top_ok = leg["ex_top3_sol"] is not None and leg["ex_top3_sol"] > 0
    b = {"n": n_entered >= MIN_N, "days": bool(days_ok), "ci_lower_gt_0": bool(ci_ok), "ex_top3_gt_0": bool(top_ok)}
    b["all"] = all(b.values())
    return b


def _pos(x: float | None) -> bool | None:
    return None if x is None else x > 0


def modifier_reasons(primary: Mapping[str, Any], halves: Mapping[str, Any], without_w1: Mapping[str, Any]) -> list[str]:
    """Modifier (rule 5): the first-7 and last-6 halves disagree in sign, or the primary without w1 flips sign, on either leg.
    A half or a without-w1 book with no trades counts as a disagreement (the sign cannot be confirmed)."""
    out = []
    for leg in ("flat", "press"):
        a, b = halves["first_7_days"][leg], halves["last_6_days"][leg]
        sa, sb = _pos(a and a["mean_sol"]), _pos(b and b["mean_sol"])
        if sa is None or sb is None or sa != sb:
            out.append(f"{leg}: halves disagree in sign")
        w = without_w1[leg]
        sw, sf = _pos(w and w["mean_sol"]), _pos(primary[leg] and primary[leg]["mean_sol"])
        if sw is None or sf is None or sw != sf:
            out.append(f"{leg}: primary without w1 flips sign")
    return out


def read_outcome(primary: Mapping[str, Any], halves: Mapping[str, Any], without_w1: Mapping[str, Any], run_status: str = "completed") -> dict[str, Any]:
    """The pre-declared outcome rules on the HAIRCUT primary, applied in order (first match wins):
    0 a refused or aborted run: no reading; 1 n < 100: inconclusive; 2 either leg's mean < 0: strong caution against any size
    step; 3 every bar under BOTH legs: consistent with ...; 4 anything else: does not support. Modifier (rule 5): if it fires on a
    rule-3 match, the reading is downgraded to rule 4's."""
    if run_status != "completed":
        return {"matched_rule": 0, "matched": RULES[0], "modifier": None, "final_reading": RULES[0], "bars": None}
    n = primary["n_entered"]
    bars = {leg: leg_bars(primary[leg], n) for leg in ("flat", "press")}
    mods = modifier_reasons(primary, halves, without_w1)
    if n < MIN_N:
        rule = 1
    elif any(primary[leg] is not None and primary[leg]["mean_sol"] < 0 for leg in ("flat", "press")):
        rule = 2
    elif bars["flat"]["all"] and bars["press"]["all"]:
        rule = 3
    else:
        rule = 4
    final, fired = RULES[rule], False
    if rule == 3 and mods:
        final, fired = RULES[4], True
    return {"matched_rule": rule, "matched": RULES[rule], "modifier": {"rule": 5, "fired": fired, "reasons": mods, "applied_to": "rule 3 only"}, "final_reading": final, "bars": bars}


def analyze(rows: Sequence[Mapping[str, Any]], cells: Sequence[Mapping[str, Any]] = CELLS, count_start: str = COUNT_START) -> dict[str, Any]:
    cr = counted(rows, count_start)
    days = day_labels(cr)
    selected = {r["mint"] for r in cr if not r.get("unselected")}
    n_days = n_counted_days(count_start)
    cell_reports = [cell_report(cr, c, n_days) for c in cells]
    raw_reports = [cell_report(cr, c, n_days, haircut=False) for c in cells]
    p = cells[0]
    ptrades, _cen, _bm = cell_trades(cr, p)
    halves, wo_w1 = week_split(cr, p), primary_without_w1(cr, p)
    return {
        "schema": "exp012_backcheck_v1",
        "status": BANNER,
        "banner": BANNER,
        "n_cells": len(cells),
        "existing_tries_on_pool": EXISTING_TRIES_ON_POOL,
        "cumulative_tries_on_pool": EXISTING_TRIES_ON_POOL + len(cells),
        "pool": POOL_NAME,
        "pool_window": [POOL_START, POOL_END],
        "counted_from": count_start,
        "n_migrations_scored_counted": len({r["mint"] for r in cr}),
        "n_selected_counted": len(selected),
        "n_migrations_buffer_only": len({r["mint"] for r in rows if int(r["mig_ms"]) < hour_ms(count_start)}),
        "days": days,
        "n_days": n_days,
        "n_days_with_counted_migrations": len(days),
        "day_definition": DAY_DEFINITION,
        "ci": "gate cluster bootstrap, 1000 draws, seed 1 (tools.paper_attention_promote.book_stats), 5th-95th percentile of the mean",
        "primary": cell_label(p),
        "haircut": {
            "applies_to": "every cell in `cells` and the primary tables below (the deciding read); `cells_raw` is the unadjusted simulator result, report-only",
            "sell_shortfall_bps": SELL_SHORTFALL_BPS,
            "entry_gap_bps": ENTRY_GAP_BPS,
            "formula": "net0' = net0 - P * (1 - (1 - 0.002608)(1 - 0.0016)), P = max(net0 + size, 0), filled trades only; then fee and both fail models as usual",
        },
        "cells": cell_reports,
        "cells_raw": raw_reports,
        "reading": read_outcome(cell_reports[0], halves, wo_w1),
        "primary_per_day": per_day_table(ptrades),
        "primary_week_split": halves,
        "primary_without_w1": wo_w1,
        "sharp_drop_context": sharp_drop_context(cr, p),
        "caveats": [
            "exploration pool, never trained on; NOT unread (w1 [2026-08-26T12, 2026-08-28T12) was outcome-read by DEC-017 candidate (a)); not a confirmation holdout, not gate evidence",
            "EXP-013 and EXP-014 August bars are no longer on unread data after this run (their frozen screens are unchanged)",
            "V pricing needs a pool -> V map covering these pools; the run refuses (exit 2) if more than 1% of counted PumpSwap prints lack V; see v_coverage",
            "k counts from the first PumpSwap print, not migration",
            "sizes above 0.05: " + SIZE_NOTE,
            "primary exit lag 2 slots is still optimistic versus the measured live exit leak; the lag-0 cell is an upper bound (" + LAG_NOTE + ")",
            "the deciding read nets the sell shortfall (-16 bps of proceeds) and the sim-vs-live entry gap (+26.08 bps, job #175) out of every filled trade; entry noise and MEV are not added; the unadjusted result is `cells_raw`, report-only",
        ],
    }


# --- rendering -------------------------------------------------------------------------------------------------------------


def _f(v: float | None, d: int = 5) -> str:
    return "n/a" if v is None else f"{v:.{d}f}"


def _ci(ci: Sequence[float] | None) -> str:
    return "n/a" if not ci else f"[{ci[0]:.5f}, {ci[1]:.5f}]"


def _leg_cols(g: Mapping[str, Any] | None) -> str:
    if not g:
        return "n/a | n/a | n/a | n/a | n/a"
    return f"{_f(g['mean_sol'])} | {_ci(g['ci90_sol'])} | {_f(g['total_sol'], 4)} | {_f(g['ex_top3_sol'], 4)} | {g['days_positive']}/{g['days_with_trades']}"


def render_md(rep: Mapping[str, Any]) -> str:
    L = [rep["banner"], "", "# EXP-012 back-check on explore-0814 (frozen model, live operating point)", ""]
    L += [f"N cells = {rep['n_cells']} (existing tries on explore-0814: {rep['existing_tries_on_pool']}).", ""]
    rd = rep["reading"]
    L += ["## Reading (pre-declared rules, applied to the haircut primary)", "", f"- Matched rule {rd['matched_rule']}: {rd['matched']}.", f"- Modifier (rule 5, applies to rule 3 only): " + ("none" if rd["modifier"] is None else f"fired={rd['modifier']['fired']}; reasons: {'; '.join(rd['modifier']['reasons']) or 'none'}") + ".", f"- **Final reading: {rd['final_reading']}.**", f"- Haircut: {rep['haircut']['formula']} (sell shortfall {rep['haircut']['sell_shortfall_bps']} bps, entry gap {rep['haircut']['entry_gap_bps']} bps).", ""]
    if rep.get("v_adapter_counts", {}).get("incomplete"):
        L += [f"**WARNING: V adapter saw prints with no V** ({rep['v_adapter_counts']['no_v']} of {rep['v_adapter_counts']['pumpswap_prints']}). P&L below is NOT V-priced for those pools.", ""]
    L += [
        f"- Counted from {rep['counted_from']} (first 24 h of the pool are feature buffer only). {rep['n_selected_counted']} of {rep['n_migrations_scored_counted']} counted migrations scored at or above 0.8031; {rep['n_migrations_buffer_only']} buffer-only migrations not counted.",
        f"- Counted days: {rep['n_days']} ({rep['day_definition']}). CI: {rep['ci']}.",
        f"- Tries on this pool: {rep['existing_tries_on_pool']} existing (DEC-017 candidate (a) on w1) + {rep['n_cells']} new = {rep['cumulative_tries_on_pool']}. Code {rep.get('git_head', 'n/a')}.",
        f"- {rep['v_prepass_line']}" if "v_prepass_line" in rep else "- V pre-pass: not run",
        f"- V map {rep.get('vmap_path', 'n/a')} sha256 {rep.get('vmap_sha256', 'n/a')}; primary-cell trades on no-V pools: {rep.get('primary_trades_on_no_v_pools', 'n/a')}.",
        "",
        "## Cells, net of the measured live costs (haircut); RAW rows are the unadjusted simulator result, report-only. Both fail models; sensitivity cells are report-only, never selected among",
        "",
        "| role | cell | note | n entered | filled | miss | tp | sl | time | tp rate | flat mean % size | press mean % size | flat mean | flat CI90 | flat total | flat ex-top3 | flat days+ | press mean | press CI90 | press total | press ex-top3 | press days+ |",
        "| " + " | ".join(["---"] * 22) + " |",
    ]
    for s in rep["cells"] + [dict(x, role="RAW " + x["role"]) for x in rep["cells_raw"]]:
        L.append(f"| {s['role']} | {s['label']} | {s['note']} | {s['n_entered']} | {s['n_filled']} | {s['n_miss']} | {s['tp']} | {s['sl']} | {s['time_stop']} | {_f(s['tp_rate_filled'], 3)} | {_f(s['flat_mean_pct_of_size'], 3)} | {_f(s['press_mean_pct_of_size'], 3)} | {_leg_cols(s['flat'])} | {_leg_cols(s['press'])} |")
    L += ["", "## Primary cell per counted day (SOL)", "", "| day | n | flat mean | flat total | press mean | press total |", "| --- | --- | --- | --- | --- | --- |"]
    for d in rep["primary_per_day"]:
        L.append(f"| {d['day']} | {d['n']} | {_f(d['flat_mean_sol'])} | {_f(d['flat_total_sol'], 4)} | {_f(d['press_mean_sol'])} | {_f(d['press_total_sol'], 4)} |")
    L += ["", "## Primary cell, first 7 days vs last 6 days (report-only)", "", "| half | n | filled | flat mean | flat CI90 | press mean | press CI90 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for name, h in rep["primary_week_split"].items():
        fl, pr = h["flat"], h["press"]
        L.append(f"| {name} | {h['n']} | {h['filled']} | {_f(fl and fl['mean_sol'])} | {_ci(fl and fl['ci90_sol'])} | {_f(pr and pr['mean_sol'])} | {_ci(pr and pr['ci90_sol'])} |")
    w1 = rep["primary_without_w1"]
    fl, pr = w1["flat"], w1["press"]
    L += ["", f"## Primary cell without w1's hours (report-only; migrations before {w1['excluded_from']})", "", f"- n {w1['n']}, filled {w1['filled']}; flat mean {_f(fl and fl['mean_sol'])} CI90 {_ci(fl and fl['ci90_sol'])}; pressure mean {_f(pr and pr['mean_sol'])} CI90 {_ci(pr and pr['ci90_sol'])}."]
    rc = rep["sharp_drop_context"]
    L += ["", "## Sharp-drop rate (#336 label; report-only, primary filled trades)", "", f"- labelled {rc['n_labelled']} of {rc['n_filled']} filled; sharp-drop rate {_f(rc['sharp_drop_fraction'], 3)}, one-step drop {_f(rc['one_step_fraction'], 3)}, crash below 0.5x before 1.5x {_f(rc['crash50_fraction'], 3)}. {rc['note']}.", "", "## Caveats", ""]
    L += [f"- {c}" for c in rep["caveats"]]
    if "v_adapter_counts" in rep:
        v = rep["v_adapter_counts"]
        L += ["", f"V adapter during the pass: {v['corrected']} corrected, {v['no_v']} no-V of {v['pumpswap_prints']} PumpSwap prints ({v['no_v_pools']} pools without V)."]
    return "\n".join(L) + "\n"


# --- tries log -------------------------------------------------------------------------------------------------------------


def pool_blocks() -> list[dict[str, str]]:
    return [{"start_hour": POOL_START, "end_hour_exclusive": POOL_END, "host": "mal-research-0", "ledger_owner": "exploration pool"}]


def _already_in_log(log: Path, cell: str, result_path: Path, status: str = "completed") -> bool:
    if not log.is_file():
        return False
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        cfg = rec.get("config", {})
        if rec.get("tool") == TOOL and cfg.get("cell") == cell and rec.get("result_path") == str(result_path) and cfg.get("status", "completed") == status:
            return True
    return False


def planned_report() -> dict[str, Any]:
    """The cell list as a report skeleton, for logging tries when a run stops before analyze() produced a report."""
    return {"n_cells": len(CELLS), "existing_tries_on_pool": EXISTING_TRIES_ON_POOL, "cells": [{"id": cell_id(c), "role": cell_role(c), **{k: c[k] for k in ("threshold", "k", "size_sol", "fee", "exit_lag")}} for c in CELLS]}


def log_tries(rep: Mapping[str, Any], out_dir: Path, tries_log: str | Path, marker_name: str = MARKER, status: str = "completed") -> int:
    """One result.v1 tries line per cell, tagged pool=explore-0814 (its own pool, separate from the 9-day count) and with
    config.status (completed, refused_after_read, aborted_after_read). Idempotent on re-run per status: a marker in out_dir plus
    a scan of the log for (tool, cell, result_path, status)."""
    from tools import mal_result
    from tools.exp012_exit_sensitivity import _read_marker, _write_marker

    marker = out_dir / marker_name
    done = _read_marker(marker)
    result_path = out_dir / RESULT_NAME
    n = 0
    for s in rep["cells"]:
        key = s["id"] if status == "completed" else f"{s['id']}@{status}"
        if key in done or "*" in done:
            continue
        if status != "completed" and (s["id"] in done or _already_in_log(Path(tries_log), s["id"], result_path, "completed")):
            continue  # per-log guard: a cell already logged completed in this log never gets an aborted/refused line
        if not _already_in_log(Path(tries_log), s["id"], result_path, status):
            mal_result.append_try(
                tries_log,
                tool=TOOL,
                config={"experiment": "EXP-012 backcheck", "pool": POOL_NAME, "status": status, "cell": s["id"], "role": s["role"], "threshold": s["threshold"], "k": s["k"], "size_sol": s["size_sol"], "fee_lamports": s["fee"], "exit_lag": s["exit_lag"], "selection": "frozen model", "pricing": "V", "n_cells": rep["n_cells"], "existing_tries_on_pool": rep["existing_tries_on_pool"]},
                data_blocks=pool_blocks(),
                result_path=result_path,
                role="exploration",
            )
            n += 1
        done.add(key)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_marker(marker, done)
    return n


def log_all(rep: Mapping[str, Any], out_dir: Path, tries_path: Path, canonical: Path, status: str) -> dict[str, Any]:
    """Log the cells to --tries-log and to the canonical log (once each if they are the same file)."""
    out = {"logged": log_tries(rep, out_dir, tries_path, MARKER, status), "log_path": str(tries_path), "status": status}
    if canonical.resolve() != Path(tries_path).resolve():
        out["canonical_logged"] = log_tries(rep, out_dir, canonical, "tries_logged_canonical.marker", status)
        out["canonical_path"] = str(canonical)
    return out


# --- CLI -------------------------------------------------------------------------------------------------------------------


def write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    from tools.exp012_exit_sensitivity import resolve_tries_path

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--view-dir", type=Path, action="append", default=None, help="repeatable: an explore-0814 clean view (w1..w7)")
    ap.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--tries-log", default=None, help="absolute tries-log path (default: MAL_TRIES_LOG, else data/tries.jsonl)")
    ap.add_argument("--max-workers", type=int, default=2)
    ap.add_argument("--vmap", default=DEFAULT_VMAP_0814, help="pool -> V map; sha256 is asserted against the pinned pool_v_0814.json")
    ap.add_argument("--canonical-tries", type=Path, default=CANONICAL_TRIES, help="absolute path of the repo's data/tries.jsonl (default: resolved from this file, not the cwd); the run's lines are appended here too")
    args = ap.parse_args(argv)
    try:
        if args.max_workers > 2:
            raise Refused("keep --max-workers <= 2")
        g = guard_views(args.view_dir or [])  # before any row is read
        _model, threshold, _names = e11.load_frozen_spec(args.artifact_dir)
        check_frozen_threshold(threshold)
        model_md5 = check_model_md5(args.artifact_dir)  # before any row is read
        vmap_sha = check_vmap_sha(args.vmap)  # before any row is read
        cov, mint_pools = v_prepass(g["roots"], g["pool"], args.vmap)  # pool field only; before any scoring or P&L
        print(coverage_line(cov), file=sys.stderr, flush=True)
        check_v_coverage(cov)
    except Refused as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    tries_path = resolve_tries_path(args.tries_log)
    canonical = Path(args.canonical_tries).resolve()
    t0 = time.time()
    status = "aborted_after_read"  # any exception or exit from here on leaves an honest tries record (finally below)

    def _on_sigterm(signum: int, frame: Any) -> None:  # a MiScusi cancel: unwind through the finally below
        raise SystemExit(128 + signum)

    prev_handler = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        rows = collect_rows(g["roots"], g["pool"], args.artifact_dir, args.vmap, args.out_dir / "scratch", args.max_workers)
        write_rows(args.out_dir / ROWS_NAME, rows)
        rep = analyze(rows)
        n_no_v = primary_no_v_trades(rows, mint_pools, adapter_no_v_pools(args.out_dir / "scratch" / "counts_virtual"))
        print(f"primary-cell trades whose pool the adapter priced without V: {n_no_v}", file=sys.stderr, flush=True)
        if n_no_v > 0:
            status = "refused_after_read"
            print(f"refusing: {n_no_v} primary-cell trades are on pools with no V; tries logged as refused_after_read, no report written", file=sys.stderr)
            return 2
        rep["primary_trades_on_no_v_pools"] = n_no_v
        rep["vmap_sha256"] = vmap_sha
        rep["vmap_path"] = str(args.vmap)
        rep["v_coverage"] = cov
        rep["v_prepass_line"] = coverage_line(cov)
        rep["v_adapter_counts"] = v_coverage(args.out_dir / "scratch" / "counts_virtual")
        rep["git_head"] = git_head()
        rep["view_sha256"] = g["view_sha256"]
        rep["model_md5"] = model_md5
        rep["wall_s"] = time.time() - t0
        rep["tries"] = {"to_log": "completed", "log_path": str(tries_path), "canonical_path": str(canonical)}
        (args.out_dir / RESULT_NAME).write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
        md = render_md(rep)
        (args.out_dir / "report.md").write_text(md, encoding="utf-8")
        log_all(rep, args.out_dir, tries_path, canonical, "completed")  # the report is on disk first; "completed" is set only after
        status = "completed"
        print(md)
        return 0
    finally:
        signal.signal(signal.SIGTERM, prev_handler)
        if status != "completed":
            log_all(planned_report(), args.out_dir, tries_path, canonical, status)


if __name__ == "__main__":
    raise SystemExit(main())
