"""P15 DBC latency sampler: public logsSubscribe on the Meteora DBC program, processed AND confirmed on one socket.
Paper only. No keys, no transactions, public RPC only. Writes one JSONL row per notice: t_recv_ms, slot, signature, commitment, err.
Usage: python tools/dbc_latency_sampler.py --seconds 600 --out DIR [--ws wss://api.mainnet-beta.solana.com]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

DBC = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"


async def run(ws_url: str, seconds: float, out: Path) -> dict:
    import websockets  # noqa: PLC0415

    n = {"processed": 0, "confirmed": 0}
    end = time.time() + seconds
    with out.open("w") as fh:
        async with websockets.connect(ws_url, max_size=2**24, ping_interval=20) as ws:
            for i, c in enumerate(("processed", "confirmed")):
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": i + 1, "method": "logsSubscribe",
                                          "params": [{"mentions": [DBC]}, {"commitment": c}]}))
            subs: dict[int, str] = {}
            while time.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.time()))
                except asyncio.TimeoutError:
                    break
                t = int(time.time() * 1000)
                m = json.loads(raw)
                if "id" in m and "result" in m:
                    subs[m["result"]] = ("processed", "confirmed")[m["id"] - 1]
                    continue
                if m.get("method") != "logsNotification":
                    continue
                p = m["params"]
                r = p["result"]
                c = subs.get(p["subscription"], "?")
                logs = r["value"].get("logs") or []
                swap = any("Instruction: Swap" in l for l in logs)
                fh.write(json.dumps({"t_recv_ms": t, "slot": r["context"]["slot"], "sig": r["value"]["signature"],
                                     "c": c, "err": r["value"].get("err") is not None, "swap": swap}) + "\n")
                n[c] = n.get(c, 0) + 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ws", default="wss://api.mainnet-beta.solana.com")
    a = ap.parse_args()
    d = Path(a.out)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"dbc-sample-{int(time.time())}.jsonl"
    n = asyncio.run(run(a.ws, a.seconds, f))
    print(json.dumps({"file": str(f), "counts": n}))


if __name__ == "__main__":
    main()
