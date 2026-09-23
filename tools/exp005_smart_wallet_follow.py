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
EXP_005B_ID = "EXP-005b-l3-residual-vs-exp002c-v2-falsifier-v0"
DEFAULT_OUTPUT_DIR = Path("data/observe")
DEFAULT_PREFIX = "_exp005"
DEFAULT_PREFIX_005B = "_exp005b"

ResidualArmId = Literal["L3_full", "L3_minus_v2", "L3_intersect_v2"]
RESIDUAL_ARMS: tuple[ResidualArmId, ...] = ("L3_minus_v2", "L3_intersect_v2", "L3_full")
PRIMARY_RESIDUAL_ARM: ResidualArmId = "L3_minus_v2"

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


def _arm_passes_both_gates(arm_result: Mapping[str, Any]) -> bool:
    gates = arm_result.get("gates") or {}
    sep = (gates.get("separable_vs_spine") or {}).get("result")
    lift = (gates.get("no_lift_vs_random") or {}).get("result")
    return sep == "PASS" and lift == "PASS"


def _packet_passes_both_gates(packet_result: Mapping[str, Any]) -> bool:
    return _arm_passes_both_gates(packet_result)


def build_v2_runner_signatures(
    loaded: Sequence[LoadedRow],
    book: Sequence[ScoredRow],
) -> set[str]:
    """EXP-002c EvaluateRulesV2 runner labels on the same sealed graph book rows."""
    rules = EvaluateRulesV2()
    book_sigs = {r.signature for r in book if isinstance(r.signature, str)}
    v2_runner: set[str] = set()
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
    return v2_runner


def arm_row_in_cohort(
    row: ScoredRow,
    arm_id: ResidualArmId,
    v2_runner_sigs: set[str],
) -> bool:
    """Residual arm membership on L3_PACKET_V0 signatures."""
    if not l3_packet_selected(row.features):
        return False
    sig = row.signature
    if arm_id == "L3_full":
        return True
    if not isinstance(sig, str):
        return False
    in_v2 = sig in v2_runner_sigs
    if arm_id == "L3_minus_v2":
        return not in_v2
    return in_v2


def _select_arm_rows(
    book: Sequence[ScoredRow],
    arm_id: ResidualArmId,
    v2_runner_sigs: set[str],
) -> list[dict[str, Any]]:
    return [
        r.as_dict()
        for r in book
        if arm_row_in_cohort(r, arm_id, v2_runner_sigs)
    ]


def _complement_arm_rows(
    book: Sequence[ScoredRow],
    arm_id: ResidualArmId,
    v2_runner_sigs: set[str],
) -> list[dict[str, Any]]:
    return [
        r.as_dict()
        for r in book
        if not arm_row_in_cohort(r, arm_id, v2_runner_sigs)
    ]


