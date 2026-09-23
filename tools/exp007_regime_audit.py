#!/usr/bin/env python3
"""EXP-007 sealed platform-regime coverage audit (paper-only, stratify report).

Offline CLI: sealed ingest_hot **create** rows only. No RPC, no enrich rewrite,
no observe-wiring changes. Reports label coverage / knowable-at-T presence for
Scout-owned platform dimensions (bonding lineage, fees, graduation, quote).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from tools.exp001_mislabel import LoadedRow, load_jsonl_files, parse_regime_id, t_ws_missing
from tools.exp002_paper_runner import is_bonding_create
from tools.exp007_rpc_enrich import apply_enrich_overlay, load_enrich_by_parent
EXP_ID = "EXP-007-platform-regime-taxonomy-v0"
EXP007B_ID = "EXP-007b-platform-regime-rpc-enrich-v0"


def regime_gate_key(regime_id: str | None) -> str:
    """S1 scope key — full regime_id string (matches EXP-004 policy)."""
    if isinstance(regime_id, str) and regime_id.strip():
        return regime_id.strip()
    return "regime_gate=unknown|reason=missing_regime_id"

DEFAULT_OUTPUT_DIR = Path("data/observe")
DEFAULT_PREFIX = "_exp007"

# EXP-007 §1 platform slice keys in regime_id (DEC-004 order subset).
PLATFORM_REGIME_KEYS = ("venue", "instr", "fee", "stage", "market", "quote")
KNOWABLE_AT_T_PLATFORM_KEYS = ("venue", "instr", "fee", "quote", "quote_verified")

# Explicit unverified / pending tokens (DEC-003 / OBSERVE schema — not blank).
ALLOWED_UNVERIFIED = frozenset(
    {
        "unverified",
        "pending_rpc",
        "unknown_until_rpc",
        "wsol_assumed",
        "UNK",
    }
)

GateResult = Literal["PASS", "FAIL", "INCOMPLETE", "N/A"]


def _day_key_from_path(path: Path) -> str:
    stem = path.stem
    if stem.startswith("observe-"):
        return stem.replace("observe-", "")
    return stem


def _kat(row: Mapping[str, Any]) -> dict[str, Any] | None:
    kat = row.get("knowable_at_t")
    return kat if isinstance(kat, dict) else None


def _token_blank(val: str | None) -> bool:
    if val is None:
        return True
    s = val.strip()
    return s == ""


def _is_explicit_unverified(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip() in ALLOWED_UNVERIFIED


def _regime_tokens(row: Mapping[str, Any]) -> dict[str, str] | None:
    return parse_regime_id(row.get("regime_id"))


@dataclass
class RowAudit:
    source_path: str
    line_no: int
    signature: Any
    mint: Any
    stage: Any
    regime_id: Any
    regime_gate_key: str
    kat_present: bool
    regime_parse_ok: bool
    blank_platform_keys: list[str] = field(default_factory=list)
    kat_missing_keys: list[str] = field(default_factory=list)
    id_kat_mismatch: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "line_no": self.line_no,
            "signature": self.signature,
            "mint": self.mint,
            "stage": self.stage,
            "regime_id": self.regime_id,
            "regime_gate_key": self.regime_gate_key,
            "kat_present": self.kat_present,
            "regime_parse_ok": self.regime_parse_ok,
            "blank_platform_keys": list(self.blank_platform_keys),
            "kat_missing_keys": list(self.kat_missing_keys),
            "id_kat_mismatch": list(self.id_kat_mismatch),
        }


def audit_row(row: Mapping[str, Any], *, source_path: str, line_no: int) -> RowAudit:
    tokens = _regime_tokens(row)
    kat = _kat(row)
    rid = row.get("regime_id")
    gate = regime_gate_key(rid if isinstance(rid, str) else None)

    blank: list[str] = []
    if tokens:
        for key in PLATFORM_REGIME_KEYS:
            if key == "stage":
                continue  # top-level row field + regime_id duplicate
            val = tokens.get(key)
            if _token_blank(val):
                blank.append(f"regime_id.{key}")
            elif not _is_explicit_unverified(val) and key in ("instr", "fee", "quote"):
                pass  # resolved or enum value — coverage counted separately
    else:
        blank.append("regime_id.parse")

    kat_missing: list[str] = []
    mismatch: list[str] = []
    if kat is None:
        kat_missing.append("knowable_at_t")
    else:
        for key in KNOWABLE_AT_T_PLATFORM_KEYS:
            if key not in kat:
                kat_missing.append(f"knowable_at_t.{key}")
        if tokens:
            for key in ("quote", "instr", "fee", "venue"):
                if key in tokens and key in kat:
                    rv = str(tokens[key])
                    kv = kat[key]
                    if kv is not None and str(kv) != rv:
                        mismatch.append(key)

    stamped_stage = row.get("stage")
    if tokens and stamped_stage is not None:
        ts = str(stamped_stage)
        rs = tokens.get("stage")
        if rs and rs != ts:
            mismatch.append("stage_row_vs_regime_id")

    return RowAudit(
        source_path=source_path,
        line_no=line_no,
        signature=row.get("signature"),
        mint=row.get("mint"),
        stage=row.get("stage"),
        regime_id=rid,
        regime_gate_key=gate,
        kat_present=kat is not None,
        regime_parse_ok=tokens is not None,
        blank_platform_keys=blank,
        kat_missing_keys=kat_missing,
        id_kat_mismatch=mismatch,
    )


def _value_counts(rows: Sequence[Mapping[str, Any]], key: str, *, from_regime: str) -> Counter[str]:
    c: Counter[str] = Counter()
    for row in rows:
        if from_regime == "regime_id":
            tokens = _regime_tokens(row)
            if not tokens:
                c["__parse_fail__"] += 1
                continue
            c[str(tokens.get(key, "__missing__"))] += 1
        elif from_regime == "knowable_at_t":
            kat = _kat(row)
            if kat is None:
                c["__kat_missing__"] += 1
                continue
            c[str(kat.get(key, "__missing__"))] += 1
        elif from_regime == "row":
            c[str(row.get(key, "__missing__"))] += 1
    return c


def _coverage_block(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"population_n": 0}

    def rate(num: int) -> float | None:
        return (num / n) if n else None

    tokens_ok = sum(1 for r in rows if _regime_tokens(r) is not None)
    kat_ok = sum(1 for r in rows if _kat(r) is not None)

    instr_pending = sum(
        1
        for r in rows
        if (_regime_tokens(r) or {}).get("instr") in ("pending_rpc", "unknown_until_rpc")
    )
    fee_unverified = sum(
        1 for r in rows if (_regime_tokens(r) or {}).get("fee") in ("unverified", "UNK")
    )
    quote_assumed = sum(
        1
        for r in rows if (_regime_tokens(r) or {}).get("quote") in ("wsol_assumed", "UNK")
    )
    quote_verified = sum(
        1 for r in rows if (_kat(r) or {}).get("quote_verified") is True
    )

    gates_unique = {regime_gate_key(r.get("regime_id") if isinstance(r.get("regime_id"), str) else None) for r in rows}

    return {
        "population_n": n,
        "regime_id_parse_rate": rate(tokens_ok),
        "knowable_at_t_present_rate": rate(kat_ok),
        "instr_pending_or_unknown_rate": rate(instr_pending),
        "fee_unverified_rate": rate(fee_unverified),
        "quote_assumed_or_unk_rate": rate(quote_assumed),
        "quote_verified_true_rate": rate(quote_verified),
        "regime_gate_keys_n": len(gates_unique),
        "value_counts": {
            "regime_id.instr": dict(_value_counts(rows, "instr", from_regime="regime_id")),
            "regime_id.fee": dict(_value_counts(rows, "fee", from_regime="regime_id")),
            "regime_id.quote": dict(_value_counts(rows, "quote", from_regime="regime_id")),
            "regime_id.market": dict(_value_counts(rows, "market", from_regime="regime_id")),
            "regime_id.venue": dict(_value_counts(rows, "venue", from_regime="regime_id")),
            "row.stage": dict(_value_counts(rows, "stage", from_regime="row")),
            "knowable_at_t.instr": dict(_value_counts(rows, "instr", from_regime="knowable_at_t")),
            "knowable_at_t.fee": dict(_value_counts(rows, "fee", from_regime="knowable_at_t")),
            "knowable_at_t.quote": dict(_value_counts(rows, "quote", from_regime="knowable_at_t")),
        },
    }


def _gate_k_blank(audits: Sequence[RowAudit]) -> dict[str, Any]:
    bad = [a for a in audits if a.blank_platform_keys]
    n = len(audits)
    rate = (len(bad) / n) if n else None
    result: GateResult = "PASS" if n and not bad else ("INCOMPLETE" if not n else "FAIL")
    return {
        "id": "K-blank",
        "result": result,
        "violations_n": len(bad),
        "violation_rate": rate,
        "note": "Empty platform key in regime_id without explicit unverified token.",
    }


def _gate_k_kat(audits: Sequence[RowAudit]) -> dict[str, Any]:
    bad = [a for a in audits if not a.kat_present or a.kat_missing_keys]
    n = len(audits)
    result: GateResult = "PASS" if n and not bad else ("INCOMPLETE" if not n else "FAIL")
    return {
        "id": "K-knowable-at-t",
        "result": result,
        "violations_n": len(bad),
        "violation_rate": (len(bad) / n) if n else None,
        "note": "knowable_at_t object + platform keys present on every eligible create.",
    }


def _gate_k_id_kat(audits: Sequence[RowAudit]) -> dict[str, Any]:
    bad = [a for a in audits if a.id_kat_mismatch]
    n = len(audits)
    result: GateResult = "PASS" if n and not bad else ("INCOMPLETE" if not n else "FAIL")
    return {
        "id": "K-id-kat-consistency",
        "result": result,
        "violations_n": len(bad),
        "violation_rate": (len(bad) / n) if n else None,
        "note": "regime_id tokens align with knowable_at_t for quote/instr/fee/venue/stage.",
    }


def _gate_k_platform_resolved(
    coverage: Mapping[str, Any],
    *,
    scope: str = "sealed_book",
) -> dict[str, Any]:
    """Honest wiring readiness — WS-only defaults are stamped but not RPC-resolved."""
    n = int(coverage.get("population_n") or 0)
    if n == 0:
        return {
            "id": "K-platform-rpc-resolved",
            "result": "INCOMPLETE",
            "note": "empty population",
            "scope": scope,
        }
    pending = float(coverage.get("instr_pending_or_unknown_rate") or 0.0)
    fee_uv = float(coverage.get("fee_unverified_rate") or 0.0)
    quote_asm = float(coverage.get("quote_assumed_or_unk_rate") or 0.0)
    quote_ver = float(coverage.get("quote_verified_true_rate") or 0.0)
    # All rows still on WS defaults → INCOMPLETE (expected phase-0 observe).
    if pending >= 0.99 and fee_uv >= 0.99 and quote_asm >= 0.99:
        result: GateResult = "INCOMPLETE"
        note = "≥99% instr pending + fee unverified + quote assumed — taxonomy stamped, RPC enrich not landed."
    elif pending > 0.05 or fee_uv > 0.05 or quote_asm > 0.05:
        if scope == "enriched_sample" and quote_ver >= 0.90 and pending <= 0.05:
            result = "PASS"
            note = "EXP-007b enriched sample: instr resolved + quote verified on ≥90% of RPC overlay rows."
        else:
            result = "INCOMPLETE"
            note = "Mixed resolved/unverified platform slice — stratify mandatory on measures."
    else:
        result = "PASS"
        note = "Majority RPC-resolved platform keys on book."
    return {
        "id": "K-platform-rpc-resolved",
        "result": result,
        "scope": scope,
        "instr_pending_or_unknown_rate": pending,
        "fee_unverified_rate": fee_uv,
        "quote_assumed_or_unk_rate": quote_asm,
        "quote_verified_true_rate": quote_ver,
        "note": note,
    }


def _gate_k_leak_enriched(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    bad = [r for r in rows if r.get("_exp007b_leak_reject")]
    n = len(rows)
    result: GateResult = "PASS" if n and not bad else ("INCOMPLETE" if not n else "FAIL")
    return {
        "id": "K-leak-enriched",
        "result": result,
        "violations_n": len(bad),
        "violation_rate": (len(bad) / n) if n else None,
        "note": "Enriched overlay rows must not use RPC timestamps after decision T.",
    }


def _audit_bundle(
    eligible: Sequence[Any],
    *,
    scope: str,
) -> dict[str, Any]:
    rows = [item.row for item in eligible]
    audits = [
        audit_row(item.row, source_path=item.source_path, line_no=item.line_no) for item in eligible
    ]
    coverage = _coverage_block(rows)
    gates = [
        _gate_k_blank(audits),
        _gate_k_kat(audits),
        _gate_k_id_kat(audits),
        _gate_k_platform_resolved(coverage, scope=scope),
    ]
    if scope == "enriched_sample":
        gates.append(_gate_k_leak_enriched(rows))
    overall = _overall_from_gates(gates)
    return {
        "scope": scope,
        "eligible_n": len(eligible),
        "coverage": coverage,
        "gates": {g["id"]: g for g in gates},
        "overall": overall,
        "row_audits": audits,
    }


def _overall_from_gates(gates: Sequence[Mapping[str, Any]]) -> str:
    results = [str(g.get("result")) for g in gates]
    if any(r == "FAIL" for r in results):
        return "FAIL"
    if any(r == "INCOMPLETE" for r in results):
        return "INCOMPLETE"
    return "PASS"


def run_single_day(
    observe_path: Path,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = DEFAULT_PREFIX,
    enrich_paths: Sequence[Path] | None = None,
) -> dict[str, Any]:
    loaded, malformed_n = load_jsonl_files([observe_path])
    creates = [item for item in loaded if is_bonding_create(item.row)]
    voided = [item for item in creates if t_ws_missing(item.row)]
    eligible = [item for item in creates if not t_ws_missing(item.row)]

    sealed_bundle = _audit_bundle(eligible, scope="sealed_book")

    enrich_map: dict[str, dict[str, Any]] = {}
    enriched_bundle: dict[str, Any] | None = None
    if enrich_paths:
        enrich_map = load_enrich_by_parent(enrich_paths)
        enriched_items: list[Any] = []
        for item in eligible:
            sig = item.row.get("signature")
            if not isinstance(sig, str) or sig not in enrich_map:
                continue
            overlay_row = apply_enrich_overlay(item.row, enrich_map[sig])
            enriched_items.append(
                LoadedRow(
                    row=overlay_row,
                    source_path=item.source_path,
                    line_no=item.line_no,
                )
            )
        if enriched_items:
            enriched_bundle = _audit_bundle(enriched_items, scope="enriched_sample")

    day_key = _day_key_from_path(observe_path)
    summary: dict[str, Any] = {
        "exp": EXP_ID,
        "unit": "sealed_ingest_hot_create",
        "courier_day": day_key,
        "observe_path": str(observe_path),
        "malformed_jsonl_lines": malformed_n,
        "population_create_n": len(creates),
        "void_missing_t_ws_n": len(voided),
        "eligible_n": len(eligible),
        "coverage": sealed_bundle["coverage"],
        "gates": sealed_bundle["gates"],
        "overall": sealed_bundle["overall"],
        "limitations": [
            "Stratify-only audit — no independent RPC reconstruction (not EXP-001 mislabel sample).",
            "regime_gate_key policy matches EXP-004 (full regime_id string).",
            "trade_iface not in observe_hot_v0 regime_id — bonding lineage = venue+instr only.",
        ],
    }
    if enrich_paths:
        enriched_public = None
        if enriched_bundle:
            enriched_public = {
                k: v for k, v in enriched_bundle.items() if k != "row_audits"
            }
        summary["exp007b"] = {
            "exp": EXP007B_ID,
            "enrich_paths": [str(p) for p in enrich_paths],
            "enrich_rows_loaded_n": len(enrich_map),
            "enriched_sample": enriched_public,
            "overall": enriched_bundle["overall"] if enriched_bundle else "INCOMPLETE",
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    day_prefix = f"{prefix}-{day_key}"
    jsonl_path = output_dir / f"{day_prefix}_row_audit.jsonl"
    summary_path = output_dir / f"{day_prefix}_summary.json"
    report_path = output_dir / f"{day_prefix}_report.md"

    audits = sealed_bundle["row_audits"]
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for a in audits:
            fh.write(json.dumps(a.as_dict(), ensure_ascii=False) + "\n")

    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    report_path.write_text(_render_report(summary), encoding="utf-8")
    artifacts: dict[str, str] = {
        "row_audit_jsonl": str(jsonl_path),
        "summary_json": str(summary_path),
        "report_md": str(report_path),
    }
    if enriched_bundle:
        enrich_prefix = f"{prefix}-{day_key}_enriched"
        enrich_jsonl = output_dir / f"{enrich_prefix}_row_audit.jsonl"
        with enrich_jsonl.open("w", encoding="utf-8") as fh:
            for a in enriched_bundle["row_audits"]:
                fh.write(json.dumps(a.as_dict(), ensure_ascii=False) + "\n")
        artifacts["enriched_row_audit_jsonl"] = str(enrich_jsonl)
    summary["_artifacts"] = artifacts
    return summary


def _render_report(summary: Mapping[str, Any]) -> str:
    cov = summary.get("coverage") or {}
    lines = [
        f"# {EXP_ID} sealed regime audit — {summary.get('courier_day')}",
        "",
        f"**Overall:** {summary.get('overall')}",
        f"**Eligible creates:** {summary.get('eligible_n')} (void t_ws: {summary.get('void_missing_t_ws_n')})",
        "",
        "## Coverage",
        "",
        f"- regime_id parse rate: {cov.get('regime_id_parse_rate')}",
        f"- knowable_at_t present rate: {cov.get('knowable_at_t_present_rate')}",
        f"- instr pending/unknown rate: {cov.get('instr_pending_or_unknown_rate')}",
        f"- fee unverified rate: {cov.get('fee_unverified_rate')}",
        f"- quote assumed/UNK rate: {cov.get('quote_assumed_or_unk_rate')}",
        f"- quote_verified true rate: {cov.get('quote_verified_true_rate')}",
        f"- regime_gate_keys_n: {cov.get('regime_gate_keys_n')}",
        "",
        "## Gates",
        "",
    ]
    for gid, gate in (summary.get("gates") or {}).items():
        lines.append(f"- **{gid}:** {gate.get('result')} — {gate.get('note')}")
    exp007b = summary.get("exp007b")
    if isinstance(exp007b, dict):
        es = exp007b.get("enriched_sample")
        if isinstance(es, dict):
            lines.extend(
                [
                    "",
                    f"## EXP-007b enriched sample ({es.get('eligible_n')} rows)",
                    "",
                    f"**Overall (enriched):** {es.get('overall')}",
                    "",
                ]
            )
            for gid, gate in (es.get("gates") or {}).items():
                lines.append(f"- **{gid}:** {gate.get('result')} — {gate.get('note')}")
            lines.append("")
    lines.append("")
    vc = cov.get("value_counts") or {}
    for label, counts in sorted(vc.items()):
        lines.append(f"### {label}")
        for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
            lines.append(f"- `{k}`: {v}")
        lines.append("")
    return "\n".join(lines)


def run_multi_day(
    observe_paths: Sequence[Path],
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = DEFAULT_PREFIX,
    enrich_paths: Sequence[Path] | None = None,
) -> dict[str, Any]:
    per_day: dict[str, Any] = {}
    day_overalls: list[str] = []
    enriched_overalls: list[str] = []
    for path in observe_paths:
        day_enrich = None
        if enrich_paths:
            day_key = _day_key_from_path(path)
            day_enrich = [p for p in enrich_paths if day_key in p.stem]
        summary = run_single_day(
            path,
            output_dir=output_dir,
            prefix=prefix,
            enrich_paths=day_enrich or None,
        )
        per_day[summary["courier_day"]] = summary
        day_overalls.append(summary["overall"])
        b = summary.get("exp007b")
        if isinstance(b, dict) and isinstance(b.get("overall"), str):
            enriched_overalls.append(b["overall"])

    cross = "FAIL" if "FAIL" in day_overalls else ("INCOMPLETE" if "INCOMPLETE" in day_overalls else "PASS")
    cross_enriched = None
    if enriched_overalls:
        cross_enriched = (
            "FAIL"
            if "FAIL" in enriched_overalls
            else ("INCOMPLETE" if "INCOMPLETE" in enriched_overalls else "PASS")
        )
    return {
        "exp": EXP_ID,
        "cross_day_overall": cross,
        "cross_day_enriched_overall": cross_enriched,
        "day_overalls": day_overalls,
        "enriched_day_overalls": enriched_overalls,
        "per_day": per_day,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{EXP_ID} sealed platform-regime coverage audit")
    parser.add_argument("observe_paths", nargs="+", type=Path, help="Day-aligned sealed observe JSONL.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument(
        "--enrich-jsonl",
        nargs="*",
        type=Path,
        default=None,
        help="EXP-007b regime_enrich side JSONL (optional overlay audit).",
    )
    args = parser.parse_args(argv)

    if len(args.observe_paths) == 1:
        summary = run_single_day(
            args.observe_paths[0],
            output_dir=args.output_dir,
            prefix=args.prefix,
            enrich_paths=args.enrich_jsonl,
        )
        print(
            json.dumps(
                {
                    "exp": summary["exp"],
                    "courier_day": summary["courier_day"],
                    "overall": summary["overall"],
                    "eligible_n": summary["eligible_n"],
                    "gates": {k: v.get("result") for k, v in (summary.get("gates") or {}).items()},
                    "artifacts": summary.get("_artifacts"),
                },
                indent=2,
            )
        )
        return 1 if summary["overall"] in ("INCOMPLETE", "FAIL") else 0

    multi = run_multi_day(
        args.observe_paths,
        output_dir=args.output_dir,
        prefix=args.prefix,
        enrich_paths=args.enrich_jsonl,
    )
    print(
        json.dumps(
            {
                "cross_day_overall": multi["cross_day_overall"],
                "cross_day_enriched_overall": multi.get("cross_day_enriched_overall"),
                "day_overalls": multi["day_overalls"],
                "enriched_day_overalls": multi.get("enriched_day_overalls"),
                "per_day_eligible_n": {
                    k: v.get("eligible_n") for k, v in multi["per_day"].items()
                },
            },
            indent=2,
        )
    )
    return 1 if multi["cross_day_overall"] in ("INCOMPLETE", "FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(main())
