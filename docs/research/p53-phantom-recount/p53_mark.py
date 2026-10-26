#!/usr/bin/env python3
"""P53 stage 1: mark the phantom-class rows of the closed arena tape with the HV6 filter (tools/hv6_phantom.py, merged in #980).

Counts only. The filter is IMPORTED, not copied: every row of every file, in file order, goes through one PhantomFilter.check(row),
exactly as tools/hv6_runner.py's row hook does (all row types; a row that is not valid JSON is kept and counted as bad_json).
Output (JSON): for each file, the 0-based indices of the dropped lines (as `for line in zstd -dc` stdout yields them, the same
split the consumer scripts use) and their kind; per hour, rows and drops by (type, prog) and kind. No price, fill, exit, outcome
or return is read or written. Fields the filter reads: type, trader, signature, slot, t_recv_ms. This script also reads prog.

Usage: p53_mark.py OUT_JSON FILE [FILE ...]   (files in time order; only closed hours 2026-10-19T08 .. 2026-10-24T23)
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import re
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
sys.path.insert(0, REPO)
from tools.hv6_phantom import PhantomFilter  # noqa: E402  (the filter of record; not a copy)

PATH_RE = re.compile(r"^/data/mal/arena/tape/tape-2026-10-(19T(0[89]|1[0-9]|2[0-3])|2[0-4]T([01][0-9]|2[0-3]))\.jsonl\.zst$")


def lines_of(path, allow_any=False):
    """Yield the raw lines of one tape file, split exactly as the consumers split them (iteration over zstd stdout)."""
    if not allow_any and not PATH_RE.match(path):
        raise SystemExit(f"REFUSED path outside the plan: {path}")
    if path.endswith(".zst"):
        proc = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, bufsize=1 << 22)
        yield from proc.stdout
        if proc.wait() != 0:
            raise SystemExit(f"zstd failed on {path}")
    else:  # plain .jsonl, tests only (allow_any)
        with open(path, "rb") as f:
            yield from f


def mark(files, allow_any=False):
    flt = PhantomFilter()
    drops = {}
    hours = {}
    prev = None
    for f in files:
        base = os.path.basename(f)
        if prev is not None and base <= prev:
            raise SystemExit(f"REFUSED files not in time order: {prev} then {base}")
        prev = base
        idx = []
        rows = collections.Counter()
        dk = collections.Counter()
        n = 0
        for i, line in enumerate(lines_of(f, allow_any)):
            n += 1
            try:
                r = json.loads(line)
            except ValueError:
                rows["bad_json"] += 1
                continue
            if not isinstance(r, dict):
                rows["bad_json"] += 1
                continue
            tp = f"{r.get('type')}|{r.get('prog')}"
            rows[tp] += 1
            kind = flt.check(r)
            if kind is not None:
                idx.append([i, kind])
                dk[f"{kind}|{tp}"] += 1
        drops[base] = idx
        hours[base] = {"lines": n, "rows": dict(rows), "drops": dict(dk)}
        print("done", base, "lines", n, "drops", len(idx), "filter_keys", flt.size(), file=sys.stderr, flush=True)
    return {"drops": drops, "hours": hours, "filter_counts": dict(flt.counts), "filter_total": flt.total()}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(argv):
    out, files = argv[1], argv[2:]
    if not files:
        raise SystemExit("usage: p53_mark.py OUT_JSON FILE [FILE ...]")
    res = mark(files)
    res["filter_module_sha256"] = sha256_of(os.path.join(REPO, "tools", "hv6_phantom.py"))
    res["files"] = [os.path.basename(f) for f in files]
    with open(out, "w") as fh:
        json.dump(res, fh, sort_keys=True)
    print(json.dumps({"filter_counts": res["filter_counts"], "filter_total": res["filter_total"], "files": len(files)}))


if __name__ == "__main__":
    main(sys.argv)