def score_l3_arm_at_horizon(
    book: Sequence[ScoredRow],
    *,
    arm_id: ResidualArmId,
    v2_runner_sigs: set[str],
    horizon: str,
    seed: int,
) -> dict[str, Any]:
    arm_rows = _select_arm_rows(book, arm_id, v2_runner_sigs)
    complement_rows = _complement_arm_rows(book, arm_id, v2_runner_sigs)
    spine_rows = [r.as_dict() for r in book]

    arm_mean, arm_priced_n = _cohort_mean(arm_rows, horizon)
    complement_mean, complement_n = _cohort_mean(complement_rows, horizon)
    spine_mean, spine_n = _cohort_mean(spine_rows, horizon)

    random_baseline = _random_same_n(
        spine_rows,
        sample_n=len(arm_rows),
        horizon=horizon,
        seed=seed + 5 + hash(arm_id) % 97,
    )

    lift_gate = _gate_lift(
        arm_mean,
        random_baseline.get("mean_return_pct")
        if isinstance(random_baseline.get("mean_return_pct"), (int, float))
        else None,
        arm_priced_n,
        int(random_baseline.get("priced_n", 0)),
    )
    separable_gate = _gate_separable_vs_spine(arm_mean, spine_mean, arm_priced_n, spine_n)

    if len(arm_rows) == 0:
        overall = "KILL_RESIDUAL_EMPTY" if arm_id == "L3_minus_v2" else "INCOMPLETE"
        lift_gate = "N/A"
        separable_gate = "N/A"
    elif lift_gate == "INCOMPLETE" or separable_gate == "INCOMPLETE":
        overall = "INCOMPLETE"
    elif separable_gate == "FAIL" and lift_gate == "FAIL":
        overall = (
            "KILL_NO_SEPARABLE_L3_PACKET"
            if arm_id == "L3_full"
            else "KILL_RESIDUAL_PRIMARY"
        )
    elif lift_gate == "FAIL":
        overall = "FAIL_NO_LIFT_VS_RANDOM"
    elif separable_gate == "FAIL":
        overall = "FAIL_NO_LIFT_VS_SPINE"
    else:
        overall = "DIRECTIONAL_NON_KILL"

    falsifiers = ["K-spine-random", "K-S1-collapse"]
    if arm_id == "L3_minus_v2":
        falsifiers.extend(["K-residual-primary", "K-residual-thin", "K-residual-empty"])
    elif arm_id == "L3_full":
        falsifiers.extend(["K-L3-cohort"])

    return {
        "horizon": horizon,
        "arm_id": arm_id,
        "l3_packet_rule": "L3_PACKET_V0",
        "arm_n": len(arm_rows),
        "complement_arm_n": len(complement_rows),
        "arm_cohort": {"mean_return_pct": arm_mean, "priced_n": arm_priced_n},
        "complement_cohort": {"mean_return_pct": complement_mean, "priced_n": complement_n},
        "spine_baseline": {"mean_return_pct": spine_mean, "priced_n": spine_n},
        "random_baseline": random_baseline,
        "gates": {
            "no_lift_vs_random": {
                "comparator": f"arm_mean_{horizon} > random_same_n_{horizon}",
                "min_priced_per_arm": MIN_PRICED_FOR_KILL,
                "result": lift_gate,
            },
            "separable_vs_spine": {
                "comparator": f"arm_mean_{horizon} > spine_mean_{horizon}",
                "min_priced_per_arm": MIN_PRICED_FOR_KILL,
                "result": separable_gate,
            },
        },
        "overall": overall,
        "falsifiers": falsifiers,
    }


def score_l3_packet_at_horizon(
    book: Sequence[ScoredRow],
    *,
    horizon: str,
    seed: int,
) -> dict[str, Any]:
    """EXP-005 parent arm — L3_PACKET_V0 (L3_full), legacy key names."""
    raw = score_l3_arm_at_horizon(
        book,
        arm_id="L3_full",
        v2_runner_sigs=set(),
        horizon=horizon,
        seed=seed,
    )
    return {
        "horizon": raw["horizon"],
        "l3_packet_rule": raw["l3_packet_rule"],
        "packet_arm_n": raw["arm_n"],
        "complement_arm_n": raw["complement_arm_n"],
        "packet_cohort": raw["arm_cohort"],
        "complement_cohort": raw["complement_cohort"],
        "spine_baseline": raw["spine_baseline"],
        "random_baseline": raw["random_baseline"],
        "gates": raw["gates"],
        "overall": raw["overall"],
        "falsifiers": raw["falsifiers"],
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
    v2_runner = build_v2_runner_signatures(loaded, book)
    v2_reject: set[str] = set()
    for item in loaded:
        row = item.row
        if not is_bonding_create(row):
            continue
        sig = row.get("signature")
        if not isinstance(sig, str) or sig not in book_sigs:
            continue
        label, _reasons = rules.evaluate(row)
        if label == "reject":
            v2_reject.add(sig)

    l3_packet = {r.signature for r in book if l3_packet_selected(r.features) and isinstance(r.signature, str)}
    l3_minus = {
        sig
        for sig in l3_packet
        if sig not in v2_runner
    }
    l3_intersect = l3_packet & v2_runner
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
        "residual_arm_overlap": {
            "L3_minus_v2_n": len(l3_minus),
            "L3_intersect_v2_n": len(l3_intersect),
            "pct_l3_minus_v2_of_l3": _pct(len(l3_minus), l3_n),
            "pct_l3_intersect_v2_of_l3": _pct(len(l3_intersect), l3_n),
            "jaccard_l3_minus_v2_vs_v2_runner": (
                len(l3_minus & v2_runner) / len(l3_minus | v2_runner)
                if (l3_minus | v2_runner)
                else None
            ),
        },
    }


