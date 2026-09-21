"""Post-create outcome marks — schema + as-of-H join (EXP-003).

Marks are enrich rows. Never mutate sealed ingest_hot packets.
Stdlib only. No RPC, no WebSocket, no secrets.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

MARK_SCHEMA_VERSION = "observe_mark_v0"
MARK_TYPE = "outcome_mark"

MARK_SOURCES = frozenset(
    {
        "rpc_tx",
        "rpc_account_poll",
        "account_state",
        "pumpportal_ws_trade",
    }
)

# Spine-forbidden sources (DEC-003 / EXP-003 K4).
FORBIDDEN_MARK_SOURCES = frozenset(
    {
        "dexscreener",
        "birdeye",
        "debug_dexscreener",
    }
)


def parse_iso_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def price_proxy_from_mapping(row: Mapping[str, Any]) -> float | None:
    """Finite >0 proxy: explicit price_proxy, else marketCapSol / vSolInBondingCurve."""
    for key in ("price_proxy", "marketCapSol", "vSolInBondingCurve"):
        val = row.get(key)
        if val is None and isinstance(row.get("ws_payload"), dict):
            val = row["ws_payload"].get(key)
        if val is None:
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f) and f > 0:
            return f
    return None


def is_outcome_mark(row: Mapping[str, Any]) -> bool:
    return row.get("type") == MARK_TYPE and row.get("schema_version") == MARK_SCHEMA_VERSION


def mark_void_reason(row: Mapping[str, Any]) -> str | None:
    """Why a parsed object is not a usable tick. None = usable."""
    if not is_outcome_mark(row):
        return "not_outcome_mark"
    mint = row.get("mint")
    if not isinstance(mint, str) or mint in ("", "UNK"):
        return "missing_mint"
    parent = row.get("parent_signature")
    if not isinstance(parent, str) or parent in ("", "UNK"):
        return "missing_parent_signature"
    if parse_iso_ts(row.get("t_mark")) is None:
        return "missing_t_mark"
    src = row.get("source")
    if src in FORBIDDEN_MARK_SOURCES:
        return "forbidden_source"
    if src not in MARK_SOURCES:
        return "unknown_source"
    if price_proxy_from_mapping(row) is None:
        return "no_price_proxy"
    return None


def is_as_of_tick(t_decision: datetime, t_mark: datetime, horizon_s: float) -> bool:
    """True iff T < t_mark <= T+H (entry at T excluded; post-horizon is a leak)."""
    if horizon_s < 0:
        return False
    target = t_decision + timedelta(seconds=horizon_s)
    return t_decision < t_mark <= target


def select_last_as_of(
    marks: Sequence[tuple[datetime, float]],
    t_decision: datetime,
    horizon_s: float,
) -> tuple[datetime, float] | None:
    """Last tick with T < t_mark <= T+H. Sorted or unsorted input is fine."""
    chosen: tuple[datetime, float] | None = None
    for t_mark, price in marks:
        if is_as_of_tick(t_decision, t_mark, horizon_s):
            if chosen is None or t_mark >= chosen[0]:
                chosen = (t_mark, price)
    return chosen


def parse_mark_tick(row: Mapping[str, Any]) -> tuple[str, datetime, float] | None:
    """Return (mint, t_mark, price) or None if void."""
    if mark_void_reason(row) is not None:
        return None
    mint = row["mint"]
    t_mark = parse_iso_ts(row.get("t_mark"))
    price = price_proxy_from_mapping(row)
    if not isinstance(mint, str) or t_mark is None or price is None:
        return None
    return mint, t_mark, price
