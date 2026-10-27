#!/usr/bin/env python3
"""dbc_p18_live: wiring for the P18 live PAPER process (pre-build, QP-RULING-1026-p33-p18 Q1). New module; tools/dbc_p18_run.py and every pinned file are NOT edited.
PAPER ONLY: no wallet, no send, no price. Registers nothing. No live call is made by this module's tests (fake sockets, injected fetchers, injected clocks).

Adds around `dbc_p18_run.Runner`:
1. SLOT SIGNAL for slot-close (#978). `slotSubscribe` (second websocket, same Helius key) or a wall-clock estimate turns into `Runner.on_slot_signal(slot - CLOSE_LAG_SLOTS)`. The
   SlotCloser clamps a signal to the highest row slot seen, ignores stale ones, and logs/counts every row that arrives for an already-closed slot (`late_after_close`). A lost slot
   socket sends no signal; the pinned row-driven close then applies (neutral).
2. FIRST-SIGHT CONFIG FETCH. A trade/curve_complete row whose config is in neither dbc_price.CFG_THR nor CFG_UNMAPPED triggers one blocking getAccountInfo (finalized, base64, 3 s
   timeout, 2 attempts) in a THREAD, only for SOL-quoted trade rows (R4's scan scope). Rows keep their true receive stamps; every later row and slot signal waits in one FIFO until the
   fetch ends, so the wait is inside t_decide - t_recv and L90 counts it (see `stall_report`); the account is decoded with tools.dbc_config.summary_map (the read's R4 decoder), thr/transfer-fee go into CFG_THR /
   CFG_XFER exactly as dbc_price.load_config_thresholds does; undecodable or absent = CFG_UNMAPPED (excluded and counted, as R4). One `cfg_fetch` row per config goes to the chain.
3. PROBE (`--probe`): N minutes (default 10) of the feed with a mandatory --credit-cap; NO engine, NO decision log. Records bytes/s, the byte-meter credit estimate (NOT billed
   credits) and the feed-lag p90 by the QP feed-lag method (#968): piecewise-linear getBlockTime anchors (one per 30 s, slots >= 150 old), +0.5 s floor correction.
4. The LIVE entry takes a local `--look-flag` in place of the marker (tools.dbc_p18_gate); start_guard itself is unchanged and still enforces 2026-11-01T00:10Z.

    python3 -m tools.dbc_p18_live --probe --env-file ENV --credit-cap N --look-flag FLAG --out DIR [--minutes 10]
    python3 -m tools.dbc_p18_live --live  --env-file ENV --credit-cap N --look-flag FLAG --out DIR [--slot-source ws|wall|none]
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from tools import dbc_config as C
from tools import dbc_fwd as F
from tools import dbc_fwd_feed as FF
from tools import dbc_p18_gate as G
from tools import dbc_p18_run as P
from tools import dbc_price as L

CLOSE_LAG_SLOTS = 2  # signal = notified slot - lag: rows of the last 2 slots may still be in flight on a processed feed. Outcome-blind constant; late rows are counted and bar-checked.
WALL_SLOT_S = 0.4
WALL_WINDOW_S = 120.0
CFG_TIMEOUT_S, CFG_ATTEMPTS = 3.0, 2
ANCHOR_EVERY_S, ANCHOR_MIN_AGE_SLOTS = 30.0, 150
PROBE_MINUTES = 10.0
CODE_FILES = ("dbc_p18_live.py", "dbc_p18_gate.py")


# ------------------------------------------------------------------------------------------------------------------------------ 1. slot signal
def slot_subscribe_request(req_id: int = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "method": "slotSubscribe"}


def parse_slot_notification(raw: str | bytes) -> int | None:
    """slotNotification -> slot (int) or None for anything else (subscribe ack, junk)."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict) or msg.get("method") != "slotNotification":
        return None
    s = (((msg.get("params") or {}).get("result")) or {}).get("slot")
    return s if isinstance(s, int) and not isinstance(s, bool) else None


class SlotSignals:
    """Notified slot -> close signal (slot - lag), strictly increasing."""

    def __init__(self, lag: int = CLOSE_LAG_SLOTS) -> None:
        self.lag, self.last = int(lag), None

    def feed(self, slot: int) -> int | None:
        sig = int(slot) - self.lag
        if self.last is not None and sig <= self.last:
            return None
        self.last = sig
        return sig


