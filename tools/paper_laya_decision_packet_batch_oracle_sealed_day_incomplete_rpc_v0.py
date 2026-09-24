"""paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0.

Proposed paper batch. Day-aligned manifests cite validated
paper_laya_precompute_decision_packet_v0 objects and count them with the
paper_laya_decision_packet_scoreboard_sealed_fixture_v0 path.

No RPC. No observe client. No measure exit. No closed-book claim.
Fixtures only; refuses /var/lib/mal lexically before any filesystem touch.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.paper_laya_decision_packet_scoreboard_sealed_fixture_v0 import (
    DELTA_JOIN_REASON,
    EXPECTATION_KIND,
    HOST_ROOT,
    HORIZON_JOIN_STATUS,
    SCHEMA_VERSION as SCOREBOARD_SCHEMA,
    SOFT_WATCH_ITEMS as INHERITED_SOFT_WATCH_ITEMS,
    _expectation_row_from_packet,
    validate_expectation,
)
import tools.paper_laya_decision_packet_scoreboard_sealed_fixture_v0 as _scoreboard_mod
from tools.paper_laya_precompute_decision_packet_v0 import (
    PREFIX_FILL_SIM,
    PREFIX_LOCK_RECEIPT,
    PREFIX_NON_FILL,
    assemble as assemble_decision_packet,
    load_json,
    validate_packet,
)

SCHEMA_VERSION = "paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0"
BATCH_TYPE = "paper_batch"
BATCH_ID = "paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0"
INCOMPLETE_REASON = "laya_decision_packet_batch_does_not_close_the_sealed_book"
MANIFEST_KIND = "decision_day_manifest"
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MANIFEST_NAME_RE = re.compile(r"^decision-day-(\d{4}-\d{2}-\d{2})\.json$")
EXAMPLE_DAYS: tuple[str, ...] = ("2026-09-20", "2026-09-21")
KNOWN_ROLLUP_DAYS: tuple[tuple[str, ...], ...] = (
    ("2026-09-20", "2026-09-21"),
    ("2026-09-20",),
    ("2026-09-21",),
)
FIXTURE_ORIGINS: tuple[str, ...] = ("synthetic",)

PIPELINE: tuple[str, ...] = (
    "paper_laya_precompute_decision_packet_v0",
    "paper_laya_decision_packet_scoreboard_sealed_fixture_v0",
)

SOFT_WATCH_SOURCE = (
    "hot_packet_v0_soft_gate_pr49_through_paper_laya_decision_packet_scoreboard_pr68_"
    "laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0"
)
INHERITED_FROM: tuple[str, ...] = (
    "paper_laya_precompute_decision_packet_v0_pr67",
    "paper_laya_decision_packet_scoreboard_sealed_fixture_v0_pr68",
)
LOCAL_SOFT_WATCH_ITEMS: tuple[str, ...] = (
    "decision_packet_batch_not_a_host_extract",
    "decision_packet_batch_counts_are_not_returns",
    "decision_packet_batch_not_risk_gate_unlock",
    "decision_packet_batch_not_laya_authorize_run",
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
        "PASS",
        "FAIL_NO_LIFT",
    }
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE_DIR = (
    REPO / "fixtures" / "paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0"
)
ALLOWED_DECISION_PREFIXES: tuple[str, ...] = (
    "fixtures/paper_laya_precompute_decision_packet_v0/",
)
ALLOWED_MANIFEST_PREFIX = "fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/"

DAY_20_PACKET_PATHS: tuple[str, ...] = (
    "fixtures/paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json",
    "fixtures/paper_laya_precompute_decision_packet_v0/decision_fill_sim_surround.json",
    "fixtures/paper_laya_precompute_decision_packet_v0/decision_mixed_spines.json",
)
DAY_21_PACKET_PATHS: tuple[str, ...] = (
    "fixtures/paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json",
    "fixtures/paper_laya_precompute_decision_packet_v0/decision_fill_sim_surround.json",
)
KNOWN_DECISION_PACKET_PATHS: frozenset[str] = frozenset(
    {*DAY_20_PACKET_PATHS, *DAY_21_PACKET_PATHS}
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


def _collapse_leading_slashes(posix_path: str) -> str:
    if posix_path.startswith("//"):
        return "/" + posix_path.lstrip("/")
    return posix_path


def _normpath_string_only(text: str) -> str:
    raw = text.replace("\\", "/").strip()
    return _collapse_leading_slashes(posixpath.normpath(raw))


def _lexical_under_host_root(posix_path: str) -> bool:
    normalized = _collapse_leading_slashes(posixpath.normpath(posix_path))
    return normalized == HOST_ROOT or normalized.startswith(HOST_ROOT + "/")


def _relative_joined_under_host_root(text: str, cwd: str) -> bool:
    joined = posixpath.join(cwd, text)
    return _lexical_under_host_root(joined)


def _score_packets_for_calendar_day(
    packets: Any, expectation: Any
) -> tuple[dict[str, Any] | None, list[str]]:
    """Score one day using #68 path without rewriting parent CLI (calendar-day identity)."""
    if not isinstance(expectation, Mapping):
        return None, ["expectation: must be a JSON object"]
    sealed_day = expectation.get("day")
    if not isinstance(sealed_day, str):
        return None, ["expectation.day: must be YYYY-MM-DD"]

    def _identity(packet: Mapping[str, Any]) -> tuple[str, str, str]:
        return (
            sealed_day,
            _scoreboard_mod._spine_profile(packet),
            _scoreboard_mod._assembly_fingerprint(packet),
        )

    def _join_status(
        packet: Mapping[str, Any],
        exp_rows: Mapping[tuple[str, str, str], Mapping[str, Any]],
    ) -> str:
        key = _identity(packet)
        row = exp_rows.get(key)
        if row is None:
            return _scoreboard_mod.JOIN_UNMATCHED
        if row.get("spine_profile") != _scoreboard_mod._spine_profile(packet):
            return _scoreboard_mod.JOIN_SPINE
        total, runner, reject = _scoreboard_mod._digest_totals(packet)
        if (
            int(row.get("digest_n", -1)) != total
            or int(row.get("digest_runner_n", -1)) != runner
            or int(row.get("digest_reject_n", -1)) != reject
        ):
            return _scoreboard_mod.JOIN_DIGEST
        rg = packet.get("risk_gate", {})
        if not isinstance(rg, Mapping) or rg.get("decision") != row.get("risk_gate_decision"):
            return _scoreboard_mod.JOIN_RISK_GATE
        return _scoreboard_mod.JOIN_MATCHED

    original_build = _scoreboard_mod._build_scoreboard

    def _build_scoreboard(
        packet_list: Sequence[Mapping[str, Any]], exp: Mapping[str, Any]
    ) -> dict[str, Any]:
        board = original_build(packet_list, exp)
        board["sealed_days"] = [
            {**board["sealed_days"][0], "day": sealed_day},
        ]
        return board

    saved = (
        _scoreboard_mod._identity,
        _scoreboard_mod._join_status,
        _scoreboard_mod._build_scoreboard,
    )
    _scoreboard_mod._identity = _identity
    _scoreboard_mod._join_status = _join_status
    _scoreboard_mod._build_scoreboard = _build_scoreboard
    try:
        return _scoreboard_mod.score_packets(packets, expectation)
    finally:
        _scoreboard_mod._identity = saved[0]
        _scoreboard_mod._join_status = saved[1]
        _scoreboard_mod._build_scoreboard = saved[2]


