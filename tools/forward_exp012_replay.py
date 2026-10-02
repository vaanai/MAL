"""Compare the runner's EXP-012 gate rows with the forward scorer's decision export.

DEC-016 Amendment 2: before the FINAL read nobody opens OUT/rows.jsonl. This helper
reads only `decisions.jsonl` written by `tools/exp012_forward.py export-decisions`,
whose keys are the allowlist `DECISION_EXPORT_KEYS` (mint, mig_ms, score, entered,
day). A scorer file with any other key is refused, so a rows.jsonl cannot be passed
in by mistake. Runner side: `exp012-gate.jsonl` rows (forward_paper_exp012_gate_v1).
Output: entered-set overlap, score agreement (max |delta|) over mints on both sides,
migration-time difference and runner skip reasons. Per-feature comparison is not
done: the scorer export carries no features. Measurement only; no verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from tools.exp012_forward import DECISION_EXPORT_KEYS


class ExportRefused(Exception):
    pass


def _read(path: Path) -> list[dict[str, Any]]:
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


def compare(runner_rows: Iterable[dict[str, Any]], scorer_rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
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
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("runner_gate_jsonl", type=Path)
    ap.add_argument("scorer_decisions_jsonl", type=Path, help="export-decisions output only")
    args = ap.parse_args(argv)
    try:
        report = compare(_read(args.runner_gate_jsonl), _read(args.scorer_decisions_jsonl))
    except ExportRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
