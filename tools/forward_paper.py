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
import heapq
import json
import math
import signal
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, TextIO

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
    simulate_ladder,
    vector,
)
from tools.paper_curve_math import (
    DEFAULT_SIZE_LAMPORTS,
    DEFAULT_SLIPPAGE_CAP,
    LAMPORTS_PER_SOL,
    PRIORITY_FEE_LAMPORTS,
)
from tools.paper_price_path import (
    CreateSignal,
    MintPath,
    create_from_observe_row,
    load_creates,
    open_text,
)
from tools.paper_tape_scoreboard import (
    EXIT_RULES,
    ExitRule,
    _pct,
    _stats_from_lamports,
    features_at_t,
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


class RiskConfigError(Exception):
    """Host JSON asked for a looser risk limit than the hard ceiling."""
PRUNE_AFTER_MS = 45 * 60 * 1000
CHAIN_LAG_MIN_MS = -5_000
CHAIN_LAG_MAX_MS = 120_000
RULES: dict[str, ExitRule] = {rule.rule_id: rule for rule in EXIT_RULES}


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
        self.chain_to_recv: list[int] = []
        self.recv_to_decision: list[int] = []
        self.decision_to_send: list[int] = []
        self.applied: list[int] = []
        self._median: int | None = None
        self._median_n = 0

    def note_print(self, t_recv_ms: int, event_ts: int | None) -> None:
        if event_ts is None:
            return
        lag = t_recv_ms - event_ts * 1000
        if lag < CHAIN_LAG_MIN_MS or lag > CHAIN_LAG_MAX_MS:
            return
        self.chain_to_recv.append(lag)

    def chain_median_ms(self) -> int | None:
        n = len(self.chain_to_recv)
        if n == 0:
            return None
        if self._median is not None and n - self._median_n < 2000:
            return self._median
        sample = self.chain_to_recv[-8192:]
        self._median = int(round(statistics.median(sample)))
        self._median_n = n
        return self._median

    def measure(self, decision_t_ms: int) -> dict[str, Any]:
        chain = self.chain_median_ms()
        post = self.extra_ms
        if self.now_ms is not None:
            post += max(0, self.now_ms() - decision_t_ms)
        send = 0
        applied = max(0, (chain or 0) + post + send)
        if post:
            self.recv_to_decision.append(post)
        self.decision_to_send.append(send)
        self.applied.append(applied)
        return {
            "chain_to_recv_ms": chain,
            "recv_to_decision_ms": post,
            "decision_to_send_ms": send,
            "applied_latency_ms": applied,
            "e2e_on_chain_to_send_ms": None if chain is None else chain + post + send,
        }

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

    def resolved_exit(self, deploy_rule: str) -> ExitRule | LadderRule:
        rule_id = deploy_rule if self.exit_rule == "deploy" else self.exit_rule
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


def books_from_config(raw: dict[str, Any]) -> list[BookSpec]:
    guard_kill_switch(raw)
    found: list[BookSpec] = []
    for item in raw.get("books") or []:
        if not isinstance(item, dict):
            raise SystemExit("book entries must be objects")
        kind = str(item.get("kind") or "")
        if kind not in ("baseline", "laya", "migrate"):
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
        model_key = str(item.get("model") or "entry")
        if model_key not in ("entry", "barrier"):
            raise SystemExit(f"book {item.get('id')} model must be entry or barrier")
        found.append(
            BookSpec(
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


@dataclass
class _BookRun:
    spec: BookSpec
    pending: dict[str, _Pending] = field(default_factory=dict)
    open: dict[str, _Open] = field(default_factory=dict)
    day: str = ""
    day_pnl: int = 0
    realized: list[int] = field(default_factory=list)
    token_ready: dict[str, int] = field(default_factory=dict)
    creator_ready: dict[str, int] = field(default_factory=dict)
    closed_n: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    rank: _RankWindow | None = None


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
        offsets_ms: Sequence[int] = DECISION_OFFSETS_MS,
        slippage_cap: float = DEFAULT_SLIPPAGE_CAP,
        tape_end_ms: int | None = None,
        record_packets: bool = False,
        retain_rows: bool = False,
        logs: dict[str, JsonlLog] | None = None,
    ) -> None:
        self.books = []
        for spec in books:
            run = _BookRun(spec)
            if spec.top_frac is not None:
                run.rank = _RankWindow(spec.top_frac)
            self.books.append(run)
        self.kill_file = kill_file
        self.latency = latency or LatencyMeter()
        self.model = model or ModelSlot(None, None)
        self.barrier = barrier or ModelSlot(None, None)
        self.offsets_ms = tuple(offsets_ms)
        self.slippage_cap = slippage_cap
        self.tape_end_ms = tape_end_ms
        # Packets are scored on the fly. Only a replay keeps the rows in RAM.
        self.record_packets = record_packets
        self.retain_rows = retain_rows
        self.logs = logs or {}
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
        self.early: dict[str, list[tuple[FlowPrint, int | None]]] = defaultdict(list)
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
        if create.mint in self.library:
            return
        if self.tape_end_ms is not None and create.t_signal_ms > self.tape_end_ms:
            return
        book = MintBook(create=create, flow=[])
        self.library[create.mint] = book
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
        self._push(pr.t_recv_ms, 0, ("print", mint, pr, event_ts))

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
        self._fill_through(t_ms - 1)
        self._exits_through(t_ms - 1)

    def _at_time(self, t_ms: int) -> None:
        self._clock_ms = t_ms
        self._emit_grids_through(t_ms)
        queued = self._triggers
        self._triggers = []
        for mint, when, trigger in queued:
            if when <= t_ms:
                self._on_signal(mint, when, trigger)
        self._fill_through(t_ms)
        self._exits_through(t_ms)
        if self._prints and self._prints % 50_000 == 0:
            self._prune(t_ms)

    def _apply_batch(self, t_ms: int, batch: list[tuple[int, int, int, Any]]) -> None:
        creators = [item for item in batch if item[3][0] == "creator"]
        prints = [item for item in batch if item[3][0] == "print"]
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
        for item in creators:
            create = item[3][1]
            if create.mint in self.library and create.t_signal_ms == t_ms:
                self._baseline(create.mint, t_ms)

    def _print_sort_key(self, mint: str, pr: FlowPrint) -> tuple[Any, ...]:
        return (self.mint_order.get(mint, 10**9), pr.slot, pr.event_index, pr.trader or "", pr.side)

    def _flush_early(self, mint: str) -> None:
        buffered = self.early.pop(mint, [])
        for pr, event_ts in buffered:
            if self._add_print(mint, pr, event_ts):
                self._consider_triggers(mint, pr)

    def _add_print(self, mint: str, pr: FlowPrint, event_ts: int | None = None) -> bool:
        book = self.library.get(mint)
        if book is None:
            self.early[mint].append((pr, event_ts))
            return False
        if self.tape_end_ms is not None and pr.t_recv_ms > self.tape_end_ms:
            return False
        key = _dedupe_key(pr)
        if key in self.seen[mint]:
            return False
        self.seen[mint].add(key)
        self._insert_print(book, pr)
        self.wallets.observe_print(mint, pr)
        self.latency.note_print(pr.t_recv_ms, event_ts)
        self._prints += 1
        return True

    def _insert_print(self, book: MintBook, pr: FlowPrint) -> None:
        flow = book.flow
        key = (pr.t_recv_ms, pr.slot, pr.event_index, pr.trader or "", pr.side)
        if not flow:
            book.flow.append(pr)
            book.path.prints.append(pr.to_tape())
            return
        last = flow[-1]
        last_key = (last.t_recv_ms, last.slot, last.event_index, last.trader or "", last.side)
        if key >= last_key:
            book.flow.append(pr)
            book.path.prints.append(pr.to_tape())
            return
        lo, hi = 0, len(flow)
        while lo < hi:
            mid = (lo + hi) // 2
            cur = flow[mid]
            cur_key = (cur.t_recv_ms, cur.slot, cur.event_index, cur.trader or "", cur.side)
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
            self._triggers.append((mint, pr.t_recv_ms, "migrate"))
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
        for run in self.books:
            if not _point_matches(run.spec, book, t_ms, trigger):
                continue
            if run.spec.kind == "laya":
                self._enter_or_skip(run, book, t_ms, trigger, feats)
            elif run.spec.kind == "migrate" and trigger == "migrate":
                self._enter_or_skip(run, book, t_ms, trigger, feats)

    def _enter_or_skip(
        self,
        run: _BookRun,
        book: MintBook,
        t_ms: int,
        trigger: str,
        feats: dict[str, float] | None,
    ) -> None:
        spec = run.spec
        mint = book.create.mint
        creator = book.create.creator
        score = None
        if spec.kind == "laya":
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
        reason = self._risk_reason(run, mint, creator, t_ms, spec.size_lamports)
        if reason:
            self._decision(run, book, t_ms, trigger, "skip", reason, score, None)
            return
        hops = self.latency.measure(t_ms)
        latency_ms = int(hops["applied_latency_ms"])
        ref = self._ref_price(book, t_ms, feats)
        rule = spec.resolved_exit(self.model.rule_id)
        pending = _Pending(
            book_id=spec.book_id,
            mint=mint,
            creator=creator,
            t_entry_ms=t_ms + latency_ms,
            decision_t_ms=t_ms,
            trigger=trigger,
            rule=rule,
            size_lamports=spec.size_lamports,
            latency_ms=latency_ms,
            score=score,
            ref_price=ref,
            hops=hops,
        )
        run.pending[mint] = pending
        if pending.t_entry_ms <= self._clock_ms:
            self._fill_one(run, pending)

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

    def _risk_reason(self, run: _BookRun, mint: str, creator: str | None, t_ms: int, size: int) -> str | None:
        # Kill switch is not configurable. A loosened BookSpec still cannot trade past the ceilings.
        if self.kill_file.is_file():
            return "kill_switch"
        spec = run.spec
        if size > HARD_MAX_POSITION_LAMPORTS:
            return "max_position_size"
        if mint in run.open or mint in run.pending:
            return "already_open"
        concurrent = CEILING_MAX_CONCURRENT if spec.max_concurrent is None else min(spec.max_concurrent, CEILING_MAX_CONCURRENT)
        if len(run.open) + len(run.pending) >= concurrent:
            return "max_concurrent"
        self._roll_day(run, t_ms)
        loss_cap = CEILING_DAILY_LOSS_LAMPORTS if spec.daily_loss_lamports is None else min(spec.daily_loss_lamports, CEILING_DAILY_LOSS_LAMPORTS)
        if run.day_pnl <= -loss_cap:
            return "daily_loss_cap"
        if t_ms < run.token_ready.get(mint, 0):
            return "token_cooldown"
        if creator and t_ms < run.creator_ready.get(creator, 0):
            return "creator_cooldown"
        return None

    def _roll_day(self, run: _BookRun, t_ms: int) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime(t_ms / 1000))
        if run.day != day:
            run.day = day
            run.day_pnl = 0
            run.realized = []

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
    ) -> None:
        if reason:
            run.skip_reasons[reason] = run.skip_reasons.get(reason, 0) + 1
        row = {
            "schema": SCHEMA_DECISION,
            "book": run.spec.book_id,
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
        if self.retain_rows:
            self.decisions.append(row)
        log = self.logs.get("decisions")
        if log is not None:
            log.write(row)

    def _fill_through(self, t_ms: int) -> None:
        for run in self.books:
            due = [p for p in run.pending.values() if p.t_entry_ms <= t_ms]
            for pending in due:
                self._fill_one(run, pending)

    def _fill_one(self, run: _BookRun, pending: _Pending) -> None:
        if run.pending.get(pending.mint) is not pending:
            return
        book = self.library.get(pending.mint)
        if book is None:
            run.pending.pop(pending.mint, None)
            return
        if self.kill_file.is_file():
            run.pending.pop(pending.mint, None)
            self._decision(run, book, pending.decision_t_ms, pending.trigger, "skip", "kill_switch", pending.score, pending.hops)
            return
        feats = {"f_tape_last_price_sol": pending.ref_price}
        entry = try_entry(
            book.path,
            t_entry_ms=pending.t_entry_ms,
            size_lamports=pending.size_lamports,
            slippage_cap=self.slippage_cap,
            feats=feats,
        )
        run.pending.pop(pending.mint, None)
        if entry.status != "filled":
            # Same cost the scoreboard keeps inside n: an attempt that does not fill burns priority.
            cost = -PRIORITY_FEE_LAMPORTS
            self._roll_day(run, pending.t_entry_ms)
            run.day_pnl += cost
            run.realized.append(cost)
            run.closed_n += 1
            self._decision(
                run,
                book,
                pending.decision_t_ms,
                pending.trigger,
                "skip",
                entry.status,
                pending.score,
                pending.hops,
                entry_status=entry.status,
            )
            self._position(
                {
                    "schema": SCHEMA_POSITION,
                    "event": "miss",
                    "book": pending.book_id,
                    "mint": pending.mint,
                    "creator": pending.creator,
                    "trigger": pending.trigger,
                    "decision_t_ms": pending.decision_t_ms,
                    "t_entry_ms": pending.t_entry_ms,
                    "entry_status": entry.status,
                    "pnl_lamports": cost,
                    "attempt_cost_lamports": cost,
                    "reason": "priority_fee_on_unfilled_attempt",
                }
            )
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
        )
        run.open[pending.mint] = opened
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
        )
        self._position(
            {
                "schema": SCHEMA_POSITION,
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
            }
        )

    def _exits_through(self, t_ms: int) -> None:
        for run in self.books:
            for mint in list(run.open):
                self._try_exit(run, mint, t_ms)

    def _try_exit(self, run: _BookRun, mint: str, t_ms: int) -> None:
        opened = run.open.get(mint)
        book = self.library.get(mint)
        if opened is None or book is None:
            return
        if isinstance(opened.rule, LadderRule):
            part = simulate_ladder(
                book.path,
                opened.entry,
                opened.rule,
                latency_ms=opened.latency_ms,
                tape_end_ms=t_ms,
                size_lamports=opened.size_lamports,
            )
        else:
            part = simulate_exit(
                book.path,
                opened.entry,
                opened.rule,
                latency_ms=opened.latency_ms,
                tape_end_ms=t_ms,
                size_lamports=opened.size_lamports,
            )
        if part["exit_status"] == "censored":
            return
        if part["exit_status"] not in ("realized", "no_exit_liquidity"):
            return
        run.open.pop(mint, None)
        pnl = part.get("pnl_lamports")
        self._roll_day(run, t_ms)
        if isinstance(pnl, int):
            run.day_pnl += pnl
            run.realized.append(pnl)
        run.closed_n += 1
        ready = t_ms
        run.token_ready[mint] = ready + run.spec.token_cooldown_ms
        if opened.creator:
            run.creator_ready[opened.creator] = ready + run.spec.creator_cooldown_ms
        self._position(
            {
                "schema": SCHEMA_POSITION,
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
            }
        )

    def _position(self, row: dict[str, Any]) -> None:
        if self.retain_rows:
            self.positions.append(row)
        log = self.logs.get("positions")
        if log is not None:
            log.write(row)

    def _prune(self, now_ms: int) -> None:
        busy: set[str] = set()
        for run in self.books:
            busy.update(run.open)
            busy.update(run.pending)
        for mint, book in list(self.library.items()):
            if mint in busy:
                continue
            last = book.flow[-1].t_recv_ms if book.flow else book.create.t_signal_ms
            if now_ms - last < PRUNE_AFTER_MS:
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
            for pr in (last_t0, last_h):
                if pr is not None and pr not in keep:
                    keep.append(pr)
            book.flow = keep
            book.path.prints = [pr.to_tape() for pr in keep]
            self.seen[mint] = {_dedupe_key(pr) for pr in keep}

    def summary(self, t_ms: int | None = None) -> dict[str, Any]:
        when = self._clock_ms if t_ms is None else t_ms
        day = time.strftime("%Y-%m-%d", time.gmtime(when / 1000)) if when else ""
        books = {}
        for run in self.books:
            stats = _stats_from_lamports(run.realized)
            books[run.spec.book_id] = {
                "kind": run.spec.kind,
                "open": len(run.open),
                "pending": len(run.pending),
                "closed": run.closed_n,
                "day": run.day or day,
                "day_pnl_sol": run.day_pnl / LAMPORTS_PER_SOL,
                "realized": stats,
                "skips": dict(run.skip_reasons),
            }
        return {"schema": SCHEMA_PNL, "day": day, "t_ms": when, "books": books, "latency": self.latency.report()}


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
    engine.drain_until(tape_end_ms, final=True)
    return engine


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
    online = [row for row in engine.positions if row.get("book") == book_id and row.get("event") == "close"]
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
            data = fh.read(size - self.offset)
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
    """Follow the open trade hour and the observe day file. No writes to those dirs."""

    def __init__(self, tape_dir: Path, creates_dir: Path, offsets: dict[str, int]) -> None:
        self.tape_dir = tape_dir
        self.creates_dir = creates_dir
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
            kind = "create" if key.startswith("create:") else "trade"
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
        for kind, path in (("trade", trade), ("create", create)):
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
    engine = replay_rows(
        loaded.values(),
        rows,
        config_books,
        tape_end_ms=tape_end_ms,
        kill_file=kill_file,
        model=model,
        barrier=barrier,
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
            run.spec = spec


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
    holdback_ms = int(raw.get("holdback_ms", 300))
    slippage = float(raw.get("slippage_cap", DEFAULT_SLIPPAGE_CAP))
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
    }
    model = ModelSlot(model_path, meta_path)
    model.maybe_reload(force=True)
    barrier = ModelSlot(barrier_path, meta_path)
    barrier.maybe_reload(force=True)
    engine = ForwardEngine(
        books,
        kill_file=kill_file,
        latency=LatencyMeter(now_ms=lambda: int(time.time() * 1000)),
        model=model,
        barrier=barrier,
        slippage_cap=slippage,
        logs={"decisions": logs["decisions"], "positions": logs["positions"]},
    )
    graph_dir = Path(str(raw.get("graph_dir") or "/var/lib/mal/graph"))
    if graph_dir.is_dir():
        engine.graph_dir = graph_dir
    tail = DirectoryTail(tape_dir, creates_dir, offsets)
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
    print(
        f"forward_paper books={','.join(b.book_id for b in books)} kill={kill_file}",
        file=sys.stderr,
    )
    while not stop["flag"]:
        batch = tail.poll()
        watermark = 0
        for kind, row in batch:
            if kind == "create":
                create = create_from_observe_row(row)
                if create is None:
                    continue
                engine.push_create(create)
                watermark = max(watermark, create.t_signal_ms)
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
            try:
                mtime = config_path.stat().st_mtime
            except OSError:
                mtime = config_mtime
            if mtime != config_mtime:
                fresh, kill = reload_risk_config(config_path, books)
                if fresh is not books:
                    books = fresh
                    adopt_book_limits(engine, books)
                    if kill is not None:
                        engine.kill_file = kill
                config_mtime = mtime
            last_model = now
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
