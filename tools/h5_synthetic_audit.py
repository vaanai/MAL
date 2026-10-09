#!/usr/bin/env python3
"""Daily synthetic-class audit of the H5 executor's buys (DEC-024 Amendment 2, Clarification 1).

The shadow classified each pool at decision time and the executor bought only on `synthetic: false`. This tool re-classifies every
pool the executor made a buy decision on, with the read-time procedure of EXP-024 Amendment 4 (B4, `tools/synthetic_class.py`), and
counts the pools where the shadow's class and B4's class disagree. A disagreement on any pool is a live halt (DEC-024 5.6).

INPUT, one of
  --decisions FILE|-   a JSONL already projected to {pool, mint, synthetic, synthetic_src, signature}. Any other key is dropped on load.
                       --prev-decisions FILE is the same for the previous UTC day (the re-audit below).
  --ledger FILE|-      the executor ledger (`h5-ledger.jsonl`), projected here. Per line: parse, keep only the five keys above, plus
                       two selectors that are read and thrown away: `kind` (only `kind == "decision"` rows are audited) and `ts_ms`
                       (only rows whose UTC day is --date, or the day before it for the re-audit). No other field of a row is kept,
                       printed or passed on: not a fill, size, exit, price or P&L field, not the nested `plan`, `anchor` or `sent`
                       objects. The JSON parser's object hook drops every other key as soon as its object closes. The file is opened
                       with O_NOFOLLOW; `-` reads stdin.
  --out PATH           with --ledger: write the projected rows of --date (the five keys, mode 0600, O_NOFOLLOW) to PATH and stop;
                       --out-prev PATH2 writes the previous UTC day's rows the same way. Nothing about a row goes to stdout. This is
                       the step that takes the `sudo dd` stream of the root-owned ledger (`--ledger -`), as the manager's own user:
                       the projection is written once, to files the audit then reads (`--decisions`, `--prev-decisions`).
                       Without --out, --ledger audits both days straight from the stream.

FAIL CLOSED ON THE READ. `--expect-ledger` (on by default; `--no-expect-ledger` turns it off): zero non-blank input lines is not an
all-clear. It prints {"date", "error": "no_ledger_lines"} and exits 4. A read that fails (missing file, a symlink, a permission error, a
bad byte) prints {"date", "error": "ledger_read_failed"} and exits 4 whatever the flag says. `n_lines_read` goes to stderr on every run.
Use --no-expect-ledger only on the second step of the runbook, where an empty projected file is a day with no buys.

FAIL CLOSED ON UNCLASSIFIED POOLS. A pool B4 cannot classify today is counted in n_unclassified_now, and any n_unclassified_now > 0 exits 5
(an alert, not a halt). Every run also RE-AUDITS THE PREVIOUS UTC DAY (--date minus one): a pool of that day that is STILL unclassified now
counts as a disagreement (a halt, exit 3), and so does any other disagreement found on that second look. Those pools are in n_pools and
n_disagree, never in n_unclassified_now. The output keys stay the five.

`signature` on a decision row is the signature of OUR buy transaction, not of the trigger print (the ledger carries no trigger
signature). It is used as B4's `before` anchor. B4 says "before the s0 print's signature"; our buy is after s0, so this window is a
SUPERSET of B4's: the migrate tx is older than both, so it is still found, but a pool with more than `cap` (1,000) signatures between
its migrate tx and our buy fails the pool-address search here where B4 would have found it. A buy that never landed has a signature the
node does not know (the public RPC answers -32020 "Transaction ... not found" to such a `before`). For both, the audit turns on
`classify_pool(audit_fallback=True)`, which is NOT part of B4: when the pool-address search fails it searches the bonding-curve PDA
newest-first with no `before` (up to the cap) for the newest successful tx that carries the CreatePoolEvent for the pool and the
CompletePumpAmmMigrationEvent for the mint, then goes on as B4. A pool that still cannot be classified has its reasons on stderr.

OUTPUT (one JSON line on stdout, exactly these keys, and nothing else on stdout):
  {"date", "n_pools", "n_disagree", "n_unclassified_now", "halt": n_disagree > 0}
n_pools counts the pools of --date and of the re-audited day. No pool, mint, signature, per-pool class or fill is printed anywhere. stderr
carries only counts (`n_lines_read`, the rows kept, the unclassified pools by reason) and error type names, never an id or an error message.

A pool "disagrees" when
  - B4 classifies it (synthetic or non_synthetic) and the shadow's class is not the same. The shadow's class is `true` -> synthetic,
    `false` -> non_synthetic; an absent, null or non-bool field is no class, so a bought pool with no recorded class disagrees with any
    B4 class (the executor must have refused it; this is the case on every ledger from before the #519 gate, by design); or
  - B4 could not classify it but saw the PostCompleteBuyEvent in ANY readable located transaction (`event_seen_any`), even though the
    other transaction is unreadable, and the shadow did not call it synthetic: it called it plain (`synthetic` false) or recorded no
    class (DEC-024 Am.2 C1); or
  - it is a pool of the previous UTC day that is still unclassified (the second look above).
A pool of --date that B4 cannot classify now is also counted in n_unclassified_now, whether or not it is a disagreement.

Budget: public RPC only; at most `--max-calls-per-pool` (300) calls per pool, `--max-calls` (3,000) in all, and at least
`--min-interval` (0.2 s, default 0.5) between calls. A pool that runs out of calls is `unclassified` (reason `...:PoolCallBudgetExceeded`).

Exit code 3 if n_disagree > 0 (checked first), else 5 if n_unclassified_now > 0, 4 on a failed or empty ledger read, 2 on a usage error
or a refused RPC URL, else 0. It does not call the monitor: it imports its helpers.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlparse

from tools import pump_structure_monitor as M
from tools import synthetic_class as sc

KEEP_KEYS = ("pool", "mint", "synthetic", "synthetic_src", "signature")  # the only fields ever kept
SELECTOR_KEYS = ("kind", "ts_ms")  # read to choose rows, then discarded
OUTPUT_KEYS = ("date", "n_pools", "n_disagree", "n_unclassified_now", "halt")
_READ = frozenset(KEEP_KEYS) | frozenset(SELECTOR_KEYS)

DEFAULT_RPC_HOST = "api.mainnet-beta.solana.com"
MIN_INTERVAL_FLOOR = 0.2
MAX_CALLS_PER_POOL_DEFAULT = 300
EXIT_HALT, EXIT_READ, EXIT_UNCLASSIFIED = 3, 4, 5


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


def project_days(lines: Iterable[str], days: Sequence[str]) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """({day: projected decision rows of that UTC day}, count of unparseable lines). Nothing but the five KEEP_KEYS survives."""
    out: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    bad = 0
    for line in lines:
        row = _parse(line)
        if row is None:
            continue
        if row.get("__bad__"):
            bad += 1
            continue
        if row.get("kind") == "decision":
            day = _utc_day(row.get("ts_ms"))
            if day in out:
                out[day].append(_project(row))
        del row
    return out, bad


def project_ledger(lines: Iterable[str], date: str) -> tuple[list[dict[str, Any]], int]:
    days, bad = project_days(lines, (date,))
    return days[date], bad


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


def run_audit(rows: Sequence[Mapping[str, Any]], date: str, classify: Callable[[Mapping[str, Any]], Mapping[str, Any]],
              reasons: Counter | None = None, *, second_run: bool = False) -> dict[str, Any]:
    """`classify(row)` returns classify_pool's dict (`class`, `reason`, `event_seen_any`). Rows are grouped by pool; a row with no pool or
    mint cannot be classified. `reasons` (if given) counts the reasons of the unclassified pools: no ids.
    `second_run`: the re-audit of the previous day. A pool still unclassified is a disagreement (and not an n_unclassified_now)."""
    pools: dict[str, list[Mapping[str, Any]]] = {}
    for i, r in enumerate(rows):
        pools.setdefault(r.get("pool") or f"?row{i}", []).append(r)
    n_disagree = n_unclassified = 0
    for rs in pools.values():
        first = rs[0]
        res: Mapping[str, Any] = {"class": sc.CLASS_UNCLASSIFIED, "reason": "no_pool_or_mint", "event_seen_any": False}
        if first.get("pool") and first.get("mint"):
            try:
                res = classify(first)
            except Exception as exc:  # noqa: BLE001  (a lookup that blows up is an unclassified pool, not a crash; the type goes to stderr)
                res = {"class": sc.CLASS_UNCLASSIFIED, "reason": f"classify_error:{type(exc).__name__}", "event_seen_any": False}
        cls = res.get("class")
        if cls in (sc.CLASS_SYNTHETIC, sc.CLASS_NON_SYNTHETIC):
            if {shadow_class(r.get("synthetic")) for r in rs} != {cls}:
                n_disagree += 1
            continue
        if reasons is not None:
            reasons[("reaudit:" if second_run else "") + str(res.get("reason") or "unknown")] += 1
        if second_run:
            n_disagree += 1  # still unclassified on the second look
            continue
        n_unclassified += 1
        if res.get("event_seen_any") is True and {shadow_class(r.get("synthetic")) for r in rs} != {sc.CLASS_SYNTHETIC}:
            n_disagree += 1  # B4 saw the event in a readable located tx (the other tx is unreadable) and the shadow did not say synthetic: plain, or no class
    return {"date": date, "n_pools": len(pools), "n_disagree": n_disagree, "n_unclassified_now": n_unclassified, "halt": n_disagree > 0}


# ---- RPC guard and budget --------------------------------------------------------------------------------------------


def check_public_rpc(url: str, allow_hosts: Sequence[str] = ()) -> str | None:
    """None if `url` may be used. Allowlist: https://api.mainnet-beta.solana.com, plus any host named by --allow-rpc-host. Never a keyed URL."""
    p = urlparse(url)
    if "helius" in url.lower() or p.query or p.username or p.password or p.params or p.fragment:
        return "refused: public RPC only (no Helius, no keyed URL)"
    if p.scheme != "https" or not p.hostname:
        return "refused: https only"
    allowed = {DEFAULT_RPC_HOST, *(h.lower() for h in allow_hosts)}
    if p.hostname.lower() not in allowed:
        return "refused: host not on the allowlist (name a public host with --allow-rpc-host)"
    if any("helius" in h.lower() for h in allow_hosts):
        return "refused: public RPC only (no Helius, no keyed URL)"
    return None


