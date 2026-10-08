"""Measure a processed-commitment migration stream against the getBlock tip follower.

Paper, read-only on chain, no keys beyond the Helius read key. Subscribes to Helius enhanced
websocket `transactionSubscribe` on the pump migration account and writes one JSONL row per
notification (t_recv_ms, slot, signature, err, is_migration, mint, pool, canonical pool).
`compare` joins those rows with tools/fast_tip_follower.py migrations-*.jsonl by mint and prints
the latency gain: p50/p90 of (tip t_recv_ms - stream t_recv_ms).

  python -m tools.migration_stream_probe run [--out DIR] [--env-file F] [--encoding json]
  python -m tools.migration_stream_probe compare --start ISO --end ISO

The URL carries the API key. It is never logged; every message goes through redact_rpc_url.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from tools.pump_history_backfill import (
    redact_rpc_url,
    rows_from_block,
    tx_signature,
)

log = logging.getLogger("migration_stream_probe")

MIGRATION_ACCOUNT = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"
HELIUS_WSS = "wss://mainnet.helius-rpc.com"
DEFAULT_ENV_FILE = "/var/lib/mal/fast-listener/helius.env"
DEFAULT_OUT = "/var/lib/mal/sealed/fast-migration-stream"
DEFAULT_TIP_DIR = "/var/lib/mal/sealed/fast-trades-tip"
SOURCE = "stream_processed"
# Highest transaction version a full-transaction request accepts. 0 returns JSON-RPC -32015 (or drops
# the notification) for every version-1 transaction, which has been on mainnet since 2026-09-20 and
# carries the compute budget in message.transactionConfig. 1 returns legacy, v0 and v1.
# Helius: https://www.helius.dev/docs/rpc/transaction-v1 (getTransaction, getBlock, transactionSubscribe,
# blockSubscribe); https://www.helius.dev/docs/enhanced-websockets/transaction-subscribe
# Solana: https://solana.com/docs/rpc/http/gettransaction ; v1 format: SIMD-0385.
MAX_SUPPORTED_TX_VERSION = 1
_FILE_RE = re.compile(r"^migrations-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl$")


# ---------------------------------------------------------------- url / key


def load_api_key(env_file: str, environ: Mapping[str, str] | None = None) -> str:
    environ = os.environ if environ is None else environ
    key = (environ.get("HELIUS_API_KEY") or "").strip()
    p = Path(env_file)
    if not key and p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("HELIUS_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
    if not key or any(ch in key for ch in "\r\n& #"):
        raise SystemExit("no usable HELIUS_API_KEY (env or --env-file)")
    return key


def helius_ws_url(api_key: str, base: str = HELIUS_WSS) -> str:
    """Caller must not log the return value."""
    return f"{base.rstrip('/')}/?api-key={api_key.strip()}"


def subscribe_request(encoding: str = "json", req_id: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "transactionSubscribe",
        "params": [
            {"accountInclude": [MIGRATION_ACCOUNT], "failed": False, "vote": False},
            {
                "commitment": "processed",
                "encoding": encoding,
                "transactionDetails": "full",
                "maxSupportedTransactionVersion": MAX_SUPPORTED_TX_VERSION,
                "showRewards": False,
            },
        ],
    }


# ---------------------------------------------------------------- decoding


def hour_of_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def canonical_pool_str(mint: str) -> str | None:
    try:
        from solders.pubkey import Pubkey

        from tools.pumpswap_tx import canonical_pool

        return str(canonical_pool(Pubkey.from_string(mint)))
    except Exception:  # solders missing or bad mint: leave the field empty, never crash the stream
        return None


def tx_entry_from_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a notification result to a getBlock-style entry {"transaction": ..., "meta": ...}.

    Helius wraps it as result.transaction = {transaction, meta}; also accept the entry sitting
    directly on result, or a flat result.transaction (message body) with meta on result.
    """
    def _meta(d: Mapping[str, Any]) -> dict[str, Any]:
        return d.get("meta") if isinstance(d.get("meta"), dict) else {}

    inner = result.get("transaction")
    if isinstance(inner, dict):
        if isinstance(inner.get("meta"), dict) or isinstance(inner.get("transaction"), dict):
            return {"transaction": inner.get("transaction") or {}, "meta": _meta(inner)}
        return {"transaction": inner, "meta": _meta(result)}
    return {"transaction": {}, "meta": _meta(result)}