class WallClockSlots:
    """Slot estimate without a slot socket. Each row gives offset = t_recv - slot * WALL_SLOT_S; the minimum over the last WALL_WINDOW_S is the best-case feed lag plus the
    chain epoch, so (now - offset_min) / WALL_SLOT_S is the newest slot the feed can have delivered by now. Drift of the true slot time from 0.4 s moves it by at most a few slots
    inside the window; an early signal only makes rows late (counted), never un-decides."""

    def __init__(self, lag: int = CLOSE_LAG_SLOTS, slot_s: float = WALL_SLOT_S, window_s: float = WALL_WINDOW_S) -> None:
        self.sig, self.slot_s, self.window_s = SlotSignals(lag), slot_s, window_s
        self.obs: deque[tuple[float, float]] = deque()

    def observe(self, slot: int, t_recv_s: float) -> None:
        self.obs.append((t_recv_s, t_recv_s - slot * self.slot_s))
        while self.obs and self.obs[0][0] < t_recv_s - self.window_s:
            self.obs.popleft()

    def tick(self, now_s: float) -> int | None:
        if not self.obs:
            return None
        off = min(o for _, o in self.obs)
        return self.sig.feed(int((now_s - off) / self.slot_s))


async def run_slot_feed(url: str, on_slot: Callable[[int], Any], stop: asyncio.Event, *, connect: Callable[[str], Any], meter: FF.Meter, stats: dict[str, int],
                        sleep: Callable[[float], Any] = asyncio.sleep, recv_timeout_s: float = 20.0) -> None:
    """slotSubscribe on its own connection, reconnect with backoff. `on_slot(slot)` gets every notified slot. Bytes go to `meter` (no cap on this meter). No gap rows: a lost
    slot socket just means no signals (the pinned row-driven close covers it)."""
    backoff = FF.INITIAL_BACKOFF_S
    while not stop.is_set():
        try:
            meter.connections += 1
            async with connect(url) as ws:
                await ws.send(json.dumps(slot_subscribe_request()))
                backoff = FF.INITIAL_BACKOFF_S
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout_s)
                    except asyncio.TimeoutError:
                        stats["timeouts"] = stats.get("timeouts", 0) + 1
                        break
                    meter.bytes += len(raw)
                    meter.msgs += 1
                    s = parse_slot_notification(raw)
                    if s is not None:
                        stats["notifications"] = stats.get("notifications", 0) + 1
                        on_slot(s)
        except Exception as e:  # noqa: BLE001 - never let a key-bearing message out unredacted
            stats["errors"] = stats.get("errors", 0) + 1
            FF.log.warning("slot ws closed %s: %s", type(e).__name__, FF.redact(str(e))[:200])
        if stop.is_set():
            break
        stats["reconnects"] = stats.get("reconnects", 0) + 1
        await sleep(backoff)
        backoff = min(backoff * 2, FF.MAX_BACKOFF_S)


# ------------------------------------------------------------------------------------------------------------------------------ 2. config fetch
def make_account_fetch(url: str, timeout_s: float = CFG_TIMEOUT_S, urlopen: Callable[..., Any] = urllib.request.urlopen) -> Callable[[str], dict[str, str] | None]:
    """getAccountInfo(config, finalized, base64) -> {"data_b64": ...} (the shape tools.dbc_config.summary_map reads) or None when the account does not exist. Raises on RPC/HTTP error."""
    def fetch(cfg: str) -> dict[str, str] | None:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [cfg, {"encoding": "base64", "commitment": "finalized"}]}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=timeout_s) as resp:
                res = json.loads(resp.read())
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(FF.redact(f"{type(e).__name__}: {e}")[:200]) from None
        if not isinstance(res, dict) or "error" in res:
            raise RuntimeError("rpc error")
        v = (res.get("result") or {}).get("value")
        return {"data_b64": v["data"][0]} if v else None

    return fetch


