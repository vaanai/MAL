#!/usr/bin/env python3
"""Derive fast-0 runner downtime from heartbeat.jsonl (DEC-016 Amendment 3 (b)).

Output: JSON list of merged [start_ms, end_ms) pairs for `forward_exp012_replay.py --downtime`,
plus a summary on stdout. A sample is fresh if ok and sampled_ms - status_ts_ms < 60_000.
Down: (1) every minute of [--from, --to) (aligned to --from) with no fresh sample;
(2) every minute overlapping a sampler gap longer than 60 s (no evidence of being up);
(3) each restart, from the last fresh sample before it to 10 min after the first fresh
sample of the new run. A restart is a pid change between fresh samples or an ok:false gap.
The first fresh sample in the file counts as an initial start: excluded for 10 min, not
counted as a restart.
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
    return bool(r.get("ok")) and isinstance(ts, int) and r["sampled_ms"] - ts < FRESH_MS


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
    fresh_minutes: set[int] = set()
    seen_minutes: set[int] = set()
    for r in samples:
        t = r["sampled_ms"]
        if not (from_ms <= t < to_ms):
            continue
        k = (t - from_ms) // MINUTE
        seen_minutes.add(k)
        if is_fresh(r):
            fresh_minutes.add(k)
    down: list[tuple[int, int]] = []

    def minute_iv(k: int) -> tuple[int, int]:
        return from_ms + k * MINUTE, min(from_ms + (k + 1) * MINUTE, to_ms)

    for k in range(n_min):
        if k not in fresh_minutes:
            down.append(minute_iv(k))
    # sampler gaps longer than 60 s
    gaps = 0
    for a, b in zip(samples, samples[1:]):
        if b["sampled_ms"] - a["sampled_ms"] > FRESH_MS:
            gaps += 1
            lo, hi = a["sampled_ms"], b["sampled_ms"]
            for k in range(n_min):
                s, e = minute_iv(k)
                if s < hi and e > lo:
                    down.append((s, e))
    # restarts
    restarts = 0
    excl: list[tuple[int, int]] = []
    last_fresh: dict[str, Any] | None = None
    bad_run = False  # an ok:false sample since last_fresh
    for r in samples:
        if is_fresh(r):
            if last_fresh is None:
                excl.append((r["sampled_ms"], r["sampled_ms"] + SETTLE_MS))
            else:
                p0, p1 = last_fresh.get("pid"), r.get("pid")
                if bad_run or (p0 is not None and p1 is not None and p0 != p1):
                    restarts += 1
                    excl.append((last_fresh["sampled_ms"], r["sampled_ms"] + SETTLE_MS))
            last_fresh, bad_run = r, False
        elif not r.get("ok"):
            bad_run = True
    if bad_run and last_fresh is not None:
        restarts += 1
        excl.append((last_fresh["sampled_ms"], to_ms))
    down.extend(excl)
    clipped = [(max(a, from_ms), min(b, to_ms)) for a, b in down]
    merged = merge([iv for iv in clipped if iv[1] > iv[0]])
    down_minutes = sum(
        1 for k in range(n_min) if any(a < minute_iv(k)[1] and b > minute_iv(k)[0] for a, b in merged)
    )
    summary = {
        "from_ms": from_ms,
        "to_ms": to_ms,
        "window_minutes": n_min,
        "down_minutes": down_minutes,
        "up_minutes": n_min - down_minutes,
        "down_ms": sum(b - a for a, b in merged),
        "restarts": restarts,
        "sampler_gaps_over_60s": gaps,
        "sampler_coverage": len(seen_minutes) / n_min,
        "samples_in_window": sum(1 for r in samples if from_ms <= r["sampled_ms"] < to_ms),
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
