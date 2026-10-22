"""Run the pinned tools/arena_h5_score.py for the H5-BOOSTFLOOR-Q50 book `h5v1q50-boostfloor-a1` only (QP-RULING-1022-q50 section 3; DEC-031 Amendment 5).

The scorer module is imported unmodified (sha256 dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006, pinned by a test and asserted at import). Only BOTS and the
score_book / summarize seams are replaced, and only for the duration of main(); importing this module has no side effect on other users of the scorer.

- BOTS is exactly {Q50_ID: "v1"} inside the block (the 330 s exit; bx10 is never read for this book) and is restored on exit.
- Subcommand whitelist: `score` (with or without --dry-run; bare score flags mean `score`) and `register`. `halt` (the a1/a2 prefixes only; Q50 is ended by the A3 rule through
  the books file, never by set_window_end), `refit`, `-h`, unknown and `--` exit 2.
- `register` writes ONLY the Q50 line (register_books hard-codes rule "H5-BOOSTFLOOR v1" and derives ids from BOTS). It reuses the scorer's registry lock and atomic write and
  never edits or removes any other line.
- `score` refuses to run without an explicit --shadow-dir (the scorer's default glob h5-shadow-* misses the h5-q50-* out dirs).
- A shadow dir feeds the book only if EVERY start record carries q_star_sol == 50.0, rule "H5-BOOSTFLOOR-Q50 v1" and the Q50 rule sha256 (otherwise a Q* = 40 run at the same
  sha could feed the Q50 id). Such a dir is refused by the scorer's own refusal path (refused_dirs, with the reason).
- pv triggers whose s0_t_recv_ms is missing, not an integer, or below clock_start_ms + 60,000 ms are dropped before score_book (counted, never attempts), with the a2 split counters.
"""
from __future__ import annotations

import collections
import contextlib
import copy
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterator, Sequence

from tools import arena_h5_score as sc
from tools.arena_h5_score_a2 import DROP_KEYS, S0_MARGIN_MS, _bucket, _s0_ok

SCORER_SHA256 = "dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006"
Q50_ID = "h5v1q50-boostfloor-a1"
Q50_BOTS = {Q50_ID: "v1"}
Q50_RULE = "H5-BOOSTFLOOR-Q50 v1"
Q50_RULE_SHA256 = "b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4"  # EXP/EXP-024-RULE-Q50.md
Q_STAR = 50.0
A1_BOTS = {"h5v1-boostfloor-a1": "v1", "h5v2-boostclock-a1": "v2"}
ALLOWED = ("score", "register")
SCORE_FLAGS = ("--arena-root", "--shadow-dir", "--ledger-root", "--dry-run", "--now-ms")
REGISTER_FLAGS = ("--arena-root", "--frozen-sha", "--sockets", "--endpoint", "--host", "--clock-start-ms")

if sc.scorer_sha256() != SCORER_SHA256:
    raise RuntimeError("tools/arena_h5_score.py is not the pinned scorer")


def start_problem(start: dict) -> str | None:
    """Why this start record is not a Q50 process's (None: it is)."""
    q = start.get("q_star_sol")
    if isinstance(q, bool) or not isinstance(q, (int, float)) or q != Q_STAR:
        return "q_star_mismatch"
    if start.get("rule") != Q50_RULE:
        return "rule_mismatch"
    if start.get("rule_sha256") != Q50_RULE_SHA256:
        return "rule_sha_mismatch"
    return None


def q50_problem(d: sc.ShadowDir) -> str | None:
    for s in d.starts:
        p = start_problem(s)
        if p:
            return p
    return None


def _guarded(d: sc.ShadowDir) -> sc.ShadowDir:
    """A shallow copy whose identity check is the scorer's own and then the Q50 start-record check (every start record in the dir)."""
    c = copy.copy(d)
    orig = d.identity_problem
    c.identity_problem = lambda entry: orig(entry) or q50_problem(d)  # type: ignore[method-assign]
    return c


def filtered_dirs(entry: dict, dirs: Sequence[sc.ShadowDir]) -> tuple[list[sc.ShadowDir], collections.Counter]:
    """Dirs guarded by the Q50 start-record check, with pv triggers kept only for pools whose s0 is at or after clock + 60 s. A dropped pool is never an attempt: its outcome and
    strip are never looked up, because score_book reaches them only through the trigger. The counts cover only dirs that pass both identity checks, by distinct pool, split by decision
    instant into dropped_s0_before_clock, dropped_decided_before_clock and dropped_after_window (as the a2 wrapper)."""
    clock, end = int(entry["clock_start_ms"]), entry.get("window_end_ms")
    out, counts, seen = [], collections.Counter(), set()
    for d in dirs:
        c = _guarded(d)
        c.triggers = {p: t for p, t in d.triggers.items() if _s0_ok(t, clock)}
        if c.identity_problem(entry) is None:
            for pool, t in d.triggers.items():
                if pool in c.triggers or pool in seen:
                    continue
                seen.add(pool)
                k = _bucket(t, clock, end)
                if k:
                    counts[k] += 1
        out.append(c)
    return out, counts


