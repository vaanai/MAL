#!/usr/bin/env python3
"""EXP-006 — Paper would-have-happened fill harness (P0 fill-sim on sealed JSONL + marks).

Paper-only: no RPC signing, no live capital. Emits gitignored paper_fill side JSONL.
Watch-list cohorts only (EXP-002c v2 runners, EXP-005b L3_minus_v2, optional L3_full).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from tools.exp001_mislabel import LoadedRow, load_jsonl_files
from tools.exp002_paper_runner import (
    HORIZON_SECONDS,
    LIFT_EPSILON,
    MIN_PRICED_FOR_KILL,
    PriceMark,
    _last_mark_as_of,
    _parse_iso_ts,
    _sealed_float,
    build_mint_price_series,
    compute_outcomes,
    is_bonding_create,
)
from tools.exp004_graph_discovery import (
    DEFAULT_SEED,
    PRIMARY_HORIZON,
    ScoredRow,
    build_scored_book,
    precompute_graph_book,
)
from tools.exp005_smart_wallet_follow import (
    arm_row_in_cohort,
    build_v2_runner_signatures,
    l3_packet_selected,
)
EXP_ID = "EXP-006-paper-would-have-happened-harness-v0"
DEFAULT_OUTPUT_DIR = Path("data/observe")
DEFAULT_PREFIX = "_exp006"

# P0 v0 documented constants — NOT fitted to lift (PAPER-TRADING-SURFACE-BRIEF / EXP-006).
FILL_LATENCY_MS = 500
PAPER_SIZE_SOL = 0.1
FEE_MODEL_ID = "pump_assumed_bps_v0"
TOTAL_FEE_BPS = 125.0
MAX_SLIPPAGE_BPS = 500.0
SIM_REJECT_ON_SLIP = True

STAMP_HORIZONS = ("1s", "5s", "15s", "30s", "60s")

CohortId = Literal["v2_runner", "L3_minus_v2", "L3_full"]
COHORT_ORDER: tuple[CohortId, ...] = ("L3_minus_v2", "v2_runner", "L3_full")

FillStatus = Literal["filled", "unfilled", "sim_reject", "route_unknown"]
GateResult = Literal["PASS", "FAIL", "INCOMPLETE", "N/A"]


def _last_tick_at_or_before(marks: Sequence[PriceMark], t_cutoff: datetime) -> PriceMark | None:
    chosen: PriceMark | None = None
    for m in marks:
        if m.t <= t_cutoff:
            if chosen is None or m.t >= chosen.t:
                chosen = m
    return chosen


def _reserves_from_row(row: Mapping[str, Any]) -> tuple[float | None, float | None]:
    v_sol = _sealed_float(row, "vSolInBondingCurve")
    v_tok = _sealed_float(row, "vTokensInBondingCurve")
    return v_sol, v_tok


def _curve_slippage_bps(v_sol: float, size_sol: float) -> float:
    """Honest v0 buy impact proxy on bonding reserves (not calibrated to lift)."""
    if v_sol <= 0 or size_sol <= 0:
        return 0.0
    impact_frac = size_sol / (v_sol + size_sol * 0.5)
    return min(impact_frac * 10_000.0, MAX_SLIPPAGE_BPS)


def _fee_sol(size_sol: float) -> float:
    return size_sol * (TOTAL_FEE_BPS / 10_000.0)


def _fee_penalty_return_pct(size_sol: float, fee_sol: float) -> float:
    if size_sol <= 0:
        return 0.0
    return (fee_sol / size_sol) * 100.0


@dataclass(frozen=True)
class PaperFillResult:
    status: FillStatus
    t_decision: datetime
    t_fill: datetime | None
    fill_price_proxy: float | None
    fee_sol: float
    slippage_bps: float
    notes: str = ""

    def as_paper_fill_record(
        self,
        *,
        parent_signature: str,
        mint: str,
        runner_label: str,
        source_path: str,
        line_no: int,
    ) -> dict[str, Any]:
        t_fill_epoch = self.t_fill.timestamp() if self.t_fill else None
        t_dec_epoch = self.t_decision.timestamp()
        return {
            "type": "paper_fill",
            "schema": "paper_fill_v0",
            "parent_signature": parent_signature,
            "mint": mint,
            "runner_label": runner_label,
            "t_decision": t_dec_epoch,
            "t_fill": t_fill_epoch,
            "latency_ms": FILL_LATENCY_MS,
            "size_sol": PAPER_SIZE_SOL,
            "fill_price_proxy": self.fill_price_proxy,
            "fee_model_id": FEE_MODEL_ID,
            "fee_sol": self.fee_sol,
            "slippage_bps": self.slippage_bps,
            "status": self.status,
            "source": "sim_curve_v0",
            "knowable_at_t": True,
            "exp_id": EXP_ID,
            "source_path": source_path,
            "line_no": line_no,
            "notes": self.notes,
        }


def simulate_paper_fill(
    *,
    row: Mapping[str, Any],
    marks: Sequence[PriceMark],
    t_decision: datetime,
) -> PaperFillResult:
    stage = row.get("stage")
    if stage not in (None, "bonding"):
        return PaperFillResult(
            status="route_unknown",
            t_decision=t_decision,
            t_fill=None,
            fill_price_proxy=None,
            fee_sol=0.0,
            slippage_bps=0.0,
            notes=f"stage={stage}",
        )

    t_fill = t_decision + timedelta(milliseconds=FILL_LATENCY_MS)
    tick = _last_tick_at_or_before(marks, t_fill)
    if tick is None:
        return PaperFillResult(
            status="unfilled",
            t_decision=t_decision,
            t_fill=t_fill,
            fill_price_proxy=None,
            fee_sol=0.0,
            slippage_bps=0.0,
            notes="no_mark_at_or_before_t_fill",
        )

    base_price = tick.price
    v_sol, _v_tok = _reserves_from_row(row)
    slip_bps = _curve_slippage_bps(v_sol or 0.0, PAPER_SIZE_SOL)
    if SIM_REJECT_ON_SLIP and slip_bps >= MAX_SLIPPAGE_BPS:
        return PaperFillResult(
            status="sim_reject",
            t_decision=t_decision,
            t_fill=t_fill,
            fill_price_proxy=None,
            fee_sol=0.0,
            slippage_bps=slip_bps,
            notes="slippage_at_cap",
        )

    fill_price = base_price * (1.0 + slip_bps / 10_000.0)
    fee = _fee_sol(PAPER_SIZE_SOL)
    return PaperFillResult(
        status="filled",
        t_decision=t_decision,
        t_fill=t_fill,
        fill_price_proxy=fill_price,
        fee_sol=fee,
        slippage_bps=slip_bps,
        notes="",
    )


def compute_paper_horizons(
    *,
    t_decision: datetime,
    fill_price: float,
    fee_penalty_pct: float,
    marks: Sequence[PriceMark],
) -> dict[str, Any]:
    raw = compute_outcomes(t0=t_decision, p0=fill_price, marks=marks)
    horizons: dict[str, dict[str, Any]] = {}
    for name in STAMP_HORIZONS:
        h = raw["horizons"].get(name, {})
        gross = h.get("return_pct") if h.get("status") == "ok" else None
        delta = None
        status = h.get("status", "na")
        if gross is not None:
            delta = gross - fee_penalty_pct
            status = "ok"
        horizons[name] = {
            "gross_return_pct": gross,
            "delta_exec_return_pct": delta,
            "status": status,
            "mark_t": h.get("mark_t"),
            "notes": h.get("notes", ""),
        }
    return {
        "horizons": horizons,
        "fee_penalty_return_pct": fee_penalty_pct,
        "fill_price_proxy": fill_price,
    }


def _cohort_member(
    row: ScoredRow,
    cohort_id: CohortId,
    v2_runner_sigs: set[str],
) -> bool:
    if cohort_id == "v2_runner":
        sig = row.signature
        return isinstance(sig, str) and sig in v2_runner_sigs
    if cohort_id == "L3_full":
        return l3_packet_selected(row.features)
    return arm_row_in_cohort(row, "L3_minus_v2", v2_runner_sigs)


def _marks_only_return(row: Mapping[str, Any], horizon: str) -> float | None:
    horizons = (row.get("marks_only") or {}).get("horizons") or {}
    h = horizons.get(horizon)
    if isinstance(h, dict) and h.get("status") == "ok":
        val = h.get("return_pct")
        return float(val) if isinstance(val, (int, float)) else None
    return None


def _mean_marks_only(rows: Sequence[Mapping[str, Any]], horizon: str) -> tuple[float | None, int]:
    vals = [v for v in (_marks_only_return(r, horizon) for r in rows) if v is not None]
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


def _mean_field(rows: Sequence[Mapping[str, Any]], horizon: str, field: str) -> tuple[float | None, int]:
    vals: list[float] = []
    for row in rows:
        paper = row.get("paper") or {}
        h = (paper.get("horizons") or {}).get(horizon) or {}
        val = h.get(field)
        if isinstance(val, (int, float)):
            vals.append(float(val))
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


def _gate_lift_rescue(
    marks_mean: float | None,
    paper_mean: float | None,
    priced_n: int,
) -> GateResult:
    if priced_n < MIN_PRICED_FOR_KILL:
        return "INCOMPLETE"
    if marks_mean is None or paper_mean is None:
        return "INCOMPLETE"
    if paper_mean > marks_mean + LIFT_EPSILON + 0.25:
        return "FAIL"
    return "PASS"


def _gate_thin_cohort(priced_n: int) -> GateResult:
    return "INCOMPLETE" if priced_n < MIN_PRICED_FOR_KILL else "PASS"


def _gate_fill_leak_audit(rows: Sequence[Mapping[str, Any]]) -> GateResult:
    for row in rows:
        fill = row.get("fill") or {}
        if fill.get("status") == "filled":
            t_fill_s = fill.get("t_fill")
            mark_t = fill.get("fill_mark_t")
            if isinstance(t_fill_s, str) and isinstance(mark_t, str):
                t_fill = _parse_iso_ts(t_fill_s)
                t_mark = _parse_iso_ts(mark_t)
                if t_fill and t_mark and t_mark > t_fill:
                    return "FAIL"
    return "PASS"


def _stratified_s1_collapse(
    scored_rows: Sequence[Mapping[str, Any]],
    *,
    horizon: str,
) -> dict[str, Any]:
    by_gate: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in scored_rows:
        gate = row.get("regime_gate_key") or "UNK"
        by_gate[str(gate)].append(row)

    def _both_pass(rows: list[Mapping[str, Any]]) -> bool:
        marks_mean, marks_n = _mean_marks_only(rows, horizon)
        paper_mean, paper_n = _mean_field(rows, horizon, "delta_exec_return_pct")
        thin = _gate_thin_cohort(marks_n) == "PASS" and _gate_thin_cohort(paper_n) == "PASS"
        if not thin:
            return False
        return marks_mean is not None and paper_mean is not None

    passing = [gate for gate, rows in by_gate.items() if _both_pass(rows)]
    collapsed = len(by_gate) > 0 and len(passing) == 0
    return {
        "s1_collapse": collapsed,
        "regime_gate_keys_n": len(by_gate),
        "per_gate_with_priced_data": passing,
        "note": (
            "K-S1-collapse: no regime_gate_key retains priced marks+paper @60s."
            if collapsed
            else "At least one regime_gate_key has priced marks+paper data."
        ),
    }


def build_cohort_rows(
    book: Sequence[ScoredRow],
    loaded: Sequence[LoadedRow],
    mint_series: Mapping[str, Sequence[PriceMark]],
    cohort_id: CohortId,
    v2_runner_sigs: set[str],
) -> list[dict[str, Any]]:
    sig_to_row: dict[str, Mapping[str, Any]] = {}
    for item in loaded:
        row = item.row
        if not is_bonding_create(row):
            continue
        sig = row.get("signature")
        if isinstance(sig, str):
            sig_to_row[sig] = row

    out: list[dict[str, Any]] = []
    for scored in book:
        if not _cohort_member(scored, cohort_id, v2_runner_sigs):
            continue
        sig = scored.signature
        if not isinstance(sig, str):
            continue
        create_row = sig_to_row.get(sig)
        if create_row is None:
            continue
        t0 = _parse_iso_ts(scored.t_ws)
        if t0 is None:
            continue
        marks = list(mint_series.get(scored.mint, []))
        fill = simulate_paper_fill(row=create_row, marks=marks, t_decision=t0)
        fill_mark_t: str | None = None
        if fill.t_fill is not None:
            tick = _last_tick_at_or_before(marks, fill.t_fill)
            if tick is not None:
                fill_mark_t = tick.t.isoformat()

        paper_payload: dict[str, Any] | None = None
        if fill.status == "filled" and fill.fill_price_proxy is not None:
            fee_pen = _fee_penalty_return_pct(PAPER_SIZE_SOL, fill.fee_sol)
            paper_payload = compute_paper_horizons(
                t_decision=t0,
                fill_price=fill.fill_price_proxy,
                fee_penalty_pct=fee_pen,
                marks=marks,
            )

        marks_only = scored.outcomes
        out.append(
            {
                "signature": sig,
                "mint": scored.mint,
                "regime_gate_key": scored.features.regime_gate_key,
                "marks_only": marks_only,
                "fill": {
                    "status": fill.status,
                    "t_fill": fill.t_fill.isoformat() if fill.t_fill else None,
                    "fill_mark_t": fill_mark_t,
                    "slippage_bps": fill.slippage_bps,
                    "fee_sol": fill.fee_sol,
                    "notes": fill.notes,
                },
                "paper": paper_payload,
                "paper_fill_record": fill.as_paper_fill_record(
                    parent_signature=sig,
                    mint=scored.mint,
                    runner_label="watch_list",
                    source_path=scored.source_path,
                    line_no=scored.line_no,
                ),
            }
        )
    return out


def score_cohort(
    cohort_rows: Sequence[Mapping[str, Any]],
    *,
    cohort_id: CohortId,
    horizon: str,
) -> dict[str, Any]:
    dict_rows = [dict(r) for r in cohort_rows]
    marks_mean, marks_priced_n = _mean_marks_only(dict_rows, horizon)
    paper_gross_mean, paper_gross_n = _mean_field(dict_rows, horizon, "gross_return_pct")
    paper_delta_mean, paper_delta_n = _mean_field(dict_rows, horizon, "delta_exec_return_pct")

    filled_n = sum(1 for r in cohort_rows if (r.get("fill") or {}).get("status") == "filled")
    unfilled_n = sum(1 for r in cohort_rows if (r.get("fill") or {}).get("status") == "unfilled")
    reject_n = sum(1 for r in cohort_rows if (r.get("fill") or {}).get("status") == "sim_reject")
    cohort_n = len(cohort_rows)
    fill_rate = (filled_n / cohort_n) if cohort_n else None

    thin_gate = _gate_thin_cohort(marks_priced_n)
    lift_rescue = _gate_lift_rescue(marks_mean, paper_delta_mean, paper_delta_n)

    if cohort_n == 0:
        overall = "INCOMPLETE"
    elif thin_gate == "INCOMPLETE":
        overall = "INCOMPLETE"
    elif lift_rescue == "FAIL":
        overall = "FAIL_K_LIFT_RESCUE"
    else:
        overall = "DIRECTIONAL_NON_KILL"

    return {
        "cohort_id": cohort_id,
        "horizon": horizon,
        "cohort_n": cohort_n,
        "filled_n": filled_n,
        "unfilled_n": unfilled_n,
        "sim_reject_n": reject_n,
        "fill_rate": fill_rate,
        "marks_only": {"mean_return_pct": marks_mean, "priced_n": marks_priced_n},
        "paper_gross": {"mean_return_pct": paper_gross_mean, "priced_n": paper_gross_n},
        "paper_delta_exec": {"mean_return_pct": paper_delta_mean, "priced_n": paper_delta_n},
        "gates": {
            "K-thin-cohort": {"result": thin_gate, "min_priced": MIN_PRICED_FOR_KILL},
            "K-lift-rescue": {"result": lift_rescue, "note": "paper Δ_exec must not systematically beat marks-only"},
            "K-fill-leak": {"result": _gate_fill_leak_audit(dict_rows)},
        },
        "overall": overall,
    }


def _day_overall(cohort_scores: Mapping[str, Mapping[str, Any]]) -> str:
    primary = cohort_scores.get("L3_minus_v2", {}).get(PRIMARY_HORIZON, {})
    if not primary:
        return "INCOMPLETE"
    o = primary.get("overall")
    if o == "FAIL_K_LIFT_RESCUE":
        return "FAIL"
    if o == "INCOMPLETE":
        return "INCOMPLETE"
    return "DIRECTIONAL_NON_KILL"


def cross_day_overall(day_overalls: Sequence[str]) -> str:
    if not day_overalls:
        return "INCOMPLETE"
    if any(o == "INCOMPLETE" for o in day_overalls):
        return "INCOMPLETE"
    if any(o == "FAIL" for o in day_overalls):
        return "FAIL"
    if all(o == "DIRECTIONAL_NON_KILL" for o in day_overalls):
        return "DIRECTIONAL_WATCH"
    return "INCOMPLETE"


def run_single_day(
    observe_path: Path,
    marks_paths: Sequence[Path],
    *,
    seed: int,
    output_dir: Path,
    prefix: str,
    emit_paper_fills: bool = True,
) -> dict[str, Any]:
    all_paths = [observe_path, *marks_paths]
    loaded, malformed = load_jsonl_files(all_paths)
    graph_pairs, pre_meta = precompute_graph_book(loaded)
    mint_series = build_mint_price_series(loaded)
    book = build_scored_book(graph_pairs, mint_series)
    v2_runner_sigs = build_v2_runner_signatures(loaded, book) if book else set()

    cohort_data: dict[str, list[dict[str, Any]]] = {}
    for cid in COHORT_ORDER:
        cohort_data[cid] = build_cohort_rows(
            book, loaded, mint_series, cid, v2_runner_sigs
        )

    cohort_scores: dict[str, dict[str, Any]] = {}
    for cid in COHORT_ORDER:
        cohort_scores[cid] = {
            h: score_cohort(cohort_data[cid], cohort_id=cid, horizon=h) for h in STAMP_HORIZONS
        }

    primary_rows = cohort_data["L3_minus_v2"]
    s1 = _stratified_s1_collapse(primary_rows, horizon=PRIMARY_HORIZON)

    day_overall = _day_overall({k: v for k, v in cohort_scores.items()})

    summary: dict[str, Any] = {
        "exp": EXP_ID,
        "observe_path": str(observe_path),
        "marks_paths": [str(p) for p in marks_paths],
        "seed": seed,
        "fill_model": {
            "latency_ms": FILL_LATENCY_MS,
            "size_sol": PAPER_SIZE_SOL,
            "fee_model_id": FEE_MODEL_ID,
            "total_fee_bps": TOTAL_FEE_BPS,
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
            "sim_reject_on_slip": SIM_REJECT_ON_SLIP,
        },
        "precompute_meta": pre_meta,
        "malformed_lines": malformed,
        "book_n": len(book),
        "v2_runner_n": len(v2_runner_sigs),
        "cohort_arm_n": {cid: len(cohort_data[cid]) for cid in COHORT_ORDER},
        "cohort_scores": cohort_scores,
        "s1_collapse_falsifier": s1,
        "overall": day_overall,
        "limitations": [
            "Fees assumed bps v0 — not on-chain feeConfig snapshot in this run.",
            "Curve slip is reserve-ratio proxy only; no Raydium post-migration depth.",
            "Horizon marks anchored at t_decision (t_ws); fill entry at t_fill only.",
            "Watch-list cohorts only — not Discovery promote.",
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / f"{prefix}_summary.json"
    report_path = output_dir / f"{prefix}_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    if emit_paper_fills:
        paper_path = output_dir / f"{prefix}_paper_fills.jsonl"
        with paper_path.open("w", encoding="utf-8") as fh:
            for cid in COHORT_ORDER:
                for row in cohort_data[cid]:
                    rec = row.get("paper_fill_record")
                    if isinstance(rec, dict):
                        fh.write(json.dumps(rec, sort_keys=True) + "\n")

    summary["_artifacts"] = {
        "summary_json": str(summary_path),
        "report_md": str(report_path),
        "paper_fills_jsonl": str(output_dir / f"{prefix}_paper_fills.jsonl"),
    }
    return summary


def _render_report(summary: Mapping[str, Any]) -> str:
    lines = [
        f"# {summary.get('exp')} — sealed paper fill measure",
        "",
        f"**Overall:** {summary.get('overall')}",
        "",
        "## Fill model (documented constants)",
        "",
        f"```json\n{json.dumps(summary.get('fill_model'), indent=2)}\n```",
        "",
        "## Cohort @ horizons (marks-only vs paper Δ_exec)",
        "",
    ]
    scores = summary.get("cohort_scores") or {}
    for cid in COHORT_ORDER:
        lines.append(f"### {cid}")
        lines.append("")
        lines.append("| horizon | marks priced_n | marks mean % | paper Δ_exec priced_n | paper Δ_exec mean % | fill_rate | K-lift-rescue |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | --- |")
        for h in STAMP_HORIZONS:
            block = (scores.get(cid) or {}).get(h) or {}
            mo = block.get("marks_only") or {}
            pd = block.get("paper_delta_exec") or {}
            gates = block.get("gates") or {}
            lr = (gates.get("K-lift-rescue") or {}).get("result", "N/A")
            fr = block.get("fill_rate")
            fr_s = f"{fr:.3f}" if isinstance(fr, float) else "N/A"
            lines.append(
                f"| {h} | {mo.get('priced_n', 0)} | {mo.get('mean_return_pct')} | "
                f"{pd.get('priced_n', 0)} | {pd.get('mean_return_pct')} | {fr_s} | {lr} |"
            )
        lines.append("")
    s1 = summary.get("s1_collapse_falsifier") or {}
    lines.append(f"**S1 collapse (L3_minus_v2 primary):** {s1.get('s1_collapse')} — {s1.get('note')}")
    lines.append("")
    for lim in summary.get("limitations") or []:
        lines.append(f"- {lim}")
    return "\n".join(lines) + "\n"


def run_exp006_multi_day(
    day_specs: Sequence[tuple[Path, Sequence[Path]]],
    *,
    seed: int = DEFAULT_SEED,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = DEFAULT_PREFIX,
) -> dict[str, Any]:
    per_day: dict[str, Any] = {}
    day_overalls: list[str] = []
    for observe_path, marks_paths in day_specs:
        day_key = observe_path.stem.replace("observe-", "")
        day_prefix = f"{prefix}-{day_key}"
        summary = run_single_day(
            observe_path,
            marks_paths,
            seed=seed,
            output_dir=output_dir,
            prefix=day_prefix,
        )
        per_day[day_key] = summary
        day_overalls.append(summary["overall"])

    cross = cross_day_overall(day_overalls)
    return {
        "exp": EXP_ID,
        "cross_day_overall": cross,
        "per_day": per_day,
        "day_overalls": day_overalls,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{EXP_ID} P0 paper fill-sim harness")
    parser.add_argument("observe_paths", nargs="+", type=Path, help="Day-aligned sealed observe JSONL.")
    parser.add_argument("--marks", nargs="+", type=Path, required=True, help="Matching marks JSONL (same day).")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    args = parser.parse_args(argv)

    if len(args.observe_paths) != len(args.marks):
        print("error: one --marks file per observe path (same-day pairs)", file=sys.stderr)
        return 2

    if len(args.observe_paths) == 1:
        summary = run_single_day(
            args.observe_paths[0],
            [args.marks[0]],
            seed=args.seed,
            output_dir=args.output_dir,
            prefix=args.prefix,
        )
        print(
            json.dumps(
                {
                    "exp": summary["exp"],
                    "overall": summary["overall"],
                    "primary_cohort": "L3_minus_v2",
                    "primary_60s": (summary.get("cohort_scores") or {})
                    .get("L3_minus_v2", {})
                    .get(PRIMARY_HORIZON),
                    "artifacts": summary.get("_artifacts"),
                },
                indent=2,
            )
        )
        exit_code = 0 if summary["overall"] != "FAIL" else 1
        return 1 if summary["overall"] == "INCOMPLETE" else exit_code

    day_specs = [(obs, [m]) for obs, m in zip(args.observe_paths, args.marks)]
    multi = run_exp006_multi_day(
        day_specs,
        seed=args.seed,
        output_dir=args.output_dir,
        prefix=args.prefix,
    )
    print(json.dumps({"cross_day_overall": multi["cross_day_overall"], "day_overalls": multi["day_overalls"]}, indent=2))
    if multi["cross_day_overall"] == "INCOMPLETE":
        return 1
    if multi["cross_day_overall"] == "FAIL":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
