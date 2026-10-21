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

Line classification is from raw text only (QP-RULING-1022 (3)): total lines; start/trigger/gap lines parsed; lines carrying some other `"type": "<word>"`; UNCLASSIFIED lines (no
`"type"` text, or not ending in `}`; never JSON-parsed). Any unclassified or malformed line gives exit 3 and the output says LOWER BOUND.
Feed gaps by UTC date, from the shadow's own record `{"type": "gap", "kind": slot_jump|silence|feed_restart|socket_reconnect}` (tools/h5_shadow.py `_gap`). Fields kept:
`missed_slots` (slot_jump), `silent_ms` (silence: the time since the last event at DETECTION, about SILENCE_S; one record per silence, none when it ends, so the sum
`silence_detect_ms_total` is NOT A DURATION and is not used for the 1 h rule), `common_down_ms` and `flags_pools` (socket_reconnect: the measured length of a common all-socket outage).
data_loss_ms = 400 x sum(missed_slots) + sum(common_down_ms of flagged socket_reconnect records); `data_loss_over_1h` is that over the whole window (overlap counted twice: fail-closed).
HARD gaps (no duration recorded): restarts (extra start records), missing hour files, any feed_restart record, any silence not followed by a later slot_jump of the same dir inside the
window, any flagged socket_reconnect without a usable common_down_ms.
`--p0-dates D1,D2,..` is the P0 mode: attempts by date (zero-filled) over those whole UTC days, plus the gap counts.

Exit codes: 0 counted cleanly; 2 refused input; 3 counted, but some lines or records were malformed or unclassified (the counts are then a LOWER BOUND).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from tools import arena_h5_score as sc
from tools import h5_shadow as h5

KNOWN_BOOKS = ("h5v1-boostfloor-a1", "h5v2-boostclock-a1", "h5v1-boostfloor-a2", "h5v2-boostclock-a2")
TYPE_RE = re.compile(r'^\{\s*"type"\s*:\s*"([A-Za-z0-9_]+)"')  # anchored: only a line whose FIRST key is "type" is classified; a nested "type" can never select a parse
PARSED_TYPES = ("start", "trigger", "gap")
START_FIELDS = ("type", "start_ms", "t_ms", "code_sha", "fresh_forward", "dirty")
TRIGGER_FIELDS = ("type", "variant", "pool", "t_detect_ms", "h5_look2_sealed")
GAP_FIELDS = ("type", "kind", "t_ms", "silent_ms", "missed_slots", "flags_pools", "common_down_ms")
SLOT_MS = 400  # the lab's slot length for the data-loss measure
P0_MIN_AGE_MS = 600_000
WHITELIST = {"start": START_FIELDS, "trigger": TRIGGER_FIELDS, "gap": GAP_FIELDS}
GAP_KINDS = ("slot_jump", "silence", "feed_restart", "socket_reconnect")
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
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


def read_decision_records(shadow_dir: Path, malformed: collections.Counter, lines: collections.Counter | None = None) -> Iterator[dict]:
    """Whitelisted start / trigger / gap records in file then line order. Classification is by raw text: a line that does not begin `{"type": "<word>"` or does not end in `}` is
    UNCLASSIFIED and never parsed. A trigger's whole record is kept as `_raw` for the executor's parser only; it is never output; a line of another type is counted and never parsed. A start/trigger/gap line that is not strict JSON is counted as malformed."""
    lines = lines if lines is not None else collections.Counter()
    for f in sorted(shadow_dir.glob(HOUR_FILE)):
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                text = line.strip()
                if not text:
                    continue
                lines["total_lines"] += 1
                m = TYPE_RE.match(text)
                if not m or not text.endswith("}"):
                    lines["unclassified_lines"] += 1
                    continue
                if m.group(1) not in PARSED_TYPES:
                    lines["other_type_lines"] += 1
                    continue
                try:
                    rec = json.loads(text, parse_constant=_no_constant)
                except ValueError:
                    malformed["bad_json_lines"] += 1
                    continue
                if not isinstance(rec, dict) or rec.get("type") != m.group(1):
                    malformed["bad_record_shape"] += 1
                    continue
                lines["parsed_lines"] += 1
                yield {k: rec[k] for k in WHITELIST[rec["type"]] if k in rec} | ({"_raw": rec} if rec["type"] == "trigger" else {})


