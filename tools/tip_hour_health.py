#!/usr/bin/env python3
"""Outcome-blind per-UTC-hour health of the arena TIP source (DEC-031 Amendment 23 items 5 and 6; Amendment 6 item 2; QP-RULING-1101-arena-tip-reader section 5;
QP-RULING-1102-tip-shared-slot R2/R3). Shared by every tip-fed arena book (HV7, s1t, HV4-T, r4-b-T hour table).

Subcommands
  table  per-hour rows for [--from, --to]: the seven FIXED Am.23 item 5 checks (hour EXCLUDED when one fails or cannot be measured) plus report-only fields and the
         QP-RULING-1102 R2 tripwires. Output: one canonical JSON line per TABLED hour, then {"type":"sha256",...} whose sha256 covers the hour lines (each + "\\n").
  bar    the Am.23 item 6 pre-clock feed bar for a clock instant and a book's T_max: over the 24 closed UTC hours before the clock, per program the book trades, the count of
         data_loss gap rows of that program or of prog `all` in the writer counts log; lambda = count / 86400 s; PASS iff 1 - exp(-lambda * T_max) <= 0.025 for every program
         (T_max above 0.6076 h = -24 h * ln 0.975 therefore needs ZERO rows). Exit 0 PASS, 1 FAIL, 3 INCOMPLETE (a window hour has no counts row, or pins differ).
  daily  the daily hour-table writer (Am.6 item 2.6): the table of one UTC date, written atomically to <out-dir>/tip-hour-table-<date>.jsonl with a .sha256 sidecar.
         A tabled hour is never re-decided: an existing file whose common hours differ is refused (exit 4); a file with fewer hours is replaced by the longer one.

Inputs (each named; nothing else is opened). NEVER a tip file, a derived-tape file, .writer/state.json, a ledger, bots.json, runner-status, a decision row or any price/fill:
  --counts FILE      the writer's per-closed-hour counts log (.writer/counts.jsonl, schema arena_tip_tape_writer_counts_v1)
  --silence FILE     optional .writer/silence.jsonl (silences that started in an already closed hour)
  --samples FILE...  the follower-state sampler jsonl (keys t, s_ts_ms, s_decoder_blobs_pinned, s_trade_event_v, s_gaps, ...); repeatable, merged and de-duplicated by t
  --events FILE      receiver .mirror/events.jsonl (kind closed = hour verified on the engine host) OR the pusher's .push/verified.json ({"hours": {hour: ms}})
  --arrival FILE     receiver .mirror/arrival.jsonl (transport B: newest PumpSwap t_recv_ms and its arrival instant per minute)
  --lag FILE         optional receiver .mirror/lag.jsonl (report-only transport lag)
  --pool-cache FILE  optional daily recorder jsonl (utc, pool_cache, backlog_jumps; "NRestarts=N" lines)
  --writer-status    optional writer_status.json relay or .writer/status.json (liveness header only)
  --thresholds FILE  canonical JSON {check: {"value": n, "label": "price-path"|"decision-input"}}
  --pins FILE|auto   the registered {adapter_sha256, reader_sha256, writer_sha256}; `auto` = the first counts row's pins (a change then fails the hour; labelled pins_source auto)

Measured vs. assumed (the counts log carries no data_loss flag on a gap span): every gap row of the tip source is data_loss true (Am.23 item 3), so every gap span counts as data
loss. A metric that cannot be computed FAILS the hour (it is excluded, never passed): Am.6 item 2.2. Hour h is TABLED only when h and h+1 are both closed (end + 300 s <= now),
because a gap row is filed under the hour of its write and a span that began in h can be filed in h+1; a later row never re-decides a tabled hour.
`max_input_age_s` is measured for program `pumpswap` only (the receiver's arrival log holds no other program): any other program in --programs is unmeasurable.
Stdlib only. Prints no mint, pool, wallet or signature (the inputs hold none).
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "tip_hour_health_v1"
SCHEMA_COUNTS = "arena_tip_tape_writer_counts_v1"
HOUR_MS = 3_600_000
CLOSE_GRACE_MS = 300_000  # an hour is closed when end + 300 s <= now (the writer closes an hour a 120 s grace past its end)
VERIFY_WITHIN_MS = 900_000  # Am.23 item 5 writer_hour_complete: verified on the engine host within 15 min after h ends
HB_MAX_GAP_MS = 60_000  # no gap > 60 s between writer heartbeats overlapping h
STATUS_MAX_AGE_MS = 30_000  # status_bad_samples: ts_ms older than 30 s at the sample instant
INPUT_AGE_OFFSET_MS = 2_000  # max_input_age_s is judged at second :02 of each minute of h
POOL_CACHE_CAP = 200_000  # QP-RULING-1102 F3: LRUPools cap
POOL_CACHE_ALERT = 180_000  # QP-RULING-1102 R2 (b): 90 percent of the cap
BAR_MAX = 0.025  # Am.23 item 6
BAR_WINDOW_H = 24
T_ZERO_H = -24.0 * math.log(1.0 - BAR_MAX)  # 0.6076 h: above this a book passes the bar only with ZERO rows
LABELS = ("price-path", "decision-input")
THRESHOLD_KEYS = ("writer_hour_complete", "max_loss_span_s", "max_data_silence_s", "status_bad_samples", "min_samples_per_hour", "max_sample_gap_s", "max_input_age_s")
DEFAULT_PROGRAMS = ("pumpswap",)
KIND_OK = re.compile(r"^[a-z0-9_]{1,60}$")
HOUR_KEY = re.compile(r"^\d{4}-\d\d-\d\dT\d\d$")
PIN_KEYS = ("adapter_sha256", "reader_sha256", "writer_sha256")
FORBIDDEN_WORDS = ("ledger", "decisions", "bots.json", "runner-status", "meta-capture", "tip-tape-archive", "/var/lib/mal/sealed", "fast-trades-tip", "/forward-", "state.json")
FORBIDDEN_NAME = re.compile(r"^(tape-.*\.jsonl(\.zst)?|(trades|creates|migrations|skipped-slots)-\d{4}-\d\d-\d\dT\d\d\.jsonl|gaps\.jsonl|state\.json)$", re.IGNORECASE)
# thresholds whose failure is a tape-level (structural) failure carry BOTH labels, whatever is pinned (as tools/arena_hour_health.py R2)
STRUCTURAL = frozenset({"counts_row_missing", "counts_duplicate_conflict", "counts_row_unreadable", "next_counts_row_missing"})


class Refused(Exception):
    pass


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hour_key(h_ms: int) -> str:
    return datetime.fromtimestamp(h_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def parse_hour(s: str) -> int:
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H"):
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000) // HOUR_MS * HOUR_MS
        except ValueError:
            pass
    raise ValueError(f"bad UTC hour: {s!r}")


def parse_instant(s: str) -> int:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H"):
        try:
            return int(datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            pass
    raise ValueError(f"bad UTC instant: {s!r}")


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def guard(path: str | os.PathLike) -> Path:
    """Refuse a path that names a tip file, a derived-tape file, .writer/state.json, a ledger, bots.json, runner-status or any sealed tree. Each input is a single named file."""
    s = os.path.abspath(str(path))
    for cand in (s, os.path.realpath(s)):
        low = cand.lower()
        if any(w in low for w in FORBIDDEN_WORDS) or FORBIDDEN_NAME.match(os.path.basename(cand)):
            raise Refused("path refused by policy (tip and derived-tape files, .writer/state.json, ledgers, decisions, bots.json and runner-status are never opened)")
    return Path(s)


def read_input(path: str | os.PathLike | None) -> bytes | None:
    """The bytes of one named input file (so the table can state the sha256 it read), or None when absent/unreadable (the dependent checks then fail the hour)."""
    if path is None:
        return None
    p = guard(path)
    try:
        if not p.is_file():
            return None
        return p.read_bytes()
    except OSError:
        return None


def _jsonl(data: bytes | None) -> tuple[list[dict], int]:
    """Valid object lines and the number of bad lines (torn tail, non-JSON, non-object)."""
    out: list[dict] = []
    bad = 0
    for ln in (data or b"").split(b"\n"):
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            bad += 1
            continue
        if isinstance(r, dict):
            out.append(r)
        else:
            bad += 1
    return out, bad


# ------------------------------------------------------------------------------------------------------------------------------ counts log
def load_counts(data: bytes | None) -> tuple[dict[int, dict], set[int], int]:
    """hour_ms -> counts row; the set of hours with two DIFFERENT rows (a conflict is unmeasurable); the number of bad lines. Identical duplicates collapse."""
    rows, bad = _jsonl(data)
    by: dict[int, dict] = {}
    conflict: set[int] = set()
    for r in rows:
        if r.get("schema") != SCHEMA_COUNTS or not isinstance(r.get("hour"), str) or not HOUR_KEY.match(r["hour"]):
            bad += 1
            continue
        h = parse_hour(r["hour"])
        if h in by and canon(by[h]) != canon(r):
            conflict.add(h)
        by.setdefault(h, r)
    return by, conflict, bad


def load_silences(data: bytes | None) -> list[tuple[str, int, int]]:
    out = []
    for r in _jsonl(data)[0]:
        p, f, t = r.get("prog"), _int(r.get("from_ms")), _int(r.get("to_ms"))
        if isinstance(p, str) and f is not None and t is not None and t >= f:
            out.append((p, f, t))
    return out


def gap_spans(rows: dict[int, dict]) -> tuple[list[tuple[str, str, int, int, int]], set[int], set[int]]:
    """Every gap span of the counts log as (kind, prog, from_ms, to_ms, filed_hour_ms), the hours whose spans list was truncated (spans_dropped > 0) and the hours with a
    span whose from_ms/to_ms is not an integer pair (unreadable)."""
    out: list[tuple[str, str, int, int, int]] = []
    truncated: set[int] = set()
    unreadable: set[int] = set()
    for h, r in rows.items():
        if (_int(r.get("gap_spans_dropped")) or 0) > 0:
            truncated.add(h)
        sp = r.get("gap_spans")
        if not isinstance(sp, list):
            unreadable.add(h)
            continue
        for s in sp:
            if not (isinstance(s, list) and len(s) >= 6):
                unreadable.add(h)
                continue
            kind, prog, f, t = s[0], s[1], _int(s[4]), _int(s[5])
            if not isinstance(kind, str) or not isinstance(prog, str) or f is None or t is None or t < f:
                unreadable.add(h)
                continue
            out.append((kind, prog, f, t, h))
    return out, truncated, unreadable


def overlaps(f: int, t: int, hs: int) -> bool:
    he = hs + HOUR_MS
    return f < he and (t > hs or (f == t and f >= hs))


def gap_rows_by_kind(row: dict) -> dict[str, int]:
    g = row.get("gap_rows")
    return {k: v for k, v in sorted(g.items()) if isinstance(k, str) and _int(v) is not None} if isinstance(g, dict) else {}


# ------------------------------------------------------------------------------------------------------------------------------ silences
def silence_chain(rows: dict[int, dict], prog: str) -> tuple[list[tuple[int, int]], set[int]]:
    """Silence intervals (a, b) of data rows of `prog` from consecutive counts rows, carried across hour boundaries, plus the hours whose incoming boundary is unknown
    (no earlier data row of the prog is known, or an hour row between is missing). A trailing silence is closed at the end of the last known row."""
    ivs: list[tuple[int, int]] = []
    unknown: set[int] = set()
    prev: int | None = None
    last_h: int | None = None
    for h in sorted(rows):
        if last_h is not None and h != last_h + HOUR_MS:
            prev = None  # a row is missing between: the boundary silence is not measurable
        r = rows[h]
        first = (r.get("data_t_first_ms") or {}).get(prog) if isinstance(r.get("data_t_first_ms"), dict) else None
        last = (r.get("data_t_last_ms") or {}).get(prog) if isinstance(r.get("data_t_last_ms"), dict) else None
        if prev is None:
            unknown.add(h)
        first, last = _int(first), _int(last)
        if first is not None and last is not None:
            if prev is not None:
                ivs.append((prev, first))
            prev = last
        last_h = h
    if prev is not None and last_h is not None:
        ivs.append((prev, last_h + HOUR_MS))
    return ivs, unknown


def silence_max_s(rows: dict[int, dict], extra: list[tuple[str, int, int]], prog: str, h: int, chain: tuple[list[tuple[int, int]], set[int]]) -> float | None:
    """The longest silence of data rows of `prog` that overlaps hour h, seconds, unclipped (None = not measurable)."""
    ivs, unknown = chain
    if h in unknown:
        return None
    best = 0
    for a, b in ivs:
        if a < h + HOUR_MS and b > h:
            best = max(best, b - a)
    for p, a, b in extra:
        if p == prog and a < h + HOUR_MS and b > h:
            best = max(best, b - a)
    r = rows.get(h) or {}
    for key in ("max_data_silence_s", "max_data_silence_ending_s"):
        d = r.get(key)
        if isinstance(d, dict) and isinstance(d.get(prog), (int, float)) and not isinstance(d.get(prog), bool):
            best = max(best, int(round(d[prog] * 1000)))
    return best / 1000.0


# ------------------------------------------------------------------------------------------------------------------------------ samples (follower state)
def load_samples(blobs: list[bytes | None]) -> tuple[list[dict], int]:
    """Valid sampler lines (an integer `t` in epoch seconds), merged over files, de-duplicated by t, sorted. Also the number of bad lines."""
    by: dict[int, dict] = {}
    bad = 0
    for b in blobs:
        rows, nb = _jsonl(b)
        bad += nb
        for r in rows:
            t = _int(r.get("t"))
            if t is None:
                bad += 1
                continue
            by.setdefault(t, r)
    return [by[t] for t in sorted(by)], bad


def bad_sample(r: dict) -> bool:
    """A follower-state sample that cannot show a healthy follower: decoder_blobs_pinned not true, trade_event_v not true, or ts_ms older than 30 s at the sample instant."""
    ts = _int(r.get("s_ts_ms"))
    return r.get("s_decoder_blobs_pinned") is not True or r.get("s_trade_event_v") is not True or ts is None or r["t"] * 1000 - ts > STATUS_MAX_AGE_MS


COUNTER_KEYS = ("s_gaps", "s_skipped", "s_backlog_jumps", "s_retries", "s_breaker_trips", "c_gaps", "c_skipped")


def sample_stats(samples: list[dict], h: int) -> dict:
    ts = [r["t"] * 1000 for r in samples]
    inside = [r for r in samples if h <= r["t"] * 1000 < h + HOUR_MS]
    lo = max([t for t in ts if t < h], default=None)
    hi = min([t for t in ts if t >= h + HOUR_MS], default=None)
    pts = [max(lo, h) if lo is not None else h] + [r["t"] * 1000 for r in inside] + [min(hi, h + HOUR_MS) if hi is not None else h + HOUR_MS]
    resets = 0
    seq = [r for r in samples if (h - HOUR_MS) <= r["t"] * 1000 < h + HOUR_MS]
    for a, b in zip(seq, seq[1:]):
        if not (h <= b["t"] * 1000 < h + HOUR_MS):
            continue
        for k in COUNTER_KEYS:
            va, vb = _int(a.get(k)), _int(b.get(k))
            if va is not None and vb is not None and vb < va:
                resets += 1
                break
    deltas = {}
    for k in ("s_gaps", "s_backlog_jumps", "s_unresolved_dropped"):
        vals = [_int(r.get(k)) for r in inside]
        vals = [v for v in vals if v is not None]
        deltas[k[2:]] = (max(vals) - min(vals)) if vals else None
    p90 = [r.get("s_block_lag_ms_p90") for r in inside if isinstance(r.get("s_block_lag_ms_p90"), (int, float)) and not isinstance(r.get("s_block_lag_ms_p90"), bool)]
    return {"n": len(inside), "max_gap_s": max(b - a for a, b in zip(pts, pts[1:])) / 1000.0, "bad": sum(1 for r in inside if bad_sample(r)),
            "counter_resets": resets, "counter_range": deltas, "block_lag_ms_p90_max": max(p90) if p90 else None}


# ------------------------------------------------------------------------------------------------------------------------------ receiver (transport B)
def load_verified(data: bytes | None) -> dict[int, int]:
    """hour_ms -> the instant the engine host verified the closed hour: receiver events.jsonl (first `closed` event per hour) or the pusher's verified.json {"hours": {...}}."""
    out: dict[int, int] = {}
    if not data:
        return out
    try:
        obj = json.loads(data)  # a whole-file JSON object with "hours" is verified.json; a multi-line jsonl does not parse as one document
    except ValueError:
        obj = None
    if isinstance(obj, dict) and isinstance(obj.get("hours"), dict):
        for k, v in obj["hours"].items():
            if isinstance(k, str) and HOUR_KEY.match(k) and _int(v) is not None:
                out[parse_hour(k)] = int(v)
        return out
    for r in _jsonl(data)[0]:
        h, ms = r.get("h"), _int(r.get("ms"))
        if r.get("kind") == "closed" and isinstance(h, str) and HOUR_KEY.match(h) and ms is not None:
            hs = parse_hour(h)
            out[hs] = min(out.get(hs, ms), ms)
    return out


