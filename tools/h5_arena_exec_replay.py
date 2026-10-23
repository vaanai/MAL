"""Decision-equivalence replay for tools/h5_arena_executor.py (QP-SCOPE-1022-h5exec items 11-12). Counts and md5 only.

INPUT is a shadow output dir of a REPLAY of the pinned E0-H5 exploration day 2026-09-20 (fast-pool-0918, EXP-024 Am.5 C hours), run at the book's frozen
sha. This tool never takes a live arena dir: it refuses any dir whose name or path contains an October date or "arena" (the Look-2 seal covers October pools),
and it prints no price, fill, exit or outcome (the bx10 check reads the shadow's own record of a SLOT, never a price).

For one book it computes, over the pools of that dir:
  (a) the scorer's decision layer: tools/arena_h5_score.py primitives (ShadowDir first-trigger-per-pool, executor_refusal = the pinned parser, decision_ms), the same
      filter order as score_book (before clock/start, after window end, look2-sealed, duplicate pool), with the a2 wrapper's s0 margin first for v2 and Q50;
  (b) the new module in replay: ArenaExecutor.decide_row over the same records, in file order, with a stub RPC (any call raises).
The md5 is over the sorted rows (mint, pool, trigger_slot, verdict), verdict in {attempt, <refusal reason>}. Window drops are counted separately and are outside the md5.
For the v2 book a second md5 is over (mint, leg, exit_trigger_slot): the executor's v2 rule (v2_cad_exit + v2_exit_trigger_slot, run on the slices the shadow wrote into
its own outcome_bx10 record) against the shadow's recorded exit_trigger_slot. Path-only refusals (max_open, caps, balance, age, sps_mismatch, halts) are not decided here.

Replay start records do not carry the arena identity (fresh_forward ARENA); the replay run marks the feed verified by hand and COUNTS the start records. The identity
check itself is covered by the unit tests, not by this replay. Any later change to the module needs this rerun with equal md5 (CLAUDE.md runner rule).
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from tools import arena_h5_score as sc
from tools import h5_arena_executor as ax
from tools import h5_executor as h

OCTOBER_RE = re.compile(r"2026-10|202610|arena", re.I)
EXPLORATION_DAY_RE = re.compile(r"2026-09-20|20260920")


class StubRpc:
    def __call__(self, method: str, params: list) -> Any:
        raise RuntimeError(f"stub rpc: {method} must not be called in a decision replay")


def md5_of(rows: list[tuple]) -> str:
    return hashlib.md5("\n".join(json.dumps(list(r), sort_keys=True) for r in sorted(rows)).encode()).hexdigest()


def replay_book(book: ax.Book) -> ax.Book:
    """The book with the window opened to the replay day: the clock and seal end are the only fields changed, so the filter order is the real one."""
    return dataclasses.replace(book, clock_start_ms=0, end_ms_seal=2**62)


def load_frozen_scorer(root: Path) -> Any:
    """tools/arena_h5_score.py (and the tools it imports) loaded from a SEPARATE export at `root` (the book's registration sha), so the reference does not move with
    the PR head (QP 10-23 ruling 4 (c)1). sys.modules is restored afterwards: the executor side keeps using this checkout's modules."""
    import importlib
    saved = {k: v for k, v in sys.modules.items() if k == "tools" or k.startswith("tools.")}
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, str(root))
    try:
        mod = importlib.import_module("tools.arena_h5_score")
        if not str(Path(mod.__file__).resolve()).startswith(str(Path(root).resolve())):
            raise SystemExit("refused: the frozen scorer was not loaded from the export")
        return mod
    finally:
        sys.path.remove(str(root))
        for k in [k for k in sys.modules if k == "tools" or k.startswith("tools.")]:
            del sys.modules[k]
        sys.modules.update(saved)


