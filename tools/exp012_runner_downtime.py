#!/usr/bin/env python3
"""Derive fast-0 runner downtime from heartbeat.jsonl (DEC-016 Amendment 3 (b)).

Output: JSON list of merged [start_ms, end_ms) pairs for `forward_exp012_replay.py --downtime`,
plus a summary on stdout.

A sample is fresh if ok and 0 <= sampled_ms - status_ts_ms < 60_000 (a future status
timestamp is not fresh). Each sample's fresh/stale state holds until the next sample, capped
at 60 s; time not held fresh is down, so a sampler gap over 60 s is down. Clipped to
[--from, --to). The minute grid (aligned to --from) is used only in the summary.

Also down: each restart, from the last fresh sample before it to 10 min after the first
fresh sample of the new run. A restart is a pid change between fresh samples or >= 2
consecutive ok:false samples (one can be a torn read). The first fresh sample in the file
counts as an initial start: excluded for 10 min, not counted as a restart.

If more than 5% of in-window samples have a null pid, or there are no samples, the summary
has NOT_DECIDABLE true and the exit code is 3 (the downtime file is still written).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from tools.forward_exp012_replay import _to_ms

MINUTE = 60_000
FRESH_MS = 60_000
SETTLE_MS = 600_000
MIN_BAD_RUN = 2
NULL_PID_MAX_SHARE = 0.05


def load_samples(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("sampled_ms"), int):
            rows.append(r)
    rows.sort(key=lambda r: r["sampled_ms"])
    return rows


def is_fresh(r: dict[str, Any]) -> bool:
    ts = r.get("status_ts_ms")
    if not (bool(r.get("ok")) and isinstance(ts, int)):
        return False
    return 0 <= r["sampled_ms"] - ts < FRESH_MS


def merge(iv: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in sorted(iv):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def derive(samples: list[dict[str, Any]], from_ms: int, to_ms: int) -> tuple[list[tuple[int, int]], dict[str, Any]]:
    assert to_ms > from_ms
    n_min = -(-(to_ms - from_ms) // MINUTE)
    up: list[tuple[int, int]] = []
    for i, r in enumerate(samples):
        if not is_fresh(r):
            continue
        end = r["sampled_ms"] + FRESH_MS
        if i + 1 < len(samples):
            end = min(end, samples[i + 1]["sampled_ms"])
        up.append((r["sampled_ms"], end))
    up = merge([(max(a, from_ms), min(b, to_ms)) for a, b in up])
    down: list[tuple[int, int]] = []
    cur = from_ms
    for a, b in up:
        if a > cur:
            down.append((cur, a))
        cur = max(cur, b)
    if cur < to_ms:
        down.append((cur, to_ms))
    gaps = sum(1 for a, b in zip(samples, samples[1:]) if b["sampled_ms"] - a["sampled_ms"] > FRESH_MS)
    restarts = 0
    excl: list[tuple[int, int]] = []
    last_fresh: dict[str, Any] | None = None
    last_pid: int | None = None
    bad_run = 0
    for r in samples:
        if is_fresh(r):
            if last_fresh is None:
                excl.append((r["sampled_ms"], r["sampled_ms"] + SETTLE_MS))
            else:
                p1 = r.get("pid")
                if bad_run >= MIN_BAD_RUN or (last_pid is not None and p1 is not None and last_pid != p1):
                    restarts += 1
                    excl.append((last_fresh["sampled_ms"], r["sampled_ms"] + SETTLE_MS))
            last_fresh, bad_run = r, 0
            if r.get("pid") is not None:
                last_pid = r["pid"]
        elif not r.get("ok"):
            bad_run += 1
    if bad_run >= MIN_BAD_RUN and last_fresh is not None:
        restarts += 1
        excl.append((last_fresh["sampled_ms"], to_ms))
    down.extend(excl)
    merged = merge([iv for iv in ((max(a, from_ms), min(b, to_ms)) for a, b in down) if iv[1] > iv[0]])

    def minute_iv(k: int) -> tuple[int, int]:
        return from_ms + k * MINUTE, min(from_ms + (k + 1) * MINUTE, to_ms)

    down_minutes = sum(
        1 for k in range(n_min) if any(a < minute_iv(k)[1] and b > minute_iv(k)[0] for a, b in merged)
    )
    inwin = [r for r in samples if from_ms <= r["sampled_ms"] < to_ms]
    seen = {(r["sampled_ms"] - from_ms) // MINUTE for r in inwin}
    null_pid = sum(1 for r in inwin if r.get("pid") is None)
    share = null_pid / len(inwin) if inwin else 1.0
    reasons = []
    if not inwin:
        reasons.append("no samples in window")
    if share > NULL_PID_MAX_SHARE:
        reasons.append(f"null pid share {share:.3f} > {NULL_PID_MAX_SHARE}")
    summary = {
        "from_ms": from_ms,
        "to_ms": to_ms,
        "window_minutes": n_min,
        "down_minutes": down_minutes,
        "up_minutes": n_min - down_minutes,
        "down_ms": sum(b - a for a, b in merged),
        "restarts": restarts,
        "sampler_gaps_over_60s": gaps,
        "sampler_coverage": len(seen) / n_min,
        "samples_in_window": len(inwin),
        "null_pid_samples": null_pid,
        "null_pid_share": share,
        "NOT_DECIDABLE": bool(reasons),
        "not_decidable_reasons": reasons,
    }
    return merged, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--heartbeat", type=Path, required=True)
    ap.add_argument("--from", dest="from_", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--out", type=Path, required=True, help="downtime JSON for forward_exp012_replay --downtime")
    args = ap.parse_args(argv)
    merged, summary = derive(load_samples(args.heartbeat), _to_ms(args.from_), _to_ms(args.to))
    args.out.write_text(json.dumps([[a, b] for a, b in merged]) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 3 if summary["NOT_DECIDABLE"] else 0


if __name__ == "__main__":
    sys.exit(main())
