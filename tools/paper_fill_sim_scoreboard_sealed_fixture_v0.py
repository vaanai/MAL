"""paper-fill-sim-scoreboard-sealed-fixture-v0 fixture aggregator.

Proposed paper scoreboard. Input is a local set of validated
paper_fill_sim_hot_packet_evaluate_v0 stamps plus one checked-in sealed-day
expectation. No RPC, no observe client, no JSONL tail, no host extract,
no /var/lib/mal, no measure exit that claims alpha.
"""

from __future__ import annotations

import argparse
import copy
import json
import posixpath
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.paper_evaluate_hot_packet_v0 import REASON_ORDER
from tools.paper_fill_sim_hot_packet_evaluate_v0 import (
    FILL_SIM_REJECT,
    FILL_SIM_RUNNER,
    HORIZON_STATUS as STAMP_HORIZON_STATUS,
    SOFT_WATCH_ITEMS as INHERITED_SOFT_WATCH_ITEMS,
    example_enriched_fee_runner_bind,
    example_enriched_launchlab_runner_bind,
    example_graph_slots_bind,
    example_reject_identity_bind,
    example_sealed_cold_runner_bind,
    validate_fill_sim_stamp,
)
from tools.paper_evaluate_hot_packet_v0 import (
    GRAPH_LIFT_COLD,
    GRAPH_LIFT_SLOTS,
    HORIZONS,
)

SCHEMA_VERSION = "paper_fill_sim_scoreboard_sealed_fixture_v0"
SCOREBOARD_TYPE = "paper_fill_sim_scoreboard"
SCOREBOARD_ID = "paper-fill-sim-scoreboard-sealed-fixture-v0"
EXPECTATION_KIND = "sealed_day_expectation"
SEALED_DAY = "2026-09-20"
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

HORIZON_JOIN_STATUS = "fixture_joined_null_explicit"
STAMP_DELTA_STATUS = "fill_sim_registration_null_explicit"
DELTA_JOIN_REASON = "fill_sim_sealed_day_fixture_has_no_scored_fill"
GRAPH_AGGREGATE_MIXED = "not_scored_mixed"
GRAPH_STATUS_ORDER: tuple[str, ...] = (GRAPH_LIFT_COLD, GRAPH_LIFT_SLOTS)
FILL_SIM_STATUS_ORDER: tuple[str, ...] = (FILL_SIM_RUNNER, FILL_SIM_REJECT)

SOFT_WATCH_SOURCE = (
    "hot_packet_v0_soft_gate_pr49_paper_evaluate_pr50_scoreboard_pr51_"
    "fill_sim_hot_packet_evaluate_v0_pr57_fill_sim_scoreboard_sealed_fixture_v0"
)
INHERITED_FROM: tuple[str, ...] = (
    "hot_packet_v0_soft_gate_pr49",
    "paper_evaluate_hot_packet_v0_pr50",
    "paper_scoreboard_sealed_fixture_v0_pr51",
    "paper_fill_sim_hot_packet_evaluate_v0_pr57",
)
LOCAL_SOFT_WATCH_ITEMS: tuple[str, ...] = (
    "label_share_is_not_a_return",
    "fill_sim_status_share_is_not_a_return",
    "synthetic_sealed_day_not_a_host_extract",
    "local_subset_is_not_dropped_history",
    "fill_sim_vocabulary_not_exp006_promote",
    "documented_constants_not_a_scored_fill",
)
SOFT_WATCH_ITEMS: tuple[str, ...] = INHERITED_SOFT_WATCH_ITEMS + LOCAL_SOFT_WATCH_ITEMS

