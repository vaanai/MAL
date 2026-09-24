"""paper-laya-decision-packet-scoreboard-sealed-fixture-v0 fixture aggregator.

Proposed scoreboard. Input is a local set of validated
paper_laya_precompute_decision_packet_v0 objects plus one checked-in sealed-day
expectation. No RPC, no observe client, no /var/lib/mal, no measure exit.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import posixpath
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.paper_evaluate_hot_packet_v0 import REASON_ORDER
from tools.paper_laya_precompute_decision_packet_v0 import (
    HORIZON_STATUS as PACKET_HORIZON_STATUS,
    PREFIX_FILL_SIM,
    PREFIX_NON_FILL,
    SOFT_WATCH_ITEMS as INHERITED_SOFT_WATCH_ITEMS,
    example_fill_sim_surround,
    example_mixed_spines,
    example_non_fill_surround,
    validate_packet,
)

SCHEMA_VERSION = "paper_laya_decision_packet_scoreboard_sealed_fixture_v0"
SCOREBOARD_TYPE = "paper_laya_decision_packet_scoreboard"
SCOREBOARD_ID = "paper-laya-decision-packet-scoreboard-sealed-fixture-v0"
EXPECTATION_KIND = "sealed_day_expectation"
SEALED_DAY = "2026-09-20"
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

HORIZON_JOIN_STATUS = "fixture_joined_null_explicit"
PACKET_DELTA_STATUS = "decision_packet_null_explicit"
DELTA_JOIN_REASON = "laya_decision_packet_sealed_day_fixture_has_no_scored_fill"
LOCK_REASON = "paper_registration_fixture_scoreboard_not_risk_gate_unlock"

SPINE_PROFILE_ORDER: tuple[str, ...] = (
    "non_fill_sim_spine",
    "fill_sim_spine",
    "mixed_spines",
)
FILL_SIM_STATUS_ORDER: tuple[str, ...] = (
    "documented_model_only",
    "reject_arm_no_pretend_buy",
)

SOFT_WATCH_SOURCE = (
    "hot_packet_v0_soft_gate_pr49_through_paper_laya_precompute_decision_packet_pr67_"
    "laya_decision_packet_scoreboard_sealed_fixture_v0"
)
INHERITED_FROM: tuple[str, ...] = (
    "paper_laya_precompute_decision_packet_v0_pr67",
)
LOCAL_SOFT_WATCH_ITEMS: tuple[str, ...] = (
    "digest_label_share_is_not_a_return",
    "spine_profile_share_is_not_a_return",
    "fill_sim_digest_share_is_not_a_return",
    "synthetic_sealed_day_not_a_host_extract",
    "local_subset_is_not_dropped_history",
    "decision_packet_not_risk_gate_unlock",
    "decision_packet_not_laya_authorize_run",
    "decision_packet_not_live",
)
SOFT_WATCH_ITEMS: tuple[str, ...] = tuple(
    dict.fromkeys(INHERITED_SOFT_WATCH_ITEMS + LOCAL_SOFT_WATCH_ITEMS)
)

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
    "laya_authorize_run",
    "risk_gate_unlock",
    "live_trading_enabled",
    "decision_packet_body_embedded_as_scored",
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
ALLOWED_PACKET_PREFIXES: tuple[str, ...] = (
    "fixtures/paper_laya_precompute_decision_packet_v0/",
)

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
    "spine_profile",
    "assembly_fingerprint",
    "digest_n",
    "digest_runner_n",
    "digest_reject_n",
    "risk_gate_decision",
)

JOIN_MATCHED = "matched"
JOIN_SPINE = "spine_profile_disagree"
JOIN_DIGEST = "digest_disagree"
JOIN_RISK_GATE = "risk_gate_disagree"
JOIN_UNMATCHED = "unmatched"

HORIZONS = ("1s", "5s", "15s", "30s", "60s")


def _err(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def _collapse_leading_slashes(posix_path: str) -> str:
    if posix_path.startswith("//"):
        return "/" + posix_path.lstrip("/")
    return posix_path


def _lexical_posix(text: str) -> str:
    raw = text.replace("\\", "/").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = REPO / path
    return _collapse_leading_slashes(posixpath.normpath(path.as_posix()))


def _under_var_lib_mal(text: str) -> bool:
    normalized = _lexical_posix(text)
    return normalized == HOST_ROOT or normalized.startswith(HOST_ROOT + "/")


def _host_open_refusal(text: str) -> str | None:
    if _under_var_lib_mal(text):
        return f"{text}: does not open {HOST_ROOT}"
    raw = text.replace("\\", "/").strip()
    norm = _collapse_leading_slashes(posixpath.normpath(raw))
    if posixpath.isabs(norm) and _under_var_lib_mal(norm):
        return f"{text}: does not open {HOST_ROOT}"
    if not posixpath.isabs(norm):
        for cwd in ("/", os.getcwd()):
            joined = _collapse_leading_slashes(posixpath.normpath(posixpath.join(cwd, raw)))
            if joined == HOST_ROOT or joined.startswith(HOST_ROOT + "/"):
                return f"{text}: does not open {HOST_ROOT}"
    return None


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


def _share(numerator: int, denominator: int) -> dict[str, int]:
    return {"numerator": numerator, "denominator": denominator}


def _null_horizons() -> dict[str, None]:
    return {name: None for name in HORIZONS}


def _honesty() -> dict[str, Any]:
    body: dict[str, Any] = {"registration": "proposed"}
    for key in HONESTY_FALSE:
        body[key] = False
    return body


def _laya_caps() -> dict[str, Any]:
    return {
        "authorize_run": False,
        "risk_gate_unlock": False,
        "live_trading": False,
        "consumption": "fixtures_only_decision_packet_scoreboard_v0",
    }


def _risk_gate_block() -> dict[str, Any]:
    return {
        "decision": "locked",
        "unlock": False,
        "reason": LOCK_REASON,
    }


def _assembly_fingerprint(packet: Mapping[str, Any]) -> str:
    asm = packet["input"]["assembly"]
    surround = sorted(str(item) for item in asm["surround_paths"])
    locks = sorted(str(item) for item in asm["lock_receipt_paths"])
    return "|".join((*surround, *locks))


def _spine_profile(packet: Mapping[str, Any]) -> str:
    paths = packet["input"]["assembly"]["surround_paths"]
    has_non_fill = any(str(p).startswith(PREFIX_NON_FILL) for p in paths)
    has_fill = any(str(p).startswith(PREFIX_FILL_SIM) for p in paths)
    if has_non_fill and has_fill:
        return "mixed_spines"
    if has_fill:
        return "fill_sim_spine"
    if has_non_fill:
        return "non_fill_sim_spine"
    return "unknown"


def _digest_totals(packet: Mapping[str, Any]) -> tuple[int, int, int]:
    runner = reject = total = 0
    for cite in packet.get("surround_citations", []):
        if not isinstance(cite, Mapping):
            continue
        digest = cite.get("digest")
        if not isinstance(digest, Mapping):
            continue
        fb = digest.get("full_book")
        if not isinstance(fb, Mapping):
            continue
        runner += int(fb.get("runner_n", 0))
        reject += int(fb.get("reject_n", 0))
        total += int(fb.get("n", 0))
    return total, runner, reject


def _fill_sim_digest_counts(packet: Mapping[str, Any]) -> dict[str, int]:
    counts = {status: 0 for status in FILL_SIM_STATUS_ORDER}
    for cite in packet.get("surround_citations", []):
        if not isinstance(cite, Mapping):
            continue
        digest = cite.get("digest")
        if not isinstance(digest, Mapping):
            continue
        rows = digest.get("fill_sim_status_counts")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            status = row.get("status")
            if status in counts:
                counts[str(status)] += int(row.get("n", 0))
    return counts


def _identity(packet: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        SEALED_DAY,
        _spine_profile(packet),
        _assembly_fingerprint(packet),
    )


def _packet_score_guards(packet: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if packet.get("graph_lift") is not None:
        _err(errors, "graph_lift", "must stay null")
    if packet.get("graph_policy") != "cold":
        _err(errors, "graph_policy", "must stay cold")
    horizons = packet.get("horizons")
    if not isinstance(horizons, Mapping) or horizons.get("status") != PACKET_HORIZON_STATUS:
        _err(errors, "horizons.status", f"must be {PACKET_HORIZON_STATUS}")
    if isinstance(horizons, Mapping):
        values = horizons.get("values")
        if not isinstance(values, Mapping) or any(values.get(name) is not None for name in HORIZONS):
            _err(errors, "horizons.values", "null is legal; a number is not a return")
    delta = packet.get("delta_exec")
    if (
        not isinstance(delta, Mapping)
        or delta.get("value") is not None
        or delta.get("status") != PACKET_HORIZON_STATUS
    ):
        _err(errors, "delta_exec", f"{PACKET_HORIZON_STATUS} only")
    dual = packet.get("dual_read")
    if not isinstance(dual, Mapping) or dual.get("sealed_book_rpc_slice") != "incomplete":
        _err(errors, "dual_read.sealed_book_rpc_slice", "must stay incomplete")
    if isinstance(dual, Mapping) and dual.get("closed_book_claim") is not False:
        _err(errors, "dual_read.closed_book_claim", "must stay false")
    laya = packet.get("laya")
    if not isinstance(laya, Mapping):
        _err(errors, "laya", "must be an object")
    elif any(laya.get(key) is not False for key in ("authorize_run", "risk_gate_unlock", "live_trading")):
        _err(errors, "laya", "authorize_run, risk_gate_unlock, and live_trading must stay false")
    rg = packet.get("risk_gate")
    if not isinstance(rg, Mapping):
        _err(errors, "risk_gate", "must be an object")
    elif rg.get("decision") != "locked" or rg.get("unlock") is not False:
        _err(errors, "risk_gate", "must stay locked with unlock false")
    measure = packet.get("measure")
    if not isinstance(measure, Mapping) or measure.get("kind") != "none":
        _err(errors, "measure.kind", "must stay none")
    inp = packet.get("input")
    if isinstance(inp, Mapping) and inp.get("marks_joined") is True:
        _err(errors, "input.marks_joined", "must stay false")
    return errors


def validate_expectation(expectation: Any) -> list[str]:
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
            _err(errors, "expectation.horizon_join.values_supplied", "must be false")
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
        if row.get("source_day") != day:
            _err(errors, f"{path}.source_day", "must equal expectation.day")
        if row.get("spine_profile") not in SPINE_PROFILE_ORDER:
            _err(errors, f"{path}.spine_profile", "must be a documented spine profile")
        fp = row.get("assembly_fingerprint")
        if not isinstance(fp, str) or fp.strip() == "":
            _err(errors, f"{path}.assembly_fingerprint", "must be a non-empty string")
        for field in ("digest_n", "digest_runner_n", "digest_reject_n"):
            if not isinstance(row.get(field), int) or int(row[field]) < 0:
                _err(errors, f"{path}.{field}", "must be a non-negative integer")
        if row.get("risk_gate_decision") != "locked":
            _err(errors, f"{path}.risk_gate_decision", "must be locked on this registration")
        if isinstance(fp, str):
            keys.append((str(day), str(row.get("spine_profile")), fp))
    if len(keys) == len(rows):
        if keys != sorted(keys):
            _err(errors, "expectation.rows", "must be sorted by source_day, spine_profile, assembly_fingerprint")
        if len(keys) != len(set(keys)):
            _err(errors, "expectation.rows", "duplicate identity")
    return errors


def _join_status(packet: Mapping[str, Any], exp_rows: Mapping[tuple[str, str, str], Mapping[str, Any]]) -> str:
    key = _identity(packet)
    row = exp_rows.get(key)
    if row is None:
        return JOIN_UNMATCHED
    if row.get("spine_profile") != _spine_profile(packet):
        return JOIN_SPINE
    total, runner, reject = _digest_totals(packet)
    if (
        int(row.get("digest_n", -1)) != total
        or int(row.get("digest_runner_n", -1)) != runner
        or int(row.get("digest_reject_n", -1)) != reject
    ):
        return JOIN_DIGEST
    rg = packet.get("risk_gate", {})
    if not isinstance(rg, Mapping) or rg.get("decision") != row.get("risk_gate_decision"):
        return JOIN_RISK_GATE
    return JOIN_MATCHED


def _build_scoreboard(packets: Sequence[Mapping[str, Any]], expectation: Mapping[str, Any]) -> dict[str, Any]:
    ordered = sorted(packets, key=_identity)
    packet_n = len(ordered)
    spine_counts = {profile: 0 for profile in SPINE_PROFILE_ORDER}
    fill_sim_counts = {status: 0 for status in FILL_SIM_STATUS_ORDER}
    digest_runner = digest_reject = digest_total = 0
    exp_rows = {
        (row["source_day"], row["spine_profile"], row["assembly_fingerprint"]): row
        for row in expectation["rows"]
    }
    seen: dict[tuple[str, str, str], int] = {}
    join_counts = {
        JOIN_MATCHED: 0,
        JOIN_SPINE: 0,
        JOIN_DIGEST: 0,
        JOIN_RISK_GATE: 0,
        JOIN_UNMATCHED: 0,
    }
    packet_rows: list[dict[str, Any]] = []

    for ordinal, packet in enumerate(ordered):
        day, spine, fingerprint = _identity(packet)
        seen[(day, spine, fingerprint)] = seen.get((day, spine, fingerprint), 0) + 1
        profile = _spine_profile(packet)
        if profile in spine_counts:
            spine_counts[profile] += 1
        total, runner, reject = _digest_totals(packet)
        digest_total += total
        digest_runner += runner
        digest_reject += reject
        for status, count in _fill_sim_digest_counts(packet).items():
            fill_sim_counts[status] += count
        joined = _join_status(packet, exp_rows)
        join_counts[joined] += 1
        packet_rows.append(
            {
                "ordinal": ordinal,
                "source_day": day,
                "spine_profile": profile,
                "assembly_fingerprint": fingerprint,
                "digest_n": total,
                "digest_runner_n": runner,
                "digest_reject_n": reject,
                "risk_gate_decision": packet["risk_gate"]["decision"],
                "risk_gate_unlock": packet["risk_gate"]["unlock"],
                "laya_authorize_run": packet["laya"]["authorize_run"],
                "laya_live_trading": packet["laya"]["live_trading"],
                "horizon_status": PACKET_HORIZON_STATUS,
                "delta_exec_value": None,
                "delta_exec_status": PACKET_HORIZON_STATUS,
                "sealed_book_rpc_slice": "incomplete",
                "fixture_join": joined,
            }
        )

    packet_keys = set(seen)
    absent = sum(1 for key in exp_rows if key not in packet_keys)
    duplicate_identity_n = sum(count - 1 for count in seen.values())

    reason_counts = {code: 0 for code in REASON_ORDER}

    return {
        "schema_version": SCHEMA_VERSION,
        "type": SCOREBOARD_TYPE,
        "id": SCOREBOARD_ID,
        "paper_only": True,
        "laya": _laya_caps(),
        "risk_gate": _risk_gate_block(),
        "input": {
            "contract": "paper_laya_precompute_decision_packet_v0",
            "local_json_only": True,
            "rpc": False,
            "observe_jsonl_tail": False,
            "host_extract_required": False,
            "host_jsonl_read": False,
            "marks_joined": False,
            "fixture_side": "sealed_day_checked_in",
            "packets": [copy.deepcopy(packet) for packet in ordered],
            "expectation": copy.deepcopy(expectation),
        },
        "full_book": {
            "policy": "dec007_both_arms_retained",
            "arm_retained": True,
            "deletes_detect_history": False,
            "reject_stamps_dropped": 0,
            "local_set_is_not_the_sealed_book": True,
            "packet_n": packet_n,
            "digest_stamp_n": digest_total,
            "digest_runner_n": digest_runner,
            "digest_reject_n": digest_reject,
        },
        "spine_profile_counts": [
            {
                "spine_profile": profile,
                "n": spine_counts[profile],
                "share": _share(spine_counts[profile], packet_n or 1),
            }
            for profile in SPINE_PROFILE_ORDER
        ],
        "label_rates": {
            "n_meaning": "digest_stamp_count_not_a_return",
            "share_kind": "count_fraction_not_a_return",
            "n": digest_total,
            "runner_n": digest_runner,
            "reject_n": digest_reject,
            "rows": [
                {"label": "runner", "n": digest_runner, "share": _share(digest_runner, digest_total or 1)},
                {"label": "reject", "n": digest_reject, "share": _share(digest_reject, digest_total or 1)},
            ],
        },
        "fill_sim_status_counts": [
            {
                "status": status,
                "n": fill_sim_counts[status],
                "share": _share(fill_sim_counts[status], digest_total or 1),
            }
            for status in FILL_SIM_STATUS_ORDER
        ],
        "reason_histogram": {
            "n_meaning": "not_on_decision_packet_digest_v0",
            "share_kind": "count_fraction_not_a_return",
            "closed_list": list(REASON_ORDER),
            "rows": [
                {"reason": code, "n": reason_counts[code], "share": _share(reason_counts[code], packet_n or 1)}
                for code in REASON_ORDER
            ],
        },
        "graph_lift": None,
        "graph_policy": "cold",
        "horizons": {
            "status": HORIZON_JOIN_STATUS,
            "null_is_not_zero_return": True,
            "source": "sealed_day_expectation_and_decision_packet",
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
            "incomplete_reason": "laya_decision_packet_fixture_join_does_not_close_the_sealed_book",
        },
        "fixture_join": {
            "mode": "sealed_day_checked_in",
            "origin": "synthetic",
            "day": expectation["day"],
            "matched_n": join_counts[JOIN_MATCHED],
            "spine_profile_disagree_n": join_counts[JOIN_SPINE],
            "digest_disagree_n": join_counts[JOIN_DIGEST],
            "risk_gate_disagree_n": join_counts[JOIN_RISK_GATE],
            "unmatched_packet_n": join_counts[JOIN_UNMATCHED],
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
        "sealed_days": [
            {
                "day": SEALED_DAY,
                "packet_n": packet_n,
                "fixture_origin": "synthetic",
                "sealed_book_rpc_slice": "incomplete",
            }
        ],
        "packet_rows": packet_rows,
        "soft_watches": {
            "blocking": False,
            "source": SOFT_WATCH_SOURCE,
            "inherited_from": list(INHERITED_FROM),
            "items": list(SOFT_WATCH_ITEMS),
        },
        "honesty": _honesty(),
    }


def score_packets(
    packets: Any, expectation: Any
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(packets, list) or not packets:
        return None, [
            "packets: local set must contain at least one paper_laya_precompute_decision_packet_v0"
        ]
    problems = validate_expectation(expectation)
    if problems:
        return None, problems
    errors: list[str] = []
    validated: list[Mapping[str, Any]] = []
    for index, packet in enumerate(packets):
        packet_errors = validate_packet(packet)
        if packet_errors:
            errors.extend(f"packets[{index}]: {item}" for item in packet_errors)
            continue
        guard = _packet_score_guards(packet)
        if guard:
            errors.extend(f"packets[{index}]: {item}" for item in guard)
            continue
        validated.append(packet)
    if errors:
        return None, errors
    if len(validated) != len(packets):
        return None, ["packets: a spine arm or locked packet was dropped before aggregation"]
    return _build_scoreboard(validated, expectation), []


def _reject_forbidden_keys(errors: list[str], obj: Any, path: str) -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            if str(key).lower() in FORBIDDEN_KEYS or key in FORBIDDEN_KEYS:
                _err(errors, child, "forbidden on decision-packet-scoreboard-sealed-fixture-v0")
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


def validate_scoreboard(board: Any) -> list[str]:
    if not isinstance(board, Mapping):
        return ["scoreboard: must be a JSON object"]
    errors: list[str] = []
    _reject_forbidden_keys(errors, board, "scoreboard")
    if errors:
        return errors
    inp = board.get("input")
    if not isinstance(inp, Mapping):
        return ["input: must be an object"]
    expected, problems = score_packets(inp.get("packets"), inp.get("expectation"))
    if problems or expected is None:
        return problems or ["scoreboard: embedded set did not rescore"]
    if board != expected:
        diffs: list[str] = []
        _diff_paths(expected, board, "", diffs)
        if not diffs:
            diffs.append("scoreboard: does not match the local-set aggregation")
        return diffs
    return []


def _expectation_row_from_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    total, runner, reject = _digest_totals(packet)
    return {
        "source_day": SEALED_DAY,
        "spine_profile": _spine_profile(packet),
        "assembly_fingerprint": _assembly_fingerprint(packet),
        "digest_n": total,
        "digest_runner_n": runner,
        "digest_reject_n": reject,
        "risk_gate_decision": "locked",
    }


def sealed_day_expectation() -> dict[str, Any]:
    rows = [_expectation_row_from_packet(builder()) for builder in SET_BUILDERS["all-spines"]]
    rows.sort(key=lambda row: (row["source_day"], row["spine_profile"], row["assembly_fingerprint"]))
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
    "all-spines": (
        example_non_fill_surround,
        example_fill_sim_surround,
        example_mixed_spines,
    ),
    "non-fill-only": (example_non_fill_surround,),
    "fill-sim-only": (example_fill_sim_surround,),
    "mixed-spines-only": (example_mixed_spines,),
}


def _example(which: str) -> dict[str, Any]:
    packets = [builder() for builder in SET_BUILDERS[which]]
    board, errors = score_packets(packets, sealed_day_expectation())
    if board is None:
        raise RuntimeError(errors)
    return board


def example_all_spines() -> dict[str, Any]:
    return _example("all-spines")


def example_non_fill_only() -> dict[str, Any]:
    return _example("non-fill-only")


def example_fill_sim_only() -> dict[str, Any]:
    return _example("fill-sim-only")


def example_mixed_spines_only() -> dict[str, Any]:
    return _example("mixed-spines-only")


EXAMPLES: dict[str, Any] = {
    "all-spines": example_all_spines,
    "non-fill-only": example_non_fill_only,
    "fill-sim-only": example_fill_sim_only,
    "mixed-spines-only": example_mixed_spines_only,
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _packet_path_allowed(path: Path) -> bool:
    if _under_var_lib_mal(str(path)):
        return False
    try:
        rel = path.resolve().relative_to(REPO.resolve())
    except ValueError:
        return False
    rel_str = rel.as_posix()
    return any(rel_str.startswith(prefix) for prefix in ALLOWED_PACKET_PREFIXES)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def _collect_packet_paths(paths: Sequence[Path], packet_dir: Path | None) -> list[Path]:
    collected = list(paths)
    if packet_dir is not None:
        if not packet_dir.is_dir():
            raise OSError(f"{packet_dir}: not a directory")
        collected.extend(sorted(path for path in packet_dir.glob("*.json") if path.is_file()))
    return collected


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="python -m tools.paper_laya_decision_packet_scoreboard_sealed_fixture_v0",
        description=(
            "Score a local set of paper_laya_precompute_decision_packet_v0 objects against "
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
        help="Score local decision-packet JSON files against one sealed-day expectation file",
    )
    score.add_argument("--expectation", type=Path, required=True)
    score.add_argument("--dir", type=Path, dest="packet_dir")
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
    if args.packet_dir is not None:
        refusal = _host_open_refusal(str(args.packet_dir))
        if refusal:
            print(refusal, file=sys.stderr)
            return 1

    try:
        expectation = load_json(args.expectation)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{args.expectation}: {exc}", file=sys.stderr)
        return 1
    try:
        packet_paths = _collect_packet_paths(args.paths, args.packet_dir)
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not packet_paths:
        print("score: pass decision-packet JSON paths and/or --dir", file=sys.stderr)
        return 1

    packets: list[Any] = []
    load_errors: list[str] = []
    for path in packet_paths:
        refusal = _host_open_refusal(str(path))
        if refusal:
            print(refusal, file=sys.stderr)
            return 1
        if not _packet_path_allowed(path):
            print(
                f"{path}: score accepts fixtures under {', '.join(ALLOWED_PACKET_PREFIXES)} only",
                file=sys.stderr,
            )
            return 1
        try:
            packets.append(load_json(path))
        except (OSError, json.JSONDecodeError) as exc:
            load_errors.append(f"{path}: {exc}")
    if load_errors:
        for problem in load_errors:
            print(problem, file=sys.stderr)
        return 1

    board, problems = score_packets(packets, expectation)
    if problems or board is None:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    json.dump(board, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
