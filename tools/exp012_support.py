"""EXP-012 support: the proceed screen and result.v1 records.

- `proceed_screen`: EXP-012 section 3.3's pre-stated proceed condition, computed
  mechanically from the freeze's nested fixed-threshold LODO report by reusing
  `tools.exploration_entry_model_b3.screen_candidate` (the B3 screen, unchanged).
- `write_freeze_result` / `write_scorer_result`: one `result.v1` record each
  (docs/contracts/result-json.md, tools/mal_result.py). Roles: the freeze's
  nested LODO is "exploration"; the one-shot read is "confirmation-oneshot".
  The record is written in addition to, and never instead of, the tool's own
  report; it does not change any verdict.
"""

from __future__ import annotations

import resource
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import mal_result
from tools.exploration_entry_model_b3 import screen_candidate
from tools.exploration_exits import ENTRY_SIZE, POOL_HOURS as POOL_A_HOURS
from tools.oracle_insample_adapter import POOL_C_HOURS
from tools.oracle_live_adapter import POOL_B_HOURS
from tools.paper_curve_math import LAMPORTS_PER_SOL

HOST = "mal-research-0"


def proceed_screen(nested_report: Mapping[str, Any]) -> dict[str, Any]:
    """EXP-012 section 3.3: under BOTH fail models, pooled mean net % > 0,
    pooled ex-top-3 SOL > 0, and more than half of the outer days positive.
    `proceed` is the single boolean the pre-registration conditions on."""
    pooled = {
        "n_days_total": nested_report["n_days_total"],
        "flat_net_mean_pct": nested_report["flat"]["mean_pct"],
        "flat_ex_top3_sol": nested_report["flat"]["ex_top3_sol"],
        "n_days_flat_positive": nested_report["n_days_flat_positive"],
        "press_net_mean_pct": nested_report["press"]["mean_pct"],
        "press_ex_top3_sol": nested_report["press"]["ex_top3_sol"],
        "n_days_press_positive": nested_report["n_days_press_positive"],
    }
    s = screen_candidate(pooled)
    return {
        "schema": "exp012_proceed_screen_v1",
        "rule": "EXP-012 section 3.3 (B3 screen): both fail models, pooled mean net % > 0, ex-top-3 SOL > 0, days positive > n_days/2",
        "inputs": pooled,
        "flat_ok": s["flat_ok"],
        "press_ok": s["press_ok"],
        "proceed": s["candidate"],
        "majority_needed": s["majority_needed"],
    }


def _hour_plus_one(h: str) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H")


def exploration_pool_blocks() -> list[dict[str, str]]:
    return [
        {"start_hour": hours[0], "end_hour_exclusive": _hour_plus_one(hours[-1]), "host": HOST, "ledger_owner": "exploration pool"}
        for hours in (POOL_A_HOURS, POOL_C_HOURS, POOL_B_HOURS)
    ]


def holdout_block() -> list[dict[str, str]]:
    return [{"start_hour": "2026-09-03T12", "end_hour_exclusive": "2026-09-09T12", "host": HOST, "ledger_owner": "EXP-012"}]


def trades_from_rows(rows: Sequence[Mapping[str, Any]], pnl_key: str, segment_key: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        sol = float(r[pnl_key]) / LAMPORTS_PER_SOL
        out.append({"sol": sol, "pct": float(r[pnl_key]) / ENTRY_SIZE * 100.0, "day": r["day"], "filled": bool(r.get("filled", True)), "segment": r.get(segment_key)})
    return out


def peak_rss_mb() -> float:
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return max(own, kids) / 1024.0  # Linux ru_maxrss is KiB


def _emit(
    path: Path,
    *,
    tool: str,
    command: str,
    config: Mapping[str, Any],
    role: str,
    stage: str,
    blocks: list[dict[str, str]],
    entered: Sequence[Mapping[str, Any]],
    segment_key: str,
    n_candidates: int | None,
    n_days: int,
    runtime_s: float | None,
    notes: str,
    tries_log: str | Path | None,
    git_sha: str,
) -> dict[str, Any]:
    ap = mal_result.append_try(tries_log, tool=tool, config=config, data_blocks=blocks, result_path=path, role=role)
    tries = {**ap, **mal_result.tries_summary(tries_log, ap["data_key"])}
    result = mal_result.build_result(
        tool=tool,
        git_sha=git_sha,
        command=command,
        config=config,
        role=role,
        data_blocks=blocks,
        stage=stage,
        trades_flat=trades_from_rows(entered, "flat", segment_key),
        trades_pressure_s1=trades_from_rows(entered, "press", segment_key),
        tries=tries,
        n_candidates=n_candidates,
        n_days=n_days,
        runtime_s=runtime_s,
        peak_rss_mb=peak_rss_mb(),
        notes=notes,
    )
    mal_result.write_result(path, result)
    return result


def write_freeze_result(
    path: Path, entries: Sequence[Mapping[str, Any]], nested_report: Mapping[str, Any], *, command: str, runtime_s: float | None, tries_log: str | Path | None = None, git_sha: str = "unknown"
) -> dict[str, Any]:
    screen = proceed_screen(nested_report)
    n_cand = sum(f["n_test"] for f in nested_report["fold_info"])
    return _emit(
        path,
        tool="tools.exp011_freeze (EXP-012 re-freeze, nested fixed-threshold LODO)",
        command=command,
        config={"experiment": "EXP-012", "recipe": "EXP-011 section 3 unchanged", "proceed": screen["proceed"], "proceed_screen": screen["inputs"]},
        role="exploration",
        stage="exploring",
        blocks=exploration_pool_blocks(),
        entered=entries,
        segment_key="pool",
        n_candidates=n_cand,
        n_days=int(nested_report["n_days_total"]),
        runtime_s=runtime_s,
        notes="Report-only nested fixed-threshold LODO on the deduplicated exploration pool (in-pool, not out-of-sample, not gate evidence). Gate legs here are in-pool and informational; "
        f"EXP-012 proceed condition = {screen['proceed']}.",
        tries_log=tries_log,
        git_sha=git_sha,
    )


def write_scorer_result(
    path: Path, rows: Sequence[Mapping[str, Any]], entered: Sequence[Mapping[str, Any]], report: Mapping[str, Any], *, command: str, runtime_s: float | None, tries_log: str | Path | None = None
) -> dict[str, Any]:
    return _emit(
        path,
        tool="tools.exp012_score",
        command=command,
        config={"experiment": "EXP-012", "threshold": report["threshold"], "primary_cell": "s2_ablated_top", "verdict": report["verdict"]},
        role="confirmation-oneshot",
        stage="passed" if report["gate"].get("promote") else "failed",
        blocks=holdout_block(),
        entered=entered,
        segment_key="day",
        n_candidates=len(rows),
        n_days=6,
        runtime_s=runtime_s,
        notes="EXP-012 one-shot read of the deduplicated fresh block. The verdict is the report's (tools.exp011_score.compute_gate); this record is a copy for the Console.",
        tries_log=tries_log,
        git_sha=report.get("code_commit", "unknown"),
    )
