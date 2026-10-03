"""Compare the runner's EXP-012 gate rows with the forward scorer's decision export.

DEC-016 Amendment 2: before the FINAL read nobody opens OUT/rows.jsonl. This helper
reads only `decisions.jsonl` written by `tools/exp012_forward.py export-decisions`,
whose keys are the allowlist `DECISION_EXPORT_KEYS` (mint, mig_ms, score, entered,
day). A scorer file with any other key is refused, so a rows.jsonl cannot be passed
in by mistake. Runner side: `exp012-gate.jsonl` rows (forward_paper_exp012_gate_v1).
Output: entered-set overlap, score agreement (max |delta|) over mints on both sides,
migration-time difference and runner skip reasons. Per-feature comparison is not
done: the scorer export carries no features. Measurement only for the legacy keys.

DEC-016 Amendment 3 (b) rows 0-2 are computed under the key `amendment3_b`, each with
a pass/fail and an overall verdict (PASS / FAIL / NOT_DECIDABLE). Population: scorer
mints with `mig_ms` in [--from, --to) outside the downtime intervals. The runner does
not log its heartbeat history (runner-status.json is overwritten in place and is
sealed), so runner-up minutes cannot be derived from its logs: pass `--downtime FILE`
(JSON list of [start, end) pairs, epoch ms or ISO) with each restart from stop until
10 min after the first heartbeat, plus every minute whose heartbeat was 60 s or older.
`stale_recv` drops come from an `exp012_runner_latency_export` file (`--latency-export`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Any, Iterable, Sequence

from tools.exp012_forward import DECISION_EXPORT_KEYS
from tools.exp012_runner_latency_export import EXPORT_KEYS as LATENCY_EXPORT_KEYS
from tools.exp012_runner_latency_export import Refused, refuse_forbidden


class ExportRefused(Exception):
    pass


def _read(path: Path) -> list[dict[str, Any]]:
    refuse_forbidden(path)  # positions* and runner-status* are sealed (DEC-016 Amendment 3)
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def check_export(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Refuse any scorer row whose keys are not exactly the export allowlist."""
    out = list(rows)
    for n, r in enumerate(out, 1):
        extra = sorted(set(r) - set(DECISION_EXPORT_KEYS))
        missing = sorted(set(DECISION_EXPORT_KEYS) - set(r))
        if extra or missing:
            raise ExportRefused(f"scorer row {n} is not a decisions export row (extra keys: {extra}, missing: {missing}); read only export-decisions output")
    return out


def _by_mint(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out.setdefault(r["mint"], r)
    return out


# Tolerance comparisons below are strict float comparisons (>= / <=), with no epsilon.
COVERAGE_MIN = 0.95
JACCARD_MIN = 0.90
DELTA_P95_MAX = 0.02
DELTA_P99_MAX = 0.05
MIN_ENTERED_BOTH = 200
MIN_DAYS = 5


def _pct(sorted_vals: Sequence[float], p: float) -> float:
    """Non-interpolating percentile, index round(p * (n - 1))."""
    return sorted_vals[int(round(p * (len(sorted_vals) - 1)))]


def _to_ms(v: Any) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v)
    return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp() * 1000)


def load_downtime(path: Path) -> list[tuple[int, int]]:
    return [(_to_ms(a), _to_ms(b)) for a, b in json.loads(path.read_text(encoding="utf-8"))]


def amendment3_b(
    run: dict[str, dict[str, Any]],
    sco: dict[str, dict[str, Any]],
    *,
    stale_mints: Iterable[str] | None = None,
    downtime: Sequence[tuple[int, int]] | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
) -> dict[str, Any]:
    """Rows 0-2 of DEC-016 Amendment 3 (b). `run` and `sco` are by-mint maps."""
    stale_known = stale_mints is not None
    window_supplied = from_ms is not None and to_ms is not None
    downtime_supplied = downtime is not None
    downtime = downtime or ()
    stale = set(stale_mints or ())

    def in_pop(row: dict[str, Any]) -> bool:
        t = row.get("mig_ms")
        if t is None:
            return False
        if from_ms is not None and t < from_ms:
            return False
        if to_ms is not None and t >= to_ms:
            return False
        return not any(a <= t < b for a, b in downtime)

    pop = {m: r for m, r in sco.items() if in_pop(r)}
    sco_entered = {m for m, r in pop.items() if r.get("entered")}
    covered = {m for m in sco_entered if m in run and m not in stale}
    both = sorted(m for m in pop if m in run)
    r_in = {m for m in both if run[m].get("entered")}
    s_in = {m for m in both if pop[m].get("entered")}
    entered_both = r_in & s_in
    union = r_in | s_in
    days = {pop[m]["day"] for m in entered_both}
    deltas = sorted(
        abs(float(run[m]["score"]) - float(pop[m]["score"]))
        for m in both
        if run[m].get("score") is not None and pop[m].get("score") is not None
    )
    # Never assume there were no stale decisions: without the latency export row 0 is undecidable.
    cov = (len(covered) / len(sco_entered)) if (sco_entered and stale_known) else None
    jac = (len(entered_both) / len(union)) if union else None
    p95 = _pct(deltas, 0.95) if deltas else None
    p99 = _pct(deltas, 0.99) if deltas else None
    row0 = None if cov is None else cov >= COVERAGE_MIN
    row1 = None if jac is None else jac >= JACCARD_MIN
    row2 = None if p95 is None else (p95 <= DELTA_P95_MAX and p99 <= DELTA_P99_MAX)
    decidable = len(entered_both) >= MIN_ENTERED_BOTH and len(days) >= MIN_DAYS
    rows = (row0, row1, row2)
    if not decidable or not window_supplied or not downtime_supplied or any(x is None for x in rows):
        verdict = "NOT_DECIDABLE"
    else:
        verdict = "PASS" if all(rows) else "FAIL"
    return {
        "n_population_scorer_mints": len(pop),
        "n_population_scorer_entered": len(sco_entered),
        "n_both_seen": len(both),
        "n_entered_by_both": len(entered_both),
        "n_utc_days_entered_by_both": len(days),
        "min_entered_by_both": MIN_ENTERED_BOTH,
        "min_days": MIN_DAYS,
        "decidable": decidable,
        "stale_mints_supplied": stale_known,
        "window_supplied": window_supplied,
        "downtime_supplied": downtime_supplied,
        "n_both_seen_unscored": sum(1 for m in both if run[m].get("score") is None or pop[m].get("score") is None),
        "row0_coverage": cov,
        "row0_pass": row0,
        "row1_jaccard_both_seen": jac,
        "row1_pass": row1,
        "row2_n": len(deltas),
        "row2_p95": p95,
        "row2_p99": p99,
        "row2_max_reported_only": deltas[-1] if deltas else None,
        "row2_pass": row2,
        "verdict": verdict,
    }


