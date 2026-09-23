#!/usr/bin/env python3
"""EXP-004 — Graph creator-recurrence Discovery measurement (paper-only).

Offline: sealed ingest JSONL + optional EXP-003 marks. Regime-gated as-of-T graph
features (H-G1…H-G4) vs spine-only baseline on the full bonding-create detect book.
No RPC, no live capital, no evaluate retune, no invented lift.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from tools.exp001_mislabel import LoadedRow, load_jsonl_files
from tools.exp002_paper_runner import (
    HORIZON_SECONDS,
    LIFT_EPSILON,
    MIN_PRICED_FOR_KILL,
    PARITY_REL_TOLERANCE,
    PriceMark,
    _bonk_pool_hint,
    _gate_lift,
    _gate_parity,
    _mean_returns,
    _parse_iso_ts,
    _sealed_field,
    _sealed_float,
    build_mint_price_series,
    compute_outcomes,
    is_bonding_create,
)
EXP_ID = "EXP-004-graph-creator-recurrence-v0"
DEFAULT_SEED = 1
DEFAULT_OUTPUT_DIR = Path("data/observe")
DEFAULT_PREFIX = "_exp004"

PRIMARY_HORIZONS = ("1s", "5s", "15s", "30s", "60s")
PRIMARY_HORIZON = "60s"
BURST_LOOKBACK_S = 3600.0
GRAPH_SNAPSHOT_SCHEMA = "graph_snapshot_v0"
GateResult = Literal["PASS", "FAIL", "INCOMPLETE", "N/A", "STUB"]

IN_SCOPE_HYPOTHESES = frozenset({"H-G1", "H-G2", "H-G3", "H-G4"})
OUT_OF_SCOPE_NOTE = (
    "H-G5 (reserve collinearity) and H-G6 (migration retrospective) are taxonomy-only "
    "for v0 — not scored in this runner."
)


def regime_gate_key(regime_id: str | None) -> str:
    """S1 scope key — full regime_id string (Graph open Q5: explicit, no hash)."""
    if isinstance(regime_id, str) and regime_id.strip():
        return regime_id.strip()
    return "regime_gate=unknown|reason=missing_regime_id"


@dataclass
class CreatorState:
    first_seen_t: datetime
    prior_mint_count: int = 0
    last_create_t: datetime | None = None
    prior_buy_signatures: set[tuple[float | None, float | None]] = field(default_factory=set)
    create_times_lookback: list[datetime] = field(default_factory=list)


@dataclass
class GraphFeatures:
    regime_id: str | None
    regime_gate_key: str
    trader_public_key: str
    t_decision: datetime
    t_precompute_as_of: datetime
    book_t0: datetime
    creator_age_seconds: float | None
    prior_mint_count: int
    burst_count: int
    min_gap_seconds: float | None
    recurrence_weak: bool
    early_wallet_dt_min_seconds: float | None
    cluster_tier: str
    evidence_tier: str = "weak_ws"
    bonk_parked: bool = False

    def as_features_dict(self) -> dict[str, Any]:
        return {
            "creator_age_seconds": self.creator_age_seconds,
            "prior_mint_count": self.prior_mint_count,
            "burst_count": self.burst_count,
            "min_gap_seconds": self.min_gap_seconds,
            "creator_buyer_recurrence_weak": self.recurrence_weak,
            "early_wallet_dt_min_seconds": self.early_wallet_dt_min_seconds,
            "cluster_tier": self.cluster_tier,
            "evidence_tier": self.evidence_tier,
        }

    def snapshot_row(
        self,
        *,
        parent_signature: str,
        mint: str,
        latency_delta_ws_event_ms: float | None,
    ) -> dict[str, Any]:
        return {
            "schema_version": GRAPH_SNAPSHOT_SCHEMA,
            "type": "graph_snapshot_v0",
            "parent_signature": parent_signature,
            "mint": mint,
            "T_decision": self.t_decision.isoformat(),
            "t_precompute_as_of": self.t_precompute_as_of.isoformat(),
            "regime_id": self.regime_id,
            "regime_gate_key": self.regime_gate_key,
            "traderPublicKey": self.trader_public_key,
            "features": self.as_features_dict(),
            "book_t0": self.book_t0.isoformat(),
            "latency_delta_ws_event_ms": latency_delta_ws_event_ms,
        }


@dataclass
class ScoredRow:
    source_path: str
    line_no: int
    signature: str
    mint: str
    t_ws: str
    regime_id: str | None
    features: GraphFeatures
    outcomes: dict[str, Any]
    entry_price: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "line_no": self.line_no,
            "signature": self.signature,
            "mint": self.mint,
            "t_ws": self.t_ws,
            "regime_id": self.regime_id,
            "regime_gate_key": self.features.regime_gate_key,
            "graph_features": self.features.as_features_dict(),
            "entry_price": self.entry_price,
            **self.outcomes,
        }


def _latency_delta_ms(row: Mapping[str, Any]) -> float | None:
    t_ws = _parse_iso_ts(row.get("t_ws"))
    t_ev = _parse_iso_ts(row.get("t_event"))
    if t_ws is None or t_ev is None:
        return None
    return (t_ws - t_ev).total_seconds() * 1000.0


def _creator_pk(row: Mapping[str, Any]) -> str | None:
    pk = _sealed_field(row, "traderPublicKey")
    if isinstance(pk, str) and pk.strip() and pk != "UNK":
        return pk.strip()
    return None


def _buy_pattern(row: Mapping[str, Any]) -> tuple[float | None, float | None]:
    return (_sealed_float(row, "initialBuy"), _sealed_float(row, "solAmount"))


def _prune_lookback(times: list[datetime], t_decision: datetime, lookback_s: float) -> list[datetime]:
    cutoff = t_decision - timedelta(seconds=lookback_s)
    return [t for t in times if t >= cutoff]


def compute_graph_features_for_row(
    *,
    row: Mapping[str, Any],
    t_decision: datetime,
    book_t0: datetime,
    state_by_gate: dict[str, dict[str, CreatorState]],
    burst_lookback_s: float,
) -> GraphFeatures | None:
    if not is_bonding_create(row):
        return None
    pk = _creator_pk(row)
    if pk is None:
        return None
    rid = row.get("regime_id") if isinstance(row.get("regime_id"), str) else None
    gate = regime_gate_key(rid)
    bonk = _bonk_pool_hint(row)

    per_gate = state_by_gate.setdefault(gate, {})
    prior = per_gate.get(pk)

    if prior is None:
        age_s = 0.0
        prior_n = 0
        burst_n = 0
        min_gap: float | None = None
        recurrence = False
    else:
        age_s = (t_decision - prior.first_seen_t).total_seconds()
        prior_n = prior.prior_mint_count
        burst_n = len(_prune_lookback(prior.create_times_lookback, t_decision, burst_lookback_s))
        if prior.last_create_t is not None:
            min_gap = (t_decision - prior.last_create_t).total_seconds()
        else:
            min_gap = None
        pattern = _buy_pattern(row)
        recurrence = pattern in prior.prior_buy_signatures and prior_n > 0

    return GraphFeatures(
        regime_id=rid,
        regime_gate_key=gate,
        trader_public_key=pk,
        t_decision=t_decision,
        t_precompute_as_of=t_decision,
        book_t0=book_t0,
        creator_age_seconds=age_s if age_s >= 0 else None,
        prior_mint_count=prior_n,
        burst_count=burst_n,
        min_gap_seconds=min_gap,
        recurrence_weak=recurrence,
        early_wallet_dt_min_seconds=None,
        cluster_tier="weak_creator_only",
        bonk_parked=bonk,
    )


def _update_creator_state(
    *,
    row: Mapping[str, Any],
    t_decision: datetime,
    state_by_gate: dict[str, dict[str, CreatorState]],
    burst_lookback_s: float,
) -> None:
    """Advance rolling state after features for this row are fixed (strict T_prior < T)."""
    if _bonk_pool_hint(row):
        return
    pk = _creator_pk(row)
    if pk is None:
        return
    rid = row.get("regime_id") if isinstance(row.get("regime_id"), str) else None
    gate = regime_gate_key(rid)
    per_gate = state_by_gate.setdefault(gate, {})
    st = per_gate.get(pk)
    pattern = _buy_pattern(row)
    if st is None:
        st = CreatorState(first_seen_t=t_decision)
        per_gate[pk] = st
    st.prior_mint_count += 1
    st.prior_buy_signatures.add(pattern)
    st.last_create_t = t_decision
    st.create_times_lookback.append(t_decision)
    st.create_times_lookback = _prune_lookback(st.create_times_lookback, t_decision, burst_lookback_s)


def precompute_graph_book(
    loaded: Sequence[LoadedRow],
    *,
    burst_lookback_s: float = BURST_LOOKBACK_S,
) -> tuple[list[tuple[LoadedRow, GraphFeatures]], dict[str, Any]]:
    """Single-pass as-of-T graph features on bonding creates sorted by t_ws."""
    candidates: list[tuple[LoadedRow, datetime]] = []
    for item in loaded:
        row = item.row
        if not is_bonding_create(row):
            continue
        t = _parse_iso_ts(row.get("t_ws"))
        if t is None:
            continue
        candidates.append((item, t))
    candidates.sort(key=lambda x: (x[1], x[0].line_no))

    book_t0 = candidates[0][1] if candidates else datetime.now(timezone.utc)
    state_by_gate: dict[str, dict[str, CreatorState]] = {}
    out: list[tuple[LoadedRow, GraphFeatures]] = []
    skipped_bonk = 0
    skipped_no_pk = 0

    for item, t_decision in candidates:
        row = item.row
        if _bonk_pool_hint(row):
            skipped_bonk += 1
            continue
        feats = compute_graph_features_for_row(
            row=row,
            t_decision=t_decision,
            book_t0=book_t0,
            state_by_gate=state_by_gate,
            burst_lookback_s=burst_lookback_s,
        )
        if feats is None:
            skipped_no_pk += 1
            continue
        out.append((item, feats))
        _update_creator_state(
            row=row,
            t_decision=t_decision,
            state_by_gate=state_by_gate,
            burst_lookback_s=burst_lookback_s,
        )

    meta = {
        "book_t0": book_t0.isoformat(),
        "bonding_create_candidates_n": len(candidates),
        "graph_scored_n": len(out),
        "bonk_parked_excluded_n": skipped_bonk,
        "missing_trader_public_key_n": skipped_no_pk,
        "burst_lookback_s": burst_lookback_s,
        "regime_gate_key_policy": "full_regime_id_string",
        "evidence_tier": "weak_ws",
        "dec_005": "weak_create_spine_traderPublicKey_only_day1",
    }
    return out, meta


def build_scored_book(
    graph_rows: Sequence[tuple[LoadedRow, GraphFeatures]],
    mint_series: Mapping[str, Sequence[PriceMark]],
) -> list[ScoredRow]:
    scored: list[ScoredRow] = []
    for item, feats in graph_rows:
        row = item.row
        sig = row.get("signature")
        mint = row.get("mint")
        if not isinstance(sig, str) or not isinstance(mint, str):
            continue
        t_ws_s = row.get("t_ws")
        t0 = _parse_iso_ts(t_ws_s)
        marks = mint_series.get(mint, [])
        entry = None
        for key in ("marketCapSol", "vSolInBondingCurve"):
            val = row.get(key)
            if val is None and isinstance(row.get("ws_payload"), dict):
                val = row["ws_payload"].get(key)
            if val is not None:
                try:
                    entry = float(val)
                    break
                except (TypeError, ValueError):
                    pass
        outcomes = (
            compute_outcomes(t0=t0, p0=entry, marks=marks)
            if t0 is not None
            else compute_outcomes(
                t0=datetime.now(timezone.utc),
                p0=None,
                marks=[],
            )
        )
        scored.append(
            ScoredRow(
                source_path=item.source_path,
                line_no=item.line_no,
                signature=sig,
                mint=mint,
                t_ws=str(t_ws_s),
                regime_id=feats.regime_id,
                features=feats,
                outcomes=outcomes,
                entry_price=entry,
            )
        )
    return scored


def _horizon_return(row: Mapping[str, Any], horizon: str) -> float | None:
    horizons = row.get("horizons")
    if not isinstance(horizons, dict):
        return None
    h = horizons.get(horizon)
    if not isinstance(h, dict) or h.get("status") != "ok":
        return None
    val = h.get("return_pct")
    return float(val) if isinstance(val, (int, float)) else None


def _cohort_mean(rows: Sequence[Mapping[str, Any]], horizon: str) -> tuple[float | None, int]:
    return _mean_returns(rows, horizon)


def _random_same_n(
    pool: Sequence[Mapping[str, Any]],
    *,
    sample_n: int,
    horizon: str,
    seed: int,
) -> dict[str, Any]:
    if sample_n <= 0:
        return {"sample_n": 0, "priced_n": 0, "mean_return_pct": None}
    rng = random.Random(seed)
    if sample_n >= len(pool):
        sample = list(pool)
    else:
        sample = rng.sample(list(pool), sample_n)
    mean, priced_n = _cohort_mean(sample, horizon)
    return {
        "sample_n": len(sample),
        "priced_n": priced_n,
        "mean_return_pct": mean,
        "allocation": "simple_random_same_n_as_positive_arm",
    }


def _gate_separable_vs_spine(
    arm_mean: float | None,
    spine_mean: float | None,
    arm_n: int,
    spine_n: int,
) -> GateResult:
    if arm_n < MIN_PRICED_FOR_KILL or spine_n < MIN_PRICED_FOR_KILL:
        return "INCOMPLETE"
    if arm_mean is None or spine_mean is None:
        return "INCOMPLETE"
    return "PASS" if arm_mean > spine_mean + LIFT_EPSILON else "FAIL"


def _select_hg1_positive(row: ScoredRow) -> bool:
    return row.features.prior_mint_count > 0


def _select_hg1_negative(row: ScoredRow) -> bool:
    return row.features.prior_mint_count == 0


def _select_hg2_positive(row: ScoredRow) -> bool:
    return row.features.burst_count >= 2


def _select_hg2_negative(row: ScoredRow) -> bool:
    return row.features.burst_count < 2


def _select_hg3_positive(row: ScoredRow) -> bool:
    return row.features.recurrence_weak


def _select_hg3_negative(row: ScoredRow) -> bool:
    return not row.features.recurrence_weak


def _select_hg4_positive(row: ScoredRow) -> bool:
    return row.features.early_wallet_dt_min_seconds is not None


def _select_hg4_negative(row: ScoredRow) -> bool:
    return row.features.early_wallet_dt_min_seconds is None


HYPOTHESIS_ARMS: dict[str, dict[str, Any]] = {
    "H-G1": {
        "description": "Sealed-history creator age / prior-mints (repeat vs novel creator)",
        "positive": _select_hg1_positive,
        "negative": _select_hg1_negative,
        "falsifier": "K-HG1",
    },
    "H-G2": {
        "description": "Creator burst cluster within lookback",
        "positive": _select_hg2_positive,
        "negative": _select_hg2_negative,
        "falsifier": "K-HG2",
    },
    "H-G3": {
        "description": "Weak creator↔buyer recurrence (initialBuy+solAmount pattern)",
        "positive": _select_hg3_positive,
        "negative": _select_hg3_negative,
        "falsifier": "K-HG3",
    },
    "H-G4": {
        "description": "Early-wallet Δt cluster (create-spine; often empty)",
        "positive": _select_hg4_positive,
        "negative": _select_hg4_negative,
        "falsifier": "K-HG4",
    },
}


def score_hypothesis_at_horizon(
    book: Sequence[ScoredRow],
    *,
    hyp_id: str,
    horizon: str,
    seed: int,
) -> dict[str, Any]:
    spec = HYPOTHESIS_ARMS[hyp_id]
    pos_sel = spec["positive"]
    neg_sel = spec["negative"]
    pos_rows = [r.as_dict() for r in book if pos_sel(r)]
    neg_rows = [r.as_dict() for r in book if neg_sel(r)]
    spine_rows = [r.as_dict() for r in book]

    pos_mean, pos_n = _cohort_mean(pos_rows, horizon)
    neg_mean, neg_n = _cohort_mean(neg_rows, horizon)
    spine_mean, spine_n = _cohort_mean(spine_rows, horizon)

    random_baseline = _random_same_n(spine_rows, sample_n=len(pos_rows), horizon=horizon, seed=seed)

    lift_gate = _gate_lift(
        pos_mean,
        random_baseline.get("mean_return_pct")
        if isinstance(random_baseline.get("mean_return_pct"), (int, float))
        else None,
        pos_n,
        int(random_baseline.get("priced_n", 0)),
    )
    separable_gate = _gate_separable_vs_spine(pos_mean, spine_mean, pos_n, spine_n)
    parity_gate = _gate_parity(pos_mean, neg_mean, pos_n, neg_n)

    if hyp_id == "H-G4" and len(pos_rows) == 0:
        overall = "INCOMPLETE"
        lift_gate = "N/A"
        separable_gate = "N/A"
        parity_gate = "N/A"
        sparse_note = (
            "Free create-spine exposes creator timing only; early-wallet Δt arms are "
            "empty by construction — not a fabricated zero."
        )
    else:
        sparse_note = None
        if lift_gate == "INCOMPLETE" or separable_gate == "INCOMPLETE" or parity_gate == "INCOMPLETE":
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
        "hypothesis": hyp_id,
        "horizon": horizon,
        "description": spec["description"],
        "falsifier": spec["falsifier"],
        "positive_arm_n": len(pos_rows),
        "negative_arm_n": len(neg_rows),
        "positive_cohort": {"mean_return_pct": pos_mean, "priced_n": pos_n},
        "negative_cohort": {"mean_return_pct": neg_mean, "priced_n": neg_n},
        "spine_baseline": {"mean_return_pct": spine_mean, "priced_n": spine_n},
        "random_baseline": random_baseline,
        "gates": {
            "no_lift_vs_random": {
                "comparator": f"positive_mean_{horizon} > random_same_n_{horizon}",
                "min_priced_per_arm": MIN_PRICED_FOR_KILL,
                "result": lift_gate,
            },
            "separable_vs_spine": {
                "comparator": f"positive_mean_{horizon} > spine_mean_{horizon}",
                "min_priced_per_arm": MIN_PRICED_FOR_KILL,
                "result": separable_gate,
            },
            "positive_negative_parity": {
                "comparator": (
                    f"|pos_mean - neg_mean| / max(|means|) <= {PARITY_REL_TOLERANCE} "
                    "→ no separable graph arms (reject↔runner analog)"
                ),
                "min_priced_per_arm": MIN_PRICED_FOR_KILL,
                "result": parity_gate,
            },
        },
        "overall": overall,
        "sparse_note": sparse_note,
    }


def regime_stratified_counts(book: Sequence[ScoredRow]) -> dict[str, Any]:
    by_gate: Counter[str] = Counter()
    repeat_by_gate: Counter[str] = Counter()
    for row in book:
        g = row.features.regime_gate_key
        by_gate[g] += 1
        if row.features.prior_mint_count > 0:
            repeat_by_gate[g] += 1
    return {
        "rows_by_regime_gate_key": dict(sorted(by_gate.items())),
        "repeat_creator_rows_by_regime_gate_key": dict(sorted(repeat_by_gate.items())),
        "note": "No cross-regime merge in precompute state (S1).",
    }


def build_summary(
    *,
    paths: Sequence[Path],
    marks_paths: Sequence[Path],
    seed: int,
    book: Sequence[ScoredRow],
    precompute_meta: Mapping[str, Any],
    data_blockers: list[str],
) -> dict[str, Any]:
    hypotheses: dict[str, Any] = {}
    for hyp_id in sorted(IN_SCOPE_HYPOTHESES):
        hypotheses[hyp_id] = {
            horizon: score_hypothesis_at_horizon(book, hyp_id=hyp_id, horizon=horizon, seed=seed)
            for horizon in PRIMARY_HORIZONS
        }

    priced_any = sum(
        1
        for r in book
        if _horizon_return(r.as_dict(), PRIMARY_HORIZON) is not None
    )

    if not paths or len(book) == 0:
        overall = "INCOMPLETE"
    elif priced_any < MIN_PRICED_FOR_KILL:
        overall = "INCOMPLETE"
    elif data_blockers:
        overall = "INCOMPLETE"
    else:
        primary_results = [hypotheses[h][PRIMARY_HORIZON]["overall"] for h in sorted(IN_SCOPE_HYPOTHESES)]
        if all(r in ("KILL_NO_SEPARABLE_ARM", "FAIL_NO_LIFT_VS_RANDOM", "FAIL_NO_LIFT_VS_SPINE") for r in primary_results):
            overall = "KILL_H_GRAPH"
        elif any(r == "DIRECTIONAL_NON_KILL" for r in primary_results):
            overall = "DIRECTIONAL_WATCH"
        else:
            overall = "INCOMPLETE"

    return {
        "exp": EXP_ID,
        "unit": "sealed_ingest_hot_bonding_create_full_book_graph_discovery",
        "paths": [str(p) for p in paths],
        "marks_paths": [str(p) for p in marks_paths],
        "seed": seed,
        "primary_horizon": PRIMARY_HORIZON,
        "horizons": list(PRIMARY_HORIZONS),
        "population_n": len(book),
        "priced_entry_n": sum(1 for r in book if r.entry_price is not None),
        f"priced_{PRIMARY_HORIZON}_n": priced_any,
        "min_priced_for_kill": MIN_PRICED_FOR_KILL,
        "precompute": dict(precompute_meta),
        "regime_stratification": regime_stratified_counts(book),
        "hypotheses": hypotheses,
        "out_of_scope": OUT_OF_SCOPE_NOTE,
        "data_blockers": data_blockers,
        "soft_fences": [
            "no_densify_marks_for_power_until_non_adverse_directional_hyp",
            "no_exp_002c_retune",
            "no_h_g5_h_g6_lift_claims",
        ],
        "overall": overall,
        "limitations": [
            "Graph features are weak_ws create-spine only (DEC-005 day-1).",
            "Marks are outcome meters only; horizons use T < t_mark <= T+H.",
            "Bonk/mayhem parked rows excluded from graph precompute population (S5).",
            "H-G4 early-wallet Δt typically empty on create-only JSONL.",
            "No evaluate rule promotion from this EXP.",
        ],
    }


def write_snapshots(
    book: Sequence[ScoredRow],
    path: Path,
    loaded_rows: Sequence[tuple[LoadedRow, GraphFeatures]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for item, feats in loaded_rows:
            row = item.row
            sig = row.get("signature")
            mint = row.get("mint")
            if not isinstance(sig, str) or not isinstance(mint, str):
                continue
            delta = _latency_delta_ms(row)
            line = feats.snapshot_row(
                parent_signature=sig,
                mint=mint,
                latency_delta_ws_event_ms=delta,
            )
            fh.write(json.dumps(line, sort_keys=True) + "\n")


def _detect_data_blockers(
    paths: Sequence[Path],
    marks_paths: Sequence[Path],
    loaded: Sequence[LoadedRow],
    book: Sequence[ScoredRow],
) -> list[str]:
    blockers: list[str] = []
    if not paths:
        blockers.append("No observe JSONL paths provided.")
    else:
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            blockers.append(f"Missing sealed observe file(s): {missing}")
    if not loaded:
        blockers.append("No JSONL rows loaded — courier sealed observe from laptop or Oracle tunnel.")
    bonding = sum(1 for item in loaded if is_bonding_create(item.row))
    if loaded and bonding == 0:
        blockers.append("Loaded JSONL has zero bonding creates (ingest_hot txType=create stage=bonding).")
    if marks_paths:
        missing_marks = [str(p) for p in marks_paths if not p.is_file()]
        if missing_marks:
            blockers.append(f"Missing marks file(s): {missing_marks}")
    elif loaded:
        blockers.append(
            "No --marks paths; outcome horizons will be N/A-heavy (priced_n floor likely INCOMPLETE)."
        )
    priced_60 = sum(
        1 for r in book if _horizon_return(r.as_dict(), PRIMARY_HORIZON) is not None
    )
    if book and priced_60 < MIN_PRICED_FOR_KILL:
        blockers.append(
            f"priced_{PRIMARY_HORIZON}_n={priced_60} below MIN_PRICED_FOR_KILL={MIN_PRICED_FOR_KILL}."
        )
    return blockers


def render_report(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary['exp']} — Discovery measurement report",
        "",
        f"**Overall:** {summary.get('overall')}",
        f"**Population (graph-scored):** {summary.get('population_n')}",
        f"**priced_{PRIMARY_HORIZON}_n:** {summary.get(f'priced_{PRIMARY_HORIZON}_n')}",
        "",
    ]
    blockers = summary.get("data_blockers") or []
    if blockers:
        lines.append("## DATA BLOCKER")
        for b in blockers:
            lines.append(f"- {b}")
        lines.append("")

    lines.append("## Proof GATE checklist (primary horizon 60s)")
    hyps = summary.get("hypotheses") or {}
    for hyp_id in sorted(hyps.keys()):
        h60 = hyps[hyp_id].get(PRIMARY_HORIZON, {})
        gates = h60.get("gates") or {}
        lines.append(
            f"- **{hyp_id}** overall={h60.get('overall')} "
            f"no_lift_vs_random={gates.get('no_lift_vs_random', {}).get('result')} "
            f"separable_vs_spine={gates.get('separable_vs_spine', {}).get('result')} "
            f"parity={gates.get('positive_negative_parity', {}).get('result')}"
        )
        if h60.get("sparse_note"):
            lines.append(f"  - {h60['sparse_note']}")
    lines.append("")
    lines.append(f"_{OUT_OF_SCOPE_NOTE}_")
    return "\n".join(lines) + "\n"


def run_exp004(
    paths: Sequence[Path],
    *,
    marks_paths: Sequence[Path] | None = None,
    seed: int = DEFAULT_SEED,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = DEFAULT_PREFIX,
    write_snapshot_path: Path | None = None,
    burst_lookback_s: float = BURST_LOOKBACK_S,
) -> dict[str, Any]:
    marks_paths = list(marks_paths or [])
    all_paths = list(paths) + marks_paths
    loaded, _malformed_n = load_jsonl_files(all_paths) if all_paths else ([], 0)
    graph_pairs, pre_meta = precompute_graph_book(loaded, burst_lookback_s=burst_lookback_s)
    mint_series = build_mint_price_series(loaded)
    book = build_scored_book(graph_pairs, mint_series)
    blockers = _detect_data_blockers(paths, marks_paths, loaded, book)
    summary = build_summary(
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
    if write_snapshot_path is not None:
        write_snapshots(book, write_snapshot_path, graph_pairs)
    summary["_artifacts"] = {
        "summary_json": str(summary_path),
        "report_md": str(report_path),
        "snapshots_jsonl": str(write_snapshot_path) if write_snapshot_path else None,
    }
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{EXP_ID} offline Discovery measurement")
    parser.add_argument(
        "observe_paths",
        nargs="*",
        type=Path,
        help="Sealed observe JSONL (ingest_hot). Omit if only checking CLI.",
    )
    parser.add_argument(
        "--marks",
        nargs="*",
        type=Path,
        default=[],
        help="EXP-003 outcome_mark side files (optional).",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument(
        "--write-snapshots",
        type=Path,
        default=None,
        help="Optional graph_snapshot_v0 sidecar JSONL path.",
    )
    parser.add_argument("--burst-lookback-s", type=float, default=BURST_LOOKBACK_S)
    args = parser.parse_args(argv)

    summary = run_exp004(
        args.observe_paths,
        marks_paths=args.marks,
        seed=args.seed,
        output_dir=args.output_dir,
        prefix=args.prefix,
        write_snapshot_path=args.write_snapshots,
        burst_lookback_s=args.burst_lookback_s,
    )
    print(json.dumps({"overall": summary["overall"], "artifacts": summary.get("_artifacts")}, indent=2))
    if summary.get("data_blockers"):
        print("\nDATA BLOCKER:", file=sys.stderr)
        for b in summary["data_blockers"]:
            print(f"  - {b}", file=sys.stderr)
        return 2
    return 0 if summary["overall"] != "INCOMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
