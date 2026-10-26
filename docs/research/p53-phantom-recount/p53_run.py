#!/usr/bin/env python3
"""P53 stage 2: run an existing tape counter UNCHANGED, with the lines marked by p53_mark.py removed from its tape input.

The counter is run with runpy as __main__ (its own argv, guards and outputs). Only `subprocess.Popen(["zstd", "-dc", PATH, ...])`
is wrapped: its stdout skips the marked line indices of PATH. Every other Popen is untouched. Forked workers (multiprocessing
fork, the Linux default on Python 3.12) inherit the wrapper. With --off nothing is skipped: that run must reproduce the counter's
original output byte for byte (equivalence proof of the wrapper).

Each fully read file appends one JSON line {"file", "lines", "skipped", "expected"} to P53_SKIPLOG (if set), from whichever
process read it, so the job can check that every marked line was reached and skipped.

Usage: p53_run.py (--off | --drops DROPS_JSON) [--sha256 HEX] SCRIPT [SCRIPT_ARGS ...]
  --sha256  refuse unless SCRIPT has this sha256 (pins the counter of record)
"""
from __future__ import annotations

import hashlib
import json
import os
import runpy
import subprocess
import sys

_REAL_POPEN = subprocess.Popen
DROPS: dict[str, frozenset] = {}


def _log(rec):
    p = os.environ.get("P53_SKIPLOG")
    if p:
        with open(p, "a") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")


class SkipLines:
    """Iterable stand-in for a zstd stdout pipe: yields every line except the marked indices."""

    def __init__(self, f, path):
        self._f = f
        self.path = path
        self.drop = DROPS.get(os.path.basename(path), frozenset())

    def __iter__(self):
        n = s = 0
        for i, line in enumerate(self._f):
            n += 1
            if i in self.drop:
                s += 1
                continue
            yield line
        _log({"file": os.path.basename(self.path), "lines": n, "skipped": s, "expected": len(self.drop)})

    def __getattr__(self, k):
        if k in ("read", "readline", "readlines"):
            raise RuntimeError("P53 wrapper supports line iteration only")  # a consumer that reads otherwise would bypass the skip
        return getattr(self._f, k)


class Popen(_REAL_POPEN):
    def __init__(self, args, *a, **k):
        super().__init__(args, *a, **k)
        if isinstance(args, (list, tuple)) and len(args) >= 3 and list(args[:2]) == ["zstd", "-dc"] and self.stdout is not None:
            self.stdout = SkipLines(self.stdout, str(args[2]))


def load_drops(path):
    with open(path) as fh:
        d = json.load(fh)
    return {base: frozenset(i for i, _k in v) for base, v in d["drops"].items()}


def main(argv):
    a = argv[1:]
    off, drops_path, want_sha = False, None, None
    while a and a[0].startswith("--"):
        if a[0] == "--off":
            off = True
            a = a[1:]
        elif a[0] == "--drops":
            drops_path, a = a[1], a[2:]
        elif a[0] == "--sha256":
            want_sha, a = a[1], a[2:]
        else:
            raise SystemExit(f"unknown option {a[0]}")
    if off == (drops_path is not None) or not a:
        raise SystemExit("usage: p53_run.py (--off | --drops DROPS_JSON) [--sha256 HEX] SCRIPT [ARGS ...]")
    script = a[0]
    if want_sha is not None:
        got = hashlib.sha256(open(script, "rb").read()).hexdigest()
        if got != want_sha:
            raise SystemExit(f"REFUSED {script} sha256 {got} != {want_sha}")
    DROPS.clear()
    if drops_path:
        DROPS.update(load_drops(drops_path))
    subprocess.Popen = Popen
    sys.argv = list(a)
    try:
        runpy.run_path(script, run_name="__main__")
    finally:
        subprocess.Popen = _REAL_POPEN


if __name__ == "__main__":
    main(sys.argv)
