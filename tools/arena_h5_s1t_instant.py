"""Constants, table rows and read instants of the tip-source Q50 successor `h5v1q50-boostfloor-s1t`. PAPER ONLY. COUNTS ONLY.

RULE: DEC-031 Amendment 24 (#1186, main ec328be2) items 2, 5 and 9, which carry Amendment 22 item 8.1's read sizes with s1t's clock:
  - E = E_c = SUCC_END_MS = 2026-12-01T00:00:00Z (1796083200000). H = 24 d from the 2026-11-07 clock, 21 d from the 2026-11-10 slip.
  - SCREEN (`instant_rule`): the first 00:00Z boundary B with clock < B <= E such that the counted trades decided in [clock, B) are on at least 5 distinct UTC decision dates
    and number either (i) at least N_s = 200, or (ii) at least 100 when B >= S_cap = clock + 10 d (2026-11-17T00Z for the 11-07 clock, 2026-11-20T00Z for the 11-10 slip).
    None by E: ENDED NOT_DECIDABLE, final.
  - CONFIRMATION (`confirmation_instant`): the first boundary B with T_s < B <= E_c (T_s = the screen read's instant) such that the counted trades decided in [T_s, B) are on at
    least 5 further UTC dates and number either (i) at least N_c = 300, or (ii) at least 100 when B = E_c. None by E_c: NOT_DECIDABLE, final for the bot_id.
  - In both, B qualifies only once every attempt decided before B has closed or been excluded: while one is still open, B is PENDING and no later boundary is looked at.
  - Inputs are decision timestamps and the scorer's class only (counted / excluded / open / refused). No fill, exit, net, leg or P&L field is taken or printed.

`tools/arena_h5_q50s_instant.py` (s1's tool, pinned in scripts/ops/q50s1-score.sh) and `tools/arena_h5_q50_instant.py` (Q50's) are imported and NOT edited. The loop and the PENDING /
ENDED semantics are s1's `sized_rule`; this module adds only s1t's table rows, its read-instant file name and the rule texts. Importing it has no side effect.

THE TABLE ROWS (`ROW_1107`, `ROW_1110`) are the compiled source of the book's values: S*' (the commit of tag `q50-succ-tip-1`, tag-only), its blobs, clock, window end E, rule and its
sha256, Q* 50, exit v1, the s0 margin, the registration window, the start record's `h5_look2.end_only_ms` and the declared read sizes. Amendment 24 item 9 pre-commits exactly two
clocks: 1794009600000 (2026-11-07T00:00:00Z) and the first slip 1794268800000 (2026-11-10T00:00:00Z); there is no 11-08 or 11-09 clock (item 9). A later slip is a new row in a new PR.

THE RECORD: <ledger>/<bot_id>/s1t-read-instant.json, created O_CREAT | O_EXCL (write-once). It carries the fields of s1's record plus the clock row's S_cap.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools import arena_h5_q50_instant as qi
from tools import arena_h5_q50s_instant as si

S1T_ID = "h5v1q50-boostfloor-s1t"
INSTANT_FILE = "s1t-read-instant.json"
DAY_MS = qi.DAY_MS
MIN_TRADES, MIN_DATES = qi.MIN_TRADES, qi.MIN_DATES  # 100 = the floor of branch (ii); 5 dates in every branch
READ, PENDING, NOT_YET, ENDED = qi.READ, qi.PENDING, qi.NOT_YET, qi.ENDED
BRANCH_N, BRANCH_CAP = si.BRANCH_N, si.BRANCH_CAP

SUCC_END_MS = 1_796_083_200_000  # S*'s and S*''s SUCC_END_MS = E = E_c (2026-12-01T00:00:00Z)
RULE = ("DEC-031 Amendment 24 item 9 (Amendment 22 item 8.1 numbers): screen at the first 00:00Z with >= 200 counted trades on >= 5 dates, or at B >= S_cap = clock + 10 d "
        "with >= 100; end E = 2026-12-01T00:00:00Z")
CONFIRM_RULE = ("DEC-031 Amendment 24 item 9 (confirmation: >= 300 counted trades after the screen instant on >= 5 further dates, or >= 100 at E_c = E = 2026-12-01T00:00:00Z)")

_COMMON: Mapping[str, Any] = {
    "bot_id": S1T_ID,
    "frozen_sha": "afa4971d9dd8b0bca2e73a758c22f4539f675d63",  # S*', the commit of tag q50-succ-tip-1 (tag-only; #1185 closed unmerged); NOT S* 5c2ab7c6
    "tag": "q50-succ-tip-1",
    "tag_object": "c8cb9a8e31419fcce03b9fe3947a474ebdd7f47a",
    "s_star": "5c2ab7c64b21358619e37068c6a53e4d183512ea",  # S* (tag q50-succ-base-1): tools/h5_shadow.py and its import set are byte-identical at S*'
    "shadow_blob": "7f859b12deef87573a7314e49e125b5121eaa645",  # tools/h5_shadow.py at S* and S*'
    "wrapper_blob": "05c5489a786dac4eb0696a74279a0f789ddb1deb",  # tools/h5_shadow_tip.py at S*'
    "rule": "H5-BOOSTFLOOR-Q50 v1",
    "rule_sha256": "b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4",  # EXP/EXP-024-RULE-Q50.md, as Q50's and s1's
    "q_star_sol": 50.0,
    "exit_rule": "v1",
    "s0_margin_ms": 60_000,
    "end_only_ms": SUCC_END_MS,  # h5_look2.end_only_ms of every start record
    "window_end_ms": SUCC_END_MS,
    "screen_n": 200,  # N_s
    "confirm_n": 300,  # N_c
    "confirm_end_ms": SUCC_END_MS,  # E_c = E
    "tip_input": "/data2/mal/arena-tip-feed/hv7",
    "syn_sockets": 1,
    "side_feed_ws": "wss://rpc.solanatracker.io/public",  # class side feed (Am.24 item 2; QP-NOTE-1102 section 3 condition 1)
    "side_feed_host": "wss://rpc.solanatracker.io",  # the host-only form the bots.json line records (QP A1 on #1121: a path may carry a credential on other providers)
    "fresh_forward": "ARENA",
}


def _row(clock_ms: int, *, reg_from_ms: int, reg_to_ms: int, h_days: int, p0_per_day: float | None, p0_ref: str, p0_rule: str) -> dict[str, Any]:
    return {**_COMMON, "clock_start_ms": clock_ms, "screen_cap_ms": clock_ms + 10 * DAY_MS, "register_not_before_ms": reg_from_ms, "register_not_after_ms": reg_to_ms,
            "h_days": h_days, "p0_per_day": p0_per_day, "p0_ref": p0_ref, "p0_rule": p0_rule}


# 2026-11-07T00:00:00Z clock. Registration: not before S + 24 h (2026-11-06T11:45:00Z, the earliest end of a smoke that began at S = 2026-11-05T11:45:00Z and ran >= 24 h)
# and not after 2026-11-06T19:00:00Z (QP on #1202: the first A3 run after the registration is 19:10Z, and it must land before the clock; the shadow starts 22:55:00Z at the earliest and the
# scorer needs registered_at_ms before the process start; Am.24 item 7 starts it >= 1 h before the clock).
ROW_1107: Mapping[str, Any] = _row(
    1_794_009_600_000, reg_from_ms=1_793_965_500_000, reg_to_ms=1_793_991_600_000, h_days=24, p0_per_day=35.333333333333336,
    p0_ref="job #1413 (Q50's P0, reused as Amendment 18 item 3 reuses it for s1; valid for a clock at or before 2026-11-07T00:00Z; Amendment 24 item 5)",
    p0_rule="reused, fixed")
# 2026-11-10T00:00:00Z first slip. Registration on 2026-11-09 (the latest 3 closed tip days 11-06, 11-07, 11-08 precede it; Amendment 24 item 5), not after 19:00Z (the 19:10Z A3 run, as above).
# The slip's P0 is a tip count made before the registration: it is a REQUIRED registration argument for this row (no value is compiled here).
ROW_1110: Mapping[str, Any] = _row(
    1_794_268_800_000, reg_from_ms=1_794_182_400_000, reg_to_ms=1_794_250_800_000, h_days=21, p0_per_day=None,
    p0_ref="the pinned tip-source P0 counter (Amendment 23 item 9) on 2026-11-06, 11-07 and 11-08; notebooked before the registration (Amendment 24 item 5)",
    p0_rule="slip: counted on the tip, fixed first")
ROWS: Mapping[int, Mapping[str, Any]] = {r["clock_start_ms"]: r for r in (ROW_1107, ROW_1110)}


def row_for_clock(clock_start_ms: int) -> Mapping[str, Any]:
    """The compiled row of a pre-committed clock. Any other clock is refused: there is no 11-08 or 11-09 clock, and a later slip is a new row in a new PR (Amendment 24 item 9)."""
    if isinstance(clock_start_ms, bool) or clock_start_ms not in ROWS:
        raise ValueError(f"clock {clock_start_ms!r} is not a pre-committed s1t clock {sorted(ROWS)} (Amendment 24 item 9)")
    return ROWS[clock_start_ms]


def instant_rule(counted_ms: Iterable[int], open_ms: Iterable[int], *, now_ms: int, row: Mapping[str, Any] = ROW_1107) -> dict[str, Any]:
    """The s1t SCREEN instant: N_s = 200, or >= 100 at B >= S_cap, by E, for the row's clock."""
    return si.sized_rule(counted_ms, open_ms, start_ms=int(row["clock_start_ms"]), end_ms=int(row["window_end_ms"]), now_ms=now_ms, n_declared=int(row["screen_n"]),
                         cap_ms=int(row["screen_cap_ms"]))


