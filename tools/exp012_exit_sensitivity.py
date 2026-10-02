#!/usr/bin/env python3
"""EXP-012 exit-variant sensitivity. EXPLORATION, NOT EVIDENCE.

Question: how much of the frozen EXP-012 migrate entry model's result depends on
the frozen exit (tp50_sl30, 30 min cap)? Entry stays the frozen execution
(ENTRY_LAND_K = 1, bound "start"); only the exit rule changes.

What this does
  1. One tape pass over the 9-day EXPLORATION pool (pools A/C/B, the clean views
     the EXP-012 freeze read; same recipe and settings as
     tools.exp012_latency_sensitivity: 2 workers, buffer 24 h, home 12 h). Every
     migration is scored under every exit variant in VARIANTS in that one pass.
  2. Selection is the freeze's stored OUT-OF-FOLD LODO score
     (ARTIFACTS/exp012/oof_scores.json): enter iff score >= the frozen threshold
     (ARTIFACTS/exp012/threshold.json). The same entered set for every variant;
     only the exit differs.
  3. Per variant: n entered, fill rate, flat and pressure mean SOL/trade, 90% CI
     (the gate's cluster bootstrap, 1,000 draws, seed 1) and ex-top-3 SOL, the
     unfiltered baseline, and PER-DAY pressure means with the number of positive
     days, so variants compare on day stability and not only on the pooled mean.

Exit families come from tools.exploration_exits.build_specs() only. A requested
variant that is not implemented there is listed under "skipped" and not scored;
no new exit logic lives in this tool.

Winner's curse: the report ranks the variants, and the best of N tried variants
on the same 9 days the model was frozen on is a selection, not a result. N is
printed with the ranking. This is a sensitivity table, never a promote.

Never reads the EXP-012 holdout, the backup block, the EXP-011 block or the
forward walk: the roots go through the same guard as the latency tool (all three
roots and --verify-view required, realpath, forbidden prefixes, then
tools.exp011_freeze.resolve_roots).

Full run (see the PR body):
  nice -n 19 /data/mal/venv/bin/python -m tools.exp012_exit_sensitivity --verify-view \
    --out-dir /data/mal/exp012-exit \
    --fast-dir /data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00 \
    --oracle-insample-dir /data/mal/clean-view/oracle-insample-2026-09-22_25 \
    --oracle-live-dir /data/mal/clean-view/oracle-live-2026-09-25_27
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.exp011_freeze import TARGET_SPEC_ID, add_root_args
from tools.exp011_score import _book_trades, compute_gate
from tools.exp012_latency_sensitivity import DEFAULT_ARTIFACT_DIR, _ci, _f, guarded_roots, load_oof, write_rows
from tools.exploration_entry_model import iter_rows_jsonl, run_all_features as run_all_features_a
from tools.exploration_entry_model_b2 import run_all_features_b
from tools.exploration_entry_model_b3 import run_all_features_c
from tools.exploration_exits import build_specs
from tools.paper_attention_promote import book_stats

SCRATCH_ROWS = "rows_by_variant.jsonl"
TRIES_MARKER = "tries_logged.marker"  # one tries-log line per variant, once per out dir
KEEP = ("mint", "day", "spec", "filled", "status", "gross", "flat", "press", "pool")

# Requested variants, in report order: (family, spec id). Resolved against build_specs().
REQUESTED: tuple[tuple[str, str], ...] = (
    *(("tp_sl_grid", f"tpsl_tp{tp}_sl{sl}") for tp in (30, 50, 75, 100) for sl in (20, 30, 40)),
    *(("time_cap", f"timecap_{m}m_tp50_sl30") for m in (10, 30, 60)),
    *(("trailing_stop", f"trail_{t}") for t in (20, 30, 40)),
    *(("partial_ladder", f"ladder_take{t}_trail20") for t in (50, 100, 150, 200)),
)


def _same_exit(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return all(a.get(k) == b.get(k) for k in ("type", "tp", "sl", "cap_ms", "trail", "activation", "take", "trail_rem"))


def resolve_variants(
    specs: Sequence[Mapping[str, Any]] | None = None, requested: Sequence[tuple[str, str]] = REQUESTED
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """(variants to score, skipped). A requested id that exploration_exits does not
    implement is skipped; one that is the same exit as the reference under another
    id (the 30 min time cap) is skipped as an alias so it does not inflate the
    number of variants tried."""
    by_id = {s["id"]: s for s in (build_specs() if specs is None else specs)}
    ref = by_id[TARGET_SPEC_ID]
    variants: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for family, sid in requested:
        spec = by_id.get(sid)
        if spec is None:
            skipped.append({"id": sid, "family": family, "reason": "not implemented in tools/exploration_exits.py"})
        elif sid != TARGET_SPEC_ID and _same_exit(spec, ref):
            skipped.append({"id": sid, "family": family, "reason": f"identical exit to the reference {TARGET_SPEC_ID}"})
        else:
            variants.append({"id": sid, "family": family, "desc": spec["desc"], "reference": sid == TARGET_SPEC_ID, "spec": dict(spec)})
    if not any(v["reference"] for v in variants):
        raise SystemExit(f"reference variant {TARGET_SPEC_ID} missing from the variant list")
    return variants, skipped


# --- heavy pass ---------------------------------------------------------------


def collect_rows(
    fast: Path | None,
    insample: Path | None,
    live: Path | None,
    specs: Sequence[Mapping[str, Any]],
    scratch: Path,
    max_workers: int = 2,
    buffer_hours: int = 24,
    max_home_hours: int | None = 12,
) -> list[dict[str, Any]]:
    """Rows for every pool and every spec in `specs` (frozen k=1 'start' entry), slimmed to KEEP."""
    ids = {s["id"] for s in specs}
    out: list[dict[str, Any]] = []
    common = dict(max_workers=max_workers, buffer_hours=buffer_hours, max_home_hours=max_home_hours, specs=[dict(s) for s in specs])
    passes = (
        ("A", lambda: run_all_features_a(out_dir=scratch / "poolA", backfill=fast, **common)),
        ("C", lambda: run_all_features_c(out_dir=scratch / "poolC", root=insample, **common)),
        ("B", lambda: run_all_features_b(out_dir=scratch / "poolB", root=live, **common)),
    )
    for pool, fn in passes:
        print(f"exit pass: pool {pool}...", file=sys.stderr, flush=True)
        rows = [r for r in fn() if r["spec"] in ids]
        for r in rows:
            r["pool"] = pool
            out.append({key: r[key] for key in KEEP})
        del rows
    out.sort(key=lambda r: (r["spec"], r["day"], r["mint"]))
    return out


# --- analysis (pure; tested on fixtures) ------------------------------------------


def _leg(stats: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "mean_sol": stats["mean_sol"],
        "ci90_sol": stats["mean_ci90_sol"],
        "ex_top3_sol": stats["total_ex_top3_sol"],
        "total_sol": stats["total_sol"],
        "days": [{"day": d["day"], "n": d["n"], "mean_sol": d["mean_sol"]} for d in stats["days"]],
        "days_positive": stats["days_positive"],
        "n_days": stats["n_days"],
    }


def side(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Flat and pressure stats for one book, as the latency tool's _side (same
    compute_gate / book_stats calls) plus the per-day means and positive-day counts."""
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
        "flat": _leg(gate),
        "press": _leg(press),
        "promote_gate": bool(gate["promote"]),
    }