class PoolCallBudgetExceeded(M.CallBudgetExceeded):
    """One pool used its per-pool call allowance."""


class PoolBudget:
    """Wraps an RPC client: at most `limit` calls (logical calls; the client's own retries are not counted) between two `reset()`s."""

    def __init__(self, rpc: Any, limit: int):
        self.rpc, self.limit, self.used = rpc, limit, 0

    def reset(self) -> None:
        self.used = 0

    def call(self, method: str, params: Sequence[Any]) -> Any:
        if self.used >= self.limit:
            raise PoolCallBudgetExceeded(f"per-pool call cap {self.limit} reached before {method}")
        self.used += 1
        return self.rpc.call(method, params)


# ---- input and output files --------------------------------------------------------------------------------------------


class _Counted:
    """Iterates lines and counts the non-blank ones."""

    def __init__(self, it: Iterable[str]):
        self.it, self.n = it, 0

    def __iter__(self) -> Iterator[str]:
        for line in self.it:
            if line.strip():
                self.n += 1
            yield line


def _read_lines(path: str) -> Iterator[str]:
    if path == "-":
        yield from sys.stdin
        return
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        yield from fh


def write_rows(path: str, rows: Iterable[Mapping[str, Any]]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({k: r.get(k) for k in KEEP_KEYS}, separators=(",", ":")) + "\n")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="DEC-024 Am.2 C1 daily audit: shadow class vs EXP-024 Am.4 B4 (counts only).")
    p.add_argument("--date", required=True, help="UTC day audited, YYYY-MM-DD (filters ledger rows by ts_ms; a label for --decisions)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--decisions", metavar="FILE|-", help="JSONL projected to pool, mint, synthetic, synthetic_src, signature")
    g.add_argument("--ledger", metavar="FILE|-", help="executor ledger JSONL; projected here to those five keys")
    p.add_argument("--prev-decisions", metavar="FILE", help="with --decisions: the previous UTC day's projected rows (the re-audit)")
    p.add_argument("--out", metavar="PATH", help="with --ledger: write the projected rows of --date to PATH (0600) and stop; prints nothing about rows")
    p.add_argument("--out-prev", metavar="PATH", help="with --out: also write the previous UTC day's projected rows to PATH (0600)")
    p.add_argument("--expect-ledger", action=argparse.BooleanOptionalAction, default=True,
                   help="zero input lines is an error (exit 4), not an all-clear (default on)")
    p.add_argument("--rpc-url", default=M.DEFAULT_RPC, help="public mainnet RPC (default %(default)s)")
    p.add_argument("--allow-rpc-host", action="append", default=[], metavar="HOST", help="another public RPC host to allow (repeatable)")
    p.add_argument("--min-interval", type=float, default=0.5, help=f"seconds between RPC calls (not below {MIN_INTERVAL_FLOOR})")
    p.add_argument("--max-calls", type=int, default=3000, help="hard cap on RPC calls in all, retries included")
    p.add_argument("--max-calls-per-pool", type=int, default=MAX_CALLS_PER_POOL_DEFAULT, help="calls one pool may use")
    p.add_argument("--cap", type=int, default=sc.CAP_DEFAULT, help="signatures per search (B4: 1000)")
    return p.parse_args(argv)


