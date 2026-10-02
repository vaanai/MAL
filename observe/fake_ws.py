"""Scripted fake websocket for source tests. No network.

A script maps URL -> list of connections, in connect order. A connection is
either an int (handshake rejected with that HTTP status) or a list of steps:
  ("msg", t_recv_ms, frame_dict_or_str)  deliver a frame stamped t_recv_ms
  ("sleep", seconds)
  ("close", code)                        raise ConnectionClosed(code)
When a connection's steps run out the socket idles until cancelled. Connects
past the end of the list idle at once.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import patch

from websockets.exceptions import ConnectionClosed, InvalidStatusCode
from websockets.frames import Close

from observe import trade_source


def logs_frame(sig: str, slot: int, logs: list[str], err=None) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "logsNotification",
        "params": {
            "subscription": 1,
            "result": {
                "context": {"slot": slot},
                "value": {"signature": sig, "err": err, "logs": logs},
            },
        },
    }


class _WS:
    def __init__(self, net: "FakeNetwork", url: str, steps: list) -> None:
        self.net = net
        self.url = url
        self.steps = list(steps)

    async def send(self, payload: str) -> None:
        assert json.loads(payload).get("method") == "logsSubscribe"
        self.net.subscribed[self.url] = self.net.subscribed.get(self.url, 0) + 1

    async def recv(self):
        while True:
            if not self.steps:
                await asyncio.sleep(3600)
            step = self.steps.pop(0)
            if step[0] == "sleep":
                await asyncio.sleep(step[1])
            elif step[0] == "close":
                raise ConnectionClosed(Close(step[1], ""), None)
            else:
                _, t_ms, frame = step
                self.net.now_ms = t_ms
                return frame if isinstance(frame, str) else json.dumps(frame)


class FakeNetwork:
    def __init__(self, script: dict[str, list]) -> None:
        self.script = script
        self.connects: dict[str, int] = {url: 0 for url in script}
        self.now_ms = 0
        self.connect_times: dict[str, list[float]] = {}
        self.subscribed: dict[str, int] = {}

    def connect(self, url, *_a, **_k):
        n = self.connects[url]
        self.connect_times.setdefault(url, []).append(time.monotonic())
        self.connects[url] = n + 1
        conns = self.script[url]
        entry = conns[n] if n < len(conns) else []

        @asynccontextmanager
        async def cm():
            if isinstance(entry, int):
                raise InvalidStatusCode(entry, {})
            yield _WS(self, url, entry)

        return cm()

    def patched(self):
        net = self

        class _Ctx:
            def __enter__(ctx):
                ctx.ps = [
                    patch.object(trade_source.websockets, "connect", net.connect),
                    patch.object(trade_source, "time_ns_ms", lambda: net.now_ms),
                    patch.object(trade_source, "INITIAL_BACKOFF_S", 0.02, create=True),
                    patch.object(trade_source, "MAX_BACKOFF_S", 0.05, create=True),
                ]
                for p in ctx.ps:
                    p.start()

            def __exit__(ctx, *a):
                for p in ctx.ps:
                    p.stop()

        return _Ctx()


async def collect(source, duration: float):
    """Consume the source's notices for `duration` seconds, then stop it."""
    stop = asyncio.Event()
    out = []

    async def run():
        async for note in source.notices(stop):
            out.append(note)

    try:
        await asyncio.wait_for(run(), duration)
    except asyncio.TimeoutError:
        pass
    stop.set()
    return out


FIXTURES = Path(__file__).resolve().parent / "fixtures"
T0 = 1_790_318_932_000


def fixture_line(name: str) -> str:
    return f"Program data: {(FIXTURES / name).read_text(encoding='utf-8').strip()}"


def fixture_frames() -> list[tuple]:
    """A short mixed stream: trades, a failed tx, noise, an rpc error."""
    bonding = [fixture_line("pump_trade_event.b64")]
    swap = [fixture_line("pumpswap_sell_event.b64")]
    swap_buy = [fixture_line("pumpswap_buy_event.b64")]
    return [
        ("msg", T0 + 10, logs_frame("sigA", 100, bonding)),
        ("msg", T0 + 25, logs_frame("sigB", 101, swap)),
        ("msg", T0 + 31, {"jsonrpc": "2.0", "result": 1, "id": 1}),
        ("msg", T0 + 40, logs_frame("sigC", 101, swap_buy, err={"InstructionError": [0, "x"]})),
        ("msg", T0 + 52, "not json"),
        ("msg", T0 + 60, {"jsonrpc": "2.0", "id": 2, "error": {"code": -1, "message": "m"}}),
        ("msg", T0 + 77, logs_frame("sigD", 110, bonding)),
        ("msg", T0 + 90, logs_frame("sigE", 111, swap)),
    ]


def notice_dump(notes) -> list[str]:
    """One JSON line per notice, plus decoded trade rows, for exact comparison."""
    from dataclasses import asdict

    from observe.trade_tape import trades_from_notice

    lines = []
    for note in notes:
        lines.append(json.dumps(asdict(note), sort_keys=True))
        for rec in trades_from_notice(note, {}):
            lines.append(json.dumps(rec, sort_keys=True))
    return lines
