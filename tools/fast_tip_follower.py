#!/usr/bin/env python3
"""getBlock tip follower: the complete-by-construction feed for the fast-0 paper runner.

DEC-015 section 2.2 (owner decision 2026-10-04). Every confirmed slot from the last done
slot + 1 up to the tip is fetched, in order, with Helius getBlock. Rows are decoded with
the walker's own code (tools/pump_history_backfill.py), so they match walker rows, with two
differences: t_recv_ms is the local wall clock in ms when the block response arrived, and
source is "tip" (not "backfill"). Nothing is skipped silently:

  * a slot with no block (-32007 / -32009) is written to skipped-slots-<hour>.jsonl, and is
    checked against the next fetched block's parentSlot; a contradicted skip becomes a gap
    and the slot is fetched again;
  * a slot still unfetchable after --max-retries is written to gaps.jsonl and counted;
  * after an outage the follower never stamps old blocks with a fresh t_recv_ms: if it is
    more than --backlog-slots behind the tip it jumps to the tip and records the whole range
    in gaps.jsonl with reason "backlog_jump";
  * every counter is in the status json.

Output (--out): trades-<YYYY-MM-DDTHH>.jsonl, creates-<hour>.jsonl, migrations-<hour>.jsonl,
bucketed by the UTC hour of t_recv_ms. Creates are also written in the observe unit's format
to --creates-out as observe-<UTC day>.jsonl, so the runner switches by config only. The next
hour's (and day's) files are created empty shortly before the boundary, because the runner's
DirectoryTail opens a new file at its end and would otherwise lose the first block.
t_recv_ms is kept non-decreasing. Each block's rows are flushed before the checkpoint moves
on; a restart truncates a partial write of the in-flight slot, so there is no hole and no
duplicate.

The key is read from HELIUS_API_KEY in the process environment (systemd EnvironmentFile=).
URLs are never logged; every message goes through redact_rpc_url. Paper only.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import signal
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from observe.trade_decode import decode_pool_account
from tools.pump_history_backfill import (
    CreditBudget,
    RateLimiter,
    redact_rpc_url,
    resolve_rpc_url,
    resolve_unresolved,
    rows_from_block,
    rpc_call,
)

DEFAULT_OUT = "/var/lib/mal/sealed/fast-trades-tip"
DEFAULT_CREATES = "/var/lib/mal/sealed/fast-creates-tip"
DEFAULT_STATE = "/var/lib/mal/fast-tip-follower"
FEED = "helius_getblock_tip"
TIP_SOURCE = "tip"
SKIP_CODES = frozenset({-32007, -32009})
HEARTBEAT_S = 15.0
PRECREATE_LEAD_S = 5  # create next hour's files at about :59:55
BATCH_SLOTS = 20  # slots per tip poll, so the backlog check is re-run often
KINDS = ("trades", "creates", "migrations")
_FILE_RE = re.compile(r"^(trades|creates|migrations|skipped-slots)-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl$")
_OBS_RE = re.compile(r"^observe-(\d{4}-\d{2}-\d{2})\.jsonl$")

# Fetcher contract: slot -> (block | None, code). block None with a code in SKIP_CODES is a
# skipped slot. Anything else that is not a block raises TransientError.
Fetcher = Callable[[int], "tuple[dict[str, Any] | None, int | None]"]


class TransientError(RuntimeError):
    def __init__(self, msg: str = "", rate_limited: bool = False) -> None:
        super().__init__(msg)
        self.rate_limited = rate_limited or "429" in msg


class LRUPools(OrderedDict):
    """Pool cache with a size cap. Drop-in for the walker's dict[str, (base, quote)]."""

    def __init__(self, cap: int = 200_000) -> None:
        super().__init__()
        self.cap = cap

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.cap:
            self.popitem(last=False)

    def get(self, key, default=None):
        if key in self:
            self.move_to_end(key)
            return super().__getitem__(key)
        return default