def decode_migrations(entry: Mapping[str, Any], sig: str | None, slot: Any, t_recv_ms: int) -> list[dict[str, Any]]:
    """Migration rows via the backfill's own rows_from_block on a one-transaction synthetic block."""
    entry = dict(entry)
    tx = dict(entry.get("transaction") or {})
    if sig and not tx.get("signatures"):
        tx["signatures"] = [sig]
    entry["transaction"] = tx
    block = {
        "blockTime": int(t_recv_ms) // 1000,
        "slot": slot if isinstance(slot, int) else 0,
        "transactions": [entry],
    }
    return rows_from_block(block, {})["migrations"]


PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
# Pump `migrate` / `migrate_v2` instruction discriminators; base mint is account index 2 in both
# (same table as tools/fast_helius_pre.py). The migrate transaction itself usually carries no
# decodable Program-data event: the `complete` event is in the earlier bonding-curve tx.
_MIGRATE_IX = {bytes.fromhex("9beae792ec9ea21e"): "migrate", bytes.fromhex("bbcb121fceedfe29"): "migrate_v2"}


def _b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        n = n * 58 + _B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\x00" * (len(text) - len(text.lstrip("1"))) + body


def migrate_from_instructions(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """Detect a pump migrate instruction (top level or inner) in a json-encoded transaction."""
    try:
        msg = (entry.get("transaction") or {}).get("message") or {}
        meta = entry.get("meta") or {}
        keys = list(msg.get("accountKeys") or [])
        keys = [k.get("pubkey") if isinstance(k, dict) else k for k in keys]
        loaded = meta.get("loadedAddresses") or {}
        keys += list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])
        ixs = list(msg.get("instructions") or [])
        for group in meta.get("innerInstructions") or []:
            ixs += list(group.get("instructions") or [])
        for ix in ixs:
            pid = ix.get("programId") or (keys[ix["programIdIndex"]] if "programIdIndex" in ix else None)
            if pid != PUMP_PROGRAM:
                continue
            kind = _MIGRATE_IX.get(_b58decode(ix.get("data") or "")[:8])
            if kind is None:
                continue
            accts = [keys[a] if isinstance(a, int) else a for a in ix.get("accounts") or []]
            if len(accts) > 2:
                return {"type": kind, "mint": accts[2], "pool": None}
    except (KeyError, IndexError, ValueError, TypeError, AttributeError):
        return None
    return None


def row_from_notification(msg: Mapping[str, Any], t_recv_ms: int) -> dict[str, Any] | None:
    """One output row from a transactionNotification, or None if it is not one."""
    if msg.get("method") != "transactionNotification":
        return None
    result = (msg.get("params") or {}).get("result")
    if not isinstance(result, dict):
        return None
    entry = tx_entry_from_result(result)
    meta = entry["meta"]
    sig = result.get("signature") or tx_signature(entry)
    moved = decode_migrations(entry, sig, result.get("slot"), t_recv_ms)
    mig = next((m for m in moved if m.get("type") == "migration"), None) or (moved[0] if moved else None)
    if mig is None and meta.get("err") is None:
        mig = migrate_from_instructions(entry)
    mint = mig.get("mint") if mig else None
    return {
        "v": 1,
        "source": SOURCE,
        "t_recv_ms": int(t_recv_ms),
        "slot": result.get("slot"),
        "signature": sig,
        "err": meta.get("err"),
        "is_migration": mig is not None,
        "event_type": mig.get("type") if mig else None,
        "mint": mint,
        "pool": mig.get("pool") if mig else None,
        "canonical_pool": canonical_pool_str(mint) if mint else None,
        "commitment": "processed",
    }


class RawDump:
    """First N raw notification results as JSONL. Public tx data only; the URL is never in a frame."""

    def __init__(self, path: str | None, limit: int = 50) -> None:
        self.path, self.limit, self.n = path, limit, 0

    def add(self, msg: Mapping[str, Any]) -> None:
        if not self.path or self.n >= self.limit or msg.get("method") != "transactionNotification":
            return
        result = (msg.get("params") or {}).get("result")
        if result is None:
            return
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(result, sort_keys=True) + "\n")
        self.n += 1


