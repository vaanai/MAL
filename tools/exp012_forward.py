#!/usr/bin/env python3
"""EXP-012 FORWARD scorer (DEC-016): the frozen model on sealed forward getBlock hours.

    python3 -m tools.exp012_forward verify --walk-dir D --hour H
    python3 -m tools.exp012_forward score  --walk-dir D --out-dir OUT [--clean-clock ISO] [--to HOUR] [--pool-start HOUR]
    python3 -m tools.exp012_forward report --out-dir OUT --walk-dir D [--clean-clock ISO] [--read-end ISO] [--reprint]

This is NOT a one-shot read and writes no HOLDOUT lock. It runs the code path
`tools/exp012_score.py` used for the read, by import and not by copy:
  - the table build is `exp012_score.load_rows` (same `chunk_plan`, same
    `run_worker_features`, same creator history builder), with the same fixed
    settings read from `exp012_score` at call time (BUFFER_HOURS=24,
    MAX_HOME_HOURS=12, MAX_WORKERS=2);
  - the model, threshold and feature names come from `exp011_score.load_frozen_spec`
    on ARTIFACTS/exp012, and rows are scored by `exp011_score.score_rows`;
  - the gate is `exp011_score.compute_gate` (both fail models).
Only the hour source differs: one raw walker directory instead of three
deduplicated copies (see "Where the forward path differs from the read").

One pre-registered read (DEC-016 Amendment 1): clean clock 2026-10-06T00:00:00Z, read end
2026-10-16T00:00:00Z (exclusive). These are constants. A different clock or end is refused unless
`--test-window` is given; everything produced under it carries `test_window: true` (runs.jsonl,
report, lock, ledger) and a FINAL says "TEST WINDOW, NOT THE PRE-REGISTERED READ". `report`
refuses unless every score run in runs.jsonl has the report's clean clock, read end and
test_window flag, every row's mig_ms is in [clean clock, read end), and rows.jsonl hashes to the
last run's recorded rows_sha256. `score` counts migrations in [clean clock, min(to, read end)).
`report` is INTERIM (rows, entered count, per-day entered counts; no mean, CI, SOL, day sign or
verdict in any output) unless every hour in [pool start, read end + 1 h) is sealed and verified
AND a score run reached `to >= read end + 1 h` (the 30-minute exit cap needs the next hour).
Then it is FINAL: the gate plus report-only context, and `final_read.lock` is written once. A second
FINAL report refuses; `--reprint` re-renders only if rows.jsonl still hashes to the lock's sha256.
`score` refuses once the lock exists.
FINAL order: lock (fsync) first, then a FINAL marker (rows sha256, lock sha256, time) appended to
runs.jsonl and to the append-only ledger `--final-ledger` (default /data/mal/exp012-forward/FINAL_READS.jsonl),
and only then the verdict is printed and the report files written. `score` and `report` refuse if a
FINAL marker for the window exists in runs.jsonl or the ledger and this out-dir's lock is missing or
differs; a fresh out-dir refuses if the ledger holds a non-test FINAL for the window.
Files that hold P&L at rest: OUT/rows.jsonl; OUT/scratch/poolHOLDOUT12-w*.jsonl (the table
build's per-worker row files, rewritten every score run, with each row's flat and press nets and
features); OUT/report.json and OUT/report.md once FINAL. INTERIM report files, runs.jsonl,
the ledger and stderr carry none. The conflict refusal names fields, never values. 

Inputs and refusals (exit 2, each reason on stderr, before any row is read):
  - `check_frozen(artifact_dir)` must return no error (FROZEN.md5 md5s, model.md5,
    features, proceed screen, provenance at --freeze-commit);
  - every hour in [pool start, to) must be `sealed` in D/checkpoint.json AND have
    an OK line in D/verify.jsonl. The hour is named. Pool start defaults to the
    clean clock's hour minus 2 x BUFFER_HOURS (48 h): a counted mint can be created
    up to 24 h before the clock, and its `creator_prior_mints_24h` needs 24 h of
    creates before that. `--pool-start` overrides it.
    An OK line is the `hours[]` entry of `backfill_verify --content` for [H, H+1)
    plus a `sha256` map {sub: hex} of each sealed file, with `issues == []`, a
    `content` map, `duplicates == 0` for every file, and a trades sha256. The
    sha256 is re-checked against the file bytes on every score, for trades, creates
    and migrations alike; a file on disk that is not in the line is refused, and the
    line must carry trades and creates (the read's `check_creates_presence` rule). The last line for
    an hour wins. The `verify` subcommand writes exactly this line (idempotent:
    nothing is appended if the hour's last line is identical);
  - a rows.jsonl written under a different clean clock.
`--to` defaults to the end of the longest sealed-and-verified run that starts at
the buffer start. It is exclusive and hour-aligned.

Rows count only if the migration time is >= the clean clock and < `--to`. They
are appended to OUT/rows.jsonl keyed by (mint, mig_ms); a row already there is
never rewritten. If a recomputed row for an existing key differs from the stored
one the run stops (exit 3) and appends nothing. A re-run over the same hours
leaves rows.jsonl byte-identical. OUT/runs.jsonl logs each run (audit only).

Where the forward path differs from the read (disclosed, not hidden):
  1. Raw walker files, not the `--dedupe-out` copy. Verification requires zero
     duplicates, in which case the deduplicated copy is the same rows, so the
     read bytes are equal in content. Hours with duplicates are refused, not deduped.
  2. The hour list is [pool start, to), growing with `--to`, not a fixed 144 hours.
     The chunk plan is anchored at the pool start: fixed home chunks of
     MAX_HOME_HOURS (12) hours, each with the next BUFFER_HOURS (24) pool hours as
     buffer, only the last chunk partial. The read's `chunk_plan` splits N hours
     into ceil(N/12) near-equal chunks, which is the same plan when N is a multiple
     of 12 (the read's 144) and differs otherwise. A chunk's buffer is cut by `to`
     until `to` is 24 h past the chunk's end; mints created in a chunk with a cut
     buffer and migrating later are decided in a later run. Creator history starts
     at the pool start, so a mint created in the first 24 h of the pool has a
     short `creator_prior_mints_24h` window; with the 48 h default no counted mint
     is created that early (a counted mint is created at most 24 h before the clock).
  3. A migration needs its exit deadline inside the tape (`exploration_exits`
     censors it otherwise). Rows near `--to` therefore appear in a later run.
     What the code guarantees: a row is never rewritten, and a later run that
     computes a different value for a stored (mint, mig_ms) stops with exit 4 and
     appends nothing. It does NOT guarantee that a row decided close to `--to` is
     what a longer tape would give. In particular, a mint whose entry state or
     exit needs prints after `--to` can be scored as a MISS (no fill) or by a cap
     exit on the truncated tape; the stored row then differs from a later
     recomputation and the guard stops the run instead of correcting it. Treat
     rows within about 32 minutes of `--to` (the exit cap plus the watch window)
     as the ones at risk, and prefer to run `score` with `--to` well past them.
  4. The read's pre-lock repo-state check (tools/ unchanged since the freeze) is not
     applied: this module is new code under tools/ by construction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import tools.exp011_score as e11
import tools.exploration_entry_model as eem
import tools.exp012_score as s12
from tools.exp011_freeze import FROZEN_MANIFEST_NAME, _git_commit, _md5_of_file
import tools.backfill_verify as bv
from tools.latency_curve import _hour_file

SCHEMA_ROW = "exp012_forward_row_v1"
SCHEMA_REPORT = "exp012_forward_report_v1"
LABEL = "forward simulated paper, not money made"
PINNED_CLEAN_CLOCK = "2026-10-06T00:00:00Z"  # DEC-016 Amendment 1
PINNED_READ_END = "2026-10-16T00:00:00Z"  # exclusive: 10 full UTC days
DEFAULT_CLEAN_CLOCK = PINNED_CLEAN_CLOCK
DEFAULT_READ_END = PINNED_READ_END
DEFAULT_LEDGER = Path("/data/mal/exp012-forward/FINAL_READS.jsonl")
TEST_WINDOW_BANNER = "TEST WINDOW, NOT THE PRE-REGISTERED READ"
SCHEMA_MARKER = "exp012_forward_final_marker_v1"
LOCK_NAME = "final_read.lock"
SCHEMA_LOCK = "exp012_forward_final_read_lock_v1"
DEFAULT_FREEZE_COMMIT = "ea5ec374010378b14a5c19e81bf045679bde73b9"  # EXP-012 section 12 (Part 2)
ROWS_NAME = "rows.jsonl"
RUNS_NAME = "runs.jsonl"
VERIFY_NAME = "verify.jsonl"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_DIR = REPO_ROOT / "ARTIFACTS" / "exp012"
LAMPORTS = 1_000_000_000
HOUR_FMT = "%Y-%m-%dT%H"


class Refused(Exception):
    code = 2

    def __init__(self, reasons: Sequence[str]):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


class Conflict(Refused):
    """A stored row would change. Exit 4, nothing appended."""

    code = 4


# --- time helpers -----------------------------------------------------------------


def parse_clock(text: str) -> datetime:
    t = text.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"bad time {text!r}; use YYYY-MM-DDTHH:MM:SSZ")


def hour_key(dt: datetime) -> str:
    return dt.strftime(HOUR_FMT)


def hour_dt(key: str) -> datetime:
    return datetime.strptime(key, HOUR_FMT).replace(tzinfo=timezone.utc)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def default_pool_start(clean_clock: datetime) -> datetime:
    """Clean clock hour minus 2 x BUFFER_HOURS (48 h): 24 h for the oldest counted mint's
    creation, and 24 h of creator history before that."""
    floor = clean_clock.replace(minute=0, second=0, microsecond=0)
    return floor - timedelta(hours=2 * s12.BUFFER_HOURS)


def pool_warnings(clean_clock: datetime, start: datetime) -> list[str]:
    clock_hour = clean_clock.replace(minute=0, second=0, microsecond=0)
    back = int((clock_hour - start).total_seconds() // 3600)
    out: list[str] = []
    if back != 2 * s12.BUFFER_HOURS:
        out.append(f"pool start {hour_key(start)} is {back} h before the clean clock hour, not the {2 * s12.BUFFER_HOURS} h default: a counted mint may have a short creator-history window")
    if back % 12:
        out.append(f"pool start {hour_key(start)} is not aligned to the 12 h chunk grid of the clean clock hour: chunk boundaries differ from the default plan")
    return out


def anchored_plan(pool: Sequence[str], home_hours: int | None, buffer_hours: int) -> list[tuple[int, list[str], list[str]]]:
    """Chunk plan anchored at pool[0]: fixed home chunks of `home_hours`, last one partial; each
    chunk's buffer is the next `buffer_hours` pool hours. Chunks covering earlier hours do not
    depend on the pool's end. `home_hours` None means one chunk (test settings only)."""
    pool = list(pool)
    step = len(pool) if not home_hours or home_hours <= 0 else home_hours
    plan = []
    for i, a in enumerate(range(0, len(pool), step)):
        home = pool[a : a + step]
        b = a + len(home)
        plan.append((i, home, pool[b : b + buffer_hours]))
    return plan


