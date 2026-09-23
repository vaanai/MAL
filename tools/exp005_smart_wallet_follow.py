#!/usr/bin/env python3
"""EXP-005 — Scout L3 smart-wallet / follow packet Discovery (paper-only).

Reuses EXP-004 precompute + sealed spine. Scores a documented S3 L3 packet cohort
(I1/I3 select, H-G2 veto — never mirror-wallet) vs spine + random same-n.
No H-G2 revive arm, no ordinal / NH-G3a / NH-Index.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from tools.exp001_mislabel import LoadedRow, load_jsonl_files
from tools.exp002_paper_runner import (
    MIN_PRICED_FOR_KILL,
    EvaluateRulesV2,
    _gate_lift,
    is_bonding_create,
)
from tools.exp004_graph_discovery import (
    DEFAULT_SEED,
    GraphFeatures,
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

EXP_ID = "EXP-005-smart-wallet-follow-discovery-v0"
DEFAULT_OUTPUT_DIR = Path("data/observe")
DEFAULT_PREFIX = "_exp005"

L3_PACKET_RULE_V0 = """
L3 follow packet v0 (S3 — select / veto / enrich; never mirror-wallet):
  SELECT (I1 ∪ I3): creator_buyer_recurrence_weak (H-G3) OR prior_mint_count > 0 (H-G1 / I3).
  VETO (H-G2 burst only — killed Graph lane; veto serial-spam, not scored as revive arm):
    burst_count >= 2 within EXP-004 burst lookback.
  I2 / H-G4: honest-empty on create-only spine — missing early_wallet_dt does NOT veto.
  I4 / S1: precompute and kill reads stratified per regime_gate_key (no cross-regime merge).
  FORBIDDEN: mirror-wallet lists, single-wallet PnL chase, post-T tape, marks as features.
