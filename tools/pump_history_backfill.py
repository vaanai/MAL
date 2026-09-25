#!/usr/bin/env python3
"""Backfill pump.fun trades, creates, and migrations from getBlock.

Paper only. Does not start or edit the live tape recorder. Rows match the
sealed tape schema (bonding v1, PumpSwap v2 via stored_trade) with
source=backfill, null receive time, and block_time set from the block.

Public mainnet-beta getBlock is archival and returns program logs. When
HELIUS_API_KEY is set, the RPC URL is built from that key and is never
printed. A Helius bulk run refuses to start until --credits-per-getblock
is set from a probe (or credit-probe.json confirms it).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import json
import os
import re
import shutil
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
HELIUS_HTTP = "https://mainnet.helius-rpc.com"
FEED = "public_rpc_getblock"
FEED_HELIUS = "helius_getblock"
SOURCE = "backfill"
# Published historical getBlock price. Confirm with --probe-credits before a bulk run.
PUBLISHED_GETBLOCK_CREDITS = 1
DEFAULT_CREDIT_CAP = 7_000_000
BUDGET_CODE = -2
CHECKPOINT_EVERY = 25
_API_KEY_RE = re.compile(r"(api-key=)[^&\s\"']+", re.IGNORECASE)
# Same window the live decoder accepts. See observe.trade_decode.
_TS_MIN = 1_700_000_000
_TS_MAX = 1_900_000_000
_CREATE_DISC = bytes.fromhex("1b72a94ddeeb6376")
_COMPLETE_DISC = bytes.fromhex("5f72619cd42e9808")
_MIGRATE_DISC = bytes.fromhex("bde95db95c94ea94")
_SKIP_CODES = frozenset({-32007, -32009, -32004})
# Create/complete/migration events store native SOL as the zero pubkey.
_NATIVE_SOL = "11111111111111111111111111111111"
DEFAULT_MAX_BYTES = 40 * 1024**3
HEADROOM_RATIO = 0.20


def budget_bytes(volume_total: int, volume_avail: int, cap_bytes: int = DEFAULT_MAX_BYTES) -> int:
    """Bytes we may still write. Cap, and leave 20% of the volume free."""
    reserve = int(volume_total * HEADROOM_RATIO)
    by_headroom = volume_avail - reserve
    if by_headroom < 0:
        by_headroom = 0
    return min(int(cap_bytes), by_headroom)


def helius_http_url(api_key: str, base: str = HELIUS_HTTP) -> str:
    """RPC URL for a Helius key. Caller must not log the return value."""
    key = api_key.strip()
    if not key or any(ch in key for ch in "\r\n& #"):
        raise ValueError("HELIUS_API_KEY is empty or unsafe")
    return f"{base.rstrip('/')}/?api-key={key}"


def redact_rpc_url(text: str) -> str:
    return _API_KEY_RE.sub(r"\1REDACTED", text)


def resolve_rpc_url(explicit: str | None, environ: Mapping[str, str]) -> tuple[str, str]:
    """Return (url, kind). An explicit --rpc wins. Otherwise use HELIUS_API_KEY."""
    if explicit:
        kind = "helius" if "helius-rpc.com" in explicit else "public"
        return explicit, kind
    key = (environ.get("HELIUS_API_KEY") or "").strip()
    if key:
        return helius_http_url(key), "helius"
    return DEFAULT_RPC, "public"


def plan_hours(until_ts: int, hours: int) -> list[tuple[int, int]]:
    """Newest hour first. Each pair is [start, end) in unix seconds."""
    if hours < 1:
        raise ValueError("hours must be >= 1")
    out: list[tuple[int, int]] = []
    for i in range(hours):
        end = until_ts - i * 3600
        out.append((end - 3600, end))
    return out


class CreditBudgetExceeded(RuntimeError):
    def __init__(self, used: int, cap: int) -> None:
        super().__init__(f"credit cap reached: used={used} cap={cap}")
        self.used = used
        self.cap = cap


class CreditBudget:
    """Hard cap on RPC credits. per_call=0 (public RPC) never trips the cap."""

    def __init__(self, cap: int, per_call: int, used: int = 0) -> None:
        if cap < 0 or per_call < 0 or used < 0:
            raise ValueError("credit budget must be >= 0")
        self.cap = int(cap)
        self.per_call = int(per_call)
        self.used = int(used)
        self._lock = threading.Lock()

    def can_afford(self) -> bool:
        with self._lock:
            return self.used + self.per_call <= self.cap

    def reserve(self) -> bool:
        """Count one RPC attempt. False means the call must not be sent."""
        with self._lock:
            if self.used + self.per_call > self.cap:
                return False
            self.used += self.per_call
            return True


def empty_checkpoint() -> dict[str, Any]:
    return {"version": 1, "credits_used": 0, "credits_per_getblock": None, "hours": {}}


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return empty_checkpoint()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return empty_checkpoint()
    data.setdefault("version", 1)
    data.setdefault("credits_used", 0)
    data.setdefault("credits_per_getblock", None)
    data.setdefault("hours", {})
    return data


def save_checkpoint(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def filesystem_room(out_dir: Path, cap_bytes: int) -> int:
    """Bytes still allowed under the directory cap and the 20% free reserve."""
    out_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(out_dir)
    headroom = budget_bytes(usage.total, usage.free, cap_bytes)
    by_cap = cap_bytes - dir_size(out_dir)
    if by_cap < 0:
        by_cap = 0
    return min(headroom, by_cap)


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
        if parsed != _NATIVE_SOL:
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
        "quote_mint": _quote_or_wsol(b58encode(raw[112:144])),
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
        "quote_mint": _quote_or_wsol(b58encode(raw[168:200])),
    }


def _quote_or_wsol(mint: str) -> str:
    return WSOL_MINT if mint == _NATIVE_SOL else mint


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
    feed: str = FEED,
) -> dict[str, Any]:
    row = dict(ev)
    row["v"] = 1
    row["source"] = SOURCE
    row["feed"] = feed
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
    feed: str = FEED,
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
                _stamp_lifecycle(
                    ev, slot=slot, signature=sig, event_index=index, block_time=block_time, feed=feed
                )
            )
        for index, ev in enumerate(moved):
            migrations.append(
                _stamp_lifecycle(
                    ev, slot=slot, signature=sig, event_index=index, block_time=block_time, feed=feed
                )
            )
        decoded = records_from_logs(
            logs,
            slot=slot,
            signature=sig,
            t_recv_ms=0,
            commitment="confirmed",
            feed=feed,
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
    """Append compact JSONL and zstd-seal it on close. Empty files are removed.

    resume_bytes truncates to a checkpoint offset and appends. A partial hour
    stays plain JSONL until the hour is fully consumed.
    """

    def __init__(self, path: Path, resume_bytes: int | None = None) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._base = 0
        if resume_bytes is None:
            self._fh = path.open("w", encoding="utf-8")
        else:
            keep = max(0, int(resume_bytes))
            if path.is_file():
                with path.open("r+b") as raw:
                    raw.truncate(keep)
            else:
                path.touch()
            self._fh = path.open("a", encoding="utf-8")
            self._base = keep
        self.rows = 0
        self.bytes = 0

    def write(self, row: Mapping[str, Any]) -> None:
        line = json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n"
        self._fh.write(line)
        self.rows += 1
        self.bytes += len(line.encode("utf-8"))

    def offset(self) -> int:
        self._fh.flush()
        return self._fh.tell()

    def close(self, *, seal: bool = True) -> Path | None:
        self._fh.close()
        if not seal:
            if self.rows == 0 and self._base == 0:
                self.path.unlink(missing_ok=True)
                return None
            return self.path if self.path.is_file() else None
        if self.rows == 0 and self._base == 0:
            self.path.unlink(missing_ok=True)
            return None
        if not self.path.is_file() or self.path.stat().st_size == 0:
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


def _note_credit_headers(resp: Any, header_out: dict[str, Any] | None) -> None:
    if header_out is None:
        return
    found = header_out.setdefault("credits", [])
    for key, value in resp.headers.items():
        if "credit" not in key.lower():
            continue
        found.append({"name": key.lower(), "value": str(value)[:40]})


def rpc_call(
    url: str,
    method: str,
    params: list[Any],
    limiter: RateLimiter | None,
    timeout: float = 60.0,
    budget: CreditBudget | None = None,
    header_out: dict[str, Any] | None = None,
) -> tuple[Any, int, int | None]:
    """Return (result, wire_bytes, error_code). Retries HTTP 429.

    Each attempt reserves one credit before the request is sent. The URL is
    never included in raised errors.
    """
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last_code: int | None = None
    for attempt in range(6):
        if budget is not None and not budget.reserve():
            raise CreditBudgetExceeded(budget.used, budget.cap)
        if limiter is not None:
            limiter.acquire()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept-Encoding": "gzip"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                _note_credit_headers(resp, header_out)
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            last_code = exc.code
            if exc.code == 429 and attempt < 5:
                time.sleep(0.4 * (2**attempt))
                continue
            if attempt < 5 and exc.code >= 500:
                time.sleep(0.6 * (2**attempt))
                continue
            raise RuntimeError(f"{method} http {exc.code}") from None
        except (TimeoutError, urllib.error.URLError, ConnectionError, json.JSONDecodeError) as exc:
            if attempt < 5:
                time.sleep(0.6 * (2**attempt))
                continue
            raise RuntimeError(redact_rpc_url(f"{method} transport {type(exc).__name__}")) from None
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


def _getblock_params(slot: int, *, full: bool) -> list[Any]:
    return [
        slot,
        {
            "encoding": "json",
            "transactionDetails": "full" if full else "none",
            "rewards": False,
            "commitment": "confirmed",
            "maxSupportedTransactionVersion": 1,
        },
    ]


def fetch_block(
    url: str,
    slot: int,
    limiter: RateLimiter,
    budget: CreditBudget | None = None,
    header_out: dict[str, Any] | None = None,
    *,
    full: bool = True,
) -> tuple[dict[str, Any] | None, int, int | None]:
    try:
        result, wire, code = rpc_call(
            url,
            "getBlock",
            _getblock_params(slot, full=full),
            limiter,
            timeout=90.0,
            budget=budget,
            header_out=header_out,
        )
    except CreditBudgetExceeded:
        return None, 0, BUDGET_CODE
    except (RuntimeError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, gzip.BadGzipFile) as exc:
        print(
            redact_rpc_url(f"getBlock slot={slot} failed {type(exc).__name__}: {exc}"),
            file=sys.stderr,
            flush=True,
        )
        return None, 0, -1
    if not isinstance(result, dict):
        return None, wire, code
    result["slot"] = slot
    return result, wire, code


def block_time_of(
    url: str,
    slot: int,
    limiter: RateLimiter,
    budget: CreditBudget | None = None,
) -> int | None:
    result, _wire, _code = rpc_call(
        url,
        "getBlock",
        _getblock_params(slot, full=False),
        limiter,
        timeout=30.0,
        budget=budget,
    )
    if not isinstance(result, dict):
        return None
    bt = result.get("blockTime")
    return bt if isinstance(bt, int) else None


def slot_for_time(
    url: str,
    target: int,
    anchor_slot: int,
    anchor_time: int,
    limiter: RateLimiter,
    budget: CreditBudget | None = None,
) -> int:
    """First slot whose blockTime is >= target. Public RPC has a block per recent slot."""
    span = max(target - anchor_time, 0)
    guess = anchor_slot + int(span / 0.27)
    lo = max(0, guess - 5000)
    hi = guess + 5000
    # Expand until the target is inside (lo_time, hi_time).
    for _ in range(8):
        lo_t = block_time_of(url, lo, limiter, budget)
        hi_t = block_time_of(url, hi, limiter, budget)
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
        bt = block_time_of(url, mid, limiter, budget)
        if bt is None or bt < target:
            lo = mid + 1
        else:
            best = mid
            hi = mid - 1
    return best


def slots_between(
    url: str,
    start: int,
    end: int,
    limiter: RateLimiter,
    budget: CreditBudget | None = None,
) -> list[int]:
    if end < start:
        return []
    out: list[int] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + 4999)
        result, _wire, _code = rpc_call(
            url, "getBlocks", [cursor, chunk_end], limiter, timeout=60.0, budget=budget
        )
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


def consume_slots(
    slots: Sequence[int],
    workers: int,
    fetch: Callable[[int], tuple[dict[str, Any] | None, int, int | None]],
    consume: Callable[[dict[str, Any] | None, int, int | None], None],
    stop_before: Callable[[], str | None] | None = None,
    max_slots: int | None = None,
) -> tuple[int, str | None]:
    """Fetch slots concurrently and consume them in order.

    Returns (number consumed, stop_reason). A budget miss does not consume
    that slot, so a resume retries it.
    """
    if not slots:
        return 0, None
    inflight: dict[int, Any] = {}
    next_submit = 0
    next_consume = 0
    reason: str | None = None
    executor = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        while next_consume < len(slots):
            while reason is None and len(inflight) < max(1, workers) and next_submit < len(slots):
                if max_slots is not None and next_submit >= max_slots:
                    reason = "max_slots"
                    break
                if stop_before is not None:
                    why = stop_before()
                    if why:
                        reason = why
                        break
                inflight[next_submit] = executor.submit(fetch, slots[next_submit])
                next_submit += 1
            if next_consume not in inflight:
                break
            block, wire, code = inflight.pop(next_consume).result()
            if code == BUDGET_CODE:
                reason = "credit"
                break
            consume(block, wire, code)
            next_consume += 1
        while reason != "credit" and next_consume in inflight:
            block, wire, code = inflight.pop(next_consume).result()
            if code == BUDGET_CODE:
                reason = "credit"
                break
            consume(block, wire, code)
            next_consume += 1
    finally:
        for fut in inflight.values():
            fut.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
    return next_consume, reason


def projected_backfill_days(
    credit_cap: int,
    credits_per_getblock: int,
    blocks_per_day: int,
    bytes_per_day: int,
    byte_cap: int,
) -> dict[str, Any]:
    """How many recent days fit the credit cap and the byte cap."""
    if credits_per_getblock < 1 or blocks_per_day < 1 or bytes_per_day < 1:
        raise ValueError("projection inputs must be positive")
    by_credits = (credit_cap / credits_per_getblock) / blocks_per_day
    by_disk = byte_cap / bytes_per_day
    binding = "disk" if by_disk <= by_credits else "credits"
    return {
        "days_by_credits": round(by_credits, 2),
        "days_by_disk": round(by_disk, 2),
        "days": round(min(by_credits, by_disk), 2),
        "binding": binding,
    }


def parse_credit_headers(headers: Sequence[Mapping[str, str]]) -> int | None:
    """Return a single positive integer if every credit header agrees."""
    values: list[int] = []
    for item in headers:
        raw = str(item.get("value") or "").strip()
        if not raw:
            continue
        try:
            values.append(int(float(raw)))
        except ValueError:
            continue
    if not values:
        return None
    if any(v != values[0] for v in values):
        return None
    if values[0] < 1:
        return None
    return values[0]


def _locate_slots(
    url: str,
    start_ts: int,
    end_ts: int,
    limiter: RateLimiter,
    budget: CreditBudget,
    anchor_slot: int,
    anchor_time: int,
    slot_start: int | None,
    slot_end: int | None,
    checkpoint: dict[str, Any] | None,
    key: str,
) -> tuple[int, int, list[int], bool, dict[str, Any] | None] | None:
    hours = checkpoint.setdefault("hours", {}) if checkpoint is not None else {}
    partial = hours.get(key) if isinstance(hours.get(key), dict) else None
    resume = bool(partial and partial.get("status") == "partial")
    if resume and isinstance(partial.get("start_slot"), int) and isinstance(partial.get("end_slot"), int):
        start_slot = int(partial["start_slot"])
        end_slot = int(partial["end_slot"])
    elif slot_start is not None and slot_end is not None:
        start_slot = slot_start
        end_slot = slot_end
    else:
        start_slot = slot_for_time(url, start_ts, anchor_slot, anchor_time, limiter, budget)
        end_slot = slot_for_time(url, end_ts, anchor_slot, anchor_time, limiter, budget)
    slots = slots_between(url, start_slot, max(start_slot, end_slot - 1), limiter, budget)
    if resume and isinstance(partial.get("next_slot"), int):
        next_slot = int(partial["next_slot"])
        slots = [slot for slot in slots if slot >= next_slot]
    return start_slot, end_slot, slots, resume, partial if isinstance(partial, dict) else None


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
    budget: CreditBudget | None = None,
    feed: str = FEED,
    checkpoint: dict[str, Any] | None = None,
    checkpoint_path: Path | None = None,
    max_slots: int | None = None,
    slot_start: int | None = None,
    slot_end: int | None = None,
) -> dict[str, Any]:
    """Fetch [start_ts, end_ts), newest hours first at the caller. Resume from checkpoint."""
    if budget is None:
        budget = CreditBudget(DEFAULT_CREDIT_CAP, 0)
    key = hour_key(start_ts)
    stats_path = out_dir / f"stats-{key}.json"
    if stats_path.is_file():
        return json.loads(stats_path.read_text(encoding="utf-8"))
    if filesystem_room(out_dir, max_bytes) <= 0:
        return {"hour": key, "skipped": True, "stop_reason": "disk", "credits_used": budget.used}
    try:
        located = _locate_slots(
            url, start_ts, end_ts, limiter, budget, anchor_slot, anchor_time, slot_start, slot_end, checkpoint, key
        )
    except CreditBudgetExceeded:
        return {"hour": key, "skipped": True, "stop_reason": "credit", "credits_used": budget.used}
    if located is None:
        return {"hour": key, "skipped": True, "stop_reason": "credit", "credits_used": budget.used}
    start_slot, end_slot, slots, resume, partial = located

    t0 = time.time()

    for sub, prefix in (("trades", "trades"), ("creates", "creates"), ("migrations", "migrations")):
        folder = out_dir / sub
        if folder.is_dir() and not resume:
            for stale in folder.glob(f"{prefix}-{key}.jsonl*"):
                stale.unlink()
    offsets = partial.get("offsets") if resume and isinstance(partial.get("offsets"), dict) else {}

    def _sink(sub: str, prefix: str) -> JsonlSink:
        path = out_dir / sub / f"{prefix}-{key}.jsonl"
        if resume:
            raw = offsets.get(sub, 0)
            return JsonlSink(path, resume_bytes=int(raw) if isinstance(raw, int) else 0)
        return JsonlSink(path)

    trades = _sink("trades", "trades")
    creates = _sink("creates", "creates")
    migrations = _sink("migrations", "migrations")
    prior = partial.get("counts") if resume and isinstance(partial.get("counts"), dict) else {}
    counts: dict[str, Any] = {
        "hour": key,
        "block_time_start": start_ts,
        "block_time_end": end_ts,
        "start_slot": start_slot,
        "end_slot": end_slot,
        "slots": len(slots) + (int(prior.get("slots_done") or 0) if resume else 0),
        "empty": int(prior.get("empty") or 0),
        "trades": int(prior.get("trades") or 0),
        "bonding": int(prior.get("bonding") or 0),
        "pumpswap": int(prior.get("pumpswap") or 0),
        "creates": int(prior.get("creates") or 0),
        "migrations": int(prior.get("migrations") or 0),
        "completes": int(prior.get("completes") or 0),
        "unresolved_dropped": int(prior.get("unresolved_dropped") or 0),
        "wire_bytes": int(prior.get("wire_bytes") or 0),
        "errors": int(prior.get("errors") or 0),
        "slots_done": int(prior.get("slots_done") or 0),
    }
    held: list[dict[str, Any]] = []
    credit_hit = False
    seen = {"n": 0}
    room = filesystem_room(out_dir, max_bytes)

    def _persist(status: str, next_slot: int | None, stop_reason: str | None) -> None:
        if checkpoint is None or checkpoint_path is None:
            return
        entry: dict[str, Any] = {
            "status": status,
            "start_slot": start_slot,
            "end_slot": end_slot,
            "next_slot": next_slot,
            "stop_reason": stop_reason,
            "counts": {name: counts[name] for name in counts if name != "files"},
        }
        if status != "sealed":
            entry["offsets"] = {
                "trades": trades.offset(),
                "creates": creates.offset(),
                "migrations": migrations.offset(),
            }
        checkpoint.setdefault("hours", {})[key] = entry
        checkpoint["credits_used"] = budget.used
        save_checkpoint(checkpoint_path, checkpoint)

    def _lookup(pools: list[str]) -> dict[str, tuple[str, str]]:
        nonlocal credit_hit
        for attempt in range(4):
            if not budget.reserve():
                credit_hit = True
                return {}
            lookup_limiter.acquire()
            try:
                return fetch_pool_mints(url, pools)
            except (TimeoutError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as exc:
                if attempt == 3:
                    print(redact_rpc_url(f"pool lookup failed {type(exc).__name__}"), file=sys.stderr, flush=True)
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

    last_log = {"t": time.time()}

    def _consume(block: dict[str, Any] | None, wire: int, code: int | None) -> None:
        counts["wire_bytes"] += wire
        counts["slots_done"] += 1
        if block is None:
            counts["empty"] += 1
            if code not in _SKIP_CODES and code is not None:
                counts["errors"] += 1
        else:
            decoded = rows_from_block(block, pool_mints, feed)
            bt = int(block.get("blockTime") or 0)
            for rec in decoded["unresolved"]:
                rec["_block_time"] = bt
                held.append(rec)
            if len(held) >= 250:
                _flush_held()
            if start_ts <= bt < end_ts:
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
        seen["n"] += 1
        now = time.time()
        if seen["n"] % CHECKPOINT_EVERY == 0 or now - last_log["t"] >= 20:
            nxt = slots[seen["n"]] if seen["n"] < len(slots) else None
            if seen["n"] % CHECKPOINT_EVERY == 0:
                _persist("partial", nxt, None)
            if now - last_log["t"] >= 20 or seen["n"] == len(slots):
                last_log["t"] = now
                print(
                    f"backfill {key} slots={counts['slots_done']} trades={counts['trades']} "
                    f"creates={counts['creates']} migrations={counts['migrations']} "
                    f"credits={budget.used} wire_mb={counts['wire_bytes']/1e6:.0f}",
                    file=sys.stderr,
                    flush=True,
                )

    def _stop_before() -> str | None:
        if credit_hit or not budget.can_afford():
            return "credit"
        if trades.bytes + creates.bytes + migrations.bytes >= room:
            return "disk"
        return None

    consumed = 0
    stop_reason: str | None = None
    try:
        consumed, stop_reason = consume_slots(
            slots,
            workers,
            lambda slot: fetch_block(url, slot, limiter, budget),
            _consume,
            stop_before=_stop_before,
            max_slots=max_slots,
        )
        _flush_held()
    finally:
        finished = consumed == len(slots)
        if finished:
            _flush_held()
        next_slot = slots[consumed] if consumed < len(slots) else None
        if not finished:
            _persist("partial", next_slot, stop_reason)
        sealed = {
            "trades": trades.close(seal=finished),
            "creates": creates.close(seal=finished),
            "migrations": migrations.close(seal=finished),
        }
    counts["elapsed_s"] = round(time.time() - t0, 1)
    counts["credits_used"] = budget.used
    counts["stop_reason"] = stop_reason
    counts["feed"] = feed
    if finished:
        counts["files"] = {name: (str(path) if path else None) for name, path in sealed.items()}
        counts["bytes"] = dir_size(out_dir)
        stats_path.write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")
        if checkpoint is not None and checkpoint_path is not None:
            checkpoint.setdefault("hours", {})[key] = {"status": "sealed", "stop_reason": None}
            checkpoint["credits_used"] = budget.used
            save_checkpoint(checkpoint_path, checkpoint)
        return counts
    counts["resumed"] = resume
    counts["consumed_slots"] = consumed
    return counts


def read_confirmed_credits(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    value = data.get("confirmed_credits_per_getblock") if isinstance(data, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return None


def probe_getblock_credits(
    *,
    url: str,
    slots: Sequence[int],
    limiter: RateLimiter,
    out_dir: Path,
    credit_cap: int,
) -> dict[str, Any]:
    """A handful of full getBlock calls. Records credit headers. Does not confirm a price."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # The probe itself is capped at the published price times the handful, so it cannot
    # spend the backfill budget. The observed header is what a bulk run must confirm.
    budget = CreditBudget(PUBLISHED_GETBLOCK_CREDITS * max(1, len(slots)), PUBLISHED_GETBLOCK_CREDITS)
    header_out: dict[str, Any] = {}
    ok = 0
    for slot in slots:
        block, _wire, code = fetch_block(url, slot, limiter, budget, header_out, full=True)
        if isinstance(block, dict):
            ok += 1
        elif code == BUDGET_CODE:
            break
    observed = parse_credit_headers(header_out.get("credits") or [])
    assumption = observed if observed is not None else PUBLISHED_GETBLOCK_CREDITS
    report = {
        "calls_ok": ok,
        "calls": len(slots),
        "observed_credits_per_getblock": observed,
        "published_getblock_credits": PUBLISHED_GETBLOCK_CREDITS,
        "confirmed_credits_per_getblock": None,
        "credit_headers": header_out.get("credits") or [],
        "projection_if_cost_is_assumption": projected_backfill_days(
            credit_cap,
            assumption,
            13_527 * 24,
            143_478_622 * 24,
            DEFAULT_MAX_BYTES,
        ),
        "assumption_source": "response_header" if observed is not None else "published_table",
    }
    (out_dir / "credit-probe.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report

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
    parser = argparse.ArgumentParser(description="Backfill pump.fun history from getBlock")
    parser.add_argument("--until", help="Exclusive UTC end, ISO-8601 (newest edge)")
    parser.add_argument("--hours", type=int, default=1)
    parser.add_argument("--out", type=Path, default=Path("/tmp/mal-backfill"))
    parser.add_argument("--rpc", default=None, help="Override RPC URL. Default: Helius if HELIUS_API_KEY is set, else public")
    parser.add_argument("--rps", type=float, default=3.2, help="getBlock requests per second")
    parser.add_argument("--lookup-rps", type=float, default=3.2, help="getMultipleAccounts requests per second")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--credit-cap", type=int, default=DEFAULT_CREDIT_CAP)
    parser.add_argument("--credits-per-getblock", type=int, default=None)
    parser.add_argument("--credits-file", type=Path, default=None, help="credit-probe.json with confirmed_credits_per_getblock")
    parser.add_argument("--probe-credits", type=int, default=0, help="Full getBlock calls, then exit. Does not confirm a price.")
    parser.add_argument("--max-slots", type=int, default=None, help="Stop the hour after this many slots (checkpoint, do not seal)")
    parser.add_argument("--slot-start", type=int, default=None)
    parser.add_argument("--slot-end", type=int, default=None, help="Exclusive slot end when paired with --slot-start")
    parser.add_argument("--anchor-slot", type=int, default=450278777)
    parser.add_argument("--anchor-time", type=int, default=1790319576)
    args = parser.parse_args(argv)
    if args.hours < 1:
        raise SystemExit("--hours must be >= 1")
    url, kind = resolve_rpc_url(args.rpc, os.environ)
    feed = FEED_HELIUS if kind == "helius" else FEED
    limiter = RateLimiter(args.rps)
    if args.probe_credits:
        n = args.probe_credits
        slots = [args.anchor_slot + i for i in range(n)]
        report = probe_getblock_credits(
            url=url,
            slots=slots,
            limiter=limiter,
            out_dir=args.out,
            credit_cap=args.credit_cap,
        )
        print(json.dumps(report), flush=True)
        return 0
    if args.until is None:
        print("--until is required for a backfill", file=sys.stderr)
        return 2
    per_call = 0
    if kind == "helius":
        per_call = args.credits_per_getblock
        if per_call is None and args.credits_file is not None:
            per_call = read_confirmed_credits(args.credits_file)
        if per_call is None:
            print(
                "Helius bulk backfill needs a confirmed getBlock credit cost. "
                "Run --probe-credits 5, check the dashboard delta, then pass "
                "--credits-per-getblock N or set confirmed_credits_per_getblock "
                "in credit-probe.json. Not starting.",
                file=sys.stderr,
            )
            return 2
    until = parse_utc(args.until)
    checkpoint_path = args.out / "checkpoint.json"
    checkpoint = load_checkpoint(checkpoint_path)
    budget = CreditBudget(args.credit_cap, per_call, used=int(checkpoint.get("credits_used") or 0))
    if kind == "helius" and not budget.can_afford():
        print(f"credit cap already reached: used={budget.used} cap={budget.cap}", file=sys.stderr)
        return 0
    lookup_limiter = RateLimiter(args.lookup_rps)
    cache_path = args.out / "pools" / "pool-mints.jsonl.zst"
    pool_mints = load_pool_cache(cache_path)
    args.out.mkdir(parents=True, exist_ok=True)
    for index, (start_ts, end_ts) in enumerate(plan_hours(until, args.hours)):
        if filesystem_room(args.out, args.max_bytes) <= 0:
            print(f"disk cap reached before {hour_key(start_ts)}", file=sys.stderr)
            break
        if kind == "helius" and not budget.can_afford():
            print(f"credit cap reached before {hour_key(start_ts)}: used={budget.used}", file=sys.stderr)
            break
        print(
            f"hour {hour_key(start_ts)} {datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()} "
            f".. {datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()} "
            f"feed={feed} credits_used={budget.used}",
            file=sys.stderr,
            flush=True,
        )
        summary = run_hour(
            url=url,
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
            budget=budget,
            feed=feed,
            checkpoint=checkpoint,
            checkpoint_path=checkpoint_path,
            max_slots=args.max_slots if index == 0 else None,
            slot_start=args.slot_start if index == 0 else None,
            slot_end=args.slot_end if index == 0 else None,
        )
        save_pool_cache(cache_path, pool_mints)
        safe = {k: v for k, v in summary.items() if k != "files"}
        print(json.dumps(safe), flush=True)
        if summary.get("stop_reason") in {"credit", "disk"} or summary.get("skipped"):
            break
        if args.max_slots is not None and summary.get("stop_reason") == "max_slots":
            break
    checkpoint["credits_used"] = budget.used
    save_checkpoint(checkpoint_path, checkpoint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
