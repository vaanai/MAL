"""P47 feed-lag probe: public logsSubscribe vs Helius transactionSubscribe, PumpSwap only.

Paper only, read only. No DBC / Meteora subscription: the only program filtered is PumpSwap.

Both sockets run at ``processed`` commitment and see failed=false transactions only (the public
feed's err != null rows are dropped so the two sets are comparable). For each signature we keep
the local receive time (time.time(), host NTP) per source and the slot. Block time comes from
getBlockTime anchors (1 s resolution) sampled over the run; a least-squares line
``unix_time = a + b * slot`` over the anchors turns a slot into a time at the end of the run
(the quantisation noise averages out; the fit residual is reported). Lag per source =
receive time - fitted slot time.

Caveats that the report repeats: the fitted block time is the cluster's vote timestamp, not a
wall clock, so the ABSOLUTE lag carries an unknown offset (the report gives the fit residual, not
a bound on that offset). The PAIRED difference (public receive - Helius receive, same signature)
and the first-seen shares do not depend on block time or on the host clock, only on both
sockets living on one host.

Memory is bounded: pending signatures are finalised after PENDING_TTL_S, finalised rows go into
a capped array of doubles.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import signal
import sys
import time
import urllib.request
from array import array
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Callable

PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUBLIC_WS = "wss://api.mainnet-beta.solana.com"
HELIUS_WS = "wss://mainnet.helius-rpc.com"
HELIUS_HTTP = "https://mainnet.helius-rpc.com"

PENDING_TTL_S = 90.0
MAX_PENDING = 200_000
MAX_ROWS = 2_000_000
MAX_ANCHORS = 400
ANCHOR_EVERY_S = 30.0
ANCHOR_MIN_AGE_SLOTS = 150
CREDITS_PER_100KB = 2.0  # Helius doc figure used in the brief: 2 credits per 0.1 MB
CREDITS_PER_CONNECTION = 1.0
CREDITS_PER_RPC_CALL = 1.0  # estimate for getBlockTime
NAN = float("nan")


# ----------------------------------------------------------------------------- parsing


def public_sub_request(req_id: int = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "method": "logsSubscribe",
            "params": [{"mentions": [PUMPSWAP_PROGRAM]}, {"commitment": "processed"}]}


def helius_sub_request(req_id: int = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "method": "transactionSubscribe",
            "params": [{"accountInclude": [PUMPSWAP_PROGRAM], "failed": False, "vote": False},
                       {"commitment": "processed", "encoding": "json",
                        "transactionDetails": "signatures", "showRewards": False}]}


def parse_public(msg: dict[str, Any]) -> tuple[str, int] | None:
    """(signature, slot) from a logsNotification, None for acks, failed txs and junk."""
    if msg.get("method") != "logsNotification":
        return None
    try:
        res = msg["params"]["result"]
        slot = int(res["context"]["slot"])
        val = res["value"]
        if val.get("err") is not None:
            return None
        sig = val["signature"]
    except (KeyError, TypeError, ValueError):
        return None
    return (sig, slot) if isinstance(sig, str) and sig else None


def parse_helius(msg: dict[str, Any]) -> tuple[str, int] | None:
    if msg.get("method") != "transactionNotification":
        return None
    try:
        res = msg["params"]["result"]
        slot = int(res["slot"])
        sig = res["signature"]
    except (KeyError, TypeError, ValueError):
        return None
    return (sig, slot) if isinstance(sig, str) and sig else None


def redact(text: str, key: str | None = None) -> str:
    out = text
    if key:
        out = out.replace(key, "<redacted>")
    out = re.sub(r"(api-key=)[^&\s\"']*", r"\1<redacted>", out)
    return out[:400]


# ----------------------------------------------------------------------------- statistics


def percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated percentile on a sorted list (q in [0, 100])."""
    if not sorted_vals:
        return NAN
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def summarize(vals: list[float]) -> dict[str, Any]:
    s = sorted(vals)
    return {"n": len(s), "p50": percentile(s, 50), "p90": percentile(s, 90), "p99": percentile(s, 99)}


def fit_line(points: list[tuple[int, float]]) -> tuple[float, float, float] | None:
    """Least squares unix_time = a + b*slot. Returns (a, b, residual_std) or None."""
    n = len(points)
    if n < 3:
        return None
    x0 = points[0][0]
    xs = [float(p[0] - x0) for p in points]
    ys = [p[1] for p in points]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a0 = my - b * mx
    res = [y - (a0 + b * x) for x, y in zip(xs, ys)]
    sd = math.sqrt(sum(r * r for r in res) / max(n - 2, 1))
    return a0 - b * x0, b, sd  # a such that time = a + b*slot


# ----------------------------------------------------------------------------- collector