""".strip()

GateResult = Literal["PASS", "FAIL", "INCOMPLETE", "N/A"]


def l3_packet_selected(feats: GraphFeatures) -> bool:
    """Return True when row is in the documented L3 v0 packet cohort."""
    select_i1_i3 = feats.recurrence_weak or feats.prior_mint_count > 0
    veto_h_g2_burst = feats.burst_count >= 2
    return select_i1_i3 and not veto_h_g2_burst


def _select_l3_rows(book: Sequence[ScoredRow]) -> list[dict[str, Any]]:
    return [r.as_dict() for r in book if l3_packet_selected(r.features)]


def _complement_l3_rows(book: Sequence[ScoredRow]) -> list[dict[str, Any]]:
    return [r.as_dict() for r in book if not l3_packet_selected(r.features)]


def _packet_passes_both_gates(packet_result: Mapping[str, Any]) -> bool:
    gates = packet_result.get("gates") or {}
    sep = (gates.get("separable_vs_spine") or {}).get("result")
    lift = (gates.get("no_lift_vs_random") or {}).get("result")
    return sep == "PASS" and lift == "PASS"


def score_l3_packet_at_horizon(
    book: Sequence[ScoredRow],
    *,
    horizon: str,
    seed: int,
) -> dict[str, Any]:
    packet_rows = _select_l3_rows(book)
    complement_rows = _complement_l3_rows(book)
    spine_rows = [r.as_dict() for r in book]

    packet_mean, packet_n = _cohort_mean(packet_rows, horizon)
    complement_mean, complement_n = _cohort_mean(complement_rows, horizon)
    spine_mean, spine_n = _cohort_mean(spine_rows, horizon)

    random_baseline = _random_same_n(
        spine_rows,
        sample_n=len(packet_rows),
        horizon=horizon,
        seed=seed + 5,
    )

    lift_gate = _gate_lift(
        packet_mean,
        random_baseline.get("mean_return_pct")
        if isinstance(random_baseline.get("mean_return_pct"), (int, float))
        else None,
        packet_n,
        int(random_baseline.get("priced_n", 0)),
    )
    separable_gate = _gate_separable_vs_spine(packet_mean, spine_mean, packet_n, spine_n)

    if len(packet_rows) == 0:
        overall = "INCOMPLETE"
        lift_gate = "N/A"
        separable_gate = "N/A"
    elif lift_gate == "INCOMPLETE" or separable_gate == "INCOMPLETE":
        overall = "INCOMPLETE"
    elif separable_gate == "FAIL" and lift_gate == "FAIL":
        overall = "KILL_NO_SEPARABLE_L3_PACKET"
    elif lift_gate == "FAIL":
        overall = "FAIL_NO_LIFT_VS_RANDOM"
    elif separable_gate == "FAIL":
        overall = "FAIL_NO_LIFT_VS_SPINE"
    else:
        overall = "DIRECTIONAL_NON_KILL"

    return {
        "horizon": horizon,
        "l3_packet_rule": "L3_PACKET_V0",
        "packet_arm_n": len(packet_rows),
        "complement_arm_n": len(complement_rows),
        "packet_cohort": {"mean_return_pct": packet_mean, "priced_n": packet_n},
        "complement_cohort": {"mean_return_pct": complement_mean, "priced_n": complement_n},
        "spine_baseline": {"mean_return_pct": spine_mean, "priced_n": spine_n},
        "random_baseline": random_baseline,
        "gates": {
            "no_lift_vs_random": {
                "comparator": f"packet_mean_{horizon} > random_same_n_{horizon}",
                "min_priced_per_arm": MIN_PRICED_FOR_KILL,
                "result": lift_gate,
            },
            "separable_vs_spine": {
                "comparator": f"packet_mean_{horizon} > spine_mean_{horizon}",
                "min_priced_per_arm": MIN_PRICED_FOR_KILL,
                "result": separable_gate,
            },
        },
        "overall": overall,
        "falsifiers": ["K-spine-random", "K-L3-cohort", "K-S1-collapse"],
    }


def score_l3_packet_by_regime_gate(
    book: Sequence[ScoredRow],
    *,
    horizon: str,
    seed: int,
) -> dict[str, Any]:
    by_gate: dict[str, list[ScoredRow]] = defaultdict(list)
    for row in book:
        by_gate[row.features.regime_gate_key].append(row)

    per_gate = {
        gate: score_l3_packet_at_horizon(rows, horizon=horizon, seed=seed + idx)
        for idx, (gate, rows) in enumerate(sorted(by_gate.items()))
    }
    return {
        "horizon": horizon,
        "regime_gate_keys_n": len(by_gate),
        "l3_packet_by_regime_gate_key": per_gate,
        "note": "S1 — no cross-regime merge; per-gate priced_n reported honestly.",
    }


def _detect_s1_collapse(
    *,
    aggregate_60s: Mapping[str, Any],
    stratified: Mapping[str, Any],
) -> dict[str, Any]:
    if not _packet_passes_both_gates(aggregate_60s):
        return {
            "s1_collapse": False,
            "note": "Aggregate L3 packet did not pass both gates — S1 collapse N/A.",
        }

    per_gate = stratified.get("l3_packet_by_regime_gate_key") or {}
    passing_gates = [
        gate for gate, result in per_gate.items() if _packet_passes_both_gates(result)
    ]
    collapsed = len(passing_gates) == 0
    return {
        "s1_collapse": collapsed,
        "per_gate_passing_both_gates": passing_gates,
        "note": (
            "Falsifier K-S1-collapse: aggregate L3 pass vanishes under every regime_gate_key split."
            if collapsed
            else "At least one regime_gate_key retains sep+random PASS for L3 packet."
        ),
    }


def compute_exp002c_runner_overlap(
    loaded: Sequence[LoadedRow],
    book: Sequence[ScoredRow],
) -> dict[str, Any]:
    """Overlap of L3 packet cohort with EXP-002c v2 runner labels (same sealed rows)."""
    rules = EvaluateRulesV2()
    book_sigs = {r.signature for r in book if isinstance(r.signature, str)}
    v2_runner: set[str] = set()
    v2_reject: set[str] = set()
    for item in loaded:
        row = item.row
        if not is_bonding_create(row):
            continue
        sig = row.get("signature")
        if not isinstance(sig, str) or sig not in book_sigs:
            continue
        label, _reasons = rules.evaluate(row)
        if label == "runner":
            v2_runner.add(sig)
        elif label == "reject":
            v2_reject.add(sig)

    l3_packet = {r.signature for r in book if l3_packet_selected(r.features) and isinstance(r.signature, str)}
    overlap_runner = l3_packet & v2_runner
    overlap_reject = l3_packet & v2_reject

    def _pct(n: int, d: int) -> float | None:
        return (n / d * 100.0) if d else None

    l3_n = len(l3_packet)
    runner_n = len(v2_runner)
    return {
        "comparator": "EXP-002c EvaluateRulesV2 runner set vs L3_PACKET_V0 signatures",
        "exp002c_retune": False,
        "l3_packet_n": l3_n,
        "v2_runner_n_in_graph_population": runner_n,
        "v2_reject_n_in_graph_population": len(v2_reject),
        "overlap_l3_and_v2_runner_n": len(overlap_runner),
        "overlap_l3_and_v2_reject_n": len(overlap_reject),
        "jaccard_l3_vs_v2_runner": (
            len(overlap_runner) / len(l3_packet | v2_runner) if (l3_packet | v2_runner) else None
        ),
        "pct_l3_that_are_v2_runners": _pct(len(overlap_runner), l3_n),
        "pct_v2_runners_in_l3": _pct(len(overlap_runner), runner_n),
        "k_l3_cohort_note": (
            "High overlap with v2 modal runner slice is attribution for K-L3-cohort; "
            "does not auto-kill without outcome gate failure."
        ),
    }


def build_exp005_summary(
    *,
    paths: Sequence[Path],
    marks_paths: Sequence[Path],
    seed: int,
    book: Sequence[ScoredRow],
    precompute_meta: Mapping[str, Any],
    data_blockers: list[str],
    exp002c_overlap: Mapping[str, Any],
) -> dict[str, Any]:
    l3_by_horizon = {
        horizon: score_l3_packet_at_horizon(book, horizon=horizon, seed=seed)
        for horizon in PRIMARY_HORIZONS
    }
    stratified = score_l3_packet_by_regime_gate(book, horizon=PRIMARY_HORIZON, seed=seed)
    s1 = _detect_s1_collapse(aggregate_60s=l3_by_horizon[PRIMARY_HORIZON], stratified=stratified)

    priced_any = sum(
        1 for r in book if _horizon_return(r.as_dict(), PRIMARY_HORIZON) is not None
    )
    packet_n = l3_by_horizon[PRIMARY_HORIZON]["packet_arm_n"]

    hg2_ref = score_hypothesis_at_horizon(book, hyp_id="H-G2", horizon=PRIMARY_HORIZON, seed=seed)

    if data_blockers or not book or priced_any < MIN_PRICED_FOR_KILL:
        overall = "INCOMPLETE"
    elif s1.get("s1_collapse"):
        overall = "KILL_L3_S1_COLLAPSE"
    elif not _packet_passes_both_gates(l3_by_horizon[PRIMARY_HORIZON]):
        agg = l3_by_horizon[PRIMARY_HORIZON]["overall"]
        overall = agg if isinstance(agg, str) else "KILL_L3_PACKET"
    else:
        overall = "DIRECTIONAL_NON_KILL"

    return {
        "exp": EXP_ID,
        "parent_exp": "EXP-004-graph-creator-recurrence-v0",
        "unit": "sealed_ingest_hot_bonding_create_l3_follow_packet",
        "paths": [str(p) for p in paths],
        "marks_paths": [str(p) for p in marks_paths],
        "seed": seed,
        "primary_horizon": PRIMARY_HORIZON,
        "horizons": list(PRIMARY_HORIZONS),
        "population_n": len(book),
        f"priced_{PRIMARY_HORIZON}_n": priced_any,
        "l3_packet_arm_n": packet_n,
        "min_priced_for_kill": MIN_PRICED_FOR_KILL,
        "l3_packet_rule_v0": L3_PACKET_RULE_V0,
        "precompute": dict(precompute_meta),
        "l3_packet": l3_by_horizon,
        "regime_stratification_s1": stratified,
        "s1_collapse_falsifier": s1,
        "exp002c_runner_overlap": dict(exp002c_overlap),
        "parent_h_g2_reference_60s": {
            "overall": hg2_ref.get("overall"),
            "note": "H-G2 burst lane stays killed — reference only, not an EXP-005 scored arm.",
        },
        "data_blockers": data_blockers,
        "soft_fences": [
            "no_mirror_wallet",
            "h_g2_kill_intact_no_burst_retune",
            "no_ordinal_nh_g3a_nh_index",
            "no_densify_marks",
            "no_cross_day_join",
            "no_exp_002c_retune",
            "no_discovery_promotion",
        ],
        "overall": overall,
        "limitations": [
            "L3 packet reuses EXP-004 weak_ws graph precompute only.",
            "No mean_return_pct claims in git — gates + taxonomy only.",
            "I2/H-G4 enrich not required for v0 select on create-only spine.",
        ],
    }


def render_report(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary['exp']} — L3 follow packet report",
        "",
        f"**Overall @ {PRIMARY_HORIZON}:** {summary.get('overall')}",
        f"**Population:** {summary.get('population_n')}",
        f"**L3 packet arm_n:** {summary.get('l3_packet_arm_n')}",
        f"**priced_{PRIMARY_HORIZON}_n:** {summary.get(f'priced_{PRIMARY_HORIZON}_n')}",
        f"**H-G2 ref (kill intact):** {summary.get('parent_h_g2_reference_60s', {}).get('overall')}",
        "",
        "## L3 packet rule (v0)",
        "",
        summary.get("l3_packet_rule_v0", ""),
        "",
    ]
    blockers = summary.get("data_blockers") or []
    if blockers:
        lines.append("## DATA BLOCKER")
        for b in blockers:
            lines.append(f"- {b}")
        lines.append("")

    l3 = (summary.get("l3_packet") or {}).get(PRIMARY_HORIZON, {})
    gates = l3.get("gates") or {}
    lines.append(f"## L3 packet gates @ {PRIMARY_HORIZON}")
    lines.append(
        f"- overall={l3.get('overall')} priced_n={l3.get('packet_cohort', {}).get('priced_n')} "
        f"sep={gates.get('separable_vs_spine', {}).get('result')} "
        f"random={gates.get('no_lift_vs_random', {}).get('result')}"
    )
    ov = summary.get("exp002c_runner_overlap") or {}
    lines.append("")
    lines.append("## EXP-002c runner overlap (cohort falsifier attribution)")
    lines.append(
        f"- l3_packet_n={ov.get('l3_packet_n')} v2_runner_n={ov.get('v2_runner_n_in_graph_population')} "
        f"overlap={ov.get('overlap_l3_and_v2_runner_n')} "
        f"jaccard={ov.get('jaccard_l3_vs_v2_runner')}"
    )
    s1 = summary.get("s1_collapse_falsifier") or {}
    lines.append("")
    lines.append(f"**S1 collapse:** {s1.get('s1_collapse')} — {s1.get('note')}")
    return "\n".join(lines) + "\n"


def cross_day_overall(day_overalls: Sequence[str]) -> str:
    """Honest cross-day stamp from per-day overall strings (no fabricated means)."""
    if not day_overalls:
        return "INCOMPLETE"
    if any(o == "INCOMPLETE" for o in day_overalls):
        return "INCOMPLETE"
    passing = [o for o in day_overalls if o == "DIRECTIONAL_NON_KILL"]
    if len(passing) == len(day_overalls):
        return "DIRECTIONAL_WATCH"
    if len(passing) > 0:
        return "DIRECTIONAL_NON_KILL"
    kills = {
        "KILL_NO_SEPARABLE_L3_PACKET",
        "FAIL_NO_LIFT_VS_RANDOM",
        "FAIL_NO_LIFT_VS_SPINE",
        "KILL_L3_S1_COLLAPSE",
        "KILL_L3_PACKET",
    }
    if all(o in kills for o in day_overalls):
        return "KILL_L3_PACKET"
    return "INCOMPLETE"


def run_exp005(
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
    overlap = compute_exp002c_runner_overlap(loaded, book) if book else {
        "note": "deferred — empty graph book",
    }
    summary = build_exp005_summary(
        paths=paths,
        marks_paths=marks_paths,
        seed=seed,
        book=book,
        precompute_meta=pre_meta,
        data_blockers=blockers,
        exp002c_overlap=overlap,
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
    parser = argparse.ArgumentParser(description=f"{EXP_ID} L3 follow packet measurement")
    parser.add_argument("observe_paths", nargs="+", type=Path, help="Sealed observe JSONL (day-aligned).")
    parser.add_argument("--marks", nargs="+", type=Path, required=True, help="EXP-003 marks JSONL (same day).")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    args = parser.parse_args(argv)

    summary = run_exp005(
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
                "l3_packet_arm_n": summary.get("l3_packet_arm_n"),
                "exp002c_overlap_n": summary.get("exp002c_runner_overlap", {}).get(
                    "overlap_l3_and_v2_runner_n"
                ),
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
