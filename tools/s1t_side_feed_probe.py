#!/usr/bin/env python3
"""10-minute counts-only probe of ONE keyless public websocket for the pump.fun class side feed (s1t, DEC-031 Amendment 24 item 2 "Class side feed").
Paper only. Read-only subscription; no keys, no sends, no RPC writes.

It subscribes `logsSubscribe` mentioning the pump.fun program (PUMP_PROGRAM, the PUMP_BONDING_PROGRAM of observe/trade_decode.py at 5c2ab7c6) at
commitment confirmed, reconnects on close with a doubling backoff, and prints ONE JSON line: connects, connect_errors, closes by code, ack_ms of the
first subscribe, subscribe errors (code, message redacted and cut to 120 characters), messages (logsNotification count), msgs_per_s, MB_per_h
(decimal MB of every message received) and max_silence_s (the longest span without a notification, from the start to the end of the probe included).
Notification content is never parsed and never printed (a byte test on the method name only).

REFUSED (exit 2, no connection): a URL that is not wss://; a query, fragment or userinfo; keylike text (a key-ish word, or a long token in the path);
the arena feed's providers: host api.mainnet-beta.solana.com, or any host containing 'solanavibestation'.

Run it with the venv that has websockets (it is imported only when a connection is made): /data/mal/arena/venv-h5/bin/python tools/s1t_side_feed_probe.py --url wss://...
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import re
import sys
import time
from typing import Any, Awaitable, Callable, Sequence
from urllib.parse import urlsplit

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
COMMITMENT = "confirmed"
SUBSCRIBE = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe", "params": [{"mentions": [PUMP_PROGRAM]}, {"commitment": COMMITMENT}]},
                       separators=(",", ":"))
ARENA_HOSTS = frozenset({"api.mainnet-beta.solana.com"})
ARENA_HOST_PARTS = ("solanavibestation",)
MSG_MAX = 120
SUB_ERRORS_MAX = 5
CTRL_MAX_BYTES = 65536  # only a short control message (ack, error) is ever parsed

_KEY_WORD = re.compile(r"(?i)(api[-_]?key|apikey|x-token|token|passw|auth|bearer|[?&;]key=)")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-]{24,}")


def check_url(url: str) -> str:
    """Return the URL when it is a keyless wss:// URL of a provider the arena feed does not use; raise ValueError with a clear message otherwise."""
    if not isinstance(url, str) or not url.startswith("wss://"):
        raise ValueError("refused: the URL must start with wss://")
    sp = urlsplit(url)
    host = (sp.hostname or "").lower()
    if not host:
        raise ValueError("refused: no host in the URL")
    if sp.query or sp.fragment or "?" in url or "#" in url:
        raise ValueError("refused: the URL has a query or fragment (a keyless URL is required)")
    if sp.username is not None or sp.password is not None or "@" in sp.netloc:
        raise ValueError("refused: the URL has userinfo (a keyless URL is required)")
    if host in ARENA_HOSTS or any(part in host for part in ARENA_HOST_PARTS):
        raise ValueError("refused: this is a provider of the arena feed (api.mainnet-beta.solana.com, solanavibestation); the side feed probe must use another one")
    if _KEY_WORD.search(url) or _LONG_TOKEN.search(sp.path):
        raise ValueError("refused: the URL has keylike text (a keyless URL is required)")
    return url


def redact(text: Any, limit: int = MSG_MAX) -> str:
    """Keylike text replaced, then cut to `limit` characters (so a cut never leaves a partial key)."""
    s = _LONG_TOKEN.sub("<redacted>", str(text))
    s = re.sub(r"(?i)(api[-_]?key|apikey|token|passw\w*|auth\w*|bearer|key)\s*[=:]\s*\S+", r"\1=<redacted>", s)
    return s[:limit]


def close_code(exc: BaseException) -> int:
    rcvd = getattr(exc, "rcvd", None)
    code = getattr(rcvd, "code", None)
    if code is None:
        code = getattr(exc, "code", None)
    return code if isinstance(code, int) and not isinstance(code, bool) else 1006  # no close frame = abnormal closure


