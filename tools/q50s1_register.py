"""One-command registration of `h5v1q50-boostfloor-s1` in ITS OWN arena root, with its own A3 books files (DEC-031 Amendment 22 items 9 and 10; plan C5). PAPER ONLY. No outcome is read.

    python -m tools.q50s1_register --arena-root ARENA [--books-file ARENA/a3-books.json] --preclock-notebook-ref REF [--apply]

s1 runs in its own arena root on mal-fast-0 (`/data/mal/arena`, the root the pinned scorer and S*'s shadow accept; the manager creates it), with its own `bots.json` (only s1's line),
its own 3-key A3 books file `a3-books.json` (loop 2), and a 2-key read books file `a3-books-read.json` (`tools.arena_h5_books.load_books` takes exactly two keys and a pinned sha256).
Q50's root, registry and books file v2 are never touched: there is no books v2 -> v3 swap, no v2 byte copy and no v3.

Default is a DRY RUN: every check runs and nothing is written. With --apply, under the registry lock (`bots.json.lock`):
(1) refuse inside :04 to :25 of the 07, 13 and 19 UTC hours and inside 12:10 to 12:25Z (the A3 loop and dead-man windows);
(2) refuse if --arena-root resolves (realpath) to Q50's root `$HOME/data/arena`, if it is not an existing directory, or if its `bots.json` holds any bot other than s1;
(3) refuse if --books-file is not `<root>/a3-books.json`;
(4) the 3-key and 2-key texts must pass tools.a3_halt.validate_books against the registry WITH s1 added (every listed id registered; every registered h5 book listed);
(5) the s1 line is built from the compiled row (tools.arena_h5_score_q50s.add_line: window [11-04T00Z, 11-05T12Z], before the clock, window_end_ms pinned) with the key-free
    endpoints, default ["wss://api.mainnet-beta.solana.com", "wss://mainnet.helius-rpc.com/"] (Amendment 22 item 10; an endpoint with a query, fragment, userinfo or api-key is refused);
(6) both books files are published write-once (atomic hard link of an fsynced 0444 temp file; a file already there with other bytes is refused, with the same bytes it is kept), THEN the
    s1 line is written atomically: the line is the commit point, so a line implies both files, and a rerun after a crash between the two steps completes the registration.
A rerun on a finished registration is an "already_done" no-op that writes nothing. A registered line with a missing or different books file is a manual state, refused, not repaired.

The sha256 of both texts are module constants (A3_BOOKS_SHA256, A3_BOOKS_READ_SHA256), recomputed by the tests. main() prints the JSON report on its first line, then both texts verbatim
with their sha256 for the notebook copy. It touches no other file and starts no process.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Sequence

from tools import a3_halt
from tools import arena_h5_q50s_instant as si
from tools import arena_h5_score as sc
from tools import arena_h5_score_q50s as w
from tools.arena_score import Refused

NOTEBOOK_REF_RE = re.compile(r"[A-Za-z0-9_-]{6,80}")
FORBIDDEN_NOTE = "inside :04 to :25 of the 07, 13 or 19 UTC hour, or 12:10 to 12:25Z (A3 loop / dead-man windows)"
S1_ID = w.S1_ID
ENDPOINTS = ("wss://api.mainnet-beta.solana.com", "wss://mainnet.helius-rpc.com/")  # key-free (Amendment 22 item 10): the Helius key is added at process start by the start script
BAD_ENDPOINT_RE = re.compile(r"[?#@\s]|api[-_]?key", re.I)
A3_BOOKS_NAME = "a3-books.json"
A3_BOOKS_READ_NAME = "a3-books-read.json"
# Amendment 22 item 9, verbatim. The texts are fixed bytes: json.dumps of the dicts below plus a newline (the tests recompute both the texts and the sha256 pins).
A3_BOOKS_TEXT = json.dumps({"legacy_bot_ids": [], "new_rule_bot_ids": [S1_ID], "fees_check_bot_ids": [S1_ID]}) + "\n"
A3_BOOKS_READ_TEXT = json.dumps({"legacy_bot_ids": [], "new_rule_bot_ids": [S1_ID]}) + "\n"
A3_BOOKS_SHA256 = "9fdf6ee3047e49688f7d3e6974c3914a5a11f74bd25f830a94633e0b9986099e"
A3_BOOKS_READ_SHA256 = "799f6d2244e88fdeebf4a7c307e09b0a11f8545945dbe5c63d3f51da6000a8a0"


def forbidden_minute(now_ms: int) -> bool:
    t = dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc)
    if t.hour in (7, 13, 19) and 4 <= t.minute <= 25:
        return True
    return t.hour == 12 and 10 <= t.minute <= 25


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def q50_root() -> str:
    """Realpath of Q50's arena root, `$HOME/data/arena` (the root of `h5v1q50-boostfloor-a1`; s1 never shares it)."""
    return os.path.realpath(os.path.join(os.path.expanduser("~"), *sc.h5.ARENA_HOME_SUBDIR))


