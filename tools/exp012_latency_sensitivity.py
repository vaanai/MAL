#!/usr/bin/env python3
"""EXP-012 entry-latency sensitivity. EXPLORATION, NOT EVIDENCE.

Question: how fast does the frozen EXP-012 migrate entry model's edge decay as
entry latency grows? The replay enters at the start of slot migration+1
(ENTRY_LAND_K = 1, bound "start"). The live fast feed lags >= ~1.2 s (>= 4
slots at ~3.7 slots/s), p90 more.

What this does
  1. One tape pass over the 9-day EXPLORATION pool (pools A/C/B, the clean
     views the EXP-012 freeze read), scoring every migration at every entry
     offset k in --ks (default 1,2,4,6,8,12). Same recipe as the freeze table
     (tools.exp011_build_table settings: 2 workers, buffer 24 h, home 12 h);
     the only change is `entry_land_k`, a sequence, so the tape is read once.
  2. Selection uses the freeze's stored OUT-OF-FOLD LODO scores
     (ARTIFACTS/exp012/oof_scores.json, 9 folds, each day scored by the model
     trained on the other 8 days): enter iff OOF score >= the frozen threshold
     (ARTIFACTS/exp012/threshold.json). The selection is therefore the
     decision the model made at k=1 timing, not re-scored per k, and is not
     in-sample. What moves with k is the execution: the fill, the entry price
     and the (unchanged-delay) exit.
  3. Per k: n entered, fill rate, flat and pressure mean SOL/trade, 90% CI
     (the gate's cluster bootstrap, 1,000 draws, seed 1) and ex-top-3 SOL via
     tools.exp011_score.compute_gate, and the unfiltered baseline at the same k.

Caveats stated in the output
  - Only the ENTRY slot is delayed. The exit delay inside the replay is still
    the frozen k=1 "start" (a live exit lags too, so real decay is >= this).
  - The pressure features inside a row are for the k in that row; the selection
    score is NOT recomputed per k (see 2).
  - Exploration-pool, 9 days, same days the model was frozen on (OOF removes
    the model's own-day fit, not the winner's curse of having chosen the cell).
    This is a sensitivity curve, never a promote.

Never reads the EXP-012 holdout, the backup block, or the EXP-011 block: the
roots go through tools.exp011_freeze.resolve_roots (fence + VIEW.sha256).

Full run (see the PR body):
  nice -n 19 /data/mal/venv/bin/python -m tools.exp012_latency_sensitivity --verify-view \
    --out-dir /data/mal/exp012-latency \
    --fast-dir /data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00 \
    --oracle-insample-dir /data/mal/clean-view/oracle-insample-2026-09-22_25 \
    --oracle-live-dir /data/mal/clean-view/oracle-live-2026-09-25_27
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import tools.exp011_freeze as fz
from tools.exp011_freeze import TARGET_SPEC_ID, add_root_args
from tools.exp011_score import _book_trades, compute_gate
from tools.exploration_entry_model import iter_rows_jsonl, run_all_features as run_all_features_a
from tools.exploration_entry_model_b2 import run_all_features_b
from tools.exploration_entry_model_b3 import run_all_features_c
from tools.paper_attention_promote import book_stats

DEFAULT_KS = (1, 2, 4, 6, 8, 12)
DEFAULT_ARTIFACT_DIR = Path(__file__).resolve().parent.parent / "ARTIFACTS" / "exp012"
SCRATCH_ROWS = "rows_by_k.jsonl"
# Beyond exp011_freeze.FORBIDDEN_ROOT_PREFIXES: the clean copies of the sealed blocks.
# Directory prefixes, except the second, which is a string prefix (fresh-0903, fresh-0828...).
EXTRA_FORBIDDEN_DIRS = ("/data/mal/blocks-clean",)
EXTRA_FORBIDDEN_STR = ("/data/mal/clean-view/fresh-",)
KEEP = ("mint", "day", "entry_land_k", "filled", "status", "gross", "flat", "press", "pool")


# --- root guard -----------------------------------------------------------------


def _forbidden_hit(rp: str) -> str | None:
    for bad in [os.path.realpath(p) for p in fz.FORBIDDEN_ROOT_PREFIXES] + list(EXTRA_FORBIDDEN_DIRS):
        if rp == bad or rp.startswith(bad.rstrip("/") + "/"):
            return bad
    for bad in EXTRA_FORBIDDEN_STR:
        if rp.startswith(bad):
            return bad
    return None


def guarded_roots(args: argparse.Namespace) -> dict[str, Path]:
    """All three roots and --verify-view are required (no module-default
    fallback). Each root is resolved with os.path.realpath (symlinks followed)
    BEFORE the forbidden-prefix check, then handed to the freeze's own
    resolve_roots (fence + VIEW.sha256 + pin)."""
    given = {"fast": args.fast_dir, "insample": args.oracle_insample_dir, "live": args.oracle_live_dir}
    missing = [k for k, v in given.items() if v is None]
    if missing:
        raise SystemExit(f"refusing: --fast-dir, --oracle-insample-dir and --oracle-live-dir are all required (missing: {', '.join(missing)}); there are no default roots")
    if not args.verify_view:
        raise SystemExit("refusing: --verify-view is required")
    real: dict[str, Path] = {}
    for label, root in given.items():
        rp = os.path.realpath(str(root))
        bad = _forbidden_hit(rp)
        if bad:
            raise SystemExit(f"refusing: {label} root {str(root)!r} resolves to {rp!r}, inside a reserved holdout location ({bad})")
        real[label] = Path(rp)
    args.fast_dir, args.oracle_insample_dir, args.oracle_live_dir = real["fast"], real["insample"], real["live"]
    out = fz.resolve_roots(args)
    return {k: v for k, v in out.items() if v is not None}


# --- heavy pass ---------------------------------------------------------------


def collect_rows(
    fast: Path | None,
    insample: Path | None,
    live: Path | None,
    ks: Sequence[int],
    scratch: Path,
    max_workers: int = 2,
    buffer_hours: int = 24,
    max_home_hours: int | None = 12,
) -> list[dict[str, Any]]:
    """TARGET_SPEC_ID rows for every pool and every k, slimmed to KEEP fields."""
    out: list[dict[str, Any]] = []
    common = dict(max_workers=max_workers, buffer_hours=buffer_hours, max_home_hours=max_home_hours, entry_land_k=list(ks))
    passes = (
        ("A", lambda: run_all_features_a(out_dir=scratch / "poolA", backfill=fast, **common)),
        ("C", lambda: run_all_features_c(out_dir=scratch / "poolC", root=insample, **common)),
        ("B", lambda: run_all_features_b(out_dir=scratch / "poolB", root=live, **common)),
    )
    for pool, fn in passes:
        print(f"latency pass: pool {pool}...", file=sys.stderr, flush=True)
        rows = [r for r in fn() if r["spec"] == TARGET_SPEC_ID]
        for r in rows:
            r["pool"] = pool
            out.append({key: r[key] for key in KEEP})
        del rows
    out.sort(key=lambda r: (r["entry_land_k"], r["day"], r["mint"]))
    return out


def write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# --- analysis (pure; tested on fixtures) ------------------------------------------


def load_oof(artifact_dir: Path) -> tuple[dict[str, float], float, dict[str, Any], dict[str, str]]:
    """({mint: OOF score}, frozen threshold, threshold.json, {mint: OOF day}). Refuses if the OOF
    scores are not stored: this tool does not fall back to in-sample scores."""
    oof_path = artifact_dir / "oof_scores.json"
    thr_path = artifact_dir / "threshold.json"
    if not oof_path.is_file() or not thr_path.is_file():
        raise SystemExit(f"{oof_path} / {thr_path} not found: OOF scores are required (no in-sample fallback in this tool)")
    doc = json.loads(oof_path.read_text(encoding="utf-8"))
    thr_doc = json.loads(thr_path.read_text(encoding="utf-8"))
    scores: dict[str, float] = {}
    days: dict[str, str] = {}
    for r in doc["rows"]:
        if r["mint"] in scores:
            raise SystemExit(f"duplicate mint in oof_scores.json: {r['mint']}")
        scores[r["mint"]] = float(r["score"])
        days[r["mint"]] = r["day"]
    return scores, float(thr_doc["threshold"]), thr_doc, days


def _side(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Flat and pressure stats for one book. compute_gate gives the flat leg
    (+ the pressure mean / ex-top-3); the pressure CI comes from book_stats on
    the pressure trades (the gate's own bootstrap, same draws and seed)."""
    n = len(rows)
    if n == 0:
        return {"n": 0, "filled": 0, "fill_rate": None, "flat": None, "press": None, "promote_gate": False}
    gate = compute_gate(rows)
    press = book_stats(_book_trades(rows, "press"))
    filled = sum(1 for r in rows if r["filled"])
    return {
        "n": n,
        "filled": filled,
        "fill_rate": filled / n,
        "flat": {"mean_sol": gate["mean_sol"], "ci90_sol": gate["mean_ci90_sol"], "ex_top3_sol": gate["total_ex_top3_sol"], "total_sol": gate["total_sol"]},
        "press": {"mean_sol": press["mean_sol"], "ci90_sol": press["mean_ci90_sol"], "ex_top3_sol": press["total_ex_top3_sol"], "total_sol": press["total_sol"]},
        "promote_gate": bool(gate["promote"]),
    }


