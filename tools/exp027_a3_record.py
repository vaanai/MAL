#!/usr/bin/env python3
"""Build EXP-027's A3 record (`EXP027_A3_RECORD`) from research-0's structure-monitor file, exactly as QP-RULING-1102-r4b-a3-record Q4 says.

Ruling: /data/mal/hunt-1008/ops/manager18/QP-RULING-1102-r4b-a3-record.md (sha256 a99cfd10...), "Q4 ... Building it (manager, after the 2026-11-07 daily
run; less than a minute; 0 credits)". Output format: `tools/r4b_read.load_a3` ({schema, checked_from, checked_through, halts: [{at, reason}]}; extra keys ignored).

Rules (Q4, in order):
  - Read only `run_utc` and `halt` of each line of the monitor file (default /data/mal/structure-monitor/daily.jsonl). Any line that does not parse, or lacks a
    parsable run_utc or a halt.flags object, refuses (exit 2, nothing written): fail closed.
  - Every run with W0 <= run_utc <= E is a halt when any of the six flags (EXP-022 section 11) has halt true: one entry per halting flag, at = run_utc,
    reason = "<flag>: <reason verbatim>".
  - It is also a halt when a core rule (boost_share_low or boost_last_slice_early) is unevaluated on two consecutive daily runs, a UTC date with no run counting
    as unevaluated. Implemented per UTC date d from W0's date to E's date: the rule is evaluated on d if any run of d inside [W0, E] has it evaluated. The date
    of E (2026-11-07, only [00:00, 03:00] inside the window) counts only if it has a run inside the window (choice stated here for QP; the first two pins_changed
    halts decide EXP-027 either way). at = run_utc of the second date's last in-window run, or that date's 23:59:59Z (capped at E) when it has none.
  - The first run AFTER E: a pins_changed halt whose reason names a deploy instant (YYYY-MM-DDTHH:MM:SSZ) with W0 <= instant <= E adds a halt at that instant.
  - checked_from = W0 (the monitor ran daily across it; `runs_in_window` lists every in-window run_utc); checked_through = run_utc of the first run at or after E,
    which must exist (else refuse: run it after the 2026-11-07 daily run).
  - Records `source` (path@sha256 of the monitor file as read) and the ruling path; writes OUT once (refuses if it exists) and makes it read-only (0444).

  /data/mal/venv/bin/python -m tools.exp027_a3_record --out /data/mal/structure-monitor/exp027-a3-record.json --interim ARTIFACTS/lab/exp027-a3-interim-halts.json
  -> prints `EXP027_A3_RECORD value: <out>@<sha256>`.

Exit: 0 written, 2 refused. It reads no tape, outcome or decision row; it makes no RPC call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "r4b_a3_v1"
W0 = datetime(2026, 10, 18, 3, tzinfo=timezone.utc)
E = datetime(2026, 11, 7, 3, tzinfo=timezone.utc)
DEFAULT_SRC = Path("/data/mal/structure-monitor/daily.jsonl")
RULING = "/data/mal/hunt-1008/ops/manager18/QP-RULING-1102-r4b-a3-record.md"
SIX_FLAGS = ("pins_changed", "boost_disabled", "boost_share_low", "boost_last_slice_early", "boost_budget_or_slices_changed", "synthetic_share_high")
CORE_RULES = ("boost_share_low", "boost_last_slice_early")
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class Refused(Exception):
    pass


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: Any) -> datetime | None:
    if not isinstance(s, str) or not ISO_RE.fullmatch(s):
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load_runs(text: str) -> list[tuple[datetime, dict[str, Any]]]:
    """[(run_utc, halt.flags)], in file order. Fail closed on any line that does not give both."""
    runs = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError as exc:
            raise Refused(f"line {n} is not JSON") from exc
        t = parse_iso(rec.get("run_utc")) if isinstance(rec, dict) else None
        halt = rec.get("halt") if isinstance(rec, dict) else None
        flags = halt.get("flags") if isinstance(halt, dict) else None
        if t is None or not isinstance(flags, dict):
            raise Refused(f"line {n} has no parsable run_utc and halt.flags")
        for f in SIX_FLAGS:
            v = flags.get(f)
            if v is not None and not (isinstance(v, dict) and isinstance(v.get("halt"), bool)):
                raise Refused(f"line {n}: flag {f} is malformed")
        runs.append((t, flags))
    return runs


def build(runs: Sequence[tuple[datetime, dict[str, Any]]], w0: datetime = W0, e: datetime = E) -> dict[str, Any]:
    inside = [(t, f) for t, f in runs if w0 <= t <= e]
    after = sorted((r for r in runs if r[0] >= e), key=lambda r: r[0])
    if not after:
        raise Refused(f"no monitor run at or after E {iso(e)}: build the record after the 2026-11-07 daily run")
    halts: list[dict[str, str]] = []
    for t, flags in sorted(inside, key=lambda r: r[0]):
        for name in SIX_FLAGS:
            v = flags.get(name)
            if isinstance(v, dict) and v.get("halt") is True:
                halts.append({"at": iso(t), "reason": f"{name}: {v.get('reason', '')}"})
    # core rules unevaluated on two consecutive daily runs (a date with no in-window run is unevaluated)
    by_date: dict[date, list[tuple[datetime, dict[str, Any]]]] = {}
    for t, f in inside:
        by_date.setdefault(t.date(), []).append((t, f))
    d, last = w0.date(), e.date()
    dates = []
    while d <= last:
        if d != e.date() or d in by_date:
            dates.append(d)
        d += timedelta(days=1)
    for rule in CORE_RULES:
        prev_unev = False
        for d in dates:
            runs_d = sorted(by_date.get(d, []), key=lambda r: r[0])
            evaluated = any(isinstance(f.get(rule), dict) and f[rule].get("evaluated") is True for _, f in runs_d)
            if not evaluated and prev_unev:
                at = runs_d[-1][0] if runs_d else min(datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc), e)
                halts.append({"at": iso(at), "reason": f"core_rule_unevaluated: {rule} unevaluated on two consecutive daily runs ({d - timedelta(days=1)}, {d})"})
            prev_unev = not evaluated
    # the first run after E: a pins_changed naming a deploy instant inside [W0, E]
    t_after, f_after = after[0]
    pc = f_after.get("pins_changed")
    if isinstance(pc, dict) and pc.get("halt") is True:
        for s in ISO_RE.findall(str(pc.get("reason", ""))):
            at = parse_iso(s)
            if at is not None and w0 <= at <= e:
                halts.append({"at": s, "reason": f"pins_changed (run {iso(t_after)}, deploy instant): {pc.get('reason', '')}"})
    halts.sort(key=lambda h: (h["at"], h["reason"]))
    return {"schema": SCHEMA, "book": "EXP-027 (r4-b)", "checked_from": iso(w0), "checked_through": iso(t_after), "halts": halts,
            "runs_in_window": [iso(t) for t, _ in sorted(inside, key=lambda r: r[0])], "w0": iso(w0), "e": iso(e), "ruling": RULING}


def check_interim(rec: Mapping[str, Any], interim: Path) -> None:
    """QP-RULING-1102 Addendum 1 A1.1: the post-E record must contain every entry of the pinned interim halt list unchanged (same at, same reason)."""
    try:
        want = json.loads(Path(interim).read_text(encoding="utf-8")).get("halts")
    except (OSError, ValueError, AttributeError) as exc:
        raise Refused(f"interim halt list {interim} unreadable") from exc
    if not isinstance(want, list) or not want:
        raise Refused(f"interim halt list {interim} has no halts")
    have = {(h["at"], h["reason"]) for h in rec["halts"]}
    miss = [h for h in want if not isinstance(h, dict) or (h.get("at"), h.get("reason")) not in have]
    if miss:
        raise Refused(f"{len(miss)} interim halt(s) missing or changed in the built record (a final record without them is false): {miss[:2]}")


def run(src: Path, out: Path, now: datetime | None = None, interim: Path | None = None) -> tuple[str, dict[str, Any]]:
    out = Path(out)
    if out.exists():
        raise Refused(f"{out} exists: write-once")
    raw = Path(src).read_bytes()
    rec = build(load_runs(raw.decode("utf-8")))
    if interim is not None:
        check_interim(rec, interim)
        rec["interim"] = f"{Path(interim).resolve()}@{hashlib.sha256(Path(interim).read_bytes()).hexdigest()}"
    rec["source"] = f"{Path(src).resolve()}@{hashlib.sha256(raw).hexdigest()}"
    rec["built_utc"] = iso(now or datetime.now(timezone.utc))
    body = (json.dumps(rec, indent=1, sort_keys=True) + "\n").encode("utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(body)
    os.chmod(out, 0o444)
    return hashlib.sha256(body).hexdigest(), rec


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="the structure-monitor file (research-0)")
    ap.add_argument("--out", required=True, help="the record to create (must not exist); ruling: /data/mal/structure-monitor/exp027-a3-record.json")
    ap.add_argument("--interim", default=None, help="the pinned interim halt list (ARTIFACTS/lab/exp027-a3-interim-halts.json); refuse unless every entry is in the record unchanged")
    a = ap.parse_args(argv)
    try:
        sha, rec = run(Path(a.src), Path(a.out), interim=Path(a.interim) if a.interim else None)
    except (Refused, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(f"halts in [W0, E]: {len(rec['halts'])}; checked {rec['checked_from']} .. {rec['checked_through']}; runs in window {len(rec['runs_in_window'])}")
    print(f"EXP027_A3_RECORD value: {Path(a.out).resolve()}@{sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
