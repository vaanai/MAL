#!/usr/bin/env python3
"""The shared MAL Console job entry point (docs/console-plan.md §9 item 2;
MiScusi's job contract, `~/MiScusi` `docs/CONSOLE_API.md` §3).

    python3 -m tools.mal_job <template> [--params-json '{...}'] [--out PATH]

`<template>` is one of the keys in `TEMPLATES` below, each backed by a JSON
Schema in `templates/<template>.schema.json` and a runner in
`tools.mal_templates`. Params come from `--params-json`, or -- when that flag
is absent, matching how MiScusi actually launches a job -- from
`MISCUSI_PARAM_<NAME>` environment variables (JSON-decoded per value when
possible, else kept as the raw string).

This module is a NEW file. It does not modify any existing tool -- it only
imports `tools.mal_catalog` (the read guard, docs/contracts/data-catalog.md)
and `tools.mal_result` (the result.v1 builder, docs/contracts/result-json.md).

Steps, strictly in order, each one a hard stop before the next:

  (a) validate `params` against the template's JSON Schema (draft 2020-12).
      Unknown params, missing required params, and out-of-range/out-of-enum
      values are all refused here -- exit 2.
  (b) resolve the job's role. `MISCUSI_JOB_ROLE` is authoritative; when it is
      completely absent (a by-hand run, not a MiScusi job) this defaults to
      "exploration" WITH a printed warning -- never silently. Anything other
      than role == "exploration" is refused -- exit 3 -- before any data is
      touched. These templates are exploration-only by construction
      (docs/console-plan.md §2 rule 1): there is no path through this module
      that reaches a confirmation-oneshot or ops read.
  (c) resolve the data blocks the params would read (the template's own
      `resolve_data_blocks`, a pure function -- no file is opened yet), and
      call `tools.mal_catalog.check_read` for EVERY block against the real
      `docs/HOLDOUT_LEDGER.md`. Any DENY refuses the whole job with every
      reason -- exit 3 -- before any data is touched.
  (d) call the template's runner (`tools.mal_templates.<template>.run`).
  (e) build a result.v1 via `tools.mal_result` (metrics for both fail-model
      legs, gate ticks, stage "exploring", the data blocks read, and a tries
      entry via `append_try`/`tries_summary`), write it to
      `$MISCUSI_OUTPUT_DIR/result.json` (or `--out`), and also write
      MiScusi's own `MISCUSI_RESULT` JSON.
  (f) report `MISCUSI_PROGRESS` at each of the above stage boundaries.

Exit codes: 0 success, 2 schema/CLI error, 3 refused (role or catalog), 1 an
unexpected runner exception (including the intentional `NotImplementedError`
a not-yet-wired runner raises).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import mal_catalog, mal_result
from tools.exp011_freeze import _git_commit
from tools.mal_templates import entry_filter as _entry_filter_tpl
from tools.mal_templates import exit as _exit_tpl

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
DEFAULT_LEDGER_PATH = REPO_ROOT / "docs" / "HOLDOUT_LEDGER.md"

ALLOWED_ROLE = "exploration"  # the only role any template registered here may ever run as

TEMPLATES: dict[str, dict[str, Any]] = {
    "explore_entry_filter": {
        "schema": TEMPLATES_DIR / "explore_entry_filter.schema.json",
        "resolve_data_blocks": _entry_filter_tpl.resolve_data_blocks,
        "run": _entry_filter_tpl.run,
    },
    "explore_exit": {
        "schema": TEMPLATES_DIR / "explore_exit.schema.json",
        "resolve_data_blocks": _exit_tpl.resolve_data_blocks,
        "run": _exit_tpl.run,
    },
}


class JobRefused(Exception):
    """A refusal that should end the process with the given reasons printed,
    before any data is touched. `exit_code` distinguishes schema/CLI errors
    (2) from role/catalog refusals (3)."""

    def __init__(self, reasons: Sequence[str], exit_code: int) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)
        self.exit_code = exit_code


# --- (a) schema validation ---------------------------------------------------


def load_schema(template: str) -> dict[str, Any]:
    if template not in TEMPLATES:
        raise JobRefused([f"unknown template {template!r}; known templates: {sorted(TEMPLATES)}"], exit_code=2)
    return json.loads(TEMPLATES[template]["schema"].read_text(encoding="utf-8"))


def validate_params(schema: Mapping[str, Any], params: Mapping[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty == valid)."""
    import jsonschema

    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in validator.iter_errors(params)]


