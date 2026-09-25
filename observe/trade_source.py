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
import logging
import ssl
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping, Sequence

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
) -> AsyncIterator[RawNotice]:
    backoff = 1.0
    max_backoff = 60.0
    while not stop.is_set():
        try:
            log.info("ws_connect feed=%s url=%s", feed, log_url)
            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                open_timeout=20,
                max_size=8_000_000,
                ssl=_ssl_context(),
            ) as ws:
                backoff = 1.0
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
                "ws_closed feed=%s code=%s reason=%s last_slot=%s",
                feed,
                exc.code,
                _safe_err(Exception(exc.reason or "")),
                stats.last_slot,
            )
        except InvalidStatusCode as exc:
            log.warning("ws_handshake_rejected feed=%s status_code=%s", feed, exc.status_code)
        except OSError as exc:
            log.warning("ws_error feed=%s err=%s", feed, _safe_err(exc))
        if stop.is_set():
            break
        stats.reconnects += 1
        log.info("ws_reconnect feed=%s sleep_s=%.1f reconnects=%s", feed, backoff, stats.reconnects)
        await asyncio.sleep(backoff)
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
