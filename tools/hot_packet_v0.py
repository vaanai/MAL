"""hot-packet v0 fixture validator and example emitter.

Proposed paper contract for the capped decode object LAYA would read.
No RPC, no observe client, no JSONL densify, no enum production lock.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "hot_packet_v0"
PACKET_TYPE = "hot_packet"
CLOCK_SOURCE = "dec005_draft_pr8_unmerged"
BYTE_CAP = 8192
MAX_GRAPH_SLOTS = 5

REGIME_KEYS: tuple[str, ...] = (
    "env",
    "source",
    "stream",
    "stage",
    "quote",
    "commitment",
    "venue",
    "instr",
    "fee",
    "market",
)

# Production vocabulary is REGIME-ENUM v0 + DEC-004 + the sealed observe client.
# Proposed tags are admitted on enriched overlay only and must lock as "proposed".
PRODUCTION_VALUES: dict[str, frozenset[str]] = {
    "env": frozenset({"mainnet"}),
    "source": frozenset({"pumpportal_ws"}),
    "stream": frozenset({"subscribeNewToken", "subscribeMigration"}),
    "stage": frozenset(
        {"bonding", "bonding_complete", "migrating", "pumpswap", "legacy_raydium", "UNK"}
    ),
    "quote": frozenset({"wsol", "wsol_assumed", "usdc", "other"}),
    "commitment": frozenset({"processed"}),
    "venue": frozenset({"pump_program"}),
    "instr": frozenset({"create", "create_v2", "pending_rpc", "unknown_until_rpc"}),
    "fee": frozenset({"global_100bps", "creator_dynamic", "pumpswap_pool", "unverified"}),
    "market": frozenset(
        {"bonding_curve", "pumpswap", "pumpswap_pending", "external", "legacy_raydium", "UNK"}
    ),
}

# Never production-lock. Enriched overlay only.
PROPOSED_VALUES: dict[str, frozenset[str]] = {
    "fee": frozenset({"global_95bps"}),
    "instr": frozenset({"launchlab_init"}),
    "venue": frozenset({"launchlab"}),
    "market": frozenset({"launchlab_pool"}),
}

# Capped Graph scalars already named in EXP-004 / GRAPH-DISCOVERY G2.
# burst_count (H-G2 kill), ordinal buckets (NH-G1a kill), NH-Index, NH-G3a are absent.
SLOT_LAW: dict[str, dict[str, str]] = {
    "creator_age_seconds": {
        "hypothesis_status": "directional_watch_not_promoted",
        "value_kind": "number",
    },
    "prior_mint_count": {
        "hypothesis_status": "directional_watch_not_promoted",
        "value_kind": "integer",
    },
    "min_gap_seconds": {
        "hypothesis_status": "directional_watch_not_promoted",
        "value_kind": "number",
    },
    "creator_buyer_recurrence_weak": {
        "hypothesis_status": "directional_watch_not_promoted",
        "value_kind": "boolean",
    },
    "early_wallet_dt_min_seconds": {
        "hypothesis_status": "incomplete_do_not_densify",
        "value_kind": "null_only",
    },
}

FORBIDDEN_SLOT_FRAGMENTS: tuple[str, ...] = (
    "burst",
    "ordinal",
    "bucket",
    "nh_index",
    "nh_g3a",
    "funding",
    "multi_hop",
    "social",
    "x_",
)

FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "ws_payload",
        "dexscreener",
        "birdeye",
        "api_key",
        "private_key",
        "secret",
        "twitter",
        "x_account",
        "outcome_mark",
        "mean_return",
        "lift_vs_random",
        "burst_count",
    }
)

L1_KEYS: tuple[str, ...] = (
    "signature",
    "mint",
    "txType",
    "stream",
    "source",
    "commitment",
    "stage",
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
    "pool",
)

L1_NULLABLE_STRINGS: frozenset[str] = frozenset(
    {"traderPublicKey", "name", "symbol", "uri", "bondingCurveKey"}
)
L1_NULLABLE_NUMBERS: frozenset[str] = frozenset(
    {
        "initialBuy",
        "solAmount",
        "vTokensInBondingCurve",
        "vSolInBondingCurve",
        "marketCapSol",
    }
)

HONESTY_FALSE_KEYS: tuple[str, ...] = (
    "continuous_observe_wiring",
    "encoder_promoted",
    "enum_production_lock",
    "discovery_promoted",
    "graph_lane_revived",
    "invented_lift",
)

T_WS_EXAMPLE = "2026-09-20T12:00:00.000+00:00"
SOURCE_DAY_EXAMPLE = "2026-09-20"


def _honesty() -> dict[str, Any]:
    body: dict[str, Any] = {"registration": "proposed"}
    for key in HONESTY_FALSE_KEYS:
        body[key] = False
    return body


def _clocks(*, t_ws: str, t_event: str | None, merged: bool) -> dict[str, Any]:
    t_decision = t_event if t_event else t_ws
    return {
        "t_ws": t_ws,
        "t_event": t_event,
        "T_decision": t_decision,
        "t_precompute_as_of": t_decision,
        "precompute_features_merged": merged,
        "clock_source": CLOCK_SOURCE,
    }


def _regime_id(components: Mapping[str, str]) -> str:
    return "|".join(f"{key}={components[key]}" for key in REGIME_KEYS)


def _lock_for(components: Mapping[str, str]) -> dict[str, str]:
    lock: dict[str, str] = {}
    for key in REGIME_KEYS:
        value = components[key]
        if value in PROPOSED_VALUES.get(key, frozenset()):
            lock[key] = "proposed"
        else:
            lock[key] = "production"
    return lock


def _knowable_from_components(
    components: Mapping[str, str],
    *,
    quote_verified: bool,
    venue_verified: bool,
) -> dict[str, Any]:
    return {
        "quote": components["quote"],
        "quote_verified": quote_verified,
        "instr": components["instr"],
        "fee": components["fee"],
        "venue": components["venue"],
        "venue_verified": venue_verified,
        "creator_verified": False,
        "reserves_source": "ws",
    }


def _l1_create(
    *,
    signature: str,
    mint: str,
    components: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "signature": signature,
        "mint": mint,
        "txType": "create",
        "stream": components["stream"],
        "source": components["source"],
        "commitment": components["commitment"],
        "stage": components["stage"],
        "traderPublicKey": "TraderExampleWeakWs",
        "name": "Example",
        "symbol": "EX",
        "uri": "https://example.invalid/meta.json",
        "bondingCurveKey": "CurveExample",
        "initialBuy": 1000.0,
        "solAmount": 0.1,
        "vTokensInBondingCurve": 1000000.0,
        "vSolInBondingCurve": 30.0,
        "marketCapSol": 30.0,
        "pool": None,
    }


def _packet(
    *,
    signature: str,
    mint: str,
    components: dict[str, str],
    overlay: str,
    quote_verified: bool,
    venue_verified: bool,
    graph_cold: bool,
    graph_slots: list[Any] | None,
    merged: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "type": PACKET_TYPE,
        "paper_only": True,
        "byte_cap": BYTE_CAP,
        "l1_spine": _l1_create(signature=signature, mint=mint, components=components),
        "regime": {
            "regime_id": _regime_id(components),
            "components": dict(components),
            "component_lock": _lock_for(components),
        },
        "knowable_at_t": _knowable_from_components(
            components,
            quote_verified=quote_verified,
            venue_verified=venue_verified,
        ),
        "graph": {
            "cold": graph_cold,
            "max_slots": MAX_GRAPH_SLOTS,
            "lane_policy": "exp004_capped_scalars_only",
            "slots": graph_slots,
        },
        "clocks": _clocks(t_ws=T_WS_EXAMPLE, t_event=None, merged=merged),
        "delta_exec": {
            "defined_in": CLOCK_SOURCE,
            "value": None,
            "reason": "paper_packet_has_no_fill",
        },
        "provenance": {
            "spine": "sealed_jsonl",
            "source_day": SOURCE_DAY_EXAMPLE,
            "parent_type": "ingest_hot",
            "parent_schema": "observe_hot_v0",
            "parent_signature": signature,
            "mint": mint,
            "fixture_origin": "synthetic",
            "supersedes": None,
            "day_aligned": True,
        },
        "dual_read": {
            "overlay": overlay,
            "sealed_book_rpc_slice": "incomplete",
            "enrich_type": None if overlay == "sealed" else "regime_enrich",
        },
        "honesty": _honesty(),
    }


def example_sealed_cold() -> dict[str, Any]:
    """Sealed bonding create. Graph cold. Fee stays unverified (full-book dual read)."""
    components = {
        "env": "mainnet",
        "source": "pumpportal_ws",
        "stream": "subscribeNewToken",
        "stage": "bonding",
        "quote": "wsol_assumed",
        "commitment": "processed",
        "venue": "pump_program",
        "instr": "pending_rpc",
        "fee": "unverified",
        "market": "bonding_curve",
    }
    return _packet(
        signature="SigExampleSealed",
        mint="MintExampleSealed",
        components=components,
        overlay="sealed",
        quote_verified=False,
        venue_verified=False,
        graph_cold=True,
        graph_slots=None,
        merged=False,
    )


def example_enriched_global_95bps() -> dict[str, Any]:
    """Synthetic enriched overlay. global_95bps stays Proposed. Graph stays cold."""
    components = {
        "env": "mainnet",
        "source": "pumpportal_ws",
        "stream": "subscribeNewToken",
        "stage": "bonding",
        "quote": "wsol",
        "commitment": "processed",
        "venue": "pump_program",
        "instr": "create_v2",
        "fee": "global_95bps",
        "market": "bonding_curve",
    }
    return _packet(
        signature="SigExampleEnrichedFee",
        mint="MintExampleEnrichedFee",
        components=components,
        overlay="enriched",
        quote_verified=True,
        venue_verified=True,
        graph_cold=True,
        graph_slots=None,
        merged=False,
    )


def example_enriched_launchlab() -> dict[str, Any]:
    """Synthetic LaunchLab overlay. launchlab_* tags stay Proposed. Fee not invented."""
    components = {
        "env": "mainnet",
        "source": "pumpportal_ws",
        "stream": "subscribeNewToken",
        "stage": "bonding",
        "quote": "wsol",
        "commitment": "processed",
        "venue": "launchlab",
        "instr": "launchlab_init",
        "fee": "unverified",
        "market": "launchlab_pool",
    }
    return _packet(
        signature="SigExampleLaunchlab",
        mint="MintExampleLaunchlab",
        components=components,
        overlay="enriched",
        quote_verified=True,
        venue_verified=True,
        graph_cold=True,
        graph_slots=None,
        merged=False,
    )


def example_graph_slots_nullable() -> dict[str, Any]:
    """Sealed create with the capped slot array. One weak scalar; killed lanes absent."""
    components = {
        "env": "mainnet",
        "source": "pumpportal_ws",
        "stream": "subscribeNewToken",
        "stage": "bonding",
        "quote": "wsol_assumed",
        "commitment": "processed",
        "venue": "pump_program",
        "instr": "pending_rpc",
        "fee": "unverified",
        "market": "bonding_curve",
    }
    t_as_of = T_WS_EXAMPLE
    slots: list[Any] = [
        {
            "slot_id": "prior_mint_count",
            "value": 2,
            "evidence_tier": "weak_ws",
            "hypothesis_status": "directional_watch_not_promoted",
            "t_precompute_as_of": t_as_of,
        },
        {
            "slot_id": "early_wallet_dt_min_seconds",
            "value": None,
            "evidence_tier": "weak_ws",
            "hypothesis_status": "incomplete_do_not_densify",
            "t_precompute_as_of": t_as_of,
        },
        None,
        None,
        None,
    ]
    packet = _packet(
        signature="SigExampleGraphSlots",
        mint="MintExampleGraphSlots",
        components=components,
        overlay="sealed",
        quote_verified=False,
        venue_verified=False,
        graph_cold=False,
        graph_slots=slots,
        merged=True,
    )
    return packet


EXAMPLES: dict[str, Any] = {
    "sealed-cold": example_sealed_cold,
    "enriched-fee": example_enriched_global_95bps,
    "enriched-launchlab": example_enriched_launchlab,
    "graph-slots": example_graph_slots_nullable,
}


def _err(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _exact_keys(errors: list[str], obj: Mapping[str, Any], path: str, expected: set[str]) -> None:
    if set(obj.keys()) != expected:
        _err(errors, path, f"keys must be exactly {sorted(expected)}")


def _expect_const(errors: list[str], obj: Mapping[str, Any], path: str, key: str, expected: Any) -> None:
    if key not in obj:
        _err(errors, f"{path}.{key}", "missing")
        return
    if obj[key] != expected:
        _err(errors, f"{path}.{key}", f"expected {expected!r}")


def _reject_forbidden_keys(errors: list[str], obj: Any, path: str) -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if key.lower() in FORBIDDEN_KEYS:
                _err(errors, f"{path}.{key}", "forbidden on hot-packet v0")
            _reject_forbidden_keys(errors, value, f"{path}.{key}")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _reject_forbidden_keys(errors, value, f"{path}[{index}]")


def _check_regime_id(errors: list[str], regime_id: str, components: Mapping[str, Any]) -> None:
    parts = regime_id.split("|")
    if len(parts) != len(REGIME_KEYS):
        _err(errors, "regime.regime_id", f"expected {len(REGIME_KEYS)} pipe tokens")
        return
    for index, part in enumerate(parts):
        key, sep, value = part.partition("=")
        expect = REGIME_KEYS[index]
        if not sep or key != expect or value == "" or "=" in value or " " in part:
            _err(errors, "regime.regime_id", f"token {index} must be {expect}=<value> with no spaces")
            continue
        if components.get(key) != value:
            _err(errors, f"regime.components.{key}", "disagrees with regime_id")


def _check_component_lock(errors: list[str], components: Mapping[str, Any], lock: Mapping[str, Any], overlay: str) -> None:
    if set(lock.keys()) != set(REGIME_KEYS):
        _err(errors, "regime.component_lock", f"keys must be exactly {list(REGIME_KEYS)}")
    for key in REGIME_KEYS:
        value = components.get(key)
        if not isinstance(value, str):
            _err(errors, f"regime.components.{key}", "must be a string")
            continue
        proposed = value in PROPOSED_VALUES.get(key, frozenset())
        production = value in PRODUCTION_VALUES.get(key, frozenset())
        if proposed and production:
            _err(errors, f"regime.components.{key}", "tag cannot be both production and proposed")
            continue
        if not proposed and not production:
            _err(errors, f"regime.components.{key}", f"unknown tag {value!r}")
            continue
        expect_lock = "proposed" if proposed else "production"
        if lock.get(key) != expect_lock:
            _err(
                errors,
                f"regime.component_lock.{key}",
                f"{value!r} must lock as {expect_lock}",
            )
        if proposed and overlay != "enriched":
            _err(
                errors,
                f"regime.components.{key}",
                f"{value!r} is Proposed and allowed only on dual_read.overlay=enriched",
            )


def _check_l1(errors: list[str], l1: Mapping[str, Any], components: Mapping[str, Any]) -> None:
    _exact_keys(errors, l1, "l1_spine", set(L1_KEYS))
    for key in ("signature", "mint", "txType", "stream", "source", "commitment", "stage"):
        value = l1.get(key)
        if not isinstance(value, str) or not value.strip():
            _err(errors, f"l1_spine.{key}", "must be a non-empty string")
    if l1.get("txType") != "create":
        _err(errors, "l1_spine.txType", "v0 packet is a bonding-create spine")
    if l1.get("stream") != "subscribeNewToken":
        _err(errors, "l1_spine.stream", "v0 packet is subscribeNewToken create")
    for key in ("stream", "source", "commitment", "stage"):
        if l1.get(key) != components.get(key):
            _err(errors, f"l1_spine.{key}", "must match regime.components")
    for key in L1_NULLABLE_STRINGS:
        value = l1.get(key)
        if value is not None and not isinstance(value, str):
            _err(errors, f"l1_spine.{key}", "must be a string or null")
    for key in L1_NULLABLE_NUMBERS:
        value = l1.get(key)
        if value is None:
            continue
        if not _is_number(value) or float(value) < 0:
            _err(errors, f"l1_spine.{key}", "must be a finite number >= 0 or null")
    if l1.get("stage") == "bonding" and l1.get("pool") is not None:
        _err(errors, "l1_spine.pool", "bonding create must not carry a pool (no migration inference)")
    elif l1.get("pool") is not None and not isinstance(l1.get("pool"), str):
        _err(errors, "l1_spine.pool", "must be a string or null")


def _check_knowable(errors: list[str], kat: Mapping[str, Any], components: Mapping[str, Any], overlay: str) -> None:
    _exact_keys(
        errors,
        kat,
        "knowable_at_t",
        {
            "quote",
            "quote_verified",
            "instr",
            "fee",
            "venue",
            "venue_verified",
            "creator_verified",
            "reserves_source",
        },
    )
    for key in ("quote", "instr", "fee", "venue"):
        if kat.get(key) != components.get(key):
            _err(errors, f"knowable_at_t.{key}", "must match regime.components")
    if kat.get("creator_verified") is not False:
        _err(errors, "knowable_at_t.creator_verified", "v0 does not upgrade creator provenance")
    if kat.get("reserves_source") not in ("ws", "UNK"):
        _err(errors, "knowable_at_t.reserves_source", "must be ws or UNK (no in-place RPC reserve rewrite)")
    for flag in ("quote_verified", "venue_verified"):
        if not isinstance(kat.get(flag), bool):
            _err(errors, f"knowable_at_t.{flag}", "must be boolean")
        elif kat.get(flag) is True and overlay != "enriched":
            _err(errors, f"knowable_at_t.{flag}", "true only on enriched overlay (do not flip the sealed row)")


def _check_clocks(errors: list[str], clocks: Mapping[str, Any], *, graph_cold: bool) -> None:
    _exact_keys(
        errors,
        clocks,
        "clocks",
        {
            "t_ws",
            "t_event",
            "T_decision",
            "t_precompute_as_of",
            "precompute_features_merged",
            "clock_source",
        },
    )
    _expect_const(errors, clocks, "clocks", "clock_source", CLOCK_SOURCE)
    t_ws = clocks.get("t_ws")
    t_event = clocks.get("t_event")
    t_decision = clocks.get("T_decision")
    t_pre = clocks.get("t_precompute_as_of")
    if not isinstance(t_ws, str) or _parse_iso(t_ws) is None:
        _err(errors, "clocks.t_ws", "must be timezone-aware ISO-8601")
        return None
    if t_event is not None and (not isinstance(t_event, str) or _parse_iso(t_event) is None):
        _err(errors, "clocks.t_event", "must be timezone-aware ISO-8601 or null")
    expect_decision = t_event if isinstance(t_event, str) else t_ws
    if t_decision != expect_decision:
        _err(
            errors,
            "clocks.T_decision",
            "must equal t_event when t_event is set, else t_ws (DEC-005 draft; not a later enrich clock)",
        )
    parsed_decision = _parse_iso(t_decision) if isinstance(t_decision, str) else None
    parsed_pre = _parse_iso(t_pre) if isinstance(t_pre, str) else None
    if parsed_pre is None or parsed_decision is None:
        _err(errors, "clocks.t_precompute_as_of", "must be timezone-aware ISO-8601")
    elif parsed_pre > parsed_decision:
        _err(errors, "clocks.t_precompute_as_of", "must be <= T_decision")
    merged = clocks.get("precompute_features_merged")
    if merged is not (not graph_cold):
        _err(errors, "clocks.precompute_features_merged", "must be false when graph.cold and true otherwise")
    if graph_cold and t_pre != t_decision:
        _err(errors, "clocks.t_precompute_as_of", "Graph-cold packet stamps precompute as-of at T_decision")
    return None


def _slot_value_ok(kind: str, value: Any) -> bool:
    if kind == "null_only":
        return value is None
    if value is None:
        return True
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if kind == "number":
        return _is_number(value) and float(value) >= 0
    return False


def _check_graph(errors: list[str], graph: Mapping[str, Any], clocks: Mapping[str, Any]) -> bool:
    _exact_keys(errors, graph, "graph", {"cold", "max_slots", "lane_policy", "slots"})
    _expect_const(errors, graph, "graph", "max_slots", MAX_GRAPH_SLOTS)
    _expect_const(errors, graph, "graph", "lane_policy", "exp004_capped_scalars_only")
    slots = graph.get("slots")
    cold = graph.get("cold")
    if not isinstance(cold, bool):
        _err(errors, "graph.cold", "must be boolean")
        cold = True
    if slots is not None and not isinstance(slots, list):
        _err(errors, "graph.slots", "must be null or an array")
        return bool(cold)
    if isinstance(slots, list) and len(slots) > MAX_GRAPH_SLOTS:
        _err(errors, "graph.slots", f"cap is {MAX_GRAPH_SLOTS}")
    seen: set[str] = set()
    filled = False
    if isinstance(slots, list):
        for index, slot in enumerate(slots):
            if slot is None:
                continue
            path = f"graph.slots[{index}]"
            if not isinstance(slot, Mapping):
                _err(errors, path, "must be an object or null")
                continue
            _exact_keys(
                errors,
                slot,
                path,
                {"slot_id", "value", "evidence_tier", "hypothesis_status", "t_precompute_as_of"},
            )
            slot_id = slot.get("slot_id")
            if not isinstance(slot_id, str):
                _err(errors, f"{path}.slot_id", "must be a string")
                continue
            lowered = slot_id.lower()
            if any(fragment in lowered for fragment in FORBIDDEN_SLOT_FRAGMENTS):
                _err(errors, f"{path}.slot_id", f"parked or out-of-cap graph lane ({slot_id})")
                continue
            law = SLOT_LAW.get(slot_id)
            if law is None:
                _err(errors, f"{path}.slot_id", "not in the capped allowlist")
                continue
            if slot_id in seen:
                _err(errors, f"{path}.slot_id", "duplicate")
            seen.add(slot_id)
            if slot.get("hypothesis_status") != law["hypothesis_status"]:
                _err(
                    errors,
                    f"{path}.hypothesis_status",
                    f"expected {law['hypothesis_status']}",
                )
            if slot.get("evidence_tier") != "weak_ws":
                _err(errors, f"{path}.evidence_tier", "v0 graph evidence is weak_ws only")
            if not _slot_value_ok(law["value_kind"], slot.get("value")):
                if law["value_kind"] == "null_only":
                    _err(errors, f"{path}.value", "H-G4 stays null — do not densify early-wallet")
                else:
                    _err(errors, f"{path}.value", f"expected {law['value_kind']} or null")
            if slot.get("value") is not None:
                filled = True
            if slot.get("t_precompute_as_of") != clocks.get("t_precompute_as_of"):
                _err(errors, f"{path}.t_precompute_as_of", "must match clocks.t_precompute_as_of")
    if cold is filled:
        _err(errors, "graph.cold", "true only when every slot is null or slots is null/empty")
    return bool(cold) if not isinstance(graph.get("cold"), bool) else (not filled)


def validate_packet(packet: Any) -> list[str]:
    """Return human-readable errors. Empty list means the packet matches hot-packet v0."""
    errors: list[str] = []
    if not isinstance(packet, Mapping):
        return ["packet: must be a JSON object"]
    _reject_forbidden_keys(errors, packet, "packet")
    try:
        encoded = json.dumps(packet, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return [f"packet: not JSON-serializable ({exc})"]
    if len(encoded) > BYTE_CAP:
        _err(errors, "packet", f"compact UTF-8 size {len(encoded)} exceeds byte_cap {BYTE_CAP}")

    _expect_const(errors, packet, "packet", "schema_version", SCHEMA_VERSION)
    _expect_const(errors, packet, "packet", "type", PACKET_TYPE)
    _expect_const(errors, packet, "packet", "paper_only", True)
    _expect_const(errors, packet, "packet", "byte_cap", BYTE_CAP)

    allowed_top = {
        "schema_version",
        "type",
        "paper_only",
        "byte_cap",
        "l1_spine",
        "regime",
        "knowable_at_t",
        "graph",
        "clocks",
        "delta_exec",
        "provenance",
        "dual_read",
        "honesty",
    }
    extra_top = [key for key in packet.keys() if key not in allowed_top]
    if extra_top:
        _err(errors, "packet", f"extra keys {extra_top}")

    l1 = packet.get("l1_spine")
    regime = packet.get("regime")
    kat = packet.get("knowable_at_t")
    graph = packet.get("graph")
    clocks = packet.get("clocks")
    provenance = packet.get("provenance")
    dual = packet.get("dual_read")
    honesty = packet.get("honesty")
    delta = packet.get("delta_exec")

    for name, value in (
        ("l1_spine", l1),
        ("regime", regime),
        ("knowable_at_t", kat),
        ("graph", graph),
        ("clocks", clocks),
        ("provenance", provenance),
        ("dual_read", dual),
        ("honesty", honesty),
        ("delta_exec", delta),
    ):
        if not isinstance(value, Mapping):
            _err(errors, name, "must be an object")

    if not isinstance(dual, Mapping) or not isinstance(regime, Mapping):
        return errors

    _exact_keys(
        errors,
        dual,
        "dual_read",
        {"overlay", "sealed_book_rpc_slice", "enrich_type"},
    )
    overlay = dual.get("overlay")
    if overlay not in ("sealed", "enriched"):
        _err(errors, "dual_read.overlay", "must be sealed or enriched")
        overlay = "sealed"
    _expect_const(errors, dual, "dual_read", "sealed_book_rpc_slice", "incomplete")
    if overlay == "sealed":
        _expect_const(errors, dual, "dual_read", "enrich_type", None)
    else:
        _expect_const(errors, dual, "dual_read", "enrich_type", "regime_enrich")

    components = regime.get("components")
    lock = regime.get("component_lock")
    regime_id = regime.get("regime_id")
    if isinstance(regime, Mapping):
        _exact_keys(errors, regime, "regime", {"regime_id", "components", "component_lock"})
    if not isinstance(components, Mapping) or not isinstance(lock, Mapping):
        _err(errors, "regime", "components and component_lock must be objects")
        components = {}
        lock = {}
    else:
        _exact_keys(errors, components, "regime.components", set(REGIME_KEYS))
        _exact_keys(errors, lock, "regime.component_lock", set(REGIME_KEYS))
    if not isinstance(regime_id, str):
        _err(errors, "regime.regime_id", "must be a string")
    else:
        _check_regime_id(errors, regime_id, components)
    _check_component_lock(errors, components, lock, str(overlay))

    if isinstance(l1, Mapping):
        _check_l1(errors, l1, components)
    if isinstance(kat, Mapping):
        _check_knowable(errors, kat, components, str(overlay))

    graph_cold = True
    if isinstance(graph, Mapping) and isinstance(clocks, Mapping):
        graph_cold = _check_graph(errors, graph, clocks)
        _check_clocks(errors, clocks, graph_cold=bool(graph.get("cold")) if isinstance(graph.get("cold"), bool) else graph_cold)
    elif isinstance(clocks, Mapping):
        _check_clocks(errors, clocks, graph_cold=True)

    if isinstance(delta, Mapping):
        _exact_keys(errors, delta, "delta_exec", {"defined_in", "value", "reason"})
        _expect_const(errors, delta, "delta_exec", "defined_in", CLOCK_SOURCE)
        _expect_const(errors, delta, "delta_exec", "value", None)
        _expect_const(errors, delta, "delta_exec", "reason", "paper_packet_has_no_fill")

    if isinstance(provenance, Mapping) and isinstance(l1, Mapping) and isinstance(clocks, Mapping):
        _exact_keys(
            errors,
            provenance,
            "provenance",
            {
                "spine",
                "source_day",
                "parent_type",
                "parent_schema",
                "parent_signature",
                "mint",
                "fixture_origin",
                "supersedes",
                "day_aligned",
            },
        )
        _expect_const(errors, provenance, "provenance", "spine", "sealed_jsonl")
        _expect_const(errors, provenance, "provenance", "parent_type", "ingest_hot")
        _expect_const(errors, provenance, "provenance", "parent_schema", "observe_hot_v0")
        _expect_const(errors, provenance, "provenance", "supersedes", None)
        _expect_const(errors, provenance, "provenance", "day_aligned", True)
        if provenance.get("fixture_origin") not in ("synthetic", "sealed_row_projection"):
            _err(errors, "provenance.fixture_origin", "must be synthetic or sealed_row_projection")
        if provenance.get("parent_signature") != l1.get("signature"):
            _err(errors, "provenance.parent_signature", "must match l1_spine.signature")
        if provenance.get("mint") != l1.get("mint"):
            _err(errors, "provenance.mint", "must match l1_spine.mint")
        parsed_ws = _parse_iso(clocks["t_ws"]) if isinstance(clocks.get("t_ws"), str) else None
        source_day = provenance.get("source_day")
        if parsed_ws is None or not isinstance(source_day, str):
            _err(errors, "provenance.source_day", "must be the UTC date of clocks.t_ws")
        elif source_day != parsed_ws.date().isoformat():
            _err(errors, "provenance.source_day", "must equal the UTC calendar day of clocks.t_ws")

    if isinstance(honesty, Mapping):
        _expect_const(errors, honesty, "honesty", "registration", "proposed")
        for key in HONESTY_FALSE_KEYS:
            _expect_const(errors, honesty, "honesty", key, False)
        if set(honesty.keys()) != {"registration", *HONESTY_FALSE_KEYS}:
            _err(errors, "honesty", "unexpected keys")

    if str(overlay) == "sealed" and isinstance(components, Mapping):
        sealed_expect = {
            "fee": "unverified",
            "instr": "pending_rpc",
            "venue": "pump_program",
            "quote": "wsol_assumed",
            "market": "bonding_curve",
            "stage": "bonding",
        }
        for key, expect in sealed_expect.items():
            if components.get(key) != expect:
                _err(
                    errors,
                    f"regime.components.{key}",
                    f"sealed overlay keeps observe defaults ({expect}); resolved tags belong on enrich rows",
                )

    return errors


def load_packet(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate or emit hot-packet v0 fixtures (no RPC, no observe wiring).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    example = sub.add_parser("example", help="Print a synthetic fixture JSON object")
    example.add_argument("--which", choices=sorted(EXAMPLES), required=True)

    validate = sub.add_parser("validate", help="Validate one hot-packet JSON file")
    validate.add_argument("path", type=Path)

    args = parser.parse_args(argv)
    if args.cmd == "example":
        packet = EXAMPLES[args.which]()
        json.dump(packet, sys.stdout, indent=2)
        sys.stdout.write("\n")
        problems = validate_packet(packet)
        if problems:
            print("example failed its own validator:", file=sys.stderr)
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        return 0

    path: Path = args.path
    try:
        packet = load_packet(path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return 1
    problems = validate_packet(packet)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print(f"ok {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
