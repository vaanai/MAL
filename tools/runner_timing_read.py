#!/usr/bin/env python3
"""Timing-only reader for fast-0 runner side files (DEC-016 Am.2/Am.3 seal).

Before the EXP-012 FINAL read, arm-audit.jsonl and heartbeat.jsonl are read only through
this tool. Output is built from an allowlist of named timing aggregates over the WHOLE
window. No outcome, book, candidates, pass flags, error, mint, per-day or per-hour value is
ever read into the output, and the total arm-audit row count is never printed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SEAL_UNTIL = "2026-10-16T02:00:00Z"
SCHEMA_AUDIT = "forward_paper_arm_audit_v1"
MIN_N_WINDOW_MS = 24 * 3_600_000
REFUSAL = "sealed until the EXP-012 FINAL read (DEC-016 Am.2/Am.3)"
ALLOWED_OUTPUT_KEYS = frozenset({
    "window_start", "window_end",
    "eval_delay_ms_n", "eval_delay_ms_p10", "eval_delay_ms_p50", "eval_delay_ms_p90", "eval_delay_ms_max",
    "hb_lag_ms_p10", "hb_lag_ms_p50", "hb_lag_ms_p90", "hb_lag_ms_max", "hb_ok_false_count",
})
# Any flag that would group, split or break down is refused (unknown flags too).
REFUSED_FLAGS = ("--per-day", "--per-hour", "--by-day", "--by-hour", "--by-outcome", "--by-book", "--group-by", "--daily", "--hourly", "--breakdown", "--split")


def parse_iso_ms(s: str) -> int:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _rows(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                yield r


def percentile(sorted_vals: list[int], q: float) -> int:
    """Nearest-rank percentile on a sorted non-empty list."""
    k = -(-round(q * len(sorted_vals) * 1000) // 1000) - 1
    return sorted_vals[max(0, min(len(sorted_vals) - 1, k))]


def _dist(vals: list[int]) -> dict[str, int] | None:
    if not vals:
        return None
    v = sorted(vals)
    return {"p10": percentile(v, 0.10), "p50": percentile(v, 0.50), "p90": percentile(v, 0.90), "max": v[-1]}


def build_output(audit_path: Path | None, heartbeat_path: Path | None, start_ms: int, end_ms: int) -> dict[str, Any]:
    out: dict[str, Any] = {"window_start": _iso(start_ms), "window_end": _iso(end_ms)}
    if audit_path is not None:
        delays: list[int] = []
        for r in _rows(audit_path):
            if r.get("schema") != SCHEMA_AUDIT:
                continue
            clock, recv = _int(r.get("eval_clock_ms")), _int(r.get("complete_t_recv_ms"))
            if clock is None or recv is None or not (start_ms <= clock < end_ms):
                continue
            delays.append(clock - recv)  # all outcomes together; no other field is read
        d = _dist(delays)
        if d:
            for k, v in d.items():
                out[f"eval_delay_ms_{k}"] = v
            if end_ms - start_ms >= MIN_N_WINDOW_MS:
                out["eval_delay_ms_n"] = len(delays)
    if heartbeat_path is not None:
        lags: list[int] = []
        bad = 0
        for r in _rows(heartbeat_path):
            t = _int(r.get("sampled_ms"))
            if t is None or not (start_ms <= t < end_ms):
                continue
            if r.get("ok") is False:
                bad += 1
            lag = _int(r.get("lag_ms"))
            if lag is not None:
                lags.append(lag)
        d = _dist(lags)
        if d:
            for k, v in d.items():
                out[f"hb_lag_ms_{k}"] = v
        out["hb_ok_false_count"] = bad
    extra = set(out) - ALLOWED_OUTPUT_KEYS
    assert not extra, f"non-allowlisted output keys: {sorted(extra)}"
    return out


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    for a in args_in:
        if a.split("=", 1)[0] in REFUSED_FLAGS:
            print(REFUSAL, file=sys.stderr)
            return 2
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm-audit", type=Path)
    ap.add_argument("--heartbeat", type=Path)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    try:
        args, unknown = ap.parse_known_args(args_in)
    except SystemExit as e:
        return int(e.code or 2)
    if unknown:  # an unknown flag may be a grouping flag in disguise
        print(REFUSAL, file=sys.stderr)
        return 2
    if args.arm_audit is None and args.heartbeat is None:
        print("give --arm-audit and/or --heartbeat", file=sys.stderr)
        return 2
    s, e = parse_iso_ms(args.start), parse_iso_ms(args.end)
    if e <= s:
        print("--end must be after --start", file=sys.stderr)
        return 2
    print(json.dumps(build_output(args.arm_audit, args.heartbeat, s, e), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
