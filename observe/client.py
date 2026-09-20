#!/usr/bin/env python3
"""PumpPortal observe client — phase 0, no trading, regime at ingest."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from observe.regime import STREAM_MIGRATION, STREAM_NEW_TOKEN, seal_ingest_record

DEFAULT_WS_URL = "wss://pumpportal.fun/api/data"
DEFAULT_OUTPUT_DIR = Path("data/observe")

log = logging.getLogger("mal.observe")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _jsonl_path(output_dir: Path) -> Path:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return output_dir / f"observe-{day}.jsonl"


def append_record(output_dir: Path, record: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _jsonl_path(output_dir)
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    log.info(
        "ingest_sealed stream=%s stage=%s mint=%s path=%s",
        record.get("stream"),
        record.get("stage"),
        record.get("mint"),
        path,
    )


def _normalize_messages(raw: str) -> list[dict[str, Any]]:
    data = json.loads(raw)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return [data]
    log.warning("ws_non_object message_type=%s", type(data).__name__)
    return []


def _classify_stream(payload: dict[str, Any]) -> str | None:
    tx = payload.get("txType")
    if tx == "create":
        return STREAM_NEW_TOKEN
    if tx == "migration":
        return STREAM_MIGRATION
    return None


async def _subscribe(ws: Any) -> None:
    for method in ("subscribeNewToken", "subscribeMigration"):
        await ws.send(json.dumps({"method": method}))
        log.info("subscribed method=%s", method)


async def _handle_payload(
    payload: dict[str, Any],
    output_dir: Path,
    stream_hint: str | None,
) -> None:
    stream = stream_hint or _classify_stream(payload)
    if stream is None:
        log.warning(
            "skip_unclassified txType=%s keys=%s",
            payload.get("txType"),
            sorted(payload.keys()),
        )
        return
    t_ws = _utc_iso()
    record = seal_ingest_record(t_ws=t_ws, stream=stream, payload=payload)
    append_record(output_dir, record)


async def run_client(ws_url: str, output_dir: Path, stop: asyncio.Event) -> None:
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
            ) as ws:
                backoff = 1.0
                await _subscribe(ws)
                async for message in ws:
                    if stop.is_set():
                        break
                    try:
                        for payload in _normalize_messages(message):
                            await _handle_payload(payload, output_dir, None)
                    except json.JSONDecodeError:
                        log.warning("ws_invalid_json len=%s", len(message))
        except ConnectionClosed as exc:
            log.warning("ws_closed code=%s reason=%s", exc.code, exc.reason)
        except OSError as exc:
            log.warning("ws_error err=%s", exc)
        if stop.is_set():
            break
        log.info("ws_reconnect sleep_s=%.1f", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MAL PumpPortal observe client (phase 0)")
    parser.add_argument(
        "--ws-url",
        default=os.environ.get("MAL_PUMPPORTAL_WS_URL", DEFAULT_WS_URL),
        help="PumpPortal WebSocket URL (no API key required for new token + migration)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.environ.get("MAL_OBSERVE_OUTPUT_DIR", DEFAULT_OUTPUT_DIR)),
        help="Directory for append-only JSONL",
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
    log.info("observe_client_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
