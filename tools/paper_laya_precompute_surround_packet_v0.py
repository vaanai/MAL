"""paper-laya-precompute-surround-packet-v0.

Proposed registration. Assembles validated paper_scoreboard_sealed_fixture_v0
digests (and optionally cites #52 batch rollup fields) into a fixtures-only surround
packet shaped for later LAYA precompute consumption on the non-fill-sim paper spine.

Not a measure. Not LAYA authorize-run. Not risk-gate unlock. Not live.
Fixtures only; refuses /var/lib/mal lexically before any filesystem touch.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0 import (
    BATCH_ID,
    SCHEMA_VERSION as BATCH_SCHEMA,
    SOFT_WATCH_ITEMS as BATCH_SOFT_WATCH_ITEMS,
    validate_batch,
)
from tools.paper_fill_sim_scoreboard_sealed_fixture_v0 import (
    HOST_ROOT,
    _host_open_refusal,
)
from tools.paper_scoreboard_sealed_fixture_v0 import (
    SCHEMA_VERSION as SCOREBOARD_SCHEMA,
    SCOREBOARD_ID,
    SOFT_WATCH_ITEMS as SCOREBOARD_SOFT_WATCH_ITEMS,
    load_json,
    validate_scoreboard,
)

SCHEMA_VERSION = "paper_laya_precompute_surround_packet_v0"
PACKET_TYPE = "paper_laya_precompute_surround_packet"
PACKET_ID = "paper-laya-precompute-surround-packet-v0"
INCOMPLETE_REASON = "laya_precompute_surround_does_not_close_the_sealed_book"
HORIZON_STATUS = "surround_packet_null_explicit"
DELTA_REASON = "laya_precompute_fixture_surround_has_no_scored_fill"

SCOREBOARD_PARENT_COMMIT = "aa31768"
SCOREBOARD_PARENT_PR = 51
BATCH_PARENT_COMMIT = "243e11b"
BATCH_PARENT_PR = 52

SOFT_WATCH_SOURCE = (
    "hot_packet_v0_soft_gate_pr49_paper_evaluate_pr50_scoreboard_pr51_"
    "batch_oracle_sealed_day_incomplete_rpc_v0_pr52_"
    "laya_precompute_surround_packet_v0"
)
INHERITED_FROM: tuple[str, ...] = (
    "hot_packet_v0_soft_gate_pr49",
    "paper_evaluate_hot_packet_v0_pr50",
    "paper_scoreboard_sealed_fixture_v0_pr51",
    "paper_batch_oracle_sealed_day_incomplete_rpc_v0_pr52",
)
INHERITED_SOFT_WATCH_ITEMS: tuple[str, ...] = tuple(
    dict.fromkeys(SCOREBOARD_SOFT_WATCH_ITEMS + BATCH_SOFT_WATCH_ITEMS)
)
LOCAL_SOFT_WATCH_ITEMS: tuple[str, ...] = (
    "surround_counts_are_not_returns",
    "surround_not_laya_authorize_run",
    "surround_not_risk_gate_unlock",
    "surround_not_live_trading",
    "synthetic_scoreboard_not_a_host_extract",
    "batch_digest_optional_not_a_measure",
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
    "hot_packet_embedded_as_scored",
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
ALLOWED_SCOREBOARD_PREFIX = "fixtures/paper_scoreboard_sealed_fixture_v0/"
ALLOWED_BATCH_PREFIX = "fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/"
FIXTURE_DIR = REPO / "fixtures" / "paper_laya_precompute_surround_packet_v0"


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


def _surround_path_refusal(text: str) -> str | None:
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


def _allowed_repo_path(text: str, *, prefix: str) -> bool:
    norm = text.replace("\\", "/")
    if norm.startswith("/"):
        return False
    return norm.startswith(prefix)


def _resolve_repo_path(text: str) -> Path | None:
    refusal = _surround_path_refusal(text)
    if refusal:
        return None
    path = (REPO / text).resolve()
    if not path.is_file():
        return None
    rel = _repo_relative_path(path)
    if rel is None or rel != text.replace("\\", "/"):
        return None
    return path


def _parent_scoreboard_citation() -> dict[str, Any]:
    return {
        "id": SCOREBOARD_ID,
        "schema_version": SCOREBOARD_SCHEMA,
        "pull_request": SCOREBOARD_PARENT_PR,
        "commit": SCOREBOARD_PARENT_COMMIT,
    }


def _parent_batch_citation() -> dict[str, Any]:
    return {
        "id": BATCH_ID,
        "schema_version": BATCH_SCHEMA,
        "pull_request": BATCH_PARENT_PR,
        "commit": BATCH_PARENT_COMMIT,
    }


def _fixture_join_summary(board: Mapping[str, Any]) -> dict[str, Any]:
    join = board.get("fixture_join")
    if not isinstance(join, Mapping):
        return {}
    keys = (
        "matched_n",
        "label_disagree_n",
        "reason_disagree_n",
        "unmatched_stamp_n",
        "closed_book",
    )
    return {key: join.get(key) for key in keys}


def _sealed_day_from_board(board: Mapping[str, Any]) -> str | None:
    join = board.get("fixture_join")
    if isinstance(join, Mapping):
        day = join.get("day")
        if isinstance(day, str):
            return day
    sealed_days = board.get("sealed_days")
    if isinstance(sealed_days, list) and sealed_days:
        first = sealed_days[0]
        if isinstance(first, Mapping):
            day = first.get("day")
            if isinstance(day, str):
                return day
    return None


def _scoreboard_digest(board: Mapping[str, Any], repo_path: str) -> dict[str, Any]:
    return {
        "citation": {
            "repo_path": repo_path,
            "parent": _parent_scoreboard_citation(),
        },
        "sealed_day": _sealed_day_from_board(board),
        "full_book": {
            "policy": board["full_book"]["policy"],
            "reject_stamps_dropped": board["full_book"]["reject_stamps_dropped"],
            "n": board["full_book"]["n"],
            "runner_n": board["full_book"]["runner_n"],
            "reject_n": board["full_book"]["reject_n"],
        },
        "label_rates": {
            "share_kind": board["label_rates"]["share_kind"],
            "rows": [
                {
                    "label": row["label"],
                    "n": row["n"],
                    "share": dict(row["share"]),
                }
                for row in board["label_rates"]["rows"]
            ],
        },
        "fixture_join": _fixture_join_summary(board),
        "graph_lift_aggregate_status": board.get("graph_lift_aggregate_status"),
        "dual_read": {
            "sealed_book_rpc_slice": board["dual_read"]["sealed_book_rpc_slice"],
            "closed_book_claim": board["dual_read"]["closed_book_claim"],
        },
        "stamp_bodies_embedded": False,
    }


def _rollup_digest(batch: Mapping[str, Any]) -> dict[str, Any]:
    rollup = batch["rollup"]
    return {
        "n_meaning": rollup["n_meaning"],
        "share_kind": rollup["share_kind"],
        "counts_are_not_returns": rollup["counts_are_not_returns"],
        "n": rollup["n"],
        "runner_n": rollup["runner_n"],
        "reject_n": rollup["reject_n"],
        "rows": [
            {"label": row["label"], "n": row["n"], "share": dict(row["share"])}
            for row in rollup["rows"]
        ],
        "days": list(rollup["days"]),
    }


def _aggregate_label(rows_list: Sequence[Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    totals = {"runner": 0, "reject": 0}
    for rows in rows_list:
        for row in rows:
            label = row.get("label")
            if label in totals:
                totals[str(label)] += int(row.get("n", 0))
    total_n = totals["runner"] + totals["reject"]
    return [
        {
            "label": "runner",
            "n": totals["runner"],
            "share": _share(totals["runner"], total_n or 1),
        },
        {
            "label": "reject",
            "n": totals["reject"],
            "share": _share(totals["reject"], total_n or 1),
        },
    ]


def _precompute_block() -> dict[str, Any]:
    return {
        "slot": "laya_surround_v0",
        "status": "fixture_shape_only_not_executed",
        "features_ready": False,
        "regime_tags_ready": False,
        "graph_scores_ready": False,
        "horizons": {
            "status": HORIZON_STATUS,
            "values_supplied": False,
            "null_is_not_zero_return": True,
            "values": {name: None for name in ("1s", "5s", "15s", "30s", "60s")},
        },
        "delta_exec": {
            "field": "delta_exec",
            "also_called": "Δ_exec",
            "value": None,
            "status": HORIZON_STATUS,
            "reason": DELTA_REASON,
            "null_is_not_zero_cost": True,
        },
    }


def _laya_caps() -> dict[str, Any]:
    return {
        "authorize_run": False,
        "risk_gate_unlock": False,
        "live_trading": False,
        "consumption": "fixtures_only_precompute_shape_v0",
        "packet_is_not_hot_packet_v0": True,
    }


def assemble(
    *,
    scoreboard_paths: Sequence[str],
    batch_path: str | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Build one surround packet from checked-in scoreboard files and optional batch rollup."""
    errors: list[str] = []
    if not scoreboard_paths and batch_path is None:
        return None, ["assembly: at least one scoreboard path or a batch path is required"]

    boards: list[tuple[str, Mapping[str, Any]]] = []
    for index, repo_path in enumerate(scoreboard_paths):
        path_key = f"assembly.scoreboard_paths[{index}]"
        if not _allowed_repo_path(repo_path, prefix=ALLOWED_SCOREBOARD_PREFIX):
            _err(
                errors,
                path_key,
                f"must start with {ALLOWED_SCOREBOARD_PREFIX}",
            )
            continue
        resolved = _resolve_repo_path(repo_path)
        if resolved is None:
            _err(errors, path_key, "missing or refused path")
            continue
        try:
            payload = load_json(resolved)
        except (OSError, json.JSONDecodeError) as exc:
            _err(errors, path_key, str(exc))
            continue
        problems = validate_scoreboard(payload)
        if problems:
            errors.extend(f"{path_key}: {item}" for item in problems)
            continue
        boards.append((repo_path, payload))

    batch_digest: dict[str, Any] | None = None
    if batch_path is not None:
        if not _allowed_repo_path(batch_path, prefix=ALLOWED_BATCH_PREFIX):
            _err(errors, "assembly.batch_path", f"must start with {ALLOWED_BATCH_PREFIX}")
        else:
            resolved = _resolve_repo_path(batch_path)
            if resolved is None:
                _err(errors, "assembly.batch_path", "missing or refused path")
            else:
                try:
                    batch = load_json(resolved)
                except (OSError, json.JSONDecodeError) as exc:
                    _err(errors, "assembly.batch_path", str(exc))
                    batch = None
                if batch is not None:
                    batch_problems = validate_batch(batch)
                    if batch_problems:
                        errors.extend(
                            f"assembly.batch_path: {item}" for item in batch_problems
                        )
                    else:
                        for day_block in batch.get("days", []):
                            if not isinstance(day_block, Mapping):
                                continue
                            board = day_block.get("scoreboard")
                            if not isinstance(board, Mapping):
                                continue
                            day = day_block.get("day", "?")
                            synthetic_path = f"{batch_path}#days[{day}].scoreboard"
                            boards.append((synthetic_path, board))
                        batch_digest = {
                            "included": True,
                            "citation": {
                                "repo_path": batch_path,
                                "parent": _parent_batch_citation(),
                            },
                            "rollup": _rollup_digest(batch),
                            "fields_cited": ["rollup"],
                        }

    if errors:
        return None, errors
    if not boards:
        return None, ["assembly: no validated scoreboard digests"]

    seen: set[str] = set()
    unique_boards: list[tuple[str, Mapping[str, Any]]] = []
    for repo_path, board in boards:
        if repo_path in seen:
            continue
        seen.add(repo_path)
        unique_boards.append((repo_path, board))

    digests = [_scoreboard_digest(board, repo_path) for repo_path, board in unique_boards]
    runner_n = sum(int(d["full_book"]["runner_n"]) for d in digests)
    reject_n = sum(int(d["full_book"]["reject_n"]) for d in digests)
    total_n = runner_n + reject_n

    label_rows = _aggregate_label([d["label_rates"]["rows"] for d in digests])

    sealed_days = []
    for digest in digests:
        day = digest.get("sealed_day")
        if isinstance(day, str) and day not in {item["day"] for item in sealed_days}:
            sealed_days.append({"day": day, "local_set_is_not_the_sealed_book": True})

    packet = {
        "schema_version": SCHEMA_VERSION,
        "type": PACKET_TYPE,
        "id": PACKET_ID,
        "paper_only": True,
        "laya": _laya_caps(),
        "input": {
            "local_json_only": True,
            "rpc": False,
            "observe_jsonl_tail": False,
            "host_extract_required": False,
            "host_jsonl_read": False,
            "marks_joined": False,
            "fixture_origin": "synthetic",
            "graph_policy": "cold",
            "assembly": {
                "scoreboard_paths": list(scoreboard_paths),
                "batch_path": batch_path,
            },
        },
        "scoreboard_digests": digests,
        "batch_digest": batch_digest
        if batch_digest is not None
        else {"included": False, "citation": None, "rollup": None, "fields_cited": []},
        "precompute": _precompute_block(),
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
        "label_rates": {
            "share_kind": "count_fraction_not_a_return",
            "rows": label_rows,
        },
        "horizons": {
            "status": HORIZON_STATUS,
            "values_supplied": False,
            "null_is_not_zero_return": True,
            "values": {name: None for name in ("1s", "5s", "15s", "30s", "60s")},
        },
        "delta_exec": {
            "field": "delta_exec",
            "also_called": "Δ_exec",
            "value": None,
            "status": HORIZON_STATUS,
            "reason": DELTA_REASON,
            "null_is_not_zero_cost": True,
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
        "sealed_days": sealed_days,
        "soft_watches": {
            "blocking": False,
            "source": SOFT_WATCH_SOURCE,
            "inherited_from": list(INHERITED_FROM),
            "items": list(SOFT_WATCH_ITEMS),
        },
        "honesty": _honesty(),
    }
    return packet, []


def _reject_forbidden_keys(errors: list[str], obj: Any, path: str) -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if str(key).lower() in FORBIDDEN_KEYS or key in FORBIDDEN_KEYS:
                _err(errors, f"{path}.{key}", "forbidden on surround packet")
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


def validate_surround(packet: Any) -> list[str]:
    if not isinstance(packet, Mapping):
        return ["surround: must be a JSON object"]
    errors: list[str] = []
    _reject_forbidden_keys(errors, packet, "surround")
    if errors:
        return errors
    inp = packet.get("input")
    if not isinstance(inp, Mapping):
        return ["input: must be an object"]
    if inp.get("marks_joined") is True:
        return ["input.marks_joined: must stay false"]
    assembly = inp.get("assembly")
    if not isinstance(assembly, Mapping):
        return ["input.assembly: must be an object"]
    scoreboard_paths = assembly.get("scoreboard_paths")
    batch_path = assembly.get("batch_path")
    if not isinstance(scoreboard_paths, list):
        return ["input.assembly.scoreboard_paths: must be a list"]
    if batch_path is not None and not isinstance(batch_path, str):
        return ["input.assembly.batch_path: must be a string or null"]
    normalized_paths = [str(item) for item in scoreboard_paths]
    expected, problems = assemble(
        scoreboard_paths=normalized_paths,
        batch_path=batch_path if isinstance(batch_path, str) else None,
    )
    if problems or expected is None:
        return problems or ["surround: assembly did not rebuild"]
    if packet != expected:
        diffs: list[str] = []
        _diff_paths(expected, packet, "", diffs)
        if not diffs:
            diffs.append("surround: does not match assembly rebuild")
        return diffs
    if packet.get("laya", {}).get("authorize_run") is not False:
        return ["laya.authorize_run: must stay false"]
    if packet.get("laya", {}).get("risk_gate_unlock") is not False:
        return ["laya.risk_gate_unlock: must stay false"]
    if packet.get("laya", {}).get("live_trading") is not False:
        return ["laya.live_trading: must stay false"]
    return []


def example_mixed_scoreboard() -> dict[str, Any]:
    path = "fixtures/paper_scoreboard_sealed_fixture_v0/mixed_runner_reject.json"
    packet, errors = assemble(scoreboard_paths=[path], batch_path=None)
    if packet is None:
        raise RuntimeError(errors)
    return packet


def example_batch_two_day_with_digest() -> dict[str, Any]:
    batch_path = "fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json"
    packet, errors = assemble(scoreboard_paths=[], batch_path=batch_path)
    if packet is None:
        raise RuntimeError(errors)
    return packet


EXAMPLES: dict[str, Any] = {
    "mixed-scoreboard": example_mixed_scoreboard,
    "batch-two-day-with-digest": example_batch_two_day_with_digest,
}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="python -m tools.paper_laya_precompute_surround_packet_v0",
        description=(
            "Assemble validated paper scoreboard digests into a LAYA precompute "
            "surround packet. Fixtures only. Sealed book stays incomplete."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    example = sub.add_parser("example", help="Print a checked-in surround example")
    example.add_argument("--which", choices=sorted(EXAMPLES), required=True)

    validate = sub.add_parser("validate", help="Validate one surround JSON file")
    validate.add_argument("path", type=Path)

    assemble_cmd = sub.add_parser(
        "assemble",
        help="Assemble from checked-in scoreboard paths and optional batch rollup",
    )
    assemble_cmd.add_argument(
        "--scoreboard",
        action="append",
        default=[],
        help=f"Repo-relative path under {ALLOWED_SCOREBOARD_PREFIX}",
    )
    assemble_cmd.add_argument(
        "--batch",
        default=None,
        help=f"Optional repo-relative batch file under {ALLOWED_BATCH_PREFIX}",
    )

    args = parser.parse_args(argv)

    if args.cmd == "example":
        packet = EXAMPLES[args.which]()
        problems = validate_surround(packet)
        if problems:
            print("example failed its own validator:", file=sys.stderr)
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        json.dump(packet, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "validate":
        refusal = _surround_path_refusal(str(args.path))
        if refusal:
            print(refusal, file=sys.stderr)
            return 1
        try:
            payload = load_json(args.path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{args.path}: {exc}", file=sys.stderr)
            return 1
        problems = validate_surround(payload)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"ok {args.path}")
        return 0

    refusal = _surround_path_refusal(args.batch) if args.batch else None
    if refusal:
        print(refusal, file=sys.stderr)
        return 1
    for candidate in args.scoreboard:
        refusal = _surround_path_refusal(candidate)
        if refusal:
            print(refusal, file=sys.stderr)
            return 1
    packet, problems = assemble(
        scoreboard_paths=list(args.scoreboard),
        batch_path=args.batch,
    )
    if problems or packet is None:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    json.dump(packet, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
