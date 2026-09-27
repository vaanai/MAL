#!/usr/bin/env python3
"""Offline memory profiler for the forward-paper engine.

Feeds a historical slice of the sealed trade tape through the exact
`ForwardEngine` class `tools.forward_paper.serve()` uses -- same push_create /
push_print / push_attention / drain_until / _prune calls, same holdback -- with
no sockets and no writes to any live host. It is read-only with respect to
Oracle: every input path here is a local copy pulled ahead of time with
`ssh mal-core-0 'cat ...' > local-file` (or scp), never a live mount.

At regular checkpoints (every `--checkpoint-prints` print rows) it records:
  - RSS (ru_maxrss)
  - the engine's own simulated tape clock (`engine._clock_ms`), so growth can
    be expressed per hour of *tape* time rather than wall time
  - len() of every long-lived engine container implicated by the static
    analysis (library, tracks, by_creator, seen, attention, early, wallets,
    plus the per-wallet sub-dicts that never shrink even after a position
    closes)
  - an approximate deep byte size of each container. `library`/`tracks`/
    `by_creator`/`seen`/`attention`/`early`/`mint_order` stay O(distinct
    mints), so they get a real `pympler.asizeof` every checkpoint. `wallets`
    can be O(total print volume) -- a full asizeof walk of it at every
    checkpoint would dominate the run's own wall time, so instead this script
    calibrates, once, the *measured* (pympler) marginal bytes of an empty
    `_Wallet` and of one more (wallet, mint) pair inside it, then multiplies
    those measured constants by cheap `len()` counts every checkpoint. The
    first and last checkpoint also take a real, full `asizeof(engine.wallets)`
    so the estimate's accuracy against ground truth is itself reported, not
    assumed.
  - a tracemalloc snapshot (shallow, nframe=1: just the allocating line),
    so the top allocating call sites can be listed without the multi-x
    overhead a deep traceback would add to a multi-million-row replay

Output: a JSON report (`--out`) with one row per checkpoint.

This script performs no network I/O and writes only under `--output-dir`.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

from pympler import asizeof

from tools.forward_paper import (
    ForwardEngine,
    JsonlLog,
    LatencyMeter,
    ModelSlot,
    _discover,
    _event_ts,
    _json_safe,
    books_from_config,
    flow_from_tape_row,
    load_config,
    window_creates,
)
from tools.laya_v0 import _Wallet
from tools.paper_price_path import load_creates, open_text

# Containers named in the static-analysis suspect list, minus `book.flow` /
# `book.path.prints`, which PR #120 already bounds per-mint. These are the
# ones that keep one entry per mint (or per creator) forever, and stay
# O(distinct mints) rather than O(print volume) -- cheap to asizeof directly.
MINT_SCALE_CONTAINERS = ("library", "tracks", "by_creator", "seen", "attention", "early", "mint_order")


def _rss_kb() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def _tape_time_bounds(tape: list[Path]) -> tuple[int | None, int | None]:
    """Scan the tape once for min/max `t_recv_ms`, the same field
    `run_replay_files` uses to build `observed` before calling
    `window_creates()`. Deliberately cheap: just a `row.get("t_recv_ms")`
    per line, not the full `flow_from_tape_row` parse, and nothing is held
    in memory beyond two ints -- this is a second pass over the tape purely
    so creates can be windowed *before* any are pushed into the engine,
    instead of loading the whole day's creates unwindowed (see the harness
    bug recorded in ARTIFACTS/lab/forward-paper-memory-2026-09-27.md).
    """
    t_min: int | None = None
    t_max: int | None = None
    for path in tape:
        with open_text(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = row.get("t_recv_ms")
                if not isinstance(t, int):
                    continue
                if t_min is None or t < t_min:
                    t_min = t
                if t_max is None or t > t_max:
                    t_max = t
    return t_min, t_max


def _wallet_substats(wallets: dict[str, Any]) -> dict[str, int]:
    """Aggregate the per-mint sub-dicts every `_Wallet` carries.

    `_on_sell` in laya_v0.WalletState pops `pos_tokens` once a position fully
    closes, but `pos_cost`, `pos_open_t`, `pos_invested`, `mint_pnl` and the
    `mints` set are never popped -- they grow with every distinct mint a
    wallet has ever touched, for as long as the wallet is remembered at all.
    """
    sub = dict(mints=0, pos_tokens=0, pos_cost=0, pos_open_t=0, pos_invested=0, mint_pnl=0, holds=0)
    for w in wallets.values():
        sub["mints"] += len(w.mints)
        sub["pos_tokens"] += len(w.pos_tokens)
        sub["pos_cost"] += len(w.pos_cost)
        sub["pos_open_t"] += len(w.pos_open_t)
        sub["pos_invested"] += len(w.pos_invested)
        sub["mint_pnl"] += len(w.mint_pnl)
        sub["holds"] += len(w.holds)
    return sub


def calibrate_wallet_bytes() -> dict[str, float]:
    """Measure (not guess) the marginal pympler bytes of `_Wallet` growth.

    Returns bytes-per-empty-wallet and bytes-per-(wallet,mint)-pair, derived
    from real asizeof() calls on real `_Wallet` instances -- the same class
    the engine uses -- so the fast per-checkpoint estimate is anchored to a
    measurement, not a guess.
    """
    empty = _Wallet()
    base = asizeof.asizeof(empty)
    w = _Wallet()
    n = 300
    for i in range(n):
        mint = f"CalibMint{i:012d}" + "x" * 20
        w.mints.add(mint)
        w.pos_cost[mint] = 1
        w.pos_open_t[mint] = 1
        w.pos_invested[mint] = 1
        w.mint_pnl[mint] = 1
    with_pairs = asizeof.asizeof(w)
    per_pair = (with_pairs - base) / n
    w2 = _Wallet()
    for i in range(n):
        w2.holds.append(i)
    per_hold = (asizeof.asizeof(w2) - base) / n
    return {"empty_wallet_bytes": float(base), "per_pair_bytes": float(per_pair), "per_hold_bytes": float(per_hold)}


def estimate_wallets_bytes(wallets: dict[str, Any], sub: dict[str, int], calib: dict[str, float]) -> float:
    n_wallets = len(wallets)
    # Each pair touches up to 4 dicts (pos_cost/pos_open_t/pos_invested/mint_pnl)
    # plus the `mints` set entry; calibration already amortizes all of that
    # per added mint (since the calibration loop adds to all of them together).
    pairs = sub["pos_cost"] + sub["pos_open_t"] + sub["pos_invested"] + sub["mint_pnl"] + sub["mints"]
    pairs_equiv = pairs / 5.0
    return n_wallets * calib["empty_wallet_bytes"] + pairs_equiv * calib["per_pair_bytes"] + sub["holds"] * calib["per_hold_bytes"]


def take_snapshot(
    engine: ForwardEngine,
    *,
    checkpoint: int,
    label: str,
    t0_wall: float,
    calib: dict[str, float],
    deep_wallets: bool,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "checkpoint": checkpoint,
        "label": label,
        "wall_s": round(time.time() - t0_wall, 1),
        "sim_clock_ms": engine._clock_ms,
        "rss_kb": _rss_kb(),
        "n_prints": engine._prints,
        "counts": {},
        "bytes": {},
    }
    for name in MINT_SCALE_CONTAINERS:
        c = getattr(engine, name)
        row["counts"][name] = len(c)
        row["bytes"][name] = asizeof.asizeof(c)
    sub = _wallet_substats(engine.wallets.wallets)
    row["counts"]["wallets"] = len(engine.wallets.wallets)
    row["counts"].update({f"wallet_sub_{k}": v for k, v in sub.items()})
    row["bytes"]["wallets_estimated"] = estimate_wallets_bytes(engine.wallets.wallets, sub, calib)
    if deep_wallets:
        row["bytes"]["wallets_asizeof_true"] = asizeof.asizeof(engine.wallets)
    row["counts"]["grids_housekeeping"] = len(engine.grids) + len(engine.mig15) + len(engine.emitted_grids) + len(engine.mig15_waiting)
    row["bytes"]["grids_housekeeping"] = (
        asizeof.asizeof(engine.grids) + asizeof.asizeof(engine.mig15) + asizeof.asizeof(engine.emitted_grids) + asizeof.asizeof(engine.mig15_waiting)
    )
    row["counts"]["positions_rows"] = len(engine.positions)
    row["counts"]["decisions_rows"] = len(engine.decisions)
    row["counts"]["packets"] = len(engine.packets)
    row["counts"]["inbox"] = len(engine.inbox)
    if deep_wallets:
        row["bytes"]["books"] = asizeof.asizeof(engine.books)
        row["bytes"]["graph"] = asizeof.asizeof(engine.graph) if engine.graph is not None else 0
    return row


def top_tracemalloc(snapshot: tracemalloc.Snapshot, key_type: str, limit: int) -> list[dict[str, Any]]:
    stats = snapshot.statistics(key_type)
    out = []
    for stat in stats[:limit]:
        frame = stat.traceback[0]
        out.append(
            {
                "file_line": f"{frame.filename}:{frame.lineno}",
                "size_kb": round(stat.size / 1024, 1),
                "count": stat.count,
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--tape-dir", type=Path)
    ap.add_argument("--tape", nargs="*", type=Path, default=[])
    ap.add_argument("--creates-dir", type=Path)
    ap.add_argument("--creates", nargs="*", type=Path, default=[])
    ap.add_argument("--attention-dir", type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--checkpoint-prints", type=int, default=200_000)
    ap.add_argument("--holdback-ms", type=int, default=300)
    ap.add_argument("--tracemalloc-frames", type=int, default=1)
    ap.add_argument("--tracemalloc-top", type=int, default=20)
    ap.add_argument("--write-logs", action="store_true", help="also write decisions/positions jsonl (slower, more disk)")
    ap.add_argument("--out", type=Path, help="JSON report path (default: <output-dir>/mem-profile.json)")
    args = ap.parse_args(argv)

    raw = load_config(args.config)
    books = books_from_config(raw)

    tape = list(args.tape)
    if args.tape_dir:
        tape.extend(_discover(args.tape_dir, ("trades-*.jsonl", "trades-*.jsonl.zst", "trades-*.jsonl.gz")))
    tape = sorted({p.resolve() for p in tape})
    creates_paths = list(args.creates)
    if args.creates_dir:
        creates_paths.extend(_discover(args.creates_dir, ("observe-*.jsonl", "observe-*.jsonl.zst")))
    creates_paths = sorted({p.resolve() for p in creates_paths})
    attention_paths: list[Path] = []
    if args.attention_dir and args.attention_dir.is_dir():
        attention_paths = sorted(args.attention_dir.glob("attention-*.jsonl*"))

    if not tape:
        print("no tape files found", file=sys.stderr)
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out or (args.output_dir / "mem-profile.json")

    print("calibrating _Wallet marginal bytes via pympler.asizeof...", file=sys.stderr)
    calib = calibrate_wallet_bytes()
    print(f"calibration: {calib}", file=sys.stderr)

    model_path = Path(raw["model_path"]) if raw.get("model_path") else None
    meta_path = Path(raw["model_meta"]) if raw.get("model_meta") else None
    barrier_path = Path(raw["barrier_model"]) if raw.get("barrier_model") else None
    swing_path = Path(raw["swing_model"]) if raw.get("swing_model") else None
    slippage = float(raw.get("slippage_cap", 0.15))
    holdback_ms = args.holdback_ms

    model = ModelSlot(model_path, meta_path)
    model.maybe_reload(force=True)
    barrier = ModelSlot(barrier_path, meta_path)
    barrier.maybe_reload(force=True)
    swing = ModelSlot(swing_path, None)
    swing.maybe_reload(force=True)

    logs = {}
    if args.write_logs:
        logs = {
            "decisions": JsonlLog(args.output_dir / "decisions.jsonl"),
            "positions": JsonlLog(args.output_dir / "positions.jsonl"),
        }

    # No `now_ms` callable: matches replay_rows(), so `_prune`'s "live_now"
    # comes from the tape's own simulated clock, not wall time. This is the
    # same class and the same code path `serve()` drives -- only the event
    # source (a fixed file slice instead of a live tail) differs.
    engine = ForwardEngine(
        books,
        kill_file=args.output_dir / "KILL",
        latency=LatencyMeter(),
        model=model,
        barrier=barrier,
        swing=swing,
        slippage_cap=slippage,
        logs=logs,
        fail_rate=0.0,
        positions_path=args.output_dir / "positions.jsonl",
    )

    attention_dir = Path(raw["attention_dir"]) if raw.get("attention_dir") else args.attention_dir
    if attention_dir and attention_dir.is_dir():
        from tools.forward_paper import bind_attention

        bind_attention(engine, attention_dir)

    graph_dir = Path(str(raw.get("graph_dir") or "")) if raw.get("graph_dir") else None
    if graph_dir is not None and graph_dir.is_dir():
        engine.graph_dir = graph_dir

    print(
        f"forward_paper_mem_profile: tape files={len(tape)} creates files={len(creates_paths)} attention files={len(attention_paths)}",
        file=sys.stderr,
    )

    print("scanning tape for time bounds (to window creates)...", file=sys.stderr)
    tape_t_min, tape_t_max = _tape_time_bounds(tape)
    print(f"tape spans [{tape_t_min}, {tape_t_max}]", file=sys.stderr)

    creates = load_creates(creates_paths)
    n_creates_loaded = len(creates)
    if tape_t_min is not None and tape_t_max is not None:
        creates = window_creates(creates, tape_t_min, tape_t_max)
    print(
        f"windowed creates {n_creates_loaded} -> {len(creates)} to the tape's own "
        f"[{tape_t_min}, {tape_t_max}] range (same window_creates() run_replay_files uses)",
        file=sys.stderr,
    )
    for create in creates.values():
        engine.push_create(create)
    print(f"pushed {len(creates)} creates", file=sys.stderr)

    n_attn = 0
    for path in attention_paths:
        with open_text(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                engine.push_attention(row)
                n_attn += 1
    print(f"pushed {n_attn} attention rows", file=sys.stderr)

    tracemalloc.start(args.tracemalloc_frames)
    t0_wall = time.time()
    checkpoints: list[dict[str, Any]] = []
    tm_reports: dict[int, dict[str, Any]] = {}

    checkpoints.append(
        take_snapshot(engine, checkpoint=0, label="after_creates+attention", t0_wall=t0_wall, calib=calib, deep_wallets=True)
    )

    n_prints = 0
    n_rows = 0
    next_check = args.checkpoint_prints
    checkpoint_idx = 1
    first_t_ms: int | None = None
    last_t_ms: int | None = None

    for path in tape:
        with open_text(path) as fh:
            for line in fh:
                n_rows += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                parsed = flow_from_tape_row(row)
                if parsed is None:
                    continue
                mint, pr = parsed
                engine.push_print(mint, pr, _event_ts(row))
                n_prints += 1
                if first_t_ms is None:
                    first_t_ms = pr.t_recv_ms
                last_t_ms = pr.t_recv_ms

                if n_prints >= next_check:
                    watermark = pr.t_recv_ms - holdback_ms
                    engine.drain_until(max(0, watermark))
                    engine._prune(engine._clock_ms or pr.t_recv_ms)
                    snap = tracemalloc.take_snapshot()
                    tm_reports[checkpoint_idx] = {"by_lineno": top_tracemalloc(snap, "lineno", args.tracemalloc_top)}
                    row_ck = take_snapshot(
                        engine,
                        checkpoint=checkpoint_idx,
                        label=f"prints={n_prints}",
                        t0_wall=t0_wall,
                        calib=calib,
                        deep_wallets=False,
                    )
                    checkpoints.append(row_ck)
                    elapsed_tape_h = (last_t_ms - first_t_ms) / 3_600_000 if first_t_ms else 0.0
                    print(
                        f"[ck {checkpoint_idx}] prints={n_prints} tape_h={elapsed_tape_h:.2f} wall_s={row_ck['wall_s']:.0f} "
                        f"rss_mb={row_ck['rss_kb']/1024:.0f} library={row_ck['counts']['library']} "
                        f"wallets={row_ck['counts']['wallets']} tracks={row_ck['counts']['tracks']} "
                        f"wallets_est_mb={row_ck['bytes']['wallets_estimated']/1e6:.1f}",
                        file=sys.stderr,
                    )
                    checkpoint_idx += 1
                    next_check += args.checkpoint_prints

    # Final drain + prune + snapshot (with the ground-truth deep wallets asizeof).
    if last_t_ms is not None:
        engine.drain_until(last_t_ms, final=True)
        engine._prune(engine._clock_ms or last_t_ms)
    snap = tracemalloc.take_snapshot()
    tm_reports[checkpoint_idx] = {"by_lineno": top_tracemalloc(snap, "lineno", args.tracemalloc_top)}
    checkpoints.append(
        take_snapshot(engine, checkpoint=checkpoint_idx, label="final", t0_wall=t0_wall, calib=calib, deep_wallets=True)
    )
    tracemalloc.stop()

    for log in logs.values():
        log.close()

    report = {
        "calibration": calib,
        "tape_files": [str(p) for p in tape],
        "creates_files": [str(p) for p in creates_paths],
        "attention_files": [str(p) for p in attention_paths],
        "n_rows_read": n_rows,
        "n_prints_applied": n_prints,
        "first_t_ms": first_t_ms,
        "last_t_ms": last_t_ms,
        "tape_span_h": (last_t_ms - first_t_ms) / 3_600_000 if first_t_ms and last_t_ms else None,
        "checkpoints": checkpoints,
        "tracemalloc": tm_reports,
    }
    out_path.write_text(json.dumps(_json_safe(report), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
