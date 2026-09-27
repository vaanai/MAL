#!/usr/bin/env python3
"""Permanent mint-authority preprocessedSubscribe on mal-fast-0.

Daily cap defaults to 10,000 credits. A trip closes the socket, logs, and
does not open it again until the next UTC day. The key is read from the
mode-600 env file and is never logged.
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
from typing import Any

log = logging.getLogger("mal.fast_pre_create")


def _load_lib() -> Any:
    here = Path(__file__).resolve().parent
    parent = here.parent
    if (here / "fast_helius_pre.py").is_file() and (here / "observe" / "trade_decode.py").is_file():
        sys.path.insert(0, str(here))
        import fast_helius_pre as pre

        return pre
    sys.path.insert(0, str(parent))
    from tools import fast_helius_pre as pre

    return pre


pre = _load_lib()


def _ssl() -> Any:
    import ssl

    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


async def _resolve_loaded(tx: dict[str, Any], cache: dict[str, list[str]]) -> list[str]:
    lookups = tx["lookups"]
    if not lookups:
        return []
    missing = [look["table"] for look in lookups if look["table"] not in cache]
    if missing:
        fetched = await asyncio.to_thread(_fetch_tables, missing)
        cache.update(fetched)
    loaded = pre.loaded_account_keys(lookups, cache)
    return loaded or []


def _fetch_tables(tables: list[str]) -> dict[str, list[str]]:
    import urllib.request

    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getMultipleAccounts",
            "params": [tables, {"encoding": "base64"}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.mainnet-beta.solana.com",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    out: dict[str, list[str]] = {}
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return out
    values = ((payload.get("result") or {}).get("value")) or []
    import base64

    for table, value in zip(tables, values):
        if not isinstance(value, dict):
            continue
        data = value.get("data")
        if not isinstance(data, list) or not data:
            continue
        try:
            raw = base64.b64decode(data[0])
        except (ValueError, TypeError):
            continue
        out[table] = pre.addresses_from_lookup_table(raw)
    return out


async def _sleep_until_next_day(stop: asyncio.Event, gate: Any) -> None:
    log.error(
        "daily_cap_held credits=%s cap=%s reason=%s",
        round(gate.credits, 4),
        gate.cap,
        gate.reason or "credit_trip",
    )
    while not stop.is_set():
        if gate._today() != gate.utc_date or not gate.tripped:
            gate.load()
            if not gate.tripped:
                log.info("daily_cap_resumed date=%s", gate.utc_date)
                return
        await asyncio.sleep(min(30.0, pre.seconds_until_next_utc_day()))


async def run(output_dir: Path, state_path: Path, daily_cap: float, stop: asyncio.Event) -> None:
    gate = pre.DailyCreditGate(state_path, daily_cap)
    meter = pre.CreditMeter(trip=0, rate_max=pre.CREATE_RATE_MAX)
    cache: dict[str, list[str]] = {}
    if gate.tripped:
        await _sleep_until_next_day(stop, gate)
    url = pre.helius_ws_url(os.environ["HELIUS_API_KEY"])
    backoff = 1.0
    import websockets
    from websockets.exceptions import ConnectionClosed

    while not stop.is_set():
        if gate.tripped:
            await _sleep_until_next_day(stop, gate)
            if stop.is_set():
                break
        try:
            log.info("ws_connect host=%s", pre.HELIUS_HOST)
            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                open_timeout=20,
                max_size=8_000_000,
                ssl=_ssl(),
            ) as ws:
                backoff = 1.0
                req = pre.preprocessed_subscribe_request([pre.MINT_AUTHORITY], 1)
                await ws.send(json.dumps(req))
                log.info("subscribed accounts=1 method=preprocessedSubscribe")
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        log.warning("ws_idle")
                        break
                    t_recv_ms = time.time_ns() // 1_000_000
                    if isinstance(raw, str):
                        if "error" in raw:
                            log.warning("ws_rpc_error error=%s", pre.redact(raw))
                        continue
                    if not isinstance(raw, bytes):
                        continue
                    now_s = time.time()
                    if meter.note(now_s):
                        gate.hold(meter.reason or "rate_trip")
                        log.error(
                            "trip reason=%s credits=%s messages=%s",
                            gate.reason,
                            round(gate.credits, 4),
                            meter.messages,
                        )
                        break
                    if gate.note():
                        log.error(
                            "trip reason=%s credits=%s cap=%s",
                            gate.reason,
                            round(gate.credits, 4),
                            gate.cap,
                        )
                        break
                    frame = pre.parse_preprocessed_frame(raw)
                    if frame is None:
                        log.warning("pre_frame_skip len=%s", len(raw))
                        continue
                    try:
                        tx = pre.parse_wire_transaction(frame["wire"])
                    except ValueError:
                        log.warning("pre_tx_skip signature=%s", frame["signature"])
                        continue
                    actions = pre.pump_actions(tx, [])
                    record = pre.create_record(frame["signature"], frame["slot"], t_recv_ms, actions)
                    if record is None and tx["lookups"]:
                        loaded = await _resolve_loaded(tx, cache)
                        actions = pre.pump_actions(tx, loaded)
                        record = pre.create_record(frame["signature"], frame["slot"], t_recv_ms, actions)
                    if record is None:
                        continue
                    path = pre.day_path(output_dir, "fast-pre-create")
                    pre.append_jsonl(path, record)
                    log.info(
                        "create mint=%s curve=%s slot=%s t_recv_ms=%s",
                        record["mint"],
                        record["bonding_curve"],
                        record["slot"],
                        record["t_recv_ms"],
                    )
        except ConnectionClosed as exc:
            log.warning("ws_closed code=%s", getattr(exc, "code", None))
        except Exception as exc:
            log.warning("ws_error err=%s", pre.redact(f"{type(exc).__name__}: {exc}"))
        if stop.is_set():
            break
        if gate.tripped:
            continue
        log.info("ws_reconnect sleep_s=%.1f", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MAL mint-authority preprocessed create listener")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/var/lib/mal/sealed/fast-pre-create"),
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("/var/lib/mal/fast-listener/pre-create-credits.json"),
    )
    parser.add_argument("--daily-cap", type=float, default=pre.CREATE_DAILY_CAP)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/var/lib/mal/fast-listener/helius.env"),
    )
    parser.add_argument("--log-level", default=os.environ.get("MAL_LOG_LEVEL", "INFO"))
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    if not os.environ.get("HELIUS_API_KEY"):
        try:
            pre.load_env_file(args.env_file)
        except (OSError, PermissionError) as exc:
            log.error("helius_env_refused err=%s", type(exc).__name__)
            return 2
    if not os.environ.get("HELIUS_API_KEY"):
        log.error("HELIUS_API_KEY missing")
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
        loop.run_until_complete(run(args.output_dir, args.state, args.daily_cap, stop))
    finally:
        loop.close()
    log.info("fast_pre_create_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
