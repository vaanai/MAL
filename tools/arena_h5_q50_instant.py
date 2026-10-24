"""The read instant of the H5-BOOSTFLOOR-Q50 book `h5v1q50-boostfloor-a1` under DEC-031 Amendment 11 (QP-RULING-1024-q50-m1; Q50 audit M1). PAPER ONLY. COUNTS ONLY.

WHY. The pinned scorer (tools/arena_h5_score.py, sha256 dfe7f018..., never edited here) records `read-instant.json` at the first 00:00Z with >= 100 ATTEMPTS (excluded and open ones
included) on >= 5 dates. The gate needs n >= 100 counted trades and allows up to 5% exclusions, so a read at 100 to 104 attempts with a few exclusions is a FAIL decided by count alone.
Amendment 11 fixes the Q50 book's instant on COUNTED TRADES. For this book `read-instant.json` is an attempt-boundary record only: it is never the read instant and no seal opens on it.

ITEM 1 (the rule, `instant_rule`, the ONE function every caller uses). The read instant is the first 00:00Z boundary B with start_ms < B <= end_ms such that the attempts decided in
[start_ms, B) include at least 100 counted trades on at least 5 distinct UTC dates (the decision dates of those counted trades). A counted trade is a non-refused attempt that has closed
and is not excluded. B qualifies only once every attempt decided before B has closed or been excluded: while one is still open, B is PENDING and no later boundary is looked at (the
first boundary is decided in time order, never skipped because an attempt is open). The rule takes decision timestamps and the scorer's class only (counted / excluded / open; refused
triggers are not attempts and are never passed in). It takes and prints no fill, exit, net, leg or P&L field. A counted trade without its deciding-leg P&L still counts here (the read
tool makes that read NOT_DECIDABLE; it never moves the instant).
ITEM 2. No B at or before the end-seal (2026-11-06T00:00:00Z, 1793923200000) once `now` has reached it: the book ENDS NOT_DECIDABLE, final, counts only. No early read, no extension.
ITEM 3 (confirmation, `confirmation_instant`). The same rule with start_ms = the screen read's instant: at least 100 counted trades decided at or after it, on at least 5 further UTC dates
(every date of a decision at or after a 00:00Z instant is a further date). The end that applies to the confirmation is a parameter (no default).

THE RECORD (`record`): <ledger>/<bot_id>/q50-read-instant.json, created with O_CREAT | O_EXCL (write-once; an existing file is never rewritten: a later run that computes a different
outcome counts `q50_read_instant_conflict` and keeps the old file). Fields: counts and instants only. It is written by the Q50 scoring wrapper (tools/arena_h5_score_q50.py) on every
`score` run that is not --dry-run, from the scorer's own BookResult of that run. tools/arena_h5_legs.gate and tools/arena_h5_read key the Q50 seal on this file.
"""

from __future__ import annotations

import collections
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

Q50_ID = "h5v1q50-boostfloor-a1"
RULE = "DEC-031 Amendment 11 item 1 (counted trades, QP-RULING-1024-q50-m1)"
CONFIRM_RULE = "DEC-031 Amendment 11 item 3 (confirmation: counted trades after the screen instant)"
INSTANT_FILE = "q50-read-instant.json"
MIN_TRADES = 100
MIN_DATES = 5
DAY_MS = 86_400_000
END_SEAL_MS = 1_793_923_200_000  # 2026-11-06T00:00:00Z, the arena end-seal (DEC-031 Amendment 5 item 2.4)

READ, PENDING, NOT_YET, ENDED = "READ", "PENDING_OPEN", "NOT_YET", "ENDED_NOT_DECIDABLE"


def _ints(xs: Iterable[int]) -> list[int]:
    out = []
    for x in xs:
        if isinstance(x, bool) or not isinstance(x, int):
            raise ValueError(f"a decision timestamp must be an integer ms, not {x!r}")
        out.append(x)
    return sorted(out)


