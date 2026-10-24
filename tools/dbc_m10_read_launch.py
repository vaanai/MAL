#!/usr/bin/env python3
"""dbc_m10_read_launch: Option A launcher of the ONE-LOOK DBC m10 forward read (QP-RULING-1024-read-hardening.md item 3). PAPER ONLY.

    python3 tools/dbc_m10_read_launch.py --tag-root <clean worktree of tag dbc-m10-forward-v1-am1> --confirm-one-look
    python3 tools/dbc_m10_read_launch.py --tag-root <same> --warmup-only          (Option B pre-step: warm-up only, never calls run())

Whole behaviour:
* prints its own sha256;
* imports ONLY the tagged module tools.dbc_fwd_read_am1 (from --tag-root, checked by path); the unamended module is reached only as that module's attribute R
  (the library the tagged code itself imports), never run;
* calls dbc_fwd_read_am1.run(Path(dbc_fwd_read_am1.DEFAULT_OUT), make_fetch=mk). Every other argument stays default (root, now, fetch, marker, base_map); no --out.
  Returns run()'s rc.
* mk() is called by the tagged run() in its PRE-CLAIM setup block (an exception there = rc 7, marker NOT claimed):
    f = R.helius_fetch() (same key file, lock slots, Pacer 4.0, http_rpc, getMultipleAccounts base64 finalized);
    warm-up: three calls f(sorted(keys of R.BASE_MAP)[:100]), 20 s apart, each must return >= 1 account, otherwise raise;
    returns fetch(batch) = f(batch), retried on RuntimeError every 60 s, up to 60 attempts, then raise. Keys, order, 100-batching, commitment and decode are the
    tagged ones; a null account is not retried (it stays unmapped, as tagged).
This changes no AM term and no R4 term. It only changes how long a Helius outage the post-claim R4 fetch survives.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

WARMUP_CALLS, WARMUP_GAP_S, WARMUP_KEYS = 3, 20, 100
RETRY_ATTEMPTS, RETRY_GAP_S = 60, 60


def own_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def load_am1(tag_root: str) -> Any:
    """Import tools.dbc_fwd_read_am1 from tag_root and nowhere else."""
    if "tools" in sys.modules:
        raise RuntimeError("a 'tools' package is already imported; refusing (the tagged one must be the only one)")
    here = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
    sys.path[:] = [os.path.realpath(tag_root)] + [p for p in sys.path if p and os.path.realpath(p) != here]
    import importlib

    A = importlib.import_module("tools.dbc_fwd_read_am1")
    want = os.path.realpath(os.path.join(tag_root, "tools", "dbc_fwd_read_am1.py"))
    for mod in (A, A.R, A.L, A.S, A.W, A.X):
        got = os.path.realpath(mod.__file__)
        if os.path.dirname(got) != os.path.dirname(want):
            raise RuntimeError(f"tagged module loaded from the wrong place: {got}")
    return A


def warm_up(f: Callable[[list[str]], dict[str, Any]], base_map: str, sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> None:
    keys = sorted(json.loads(Path(base_map).read_text()))[:WARMUP_KEYS]
    for i in range(WARMUP_CALLS):
        if i:
            sleep(WARMUP_GAP_S)
        got = f(keys)
        if len(got) < 1:
            raise RuntimeError(f"warm-up call {i + 1}/{WARMUP_CALLS} returned no account")
        log(f"warm-up {i + 1}/{WARMUP_CALLS} ok (pre-claim)")


def retrying(f: Callable[[list[str]], dict[str, Any]], sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> Callable[[list[str]], dict[str, Any]]:
    def fetch(batch: list[str]) -> dict[str, Any]:
        for att in range(1, RETRY_ATTEMPTS + 1):
            try:
                return f(batch)
            except RuntimeError as e:
                if att >= RETRY_ATTEMPTS:
                    raise
                log(f"R4 fetch attempt {att}/{RETRY_ATTEMPTS} failed ({str(e)[:120]}); retry in {RETRY_GAP_S} s")
                sleep(RETRY_GAP_S)
        raise AssertionError("unreachable")

    return fetch


def make_fetch_factory(A: Any, sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> Callable[[], Callable[[list[str]], dict[str, Any]]]:
    def mk() -> Callable[[list[str]], dict[str, Any]]:
        f = A.R.helius_fetch()
        warm_up(f, A.R.BASE_MAP, sleep, log)
        return retrying(f, sleep, log)

    return mk


def launch(A: Any, sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> int:
    return A.run(Path(A.DEFAULT_OUT), make_fetch=make_fetch_factory(A, sleep, log))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tag-root", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--confirm-one-look", action="store_true")
    g.add_argument("--warmup-only", action="store_true")
    ns = ap.parse_args(argv)
    print(f"launcher sha256 {own_sha256()} ({os.path.realpath(__file__)})", flush=True)
    log = lambda s: print(s, flush=True)  # noqa: E731
    try:
        A = load_am1(ns.tag_root)
    except Exception as e:  # noqa: BLE001
        print(f"REFUSED (launcher import, marker NOT claimed): {type(e).__name__}: {str(e)[:160]}", file=sys.stderr, flush=True)
        return 7
    print(f"tagged module {os.path.realpath(A.__file__)}", flush=True)
    if ns.warmup_only:
        try:
            warm_up(A.R.helius_fetch(), A.R.BASE_MAP, time.sleep, log)
        except Exception as e:  # noqa: BLE001
            print(f"REFUSED (warm-up, marker NOT claimed): {type(e).__name__}: {str(e)[:120]}", file=sys.stderr, flush=True)
            return 7
        print("WARM-UP OK (Option B pre-step; run() not called)", flush=True)
        return 0
    return launch(A, time.sleep, log)


if __name__ == "__main__":
    sys.exit(main())