class Arrival:
    """(arrival_ms, t_recv_ms) of the newest PumpSwap row per minute from the receiver's arrival log; age(instant) = instant - the newest t_recv_ms ARRIVED by that instant."""

    def __init__(self, data: bytes | None):
        pts = []
        for r in _jsonl(data)[0]:
            a, t = _int(r.get("newest_pumpswap_arrival_ms")), _int(r.get("newest_pumpswap_t_recv_ms"))
            if a is not None and t is not None:
                pts.append((a, t))
        pts.sort()
        self.arr = [a for a, _ in pts]
        self.pref: list[int] = []
        m = 0
        for _, t in pts:
            m = max(m, t)
            self.pref.append(m)

    def newest_at(self, instant: int) -> int | None:
        i = bisect.bisect_right(self.arr, instant)
        return self.pref[i - 1] if i else None

    def max_age_s(self, h: int) -> float | None:
        worst = 0
        for k in range(60):
            inst = h + k * 60_000 + INPUT_AGE_OFFSET_MS
            n = self.newest_at(inst)
            if n is None:
                return None
            worst = max(worst, inst - n)
        return worst / 1000.0


def lag_stats(data: bytes | None, h: int) -> dict | None:
    rows = [r for r in _jsonl(data)[0] if isinstance(r.get("minute"), str)]
    sel = []
    for r in rows:
        try:
            m = parse_instant(r["minute"].replace("Z", ":00Z"))
        except ValueError:
            continue
        if h <= m < h + HOUR_MS:
            sel.append(r)
    if not sel:
        return None
    p90 = [r["lag_new_p90_ms"] for r in sel if isinstance(r.get("lag_new_p90_ms"), (int, float))]
    p50 = sorted(r["lag_new_p50_ms"] for r in sel if isinstance(r.get("lag_new_p50_ms"), (int, float)))
    return {"minutes": len(sel), "lag_new_p50_ms_median": p50[len(p50) // 2] if p50 else None, "lag_new_p90_ms_max": max(p90) if p90 else None}


# ------------------------------------------------------------------------------------------------------------------------------ pool cache and restarts (QP-RULING-1102 R2)
def load_pool_cache(data: bytes | None) -> list[dict]:
    """The daily recorder's records [{utc_ms, pool_cache, nrestarts}] in file order: a JSON line with `utc`, then an optional `NRestarts=N` line that belongs to it."""
    out: list[dict] = []
    for ln in (data or b"").decode("utf-8", "replace").splitlines():
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict) and isinstance(r.get("utc"), str):
                try:
                    ms = parse_instant(r["utc"])
                except ValueError:
                    continue
                out.append({"utc_ms": ms, "pool_cache": _int(r.get("pool_cache")), "nrestarts": None})
        else:
            m = re.match(r"^NRestarts=(\d+)$", ln)
            if m and out:
                out[-1]["nrestarts"] = int(m.group(1))
    return out


def pool_cache_fields(recs: list[dict], h: int) -> dict:
    he = h + HOUR_MS
    prior = [r for r in recs if r["utc_ms"] <= he]
    if not prior:
        return {"pool_cache": None, "pool_cache_record": None, "tripwire_pool_cache_180k": None, "nrestarts": None, "nrestarts_delta": None}
    last = prior[-1]
    # the recorder runs once a day: the NRestarts change is attributed to the hour that holds the later record (day granularity, report-only)
    delta = None
    if h <= last["utc_ms"] < he and len(prior) > 1 and last["nrestarts"] is not None and prior[-2]["nrestarts"] is not None:
        delta = last["nrestarts"] - prior[-2]["nrestarts"]
    pc = last["pool_cache"]
    return {"pool_cache": pc, "pool_cache_record": iso(last["utc_ms"]), "tripwire_pool_cache_180k": None if pc is None else pc >= POOL_CACHE_ALERT,
            "nrestarts": last["nrestarts"], "nrestarts_delta": delta}


# ------------------------------------------------------------------------------------------------------------------------------ thresholds, pins
def load_thresholds(data: bytes) -> dict:
    thr = json.loads(data)
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


def load_pins(spec: str | None, rows: dict[int, dict]) -> tuple[dict | None, str]:
    """(expected pins, source). `auto` = the earliest counts row's pins."""
    if spec is None:
        return None, "none"
    if spec == "auto":
        for h in sorted(rows):
            p = rows[h].get("pins")
            if isinstance(p, dict) and all(isinstance(p.get(k), str) for k in PIN_KEYS):
                return {k: p[k] for k in PIN_KEYS}, "auto"
        return None, "auto"
    p = json.loads(guard(spec).read_bytes())
    if not isinstance(p, dict) or not all(isinstance(p.get(k), str) and re.fullmatch(r"[0-9a-f]{64}", p[k]) for k in PIN_KEYS):
        raise ValueError(f"pins file must hold {PIN_KEYS} as 64-hex strings")
    return {k: p[k] for k in PIN_KEYS}, "file"


def pins_ok(row: dict, expected: dict | None) -> bool | None:
    p = row.get("pins")
    if expected is None or not isinstance(p, dict):
        return None
    return all(p.get(k) == expected[k] for k in PIN_KEYS)


def restart_instants(rows: dict[int, dict]) -> list[int]:
    """Start instants of every writer run after the first (distinct run_id with its started_ms)."""
    runs: dict[str, int] = {}
    for h in sorted(rows):
        r = rows[h]
        rid, st = r.get("run_id"), _int(r.get("started_ms"))
        if isinstance(rid, str) and st is not None:
            runs[rid] = min(runs.get(rid, st), st)
    order = sorted(runs.values())
    return order[1:]


# ------------------------------------------------------------------------------------------------------------------------------ the table
def evaluate(*, counts: bytes | None, silence: bytes | None, samples: list[bytes | None], events: bytes | None, arrival: bytes | None, lag: bytes | None,
             pool_cache: bytes | None, thr: dict, h0: int, h1: int, now_ms: int, programs: tuple[str, ...], pins_spec: str | None) -> tuple[list[dict], dict]:
    """Rows of the TABLED hours of [h0, h1] and a summary (hours not tabled, input stats)."""
    rows, conflict, bad_lines = load_counts(counts)
    extra_sil = load_silences(silence)
    samp, bad_samp_lines = load_samples(samples)
    have_samples = any(b is not None for b in samples)
    verified = load_verified(events)
    arr = Arrival(arrival)
    have_arrival = bool(arr.arr)
    spans, truncated, unreadable = gap_spans(rows)
    expected, pins_source = load_pins(pins_spec, rows)
    restarts = restart_instants(rows)
    recs = load_pool_cache(pool_cache)
    chains = {p: silence_chain(rows, p) for p in programs}
    closed = lambda h: h + HOUR_MS + CLOSE_GRACE_MS <= now_ms  # noqa: E731
    tabled = lambda h: closed(h) and closed(h + HOUR_MS)  # noqa: E731
    not_tabled = [hour_key(h) for h in range(h0, h1 + HOUR_MS, HOUR_MS) if not tabled(h)]
    out: list[dict] = []
    for h in range(h0, h1 + HOUR_MS, HOUR_MS):
        if not tabled(h):
            continue
        r = rows.get(h)
        nxt = rows.get(h + HOUR_MS)
        failed: list[str] = []
        unm: list[str] = []
        why: list[str] = []  # writer_hour_complete sub-reasons
        row: dict[str, Any] = {"schema": SCHEMA, "hour": iso(h), "counts_row": r is not None}
        if h in conflict:
            unm.append("counts_duplicate_conflict")
            r = None
        elif r is None:
            failed.append("counts_row_missing")
        if r is not None and nxt is None and (h + HOUR_MS) not in conflict:
            unm.append("next_counts_row_missing")  # a span that began in h can be filed in h+1
        if r is not None and (h in unreadable or (h + HOUR_MS) in unreadable):
            unm.append("counts_row_unreadable")

        # --- writer_hour_complete
        if r is not None:
            p_ok = pins_ok(r, expected)
            if expected is None:
                why.append("pins_not_given")
            elif p_ok is False:
                why.append("pins_differ")
            runs = r.get("runs") if isinstance(r.get("runs"), list) else []
            restart_in_h = [x for x in restarts if h <= x < h + HOUR_MS]
            if len(runs) > 1 or restart_in_h:
                why.append("writer_restart_in_hour")
            hb = [g for g in (r.get("hb_gaps") or []) if isinstance(g, list) and len(g) >= 2 and _int(g[0]) is not None and _int(g[1]) is not None and g[0] < h + HOUR_MS and g[1] > h]
            hb_max = max([g[1] - g[0] for g in hb] or [0])
            if hb_max > HB_MAX_GAP_MS:
                why.append("heartbeat_gap_over_60s")
            v = verified.get(h)
            if v is None:
                why.append("hour_not_verified")
            elif v - (h + HOUR_MS) > VERIFY_WITHIN_MS:
                why.append("verified_after_15min")
            row.update(writer_run_ids=runs, writer_restarts_in_hour=len(restart_in_h), heartbeat_gap_max_s=hb_max / 1000.0, verified_after_hour_end_s=None if v is None else (v - h - HOUR_MS) / 1000.0,
                       pins_match=p_ok, counts_bytes=_int(r.get("bytes")), counts_lines=_int(r.get("lines")), hour_sha256=r.get("sha256"))
            row["writer_hour_reasons"] = sorted(set(why))
        else:
            row["writer_hour_reasons"] = ["no_counts_row"]

        # --- max_loss_span_s: gap rows of the book's programs or prog `all` that overlap h (filed in h or h+1; spans are data_loss by Am.23 item 3)
        ok_spans = r is not None and h not in truncated and (h + HOUR_MS) not in truncated and nxt is not None
        ov = [(k, p, f, t) for (k, p, f, t, fh) in spans if fh in (h, h + HOUR_MS) and (p in programs or p == "all") and overlaps(f, t, h)] if ok_spans else []
        row["data_loss_rows_overlapping"] = len(ov) if ok_spans else None
        row["max_loss_span_s"] = max([(t - f) / 1000.0 for _, _, f, t in ov] or [0.0]) if ok_spans else None
        if r is not None and (h in truncated or (h + HOUR_MS) in truncated):
            unm.append("gap_spans_truncated")
        # data_loss rows by kind|prog, filed in h (report-only; all programs)
        row["data_loss_rows_by_kind"] = gap_rows_by_kind(r) if r is not None else {}
        row["backlog_jump_rows"] = sum(n for k, n in row["data_loss_rows_by_kind"].items() if k.split("|")[0].endswith("backlog_jump"))
        row["writer_hole_rows"] = sum(n for k, n in row["data_loss_rows_by_kind"].items() if k.split("|")[0].endswith("writer_hole"))

        # --- max_data_silence_s per program
        sil: dict[str, float | None] = {}
        for p in programs:
            sil[p] = silence_max_s(rows, extra_sil, p, h, chains[p]) if r is not None else None
        row["data_silence_by_prog_s"] = sil
        row["max_data_silence_s"] = None if any(v is None for v in sil.values()) else max(sil.values())  # type: ignore[type-var]

        # --- samples
        if have_samples:
            ss = sample_stats(samp, h)
            row.update(samples_n=ss["n"], sample_gap_max_s=ss["max_gap_s"], status_bad_samples=ss["bad"] if ss["n"] else None,
                       follower_counter_resets=ss["counter_resets"], follower_counter_range=ss["counter_range"], block_lag_ms_p90_max=ss["block_lag_ms_p90_max"])
        else:
            row.update(samples_n=None, sample_gap_max_s=None, status_bad_samples=None, follower_counter_resets=None, follower_counter_range=None, block_lag_ms_p90_max=None)

        # --- input age (transport B, pumpswap only)
        age_by: dict[str, float | None] = {}
        for p in programs:
            age_by[p] = arr.max_age_s(h) if (p == "pumpswap" and have_arrival) else None
        row["input_age_by_prog_s"] = age_by
        row["max_input_age_s"] = None if any(v is None for v in age_by.values()) else max(age_by.values())  # type: ignore[type-var]

        # --- report-only
        if r is not None:
            tot = sum(n for n in (r.get("rows") or {}).values() if _int(n) is not None) if isinstance(r.get("rows"), dict) else None
            row.update(rows_total=tot, rows_per_min=None if tot is None else round(tot / 60.0, 3), no_tx_index=r.get("no_tx_index"), dropped_rows=r.get("dropped_rows"),
                       late_rows=r.get("late_rows"), skips=r.get("skips"), writer_status_failures=_int(r.get("status_bad_samples")))
            nt = r.get("no_tx_index")
            row["tripwire_mixed_group"] = (_int(nt.get("pool_slots_mixed")) or 0) > 0 if isinstance(nt, dict) and _int(nt.get("pool_slots_mixed")) is not None else None
        else:
            row.update(rows_total=None, rows_per_min=None, no_tx_index=None, dropped_rows=None, late_rows=None, skips=None, writer_status_failures=None, tripwire_mixed_group=None)
        row["transport_lag"] = lag_stats(lag, h)
        row.update(pool_cache_fields(recs, h))
        row["tripwire_follower_restart"] = bool((row.get("follower_counter_resets") or 0) > 0 or (row.get("nrestarts_delta") or 0) > 0 or row.get("writer_restarts_in_hour"))

        # --- the fixed checks
        def over(name: str, val: float | None) -> None:
            if name not in thr:
                return
            if val is None:
                unm.append(name)
            elif val > thr[name]["value"]:
                failed.append(name)

        if "writer_hour_complete" in thr:
            if r is None:
                unm.append("writer_hour_complete")
            elif row["writer_hour_reasons"]:
                failed.append("writer_hour_complete")
        over("max_loss_span_s", row["max_loss_span_s"])
        over("max_data_silence_s", row["max_data_silence_s"])
        over("status_bad_samples", row["status_bad_samples"])
        if "min_samples_per_hour" in thr:
            if row["samples_n"] is None:
                unm.append("min_samples_per_hour")
            elif row["samples_n"] < thr["min_samples_per_hour"]["value"]:
                failed.append("min_samples_per_hour")
        over("max_sample_gap_s", row["sample_gap_max_s"])
        over("max_input_age_s", row["max_input_age_s"])
        struct = sorted(set(failed + unm) & STRUCTURAL)
        row["unmeasurable"] = sorted(set(unm))
        row["failed_checks"] = sorted(set(failed))
        row["structural_failures"] = struct
        row["labels_failing"] = sorted(set(LABELS if struct else ()) | {thr[n]["label"] for n in set(failed + unm) if n in thr})
        row["failed_labels"] = {n: thr[n]["label"] for n in sorted(set(failed + unm)) if n in thr}
        row["excluded"] = bool(failed or unm)
        out.append(row)
    summary = {"not_tabled_hours": not_tabled, "counts_rows": len(rows), "counts_bad_lines": bad_lines, "sample_rows": len(samp), "sample_bad_lines": bad_samp_lines, "verified_hours": len(verified),
               "arrival_rows": len(arr.arr), "pins_source": pins_source, "pins": expected, "writer_restart_instants": [iso(x) for x in restarts]}
    return out, summary


def sha(b: bytes | None) -> dict | None:
    return None if b is None else {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}


def render(rows: list[dict], thr_sha: str, summary: dict, programs: tuple[str, ...], inputs: dict) -> list[str]:
    lines = [canon(r) for r in rows]
    body = "".join(ln + "\n" for ln in lines)
    final = {"type": "sha256", "schema": SCHEMA, "hours": len(rows), "excluded_hours": sum(1 for r in rows if r["excluded"]), "thresholds_sha256": thr_sha, "programs": list(programs),
             "inputs": inputs, **summary, "sha256": hashlib.sha256(body.encode()).hexdigest()}
    return lines + [canon(final)]


# ------------------------------------------------------------------------------------------------------------------------------ the pre-clock bar (Am.23 item 6)
def bar(counts: bytes | None, clock_ms: int, t_max_s: float, programs: tuple[str, ...], pins_spec: str | None) -> dict:
    """Over the 24 closed UTC hours before the clock, per program: count of gap rows of that program or prog `all` in the counts log (rows are filed by write hour, as the
    log holds them), lambda = count / 86400 s, p = 1 - exp(-lambda * T_max). PASS iff every program has p <= 0.025 and the writer was at the registered pins for all 24 hours."""
    rows, conflict, bad = load_counts(counts)
    h1 = clock_ms // HOUR_MS * HOUR_MS
    if h1 != clock_ms:
        raise ValueError("the clock must be a whole UTC hour")
    hours = [h1 - (BAR_WINDOW_H - i) * HOUR_MS for i in range(BAR_WINDOW_H)]
    missing = [hour_key(h) for h in hours if h not in rows]
    expected, pins_source = load_pins(pins_spec, rows)
    pins_bad = [hour_key(h) for h in hours if h in rows and expected is not None and pins_ok(rows[h], expected) is not True]
    restarts = [iso(x) for x in restart_instants(rows) if hours[0] <= x < h1]
    per: dict[str, dict] = {}
    t_h = t_max_s / 3600.0
    for p in programs:
        cnt, by_kind = 0, {}
        for h in hours:
            for k, n in gap_rows_by_kind(rows.get(h, {})).items():
                kind, prog = (k.split("|") + [""])[:2]
                if prog in (p, "all"):
                    cnt += n
                    by_kind[k] = by_kind.get(k, 0) + n
        lam = cnt / (BAR_WINDOW_H * 3600.0)
        pl = 1.0 - math.exp(-lam * t_max_s)
        per[p] = {"rows": cnt, "by_kind": dict(sorted(by_kind.items())), "lambda_per_s": lam, "p_loss": pl, "pass": pl <= BAR_MAX}
    incomplete = bool(missing or conflict & set(hours) or expected is None)
    ok = all(v["pass"] for v in per.values()) and not pins_bad
    verdict = "INCOMPLETE" if incomplete else ("PASS" if ok else "FAIL")
    return {"type": "bar", "schema": SCHEMA, "window_from": iso(hours[0]), "window_to": iso(h1), "clock": iso(clock_ms), "t_max_s": t_max_s, "t_max_h": t_h,
            "t_zero_h": T_ZERO_H, "zero_rows_required": t_h > T_ZERO_H, "programs": per, "hours_missing": missing, "pins_source": pins_source, "hours_pins_differ_or_unchecked": pins_bad,
            "writer_restarts_in_window": restarts, "counts_bad_lines": bad, "verdict": verdict}


# ------------------------------------------------------------------------------------------------------------------------------ daily writer
def write_daily(out_dir: Path, date: str, lines: list[str]) -> tuple[int, dict]:
    """Write tip-hour-table-<date>.jsonl atomically (+ .sha256). A tabled hour is never re-decided: the common hours of an existing file must be byte-identical."""
    name = f"tip-hour-table-{date}.jsonl"
    path = out_dir / name
    new_hours = {json.loads(ln)["hour"]: ln for ln in lines[:-1]}
    info: dict[str, Any] = {"type": "daily", "date": date, "path": str(path), "hours_tabled": len(new_hours)}
    if path.exists():
        old = [ln for ln in path.read_text().splitlines() if ln.strip()]
        old_hours = {json.loads(ln)["hour"]: ln for ln in old if '"type":"sha256"' not in ln}
        diff = sorted(h for h in old_hours if h in new_hours and old_hours[h] != new_hours[h])
        lost = sorted(h for h in old_hours if h not in new_hours)
        if diff or lost:
            info.update(status="REFUSED_tabled_hour_would_change", changed_hours=diff, missing_hours=lost)
            return 4, info
        if len(old_hours) == len(new_hours):
            info.update(status="unchanged", sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            return 0, info
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f".{name}.tmp{os.getpid()}"
    data = ("\n".join(lines) + "\n").encode()
    tmp.write_bytes(data)
    os.replace(tmp, path)
    digest = hashlib.sha256(data).hexdigest()
    (out_dir / (name + ".sha256")).write_text(f"{digest}  {name}\n")
    info.update(status="written", sha256=digest, excluded_hours=json.loads(lines[-1])["excluded_hours"])
    return 0, info


# ------------------------------------------------------------------------------------------------------------------------------ CLI
def _add_inputs(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--counts", required=True)
    ap.add_argument("--silence")
    ap.add_argument("--samples", nargs="*", default=[])
    ap.add_argument("--events")
    ap.add_argument("--arrival")
    ap.add_argument("--lag")
    ap.add_argument("--pool-cache")
    ap.add_argument("--thresholds", required=True)
    ap.add_argument("--pins", default=None, help="path to the registered pins JSON, or `auto`")
    ap.add_argument("--programs", default=",".join(DEFAULT_PROGRAMS), help="comma list of programs the book trades (default pumpswap)")
    ap.add_argument("--now", help="UTC instant YYYY-MM-DDTHH:MM:SSZ deciding which hours are closed (default the wall clock)")


def _programs(s: str) -> tuple[str, ...]:
    ps = tuple(x for x in s.split(",") if x)
    if not ps or not all(KIND_OK.match(x) for x in ps):
        raise ValueError("bad --programs")
    return ps


def _run_table(a: argparse.Namespace, h0: int, h1: int) -> tuple[list[str], list[dict]]:
    thr_b = read_input(a.thresholds)
    if thr_b is None:
        raise ValueError("thresholds file unreadable")
    thr = load_thresholds(thr_b)
    programs = _programs(a.programs)
    now_ms = int(time.time() * 1000) if not a.now else parse_instant(a.now)
    blobs = {"counts": read_input(a.counts), "silence": read_input(a.silence), "events": read_input(a.events), "arrival": read_input(a.arrival), "lag": read_input(a.lag),
             "pool_cache": read_input(a.pool_cache)}
    samples = [read_input(p) for p in a.samples]
    rows, summary = evaluate(counts=blobs["counts"], silence=blobs["silence"], samples=samples, events=blobs["events"], arrival=blobs["arrival"], lag=blobs["lag"],
                             pool_cache=blobs["pool_cache"], thr=thr, h0=h0, h1=h1, now_ms=now_ms, programs=programs, pins_spec=a.pins)
    inputs = {k: sha(v) for k, v in blobs.items()}
    inputs["samples"] = [sha(b) for b in samples]
    return render(rows, hashlib.sha256(thr_b).hexdigest(), summary, programs, inputs), rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("table")
    _add_inputs(t)
    t.add_argument("--from", dest="frm", required=True)
    t.add_argument("--to", required=True)
    d = sub.add_parser("daily")
    _add_inputs(d)
    d.add_argument("--date", required=True, help="UTC date YYYY-MM-DD")
    d.add_argument("--out-dir", required=True)
    b = sub.add_parser("bar")
    b.add_argument("--counts", required=True)
    b.add_argument("--clock", required=True, help="the book's clock instant, a whole UTC hour")
    b.add_argument("--t-max-s", type=float, required=True, help="the book's registered time-stop or maximum hold, seconds")
    b.add_argument("--programs", default=",".join(DEFAULT_PROGRAMS))
    b.add_argument("--pins", default=None)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "bar":
            res = bar(read_input(a.counts), parse_instant(a.clock), a.t_max_s, _programs(a.programs), a.pins)
            print(canon(res))
            return {"PASS": 0, "FAIL": 1, "INCOMPLETE": 3}[res["verdict"]]
        if a.cmd == "table":
            h0, h1 = parse_hour(a.frm), parse_hour(a.to)
            if h1 < h0:
                raise ValueError("--to before --from")
            lines, _ = _run_table(a, h0, h1)
            print("\n".join(lines))
            return 0
        day = parse_hour(a.date + "T00")
        lines, _ = _run_table(a, day, day + 23 * HOUR_MS)
        rc, info = write_daily(Path(a.out_dir), a.date, lines)
        print(canon(info))
        return rc
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
