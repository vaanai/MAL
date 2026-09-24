"""paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.

Proposed paper registration. A thin receipt for the operator-local dry-run
shape of paper_batch_oracle_sealed_day_incomplete_rpc_v0 against day-aligned
sealed observe JSONL on mal-core-vnic.

No RPC. No SSH. No read of /var/lib/mal. No closed-book claim. No measure exit.
Checked-in fixtures are synthetic receipts, not host extracts.
The parent stamp is not rewritten.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import posixpath
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0 import (
    BATCH_ID as PARENT_ID,
    EXAMPLE_DAYS,
    FORBIDDEN_KEYS as PARENT_FORBIDDEN_KEYS,
    SCHEMA_VERSION as PARENT_SCHEMA_VERSION,
    SOFT_WATCH_ITEMS as PARENT_SOFT_WATCH_ITEMS,
    main as parent_main,
)

SCHEMA_VERSION = "paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0"
RECEIPT_TYPE = "paper_dry_run_receipt"
RECEIPT_ID = "paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0"
PARENT_COMMIT = "243e11b"
PARENT_PR = 52
PARENT_CLI = "python -m tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0"
HOST = "mal-core-vnic"
HOST_ROOT = "/var/lib/mal"
INCOMPLETE_REASON = "host_local_dry_run_does_not_close_the_sealed_book"
OPERATOR_DIGEST_REASON = "operator_shape_not_executed_by_this_registration"
COURIER_DAYS: tuple[str, ...] = ("2026-09-20", "2026-09-21")
FIXTURE_ORIGINS: tuple[str, ...] = ("synthetic", "sealed_row_projection")
RECEIPT_KINDS: tuple[str, ...] = (
    "synthetic_replay",
    "projection_on_synthetic",
    "operator_declared",
)

SOFT_WATCH_SOURCE = (
    "hot_packet_v0_soft_gate_pr49_paper_evaluate_pr50_scoreboard_pr51_batch_pr52"
)
INHERITED_FROM: tuple[str, ...] = (
    "hot_packet_v0_soft_gate_pr49",
    "paper_evaluate_hot_packet_v0_pr50",
    "paper_scoreboard_sealed_fixture_v0_pr51",
    "paper_batch_oracle_sealed_day_incomplete_rpc_v0_pr52",
)
LOCAL_SOFT_WATCH_ITEMS: tuple[str, ...] = (
    "host_jsonl_read_is_not_a_closed_book",
    "operator_path_string_is_not_a_host_extract",
    "dry_run_does_not_ssh_or_open_var_lib_mal",
    "parent_stamp_not_rewritten",
)
SOFT_WATCH_ITEMS: tuple[str, ...] = PARENT_SOFT_WATCH_ITEMS + LOCAL_SOFT_WATCH_ITEMS

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
    "live_capital",
    "trading_keys",
    "pumpportal_trade_api",
    "ssh_to_host",
    "host_extract_checked_into_git",
    "var_lib_mal_read",
)

FORBIDDEN_KEYS: frozenset[str] = PARENT_FORBIDDEN_KEYS | frozenset(
    {
        "ws_payload",
        "price_proxy",
        "host_stdout",
        "sealed_book_close",
    }
)

HOST_JSONL: dict[str, str] = {
    day: f"{HOST_ROOT}/sealed/jsonl/observe-{day}.jsonl" for day in COURIER_DAYS
}
HOST_EXPECTATION: dict[str, str] = {
    day: (
        f"{HOST_ROOT}/paper/{PARENT_ID}/sealed_day_{day}_expectation.json"
    )
    for day in COURIER_DAYS
}
SYNTHETIC_JSONL: dict[str, str] = {
    day: (
        "fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/"
        f"observe-{day}.jsonl"
    )
    for day in COURIER_DAYS
}
SYNTHETIC_EXPECTATION: dict[str, str] = {
    day: (
        "fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/"
        f"sealed_day_{day}_expectation.json"
    )
    for day in COURIER_DAYS
}

JSONL_NAME_RE_TEXT = r"^observe-(\d{4}-\d{2}-\d{2})\.jsonl$"
EXPECT_NAME_RE_TEXT = r"^sealed_day_(\d{4}-\d{2}-\d{2})_expectation\.json$"

REPO = Path(__file__).resolve().parents[1]


def _err(errors: list[str], message: str) -> None:
    errors.append(message)


def _honesty() -> dict[str, Any]:
    body: dict[str, Any] = {"registration": "proposed"}
    for key in HONESTY_FALSE:
        body[key] = False
    return body


def _lexical_posix(text: str) -> str:
    raw = text.replace("\\", "/").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = REPO / path
    return posixpath.normpath(path.as_posix())


def _under_var_lib_mal(text: str) -> bool:
    """Lexical check only. Does not stat or read the path."""
    normalized = _lexical_posix(text)
    return normalized == HOST_ROOT or normalized.startswith(HOST_ROOT + "/")


def _host_path_message(text: str) -> str:
    return (
        f"{text}: this receipt CLI does not open {HOST_ROOT} and does not SSH. "
        "The operator-declared shape is example --which operator-declared."
    )


def _host_open_refusal(text: str) -> str | None:
    """Refuse a host path before any file read.

    Same order as receipt: lexical check first (no stat), then realpath.
    A lexical hit does not call realpath and does not read the file.
    """
    if _under_var_lib_mal(text):
        return _host_path_message(text)
    real = os.path.realpath(_lexical_posix(text))
    if _under_var_lib_mal(real):
        return _host_path_message(text)
    return None


def _display(text: str) -> str:
    lexical = _lexical_posix(text)
    prefix = REPO.as_posix().rstrip("/") + "/"
    if lexical.startswith(prefix):
        return lexical[len(prefix) :]
    return lexical


def _openable_repo_path(text: str) -> Path | None:
    """Repo path safe to hand to the parent CLI. Host paths return None."""
    if _under_var_lib_mal(text):
        return None
    lexical = _lexical_posix(text)
    if _under_var_lib_mal(lexical):
        return None
    real = os.path.realpath(lexical)
    if _under_var_lib_mal(real):
        return None
    repo_real = os.path.realpath(REPO)
    try:
        if os.path.commonpath([repo_real, real]) != repo_real:
            return None
    except ValueError:
        return None
    return Path(real)


def _day_from_basename(name: str, pattern: str) -> str | None:
    match = re.fullmatch(pattern, name)
    if match is None:
        return None
    return match.group(1)


def _pair(
    jsonl: Sequence[str], expectation: Sequence[str]
) -> tuple[dict[str, tuple[str, str]] | None, list[str]]:
    if len(jsonl) != 2 or len(expectation) != 2:
        return None, [
            "invocation: the courier pair is exactly two JSONL files and two expectation files"
        ]
    errors: list[str] = []
    jsonl_by_day: dict[str, str] = {}
    expect_by_day: dict[str, str] = {}
    for text in jsonl:
        if not isinstance(text, str) or text.strip() == "":
            _err(errors, "jsonl: path must be a non-empty string")
            continue
        day = _day_from_basename(Path(text).name, JSONL_NAME_RE_TEXT)
        if day is None:
            _err(errors, f"{text}: basename must be observe-YYYY-MM-DD.jsonl")
            continue
        if day in jsonl_by_day:
            _err(errors, f"{text}: duplicate day {day}")
            continue
        jsonl_by_day[day] = text
    for text in expectation:
        if not isinstance(text, str) or text.strip() == "":
            _err(errors, "expectation: path must be a non-empty string")
            continue
        day = _day_from_basename(Path(text).name, EXPECT_NAME_RE_TEXT)
        if day is None:
            _err(errors, f"{text}: basename must be sealed_day_YYYY-MM-DD_expectation.json")
            continue
        if day in expect_by_day:
            _err(errors, f"{text}: duplicate expectation day {day}")
            continue
        expect_by_day[day] = text
    if errors:
        return None, errors
    if set(jsonl_by_day) != set(COURIER_DAYS) or set(expect_by_day) != set(COURIER_DAYS):
        return None, [
            "invocation: days must be the courier pair 2026-09-20 and 2026-09-21"
        ]
    return {day: (jsonl_by_day[day], expect_by_day[day]) for day in COURIER_DAYS}, []


def _argv_shape(origin: str, jsonl: Sequence[str], expectation: Sequence[str]) -> list[str]:
    argv = [
        "python",
        "-m",
        "tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0",
        "batch",
        "--fixture-origin",
        origin,
    ]
    for path in jsonl:
        argv.extend(["--jsonl", path])
    for path in expectation:
        argv.extend(["--expectation", path])
    return argv


def _parent_is_honest(batch: Mapping[str, Any]) -> bool:
    dual = batch.get("dual_read")
    measure = batch.get("measure")
    honesty = batch.get("honesty")
    graph_ok = batch.get("graph_policy") == "cold" and batch.get("graph_lift") is None
    identity_ok = (
        batch.get("schema_version") == PARENT_SCHEMA_VERSION and batch.get("id") == PARENT_ID
    )
    dual_ok = (
        isinstance(dual, Mapping)
        and dual.get("sealed_book_rpc_slice") == "incomplete"
        and dual.get("closed_book_claim") is False
    )
    measure_ok = (
        isinstance(measure, Mapping)
        and measure.get("kind") == "none"
        and measure.get("pass_fail_no_lift") is False
        and measure.get("invented_ev") is False
        and measure.get("invented_lift") is False
        and measure.get("claims_alpha") is False
    )
    honesty_ok = (
        isinstance(honesty, Mapping)
        and honesty.get("executed_host_sealed_book") is False
        and honesty.get("scored_oracle_measure") is False
    )
    return identity_ok and graph_ok and dual_ok and measure_ok and honesty_ok


def _digest(batch: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "included": True,
        "schema_version": batch["schema_version"],
        "id": batch["id"],
        "sealed_book_rpc_slice": batch["dual_read"]["sealed_book_rpc_slice"],
        "closed_book_claim": batch["dual_read"]["closed_book_claim"],
        "measure_kind": batch["measure"]["kind"],
        "graph_policy": batch["graph_policy"],
        "executed_host_sealed_book": batch["honesty"]["executed_host_sealed_book"],
        "scored_oracle_measure": batch["honesty"]["scored_oracle_measure"],
        "batch_embedded": False,
        "host_jsonl_read": batch["input"]["host_jsonl_read"],
    }


def _absent_digest() -> dict[str, Any]:
    return {
        "included": False,
        "reason": OPERATOR_DIGEST_REASON,
        "batch_embedded": False,
    }


def _call_parent(
    jsonl: Sequence[Path],
    expectation: Sequence[Path],
    fixture_origin: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    argv = ["batch", "--fixture-origin", fixture_origin]
    for path in jsonl:
        argv.extend(["--jsonl", str(path)])
    for path in expectation:
        argv.extend(["--expectation", str(path)])
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = parent_main(argv)
    if code != 0:
        message = err.getvalue().strip() or f"parent batch exit {code}"
        return None, [message]
    try:
        batch = json.loads(out.getvalue())
    except json.JSONDecodeError as exc:
        return None, [f"parent batch stdout was not JSON: {exc}"]
    if not isinstance(batch, dict):
        return None, ["parent batch stdout was not an object"]
    return batch, []


def _envelope(
    *,
    receipt_kind: str,
    fixture_origin: str,
    jsonl: Sequence[str],
    expectation: Sequence[str],
    parent_invoked: bool,
    host_jsonl_read: bool,
    host_path_declared: bool,
    parent_digest: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "type": RECEIPT_TYPE,
        "id": RECEIPT_ID,
        "paper_only": True,
        "receipt_kind": receipt_kind,
        "parent": {
            "id": PARENT_ID,
            "schema_version": PARENT_SCHEMA_VERSION,
            "pull_request": PARENT_PR,
            "commit": PARENT_COMMIT,
            "cli": PARENT_CLI,
            "rewritten_by_this_stamp": False,
        },
        "invocation": {
            "host": HOST,
            "host_contacted": False,
            "executed_by_this_process": parent_invoked,
            "fixture_origin": fixture_origin,
            "days": list(COURIER_DAYS),
            "jsonl": list(jsonl),
            "expectation": list(expectation),
            "argv_shape": _argv_shape(fixture_origin, jsonl, expectation),
        },
        "input": {
            "local_json_only": True,
            "rpc": False,
            "ssh_to_host": False,
            "observe_jsonl_tail": False,
            "host_extract_checked_into_git": False,
            "var_lib_mal_opened": False,
            "host_jsonl_read": host_jsonl_read,
            "host_path_declared": host_path_declared,
            "fixture_origin": fixture_origin,
            "parent_invoked": parent_invoked,
            "paths_opened": parent_invoked,
            "graph_policy": "cold",
        },
        "parent_digest": dict(parent_digest),
        "carries": {
            "parent_batch_body": False,
            "horizons": False,
            "delta_exec": False,
            "rollup_counts": False,
            "host_bytes": False,
        },
        "full_book": {
            "policy": "dec007_both_arms_retained",
            "arm_retained": True,
            "deletes_detect_history": False,
            "reject_stamps_dropped": 0,
            "local_set_is_not_the_sealed_book": True,
            "both_arms_unchanged": True,
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
    }


def assemble_receipt(
    *,
    receipt_kind: str,
    fixture_origin: str,
    jsonl: Sequence[str],
    expectation: Sequence[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Build one dry-run receipt. Host paths are recorded only, never opened."""
    if tuple(EXAMPLE_DAYS) != COURIER_DAYS:
        return None, [
            "parent EXAMPLE_DAYS is not the courier pair 2026-09-20 and 2026-09-21"
        ]
    if receipt_kind not in RECEIPT_KINDS:
        return None, [f"receipt_kind: must be one of {list(RECEIPT_KINDS)}"]
    if fixture_origin not in FIXTURE_ORIGINS:
        return None, [f"fixture_origin: must be one of {list(FIXTURE_ORIGINS)}"]
    paired, pair_errors = _pair(jsonl, expectation)
    if pair_errors or paired is None:
        return None, pair_errors or ["invocation: could not pair JSONL and expectation paths"]

    if receipt_kind == "operator_declared":
        if fixture_origin != "sealed_row_projection":
            return None, ["operator_declared: fixture_origin must be sealed_row_projection"]
        for day in COURIER_DAYS:
            got_jsonl, got_expectation = paired[day]
            if got_jsonl != HOST_JSONL[day] or got_expectation != HOST_EXPECTATION[day]:
                return None, [
                    f"{day}: operator-declared paths are the documented host paths only"
                ]
            if not _under_var_lib_mal(got_jsonl) or not _under_var_lib_mal(got_expectation):
                return None, [f"{day}: operator-declared paths must sit under {HOST_ROOT}"]
        return _envelope(
            receipt_kind=receipt_kind,
            fixture_origin=fixture_origin,
            jsonl=[HOST_JSONL[day] for day in COURIER_DAYS],
            expectation=[HOST_EXPECTATION[day] for day in COURIER_DAYS],
            parent_invoked=False,
            host_jsonl_read=True,
            host_path_declared=True,
            parent_digest=_absent_digest(),
        ), []

    expected_origin = (
        "synthetic" if receipt_kind == "synthetic_replay" else "sealed_row_projection"
    )
    if fixture_origin != expected_origin:
        return None, [
            f"{receipt_kind}: fixture_origin must be {expected_origin}"
        ]
    opened_jsonl: list[Path] = []
    opened_expectation: list[Path] = []
    display_jsonl: list[str] = []
    display_expectation: list[str] = []
    for day in COURIER_DAYS:
        jsonl_text, expectation_text = paired[day]
        if _under_var_lib_mal(jsonl_text) or _under_var_lib_mal(expectation_text):
            return None, [
                f"{jsonl_text}: this receipt CLI does not open {HOST_ROOT} and does not SSH. "
                "The operator-declared shape is example --which operator-declared."
            ]
        jsonl_path = _openable_repo_path(jsonl_text)
        expectation_path = _openable_repo_path(expectation_text)
        if jsonl_path is None or expectation_path is None:
            return None, [
                f"{day}: refusing to open a path outside the repo or under {HOST_ROOT}"
            ]
        opened_jsonl.append(jsonl_path)
        opened_expectation.append(expectation_path)
        display_jsonl.append(_display(jsonl_text))
        display_expectation.append(_display(expectation_text))

    batch, problems = _call_parent(opened_jsonl, opened_expectation, fixture_origin)
    if problems or batch is None:
        return None, problems or ["parent batch did not return a paper batch"]
    if not _parent_is_honest(batch):
        return None, ["parent batch is not an incomplete-RPC paper batch"]
    host_jsonl_read = fixture_origin == "sealed_row_projection"
    if batch["input"]["host_jsonl_read"] is not host_jsonl_read:
        return None, ["parent host_jsonl_read does not match fixture_origin"]
    digest = _digest(batch)
    if digest["host_jsonl_read"] is not host_jsonl_read:
        return None, ["parent digest host_jsonl_read drifted"]
    # The batch body is not retained on the receipt.
    del batch
    return _envelope(
        receipt_kind=receipt_kind,
        fixture_origin=fixture_origin,
        jsonl=display_jsonl,
        expectation=display_expectation,
        parent_invoked=True,
        host_jsonl_read=host_jsonl_read,
        host_path_declared=False,
        parent_digest=digest,
    ), []


