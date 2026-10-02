#!/usr/bin/env python3
"""Forward paper books on the live pump tape.

Tails the recorder's JSONL (it does not restart or edit that service), keeps
per-mint state, and builds LAYA v0 packets with the same functions as the
offline builder. Fills use the PR #76 curve model. Entry delay is the measured
chain → receive → decision → simulated-send path, not a fixed 1s.

Paper only. This process has no key, does not sign, and does not send.
"""

from __future__ import annotations

import argparse
import dataclasses
import gc
import heapq
import json
import math
import random
import signal
import statistics
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, TextIO

from observe.attention import is_genuine_arrival, load_poller_start_ms, load_snapshot_keys
from tools.forward_exp012_gate import Exp012Online, GateConfigError, GateSpec, gate_row, load_gate
from tools.funding_graph import fill_funding_features
from tools.graduated_swing import (
    SWING_EXITS,
    SWING_LADDERS,
    AttentionEvent,
    _migration_price,
    features_at,
)
from tools.laya_v0 import (
    BUYER_TRIGGER_NS,
    CURVE_LEVELS,
    DECISION_OFFSETS_MS,
    FEATURE_NAMES,
    LADDER_RULES,
    TRADE_TRIGGER_BUYERS,
    TRADE_TRIGGER_NEAR_MS,
    Booster,
    FlowPrint,
    LadderRule,
    MintBook,
    WalletState,
    _curve_progress,
    _dedupe_key,
    _json_safe,
    _price_at,
    build_feature_rows,
    flow_from_row,
    RankWindow,
    packet_at,
    _ladder_legs,
    simulate_ladder,
    vector,
)
from tools.paper_curve_math import (
    DEFAULT_SIZE_LAMPORTS,
    DEFAULT_SLIPPAGE_CAP,
    LAMPORTS_PER_SOL,
    PORTAL_FEE_PPM,
    PRIORITY_FEE_LAMPORTS,
)
from tools.paper_price_path import (
    CreateSignal,
    MintPath,
    TxOrder,
    create_from_observe_row,
    load_creates,
    open_text,
    parse_time_ms,
)
from tools.paper_tape_scoreboard import (
    DEFAULT_FAIL_RATE,
    EXIT_RULES,
    FEE_SENSITIVITY_PRIORITIES,
    ExitRule,
    _pct,
    _stats_from_lamports,
    _plan_exit_resume,
    features_at_t,
    priority_grid,
    priority_sides_for_event,
    simulate_exit,
    try_entry,
)

SCHEMA_DECISION = "forward_paper_decision_v1"
SCHEMA_POSITION = "forward_paper_position_v1"
SCHEMA_PNL = "forward_paper_pnl_v1"
SCHEMA_LATENCY = "forward_paper_latency_v1"
SCHEMA_RECONCILE = "forward_paper_reconcile_v1"

# Hard ceilings. Config may set a tighter limit. It cannot raise or disable these.
HARD_MAX_POSITION_LAMPORTS = DEFAULT_SIZE_LAMPORTS  # 0.05 SOL
CEILING_MAX_CONCURRENT = 3
CEILING_DAILY_LOSS_LAMPORTS = 200_000_000  # 0.2 SOL
DEFAULT_DAILY_LOSS_LAMPORTS = CEILING_DAILY_LOSS_LAMPORTS
DEFAULT_MAX_CONCURRENT = CEILING_MAX_CONCURRENT
# Graduated-swing selection sample ends here. Swing books score strictly after it.
# Config may move this later. It cannot move it earlier.
SWING_FREEZE_AT = "2026-09-25T18:25:57Z"
SWING_FREEZE_MS = 1_790_360_757_000
MIG15_OFFSET_MS = 900_000
SWING_RULES: dict[str, ExitRule] = {rule.rule_id: rule for rule in SWING_EXITS}
SWING_LADDER_BY_ID: dict[str, LadderRule] = {rule.rule_id: rule for rule in SWING_LADDERS}


class RiskConfigError(Exception):
    """Host JSON asked for a looser risk limit than the hard ceiling."""


# Cyclic-GC mitigation for the long-lived `serve()` process. Measured directly
# (tools/forward_paper_mem_profile.py's replay plus a standalone probe growing
# just `WalletState`): a single `gc.collect(2)` over a ~2 GB heap of the kind
# this runner reaches after several hours takes ~1.6 SECONDS wall time, and
# scales close to linearly with the live tracked-object count (roughly 0.35 ms
# per 1,000 objects, from 30 ms at 20k wallets to 1.6 s at 1.6M wallets in the
# probe). That single stop-the-world pause is a plausible cause of the
# `runner_lag` health-check breaches seen with zero swap use (a GC pause is a
# processing stall, not a memory-pressure symptom). None of `_Wallet`,
# `MintBook`, `_Track`, or `WalletState` hold a back-reference that could form
# a reference cycle (checked: no `__del__`, no `weakref`, no cyclic field
# anywhere in tools/forward_paper.py, tools/laya_v0.py, tools/funding_graph.py)
# -- ordinary refcounting already frees every dict/list/set entry the moment
# it is popped or goes out of scope, with or without the cyclic collector.
# The cyclic collector exists only to catch reference cycles, which this
# codebase does not appear to create; letting collections run far less often
# is safe and changes no decision (GC never touches a live object's value,
# only reclaims unreachable ones -- see the md5-identical replay proof in the
# PR that raises these thresholds).
#
# Chosen mitigation: raise the collection thresholds (not `gc.disable()`) so
# automatic collection keeps running as a safety net for any cycle this audit
# missed, just far less often -- default is (700, 10, 10); this cuts
# generation-0 collections by ~70x and, since generation-1/2 trigger on a
# *count* of lower-generation collections, generation-2 runs roughly another
# 4x less often on top of that (~280x fewer stop-the-world scans overall).
# `gc.freeze()` is called once after startup (model files loaded, engine
# built) so the static baseline (interpreter, imports, loaded models) is
# permanently excluded from every future scan -- it cannot go uncollected
# because refcounting still frees it immediately if it ever becomes garbage;
# freezing only removes it from the cyclic scanner's work list.
GC_THRESHOLD = (50_000, 40, 40)

# Live evidence on Oracle, 2h after a restart on the #123 code (raised
# thresholds + one startup freeze): RSS 1.14 GB, 9,713 collections, max pause
# 1,949 ms, avg 21.9 ms, and 3 runner_lag breaches (>5s recv->decision) in
# that window. #123 only froze the *startup* baseline (interpreter, imports,
# loaded models); every `_Wallet`/`MintBook`/`_Track`/`WalletState` entry
# created after that -- the entire live tracked-object set an hours-old
# runner accumulates -- still sits in the generation gen2 walks on every
# collection. That is why gen2 pauses keep growing with uptime instead of
# staying flat after the one startup freeze.
#
# Fix: call `gc.freeze()` again periodically from `serve()`'s main loop (see
# `maybe_gc_freeze`), not just once at startup, so long-lived objects created
# during the run also move to the permanent generation and gen2 only ever
# walks objects created in roughly the last interval.
#
# Two ways to trigger the periodic freeze: (a) a wall-clock timer, checked
# alongside the other `now - last_x > N` blocks `serve()` already runs
# (model reload, prune, summary, status, mem-census), or (b) inside
# `GcStats._callback` itself, freezing right after any collection whose
# `info["generation"] == 2` finishes. Chosen: (a), the wall-clock timer.
# `_callback` fires on *every* collection -- automatic gen0/gen1 included,
# and any manual `gc.collect()` a test calls -- so gating it correctly still
# needs its own rate limit, and running a `gc.freeze()` walk of the live heap
# immediately after a gen2 pause risks stacking two stop-the-world passes
# back to back right when the process was already paused the longest. A
# simple timer keeps periodic-task logic in the one place `serve()` already
# puts it and freezes on a predictable cadence (`GC_FREEZE_INTERVAL_S`)
# independent of how often gen2 happens to fire.
#
# Trade-off, and why it does not change a decision: a frozen object is never
# again collected by the cyclic GC, even if it becomes part of an unreachable
# reference cycle later. That is acceptable because the #123 audit found no
# `__del__`, `weakref`, or cyclic field anywhere in this codebase's engine
# state (`_Wallet`, `MintBook`, `_Track`, `WalletState`) -- there is no cycle
# for the cyclic collector to ever need to catch here. Freezing does not
# touch reference counting: CPython's `gc.freeze()` (Modules/gcmodule.c,
# `gc_freeze_impl`, unchanged through 3.12, the interpreter this runner
# uses -- confirmed via `python3 --version` on this box and the CPython 3.12
# `gc` docs, "gc.freeze: ... objects ... will no longer be included in
# future collections") only moves an object's `PyGC_Head` into the permanent
# generation so the *cyclic* scan skips it; it never touches `ob_refcnt`.
# An object's refcount hitting zero still calls its deallocator immediately,
# frozen or not. So every plain dict/list/set entry this codebase pops or
# drops out of scope -- which is how `_Wallet`/`MintBook`/`_Track` state is
# actually freed -- is freed exactly as before; only a genuine, uncollected
# reference cycle would be missed, and this codebase does not create one.
GC_FREEZE_INTERVAL_S = 600.0  # 10 minutes of wall time between periodic freezes.


class GcStats:
    """Times every collection (automatic or manual) via `gc.callbacks`, and
    tracks periodic `gc.freeze()` calls (see `maybe_gc_freeze`), so `serve()`
    can log a periodic `gc_stats` line the team can verify live.
    """

    def __init__(self) -> None:
        self.collections = 0
        self.last_pause_ms = 0.0
        self.max_pause_ms = 0.0
        self.total_pause_ms = 0.0
        self._t0: float | None = None
        # Periodic-freeze bookkeeping (see `maybe_gc_freeze`). `last_freeze_at`
        # is a `time.monotonic()`-style clock reading, seeded lazily on the
        # first `maybe_gc_freeze` call so the first real freeze happens one
        # full interval after `serve()` starts polling it, not at t=0.
        self.freeze_count = 0
        self.last_freeze_ms = 0.0
        self.total_freeze_ms = 0.0
        self.last_freeze_at: float | None = None

    def _callback(self, phase: str, info: dict[str, Any]) -> None:
        if phase == "start":
            self._t0 = time.perf_counter()
            return
        if self._t0 is None:
            return
        dt_ms = (time.perf_counter() - self._t0) * 1000.0
        self._t0 = None
        self.collections += 1
        self.last_pause_ms = dt_ms
        self.max_pause_ms = max(self.max_pause_ms, dt_ms)
        self.total_pause_ms += dt_ms

    def install(self) -> None:
        gc.callbacks.append(self._callback)

    def report(self) -> dict[str, Any]:
        counts = gc.get_count()
        thresholds = gc.get_threshold()
        return {
            "collections": self.collections,
            "last_pause_ms": round(self.last_pause_ms, 2),
            "max_pause_ms": round(self.max_pause_ms, 2),
            "avg_pause_ms": round(self.total_pause_ms / self.collections, 2) if self.collections else 0.0,
            "total_pause_ms": round(self.total_pause_ms, 2),
            "gen_counts": {"gen0": counts[0], "gen1": counts[1], "gen2": counts[2]},
            "thresholds": {"gen0": thresholds[0], "gen1": thresholds[1], "gen2": thresholds[2]},
            "enabled": gc.isenabled(),
            "freeze_count": self.freeze_count,
            "gc_freeze_count": gc.get_freeze_count(),
            "last_freeze_ms": round(self.last_freeze_ms, 2),
            "total_freeze_ms": round(self.total_freeze_ms, 2),
        }


def install_gc_mitigation() -> GcStats:
    """Raise GC thresholds, freeze the current (static) heap, and start timing
    every collection. Call once, after startup (models loaded, engine built),
    right before the live tail loop. See `GC_THRESHOLD`'s comment above.
    """
    stats = GcStats()
    stats.install()
    gc.set_threshold(*GC_THRESHOLD)
    gc.collect()
    gc.freeze()
    return stats


def maybe_gc_freeze(gc_stats: GcStats, now: float, interval_s: float = GC_FREEZE_INTERVAL_S) -> bool:
    """Best-effort periodic `gc.freeze()`, called every loop tick from
    `serve()` with its own `time.monotonic()` reading (the same clock its
    other periodic-task timers use). Freezes at most once per `interval_s`;
    the first call after startup only seeds the clock (no freeze), matching
    the other `last_x`-style timers in `serve()`. See `GC_THRESHOLD`'s
    comment above for why this is safe and why the wall-clock timer was
    chosen over hooking `GcStats._callback`.

    Diagnostics-and-mitigation only, exactly like `write_mem_census`: never
    allowed to kill `serve()`'s main loop. Any failure -- `gc.freeze()`
    itself raising, a bad clock reading, anything -- is caught here so the
    worst case is one missed freeze, never a crashed run. Returns whether a
    freeze actually ran (tests use this; `serve()` does not need to).
    """
    try:
        if gc_stats.last_freeze_at is None:
            gc_stats.last_freeze_at = now
            return False
        if now - gc_stats.last_freeze_at < interval_s:
            return False
        t0 = time.perf_counter()
        gc.freeze()
        dt_ms = (time.perf_counter() - t0) * 1000.0
        gc_stats.freeze_count += 1
        gc_stats.last_freeze_ms = dt_ms
        gc_stats.total_freeze_ms += dt_ms
        gc_stats.last_freeze_at = now
        return True
    except Exception:  # noqa: BLE001 - periodic freeze is a mitigation only, must never propagate
        return False