class Collector:
    """Pairs signatures across the two sources with bounded memory.

    rows: flat array of (slot, t_public, t_helius), NaN where a source never saw the signature.
    """

    SRC = ("public", "helius")

    def __init__(self, ttl_s: float = PENDING_TTL_S, max_pending: int = MAX_PENDING,
                 max_rows: int = MAX_ROWS) -> None:
        self.ttl_s = ttl_s
        self.max_pending = max_pending
        self.max_rows = max_rows
        self.pending: OrderedDict[str, list[float]] = OrderedDict()  # sig -> [slot, t_pub, t_hel, first_seen]
        self.rows = array("d")
        self.msgs = {"public": 0, "helius": 0}
        self.dups = {"public": 0, "helius": 0}
        self.rows_dropped = 0
        self.max_slot_seen = 0

    def add(self, src: str, sig: str, slot: int, t: float) -> None:
        self.msgs[src] += 1
        self.max_slot_seen = max(self.max_slot_seen, slot)
        idx = 1 if src == "public" else 2
        ent = self.pending.get(sig)
        if ent is None:
            ent = [float(slot), NAN, NAN, t]
            self.pending[sig] = ent
            if len(self.pending) > self.max_pending:
                self._finalise_oldest()
        if not math.isnan(ent[idx]):
            self.dups[src] += 1
            return
        ent[idx] = t
        if not math.isnan(ent[1]) and not math.isnan(ent[2]):
            del self.pending[sig]
            self._emit(ent)

    def _emit(self, ent: list[float]) -> None:
        if len(self.rows) >= 3 * self.max_rows:
            self.rows_dropped += 1
            return
        self.rows.extend((ent[0], ent[1], ent[2]))

    def _finalise_oldest(self) -> None:
        _, ent = self.pending.popitem(last=False)
        self._emit(ent)

    def expire(self, now: float) -> None:
        while self.pending:
            sig = next(iter(self.pending))
            if now - self.pending[sig][3] < self.ttl_s:
                break
            self._finalise_oldest()

    def flush(self) -> None:
        while self.pending:
            self._finalise_oldest()

    def iter_rows(self):
        r = self.rows
        for i in range(0, len(r), 3):
            yield r[i], r[i + 1], r[i + 2]


def build_report(col: Collector, anchors: list[tuple[int, float]], meta: dict[str, Any]) -> dict[str, Any]:
    col.flush()
    fit = fit_line(anchors)
    both = pub_only = hel_only = 0
    pub_first = hel_first = tie = 0
    lag_pub: list[float] = []
    lag_hel: list[float] = []
    lag_pub_both: list[float] = []
    lag_hel_both: list[float] = []
    diffs: list[float] = []
    for slot, tp, th in col.iter_rows():
        pp, hh = not math.isnan(tp), not math.isnan(th)
        bt = (fit[0] + fit[1] * slot) if fit else None
        if pp and hh:
            both += 1
            d = tp - th
            diffs.append(d)
            if d > 0:
                hel_first += 1
            elif d < 0:
                pub_first += 1
            else:
                tie += 1
        elif pp:
            pub_only += 1
        elif hh:
            hel_only += 1
        if bt is not None:
            if pp:
                lag_pub.append(tp - bt)
            if hh:
                lag_hel.append(th - bt)
            if pp and hh:
                lag_pub_both.append(tp - bt)
                lag_hel_both.append(th - bt)
    first_share = None
    if both:
        first_share = {"helius_first": hel_first / both, "public_first": pub_first / both, "tie": tie / both}
    return {
        "meta": meta,
        "signatures": {"seen_by_both": both, "public_only": pub_only, "helius_only": hel_only,
                       "rows_dropped_at_cap": col.rows_dropped},
        "lag_vs_block_time_s": {
            "public_all": summarize(lag_pub), "helius_all": summarize(lag_hel),
            "public_both": summarize(lag_pub_both), "helius_both": summarize(lag_hel_both),
        },
        "paired_public_minus_helius_receive_s": summarize(diffs),
        "first_seen_share_of_both": first_share,
        "block_time_fit": None if not fit else {
            "anchors": len(anchors), "slot_seconds": fit[1], "residual_std_s": fit[2]},
        "messages": dict(col.msgs), "duplicates": dict(col.dups),
    }


# ----------------------------------------------------------------------------- network


class Stats:
    def __init__(self) -> None:
        self.helius_bytes = 0
        self.public_bytes = 0
        self.helius_connections = 0
        self.public_connections = 0
        self.rpc_calls = 0
        self.reconnects = {"public": 0, "helius": 0}
        self.helius_error: str | None = None
        self.public_error: str | None = None

    def helius_credits_estimate(self) -> float:
        return (self.helius_bytes / 100_000.0 * CREDITS_PER_100KB
                + self.helius_connections * CREDITS_PER_CONNECTION
                + self.rpc_calls * CREDITS_PER_RPC_CALL)


class FatalSubscribeError(Exception):
    pass


