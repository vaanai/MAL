#!/usr/bin/env python3
"""EXP-003 coverage CLI — join sealed creates to outcome_mark ticks (offline).

No RPC, no WebSocket, no secrets. Producer of marks is local (see EXP-003).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

from tools.exp001_mislabel import LoadedRow, load_jsonl_files, t_ws_missing
from tools.exp002_paper_runner import HORIZON_SECONDS, PRIMARY_HORIZON, is_bonding_create
from tools.marks import (
    MARK_SCHEMA_VERSION,
    MARK_TYPE,
    is_outcome_mark,
    mark_void_reason,
    parse_iso_ts,
    parse_mark_tick,
    select_last_as_of,
)

DEFAULT_OUTPUT_DIR = Path("data/observe")
DEFAULT_PREFIX = "_exp003"
MIN_OK_FOR_READY = 20


def collect_creates(loaded: Sequence[LoadedRow]) -> tuple[list[LoadedRow], int]:
    eligible: list[LoadedRow] = []
    void_n = 0
    for item in loaded:
        if not is_bonding_create(item.row):
            continue
        if t_ws_missing(item.row):
            void_n += 1
            continue
        eligible.append(item)
    return eligible, void_n


def collect_ticks(
    loaded: Sequence[LoadedRow],
) -> tuple[dict[str, list[tuple[datetime, float]]], Counter[str], int]:
    """Mint → [(t_mark, price), ...]. Counts void reasons; skips non-mark rows."""
    by_mint: dict[str, list[tuple[datetime, float]]] = {}
    void_reasons: Counter[str] = Counter()
    mark_n = 0
    for item in loaded:
        row = item.row
        if row.get("type") == MARK_TYPE or is_outcome_mark(row):
            mark_n += 1
            reason = mark_void_reason(row)
            if reason is not None:
                void_reasons[reason] += 1
                continue
            parsed = parse_mark_tick(row)
            if parsed is None:
                void_reasons["parse_failed"] += 1
                continue
            mint, t_mark, price = parsed
            by_mint.setdefault(mint, []).append((t_mark, price))
    return by_mint, void_reasons, mark_n


def coverage_for_create(
    row: Mapping[str, Any],
    ticks: Sequence[tuple[datetime, float]],
) -> dict[str, Any]:
    t0 = parse_iso_ts(row.get("t_ws"))
    horizons: dict[str, dict[str, Any]] = {}
    if t0 is None:
        for name in HORIZON_SECONDS:
            horizons[name] = {"status": "na", "notes": "no_t_decision"}
        return {"mint": row.get("mint"), "signature": row.get("signature"), "horizons": horizons}

    for name, offset_s in HORIZON_SECONDS.items():
        chosen = select_last_as_of(ticks, t0, offset_s)
        if chosen is None:
            horizons[name] = {"status": "na", "notes": "no_tick_in_(T, T+H]"}
        else:
            t_mark, price = chosen
            horizons[name] = {
                "status": "ok",
                "t_mark": t_mark.isoformat(),
                "price_proxy": price,
            }
    return {
        "mint": row.get("mint"),
        "signature": row.get("signature"),
        "t_ws": row.get("t_ws"),
        "horizons": horizons,
    }


def build_coverage(
    *,
    observe_paths: Sequence[Path],
    marks_paths: Sequence[Path],
) -> dict[str, Any]:
    observe_loaded, observe_malformed = load_jsonl_files(observe_paths)
    marks_loaded: list[LoadedRow] = []
    marks_malformed = 0
    if marks_paths:
        marks_loaded, marks_malformed = load_jsonl_files(marks_paths)

    creates, void_n = collect_creates(observe_loaded)
    # Marks may also be mixed into observe files (discouraged but counted).
    ticks, void_reasons, mark_n_obs = collect_ticks(observe_loaded)
    ticks_m, void_reasons_m, mark_n_side = collect_ticks(marks_loaded)
    for mint, series in ticks_m.items():
        ticks.setdefault(mint, []).extend(series)
    void_reasons.update(void_reasons_m)
    mark_n = mark_n_obs + mark_n_side

    ok_counts: dict[str, int] = {name: 0 for name in HORIZON_SECONDS}
    na_counts: dict[str, int] = {name: 0 for name in HORIZON_SECONDS}
    for item in creates:
        mint = item.row.get("mint")
        series = ticks.get(mint, []) if isinstance(mint, str) else []
        cov = coverage_for_create(item.row, series)
        for name, cell in cov["horizons"].items():
            if cell.get("status") == "ok":
                ok_counts[name] += 1
            else:
                na_counts[name] += 1

    primary = PRIMARY_HORIZON
    primary_ok = ok_counts.get(primary, 0)
    if not marks_paths and mark_n == 0:
        overall = "INCOMPLETE"
        overall_notes = "no_marks_file"
    elif primary_ok >= MIN_OK_FOR_READY:
        overall = "READY"
        overall_notes = f"{primary}_ok_n>={MIN_OK_FOR_READY}"
    else:
        overall = "INCOMPLETE"
        overall_notes = f"{primary}_ok_n={primary_ok}<{MIN_OK_FOR_READY}"

    return {
        "exp": "EXP-003",
        "schema_version": MARK_SCHEMA_VERSION,
        "mark_type": MARK_TYPE,
        "join": "last_tick_with_T_lt_t_mark_lte_T_plus_H",
        "observe_paths": [str(p) for p in observe_paths],
        "marks_paths": [str(p) for p in marks_paths],
        "observe_malformed_n": observe_malformed,
        "marks_malformed_n": marks_malformed,
        "creates_n": len(creates),
        "creates_void_t_ws_n": void_n,
        "marks_n": mark_n,
        "marks_void_reasons": dict(void_reasons),
        "horizon_ok_n": ok_counts,
        "horizon_na_n": na_counts,
        "primary_horizon": primary,
        "min_ok_for_ready": MIN_OK_FOR_READY,
        "overall": overall,
        "overall_notes": overall_notes,
        "limitations": [
            "Coverage only; does not call RPC or PumpPortal.",
            "Δ_exec not computed here.",
            "Sealed ingest_hot rows are not modified.",
        ],
    }


def write_coverage(path: Path, summary: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _print_summary(summary: Mapping[str, Any], out: TextIO = sys.stdout) -> None:
    ok = summary["horizon_ok_n"]
    na = summary["horizon_na_n"]
    bits = " ".join(f"{h} ok={ok[h]} na={na[h]}" for h in HORIZON_SECONDS)
    print(
        "EXP-003 post-create marks coverage\n"
        f"  creates_n={summary['creates_n']} marks_n={summary['marks_n']}\n"
        f"  join={summary['join']}\n"
        f"  {bits}\n"
        f"  overall={summary['overall']} ({summary['overall_notes']})",
        file=out,
    )


def _exit_code(overall: str) -> int:
    if overall == "READY":
        return 0
    if overall == "INCOMPLETE":
        return 3
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.exp003_marks",
        description=(
            "EXP-003 coverage: sealed bonding creates × outcome_mark ticks "
            "(last-at-or-before; no network)."
        ),
    )
    p.add_argument(
        "jsonl",
        nargs="+",
        type=Path,
        help="Sealed observe JSONL path(s)",
    )
    p.add_argument(
        "--marks",
        nargs="*",
        default=[],
        type=Path,
        help="Side outcome_mark JSONL path(s) (gitignored)",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory (default: data/observe)",
    )
    p.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help="Output filename prefix (default: _exp003)",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    missing = [p for p in args.jsonl if not p.is_file()]
    if missing:
        print(f"error: JSONL not found: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 1
    marks_missing = [p for p in args.marks if not p.is_file()]
    if marks_missing:
        print(
            f"error: marks JSONL not found: {', '.join(str(p) for p in marks_missing)}",
            file=sys.stderr,
        )
        return 1
    summary = build_coverage(observe_paths=args.jsonl, marks_paths=args.marks)
    out_path = args.output_dir / f"{args.prefix}_coverage.json"
    write_coverage(out_path, summary)
    summary["output_paths"] = {"coverage": str(out_path)}
    _print_summary(summary)
    print(f"  outputs: {summary['output_paths']}", file=sys.stderr)
    return _exit_code(str(summary["overall"]))


if __name__ == "__main__":
    raise SystemExit(main())
