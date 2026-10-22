#!/usr/bin/env python3
"""Outcome-blind per-UTC-hour data-integrity report for paper books on the arena host (DEC-031 Amendment 6 item 2).

Reads ONLY: tape-hour file names and sizes, the counts-only gap rows and the feed-stamped `t_recv_ms` of tape rows, an optional feed-status
sampler jsonl (format of tools/hv4_shakedown_report.py `sample`), and a pinned thresholds JSON.
FORBIDDEN (refused with exit 2): any path containing "ledger", "decisions", "bots.json" or "runner-status". Never read: fills, prices, exits,
position state, P&L, decision-row contents. No mint, wallet or signature is kept or printed. Stdlib plus the zstd CLI for .zst hours.

Output (stdout): one JSON line per UTC hour, then one final line {"type":"sha256",...} whose sha256 covers the hour lines (each + "\\n").
Threshold file: {check: {"value": number, "label": "price-path" | "decision-input"}}; checks (all optional, absent = not checked; a check whose metric
cannot be computed FAILS the hour):
  max_gap_s              longest data_loss gap span (to_ms - from_ms, unclipped) among spans OVERLAPPING the hour, seconds. Open/closed twins collapse.
  max_data_loss_count    number of distinct data_loss spans overlapping the hour (a span fails every hour it overlaps)
  min_samples_per_hour   feed-status samples inside the hour                                  (needs --samples)
  max_sample_gap_s       longest gap between consecutive samples, clipped to the hour          (needs --samples)
  max_input_age_s        longest silence of feed-stamped DATA rows (types trade, create, complete, pool_create; hb/start/stop/pool_info/gap never count),
                         per program in --programs (default pumpswap,pumpfun), carried across hour boundaries and unclipped (a 150 s silence over a
                         boundary is 150 s in both hours); plus sampler (t_ms - feed.updated_ms)  (sampler part needs --samples)
A missing tape hour, a zero-size file, torn lines or both .jsonl and .jsonl.zst for one hour make the hour fail or be unmeasurable (excluded).
Only CLOSED hours are tabled: an hour is closed when its end + 300 s <= now (the feed compacts at end + 120 s); others are listed in the final line.
Each hour prints failed_labels {check: label} for failed and unmeasurable checks.
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
import time
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


TYPE_RE = re.compile(rb'"type":\s*"([a-z_]+)"')
PROG_RE = re.compile(rb'"prog":\s*"([a-z_]+)"')
# Feed-stamped DATA row types written by tools/arena_feed.py (_row(): trade, create, complete, pool_create). hb/start/stop/pool_info/gap never count as input.
DATA_TYPES = frozenset({b"trade", b"create", b"complete", b"pool_create"})
DEFAULT_PROGRAMS = ("pumpswap", "pumpfun")  # tools/arena_feed.py PROG_PROGRAM keys
CLOSE_GRACE_MS = 300_000  # feed compacts an hour 120 s after it ends; an hour is tabled only after end + 300 s
LABELS = ("price-path", "decision-input")


def scan_hour(path: Path) -> dict:
    """Counts only. gap rows by (kind, data_loss); data_loss gap spans (kind, from_ms, to_ms, closed); per data program first/last/longest-intra-hour
    silence of feed-stamped data rows (t_recv_ms). A torn or cut line is counted in `torn`."""
    guard(path)
    gaps: Counter = Counter()
    spans: list[tuple[str, int, int, bool]] = []
    bad_spans = 0
    prog: dict[str, list[int]] = {}  # prog -> [first, last, max_intra, intra_a, intra_b]
    torn = 0
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
                core = ln.strip()
                if core and (not ln.endswith(b"\n") or not core.startswith(b"{") or not core.endswith(b"}")):
                    torn += 1  # a torn tail or a cut line: the hour is unmeasurable, never silently clean
                    continue
                if b'"gap"' in ln:
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        torn += 1
                        continue
                    if isinstance(r, dict) and r.get("type") == "gap":
                        k = r.get("kind")
                        kind = k if isinstance(k, str) and KIND_OK.match(k) else "other"
                        dl = r.get("data_loss") is True
                        gaps[(kind, dl)] += 1
                        if dl:
                            f, t = r.get("from_ms"), r.get("to_ms")
                            if isinstance(f, int) and isinstance(t, int) and not isinstance(f, bool) and not isinstance(t, bool) and t >= f:
                                spans.append((kind, f, t, r.get("closed") is True))
                            else:
                                bad_spans += 1
                        continue
                tm = TYPE_RE.search(ln)
                if not tm or tm.group(1) not in DATA_TYPES:
                    continue
                pm, rm = PROG_RE.search(ln), RECV_RE.search(ln)
                if not pm or not rm:
                    continue
                t = int(rm.group(1))
                st = prog.setdefault(pm.group(1).decode(), [t, t, 0, 0, 0])
                if t > st[1]:
                    if t - st[1] > st[2]:
                        st[2], st[3], st[4] = t - st[1], st[1], t
                    st[1] = t
                if t < st[0]:
                    st[0] = t
        finally:
            stream.close()
        if proc is not None and proc.wait() != 0:
            err = "zstd failed"
    except OSError as e:
        err = type(e).__name__
    return {"gaps": gaps, "spans": spans, "bad_spans": bad_spans, "prog": prog, "torn": torn, "error": err}


def merge_spans(spans: list[tuple[str, int, int, bool]]) -> list[tuple[str, int, int]]:
    """Open and closed rows of one outage share (kind, from_ms): keep the closed one, else the open one. Duplicates collapse."""
    best: dict[tuple[str, int], tuple[int, bool]] = {}
    for kind, f, t, closed in spans:
        cur = best.get((kind, f))
        if cur is None or (closed and not cur[1]) or (closed == cur[1] and t > cur[0]):
            best[(kind, f)] = (t, closed)
    return sorted((k, f, t) for (k, f), (t, _) in best.items())


def silences(hours: dict[int, dict | None], programs: tuple[str, ...]) -> dict[str, list[tuple[int, int]] | None]:
    """Per program, every silence interval (a, b) of feed-stamped data rows, carried across hour boundaries. `hours` maps hour start -> scan (None =
    unknown hour). A run of consecutive known hours is one segment; its ends are clipped to the segment (an unknown neighbour hides what lies
    beyond). None for a program with no data row anywhere (its age is not measurable)."""
    segs: list[list[int]] = []
    for h in sorted(hours):
        if hours[h] is None:
            continue
        if segs and segs[-1][-1] + HOUR_MS == h:
            segs[-1].append(h)
        else:
            segs.append([h])
    out: dict[str, list[tuple[int, int]] | None] = {}
    for pg in programs:
        ivs: list[tuple[int, int]] = []
        seen = False
        for seg in segs:
            prev = seg[0]
            for h in seg:
                st = hours[h]["prog"].get(pg)  # type: ignore[index]
                if st is None:
                    continue
                seen = True
                ivs.append((prev, st[0]))
                if st[2]:
                    ivs.append((st[3], st[4]))
                prev = st[1]
            ivs.append((prev, seg[-1] + HOUR_MS))
        out[pg] = ivs if seen else None
    return out


def overlap_max(ivs: list[tuple[int, int]], h: int) -> int:
    return max([b - a for a, b in ivs if a < h + HOUR_MS and b > h] or [0])


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
    bad = len(inside) - len(ages)  # no feed time or negative age: the age is not measurable for the hour
    return {"n": len(inside), "max_gap_ms": max_gap, "max_age_ms": max(ages) if ages and not bad else None, "n_with_feed_time": len(ages), "bad_age_samples": bad}


def base_check(name: str) -> str:
    return name.split("[")[0]


def evaluate(tape_dir: Path, samples_path: Path | None, h0: int, h1: int, thr: dict, now_ms: int | None = None,
             programs: tuple[str, ...] = DEFAULT_PROGRAMS) -> tuple[list[dict], list[str]]:
    """Rows for the CLOSED hours of [h0, h1] and the list of hours not tabled because they are not closed (end + 300 s > now).
    `thr` is {check: {"value": number, "label": "price-path" | "decision-input"}}."""
    tape_dir = guard(tape_dir)
    if not tape_dir.is_dir():
        raise NotADirectoryError("tape dir missing or unreadable")
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    names = sorted(p.name for p in tape_dir.iterdir() if TAPE_NAME.match(p.name))
    samples, _ = load_samples(samples_path)
    closed = lambda h: h + HOUR_MS + CLOSE_GRACE_MS <= now_ms  # noqa: E731
    not_closed = [iso(h) for h in range(h0, h1 + HOUR_MS, HOUR_MS) if not closed(h)]
    scans: dict[int, dict | None] = {}
    meta: dict[int, dict] = {}
    for h in range(h0 - HOUR_MS, h1 + 2 * HOUR_MS, HOUR_MS):  # the tabled hours plus one neighbour each side, to carry silences and spans over boundaries
        stamp = hour_name(h)
        have = [n for n in (stamp, stamp + ".zst") if n in names]
        m: dict[str, Any] = {"file": None, "size": None, "failed": [], "unm": [], "sc": None}
        meta[h] = m
        scans[h] = None
        if not closed(h):
            continue
        if not have:
            m["failed"].append("tape_file_missing")
            continue
        m["file"] = have[0]
        try:
            m["size"] = (tape_dir / have[0]).stat().st_size
        except OSError:
            m["failed"].append("tape_file_unreadable")
            continue
        if len(have) > 1:
            m["unm"].append("tape_ambiguous_files")  # both .jsonl and .jsonl.zst present (compaction in progress or stuck): no silent pick
            continue
        if m["size"] == 0:
            m["failed"].append("tape_file_zero_size")
            continue
        sc = scan_hour(tape_dir / have[0])
        if sc.get("error"):
            m["failed"].append("tape_scan_error")
        elif sc["torn"]:
            m["unm"].append("tape_torn_lines")
            m["torn"] = sc["torn"]
        elif sc["bad_spans"]:
            m["unm"].append("gap_span_unreadable")
        else:
            m["sc"] = scans[h] = sc
    sil = silences(scans, programs)
    all_spans = merge_spans([sp for sc in scans.values() if sc for sp in sc["spans"]])
    rows = []
    for h in range(h0, h1 + HOUR_MS, HOUR_MS):
        if not closed(h):
            continue
        m = meta[h]
        sc = m["sc"]
        failed, unm = list(m["failed"]), list(m["unm"])
        row: dict[str, Any] = {"schema": SCHEMA, "hour": iso(h), "tape_file": m["file"], "tape_size": m["size"]}
        if "torn" in m:
            row["torn_lines"] = m["torn"]
        ov = [sp for sp in all_spans if sp[1] < h + HOUR_MS and sp[2] > h] if sc else []
        if sc:
            row["gap_rows"] = [{"kind": k, "data_loss": dl, "rows": n} for (k, dl), n in sorted(sc["gaps"].items())]
            row["data_loss_rows"] = len(ov)
            row["data_loss_max_span_s"] = max([(t - f) / 1000.0 for _, f, t in ov] or [0.0])
        else:
            row.update(gap_rows=[], data_loss_rows=None, data_loss_max_span_s=None)
        by_prog: dict[str, float | None] = {}
        for pg in programs:
            ivs = sil.get(pg)
            by_prog[pg] = None if (sc is None or ivs is None) else overlap_max(ivs, h) / 1000.0
        row["tape_input_age_by_prog_s"] = by_prog
        row["tape_input_age_max_s"] = None if sc is None or any(v is None for v in by_prog.values()) else max(by_prog.values())  # type: ignore[type-var]
        if samples is None:
            row.update(samples_n=None, sample_gap_max_s=None, sampler_input_age_max_s=None)
        else:
            ss = sample_stats(samples, h)
            row.update(samples_n=ss["n"], sample_gap_max_s=ss["max_gap_ms"] / 1000.0,
                       sampler_input_age_max_s=None if ss["max_age_ms"] is None else ss["max_age_ms"] / 1000.0)

        def limit(key: str) -> Any:
            return thr[key]["value"]

        def over(name: str, val: float | None, key: str) -> None:
            if val is None:
                unm.append(name)
            elif val > limit(key):
                failed.append(name)

        tape_ok = sc is not None
        if "max_gap_s" in thr:
            over("max_gap_s", row["data_loss_max_span_s"] if tape_ok else None, "max_gap_s")
        if "max_data_loss_count" in thr:
            over("max_data_loss_count", row["data_loss_rows"], "max_data_loss_count")
        if "min_samples_per_hour" in thr:
            if row["samples_n"] is None:
                unm.append("min_samples_per_hour")
            elif row["samples_n"] < limit("min_samples_per_hour"):
                failed.append("min_samples_per_hour")
        if "max_sample_gap_s" in thr:
            over("max_sample_gap_s", row["sample_gap_max_s"], "max_sample_gap_s")
        if "max_input_age_s" in thr:
            over("max_input_age_s[tape]", row["tape_input_age_max_s"], "max_input_age_s")
            if samples is not None:
                over("max_input_age_s[sampler]", row["sampler_input_age_max_s"], "max_input_age_s")
        # "unmeasurable" is treated as failing (Amendment 6 item 2.2): the hour is excluded, later evidence does not replace it
        row["unmeasurable"] = sorted(set(unm))
        row["failed_checks"] = sorted(set(failed))
        row["failed_labels"] = {base_check(n): thr[base_check(n)]["label"] for n in sorted(set(failed + unm)) if base_check(n) in thr}
        row["excluded"] = bool(failed or unm)
        rows.append(row)
    return rows, not_closed


def load_thresholds(path: Path) -> dict:
    thr = json.loads(guard(path).read_bytes())
    if not isinstance(thr, dict):
        raise ValueError("thresholds must be a JSON object")
    bad = [k for k in thr if k not in THRESHOLD_KEYS]
    if bad:
        raise ValueError(f"unknown threshold keys: {bad}")
    for k, v in thr.items():
        if not isinstance(v, dict) or set(v) != {"value", "label"}:
            raise ValueError(f"threshold {k} must be {{\"value\": number, \"label\": ...}}")
        if isinstance(v["value"], bool) or not isinstance(v["value"], (int, float)) or v["value"] < 0:
            raise ValueError(f"threshold {k} value must be a non-negative number")
        if v["label"] not in LABELS:
            raise ValueError(f"threshold {k} label must be one of {LABELS}")
    return thr


def render(rows: list[dict], thr_sha: str, not_closed: list[str] | None = None, programs: tuple[str, ...] = DEFAULT_PROGRAMS) -> list[str]:
    lines = [canon(r) for r in rows]
    body = "".join(ln + "\n" for ln in lines)
    final = {"type": "sha256", "schema": SCHEMA, "hours": len(rows), "excluded_hours": sum(1 for r in rows if r["excluded"]),
             "thresholds_sha256": thr_sha, "programs": list(programs), "not_closed_hours_not_tabled": not_closed or [], "sha256": hashlib.sha256(body.encode()).hexdigest()}
    return lines + [canon(final)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--samples")
    ap.add_argument("--from", dest="frm", required=True, help="first UTC hour, e.g. 2026-10-23T00")
    ap.add_argument("--to", required=True, help="last UTC hour (inclusive)")
    ap.add_argument("--thresholds", required=True, help='JSON {check: {"value": n, "label": "price-path"|"decision-input"}}')
    ap.add_argument("--programs", default=",".join(DEFAULT_PROGRAMS), help="comma list of feed programs the book reads (default pumpswap,pumpfun)")
    ap.add_argument("--now", help="UTC instant (YYYY-MM-DDTHH:MM:SSZ) used to decide which hours are closed; default the wall clock")
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
        programs = tuple(x for x in a.programs.split(",") if x)
        if not programs or not all(KIND_OK.match(x) for x in programs):
            raise ValueError("bad --programs")
        now_ms = None if not a.now else int(datetime.strptime(a.now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
        thr_sha = hashlib.sha256(thp.read_bytes()).hexdigest()
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    needs = [k for k in ("max_input_age_s", "min_samples_per_hour", "max_sample_gap_s") if k in thr]
    if needs and samples is None:
        print(f"error: thresholds {needs} need --samples", file=sys.stderr)
        return 2
    try:
        rows, nc = evaluate(tape, samples, h0, h1, thr, now_ms, programs)
        out = render(rows, thr_sha, nc, programs)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
