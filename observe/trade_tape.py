#!/usr/bin/env python3
"""Paper trade tape for pump.fun bonding-curve and PumpSwap trades.

Hourly append-only JSONL, zstd-sealed when the hour rolls. No keys, no sends.
Default feed is public RPC logsSubscribe ($0). Helius transactionSubscribe is
the same decoder behind --source helius_tx and is not contacted unless
HELIUS_API_KEY is set.

Receive time is stamped when the websocket frame arrives, before JSON parse,
as t_recv_ms (unix milliseconds) and t_recv (UTC ...sssZ).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import logging
import os
import signal
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from observe.trade_decode import (
    PUMP_BONDING_PROGRAM,
    PUMPSWAP_PROGRAM,
    WSOL_MINT,
    apply_pool_mints,
    decode_pool_account,
    records_from_logs,
)
from observe.trade_source import (
    SOURCE_HELIUS_TX,
    SOURCE_PUBLIC_RPC_LOGS,
    SOURCES,
    HeliusTransactionSource,
    LogsSubscribeSource,
    RawNotice,
)
from observe.trade_store import (
    CompletenessMonitor,
    DiskGuard,
    HourlyJsonlWriter,
    ZstdCompressor,
    filesystem_bytes,
    hour_stamp,
    stored_trade,
)

DEFAULT_OUTPUT_DIR = Path("data/observe")
DEFAULT_HTTP = "https://api.mainnet-beta.solana.com"
DEFAULT_OBSERVE_DIR = Path("/var/lib/mal/sealed/jsonl")
POOL_WAIT_S = 1.5
STATS_INTERVAL_S = 600

log = logging.getLogger("mal.trade_tape")


class PoolMintCache:
    """Remember pool -> (base_mint, quote_mint). Defer PumpSwap rows briefly."""

    def __init__(self) -> None:
        self.mints: dict[str, tuple[str, str]] = {}
        self._pending: dict[str, list[dict[str, Any]]] = {}
        self._since: dict[str, float] = {}
        self.unresolved_written = 0

    def remember(self, pool: str, base_mint: str, quote_mint: str) -> list[dict[str, Any]]:
        self.mints[pool] = (base_mint, quote_mint)
        rows = self._pending.pop(pool, [])
        self._since.pop(pool, None)
        for rec in rows:
            apply_pool_mints(rec, base_mint, quote_mint, mint_source="pool_account")
        return rows

    def accept(self, records: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        ready: list[dict[str, Any]] = []
        for rec in records:
            pool = rec.get("pool")
            if rec.get("venue") != "pumpswap" or rec.get("mint") or not isinstance(pool, str):
                ready.append(rec)
                continue
            known = self.mints.get(pool)
            if known is not None:
                apply_pool_mints(rec, known[0], known[1], mint_source="pool_account")
                ready.append(rec)
                continue
            self._pending.setdefault(pool, []).append(rec)
            self._since.setdefault(pool, now)
        return ready

    def unknown_pools(self, limit: int = 100) -> list[str]:
        return list(self._pending.keys())[:limit]

    def flush_timeouts(self, now: float, timeout_s: float = POOL_WAIT_S) -> list[dict[str, Any]]:
        ready: list[dict[str, Any]] = []
        for pool, started in list(self._since.items()):
            if now - started < timeout_s:
                continue
            rows = self._pending.pop(pool, [])
            self._since.pop(pool, None)
            for rec in rows:
                rec["mint_source"] = "unresolved"
                ready.append(rec)
                self.unresolved_written += 1
        return ready


def fetch_pool_mints(http_url: str, pools: list[str]) -> dict[str, tuple[str, str]]:
    """One getMultipleAccounts. Returns pools whose account decoded."""
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
    req = urllib.request.Request(
        http_url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
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


def trades_from_notice(notice: RawNotice, pool_mints: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    if notice.failed or not notice.signature:
        return []
    return records_from_logs(
        notice.logs,
        slot=notice.slot,
        signature=notice.signature,
        t_recv_ms=notice.t_recv_ms,
        commitment=notice.commitment,
        feed=notice.feed,
        pool_mints=pool_mints,
    )


def programs_for_venues(spec: str) -> tuple[str, ...]:
    mapping = {"bonding": PUMP_BONDING_PROGRAM, "pumpswap": PUMPSWAP_PROGRAM}
    names = [part.strip() for part in spec.split(",") if part.strip()]
    if not names:
        raise SystemExit("venues is empty")
    unknown = [name for name in names if name not in mapping]
    if unknown:
        raise SystemExit(f"unknown venues: {','.join(unknown)}")
    return tuple(mapping[name] for name in names)


def accept_trade(guard: DiskGuard, monitor: CompletenessMonitor, writer: HourlyJsonlWriter, rec: dict[str, Any]) -> bool:
    """Write one trade, or count it as dropped while the disk guard is holding."""
    if guard.holding:
        guard.dropped += 1
        return False
    monitor.note(rec)
    writer.write(stored_trade(rec))
    return True


def build_source(name: str, ws_url: str, commitment: str, helius_key: str, programs: tuple[str, ...]):
    if name == SOURCE_PUBLIC_RPC_LOGS:
        return LogsSubscribeSource(ws_url=ws_url, programs=programs, commitment=commitment)
    if name == SOURCE_HELIUS_TX:
        return HeliusTransactionSource(api_key=helius_key, programs=programs, commitment=commitment)
    raise SystemExit(f"unknown source {name}")


async def run_tape(
    source,
    output_dir: Path,
    http_url: str,
    stop: asyncio.Event,
    observe_dir: Path | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    compressor = ZstdCompressor()
    guard = DiskGuard(output_dir)
    writer = HourlyJsonlWriter(output_dir, "trades", compressor, on_seal=guard.note_hour)
    pools_writer = HourlyJsonlWriter(output_dir, "pool-mints", compressor, on_seal=guard.note_hour)
    monitor = CompletenessMonitor(
        output_dir,
        observe_dir if observe_dir is not None else Path(
            os.environ.get("MAL_OBSERVE_JSONL_DIR", DEFAULT_OBSERVE_DIR)
        ),
        source.stats,
        interval_s=float(os.environ.get("MAL_TRADE_TAPE_STATS_INTERVAL_S", STATS_INTERVAL_S)),
    )
    cache = PoolMintCache()
    trades_written = 0
    compressor.sweep_startup(output_dir, hour_stamp(monitor._clock()))
    loop = asyncio.get_running_loop()
    monitor.start(loop.time())

    def emit(rec: dict[str, Any]) -> None:
        nonlocal trades_written
        if accept_trade(guard, monitor, writer, rec):
            trades_written += 1

    def open_paths() -> set[Path]:
        return {path for path in (writer.path, pools_writer.path) if path is not None}

    async def consume() -> None:
        async for notice in source.notices(stop):
            records = trades_from_notice(notice, cache.mints)
            ready = cache.accept(records, loop.time())
            for rec in ready:
                emit(rec)

    async def resolve() -> None:
        while not stop.is_set():
            batch = cache.unknown_pools(100)
            if batch and not guard.holding:
                try:
                    found = await asyncio.to_thread(fetch_pool_mints, http_url, batch)
                except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as exc:
                    log.warning("pool_lookup_failed n=%s err=%s", len(batch), exc)
                    found = {}
                for pool, (base_mint, quote_mint) in found.items():
                    if not guard.holding:
                        pools_writer.write(
                            {
                                "v": 1,
                                "type": "pool_mint",
                                "pool": pool,
                                "mint": base_mint,
                                "quote_mint": quote_mint,
                                "quote_is_wsol": quote_mint == WSOL_MINT,
                            }
                        )
                    for rec in cache.remember(pool, base_mint, quote_mint):
                        emit(rec)
            for rec in cache.flush_timeouts(loop.time()):
                emit(rec)
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.4)
            except asyncio.TimeoutError:
                pass

    async def heartbeat() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                writer.rotate()
                pools_writer.rotate()
                guard.tick(
                    open_paths=open_paths(),
                    inflight=compressor.inflight(),
                    current_hour=hour_stamp(monitor._clock()),
                )
                total, _used, avail = filesystem_bytes(output_dir)
                row = monitor.maybe_flush(
                    loop.time(),
                    {"free_bytes": avail, "total_bytes": total, "hold": guard.holding},
                )
                stats = source.stats
                log.info(
                    "heartbeat source=%s commitment=%s trades=%s notes=%s failed_notes=%s "
                    "reconnects=%s slot_jumps=%s last_slot=%s unresolved=%s pool_cache=%s "
                    "bytes=%s hold=%s dropped=%s keep_days=%s free_ratio=%.3f stats=%s",
                    type(source).__name__,
                    source.commitment,
                    trades_written,
                    stats.notes,
                    stats.failed_notes,
                    stats.reconnects,
                    stats.slot_jumps,
                    stats.last_slot,
                    cache.unresolved_written,
                    len(cache.mints),
                    writer.bytes,
                    int(guard.holding),
                    guard.dropped,
                    guard.keep_days,
                    guard.free_ratio,
                    row is not None,
                )

    log.info(
        "trade_tape_start source=%s commitment=%s venues=%s output=%s",
        type(source).__name__,
        source.commitment,
        ",".join(source.programs),
        output_dir,
    )
    guard.tick(
        open_paths=open_paths(),
        inflight=compressor.inflight(),
        current_hour=hour_stamp(monitor._clock()),
    )
    try:
        await asyncio.gather(consume(), resolve(), heartbeat())
    finally:
        writer.close()
        pools_writer.close()
        compressor.close()
        log.info(
            "trade_tape_stopped trades=%s bytes=%s dropped=%s zstd_files=%s zstd_errors=%s",
            trades_written,
            writer.bytes,
            guard.dropped,
            compressor.compressed,
            compressor.errors,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MAL paper trade tape (pump.fun + PumpSwap)")
    parser.add_argument(
        "--source",
        default=os.environ.get("MAL_TRADE_TAPE_SOURCE", SOURCE_PUBLIC_RPC_LOGS),
        choices=SOURCES,
        help="public_rpc_logs ($0) or helius_tx (needs HELIUS_API_KEY; not contacted otherwise)",
    )
    parser.add_argument(
        "--ws-url",
        default=os.environ.get("MAL_TRADE_TAPE_WS_URL", "wss://api.mainnet-beta.solana.com"),
        help="Public RPC websocket. Ignored for helius_tx.",
    )
    parser.add_argument(
        "--http-url",
        default=os.environ.get("MAL_SOLANA_HTTP_URL", DEFAULT_HTTP),
        help="HTTP RPC for PumpSwap pool->mint lookups (public cluster by default)",
    )
    parser.add_argument(
        "--venues",
        default=os.environ.get("MAL_TRADE_TAPE_VENUES", "bonding,pumpswap"),
        help="Comma list: bonding, pumpswap",
    )
    parser.add_argument(
        "--commitment",
        default=os.environ.get("MAL_TRADE_TAPE_COMMITMENT", "confirmed"),
        choices=("processed", "confirmed", "finalized"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.environ.get("MAL_TRADE_TAPE_OUTPUT_DIR", DEFAULT_OUTPUT_DIR)),
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
    helius_key = os.environ.get("HELIUS_API_KEY", "")
    if args.source == SOURCE_HELIUS_TX and not helius_key.strip():
        log.error("HELIUS_API_KEY is not set; not starting helius_tx")
        return 2
    programs = programs_for_venues(args.venues)
    source = build_source(args.source, args.ws_url, args.commitment, helius_key, programs)

    stop = asyncio.Event()
    loop = asyncio.new_event_loop()

    def _request_stop(*_: object) -> None:
        log.info("shutdown_requested")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_a: _request_stop())
    try:
        loop.run_until_complete(run_tape(source, args.output_dir, args.http_url, stop))
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
