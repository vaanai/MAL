#!/usr/bin/env python3
"""getBlock tip follower: the complete-by-construction feed for the fast-0 paper runner.

DEC-015 section 2.2 (owner decision 2026-10-04). Every confirmed slot from the last done
slot + 1 up to the tip is fetched, in order, with Helius getBlock. Rows are decoded with
the walker's own code (tools/pump_history_backfill.py) so they are schema-identical to
walker rows, plus one filled-in field: t_recv_ms, the local wall clock in ms when the block
response arrived. Nothing is skipped silently:

  * a slot with no block (-32007 / -32009) is written to skipped-slots-<hour>.jsonl;
  * a slot still unfetchable after --max-retries is written to gaps.jsonl and counted;
  * both counters are in the status json.

Output (--out): trades-<YYYY-MM-DDTHH>.jsonl, creates-<hour>.jsonl, migrations-<hour>.jsonl,
bucketed by the UTC hour of t_recv_ms. t_recv_ms is kept non-decreasing across the stream.
Each block's rows are flushed before the checkpoint moves on. A restart truncates any
partial write of the in-flight slot (byte offsets recorded before the write), so a restart
leaves no hole and no duplicate.

The key is read from HELIUS_API_KEY in the process environment (systemd EnvironmentFile=).
URLs are never logged; every message goes through redact_rpc_url. Paper only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools.pump_history_backfill import (
    CreditBudget,
    RateLimiter,
    fetch_pool_mints,
    helius_http_url,
    redact_rpc_url,
    resolve_rpc_url,
    resolve_unresolved,
    rows_from_block,
    rpc_call,
)

DEFAULT_OUT = "/var/lib/mal/sealed/fast-trades-tip"
DEFAULT_STATE = "/var/lib/mal/fast-tip-follower"
FEED = "helius_getblock_tip"
SKIP_CODES = frozenset({-32007, -32009})
TRANSIENT_NO_BLOCK = -32004  # "block not available yet": a lagging node, retry
HEARTBEAT_S = 15.0
KINDS = ("trades", "creates", "migrations")
_FILE_RE = re.compile(r"^(trades|creates|migrations|skipped-slots)-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl$")

# Fetcher contract: slot -> (block | None, code). block None with a code in SKIP_CODES is a
# skipped slot. Anything else that is not a block raises TransientError.
Fetcher = Callable[[int], "tuple[dict[str, Any] | None, int | None]"]


class TransientError(RuntimeError):
    pass


def hour_of_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class HourlyWriter:
    """Append-only hourly jsonl files, one open handle per (kind, hour), flushed per write."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self._fh: dict[tuple[str, str], Any] = {}

    def path(self, kind: str, hour: str) -> Path:
        return self.out_dir / f"{kind}-{hour}.jsonl"

    def size(self, kind: str, hour: str) -> int:
        try:
            return self.path(kind, hour).stat().st_size
        except OSError:
            return 0

    def append(self, kind: str, hour: str, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        key = (kind, hour)
        fh = self._fh.get(key)
        if fh is None:
            fh = open(self.path(kind, hour), "ab")
            self._fh[key] = fh
        fh.write(b"".join(json.dumps(r, separators=(",", ":")).encode() + b"\n" for r in rows))
        fh.flush()

    def close_before(self, hour: str) -> None:
        for key in [k for k in self._fh if k[1] < hour]:
            self._fh.pop(key).close()

    def close(self) -> None:
        for fh in self._fh.values():
            fh.close()
        self._fh.clear()


class TipFollower:
    def __init__(
        self,
        *,
        out_dir: Path,
        state_dir: Path,
        fetch: Fetcher,
        get_tip: Callable[[], int],
        lookup: Callable[[list[str]], dict[str, tuple[str, str]]] | None = None,
        credits: Callable[[], int] = lambda: 0,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 8,
        backoff_s: float = 0.5,
        backoff_cap_s: float = 10.0,
        skip_rechecks: int = 1,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.out_dir = out_dir
        self.state_dir = state_dir
        self.fetch = fetch
        self.get_tip = get_tip
        self.lookup = lookup or (lambda pools: {})
        self.credits = credits
        self.clock_ms = clock_ms
        self.sleep = sleep
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.backoff_cap_s = backoff_cap_s
        self.skip_rechecks = skip_rechecks
        self._log = log or (lambda m: print(redact_rpc_url(m), file=sys.stderr, flush=True))
        out_dir.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        self.ckpt_path = state_dir / "checkpoint.json"
        self.status_path = state_dir / "status.json"
        self.gaps_path = out_dir / "gaps.jsonl"
        self.writer = HourlyWriter(out_dir)
        self.pool_cache: dict[str, tuple[str, str]] = {}
        self.last_done: int | None = None
        self.last_t_recv_ms = 0
        self.last_block_lag_ms: int | None = None
        self.tip: int | None = None
        self.retries = 0
        self.gaps = 0
        self.skipped = 0
        self.unresolved_dropped = 0
        self.blocks = 0
        self.hour_counts: dict[str, int] = {}
        self._hour: str | None = None
        self._last_beat = 0.0
        self._recover()

    # ------------------------------------------------------------ checkpoint
    def _recover(self) -> None:
        if not self.ckpt_path.is_file():
            return
        try:
            ck = json.loads(self.ckpt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._log("checkpoint unreadable; refusing to guess")
            raise
        self.last_done = int(ck["last_done"])
        self.last_t_recv_ms = int(ck.get("last_t_recv_ms") or 0)
        self.gaps = int(ck.get("gaps") or 0)
        self.skipped = int(ck.get("skipped") or 0)
        pending = ck.get("writing")
        if isinstance(pending, dict):
            # crashed mid-write of this slot: cut every file back to its pre-write size
            for name, size in (pending.get("offsets") or {}).items():
                path = self.out_dir / name
                if path.is_file() and path.stat().st_size > int(size):
                    with open(path, "r+b") as fh:
                        fh.truncate(int(size))
            self._log(f"recovered: truncated partial write of slot {pending.get('slot')}")

    def _checkpoint(self, writing: dict[str, Any] | None = None) -> None:
        _atomic_json(
            self.ckpt_path,
            {
                "last_done": self.last_done,
                "last_t_recv_ms": self.last_t_recv_ms,
                "gaps": self.gaps,
                "skipped": self.skipped,
                "writing": writing,
            },
        )

    # ------------------------------------------------------------ fetch
    def _fetch_with_retry(self, slot: int) -> tuple[dict[str, Any] | None, str]:
        """Return (block, outcome). outcome is ok, skipped or gap."""
        rechecks = 0
        attempt = 0
        while True:
            try:
                block, code = self.fetch(slot)
            except TransientError as exc:
                attempt += 1
                self.retries += 1
                if attempt > self.max_retries:
                    self._log(f"slot {slot} unfetchable after {self.max_retries} retries: {type(exc).__name__}")
                    return None, "gap"
                self.sleep(min(self.backoff_cap_s, self.backoff_s * (2 ** (attempt - 1))))
                continue
            if block is not None:
                return block, "ok"
            if code in SKIP_CODES:
                if rechecks < self.skip_rechecks:
                    rechecks += 1
                    self.sleep(self.backoff_s)
                    continue
                return None, "skipped"
            # no block and no skip code: treat as transient
            attempt += 1
            self.retries += 1
            if attempt > self.max_retries:
                return None, "gap"
            self.sleep(min(self.backoff_cap_s, self.backoff_s * (2 ** (attempt - 1))))

    def _note_event(self, slot: int, kind: str, t_ms: int, code: int | None = None) -> None:
        row = {"slot": slot, "kind": kind, "t_recv_ms": t_ms, "code": code}
        if kind == "skipped":
            self.writer.append("skipped-slots", hour_of_ms(t_ms), [row])
        else:
            with open(self.gaps_path, "ab") as fh:
                fh.write(json.dumps(row, separators=(",", ":")).encode() + b"\n")

    def process_slot(self, slot: int) -> None:
        block, outcome = self._fetch_with_retry(slot)
        t_ms = max(self.clock_ms(), self.last_t_recv_ms)  # non-decreasing in the files
        hour = hour_of_ms(t_ms)
        rows: dict[str, list[dict[str, Any]]] = {k: [] for k in KINDS}
        if outcome == "ok" and block is not None:
            block["slot"] = slot
            decoded = rows_from_block(block, self.pool_cache, FEED)
            pending = decoded.get("unresolved") or []
            bt = block.get("blockTime")
            ready: list[dict[str, Any]] = []
            if pending and isinstance(bt, int):
                ready, dropped = resolve_unresolved(pending, self.pool_cache, bt, self._safe_lookup)
                self.unresolved_dropped += dropped
            for kind in KINDS:
                rows[kind] = list(decoded.get(kind) or [])
            rows["trades"].extend(ready)
            for kind in KINDS:
                for r in rows[kind]:
                    r["t_recv_ms"] = t_ms
                rows[kind].sort(key=lambda r: (r.get("tx_index", 0), r.get("event_index", 0)))
            if isinstance(bt, int):
                self.last_block_lag_ms = t_ms - bt * 1000
            self.blocks += 1
        elif outcome == "skipped":
            self.skipped += 1
        else:
            self.gaps += 1
        # intent first (pre-write sizes), then rows, then the checkpoint moves on
        names = {f"{k}-{hour}.jsonl": self.writer.size(k, hour) for k in KINDS if rows[k]}
        if outcome == "skipped":
            names[f"skipped-slots-{hour}.jsonl"] = self.writer.size("skipped-slots", hour)
        elif outcome == "gap":
            names[self.gaps_path.name] = self.gaps_path.stat().st_size if self.gaps_path.exists() else 0
        self._checkpoint({"slot": slot, "offsets": names} if names else None)
        for kind in KINDS:
            self.writer.append(kind, hour, rows[kind])
        if outcome == "skipped":
            self._note_event(slot, "skipped", t_ms)
        elif outcome == "gap":
            self._note_event(slot, "gap", t_ms)
        self.last_t_recv_ms = t_ms
        self.last_done = slot
        self._checkpoint(None)
        self._count_hour(hour, rows, outcome)

    def _safe_lookup(self, pools: list[str]) -> dict[str, tuple[str, str]]:
        try:
            return self.lookup(pools)
        except Exception as exc:  # network: counted as unresolved_dropped by the caller
            self._log(f"pool lookup failed: {type(exc).__name__}")
            return {}

    # ------------------------------------------------------------ bookkeeping
    def _count_hour(self, hour: str, rows: Mapping[str, list], outcome: str) -> None:
        if self._hour is not None and hour != self._hour:
            self._log(
                f"hour {self._hour} counts: "
                + " ".join(f"{k}={v}" for k, v in sorted(self.hour_counts.items()))
            )
            self.hour_counts = {}
            self.writer.close_before(hour)
        self._hour = hour
        c = self.hour_counts
        c["slots"] = c.get("slots", 0) + 1
        if outcome != "ok":
            c[outcome] = c.get(outcome, 0) + 1
        for k in KINDS:
            c[k] = c.get(k, 0) + len(rows[k])

    def status(self) -> dict[str, Any]:
        tip, done = self.tip, self.last_done
        return {
            "ts_ms": self.clock_ms(),
            "tip_slot": tip,
            "last_done_slot": done,
            "lag_slots": (tip - done) if tip is not None and done is not None else None,
            "lag_ms": self.last_block_lag_ms,
            "retries": self.retries,
            "gaps": self.gaps,
            "skipped": self.skipped,
            "unresolved_dropped": self.unresolved_dropped,
            "blocks": self.blocks,
            "credits_used": self.credits(),
        }

    def heartbeat(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_beat >= HEARTBEAT_S:
            self._last_beat = now
            _atomic_json(self.status_path, self.status())

    def retain(self, max_keep_days: int) -> int:
        cutoff = hour_of_ms(self.clock_ms() - max_keep_days * 86_400_000)
        removed = 0
        for p in self.out_dir.iterdir():
            m = _FILE_RE.match(p.name)
            if m and m.group(2) < cutoff:
                p.unlink()
                removed += 1
        return removed

    # ------------------------------------------------------------ loop
    def step(self) -> int:
        """Fetch every slot from last_done + 1 to the tip. Returns slots processed."""
        tip = self.get_tip()
        self.tip = tip
        if self.last_done is None:
            self.last_done = tip - 1  # no checkpoint: begin at the current tip
            self._checkpoint(None)
        n = 0
        for slot in range(self.last_done + 1, tip + 1):
            self.process_slot(slot)
            n += 1
            self.heartbeat()
        return n

    def run(self, stop: threading.Event, max_keep_days: int = 2, idle_s: float = 0.4) -> None:
        last_retain = 0.0
        while not stop.is_set():
            try:
                n = self.step()
            except TransientError as exc:
                self.retries += 1
                self._log(f"tip poll failed: {type(exc).__name__}")
                n = 0
            self.heartbeat()
            if time.monotonic() - last_retain > 3600:
                last_retain = time.monotonic()
                self._log(f"retention removed {self.retain(max_keep_days)} files")
            if n == 0:
                stop.wait(idle_s)
        self.writer.close()
        self.heartbeat(force=True)


# ---------------------------------------------------------------- real RPC glue
def make_rpc(url: str, rps: float) -> tuple[Fetcher, Callable[[], int], Callable[[], int], Callable[[list[str]], dict]]:
    limiter = RateLimiter(rps)
    budget = CreditBudget(cap=10**12, per_call=1)

    def get_tip() -> int:
        try:
            result, _w, _c = rpc_call(url, "getSlot", [{"commitment": "confirmed"}], limiter, timeout=15.0, budget=budget)
        except RuntimeError as exc:
            raise TransientError(redact_rpc_url(str(exc))) from None
        if not isinstance(result, int):
            raise TransientError("getSlot returned no slot")
        return result

    def fetch(slot: int) -> tuple[dict[str, Any] | None, int | None]:
        params = [
            slot,
            {
                "encoding": "json",
                "transactionDetails": "full",
                "rewards": False,
                "commitment": "confirmed",
                "maxSupportedTransactionVersion": 0,
            },
        ]
        try:
            result, _w, code = rpc_call(url, "getBlock", params, limiter, timeout=60.0, budget=budget)
        except RuntimeError as exc:
            raise TransientError(redact_rpc_url(str(exc))) from None
        if isinstance(result, dict):
            return result, None
        if code in SKIP_CODES:
            return None, code
        raise TransientError(f"getBlock no block code={code}")

    return fetch, get_tip, (lambda: budget.used), (lambda pools: fetch_pool_mints(url, pools))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--state-dir", default=DEFAULT_STATE)
    ap.add_argument("--rps", type=float, default=5.0)
    ap.add_argument("--max-keep-days", type=int, default=2)
    ap.add_argument("--max-retries", type=int, default=8)
    args = ap.parse_args(argv)
    if args.rps <= 0:
        ap.error("--rps must be > 0")
    url, kind = resolve_rpc_url(None, os.environ)
    if kind != "helius":
        print("fast_tip_follower: HELIUS_API_KEY is not set; refusing the public RPC", file=sys.stderr)
        return 2
    fetch, get_tip, credits, lookup = make_rpc(url, args.rps)
    follower = TipFollower(
        out_dir=Path(args.out),
        state_dir=Path(args.state_dir),
        fetch=fetch,
        get_tip=get_tip,
        lookup=lookup,
        credits=credits,
        max_retries=args.max_retries,
    )
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    print(f"fast_tip_follower: start out={args.out} rps={args.rps} resume={follower.last_done}", flush=True)
    follower.run(stop, max_keep_days=args.max_keep_days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
