#!/usr/bin/env python3
"""FLOOR (l1-floor-v2) read instant, DEC-031 Amendment 12 items 1, 3 and 6. Outcome-blind. Decision timestamps and row kind only. PAPER ONLY.

    python -m tools.floor_read_instant --mode screen  --ledger-root DIR --clock-ms MS --health T.jsonl --health-sha SHA [...] --restarts R.jsonl --as-of-ms MS [--out FILE]
    python -m tools.floor_read_instant --mode confirm --screen-record SCREEN.json   (same other flags)                                       [--out FILE]

RULE (Amendment 12 item 1). The read instant is the first 00:00Z boundary B after the clock such that, among the attempts decided in [clock_start_ms, B) and not
dropped by Amendment 6 as decided in an excluded hour, the non-refused attempts O (kind `open`) and the refused buys R (kind `refused`, any reason) satisfy
    O - floor(0.05 x (O + R)) >= 100
and the decision dates (UTC) of the O attempts cover at least 5 distinct dates. floor(0.05 x (O + R)) is computed exactly in integers as (O + R) // 20.
Horizon (Amendment 9 item 3, unchanged by item 1): END_H = clock + 12 d; no attempt decided at or after END_H counts; B may be at most END_H (the attempts of
[clock, END_H) are all before END_H: the "has not come by clock + H" of Amendment 4 item 2.3, and arena_am6_read's NOT_READY test `as_of_ms < b` at as-of END_H).
No such B <= END_H, once --as-of-ms >= END_H: NOT_DECIDABLE, final, N + 1. Before that: NOT_READY.
CONFIRMATION (item 3): the same margin condition over the attempts decided in [B1, B2), B1 = the screen record's instant, at the first 00:00Z boundary
B2 > B1. Its O dates are therefore all "further" dates (>= B1). The same END_H applies (Amendment 9 item 3: no attempt at or after END_H counts).

INPUTS, and the only fields used:
  - the ledger: exactly the lines arena_score.load_decisions reads at its pinned sha256 a508ed79 (frozen sha 5e12f8f5): files <root>/<bot>/trades-*.jsonl, sorted
    by name, lines that START WITH '{"kind":"open"' or '{"kind":"refused"'. Of those, only the kind (from that prefix) and decision_ms.entry are used. A line of
    any other kind (trade, excluded, late_print, start, ...: closes, exclusion classes, fills, exits, legs, P&L) is never parsed. A torn or unparseable decision
    line refuses, as in load_decisions. No de-duplication, as in load_decisions.
  - the Amendment 6 hour tables: the notebooked daily tables of tools/arena_hour_health.py, each with its notebooked sha256 (--health / --health-sha, repeatable),
    merged as arena_am6_read.merge_health does (recomputed sha256 per table, the set of shas must equal the expected set, an hour in two tables must be identical).
  - the restart gaps (--restarts, required; an empty file means none): [start_ms, end_ms] per line, rounded outward to whole UTC hours, as
    arena_am6_exclusions.classify does.
  An attempt is DROPPED (decided in an excluded hour) iff its decision hour is excluded in the merged table or lies in a restart gap: exactly class 1 of
  arena_am6_exclusions.classify (S_10). A decision hour absent from the table is unmeasurable and refuses (never silently counted), as in classify. Hours are
  only checked for attempts that a tested boundary needs (attempts after the first B are never looked at).

OUTPUT: a status line and counts only (O, R, margin, distinct O dates, kept O / R per UTC date, dropped). No mint, wallet, signature or position id.
--out FILE writes the record ONCE (os.open O_CREAT | O_EXCL, mode 0444): a second write to the same path is refused. Only a final status is written
(READ_INSTANT or NOT_DECIDABLE); NOT_READY writes nothing. Without --out the output is not a record.
Exit codes: 0 status printed (NOT_READY included), 2 refused.

SCOPE AND LIMITS (stated):
  - This tool fixes the instant only. It does not score. arena_score (a508ed79) and arena_am6_read (ef6e4555) are not changed by this tool; they compute the
    UNAMENDED boundary. Amendment 12 item 6: a boundary those tools print is an attempt-boundary record, not FLOOR's read instant. Scoring FLOOR at this tool's
    B needs its own reviewed path (a pin change of either tool, disclosed at the read).
  - The CLI accepts only FLOOR's bot id and its two registered clock candidates (2026-10-26T00Z, slip 2026-10-27T00Z). --as-of-ms may not be in the future.
  - Whether a confirmation is allowed at all (a screen PASS, quant-proof, owner OK) is not checked here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from tools import arena_am6_exclusions as X

SCHEMA = "floor_read_instant_v1"
BOT_ID = "l1-floor-v2"
CLOCKS_MS = (1_792_972_800_000, 1_793_059_200_000)  # 2026-10-26T00:00:00Z; slip 2026-10-27T00:00:00Z (DEC-031 Am.9 Erratum 1 item 8)
H_DAYS = 12  # DEC-031 Am.9 item 3
DAY_MS = 86_400_000
HOUR_MS = X.HOUR_MS
MIN_MARGIN = 100
MIN_O_DATES = 5
CAP_DIV = 20  # floor(0.05 x n) == n // 20 for integer n >= 0
PINNED_ARENA_SCORE_SHA256 = "a508ed79ed1cc425af0aa7dcd08e3ef5f8599dd6a45cb5e47cc8a779c6a0cd34"  # load_decisions read from here (blob at 5e12f8f5)
PINNED_AM6_READ_SHA256 = "ef6e4555bc9e49272a5764bd75e1510f210af669a7a974cc68042b7eaaef32b3"
OPEN_PREFIX = '{"kind":"open"'
REFUSED_PREFIX = '{"kind":"refused"'
ST_READY, ST_NOT_READY, ST_ND = "READ_INSTANT", "NOT_READY", "NOT_DECIDABLE"


class Refused(Exception):
    pass


def canon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), allow_nan=False)


def utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def file_sha256(p: str | Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ------------------------------------------------------------------------------------------------------------------------------ decision rows
def load_decision_rows(ledger_root: str | Path, bot_id: str) -> list[tuple[str, int]]:
    """(kind, decision_ms.entry) of every open row and refused buy: the same files, lines, order and field as arena_score.load_decisions at a508ed79, with
    the kind taken from the same line prefix. No other line is parsed."""
    out: list[tuple[str, int]] = []
    d = Path(ledger_root) / bot_id
    for p in sorted(d.glob("trades-*.jsonl")) if d.exists() else []:
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if line.startswith(OPEN_PREFIX):
                kind = "open"
            elif line.startswith(REFUSED_PREFIX):
                kind = "refused"
            else:
                continue
            try:
                out.append((kind, int(json.loads(line)["decision_ms"]["entry"])))
            except (ValueError, KeyError, TypeError):
                raise Refused(f"{bot_id}: {p.name} line {i} is a torn or unparseable decision line: the read instant cannot be computed") from None
    return out


def decisions_sha256(rows: Iterable[tuple[str, int]]) -> str:
    """Commitment to exactly the inputs used: sorted (entry, kind) pairs, one canonical line each."""
    body = "".join(canon([e, k]) + "\n" for k, e in sorted(rows, key=lambda r: (r[1], r[0])))
    return hashlib.sha256(body.encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------------------------------ Amendment 6 hour drop
def merge_health(texts: Sequence[str], expected_shas: Sequence[str]) -> tuple[dict[int, tuple[bool, frozenset]], str, list[str]]:
    """The notebooked daily hour tables, merged with arena_am6_read.merge_health's checks. Returns (table, merged sha256 in that tool's format, table shas)."""
    if not texts or not expected_shas:
        raise Refused("at least one --health table and its --health-sha are required")
    merged: dict[int, tuple[bool, frozenset]] = {}
    shas = []
    for t in texts:
        try:
            table, sha = X.load_health(t)
        except X.Refused as ex:
            raise Refused(str(ex)) from None
        shas.append(sha)
        for h, v in table.items():
            if h in merged and merged[h] != v:
                raise Refused(f"hour {h} differs between two health tables")
            merged[h] = v
    if sorted(shas) != sorted(expected_shas):
        raise Refused(f"health table sha256 {sorted(shas)} != expected {sorted(expected_shas)}")
    lines = [canon({"hour": utc(h), "excluded": ex, "labels_failing": sorted(lb)}) for h, (ex, lb) in sorted(merged.items())]
    msha = hashlib.sha256("".join(ln + "\n" for ln in lines).encode()).hexdigest()
    return merged, msha, shas


def restart_gap_hours(restarts: Sequence[tuple[int, int]]) -> set[int]:
    """Restart gaps rounded outward to whole UTC hours, exactly as arena_am6_exclusions.classify."""
    gap: set[int] = set()
    for s, e in restarts:
        h = s // HOUR_MS * HOUR_MS
        last = max(h, (e - 1) // HOUR_MS * HOUR_MS)
        while h <= last:
            gap.add(h)
            h += HOUR_MS
    return gap


def hour_dropper(table: Mapping[int, tuple[bool, frozenset]], restarts: Sequence[tuple[int, int]]) -> Callable[[int], bool]:
    """decided_ms -> True if the attempt is decided in an excluded hour (class S_10 of arena_am6_exclusions.classify). Refuses on an unmeasurable hour."""
    gap = restart_gap_hours(restarts)

    def dropped(decided_ms: int) -> bool:
        h = decided_ms // HOUR_MS * HOUR_MS
        if h not in table:
            raise Refused(f"decision hour {utc(h)} absent from the health table (unmeasurable)")
        return h in gap or table[h][0]

    return dropped


# ------------------------------------------------------------------------------------------------------------------------------ the rule
def margin(o: int, r: int) -> int:
    """O - floor(0.05 x (O + R)), exact."""
    if o < 0 or r < 0:
        raise ValueError("negative count")
    return o - (o + r) // CAP_DIV


def find_instant(rows: Sequence[tuple[str, int]], start_ms: int, end_h_ms: int, as_of_ms: int, dropped: Callable[[int], bool]) -> dict:
    """The first 00:00Z boundary B in (start_ms, min(END_H, last 00:00Z <= as_of)] that meets the margin and O-date conditions over the non-dropped attempts
    decided in [start_ms, B). rows: (kind, decided_ms). Returns a dict with status READ_INSTANT / NOT_READY / NOT_DECIDABLE and the counts at B (or at the
    last boundary tested)."""
    if start_ms % DAY_MS or end_h_ms % DAY_MS:
        raise Refused("start and END_H must be 00:00Z instants")
    if not start_ms < end_h_ms:
        raise Refused("start must be before END_H")
    for k, _ in rows:
        if k not in ("open", "refused"):
            raise Refused(f"unknown kind {k!r}")
    win = sorted((e, k) for k, e in rows if start_ms <= e < end_h_ms)
    last_b = min(end_h_ms, as_of_ms // DAY_MS * DAY_MS)
    o = r = n_drop = 0
    o_by_date: dict[str, int] = {}
    r_by_date: dict[str, int] = {}
    i = 0
    b = start_ms + DAY_MS
    tested = None
    while b <= last_b:
        while i < len(win) and win[i][0] < b:
            e, k = win[i]
            i += 1
            if dropped(e):
                n_drop += 1
                continue
            dk = utc_date(e)
            if k == "open":
                o += 1
                o_by_date[dk] = o_by_date.get(dk, 0) + 1
            else:
                r += 1
                r_by_date[dk] = r_by_date.get(dk, 0) + 1
        tested = b
        if margin(o, r) >= MIN_MARGIN and len(o_by_date) >= MIN_O_DATES:
            break
        b += DAY_MS
    else:
        b = None
    if b is not None:
        status = ST_READY
    elif as_of_ms >= end_h_ms:
        status = ST_ND
    else:
        status = ST_NOT_READY
    return {"status": status, "read_instant_ms": b, "read_instant_utc": utc(b) if b is not None else None, "last_boundary_tested_ms": tested,
            "start_ms": start_ms, "end_h_ms": end_h_ms, "as_of_ms": as_of_ms, "O": o, "R": r, "margin": margin(o, r), "cap_floor": (o + r) // CAP_DIV,
            "o_dates": len(o_by_date), "kept_open_by_utc_date": dict(sorted(o_by_date.items())), "kept_refused_by_utc_date": dict(sorted(r_by_date.items())),
            "n_dropped_excluded_hour": n_drop, "n_attempts_considered": i,
            "attempts_sha256": decisions_sha256([(k, e) for e, k in win[:i]])}


# ------------------------------------------------------------------------------------------------------------------------------ record
def write_once(path: str | Path, rec: Mapping) -> str:
    """Write the record with O_CREAT | O_EXCL (refused if the path exists), mode 0444, fsync. Returns the sha256 of the bytes written."""
    data = (canon(rec) + "\n").encode()
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        raise Refused(f"{path} exists: the read instant record is write-once") from None
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return hashlib.sha256(data).hexdigest()


def load_screen_record(path: str | Path, clock_ms: int, bot_id: str) -> int:
    try:
        rec = json.loads(Path(path).read_text())
    except (OSError, ValueError) as ex:
        raise Refused(f"screen record unreadable: {ex}") from None
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA or rec.get("mode") != "screen" or rec.get("status") != ST_READY:
        raise Refused("screen record is not a READ_INSTANT screen record of this tool")
    if rec.get("bot_id") != bot_id or rec.get("clock_ms") != clock_ms:
        raise Refused("screen record is for another bot or clock")
    b1 = rec.get("read_instant_ms")
    if not isinstance(b1, int) or isinstance(b1, bool) or b1 % DAY_MS or not clock_ms < b1 <= clock_ms + H_DAYS * DAY_MS:
        raise Refused("screen record read_instant_ms invalid")
    return b1


def compute(ledger_root: str, bot_id: str, clock_ms: int, mode: str, health_texts: Sequence[str], health_shas: Sequence[str], restarts_text: str,
            as_of_ms: int, screen_b_ms: int | None = None) -> dict:
    if mode not in ("screen", "confirm"):
        raise Refused(f"mode {mode!r}")
    if clock_ms % DAY_MS:
        raise Refused("clock must be a 00:00Z instant")
    end_h = clock_ms + H_DAYS * DAY_MS
    if mode == "screen":
        if screen_b_ms is not None:
            raise Refused("screen mode takes no screen instant")
        start = clock_ms
    else:
        if screen_b_ms is None:
            raise Refused("confirm mode needs the screen record's instant")
        start = screen_b_ms
        if start >= end_h:
            raise Refused("screen instant at END_H: no confirmation attempt can count (Am.9 item 3)")
    if restarts_text is None:
        raise Refused("--restarts is required (an empty file means no restarts)")
    table, msha, shas = merge_health(health_texts, health_shas)
    try:
        restarts = X.load_restarts(restarts_text)
    except X.Refused as ex:
        raise Refused(str(ex)) from None
    rows = load_decision_rows(ledger_root, bot_id)
    res = find_instant(rows, start, end_h, as_of_ms, hour_dropper(table, restarts))
    res.update({"schema": SCHEMA, "mode": mode, "bot_id": bot_id, "clock_ms": clock_ms, "clock_utc": utc(clock_ms), "end_h_utc": utc(end_h),
                "screen_instant_ms": screen_b_ms, "health_table_sha256": sorted(shas), "health_merged_sha256": msha,
                "restarts_sha256": hashlib.sha256(restarts_text.encode()).hexdigest(),
                "rule": "first 00:00Z B with O - floor(0.05*(O+R)) >= 100 and >= 5 distinct O decision dates; Am.6 excluded-hour attempts dropped; B <= END_H"})
    return res


def render(res: Mapping) -> list[str]:
    head = f"FLOOR READ INSTANT ({res['mode']})  bot {res['bot_id']}  clock {res['clock_utc']}  END_H {res['end_h_utc']}  as_of {utc(res['as_of_ms'])}"
    if res["status"] == ST_READY:
        st = f"{ST_READY} {res['read_instant_utc']} ({res['read_instant_ms']})"
    elif res["status"] == ST_ND:
        st = f"{ST_ND}: no 00:00Z boundary <= END_H meets the margin; final for the bot_id, N + 1"
    else:
        st = f"{ST_NOT_READY}: no boundary yet (last tested {utc(res['last_boundary_tested_ms']) if res['last_boundary_tested_ms'] else 'none'})"
    return [head, st,
            f"O {res['O']}  R {res['R']}  floor(0.05*(O+R)) {res['cap_floor']}  margin {res['margin']} (need >= {MIN_MARGIN})  O dates {res['o_dates']} (need >= {MIN_O_DATES})",
            "kept_open_by_utc_date " + canon(res["kept_open_by_utc_date"]),
            "kept_refused_by_utc_date " + canon(res["kept_refused_by_utc_date"]),
            f"dropped (decided in an excluded hour) {res['n_dropped_excluded_hour']}  attempts considered {res['n_attempts_considered']}  attempts_sha256 {res['attempts_sha256']}",
            f"health {','.join(res['health_table_sha256'])}  merged {res['health_merged_sha256']}  restarts {res['restarts_sha256']}",
            f"tool sha256 {res.get('tool_sha256')}  arena_am6_exclusions sha256 {res.get('am6_exclusions_sha256')}"]


def main(argv: Sequence[str] | None = None, now_ms: int | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", required=True, choices=("screen", "confirm"))
    ap.add_argument("--ledger-root", required=True)
    ap.add_argument("--clock-ms", required=True, type=int, help="FLOOR's registered clock: 1792972800000 (10-26) or 1793059200000 (slip 10-27)")
    ap.add_argument("--health", required=True, action="append", help="a notebooked daily hour table (repeatable)")
    ap.add_argument("--health-sha", required=True, action="append", help="the notebooked sha256 of each --health table (repeatable)")
    ap.add_argument("--restarts", required=True, help="restart-gap JSONL from the notebook; an empty file means no restarts")
    ap.add_argument("--as-of-ms", required=True, type=int)
    ap.add_argument("--screen-record", help="confirm mode: this tool's write-once screen record")
    ap.add_argument("--out", help="write the record once (O_CREAT|O_EXCL); only READ_INSTANT or NOT_DECIDABLE is written")
    a = ap.parse_args(argv)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        if a.clock_ms not in CLOCKS_MS:
            raise Refused(f"--clock-ms {a.clock_ms} is not one of FLOOR's clocks {CLOCKS_MS}")
        if a.as_of_ms > now:
            raise Refused("--as-of-ms is in the future")
        if (a.mode == "confirm") != (a.screen_record is not None):
            raise Refused("--screen-record is required in confirm mode and refused in screen mode")
        if a.out and os.path.lexists(a.out):
            raise Refused(f"{a.out} exists: the read instant record is write-once")
        b1 = load_screen_record(a.screen_record, a.clock_ms, BOT_ID) if a.mode == "confirm" else None
        res = compute(a.ledger_root, BOT_ID, a.clock_ms, a.mode, [Path(h).read_text() for h in a.health], a.health_sha, Path(a.restarts).read_text(),
                      a.as_of_ms, b1)
        res["tool_sha256"] = file_sha256(__file__)
        res["am6_exclusions_sha256"] = file_sha256(X.__file__)
        res["computed_at_ms"] = now
        lines = render(res)
        if a.out:
            if res["status"] == ST_NOT_READY:
                lines.append("NOT_READY: nothing written")
            else:
                lines.append(f"recorded {a.out} sha256 {write_once(a.out, res)}")
        else:
            lines.append("not recorded (no --out)")
    except (Refused, OSError) as ex:
        print(f"REFUSED: {ex}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
