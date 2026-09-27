#!/usr/bin/env python3
"""First-120-second bonding-curve preprocessedSubscribe.

Tails mint-authority creates, subscribes each bonding curve for 120 seconds,
and keeps the migration account when it is enabled. Buy and Sell rows are
sealed with observe.trade_decode on public logs. ``t_recv_ms`` on those rows
is the preprocessed receive time when that frame arrived. A 20,000-credit
probe trip stops the process. A daily cap, once set, holds until the next UTC day.
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

log = logging.getLogger("mal.fast_early_trade")


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


class Session:
    def __init__(self, book: Any, output_dir: Path, meter: Any, gate: Any | None) -> None:
        self.book = book
        self.output_dir = output_dir
        self.meter = meter
        self.gate = gate
        self.seen: dict[str, dict[str, Any]] = {}
        self.messages_by_mint: dict[str, int] = {}
        self.unattributed = 0
        self.sealed = 0
        self.graduations = 0
        self.max_accounts = 0
        self.changed = asyncio.Event()
        self.stop_streams = asyncio.Event()
        self._alt: dict[str, list[str]] = {}
        self._attributed: set[str] = set()

    def note_accounts(self) -> None:
        self.max_accounts = max(self.max_accounts, len(self.book.accounts()))

    def trip(self, reason: str) -> None:
        if self.gate is not None:
            self.gate.hold(reason)
        self.stop_streams.set()
        log.error(
            "trip reason=%s credits=%s messages=%s",
            reason,
            round(self.meter.credits, 4),
            self.meter.messages,
        )

    def on_binary(self, raw: bytes, t_recv_ms: int) -> None:
        now_s = time.time()
        if self.meter.note(now_s):
            self.trip(self.meter.reason or "credit_trip")
            return
        if self.gate is not None and self.gate.note():
            self.trip(self.gate.reason or "credit_trip")
            return
        frame = pre.parse_preprocessed_frame(raw)
        if frame is None:
            return
        signature = frame["signature"]
        slot = frame["slot"]
        actions: list[dict[str, Any]] = []
        keys: list[str] = []
        pending = False
        try:
            tx = pre.parse_wire_transaction(frame["wire"])
            resolved = pre.loaded_account_keys(tx["lookups"], self._alt)
            pending = bool(tx["lookups"]) and resolved is None
            loaded = resolved or []
            if pending:
                asyncio.create_task(self._finish_loaded(tx, signature))
            actions = pre.pump_actions(tx, loaded)
            keys = list(tx["keys"]) + list(loaded)
        except ValueError:
            actions = []
        self._write_seen(signature, slot, t_recv_ms, actions, keys, pending=pending)

    async def _finish_loaded(self, tx: dict[str, Any], signature: str) -> None:
        try:
            missing = [look["table"] for look in tx["lookups"] if look["table"] not in self._alt]
            if missing:
                fetched = await asyncio.to_thread(_fetch_tables, missing)
                self._alt.update(fetched)
            loaded = pre.loaded_account_keys(tx["lookups"], self._alt) or []
            actions = pre.pump_actions(tx, loaded)
        except Exception as exc:
            log.warning("alt_resolve_failed err=%s", type(exc).__name__)
            self._attribute(signature, [], [], pending=False)
            return
        keys = list(tx["keys"]) + list(loaded)
        self._attribute(signature, actions, keys, pending=False)
        prior = self.seen.get(signature)
        if prior is None:
            return
        for action in actions:
            if action.get("kind") in ("buy", "sell") and prior.get("side") is None:
                prior["side"] = action.get("kind")
                prior["mint"] = action.get("mint")
                prior["bonding_curve"] = action.get("bonding_curve")
                prior["ix_event_index"] = action.get("ix_event_index")
                prior["instr"] = action.get("instr")

    def _attribute(
        self,
        signature: str,
        actions: list[dict[str, Any]],
        keys: list[str],
        pending: bool,
    ) -> None:
        if signature in self._attributed:
            return
        touched = pre.mints_touched(actions, keys, self.book.curve_to_mint())
        if not touched:
            if pending:
                return
            self._attributed.add(signature)
            self.unattributed += 1
            return
        self._attributed.add(signature)
        for mint in touched:
            self.messages_by_mint[mint] = self.messages_by_mint.get(mint, 0) + 1

    def _write_seen(
        self,
        signature: str,
        slot: int,
        t_recv_ms: int,
        actions: list[dict[str, Any]],
        keys: list[str],
        pending: bool,
    ) -> None:
        self._attribute(signature, actions, keys, pending=pending)
        if signature in self.seen:
            return
        self.seen[signature] = {
            "t_recv_ms": t_recv_ms,
            "slot": slot,
            "side": None,
            "mint": None,
            "bonding_curve": None,
            "ix_event_index": None,
            "instr": None,
        }
        rows = pre.seen_records(signature, slot, t_recv_ms, actions)
        path = pre.day_path(self.output_dir, "early-seen")
        for row in rows:
            pre.append_jsonl(path, row)
            if row.get("side") and self.seen[signature].get("side") is None:
                self.seen[signature].update(
                    {
                        "side": row.get("side"),
                        "mint": row.get("mint"),
                        "bonding_curve": row.get("bonding_curve"),
                        "ix_event_index": row.get("ix_event_index"),
                        "instr": row.get("instr"),
                    }
                )
        grads = pre.graduation_records(signature, slot, t_recv_ms, actions)
        if grads:
            gpath = pre.day_path(self.output_dir, "early-graduation")
            for row in grads:
                pre.append_jsonl(gpath, row)
                self.graduations += 1

    def on_logs(self, notice: dict[str, Any], t_logs_ms: int) -> None:
        slot = notice["slot"] if isinstance(notice["slot"], int) else 0
        try:
            rows = pre.sealed_trades_from_logs(
                notice["logs"],
                slot=slot,
                signature=notice["signature"],
                t_recv_ms=t_logs_ms,
            )
        except (ValueError, KeyError):
            return
        prior = self.seen.get(notice["signature"])
        path = pre.day_path(self.output_dir, "early-trade")
        for row in rows:
            if prior is not None:
                row = pre.stamp_fast_recv(row, prior["t_recv_ms"], prior["slot"])
            else:
                row["t_recv_source"] = "public_logs"
            pre.append_jsonl(path, row)
            self.sealed += 1

    def status(self, started_ms: int) -> dict[str, Any]:
        summary = pre.credits_per_mint_summary(self.messages_by_mint)
        elapsed_s = max(0.001, (time.time_ns() // 1_000_000 - started_ms) / 1000)
        return {
            "started_ms": started_ms,
            "elapsed_s": round(elapsed_s, 3),
            "meter": self.meter.snapshot(),
            "gate": None
            if self.gate is None
            else {
                "credits": round(self.gate.credits, 4),
                "tripped": self.gate.tripped,
                "reason": self.gate.reason,
                "cap": self.gate.cap,
                "utc_date": self.gate.utc_date,
            },
            "accounts": len(self.book.accounts()),
            "max_accounts": self.max_accounts,
            "curves": len(self.book.curves),
            "seen": len(self.seen),
            "sealed": self.sealed,
            "graduations": self.graduations,
            "unattributed": self.unattributed,
            "credits_per_mint": summary,
            "projected_credits_per_day": round(pre.project_daily_credits(self.meter.credits, elapsed_s), 2),
        }


def _fetch_tables(tables: list[str]) -> dict[str, list[str]]:
    import base64
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


async def _tail_creates(create_dir: Path, session: Session, stop: asyncio.Event) -> None:
    path: Path | None = None
    handle = None
    while not stop.is_set() and not session.stop_streams.is_set():
        current = pre.day_path(create_dir, "fast-pre-create")
        if path != current:
            if handle is not None:
                handle.close()
            path = current
            if not path.is_file():
                await asyncio.sleep(0.2)
                continue
            handle = path.open("r", encoding="utf-8")
            handle.seek(0, 2)
        if handle is None:
            await asyncio.sleep(0.2)
            continue
        line = handle.readline()
        if not line:
            await asyncio.sleep(0.05)
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        mint = row.get("mint")
        curve = row.get("bonding_curve")
        if not isinstance(mint, str) or not isinstance(curve, str):
            continue
        result = session.book.add(curve, mint, time.time())
        if result == "added":
            session.note_accounts()
            session.changed.set()
            log.info("curve_add mint=%s accounts=%s", mint, len(session.book.accounts()))
        elif result == "cap":
            log.error("account_cap accounts=%s", len(session.book.accounts()))


async def _expire(session: Session, stop: asyncio.Event) -> None:
    while not stop.is_set() and not session.stop_streams.is_set():
        dead = session.book.expire(time.time())
        if dead:
            session.changed.set()
            log.info("curve_expire n=%s accounts=%s", len(dead), len(session.book.accounts()))
        await asyncio.sleep(0.5)


async def _read_pre(ws: Any, session: Session, req_id: int | None) -> bool:
    """Read one frame. Return True when the subscribe ack for ``req_id`` arrives."""
    raw = await asyncio.wait_for(ws.recv(), timeout=10)
    if isinstance(raw, bytes):
        session.on_binary(raw, time.time_ns() // 1_000_000)
        return False
    if not isinstance(raw, str):
        return False
    if "error" in raw:
        log.warning("ws_rpc_error error=%s", pre.redact(raw))
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(msg, dict):
        return False
    if "error" in msg and req_id is not None and msg.get("id") == req_id:
        raise RuntimeError(pre.redact(json.dumps(msg["error"])))
    return bool(req_id is not None and msg.get("id") == req_id and "result" in msg)


async def _pre_once(
    session: Session,
    accounts: list[str],
    ready: asyncio.Event,
    stop_gen: asyncio.Event,
    stop: asyncio.Event,
) -> None:
    import websockets

    url = pre.helius_ws_url(os.environ["HELIUS_API_KEY"])
    async with websockets.connect(
        url,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=2,
        open_timeout=20,
        max_size=8_000_000,
        ssl=_ssl(),
    ) as ws:
        await ws.send(json.dumps(pre.preprocessed_subscribe_request(accounts, 1)))
        deadline = time.time() + 10
        acked = False
        while time.time() < deadline and not acked:
            acked = await _read_pre(ws, session, 1)
        if not acked:
            raise TimeoutError("preprocessed subscribe ack")
        ready.set()
        log.info("pre_live accounts=%s", len(accounts))
        while not stop.is_set() and not stop_gen.is_set() and not session.stop_streams.is_set():
            try:
                await asyncio.wait_for(_read_pre(ws, session, None), timeout=0.5)
            except asyncio.TimeoutError:
                continue


async def _close_task(task: asyncio.Task[None] | None, stop_gen: asyncio.Event | None) -> None:
    if stop_gen is not None:
        stop_gen.set()
    if task is None:
        return
    try:
        await asyncio.wait_for(task, timeout=5)
    except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def _pre_loop(session: Session, stop: asyncio.Event) -> None:
    """Open the next filter before closing the previous socket."""
    current: asyncio.Task[None] | None = None
    current_stop: asyncio.Event | None = None
    current_accounts: list[str] = []
    while not stop.is_set() and not session.stop_streams.is_set():
        if session.changed.is_set():
            await asyncio.sleep(0.05)
        accounts = session.book.accounts()
        session.changed.clear()
        if not accounts:
            await asyncio.sleep(0.1)
            continue
        if accounts == current_accounts and current is not None and not current.done():
            try:
                await asyncio.wait_for(session.changed.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            continue
        ok, reason = pre.account_filter_decision(accounts)
        if not ok:
            log.error("filter_refused reason=%s", reason)
            session.trip(reason)
            break
        ready = asyncio.Event()
        stop_gen = asyncio.Event()
        task = asyncio.create_task(_pre_once(session, list(accounts), ready, stop_gen, stop))
        try:
            await asyncio.wait_for(ready.wait(), timeout=15)
        except (asyncio.TimeoutError, Exception) as exc:
            log.warning("pre_rotate_failed err=%s", pre.redact(f"{type(exc).__name__}: {exc}"))
            await _close_task(task, stop_gen)
            await asyncio.sleep(1)
            continue
        previous = current
        previous_stop = current_stop
        current = task
        current_stop = stop_gen
        current_accounts = list(accounts)
        await _close_task(previous, previous_stop)
    await _close_task(current, current_stop)


async def _seal_when_seen(session: Session, notice: dict[str, Any], t_logs_ms: int) -> None:
    for _ in range(5):
        if notice["signature"] in session.seen or session.stop_streams.is_set():
            break
        await asyncio.sleep(0.05)
    session.on_logs(notice, t_logs_ms)


async def _logs_loop(session: Session, public_ws: str, stop: asyncio.Event) -> None:
    import websockets
    from websockets.exceptions import ConnectionClosed

    backoff = 1.0
    while not stop.is_set() and not session.stop_streams.is_set():
        try:
            async with websockets.connect(
                public_ws,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                open_timeout=20,
                max_size=8_000_000,
                ssl=_ssl(),
            ) as ws:
                backoff = 1.0
                sub_for: dict[str, int] = {}
                id_for: dict[int, str] = {}
                next_id = 1
                while not stop.is_set() and not session.stop_streams.is_set():
                    wanted = session.book.accounts()
                    for account in wanted:
                        if account in sub_for:
                            continue
                        req_id = next_id
                        next_id += 1
                        id_for[req_id] = account
                        await ws.send(json.dumps(pre.logs_subscribe_request(account, req_id)))
                    for account, sub_id in list(sub_for.items()):
                        if account in wanted:
                            continue
                        await ws.send(json.dumps(pre.logs_unsubscribe_request(sub_id, next_id)))
                        next_id += 1
                        del sub_for[account]
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    t_recv_ms = time.time_ns() // 1_000_000
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    if "id" in msg and msg["id"] in id_for and "result" in msg:
                        account = id_for.pop(msg["id"])
                        if isinstance(msg["result"], int):
                            sub_for[account] = msg["result"]
                        continue
                    if "error" in msg and msg.get("method") is None:
                        log.warning("logs_rpc_error error=%s", pre.redact(json.dumps(msg.get("error"))))
                        continue
                    notice = pre.parse_logs_notice(msg)
                    if notice is None:
                        continue
                    asyncio.create_task(_seal_when_seen(session, notice, t_recv_ms))
        except ConnectionClosed as exc:
            log.warning("logs_closed code=%s", getattr(exc, "code", None))
        except Exception as exc:
            log.warning("logs_error err=%s", pre.redact(f"{type(exc).__name__}: {exc}"))
        if stop.is_set() or session.stop_streams.is_set():
            break
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


async def _status_loop(session: Session, path: Path, started_ms: int, stop: asyncio.Event) -> None:
    while not stop.is_set():
        body = session.status(started_ms)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
        log.info(
            "status credits=%s seen=%s sealed=%s accounts=%s projected_day=%s",
            body["meter"]["credits"],
            body["seen"],
            body["sealed"],
            body["accounts"],
            body["projected_credits_per_day"],
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass


async def run(args: argparse.Namespace, stop: asyncio.Event) -> dict[str, Any]:
    permanent = [pre.MIGRATION_ACCOUNT] if args.include_migration else []
    book = pre.CurveBook(permanent, cap=pre.ACCOUNT_CAP, ttl_s=args.ttl_s)
    gate = None
    if args.daily_cap > 0:
        gate = pre.DailyCreditGate(args.state, args.daily_cap)
        if gate.tripped:
            log.error("daily_cap_held credits=%s cap=%s", round(gate.credits, 4), gate.cap)
            while not stop.is_set() and gate.tripped and gate._today() == gate.utc_date:
                await asyncio.sleep(min(30.0, pre.seconds_until_next_utc_day()))
                gate.load()
    meter = pre.CreditMeter(trip=args.credit_trip, rate_max=pre.TRADE_RATE_MAX)
    session = Session(book, args.output_dir, meter, gate)
    started_ms = time.time_ns() // 1_000_000
    if args.seconds > 0:
        asyncio.get_running_loop().call_later(args.seconds, stop.set)
    tasks = [
        asyncio.create_task(_tail_creates(args.create_dir, session, stop)),
        asyncio.create_task(_expire(session, stop)),
        asyncio.create_task(_pre_loop(session, stop)),
        asyncio.create_task(_logs_loop(session, args.public_ws, stop)),
        asyncio.create_task(_status_loop(session, args.status, started_ms, stop)),
    ]
    await stop.wait()
    session.stop_streams.set()
    session.changed.set()
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    body = session.status(started_ms)
    args.status.parent.mkdir(parents=True, exist_ok=True)
    args.status.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
    log.info("early_trade_stopped %s", json.dumps(body["meter"]))
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MAL early-trade preprocessed listener")
    parser.add_argument("--create-dir", type=Path, default=Path("/var/lib/mal/sealed/fast-pre-create"))
    parser.add_argument("--output-dir", type=Path, default=Path("/var/lib/mal/sealed/fast-early"))
    parser.add_argument("--state", type=Path, default=Path("/var/lib/mal/fast-listener/early-trade-credits.json"))
    parser.add_argument("--status", type=Path, default=Path("/var/lib/mal/sealed/fast-early/early-status.json"))
    parser.add_argument("--daily-cap", type=float, default=0)
    parser.add_argument("--credit-trip", type=float, default=pre.PROBE_CREDIT_TRIP)
    parser.add_argument("--seconds", type=float, default=1800)
    parser.add_argument("--ttl-s", type=float, default=pre.CURVE_TTL_S)
    parser.add_argument("--public-ws", default=pre.PUBLIC_WS)
    parser.add_argument("--include-migration", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--env-file", type=Path, default=Path("/var/lib/mal/fast-listener/helius.env"))
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
        loop.run_until_complete(run(args, stop))
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
