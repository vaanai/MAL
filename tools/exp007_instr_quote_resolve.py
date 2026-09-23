"""Knowable-at-T instr / quote / venue helpers for EXP-007b enrich and EXP-007e restamp."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

import base64

from tools.exp003_rpc_backfill import (
    _b58encode,
    _instruction_side_from_logs,
    _parse_pump_create_event,
)

WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _quote_tag_from_mint(mint_b58: str | None) -> tuple[str, bool]:
    if not mint_b58:
        return "wsol_assumed", False
    if mint_b58 == WSOL_MINT:
        return "wsol", True
    if mint_b58 == USDC_MINT:
        return "usdc", True
    return "other", True


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

PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
# Raydium LaunchLab (mainnet); program id may vary by deployment — match prefix.
LAUNCHLAB_PROGRAM_PREFIX = "LanMV9sAd"

# BondingCurve extended layout (pump-public-docs / EXP-007c test fixture).
QUOTE_MINT_OFFSET = 83
INVALID_MINT_PREFIXES = ("11111111111111111111111111111111",)


def is_plausible_mint(mint_b58: str | None) -> bool:
    if not mint_b58 or not isinstance(mint_b58, str):
        return False
    m = mint_b58.strip()
    if len(m) < 32:
        return False
    return not any(m.startswith(p) for p in INVALID_MINT_PREFIXES)


def quote_mint_from_curve_bytes(data: bytes) -> str | None:
    if len(data) < QUOTE_MINT_OFFSET + 32:
        return None
    mint = _b58encode(data[QUOTE_MINT_OFFSET : QUOTE_MINT_OFFSET + 32])
    return mint if is_plausible_mint(mint) else None


def is_launchlab_program(program_id: str) -> bool:
    return isinstance(program_id, str) and program_id.startswith(LAUNCHLAB_PROGRAM_PREFIX)


def programs_in_transaction(tx: Mapping[str, Any]) -> set[str]:
    progs: set[str] = set()
    msg = tx.get("transaction", {})
    if isinstance(msg, dict):
        message = msg.get("message")
        if isinstance(message, dict):
            for ins in message.get("instructions") or []:
                if isinstance(ins, dict):
                    pid = ins.get("programId")
                    if isinstance(pid, str):
                        progs.add(pid)
    meta = tx.get("meta")
    if isinstance(meta, dict):
        for inner in meta.get("innerInstructions") or []:
            if not isinstance(inner, dict):
                continue
            for ins in inner.get("instructions") or []:
                if isinstance(ins, dict):
                    pid = ins.get("programId")
                    if isinstance(pid, str):
                        progs.add(pid)
    return progs


def venue_from_transaction(tx: Mapping[str, Any], *, default: str = "pump_program") -> str:
    progs = programs_in_transaction(tx)
    if any(is_launchlab_program(p) for p in progs):
        return "launchlab"
    if PUMP_PROGRAM_ID in progs:
        return "pump_program"
    return default


def instr_from_transaction_and_logs(
    tx: Mapping[str, Any],
    log_messages: Sequence[Any],
) -> str | None:
    from_logs = _instr_from_logs(log_messages)
    if from_logs:
        return from_logs
    for line in log_messages:
        if not isinstance(line, str):
            continue
        if "Instruction: InitializeWithToken2022" in line:
            return "launchlab_init"
    progs = programs_in_transaction(tx)
    if any(is_launchlab_program(p) for p in progs) and PUMP_PROGRAM_ID not in progs:
        return "launchlab_init"
    for line in log_messages:
        if not isinstance(line, str) or not line.startswith("Program data: "):
            continue
        blob = line[len("Program data: ") :].strip()
        if not blob:
            continue
        try:
            raw = base64.b64decode(blob, validate=False)
        except (ValueError, base64.binascii.Error):
            continue
        if _parse_pump_create_event(raw) is not None:
            return "create"
    return None


def transfer_checked_amounts(tx: Mapping[str, Any]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    meta = tx.get("meta")
    if not isinstance(meta, dict):
        return out
    for inner in meta.get("innerInstructions") or []:
        if not isinstance(inner, dict):
            continue
        for ins in inner.get("instructions") or []:
            if not isinstance(ins, dict):
                continue
            parsed = ins.get("parsed")
            if not isinstance(parsed, dict) or parsed.get("type") != "transferChecked":
                continue
            info = parsed.get("info")
            if not isinstance(info, dict):
                continue
            mint = info.get("mint")
            if not isinstance(mint, str) or not is_plausible_mint(mint):
                continue
            amt_raw = info.get("tokenAmount")
            amount = 0
            if isinstance(amt_raw, dict) and isinstance(amt_raw.get("amount"), str):
                try:
                    amount = int(amt_raw["amount"])
                except ValueError:
                    amount = 0
            out.append((mint, amount))
    return out


def transfer_checked_mints(tx: Mapping[str, Any]) -> list[str]:
    return [m for m, _ in transfer_checked_amounts(tx)]


def quote_from_transaction(
    tx: Mapping[str, Any],
    *,
    create_mint: str | None,
    venue: str,
) -> tuple[str, bool, str | None]:
    """Return (quote_tag, quote_verified, quote_mint) from tx when curve decode missed."""
    if venue != "launchlab":
        return "wsol_assumed", False, None
    amounts = transfer_checked_amounts(tx)
    if not amounts:
        return "wsol_assumed", False, None
    max_amt_mint = max(amounts, key=lambda x: x[1])[0]
    pool = [m for m, _ in amounts if m != max_amt_mint]
    if create_mint:
        pool = [m for m in pool if m != create_mint]
    if not pool:
        pool = [m for m, _ in amounts if m != max_amt_mint]
    if not pool:
        return "wsol_assumed", False, None
    quote_mint = Counter(pool).most_common(1)[0][0]
    quote, verified = _quote_tag_from_mint(quote_mint)
    return quote, verified, quote_mint


def classify_instr_quote_residual(
    *,
    instr: str,
    quote: str,
    quote_verified: bool,
    venue: str,
    quote_mint: str | None,
    tx_fetched: bool,
    curve_account_ok: bool,
) -> str:
    """Honest taxonomy bucket for EXP-007e diagnosis (not a regime_id token)."""
    if not tx_fetched:
        return "rpc_tx_miss"
    if instr in ("pending_rpc", "unknown_until_rpc"):
        if venue == "launchlab":
            return "instr_launchlab_unresolved"
        return "instr_log_gap"
    if venue == "launchlab" and instr == "launchlab_init":
        if not quote_verified:
            return "launchlab_quote_transfer_gap"
        return "launchlab_resolved"
    if quote == "wsol_assumed" and not quote_verified:
        if instr == "create_v2" and curve_account_ok:
            return "quote_mint_decode_gap"
        if instr == "create":
            return "legacy_create_quote_implied_gap"
        return "quote_unverified_other"
    if quote_mint and not is_plausible_mint(quote_mint):
        return "quote_mint_invalid_bytes"
    if quote_verified:
        return "resolved_ok"
    return "quote_unverified_other"