class Tally:
    def __init__(self, t0: float) -> None:
        self.t0, self.last_msg, self.max_gap = t0, t0, 0.0
        self.connects = self.connect_errors = self.messages = self.bytes = 0
        self.closes: collections.Counter = collections.Counter()
        self.ack_ms: int | None = None
        self.sub_errors: list[dict] = []

    def on_message(self, now: float, data: Any, t_sub: float, first_conn: bool) -> None:
        raw = data if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8", "replace")
        self.bytes += len(raw)
        if b"logsNotification" in raw:  # a byte test on the method name; the content is never parsed
            self.messages += 1
            self.max_gap = max(self.max_gap, now - self.last_msg)
            self.last_msg = now
            return
        if len(raw) > CTRL_MAX_BYTES:
            return
        try:
            obj = json.loads(raw)
        except ValueError:
            return
        if not isinstance(obj, dict) or obj.get("id") != 1:
            return
        err = obj.get("error")
        if isinstance(err, dict):
            if len(self.sub_errors) < SUB_ERRORS_MAX:
                code = err.get("code")
                self.sub_errors.append({"code": code if isinstance(code, int) else None, "msg": redact(err.get("message", ""))})
        elif "result" in obj and first_conn and self.ack_ms is None:
            self.ack_ms = int(round((now - t_sub) * 1000))

    def summary(self, now: float, host: str, seconds: float) -> dict:
        elapsed = max(now - self.t0, 1e-9)
        return {"probe": "s1t_side_feed", "host": host, "commitment": COMMITMENT, "seconds_planned": seconds, "elapsed_s": round(elapsed, 3),
                "connects": self.connects, "connect_errors": self.connect_errors, "closes": {str(k): v for k, v in sorted(self.closes.items())},
                "ack_ms": self.ack_ms, "sub_errors": self.sub_errors, "messages": self.messages,
                "msgs_per_s": round(self.messages / elapsed, 3), "MB_per_h": round(self.bytes / 1e6 / (elapsed / 3600), 3),
                "max_silence_s": round(max(self.max_gap, now - self.last_msg), 3)}


async def run_probe(connect: Callable[[], Any], seconds: float, *, host: str = "", clock: Callable[[], float] = time.monotonic,
                    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, backoff0: float = 1.0, backoff_max: float = 30.0) -> dict:
    """`connect()` returns an async context manager yielding a connection with `send(str)` and `recv()`. Injectable for tests: no network here."""
    tally = Tally(clock())
    deadline = tally.t0 + seconds
    backoff = backoff0
    while clock() < deadline:
        opened = False
        try:
            async with connect() as ws:
                opened = True
                first = tally.connects == 0
                tally.connects += 1
                backoff = backoff0
                await ws.send(SUBSCRIBE)
                t_sub = clock()
                while clock() < deadline:
                    try:
                        data = await asyncio.wait_for(ws.recv(), timeout=max(0.05, deadline - clock()))
                    except asyncio.TimeoutError:
                        continue
                    tally.on_message(clock(), data, t_sub, first)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a close or a failed connect is counted, then the loop reconnects
            if opened:
                tally.closes[close_code(exc)] += 1
            else:
                tally.connect_errors += 1
            await sleep(min(backoff, max(0.0, deadline - clock())))
            backoff = min(backoff * 2, backoff_max)
    return tally.summary(clock(), host, seconds)


def ws_connect_factory(url: str) -> Callable[[], Any]:
    import websockets  # imported here so the pure parts (and the tests) need no websockets

    def connect() -> Any:
        return websockets.connect(url, open_timeout=15, max_size=16 * 1024 * 1024)

    return connect


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Counts-only probe of one keyless public websocket for the s1t pump.fun class side feed. Prints one JSON line.")
    ap.add_argument("--url", required=True, help="a keyless wss:// URL, not an arena feed provider")
    ap.add_argument("--seconds", type=float, default=600.0)
    args = ap.parse_args(argv)
    try:
        url = check_url(args.url)
        if not args.seconds > 0:
            raise ValueError("refused: --seconds must be positive")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        factory = ws_connect_factory(url)
    except ImportError:
        print("websockets is not installed in this python; use /data/mal/arena/venv-h5/bin/python", file=sys.stderr)
        return 2
    summary = asyncio.run(run_probe(factory, args.seconds, host=urlsplit(url).hostname or ""))
    print(json.dumps(summary, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
