"""paper-evaluate-hot-packet-v0 fixture validator and example emitter.

Proposed paper evaluate→runners stamp. Input is one validated hot_packet_v0
object. No RPC, no observe client, no JSONL tail, no sealed-book scoring.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from tools.hot_packet_v0 import (
    CLOCK_SOURCE,
    HONESTY_FALSE_KEYS,
    PROPOSED_VALUES,
    example_enriched_global_95bps,
    example_enriched_launchlab,
    example_graph_slots_nullable,
    example_sealed_cold,
    validate_packet,
)

SCHEMA_VERSION = "paper_evaluate_hot_packet_v0"
STAMP_TYPE = "paper_evaluate_stamp"
STAMP_ID = "paper-evaluate-hot-packet-v0"
RULESET = "paper_evaluate_hot_packet_v0_rules"

HORIZONS: tuple[str, ...] = (
    "1s",
    "5s",
    "15s",
    "30s",
    "60s",
    "+2s",
    "+10s",
    "+5m",
    "peak",
    "drawdown",
)

REASON_ORDER: tuple[str, ...] = (
    "missing_signature",
    "missing_mint",
    "stage_not_bonding",
    "honesty_reject",
    "overlay_discipline",
)

GRAPH_LIFT_COLD = "not_used_graph_cold"
GRAPH_LIFT_SLOTS = "not_used_slots_not_scored"

SOFT_WATCH_SOURCE = "hot_packet_v0_soft_gate_pr49"
SOFT_WATCH_ITEMS: tuple[str, ...] = (
    "schema_looser_than_cli",
    "synthetic_launchlab_shape",
    "graph_slot_shape_not_a_score",
    "dec005_draft_unmerged",
    "sealed_book_rpc_slice_incomplete",
)

STAMP_HONESTY_FALSE: tuple[str, ...] = (
    "scored_oracle_measure",
    "continuous_observe_wiring",
    "encoder_promoted",
    "enum_production_lock",
    "discovery_promoted",
    "graph_lane_revived",
    "invented_lift",
    "exp002c_retuned",
    "live_capital",
    "trading_keys",
    "pumpportal_trade_api",
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

SEALED_DEFAULTS: dict[str, str] = {
    "fee": "unverified",
    "instr": "pending_rpc",
    "venue": "pump_program",
    "quote": "wsol_assumed",
    "market": "bonding_curve",
    "stage": "bonding",
}


def _missing_identity(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    stripped = value.strip()
    return stripped == "" or stripped == "UNK"


def _honesty_reject(packet: Mapping[str, Any]) -> bool:
    if packet.get("paper_only") is not True:
        return True
    honesty = packet.get("honesty")
    if not isinstance(honesty, Mapping):
        return True
    if honesty.get("registration") != "proposed":
        return True
    for key in HONESTY_FALSE_KEYS:
        if honesty.get(key) is not False:
            return True
    return False


def _overlay_discipline_failed(packet: Mapping[str, Any]) -> bool:
    regime = packet.get("regime")
    dual = packet.get("dual_read")
    kat = packet.get("knowable_at_t")
    if not isinstance(regime, Mapping) or not isinstance(dual, Mapping) or not isinstance(kat, Mapping):
        return True
    components = regime.get("components")
    lock = regime.get("component_lock")
    if not isinstance(components, Mapping) or not isinstance(lock, Mapping):
        return True
    overlay = dual.get("overlay")
    for key, proposed_values in PROPOSED_VALUES.items():
        value = components.get(key)
        if value in proposed_values:
            if lock.get(key) != "proposed" or overlay != "enriched":
                return True
    if kat.get("creator_verified") is not False:
        return True
    if overlay == "sealed":
        if kat.get("quote_verified") is True or kat.get("venue_verified") is True:
            return True
        for key, expected in SEALED_DEFAULTS.items():
            if components.get(key) != expected:
                return True
    return False


def evaluate_reasons(packet: Mapping[str, Any]) -> list[str]:
    """Rules-only reasons from packet fields. Empty list means runner.

    Honesty and overlay failures also fail hot-packet validation. The stamp
    path refuses those packets instead of emitting a scored reject.
    """
    reasons: list[str] = []
    l1 = packet.get("l1_spine")
    regime = packet.get("regime")
    components = regime.get("components") if isinstance(regime, Mapping) else None
    if not isinstance(l1, Mapping):
        reasons.extend(["missing_signature", "missing_mint", "stage_not_bonding"])
    else:
        if _missing_identity(l1.get("signature")):
            reasons.append("missing_signature")
        if _missing_identity(l1.get("mint")):
            reasons.append("missing_mint")
        regime_stage = components.get("stage") if isinstance(components, Mapping) else None
        if l1.get("stage") != "bonding" or regime_stage != "bonding":
            reasons.append("stage_not_bonding")
    if _honesty_reject(packet):
        reasons.append("honesty_reject")
    if _overlay_discipline_failed(packet):
        reasons.append("overlay_discipline")
    return [code for code in REASON_ORDER if code in reasons]


def _graph_lift_status(packet: Mapping[str, Any]) -> str:
    graph = packet.get("graph")
    if isinstance(graph, Mapping) and graph.get("cold") is False:
        return GRAPH_LIFT_SLOTS
    return GRAPH_LIFT_COLD


def _horizons() -> dict[str, None]:
    return {name: None for name in HORIZONS}


def _stamp_honesty() -> dict[str, Any]:
    body: dict[str, Any] = {"registration": "proposed"}
    for key in STAMP_HONESTY_FALSE:
        body[key] = False
    return body


def _build_stamp(packet: Mapping[str, Any]) -> dict[str, Any]:
    reasons = evaluate_reasons(packet)
    label = "reject" if reasons else "runner"
    clocks = packet["clocks"]
    return {
        "schema_version": SCHEMA_VERSION,
        "type": STAMP_TYPE,
        "id": STAMP_ID,
        "paper_only": True,
        "input": {
            "contract": "hot_packet_v0",
            "only_decode_input": True,
            "raw_ws_replay": False,
            "post_hoc_enrich_on_evaluate": False,
            "packet": copy.deepcopy(packet),
        },
        "evaluate_label": label,
        "reasons": reasons,
        "ruleset": RULESET,
        "knowable_at_t_only": True,
        "graph_lift": None,
        "graph_lift_status": _graph_lift_status(packet),
        "runner_stamp": {
            "kind": "paper_promotion" if label == "runner" else "reject_arm_retained",
            "pretend_buy": label == "runner",
            "live_capital": False,
            "T": clocks["T_decision"],
            "clock_source": CLOCK_SOURCE,
            "horizons": _horizons(),
            "horizon_status": "null_ok_no_marks_on_this_stamp",
            "delta_exec": {
                "field": "delta_exec",
                "also_called": "Δ_exec",
                "value": None,
                "status": "null_ok",
                "reason": "paper_stamp_has_no_fill",
            },
        },
        "full_book": {
            "policy": "dec007_both_arms_retained",
            "arm_retained": True,
            "deletes_detect_history": False,
        },
        "soft_watches": {
            "blocking": False,
            "source": SOFT_WATCH_SOURCE,
            "items": list(SOFT_WATCH_ITEMS),
        },
        "honesty": _stamp_honesty(),
    }


def stamp_from_packet(packet: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Build a stamp from one hot_packet_v0 object. Invalid packets do not stamp."""
    errors = validate_packet(packet)
    if errors:
        return None, errors
    reasons = evaluate_reasons(packet)
    if "honesty_reject" in reasons or "overlay_discipline" in reasons:
        return None, [
            "evaluate: honesty_reject or overlay_discipline on a packet hot-packet v0 accepted"
        ]
    return _build_stamp(packet), []


