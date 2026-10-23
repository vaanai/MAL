#!/usr/bin/env python3
"""Fetch the PRODUCED (block-bearing) slots of a range from a public Solana RPC and write a pinned file for tools/arena_gap_reclassify.py.

Separate from the reclassifier on purpose: the reclassifier stays offline and deterministic; this script is the only network step, and its
output file is hashed and pinned. Public endpoint only (https://api.mainnet-beta.solana.com), JSON-RPC getBlocks(start, end) with
end - start <= 500,000 (calls are chunked to 100,000), one request per second, finalized commitment. No API key, no credential material, no wallet; it refuses any URL that is
not on the allow list. Input is the range [first, last] of slots (from the reclassifier output's uncovered ranges, or given explicitly).
Output JSON lines: {"type":"produced_slots","source","commitment","first","last","count","ranges":[[a,b],...]} then {"type":"sha256",...}.
A slot in [first, last] that is NOT in ranges produced no block (leader-skipped).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ALLOWED_URLS = ("https://api.mainnet-beta.solana.com",)
MAX_SPAN = 500_000  # RPC limit per call
CHUNK = 100_000  # a 500k call timed out on the public endpoint; 100k takes ~8 s
RETRIES = 3
MIN_INTERVAL_S = 1.0


def rpc_get_blocks(url: str, a: int, b: int, commitment: str = "finalized") -> list[int]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBlocks", "params": [a, b, {"commitment": commitment}]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    if "result" not in d:
        raise RuntimeError(f"rpc error: {str(d.get('error'))[:200]}")
    return d["result"]


def to_ranges(slots: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for s in sorted(set(slots)):
        if out and out[-1][1] == s - 1:
            out[-1][1] = s
        else:
            out.append([s, s])
    return out


def fetch(url: str, first: int, last: int, getter=rpc_get_blocks, sleep=time.sleep) -> list[list[int]]:
    if url not in ALLOWED_URLS:
        raise ValueError("url not on the allow list (public endpoint only, no key)")
    if first > last:
        raise ValueError("first > last")
    got: list[int] = []
    a = first
    while a <= last:
        b = min(last, a + CHUNK - 1)
        for attempt in range(RETRIES):
            try:
                got.extend(getter(url, a, b))
                break
            except (OSError, RuntimeError):
                if attempt == RETRIES - 1:
                    raise
                sleep(MIN_INTERVAL_S * 5)
        a = b + 1
        if a <= last:
            sleep(MIN_INTERVAL_S)
    if any(s < first or s > last for s in got):
        raise RuntimeError("rpc returned a slot outside the requested range")
    return to_ranges(got)


def render(url: str, first: int, last: int, ranges: list[list[int]]) -> list[str]:
    head = {"type": "produced_slots", "source": url, "commitment": "finalized", "first": first, "last": last,
            "count": sum(b - a + 1 for a, b in ranges), "ranges": ranges}
    l1 = json.dumps(head, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256((l1 + "\n").encode()).hexdigest()
    return [l1, json.dumps({"type": "sha256", "lines": 1, "sha256": h}, sort_keys=True, separators=(",", ":"))]


def span_from_reclassify(path: str) -> tuple[int, int]:
    lo, hi = None, None
    for ln in open(path):
        r = json.loads(ln)
        if r.get("type") == "gap" and r.get("class") in ("partial", "empty"):
            for a, b in r["uncovered_ranges"]:
                lo = a if lo is None else min(lo, a)
                hi = b if hi is None else max(hi, b)
    if lo is None:
        raise ValueError("no uncovered ranges in input")
    return lo, hi


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=ALLOWED_URLS[0])
    ap.add_argument("--from-reclassify", help="reclassifier output (full, not counts-only): span = min..max uncovered slot")
    ap.add_argument("--first", type=int)
    ap.add_argument("--last", type=int)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        first, last = span_from_reclassify(args.from_reclassify) if args.from_reclassify else (args.first, args.last)
        if first is None or last is None:
            raise ValueError("give --from-reclassify or --first and --last")
        ranges = fetch(args.url, first, last)
    except (ValueError, RuntimeError, OSError) as e:
        print(f"refused/failed: {e}", file=sys.stderr)
        return 2
    Path(args.out).write_text("\n".join(render(args.url, first, last, ranges)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
