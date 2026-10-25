#!/usr/bin/env python3
"""hv6_memscale: peak RSS of the HV6 runner against SIMULATED hours, on a SYNTHETIC PumpSwap tape scaled to a chosen volume. Prints counts and MB only.

PAPER only. StubModel, a fake chain, a tape this tool writes itself under --out (a temp dir). It reads no real tape and no /data/mal/arena path; the volume
figures (pools and prints per hour) are ARGUMENTS taken from the writer's documented rates (tools/arena_state.py MEMORY note: about 270 new keys a minute and
880k prints an hour on the 2026-10-19 tape), not from tape contents. One process per configuration (run it twice, never run1 and run2 in one process).

  python3 -m tools.hv6_memscale --out /tmp/x --hours 4 --pools-per-min 3 --prints-per-hour 100000 [--no-bound]

Each simulated hour it prints one JSON line: rss_mb, state keys, engine pools, engine prints held, quote-history rows, decision-log rows.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import math
import os
import random
import shutil
import sys
from pathlib import Path
from typing import Any

SOL = 1_000_000_000
HOUR = 3_600_000
MIN = 60_000
VQ = 17_584_505_288
WSOL = "So11111111111111111111111111111111111111112"
T0 = 1_790_316_000_000  # 2026-09-25T06:00Z, a synthetic instant long before any seal window


def rss_mb() -> float:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024.0, 1)
    return -1.0


def hour_name(t: int) -> str:
    from datetime import datetime, timezone

    return "tape-" + datetime.fromtimestamp(t // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"


def write_tape(d: Path, start_ms: int, hours: int, pools_per_min: float, prints_per_hour: int, seed: int = 1, pre_hours: int = 8, mean_life_h: float = 10.0) -> dict:
    """Synthetic PumpSwap tape, one hour file at a time (never the whole tape in memory). Pools are born at `pools_per_min`, live an exponential time, and trade with
    log-normal weights; `prints_per_hour` is split among the pools alive that hour. Starts `pre_hours` before start_ms so pools are old enough to be carded."""
    rnd = random.Random(seed)
    d.mkdir(parents=True, exist_ok=True)
    pools: list[dict] = []
    n_sig = 0
    t_begin = start_ms - pre_hours * HOUR
    total = {"pools": 0, "prints": 0}
    for h in range(pre_hours + hours):
        h0 = t_begin + h * HOUR
        rows: list[tuple[int, dict]] = []
        for m in range(60):
            k = int(pools_per_min) + (1 if rnd.random() < pools_per_min - int(pools_per_min) else 0)
            for _ in range(k):
                i = len(pools)
                t = h0 + m * MIN + rnd.randrange(MIN)
                pools.append({"i": i, "pool": f"Pxx{i:07d}" + "1" * 33, "mint": f"Mnt{i:07d}" + "1" * 33, "born": t, "dies": t + int(rnd.expovariate(1.0 / mean_life_h) * HOUR),
                              "w": math.exp(rnd.gauss(0, 1.2)), "price": 1e-6 * math.exp(rnd.gauss(0, 0.3))})
                n_sig += 1
                rows.append((t, {"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": 399_000_000 + (t - t_begin) // 400, "t_recv_ms": t, "signature": f"pc{n_sig}",
                                 "event_index": 0, "pool": pools[-1]["pool"], "mint": pools[-1]["mint"], "quote_mint": WSOL}))
                total["pools"] += 1
        alive = [p for p in pools if p["born"] < h0 + HOUR and p["dies"] > h0]
        wsum = sum(p["w"] for p in alive) or 1.0
        for p in alive:
            lo, hi = max(h0, p["born"]), min(h0 + HOUR, p["dies"])
            n = int(round(prints_per_hour * p["w"] / wsum))
            for _ in range(n):
                t = rnd.randrange(int(lo), int(hi)) if hi > lo else int(lo)
                p["price"] *= math.exp(rnd.gauss(0, 0.004))
                buy = rnd.random() < 0.6
                sol = int(rnd.uniform(0.02, 0.4) * SOL)
                q, b = 60 * SOL, int((60 * SOL + VQ) / SOL / p["price"] * 1_000_000)
                n_sig += 1
                rows.append((t, {"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 399_000_000 + (t - t_begin) // 400, "t_recv_ms": t,
                                 "signature": f"s{n_sig}", "event_index": 0, "pool": p["pool"], "mint": p["mint"], "trader": f"T{p['i'] % 997}_{rnd.randrange(60)}",
                                 "side": "buy" if buy else "sell", "sol_lamports": sol, "token_raw": int(sol / SOL / p["price"] * 1_000_000), "quote_reserve": q,
                                 "base_reserve": b, "virtual_quote_reserves": VQ, "quote_mint": WSOL, "event_ts": t // 1000}))
                total["prints"] += 1
        rows.sort(key=lambda r: (r[0], r[1]["signature"]))
        (d / hour_name(h0)).write_text("\n".join(json.dumps(r) for _, r in rows) + "\n")
    return total


def build_runner(base: Path, tape: Path, *, bound: bool, cfg_extra: dict | None = None) -> Any:
    from tools import drill_hv6_item6 as D
    from tools import hv6_agents as A
    from tools import hv6_runner as R
    from tools.arena_paper import ArenaEngine, register_bot
    from tools.arena_state import ArenaState

    os.environ["MAL_ARENA_TEST"] = "1"
    root = base / "v6"
    guard = A.BudgetGuard(root)
    model = A.StubModel(D._script, guard=guard, latency_ms=50)
    reg = base / "reg.json"
    eng = ArenaEngine(base / "eng", reg, cfg=R.stack_config(2), sps_fn=lambda: 0.4)
    for a in R.ARM_ORDER:
        register_bot(R.bot_id(a), "abcdef1234567", T0 - 60 * MIN, str(reg), deciding_stake=0.25, model_ids=[A.MODEL_ID] if A.ARMS[a].llm else [], now_ms=T0 - 61 * MIN,
                     verify_sha=False)
    kw = dict(cfg_extra or {})
    if "bound_memory" in R.RunnerConfig.__dataclass_fields__:
        kw["bound_memory"] = bound
    cfg = R.RunnerConfig(root=str(root), frozen_sha="abcdef1234567", clock_start_ms=T0 - 60 * MIN, **kw)
    return R.Runner(cfg, ArenaState(tape, start_ms=0), eng, model, D.FakeChain(), guard)


def sample(run: Any, hour: int) -> dict:
    eng, st = run.engine, run.state
    prints = sum(len(h.slot) for h in eng.pools.values())
    q = sum(len(v) for v in run.quotes.t.values())
    return {"sim_hour": hour, "rss_mb": rss_mb(), "state_keys": len(getattr(st, "_pools", {})), "engine_pools": len(eng.pools), "engine_prints": prints,
            "quote_pools": len(run.quotes.t), "quote_rows": q, "log_rows": sum(len(run.log.rows(a)) for a in (*run.arm_names, "SCAN"))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="a new empty temp directory")
    ap.add_argument("--hours", type=int, default=4)
    ap.add_argument("--pools-per-min", type=float, default=3.0)
    ap.add_argument("--prints-per-hour", type=int, default=100_000)
    ap.add_argument("--mean-life-h", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-bound", action="store_true", help="the runner without its memory bound (the 'before' configuration)")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        print("hv6_memscale: --out must be empty", file=sys.stderr)
        return 2
    from tools import hv6_cards as H

    out.mkdir(parents=True, exist_ok=True)
    H.READ_ROOTS = H.WRITE_ROOTS = (os.path.realpath(out),)  # this tool's own synthetic dir is its only allowlist; production roots are never added
    start = T0
    tape = out / "tape"
    gen = write_tape(tape, start, a.hours, a.pools_per_min, a.prints_per_hour, a.seed, mean_life_h=a.mean_life_h)
    print(json.dumps({"memscale": "start", "hours": a.hours, "pools_per_min": a.pools_per_min, "prints_per_hour": a.prints_per_hour, "bound": not a.no_bound, "generated": gen,
                      "rss_after_generate_mb": rss_mb(), "tape": "synthetic"}), flush=True)
    base = out / "run"
    run = build_runner(base, tape, bound=not a.no_bound)
    peak = [0.0]
    orig = run.tick
    last = [-1]

    def tick(T: int) -> None:
        orig(T)
        h = (T - start) // HOUR
        r = rss_mb()
        peak[0] = max(peak[0], r)
        if (T - start) % HOUR < 1000 and h != last[0] and h > 0:
            last[0] = h
            print(json.dumps(sample(run, int(h))), flush=True)

    run.tick = tick
    run.run(start + 2000, start + a.hours * HOUR)
    print(json.dumps({**sample(run, a.hours), "memscale": "end", "peak_rss_mb": peak[0], "entries": {k: len(b.entries_ms) for k, b in run.books.items()}}), flush=True)
    if not a.keep:
        shutil.rmtree(out, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
