"""Constants and the read instant of the Q50 successor book `h5v1q50-boostfloor-s1` (DEC-031 Amendments 17 and 18; plan C2 and C3). PAPER ONLY. COUNTS ONLY.

This module is NEW. `tools/arena_h5_q50_instant.py` (Q50's) is imported and not edited: its `instant_rule` is parameterized by start and end, and is the one function that decides
the instant (DEC-031 Amendment 18 item 7: Amendment 11 items 1 to 5 word for word, with `h5v1q50-boostfloor-a1` read as `h5v1q50-boostfloor-s1`, clock 1793923200000, and the
end-seal read as E_s1 = 2026-11-20T00:00:00Z = 1795132800000).

THE TABLE ROW (`S1`) is the one compiled source of the book's values: frozen_sha S* (tag q50-succ-base-1), clock, window end (H = 14 d), rule and its sha256, Q* 50, exit v1, the
s0 margin, the registration window (Amendment 18 item 4: 2026-11-04T00:00Z to 2026-11-05T12:00Z) and the start record's `h5_look2.end_only_ms` (S*'s SUCC_END_MS). A slip of the
clock (Amendment 17 item 1) is a new PR with a new row, not a flag.

THE RECORD: <ledger>/<bot_id>/q50s1-read-instant.json, created O_CREAT | O_EXCL (write-once). It carries the same fields as Q50's Am.11 record (`end_seal_ms` = E_s1) and the rule
text below, so tools/arena_h5_legs.gate and tools/arena_h5_read can key their seal on it (arena_h5_books.AM11_FILES).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from tools import arena_h5_q50_instant as qi

S1_ID = "h5v1q50-boostfloor-s1"
RULE = "DEC-031 Amendment 18 item 7 (Amendment 11 items 1 to 5, counted trades, read for h5v1q50-boostfloor-s1)"
CONFIRM_RULE = "DEC-031 Amendment 18 item 7 (confirmation: counted trades after the screen instant, by E_s1)"
INSTANT_FILE = "q50s1-read-instant.json"
MIN_TRADES, MIN_DATES, DAY_MS = qi.MIN_TRADES, qi.MIN_DATES, qi.DAY_MS
READ, PENDING, NOT_YET, ENDED = qi.READ, qi.PENDING, qi.NOT_YET, qi.ENDED

S1: Mapping[str, Any] = {
    "bot_id": S1_ID,
    "frozen_sha": "5c2ab7c64b21358619e37068c6a53e4d183512ea",  # S*, tag q50-succ-base-1; NOT F50 a5f41370 (DEC-031 Amendment 18 item 2.3)
    "clock_start_ms": 1_793_923_200_000,  # 2026-11-06T00:00:00Z, exactly Q50's end-seal
    "window_end_ms": 1_795_132_800_000,  # E_s1 = 2026-11-20T00:00:00Z = clock + H (H = 14 d); written into the bots.json line at registration
    "rule": "H5-BOOSTFLOOR-Q50 v1",
    "rule_sha256": "b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4",  # EXP/EXP-024-RULE-Q50.md, as Q50's
    "q_star_sol": 50.0,
    "exit_rule": "v1",
    "s0_margin_ms": 60_000,
    "end_only_ms": 1_796_083_200_000,  # S*'s SUCC_END_MS (2026-12-01T00:00:00Z): h5_look2.end_only_ms of every start record
    "register_not_before_ms": 1_793_750_400_000,  # 2026-11-04T00:00:00Z
    "register_not_after_ms": 1_793_880_000_000,  # 2026-11-05T12:00:00Z
}
CLOCK_MS: int = S1["clock_start_ms"]
END_MS: int = S1["window_end_ms"]
EARLIEST_BOUNDARY_MS = CLOCK_MS + 5 * DAY_MS  # 2026-11-11T00:00:00Z: 5 distinct decision dates cannot be met earlier


def instant_rule(counted_ms: Iterable[int], open_ms: Iterable[int], *, now_ms: int, start_ms: int = CLOCK_MS, end_ms: int = END_MS) -> dict[str, Any]:
    """Amendment 18 item 7 = Amendment 11 item 1 with the s1 clock and E_s1 (qi.instant_rule is the one implementation)."""
    return qi.instant_rule(counted_ms, open_ms, start_ms=start_ms, end_ms=end_ms, now_ms=now_ms)


def confirmation_instant(counted_ms: Iterable[int], open_ms: Iterable[int], *, screen_instant_ms: int, now_ms: int, end_ms: int = END_MS) -> dict[str, Any]:
    """The confirmation (>= 100 counted trades decided at or after the screen instant on >= 5 further UTC dates), ending at E_s1."""
    return dict(qi.confirmation_instant(counted_ms, open_ms, screen_instant_ms=screen_instant_ms, end_ms=end_ms, now_ms=now_ms), rule=CONFIRM_RULE)


def record_doc(bot_id: str, cls: Mapping[str, list], *, clock_start_ms: int, end_ms: int, now_ms: int, attempt_definition: str | None = None) -> dict[str, Any] | None:
    """The record of item 1 / item 2 for s1, or None while nothing is decided. qi.record_doc's fields, with this book's rule text."""
    if bot_id != S1_ID:
        raise ValueError(f"the s1 instant tool records only {S1_ID}, not {bot_id!r}")
    doc = qi.record_doc(bot_id, cls, clock_start_ms=clock_start_ms, end_ms=end_ms, now_ms=now_ms, attempt_definition=attempt_definition)
    if doc is not None:
        doc["rule"] = RULE
        doc["note"] = "counts and instants only; read-instant.json (the scorer's attempt boundary) is not this book's read instant"
    return doc


def record(bot_dir: Path, doc: Mapping[str, Any]) -> str:
    """Write once. Returns "written", "unchanged" (an equal record exists) or "conflict" (a different one exists; it is kept)."""
    import json
    path = Path(bot_dir) / INSTANT_FILE
    if qi.write_once(path, doc):
        return "written"
    old = json.loads(path.read_text())
    keys = ("status", "read_instant_ms", "boundary_ms", "clock_start_ms", "end_seal_ms", "counted_before", "excluded_before", "refused_before")
    return "unchanged" if all(old.get(k) == doc.get(k) for k in keys) else "conflict"