def hour_of_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def day_of_ms(ms: int) -> str:
    return hour_of_ms(ms)[:10]


def t_ws_of_ms(ms: int) -> str:
    """Same format as the observe unit's t_ws (isoformat, milliseconds, +00:00)."""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat(timespec="milliseconds")


def observe_create_row(create: Mapping[str, Any], t_recv_ms: int) -> dict[str, Any]:
    """Observe-format create row. No event_ts, block_time, initialBuy or solAmount: the
    runner's create-time path must stay what the md5 replay proof covered."""
    row: dict[str, Any] = {
        "stream": "subscribeNewToken",
        "txType": "create",
        "source": TIP_SOURCE,
        "mint": create.get("mint"),
        "t_ws": t_ws_of_ms(t_recv_ms),
        "traderPublicKey": create.get("creator"),
        "signature": create.get("signature"),
        "slot": create.get("slot"),
    }
    qr, br = create.get("quote_reserve"), create.get("base_reserve")
    if isinstance(qr, int):
        row["vSolInBondingCurve"] = qr / 1e9
    if isinstance(br, int):
        row["vTokensInBondingCurve"] = br / 1e6
    return row


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class HourlyWriter:
    """Append-only jsonl files, one open handle per (dir, name), flushed per write."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self._fh: dict[Path, Any] = {}

    def path(self, kind: str, hour: str) -> Path:
        return self.out_dir / f"{kind}-{hour}.jsonl"

    def size_of(self, path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def size(self, kind: str, hour: str) -> int:
        return self.size_of(self.path(kind, hour))

    def append_to(self, path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        fh = self._fh.get(path)
        if fh is None:
            fh = open(path, "ab")
            self._fh[path] = fh
        fh.write(b"".join(json.dumps(r, separators=(",", ":")).encode() + b"\n" for r in rows))
        fh.flush()

    def append(self, kind: str, hour: str, rows: Sequence[Mapping[str, Any]]) -> None:
        self.append_to(self.path(kind, hour), rows)

    def close_stale(self, keep: set[Path]) -> None:
        for p in [p for p in self._fh if p not in keep]:
            self._fh.pop(p).close()

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
        creates_dir: Path | None = None,
        lookup: Callable[[list[str]], dict[str, tuple[str, str]]] | None = None,
        credits: Callable[[], int] = lambda: 0,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 8,
        backoff_s: float = 0.5,
        backoff_cap_s: float = 10.0,
        skip_rechecks: int = 1,
        backlog_slots: int = 50,
        breaker_after: int = 5,
        breaker_pause_s: float = 60.0,
        pool_cap: int = 200_000,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.out_dir = out_dir
        self.creates_dir = creates_dir or (state_dir / "creates")
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
        self.backlog_slots = backlog_slots
        self.breaker_after = breaker_after
        self.breaker_pause_s = breaker_pause_s
        self._log = log or (lambda m: print(redact_rpc_url(m), file=sys.stderr, flush=True))
        for d in (out_dir, self.creates_dir, state_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.ckpt_path = state_dir / "checkpoint.json"
        self.status_path = state_dir / "status.json"
        self.gaps_path = out_dir / "gaps.jsonl"
        self.writer = HourlyWriter(out_dir)
        self.cwriter = HourlyWriter(self.creates_dir)
        self.pool_cache: LRUPools = LRUPools(pool_cap)
        self.last_done: int | None = None
        self.last_t_recv_ms = 0
        self.last_block_lag_ms: int | None = None
        self.tip: int | None = None
        self.retries = 0
        self.gaps = 0
        self.skipped = 0
        self.unresolved_dropped = 0
        self.lookup_failures = 0
        self.backlog_jumps = 0
        self.breaker_trips = 0
        self.blocks = 0
        self.hour_counts: dict[str, int] = {}
        self._hour: str | None = None
        self._last_beat = 0.0
        self._lookup_failed = False
        self._consec_429 = 0
        self._pending_skips: list[int] = []
        self._log_last: dict[str, float] = {}
        self._precreated: set[Path] = set()
        self._recover()

    # ------------------------------------------------------------ logging
    def _log_limited(self, key: str, msg: str, every_s: float = 30.0) -> None:
        now = time.monotonic()
        if now - self._log_last.get(key, -1e9) >= every_s:
            self._log_last[key] = now
            self._log(msg)

    # ------------------------------------------------------------ checkpoint
    def _recover(self) -> None:
        if not self.ckpt_path.is_file():
            return
        try:
            ck = json.loads(self.ckpt_path.read_text(encoding="utf-8"))
            self.last_done = int(ck["last_done"])
        except (OSError, ValueError, KeyError, TypeError):
            self._log("checkpoint unreadable; refusing to guess")
            raise
        self.last_t_recv_ms = int(ck.get("last_t_recv_ms") or 0)
        self.gaps = int(ck.get("gaps") or 0)
        self.skipped = int(ck.get("skipped") or 0)
        pending = ck.get("writing")
        if isinstance(pending, dict):
            # crashed mid-write of this slot: cut every file back to its pre-write size
            for name, size in (pending.get("offsets") or {}).items():
                for base in (self.out_dir, self.creates_dir):
                    path = base / name
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

    # ------------------------------------------------------------ pre-create
    def precreate(self, now_ms: int | None = None) -> None:
        """Touch this hour's files, and next hour's from :59:55, so the runner's tail
        (which opens a new file at its end) never misses the first block of an hour."""
        now_ms = self.clock_ms() if now_ms is None else now_ms
        targets = [(now_ms, True)]
        into_hour = (now_ms // 1000) % 3600
        if into_hour >= 3600 - PRECREATE_LEAD_S:
            targets.append((now_ms + 1000 * PRECREATE_LEAD_S, False))
        for ms, _cur in targets:
            hour, day = hour_of_ms(ms), day_of_ms(ms)
            for path in (
                self.writer.path("trades", hour),
                self.writer.path("migrations", hour),
                self.cwriter.out_dir / f"observe-{day}.jsonl",
            ):
                if path not in self._precreated:
                    path.touch(exist_ok=True)
                    self._precreated.add(path)
        if len(self._precreated) > 12:
            self._precreated = {p for p in self._precreated if p.exists()}
            self._precreated = set(sorted(self._precreated)[-6:])

    # ------------------------------------------------------------ gaps log
    def _gap_row(self, row: dict[str, Any]) -> None:
        with open(self.gaps_path, "ab") as fh:
            fh.write(json.dumps(row, separators=(",", ":")).encode() + b"\n")

    # ------------------------------------------------------------ fetch
    def _backoff(self, attempt: int) -> None:
        self.sleep(min(self.backoff_cap_s, self.backoff_s * (2 ** (attempt - 1))))

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
                if exc.rate_limited:
                    self._consec_429 += 1
                    if self._consec_429 >= self.breaker_after:
                        self.breaker_trips += 1
                        self._log_limited("breaker", f"circuit breaker: {self._consec_429} rate limits, pausing")
                        self.sleep(self.breaker_pause_s)
                        self._consec_429 = 0
                else:
                    self._consec_429 = 0
                if attempt > self.max_retries:
                    self._log_limited("unfetchable", f"slot {slot} unfetchable after {self.max_retries} retries")
                    return None, "gap"
                self._backoff(attempt)
                continue
            self._consec_429 = 0
            if block is not None:
                return block, "ok"
            if code in SKIP_CODES:
                if rechecks < self.skip_rechecks:
                    rechecks += 1
                    self.sleep(self.backoff_s)
                    continue
                return None, "skipped"
            attempt += 1  # no block and no skip code: transient
            self.retries += 1
            if attempt > self.max_retries:
                return None, "gap"
            self._backoff(attempt)

    def process_slot(self, slot: int) -> None:
        block, outcome = self._fetch_with_retry(slot)
        if outcome == "ok" and block is not None:
            self._verify_skips(block)
        self._emit(slot, block, outcome)
        if outcome == "skipped":
            self._pending_skips.append(slot)
        elif outcome == "ok":
            self._pending_skips.clear()
        self.last_done = slot
        self._checkpoint(None)

    def _verify_skips(self, block: Mapping[str, Any]) -> None:
        """Every slot between two fetched blocks is skipped, so the later block's parentSlot
        is below all of them. A pending skip at or below parentSlot is contradicted: it is a
        gap, and the slot is fetched again (before this block, so slot order holds)."""
        parent = block.get("parentSlot")
        if not isinstance(parent, int) or not self._pending_skips:
            return
        bad = [s for s in self._pending_skips if s <= parent]
        self._pending_skips = []
        for s in bad:
            self.skipped = max(0, self.skipped - 1)
            self.gaps += 1
            self._gap_row({"slot": s, "kind": "gap", "reason": "skip_contradicted",
                           "t_recv_ms": self.clock_ms(), "parentSlot": parent})
            self._log_limited("contradicted", f"slot {s} skip contradicted by parentSlot {parent}; refetching")
            blk, out = self._fetch_with_retry(s)
            if out == "skipped":
                out = "gap"  # still contradicted: never record it as a skip again
                self.gaps += 1
                self._gap_row({"slot": s, "kind": "gap", "reason": "skip_contradicted_refetch",
                               "t_recv_ms": self.clock_ms()})
            self._emit(s, blk, out, count_gap=False)

    def _emit(self, slot: int, block: dict[str, Any] | None, outcome: str, count_gap: bool = True) -> None:
        t_ms = max(self.clock_ms(), self.last_t_recv_ms)  # non-decreasing in the files
        hour, day = hour_of_ms(t_ms), day_of_ms(t_ms)
        rows: dict[str, list[dict[str, Any]]] = {k: [] for k in KINDS}
        obs: list[dict[str, Any]] = []
        self._lookup_failed = False
        dropped = 0
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
                    r["source"] = TIP_SOURCE
                rows[kind].sort(key=lambda r: (r.get("tx_index", 0), r.get("event_index", 0)))
            obs = [observe_create_row(r, t_ms) for r in rows["creates"]]
            if isinstance(bt, int):
                self.last_block_lag_ms = t_ms - bt * 1000
            self.blocks += 1
        elif outcome == "skipped":
            self.skipped += 1
        elif count_gap:
            self.gaps += 1
        self.precreate(t_ms)
        # intent first (pre-write sizes), then rows, then the checkpoint moves on
        offsets = {f"{k}-{hour}.jsonl": self.writer.size(k, hour) for k in KINDS if rows[k]}
        if obs:
            name = f"observe-{day}.jsonl"
            offsets[name] = self.cwriter.size_of(self.creates_dir / name)
        if outcome == "skipped":
            offsets[f"skipped-slots-{hour}.jsonl"] = self.writer.size("skipped-slots", hour)
        elif outcome == "gap":
            offsets[self.gaps_path.name] = self.writer.size_of(self.gaps_path)
        self._checkpoint({"slot": slot, "offsets": offsets} if offsets else None)
        for kind in KINDS:
            self.writer.append(kind, hour, rows[kind])
        self.cwriter.append_to(self.creates_dir / f"observe-{day}.jsonl", obs)
        if outcome == "skipped":
            self.writer.append("skipped-slots", hour, [{"slot": slot, "kind": "skipped", "t_recv_ms": t_ms}])
        elif outcome == "gap":
            self._gap_row({"slot": slot, "kind": "gap", "reason": "unfetchable", "t_recv_ms": t_ms})
        if dropped or self._lookup_failed:
            self._gap_row({"slot": slot, "kind": "unresolved_dropped", "n": dropped,
                           "lookup_failed": self._lookup_failed, "t_recv_ms": t_ms})
        self.last_t_recv_ms = t_ms
        self._count_hour(hour, rows, outcome)

    def _safe_lookup(self, pools: list[str]) -> dict[str, tuple[str, str]]:
        try:
            return self.lookup(pools)
        except Exception as exc:  # network: the caller counts the drops
            self.lookup_failures += 1
            self._lookup_failed = True
            self._log_limited("lookup", f"pool lookup failed: {type(exc).__name__}")
            return {}

    # ------------------------------------------------------------ bookkeeping
    def _count_hour(self, hour: str, rows: Mapping[str, list], outcome: str) -> None:
        if self._hour is not None and hour != self._hour:
            self._log(
                f"hour {self._hour} counts: "
                + " ".join(f"{k}={v}" for k, v in sorted(self.hour_counts.items()))
            )
            self.hour_counts = {}
        self._hour = hour
        keep = {self.writer.path(k, hour) for k in (*KINDS, "skipped-slots")}
        self.writer.close_stale(keep)
        self.cwriter.close_stale(self._keep_creates(hour))
        c = self.hour_counts
        c["slots"] = c.get("slots", 0) + 1
        if outcome != "ok":
            c[outcome] = c.get(outcome, 0) + 1
        for k in KINDS:
            c[k] = c.get(k, 0) + len(rows[k])

    def _keep_creates(self, hour: str) -> set[Path]:
        return {self.creates_dir / f"observe-{hour[:10]}.jsonl"}

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
            "lookup_failures": self.lookup_failures,
            "backlog_jumps": self.backlog_jumps,
            "breaker_trips": self.breaker_trips,
            "pool_cache": len(self.pool_cache),
            "blocks": self.blocks,
            "credits_used": self.credits(),
        }

    def heartbeat(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_beat >= HEARTBEAT_S:
            self._last_beat = now
            _atomic_json(self.status_path, self.status())

    def retain(self, max_keep_days: int) -> int:
        cutoff_ms = self.clock_ms() - max_keep_days * 86_400_000
        cut_hour, cut_day = hour_of_ms(cutoff_ms), day_of_ms(cutoff_ms)
        removed = 0
        for d in (self.out_dir, self.creates_dir):
            for p in d.iterdir():
                m, o = _FILE_RE.match(p.name), _OBS_RE.match(p.name)
                if (m and m.group(2) < cut_hour) or (o and o.group(1) < cut_day):
                    p.unlink()
                    removed += 1
        return removed

    # ------------------------------------------------------------ loop
    def _backlog_jump(self, tip: int) -> None:
        assert self.last_done is not None
        first, last = self.last_done + 1, tip - 1
        n = last - first + 1
        t_ms = max(self.clock_ms(), self.last_t_recv_ms)
        self._gap_row({"kind": "gap", "reason": "backlog_jump", "from_slot": first, "to_slot": last,
                       "slots": n, "t_recv_ms": t_ms})
        self.gaps += n
        self.backlog_jumps += 1
        self._pending_skips.clear()
        self.last_done = last
        self._checkpoint(None)
        self._log(f"backlog jump: {n} slots {first}..{last} recorded as gaps, resuming at tip {tip}")

    def step(self) -> int:
        """Fetch up to BATCH_SLOTS slots, from last_done + 1 toward the tip. Returns slots processed."""
        tip = self.get_tip()
        self.tip = tip
        if self.last_done is None:
            self.last_done = tip - 1  # no checkpoint: begin at the current tip
            self._checkpoint(None)
        if tip - self.last_done > self.backlog_slots:
            self._backlog_jump(tip)
        n = 0
        for slot in range(self.last_done + 1, min(tip, self.last_done + BATCH_SLOTS) + 1):
            self.process_slot(slot)
            n += 1
            self.heartbeat()
        return n

    def run(self, stop: threading.Event, max_keep_days: int = 3, idle_s: float = 0.4) -> None:
        last_retain = 0.0
        fail_wait = idle_s
        while not stop.is_set():
            self.precreate()
            try:
                n = self.step()
                fail_wait = idle_s
            except TransientError as exc:
                self.retries += 1
                self._log_limited("tip", f"tip poll failed: {type(exc).__name__}")
                if exc.rate_limited:
                    self._consec_429 += 1
                    if self._consec_429 >= self.breaker_after:
                        self.breaker_trips += 1
                        fail_wait = max(fail_wait, self.breaker_pause_s)
                        self._consec_429 = 0
                stop.wait(fail_wait)
                fail_wait = min(30.0, fail_wait * 2)
                self.heartbeat()
                continue
            self.heartbeat()
            if time.monotonic() - last_retain > 3600:
                last_retain = time.monotonic()
                self._log(f"retention removed {self.retain(max_keep_days)} files")
            if n == 0:
                stop.wait(idle_s)
        self.writer.close()
        self.cwriter.close()
        self.heartbeat(force=True)


# ---------------------------------------------------------------- real RPC glue
def make_rpc(
    url: str, rps: float, credits_per_call: int = 1
) -> tuple[Fetcher, Callable[[], int], Callable[[], int], Callable[[list[str]], dict]]:
    """credits_per_call is 1: the Helius dashboard showed about 1 credit per getBlock on this plan."""
    limiter = RateLimiter(rps)
    budget = CreditBudget(cap=10**12, per_call=credits_per_call)

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
                "maxSupportedTransactionVersion": 1,  # as the walker
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

    def lookup(pools: list[str]) -> dict[str, tuple[str, str]]:
        """getMultipleAccounts through the same limiter and credit budget as getBlock."""
        if not pools:
            return {}
        result, _w, _c = rpc_call(
            url, "getMultipleAccounts", [pools, {"encoding": "base64", "commitment": "confirmed"}],
            limiter, timeout=20.0, budget=budget,
        )
        values = result.get("value") if isinstance(result, dict) else None
        found: dict[str, tuple[str, str]] = {}
        if not isinstance(values, list):
            return found
        for pool, entry in zip(pools, values):
            data = entry.get("data") if isinstance(entry, dict) else None
            if not (isinstance(data, list) and data and isinstance(data[0], str)):
                continue
            try:
                decoded = decode_pool_account(base64.b64decode(data[0]))
            except (ValueError, binascii.Error):
                continue
            if decoded is not None:
                found[pool] = (decoded["base_mint"], decoded["quote_mint"])
        return found

    return fetch, get_tip, (lambda: budget.used), lookup


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--creates-out", default=DEFAULT_CREATES)
    ap.add_argument("--state-dir", default=DEFAULT_STATE)
    ap.add_argument("--rps", type=float, default=5.0)
    ap.add_argument("--max-keep-days", type=int, default=3)
    ap.add_argument("--max-retries", type=int, default=8)
    ap.add_argument("--backlog-slots", type=int, default=50)
    ap.add_argument("--credits-per-call", type=int, default=1)
    args = ap.parse_args(argv)
    if args.rps <= 0:
        ap.error("--rps must be > 0")
    url, kind = resolve_rpc_url(None, os.environ)
    if kind != "helius":
        print("fast_tip_follower: HELIUS_API_KEY is not set; refusing the public RPC", file=sys.stderr)
        return 2
    fetch, get_tip, credits, lookup = make_rpc(url, args.rps, args.credits_per_call)
    follower = TipFollower(
        out_dir=Path(args.out),
        creates_dir=Path(args.creates_out),
        state_dir=Path(args.state_dir),
        fetch=fetch,
        get_tip=get_tip,
        lookup=lookup,
        credits=credits,
        max_retries=args.max_retries,
        backlog_slots=args.backlog_slots,
    )
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    print(f"fast_tip_follower: start out={args.out} rps={args.rps} resume={follower.last_done}", flush=True)
    follower.run(stop, max_keep_days=args.max_keep_days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
