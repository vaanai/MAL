#!/usr/bin/env python3
"""EXP-007b — knowable-at-T RPC platform enrich (paper-only, append-only).

Subsamples sealed bonding creates (≤500/day), resolves platform fields via RPC at
decision time T (create tx + bonding-curve account at create slot). Writes
``type=regime_enrich`` side JSONL — never mutates sealed observe ingest lines.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from observe.regime import build_regime_id
from tools.exp001_mislabel import LoadedRow, load_jsonl_files, t_ws_missing
from tools.exp002_paper_runner import is_bonding_create
from tools.exp003_rpc_backfill import (
    SolanaRpcClient,
    _account_info_data_bytes,
    _b58encode,
    _instruction_side_from_logs,
    _parse_pump_bonding_curve_account,
    bonding_curve_address,
    resolve_rpc_url,
    subsample_creates,
)
from tools.marks import parse_iso_ts

EXP_ID = "EXP-007b-platform-regime-rpc-enrich-v0"
ENRICH_SCHEMA = "regime_enrich_v0"
ENRICH_TYPE = "regime_enrich"

DEFAULT_OUTPUT_DIR = Path("data/observe")
DEFAULT_PREFIX = "_exp007b"
DEFAULT_SAMPLE_N = 500
DEFAULT_SEED = 1
DEFAULT_COMMITMENT = "confirmed"
MAX_SAMPLE_PER_DAY = 500
# WS receipt may trail on-chain blockTime slightly; larger slack = false leak flags.
T_WS_BLOCKTIME_SLACK_S = 120

WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

LOG = logging.getLogger("exp007_rpc_enrich")


def cap_sample_n(requested: int, population_n: int) -> int:
    """Hard cap ≤500/day (or full population if smaller)."""
    if requested < 0:
        raise ValueError("sample_n must be >= 0")
    cap = min(MAX_SAMPLE_PER_DAY, population_n) if population_n > 0 else MAX_SAMPLE_PER_DAY
    if requested == 0:
        return cap
    return min(requested, cap)


def collect_eligible_creates(loaded: Sequence[LoadedRow]) -> list[LoadedRow]:
    out: list[LoadedRow] = []
    for item in loaded:
        if not is_bonding_create(item.row):
            continue
        if t_ws_missing(item.row):
            continue
        out.append(item)
    return out


def _load_marks_parent_sigs(marks_paths: Sequence[Path]) -> set[str]:
    sigs: set[str] = set()
    for path in marks_paths:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as fh:
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
                parent = obj.get("parent_signature")
                if isinstance(parent, str) and parent not in ("", "UNK"):
                    sigs.add(parent)
    return sigs


def sample_creates_for_day(
    creates: Sequence[LoadedRow],
    *,
    sample_n: int,
    seed: int,
    marks_paths: Sequence[Path] | None = None,
) -> list[LoadedRow]:
    pool = list(creates)
    if marks_paths:
        parent_sigs = _load_marks_parent_sigs(marks_paths)
        if parent_sigs:
            pool = [
                item
                for item in pool
                if isinstance(item.row.get("signature"), str)
                and item.row["signature"] in parent_sigs
            ]
    n = cap_sample_n(sample_n, len(pool))
    return subsample_creates(pool, sample_n=n, seed=seed)


def _instr_from_logs(log_messages: Sequence[Any]) -> str | None:
    for line in log_messages:
        if not isinstance(line, str):
            continue
        if "Instruction: CreateV2" in line:
            return "create_v2"
        if "Instruction: Create" in line:
            return "create"
    side = _instruction_side_from_logs(log_messages)
    if side == "create":
        return "create"
    return None


def _quote_tag_from_mint(mint_b58: str | None) -> tuple[str, bool]:
    if not mint_b58:
        return "wsol_assumed", False
    if mint_b58 == WSOL_MINT:
        return "wsol", True
    if mint_b58 == USDC_MINT:
        return "usdc", True
    return "other", True


def _parse_bonding_curve_extended(data: bytes) -> dict[str, Any] | None:
    base = _parse_pump_bonding_curve_account(data)
    if base is None:
        return None
    quote_mint: str | None = None
    # v2 layout: mint @49, quote_mint @81 (after 8-byte discriminator layout used in exp003).
    if len(data) >= 113:
        quote_mint = _b58encode(data[81:113])
    out = dict(base)
    out["quote_mint"] = quote_mint
    return out


def _slot_from_transaction(tx: Mapping[str, Any]) -> int | None:
    slot = tx.get("slot")
    if isinstance(slot, int):
        return slot
    if isinstance(slot, float):
        return int(slot)
    return None


def _block_time_from_tx(tx: Mapping[str, Any]) -> int | None:
    bt = tx.get("blockTime")
    if isinstance(bt, int):
        return bt
    if isinstance(bt, float):
        return int(bt)
    return None


def _t_ws_unix(row: Mapping[str, Any]) -> int | None:
    t_ws = row.get("t_ws")
    if not isinstance(t_ws, str):
        return None
    dt = parse_iso_ts(t_ws)
    if dt is None:
        return None
    return int(dt.timestamp())


def knowable_at_t_ok(t_ws_unix: int, rpc_block_time: int, *, slack_s: int = T_WS_BLOCKTIME_SLACK_S) -> bool:
    """RPC evidence must not be after decision T (with small WS latency slack)."""
    return rpc_block_time <= t_ws_unix + slack_s


@dataclass(frozen=True)
class PlatformResolve:
    instr: str
    fee: str
    quote: str
    quote_verified: bool
    venue: str
    stage: str
    market: str
    rpc_block_time: int
    rpc_slot: int | None
    quote_mint: str | None
    leak_reject: bool
    leak_reason: str | None


def resolve_platform_at_t(
    client: SolanaRpcClient,
    create: Mapping[str, Any],
    *,
    slack_s: int = T_WS_BLOCKTIME_SLACK_S,
) -> PlatformResolve | None:
    """Resolve platform slice from create tx + bonding curve at create slot."""
    sig = create.get("signature")
    if not isinstance(sig, str) or sig in ("", "UNK"):
        return None
    t_unix = _t_ws_unix(create)
    if t_unix is None:
        return None

    tx = client.get_transaction(sig)
    if tx is None:
        return None
    bt = _block_time_from_tx(tx)
    if bt is None:
        return None
    leak = not knowable_at_t_ok(t_unix, bt, slack_s=slack_s)
    leak_reason = "rpc_block_time_after_t_ws" if leak else None

    meta = tx.get("meta")
    logs: list[Any] = []
    if isinstance(meta, dict) and isinstance(meta.get("logMessages"), list):
        logs = meta["logMessages"]

    instr = _instr_from_logs(logs) or "unknown_until_rpc"
    row_stage = create.get("stage")
    stage = str(row_stage) if row_stage else "bonding"
    market = "bonding_curve"
    fee = "unverified"
    quote = "wsol_assumed"
    quote_verified = False
    quote_mint: str | None = None

    curve_addr = bonding_curve_address(create)
    slot = _slot_from_transaction(tx)
    if curve_addr and slot is not None and not leak:
        account_info = client.get_account_info_at_slot(curve_addr, min_context_slot=slot)
        if account_info is not None:
            raw = _account_info_data_bytes(account_info)
            if raw is not None:
                parsed = _parse_bonding_curve_extended(raw)
                if parsed is not None:
                    if parsed.get("complete"):
                        stage = "bonding_complete"
                        market = "bonding_curve"
                    quote_mint = parsed.get("quote_mint") if isinstance(parsed.get("quote_mint"), str) else None
                    if quote_mint:
                        quote, quote_verified = _quote_tag_from_mint(quote_mint)
                    elif instr in ("create", "create_v2"):
                        # Legacy create path — SOL quote implied when mint field absent.
                        quote, quote_verified = ("wsol", True) if instr == "create" else ("wsol_assumed", False)

    return PlatformResolve(
        instr=instr,
        fee=fee,
        quote=quote,
        quote_verified=quote_verified,
        venue="pump_program",
        stage=stage,
        market=market,
        rpc_block_time=bt,
        rpc_slot=slot,
        quote_mint=quote_mint,
        leak_reject=leak,
        leak_reason=leak_reason,
    )


def build_enrich_row(
    create: Mapping[str, Any],
    resolved: PlatformResolve,
    *,
    source_path: str,
    line_no: int,
) -> dict[str, Any]:
    stream = create.get("stream")
    stream_s = stream if isinstance(stream, str) else "subscribeNewToken"
    regime_id_enriched = build_regime_id(
        stream=stream_s,
        stage=resolved.stage if resolved.stage in ("bonding", "bonding_complete", "migrating", "pumpswap", "legacy_raydium") else "bonding",
        quote=resolved.quote,
        instr=resolved.instr,
        fee=resolved.fee,
        venue=resolved.venue,
        market=resolved.market,
    )
    kat = {
        "quote": resolved.quote,
        "quote_verified": resolved.quote_verified,
        "instr": resolved.instr,
        "fee": resolved.fee,
        "venue": resolved.venue,
        "venue_verified": True,
        "creator_verified": create.get("knowable_at_t", {}).get("creator_verified", False)
        if isinstance(create.get("knowable_at_t"), dict)
        else False,
        "reserves_source": "ws",
    }
    t_ws = create.get("t_ws")
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    row: dict[str, Any] = {
        "schema_version": ENRICH_SCHEMA,
        "type": ENRICH_TYPE,
        "exp": EXP_ID,
        "parent_signature": create.get("signature"),
        "mint": create.get("mint"),
        "t_decision": t_ws,
        "t_enrich": now,
        "t_rpc_as_of": datetime.fromtimestamp(resolved.rpc_block_time, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        ),
        "source": "rpc_knowable_at_t",
        "knowable_at_t_enriched": kat,
        "regime_id_enriched": regime_id_enriched,
        "platform_resolved": {
            "instr": resolved.instr,
            "fee": resolved.fee,
            "quote": resolved.quote,
            "quote_verified": resolved.quote_verified,
            "stage": resolved.stage,
            "market": resolved.market,
            "quote_mint": resolved.quote_mint,
        },
        "rpc_meta": {
            "create_slot": resolved.rpc_slot,
            "rpc_block_time": resolved.rpc_block_time,
            "leak_reject": resolved.leak_reject,
            "leak_reason": resolved.leak_reason,
        },
        "provenance": {"observe_source_path": source_path, "observe_line_no": line_no},
    }
    return row


def append_enrich_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_enrich_by_parent(paths: Sequence[Path]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict) or obj.get("type") != ENRICH_TYPE:
                    continue
                parent = obj.get("parent_signature")
                if isinstance(parent, str) and parent:
                    out[parent] = obj
    return out


def apply_enrich_overlay(row: Mapping[str, Any], enrich: Mapping[str, Any] | None) -> dict[str, Any]:
    """Virtual row for audit — sealed ingest line unchanged on disk."""
    if not enrich:
        return dict(row)
    out = dict(row)
    rid = enrich.get("regime_id_enriched")
    kat = enrich.get("knowable_at_t_enriched")
    if isinstance(rid, str):
        out["regime_id"] = rid
    if isinstance(kat, dict):
        out["knowable_at_t"] = dict(kat)
    meta = enrich.get("rpc_meta")
    if isinstance(meta, dict) and meta.get("leak_reject"):
        out["_exp007b_leak_reject"] = True
    return out


def run_single_day(
    observe_path: Path,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = DEFAULT_PREFIX,
    sample_n: int = DEFAULT_SAMPLE_N,
    seed: int = DEFAULT_SEED,
    marks_paths: Sequence[Path] | None = None,
    rpc_url: str | None = None,
    client: SolanaRpcClient | None = None,
    dry_run: bool = False,
    commitment: str = DEFAULT_COMMITMENT,
) -> dict[str, Any]:
    loaded, malformed = load_jsonl_files([observe_path])
    creates = collect_eligible_creates(loaded)
    sampled = sample_creates_for_day(
        creates, sample_n=sample_n, seed=seed, marks_paths=marks_paths
    )
    day_key = observe_path.stem.replace("observe-", "")
    out_jsonl = output_dir / f"{prefix}-{day_key}_regime_enrich.jsonl"
    summary_path = output_dir / f"{prefix}-{day_key}_enrich_summary.json"

    rpc = client or SolanaRpcClient(url=rpc_url or resolve_rpc_url(), commitment=commitment)

    written = 0
    skipped: dict[str, int] = {}
    leak_n = 0
    rows_out: list[dict[str, Any]] = []

    for item in sampled:
        if dry_run:
            continue
        resolved = resolve_platform_at_t(rpc, item.row)
        if resolved is None:
            skipped["resolve_failed"] = skipped.get("resolve_failed", 0) + 1
            continue
        if resolved.leak_reject:
            leak_n += 1
            skipped["leak_reject"] = skipped.get("leak_reject", 0) + 1
            continue
        enrich_row = build_enrich_row(
            item.row, resolved, source_path=item.source_path, line_no=item.line_no
        )
        rows_out.append(enrich_row)
        written += 1
        if written % 25 == 0:
            LOG.info("enriched %d/%d", written, len(sampled))

    if not dry_run and rows_out:
        append_enrich_rows(out_jsonl, rows_out)

    summary = {
        "exp": EXP_ID,
        "courier_day": day_key,
        "observe_path": str(observe_path),
        "malformed_jsonl_lines": malformed,
        "eligible_n": len(creates),
        "sample_requested_n": sample_n,
        "sample_effective_n": len(sampled),
        "max_sample_cap": MAX_SAMPLE_PER_DAY,
        "seed": seed,
        "marks_intersection": bool(marks_paths),
        "enrich_written_n": written,
        "leak_reject_n": leak_n,
        "skipped": skipped,
        "dry_run": dry_run,
        "output_enrich_jsonl": str(out_jsonl),
        "summary_path": str(summary_path),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=f"{EXP_ID} RPC platform enrich (append-only)")
    parser.add_argument("observe_path", type=Path, help="Day-aligned sealed observe JSONL")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument(
        "--sample",
        type=int,
        default=DEFAULT_SAMPLE_N,
        help=f"Max creates to enrich (hard cap {MAX_SAMPLE_PER_DAY}/day)",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--marks", nargs="*", type=Path, default=None, help="Optional same-day marks JSONL for cohort intersection")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--commitment", default=DEFAULT_COMMITMENT)
    args = parser.parse_args(argv)

    summary = run_single_day(
        args.observe_path,
        output_dir=args.output_dir,
        prefix=args.prefix,
        sample_n=args.sample,
        seed=args.seed,
        marks_paths=args.marks,
        dry_run=args.dry_run,
        commitment=args.commitment,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["enrich_written_n"] > 0 or args.dry_run else 1


# Extend RPC client with slot-bounded account reads (knowable-at-T).
def _patch_solana_client() -> None:
    if getattr(SolanaRpcClient, "get_account_info_at_slot", None):
        return

    def get_account_info_at_slot(
        self: SolanaRpcClient, address: str, *, min_context_slot: int
    ) -> dict[str, Any] | None:
        result = self.post(
            "getAccountInfo",
            [
                address,
                {
                    "encoding": "base64",
                    "commitment": self.commitment,
                    "minContextSlot": min_context_slot,
                },
            ],
        )
        return result if isinstance(result, dict) else None

    SolanaRpcClient.get_account_info_at_slot = get_account_info_at_slot  # type: ignore[method-assign]


_patch_solana_client()


if __name__ == "__main__":
    raise SystemExit(main())