def confirmation_instant(counted_ms: Iterable[int], open_ms: Iterable[int], *, screen_instant_ms: int, now_ms: int, row: Mapping[str, Any] = ROW_1107) -> dict[str, Any]:
    """The s1t CONFIRMATION instant: >= 300 counted trades decided at or after the screen instant on >= 5 further UTC dates, or >= 100 at B = E_c."""
    if int(screen_instant_ms) % DAY_MS:
        raise ValueError("the screen read's instant is a 00:00Z boundary")
    end = int(row["confirm_end_ms"])
    r = si.sized_rule(counted_ms, open_ms, start_ms=int(screen_instant_ms), end_ms=end, now_ms=now_ms, n_declared=int(row["confirm_n"]), cap_ms=end)
    return dict(r, rule=CONFIRM_RULE)


def record_doc(bot_id: str, cls: Mapping[str, list], *, row: Mapping[str, Any], now_ms: int, attempt_definition: str | None = None) -> dict[str, Any] | None:
    """The screen record, or None while nothing is decided (PENDING / NOT_YET). Counts and instants only, over decisions in [clock, boundary)."""
    if bot_id != S1T_ID:
        raise ValueError(f"the s1t instant tool records only {S1T_ID}, not {bot_id!r}")
    clock, end = int(row["clock_start_ms"]), int(row["window_end_ms"])
    r = instant_rule(cls["counted"], cls["open"], now_ms=now_ms, row=row)
    if r["status"] not in (READ, ENDED):
        return None
    b = r["instant_ms"] if r["status"] == READ else end
    inwin = lambda xs: [d for d in xs if clock <= d < b]  # noqa: E731
    c, e = inwin(cls["counted"]), inwin(cls["excluded"])
    ref = [(d, why) for d, why in cls["refused"] if clock <= d < b]
    return {"bot_id": bot_id, "rule": RULE, "status": r["status"], "read_instant_ms": r["instant_ms"], "boundary_ms": b, "clock_start_ms": clock, "end_seal_ms": end,
            "counted_before": len(c), "counted_dates": len({d // DAY_MS for d in c}), "excluded_before": len(e), "attempts_before": len(c) + len(e), "open_before_read": 0,
            "refused_before": len(ref), "refused_by_reason_before": dict(sorted(collections.Counter(why for _, why in ref).items())), "attempt_definition": attempt_definition,
            "screen_n": int(row["screen_n"]), "screen_cap_ms": int(row["screen_cap_ms"]), "floor": MIN_TRADES, "branch": r.get("branch"),
            "note": "counts and instants only; read-instant.json (the scorer's attempt boundary) is not this book's read instant"}


def record(bot_dir: Path, doc: Mapping[str, Any]) -> str:
    """Write once. Returns "written", "unchanged" (an equal record exists) or "conflict" (a different one exists; it is kept)."""
    path = Path(bot_dir) / INSTANT_FILE
    if qi.write_once(path, doc):
        return "written"
    old = json.loads(path.read_text())
    keys = ("status", "read_instant_ms", "boundary_ms", "clock_start_ms", "end_seal_ms", "counted_before", "excluded_before", "refused_before")
    return "unchanged" if all(old.get(k) == doc.get(k) for k in keys) else "conflict"
