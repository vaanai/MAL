#!/usr/bin/env python3
"""P2 proof for the follower's inner-instruction migration rows (QP-RULING-1107-tip-migration-crossbook s3).

Fetches RECENT slots once (Helius getBlock through the follower's own make_rpc: its rate limiter and credit counter), and feeds the SAME responses to two followers:
OLD (the follower source file given by --old-follower-file, normally origin/main's) and NEW (the checkout's tools/fast_tip_follower.py). Blocks are held only in a small
in-memory memo (each response is deep-copied to each follower); the two outputs go to a private tmp dir that is deleted at the end. No pool-account lookups are made
(lookup is None for both), so the only RPC is getSlot + one getBlock per slot.

PRINTS ONLY one JSON line: md5 of the trades-, creates-, skipped-, gaps- and observe streams for old and new (+ equal flags), whether every old migration line is
present byte-identical and in order in new, whether every extra line is event_source inner_event, and counts. Never a signature, mint, pool or amount.
Reads no tip tape, derived copy or sealed path; the RPC key is taken from the environment (HELIUS_API_KEY), never printed.

  set -a; . <follower env file>; set +a; nice -n 19 python3 scripts/ops/tip_follower_p2_equiv.py --old-follower-file OLD.py --slots 33500
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import types
from collections import OrderedDict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

BAD_WORDS = ("probe", "shadow", "tape", "arena", "sealed")
STREAMS = ("trades", "creates", "skipped-slots", "gaps", "observe")


def load_old(path: Path):
    mod = types.ModuleType("fast_tip_follower_old")
    mod.__file__ = str(path)
    exec(compile(path.read_text(), str(path), "exec"), mod.__dict__)
    if hasattr(mod, "inner_migration_rows"):
        raise SystemExit("REFUSED: the OLD follower file already has inner_migration_rows")
    return mod


class Clock:
    def __init__(self) -> None:
        self.ms = int(time.time() * 1000)

    def __call__(self) -> int:
        return self.ms


def make_followers(old_mod, new_mod, root: Path, fetch, tip: int, clock):
    out = []
    for name, mod in (("old", old_mod), ("new", new_mod)):
        f = mod.TipFollower(out_dir=root / name / "tip", creates_dir=root / name / "creates", state_dir=root / name / "state", fetch=fetch, get_tip=lambda: tip,
                            clock_ms=clock, sleep=lambda s: None, log=lambda m: None, max_retries=3, backlog_slots=10**9, fetch_workers=1)
        out.append(f)
    return out


def stream_md5(root: Path) -> dict[str, str]:
    """md5 over each stream's files concatenated in name order. Names are only used to group; nothing is printed except the md5."""
    groups: dict[str, list[Path]] = {s: [] for s in STREAMS}
    for sub in ("tip", "creates"):
        d = root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            n = p.name
            for s in STREAMS:
                if n.startswith(s + "-") or n.startswith(s + "."):
                    groups[s].append(p)
    res = {}
    for s, paths in groups.items():
        h = hashlib.md5()
        for p in paths:
            with p.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        res[s] = h.hexdigest()
    return res


