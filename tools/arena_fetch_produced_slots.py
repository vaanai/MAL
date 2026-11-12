#!/usr/bin/env python3
"""Fetch the PRODUCED (block-bearing) slots of a range from a public Solana RPC and write a pinned file for tools/arena_gap_reclassify.py.

Separate from the reclassifier on purpose: the reclassifier stays offline and deterministic; this script is the only network step, and its
output file is hashed and pinned. Public endpoint only (https://api.mainnet-beta.solana.com), JSON-RPC getBlocks(start, end) with
end - start <= 500,000 (calls are chunked to 50,000), one request per second, finalized commitment. No API key, no credential material, no wallet; it refuses any URL that is
not on the allow list. Input: the uncovered ranges of one program in a full reclassifier output; only those ranges (optionally merged) are queried, not the whole window.
Output JSON lines: {"type":"produced_slots","prog","source","commitment","queried":[[a,b],...],"first","last","count","ranges":[[a,b],...]} then {"type":"sha256",...}.
A slot inside a queried range that is NOT in ranges produced no block (leader-skipped). Slots outside every queried range are unknown.
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
CHUNK = 50_000  # a 500k call timed out on the public endpoint and a 100k call timed out at the newest slots; 50k is reliable
RETRIES = 5
MIN_INTERVAL_S = 1.0


def rpc_get_blocks(url: str, a: int, b: int, commitment: str = "finalized") -> list[int]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBlocks", "params": [a, b, {"commitment": commitment}]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
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


def merge_ranges(rs: list[list[int]], gap: int) -> list[list[int]]:
    out: list[list[int]] = []
    for a, b in sorted(rs):
        if out and a <= out[-1][1] + 1 + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def fetch(url: str, queried: list[list[int]], getter=rpc_get_blocks, sleep=time.sleep) -> list[list[int]]:
    """getBlocks over each queried range (chunked to CHUNK slots per call, 1 request per second). Returns the produced ranges."""
    if url not in ALLOWED_URLS:
        raise ValueError("url not on the allow list (public endpoint only, no key)")
    got: list[int] = []
    first_call = True
    for lo, hi in queried:
        if lo > hi:
            raise ValueError("bad range")
        a = lo
        while a <= hi:
            b = min(hi, a + CHUNK - 1)
            if not first_call:
                sleep(MIN_INTERVAL_S)
            first_call = False
            for attempt in range(RETRIES):
                try:
                    r = getter(url, a, b)
                    break
                except (OSError, RuntimeError):
                    if attempt == RETRIES - 1:
                        raise
                    sleep(MIN_INTERVAL_S * 5)
            if any(x < a or x > b for x in r):
                raise RuntimeError("rpc returned a slot outside the requested range")
            got.extend(r)
            a = b + 1
    return to_ranges(got)


def render(url: str, queried: list[list[int]], ranges: list[list[int]], prog: str = "pumpswap") -> list[str]:
    head = {"type": "produced_slots", "prog": prog, "source": url, "commitment": "finalized", "queried": queried, "first": queried[0][0], "last": queried[-1][1],
            "count": sum(b - a + 1 for a, b in ranges), "ranges": ranges}
    l1 = json.dumps(head, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256((l1 + "\n").encode()).hexdigest()
    return [l1, json.dumps({"type": "sha256", "lines": 1, "sha256": h}, sort_keys=True, separators=(",", ":"))]


def uncovered_from_reclassify(path: str, prog: str, merge_gap: int) -> list[list[int]]:
    rs: list[list[int]] = []
    for ln in open(path):
        r = json.loads(ln)
        if r.get("type") == "gap" and r.get("prog") == prog and r.get("class") in ("partial", "empty"):
            rs.extend(r["uncovered_ranges"])
    if not rs:
        raise ValueError("no uncovered ranges in input")
    return merge_ranges(rs, merge_gap)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=ALLOWED_URLS[0])
    ap.add_argument("--from-reclassify", required=True, help="reclassifier output (full, not counts-only)")
    ap.add_argument("--prog", required=True, choices=("pumpswap", "pumpfun"))
    ap.add_argument("--merge-gap", type=int, default=0, help="merge uncovered ranges closer than this many slots (fewer calls; the padding is queried too)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        queried = uncovered_from_reclassify(args.from_reclassify, args.prog, args.merge_gap)
        print(f"{len(queried)} ranges, {sum(b - a + 1 for a, b in queried)} slots to query", file=sys.stderr)
        ranges = fetch(args.url, queried)
    except (ValueError, RuntimeError, OSError) as e:
        print(f"refused/failed: {e}", file=sys.stderr)
        return 2
    Path(args.out).write_text("\n".join(render(args.url, queried, ranges, args.prog)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