@contextlib.contextmanager
def q50_bots() -> Iterator[None]:
    """BOTS = exactly the Q50 id inside the block; score_book / summarize wrapped; all restored on exit."""
    before = dict(sc.BOTS)
    if before != A1_BOTS:
        raise RuntimeError(f"scorer BOTS is not the pinned a1 pair: {sorted(before)}")
    orig_score, orig_summ = sc.score_book, sc.summarize

    def score_book(bot, entry, dirs):
        if bot != Q50_ID:
            raise RuntimeError(f"the Q50 wrapper scores only {Q50_ID}, not {bot!r}")
        fdirs, dropped = filtered_dirs(entry, dirs)
        res = orig_score(bot, entry, fdirs)
        for k in DROP_KEYS:
            res.counts[k] += dropped[k]
        return res

    def summarize(bot, res, now_ms):
        out = orig_summ(bot, res, now_ms)
        for k in DROP_KEYS:
            out[k] = res.counts[k]
        return out

    try:
        sc.BOTS.clear()
        sc.BOTS.update(Q50_BOTS)
        if sc.BOTS != Q50_BOTS or len(sc.BOTS) != 1:
            raise RuntimeError(f"BOTS is not exactly the Q50 id: {sorted(sc.BOTS)}")
        sc.score_book, sc.summarize = score_book, summarize
        yield
    finally:
        sc.score_book, sc.summarize = orig_score, orig_summ
        sc.BOTS.clear()
        sc.BOTS.update(before)


def register_q50(path: Path, *, frozen_sha: str, sockets: int, endpoints: Sequence[str], host: str, clock_start_ms: int | None = None, now_ms: int | None = None) -> dict[str, dict]:
    """Write the Q50 book's line only. Before the clock; never edited after (the same id with a different sha, clock or stake is refused; no other line is read for change, written
    differently or removed). The shadow process must START after registered_at_ms (the scorer enforces it)."""
    if not frozen_sha or len(frozen_sha) != 40 or any(c not in "0123456789abcdef" for c in frozen_sha):
        raise ValueError("frozen_sha must be a 40-hex git sha")
    reg_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    clock = int(clock_start_ms) if clock_start_ms is not None else reg_ms
    entry = {"frozen_sha": frozen_sha, "clock_start_ms": clock, "registered_at_ms": reg_ms, "deciding_stake_sol": float(sc.DECIDING_STAKE), "deciding_cell": "1p9",
             "stack": sc.STACK, "rule": Q50_RULE, "rule_sha256": Q50_RULE_SHA256, "q_star_sol": Q_STAR, "exit_rule": "v1", "sockets": int(sockets), "endpoints": list(endpoints),
             "host": host, "window_end_ms": None, "stopped_ms": None, "scorer": "tools/arena_h5_score.py", "cost_module": "tools/arena_h5_cost.py"}

    def go():
        bots = sc.load_registry(path)
        if Q50_ID in bots:
            old = bots[Q50_ID]
            if any(old.get(k) != entry[k] for k in ("frozen_sha", "clock_start_ms", "deciding_stake_sol", "rule", "rule_sha256", "q_star_sol")):
                raise ValueError(f"{Q50_ID} is registered with a different sha, clock, stake or rule: a change is a new bot_id")
            return {Q50_ID: old}
        if clock < reg_ms:
            raise ValueError("clock_start_ms is before registered_at_ms")
        bots[Q50_ID] = entry
        sc._atomic_write_json(path, {"bots": bots})
        return {Q50_ID: entry}

    return sc._with_registry_lock(path, go)


def cmd_register(argv: Sequence[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="arena_h5_score_q50 register")
    ap.add_argument("--arena-root", required=True)
    ap.add_argument("--frozen-sha", required=True)
    ap.add_argument("--sockets", type=int, required=True)
    ap.add_argument("--endpoint", action="append", default=None)
    ap.add_argument("--host", default=None)
    ap.add_argument("--clock-start-ms", type=int, default=None)
    a = ap.parse_args(list(argv))
    try:
        root = sc._arena_root(a.arena_root)
        out = register_q50(root / "bots.json", frozen_sha=a.frozen_sha, sockets=a.sockets, endpoints=a.endpoint or ["default"], host=a.host or os.uname().nodename,
                           clock_start_ms=a.clock_start_ms)
    except ValueError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"registered": sorted(out), "registry": str(root / "bots.json")}, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        print("REFUSED: arena_h5_score_q50 does not accept '--'", file=sys.stderr)
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
        print(f"REFUSED: arena_h5_score_q50 allows only {', '.join(ALLOWED)}; {cmd!r} is not allowed here", file=sys.stderr)
        return 2
    if cmd == "register":
        return cmd_register(argv[1:])
    if not any(a == "--shadow-dir" or a.startswith("--shadow-dir=") for a in argv[1:]):
        # the scorer's default glob h5-shadow-* misses the h5-q50-* out dirs, so the run would silently score nothing
        print("REFUSED: arena_h5_score_q50 score needs an explicit --shadow-dir (the default h5-shadow-* glob does not match h5-q50-* dirs)", file=sys.stderr)
        return 2
    with q50_bots():
        return sc.main(argv)


if __name__ == "__main__":
    sys.exit(main())