# --- sealed + verified hours -------------------------------------------------------


def _sealed_hours(walk_dir: Path) -> set[str]:
    cp = walk_dir / "checkpoint.json"
    if not cp.is_file():
        return set()
    try:
        hours = json.loads(cp.read_text(encoding="utf-8")).get("hours")
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(hours, dict):
        return set()
    return {h for h, e in hours.items() if isinstance(e, dict) and e.get("status") == "sealed"}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _line_ok(rec: dict[str, Any]) -> bool:
    if rec.get("issues") != [] or not isinstance(rec.get("content"), dict):
        return False
    sha = rec.get("sha256")
    if not isinstance(sha, dict) or not all(isinstance(sha.get(sub), str) for sub in ("trades", "creates")):
        return False
    for stats in rec["content"].values():
        if not isinstance(stats, dict) or stats.get("duplicates") != 0:
            return False
    return True


def verified_hours(walk_dir: Path) -> dict[str, dict[str, Any]]:
    """{hour: last verify line} for hours whose LAST line is OK."""
    path = walk_dir / VERIFY_NAME
    last: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and isinstance(rec.get("hour"), str):
            last[rec["hour"]] = rec
    return {h: r for h, r in last.items() if _line_ok(r)}


def hour_problems(walk_dir: Path, hours: Sequence[str]) -> list[str]:
    sealed, ok = _sealed_hours(walk_dir), verified_hours(walk_dir)
    out: list[str] = []
    for h in hours:
        if h not in sealed:
            out.append(f"hour {h} is not sealed in {walk_dir / 'checkpoint.json'}")
        elif h not in ok:
            out.append(f"hour {h} has no OK line in {walk_dir / VERIFY_NAME} (backfill_verify --content: 0 issues, 0 duplicates)")
        elif _hour_file(walk_dir / "trades", "trades", h) is None:
            out.append(f"hour {h} is sealed and verified but has no trades file under {walk_dir / 'trades'}")
        elif _hour_file(walk_dir / "creates", "creates", h) is None:
            out.append(f"hour {h} is sealed and verified but has no creates file under {walk_dir / 'creates'}")
        else:
            want = ok[h]["sha256"]
            for sub in ("trades", "creates", "migrations"):
                f = _hour_file(walk_dir / sub, sub, h)
                if f is None:
                    if sub in want:
                        out.append(f"hour {h}: {sub} file is gone but {VERIFY_NAME} has its sha256")
                elif sub not in want:
                    out.append(f"hour {h}: {sub} file is present but not in the {VERIFY_NAME} line")
                elif _sha256_file(f) != want[sub]:
                    out.append(f"hour {h}: {sub} bytes do not match the sha256 in {VERIFY_NAME}")
    return out