def check_start_records(shadow_dir: Path, expect_sha: str) -> int:
    """Every start record of the replay input must carry code_sha == the frozen shadow sha and not be dirty (QP 10-23 ruling 4 (c)2). Returns the count; refuses on any miss."""
    n = 0
    for f in sorted(shadow_dir.glob("h5-shadow-????-??-??T??.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if '"type": "start"' not in line and '"type":"start"' not in line:
                continue
            r = json.loads(line)
            if r.get("type") != "start":
                continue
            n += 1
            if r.get("code_sha") != expect_sha or r.get("dirty") not in (False, None, 0):
                raise SystemExit(f"refused: start record code_sha {r.get('code_sha')!r} dirty {r.get('dirty')!r} is not the frozen {expect_sha} with dirty=0")
    if n == 0:
        raise SystemExit("refused: no start record in the replay input")
    return n


def scorer_verdicts(shadow_dir: Path, book: ax.Book, sc: Any = sc) -> tuple[dict[tuple, str], collections.Counter]:
    counts: collections.Counter = collections.Counter()
    d = sc.ShadowDir(shadow_dir, counts)
    out: dict[tuple, str] = {}
    seen: set[str] = set()
    for pool, trig in d.triggers.items():  # first pv trigger per pool, file order
        start = trig["_start"]
        dec = sc.decision_ms(trig)
        s0 = trig.get("s0_t_recv_ms")
        if book.s0_margin_ms and (isinstance(s0, bool) or not isinstance(s0, int) or s0 < book.clock_start_ms + book.s0_margin_ms):
            counts["window_s0"] += 1
            continue
        if dec < book.clock_start_ms or (start is not None and start.get("start_ms") is not None and dec < int(start["start_ms"])):
            counts["window_before"] += 1
            continue
        if dec >= book.end_ms_seal:
            counts["window_after"] += 1
            continue
        if trig.get("h5_look2_sealed"):
            counts["window_look2"] += 1
            continue
        if pool in seen:
            continue
        seen.add(pool)
        why = sc.executor_refusal(trig)
        out[(trig.get("mint"), pool, trig.get("slot"))] = "attempt" if why is None else why
    return out, counts


def executor_verdicts(shadow_dir: Path, book: ax.Book) -> tuple[dict[tuple, str], collections.Counter]:
    book = replay_book(book)
    out: dict[tuple, str] = {}
    counts: collections.Counter = collections.Counter()
    with tempfile.TemporaryDirectory() as td:
        cfg = {"book_id": book.bot_id, "replay_only": True, "state_dir": td + "/state", "intents_file": td + "/in.jsonl"}
        ex = ax.ArenaExecutor(StubRpc(), cfg, None, now_ms=lambda: 0, root=Path(td))
        ex.abook = book
        ex.feed_verified, ex.slices_taken = True, 1
        excluded: set[str] = set()
        no_sps: set[str] = set()
        for f in sorted(shadow_dir.glob("h5-shadow-????-??-??T??.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line, parse_constant=sc._no_constant)
                except ValueError:
                    continue
                t = row.get("type")
                if t == "start":
                    counts["start_records"] += 1
                    ex._start_ms = int(row["start_ms"]) if isinstance(row.get("start_ms"), int) else 0
                    continue
                if t == "excluded":
                    excluded.update(x for x in (row.get("pool"), row.get("mint")) if isinstance(x, str))
                    continue
                if t == "skipped_no_sps" and isinstance(row.get("mint"), str):
                    no_sps.add(row["mint"])
                    continue
                keep, verdict = ex.decide_row(row)
                if t != "trigger" or row.get("variant") != "pv" or verdict is None:
                    continue
                if keep == "drop":
                    counts["window_" + {ax.REFUSAL_LOOK2: "look2", ax.REFUSAL_S0_WINDOW: "s0", ax.REFUSAL_DECISION_WINDOW: "before"}.get(verdict, verdict)] += 1
                    continue
                if keep == "keep_dup":
                    continue
                if verdict == "attempt" and (row.get("pool") in excluded or row.get("mint") in excluded):
                    verdict = h.EXCLUDED_POOL
                elif verdict == "attempt" and row.get("mint") in no_sps:
                    verdict = "sps_skipped_pool"
                out[(row.get("mint"), row.get("pool"), row.get("slot"))] = verdict
    return out, counts


def bx10_exit_slots(shadow_dir: Path) -> tuple[list[tuple], list[tuple]]:
    """(executor's, shadow's) (mint, leg, exit_trigger_slot) rows for every outcome_bx10 leg that fired or not. The executor's come from its own v2 functions run
    on the slices recorded in the same outcome_bx10 record; the shadow's are the recorded exit_trigger_slot."""
    mine, theirs = [], []
    for f in sorted(shadow_dir.glob("h5-shadow-????-??-??T??.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if '"outcome_bx10"' not in line:
                continue
            r = json.loads(line)
            if r.get("type") != "outcome_bx10":
                continue
            s0, sps = r["s0"], r["sps"]
            t = [off * sps for off, _x, _rm in r["slices"]]
            a = [float(x) for _o, x, _rm in r["slices"]]
            for leg, ent in sorted(r["legs"].items()):
                x = ent["landing_slot"]
                tau, fired, _k, _p = h.v2_cad_exit(t, a, (x - s0) * sps)
                mine.append((r["mint"], leg, h.v2_exit_trigger_slot(s0, sps, x, tau, fired)))
                theirs.append((r["mint"], leg, ent["exit_trigger_slot"]))
    return mine, theirs


def run(shadow_dir: Path, bot_id: str, scorer_root: Path | None = None, expect_code_sha: str | None = None) -> dict[str, Any]:
    if OCTOBER_RE.search(str(shadow_dir)) or not EXPLORATION_DAY_RE.search(str(shadow_dir)):
        raise SystemExit("refused: the replay dir must be the 2026-09-20 exploration day and must not be an October or arena path")
    book = replay_book(ax.BOOKS[bot_id])
    n_start = check_start_records(shadow_dir, expect_code_sha) if expect_code_sha else None
    frozen = load_frozen_scorer(scorer_root) if scorer_root else sc
    s_v, s_c = scorer_verdicts(shadow_dir, book, frozen)
    e_v, e_c = executor_verdicts(shadow_dir, ax.BOOKS[bot_id])
    s_rows = [(*k, v) for k, v in s_v.items()]
    e_rows = [(*k, v) for k, v in e_v.items()]
    res: dict[str, Any] = {"bot_id": bot_id, "n_scorer": len(s_rows), "n_executor": len(e_rows), "md5_scorer": md5_of(s_rows), "md5_executor": md5_of(e_rows),
                           "equal": md5_of(s_rows) == md5_of(e_rows), "attempts_scorer": sum(1 for r in s_rows if r[-1] == "attempt"),
                           "attempts_executor": sum(1 for r in e_rows if r[-1] == "attempt"),
                           "refusals_scorer": dict(collections.Counter(r[-1] for r in s_rows if r[-1] != "attempt")),
                           "refusals_executor": dict(collections.Counter(r[-1] for r in e_rows if r[-1] != "attempt")),
                           "window_counts_scorer": dict(s_c), "window_counts_executor": dict(e_c), "differing_keys": len(set(s_rows) ^ set(e_rows)),
                           "scorer_file_sha256": hashlib.sha256(Path(frozen.__file__).read_bytes()).hexdigest(), "scorer_is_separate_export": scorer_root is not None,
                           "start_records_checked": n_start}
    if ax.BOOKS[bot_id].exit_pin == h.EXIT_MODE_V2:
        mine, theirs = bx10_exit_slots(shadow_dir)
        res.update(bx10_n=len(mine), bx10_md5_executor=md5_of(mine), bx10_md5_shadow=md5_of(theirs), bx10_equal=md5_of(mine) == md5_of(theirs))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="decision-equivalence replay of the H5 arena executor (exploration day only; counts and md5)")
    ap.add_argument("--shadow-dir", required=True, type=Path)
    ap.add_argument("--bot-id", required=True, choices=sorted(ax.BOOKS))
    ap.add_argument("--scorer-root", type=Path, help="a separate export (git archive) of the book's registration sha: the reference scorer is loaded from here")
    ap.add_argument("--expect-code-sha", help="every start record must carry this code_sha and dirty=0")
    args = ap.parse_args(argv)
    print(json.dumps(run(args.shadow_dir, args.bot_id, args.scorer_root, args.expect_code_sha), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