def score_residual_arm_by_regime_gate(
    book: Sequence[ScoredRow],
    *,
    arm_id: ResidualArmId,
    v2_runner_sigs: set[str],
    horizon: str,
    seed: int,
) -> dict[str, Any]:
    by_gate: dict[str, list[ScoredRow]] = defaultdict(list)
    for row in book:
        by_gate[row.features.regime_gate_key].append(row)

    per_gate = {
        gate: score_l3_arm_at_horizon(
            rows,
            arm_id=arm_id,
            v2_runner_sigs=v2_runner_sigs,
            horizon=horizon,
            seed=seed + idx,
        )
        for idx, (gate, rows) in enumerate(sorted(by_gate.items()))
    }
    return {
        "horizon": horizon,
        "arm_id": arm_id,
        "regime_gate_keys_n": len(by_gate),
        "arm_by_regime_gate_key": per_gate,
        "note": "S1 — no cross-regime merge; per-gate priced_n reported honestly.",
    }


def _detect_residual_s1_collapse(
    *,
    aggregate_60s: Mapping[str, Any],
    stratified: Mapping[str, Any],
) -> dict[str, Any]:
    if not _arm_passes_both_gates(aggregate_60s):
        return {
            "s1_collapse": False,
            "note": "Aggregate residual primary arm did not pass both gates — S1 collapse N/A.",
        }

    per_gate = stratified.get("arm_by_regime_gate_key") or {}
    passing_gates = [
        gate for gate, result in per_gate.items() if _arm_passes_both_gates(result)
    ]
    collapsed = len(passing_gates) == 0
    return {
        "s1_collapse": collapsed,
        "per_gate_passing_both_gates": passing_gates,
        "note": (
            "Falsifier K-S1-collapse: aggregate L3_minus_v2 pass vanishes under every regime_gate_key."
            if collapsed
            else "At least one regime_gate_key retains sep+random PASS for L3_minus_v2."
        ),
    }


def build_exp005b_summary(
    *,
    paths: Sequence[Path],
    marks_paths: Sequence[Path],
    seed: int,
    book: Sequence[ScoredRow],
    precompute_meta: Mapping[str, Any],
    data_blockers: list[str],
    exp002c_overlap: Mapping[str, Any],
    v2_runner_sigs: set[str],
) -> dict[str, Any]:
    residual_by_arm: dict[str, dict[str, Any]] = {}
    for arm_id in RESIDUAL_ARMS:
        residual_by_arm[arm_id] = {
            horizon: score_l3_arm_at_horizon(
                book,
                arm_id=arm_id,
                v2_runner_sigs=v2_runner_sigs,
                horizon=horizon,
                seed=seed,
            )
            for horizon in PRIMARY_HORIZONS
        }

    primary_60s = residual_by_arm[PRIMARY_RESIDUAL_ARM][PRIMARY_HORIZON]
    stratified = score_residual_arm_by_regime_gate(
        book,
        arm_id=PRIMARY_RESIDUAL_ARM,
        v2_runner_sigs=v2_runner_sigs,
        horizon=PRIMARY_HORIZON,
        seed=seed,
    )
    s1 = _detect_residual_s1_collapse(aggregate_60s=primary_60s, stratified=stratified)

    priced_any = sum(
        1 for r in book if _horizon_return(r.as_dict(), PRIMARY_HORIZON) is not None
    )
    primary_arm_n = primary_60s["arm_n"]
    primary_priced_n = int((primary_60s.get("arm_cohort") or {}).get("priced_n") or 0)

    hg2_ref = score_hypothesis_at_horizon(book, hyp_id="H-G2", horizon=PRIMARY_HORIZON, seed=seed)

    if data_blockers or not book or priced_any < MIN_PRICED_FOR_KILL:
        overall = "INCOMPLETE"
    elif primary_arm_n == 0:
        overall = "KILL_RESIDUAL_EMPTY"
    elif primary_priced_n < MIN_PRICED_FOR_KILL:
        overall = "INCOMPLETE"
    elif s1.get("s1_collapse"):
        overall = "KILL_L3_RESIDUAL_S1_COLLAPSE"
    elif not _arm_passes_both_gates(primary_60s):
        agg = primary_60s.get("overall")
        overall = agg if isinstance(agg, str) else "KILL_RESIDUAL_PRIMARY"
    else:
        overall = "DIRECTIONAL_NON_KILL"

    return {
        "exp": EXP_005B_ID,
        "parent_exp": EXP_ID,
        "authorization_note": (
            "Helm/Vaan 2026-09-23 explicit sealed-measure re-auth for EXP-005b; "
            "merge of docs #38 (bf31d49) was registration only, not authorize-run."
        ),
        "unit": "sealed_ingest_hot_bonding_create_l3_residual_falsifier",
        "paths": [str(p) for p in paths],
        "marks_paths": [str(p) for p in marks_paths],
        "seed": seed,
        "primary_horizon": PRIMARY_HORIZON,
        "primary_residual_arm": PRIMARY_RESIDUAL_ARM,
        "horizons": list(PRIMARY_HORIZONS),
        "population_n": len(book),
        f"priced_{PRIMARY_HORIZON}_n": priced_any,
        "l3_minus_v2_arm_n": primary_arm_n,
        "min_priced_for_kill": MIN_PRICED_FOR_KILL,
        "l3_packet_rule_v0": L3_PACKET_RULE_V0,
        "precompute": dict(precompute_meta),
        "residual_arms": residual_by_arm,
        "regime_stratification_s1_primary": stratified,
        "s1_collapse_falsifier": s1,
        "exp002c_runner_overlap": dict(exp002c_overlap),
        "v2_runner_n_in_graph_population": len(v2_runner_sigs),
        "parent_h_g2_reference_60s": {
            "overall": hg2_ref.get("overall"),
            "note": "H-G2 burst lane stays killed — reference only, not an EXP-005b scored arm.",
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
            "Residual arms reuse EXP-004 weak_ws graph precompute + read-only EXP-002c v2 labels.",
            "L3_intersect_v2 is attribution-only — not a Discovery promote path.",
            "No mean_return_pct claims in git — gates + taxonomy only.",
        ],
    }


