#!/usr/bin/env python3
"""EXP-001 local regime/stage mislabel audit.

Offline CLI: sealed ingest JSONL only. No RPC, no enrich rewrite, no observe-wiring
changes. Reconstruction uses DEC-003/004 + REGIME-AT-INGEST-MATRIX via shared
``observe.regime`` encoding (``build_regime_id`` / ``seal_ingest_record``).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence, TextIO

from observe.regime import (
    STAGES,
    STREAM_MIGRATION,
    STREAM_NEW_TOKEN,
    build_regime_id,
    canonical_tx_type,
    seal_ingest_record,
)

STAGE_VOCAB = STAGES | {"UNK"}
CREATE_TX = "create"
MIGRATION_TX = "migration"

DEFAULT_N = 100
DEFAULT_SEED = 1
DEFAULT_OUTPUT_DIR = Path("data/observe")
DEFAULT_PREFIX = "_exp001"

# Council lock — do not invent new thresholds (DEC-003 / EXP-001).
DEC_REVIEW_THRESHOLD = 0.01
WIRING_BLOCK_THRESHOLD = 0.05

Verdict = Literal["agree", "disagree", "inconclusive"]
GateResult = Literal["PASS", "FAIL", "INCOMPLETE"]


def canonical_regime_keys() -> list[str]:
    """DEC-004 key order from shared encoder (no duplicated list)."""
    sample = build_regime_id(stream=STREAM_NEW_TOKEN, stage="bonding")
    return [part.split("=", 1)[0] for part in sample.split("|")]


CANONICAL_REGIME_KEYS = canonical_regime_keys()


def parse_regime_id(value: Any) -> dict[str, str] | None:
    """Parse pipe key=value. None if not a well-formed composite."""
    if not isinstance(value, str) or not value.strip():
        return None
    tokens: dict[str, str] = {}
    parts = value.split("|")
    for part in parts:
        if "=" not in part:
            return None
        key, val = part.split("=", 1)
        if not key or " " in key or key in tokens:
            return None
        tokens[key] = val
    return tokens


def _json_load_object(line: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _payload(row: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = row.get("ws_payload")
    return payload if isinstance(payload, dict) else None


def _row_tx_raw(row: Mapping[str, Any]) -> Any:
    tx = row.get("txType")
    if tx not in (None, "", "UNK"):
        return tx
    payload = _payload(row)
    if payload is not None:
        return payload.get("txType")
    return tx


def is_ingest_hot_create(row: Mapping[str, Any]) -> bool:
    """Population: type=ingest_hot and txType=create (canonical)."""
    if row.get("type") != "ingest_hot":
        return False
    return canonical_tx_type(_row_tx_raw(row)) == CREATE_TX


def t_ws_missing(row: Mapping[str, Any]) -> bool:
    """Void only when t_ws is missing, null, or empty. Null t_event is legal."""
    t_ws = row.get("t_ws")
    if t_ws is None:
        return True
    if isinstance(t_ws, str) and t_ws.strip() == "":
        return True
    return False


def stratum_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (str(row.get("stage") or ""), str(row.get("regime_id") or ""))


def _latency_seconds(t_ws: Any, t_event: Any) -> float | None:
    """Descriptive Δ only; never a verdict. Null t_event → None."""
    if not isinstance(t_ws, str) or not isinstance(t_event, str) or not t_event:
        return None
    try:
        ws = datetime.fromisoformat(t_ws)
        ev = datetime.fromisoformat(t_event)
    except ValueError:
        return None
    return (ws - ev).total_seconds()


def _stream_tx_conflict(stream: Any, payload: Mapping[str, Any], row: Mapping[str, Any]) -> str | None:
    """Map does not resolve these; EXP-001 §7.3 inconclusive."""
    payload_tx = canonical_tx_type(payload.get("txType"))
    row_tx = canonical_tx_type(row.get("txType"))
    if row_tx and payload_tx and row_tx != payload_tx:
        return "txType_row_vs_payload_conflict"
    tx = payload_tx or row_tx
    if stream == STREAM_MIGRATION and tx == CREATE_TX:
        return "stream_txType_conflict"
    if stream == STREAM_NEW_TOKEN and tx == MIGRATION_TX:
        return "stream_txType_conflict"
    return None


@dataclass
class Reconstruction:
    ok: bool
    stage: str | None = None
    regime_id: str | None = None
    tokens: dict[str, str] | None = None
    reason: str | None = None


def reconstruct_expected(row: Mapping[str, Any]) -> Reconstruction:
    """Independent expected stage/regime_id from sealed stream + ws_payload."""
    payload = _payload(row)
    if payload is None:
        return Reconstruction(ok=False, reason="missing_or_null_ws_payload")

    stream = row.get("stream")
    conflict = _stream_tx_conflict(stream, payload, row)
    if conflict:
        return Reconstruction(ok=False, reason=conflict)

    derive_payload = dict(payload)
    if "txType" not in derive_payload and row.get("txType") not in (None, ""):
        derive_payload["txType"] = row.get("txType")

    stream_for_seal = stream if isinstance(stream, str) and stream else "UNK"
    t_ws = row.get("t_ws")
    if not isinstance(t_ws, str) or not t_ws.strip():
        # Callers void missing t_ws before scoring; keep reconstruct total.
        t_ws = "1970-01-01T00:00:00+00:00"

    expected_row = seal_ingest_record(
        t_ws=t_ws,
        stream=stream_for_seal,
        payload=derive_payload,
    )
    expected_stage = str(expected_row["stage"])
    expected_regime_id = build_regime_id(stream=stream_for_seal, stage=expected_stage)
    tokens = parse_regime_id(expected_regime_id)
    return Reconstruction(
        ok=True,
        stage=expected_stage,
        regime_id=expected_regime_id,
        tokens=tokens,
    )


@dataclass
class Judgment:
    source_path: str
    line_no: int
    signature: Any
    mint: Any
    t_ws: Any
    t_event: Any
    dt_ws_minus_t_event_s: float | None
    stream: Any
    txType: Any
    stamped_stage: Any
    stamped_regime_id: Any
    expected_stage: str | None
    expected_regime_id: str | None
    verdict: Verdict
    failed_checks: list[str] = field(default_factory=list)
    notes: str = ""
    stratum_stage: str = ""
    stratum_regime_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "line_no": self.line_no,
            "signature": self.signature,
            "mint": self.mint,
            "t_ws": self.t_ws,
            "t_event": self.t_event,
            "dt_ws_minus_t_event_s": self.dt_ws_minus_t_event_s,
            "stream": self.stream,
            "txType": self.txType,
            "stamped_stage": self.stamped_stage,
            "stamped_regime_id": self.stamped_regime_id,
            "expected_stage": self.expected_stage,
            "expected_regime_id": self.expected_regime_id,
            "verdict": self.verdict,
            "failed_checks": list(self.failed_checks),
            "notes": self.notes,
            "stratum_stage": self.stratum_stage,
            "stratum_regime_id": self.stratum_regime_id,
        }


def judge_row(row: Mapping[str, Any], *, source_path: str, line_no: int) -> Judgment:
    stage_s, rid_s = stratum_key(row)
    t_event = row.get("t_event", None)
    base = dict(
        source_path=source_path,
        line_no=line_no,
        signature=row.get("signature"),
        mint=row.get("mint"),
        t_ws=row.get("t_ws"),
        t_event=t_event,
        dt_ws_minus_t_event_s=_latency_seconds(row.get("t_ws"), t_event),
        stream=row.get("stream"),
        txType=row.get("txType"),
        stamped_stage=row.get("stage"),
        stamped_regime_id=row.get("regime_id"),
        stratum_stage=stage_s,
        stratum_regime_id=rid_s,
    )
    recon = reconstruct_expected(row)
    if not recon.ok:
        return Judgment(
            **base,
            expected_stage=None,
            expected_regime_id=None,
            verdict="inconclusive",
            notes=recon.reason or "inconclusive",
        )

    failed: list[str] = []
    stamped_stage = row.get("stage")
    stamped_rid = row.get("regime_id")

    if stamped_stage not in STAGE_VOCAB:
        failed.append("stage_not_in_vocab")
    if stamped_stage != recon.stage:
        failed.append("stage_mismatch")

    parsed = parse_regime_id(stamped_rid)
    if parsed is None:
        failed.append("regime_id_unparseable")
    else:
        if list(parsed.keys()) != CANONICAL_REGIME_KEYS:
            failed.append("regime_id_key_order")
        if parsed.get("stage") != stamped_stage:
            failed.append("regime_id_stage_ne_top_level")
        expected_tokens = recon.tokens or {}
        for key in CANONICAL_REGIME_KEYS:
            if parsed.get(key) != expected_tokens.get(key):
                failed.append(f"regime_id_{key}_mismatch")

    # Illegal WS-unknowable upgrades (lookahead / backfill) are token mismatches.
    if recon.stage in ("bonding", "UNK") and stamped_stage in (
        "bonding_complete",
        "pumpswap",
        "legacy_raydium",
        "migrating",
    ):
        if "stage_mismatch" not in failed:
            failed.append("rpc_only_stage_on_create")

    if failed:
        return Judgment(
            **base,
            expected_stage=recon.stage,
            expected_regime_id=recon.regime_id,
            verdict="disagree",
            failed_checks=failed,
            notes=";".join(failed),
        )
    return Judgment(
        **base,
        expected_stage=recon.stage,
        expected_regime_id=recon.regime_id,
        verdict="agree",
        notes="",
    )


@dataclass
class LoadedRow:
    row: dict[str, Any]
    source_path: str
    line_no: int


def load_jsonl_files(paths: Sequence[Path]) -> tuple[list[LoadedRow], int]:
    """Return parsed objects and count of malformed/non-object lines."""
    loaded: list[LoadedRow] = []
    malformed = 0
    for path in paths:
        with path.open("r", encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                obj = _json_load_object(line)
                if obj is None:
                    malformed += 1
                    continue
                loaded.append(LoadedRow(row=obj, source_path=str(path), line_no=line_no))
    return loaded, malformed


def draw_sample(
    eligible: Sequence[LoadedRow],
    n: int,
    rng: random.Random,
) -> tuple[list[LoadedRow], str]:
    """Deterministic sample. Stratify by stage/regime_id when ≥2 strata."""
    if not eligible:
        return [], "empty"
    if n >= len(eligible):
        return list(eligible), "all_eligible"

    groups: dict[tuple[str, str], list[LoadedRow]] = {}
    for item in eligible:
        groups.setdefault(stratum_key(item.row), []).append(item)
    keys = sorted(groups.keys())

    if len(keys) < 2:
        picked = rng.sample(list(eligible), n)
        return picked, "simple_random_single_stratum"

    n_pop = len(eligible)
    raw_alloc = {k: n * len(groups[k]) / n_pop for k in keys}
    alloc = {k: min(len(groups[k]), int(raw_alloc[k])) for k in keys}
    remaining = n - sum(alloc.values())
    remainder_order = sorted(
        keys,
        key=lambda k: (raw_alloc[k] - int(raw_alloc[k]), k[0], k[1]),
        reverse=True,
    )
    for k in remainder_order:
        if remaining <= 0:
            break
        if alloc[k] < len(groups[k]):
            alloc[k] += 1
            remaining -= 1
    picked: list[LoadedRow] = []
    for k in keys:
        k_n = alloc[k]
        if k_n:
            picked.extend(rng.sample(groups[k], k_n))
    if remaining > 0:
        picked_ids = {id(x) for x in picked}
        leftovers = [x for x in eligible if id(x) not in picked_ids]
        picked.extend(rng.sample(leftovers, remaining))
    return picked, "proportional_stratified"


def _count_strata(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        stage, rid = stratum_key(row)
        counts[f"stage={stage}|regime_id={rid}"] += 1
    return dict(sorted(counts.items()))


def _gate(rate: float | None, threshold: float) -> GateResult:
    if rate is None:
        return "INCOMPLETE"
    return "FAIL" if rate > threshold else "PASS"


def build_summary(
    *,
    paths: Sequence[Path],
    seed: int,
    n_requested: int,
    population_n: int,
    void_n: int,
    malformed_n: int,
    eligible_n: int,
    allocation_rule: str,
    judgments: Sequence[Judgment],
    population_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    agree_n = sum(1 for j in judgments if j.verdict == "agree")
    disagree_n = sum(1 for j in judgments if j.verdict == "disagree")
    inconclusive_n = sum(1 for j in judgments if j.verdict == "inconclusive")
    denom = agree_n + disagree_n
    rate: float | None = (disagree_n / denom) if denom else None

    dec_gate = _gate(rate, DEC_REVIEW_THRESHOLD)
    wiring_gate = _gate(rate, WIRING_BLOCK_THRESHOLD)
    if dec_gate == "INCOMPLETE" or wiring_gate == "INCOMPLETE":
        overall: str = "INCOMPLETE"
    elif wiring_gate == "FAIL":
        overall = "FAIL_WIRING_BLOCK"
    elif dec_gate == "FAIL":
        overall = "FAIL_DEC_REVIEW"
    else:
        overall = "PASS"

    return {
        "exp": "EXP-001",
        "unit": "sealed_ingest_hot_create",
        "paths": [str(p) for p in paths],
        "seed": seed,
        "n_requested": n_requested,
        "n": len(judgments),
        "population_n": population_n,
        "void_n": void_n,
        "malformed_n": malformed_n,
        "eligible_n": eligible_n,
        "agree_n": agree_n,
        "disagree_n": disagree_n,
        "inconclusive_n": inconclusive_n,
        "agree_rate": (agree_n / denom) if denom else None,
        "hard_disagree_rate": rate,
        "inconclusive_excluded_from_denominator": True,
        "void_excluded_from_sample_and_denominator": True,
        "null_t_event_legal": True,
        "allocation_rule": allocation_rule,
        "population_stratum_counts": _count_strata(population_rows),
        "sample_stratum_counts": _count_strata(
            [
                {
                    "stage": j.stamped_stage,
                    "regime_id": j.stamped_regime_id,
                }
                for j in judgments
            ]
        ),
        "gates": {
            "dec_enum_review": {
                "threshold": DEC_REVIEW_THRESHOLD,
                "comparator": "hard_disagree_rate > threshold",
                "result": dec_gate,
            },
            "wiring_block": {
                "threshold": WIRING_BLOCK_THRESHOLD,
                "comparator": "hard_disagree_rate > threshold",
                "result": wiring_gate,
            },
        },
        "overall": overall,
        "sample_signatures": [j.signature for j in judgments],
        "sample_line_ids": [f"{j.source_path}:{j.line_no}" for j in judgments],
    }


def write_outputs(
    *,
    output_dir: Path,
    prefix: str,
    judgments: Sequence[Judgment],
    summary: Mapping[str, Any],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"{prefix}_judgments.jsonl"
    csv_path = output_dir / f"{prefix}_judgments.csv"
    summary_path = output_dir / f"{prefix}_summary.json"

    with jsonl_path.open("w", encoding="utf-8") as fh:
        for j in judgments:
            fh.write(json.dumps(j.as_dict(), ensure_ascii=False) + "\n")

    fieldnames = list(Judgment.__dataclass_fields__.keys())
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for j in judgments:
            row = j.as_dict()
            row["failed_checks"] = ";".join(row["failed_checks"])
            writer.writerow(row)

    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    return {"jsonl": jsonl_path, "csv": csv_path, "summary": summary_path}


def run_audit(
    paths: Sequence[Path],
    *,
    n: int = DEFAULT_N,
    seed: int = DEFAULT_SEED,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = DEFAULT_PREFIX,
) -> dict[str, Any]:
    loaded, malformed_n = load_jsonl_files(paths)
    creates = [item for item in loaded if is_ingest_hot_create(item.row)]
    voided = [item for item in creates if t_ws_missing(item.row)]
    eligible = [item for item in creates if not t_ws_missing(item.row)]

    rng = random.Random(seed)
    sample, allocation_rule = draw_sample(eligible, n, rng)
    judgments = [
        judge_row(item.row, source_path=item.source_path, line_no=item.line_no)
        for item in sample
    ]
    summary = build_summary(
        paths=paths,
        seed=seed,
        n_requested=n,
        population_n=len(creates),
        void_n=len(voided),
        malformed_n=malformed_n,
        eligible_n=len(eligible),
        allocation_rule=allocation_rule,
        judgments=judgments,
        population_rows=[item.row for item in eligible],
    )
    paths_out = write_outputs(
        output_dir=output_dir,
        prefix=prefix,
        judgments=judgments,
        summary=summary,
    )
    summary_with_paths = dict(summary)
    summary_with_paths["output_paths"] = {k: str(v) for k, v in paths_out.items()}
    with paths_out["summary"].open("w", encoding="utf-8") as fh:
        json.dump(summary_with_paths, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return summary_with_paths


def _print_summary(summary: Mapping[str, Any], out: TextIO = sys.stdout) -> None:
    rate = summary.get("hard_disagree_rate")
    rate_s = "n/a" if rate is None else f"{rate:.6f}"
    gates = summary["gates"]
    print(
        "EXP-001 mislabel audit\n"
        f"  n={summary['n']} void_n={summary['void_n']} "
        f"agree={summary['agree_n']} disagree={summary['disagree_n']} "
        f"inconclusive={summary['inconclusive_n']}\n"
        f"  hard_disagree_rate={rate_s}  "
        f"dec_enum_review(>1%)={gates['dec_enum_review']['result']}  "
        f"wiring_block(>5%)={gates['wiring_block']['result']}\n"
        f"  overall={summary['overall']}  allocation={summary['allocation_rule']}\n"
        f"  outputs: {summary.get('output_paths')}",
        file=out,
    )


def _exit_code(overall: str) -> int:
    if overall == "PASS":
        return 0
    if overall == "INCOMPLETE":
        return 3
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.exp001_mislabel",
        description=(
            "EXP-001 local mislabel audit: re-derive regime_id/stage on a "
            "deterministic sample of sealed create packets (no RPC)."
        ),
    )
    p.add_argument(
        "jsonl",
        nargs="+",
        type=Path,
        help="Sealed observe JSONL path(s), e.g. data/observe/observe-2026-09-20.jsonl",
    )
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="RNG seed (default: 1)")
    p.add_argument("--n", type=int, default=DEFAULT_N, help="Sample size (default: 100)")
    p.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory (default: data/observe)",
    )
    p.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help="Output filename prefix (default: _exp001)",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    missing = [p for p in args.jsonl if not p.is_file()]
    if missing:
        print(f"error: JSONL not found: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 1
    if args.n < 1:
        print("error: --n must be >= 1", file=sys.stderr)
        return 1
    summary = run_audit(
        args.jsonl,
        n=args.n,
        seed=args.seed,
        output_dir=args.output_dir,
        prefix=args.prefix,
    )
    _print_summary(summary)
    return _exit_code(str(summary["overall"]))


if __name__ == "__main__":
    raise SystemExit(main())
