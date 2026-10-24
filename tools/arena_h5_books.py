"""The H5 arena book list of a read, from the REGISTERED books file (v2: `$HOME/data/arena/a3-books.json` on mal-fast-0, sha256 819c699d..., DEC-031 Amendments 4 and 5;
verbatim copy in ARTIFACTS/lab/arena-records-2026-10-24-q50.md section 2). PAPER ONLY. It reads the books file (a list of bot ids, no outcome) and nothing else.

    {"legacy_bot_ids": ["h5v1-boostfloor-a1", "h5v2-boostclock-a1"], "new_rule_bot_ids": ["h5v1-boostfloor-a2", "h5v2-boostclock-a2", "h5v1q50-boostfloor-a1"]}

The schema is tools/a3_halt.validate_books' (exactly the two keys, each a list of unique strings, no id in both); the registry check of a3_halt is not repeated here (this tool never opens
bots.json). The file's sha256 must equal the pin (default BOOKS_V2_SHA256; --books-sha256 overrides it, for fixtures). Every listed id needs a KNOWN exit rule (the scorer's a1 BOTS, the
a2 wrapper's A2_BOTS, the Q50 wrapper's Q50_BOTS); an unknown id refuses the whole list.

THE READ INSTANT OF EACH BOOK (`instant_file`): `read-instant.json` (the pinned scorer's attempt boundary, DEC-031 section 2) for every book except `h5v1q50-boostfloor-a1`, whose read
instant is DEC-031 Amendment 11's, recorded write-once in `q50-read-instant.json` (tools/arena_h5_q50_instant.py). For Q50 the scorer's read-instant.json is never the instant.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from tools import arena_h5_q50_instant as qi
from tools import arena_h5_score as h5s
from tools.arena_h5_score_a2 import A2_BOTS
from tools.arena_score import Refused

BOOKS_V2_SHA256 = "819c699d74a2fab8b8c67916a5732e54950d158c514d8fa24dabc9b65b7665c0"
BOOKS_KEYS = ("legacy_bot_ids", "new_rule_bot_ids")
Q50_ID = qi.Q50_ID
EXIT_RULES: dict[str, str] = {**h5s.BOTS, **A2_BOTS, Q50_ID: "v1"}  # the Q50 wrapper's Q50_BOTS (tools/arena_h5_score_q50.py); a test checks they agree


class Books(dict):
    """bot_id -> exit rule, in books-file order (legacy first). `.new_rule` is the new-rule list (the default book set of a read), `.sha256` the file's."""

    new_rule: list[str]
    legacy: list[str]
    sha256: str
    path: str


def load_books(path: str | os.PathLike, sha256: str | None = None) -> Books:
    """Validate and load the books file. Raises Refused on any doubt, before any ledger is touched."""
    p = Path(path)
    data = p.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    want = sha256 or BOOKS_V2_SHA256
    if got != want:
        raise Refused(f"books file {os.fspath(path)!r} has sha256 {got}, not the registered {want}")
    try:
        doc = json.loads(data)
    except ValueError:
        raise Refused("books file is not JSON") from None
    if not isinstance(doc, dict) or set(doc) != set(BOOKS_KEYS):
        raise Refused(f"books file must be an object with exactly the keys {list(BOOKS_KEYS)}")
    lists = {}
    for k in BOOKS_KEYS:
        v = doc[k]
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v) or len(set(v)) != len(v):
            raise Refused(f"books file {k} must be a list of unique non-empty strings")
        lists[k] = v
    both = sorted(set(lists["legacy_bot_ids"]) & set(lists["new_rule_bot_ids"]))
    if both:
        raise Refused(f"books file lists {both} in both lists")
    out = Books()
    for b in lists["legacy_bot_ids"] + lists["new_rule_bot_ids"]:
        if b not in EXIT_RULES:
            raise Refused(f"books file lists {b!r}, which has no known exit rule {sorted(EXIT_RULES)}")
        out[b] = EXIT_RULES[b]
    out.legacy, out.new_rule, out.sha256, out.path = list(lists["legacy_bot_ids"]), list(lists["new_rule_bot_ids"]), got, os.fspath(path)
    return out


def instant_file(bot_id: str) -> str:
    """The file that holds the book's read instant: the Am.11 write-once file for Q50, the scorer's record for every other book."""
    return qi.INSTANT_FILE if bot_id == Q50_ID else h5s.READ_INSTANT_FILE


def check_recorded_instant(bot_id: str, doc: Mapping[str, Any], counted_decisions: list[int], all_decisions: list[int], ri: int) -> str | None:
    """THE ONE place a read recomputes a book's instant from the ledger's decision timestamps (one per pool) and compares it with the record. Returns None if they agree, else why.
    Q50 (Am.11 item 1): the first boundary with 100 counted trades on 5 dates after the record's clock, with nothing open (the seal already refused an open attempt) and the
    record's end-seal; every other book: the pinned scorer's read_instant over every attempt."""
    if bot_id == Q50_ID:
        r = qi.instant_rule(counted_decisions, [], start_ms=int(doc["clock_start_ms"]), end_ms=int(doc["end_seal_ms"]), now_ms=ri)
        return None if r["status"] == qi.READ and r["instant_ms"] == ri else f"Am.11 rule recomputed from the ledger gives {r['status']} {r['instant_ms']}"
    return None if h5s.read_instant(all_decisions, ri) == ri else "differs"