class ConfigGate:
    """First sight of an unmapped config on a SOL-quoted trade row (the rows R4's scan covers): fetch once, decode with the read's decoder, update the dbc_price maps.
    Split in three so the blocking part can run off the event loop: `needs` (cheap test), `fetch_raw` (blocking RPC, touches no shared map: safe in a thread), `apply` (map update, loop thread).
    `check` = all three inline (tests and the md5 harness, where the fetcher must never be called)."""

    def __init__(self, fetch: Callable[[str], dict[str, str] | None], attempts: int = CFG_ATTEMPTS, clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000) -> None:
        self.fetch, self.attempts, self.clock_ms = fetch, attempts, clock_ms
        self.reasons: dict[str, str] = {}  # unmapped config -> absent | undecodable | rpc_error
        self.stats = {"sights": 0, "mapped": 0, "unmapped": 0, "rpc_calls": 0, "rpc_errors": 0, "stall_ms_total": 0, "stall_ms_max": 0}

    @staticmethod
    def needs(r: dict[str, Any]) -> bool:
        cfg = r.get("config")
        return bool(cfg) and r.get("type") == "trade" and r.get("quote_mint") == L.WSOL and cfg not in L.CFG_THR and cfg not in L.CFG_UNMAPPED

    def fetch_raw(self, cfg: str) -> dict[str, Any]:
        t0 = self.clock_ms()
        raw, err, n = None, None, 0
        for n in range(1, self.attempts + 1):
            self.stats["rpc_calls"] += 1
            try:
                raw, err = self.fetch(cfg), None
                break
            except Exception as e:  # noqa: BLE001
                err = FF.redact(f"{type(e).__name__}: {e}")[:120]
                self.stats["rpc_errors"] += 1
        return {"cfg": cfg, "raw": raw, "err": err, "attempts": n, "t_start_ms": t0, "t_end_ms": self.clock_ms()}

    def apply(self, res: dict[str, Any]) -> dict[str, Any]:
        cfg, raw, err = res["cfg"], res["raw"], res["err"]
        dur = res["t_end_ms"] - res["t_start_ms"]
        self.stats["sights"] += 1
        self.stats["stall_ms_total"] += dur
        self.stats["stall_ms_max"] = max(self.stats["stall_ms_max"], dur)
        base = {"type": "cfg_fetch", "config": cfg, "attempts": res["attempts"], "t_start_ms": res["t_start_ms"], "t_end_ms": res["t_end_ms"], "fetch_ms": dur}
        m = C.summary_map({cfg: raw}) if raw else {}
        if cfg in m:
            v = m[cfg]
            L.CFG_THR[cfg] = int(v["thr"])
            xfer = int(v.get("transfer_fee_bps") or 0) > 0
            if xfer:
                L.CFG_XFER.add(cfg)
            self.stats["mapped"] += 1
            return {**base, "result": "mapped", "thr": int(v["thr"]), "xfer": xfer, "hook": bool(v.get("hook"))}
        reason = "rpc_error" if err else ("absent" if raw is None else "undecodable")
        L.CFG_UNMAPPED.add(cfg)
        self.reasons[cfg] = reason
        self.stats["unmapped"] += 1
        return {**base, "result": "unmapped", "reason": reason, "error": err}

    def check(self, r: dict[str, Any]) -> dict[str, Any] | None:
        if not self.needs(r):
            return None
        return self.apply(self.fetch_raw(r["config"]))


