"""Registration and scoring wrapper for the Q50 successor `h5v1q50-boostfloor-s1` around the pinned scorer (DEC-031 Amendments 17 and 18; plan C2). PAPER ONLY.

`tools/arena_h5_score_q50.py` (Q50's wrapper, sha256 f5a479ad...) and the scorer `tools/arena_h5_score.py` (sha256 dfe7f018...) are imported and NOT edited; this module only
reuses Q50's pure helpers and replaces BOTS / score_book / summarize / write_ledger for the duration of main(), as Q50's wrapper does. Importing it has no side effect.

- The book's values are the compiled table row `tools.arena_h5_q50s_instant.S1` (frozen_sha S*, clock 2026-11-06T00Z, window_end_ms E 2026-12-01T00Z (DEC-031 power-n amendment
  item 8.1; was E_s1 2026-11-20T00Z under Amendment 18 item 3.2), rule and its sha256, Q* 50, exit v1, s0 margin 60 s). `register` has NO sha, clock, end or stake flag: it writes that row.
- `register_s1` writes the one line (refuses any change of sha, clock, end, rule or stake; never edits or removes another line). `window_end_ms` is set AT REGISTRATION (the H pin,
  Amendment 18 item 2.3) and an A3 end may only move it earlier. Registration is refused outside the row's registration window (Amendment 18 item 4) and at or after the clock.
- A shadow dir feeds the book only if EVERY start record carries code_sha S* (the scorer's identity check), q_star_sol == 50.0, the Q50 rule and its sha256, and
  h5_look2.end_only_ms == 1796083200000 (S*'s SUCC_END_MS). Anything else is refused by the scorer's own refusal path with the reason.
- pv triggers whose s0_t_recv_ms is missing, not an integer or below clock + 60,000 ms are dropped before score_book (counted, never attempts), with the a2 split counters.
- THE READ INSTANT: on every `score` run the power-n screen instant (200 counted trades, or 100 at B >= S_cap = clock + 10 d, on 5 dates, by E; tools.arena_h5_q50s_instant)
  is computed from the scorer's own BookResult (decision timestamps and class only) and, unless --dry-run, recorded ONCE in
  <ledger>/h5v1q50-boostfloor-s1/q50s1-read-instant.json. The scorer's read-instant.json is an attempt-boundary record only.
- Subcommand whitelist: `score` and `register`. `halt` (set_window_end never reaches this id), `refit`, `-h`, unknown and `--` exit 2. `score` needs an explicit --shadow-dir.
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
from typing import Iterator, Mapping, Sequence

from tools import arena_h5_q50s_instant as si
from tools import arena_h5_score as sc
from tools import arena_h5_score_q50 as q50w
from tools.arena_h5_score_a2 import DROP_KEYS, _bucket, _s0_ok

SCORER_SHA256 = q50w.SCORER_SHA256
S1_ID = si.S1_ID
S1_BOTS = {S1_ID: si.S1["exit_rule"]}
A1_BOTS = q50w.A1_BOTS
ALLOWED = ("score", "register")
SCORE_FLAGS = q50w.SCORE_FLAGS
REGISTER_FLAGS = ("--arena-root", "--sockets", "--endpoint", "--host")
CHANGE_KEYS = ("frozen_sha", "clock_start_ms", "window_end_ms_pin", "deciding_stake_sol", "rule", "rule_sha256", "q_star_sol", "exit_rule")

if sc.scorer_sha256() != SCORER_SHA256:
    raise RuntimeError("tools/arena_h5_score.py is not the pinned scorer")


def start_problem(start: Mapping, row: Mapping = si.S1) -> str | None:
    """Why this start record is not an s1 process's (None: it is). Q50's checks first, then S*'s end_only_ms."""
    q = start.get("q_star_sol")
    if isinstance(q, bool) or not isinstance(q, (int, float)) or q != row["q_star_sol"]:
        return "q_star_mismatch"
    if start.get("rule") != row["rule"]:
        return "rule_mismatch"
    if start.get("rule_sha256") != row["rule_sha256"]:
        return "rule_sha_mismatch"
    e = (start.get("h5_look2") or {}).get("end_only_ms")
    if isinstance(e, bool) or e != row["end_only_ms"]:
        return "end_only_mismatch"
    return None


