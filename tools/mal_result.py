"""The standard result.v1 record every exploration/scoring tool emits.

See schemas/result.v1.schema.json and docs/contracts/result-json.md for the
field-by-field meaning, and docs/console-plan.md §5 ("how MAL judges an
edge") and §9 item 1 ("a standard result.json") for why this exists: the
MAL Console renders this dict as one "result card" per run, and every card
carries "variant N of M tried on this data" (§2 rule 1).

This module is a NEW file. It does not modify any existing tool -- it only
imports `tools.paper_attention_promote.book_stats` / `BookTrade`, which is
the same cluster-bootstrap gate function `tools/exp011_score.py`'s
`compute_gate` and `tools/kill_review.py` already use for the project-wide
promotion rule (1,000 draws, seed 1, 5th percentile of means, resampled by
whole token/mint with replacement). We do not re-derive that arithmetic
here.

`leg_metrics`'s input trades are plain dicts (no mint field -- see the
task's own trade shape: sol, pct, day, filled, segment). Lacking a real
mint to cluster on, each trade is given a synthetic, unique one-trade
cluster (`f"t{i}"`), which makes `book_stats`'s cluster bootstrap reduce to
an ordinary trade-level bootstrap with replacement -- same 1,000/seed-1/p05
recipe, just no token-level grouping (there is none to have, at this call
shape).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.paper_attention_promote import LAMPORTS_PER_SOL, MIN_DAYS, MIN_N, BookTrade, book_stats

SCHEMA_VERSION = "result.v1"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "result.v1.schema.json"
DEFAULT_TRIES_LOG = Path("data/tries.jsonl")

TOP_K_CONCENTRATION = 5


# --- metrics ------------------------------------------------------------


def _t_ms_for_day(day: str) -> int:
    """Midday UTC of `day` (YYYY-MM-DD). Only used to hand `book_stats` a
    t_ms it can bucket back into the same UTC day via its own `_utc_day`."""
    dt = datetime.strptime(day, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _book_trades(trades: Sequence[Mapping[str, Any]]) -> list[BookTrade]:
    out = []
    for i, t in enumerate(trades):
        pnl_lamports = int(round(float(t["sol"]) * LAMPORTS_PER_SOL))
        out.append(BookTrade(mint=f"t{i}", t_ms=_t_ms_for_day(t["day"]), pnl=pnl_lamports))
    return out


def _mean(values: Sequence[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _by_segment(trades: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for t in trades:
        seg = t.get("segment")
        if seg is None:
            continue
        groups.setdefault(seg, []).append(t)
    out: dict[str, dict[str, Any]] = {}
    for seg, rows in groups.items():
        sols = [float(r["sol"]) for r in rows]
        out[seg] = {
            "n_trades": len(rows),
            "mean_pct": _mean([float(r["pct"]) for r in rows]),
            "total_sol": sum(sols),
        }
    return out


def _top5_profit_share(trades: Sequence[Mapping[str, Any]]) -> float | None:
    """The §5 item 9 concentration check: the share of *profit* (gross
    winners only) that came from the top 5 trades. None when there is no
    gross profit to share (no winning trades)."""
    positive = sorted((float(t["sol"]) for t in trades if float(t["sol"]) > 0), reverse=True)
    gross = sum(positive)
    if gross <= 0:
        return None
    return sum(positive[:TOP_K_CONCENTRATION]) / gross


def leg_metrics(
    trades: Sequence[Mapping[str, Any]],
    *,
    n_candidates: int | None = None,
    n_days: int | None = None,
) -> dict[str, Any]:
    """One fail-model leg's §5 metrics for a list of trade dicts:
    {sol, pct, day (YYYY-MM-DD UTC), filled (bool, default True), segment
    (optional)}.

    `n_candidates`, if given, is the pre-selection candidate count used for
    `selected_fraction`. `n_days`, if given, overrides the day count used
    as the denominator for `trades_per_day` / `sol_per_day` (e.g. the
    calendar span of the data block, which can exceed the number of
    distinct days that actually produced a trade); otherwise the distinct
    UTC day count from the trades themselves is used.

    The CI lower bound, total SOL and ex-top-3 SOL are computed by
    `tools.paper_attention_promote.book_stats` -- the repo's existing
    promotion-gate bootstrap -- not re-implemented here.
    """
    n = len(trades)
    pcts = [float(t["pct"]) for t in trades]
    sols = [float(t["sol"]) for t in trades]
    filled_trades = [t for t in trades if t.get("filled", True)]

    stats = book_stats(_book_trades(trades))

    distinct_days = stats["n_days"]
    effective_days = n_days if n_days is not None else distinct_days

    return {
        "n_trades": n,
        "selected_fraction": (n / n_candidates) if n_candidates else None,
        "trades_per_day": (n / effective_days) if effective_days else None,
        "sol_per_day": (stats["total_sol"] / effective_days) if effective_days else None,
        "mean_pct": _mean(pcts),
        "median_pct": _median(pcts),
        "mean_sol": _mean(sols),
        "median_sol": _median(sols),
        "ci90_lo_sol": stats["mean_ci90_sol"][0] if stats["mean_ci90_sol"] is not None else None,
        "total_sol": stats["total_sol"],
        "ex_top3_sol": stats["total_ex_top3_sol"],
        "days": distinct_days,
        "days_positive": stats["days_positive"],
        "fill_rate": (len(filled_trades) / n) if n else None,
        "fill_cond_mean_pct": _mean([float(t["pct"]) for t in filled_trades]) if filled_trades else None,
        "top5_profit_share": _top5_profit_share(trades),
        "by_segment": _by_segment(trades),
    }


# --- gate -----------------------------------------------------------------


def gate_leg(metrics: Mapping[str, Any]) -> dict[str, bool]:
    """Exactly LAB_STATE.md's promotion gate, one fail-model leg:
    n >= 100, >= 5 distinct UTC days with a majority positive, 90% CI
    lower bound of mean SOL/trade > 0, total SOL still positive after
    removing the top 3 trades."""
    n = metrics["n_trades"]
    days = metrics["days"]
    days_positive = metrics["days_positive"]
    ci_lo = metrics["ci90_lo_sol"]
    ex_top3 = metrics["ex_top3_sol"]

    n_ge_100 = n >= MIN_N
    days_ge_5 = days >= MIN_DAYS
    majority_days_positive = days > 0 and (days_positive * 2 > days)
    ci90_lo_gt_0 = ci_lo is not None and ci_lo > 0
    ex_top3_gt_0 = ex_top3 is not None and ex_top3 > 0

    return {
        "n_ge_100": n_ge_100,
        "days_ge_5": days_ge_5,
        "majority_days_positive": majority_days_positive,
        "ci90_lo_gt_0": ci90_lo_gt_0,
        "ex_top3_gt_0": ex_top3_gt_0,
        "pass": bool(n_ge_100 and days_ge_5 and majority_days_positive and ci90_lo_gt_0 and ex_top3_gt_0),
    }


# --- data key / tries log --------------------------------------------------


def data_key(data_blocks: Sequence[Mapping[str, Any]]) -> str:
    """sha256 of the canonical (sorted) JSON of `data_blocks`, first 16 hex
    chars. Order-independent: the same set of blocks in any input order
    hashes to the same key."""
    normalized = sorted((json.dumps(b, sort_keys=True, separators=(",", ":")) for b in data_blocks))
    canon = json.dumps(normalized, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _tries_log_path(log_path: str | Path | None) -> Path:
    if log_path is not None:
        return Path(log_path)
    return Path(os.environ.get("MAL_TRIES_LOG", str(DEFAULT_TRIES_LOG)))


def append_try(
    log_path: str | Path | None = None,
    *,
    tool: str,
    config: Mapping[str, Any],
    data_blocks: Sequence[Mapping[str, Any]],
    result_path: str | Path,
    role: str,
) -> dict[str, Any]:
    """Append one line to the tries jsonl (default data/tries.jsonl,
    overridable via MAL_TRIES_LOG or the `log_path` arg), under an fcntl
    exclusive lock so concurrent appends from separate processes never
    interleave or drop a line. Returns {data_key, variant_n} where
    variant_n = 1 + the number of prior lines already logged for this same
    data_key (this run's own 1-indexed position)."""
    import fcntl

    path = _tries_log_path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    key = data_key(data_blocks)

    with open(path, "r+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.seek(0)
            prior = 0
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("data_key") == key:
                    prior += 1
            variant_n = prior + 1
            entry = {
                "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tool": tool,
                "config": config,
                "data_blocks": list(data_blocks),
                "result_path": str(result_path),
                "role": role,
                "data_key": key,
                "variant_n": variant_n,
            }
            fh.seek(0, os.SEEK_END)
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    return {"data_key": key, "variant_n": variant_n}


def tries_summary(log_path: str | Path | None, key: str) -> dict[str, Any]:
    """{data_key, of_m}: of_m = total number of lines in the tries log with
    this data_key (including any appended by `append_try` already)."""
    path = _tries_log_path(log_path)
    of_m = 0
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("data_key") == key:
                    of_m += 1
    return {"data_key": key, "of_m": of_m}


# --- build / write ----------------------------------------------------------


def build_result(
    *,
    tool: str,
    git_sha: str,
    command: str,
    config: Mapping[str, Any],
    role: str,
    data_blocks: Sequence[Mapping[str, Any]],
    stage: str,
    trades_flat: Sequence[Mapping[str, Any]],
    trades_pressure_s1: Sequence[Mapping[str, Any]],
    tries: Mapping[str, Any],
    n_candidates: int | None = None,
    n_days: int | None = None,
    runtime_s: float | None = None,
    peak_rss_mb: float | None = None,
    notes: str | None = None,
    created_utc: str | None = None,
) -> dict[str, Any]:
    """Build one result.v1 dict. `tries` is `{data_key, variant_n, of_m}`
    (typically the merge of `append_try`'s return and `tries_summary`'s
    `of_m`, computed by the caller once it knows where the result will be
    written -- see docs/contracts/result-json.md for the call order)."""
    metrics_flat = leg_metrics(trades_flat, n_candidates=n_candidates, n_days=n_days)
    metrics_pressure = leg_metrics(trades_pressure_s1, n_candidates=n_candidates, n_days=n_days)
    gate_flat = gate_leg(metrics_flat)
    gate_pressure = gate_leg(metrics_pressure)

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": tool,
        "git_sha": git_sha,
        "command": command,
        "config": dict(config),
        "role": role,
        "data_blocks": list(data_blocks),
        "stage": stage,
        "metrics": {"flat": metrics_flat, "pressure_s1": metrics_pressure},
        "gate": {
            "flat": gate_flat,
            "pressure_s1": gate_pressure,
            "pass_both": bool(gate_flat["pass"] and gate_pressure["pass"]),
        },
        "tries": {
            "data_key": tries["data_key"],
            "variant_n": tries["variant_n"],
            "of_m": tries["of_m"],
        },
        "runtime_s": runtime_s,
        "peak_rss_mb": peak_rss_mb,
        "created_utc": created_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if notes is not None:
        result["notes"] = notes
    return result


def _minimal_validate(result: Mapping[str, Any], schema: Mapping[str, Any]) -> list[str]:
    """Fallback validator (required keys + basic types) used only if the
    `jsonschema` package is not installed."""
    errors: list[str] = []
    for key in schema.get("required", []):
        if key not in result:
            errors.append(f"missing required top-level key: {key!r}")
    if result.get("schema_version") != "result.v1":
        errors.append("schema_version must be 'result.v1'")
    for leg in ("metrics",):
        legs = result.get(leg, {})
        for name in ("flat", "pressure_s1"):
            if name not in legs:
                errors.append(f"{leg}.{name} missing")
                continue
            m = legs[name]
            for req in schema["$defs"]["metricsLeg"]["required"]:
                if req not in m:
                    errors.append(f"{leg}.{name}.{req} missing")
    for name in ("flat", "pressure_s1"):
        g = result.get("gate", {}).get(name, {})
        for req in schema["$defs"]["gateLeg"]["required"]:
            if req not in g:
                errors.append(f"gate.{name}.{req} missing")
    for req in schema["$defs"]["dataBlock"]["required"]:
        for i, block in enumerate(result.get("data_blocks", [])):
            if req not in block:
                errors.append(f"data_blocks[{i}].{req} missing")
    return errors


def validate_result(result: Mapping[str, Any], schema: Mapping[str, Any] | None = None) -> list[str]:
    """Return a list of validation error strings (empty == valid)."""
    if schema is None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        import jsonschema
    except ImportError:
        return _minimal_validate(result, schema)

    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    return [f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in validator.iter_errors(result)]


def write_result(path: str | Path, result: Mapping[str, Any]) -> None:
    """Validate `result` against schemas/result.v1.schema.json, then write
    it atomically (tmp file in the same directory + os.replace)."""
    errors = validate_result(result)
    if errors:
        raise ValueError("result does not match result.v1 schema:\n" + "\n".join(errors))

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + f".tmp{os.getpid()}")
    tmp_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, out_path)


# --- CLI --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    val = sub.add_parser("validate", help="validate a result.json against schemas/result.v1.schema.json")
    val.add_argument("path")
    args = ap.parse_args(argv)

    if args.cmd == "validate":
        result = json.loads(Path(args.path).read_text(encoding="utf-8"))
        errors = validate_result(result)
        if errors:
            for e in errors:
                print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print("OK")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