def params_from_env() -> dict[str, Any]:
    """`MISCUSI_PARAM_<NAME>` -> `{name: value}` (name lower-cased to match the
    schema's property names). Each value is JSON-decoded when possible (so
    numbers, booleans, lists and objects round-trip), else kept as the raw
    string (so a plain enum value like `migrate` still works)."""
    out: dict[str, Any] = {}
    prefix = "MISCUSI_PARAM_"
    for key, raw in os.environ.items():
        if not key.startswith(prefix):
            continue
        name = key[len(prefix) :].lower()
        try:
            out[name] = json.loads(raw)
        except json.JSONDecodeError:
            out[name] = raw
    return out


# --- (b) role -----------------------------------------------------------------


def resolve_role() -> tuple[str, list[str]]:
    """`MISCUSI_JOB_ROLE` is authoritative. A completely absent env var (a
    by-hand run, not a MiScusi job) defaults to "exploration" WITH a warning
    -- never silently defaulted without saying so."""
    warnings: list[str] = []
    env_role = os.environ.get("MISCUSI_JOB_ROLE")
    if env_role is None:
        warnings.append("MISCUSI_JOB_ROLE is not set; defaulting to 'exploration' for a by-hand run")
        return "exploration", warnings
    return env_role, warnings


def require_exploration_role(role: str) -> None:
    if role != ALLOWED_ROLE:
        raise JobRefused(
            [f"role {role!r} is not allowed; these templates only ever run as role={ALLOWED_ROLE!r}"],
            exit_code=3,
        )


# --- (c) catalog --------------------------------------------------------------


def check_all_blocks(data_blocks: Sequence[Mapping[str, Any]], role: str, ledger_path: Path = DEFAULT_LEDGER_PATH) -> None:
    """Calls `tools.mal_catalog.check_read` for every block in `data_blocks`
    against a freshly parsed `ledger_path`. Any DENY collects every reason
    and raises `JobRefused` -- no data has been opened by the time this
    raises or returns."""
    ledger_text = ledger_path.read_text(encoding="utf-8")
    blocks = mal_catalog.parse_ledger(ledger_text)
    reasons: list[str] = []
    for b in data_blocks:
        ok, why = mal_catalog.check_read(
            blocks, role, b["host"], b["start_hour"], b["end_hour_exclusive"], exp_id=b.get("exp_id")
        )
        if not ok:
            reasons.extend(f"{b['host']} {b['start_hour']}..{b['end_hour_exclusive']}: {r}" for r in why)
    if reasons:
        raise JobRefused(reasons, exit_code=3)


# --- (f) progress ---------------------------------------------------------------


def report_progress(pct: int, note: str) -> None:
    print(f"[progress] {pct}% {note}", file=sys.stderr, flush=True)
    path = os.environ.get("MISCUSI_PROGRESS")
    if path:
        Path(path).write_text(json.dumps({"pct": pct, "note": note}), encoding="utf-8")


# --- (e) result.v1 + MISCUSI_RESULT --------------------------------------------


def _resolve_out_path(out_arg: str | None) -> Path:
    if out_arg is not None:
        return Path(out_arg)
    out_dir = os.environ.get("MISCUSI_OUTPUT_DIR")
    if out_dir:
        return Path(out_dir) / "result.json"
    return Path("result.json")


def _ledger_sha256(ledger_path: Path = DEFAULT_LEDGER_PATH) -> str:
    import hashlib

    return hashlib.sha256(ledger_path.read_bytes()).hexdigest()


def _write_miscusi_result(result: dict[str, Any], data_version: str) -> None:
    path = os.environ.get("MISCUSI_RESULT")
    if not path:
        return
    metrics_flat = result["metrics"]["flat"]
    metrics_press = result["metrics"]["pressure_s1"]
    doc = {
        "ok": True,
        "metrics": {
            "flat_mean_pct": metrics_flat["mean_pct"],
            "pressure_mean_pct": metrics_press["mean_pct"],
            "flat_ex_top3_sol": metrics_flat["ex_top3_sol"],
            "pressure_ex_top3_sol": metrics_press["ex_top3_sol"],
            "n_trades": metrics_flat["n_trades"],
            "pass_both": result["gate"]["pass_both"],
        },
        "summary": (
            f"{result['tool']}: n={metrics_flat['n_trades']} "
            f"flat_mean={metrics_flat['mean_pct']} pressure_mean={metrics_press['mean_pct']} "
            f"pass_both={result['gate']['pass_both']} "
            f"(variant {result['tries']['variant_n']} of {result['tries']['of_m']} on this data)"
        ),
        "outputs": ["result.json"],
        "dataVersion": data_version,
    }
    Path(path).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --- main -----------------------------------------------------------------------