def s1_problem(d: sc.ShadowDir, row: Mapping = si.S1) -> str | None:
    for s in d.starts:
        p = start_problem(s, row)
        if p:
            return p
    return None


def _guarded(d: sc.ShadowDir, row: Mapping) -> sc.ShadowDir:
    c = copy.copy(d)
    orig = d.identity_problem
    c.identity_problem = lambda entry: orig(entry) or s1_problem(d, row)  # type: ignore[method-assign]
    return c


def filtered_dirs(entry: dict, dirs: Sequence[sc.ShadowDir], row: Mapping = si.S1) -> tuple[list[sc.ShadowDir], collections.Counter]:
    """Dirs guarded by the s1 start-record check, with pv triggers kept only for pools whose s0 is at or after clock + 60 s (the margin of the row); dropped pools are counted by decision
    instant into the a2 split counters, over dirs that pass both identity checks, by distinct pool. A dropped pool is never an attempt."""
    clock, end = int(entry["clock_start_ms"]), entry.get("window_end_ms")
    out, counts, seen = [], collections.Counter(), set()
    for d in dirs:
        c = _guarded(d, row)
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


def s1_instant(bot: str, res: sc.BookResult, entry: dict, ledger_root: Path, now_ms: int, *, dry_run: bool) -> dict:
    """The power-n screen instant on this run's BookResult (counts and instants only), recorded write-once unless dry_run. The end is E always, as Q50's is the end-seal always: an
    A3 end moves the window (the scorer skips later decisions) and is judged by Amendment 4 / 14, not by this rule."""
    from tools import arena_h5_q50_instant as qi
    end = si.END_MS
    cls = qi.classes_from_result(res)
    clock = int(entry["clock_start_ms"])
    r = si.instant_rule(cls["counted"], cls["open"], start_ms=clock, end_ms=end, now_ms=now_ms)
    out = {"rule": si.RULE, "status": r["status"], "instant_ms": r["instant_ms"], "boundary_ms": r["boundary_ms"], "file": si.INSTANT_FILE}
    n_ref = len(cls["refused"])
    doc = si.record_doc(bot, cls, clock_start_ms=clock, end_ms=end, now_ms=now_ms, attempt_definition=sc.attempt_disclosure(n_ref, len(res.decisions) + n_ref))
    if doc is None:
        out["record"] = "not decided"
    elif dry_run:
        out["record"] = "dry run: not written"
    else:
        out["record"] = si.record(Path(ledger_root) / bot, doc)
        if out["record"] == "conflict":
            res.counts["q50s1_read_instant_conflict"] += 1
    return out


@contextlib.contextmanager
def s1_bots(row: Mapping = si.S1) -> Iterator[None]:
    """BOTS = exactly the s1 id inside the block; score_book / summarize / write_ledger wrapped; all restored on exit."""
    before = dict(sc.BOTS)
    if before != A1_BOTS:
        raise RuntimeError(f"scorer BOTS is not the pinned a1 pair: {sorted(before)}")
    orig_score, orig_summ, orig_write = sc.score_book, sc.summarize, sc.write_ledger
    seen: dict[str, dict] = {}

    def score_book(bot, entry, dirs):
        if bot != S1_ID:
            raise RuntimeError(f"the s1 wrapper scores only {S1_ID}, not {bot!r}")
        fdirs, dropped = filtered_dirs(entry, dirs, row)
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
            raise RuntimeError(f"{bot}: summarize before score_book and write_ledger: the s1 read instant cannot be recorded")
        out["q50s1_read_instant"] = s1_instant(bot, res, st["entry"], st["ledger_root"], int(now_ms), dry_run=st["dry_run"])
        return out

    try:
        sc.BOTS.clear()
        sc.BOTS.update(S1_BOTS)
        if sc.BOTS != S1_BOTS or len(sc.BOTS) != 1:
            raise RuntimeError(f"BOTS is not exactly the s1 id: {sorted(sc.BOTS)}")
        sc.score_book, sc.summarize, sc.write_ledger = score_book, summarize, write_ledger
        yield
    finally:
        sc.score_book, sc.summarize, sc.write_ledger = orig_score, orig_summ, orig_write
        sc.BOTS.clear()
        sc.BOTS.update(before)


