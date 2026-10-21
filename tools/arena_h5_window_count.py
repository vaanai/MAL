"""Counts-only: the H5 arena shadow's decided attempts in a half-open UTC window [start, end).

Spec: A2-RESTART-PACK step 5 / QP repin Q3 item 5. Prints COUNTS ONLY: per book the attempts, the refused triggers (with reason counts), the triggers seen and the attempts per UTC
day; plus counts of lines it could not read. It prints no pool, mint, signature, price, P&L, exit, fill or outcome field, and it never parses an outcome record.

What it reads: the hourly decision logs `h5-shadow-<hour>.jsonl` of the --shadow-dir(s), and only lines whose raw text says `"type": "start"` or `"type": "trigger"` (a regex
pre-filter runs BEFORE json.loads, so outcome / outcome_bx10 / strip / pool lines are never parsed). Of a start record it keeps the identity fields the scorer's
`identity_problem` uses; of a trigger record the decision timestamp (t_detect_ms), the variant, the pool (for de-duplication only) and the sealed flag. The executor's own parser
(arena_h5_score.executor_refusal) decides refusal from the decision-time trigger record, exactly as the scorer does.
What it never reads: any arena ledger (trades-*, exclusions-*, refusals-*), bots.json (the registered sha and registered_at_ms are command-line values), outcome columns.

The attempt rules are the scorer's `score_book` (tools/arena_h5_score.py): one entry per pool across dirs, pv triggers only, decision >= the run's start_ms, sealed-look-2 triggers
not counted, refused triggers are not attempts. A dir whose start records fail the identity check feeds nothing (reported in `refused_dirs` as a count).

Exit codes: 0 counted cleanly; 2 refused input; 3 counted, but some lines or records were malformed (listed in `malformed`; the counts are then NOT complete).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from tools import arena_h5_score as sc
from tools import h5_shadow as h5

KNOWN_BOOKS = ("h5v1-boostfloor-a1", "h5v2-boostclock-a1", "h5v1-boostfloor-a2", "h5v2-boostclock-a2")
KIND_RE = re.compile(r'"type"\s*:\s*"(start|trigger)"')
START_FIELDS = ("type", "start_ms", "code_sha", "fresh_forward", "dirty")
TRIGGER_FIELDS = ("type", "variant", "pool", "t_detect_ms", "h5_look2_sealed")
HOUR_FILE = "h5-shadow-????-??-??T??.jsonl"


class Refused(ValueError):
    """Input refused: exit 2, nothing counted."""


def parse_utc(text: str) -> int:
    t = text.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", t):
        raise Refused(f"not an instant of the form YYYY-MM-DDTHH:MM:SSZ: {text!r}")
    return int(datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _no_constant(name: str) -> Any:
    raise ValueError(f"non-strict JSON constant {name}")


def read_decision_records(shadow_dir: Path, malformed: collections.Counter) -> Iterator[dict]:
    """Whitelisted start / trigger records in file then line order. Lines of any other type are not parsed. A matching line that is not strict JSON is counted."""
    for f in sorted(shadow_dir.glob(HOUR_FILE)):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                m = KIND_RE.search(line)
                if not m:
                    continue
                try:
                    rec = json.loads(line, parse_constant=_no_constant)
                except ValueError:
                    malformed["bad_json_lines"] += 1
                    continue
                if not isinstance(rec, dict) or rec.get("type") != m.group(1):
                    malformed["bad_record_shape"] += 1
                    continue
                keep = START_FIELDS if rec["type"] == "start" else TRIGGER_FIELDS
                yield {k: rec[k] for k in keep if k in rec} | ({"_raw": rec} if rec["type"] == "trigger" else {})


def _usable_ms(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


class DirRead:
    """One shadow dir: its start records, and its pv triggers by pool each with the start record of the run that wrote it."""

    def __init__(self, path: Path, malformed: collections.Counter) -> None:
        self.path = path
        self.starts: list[dict] = []
        self.triggers: dict[str, dict] = {}
        cur: dict | None = None
        for rec in read_decision_records(path, malformed):
            if rec["type"] == "start":
                cur = rec
                self.starts.append(rec)
                continue
            if rec.get("variant") != sc.VARIANT:
                continue
            pool = rec.get("pool")
            if not (isinstance(pool, str) and pool):
                malformed["pv_trigger_without_pool"] += 1
                continue
            if cur is None:
                malformed["trigger_before_any_start"] += 1
                continue
            if not _usable_ms(rec.get("t_detect_ms")):
                malformed["trigger_without_usable_t_detect_ms"] += 1
                continue
            self.triggers.setdefault(pool, {"rec": rec, "start": cur})

    def identity_problem(self, frozen_sha: str, registered_at_ms: int) -> str | None:
        if not self.starts:
            return "no_start_record"
        for s in self.starts:
            if s.get("fresh_forward") != h5.FRESH_FORWARD_REF:
                return "not_fresh_forward"
            if not s.get("code_sha"):
                return "null_code_sha"
            if s["code_sha"] != frozen_sha:
                return "sha_mismatch"
            if s.get("dirty"):
                return "dirty"
            if not _usable_ms(s.get("start_ms")) or int(s["start_ms"]) < registered_at_ms:
                return "started_before_registration"
        return None


def count_window(dirs: Sequence[Path], start_ms: int, end_ms: int, frozen_sha: str, registered_at_ms: int, malformed: collections.Counter) -> dict[str, Any]:
    """Counts for [start_ms, end_ms) over the dirs. Returns triggers_seen, refused, refused_by_reason, attempts, per_utc_date, refused_dirs (a count)."""
    out: dict[str, Any] = {"triggers_seen": 0, "refused": 0, "refused_by_reason": collections.Counter(), "attempts": 0, "per_utc_date": collections.Counter(), "refused_dirs": 0}
    feeding: list[DirRead] = []
    for p in sorted(dirs, key=lambda x: x.name):
        d = DirRead(p, malformed)
        if d.identity_problem(frozen_sha, registered_at_ms):
            out["refused_dirs"] += 1
            continue
        feeding.append(d)
    seen: set[str] = set()
    for d in feeding:
        for pool, t in sorted(d.triggers.items(), key=lambda kv: kv[1]["rec"]["t_detect_ms"]):
            rec = t["rec"]
            decision = int(rec["t_detect_ms"])
            if decision < start_ms or decision < int(t["start"]["start_ms"]) or decision >= end_ms:
                continue
            if rec.get("h5_look2_sealed"):
                continue
            if pool in seen:
                continue
            seen.add(pool)
            out["triggers_seen"] += 1
            try:
                refusal = sc.executor_refusal(rec["_raw"])  # decision-time record in, a refusal string or None out
            except sc.ParserFault:
                malformed["parser_fault"] += 1
                continue
            if refusal is not None:
                out["refused"] += 1
                out["refused_by_reason"][refusal] += 1
                continue
            out["attempts"] += 1
            out["per_utc_date"][sc.utc_day(decision)] += 1
    return out


def _resolve_dirs(root: Path, names: Sequence[str]) -> list[Path]:
    dirs = []
    for n in names:
        real = Path(os.path.realpath(n if os.path.isabs(n) else root / n))
        if real == root or root not in real.parents:
            raise Refused(f"--shadow-dir {n!r} must be a directory under the arena root {root}")
        if not real.is_dir():
            raise Refused(f"--shadow-dir {n!r} is not a directory")
        dirs.append(real)
    return dirs


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {"attempts": d["attempts"], "refused": d["refused"], "refused_by_reason": dict(sorted(d["refused_by_reason"].items())), "triggers_seen": d["triggers_seen"],
            "per_utc_date": dict(sorted(d["per_utc_date"].items())), "total": d["attempts"]}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Counts-only: H5 arena decided attempts in a half-open UTC window [start, end).")
    ap.add_argument("--books", required=True, help="comma-separated bot ids")
    ap.add_argument("--start-utc", required=True, help="window start, inclusive, YYYY-MM-DDTHH:MM:SSZ")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--end-ms", type=int, help="window end, exclusive, epoch ms (the halt flag instant)")
    g.add_argument("--end-utc", help="window end, exclusive, YYYY-MM-DDTHH:MM:SSZ")
    ap.add_argument("--arena-root", help="the arena root; only the scorer's own roots are accepted unless --allow-root")
    ap.add_argument("--allow-root", action="store_true", help="accept an arena root other than the scorer's (tests only)")
    ap.add_argument("--shadow-dir", action="append", required=True, help="a shadow dir under the arena root (repeat)")
    ap.add_argument("--frozen-sha", required=True, help="the books' registered frozen_sha (copied, not read from bots.json)")
    ap.add_argument("--registered-at-ms", required=True, type=int, help="the books' registered_at_ms (copied, not read from bots.json)")
    return ap


def run(a: argparse.Namespace, out=None) -> int:
    out = out or sys.stdout
    books = [b for b in (x.strip() for x in a.books.split(",")) if b]
    if not books or len(set(books)) != len(books):
        raise Refused("--books must be a non-empty list of distinct bot ids")
    unknown = [b for b in books if b not in KNOWN_BOOKS]
    if unknown:
        raise Refused(f"unknown bot id(s) {unknown}; known: {list(KNOWN_BOOKS)}")
    start_ms = parse_utc(a.start_utc)
    end_ms = a.end_ms if a.end_ms is not None else parse_utc(a.end_utc)
    if end_ms < start_ms:
        raise Refused(f"window end {end_ms} is before start {start_ms}")
    if a.allow_root:
        if not a.arena_root:
            raise Refused("--allow-root needs --arena-root")
        root = Path(os.path.realpath(a.arena_root))
    else:
        try:
            root = sc._arena_root(a.arena_root)
        except ValueError as exc:
            raise Refused(str(exc)) from exc
    dirs = _resolve_dirs(root, a.shadow_dir)
    malformed: collections.Counter = collections.Counter()
    res = count_window(dirs, start_ms, end_ms, a.frozen_sha, a.registered_at_ms, malformed)
    clean = _clean(res)
    doc = {"window": {"start_ms": start_ms, "end_ms": end_ms, "half_open": True}, "books": {b: dict(clean) for b in books}, "refused_dirs": res["refused_dirs"],
           "malformed": dict(sorted(malformed.items())), "counts_only": True}
    out.write(json.dumps(doc, sort_keys=True) + "\n")
    return 3 if malformed else 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(build_parser().parse_args(argv))
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
