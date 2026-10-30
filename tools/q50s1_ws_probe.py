"""Measure the Helius credit cost of ONE s1-style websocket subscription (DEC-031 Amendment 22 item 10: "The credits used are recorded per day"; the manager's ask of 2026-10-30:
"measure Helius credit use per hour for one ws subscription ... and project it to 12-01"). PAPER ONLY. Counts and byte totals only: no notification content is kept or printed.

    /var/lib/mal/fast-listener/.venv/bin/python -I tools/q50s1_ws_probe.py --key-file /var/lib/mal/fast-listener/helius.env --seconds 3600

It opens the subscription S*'s shadow opens on its Helius socket (observe/trade_source.logs_subscribe_request at S*: logsSubscribe, mentions the PumpSwap program, commitment
confirmed) on wss://mainnet.helius-rpc.com/ with the key added INSIDE this process (read from --key-file; never in argv, never printed), with websocket compression OFF so the
counted bytes are the uncompressed payload Helius bills (docs.helius.xyz/docs/billing/credits: standard WebSocket methods 2 credits per 0.1 MB of uncompressed data; the plans
page's overage text says 3 credits per 0.1 MB, so both are printed). One JSON line: duration, messages, payload bytes, reconnects, MB/h, credits/h at 2 and at 3, and the projection
over s1's planned window (2026-11-05T20:00Z to 2026-12-01T01:00Z = 605 h) and per day. Mainnet-beta is not measured (public, 0 credits).
Run it as a MiScusi job on mal-fast-0 (websockets + certifi are in the fast-listener venv), about 1 h, 256 MB; it adds one subscription on fast-0's IP for that hour.
Exit: 0 measured, 2 usage, 11 no usable key, 12 never connected.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time

PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"  # observe/trade_decode.py:21 (byte-identical at S*)
BASE = "wss://mainnet.helius-rpc.com/"
CREDITS_PER_TENTH_MB = (2, 3)
PLAN_H = 605
KEY_RE = re.compile(r"^[A-Za-z0-9-]{8,128}$")


def read_key(path: str) -> str | None:
    """HELIUS_API_KEY from a KEY=VALUE file (optional `export `, optional quotes); None on any doubt. Never logs."""
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


def subscribe_request(commitment: str = "confirmed") -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe", "params": [{"mentions": [PUMPSWAP_PROGRAM]}, {"commitment": commitment}]}


def summary(seconds: float, messages: int, payload_bytes: int, reconnects: int, connected: bool) -> dict:
    hours = max(seconds, 1e-9) / 3600.0
    mb_h = payload_bytes / 1e6 / hours
    out = {"tool": "q50s1_ws_probe", "endpoint": BASE, "subscription": "logsSubscribe mentions PumpSwap, commitment confirmed", "compression": "off",
           "seconds": round(seconds, 1), "messages": messages, "payload_bytes": payload_bytes, "reconnects": reconnects, "connected": connected,
           "mb_per_hour": round(mb_h, 4), "plan_hours": PLAN_H}
    for c in CREDITS_PER_TENTH_MB:
        per_h = mb_h * 10 * c
        out[f"credits_per_hour_at_{c}_per_0.1MB"] = round(per_h, 1)
        out[f"credits_per_day_at_{c}_per_0.1MB"] = round(per_h * 24, 1)
        out[f"credits_to_2026-12-01_at_{c}_per_0.1MB"] = round(per_h * PLAN_H, 1)
    return out


async def measure(url: str, seconds: float) -> tuple[float, int, int, int, bool]:
    import ssl

    import certifi
    import websockets

    ctx = ssl.create_default_context(cafile=certifi.where())
    t0 = time.monotonic()
    msgs = nbytes = reconnects = 0
    connected = False
    while time.monotonic() - t0 < seconds:
        try:
            async with websockets.connect(url, ssl=ctx, compression=None, ping_interval=20, ping_timeout=20, open_timeout=20, max_size=8_000_000) as ws:
                connected = True
                await ws.send(json.dumps(subscribe_request()))
                while True:
                    left = seconds - (time.monotonic() - t0)
                    if left <= 0:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(left, 30))
                    except asyncio.TimeoutError:
                        continue
                    msgs += 1
                    nbytes += len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
        except Exception as exc:  # noqa: BLE001 - count and reconnect; the message may name the URL, so it is never printed
            reconnects += 1
            print(f"q50s1_ws_probe: reconnect {reconnects} ({type(exc).__name__})", file=sys.stderr)
            await asyncio.sleep(min(30, 2 ** min(reconnects, 5)))
    return time.monotonic() - t0, msgs, nbytes, reconnects, connected


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--seconds", type=float, default=3600.0)
    a = ap.parse_args(argv)
    if not 60 <= a.seconds <= 4 * 3600:
        print("q50s1_ws_probe: --seconds must be in [60, 14400]", file=sys.stderr)
        return 2
    key = read_key(a.key_file)
    if key is None:
        print("q50s1_ws_probe: no usable HELIUS_API_KEY in the key file", file=sys.stderr)
        return 11
    secs, msgs, nbytes, rec, ok = asyncio.run(measure(f"{BASE}?api-key={key}", a.seconds))
    print(json.dumps(summary(secs, msgs, nbytes, rec, ok), sort_keys=True))
    return 0 if ok else 12


if __name__ == "__main__":
    sys.exit(main())