def example_synthetic_replay() -> dict[str, Any]:
    receipt, errors = assemble_receipt(
        receipt_kind="synthetic_replay",
        fixture_origin="synthetic",
        jsonl=[SYNTHETIC_JSONL[day] for day in COURIER_DAYS],
        expectation=[SYNTHETIC_EXPECTATION[day] for day in COURIER_DAYS],
    )
    if receipt is None:
        raise RuntimeError(errors)
    return receipt


def example_projection_on_synthetic() -> dict[str, Any]:
    receipt, errors = assemble_receipt(
        receipt_kind="projection_on_synthetic",
        fixture_origin="sealed_row_projection",
        jsonl=[SYNTHETIC_JSONL[day] for day in COURIER_DAYS],
        expectation=[SYNTHETIC_EXPECTATION[day] for day in COURIER_DAYS],
    )
    if receipt is None:
        raise RuntimeError(errors)
    return receipt


def example_operator_declared() -> dict[str, Any]:
    receipt, errors = assemble_receipt(
        receipt_kind="operator_declared",
        fixture_origin="sealed_row_projection",
        jsonl=[HOST_JSONL[day] for day in COURIER_DAYS],
        expectation=[HOST_EXPECTATION[day] for day in COURIER_DAYS],
    )
    if receipt is None:
        raise RuntimeError(errors)
    return receipt


