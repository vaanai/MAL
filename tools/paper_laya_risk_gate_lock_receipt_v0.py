"""paper-laya-risk-gate-lock-receipt-v0.

Proposed registration. Fixtures-only lock receipt citing validated LAYA precompute
surround packets (#63 fill-sim and/or #64 non-fill-sim) already on main.

Records risk_gate decision locked. Does not unlock, authorize-run, or go live.
Parent #49–#64 surround CLIs are called for validation, not rewritten.
Refuses /var/lib/mal lexically before any filesystem touch.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.paper_laya_precompute_fill_sim_surround_packet_v0 import (
    PACKET_ID as FILL_SIM_SURROUND_ID,
    SCHEMA_VERSION as FILL_SIM_SURROUND_SCHEMA,
    SOFT_WATCH_ITEMS as FILL_SIM_SURROUND_SOFT_WATCH_ITEMS,
    validate_surround as validate_fill_sim_surround,
)
from tools.paper_laya_precompute_surround_packet_v0 import (
    PACKET_ID as NON_FILL_SURROUND_ID,
    SCHEMA_VERSION as NON_FILL_SURROUND_SCHEMA,
    SOFT_WATCH_ITEMS as NON_FILL_SURROUND_SOFT_WATCH_ITEMS,
    load_json,
    validate_surround as validate_non_fill_surround,
)

SCHEMA_VERSION = "paper_laya_risk_gate_lock_receipt_v0"
RECEIPT_TYPE = "paper_laya_risk_gate_lock_receipt"
RECEIPT_ID = "paper-laya-risk-gate-lock-receipt-v0"
INCOMPLETE_REASON = "laya_risk_gate_lock_receipt_does_not_close_the_sealed_book"
HORIZON_STATUS = "lock_receipt_null_explicit"
DELTA_REASON = "laya_risk_gate_lock_receipt_has_no_scored_fill"
LOCK_REASON = "paper_registration_fixture_receipt_only_not_risk_gate_unlock"

NON_FILL_SURROUND_COMMIT = "277a142"
NON_FILL_SURROUND_PR = 64
FILL_SIM_SURROUND_COMMIT = "171cd61"
FILL_SIM_SURROUND_PR = 63

PREFIX_NON_FILL = "fixtures/paper_laya_precompute_surround_packet_v0/"
PREFIX_FILL_SIM = "fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/"

SOFT_WATCH_SOURCE = (
    "hot_packet_v0_soft_gate_pr49_through_paper_laya_precompute_surround_pr64_"
    "laya_risk_gate_lock_receipt_v0"
)
INHERITED_FROM: tuple[str, ...] = (
    "paper_laya_precompute_fill_sim_surround_packet_v0_pr63",
    "paper_laya_precompute_surround_packet_v0_pr64",
)
INHERITED_SOFT_WATCH_ITEMS: tuple[str, ...] = tuple(
    dict.fromkeys(
        NON_FILL_SURROUND_SOFT_WATCH_ITEMS + FILL_SIM_SURROUND_SOFT_WATCH_ITEMS
    )
)
LOCAL_SOFT_WATCH_ITEMS: tuple[str, ...] = (
    "receipt_not_risk_gate_unlock",
    "receipt_not_laya_authorize_run",
    "receipt_not_live",
    "synthetic_surround_not_host_extract",
    "lock_counts_are_stamp_counts_not_returns",
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
    "surround_body_embedded_as_scored",
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
HOST_ROOT = "/var/lib/mal"
FIXTURE_DIR = REPO / "fixtures" / "paper_laya_risk_gate_lock_receipt_v0"

_SURROUND_REGISTRY: dict[str, dict[str, Any]] = {
    PREFIX_NON_FILL: {
        "id": NON_FILL_SURROUND_ID,
        "schema_version": NON_FILL_SURROUND_SCHEMA,
        "pull_request": NON_FILL_SURROUND_PR,
        "commit": NON_FILL_SURROUND_COMMIT,
        "validate": validate_non_fill_surround,
    },
    PREFIX_FILL_SIM: {
        "id": FILL_SIM_SURROUND_ID,
        "schema_version": FILL_SIM_SURROUND_SCHEMA,
        "pull_request": FILL_SIM_SURROUND_PR,
        "commit": FILL_SIM_SURROUND_COMMIT,
        "validate": validate_fill_sim_surround,
    },
}


def _err(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def _honesty() -> dict[str, Any]:
    body: dict[str, Any] = {"registration": "proposed"}
    for key in HONESTY_FALSE:
        body[key] = False
    return body


def _collapse_leading_slashes(posix_path: str) -> str:
    if posix_path.startswith("//"):
        return "/" + posix_path.lstrip("/")
    return posix_path


def _host_open_refusal(text: str) -> str | None:
    normalized = _collapse_leading_slashes(posixpath.normpath(text.replace("\\", "/").strip()))
    if normalized == HOST_ROOT or normalized.startswith(HOST_ROOT + "/"):
        return f"{text}: does not open {HOST_ROOT}"
    return None


def _normpath_string_only(text: str) -> str:
    raw = text.replace("\\", "/").strip()
    return _collapse_leading_slashes(posixpath.normpath(raw))


def _lexical_under_host_root(posix_path: str) -> bool:
    normalized = _collapse_leading_slashes(posixpath.normpath(posix_path))
    return normalized == HOST_ROOT or normalized.startswith(HOST_ROOT + "/")


def _relative_joined_under_host_root(text: str, cwd: str) -> bool:
    joined = posixpath.join(cwd, text)
    return _lexical_under_host_root(joined)


def _receipt_path_refusal(text: str) -> str | None:
    refusal = _host_open_refusal(text)
    if refusal:
        return refusal
    norm = _normpath_string_only(text)
    if posixpath.isabs(norm):
        if _lexical_under_host_root(norm):
            return f"{text}: does not open {HOST_ROOT}"
        return None
    for cwd in ("/", os.getcwd()):
        if _relative_joined_under_host_root(text, cwd):
            return f"{text}: does not open {HOST_ROOT}"
    return None


def _repo_relative_path(path: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(REPO.resolve())
    except ValueError:
        return None
    return rel.as_posix()


def _surround_registry_for(repo_path: str) -> dict[str, Any] | None:
    norm = repo_path.replace("\\", "/")
    for prefix, meta in _SURROUND_REGISTRY.items():
        if norm.startswith(prefix):
            return meta
    return None


def _resolve_surround_fixture(text: str) -> tuple[str, Mapping[str, Any]] | None:
    refusal = _receipt_path_refusal(text)
    if refusal:
        return None
    if _surround_registry_for(text) is None:
        return None
    path = (REPO / text).resolve()
    if not path.is_file():
        return None
    rel = _repo_relative_path(path)
    if rel is None or rel != text.replace("\\", "/"):
        return None
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return rel, payload


def _digest_from_surround(surround: Mapping[str, Any]) -> dict[str, Any]:
    digest: dict[str, Any] = {
        "counts_are_stamp_counts_not_returns": True,
        "full_book": {
            "policy": surround["full_book"]["policy"],
            "reject_stamps_dropped": surround["full_book"]["reject_stamps_dropped"],
            "n": surround["full_book"]["n"],
            "runner_n": surround["full_book"]["runner_n"],
            "reject_n": surround["full_book"]["reject_n"],
        },
        "label_rates": {
            "share_kind": surround["label_rates"]["share_kind"],
            "rows": [
                {
                    "label": row["label"],
                    "n": row["n"],
                    "share": dict(row["share"]),
                }
                for row in surround["label_rates"]["rows"]
            ],
        },
    }
    status_counts = surround.get("fill_sim_status_counts")
    if isinstance(status_counts, list) and status_counts:
        digest["fill_sim_status_counts"] = [
            {
                "status": row["status"],
                "n": row["n"],
                "share": dict(row["share"]),
            }
            for row in status_counts
        ]
    return digest


def _laya_caps() -> dict[str, Any]:
    return {
        "authorize_run": False,
        "risk_gate_unlock": False,
        "live_trading": False,
        "consumption": "fixtures_only_lock_receipt_v0",
    }


def _risk_gate_block() -> dict[str, Any]:
    return {
        "decision": "locked",
        "unlock": False,
        "reason": LOCK_REASON,
    }


def _horizons_block() -> dict[str, Any]:
    return {
        "status": HORIZON_STATUS,
        "values_supplied": False,
        "null_is_not_zero_return": True,
        "values": {name: None for name in ("1s", "5s", "15s", "30s", "60s")},
    }


def _delta_exec_block() -> dict[str, Any]:
    return {
        "field": "delta_exec",
        "also_called": "Δ_exec",
        "value": None,
        "status": HORIZON_STATUS,
        "reason": DELTA_REASON,
        "null_is_not_zero_cost": True,
    }


def assemble(*, surround_paths: Sequence[str]) -> tuple[dict[str, Any] | None, list[str]]:
    """Build one lock receipt from validated checked-in surround fixture paths."""
    errors: list[str] = []
    if not surround_paths:
        return None, ["assembly.surround_paths: at least one path is required"]

    citations: list[dict[str, Any]] = []
    for index, repo_path in enumerate(surround_paths):
        key = f"assembly.surround_paths[{index}]"
        meta = _surround_registry_for(repo_path)
        if meta is None:
            _err(
                errors,
                key,
                f"must start with {PREFIX_NON_FILL} or {PREFIX_FILL_SIM}",
            )
            continue
        resolved = _resolve_surround_fixture(repo_path)
        if resolved is None:
            _err(errors, key, "missing or refused path")
            continue
        rel, surround = resolved
        problems = meta["validate"](surround)
        if problems:
            errors.extend(f"{key}: {item}" for item in problems)
            continue
        if surround.get("laya", {}).get("risk_gate_unlock") is not False:
            _err(errors, key, "cited surround must keep risk_gate_unlock false")
            continue
        citations.append(
            {
                "registration": {
                    "id": meta["id"],
                    "schema_version": meta["schema_version"],
                    "pull_request": meta["pull_request"],
                    "commit": meta["commit"],
                    "rewritten_by_this_stamp": False,
                },
                "fixture_path": rel,
                "validated_by_parent_cli": True,
                "surround_body_embedded": False,
                "digest": _digest_from_surround(surround),
            }
        )

    if errors:
        return None, errors
    if not citations:
        return None, ["assembly: no validated surround citations"]

    receipt = {
        "schema_version": SCHEMA_VERSION,
        "type": RECEIPT_TYPE,
        "id": RECEIPT_ID,
        "paper_only": True,
        "receipt_kind": "lock",
        "laya": _laya_caps(),
        "risk_gate": _risk_gate_block(),
        "input": {
            "local_json_only": True,
            "rpc": False,
            "observe_jsonl_tail": False,
            "host_extract_required": False,
            "host_jsonl_read": False,
            "marks_joined": False,
            "fixture_origin": "synthetic",
            "graph_policy": "cold",
            "assembly": {"surround_paths": list(surround_paths)},
        },
        "surround_citations": citations,
        "carries": {
            "surround_body": False,
            "host_bytes": False,
            "horizons": False,
            "delta_exec": False,
        },
        "full_book": {
            "policy": "dec007_both_arms_retained",
            "reject_stamps_dropped": 0,
            "local_set_is_not_the_sealed_book": True,
        },
        "horizons": _horizons_block(),
        "delta_exec": _delta_exec_block(),
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
    }
    return receipt, []


def example_non_fill_surround() -> dict[str, Any]:
    path = f"{PREFIX_NON_FILL}mixed_scoreboard.json"
    receipt, errors = assemble(surround_paths=[path])
    if receipt is None:
        raise RuntimeError(errors)
    return receipt


def example_fill_sim_surround() -> dict[str, Any]:
    path = f"{PREFIX_FILL_SIM}mixed_scoreboard.json"
    receipt, errors = assemble(surround_paths=[path])
    if receipt is None:
        raise RuntimeError(errors)
    return receipt


def example_mixed_spines() -> dict[str, Any]:
    paths = [
        f"{PREFIX_NON_FILL}mixed_scoreboard.json",
        f"{PREFIX_FILL_SIM}mixed_scoreboard.json",
    ]
    receipt, errors = assemble(surround_paths=paths)
    if receipt is None:
        raise RuntimeError(errors)
    return receipt


EXAMPLES: dict[str, Any] = {
    "non-fill-surround": example_non_fill_surround,
    "fill-sim-surround": example_fill_sim_surround,
    "mixed-spines": example_mixed_spines,
}


def _reject_forbidden_keys(errors: list[str], obj: Any, path: str) -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            if str(key).lower() in FORBIDDEN_KEYS or key in FORBIDDEN_KEYS:
                _err(errors, child, "forbidden on lock receipt")
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


def validate_receipt(receipt: Any) -> list[str]:
    if not isinstance(receipt, Mapping):
        return ["receipt: must be a JSON object"]
    errors: list[str] = []
    _reject_forbidden_keys(errors, receipt, "receipt")
    if errors:
        return errors

    if receipt.get("receipt_kind") != "lock":
        return ["receipt_kind: must be lock"]

    inp = receipt.get("input")
    if not isinstance(inp, Mapping):
        return ["input: must be an object"]
    assembly = inp.get("assembly")
    if not isinstance(assembly, Mapping):
        return ["input.assembly: must be an object"]
    paths = assembly.get("surround_paths")
    if not isinstance(paths, list) or not paths:
        return ["input.assembly.surround_paths: must be a non-empty list"]

    expected, problems = assemble(surround_paths=[str(item) for item in paths])
    if problems or expected is None:
        return problems or ["receipt: assembly did not rebuild"]

    if receipt != expected:
        diffs = []
        _diff_paths(expected, receipt, "", diffs)
        return diffs or ["receipt: does not match assembly rebuild"]

    if receipt.get("laya", {}).get("authorize_run") is not False:
        return ["laya.authorize_run: must stay false"]
    if receipt.get("laya", {}).get("risk_gate_unlock") is not False:
        return ["laya.risk_gate_unlock: must stay false"]
    if receipt.get("laya", {}).get("live_trading") is not False:
        return ["laya.live_trading: must stay false"]
    rg = receipt.get("risk_gate")
    if not isinstance(rg, Mapping):
        return ["risk_gate: must be an object"]
    if rg.get("decision") != "locked" or rg.get("unlock") is not False:
        return ["risk_gate: must stay locked with unlock false"]
    return []


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="python -m tools.paper_laya_risk_gate_lock_receipt_v0",
        description=(
            "Assemble fixtures-only LAYA risk-gate lock receipts citing validated "
            "surround packets (#63/#64). Risk gate stays locked. Sealed book incomplete."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    example = sub.add_parser("example", help="Print a checked-in lock receipt example")
    example.add_argument("--which", choices=sorted(EXAMPLES), required=True)

    validate = sub.add_parser("validate", help="Validate one lock receipt JSON file")
    validate.add_argument("path", type=Path)

    assemble_cmd = sub.add_parser(
        "assemble",
        help="Assemble a lock receipt from checked-in surround fixture path(s)",
    )
    assemble_cmd.add_argument(
        "--surround",
        action="append",
        dest="surround_paths",
        required=True,
        help=f"Repo-relative path under {PREFIX_NON_FILL} or {PREFIX_FILL_SIM}",
    )

    args = parser.parse_args(argv)

    if args.cmd == "example":
        receipt = EXAMPLES[args.which]()
        problems = validate_receipt(receipt)
        if problems:
            print("example failed its own validator:", file=sys.stderr)
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        json.dump(receipt, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "validate":
        refusal = _receipt_path_refusal(str(args.path))
        if refusal:
            print(refusal, file=sys.stderr)
            return 1
        try:
            payload = load_json(args.path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{args.path}: {exc}", file=sys.stderr)
            return 1
        problems = validate_receipt(payload)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"ok {args.path}")
        return 0

    for candidate in args.surround_paths:
        refusal = _receipt_path_refusal(candidate)
        if refusal:
            print(refusal, file=sys.stderr)
            return 1
    receipt, problems = assemble(surround_paths=list(args.surround_paths))
    if problems or receipt is None:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    json.dump(receipt, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