class Writer:
    def __init__(self, out_dir: Path) -> None:
        self.dir = out_dir
        self._fh = None
        self._hour = None

    def write(self, row: Mapping[str, Any]) -> None:
        hour = hour_of_ms(int(row["t_recv_ms"]))
        if hour != self._hour:
            self.close()
            self.dir.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.dir / f"migrations-{hour}.jsonl", "a", encoding="utf-8")
            self._hour = hour
        self._fh.write(json.dumps(row, sort_keys=True) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
        self._fh = None
        self._hour = None


# ---------------------------------------------------------------- run loop


class Counts:
    def __init__(self) -> None:
        self.notifications = 0
        self.migrations = 0
        self.errors = 0
        self.reconnects = 0


def next_backoff(prev: float, cap: float = 60.0, jitter: Callable[[], float] = random.random) -> float:
    return min(cap, max(1.0, prev * 2)) * (0.9 + 0.2 * jitter())


DATA_TIMEOUT_S = 900.0  # liveness is WebSocket ping/pong; the account is quiet, so this is only a backstop
ACK_TIMEOUT_S = 30.0


async def session(ws, encoding: str, writer: Writer, counts: Counts, clock_ms: Callable[[], int],
                  stop: asyncio.Event, raw_dump: RawDump | None = None, req_id: int = 1,
                  data_timeout_s: float = DATA_TIMEOUT_S, ack_timeout_s: float = ACK_TIMEOUT_S) -> None:
    """One connected session: subscribe (every time), require the ack, read until close or stop."""
    await ws.send(json.dumps(subscribe_request(encoding, req_id)))
    acked = False
    while not stop.is_set():
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=data_timeout_s if acked else ack_timeout_s)
        except asyncio.TimeoutError:
            raise ConnectionError("data backstop timeout" if acked else "no subscribe ack")
        t = clock_ms()
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if "error" in msg and "method" not in msg:
            raise ConnectionError(f"subscribe error: {redact_rpc_url(str(msg['error']))[:200]}")
        if "method" not in msg and msg.get("id") == req_id and "result" in msg:
            acked = True
            log.info("subscribed id=%s", msg["result"])
            continue
        if raw_dump is not None:
            raw_dump.add(msg)
        row = row_from_notification(msg, t)
        if row is None:
            continue
        writer.write(row)
        counts.notifications += 1
        counts.migrations += bool(row["is_migration"])
        counts.errors += row["err"] is not None


async def run(url: str, encoding: str, writer: Writer, stop: asyncio.Event, connect=None,
              clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
              sleep=asyncio.sleep, counts: Counts | None = None, max_sessions: int | None = None,
              raw_dump: RawDump | None = None) -> Counts:
    counts = counts or Counts()
    if connect is None:
        import websockets

        def connect(u):  # noqa: E306
            return websockets.connect(u, ping_interval=20, ping_timeout=20, close_timeout=5)

    backoff, sessions = 1.0, 0
    while not stop.is_set() and (max_sessions is None or sessions < max_sessions):
        sessions += 1
        started = time.monotonic()
        try:
            async with connect(url) as ws:
                log.info("ws_connected")
                await session(ws, encoding, writer, counts, clock_ms, stop, raw_dump)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # reconnect on anything; message is redacted
            log.warning("ws_down %s: %s", type(exc).__name__, redact_rpc_url(str(exc))[:200])
        if stop.is_set():
            break
        counts.reconnects += 1
        if time.monotonic() - started > 60:
            backoff = 1.0
        else:
            backoff = next_backoff(backoff)
        await sleep(backoff)
    return counts


async def _log_counts(counts: Counts, stop: asyncio.Event, every_s: float = 60.0) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=every_s)
        except asyncio.TimeoutError:
            log.info("counts notifications=%d migrations=%d err=%d reconnects=%d", counts.notifications,
                     counts.migrations, counts.errors, counts.reconnects)


async def amain(args) -> int:
    url = helius_ws_url(load_api_key(args.env_file))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, stop.set)
    writer, counts = Writer(Path(args.out)), Counts()
    logger = asyncio.create_task(_log_counts(counts, stop))
    try:
        await run(url, args.encoding, writer, stop, counts=counts,
                  raw_dump=RawDump(args.raw_dump, args.raw_dump_n) if args.raw_dump else None)
    finally:
        stop.set()
        await logger
        writer.close()
    return 0


