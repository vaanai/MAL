#!/usr/bin/env python3
"""EXP-012 FORWARD scorer (DEC-016): the frozen model on sealed forward getBlock hours.

    python3 -m tools.exp012_forward verify --walk-dir D --hour H
    python3 -m tools.exp012_forward score  --walk-dir D --out-dir OUT [--clean-clock ISO] [--to HOUR] [--pool-start HOUR]
    python3 -m tools.exp012_forward report --out-dir OUT --walk-dir D [--clean-clock ISO] [--read-end ISO] [--reprint]

DEC-017 (secondary family): `--experiment EXP-###` (default EXP-012) picks a committed
`ARTIFACTS/<exp>/forward_spec.json` (see tools/forward_family.py). EXP-012's spec equals the constants
below and its outputs are byte-identical with or without it. A secondary has its own out-dir, lock and
external ledger (/data/mal/forward-family/<exp>/FINAL_READS.jsonl), the DEC-017 window, and its FINAL
refuses unless EXP-012's FINAL is in EXP-012's ledger (`--primary-ledger`). If EXP-012 FAILed it reports
"not tested (gate closed)" with no verdict; if PASS it computes the full-book gate and the tested quantity
(exit: paired increment vs the reference; band: the band's own gate; refit: the full gate) with one-sided
bootstrap p-values (10,000 draws, seed 1, both fail models). `family_holm` applies Holm across every
secondary in ARTIFACTS/forward-family/registry.json (fixed k). INTERIM hides P&L for every experiment.

Walk 2 (EXP-022) uses --strict-lines; the DEC-016 forward-1002 FINAL runs as written, without it.
`score --strict-lines` reads the walk tape strict (a raw NUL, non-JSON or non-object line in any hour refuses the
run, nothing appended); `verify --strict-lines` counts such lines as a verify issue. Without the flags `score` and
`verify` are exactly what they were: the verify line has none of the bad-line keys (`--report-bad-lines` adds them
without changing the OK status). Nothing in the environment switches either mode.

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
from typing import Any, ClassVar, Sequence

import tools.exp011_score as e11
import tools.exploration_entry_model as eem
import tools.exp012_score as s12
from tools.exp011_freeze import FROZEN_MANIFEST_NAME, _git_commit, _md5_of_file
import tools.backfill_verify as bv
import tools.tape_lines as tape_lines
import tools.forward_family as ff
from tools.latency_curve import _hour_file, _iter_trades

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


def _line_ok(rec: dict[str, Any], strict_bad_lines: bool = False) -> bool:
    if rec.get("issues") != [] or not isinstance(rec.get("content"), dict):
        return False
    if strict_bad_lines:
        # Opt-in (walk 2). A line with no bad_lines key predates the A8 verify and stays OK here;
        # the strict reader in run_score still checks the bytes.
        if rec.get("bad_lines") not in (None, 0):
            return False
        for stats in rec["content"].values():
            if isinstance(stats, dict) and stats.get("bad_lines") not in (None, 0):
                return False
    sha = rec.get("sha256")
    if not isinstance(sha, dict) or not all(isinstance(sha.get(sub), str) for sub in ("trades", "creates")):
        return False
    for stats in rec["content"].values():
        if not isinstance(stats, dict) or stats.get("duplicates") != 0:
            return False
    return True


def verified_hours(walk_dir: Path, strict_bad_lines: bool = False) -> dict[str, dict[str, Any]]:
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
    return {h: r for h, r in last.items() if _line_ok(r, strict_bad_lines)}


def hour_problems(walk_dir: Path, hours: Sequence[str], strict_bad_lines: bool = False) -> list[str]:
    sealed, ok = _sealed_hours(walk_dir), verified_hours(walk_dir, strict_bad_lines)
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


def verify_line(walk_dir: Path, hour: str, strict_bad_lines: bool = False, report_bad_lines: bool = False) -> dict[str, Any]:
    """`tools.backfill_verify` with --content for [hour, hour+1), plus the sha256 of each sealed file.

    By default the line is exactly what it was before the A8 bad-line count existed: no `bad_lines`, `nul`,
    `not_json`, `non_object` or `lenient` key, and the zstd exit code is not checked. `report_bad_lines=True` adds
    those keys and changes nothing else (`issues` and the OK status are as before). `strict_bad_lines=True`
    (walk 2) also reports them, and makes a bad line or a failed zstd stream an issue, so the hour is not OK."""
    nxt = hour_key(hour_dt(hour) + timedelta(hours=1))
    mode = "strict" if strict_bad_lines else "report" if report_bad_lines else "off"
    report = bv.build_report(walk_dir, hour, nxt, content=True, dedupe_out=None, min_slots_per_hour=9_000, max_slots_per_hour=bv.MAX_SLOTS_PER_HOUR, bad_lines=mode)
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


def run_verify(walk_dir: Path, hour: str, strict_bad_lines: bool = False, report_bad_lines: bool = False) -> tuple[dict[str, Any], bool]:
    """(line, appended). Appends to D/verify.jsonl unless the hour's last line is identical."""
    rec = verify_line(walk_dir, hour, strict_bad_lines, report_bad_lines)
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


def default_to(walk_dir: Path, start: datetime, strict_bad_lines: bool = False) -> datetime:
    """End (exclusive) of the longest sealed+verified run beginning at `start`."""
    sealed, ok = _sealed_hours(walk_dir), verified_hours(walk_dir, strict_bad_lines)
    cur = start
    while hour_key(cur) in sealed and hour_key(cur) in ok:
        cur += timedelta(hours=1)
    return cur


# --- hour resolver + tagged worker (picklable, module level) --------------------------


@dataclass(frozen=True)
class ForwardHours:
    root: str
    allowed: frozenset[str]
    exit_spec_id: str | None = None  # None: the read's own exit (tp50_sl30), nothing injected
    strict_lines: ClassVar[bool] = False  # not a field: see StrictForwardHours

    def __call__(self, key: str) -> dict[str, Any]:
        assert key in self.allowed, f"hour {key!r} is outside the sealed+verified forward range"
        root = Path(self.root)
        trade = _hour_file(root / "trades", "trades", key)
        if trade is None:
            raise SystemExit(f"missing trades file for verified hour {key} under {root}")
        create = _hour_file(root / "creates", "creates", key)
        return {"hour": key, "day": key[:10], "end": int(hour_dt(key).timestamp()) + 3600, "trade": trade, "create": create}


