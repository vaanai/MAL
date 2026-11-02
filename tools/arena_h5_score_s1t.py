"""Registration line and scoring wrapper for the tip-source Q50 successor `h5v1q50-boostfloor-s1t` around the pinned scorer (DEC-031 Amendment 24; DEC-031 Amendment 22 items 8.1 and 9 carried). PAPER ONLY.

Modelled on `tools/arena_h5_score_q50s.py` (s1's wrapper). That module, `tools/arena_h5_score_q50.py` (Q50's) and the scorer `tools/arena_h5_score.py` are imported and NOT edited; this module reuses
their pure helpers (start-record check, pv s0 filter, line builder) with s1t's table row and replaces BOTS / score_book / summarize / write_ledger for the duration of main(). Importing it has no side effect.

- The book's values are the compiled table row `tools.arena_h5_s1t_instant.ROW_1107` or `ROW_1110`, chosen by the registry line's own `clock_start_ms` (a clock that is not one of the two
  pre-committed clocks is refused). There is no sha, clock, end or stake flag anywhere.
- `frozen_sha` is S*' (the commit of tag `q50-succ-tip-1`, afa4971d...). A shadow dir feeds the book only if EVERY start record carries code_sha S*' (the scorer's identity check), q_star_sol == 50.0,
  the Q50 rule and its sha256, and h5_look2.end_only_ms == 1796083200000 (SUCC_END_MS, which S*' shares with S*). Anything else is refused by the scorer's own refusal path with the reason.
- BOTH segment out dirs are passed, one `--shadow-dir` each (the 2026-11-20T21:00Z hand-off writes a new out dir). Every `--shadow-dir` must be named `h5-s1t-book-<YYYYMMDDTHHMMSSZ>`: the smoke's
  out dir (`h5-smoke-s1t-*`, sealed, never counted, scored or read), s1's, Q50's and HV4-T's dirs are refused before the scorer is called.
- pv triggers whose s0_t_recv_ms is missing, not an integer or below clock + 60,000 ms are dropped before score_book (counted, never attempts), with the a2 split counters.
- THE READ INSTANT: on every `score` run the screen instant (200 counted trades on 5 dates, or 100 at B >= S_cap = clock + 10 d, by E; tools.arena_h5_s1t_instant) is computed from the scorer's own
  BookResult (decision timestamps and class only) and, unless --dry-run, recorded ONCE in <ledger>/h5v1q50-boostfloor-s1t/s1t-read-instant.json. The scorer's read-instant.json is an attempt-boundary
  record only. The confirmation instant (300 after the screen instant, or 100 at E_c = E = 2026-12-01T00Z) is `tools.arena_h5_s1t_instant.confirmation_instant`; it is a function of the ledger, not a file.
- Subcommand whitelist: `score` only. `register` (the registration is `tools/s1t_register.py`), `halt` (set_window_end never reaches this id), `refit`, `-h`, unknown and `--` exit 2.
  `score` needs an explicit --shadow-dir.
- NOT wired here: the reader (`tools/arena_h5_books.py` keys EXIT_RULES, AM11_BOOKS and check_recorded_instant on s1's id only). A read of s1t needs that wiring in its own reviewed PR.
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from tools import arena_h5_s1t_instant as ti
from tools import arena_h5_score as sc
from tools import arena_h5_score_q50 as q50w
from tools import arena_h5_score_q50s as w
from tools.arena_h5_score_a2 import DROP_KEYS

SCORER_SHA256 = q50w.SCORER_SHA256
S1T_ID = ti.S1T_ID
S1T_BOTS = {S1T_ID: ti.ROW_1107["exit_rule"]}
A1_BOTS = q50w.A1_BOTS
ALLOWED = ("score",)
SCORE_FLAGS = q50w.SCORE_FLAGS
CHANGE_KEYS = w.CHANGE_KEYS
BOOK_DIR_RE = re.compile(r"h5-s1t-book-20[0-9]{2}[01][0-9][0-3][0-9]T[0-2][0-9][0-5][0-9][0-5][0-9]Z")

if sc.scorer_sha256() != SCORER_SHA256:
    raise RuntimeError("tools/arena_h5_score.py is not the pinned scorer")


def row_for_entry(entry: Mapping) -> Mapping:
    """The compiled row of this registry line's clock (ValueError when the clock is not a pre-committed s1t clock)."""
    return ti.row_for_clock(entry["clock_start_ms"])