HONESTY_FALSE: tuple[str, ...] = (
    "scored_oracle_measure",
    "executed_host_sealed_book",
    "ci_claimed_sealed_book_close",
    "continuous_observe_wiring",
    "encoder_promoted",
    "enum_production_lock",
    "discovery_promoted",
    "graph_lane_revived",
    "invented_lift",
    "invented_ev",
    "claims_alpha",
    "exp002c_retuned",
    "exp006_promoted",
    "exp006_harness_rewritten",
    "marks_joined_on_this_stamp",
    "live_capital",
    "trading_keys",
    "pumpportal_trade_api",
    "ssh_to_host",
    "host_extract_checked_into_git",
    "var_lib_mal_read",
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
        "expected_value",
        "alpha",
        "cohort_alpha",
        "ev",
        "verdict",
        "fail_no_lift",
        "exit_code",
        "PASS",
        "FAIL_NO_LIFT",
    }
)

REPO = Path(__file__).resolve().parents[1]
HOST_ROOT = "/var/lib/mal"
ALLOWED_STAMP_PREFIXES: tuple[str, ...] = (
    "fixtures/paper_fill_sim_hot_packet_evaluate_v0/",
)


def _collapse_leading_slashes(posix_path: str) -> str:
    """posixpath.normpath keeps a leading //; collapse before host-root checks."""
    if posix_path.startswith("//"):
        return "/" + posix_path.lstrip("/")
    return posix_path


def _lexical_posix(text: str) -> str:
    """Normalize a path string without reading the filesystem."""
    raw = text.replace("\\", "/").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = REPO / path
    return _collapse_leading_slashes(posixpath.normpath(path.as_posix()))


def _under_var_lib_mal(text: str) -> bool:
    """Lexical check only. Does not stat or read the path."""
    normalized = _lexical_posix(text)
    return normalized == HOST_ROOT or normalized.startswith(HOST_ROOT + "/")


def _host_open_refusal(text: str) -> str | None:
    if _under_var_lib_mal(text):
        return f"{text}: does not open {HOST_ROOT}"
    return None

EXPECTATION_KEYS: tuple[str, ...] = (
    "schema_version",
    "fixture_kind",
    "origin",
    "day",
    "sealed_book_rpc_slice",
    "closed_book_claim",
    "horizon_join",
    "delta_exec_join",
    "rows",
)
HORIZON_JOIN_KEYS: tuple[str, ...] = (
    "status",
    "values_supplied",
    "null_is_not_zero_return",
)
DELTA_JOIN_KEYS: tuple[str, ...] = (
    "field",
    "also_called",
    "value",
    "status",
    "reason",
    "null_is_not_zero_cost",
)
EXPECTATION_ROW_KEYS: tuple[str, ...] = (
    "source_day",
    "signature",
    "mint",
    "evaluate_label",
    "fill_sim_status",
    "reasons",
)

JOIN_MATCHED = "matched"
JOIN_LABEL = "label_disagree"
JOIN_REASON = "reason_disagree"
JOIN_FILL_SIM = "fill_sim_disagree"
JOIN_UNMATCHED = "unmatched"


