#!/usr/bin/env python3
"""Outcome-blind per-UTC-hour data-integrity report for paper books on the arena host (DEC-031 Amendment 6 item 2).

Reads ONLY: tape-hour file names and sizes, the counts-only gap rows and the feed-stamped `t_recv_ms` of tape rows, an optional feed-status
sampler jsonl (format of tools/hv4_shakedown_report.py `sample`), and a pinned thresholds JSON.
FORBIDDEN (refused with exit 2): any path containing "ledger", "decisions", "bots.json" or "runner-status". Never read: fills, prices, exits,
position state, P&L, decision-row contents. No mint, wallet or signature is kept or printed. Stdlib plus the zstd CLI for .zst hours.

Output (stdout): one JSON line per UTC hour, then one final line {"type":"sha256",...} whose sha256 covers the hour lines (each + "\\n").
Threshold keys (all optional; a key that is absent is not checked, a key whose metric cannot be computed FAILS the hour):
  max_gap_s              longest data_loss gap row span (to_ms - from_ms) in the hour, seconds
  max_data_loss_count    number of data_loss gap rows in the hour
  min_samples_per_hour   feed-status samples inside the hour
  max_sample_gap_s       longest gap between consecutive samples, clipped to the hour (no sample: the whole hour)
  max_input_age_s        longest feed-stamped silence: tape t_recv_ms gaps (clipped to the hour) and sampler (t_ms - feed.updated_ms)
A missing tape hour file or a zero-size file always fails the hour.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "arena_hour_health_v1"
SAMPLE_SCHEMA = "hv4_shakedown_sample_v1"
HOUR_MS = 3_600_000
FORBIDDEN_WORDS = ("ledger", "decisions", "bots.json", "runner-status")
KIND_OK = re.compile(r"^[a-z0-9_]{1,40}$")
TAPE_NAME = re.compile(r"^tape-(\d{4}-\d\d-\d\dT\d\d)\.jsonl(\.zst)?$")
RECV_RE = re.compile(rb'"t_recv_ms":\s*(\d+)')
THRESHOLD_KEYS = ("max_gap_s", "max_data_loss_count", "min_samples_per_hour", "max_sample_gap_s", "max_input_age_s")


class Refused(Exception):
    pass


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_hour(s: str) -> int:
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H"):
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000) // HOUR_MS * HOUR_MS
        except ValueError:
            pass
    raise ValueError(f"bad UTC hour: {s!r}")


def guard(path: str | os.PathLike) -> Path:
    s = os.path.abspath(str(path))
    for cand in (s, os.path.realpath(s)):
        low = cand.lower()
        if any(w in low for w in FORBIDDEN_WORDS):
            raise Refused("path refused by policy (ledgers, decisions, bots.json and runner-status are never opened)")
    return Path(s)


def hour_name(h: int) -> str:
    return datetime.fromtimestamp(h / 1000, tz=timezone.utc).strftime("tape-%Y-%m-%dT%H.jsonl")


def scan_hour(path: Path) -> dict:
    """Counts only: gap rows by (kind, data_loss), max data_loss span, and the feed-stamped t_recv_ms gaps of the file."""
    guard(path)
    gaps: Counter = Counter()
    max_span_ms: int | None = None
    recv: list[int] = []
    err = None
    proc = None
    try:
        if path.name.endswith(".zst"):
            exe = shutil.which("zstd")
            if exe is None:
                return {"error": "zstd CLI not found"}
            proc = subprocess.Popen([exe, "-dc", "-q", str(path)], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
            stream = proc.stdout
        else:
            stream = open(path, "rb")
        assert stream is not None
        try:
            for ln in stream:
                m = RECV_RE.search(ln)
                if b'"gap"' in ln:
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and r.get("type") == "gap":
                        k = r.get("kind")
                        kind = k if isinstance(k, str) and KIND_OK.match(k) else "other"
                        dl = r.get("data_loss") is True
                        gaps[(kind, dl)] += 1
                        a, b = r.get("from_ms"), r.get("to_ms")
                        if dl and isinstance(a, int) and isinstance(b, int) and b >= a:
                            max_span_ms = max(max_span_ms or 0, b - a)
                        continue
                if m:
                    recv.append(int(m.group(1)))
        finally:
            stream.close()
        if proc is not None and proc.wait() != 0:
            err = "zstd failed"
    except OSError as e:
        err = type(e).__name__
    return {"gaps": gaps, "max_span_ms": max_span_ms, "recv": recv, "error": err}


def tape_age_ms(recv: list[int], h: int) -> int | None:
    """Longest feed-stamped silence inside [h, h+1h), edges clipped to the hour. None when the file has no feed timestamp."""
    ts = sorted(t for t in recv if h <= t < h + HOUR_MS)
    if not ts:
        return None
    pts = [h] + ts + [h + HOUR_MS]
    return max(b - a for a, b in zip(pts, pts[1:]))


def load_samples(path: Path | None) -> tuple[list[tuple[int, int | None]] | None, int]:
    """[(t_ms, feed.updated_ms or None)] sorted, or None if no file / unreadable. Also the number of error lines."""
    if path is None:
        return None, 0
    try:
        data = guard(path).read_bytes()
    except OSError:
        return None, 0
    out, errs = [], 0
    for ln in data.split(b"\n"):
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(r, dict) or r.get("schema") != SAMPLE_SCHEMA or not isinstance(r.get("t_ms"), int):
            continue
        if "error" in r:
            errs += 1
            continue
        f = r.get("feed")
        u = f.get("updated_ms") if isinstance(f, dict) else None
        out.append((r["t_ms"], u if isinstance(u, int) and not isinstance(u, bool) else None))
    return sorted(out), errs


def sample_stats(samples: list[tuple[int, int | None]], h: int) -> dict:
    ts = [t for t, _ in samples]
    inside = [(t, u) for t, u in samples if h <= t < h + HOUR_MS]
    lo = max([t for t in ts if t < h], default=None)
    hi = min([t for t in ts if t >= h + HOUR_MS], default=None)
    pts = [max(lo, h) if lo is not None else h] + [t for t, _ in inside] + [min(hi, h + HOUR_MS) if hi is not None else h + HOUR_MS]
    max_gap = max(b - a for a, b in zip(pts, pts[1:]))
    ages = [t - u for t, u in inside if u is not None and t >= u]
    return {"n": len(inside), "max_gap_ms": max_gap, "max_age_ms": max(ages) if ages else None, "n_with_feed_time": len(ages)}


def evaluate(tape_dir: Path, samples_path: Path | None, h0: int, h1: int, thr: dict) -> list[dict]:
    tape_dir = guard(tape_dir)
    names = sorted(p.name for p in tape_dir.iterdir() if TAPE_NAME.match(p.name)) if tape_dir.is_dir() else []
    samples, sample_errs = load_samples(samples_path)
    rows = []
    h = h0
    while h <= h1:
        stamp = hour_name(h)
        have = [n for n in (stamp, stamp + ".zst") if n in names]
        failed: list[str] = []
        unmeasurable: list[str] = []
        row: dict[str, Any] = {"schema": SCHEMA, "hour": iso(h)}
        tape_age = None
        if not have:
            row.update(tape_file=None, tape_size=None, gap_rows=[], data_loss_rows=None, data_loss_max_span_s=None)
            failed.append("tape_file_missing")
        else:
            p = tape_dir / have[0]
            try:
                size = p.stat().st_size
            except OSError:
                size = None
            row.update(tape_file=have[0], tape_size=size)
            sc = None
            if size is None:
                failed.append("tape_file_unreadable")
            elif size == 0:
                failed.append("tape_file_zero_size")
            else:
                sc = scan_hour(p)
                if sc.get("error"):
                    failed.append("tape_scan_error")
                    sc = None
            if sc is None:
                row.update(gap_rows=[], data_loss_rows=None, data_loss_max_span_s=None)
            else:
                g = sc["gaps"]
                row["gap_rows"] = [{"kind": k, "data_loss": dl, "rows": n} for (k, dl), n in sorted(g.items())]
                row["data_loss_rows"] = sum(n for (_, dl), n in g.items() if dl)
                row["data_loss_max_span_s"] = None if sc["max_span_ms"] is None else sc["max_span_ms"] / 1000.0
                ta = tape_age_ms(sc["recv"], h)
                tape_age = None if ta is None else ta / 1000.0
        row["tape_input_age_max_s"] = tape_age
        if samples is None:
            row.update(samples_n=None, sample_gap_max_s=None, sampler_input_age_max_s=None)
        else:
            ss = sample_stats(samples, h)
            row.update(samples_n=ss["n"], sample_gap_max_s=ss["max_gap_ms"] / 1000.0,
                       sampler_input_age_max_s=None if ss["max_age_ms"] is None else ss["max_age_ms"] / 1000.0)

        def over(name: str, val: float | None, limit: Any) -> None:
            if val is None:
                unmeasurable.append(name)
            elif val > limit:
                failed.append(name)

        have_tape = not any(f.startswith("tape_") for f in failed)
        if "max_gap_s" in thr:
            if not have_tape:
                unmeasurable.append("max_gap_s")
            else:
                over("max_gap_s", row["data_loss_max_span_s"] if row["data_loss_rows"] else 0.0, thr["max_gap_s"])
        if "max_data_loss_count" in thr:
            over("max_data_loss_count", row["data_loss_rows"], thr["max_data_loss_count"])
        if "min_samples_per_hour" in thr:
            if row["samples_n"] is None:
                unmeasurable.append("min_samples_per_hour")
            elif row["samples_n"] < thr["min_samples_per_hour"]:
                failed.append("min_samples_per_hour")
        if "max_sample_gap_s" in thr:
            over("max_sample_gap_s", row["sample_gap_max_s"], thr["max_sample_gap_s"])
        if "max_input_age_s" in thr:
            over("max_input_age_s[tape]", row["tape_input_age_max_s"], thr["max_input_age_s"])
            if samples is not None:
                over("max_input_age_s[sampler]", row["sampler_input_age_max_s"], thr["max_input_age_s"])
        # "unmeasurable" is treated as failing (Amendment 6 item 2.2): the hour is excluded, later evidence does not replace it
        row["unmeasurable"] = sorted(set(unmeasurable))
        row["failed_checks"] = sorted(set(failed))
        row["excluded"] = bool(failed or unmeasurable)
        rows.append(row)
        h += HOUR_MS
    return rows


def load_thresholds(path: Path) -> dict:
    thr = json.loads(guard(path).read_bytes())
    if not isinstance(thr, dict):
        raise ValueError("thresholds must be a JSON object")
    bad = [k for k in thr if k not in THRESHOLD_KEYS]
    if bad:
        raise ValueError(f"unknown threshold keys: {bad}")
    for k, v in thr.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise ValueError(f"threshold {k} must be a non-negative number")
    return thr


def render(rows: list[dict], thr_sha: str) -> list[str]:
    lines = [canon(r) for r in rows]
    body = "".join(ln + "\n" for ln in lines)
    final = {"type": "sha256", "schema": SCHEMA, "hours": len(rows), "excluded_hours": sum(1 for r in rows if r["excluded"]),
             "thresholds_sha256": thr_sha, "sha256": hashlib.sha256(body.encode()).hexdigest()}
    return lines + [canon(final)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--samples")
    ap.add_argument("--from", dest="frm", required=True, help="first UTC hour, e.g. 2026-10-23T00")
    ap.add_argument("--to", required=True, help="last UTC hour (inclusive)")
    ap.add_argument("--thresholds", required=True)
    a = ap.parse_args(argv)
    try:
        tape = guard(a.tape_dir)
        samples = guard(a.samples) if a.samples else None
        thp = guard(a.thresholds)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    try:
        h0, h1 = parse_hour(a.frm), parse_hour(a.to)
        if h1 < h0:
            raise ValueError("--to before --from")
        thr = load_thresholds(thp)
        thr_sha = hashlib.sha256(thp.read_bytes()).hexdigest()
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        out = render(evaluate(tape, samples, h0, h1, thr), thr_sha)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