EXAMPLES: dict[str, Any] = {
    "synthetic-replay": example_synthetic_replay,
    "projection-on-synthetic": example_projection_on_synthetic,
    "operator-declared": example_operator_declared,
}


def _key_forbidden(key: str) -> bool:
    return key.lower() in FORBIDDEN_KEYS


def _reject_forbidden_keys(errors: list[str], obj: Any, path: str) -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            if _key_forbidden(str(key)):
                _err(errors, f"{child}: forbidden on {RECEIPT_ID}")
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
    """Return errors. Empty list means the receipt matches a rebuild."""
    if not isinstance(receipt, Mapping):
        return ["receipt: must be a JSON object"]
    errors: list[str] = []
    _reject_forbidden_keys(errors, receipt, "receipt")
    if errors:
        return errors
    kind = receipt.get("receipt_kind")
    invocation = receipt.get("invocation")
    if not isinstance(kind, str):
        return ["receipt_kind: required"]
    if not isinstance(invocation, Mapping):
        return ["invocation: must be an object"]
    origin = invocation.get("fixture_origin")
    jsonl = invocation.get("jsonl")
    expectation = invocation.get("expectation")
    if not isinstance(origin, str) or not isinstance(jsonl, list) or not isinstance(expectation, list):
        return ["invocation: fixture_origin, jsonl, and expectation are required"]
    if not all(isinstance(item, str) for item in jsonl + expectation):
        return ["invocation: jsonl and expectation paths must be strings"]
    expected, problems = assemble_receipt(
        receipt_kind=kind,
        fixture_origin=origin,
        jsonl=list(jsonl),
        expectation=list(expectation),
    )
    if problems or expected is None:
        return problems or ["receipt: invocation did not rebuild"]
    if receipt != expected:
        diffs: list[str] = []
        _diff_paths(expected, receipt, "", diffs)
        if not diffs:
            diffs.append("receipt: does not match the dry-run shape")
        return diffs
    return []


