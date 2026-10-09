#!/usr/bin/env python3
"""Daily synthetic-class audit of the H5 executor's buys (DEC-024 Amendment 2, Clarification 1).

The shadow classified each pool at decision time and the executor bought only on `synthetic: false`. This tool re-classifies every
pool the executor made a buy decision on, with the read-time procedure of EXP-024 Amendment 4 (B4, `tools/synthetic_class.py`), and
counts the pools where the shadow's class and B4's class disagree. A disagreement on any pool is a live halt (DEC-024 5.6).

INPUT, one of
  --decisions FILE|-   a JSONL already projected to {pool, mint, synthetic, synthetic_src, signature}. Any other key is dropped on load.
  --ledger FILE|-      the executor ledger (`h5-ledger.jsonl`), projected here. Per line: parse, keep only the five keys above, plus
                       two selectors that are read and thrown away: `kind` (only `kind == "decision"` rows are audited) and `ts_ms`
                       (only rows whose UTC day is --date). No other field of a row is kept, printed or passed on: not a fill, size,
                       exit, price or P&L field, not the nested `plan`, `anchor` or `sent` objects. The JSON parser's object hook drops every
                       other key as soon as its object closes. `-` reads stdin, so a root-owned ledger is piped in with `sudo`.
  --project-only       with --ledger: print the projected rows (the five keys) as JSONL and stop. For a two-stage run.

`signature` on a decision row is the signature of OUR buy transaction, not of the trigger print (the ledger carries no trigger
signature). It is used as B4's `before` anchor. B4 says "before the s0 print's signature"; our buy is after s0, so this window is a
SUPERSET of B4's: the migrate tx is older than both, so it is still found, but a pool with more than `cap` (1,000) signatures between
its migrate tx and our buy is `unclassified` here where B4 would have found it. That shows in n_unclassified_now, never in n_disagree.
A buy that never landed has an unknown signature to the node: the lookup fails and the pool is unclassified.

OUTPUT (one JSON line on stdout, exactly these keys, and nothing else on stdout):
  {"date", "n_pools", "n_disagree", "n_unclassified_now", "halt": n_disagree > 0}
No pool, mint, signature, per-pool class or fill is printed anywhere. stderr carries only counts and error type names.

A pool "disagrees" when B4 classifies it (synthetic or non_synthetic) and the shadow's class is not the same. The shadow's class is
`synthetic` -> synthetic, `false` -> non_synthetic; an absent, null or non-bool field is no class, so a bought pool with no recorded
class disagrees with any B4 class (the executor must have refused it). A pool B4 cannot classify now is counted in n_unclassified_now.

Exit code 3 if n_disagree > 0, 2 on a usage error or a refused RPC URL, else 0. Public RPC only (Helius and keyed URLs are refused).
Never run this against, or beside, `python -m tools.pump_structure_monitor`: it does not call the monitor, it imports its helpers.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlparse

from tools import pump_structure_monitor as M
from tools import synthetic_class as sc

KEEP_KEYS = ("pool", "mint", "synthetic", "synthetic_src", "signature")  # the only fields ever kept
SELECTOR_KEYS = ("kind", "ts_ms")  # read to choose rows, then discarded
OUTPUT_KEYS = ("date", "n_pools", "n_disagree", "n_unclassified_now", "halt")
_READ = frozenset(KEEP_KEYS) | frozenset(SELECTOR_KEYS)


def _hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    return {k: v for k, v in pairs if k in _READ}  # every other key is dropped as its object closes, at any depth


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _project(row: Mapping[str, Any]) -> dict[str, Any]:
    syn = row.get("synthetic")
    return {
        "pool": _str(row.get("pool")), "mint": _str(row.get("mint")),
        "synthetic": syn if isinstance(syn, bool) else None,  # only a real bool is a class
        "synthetic_src": _str(row.get("synthetic_src")), "signature": _str(row.get("signature")),
    }


def _parse(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        row = json.loads(line, object_pairs_hook=_hook)
    except ValueError:
        return {"__bad__": True}
    return row if isinstance(row, dict) else {"__bad__": True}


def _utc_day(ts_ms: Any) -> str | None:
    if isinstance(ts_ms, bool) or not isinstance(ts_ms, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def project_ledger(lines: Iterable[str], date: str) -> tuple[list[dict[str, Any]], int]:
    """(projected decision rows of UTC day `date`, count of unparseable lines). Nothing but the five KEEP_KEYS survives."""
    rows: list[dict[str, Any]] = []
    bad = 0
    for line in lines:
        row = _parse(line)
        if row is None:
            continue
        if row.get("__bad__"):
            bad += 1
            continue
        if row.get("kind") == "decision" and _utc_day(row.get("ts_ms")) == date:
            rows.append(_project(row))
        del row
    return rows, bad


def load_projected(lines: Iterable[str]) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    bad = 0
    for line in lines:
        row = _parse(line)
        if row is None:
            continue
        if row.get("__bad__"):
            bad += 1
            continue
        rows.append(_project(row))
    return rows, bad


def shadow_class(synthetic: Any) -> str | None:
    if synthetic is True:
        return sc.CLASS_SYNTHETIC
    if synthetic is False:
        return sc.CLASS_NON_SYNTHETIC
    return None


def run_audit(rows: Sequence[Mapping[str, Any]], date: str, classify: Callable[[Mapping[str, Any]], str]) -> dict[str, Any]:
    """`classify(row)` returns the B4 class string. Rows are grouped by pool; a row with no pool or mint cannot be classified."""
    pools: dict[str, list[Mapping[str, Any]]] = {}
    for i, r in enumerate(rows):
        pools.setdefault(r.get("pool") or f"?row{i}", []).append(r)
    n_disagree = n_unclassified = 0
    for rs in pools.values():
        first = rs[0]
        cls = sc.CLASS_UNCLASSIFIED
        if first.get("pool") and first.get("mint"):
            try:
                cls = classify(first)
            except Exception as exc:  # noqa: BLE001  (a lookup that blows up is an unclassified pool, not a crash; the type goes to stderr)
                print(f"classify error: {type(exc).__name__}", file=sys.stderr)
        if cls not in (sc.CLASS_SYNTHETIC, sc.CLASS_NON_SYNTHETIC):
            n_unclassified += 1
        elif {shadow_class(r.get("synthetic")) for r in rs} != {cls}:
            n_disagree += 1
    return {"date": date, "n_pools": len(pools), "n_disagree": n_disagree, "n_unclassified_now": n_unclassified, "halt": n_disagree > 0}


def check_public_rpc(url: str) -> str | None:
    p = urlparse(url)
    if "helius" in url.lower() or p.query or p.username or p.password:
        return "refused: public RPC only (no Helius, no keyed URL)"
    return None


def _lines(path: str) -> Iterator[str]:
    if path == "-":
        yield from sys.stdin
        return
    with open(path, encoding="utf-8") as fh:
        yield from fh


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="DEC-024 Am.2 C1 daily audit: shadow class vs EXP-024 Am.4 B4 (counts only).")
    p.add_argument("--date", required=True, help="UTC day audited, YYYY-MM-DD (filters ledger rows by ts_ms; a label for --decisions)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--decisions", metavar="FILE|-", help="JSONL projected to pool, mint, synthetic, synthetic_src, signature")
    g.add_argument("--ledger", metavar="FILE|-", help="executor ledger JSONL; projected here to those five keys")
    p.add_argument("--project-only", action="store_true", help="with --ledger: print the projected rows and stop (no RPC)")
    p.add_argument("--rpc-url", default=M.DEFAULT_RPC, help="public mainnet RPC (default %(default)s)")
    p.add_argument("--min-interval", type=float, default=0.5, help="seconds between RPC calls")
    p.add_argument("--max-calls", type=int, default=3000, help="hard cap on RPC calls, retries included")
    p.add_argument("--cap", type=int, default=sc.CAP_DEFAULT, help="signatures per search (B4: 1000)")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, rpc: Any = None) -> int:
    args = parse_args(argv)
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print("usage: --date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    if args.project_only and not args.ledger:
        print("usage: --project-only needs --ledger", file=sys.stderr)
        return 2
    if args.ledger:
        rows, bad = project_ledger(_lines(args.ledger), args.date)
    else:
        rows, bad = load_projected(_lines(args.decisions))
    if bad:
        print(f"{bad} unparseable input line(s) skipped", file=sys.stderr)
    if args.project_only:
        for r in rows:
            print(json.dumps(r, separators=(",", ":")))
        return 0
    if rpc is None:
        refusal = check_public_rpc(args.rpc_url)
        if refusal:
            print(refusal, file=sys.stderr)
            return 2
        rpc = M.RpcClient(args.rpc_url, min_interval=args.min_interval, max_calls=args.max_calls)

    def classify(r: Mapping[str, Any]) -> str:
        return sc.classify_pool(rpc, mint=r["mint"], pool=r["pool"], before_sig=r.get("signature"), cap=args.cap)["class"]

    out = run_audit(rows, args.date, classify)
    print(json.dumps({k: out[k] for k in OUTPUT_KEYS}, separators=(",", ":")))
    return 3 if out["n_disagree"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
