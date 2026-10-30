"""Constants and the read instant of the Q* = 60 sibling book `h5v1q60-boostfloor-a1` (DEC-031 Amendment 19; plan Q5, the item 5 "Tools fallback (B3)" form). PAPER ONLY.
COUNTS ONLY.

This module is NEW and stands alone. It has the form of `tools/arena_h5_q50s_instant.py` (s1's), which, like Q50's `tools/arena_h5_q50_instant.py`, is imported or mirrored
but NOT edited. Q50's `instant_rule` is parameterized by start and end and is the one function that decides the instant. DEC-031 Amendment 19 item 7: Amendment 11 items 1
to 5 word for word, with `h5v1q50-boostfloor-a1` read as `h5v1q60-boostfloor-a1`, its clock 1794009600000, and the end-seal read as E_q60 = 2026-11-21T00:00:00Z =
1795219200000.

THE TABLE ROW (`Q60`) is the one compiled source of the book's values:
- frozen_sha q60_sha (the commit of tag q60-base-1);
- clock, window end (H = 14 d), rule and its sha256, Q* 60, exit v1, the s0 margin;
- the registration window (plan section 4: 2026-11-05T14:30Z to 18:30Z, after s1's window);
- the start record's `h5_look2.end_only_ms` (S*'s SUCC_END_MS, which Amendment 19 item 2 leaves unchanged).
A slip of the clock (Amendment 19 item 4) is a new PR with a new row, not a flag.

THE RECORD is <ledger>/<bot_id>/q60-read-instant.json, created O_CREAT | O_EXCL (write-once). It carries the same fields as Q50's Am.11 record (`end_seal_ms` = E_q60) and
the rule text below, so tools/arena_h5_legs.gate and tools/arena_h5_read can key their seal on it (arena_h5_books.AM11_FILES).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from tools import arena_h5_q50_instant as qi

Q60_ID = "h5v1q60-boostfloor-a1"
RULE = "DEC-031 Amendment 19 item 7 (Amendment 11 items 1 to 5, counted trades, read for h5v1q60-boostfloor-a1)"
CONFIRM_RULE = "DEC-031 Amendment 19 item 7 (confirmation: counted trades after the screen instant, by E_q60)"
INSTANT_FILE = "q60-read-instant.json"
MIN_TRADES, MIN_DATES, DAY_MS = qi.MIN_TRADES, qi.MIN_DATES, qi.DAY_MS
READ, PENDING, NOT_YET, ENDED = qi.READ, qi.PENDING, qi.NOT_YET, qi.ENDED

Q60: Mapping[str, Any] = {
    "bot_id": Q60_ID,
    # q60_sha = the commit tagged q60-base-1 (DEC-031 Amendment 19 item 2). CANDIDATE: the tag is pending QP-RULING-1030-q60-proofs. This row must equal the tag's
    # commit at merge; if the tag lands on another commit, the row changes before merge.
    "frozen_sha": "c0cfff92247337d1c16e070d7532960800665a86",
    "clock_start_ms": 1_794_009_600_000,  # 2026-11-07T00:00:00Z (Amendment 19 item 4)
    "window_end_ms": 1_795_219_200_000,  # E_q60 = 2026-11-21T00:00:00Z = clock + H (H = 14 d, Amendment 19 item 3); written into the bots.json line at registration
    "rule": "H5-BOOSTFLOOR-Q60 v1",
    "rule_sha256": "f36afaae00edd5b8292fdbe732c6034251cc1371afb6c1e54b50e548a91717b9",  # EXP/EXP-024-RULE-Q60.md (Amendment 19 item 1)
    "q_star_sol": 60.0,
    "exit_rule": "v1",
    "s0_margin_ms": 60_000,
    "end_only_ms": 1_796_083_200_000,  # S*'s SUCC_END_MS (2026-12-01T00:00:00Z), unchanged at q60_sha: h5_look2.end_only_ms of every start record
    "register_not_before_ms": 1_793_889_000_000,  # 2026-11-05T14:30:00Z (plan section 4)
    "register_not_after_ms": 1_793_903_400_000,  # 2026-11-05T18:30:00Z (plan section 4)
}
CLOCK_MS: int = Q60["clock_start_ms"]
END_MS: int = Q60["window_end_ms"]
EARLIEST_BOUNDARY_MS = CLOCK_MS + 5 * DAY_MS  # 2026-11-12T00:00:00Z: 5 distinct decision dates cannot be met earlier


def instant_rule(counted_ms: Iterable[int], open_ms: Iterable[int], *, now_ms: int, start_ms: int = CLOCK_MS, end_ms: int = END_MS) -> dict[str, Any]:
    """Amendment 19 item 7 = Amendment 11 item 1 with the Q60 clock and E_q60 (qi.instant_rule is the one implementation)."""
    return qi.instant_rule(counted_ms, open_ms, start_ms=start_ms, end_ms=end_ms, now_ms=now_ms)


def confirmation_instant(counted_ms: Iterable[int], open_ms: Iterable[int], *, screen_instant_ms: int, now_ms: int, end_ms: int = END_MS) -> dict[str, Any]:
    """The confirmation (>= 100 counted trades decided at or after the screen instant on >= 5 further UTC dates), ending at E_q60."""
    return dict(qi.confirmation_instant(counted_ms, open_ms, screen_instant_ms=screen_instant_ms, end_ms=end_ms, now_ms=now_ms), rule=CONFIRM_RULE)


def record_doc(bot_id: str, cls: Mapping[str, list], *, clock_start_ms: int, end_ms: int, now_ms: int, attempt_definition: str | None = None) -> dict[str, Any] | None:
    """The record of item 1 / item 2 for Q60, or None while nothing is decided. qi.record_doc's fields, with this book's rule text."""
    if bot_id != Q60_ID:
        raise ValueError(f"the Q60 instant tool records only {Q60_ID}, not {bot_id!r}")
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