def _print_receipt(receipt: Mapping[str, Any]) -> None:
    json.dump(receipt, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


class _Parser(argparse.ArgumentParser):
    """Usage mistakes exit 1. This module has no measure exit."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="python -m tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0",
        description=(
            "Receipt for an operator-local dry-run of the sealed-day paper batch. "
            "Sealed book stays incomplete. No RPC, no SSH, no /var/lib/mal read, "
            "no closed-book claim, no measure exit."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    example = sub.add_parser("example", help="Print one synthetic or operator-declared receipt")
    example.add_argument("--which", choices=sorted(EXAMPLES), required=True)

    validate = sub.add_parser("validate", help="Validate one dry-run receipt JSON file")
    validate.add_argument("path", type=Path)

    receipt_cmd = sub.add_parser(
        "receipt",
        help="Wrap the parent batch CLI on in-repo files and print a receipt",
    )
    receipt_cmd.add_argument("--jsonl", type=str, action="append", required=True)
    receipt_cmd.add_argument("--expectation", type=str, action="append", required=True)
    receipt_cmd.add_argument(
        "--fixture-origin",
        choices=FIXTURE_ORIGINS,
        default="synthetic",
    )

    args = parser.parse_args(argv)
    if args.cmd == "example":
        receipt = EXAMPLES[args.which]()
        _print_receipt(receipt)
        problems = validate_receipt(receipt)
        if problems:
            print("example failed its own validator:", file=sys.stderr)
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        return 0

    if args.cmd == "validate":
        path: Path = args.path
        text = os.fspath(path)
        # Repo-joined lexical+realpath (absolute args and FAIL #1), then the
        # cwd abspath read_text will open. Relative args can diverge.
        refusal = _host_open_refusal(text)
        if refusal is None:
            refusal = _host_open_refusal(os.path.abspath(text))
        if refusal is not None:
            print(refusal, file=sys.stderr)
            return 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            return 1
        problems = validate_receipt(payload)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"ok {path}")
        return 0

    for text in list(args.jsonl) + list(args.expectation):
        refusal = _host_open_refusal(text)
        if refusal is not None:
            print(refusal, file=sys.stderr)
            return 1
    kind = (
        "synthetic_replay"
        if args.fixture_origin == "synthetic"
        else "projection_on_synthetic"
    )
    receipt, problems = assemble_receipt(
        receipt_kind=kind,
        fixture_origin=args.fixture_origin,
        jsonl=list(args.jsonl),
        expectation=list(args.expectation),
    )
    if problems or receipt is None:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    _print_receipt(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