def migration_lines(root: Path) -> list[bytes]:
    out: list[bytes] = []
    for p in sorted((root / "tip").glob("migrations-*.jsonl")):
        out += p.read_bytes().splitlines()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-follower-file", required=True)
    ap.add_argument("--slots", type=int, default=33500, help="slots to fetch (about 215 ms each at the current rate: 33500 is about 2 h)")
    ap.add_argument("--end-lag-slots", type=int, default=300, help="stay this far behind the confirmed tip")
    ap.add_argument("--rps", type=float, default=5.0)
    ap.add_argument("--max-credits", type=int, default=40000)
    ap.add_argument("--tmp-parent", default=os.environ.get("TMPDIR", "/tmp"))
    a = ap.parse_args(argv)
    if a.rps <= 0 or a.slots < 1:
        ap.error("bad arguments")
    parent = Path(a.tmp_parent).resolve()
    if any(w in str(parent).lower() for w in BAD_WORDS):
        print("REFUSED: tmp parent name", file=sys.stderr)
        return 2
    from tools import fast_tip_follower as new_mod
    from tools.pump_history_backfill import resolve_rpc_url

    url, kind = resolve_rpc_url(None, os.environ)
    if kind != "helius":
        print("REFUSED: HELIUS_API_KEY not set", file=sys.stderr)
        return 2
    old_mod = load_old(Path(a.old_follower_file))
    real_fetch, real_tip, credits, _lookup = new_mod.make_rpc(url, a.rps, 1)
    try:
        end = real_tip() - a.end_lag_slots
    except Exception as exc:
        print(f"FAILED: getSlot {type(exc).__name__}", file=sys.stderr)
        return 3
    start = end - a.slots + 1
    memo: OrderedDict[int, tuple] = OrderedDict()

    def fetch(slot: int):
        if slot not in memo:
            if credits() >= a.max_credits:
                raise new_mod.TransientError("credit cap")  # surfaces as an unfetched slot in both followers alike; the run then reports capped
            memo[slot] = real_fetch(slot)
            while len(memo) > 120:
                memo.popitem(last=False)
        blk, code = memo[slot]
        return (copy.deepcopy(blk), code)

    root = Path(tempfile.mkdtemp(prefix="p2equiv-", dir=str(parent)))
    if any(w in str(root).lower() for w in BAD_WORDS):
        shutil.rmtree(root, ignore_errors=True)
        print("REFUSED: tmp dir name", file=sys.stderr)
        return 2
    try:
        clock = Clock()
        old, new = make_followers(old_mod, new_mod, root, fetch, end, clock)
        for f in (old, new):
            f.last_done = start - 1
            f._checkpoint(None)
        capped = False
        while old.last_done < end or new.last_done < end:
            before = (old.last_done, new.last_done)
            for f in (old, new):  # old first: the shared memo is filled by the first follower and read by the second
                if f.last_done < end:
                    f.step()
            if credits() >= a.max_credits:
                capped = True
                break
            if (old.last_done, new.last_done) == before:
                capped = True
                break
        for f in (old, new):
            f.writer.close()
            f.cwriter.close()
        if old.last_done != new.last_done:
            print(json.dumps({"error": "followers_out_of_step"}))
            return 4
        mo, mn = stream_md5(root / "old"), stream_md5(root / "new")
        lo, ln = migration_lines(root / "old"), migration_lines(root / "new")
        it = iter(ln)
        old_present = all(any(x == y for y in it) for x in lo)  # ordered subsequence: byte-identical, same order
        extra = [x for x in ln if x not in set(lo)] if len(ln) != len(lo) else []
        extra_ok = all(b'"event_source":"inner_event"' in x for x in extra) and len(ln) - len(lo) == len(extra)
        rows_o = [json.loads(x) for x in lo]
        rows_n = [json.loads(x) for x in ln]
        res = {
            "slots_requested": a.slots,
            "slots_done": new.last_done - start + 1,
            "capped": capped,
            "blocks_fetched": new.blocks,
            "credits_used_estimate": credits(),
            "old_blocks": old.blocks,
            "md5_old": {s: mo[s] for s in STREAMS},
            "md5_new": {s: mn[s] for s in STREAMS},
            "equal": {s: mo[s] == mn[s] for s in STREAMS},
            "old_migration_rows_all_present_byte_identical_in_new": old_present,
            "extra_rows_all_inner_event": extra_ok,
            "extra_rows": len(ln) - len(lo),
            "old_migrations": sum(1 for r in rows_o if r.get("type") == "migration"),
            "new_migrations": sum(1 for r in rows_n if r.get("type") == "migration"),
            "completes": sum(1 for r in rows_o if r.get("type") == "complete"),
            "inner_added_counter": new.migrations_inner_added,
            "inner_pool_mismatch_counter": new.migrations_inner_pool_mismatch,
            "inner_errors_counter": new.migrations_inner_errors,
            "note": "walk-2-hour structure; no lookup (V null in both); counts and md5 only",
        }
        print(json.dumps(res, sort_keys=True))
        ok = all(res["equal"].values()) and old_present and extra_ok and not capped
        return 0 if ok else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
