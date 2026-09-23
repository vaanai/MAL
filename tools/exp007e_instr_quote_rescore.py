#!/usr/bin/env python3
"""EXP-007e — instr/quote restamp on reused EXP-007d enrich (knowable-at-T RPC re-decode)."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from observe.regime import build_regime_id
from tools.exp003_rpc_backfill import SolanaRpcClient, resolve_rpc_url
from tools.exp007_instr_quote_resolve import classify_instr_quote_residual
from tools.exp007_rpc_enrich import (
    build_enrich_row,
    load_enrich_by_parent,
    resolve_platform_at_t,
)

EXP_ID = "EXP-007e-instr-quote-residual-v0"
LOG = logging.getLogger("exp007e_instr_quote_rescore")


def _fee_from_enrich_row(row: Mapping[str, Any]) -> tuple[str, Mapping[str, Any] | None]:
    kat = row.get("knowable_at_t_enriched")
    if isinstance(kat, dict) and isinstance(kat.get("fee"), str):
        fee = kat["fee"]
    else:
        fee = "unverified"
    meta = row.get("rpc_meta")
    fr = meta.get("fee_resolve") if isinstance(meta, dict) else None
    return fee, fr if isinstance(fr, dict) else None


def patch_enrich_row_platform(
    enrich_row: Mapping[str, Any],
    create: Mapping[str, Any],
    *,
    source_path: str,
    line_no: int,
    client: SolanaRpcClient,
) -> dict[str, Any] | None:
    resolved = resolve_platform_at_t(client, create)
    if resolved is None or resolved.leak_reject:
        return None
    fee_keep, fee_resolve_meta = _fee_from_enrich_row(enrich_row)
    resolved = replace(resolved, fee=fee_keep, fee_reason="preserved_from_exp007d")
    out = build_enrich_row(create, resolved, source_path=source_path, line_no=line_no)
    out["exp"] = enrich_row.get("exp", out.get("exp"))
    if fee_resolve_meta:
        rpc_meta = dict(out.get("rpc_meta") or {})
        rpc_meta["fee_resolve"] = fee_resolve_meta
        out["rpc_meta"] = rpc_meta
    kat = dict(out.get("knowable_at_t_enriched") or {})
    kat["fee"] = fee_keep
    out["knowable_at_t_enriched"] = kat
    plat = dict(out.get("platform_resolved") or {})
    plat["fee"] = fee_keep
    out["platform_resolved"] = plat
    stream = "subscribeNewToken"
    old_rid = out.get("regime_id_enriched")
    if isinstance(old_rid, str) and "stream=" in old_rid:
        for part in old_rid.split("|"):
            if part.startswith("stream="):
                stream = part.split("=", 1)[1]
                break
    out["regime_id_enriched"] = build_regime_id(
        stream=stream,
        stage=str(plat.get("stage") or "bonding"),
        quote=str(plat.get("quote") or "wsol_assumed"),
        instr=str(plat.get("instr") or "unknown_until_rpc"),
        fee=fee_keep,
        venue=str(plat.get("venue") or resolved.venue),
        market=str(plat.get("market") or "bonding_curve"),
    )
    out["exp007e_instr_quote_rescore"] = EXP_ID
    out["residual_taxonomy_v0"] = classify_instr_quote_residual(
        instr=str(plat.get("instr") or ""),
        quote=str(plat.get("quote") or ""),
        quote_verified=bool(plat.get("quote_verified")),
        venue=str(plat.get("venue") or resolved.venue),
        quote_mint=plat.get("quote_mint") if isinstance(plat.get("quote_mint"), str) else None,
        tx_fetched=True,
        curve_account_ok=resolved.venue == "pump_program",
    )
    for key in ("exp007c_fee_stamp", "exp007d_fee_rescore"):
        if key in enrich_row:
            out[key] = enrich_row[key]
    return out


def restamp_instr_quote_jsonl(
    enrich_path: Path,
    observe_path: Path,
    *,
    output_path: Path | None = None,
    client: SolanaRpcClient | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    rpc = client or SolanaRpcClient(url=resolve_rpc_url())
    enrich_map = load_enrich_by_parent([enrich_path])
    sig_to_obs: dict[str, tuple[Mapping[str, Any], str, int]] = {}
    with observe_path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            sig = obj.get("signature")
            if isinstance(sig, str) and sig in enrich_map:
                sig_to_obs[sig] = (obj, str(observe_path), line_no)

    taxonomy: dict[str, int] = {}
    skipped: dict[str, int] = {}
    patched: list[dict[str, Any]] = []
    parents = list(enrich_map.keys())
    if limit is not None:
        parents = parents[:limit]

    for i, sig in enumerate(parents):
        enrich_row = enrich_map[sig]
        obs = sig_to_obs.get(sig)
        if obs is None:
            skipped["observe_parent_missing"] = skipped.get("observe_parent_missing", 0) + 1
            continue
        create, src, lno = obs
        row = patch_enrich_row_platform(
            enrich_row, create, source_path=src, line_no=lno, client=rpc
        )
        if row is None:
            skipped["platform_resolve_failed"] = skipped.get("platform_resolve_failed", 0) + 1
            continue
        patched.append(row)
        bucket = row.get("residual_taxonomy_v0")
        if isinstance(bucket, str):
            taxonomy[bucket] = taxonomy.get(bucket, 0) + 1
        if (i + 1) % 50 == 0:
            LOG.info("restamped %d/%d", i + 1, len(parents))

    if output_path is None:
        if "_exp007d-" in enrich_path.name:
            out_path = enrich_path.with_name(enrich_path.name.replace("_exp007d-", "_exp007e-oracle-"))
        else:
            out_path = enrich_path.with_name(f"{enrich_path.stem}_007e_rescore.jsonl")
    else:
        out_path = output_path

    if patched:
        with out_path.open("w", encoding="utf-8") as fh:
            for row in patched:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    return {
        "exp": EXP_ID,
        "enrich_in": str(enrich_path),
        "observe_path": str(observe_path),
        "output_enrich_jsonl": str(out_path),
        "rows_in": len(enrich_map),
        "rows_patched_n": len(patched),
        "residual_taxonomy": taxonomy,
        "skipped": skipped,
    }


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=f"{EXP_ID} instr/quote restamp on EXP-007d enrich")
    parser.add_argument("enrich_jsonl", type=Path, help="EXP-007d fee-rescored regime_enrich JSONL")
    parser.add_argument("observe_jsonl", type=Path, help="Matching sealed observe day file")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    summary = restamp_instr_quote_jsonl(
        args.enrich_jsonl,
        args.observe_jsonl,
        output_path=args.output,
        limit=args.limit,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["rows_patched_n"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
