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
    """Aggregate the per-mint sub-containers every `_Wallet` carries.

    `_on_sell` in laya_v0.WalletState pops `pos[mint]` once a position fully
    closes, but `mint_pnl` and the `mints` set are never popped -- they grow
    with every distinct mint a wallet has ever touched, for as long as the
    wallet is remembered at all. `pos` (the compacted `pos_tokens`/`pos_cost`/
    `pos_open_t`/`pos_invested`) is transient: it only holds entries for
    mints with a currently-open (not fully sold) position.
    """
    sub = dict(mints=0, pos=0, mint_pnl=0, holds=0)
    for w in wallets.values():
        sub["mints"] += len(w.mints)
        sub["pos"] += len(w.pos)
        sub["mint_pnl"] += len(w.mint_pnl)
        sub["holds"] += len(w.holds)
    return sub


def calibrate_wallet_bytes() -> dict[str, float]:
    """Measure (not guess) the marginal pympler bytes of `_Wallet` growth.

    Returns bytes-per-empty-wallet and the marginal bytes of one more entry
    in each of `_Wallet`'s per-mint containers, derived from real asizeof()
    calls on real `_Wallet` instances -- the same class the engine uses --
    so the fast per-checkpoint estimate is anchored to a measurement, not a
    guess. Each container is calibrated in isolation (a fresh `_Wallet` per
    container) so one estimate cannot mask another's true marginal cost.
    """
    empty = _Wallet()
    base = asizeof.asizeof(empty)
    n = 300

    w_mints = _Wallet()
    for i in range(n):
        w_mints.mints.add(f"CalibMint{i:012d}" + "x" * 20)
    per_mints_entry = (asizeof.asizeof(w_mints) - base) / n

    w_pnl = _Wallet()
    for i in range(n):
        w_pnl.mint_pnl[f"CalibMint{i:012d}" + "x" * 20] = 1
    per_mint_pnl_entry = (asizeof.asizeof(w_pnl) - base) / n

    w_pos = _Wallet()
    for i in range(n):
        w_pos.pos[f"CalibMint{i:012d}" + "x" * 20] = [1, 1, 1, 1]
    per_pos_entry = (asizeof.asizeof(w_pos) - base) / n

    w_holds = _Wallet()
    for i in range(n):
        w_holds.holds.append(i)
    per_hold = (asizeof.asizeof(w_holds) - base) / n

    return {
        "empty_wallet_bytes": float(base),
        "per_mints_entry_bytes": float(per_mints_entry),
        "per_mint_pnl_entry_bytes": float(per_mint_pnl_entry),
        "per_pos_entry_bytes": float(per_pos_entry),
        "per_hold_bytes": float(per_hold),
    }


def estimate_wallets_bytes(wallets: dict[str, Any], sub: dict[str, int], calib: dict[str, float]) -> float:
    n_wallets = len(wallets)
    return (
        n_wallets * calib["empty_wallet_bytes"]
        + sub["mints"] * calib["per_mints_entry_bytes"]
        + sub["mint_pnl"] * calib["per_mint_pnl_entry_bytes"]
        + sub["pos"] * calib["per_pos_entry_bytes"]
        + sub["holds"] * calib["per_hold_bytes"]
    )


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
    ap.add_argument(
        "--creates-since-ms",
        type=int,
        default=None,
        help=(
            "Lower bound for windowed creates, in epoch ms, instead of the tape "
            "slice's own start. Use the runner's actual boot instant when "
            "replaying a slice that starts mid-run (e.g. Oracle's serve() cold "
            "start), so a mint created between that boot and the tape slice's "
            "own start -- which the continuously-running process would already "
            "have in `library` -- is not wrongly treated as createless (which "
            "buffers its prints in `self.early` forever instead of pruning them "
            "as a normal library entry; see the lab note for how much this can "
            "inflate `early`). Omit this to window strictly to the tape slice "
            "itself, i.e. simulate a cold start exactly at the slice's start."
        ),
    )
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

    # A `now_ms` callable IS supplied here, on purpose, pointed at the tape's
    # own simulated clock rather than wall time -- unlike `replay_rows()`
    # (ParityTests, promotion backtests), which passes `LatencyMeter()` with
    # no `now_ms` at all. That is correct for THOSE tools: offline re-scoring
    # wants the full historical `book.flow`, not a live-sized window, because
    # `local_features`/promotion re-derive decisions after the fact. But
    # `_prune()` reuses that exact same `self.latency.now_ms is not None`
    # flag to gate its rolling `LIVE_IDLE_RETAIN_MS` (3-minute) truncation of
    # `book.flow`/`book.path.prints` for a mint that is not yet 45-minutes
    # idle (`forward_paper.py`'s `_prune`, guarded at
    # `if self.latency.now_ms is not None and book.flow:`) -- the one
    # production `serve()` gets for free from its real wall clock
    # (`now_ms=lambda: int(time.time() * 1000)`). Measured directly: without
    # this, a 1-hour replay showed `library`/`by_creator`/`seen` bytes
    # growing to ~200 MB with the *mint count unchanged* (1341 the whole
    # hour) -- i.e. `book.flow` growing unbounded for any mint still getting
    # occasional prints, because the live-window cap never engaged and only
    # the 45-minute-fully-idle -> 3-anchor reduction remained. That is not
    # what `serve()` retains; it is an artifact of reusing the offline
    # (`replay_rows`) code path for a *memory* measurement instead of a
    # *decision* one. Since this script does not compare decisions against
    # any other replay (no `--write-logs` by default; nothing diffs its
    # positions/decisions), giving it a synthetic tape-clock `now_ms` here
    # does not compromise anything this script itself claims -- it makes the
    # measured `library`/`tracks`/`by_creator`/`seen`/`early` bytes represent
    # what `serve()` actually keeps, not what a from-scratch offline re-score
    # would.
    _clock_box: dict[str, int] = {"ms": 0}
    engine = ForwardEngine(
        books,
        kill_file=args.output_dir / "KILL",
        latency=LatencyMeter(now_ms=lambda: _clock_box["ms"]),
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
        if args.creates_since_ms is not None:
            lo = min(args.creates_since_ms, tape_t_min)
            creates = window_creates(creates, lo, tape_t_max, pad_ms=0)
            print(
                f"windowed creates {n_creates_loaded} -> {len(creates)} to "
                f"[--creates-since-ms={args.creates_since_ms}, {tape_t_max}] "
                "(mid-run replay: includes creates from the runner's own boot, not just this slice)",
                file=sys.stderr,
            )
        else:
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
                _clock_box["ms"] = pr.t_recv_ms
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
