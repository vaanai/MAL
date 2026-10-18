#!/usr/bin/env python3
"""hv4_review_bench: a synthetic full-day benchmark of the HV4 reviewer's skip pricing. NOT a test and not part of any read. PAPER only; no key, no network.

It writes a synthetic arena tape (many pools on constant-product reserves, one print every `--period-s` seconds, a random-walk price), about `--skips` skip
rows with their cards in the runner's card-store format, and then times hv4_review.load_skip_records with hv4_review_replay.ArenaReplayPricer: the cap to 60
cards BEFORE pricing, the card store read, the replay of the 60 through a scratch ArenaState / ArenaEngine / Runner. It prints one JSON line with wall seconds
and the process's peak RSS.

    python3 tools/hv4_review_bench.py --dir DIR [--pools 100] [--skips 2000] [--hours 30] [--period-s 30] [--seed 1]

Run it under a memory cap (systemd-run --user --scope -p MemoryMax=8G) or as a MiScusi job; it writes about 150 MB under --dir. The synthetic tape says nothing
about the real arena tape's size or print rate: the numbers are a scale check for the pricer's own loop, and the first night on the real tape is the measurement.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import math
import random
import resource
import time
from datetime import datetime, timezone
from pathlib import Path

from tools import hv4_prompts as pr
from tools import hv4_review as RV

SOL = 1_000_000_000
V = 17_584_505_288
WSOL = "So11111111111111111111111111111111111111112"
H = 3_600_000
GRID = 300_000


def hour_name(t: int) -> str:
    return "tape-" + datetime.fromtimestamp(t // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"


def write_tape(d: Path, pools: int, t_start: int, t_end: int, period_s: int, rng: random.Random) -> int:
    d.mkdir(parents=True, exist_ok=True)
    files: dict[str, list[str]] = {}
    n = 0
    for k in range(pools):
        pool, mint = f"BenchPool{k:04d}" + "1" * 28, f"BenchMint{k:04d}" + "1" * 28
        files.setdefault(hour_name(t_start), []).append(json.dumps({
            "v": 1, "type": "pool_create", "prog": "pumpswap", "slot": 399_000_000, "t_recv_ms": t_start, "signature": f"pc{k}", "event_index": 0, "pool": pool,
            "mint": mint, "quote_mint": WSOL}))
        price = 1e-6 * rng.uniform(0.5, 2.0)
        p0 = price
        t = t_start + 1000 + rng.randrange(period_s * 1000)
        while t < t_end:
            n += 1
            price *= math.exp(rng.gauss(0.0, 0.004))
            q_eff = (60 * SOL + V) / 1e9 * (price / p0) ** 0.5
            buy = rng.random() < 0.55
            sol = int(rng.uniform(0.05, 0.4) * SOL)
            files.setdefault(hour_name(t), []).append(json.dumps({
                "v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 400_000_000 + (t - t_start) // 400, "t_recv_ms": t, "signature": f"s{k}-{n}",
                "event_index": 0, "pool": pool, "mint": mint, "trader": f"W{rng.randrange(60)}", "side": "buy" if buy else "sell", "sol_lamports": sol,
                "token_raw": int(sol / SOL / price * 1_000_000), "quote_reserve": int(q_eff * 1e9) - V, "base_reserve": int(q_eff / price * 1_000_000),
                "virtual_quote_reserves": V, "quote_mint": WSOL, "event_ts": t // 1000}))
            t += int(period_s * 1000 * rng.uniform(0.5, 1.5))
    for name, lines in files.items():  # one file per hour; the reader sorts within the hour by arrival time
        lines.sort(key=lambda s: json.loads(s)["t_recv_ms"])
        (d / name).write_text("\n".join(lines) + "\n")
    return n + pools


def write_skips(root: Path, arm: str, day: str, cut: int, pools: int, skips: int, rng: random.Random) -> None:
    (root / "decisions" / arm).mkdir(parents=True, exist_ok=True)
    (root / "cards").mkdir(parents=True, exist_ok=True)
    slots = list(range((cut - 24 * H) // GRID + 1, (cut - H) // GRID))
    picks = sorted({(rng.choice(slots) * GRID + 2000, rng.randrange(pools)) for _ in range(skips)})
    rows, cards = [], []
    for i, (t, k) in enumerate(picks):
        card = {"id": f"BenchPool{k:04d}" + "1" * 28, "as_of_ms": t, "as_of_slot": 0, "f": {"age_tier": "established", "ret_15m": rng.uniform(-10, 10)}, "x": None}
        sha = pr.card_sha256(card)
        rows.append({"seq": i, "arm": arm, "ms": t + 1500, "kind": "skip", "lane": "G", "pool": card["id"], "card_sha256": sha, "reasons": ["model_skip"]})
        cards.append(RV.canon({"sha": sha, "card": card}))
    (root / "decisions" / arm / f"{day}.jsonl").write_text("".join(RV.canon(r) + "\n" for r in rows))
    (root / "cards" / f"{day}.jsonl").write_text("\n".join(cards) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--pools", type=int, default=100)
    ap.add_argument("--skips", type=int, default=2000)
    ap.add_argument("--hours", type=int, default=30, help="tape length: the 24 h of the skip day plus the pricer's 6 h look-back")
    ap.add_argument("--period-s", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--d", type=int, default=2, help="feed delay slots (a test value)")
    ap.add_argument("--reuse", action="store_true", help="the tape and skip files under --dir already exist: time only the pricing, in a fresh process")
    ap.add_argument("--cap", type=int, default=RV.MAX_SKIP_CARDS, help="skips priced (the reviewer's cap is 60; a larger value shows what the cap saves)")
    a = ap.parse_args(argv)
    from tools.hv4_review_replay import ArenaReplayPricer

    rng = random.Random(a.seed)
    root = Path(a.dir)
    day = "2026-10-21"
    cut = RV.day_start_ms("2026-10-22")
    t_end = cut + 5 * 60_000
    t0 = time.perf_counter()
    if a.reuse:
        rows = sum(f.read_bytes().count(b"\n") for f in (root / "tape").glob("*.jsonl"))
    else:
        rows = write_tape(root / "tape", a.pools, t_end - a.hours * H, t_end, a.period_s, rng)
        write_skips(root, "CON-L", day, cut, a.pools, a.skips, rng)
    gen_s = time.perf_counter() - t0
    RV.MAX_SKIP_CARDS = a.cap
    tape_mb = sum(f.stat().st_size for f in (root / "tape").glob("*.jsonl")) / 1e6
    pricer = ArenaReplayPricer(root / "tape", a.d, sps_fn=lambda: 0.4)
    t1 = time.perf_counter()
    got = RV.load_skip_records("CON-L", root, day, "CON", pricer, cut)
    wall = time.perf_counter() - t1
    print(json.dumps({"tape_rows": rows, "tape_mb": round(tape_mb, 1), "skip_rows": a.skips, "priced_at_most": a.cap, "priced_outcomes": len(got),
                      "exit_reasons": sorted({r["exit_reason"] for r in got}), "gen_s": round(gen_s, 1), "price_wall_s": round(wall, 1),
                      "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 0)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
