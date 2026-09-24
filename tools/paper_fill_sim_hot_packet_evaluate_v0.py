"""paper-fill-sim-hot-packet-evaluate-v0.

Proposed registration. Binds EXP-006-style fill-sim honesty (documented
constants only) onto validated paper_evaluate_hot_packet_v0 stamps.

Does not run the EXP-006 Oracle harness, does not join marks, does not open
/var/lib/mal, and does not rewrite parent CLIs (#49–#55 or EXP-006).
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from tools.paper_evaluate_hot_packet_v0 import (
    HORIZONS,
    example_enriched_fee_runner,
    example_enriched_launchlab_runner,
    example_graph_slots_not_scored,
    example_reject_missing_mint,
    example_sealed_cold_runner,
    validate_stamp as validate_evaluate_stamp,
)
from tools.paper_host_local_dry_run_receipt_capture_v0 import (
    INHERITED_FROM as CAPTURE_INHERITED_FROM,
    SOFT_WATCH_ITEMS as CAPTURE_SOFT_WATCH_ITEMS,
)

SCHEMA_VERSION = "paper_fill_sim_hot_packet_evaluate_v0"
STAMP_TYPE = "paper_fill_sim_stamp"
STAMP_ID = "paper-fill-sim-hot-packet-evaluate-v0"

PARENT_EVALUATE_ID = "paper-evaluate-hot-packet-v0"
PARENT_EVALUATE_SCHEMA = "paper_evaluate_hot_packet_v0"
PARENT_EVALUATE_PR = 50
PARENT_EVALUATE_COMMIT = "224166c"
PARENT_EVALUATE_CLI = "python -m tools.paper_evaluate_hot_packet_v0"

EXP006_ID = "EXP-006-paper-would-have-happened-harness-v0"
EXP006_CLI = "python -m tools.exp006_paper_fill_sim"

FILL_LATENCY_MS = 500
PAPER_SIZE_SOL = 0.1
FEE_MODEL_ID = "pump_assumed_bps_v0"
TOTAL_FEE_BPS = 125.0
MAX_SLIPPAGE_BPS = 500.0
SIM_REJECT_ON_SLIP = True

FILL_SIM_RUNNER = "documented_model_only"
FILL_SIM_REJECT = "reject_arm_no_pretend_buy"
HORIZON_STATUS = "fill_sim_registration_null_explicit"
DELTA_REASON = "fill_sim_stamp_has_no_scored_delta"
INCOMPLETE_REASON = "fill_sim_bind_does_not_close_the_sealed_book"

SOFT_WATCH_SOURCE = (
    "hot_packet_v0_soft_gate_pr49_paper_evaluate_pr50_scoreboard_pr51_"
    "batch_pr52_dry_run_pr53_schema_align_pr54_dry_run_capture_pr55_"
    "fill_sim_hot_packet_evaluate_v0"
)
INHERITED_FROM: tuple[str, ...] = CAPTURE_INHERITED_FROM + (
    "paper_host_local_dry_run_receipt_capture_v0_pr55",
)
LOCAL_SOFT_WATCH_ITEMS: tuple[str, ...] = (
    "fill_sim_vocabulary_not_exp006_promote",
    "documented_constants_not_a_scored_fill",
    "parent_evaluate_cli_not_rewritten",
    "exp006_harness_not_rewritten",
)
SOFT_WATCH_ITEMS: tuple[str, ...] = CAPTURE_SOFT_WATCH_ITEMS + LOCAL_SOFT_WATCH_ITEMS

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
FIXTURE_ROOT = REPO / "fixtures"
ALLOWED_SIM_PREFIXES: tuple[str, ...] = (
    "fixtures/paper_evaluate_hot_packet_v0/",
    "fixtures/paper_fill_sim_hot_packet_evaluate_v0/",
)


def _err(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def _null_horizons() -> dict[str, None]:
    return {name: None for name in HORIZONS}


def _honesty() -> dict[str, Any]:
    body: dict[str, Any] = {"registration": "proposed"}
    for key in HONESTY_FALSE:
        body[key] = False
    return body


def _fill_model() -> dict[str, Any]:
    return {
        "latency_ms": FILL_LATENCY_MS,
        "paper_size_sol": PAPER_SIZE_SOL,
        "fee_model_id": FEE_MODEL_ID,
        "total_fee_bps": TOTAL_FEE_BPS,
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
        "sim_reject_on_slip": SIM_REJECT_ON_SLIP,
        "source": "documented_constants_exp006_p0",
        "fitted_to_lift": False,
        "vocabulary_from": EXP006_ID,
    }


def _exp006_vocabulary() -> dict[str, Any]:
    return {
        "exp_id": EXP006_ID,
        "cli": EXP006_CLI,
        "harness_rewritten": False,
        "oracle_run": False,
        "promoted": False,
        "vocabulary_only": True,
    }


def _parent_evaluate() -> dict[str, Any]:
    return {
        "id": PARENT_EVALUATE_ID,
        "schema_version": PARENT_EVALUATE_SCHEMA,
        "pull_request": PARENT_EVALUATE_PR,
        "commit": PARENT_EVALUATE_COMMIT,
        "cli": PARENT_EVALUATE_CLI,
        "rewritten_by_this_stamp": False,
    }


def _evaluate_guards(evaluate_stamp: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if evaluate_stamp.get("graph_lift") is not None:
        _err(errors, "input.stamp.graph_lift", "must stay null")
    runner = evaluate_stamp.get("runner_stamp")
    if not isinstance(runner, Mapping):
        _err(errors, "input.stamp.runner_stamp", "missing")
        return errors
    horizons = runner.get("horizons")
    if not isinstance(horizons, Mapping) or any(
        horizons.get(name) is not None for name in HORIZONS
    ):
        _err(errors, "input.stamp.runner_stamp.horizons", "null is legal; a number is not a return")
    if runner.get("horizon_status") != "null_ok_no_marks_on_this_stamp":
        _err(errors, "input.stamp.runner_stamp.horizon_status", "must stay null_ok_no_marks_on_this_stamp")
    delta = runner.get("delta_exec")
    if not isinstance(delta, Mapping) or delta.get("value") is not None:
        _err(errors, "input.stamp.runner_stamp.delta_exec", "null_ok only")
    packet = evaluate_stamp.get("input", {}).get("packet") if isinstance(
        evaluate_stamp.get("input"), Mapping
    ) else None
    dual = packet.get("dual_read") if isinstance(packet, Mapping) else None
    if not isinstance(dual, Mapping) or dual.get("sealed_book_rpc_slice") != "incomplete":
        _err(errors, "input.stamp.input.packet.dual_read.sealed_book_rpc_slice", "must stay incomplete")
    if dual is not None and dual.get("closed_book_claim") is True:
        _err(errors, "input.stamp.input.packet.dual_read.closed_book_claim", "must not be true")
    return errors


def _fill_sim_block(evaluate_stamp: Mapping[str, Any]) -> dict[str, Any]:
    label = evaluate_stamp["evaluate_label"]
    pretend = evaluate_stamp["runner_stamp"]["pretend_buy"]
    if label == "runner":
        status = FILL_SIM_RUNNER
        notes = "Documented EXP-006 P0 constants only. No marks joined. No scored fill on this registration."
    else:
        status = FILL_SIM_REJECT
        notes = "Reject arm retained (DEC-007). No pretend buy. Fill-sim constants are vocabulary only."
    return {
        "status": status,
        "pretend_buy": pretend,
        "fill_status": None,
        "t_fill": None,
        "fill_price_proxy": None,
        "fee_sol": None,
        "slippage_bps": None,
        "notes": notes,
    }


def bind_from_evaluate_stamp(
    evaluate_stamp: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Attach fill-sim honesty to one validated paper_evaluate_hot_packet_v0 stamp."""
    problems = validate_evaluate_stamp(evaluate_stamp)
    if problems:
        return None, [f"input.stamp: {item}" for item in problems]
    guard = _evaluate_guards(evaluate_stamp)
    if guard:
        return None, guard
    evaluate_copy = copy.deepcopy(evaluate_stamp)
    label = evaluate_copy["evaluate_label"]
    reasons = list(evaluate_copy["reasons"])
    graph_status = evaluate_copy["graph_lift_status"]
    return {
        "schema_version": SCHEMA_VERSION,
        "type": STAMP_TYPE,
        "id": STAMP_ID,
        "paper_only": True,
        "input": {
            "contract": "paper_evaluate_hot_packet_v0",
            "local_json_only": True,
            "rpc": False,
            "observe_jsonl_tail": False,
            "host_extract_required": False,
            "marks_joined": False,
            "stamp": evaluate_copy,
        },
        "parent_evaluate": _parent_evaluate(),
        "exp006_vocabulary": _exp006_vocabulary(),
        "evaluate_label": label,
        "reasons": reasons,
        "fill_model": _fill_model(),
        "fill_sim": _fill_sim_block(evaluate_copy),
        "graph_lift": None,
        "graph_lift_status": graph_status,
        "graph_policy": "cold",
        "horizons": {
            "status": HORIZON_STATUS,
            "null_is_not_zero_return": True,
            "source": "parent_evaluate_runner_stamp_and_fill_sim_registration",
            "values": _null_horizons(),
        },
        "delta_exec": {
            "field": "delta_exec",
            "also_called": "Δ_exec",
            "value": None,
            "status": HORIZON_STATUS,
            "reason": DELTA_REASON,
            "null_is_not_zero_cost": True,
        },
        "dual_read": {
            "sealed_book_rpc_slice": "incomplete",
            "closed_book_claim": False,
            "incomplete_reason": INCOMPLETE_REASON,
        },
        "full_book": {
            "policy": "dec007_both_arms_retained",
            "arm_retained": True,
            "deletes_detect_history": False,
            "reject_stamps_dropped": 0,
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
            if key.lower() in FORBIDDEN_KEYS or key in FORBIDDEN_KEYS:
                _err(errors, f"{path}.{key}", "forbidden on paper-fill-sim-hot-packet-evaluate-v0")
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
        if expected != actual and path != "input.stamp":
            out.append(f"{path}: list mismatch")
        return
    if expected != actual and path != "input.stamp":
        out.append(f"{path}: expected {expected!r}")


def validate_fill_sim_stamp(stamp: Any) -> list[str]:
    """Return errors. Empty list means the stamp matches this contract."""
    errors: list[str] = []
    if not isinstance(stamp, Mapping):
        return ["stamp: must be a JSON object"]
    _reject_forbidden_keys(errors, stamp, "stamp")
    if errors:
        return errors
    inp = stamp.get("input")
    evaluate = inp.get("stamp") if isinstance(inp, Mapping) else None
    if not isinstance(evaluate, Mapping):
        return ["input.stamp: must be a paper_evaluate_hot_packet_v0 object"]
    expected, problems = bind_from_evaluate_stamp(evaluate)
    if problems or expected is None:
        return problems or ["input.stamp: could not rebuild fill-sim bind"]
    if stamp != expected:
        diffs: list[str] = []
        _diff_paths(expected, stamp, "", diffs)
        if not diffs:
            diffs.append("stamp: does not match bind rules for the embedded evaluate stamp")
        errors.extend(diffs)
    return errors


def _sim_path_allowed(path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(REPO.resolve())
    except ValueError:
        return False
    rel_str = rel.as_posix()
    return any(rel_str.startswith(prefix) for prefix in ALLOWED_SIM_PREFIXES)


def example_sealed_cold_runner_bind() -> dict[str, Any]:
    stamp, errors = bind_from_evaluate_stamp(example_sealed_cold_runner())
    if stamp is None:
        raise RuntimeError(errors)
    return stamp


def example_enriched_fee_runner_bind() -> dict[str, Any]:
    stamp, errors = bind_from_evaluate_stamp(example_enriched_fee_runner())
    if stamp is None:
        raise RuntimeError(errors)
    return stamp


def example_enriched_launchlab_runner_bind() -> dict[str, Any]:
    stamp, errors = bind_from_evaluate_stamp(example_enriched_launchlab_runner())
    if stamp is None:
        raise RuntimeError(errors)
    return stamp


def example_graph_slots_bind() -> dict[str, Any]:
    stamp, errors = bind_from_evaluate_stamp(example_graph_slots_not_scored())
    if stamp is None:
        raise RuntimeError(errors)
    return stamp


def example_reject_identity_bind() -> dict[str, Any]:
    stamp, errors = bind_from_evaluate_stamp(example_reject_missing_mint())
    if stamp is None:
        raise RuntimeError(errors)
    return stamp


EXAMPLES: dict[str, Any] = {
    "sealed-cold-runner": example_sealed_cold_runner_bind,
    "enriched-fee-runner": example_enriched_fee_runner_bind,
    "enriched-launchlab-runner": example_enriched_launchlab_runner_bind,
    "graph-slots-not-scored": example_graph_slots_bind,
    "reject-missing-identity": example_reject_identity_bind,
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or emit paper-fill-sim-hot-packet-evaluate-v0 stamps "
            "(fixtures only; no RPC, no marks join, no EXP-006 Oracle run)."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    example = sub.add_parser("example", help="Print a synthetic fill-sim bind stamp")
    example.add_argument("--which", choices=sorted(EXAMPLES), required=True)

    validate = sub.add_parser("validate", help="Validate one fill-sim stamp JSON file")
    validate.add_argument("path", type=Path)

    sim = sub.add_parser(
        "sim",
        help="Bind fill-sim honesty onto a local paper_evaluate_hot_packet_v0 JSON (fixtures only)",
    )
    sim.add_argument("path", type=Path)

    args = parser.parse_args(argv)
    if args.cmd == "example":
        stamp = EXAMPLES[args.which]()
        json.dump(stamp, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        problems = validate_fill_sim_stamp(stamp)
        if problems:
            print("example failed its own validator:", file=sys.stderr)
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        return 0

    path: Path = args.path
    if str(path).startswith("/var/lib/mal"):
        print(f"{path}: does not open /var/lib/mal", file=sys.stderr)
        return 1

    if args.cmd == "sim" and not _sim_path_allowed(path):
        print(
            f"{path}: sim accepts fixtures under {', '.join(ALLOWED_SIM_PREFIXES)} only",
            file=sys.stderr,
        )
        return 1

    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return 1

    if args.cmd == "sim":
        stamp, problems = bind_from_evaluate_stamp(payload)
        if problems or stamp is None:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        json.dump(stamp, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    problems = validate_fill_sim_stamp(payload)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print(f"ok {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