def _rss_kb_proc() -> int | None:
    """Current RSS from /proc/self/status (VmRSS), not `resource.ru_maxrss`
    (which only ever grows). Cheap: one small file read."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except OSError:
        return None
    return None


MEM_CENSUS_WALK_BUDGET_MS = 50.0


class MemCensus:
    """Read-only periodic snapshot of every engine container's size, so
    Oracle's live process can report the container-by-container breakdown
    the offline harness (tools/forward_paper_mem_profile.py, #122) could not
    cleanly reproduce at ~460 MB/h. Never mutates `engine` state -- every
    field read here is a `len()` or a value already stored on the object;
    see the md5-identical replay proof in this PR's description (this class
    is only ever called from `serve()`, never from `replay_rows()`/the
    `replay` CLI, so it cannot affect a decision either way).

    Walking every wallet's `pos`/`mints`/`holds`/`mint_pnl` sizes is the one
    part that can get expensive as `wallets` grows (it is exactly the
    container size we are trying to observe). Adaptive stride: start at a
    full walk, time it, and if it is over `MEM_CENSUS_WALK_BUDGET_MS` double
    the stride for the next call (extrapolating the summed counts by that
    stride) instead of skipping the walk outright -- a coarser count is
    still useful, an absent one is not.
    """

    def __init__(self) -> None:
        self._stride = 1
        self._skip_gc_objects = False

    def _walk_wallets(self, wallets: dict[str, Any]) -> dict[str, Any]:
        n = len(wallets)
        stride = max(1, self._stride)
        t0 = time.perf_counter()
        sampled = 0
        pos = mints = holds = mint_pnl = 0
        it = iter(wallets.values())
        idx = 0
        for w in it:
            if idx % stride == 0:
                sampled += 1
                pos += len(w.pos)
                mints += len(w.mints)
                holds += len(w.holds)
                mint_pnl += len(w.mint_pnl)
            idx += 1
        walk_ms = (time.perf_counter() - t0) * 1000.0
        scale = (n / sampled) if sampled else 1.0
        if walk_ms > MEM_CENSUS_WALK_BUDGET_MS:
            self._stride = stride * 2
        elif walk_ms < MEM_CENSUS_WALK_BUDGET_MS / 4 and stride > 1:
            self._stride = max(1, stride // 2)
        return {
            "n_wallets": n,
            "pos_entries": round(pos * scale),
            "mints_entries": round(mints * scale),
            "holds_entries": round(holds * scale),
            "mint_pnl_entries": round(mint_pnl * scale),
            "walk_stride": stride,
            "walk_sampled": sampled,
            "walk_ms": round(walk_ms, 2),
        }

    def snapshot(self, engine: "ForwardEngine") -> dict[str, Any]:
        row: dict[str, Any] = {
            "rss_kb_proc": _rss_kb_proc(),
            "library": len(engine.library),
            "tracks": len(engine.tracks),
            "by_creator": len(engine.by_creator),
            "seen": len(engine.seen),
            "attention": len(engine.attention),
            "early_mints": len(engine.early),
            "early_prints_buffered": sum(len(v) for v in engine.early.values()),
            "dead_mints": len(engine.dead_mints),
            "dead_prints_dropped": engine.dead_prints_dropped,
            "early_timeout_mints_dropped": engine.early_timeout_mints_dropped,
            "tx_order_entries": engine._tx_order.entry_count(),
        }
        row["wallets"] = self._walk_wallets(engine.wallets.wallets)
        t0 = time.perf_counter()
        counts = gc.get_count()
        row["gc_count"] = {"gen0": counts[0], "gen1": counts[1], "gen2": counts[2]}
        if not self._skip_gc_objects:
            n_objs = len(gc.get_objects())
            dt_ms = (time.perf_counter() - t0) * 1000.0
            if dt_ms > MEM_CENSUS_WALK_BUDGET_MS:
                self._skip_gc_objects = True
            row["gc_tracked_objects"] = n_objs
        return row


def write_mem_census(
    engine: "ForwardEngine",
    mem_census: MemCensus,
    gc_stats: GcStats,
    output_dir: Path,
    logs: dict[str, "JsonlLog"],
    now_ms: int,
    failures: int,
) -> int:
    """Best-effort diagnostics only -- must never be able to kill `serve()`'s
    main loop. Every step (the wallet walk, the two file writes) is wrapped
    in one `try`, matched to a single `except Exception` so any failure here
    -- a permissions error on `output_dir`, a transient issue inside the
    adaptive wallet walk, anything -- is caught, counted, and logged at a
    rate-limited cadence (every one of the first 3 failures, then every
    50th) instead of ending the process. Returns the updated failure count.
    """
    try:
        census = mem_census.snapshot(engine)
        census["gc"] = gc_stats.report()
        census["t_ms"] = now_ms
        (output_dir / "mem-census.json").write_text(json.dumps(census, indent=2) + "\n", encoding="utf-8")
        logs["mem_census"].write(census)
        print(f"forward_paper mem_census={json.dumps(census)}", file=sys.stderr)
        return failures
    except Exception as exc:  # noqa: BLE001 - diagnostics only, must never propagate
        failures += 1
        if failures <= 3 or failures % 50 == 0:
            print(f"forward_paper mem_census_error count={failures} err={exc!r}", file=sys.stderr)
        return failures


PRUNE_AFTER_MS = 45 * 60 * 1000
# A restart's fresh `ForwardEngine` starts with an empty `self.library`, and
# `DirectoryTail` never rewinds before its saved/EOF-default offset (see
# `DirectoryTail._ensure`) -- so a create at or before that offset is
# structurally unobservable for the rest of this process's life: no future
# `poll()` can ever return it. Without this, `_add_print` buffers every print
# for such a mint in `self.early` forever (only a full 45-minute silence
# evicts it via `_prune_early`), which is unbounded for as long as the mint
# keeps trading -- the single largest identified leak on Oracle (measured
# ~115 MB/h, see ARTIFACTS/lab/forward-paper-memory-2026-09-27.md).
#
# `_preboot_dead_mints` identifies that doomed set at startup by re-reading
# (read-only) the observe creates file(s) `serve()` is about to tail, and
# excludes any create less than `PREBOOT_DEAD_MARGIN_MS` before boot -- the
# offsets file is flushed every 60s (`serve()`'s `last_summary` cadence) and
# at clean shutdown, so a genuine restart can only replay up to about that
# much trailing history; anything closer to boot than the margin is left on
# the normal `self.early` path (unchanged, existing behavior) in case its
# create is simply still in flight rather than truly unreachable. Erring
# toward "leave it buffered" here is the safe direction: at worst it costs
# the memory this fix targets for a handful of edge-of-boot mints, never a
# dropped decision.
PREBOOT_DEAD_MARGIN_MS = 10 * 60 * 1000
# Rule A (`PREBOOT_DEAD_MARGIN_MS`/`_preboot_dead_mints`) only catches a mint
# whose create predates *this boot*. Measured on the staged 3.8h Oracle slice
# (2026-09-27T14-18Z, creates windowed from the real 06:58:12Z boot), that
# only explained a small share of `self.early`'s growth: of 19,069 distinct
# mints touched, 11,132 have NO create record at all across two full days
# of Oracle's retained `observe-*.jsonl` (not just before this boot) -- e.g.
# `124Yn3UCD4YBb8JcyW6A2g9jjgG4Ykp8VBp7XQNQpump`, sampled directly off that
# slice, trades only on `pumpswap` (post-migration) and has no create on
# Oracle going back to 2026-09-20: an old, already-migrated token, not a
# restart artifact. No feasible amount of extra day-file lookback fixes
# this -- some of these mints are simply older than any retained history.
#
# Rule B is the general catch-all: once a still-createless mint's OLDEST
# buffered `self.early` print is older than this many ms, give up on ever
# getting its create and mark it dead (added to `dead_mints`, buffer
# dropped) instead of waiting for `PRUNE_AFTER_MS`'s full 45-minute-silence
# eviction, which never fires for a mint that keeps trading. Sizing this
# value: on that same slice, of 7,937 mints with both a create anywhere in
# two days and a print in the slice, the print-after-create gap was 1.2s at
# the median and >1 minute for well under 1% (the >1h tail there is a
# different, unrelated population -- an old token's create long preceding
# ANOTHER, later, unrelated trade, not a genuinely-delayed create ingest).
# The direct, decisive evidence is the neutrality proof itself: applying
# this same ~10-minute cutoff to rule A produced byte-identical
# decisions.jsonl/positions.jsonl on the full slice (2,885,548 prints,
# 217,266 decisions, 72 positions) -- nothing in that real data ever needed
# a buffered print older than 10 minutes for a later, legitimate flush.
# Like `dead_mints`, this is opt-in (`None` by default) so replay_rows()/
# ParityTests/promotion backtests, which want full history, are unaffected;
# only `serve()` passes it.
EARLY_BUFFER_DEAD_MS = 10 * 60 * 1000
CHAIN_LAG_MIN_MS = -5_000
CHAIN_LAG_MAX_MS = 120_000
# Healthy recv→decision on this runner is the 26 ms floor (pre-#95 hourly
# p50 was 16–131 ms). The honest-fill cut already used for this book is 5 s.
# A decision older than that cannot be acted on. 5 s rejects the 2.3 h fills.
STALE_ACTION_MS = 5_000
# `TxOrder._seen`/`_next` (see `paper_price_path.py`) never evict on their
# own: about one entry per distinct (slot, signature) print ever stamped by
# `push_print`, unbounded for the life of a long-running `serve()` process
# (~275 bytes/entry, measured by tracemalloc on the real tape). Pruned by
# `_prune()` calling `TxOrder.prune_before_ms(live_now - TX_ORDER_PRUNE_MS)`,
# live only (gated by `tx_order_prune_ms`, `None` for every other caller).
# Margin: 2x the largest of the three known bounds on how late a legitimate
# duplicate/out-of-order print for an old slot can reach `push_print` --
# `STALE_ACTION_MS` (serve()'s own stale-row drop, ~5s), `EARLY_BUFFER_DEAD_MS`
# (createless-buffer give-up, 10 min), and `PRUNE_AFTER_MS` itself (idle-mint
# eviction, 45 min, the largest of the three and this file's own standing
# "nothing legitimate outlives this much silence" bound). `PRUNE_AFTER_MS` is
# the dominant term, so the margin is effectively 90 minutes -- twice as long
# as the longest window anything else in this engine already treats as
# "gone", specifically because no direct measurement of a real duplicate-
# delivery gap exists yet (unlike `EARLY_BUFFER_DEAD_MS`'s measured print-
# after-create gap): this is a documented, generous assumption, not a
# measured one, and the replay-equivalence proof is the actual safety check.
# The stronger guarantee is upstream: serve() drops any row whose t_recv_ms is
# more than STALE_ACTION_MS behind wall clock before it reaches push_print,
# and DirectoryTail resumes from persisted byte offsets, so every stamp()
# happens within seconds of real time and an old slot cannot be re-delivered.
TX_ORDER_PRUNE_MS = 2 * max(PRUNE_AFTER_MS, EARLY_BUFFER_DEAD_MS, STALE_ACTION_MS)
# prune_before_ms scans all of `_seen` (~1.2M keys at the 90 min plateau), so it
# runs at most this often instead of on every `_prune()` (~every 5,000 prints).
# Pruning less often only keeps more entries, never fewer, so it cannot change
# a stamp. Capped at the prune margin so short test margins still prune.
TX_ORDER_PRUNE_EVERY_MS = 600_000
# Live process only. Replay keeps the full path. Covers the 120 s decision grid.
LIVE_IDLE_RETAIN_MS = 180_000
READ_CHUNK_BYTES = 2 * 1024 * 1024
# LatencyMeter is diagnostics only (report()/latency.jsonl). A decision's own
# applied_latency_ms comes from chain_median_ms(), which only ever reads the
# newest 8192 samples, so a ring buffer this much bigger changes no decision.
CHAIN_SAMPLE_CAP = 20_000
LATENCY_SAMPLE_CAP = 50_000
# Post-#95 forward book is void from this instant until the guarded runner is live.
VOID_FROM = "2026-09-25T19:00:00Z"
VOID_FROM_MS = 1_790_362_800_000
VOID_REASON = "post-#95 forward book void: recv to decision lag past the live cap"
GUARD_LIVE_NAME = "guard-live.json"
RUNNER_STATUS_NAME = "runner-status.json"
PROMOTION_VOID_NAME = "invalid-for-promotion.json"
RULES: dict[str, ExitRule] = {rule.rule_id: rule for rule in EXIT_RULES}


def _utc(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000.0))


def _next_utc_midnight_ms(now_ms: int) -> int:
    """First UTC midnight strictly after now_ms."""
    day = 86_400_000
    return now_ms - (now_ms % day) + day


def clean_clock(live_at_ms: int) -> dict[str, str]:
    """7 full UTC days from the first midnight after go-live. Review at 05:00 the next morning."""
    start = _next_utc_midnight_ms(live_at_ms)
    review = start + 7 * 86_400_000 + 5 * 3_600_000
    return {"clean_start": _utc(start), "kill_review_at": _utc(review)}


def decision_counts_for_promotion(decision_t_ms: int | None, live_at_ms: int | None) -> bool:
    """Rows in [VOID_FROM, guard-live) do not count. Earlier rows stay eligible."""
    if isinstance(decision_t_ms, bool) or not isinstance(decision_t_ms, int):
        return False
    if decision_t_ms < VOID_FROM_MS:
        return True
    if live_at_ms is None:
        return False
    return decision_t_ms >= live_at_ms


def position_row_counts_for_promotion(row: dict[str, Any], live_at_ms: int | None) -> bool:
    """Promotion ledger only: shadow, plus rows from before that split existed.

    Ceiling is the capacity book. It is not scored here. The JSONL is left on disk.
    """
    if row.get("event") not in ("close", "miss"):
        return False
    if row.get("ledger") not in (None, "shadow"):
        return False
    return decision_counts_for_promotion(row.get("decision_t_ms"), live_at_ms)


def iter_position_rows(path: Path) -> Iterable[dict[str, Any]]:
    """Read positions.jsonl. Does not truncate or delete it."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def migrate_fee_sensitivity_summary(
    rows: Iterable[dict[str, Any]],
    live_at_ms: int | None,
    book_ids: Iterable[str],
) -> dict[str, Any]:
    """Mean SOL at other routes and priorities. Not a promotion input.

    Portal columns are a shift of the booked `pnl_lamports` (already charged
    at 0.001 SOL per side). Direct columns come from the counterfactual stamp
    on the row. Rows written before that stamp still fill the portal columns.
    """
    labels = [label for label, _priority in FEE_SENSITIVITY_PRIORITIES]
    buckets: dict[str, dict[str, dict[str, list[int]]]] = {
        book_id: {"portal": {label: [] for label in labels}, "direct": {label: [] for label in labels}}
        for book_id in book_ids
    }
    for row in rows:
        book = row.get("book")
        if not isinstance(book, str) or book not in buckets:
            continue
        if not position_row_counts_for_promotion(row, live_at_ms):
            continue
        pnl = row.get("pnl_lamports")
        exit_status = row.get("exit_status")
        sides = priority_sides_for_event(
            str(row.get("event") or ""),
            exit_status if isinstance(exit_status, str) else None,
        )
        if isinstance(pnl, int) and not isinstance(pnl, bool) and sides is not None:
            for label, value in priority_grid(pnl, sides).items():
                buckets[book]["portal"][label].append(value)
        sens = row.get("fee_sensitivity")
        direct = sens.get("direct") if isinstance(sens, dict) else None
        if isinstance(direct, dict):
            for label in labels:
                value = direct.get(label)
                if isinstance(value, int) and not isinstance(value, bool):
                    buckets[book]["direct"][label].append(value)
    out: dict[str, Any] = {}
    for book_id, routes in buckets.items():
        packed: dict[str, Any] = {}
        for route, grids in routes.items():
            packed[route] = {}
            for label, values in grids.items():
                n = len(values)
                packed[route][label] = {
                    "n": n,
                    "mean_sol": None if n == 0 else (sum(values) / n) / LAMPORTS_PER_SOL,
                }
        out[book_id] = {
            "reporting_only": True,
            "promotion_pnl": "pnl_lamports",
            "portal_fee_ppm": {"direct": 0, "portal": PORTAL_FEE_PPM},
            "priority_sol": labels,
            "routes": packed,
        }
    return out


def promotion_pnls_by_book(rows: Iterable[dict[str, Any]], live_at_ms: int | None) -> dict[str, list[int]]:
    """PnL the promotion read keeps. Void-window decision times are left out."""
    out: dict[str, list[int]] = {}
    for row in rows:
        if not position_row_counts_for_promotion(row, live_at_ms):
            continue
        pnl = row.get("pnl_lamports")
        if isinstance(pnl, bool) or not isinstance(pnl, int):
            continue
        book = row.get("book")
        if not isinstance(book, str) or not book:
            continue
        out.setdefault(book, []).append(pnl)
    return out


