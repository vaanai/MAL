#!/usr/bin/env python3
"""dbc_fwd_feed: live DBC transaction feed for the P18 forward PAPER book (docs/research/dbc-p18/DESIGN.md option (c)). PAPER ONLY: no wallet, no send.

Helius `transactionSubscribe` (accountInclude = [DBC program], failed false, vote false, commitment processed, json, full details) pushes the same full transaction that
tools/dbc_decode.decode_tx reads in the backfill. This module turns each notification into decoded rows and hands them to a sink (tools/dbc_fwd.Engine.on_row).

Differences from the backfill, declared:
* `blockTime` is not in a processed notification. Rows carry t_recv_ms = local receive time and `bt_src: "recv"`; tools.dbc_price.block_time_s then uses t_recv_ms / 1000
  (float seconds). The backfill uses the integer block time. The engine's 60 s window and warm-up therefore run on receive time live.
* Failed transactions are not delivered (failed: false); the backfill rows of failed txs are marked `failed` and are not swaps that moved the pool.

Resilience and accounting: reconnect with exponential backoff (1 s .. 60 s); every reconnect and every silence > SILENCE_S becomes a `gap` row, the first message after it a
`gap_end` row (the sink gets them; the engine ignores them, the decision log records them). A byte/message meter estimates credits (CREDITS_PER_100KB per 0.1 MB streamed
plus 1 per connection: the Helius billing rule as researched 2026-10-05, NOT measured on DBC); crossing --credit-cap stops the feed with exit code 5 (as venue_backfill).
The key is read from an env file by tools.venue_backfill.read_key, never printed; every URL in a log line goes through `redact`.

    python3 -m tools.dbc_fwd_feed --fixture tools/fixtures/dbc_txs_2026-10-14T12.json.gz --out DIR     # offline: fixture txs as notifications -> rows -> engine -> log
The live loop `run_feed` is tested only against a fake socket; no CLI path starts it in this build (`--live` is refused). The first 10-minute probe and the book are PR 2-4.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import gzip
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable

from tools import dbc_decode as DD

log = logging.getLogger("dbc_fwd_feed")

HELIUS_WS = "wss://mainnet.helius-rpc.com"
SILENCE_S = 90.0  # venue_tape's DBC silence threshold
INITIAL_BACKOFF_S = 1.0
MAX_BACKOFF_S = 60.0
CREDITS_PER_100KB = 2.0  # researched rule (2026-10-05), unmeasured on DBC: the first live probe replaces it
EXIT_CAP = 5


class CreditCap(Exception):
    """The estimated credits crossed the cap: the feed stops (exit 5)."""


def subscribe_request(commitment: str = "processed", req_id: int = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "method": "transactionSubscribe",
            "params": [{"vote": False, "failed": False, "accountInclude": [DD.PROGRAM]},
                       {"commitment": commitment, "encoding": "json", "transactionDetails": "full", "showRewards": False, "maxSupportedTransactionVersion": 0}]}


def ws_url(key: str, base: str = HELIUS_WS) -> str:
    key = key.strip()
    if not key or any(c in key for c in "\r\n& #?/"):
        raise ValueError("empty or unsafe key")
    return f"{base.rstrip('/')}/?api-key={key}"


_KEY_RE = re.compile(r"(api-key=)[^&\s]+")


def redact(text: str) -> str:
    return _KEY_RE.sub(r"\1***", text)


def tx_from_notification(msg: dict[str, Any], t_recv_ms: int) -> dict[str, Any] | None:
    """transactionNotification -> the getTransaction-shaped dict decode_tx reads (slot, blockTime, transaction, meta, version). blockTime = receive second (see module doc)."""
    if msg.get("method") != "transactionNotification":
        return None
    res = ((msg.get("params") or {}).get("result")) or {}
    slot = res.get("slot")
    tx = res.get("transaction") or {}
    inner, meta = tx.get("transaction"), tx.get("meta")
    if not isinstance(slot, int) or not isinstance(inner, dict) or not isinstance(meta, dict):
        return None
    return {"slot": slot, "blockTime": int(t_recv_ms // 1000), "transaction": inner, "meta": meta, "version": tx.get("version")}


def rows_from_notification(msg: dict[str, Any], t_recv_ms: int) -> list[dict[str, Any]]:
    tx = tx_from_notification(msg, t_recv_ms)
    if tx is None:
        return []
    out = DD.decode_tx(tx)
    for r in out:
        r["t_recv_ms"] = int(t_recv_ms)
        r["bt_src"] = "recv"
    return out


def notification_of(tx: dict[str, Any], sub: int = 1) -> dict[str, Any]:
    """A getTransaction json object wrapped as a transactionNotification (fixtures and tests; the shape Helius documents for transactionSubscribe)."""
    return {"jsonrpc": "2.0", "method": "transactionNotification",
            "params": {"subscription": sub, "result": {"transaction": {"transaction": tx["transaction"], "meta": tx["meta"], "version": tx.get("version")},
                                                        "signature": (tx["transaction"].get("signatures") or [None])[0], "slot": tx["slot"]}}}


class Meter:
    def __init__(self, credit_cap: float | None = None) -> None:
        self.bytes = 0
        self.msgs = 0
        self.connections = 0
        self.cap = credit_cap

    def credits(self) -> float:
        return self.bytes / 100_000.0 * CREDITS_PER_100KB + self.connections

    def add(self, nbytes: int) -> None:
        self.bytes += nbytes
        self.msgs += 1
        self.check()

    def connect(self) -> None:
        self.connections += 1
        self.check()

    def check(self) -> None:
        if self.cap is not None and self.credits() > self.cap:
            raise CreditCap(f"estimated credits {self.credits():.0f} > cap {self.cap:.0f}")

    def row(self) -> dict[str, Any]:
        return {"bytes": self.bytes, "msgs": self.msgs, "connections": self.connections, "credits_est": round(self.credits(), 1), "credits_rule": f"{CREDITS_PER_100KB}/100KB+1/conn"}


class Gaps:
    """Reconnects and silences as explicit rows. `now_ms` is the receive clock."""

    def __init__(self, silence_s: float = SILENCE_S) -> None:
        self.silence_ms = silence_s * 1000.0
        self.last_ms: int | None = None
        self.open: dict[str, Any] | None = None
        self.count = 0

    def _gap(self, reason: str, now_ms: int, **kw: Any) -> dict[str, Any]:
        self.count += 1
        self.open = {"type": "gap", "reason": reason, "start_ms": self.last_ms if self.last_ms is not None else now_ms, "t_ms": now_ms, **kw}
        return self.open

    def reconnect(self, now_ms: int, n: int) -> list[dict[str, Any]]:
        return [self._gap("socket_reconnect", now_ms, reconnects=n)] if self.open is None else []

    def tick(self, now_ms: int) -> list[dict[str, Any]]:
        if self.open is None and self.last_ms is not None and now_ms - self.last_ms > self.silence_ms:
            return [self._gap("silence", now_ms)]
        return []

    def saw(self, now_ms: int) -> list[dict[str, Any]]:
        out = []
        if self.open is not None:
            out.append({"type": "gap_end", "reason": self.open["reason"], "start_ms": self.open["start_ms"], "end_ms": now_ms})
            self.open = None
        self.last_ms = now_ms
        return out


async def run_feed(url: str, sink: Callable[[dict[str, Any]], None], stop: asyncio.Event, *, connect: Callable[[str], Any], meter: Meter, gaps: Gaps,
                   clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000, sleep: Callable[[float], Any] = asyncio.sleep, recv_timeout_s: float = 30.0) -> int:
    """Subscribe, decode, push rows to `sink` until `stop` is set. `connect(url)` is an async context manager yielding an object with async send(str) / recv() -> str|bytes
    (websockets.connect in production, a fake in tests). Returns 0, or EXIT_CAP when the credit cap stops it."""
    backoff = INITIAL_BACKOFF_S
    reconnects = 0
    first = True
    while not stop.is_set():
        try:
            meter.connect()
            if not first:
                reconnects += 1
                for g in gaps.reconnect(clock_ms(), reconnects):
                    sink(g)
            first = False
            async with connect(url) as ws:
                await ws.send(json.dumps(subscribe_request()))
                log.info("subscribed %s", redact(url))
                backoff = INITIAL_BACKOFF_S
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout_s)
                    except asyncio.TimeoutError:
                        for g in gaps.tick(clock_ms()):
                            sink(g)
                        break
                    now = clock_ms()
                    meter.add(len(raw))
                    try:
                        msg = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(msg, dict) or msg.get("method") != "transactionNotification":
                        continue
                    for g in gaps.saw(now):
                        sink(g)
                    for r in rows_from_notification(msg, now):
                        sink(r)
        except CreditCap as e:
            log.error("credit cap: %s", e)
            sink({"type": "stop", "reason": "credit_cap", **meter.row()})
            return EXIT_CAP
        except (OSError, EOFError, ConnectionError) as e:
            log.warning("ws error %s: %s", type(e).__name__, redact(str(e))[:200])
        except Exception as e:  # noqa: BLE001 - websockets' ConnectionClosed and friends; never let a key-bearing message out unredacted
            log.warning("ws closed %s: %s", type(e).__name__, redact(str(e))[:200])
        if stop.is_set():
            break
        await sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF_S)
    return 0


def fixture_notifications(path: str | Path) -> Iterable[tuple[dict[str, Any], int]]:
    """(notification, t_recv_ms) for every tx of a getTransaction json list (optionally .gz), in slot order; t_recv_ms = block time * 1000 + 500."""
    op = gzip.open if str(path).endswith(".gz") else open
    with op(path, "rt") as f:
        txs = json.load(f)
    for tx in sorted(txs, key=lambda t: (t["slot"], t.get("transactionIndex") or 0)):
        yield notification_of(tx), int(tx["blockTime"]) * 1000 + 500


def run_fixture(path: str, out: str) -> dict[str, Any]:
    """Offline: fixture txs -> notifications -> rows -> engine (paper, causal) -> hash-chained decision log under out/declog. No network."""
    from tools import dbc_fwd as F  # noqa: PLC0415
    from tools import dbc_price as L  # noqa: PLC0415
    from tools.arena_declog import DecisionLog  # noqa: PLC0415

    L.enable_swap3_pricing()
    dl = DecisionLog(Path(out) / "declog")
    notes = list(fixture_notifications(path))
    t_first = notes[0][1] if notes else 0
    eng = F.Engine(t_first / 1000.0 + 3600.0, log=dl, meta={"bt_src": "recv"})
    dl.append(F.BOOK, t_first, {"type": "start", "book": F.BOOK, "mode": "fixture", "source": Path(path).name, "code_sha256": F.code_shas()})
    meter, gaps = Meter(), Gaps()
    other = 0
    for msg, t in notes:
        meter.add(len(json.dumps(msg)))
        for r in rows_from_notification(msg, t):
            if r["type"] in ("trade", "curve_complete"):
                eng.on_row(r)
            else:
                other += 1
    eng.finish()
    return {"txs": len(notes), "engine": eng.stats, "other_rows": other, "meter": meter.row(), "declog_head": list(dl.verify(F.BOOK)), "declog_md5": dl.md5(),
            "decisions": len(eng.decisions_live)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fixture", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--live", action="store_true", help="not used by this build (PR 2-4: the probe and the book)")
    ns = ap.parse_args(argv)
    if ns.live or not ns.fixture:
        print("REFUSED: only --fixture exists in this build; no live feed is started", file=sys.stderr)
        return 2
    Path(ns.out).mkdir(parents=True, exist_ok=True)
    res = run_fixture(ns.fixture, ns.out)
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