def check_root(arena_root: str | os.PathLike) -> Path:
    """The resolved s1 root. Refused: Q50's root (by realpath, so a symlink to it is caught too) and anything that is not an existing directory. Writes nothing."""
    real = os.path.realpath(arena_root)
    if real == q50_root():
        raise Refused(f"--arena-root {os.fspath(arena_root)!r} resolves to Q50's root {real}: s1 has its own root (DEC-031 Amendment 22 item 9)")
    if not os.path.isdir(real):
        raise Refused(f"--arena-root {os.fspath(arena_root)!r} is not an existing directory (the manager creates s1's root; this tool does not)")
    return Path(real)


def check_endpoints(endpoints: Sequence[str]) -> list[str]:
    eps = list(endpoints)
    if not eps or not all(isinstance(e, str) and e for e in eps):
        raise Refused("endpoints must be a non-empty list of non-empty strings")
    bad = [e for e in eps if BAD_ENDPOINT_RE.search(e)]
    if bad:
        raise Refused("an endpoint has a query, fragment, userinfo, whitespace or an api-key: the registry line records key-free endpoints only (Amendment 22 item 10)")
    return eps


def _file_state(path: Path, text: str) -> str:
    """'ok' (the file is there with exactly `text`) or 'missing'. Refused when a different file, a directory or an unreadable file is there."""
    try:
        have = path.read_bytes()
    except FileNotFoundError:
        return "missing"
    except OSError as e:
        raise Refused(f"cannot read {path}: {e}") from None
    if have != text.encode():
        raise Refused(f"{path} exists and is not the registered text (sha256 {_sha(have)}, want {_sha(text.encode())}): a different file is there, not mine to overwrite")
    return "ok"


def _publish_once(path: Path, text: str) -> None:
    """Write `text` to `path` only if nothing is there: fsynced 0444 temp file, then a hard link (atomic; FileExistsError instead of an overwrite), then the temp is removed."""
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode())
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise Refused(f"{path} appeared during the run: not overwritten") from None
    finally:
        os.unlink(tmp)


