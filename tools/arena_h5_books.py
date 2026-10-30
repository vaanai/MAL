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
from tools import arena_h5_q50s_instant as s1i
from tools import arena_h5_score as h5s
from tools.arena_h5_score_a2 import A2_BOTS
from tools.arena_score import Refused

BOOKS_V2_SHA256 = "819c699d74a2fab8b8c67916a5732e54950d158c514d8fa24dabc9b65b7665c0"
# v3 = v2 with the Q50 successor appended to new_rule_bot_ids (DEC-031 Amendment 18 item 6.1; plan C4/C5). The sha256 is of the canonical text `books_v3_text` makes from v2's bytes.
BOOKS_V3_SHA256 = "f8e34748ad414240aeba803e298aaadc799900d2987c51d9535ed7716ca7457c"
BOOKS_REGISTERED = (BOOKS_V2_SHA256, BOOKS_V3_SHA256)  # the default pin of load_books: either registered text (v2 stays loadable byte for byte)
BOOKS_KEYS = ("legacy_bot_ids", "new_rule_bot_ids")
Q50_ID = qi.Q50_ID
S1_ID = s1i.S1_ID
EXIT_RULES: dict[str, str] = {**h5s.BOTS, **A2_BOTS, Q50_ID: "v1", S1_ID: "v1"}  # the Q50 wrapper's Q50_BOTS (tools/arena_h5_score_q50.py) and the s1 table row; tests check they agree
# the Amendment 11 write-once instant files and the rule text each must carry (the seal in tools/arena_h5_legs.gate keys on this)
AM11_FILES: dict[str, str] = {qi.INSTANT_FILE: qi.RULE, s1i.INSTANT_FILE: s1i.RULE}
AM11_BOOKS: dict[str, str] = {Q50_ID: qi.INSTANT_FILE, S1_ID: s1i.INSTANT_FILE}


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
    want = (sha256,) if sha256 else BOOKS_REGISTERED
    if got not in want:
        raise Refused(f"books file {os.fspath(path)!r} has sha256 {got}, not the registered {' or '.join(want)}")
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


def books_v3_text(v2_bytes: bytes) -> str:
    """The canonical text of books v3 from v2's exact bytes: v2 with the successor appended to new_rule_bot_ids. Refuses any input that is not the registered v2."""
    if hashlib.sha256(v2_bytes).hexdigest() != BOOKS_V2_SHA256:
        raise Refused("books v3 is made from the registered v2 text only")
    doc = json.loads(v2_bytes)
    if S1_ID in doc["new_rule_bot_ids"] or S1_ID in doc["legacy_bot_ids"]:
        raise Refused(f"{S1_ID} is already listed")
    doc["new_rule_bot_ids"] = [*doc["new_rule_bot_ids"], S1_ID]
    return json.dumps(doc) + "\n"


def instant_file(bot_id: str) -> str:
    """The file that holds the book's read instant: the Am.11 write-once file for Q50 (q50-read-instant.json) and for s1 (q50s1-read-instant.json), the scorer's record for every other book."""
    return AM11_BOOKS.get(bot_id, h5s.READ_INSTANT_FILE)


def check_recorded_instant(bot_id: str, doc: Mapping[str, Any], counted_decisions: list[int], all_decisions: list[int], ri: int) -> str | None:
    """THE ONE place a read recomputes a book's instant from the ledger's decision timestamps (one per pool) and compares it with the record. Returns None if they agree, else why.
    Q50 (Am.11 item 1): the first boundary with 100 counted trades on 5 dates after the record's clock, with nothing open (the seal already refused an open attempt) and the
    record's end-seal; s1: tools.arena_h5_q50s_instant.instant_rule (DEC-031 power-n amendment item 8.1) with the record's clock and end, which must be the s1 table row's;
    every other book: the pinned scorer's read_instant over every attempt."""
    if bot_id == S1_ID and (doc.get("clock_start_ms"), doc.get("end_seal_ms")) != (s1i.CLOCK_MS, s1i.END_MS):
        return f"the record's clock {doc.get('clock_start_ms')} / end {doc.get('end_seal_ms')} are not the s1 table row's"
    if bot_id == S1_ID:  # s1: the power-n screen rule (200 counted trades, or 100 at B >= S_cap, on 5 dates, by E), never Q50's single threshold
        r = s1i.instant_rule(counted_decisions, [], start_ms=int(doc["clock_start_ms"]), end_ms=int(doc["end_seal_ms"]), now_ms=ri)
        return None if r["status"] == s1i.READ and r["instant_ms"] == ri else f"s1 power-n rule recomputed from the ledger gives {r['status']} {r['instant_ms']}"
    if bot_id in AM11_BOOKS:  # Q50 and s1 share Amendment 11's rule; the record carries each book's own clock and end
        r = qi.instant_rule(counted_decisions, [], start_ms=int(doc["clock_start_ms"]), end_ms=int(doc["end_seal_ms"]), now_ms=ri)
        return None if r["status"] == qi.READ and r["instant_ms"] == ri else f"Am.11 rule recomputed from the ledger gives {r['status']} {r['instant_ms']}"
    return None if h5s.read_instant(all_decisions, ri) == ri else "differs"
