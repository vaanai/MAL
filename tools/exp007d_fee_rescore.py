#!/usr/bin/env python3
"""EXP-007d — decode-only fee re-label on reused EXP-007c enrich (no new RPC)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.exp007c_fee_at_t import patch_enrich_row_fee
from tools.exp007_fee_resolve import classify_fee_tag
from tools.exp007_rpc_enrich import load_enrich_by_parent

EXP_ID = "EXP-007d-fee-global-95bps-enum-v0"


def fee_resolve_from_stored_rpc_meta(row: Mapping[str, Any]) -> Any | None:
    """Re-classify fee tag from EXP-007c `rpc_meta.fee_resolve` (knowable-at-T fields only)."""
    meta = row.get("rpc_meta")
    if not isinstance(meta, dict):
        return None
    fr = meta.get("fee_resolve")
    if not isinstance(fr, dict):
        return None
    global_bps = fr.get("global_fee_bps")
    if global_bps is not None and not isinstance(global_bps, int):
        return None
    is_holder = fr.get("is_holder_reward")
    if is_holder is not None and not isinstance(is_holder, bool):
        is_holder = None
    creator_bps = fr.get("creator_fee_bps")
    if creator_bps is not None and not isinstance(creator_bps, int):
        creator_bps = None
    global_ok = global_bps is not None
    return classify_fee_tag(
        global_fee_bps=global_bps,
        global_ok=global_ok,
        is_holder_reward=is_holder,
        creator_fee_bps=creator_bps,
    )


def rescore_enrich_jsonl(
    enrich_path: Path,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    enrich_map = load_enrich_by_parent([enrich_path])
    counts: dict[str, int] = {}
    skipped: dict[str, int] = {}
    patched: list[dict[str, Any]] = []

    for sig, row in enrich_map.items():
        fee_res = fee_resolve_from_stored_rpc_meta(row)
        if fee_res is None:
            skipped["missing_fee_resolve_meta"] = skipped.get("missing_fee_resolve_meta", 0) + 1
            continue
        counts[fee_res.fee] = counts.get(fee_res.fee, 0) + 1
        out_row = patch_enrich_row_fee(row, fee_res)
        out_row["exp007d_fee_rescore"] = EXP_ID
        patched.append(out_row)

    if output_path is not None:
        out_path = output_path
    elif "_exp007c-" in enrich_path.name:
        out_path = enrich_path.with_name(enrich_path.name.replace("_exp007c-", "_exp007d-"))
    else:
        out_path = enrich_path.with_name(f"{enrich_path.stem}_007d_rescore.jsonl")

    if patched:
        with out_path.open("w", encoding="utf-8") as fh:
            for row in patched:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    n = len(patched)
    fee_uv = counts.get("unverified", 0) / n if n else 1.0
    return {
        "exp": EXP_ID,
        "enrich_in": str(enrich_path),
        "output_enrich_jsonl": str(out_path),
        "rows_in": len(enrich_map),
        "rows_patched_n": n,
        "fee_counts": counts,
        "fee_unverified_rate": fee_uv,
        "fee_resolved_rate": 1.0 - fee_uv if n else 0.0,
        "skipped": skipped,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"{EXP_ID} — re-label fee from stored rpc_meta (proposed global_95bps)"
    )
    parser.add_argument("enrich_jsonl", type=Path, help="EXP-007c fee-stamped regime_enrich JSONL")
    parser.add_argument("--output", type=Path, default=None, help="Output path")
    args = parser.parse_args(argv)
    summary = rescore_enrich_jsonl(args.enrich_jsonl, output_path=args.output)
    print(json.dumps(summary, indent=2))
    return 0 if summary["rows_patched_n"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
