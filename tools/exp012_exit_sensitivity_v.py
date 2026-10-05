#!/usr/bin/env python3
"""EXP-012 exit-variant sensitivity under V pricing, entry k=6, exit lag. EXPLORATION, NOT EVIDENCE.

Re-check of ARTIFACTS/lab/exp012-exit-sensitivity-2026-10-02.md, whose three conditions are outdated:
it ignored the PumpSwap virtual reserve V, entered at k=1, and filled exits with no delay. Here
  - pricing: V added to PumpSwap prints in-process (tools.pumpswap_virtual_adapter, as tools.exp012_latency_virtual);
  - entry: k=6 slots after the first PumpSwap print (the live operating point), bound "start", direct;
  - exit: `exit_land_k=2` (tools.exploration_exits): the sell fills at the state of the last print before
    trigger_slot + 2 (a time-cap exit fires at its deadline and lands 2 slots, 800 ms, later);
  - size 0.05 SOL, priority fee 505,000 lamports per side.
Selection is the freeze's stored OUT-OF-FOLD score at the frozen threshold, the same entered set for every
variant. Analysis is tools.exp012_exit_sensitivity's, unchanged: paired increment vs the frozen tp50_sl30
(both fail models, CI90, days positive) and the DEC-017 section 5 nested leave-one-day-out estimate.

Pre-declared family (at most 8; declared before any data was read):
  tp50_sl30 (frozen reference), tp50_sl20, tp50_sl40, tp40_sl30, tp75_sl30, timecap_10m_tp50_sl30,
  timecap_5m_tp50_sl30, and breakeven-after-tp25, which tools/exploration_exits.py does not implement:
  it is SKIPPED and listed, no new exit logic is added. No trailing or ladder variants (they lost clearly).

Never reads the EXP-012 holdout, the backup block, the EXP-011 block, the forward walk or 2026-10-02 onwards:
roots go through guarded_roots (all three roots and --verify-view required, realpath, forbidden prefixes).

Full run (see the PR body):
  nice -n 19 /data/mal/venv/bin/python -m tools.exp012_exit_sensitivity_v --verify-view \
    --out-dir /data/mal/exp012-exit-v --tries-log /data/mal/ops/tries-exp012-exit-v.jsonl \
    --fast-dir /data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00 \
    --oracle-insample-dir /data/mal/clean-view/oracle-insample-2026-09-22_25 \
    --oracle-live-dir /data/mal/clean-view/oracle-live-2026-09-25_27
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import tools.exp012_exit_sensitivity as es
import tools.exploration_entry_model as eem
from tools.exp011_freeze import TARGET_SPEC_ID, add_root_args
from tools.exp012_latency_sensitivity import DEFAULT_ARTIFACT_DIR, guarded_roots, load_oof, write_rows
from tools.exp012_latency_virtual import DEFAULT_VMAP, set_env
from tools.exploration_entry_model import iter_rows_jsonl
from tools.exploration_exits import build_specs

# --- declared before any data is read ----------------------------------------------------------
ENTRY_K = 6
EXIT_LAND_K = 2
SIZE_SOL = 0.05
FEE_LAMPORTS = 505_000
FROZEN_THRESHOLD = 0.8030766588450794
EXISTING_TRIES_ON_POOL = 54  # 18 earlier + 36 from tools.exp012_operating_point
TOOL = "tools.exp012_exit_sensitivity_v"
SCRATCH_ROWS = "rows_by_variant.jsonl"
TRIES_MARKER = "tries_logged.marker"
RESULT_NAME = "exit_sensitivity_v.json"
KEEP = es.KEEP
BANNER = "EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE EVIDENCE"
ENTRY_LABEL = f"k={ENTRY_K} (bound 'start', direct), V pricing, exit_land_k={EXIT_LAND_K}, size {SIZE_SOL} SOL, fee {FEE_LAMPORTS} lamports/side"

REQUESTED: tuple[tuple[str, str], ...] = (
    ("tp_sl_grid", TARGET_SPEC_ID),
    ("tp_sl_grid", "tpsl_tp50_sl20"),
    ("tp_sl_grid", "tpsl_tp50_sl40"),
    ("tp_sl_grid", "tpsl_tp40_sl30"),
    ("tp_sl_grid", "tpsl_tp75_sl30"),
    ("time_cap", "timecap_10m_tp50_sl30"),
    ("time_cap", "timecap_5m_tp50_sl30"),
    ("breakeven", "breakeven_after_tp25"),
)
assert len(REQUESTED) <= 8


def _tp40_sl30() -> dict[str, Any]:
    """Same dict shape as exploration_exits.build_specs() grid cells (type 'tpsl'); no new exit logic."""
    return {"id": "tpsl_tp40_sl30", "family": "tp_sl_grid", "type": "tpsl", "tp": 0.40, "sl": 0.30, "cap_ms": 30 * 60 * 1000, "desc": "+40% / -30%, 30 min cap"}


def resolve_variants(specs: Sequence[Mapping[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    have = list(build_specs() if specs is None else specs)
    if not any(s["id"] == "tpsl_tp40_sl30" for s in have):
        have.append(_tp40_sl30())
    return es.resolve_variants(have, REQUESTED)


# --- tape pass: score_one with this study's cell (runs inside the pool workers) -----------------


@contextlib.contextmanager
def entry_exit_patch() -> Iterator[None]:
    """Wrap eem.score_one so every call uses this study's entry k, size, fee and exit_land_k. Refuses a call
    that already sets any of them (nothing else may change the cell). Restores on exit."""
    orig = eem.score_one

    def wrapped(*a: Any, **kw: Any) -> Any:
        for key in ("size", "priority", "entry_land_k", "entry_bound"):
            if kw.get(key) is not None:
                raise RuntimeError(f"score_one was called with {key}={kw[key]!r}; this pass fixes the cell")
        if kw.get("exit_land_k"):
            raise RuntimeError("score_one was called with exit_land_k; this pass fixes the cell")
        kw.update(size=int(round(SIZE_SOL * 1_000_000_000)), priority=FEE_LAMPORTS, entry_land_k=ENTRY_K, exit_land_k=EXIT_LAND_K)
        return orig(*a, **kw)

    eem.score_one = wrapped
    try:
        yield
    finally:
        eem.score_one = orig


def _worker(which: str, *args: Any, **kw: Any) -> Any:
    from tools import pumpswap_virtual_adapter as ad

    with entry_exit_patch():
        return getattr(ad, f"worker_{which}")(*args, **kw)


# Module-level so a spawn pool can pickle them by reference.
def v_worker_a(*args: Any, **kw: Any) -> Any:
    return _worker("a", *args, **kw)


def v_worker_b(*args: Any, **kw: Any) -> Any:
    return _worker("b", *args, **kw)


def v_worker_c(*args: Any, **kw: Any) -> Any:
    return _worker("c", *args, **kw)


@contextlib.contextmanager
def patched_v_workers() -> Iterator[None]:
    import tools.exploration_entry_model_b2 as b2
    import tools.exploration_entry_model_b3 as b3

    saved = (eem.run_worker_a, b2.run_worker_b, b3.run_worker_c)
    eem.run_worker_a, b2.run_worker_b, b3.run_worker_c = v_worker_a, v_worker_b, v_worker_c
    try:
        yield
    finally:
        eem.run_worker_a, b2.run_worker_b, b3.run_worker_c = saved


def collect_rows(
    fast: Path,
    insample: Path,
    live: Path,
    specs: Sequence[Mapping[str, Any]],
    scratch: Path,
    vmap: str,
    max_workers: int = 2,
    buffer_hours: int = 24,
    max_home_hours: int | None = 12,
) -> list[dict[str, Any]]:
    from tools.exploration_entry_model_b2 import run_all_features_b
    from tools.exploration_entry_model_b3 import run_all_features_c

    if max_workers > 2:
        raise SystemExit("keep max-workers <= 2")
    set_env(vmap, scratch / "counts_virtual")
    ids = {s["id"] for s in specs}
    common = dict(max_workers=max_workers, buffer_hours=buffer_hours, max_home_hours=max_home_hours, specs=[dict(s) for s in specs])
    passes = (
        ("A", lambda: eem.run_all_features(out_dir=scratch / "poolA", backfill=fast, **common)),
        ("C", lambda: run_all_features_c(out_dir=scratch / "poolC", root=insample, **common)),
        ("B", lambda: run_all_features_b(out_dir=scratch / "poolB", root=live, **common)),
    )
    out: list[dict[str, Any]] = []
    with patched_v_workers():
        for pool, fn in passes:
            print(f"exit-V pass: pool {pool}...", file=sys.stderr, flush=True)
            for r in fn():
                if r["spec"] in ids:
                    r["pool"] = pool
                    out.append({key: r[key] for key in KEEP})
    out.sort(key=lambda r: (r["spec"], r["day"], r["mint"]))
    return out


# --- analysis ---------------------------------------------------------------------------------


def check_integrity_v(
    rows: Sequence[Mapping[str, Any]],
    scores: Mapping[str, float],
    threshold: float,
    thr_doc: Mapping[str, Any],
    oof_days: Mapping[str, str],
) -> dict[str, Any]:
    """Hard asserts (SystemExit). Unlike the k=1 tool, rows may be missing: an exit whose lag runs past the end of
    the tape is censored by the scorer. So: the reference has rows, mints are unique per variant and on their OOF
    day, and no variant has more rows than n_oof or more selected than the threshold file says. Returns row counts."""
    n_oof, want = int(thr_doc["n_oof"]), int(thr_doc["n_selected_at_or_above_threshold"])
    by: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["spec"], []).append(r)
    if TARGET_SPEC_ID not in by:
        raise SystemExit(f"integrity: reference {TARGET_SPEC_ID} has no rows")
    info: dict[str, Any] = {"n_oof": n_oof, "n_selected_expected": want, "rows_by_variant": {}}
    for spec, rs in sorted(by.items()):
        seen: set[str] = set()
        for r in rs:
            if r["mint"] in seen:
                raise SystemExit(f"integrity: mint {r['mint']} repeats in variant {spec}")
            seen.add(r["mint"])
            if r["mint"] in oof_days and oof_days[r["mint"]] != r["day"]:
                raise SystemExit(f"integrity: mint {r['mint']} variant {spec} day {r['day']} != OOF day {oof_days[r['mint']]}")
        n_sel = sum(1 for r in rs if scores.get(r["mint"], float("-inf")) >= threshold)
        if len(rs) > n_oof or n_sel > want:
            raise SystemExit(f"integrity: variant {spec} has {len(rs)} rows / {n_sel} selected, more than n_oof={n_oof} / {want}")
        info["rows_by_variant"][spec] = {"rows": len(rs), "selected": n_sel}
    return info


def optimism_gap(rep: Mapping[str, Any]) -> dict[str, Any]:
    """Best non-reference variant's pooled paired increment minus the nested held-out estimate: how much of the
    best-of-N pooled number the leave-one-day-out selection does not reproduce."""
    cands = [v for v in rep["variants"] if v.get("paired_vs_reference") and v["paired_vs_reference"]["n"]]
    nl = rep["nested_lodo"]
    if not cands or not nl.get("available") or not nl["pooled"]["n"]:
        return {"available": False}
    best = max(cands, key=lambda v: v["paired_vs_reference"]["press"]["mean_sol"])
    p = best["paired_vs_reference"]
    return {
        "available": True,
        "best_variant": best["id"],
        "best_pooled_press_increment": p["press"]["mean_sol"],
        "best_pooled_flat_increment": p["flat"]["mean_sol"],
        "nested_press_increment": nl["pooled"]["press"]["mean_sol"],
        "nested_flat_increment": nl["pooled"]["flat"]["mean_sol"],
        "gap_press": p["press"]["mean_sol"] - nl["pooled"]["press"]["mean_sol"],
        "gap_flat": p["flat"]["mean_sol"] - nl["pooled"]["flat"]["mean_sol"],
    }


def analyze(
    rows: Sequence[Mapping[str, Any]],
    scores: Mapping[str, float],
    threshold: float,
    variants: Sequence[Mapping[str, Any]],
    skipped: Sequence[Mapping[str, str]],
    thr_doc: Mapping[str, Any],
    oof_days: Mapping[str, str],
) -> dict[str, Any]:
    integrity = check_integrity_v(rows, scores, threshold, thr_doc, oof_days)
    rep = es.analyze(rows, scores, threshold, variants, skipped, thr_doc=None)
    n = len(rep["variants"])
    rep.update(
        schema="exp012_exit_sensitivity_v_v1",
        status=BANNER,
        entry=ENTRY_LABEL,
        size_label=f"{SIZE_SOL} SOL entries",
        integrity=integrity,
        exit_side_latency=(
            f"modeled: exit_land_k={EXIT_LAND_K}. An event exit (tp/sl) fills at the state of the last print before slot trigger+{EXIT_LAND_K}; "
            f"a time-cap exit fires at its deadline and lands {EXIT_LAND_K} slots ({EXIT_LAND_K * 400} ms) later. The 400 ms poll is not "
            "modeled beyond that fixed lag: the trigger is read from every print, so a real poll adds up to one more slot of delay on average."
        ),
        caveats=[
            f"cell: {ENTRY_LABEL}; only the exit rule changes between variants",
            "exploration pool, same 9 days the model was frozen on; OOF removes the model's own-day fit, not the winner's curse of the variant choice",
            "per-day means are small-n cells; a positive-day count is a coarse stability read",
            "costs still missing, as in the operating-point note: sell shortfall beyond the modeled lag, MEV, size-proportional costs",
        ],
        existing_tries_on_pool=EXISTING_TRIES_ON_POOL,
        cumulative_tries_on_pool=EXISTING_TRIES_ON_POOL + n,
    )
    rep["winners_curse"] = (
        f"{n} exit variants were tried on the same 9 days the model was frozen on, on top of {EXISTING_TRIES_ON_POOL} earlier tries "
        f"(cumulative {EXISTING_TRIES_ON_POOL + n}). The best of them is a selection, biased upward; the nested estimate is the honest number."
    )
    rep["tries"].update(n_requested=len(REQUESTED), n_skipped=len(skipped))
    rep["optimism_gap"] = optimism_gap(rep)
    return rep


def render_md(rep: Mapping[str, Any]) -> str:
    n = rep["n_variants_tried"]
    head = f"# **{rep['status']}: BEST OF {n}; cumulative tries on these 9 days: {rep['existing_tries_on_pool']} existing + {n} = {rep['cumulative_tries_on_pool']}**\n"
    body = es.render_md(rep).split("\n", 1)[1]  # drop the k=1 tool's title, keep the generated sections
    g = rep["optimism_gap"]
    gap = ["## Optimism gap", ""]
    if g["available"]:
        gap += [
            f"Best variant by pooled pressure increment: `{g['best_variant']}`.",
            "",
            "| | flat | pressure |",
            "| --- | --- | --- |",
            f"| best variant, pooled paired increment vs tp50_sl30 | {es._f(g['best_pooled_flat_increment'])} | {es._f(g['best_pooled_press_increment'])} |",
            f"| nested leave-one-day-out estimate | {es._f(g['nested_flat_increment'])} | {es._f(g['nested_press_increment'])} |",
            f"| optimism gap (pooled minus nested) | {es._f(g['gap_flat'])} | {es._f(g['gap_press'])} |",
            "",
        ]
    else:
        gap += ["Not available (no paired variant, or the nested estimate is unavailable).", ""]
    integ = rep["integrity"]
    cens = ["## Rows and censoring", "", f"n_oof {integ['n_oof']}, OOF-selected expected {integ['n_selected_expected']}.", "", "| variant | rows | selected |", "| --- | --- | --- |"]
    cens += [f"| `{k}` | {v['rows']} | {v['selected']} |" for k, v in integ["rows_by_variant"].items()]
    return head + body.rstrip("\n") + "\n\n" + "\n".join(gap) + "\n" + "\n".join(cens) + "\n"


# --- tries log --------------------------------------------------------------------------------


def _already_in_log(log: Path, variant: str, result_path: Path) -> bool:
    if not log.is_file():
        return False
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("tool") == TOOL and rec.get("config", {}).get("variant") == variant and rec.get("result_path") == str(result_path):
            return True
    return False


def log_tries(rep: Mapping[str, Any], out_dir: Path, tries_log: str | Path) -> int:
    """One result.v1 line per variant scored (role exploration), idempotent per out dir (marker + log scan)."""
    from tools import mal_result
    from tools.exp012_support import exploration_pool_blocks

    marker = out_dir / TRIES_MARKER
    done = es._read_marker(marker)
    if "*" in done:
        return 0
    result_path = out_dir / RESULT_NAME
    blocks = exploration_pool_blocks()
    n = 0
    for v in rep["variants"]:
        if v["id"] in done:
            continue
        if not _already_in_log(Path(tries_log), v["id"], result_path):
            mal_result.append_try(
                tries_log,
                tool=TOOL,
                config={
                    "experiment": "EXP-012 exit sensitivity V",
                    "variant": v["id"],
                    "family": v["family"],
                    "reference": v["reference"],
                    "entry_k": ENTRY_K,
                    "exit_land_k": EXIT_LAND_K,
                    "size_sol": SIZE_SOL,
                    "fee_lamports": FEE_LAMPORTS,
                    "pricing": "V",
                    "selection": "OOF",
                },
                data_blocks=blocks,
                result_path=result_path,
                role="exploration",
            )
            n += 1
        done.add(v["id"])
        es._write_marker(marker, done)
    return n


# --- CLI ---------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_root_args(ap)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    ap.add_argument("--vmap", default=DEFAULT_VMAP)
    ap.add_argument("--max-workers", type=int, default=2)
    ap.add_argument("--buffer-hours", type=int, default=24)
    ap.add_argument("--max-home-hours", type=int, default=12)
    ap.add_argument("--tries-log", default=None, help="absolute tries-log path (default: MAL_TRIES_LOG, else data/tries.jsonl)")
    ap.add_argument("--reuse-rows", action="store_true", help=f"skip the tape pass and analyze OUT_DIR/{SCRATCH_ROWS} if it exists")
    args = ap.parse_args(argv)
    if args.max_workers > 2:
        raise SystemExit("keep --max-workers <= 2")
    tries_path = es.resolve_tries_path(args.tries_log)
    variants, skipped = resolve_variants()
    scores, threshold, thr_doc, oof_days = load_oof(args.artifact_dir)
    if abs(threshold - FROZEN_THRESHOLD) > 1e-15:
        raise SystemExit(f"threshold.json threshold {threshold!r} != the frozen {FROZEN_THRESHOLD!r}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = args.out_dir / SCRATCH_ROWS
    t0 = time.time()
    if args.reuse_rows and rows_path.is_file():
        rows = list(iter_rows_jsonl(rows_path))
        es.check_rows_meta(args.out_dir, variants, rows)
    else:
        roots = guarded_roots(args)
        rows = collect_rows(
            roots["fast"], roots["insample"], roots["live"], [v["spec"] for v in variants], args.out_dir / "scratch", args.vmap,
            max_workers=args.max_workers, buffer_hours=args.buffer_hours, max_home_hours=(args.max_home_hours or None),
        )
        write_rows(rows_path, rows)
        es.write_rows_meta(args.out_dir, variants, roots)
    rep = analyze(rows, scores, threshold, variants, skipped, thr_doc, oof_days)
    rep["wall_s"] = time.time() - t0
    rep["tries"]["logged"] = log_tries(rep, args.out_dir, tries_path)
    rep["tries"]["log_path"] = str(tries_path)
    (args.out_dir / RESULT_NAME).write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    md = render_md(rep)
    (args.out_dir / "exit_sensitivity_v.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