def run(arena_root: str | os.PathLike, books_path: str | os.PathLike | None = None, *, now_ms: int, apply: bool, row=si.S1, sockets: int = 3,
        endpoints: Sequence[str] = ENDPOINTS, host: str | None = None) -> dict:
    """Returns the report dict. Raises Refused / ValueError / a3_halt.Invalid on any doubt before anything is written."""
    for text, pin in ((A3_BOOKS_TEXT, A3_BOOKS_SHA256), (A3_BOOKS_READ_TEXT, A3_BOOKS_READ_SHA256)):
        if _sha(text.encode()) != pin:
            raise Refused("a books text does not match its sha256 pin: the module was edited")
    root = check_root(arena_root)  # structural refusals first: they do not depend on the clock
    forbidden = forbidden_minute(now_ms)
    if forbidden and apply:
        raise Refused(f"refused at {now_ms}: {FORBIDDEN_NOTE}")
    books = root / A3_BOOKS_NAME
    if books_path is not None and (Path(books_path).name != A3_BOOKS_NAME or os.path.realpath(Path(books_path).parent) != os.fspath(root)):
        raise Refused(f"--books-file {os.fspath(books_path)!r} is not {books}: s1's A3 books file lives in its own root")
    read_books = root / A3_BOOKS_READ_NAME
    eps = check_endpoints(endpoints)
    registry = root / "bots.json"
    host = host or os.uname().nodename

    def go():
        bots = sc.load_registry(registry)
        extra = sorted(set(bots) - {S1_ID})
        if extra:
            raise Refused(f"{registry} holds {extra}: s1's own root holds only the s1 line (DEC-031 Amendment 22 item 9); this tool does not touch another book's registry")
        state = {books: _file_state(books, A3_BOOKS_TEXT), read_books: _file_state(read_books, A3_BOOKS_READ_TEXT)}  # refuses a different file before any write
        rep = {"arena_root": os.fspath(root), "a3_books": os.fspath(books), "a3_books_sha256": A3_BOOKS_SHA256, "a3_books_read": os.fspath(read_books),
               "a3_books_read_sha256": A3_BOOKS_READ_SHA256, "endpoints": eps}
        if S1_ID in bots:
            missing = sorted(os.fspath(p) for p, s in state.items() if s == "missing")
            if missing:
                raise Refused(f"the s1 line is registered but {missing} is missing: a manual state, not mine to repair")
            e = w.add_line(dict(bots), row, sockets=sockets, endpoints=eps, host=host, reg_ms=int(bots[S1_ID]["registered_at_ms"]))
            return {**rep, "status": "already_done", "registered_at_ms": e["registered_at_ms"], "window_end_ms": e["window_end_ms_pin"], "endpoints": list(e["endpoints"])}
        trial: dict[str, dict] = {}
        window_ok = int(row["register_not_before_ms"]) <= now_ms <= int(row["register_not_after_ms"])
        # window, clock and change checks, in memory only; a dry run outside the window still runs every other check and reports window_ok false
        entry = w.add_line(trial, row if (window_ok or apply) else {**row, "register_not_before_ms": 0, "register_not_after_ms": 2**62}, sockets=sockets, endpoints=eps,
                           host=host, reg_ms=now_ms)
        legacy, new = a3_halt.validate_books(json.loads(A3_BOOKS_TEXT), sorted(trial))  # unregistered / uncovered / duplicate / both-lists refusals
        if a3_halt.validate_books(json.loads(A3_BOOKS_READ_TEXT), sorted(trial)) != (legacy, new):
            raise Refused("the read books file and the A3 books file list different books")
        rep.update(status="applied" if apply else "dry_run", registered_at_ms=now_ms, window_end_ms=entry["window_end_ms_pin"], clock_start_ms=entry["clock_start_ms"],
                   frozen_sha=entry["frozen_sha"], legacy=legacy, new_rule=new, window_ok=window_ok, forbidden_minute=forbidden)
        if not apply:
            return rep
        for p, text in ((books, A3_BOOKS_TEXT), (read_books, A3_BOOKS_READ_TEXT)):
            if state[p] == "missing":
                _publish_once(p, text)
        for p, text in ((books, A3_BOOKS_TEXT), (read_books, A3_BOOKS_READ_TEXT)):
            if _sha(p.read_bytes()) != _sha(text.encode()):
                raise Refused(f"{p} does not match its pin after the write: STOP and tell the manager")
        sc._atomic_write_json(registry, {"bots": trial})  # the s1 line last: it is the commit point
        return rep

    return sc._with_registry_lock(registry, go)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="q50s1_register", description=__doc__.split("\n")[0])
    ap.add_argument("--arena-root", required=True)
    ap.add_argument("--books-file", default=None, help=f"optional; if given it must be <arena-root>/{A3_BOOKS_NAME} (the read books file is <arena-root>/{A3_BOOKS_READ_NAME})")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--now-ms", type=int, default=None, help="dry run / fixtures only; refused with --apply (a real run uses the wall clock)")
    ap.add_argument("--preclock-notebook-ref", required=True, help="the MiScusi notebook entry that lists the pre-clock items (format checked only)")
    ap.add_argument("--sockets", type=int, default=3)
    ap.add_argument("--endpoint", action="append", default=None, help=f"key-free; repeatable; default {list(ENDPOINTS)}")
    ap.add_argument("--host", default=None)
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if not NOTEBOOK_REF_RE.fullmatch(a.preclock_notebook_ref):
        print("REFUSED: --preclock-notebook-ref must be a notebook entry id (6 to 80 of A-Z a-z 0-9 _ -)", file=sys.stderr)
        return 2
    if a.apply and a.now_ms is not None:
        print("REFUSED: --now-ms is not accepted with --apply (the registration instant is the wall clock)", file=sys.stderr)
        return 2
    now = a.now_ms if a.now_ms is not None else int(time.time() * 1000)
    try:
        rep = dict(run(sc._arena_root(a.arena_root), a.books_file, now_ms=now, apply=a.apply, sockets=a.sockets, endpoints=a.endpoint or list(ENDPOINTS), host=a.host),
                   preclock_notebook_ref=a.preclock_notebook_ref)
    except (Refused, ValueError, a3_halt.Invalid) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(json.dumps(rep, sort_keys=True))
    for name, sha, text in ((A3_BOOKS_NAME, A3_BOOKS_SHA256, A3_BOOKS_TEXT), (A3_BOOKS_READ_NAME, A3_BOOKS_READ_SHA256, A3_BOOKS_READ_TEXT)):
        print(f"# {name} sha256 {sha}, verbatim (one line, trailing newline):")
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
