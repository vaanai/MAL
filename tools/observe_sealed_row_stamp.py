#!/usr/bin/env python3
"""Scout sealed-row stamp sampler for PumpPortal observe JSONL (EXP-001).

Standalone, stdlib-only, no network. Scans day-wide JSONL hot rows and prints a
thin PASS/FAIL summary plus soft inventory counts. Soft inventory never flips
PASS → FAIL by itself.

Does not reopen Scout (manager) PASS on Vaan's local capture. Replaces ad-hoc
``data/observe/_scout_*`` audits with a durable local CLI.

Usage:
    python tools/observe_sealed_row_stamp.py path/to/a.jsonl [path/to/b.jsonl ...]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "observe_sealed_row_stamp_v0"
LEGAL_STAGES = frozenset(
    {
        "bonding",
        "bonding_complete",
        "migrating",
        "pumpswap",
        "legacy_raydium",
        "UNK",
    }
)
REGIME_KEYS = (
    "env",
    "source",
    "stream",
    "stage",
    "quote",
    "commitment",
    "venue",
    "instr",
    "fee",
    "market",
)
KNOWABLE_PIPE_DIMS = ("quote", "instr", "fee", "venue")
MIGRATION_TX_TYPES = frozenset({"migrate", "migration"})
UNVERIFIED_MARKERS = ("assumed", "unverified", "pending")
NON_PUMP_POOL_MARKERS = (
    "bonk",
    "letsbonk",
    "raydium",
    "meteora",
    "orca",
    "moonshot",
    "bags",
)
PUMPISH_POOL_LABELS = frozenset(
    {"pump", "pump-amm", "pumpswap", "pump_amm", "pump_program", "pumpfun"}
)
MAYHEM_KEYS = ("is_mayhem_mode", "isMayhemMode", "isMayhem")
MAX_EXAMPLES = 5
HARD_GATES = ("dual_clocks", "stage_map", "regime_id", "knowable_at_t")

# Inventory keys copied from observe/regime.py (stamp stays standalone).
KNOWN_CREATE_FIELDS = frozenset(
    {
        "txType",
        "signature",
        "mint",
        "traderPublicKey",
        "name",
        "symbol",
        "uri",
        "bondingCurveKey",
        "initialBuy",
        "solAmount",
        "vTokensInBondingCurve",
        "vSolInBondingCurve",
        "marketCapSol",
        "timestamp",
        "blockTime",
    }
)
KNOWN_MIGRATION_FIELDS = frozenset(
    {
        "txType",
        "mint",
        "pool",
        "signature",
        "traderPublicKey",
        "timestamp",
        "blockTime",
    }
)


def parse_iso(value: Any) -> datetime | None:
    """Parse ISO-8601 timestamps used on hot rows. Empty/non-string → None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_regime_id(raw: Any) -> tuple[dict[str, str] | None, str | None]:
    """Parse DEC-004 pipe ``key=value`` encoding. Returns (map, error)."""
    if not isinstance(raw, str) or not raw.strip():
        return None, "regime_id missing or empty"
    if " " in raw and (" |" in raw or "| " in raw or " =" in raw or "= " in raw):
        return None, "regime_id has spaces around pipe or '='"
    parts = raw.split("|")
    if len(parts) != len(REGIME_KEYS):
        return None, (
            f"regime_id expected {len(REGIME_KEYS)} tokens, got {len(parts)}"
        )
    parsed: dict[str, str] = {}
    for idx, (expected_key, token) in enumerate(zip(REGIME_KEYS, parts)):
        if "=" not in token:
            return None, f"regime_id token {idx} is not key=value"
        key, value = token.split("=", 1)
        if key != expected_key:
            return None, (
                f"regime_id key order: expected {expected_key} at {idx}, got {key}"
            )
        if not value:
            return None, f"regime_id empty value for {key}"
        parsed[key] = value
    return parsed, None


def _tx_type(row: dict[str, Any]) -> str:
    payload = row.get("ws_payload")
    raw = row.get("txType")
    if isinstance(payload, dict) and payload.get("txType") is not None:
        raw = payload.get("txType")
    if not isinstance(raw, str):
        return ""
    return raw.strip().lower()


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("ws_payload")
    return payload if isinstance(payload, dict) else {}


def _pool_value(row: dict[str, Any]) -> Any:
    payload = _payload(row)
    if "pool" in payload:
        return payload.get("pool")
    return row.get("pool")