def row_clock_ms(kind: str, row: dict[str, Any]) -> int | None:
    if kind == "create":
        return parse_time_ms(row.get("t_ws"))
    key = "t_first_ms" if kind == "attention" else "t_recv_ms"
    raw = row.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def load_guard_live_ms(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = data.get("live_at_ms") if isinstance(data, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def ensure_guard_live(directory: Path, now_ms: int) -> int:
    """Stamp the first moment the guarded runner is actually at the live edge."""
    path = directory / GUARD_LIVE_NAME
    existing = load_guard_live_ms(path)
    if existing is not None:
        return existing
    clock = clean_clock(now_ms)
    rec = {
        "schema": "forward_paper_guard_live_v1",
        "live_at_ms": now_ms,
        "live_at": _utc(now_ms),
        "stale_cap_ms": STALE_ACTION_MS,
        "fail_rate": DEFAULT_FAIL_RATE,
        "void_from": VOID_FROM,
        "void_from_ms": VOID_FROM_MS,
        "reason": VOID_REASON,
        **clock,
    }
    path.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    void = {
        "schema": "forward_paper_promotion_void_v1",
        "from": VOID_FROM,
        "from_ms": VOID_FROM_MS,
        "until": rec["live_at"],
        "until_ms": now_ms,
        "reason": VOID_REASON,
        "do_not_delete_tape": True,
        "clean_start": clock["clean_start"],
        "kill_review_at": clock["kill_review_at"],
    }
    (directory / PROMOTION_VOID_NAME).write_text(json.dumps(void, indent=2) + "\n", encoding="utf-8")
    return now_ms


def write_runner_status(path: Path, engine: "ForwardEngine", *, live_at_ms: int | None) -> None:
    now_ms = int(time.time() * 1000)
    newest = engine.newest_recv_ms
    lag = None if newest is None else max(0, now_ms - newest)
    rec = {
        "schema": "forward_paper_runner_status_v1",
        "ts": _utc(now_ms),
        "ts_ms": now_ms,
        "stale_cap_ms": STALE_ACTION_MS,
        "lag_ms": lag,
        "stale_dropped": engine.stale_dropped,
        "fail_rate": engine.fail_rate,
        "promotion_live_ms": live_at_ms,
        "newest_recv_ms": newest,
    }
    path.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _event_ts(row: dict[str, Any]) -> int | None:
    raw = row.get("event_ts")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    whole = int(raw)
    if whole < 1_000_000_000:
        return None
    return whole


def flow_from_tape_row(row: dict[str, Any]) -> tuple[str, FlowPrint] | None:
    """Same acceptance rule as the offline book loader."""
    if row.get("quote_is_wsol") is False:
        return None
    return flow_from_row(row)


def _percentile(values: Sequence[int], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    return _pct(ordered, p)


def _hop_summary(values: Sequence[int]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50_ms": _percentile(values, 0.50),
        "p99_ms": _percentile(values, 0.99),
    }


class LatencyMeter:
    """Hop timers. Applied entry delay is the sum, never a fixed 1s seed."""

    def __init__(self, *, now_ms: Callable[[], int] | None = None, extra_ms: int = 0) -> None:
        self.now_ms = now_ms
        self.extra_ms = extra_ms
        # Ring buffers: diagnostics only. A decision's own applied_latency_ms
        # is computed live in quote()/measure() and never re-reads history
        # beyond chain_median_ms()'s newest-8192 window, so capping the
        # buffers changes no decision, only the long-run percentile report.
        self.chain_to_recv: deque[int] = deque(maxlen=CHAIN_SAMPLE_CAP)
        self.recv_to_decision: deque[int] = deque(maxlen=LATENCY_SAMPLE_CAP)
        self.decision_to_send: deque[int] = deque(maxlen=LATENCY_SAMPLE_CAP)
        self.applied: deque[int] = deque(maxlen=LATENCY_SAMPLE_CAP)
        self._median: int | None = None
        self._median_n = 0
        self._chain_n = 0

    def note_print(self, t_recv_ms: int, event_ts: int | None) -> None:
        if event_ts is None:
            return
        lag = t_recv_ms - event_ts * 1000
        if lag < CHAIN_LAG_MIN_MS or lag > CHAIN_LAG_MAX_MS:
            return
        self.chain_to_recv.append(lag)
        self._chain_n += 1

    def chain_median_ms(self) -> int | None:
        n = self._chain_n
        if n == 0:
            return None
        if self._median is not None and n - self._median_n < 2000:
            return self._median
        tail = list(self.chain_to_recv)[-8192:]
        self._median = int(round(statistics.median(tail)))
        self._median_n = n
        return self._median

    def quote(self, decision_t_ms: int) -> dict[str, Any]:
        """Same delay the execution path would apply. Does not record a send."""
        chain = self.chain_median_ms()
        post = self.extra_ms
        if self.now_ms is not None:
            post += max(0, self.now_ms() - decision_t_ms)
        send = 0
        applied = max(0, (chain or 0) + post + send)
        return {
            "chain_to_recv_ms": chain,
            "recv_to_decision_ms": post,
            "decision_to_send_ms": send,
            "applied_latency_ms": applied,
            "e2e_on_chain_to_send_ms": None if chain is None else chain + post + send,
        }

    def measure(self, decision_t_ms: int) -> dict[str, Any]:
        hops = self.quote(decision_t_ms)
        post = int(hops["recv_to_decision_ms"])
        if post:
            self.recv_to_decision.append(post)
        self.decision_to_send.append(int(hops["decision_to_send_ms"]))
        self.applied.append(int(hops["applied_latency_ms"]))
        return hops

    def report(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_LATENCY,
            "chain_to_recv": _hop_summary(self.chain_to_recv),
            "recv_to_decision": _hop_summary(self.recv_to_decision),
            "decision_to_send": _hop_summary(self.decision_to_send),
            "applied_entry": _hop_summary(self.applied),
            "note": (
                "chain_to_recv is t_recv_ms minus on-chain event_ts. "
                "applied_entry is that lag plus time from the decision clock to the simulated send. "
                "A faster feed (Helius transactionSubscribe) would cut chain_to_recv; it does not change the fee stack."
            ),
        }


@dataclass
class BookSpec:
    book_id: str
    kind: str
    exit_rule: str
    threshold: float | None = None
    top_frac: float | None = None
    point: str | None = None
    model_key: str = "entry"
    max_concurrent: int | None = DEFAULT_MAX_CONCURRENT
    daily_loss_lamports: int | None = DEFAULT_DAILY_LOSS_LAMPORTS
    creator_cooldown_ms: int = 60_000
    token_cooldown_ms: int = 300_000
    size_lamports: int = DEFAULT_SIZE_LAMPORTS
    freeze_ms: int | None = None
    # EXP-012 gate (DEC-016 section 3), migrate books only. Plain config here; the
    # model is verified at load (books_from_config) and loaded once by the engine.
    # Never hot-reloaded: adopt_book_limits keeps the running book's values.
    entry_model: str | None = None
    entry_model_md5: str | None = None
    entry_threshold: float | None = None
    entry_features: str | None = None

    def resolved_exit(self, deploy_rule: str) -> ExitRule | LadderRule:
        rule_id = deploy_rule if self.exit_rule == "deploy" else self.exit_rule
        if self.kind == "swing":
            swing = SWING_RULES.get(rule_id)
            if swing is not None:
                return swing
            ladder = SWING_LADDER_BY_ID.get(rule_id)
            if ladder is not None:
                return ladder
        rule = RULES.get(rule_id)
        if rule is not None:
            return rule
        for ladder in LADDER_RULES:
            if ladder.rule_id == rule_id:
                return ladder
        raise ValueError(f"unknown exit rule {rule_id}")


def load_config(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("config must be a JSON object")
    return raw


def _reject_risk(message: str) -> None:
    raise RiskConfigError(message)


def guard_kill_switch(raw: dict[str, Any]) -> None:
    """The kill switch is always on. Config cannot turn it off or clear its path."""
    if raw.get("kill_switch") is False or raw.get("honor_kill_switch") is False:
        _reject_risk("kill switch cannot be disabled")
    if "kill_file" in raw and not raw.get("kill_file"):
        _reject_risk("kill_file cannot be cleared")


def _optional_cap(item: dict[str, Any], key: str, book_id: str) -> Any:
    """Missing key means 'use the ceiling'. An explicit null is a refused disable."""
    if key not in item:
        return None
    if item[key] is None:
        _reject_risk(f"book {book_id} {key} null would disable the cap")
    return item[key]


def _parse_iso_ms(text: str) -> int:
    import calendar

    return int(calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


def resolve_swing_freeze(raw: dict[str, Any]) -> int:
    """Registered sample end. A missing key uses it. An earlier value is refused."""
    if "swing_freeze_ms" in raw and raw.get("swing_freeze_ms") is None:
        _reject_risk("swing freeze cannot be cleared")
    if "swing_freeze_at" in raw and raw.get("swing_freeze_at") is None:
        _reject_risk("swing freeze cannot be cleared")
    has_ms = isinstance(raw.get("swing_freeze_ms"), (int, float)) and not isinstance(raw.get("swing_freeze_ms"), bool)
    has_at = isinstance(raw.get("swing_freeze_at"), str) and bool(raw.get("swing_freeze_at"))
    if not has_ms and not has_at:
        return SWING_FREEZE_MS
    ms = int(raw["swing_freeze_ms"]) if has_ms else None
    if has_at:
        parsed = _parse_iso_ms(str(raw["swing_freeze_at"]))
        if ms is not None and ms != parsed:
            _reject_risk("swing_freeze_at does not match swing_freeze_ms")
        if ms is None:
            ms = parsed
    if ms is None or ms < SWING_FREEZE_MS:
        _reject_risk(f"swing freeze is before the registered {SWING_FREEZE_AT}")
    return ms


def books_from_config(raw: dict[str, Any]) -> list[BookSpec]:
    guard_kill_switch(raw)
    freeze_ms = resolve_swing_freeze(raw)
    found: list[BookSpec] = []
    for item in raw.get("books") or []:
        if not isinstance(item, dict):
            raise SystemExit("book entries must be objects")
        kind = str(item.get("kind") or "")
        if kind not in ("baseline", "laya", "migrate", "swing"):
            raise SystemExit(f"unknown book kind {kind}")
        book_id = str(item.get("id") or "")
        size_sol = float(item.get("size_sol", raw.get("size_sol", 0.05)))
        size = int(round(size_sol * LAMPORTS_PER_SOL))
        if size <= 0 or size > HARD_MAX_POSITION_LAMPORTS:
            _reject_risk(
                f"book {book_id} size {size_sol} SOL exceeds the {HARD_MAX_POSITION_LAMPORTS / LAMPORTS_PER_SOL} SOL cap"
            )
        loss_raw = _optional_cap(item, "daily_loss_sol", book_id)
        if loss_raw is None:
            loss_lamports = CEILING_DAILY_LOSS_LAMPORTS
        else:
            loss_sol = float(loss_raw)
            loss_lamports = int(round(loss_sol * LAMPORTS_PER_SOL))
            if loss_lamports < 0 or loss_lamports > CEILING_DAILY_LOSS_LAMPORTS:
                _reject_risk(
                    f"book {book_id} daily_loss_sol {loss_sol} exceeds the {CEILING_DAILY_LOSS_LAMPORTS / LAMPORTS_PER_SOL} SOL cap"
                )
        concurrent_raw = _optional_cap(item, "max_concurrent", book_id)
        if concurrent_raw is None:
            concurrent = CEILING_MAX_CONCURRENT
        elif isinstance(concurrent_raw, bool) or not isinstance(concurrent_raw, (int, float)):
            _reject_risk(f"book {book_id} max_concurrent is not a number")
            concurrent = CEILING_MAX_CONCURRENT
        else:
            concurrent = int(concurrent_raw)
            if concurrent < 0 or concurrent > CEILING_MAX_CONCURRENT:
                _reject_risk(
                    f"book {book_id} max_concurrent {concurrent_raw} exceeds ceiling {CEILING_MAX_CONCURRENT}"
                )
        top = item.get("top_frac")
        top_frac = None if top is None else float(top)
        if top_frac is not None and not 0 < top_frac < 1:
            raise SystemExit(f"book {item.get('id')} top_frac must be between 0 and 1")
        if kind == "swing":
            default_model = "swing" if top_frac is not None else "none"
        else:
            default_model = "entry"
        model_key = str(item.get("model") or default_model)
        if kind == "swing":
            if model_key not in ("swing", "none"):
                raise SystemExit(f"book {item.get('id')} swing model must be swing or none")
            if not item.get("point"):
                raise SystemExit(f"book {item.get('id')} swing book needs a point")
        elif model_key not in ("entry", "barrier"):
            raise SystemExit(f"book {item.get('id')} model must be entry or barrier")
        entry_keys = ("entry_model", "entry_model_md5", "entry_threshold", "entry_features")
        entry_cfg: dict[str, Any] = {}
        if any(item.get(k) is not None for k in entry_keys):
            if kind != "migrate":
                _reject_risk(f"book {book_id} entry_model is for migrate books only")
            missing = [k for k in entry_keys if item.get(k) is None]
            if missing:
                _reject_risk(f"book {book_id} entry gate needs {', '.join(missing)}")
            thr = _finite(item.get("entry_threshold"))
            if thr is None:
                _reject_risk(f"book {book_id} entry_threshold is not a finite number")
            try:
                load_gate(str(item["entry_model"]), str(item["entry_model_md5"]), float(thr), str(item["entry_features"]))
            except GateConfigError as exc:
                _reject_risk(f"book {book_id}: {exc}")
            entry_cfg = {
                "entry_model": str(item["entry_model"]),
                "entry_model_md5": str(item["entry_model_md5"]).strip().lower(),
                "entry_threshold": float(thr),
                "entry_features": str(item["entry_features"]),
            }
        found.append(
            BookSpec(
                **entry_cfg,
                book_id=str(item["id"]),
                kind=kind,
                exit_rule=str(item.get("exit") or ("hold_30s" if kind == "baseline" else "tp50_sl30" if kind == "migrate" else "deploy")),
                threshold=_finite(item.get("threshold")),
                top_frac=top_frac,
                point=None if item.get("point") is None else str(item.get("point")),
                model_key=model_key,
                max_concurrent=concurrent,
                daily_loss_lamports=loss_lamports,
                creator_cooldown_ms=int(float(item.get("creator_cooldown_s", 0 if kind == "baseline" else 60)) * 1000),
                token_cooldown_ms=int(float(item.get("token_cooldown_s", 0 if kind == "baseline" else 300)) * 1000),
                size_lamports=size,
                freeze_ms=freeze_ms if kind == "swing" else None,
            )
        )
    if not found:
        raise SystemExit("config has no books")
    return found


def _point_matches(spec: BookSpec, book: MintBook, t_ms: int, trigger: str) -> bool:
    """A blank point listens to every clock. "30" is the T+30s grid. Other values are trigger names."""
    point = spec.point
    if not point:
        return True
    if point == trigger:
        return True
    if point == "attn" and trigger.startswith("attn:"):
        return True
    if trigger == "grid" and point.isdigit():
        return t_ms - book.create.t_signal_ms == int(point) * 1000
    return False


@dataclass
class _Track:
    buyers: set[str] = field(default_factory=set)
    buyers_done: bool = False
    migrate_done: bool = False
    baseline_done: bool = False
    clean_buyers: set[str] = field(default_factory=set)
    nv_fired: set[int] = field(default_factory=set)
    curve_crossed: set[int] = field(default_factory=set)
    migration_t_ms: int | None = None
    attn_fired: set[str] = field(default_factory=set)


# Same object the offline scoreboard walks. Do not keep a second copy.
_RankWindow = RankWindow


@dataclass
class _Pending:
    book_id: str
    mint: str
    creator: str | None
    t_entry_ms: int
    decision_t_ms: int
    trigger: str
    rule: ExitRule | LadderRule
    size_lamports: int
    latency_ms: int
    score: float | None
    ref_price: float | None
    hops: dict[str, Any]
    ledger: str = "ceiling"


@dataclass
class _Open:
    book_id: str
    mint: str
    creator: str | None
    rule: ExitRule | LadderRule
    entry_status: str
    entry: Any
    size_lamports: int
    latency_ms: int
    decision_t_ms: int
    trigger: str
    score: float | None
    hops: dict[str, Any]
    # Slippage reference from the decision. The direct-route column re-quotes
    # with it. The booked fill does not read it again.
    ref_price: float | None = None
    # Exit-plan cursor. Not a fill input. Lets a repeat check skip the print walk.
    exit_scan: Any = None
    ladder_key: tuple[int, int] | None = None
    ladder_plan: list[tuple[int, int]] | None = None


@dataclass
class _Ledger:
    """One paper book. `ceiling` is the execution path. `shadow` is uncapped scoring."""

    pending: dict[str, _Pending] = field(default_factory=dict)
    open: dict[str, _Open] = field(default_factory=dict)
    day: str = ""
    day_pnl: int = 0
    realized: list[int] = field(default_factory=list)
    realized_decision_t_ms: list[int] = field(default_factory=list)
    token_ready: dict[str, int] = field(default_factory=dict)
    creator_ready: dict[str, int] = field(default_factory=dict)
    closed_n: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class _BookRun:
    spec: BookSpec
    ceiling: _Ledger = field(default_factory=_Ledger)
    shadow: _Ledger = field(default_factory=_Ledger)
    rank: _RankWindow | None = None

    # The names below are the ceilinged execution ledger. Tests and the risk
    # gate read them. Shadow never writes through these.
    @property
    def pending(self) -> dict[str, _Pending]:
        return self.ceiling.pending

    @pending.setter
    def pending(self, value: dict[str, _Pending]) -> None:
        self.ceiling.pending = value

    @property
    def open(self) -> dict[str, _Open]:
        return self.ceiling.open

    @open.setter
    def open(self, value: dict[str, _Open]) -> None:
        self.ceiling.open = value

    @property
    def day(self) -> str:
        return self.ceiling.day

    @day.setter
    def day(self, value: str) -> None:
        self.ceiling.day = value

    @property
    def day_pnl(self) -> int:
        return self.ceiling.day_pnl

    @day_pnl.setter
    def day_pnl(self, value: int) -> None:
        self.ceiling.day_pnl = value

    @property
    def realized(self) -> list[int]:
        return self.ceiling.realized

    @realized.setter
    def realized(self, value: list[int]) -> None:
        self.ceiling.realized = value

    @property
    def token_ready(self) -> dict[str, int]:
        return self.ceiling.token_ready

    @property
    def creator_ready(self) -> dict[str, int]:
        return self.ceiling.creator_ready

    @property
    def closed_n(self) -> int:
        return self.ceiling.closed_n

    @closed_n.setter
    def closed_n(self, value: int) -> None:
        self.ceiling.closed_n = value

    @property
    def skip_reasons(self) -> dict[str, int]:
        return self.ceiling.skip_reasons


class ModelSlot:
    """Reload the daily LAYA file when its mtime changes."""

    def __init__(self, model_path: Path | None, meta_path: Path | None) -> None:
        self.model_path = model_path
        self.meta_path = meta_path
        self.booster: Booster | None = None
        self.rule_id = "hold_30s"
        self.mtime: float | None = None
        self.error: str | None = None
        self.loads = 0

    def maybe_reload(self, *, force: bool = False) -> None:
        path = self.model_path
        if path is None or not path.is_file():
            self.booster = None
            self.error = "no_model"
            return
        mtime = path.stat().st_mtime
        if not force and self.booster is not None and mtime == self.mtime:
            return
        try:
            self.booster = Booster.load(path)
            self.mtime = mtime
            self.error = None
            self.loads += 1
        except (OSError, ValueError, ImportError) as exc:
            self.booster = None
            self.error = type(exc).__name__
            return
        self._read_rule()

    def _read_rule(self) -> None:
        meta = self.meta_path
        if meta is None or not meta.is_file():
            return
        try:
            board = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        rule = (((board.get("entry") or {}).get("deploy") or {}).get("rule_id"))
        if isinstance(rule, str) and rule in RULES:
            self.rule_id = rule

    def score(self, feats: dict[str, float]) -> float | None:
        if self.booster is None:
            return None
        names = self.booster.names or list(FEATURE_NAMES)
        return self.booster.predict_one(vector(feats, names))


class JsonlLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._fh: TextIO = path.open("a", encoding="utf-8")

    def write(self, row: dict[str, Any]) -> None:
        self._fh.write(json.dumps(_json_safe(row), separators=(",", ":"), ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class ForwardEngine:
    """Incremental tape → packets → paper books.

    `record_packets` keeps every LAYA decision packet so a replay can be
    compared to `build_feature_rows`.
    """

    def __init__(
        self,
        books: Sequence[BookSpec],
        *,
        kill_file: Path,
        latency: LatencyMeter | None = None,
        model: ModelSlot | None = None,
        barrier: ModelSlot | None = None,
        swing: ModelSlot | None = None,
        offsets_ms: Sequence[int] = DECISION_OFFSETS_MS,
        slippage_cap: float = DEFAULT_SLIPPAGE_CAP,
        tape_end_ms: int | None = None,
        record_packets: bool = False,
        retain_rows: bool = False,
        logs: dict[str, JsonlLog] | None = None,
        fail_rate: float = 0.0,
        promotion_live_ms: int | None = None,
        positions_path: Path | None = None,
        dead_mints: frozenset[str] | None = None,
        early_timeout_ms: int | None = None,
        tx_order_prune_ms: int | None = None,
    ) -> None:
        self.books = []
        for spec in books:
            run = _BookRun(spec)
            if spec.top_frac is not None:
                run.rank = _RankWindow(spec.top_frac)
            self.books.append(run)
        self.kill_file = kill_file
        # EXP-012 gate: built only when a book asks for it. None keeps every other
        # path byte-identical (no hook below runs).
        self.exp012: Exp012Online | None = None
        self.exp012_gates: dict[str, GateSpec] = {}
        self.exp012_rows: list[dict[str, Any]] = []
        for run in self.books:
            sp = run.spec
            if sp.entry_model is not None:
                try:
                    self.exp012_gates[sp.book_id] = load_gate(
                        sp.entry_model, sp.entry_model_md5 or "", float(sp.entry_threshold), sp.entry_features or ""
                    )
                except GateConfigError as exc:
                    raise SystemExit(f"book {sp.book_id}: {exc}") from exc
        if self.exp012_gates:
            self.exp012 = Exp012Online()
        self.latency = latency or LatencyMeter()
        self.model = model or ModelSlot(None, None)
        self.barrier = barrier or ModelSlot(None, None)
        self.swing = swing or ModelSlot(None, None)
        self.offsets_ms = tuple(offsets_ms)
        self.slippage_cap = slippage_cap
        self.tape_end_ms = tape_end_ms
        # Packets are scored on the fly. Only a replay keeps the rows in RAM.
        self.record_packets = record_packets
        self.retain_rows = retain_rows
        self.logs = logs or {}
        # Live serve passes the flat 15% promotion rate. Replay stays at 0 so
        # packet parity is not a coin flip. Config cannot lower the live rate.
        self.fail_rate = float(fail_rate)
        self.fail_rng = random.Random()
        self.promotion_live_ms = promotion_live_ms
        # Durable ledger. summary() scores this file for promotion and does not rewrite it.
        self.positions_path = positions_path
        self.stale_dropped = 0
        self.newest_recv_ms: int | None = None
        self._tx_order = TxOrder()
        # Opt-in, `serve()`-only (see `TX_ORDER_PRUNE_MS`'s comment). `None`
        # for every other caller -- `replay_rows()`/ParityTests/promotion
        # backtests keep today's unbounded, full-history `TxOrder` behavior.
        self.tx_order_prune_ms = tx_order_prune_ms
        self._tx_order_pruned_at_ms: int | None = None
        self.wallets = WalletState()
        self.graph = None
        self.graph_dir: Path | None = None
        self._graph_checked_s = 0.0
        self._graph_stamp: tuple[int, int] | None = None
        self.by_creator: dict[str, list[MintBook]] = defaultdict(list)
        self.library: dict[str, MintBook] = {}
        self.tracks: dict[str, _Track] = {}
        self.mint_order: dict[str, int] = {}
        self.seen: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
        self.grids: list[tuple[int, int, str]] = []
        self.grid_seq = 0
        self.emitted_grids: set[tuple[str, int]] = set()
        self.mig15: list[tuple[int, int, str]] = []
        self.mig15_seq = 0
        self.mig15_waiting: set[str] = set()
        self.attention: dict[str, list[AttentionEvent]] = defaultdict(list)
        self.attn_t_start_ms = 0
        self.attn_snapshot: set[tuple[str, str]] = set()
        self.early: dict[str, list[tuple[FlowPrint, int | None]]] = defaultdict(list)
        # Mints a create can structurally never arrive for this run: the
        # pre-boot set `_preboot_dead_mints` computes at startup (rule A),
        # plus anything `_prune_early` later gives up on via `early_timeout_ms`
        # (rule B). A mutable set, not a frozenset: rule B adds to it live.
        # Empty for every caller except `serve()` -- `replay_rows()`/offline
        # tools never pass `dead_mints`/`early_timeout_ms`, so their
        # full-history semantics are unchanged. A hit here means `_add_print`
        # drops the print instead of buffering it in `self.early`.
        self.dead_mints: set[str] = set(dead_mints or ())
        self.dead_prints_dropped = 0
        # Rule B's threshold (see EARLY_BUFFER_DEAD_MS's comment). None
        # disables it entirely (every non-serve() caller).
        self.early_timeout_ms = early_timeout_ms
        self.early_timeout_mints_dropped = 0
        self.inbox: list[tuple[int, int, int, Any]] = []
        self.inbox_seq = 0
        self.packets: list[tuple[str, int, str, dict[str, float]]] = []
        self.positions: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self._triggers: list[tuple[str, int, str]] = []
        self._clock_ms = 0
        self._prints = 0

    def push_create(self, create: CreateSignal) -> None:
        """Register the mint immediately so earlier prints are not dropped.

        The creator note itself stays on the tape clock, matching the batch
        scorer (a print before t_signal is observed before the creator flag).
        """
        self._register_create(create)

    def _register_create(self, create: CreateSignal) -> None:
        # `self.library` is never evicted (funding_graph.fill_funding_features
        # and laya_v0.creator_features walk it cross-mint), so this dedupe
        # check alone still catches a duplicate create replay after a restart.
        if create.mint in self.library:
            return
        if self.tape_end_ms is not None and create.t_signal_ms > self.tape_end_ms:
            return
        book = MintBook(create=create, flow=[])
        self.library[create.mint] = book
        if self.exp012 is not None:
            vs, vt = create.v_sol, create.v_token_ui
            self.exp012.note_create(
                create.mint, create.creator, create.t_signal_ms, vs / vt if vs and vt and vs > 0 and vt > 0 else None
            )
        self.tracks[create.mint] = _Track()
        self.mint_order[create.mint] = len(self.mint_order)
        if create.creator:
            lst = self.by_creator[create.creator]
            key = (create.t_signal_ms, create.mint)
            idx = 0
            while idx < len(lst) and (lst[idx].create.t_signal_ms, lst[idx].create.mint) < key:
                idx += 1
            lst.insert(idx, book)
        t0 = create.t_signal_ms
        for off in self.offsets_ms:
            when = t0 + off
            if self.tape_end_ms is not None and when > self.tape_end_ms:
                continue
            heapq.heappush(self.grids, (when, self.grid_seq, create.mint))
            self.grid_seq += 1
        self._push(t0, -1, ("creator", create))
        self._flush_early(create.mint)

    def push_print(self, mint: str, pr: FlowPrint, event_ts: int | None = None) -> None:
        pr = self._tx_order.stamp(pr)
        self._push(pr.t_recv_ms, 0, ("print", mint, pr, event_ts))

    def push_attention(self, row: dict[str, Any]) -> None:
        """Genuine first-seen only. Pre-migration rows stay buffered until graduation."""
        ev = self._attention_event(row)
        if ev is None:
            return
        if self.tape_end_ms is not None and ev.t_ms > self.tape_end_ms:
            return
        self._push(ev.t_ms, 1, ("attn", ev))

    def _attention_event(self, row: dict[str, Any]) -> AttentionEvent | None:
        mint = row.get("mint")
        kind = row.get("kind")
        t_ms = row.get("t_first_ms")
        if not isinstance(mint, str) or not isinstance(kind, str) or not isinstance(t_ms, int):
            return None
        if not is_genuine_arrival(row, t_start_ms=self.attn_t_start_ms, snapshot_keys=self.attn_snapshot):
            return None
        rank = row.get("rank")
        rank_i = int(rank) if isinstance(rank, int) and not isinstance(rank, bool) else None
        return AttentionEvent(mint=mint, kind=kind, t_ms=t_ms, rank=rank_i, genuine=True)

    def _push(self, t_ms: int, kind: int, payload: Any) -> None:
        heapq.heappush(self.inbox, (t_ms, kind, self.inbox_seq, payload))
        self.inbox_seq += 1

    def flush(self) -> None:
        """Apply events still held back once the tape goes quiet."""
        if not self.inbox:
            return
        watermark = max(item[0] for item in self.inbox)
        self.drain_until(watermark, final=True)

    def drain_until(self, watermark_ms: int, *, final: bool = False) -> None:
        ready: list[tuple[int, int, int, Any]] = []
        while self.inbox:
            t_ms = self.inbox[0][0]
            if final:
                if t_ms > watermark_ms:
                    break
            elif t_ms >= watermark_ms:
                break
            ready.append(heapq.heappop(self.inbox))
        idx = 0
        while idx < len(ready):
            t_ms = ready[idx][0]
            self._before_time(t_ms)
            batch: list[tuple[int, int, int, Any]] = []
            while idx < len(ready) and ready[idx][0] == t_ms:
                batch.append(ready[idx])
                idx += 1
            self._apply_batch(t_ms, batch)
            self._at_time(t_ms)
        if final:
            self._before_time(watermark_ms + 1)
            self._at_time(watermark_ms)

    def _before_time(self, t_ms: int) -> None:
        self._emit_grids_through(t_ms - 1)
        self._emit_mig15_through(t_ms - 1)
        self._fill_through(t_ms - 1)
        self._exits_through(t_ms - 1)

    def _at_time(self, t_ms: int) -> None:
        self._clock_ms = t_ms
        self._emit_grids_through(t_ms)
        self._emit_mig15_through(t_ms)
        queued = self._triggers
        self._triggers = []
        for mint, when, trigger in queued:
            if when <= t_ms:
                self._on_signal(mint, when, trigger)
        self._fill_through(t_ms)
        self._exits_through(t_ms)
        every = 5_000 if self.latency.now_ms is not None else 50_000
        if self._prints and self._prints % every == 0:
            self._prune(t_ms)

    def _apply_batch(self, t_ms: int, batch: list[tuple[int, int, int, Any]]) -> None:
        creators = [item for item in batch if item[3][0] == "creator"]
        prints = [item for item in batch if item[3][0] == "print"]
        attentions = [item for item in batch if item[3][0] == "attn"]
        for item in creators:
            create = item[3][1]
            self._flush_early(create.mint)
            self.wallets.note_creator(create.creator)
            book = self.library.get(create.mint)
            if book is None:
                continue
            anchor = book.path.anchor()
            if anchor is not None and anchor.t_recv_ms <= create.t_signal_ms:
                self._note_curve(create.mint, create.t_signal_ms, anchor.venue, anchor.base_reserve)
        prints.sort(key=lambda item: self._print_sort_key(item[3][1], item[3][2]))
        for item in prints:
            _tag, mint, pr, event_ts = item[3]
            if self._add_print(mint, pr, event_ts):
                self._consider_triggers(mint, pr)
        for item in attentions:
            self._note_attention(item[3][1])
        for item in creators:
            create = item[3][1]
            if create.mint in self.library and create.t_signal_ms == t_ms:
                self._baseline(create.mint, t_ms)

    def _print_sort_key(self, mint: str, pr: FlowPrint) -> tuple[Any, ...]:
        return (self.mint_order.get(mint, 10**9), pr.slot, pr.tx_index, pr.event_index, pr.trader or "", pr.side)

    def _flush_early(self, mint: str) -> None:
        buffered = self.early.pop(mint, [])
        for pr, event_ts in buffered:
            if self._add_print(mint, pr, event_ts):
                self._consider_triggers(mint, pr)

    def _add_print(self, mint: str, pr: FlowPrint, event_ts: int | None = None) -> bool:
        book = self.library.get(mint)
        if book is None:
            if mint in self.dead_mints:
                self.dead_prints_dropped += 1
                return False
            self.early[mint].append((pr, event_ts))
            return False
        if self.tape_end_ms is not None and pr.t_recv_ms > self.tape_end_ms:
            return False
        key = _dedupe_key(pr)
        if key in self.seen[mint]:
            return False
        self.seen[mint].add(key)
        self._insert_print(book, pr)
        if self.exp012 is not None:
            self.exp012.note_print(
                mint, venue=pr.venue, t_ms=pr.t_recv_ms, side=pr.side, trader=pr.trader,
                sol_lamports=pr.sol_lamports, token_raw=pr.token_raw, price_sol=pr.price_sol,
            )
        self.wallets.observe_print(mint, pr)
        self.latency.note_print(pr.t_recv_ms, event_ts)
        self._prints += 1
        return True

    def _insert_print(self, book: MintBook, pr: FlowPrint) -> None:
        flow = book.flow
        key = (pr.t_recv_ms, pr.slot, pr.tx_index, pr.event_index, pr.trader or "", pr.side)
        if not flow:
            book.flow.append(pr)
            book.path.prints.append(pr.to_tape())
            return
        last = flow[-1]
        last_key = (last.t_recv_ms, last.slot, last.tx_index, last.event_index, last.trader or "", last.side)
        if key >= last_key:
            book.flow.append(pr)
            book.path.prints.append(pr.to_tape())
            return
        lo, hi = 0, len(flow)
        while lo < hi:
            mid = (lo + hi) // 2
            cur = flow[mid]
            cur_key = (cur.t_recv_ms, cur.slot, cur.tx_index, cur.event_index, cur.trader or "", cur.side)
            if cur_key < key:
                lo = mid + 1
            else:
                hi = mid
        flow.insert(lo, pr)
        book.path.prints.insert(lo, pr.to_tape())

    def _consider_triggers(self, mint: str, pr: FlowPrint) -> None:
        book = self.library[mint]
        track = self.tracks[mint]
        t0 = book.create.t_signal_ms
        if pr.t_recv_ms >= t0:
            self._note_curve(mint, pr.t_recv_ms, pr.venue, pr.base_reserve)
        if not track.migrate_done and pr.venue == "pumpswap" and pr.t_recv_ms >= t0:
            track.migrate_done = True
            track.migration_t_ms = pr.t_recv_ms
            if self.exp012 is not None:
                self.exp012.mark_migrated(mint)
            self._triggers.append((mint, pr.t_recv_ms, "migrate"))
            self._schedule_mig15(mint, pr.t_recv_ms)
            self._flush_attention(mint)
        self._note_clean_buyer(mint, pr)
        self._note_buyers_8(book, track, pr, t0)

    def _note_curve(self, mint: str, t_ms: int, venue: str, base: int) -> None:
        track = self.tracks.get(mint)
        if track is None:
            return
        progress = _curve_progress(venue, base)
        if progress != progress:
            return
        for level in CURVE_LEVELS:
            mark = int(round(level * 100))
            if mark in track.curve_crossed or progress < level:
                continue
            track.curve_crossed.add(mark)
            self._triggers.append((mint, t_ms, f"curve_{mark}"))

    def _note_clean_buyer(self, mint: str, pr: FlowPrint) -> None:
        """Same veto as causal_buyer_triggers, read from the shared wallet state after this print."""
        if pr.side != "buy" or not pr.trader:
            return
        track = self.tracks[mint]
        wallets = self.wallets
        if pr.trader in wallets.bots or pr.trader in wallets.snipers or pr.trader in wallets.creators:
            return
        if pr.trader in track.clean_buyers:
            return
        track.clean_buyers.add(pr.trader)
        n_clean = len(track.clean_buyers)
        for level in BUYER_TRIGGER_NS:
            if level in track.nv_fired or n_clean < level:
                continue
            track.nv_fired.add(level)
            self._triggers.append((mint, pr.t_recv_ms, f"buyers_nv{level}"))

    def _note_buyers_8(self, book: MintBook, track: _Track, pr: FlowPrint, t0: int) -> None:
        if track.buyers_done or pr.side != "buy" or not pr.trader:
            return
        track.buyers.add(pr.trader)
        if len(track.buyers) < TRADE_TRIGGER_BUYERS:
            return
        t_ms = pr.t_recv_ms
        if t_ms < t0 + self.offsets_ms[0]:
            return
        if t_ms > t0 + self.offsets_ms[-1]:
            track.buyers_done = True
            return
        grid = []
        for off in self.offsets_ms:
            g = t0 + off
            if self.tape_end_ms is None or g <= self.tape_end_ms:
                grid.append(g)
        track.buyers_done = True
        if all(abs(t_ms - g) > TRADE_TRIGGER_NEAR_MS for g in grid):
            self._triggers.append((book.create.mint, t_ms, "buyers_8"))

    def _emit_grids_through(self, t_ms: int) -> None:
        while self.grids and self.grids[0][0] <= t_ms:
            when, _seq, mint = heapq.heappop(self.grids)
            if (mint, when) in self.emitted_grids:
                continue
            self.emitted_grids.add((mint, when))
            self._on_signal(mint, when, "grid")

    def _schedule_mig15(self, mint: str, migration_t_ms: int) -> None:
        if not any(run.spec.kind == "swing" and run.spec.point == "mig_15" for run in self.books):
            return
        when = migration_t_ms + MIG15_OFFSET_MS
        if self.tape_end_ms is not None and when > self.tape_end_ms:
            return
        heapq.heappush(self.mig15, (when, self.mig15_seq, mint))
        self.mig15_seq += 1
        self.mig15_waiting.add(mint)

    def _emit_mig15_through(self, t_ms: int) -> None:
        while self.mig15 and self.mig15[0][0] <= t_ms:
            when, _seq, mint = heapq.heappop(self.mig15)
            self.mig15_waiting.discard(mint)
            self._on_signal(mint, when, "mig_15")

    def _note_attention(self, ev: AttentionEvent) -> None:
        prior = self.attention[ev.mint]
        if any(old.kind == ev.kind for old in prior):
            return
        prior.append(ev)
        track = self.tracks.get(ev.mint)
        if track is None or track.migration_t_ms is None:
            return
        self._fire_attention(ev.mint, ev, track)

    def _flush_attention(self, mint: str) -> None:
        track = self.tracks.get(mint)
        if track is None or track.migration_t_ms is None:
            return
        for ev in self.attention.get(mint, ()):
            self._fire_attention(mint, ev, track)

    def _fire_attention(self, mint: str, ev: AttentionEvent, track: _Track) -> None:
        if track.migration_t_ms is None or ev.t_ms < track.migration_t_ms:
            return
        if ev.kind in track.attn_fired:
            return
        track.attn_fired.add(ev.kind)
        self._triggers.append((mint, ev.t_ms, f"attn:{ev.kind}"))

    def _swing_features(self, book: MintBook, t_ms: int, trigger: str) -> dict[str, float]:
        track = self.tracks.get(book.create.mint)
        migration_t = track.migration_t_ms if track and track.migration_t_ms is not None else t_ms
        price, _quote = _migration_price(book, migration_t)
        events = self.attention.get(book.create.mint, ())
        feats = features_at(
            book,
            decision_t_ms=t_ms,
            migration_t_ms=migration_t,
            migration_price=price,
            attention=events,
            is_attention=trigger.startswith("attn:"),
        )
        self._maybe_reload_graph()
        fill_funding_features(book, t_ms, feats, self.wallets, self.graph, self.library)
        return feats

    def _baseline(self, mint: str, t_ms: int) -> None:
        track = self.tracks.get(mint)
        book = self.library.get(mint)
        if track is None or book is None or track.baseline_done:
            return
        if book.create.t_signal_ms != t_ms:
            return
        track.baseline_done = True
        for run in self.books:
            if run.spec.kind == "baseline":
                self._enter_or_skip(run, book, t_ms, "create", feats=None)

    def _maybe_reload_graph(self) -> None:
        """Reload append-only funding rows at most every few seconds."""
        if self.graph_dir is None:
            return
        now = time.monotonic()
        if self.graph is not None and now - self._graph_checked_s < 5.0:
            return
        self._graph_checked_s = now
        files = sorted(self.graph_dir.glob("funding-*.jsonl"))
        if not files:
            stamp = (0, 0)
        else:
            try:
                st = files[-1].stat()
                stamp = (int(st.st_mtime_ns), int(st.st_size))
            except OSError:
                stamp = (0, 0)
        if stamp == self._graph_stamp:
            return
        from tools.funding_graph import FundingGraph

        self.graph = FundingGraph.load(self.graph_dir)
        self._graph_stamp = stamp

    def _on_signal(self, mint: str, t_ms: int, trigger: str) -> None:
        book = self.library.get(mint)
        if book is None:
            return
        want_score = self.record_packets or any(
            run.spec.kind == "laya" and _point_matches(run.spec, book, t_ms, trigger) for run in self.books
        )
        feats = None
        if want_score or self.record_packets:
            self._maybe_reload_graph()
            feats = packet_at(
                book, t_ms, trigger, self.by_creator, self.wallets, graph=self.graph, library=self.library
            )
            if self.record_packets and self.retain_rows:
                self.packets.append((mint, t_ms, trigger, dict(feats)))
        gate_feats: dict[str, float] | None = None
        gate_reason: str | None = None
        if trigger == "migrate" and self.exp012 is not None:
            gate_feats, gate_reason = self.exp012.features_at(mint, t_ms)
        for run in self.books:
            if not _point_matches(run.spec, book, t_ms, trigger):
                continue
            if run.spec.kind == "laya":
                self._enter_or_skip(run, book, t_ms, trigger, feats)
            elif run.spec.kind == "migrate" and trigger == "migrate":
                gate_score: float | None = None
                if run.spec.book_id in self.exp012_gates:
                    passed, gate_score = self._exp012_pass(run, book, t_ms, trigger, gate_feats, gate_reason)
                    if not passed:
                        continue
                self._enter_or_skip(run, book, t_ms, trigger, feats, gate_score=gate_score)
            elif run.spec.kind == "swing":
                self._enter_or_skip(run, book, t_ms, trigger, feats)
        if trigger == "migrate" and self.exp012 is not None:
            self.exp012.drop(mint)

    def _exp012_pass(
        self,
        run: _BookRun,
        book: MintBook,
        t_ms: int,
        trigger: str,
        feats: dict[str, float] | None,
        reason: str | None,
    ) -> tuple[bool, float | None]:
        """EXP-012 model gate. Emits one gate row per (book, mint). True = go on to the
        normal path (risk gate, fill, exits unchanged). False = a skip was recorded.
        `entered` in the gate row means the model gate passed; a risk cap can still skip."""
        gate = self.exp012_gates[run.spec.book_id]
        mint = book.create.mint
        score: float | None = None
        passed = False
        if feats is not None:
            score = gate.score(feats)
            passed = score >= gate.threshold
            if not passed:
                reason = "below_threshold"
        row = gate_row(run.spec.book_id, mint, t_ms, gate, feats, score, passed, reason)
        if self.retain_rows:
            self.exp012_rows.append(row)
        log = self.logs.get("exp012_gate")
        if log is not None:
            log.write(row)
        if not passed:
            self._decision(run, book, t_ms, trigger, "skip", reason, score, None)
        return passed, score

    def _enter_or_skip(
        self,
        run: _BookRun,
        book: MintBook,
        t_ms: int,
        trigger: str,
        feats: dict[str, float] | None,
        gate_score: float | None = None,
    ) -> None:
        spec = run.spec
        mint = book.create.mint
        creator = book.create.creator
        score = gate_score
        if spec.kind == "swing":
            freeze = spec.freeze_ms if spec.freeze_ms is not None else SWING_FREEZE_MS
            if t_ms <= freeze:
                self._decision(run, book, t_ms, trigger, "skip", "before_freeze", None, None)
                return
            if spec.model_key == "swing":
                feats = self._swing_features(book, t_ms, trigger)
                self.swing.maybe_reload()
                if self.swing.booster is None:
                    self._decision(run, book, t_ms, trigger, "skip", "no_model", None, None)
                    return
                score = self.swing.score(feats)
                if score is None:
                    self._decision(run, book, t_ms, trigger, "skip", "no_score", None, None)
                    return
                if spec.top_frac is not None and run.rank is not None:
                    verdict = run.rank.consider(score)
                    if verdict != "take":
                        reason = "topk_warmup" if verdict == "warmup" else "below_top"
                        self._decision(run, book, t_ms, trigger, "skip", reason, score, None)
                        return
                elif spec.threshold is not None and score < spec.threshold:
                    self._decision(run, book, t_ms, trigger, "skip", "below_threshold", score, None)
                    return
        elif spec.kind == "laya":
            if feats is None:
                self._maybe_reload_graph()
                feats = packet_at(
                    book, t_ms, trigger, self.by_creator, self.wallets, graph=self.graph, library=self.library
                )
                if self.record_packets and self.retain_rows:
                    self.packets.append((mint, t_ms, trigger, dict(feats)))
            slot = self.barrier if spec.model_key == "barrier" else self.model
            slot.maybe_reload()
            if slot.booster is None:
                self._decision(run, book, t_ms, trigger, "skip", "no_model", None, None)
                return
            score = slot.score(feats)
            if score is None:
                self._decision(run, book, t_ms, trigger, "skip", "no_score", None, None)
                return
            if spec.top_frac is not None and run.rank is not None:
                verdict = run.rank.consider(score)
                if verdict != "take":
                    reason = "topk_warmup" if verdict == "warmup" else "below_top"
                    self._decision(run, book, t_ms, trigger, "skip", reason, score, None)
                    return
            elif spec.threshold is None or score < spec.threshold:
                self._decision(run, book, t_ms, trigger, "skip", "below_threshold", score, None)
                return
        # A decision that is already too old to act on is not a fill.
        lag = self._action_lag_ms(t_ms)
        if lag is not None and lag > STALE_ACTION_MS:
            self.stale_dropped += 1
            self._decision(run, book, t_ms, trigger, "skip", "stale_recv", score, None, ledger="ceiling")
            self._decision(run, book, t_ms, trigger, "skip", "stale_recv", score, None, ledger="shadow")
            return
        # Strategy said take. Ceiling still enforces the hard caps. Shadow fills
        # every such signal with no concurrent cap and no daily-loss halt.
        ceiling_reason = self._risk_reason(run, mint, creator, t_ms, spec.size_lamports, ledger=run.ceiling, capped=True)
        shadow_reason = self._risk_reason(run, mint, creator, t_ms, spec.size_lamports, ledger=run.shadow, capped=False)
        hops = None
        if ceiling_reason is None:
            hops = self.latency.measure(t_ms)
        elif shadow_reason is None:
            hops = self.latency.quote(t_ms)
        if ceiling_reason:
            self._decision(run, book, t_ms, trigger, "skip", ceiling_reason, score, None, ledger="ceiling")
        else:
            assert hops is not None
            self._queue(run, run.ceiling, book, t_ms, trigger, score, feats, hops)
        if shadow_reason:
            self._decision(run, book, t_ms, trigger, "skip", shadow_reason, score, None, ledger="shadow")
        else:
            assert hops is not None
            self._queue(run, run.shadow, book, t_ms, trigger, score, feats, hops)

    def _queue(
        self,
        run: _BookRun,
        ledger: _Ledger,
        book: MintBook,
        t_ms: int,
        trigger: str,
        score: float | None,
        feats: dict[str, float] | None,
        hops: dict[str, Any],
    ) -> None:
        spec = run.spec
        name = "shadow" if ledger is run.shadow else "ceiling"
        latency_ms = int(hops["applied_latency_ms"])
        pending = _Pending(
            book_id=spec.book_id,
            mint=book.create.mint,
            creator=book.create.creator,
            t_entry_ms=t_ms + latency_ms,
            decision_t_ms=t_ms,
            trigger=trigger,
            rule=spec.resolved_exit(self.model.rule_id),
            size_lamports=spec.size_lamports,
            latency_ms=latency_ms,
            score=score,
            ref_price=self._ref_price(book, t_ms, feats),
            hops=hops,
            ledger=name,
        )
        ledger.pending[pending.mint] = pending
        if pending.t_entry_ms <= self._clock_ms:
            self._fill_one(run, ledger, pending)

    def _ref_price(self, book: MintBook, t_ms: int, feats: dict[str, float] | None) -> float | None:
        if feats is not None:
            price = _finite(feats.get("f_price_sol"))
            if price is not None and price > 0:
                return price
        if book.create.t_signal_ms == t_ms or feats is None:
            spot = features_at_t(book.path).get("f_tape_last_price_sol")
            found = _finite(spot)
            if found is not None and found > 0:
                return found
        price = _price_at(book, t_ms)
        if price is not None and price > 0:
            return price
        return None

    def _risk_reason(
        self,
        run: _BookRun,
        mint: str,
        creator: str | None,
        t_ms: int,
        size: int,
        *,
        ledger: _Ledger | None = None,
        capped: bool = True,
    ) -> str | None:
        # Kill switch is not configurable. A loosened BookSpec still cannot trade past the ceilings.
        # `capped=False` is the shadow ledger: no concurrent cap and no daily-loss halt.
        # It does not raise position size, and it does not turn the kill switch off.
        book = run.ceiling if ledger is None else ledger
        if self.kill_file.is_file():
            return "kill_switch"
        spec = run.spec
        if size > HARD_MAX_POSITION_LAMPORTS:
            return "max_position_size"
        if mint in book.open or mint in book.pending:
            return "already_open"
        self._roll_day(book, t_ms)
        if capped:
            concurrent = CEILING_MAX_CONCURRENT if spec.max_concurrent is None else min(spec.max_concurrent, CEILING_MAX_CONCURRENT)
            if len(book.open) + len(book.pending) >= concurrent:
                return "max_concurrent"
            loss_cap = CEILING_DAILY_LOSS_LAMPORTS if spec.daily_loss_lamports is None else min(spec.daily_loss_lamports, CEILING_DAILY_LOSS_LAMPORTS)
            if book.day_pnl <= -loss_cap:
                return "daily_loss_cap"
        if t_ms < book.token_ready.get(mint, 0):
            return "token_cooldown"
        if creator and t_ms < book.creator_ready.get(creator, 0):
            return "creator_cooldown"
        return None

    def _action_lag_ms(self, t_ms: int) -> int | None:
        """Wall clock minus tape receive. Replay has no wall clock and is not stale."""
        if self.latency.now_ms is None:
            return None
        return self.latency.now_ms() - t_ms

    def _landing_failed(self) -> bool:
        """Flat landing failure. Misses that already failed are not rolled again."""
        if self.fail_rate <= 0:
            return False
        return self.fail_rng.random() < self.fail_rate

    def _promotion_valid(self, decision_t_ms: int) -> bool:
        return decision_counts_for_promotion(decision_t_ms, self.promotion_live_ms)

    def _roll_day(self, ledger: _Ledger, t_ms: int) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime(t_ms / 1000))
        if ledger.day != day:
            ledger.day = day
            ledger.day_pnl = 0
            ledger.realized = []
            ledger.realized_decision_t_ms = []

    def _decision(
        self,
        run: _BookRun,
        book: MintBook,
        t_ms: int,
        trigger: str,
        action: str,
        reason: str | None,
        score: float | None,
        hops: dict[str, Any] | None,
        *,
        entry_status: str | None = None,
        ledger: str | None = None,
    ) -> None:
        if reason:
            targets = (run.ceiling, run.shadow) if ledger is None else ((run.shadow if ledger == "shadow" else run.ceiling),)
            for target in targets:
                target.skip_reasons[reason] = target.skip_reasons.get(reason, 0) + 1
        row = {
            "schema": SCHEMA_DECISION,
            "book": run.spec.book_id,
            "ledger": ledger,
            "mint": book.create.mint,
            "creator": book.create.creator,
            "decision_t_ms": t_ms,
            "trigger": trigger,
            "action": action,
            "reason": reason,
            "score": score,
            "entry_status": entry_status,
            "latency": hops,
        }
        if run.spec.kind == "swing":
            freeze = run.spec.freeze_ms if run.spec.freeze_ms is not None else SWING_FREEZE_MS
            row["freeze_ms"] = freeze
            row["freeze_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(freeze / 1000.0))
        if self.retain_rows:
            self.decisions.append(row)
        log = self.logs.get("decisions")
        if log is not None:
            log.write(row)

    def _fill_through(self, t_ms: int) -> None:
        for run in self.books:
            for ledger in (run.ceiling, run.shadow):
                due = [p for p in ledger.pending.values() if p.t_entry_ms <= t_ms]
                for pending in due:
                    self._fill_one(run, ledger, pending)

    def _fill_one(self, run: _BookRun, ledger: _Ledger, pending: _Pending) -> None:
        if ledger.pending.get(pending.mint) is not pending:
            return
        book = self.library.get(pending.mint)
        if book is None:
            ledger.pending.pop(pending.mint, None)
            return
        if self.kill_file.is_file():
            ledger.pending.pop(pending.mint, None)
            self._decision(
                run, book, pending.decision_t_ms, pending.trigger, "skip", "kill_switch", pending.score, pending.hops, ledger=pending.ledger
            )
            return
        feats = {"f_tape_last_price_sol": pending.ref_price}
        entry = try_entry(
            book.path,
            t_entry_ms=pending.t_entry_ms,
            size_lamports=pending.size_lamports,
            slippage_cap=self.slippage_cap,
            feats=feats,
        )
        ledger.pending.pop(pending.mint, None)
        landing_fail = entry.status == "filled" and self._landing_failed()
        if entry.status != "filled" or landing_fail:
            # Same cost the scoreboard keeps inside n: an attempt that does not fill burns priority.
            # A curve fill still fails the flat 15% landing rate. That miss is not mixed twice.
            status = "missed_landing" if landing_fail else entry.status
            cost = -PRIORITY_FEE_LAMPORTS
            self._roll_day(ledger, pending.t_entry_ms)
            ledger.day_pnl += cost
            self._book_realized(ledger, pending.decision_t_ms, cost)
            ledger.closed_n += 1
            self._decision(
                run,
                book,
                pending.decision_t_ms,
                pending.trigger,
                "skip",
                status,
                pending.score,
                pending.hops,
                entry_status=status,
                ledger=pending.ledger,
            )
            miss_row = {
                "schema": SCHEMA_POSITION,
                "ledger": pending.ledger,
                "event": "miss",
                "book": pending.book_id,
                "mint": pending.mint,
                "creator": pending.creator,
                "trigger": pending.trigger,
                "decision_t_ms": pending.decision_t_ms,
                "t_entry_ms": pending.t_entry_ms,
                "entry_status": status,
                "promotion_valid": self._promotion_valid(pending.decision_t_ms),
                "pnl_lamports": cost,
                "attempt_cost_lamports": cost,
                "reason": "flat_15pct_landing" if landing_fail else "priority_fee_on_unfilled_attempt",
            }
            if landing_fail:
                # Reporting only: the curve fill the flat coin flip discarded,
                # with the same field names as an `open` row, so an offline tool
                # (tools/forward_paper_pressure_stamp.py) can simulate the exit
                # this attempt would have had and price it under the pressure
                # fail model. Nothing here feeds a decision or the RNG.
                miss_row.update(
                    {
                        "counterfactual_fill": True,
                        "applied_latency_ms": pending.latency_ms,
                        "size_lamports": pending.size_lamports,
                        "exit_rule": pending.rule.rule_id,
                        "entry_venue": entry.venue,
                        "entry_spot_sol": entry.spot_sol,
                        "entry_tokens_raw": entry.tokens_raw,
                        "entry_t_ms": entry.t_entry_ms,
                    }
                )
            self._stamp_migrate_sensitivity(run, miss_row)
            self._position(miss_row)
            return
        opened = _Open(
            book_id=pending.book_id,
            mint=pending.mint,
            creator=pending.creator,
            rule=pending.rule,
            entry_status=entry.status,
            entry=entry,
            size_lamports=pending.size_lamports,
            latency_ms=pending.latency_ms,
            decision_t_ms=pending.decision_t_ms,
            trigger=pending.trigger,
            score=pending.score,
            hops=pending.hops,
            ref_price=pending.ref_price,
        )
        ledger.open[pending.mint] = opened
        self._decision(
            run,
            book,
            pending.decision_t_ms,
            pending.trigger,
            "enter",
            None,
            pending.score,
            pending.hops,
            entry_status="filled",
            ledger=pending.ledger,
        )
        self._position(
            {
                "schema": SCHEMA_POSITION,
                "ledger": pending.ledger,
                "event": "open",
                "book": pending.book_id,
                "mint": pending.mint,
                "creator": pending.creator,
                "trigger": pending.trigger,
                "score": pending.score,
                "decision_t_ms": pending.decision_t_ms,
                "t_entry_ms": entry.t_entry_ms,
                "applied_latency_ms": pending.latency_ms,
                "latency": pending.hops,
                "size_lamports": pending.size_lamports,
                "exit_rule": pending.rule.rule_id,
                "entry_status": entry.status,
                "entry_venue": entry.venue,
                "entry_spot_sol": entry.spot_sol,
                "entry_tokens_raw": entry.tokens_raw,
                "promotion_valid": self._promotion_valid(pending.decision_t_ms),
            }
        )

    def _exits_through(self, t_ms: int) -> None:
        for run in self.books:
            for ledger in (run.ceiling, run.shadow):
                for mint in list(ledger.open):
                    self._try_exit(run, ledger, mint, t_ms)

    def _rule_part(self, opened: _Open, book: MintBook, t_ms: int) -> dict[str, Any]:
        """Censored checks reuse the exit plan. The close still uses `simulate_exit`."""
        entry = opened.entry
        rule = opened.rule
        if not isinstance(rule, ExitRule) or entry.status != "filled":
            return simulate_exit(
                book.path,
                entry,
                rule,
                latency_ms=opened.latency_ms,
                tape_end_ms=t_ms,
                size_lamports=opened.size_lamports,
            )
        plan, scan = _plan_exit_resume(book.path, entry, rule, opened.latency_ms, opened.exit_scan)
        opened.exit_scan = scan
        if plan[1] > t_ms:
            return {"exit_status": "censored"}
        return simulate_exit(
            book.path,
            entry,
            rule,
            latency_ms=opened.latency_ms,
            tape_end_ms=t_ms,
            size_lamports=opened.size_lamports,
        )

    def _ladder_part(self, opened: _Open, book: MintBook, t_ms: int) -> dict[str, Any]:
        entry = opened.entry
        rule = opened.rule
        assert isinstance(rule, LadderRule)
        if entry.status != "filled" or not entry.spot_sol or entry.spot_sol <= 0 or entry.tokens_raw <= 0:
            return simulate_ladder(
                book.path,
                entry,
                rule,
                latency_ms=opened.latency_ms,
                tape_end_ms=t_ms,
                size_lamports=opened.size_lamports,
            )
        prints = book.path.prints
        key = (id(prints), len(prints))
        if opened.ladder_key != key or opened.ladder_plan is None:
            opened.ladder_key = key
            opened.ladder_plan = _ladder_legs(
                prints,
                entry_t_ms=entry.t_entry_ms,
                entry_spot=float(entry.spot_sol),
                tokens=int(entry.tokens_raw),
                rule=rule,
            )
        if any(leg_t + opened.latency_ms > t_ms for leg_t, _tokens in opened.ladder_plan):
            return {"exit_status": "censored"}
        return simulate_ladder(
            book.path,
            entry,
            rule,
            latency_ms=opened.latency_ms,
            tape_end_ms=t_ms,
            size_lamports=opened.size_lamports,
        )

    def _try_exit(self, run: _BookRun, ledger: _Ledger, mint: str, t_ms: int) -> None:
        opened = ledger.open.get(mint)
        book = self.library.get(mint)
        if opened is None or book is None:
            return
        if isinstance(opened.rule, LadderRule):
            part = self._ladder_part(opened, book, t_ms)
        else:
            part = self._rule_part(opened, book, t_ms)
        if part["exit_status"] == "censored":
            return
        if part["exit_status"] not in ("realized", "no_exit_liquidity"):
            return
        ledger.open.pop(mint, None)
        pnl = part.get("pnl_lamports")
        self._roll_day(ledger, t_ms)
        if isinstance(pnl, int):
            ledger.day_pnl += pnl
            self._book_realized(ledger, opened.decision_t_ms, pnl)
        ledger.closed_n += 1
        ready = t_ms
        ledger.token_ready[mint] = ready + run.spec.token_cooldown_ms
        if opened.creator:
            ledger.creator_ready[opened.creator] = ready + run.spec.creator_cooldown_ms
        name = "shadow" if ledger is run.shadow else "ceiling"
        close_row = {
            "schema": SCHEMA_POSITION,
            "ledger": name,
            "event": "close",
            "book": opened.book_id,
            "mint": mint,
            "creator": opened.creator,
            "trigger": opened.trigger,
            "score": opened.score,
            "decision_t_ms": opened.decision_t_ms,
            "t_entry_ms": opened.entry.t_entry_ms,
            "applied_latency_ms": opened.latency_ms,
            "size_lamports": opened.size_lamports,
            "exit_rule": opened.rule.rule_id,
            "exit_status": part["exit_status"],
            "exit_t_ms": part.get("exit_t_ms"),
            "trigger_exit": part.get("trigger"),
            "pnl_lamports": pnl,
            "pnl_sol": None if pnl is None else pnl / LAMPORTS_PER_SOL,
            "promotion_valid": self._promotion_valid(opened.decision_t_ms),
        }
        self._stamp_migrate_sensitivity(run, close_row, opened=opened, book=book, t_ms=t_ms)
        self._position(close_row)

    def _stamp_migrate_sensitivity(
        self,
        run: _BookRun,
        row: dict[str, Any],
        *,
        opened: _Open | None = None,
        book: MintBook | None = None,
        t_ms: int | None = None,
    ) -> None:
        """Reporting columns. Does not change pnl_lamports, day_pnl, or the fill."""
        if run.spec.kind != "migrate":
            return
        pnl = row.get("pnl_lamports")
        if isinstance(pnl, bool) or not isinstance(pnl, int):
            return
        exit_status = row.get("exit_status")
        sides = priority_sides_for_event(
            str(row.get("event") or ""),
            exit_status if isinstance(exit_status, str) else None,
        )
        if sides is None:
            return
        portal = priority_grid(pnl, sides)
        direct: dict[str, int] | None = portal if row.get("event") == "miss" else None
        if row.get("event") == "close" and opened is not None and book is not None and t_ms is not None:
            direct = self._direct_priority_grid(opened, book, t_ms)
        row["fee_sensitivity"] = {"reporting_only": True, "portal": portal, "direct": direct}

    def _direct_priority_grid(self, opened: _Open, book: MintBook, t_ms: int) -> dict[str, int] | None:
        """Same decision, portal fee 0. Does not replace `opened.entry`."""
        if not isinstance(opened.rule, ExitRule):
            return None
        entry = try_entry(
            book.path,
            t_entry_ms=opened.entry.t_entry_ms,
            size_lamports=opened.size_lamports,
            slippage_cap=self.slippage_cap,
            feats={"f_tape_last_price_sol": opened.ref_price},
            portal_fee_ppm=0,
        )
        if entry.status != "filled":
            return priority_grid(-PRIORITY_FEE_LAMPORTS, 1)
        part = simulate_exit(
            book.path,
            entry,
            opened.rule,
            latency_ms=opened.latency_ms,
            tape_end_ms=t_ms,
            size_lamports=opened.size_lamports,
            portal_fee_ppm=0,
        )
        direct_pnl = part.get("pnl_lamports")
        status = part.get("exit_status")
        if status == "not_entered":
            sides = 1
        elif status in ("realized", "no_exit_liquidity"):
            sides = 2
        else:
            return None
        if isinstance(direct_pnl, bool) or not isinstance(direct_pnl, int):
            return None
        return priority_grid(direct_pnl, sides)

    def _book_realized(self, ledger: _Ledger, decision_t_ms: int, pnl: int) -> None:
        ledger.realized.append(pnl)
        ledger.realized_decision_t_ms.append(decision_t_ms)

    def _position(self, row: dict[str, Any]) -> None:
        if self.retain_rows:
            self.positions.append(row)
        log = self.logs.get("positions")
        if log is not None:
            log.write(row)

    def _prune_cooldowns(self, now_ms: int) -> None:
        """Drop cooldown timestamps once they can never gate another entry.

        `_risk_reason` only ever reads these as `t_ms < ready`, with `t_ms`
        the current decision clock. Event time is non-decreasing across the
        run, so once `ready <= now_ms` the comparison is False for every
        future call too -- removing the entry is exactly as if it were still
        there.
        """
        for run in self.books:
            for ledger in (run.ceiling, run.shadow):
                if ledger.token_ready:
                    for mint in [m for m, ready in ledger.token_ready.items() if ready <= now_ms]:
                        del ledger.token_ready[mint]
                if ledger.creator_ready:
                    for creator in [c for c, ready in ledger.creator_ready.items() if ready <= now_ms]:
                        del ledger.creator_ready[creator]

    def _prune_early(self, live_now: int) -> None:
        """Drop print buffers for a create that never arrived.

        These prints can never reach a book: `_flush_early` only ever runs
        from `_register_create`, and a mint with no create has no MintBook to
        score, hold, or exit. There is no decision to preserve here, only
        memory that a missing create row would otherwise hold forever.

        Two independent rules:

        - Idle eviction (always on, unchanged): a mint gone fully silent for
          `PRUNE_AFTER_MS` is evicted -- safe for any createless mint, but
          never fires for one that keeps trading.
        - Early-buffer timeout (`self.early_timeout_ms`, opt-in, `serve()`
          only -- see `EARLY_BUFFER_DEAD_MS`'s comment): once a mint's OLDEST
          buffered print is older than the timeout with still no create, give
          up outright -- added to `dead_mints` (so its future prints are
          dropped too, not just this buffer) instead of waiting for a full
          idle window that an actively-trading createless mint will never
          reach.
        """
        if not self.early:
            return
        for mint in [
            m
            for m, buffered in self.early.items()
            if buffered and live_now - max(pr.t_recv_ms for pr, _ in buffered) >= PRUNE_AFTER_MS
        ]:
            del self.early[mint]
        if self.early_timeout_ms is None or not self.early:
            return
        for mint in [
            m
            for m, buffered in self.early.items()
            if buffered and live_now - min(pr.t_recv_ms for pr, _ in buffered) >= self.early_timeout_ms
        ]:
            buffered = self.early.pop(mint)
            self.dead_mints.add(mint)
            self.dead_prints_dropped += len(buffered)
            self.early_timeout_mints_dropped += 1

    def _prune(self, now_ms: int) -> None:
        busy: set[str] = set()
        for run in self.books:
            for ledger in (run.ceiling, run.shadow):
                busy.update(ledger.open)
                busy.update(ledger.pending)
        self._prune_cooldowns(now_ms)
        if self.exp012 is not None:
            self.exp012.prune(now_ms)
        live_now = self.latency.now_ms() if self.latency.now_ms is not None else now_ms
        self._prune_early(live_now)
        if self.tx_order_prune_ms is not None:
            every = min(TX_ORDER_PRUNE_EVERY_MS, self.tx_order_prune_ms)
            last = self._tx_order_pruned_at_ms
            if last is None or live_now - last >= every:
                self._tx_order.prune_before_ms(live_now - self.tx_order_prune_ms)
                self._tx_order_pruned_at_ms = live_now
        for mint, book in list(self.library.items()):
            if mint in busy or mint in self.mig15_waiting:
                continue
            if self.latency.now_ms is not None and book.flow:
                cutoff = live_now - LIVE_IDLE_RETAIN_MS
                if book.flow[0].t_recv_ms < cutoff:
                    kept = [pr for pr in book.flow if pr.t_recv_ms >= cutoff]
                    if not kept:
                        kept = [book.flow[-1]]
                    book.flow = kept
                    book.path.prints = [pr.to_tape() for pr in kept]
                    self.seen[mint] = {_dedupe_key(pr) for pr in kept}
            last = book.flow[-1].t_recv_ms if book.flow else book.create.t_signal_ms
            if live_now - last < PRUNE_AFTER_MS:
                continue
            t0 = book.create.t_signal_ms
            horizon = t0 + 30_000
            keep: list[FlowPrint] = []
            last_t0 = None
            last_h = None
            for pr in book.flow:
                if pr.t_recv_ms <= t0:
                    last_t0 = pr
                if pr.t_recv_ms <= horizon:
                    last_h = pr
            last_any = book.flow[-1] if book.flow else None
            for pr in (last_t0, last_h, last_any):
                if pr is not None and pr not in keep:
                    keep.append(pr)
            book.flow = keep
            book.path.prints = [pr.to_tape() for pr in keep]
            self.seen[mint] = {_dedupe_key(pr) for pr in keep}

    def summary(self, t_ms: int | None = None) -> dict[str, Any]:
        when = self._clock_ms if t_ms is None else t_ms
        day = time.strftime("%Y-%m-%d", time.gmtime(when / 1000)) if when else ""
        from_file: dict[str, list[int]] | None = None
        path = self.positions_path
        if path is not None and path.is_file():
            from_file = promotion_pnls_by_book(iter_position_rows(path), self.promotion_live_ms)
        migrate_ids = [run.spec.book_id for run in self.books if run.spec.kind == "migrate"]
        if path is not None and path.is_file():
            sens_rows: Iterable[dict[str, Any]] = iter_position_rows(path)
        elif self.retain_rows:
            sens_rows = self.positions
        else:
            sens_rows = ()
        sens_by_book = migrate_fee_sensitivity_summary(sens_rows, self.promotion_live_ms, migrate_ids)
        books = {}
        for run in self.books:
            ceiling = _ledger_view(run.ceiling, day)
            if from_file is not None:
                shadow = _ledger_view(run.shadow, day)
                shadow["realized"] = _stats_from_lamports(from_file.get(run.spec.book_id, []))
            else:
                shadow = _ledger_view(
                    run.shadow,
                    day,
                    promotion_live_ms=self.promotion_live_ms,
                    for_promotion=True,
                )
            block = {
                "kind": run.spec.kind,
                **ceiling,
                "shadow": shadow,
                "promote_ledger": "shadow",
                "capacity_ledger": "ceiling",
            }
            if run.spec.kind == "migrate":
                block["fee_sensitivity"] = sens_by_book.get(run.spec.book_id)
            books[run.spec.book_id] = block
        freeze = SWING_FREEZE_MS
        for run in self.books:
            if run.spec.kind == "swing" and run.spec.freeze_ms is not None:
                freeze = run.spec.freeze_ms
                break
        return {
            "schema": SCHEMA_PNL,
            "day": day,
            "t_ms": when,
            "swing_freeze_ms": freeze,
            "swing_freeze_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(freeze / 1000.0)),
            "promotion": "shadow",
            "capacity": "ceiling",
            "fail_rate": self.fail_rate,
            "stale_dropped": self.stale_dropped,
            "stale_cap_ms": STALE_ACTION_MS,
            "promotion_void_from": VOID_FROM,
            "promotion_live_ms": self.promotion_live_ms,
            "books": books,
            "latency": self.latency.report(),
        }


def _ledger_view(
    ledger: _Ledger,
    day: str,
    *,
    promotion_live_ms: int | None = None,
    for_promotion: bool = False,
) -> dict[str, Any]:
    pnls = ledger.realized
    if for_promotion:
        pnls = []
        times = ledger.realized_decision_t_ms
        for index, pnl in enumerate(ledger.realized):
            decision_t = times[index] if index < len(times) else None
            if decision_counts_for_promotion(decision_t, promotion_live_ms):
                pnls.append(pnl)
    return {
        "open": len(ledger.open),
        "pending": len(ledger.pending),
        "closed": ledger.closed_n,
        "day": ledger.day or day,
        "day_pnl_sol": ledger.day_pnl / LAMPORTS_PER_SOL,
        "realized": _stats_from_lamports(pnls),
        "skips": dict(ledger.skip_reasons),
    }


def window_creates(
    creates: dict[str, CreateSignal],
    t_min_ms: int,
    t_max_ms: int,
    *,
    pad_ms: int = 2_000,
) -> dict[str, CreateSignal]:
    """Creates whose signal falls in the replayed tape, plus a short lead."""
    start = t_min_ms - pad_ms
    return {mint: create for mint, create in creates.items() if start <= create.t_signal_ms <= t_max_ms}


def replay_rows(
    creates: Iterable[CreateSignal],
    trade_rows: Iterable[dict[str, Any]],
    books: Sequence[BookSpec],
    *,
    tape_end_ms: int,
    kill_file: Path,
    model: ModelSlot | None = None,
    barrier: ModelSlot | None = None,
    swing: ModelSlot | None = None,
    attention_rows: Iterable[dict[str, Any]] | None = None,
    extra_ms: int = 0,
    offsets_ms: Sequence[int] = DECISION_OFFSETS_MS,
    slippage_cap: float = DEFAULT_SLIPPAGE_CAP,
    record_packets: bool = False,
    retain_rows: bool = True,
    logs: dict[str, JsonlLog] | None = None,
) -> ForwardEngine:
    engine = ForwardEngine(
        books,
        kill_file=kill_file,
        latency=LatencyMeter(extra_ms=extra_ms),
        model=model,
        barrier=barrier,
        swing=swing,
        offsets_ms=offsets_ms,
        slippage_cap=slippage_cap,
        tape_end_ms=tape_end_ms,
        record_packets=record_packets,
        retain_rows=retain_rows,
        logs=logs,
    )
    if model is not None:
        model.maybe_reload(force=True)
    if barrier is not None:
        barrier.maybe_reload(force=True)
    if swing is not None:
        swing.maybe_reload(force=True)
    for create in creates:
        if create.t_signal_ms <= tape_end_ms:
            engine.push_create(create)
    for row in trade_rows:
        parsed = flow_from_tape_row(row)
        if parsed is None:
            continue
        mint, pr = parsed
        if pr.t_recv_ms > tape_end_ms:
            continue
        engine.push_print(mint, pr, _event_ts(row))
    for row in attention_rows or ():
        engine.push_attention(row)
    engine.drain_until(tape_end_ms, final=True)
    return engine


# Cross-mint containers a restart would otherwise reset to empty. Mutated (directly
# or via an object they hold, e.g. `library`'s MintBooks) by exactly the functions
# `build_shadow_state`/`splice_state`'s docstrings point at: `_register_create`,
# `_add_print`, `_consider_triggers`, `_note_clean_buyer`, `_note_curve`,
# `_note_buyers_8`, `_note_attention`/`_fire_attention`, `push_attention`, and
# `push_print`'s own `TxOrder.stamp` call. Everything here was reached by tracing
# every `self.<attr> = `/`self.<attr>.<mutator>()` inside those functions and their
# callees; see `splice_state`'s body for what each entry is and why a restart needs
# it back. Deliberately NOT here: `self.books` (ledgers/positions, a fill replay is
# not a state splice), `self.graph` (disk-reloaded on its own 5s clock, not
# tape-derived), and the three `LatencyMeter` diagnostics-only deques
# (`recv_to_decision`/`decision_to_send`/`applied` -- never re-read by
# `quote()`/`measure()`, per that class's own docstring).
_SHADOW_SPLICE_ATTRS: tuple[str, ...] = (
    "wallets",
    "by_creator",
    "library",
    "tracks",
    "mint_order",
    "seen",
    "early",
    "dead_mints",
    "dead_prints_dropped",
    "early_timeout_mints_dropped",
    "attention",
    "attn_snapshot",
    "attn_t_start_ms",
    "grids",
    "grid_seq",
    "emitted_grids",
    "mig15",
    "mig15_seq",
    "mig15_waiting",
    "_triggers",
    "_tx_order",
    "_prints",
    "stale_dropped",
)


def build_shadow_state(
    creates: Iterable[CreateSignal],
    trade_rows: Iterable[dict[str, Any]],
    books: Sequence[BookSpec],
    *,
    until_ms: int,
    attention_rows: Iterable[dict[str, Any]] | None = None,
    offsets_ms: Sequence[int] = DECISION_OFFSETS_MS,
    slippage_cap: float = DEFAULT_SLIPPAGE_CAP,
    model: ModelSlot | None = None,
    barrier: ModelSlot | None = None,
    swing: ModelSlot | None = None,
    dead_mints: frozenset[str] | None = None,
    early_timeout_ms: int | None = None,
    attn_t_start_ms: int = 0,
    attn_snapshot: set[tuple[str, str]] | None = None,
) -> ForwardEngine:
    """Throwaway engine that replays sealed input up to `until_ms` for its
    cross-mint state only. `splice_state` moves that state onto a freshly
    booted real engine so a restart's post-boot decisions match what an
    uninterrupted run would have made, instead of starting from empty
    `library`/`wallets`/pending-trigger state every time the process bounces.

    Zero decision/position/log side effects: `record_packets=False`,
    `retain_rows=False`, no `logs`, no `positions_path`, so `_on_signal`
    never appends to `self.packets`/`self.decisions`/`self.positions` and
    never touches disk (verified against the ~1698 `want_score` gate and the
    `if self.retain_rows` / `log is not None` guards in `_decision`/`_position`).
    `splice_state` never reads `self.books`, so nothing this function's own
    `_BookRun.ceiling`/`.shadow` ledgers accumulate (real opens, closes,
    skip reasons) ever reaches the real engine.

    `books` is the caller's *full* real book list, unmodified -- an earlier
    version of this function ran only a `swing`/`mig_15` subset (enough to
    keep `_schedule_mig15`'s ~1601 `self.books` gate correct without paying
    for real entry attempts), but that broke `_prune()` (~2328): its `busy`
    set (~2330) is `for run in self.books: busy.update(ledger.open);
    busy.update(ledger.pending)`, i.e. it is only as complete as the books
    actually run inside *this* engine. With the subset, a mint holding a
    real, still-open baseline/laya/migrate position was invisible to
    `busy`, so once it looked idle (~2340's `LIVE_IDLE_RETAIN_MS` window, or
    ~2350's `PRUNE_AFTER_MS` one) `_prune` truncated its `book.flow`/
    `self.seen` entry anyway -- silently, on a mint an uninterrupted engine
    (where that position really is open, really is busy, and is therefore
    never touched by `_prune` at all) would have kept in full. That
    truncated `library`/`seen` is exactly what `splice_state` hands to the
    real engine, so a multi-hour rebuild could restart with a stale-length
    history for any mint whose real position was open across the boundary
    but not represented in a book subset. Running the full book list
    (`_enter_or_skip` and all) is what actually makes `busy` -- and
    therefore what `_prune` keeps or drops -- match an uninterrupted engine.
    `tools/test_forward_paper.py::WarmStartTests
    .test_prune_keeps_a_mint_busy_across_a_restart_the_same_as_no_restart`
    fails against the old subset-only version and passes against this one.

    Model scoring during the shadow build is therefore a real cost, not
    optional: a `laya`/`swing` book with a real `model`/`barrier`/`swing`
    path scores every trigger it would have scored live, for the whole
    sealed window. Callers that don't pass a `model`/`barrier`/`swing`
    `ModelSlot` get the harmless default (`ModelSlot(None, None)`, always a
    same-line "no_model" skip, no scoring cost) -- but that is a caller
    choice now, not something this function forces to keep the state splice
    correct. See the PR body for a boot-time estimate on real model paths
    across a full week of tape.

    `_prune()`'s live-window truncation of `book.flow`/`book.path.prints`
    (~1440's `if self.latency.now_ms is not None`) is wall-clock gated, and
    a bare `LatencyMeter()` (as `replay_rows`/ParityTests/promotion backtests
    use) leaves `now_ms` unset, i.e. full offline history, never the
    live-sized window `serve()` actually keeps. This function instead seeds
    `LatencyMeter(now_ms=...)` off the tape's own clock -- one `_clock_box`
    bumped to each print's own `t_recv_ms` right before it is pushed -- the
    same substitution `tools/forward_paper_mem_profile.py` already uses (see
    its comment above its own `_clock_box`) to make `_prune()` reproduce
    what a live `serve()` retains instead of an offline re-score's full
    history. A restart's warm start wants exactly that: state as it would
    sit in a live process's memory at `until_ms`, not a from-scratch replay.

    `tape_end_ms` is left `None` on purpose -- the same as `serve()`'s own
    unbounded live tape, and unlike `replay_rows`'s finite offline
    `tape_end_ms`. `_register_create` (~1354) uses `self.tape_end_ms` to
    decide whether a future grid offset can ever fire; setting it to
    `until_ms` here would make every grid beyond the restart point
    (correctly still pending for a live process, which keeps receiving
    tape after it reboots) silently never get scheduled at all -- caught by
    this function's own test, which pushes a create before `until_ms` with
    a grid offset landing after it. `until_ms` only bounds what this
    function itself reads from `creates`/`trade_rows`/`attention_rows`
    (below) and where `drain_until` stops; it is not told to the engine.
    """
    clock_box: dict[str, int] = {"ms": 0}
    engine = ForwardEngine(
        books,
        kill_file=Path("/nonexistent/forward-paper-shadow-kill-never"),
        latency=LatencyMeter(now_ms=lambda: clock_box["ms"]),
        model=model,
        barrier=barrier,
        swing=swing,
        offsets_ms=offsets_ms,
        slippage_cap=slippage_cap,
        tape_end_ms=None,
        record_packets=False,
        retain_rows=False,
        logs=None,
        dead_mints=dead_mints,
        early_timeout_ms=early_timeout_ms,
    )
    if model is not None:
        model.maybe_reload(force=True)
    if barrier is not None:
        barrier.maybe_reload(force=True)
    if swing is not None:
        swing.maybe_reload(force=True)
    engine.attn_t_start_ms = attn_t_start_ms
    engine.attn_snapshot = attn_snapshot if attn_snapshot is not None else set()
    for create in creates:
        if create.t_signal_ms <= until_ms:
            engine.push_create(create)
    for row in trade_rows:
        parsed = flow_from_tape_row(row)
        if parsed is None:
            continue
        mint, pr = parsed
        if pr.t_recv_ms > until_ms:
            continue
        clock_box["ms"] = pr.t_recv_ms
        engine.push_print(mint, pr, _event_ts(row))
    for row in attention_rows or ():
        engine.push_attention(row)
    engine.drain_until(until_ms, final=True)
    return engine


def splice_state(src: ForwardEngine, dst: ForwardEngine) -> None:
    """Move `src`'s tape-built cross-mint state onto `dst`, in place.

    `src` is meant to be a `build_shadow_state()` throwaway; `dst` is a
    freshly booted real engine (its own `books`/kill file/model slots/logs
    already set up by the caller). Every attribute in `_SHADOW_SPLICE_ATTRS`
    is reassigned by reference (not deep-copied) -- `dst` then owns the same
    `library`/`wallets`/etc. objects `src` built, and `src` should be
    discarded right after this call.

    `dst.latency` keeps its own object (its `now_ms`/`extra_ms` are the
    live engine's real config, not the shadow's tape-clock stand-in) --
    only the chain-latency measurement is copied onto it, because that is
    the one piece of `LatencyMeter` a future decision actually reads:
    `chain_median_ms()` (fed by `chain_to_recv`/`_chain_n`, cached in
    `_median`/`_median_n`) is called from `quote()`/`measure()`, which sets
    `applied_latency_ms` and therefore `t_entry_ms` for every future fill.
    Losing that history at every restart would make the first entries after
    a restart price latency off zero prior chain samples instead of the
    engine's real running median. The other three `LatencyMeter` deques
    (`recv_to_decision`, `decision_to_send`, `applied`) are diagnostics the
    class's own docstring says are never re-read by a decision, so `dst`
    keeps its own (empty-since-boot is the correct state for them, same as
    an uninterrupted process's `report()` would show right after any of its
    periodic housekeeping -- they are rolling windows, not all-time totals).
    """
    for attr in _SHADOW_SPLICE_ATTRS:
        setattr(dst, attr, getattr(src, attr))
    dst.latency.chain_to_recv = src.latency.chain_to_recv
    dst.latency._chain_n = src.latency._chain_n
    dst.latency._median = src.latency._median
    dst.latency._median_n = src.latency._median_n


def offline_packets(
    creates: dict[str, CreateSignal],
    trade_rows: Iterable[dict[str, Any]],
    *,
    tape_end_ms: int,
    offsets_ms: Sequence[int] = DECISION_OFFSETS_MS,
) -> dict[tuple[str, int, str], dict[str, float]]:
    """Batch builder on the same rows the forward engine just replayed."""
    buckets: dict[str, list[FlowPrint]] = {mint: [] for mint in creates}
    for row in trade_rows:
        parsed = flow_from_tape_row(row)
        if parsed is None:
            continue
        mint, pr = parsed
        if mint not in buckets or pr.t_recv_ms > tape_end_ms:
            continue
        buckets[mint].append(pr)
    from tools.laya_v0 import _dedupe_sorted

    books = {
        mint: MintBook(create=create, flow=_dedupe_sorted(buckets[mint]))
        for mint, create in creates.items()
        if create.t_signal_ms <= tape_end_ms
    }
    rows, _diag = build_feature_rows(books, tape_end_ms=tape_end_ms, offsets_ms=offsets_ms)
    return {(row.mint, row.decision_t_ms, row.trigger): row.features for row in rows}


def reconcile_baseline(
    engine: ForwardEngine,
    creates: dict[str, CreateSignal],
    trade_rows: Sequence[dict[str, Any]],
    *,
    tape_end_ms: int,
    book_id: str,
    constant_latency_ms: int = 1000,
    slippage_cap: float = DEFAULT_SLIPPAGE_CAP,
) -> dict[str, Any]:
    """Compare the baseline book with the offline fill at the same latency and at 1s."""
    paths = _paths_from_rows(creates, trade_rows, tape_end_ms)
    online = [
        row
        for row in engine.positions
        if row.get("book") == book_id and row.get("event") == "close" and row.get("ledger", "ceiling") == "ceiling"
    ]
    online_pnl = [int(row["pnl_lamports"]) for row in online if isinstance(row.get("pnl_lamports"), int)]
    resim: list[int] = []
    gaps = 0
    for row in online:
        path = paths.get(row["mint"])
        if path is None or not isinstance(row.get("pnl_lamports"), int):
            gaps += 1
            continue
        latency_ms = int(row["applied_latency_ms"])
        feats = features_at_t(path)
        entry = try_entry(
            path,
            t_entry_ms=path.create.t_signal_ms + latency_ms,
            size_lamports=int(row["size_lamports"]),
            slippage_cap=slippage_cap,
            feats=feats,
        )
        rule = RULES[str(row["exit_rule"])]
        part = simulate_exit(
            path,
            entry,
            rule,
            latency_ms=latency_ms,
            tape_end_ms=tape_end_ms,
            size_lamports=int(row["size_lamports"]),
        )
        pnl = part.get("pnl_lamports")
        if pnl != row["pnl_lamports"]:
            gaps += 1
        if isinstance(pnl, int):
            resim.append(pnl)
    constant: list[int] = []
    for path in paths.values():
        if path.create.t_signal_ms > tape_end_ms:
            continue
        feats = features_at_t(path)
        entry = try_entry(
            path,
            t_entry_ms=path.create.t_signal_ms + constant_latency_ms,
            size_lamports=HARD_MAX_POSITION_LAMPORTS,
            slippage_cap=slippage_cap,
            feats=feats,
        )
        part = simulate_exit(
            path,
            entry,
            RULES["hold_30s"],
            latency_ms=constant_latency_ms,
            tape_end_ms=tape_end_ms,
            size_lamports=HARD_MAX_POSITION_LAMPORTS,
        )
        pnl = part.get("pnl_lamports")
        if isinstance(pnl, int):
            constant.append(pnl)
    online_stats = _stats_from_lamports(online_pnl)
    constant_stats = _stats_from_lamports(constant)
    return {
        "schema": SCHEMA_RECONCILE,
        "book": book_id,
        "tape_end_ms": tape_end_ms,
        "online_vs_same_latency": {
            "closes": len(online),
            "pnl_mismatches": gaps,
            "online": online_stats,
            "resim": _stats_from_lamports(resim),
        },
        "online_vs_constant_1s": {
            "online": online_stats,
            "constant_1s_hold_30s": constant_stats,
            "total_sol_gap": None
            if online_stats["n"] == 0 or constant_stats["n"] == 0
            else online_stats["total_sol"] - constant_stats["total_sol"],
            "median_sol_gap": None
            if online_stats["median_sol"] is None or constant_stats["median_sol"] is None
            else online_stats["median_sol"] - constant_stats["median_sol"],
            "n_gap": online_stats["n"] - constant_stats["n"],
        },
    }


def _paths_from_rows(
    creates: dict[str, CreateSignal],
    trade_rows: Iterable[dict[str, Any]],
    tape_end_ms: int,
) -> dict[str, MintPath]:
    from tools.laya_v0 import _dedupe_sorted

    buckets: dict[str, list[FlowPrint]] = {mint: [] for mint in creates}
    for row in trade_rows:
        parsed = flow_from_tape_row(row)
        if parsed is None:
            continue
        mint, pr = parsed
        if mint not in buckets or pr.t_recv_ms > tape_end_ms:
            continue
        buckets[mint].append(pr)
    paths: dict[str, MintPath] = {}
    for mint, create in creates.items():
        flow = _dedupe_sorted(buckets[mint])
        paths[mint] = MintPath(create=create, prints=[pr.to_tape() for pr in flow])
    return paths


def _iter_jsonl(paths: Iterable[Path], *, span_ms: int | None = None) -> Iterable[dict[str, Any]]:
    """Read tape rows. `span_ms` stops after the first receive time plus that span."""
    t0: int | None = None
    for path in paths:
        with open_text(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                raw = row.get("t_recv_ms")
                if span_ms is not None and isinstance(raw, int):
                    if t0 is None:
                        t0 = raw
                    elif raw > t0 + span_ms:
                        return
                yield row


def _discover(directory: Path, patterns: Sequence[str]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        found.extend(directory.glob(pattern))
    return sorted({path.resolve() for path in found if path.is_file()})


def _preboot_dead_mints(
    creates_dir: Path,
    boot_ms: int,
    *,
    margin_ms: int = PREBOOT_DEAD_MARGIN_MS,
) -> frozenset[str]:
    """Mints whose create this boot's `DirectoryTail` can never deliver.

    `DirectoryTail` only ever watches the *current* UTC day's
    `observe-{day}.jsonl` (`_ensure()` recomputes `day` from wall time, it
    never re-opens a prior day's file even if its key still sits in a loaded
    `offsets.json`), and on that file it resumes from the last persisted
    offset or, absent one, today's current EOF -- never rewinding. So any
    create dated more than `margin_ms` before boot, in today's file or
    (fully closed, guaranteed never re-opened by a fresh boot) yesterday's,
    is read once here -- purely to build a drop-set, never registered as a
    `MintBook` -- and its mint's future prints are then dropped by
    `ForwardEngine._add_print` instead of buffered in `self.early` forever.

    Read-only: opens these files for reading only, and does not touch
    `DirectoryTail`'s own followers or `offsets.json`.
    """
    cutoff_ms = boot_ms - margin_ms
    boot_day = time.strftime("%Y-%m-%d", time.gmtime(boot_ms / 1000))
    prior_day = time.strftime("%Y-%m-%d", time.gmtime(boot_ms / 1000 - 86_400))
    paths: list[Path] = []
    for day in (prior_day, boot_day):
        for suffix in (".jsonl", ".jsonl.zst"):
            candidate = creates_dir / f"observe-{day}{suffix}"
            if candidate.is_file():
                paths.append(candidate)
    if not paths:
        return frozenset()
    dead = load_creates(paths, t_max_ms=cutoff_ms, pad_before_ms=0)
    return frozenset(dead.keys())


def _safe_preboot_dead_mints(
    creates_dir: Path,
    boot_ms: int,
    *,
    margin_ms: int = PREBOOT_DEAD_MARGIN_MS,
) -> frozenset[str]:
    """`_preboot_dead_mints`, but never able to stop `serve()` from starting.

    This runs once at boot, before the main loop, before the kill switch or
    any risk gate is live -- a corrupt/truncated observe line, a `zstd`
    failure on a sealed `.jsonl.zst`, or a permissions error inside
    `load_creates` must not crash `serve()` and restart-loop the live
    runner. An empty result here is always safe: it just means rule A finds
    nothing this boot, and rule B (`EARLY_BUFFER_DEAD_MS`, unaffected by
    this) still catches the same mints within its own timeout -- the
    pre-existing `self.early`/`_prune_early` path is the fallback either
    way, exactly today's shipped behavior before this PR.
    """
    try:
        return _preboot_dead_mints(creates_dir, boot_ms, margin_ms=margin_ms)
    except Exception as exc:  # noqa: BLE001 - startup safety net, must never propagate
        print(f"forward_paper preboot_dead_mints_error err={exc!r}", file=sys.stderr)
        return frozenset()


class _Follower:
    """Tail plain JSONL. A sealed `.zst` hour is left to the recorder."""

    def __init__(self, path: Path, offset: int = 0) -> None:
        self.path = path
        self.offset = offset

    def read_exact(self) -> list[str]:
        if not self.path.is_file():
            return []
        size = self.path.stat().st_size
        if size < self.offset:
            self.offset = 0
        with self.path.open("rb") as fh:
            fh.seek(self.offset)
            # One poll must not pull a whole hour into the 1G cgroup.
            data = fh.read(min(READ_CHUNK_BYTES, size - self.offset))
        if not data:
            return []
        cut = data.rfind(b"\n")
        if cut < 0:
            return []
        take = data[: cut + 1]
        self.offset += len(take)
        text = take.decode("utf-8", errors="replace")
        return [line for line in text.splitlines() if line]


class DirectoryTail:
    """Follow the open trade hour, the observe day file, and attention hours. No writes to those dirs."""

    def __init__(
        self,
        tape_dir: Path,
        creates_dir: Path,
        offsets: dict[str, int],
        attention_dir: Path | None = None,
    ) -> None:
        self.tape_dir = tape_dir
        self.creates_dir = creates_dir
        self.attention_dir = attention_dir
        self.offsets = offsets
        self._open: dict[str, _Follower] = {}

    def poll(self) -> list[tuple[str, dict[str, Any]]]:
        self._ensure()
        out: list[tuple[str, dict[str, Any]]] = []
        for key, follower in list(self._open.items()):
            try:
                lines = follower.read_exact()
            except OSError:
                continue
            self.offsets[key] = follower.offset
            if key.startswith("create:"):
                kind = "create"
            elif key.startswith("attention:"):
                kind = "attention"
            else:
                kind = "trade"
            for line in lines:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    out.append((kind, row))
        return out

    def _ensure(self) -> None:
        now = time.gmtime()
        hour = time.strftime("%Y-%m-%dT%H", now)
        day = time.strftime("%Y-%m-%d", now)
        trade = self.tape_dir / f"trades-{hour}.jsonl"
        create = self.creates_dir / f"observe-{day}.jsonl"
        watched = [("trade", trade), ("create", create)]
        if self.attention_dir is not None:
            watched.append(("attention", self.attention_dir / f"attention-{hour}.jsonl"))
        for kind, path in watched:
            key = f"{kind}:{path}"
            if key in self._open or not path.is_file():
                continue
            offset = self.offsets.get(key, path.stat().st_size)
            self._open[key] = _Follower(path, offset)
        # Drop followers whose hour file was renamed away.
        for key, follower in list(self._open.items()):
            if not follower.path.is_file():
                self._open.pop(key, None)


def run_replay_files(
    *,
    tape: Sequence[Path],
    creates: Sequence[Path],
    output_dir: Path,
    config_books: Sequence[BookSpec],
    kill_file: Path,
    model_path: Path | None,
    meta_path: Path | None,
    barrier_path: Path | None = None,
    swing_path: Path | None = None,
    tape_end_ms: int | None,
    slippage_cap: float,
    span_ms: int | None = None,
) -> dict[str, Any]:
    loaded = load_creates(creates)
    rows = list(_iter_jsonl(tape, span_ms=span_ms))
    observed = [row["t_recv_ms"] for row in rows if isinstance(row.get("t_recv_ms"), int)]
    if tape_end_ms is None:
        tape_end_ms = max(observed) if observed else 0
    if observed:
        loaded = window_creates(loaded, min(observed), tape_end_ms)
    output_dir.mkdir(parents=True, exist_ok=True)
    logs = {
        "decisions": JsonlLog(output_dir / "decisions.jsonl"),
        "positions": JsonlLog(output_dir / "positions.jsonl"),
    }
    model = ModelSlot(model_path, meta_path)
    barrier = ModelSlot(barrier_path, meta_path)
    swing_slot = ModelSlot(swing_path, None)
    engine = replay_rows(
        loaded.values(),
        rows,
        config_books,
        tape_end_ms=tape_end_ms,
        kill_file=kill_file,
        model=model,
        barrier=barrier,
        swing=swing_slot,
        slippage_cap=slippage_cap,
        logs=logs,
        record_packets=True,
    )
    baseline_ids = [b.spec.book_id for b in engine.books if b.spec.kind == "baseline"]
    reconcile = None
    if baseline_ids:
        reconcile = reconcile_baseline(
            engine,
            loaded,
            rows,
            tape_end_ms=tape_end_ms,
            book_id=baseline_ids[0],
            slippage_cap=slippage_cap,
        )
        (output_dir / "reconcile.json").write_text(json.dumps(_json_safe(reconcile), indent=2) + "\n", encoding="utf-8")
    summary = engine.summary(tape_end_ms)
    summary_log = JsonlLog(output_dir / "pnl-daily.jsonl")
    summary_log.write(summary)
    latency = engine.latency.report()
    (output_dir / "latency.json").write_text(json.dumps(_json_safe(latency), indent=2) + "\n", encoding="utf-8")
    JsonlLog(output_dir / "latency.jsonl").write(latency)
    for log in logs.values():
        log.close()
    summary_log.close()
    return {"summary": summary, "reconcile": reconcile, "latency": latency}


def reload_risk_config(path: Path, current: list[BookSpec]) -> tuple[list[BookSpec], Path | None]:
    """Re-read host JSON. A file above a ceiling is logged and not applied."""
    try:
        raw = load_config(path)
        books = books_from_config(raw)
    except (RiskConfigError, OSError, json.JSONDecodeError, SystemExit, KeyError, TypeError, ValueError) as exc:
        print(f"forward_paper risk refused: {exc}", file=sys.stderr)
        return current, None
    kill = raw.get("kill_file")
    return books, Path(kill) if isinstance(kill, str) and kill else None


def adopt_book_limits(engine: ForwardEngine, books: Sequence[BookSpec]) -> None:
    incoming = {spec.book_id: spec for spec in books}
    for run in engine.books:
        spec = incoming.get(run.spec.book_id)
        if spec is not None:
            if run.spec.entry_model is not None or spec.entry_model is not None:
                # No hot reload of the gate keys: the running book keeps its own.
                spec = dataclasses.replace(
                    spec,
                    entry_model=run.spec.entry_model,
                    entry_model_md5=run.spec.entry_model_md5,
                    entry_threshold=run.spec.entry_threshold,
                    entry_features=run.spec.entry_features,
                )
            run.spec = spec


def bind_attention(engine: ForwardEngine, directory: Path | None) -> None:
    """Startup snapshot and poller start. Rows in that set are not genuine arrivals."""
    if directory is None or not directory.is_dir():
        return
    start = load_poller_start_ms(directory)
    engine.attn_t_start_ms = start or 0
    engine.attn_snapshot = load_snapshot_keys(directory)


def serve(config_path: Path) -> int:
    try:
        raw = load_config(config_path)
        books = books_from_config(raw)
    except RiskConfigError as exc:
        print(f"forward_paper risk refused: {exc}", file=sys.stderr)
        return 1
    tape_dir = Path(raw.get("tape_dir") or "/var/lib/mal/sealed/trades")
    creates_dir = Path(raw.get("creates_dir") or "/var/lib/mal/sealed/jsonl")
    output_dir = Path(raw.get("output_dir") or "/var/lib/mal/paper/forward-paper")
    kill_file = Path(raw.get("kill_file") or (output_dir / "KILL"))
    model_path = Path(raw["model_path"]) if raw.get("model_path") else None
    meta_path = Path(raw["model_meta"]) if raw.get("model_meta") else None
    barrier_path = Path(raw["barrier_model"]) if raw.get("barrier_model") else None
    swing_path = Path(raw["swing_model"]) if raw.get("swing_model") else None
    attention_dir = Path(raw["attention_dir"]) if raw.get("attention_dir") else None
    holdback_ms = int(raw.get("holdback_ms", 300))
    slippage = float(raw.get("slippage_cap", DEFAULT_SLIPPAGE_CAP))
    boot_ms = int(time.time() * 1000)
    _preboot_t0 = time.monotonic()
    dead_mints = _safe_preboot_dead_mints(creates_dir, boot_ms)
    preboot_scan_ms = (time.monotonic() - _preboot_t0) * 1000.0
    print(
        f"forward_paper preboot_dead_mints={len(dead_mints)} boot={_utc(boot_ms)} scan_ms={preboot_scan_ms:.1f}",
        file=sys.stderr,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    offset_path = output_dir / "offsets.json"
    offsets: dict[str, int] = {}
    if offset_path.is_file():
        try:
            loaded = json.loads(offset_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                offsets = {str(k): int(v) for k, v in loaded.items()}
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            offsets = {}
    logs = {
        "decisions": JsonlLog(output_dir / "decisions.jsonl"),
        "positions": JsonlLog(output_dir / "positions.jsonl"),
        "pnl": JsonlLog(output_dir / "pnl-daily.jsonl"),
        "latency": JsonlLog(output_dir / "latency.jsonl"),
        "mem_census": JsonlLog(output_dir / "mem-census.jsonl"),
    }
    if any(b.entry_model is not None for b in books):
        logs["exp012_gate"] = JsonlLog(output_dir / "exp012-gate.jsonl")
    model = ModelSlot(model_path, meta_path)
    model.maybe_reload(force=True)
    barrier = ModelSlot(barrier_path, meta_path)
    barrier.maybe_reload(force=True)
    swing = ModelSlot(swing_path, None)
    swing.maybe_reload(force=True)
    engine = ForwardEngine(
        books,
        kill_file=kill_file,
        latency=LatencyMeter(now_ms=lambda: int(time.time() * 1000)),
        model=model,
        barrier=barrier,
        swing=swing,
        slippage_cap=slippage,
        logs={k: logs[k] for k in ("decisions", "positions", "exp012_gate") if k in logs},
        fail_rate=DEFAULT_FAIL_RATE,
        positions_path=output_dir / "positions.jsonl",
        dead_mints=dead_mints,
        early_timeout_ms=EARLY_BUFFER_DEAD_MS,
        tx_order_prune_ms=TX_ORDER_PRUNE_MS,
    )
    if engine.exp012 is not None:
        n_hist = engine.exp012.preload(creates_dir, boot_ms)
        print(f"forward_paper exp012_gate books={sorted(engine.exp012_gates)} creator_history_preloaded={n_hist}", file=sys.stderr)
    bind_attention(engine, attention_dir)
    graph_dir = Path(str(raw.get("graph_dir") or "/var/lib/mal/graph"))
    if graph_dir.is_dir():
        engine.graph_dir = graph_dir
    # Startup is done (models loaded, engine built): raise the cyclic-GC
    # thresholds and freeze the static baseline. See GC_THRESHOLD's comment.
    gc_stats = install_gc_mitigation()
    mem_census = MemCensus()
    tail = DirectoryTail(tape_dir, creates_dir, offsets, attention_dir)
    stop = {"flag": False}

    def _stop(_signum: int, _frame: Any) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    last_summary = 0.0
    last_model = 0.0
    try:
        config_mtime = config_path.stat().st_mtime
    except OSError:
        config_mtime = 0.0
    freeze_ms = next((b.freeze_ms for b in books if b.kind == "swing" and b.freeze_ms is not None), SWING_FREEZE_MS)
    freeze_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(freeze_ms / 1000.0))
    print(
        f"forward_paper books={','.join(b.book_id for b in books)} kill={kill_file} "
        f"swing_freeze={freeze_at} stale_cap_ms={STALE_ACTION_MS} fail_rate={DEFAULT_FAIL_RATE}",
        file=sys.stderr,
    )
    # Replace a polluted recv→decision report before the next label build reads it.
    (output_dir / "latency.json").write_text(
        json.dumps(_json_safe(engine.latency.report()), indent=2) + "\n",
        encoding="utf-8",
    )
    status_path = output_dir / RUNNER_STATUS_NAME
    live_at_ms = load_guard_live_ms(output_dir / GUARD_LIVE_NAME)
    engine.promotion_live_ms = live_at_ms
    last_status = 0.0
    last_prune = 0.0
    last_mem_census = 0.0
    mem_census_failures = 0
    while not stop["flag"]:
        batch = tail.poll()
        now_ms = int(time.time() * 1000)
        fresh: list[tuple[str, dict[str, Any]]] = []
        for kind, row in batch:
            clock = row_clock_ms(kind, row)
            if clock is not None:
                engine.newest_recv_ms = clock if engine.newest_recv_ms is None else max(engine.newest_recv_ms, clock)
                if now_ms - clock > STALE_ACTION_MS:
                    engine.stale_dropped += 1
                    continue
            fresh.append((kind, row))
        if live_at_ms is None:
            newest = engine.newest_recv_ms
            caught_up = (not batch) or (newest is not None and now_ms - newest <= STALE_ACTION_MS)
            if caught_up:
                live_at_ms = ensure_guard_live(output_dir, now_ms)
                engine.promotion_live_ms = live_at_ms
                print(f"forward_paper guard_live={_utc(live_at_ms)}", file=sys.stderr)
        watermark = 0
        for kind, row in fresh:
            if kind == "create":
                create = create_from_observe_row(row)
                if create is None:
                    continue
                engine.push_create(create)
                watermark = max(watermark, create.t_signal_ms)
            elif kind == "attention":
                engine.push_attention(row)
                raw_t = row.get("t_first_ms")
                if isinstance(raw_t, int):
                    watermark = max(watermark, raw_t)
            else:
                parsed = flow_from_tape_row(row)
                if parsed is None:
                    continue
                mint, pr = parsed
                engine.push_print(mint, pr, _event_ts(row))
                watermark = max(watermark, pr.t_recv_ms)
        if watermark:
            engine.drain_until(max(0, watermark - holdback_ms))
        elif engine.inbox:
            engine.flush()
        now = time.monotonic()
        if now - last_model > 30:
            model.maybe_reload()
            barrier.maybe_reload()
            swing.maybe_reload()
            bind_attention(engine, attention_dir)
            try:
                mtime = config_path.stat().st_mtime
            except OSError:
                mtime = config_mtime
            if mtime != config_mtime:
                reloaded, kill = reload_risk_config(config_path, books)
                if reloaded is not books:
                    books = reloaded
                    adopt_book_limits(engine, books)
                    if kill is not None:
                        engine.kill_file = kill
                config_mtime = mtime
            last_model = now
        if now - last_prune > 5 and engine.latency.now_ms is not None:
            engine._prune(engine._clock_ms or now_ms)
            last_prune = now
        if now - last_summary > 60 and engine._clock_ms:
            snap = engine.summary()
            logs["pnl"].write(snap)
            logs["latency"].write(engine.latency.report())
            (output_dir / "latency.json").write_text(
                json.dumps(_json_safe(engine.latency.report()), indent=2) + "\n",
                encoding="utf-8",
            )
            offset_path.write_text(json.dumps(tail.offsets) + "\n", encoding="utf-8")
            last_summary = now
        if now - last_status > 15:
            write_runner_status(status_path, engine, live_at_ms=live_at_ms)
            last_status = now
        if now - last_mem_census > 300:
            mem_census_failures = write_mem_census(
                engine, mem_census, gc_stats, output_dir, logs, now_ms, mem_census_failures
            )
            last_mem_census = now
        maybe_gc_freeze(gc_stats, now)
        if not batch:
            time.sleep(0.025)
    if engine._clock_ms:
        logs["pnl"].write(engine.summary())
    offset_path.write_text(json.dumps(tail.offsets) + "\n", encoding="utf-8")
    for log in logs.values():
        log.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forward paper books on the pump tape (paper only, no keys)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve_p = sub.add_parser("serve", help="tail the live tape")
    serve_p.add_argument("--config", required=True, type=Path)

    replay_p = sub.add_parser("replay", help="replay sealed files and write the gap report")
    replay_p.add_argument("--config", required=True, type=Path)
    replay_p.add_argument("--tape", nargs="*", type=Path, default=[])
    replay_p.add_argument("--creates", nargs="*", type=Path, default=[])
    replay_p.add_argument("--tape-dir", type=Path)
    replay_p.add_argument("--creates-dir", type=Path)
    replay_p.add_argument("--output-dir", required=True, type=Path)
    replay_p.add_argument("--tape-end-ms", type=int)
    replay_p.add_argument("--span-min", type=float, default=0, help="stop this many minutes after the first tape row; 0 reads the file")

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        return serve(args.config)
    try:
        raw = load_config(args.config)
        books = books_from_config(raw)
    except RiskConfigError as exc:
        print(f"forward_paper risk refused: {exc}", file=sys.stderr)
        return 1
    tape = list(args.tape)
    creates = list(args.creates)
    if args.tape_dir:
        tape.extend(_discover(args.tape_dir, ("trades-*.jsonl", "trades-*.jsonl.zst", "trades-*.jsonl.gz")))
    if args.creates_dir:
        creates.extend(_discover(args.creates_dir, ("observe-*.jsonl", "observe-*.jsonl.zst")))
    model = Path(raw["model_path"]) if raw.get("model_path") else None
    meta = Path(raw["model_meta"]) if raw.get("model_meta") else None
    barrier = Path(raw["barrier_model"]) if raw.get("barrier_model") else None
    swing_model = Path(raw["swing_model"]) if raw.get("swing_model") else None
    kill = Path(raw.get("kill_file") or (args.output_dir / "KILL"))
    result = run_replay_files(
        tape=sorted({p.resolve() for p in tape}),
        creates=sorted({p.resolve() for p in creates}),
        output_dir=args.output_dir,
        config_books=books,
        kill_file=kill,
        model_path=model if model and model.is_file() else None,
        meta_path=meta if meta and meta.is_file() else None,
        barrier_path=barrier if barrier and barrier.is_file() else None,
        swing_path=swing_model if swing_model and swing_model.is_file() else None,
        tape_end_ms=args.tape_end_ms,
        slippage_cap=float(raw.get("slippage_cap", DEFAULT_SLIPPAGE_CAP)),
        span_ms=None if args.span_min <= 0 else int(args.span_min * 60_000),
    )
    recon = result.get("reconcile") or {}
    gap = (recon.get("online_vs_same_latency") or {}).get("pnl_mismatches")
    sys.stdout.write(f"forward_paper replay mismatches={gap}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