def _decision_repo_path_refusal(text: str) -> str | None:
    """Lexical refuse before FS: host root, absolute, .., collapse, backslash."""
    refusal = _batch_path_refusal(text)
    if refusal:
        return refusal
    raw = text.replace("\\", "/")
    if "\\" in text:
        return f"{text}: backslash paths are refused lexically"
    if raw != text.strip():
        return f"{text}: path must not have leading or trailing whitespace"
    if raw.startswith("./") or "/./" in raw or raw.endswith("/"):
        return f"{text}: non-canonical repo-relative path"
    if raw.startswith("//") or "///" in raw:
        return f"{text}: non-canonical repo-relative path"
    if ".." in raw.split("/"):
        return f"{text}: parent-segment paths are refused"
    if posixpath.isabs(raw):
        return f"{text}: absolute paths are refused"
    return None


def _batch_path_refusal(text: str) -> str | None:
    norm = _normpath_string_only(text)
    if _lexical_under_host_root(norm):
        return f"{text}: does not open {HOST_ROOT}"
    if posixpath.isabs(norm):
        return None
    if ".." in text.replace("\\", "/").split("/"):
        collapsed = _collapse_leading_slashes(posixpath.normpath(text.replace("\\", "/")))
        if _lexical_under_host_root(collapsed):
            return f"{text}: does not open {HOST_ROOT}"
    for cwd in ("/", os.getcwd()):
        if _relative_joined_under_host_root(text, cwd):
            return f"{text}: does not open {HOST_ROOT}"
    return None