def render_report_005b(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary['exp']} — L3 residual falsifier report",
        "",
        f"**Overall @ {PRIMARY_HORIZON} (primary {PRIMARY_RESIDUAL_ARM}):** {summary.get('overall')}",
        f"**Population:** {summary.get('population_n')}",
        f"**L3_minus_v2 arm_n:** {summary.get('l3_minus_v2_arm_n')}",
        f"**priced_{PRIMARY_HORIZON}_n (book):** {summary.get(f'priced_{PRIMARY_HORIZON}_n')}",
        "",
        "## Residual arms @ 60s",
        "",
    ]
    arms = summary.get("residual_arms") or {}
    for arm_id in RESIDUAL_ARMS:
        block = (arms.get(arm_id) or {}).get(PRIMARY_HORIZON, {})
        gates = block.get("gates") or {}
        lines.append(
            f"- **{arm_id}** overall={block.get('overall')} arm_n={block.get('arm_n')} "
            f"priced_n={block.get('arm_cohort', {}).get('priced_n')} "
            f"sep={gates.get('separable_vs_spine', {}).get('result')} "
            f"random={gates.get('no_lift_vs_random', {}).get('result')}"
        )
    ov = summary.get("exp002c_runner_overlap") or {}
    lines.append("")
    lines.append("## EXP-002c overlap (parent attribution)")
    lines.append(
        f"- l3_packet_n={ov.get('l3_packet_n')} overlap_l3∩v2={ov.get('overlap_l3_and_v2_runner_n')} "
        f"jaccard={ov.get('jaccard_l3_vs_v2_runner')}"
    )
    res_ov = ov.get("residual_arm_overlap") or {}
    lines.append(
        f"- L3_minus_v2_n={res_ov.get('L3_minus_v2_n')} L3_intersect_v2_n={res_ov.get('L3_intersect_v2_n')}"
    )
    s1 = summary.get("s1_collapse_falsifier") or {}
    lines.append("")
    lines.append(f"**S1 collapse (primary):** {s1.get('s1_collapse')} — {s1.get('note')}")
    return "\n".join(lines) + "\n"


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


