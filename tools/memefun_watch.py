"""meme.fun watch recorder (lane P45, P36 proposal R2). Standalone: its own public logsSubscribe on the meme.fun program, hourly JSONL. Paper only. No keys.

Program: MEME4gfGe6itEpxgM1xaBVmiFF5XuCTc4oVXUE5w56F (verified executable, upgradeable loader, 2026-10-26). It publishes an Anchor IDL
(account 5AmQoejxDye85tjLRwykFvUk3VimYaFCe52YVQfbzQMj, name `memefun`, "fair-launch memecoin launchpad"); a copy is in docs/research/p45-memefun-watch/idl.json.

Instructions (IDL): buy, sell, swap, create_token, claim_creator_fees, claim_protocol_fees, initialize, set_*, propose/accept_admin.
Events (IDL, emitted with `Program data:` in the logs, so they are readable from logs): CreateEvent, TradeEvent, ClaimEvent. Layouts below are the IDL
layouts (little-endian, borsh). The IDL TradeEvent has the same 8-byte discriminator as Raydium LaunchLab's TradeEvent but a different body (137 bytes
total); tools.venue_tape reports it as event_undecoded trade_len_137, so this module must not be mixed up with it.

Rows: v, venue="memefun", type, slot, t_recv_ms, signature, event_index, then fields.
  create  : mint, bonding_curve, creator, name, symbol, uri, virtual_sol_reserves, virtual_token_reserves, real_token_reserves, ts, ev_slot, ix
  trade   : mint, side, sol_amount (lamports), token_amount, trader, virtual_sol_reserves, virtual_token_reserves, real_sol_reserves, real_token_reserves, ts, ev_slot, ix
  claim   : mint, user, amount, ts, ix
  ix      : no decodable event (failed tx, swap/claim/admin instruction, truncated logs): instruction names, failed flag
  event_undecoded : unknown discriminator or unexpected length (raw base64 kept)
Accounts: logsSubscribe does not carry the account list. mint, bonding_curve, creator and trader come from the event bodies; nothing else is recorded.
Special rows: start, stop, gap, gap_end, hb, disk_pause, disk_resume (written by tools.venue_tape.TapeWriter, imported, not modified).

Does not modify tools/venue_tape.py or tools/arena_feed.py; shares no socket with them. Public wss only; a url that could hold a key is refused.
Memory is bounded: only counters are kept (the merged source dedups with a bounded set). One instance only: flock on <out>/.memefun-watch.lock.
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
import struct
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from tools.venue_tape import TapeWriter, b58, program_events, write_status

log = logging.getLogger("memefun_watch")

SCHEMA_V = 1
PROGRAM = "MEME4gfGe6itEpxgM1xaBVmiFF5XuCTc4oVXUE5w56F"
VENUE = "memefun"
DEFAULT_OUT = "/data/mal/memefun-watch"
DEFAULT_WS = ["wss://api.mainnet-beta.solana.com"]  # venue_tape's second endpoint (solanavibestation) answered HTTP 429 on every attempt 2026-10-26; two sockets on one url dedup
SILENCE_S = 1800  # ~390 program txs/hour were seen on day 0; half an hour of nothing is a gap
MAX_UNDECODED_B64 = 1400
MAX_STR = 400

# Anchor event discriminators from the IDL (sha256("event:<Name>")[:8]).
CLAIM_DISC = bytes([93, 15, 70, 170, 48, 140, 212, 219])
CREATE_DISC = bytes([27, 114, 169, 77, 222, 235, 99, 118])
TRADE_DISC = bytes([189, 219, 127, 211, 78, 230, 97, 238])
TRADE_LEN = 8 + 32 + 8 + 8 + 1 + 32 + 4 * 8 + 2 * 8  # 137
CLAIM_LEN = 8 + 32 + 32 + 8 + 8  # 88
CREATE_MIN = 8 + 32 * 3 + 3 * 4 + 5 * 8


def _u64(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 8], "little")


def _i64(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 8], "little", signed=True)


def _und(reason: str, raw: bytes, b64: str) -> dict[str, Any]:
    return {"type": "event_undecoded", "reason": reason, "disc": raw[:8].hex(), "data_b64": b64[:MAX_UNDECODED_B64]}


def decode_event(b64: str) -> dict[str, Any]:
    """One `Program data` payload of the meme.fun program -> a row body. Never raises; anything unexpected is an event_undecoded body."""
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:  # noqa: BLE001
        return {"type": "event_undecoded", "reason": "bad_base64", "data_b64": b64[:MAX_UNDECODED_B64]}
    disc = raw[:8]
    if disc == TRADE_DISC:
        if len(raw) != TRADE_LEN:
            return _und(f"trade_len_{len(raw)}", raw, b64)
        if raw[56] not in (0, 1):
            return _und("trade_bool", raw, b64)
        return {
            "type": "trade", "mint": b58(raw[8:40]), "sol_amount": _u64(raw, 40), "token_amount": _u64(raw, 48),
            "side": "buy" if raw[56] == 1 else "sell", "trader": b58(raw[57:89]),
            "virtual_sol_reserves": _u64(raw, 89), "virtual_token_reserves": _u64(raw, 97),
            "real_sol_reserves": _u64(raw, 105), "real_token_reserves": _u64(raw, 113),
            "ts": _i64(raw, 121), "ev_slot": _u64(raw, 129),
        }
    if disc == CREATE_DISC:
        if len(raw) < CREATE_MIN:
            return _und(f"create_len_{len(raw)}", raw, b64)
        o = 8
        keys = []
        for _ in range(3):
            keys.append(b58(raw[o:o + 32]))
            o += 32
        strs = []
        for _ in range(3):
            if o + 4 > len(raw):
                return _und("create_str_overrun", raw, b64)
            n = struct.unpack("<I", raw[o:o + 4])[0]
            if n > 4096 or o + 4 + n > len(raw):
                return _und("create_str_overrun", raw, b64)
            strs.append(raw[o + 4:o + 4 + n].decode("utf-8", "replace")[:MAX_STR])
            o += 4 + n
        if len(raw) - o != 5 * 8:
            return _und(f"create_tail_{len(raw) - o}", raw, b64)
        return {
            "type": "create", "mint": keys[0], "bonding_curve": keys[1], "creator": keys[2], "name": strs[0], "symbol": strs[1], "uri": strs[2],
            "virtual_sol_reserves": _u64(raw, o), "virtual_token_reserves": _u64(raw, o + 8), "real_token_reserves": _u64(raw, o + 16),
            "ts": _i64(raw, o + 24), "ev_slot": _u64(raw, o + 32),
        }
    if disc == CLAIM_DISC:
        if len(raw) != CLAIM_LEN:
            return _und(f"claim_len_{len(raw)}", raw, b64)
        return {"type": "claim", "mint": b58(raw[8:40]), "user": b58(raw[40:72]), "amount": _u64(raw, 72), "ts": _i64(raw, 80)}
    return _und("unknown_disc", raw, b64)


def rows_for_notice(note: Any) -> list[dict[str, Any]]:
    """RawNotice (slot, signature, failed, logs, t_recv_ms) -> rows ([] when the program was only named in the account list). Pure."""
    pe = program_events(note.logs, PROGRAM)
    if pe["depth"] == 0:
        return []
    base = {"v": SCHEMA_V, "venue": VENUE, "slot": note.slot, "t_recv_ms": note.t_recv_ms, "signature": note.signature}
    flags: dict[str, Any] = {}
    if note.failed:
        flags["failed"] = True
    if pe["truncated"]:
        flags["logs_truncated"] = True
    if pe["depth"] > 1:
        flags["via_cpi"] = True
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for b in pe["data"]:
        body = decode_event(b)
        t = body["type"]
        body["event_index"] = counts.get(t, 0)
        counts[t] = body["event_index"] + 1
        rows.append({**base, **flags, **body, "ix": pe["ix"]})
    if not rows:
        rows.append({**base, **flags, "type": "ix", "event_index": 0, "ix": pe["ix"]})
    return rows


class Monitor:
    """Gap bookkeeping: long silence becomes explicit gap / gap_end rows (same row shapes as venue_tape); reconnects are counted only."""

    def __init__(self, writer: TapeWriter, silence_s: int = SILENCE_S, now_ms: int | None = None) -> None:
        self.w = writer
        self.silence_s = silence_s
        self.last_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        self.in_silence: int | None = None
        self.reconnects = 0
        self.gaps = 0

    def saw(self, t_ms: int) -> None:
        self.last_ms = t_ms
        if self.in_silence is not None:
            s, self.in_silence = self.in_silence, None
            self.w.write_all({"type": "gap_end", "reason": "silence", "target": VENUE, "start_ms": s, "end_ms": t_ms})

    def tick(self, now_ms: int, socket_reconnects: dict[int, int]) -> None:
        """Reconnects are NOT gap rows: the shared ws client drops a socket after 30 s without a message (ws_idle) and this venue is quiet, so each
        socket reconnects about twice a minute and two staggered sockets cover each other. They are counted (self.reconnects, hb rows, status).
        A gap row is written only for silence longer than silence_s."""
        self.reconnects = sum(socket_reconnects.values())
        if self.in_silence is None and now_ms - self.last_ms > self.silence_s * 1000:
            self.in_silence = self.last_ms
            self.gaps += 1
            self.w.write_all({"type": "gap", "reason": "silence", "target": VENUE, "start_ms": self.last_ms, "silence_s": self.silence_s})


def acquire_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock = open(root / ".memefun-watch.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    return lock


def bad_ws(urls: Iterable[str]) -> bool:
    return any("helius" in u.lower() or "?" in u or "@" in u for u in urls)


async def run(args: argparse.Namespace) -> int:
    from observe.trade_source import MultiSocketLogsSource  # noqa: PLC0415 - needs websockets; tests do not

    urls = args.ws_url or DEFAULT_WS
    if bad_ws(urls):
        print("refusing a ws url that could hold a key", file=sys.stderr)
        return 2
    root = Path(args.out_dir)
    lock = acquire_lock(root)
    if lock is None:
        print("another memefun_watch holds the lock", file=sys.stderr)
        return 3
    w = TapeWriter(root, [VENUE], min_free=args.min_free)
    mon = Monitor(w)
    src = MultiSocketLogsSource(ws_urls=urls, sockets=args.sockets, programs=[PROGRAM])
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sg in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sg, stop.set)
    t0 = time.time()
    prior = {}
    sp = root / "memefun-watch-status.json"
    if sp.exists():
        try:
            prior = json.loads(sp.read_text()).get("counts_total", {})
        except (OSError, ValueError):
            prior = {}
    counts: dict[str, int] = {}
    w.write_all({"type": "start", "program": PROGRAM, "sockets": args.sockets, "ws": [u.split("?")[0] for u in urls], "pid": os.getpid()})

    def status(final: bool = False) -> None:
        now = time.time()
        total = {k: counts.get(k, 0) + prior.get(k, 0) for k in set(counts) | set(prior)}
        write_status(sp, {
            "t_ms": int(now * 1000), "uptime_s": int(now - t0), "final": final, "program": PROGRAM, "free_fraction": round(w.free, 4),
            "paused": w.paused, "dropped_while_paused": w.dropped_while_paused, "rows_written": w.rows_written, "counts_this_run": counts,
            "counts_total": total, "gaps": mon.gaps, "reconnects": mon.reconnects, "last_slot": src.stats.last_slot, "notices": src.stats.notes,
            "dedup_dropped": src.stats.dedup_dropped, "sockets": src.stats.socket_rows(), "torn_lines_ended": w.torn_lines_ended,
        })

    async def ticker() -> None:
        n = 0
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            mon.tick(int(time.time() * 1000), {s.index: s.reconnects for s in src.stats.sockets})
            n += 1
            if n % 12 == 0:
                status()
                w.write_all({"type": "hb", "rows": dict(w.rows_written), "last_slot": src.stats.last_slot, "reconnects": mon.reconnects})

    tk = asyncio.create_task(ticker())
    try:
        async for note in src.notices(stop):
            for row in rows_for_notice(note):
                mon.saw(note.t_recv_ms)
                if w.write(VENUE, row):
                    k = row["type"] + ("_" + row["side"] if row["type"] == "trade" else "")
                    counts[k] = counts.get(k, 0) + 1
                    counts["rows"] = counts.get("rows", 0) + 1
                    if row.get("failed"):
                        counts["failed"] = counts.get("failed", 0) + 1
    finally:
        stop.set()
        await tk
        w.write_all({"type": "stop", "rows": dict(w.rows_written)})
        status(final=True)
        w.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="meme.fun watch recorder (public RPC logsSubscribe only; no keys, no transactions)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--ws-url", action="append", default=None, help="public wss endpoint, repeatable; Helius or any url with a query is refused")
    ap.add_argument("--sockets", type=int, default=2)
    ap.add_argument("--min-free", type=float, default=0.20)
    ap.add_argument("--log-level", default="INFO")
    a = ap.parse_args(argv)
    logging.basicConfig(level=a.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
