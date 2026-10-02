#!/usr/bin/env python3
"""EXP-012 FORWARD scorer (DEC-016): the frozen model on sealed forward getBlock hours.

    python3 -m tools.exp012_forward verify --walk-dir D --hour H
    python3 -m tools.exp012_forward score  --walk-dir D --out-dir OUT [--clean-clock ISO] [--to HOUR] [--pool-start HOUR]
    python3 -m tools.exp012_forward report --out-dir OUT

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
    sha256 is re-checked against the file bytes on every score. The last line for
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
     Rows already written are complete; the conflict check above guards that.
  4. The read's pre-lock repo-state check (tools/ unchanged since the freeze) is not
     applied: this module is new code under tools/ by construction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
DEFAULT_CLEAN_CLOCK = "2026-10-05T05:00:00Z"
DEFAULT_FREEZE_COMMIT = "ea5ec374010378b14a5c19e81bf045679bde73b9"  # EXP-012 section 12 (Part 2)
ROWS_NAME = "rows.jsonl"
RUNS_NAME = "runs.jsonl"
VERIFY_NAME = "verify.jsonl"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_DIR = REPO_ROOT / "ARTIFACTS" / "exp012"
LAMPORTS = 1_000_000_000
HOUR_FMT = "%Y-%m-%dT%H"


class Refused(Exception):
    def __init__(self, reasons: Sequence[str]):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


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
    if not isinstance(sha, dict) or not isinstance(sha.get("trades"), str):
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
        else:
            for sub, digest in sorted(ok[h]["sha256"].items()):
                f = _hour_file(walk_dir / sub, sub, h)
                if f is None or _sha256_file(f) != digest:
                    out.append(f"hour {h}: {sub} bytes do not match the sha256 in {VERIFY_NAME}")
    return out


def verify_line(walk_dir: Path, hour: str) -> dict[str, Any]:
    """`tools.backfill_verify` with --content for [hour, hour+1), plus the sha256 of each sealed file."""
    nxt = hour_key(hour_dt(hour) + timedelta(hours=1))
    report = bv.build_report(walk_dir, hour, nxt, content=True, dedupe_out=None, min_slots_per_hour=9_000, max_slots_per_hour=14_000)
    rec = report["hours"][0]
    if rec["files"].get("trades") is None and "sealed_with_no_trades_file" not in rec["issues"]:
        rec["issues"].append("no_trades_file")
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
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


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
                conflicts.append(f"row {k[0]} @ {k[1]} differs from the stored row")
        else:
            have[k] = _dump(r)
            new.append(r)
    return new, conflicts


def run_score(walk_dir: Path, out_dir: Path, artifact_dir: Path, clean_clock: datetime, to: datetime | None, freeze_commit: str, frozen_manifest_md5: str | None = None, pool_start: datetime | None = None) -> dict[str, Any]:
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

    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path, runs_path = out_dir / ROWS_NAME, out_dir / RUNS_NAME
    cc_s = clean_clock.strftime("%Y-%m-%dT%H:%M:%SZ")
    prior_runs = read_rows(runs_path)
    if any(r.get("clean_clock") != cc_s for r in prior_runs):
        raise Refused([f"{runs_path} was written under a different clean clock; use a new --out-dir"])

    t0 = time.time()
    rows, threshold = score_hours(walk_dir, pool, artifact_dir, out_dir / "scratch")
    lo, hi = ms(clean_clock), ms(to)
    fresh = [make_row(r, threshold) for r in rows if lo <= int(r["mig_ms"]) < hi]
    existing = read_rows(rows_path)
    new, conflicts = merge_rows(existing, fresh)
    if conflicts:
        raise Refused(["stored rows would change (nothing appended): " + "; ".join(conflicts[:5])])
    if new:
        with rows_path.open("a", encoding="utf-8") as fh:
            for r in new:
                fh.write(_dump(r) + "\n")
    summary = {
        "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "clean_clock": cc_s,
        "pool_from": pool[0],
        "to_exclusive": hour_key(to),
        "n_hours": len(pool),
        "n_rows_seen_in_range": len(fresh),
        "n_appended": len(new),
        "n_total": len(existing) + len(new),
        "model_md5": (artifact_dir / "model.md5").read_text(encoding="utf-8").strip(),
        "frozen_manifest_md5": _md5_of_file(artifact_dir / FROZEN_MANIFEST_NAME),
        "threshold": threshold,
        "code_commit": _git_commit(),
        "runtime_s": round(time.time() - t0, 1),
    }
    with runs_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, sort_keys=True) + "\n")
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
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def render_markdown(rep: dict[str, Any]) -> str:
    L = [f"# EXP-012 forward report: {rep['label']}", "", f"VERDICT: {rep['verdict']} ({rep['verdict_note']})", ""]
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
    L += ["", f"Result label: {rep['label']}."]
    return "\n".join(L) + "\n"


def run_report(out_dir: Path) -> dict[str, Any]:
    rows = read_rows(out_dir / ROWS_NAME)
    rep = build_report(rows, read_rows(out_dir / RUNS_NAME))
    (out_dir / "report.json").write_text(json.dumps(rep, indent=2, default=str) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(rep), encoding="utf-8")
    return rep


# --- CLI -------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("score", help="score sealed+verified forward hours; append new rows")
    sc.add_argument("--walk-dir", required=True)
    sc.add_argument("--clean-clock", default=DEFAULT_CLEAN_CLOCK)
    sc.add_argument("--to", default=None, help="YYYY-MM-DDTHH, exclusive (default: latest sealed+verified hour boundary)")
    sc.add_argument("--pool-start", default=None, help="YYYY-MM-DDTHH (default: clean clock hour - 2 x BUFFER_HOURS = 48 h)")
    sc.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    sc.add_argument("--out-dir", required=True)
    sc.add_argument("--freeze-commit", default=DEFAULT_FREEZE_COMMIT)
    sc.add_argument("--frozen-manifest-md5", default=None)
    vf = sub.add_parser("verify", help="backfill_verify --content for one hour, append its line to D/verify.jsonl")
    vf.add_argument("--walk-dir", required=True)
    vf.add_argument("--hour", required=True, help="YYYY-MM-DDTHH")
    rp = sub.add_parser("report", help="write report.json and report.md from rows.jsonl")
    rp.add_argument("--out-dir", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "report":
        rep = run_report(Path(args.out_dir))
        print(f"VERDICT: {rep['verdict']} ({LABEL}); n_entered={rep['n_entered']}", file=sys.stderr)
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
            parse_clock(args.clean_clock),
            parse_clock(args.to) if args.to else None,
            args.freeze_commit,
            args.frozen_manifest_md5,
            parse_clock(args.pool_start) if args.pool_start else None,
        )
    except Refused as exc:
        for r in exc.reasons:
            print(f"REFUSED: {r}", file=sys.stderr)
        return 2
    except (SystemExit, Exception) as exc:  # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(summary, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
