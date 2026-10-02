"""Swappable raw-trade feeds.

Both feeds normalize to RawNotice (slot, signature, logs, receive time).
The recorder decodes program logs; it does not depend on which socket
delivered them.

- public_rpc_logs: Solana logsSubscribe mentions on pump.fun + PumpSwap. $0.
- helius_tx: Helius transactionSubscribe (Developer plan). Requires
  HELIUS_API_KEY already in the environment. This module never creates an
  account and never logs the key.
"""

from __future__ import annotations

import asyncio
import json
import collections
import logging
import random
import ssl
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

import certifi
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

from observe.trade_decode import TRADE_PROGRAMS

log = logging.getLogger("mal.trade_tape")


def _safe_err(exc: BaseException) -> str:
    text = str(exc)
    marker = "api-key="
    if marker in text:
        head, _, tail = text.partition(marker)
        rest = tail.split("&", 1)
        tail_out = f"&{rest[1]}" if len(rest) > 1 else ""
        text = f"{head}{marker}REDACTED{tail_out}"
    return text[:300]

SOURCE_PUBLIC_RPC_LOGS = "public_rpc_logs"
SOURCE_HELIUS_TX = "helius_tx"
SOURCES = (SOURCE_PUBLIC_RPC_LOGS, SOURCE_HELIUS_TX)

DEFAULT_PUBLIC_WS = "wss://api.mainnet-beta.solana.com"
DEFAULT_HELIUS_WS = "wss://mainnet.helius-rpc.com"

INITIAL_BACKOFF_S = 1.0
MAX_BACKOFF_S = 60.0


@dataclass(frozen=True)
class RawNotice:
    slot: int
    signature: str
    failed: bool
    logs: tuple[str, ...]
    t_recv_ms: int
    commitment: str
    feed: str


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def logs_subscribe_request(program: str, commitment: str, req_id: int) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "logsSubscribe",
        "params": [{"mentions": [program]}, {"commitment": commitment}],
    }