async def run_source(src: str, url: str, req: dict[str, Any], parse: Callable, col: Collector, stats: Stats,
                     stop: asyncio.Event, key: str | None, connect=None) -> None:
    if connect is None:
        import websockets

        def connect(u):  # noqa: E306
            return websockets.connect(u, ping_interval=20, ping_timeout=20, close_timeout=5,
                                      max_size=8 * 1024 * 1024)
    backoff = 1.0
    while not stop.is_set():
        started = time.monotonic()
        try:
            async with connect(url) as ws:
                setattr(stats, f"{src}_connections", getattr(stats, f"{src}_connections") + 1)
                await ws.send(json.dumps(req))
                acked = False
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    t = time.time()
                    nbytes = len(raw)
                    setattr(stats, f"{src}_bytes", getattr(stats, f"{src}_bytes") + nbytes)
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if "error" in msg and "method" not in msg:
                        err = redact(json.dumps(msg["error"]), key)
                        setattr(stats, f"{src}_error", err)
                        if not acked:
                            raise FatalSubscribeError(err)
                        continue
                    if not acked and msg.get("id") == req["id"] and "result" in msg:
                        acked = True
                        continue
                    parsed = parse(msg)
                    if parsed is not None:
                        col.add(src, parsed[0], parsed[1], t)
        except asyncio.CancelledError:
            raise
        except FatalSubscribeError:
            raise
        except Exception as exc:  # reconnect
            print(f"[{src}] ws_down {type(exc).__name__}: {redact(str(exc), key)[:200]}", file=sys.stderr, flush=True)
        if stop.is_set():
            break
        stats.reconnects[src] += 1
        backoff = 1.0 if time.monotonic() - started > 60 else min(backoff * 2, 30.0)
        await asyncio.sleep(backoff)


def rpc_block_time(slot: int, key: str, timeout: float = 10.0) -> float | None:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBlockTime", "params": [slot]}).encode()
    req = urllib.request.Request(f"{HELIUS_HTTP}/?api-key={key}", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        res = json.loads(r.read()).get("result")
    return float(res) if isinstance(res, int) else None


async def anchor_loop(col: Collector, stats: Stats, anchors: deque, stop: asyncio.Event, key: str,
                      every_s: float = ANCHOR_EVERY_S, fetch: Callable = rpc_block_time) -> None:
    last_slot = 0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=every_s)
            break
        except asyncio.TimeoutError:
            pass
        slot = col.max_slot_seen - ANCHOR_MIN_AGE_SLOTS
        if slot <= 0 or slot <= last_slot:
            continue
        try:
            stats.rpc_calls += 1
            bt = await asyncio.to_thread(fetch, slot, key)
        except Exception as exc:
            print(f"[anchor] {type(exc).__name__}: {redact(str(exc), key)[:200]}", file=sys.stderr, flush=True)
            continue
        if bt is not None:
            anchors.append((slot, bt))
            last_slot = slot


async def housekeeping(col: Collector, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        col.expire(time.time())


def load_key(environ=os.environ) -> str:
    key = (environ.get("HELIUS_API_KEY") or "").strip()
    if not key or any(ch in key for ch in "\r\n& #"):
        raise SystemExit("no usable HELIUS_API_KEY in the environment")
    return key


async def amain(args) -> int:
    key = load_key()
    col, stats = Collector(), Stats()
    anchors: deque = deque(maxlen=MAX_ANCHORS)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, stop.set)
    started = time.time()
    tasks = [
        asyncio.create_task(run_source("public", PUBLIC_WS, public_sub_request(), parse_public, col, stats, stop, key)),
        asyncio.create_task(run_source("helius", f"{HELIUS_WS}/?api-key={key}", helius_sub_request(),
                                       parse_helius, col, stats, stop, key)),
        asyncio.create_task(anchor_loop(col, stats, anchors, stop, key)),
        asyncio.create_task(housekeeping(col, stop)),
    ]
    rc = 0

    async def deadline():
        try:
            await asyncio.wait_for(stop.wait(), timeout=args.minutes * 60)
        except asyncio.TimeoutError:
            stop.set()

    tasks.append(asyncio.create_task(deadline()))
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for t in done:
        exc = t.exception() if not t.cancelled() else None
        if isinstance(exc, FatalSubscribeError):
            rc = 3
            print(f"FATAL subscribe error: {exc}", flush=True)
        elif exc is not None:
            rc = 1
            print(f"FATAL {type(exc).__name__}: {redact(str(exc), key)}", flush=True)
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    ended = time.time()
    meta = {"program": PUMPSWAP_PROGRAM, "started_unix": started, "ended_unix": ended,
            "duration_s": ended - started, "commitment": "processed",
            "helius_filter": "accountInclude=[PumpSwap], failed=false, vote=false, transactionDetails=signatures",
            "public_filter": "logsSubscribe mentions=[PumpSwap], err==null kept",
            "exit_code": rc}
    rep = build_report(col, list(anchors), meta)
    rep["bytes"] = {"helius": stats.helius_bytes, "public": stats.public_bytes}
    rep["connections"] = {"helius": stats.helius_connections, "public": stats.public_connections}
    rep["reconnects"] = dict(stats.reconnects)
    rep["getBlockTime_calls"] = stats.rpc_calls
    rep["helius_credits_estimate"] = stats.helius_credits_estimate()
    rep["helius_credits_formula"] = ("bytes/100000*2 + connections*1 + getBlockTime calls*1; "
                                     "an estimate from the brief's doc figure, not a billed number")
    rep["errors"] = {"helius": stats.helius_error, "public": stats.public_error}
    out = json.dumps(rep, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
    print(out, flush=True)
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
