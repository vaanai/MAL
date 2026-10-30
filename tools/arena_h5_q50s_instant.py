"""Constants and the read instants of the Q50 successor book `h5v1q50-boostfloor-s1`. PAPER ONLY. COUNTS ONLY.

RULE: DEC-031's power-n amendment (QP-RULING-1030-power-n, sha256 c86f0613e79d47e18f1bbb23a52b56ad20f4fc4a68e9e4babfe679e58d113631, R2 and Appendix B item 8.1; numbers pinned
in notebook n_F60AKfUlWM41kw before 2026-10-31T00:20:00Z). For this book it replaces DEC-031 Amendment 18 items 3.2 and 3.3 and the instant clauses of its item 7:
  - E = 2026-12-01T00:00:00Z (1796083200000), S*'s SUCC_END_MS (tools/h5_shadow.py at S* line 261). H = 25 d. Written as `window_end_ms` in the bots.json line at registration.
  - SCREEN (`instant_rule`): the first 00:00Z boundary B with clock < B <= E such that the counted trades decided in [clock, B) are on at least 5 distinct UTC decision dates
    and number either (i) at least N_s = 200, or (ii) at least 100 when B >= S_cap = clock + 10 d = 2026-11-16T00:00:00Z (1794787200000). None by E: ENDED NOT_DECIDABLE, final.
  - CONFIRMATION (`confirmation_instant`): the first 00:00Z boundary B with T_s < B <= E_c = E (T_s = the screen read's instant) such that the counted trades decided in [T_s, B)
    are on at least 5 further UTC dates and number either (i) at least N_c = 300, or (ii) at least 100 when B = E_c. None by E_c: NOT_DECIDABLE, final for the bot_id.
  - In both, B qualifies only once every attempt decided before B has closed or been excluded: while one is still open, B is PENDING and no later boundary is looked at.
  - Inputs are decision timestamps and the scorer's class only (counted / excluded / open / refused). No fill, exit, net, leg or P&L field is taken or printed.

`tools/arena_h5_q50_instant.py` (Q50's tool) is imported and NOT edited. Its `instant_rule` decides Q50's instant and is not used for s1 any more: the s1 rule has two branches (a
declared size and a calendar cap), which Q50's single-threshold function cannot express. The loop below is Q50's loop (same boundary order, same PENDING and ENDED semantics) with
that one change. Q50's screen and confirmation are untouched (power-n item 8.3).

THE TABLE ROW (`S1`) is the one compiled source of the book's values: frozen_sha S* (tag q50-succ-base-1), clock, window end E, rule and its sha256, Q* 50, exit v1, the s0
margin, the registration window (Amendment 18 item 4: 2026-11-04T00:00Z to 2026-11-05T12:00Z), the start record's `h5_look2.end_only_ms` (S*'s SUCC_END_MS) and the declared read
sizes. A slip of the clock (Amendment 17 item 1) is a new PR with a new row, not a flag (on the 2026-11-07 slip S_cap is 2026-11-17T00:00Z and E stays 2026-12-01T00:00Z).

THE RECORD: <ledger>/<bot_id>/q50s1-read-instant.json, created O_CREAT | O_EXCL (write-once). It carries the fields of Q50's Am.11 record (`end_seal_ms` = E) plus the declared
sizes and the branch that fired, so tools/arena_h5_legs.gate and tools/arena_h5_read can key their seal on it (arena_h5_books.AM11_FILES), and arena_h5_books.check_recorded_instant
recomputes it with this module's rule.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools import arena_h5_q50_instant as qi

S1_ID = "h5v1q50-boostfloor-s1"
RULE = ("DEC-031 power-n amendment item 8.1 (QP-RULING-1030-power-n R2; notebook n_F60AKfUlWM41kw): screen at the first 00:00Z with >= 200 counted trades on >= 5 dates, "
        "or at B >= S_cap = clock + 10 d with >= 100; end E = 2026-12-01T00:00:00Z")
CONFIRM_RULE = ("DEC-031 power-n amendment item 8.1 (confirmation: >= 300 counted trades after the screen instant on >= 5 further dates, or >= 100 at E_c = E "
                "= 2026-12-01T00:00:00Z)")
INSTANT_FILE = "q50s1-read-instant.json"
MIN_TRADES, MIN_DATES, DAY_MS = qi.MIN_TRADES, qi.MIN_DATES, qi.DAY_MS  # 100 = the floor of branch (ii); 5 dates in every branch
READ, PENDING, NOT_YET, ENDED = qi.READ, qi.PENDING, qi.NOT_YET, qi.ENDED
BRANCH_N, BRANCH_CAP = "i", "ii"

S1: Mapping[str, Any] = {
    "bot_id": S1_ID,
    "frozen_sha": "5c2ab7c64b21358619e37068c6a53e4d183512ea",  # S*, tag q50-succ-base-1; NOT F50 a5f41370 (DEC-031 Amendment 18 item 2.3)
    "clock_start_ms": 1_793_923_200_000,  # 2026-11-06T00:00:00Z, exactly Q50's end-seal
    "window_end_ms": 1_796_083_200_000,  # E = 2026-12-01T00:00:00Z = S*'s SUCC_END_MS (H = 25 d; power-n item 8.1); written into the bots.json line at registration
    "rule": "H5-BOOSTFLOOR-Q50 v1",
    "rule_sha256": "b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4",  # EXP/EXP-024-RULE-Q50.md, as Q50's
    "q_star_sol": 50.0,
    "exit_rule": "v1",
    "s0_margin_ms": 60_000,
    "end_only_ms": 1_796_083_200_000,  # S*'s SUCC_END_MS (2026-12-01T00:00:00Z): h5_look2.end_only_ms of every start record
    "register_not_before_ms": 1_793_750_400_000,  # 2026-11-04T00:00:00Z
    "register_not_after_ms": 1_793_880_000_000,  # 2026-11-05T12:00:00Z
    "screen_n": 200,  # N_s
    "screen_cap_ms": 1_794_787_200_000,  # S_cap = clock + 10 d = 2026-11-16T00:00:00Z
    "confirm_n": 300,  # N_c
    "confirm_end_ms": 1_796_083_200_000,  # E_c = E
}
CLOCK_MS: int = S1["clock_start_ms"]
END_MS: int = S1["window_end_ms"]
SCREEN_N: int = S1["screen_n"]
SCREEN_CAP_MS: int = S1["screen_cap_ms"]
CONFIRM_N: int = S1["confirm_n"]
CONFIRM_END_MS: int = S1["confirm_end_ms"]
EARLIEST_BOUNDARY_MS = CLOCK_MS + 5 * DAY_MS  # 2026-11-11T00:00:00Z: 5 distinct decision dates cannot be met earlier


def sized_rule(counted_ms: Iterable[int], open_ms: Iterable[int], *, start_ms: int, end_ms: int, now_ms: int, n_declared: int, cap_ms: int,
               floor: int = MIN_TRADES, min_dates: int = MIN_DATES) -> dict[str, Any]:
    """The power-n read instant (Appendix A item 2): the first 00:00Z boundary B, start_ms < B <= end_ms, at which the counted trades decided in [start_ms, B) are on at least
    min_dates distinct UTC dates and number at least n_declared (branch "i"), or at least floor with B >= cap_ms (branch "ii"). counted_ms: one decision timestamp per counted
    trade (per pool); open_ms: one per open attempt; excluded attempts are neither. Statuses as tools.arena_h5_q50_instant.instant_rule:
      READ      instant_ms = B (B <= now_ms and B <= end_ms), with "branch";
      PENDING   boundary_ms = the first boundary (<= now_ms, <= end_ms) that has an open attempt decided before it: nothing at or after it is decided yet;
      NOT_YET   no boundary up to min(now_ms, end_ms) qualifies, now_ms < end_ms;
      ENDED     no boundary up to end_ms qualifies and now_ms >= end_ms (NOT_DECIDABLE, final)."""
    start_ms, end_ms, now_ms, cap_ms, n_declared, floor = int(start_ms), int(end_ms), int(now_ms), int(cap_ms), int(n_declared), int(floor)
    if not floor <= n_declared:
        raise ValueError(f"the declared size {n_declared} is below the floor {floor}")
    cs = [d for d in qi._ints(counted_ms) if d >= start_ms]
    op = [d for d in qi._ints(open_ms) if d >= start_ms]
    last = min(now_ms, end_ms)
    b = (start_ms // DAY_MS + 1) * DAY_MS  # the first 00:00Z strictly after start_ms
    while b <= last:
        if any(d < b for d in op):
            return {"status": PENDING, "instant_ms": None, "boundary_ms": b, "open_before": sum(1 for d in op if d < b)}
        before = [d for d in cs if d < b]
        dates = {d // DAY_MS for d in before}
        if len(dates) >= min_dates:
            branch = BRANCH_N if len(before) >= n_declared else (BRANCH_CAP if b >= cap_ms and len(before) >= floor else None)
            if branch:
                return {"status": READ, "instant_ms": b, "boundary_ms": b, "counted_before": len(before), "dates": len(dates), "open_before": 0, "branch": branch}
        b += DAY_MS
    return {"status": ENDED if now_ms >= end_ms else NOT_YET, "instant_ms": None, "boundary_ms": None}


def instant_rule(counted_ms: Iterable[int], open_ms: Iterable[int], *, now_ms: int, start_ms: int = CLOCK_MS, end_ms: int = END_MS) -> dict[str, Any]:
    """The s1 SCREEN instant: N_s = 200, or >= 100 at B >= S_cap, by E (power-n item 8.1)."""
    return sized_rule(counted_ms, open_ms, start_ms=start_ms, end_ms=end_ms, now_ms=now_ms, n_declared=SCREEN_N, cap_ms=SCREEN_CAP_MS)


def confirmation_instant(counted_ms: Iterable[int], open_ms: Iterable[int], *, screen_instant_ms: int, now_ms: int, end_ms: int = CONFIRM_END_MS) -> dict[str, Any]:
    """The s1 CONFIRMATION instant: >= 300 counted trades decided at or after the screen instant on >= 5 further UTC dates, or >= 100 at B = E_c (power-n item 8.1)."""
    if int(screen_instant_ms) % DAY_MS:
        raise ValueError("the screen read's instant is a 00:00Z boundary")
    r = sized_rule(counted_ms, open_ms, start_ms=int(screen_instant_ms), end_ms=end_ms, now_ms=now_ms, n_declared=CONFIRM_N, cap_ms=end_ms)
    return dict(r, rule=CONFIRM_RULE)


def record_doc(bot_id: str, cls: Mapping[str, list], *, clock_start_ms: int, end_ms: int, now_ms: int, attempt_definition: str | None = None) -> dict[str, Any] | None:
    """The screen record, or None while nothing is decided (PENDING / NOT_YET). The fields of Q50's Am.11 record (tools.arena_h5_q50_instant.record_doc) computed with this book's
    rule over decisions in [clock_start_ms, boundary), plus the declared sizes and the branch that fired. Counts and instants only."""
    if bot_id != S1_ID:
        raise ValueError(f"the s1 instant tool records only {S1_ID}, not {bot_id!r}")
    r = instant_rule(cls["counted"], cls["open"], start_ms=clock_start_ms, end_ms=end_ms, now_ms=now_ms)
    if r["status"] not in (READ, ENDED):
        return None
    b = r["instant_ms"] if r["status"] == READ else int(end_ms)
    inwin = lambda xs: [d for d in xs if int(clock_start_ms) <= d < b]  # noqa: E731
    c, e = inwin(cls["counted"]), inwin(cls["excluded"])
    ref = [(d, why) for d, why in cls["refused"] if int(clock_start_ms) <= d < b]
    return {"bot_id": bot_id, "rule": RULE, "status": r["status"], "read_instant_ms": r["instant_ms"], "boundary_ms": b, "clock_start_ms": int(clock_start_ms), "end_seal_ms": int(end_ms),
            "counted_before": len(c), "counted_dates": len({d // DAY_MS for d in c}), "excluded_before": len(e), "attempts_before": len(c) + len(e), "open_before_read": 0,
            "refused_before": len(ref), "refused_by_reason_before": dict(sorted(collections.Counter(why for _, why in ref).items())), "attempt_definition": attempt_definition,
            "screen_n": SCREEN_N, "screen_cap_ms": SCREEN_CAP_MS, "floor": MIN_TRADES, "branch": r.get("branch"),
            "note": "counts and instants only; read-instant.json (the scorer's attempt boundary) is not this book's read instant"}


def record(bot_dir: Path, doc: Mapping[str, Any]) -> str:
    """Write once. Returns "written", "unchanged" (an equal record exists) or "conflict" (a different one exists; it is kept)."""
    path = Path(bot_dir) / INSTANT_FILE
    if qi.write_once(path, doc):
        return "written"
    old = json.loads(path.read_text())
    keys = ("status", "read_instant_ms", "boundary_ms", "clock_start_ms", "end_seal_ms", "counted_before", "excluded_before", "refused_before")
    return "unchanged" if all(old.get(k) == doc.get(k) for k in keys) else "conflict"
