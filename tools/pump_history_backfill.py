#!/usr/bin/env python3
"""Backfill pump.fun trades, creates, and migrations from public RPC getBlock.

Paper only. Does not start or edit the live tape recorder. Rows match the
sealed tape schema (bonding v1, PumpSwap v2 via stored_trade) with
source=backfill, null receive time, and block_time set from the block.

Public mainnet-beta getBlock is archival and returns program logs, so the
same Program data decoder as the live tape can rebuild the rows. No API key.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from observe.trade_decode import (
    WSOL_MINT,
    apply_pool_mints,
    b58encode,
    decode_pool_account,
    decode_program_data,
    records_from_logs,
)
from observe.trade_store import stored_trade

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
FEED = "public_rpc_getblock"
SOURCE = "backfill"
# Same window the live decoder accepts. See observe.trade_decode.
_TS_MIN = 1_700_000_000
_TS_MAX = 1_900_000_000
_CREATE_DISC = bytes.fromhex("1b72a94ddeeb6376")
_COMPLETE_DISC = bytes.fromhex("5f72619cd42e9808")
_MIGRATE_DISC = bytes.fromhex("bde95db95c94ea94")
_SKIP_CODES = frozenset({-32007, -32009, -32004})
DEFAULT_MAX_BYTES = 40 * 1024**3
HEADROOM_RATIO = 0.20


def budget_bytes(volume_total: int, volume_avail: int, cap_bytes: int = DEFAULT_MAX_BYTES) -> int:
    """Bytes we may still write. Cap, and leave 20% of the volume free."""
    reserve = int(volume_total * HEADROOM_RATIO)
    by_headroom = volume_avail - reserve
    if by_headroom < 0:
        by_headroom = 0
    return min(int(cap_bytes), by_headroom)


def _read_string(buf: bytes, off: int, limit: int = 512) -> tuple[str | None, int]:
    if off + 4 > len(buf):
        return None, off
    size = int.from_bytes(buf[off : off + 4], "little")
    off += 4
    if size > limit or off + size > len(buf):
        return None, off
    try:
        text = buf[off : off + size].decode("utf-8")
    except UnicodeDecodeError:
        return None, off
    return text, off + size


def _take(buf: bytes, off: int, size: int) -> tuple[bytes | None, int]:
    if off + size > len(buf):
        return None, off
    return buf[off : off + size], off + size


def decode_create_event(raw: bytes) -> dict[str, Any] | None:
    """CreateEvent prefix from the public pump IDL. Extra tail is optional."""
    if len(raw) < 8 or raw[:8] != _CREATE_DISC:
        return None
    off = 8
    name, off = _read_string(raw, off)
    symbol, off = _read_string(raw, off)
    uri, off = _read_string(raw, off, limit=512)
    if name is None or symbol is None or uri is None:
        return None
    mint_b, off = _take(raw, off, 32)
    _curve, off = _take(raw, off, 32)
    user_b, off = _take(raw, off, 32)
    creator_b, off = _take(raw, off, 32)
    ts_b, off = _take(raw, off, 8)
    vtok_b, off = _take(raw, off, 8)
    vsol_b, off = _take(raw, off, 8)
    rtok_b, off = _take(raw, off, 8)
    supply_b, off = _take(raw, off, 8)
    if None in (mint_b, user_b, creator_b, ts_b, vtok_b, vsol_b, rtok_b, supply_b):
        return None
    assert mint_b and user_b and creator_b and ts_b and vtok_b and vsol_b and rtok_b and supply_b
    ts = int.from_bytes(ts_b, "little", signed=True)
    if not _TS_MIN <= ts <= _TS_MAX:
        return None
    quote_mint = WSOL_MINT
    is_mayhem = False
    # token_program, is_mayhem, is_cashback, quote_mint — present on current events.
    # Native SOL is the zero pubkey in this event; the tape uses the WSOL mint.
    tail, off2 = _take(raw, off, 32 + 1 + 1 + 32)
    if tail is not None:
        is_mayhem = tail[32] == 1
        parsed = b58encode(tail[34:66])
        if parsed != "11111111111111111111111111111111":
            quote_mint = parsed
    return {
        "type": "create",
        "mint": b58encode(mint_b),
        "trader": b58encode(user_b),
        "creator": b58encode(creator_b),
        "event_ts": ts,
        "quote_reserve": int.from_bytes(vsol_b, "little"),
        "base_reserve": int.from_bytes(vtok_b, "little"),
        "real_token_reserves": int.from_bytes(rtok_b, "little"),
        "token_raw": int.from_bytes(supply_b, "little"),
        "quote_mint": quote_mint,
        "name": name,
        "symbol": symbol,
        "is_mayhem_mode": is_mayhem,
    }


def decode_complete_event(raw: bytes) -> dict[str, Any] | None:
    if len(raw) < 144 or raw[:8] != _COMPLETE_DISC:
        return None
    ts = int.from_bytes(raw[104:112], "little", signed=True)
    if not _TS_MIN <= ts <= _TS_MAX:
        return None
    return {
        "type": "complete",
        "trader": b58encode(raw[8:40]),
        "mint": b58encode(raw[40:72]),
        "bonding_curve": b58encode(raw[72:104]),
        "event_ts": ts,
        "quote_mint": b58encode(raw[112:144]),
    }


def decode_migration_event(raw: bytes) -> dict[str, Any] | None:
    """CompletePumpAmmMigrationEvent. sol_amount is quote-mint lamports."""
    if len(raw) < 200 or raw[:8] != _MIGRATE_DISC:
        return None
    ts = int.from_bytes(raw[128:136], "little", signed=True)
    if not _TS_MIN <= ts <= _TS_MAX:
        return None
    return {
        "type": "migration",
        "trader": b58encode(raw[8:40]),
        "mint": b58encode(raw[40:72]),
        "token_raw": int.from_bytes(raw[72:80], "little"),
        "sol_lamports": int.from_bytes(raw[80:88], "little"),
        "migration_fee": int.from_bytes(raw[88:96], "little"),
        "bonding_curve": b58encode(raw[96:128]),
        "event_ts": ts,
        "pool": b58encode(raw[136:168]),
        "quote_mint": b58encode(raw[168:200]),
    }


def _program_data(line: str) -> bytes | None:
    if "Program data: " not in line:
        return None
    blob = line.split("Program data: ", 1)[1].strip()
    if not blob:
        return None
    try:
        return base64.b64decode(blob, validate=False)
    except (ValueError, binascii.Error):
        return None


def lifecycle_from_logs(logs: Sequence[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    creates: list[dict[str, Any]] = []
    migrations: list[dict[str, Any]] = []
    for line in logs:
        if not isinstance(line, str):
            continue
        raw = _program_data(line)
        if raw is None:
            continue
        if raw[:8] == _CREATE_DISC:
            ev = decode_create_event(raw)
            if ev is not None:
                creates.append(ev)
        elif raw[:8] == _MIGRATE_DISC:
            ev = decode_migration_event(raw)
            if ev is not None:
                migrations.append(ev)
        elif raw[:8] == _COMPLETE_DISC:
            ev = decode_complete_event(raw)
            if ev is not None:
                migrations.append(ev)
    return creates, migrations


def prime_pool_cache(logs: Sequence[str], cache: dict[str, tuple[str, str]]) -> None:
    for line in logs:
        if not isinstance(line, str):
            continue
        raw = _program_data(line)
        if raw is None:
            continue
        ev = decode_program_data(raw)
        if ev is not None and ev.get("kind") == "create_pool":
            cache[ev["pool"]] = (ev["base_mint"], ev["quote_mint"])


def backfill_trade_row(record: Mapping[str, Any], block_time: int) -> dict[str, Any]:
    """Tape schema plus source, block time, and null receive time."""
    row = stored_trade(record)
    row["source"] = SOURCE
    row["block_time"] = int(block_time)
    row["t_recv"] = None
    row["t_recv_ms"] = None
    return row


def _stamp_lifecycle(
    ev: Mapping[str, Any],
    *,
    slot: int,
    signature: str,
    event_index: int,
    block_time: int,
) -> dict[str, Any]:
    row = dict(ev)
    row["v"] = 1
    row["source"] = SOURCE
    row["feed"] = FEED
    row["venue"] = "pump_bonding"
    row["slot"] = int(slot)
    row["signature"] = signature
    row["event_index"] = int(event_index)
    row["block_time"] = int(block_time)
    row["t_recv"] = None
    row["t_recv_ms"] = None
    row["commitment"] = "confirmed"
    return row


def tx_signature(tx: Mapping[str, Any]) -> str | None:
    body = tx.get("transaction")
    if not isinstance(body, dict):
        return None
    sigs = body.get("signatures")
    if isinstance(sigs, list) and sigs and isinstance(sigs[0], str):
        return sigs[0]
    return None


def rows_from_block(
    block: Mapping[str, Any],
    pool_mints: dict[str, tuple[str, str]],
) -> dict[str, list[dict[str, Any]]]:
    """Decode one getBlock result. Mutates pool_mints with CreatePool events."""
    block_time = block.get("blockTime")
    slot = block.get("slot")
    parent = block.get("parentSlot")
    # getBlock puts slot on the request, not always in the body. Caller may set it.
    if not isinstance(block_time, int):
        return {"trades": [], "creates": [], "migrations": [], "unresolved": []}
    if not isinstance(slot, int):
        slot = int(parent) + 1 if isinstance(parent, int) else 0
    trades: list[dict[str, Any]] = []
    creates: list[dict[str, Any]] = []
    migrations: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    txs = block.get("transactions") or []
    for tx in txs:
        if not isinstance(tx, dict):
            continue
        meta = tx.get("meta") if isinstance(tx.get("meta"), dict) else {}
        if meta.get("err") is not None:
            continue
        sig = tx_signature(tx)
        logs = meta.get("logMessages") or []
        if not sig or not isinstance(logs, list):
            continue
        prime_pool_cache(logs, pool_mints)
        made, moved = lifecycle_from_logs(logs)
        for index, ev in enumerate(made):
            creates.append(
                _stamp_lifecycle(ev, slot=slot, signature=sig, event_index=index, block_time=block_time)
            )
        for index, ev in enumerate(moved):
            migrations.append(
                _stamp_lifecycle(ev, slot=slot, signature=sig, event_index=index, block_time=block_time)
            )
        decoded = records_from_logs(
            logs,
            slot=slot,
            signature=sig,
            t_recv_ms=0,
            commitment="confirmed",
            feed=FEED,
            pool_mints=pool_mints,
        )
        for rec in decoded:
            if rec.get("venue") == "pumpswap" and not rec.get("quote_mint"):
                unresolved.append(rec)
                continue
            trades.append(backfill_trade_row(rec, block_time))
    return {
        "trades": trades,
        "creates": creates,
        "migrations": migrations,
        "unresolved": unresolved,
    }


def resolve_unresolved(
    pending: list[dict[str, Any]],
    pool_mints: dict[str, tuple[str, str]],
    block_time: int | None,
    lookup: Callable[[list[str]], dict[str, tuple[str, str]]],
) -> tuple[list[dict[str, Any]], int]:
    """Fill PumpSwap mints from the cache or batched account lookups.

    When block_time is None, return the mutated trade records. Callers that
    batch several blocks stamp block_time themselves.
    """
    need: list[str] = []
    seen: set[str] = set()
    for rec in pending:
        pool = rec.get("pool")
        if isinstance(pool, str) and pool not in pool_mints and pool not in seen:
            seen.add(pool)
            need.append(pool)
    for offset in range(0, len(need), 100):
        found = lookup(need[offset : offset + 100])
        pool_mints.update(found)
    ready: list[dict[str, Any]] = []
    dropped = 0
    for rec in pending:
        pool = rec.get("pool")
        known = pool_mints.get(pool) if isinstance(pool, str) else None
        if known is None:
            dropped += 1
            continue
        apply_pool_mints(rec, known[0], known[1], mint_source="pool_account")
        if block_time is None:
            ready.append(rec)
        else:
            ready.append(backfill_trade_row(rec, block_time))
    return ready, dropped


class JsonlSink:
    """Append compact JSONL and zstd-seal it on close. Empty files are removed."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w", encoding="utf-8")
        self.rows = 0
        self.bytes = 0

    def write(self, row: Mapping[str, Any]) -> None:
        line = json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n"
        self._fh.write(line)
        self.rows += 1
        self.bytes += len(line.encode("utf-8"))

    def close(self) -> Path | None:
        self._fh.close()
        if self.rows == 0:
            self.path.unlink(missing_ok=True)
            return None
        return seal_jsonl(self.path)


