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
import copy
import sys
from typing import Iterator, Sequence

from tools import arena_h5_score as sc

A1_BOTS = {"h5v1-boostfloor-a1": "v1", "h5v2-boostclock-a1": "v2"}
A2_BOTS = {"h5v1-boostfloor-a2": "v1", "h5v2-boostclock-a2": "v2"}
ALLOWED = ("score",)
SCORE_FLAGS = ("--arena-root", "--shadow-dir", "--ledger-root", "--dry-run", "--now-ms")
V2_A2 = "h5v2-boostclock-a2"
S0_MARGIN_MS = 60_000
DROPPED = "dropped_s0_before_clock"


def _s0_ok(trig: dict, clock: int) -> bool:
    """s0_t_recv_ms present, an integer, and at or after clock + 60 s (fail-closed: missing or odd types are dropped)."""
    v = trig.get("s0_t_recv_ms")
    return isinstance(v, int) and not isinstance(v, bool) and v >= clock + S0_MARGIN_MS


def filtered_dirs(entry: dict, dirs: Sequence[sc.ShadowDir]) -> tuple[list[sc.ShadowDir], int]:
    """Shallow copies of the dirs whose pv triggers keep only the pools with s0 at or after clock + 60 s. A dropped pool is never an attempt: its outcome, bx10 and strip are
    never looked up because score_book reaches them only through the trigger."""
    clock, out, dropped = int(entry["clock_start_ms"]), [], 0
    for d in dirs:
        c = copy.copy(d)
        c.triggers = {p: t for p, t in d.triggers.items() if _s0_ok(t, clock)}
        dropped += len(d.triggers) - len(c.triggers)
        out.append(c)
    return out, dropped


@contextlib.contextmanager
def a2_bots() -> Iterator[None]:
    """BOTS = the four ids inside the block; restored on exit."""
    before = dict(sc.BOTS)
    if before != A1_BOTS:
        raise RuntimeError(f"scorer BOTS is not the pinned a1 pair: {sorted(before)}")
    sc.BOTS.update(A2_BOTS)
    orig_score, orig_summ = sc.score_book, sc.summarize

    def score_book(bot, entry, dirs):
        if bot != V2_A2:
            return orig_score(bot, entry, dirs)
        fdirs, dropped = filtered_dirs(entry, dirs)
        res = orig_score(bot, entry, fdirs)
        res.counts[DROPPED] += dropped
        return res

    def summarize(bot, res, now_ms):
        out = orig_summ(bot, res, now_ms)
        if bot == V2_A2:
            out[DROPPED] = res.counts[DROPPED]
        return out

    try:
        if set(sc.BOTS) != set(A1_BOTS) | set(A2_BOTS) or len(sc.BOTS) != 4:
            raise RuntimeError(f"BOTS is not exactly the four ids: {sorted(sc.BOTS)}")
        sc.score_book, sc.summarize = score_book, summarize
        yield
    finally:
        sc.score_book, sc.summarize = orig_score, orig_summ
        sc.BOTS.clear()
        sc.BOTS.update(before)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        print("REFUSED: arena_h5_score_a2 does not accept '--'", file=sys.stderr)
        return 2
    if argv and not argv[0].startswith("-"):
        cmd = argv[0]
    elif argv and argv[0].split("=", 1)[0] in SCORE_FLAGS:
        cmd, argv = "score", ["score", *argv]  # the scorer's own default: bare score flags mean `score`
    elif not argv:
        cmd, argv = "score", ["score"]
    else:
        cmd = argv[0]  # -h, --help, any other flag: refused below
    if cmd not in ALLOWED:
        print(f"REFUSED: arena_h5_score_a2 allows only {', '.join(ALLOWED)}; {cmd!r} is not allowed here", file=sys.stderr)
        return 2
    with a2_bots():
        return sc.main(argv)


if __name__ == "__main__":
    sys.exit(main())