def _repo_relative_allowed(text: str, prefixes: Sequence[str]) -> bool:
    if _decision_repo_path_refusal(text):
        return False
    raw = text.replace("\\", "/").strip()
    if raw != text.replace("\\", "/").strip():
        return False
    if raw.startswith("./") or raw.endswith("/") or "//" in raw or "/./" in raw:
        return False
    if ".." in raw.split("/"):
        return False
    if posixpath.isabs(raw):
        return False
    path = Path(raw)
    if not path.is_absolute():
        try:
            rel = (REPO / path).resolve().relative_to(REPO.resolve())
        except ValueError:
            return False
        rel_str = rel.as_posix()
        return any(rel_str.startswith(prefix) for prefix in prefixes)
    return False


def _decision_path_allowed(text: str) -> bool:
    if text not in KNOWN_DECISION_PACKET_PATHS:
        return False
    return _repo_relative_allowed(text, ALLOWED_DECISION_PREFIXES)


def _expected_paths_for_day(day: str) -> tuple[str, ...] | None:
    if day == "2026-09-20":
        return DAY_20_PACKET_PATHS
    if day == "2026-09-21":
        return DAY_21_PACKET_PATHS
    return None


def _manifest_path_allowed(text: str) -> bool:
    return _repo_relative_allowed(text, (ALLOWED_MANIFEST_PREFIX,))


def validate_manifest(manifest: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, Mapping):
        return ["manifest: must be a JSON object"]
    if manifest.get("schema_version") != SCHEMA_VERSION:
        _err(errors, "manifest.schema_version", f"must be {SCHEMA_VERSION}")
    if manifest.get("fixture_kind") != MANIFEST_KIND:
        _err(errors, "manifest.fixture_kind", f"must be {MANIFEST_KIND}")
    if manifest.get("origin") != "synthetic":
        _err(errors, "manifest.origin", "checked-in manifest is synthetic")
    day = manifest.get("day")
    if not isinstance(day, str) or DAY_RE.fullmatch(day) is None:
        _err(errors, "manifest.day", "must be YYYY-MM-DD")
    assemblies = manifest.get("assemblies")
    paths = manifest.get("decision_packet_paths")
    has_assemblies = isinstance(assemblies, list) and bool(assemblies)
    has_paths = isinstance(paths, list) and bool(paths)
    if has_assemblies == has_paths:
        _err(
            errors,
            "manifest",
            "exactly one of assemblies or decision_packet_paths must be a non-empty list",
        )
        return errors
    if has_paths:
        assert isinstance(paths, list)
        expected_paths = _expected_paths_for_day(str(day)) if isinstance(day, str) else None
        if expected_paths is not None and tuple(paths) != expected_paths:
            _err(
                errors,
                "manifest.decision_packet_paths",
                "must match the checked-in fixture path list for this calendar day",
            )
        for index, item in enumerate(paths):
            if not isinstance(item, str):
                _err(errors, f"manifest.decision_packet_paths[{index}]", "must be a string")
                continue
            refusal = _decision_repo_path_refusal(item)
            if refusal:
                _err(errors, f"manifest.decision_packet_paths[{index}]", refusal)
                continue
            if item not in KNOWN_DECISION_PACKET_PATHS:
                _err(
                    errors,
                    f"manifest.decision_packet_paths[{index}]",
                    "must cite a known checked-in decision-packet fixture path",
                )
                continue
            if not _decision_path_allowed(item):
                _err(
                    errors,
                    f"manifest.decision_packet_paths[{index}]",
                    f"must be a repo path under {ALLOWED_DECISION_PREFIXES}",
                )
    if has_assemblies:
        _err(
            errors,
            "manifest",
            "assemblies mode is fail-closed on this stamp; use decision_packet_paths",
        )
        return errors
    return errors