def _looks_non_pump_pool(pool: Any) -> bool:
    if pool is None or isinstance(pool, bool):
        return False
    text = str(pool).strip().lower()
    if not text or text in PUMPISH_POOL_LABELS:
        return False
    return any(marker in text for marker in NON_PUMP_POOL_MARKERS)


def _is_unverified_token(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    low = value.lower()
    return any(marker in low for marker in UNVERIFIED_MARKERS)


def _example(path: Path, line_no: int, reason: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "file": str(path),
        "line": line_no,
        "reason": reason,
        "signature": row.get("signature"),
        "mint": row.get("mint"),
        "stage": row.get("stage"),
        "txType": row.get("txType"),
    }


def check_dual_clocks(row: dict[str, Any]) -> str | None:
    """Hard: t_ws present + parseable. null t_event is legal. Void if t_ws missing."""
    if "t_ws" not in row or row.get("t_ws") in (None, ""):
        return "t_ws missing (dual-clock void)"
    if parse_iso(row.get("t_ws")) is None:
        return "t_ws present but not parseable ISO-8601"
    t_event = row.get("t_event", None)
    if t_event in (None, ""):
        return None
    if parse_iso(t_event) is None:
        return "t_event non-null but not parseable ISO-8601"
    return None


def check_stage_map(row: dict[str, Any]) -> str | None:
    stage = row.get("stage")
    if not isinstance(stage, str) or stage not in LEGAL_STAGES:
        return f"stage not in legal map: {stage!r}"
    tx = _tx_type(row)
    if tx in MIGRATION_TX_TYPES and stage != "migrating":
        return f"txType={tx!r} must map to stage=migrating (got {stage!r})"
    return None


def check_regime_id(row: dict[str, Any]) -> str | None:
    parsed, err = parse_regime_id(row.get("regime_id"))
    if err:
        return err
    assert parsed is not None
    stage = row.get("stage")
    if parsed["stage"] != stage:
        return (
            f"regime_id stage={parsed['stage']!r} does not match row.stage={stage!r}"
        )
    return None


def check_knowable_at_t(row: dict[str, Any]) -> str | None:
    knowable = row.get("knowable_at_t")
    if not isinstance(knowable, dict):
        return "knowable_at_t missing or not an object"
    parsed, _err = parse_regime_id(row.get("regime_id"))
    for dim in KNOWABLE_PIPE_DIMS:
        row_val = knowable.get(dim)
        if parsed is not None:
            if dim not in knowable:
                return f"knowable_at_t missing {dim} (required vs regime pipe)"
            if not isinstance(row_val, str):
                return f"knowable_at_t.{dim} is not a string"
            pipe_val = parsed.get(dim)
            if row_val != pipe_val:
                return (
                    f"knowable_at_t.{dim}={row_val!r} != regime_id {dim}={pipe_val!r}"
                )
        verified_key = f"{dim}_verified"
        if _is_unverified_token(row_val) and knowable.get(verified_key) is True:
            return (
                f"knowable_at_t dishonest: {dim}={row_val!r} claims {verified_key}=true"
            )
    for key, value in knowable.items():
        if not key.endswith("_verified"):
            continue
        if value is not True:
            continue
        dim = key[: -len("_verified")]
        related = knowable.get(dim)
        if _is_unverified_token(related):
            return (
                f"knowable_at_t dishonest: {dim}={related!r} claims {key}=true"
            )
    return None


GATE_CHECKERS = {
    "dual_clocks": check_dual_clocks,
    "stage_map": check_stage_map,
    "regime_id": check_regime_id,
    "knowable_at_t": check_knowable_at_t,
}


def _known_fields_for_row(row: dict[str, Any]) -> frozenset[str]:
    stream = row.get("stream")
    if stream == "subscribeMigration":
        return KNOWN_MIGRATION_FIELDS
    return KNOWN_CREATE_FIELDS


def scan_file(path: Path) -> dict[str, Any]:
    """Scan one JSONL file. Returns per-file counts, fails, and soft inventory."""
    result: dict[str, Any] = {
        "file": str(path),
        "rows": 0,
        "skipped_non_hot": 0,
        "parse_errors": 0,
        "hard_fail_rows": 0,
        "gate_fail_counts": {gate: 0 for gate in HARD_GATES},
        "examples": {gate: [] for gate in HARD_GATES},
        "parse_error_examples": [],
        "soft": {
            "non_pump_pool_still_bonding": 0,
            "non_pump_pool_examples": [],
            "ws_fields_unknown_listed_present": 0,
            "ws_fields_unknown_listed_absent": 0,
            "ws_fields_unknown_known_inventory": 0,
            "ws_fields_unknown_keys": Counter(),
            "pool_values": Counter(),
            "is_mayhem_mode_present": 0,
            "t_event_null": 0,
            "t_event_present": 0,
        },
    }
    try:
        fh = path.open("r", encoding="utf-8")
    except OSError as exc:
        result["parse_errors"] = 1
        result["parse_error_examples"].append(
            {"file": str(path), "line": 0, "reason": f"cannot read file: {exc}"}
        )
        return result

    with fh:
        for line_no, line in enumerate(fh, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                result["parse_errors"] += 1
                if len(result["parse_error_examples"]) < MAX_EXAMPLES:
                    result["parse_error_examples"].append(
                        {
                            "file": str(path),
                            "line": line_no,
                            "reason": f"invalid JSON: {exc.msg}",
                        }
                    )
                continue
            if not isinstance(row, dict):
                result["parse_errors"] += 1
                if len(result["parse_error_examples"]) < MAX_EXAMPLES:
                    result["parse_error_examples"].append(
                        {
                            "file": str(path),
                            "line": line_no,
                            "reason": "JSONL line is not an object",
                        }
                    )
                continue
            row_type = row.get("type")
            schema = row.get("schema_version")
            if row_type not in (None, "ingest_hot") or schema not in (
                None,
                "observe_hot_v0",
            ):
                result["skipped_non_hot"] += 1
                continue
            result["rows"] += 1

            row_failed = False
            for gate, checker in GATE_CHECKERS.items():
                reason = checker(row)
                if reason:
                    row_failed = True
                    result["gate_fail_counts"][gate] += 1
                    examples = result["examples"][gate]
                    if len(examples) < MAX_EXAMPLES:
                        examples.append(_example(path, line_no, reason, row))
            if row_failed:
                result["hard_fail_rows"] += 1

            _accumulate_soft(result["soft"], path, line_no, row)

    return result


def _accumulate_soft(
    soft: dict[str, Any],
    path: Path,
    line_no: int,
    row: dict[str, Any],
) -> None:
    payload = _payload(row)
    pool = _pool_value(row)
    if pool not in (None, ""):
        soft["pool_values"][str(pool)] += 1
    stage = row.get("stage")
    parsed, _ = parse_regime_id(row.get("regime_id"))
    market = parsed.get("market") if parsed else None
    bondingish = stage in {"bonding", "bonding_complete"} or market == "bonding_curve"
    if bondingish and _looks_non_pump_pool(pool):
        soft["non_pump_pool_still_bonding"] += 1
        if len(soft["non_pump_pool_examples"]) < MAX_EXAMPLES:
            soft["non_pump_pool_examples"].append(
                _example(
                    path,
                    line_no,
                    f"non-pump pool {pool!r} still labeled stage={stage!r} market={market!r}",
                    row,
                )
            )

    unknown = row.get("ws_fields_unknown")
    unknown_list = unknown if isinstance(unknown, list) else []
    known = _known_fields_for_row(row)
    for key in unknown_list:
        if not isinstance(key, str):
            continue
        soft["ws_fields_unknown_keys"][key] += 1
        if key in payload:
            soft["ws_fields_unknown_listed_present"] += 1
        else:
            soft["ws_fields_unknown_listed_absent"] += 1
        if key in known:
            soft["ws_fields_unknown_known_inventory"] += 1

    if any(key in payload for key in MAYHEM_KEYS):
        soft["is_mayhem_mode_present"] += 1

    if row.get("t_event") in (None, ""):
        soft["t_event_null"] += 1
    else:
        soft["t_event_present"] += 1


def _counter_to_dict(counter: Counter[str], *, limit: int = 20) -> dict[str, int]:
    return dict(counter.most_common(limit))


def merge_scans(scans: list[dict[str, Any]], paths: list[Path]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    gate_fail_counts = {gate: 0 for gate in HARD_GATES}
    examples = {gate: [] for gate in HARD_GATES}
    parse_error_examples: list[dict[str, Any]] = []
    rows = 0
    skipped = 0
    parse_errors = 0
    hard_fail_rows = 0
    soft = {
        "non_pump_pool_still_bonding": 0,
        "non_pump_pool_examples": [],
        "ws_fields_unknown_listed_present": 0,
        "ws_fields_unknown_listed_absent": 0,
        "ws_fields_unknown_known_inventory": 0,
        "ws_fields_unknown_keys": Counter(),
        "pool_values": Counter(),
        "is_mayhem_mode_present": 0,
        "t_event_null": 0,
        "t_event_present": 0,
    }

    for scan in scans:
        rows += scan["rows"]
        skipped += scan["skipped_non_hot"]
        parse_errors += scan["parse_errors"]
        hard_fail_rows += scan["hard_fail_rows"]
        for gate in HARD_GATES:
            gate_fail_counts[gate] += scan["gate_fail_counts"][gate]
            for ex in scan["examples"][gate]:
                if len(examples[gate]) < MAX_EXAMPLES:
                    examples[gate].append(ex)
        for ex in scan["parse_error_examples"]:
            if len(parse_error_examples) < MAX_EXAMPLES:
                parse_error_examples.append(ex)
        src = scan["soft"]
        soft["non_pump_pool_still_bonding"] += src["non_pump_pool_still_bonding"]
        for ex in src["non_pump_pool_examples"]:
            if len(soft["non_pump_pool_examples"]) < MAX_EXAMPLES:
                soft["non_pump_pool_examples"].append(ex)
        for key in (
            "ws_fields_unknown_listed_present",
            "ws_fields_unknown_listed_absent",
            "ws_fields_unknown_known_inventory",
            "is_mayhem_mode_present",
            "t_event_null",
            "t_event_present",
        ):
            soft[key] += src[key]
        soft["ws_fields_unknown_keys"].update(src["ws_fields_unknown_keys"])
        soft["pool_values"].update(src["pool_values"])

    hard_pass = (
        rows > 0
        and hard_fail_rows == 0
        and parse_errors == 0
        and all(count == 0 for count in gate_fail_counts.values())
    )
    stamp = "PASS" if hard_pass else "FAIL"
    reasons: list[str] = []
    if rows == 0:
        reasons.append("no ingest_hot rows")
    if parse_errors:
        reasons.append(f"jsonl_parse_errors={parse_errors}")
    for gate, count in gate_fail_counts.items():
        if count:
            reasons.append(f"{gate}={count}")

    return {
        "schema_version": SCHEMA_VERSION,
        "stamp": stamp,
        "exit_code": 0 if hard_pass else 1,
        "as_of": now,
        "note": (
            "Scout manager PASS on Vaan local capture is not reopened. "
            "This tool is a durable re-run sampler. Soft inventory never "
            "flips PASS→FAIL alone."
        ),
        "files": [str(p) for p in paths],
        "row_count": rows,
        "skipped_non_hot": skipped,
        "parse_errors": parse_errors,
        "hard_fail_rows": hard_fail_rows,
        "hard_gates": {
            gate: {
                "pass": gate_fail_counts[gate] == 0 and rows > 0,
                "fail_count": gate_fail_counts[gate],
                "examples": examples[gate],
            }
            for gate in HARD_GATES
        },
        "parse_error_examples": parse_error_examples,
        "fail_reasons": reasons,
        "soft_inventory": {
            "non_pump_pool_still_bonding": {
                "count": soft["non_pump_pool_still_bonding"],
                "examples": soft["non_pump_pool_examples"],
            },
            "ws_fields_unknown_listed_present_in_payload": {
                "count": soft["ws_fields_unknown_listed_present"],
                "listed_absent_from_payload": soft["ws_fields_unknown_listed_absent"],
                "listed_but_known_inventory": soft["ws_fields_unknown_known_inventory"],
                "keys": _counter_to_dict(soft["ws_fields_unknown_keys"]),
            },
            "pool": {
                "distinct": len(soft["pool_values"]),
                "values": _counter_to_dict(soft["pool_values"]),
            },
            "is_mayhem_mode": {
                "rows_with_payload_flag": soft["is_mayhem_mode_present"],
            },
            "t_event": {
                "null_or_empty": soft["t_event_null"],
                "present": soft["t_event_present"],
            },
        },
        "files_detail": [
            {
                "file": scan["file"],
                "rows": scan["rows"],
                "hard_fail_rows": scan["hard_fail_rows"],
                "parse_errors": scan["parse_errors"],
                "skipped_non_hot": scan["skipped_non_hot"],
            }
            for scan in scans
        ],
    }


def format_summary(result: dict[str, Any], artifact_path: str | None) -> str:
    lines = [
        f"STAMP: {result['stamp']}",
        f"files: {len(result['files'])}",
        f"rows: {result['row_count']}",
        f"hard_fail_rows: {result['hard_fail_rows']}",
        f"parse_errors: {result['parse_errors']}",
    ]
    if result["skipped_non_hot"]:
        lines.append(f"skipped_non_hot: {result['skipped_non_hot']}")
    for gate in HARD_GATES:
        info = result["hard_gates"][gate]
        status = "PASS" if info["pass"] else "FAIL"
        extra = f" fail_count={info['fail_count']}" if info["fail_count"] else ""
        lines.append(f"{gate}: {status}{extra}")
        for ex in info["examples"][:MAX_EXAMPLES]:
            lines.append(f"  - L{ex['line']} {ex['reason']}")
    for ex in result.get("parse_error_examples") or []:
        lines.append(f"  - parse L{ex['line']} {ex['reason']}")
    if result["fail_reasons"] and result["stamp"] != "PASS":
        lines.append("fail_reasons: " + ", ".join(result["fail_reasons"]))

    soft = result["soft_inventory"]
    lines.append("")
    lines.append("soft_inventory (never flips PASS→FAIL):")
    lines.append(
        f"  non_pump_pool_still_bonding: {soft['non_pump_pool_still_bonding']['count']}"
    )
    unk = soft["ws_fields_unknown_listed_present_in_payload"]
    lines.append(
        f"  ws_fields_unknown_listed_present: {unk['count']}"
    )
    if unk["keys"]:
        top = ", ".join(f"{k}={v}" for k, v in list(unk["keys"].items())[:8])
        lines.append(f"  ws_fields_unknown_keys: {top}")
    lines.append(
        f"  is_mayhem_mode_present: {soft['is_mayhem_mode']['rows_with_payload_flag']}"
    )
    pool_vals = soft["pool"]["values"]
    if pool_vals:
        top = ", ".join(f"{k}={v}" for k, v in list(pool_vals.items())[:8])
        lines.append(f"  pool_values: {top}")
    tev = soft["t_event"]
    lines.append(
        f"  t_event_null: {tev['null_or_empty']}  t_event_present: {tev['present']}"
    )
    if artifact_path:
        lines.append("")
        lines.append(f"artifact: {artifact_path}")
    return "\n".join(lines) + "\n"


def default_artifact_path(paths: list[Path]) -> Path:
    parents = [p.resolve().parent for p in paths]
    shared = parents[0]
    if all(parent == shared for parent in parents):
        candidate = shared / "observe-sealed-row-stamp.json"
        if _writable_dir(shared):
            return candidate
    observe_dir = Path("data/observe")
    if _writable_dir(observe_dir, mkdir=True):
        return observe_dir / "observe-sealed-row-stamp.json"
    return shared / "observe-sealed-row-stamp.json"


def _writable_dir(path: Path, *, mkdir: bool = False) -> bool:
    try:
        if mkdir:
            path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            return False
        with tempfile.NamedTemporaryFile(dir=path, delete=True):
            return True
    except OSError:
        return False


def write_artifact(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=False)
    path.write_text(text + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Day-wide Scout sealed-row stamp for observe JSONL. "
            "Exit 0 = hard gates PASS; non-zero = FAIL. "
            "Soft inventory never fails the stamp."
        )
    )
    parser.add_argument(
        "jsonl",
        nargs="+",
        type=Path,
        help="Observe JSONL path(s), e.g. data/observe/observe-YYYY-MM-DD.jsonl",
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="JSON summary path (default: next to inputs, else data/observe/)",
    )
    parser.add_argument(
        "--no-artifact",
        action="store_true",
        help="Print summary only; do not write JSON artifact",
    )
    return parser


def stamp_paths(paths: list[Path]) -> dict[str, Any]:
    scans = [scan_file(path) for path in paths]
    return merge_scans(scans, paths)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths: list[Path] = args.jsonl
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        print("ERROR: JSONL not found: " + ", ".join(missing), file=sys.stderr)
        return 2

    result = stamp_paths(paths)
    artifact_path: str | None = None
    if not args.no_artifact:
        dest = args.artifact if args.artifact is not None else default_artifact_path(paths)
        try:
            write_artifact(result, dest)
            artifact_path = str(dest)
        except OSError as exc:
            print(f"WARN: could not write artifact {dest}: {exc}", file=sys.stderr)

    sys.stdout.write(format_summary(result, artifact_path))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