def start_problem(start: Mapping, row: Mapping = ti.ROW_1107) -> str | None:
    """Why this start record is not an s1t process's (None: it is): q_star, rule, rule sha256 and S*''s end_only_ms, as s1's check with s1t's row."""
    return w.start_problem(start, row)


def filtered_dirs(entry: dict, dirs: Sequence[sc.ShadowDir]):
    """Dirs guarded by the start-record check, with pv triggers kept only for pools whose s0 is at or after clock + 60 s (s1's filter, s1t's row)."""
    return w.filtered_dirs(entry, dirs, row_for_entry(entry))


def s1t_instant(bot: str, res: sc.BookResult, entry: dict, ledger_root: Path, now_ms: int, *, dry_run: bool) -> dict:
    """The screen instant on this run's BookResult (counts and instants only), recorded write-once unless dry_run. The end is E always: an A3 end moves the window (the scorer skips later
    decisions) and is judged by Amendment 4 / 14, not by this rule."""
    from tools import arena_h5_q50_instant as qi
    row = row_for_entry(entry)
    cls = qi.classes_from_result(res)
    r = ti.instant_rule(cls["counted"], cls["open"], now_ms=now_ms, row=row)
    out = {"rule": ti.RULE, "status": r["status"], "instant_ms": r["instant_ms"], "boundary_ms": r["boundary_ms"], "file": ti.INSTANT_FILE}
    n_ref = len(cls["refused"])
    doc = ti.record_doc(bot, cls, row=row, now_ms=now_ms, attempt_definition=sc.attempt_disclosure(n_ref, len(res.decisions) + n_ref))
    if doc is None:
        out["record"] = "not decided"
    elif dry_run:
        out["record"] = "dry run: not written"
    else:
        out["record"] = ti.record(Path(ledger_root) / bot, doc)
        if out["record"] == "conflict":
            res.counts["s1t_read_instant_conflict"] += 1
    return out


@contextlib.contextmanager
def s1t_bots() -> Iterator[None]:
    """BOTS = exactly the s1t id inside the block; score_book / summarize / write_ledger wrapped; all restored on exit."""
    before = dict(sc.BOTS)
    if before != A1_BOTS:
        raise RuntimeError(f"scorer BOTS is not the pinned a1 pair: {sorted(before)}")
    orig_score, orig_summ, orig_write = sc.score_book, sc.summarize, sc.write_ledger
    seen: dict[str, dict] = {}

    def score_book(bot, entry, dirs):
        if bot != S1T_ID:
            raise RuntimeError(f"the s1t wrapper scores only {S1T_ID}, not {bot!r}")
        row = row_for_entry(entry)
        fdirs, dropped = w.filtered_dirs(entry, dirs, row)
        res = orig_score(bot, entry, fdirs)
        for k in DROP_KEYS:
            res.counts[k] += dropped[k]
        seen[bot] = {"entry": entry}
        return res

    def write_ledger(bot, rows, exclusions, ledger_root, counts, dry_run=False, **kw):
        out = orig_write(bot, rows, exclusions, ledger_root, counts, dry_run=dry_run, **kw)
        seen.setdefault(bot, {}).update(ledger_root=Path(ledger_root), dry_run=bool(dry_run))
        return out

    def summarize(bot, res, now_ms):
        out = orig_summ(bot, res, now_ms)
        for k in DROP_KEYS:
            out[k] = res.counts[k]
        st = seen.get(bot) or {}
        if "entry" not in st or "ledger_root" not in st:
            raise RuntimeError(f"{bot}: summarize before score_book and write_ledger: the s1t read instant cannot be recorded")
        out["s1t_read_instant"] = s1t_instant(bot, res, st["entry"], st["ledger_root"], int(now_ms), dry_run=st["dry_run"])
        return out

    try:
        sc.BOTS.clear()
        sc.BOTS.update(S1T_BOTS)
        if sc.BOTS != S1T_BOTS or len(sc.BOTS) != 1:
            raise RuntimeError(f"BOTS is not exactly the s1t id: {sorted(sc.BOTS)}")
        sc.score_book, sc.summarize, sc.write_ledger = score_book, summarize, write_ledger
        yield
    finally:
        sc.score_book, sc.summarize, sc.write_ledger = orig_score, orig_summ, orig_write
        sc.BOTS.clear()
        sc.BOTS.update(before)


