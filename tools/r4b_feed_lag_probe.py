"""r4b_feed_lag_probe: Helius websocket lag at r4-b's decision instant, measured in mark windows. PAPER ONLY: counts, slot lags, times and bytes; no content kept.

Why (QP-RULING-1030 §3; DEC-032 Amendment 8 A8 / Erratum 1): r4-b's decision at mark T reads every print with slot < sd(T), the first slot whose block_time is at or
after T, and the canary's landing is measured on chain as d_land = landed slot - sd(T) against the twin's k (np.round(1.3 / slot_s), np.round(1.9 / slot_s)). The public
feed delivers prints about 7 slots late. This probe measures how late a Helius websocket delivers them, opening the socket only in a window [M - 30 s, M + 10 s] around
each pseudo-mark M (every --interval seconds; the hour marks are among them), which is the cheap design proposed for r4-b (lane report §10.3, option C).

Per window and commitment, one socket: slotSubscribe (the processed tip, to place wall time on the slot axis) plus logsSubscribe on the PumpSwap program and on the pump.fun
bonding-curve program. Each logs notification is decoded with observe.trade_decode only to read its trades' event_ts (the block time); slot and local receive time are kept;
signatures, mints, prices, sizes and reserves are never stored or printed. Then, with sd(M) = the smallest slot among decoded trades with event_ts >= M:
  sd_lag_slots     tip slot (latest slotNotification received) at the first-seen time of any sd(M) print, minus sd(M)
  ready_lag_slots  tip slot when the last print with slot < sd(M) and event_ts in [M - 5, M) had arrived (or sd's first-seen, whichever is later), minus sd(M)
  sd_lag_ms        first-seen time of sd(M)'s prints minus the receive time of the slotNotification for sd(M) (the tip reaching it)
  ready_lag_ms     the same for the ready instant
  event_lag_slots  per print: tip slot at its first-seen time minus its slot (p50 / p90 / max per window)
Credits: uncompressed payload bytes per socket (compression off), priced at 2 and 3 credits per 0.1 MB as tools/q50s1_ws_probe.py (probe #1891) does, with the per-window
cost and the projection to 24 r4-b mark windows a day.

Run as a MiScusi job on mal-fast-0 (the Helius key file is there; never printed, never in argv; added to the URL inside this process):
    /var/lib/mal/fast-listener/.venv/bin/python -I tools/r4b_feed_lag_probe.py --key-file /var/lib/mal/fast-listener/helius.env --seconds 7200 --interval 600
No public-RPC socket is opened (fast-0's public IP load is a known Q50 feed issue). Exit: 0 measured, 2 usage, 11 no usable key, 12 no window connected.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from observe import trade_decode as td  # noqa: E402

BASE = "wss://mainnet.helius-rpc.com/"
PROGRAMS = {"pumpswap": td.PUMPSWAP_PROGRAM, "pumpfun": td.PUMP_BONDING_PROGRAM}
PRE_S, POST_S = 30, 10
PRE_SD_WINDOW_S = 5
CREDITS_PER_TENTH_MB = (2, 3)
MARKS_PER_DAY = 24
KEY_RE = re.compile(r"^[A-Za-z0-9-]{8,128}$")


def read_key(path: str) -> str | None:
    """HELIUS_API_KEY from a KEY=VALUE file (as tools/q50s1_ws_probe.read_key); None on any doubt. Never logs."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    for ln in lines:
        s = ln.strip()
        if s.startswith("export "):
            s = s[7:].lstrip()
        if s.startswith("HELIUS_API_KEY="):
            v = s[len("HELIUS_API_KEY="):].strip().strip("'\"")
            return v if KEY_RE.match(v) else None
    return None


def requests(commitment: str) -> list[dict]:
    out = [{"jsonrpc": "2.0", "id": 1, "method": "slotSubscribe"}]
    for i, prog in enumerate(PROGRAMS.values()):
        out.append({"jsonrpc": "2.0", "id": 2 + i, "method": "logsSubscribe", "params": [{"mentions": [prog]}, {"commitment": commitment}]})
    return out


