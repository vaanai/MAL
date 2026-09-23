#!/usr/bin/env python3
"""EXP-007c — fee restamp CLI on reused EXP-007b enrich sample (knowable-at-T)."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

from observe.regime import build_regime_id
from tools.exp003_rpc_backfill import (
    SolanaRpcClient,
    _account_info_data_bytes,
    bonding_curve_address,
    resolve_rpc_url,
)
from tools.exp007_fee_resolve import (
    FeeResolve,
    PUMP_GLOBAL_PDA,
    classify_fee_tag,
    parse_bonding_curve_fee_extension,
    parse_global_fee_bps,
)
from tools.exp007_rpc_enrich import (
    T_WS_BLOCKTIME_SLACK_S,
    _slot_from_transaction,
    _t_ws_unix,
    knowable_at_t_ok,
    load_enrich_by_parent,
)

EXP_ID = "EXP-007c-fee-knowable-at-t-v0"
LOG = logging.getLogger("exp007c_fee_at_t")


def resolve_fee_at_t(
    client: SolanaRpcClient,
    create: Mapping[str, Any],
    *,
    create_slot: int,
    slack_s: int = T_WS_BLOCKTIME_SLACK_S,
) -> FeeResolve | None:
    t_unix = _t_ws_unix(create)
    if t_unix is None:
        return None
    sig = create.get("signature")
    if not isinstance(sig, str) or not sig:
        return None

    tx = client.get_transaction(sig)
    if tx is None:
        return None
    bt = tx.get("blockTime")
    if not isinstance(bt, (int, float)):
        return None
    bt_i = int(bt)
    if not knowable_at_t_ok(t_unix, bt_i, slack_s=slack_s):
        return None

    global_ok = False
    global_bps: int | None = None
    ginfo = client.get_account_info_at_slot(PUMP_GLOBAL_PDA, min_context_slot=create_slot)
    if ginfo is not None:
        raw_g = _account_info_data_bytes(ginfo)
        if raw_g is not None:
            global_bps = parse_global_fee_bps(raw_g)
            global_ok = global_bps is not None

    is_holder: bool | None = None
    creator_bps: int | None = None
    curve_addr = bonding_curve_address(create)
    if curve_addr:
        cinfo = client.get_account_info_at_slot(curve_addr, min_context_slot=create_slot)
        if cinfo is not None:
            raw_c = _account_info_data_bytes(cinfo)
            if raw_c is not None:
                is_holder, creator_bps = parse_bonding_curve_fee_extension(raw_c)

    return classify_fee_tag(
        global_fee_bps=global_bps,
        global_ok=global_ok,
        is_holder_reward=is_holder,
        creator_fee_bps=creator_bps,
    )


def patch_enrich_row_fee(row: Mapping[str, Any], fee_res: FeeResolve) -> dict[str, Any]:
    out = dict(row)
    kat = dict(out.get("knowable_at_t_enriched") or {})
    kat["fee"] = fee_res.fee
    out["knowable_at_t_enriched"] = kat

    plat = dict(out.get("platform_resolved") or {})
    plat["fee"] = fee_res.fee
    out["platform_resolved"] = plat

    stream = "subscribeNewToken"
    old_rid = out.get("regime_id_enriched")
    if isinstance(old_rid, str) and "stream=" in old_rid:
        for part in old_rid.split("|"):
            if part.startswith("stream="):
                stream = part.split("=", 1)[1]
                break
    stage = plat.get("stage") or "bonding"
    out["regime_id_enriched"] = build_regime_id(
        stream=stream,
        stage=stage if isinstance(stage, str) else "bonding",
        quote=str(plat.get("quote") or "wsol_assumed"),
        instr=str(plat.get("instr") or "unknown_until_rpc"),
        fee=fee_res.fee,
        venue="pump_program",
        market=str(plat.get("market") or "bonding_curve"),
    )

    rpc_meta = dict(out.get("rpc_meta") or {})
    rpc_meta["fee_resolve"] = {
        "exp": EXP_ID,
        "fee": fee_res.fee,
        "reason": fee_res.reason,
        "global_fee_bps": fee_res.global_fee_bps,
        "is_holder_reward": fee_res.is_holder_reward,
        "creator_fee_bps": fee_res.creator_fee_bps,
    }
    out["rpc_meta"] = rpc_meta
    out["exp007c_fee_stamp"] = EXP_ID
    return out


def restamp_enrich_jsonl(
    enrich_path: Path,
    observe_path: Path,
    *,
    output_path: Path | None = None,
    client: SolanaRpcClient | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    rpc = client or SolanaRpcClient(url=resolve_rpc_url())
    enrich_map = load_enrich_by_parent([enrich_path])
    sig_to_row: dict[str, Mapping[str, Any]] = {}
    with observe_path.open(encoding="utf-8") as fh:
        for line in fh:
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
                sig_to_row[sig] = obj

    counts: dict[str, int] = {}
    skipped: dict[str, int] = {}
    patched: list[dict[str, Any]] = []
    parents = list(enrich_map.keys())
    if limit is not None:
        parents = parents[:limit]

    for sig in parents:
        enrich_row = enrich_map[sig]
        create = sig_to_row.get(sig)
        if create is None:
            skipped["observe_parent_missing"] = skipped.get("observe_parent_missing", 0) + 1
            continue
        meta = enrich_row.get("rpc_meta")
        slot = meta.get("create_slot") if isinstance(meta, dict) else None
        if not isinstance(slot, int):
            tx = rpc.get_transaction(sig)
            slot = _slot_from_transaction(tx) if tx else None
        if slot is None:
            skipped["missing_create_slot"] = skipped.get("missing_create_slot", 0) + 1
            continue
        fee_res = resolve_fee_at_t(rpc, create, create_slot=slot)
        if fee_res is None:
            skipped["fee_resolve_failed"] = skipped.get("fee_resolve_failed", 0) + 1
            continue
        counts[fee_res.fee] = counts.get(fee_res.fee, 0) + 1
        patched.append(patch_enrich_row_fee(enrich_row, fee_res))

    out_path = output_path or enrich_path
    if patched:
        if out_path.resolve() == enrich_path.resolve():
            backup = enrich_path.with_suffix(enrich_path.suffix + ".pre007c")
            backup.write_text(enrich_path.read_text(encoding="utf-8"), encoding="utf-8")
        with out_path.open("w", encoding="utf-8") as fh:
            for row in patched:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    n = len(patched)
    fee_uv = counts.get("unverified", 0) / n if n else 1.0
    return {
        "exp": EXP_ID,
        "enrich_in": str(enrich_path),
        "observe_path": str(observe_path),
        "output_enrich_jsonl": str(out_path),
        "rows_in": len(enrich_map),
        "rows_patched_n": n,
        "fee_counts": counts,
        "fee_unverified_rate": fee_uv,
        "fee_resolved_rate": 1.0 - fee_uv if n else 0.0,
        "skipped": skipped,
    }


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=f"{EXP_ID} fee restamp on EXP-007b enrich JSONL")
    parser.add_argument("enrich_jsonl", type=Path, help="Existing regime_enrich side JSONL")
    parser.add_argument("observe_jsonl", type=Path, help="Matching sealed observe day file")
    parser.add_argument("--output", type=Path, default=None, help="Output path (default: in-place)")
    parser.add_argument("--limit", type=int, default=None, help="Debug cap on rows")
    args = parser.parse_args(argv)
    summary = restamp_enrich_jsonl(
        args.enrich_jsonl,
        args.observe_jsonl,
        output_path=args.output,
        limit=args.limit,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["rows_patched_n"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
