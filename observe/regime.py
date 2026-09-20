"""Regime ID and stage stamping per DEC-004 / REGIME-AT-INGEST-MATRIX."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

# WS payload keys that may carry event time when vendor sends them (inventory: often absent).
EVENT_TIME_PAYLOAD_KEYS = ("timestamp", "blockTime")

# Canonical stage vocabulary (DEC-004)
STAGES = frozenset(
    {
        "bonding",
        "bonding_complete",
        "migrating",
        "pumpswap",
        "legacy_raydium",
    }
)

STREAM_NEW_TOKEN = "subscribeNewToken"
STREAM_MIGRATION = "subscribeMigration"

# Known WS fields from PUMPPORTAL-PAYLOAD-INVENTORY (creation)
KNOWN_CREATE_FIELDS = frozenset(
    {
        "txType",
        "signature",
        "mint",
        "traderPublicKey",
        "name",
        "symbol",
        "uri",
        "bondingCurveKey",
        "initialBuy",
        "solAmount",
        "vTokensInBondingCurve",
        "vSolInBondingCurve",
        "marketCapSol",
        "timestamp",
        "blockTime",
    }
)

KNOWN_MIGRATION_FIELDS = frozenset(
    {
        "txType",
        "mint",
        "pool",
        "signature",
        "traderPublicKey",
        "timestamp",
        "blockTime",
    }
)


def extract_t_event(payload: Mapping[str, Any]) -> str | None:
    """Event time from WS payload when present; never synthesized."""
    for key in EVENT_TIME_PAYLOAD_KEYS:
        if key not in payload:
            continue
        val = payload[key]
        if val is None:
            continue
        if isinstance(val, str):
            return val
        if isinstance(val, (int, float)):
            ts = float(val)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds")
    return None


def _stage_for_event(stream: str, payload: Mapping[str, Any]) -> str:
    tx_type = payload.get("txType")
    if stream == STREAM_NEW_TOKEN:
        if tx_type == "create":
            return "bonding"
        return "UNK"
    if stream == STREAM_MIGRATION:
        if tx_type == "migration":
            return "migrating"
        return "UNK"
    return "UNK"


def _market_for_stage(stage: str) -> str:
    if stage == "bonding":
        return "bonding_curve"
    if stage in ("migrating", "bonding_complete"):
        return "pumpswap_pending"
    if stage == "pumpswap":
        return "pumpswap"
    if stage == "legacy_raydium":
        return "legacy_raydium"
    return "UNK"


def unknown_payload_keys(stream: str, payload: Mapping[str, Any]) -> list[str]:
    known = KNOWN_CREATE_FIELDS if stream == STREAM_NEW_TOKEN else KNOWN_MIGRATION_FIELDS
    return sorted(k for k in payload.keys() if k not in known)


def build_regime_id(
    *,
    stream: str,
    stage: str,
    quote: str = "wsol_assumed",
    instr: str = "pending_rpc",
    fee: str = "unverified",
    venue: str = "pump_program",
    market: str | None = None,
) -> str:
    """Pipe-separated key=value encoding (DEC-004). Order is canonical."""
    if stage not in STAGES and stage != "UNK":
        stage = "UNK"
    if market is None:
        market = _market_for_stage(stage)
    parts = [
        "env=mainnet",
        "source=pumpportal_ws",
        f"stream={stream}",
        f"stage={stage}",
        f"quote={quote}",
        "commitment=processed",
        f"venue={venue}",
        f"instr={instr}",
        f"fee={fee}",
        f"market={market}",
    ]
    return "|".join(parts)


def build_knowable_at_t(stage: str) -> dict[str, Any]:
    return {
        "quote": "wsol_assumed",
        "quote_verified": False,
        "instr": "pending_rpc",
        "fee": "unverified",
        "venue": "pump_program",
        "venue_verified": False,
        "creator_verified": False,
        "reserves_source": "ws" if stage == "bonding" else "UNK",
    }


def seal_ingest_record(
    *,
    t_ws: str,
    stream: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Build immutable hot observe row for JSONL append."""
    stage = _stage_for_event(stream, payload)
    regime_id = build_regime_id(stream=stream, stage=stage)
    record: dict[str, Any] = {
        "schema_version": "observe_hot_v0",
        "type": "ingest_hot",
        "t_ws": t_ws,
        "t_event": extract_t_event(payload),
        "stream": stream,
        "source": "pumpportal_ws",
        "commitment": "processed",
        "stage": stage,
        "regime_id": regime_id,
        "txType": payload.get("txType", "UNK"),
        "signature": payload.get("signature", "UNK"),
        "mint": payload.get("mint", "UNK"),
        "knowable_at_t": build_knowable_at_t(stage),
        "ws_payload": dict(payload),
        "ws_fields_unknown": unknown_payload_keys(stream, payload),
    }
    if stream == STREAM_NEW_TOKEN:
        for key in (
            "traderPublicKey",
            "name",
            "symbol",
            "uri",
            "bondingCurveKey",
            "vTokensInBondingCurve",
            "vSolInBondingCurve",
            "marketCapSol",
        ):
            if key in payload:
                record[key] = payload[key]
    if stream == STREAM_MIGRATION and "pool" in payload:
        record["pool"] = payload["pool"]
    return record