def verify_line(walk_dir: Path, hour: str) -> dict[str, Any]:
    """`tools.backfill_verify` with --content for [hour, hour+1), plus the sha256 of each sealed file."""
    nxt = hour_key(hour_dt(hour) + timedelta(hours=1))
    report = bv.build_report(walk_dir, hour, nxt, content=True, dedupe_out=None, min_slots_per_hour=9_000, max_slots_per_hour=14_000)
    rec = report["hours"][0]
    if rec["files"].get("trades") is None and "sealed_with_no_trades_file" not in rec["issues"]:
        rec["issues"].append("no_trades_file")
    if rec["files"].get("creates") is None:
        rec["issues"].append("no_creates_file")
    sha: dict[str, str] = {}
    for sub, path in sorted(rec["files"].items()):
        if path is None:
            continue
        if rec["sealed"].get(sub):
            sha[sub] = _sha256_file(Path(path))
        else:
            rec["issues"].append(f"{sub}: not sealed to .zst")
    rec["sha256"] = sha
    return rec


def run_verify(walk_dir: Path, hour: str) -> tuple[dict[str, Any], bool]:
    """(line, appended). Appends to D/verify.jsonl unless the hour's last line is identical."""
    rec = verify_line(walk_dir, hour)
    path = walk_dir / VERIFY_NAME
    text = json.dumps(rec, sort_keys=True)
    last = None
    if path.is_file():
        for x in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(x)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and r.get("hour") == hour:
                last = x.strip()
    if last == text:
        return rec, False
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return rec, True


def default_to(walk_dir: Path, start: datetime) -> datetime:
    """End (exclusive) of the longest sealed+verified run beginning at `start`."""
    sealed, ok = _sealed_hours(walk_dir), verified_hours(walk_dir)
    cur = start
    while hour_key(cur) in sealed and hour_key(cur) in ok:
        cur += timedelta(hours=1)
    return cur


# --- hour resolver + tagged worker (picklable, module level) --------------------------


@dataclass(frozen=True)
class ForwardHours:
    root: str
    allowed: frozenset[str]

    def __call__(self, key: str) -> dict[str, Any]:
        assert key in self.allowed, f"hour {key!r} is outside the sealed+verified forward range"
        root = Path(self.root)
        trade = _hour_file(root / "trades", "trades", key)
        if trade is None:
            raise SystemExit(f"missing trades file for verified hour {key} under {root}")
        create = _hour_file(root / "creates", "creates", key)
        return {"hour": key, "day": key[:10], "end": int(hour_dt(key).timestamp()) + 3600, "trade": trade, "create": create}


def _tagged_worker(worker_id: int, home: list[str], buf: list[str], creator_hist: dict[str, list[int]], rows_out_path: Path | None, hours: ForwardHours) -> list[dict[str, Any]]:
    """`exp012_score._run_worker` with one addition: each scored row also carries the
    migration time (`mig_ms`), which `score_one` has but does not put in the row.
    Nothing else about a row changes. `score_one` is wrapped for the duration of the call only."""
    orig = eem.score_one

    def tagged(mint_id: str, mint: Any, *a: Any, **k: Any) -> list[dict[str, Any]]:
        rows = orig(mint_id, mint, *a, **k)
        for r in rows:
            r["mig_ms"] = int(mint.mig_ms)
        return rows

    eem.score_one = tagged
    try:
        return s12._run_worker(worker_id, home, buf, creator_hist, rows_out_path, hours)
    finally:
        eem.score_one = orig


# --- scoring ----------------------------------------------------------------------------


def score_hours(walk_dir: Path, pool: Sequence[str], artifact_dir: Path, scratch: Path) -> tuple[list[dict[str, Any]], float]:
    """Rows of the read's pipeline on `pool`, scored by the frozen model. Every row has mig_ms and score."""
    model, threshold, names = e11.load_frozen_spec(artifact_dir)
    hours = ForwardHours(str(walk_dir), frozenset(pool))
    plan = anchored_plan(pool, s12.MAX_HOME_HOURS, s12.BUFFER_HOURS)
    rows = s12.load_rows(hours, s12.MAX_WORKERS, s12.BUFFER_HOURS, s12.MAX_HOME_HOURS, scratch, pool_hours=list(pool), worker_fn=_tagged_worker, plan=plan)
    e11.score_rows(model, rows, names)
    return rows, threshold


def make_row(r: dict[str, Any], threshold: float) -> dict[str, Any]:
    flat, press = float(r["flat"]), float(r["press"])
    return {
        "schema": SCHEMA_ROW,
        "mint": r["mint"],
        "mig_ms": int(r["mig_ms"]),
        "day": r["day"],
        "spec": r["spec"],
        "score": float(r["score"]),
        "entered": bool(r["score"] >= threshold),
        "filled": bool(r["filled"]),
        "status": r["status"],
        "gross": r["gross"],
        "flat": flat,
        "press": press,
        "flat_sol": flat / LAMPORTS,
        "press_sol": press / LAMPORTS,
    }


