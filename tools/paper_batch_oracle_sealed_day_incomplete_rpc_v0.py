"""paper-batch-oracle-sealed-day-incomplete-rpc-v0.

Proposed paper batch. A day-aligned sealed observe JSONL is projected to
hot_packet_v0 (sealed overlay, graph cold, RPC slice incomplete), stamped by
paper_evaluate_hot_packet_v0, and counted by paper_scoreboard_sealed_fixture_v0.

No RPC. No observe client. No measure exit. No closed-book claim.
Checked-in fixtures are synthetic calendar labels, not a host extract.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.hot_packet_v0 import (
    BYTE_CAP,
    CLOCK_SOURCE,
    MAX_GRAPH_SLOTS,
    PACKET_TYPE,
    SCHEMA_VERSION as HOT_PACKET_SCHEMA,
    _honesty as _packet_honesty,
    _lock_for,
    _parse_iso,
    _regime_id,
    validate_packet,
)
from tools.paper_evaluate_hot_packet_v0 import stamp_from_packet
from tools.paper_scoreboard_sealed_fixture_v0 import (
    DELTA_JOIN_REASON,
    EXPECTATION_KIND,
    HORIZON_JOIN_STATUS,
    SCHEMA_VERSION as SCOREBOARD_SCHEMA,
    SOFT_WATCH_ITEMS as SCOREBOARD_SOFT_WATCH_ITEMS,
    score_stamps,
    validate_expectation,
)

SCHEMA_VERSION = "paper_batch_oracle_sealed_day_incomplete_rpc_v0"
BATCH_TYPE = "paper_batch"
BATCH_ID = "paper-batch-oracle-sealed-day-incomplete-rpc-v0"
PARENT_SCHEMA = "observe_hot_v0"
INCOMPLETE_REASON = "batch_projection_does_not_close_the_sealed_book"
SKIP_NOT_CREATE = "not_create_row"
FIXTURE_ORIGINS: tuple[str, ...] = ("synthetic", "sealed_row_projection")
EXAMPLE_DAYS: tuple[str, ...] = ("2026-09-20", "2026-09-21")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JSONL_NAME_RE = re.compile(r"^observe-(\d{4}-\d{2}-\d{2})\.jsonl$")

PIPELINE: tuple[str, ...] = (
    "sealed_observe_jsonl",
    "hot_packet_v0",
    "paper_evaluate_hot_packet_v0",
    "paper_scoreboard_sealed_fixture_v0",
)

SOFT_WATCH_SOURCE = (
    "hot_packet_v0_soft_gate_pr49_paper_evaluate_pr50_scoreboard_pr51"
)
INHERITED_FROM: tuple[str, ...] = (
    "hot_packet_v0_soft_gate_pr49",
    "paper_evaluate_hot_packet_v0_pr50",
    "paper_scoreboard_sealed_fixture_v0_pr51",
)
LOCAL_SOFT_WATCH_ITEMS: tuple[str, ...] = (
    "host_jsonl_not_readable_from_ci",
    "counts_are_not_returns",
    "graph_stays_cold_on_this_stamp",
)
SOFT_WATCH_ITEMS: tuple[str, ...] = SCOREBOARD_SOFT_WATCH_ITEMS + LOCAL_SOFT_WATCH_ITEMS

HONESTY_FALSE: tuple[str, ...] = (
    "scored_oracle_measure",
    "executed_host_sealed_book",
    "continuous_observe_wiring",
    "encoder_promoted",
    "enum_production_lock",
    "discovery_promoted",
    "graph_lane_revived",
    "invented_lift",
    "invented_ev",
    "claims_alpha",
    "exp002c_retuned",
    "live_capital",
    "trading_keys",
    "pumpportal_trade_api",
)

FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "dexscreener",
        "birdeye",
        "api_key",
        "private_key",
        "secret",
        "twitter",
        "x_account",
        "outcome_mark",
        "mean_return",
        "mean",
        "lift_vs_random",
        "burst_count",
        "expected_value",
        "alpha",
        "cohort_alpha",
        "ev",
        "verdict",
        "fail_no_lift",
        "exit_code",
    }
)
SOURCE_ROW_ONLY_KEYS: frozenset[str] = frozenset({"ws_payload", "price_proxy"})

SEALED_COMPONENTS: dict[str, str] = {
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
SEALED_REGIME_ID = _regime_id(SEALED_COMPONENTS)
SEALED_KNOWABLE: dict[str, Any] = {
    "quote": "wsol_assumed",
    "quote_verified": False,
    "instr": "pending_rpc",
    "fee": "unverified",
    "venue": "pump_program",
    "venue_verified": False,
    "creator_verified": False,
    "reserves_source": "ws",
}

L1_NULLABLE_STRINGS: tuple[str, ...] = (
    "traderPublicKey",
    "name",
    "symbol",
    "uri",
    "bondingCurveKey",
)
L1_NULLABLE_NUMBERS: tuple[str, ...] = (
    "initialBuy",
    "solAmount",
    "vTokensInBondingCurve",
    "vSolInBondingCurve",
    "marketCapSol",
)

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "paper_batch_oracle_sealed_day_incomplete_rpc_v0"
)


def _err(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def _share(numerator: int, denominator: int) -> dict[str, int]:
    return {"numerator": numerator, "denominator": denominator}


def _honesty() -> dict[str, Any]:
    body: dict[str, Any] = {"registration": "proposed"}
    for key in HONESTY_FALSE:
        body[key] = False
    return body


def _is_create_row(row: Mapping[str, Any]) -> bool:
    return (
        row.get("type") == "ingest_hot"
        and row.get("txType") == "create"
        and row.get("stream") == "subscribeNewToken"
    )


def _copied_field(row: Mapping[str, Any], key: str) -> Any:
    if key in row:
        return row[key]
    payload = row.get("ws_payload")
    if isinstance(payload, Mapping) and key in payload:
        return payload[key]
    return None


def _project_create(
    row: Mapping[str, Any],
    *,
    day: str,
    fixture_origin: str,
    path: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Project one sealed bonding create. Graph stays cold. Overlay stays sealed."""
    errors: list[str] = []
    if row.get("schema_version") != PARENT_SCHEMA:
        _err(errors, f"{path}.schema_version", f"create parent schema must be {PARENT_SCHEMA}")
    if row.get("regime_id") != SEALED_REGIME_ID:
        _err(
            errors,
            f"{path}.regime_id",
            "sealed observe defaults only; this batch does not write an enriched overlay",
        )
    if row.get("knowable_at_t") != SEALED_KNOWABLE:
        _err(
            errors,
            f"{path}.knowable_at_t",
            "sealed observe defaults only; verified flags stay false",
        )
    for key, expected in (
        ("source", "pumpportal_ws"),
        ("commitment", "processed"),
        ("stage", "bonding"),
        ("txType", "create"),
        ("stream", "subscribeNewToken"),
    ):
        if row.get(key) != expected:
            _err(errors, f"{path}.{key}", f"must be {expected}")
    if "t_event" not in row:
        _err(errors, f"{path}.t_event", "required; null when the sealed row has none")
    t_event = row.get("t_event")
    if t_event is not None and (not isinstance(t_event, str) or _parse_iso(t_event) is None):
        _err(errors, f"{path}.t_event", "must be timezone-aware ISO-8601 or null")
    t_ws = row.get("t_ws")
    parsed_ws = _parse_iso(t_ws) if isinstance(t_ws, str) else None
    if parsed_ws is None:
        _err(errors, f"{path}.t_ws", "must be timezone-aware ISO-8601")
    elif parsed_ws.date().isoformat() != day:
        _err(
            errors,
            f"{path}.t_ws",
            f"UTC calendar day must equal the JSONL day {day}",
        )
    signature = row.get("signature")
    mint = row.get("mint")
    if not isinstance(signature, str) or signature.strip() == "":
        _err(errors, f"{path}.signature", "must be a non-empty string")
    if not isinstance(mint, str) or mint.strip() == "":
        _err(errors, f"{path}.mint", "must be a non-empty string; UNK is an identity")
    if row.get("pool") is not None:
        _err(errors, f"{path}.pool", "bonding create must not carry a pool")
    if errors or parsed_ws is None or not isinstance(signature, str) or not isinstance(mint, str):
        return None, errors

    t_decision = t_event if isinstance(t_event, str) else t_ws
    l1: dict[str, Any] = {
        "signature": signature,
        "mint": mint,
        "txType": "create",
        "stream": "subscribeNewToken",
        "source": "pumpportal_ws",
        "commitment": "processed",
        "stage": "bonding",
    }
    for key in L1_NULLABLE_STRINGS:
        l1[key] = _copied_field(row, key)
    for key in L1_NULLABLE_NUMBERS:
        l1[key] = _copied_field(row, key)
    l1["pool"] = None

    packet: dict[str, Any] = {
        "schema_version": HOT_PACKET_SCHEMA,
        "type": PACKET_TYPE,
        "paper_only": True,
        "byte_cap": BYTE_CAP,
        "l1_spine": l1,
        "regime": {
            "regime_id": SEALED_REGIME_ID,
            "components": dict(SEALED_COMPONENTS),
            "component_lock": _lock_for(SEALED_COMPONENTS),
        },
        "knowable_at_t": dict(SEALED_KNOWABLE),
        "graph": {
            "cold": True,
            "max_slots": MAX_GRAPH_SLOTS,
            "lane_policy": "exp004_capped_scalars_only",
            "slots": None,
        },
        "clocks": {
            "t_ws": t_ws,
            "t_event": t_event,
            "T_decision": t_decision,
            "t_precompute_as_of": t_decision,
            "precompute_features_merged": False,
            "clock_source": CLOCK_SOURCE,
        },
        "delta_exec": {
            "defined_in": CLOCK_SOURCE,
            "value": None,
            "reason": "paper_packet_has_no_fill",
        },
        "provenance": {
            "spine": "sealed_jsonl",
            "source_day": day,
            "parent_type": "ingest_hot",
            "parent_schema": PARENT_SCHEMA,
            "parent_signature": signature,
            "mint": mint,
            "fixture_origin": fixture_origin,
            "supersedes": None,
            "day_aligned": True,
        },
        "dual_read": {
            "overlay": "sealed",
            "sealed_book_rpc_slice": "incomplete",
            "enrich_type": None,
        },
        "honesty": _packet_honesty(),
    }
    packet_errors = validate_packet(packet)
    if packet_errors:
        return None, [f"{path}: {item}" for item in packet_errors]
    if packet["graph"]["cold"] is not True or packet["graph"]["slots"] is not None:
        return None, [f"{path}: graph stays cold on this stamp"]
    if packet["dual_read"]["sealed_book_rpc_slice"] != "incomplete":
        return None, [f"{path}: sealed_book_rpc_slice must stay incomplete"]
    if "ws_payload" in packet:
        return None, [f"{path}: ws_payload must not be copied onto the packet"]
    return packet, []