def _err(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def _exact_object(errors: list[str], obj: Any, path: str, required: Sequence[str]) -> bool:
    if not isinstance(obj, Mapping):
        _err(errors, path, "must be a JSON object")
        return False
    ok = True
    allowed = set(required)
    for key in obj:
        if key not in allowed:
            _err(errors, f"{path}.{key}", "unexpected")
            ok = False
    for key in required:
        if key not in obj:
            _err(errors, f"{path}.{key}", "missing")
            ok = False
    return ok


def _reasons_well_formed(reasons: Any) -> bool:
    if not isinstance(reasons, list):
        return False
    if any(not isinstance(item, str) or item not in REASON_ORDER for item in reasons):
        return False
    if len(reasons) != len(set(reasons)):
        return False
    return reasons == [code for code in REASON_ORDER if code in reasons]


def _identity(stamp: Mapping[str, Any]) -> tuple[str, str, str]:
    evaluate = stamp["input"]["stamp"]
    packet = evaluate["input"]["packet"]
    return (
        packet["provenance"]["source_day"],
        packet["l1_spine"]["signature"],
        packet["l1_spine"]["mint"],
    )


def _share(numerator: int, denominator: int) -> dict[str, int]:
    return {"numerator": numerator, "denominator": denominator}


def _null_horizons() -> dict[str, None]:
    return {name: None for name in HORIZONS}


def _honesty() -> dict[str, Any]:
    body: dict[str, Any] = {"registration": "proposed"}
    for key in HONESTY_FALSE:
        body[key] = False
    return body


def _fill_sim_status(stamp: Mapping[str, Any]) -> str:
    block = stamp.get("fill_sim")
    if not isinstance(block, Mapping):
        return ""
    status = block.get("status")
    return status if isinstance(status, str) else ""


def _stamp_score_guards(stamp: Mapping[str, Any]) -> list[str]:
    """Refuse shapes that would turn null horizons or an open book into a measure."""
    errors: list[str] = []
    if stamp.get("graph_lift") is not None:
        _err(errors, "graph_lift", "must stay null")
    status = stamp.get("graph_lift_status")
    if status not in GRAPH_STATUS_ORDER:
        _err(errors, "graph_lift_status", "must be a not-scored status")
    horizons = stamp.get("horizons")
    if not isinstance(horizons, Mapping) or horizons.get("status") != STAMP_HORIZON_STATUS:
        _err(errors, "horizons.status", f"must be {STAMP_HORIZON_STATUS}")
    if isinstance(horizons, Mapping):
        values = horizons.get("values")
        if not isinstance(values, Mapping) or any(
            values.get(name) is not None for name in HORIZONS
        ):
            _err(errors, "horizons.values", "null is legal; a number is not a return")
    delta = stamp.get("delta_exec")
    if (
        not isinstance(delta, Mapping)
        or delta.get("value") is not None
        or delta.get("status") != STAMP_DELTA_STATUS
    ):
        _err(errors, "delta_exec", "fill_sim_registration_null_explicit only")
    fill_sim = stamp.get("fill_sim")
    if isinstance(fill_sim, Mapping):
        for key in ("fill_status", "t_fill", "fill_price_proxy", "fee_sol", "slippage_bps"):
            if fill_sim.get(key) is not None:
                _err(errors, f"fill_sim.{key}", "null outcomes only on this registration")
    dual = stamp.get("dual_read")
    if not isinstance(dual, Mapping) or dual.get("sealed_book_rpc_slice") != "incomplete":
        _err(errors, "dual_read.sealed_book_rpc_slice", "must stay incomplete")
    inp = stamp.get("input")
    if isinstance(inp, Mapping) and inp.get("marks_joined") is True:
        _err(errors, "input.marks_joined", "must stay false")
    return errors


def validate_expectation(expectation: Any) -> list[str]:
    """Checked-in sealed-day expectation. Synthetic only. No numeric horizons."""
    errors: list[str] = []
    if not _exact_object(errors, expectation, "expectation", EXPECTATION_KEYS):
        return errors
    assert isinstance(expectation, Mapping)
    if expectation.get("schema_version") != SCHEMA_VERSION:
        _err(errors, "expectation.schema_version", f"must be {SCHEMA_VERSION}")
    if expectation.get("fixture_kind") != EXPECTATION_KIND:
        _err(errors, "expectation.fixture_kind", f"must be {EXPECTATION_KIND}")
    if expectation.get("origin") != "synthetic":
        _err(errors, "expectation.origin", "checked-in fixture side is synthetic")
    day = expectation.get("day")
    if not isinstance(day, str) or DAY_RE.fullmatch(day) is None:
        _err(errors, "expectation.day", "must be YYYY-MM-DD")
    if expectation.get("sealed_book_rpc_slice") != "incomplete":
        _err(errors, "expectation.sealed_book_rpc_slice", "must stay incomplete")
    if expectation.get("closed_book_claim") is not False:
        _err(errors, "expectation.closed_book_claim", "must be false")

    horizon = expectation.get("horizon_join")
    if _exact_object(errors, horizon, "expectation.horizon_join", HORIZON_JOIN_KEYS):
        assert isinstance(horizon, Mapping)
        if horizon.get("status") != HORIZON_JOIN_STATUS:
            _err(errors, "expectation.horizon_join.status", f"must be {HORIZON_JOIN_STATUS}")
        if horizon.get("values_supplied") is not False:
            _err(
                errors,
                "expectation.horizon_join.values_supplied",
                "fixture join supplies a status only; numeric horizons are refused",
            )
        if horizon.get("null_is_not_zero_return") is not True:
            _err(errors, "expectation.horizon_join.null_is_not_zero_return", "must be true")

    delta = expectation.get("delta_exec_join")
    if _exact_object(errors, delta, "expectation.delta_exec_join", DELTA_JOIN_KEYS):
        assert isinstance(delta, Mapping)
        if delta.get("field") != "delta_exec" or delta.get("also_called") != "Δ_exec":
            _err(errors, "expectation.delta_exec_join", "field names are delta_exec / Δ_exec")
        if delta.get("value") is not None:
            _err(errors, "expectation.delta_exec_join.value", "null is legal; zero is not a fill")
        if delta.get("status") != HORIZON_JOIN_STATUS:
            _err(errors, "expectation.delta_exec_join.status", f"must be {HORIZON_JOIN_STATUS}")
        if delta.get("reason") != DELTA_JOIN_REASON:
            _err(errors, "expectation.delta_exec_join.reason", f"must be {DELTA_JOIN_REASON}")
        if delta.get("null_is_not_zero_cost") is not True:
            _err(errors, "expectation.delta_exec_join.null_is_not_zero_cost", "must be true")

    rows = expectation.get("rows")
    if not isinstance(rows, list) or not rows:
        _err(errors, "expectation.rows", "must be a non-empty array")
        return errors
    keys: list[tuple[str, str, str]] = []
    for index, row in enumerate(rows):
        path = f"expectation.rows[{index}]"
        if not _exact_object(errors, row, path, EXPECTATION_ROW_KEYS):
            continue
        assert isinstance(row, Mapping)
        source_day = row.get("source_day")
        signature = row.get("signature")
        mint = row.get("mint")
        if source_day != day:
            _err(errors, f"{path}.source_day", "must equal expectation.day")
        if not isinstance(signature, str) or signature.strip() == "":
            _err(errors, f"{path}.signature", "must be a non-empty string")
        if not isinstance(mint, str) or mint.strip() == "":
            _err(errors, f"{path}.mint", "must be a non-empty string; UNK is an identity, not a wildcard")
        if row.get("evaluate_label") not in ("runner", "reject"):
            _err(errors, f"{path}.evaluate_label", "must be runner or reject")
        if row.get("fill_sim_status") not in FILL_SIM_STATUS_ORDER:
            _err(errors, f"{path}.fill_sim_status", "must be a documented fill-sim status")
        if not _reasons_well_formed(row.get("reasons")):
            _err(errors, f"{path}.reasons", "must be the closed reason list, in order, without duplicates")
        if isinstance(source_day, str) and isinstance(signature, str) and isinstance(mint, str):
            keys.append((source_day, signature, mint))
    if len(keys) == len(rows):
        if keys != sorted(keys):
            _err(errors, "expectation.rows", "must be sorted by source_day, signature, mint")
        if len(keys) != len(set(keys)):
            _err(errors, "expectation.rows", "duplicate identity")
    return errors


def _join_status(stamp: Mapping[str, Any], expectation_rows: Mapping[tuple[str, str, str], Mapping[str, Any]]) -> str:
    key = _identity(stamp)
    row = expectation_rows.get(key)
    if row is None:
        return JOIN_UNMATCHED
    if row.get("evaluate_label") != stamp.get("evaluate_label"):
        return JOIN_LABEL
    if row.get("fill_sim_status") != _fill_sim_status(stamp):
        return JOIN_FILL_SIM
    if list(row.get("reasons") or []) != list(stamp.get("reasons") or []):
        return JOIN_REASON
    return JOIN_MATCHED


def _graph_aggregate(cold_n: int, slots_n: int) -> str:
    if slots_n == 0:
        return GRAPH_LIFT_COLD
    if cold_n == 0:
        return GRAPH_LIFT_SLOTS
    return GRAPH_AGGREGATE_MIXED


def _build_scoreboard(stamps: Sequence[Mapping[str, Any]], expectation: Mapping[str, Any]) -> dict[str, Any]:
    ordered = sorted(stamps, key=_identity)
    n = len(ordered)
    runner_n = sum(1 for stamp in ordered if stamp.get("evaluate_label") == "runner")
    reject_n = sum(1 for stamp in ordered if stamp.get("evaluate_label") == "reject")
    fill_sim_counts = {status: 0 for status in FILL_SIM_STATUS_ORDER}
    reason_counts = {code: 0 for code in REASON_ORDER}
    graph_counts = {status: 0 for status in GRAPH_STATUS_ORDER}
    exp_rows = {
        (row["source_day"], row["signature"], row["mint"]): row for row in expectation["rows"]
    }
    seen: dict[tuple[str, str, str], int] = {}
    join_counts = {
        JOIN_MATCHED: 0,
        JOIN_LABEL: 0,
        JOIN_FILL_SIM: 0,
        JOIN_REASON: 0,
        JOIN_UNMATCHED: 0,
    }
    stamp_rows: list[dict[str, Any]] = []
    by_day: dict[str, list[Mapping[str, Any]]] = {}

    for ordinal, stamp in enumerate(ordered):
        day, signature, mint = _identity(stamp)
        seen[(day, signature, mint)] = seen.get((day, signature, mint), 0) + 1
        for code in stamp["reasons"]:
            reason_counts[code] += 1
        graph_counts[stamp["graph_lift_status"]] += 1
        fs_status = _fill_sim_status(stamp)
        if fs_status in fill_sim_counts:
            fill_sim_counts[fs_status] += 1
        joined = _join_status(stamp, exp_rows)
        join_counts[joined] += 1
        by_day.setdefault(day, []).append(stamp)
        stamp_rows.append(
            {
                "ordinal": ordinal,
                "source_day": day,
                "signature": signature,
                "mint": mint,
                "evaluate_label": stamp["evaluate_label"],
                "fill_sim_status": fs_status,
                "reasons": list(stamp["reasons"]),
                "graph_lift": None,
                "graph_lift_status": stamp["graph_lift_status"],
                "horizon_status": STAMP_HORIZON_STATUS,
                "delta_exec_value": None,
                "delta_exec_status": STAMP_DELTA_STATUS,
                "sealed_book_rpc_slice": "incomplete",
                "fixture_join": joined,
            }
        )

    stamp_keys = set(seen)
    absent = sum(1 for key in exp_rows if key not in stamp_keys)
    duplicate_identity_n = sum(count - 1 for count in seen.values())

    sealed_days: list[dict[str, Any]] = []
    for day in sorted(by_day):
        origins = {
            stamp["input"]["stamp"]["input"]["packet"]["provenance"]["fixture_origin"]
            for stamp in by_day[day]
        }
        origin = next(iter(origins)) if len(origins) == 1 else "mixed"
        sealed_days.append(
            {
                "day": day,
                "n": len(by_day[day]),
                "fixture_origin": origin,
                "sealed_book_rpc_slice": "incomplete",
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "type": SCOREBOARD_TYPE,
        "id": SCOREBOARD_ID,
        "paper_only": True,
        "input": {
            "contract": "paper_fill_sim_hot_packet_evaluate_v0",
            "local_json_only": True,
            "rpc": False,
            "observe_jsonl_tail": False,
            "host_extract_required": False,
            "marks_joined": False,
            "fixture_side": "sealed_day_checked_in",
            "stamps": [copy.deepcopy(stamp) for stamp in ordered],
            "expectation": copy.deepcopy(expectation),
        },
        "full_book": {
            "policy": "dec007_both_arms_retained",
            "arm_retained": True,
            "deletes_detect_history": False,
            "reject_stamps_dropped": 0,
            "local_set_is_not_the_sealed_book": True,
            "n": n,
            "runner_n": runner_n,
            "reject_n": reject_n,
        },
        "label_rates": {
            "n_meaning": "stamp_count_not_a_return",
            "share_kind": "count_fraction_not_a_return",
            "n": n,
            "runner_n": runner_n,
            "reject_n": reject_n,
            "rows": [
                {"label": "runner", "n": runner_n, "share": _share(runner_n, n)},
                {"label": "reject", "n": reject_n, "share": _share(reject_n, n)},
            ],
        },
        "fill_sim_status_counts": [
            {"status": status, "n": fill_sim_counts[status], "share": _share(fill_sim_counts[status], n)}
            for status in FILL_SIM_STATUS_ORDER
        ],
        "reason_histogram": {
            "n_meaning": "stamp_occurrences_not_a_return",
            "share_kind": "count_fraction_not_a_return",
            "closed_list": list(REASON_ORDER),
            "rows": [
                {"reason": code, "n": reason_counts[code], "share": _share(reason_counts[code], n)}
                for code in REASON_ORDER
            ],
        },
        "graph_lift": None,
        "graph_lift_aggregate_status": _graph_aggregate(
            graph_counts[GRAPH_LIFT_COLD], graph_counts[GRAPH_LIFT_SLOTS]
        ),
        "graph_status_counts": [
            {"status": status, "n": graph_counts[status]} for status in GRAPH_STATUS_ORDER
        ],
        "horizons": {
            "status": HORIZON_JOIN_STATUS,
            "null_is_not_zero_return": True,
            "source": "sealed_day_expectation_and_fill_sim_stamp",
            "values": _null_horizons(),
        },
        "delta_exec": {
            "field": "delta_exec",
            "also_called": "Δ_exec",
            "value": None,
            "status": HORIZON_JOIN_STATUS,
            "reason": DELTA_JOIN_REASON,
            "null_is_not_zero_cost": True,
        },
        "dual_read": {
            "sealed_book_rpc_slice": "incomplete",
            "closed_book_claim": False,
            "incomplete_reason": "fill_sim_fixture_join_does_not_close_the_sealed_book",
        },
        "fixture_join": {
            "mode": "sealed_day_checked_in",
            "origin": "synthetic",
            "day": expectation["day"],
            "matched_n": join_counts[JOIN_MATCHED],
            "label_disagree_n": join_counts[JOIN_LABEL],
            "fill_sim_disagree_n": join_counts[JOIN_FILL_SIM],
            "reason_disagree_n": join_counts[JOIN_REASON],
            "unmatched_stamp_n": join_counts[JOIN_UNMATCHED],
            "expectation_rows_not_in_local_set": absent,
            "duplicate_identity_n": duplicate_identity_n,
            "closed_book": False,
        },
        "measure": {
            "kind": "none",
            "claims_alpha": False,
            "invented_ev": False,
            "invented_lift": False,
            "pass_fail_no_lift": False,
        },
        "sealed_days": sealed_days,
        "stamp_rows": stamp_rows,
        "soft_watches": {
            "blocking": False,
            "source": SOFT_WATCH_SOURCE,
            "inherited_from": list(INHERITED_FROM),
            "items": list(SOFT_WATCH_ITEMS),
        },
        "honesty": _honesty(),
    }


def score_stamps(
    stamps: Any, expectation: Any
) -> tuple[dict[str, Any] | None, list[str]]:
    """Aggregate a local set. Both arms stay. Invalid input does not emit a board."""
    if not isinstance(stamps, list) or not stamps:
        return None, [
            "stamps: local set must contain at least one paper_fill_sim_hot_packet_evaluate_v0 stamp"
        ]
    problems = validate_expectation(expectation)
    if problems:
        return None, problems
    errors: list[str] = []
    validated: list[Mapping[str, Any]] = []
    for index, stamp in enumerate(stamps):
        stamp_errors = validate_fill_sim_stamp(stamp)
        if stamp_errors:
            errors.extend(f"stamps[{index}]: {item}" for item in stamp_errors)
            continue
        guard = _stamp_score_guards(stamp)
        if guard:
            errors.extend(f"stamps[{index}]: {item}" for item in guard)
            continue
        validated.append(stamp)
    if errors:
        return None, errors
    if len(validated) != len(stamps):
        return None, ["stamps: reject arm or runner arm was dropped before aggregation"]
    return _build_scoreboard(validated, expectation), []


def _reject_forbidden_keys(errors: list[str], obj: Any, path: str) -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if str(key).lower() in FORBIDDEN_KEYS or key in FORBIDDEN_KEYS:
                _err(errors, f"{path}.{key}", "forbidden on paper-fill-sim-scoreboard-sealed-fixture-v0")
            _reject_forbidden_keys(errors, value, f"{path}.{key}")
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


def validate_scoreboard(board: Any) -> list[str]:
    """Return errors. Empty list means the board matches a rescore of its embedded set."""
    if not isinstance(board, Mapping):
        return ["scoreboard: must be a JSON object"]
    errors: list[str] = []
    _reject_forbidden_keys(errors, board, "scoreboard")
    if errors:
        return errors
    inp = board.get("input")
    if not isinstance(inp, Mapping):
        return ["input: must be an object"]
    expected, problems = score_stamps(inp.get("stamps"), inp.get("expectation"))
    if problems or expected is None:
        return problems or ["scoreboard: embedded set did not rescore"]
    if board != expected:
        diffs: list[str] = []
        _diff_paths(expected, board, "", diffs)
        if not diffs:
            diffs.append("scoreboard: does not match the local-set aggregation")
        return diffs
    return []


def sealed_day_expectation() -> dict[str, Any]:
    """Synthetic checklist for the checked-in fill-sim identities. One day."""
    rows: list[dict[str, Any]] = []
    days: set[str] = set()
    for builder in SET_BUILDERS["mixed"]:
        stamp = builder()
        day, signature, mint = _identity(stamp)
        days.add(day)
        rows.append(
            {
                "source_day": day,
                "signature": signature,
                "mint": mint,
                "evaluate_label": stamp["evaluate_label"],
                "fill_sim_status": _fill_sim_status(stamp),
                "reasons": list(stamp["reasons"]),
            }
        )
    if days != {SEALED_DAY}:
        raise RuntimeError(f"synthetic sealed-day expectation must be {SEALED_DAY}, got {sorted(days)}")
    rows.sort(key=lambda row: (row["source_day"], row["signature"], row["mint"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_kind": EXPECTATION_KIND,
        "origin": "synthetic",
        "day": SEALED_DAY,
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


SET_BUILDERS: dict[str, tuple[Any, ...]] = {
    "all-runner": (
        example_enriched_fee_runner_bind,
        example_graph_slots_bind,
        example_enriched_launchlab_runner_bind,
        example_sealed_cold_runner_bind,
    ),
    "mixed": (
        example_enriched_fee_runner_bind,
        example_graph_slots_bind,
        example_enriched_launchlab_runner_bind,
        example_reject_identity_bind,
        example_sealed_cold_runner_bind,
    ),
    "identity-reject": (example_reject_identity_bind,),
    "graph-cold": (example_sealed_cold_runner_bind,),
    "slots-not-scored": (example_graph_slots_bind,),
}


def _example(which: str) -> dict[str, Any]:
    stamps = [builder() for builder in SET_BUILDERS[which]]
    board, errors = score_stamps(stamps, sealed_day_expectation())
    if board is None:
        raise RuntimeError(errors)
    return board


def example_all_runner() -> dict[str, Any]:
    return _example("all-runner")


def example_mixed() -> dict[str, Any]:
    return _example("mixed")


def example_identity_reject() -> dict[str, Any]:
    return _example("identity-reject")


def example_graph_cold() -> dict[str, Any]:
    return _example("graph-cold")


def example_slots_not_scored() -> dict[str, Any]:
    return _example("slots-not-scored")


EXAMPLES: dict[str, Any] = {
    "all-runner": example_all_runner,
    "mixed": example_mixed,
    "identity-reject": example_identity_reject,
    "graph-cold": example_graph_cold,
    "slots-not-scored": example_slots_not_scored,
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _stamp_path_allowed(path: Path) -> bool:
    if _under_var_lib_mal(str(path)):
        return False
    try:
        rel = path.resolve().relative_to(REPO.resolve())
    except ValueError:
        return False
    rel_str = rel.as_posix()
    return any(rel_str.startswith(prefix) for prefix in ALLOWED_STAMP_PREFIXES)


class _Parser(argparse.ArgumentParser):
    """Usage mistakes exit 1. This module has no measure exit."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def _collect_stamp_paths(paths: Sequence[Path], stamp_dir: Path | None) -> list[Path]:
    collected = list(paths)
    if stamp_dir is not None:
        if not stamp_dir.is_dir():
            raise OSError(f"{stamp_dir}: not a directory")
        collected.extend(sorted(path for path in stamp_dir.glob("*.json") if path.is_file()))
    return collected


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="python -m tools.paper_fill_sim_scoreboard_sealed_fixture_v0",
        description=(
            "Score a local set of paper_fill_sim_hot_packet_evaluate_v0 stamps against "
            "a checked-in sealed-day expectation. Fixtures only. No RPC, no observe tail, "
            "no host extract, no /var/lib/mal, no measure exit."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    example = sub.add_parser("example", help="Print a synthetic scoreboard")
    example.add_argument("--which", choices=sorted(EXAMPLES), required=True)

    validate = sub.add_parser("validate", help="Validate one scoreboard JSON file")
    validate.add_argument("path", type=Path)

    score = sub.add_parser(
        "score",
        help="Score local fill-sim stamp JSON files against one sealed-day expectation file",
    )
    score.add_argument("--expectation", type=Path, required=True)
    score.add_argument("--dir", type=Path, dest="stamp_dir")
    score.add_argument("paths", nargs="*", type=Path)

    args = parser.parse_args(argv)
    if args.cmd == "example":
        board = EXAMPLES[args.which]()
        json.dump(board, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        problems = validate_scoreboard(board)
        if problems:
            print("example failed its own validator:", file=sys.stderr)
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        return 0

    if args.cmd == "validate":
        path: Path = args.path
        refusal = _host_open_refusal(str(path))
        if refusal:
            print(refusal, file=sys.stderr)
            return 1
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            return 1
        problems = validate_scoreboard(payload)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"ok {path}")
        return 0

    for candidate in (args.expectation, *args.paths):
        refusal = _host_open_refusal(str(candidate))
        if refusal:
            print(refusal, file=sys.stderr)
            return 1
    if args.stamp_dir is not None:
        refusal = _host_open_refusal(str(args.stamp_dir))
        if refusal:
            print(refusal, file=sys.stderr)
            return 1

    try:
        expectation = load_json(args.expectation)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{args.expectation}: {exc}", file=sys.stderr)
        return 1
    try:
        stamp_paths = _collect_stamp_paths(args.paths, args.stamp_dir)
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not stamp_paths:
        print("score: pass fill-sim stamp JSON paths and/or --dir", file=sys.stderr)
        return 1

    stamps: list[Any] = []
    load_errors: list[str] = []
    for path in stamp_paths:
        refusal = _host_open_refusal(str(path))
        if refusal:
            print(refusal, file=sys.stderr)
            return 1
        if not _stamp_path_allowed(path):
            print(
                f"{path}: score accepts fixtures under {', '.join(ALLOWED_STAMP_PREFIXES)} only",
                file=sys.stderr,
            )
            return 1
        try:
            stamps.append(load_json(path))
        except (OSError, json.JSONDecodeError) as exc:
            load_errors.append(f"{path}: {exc}")
    if load_errors:
        for problem in load_errors:
            print(problem, file=sys.stderr)
        return 1

    board, problems = score_stamps(stamps, expectation)
    if problems or board is None:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    json.dump(board, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
