#!/usr/bin/env python3
"""EXP-013 candidate refit: EXP-012's freeze recipe on an EXPANDED exploration pool.

Exploration tooling. No result. The candidate it writes is frozen and
pre-registered later by the manager, not by this script.

The recipe is EXP-012's, unchanged (EXP/EXP-012-migrate-entry-model-refreeze-prereg.md
section 2; tools/exp011_freeze.py; tools/exp012_support.py): the same 18 ablated
features, LightGBM params and seed, label 1{pressure net > 0} on tp50_sl30, the same
execution, the same table build (max_workers=2, buffer_hours=24, max_home_hours=12), and
the threshold = the 90th percentile of the pooled out-of-fold scores (non-interpolating
index rule). Only the LODO fold count changes: N-fold over every pool day, i.e. the 9
built-in days plus the days of each verified `--extra-fast-view`.

Pool inputs: the three built-in clean views (--fast-dir, --oracle-insample-dir,
--oracle-live-dir, pinned and verified exactly as for EXP-012) and one or more extra
fast-format views (tools/exp013_pool.py: VIEW.sha256 verified, forbidden roots and
overlaps refused, hours inside the expansion block only).

Output: /data/mal/exp013-candidate/<run-id>/ (never ARTIFACTS/):
  model.txt model.md5 threshold.json features.json oof_scores.json train_manifest.json
  per_day_lodo.json per_day_lodo.md   per-day n / selected / flat+pressure mean SOL per trade + CI (compute_gate)
  screens.json screens.md             DEC-017 section 5: period transfer, September-only and source-only LODO, overlap with EXP-012
  manifest.json                       sha256 of every input VIEW.sha256 + every verified file, table md5, code commit
  table/table.jsonl (+ .md5, row_counts.json)

The per-day table uses the pooled threshold (threshold.json) on each day's out-of-fold
scores. It is a descriptive in-pool table, not evidence for promotion.

Run (see the MiScusi command in the PR): python -m tools.exp013_refit --run-id ID
  --fast-dir D --oracle-insample-dir D --oracle-live-dir D --extra-fast-view D [--extra-fast-view D ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import tools.exp011_freeze as fz
from tools.exp011_build_table import FORCED_BUFFER_HOURS, FORCED_MAX_HOME_HOURS, FORCED_MAX_WORKERS, build_table
from tools import exp013_screens
from tools.exp013_pool import EXPANSION_END, EXPANSION_START, add_extra_view_arg, json_dumps, load_extra_views

DEFAULT_OUT_ROOT = "/data/mal/exp013-candidate"
DEFAULT_EXP012_DIR = str(Path(__file__).resolve().parents[1] / "ARTIFACTS" / "exp012")
NO_HOLDOUT_STATEMENT = (
    "No row from ARTIFACTS/exp012/read/ or from the EXP-012 holdout [2026-09-03T12, 2026-09-09T12) was used. "
    "Inputs: the three built-in clean views and the extra views listed here, all hour-fenced to the exploration pool."
)
BOOTSTRAP = {"draws": 1000, "seed": 1, "pctile": 5}


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _view_files(root: Path) -> dict[str, str]:
    """{relative path: sha256 as listed in VIEW.sha256} (already verified to match the files)."""
    out: dict[str, str] = {}
    for line in (root / "VIEW.sha256").read_text(encoding="utf-8").splitlines():
        m = fz._SHA256_LINE.match(line)
        if m:
            out[os.path.normpath(m.group(2))] = m.group(1).lower()
    return out


def per_day_lodo_table(rows: Sequence[dict[str, Any]], oof: Sequence[dict[str, Any]], threshold: float, days: Sequence[str]) -> dict[str, Any]:
    """Per held-out day: n (OOF rows), selected (score >= threshold), flat and pressure mean
    SOL per trade with the 90% CI from the project gate's own bootstrap (compute_gate / book_stats).
    Plus a pooled "ALL" row carrying the full gate verdict for both fail models."""
    from tools.exp011_score import _book_trades, compute_gate
    from tools.paper_attention_promote import book_stats

    by_key = {(r["day"], r["mint"]): r for r in rows}

    def leg(sel: list[dict[str, Any]]) -> dict[str, Any]:
        if not sel:
            return {"n_selected": 0, "flat_mean_sol": None, "flat_mean_ci90_sol": None, "press_mean_sol": None, "press_mean_ci90_sol": None}
        gate = compute_gate(sel)
        press = book_stats(_book_trades(sel, "press"))
        return {
            "n_selected": len(sel),
            "flat_mean_sol": gate["mean_sol"],
            "flat_mean_ci90_sol": gate["mean_ci90_sol"],
            "press_mean_sol": press["mean_sol"],
            "press_mean_ci90_sol": press["mean_ci90_sol"],
        }

    per_day: list[dict[str, Any]] = []
    all_sel: list[dict[str, Any]] = []
    for d in days:
        scored = [o for o in oof if o["day"] == d]
        sel = [by_key[(o["day"], o["mint"])] for o in scored if o["score"] >= threshold]
        all_sel.extend(sel)
        per_day.append({"day": d, "n": len(scored), "selected": len(sel), **leg(sel)})
    pooled = {"day": "ALL", "n": len(oof), "selected": len(all_sel), **leg(all_sel)}
    gate_all = compute_gate(all_sel) if all_sel else None
    return {
        "schema": "exp013_per_day_lodo_v1",
        "note": "descriptive, in-pool, exploration only. Threshold = pooled OOF 90th percentile (threshold.json); not a promotion test.",
        "threshold": threshold,
        "bootstrap": BOOTSTRAP,
        "per_day": per_day,
        "pooled": pooled,
        "gate_pooled": gate_all,
    }


def _fmt(v: Any) -> str:
    return "n/a" if v is None else (f"[{v[0]:+.4f}, {v[1]:+.4f}]" if isinstance(v, (list, tuple)) else f"{v:+.4f}")


def per_day_markdown(doc: dict[str, Any]) -> str:
    lines = [
        "# EXP-013 per-day LODO (exploration, no result)",
        "",
        f"Threshold {doc['threshold']}. Mean SOL per trade, 90% CI from the gate bootstrap (1000 draws, seed 1).",
        "",
        "| Day | n | selected | flat mean | flat CI90 | press mean | press CI90 |",
        "| --- | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for r in [*doc["per_day"], doc["pooled"]]:
        lines.append(f"| {r['day']} | {r['n']} | {r['selected']} | {_fmt(r['flat_mean_sol'])} | {_fmt(r['flat_mean_ci90_sol'])} | {_fmt(r['press_mean_sol'])} | {_fmt(r['press_mean_ci90_sol'])} |")
    return "\n".join(lines) + "\n"


def assert_no_holdout(rows: Sequence[dict[str, Any]], days: Sequence[str], extra_views: Sequence[Any], roots: dict[str, Path], e12_dir: Path) -> list[str]:
    """Day-pinning assertions (DEC-017 section 3). Raises AssertionError; returns the list of checks that passed."""
    from tools.exp013_pool import _is_forbidden

    checks = []
    allowed = set(fz.DAYS_ALL) | {d for v in extra_views for d in v.days}
    assert {r["day"] for r in rows} <= allowed, "table has a row on a day that is not a built-in or extra-view day"
    checks.append("every table row's day is a built-in pool day or a day of a verified extra view")
    assert set(days) == allowed, "LODO day list differs from the pinned day list"
    checks.append("the LODO day list equals the pinned day list")
    for v in extra_views:
        assert EXPANSION_START <= v.hours[0] and v.hours[-1] < EXPANSION_END, f"{v.root}: hours outside the expansion block"
    checks.append(f"every extra-view hour lies in [{EXPANSION_START}, {EXPANSION_END}), which is disjoint from [2026-09-03T12, 2026-09-09T12)")
    for label, root in roots.items():
        assert _is_forbidden(os.path.realpath(str(root))) is None, f"{label} root is under a forbidden location"
    checks.append("no input root resolves into a forbidden location (realpath)")
    assert not any(p == "read" for p in Path(os.path.realpath(str(e12_dir))).parts), "EXP-012 read/ directory given"
    checks.append("only model.txt, threshold.json, features.json, oof_scores.json of ARTIFACTS/exp012 are read, never read/")
    return checks


def write_manifest(
    out_dir: Path,
    args_doc: dict[str, Any],
    roots: dict[str, Path],
    extra_views: Sequence[Any],
    table_md5: str,
    days: Sequence[str],
    wall_s: float,
    rows: Sequence[dict[str, Any]] = (),
    e12_dir: Path | None = None,
) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for label, root in roots.items():
        inputs[label] = {"root": str(root), "view_sha256_file_sha256": _sha256_of_file(root / "VIEW.sha256"), "files": _view_files(root)}
    extras = []
    for v in extra_views:
        d = v.describe()
        d["files"] = _view_files(v.root)
        extras.append(d)
    sha, dirty = fz._git_state(out_dir)
    doc = {
        "schema": "exp013_candidate_manifest_v1",
        "status": "exploration candidate, unfrozen, no result",
        "run_id": out_dir.name,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_commit": sha,
        "code_dirty": dirty,
        "args": args_doc,
        "recipe": {
            "feature_names": fz.FROZEN_FEATURE_NAMES,
            "lgb_params": fz.LGB_PARAMS,
            "seed": fz.SEED,
            "target_spec": fz.TARGET_SPEC_ID,
            "label": "1{pressure net > 0} on tp50_sl30",
            "threshold_pct": 0.90,
            "table_build": {"max_workers": FORCED_MAX_WORKERS, "buffer_hours": FORCED_BUFFER_HOURS, "max_home_hours": FORCED_MAX_HOME_HOURS},
            "lodo_days": list(days),
            "n_folds": len(days),
        },
        "table_md5": table_md5,
        "days_used": [
            {
                "day": d,
                "period": exp013_screens.period_of(d),
                "n_rows": sum(1 for r in rows if r["day"] == d),
                "pools": sorted({r["pool"] for r in rows if r["day"] == d}),
                "extra_view_roots": [str(v.root) for v in extra_views if d in v.days],
            }
            for d in days
        ],
        "no_holdout_statement": NO_HOLDOUT_STATEMENT,
        "no_holdout_assertions": assert_no_holdout(rows, days, extra_views, roots, e12_dir) if e12_dir is not None else [],
        "exp012_files_sha256": exp013_screens.e12_file_sha256(e12_dir) if e12_dir is not None else None,
        "builtin_pool_views": inputs,
        "extra_views": extras,
        "wall_s": wall_s,
    }
    (out_dir / "manifest.json").write_text(json_dumps(doc), encoding="utf-8")
    return doc


def run(args: argparse.Namespace) -> Path:
    out_dir = Path(args.out_root) / args.run_id
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SystemExit(f"{out_dir} already exists and is not empty")
    fz.assert_out_not_frozen_dir(out_dir)
    if not args.extra_fast_view:
        raise SystemExit("give at least one --extra-fast-view (without one this is just the EXP-012 freeze)")
    if not args.verify_view:
        raise SystemExit("--verify-view is required")
    roots = fz.resolve_roots(args)
    if any(r is None for r in roots.values()):
        raise SystemExit("give all three clean-view pool roots (--fast-dir, --oracle-insample-dir, --oracle-live-dir)")
    extra_views = load_extra_views(args.extra_fast_view, fz.DAYS_ALL)

    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    table = out_dir / "table" / "table.jsonl"
    counts = build_table(
        table,
        out_dir / "table" / "scratch",
        max_workers=FORCED_MAX_WORKERS,
        buffer_hours=FORCED_BUFFER_HOURS,
        max_home_hours=FORCED_MAX_HOME_HOURS,
        fast_dir=roots["fast"],
        insample_dir=roots["insample"],
        live_dir=roots["live"],
        verify_view=True,
        extra_views=extra_views,
    )
    shutil.rmtree(out_dir / "table" / "scratch", ignore_errors=True)

    rows, manifest = fz.load_table(table)
    days = fz.pool_days(manifest)
    model, thr, oof, manifest, wall_s, _nested = fz.freeze(table_path=table, run_nested_lodo=False)
    fz.write_outputs(out_dir, model, thr, oof, manifest, wall_s, nested_report=None, days=days)
    doc = per_day_lodo_table(rows, oof, thr["threshold"], days)
    (out_dir / "per_day_lodo.json").write_text(json_dumps(doc), encoding="utf-8")
    (out_dir / "per_day_lodo.md").write_text(per_day_markdown(doc), encoding="utf-8")
    e12_dir = Path(args.exp012_dir)
    screens = exp013_screens.run_screens(rows, oof, thr["threshold"], e12_dir)
    (out_dir / "screens.json").write_text(json_dumps(screens), encoding="utf-8")
    (out_dir / "screens.md").write_text(exp013_screens.screens_markdown(screens), encoding="utf-8")
    args_doc = {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()}
    write_manifest(out_dir, args_doc, {k: v for k, v in roots.items() if v is not None}, extra_views, counts["table_md5"], days, time.time() - t0, rows=rows, e12_dir=e12_dir)
    print(f"EXP-013 refit: wrote {out_dir} ({len(days)} folds, threshold={thr['threshold']:.6f})", file=sys.stderr, flush=True)
    return out_dir


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out-root", default=DEFAULT_OUT_ROOT)
    ap.add_argument("--exp012-dir", default=DEFAULT_EXP012_DIR, help="EXP-012's frozen artifact dir (model.txt, threshold.json, features.json, oof_scores.json only; never read/)")
    fz.add_root_args(ap)
    add_extra_view_arg(ap)
    args = ap.parse_args(argv)
    run(args)


if __name__ == "__main__":
    main()