# ------------------------------------------------------------------------------------------------------------------------------ runner
class WiredRunner(P.Runner):
    """dbc_p18_run.Runner + config gate + slot-signal entry points. With an empty gate and no signals it writes the same chain as Runner plus one `wire` row after `start`."""

    def __init__(self, out: str | Path, *, gate: ConfigGate | None = None, slot_source: str = "none", close_lag: int = CLOSE_LAG_SLOTS, slot_meter: FF.Meter | None = None,
                 **kw: Any) -> None:
        self.gate, self.slot_source, self.close_lag, self.slot_meter = gate, slot_source, close_lag, slot_meter
        self.sigs = SlotSignals(close_lag)
        self.wall = WallClockSlots(close_lag) if slot_source == "wall" else None
        self.slot_stats: dict[str, int] = {}
        self.hold, self.fifo, self.fetch_waits = False, deque(), 0
        self.sol_trade_rows = self.unmapped_rows = 0
        super().__init__(out, **kw)

    def _ensure(self, t_s: float) -> None:
        first = self.eng is None
        super()._ensure(t_s)
        if first:
            here = Path(__file__).resolve().parent
            self.log.append(F.BOOK, int(t_s * 1000), {"type": "wire", "slot_source": self.slot_source, "close_lag_slots": self.close_lag, "cfg_first_sight_fetch": self.gate is not None,
                                                      "cfg_attempts": CFG_ATTEMPTS, "cfg_fetch_scope": "sol_quoted_trade_rows", "cfg_fetch_hold": "fifo_off_loop", "code_sha256": {f: F.sha256_file(here / f) for f in CODE_FILES}})

    # -- ingest. The feed's sink is `ingest`: rows keep the receive stamp the feed gave them (t_recv_ms is set BEFORE the sink runs). A first-sight config fetch runs in a thread;
    # while it runs every later row and slot signal waits in one FIFO and is delivered in order when the fetch ends. The wait is therefore inside t_decide - t_recv (L90 sees it).
    def ingest(self, r: dict[str, Any]) -> None:
        if self.hold:
            self.fifo.append(("row", r))
            return
        self._deliver(r)

    def _deliver(self, r: dict[str, Any]) -> None:
        if self.gate is not None and self.gate.needs(r):
            self._ensure(L.block_time_s(r))
            self.hold = True
            self.fifo.append(("row", r))
            self.fetch_waits += 1
            asyncio.get_running_loop().create_task(self._fetch_then_drain(r["config"]))
            return
        self.on_row(r)

    async def _fetch_then_drain(self, cfg: str) -> None:
        assert self.gate is not None
        res = await asyncio.get_running_loop().run_in_executor(None, self.gate.fetch_raw, cfg)
        ev = self.gate.apply(res)
        self.log.append(F.BOOK, int(res["t_end_ms"]), {**ev, "book": F.BOOK, "fifo_len": len(self.fifo)})
        self.hold = False
        self.drain()

    def drain(self) -> None:
        while self.fifo and not self.hold:
            kind, x = self.fifo.popleft()
            if kind == "row":
                self._deliver(x)
            else:
                self._signal(x)

    def on_row(self, r: dict[str, Any]) -> None:
        if r.get("type") == "trade" and r.get("quote_mint") == L.WSOL:
            self.sol_trade_rows += 1
            if r.get("config") in L.CFG_UNMAPPED:
                self.unmapped_rows += 1
        if r.get("type") in ("trade", "curve_complete") and self.wall is not None and r.get("slot") is not None:
            self.wall.observe(int(r["slot"]), L.block_time_s(r))
        super().on_row(r)

    def _signal(self, sig: int) -> bool:
        return self.on_slot_signal(sig)

    def on_slot_notified(self, slot: int) -> bool:
        """A slotNotification arrived (slotSubscribe): close slots <= slot - lag (queued behind a pending config fetch, in order)."""
        sig = self.sigs.feed(slot)
        if sig is None:
            return False
        if self.hold:
            self.fifo.append(("sig", sig))
            return False
        return self._signal(sig)

    def wall_tick(self, now_s: float) -> bool:
        sig = self.wall.tick(now_s) if self.wall is not None else None
        if sig is None:
            return False
        if self.hold:
            self.fifo.append(("sig", sig))  # same FIFO as the rows: never close a slot before the queued rows of that slot are delivered
            return False
        return self._signal(sig)

    def report(self) -> dict[str, Any]:
        st = super().report()
        st["stalls"] = stall_report(self.log.rows(F.BOOK))
        return st

    def unmapped_report(self) -> dict[str, Any]:
        """R4's bar on the dry-score day: unmapped SOL-quoted trade rows / all SOL-quoted trade rows must be <= 5%; split by reason (absent / undecodable / rpc_error)."""
        by: dict[str, int] = {}
        for c, why in (self.gate.reasons if self.gate else {}).items():
            by[why] = by.get(why, 0) + 1
        share = self.unmapped_rows / self.sol_trade_rows if self.sol_trade_rows else None
        return {"sol_trade_rows": self.sol_trade_rows, "unmapped_rows": self.unmapped_rows, "share": share, "bar": 0.05, "ok": (share <= 0.05) if share is not None else None,
                "configs_by_reason": by, "fetch_waits": self.fetch_waits, "fifo_len": len(self.fifo)}

    def tick(self) -> dict[str, Any]:
        st = super().tick()
        st["wire"] = {"slot_source": self.slot_source, "close_lag_slots": self.close_lag, "slot_feed": dict(self.slot_stats), "cfg_gate": dict(self.gate.stats) if self.gate else None,
                      "cfg_unmapped": self.unmapped_report()}
        if self.slot_meter is not None:
            st["slot_feed_meter"] = FP_row(self.slot_meter)
        tmp = self.out / "status.json.tmp"
        tmp.write_text(json.dumps(st, default=float))
        os.replace(tmp, self.out / "status.json")
        return st