def _emit_error(date: str, name: str) -> int:
    print(json.dumps({"date": date, "error": name}, separators=(",", ":")))
    return EXIT_READ


def main(argv: Sequence[str] | None = None, *, rpc: Any = None) -> int:
    args = parse_args(argv)
    try:
        day = datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print("usage: --date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    prev = (day - timedelta(days=1)).strftime("%Y-%m-%d")
    if (args.out and (not args.ledger or args.out == "-")) or (args.out_prev and (not args.out or args.out_prev == "-")) or (args.prev_decisions and not args.decisions):
        print("usage: --out needs --ledger and a file path; --out-prev needs --out; --prev-decisions needs --decisions", file=sys.stderr)
        return 2
    if args.min_interval < MIN_INTERVAL_FLOOR or args.max_calls_per_pool < 1 or args.max_calls < 1 or args.cap < 1:
        print(f"usage: --min-interval must be at least {MIN_INTERVAL_FLOOR}; the caps must be positive", file=sys.stderr)
        return 2
    if rpc is None and not args.out:
        refusal = check_public_rpc(args.rpc_url, args.allow_rpc_host)
        if refusal:
            print(refusal, file=sys.stderr)
            return 2
    src = _Counted(_read_lines(args.ledger or args.decisions))
    prev_rows: list[dict[str, Any]] = []
    try:
        if args.ledger:
            days, bad = project_days(src, (args.date, prev))
            rows, prev_rows = days[args.date], days[prev]
        else:
            rows, bad = load_projected(src)
            if args.prev_decisions:
                prev_rows, bad_prev = load_projected(_Counted(_read_lines(args.prev_decisions)))
                bad += bad_prev
    except (OSError, UnicodeError) as exc:
        print(f"n_lines_read={src.n} read failed: {type(exc).__name__}", file=sys.stderr)
        return _emit_error(args.date, "ledger_read_failed")
    print(f"n_lines_read={src.n} n_rows={len(rows)} n_prev_rows={len(prev_rows)}" + (f" n_unparseable={bad}" if bad else ""), file=sys.stderr)
    if args.expect_ledger and src.n == 0:
        return _emit_error(args.date, "no_ledger_lines")
    if args.out:
        try:
            write_rows(args.out, rows)
            if args.out_prev:
                write_rows(args.out_prev, prev_rows)
        except OSError as exc:
            print(f"write failed: {type(exc).__name__}", file=sys.stderr)
            return _emit_error(args.date, "projection_write_failed")
        return 0
    if rpc is None:
        rpc = M.RpcClient(args.rpc_url, min_interval=args.min_interval, max_calls=args.max_calls)
    budget = PoolBudget(rpc, args.max_calls_per_pool)

    def classify(r: Mapping[str, Any]) -> Mapping[str, Any]:
        budget.reset()
        return sc.classify_pool(budget, mint=r["mint"], pool=r["pool"], before_sig=r.get("signature"), cap=args.cap, audit_fallback=True)

    reasons: Counter = Counter()
    today = run_audit(rows, args.date, classify, reasons)
    second = run_audit(prev_rows, prev, classify, reasons, second_run=True)
    n_disagree = today["n_disagree"] + second["n_disagree"]
    out = {"date": args.date, "n_pools": today["n_pools"] + second["n_pools"], "n_disagree": n_disagree,
           "n_unclassified_now": today["n_unclassified_now"], "halt": n_disagree > 0}
    if reasons:
        print("unclassified_by_reason=" + json.dumps(dict(sorted(reasons.items())), separators=(",", ":")), file=sys.stderr)
    print(json.dumps({k: out[k] for k in OUTPUT_KEYS}, separators=(",", ":")))
    if n_disagree > 0:
        return EXIT_HALT
    return EXIT_UNCLASSIFIED if out["n_unclassified_now"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