@dataclass
class Window:
    mark: int
    commitment: str
    slots: list = field(default_factory=list)  # (recv_ms, tip slot) in arrival order
    prints: list = field(default_factory=list)  # (recv_ms, slot, event_ts) per decoded trade
    notes: int = 0
    undecoded: int = 0
    bytes: int = 0
    connect_ms: int | None = None
    first_note_ms: int | None = None
    errors: int = 0

    def handle(self, raw: str | bytes, recv_ms: int) -> None:
        self.bytes += len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            self.errors += 1
            return
        method = msg.get("method")
        if method not in ("slotNotification", "logsNotification"):
            return
        if self.first_note_ms is None:
            self.first_note_ms = recv_ms
        res = (msg.get("params") or {}).get("result") or {}
        if method == "slotNotification":
            if isinstance(res.get("slot"), int):
                self.slots.append((recv_ms, int(res["slot"])))
            return
        self.notes += 1
        slot = (res.get("context") or {}).get("slot")
        val = res.get("value") or {}
        if not isinstance(slot, int) or val.get("err") is not None:
            return
        try:
            recs = td.records_from_logs(val.get("logs") or [], slot=slot, signature=str(val.get("signature") or ""), t_recv_ms=recv_ms,
                                        commitment=self.commitment, feed="helius_probe")
        except Exception:  # noqa: BLE001 - a decode failure is counted, never raised
            recs = []
        ts = [int(r["event_ts"]) for r in recs if isinstance(r.get("event_ts"), int)]
        if not ts:
            self.undecoded += 1
            return
        for e in ts:
            self.prints.append((recv_ms, slot, e))


def _tip_at(slots: list, t_ms: int) -> int | None:
    best = None
    for r, s in slots:
        if r <= t_ms and (best is None or s > best):
            best = s
    return best


def _first_tip_time(slots: list, slot: int) -> int | None:
    ts = [r for r, s in slots if s >= slot]
    return min(ts) if ts else None


def _pct(xs: list, q: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    k = min(len(ys) - 1, max(0, int(round(q * (len(ys) - 1)))))
    return float(ys[k])


def analyse(w: Window) -> dict:
    """Window statistics (no content): see the module docstring."""
    out = {"mark": w.mark, "hour_mark": w.mark % 3600 == 0, "commitment": w.commitment, "logs_notes": w.notes, "prints": len(w.prints), "undecoded_notes": w.undecoded,
           "slot_notes": len(w.slots), "bytes": w.bytes, "connect_ms": w.connect_ms, "first_note_after_connect_ms": None, "errors": w.errors, "sd_found": False}
    if w.connect_ms is not None and w.first_note_ms is not None:
        out["first_note_after_connect_ms"] = w.first_note_ms - w.connect_ms
    lags = [s_tip - slot for r, slot, _ in w.prints for s_tip in [_tip_at(w.slots, r)] if s_tip is not None]
    out.update({"event_lag_slots_p50": _pct(lags, 0.5), "event_lag_slots_p90": _pct(lags, 0.9), "event_lag_slots_max": max(lags) if lags else None})
    after = [slot for _, slot, e in w.prints if e >= w.mark]
    if not after:
        return out
    sd = min(after)
    sd_seen = min(r for r, slot, _ in w.prints if slot == sd)
    pre = [r for r, slot, e in w.prints if slot < sd and w.mark - PRE_SD_WINDOW_S <= e < w.mark]
    ready = max([sd_seen] + pre)
    tip_sd, tip_ready = _tip_at(w.slots, sd_seen), _tip_at(w.slots, ready)
    t_tip = _first_tip_time(w.slots, sd)
    out.update({"sd_found": True, "pre_sd_prints": len(pre),
                "sd_lag_slots": None if tip_sd is None else tip_sd - sd, "ready_lag_slots": None if tip_ready is None else tip_ready - sd,
                "sd_lag_ms": None if t_tip is None else sd_seen - t_tip, "ready_lag_ms": None if t_tip is None else ready - t_tip,
                "sd_seen_after_mark_ms": sd_seen - w.mark * 1000, "ready_after_mark_ms": ready - w.mark * 1000})
    return out


def summarise(rows: list[dict], seconds: float) -> dict:
    out: dict = {"tool": "r4b_feed_lag_probe", "endpoint": BASE, "window_s": PRE_S + POST_S, "seconds": round(seconds, 1), "by_commitment": {}}
    for c in sorted({r["commitment"] for r in rows}):
        rs = [r for r in rows if r["commitment"] == c]
        ok = [r for r in rs if r["sd_found"]]
        b = sum(r["bytes"] for r in rs)
        d = {"windows": len(rs), "windows_sd_found": len(ok), "hour_marks": sum(1 for r in rs if r["hour_mark"]), "bytes": b, "prints": sum(r["prints"] for r in rs)}
        for k in ("sd_lag_slots", "ready_lag_slots", "sd_lag_ms", "ready_lag_ms", "ready_after_mark_ms", "event_lag_slots_p50", "event_lag_slots_p90",
                  "first_note_after_connect_ms"):
            xs = [r[k] for r in (ok if k not in ("event_lag_slots_p50", "event_lag_slots_p90", "first_note_after_connect_ms") else rs) if r.get(k) is not None]
            d[k] = {"p50": _pct(xs, 0.5), "p90": _pct(xs, 0.9), "max": max(xs) if xs else None, "n": len(xs)}
        for cr in CREDITS_PER_TENTH_MB:
            per_window = (b / max(len(rs), 1)) / 1e5 * cr
            d[f"credits_at_{cr}_per_0.1MB"] = round(b / 1e5 * cr, 1)
            d[f"credits_per_window_at_{cr}"] = round(per_window, 1)
            d[f"credits_per_day_24_marks_at_{cr}"] = round(per_window * MARKS_PER_DAY, 1)
        out["by_commitment"][c] = d
    return out


async def run_window(url: str, mark: int, commitment: str) -> Window:
    import ssl

    import certifi
    import websockets

    w = Window(mark, commitment)
    end = mark + POST_S
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        w.connect_ms = int(time.time() * 1000)
        async with websockets.connect(url, ssl=ctx, compression=None, ping_interval=20, ping_timeout=20, open_timeout=15, max_size=8_000_000) as ws:
            for req in requests(commitment):
                await ws.send(json.dumps(req))
            while True:
                left = end - time.time()
                if left <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=left)
                except asyncio.TimeoutError:
                    break
                w.handle(raw, int(time.time() * 1000))
    except Exception as exc:  # noqa: BLE001 - the message may carry the URL: only the class is printed
        w.errors += 1
        print(f"r4b_feed_lag_probe: window {mark} {commitment}: {type(exc).__name__}", file=sys.stderr)
    return w


