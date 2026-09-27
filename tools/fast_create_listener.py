#!/usr/bin/env python3
"""Phase-1 create alarm for mal-fast-0.

PumpPortal ``subscribeNewToken`` only. Each create is one JSONL row:
mint, slot if the vendor sent one, vendor time if present, and
``t_recv_ms`` taken when the websocket frame arrived.

This process does not open Helius. A create alarm cannot name the new
mint ahead of time, and a program-wide ``preprocessedSubscribe`` is the
firehose this phase refuses (the logs firehose was ~172k credits/hour).
``CreditCounter`` trips at 20,000 credits and ``helius_filter_decision``
rejects program ids and any list that is not well under the 5,000-account
cap. Neither is wired to a socket on this pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

log = logging.getLogger("mal.fast_create")

DEFAULT_WS_URL = "wss://pumpportal.fun/api/data"
SUBSCRIBE_METHOD = "subscribeNewToken"
NARROW_ACCOUNT_MAX = 64
ACCOUNT_CAP = 5_000
CREDIT_TRIP = 20_000
PROBE_MAX_S = 3_600

# One accountInclude entry on these ids is still a program-wide stream.
PROGRAM_WIDE_DENY = frozenset(
    {
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
        "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
        "11111111111111111111111111111111",
        "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
        "ComputeBudget111111111111111111111111111111",
    }
)

VENDOR_TIME_KEYS = ("timestamp", "blockTime")


def subscribe_payload() -> dict[str, str]:
    return {"method": SUBSCRIBE_METHOD}


def phase1_helius_decision() -> dict[str, Any]:
    """Helius stays closed on this pass. Credits spent stay zero."""
    return {
        "open_socket": False,
        "reason": "no_narrow_create_filter",
        "credit_trip": CREDIT_TRIP,
        "credits_spent": 0,
        "narrow_account_max": NARROW_ACCOUNT_MAX,
        "account_cap": ACCOUNT_CAP,
        "probe_max_s": PROBE_MAX_S,
    }


def helius_filter_decision(accounts: list[str]) -> tuple[bool, str]:
    """Refuse a preprocessedSubscribe filter that is not narrow.

    A passing result is still not a create alarm: unknown mints are not
    in the list. ``phase1_helius_decision`` does not open a socket.
    """
    cleaned: list[str] = []
    for raw in accounts:
        if not isinstance(raw, str):
            continue
        account = raw.strip()
        if not account or account in cleaned:
            continue
        cleaned.append(account)
    if not cleaned:
        return False, "empty"
    if len(cleaned) > NARROW_ACCOUNT_MAX:
        return False, "over_narrow_cap"
    if any(account in PROGRAM_WIDE_DENY for account in cleaned):
        return False, "program_wide"
    return True, "narrow_but_not_a_create_alarm"


class CreditCounter:
    """Hard stop. Trips when ``used`` reaches ``trip`` (default 20_000)."""

    def __init__(self, trip: int = CREDIT_TRIP) -> None:
        if trip < 1:
            raise ValueError("trip must be >= 1")
        self.trip = trip
        self.used = 0
        self.tripped = False

    def add(self, credits: int) -> bool:
        if credits < 0:
            raise ValueError("credits must be >= 0")
        self.used += credits
        if self.used >= self.trip:
            self.tripped = True
        return self.tripped


def _as_slot(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def _vendor_time(payload: Mapping[str, Any]) -> Any:
    for key in VENDOR_TIME_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if value is None:
            continue
        return value
    return None


def create_record(payload: Mapping[str, Any], t_recv_ms: int) -> dict[str, Any] | None:
    """One create row. Non-creates and mint-less frames are dropped."""
    if t_recv_ms < 0:
        raise ValueError("t_recv_ms must be >= 0")
    tx = payload.get("txType")
    if isinstance(tx, str) and tx.strip().lower() not in ("", "create"):
        return None
    mint = payload.get("mint")
    if not isinstance(mint, str) or not mint.strip():
        return None
    return {
        "schema": "fast_create_v0",
        "source": "pumpportal_ws",
        "stream": SUBSCRIBE_METHOD,
        "mint": mint.strip(),
        "slot": _as_slot(payload.get("slot")),
        "vendor_time": _vendor_time(payload),
        "t_recv_ms": int(t_recv_ms),
        "tx_type": "create",
    }


def _normalize_messages(raw: str | bytes) -> list[dict[str, Any]]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    data = json.loads(raw)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def jsonl_path(output_dir: Path, now: datetime | None = None) -> Path:
    moment = now or datetime.now(timezone.utc)
    day = moment.strftime("%Y-%m-%d")
    return output_dir / f"fast-create-{day}.jsonl"


def append_record(output_dir: Path, record: Mapping[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = jsonl_path(output_dir)
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False, default=str)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


def _ssl_context() -> Any:
    import ssl

    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


async def run_client(ws_url: str, output_dir: Path, stop: asyncio.Event) -> None:
    import websockets
    from websockets.exceptions import ConnectionClosed

    decision = phase1_helius_decision()
    if decision["open_socket"]:
        raise RuntimeError("helius socket is not allowed on this pass")
    log.info(
        "helius_skipped reason=%s credits_spent=%s trip=%s",
        decision["reason"],
        decision["credits_spent"],
        decision["credit_trip"],
    )
    backoff = 1.0
    max_backoff = 60.0
    while not stop.is_set():
        try:
            log.info("ws_connect url=%s", ws_url.split("?")[0])
            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                open_timeout=20,
                ssl=_ssl_context(),
            ) as ws:
                backoff = 1.0
                payload = subscribe_payload()
                if payload.get("method") != SUBSCRIBE_METHOD:
                    raise RuntimeError("refusing non-create subscribe")
                await ws.send(json.dumps(payload))
                log.info("subscribed method=%s", SUBSCRIBE_METHOD)
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                    except asyncio.TimeoutError:
                        log.warning("ws_idle")
                        break
                    t_recv_ms = time.time_ns() // 1_000_000
                    try:
                        messages = _normalize_messages(raw)
                    except json.JSONDecodeError:
                        log.warning("ws_invalid_json len=%s", len(raw))
                        continue
                    for message in messages:
                        record = create_record(message, t_recv_ms)
                        if record is None:
                            continue
                        append_record(output_dir, record)
                        log.info(
                            "create mint=%s slot=%s vendor_time=%s t_recv_ms=%s",
                            record["mint"],
                            record["slot"],
                            record["vendor_time"],
                            record["t_recv_ms"],
                        )
        except ConnectionClosed as exc:
            log.warning("ws_closed code=%s", getattr(exc, "code", None))
        except OSError as exc:
            log.warning("ws_error err=%s", type(exc).__name__)
        if stop.is_set():
            break
        log.info("ws_reconnect sleep_s=%.1f", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MAL phase-1 PumpPortal create alarm")
    parser.add_argument(
        "--ws-url",
        default=os.environ.get("MAL_PUMPPORTAL_WS_URL", DEFAULT_WS_URL),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.environ.get("MAL_FAST_CREATE_OUTPUT_DIR", "/var/lib/mal/sealed/fast-create")),
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("MAL_LOG_LEVEL", "INFO"),
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    if phase1_helius_decision()["open_socket"]:
        log.error("helius_refused")
        return 2
    stop = asyncio.Event()
    loop = asyncio.new_event_loop()

    def _request_stop(*_: object) -> None:
        log.info("shutdown_requested")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _request_stop())
    try:
        loop.run_until_complete(run_client(args.ws_url, args.output_dir, stop))
    finally:
        loop.close()
    log.info("fast_create_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