def project_day(
    source_rows: Sequence[Any],
    *,
    day: str,
    fixture_origin: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Project a day's parsed JSONL objects. Non-creates are counted, not stamped."""
    errors: list[str] = []
    packets: list[dict[str, Any]] = []
    skipped = 0
    skipped_types: list[str] = []
    if not isinstance(source_rows, Sequence) or isinstance(source_rows, (str, bytes)):
        return [], {}, [f"{day}: source_rows must be a list"]
    for index, row in enumerate(source_rows):
        path = f"{day}.source_rows[{index}]"
        if not isinstance(row, Mapping):
            _err(errors, path, "must be a JSON object")
            continue
        if not _is_create_row(row):
            skipped += 1
            row_type = row.get("type")
            if isinstance(row_type, str):
                skipped_types.append(row_type)
            continue
        packet, problems = _project_create(
            row, day=day, fixture_origin=fixture_origin, path=path
        )
        if problems or packet is None:
            errors.extend(problems)
            continue
        packets.append(packet)
    if errors:
        return [], {}, errors
    if not packets:
        return [], {}, [f"{day}: no projectable bonding create"]
    census = {
        "lines": len(source_rows),
        "projected_n": len(packets),
        "skipped_n": skipped,
        "skipped": ([{"reason": SKIP_NOT_CREATE, "n": skipped}] if skipped else []),
        "skipped_types": sorted(set(skipped_types)),
        "skip_is_not_a_dropped_evaluate_arm": True,
    }
    return packets, census, []


def expectation_from_stamps(day: str, stamps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Synthetic identity checklist for this local projection. Same shape as the scoreboard."""
    rows: list[dict[str, Any]] = []
    for stamp in stamps:
        packet = stamp["input"]["packet"]
        rows.append(
            {
                "source_day": packet["provenance"]["source_day"],
                "signature": packet["l1_spine"]["signature"],
                "mint": packet["l1_spine"]["mint"],
                "evaluate_label": stamp["evaluate_label"],
                "reasons": list(stamp["reasons"]),
            }
        )
    rows.sort(key=lambda row: (row["source_day"], row["signature"], row["mint"]))
    return {
        "schema_version": SCOREBOARD_SCHEMA,
        "fixture_kind": EXPECTATION_KIND,
        "origin": "synthetic",
        "day": day,
        "sealed_book_rpc_slice": "incomplete",
        "closed_book_claim": False,
        "horizon_join": {
            "status": HORIZON_JOIN_STATUS,
            "values_supplied": False,
            "null_is_not_zero_return": True,
        },
        "delta_exec_join": {
            "field": "delta_exec",
            "also_called": "Δ_exec",
            "value": None,
            "status": HORIZON_JOIN_STATUS,
            "reason": DELTA_JOIN_REASON,
            "null_is_not_zero_cost": True,
        },
        "rows": rows,
    }


def _stamp_packets(
    packets: Sequence[Mapping[str, Any]], day: str
) -> tuple[list[dict[str, Any]], list[str]]:
    stamps: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, packet in enumerate(packets):
        stamp, problems = stamp_from_packet(packet)
        if problems or stamp is None:
            errors.extend(f"{day}.packets[{index}]: {item}" for item in problems)
            continue
        stamps.append(stamp)
    if errors:
        return [], errors
    if len(stamps) != len(packets):
        return [], [f"{day}: evaluate dropped a packet"]
    return stamps, []


def assemble(
    day_inputs: Any,
    *,
    fixture_origin: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Build one batch from parsed JSONL rows and one synthetic expectation per day."""
    if fixture_origin not in FIXTURE_ORIGINS:
        return None, [f"fixture_origin: must be one of {list(FIXTURE_ORIGINS)}"]
    if not isinstance(day_inputs, list) or not day_inputs:
        return None, ["days: at least one day-aligned JSONL is required"]
    errors: list[str] = []
    normalized: list[Mapping[str, Any]] = []
    seen_days: set[str] = set()
    for index, item in enumerate(day_inputs):
        path = f"input.days[{index}]"
        if not isinstance(item, Mapping):
            _err(errors, path, "must be an object")
            continue
        day = item.get("day")
        jsonl_name = item.get("jsonl_name")
        if not isinstance(day, str) or DAY_RE.fullmatch(day) is None:
            _err(errors, f"{path}.day", "must be YYYY-MM-DD")
            continue
        if jsonl_name != f"observe-{day}.jsonl":
            _err(errors, f"{path}.jsonl_name", "must be observe-YYYY-MM-DD.jsonl for that day")
        if day in seen_days:
            _err(errors, f"{path}.day", "duplicate day")
        seen_days.add(day)
        normalized.append(item)
    if errors:
        return None, errors

    ordered = sorted(normalized, key=lambda item: str(item["day"]))
    input_days: list[dict[str, Any]] = []
    output_days: list[dict[str, Any]] = []
    total_n = 0
    runner_n = 0
    reject_n = 0

    for item in ordered:
        day = str(item["day"])
        source_rows = item.get("source_rows")
        expectation = item.get("expectation")
        problems = validate_expectation(expectation)
        if problems:
            errors.extend(f"{day}.expectation: {problem}" for problem in problems)
            continue
        assert isinstance(expectation, Mapping)
        if expectation.get("day") != day:
            _err(errors, f"{day}.expectation.day", "must equal the JSONL day")
            continue
        packets, census, project_errors = project_day(
            source_rows, day=day, fixture_origin=fixture_origin
        )
        if project_errors:
            errors.extend(project_errors)
            continue
        stamps, stamp_errors = _stamp_packets(packets, day)
        if stamp_errors:
            errors.extend(stamp_errors)
            continue
        board, score_errors = score_stamps(stamps, expectation)
        if score_errors or board is None:
            errors.extend(f"{day}.scoreboard: {problem}" for problem in score_errors)
            continue
        if board["dual_read"]["sealed_book_rpc_slice"] != "incomplete":
            _err(errors, f"{day}.scoreboard.dual_read.sealed_book_rpc_slice", "must stay incomplete")
        if board["dual_read"]["closed_book_claim"] is not False:
            _err(errors, f"{day}.scoreboard.dual_read.closed_book_claim", "must be false")
        if board["fixture_join"]["closed_book"] is not False:
            _err(errors, f"{day}.scoreboard.fixture_join.closed_book", "must be false")
        if board["measure"]["kind"] != "none" or board["measure"]["pass_fail_no_lift"] is not False:
            _err(errors, f"{day}.scoreboard.measure", "kind stays none; no PASS/FAIL_NO_LIFT exit")
        if board["graph_lift"] is not None or board["graph_lift_aggregate_status"] != "not_used_graph_cold":
            _err(errors, f"{day}.scoreboard.graph_lift", "graph stays cold on this stamp")
        if board["full_book"]["reject_stamps_dropped"] != 0:
            _err(errors, f"{day}.scoreboard.full_book.reject_stamps_dropped", "must stay 0")
        input_days.append(
            {
                "day": day,
                "jsonl_name": f"observe-{day}.jsonl",
                "source_rows": list(source_rows),
                "expectation": expectation,
            }
        )
        output_days.append(
            {
                "day": day,
                "jsonl_name": f"observe-{day}.jsonl",
                "fixture_origin": fixture_origin,
                "row_census": census,
                "scoreboard": board,
            }
        )
        total_n += int(board["full_book"]["n"])
        runner_n += int(board["full_book"]["runner_n"])
        reject_n += int(board["full_book"]["reject_n"])

    if errors:
        return None, errors

    host_jsonl_read = fixture_origin == "sealed_row_projection"
    return {
        "schema_version": SCHEMA_VERSION,
        "type": BATCH_TYPE,
        "id": BATCH_ID,
        "paper_only": True,
        "input": {
            "pipeline": list(PIPELINE),
            "local_json_only": True,
            "rpc": False,
            "observe_jsonl_tail": False,
            "host_extract_required": False,
            "host_jsonl_read": host_jsonl_read,
            "fixture_origin": fixture_origin,
            "graph_policy": "cold",
            "days": input_days,
        },
        "days": output_days,
        "rollup": {
            "n_meaning": "stamp_count_not_a_return",
            "share_kind": "count_fraction_not_a_return",
            "counts_are_not_returns": True,
            "n": total_n,
            "runner_n": runner_n,
            "reject_n": reject_n,
            "rows": [
                {"label": "runner", "n": runner_n, "share": _share(runner_n, total_n)},
                {"label": "reject", "n": reject_n, "share": _share(reject_n, total_n)},
            ],
            "days": [item["day"] for item in output_days],
        },
        "full_book": {
            "policy": "dec007_both_arms_retained",
            "arm_retained": True,
            "deletes_detect_history": False,
            "reject_stamps_dropped": 0,
            "local_set_is_not_the_sealed_book": True,
            "n": total_n,
            "runner_n": runner_n,
            "reject_n": reject_n,
        },
        "graph_lift": None,
        "graph_policy": "cold",
        "dual_read": {
            "sealed_book_rpc_slice": "incomplete",
            "closed_book_claim": False,
            "incomplete_reason": INCOMPLETE_REASON,
        },
        "measure": {
            "kind": "none",
            "claims_alpha": False,
            "invented_ev": False,
            "invented_lift": False,
            "pass_fail_no_lift": False,
        },
        "soft_watches": {
            "blocking": False,
            "source": SOFT_WATCH_SOURCE,
            "inherited_from": list(INHERITED_FROM),
            "items": list(SOFT_WATCH_ITEMS),
        },
        "honesty": _honesty(),
    }, []


def _key_forbidden(key: str, path: str) -> bool:
    lowered = key.lower()
    if lowered in SOURCE_ROW_ONLY_KEYS:
        return ".source_rows" not in path
    return lowered in FORBIDDEN_KEYS


def _reject_forbidden_keys(errors: list[str], obj: Any, path: str) -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            if _key_forbidden(str(key), child):
                _err(errors, child, "forbidden on paper-batch-oracle-sealed-day-incomplete-rpc-v0")
            _reject_forbidden_keys(errors, value, child)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _reject_forbidden_keys(errors, value, f"{path}[{index}]")


def _diff_paths(expected: Any, actual: Any, path: str, out: list[str]) -> None:
    if len(out) >= 12:
        return
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        keys = list(dict.fromkeys([*expected.keys(), *actual.keys()]))
        for key in keys:
            child = f"{path}.{key}" if path else str(key)
            if key not in actual:
                out.append(f"{child}: missing")
            elif key not in expected:
                out.append(f"{child}: unexpected")
            else:
                _diff_paths(expected[key], actual[key], child, out)
        return
    if isinstance(expected, list) and isinstance(actual, list):
        if expected != actual:
            out.append(f"{path}: list mismatch")
        return
    if expected != actual:
        out.append(f"{path}: expected {expected!r}")


def validate_batch(batch: Any) -> list[str]:
    """Return errors. Empty list means the batch matches a rebuild of its JSONL rows."""
    if not isinstance(batch, Mapping):
        return ["batch: must be a JSON object"]
    errors: list[str] = []
    _reject_forbidden_keys(errors, batch, "batch")
    if errors:
        return errors
    inp = batch.get("input")
    if not isinstance(inp, Mapping):
        return ["input: must be an object"]
    expected, problems = assemble(inp.get("days"), fixture_origin=inp.get("fixture_origin"))
    if problems or expected is None:
        return problems or ["batch: embedded days did not rebuild"]
    if batch != expected:
        diffs: list[str] = []
        _diff_paths(expected, batch, "", diffs)
        if not diffs:
            diffs.append("batch: does not match the JSONL projection")
        return diffs
    return []


def read_jsonl(path: Path) -> tuple[list[Any], list[str]]:
    """Non-blank lines. Invalid JSON fails the file. Blank lines are ignored."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], [f"{path}: {exc}"]
    rows: list[Any] = []
    errors: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.strip() == "":
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path}:{lineno}: {exc}")
            continue
        if not isinstance(obj, dict):
            errors.append(f"{path}:{lineno}: line must be a JSON object")
            continue
        rows.append(obj)
    return rows, errors


def day_from_jsonl_name(path: Path) -> str | None:
    match = JSONL_NAME_RE.fullmatch(path.name)
    if match is None:
        return None
    return match.group(1)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _specs_from_files(
    pairs: Sequence[tuple[Path, Path]],
) -> tuple[list[dict[str, Any]], list[str]]:
    specs: list[dict[str, Any]] = []
    errors: list[str] = []
    for jsonl_path, expectation_path in pairs:
        day = day_from_jsonl_name(jsonl_path)
        if day is None:
            errors.append(f"{jsonl_path}: basename must be observe-YYYY-MM-DD.jsonl")
            continue
        rows, read_errors = read_jsonl(jsonl_path)
        errors.extend(read_errors)
        try:
            expectation = load_json(expectation_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{expectation_path}: {exc}")
            continue
        specs.append(
            {
                "day": day,
                "jsonl_name": jsonl_path.name,
                "source_rows": rows,
                "expectation": expectation,
            }
        )
    if errors:
        return [], errors
    return specs, []


def example_two_day() -> dict[str, Any]:
    """Checked-in synthetic pair. Calendar labels 2026-09-20 and 2026-09-21."""
    specs: list[dict[str, Any]] = []
    for day in EXAMPLE_DAYS:
        path = FIXTURE_DIR / f"observe-{day}.jsonl"
        rows, errors = read_jsonl(path)
        if errors:
            raise RuntimeError(errors)
        packets, _census, problems = project_day(rows, day=day, fixture_origin="synthetic")
        if problems:
            raise RuntimeError(problems)
        stamps, stamp_errors = _stamp_packets(packets, day)
        if stamp_errors:
            raise RuntimeError(stamp_errors)
        specs.append(
            {
                "day": day,
                "jsonl_name": path.name,
                "source_rows": rows,
                "expectation": expectation_from_stamps(day, stamps),
            }
        )
    batch, problems = assemble(specs, fixture_origin="synthetic")
    if batch is None:
        raise RuntimeError(problems)
    return batch


EXAMPLES: dict[str, Any] = {
    "two-day": example_two_day,
}


class _Parser(argparse.ArgumentParser):
    """Usage mistakes exit 1. This module has no measure exit."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def _pair_inputs(
    jsonl_paths: Sequence[Path], expectation_paths: Sequence[Path]
) -> tuple[list[tuple[Path, Path]], list[str]]:
    if not jsonl_paths or not expectation_paths:
        return [], ["batch: pass at least one --jsonl and one --expectation"]
    by_jsonl: dict[str, Path] = {}
    errors: list[str] = []
    for path in jsonl_paths:
        day = day_from_jsonl_name(path)
        if day is None:
            errors.append(f"{path}: basename must be observe-YYYY-MM-DD.jsonl")
            continue
        if day in by_jsonl:
            errors.append(f"{path}: duplicate day {day}")
            continue
        by_jsonl[day] = path
    by_expectation: dict[str, Path] = {}
    for path in expectation_paths:
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        day = payload.get("day") if isinstance(payload, Mapping) else None
        if not isinstance(day, str):
            errors.append(f"{path}: expectation.day missing")
            continue
        if day in by_expectation:
            errors.append(f"{path}: duplicate expectation day {day}")
            continue
        by_expectation[day] = path
    if errors:
        return [], errors
    if set(by_jsonl) != set(by_expectation):
        return [], [
            "batch: JSONL days and expectation days must match "
            f"(jsonl={sorted(by_jsonl)} expectation={sorted(by_expectation)})"
        ]
    pairs = [(by_jsonl[day], by_expectation[day]) for day in sorted(by_jsonl)]
    return pairs, []


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="python -m tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0",
        description=(
            "Project day-aligned sealed observe JSONL to hot-packet, "
            "paper-evaluate, and paper-scoreboard. Sealed book stays incomplete. "
            "No RPC, no observe tail, no closed-book claim, no measure exit."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    example = sub.add_parser("example", help="Print the synthetic two-day batch")
    example.add_argument("--which", choices=sorted(EXAMPLES), required=True)

    validate = sub.add_parser("validate", help="Validate one batch JSON file")
    validate.add_argument("path", type=Path)

    batch_cmd = sub.add_parser(
        "batch",
        help="Project local observe-YYYY-MM-DD.jsonl files and print one batch",
    )
    batch_cmd.add_argument("--jsonl", type=Path, action="append", required=True)
    batch_cmd.add_argument("--expectation", type=Path, action="append", required=True)
    batch_cmd.add_argument(
        "--fixture-origin",
        choices=FIXTURE_ORIGINS,
        default="synthetic",
    )

    args = parser.parse_args(argv)
    if args.cmd == "example":
        batch = EXAMPLES[args.which]()
        json.dump(batch, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        problems = validate_batch(batch)
        if problems:
            print("example failed its own validator:", file=sys.stderr)
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        return 0

    if args.cmd == "validate":
        path: Path = args.path
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            return 1
        problems = validate_batch(payload)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"ok {path}")
        return 0

    pairs, pair_errors = _pair_inputs(args.jsonl, args.expectation)
    if pair_errors:
        for problem in pair_errors:
            print(problem, file=sys.stderr)
        return 1
    specs, spec_errors = _specs_from_files(pairs)
    if spec_errors:
        for problem in spec_errors:
            print(problem, file=sys.stderr)
        return 1
    batch, problems = assemble(specs, fixture_origin=args.fixture_origin)
    if problems or batch is None:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    json.dump(batch, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