def helius_subscribe_request(programs: Sequence[str], commitment: str, req_id: int = 1) -> dict[str, Any]:
    """Program-filtered transactionSubscribe. accountInclude is a match-any list."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "transactionSubscribe",
        "params": [
            {
                "vote": False,
                "failed": False,
                "accountInclude": list(programs),
            },
            {
                "commitment": commitment,
                "encoding": "json",
                "transactionDetails": "full",
                "showRewards": False,
                "maxSupportedTransactionVersion": 1,
            },
        ],
    }


def helius_ws_url(api_key: str, base: str = DEFAULT_HELIUS_WS) -> str:
    return f"{base.rstrip('/')}/?api-key={api_key}"


def parse_logs_notification(payload: Mapping[str, Any], t_recv_ms: int, commitment: str) -> RawNotice | None:
    if payload.get("method") != "logsNotification":
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    result = params.get("result")
    if not isinstance(result, dict):
        return None
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    value = result.get("value") if isinstance(result.get("value"), dict) else {}
    slot = context.get("slot")
    signature = value.get("signature")
    logs = value.get("logs")
    if not isinstance(slot, int) or not isinstance(signature, str):
        return None
    if not isinstance(logs, list):
        logs = []
    lines = tuple(line for line in logs if isinstance(line, str))
    return RawNotice(
        slot=slot,
        signature=signature,
        failed=value.get("err") is not None,
        logs=lines,
        t_recv_ms=t_recv_ms,
        commitment=commitment,
        feed=SOURCE_PUBLIC_RPC_LOGS,
    )


def _logs_from_meta(meta: Mapping[str, Any]) -> tuple[str, ...]:
    raw = meta.get("logMessages")
    if not isinstance(raw, list):
        raw = meta.get("logs")
    if not isinstance(raw, list):
        return ()
    return tuple(line for line in raw if isinstance(line, str))


def parse_helius_notification(payload: Mapping[str, Any], t_recv_ms: int, commitment: str) -> RawNotice | None:
    """Normalize a transactionNotification into the same notice the log feed uses."""
    if payload.get("method") != "transactionNotification":
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    result = params.get("result")
    if not isinstance(result, dict):
        return None
    slot = result.get("slot")
    if not isinstance(slot, int):
        context = result.get("context") if isinstance(result.get("context"), dict) else {}
        slot = context.get("slot")
    if not isinstance(slot, int):
        return None
    signature = result.get("signature") if isinstance(result.get("signature"), str) else None
    meta: Mapping[str, Any] | None = None
    tx = result.get("transaction")
    if isinstance(tx, dict):
        if isinstance(tx.get("meta"), dict):
            meta = tx["meta"]
        if signature is None:
            inner = tx.get("transaction")
            if isinstance(inner, dict):
                sigs = inner.get("signatures")
                if isinstance(sigs, list) and sigs and isinstance(sigs[0], str):
                    signature = sigs[0]
    if meta is None and isinstance(result.get("meta"), dict):
        meta = result["meta"]
    if meta is None or signature is None:
        return None
    return RawNotice(
        slot=slot,
        signature=signature,
        failed=meta.get("err") is not None,
        logs=_logs_from_meta(meta),
        t_recv_ms=t_recv_ms,
        commitment=commitment,
        feed=SOURCE_HELIUS_TX,
    )


class _ReconnectStats:
    def __init__(self) -> None:
        self.reconnects = 0
        self.notes = 0
        self.failed_notes = 0
        self.slot_jumps = 0
        self.last_slot: int | None = None
        # Handshake rejections by HTTP status (e.g. {413: 3}). Counted in every
        # feed; only the multi-socket heartbeat prints it.
        self.rejections: dict[int, int] = {}

    def observe(self, note: RawNotice) -> None:
        self.notes += 1
        if note.failed:
            self.failed_notes += 1
        if self.last_slot is not None and note.slot > self.last_slot + 2:
            self.slot_jumps += 1
            log.info("slot_jump from=%s to=%s feed=%s", self.last_slot, note.slot, note.feed)
        if self.last_slot is None or note.slot > self.last_slot:
            self.last_slot = note.slot


async def _iter_ws(
    url: str,
    subscribe_payloads: Sequence[Mapping[str, Any]],
    parse,
    commitment: str,
    feed: str,
    stop: asyncio.Event,
    stats: _ReconnectStats,
    *,
    log_url: str,
    jitter: Callable[[float], float] | None = None,
    label: str = "",
) -> AsyncIterator[RawNotice]:
    # jitter=None keeps the single-socket sleep exactly as before. label is
    # appended to log lines only when set (multi-socket runs).
    tag = f" {label}" if label else ""
    backoff = INITIAL_BACKOFF_S
    max_backoff = MAX_BACKOFF_S
    while not stop.is_set():
        try:
            log.info("ws_connect feed=%s url=%s%s", feed, log_url, tag)
            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                open_timeout=20,
                max_size=8_000_000,
                ssl=_ssl_context(),
            ) as ws:
                backoff = INITIAL_BACKOFF_S
                for payload in subscribe_payloads:
                    await ws.send(json.dumps(payload))
                    log.info("subscribed feed=%s method=%s id=%s", feed, payload.get("method"), payload.get("id"))
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        log.warning("ws_idle feed=%s last_slot=%s", feed, stats.last_slot)
                        break
                    t_recv_ms = time_ns_ms()
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        log.warning("ws_invalid_json feed=%s", feed)
                        continue
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("method") is None and "error" in msg:
                        log.warning("ws_rpc_error feed=%s error=%s", feed, msg.get("error"))
                        continue
                    note = parse(msg, t_recv_ms, commitment)
                    if note is None:
                        continue
                    stats.observe(note)
                    yield note
        except ConnectionClosed as exc:
            log.warning(
                "ws_closed feed=%s code=%s reason=%s last_slot=%s%s",
                feed,
                exc.code,
                _safe_err(Exception(exc.reason or "")),
                stats.last_slot,
                tag,
            )
        except InvalidStatusCode as exc:
            stats.rejections[exc.status_code] = stats.rejections.get(exc.status_code, 0) + 1
            log.warning("ws_handshake_rejected feed=%s status_code=%s%s", feed, exc.status_code, tag)
        except OSError as exc:
            log.warning("ws_error feed=%s err=%s", feed, _safe_err(exc))
        if stop.is_set():
            break
        stats.reconnects += 1
        delay = backoff if jitter is None else jitter(backoff)
        log.info("ws_reconnect feed=%s sleep_s=%.1f reconnects=%s%s", feed, delay, stats.reconnects, tag)
        await asyncio.sleep(delay)
        backoff = min(backoff * 2, max_backoff)


def time_ns_ms() -> int:
    return time.time_ns() // 1_000_000


class LogsSubscribeSource:
    """Public RPC logsSubscribe. One subscription per program (mentions allows one address)."""

    def __init__(
        self,
        ws_url: str = DEFAULT_PUBLIC_WS,
        programs: Sequence[str] = TRADE_PROGRAMS,
        commitment: str = "confirmed",
    ) -> None:
        self.ws_url = ws_url
        self.programs = tuple(programs)
        self.commitment = commitment
        self.stats = _ReconnectStats()

    def subscribe_payloads(self) -> list[dict[str, Any]]:
        return [
            logs_subscribe_request(program, self.commitment, index)
            for index, program in enumerate(self.programs, start=1)
        ]

    async def notices(self, stop: asyncio.Event) -> AsyncIterator[RawNotice]:
        async for note in _iter_ws(
            self.ws_url,
            self.subscribe_payloads(),
            parse_logs_notification,
            self.commitment,
            SOURCE_PUBLIC_RPC_LOGS,
            stop,
            self.stats,
            log_url=self.ws_url.split("?")[0],
        ):
            yield note


class SeenSet:
    """Time-windowed, size-capped set of keys. Memory stays bounded.

    Keys expire window_s after first sight, and the oldest keys go first when
    max_keys is hit. Insertion order is time order, so expiry is a popleft loop.
    """

    def __init__(self, window_s: float = 600.0, max_keys: int = 500_000,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.window_s = window_s
        self.max_keys = max_keys
        self._clock = clock
        self._first: collections.OrderedDict[str, float] = collections.OrderedDict()

    def __len__(self) -> int:
        return len(self._first)

    def _expire(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._first:
            key, seen_at = next(iter(self._first.items()))
            if seen_at > cutoff:
                break
            del self._first[key]

    def add(self, key: str) -> bool:
        """True when key is new (and is now remembered), False for a duplicate."""
        now = self._clock()
        self._expire(now)
        if key in self._first:
            return False
        self._first[key] = now
        while len(self._first) > self.max_keys:
            self._first.popitem(last=False)
        return True


class SocketStats(_ReconnectStats):
    """Per-socket counters. unique = notices this socket delivered first."""

    def __init__(self, index: int, url: str) -> None:
        super().__init__()
        self.index = index
        self.url = url
        self.unique = 0

    def row(self) -> dict[str, Any]:
        return {
            "i": self.index,
            "reconnects": self.reconnects,
            "rejections": {str(code): n for code, n in sorted(self.rejections.items())},
            "notes": self.notes,
            "unique": self.unique,
        }


class MergedStats:
    """Source-level stats for N sockets. notes/failed_notes/slot_jumps count the
    merged (deduplicated) stream, so they match what the recorder decodes;
    reconnects is the sum over sockets."""

    def __init__(self, sockets: Sequence[SocketStats]) -> None:
        self.sockets = list(sockets)
        self._merged = _ReconnectStats()
        self.dedup_dropped = 0

    notes = property(lambda self: self._merged.notes)
    failed_notes = property(lambda self: self._merged.failed_notes)
    slot_jumps = property(lambda self: self._merged.slot_jumps)
    last_slot = property(lambda self: self._merged.last_slot)
    reconnects = property(lambda self: sum(s.reconnects for s in self.sockets))

    def observe(self, note: RawNotice) -> None:
        self._merged.observe(note)

    def socket_rows(self) -> list[dict[str, Any]]:
        return [s.row() for s in self.sockets]

    def summary(self) -> str:
        parts = []
        for s in self.sockets:
            rej = ",".join(f"{code}x{n}" for code, n in sorted(s.rejections.items())) or "0"
            parts.append(
                f"s{s.index}[reconnects={s.reconnects} rejected={rej} "
                f"notes={s.notes} unique={s.unique}]"
            )
        return " ".join(parts)


class MultiSocketLogsSource:
    """N redundant public logsSubscribe sockets merged into one notice stream.

    Each socket has its own connect, subscribe and reconnect loop (jittered
    backoff, staggered first connect). Notices are deduplicated by transaction
    signature before decode; the first to reach the merge wins, so t_recv_ms is
    that socket's own receive stamp. Not used unless --sockets > 1.
    """

    def __init__(
        self,
        ws_urls: Sequence[str] = (DEFAULT_PUBLIC_WS,),
        sockets: int = 2,
        programs: Sequence[str] = TRADE_PROGRAMS,
        commitment: str = "confirmed",
        stagger_s: float = 3.0,
        seen_window_s: float = 600.0,
        max_seen: int = 500_000,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if sockets < 1:
            raise ValueError("sockets must be >= 1")
        urls = list(ws_urls) or [DEFAULT_PUBLIC_WS]
        self.ws_urls = [urls[i % len(urls)] for i in range(sockets)]
        self.programs = tuple(programs)
        self.commitment = commitment
        self.stagger_s = stagger_s
        self._rng = rng or random.Random()
        self.seen = SeenSet(seen_window_s, max_seen, clock)
        self.socket_stats = [SocketStats(i, u) for i, u in enumerate(self.ws_urls)]
        self.stats = MergedStats(self.socket_stats)

    def subscribe_payloads(self) -> list[dict[str, Any]]:
        return [
            logs_subscribe_request(program, self.commitment, index)
            for index, program in enumerate(self.programs, start=1)
        ]

    def _jitter(self, backoff: float) -> float:
        # Full-ish jitter: 50-100% of the nominal backoff, so sockets that
        # dropped together do not come back together.
        return backoff * (0.5 + 0.5 * self._rng.random())

    async def _pump(self, index: int, queue: "asyncio.Queue[tuple[int, RawNotice]]", stop: asyncio.Event) -> None:
        if index and self.stagger_s > 0:
            await asyncio.sleep(self.stagger_s * index)
        url = self.ws_urls[index]
        async for note in _iter_ws(
            url,
            self.subscribe_payloads(),
            parse_logs_notification,
            self.commitment,
            SOURCE_PUBLIC_RPC_LOGS,
            stop,
            self.socket_stats[index],
            log_url=url.split("?")[0],
            jitter=self._jitter,
            label=f"socket={index}",
        ):
            await queue.put((index, note))

    async def notices(self, stop: asyncio.Event) -> AsyncIterator[RawNotice]:
        queue: asyncio.Queue[tuple[int, RawNotice]] = asyncio.Queue(maxsize=10_000)
        tasks = [asyncio.create_task(self._pump(i, queue, stop)) for i in range(len(self.ws_urls))]
        try:
            while True:
                try:
                    index, note = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if queue.empty() and (stop.is_set() or all(t.done() for t in tasks)):
                        break
                    continue
                if not self.seen.add(note.signature):
                    self.stats.dedup_dropped += 1
                    continue
                self.socket_stats[index].unique += 1
                self.stats.observe(note)
                yield note
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


class HeliusTransactionSource:
    """Helius transactionSubscribe for the same programs. Key stays in the URL only."""

    def __init__(
        self,
        api_key: str,
        programs: Sequence[str] = TRADE_PROGRAMS,
        commitment: str = "confirmed",
        ws_base: str = DEFAULT_HELIUS_WS,
    ) -> None:
        if not api_key or not api_key.strip():
            raise RuntimeError("helius_tx source needs HELIUS_API_KEY; refusing to start a paid feed")
        self._url = helius_ws_url(api_key.strip(), ws_base)
        self.programs = tuple(programs)
        self.commitment = commitment
        self.stats = _ReconnectStats()

    def subscribe_payloads(self) -> list[dict[str, Any]]:
        return [helius_subscribe_request(self.programs, self.commitment, 1)]

    async def notices(self, stop: asyncio.Event) -> AsyncIterator[RawNotice]:
        async for note in _iter_ws(
            self._url,
            self.subscribe_payloads(),
            parse_helius_notification,
            self.commitment,
            SOURCE_HELIUS_TX,
            stop,
            self.stats,
            log_url=DEFAULT_HELIUS_WS,
        ):
            yield note