class RateLimiter:
    def __init__(self, rps: float) -> None:
        self.interval = 1.0 / rps if rps > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait < 0:
                wait = 0.0
                self._next = now + self.interval
            else:
                self._next += self.interval
        if wait:
            time.sleep(wait)


def rpc_call(
    url: str,
    method: str,
    params: list[Any],
    limiter: RateLimiter | None,
    timeout: float = 60.0,
) -> tuple[Any, int, int | None]:
    """Return (result, wire_bytes, error_code). Retries HTTP 429."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last_code: int | None = None
    for attempt in range(6):
        if limiter is not None:
            limiter.acquire()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept-Encoding": "gzip"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            last_code = exc.code
            if exc.code == 429 and attempt < 5:
                time.sleep(0.4 * (2**attempt))
                continue
            if attempt < 5 and exc.code >= 500:
                time.sleep(0.6 * (2**attempt))
                continue
            raise
        except (TimeoutError, urllib.error.URLError, ConnectionError, json.JSONDecodeError) as exc:
            if attempt < 5:
                time.sleep(0.6 * (2**attempt))
                continue
            raise RuntimeError(f"{method} transport {type(exc).__name__}") from exc
        wire = len(raw)
        try:
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            payload = json.loads(raw)
        except (gzip.BadGzipFile, json.JSONDecodeError, EOFError, TimeoutError) as exc:
            if attempt < 5:
                time.sleep(0.6 * (2**attempt))
                continue
            raise RuntimeError(f"{method} decode {type(exc).__name__}") from exc
        if "error" in payload:
            err = payload["error"] or {}
            code = err.get("code")
            message = str(err.get("message") or "")
            if code == 429 and attempt < 5:
                last_code = 429
                time.sleep(0.4 * (2**attempt))
                continue
            if code in _SKIP_CODES or "skipped" in message.lower() or "not available" in message.lower():
                return None, wire, code if isinstance(code, int) else None
            raise RuntimeError(f"{method} {code} {message[:160]}")
        return payload.get("result"), wire, None
    raise RuntimeError(f"{method} gave up after 429 ({last_code})")


def fetch_pool_mints(url: str, pools: list[str]) -> dict[str, tuple[str, str]]:
    """One getMultipleAccounts. Same decode as the live tape, no websockets import."""
    if not pools:
        return {}
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getMultipleAccounts",
            "params": [pools, {"encoding": "base64", "commitment": "confirmed"}],
        }
    ).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.load(resp)
    if "error" in payload:
        raise RuntimeError(str(payload["error"])[:200])
    values = payload.get("result", {}).get("value")
    if not isinstance(values, list):
        return {}
    found: dict[str, tuple[str, str]] = {}
    for pool, entry in zip(pools, values):
        if not isinstance(entry, dict):
            continue
        data = entry.get("data")
        if not (isinstance(data, list) and data and isinstance(data[0], str)):
            continue
        try:
            raw = base64.b64decode(data[0])
        except (ValueError, binascii.Error):
            continue
        decoded = decode_pool_account(raw)
        if decoded is None:
            continue
        found[pool] = (decoded["base_mint"], decoded["quote_mint"])
    return found


def fetch_block(url: str, slot: int, limiter: RateLimiter) -> tuple[dict[str, Any] | None, int, int | None]:
    try:
        result, wire, code = rpc_call(
            url,
            "getBlock",
            [
                slot,
                {
                    "encoding": "json",
                    "transactionDetails": "full",
                    "rewards": False,
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 1,
                },
            ],
            limiter,
            timeout=90.0,
        )
    except (RuntimeError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, gzip.BadGzipFile) as exc:
        print(f"getBlock slot={slot} failed {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return None, 0, -1
    if not isinstance(result, dict):
        return None, wire, code
    result["slot"] = slot
    return result, wire, code


def block_time_of(url: str, slot: int, limiter: RateLimiter) -> int | None:
    result, _wire, _code = rpc_call(
        url,
        "getBlock",
        [
            slot,
            {
                "encoding": "json",
                "transactionDetails": "none",
                "rewards": False,
                "commitment": "confirmed",
                "maxSupportedTransactionVersion": 1,
            },
        ],
        limiter,
        timeout=30.0,
    )
    if not isinstance(result, dict):
        return None
    bt = result.get("blockTime")
    return bt if isinstance(bt, int) else None


def slot_for_time(url: str, target: int, anchor_slot: int, anchor_time: int, limiter: RateLimiter) -> int:
    """First slot whose blockTime is >= target. Public RPC has a block per recent slot."""
    span = max(target - anchor_time, 0)
    guess = anchor_slot + int(span / 0.27)
    lo = max(0, guess - 5000)
    hi = guess + 5000
    # Expand until the target is inside (lo_time, hi_time).
    for _ in range(8):
        lo_t = block_time_of(url, lo, limiter)
        hi_t = block_time_of(url, hi, limiter)
        if lo_t is None or hi_t is None:
            lo = max(0, lo - 2000)
            hi += 2000
            continue
        if lo_t > target:
            hi = lo
            lo = max(0, lo - max(2000, (lo_t - target) * 4))
            continue
        if hi_t < target:
            lo = hi
            hi = hi + max(2000, (target - hi_t) * 4)
            continue
        break
    best = hi
    while lo <= hi:
        mid = (lo + hi) // 2
        bt = block_time_of(url, mid, limiter)
        if bt is None or bt < target:
            lo = mid + 1
        else:
            best = mid
            hi = mid - 1
    return best


def slots_between(url: str, start: int, end: int, limiter: RateLimiter) -> list[int]:
    if end < start:
        return []
    out: list[int] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + 4999)
        result, _wire, _code = rpc_call(url, "getBlocks", [cursor, chunk_end], limiter, timeout=60.0)
        if not isinstance(result, list):
            raise RuntimeError(f"getBlocks {cursor}-{chunk_end} returned {type(result).__name__}")
        out.extend(int(s) for s in result)
        cursor = chunk_end + 1
    return out


def seal_jsonl(path: Path) -> Path | None:
    if not path.is_file() or path.stat().st_size == 0:
        if path.is_file():
            path.unlink()
        return None
    subprocess.run(
        ["zstd", "-q", "-3", "-T1", "--rm", "-f", str(path)],
        check=True,
    )
    sealed = path.with_name(path.name + ".zst")
    return sealed


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file() and not item.is_symlink():
            total += item.stat().st_size
    return total


def hour_key(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def load_pool_cache(path: Path) -> dict[str, tuple[str, str]]:
    cache: dict[str, tuple[str, str]] = {}
    if not path.is_file():
        return cache
    opener = subprocess.Popen(["zstdcat", str(path)], stdout=subprocess.PIPE, text=True) if path.suffix == ".zst" else None
    fh = opener.stdout if opener is not None and opener.stdout is not None else path.open(encoding="utf-8")
    try:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            pool = row.get("pool")
            base = row.get("base_mint")
            quote = row.get("quote_mint")
            if isinstance(pool, str) and isinstance(base, str) and isinstance(quote, str):
                cache[pool] = (base, quote)
    finally:
        if opener is not None:
            fh.close()
            opener.wait()
        else:
            fh.close()
    return cache


def run_hour(
    *,
    url: str,
    start_ts: int,
    end_ts: int,
    out_dir: Path,
    limiter: RateLimiter,
    lookup_limiter: RateLimiter,
    pool_mints: dict[str, tuple[str, str]],
    workers: int,
    max_bytes: int,
    anchor_slot: int,
    anchor_time: int,
) -> dict[str, Any]:
    """Fetch [start_ts, end_ts) and write sealed hour files. Idempotent if stats exist."""
    key = hour_key(start_ts)
    stats_path = out_dir / f"stats-{key}.json"
    if stats_path.is_file():
        return json.loads(stats_path.read_text(encoding="utf-8"))
    if dir_size(out_dir) >= max_bytes:
        raise RuntimeError(f"backfill dir already {dir_size(out_dir)} bytes, cap {max_bytes}")

    t0 = time.time()
    start_slot = slot_for_time(url, start_ts, anchor_slot, anchor_time, limiter)
    end_slot = slot_for_time(url, end_ts, anchor_slot, anchor_time, limiter)
    slots = slots_between(url, start_slot, max(start_slot, end_slot - 1), limiter)
    for sub, prefix in (("trades", "trades"), ("creates", "creates"), ("migrations", "migrations")):
        folder = out_dir / sub
        if folder.is_dir():
            for stale in folder.glob(f"{prefix}-{key}.jsonl*"):
                stale.unlink()
    trades = JsonlSink(out_dir / "trades" / f"trades-{key}.jsonl")
    creates = JsonlSink(out_dir / "creates" / f"creates-{key}.jsonl")
    migrations = JsonlSink(out_dir / "migrations" / f"migrations-{key}.jsonl")

    counts = {
        "hour": key,
        "block_time_start": start_ts,
        "block_time_end": end_ts,
        "start_slot": start_slot,
        "end_slot": end_slot,
        "slots": len(slots),
        "empty": 0,
        "trades": 0,
        "bonding": 0,
        "pumpswap": 0,
        "creates": 0,
        "migrations": 0,
        "completes": 0,
        "unresolved_dropped": 0,
        "wire_bytes": 0,
        "errors": 0,
    }

    held: list[dict[str, Any]] = []

    def _lookup(pools: list[str]) -> dict[str, tuple[str, str]]:
        # Different RPC method from getBlock, so it has its own 40-per-10s budget.
        for attempt in range(4):
            lookup_limiter.acquire()
            try:
                return fetch_pool_mints(url, pools)
            except (TimeoutError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as exc:
                if attempt == 3:
                    print(f"pool lookup failed {type(exc).__name__}", file=sys.stderr, flush=True)
                    return {}
                time.sleep(0.5 * (2**attempt))
        return {}

    def _emit_trade(row: Mapping[str, Any]) -> None:
        trades.write(row)
        counts["trades"] += 1
        if row.get("venue") == "pumpswap":
            counts["pumpswap"] += 1
        else:
            counts["bonding"] += 1

    def _flush_held() -> None:
        if not held:
            return
        ready, dropped = resolve_unresolved(held, pool_mints, None, _lookup)
        counts["unresolved_dropped"] += dropped
        for rec in ready:
            bt = int(rec.pop("_block_time"))
            if start_ts <= bt < end_ts:
                _emit_trade(backfill_trade_row(rec, bt))
        held.clear()

    def _consume(block: dict[str, Any] | None, wire: int, code: int | None) -> None:
        counts["wire_bytes"] += wire
        if block is None:
            counts["empty"] += 1
            if code not in _SKIP_CODES and code is not None:
                counts["errors"] += 1
            return
        decoded = rows_from_block(block, pool_mints)
        bt = int(block.get("blockTime") or 0)
        for rec in decoded["unresolved"]:
            rec["_block_time"] = bt
            held.append(rec)
        if len(held) >= 250:
            _flush_held()
        if not start_ts <= bt < end_ts:
            return
        for row in decoded["trades"]:
            _emit_trade(row)
        for row in decoded["creates"]:
            creates.write(row)
            counts["creates"] += 1
        for row in decoded["migrations"]:
            migrations.write(row)
            if row.get("type") == "complete":
                counts["completes"] += 1
            else:
                counts["migrations"] += 1

    try:
        _run_slots(url, slots, workers, limiter, _consume, counts, t0, key, max_bytes, out_dir)
        _flush_held()
    finally:
        sealed = {
            "trades": trades.close(),
            "creates": creates.close(),
            "migrations": migrations.close(),
        }
    counts["elapsed_s"] = round(time.time() - t0, 1)
    counts["files"] = {name: (str(path) if path else None) for name, path in sealed.items()}
    counts["bytes"] = dir_size(out_dir)
    stats_path.write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")
    return counts


def _run_slots(
    url: str,
    slots: list[int],
    workers: int,
    limiter: RateLimiter,
    consume: Callable[[dict[str, Any] | None, int, int | None], None],
    counts: dict[str, Any],
    t0: float,
    key: str,
    max_bytes: int,
    out_dir: Path,
) -> None:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        inflight: dict[int, Any] = {}
        slot_iter = iter(slots)

        def submit() -> bool:
            try:
                slot = next(slot_iter)
            except StopIteration:
                return False
            inflight[slot] = pool.submit(fetch_block, url, slot, limiter)
            return True

        for _ in range(max(1, workers)):
            if not submit():
                break
        done_slots = 0
        last_log = time.time()
        for slot in slots:
            fut = inflight.pop(slot)
            block, wire, code = fut.result()
            submit()
            consume(block, wire, code)
            done_slots += 1
            now = time.time()
            if now - last_log >= 20 or done_slots == len(slots):
                last_log = now
                elapsed = now - t0
                print(
                    f"backfill {key} slots={done_slots}/{len(slots)} trades={counts['trades']} "
                    f"creates={counts['creates']} migrations={counts['migrations']} "
                    f"wire_mb={counts['wire_bytes']/1e6:.0f} elapsed_s={elapsed:.0f}",
                    file=sys.stderr,
                    flush=True,
                )
            if done_slots % 400 == 0 and dir_size(out_dir) >= max_bytes:
                raise RuntimeError(f"hit backfill cap {max_bytes} during {key}")


def save_pool_cache(path: Path, cache: Mapping[str, tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / "pool-mints.jsonl.tmp"
    with tmp.open("w", encoding="utf-8") as fh:
        for pool, (base, quote) in cache.items():
            fh.write(
                json.dumps(
                    {"pool": pool, "base_mint": base, "quote_mint": quote},
                    separators=(",", ":"),
                )
                + "\n"
            )
    if not cache:
        tmp.unlink(missing_ok=True)
        return
    sealed = seal_jsonl(tmp)
    if sealed is None:
        return
    sealed.replace(path)


def compare_trades(
    live: Iterable[Mapping[str, Any]],
    backfill: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Match on (signature, event_index). Amounts and identities must agree."""
    fields = (
        "venue",
        "mint",
        "trader",
        "side",
        "sol_lamports",
        "token_raw",
        "slot",
        "event_ts",
        "quote_reserve",
        "base_reserve",
    )

    def key(row: Mapping[str, Any]) -> tuple[str, int]:
        return (str(row.get("signature")), int(row.get("event_index") or 0))

    live_map: dict[tuple[str, int], Mapping[str, Any]] = {}
    live_dup = 0
    for row in live:
        k = key(row)
        if k in live_map:
            live_dup += 1
            continue
        live_map[k] = row
    back_map: dict[tuple[str, int], Mapping[str, Any]] = {}
    back_dup = 0
    for row in backfill:
        k = key(row)
        if k in back_map:
            back_dup += 1
            continue
        back_map[k] = row
    both = live_map.keys() & back_map.keys()
    match = 0
    mismatches: list[dict[str, Any]] = []
    for k in both:
        a = live_map[k]
        b = back_map[k]
        bad = [field for field in fields if a.get(field) != b.get(field)]
        if a.get("venue") == "pumpswap":
            for field in ("pool", "quote_mint", "quote_is_wsol"):
                if field in a and a.get(field) != b.get(field):
                    bad.append(field)
        if bad:
            if len(mismatches) < 8:
                mismatches.append({"signature": k[0], "event_index": k[1], "fields": bad})
        else:
            match += 1
    only_back = [k for k in back_map.keys() - live_map.keys()]
    zero_only = 0
    for k in only_back:
        if back_map[k].get("zero_sol") is True:
            zero_only += 1
    return {
        "live": len(live_map),
        "backfill": len(back_map),
        "both": len(both),
        "match": match,
        "mismatch": len(both) - match,
        "only_live": len(live_map.keys() - back_map.keys()),
        "only_backfill": len(only_back),
        "only_backfill_zero_sol": zero_only,
        "live_dup": live_dup,
        "backfill_dup": back_dup,
        "mismatches": mismatches,
    }


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if path.suffix == ".zst" or path.name.endswith(".jsonl.zst"):
        proc = subprocess.Popen(["zstdcat", str(path)], stdout=subprocess.PIPE, text=True)
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if line.strip():
                    yield json.loads(line)
        finally:
            proc.stdout.close()
            proc.wait()
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def parse_utc(text: str) -> int:
    raw = text.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill pump.fun history from public RPC getBlock")
    parser.add_argument("--until", required=True, help="Exclusive UTC end, ISO-8601 (newest edge)")
    parser.add_argument("--hours", type=int, default=1)
    parser.add_argument("--out", type=Path, default=Path("/tmp/mal-backfill"))
    parser.add_argument("--rpc", default=DEFAULT_RPC)
    parser.add_argument("--rps", type=float, default=3.2, help="getBlock requests per second")
    parser.add_argument("--lookup-rps", type=float, default=3.2, help="getMultipleAccounts requests per second")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--anchor-slot", type=int, default=450278777)
    parser.add_argument("--anchor-time", type=int, default=1790319576)
    args = parser.parse_args(argv)
    if args.hours < 1:
        raise SystemExit("--hours must be >= 1")
    until = parse_utc(args.until)
    limiter = RateLimiter(args.rps)
    lookup_limiter = RateLimiter(args.lookup_rps)
    cache_path = args.out / "pools" / "pool-mints.jsonl.zst"
    pool_mints = load_pool_cache(cache_path)
    args.out.mkdir(parents=True, exist_ok=True)
    summaries = []
    for i in range(args.hours):
        end_ts = until - i * 3600
        start_ts = end_ts - 3600
        print(
            f"hour {hour_key(start_ts)} {datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()} "
            f".. {datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()}",
            file=sys.stderr,
            flush=True,
        )
        summary = run_hour(
            url=args.rpc,
            start_ts=start_ts,
            end_ts=end_ts,
            out_dir=args.out,
            limiter=limiter,
            lookup_limiter=lookup_limiter,
            pool_mints=pool_mints,
            workers=args.workers,
            max_bytes=args.max_bytes,
            anchor_slot=args.anchor_slot,
            anchor_time=args.anchor_time,
        )
        summaries.append(summary)
        save_pool_cache(cache_path, pool_mints)
        print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