@dataclass(frozen=True)
class StrictForwardHours(ForwardHours):
    """Walk 2 (`score --strict-lines`): the same resolver; `_tagged_worker` reads its trades and creates strict."""

    strict_lines: ClassVar[bool] = True


def _strict_iter(path: Path) -> Any:
    return _iter_trades(path, strict=True)


def _tagged_worker(worker_id: int, home: list[str], buf: list[str], creator_hist: dict[str, list[int]], rows_out_path: Path | None, hours: ForwardHours) -> list[dict[str, Any]]:
    """`exp012_score._run_worker` with one addition: each scored row also carries the
    migration time (`mig_ms`), which `score_one` has but does not put in the row.
    Nothing else about a row changes. `score_one` is wrapped for the duration of the call only."""
    orig = eem.score_one
    orig_iter = eem._iter_trades
    exit_spec = None
    if hours.exit_spec_id is not None and hours.exit_spec_id != e11.TARGET_SPEC_ID:
        exit_spec = [s for s in eem.build_specs() if s["id"] == hours.exit_spec_id]  # DEC-017: a secondary's exit
        if len(exit_spec) != 1:
            raise SystemExit(f"unknown exit spec {hours.exit_spec_id!r}")

    def tagged(mint_id: str, mint: Any, *a: Any, **k: Any) -> list[dict[str, Any]]:
        if exit_spec is not None:
            k["specs"] = exit_spec
        rows = orig(mint_id, mint, *a, **k)
        for r in rows:
            r["mig_ms"] = int(mint.mig_ms)
        return rows

    eem.score_one = tagged
    try:
        if getattr(hours, "strict_lines", False):  # the same call as s12._run_worker, with the strict reader on the trades and the creates
            eem._iter_trades = _strict_iter
            return s12.run_worker_features(worker_id, home, buf, creator_hist, hour_info_fn=hours, rows_out_path=rows_out_path, row_iter_fn=_strict_iter)
        return s12._run_worker(worker_id, home, buf, creator_hist, rows_out_path, hours)
    finally:
        eem.score_one = orig
        eem._iter_trades = orig_iter


# --- scoring ----------------------------------------------------------------------------


def score_hours(walk_dir: Path, pool: Sequence[str], artifact_dir: Path, scratch: Path, exit_spec_id: str | None = None, strict_lines: bool = False) -> tuple[list[dict[str, Any]], float]:
    """Rows of the read's pipeline on `pool`, scored by the frozen model. Every row has mig_ms and score.
    `exit_spec_id` None or tp50_sl30 is the read's exit and calls load_rows exactly as before."""
    model, threshold, names = e11.load_frozen_spec(artifact_dir)
    plan = anchored_plan(pool, s12.MAX_HOME_HOURS, s12.BUFFER_HOURS)
    orig_iter = s12._iter_trades
    if strict_lines:  # build_creator_history (this process) reads the creates; the workers get it from StrictForwardHours
        s12._iter_trades = _strict_iter
    try:
        if exit_spec_id is None or exit_spec_id == e11.TARGET_SPEC_ID:
            hours = (StrictForwardHours if strict_lines else ForwardHours)(str(walk_dir), frozenset(pool))
            rows = s12.load_rows(hours, s12.MAX_WORKERS, s12.BUFFER_HOURS, s12.MAX_HOME_HOURS, scratch, pool_hours=list(pool), worker_fn=_tagged_worker, plan=plan)
        else:
            hours = (StrictForwardHours if strict_lines else ForwardHours)(str(walk_dir), frozenset(pool), exit_spec_id)
            rows = s12.load_rows(hours, s12.MAX_WORKERS, s12.BUFFER_HOURS, s12.MAX_HOME_HOURS, scratch, pool_hours=list(pool), worker_fn=_tagged_worker, plan=plan, spec_id=exit_spec_id)
    finally:
        s12._iter_trades = orig_iter
    e11.score_rows(model, rows, names)
    return rows, threshold


