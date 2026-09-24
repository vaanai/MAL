"""paper-fill-sim-host-local-dry-run-receipt-capture-v0.

Proposed registration. Citeable capture of refuse and receipt outcomes from
paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 (#60).

The parent dry-run CLI is not rewritten. This module calls it. It does not
open /var/lib/mal, does not SSH, and does not embed a receipt body, a parent
batch, host bytes, a horizon, or a return.

No measure exit. Sealed book stays incomplete. measure.kind stays none.
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

from tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 import (
    FORBIDDEN_KEYS as PARENT_FORBIDDEN_KEYS,
    HONESTY_FALSE,
    HOST_EXPECTATION,
    HOST_JSONL,
    HOST_ROOT,
    RECEIPT_ID,
    SCHEMA_VERSION as PARENT_SCHEMA_VERSION,
    SOFT_WATCH_ITEMS as PARENT_DRY_RUN_SOFT_WATCH_ITEMS,
    _kernel_realpath_refusal,
    _validate_path_refusal,
    example_operator_declared,
    example_projection_on_synthetic,
    example_synthetic_replay,
    main as dry_main,
    validate_receipt,
)
from tools.paper_fill_sim_hot_packet_evaluate_v0 import FILL_SIM_REJECT, FILL_SIM_RUNNER

SCHEMA_VERSION = "paper_fill_sim_host_local_dry_run_receipt_capture_v0"
CAPTURE_TYPE = "paper_dry_run_capture"
CAPTURE_ID = "paper-fill-sim-host-local-dry-run-receipt-capture-v0"
PARENT_COMMIT = "d3b5554"
PARENT_PR = 60
PARENT_CLI = (
    "python -m tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0"
)
ALIGN_ID = "paper-fill-sim-incomplete-rpc-honesty-schema-align-v0"
ALIGN_COMMIT = "677e88b"
ALIGN_PR = 61
INCOMPLETE_REASON = "capture_does_not_close_the_sealed_book"
COURIER_DAYS: tuple[str, ...] = ("2026-09-20", "2026-09-21")

SOFT_WATCH_SOURCE = (
    "hot_packet_v0_soft_gate_pr49_paper_evaluate_pr50_scoreboard_pr51_"
    "fill_sim_hot_packet_evaluate_v0_pr57_fill_sim_scoreboard_sealed_fixture_v0_pr58_"
    "fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0_pr59_"
    "fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0_pr60_"
    "fill_sim_incomplete_rpc_honesty_schema_align_v0_pr61"
)
INHERITED_FROM: tuple[str, ...] = (
    "hot_packet_v0_soft_gate_pr49",
    "paper_evaluate_hot_packet_v0_pr50",
    "paper_scoreboard_sealed_fixture_v0_pr51",
    "paper_fill_sim_hot_packet_evaluate_v0_pr57",
    "paper_fill_sim_scoreboard_sealed_fixture_v0_pr58",
    "paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0_pr59",
    "paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0_pr60",
    "paper_fill_sim_incomplete_rpc_honesty_schema_align_v0_pr61",
)
LOCAL_SOFT_WATCH_ITEMS: tuple[str, ...] = (
    "capture_records_refusal_not_a_measure",
    "cited_receipt_is_not_a_host_extract",
    "path_string_on_a_refuse_is_not_file_bytes",
)
SOFT_WATCH_ITEMS: tuple[str, ...] = PARENT_DRY_RUN_SOFT_WATCH_ITEMS + LOCAL_SOFT_WATCH_ITEMS

RECEIPT_PATHS: dict[str, str] = {
    "synthetic_replay": (
        "fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/"
        "synthetic_replay.json"
    ),
    "projection_on_synthetic": (
        "fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/"
        "projection_on_synthetic.json"
    ),
    "operator_declared": (
        "fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/"
        "operator_declared.json"
    ),
}
RECEIPT_BUILDERS = {
    "synthetic_replay": example_synthetic_replay,
    "projection_on_synthetic": example_projection_on_synthetic,
    "operator_declared": example_operator_declared,
}

OUTSIDE_REPO = "/tmp/mal-paper-fill-sim-capture-outside-repo"
HOST_VALIDATE_PATH = f"{HOST_ROOT}/paper/{RECEIPT_ID}/receipt.json"
DISHONEST_PROBES: tuple[str, ...] = (
    "closed_book_claim",
    "measure_kind",
    "var_lib_mal_opened",
    "var_lib_mal_read",
    "invented_ev",
)
FILL_SIM_STATUS_ARMS: tuple[str, ...] = (FILL_SIM_RUNNER, FILL_SIM_REJECT)

REPO = Path(__file__).resolve().parents[1]
DRY_MODULE = "tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0"


def _err(errors: list[str], message: str) -> None:
    errors.append(message)


def _honesty() -> dict[str, Any]:
    body: dict[str, Any] = {"registration": "proposed"}
    for key in HONESTY_FALSE:
        body[key] = False
    return body


def _host_path_forms() -> list[dict[str, Any]]:
    """Input catalog only. Path strings are not file bytes."""
    return [
        {
            "form": "lexical_absolute_validate",
            "command": "validate",
            "cwd": None,
            "path": HOST_VALIDATE_PATH,
        },
        {
            "form": "lexical_absolute_receipt",
            "command": "receipt",
            "cwd": None,
            "jsonl": [HOST_JSONL[day] for day in COURIER_DAYS],
            "expectation": [HOST_EXPECTATION[day] for day in COURIER_DAYS],
        },
        {
            "form": "cwd_abspath_validate",
            "command": "validate",
            "cwd": "/",
            "path": "var/lib/mal/paper/receipt.json",
        },
        {
            "form": "cwd_abspath_validate",
            "command": "validate",
            "cwd": "/var/lib",
            "path": "mal/paper/receipt.json",
        },
        {
            "form": "realpath_symlink_dotdot_validate",
            "command": "validate",
            "cwd": None,
            "path_stored": False,
        },
        {
            "form": "outside_repo_receipt",
            "command": "receipt",
            "cwd": None,
            "jsonl": [f"{OUTSIDE_REPO}/observe-{day}.jsonl" for day in COURIER_DAYS],
            "expectation": [
                f"{OUTSIDE_REPO}/sealed_day_{day}_expectation.json" for day in COURIER_DAYS
            ],
        },
    ]


def _envelope(
    *,
    capture_kind: str,
    subject: Mapping[str, Any],
    outcome: Mapping[str, Any],
    recorded: Mapping[str, Any],
    knowable_basis: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "type": CAPTURE_TYPE,
        "id": CAPTURE_ID,
        "paper_only": True,
        "capture_kind": capture_kind,
        "parent_dry_run": {
            "id": RECEIPT_ID,
            "schema_version": PARENT_SCHEMA_VERSION,
            "pull_request": PARENT_PR,
            "commit": PARENT_COMMIT,
            "cli": PARENT_CLI,
            "rewritten_by_this_stamp": False,
        },
        "honesty_align": {
            "id": ALIGN_ID,
            "pull_request": ALIGN_PR,
            "commit": ALIGN_COMMIT,
            "rewritten_by_this_stamp": False,
        },
        "subject": dict(subject),
        "outcome": dict(outcome),
        "recorded": dict(recorded),
        "carries": {
            "parent_batch_body": False,
            "receipt_body": False,
            "horizons": False,
            "delta_exec": False,
            "rollup_counts": False,
            "host_bytes": False,
        },
        "knowable_at_t": {
            "basis": knowable_basis,
            "host_file_bytes_required": False,
            "numeric_horizon_known": False,
            "delta_exec_known": False,
            "return_known": False,
            "sealed_book_status": "incomplete",
        },
        "full_book": {
            "policy": "dec007_both_arms_retained",
            "arm_retained": True,
            "deletes_detect_history": False,
            "reject_stamps_dropped": 0,
            "local_set_is_not_the_sealed_book": True,
            "both_arms_unchanged": True,
            "fill_sim_status_arms": list(FILL_SIM_STATUS_ARMS),
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


def _blank_recorded() -> dict[str, Any]:
    return {
        "receipt_kind": None,
        "receipt_path": None,
        "host_jsonl_read_on_receipt": None,
        "host_path_declared_on_receipt": None,
        "parent_invoked_on_receipt": None,
        "paths_opened_on_receipt": None,
        "parent_digest_included": None,
        "fill_sim_status_arms_on_receipt": None,
        "receipt_body_embedded": False,
        "host_bytes_embedded": False,
        "probe_document_checked_in": False,
    }


def _arms_on_receipt(receipt: Mapping[str, Any]) -> list[str] | None:
    digest = receipt.get("parent_digest")
    if not isinstance(digest, Mapping) or not digest.get("included"):
        return None
    arms = digest.get("fill_sim_status_arms")
    if arms == list(FILL_SIM_STATUS_ARMS):
        return list(FILL_SIM_STATUS_ARMS)
    return None


def _run_dry(argv: list[str], cwd: str | None) -> tuple[int, str, list[str], list[object]]:
    """Call the #60 dry-run CLI. Count read_text and parent_main calls."""
    reads: list[str] = []
    parent_calls: list[object] = []

    def _read_text(self: Path, *args: object, **kwargs: object) -> str:
        reads.append(os.fspath(self))
        raise AssertionError(f"read_text during capture refuse: {self}")

    def _parent(argv: object = None) -> int:
        parent_calls.append(argv)
        return 0

    err = io.StringIO()
    getcwd_patch = (
        mock.patch(f"{DRY_MODULE}.os.getcwd", return_value=cwd)
        if cwd is not None
        else mock.patch(f"{DRY_MODULE}.os.getcwd", wraps=os.getcwd)
    )
    with (
        mock.patch.object(Path, "read_text", _read_text),
        mock.patch(f"{DRY_MODULE}.parent_main", _parent),
        getcwd_patch,
        redirect_stdout(io.StringIO()),
        redirect_stderr(err),
    ):
        code = dry_main(argv)
    return code, err.getvalue(), reads, parent_calls