def s1t_line(row: Mapping, *, sockets: int, endpoints: Sequence[str], host: str, reg_ms: int) -> dict:
    """The bots.json line: s1's line shape from s1t's row (key-free endpoints only)."""
    return w.s1_line(row, sockets=sockets, endpoints=endpoints, host=host, reg_ms=reg_ms)


def add_line(bots: dict, row: Mapping, *, sockets: int, endpoints: Sequence[str], host: str, reg_ms: int) -> dict:
    """Pure: add the s1t line to a loaded registry dict (in place) and return it, or return the existing line if it is the same book. Raises ValueError on any doubt. The caller holds the
    registry lock and writes the file. The registration window is the row's, and registration at or after the clock is refused."""
    clock = int(row["clock_start_ms"])
    if row is not ti.row_for_clock(clock):
        raise ValueError("the row is not a compiled s1t row")
    if not (int(row["register_not_before_ms"]) <= reg_ms <= int(row["register_not_after_ms"])):
        raise ValueError(f"registration at {reg_ms} is outside the row's registration window [{row['register_not_before_ms']}, {row['register_not_after_ms']}] (DEC-031 Amendment 24 items 5 and 7)")
    if reg_ms >= clock:
        raise ValueError("registration is at or after the clock")
    entry = s1t_line(row, sockets=sockets, endpoints=endpoints, host=host, reg_ms=reg_ms)
    if S1T_ID in bots:
        old = bots[S1T_ID]
        if any(old.get(k) != entry[k] for k in CHANGE_KEYS):
            raise ValueError(f"{S1T_ID} is registered with a different sha, clock, end, stake or rule: a change is a new bot_id")
        return old
    bots[S1T_ID] = entry
    return entry


def shadow_dir_problem(argv: Sequence[str]) -> str | None:
    """None when argv has at least one --shadow-dir and every one is named h5-s1t-book-<YYYYMMDDTHHMMSSZ>; else why not."""
    dirs, it = [], iter(argv)
    for a in it:
        if a == "--shadow-dir":
            dirs.append(next(it, ""))
        elif a.startswith("--shadow-dir="):
            dirs.append(a.split("=", 1)[1])
    if not dirs:
        return "arena_h5_score_s1t score needs an explicit --shadow-dir (one per segment out dir: h5-s1t-book-<YYYYMMDDTHHMMSSZ>)"
    for d in dirs:
        if ".." in d or not BOOK_DIR_RE.fullmatch(os.path.basename(os.path.normpath(d))):
            return f"--shadow-dir {d!r} is not an s1t book out dir (h5-s1t-book-<YYYYMMDDTHHMMSSZ>); the smoke's, s1's, Q50's and HV4-T's dirs are never scored here"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        print("REFUSED: arena_h5_score_s1t does not accept '--'", file=sys.stderr)
        return 2
    if argv and not argv[0].startswith("-"):
        cmd = argv[0]
    elif argv and argv[0].split("=", 1)[0] in SCORE_FLAGS:
        cmd, argv = "score", ["score", *argv]
    elif not argv:
        cmd, argv = "score", ["score"]
    else:
        cmd = argv[0]
    if cmd not in ALLOWED:
        print(f"REFUSED: arena_h5_score_s1t allows only {', '.join(ALLOWED)}; {cmd!r} is not allowed here (the registration is tools/s1t_register.py)", file=sys.stderr)
        return 2
    why = shadow_dir_problem(argv[1:])
    if why:
        print(f"REFUSED: {why}", file=sys.stderr)
        return 2
    with s1t_bots():
        try:
            return sc.main(argv)
        except ValueError as e:  # a registry clock that is not a pre-committed s1t clock
            print(f"REFUSED: {e}", file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(main())
