#!/usr/bin/env python3
"""Count-only probe of free public Solana websocket endpoints: does `logsSubscribe` on PumpSwap deliver, and how much, in N seconds. Paper only: no key, no
transaction, no RPC write, $0. One socket per endpoint, all endpoints at once, so the counts are comparable.

    python tools/arena_ws_probe.py --seconds 60 wss://api.mainnet-beta.solana.com wss://solana-rpc.publicnode.com        (needs websockets + certifi)

Prints one JSON object: per endpoint (scheme://host/path only, never a query) the subscription ack, notifications, distinct signatures, errors, the close code, the slot range
and how many of its signatures every OTHER endpoint also delivered (overlap, so a shallow endpoint shows). Counts only: no log text, no account, no amount.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
import time
from urllib.parse import urlsplit

PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"


def label(url: str) -> str:
    sp = urlsplit(url)
    return f"{sp.scheme}://{sp.hostname}{sp.path}" + ("?REDACTED" if sp.query else "")


async def probe_one(url: str, seconds: float, commitment: str) -> dict:
    import certifi
    import websockets

    out = {"endpoint": label(url), "connected": False, "ack": False, "notifications": 0, "errors": 0, "closed": None, "first_slot": None, "last_slot": None, "_sigs": set()}
    t0 = time.monotonic()
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        async with websockets.connect(url, ssl=ctx, open_timeout=15, ping_interval=20, max_size=8_000_000) as ws:
            out["connected"] = True
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe", "params": [{"mentions": [PUMPSWAP_PROGRAM]}, {"commitment": commitment}]}))
            while time.monotonic() - t0 < seconds:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, seconds - (time.monotonic() - t0)))
                except asyncio.TimeoutError:
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    out["errors"] += 1
                    continue
                if "error" in msg:
                    out["errors"] += 1
                elif msg.get("id") == 1 and "result" in msg:
                    out["ack"] = True
                elif msg.get("method") == "logsNotification":
                    v = msg["params"]["result"]
                    out["notifications"] += 1
                    slot = v.get("context", {}).get("slot")
                    if isinstance(slot, int):
                        out["first_slot"] = slot if out["first_slot"] is None else min(out["first_slot"], slot)
                        out["last_slot"] = slot if out["last_slot"] is None else max(out["last_slot"], slot)
                    sig = (v.get("value") or {}).get("signature")
                    if sig:
                        out["_sigs"].add(sig)
    except websockets.ConnectionClosed as e:
        out["closed"] = getattr(e, "code", None) or getattr(getattr(e, "rcvd", None), "code", None) or "closed"
    except Exception as e:  # noqa: BLE001 - the class name only, never the message (a URL can carry a key)
        out["closed"] = type(e).__name__
    return out


async def run(urls: list[str], seconds: float, commitment: str) -> dict:
    res = await asyncio.gather(*[probe_one(u, seconds, commitment) for u in urls])
    for r in res:
        others = set().union(*[o["_sigs"] for o in res if o is not r]) if len(res) > 1 else set()
        r["distinct_signatures"] = len(r["_sigs"])
        r["also_on_other_endpoints"] = len(r["_sigs"] & others)
        del r["_sigs"]
    return {"seconds": seconds, "commitment": commitment, "program": "pumpswap", "results": res}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("urls", nargs="+", help="public websocket URLs (no key in them)")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--commitment", default="confirmed", choices=("processed", "confirmed", "finalized"))
    a = ap.parse_args()
    for u in a.urls:
        sp = urlsplit(u)
        if sp.scheme != "wss" or sp.query or sp.username or "helius" in (sp.hostname or "").lower():
            print(f"REFUSED: {label(u)} (wss only, no query/credentials, no Helius)", file=sys.stderr)
            return 2
    print(json.dumps(asyncio.run(run(a.urls, a.seconds, a.commitment)), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