def s1_line(row: Mapping, *, sockets: int, endpoints: Sequence[str], host: str, reg_ms: int) -> dict:
    return {"frozen_sha": row["frozen_sha"], "clock_start_ms": int(row["clock_start_ms"]), "registered_at_ms": reg_ms, "deciding_stake_sol": float(sc.DECIDING_STAKE),
            "deciding_cell": "1p9", "stack": sc.STACK, "rule": row["rule"], "rule_sha256": row["rule_sha256"], "q_star_sol": row["q_star_sol"], "exit_rule": row["exit_rule"],
            "sockets": int(sockets), "endpoints": list(endpoints), "host": host, "window_end_ms": int(row["window_end_ms"]), "window_end_ms_pin": int(row["window_end_ms"]),
            "stopped_ms": None, "scorer": "tools/arena_h5_score.py", "cost_module": "tools/arena_h5_cost.py"}


def add_line(bots: dict, row: Mapping, *, sockets: int, endpoints: Sequence[str], host: str, reg_ms: int) -> dict:
    """Pure: add the s1 line to a loaded registry dict (in place) and return it, or return the existing line if it is the same book. Raises ValueError on any doubt. The caller holds
    the registry lock and writes the file."""
    clock = int(row["clock_start_ms"])
    if not (int(row["register_not_before_ms"]) <= reg_ms <= int(row["register_not_after_ms"])):
        raise ValueError(f"registration at {reg_ms} is outside the registration window [{row['register_not_before_ms']}, {row['register_not_after_ms']}] (DEC-031 Amendment 18 item 4)")
    if reg_ms >= clock:
        raise ValueError("registration is at or after the clock")
    entry = s1_line(row, sockets=sockets, endpoints=endpoints, host=host, reg_ms=reg_ms)
    if S1_ID in bots:
        old = bots[S1_ID]
        if any(old.get(k) != entry[k] for k in CHANGE_KEYS):
            raise ValueError(f"{S1_ID} is registered with a different sha, clock, end, stake or rule: a change is a new bot_id")
        return old
    bots[S1_ID] = entry
    return entry


def register_s1(path: Path, *, sockets: int, endpoints: Sequence[str], host: str, now_ms: int | None = None, row: Mapping = si.S1) -> dict[str, dict]:
    """Write the s1 line only, under the registry lock and atomically. Values come from the compiled row; there is no way to pass a sha, clock or end."""
    reg_ms = int(now_ms if now_ms is not None else time.time() * 1000)

    def go():
        bots = sc.load_registry(path)
        had = S1_ID in bots
        e = add_line(bots, row, sockets=sockets, endpoints=endpoints, host=host, reg_ms=reg_ms)
        if not had:
            sc._atomic_write_json(path, {"bots": bots})
        return {S1_ID: e}

    return sc._with_registry_lock(path, go)


def cmd_register(argv: Sequence[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="arena_h5_score_q50s register")
    ap.add_argument("--arena-root", required=True)
    ap.add_argument("--sockets", type=int, required=True)
    ap.add_argument("--endpoint", action="append", default=None)
    ap.add_argument("--host", default=None)
    a = ap.parse_args(list(argv))
    try:
        root = sc._arena_root(a.arena_root)
        out = register_s1(root / "bots.json", sockets=a.sockets, endpoints=a.endpoint or ["default"], host=a.host or os.uname().nodename)
    except ValueError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"registered": sorted(out), "registry": str(root / "bots.json")}, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        print("REFUSED: arena_h5_score_q50s does not accept '--'", file=sys.stderr)
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
        print(f"REFUSED: arena_h5_score_q50s allows only {', '.join(ALLOWED)}; {cmd!r} is not allowed here", file=sys.stderr)
        return 2
    if cmd == "register":
        return cmd_register(argv[1:])
    if not any(a == "--shadow-dir" or a.startswith("--shadow-dir=") for a in argv[1:]):
        print("REFUSED: arena_h5_score_q50s score needs an explicit --shadow-dir (the default h5-shadow-* glob does not match h5-q50s1-* dirs)", file=sys.stderr)
        return 2
    with s1_bots():
        return sc.main(argv)


if __name__ == "__main__":
    sys.exit(main())
