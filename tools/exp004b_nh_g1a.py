#!/usr/bin/env python3
"""EXP-004b — NH-G1a ordinal prior-mint depth (paper-only).

Reuses EXP-004 precompute + marks join on the same sealed day-aligned JSONL.
Stratifies prior_mint_count into {0, 1, 2, 3+} buckets vs full-book spine + random same-n.
No new feeds, no cross-day state, no H-G2 retune.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from tools.exp004_graph_discovery import (
    DEFAULT_SEED,
    PRIMARY_HORIZON,
    PRIMARY_HORIZONS,
    ScoredRow,
    _cohort_mean,
    _detect_data_blockers,
    _gate_separable_vs_spine,
    _horizon_return,
    _random_same_n,
    build_mint_price_series,
    build_scored_book,
    precompute_graph_book,
    score_hypothesis_at_horizon,
)
from tools.exp001_mislabel import load_jsonl_files
from tools.exp002_paper_runner import (
    MIN_PRICED_FOR_KILL,
    PARITY_REL_TOLERANCE,
    _gate_lift,
    _gate_parity,
)

EXP_ID = "EXP-004b-nh-g1a-ordinal-prior-mint-v0"
DEFAULT_OUTPUT_DIR = Path("data/observe")
DEFAULT_PREFIX = "_exp004b"

OrdinalBucketId = Literal["bucket_0", "bucket_1", "bucket_2", "bucket_3plus"]

ORDINAL_BUCKETS: tuple[tuple[OrdinalBucketId, str], ...] = (
    ("bucket_0", "prior_mint_count == 0 (novel creator)"),
    ("bucket_1", "prior_mint_count == 1"),
    ("bucket_2", "prior_mint_count == 2"),
    ("bucket_3plus", "prior_mint_count >= 3"),
)


def prior_mint_bucket(count: int) -> OrdinalBucketId:
    if count <= 0:
        return "bucket_0"
    if count == 1:
        return "bucket_1"
    if count == 2:
        return "bucket_2"
    return "bucket_3plus"


def _select_bucket_rows(book: Sequence[ScoredRow], bucket: OrdinalBucketId) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in book:
        b = prior_mint_bucket(row.features.prior_mint_count)
        if b == bucket:
            out.append(row.as_dict())
    return out


def _other_bucket_rows(book: Sequence[ScoredRow], bucket: OrdinalBucketId) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in book:
        b = prior_mint_bucket(row.features.prior_mint_count)
        if b != bucket:
            out.append(row.as_dict())
    return out


def score_bucket_at_horizon(
    book: Sequence[ScoredRow],
    *,
    bucket: OrdinalBucketId,
    description: str,
    horizon: str,
    seed: int,
) -> dict[str, Any]:
    bucket_rows = _select_bucket_rows(book, bucket)
    other_rows = _other_bucket_rows(book, bucket)
    spine_rows = [r.as_dict() for r in book]

    bucket_mean, bucket_n = _cohort_mean(bucket_rows, horizon)
    other_mean, other_n = _cohort_mean(other_rows, horizon)
    spine_mean, spine_n = _cohort_mean(spine_rows, horizon)

    random_baseline = _random_same_n(spine_rows, sample_n=len(bucket_rows), horizon=horizon, seed=seed + hash(bucket) % 997)

    lift_gate = _gate_lift(
        bucket_mean,
        random_baseline.get("mean_return_pct")
        if isinstance(random_baseline.get("mean_return_pct"), (int, float))
        else None,
        bucket_n,
        int(random_baseline.get("priced_n", 0)),
    )
    separable_gate = _gate_separable_vs_spine(bucket_mean, spine_mean, bucket_n, spine_n)
    parity_gate = _gate_parity(bucket_mean, other_mean, bucket_n, other_n)

    if len(bucket_rows) == 0:
        overall = "INCOMPLETE"
        lift_gate = "N/A"
        separable_gate = "N/A"
        parity_gate = "N/A"
    elif lift_gate == "INCOMPLETE" or separable_gate == "INCOMPLETE" or parity_gate == "INCOMPLETE":
        overall = "INCOMPLETE"
    elif separable_gate == "FAIL" and lift_gate == "FAIL":
        overall = "KILL_NO_SEPARABLE_ARM"
    elif lift_gate == "FAIL":
        overall = "FAIL_NO_LIFT_VS_RANDOM"
    elif separable_gate == "FAIL":
        overall = "FAIL_NO_LIFT_VS_SPINE"
    else:
        overall = "DIRECTIONAL_NON_KILL"

    return {
        "bucket": bucket,
        "horizon": horizon,
        "description": description,
        "arm_n": len(bucket_rows),
        "complement_arm_n": len(other_rows),
        "cohort": {"mean_return_pct": bucket_mean, "priced_n": bucket_n},
        "complement_cohort": {"mean_return_pct": other_mean, "priced_n": other_n},
        "spine_baseline": {"mean_return_pct": spine_mean, "priced_n": spine_n},
        "random_baseline": random_baseline,
        "gates": {
            "no_lift_vs_random": {
                "comparator": f"bucket_mean_{horizon} > random_same_n_{horizon}",
                "min_priced_per_arm": MIN_PRICED_FOR_KILL,
                "result": lift_gate,
            },
            "separable_vs_spine": {
                "comparator": f"bucket_mean_{horizon} > spine_mean_{horizon}",
                "min_priced_per_arm": MIN_PRICED_FOR_KILL,
                "result": separable_gate,
            },
            "bucket_vs_complement_parity": {
                "comparator": (
                    f"|bucket_mean - complement_mean| / max(|means|) <= {PARITY_REL_TOLERANCE} "
                    "→ ordinal bucket not separable from rest-of-book"
                ),
                "min_priced_per_arm": MIN_PRICED_FOR_KILL,
                "result": parity_gate,
            },
        },
        "overall": overall,
    }


def _bucket_passes_both_gates(bucket_result: Mapping[str, Any]) -> bool:
    gates = bucket_result.get("gates") or {}
    sep = (gates.get("separable_vs_spine") or {}).get("result")
    lift = (gates.get("no_lift_vs_random") or {}).get("result")
    return sep == "PASS" and lift == "PASS"


def score_buckets_by_regime_gate(
    book: Sequence[ScoredRow],
    *,
    horizon: str,
    seed: int,
) -> dict[str, Any]:
    by_gate: dict[str, list[ScoredRow]] = defaultdict(list)
    for row in book:
        by_gate[row.features.regime_gate_key].append(row)

    per_gate: dict[str, Any] = {}
    for gate, rows in sorted(by_gate.items()):
        per_gate[gate] = {
            bid: score_bucket_at_horizon(
                rows,
                bucket=bid,
                description=desc,
                horizon=horizon,
                seed=seed,
            )
            for bid, desc in ORDINAL_BUCKETS
        }

    return {
        "horizon": horizon,
        "regime_gate_keys_n": len(by_gate),
        "buckets_by_regime_gate_key": per_gate,
        "note": "S1 — no cross-regime merge; per-gate priced_n reported honestly.",
    }


def _ordinal_parity_across_buckets(bucket_results: Mapping[str, Mapping[str, Any]], horizon: str) -> dict[str, Any]:
    means: list[tuple[str, float]] = []
    priced: list[int] = []
    for bid, _desc in ORDINAL_BUCKETS:
        br = bucket_results.get(bid, {}).get(horizon, {})
        if not isinstance(br, dict):
            continue
        m = br.get("cohort", {}).get("mean_return_pct")
        n = int(br.get("cohort", {}).get("priced_n") or 0)
        if isinstance(m, (int, float)) and n >= MIN_PRICED_FOR_KILL:
            means.append((bid, float(m)))
            priced.append(n)
    if len(means) < 2:
        return {"result": "INCOMPLETE", "note": "Fewer than two ordinal buckets with priced_n floor."}
    max_b, max_m = max(means, key=lambda x: x[1])
    min_b, min_m = min(means, key=lambda x: x[1])
    parity = _gate_parity(max_m, min_m, min(priced), min(priced))
    return {
        "result": parity,
        "max_bucket": max_b,
        "min_bucket": min_b,
        "max_mean_return_pct": max_m,
        "min_mean_return_pct": min_m,
    }


def _detect_s1_collapse(
    *,
    aggregate_60s: Mapping[str, Mapping[str, Any]],
    stratified: Mapping[str, Any],
) -> dict[str, Any]:
    passing_agg = [
        bid
        for bid, _ in ORDINAL_BUCKETS
        if _bucket_passes_both_gates(aggregate_60s.get(bid, {}))
    ]
    if not passing_agg:
        return {"s1_collapse": False, "note": "No aggregate bucket passed both gates — S1 collapse N/A."}

    per_gate = stratified.get("buckets_by_regime_gate_key") or {}
    per_gate_pass: Counter[str] = Counter()
    for _gate, buckets in per_gate.items():
        for bid in passing_agg:
            br = buckets.get(bid, {})
            if _bucket_passes_both_gates(br):
                per_gate_pass[bid] += 1

    collapsed = all(per_gate_pass.get(bid, 0) == 0 for bid in passing_agg)
    return {
        "s1_collapse": collapsed,
        "aggregate_pass_buckets": passing_agg,
        "per_gate_pass_counts": dict(per_gate_pass),
        "note": (
            "Falsifier: aggregate ordinal pass vanishes when split per regime_gate_key."
            if collapsed
            else "At least one aggregate-pass bucket also passes both gates in some regime_gate_key."
        ),
    }


def build_exp004b_summary(
    *,
    paths: Sequence[Path],
    marks_paths: Sequence[Path],
    seed: int,
    book: Sequence[ScoredRow],
    precompute_meta: Mapping[str, Any],
    data_blockers: list[str],
) -> dict[str, Any]:
    buckets: dict[str, Any] = {}
    for bid, desc in ORDINAL_BUCKETS:
        buckets[bid] = {
            horizon: score_bucket_at_horizon(
                book,
                bucket=bid,
                description=desc,
                horizon=horizon,
                seed=seed,
            )
            for horizon in PRIMARY_HORIZONS
        }

    stratified = score_buckets_by_regime_gate(book, horizon=PRIMARY_HORIZON, seed=seed)
    s1 = _detect_s1_collapse(aggregate_60s=buckets, stratified=stratified)
    ordinal_parity = _ordinal_parity_across_buckets(buckets, PRIMARY_HORIZON)

    passing_buckets_60s = [
        bid for bid, _ in ORDINAL_BUCKETS if _bucket_passes_both_gates(buckets[bid][PRIMARY_HORIZON])
    ]

    priced_any = sum(
        1 for r in book if _horizon_return(r.as_dict(), PRIMARY_HORIZON) is not None
    )

    hg2_ref = score_hypothesis_at_horizon(book, hyp_id="H-G2", horizon=PRIMARY_HORIZON, seed=seed)

    if data_blockers or not book or priced_any < MIN_PRICED_FOR_KILL:
        overall = "INCOMPLETE"
    elif s1.get("s1_collapse"):
        overall = "KILL_ORDINAL_S1_COLLAPSE"
    elif not passing_buckets_60s:
        overall = "KILL_NO_ORDINAL_SEPARABLE_BUCKET"
    else:
        overall = "DIRECTIONAL_NON_KILL"

    bucket_counts = Counter(prior_mint_bucket(r.features.prior_mint_count) for r in book)

    return {
        "exp": EXP_ID,
        "parent_exp": "EXP-004-graph-creator-recurrence-v0",
        "unit": "sealed_ingest_hot_bonding_create_ordinal_prior_mint_depth",
        "paths": [str(p) for p in paths],
        "marks_paths": [str(p) for p in marks_paths],
        "seed": seed,
        "primary_horizon": PRIMARY_HORIZON,
        "horizons": list(PRIMARY_HORIZONS),
        "population_n": len(book),
        f"priced_{PRIMARY_HORIZON}_n": priced_any,
        "min_priced_for_kill": MIN_PRICED_FOR_KILL,
        "ordinal_arm_n_by_bucket": dict(sorted(bucket_counts.items())),
        "precompute": dict(precompute_meta),
        "ordinal_buckets": buckets,
        "ordinal_parity_60s": ordinal_parity,
        "regime_stratification_s1": stratified,
        "s1_collapse_falsifier": s1,
        "passing_buckets_60s": passing_buckets_60s,
        "parent_h_g2_reference_60s": {
            "overall": hg2_ref.get("overall"),
            "note": "H-G2 burst lane stays killed — reference only, no retune.",
        },
        "data_blockers": data_blockers,
        "soft_fences": [
            "h_g2_kill_intact_no_burst_retune",
            "no_densify_marks",
            "no_cross_day_join",
            "no_exp_002c_retune",
            "no_discovery_promotion",
        ],
        "overall": overall,
        "limitations": [
            "Ordinal arms reuse EXP-004 graph precompute (weak_ws create spine).",
            "Bucket 0 is novel creator baseline; buckets 1/2/3+ are repeat-depth arms.",
            "No mean_return_pct claims in git — gates + taxonomy only.",
        ],
    }


def render_report(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary['exp']} — NH-G1a ordinal report",
        "",
        f"**Overall @ {PRIMARY_HORIZON}:** {summary.get('overall')}",
        f"**Population:** {summary.get('population_n')}",
        f"**priced_{PRIMARY_HORIZON}_n:** {summary.get(f'priced_{PRIMARY_HORIZON}_n')}",
        f"**Passing buckets (sep+random PASS):** {summary.get('passing_buckets_60s')}",
        f"**H-G2 ref (kill intact):** {summary.get('parent_h_g2_reference_60s', {}).get('overall')}",
        "",
    ]
    blockers = summary.get("data_blockers") or []
    if blockers:
        lines.append("## DATA BLOCKER")
        for b in blockers:
            lines.append(f"- {b}")
        lines.append("")

    lines.append(f"## Ordinal buckets @ {PRIMARY_HORIZON}")
    buckets = summary.get("ordinal_buckets") or {}
    for bid, _ in ORDINAL_BUCKETS:
        b60 = buckets.get(bid, {}).get(PRIMARY_HORIZON, {})
        gates = b60.get("gates") or {}
        lines.append(
            f"- **{bid}** overall={b60.get('overall')} arm_n={b60.get('arm_n')} "
            f"priced_n={b60.get('cohort', {}).get('priced_n')} "
            f"sep={gates.get('separable_vs_spine', {}).get('result')} "
            f"random={gates.get('no_lift_vs_random', {}).get('result')}"
        )
    s1 = summary.get("s1_collapse_falsifier") or {}
    lines.append("")
    lines.append(f"**S1 collapse falsifier:** {s1.get('s1_collapse')} — {s1.get('note')}")
    return "\n".join(lines) + "\n"


def run_exp004b(
    paths: Sequence[Path],
    *,
    marks_paths: Sequence[Path] | None = None,
    seed: int = DEFAULT_SEED,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = DEFAULT_PREFIX,
) -> dict[str, Any]:
    marks_paths = list(marks_paths or [])
    all_paths = list(paths) + marks_paths
    loaded, _malformed = load_jsonl_files(all_paths) if all_paths else ([], 0)
    graph_pairs, pre_meta = precompute_graph_book(loaded)
    mint_series = build_mint_price_series(loaded)
    book = build_scored_book(graph_pairs, mint_series)
    blockers = _detect_data_blockers(paths, marks_paths, loaded, book)
    summary = build_exp004b_summary(
        paths=paths,
        marks_paths=marks_paths,
        seed=seed,
        book=book,
        precompute_meta=pre_meta,
        data_blockers=blockers,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / f"{prefix}_summary.json"
    report_path = output_dir / f"{prefix}_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(render_report(summary), encoding="utf-8")
    summary["_artifacts"] = {
        "summary_json": str(summary_path),
        "report_md": str(report_path),
    }
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{EXP_ID} ordinal prior-mint re-score")
    parser.add_argument("observe_paths", nargs="+", type=Path, help="Sealed observe JSONL (day-aligned).")
    parser.add_argument("--marks", nargs="+", type=Path, required=True, help="EXP-003 marks JSONL (same day).")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    args = parser.parse_args(argv)

    summary = run_exp004b(
        args.observe_paths,
        marks_paths=args.marks,
        seed=args.seed,
        output_dir=args.output_dir,
        prefix=args.prefix,
    )
    print(
        json.dumps(
            {
                "overall": summary["overall"],
                "passing_buckets_60s": summary.get("passing_buckets_60s"),
                "artifacts": summary.get("_artifacts"),
            },
            indent=2,
        )
    )
    if summary.get("data_blockers"):
        print("\nDATA BLOCKER:", file=sys.stderr)
        for b in summary["data_blockers"]:
            print(f"  - {b}", file=sys.stderr)
        return 2
    if summary["overall"] == "INCOMPLETE":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
