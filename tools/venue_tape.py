"""Venue tape recorder: public logsSubscribe on other Solana launchpad programs, hourly JSONL (zstd when closed). Paper only. No keys.

Venues (program ids verified executable on mainnet 2026-10-24; LaunchLab id also matches the official IDL address):
  launchlab    Raydium LaunchLab (LetsBonk is a platform on it; the logs do not name the platform)   LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj
  meteora_dbc  Meteora Dynamic Bonding Curve                                                          dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN

Decode only what the logs give, never guess:
  * launchlab: Anchor events are emitted with `Program data:`. TradeEvent (pre/post real reserves, virtual reserves, amounts, fees, direction) and
    PoolCreateEvent (pool, creator, config) are decoded from the IDL layout; any other event, or a TradeEvent of an unexpected length, is an
    `event_undecoded` row carrying its discriminator and base64. The signer (trader) is NOT in the logs: `trader` is absent.
  * meteora_dbc: its events are self-CPI (no `Program data` in the logs), so amounts, reserves and signer are not recoverable from logs. One `ix`
    row per transaction: instruction names, failed flag, CPI depth. A swap row means "a swap instruction ran", nothing about size.
Every row: v, venue, type, slot, t_recv_ms, signature, event_index. Special rows: start, stop, gap, gap_end, hb, disk_pause, disk_resume.
Tape schema: tools/venue_tape_schema.md. Does not import or change tools/arena_feed.py; reuses observe.trade_source sockets and tools.arena_zst.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import fcntl
import json
import logging
import os
import signal
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("venue_tape")

SCHEMA_V = 1
HOUR_MS = 3_600_000
CLOSE_GRACE_S = 120
DISK_MIN_FREE = 0.20
DEFAULT_OUT = "/data2/mal/venue-tape"
DEFAULT_WS = ["wss://api.mainnet-beta.solana.com", "wss://public.rpc.solanavibestation.com"]

VENUES: dict[str, dict[str, Any]] = {
    "launchlab": {"program": "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj", "silence_s": 900},
    "meteora_dbc": {"program": "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN", "silence_s": 90},
}

# Anchor event discriminators from the official raydium_launchpad IDL (sha256("event:<Name>")[:8]).
LL_TRADE_DISC = bytes([189, 219, 127, 211, 78, 230, 97, 238])
LL_CREATE_DISC = bytes([151, 215, 226, 9, 118, 161, 115, 174])
LL_TRADE_LEN = 8 + 32 + 13 * 8 + 3  # disc + pool_state + 13 u64 + direction + status + exact_in
LL_CREATE_MIN = 8 + 32 * 3
LL_STATUS = {0: "fund", 1: "migrate", 2: "trade"}
MAX_UNDECODED_B64 = 1400

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


# ---------------------------------------------------------------------------------------------------------------- log parsing
def program_events(logs: Iterable[str], program: str) -> dict[str, Any]:
    """Walk the invoke stack of one transaction's logs. Returns what the TARGET program itself logged:
    ix (instruction names, in order), data (base64 `Program data` payloads, in order), depth (min invoke depth of the program, 1 = top level),
    truncated (the node cut the logs)."""
    stack: list[str] = []
    ix: list[str] = []
    data: list[str] = []
    depth_min = 0
    truncated = False
    for ln in logs:
        if ln.startswith("Program ") and " invoke [" in ln:
            pid = ln[8:].split(" ", 1)[0]
            stack.append(pid)
            if pid == program:
                try:
                    d = int(ln.rsplit("[", 1)[1].rstrip("]"))
                except ValueError:
                    d = 0
                depth_min = d if depth_min == 0 else min(depth_min, d)
            continue
        if ln.startswith("Program ") and (ln.endswith(" success") or " failed: " in ln):
            if stack:
                stack.pop()
            continue
        if ln.startswith("Log truncated"):
            truncated = True
            continue
        if stack and stack[-1] == program:
            if ln.startswith("Program log: Instruction: "):
                ix.append(ln[26:].strip())
            elif ln.startswith("Program data: "):
                data.append(ln[14:].strip())
    return {"ix": ix, "data": data, "depth": depth_min, "truncated": truncated}


def _u64(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 8], "little")


def decode_ll_event(b64: str) -> dict[str, Any]:
    """One LaunchLab `Program data` payload -> a row body (type + fields) or an event_undecoded body. Never raises."""
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:  # noqa: BLE001
        return {"type": "event_undecoded", "reason": "bad_base64", "data_b64": b64[:MAX_UNDECODED_B64]}
    disc = raw[:8]
    if disc == LL_TRADE_DISC:
        if len(raw) != LL_TRADE_LEN:
            return {"type": "event_undecoded", "reason": f"trade_len_{len(raw)}", "disc": disc.hex(), "data_b64": b64[:MAX_UNDECODED_B64]}
        names = ["total_base_sell", "virtual_base", "virtual_quote", "real_base_before", "real_quote_before", "real_base_after",
                 "real_quote_after", "amount_in", "amount_out", "protocol_fee", "platform_fee", "creator_fee", "share_fee"]
        row: dict[str, Any] = {"type": "trade", "pool": b58(raw[8:40])}
        for i, n in enumerate(names):
            row[n] = _u64(raw, 40 + 8 * i)
        d, st, ex = raw[144], raw[145], raw[146]
        if d not in (0, 1) or st not in LL_STATUS or ex not in (0, 1):
            return {"type": "event_undecoded", "reason": "trade_enum", "disc": disc.hex(), "data_b64": b64[:MAX_UNDECODED_B64]}
        row["side"] = "buy" if d == 0 else "sell"
        row["pool_status"] = LL_STATUS[st]
        row["exact_in"] = bool(ex)
        return row
    if disc == LL_CREATE_DISC:
        if len(raw) < LL_CREATE_MIN:
            return {"type": "event_undecoded", "reason": f"create_len_{len(raw)}", "disc": disc.hex(), "data_b64": b64[:MAX_UNDECODED_B64]}
        # only the three leading pubkeys are decoded; the params (mint, curve, vesting) that follow are kept raw, undecoded
        return {"type": "create", "pool": b58(raw[8:40]), "creator": b58(raw[40:72]), "config": b58(raw[72:104]),
                "params_undecoded_b64": b64[:MAX_UNDECODED_B64], "params_truncated": len(b64) > MAX_UNDECODED_B64}
    return {"type": "event_undecoded", "reason": "unknown_disc", "disc": disc.hex(), "data_b64": b64[:MAX_UNDECODED_B64]}


def rows_for_venue(venue: str, note: Any) -> list[dict[str, Any]]:
    """RawNotice -> rows for this venue ([] when the venue's program is not in the transaction). Pure."""
    prog = VENUES[venue]["program"]
    pe = program_events(note.logs, prog)
    if pe["depth"] == 0:
        return []  # the program id was only mentioned (account list), never invoked in the logs we got
    base = {"v": SCHEMA_V, "venue": venue, "slot": note.slot, "t_recv_ms": note.t_recv_ms, "signature": note.signature}
    flags: dict[str, Any] = {}
    if note.failed:
        flags["failed"] = True
    if pe["truncated"]:
        flags["logs_truncated"] = True
    if pe["depth"] > 1:
        flags["via_cpi"] = True
    rows: list[dict[str, Any]] = []
    if venue == "launchlab":
        counts: dict[str, int] = {}
        for b in pe["data"]:
            body = decode_ll_event(b)
            t = body["type"]
            body["event_index"] = counts.get(t, 0)
            counts[t] = body["event_index"] + 1
            rows.append({**base, **flags, **body, "ix": pe["ix"]})
        if not rows:  # failed tx, claim/config instruction, or truncated logs: keep that the program ran
            rows.append({**base, **flags, "type": "ix", "event_index": 0, "ix": pe["ix"]})
    else:  # meteora_dbc: events are self-CPI, not in the logs
        rows.append({**base, **flags, "type": "ix", "event_index": 0, "ix": pe["ix"], "events_in_logs": bool(pe["data"]),
                     "amounts_undecoded": True})
        for b in pe["data"]:
            rows.append({**base, **flags, "type": "event_undecoded", "event_index": len(rows) - 1, "reason": "dbc_data_not_decoded",
                         "data_b64": b[:MAX_UNDECODED_B64]})
    return rows


# ------------------------------------------------------------------------------------------------------------------- writer
def hour_name(t_ms: int) -> str:
    return "tape-" + datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"


def hour_start_ms(name: str) -> int:
    s = name.split("tape-", 1)[1].split(".jsonl", 1)[0]
    return int(datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)


def free_fraction(path: Path) -> float:
    u = shutil.disk_usage(path)
    return u.free / u.total if u.total else 0.0


class TapeWriter:
    """Hourly JSONL per venue directory, append + flush per line. A closed hour (end + CLOSE_GRACE_S) is never appended to: late rows go to the
    current hour. The disk guard pauses all writing below DISK_MIN_FREE free (a disk_pause row is written once when it trips, if possible)."""

    def __init__(self, root: Path, venues: Iterable[str], min_free: float = DISK_MIN_FREE, free_fn=free_fraction, clock=time.time) -> None:
        self.root = root
        self.venues = list(venues)
        self.min_free = min_free
        self._free_fn = free_fn
        self._clock = clock
        self.dirs = {v: root / v for v in self.venues}
        for d in self.dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self._fh: dict[str, tuple[str, Any]] = {}
        self.paused = False
        self.dropped_while_paused = 0
        self.rows_written: dict[str, int] = {v: 0 for v in self.venues}
        self.torn_lines_ended = 0
        self._last_check = 0.0
        self.free = 1.0
        self._lock = threading.Lock()

    def _guard(self, now: float) -> bool:
        if now - self._last_check >= 5.0:
            self._last_check = now
            self.free = self._free_fn(self.root)
            was = self.paused
            self.paused = self.free < self.min_free
            if self.paused and not was:
                log.error("disk_guard_pause free=%.3f", self.free)
                self._write_all({"type": "disk_pause", "free_fraction": round(self.free, 4)}, force=True)
            elif was and not self.paused:
                log.warning("disk_guard_resume free=%.3f", self.free)
                self._write_all({"type": "disk_resume", "free_fraction": round(self.free, 4), "rows_dropped": self.dropped_while_paused})
                self.dropped_while_paused = 0
        return not self.paused

    def _open(self, venue: str, t_ms: int):
        now_ms = int(self._clock() * 1000)
        name = hour_name(t_ms)
        if hour_start_ms(name) + HOUR_MS + CLOSE_GRACE_S * 1000 <= now_ms:
            name = hour_name(now_ms)
        cur = self._fh.get(venue)
        if cur and cur[0] == name:
            return cur[1]
        if cur:
            cur[1].close()
        path = self.dirs[venue] / name
        if path.exists() and path.stat().st_size > 0:
            with open(path, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                torn = fh.read(1) != b"\n"
        else:
            torn = False
        fh = open(path, "a", encoding="utf-8")
        if torn:
            fh.write("\n")
            self.torn_lines_ended += 1
        self._fh[venue] = (name, fh)
        return fh

    def write(self, venue: str, row: dict[str, Any]) -> bool:
        with self._lock:
            if not self._guard(self._clock()):
                self.dropped_while_paused += 1
                return False
            fh = self._open(venue, int(row["t_recv_ms"]))
            fh.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
            fh.flush()
            self.rows_written[venue] += 1
            return True

    def _write_all(self, body: dict[str, Any], force: bool = False) -> None:
        t = int(self._clock() * 1000)
        for v in self.venues:
            row = {"v": SCHEMA_V, "venue": "all", "slot": 0, "t_recv_ms": t, "signature": None, "event_index": None, **body}
            try:
                fh = self._open(v, t)
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                fh.flush()
            except OSError:
                if not force:
                    raise

    def write_all(self, body: dict[str, Any]) -> None:
        """start / stop / gap / hb rows go to every venue dir; they bypass the pause (they are tiny and say why rows are missing)."""
        with self._lock:
            self._write_all(body, force=True)

    def close(self) -> None:
        with self._lock:
            for _, fh in self._fh.values():
                fh.close()
            self._fh.clear()

    def open_names(self) -> set[tuple[str, str]]:
        return {(v, n) for v, (n, _) in self._fh.items()}


def compact_closed(root: Path, venues: Iterable[str], now_ms: int, open_names: set[tuple[str, str]], compact) -> list[str]:
    """Compact every .jsonl whose hour ended more than CLOSE_GRACE_S ago and is not open. Returns the files compacted. Errors keep the source."""
    done: list[str] = []
    for v in venues:
        for p in sorted((root / v).glob("tape-*.jsonl")):
            if (v, p.name) in open_names:
                continue
            if hour_start_ms(p.name) + HOUR_MS + CLOSE_GRACE_S * 1000 > now_ms:
                continue
            try:
                compact(p)
                done.append(str(p))
            except Exception as exc:  # noqa: BLE001 - the source stays, retried on the next scan
                log.warning("compact_failed file=%s err=%s", p.name, type(exc).__name__)
    for v in venues:
        for p in (root / v).glob("*.zst.tmp"):
            if hour_start_ms(p.name.split(".jsonl")[0] + ".jsonl") + HOUR_MS < now_ms - 600_000:
                p.unlink(missing_ok=True)
    return done


# --------------------------------------------------------------------------------------------------------------------- run
class Monitor:
    """Gap bookkeeping, fed by the run loop: socket reconnects and per-venue silence become explicit gap / gap_end rows."""

    def __init__(self, writer: TapeWriter, venues: Iterable[str]) -> None:
        self.w = writer
        self.venues = list(venues)
        self.last_notice_ms: dict[str, int] = {v: 0 for v in self.venues}
        self.in_silence: dict[str, int | None] = {v: None for v in self.venues}
        self.seen_reconnects: dict[int, int] = {}
        self.gaps = 0
        self.started_ms = int(time.time() * 1000)
        for v in self.venues:
            self.last_notice_ms[v] = self.started_ms

    def saw(self, venue: str, t_ms: int) -> None:
        self.last_notice_ms[venue] = t_ms
        s = self.in_silence[venue]
        if s is not None:
            self.in_silence[venue] = None
            self.w.write_all({"type": "gap_end", "reason": "silence", "target": venue, "start_ms": s, "end_ms": t_ms})

    def tick(self, now_ms: int, socket_reconnects: dict[int, int]) -> None:
        for i, n in socket_reconnects.items():
            prev = self.seen_reconnects.get(i, 0)
            if n > prev:
                self.gaps += 1
                self.w.write_all({"type": "gap", "reason": "socket_reconnect", "socket": i, "reconnects_total": n, "new": n - prev})
            self.seen_reconnects[i] = n
        for v in self.venues:
            lim = VENUES[v]["silence_s"] * 1000
            if self.in_silence[v] is None and now_ms - self.last_notice_ms[v] > lim:
                self.in_silence[v] = self.last_notice_ms[v]
                self.gaps += 1
                self.w.write_all({"type": "gap", "reason": "silence", "target": venue_name(v), "start_ms": self.last_notice_ms[v],
                                  "silence_s": VENUES[v]["silence_s"]})


def venue_name(v: str) -> str:
    return v


def write_status(path: Path, status: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, indent=1, sort_keys=True, allow_nan=False) + "\n")
    os.replace(tmp, path)


async def run(args: argparse.Namespace) -> int:
    from observe.trade_source import MultiSocketLogsSource  # noqa: PLC0415 - needs websockets; tests do not
    from tools.arena_zst import compact_file, zstd_path  # noqa: PLC0415

    venues = [v.strip() for v in args.venues.split(",") if v.strip()]
    for v in venues:
        if v not in VENUES:
            print(f"unknown venue {v}", file=sys.stderr)
            return 2
    for u in args.ws_url or DEFAULT_WS:
        if "helius" in u.lower() or "?" in u or "@" in u:
            print("refusing a ws url that could hold a key", file=sys.stderr)
            return 2
    root = Path(args.out_dir)
    root.mkdir(parents=True, exist_ok=True)
    lock = open(root / ".venue-tape.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another venue_tape holds the lock", file=sys.stderr)
        return 3
    if not args.no_compress and zstd_path() is None:
        print("zstd CLI missing (use --no-compress)", file=sys.stderr)
        return 4
    w = TapeWriter(root, venues, min_free=args.min_free)
    mon = Monitor(w, venues)
    src = MultiSocketLogsSource(ws_urls=args.ws_url or DEFAULT_WS, sockets=args.sockets, programs=[VENUES[v]["program"] for v in venues])
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sg in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sg, stop.set)
    t0 = time.time()
    counts = {v: {"rows": 0, "trade": 0, "create": 0, "ix": 0, "event_undecoded": 0, "failed": 0} for v in venues}
    w.write_all({"type": "start", "venues": venues, "programs": {v: VENUES[v]["program"] for v in venues}, "sockets": args.sockets,
                 "ws": [u.split("?")[0] for u in (args.ws_url or DEFAULT_WS)], "pid": os.getpid()})

    def status(final: bool = False) -> None:
        now = time.time()
        write_status(root / "venue-tape-status.json", {
            "t_ms": int(now * 1000), "uptime_s": int(now - t0), "final": final, "venues": venues, "free_fraction": round(w.free, 4),
            "paused": w.paused, "dropped_while_paused": w.dropped_while_paused, "rows_written": w.rows_written, "counts": counts,
            "gaps": mon.gaps, "last_slot": src.stats.last_slot, "notices": src.stats.notes, "dedup_dropped": src.stats.dedup_dropped,
            "sockets": src.stats.socket_rows(), "torn_lines_ended": w.torn_lines_ended,
        })

    async def ticker() -> None:
        n = 0
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            now_ms = int(time.time() * 1000)
            mon.tick(now_ms, {s.index: s.reconnects for s in src.stats.sockets})
            n += 1
            if n % 12 == 0:
                status()
                w.write_all({"type": "hb", "rows": dict(w.rows_written), "last_slot": src.stats.last_slot})
            if n % 12 == 6 and not args.no_compress:
                await asyncio.to_thread(compact_closed, root, venues, now_ms, w.open_names(), compact_file)

    tk = asyncio.create_task(ticker())
    try:
        async for note in src.notices(stop):
            for v in venues:
                for row in rows_for_venue(v, note):
                    mon.saw(v, note.t_recv_ms)
                    if w.write(v, row):
                        c = counts[v]
                        c["rows"] += 1
                        c[row["type"]] = c.get(row["type"], 0) + 1
                        if row.get("failed"):
                            c["failed"] += 1
    finally:
        stop.set()
        await tk
        w.write_all({"type": "stop", "rows": dict(w.rows_written)})
        status(final=True)
        w.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Venue tape (public RPC logsSubscribe only; no keys, no transactions)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--venues", default="launchlab,meteora_dbc")
    ap.add_argument("--ws-url", action="append", default=None, help="public wss endpoint, repeatable; Helius or any url with a query is refused")
    ap.add_argument("--sockets", type=int, default=2)
    ap.add_argument("--min-free", type=float, default=DISK_MIN_FREE)
    ap.add_argument("--no-compress", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