def _usable_ms(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


class DirRead:
    """One shadow dir: its start records, and its pv triggers by pool each with the start record of the run that wrote it."""

    def __init__(self, path: Path, malformed: collections.Counter, lines: collections.Counter | None = None) -> None:
        self.path = path
        self.poisoned: set[str] = set()  # pools whose FIRST pv trigger is unusable: the scorer stops on them, so the tool never counts them
        self.starts: list[dict] = []
        self.triggers: dict[str, dict] = {}
        self.gaps: list[dict] = []
        self.restarts: list[tuple[int, int]] = []  # (start_ms of the extra start record, ms since the last record seen before it)
        self.hours: set[str] = {f.name[len("h5-shadow-"):-len(".jsonl")] for f in path.glob(HOUR_FILE)}
        cur: dict | None = None
        last_t: int | None = None
        for rec in read_decision_records(path, malformed, lines):
            if rec["type"] == "start":
                if self.starts and _usable_ms(rec.get("start_ms", rec.get("t_ms"))):
                    sm = int(rec.get("start_ms", rec.get("t_ms")))
                    self.restarts.append((sm, max(0, sm - last_t) if last_t is not None else 0))
                cur = rec
                self.starts.append(rec)
                continue
            if rec["type"] == "gap":
                if _usable_ms(rec.get("t_ms")):
                    self.gaps.append(rec)
                    last_t = max(last_t or 0, int(rec["t_ms"]))
                else:
                    malformed["gap_without_usable_t_ms"] += 1
                continue
            if _usable_ms(rec.get("t_detect_ms")):
                last_t = max(last_t or 0, int(rec["t_detect_ms"]))
            if rec.get("variant") != sc.VARIANT:
                continue
            pool = rec.get("pool")
            if not (isinstance(pool, str) and pool):
                malformed["pv_trigger_without_pool"] += 1
                continue
            if pool in self.triggers or pool in self.poisoned:
                continue  # the first record of a pool wins, as in the scorer
            if cur is None:
                malformed["trigger_before_any_start"] += 1
                self.poisoned.add(pool)
                continue
            if not _usable_ms(rec.get("t_detect_ms")):
                malformed["trigger_without_usable_t_detect_ms"] += 1
                self.poisoned.add(pool)
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


def count_window(dirs: Sequence[Path], start_ms: int, end_ms: int, frozen_sha: str, registered_at_ms: int, malformed: collections.Counter,
                 lines: collections.Counter | None = None) -> dict[str, Any]:
    """Counts for [start_ms, end_ms) over the dirs. Returns triggers_seen, refused, refused_by_reason, attempts, per_utc_date, refused_dirs (a count)."""
    out: dict[str, Any] = {"triggers_seen": 0, "refused": 0, "refused_by_reason": collections.Counter(), "attempts": 0, "per_utc_date": collections.Counter(), "refused_dirs": 0, "feeding": []}
    feeding: list[DirRead] = []
    for p in sorted(dirs, key=lambda x: x.name):
        d = DirRead(p, malformed, lines)
        if d.identity_problem(frozen_sha, registered_at_ms):
            out["refused_dirs"] += 1
            continue
        feeding.append(d)
    out["feeding"] = feeding
    seen: set[str] = set().union(*(d.poisoned for d in feeding)) if feeding else set()  # poisoned pools are never counted
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


def _hour_ms(h: str) -> int | None:
    try:
        return int(datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def gap_summary(feeding: Sequence[DirRead], start_ms: int, end_ms: int) -> dict[str, Any]:
    """Feed-gap counts by UTC date over [start_ms, end_ms); see the module docstring for the measures and the hard-gap causes. Returns {"by_date", "data_loss_ms", ...}."""
    by_date: dict[str, dict[str, Any]] = {}

    def day(ms: int) -> dict[str, Any]:
        return by_date.setdefault(sc.utc_day(ms), {"gap_records": {}, "silence_detect_ms_total_not_a_duration": 0, "missed_slots_total": 0, "common_down_ms_flagged": 0,
                                                    "common_down_ms_all": 0, "data_loss_ms": 0, "flagged_gap_records": 0, "feed_restart_records": 0, "unresolved_silences": 0,
                                                    "flagged_reconnect_without_common_down": 0, "restarts": 0, "restart_ms_upper_bound": 0, "missing_hour_files": 0,
                                                    "missing_hour_ms": 0, "hard_gap": False})
    for d in feeding:
        inwin = [g for g in d.gaps if start_ms <= int(g["t_ms"]) < end_ms]
        for i, g in enumerate(inwin):
            t = int(g["t_ms"])
            row = day(t)
            kind = g.get("kind") if isinstance(g.get("kind"), str) else "other"
            row["gap_records"][kind] = row["gap_records"].get(kind, 0) + 1
            flagged = bool(g.get("flags_pools"))
            if flagged:
                row["flagged_gap_records"] += 1
            if kind == "silence":
                if _usable_ms(g.get("silent_ms")):
                    row["silence_detect_ms_total_not_a_duration"] += int(g["silent_ms"])
                if not any(h.get("kind") == "slot_jump" for h in inwin[i + 1:]):
                    row["unresolved_silences"] += 1
                    row["hard_gap"] = True
            elif kind == "slot_jump":
                if _usable_ms(g.get("missed_slots")):
                    row["missed_slots_total"] += int(g["missed_slots"])
                    row["data_loss_ms"] += SLOT_MS * int(g["missed_slots"])
            elif kind == "feed_restart":
                row["feed_restart_records"] += 1
                row["hard_gap"] = True
            elif kind == "socket_reconnect":
                cd = g.get("common_down_ms")
                if _usable_ms(cd):
                    row["common_down_ms_all"] += int(cd)
                    if flagged:
                        row["common_down_ms_flagged"] += int(cd)
                        row["data_loss_ms"] += int(cd)
                elif flagged:
                    row["flagged_reconnect_without_common_down"] += 1
                    row["hard_gap"] = True
        for sm, down in d.restarts:
            if start_ms <= sm < end_ms:
                row = day(sm)
                row["restarts"] += 1
                row["restart_ms_upper_bound"] += down
                row["hard_gap"] = True
    have = set().union(*(d.hours for d in feeding)) if feeding else set()
    h = start_ms // HOUR_MS * HOUR_MS
    while h < end_ms:
        name = datetime.fromtimestamp(h / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")
        if name not in have:
            row = day(h)
            row["missing_hour_files"] += 1
            row["missing_hour_ms"] += HOUR_MS
            row["hard_gap"] = True
        h += HOUR_MS
    by = {k: by_date[k] for k in sorted(by_date)}
    total = sum(r["data_loss_ms"] for r in by.values())
    return {"by_date": by, "data_loss_ms": total, "data_loss_over_1h": total > 3_600_000, "hard_gap": any(r["hard_gap"] for r in by.values())}


def _now_ms() -> int:
    return int(time.time() * 1000)


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
    ap.add_argument("--books", required=True, help="comma-separated bot ids; every book gets identical counts (one --frozen-sha / --registered-at-ms per call)")
    ap.add_argument("--start-utc", help="window start, inclusive, YYYY-MM-DDTHH:MM:SSZ")
    ap.add_argument("--p0-dates", help="P0 mode: comma-separated whole UTC dates YYYY-MM-DD (consecutive); the window is those days; replaces --start-utc / --end-*")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--end-ms", type=int, help="window end, exclusive, epoch ms (the halt flag instant)")
    g.add_argument("--end-utc", help="window end, exclusive, YYYY-MM-DDTHH:MM:SSZ")
    ap.add_argument("--arena-root", help="the arena root; only the scorer's own roots are accepted (no override flag; tests patch the scorer's guard)")
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
    p0: list[str] | None = None
    if a.p0_dates:
        if a.start_utc or a.end_ms is not None or a.end_utc:
            raise Refused("--p0-dates replaces --start-utc / --end-ms / --end-utc")
        p0 = [x.strip() for x in a.p0_dates.split(",") if x.strip()]
        days = [parse_utc(x + "T00:00:00Z") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x) else None for x in p0]
        if not days or None in days or any(b - a_ != DAY_MS for a_, b in zip(days, days[1:])):
            raise Refused("--p0-dates must be consecutive YYYY-MM-DD dates")
        start_ms, end_ms = days[0], days[-1] + DAY_MS
        if end_ms > _now_ms() - P0_MIN_AGE_MS:
            raise Refused(f"P0 window end {end_ms} is later than now minus 10 min: the last day is not closed; nothing counted")
    else:
        if not a.start_utc or (a.end_ms is None and not a.end_utc):
            raise Refused("give --start-utc and --end-ms/--end-utc, or --p0-dates")
        start_ms = parse_utc(a.start_utc)
        end_ms = a.end_ms if a.end_ms is not None else parse_utc(a.end_utc)
    if end_ms < start_ms:
        raise Refused(f"window end {end_ms} is before start {start_ms}")
    try:
        root = sc._arena_root(a.arena_root)
    except ValueError as exc:
        raise Refused(str(exc)) from exc
    dirs = _resolve_dirs(root, a.shadow_dir)
    malformed: collections.Counter = collections.Counter()
    lines: collections.Counter = collections.Counter()
    res = count_window(dirs, start_ms, end_ms, a.frozen_sha, a.registered_at_ms, malformed, lines)
    clean = _clean(res)
    line_doc = {k: lines.get(k, 0) for k in ("total_lines", "parsed_lines", "other_type_lines", "unclassified_lines")}
    gs = gap_summary(res["feeding"], start_ms, end_ms)
    gaps = gs["by_date"]
    lower = bool(malformed) or line_doc["unclassified_lines"] > 0
    doc = {"window": {"start_ms": start_ms, "end_ms": end_ms, "half_open": True}, "books": {b: dict(clean) for b in books}, "refused_dirs": res["refused_dirs"],
           "lines": line_doc, "malformed": dict(sorted(malformed.items())), "feed_gaps_by_date": gaps, "hard_gap": gs["hard_gap"], "data_loss_ms": gs["data_loss_ms"],
           "data_loss_over_1h": gs["data_loss_over_1h"],
           "lower_bound": lower, "label": "LOWER BOUND: unclassified or malformed lines present" if lower else "complete for the lines read", "counts_only": True}
    if p0 is not None:
        per = {d: clean["per_utc_date"].get(d, 0) for d in p0}
        avg = sum(per.values()) / len(per)
        doc["p0"] = {"dates": p0, "attempts_by_date": per, "average_per_day": avg, "min_day": min(per.values()), "floor_average_ge_2_0": avg >= 2.0,
                     "floor_no_zero_day": min(per.values()) > 0, "hard_gap": doc["hard_gap"], "data_loss_ms": gs["data_loss_ms"], "data_loss_over_1h": gs["data_loss_over_1h"],
                     "not_decidable_by_gap_rule": bool(doc["hard_gap"] or gs["data_loss_over_1h"])}
    out.write(json.dumps(doc, sort_keys=True) + "\n")
    return 3 if lower else 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(build_parser().parse_args(argv))
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