async def probe(url: str, seconds: float, interval: int, commitments: list[str], out_path: Path | None) -> list[dict]:
    t0 = time.time()
    first = int(-(-(t0 + PRE_S + 5) // interval) * interval)
    rows: list[dict] = []
    m = first
    while m + POST_S <= t0 + seconds:
        await asyncio.sleep(max(0.0, (m - PRE_S) - time.time()))
        ws = await asyncio.gather(*(run_window(url, m, c) for c in commitments))
        for w in ws:
            r = analyse(w)
            rows.append(r)
            if out_path is not None:
                with open(out_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(r, sort_keys=True) + "\n")
        prog = os.environ.get("MISCUSI_PROGRESS")
        if prog:
            Path(prog).write_text(json.dumps({"pct": round(100 * (m - t0) / seconds), "note": f"window {m} done, {len(rows)} rows"}))
        m += interval
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--seconds", type=float, default=7200.0)
    ap.add_argument("--interval", type=int, default=600, help="pseudo-mark spacing in seconds (a divisor of 3600, so hour marks are included)")
    ap.add_argument("--commitments", default="processed,confirmed")
    ap.add_argument("--out", default=None, help="per-window JSON lines (default $MISCUSI_OUTPUT_DIR/windows.jsonl)")
    a = ap.parse_args(argv)
    cs = [c for c in a.commitments.split(",") if c]
    if not 600 <= a.seconds <= 4 * 3600 or a.interval < 60 or 3600 % a.interval or not cs or any(c not in ("processed", "confirmed") for c in cs):
        print("r4b_feed_lag_probe: need 600 <= --seconds <= 14400, --interval a divisor of 3600 >= 60, commitments from processed,confirmed", file=sys.stderr)
        return 2
    key = read_key(a.key_file)
    if key is None:
        print("r4b_feed_lag_probe: no usable HELIUS_API_KEY in the key file", file=sys.stderr)
        return 11
    out = Path(a.out) if a.out else (Path(os.environ["MISCUSI_OUTPUT_DIR"]) / "windows.jsonl" if os.environ.get("MISCUSI_OUTPUT_DIR") else None)
    t0 = time.monotonic()
    rows = asyncio.run(probe(f"{BASE}?api-key={key}", a.seconds, a.interval, cs, out))
    s = summarise(rows, time.monotonic() - t0)
    print(json.dumps(s, sort_keys=True))
    res = os.environ.get("MISCUSI_RESULT")
    if res:
        m = {}
        for c, d in s["by_commitment"].items():
            m[f"{c}_ready_lag_slots_p50"] = d["ready_lag_slots"]["p50"]
            m[f"{c}_ready_lag_slots_p90"] = d["ready_lag_slots"]["p90"]
            m[f"{c}_credits_at_2"] = d["credits_at_2_per_0.1MB"]
        Path(res).write_text(json.dumps({"ok": True, "metrics": m, "summary": "r4b feed lag probe: Helius mark windows; slot lags of sd(T) and credits"}))
    return 0 if any(r["sd_found"] for r in rows) else 12


if __name__ == "__main__":
    sys.exit(main())
