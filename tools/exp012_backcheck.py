#!/usr/bin/env python3
"""EXP-012 back-check on the 14-day exploration expansion explore-0814. **EXPLORATION, NOT A PROMOTE, NOT GATE
EVIDENCE.** Pre-declaration: EXP/EXP-012-backcheck-0814.md.

Question: the frozen EXP-012 entry model (threshold 0.8031, never refit) was frozen on a 9-day pool. What does the
frozen strategy earn, at the LIVE operating point (V-priced, k = 6 slots after migration, 0.05 SOL, per-side fee
505,000 lamports, tp50_sl30, 30-minute exit cap), on 14 days of tape the model never saw and whose migrate-entry
outcomes were never read? Exploration evidence only; it informs the live probe's size step (DEC-020), nothing else.

Data: the seven clean views /data/mal/clean-view/explore-0814/w1..w7 (pool [2026-08-14T12, 2026-08-28T12), 336 h).
The first 24 h of the pool (to 2026-08-15T12) are FEATURE BUFFER ONLY: migrations before 2026-08-15T12 are scored but
not counted, so creator_prior_mints_24h is complete for every counted mint. The table is built by the frozen code path
(tools.exp012_score.load_rows, anchored chunk plan, creator history, BUFFER_HOURS = 24, MAX_HOME_HOURS = 12). Every
migration is scored with the FROZEN model (ARTIFACTS/exp012/model.txt, tools.exp011_score.score_rows), not OOF.

PRE-DECLARED CELLS (6; constants CELLS in this module; the labels below are asserted equal to the code by a test).
All at the frozen threshold, fee 505000, exit tp50_sl30. Fail models: flat 15% and pressure (slope scale 1).
  PRIMARY
    thr 0.8031 k=6 size=0.05 fee=505000 exit_lag=0
  SENSITIVITY (report-only, never selected among, none can change the primary)
    thr 0.8031 k=6 size=0.25 fee=505000 exit_lag=0
    thr 0.8031 k=6 size=0.5 fee=505000 exit_lag=0
    thr 0.8031 k=4 size=0.05 fee=505000 exit_lag=0
    thr 0.8031 k=8 size=0.05 fee=505000 exit_lag=0
    thr 0.8031 k=6 size=0.25 fee=505000 exit_lag=2
The exit-lag cell uses exploration_exits' exit_land_k = 2 (slots the sell lands after the trigger; score_one exposes it).
Sizes above 0.05 miss size-proportional costs (sell shortfall, MEV) and do not by themselves support a larger live size.

Simulation is tools.exp012_operating_point's, by import (multi_cell_patch with explicit combos): V pricing through the
PumpSwap virtual-reserve adapter, latency_curve buy at k slots with its 15% cap (a MISS pays the per-side fee), the
frozen tp50_sl30 exit, 30-minute exit cap. CIs: the gate's cluster bootstrap (tools.paper_attention_promote.book_stats,
1,000 draws, seed 1; 5th-95th percentile of the mean). Sharp-drop rate (report-only; NOT a rug rate, it measures post-migration volatility) is the #336 label (one-step drop >= 30%
vs the previous print, or a print < 0.5x entry before 1.5x, within 15 min); tools/exp012_rug_risk.py is not on main, so
the same definition is implemented here and tested.

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
from tools.latency_curve import _hour_file

TOOL = "tools.exp012_backcheck"
POOL_NAME = "explore-0814"
POOL_START = "2026-08-14T12"
POOL_END = "2026-08-28T12"  # exclusive
COUNT_START = "2026-08-15T12"  # first 24 h of the pool are feature buffer only
WEEK_SPLIT = "2026-08-22T12"  # first 7 counted days | last 6
HOUR_FMT = "%Y-%m-%dT%H"
FROZEN_THRESHOLD = op.FROZEN_THRESHOLD
PRIMARY_FEE = op.PRIMARY_FEE  # 505,000 = priority 2 x 250k + base, the operating-point convention
LAMPORTS = 1_000_000_000
BANNER = "EXPLORATION, frozen EXP-012 on explore-0814 (never trained on, never outcome-read before). Not gate evidence."
MARKER = "tries_logged.marker"
RESULT_NAME = "report.json"
ROWS_NAME = "backcheck_rows.jsonl"
ENV_ARTIFACT = "MAL_BACKCHECK_ARTIFACT"
RUG_WINDOW_MS = 15 * 60 * 1000
RUG_ONE_STEP = -0.30
RUG_CRASH = 0.5
RUG_TP = 1.5
RUG_K = 6
V_INCOMPLETE_FRACTION = 0.01

# Extra holdout / reserved name fragments refused anywhere in a resolved view path (beyond op's guarded prefixes).
FORBIDDEN_NAME_FRAGMENTS = ("fresh-0903", "fresh-0828", "fresh-0808", "forward-1002")


def _c(k: int, size_sol: float, exit_lag: int = 0) -> dict[str, Any]:
    return {"threshold": FROZEN_THRESHOLD, "k": k, "size_sol": size_sol, "fee": PRIMARY_FEE, "exit_lag": exit_lag}


PRIMARY = _c(6, 0.05)
SENSITIVITY = (_c(6, 0.25), _c(6, 0.5), _c(4, 0.05), _c(8, 0.05), _c(6, 0.25, 2))
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


# --- rug label (the #336 definition; tools/exp012_rug_risk.py is not on main) -----------------------------------------


def rug_label(fills: Sequence[Any], idx: int, landing_ms: int, window_ms: int = RUG_WINDOW_MS) -> dict[str, Any] | None:
    """None when there is no entry state. PumpSwap prints after the entry state within the window: one_step = a SELL print at
    <= -30% of the immediately preceding print (the first preceding is the entry state's price); crash50 = a print below
    0.5x the entry price before any print >= 1.5x. rug = one_step or crash50."""
    if idx < 0:
        return None
    p0 = fills[idx].price_sol
    prev = p0
    one_step = crash = tp_seen = False
    for p in fills[idx + 1 :]:
        if p.venue != "pumpswap" or p.t_recv_ms <= landing_ms:
            continue
        if p.t_recv_ms > landing_ms + window_ms:
            break
        if prev > 0 and p.side == "sell" and p.price_sol / prev - 1.0 <= RUG_ONE_STEP:
            one_step = True
        if p.price_sol >= RUG_TP * p0:
            tp_seen = True
        if not tp_seen and p.price_sol < RUG_CRASH * p0:
            crash = True
        prev = p.price_sol
    return {"one_step": one_step, "crash50": crash, "rug": one_step or crash}


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


def cell_trades(rows: Sequence[Mapping[str, Any]], c: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int, dict[str, Mapping[str, Any]]]:
    """(trades at the cell's fee, n_censored, {mint: cached row}); the operating point's cell_trades does the pricing."""
    sel = cell_rows(rows, c)
    by_mint = {r["mint"]: r for r in sel}
    if len(by_mint) != len(sel):
        raise SystemExit("integrity: duplicate (mint, cell) rows")
    trades, cen = op.cell_trades({(c["k"], op.size_lamports(c["size_sol"])): sel}, c)
    return trades, cen, by_mint


def day_labels(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted({time.strftime("%Y-%m-%d", time.gmtime(int(r["mig_ms"]) / 1000.0)) for r in rows})


def cell_report(rows: Sequence[Mapping[str, Any]], c: Mapping[str, Any], n_days: int) -> dict[str, Any]:
    trades, cen, by_mint = cell_trades(rows, c)
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


def sharp_drop_context(rows: Sequence[Mapping[str, Any]], c: Mapping[str, Any] = PRIMARY) -> dict[str, Any]:
    trades, _cen, by_mint = cell_trades(rows, c)
    filled = [by_mint[t["mint"]] for t in trades if t["filled"]]
    labelled = [r["rug"] for r in filled if r.get("rug")]
    n = len(labelled)
    return {
        "note": "report-only; measures post-migration volatility, NOT rugs (label base rate 0.5664 in #191); the #336 label (tools/exp012_rug_risk.py is not on main, same definition implemented here)",
        "n_filled": len(filled),
        "n_labelled": n,
        "sharp_drop_fraction": (sum(1 for x in labelled if x["rug"]) / n) if n else None,
        "one_step_fraction": (sum(1 for x in labelled if x["one_step"]) / n) if n else None,
        "crash50_fraction": (sum(1 for x in labelled if x["crash50"]) / n) if n else None,
    }


def analyze(rows: Sequence[Mapping[str, Any]], cells: Sequence[Mapping[str, Any]] = CELLS, count_start: str = COUNT_START) -> dict[str, Any]:
    cr = counted(rows, count_start)
    days = day_labels(cr)
    selected = {r["mint"] for r in cr if not r.get("unselected")}
    cell_reports = [cell_report(cr, c, len(days)) for c in cells]
    p = cells[0]
    ptrades, _cen, _bm = cell_trades(cr, p)
    return {
        "schema": "exp012_backcheck_v1",
        "status": BANNER,
        "banner": f"{BANNER} N cells = {len(cells)}.",
        "n_cells": len(cells),
        "pool": POOL_NAME,
        "pool_window": [POOL_START, POOL_END],
        "counted_from": count_start,
        "n_migrations_scored_counted": len({r["mint"] for r in cr}),
        "n_selected_counted": len(selected),
        "n_migrations_buffer_only": len({r["mint"] for r in rows if int(r["mig_ms"]) < hour_ms(count_start)}),
        "days": days,
        "n_days": len(days),
        "ci": "gate cluster bootstrap, 1000 draws, seed 1 (tools.paper_attention_promote.book_stats), 5th-95th percentile of the mean",
        "primary": cell_label(p),
        "cells": cell_reports,
        "primary_per_day": per_day_table(ptrades),
        "primary_week_split": week_split(cr, p),
        "sharp_drop_context": sharp_drop_context(cr, p),
        "caveats": [
            "exploration pool, never trained on and never outcome-read before this run; not a confirmation holdout, not gate evidence",
            "EXP-013 and EXP-014 planned screens on the same days; this read opens migrate-entry outcomes on them (their frozen screens are unchanged)",
            "V pricing needs a pool -> V map covering these pools; see v_coverage",
            "sizes above 0.05 miss size-proportional costs (sell shortfall, MEV) and do not by themselves support a larger live size",
            "the live probe's -11..-16 bps sell shortfall and +-300 bps entry noise are not added",
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
    if rep.get("v_coverage", {}).get("incomplete"):
        L += [f"**WARNING: V pricing incomplete** ({rep['v_coverage']['no_v']} of {rep['v_coverage']['pumpswap_prints']} PumpSwap prints had no V). P&L below is NOT V-priced for those pools.", ""]
    L += [
        f"- Counted from {rep['counted_from']} (first 24 h of the pool are feature buffer only). {rep['n_selected_counted']} of {rep['n_migrations_scored_counted']} counted migrations scored at or above 0.8031; {rep['n_migrations_buffer_only']} buffer-only migrations not counted.",
        f"- UTC days with counted migrations: {rep['n_days']} (first and last are partial). CI: {rep['ci']}.",
        "",
        "## Cells (both fail models; sensitivity cells are report-only, never selected among)",
        "",
        "| role | cell | n entered | filled | miss | tp | sl | time | tp rate | flat mean | flat CI90 | flat total | flat ex-top3 | flat days+ | press mean | press CI90 | press total | press ex-top3 | press days+ |",
        "| " + " | ".join(["---"] * 19) + " |",
    ]
    for s in rep["cells"]:
        L.append(f"| {s['role']} | {s['label']} | {s['n_entered']} | {s['n_filled']} | {s['n_miss']} | {s['tp']} | {s['sl']} | {s['time_stop']} | {_f(s['tp_rate_filled'], 3)} | {_leg_cols(s['flat'])} | {_leg_cols(s['press'])} |")
    L += ["", "## Primary cell per UTC day (SOL)", "", "| day | n | flat mean | flat total | press mean | press total |", "| --- | --- | --- | --- | --- | --- |"]
    for d in rep["primary_per_day"]:
        L.append(f"| {d['day']} | {d['n']} | {_f(d['flat_mean_sol'])} | {_f(d['flat_total_sol'], 4)} | {_f(d['press_mean_sol'])} | {_f(d['press_total_sol'], 4)} |")
    L += ["", "## Primary cell, first 7 days vs last 6 days (report-only)", "", "| half | n | filled | flat mean | flat CI90 | press mean | press CI90 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for name, h in rep["primary_week_split"].items():
        fl, pr = h["flat"], h["press"]
        L.append(f"| {name} | {h['n']} | {h['filled']} | {_f(fl and fl['mean_sol'])} | {_ci(fl and fl['ci90_sol'])} | {_f(pr and pr['mean_sol'])} | {_ci(pr and pr['ci90_sol'])} |")
    rc = rep["sharp_drop_context"]
    L += ["", "## Sharp-drop rate (#336 label; report-only, primary filled trades)", "", f"- labelled {rc['n_labelled']} of {rc['n_filled']} filled; sharp-drop rate {_f(rc['sharp_drop_fraction'], 3)}, one-step drop {_f(rc['one_step_fraction'], 3)}, crash below 0.5x before 1.5x {_f(rc['crash50_fraction'], 3)}. {rc['note']}.", "", "## Caveats", ""]
    L += [f"- {c}" for c in rep["caveats"]]
    if "v_coverage" in rep:
        v = rep["v_coverage"]
        L += ["", f"V coverage: {v['corrected']} corrected, {v['no_v']} no-V of {v['pumpswap_prints']} PumpSwap prints ({v['no_v_pools']} pools without V)."]
    return "\n".join(L) + "\n"


# --- tries log -------------------------------------------------------------------------------------------------------------


def pool_blocks() -> list[dict[str, str]]:
    return [{"start_hour": POOL_START, "end_hour_exclusive": POOL_END, "host": "mal-research-0", "ledger_owner": "exploration pool"}]


def _already_in_log(log: Path, cell: str, result_path: Path) -> bool:
    if not log.is_file():
        return False
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("tool") == TOOL and rec.get("config", {}).get("cell") == cell and rec.get("result_path") == str(result_path):
            return True
    return False


def log_tries(rep: Mapping[str, Any], out_dir: Path, tries_log: str | Path) -> int:
    """One result.v1 tries line per cell, tagged pool=explore-0814 (its own pool, separate from the 9-day count). Idempotent
    on re-run: a marker in out_dir plus a scan of the log for (tool, cell, result_path)."""
    from tools import mal_result
    from tools.exp012_exit_sensitivity import _read_marker, _write_marker

    marker = out_dir / MARKER
    done = _read_marker(marker)
    result_path = out_dir / RESULT_NAME
    n = 0
    for s in rep["cells"]:
        if s["id"] in done or "*" in done:
            continue
        if not _already_in_log(Path(tries_log), s["id"], result_path):
            mal_result.append_try(
                tries_log,
                tool=TOOL,
                config={"experiment": "EXP-012 backcheck", "pool": POOL_NAME, "cell": s["id"], "role": s["role"], "threshold": s["threshold"], "k": s["k"], "size_sol": s["size_sol"], "fee_lamports": s["fee"], "exit_lag": s["exit_lag"], "selection": "frozen model", "pricing": "V", "n_cells": rep["n_cells"]},
                data_blocks=pool_blocks(),
                result_path=result_path,
                role="exploration",
            )
            n += 1
        done.add(s["id"])
    _write_marker(marker, done)
    return n


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
    ap.add_argument("--vmap", default=DEFAULT_VMAP)
    args = ap.parse_args(argv)
    try:
        if args.max_workers > 2:
            raise Refused("keep --max-workers <= 2")
        g = guard_views(args.view_dir or [])  # before any row is read
        _model, threshold, _names = e11.load_frozen_spec(args.artifact_dir)
        check_frozen_threshold(threshold)
    except Refused as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    tries_path = resolve_tries_path(args.tries_log)
    t0 = time.time()
    rows = collect_rows(g["roots"], g["pool"], args.artifact_dir, args.vmap, args.out_dir / "scratch", args.max_workers)
    write_rows(args.out_dir / ROWS_NAME, rows)
    rep = analyze(rows)
    rep["v_coverage"] = v_coverage(args.out_dir / "scratch" / "counts_virtual")
    rep["view_sha256"] = g["view_sha256"]
    rep["model_md5"] = fz._md5_of_file(args.artifact_dir / "model.txt")
    rep["wall_s"] = time.time() - t0
    rep["tries"] = {"logged": log_tries(rep, args.out_dir, tries_path), "log_path": str(tries_path)}
    (args.out_dir / RESULT_NAME).write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    md = render_md(rep)
    (args.out_dir / "report.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
