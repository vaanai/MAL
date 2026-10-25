#!/usr/bin/env python3
"""p30_laneg_screen: runner for the P30 lane-G numeric grid (docs/research/p30-laneg-numeric/DECLARE.md). EXPLORATION ONLY, paper only, no edge claim.

Wraps tools.arena_screen (its guards, loader, pricer and statistics are imported, not copied) for ONE exploration block at a time, which the kit's own `run` refuses
(it wants the four canonical blocks together). Allowed blocks: explore-0814 (train) and fresh-0903 (validation, frozen one-cell rule only). Everything else is refused.

    python tools/p30_laneg_screen.py --block /data/mal/clean-view/explore-0814 --out DIR [--count-only] [--hours H ...]
    python tools/p30_laneg_screen.py --block /data/mal/clean-view/fresh-0903 --out DIR --only 'PULL-F1-A0|X1' --validation

Adds (report-only) per-cell kill columns of DECLARE section 9, the best cell's rank, and a White reality check over the 48 cells (DECLARE section 8).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from tools import arena_screen as S
from tools.p30_laneg_rule import LanegRule

TRAIN = f"{S.CLEAN_VIEW}/explore-0814"
VALID = f"{S.CLEAN_VIEW}/fresh-0903"
LAT = S.LAT_DECIDING
MODELS = ("flat", "press")
RC_B = 10_000
RC_SEED = 1
KEYS = {m: f"{LAT}|total|rent0|{m}" for m in MODELS}


def day_matrix(vals: dict[str, dict[str, tuple[np.ndarray, np.ndarray]]], cells: Sequence[str], model: str) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """(sum SOL, n) per cell x UTC day (columns = union of days)."""
    days = sorted({int(d) for c in cells for d in vals[c][model][0]})
    ix = {d: i for i, d in enumerate(days)}
    s = np.zeros((len(cells), len(days)))
    n = np.zeros((len(cells), len(days)))
    for ci, c in enumerate(cells):
        ds, ss = vals[c][model]
        for d, x in zip(ds, ss):
            s[ci, ix[int(d)]] += x
            n[ci, ix[int(d)]] += 1
    return s, n, days


def reality_check(s: np.ndarray, n: np.ndarray, B: int = RC_B, seed: int = RC_SEED) -> dict[str, Any]:
    """White reality check on the mean SOL per trade (ratio of sums), date-cluster bootstrap with the SAME day draw for every cell, each cell centred on its observed ratio.
    p = (1 + #{max_c centred_draw_c >= max_c observed_c}) / (1 + B). Cells without trades are dropped from the max."""
    keep = n.sum(1) > 0
    s, n = s[keep], n[keep]
    if s.shape[0] == 0:
        return {"p": None, "cells": 0, "B": B}
    obs = s.sum(1) / n.sum(1)
    rng = np.random.default_rng(seed)
    nd = s.shape[1]
    vmax = obs.max()
    ge = 0
    for b0 in range(0, B, 500):
        k = min(500, B - b0)
        idx = rng.integers(0, nd, size=(k, nd))
        sb = s[:, idx].sum(2)  # cells x k
        nb = n[:, idx].sum(2)
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(nb > 0, sb / nb, 0.0) - obs[:, None]
        ge += int((r.max(0) >= vmax).sum())
    return {"p": (1 + ge) / (1 + B), "cells": int(keep.sum()), "B": B, "seed": seed, "max_obs_mean_sol": float(vmax), "days": nd}


def cell_row(name: str, r: dict) -> dict:
    st = r.get("stats") or {}
    out: dict[str, Any] = {"cell": name, "n": r.get("attempts", 0)}
    ok_models = []
    for m in MODELS:
        s = st.get(KEYS[m])
        if s is None:
            out[m] = None
            ok_models.append(False)
            continue
        c = {"mean_sol": s["mean"], "mean_pct": s["mean_pct"], "ci_lo_trade": s["ci_lo_trade"], "ci_lo_date": s["ci_lo"], "total": s["total"], "ex_top3": s["ex_top3"],
             "ex_best_day": s["ex_best_day"], "pos_days": s["pos_days"], "days": s["days"], "pos_day_share": s["pos_days"] / s["days"] if s["days"] else None,
             "top_day_share": s["top_day_share"]}
        c["pass"] = bool(s["n"] >= S.MIN_N and s["days"] >= S.MIN_DAYS and s["ci_lo_trade"] > 0 and s["ex_top3"] > 0 and s["ex_best_day"] > 0)
        out[m] = c
        ok_models.append(c["pass"])
    out["pass_both"] = bool(all(ok_models))
    out["score"] = min(out[m]["mean_sol"] for m in MODELS) if all(out.get(m) for m in MODELS) else None
    out["kit_kill"], out["kit_kill_reasons"] = r.get("kill"), r.get("kill_reasons")
    sc = st.get(f"{LAT}|sc|rent0|flat"), st.get(f"{LAT}|sc|rent0|press")
    out["sc_mean_sol"] = [x["mean"] if x else None for x in sc]
    out["exits"], out["attempts_per_day"] = r.get("exits"), r.get("attempts_per_day")
    out["impact"] = r.get("impact")
    out["stake_1_0_total"] = {m: (r.get("stake_1_0_total_rent0_1p9") or {}).get(m) and (r["stake_1_0_total_rent0_1p9"][m]["mean"]) for m in MODELS} if r.get("stake_1_0_total_rent0_1p9") else None
    return out


def analyze(scr: S.Screen, cells: dict, B: int = RC_B) -> dict[str, Any]:
    names = [c.name for c in scr.cells]
    rows = [cell_row(n, cells[n]) for n in names]
    vals: dict[str, dict] = {}
    for ci, n in enumerate(names):
        if not scr.acc[ci]:
            vals[n] = {m: (np.zeros(0), np.zeros(0)) for m in MODELS}
            continue
        st = S._cat(scr.acc[ci])
        L = S.legs(st, S.STAKE_SOL * 1e9, LAT, "total", scr.cfg[S.RENT_DECIDING])
        day = st["T"] // 86400
        vals[n] = {m: (day, np.asarray(L[m], float) / 1e9) for m in MODELS}
    rc = {}
    for m in MODELS:
        s, nn, days = day_matrix(vals, names, m)
        rc[m] = reality_check(s, nn, B)
    ps = [rc[m]["p"] for m in MODELS if rc[m]["p"] is not None]
    rc["p_reported"] = max(ps) if ps else None
    scored = [r for r in rows if r["score"] is not None]
    scored.sort(key=lambda r: (-r["score"], -r["n"]))
    rank = {r["cell"]: i + 1 for i, r in enumerate(scored)}
    passing = [r for r in scored if r["pass_both"]]
    best = passing[0] if passing else None
    return {"rows": rows, "rank_by_min_mean": rank, "n_cells": len(names), "n_with_trades": len(scored),
            "n_positive_both_models": sum(1 for r in scored if r["score"] > 0), "n_pass_both": len(passing),
            "best": best, "best_rank_among_all": rank.get(best["cell"]) if best else None,
            "top_by_score": [r["cell"] for r in scored[:5]], "reality_check": rc,
            "verdict": ("SURVIVOR_FOUND" if best else "TRAIN_KILL: no cell passes (n>=100, >=5 days, CI90 LB>0, ex-top-3>0, ex-best-day>0) under both models")}


def fmt(x: Any, p: int = 4) -> str:
    return "n/a" if x is None else (f"{x:.{p}f}" if isinstance(x, float) else str(x))


def table_md(an: dict, meta: dict) -> str:
    h = ["# P30 lane-G numeric grid: result table (EXPLORATION ONLY, no edge claim, no book has cleared the promotion gate)", "",
         f"Block(s): {meta['blocks']}; rule sha256 {meta['rule_sha256'][:16]}; kit {meta['kit']}; run {meta['written_utc']}. Deciding leg 1.9 s, 0.25 SOL, rent 0, strand `total`.", "",
         f"Verdict: **{an['verdict']}**. Cells with trades: {an['n_with_trades']}/{an['n_cells']}; mean>0 under both models: {an['n_positive_both_models']}; pass both: {an['n_pass_both']}.",
         f"Reality check (date-cluster bootstrap, B={RC_B}, seed {RC_SEED}): flat p={fmt(an['reality_check']['flat']['p'])}, pressure p={fmt(an['reality_check']['press']['p'])}, reported (max) p={fmt(an['reality_check']['p_reported'])}.", "",
         "| cell | n | n/day | flat mean | flat LB90 | flat ex3 | flat exday | press mean | press LB90 | press ex3 | press exday | +days (flat/press) | pass | kit kill |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(an["rows"], key=lambda r: (r["score"] is None, -(r["score"] or 0))):
        f, p = r.get("flat"), r.get("press")
        if not f or not p:
            h.append(f"| {r['cell']} | {r['n']} | - | - | - | - | - | - | - | - | - | - | no trades | - |")
            continue
        h.append(f"| {r['cell']} | {r['n']} | {fmt(r['attempts_per_day'], 1)} | {fmt(f['mean_sol'])} | {fmt(f['ci_lo_trade'])} | {fmt(f['ex_top3'], 3)} | {fmt(f['ex_best_day'], 3)} | {fmt(p['mean_sol'])} | "
                 f"{fmt(p['ci_lo_trade'])} | {fmt(p['ex_top3'], 3)} | {fmt(p['ex_best_day'], 3)} | {f['pos_days']}/{f['days']} , {p['pos_days']}/{p['days']} | {'YES' if r['pass_both'] else 'no'} | {r['kit_kill']} |")
    if an["best"]:
        b = an["best"]
        h += ["", f"Best passing cell: `{b['cell']}` (rank {an['best_rank_among_all']} of {an['n_with_trades']} by min(flat, press) mean SOL/trade).", ""]
    return "\n".join(h) + "\n"


def check_blocks(blocks: Sequence[str], count_only: bool) -> list[tuple[str, str, list]]:
    if len(blocks) != 1:
        raise S.Refused("p30_one_block_per_run")
    out = []
    for b in blocks:
        kind = S.check_block_path(b, count_only)
        real = os.path.realpath(b)
        if real not in (os.path.realpath(TRAIN), os.path.realpath(VALID)) or kind != "clean":
            raise S.Refused(f"p30_block_refused: only explore-0814 and fresh-0903 (got {b})")
        out.append((b, kind, None))
    return out


def run(args: argparse.Namespace) -> dict:
    out = S.check_out_dir(args.out)
    if args.hours and not args.count_only:
        raise S.Refused("hours_subset_refused_when_pricing")
    only = [x for x in (args.only or [])]
    if args.validation and (os.path.realpath(args.block[0]) != os.path.realpath(VALID) or len(only) != 1):
        raise S.Refused("p30_validation_needs_fresh_0903_and_exactly_one_frozen_cell")
    if not args.validation and os.path.realpath(args.block[0]) == os.path.realpath(VALID):
        raise S.Refused("p30_fresh_0903_only_via_--validation_with_the_one_frozen_cell")
    spec = check_blocks(args.block, args.count_only)[0]
    files = S.discover(spec[0], "clean", args.hours)
    rule_path = Path(__file__).resolve().parent / "p30_laneg_rule.py"
    rule_sha = S.check_rule_source(rule_path)
    vpaths = S.check_vmap_paths(S.VMAP_DEFAULT)
    before = set(sys.modules)
    rule = LanegRule(only=only or None)
    S.check_loaded_modules(before)
    vmap = S.VMap(vpaths)
    Path(out).mkdir(parents=True, exist_ok=True)
    scr = S.Screen(rule, count_only=args.count_only, min_attempts_per_day=20.0, screen_p=False)
    name = os.path.relpath(os.path.realpath(spec[0]), S.CLEAN_VIEW)
    t0 = time.time()
    scr.run_block(0, name, S.iter_hours(files, name, "backfill", vmap, args.workers))
    S.check_loaded_modules(before)
    cells = scr.results()
    meta = {"schema": "p30_laneg_v1", "blocks": [name], "count_only": args.count_only, "hours": scr.hours, "rows_kept": scr.rows, "hours_subset": list(args.hours) if args.hours else None,
            "rule_sha256": rule_sha, "kit": S.kit_provenance(), "only": only or None, "validation": bool(args.validation), "counters": dict(scr.counters), "rule_counters": rule.state_ref.scratch.get("c"),
            "wall_s": time.time() - t0, "written_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "vmap": [{"path": v, "sha256": S.file_sha256(v)} for v in vpaths]}
    doc: dict[str, Any] = {"meta": meta, "cells": cells}
    if args.count_only:
        Path(out, "counts.json").write_text(json.dumps(doc, indent=1, default=float))
        Path(out, "COUNTS.md").write_text(S.counts_md(cells, meta))
    else:
        an = analyze(scr, cells, args.rc_draws)
        doc["p30"] = an
        Path(out, "results.json").write_text(json.dumps(doc, indent=1, default=float))
        Path(out, "TABLE.md").write_text(table_md(an, meta))
    return doc


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--block", action="append", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", nargs="*")
    ap.add_argument("--count-only", action="store_true")
    ap.add_argument("--only", action="append", help="restrict to these cell names (the frozen validation cell)")
    ap.add_argument("--validation", action="store_true")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--rc-draws", type=int, default=RC_B)
    args = ap.parse_args(argv)
    try:
        doc = run(args)
    except S.Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    m = doc["meta"]
    print(json.dumps({k: m[k] for k in ("blocks", "count_only", "hours", "rule_sha256", "wall_s", "counters", "rule_counters")}, indent=1, default=float))
    if "p30" in doc:
        a = doc["p30"]
        print(json.dumps({k: a[k] for k in ("verdict", "n_cells", "n_with_trades", "n_positive_both_models", "n_pass_both", "best_rank_among_all", "top_by_score", "reality_check")}, indent=1, default=float))
        if a["best"]:
            print("BEST", json.dumps(a["best"], default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