def _packets_from_manifest(manifest: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    packets: list[dict[str, Any]] = []
    paths = manifest.get("decision_packet_paths")
    assemblies = manifest.get("assemblies")
    if isinstance(paths, list):
        for index, rel_path in enumerate(paths):
            if not isinstance(rel_path, str):
                continue
            refusal = _decision_repo_path_refusal(rel_path)
            if refusal:
                errors.append(refusal)
                continue
            if not _decision_path_allowed(rel_path):
                errors.append(
                    f"manifest.decision_packet_paths[{index}]: "
                    f"must stay under {ALLOWED_DECISION_PREFIXES}"
                )
                continue
            try:
                payload = load_json(REPO / rel_path)
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{rel_path}: {exc}")
                continue
            packet_errors = validate_packet(payload)
            if packet_errors:
                errors.extend(f"{rel_path}: {item}" for item in packet_errors)
                continue
            packets.append(payload)
    elif isinstance(assemblies, list):
        for index, spec in enumerate(assemblies):
            if not isinstance(spec, Mapping):
                continue
            surround_paths = spec.get("surround_paths")
            lock_receipt_paths = spec.get("lock_receipt_paths")
            if not isinstance(surround_paths, list) or not isinstance(lock_receipt_paths, list):
                continue
            for candidate in [*surround_paths, *lock_receipt_paths]:
                if isinstance(candidate, str):
                    refusal = _decision_repo_path_refusal(candidate)
                    if refusal:
                        errors.append(refusal)
            packet, problems = assemble_decision_packet(
                surround_paths=[str(item) for item in surround_paths],
                lock_receipt_paths=[str(item) for item in lock_receipt_paths],
            )
            if problems or packet is None:
                errors.extend(
                    f"manifest.assemblies[{index}]: {item}" for item in problems
                )
                continue
            packets.append(packet)
    if errors:
        return [], errors
    if not packets:
        return [], ["manifest: no decision packets loaded"]
    return packets, []


def expectation_from_packets(day: str, packets: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [_expectation_row_from_packet(packet) for packet in packets]
    for row in rows:
        row["source_day"] = day
    rows.sort(key=lambda row: (row["source_day"], row["spine_profile"], row["assembly_fingerprint"]))
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


def manifest_for_day(day: str) -> dict[str, Any]:
    if day == "2026-09-20":
        paths = list(DAY_20_PACKET_PATHS)
    elif day == "2026-09-21":
        paths = list(DAY_21_PACKET_PATHS)
    else:
        raise ValueError(day)
    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_kind": MANIFEST_KIND,
        "origin": "synthetic",
        "day": day,
        "decision_packet_paths": paths,
    }


def project_day(
    manifest: Mapping[str, Any],
    *,
    day: str,
    fixture_origin: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    errors: list[str] = []
    if manifest.get("day") != day:
        return [], {}, [f"{day}: manifest.day must equal the calendar day"]
    manifest_errors = validate_manifest(manifest)
    if manifest_errors:
        return [], {}, manifest_errors
    packets, load_errors = _packets_from_manifest(manifest)
    if load_errors:
        return [], {}, load_errors
    paths = manifest.get("decision_packet_paths")
    assemblies = manifest.get("assemblies")
    listed_n = len(paths) if isinstance(paths, list) else len(assemblies or [])
    census = {
        "listed_n": listed_n,
        "loaded_n": len(packets),
        "skipped_n": 0,
        "skip_is_not_a_dropped_spine_arm": True,
    }
    if not packets:
        return [], {}, [f"{day}: no validated decision packets"]
    return packets, census, []


def assemble(
    day_inputs: Any,
    *,
    fixture_origin: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    if fixture_origin not in FIXTURE_ORIGINS:
        return None, [f"fixture_origin: must be one of {list(FIXTURE_ORIGINS)}"]
    if not isinstance(day_inputs, list) or not day_inputs:
        return None, ["days: at least one day-aligned manifest is required"]
    errors: list[str] = []
    normalized: list[Mapping[str, Any]] = []
    seen_days: set[str] = set()
    for index, item in enumerate(day_inputs):
        path = f"input.days[{index}]"
        if not isinstance(item, Mapping):
            _err(errors, path, "must be an object")
            continue
        day = item.get("day")
        manifest_name = item.get("manifest_name")
        if not isinstance(day, str) or DAY_RE.fullmatch(day) is None:
            _err(errors, f"{path}.day", "must be YYYY-MM-DD")
            continue
        if manifest_name != f"decision-day-{day}.json":
            _err(errors, f"{path}.manifest_name", "must be decision-day-YYYY-MM-DD.json")
        if day in seen_days:
            _err(errors, f"{path}.day", "duplicate day")
        seen_days.add(day)
        normalized.append(item)
    if errors:
        return None, errors

    ordered = sorted(normalized, key=lambda item: str(item["day"]))
    input_days: list[dict[str, Any]] = []
    output_days: list[dict[str, Any]] = []
    total_digest = 0
    runner_n = 0
    reject_n = 0
    packet_n = 0

    for item in ordered:
        day = str(item["day"])
        manifest = item.get("manifest")
        expectation = item.get("expectation")
        if not isinstance(manifest, Mapping):
            _err(errors, f"{day}.manifest", "must be an object")
            continue
        problems = validate_expectation(expectation)
        if problems:
            errors.extend(f"{day}.expectation: {problem}" for problem in problems)
            continue
        assert isinstance(expectation, Mapping)
        if expectation.get("day") != day:
            _err(errors, f"{day}.expectation.day", "must equal the manifest day")
            continue
        packets, census, project_errors = project_day(
            manifest, day=day, fixture_origin=fixture_origin
        )
        if project_errors:
            errors.extend(project_errors)
            continue
        canonical_expectation = expectation_from_packets(day, packets)
        if expectation != canonical_expectation:
            _err(
                errors,
                f"{day}.expectation",
                "must match the checked-in fixture expectation for this manifest composition",
            )
            continue
        board, score_errors = _score_packets_for_calendar_day(packets, expectation)
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
        if board["graph_lift"] is not None or board["graph_policy"] != "cold":
            _err(errors, f"{day}.scoreboard.graph_lift", "graph stays cold on this stamp")
        if board["full_book"]["reject_stamps_dropped"] != 0:
            _err(errors, f"{day}.scoreboard.full_book.reject_stamps_dropped", "must stay 0")
        laya = board.get("laya")
        rg = board.get("risk_gate")
        if not isinstance(laya, Mapping) or any(
            laya.get(key) is not False for key in ("authorize_run", "risk_gate_unlock", "live_trading")
        ):
            _err(errors, f"{day}.scoreboard.laya", "LAYA caps must stay false")
        if (
            not isinstance(rg, Mapping)
            or rg.get("decision") != "locked"
            or rg.get("unlock") is not False
        ):
            _err(errors, f"{day}.scoreboard.risk_gate", "must stay locked with unlock false")
        input_days.append(
            {
                "day": day,
                "manifest_name": f"decision-day-{day}.json",
                "manifest": manifest,
                "expectation": expectation,
            }
        )
        output_days.append(
            {
                "day": day,
                "manifest_name": f"decision-day-{day}.json",
                "fixture_origin": fixture_origin,
                "packet_census": census,
                "scoreboard": board,
            }
        )
        fb = board["full_book"]
        packet_n += int(fb["packet_n"])
        total_digest += int(fb["digest_stamp_n"])
        runner_n += int(fb["digest_runner_n"])
        reject_n += int(fb["digest_reject_n"])

    if errors:
        return None, errors

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
            "host_jsonl_read": False,
            "marks_joined": False,
            "fixture_origin": fixture_origin,
            "graph_policy": "cold",
            "days": input_days,
        },
        "days": output_days,
        "rollup": {
            "n_meaning": "digest_stamp_count_not_a_return",
            "share_kind": "count_fraction_not_a_return",
            "counts_are_not_returns": True,
            "packet_n": packet_n,
            "n": total_digest,
            "runner_n": runner_n,
            "reject_n": reject_n,
            "rows": [
                {"label": "runner", "n": runner_n, "share": _share(runner_n, total_digest or 1)},
                {"label": "reject", "n": reject_n, "share": _share(reject_n, total_digest or 1)},
            ],
            "days": [item["day"] for item in output_days],
        },
        "full_book": {
            "policy": "dec007_both_arms_retained",
            "arm_retained": True,
            "deletes_detect_history": False,
            "reject_stamps_dropped": 0,
            "local_set_is_not_the_sealed_book": True,
            "packet_n": packet_n,
            "digest_stamp_n": total_digest,
            "digest_runner_n": runner_n,
            "digest_reject_n": reject_n,
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


def _reject_forbidden_keys(errors: list[str], obj: Any, path: str) -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            if str(key).lower() in FORBIDDEN_KEYS or key in FORBIDDEN_KEYS:
                _err(
                    errors,
                    child,
                    "forbidden on paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0",
                )
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


def _rollup_days_tuple(rollup: Any) -> tuple[str, ...] | None:
    if not isinstance(rollup, Mapping):
        return None
    days = rollup.get("days")
    if not isinstance(days, list) or not days:
        return None
    if not all(isinstance(day, str) for day in days):
        return None
    return tuple(days)


def validate_batch(batch: Any) -> list[str]:
    if not isinstance(batch, Mapping):
        return ["batch: must be a JSON object"]
    errors: list[str] = []
    _reject_forbidden_keys(errors, batch, "batch")
    if errors:
        return errors
    rollup_days = _rollup_days_tuple(batch.get("rollup"))
    if rollup_days is None or rollup_days not in KNOWN_ROLLUP_DAYS:
        errors.append("rollup.days: must be a known sealed-day composition")
        return errors
    inp = batch.get("input")
    if not isinstance(inp, Mapping):
        return ["input: must be an object"]
    if inp.get("marks_joined") is True:
        return ["input.marks_joined: must stay false"]
    expected, problems = assemble(inp.get("days"), fixture_origin=inp.get("fixture_origin"))
    if problems or expected is None:
        return problems or ["batch: embedded days did not rebuild"]
    if batch != expected:
        diffs: list[str] = []
        _diff_paths(expected, batch, "", diffs)
        if not diffs:
            diffs.append("batch: does not match the manifest projection")
        return diffs
    return []


def day_from_manifest_name(path: Path) -> str | None:
    match = MANIFEST_NAME_RE.fullmatch(path.name)
    if match is None:
        return None
    return match.group(1)


def read_manifest(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    refusal = _batch_path_refusal(str(path))
    if refusal:
        return None, [refusal]
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"{path}: {exc}"]
    if not isinstance(payload, Mapping):
        return None, [f"{path}: manifest must be a JSON object"]
    return dict(payload), []


def _specs_from_files(
    pairs: Sequence[tuple[Path, Path]],
) -> tuple[list[dict[str, Any]], list[str]]:
    specs: list[dict[str, Any]] = []
    errors: list[str] = []
    for manifest_path, expectation_path in pairs:
        for candidate in (manifest_path, expectation_path):
            refusal = _batch_path_refusal(str(candidate))
            if refusal:
                errors.append(refusal)
                continue
        day = day_from_manifest_name(manifest_path)
        if day is None:
            errors.append(f"{manifest_path}: basename must be decision-day-YYYY-MM-DD.json")
            continue
        manifest, manifest_errors = read_manifest(manifest_path)
        if manifest_errors or manifest is None:
            errors.extend(manifest_errors)
            continue
        if manifest.get("day") != day:
            errors.append(f"{manifest_path}: manifest.day must equal basename day")
            continue
        try:
            expectation = load_json(expectation_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{expectation_path}: {exc}")
            continue
        specs.append(
            {
                "day": day,
                "manifest_name": manifest_path.name,
                "manifest": manifest,
                "expectation": expectation,
            }
        )
    if errors:
        return [], errors
    return specs, []


def example_two_day() -> dict[str, Any]:
    specs: list[dict[str, Any]] = []
    for day in EXAMPLE_DAYS:
        manifest = manifest_for_day(day)
        packets, _census, problems = project_day(manifest, day=day, fixture_origin="synthetic")
        if problems:
            raise RuntimeError(problems)
        specs.append(
            {
                "day": day,
                "manifest_name": f"decision-day-{day}.json",
                "manifest": manifest,
                "expectation": expectation_from_packets(day, packets),
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
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def _pair_inputs(
    manifest_paths: Sequence[Path], expectation_paths: Sequence[Path]
) -> tuple[list[tuple[Path, Path]], list[str]]:
    if not manifest_paths or not expectation_paths:
        return [], ["batch: pass at least one --manifest and one --expectation"]
    by_manifest: dict[str, Path] = {}
    errors: list[str] = []
    for path in manifest_paths:
        day = day_from_manifest_name(path)
        if day is None:
            errors.append(f"{path}: basename must be decision-day-YYYY-MM-DD.json")
            continue
        if day in by_manifest:
            errors.append(f"{path}: duplicate day {day}")
            continue
        by_manifest[day] = path
    by_expectation: dict[str, Path] = {}
    for path in expectation_paths:
        refusal = _batch_path_refusal(str(path))
        if refusal:
            errors.append(refusal)
            continue
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
    if set(by_manifest) != set(by_expectation):
        return [], [
            "batch: manifest days and expectation days must match "
            f"(manifest={sorted(by_manifest)} expectation={sorted(by_expectation)})"
        ]
    pairs = [(by_manifest[day], by_expectation[day]) for day in sorted(by_manifest)]
    return pairs, []


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="python -m tools.paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0",
        description=(
            "Day-aligned fixtures-only batch: cite validated LAYA precompute decision "
            "packets (#67) and count with the decision-packet scoreboard path (#68). "
            "Sealed book stays incomplete. No RPC, no observe tail, no measure exit."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    example = sub.add_parser("example", help="Print the synthetic two-day batch")
    example.add_argument("--which", choices=sorted(EXAMPLES), required=True)

    validate = sub.add_parser("validate", help="Validate one batch JSON file")
    validate.add_argument("path", type=Path)

    batch_cmd = sub.add_parser(
        "batch",
        help="Score local decision-day-YYYY-MM-DD.json manifests and print one batch",
    )
    batch_cmd.add_argument("--manifest", type=Path, action="append", required=True)
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
        refusal = _batch_path_refusal(str(path))
        if refusal:
            print(refusal, file=sys.stderr)
            return 1
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

    for candidate in (*args.manifest, *args.expectation):
        refusal = _batch_path_refusal(str(candidate))
        if refusal:
            print(refusal, file=sys.stderr)
            return 1

    pairs, pair_errors = _pair_inputs(args.manifest, args.expectation)
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
