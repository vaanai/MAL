"""One-command registration of `h5v1q50-boostfloor-s1` and the books v2 -> v3 swap (plan C5; DEC-031 Amendment 18 items 4 and 6.1). PAPER ONLY. No outcome is read.

    python -m tools.q50s1_register --arena-root ARENA --books-file ARENA/a3-books.json [--apply] [--now-ms MS]

Default is a DRY RUN: every check runs and nothing is written. With --apply, under the registry lock: (1) refuse inside :04 to :25 of the 07, 13 and 19 UTC hours and inside
12:10 to 12:25Z (the A3 loop and dead-man windows); (2) the books file must be the registered v2 (sha256 819c699d...), or already v3 with the line registered (idempotent no-op);
(3) the candidate v3 text (v2 + s1) must pass tools.a3_halt.validate_books against the registry WITH s1 added (every listed id registered; every registered h5 book listed; no
duplicates); (4) the s1 line is written from the compiled row (tools.arena_h5_score_q50s.register path: window [11-04T00Z, 11-05T12Z], before the clock, window_end_ms pinned);
(5) v2's byte copy is kept as <books>.v2.copy (never overwritten; sha256 checked), and the books file is replaced atomically by v3 and its sha256 checked against the pin.
It prints both sha256 values, the registered_at_ms and the window end, for the notebook copy. It touches no other file and starts no process.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Sequence

from tools import a3_halt
from tools import arena_h5_books as B
from tools import arena_h5_q50s_instant as si
from tools import arena_h5_score as sc
from tools import arena_h5_score_q50s as w
from tools.arena_score import Refused

FORBIDDEN_NOTE = "inside :04 to :25 of the 07, 13 or 19 UTC hour, or 12:10 to 12:25Z (A3 loop / dead-man windows)"


def forbidden_minute(now_ms: int) -> bool:
    t = dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc)
    if t.hour in (7, 13, 19) and 4 <= t.minute <= 25:
        return True
    return t.hour == 12 and 10 <= t.minute <= 25


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def run(arena_root: Path, books_path: Path, *, now_ms: int, apply: bool, row=si.S1, sockets: int = 3, endpoints: Sequence[str] = ("default",), host: str | None = None) -> dict:
    """Returns the report dict. Raises Refused / ValueError / a3_halt.Invalid on any doubt before anything is written."""
    forbidden = forbidden_minute(now_ms)
    if forbidden and apply:
        raise Refused(f"refused at {now_ms}: {FORBIDDEN_NOTE}")
    registry = Path(arena_root) / "bots.json"
    books_path = Path(books_path)
    host = host or os.uname().nodename

    def go():
        bots = sc.load_registry(registry)
        raw = books_path.read_bytes()
        got = _sha(raw)
        done = w.S1_ID in bots
        if got == B.BOOKS_V3_SHA256:
            if not done:
                raise Refused("the books file is v3 but the s1 line is not registered: a manual state, not mine to repair")
            e = w.add_line(dict(bots), row, sockets=sockets, endpoints=endpoints, host=host, reg_ms=int(bots[w.S1_ID]["registered_at_ms"]))
            return {"status": "already_done", "books_sha256": got, "registered_at_ms": e["registered_at_ms"], "window_end_ms": e["window_end_ms_pin"]}
        if got != B.BOOKS_V2_SHA256:
            raise Refused(f"the books file has sha256 {got}: neither the registered v2 ({B.BOOKS_V2_SHA256}) nor v3")
        if done:
            raise Refused("the s1 line is registered but the books file is still v2: a manual state, not mine to repair")
        v3 = B.books_v3_text(raw)
        if _sha(v3.encode()) != B.BOOKS_V3_SHA256:
            raise Refused("books v3 text does not match its pin")
        trial = dict(bots)
        window_ok = int(row["register_not_before_ms"]) <= now_ms <= int(row["register_not_after_ms"])
        # window, clock and change checks, in memory only; a dry run outside the window still runs every other check and reports window_ok false
        entry = w.add_line(trial, row if (window_ok or apply) else {**row, "register_not_before_ms": 0, "register_not_after_ms": 2**62}, sockets=sockets, endpoints=endpoints,
                           host=host, reg_ms=now_ms)
        legacy, new = a3_halt.validate_books(json.loads(v3), sorted(trial))  # unregistered / uncovered / duplicate / both-lists refusals
        rep = {"status": "applied" if apply else "dry_run", "v2_sha256": got, "books_sha256": B.BOOKS_V3_SHA256, "registered_at_ms": now_ms,
               "window_end_ms": entry["window_end_ms_pin"], "clock_start_ms": entry["clock_start_ms"], "frozen_sha": entry["frozen_sha"], "legacy": legacy, "new_rule": new,
               "window_ok": window_ok, "forbidden_minute": forbidden}
        if not apply:
            return rep
        copy = Path(str(books_path) + ".v2.copy")
        if copy.exists():
            if _sha(copy.read_bytes()) != B.BOOKS_V2_SHA256:
                raise Refused(f"{copy} exists and is not the registered v2")
        else:
            fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
        tmp = Path(str(books_path) + ".v3.tmp")
        tmp.write_bytes(v3.encode())
        if _sha(tmp.read_bytes()) != B.BOOKS_V3_SHA256:
            tmp.unlink()
            raise Refused("the v3 temp file does not match its pin")
        sc._atomic_write_json(registry, {"bots": trial})  # the s1 line ...
        os.replace(tmp, books_path)  # ... and the books swap, both inside the registry lock
        if _sha(books_path.read_bytes()) != B.BOOKS_V3_SHA256:
            raise Refused("books file sha256 after the swap is not the v3 pin: STOP and tell the manager")
        rep["v2_copy"] = str(copy)
        return rep

    return sc._with_registry_lock(registry, go)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="q50s1_register", description=__doc__.split("\n")[0])
    ap.add_argument("--arena-root", required=True)
    ap.add_argument("--books-file", required=True)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--now-ms", type=int, default=None, help="fixtures only; a real run uses the wall clock")
    ap.add_argument("--sockets", type=int, default=3)
    ap.add_argument("--endpoint", action="append", default=None)
    ap.add_argument("--host", default=None)
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    now = a.now_ms if a.now_ms is not None else int(time.time() * 1000)
    try:
        rep = run(sc._arena_root(a.arena_root), Path(a.books_file), now_ms=now, apply=a.apply, sockets=a.sockets, endpoints=a.endpoint or ["default"], host=a.host)
    except (Refused, ValueError, a3_halt.Invalid) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(json.dumps(rep, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
