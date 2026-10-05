"""Fast graduation stream: processed transactionSubscribe on near-complete bonding curves.

MEASUREMENT ONLY sidecar (not part of the runner, paper only, read-only on chain). Goal: see the
bonding curve's `complete` event and the final bonding trades ~1-1.5 s before the confirmed
getBlock tip follower does. tools/migration_stream_probe.py (job #127) showed the migration
authority alone gains only ~0.2 s, because the migrate tx lands ~3 slots after `complete`; so this
also watches the bonding-curve PDAs of mints already near graduation.

  python -m tools.fast_grad_stream [--out DIR] [--tip-dir DIR] [--threshold 0.90] [--cap 200]
  (offline comparison: python -m tools.grad_stream_compare)

Watch set: migration authority + bonding-curve PDAs of mints whose latest tip-tape `base_reserve`
gives curve progress >= threshold (tools.paper_curve_math.curve_progress). Mints that completed
(seen on the tape as PumpSwap or by this stream) or have been idle > 10 min are dropped; the set
is capped (default 200, highest progress first). Refreshed every ~2 s; on a change a NEW
transactionSubscribe is sent first and the old id is unsubscribed only after the new one is acked,
so there is no gap (duplicates in the overlap are removed by signature).

Output (--out): grad-<UTC hour>.jsonl rows {v, source, t_recv_ms, slot, signature, mint, kind,
commitment:"processed", ...}; kind is trade|complete|migrate|other. Trades carry side,
sol_lamports, token_raw, base_reserve, quote_reserve, venue, event_index. status.json every 30 s.

Credits (ASSUMPTION, unverified): Helius bills enhanced websocket data by bytes streamed, taken
here as 3 credits per 0.1 MB (decimal). The sidecar logs and writes per-hour message and byte
counts plus the implied credits; treat the credit figure as an estimate and check against the
dashboard. Helius `accountInclude` max size is NOT verified from anything in this repo (docs are
recalled as up to 50,000 addresses for transactionSubscribe); we cap at 200 by default.
transactionUnsubscribe as the cancel method name is likewise unverified here.

Key: env HELIUS_API_KEY (systemd EnvironmentFile=). The ws URL is never logged; every message
goes through redact_secrets.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from observe.trade_decode import records_from_logs
from tools.migration_stream_probe import (
    DATA_TIMEOUT_S,
    HELIUS_WSS,
    MIGRATION_ACCOUNT,
    PUMP_PROGRAM,
    hour_of_ms,
    migrate_from_instructions,
    next_backoff,
    tx_entry_from_result,
)
from tools.paper_curve_math import curve_progress
from tools.pump_history_backfill import lifecycle_from_logs, redact_rpc_url, tx_signature

log = logging.getLogger("fast_grad_stream")

DEFAULT_OUT = "/var/lib/mal/fast-grad-stream"
DEFAULT_TIP_DIR = "/var/lib/mal/sealed/fast-trades-tip"
SOURCE = "grad_stream_processed"
IDLE_MS = 600_000
MAX_TRACKED = 50_000
BOOTSTRAP_TAIL_BYTES = 8 * 1024 * 1024
MAX_PENDING_SUBS = 3
CREDITS_PER_100KB = 3  # ASSUMPTION, see module docstring
_KEY_RE = re.compile(r"(api-key=)[^&\s\"']+", re.I)


def redact_secrets(text: str, key: str | None = None) -> str:
    out = _KEY_RE.sub(r"\1<redacted>", redact_rpc_url(text))
    if key:
        out = out.replace(key, "<redacted>")
    return out


def ws_url(api_key: str, base: str = HELIUS_WSS) -> str:
    """Caller must not log the return value."""
    return f"{base.rstrip('/')}/?api-key={api_key.strip()}"


def api_key_from_env(environ: Mapping[str, str] | None = None) -> str:
    environ = os.environ if environ is None else environ
    key = (environ.get("HELIUS_API_KEY") or "").strip()
    if not key or any(ch in key for ch in "\r\n& #"):
        raise SystemExit("no usable HELIUS_API_KEY in the environment (use EnvironmentFile=)")
    return key


# ---------------------------------------------------------------- bonding curve PDA


def curve_pda(mint: str) -> str:
    from solders.pubkey import Pubkey

    return str(Pubkey.find_program_address([b"bonding-curve", bytes(Pubkey.from_string(mint))],
                                           Pubkey.from_string(PUMP_PROGRAM))[0])


class PdaCache:
    """mint -> curve PDA and back, bounded (cleared when over max; PDAs are cheap to recompute)."""

    def __init__(self, fn: Callable[[str], str] = curve_pda, max_items: int = 2000) -> None:
        self.fn, self.max = fn, max_items
        self.by_mint: dict[str, str] = {}
        self.by_curve: dict[str, str] = {}

    def curve(self, mint: str) -> str:
        c = self.by_mint.get(mint)
        if c is None:
            if len(self.by_mint) >= self.max:
                self.by_mint.clear()
                self.by_curve.clear()
            c = self.by_mint[mint] = self.fn(mint)
            self.by_curve[c] = mint
        return c


# ---------------------------------------------------------------- watch set from the tip tape


class WatchTracker:
    """Latest bonding progress per mint from tip-tape trade rows. Bounded."""

    def __init__(self, threshold: float = 0.90, cap: int = 200, idle_ms: int = IDLE_MS,
                 max_tracked: int = MAX_TRACKED) -> None:
        self.threshold, self.cap, self.idle_ms, self.max_tracked = threshold, cap, idle_ms, max_tracked
        self.state: OrderedDict[str, tuple[float, int]] = OrderedDict()  # mint -> (progress, last_ms)
        self.done: OrderedDict[str, int] = OrderedDict()  # completed mints, bounded

    def mark_done(self, mint: str, now_ms: int) -> None:
        self.done[mint] = now_ms
        self.done.move_to_end(mint)
        self.state.pop(mint, None)
        while len(self.done) > self.max_tracked:
            self.done.popitem(last=False)

    def feed(self, row: Mapping[str, Any]) -> None:
        mint, t = row.get("mint"), row.get("t_recv_ms")
        if not isinstance(mint, str) or not isinstance(t, int):
            return
        venue = row.get("venue")
        if venue == "pumpswap":
            self.mark_done(mint, t)
            return
        if venue != "pump_bonding" or mint in self.done:
            return
        base = row.get("base_reserve")
        if not isinstance(base, int) or base <= 0:
            return
        prev = self.state.get(mint)
        if prev is not None and t < prev[1]:
            return  # out-of-order older row
        self.state[mint] = (curve_progress("pump_bonding", base), t)
        self.state.move_to_end(mint)
        while len(self.state) > self.max_tracked:
            self.state.popitem(last=False)

    def select(self, now_ms: int) -> list[str]:
        """Mints at/above the threshold, not done, not idle; highest progress first, capped."""
        for m in [m for m, (_, t) in self.state.items() if now_ms - t > self.idle_ms]:
            del self.state[m]
        hits = [(p, m) for m, (p, _) in self.state.items() if p >= self.threshold]
        hits.sort(key=lambda x: (-x[0], x[1]))
        return [m for _, m in hits[: self.cap]]


class TapeTail:
    """Tails trades-<hour>.jsonl in the tip dir; bootstraps from the tail of the last two hours."""

    def __init__(self, directory: Path, tracker: WatchTracker, tail_bytes: int = BOOTSTRAP_TAIL_BYTES) -> None:
        self.dir, self.tracker, self.tail_bytes = Path(directory), tracker, tail_bytes
        self.offsets: dict[str, int] = {}
        self.prev_polled: set[str] = set()
        self.rows_read = 0

    def _consume(self, path: Path, hour: str) -> None:
        """Read new complete lines line by line (no whole-buffer copies)."""
        try:
            size = path.stat().st_size
        except OSError:
            return
        pos = self.offsets.get(hour)
        skip_partial = False
        if pos is None:
            pos = max(0, size - self.tail_bytes)
            skip_partial = pos > 0
        elif pos > size:
            pos = 0
        with open(path, "rb") as fh:
            fh.seek(pos)
            if skip_partial:
                pos += len(fh.readline())  # drop the partial first line
            while True:
                line = fh.readline()
                if not line.endswith(b"\n"):
                    break  # EOF or a half-written last line: leave it for the next poll
                pos += len(line)
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                self.rows_read += 1
                self.tracker.feed(row)
        self.offsets[hour] = pos

    def poll(self, now_ms: int) -> None:
        cur = hour_of_ms(now_ms)
        prev = hour_of_ms(now_ms - 3_600_000)
        if prev not in self.prev_polled:
            # once per hour: bootstrap the tail, or pick up what was written after the rollover
            self._consume(self.dir / f"trades-{prev}.jsonl", prev)
            self.prev_polled.add(prev)
        self._consume(self.dir / f"trades-{cur}.jsonl", cur)
        for h in [h for h in self.offsets if h not in (cur, prev)]:
            del self.offsets[h]
        self.prev_polled &= {cur, prev}


# ---------------------------------------------------------------- subscription


def subscribe_request(accounts: Sequence[str], req_id: int, encoding: str = "json") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "transactionSubscribe",
        "params": [
            {"accountInclude": list(accounts), "failed": False, "vote": False},
            {"commitment": "processed", "encoding": encoding, "transactionDetails": "full",
             "maxSupportedTransactionVersion": 0, "showRewards": False},
        ],
    }


def unsubscribe_request(sub_id: int, req_id: int) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "method": "transactionUnsubscribe", "params": [sub_id]}


class Subscriber:
    """One connection, one live subscription. A change opens the new subscription first and
    unsubscribes the old id only once the new one is acked."""

    def __init__(self, ws, mono: Callable[[], float] = time.monotonic) -> None:
        self.ws = ws
        self.active_id: int | None = None
        self.active_set: tuple[str, ...] = ()
        self.pending: dict[int, tuple[str, ...]] = {}
        self.pending_at: dict[int, float] = {}
        self.unsubs: set[int] = set()
        self._req = 0
        self.mono = mono

    def _next(self) -> int:
        self._req += 1
        return self._req

    async def request(self, accounts: Sequence[str]) -> None:
        accounts = tuple(sorted(set(accounts)))
        if not self.pending and accounts == self.active_set:
            return
        if self.pending and accounts == self.pending[max(self.pending)]:
            return
        if len(self.pending) >= MAX_PENDING_SUBS:
            raise ConnectionError(f"{len(self.pending)} subscribes pending without an ack")
        rid = self._next()
        self.pending[rid] = accounts
        self.pending_at[rid] = self.mono()
        await self.ws.send(json.dumps(subscribe_request(accounts, rid)))

    def check_stale(self, ack_timeout_s: float) -> None:
        now = self.mono()
        for rid, t in self.pending_at.items():
            if now - t > ack_timeout_s:
                raise ConnectionError("subscribe ack older than ack_timeout")

    async def on_response(self, msg: Mapping[str, Any]) -> bool:
        """True if the frame was a subscribe/unsubscribe response (consumed)."""
        rid = msg.get("id")
        if "method" in msg or rid is None:
            return False
        if rid in self.unsubs:
            self.unsubs.discard(rid)
            if "error" in msg or msg.get("result") is not True:
                # the old subscription may still be live and billing: reconnect
                raise ConnectionError(f"unsubscribe failed: {redact_secrets(str(msg.get('error', msg.get('result'))))[:200]}")
            return True
        if rid in self.pending:
            accounts = self.pending.pop(rid)
            self.pending_at.pop(rid, None)
            if "error" in msg:
                raise ConnectionError(f"subscribe error: {redact_secrets(str(msg['error']))[:200]}")
            old = self.active_id
            self.active_id, self.active_set = msg.get("result"), accounts
            if old is not None and old != self.active_id:
                uid = self._next()
                self.unsubs.add(uid)
                await self.ws.send(json.dumps(unsubscribe_request(old, uid)))
            return True
        return False


# ---------------------------------------------------------------- decoding


def tx_keys(entry: Mapping[str, Any]) -> list[str]:
    msg = (entry.get("transaction") or {}).get("message") or {}
    meta = entry.get("meta") or {}
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in (msg.get("accountKeys") or [])]
    loaded = meta.get("loadedAddresses") or {}
    keys += list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])
    return [k for k in keys if isinstance(k, str)]


def rows_from_notification(msg: Mapping[str, Any], t_recv_ms: int, pdas: PdaCache,
                           watched: set[str]) -> list[dict[str, Any]] | None:
    """Rows for one transactionNotification; None if not one. Failed txs yield no rows.

    `complete` and `migrate` are kept for any mint; trades only for watched mints (the watch set
    can change between the subscribe and the notice)."""
    if msg.get("method") != "transactionNotification":
        return None
    result = (msg.get("params") or {}).get("result")
    if not isinstance(result, dict):
        return None
    entry = tx_entry_from_result(result)
    meta = entry["meta"]
    if meta.get("err") is not None:
        return []
    sig = result.get("signature") or tx_signature(entry)
    slot = result.get("slot")
    logs = meta.get("logMessages") or []
    base = {"v": 1, "source": SOURCE, "t_recv_ms": int(t_recv_ms), "slot": slot, "signature": sig,
            "commitment": "processed"}
    rows: list[dict[str, Any]] = []
    _, moved = lifecycle_from_logs(logs)
    for ev in moved:
        kind = "complete" if ev.get("type") == "complete" else "migrate"
        rows.append({**base, "mint": ev.get("mint"), "kind": kind,
                     "bonding_curve": ev.get("bonding_curve"), "event_ts": ev.get("event_ts")})
    if not any(r["kind"] == "migrate" for r in rows):
        mig = migrate_from_instructions(entry)
        if mig is not None:
            rows.append({**base, "mint": mig["mint"], "kind": "migrate", "pool": mig.get("pool")})
    for rec in records_from_logs(logs, slot=slot if isinstance(slot, int) else 0, signature=sig or "",
                                 t_recv_ms=t_recv_ms, commitment="processed", feed="grad_stream"):
        mint = rec.get("mint")
        if not mint or (mint not in watched and rec.get("venue") != "pumpswap"):
            continue
        rows.append({**base, "mint": mint, "kind": "trade", "venue": rec.get("venue"),
                     "side": rec.get("side"), "sol_lamports": rec.get("sol_lamports"),
                     "token_raw": rec.get("token_raw"), "base_reserve": rec.get("base_reserve"),
                     "quote_reserve": rec.get("quote_reserve"), "event_index": rec.get("event_index")})
    if not rows:
        keys = tx_keys(entry)
        touched = [pdas.by_curve[k] for k in keys if k in pdas.by_curve]
        if touched or MIGRATION_ACCOUNT in keys:
            rows.append({**base, "mint": touched[0] if touched else None, "kind": "other"})
    return rows


# ---------------------------------------------------------------- output / status


class GradWriter:
    def __init__(self, out_dir: Path) -> None:
        self.dir, self._fh, self._hour = Path(out_dir), None, None

    def write(self, row: Mapping[str, Any]) -> None:
        hour = hour_of_ms(int(row["t_recv_ms"]))
        if hour != self._hour:
            self.close()
            self.dir.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.dir / f"grad-{hour}.jsonl", "a", encoding="utf-8")
            self._hour = hour
        self._fh.write(json.dumps(row, sort_keys=True) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
        self._fh, self._hour = None, None


class Stats:
    def __init__(self) -> None:
        self.reconnects = 0
        self.decode_errors = 0
        self.msgs = 0
        self.rows_by_kind: dict[str, int] = {}
        self.watch_size = 0
        self.refreshes = 0
        self.hourly: OrderedDict[str, list[int]] = OrderedDict()  # hour -> [msgs, bytes]
        self._last = (time.monotonic(), 0)

    def note_msg(self, hour: str, nbytes: int) -> None:
        self.msgs += 1
        h = self.hourly.setdefault(hour, [0, 0])
        h[0] += 1
        h[1] += nbytes
        while len(self.hourly) > 72:
            self.hourly.popitem(last=False)

    def snapshot(self, now_ms: int) -> dict[str, Any]:
        mono = time.monotonic()
        dt = max(1e-6, mono - self._last[0])
        rate = (self.msgs - self._last[1]) / dt
        self._last = (mono, self.msgs)
        return {
            "t_ms": now_ms, "watch_set_size": self.watch_size, "msgs_total": self.msgs,
            "msgs_per_s": round(rate, 3), "reconnects": self.reconnects,
            "decode_errors": self.decode_errors, "rows_by_kind": dict(self.rows_by_kind),
            "refreshes": self.refreshes,
            "hourly": {h: {"msgs": v[0], "bytes": v[1],
                           "est_credits_ASSUMED_3_per_100KB": round(v[1] / 100_000 * CREDITS_PER_100KB, 1)}
                       for h, v in self.hourly.items()},
        }


def write_status(path: Path, snap: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, sort_keys=True))
    os.replace(tmp, path)


# ---------------------------------------------------------------- run loop


class Engine:
    def __init__(self, tracker: WatchTracker, tape: TapeTail | None, pdas: PdaCache, writer: GradWriter,
                 stats: Stats, status_path: Path | None, refresh_s: float = 2.0, status_s: float = 30.0,
                 clock_ms: Callable[[], int] = lambda: int(time.time() * 1000)) -> None:
        self.tracker, self.tape, self.pdas, self.writer, self.stats = tracker, tape, pdas, writer, stats
        self.status_path, self.refresh_s, self.status_s, self.clock_ms = status_path, refresh_s, status_s, clock_ms
        self.watched: set[str] = set()
        self.seen: OrderedDict[tuple, None] = OrderedDict()
        self.next_refresh = 0.0
        self.next_status = 0.0

    def accounts(self) -> list[str]:
        now = self.clock_ms()
        if self.tape is not None:
            self.tape.poll(now)
        mints = self.tracker.select(now)
        self.watched = set(mints)
        self.stats.watch_size = len(mints)
        return [MIGRATION_ACCOUNT] + [self.pdas.curve(m) for m in mints]

    def handle(self, raw: Any, msg: Mapping[str, Any], t_ms: int) -> None:
        self.stats.note_msg(hour_of_ms(t_ms), len(raw) if isinstance(raw, (str, bytes)) else 0)
        try:
            rows = rows_from_notification(msg, t_ms, self.pdas, self.watched)
        except Exception as exc:  # a malformed notification must not kill the stream
            self.stats.decode_errors += 1
            log.warning("decode_error %s", type(exc).__name__)
            return
        for row in rows or []:
            dk = (row["kind"], row.get("signature"), row.get("mint"), row.get("event_index"))
            if dk in self.seen:
                continue  # overlap between old and new subscription
            self.seen[dk] = None
            while len(self.seen) > 20_000:
                self.seen.popitem(last=False)
            self.writer.write(row)
            self.stats.rows_by_kind[row["kind"]] = self.stats.rows_by_kind.get(row["kind"], 0) + 1
            if row["kind"] in ("complete", "migrate") and row.get("mint"):
                self.tracker.mark_done(row["mint"], t_ms)

    def periodic_status(self, mono: float) -> None:
        if mono < self.next_status:
            return
        self.next_status = mono + self.status_s
        snap = self.stats.snapshot(self.clock_ms())
        if self.status_path:
            write_status(self.status_path, snap)
        hours = sorted(snap["hourly"])
        if hours:
            h = snap["hourly"][hours[-1]]
            log.info("counts hour=%s msgs=%d bytes=%d est_credits_assumed=%s watch=%d rows=%s", hours[-1],
                     h["msgs"], h["bytes"], h["est_credits_ASSUMED_3_per_100KB"], snap["watch_set_size"],
                     snap["rows_by_kind"])


async def session(ws, eng: Engine, stop: asyncio.Event, data_timeout_s: float = DATA_TIMEOUT_S,
                  ack_timeout_s: float = 30.0, mono: Callable[[], float] = time.monotonic) -> None:
    sub = Subscriber(ws, mono)
    await sub.request(eng.accounts())
    eng.next_refresh = mono() + eng.refresh_s
    last_data = mono()
    while not stop.is_set():
        now = mono()
        if now >= eng.next_refresh:
            eng.next_refresh = now + eng.refresh_s
            await sub.request(eng.accounts())
            eng.stats.refreshes += 1
        sub.check_stale(ack_timeout_s)
        eng.periodic_status(now)
        wait = max(0.05, min(eng.next_refresh - now, 1.0))
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=wait)
        except asyncio.TimeoutError:
            if mono() - last_data > data_timeout_s:
                raise ConnectionError("data backstop timeout")
            continue
        last_data = mono()
        t = eng.clock_ms()
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            eng.stats.decode_errors += 1
            continue
        if not isinstance(msg, dict):
            continue
        if "error" in msg and "method" not in msg and msg.get("id") not in sub.pending:
            raise ConnectionError(f"rpc error: {redact_secrets(str(msg['error']))[:200]}")
        if await sub.on_response(msg):
            continue
        eng.handle(raw, msg, t)


async def run(url: str, eng: Engine, stop: asyncio.Event, connect=None, sleep=asyncio.sleep,
              max_sessions: int | None = None, key: str | None = None) -> None:
    if connect is None:
        import websockets

        def connect(u):  # noqa: E306
            return websockets.connect(u, ping_interval=20, ping_timeout=20, close_timeout=5,
                                      max_size=8 * 1024 * 1024)

    backoff, sessions = 1.0, 0
    while not stop.is_set() and (max_sessions is None or sessions < max_sessions):
        sessions += 1
        started = time.monotonic()
        try:
            async with connect(url) as ws:
                log.info("ws_connected")
                await session(ws, eng, stop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("ws_down %s: %s", type(exc).__name__, redact_secrets(str(exc), key)[:200])
        if stop.is_set():
            break
        eng.stats.reconnects += 1
        backoff = 1.0 if time.monotonic() - started > 60 else next_backoff(backoff)
        await sleep(backoff)


async def amain(args) -> int:
    key = api_key_from_env()
    url = ws_url(key)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, stop.set)
    out = Path(args.out)
    tracker = WatchTracker(args.threshold, args.cap)
    eng = Engine(tracker, TapeTail(Path(args.tip_dir), tracker), PdaCache(), GradWriter(out), Stats(),
                 out / "status.json", refresh_s=args.refresh_s)
    try:
        await run(url, eng, stop, key=key)
    finally:
        eng.writer.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--tip-dir", default=DEFAULT_TIP_DIR)
    ap.add_argument("--threshold", type=float, default=0.90)
    ap.add_argument("--cap", type=int, default=200)
    ap.add_argument("--refresh-s", type=float, default=2.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