def _exercise_realpath() -> tuple[int, str, list[str], list[object]]:
    """Symlink/.. under /var/lib/mal. The temp path is not stored on the capture."""
    tail = "link/../var/lib/mal/paper/receipt.json"
    with tempfile.TemporaryDirectory() as tmp:
        os.symlink("/var", os.path.join(tmp, "link"))
        absolute = os.path.join(tmp, tail)
        lexical = os.path.normpath(absolute)
        if lexical == HOST_ROOT or lexical.startswith(HOST_ROOT + os.sep):
            return 0, "lexical normpath already under host root; not the realpath form", [], []
        if not os.path.realpath(absolute).startswith(HOST_ROOT + os.sep):
            return 0, "realpath did not land under /var/lib/mal", [], []
        return _run_dry(["validate", absolute], None)


def _refuse_one(form: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    name = str(form["form"])
    if name == "realpath_symlink_dotdot_validate":
        code, err, reads, parent_calls = _exercise_realpath()
    elif form["command"] == "validate":
        code, err, reads, parent_calls = _run_dry(["validate", str(form["path"])], form.get("cwd"))
    else:
        argv = ["receipt", "--fixture-origin", "sealed_row_projection"]
        for path in form["jsonl"]:
            argv.extend(["--jsonl", path])
        for path in form["expectation"]:
            argv.extend(["--expectation", path])
        code, err, reads, parent_calls = _run_dry(argv, None)
    if code != 1:
        _err(errors, f"{name}: dry-run exit {code}, expected 1")
    if reads:
        _err(errors, f"{name}: read_text was called")
    if parent_calls:
        _err(errors, f"{name}: parent batch was invoked")
    if name == "outside_repo_receipt":
        if "outside the repo" not in err and HOST_ROOT not in err:
            _err(errors, f"{name}: stderr did not refuse the path")
    elif "does not open" not in err:
        _err(errors, f"{name}: stderr did not refuse the open")
    return errors


def capture_receipt(receipt_kind: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Cite one checked-in #60 receipt. The body is not copied onto this object."""
    if receipt_kind not in RECEIPT_BUILDERS:
        return None, [f"receipt_kind: must be one of {list(RECEIPT_BUILDERS)}"]
    receipt = RECEIPT_BUILDERS[receipt_kind]()
    problems = validate_receipt(receipt)
    if problems:
        return None, problems
    rel = RECEIPT_PATHS[receipt_kind]
    on_disk = json.loads((REPO / rel).read_text(encoding="utf-8"))
    if on_disk != receipt:
        return None, [f"{rel}: checked-in receipt does not match the dry-run rebuild"]
    cited = _blank_recorded()
    cited.update(
        {
            "receipt_kind": receipt_kind,
            "receipt_path": rel,
            "host_jsonl_read_on_receipt": receipt["input"]["host_jsonl_read"],
            "host_path_declared_on_receipt": receipt["input"]["host_path_declared"],
            "parent_invoked_on_receipt": receipt["input"]["parent_invoked"],
            "paths_opened_on_receipt": receipt["input"]["paths_opened"],
            "parent_digest_included": receipt["parent_digest"]["included"],
            "fill_sim_status_arms_on_receipt": _arms_on_receipt(receipt),
        }
    )
    parent_invoked = receipt_kind != "operator_declared"
    return _envelope(
        capture_kind="receipt",
        subject={
            "kind": "cited_receipt",
            "receipt_kind": receipt_kind,
            "receipt_path": rel,
        },
        outcome={
            "recorded": True,
            "refused": False,
            "reason": "receipt_rebuilt",
            "exit_class": 0,
            "path_opened": receipt["input"]["paths_opened"],
            "parent_batch_invoked_by_capture": parent_invoked,
            "var_lib_mal_opened": False,
            "forms_refused": 0,
            "probes_refused": 0,
        },
        recorded=cited,
        knowable_basis="rebuilt_receipt_honesty_digest",
    ), []


def capture_refuse_host_path() -> tuple[dict[str, Any] | None, list[str]]:
    """Re-execute the #60 host-open refuses. No path contents are stored."""
    forms = _host_path_forms()
    errors: list[str] = []
    for form in forms:
        errors.extend(_refuse_one(form))
    if errors:
        return None, errors
    return _envelope(
        capture_kind="refuse",
        subject={"kind": "host_path_not_opened", "forms": forms},
        outcome={
            "recorded": False,
            "refused": True,
            "reason": "host_path_not_opened",
            "exit_class": 1,
            "path_opened": False,
            "parent_batch_invoked_by_capture": False,
            "var_lib_mal_opened": False,
            "forms_refused": len(forms),
            "probes_refused": 0,
        },
        recorded=_blank_recorded(),
        knowable_basis="host_path_refuse_without_open",
    ), []


def _apply_probe(receipt: dict[str, Any], probe: str) -> None:
    if probe == "closed_book_claim":
        receipt["dual_read"]["closed_book_claim"] = True
    elif probe == "measure_kind":
        receipt["measure"]["kind"] = "alpha"
    elif probe == "var_lib_mal_opened":
        receipt["input"]["var_lib_mal_opened"] = True
    elif probe == "var_lib_mal_read":
        receipt["honesty"]["var_lib_mal_read"] = True
    elif probe == "invented_ev":
        receipt["measure"]["invented_ev"] = True
    else:
        raise KeyError(probe)


def capture_refuse_dishonest() -> tuple[dict[str, Any] | None, list[str]]:
    """In-memory dishonest receipts. The probes are not checked in."""
    base = example_synthetic_replay()
    errors: list[str] = []
    for probe in DISHONEST_PROBES:
        tampered = copy.deepcopy(base)
        _apply_probe(tampered, probe)
        if not validate_receipt(tampered):
            _err(errors, f"{probe}: dry-run validate accepted a dishonest receipt")
    if errors:
        return None, errors
    return _envelope(
        capture_kind="refuse",
        subject={
            "kind": "dishonest_receipt_refused",
            "probes": list(DISHONEST_PROBES),
            "probe_document_checked_in": False,
        },
        outcome={
            "recorded": False,
            "refused": True,
            "reason": "dishonest_receipt_refused",
            "exit_class": 1,
            "path_opened": False,
            "parent_batch_invoked_by_capture": True,
            "var_lib_mal_opened": False,
            "forms_refused": 0,
            "probes_refused": len(DISHONEST_PROBES),
        },
        recorded=_blank_recorded(),
        knowable_basis="dishonest_receipt_probe",
    ), []


def _key_forbidden(key: str) -> bool:
    return key.lower() in PARENT_FORBIDDEN_KEYS


def _reject_forbidden_keys(errors: list[str], obj: Any, path: str) -> None:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            if _key_forbidden(str(key)):
                _err(errors, f"{child}: forbidden on {CAPTURE_ID}")
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


def _rebuild(capture: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    kind = capture.get("capture_kind")
    subject = capture.get("subject")
    if not isinstance(subject, Mapping):
        return None, ["subject: must be an object"]
    subject_kind = subject.get("kind")
    if kind == "receipt" and subject_kind == "cited_receipt":
        receipt_kind = subject.get("receipt_kind")
        if not isinstance(receipt_kind, str):
            return None, ["subject.receipt_kind: required"]
        return capture_receipt(receipt_kind)
    if kind == "refuse" and subject_kind == "host_path_not_opened":
        return capture_refuse_host_path()
    if kind == "refuse" and subject_kind == "dishonest_receipt_refused":
        return capture_refuse_dishonest()
    return None, ["capture: kind and subject.kind do not name a capture"]


def validate_capture(capture: Any) -> list[str]:
    """Return errors. Empty list means the capture matches a rebuild."""
    if not isinstance(capture, Mapping):
        return ["capture: must be a JSON object"]
    errors: list[str] = []
    _reject_forbidden_keys(errors, capture, "capture")
    if errors:
        return errors
    expected, problems = _rebuild(capture)
    if problems or expected is None:
        return problems or ["capture: did not rebuild"]
    if capture != expected:
        diffs: list[str] = []
        _diff_paths(expected, capture, "", diffs)
        if not diffs:
            diffs.append("capture: does not match the capture shape")
        return diffs
    return []


EXAMPLES: dict[str, Any] = {
    "synthetic-replay": lambda: capture_receipt("synthetic_replay"),
    "projection-on-synthetic": lambda: capture_receipt("projection_on_synthetic"),
    "operator-declared": lambda: capture_receipt("operator_declared"),
    "refuse-host-path": capture_refuse_host_path,
    "refuse-dishonest-receipt": capture_refuse_dishonest,
}


def _print_capture(capture: Mapping[str, Any]) -> None:
    json.dump(capture, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def _refuse_capture_path(text: str) -> str | None:
    """Same open-refuse as the #60 dry-run validate, before read_text."""
    refusal = _validate_path_refusal(text)
    if refusal is None:
        refusal = _validate_path_refusal(os.path.abspath(text))
    if refusal is None:
        refusal = _kernel_realpath_refusal(text)
    return refusal


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="python -m tools.paper_fill_sim_host_local_dry_run_receipt_capture_v0",
        description=(
            "Capture citeable refuse and receipt outcomes from the fill-sim "
            "host-local dry-run (#60). Sealed book stays incomplete. "
            "No /var/lib/mal read. No measure exit."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    example = sub.add_parser("example", help="Print one capture")
    example.add_argument("--which", choices=sorted(EXAMPLES), required=True)
    validate = sub.add_parser("validate", help="Validate one capture JSON file")
    validate.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "example":
        capture, problems = EXAMPLES[args.which]()
        if problems or capture is None:
            for problem in problems or ["example: did not build"]:
                print(problem, file=sys.stderr)
            return 1
        _print_capture(capture)
        problems = validate_capture(capture)
        if problems:
            print("example failed its own validator:", file=sys.stderr)
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        return 0

    text = os.fspath(args.path)
    refusal = _refuse_capture_path(text)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 1
    try:
        payload = json.loads(args.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{args.path}: {exc}", file=sys.stderr)
        return 1
    problems = validate_capture(payload)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print(f"ok {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
