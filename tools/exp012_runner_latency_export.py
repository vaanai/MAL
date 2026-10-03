"""Allowlisted latency export and k(p50)/k(p90) for DEC-016 Amendment 3 (a)1.

Reads the fast-0 runner's `exp012-gate.jsonl` and `decisions.jsonl` and writes
ONLY the keys in `EXPORT_KEYS`. It refuses `positions.jsonl` and `runner-status*`
by name (Amendment 3 seal extension). Nothing here reads a P&L field.

Decisions used: shadow-ledger EXP-012 `enter` decisions with `decision_t_ms` in
[--from, --to), plus shadow-ledger `stale_recv` skips of the same books (k = +inf).
A decision is EXP-012 when its (book, mint) has a gate row; the gate row also gives
`mig_ms` (chain time).

    L_i  = decision_t_ms + recv_to_decision_ms - (mig_ms + 500)
    k_i  = 1 + ceil(L_i / slot_ms)      (+inf for a stale_recv drop)
    slot_ms = 3_600_000 / mean(slot_span) over the window's hours of the forward
              walk's verify.jsonl (`slot_span` = end_slot - start_slot, written by
              tools/backfill_verify.py `build_report`); hours with issues are skipped
    k(p) = max(1, sorted(k)[round(p * (n - 1))])   (`_pct` convention)

Fewer than 100 decisions: NOT_DECIDABLE.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

EXPORT_KEYS = ("mint", "mig_ms", "decision_t_ms", "recv_to_decision_ms", "ledger", "action", "stale")
FORBIDDEN_PREFIXES = ("positions", "runner-status")
MIN_DECISIONS = 100
SLOT_OFFSET_MS = 500
HOUR_MS = 3_600_000


class Refused(Exception):
    pass


def refuse_forbidden(path: Path) -> None:
    name = Path(path).name
    if any(name.startswith(p) for p in FORBIDDEN_PREFIXES):
        raise Refused(f"{name}: positions.jsonl and runner-status* hold P&L and are sealed (DEC-016 Amendment 3)")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    refuse_forbidden(path)
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def build_export(gate_rows: Iterable[dict[str, Any]], decision_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Allowlisted rows for EXP-012 decisions (shadow ledger: enter, and stale_recv skips)."""
    mig: dict[tuple[Any, Any], Any] = {}
    for g in gate_rows:
        mig.setdefault((g.get("book"), g.get("mint")), g.get("mig_ms"))
    out: list[dict[str, Any]] = []
    for d in decision_rows:
        key = (d.get("book"), d.get("mint"))
        if key not in mig or d.get("ledger") != "shadow":
            continue
        stale = d.get("reason") == "stale_recv"
        if d.get("action") != "enter" and not stale:
            continue
        lat = d.get("latency") or {}
        out.append(
            {
                "mint": d["mint"],
                "mig_ms": mig[key],
                "decision_t_ms": d.get("decision_t_ms"),
                "recv_to_decision_ms": lat.get("recv_to_decision_ms"),
                "ledger": d.get("ledger"),
                "action": d.get("action"),
                "stale": stale,
            }
        )
    assert all(set(r) == set(EXPORT_KEYS) for r in out), "export row has a non-allowlisted key"
    return out


def export_sha256(rows: Sequence[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update((json.dumps({k: r[k] for k in EXPORT_KEYS}, sort_keys=True) + "\n").encode("utf-8"))
    return h.hexdigest()


def export_file_text(rows: Sequence[dict[str, Any]]) -> str:
    """Exact text of the `--export-out` file."""
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)


def pct(sorted_vals: Sequence[float], p: float) -> float:
    """Non-interpolating percentile, the `_pct` convention."""
    if not sorted_vals:
        return 0.0
    return sorted_vals[int(round(p * (len(sorted_vals) - 1)))]


def slot_ms_from_verify(verify_rows: Iterable[dict[str, Any]], from_ms: int, to_ms: int) -> float | None:
    """3_600_000 / mean slot span over clean hours in [from_ms, to_ms). Last line per hour wins."""
    last: dict[str, dict[str, Any]] = {}
    for r in verify_rows:
        if isinstance(r.get("hour"), str):
            last[r["hour"]] = r
    spans = []
    for hour, r in last.items():
        t = datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000
        if not (from_ms <= t < to_ms) or r.get("issues"):
            continue
        span = r.get("slot_span")
        s, e = r.get("start_slot"), r.get("end_slot")
        if not isinstance(span, int) and isinstance(s, int) and isinstance(e, int):
            span = e - s
        if isinstance(span, int) and span > 0:
            spans.append(span)
    if not spans:
        return None
    return HOUR_MS / (sum(spans) / len(spans))


def k_value(row: dict[str, Any], slot_ms: float) -> float:
    if row.get("stale"):
        return math.inf
    lat = row["decision_t_ms"] + row["recv_to_decision_ms"] - (row["mig_ms"] + SLOT_OFFSET_MS)
    return 1 + math.ceil(lat / slot_ms)


def compute(rows: Sequence[dict[str, Any]], slot_ms: float | None, from_ms: int, to_ms: int) -> dict[str, Any]:
    sel = []
    seen: set[str] = set()
    for r in rows:
        t = r.get("decision_t_ms")
        if t is None or not (from_ms <= t < to_ms):
            continue
        if not r.get("stale") and (r.get("action") != "enter" or r.get("recv_to_decision_ms") is None or r.get("mig_ms") is None):
            continue
        if r["mint"] in seen:
            continue
        seen.add(r["mint"])
        sel.append(r)
    n = len(sel)
    out: dict[str, Any] = {"n": n, "n_stale": sum(1 for r in sel if r.get("stale")), "slot_ms": slot_ms, "in_window_rows_sha256": export_sha256(sel)}
    if n < MIN_DECISIONS or not slot_ms:
        out.update(verdict="NOT_DECIDABLE", k_p50=None, k_p90=None)
        return out
    ks = sorted(k_value(r, slot_ms) for r in sel)
    k50, k90 = max(1, pct(ks, 0.5)), max(1, pct(ks, 0.9))
    out.update(verdict="OK", k_p50="inf" if math.isinf(k50) else int(k50), k_p90="inf" if math.isinf(k90) else int(k90))
    return out


def _ms(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gate", type=Path, required=True, help="exp012-gate.jsonl")
    ap.add_argument("--decisions", type=Path, required=True, help="decisions.jsonl")
    ap.add_argument("--verify", type=Path, required=True, help="forward walk verify.jsonl")
    ap.add_argument("--from", dest="from_", required=True, help="runner clean clock, ISO or epoch ms")
    ap.add_argument("--to", required=True, help="exclusive end, ISO or epoch ms")
    ap.add_argument("--export-out", type=Path, help="write the allowlisted export here (jsonl)")
    args = ap.parse_args(argv)
    try:
        rows = build_export(_read_jsonl(args.gate), _read_jsonl(args.decisions))
        verify = _read_jsonl(args.verify)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    f, t = _ms(args.from_), _ms(args.to)
    text = export_file_text(rows)
    if args.export_out:
        args.export_out.write_text(text, encoding="utf-8")
    res = compute(rows, slot_ms_from_verify(verify, f, t), f, t)
    # sha256 of the whole written export file (all allowlisted rows, not only the window)
    res["export_file_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    json.dump(res, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
