"""Compare the runner's EXP-012 gate rows with tools/exp012_forward.py's rows.

Input: runner `exp012-gate.jsonl` rows (schema forward_paper_exp012_gate_v1) and the
forward scorer's rows.jsonl. Join key: mint (the scorer writes one row per mint and
exit spec with the same score; the first per mint is used). Output: entered-set
overlap, score agreement over mints on both sides, migration-time difference, and,
if a scorer row carries a `features` map, per-feature mismatch counts. Measurement
only: no tolerance is applied to a verdict here (tolerances are fixed in DEC-016).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable


def _read(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _by_mint(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out.setdefault(r["mint"], r)
    return out


def compare(runner_rows: Iterable[dict[str, Any]], scorer_rows: Iterable[dict[str, Any]], *, feature_tol: float = 1e-9) -> dict[str, Any]:
    run, sco = _by_mint(runner_rows), _by_mint(scorer_rows)
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
    feat_mismatch: dict[str, int] = {}
    n_feat = 0
    for m in both:
        rf, sf = run[m].get("features"), sco[m].get("features")
        if not isinstance(rf, dict) or not isinstance(sf, dict):
            continue
        n_feat += 1
        for name in sorted(set(rf) | set(sf)):
            a, b = rf.get(name), sf.get(name)
            same = a is not None and b is not None and math.isclose(float(a), float(b), rel_tol=feature_tol, abs_tol=feature_tol)
            if not same:
                feat_mismatch[name] = feat_mismatch.get(name, 0) + 1
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
        "feature_n_mints_compared": n_feat,
        "feature_mismatch_counts": feat_mismatch,
        "runner_skip_reasons": runner_skips,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("runner_gate_jsonl", type=Path)
    ap.add_argument("scorer_rows_jsonl", type=Path)
    ap.add_argument("--feature-tol", type=float, default=1e-9)
    args = ap.parse_args(argv)
    report = compare(_read(args.runner_gate_jsonl), _read(args.scorer_rows_jsonl), feature_tol=args.feature_tol)
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
