#!/usr/bin/env python3
"""Offline comparison of two probe-executor fill logs (DEC-020 pre-deploy check: old sha vs new sha, same intents).

    compare_executor_fills.py OLD.jsonl NEW.jsonl [--ignore FIELD ...]

Reads two JSONL files, no network, no key, no state. Rows are compared in order. Per row it drops the TIMING fields
(any key ending in `_ms`, plus `latency`, `signature`, `blockhash`, `lvbh`, `slot`-style keys listed in DEFAULT_IGNORE),
and reports an md5 of the remaining row. It prints:
  - row counts,
  - the md5 of each file's kept rows and whether they are equal,
  - for every row pair that differs after the drop: the NAMES of the differing fields (never values, so nothing
    sensitive is echoed),
  - the set of fields that were dropped and the set that differed, so the PR can say exactly which differ.
Exit 0 when the kept rows are identical, 1 when they differ or the row counts differ, 2 on bad input.
The dry-run executor reads live RPC state, so two runs at different times can legitimately differ in price fields; the
script reports that instead of assuming it. Say in the PR which fields differed and why.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

DEFAULT_IGNORE = {"latency", "signature", "blockhash", "lvbh", "pool_slot", "state_slot", "landed_slot", "slot"}


def _rows(path: Path) -> list[dict]:
    out = []
    for raw in path.read_text().splitlines():
        if raw.strip():
            r = json.loads(raw)
            if not isinstance(r, dict):
                raise ValueError("row is not an object")
            out.append(r)
    return out


def _kept(row: dict, ignore: set[str]) -> dict:
    return {k: v for k, v in row.items() if not k.endswith("_ms") and k not in ignore}


def _md5(obj) -> str:
    return hashlib.md5(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def compare(old: list[dict], new: list[dict], ignore: set[str]) -> dict:
    dropped: set[str] = set()
    differing: set[str] = set()
    row_diffs: list[tuple[int, list[str]]] = []
    ko = [_kept(r, ignore) for r in old]
    kn = [_kept(r, ignore) for r in new]
    for r in (*old, *new):
        dropped |= {k for k in r if k.endswith("_ms") or k in ignore}
    for i, (a, b) in enumerate(zip(ko, kn)):
        if a != b:
            names = sorted({k for k in {*a, *b} if a.get(k) != b.get(k)})
            row_diffs.append((i, names))
            differing |= set(names)
    return {"n_old": len(old), "n_new": len(new), "md5_old": _md5(ko), "md5_new": _md5(kn),
            "identical": len(old) == len(new) and not row_diffs, "row_diffs": row_diffs,
            "dropped_fields": sorted(dropped), "differing_fields": sorted(differing)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--ignore", nargs="*", default=[], help="extra field names to drop")
    args = ap.parse_args(argv)
    try:
        res = compare(_rows(Path(args.old)), _rows(Path(args.new)), DEFAULT_IGNORE | set(args.ignore))
    except (OSError, ValueError) as exc:
        print(f"bad input: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(f"rows old={res['n_old']} new={res['n_new']}")
    print(f"md5 old={res['md5_old']} new={res['md5_new']} identical={res['identical']}")
    print(f"dropped fields: {res['dropped_fields']}")
    print(f"differing fields: {res['differing_fields']}")
    for i, names in res["row_diffs"][:50]:
        print(f"  row {i}: {names}")
    return 0 if res["identical"] else 1


if __name__ == "__main__":
    sys.exit(main())