# ---------------------------------------------------------------- compare


def parse_time_ms(text: str) -> int:
    if re.fullmatch(r"\d{10,}", text):
        return int(text)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def read_rows(directory: Path, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not directory.is_dir():
        return rows
    lo, hi = hour_of_ms(start_ms), hour_of_ms(end_ms)
    for p in sorted(directory.rglob("migrations-*.jsonl")):
        m = _FILE_RE.match(p.name)
        if not m or not lo <= m.group(1) <= hi:
            continue
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t = r.get("t_recv_ms")
            if isinstance(t, int) and start_ms <= t <= end_ms:
                rows.append(r)
    return rows


def pct(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q / 100
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def _join(s_by: Mapping[str, Mapping[str, Any]], t_by: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    matched = sorted(set(s_by) & set(t_by))
    deltas = [t_by[m]["t_recv_ms"] - s_by[m]["t_recv_ms"] for m in matched]
    slot_diffs = [
        s_by[m]["slot"] - t_by[m]["slot"]
        for m in matched
        if isinstance(s_by[m].get("slot"), int) and isinstance(t_by[m].get("slot"), int)
    ]
    return {
        "n_matched": len(matched),
        "n_only_stream": len(set(s_by) - set(t_by)),
        "n_only_tip": len(set(t_by) - set(s_by)),
        "delta_ms_p50": pct(deltas, 50),
        "delta_ms_p90": pct(deltas, 90),
        "delta_ms_min": min(deltas) if deltas else None,
        "delta_ms_max": max(deltas) if deltas else None,
        "slot_equal": sum(1 for d in slot_diffs if d == 0),
        "slot_diff_stream_minus_tip_p50": pct(slot_diffs, 50),
        "slot_diff_stream_minus_tip_max": max(slot_diffs) if slot_diffs else None,
        "signature_equal": sum(1 for m in matched if s_by[m].get("signature") == t_by[m].get("signature")),
        "stream_first": sum(1 for d in deltas if d > 0),
    }


def compare_rows(stream: Iterable[Mapping[str, Any]], tip: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Join by mint. The stream sees the migrate transaction, which lands 0-3 slots after the
    bonding curve's `complete` event; the tip follower mostly records `complete`, rarely `migration`.
    Top-level keys compare against tip `migration` rows; `vs_tip_complete` compares against `complete`."""
    s_by: dict[str, Mapping[str, Any]] = {}
    for r in stream:
        if r.get("mint") and r.get("is_migration", True) and r.get("err") is None:
            s_by.setdefault(r["mint"], r)  # first arrival wins
    by_type: dict[str, dict[str, Mapping[str, Any]]] = {"migration": {}, "complete": {}}
    for r in tip:
        typ = r.get("type", "migration")
        if r.get("mint") and typ in by_type:
            by_type[typ].setdefault(r["mint"], r)
    out = _join(s_by, by_type["migration"])
    out["vs_tip_complete"] = _join(s_by, by_type["complete"])
    return out


def compare_dirs(stream_dir: Path, tip_dir: Path, start_ms: int, end_ms: int, slack_ms: int = 600_000) -> dict[str, Any]:
    stream = read_rows(stream_dir, start_ms, end_ms)
    tip = read_rows(tip_dir, start_ms, end_ms + slack_ms)  # tip lags the stream: look a little past the end
    return compare_rows(stream, tip)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", default=DEFAULT_OUT)
    r.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    r.add_argument("--encoding", default="json", choices=("json", "jsonParsed"))
    r.add_argument("--raw-dump", default=None, help="write the first N raw notification results as JSONL")
    r.add_argument("--raw-dump-n", type=int, default=50)
    c = sub.add_parser("compare")
    c.add_argument("--start", required=True)
    c.add_argument("--end", required=True)
    c.add_argument("--stream-dir", default=DEFAULT_OUT)
    c.add_argument("--tip-dir", default=DEFAULT_TIP_DIR)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    if args.cmd == "compare":
        out = compare_dirs(Path(args.stream_dir), Path(args.tip_dir), parse_time_ms(args.start), parse_time_ms(args.end))
        print(json.dumps(out, indent=2))
        return 0
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