def _err(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def _reject_forbidden_keys(errors: list[str], obj: Any, path: str) -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if key.lower() in FORBIDDEN_KEYS:
                _err(errors, f"{path}.{key}", "forbidden on paper-evaluate-hot-packet-v0")
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
        if expected != actual and path != "input.packet":
            out.append(f"{path}: list mismatch")
        elif path == "input.packet":
            return
        return
    if expected != actual and path != "input.packet":
        out.append(f"{path}: expected {expected!r}")


def validate_stamp(stamp: Any) -> list[str]:
    """Return errors. Empty list means the stamp matches this contract."""
    errors: list[str] = []
    if not isinstance(stamp, Mapping):
        return ["stamp: must be a JSON object"]
    _reject_forbidden_keys(errors, stamp, "stamp")
    if errors:
        return errors
    inp = stamp.get("input")
    packet = inp.get("packet") if isinstance(inp, Mapping) else None
    if not isinstance(packet, Mapping):
        return ["input.packet: must be a hot_packet_v0 object"]
    packet_errors = validate_packet(packet)
    if packet_errors:
        return [f"input.packet: {problem}" for problem in packet_errors]
    expected = _build_stamp(packet)
    if stamp != expected:
        diffs: list[str] = []
        _diff_paths(expected, stamp, "", diffs)
        if not diffs:
            diffs.append("stamp: does not match evaluate rules for the embedded packet")
        errors.extend(diffs)
    return errors


def example_sealed_cold_runner() -> dict[str, Any]:
    stamp, errors = stamp_from_packet(example_sealed_cold())
    if stamp is None:
        raise RuntimeError(errors)
    return stamp


def example_enriched_fee_runner() -> dict[str, Any]:
    stamp, errors = stamp_from_packet(example_enriched_global_95bps())
    if stamp is None:
        raise RuntimeError(errors)
    return stamp


def example_enriched_launchlab_runner() -> dict[str, Any]:
    stamp, errors = stamp_from_packet(example_enriched_launchlab())
    if stamp is None:
        raise RuntimeError(errors)
    return stamp


def example_graph_slots_not_scored() -> dict[str, Any]:
    stamp, errors = stamp_from_packet(example_graph_slots_nullable())
    if stamp is None:
        raise RuntimeError(errors)
    return stamp


def example_reject_missing_mint() -> dict[str, Any]:
    packet = example_sealed_cold()
    packet["l1_spine"]["signature"] = "SigExampleRejectIdentity"
    packet["l1_spine"]["mint"] = "UNK"
    packet["provenance"]["parent_signature"] = "SigExampleRejectIdentity"
    packet["provenance"]["mint"] = "UNK"
    stamp, errors = stamp_from_packet(packet)
    if stamp is None:
        raise RuntimeError(errors)
    return stamp


EXAMPLES: dict[str, Any] = {
    "sealed-cold-runner": example_sealed_cold_runner,
    "enriched-fee-runner": example_enriched_fee_runner,
    "enriched-launchlab-runner": example_enriched_launchlab_runner,
    "graph-slots-not-scored": example_graph_slots_not_scored,
    "reject-missing-identity": example_reject_missing_mint,
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or emit paper-evaluate-hot-packet-v0 stamps "
            "(fixtures only; no RPC, no observe tail, no sealed-book score)."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    example = sub.add_parser("example", help="Print a synthetic stamp")
    example.add_argument("--which", choices=sorted(EXAMPLES), required=True)

    validate = sub.add_parser("validate", help="Validate one stamp JSON file")
    validate.add_argument("path", type=Path)

    evaluate = sub.add_parser(
        "evaluate",
        help="Stamp one local hot_packet_v0 JSON file (no JSONL, no RPC)",
    )
    evaluate.add_argument("path", type=Path)

    args = parser.parse_args(argv)
    if args.cmd == "example":
        stamp = EXAMPLES[args.which]()
        json.dump(stamp, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        problems = validate_stamp(stamp)
        if problems:
            print("example failed its own validator:", file=sys.stderr)
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        return 0

    path: Path = args.path
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return 1

    if args.cmd == "evaluate":
        stamp, problems = stamp_from_packet(payload)
        if problems or stamp is None:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        json.dump(stamp, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    problems = validate_stamp(payload)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print(f"ok {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