FAST_POOL = "A"  # pool A is the fast-source tape; C and B are Oracle sources
FAST_ONLY_LABEL = "all-days selection rule, scored on fast days (about 3-4 days, coarse)"
EXIT_LATENCY_NOT_IMPLEMENTED = (
    "not implemented: tools/exploration_exits.py fixes the exit's landing delay to its entry constants "
    "(_delayed(..., ENTRY_LAND_K, ENTRY_BOUND, ENTRY_BOUND)) and a time-cap exit has no delay at all; "
    "there is no parameter for a delayed exit fill, and this PR does not add exit logic. "
    "The k in {1, 4, 8} exit-side check for the reference and the top-3 variants is not run."
)


def _paired_leg(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    st = book_stats(_book_trades(rows, key))
    n_days = st["n_days"]
    return {
        "mean_sol": st["mean_sol"],
        "ci90_sol": st["mean_ci90_sol"],
        "total_sol": st["total_sol"],
        "days_positive": st["days_positive"],
        "n_days": n_days,
        "share_days_positive": (st["days_positive"] / n_days) if n_days else None,
        "days": [{"day": d["day"], "n": d["n"], "mean_sol": d["mean_sol"]} for d in st["days"]],
    }


def paired_side(diff_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per-mint paired difference (variant net - reference net, lamports in the
    rows' dflat / dpress), same cluster bootstrap as the gate (1,000 draws, seed 1)."""
    if not diff_rows:
        return {"n": 0, "flat": None, "press": None}
    return {"n": len(diff_rows), "flat": _paired_leg(diff_rows, "dflat"), "press": _paired_leg(diff_rows, "dpress")}


def paired_diffs(
    by_spec_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    scores: Mapping[str, float],
    threshold: float,
    variants: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """{variant id: one row per OOF-selected mint present in both the variant and
    the reference: mint, day, pool, dflat, dpress}. The reference maps to zeros."""
    def entered(spec_id: str) -> dict[str, Mapping[str, Any]]:
        return {r["mint"]: r for r in by_spec_rows.get(spec_id, []) if scores.get(r["mint"], float("-inf")) >= threshold}

    ref = entered(TARGET_SPEC_ID)
    out: dict[str, list[dict[str, Any]]] = {}
    # pairwise: each variant against the reference on the mints both have a row for
    for v in variants:
        vr = entered(v["id"])
        out[v["id"]] = [
            {"mint": m, "day": ref[m]["day"], "pool": ref[m]["pool"], "dflat": vr[m]["flat"] - ref[m]["flat"], "dpress": vr[m]["press"] - ref[m]["press"]}
            for m in sorted(ref)
            if m in vr
        ]
    return out


def common_mint_set(diffs: Mapping[str, Sequence[Mapping[str, Any]]], ids: Sequence[str]) -> set[str]:
    """Mints with a paired row in EVERY variant of `ids` (each already paired with the reference)."""
    sets = [{r["mint"] for r in diffs[i]} for i in ids]
    return set.intersection(*sets) if sets else set()


def nested_lodo(
    diffs: Mapping[str, Sequence[Mapping[str, Any]]],
    order: Sequence[str],
    excluded_censored: Mapping[str, int] | None = None,
    n_ref_entered: int | None = None,
) -> dict[str, Any]:
    """Nested leave-one-day-out selection of the exit, on the COMMON mint set: only
    mints that have a row in every candidate variant and the reference are used,
    for the choice and for the held-out score. CENSORED variants are not
    candidates (`excluded_censored` = {variant id: mints lost to censoring}).

    For each held-out day d: pick the variant (the reference, whose paired mean is
    0, is a candidate) with the best pooled PRESSURE paired mean over the other
    days' OOF-selected mints, then score that pick on day d (its paired
    difference to the reference on d's mints). Held-out differences are pooled
    over days and reported under both fail models, plus on fast-source mints
    (pool A) only, with the gate's bootstrap and per-day positive share.
    Ties go to the earlier variant in `order` (the reference is first)."""
    common = common_mint_set(diffs, list(order))
    diffs = {k: [r for r in diffs[k] if r["mint"] in common] for k in order}
    excl = dict(excluded_censored or {})
    days = sorted({r["day"] for rs in diffs.values() for r in rs})
    if len(days) < 2:
        return {"available": False, "reason": "needs at least 2 days", "common_set_size": len(common), "excluded_censored": excl}
    held: list[dict[str, Any]] = []
    choices: dict[str, str] = {}
    for d in days:
        best_id, best_mean = order[0], float("-inf")
        for vid in order:
            others = [r["dpress"] for r in diffs[vid] if r["day"] != d]
            m = (sum(others) / len(others)) if others else float("-inf")
            if m > best_mean:
                best_id, best_mean = vid, m
        choices[d] = best_id
        held.extend(dict(r, chosen=best_id) for r in diffs[best_id] if r["day"] == d)
    fast = [r for r in held if r["pool"] == FAST_POOL]
    counts = {vid: sum(1 for c in choices.values() if c == vid) for vid in order}
    return {
        "available": True,
        "selection_rule": "per held-out day: variant with the best pooled pressure paired mean on the other days (reference = 0 is a candidate); scored on the held-out day",
        "n_days": len(days),
        "common_set_size": len(common),
        "n_ref_entered": n_ref_entered,
        "candidates": list(order),
        "excluded_censored": excl,
        "fast_only_label": FAST_ONLY_LABEL,
        "choices_by_day": [{"day": d, "chosen": choices[d]} for d in days],
        "times_chosen": counts,
        "pooled": paired_side(held),
        "fast_only": paired_side(fast),
    }


def check_integrity(
    by_spec_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    scores: Mapping[str, float],
    threshold: float,
    thr_doc: Mapping[str, Any],
    oof_days: Mapping[str, str] | None = None,
    allow_censored: bool = False,
) -> None:
    """Hard asserts (SystemExit): per variant, unique mints, exactly n_oof rows
    (8,801 in the real run), each row on its OOF day, and exactly the threshold
    file's n_selected_at_or_above_threshold (881) selected; every variant covers
    the same mint set as the reference.

    allow_censored (opt-in, --allow-censored): a NON-reference variant may have
    fewer rows than n_oof, because an exit whose deadline runs past the end of
    the tape is omitted by the scorer (the 60 min cap near the pool's last
    hour). The reference must still be complete; the variants are then flagged
    CENSORED in the report. Rows are never invented and never more than n_oof."""
    n_oof = int(thr_doc["n_oof"])
    want = int(thr_doc["n_selected_at_or_above_threshold"])
    ref_mints: set[str] | None = None
    for spec_id, rs in sorted(by_spec_rows.items()):
        seen: set[str] = set()
        for r in rs:
            if r["mint"] in seen:
                raise SystemExit(f"integrity: mint {r['mint']} repeats in variant {spec_id}")
            seen.add(r["mint"])
            if oof_days is not None and r["mint"] in oof_days and oof_days[r["mint"]] != r["day"]:
                raise SystemExit(f"integrity: mint {r['mint']} variant {spec_id} day {r['day']} != OOF day {oof_days[r['mint']]}")
        relaxed = allow_censored and spec_id != TARGET_SPEC_ID
        if len(rs) != n_oof and not (relaxed and len(rs) < n_oof):
            raise SystemExit(f"integrity: variant {spec_id} has {len(rs)} rows, expected n_oof={n_oof}")
        n_sel = sum(1 for r in rs if scores.get(r["mint"], float("-inf")) >= threshold)
        if n_sel != want and not (relaxed and n_sel < want):
            raise SystemExit(f"integrity: {n_sel} rows selected in variant {spec_id}, threshold file says {want}")
        if spec_id == TARGET_SPEC_ID:
            ref_mints = seen
    if ref_mints is None:
        raise SystemExit(f"integrity: reference variant {TARGET_SPEC_ID} has no rows")
    for spec_id, rs in by_spec_rows.items():
        got = {r["mint"] for r in rs}
        if got != ref_mints and not (allow_censored and got <= ref_mints):
            raise SystemExit(f"integrity: variant {spec_id} mint set differs from the reference")


def analyze(
    rows: Sequence[Mapping[str, Any]],
    scores: Mapping[str, float],
    threshold: float,
    variants: Sequence[Mapping[str, Any]],
    skipped: Sequence[Mapping[str, str]] = (),
    thr_doc: Mapping[str, Any] | None = None,
    oof_days: Mapping[str, str] | None = None,
    allow_censored: bool = False,
) -> dict[str, Any]:
    by_spec_rows: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        by_spec_rows.setdefault(r["spec"], []).append(r)
    missing = [v["id"] for v in variants if v["id"] not in by_spec_rows]
    if missing:  # a variant with 0 rows is an error, with or without --allow-censored
        raise SystemExit(f"integrity: no rows for variant(s) {missing}")
    if thr_doc is not None:
        check_integrity(by_spec_rows, scores, threshold, thr_doc, oof_days, allow_censored)
    n_ref = len(by_spec_rows.get(TARGET_SPEC_ID, []))
    out: list[dict[str, Any]] = []
    for v in variants:
        rs = by_spec_rows.get(v["id"], [])
        scored = [r for r in rs if r["mint"] in scores]
        entered = [r for r in scored if scores[r["mint"]] >= threshold]
        out.append(
            {
                "id": v["id"],
                "family": v["family"],
                "desc": v["desc"],
                "reference": bool(v["reference"]),
                "n_rows": len(rs),
                "censored_vs_reference": len(rs) < n_ref,
                "n_without_oof_score": len(rs) - len(scored),
                "entered": side(entered),
                "baseline_unfiltered": side(rs),
            }
        )
    ranked = sorted(
        (o for o in out if o["entered"]["press"] is not None),
        key=lambda o: o["entered"]["press"]["mean_sol"],
        reverse=True,
    )
    for i, o in enumerate(ranked, start=1):
        o["rank_press_mean"] = i
    for o in out:
        o.setdefault("rank_press_mean", None)
    diffs = paired_diffs(by_spec_rows, scores, threshold, variants)
    ref_rows = by_spec_rows.get(TARGET_SPEC_ID, [])
    ref_mint_day = {r["mint"]: r["day"] for r in ref_rows}
    n_ref_entered = sum(1 for r in ref_rows if scores.get(r["mint"], float("-inf")) >= threshold)
    for o in out:
        if o["reference"]:
            o["paired_vs_reference"] = None
            continue
        p = paired_side(diffs[o["id"]])
        p["n_common_with_reference"] = p["n"]
        p["n_lost_to_censoring"] = n_ref_entered - p["n"]
        o["paired_vs_reference"] = p
        have = {r["mint"] for r in by_spec_rows.get(o["id"], [])}
        by_day: dict[str, int] = {}
        for m, d in ref_mint_day.items():
            if m not in have:
                by_day[d] = by_day.get(d, 0) + 1
        if by_day:
            o["censored_rows_by_day"] = dict(sorted(by_day.items()))
    order = [TARGET_SPEC_ID] + [v["id"] for v in variants if v["id"] != TARGET_SPEC_ID and not next(o for o in out if o["id"] == v["id"])["censored_vs_reference"]]
    excluded = {o["id"]: n_ref_entered - o["paired_vs_reference"]["n"] for o in out if o["censored_vs_reference"] and not o["reference"]}
    nested = nested_lodo({k: diffs[k] for k in order if k in diffs}, [k for k in order if k in diffs], excluded, n_ref_entered)
    return {
        "schema": "exp012_exit_sensitivity_v1",
        "status": "EXPLORATION, NOT EVIDENCE",
        "selection": "OUT-OF-FOLD (stored 9-fold LODO scores, ARTIFACTS/exp012/oof_scores.json); enter iff score >= frozen threshold; same entered set for every variant",
        "entry": "frozen: k=1, bound 'start', direct, 0.5 SOL",
        "threshold": threshold,
        "ci": "gate cluster bootstrap, 1000 draws, seed 1 (tools.paper_attention_promote.book_stats)",
        "n_variants_tried": len(out),
        "reference": TARGET_SPEC_ID,
        "skipped": list(skipped),
        "winners_curse": (
            f"{len(out)} exit variants were tried on the same 9 days the model was frozen on. Picking the best of them "
            "is a selection: its mean is biased upward and the rank below is not evidence. Compare variants on day "
            "stability and CI width, not on the top row."
        ),
        "caveats": [
            "only the exit rule changes; entry is the frozen k=1 'start'",
            "exploration pool, same 9 days the model was frozen on; OOF removes the model's own-day fit, not the winner's curse of the variant choice",
            "per-day means are small-n cells (about 100 entered trades per day on average); a positive-day count is a coarse stability read",
        ],
        "tries": {
            "n_variants_tried": len(out),
            "n_requested": len(REQUESTED),
            "n_skipped": len(skipped),
            "also_run": "nested-LODO selection over the same variants (one selection procedure, not a variant)",
        },
        "nested_lodo": nested,
        "exit_side_latency": EXIT_LATENCY_NOT_IMPLEMENTED,
        "variants": out,
    }


def render_md(rep: Mapping[str, Any]) -> str:
    vs = rep["variants"]
    lines = [
        "# EXP-012 exit-variant sensitivity",
        "",
        f"**{rep['status']}.** Entry: {rep['entry']}. Selection: {rep['selection']}. Threshold {rep['threshold']}.",
        "",
        f"**Winner's curse.** {rep['winners_curse']} Variants tried: {rep['n_variants_tried']}.",
        "",
        "Caveats:",
        *[f"- {c}" for c in rep["caveats"]],
        "",
        "Mean SOL per trade (0.5 SOL entries), 90% CI, ex-top-3 total SOL. REF = the frozen tp50_sl30. Rank is by pressure mean among the variants tried.",
        "",
        "| rank | variant | n | fill | flat mean | flat CI90 | flat ex-top3 | press mean | press CI90 | press ex-top3 | press days+ | unfiltered press mean |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for v in vs:
        name = f"`{v['id']}`" + (" **REF**" if v["reference"] else "") + (f" CENSORED ({v['n_rows']} rows)" if v["censored_vs_reference"] else "")
        s, b = v["entered"], v["baseline_unfiltered"]
        bp = _f(b["press"]["mean_sol"]) if b["press"] else "n/a"
        rk = f"{v['rank_press_mean']} of {rep['n_variants_tried']}" if v["rank_press_mean"] is not None else "n/a"
        if not s["n"]:
            lines.append(f"| {rk} | {name} | 0 | n/a | | | | | | | | {bp} |")
            continue
        lines.append(
            f"| {rk} | {name} | {s['n']} | {s['fill_rate']:.3f} | {_f(s['flat']['mean_sol'])} | {_ci(s['flat']['ci90_sol'])} | {_f(s['flat']['ex_top3_sol'], 3)} "
            f"| {_f(s['press']['mean_sol'])} | {_ci(s['press']['ci90_sol'])} | {_f(s['press']['ex_top3_sol'], 3)} "
            f"| {s['press']['days_positive']}/{s['press']['n_days']} | {bp} |"
        )
    days = sorted({d["day"] for v in vs if v["entered"]["press"] for d in v["entered"]["press"]["days"]})
    lines += ["", "Per-day pressure mean SOL/trade, OOF-selected (n in parentheses).", "", "| variant | " + " | ".join(days) + " |", "| --- | " + " | ".join("---" for _ in days) + " |"]
    for v in vs:
        p = v["entered"]["press"]
        cells = {d["day"]: f"{d['mean_sol']:+.4f} ({d['n']})" for d in p["days"]} if p else {}
        lines.append(f"| `{v['id']}`{' **REF**' if v['reference'] else ''} | " + " | ".join(cells.get(d, "") for d in days) + " |")
    lines += [
        "",
        "## Paired increment vs the reference",
        "",
        "Per-mint difference (variant net minus tp50_sl30 net) on the same OOF-selected mints, mean SOL/trade with the gate's 90% CI "
        "(1,000 draws, seed 1) and the share of days whose mean difference is positive, under both fail models.",
        "",
        "| variant | paired n (common with ref) | lost to censoring | flat diff | flat CI90 | flat days+ | press diff | press CI90 | press days+ |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for v in vs:
        p = v["paired_vs_reference"]
        if p is None:
            lines.append(f"| `{v['id']}` **REF** | | | 0 by construction | | | 0 by construction | | |")
        elif not p["n"]:
            lines.append(f"| `{v['id']}` | 0 | {p['n_lost_to_censoring']} | n/a | | | n/a | | |")
        else:
            lines.append(
                f"| `{v['id']}` | {p['n']} | {p['n_lost_to_censoring']} | {_f(p['flat']['mean_sol'])} | {_ci(p['flat']['ci90_sol'])} | {_share(p['flat'])} "
                f"| {_f(p['press']['mean_sol'])} | {_ci(p['press']['ci90_sol'])} | {_share(p['press'])} |"
            )
    lines += ["", "## Nested leave-one-day-out selection of the exit", ""]
    nl = rep["nested_lodo"]
    if not nl["available"]:
        lines.append(f"Not available: {nl['reason']}.")
    else:
        lines += [
            f"Rule: {nl['selection_rule']}. Held-out days: {nl['n_days']}.",
            "",
            f"Common mint set (a row in every candidate variant and the reference, used for the choice and the held-out score): {nl['common_set_size']} of {nl['n_ref_entered']} OOF-selected mints. Candidates: "
            + ", ".join(f"`{c}`" for c in nl["candidates"])
            + ".",
            "",
            ("CENSORED variants excluded from the candidate set (mints lost to censoring): " + ", ".join(f"`{k}` ({n})" for k, n in nl["excluded_censored"].items()) + ".")
            if nl["excluded_censored"]
            else "No variant was excluded as censored.",
            "",
            "Advantage of the selection procedure over tp50_sl30, held-out days only (mean SOL/trade of chosen minus reference).",
            "",
            "| scope | paired n | flat adv | flat CI90 | flat days+ | press adv | press CI90 | press days+ |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for label, key in (("all held-out days", "pooled"), (nl["fast_only_label"], "fast_only")):
            p = nl[key]
            if not p["n"]:
                lines.append(f"| {label} | 0 | n/a | | | n/a | | |")
                continue
            lines.append(
                f"| {label} | {p['n']} | {_f(p['flat']['mean_sol'])} | {_ci(p['flat']['ci90_sol'])} | {_share(p['flat'])} "
                f"| {_f(p['press']['mean_sol'])} | {_ci(p['press']['ci90_sol'])} | {_share(p['press'])} |"
            )
        lines += ["", "Times chosen (of the held-out days): " + ", ".join(f"`{k}` {n}" for k, n in nl["times_chosen"].items() if n) + ".", ""]
        lines += ["| held-out day | chosen |", "| --- | --- |"] + [f"| {c['day']} | `{c['chosen']}` |" for c in nl["choices_by_day"]]
    cens = [v for v in vs if v.get("censored_rows_by_day")]
    if cens:
        lines += ["", "Censored rows per day (reference rows with no row under the variant):"]
        lines += [f"- `{v['id']}`: " + ", ".join(f"{d} {n}" for d, n in v["censored_rows_by_day"].items()) for v in cens]
    lines += ["", "## Exit-side latency", "", rep["exit_side_latency"], ""]
    t = rep["tries"]
    lines += [
        "## Tries",
        "",
        f"Variants tried: {t['n_variants_tried']} (of {t['n_requested']} requested; {t['n_skipped']} skipped, listed below). "
        f"Also run: {t['also_run']}. Tries-log lines written this run: {t.get('logged', 0)}"
        + (f" ({t['log_note']})" if t.get("log_note") else "")
        + ".",
        "",
        "Skipped requested variants:",
    ]
    lines += [f"- `{s['id']}` ({s['family']}): {s['reason']}" for s in rep["skipped"]] or ["- none"]
    lines.append("")
    return "\n".join(lines)


def _share(leg: Mapping[str, Any]) -> str:
    s = leg["share_days_positive"]
    return "n/a" if s is None else f"{leg['days_positive']}/{leg['n_days']} ({s:.2f})"


# --- rows provenance ---------------------------------------------------------------


ROWS_META = "rows_meta.json"


def variants_hash(variants: Sequence[Mapping[str, Any]]) -> str:
    canon = json.dumps([v["spec"] for v in variants], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _view_sha256s(roots: Mapping[str, Path]) -> dict[str, str | None]:
    """sha256 of each root's own VIEW.sha256 manifest file (what the EXP-012 freeze pins)."""
    out: dict[str, str | None] = {}
    for label, root in sorted(roots.items()):
        f = Path(root) / "VIEW.sha256"
        out[label] = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
    return out


def _code_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent, capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except Exception:  # noqa: BLE001 - provenance only, never fatal
        return "unknown"


def write_rows_meta(out_dir: Path, variants: Sequence[Mapping[str, Any]], roots: Mapping[str, Path]) -> dict[str, Any]:
    meta = {
        "schema": "exp012_exit_rows_meta_v1",
        "variants": [v["id"] for v in variants],
        "variants_sha256": variants_hash(variants),
        "code_commit": _code_commit(),
        "input_view_sha256": _view_sha256s(roots),
    }
    (out_dir / ROWS_META).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def check_rows_meta(out_dir: Path, variants: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """--reuse-rows gate. With rows_meta.json: refuse unless the stored variant hash
    equals the current variant list's. Without it (rows from before this sidecar
    existed): refuse unless every spec id in the rows is a current variant, and say
    so in the report."""
    path = out_dir / ROWS_META
    cur = variants_hash(variants)
    if path.is_file():
        meta = json.loads(path.read_text(encoding="utf-8"))
        if meta.get("variants_sha256") != cur:
            raise SystemExit(f"refusing --reuse-rows: the stored variant list {meta.get('variants')} (hash {str(meta.get('variants_sha256'))[:12]}) differs from the current one (hash {cur[:12]}, {[v['id'] for v in variants]}); rerun the tape pass")
        return {**meta, "legacy_no_meta": False}
    have = {r["spec"] for r in rows}
    extra = sorted(have - {v["id"] for v in variants})
    if extra:
        raise SystemExit(f"refusing --reuse-rows: no {ROWS_META} and the rows hold spec ids not in the current variant list: {extra}")
    return {"legacy_no_meta": True, "note": f"no {ROWS_META} next to the rows (written before provenance existed); spec ids checked against the current list only", "variants_sha256_current": cur}


# --- tries log ---------------------------------------------------------------------


def resolve_tries_path(arg: str | None) -> Path:
    """Absolute tries-log path. --tries-log, else MAL_TRIES_LOG, else data/tries.jsonl.
    Under a MiScusi job ($MISCUSI_OUTPUT_DIR set) a relative path would land in the job's
    checkout, so it is refused: pass an absolute --tries-log or set an absolute MAL_TRIES_LOG."""
    in_miscusi = bool(os.environ.get("MISCUSI_OUTPUT_DIR"))
    if arg:
        src, p = "--tries-log", Path(arg)
    elif os.environ.get("MAL_TRIES_LOG"):
        src, p = "MAL_TRIES_LOG", Path(os.environ["MAL_TRIES_LOG"])
    else:
        src, p = "default data/tries.jsonl", Path("data/tries.jsonl")
    if not p.is_absolute():
        if in_miscusi:
            raise SystemExit(f"refusing: tries log {str(p)!r} ({src}) is relative under a MiScusi checkout; give an absolute --tries-log or an absolute MAL_TRIES_LOG")
        p = p.resolve()
    return p


def _read_marker(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8").strip()
    try:
        doc = json.loads(text)
        return set(doc["logged_variants"])
    except (ValueError, KeyError, TypeError):
        return {"*"}  # legacy marker ("N lines"): the whole set was logged


def _write_marker(path: Path, ids: set[str]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"logged_variants": sorted(ids)}) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _already_in_log(log: Path, variant: str, result_path: Path) -> bool:
    if not log.is_file():
        return False
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("tool") == "tools.exp012_exit_sensitivity" and rec.get("config", {}).get("variant") == variant and rec.get("result_path") == str(result_path):
            return True
    return False


def log_tries(rep: dict[str, Any], out_dir: Path, tries_log: str | Path) -> int:
    """One result.v1 tries-log line per variant scored (tools.mal_result.append_try),
    role 'exploration', data blocks = the three exploration-pool blocks. Idempotent:
    after each appended line the marker (out_dir/TRIES_MARKER) is atomically rewritten
    with the set of logged variant ids; a variant already in the marker, or already
    present in the log for this result path (a crash between append and marker),
    is not appended again. Returns the number of lines appended now."""
    from tools import mal_result
    from tools.exp012_support import exploration_pool_blocks

    marker = out_dir / TRIES_MARKER
    done = _read_marker(marker)
    if "*" in done:
        return 0
    result_path = out_dir / "exit_sensitivity.json"
    blocks = exploration_pool_blocks()
    n = 0
    for v in rep["variants"]:
        if v["id"] not in done:
            if not _already_in_log(Path(tries_log), v["id"], result_path):
                mal_result.append_try(
                    tries_log,
                    tool="tools.exp012_exit_sensitivity",
                    config={"experiment": "EXP-012 exit sensitivity", "variant": v["id"], "family": v["family"], "reference": v["reference"], "entry": rep["entry"], "selection": "OOF"},
                    data_blocks=blocks,
                    result_path=result_path,
                    role="exploration",
                )
                n += 1
            done.add(v["id"])
            _write_marker(marker, done)
    return n


# --- CLI -----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_root_args(ap)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    ap.add_argument("--max-workers", type=int, default=2)
    ap.add_argument("--buffer-hours", type=int, default=24)
    ap.add_argument("--max-home-hours", type=int, default=12)
    ap.add_argument("--allow-censored", action="store_true", help="let a non-reference variant have fewer rows than n_oof (exit past the end of the tape); flagged CENSORED. A variant with 0 rows is always an error")
    ap.add_argument("--tries-log", default=None, help="absolute tries-log path (default: MAL_TRIES_LOG, else data/tries.jsonl; a relative path is refused under a MiScusi checkout)")
    ap.add_argument("--reuse-rows", action="store_true", help=f"skip the tape pass and analyze OUT_DIR/{SCRATCH_ROWS} if it exists (refused if the variant list changed since {ROWS_META})")
    args = ap.parse_args(argv)
    assert args.max_workers <= 2, "keep max-workers <= 2 (same memory budget as the table build)"
    tries_path = resolve_tries_path(args.tries_log)  # refuse a bad path before any long pass
    variants, skipped = resolve_variants()
    scores, threshold, thr_doc, oof_days = load_oof(args.artifact_dir)
    rows_path = args.out_dir / SCRATCH_ROWS
    t0 = time.time()
    if args.reuse_rows and rows_path.is_file():
        rows = list(iter_rows_jsonl(rows_path))
        provenance = check_rows_meta(args.out_dir, variants, rows)
    else:
        roots = guarded_roots(args)
        rows = collect_rows(
            roots["fast"], roots["insample"], roots["live"], [v["spec"] for v in variants], args.out_dir / "scratch",
            max_workers=args.max_workers, buffer_hours=args.buffer_hours, max_home_hours=(args.max_home_hours or None),
        )
        write_rows(rows_path, rows)
        provenance = {**write_rows_meta(args.out_dir, variants, roots), "legacy_no_meta": False}
    rep = analyze(rows, scores, threshold, variants, skipped, thr_doc, oof_days, args.allow_censored)
    rep["wall_s"] = time.time() - t0
    rep["rows_provenance"] = provenance
    rep["tries"]["logged"] = log_tries(rep, args.out_dir, tries_path)
    rep["tries"]["log_path"] = str(tries_path)
    if rep["tries"]["logged"] < len(rep["variants"]):
        rep["tries"]["log_note"] = "the rest were already logged by an earlier analysis of these rows"
    (args.out_dir / "exit_sensitivity.json").write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    md = render_md(rep)
    (args.out_dir / "exit_sensitivity.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