def stall_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How L90 accounts for config-fetch stalls (fixed before the dry score). While a first-sight fetch runs, rows keep their TRUE receive stamps and wait in a FIFO, so the wait is
    inside (t_decide - t_recv_trigger) and L90 counts it in full with no correction. This report only prints: fetches, total / max fetch time, and the decisions whose trigger row was
    received during a fetch window (the ones the stall touched). Reads `cfg_fetch` and `decision` rows only (never exit rows)."""
    f = [r for r in rows if r.get("type") == "cfg_fetch"]
    win = [(r["t_start_ms"], r["t_end_ms"]) for r in f]
    dec = [r for r in rows if r.get("type") == "decision" and "t_recv_ms" in r]
    touched = sum(1 for r in dec if any(a <= r["t_recv_ms"] <= b for a, b in win))
    ms = [b - a for a, b in win]
    return {"fetches": len(f), "fetch_ms_total": sum(ms), "fetch_ms_max": max(ms) if ms else 0, "decisions_with_stamped_trigger": len(dec), "decisions_received_during_a_fetch": touched}


def FP_row(m: FF.Meter) -> dict[str, Any]:
    return P.feed_row(m)


# ------------------------------------------------------------------------------------------------------------------------------ 3. probe
def feed_lag_report(rows: list[tuple[int, int]], anchors: list[tuple[int, int]]) -> dict[str, Any]:
    """QP feed-lag method (#968): block time of a row's slot = piecewise-linear interpolation between getBlockTime anchors (+0.5 s: getBlockTime floors to a second);
    lag = t_recv - that. Rows outside the anchor span are not used. `resid_sd_s` = leave-one-out error of the interior anchors (how good the interpolation is)."""
    out: dict[str, Any] = {"n_anchors": len(anchors), "n_rows_used": 0, "p50_s": None, "p90_s": None, "p99_s": None, "resid_sd_s": None}
    a = sorted(set(anchors))
    if len(a) < 2 or not rows:
        return out
    sl, bt = np.array([x[0] for x in a], float), np.array([x[1] for x in a], float)
    rs = np.array([r[0] for r in rows], float)
    tr = np.array([r[1] for r in rows], float) / 1000.0
    use = (rs >= sl[0]) & (rs <= sl[-1])
    if not use.any():
        return out
    lag = tr[use] - (np.interp(rs[use], sl, bt) + 0.5)
    out.update({"n_rows_used": int(use.sum()), "p50_s": float(np.quantile(lag, 0.5)), "p90_s": float(np.quantile(lag, 0.9)), "p99_s": float(np.quantile(lag, 0.99))})
    if len(a) >= 3:
        pred = np.array([np.interp(sl[i], [sl[i - 1], sl[i + 1]], [bt[i - 1], bt[i + 1]]) for i in range(1, len(a) - 1)])
        out["resid_sd_s"] = float(np.std(bt[1:-1] - pred))
    return out


async def run_probe(url: str, rpc: Callable[[str, list[Any]], Any], out: Path, credit_cap: float, *, minutes: float = PROBE_MINUTES, connect: Callable[[str], Any],
                    clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000, sleep: Callable[[float], Any] = asyncio.sleep, anchor_every_s: float = ANCHOR_EVERY_S) -> dict[str, Any]:
    """Feed only (no engine, no decision log): count bytes, sample getBlockTime anchors, write OUT/probe.json. `rpc(method, params)` is a blocking JSON-RPC call (injected)."""
    if credit_cap is None or credit_cap <= 0:
        raise ValueError("--credit-cap is required for the probe")
    out.mkdir(parents=True, exist_ok=True)
    meter, gaps, stop = FF.Meter(credit_cap), FF.Gaps(), asyncio.Event()
    rows: list[tuple[int, int]] = []
    slots: deque[int] = deque(maxlen=20000)
    anchors: list[tuple[int, int]] = []
    calls = [0]
    t0 = clock_ms()

    def sink(r: dict[str, Any]) -> None:
        if r.get("type") in ("trade", "curve_complete") and isinstance(r.get("slot"), int) and len(rows) < 2_000_000:
            rows.append((int(r["slot"]), int(r["t_recv_ms"])))
            if not slots or r["slot"] > slots[-1]:
                slots.append(int(r["slot"]))

    async def anchors_task() -> None:
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            await sleep(anchor_every_s)
            if stop.is_set() or not slots:
                continue
            cand = [s for s in slots if s <= slots[-1] - ANCHOR_MIN_AGE_SLOTS]
            if not cand:
                continue
            for d in range(4):  # a skipped slot has no block time: next slot
                s = cand[-1] + d
                calls[0] += 1
                try:
                    bt = await loop.run_in_executor(None, rpc, "getBlockTime", [s])
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(bt, int):
                    anchors.append((s, bt))
                    break

    async def timer() -> None:
        await sleep(minutes * 60.0)
        stop.set()

    tasks = [asyncio.create_task(anchors_task()), asyncio.create_task(timer())]
    try:
        rc = await FF.run_feed(url, sink, stop, connect=connect, meter=meter, gaps=gaps, clock_ms=clock_ms)
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
    secs = max((clock_ms() - t0) / 1000.0, 1e-9)
    lag = feed_lag_report(rows, anchors)
    est = meter.credits() + calls[0]  # getBlockTime = 1 credit each (assumed)
    res = {"kind": "p18-probe", "exit": rc, "seconds": round(secs, 1), "bytes": meter.bytes, "msgs": meter.msgs, "connections": meter.connections, "bytes_per_s": round(meter.bytes / secs, 1),
           "msgs_per_s": round(meter.msgs / secs, 2), "getBlockTime_calls": calls[0], "credits_est_meter": round(meter.credits(), 1), "credits_est_total": round(est, 1),
           "credits_basis": "byte-based estimate, not billed credits; record the Helius dashboard delta over the probe window (owner/Helm)",
           "credits_est_is_lower_bound": True, "getBlockTime_note": "logical calls; http_rpc may retry up to 6 HTTP attempts per call, so credits_est_total is a LOWER BOUND", "credits_per_day_est": round(est / secs * 86400, 0),
           "day_bar_credits": 3 * 85_000, "feed_lag": lag, "feed_lag_term_s": max(P.FEED_LAG_FLOOR_S, lag["p90_s"]) if lag["p90_s"] is not None else None,
           "gaps": gaps.count, "rows": len(rows)}
    (out / "probe.json").write_text(json.dumps(res, indent=1))
    return res


# ------------------------------------------------------------------------------------------------------------------------------ live
async def run_live_wired(out: str, env_file: str, credit_cap: float | None, look_flag: str, *, prune: bool = True, slot_source: str = "ws", close_lag: int = CLOSE_LAG_SLOTS,
                         now: datetime | None = None, connect: Callable[[str], Any] | None = None, fetch: Callable[[str], Any] | None = None,
                         cfg_summary: str | None = None) -> int:
    from tools import venue_backfill as VB  # noqa: PLC0415

    now = now or datetime.now(timezone.utc)
    P.start_guard(live=True, test=False, now=now, marker=look_flag, marker_exists=G.flag_valid)
    if connect is None:
        try:
            import websockets  # noqa: PLC0415
        except ImportError:
            print("REFUSED: python package 'websockets' is not installed in this interpreter", file=sys.stderr)
            return P.EXIT_REFUSED
        connect = websockets.connect
    key = VB.read_key(env_file)
    url = FF.ws_url(key)
    gate = ConfigGate(fetch or make_account_fetch(VB.make_url(key)))
    meter, gaps, slot_meter = FF.Meter(credit_cap), FF.Gaps(), FF.Meter(None)
    run = WiredRunner(out, gate=gate, slot_source=slot_source, close_lag=close_lag, slot_meter=slot_meter, prune=prune, live=True, bt_src="recv", start_ts=now.timestamp(), meter=meter, cfg_summary=cfg_summary)
    stop = asyncio.Event()

    async def watchdog() -> None:
        n = 0
        while not stop.is_set():
            await asyncio.sleep(P.WATCHDOG_S)
            st = run.tick()
            n += 1
            if n % int(60 / P.WATCHDOG_S) == 0:
                print(json.dumps({k: st.get(k) for k in ("utc", "rss_mib", "pools", "rows_retained", "decisions", "feed", "slot_close", "wire")}), flush=True)
            if run.mem_exceeded:
                stop.set()

    async def wall_ticker() -> None:
        while not stop.is_set():
            await asyncio.sleep(WALL_SLOT_S)
            run.wall_tick(time.time())

    tasks = [asyncio.create_task(watchdog())]
    if slot_source == "ws":
        tasks.append(asyncio.create_task(run_slot_feed(url, run.on_slot_notified, stop, connect=connect, meter=slot_meter, stats=run.slot_stats)))
    elif slot_source == "wall":
        tasks.append(asyncio.create_task(wall_ticker()))
    try:
        rc = await FF.run_feed(url, run.ingest, stop, connect=connect, meter=meter, gaps=gaps)
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
    if run.mem_exceeded and rc == 0:
        rc = P.EXIT_MEM
    reason = {0: "stop", FF.EXIT_CAP: "credit_cap", P.EXIT_MEM: "memory_bar"}.get(rc, "stop")
    run.tick()
    run.log.append(F.BOOK, run.clock_ms(), {"type": "stop", "reason": reason, **P.feed_row(meter), "slot_feed_bytes": slot_meter.bytes, "rss_mib": round(run.rss(), 1),
                                            "peak_rss_mib": round(P.peak_rss_mib(), 1)})
    print(json.dumps({"stop": reason, "feed": P.feed_row(meter), "slot_feed": FP_row(slot_meter), "rss_mib": round(run.rss(), 1)}), flush=True)
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--env-file", default="")
    ap.add_argument("--credit-cap", type=float, default=None)
    ap.add_argument("--look-flag", default="", help="local flag written by tools.dbc_p18_gate arm (or the marker itself on research-0)")
    ap.add_argument("--slot-source", choices=("ws", "wall", "none"), default="ws")
    ap.add_argument("--close-lag", type=int, default=CLOSE_LAG_SLOTS)
    ap.add_argument("--minutes", type=float, default=PROBE_MINUTES)
    ap.add_argument("--no-prune", action="store_true")
    ap.add_argument("--out", required=True)
    ns = ap.parse_args(argv)
    if ns.live == ns.probe:
        print("REFUSED: exactly one of --live / --probe is required", file=sys.stderr)
        return P.EXIT_REFUSED
    try:
        if not ns.look_flag or not ns.env_file:
            raise P.StartRefused("--look-flag and --env-file are required")
        if ns.live and (ns.slot_source != "ws" or ns.close_lag != CLOSE_LAG_SLOTS):
            raise P.StartRefused("--live is pinned to --slot-source ws --close-lag 2 (QP #1067 Q-C); wall/none are test-only")
        if ns.probe and (ns.credit_cap is None or ns.credit_cap <= 0 or ns.minutes > 30):
            raise P.StartRefused("--probe needs --credit-cap > 0 and --minutes <= 30")
        P.start_guard(live=True, test=False, now=datetime.now(timezone.utc), marker=os.path.expanduser(ns.look_flag), marker_exists=G.flag_valid,
                      paths=tuple(p for p in (ns.out, ns.look_flag) if p))
    except P.StartRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return P.EXIT_REFUSED
    flag = os.path.expanduser(ns.look_flag)
    if ns.live:
        return asyncio.run(run_live_wired(ns.out, ns.env_file, ns.credit_cap, flag, prune=not ns.no_prune, slot_source=ns.slot_source, close_lag=ns.close_lag))
    import websockets  # noqa: PLC0415
    from tools import venue_backfill as VB  # noqa: PLC0415

    key = VB.read_key(ns.env_file)
    http = VB.http_rpc(VB.make_url(key), VB.Pacer(2.0), timeout=5.0)
    res = asyncio.run(run_probe(FF.ws_url(key), http, Path(ns.out), ns.credit_cap, minutes=ns.minutes, connect=websockets.connect))
    print(json.dumps(res))
    return res["exit"]


if __name__ == "__main__":
    sys.exit(main())