def _dump(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True)


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        raise Refused([f"{path} ends with a torn line (no trailing newline); repair or restore it, nothing was changed"])
    out = []
    for n, x in enumerate(text.splitlines(), 1):
        if not x.strip():
            continue
        try:
            out.append(json.loads(x))
        except json.JSONDecodeError as exc:
            raise Refused([f"{path} line {n} is not valid JSON ({exc}); repair or restore it, nothing was changed"])
    return out


# The only fields export-decisions may write. No net, gross, status or filled field, ever.
DECISION_EXPORT_KEYS = ("mint", "mig_ms", "score", "entered", "day")
DECISIONS_NAME = "decisions.jsonl"


def export_decisions(out_dir: Path, output: Path | None = None) -> int:
    """Write OUT/decisions.jsonl: one allowlisted row per (mint, mig_ms) for the live-readiness
    comparison (tools/forward_exp012_replay.py). The tool reads rows.jsonl in-process, like
    score and report do; it prints and writes none of its P&L fields. Returns rows written."""
    seen: dict[tuple[str, int], dict[str, Any]] = {}
    for r in read_rows(out_dir / ROWS_NAME):
        key = (r["mint"], int(r["mig_ms"]))
        seen.setdefault(key, {k: r[k] for k in DECISION_EXPORT_KEYS})
    rows = [seen[k] for k in sorted(seen)]
    assert all(set(r) == set(DECISION_EXPORT_KEYS) for r in rows)
    dest = output if output is not None else out_dir / DECISIONS_NAME
    atomic_write(dest, "".join(_dump(r) + "\n" for r in rows).encode("utf-8"))
    return len(rows)


def atomic_write(path: Path, data: bytes) -> None:
    """tmp + fsync + rename (+ directory fsync): a crash leaves the old file or the new one, never a torn one."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    dfd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def key_of(row: dict[str, Any]) -> tuple[str, int]:
    return (row["mint"], int(row["mig_ms"]))


def merge_rows(existing: Sequence[dict[str, Any]], fresh: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """(rows to append in (mig_ms, mint) order, conflicts). Existing rows are never touched."""
    have = {key_of(r): _dump(r) for r in existing}
    new: list[dict[str, Any]] = []
    conflicts: list[str] = []
    for r in sorted(fresh, key=lambda x: (x["mig_ms"], x["mint"])):
        k = key_of(r)
        if k in have:
            if have[k] != _dump(r):
                old = json.loads(have[k])
                diff = sorted(f for f in set(old) | set(r) if old.get(f) != r.get(f))
                conflicts.append(f"mint {k[0]} mig_ms {k[1]} differs in fields {diff} (values withheld)")
        else:
            have[k] = _dump(r)
            new.append(r)
    return new, conflicts


def check_window(clean_clock: datetime, read_end: datetime, test_window: bool) -> None:
    cc_s, re_s = clean_clock.strftime("%Y-%m-%dT%H:%M:%SZ"), read_end.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not test_window and (cc_s, re_s) != (PINNED_CLEAN_CLOCK, PINNED_READ_END):
        raise Refused([f"clean clock {cc_s} / read end {re_s} differ from the pinned window ({PINNED_CLEAN_CLOCK}, {PINNED_READ_END}); an override needs --test-window"])


def _wins(clean_clock: datetime, read_end: datetime) -> tuple[str, str]:
    return clean_clock.strftime("%Y-%m-%dT%H:%M:%SZ"), read_end.strftime("%Y-%m-%dT%H:%M:%SZ")


def score_runs(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in runs if not r.get("final")]


def ledger_markers(ledger: Path | None) -> list[dict[str, Any]]:
    if ledger is None or not ledger.is_file():
        return []
    text = ledger.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        raise Refused([f"{ledger} ends with a torn line; repair it before any score or report"])
    out = []
    for n, x in enumerate(text.splitlines(), 1):
        if x.strip():
            try:
                out.append(json.loads(x))
            except json.JSONDecodeError:
                raise Refused([f"{ledger} line {n} is not valid JSON"])
    return out


def ledger_append(ledger: Path, doc: dict[str, Any]) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(ledger), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(doc, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    dfd = os.open(str(ledger.parent), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def check_final_state(out_dir: Path, ledger: Path | None, clean_clock: datetime, read_end: datetime, test_window: bool) -> None:
    """Refuse if a FINAL marker for this window exists and this out-dir's lock is missing or differs,
    or if the ledger holds a non-test FINAL for the window from another out-dir."""
    cc_s, re_s = _wins(clean_clock, read_end)
    here = str(out_dir.resolve())
    lock = out_dir / LOCK_NAME
    markers = [{**m, "out_dir": here} for m in read_rows(out_dir / RUNS_NAME) if m.get("final")] + ledger_markers(ledger)
    problems: list[str] = []
    for m in markers:
        if (m.get("clean_clock"), m.get("read_end")) != (cc_s, re_s) or bool(m.get("test_window")) != test_window:
            continue
        if m.get("out_dir") != here:
            if not m.get("test_window"):
                problems.append(f"the ledger holds a FINAL read for this window taken in {m.get('out_dir')}; no second read in {here}")
            continue
        if not lock.is_file():
            problems.append(f"a FINAL marker exists (rows sha256 {m.get('rows_sha256')}) but {lock} is missing; the lock was deleted or lost")
        elif _sha256_file(lock) != m.get("lock_sha256"):
            problems.append(f"{lock} differs from the lock sha256 in the FINAL marker")
    if problems:
        raise Refused(sorted(set(problems)))


def run_score(walk_dir: Path, out_dir: Path, artifact_dir: Path, clean_clock: datetime, to: datetime | None, freeze_commit: str, frozen_manifest_md5: str | None = None, pool_start: datetime | None = None, read_end: datetime | None = None, test_window: bool = False, final_ledger: Path | None = None) -> dict[str, Any]:
    read_end = read_end if read_end is not None else parse_clock(PINNED_READ_END)
    check_window(clean_clock, read_end, test_window)
    check_final_state(out_dir, final_ledger, clean_clock, read_end, test_window)
    if (out_dir / LOCK_NAME).exists():
        raise Refused([f"{out_dir / LOCK_NAME} exists: the final read was taken; score no longer writes rows here"])
    errors = s12.check_frozen(artifact_dir, frozen_manifest_md5, freeze_commit)
    if errors:
        raise Refused(errors)
    start = pool_start if pool_start is not None else default_pool_start(clean_clock)
    if start.minute or start.second or start.microsecond:
        raise Refused([f"--pool-start {start.isoformat()} is not hour-aligned"])
    if to is None:
        to = default_to(walk_dir, start)
        if to <= start:
            raise Refused(hour_problems(walk_dir, [hour_key(start)]) or [f"no sealed+verified hour at the pool start {hour_key(start)}"])
    if to.minute or to.second or to.microsecond:
        raise Refused([f"--to {to.isoformat()} is not hour-aligned"])
    if to <= clean_clock:
        raise Refused([f"--to {hour_key(to)} is not after the clean clock {clean_clock.strftime('%Y-%m-%dT%H:%M:%SZ')}: nothing could count"])
    pool = e11._hours_range(hour_key(start), hour_key(to))
    errors = hour_problems(walk_dir, pool)
    if errors:
        raise Refused(errors)

    warnings = pool_warnings(clean_clock, start)
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path, runs_path = out_dir / ROWS_NAME, out_dir / RUNS_NAME
    cc_s = clean_clock.strftime("%Y-%m-%dT%H:%M:%SZ")
    prior_runs = score_runs(read_rows(runs_path))
    re_s0 = read_end.strftime("%Y-%m-%dT%H:%M:%SZ")
    if any((r.get("clean_clock"), r.get("read_end"), bool(r.get("test_window"))) != (cc_s, re_s0, bool(test_window)) for r in prior_runs):
        raise Refused([f"{runs_path} was written under a different clean clock, read end or test_window flag; use a new --out-dir"])

    t0 = time.time()
    rows, threshold = score_hours(walk_dir, pool, artifact_dir, out_dir / "scratch")
    lo, hi = ms(clean_clock), min(ms(to), ms(read_end))
    fresh = [make_row(r, threshold) for r in rows if lo <= int(r["mig_ms"]) < hi]
    existing = read_rows(rows_path)
    new, conflicts = merge_rows(existing, fresh)
    if conflicts:
        raise Conflict(["stored rows would change (nothing appended): " + "; ".join(conflicts[:5])])
    if new:
        old_bytes = rows_path.read_bytes() if rows_path.is_file() else b""
        atomic_write(rows_path, old_bytes + "".join(_dump(r) + "\n" for r in new).encode("utf-8"))
    summary = {
        "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "clean_clock": cc_s,
        "pool_from": pool[0],
        "to_exclusive": hour_key(to),
        "read_end": read_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "test_window": bool(test_window),
        "n_hours": len(pool),
        "warnings": warnings,
        "n_rows_seen_in_range": len(fresh),
        "n_appended": len(new),
        "n_total": len(existing) + len(new),
        "model_md5": (artifact_dir / "model.md5").read_text(encoding="utf-8").strip(),
        "frozen_manifest_md5": _md5_of_file(artifact_dir / FROZEN_MANIFEST_NAME),
        "threshold": threshold,
        "code_commit": _git_commit(),
        "runtime_s": round(time.time() - t0, 1),
    }
    summary["rows_sha256"] = _rows_sha256(out_dir)
    old_runs = runs_path.read_bytes() if runs_path.is_file() else b""
    atomic_write(runs_path, old_runs + (json.dumps(summary, sort_keys=True) + "\n").encode("utf-8"))
    return summary


# --- report -------------------------------------------------------------------------------


def _swap(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same rows with the pressure net in the `flat` slot, so compute_gate's primary
    leg (mean, 90% CI, ex-top-3, days) is the pressure model's own."""
    return [{**r, "flat": r["press"]} for r in rows]