def _refuse(reasons: Sequence[str], label: str) -> None:
    print(f"REFUSED ({label}):", file=sys.stderr)
    for r in reasons:
        print(f"  - {r}", file=sys.stderr)


def run_job(
    template: str,
    params: Mapping[str, Any],
    *,
    out_arg: str | None = None,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    command: str = "",
) -> int:
    """The full (a)-(f) pipeline, callable directly by tests without going
    through argv/env plumbing for params. Env vars (`MISCUSI_JOB_ROLE`,
    `MISCUSI_OUTPUT_DIR`, `MISCUSI_RESULT`, `MISCUSI_PROGRESS`,
    `MAL_TRIES_LOG`) are still read from `os.environ` -- tests set those via
    `monkeypatch.setenv`."""
    try:
        schema = load_schema(template)
    except JobRefused as exc:
        _refuse(exc.reasons, "template")
        return exc.exit_code

    report_progress(0, "validating params")
    errors = validate_params(schema, params)
    if errors:
        _refuse(errors, "params")
        return 2

    role, warnings = resolve_role()
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    try:
        require_exploration_role(role)
    except JobRefused as exc:
        _refuse(exc.reasons, "role")
        return exc.exit_code

    report_progress(10, "resolving data blocks")
    resolve_fn: Callable[[Mapping[str, Any]], list[dict[str, Any]]] = TEMPLATES[template]["resolve_data_blocks"]
    data_blocks = resolve_fn(params)

    report_progress(15, "checking data catalog")
    try:
        check_all_blocks(data_blocks, role, ledger_path=ledger_path)
    except JobRefused as exc:
        _refuse(exc.reasons, "catalog")
        return exc.exit_code

    report_progress(20, "running template")
    run_fn: Callable[[Mapping[str, Any]], dict[str, Any]] = TEMPLATES[template]["run"]
    t0 = time.time()
    run_result = run_fn(params)
    runtime_s = time.time() - t0

    report_progress(80, "building result.v1")
    out_path = _resolve_out_path(out_arg)
    tries_log = os.environ.get("MAL_TRIES_LOG")
    appended = mal_result.append_try(
        tries_log,
        tool=f"mal_job:{template}",
        config=dict(params),
        data_blocks=data_blocks,
        result_path=str(out_path),
        role=role,
    )
    summary = mal_result.tries_summary(tries_log, appended["data_key"])
    tries = {"data_key": appended["data_key"], "variant_n": appended["variant_n"], "of_m": summary["of_m"]}

    result = mal_result.build_result(
        tool=f"mal_job:{template}",
        git_sha=_git_commit(),
        command=command or f"python3 -m tools.mal_job {template}",
        config=dict(params),
        role=role,
        data_blocks=data_blocks,
        stage="exploring",
        trades_flat=run_result["trades_flat"],
        trades_pressure_s1=run_result["trades_pressure_s1"],
        tries=tries,
        n_candidates=run_result.get("n_candidates"),
        n_days=run_result.get("n_days"),
        runtime_s=runtime_s,
        peak_rss_mb=run_result.get("peak_rss_mb"),
        notes=run_result.get("notes"),
    )
    mal_result.write_result(out_path, result)
    _write_miscusi_result(result, _ledger_sha256(ledger_path))

    report_progress(100, "done")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mal_job", description=__doc__)
    ap.add_argument("template", help="one of: " + ", ".join(sorted(TEMPLATES)))
    ap.add_argument("--params-json", default=None, help="JSON object of params; defaults to MISCUSI_PARAM_* env vars")
    ap.add_argument("--out", default=None, help="override the result.json output path")
    args = ap.parse_args(argv)

    if args.params_json is not None:
        try:
            params = json.loads(args.params_json)
        except json.JSONDecodeError as exc:
            _refuse([f"--params-json is not valid JSON: {exc}"], "params")
            return 2
    else:
        params = params_from_env()

    return run_job(args.template, params, out_arg=args.out, command=" ".join(["python3", "-m", "tools.mal_job", *sys.argv[1:]]))


if __name__ == "__main__":
    sys.exit(main())