def cross_day_overall_residual(day_overalls: Sequence[str]) -> str:
    """Cross-day stamp for EXP-005b primary arm (L3_minus_v2 @60s)."""
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
        "KILL_RESIDUAL_PRIMARY",
        "KILL_RESIDUAL_EMPTY",
        "FAIL_NO_LIFT_VS_RANDOM",
        "FAIL_NO_LIFT_VS_SPINE",
        "KILL_L3_RESIDUAL_S1_COLLAPSE",
        "KILL_NO_SEPARABLE_L3_PACKET",
    }
    if all(o in kills for o in day_overalls):
        if all(o == "KILL_RESIDUAL_EMPTY" for o in day_overalls):
            return "KILL_RESIDUAL_EMPTY"
        return "KILL_RESIDUAL_PRIMARY"
    return "INCOMPLETE"


def run_exp005b(
    paths: Sequence[Path],
    *,
    marks_paths: Sequence[Path] | None = None,
    seed: int = DEFAULT_SEED,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = DEFAULT_PREFIX_005B,
) -> dict[str, Any]:
    marks_paths = list(marks_paths or [])
    all_paths = list(paths) + marks_paths
    loaded, _malformed = load_jsonl_files(all_paths) if all_paths else ([], 0)
    graph_pairs, pre_meta = precompute_graph_book(loaded)
    mint_series = build_mint_price_series(loaded)
    book = build_scored_book(graph_pairs, mint_series)
    blockers = _detect_data_blockers(paths, marks_paths, loaded, book)
    v2_runner_sigs = build_v2_runner_signatures(loaded, book) if book else set()
    overlap = compute_exp002c_runner_overlap(loaded, book) if book else {
        "note": "deferred — empty graph book",
    }
    summary = build_exp005b_summary(
        paths=paths,
        marks_paths=marks_paths,
        seed=seed,
        book=book,
        precompute_meta=pre_meta,
        data_blockers=blockers,
        exp002c_overlap=overlap,
        v2_runner_sigs=v2_runner_sigs,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / f"{prefix}_summary.json"
    report_path = output_dir / f"{prefix}_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(render_report_005b(summary), encoding="utf-8")
    summary["_artifacts"] = {
        "summary_json": str(summary_path),
        "report_md": str(report_path),
    }
    return summary


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
    parser = argparse.ArgumentParser(
        description=f"{EXP_ID} L3 follow packet measurement (EXP-005b residual via --residual-falsifier)"
    )
    parser.add_argument("observe_paths", nargs="+", type=Path, help="Sealed observe JSONL (day-aligned).")
    parser.add_argument("--marks", nargs="+", type=Path, required=True, help="EXP-003 marks JSONL (same day).")
    parser.add_argument(
        "--residual-falsifier",
        action="store_true",
        help=f"Run {EXP_005B_ID} residual arms (primary L3_minus_v2).",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default=None)
    args = parser.parse_args(argv)

    prefix = args.prefix
    if prefix is None:
        prefix = DEFAULT_PREFIX_005B if args.residual_falsifier else DEFAULT_PREFIX

    if args.residual_falsifier:
        summary = run_exp005b(
            args.observe_paths,
            marks_paths=args.marks,
            seed=args.seed,
            output_dir=args.output_dir,
            prefix=prefix,
        )
        primary_60s = (summary.get("residual_arms") or {}).get(PRIMARY_RESIDUAL_ARM, {}).get(
            PRIMARY_HORIZON, {}
        )
        print(
            json.dumps(
                {
                    "exp": summary["exp"],
                    "overall": summary["overall"],
                    "primary_arm": PRIMARY_RESIDUAL_ARM,
                    "l3_minus_v2_arm_n": summary.get("l3_minus_v2_arm_n"),
                    "primary_priced_n_60s": (primary_60s.get("arm_cohort") or {}).get("priced_n"),
                    "primary_sep": (primary_60s.get("gates") or {})
                    .get("separable_vs_spine", {})
                    .get("result"),
                    "primary_random": (primary_60s.get("gates") or {})
                    .get("no_lift_vs_random", {})
                    .get("result"),
                    "artifacts": summary.get("_artifacts"),
                },
                indent=2,
            )
        )
    else:
        summary = run_exp005(
            args.observe_paths,
            marks_paths=args.marks,
            seed=args.seed,
            output_dir=args.output_dir,
            prefix=prefix,
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