def make_row(r: dict[str, Any], threshold: float, band: tuple[float, float] | None = None) -> dict[str, Any]:
    """`band` (DEC-017 looser-threshold variant) is [t_low, t_high): `entered` is the full book
    (score >= t_low) and `in_band` marks the marginal band. Without a band the row is unchanged."""
    flat, press = float(r["flat"]), float(r["press"])
    row = {
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
    if band is not None:
        row["entered"] = bool(r["score"] >= band[0])
        row["in_band"] = bool(band[0] <= r["score"] < band[1])
    return row


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


def check_final_state(out_dir: Path, ledger: Path | None, clean_clock: datetime, read_end: datetime, test_window: bool, experiment: str = ff.PRIMARY_EXPERIMENT) -> None:
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
        if m.get("experiment", ff.PRIMARY_EXPERIMENT) != experiment:
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


def check_lock_owner(out_dir: Path, experiment: str) -> None:
    """Refuse if this out-dir's lock or FINAL markers belong to another experiment (DEC-017 ownership).
    Documents written before the `experiment` field existed are EXP-012's."""
    docs = []
    lock = out_dir / LOCK_NAME
    if lock.is_file():
        docs.append((LOCK_NAME, json.loads(lock.read_text(encoding="utf-8"))))
    runs = out_dir / RUNS_NAME
    if runs.is_file():
        docs += [(RUNS_NAME, m) for m in read_rows(runs) if m.get("final")]
    for name, d in docs:
        if d.get("experiment", ff.PRIMARY_EXPERIMENT) != experiment:
            raise Refused([f"{out_dir / name} belongs to {d.get('experiment', ff.PRIMARY_EXPERIMENT)}, not {experiment}; every experiment has its own --out-dir"])


def check_ledgers(experiment: str, final_ledger: Path | None, primary_ledger: Path | None) -> None:
    """A secondary's ledger may never be EXP-012's, by realpath."""
    if experiment == ff.PRIMARY_EXPERIMENT or final_ledger is None:
        return
    mine = os.path.realpath(final_ledger)
    for other in (ff.PRIMARY_LEDGER, primary_ledger):
        if other is not None and os.path.realpath(other) == mine:
            raise Refused([f"{experiment}'s --final-ledger {final_ledger} is EXP-012's ledger; a secondary has its own"])


def secondary_binding(spec: ff.ForwardSpec, registry_path: Path | None, artifact_dir: Path) -> dict[str, Any]:
    """What a secondary's runs, lock and FINAL marker are bound to: the registry file, the spec file, the frozen manifest."""
    reg = registry_path if registry_path is not None else ff.REGISTRY_PATH
    return {
        "registry_sha256": ff.file_sha256(reg) if reg.is_file() else None,
        "spec_sha256": ff.file_sha256(spec.source) if spec.source is not None and spec.source.is_file() else None,
        "frozen_manifest_md5": _md5_of_file(artifact_dir / FROZEN_MANIFEST_NAME) if (artifact_dir / FROZEN_MANIFEST_NAME).is_file() else None,
    }


def run_score(walk_dir: Path, out_dir: Path, artifact_dir: Path, clean_clock: datetime, to: datetime | None, freeze_commit: str, frozen_manifest_md5: str | None = None, pool_start: datetime | None = None, read_end: datetime | None = None, test_window: bool = False, final_ledger: Path | None = None, spec: ff.ForwardSpec | None = None, registry_path: Path | None = None, primary_ledger: Path | None = None, strict_lines: bool = False) -> dict[str, Any]:
    read_end = read_end if read_end is not None else parse_clock(PINNED_READ_END)
    experiment = spec.experiment if spec is not None else ff.PRIMARY_EXPERIMENT
    secondary = spec is not None and spec.is_secondary
    check_window(clean_clock, read_end, test_window)
    check_lock_owner(out_dir, experiment)
    check_ledgers(experiment, final_ledger, primary_ledger)
    if secondary and spec.band is not None:
        thr_file = float(json.loads((artifact_dir / spec.threshold_file).read_text(encoding="utf-8"))["threshold"]) if (artifact_dir / spec.threshold_file).is_file() else None
        if thr_file is None or spec.band[1] != thr_file or not spec.band[0] < spec.band[1]:
            raise Refused([f"{experiment}: selection_band t_high {spec.band[1]!r} must equal the frozen threshold {thr_file!r} and t_low < t_high"])
    check_final_state(out_dir, final_ledger, clean_clock, read_end, test_window, experiment)
    if (out_dir / LOCK_NAME).exists():
        raise Refused([f"{out_dir / LOCK_NAME} exists: the final read was taken; score no longer writes rows here"])
    errors = s12.check_frozen(artifact_dir, frozen_manifest_md5, freeze_commit)
    if errors:
        raise Refused(errors)
    start = pool_start if pool_start is not None else default_pool_start(clean_clock)
    if start.minute or start.second or start.microsecond:
        raise Refused([f"--pool-start {start.isoformat()} is not hour-aligned"])
    if to is None:
        to = default_to(walk_dir, start, strict_lines)
        if to <= start:
            raise Refused(hour_problems(walk_dir, [hour_key(start)], strict_lines) or [f"no sealed+verified hour at the pool start {hour_key(start)}"])
    if to.minute or to.second or to.microsecond:
        raise Refused([f"--to {to.isoformat()} is not hour-aligned"])
    if to <= clean_clock:
        raise Refused([f"--to {hour_key(to)} is not after the clean clock {clean_clock.strftime('%Y-%m-%dT%H:%M:%SZ')}: nothing could count"])
    pool = e11._hours_range(hour_key(start), hour_key(to))
    errors = hour_problems(walk_dir, pool, strict_lines)
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
    if any(r.get("experiment", ff.PRIMARY_EXPERIMENT) != experiment for r in prior_runs):
        raise Refused([f"{runs_path} belongs to another experiment than {experiment}; every experiment has its own --out-dir"])

    t0 = time.time()
    # strict_lines (walk 2, --strict-lines): the walk tape is read strict, so a NUL / non-JSON line in any hour is
    # a data hole and the run refuses (nothing appended). Off (the DEC-016 FINAL as written): the reader is the
    # lenient one it always was. The flag travels in StrictForwardHours; no environment variable switches it.
    try:
        rows, threshold = score_hours(walk_dir, pool, artifact_dir, out_dir / "scratch", spec.exit_spec_id if secondary else None, strict_lines)
    except tape_lines.BadLinesError as exc:
        raise Refused([f"bad tape lines, hour not decidable: {exc}"])
    lo, hi = ms(clean_clock), min(ms(to), ms(read_end))
    band = spec.band if secondary else None
    fresh = [make_row(r, threshold, band) for r in rows if lo <= int(r["mig_ms"]) < hi]
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
    if secondary:
        summary.update({"experiment": experiment, "exit_spec_id": spec.exit_spec_id, "selection_band": list(spec.band) if spec.band else None, **secondary_binding(spec, registry_path, artifact_dir)})
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


def build_interim(rows: Sequence[dict[str, Any]], runs: Sequence[dict[str, Any]], reasons: Sequence[str], clean_clock: str, read_end: str, experiment: str = ff.PRIMARY_EXPERIMENT) -> dict[str, Any]:
    """Counts only. No P&L field of any kind, no gate call, no verdict."""
    by_day: dict[str, int] = {}
    for r in rows:
        if r["entered"]:
            by_day[r["day"]] = by_day.get(r["day"], 0) + 1
    last = runs[-1] if runs else {}
    out = {
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
    if experiment != ff.PRIMARY_EXPERIMENT:
        out["experiment"] = experiment
    return out


def render_interim_markdown(rep: dict[str, Any]) -> str:
    L = ([TEST_WINDOW_BANNER, ""] if rep.get("test_window") else []) + [f"# {rep.get('experiment', ff.PRIMARY_EXPERIMENT)} forward report (INTERIM): {rep['label']}", "", rep["note"], ""]
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


def _report_json_bytes(rep: dict[str, Any]) -> bytes:
    return (json.dumps(rep, indent=2, default=str) + "\n").encode("utf-8")


def _write_final_files(out_dir: Path, rep: dict[str, Any]) -> None:
    atomic_write(out_dir / "report.json", _report_json_bytes(rep))
    md = render_secondary_markdown(rep) if rep.get("schema") == SCHEMA_SECONDARY_REPORT else render_markdown(rep)
    atomic_write(out_dir / "report.md", md.encode("utf-8"))


def _final_docs(out_dir: Path, rows_sha: str, cc_s: str, re_s: str, test_window: bool, experiment: str = ff.PRIMARY_EXPERIMENT, binding: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = {
        "experiment": experiment,
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
    if binding:
        doc.update(binding)
    return doc


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
    spec: ff.ForwardSpec | None = None,
    primary_ledger: Path | None = None,
    registry_path: Path | None = None,
    reference_out_dir: Path | None = None,
    reference_ledger: Path | None = None,
) -> dict[str, Any]:
    experiment = spec.experiment if spec is not None else ff.PRIMARY_EXPERIMENT
    secondary = spec is not None and spec.is_secondary
    cc = clean_clock if clean_clock is not None else parse_clock(PINNED_CLEAN_CLOCK)
    re_ = read_end if read_end is not None else parse_clock(PINNED_READ_END)
    check_window(cc, re_, test_window)
    cc_s, re_s = _wins(cc, re_)
    check_lock_owner(out_dir, experiment)  # before --reprint can overwrite another experiment's report
    check_ledgers(experiment, final_ledger, primary_ledger)
    check_final_state(out_dir, final_ledger, cc, re_, test_window, experiment)
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
        ctx0 = secondary_context(spec, cc_s, re_s, test_window, primary_ledger, registry_path, reference_out_dir, reference_ledger) if secondary else None
        if secondary:
            changed = [k for k, v in ctx0["binding"].items() if lock.get(k) != v]
            if changed:
                raise Refused([f"--reprint: {experiment}'s lock binds {changed} to values that differ from the current registry, spec or frozen manifest"])
        bind = None
        if secondary:
            # the report is deterministic given rows, runs, ctx and the lock's time; it must hash to what the lock recorded
            rep = _final_report(rows, runs, spec, ctx0)
            rep["read_end"] = re_s
            _mark_test(rep, test_window)
            rep["generated_at_utc"] = lock.get("utc_time")
            if hashlib.sha256(_report_json_bytes(rep)).hexdigest() != lock.get("report_sha256"):
                raise Refused([f"--reprint refused: {experiment}'s report does not re-render to the report_sha256 in its lock"])
            bind = {**ctx0["binding"], "report_sha256": lock["report_sha256"]}
        # a crash between the lock and the markers leaves none: complete them from the lock
        have_runs = any(m.get("final") for m in all_runs)
        if not have_runs:
            _append_marker_to_runs(out_dir, _final_docs(out_dir, lock["rows_sha256"], cc_s, re_s, test_window, experiment, bind))
        if final_ledger is not None and not any(m.get("out_dir") == str(out_dir.resolve()) and (m.get("clean_clock"), m.get("read_end")) == (cc_s, re_s) for m in ledger_markers(final_ledger)):
            ledger_append(final_ledger, _final_docs(out_dir, lock["rows_sha256"], cc_s, re_s, test_window, experiment, bind))
        if not secondary:
            rep = _final_report(rows, runs, spec, ctx0)
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
        rep = build_interim(rows, runs, reasons, cc_s, re_s, experiment)
        _mark_test(rep, test_window)
        atomic_write(out_dir / "report.json", (json.dumps(rep, indent=2) + "\n").encode("utf-8"))
        atomic_write(out_dir / "report.md", render_interim_markdown(rep).encode("utf-8"))
        return rep
    # DEC-017 point 1: a secondary needs EXP-012's FINAL lock. Refused here, before this out-dir's lock exists.
    ctx = secondary_context(spec, cc_s, re_s, test_window, primary_ledger, registry_path, reference_out_dir, reference_ledger) if secondary else None
    if secondary:
        bind = ctx["binding"]
        stale = [k for k, v in bind.items() if any(r.get(k) != v for r in (runs[-1:] if k == "registry_sha256" else runs))]
        if stale:
            raise Refused([f"{experiment}: score runs were taken against a different {stale} than the current registry, spec or frozen manifest; re-score into a new --out-dir"])
    rep = _final_report(rows, runs, spec, ctx)
    rep["read_end"] = re_s
    _mark_test(rep, test_window)
    # a secondary's lock binds the rendered report's sha256: render first (in memory), then lock, then print/write
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    extra: dict[str, Any] = {}
    if secondary:
        rep["generated_at_utc"] = stamp  # a reprint reproduces the same bytes from the lock's time
        extra = {"report_sha256": hashlib.sha256(_report_json_bytes(rep)).hexdigest()}
    # 1. the lock, fsynced, before anything about the result is printed or written
    write_lock(
        out_dir,
        {
            "experiment": experiment,
            **(ctx["binding"] if secondary else {}),
            **extra,
            "schema": SCHEMA_LOCK,
            "utc_time": stamp if secondary else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    marker = _final_docs(out_dir, now_sha, cc_s, re_s, test_window, experiment, {**ctx["binding"], **extra} if secondary else None)
    _append_marker_to_runs(out_dir, marker)
    if final_ledger is not None:
        ledger_append(final_ledger, marker)
    # 3. only now the verdict
    print(f"{experiment + ' ' if secondary else ''}VERDICT: {rep['verdict'] or GATE_CLOSED_NOTE} ({LABEL}){' [' + TEST_WINDOW_BANNER + ']' if test_window else ''}; n_entered={rep['n_entered']}", file=sys.stderr, flush=True)
    _write_final_files(out_dir, rep)
    return rep


# --- DEC-017: the gatekept secondary family ---------------------------------------------------

SCHEMA_SECONDARY_REPORT = "forward_secondary_report_v1"
SCHEMA_FAMILY = "forward_family_holm_v1"
GATE_CLOSED_NOTE = "not tested (gate closed)"


def _spec_refused(exc: ff.SpecRefused) -> Refused:
    return Refused(exc.reasons)


def _locked_final(out_dir: Path, cc_s: str, re_s: str, test_window: bool, what: str, experiment: str = ff.PRIMARY_EXPERIMENT) -> dict[str, Any]:
    """The lock of `experiment`'s FINAL read in `out_dir`, checked against its rows file and window. Names fields, never values."""
    lock_path = out_dir / LOCK_NAME
    if not lock_path.is_file():
        raise Refused([f"{what}: no {LOCK_NAME} in {out_dir}"])
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("experiment", ff.PRIMARY_EXPERIMENT) != experiment:
        raise Refused([f"{what}: the lock in {out_dir} belongs to {lock.get('experiment', ff.PRIMARY_EXPERIMENT)}, not {experiment}"])
    if (lock.get("clean_clock"), lock.get("read_end"), bool(lock.get("test_window"))) != (cc_s, re_s, test_window):
        raise Refused([f"{what}: the lock in {out_dir} was taken for a different window or test_window flag"])
    if _rows_sha256(out_dir) != lock.get("rows_sha256"):
        raise Refused([f"{what}: {out_dir / ROWS_NAME} no longer hashes to the lock's rows_sha256"])
    return lock


def ledger_final(ledger: Path | None, experiment: str, cc_s: str, re_s: str, test_window: bool, out_dir: Path | None = None) -> dict[str, Any]:
    """`experiment`'s FINAL read as recorded in its external ledger, with its lock and rows verified against the marker.
    `out_dir` (if given) must be the marker's out_dir. Returns {out_dir, lock_sha256, rows_sha256}."""
    want = str(out_dir.resolve()) if out_dir is not None else None
    markers = [
        m
        for m in ledger_markers(ledger)
        if m.get("final")
        and m.get("experiment", ff.PRIMARY_EXPERIMENT) == experiment
        and (m.get("clean_clock"), m.get("read_end")) == (cc_s, re_s)
        and bool(m.get("test_window")) == test_window
        and (want is None or m.get("out_dir") == want)
    ]
    if not markers:
        where = f" in {out_dir}" if out_dir is not None else ""
        raise Refused([f"DEC-017: {experiment}'s FINAL read{where} for [{cc_s}, {re_s}) is not in {ledger}"])
    m = markers[-1]
    out = Path(m["out_dir"])
    lock = _locked_final(out, cc_s, re_s, test_window, f"{experiment} FINAL", experiment)
    if _sha256_file(out / LOCK_NAME) != m.get("lock_sha256") or lock.get("rows_sha256") != m.get("rows_sha256"):
        raise Refused([f"{experiment} FINAL: the lock in {out} does not match its ledger marker"])
    return {"out_dir": str(out), "lock_sha256": m["lock_sha256"], "rows_sha256": m["rows_sha256"], "report_sha256": m.get("report_sha256"), "lock": lock}


def gate_verdict(rows: Sequence[dict[str, Any]]) -> str:
    """The promotion gate's verdict recomputed from locked rows, the way `build_report` computes it."""
    entered = [r for r in rows if r["entered"]]
    return "PASS" if entered and e11.compute_gate(entered).get("promote") else "FAIL"


def primary_final(primary_ledger: Path | None, cc_s: str, re_s: str, test_window: bool) -> dict[str, Any]:
    """EXP-012's FINAL read from its external ledger: {out_dir, lock_sha256, rows_sha256, verdict}.
    The verdict is RECOMPUTED from the locked rows (whose sha256 matches the lock and the ledger marker),
    never read from the mutable report.json."""
    try:
        info = ledger_final(primary_ledger, ff.PRIMARY_EXPERIMENT, cc_s, re_s, test_window)
    except Refused as exc:
        raise Refused([f"{r}; a secondary is read only after EXP-012's FINAL" for r in exc.reasons])
    info["verdict"] = gate_verdict(read_rows(Path(info["out_dir"]) / ROWS_NAME))
    return info


def _last_model_md5(out_dir: Path) -> str | None:
    runs = score_runs(read_rows(out_dir / RUNS_NAME))
    return runs[-1].get("model_md5") if runs else None


def secondary_context(
    spec: ff.ForwardSpec,
    cc_s: str,
    re_s: str,
    test_window: bool,
    primary_ledger: Path | None,
    registry_path: Path | None,
    reference_out_dir: Path | None,
    reference_ledger: Path | None = None,
) -> dict[str, Any]:
    """Everything a secondary's FINAL needs from outside its own out-dir; raises Refused before any lock is taken."""
    reg_path = registry_path if registry_path is not None else ff.REGISTRY_PATH
    try:
        ff.require_registered(spec, ff.load_registry(reg_path))
    except ff.SpecRefused as exc:
        raise _spec_refused(exc)
    prim_ledger = primary_ledger if primary_ledger is not None else ff.PRIMARY_LEDGER
    prim = primary_final(prim_ledger, cc_s, re_s, test_window)
    ctx: dict[str, Any] = {"primary": prim, "gate_open": prim["verdict"] == "PASS", "reference_rows": None, "binding": secondary_binding(spec, reg_path, spec.artifact_dir)}
    if ctx["gate_open"] and spec.variant_kind == "exit":
        ref_exp = spec.reference_experiment
        ref_dir = reference_out_dir
        if ref_dir is None:
            if ref_exp != ff.PRIMARY_EXPERIMENT:
                raise Refused([f"{spec.experiment} is paired against {ref_exp}: pass --reference-out-dir"])
            ref_dir = Path(prim["out_dir"])
        ref_ledger = reference_ledger if reference_ledger is not None else (prim_ledger if ref_exp == ff.PRIMARY_EXPERIMENT else ff.default_ledger(ref_exp))
        # bound: the lock's experiment is the spec's reference, and its FINAL marker is in that experiment's ledger
        ledger_final(ref_ledger, ref_exp, cc_s, re_s, test_window, out_dir=ref_dir)
        ctx["reference_rows"] = read_rows(ref_dir / ROWS_NAME)
        ctx["reference_out_dir"] = str(ref_dir.resolve())
        ctx["reference_model_md5"] = _last_model_md5(ref_dir)
    return ctx


def _means_only(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    out: dict[str, Any] = {"n": n}
    for m in ("flat", "press"):
        tot = sum(r[m] for r in rows) / LAMPORTS
        out[f"{m}_total_sol"] = tot
        out[f"{m}_mean_sol"] = (tot / n) if n else None
    return out


def build_secondary_report(rows: Sequence[dict[str, Any]], runs: Sequence[dict[str, Any]], spec: ff.ForwardSpec, ctx: dict[str, Any]) -> dict[str, Any]:
    entered = [r for r in rows if r["entered"]]
    in_band = [r for r in rows if r.get("in_band")]
    last = runs[-1] if runs else {}
    rep: dict[str, Any] = {
        "schema": SCHEMA_SECONDARY_REPORT,
        "mode": "FINAL",
        "label": LABEL,
        "experiment": spec.experiment,
        "role": spec.role,
        "variant_kind": spec.variant_kind,
        "registration_order": spec.registration_order,
        "clean_clock": last.get("clean_clock"),
        "scored_through_exclusive": last.get("to_exclusive"),
        "model_md5": last.get("model_md5"),
        "threshold": last.get("threshold"),
        "exit_spec_id": spec.exit_spec_id,
        "selection_band": list(spec.band) if spec.band else None,
        "n_decided": len(rows),
        "n_entered": len(entered),
        "binding": ctx["binding"],
        "primary": {"experiment": ff.PRIMARY_EXPERIMENT, "verdict": ctx["primary"]["verdict"], "rows_sha256": ctx["primary"]["rows_sha256"], "lock_sha256": ctx["primary"]["lock_sha256"]},
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if not ctx["gate_open"]:
        rep.update(
            {
                "gate_state": "closed",
                "verdict": None,
                "verdict_note": GATE_CLOSED_NOTE,
                "exploration_only": {
                    "note": "exploration only; EXP-012 failed, so no verdict, gate or p-value is computed (DEC-017 point 1)",
                    "full_book": _means_only(entered),
                    "band": _means_only(in_band) if spec.band else None,
                },
            }
        )
        return rep
    gate = e11.compute_gate(entered)
    flat_leg, press_leg = _leg(gate), _leg(e11.compute_gate(_swap(entered)))
    full_clears = bool(gate.get("promote"))
    if spec.variant_kind == "exit":
        inc = ff.paired_increment(rows, ctx["reference_rows"])
        same_model = ctx.get("reference_model_md5") is not None and ctx.get("reference_model_md5") == last.get("model_md5")
        blockers = []
        if same_model and (inc["n_variant_only"] or inc["n_reference_only"]):
            blockers.append(f"entered sets differ with the same model as the reference: {inc['n_variant_only']} variant-only, {inc['n_reference_only']} reference-only")
        tested = {"kind": "exit_increment", "reference_experiment": spec.reference_experiment, "reference_out_dir": ctx.get("reference_out_dir"), "same_model_as_reference": same_model, **inc, "blockers": blockers}
        tested["clears"] = bool(inc["clears"]) and not blockers
        tested_clears = tested["clears"]
    elif spec.variant_kind == "band":
        bgate = e11.compute_gate(in_band) if in_band else {"promote": False, "promote_blockers": ["no trades in the band"]}
        tested = {"kind": "band", "band": list(spec.band), "n_in_band": len(in_band), "gate_promote": bool(bgate.get("promote")), "gate_blockers": bgate.get("promote_blockers"), **ff.book_test(in_band)}
        tested_clears = bool(bgate.get("promote"))
    else:
        tested = {"kind": "refit_full_book", **ff.book_test(entered)}
        tested_clears = full_clears
    tested["p"] = ff.p_pair(tested)
    own = full_clears and tested_clears
    rep.update(
        {
            "gate_state": "open",
            "verdict": "PENDING_HOLM" if own else "FAIL",
            "verdict_note": "own checks " + ("clear; the verdict is final only after `family_holm` over every registered secondary" if own else "do not clear (the full-book gate and the tested quantity are both required)"),
            "own_checks_clear": own,
            "full_book_gate": {"promote": full_clears, "blockers": gate.get("promote_blockers"), "flat_15": flat_leg, "pressure_scale_1": press_leg},
            "tested_quantity": tested,
            "bootstrap": {"draws": ff.BOOTSTRAP_DRAWS, "seed": ff.BOOTSTRAP_SEED, "note": "the p-values and the increment CI use 10,000 draws; the full-book gate's own CI (tools.paper_attention_promote) uses 1,000 draws, seed 1"},
            "context_report_only": build_context(rows),
        }
    )
    return rep


def _final_report(rows: Sequence[dict[str, Any]], runs: Sequence[dict[str, Any]], spec: ff.ForwardSpec | None, ctx: dict[str, Any] | None) -> dict[str, Any]:
    if ctx is None:
        return build_report(rows, runs)
    return build_secondary_report(rows, runs, spec, ctx)


def render_secondary_markdown(rep: dict[str, Any]) -> str:
    L = ([TEST_WINDOW_BANNER, ""] if rep.get("test_window") else []) + [f"# {rep['experiment']} forward report (secondary, DEC-017): {rep['label']}", ""]
    L.append(f"VERDICT: {rep['verdict'] or GATE_CLOSED_NOTE} ({rep['verdict_note']})")
    L.append(f"{ff.PRIMARY_EXPERIMENT} verdict: {rep['primary']['verdict']}; variant {rep['variant_kind']}, exit {rep['exit_spec_id']}, band {rep['selection_band']}")
    L.append(f"n_decided={rep['n_decided']} n_entered={rep['n_entered']}")
    if rep["gate_state"] == "closed":
        L += ["", "Exploration only (no verdict):", json.dumps(rep["exploration_only"], indent=2, default=str)]
        return "\n".join(L) + "\n"
    fb = rep["full_book_gate"]
    L += ["", f"full-book gate: promote={fb['promote']} blockers={fb['blockers']}", f"tested quantity: {json.dumps(rep['tested_quantity'], default=str)}", f"own checks clear: {rep['own_checks_clear']}"]
    return "\n".join(L) + "\n"


def run_family_holm(registry_path: Path | None, out_dirs: dict[str, Path], primary_ledger: Path | None, clean_clock: datetime, read_end: datetime, test_window: bool, output: Path, ledgers: dict[str, Path] | None = None) -> dict[str, Any]:
    """Holm-Bonferroni across every registered secondary (fixed k), both fail models, on the p-values their
    FINAL reports hold. Reads only locked, hash-checked reports. Writes `output` atomically."""
    check_window(clean_clock, read_end, test_window)
    cc_s, re_s = _wins(clean_clock, read_end)
    reg_path = registry_path if registry_path is not None else ff.REGISTRY_PATH
    try:
        reg = ff.load_registry(reg_path)
    except ff.SpecRefused as exc:
        raise _spec_refused(exc)
    if not test_window:
        problem = ff.registry_commit_problem(reg_path, reg["registry_deadline"])
        if problem:
            raise Refused([problem])
    reg_sha = ff.file_sha256(reg_path)
    registered = {s["experiment"]: s["registration_order"] for s in reg["secondaries"]}
    missing, extra = sorted(set(registered) - set(out_dirs)), sorted(set(out_dirs) - set(registered))
    if missing or extra:
        raise Refused([f"family_holm needs exactly the {len(registered)} registered secondaries; missing {missing}, not registered {extra}. A missing secondary FINAL blocks the family read: none is withdrawn once registered (DEC-017 point 2)"])
    prim = primary_final(primary_ledger if primary_ledger is not None else ff.PRIMARY_LEDGER, cc_s, re_s, test_window)
    reports: dict[str, dict[str, Any]] = {}
    for exp, d in sorted(out_dirs.items()):
        lock = _locked_final(d, cc_s, re_s, test_window, f"{exp} FINAL (a missing secondary FINAL blocks family_holm; none is withdrawn)", exp)
        if lock.get("registry_sha256") != reg_sha:
            raise Refused([f"{exp}: its lock binds registry sha256 {lock.get('registry_sha256')}, the registry file now hashes to {reg_sha}"])
        rp = d / "report.json"
        if not rp.is_file():
            raise Refused([f"{exp}: {rp} is missing; run its `report --reprint` first"])
        # the report is bound to the lock and to the ledger marker by sha256 before any p-value is read
        led = (ledgers or {}).get(exp) or ff.default_ledger(exp)
        info = ledger_final(led, exp, cc_s, re_s, test_window, out_dir=d)
        want = lock.get("report_sha256")
        if not want or want != info["report_sha256"]:
            raise Refused([f"{exp}: the lock's report_sha256 is missing or differs from its ledger marker's"])
        report_bytes = rp.read_bytes()
        if hashlib.sha256(report_bytes).hexdigest() != want:
            raise Refused([f"{exp}: {rp} no longer hashes to the report_sha256 in its lock; `report --reprint` restores it"])
        rep = json.loads(report_bytes.decode("utf-8"))
        if rep.get("schema") != SCHEMA_SECONDARY_REPORT or rep.get("mode") != "FINAL" or rep.get("experiment") != exp or rep.get("registration_order") != registered[exp]:
            raise Refused([f"{exp}: {rp} is not that registered secondary's FINAL report"])
        if rep.get("binding", {}).get("registry_sha256") != reg_sha:
            raise Refused([f"{exp}: its report does not share the registry sha256 {reg_sha}"])
        if rep["primary"]["rows_sha256"] != prim["rows_sha256"]:
            raise Refused([f"{exp}: its report was gated on a different EXP-012 read than the ledger's"])
        reports[exp] = rep
    order = sorted(registered, key=registered.get)
    out: dict[str, Any] = {
        "schema": SCHEMA_FAMILY,
        "label": LABEL,
        "clean_clock": cc_s,
        "read_end": re_s,
        "test_window": test_window,
        "k": len(registered),
        "alpha": ff.HOLM_ALPHA,
        "primary": {"experiment": ff.PRIMARY_EXPERIMENT, "verdict": prim["verdict"]},
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if prim["verdict"] != "PASS":
        out["gate_state"] = "closed"
        out["secondaries"] = {e: {"verdict": None, "verdict_note": GATE_CLOSED_NOTE} for e in order}
        out["candidate_order"] = []
    else:
        if any(r["gate_state"] != "open" for r in reports.values()):
            raise Refused(["EXP-012 PASSed but a secondary report says its gate was closed; inconsistent reads"])
        holm = ff.family_holm({e: r["tested_quantity"]["p"] for e, r in reports.items()})
        out["gate_state"] = "open"
        out["secondaries"] = {}
        for e in order:
            ok = bool(reports[e]["own_checks_clear"] and holm[e]["holm_pass"])
            out["secondaries"][e] = {"registration_order": registered[e], "own_checks_clear": reports[e]["own_checks_clear"], "p": reports[e]["tested_quantity"]["p"], "holm": holm[e], "verdict": "PASS" if ok else "FAIL"}
        # DEC-017 point 6: EXP-012 first, then the passing secondaries in registration order. No best-of.
        out["candidate_order"] = [ff.PRIMARY_EXPERIMENT] + [e for e in order if out["secondaries"][e]["verdict"] == "PASS"]
    if test_window:
        out["window_note"] = TEST_WINDOW_BANNER
    atomic_write(output, (json.dumps(out, indent=2, default=str) + "\n").encode("utf-8"))
    return out


# --- CLI -------------------------------------------------------------------------------------


def _refuse(exc: Refused) -> int:
    for r in exc.reasons:
        print(f"REFUSED: {r}", file=sys.stderr)
    return exc.code


def _resolve_spec(args: argparse.Namespace) -> ff.ForwardSpec:
    """The experiment's committed spec (`--spec` overrides the path). EXP-012's equals the constants above."""
    path = Path(args.spec) if getattr(args, "spec", None) else ff.default_spec_path(args.experiment)
    try:
        spec = ff.load_spec(path, test_window=getattr(args, "test_window", False))
    except ff.SpecRefused as exc:
        raise _spec_refused(exc)
    if spec.experiment != args.experiment:
        raise Refused([f"{path} is the spec of {spec.experiment}, not {args.experiment}"])
    return spec


def _add_experiment_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--experiment", default=ff.PRIMARY_EXPERIMENT, help="DEC-017: EXP-012 (default) or a registered secondary")
    p.add_argument("--spec", default=None, help="forward_spec.json (default ARTIFACTS/<exp>/forward_spec.json)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("score", help="score sealed+verified forward hours; append new rows")
    _add_experiment_args(sc)
    sc.add_argument("--walk-dir", required=True)
    sc.add_argument("--clean-clock", default=None, help=f"the spec's clock (pinned {PINNED_CLEAN_CLOCK}); an override needs --test-window")
    sc.add_argument("--test-window", action="store_true", help="allow a non-pinned clean clock / read end; output is marked test_window")
    sc.add_argument("--final-ledger", default=None, help="default: this experiment's own ledger (EXP-012: /data/mal/exp012-forward/FINAL_READS.jsonl; a secondary: /data/mal/forward-family/<exp>/FINAL_READS.jsonl)")
    sc.add_argument("--to", default=None, help="YYYY-MM-DDTHH, exclusive (default: latest sealed+verified hour boundary)")
    sc.add_argument("--read-end", default=None, help=f"exclusive end of the counted window (pinned {PINNED_READ_END}; an override needs --test-window)")
    sc.add_argument("--pool-start", default=None, help="YYYY-MM-DDTHH (default: clean clock hour - 2 x BUFFER_HOURS = 48 h)")
    sc.add_argument("--artifact-dir", default=None, help="default: the spec's artifact dir")
    sc.add_argument("--out-dir", required=True)
    sc.add_argument("--registry", default=str(ff.REGISTRY_PATH), help="secondaries: the family registry whose sha256 the runs record")
    sc.add_argument("--primary-ledger", default=str(ff.PRIMARY_LEDGER), help="secondaries: EXP-012's ledger (a secondary's own ledger may never be it)")
    sc.add_argument("--freeze-commit", default=None, help="default: the spec's freeze commit")
    sc.add_argument("--strict-lines", action="store_true", help="walk 2 (EXP-022): read the walk tape strict and refuse on a NUL / non-JSON line; default off, the DEC-016 forward-1002 FINAL runs without it")
    sc.add_argument("--frozen-manifest-md5", default=None, help="default: the spec's pin, unless --artifact-dir overrides the dir")
    vf = sub.add_parser("verify", help="backfill_verify --content for one hour, append its line to D/verify.jsonl")
    vf.add_argument("--walk-dir", required=True)
    vf.add_argument("--hour", required=True, help="YYYY-MM-DDTHH")
    vf.add_argument("--strict-lines", action="store_true", help="walk 2 (EXP-022): report bad_lines, and a NUL / non-JSON line or a truncated zstd stream makes the hour NOT OK; default off, the verify line is exactly the pre-A8 one")
    vf.add_argument("--report-bad-lines", action="store_true", help="add bad_lines / nul / not_json / non_object / lenient to the verify line without changing its OK status; default off")
    ex = sub.add_parser("export-decisions", help="write decisions.jsonl (mint, mig_ms, score, entered, day only) for the live-readiness comparison")
    ex.add_argument("--experiment", default=ff.PRIMARY_EXPERIMENT)
    ex.add_argument("--out-dir", required=True)
    ex.add_argument("--output", default=None)
    rp = sub.add_parser("report", help="write report.json and report.md from rows.jsonl")
    _add_experiment_args(rp)
    rp.add_argument("--out-dir", required=True)
    rp.add_argument("--walk-dir", default=None)
    rp.add_argument("--clean-clock", default=None)
    rp.add_argument("--read-end", default=None)
    rp.add_argument("--test-window", action="store_true")
    rp.add_argument("--final-ledger", default=None)
    rp.add_argument("--pool-start", default=None)
    rp.add_argument("--reprint", action="store_true", help="re-render the FINAL report from the lock's rows sha256 (no new read)")
    rp.add_argument("--primary-ledger", default=str(ff.PRIMARY_LEDGER), help="secondaries: EXP-012's external FINAL ledger (the gate)")
    rp.add_argument("--registry", default=str(ff.REGISTRY_PATH), help="secondaries: the fixed-k family registry")
    rp.add_argument("--reference-ledger", default=None, help="exit variants: the reference experiment's FINAL ledger (default: EXP-012's --primary-ledger, or that experiment's own)")
    rp.add_argument("--reference-out-dir", default=None, help="exit variants: the reference experiment's locked out-dir (default: EXP-012's, from its ledger marker)")
    fh = sub.add_parser("family_holm", help="Holm-Bonferroni across every registered secondary's FINAL report (DEC-017)")
    fh.add_argument("--out-dir", action="append", required=True, metavar="EXP-###=PATH", help="repeat once per registered secondary")
    fh.add_argument("--output", required=True)
    fh.add_argument("--clean-clock", default=None)
    fh.add_argument("--read-end", default=None)
    fh.add_argument("--test-window", action="store_true")
    fh.add_argument("--primary-ledger", default=str(ff.PRIMARY_LEDGER))
    fh.add_argument("--registry", default=str(ff.REGISTRY_PATH))
    fh.add_argument("--ledger", action="append", default=[], metavar="EXP-###=PATH", help="a secondary's FINAL ledger (default /data/mal/forward-family/<exp>/FINAL_READS.jsonl)")
    args = ap.parse_args(argv)
    try:
        spec = _resolve_spec(args) if args.cmd in ("score", "report") else None
        if spec is not None:
            args.clean_clock = args.clean_clock or spec.clean_clock
            args.read_end = args.read_end or spec.read_end
        if args.cmd in ("report", "score", "family_holm"):
            cc_arg = parse_clock(args.clean_clock or PINNED_CLEAN_CLOCK)
            re_arg = parse_clock(args.read_end or PINNED_READ_END)
            check_window(cc_arg, re_arg, args.test_window)
    except Refused as exc:
        return _refuse(exc)
    if args.cmd == "family_holm":
        try:
            outs: dict[str, Path] = {}
            for item in args.out_dir:
                name, _, path = item.partition("=")
                if not path or name in outs:
                    raise Refused([f"--out-dir needs EXP-###=PATH, once per experiment, got {item!r}"])
                outs[name] = Path(path)
            leds = {k: Path(v) for k, _, v in (x.partition("=") for x in args.ledger)}
            res = run_family_holm(Path(args.registry), outs, Path(args.primary_ledger), cc_arg, re_arg, args.test_window, Path(args.output), leds)
        except Refused as exc:
            return _refuse(exc)
        print(f"family_holm: k={res['k']} gate {res['gate_state']}; candidate order {res['candidate_order']}", file=sys.stderr)
        return 0
    if args.cmd == "export-decisions":
        try:
            check_lock_owner(Path(args.out_dir), args.experiment)
            runs = score_runs(read_rows(Path(args.out_dir) / RUNS_NAME))
            if any(r.get("experiment", ff.PRIMARY_EXPERIMENT) != args.experiment for r in runs):
                raise Refused([f"{Path(args.out_dir) / RUNS_NAME} belongs to another experiment than {args.experiment}"])
            n = export_decisions(Path(args.out_dir), Path(args.output) if args.output else None)
        except Refused as exc:
            return _refuse(exc)
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
                Path(args.final_ledger) if args.final_ledger else ff.default_ledger(spec.experiment),
                spec,
                Path(args.primary_ledger),
                Path(args.registry),
                Path(args.reference_out_dir) if args.reference_out_dir else None,
                Path(args.reference_ledger) if args.reference_ledger else None,
            )
        except Refused as exc:
            return _refuse(exc)
        if rep["mode"] == "INTERIM":
            print(f"INTERIM ({LABEL}): n_rows={rep['n_rows']} n_entered={rep['n_entered']}; no result until the read is FINAL", file=sys.stderr)
        else:
            print(f"FINAL written ({LABEL}); n_entered={rep['n_entered']}", file=sys.stderr)
        return 0
    if args.cmd == "verify":
        rec, appended = run_verify(Path(args.walk_dir), args.hour, args.strict_lines, args.report_bad_lines)
        ok = _line_ok(rec, args.strict_lines)
        print(f"hour {args.hour}: {'OK' if ok else 'NOT OK ' + str(rec['issues'])}; {'appended' if appended else 'identical line already present'}", file=sys.stderr)
        return 0 if ok else 1
    overridden = args.artifact_dir is not None
    try:
        summary = run_score(
            Path(args.walk_dir),
            Path(args.out_dir),
            Path(args.artifact_dir) if overridden else spec.artifact_dir,
            cc_arg,
            parse_clock(args.to) if args.to else None,
            args.freeze_commit or spec.freeze_commit or (None if spec.is_secondary else DEFAULT_FREEZE_COMMIT),
            args.frozen_manifest_md5 if (args.frozen_manifest_md5 or overridden) else spec.frozen_manifest_md5,
            parse_clock(args.pool_start) if args.pool_start else None,
            re_arg,
            args.test_window,
            Path(args.final_ledger) if args.final_ledger else ff.default_ledger(spec.experiment),
            spec,
            Path(args.registry),
            Path(args.primary_ledger),
            args.strict_lines,
        )
    except Refused as exc:
        return _refuse(exc)
    except (SystemExit, Exception) as exc:  # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(summary, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