def instant_rule(counted_ms: Iterable[int], open_ms: Iterable[int], *, start_ms: int, end_ms: int, now_ms: int, min_trades: int = MIN_TRADES,
                 min_dates: int = MIN_DATES) -> dict[str, Any]:
    """Item 1 (and item 3 with start_ms = the screen instant). counted_ms: one decision timestamp per counted trade (per pool, not per stake row); open_ms: one per open attempt.
    Excluded attempts are neither (they have been excluded: they never block B and never count). Returns {"status", "instant_ms", "boundary_ms", ...counts}:
      READ      instant_ms = B, the first qualifying boundary (B <= now_ms and B <= end_ms);
      PENDING   boundary_ms = the first boundary (<= now_ms, <= end_ms) that has an open attempt decided before it: nothing at or after it is decided yet;
      NOT_YET   no boundary up to min(now_ms, end_ms) qualifies, now_ms < end_ms;
      ENDED     no boundary up to end_ms qualifies and now_ms >= end_ms (item 2: NOT_DECIDABLE, final)."""
    start_ms, end_ms, now_ms = int(start_ms), int(end_ms), int(now_ms)
    cs = [d for d in _ints(counted_ms) if d >= start_ms]
    op = [d for d in _ints(open_ms) if d >= start_ms]
    last = min(now_ms, end_ms)
    b = (start_ms // DAY_MS + 1) * DAY_MS  # the first 00:00Z strictly after start_ms
    while b <= last:
        if any(d < b for d in op):
            return {"status": PENDING, "instant_ms": None, "boundary_ms": b, "open_before": sum(1 for d in op if d < b)}
        before = [d for d in cs if d < b]
        dates = {d // DAY_MS for d in before}
        if len(before) >= min_trades and len(dates) >= min_dates:
            return {"status": READ, "instant_ms": b, "boundary_ms": b, "counted_before": len(before), "dates": len(dates), "open_before": 0}
        b += DAY_MS
    return {"status": ENDED if now_ms >= end_ms else NOT_YET, "instant_ms": None, "boundary_ms": None}


def confirmation_instant(counted_ms: Iterable[int], open_ms: Iterable[int], *, screen_instant_ms: int, end_ms: int, now_ms: int) -> dict[str, Any]:
    """Item 3: at least 100 counted trades decided at or after the screen read's instant, on at least 5 further UTC dates, at the first 00:00Z that meets it. end_ms has no default."""
    if int(screen_instant_ms) % DAY_MS:
        raise ValueError("the screen read's instant is a 00:00Z boundary")
    return dict(instant_rule(counted_ms, open_ms, start_ms=int(screen_instant_ms), end_ms=end_ms, now_ms=now_ms), rule=CONFIRM_RULE)


def classes_from_result(res: Any) -> dict[str, list[int]]:
    """The scorer's BookResult -> decision timestamps by class. Reads ONLY `pool` and `decision_ms` of the rows (one per pool: a pool has one row per stake), `decision_ms` of the
    exclusion and refusal rows, and `open_decisions`. No fill, exit, net, leg or P&L field is read."""
    counted: dict[str, int] = {}
    for r in res.rows:
        p, d = r["pool"], int(r["decision_ms"])
        if p in counted and counted[p] != d:
            raise ValueError(f"pool {p}: rows with two decision instants")
        counted[p] = d
    return {"counted": sorted(counted.values()), "excluded": sorted(int(e["decision_ms"]) for e in res.exclusions), "open": sorted(int(d) for d in res.open_decisions),
            "refused": sorted((int(r["decision_ms"]), str(r["reason"])) for r in res.refusals)}


def record_doc(bot_id: str, cls: Mapping[str, list], *, clock_start_ms: int, end_ms: int, now_ms: int, attempt_definition: str | None = None) -> dict[str, Any] | None:
    """The record of item 1 / item 2, or None while nothing is decided (PENDING / NOT_YET). Counts and instants only, over decisions in [clock_start_ms, boundary): the keys the
    read seal checks against the ledger (attempts_before = counted + excluded, refused_before, refused_by_reason_before) and open_before_read (0 by construction)."""
    r = instant_rule(cls["counted"], cls["open"], start_ms=clock_start_ms, end_ms=end_ms, now_ms=now_ms)
    if r["status"] not in (READ, ENDED):
        return None
    b = r["instant_ms"] if r["status"] == READ else int(end_ms)
    inwin = lambda xs: [d for d in xs if int(clock_start_ms) <= d < b]  # noqa: E731
    c, e = inwin(cls["counted"]), inwin(cls["excluded"])
    ref = [(d, why) for d, why in cls["refused"] if int(clock_start_ms) <= d < b]
    return {"bot_id": bot_id, "rule": RULE, "status": r["status"], "read_instant_ms": r["instant_ms"], "boundary_ms": b, "clock_start_ms": int(clock_start_ms), "end_seal_ms": int(end_ms),
            "counted_before": len(c), "counted_dates": len({d // DAY_MS for d in c}), "excluded_before": len(e), "attempts_before": len(c) + len(e), "open_before_read": 0,
            "refused_before": len(ref), "refused_by_reason_before": dict(sorted(collections.Counter(why for _, why in ref).items())), "attempt_definition": attempt_definition, "note": "counts and instants only; read-instant.json (the scorer's attempt boundary) is not this book's read instant"}


def write_once(path: Path, doc: Mapping[str, Any]) -> bool:
    """O_CREAT | O_EXCL: True if written, False if the file already exists (never rewritten)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(doc, sort_keys=True, allow_nan=False) + "\n").encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        return False
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def record(bot_dir: Path, doc: Mapping[str, Any]) -> str:
    """Write the record once. Returns "written", "unchanged" (an equal record exists) or "conflict" (a different one exists; it is kept)."""
    path = bot_dir / INSTANT_FILE
    if write_once(path, doc):
        return "written"
    old = json.loads(path.read_text())
    keys = ("status", "read_instant_ms", "boundary_ms", "clock_start_ms", "end_seal_ms", "counted_before", "excluded_before", "refused_before")
    return "unchanged" if all(old.get(k) == doc.get(k) for k in keys) else "conflict"
