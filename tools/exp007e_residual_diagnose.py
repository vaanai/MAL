#!/usr/bin/env python3
"""EXP-007e — residual taxonomy on regime_enrich overlay (no RPC)."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.exp007_instr_quote_resolve import classify_instr_quote_residual, is_plausible_mint
from tools.exp007_rpc_enrich import load_enrich_by_parent

EXP_ID = "EXP-007e-instr-quote-residual-v0"


def diagnose_enrich_jsonl(enrich_path: Path) -> dict[str, Any]:
    rows = list(load_enrich_by_parent([enrich_path]).values())
    taxonomy: Counter[str] = Counter()
    instr_c: Counter[str] = Counter()
    quote_c: Counter[str] = Counter()
    qv_n = 0
    invalid_mint_n = 0

    for row in rows:
        plat = row.get("platform_resolved") if isinstance(row.get("platform_resolved"), dict) else {}
        instr = str(plat.get("instr") or "")
        quote = str(plat.get("quote") or "")
        qv = plat.get("quote_verified") is True
        if qv:
            qv_n += 1
        instr_c[instr] += 1
        quote_c[quote] += 1
        qm = plat.get("quote_mint")
        if isinstance(qm, str) and not is_plausible_mint(qm):
            invalid_mint_n += 1
        bucket = row.get("residual_taxonomy_v0")
        if isinstance(bucket, str):
            taxonomy[bucket] += 1
        else:
            taxonomy[
                classify_instr_quote_residual(
                    instr=instr,
                    quote=quote,
                    quote_verified=qv,
                    venue=str(plat.get("venue") or "pump_program"),
                    quote_mint=qm if isinstance(qm, str) else None,
                    tx_fetched=True,
                    curve_account_ok=True,
                )
            ] += 1

    n = len(rows)
    pending = sum(
        1
        for row in rows
        if str((row.get("platform_resolved") or {}).get("instr") or "")
        in ("pending_rpc", "unknown_until_rpc")
    )
    return {
        "exp": EXP_ID,
        "enrich_path": str(enrich_path),
        "population_n": n,
        "instr_pending_or_unknown_rate": (pending / n) if n else None,
        "quote_verified_true_rate": (qv_n / n) if n else None,
        "invalid_quote_mint_n": invalid_mint_n,
        "instr_counts": dict(instr_c),
        "quote_counts": dict(quote_c),
        "residual_taxonomy": dict(taxonomy),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{EXP_ID} residual diagnose (offline)")
    parser.add_argument("enrich_jsonl", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    summary = diagnose_enrich_jsonl(args.enrich_jsonl)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("population_n") else 1


if __name__ == "__main__":
    raise SystemExit(main())