def compare(
    runner_rows: Iterable[dict[str, Any]],
    scorer_rows: Iterable[dict[str, Any]],
    *,
    stale_mints: Iterable[str] | None = None,
    downtime: Sequence[tuple[int, int]] | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
) -> dict[str, Any]:
    run, sco = _by_mint(runner_rows), _by_mint(check_export(scorer_rows))
    both = sorted(set(run) & set(sco))
    run_in = {m for m, r in run.items() if r.get("entered")}
    sco_in = {m for m, r in sco.items() if r.get("entered")}
    union = run_in | sco_in
    deltas = [
        abs(float(run[m]["score"]) - float(sco[m]["score"]))
        for m in both
        if run[m].get("score") is not None and sco[m].get("score") is not None
    ]
    mig = [abs(int(run[m]["mig_ms"]) - int(sco[m]["mig_ms"])) for m in both if "mig_ms" in run[m] and "mig_ms" in sco[m]]
    runner_skips: dict[str, int] = {}
    for r in run.values():
        if not r.get("entered"):
            key = str(r.get("reason"))
            runner_skips[key] = runner_skips.get(key, 0) + 1
    return {
        "n_runner_mints": len(run),
        "n_scorer_mints": len(sco),
        "n_both": len(both),
        "only_runner_mints": sorted(set(run) - set(sco)),
        "only_scorer_mints": sorted(set(sco) - set(run)),
        "entered_runner": len(run_in),
        "entered_scorer": len(sco_in),
        "entered_both": len(run_in & sco_in),
        "entered_only_runner": sorted(run_in - sco_in),
        "entered_only_scorer": sorted(sco_in - run_in),
        "entered_jaccard": (len(run_in & sco_in) / len(union)) if union else None,
        "score_n_compared": len(deltas),
        "score_max_abs_delta": max(deltas) if deltas else None,
        "mig_ms_max_abs_delta": max(mig) if mig else None,
        "runner_skip_reasons": runner_skips,
        "amendment3_b": amendment3_b(run, sco, stale_mints=stale_mints, downtime=downtime, from_ms=from_ms, to_ms=to_ms),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("runner_gate_jsonl", type=Path)
    ap.add_argument("scorer_decisions_jsonl", type=Path, help="export-decisions output only")
    ap.add_argument("--from", dest="from_", help="runner clean clock (ISO or epoch ms)")
    ap.add_argument("--to", help="exclusive end (ISO or epoch ms)")
    ap.add_argument("--downtime", type=Path, help="JSON list of [start, end) runner-down intervals")
    ap.add_argument("--latency-export", type=Path, help="exp012_runner_latency_export rows (jsonl); stale=true marks stale_recv drops")
    args = ap.parse_args(argv)
    try:
        stale: list[str] | None = None
        if args.latency_export:
            lat = _read(args.latency_export)
            for n, r in enumerate(lat, 1):
                if set(r) != set(LATENCY_EXPORT_KEYS):
                    raise ExportRefused(f"latency export row {n} keys are not exactly the allowlist {LATENCY_EXPORT_KEYS}")
            stale = [r["mint"] for r in lat if r.get("stale")]
        report = compare(
            _read(args.runner_gate_jsonl),
            _read(args.scorer_decisions_jsonl),
            stale_mints=stale,
            downtime=load_downtime(args.downtime) if args.downtime else None,
            from_ms=_to_ms(args.from_) if args.from_ else None,
            to_ms=_to_ms(args.to) if args.to else None,
        )
    except (ExportRefused, Refused) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
