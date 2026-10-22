#!/usr/bin/env python3
"""Attempt-level exclusions of DEC-031 Amendment 6 (items 2.4, 3, 2.7 and 4). Outcome-blind, deterministic, stdlib only.

Inputs (all counts/timestamps; no price, size, fill or P&L field is ever read):
  --health    JSONL from tools/arena_hour_health.py: one row per UTC hour {"hour": "YYYY-MM-DDTHH:00:00Z", "excluded": bool,
              "labels_failing": ["price-path"|"decision-input", ...]} and a final {"type":"sha256","hours":N,"sha256":...} line. The sha256 is
              RECOMPUTED over the hour lines (each line + "\\n") and must match, as must "hours"; else exit 2.
  --attempts  JSONL, one attempt per line, EXACTLY these keys (any other key -> exit 2; keys containing pnl/price/sol/fill/amount/size/lamport/
              profit/stake/qty get a named refusal):
                attempt_id  str, unique
                decided_ms  int, decision instant, epoch ms
                exit_ms     int or null (null = still open when the file was cut)
                open_at_restart  optional bool; true with no matching restart in --restarts -> exit 2 (the restart is derived from --restarts)
                s14_excluded  REQUIRED bool, set by the pinned scorer from DEC-031 §1.4 (logged feed gap inside the span in any hour, a print the stack
                              needs without V); counted in the 5% cap only (class excluded_s14, after the span class)
  --as-of     REQUIRED int ms, the read's cut instant. Attempts decided or exited after it -> exit 2. Open attempts get the span check through its hour.
  --restarts  JSONL, each line a 2-element list [start_ms, end_ms]: the restart gap, from the last decision/heartbeat row before the stop to the
              first row after the restart (item 4). Rounded OUTWARD to whole UTC hours and treated as excluded decision-input hours.
How the attempt-timing file is cut from a book's ledger is NOT defined here.

Classification, first match wins (an attempt is in exactly one class, so never in both caps):
  1. decided in an excluded hour (health table, or a restart-gap hour)        -> excluded_decided_in_excluded_hour   (item 3, 10% cap only)
  2. open at a restart: decided before a restart gap starts and exit_ms is
     null or >= that gap's start (or open_at_restart true)                     -> excluded_open_at_restart            (item 4, 5% cap only)
  3. decided in a healthy hour, decision-to-exit span touches an hour whose
     labels_failing has "price-path" (hours after the decision hour, up to
     and including the exit hour)                                              -> excluded_span_overlap_pricepath     (item 2.4, 5% cap only)
  4. s14_excluded true                                                         -> excluded_s14                        (5% cap only)
  5. else                                                                      -> counted
A decision or exit hour absent from the health table (and not in a restart gap) is unmeasurable: exit 2, never silently counted.
Open attempts with no restart are counted (reported as n_open_unclosed_counted); the >24 h orphan rule of item 4 is not applied here (flagged, see below).

Caps (Amendment 6 item 3; DEC-031 §1.4 "Feed gaps": "If more than 5% of a book's trades are excluded this way at its read, the read is NOT_DECIDABLE"):
  10% cap: n_excl_10pct_class / n_attempts > 0.10  (denominator: all attempts given, i.e. decided before the read boundary).
  5% cap : n_excl_5pct_class / (n_attempts - n_excl_10pct_class) > 0.05  (trades that remain after item 3; the smaller, conservative denominator).
           share_5pct_of_all is also printed. Exact integer comparison, no float rounding: exactly 10% / 5% is OK.
  kept_by_utc_date: {YYYY-MM-DD: counted attempts} by decision date (item 2.7).
  Orphans (item 4, DEC-014 Am.1): exit_ms null, open at a restart and as_of - restart_end > 24 h -> verdict_cap NOT_DECIDABLE_ORPHAN, which takes
  precedence over both caps; n_orphans printed.
  verdict_cap: OK | NOT_DECIDABLE_ORPHAN | NOT_DECIDABLE_10PCT | NOT_DECIDABLE_5PCT; if both exceed, NOT_DECIDABLE_10PCT is printed (both flags are in the totals line).
Output: one JSON line per attempt (sorted by decided_ms, attempt_id), a {"type":"totals"} line, then {"type":"sha256"} over all earlier lines.
Exit codes: 0 ok (verdict is data, not exit code), 2 refused/invalid input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from typing import Any

SCHEMA = "arena_am6_exclusions_v1"
HOUR_MS = 3_600_000
ALLOWED = ("attempt_id", "decided_ms", "exit_ms", "open_at_restart", "s14_excluded")
FORBIDDEN_FRAGMENTS = ("pnl", "price", "sol", "fill", "amount", "size", "lamport", "profit", "stake", "qty")
S_10 = "excluded_decided_in_excluded_hour"
S_RESTART = "excluded_open_at_restart"
S_SPAN = "excluded_span_overlap_pricepath"
S_S14 = "excluded_s14"
S_OK = "counted"
DAY_MS = 24 * HOUR_MS


class Refused(Exception):
    pass


def canon(o: Any) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), allow_nan=False)


def is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def parse_hour(s: Any) -> int:
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000) // HOUR_MS * HOUR_MS
    except (ValueError, TypeError):
        raise Refused(f"bad hour in health table: {s!r}")


def load_health(text: str) -> tuple[dict[int, tuple[bool, frozenset]], str]:
    lines = [ln for ln in text.split("\n") if ln != ""]
    if not lines:
        raise Refused("health table empty")
    try:
        final = json.loads(lines[-1])
    except ValueError:
        raise Refused("health table: last line is not JSON")
    if not isinstance(final, dict) or final.get("type") != "sha256" or not isinstance(final.get("sha256"), str):
        raise Refused("health table: final sha256 line missing")
    body_lines = lines[:-1]
    got = hashlib.sha256("".join(ln + "\n" for ln in body_lines).encode()).hexdigest()
    if got != final["sha256"]:
        raise Refused("health table: sha256 mismatch")
    if final.get("hours") != len(body_lines):
        raise Refused("health table: hours count mismatch")
    table: dict[int, tuple[bool, frozenset]] = {}
    for ln in body_lines:
        try:
            r = json.loads(ln)
        except ValueError:
            raise Refused("health table: unparseable hour line")
        if not isinstance(r, dict) or not isinstance(r.get("excluded"), bool):
            raise Refused("health table: hour row without boolean 'excluded'")
        labels = r.get("labels_failing", [])
        if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
            raise Refused("health table: bad labels_failing")
        h = parse_hour(r.get("hour"))
        if h in table:
            raise Refused("health table: duplicate hour")
        table[h] = (r["excluded"], frozenset(labels))
    return table, final["sha256"]


def load_attempts(text: str) -> list[dict]:
    out, seen = [], set()
    for i, ln in enumerate(text.split("\n"), 1):
        if ln.strip() == "":
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            raise Refused(f"attempts line {i}: not JSON")
        if not isinstance(r, dict):
            raise Refused(f"attempts line {i}: not an object")
        for k in r:
            kl = str(k).lower()
            if any(f in kl for f in FORBIDDEN_FRAGMENTS):
                raise Refused(f"attempts line {i}: forbidden outcome/price key refused: {k!r}")
        extra = sorted(set(r) - set(ALLOWED))
        if extra:
            raise Refused(f"attempts line {i}: keys not in schema: {extra}")
        aid = r.get("attempt_id")
        if not isinstance(aid, str) or not aid or aid in seen:
            raise Refused(f"attempts line {i}: attempt_id missing, not a string or duplicate")
        seen.add(aid)
        d, e = r.get("decided_ms"), r.get("exit_ms")
        if not is_int(d) or d < 0 or "exit_ms" not in r or not (e is None or is_int(e)):
            raise Refused(f"attempts line {i}: decided_ms must be int and exit_ms int or null")
        if e is not None and e < d:
            raise Refused(f"attempts line {i}: exit_ms before decided_ms")
        o = r.get("open_at_restart", False)
        if not isinstance(o, bool):
            raise Refused(f"attempts line {i}: open_at_restart must be bool")
        s14 = r.get("s14_excluded")
        if not isinstance(s14, bool):
            raise Refused(f"attempts line {i}: s14_excluded (bool, set by the pinned scorer) is required")
        out.append({"attempt_id": aid, "decided_ms": d, "exit_ms": e, "open_at_restart": o, "s14_excluded": s14})
    return out


def load_restarts(text: str) -> list[tuple[int, int]]:
    out = []
    for i, ln in enumerate(text.split("\n"), 1):
        if ln.strip() == "":
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            raise Refused(f"restarts line {i}: not JSON")
        if not (isinstance(r, list) and len(r) == 2 and is_int(r[0]) and is_int(r[1]) and 0 <= r[0] <= r[1]):
            raise Refused(f"restarts line {i}: expected [start_ms, end_ms] ints with start <= end")
        out.append((r[0], r[1]))
    return sorted(out)


def classify(attempts: list[dict], table: dict[int, tuple[bool, frozenset]], restarts: list[tuple[int, int]], as_of: int) -> list[dict]:
    gap_hours: set[int] = set()
    for s, e in restarts:
        h = s // HOUR_MS * HOUR_MS
        last = max(h, (e - 1) // HOUR_MS * HOUR_MS)  # rounded outward (ceil): an end exactly on a boundary does not take the next hour
        while h <= last:
            gap_hours.add(h)
            h += HOUR_MS

    def health(h: int, aid: str):
        # the table row is always required. A restart-gap hour ADDS decision-input to the table's labels (never replaces them), so a price-path
        # failure of the feed in a gap hour still triggers the item 2.4 span exclusion.
        if h not in table:
            raise Refused(f"attempt {aid}: hour {h} absent from health table (unmeasurable)")
        ex, labels = table[h]
        return (True, labels | {"decision-input"}) if h in gap_hours else (ex, labels)

    res = []
    for a in sorted(attempts, key=lambda x: (x["decided_ms"], x["attempt_id"])):
        d, e, aid = a["decided_ms"], a["exit_ms"], a["attempt_id"]
        if d > as_of or (e is not None and e > as_of):
            raise Refused(f"attempt {aid}: decided/exit after --as-of")
        dh = d // HOUR_MS * HOUR_MS
        match = next(((rs, re_) for rs, re_ in restarts if d < rs and (e is None or e >= rs)), None)
        if a["open_at_restart"] and match is None:
            raise Refused(f"attempt {aid}: open_at_restart true but no matching restart in --restarts")
        status, orphan = S_OK, False
        if health(dh, aid)[0]:
            status = S_10
        elif match is not None:
            status = S_RESTART
            orphan = e is None and as_of - match[1] > DAY_MS
        else:
            end_h = (e if e is not None else as_of) // HOUR_MS * HOUR_MS  # open attempts: through the --as-of hour (C4)
            h = dh + HOUR_MS
            while h <= end_h:
                ex, labels = health(h, aid)
                if ex and "price-path" in labels:
                    status = S_SPAN
                    break
                h += HOUR_MS
            if status == S_OK and a["s14_excluded"]:
                status = S_S14
        res.append({"type": "attempt", "attempt_id": aid, "decided_ms": d, "exit_ms": e, "status": status, "orphan": orphan})
    return res


def totals(rows: list[dict], health_sha: str) -> dict:
    n = len(rows)
    n10 = sum(1 for r in rows if r["status"] == S_10)
    n5 = sum(1 for r in rows if r["status"] in (S_RESTART, S_SPAN, S_S14))
    rest = n - n10
    kept: dict[str, int] = {}
    for r in rows:
        if r["status"] == S_OK:
            dk = datetime.fromtimestamp(r["decided_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            kept[dk] = kept.get(dk, 0) + 1
    over10 = n10 * 10 > n
    over5 = n5 * 20 > rest
    n_orph = sum(1 for r in rows if r["orphan"])
    verdict = "NOT_DECIDABLE_ORPHAN" if n_orph else "NOT_DECIDABLE_10PCT" if over10 else ("NOT_DECIDABLE_5PCT" if over5 else "OK")
    return {
        "type": "totals", "schema": SCHEMA, "health_sha256": health_sha,
        "n_attempts": n, "n_counted": n - n10 - n5,
        "n_excl_10pct_class": n10, "share_10pct": (n10 / n) if n else 0.0,
        "n_excl_5pct_class": n5, "share_5pct": (n5 / rest) if rest else 0.0,
        "share_5pct_of_all": (n5 / n) if n else 0.0,
        "n_excl_span_overlap_pricepath": sum(1 for r in rows if r["status"] == S_SPAN),
        "n_excl_open_at_restart": sum(1 for r in rows if r["status"] == S_RESTART),
        "n_open_unclosed_counted": sum(1 for r in rows if r["status"] == S_OK and r["exit_ms"] is None),
        "kept_by_utc_date": kept, "n_orphans": n_orph, "n_excl_s14": sum(1 for r in rows if r["status"] == S_S14), "n_open_after_restart": sum(1 for r in rows if r["status"] == S_RESTART and r["exit_ms"] is None),
        "cap_10pct_exceeded": over10, "cap_5pct_exceeded": over5, "verdict_cap": verdict,
    }


def run(health_text: str, attempts_text: str, restarts_text: str, as_of: int) -> list[str]:
    table, hsha = load_health(health_text)
    rows = classify(load_attempts(attempts_text), table, load_restarts(restarts_text), as_of)
    lines = [canon(r) for r in rows] + [canon(totals(rows, hsha))]
    body = "".join(ln + "\n" for ln in lines)
    return lines + [canon({"type": "sha256", "schema": SCHEMA, "lines": len(lines), "sha256": hashlib.sha256(body.encode()).hexdigest()})]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--health", required=True)
    ap.add_argument("--attempts", required=True)
    ap.add_argument("--restarts", default=None, help="optional; absent = no restarts")
    ap.add_argument("--as-of", required=True, type=int, help="read cut instant, epoch ms")
    a = ap.parse_args(argv)
    try:
        rt = open(a.restarts).read() if a.restarts else ""
        out = run(open(a.health).read(), open(a.attempts).read(), rt, a.as_of)
    except (Refused, OSError) as ex:
        print(f"REFUSED: {ex}", file=sys.stderr)
        return 2
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