def _leg(g: dict[str, Any]) -> dict[str, Any]:
    return {
        "n": g["n"],
        "mean_sol": g["mean_sol"],
        "total_sol": g["total_sol"],
        "mean_ci90_sol": g["mean_ci90_sol"],
        "total_ex_top3_sol": g["total_ex_top3_sol"],
        "days_positive": g["days_positive"],
        "n_days": g["n_days"],
        "per_day": g["days"],
        "blockers": g["promote_blockers"],
        "clears_gate": bool(g["promote_flat_15"] if "promote_flat_15" in g else g["promote"]),
    }


def build_report(rows: Sequence[dict[str, Any]], runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    entered = [r for r in rows if r["entered"]]
    gate = e11.compute_gate(entered)
    flat_leg = _leg(gate)
    press_leg = _leg(e11.compute_gate(_swap(entered)))
    p1 = gate.get("pressure_scale_1") or {}
    by_day: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = by_day.setdefault(r["day"], {"day": r["day"], "n_decided": 0, "n_entered": 0, "flat_total_sol": 0.0, "press_total_sol": 0.0})
        d["n_decided"] += 1
        if r["entered"]:
            d["n_entered"] += 1
            d["flat_total_sol"] += r["flat"] / LAMPORTS
            d["press_total_sol"] += r["press"] / LAMPORTS
    last = runs[-1] if runs else {}
    return {
        "schema": SCHEMA_REPORT,
        "mode": "FINAL",
        "label": LABEL,
        "verdict": "PASS" if gate.get("promote") else "FAIL",
        "verdict_note": "forward gate under both fail models; a PASS is forward simulated paper and is not live evidence",
        "clean_clock": last.get("clean_clock"),
        "scored_through_exclusive": last.get("to_exclusive"),
        "model_md5": last.get("model_md5"),
        "threshold": last.get("threshold"),
        "n_decided": len(rows),
        "n_entered": len(entered),
        "per_day": [by_day[d] for d in sorted(by_day)],
        "flat_15": flat_leg,
        "pressure_scale_1": press_leg,
        "gate": {
            "promote": bool(gate.get("promote")),
            "promote_blockers": gate.get("promote_blockers"),
            "promote_flat_15": gate.get("promote_flat_15", False),
            "promote_pressure_1": gate.get("promote_pressure_1", False),
            "pressure_scale_1_from_gate": p1,
        },
        "context_report_only": build_context(rows),
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def render_markdown(rep: dict[str, Any]) -> str:
    L = ([TEST_WINDOW_BANNER, ""] if rep.get("test_window") else []) + [f"# EXP-012 forward report: {rep['label']}", "", f"VERDICT: {rep['verdict']} ({rep['verdict_note']})", ""]
    L.append(f"clean clock {rep['clean_clock']}, scored through (exclusive) {rep['scored_through_exclusive']}, model md5 {rep['model_md5']}, threshold {rep['threshold']!r}")
    L.append(f"n_decided={rep['n_decided']} n_entered={rep['n_entered']}")
    L += ["", "| model | n | mean SOL/trade | 90% CI of mean | total SOL | total ex top-3 SOL | days positive | clears gate | blockers |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, leg in (("flat 15%", rep["flat_15"]), ("pressure x1", rep["pressure_scale_1"])):
        L.append(
            f"| {name} | {leg['n']} | {leg['mean_sol']!r} | {leg['mean_ci90_sol']!r} | {leg['total_sol']!r} | {leg['total_ex_top3_sol']!r} | {leg['days_positive']}/{leg['n_days']} | {leg['clears_gate']} | {', '.join(leg['blockers'])} |"
        )
    L += ["", f"gate (both models): promote={rep['gate']['promote']} blockers={rep['gate']['promote_blockers']}", "", "## Per UTC day (migration day)", "| day | n_decided | n_entered | flat SOL | pressure SOL |", "| --- | --- | --- | --- | --- |"]
    for d in rep["per_day"]:
        L.append(f"| {d['day']} | {d['n_decided']} | {d['n_entered']} | {d['flat_total_sol']:.6f} | {d['press_total_sol']:.6f} |")
    ctx = rep.get("context_report_only")
    if ctx:
        def pct(x: Any) -> str:
            return "-" if x is None else f"{x * 100:.1f}%"

        L += ["", "## Report-only context (no role in the gate)", "| day | all rows | baseline flat mean SOL | baseline pressure mean SOL | entered | selected fraction | fill rate entered | fill rate all rows |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for name, d in [("ALL", ctx["overall"])] + [(x["day"], x) for x in ctx["per_day"]]:
            bl = d["baseline_unfiltered"]
            fm = "-" if bl["flat_mean_sol"] is None else f"{bl['flat_mean_sol']:.6f}"
            pm = "-" if bl["press_mean_sol"] is None else f"{bl['press_mean_sol']:.6f}"
            L.append(f"| {name} | {bl['n_all_rows']} | {fm} | {pm} | {d['n_entered']} | {pct(d['selected_fraction'])} | {pct(d['fill_rate_entered'])} | {pct(d['fill_rate_all_rows'])} |")
    L += ["", f"Result label: {rep['label']}."]
    return "\n".join(L) + "\n"


def _rows_sha256(out_dir: Path) -> str:
    return _sha256_file(out_dir / ROWS_NAME) if (out_dir / ROWS_NAME).is_file() else hashlib.sha256(b"").hexdigest()


def _frac(n: int, d: int) -> float | None:
    return (n / d) if d else None


def build_context(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """FINAL only, report-only, never part of the gate. Uses only fields already in the rows:
    `entered`, `filled`, `flat`, `press`, `day`. Baseline = every scored row (entered or not) on its own nets."""
    def baseline(rs: Sequence[dict[str, Any]]) -> dict[str, Any]:
        n = len(rs)
        return {
            "n_all_rows": n,
            "flat_mean_sol": (sum(r["flat"] for r in rs) / n / LAMPORTS) if n else None,
            "flat_total_sol": sum(r["flat"] for r in rs) / LAMPORTS,
            "press_mean_sol": (sum(r["press"] for r in rs) / n / LAMPORTS) if n else None,
            "press_total_sol": sum(r["press"] for r in rs) / LAMPORTS,
        }

    def block(rs: Sequence[dict[str, Any]]) -> dict[str, Any]:
        ent = [r for r in rs if r["entered"]]
        return {
            "baseline_unfiltered": baseline(rs),
            "n_entered": len(ent),
            "selected_fraction": _frac(len(ent), len(rs)),
            "fill_rate_entered": _frac(sum(1 for r in ent if r["filled"]), len(ent)),
            "fill_rate_all_rows": _frac(sum(1 for r in rs if r["filled"]), len(rs)),
        }

    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    return {
        "note": "report-only context; no role in the gate or the verdict",
        "overall": block(rows),
        "per_day": [{"day": d, **block(by_day[d])} for d in sorted(by_day)],
    }


def build_interim(rows: Sequence[dict[str, Any]], runs: Sequence[dict[str, Any]], reasons: Sequence[str], clean_clock: str, read_end: str) -> dict[str, Any]:
    """Counts only. No P&L field of any kind, no gate call, no verdict."""
    by_day: dict[str, int] = {}
    for r in rows:
        if r["entered"]:
            by_day[r["day"]] = by_day.get(r["day"], 0) + 1
    last = runs[-1] if runs else {}
    return {
        "schema": SCHEMA_REPORT,
        "mode": "INTERIM",
        "label": LABEL,
        "note": "INTERIM: counts only. Nothing about profit is shown until the read is FINAL.",
        "clean_clock": clean_clock,
        "read_end": read_end,
        "test_window": False,
        "scored_through_exclusive": last.get("to_exclusive"),
        "why_interim": list(reasons),
        "n_rows": len(rows),
        "n_entered": sum(by_day.values()),
        "per_day_entered": [{"day": d, "n_entered": by_day[d]} for d in sorted(by_day)],
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def render_interim_markdown(rep: dict[str, Any]) -> str:
    L = ([TEST_WINDOW_BANNER, ""] if rep.get("test_window") else []) + [f"# EXP-012 forward report (INTERIM): {rep['label']}", "", rep["note"], ""]
    L.append(f"clean clock {rep['clean_clock']}, read end {rep['read_end']}, scored through (exclusive) {rep['scored_through_exclusive']}")
    L.append(f"n_rows={rep['n_rows']} n_entered={rep['n_entered']}")
    L += ["", "Why interim: " + ("; ".join(rep["why_interim"]) or "-"), "", "| day | n_entered |", "| --- | --- |"]
    L += [f"| {d['day']} | {d['n_entered']} |" for d in rep["per_day_entered"]]
    return "\n".join(L) + "\n"


def coverage_reasons(walk_dir: Path, start: datetime, read_end: datetime, runs: Sequence[dict[str, Any]]) -> list[str]:
    need_to = read_end + timedelta(hours=1)
    hours = e11._hours_range(hour_key(start), hour_key(need_to))
    probs = hour_problems(walk_dir, hours)
    out = []
    if probs:
        out.append(f"{len(probs)} hour problem(s) before read end + 1 h ({hour_key(need_to)}), first: {probs[0]}")
    last_to = runs[-1].get("to_exclusive") if runs else None
    if last_to is None or last_to < hour_key(need_to):
        out.append(f"rows scored only through {last_to}, need a score run with --to >= {hour_key(need_to)}")
    return out


def write_lock(out_dir: Path, doc: dict[str, Any]) -> None:
    fd = os.open(str(out_dir / LOCK_NAME), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    dfd = os.open(str(out_dir), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _write_final_files(out_dir: Path, rep: dict[str, Any]) -> None:
    atomic_write(out_dir / "report.json", (json.dumps(rep, indent=2, default=str) + "\n").encode("utf-8"))
    atomic_write(out_dir / "report.md", render_markdown(rep).encode("utf-8"))


def _final_docs(out_dir: Path, rows_sha: str, cc_s: str, re_s: str, test_window: bool) -> dict[str, Any]:
    return {
        "final": True,
        "schema": SCHEMA_MARKER,
        "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "clean_clock": cc_s,
        "read_end": re_s,
        "test_window": test_window,
        "rows_sha256": rows_sha,
        "lock_sha256": _sha256_file(out_dir / LOCK_NAME),
        "out_dir": str(out_dir.resolve()),
    }


def _append_marker_to_runs(out_dir: Path, marker: dict[str, Any]) -> None:
    runs_path = out_dir / RUNS_NAME
    old = runs_path.read_bytes() if runs_path.is_file() else b""
    atomic_write(runs_path, old + (json.dumps(marker, sort_keys=True) + "\n").encode("utf-8"))


def _mark_test(rep: dict[str, Any], test_window: bool) -> None:
    rep["test_window"] = test_window
    if test_window:
        rep["window_note"] = TEST_WINDOW_BANNER


def run_report(
    out_dir: Path,
    walk_dir: Path | None = None,
    clean_clock: datetime | None = None,
    read_end: datetime | None = None,
    pool_start: datetime | None = None,
    reprint: bool = False,
    test_window: bool = False,
    final_ledger: Path | None = None,
) -> dict[str, Any]:
    cc = clean_clock if clean_clock is not None else parse_clock(PINNED_CLEAN_CLOCK)
    re_ = read_end if read_end is not None else parse_clock(PINNED_READ_END)
    check_window(cc, re_, test_window)
    cc_s, re_s = _wins(cc, re_)
    check_final_state(out_dir, final_ledger, cc, re_, test_window)
    lock_path = out_dir / LOCK_NAME
    rows = read_rows(out_dir / ROWS_NAME)
    all_runs = read_rows(out_dir / RUNS_NAME)
    runs = score_runs(all_runs)
    if reprint:
        if not lock_path.is_file():
            raise Refused([f"--reprint: {lock_path} does not exist; there is no final read to re-render"])
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if (lock.get("clean_clock"), lock.get("read_end"), bool(lock.get("test_window"))) != (cc_s, re_s, test_window):
            raise Refused(["--reprint: the lock was taken for a different window or test_window flag"])
        if _rows_sha256(out_dir) != lock.get("rows_sha256"):
            raise Refused([f"--reprint refused: {out_dir / ROWS_NAME} no longer hashes to the lock's rows_sha256"])
        # a crash between the lock and the markers leaves none: complete them from the lock
        have_runs = any(m.get("final") for m in all_runs)
        if not have_runs:
            _append_marker_to_runs(out_dir, _final_docs(out_dir, lock["rows_sha256"], cc_s, re_s, test_window))
        if final_ledger is not None and not any(m.get("out_dir") == str(out_dir.resolve()) and (m.get("clean_clock"), m.get("read_end")) == (cc_s, re_s) for m in ledger_markers(final_ledger)):
            ledger_append(final_ledger, _final_docs(out_dir, lock["rows_sha256"], cc_s, re_s, test_window))
        rep = build_report(rows, runs)
        rep["read_end"] = re_s
        rep["reprint_of_lock"] = lock.get("utc_time")
        _mark_test(rep, test_window)
        _write_final_files(out_dir, rep)
        return rep
    if lock_path.exists():
        raise Refused([f"{lock_path} exists: the final read was already taken. `--reprint` re-renders it from the lock's rows sha256"])
    if walk_dir is None:
        raise Refused(["report needs --walk-dir to decide INTERIM or FINAL"])
    bad = [r for r in runs if (r.get("clean_clock"), r.get("read_end"), bool(r.get("test_window"))) != (cc_s, re_s, test_window)]
    if bad:
        raise Refused([f"{len(bad)} run(s) in {out_dir / RUNS_NAME} have a different clean clock, read end or test_window flag than this report ({cc_s}, {re_s}, test_window={test_window})"])
    lo, hi = ms(cc), ms(re_)
    outside = [r for r in rows if not (lo <= int(r["mig_ms"]) < hi)]
    if outside:
        raise Refused([f"{len(outside)} row(s) in {out_dir / ROWS_NAME} have mig_ms outside [{cc_s}, {re_s}), first: mint {outside[0]['mint']} mig_ms {outside[0]['mig_ms']}"])
    now_sha = _rows_sha256(out_dir)
    if runs:
        if runs[-1].get("rows_sha256") != now_sha:
            raise Refused([f"{out_dir / ROWS_NAME} does not hash to the rows_sha256 recorded by the last score run: the file changed outside `score`"])
    elif rows:
        raise Refused([f"{out_dir / ROWS_NAME} has rows but {out_dir / RUNS_NAME} records no score run"])
    start = pool_start if pool_start is not None else default_pool_start(cc)
    reasons = coverage_reasons(walk_dir, start, re_, runs)
    if reasons:
        rep = build_interim(rows, runs, reasons, cc_s, re_s)
        _mark_test(rep, test_window)
        atomic_write(out_dir / "report.json", (json.dumps(rep, indent=2) + "\n").encode("utf-8"))
        atomic_write(out_dir / "report.md", render_interim_markdown(rep).encode("utf-8"))
        return rep
    rep = build_report(rows, runs)
    rep["read_end"] = re_s
    _mark_test(rep, test_window)
    # 1. the lock, fsynced, before anything about the result is printed or written
    write_lock(
        out_dir,
        {
            "schema": SCHEMA_LOCK,
            "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "clean_clock": cc_s,
            "read_end": re_s,
            "test_window": test_window,
            "rows_sha256": now_sha,
            "n_rows": len(rows),
            "model_md5": runs[-1].get("model_md5") if runs else None,
            "code_commit": _git_commit(),
        },
    )
    # 2. durable markers: runs.jsonl and the external append-only ledger
    marker = _final_docs(out_dir, now_sha, cc_s, re_s, test_window)
    _append_marker_to_runs(out_dir, marker)
    if final_ledger is not None:
        ledger_append(final_ledger, marker)
    # 3. only now the verdict
    print(f"VERDICT: {rep['verdict']} ({LABEL}){' [' + TEST_WINDOW_BANNER + ']' if test_window else ''}; n_entered={rep['n_entered']}", file=sys.stderr, flush=True)
    _write_final_files(out_dir, rep)
    return rep


# --- CLI -------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("score", help="score sealed+verified forward hours; append new rows")
    sc.add_argument("--walk-dir", required=True)
    sc.add_argument("--clean-clock", default=None, help=f"pinned {PINNED_CLEAN_CLOCK}; an override needs --test-window")
    sc.add_argument("--test-window", action="store_true", help="allow a non-pinned clean clock / read end; output is marked test_window")
    sc.add_argument("--final-ledger", default=str(DEFAULT_LEDGER))
    sc.add_argument("--to", default=None, help="YYYY-MM-DDTHH, exclusive (default: latest sealed+verified hour boundary)")
    sc.add_argument("--read-end", default=None, help=f"exclusive end of the counted window (pinned {PINNED_READ_END}; an override needs --test-window)")
    sc.add_argument("--pool-start", default=None, help="YYYY-MM-DDTHH (default: clean clock hour - 2 x BUFFER_HOURS = 48 h)")
    sc.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    sc.add_argument("--out-dir", required=True)
    sc.add_argument("--freeze-commit", default=DEFAULT_FREEZE_COMMIT)
    sc.add_argument("--frozen-manifest-md5", default=None)
    vf = sub.add_parser("verify", help="backfill_verify --content for one hour, append its line to D/verify.jsonl")
    vf.add_argument("--walk-dir", required=True)
    vf.add_argument("--hour", required=True, help="YYYY-MM-DDTHH")
    ex = sub.add_parser("export-decisions", help="write decisions.jsonl (mint, mig_ms, score, entered, day only) for the live-readiness comparison")
    ex.add_argument("--out-dir", required=True)
    ex.add_argument("--output", default=None)
    rp = sub.add_parser("report", help="write report.json and report.md from rows.jsonl")
    rp.add_argument("--out-dir", required=True)
    rp.add_argument("--walk-dir", default=None)
    rp.add_argument("--clean-clock", default=None)
    rp.add_argument("--read-end", default=None)
    rp.add_argument("--test-window", action="store_true")
    rp.add_argument("--final-ledger", default=str(DEFAULT_LEDGER))
    rp.add_argument("--pool-start", default=None)
    rp.add_argument("--reprint", action="store_true", help="re-render the FINAL report from the lock's rows sha256 (no new read)")
    args = ap.parse_args(argv)
    if args.cmd in ("report", "score"):
        cc_arg = parse_clock(args.clean_clock or PINNED_CLEAN_CLOCK)
        re_arg = parse_clock(args.read_end or PINNED_READ_END)
        try:
            check_window(cc_arg, re_arg, args.test_window)
        except Refused as exc:
            for r in exc.reasons:
                print(f"REFUSED: {r}", file=sys.stderr)
            return exc.code
    if args.cmd == "export-decisions":
        try:
            n = export_decisions(Path(args.out_dir), Path(args.output) if args.output else None)
        except Refused as exc:
            for r in exc.reasons:
                print(f"REFUSED: {r}", file=sys.stderr)
            return exc.code
        print(f"decisions exported: {n} rows, keys {list(DECISION_EXPORT_KEYS)}", file=sys.stderr)
        return 0
    if args.cmd == "report":
        try:
            rep = run_report(
                Path(args.out_dir),
                Path(args.walk_dir) if args.walk_dir else None,
                cc_arg,
                re_arg,
                parse_clock(args.pool_start) if args.pool_start else None,
                args.reprint,
                args.test_window,
                Path(args.final_ledger),
            )
        except Refused as exc:
            for r in exc.reasons:
                print(f"REFUSED: {r}", file=sys.stderr)
            return exc.code
        if rep["mode"] == "INTERIM":
            print(f"INTERIM ({LABEL}): n_rows={rep['n_rows']} n_entered={rep['n_entered']}; no result until the read is FINAL", file=sys.stderr)
        else:
            print(f"FINAL written ({LABEL}); n_entered={rep['n_entered']}", file=sys.stderr)
        return 0
    if args.cmd == "verify":
        rec, appended = run_verify(Path(args.walk_dir), args.hour)
        ok = _line_ok(rec)
        print(f"hour {args.hour}: {'OK' if ok else 'NOT OK ' + str(rec['issues'])}; {'appended' if appended else 'identical line already present'}", file=sys.stderr)
        return 0 if ok else 1
    try:
        summary = run_score(
            Path(args.walk_dir),
            Path(args.out_dir),
            Path(args.artifact_dir),
            cc_arg,
            parse_clock(args.to) if args.to else None,
            args.freeze_commit,
            args.frozen_manifest_md5,
            parse_clock(args.pool_start) if args.pool_start else None,
            re_arg,
            args.test_window,
            Path(args.final_ledger),
        )
    except Refused as exc:
        for r in exc.reasons:
            print(f"REFUSED: {r}", file=sys.stderr)
        return exc.code
    except (SystemExit, Exception) as exc:  # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(summary, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
