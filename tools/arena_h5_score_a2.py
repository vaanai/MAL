"""Run the pinned tools/arena_h5_score.py with the two a2 book ids added to BOTS.

The scorer module is imported unmodified (sha256 dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006, pinned by a test). Only the BOTS constant is extended, and
only for the duration of main(), so importing this module has no side effect on other users of the scorer.

Subcommand whitelist (QP-RULING-1022-a2pack (2) ii): only `score` (with or without --dry-run; flags with no subcommand mean `score`, as in the scorer). Everything else exits 2:
- `register` would derive wrong ids (register_books loops over BOTS). Register a2 with the UNMODIFIED module, generation="a2".
- `halt` matches the a2 prefixes and would end them at the a1 flag.
- `refit` is refused: job #1201's a1 command could not be found in the repo or ops notes (the 2026-10-19 note says refit runs by hand after read_reached), so it is not allowed.
"""
from __future__ import annotations

import contextlib
import sys
from typing import Iterator, Sequence

from tools import arena_h5_score as sc

A1_BOTS = {"h5v1-boostfloor-a1": "v1", "h5v2-boostclock-a1": "v2"}
A2_BOTS = {"h5v1-boostfloor-a2": "v1", "h5v2-boostclock-a2": "v2"}
ALLOWED = ("score",)


@contextlib.contextmanager
def a2_bots() -> Iterator[None]:
    """BOTS = the four ids inside the block; restored on exit."""
    before = dict(sc.BOTS)
    if before != A1_BOTS:
        raise RuntimeError(f"scorer BOTS is not the pinned a1 pair: {sorted(before)}")
    sc.BOTS.update(A2_BOTS)
    try:
        assert set(sc.BOTS) == set(A1_BOTS) | set(A2_BOTS) and len(sc.BOTS) == 4, sorted(sc.BOTS)
        yield
    finally:
        sc.BOTS.clear()
        sc.BOTS.update(before)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and not argv[0].startswith("-"):
        cmd = argv[0]
    elif argv and argv[0] in ("-h", "--help"):
        cmd = argv[0]
    else:
        cmd, argv = "score", ["score", *argv]  # the scorer's own default
    if cmd not in ALLOWED:
        print(f"REFUSED: arena_h5_score_a2 allows only {', '.join(ALLOWED)}; {cmd!r} is not allowed here", file=sys.stderr)
        return 2
    with a2_bots():
        return sc.main(argv)


if __name__ == "__main__":
    sys.exit(main())