def press_zero_crossing(by_k: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    """First measured k whose pressure mean is <= 0, with a linear interpolation
    between it and the previous k. Never extrapolates past the largest k."""
    ks = sorted(by_k)
    means = [(k, by_k[k]["entered"]["press"]["mean_sol"]) for k in ks if by_k[k]["entered"]["press"] is not None]
    if not means:
        return {"crosses": False, "note": "no entered rows"}
    if means[0][1] <= 0:
        return {"crosses": True, "first_k_nonpositive": means[0][0], "interpolated_k": None, "note": "already <= 0 at the smallest k"}
    for (k0, m0), (k1, m1) in zip(means, means[1:]):
        if m1 <= 0:
            return {"crosses": True, "first_k_nonpositive": k1, "interpolated_k": k0 + (k1 - k0) * m0 / (m0 - m1), "note": "linear between measured ks"}
    return {"crosses": False, "note": f"pressure mean stays > 0 through k={means[-1][0]} (no extrapolation)"}


def check_integrity(
    by_k_rows: Mapping[int, Sequence[Mapping[str, Any]]],
    scores: Mapping[str, float],
    threshold: float,
    thr_doc: Mapping[str, Any],
    oof_days: Mapping[str, str] | None = None,
) -> None:
    """Hard asserts (raise SystemExit): rows keyed (mint, k) with no repeat, the
    row count at every k equals the freeze's n_oof, the day of every row equals
    its OOF day, and the selected count at the smallest k equals the threshold
    file's n_selected_at_or_above_threshold."""
    n_oof = int(thr_doc["n_oof"])
    for k, rs in sorted(by_k_rows.items()):
        seen: set[tuple[str, int]] = set()
        for r in rs:
            key = (r["mint"], k)
            if key in seen:
                raise SystemExit(f"integrity: mint {r['mint']} repeats at k={k}")
            seen.add(key)
            if oof_days is not None and r["mint"] in oof_days and oof_days[r["mint"]] != r["day"]:
                raise SystemExit(f"integrity: mint {r['mint']} k={k} day {r['day']} != OOF day {oof_days[r['mint']]}")
        if len(rs) != n_oof:
            raise SystemExit(f"integrity: k={k} has {len(rs)} rows, expected n_oof={n_oof}")
    k0 = min(by_k_rows)
    n_sel = sum(1 for r in by_k_rows[k0] if scores.get(r["mint"], float("-inf")) >= threshold)
    want = int(thr_doc["n_selected_at_or_above_threshold"])
    if n_sel != want:
        raise SystemExit(f"integrity: {n_sel} rows selected at k={k0}, threshold file says {want}")


def analyze(
    rows: Sequence[Mapping[str, Any]],
    scores: Mapping[str, float],
    threshold: float,
    thr_doc: Mapping[str, Any] | None = None,
    oof_days: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    by_k_rows: dict[int, list[Mapping[str, Any]]] = {}
    for r in rows:
        by_k_rows.setdefault(int(r["entry_land_k"]), []).append(r)
    if thr_doc is not None and by_k_rows:
        check_integrity(by_k_rows, scores, threshold, thr_doc, oof_days)
    n_rows_min_k = len(by_k_rows[min(by_k_rows)]) if by_k_rows else 0
    by_k: dict[int, dict[str, Any]] = {}
    for k in sorted(by_k_rows):
        rs = by_k_rows[k]
        scored = [r for r in rs if r["mint"] in scores]
        entered = [r for r in scored if scores[r["mint"]] >= threshold]
        by_k[k] = {
            "n_rows": len(rs),
            "censored_vs_smallest_k": len(rs) < n_rows_min_k,
            "n_without_oof_score": len(rs) - len(scored),
            "entered": _side(entered),
            "baseline_unfiltered": _side(rs),
        }
    first = by_k[min(by_k)] if by_k else None
    checks: dict[str, Any] = {}
    if thr_doc is not None and first is not None:
        checks = {
            "n_oof_stored": thr_doc.get("n_oof"),
            "n_rows_at_smallest_k": first["n_rows"],
            "n_selected_stored": thr_doc.get("n_selected_at_or_above_threshold"),
            "n_selected_at_smallest_k": first["entered"]["n"],
        }
    return {
        "schema": "exp012_latency_sensitivity_v1",
        "status": "EXPLORATION, NOT EVIDENCE",
        "selection": "OUT-OF-FOLD (stored 9-fold LODO scores, ARTIFACTS/exp012/oof_scores.json); enter iff score >= frozen threshold",
        "threshold": threshold,
        "ci": "gate cluster bootstrap, 1000 draws, seed 1 (tools.paper_attention_promote.book_stats)",
        "caveats": [
            "only the ENTRY slot is delayed; the exit delay stays at the frozen k=1 'start', so live decay is >= this",
            "selection score is the stored OOF score (computed at k=1 timing), not recomputed per k",
            "exploration pool, same 9 days the model was frozen on; a sensitivity curve, never a promote",
        ],
        "checks": checks,
        "by_k": {str(k): v for k, v in by_k.items()},
        "press_zero_crossing": press_zero_crossing(by_k),
    }


def _f(v: float | None, digits: int = 5) -> str:
    return "n/a" if v is None else f"{v:.{digits}f}"


def _ci(ci: Sequence[float] | None) -> str:
    return "n/a" if not ci else f"[{ci[0]:.5f}, {ci[1]:.5f}]"


def render_md(rep: Mapping[str, Any]) -> str:
    lines = [
        "# EXP-012 entry-latency sensitivity",
        "",
        f"**{rep['status']}.** Selection: {rep['selection']}. Threshold {rep['threshold']}.",
        "",
        "Caveats:",
        *[f"- {c}" for c in rep["caveats"]],
        "",
        f"Checks: {json.dumps(rep['checks'])}",
        "",
        "Mean SOL per trade (0.5 SOL entries), 90% CI, ex-top-3 total SOL.",
        "",
        "| k | n_rows | book | n | fill | flat mean | flat CI90 | flat ex-top3 | press mean | press CI90 | press ex-top3 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for k, v in rep["by_k"].items():
        nr = f"{v['n_rows']}" + (" CENSORED (fewer rows than smallest k)" if v["censored_vs_smallest_k"] else "")
        for name, key in (("OOF-selected", "entered"), ("unfiltered", "baseline_unfiltered")):
            s = v[key]
            if not s["n"]:
                lines.append(f"| {k} | {nr} | {name} | 0 | n/a | | | | | | |")
                continue
            lines.append(
                f"| {k} | {nr} | {name} | {s['n']} | {s['fill_rate']:.3f} | {_f(s['flat']['mean_sol'])} | {_ci(s['flat']['ci90_sol'])} | {_f(s['flat']['ex_top3_sol'], 3)} "
                f"| {_f(s['press']['mean_sol'])} | {_ci(s['press']['ci90_sol'])} | {_f(s['press']['ex_top3_sol'], 3)} |"
            )
    z = rep["press_zero_crossing"]
    lines += ["", f"Pressure mean crosses 0: {json.dumps(z)}", ""]
    return "\n".join(lines)


# --- CLI -----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_root_args(ap)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    ap.add_argument("--ks", default=",".join(str(k) for k in DEFAULT_KS), help="comma-separated entry slot offsets")
    ap.add_argument("--max-workers", type=int, default=2)
    ap.add_argument("--buffer-hours", type=int, default=24)
    ap.add_argument("--max-home-hours", type=int, default=12)
    ap.add_argument("--reuse-rows", action="store_true", help=f"skip the tape pass and analyze OUT_DIR/{SCRATCH_ROWS} if it exists")
    args = ap.parse_args(argv)
    assert args.max_workers <= 2, "keep max-workers <= 2 (same memory budget as the table build)"
    ks = [int(x) for x in args.ks.split(",") if x.strip()]
    if len(set(ks)) != len(ks) or not ks:
        raise SystemExit("--ks must be distinct integers")
    scores, threshold, thr_doc, oof_days = load_oof(args.artifact_dir)
    rows_path = args.out_dir / SCRATCH_ROWS
    t0 = time.time()
    if args.reuse_rows and rows_path.is_file():
        rows = list(iter_rows_jsonl(rows_path))
    else:
        roots = guarded_roots(args)
        rows = collect_rows(
            roots["fast"], roots["insample"], roots["live"], ks, args.out_dir / "scratch",
            max_workers=args.max_workers, buffer_hours=args.buffer_hours, max_home_hours=(args.max_home_hours or None),
        )
        write_rows(rows_path, rows)
    rep = analyze(rows, scores, threshold, thr_doc, oof_days)
    rep["ks"] = ks
    rep["wall_s"] = time.time() - t0
    (args.out_dir / "latency_sensitivity.json").write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    md = render_md(rep)
    (args.out_dir / "latency_sensitivity.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
