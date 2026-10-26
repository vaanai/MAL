"""P36 venue census: public logsSubscribe capture of pump.fun bonding and Raydium LaunchLab for a fixed duration (counts only).

Public RPC only (api.mainnet-beta), 0 Helius, no key. One socket per program. Writes decoded rows (pump: tools.arena_feed.rows_from_notice;
LaunchLab: tools.venue_tape.rows_for_venue) plus one `notice` row per transaction (slot, signature, failed) to a gzip JSONL.
Not captured on purpose: Meteora DBC (forward window [2026-10-25T00Z, 2026-11-01T00:10Z) is sealed) and PumpSwap (EXP-024 Look-2 seal on
first-360 s of October V-range pools); those venues are sampled outside the sealed windows by p36_hist.py.
Run: python -m docs.research.p36-venue-census.p36_capture is not importable (dash); run as a file from the repo root with PYTHONPATH=. .
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
import time

from observe.trade_source import LogsSubscribeSource
from tools.arena_feed import rows_from_notice
from tools.venue_tape import rows_for_venue

PROGS = {"pump": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "launchlab": "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"}


async def run_one(name: str, prog: str, ws: str, out, stop: asyncio.Event, counts: dict) -> None:
    src = LogsSubscribeSource(ws_url=ws, programs=[prog], commitment="confirmed")
    pool_mints: dict = {}
    async for note in src.notices(stop):
        counts[name + "_notices"] = counts.get(name + "_notices", 0) + 1
        if note.failed:
            counts[name + "_failed"] = counts.get(name + "_failed", 0) + 1
        out.write(json.dumps({"k": "notice", "prog": name, "slot": note.slot, "sig": note.signature, "failed": note.failed,
                              "t": note.t_recv_ms}) + "\n")
        try:
            rows = rows_from_notice(note, pool_mints) if name == "pump" else rows_for_venue("launchlab", note)
        except Exception as e:  # noqa: BLE001
            counts[name + "_decode_err"] = counts.get(name + "_decode_err", 0) + 1
            rows = []
        for r in rows:
            r = {kk: vv for kk, vv in r.items() if kk not in ("params_undecoded_b64", "data_b64")}
            out.write(json.dumps({"k": "row", "prog": name, **r}) + "\n")


async def main_async(args) -> dict:
    stop = asyncio.Event()
    counts: dict = {"start_ms": int(time.time() * 1000)}
    with gzip.open(args.out, "wt") as out:
        tasks = [asyncio.create_task(run_one(n, p, args.ws, out, stop, counts)) for n, p in PROGS.items() if n in args.progs]
        t_end = time.time() + args.seconds
        while time.time() < t_end:
            await asyncio.sleep(10)
            if args.progress:
                with open(args.progress, "w") as f:
                    json.dump({"pct": int(100 * (1 - (t_end - time.time()) / args.seconds)), "note": json.dumps(counts)}, f)
        stop.set()
        await asyncio.sleep(2)
        for t in tasks:
            t.cancel()
    counts["end_ms"] = int(time.time() * 1000)
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=int, default=2700)
    ap.add_argument("--ws", default="wss://api.mainnet-beta.solana.com")
    ap.add_argument("--progs", default="pump,launchlab")
    ap.add_argument("--progress", default=None)
    a = ap.parse_args()
    a.progs = a.progs.split(",")
    c = asyncio.run(main_async(a))
    print(json.dumps(c))
    return 0


if __name__ == "__main__":
    sys.exit(main())
