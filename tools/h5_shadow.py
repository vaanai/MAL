#!/usr/bin/env python3
"""H5-BOOSTFLOOR v1 live SHADOW detector. Paper only. No keys, no transactions, no RPC writes, 0 Helius credits.

Frozen rule: /data/mal/hunt-1008/h5-flows/RULE.md (sha256 in RULE_SHA256). In one paragraph: after a non-mayhem graduation the pump.fun
BOOST agent buys 17.585 SOL of the canonical PumpSwap pool in ~29 slices over ~345 s. PumpSwap prices at (real quote + V) / base, V ~ 17.58
SOL, so sells cannot push Q = real quote + V below V. For a pool with V in [17.5, 17.7] SOL, on the FIRST non-BOOST sell at t in [0, 300] s
after the pool's first print that leaves Q <= 40 SOL while BOOST spent < 0.999 x 17.585 SOL, the rule buys (landing slot + ceil(1.3 s / sps),
binding leg ceil(1.9 s / sps)) and sells at s0 + round(330 s / sps) + ceil(0.55 s / sps). One trade per pool.

This process implements the DECISION half of that rule on the live public PumpSwap tape and logs what the rule WOULD have bought and what the
pool looked like at the planned landing and exit slots. It sends nothing. An offline scorer prices the trade with the frozen cost model; the
log also carries the no-fail P&L under the rule's own fill math so a fill can be checked at a glance.

INGEST. observe.trade_source logsSubscribe (public RPC, $0) on the PumpSwap program, decoded with observe.trade_decode (event-V fields, #467).
Every row carries t_recv_ms (local wall clock at receipt) and the slot. The pool is announced by the CreatePoolEvent in the migrate tx's logs
(a PumpSwap pool is only tracked if its CreatePool was seen on this stream; an old pool that happens to trade is never mistaken for a new one).

Q TWO WAYS (disclosed implementation point, not a rule change). Since 2026-09-30 PumpSwap v2 trades keep protocol and creator fees in the vault
and the stored V falls by the same amount, so a fixed V = 17.58 SOL misprices a fresh pool. Variant "pv" (primary) takes V PER PRINT from the
event (Q = quote_reserve + virtual_quote_reserves). Variant "fv" is the frozen rule's literal fixed V (V of the pool's first print). Both are
evaluated on every print, each fires at most once per pool, and every record carries both Qs.

OUTPUT. <out-dir>/h5-shadow-<UTC hour>.jsonl, strict JSON lines flushed per line (tools/tape_lines.py clean), and h5-shadow-status.json
(atomic). Record types: start, trigger, outcome, pool (EVERY tracked pool, with the BOOST last-slice time relative to s0), gap, hb, stop.
A gap record (slot jump, silence, feed restart) also flags every pool open at the time (gap=true on its pool/trigger/outcome records); an
hour with a gap record or a missing hb is a bad hour.

EXIT LADDER. Every outcome record also carries the pool at the exit landing slot for exit triggers at s0 + 310/320/330/335/340/345/350 s
(the rule exits at 330 s; VERIFY.md found the exit sits on a cliff). Report-only; the rule's exit is the 330 s row.

REPLAY. --replay-tape runs the SAME engine over the audit's exploration tape (/data/mal/audit-1008/tape) so the live code can be checked
against the frozen rule's own trigger list (--compare-frozen). The PR body carries the numbers.

Run: python -m tools.h5_shadow --out-dir /var/lib/mal/h5-shadow   (MiScusi job: bash scripts/research/h5-shadow.sh)
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/h5_shadow.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import base64
import collections
import json
import logging
import math
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from observe.trade_decode import (
    PUMPSWAP_PROGRAM,
    WSOL_MINT,
    _program_data_bytes,
    decode_extra_event,
    decode_program_data,
    iso_from_ms,
    records_from_logs,
)
from tools.paper_curve_math import pumpswap_sol_fee_ppm

log = logging.getLogger("mal.h5_shadow")

SCHEMA = "h5_shadow_v1"
RULE_ID = "H5-BOOSTFLOOR v1"
RULE_SHA256 = "c66b1a5990468080d56a97d67619c035cd81e2782eac89c48b94b8c9c9abe56c"  # /data/mal/hunt-1008/h5-flows/RULE.md
DEFAULT_OUT_DIR = os.path.join(os.path.expanduser("~"), "data", "h5-shadow")  # user-writable; no sudo needed on fast-0

# ---- the rule's numbers (RULE.md). Do not edit without a new pre-registration ---------------------------------
V_LO = 17.5e9  # lamports, universe: first print's V in [V_LO, V_HI]
V_HI = 17.7e9
SPS_LO, SPS_HI = 0.15, 0.6  # seconds per slot must lie in this open interval
Q_STAR_SOL = 40.0  # Q = real quote + V after the sell, SOL
T_MIN_S, T_MAX_S = 0.0, 300.0
BOOST_BUDGET = 17.585e9
BOOST_DONE_FRAC = 0.999
BOOST_MIN_BUYS, BOOST_BUY_MIN, BOOST_BUY_MAX, BOOST_TOTAL_MAX = 3, 0.2e9, 2.0e9, 17.7e9
ENTRY_S = {"primary": 1.3, "binding": 1.9}
EXIT_AFTER_S0_S = 330.0
EXIT_LAG_S = 0.55
EXIT_LADDER_S = (310.0, 320.0, 330.0, 335.0, 340.0, 345.0, 350.0)  # report-only exit-shift sensitivity (VERIFY.md: the exit sits on a cliff)
PRESSURE_WINDOW_S = 2.0
PRIO_LAMPORTS = 55_000
STAKES = (("0.1", 100_000_000), ("0.25", 250_000_000))

# ---- implementation constants (not the rule) ---------------------------------------------------------------
POOL_LIFE_S = 400.0  # a pool is followed from its first print to s0 + 400 s, then dropped
OUTCOME_GRACE_SLOTS = 3  # resolve a planned slot X once the global slot high-water mark reaches X + this
WALL_CLOSE_S = 430.0  # wall-clock backstop for closing a pool when the slot clock stalls
LP_FRAC = 0.002  # LP fee stays in the pool; used only when the event lacks lp_fee / pool_quote_amount (tape replay)
CHAIN_TOL = 2e-3  # |Q_pre(i+1) - Q_post(i)| / Q above this counts as a quote-chain break
GAP_SLOTS = 30  # a jump of more than this many slots between consecutive events is logged as a gap
SILENCE_S = 20.0  # no event for this long is logged as a gap
SPS_WINDOW_S = 300
SPS_MIN_SPAN_S = 8  # bootstrap: ready about 10 s after the first PumpSwap print of ANY pool; the fit sharpens as the window fills
SPS_MIN_POINTS = 8
VARIANTS = ("pv", "fv")
STRIP_MAX_ROWS = 20_000  # per triggered pool; a longer strip is cut and flagged
# CAP-PICK seal: the counted walk-2 window starts 2026-10-16T01:00Z. Inside it, a pool's outcome states and price strip are withheld unless a
# pick oracle says the mint is NOT a pick. Fail closed. Decision-time records (trigger) are unaffected.
SEAL_START_MS = int(datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc).timestamp() * 1000)
SEAL_REASON = "cap_pick_seal"
SEEN_TTL_S = 1200.0
ANNOUNCE_TTL_S = 1200.0  # must be >= SEEN_TTL_S: an announcement may not expire before the pool it announced can be forgotten
SETTLE_SLOTS = 2  # a print this close to the high-water slot may still be waiting for a predecessor that is in flight
LOOKS_WINDOW = 3  # socket-drop memory: the last 3 looks (3 x 5 s housekeeping) decide whether every socket was down
REJECT_LOG_MAX = 50  # reject records written per run (the counters are unbounded)
# heuristic only (counter unannounced_fresh): a first-seen print that looks like a new pool's first print
FRESH_REAL_QUOTE_MAX = 100 * 10**9
FRESH_BASE_MIN, FRESH_BASE_MAX = 1.8e14, 2.1e14
ERRORS_MAX_BYTES = 5_000_000
ERROR_RECORDS_MAX = 20

_CREATE_POOL_PREFIX = base64.b64encode(bytes.fromhex("b1310cd2a076a774")).decode()[:10]
_BOOST_EVENT_PREFIX = base64.b64encode(bytes.fromhex("3f451c16305cc2b9")).decode()[:10]


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def tier_fee(q: float, b: float) -> float:
    """Canonical PumpSwap total fee on the market cap of a (q incl. V, b) state, as a fraction."""
    return pumpswap_sol_fee_ppm(q / b * 1e6) / 1e6


def clean(obj: Any) -> Any:
    """JSON-safe copy: NaN / inf become None (strict JSON, allow_nan=False)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def cap_pick_seal_oracle_stub(mint: str | None) -> bool:
    """Placeholder pick oracle: True = suppress. Inside the seal window every mint is treated as a possible pick, so everything is suppressed
    until a real oracle (the pick set, known to the sealed reader only) replaces this."""
    return True


def sps_ok(sps: float | None) -> bool:
    return sps is not None and SPS_LO < sps < SPS_HI


# ---- slot clock ---------------------------------------------------------------------------------------------
class SlotClock:
    """Seconds per slot from (slot, unix block time) pairs of the events themselves: a rolling window of the first slot seen in each
    new block-time second. No RPC. None until the window spans SPS_MIN_SPAN_S."""

    def __init__(self, window_s: int = SPS_WINDOW_S, min_span_s: int = SPS_MIN_SPAN_S, min_points: int = SPS_MIN_POINTS) -> None:
        self.window_s, self.min_span_s, self.min_points = window_s, min_span_s, min_points
        self._pts: collections.deque[tuple[int, int]] = collections.deque()  # (block time s, first slot seen)
        self._dirty, self._cache = True, None

    def observe(self, slot: int, ts: int | None) -> None:
        if not ts:
            return
        if self._pts and ts <= self._pts[-1][0]:
            return
        self._pts.append((int(ts), int(slot)))
        while len(self._pts) > 2 and self._pts[-1][0] - self._pts[0][0] > self.window_s:
            self._pts.popleft()
        self._dirty = True

    def n_points(self) -> int:
        return len(self._pts)

    def span_s(self) -> int:
        return self._pts[-1][0] - self._pts[0][0] if len(self._pts) >= 2 else 0

    def sps(self) -> float | None:
        """Least-squares slope of block time on slot over the window (cached until a new second arrives)."""
        if self._dirty:
            self._dirty, self._cache = False, self._fit()
        return self._cache

    def _fit(self) -> float | None:
        n = len(self._pts)
        if n < self.min_points or self.span_s() < self.min_span_s:
            return None
        t0, s0 = self._pts[0]
        xs = [s - s0 for _, s in self._pts]
        ys = [t - t0 for t, _ in self._pts]
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx <= 0:
            return None
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        return slope if slope > 0 else None


# ---- pool arithmetic ----------------------------------------------------------------------------------------
def post_state(q_pre: float, b_pre: float, buy: bool, sol: int, tok: int, lp_fee: int | None, pool_quote_amount: int | None) -> tuple[float, float]:
    """(delta Q incl. V, delta base) of one print, from its PRE-trade state (q_pre includes V).

    With the event's own fields (live): a buy adds pool_quote_amount + lp_fee to the pool, a sell removes pool_quote_amount - lp_fee
    (the CP gross less the LP fee, which stays). Protocol / creator fees that v2 keeps in the vault move stored V the other way, so Q
    is unchanged by them. Without those fields (tape replay): constant product on the base move, LP fee 0.2 % kept in the pool.
    Base moves exactly by token_raw either way."""
    db = float(-tok if buy else tok)
    if pool_quote_amount:
        lp = lp_fee or 0
        dq = float(pool_quote_amount + lp if buy else -(pool_quote_amount - lp))
        return dq, db
    b_post = b_pre + db
    if b_post <= 0:
        return 0.0, db
    q_cp = q_pre * b_pre / b_post
    dq = (q_cp - q_pre) * (1 + LP_FRAC) if buy else -(q_pre - q_cp) * (1 - LP_FRAC)
    return dq, db


def fill_round_trip(qe: float, be: float, qx: float, bx: float, stake: float) -> tuple[float, float]:
    """(pnl lamports before any fail model, gross) of a buy at landing state (qe, be) and a sell at exit state (qx, bx). Own impact on Q
    incl. V; the buy stays in the pool until the sell; 55,000 lamports on each send. Same arithmetic as RULE.md / s14_boostdip.py."""
    f = tier_fee(qe, be)
    net = stake * (1 - f)
    tk = be * net / (qe + net)
    q2 = qx + net
    b2 = bx - tk
    proceeds = tk * q2 / (b2 + tk) * (1 - tier_fee(q2, b2 + tk))
    return proceeds - stake - 2 * PRIO_LAMPORTS, (q2 / b2) / (qe / be) - 1


class Pr:
    """One PumpSwap print of a tracked pool."""

    __slots__ = ("slot", "buy", "trader", "sol", "tok", "q", "v", "b", "dq", "db", "kept", "recv_ms", "ts", "sig", "v_missing")

    def __init__(self, **kw: Any) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def q_pre(self, variant: str, v0: int) -> float:
        return float(self.q + (self.v if variant == "pv" else v0))

    def q_post(self, variant: str, v0: int) -> float:
        return self.q_pre(variant, v0) + self.dq + (self.kept if variant == "fv" else 0)

    @property
    def b_post(self) -> float:
        return self.b + self.db


def common_down(states: Sequence[dict], now_ms_: int) -> list[tuple[int, int]]:
    """Intervals during which EVERY socket was down. states: observe.link_state snapshots {"up", "down_since_ms", "intervals": [[start, end]...]}.
    A socket that is down right now contributes its open interval [down_since_ms, now]."""
    per: list[list[tuple[int, int]]] = []
    for st in states:
        iv = [(int(a), int(b)) for a, b in st.get("intervals") or []]
        if not st.get("up") and st.get("down_since_ms") is not None:
            iv.append((int(st["down_since_ms"]), int(now_ms_)))
        per.append(sorted(iv))
    if not per:
        return []
    out = per[0]
    for iv in per[1:]:
        nxt: list[tuple[int, int]] = []
        for a1, b1 in out:
            for a2, b2 in iv:
                a, b = max(a1, a2), min(b1, b2)
                if b > a:
                    nxt.append((a, b))
        out = sorted(nxt)
        if not out:
            return []
    return out


class Pool:
    @property
    def base_unresolved(self) -> int:
        return max(0, self.pre_excess - 1)

    def __init__(self, pool: str, mint: str | None, s0: int, first: Pr, v0: int, announced_slot: int | None, pda: str | None) -> None:
        self.pool, self.mint, self.s0, self.v0 = pool, mint, s0, v0
        self.s0_recv_ms, self.s0_ts = first.recv_ms, first.ts
        self.announced_slot = announced_slot
        self.pda = pda  # PDA(["boost_vault", pool], PumpSwap): BOOST's per-pool signer
        self.boost_auth: str | None = None  # learned from a BoostBuyAndBurn event
        self.boost_remaining: int | None = None
        self.boost_event_n = 0
        self.prints: list[Pr] = []
        # trader -> [n_buys, total, min, max, last_slot, last_recv_ms, last_ts, first_slot]
        self.traders: dict[str, list] = {}
        self.sold: set[str] = set()
        self.elig: dict[str, int] = {}
        self.best: str | None = None
        self.trig: dict[str, dict] = {}
        self.pending: list[dict] = []
        self.sps0: float | None = None
        self.sps_last: float | None = None
        self.chain_breaks = 0
        self.base_breaks = 0
        self.chain_max_rel = 0.0
        self.gaps: list[dict] = []
        self.min_q_pv: float | None = None
        self.min_q_fv: float | None = None
        self.no_sps = 0
        self.disagree = 0  # sells in [0, 300] s where the per-print-V and fixed-V Q fall on different sides of 40 SOL
        self.slot_regress = 0  # prints whose slot is lower than the previous print's (arrival order is not slot order)
        # Order-independent chain check over the multiset of base values. Every print's pre-trade base must equal SOME print's post-trade base,
        # except exactly one: the chain head. pre_excess = sum over values of max(0, pre_count - post_count); unresolved = max(0, pre_excess - 1).
        # Every permutation of a complete chain gives 0 (the head is whichever print really came first, however the prints arrived); a dropped
        # mid-life print gives 1. Known blind spot: a missed print is masked when the trigger print's post base equals the missed print's
        # post base (an exact-token round trip between them); this needs zero net token flow and is rare. A dropped HEAD is not caught here;
        # the s0 anchor (announced_before_gap, s0_minus_announced_slots) covers it.
        self.pre_cnt: collections.Counter = collections.Counter()
        self.post_cnt: collections.Counter = collections.Counter()
        self.pre_excess = 0
        self.s0_reanchored_slots = 0  # a print below s0 arrived late and s0 moved down by this many slots (only done before any trigger)
        self.sealed_cache: bool | None = None
        self.skip_logged = False
        self.closed = False


class Engine:
    """The H5 decision logic over decoded PumpSwap rows. No IO: records go to `emit`. Deterministic for a given row sequence."""

    def __init__(
        self,
        emit: Callable[[dict], None],
        *,
        boost_mode: str = "auto",  # auto = per-pool PDA when it signs a buy, else behavioural | pda | behavioural
        sps_fn: Callable[[Pool], float | None] | None = None,  # replay parity: the frozen rule's per-pool sps
        require_announce: bool = True,
        clock: SlotClock | None = None,
        wall: Callable[[], int] = now_ms,
        pda_fn: Callable[[str], str] | None = None,
        suppress_outcome: Callable[[str | None], bool] | None = None,  # seal oracle: True = withhold this mint's outcome / strip
        seal_start_ms: int | None = SEAL_START_MS,  # None disables the seal (replay of exploration data)
    ) -> None:
        if boost_mode not in ("auto", "pda", "behavioural"):
            raise ValueError("boost_mode")
        self._emit, self.boost_mode, self.sps_fn, self.require_announce = emit, boost_mode, sps_fn, require_announce
        self.clock = clock or SlotClock()
        self.wall = wall
        if pda_fn is None:
            from tools.pump_structure_monitor import boost_vault_authority as pda_fn  # pure python, no network
        self.pda_fn = pda_fn
        self.suppress_outcome, self.seal_start_ms = suppress_outcome, seal_start_ms
        self.pools: dict[str, Pool] = {}
        self.announced: collections.OrderedDict[str, tuple[int, int, str | None, str | None]] = collections.OrderedDict()
        self.seen: collections.OrderedDict[str, int] = collections.OrderedDict()  # pool -> recv_ms of first sight (rejected or tracked)
        self.hw_slot: int | None = None
        self.hw_recv_ms: int | None = None
        self.last_event_ms: int | None = None
        self.silence_open = False
        self.last_flag_gap_ms: int | None = None  # time of the last gap that flagged pools; a CreatePool announced before it may have lost first prints
        self.counters: collections.Counter = collections.Counter()
        self.mint_pools: dict[str, set[str]] = {}
        self._feed_prev: dict[int, dict] = {}
        self._looks: collections.deque = collections.deque(maxlen=LOOKS_WINDOW)
        self._prev_look_ms: int | None = None
        self._flagged_starts: collections.deque = collections.deque(maxlen=64)  # starts of common-outage pieces already flagged
        self._sealed_hours: dict[int, int] = {}  # UTC hour index -> sealed decisions (unlabelled aggregate; the only trace of a sealed decision)

    # ---- emit -----------------------------------------------------------------------------------------------
    def emit(self, rec: dict) -> None:
        rec.setdefault("v", 1)
        rec.setdefault("schema", SCHEMA)
        rec.setdefault("t_ms", self.wall())
        self._emit(rec)

    # ---- announcements --------------------------------------------------------------------------------------
    def on_create_pool(self, pool: str, mint: str | None, quote_mint: str | None, slot: int, recv_ms: int) -> None:
        self.counters["create_pool_events"] += 1
        self.announced[pool] = (slot, recv_ms, mint, quote_mint)
        self.announced.move_to_end(pool)
        if mint:
            self.mint_pools.setdefault(mint, set()).add(pool)
        self._evict_announced(recv_ms)

    def _evict_announced(self, now_ms_: int) -> None:
        """Drop announcements older than ANNOUNCE_TTL_S, and the mint_pools entries that pointed at them (bounded memory)."""
        cut = now_ms_ - ANNOUNCE_TTL_S * 1000
        while self.announced:
            pool, (_slot, rms, mint, _q) = next(iter(self.announced.items()))
            if rms >= cut:
                break
            del self.announced[pool]
            pools = self.mint_pools.get(mint) if mint else None
            if pools is not None:
                pools.discard(pool)
                if not pools:
                    del self.mint_pools[mint]

    def on_boost_event(self, ev: dict, slot: int, recv_ms: int) -> None:
        """BoostBuyAndBurn event: learn the BOOST signer and the vault's remaining budget for a tracked pool (cross-check; report only)."""
        self.counters["boost_events"] += 1
        p = self.pools.get(ev.get("pool", ""))
        if p is None:
            return
        p.boost_event_n += 1
        p.boost_auth = ev.get("authority") or p.boost_auth
        p.boost_remaining = int(ev["boost_vault_remaining"])

    # ---- clock ----------------------------------------------------------------------------------------------
    def advance(self, slot: int, recv_ms: int, ts: int | None = None) -> None:
        """Any event (a trade, a failed tx, another program's notice) moves the slot clock; due outcomes resolve and old pools close."""
        self._advance_clock(slot, recv_ms, ts)
        self._housekeep(recv_ms)

    def _advance_clock(self, slot: int, recv_ms: int, ts: int | None) -> None:
        self.counters["events"] += 1
        if self.last_event_ms is not None and self.silence_open:
            self.silence_open = False
        self.last_event_ms = recv_ms
        if ts:
            self.clock.observe(slot, ts)
        if self.hw_slot is not None and slot > self.hw_slot + GAP_SLOTS:
            self._gap("slot_jump", at_ms=recv_ms, from_slot=self.hw_slot, to_slot=slot, missed_slots=slot - self.hw_slot - 1)
        if self.hw_slot is None or slot > self.hw_slot:
            self.hw_slot, self.hw_recv_ms = slot, recv_ms

    def _housekeep(self, recv_ms: int) -> None:
        self._resolve_due()
        self._close_due(recv_ms)

    def tick(self, now: int) -> None:
        """Wall-clock housekeeping (no events needed): silence detection, backstop closes, purge of the seen / announced maps."""
        if self.last_event_ms is not None and not self.silence_open and now - self.last_event_ms > SILENCE_S * 1000:
            self.silence_open = True
            self._gap("silence", at_ms=now, silent_ms=now - self.last_event_ms)
        self._close_due(now)
        self._evict_announced(now)
        self._flush_sealed_hours(now)
        cut = now - SEEN_TTL_S * 1000
        while self.seen and next(iter(self.seen.values())) < cut:
            self.seen.popitem(last=False)

    def feed_restart(self, now: int, reason: str) -> None:
        """The feed was down or restarted: every open pool may have missed prints. Pools announced before this moment are suspect."""
        self._gap("feed_restart", at_ms=now, reason=reason)
        self.hw_slot = None  # the next event is a fresh reference; do not report a slot jump across a known restart

    def _gap(self, kind: str, *, flag_pools: bool = True, at_ms: int | None = None, **kw: Any) -> None:
        self.counters["gaps"] += 1
        rec = {"type": "gap", "kind": kind, "open_pools": len(self.pools), "flags_pools": flag_pools, **kw}
        if flag_pools:
            at = self.wall() if at_ms is None else at_ms
            self.last_flag_gap_ms = at if self.last_flag_gap_ms is None else max(self.last_flag_gap_ms, at)
            for p in self.pools.values():
                p.gaps.append({"kind": kind, **{k: v for k, v in kw.items() if isinstance(v, (int, str))}})
        self.emit(rec)

    def note_feed_stats(self, key: int, snap: dict, now_ms_: int) -> None:
        """Compare the source's reconnect counters with the last look. Any advance is a gap record carrying the cause (close codes, handshake
        rejections, errors, which socket).

        Whether the open pools are FLAGGED depends on whether every socket was down at a common instant. With per-socket link state
        (snap["socket_states"], from observe.link_state: drop -> resubscribe intervals, an unsubscribed socket is down since its drop) the
        common instant is exact, and the flag's time is the real start of the common outage, not the look time. Without link state
        (a stateless snapshot) the fallback is the counter rule: distinct sockets that reconnected over the last LOOKS_WINDOW looks reach
        the socket count. A single socket always flags."""
        if key not in self._feed_prev:  # a rebuilt source starts at zero: only the current one is tracked
            self._looks.clear()
            self._flagged_starts.clear()
        prev = self._feed_prev.get(key) or {"reconnects": 0, "closes": {}, "rejections": {}, "errors": {}, "per_socket": []}
        self._feed_prev = {key: snap}
        delta = snap["reconnects"] - prev["reconnects"]
        n = int(snap.get("sockets") or 1)
        ps_prev = prev.get("per_socket") or [0] * n
        per_socket = [c - (ps_prev[i] if i < len(ps_prev) else 0) for i, c in enumerate(snap.get("per_socket") or [])]
        self._looks.append(per_socket)  # every look is remembered, with or without a reconnect
        states = snap.get("socket_states")
        pieces: list[tuple[int, int]] = []
        if states:
            pieces = [(a, b) for a, b in common_down(states, now_ms_) if a not in self._flagged_starts and (self._prev_look_ms is None or b > self._prev_look_ms)]
        self._prev_look_ms = now_ms_
        if delta <= 0 and not pieces:
            return
        down = sorted({i for look in self._looks for i, d in enumerate(look) if d > 0})  # distinct sockets that reconnected in the last LOOKS_WINDOW looks
        if states:
            flag, at = bool(pieces), (min(a for a, _ in pieces) if pieces else now_ms_)
            for a, _ in pieces:
                self._flagged_starts.append(a)
            extra = {"common_down_ms": sum(b - a for a, b in pieces), "common_down_start_ms": at if pieces else None, "link_state": True}
        else:
            flag, at, extra = (n == 1 or len(down) >= n), now_ms_, {"link_state": False}

        def dd(a: dict, b: dict) -> dict:
            return {k: v - b.get(k, 0) for k, v in a.items() if v > b.get(k, 0)}

        self._gap("socket_reconnect", flag_pools=flag, at_ms=at, sockets_down_recent=down, delta_reconnects=delta, reconnects_total=snap["reconnects"],
                  sockets=n, redundant=n > 1, per_socket_delta=per_socket, delta_closes=dd(snap["closes"], prev["closes"]),
                  delta_rejections=dd(snap["rejections"], prev["rejections"]), delta_errors=dd(snap["errors"], prev["errors"]), **extra)

    # ---- trades ---------------------------------------------------------------------------------------------
    def on_trade(self, row: dict) -> None:
        """One decoded PumpSwap Buy/Sell row (observe.trade_decode.seal_trade shape; event-V keys optional)."""
        if row.get("venue") != "pumpswap" or not row.get("pool"):
            return
        slot, recv_ms, ts = int(row["slot"]), int(row["t_recv_ms"]), row.get("event_ts")
        self._advance_clock(slot, recv_ms, ts)
        self.counters["pumpswap_prints"] += 1
        pool = row["pool"]
        p = self.pools.get(pool)
        if p is None and pool not in self.seen:
            p = self._maybe_track(row, slot, recv_ms)
        if p is not None:
            self._print_row(p, row, slot, recv_ms, ts)
        self._housekeep(recv_ms)  # after the print: an END-bound state wants the first print past the planned slot, if it is this one

    def _print_row(self, p: Pool, row: dict, slot: int, recv_ms: int, ts: int | None) -> None:
        buy = row["side"] == "buy"
        v_raw = row.get("virtual_quote_reserves")
        q, b = int(row["quote_reserve"]), int(row["base_reserve"])
        v = int(v_raw) if v_raw is not None else p.v0
        dq, db = post_state(q + v, b, buy, int(row["sol_lamports"]), int(row["token_raw"]), row.get("lp_fee"), row.get("pool_quote_amount"))
        kept = 0
        if row.get("fee_recipient_zero"):
            kept = int(row.get("protocol_fee") or 0) + int(row.get("creator_fee") or 0)
        pr = Pr(slot=slot, buy=buy, trader=row.get("trader") or "", sol=int(row["sol_lamports"]), tok=int(row["token_raw"]), q=q, v=v, b=b, dq=dq,
                db=db, kept=kept, recv_ms=recv_ms, ts=ts, sig=row.get("signature"), v_missing=v_raw is None)
        self._on_print(p, pr)

    def _maybe_track(self, row: dict, slot: int, recv_ms: int) -> Pool | None:
        pool = row["pool"]
        self.seen[pool] = recv_ms
        v_raw = row.get("virtual_quote_reserves")
        if v_raw is None:
            self.counters["rejected_no_event_v"] += 1
            return None
        if not (V_LO <= v_raw <= V_HI):  # V range first: most pools on the tape are not BOOST-era graduations at all
            self.counters["rejected_v_range"] += 1
            return None
        ann = self.announced.get(pool)
        if ann is None and self.require_announce:
            self.counters["rejected_unannounced"] += 1
            q, b = int(row["quote_reserve"]), int(row["base_reserve"])
            if q <= FRESH_REAL_QUOTE_MAX and FRESH_BASE_MIN <= b <= FRESH_BASE_MAX:  # heuristic: a first print of a pool that looks new
                self.counters["unannounced_fresh"] += 1
            return None
        if ann is not None and ann[3] is not None and ann[3] != WSOL_MINT:
            self.counters["rejected_quote_mint"] += 1
            if self.counters["reject_records"] < REJECT_LOG_MAX:
                self.counters["reject_records"] += 1
                self.emit({"type": "reject", "reason": "quote_mint", "pool": pool, "slot": slot, "base_mint": ann[2], "quote_mint": ann[3],
                           "wsol_expected": WSOL_MINT, "v": int(v_raw)})
            return None
        mint = ann[2] if ann else row.get("mint")
        first = Pr(recv_ms=recv_ms, ts=row.get("event_ts"))
        try:
            pda = self.pda_fn(pool)
        except Exception:  # noqa: BLE001 - a bad pubkey string must not stop the feed
            pda = None
        p = Pool(pool, mint, slot, first, int(v_raw), ann[0] if ann else None, pda)
        p.sps0 = self._sps(p)
        self.pools[pool] = p
        self.counters["pools_tracked"] += 1
        if mint and len(self.mint_pools.get(mint, ())) > 1:
            p.gaps.append({"kind": "ambiguous_mint"})
        if ann is not None and self.last_flag_gap_ms is not None and ann[1] < self.last_flag_gap_ms:
            p.gaps.append({"kind": "announced_before_gap"})  # first prints may have been lost in a gap between the CreatePool and this s0
        return p

    def _sps(self, p: Pool) -> float | None:
        s = self.sps_fn(p) if self.sps_fn else self.clock.sps()
        p.sps_last = s
        return s

    def _on_print(self, p: Pool, pr: Pr) -> None:
        # chain: base reserves are exact, so a mismatch means a missed or reordered print; the quote chain is informational
        if p.prints:
            prev = p.prints[-1]
            if pr.slot < prev.slot:
                p.slot_regress += 1
                self.counters["slot_regress"] += 1
            if pr.b != prev.b_post:
                p.base_breaks += 1
                self.counters["base_breaks"] += 1
            qa, qp = pr.q_pre("pv", p.v0), prev.q_post("pv", p.v0)
            rel = abs(qa - qp) / max(qa, 1.0)
            p.chain_max_rel = max(p.chain_max_rel, rel)
            if rel > CHAIN_TOL:
                p.chain_breaks += 1
                self.counters["chain_breaks"] += 1
        if p.prints and pr.slot < p.s0:
            self._below_s0(p, pr)
        self._base_chain(p, pr)
        p.prints.append(pr)
        qpv, qfv = pr.q_post("pv", p.v0), pr.q_post("fv", p.v0)
        if pr.slot - p.s0 <= 1500:  # first 300 s at the fastest slot time the rule allows (0.2 s); a monitoring field only
            p.min_q_pv = qpv if p.min_q_pv is None else min(p.min_q_pv, qpv)
            p.min_q_fv = qfv if p.min_q_fv is None else min(p.min_q_fv, qfv)
        self._boost_update(p, pr)
        if not pr.buy:
            self._eval(p, pr, len(p.prints) - 1)

    def unresolved_settled(self, p: Pool) -> int:
        """Same multiset check, but only the pre-trade bases of SETTLED prints (slot <= high-water - SETTLE_SLOTS) need a matching post-trade base
        (posts of every print count). base_breaks_unresolved counts the arrival prefix as it stands, so a predecessor still in flight in the last
        slot or two reads as a break at decision time; this one waits for those prints to settle and still catches a print missing for longer.
        One chain head is subtracted. With no settled print it is 0."""
        if self.hw_slot is None:
            return p.base_unresolved
        posts = collections.Counter(int(r.b_post) for r in p.prints)
        pres = collections.Counter(int(r.b) for r in p.prints if r.slot <= self.hw_slot - SETTLE_SLOTS)
        return max(0, sum(max(0, n - posts.get(v, 0)) for v, n in pres.items()) - 1)

    def _below_s0(self, p: Pool, pr: Pr) -> None:
        """A print with a slot below s0 arrived after s0 was set (the pool's true first print was delivered late). Before any trigger this is a
        pure reorder across slots and s0 moves down to it (safe: sells already evaluated against the later s0 had a smaller t, so none that
        qualifies under the true s0 was skipped). After a trigger the exit plan is already anchored on the old s0, so the pool is flagged."""
        if p.trig:
            p.gaps.append({"kind": "slot_below_s0", "s0": p.s0, "slot": pr.slot})
            self.counters["slot_below_s0_after_trigger"] += 1
            return
        p.s0_reanchored_slots += p.s0 - pr.slot
        p.s0 = pr.slot
        if pr.ts:
            p.s0_ts = pr.ts
        p.sealed_cache = None  # the seal window is judged on the s0 block time
        self.counters["s0_reanchored"] += 1

    @staticmethod
    def _base_chain(p: Pool, pr: Pr) -> None:
        """Multiset counts of pre- and post-trade base reserves, incremental. pre_excess = sum over values of max(0, pres - posts)."""
        bpre, bpost = int(pr.b), int(pr.b_post)
        p.pre_cnt[bpre] += 1
        if p.pre_cnt[bpre] > p.post_cnt[bpre]:
            p.pre_excess += 1
        old = p.post_cnt[bpost]
        p.post_cnt[bpost] += 1
        if p.pre_cnt[bpost] > old:
            p.pre_excess -= 1

    # ---- BOOST tracking -------------------------------------------------------------------------------------
    def _boost_update(self, p: Pool, pr: Pr) -> None:
        t = pr.trader
        if not pr.buy:
            p.sold.add(t)
            if t in p.elig:
                del p.elig[t]
                if p.best == t:
                    p.best = self._recompute_best(p)
            return
        st = p.traders.get(t)
        if st is None:
            st = p.traders[t] = [0, 0, pr.sol, pr.sol, pr.slot, pr.recv_ms, pr.ts, pr.slot]
        st[0] += 1
        st[1] += pr.sol
        st[2], st[3] = min(st[2], pr.sol), max(st[3], pr.sol)
        st[4], st[5], st[6] = pr.slot, pr.recv_ms, pr.ts
        ok = t not in p.sold and st[0] >= BOOST_MIN_BUYS and st[2] >= BOOST_BUY_MIN and st[3] <= BOOST_BUY_MAX and st[1] <= BOOST_TOTAL_MAX
        if ok:
            p.elig[t] = st[0]
            b = p.best
            if b is None or b not in p.elig or (st[0], _neg(t)) > (p.elig[b], _neg(b)):
                p.best = t
        elif t in p.elig:
            del p.elig[t]
            if p.best == t:
                p.best = self._recompute_best(p)

    @staticmethod
    def _recompute_best(p: Pool) -> str | None:
        if not p.elig:
            return None
        return max(p.elig, key=lambda k: (p.elig[k], _neg(k)))

    def boost_identity(self, p: Pool) -> tuple[str | None, str]:
        """(BOOST signer, source). The per-pool PDA when it has signed a buy (or a BoostBuyAndBurn event named it); else the RULE.md
        behavioural detector (buy-only, >= 3 buys of 0.2..2.0 SOL, total <= 17.7 SOL, most buys wins), causal."""
        if self.boost_mode in ("auto", "pda"):
            for cand, src in ((p.boost_auth, "event_authority"), (p.pda, "pda")):
                if cand and p.traders.get(cand, [0])[0] > 0:
                    return cand, src
            if self.boost_mode == "pda":
                return None, "none"
        if p.best is not None:
            return p.best, "behavioural"
        return None, "none"

    def boost_spent(self, p: Pool) -> tuple[int, str | None, str]:
        ident, src = self.boost_identity(p)
        return (p.traders[ident][1] if ident else 0), ident, src

    # ---- trigger --------------------------------------------------------------------------------------------
    def _eval(self, p: Pool, pr: Pr, idx: int) -> None:
        if len(p.trig) == len(VARIANTS):
            return
        sps = self._sps(p)
        if not sps_ok(sps):
            p.no_sps += 1
            self.counters["no_sps_evals"] += 1
            if not p.skip_logged and min(pr.q_post("pv", p.v0), pr.q_post("fv", p.v0)) / 1e9 <= Q_STAR_SOL:
                spent, ident, src = self.boost_spent(p)
                if not (ident is not None and pr.trader == ident) and spent < BOOST_BUDGET * BOOST_DONE_FRAC:
                    p.skip_logged = True  # once per pool: this sell would have been evaluated as a trigger candidate if sps had been known
                    if self._sealed(p):  # EXP-022 s9: no per-pool trace of a decision; only the unlabelled hourly aggregate
                        self._note_sealed_decision(pr.recv_ms)
                        return
                    self.counters["skipped_no_sps_would_trigger"] += 1
                    self.emit({"type": "skipped_no_sps", "pool": p.pool, "mint": p.mint, "s0": p.s0, "slot": pr.slot, "slot_offset": pr.slot - p.s0,
                               "sps": sps, "t_recv_ms": pr.recv_ms, "signature": pr.sig, "q_pv_post_sol": pr.q_post("pv", p.v0) / 1e9,
                               "q_fv_post_sol": pr.q_post("fv", p.v0) / 1e9, "boost_spent_sol": spent / 1e9, "boost_src": src})
            return
        t = (pr.slot - p.s0) * sps
        if not (T_MIN_S <= t <= T_MAX_S):
            return
        if (pr.q_post("pv", p.v0) / 1e9 <= Q_STAR_SOL) != (pr.q_post("fv", p.v0) / 1e9 <= Q_STAR_SOL) and not self._sealed(p):
            p.disagree += 1
            self.counters["pv_fv_disagree_sells"] += 1
        spent, ident, src = self.boost_spent(p)
        if ident is not None and pr.trader == ident:
            return
        if not (spent < BOOST_BUDGET * BOOST_DONE_FRAC):
            return
        for var in VARIANTS:
            if var in p.trig:
                continue
            if pr.q_post(var, p.v0) / 1e9 <= Q_STAR_SOL:
                self._fire(p, pr, idx, var, sps, t, spent, ident, src)

    def _fire(self, p: Pool, pr: Pr, idx: int, var: str, sps: float, t: float, spent: int, ident: str | None, src: str) -> None:
        k_p = math.ceil(ENTRY_S["primary"] / sps - 1e-9)
        k_b = math.ceil(ENTRY_S["binding"] / sps - 1e-9)
        el = math.ceil(EXIT_LAG_S / sps - 1e-9)
        exit_slot = p.s0 + int(round(EXIT_AFTER_S0_S / sps))
        detect = self.wall()
        if self._sealed(p):  # EXP-022 s9 (from 2026-10-16T01Z): no per-pool trigger record, no counters, no pending outcome, no strip
            p.trig[var] = {"sealed": True}
            self._note_sealed_decision(pr.recv_ms)
            return
        rec = {
            "type": "trigger", "variant": var, "pool": p.pool, "mint": p.mint, "s0": p.s0, "s0_t_recv_ms": p.s0_recv_ms,
            "slot": pr.slot, "signature": pr.sig, "t_since_s0_s": t, "sps": sps,
            "block_time": pr.ts, "t_recv_ms": pr.recv_ms, "t_recv": iso_from_ms(pr.recv_ms), "t_detect_ms": detect,
            "detect_lag_ms": detect - pr.recv_ms,
            "q_trigger_sol": pr.q_post(var, p.v0) / 1e9, "q_pv_post_sol": pr.q_post("pv", p.v0) / 1e9, "q_fv_post_sol": pr.q_post("fv", p.v0) / 1e9,
            "q_pv_pre_sol": pr.q_pre("pv", p.v0) / 1e9, "q_fv_pre_sol": pr.q_pre("fv", p.v0) / 1e9,
            "pv_fv_disagree_at_trigger": (pr.q_post("pv", p.v0) / 1e9 <= Q_STAR_SOL) != (pr.q_post("fv", p.v0) / 1e9 <= Q_STAR_SOL),
            "real_quote_pre": pr.q, "v_print": pr.v, "v0": p.v0, "base_pre": pr.b, "sell_token_raw": pr.tok, "sell_user_out": pr.sol,
            "boost_spent_sol": spent / 1e9, "boost_remaining_sol": (BOOST_BUDGET - spent) / 1e9, "boost_id": ident, "boost_src": src,
            "boost_vault_remaining_sol": None if p.boost_remaining is None else p.boost_remaining / 1e9,
            "landing_slot_primary": pr.slot + k_p, "landing_slot_binding": pr.slot + k_b, "entry_slots": {"primary": k_p, "binding": k_b},
            "exit_trigger_slot": exit_slot, "exit_landing_slot": exit_slot + el, "exit_lag_slots": el,
            "prints_seen": len(p.prints), "chain_breaks": p.chain_breaks, "base_breaks": p.base_breaks, "slot_regress": p.slot_regress,
            "base_breaks_unresolved": p.base_unresolved, "base_breaks_unresolved_settled": self.unresolved_settled(p),
            "announced_slot": p.announced_slot,
            "s0_minus_announced_slots": None if p.announced_slot is None else p.s0 - p.announced_slot,
            "s0_reanchored_slots": p.s0_reanchored_slots,
            "sps_n": None if self.sps_fn else self.clock.n_points(), "sps_span_s": None if self.sps_fn else self.clock.span_s(),
            "gap": bool(p.gaps), "gaps": p.gaps[:5], "announced": p.announced_slot is not None, "v_missing": pr.v_missing,
        }
        p.trig[var] = rec
        ladder = {T: p.s0 + int(round(T / sps)) for T in EXIT_LADDER_S}  # exit trigger slot per ladder point
        rec["exit_ladder_trigger_slots"] = {str(int(T)): v for T, v in ladder.items()}
        p.pending.append({"variant": var, "idx": idx, "sps": sps, "k": {"primary": k_p, "binding": k_b}, "exit_slot": exit_slot, "exit_land": exit_slot + el,
                          "trig_slot": pr.slot, "el": el, "ladder": ladder, "resolve_at": max(ladder.values()) + el})
        self.counters[f"triggers_{var}"] += 1
        self.emit(rec)

    # ---- outcomes -------------------------------------------------------------------------------------------
    def _state_at(self, p: Pool, X: int, var: str) -> tuple[float, float, str]:
        """END bound: the pool after every print in slots <= X = pre-trade state of the first later print, else the last print's post state."""
        for pr in p.prints:
            if pr.slot > X:
                return pr.q_pre(var, p.v0), float(pr.b), "next_pre"
        last = p.prints[-1]
        return last.q_post(var, p.v0), last.b_post, "post"

    def _resolve_due(self) -> None:
        if self.hw_slot is None:
            return
        for p in self.pools.values():
            if p.pending:
                for pend in [x for x in p.pending if self.hw_slot >= x["resolve_at"] + OUTCOME_GRACE_SLOTS]:
                    self._resolve(p, pend, final=False)

    def _resolve(self, p: Pool, pend: dict, final: bool) -> None:
        if pend not in p.pending:
            return
        p.pending.remove(pend)
        if self._sealed(p):  # unreachable for a sealed pool (_fire creates no pending outcome); silent if it ever happens
            return
        var, sps, X_exit = pend["variant"], pend["sps"], pend["exit_land"]
        complete = (self.hw_slot is not None and self.hw_slot >= pend["resolve_at"]) or not final
        legs: dict[str, Any] = {}
        qx, bx, xsrc = self._state_at(p, X_exit, var)
        w = int(round(PRESSURE_WINDOW_S / sps))
        for leg, k in pend["k"].items():
            X = pend["trig_slot"] + k
            qe, be, esrc = self._state_at(p, X, var)
            ssb = sum(1 for r in p.prints if r.buy and r.slot == X)
            nb = sum(r.sol for r in p.prints if r.buy and X - w <= r.slot <= X)
            entry = {"landing_slot": X, "q_sol": qe / 1e9, "base": be, "price_lamports_per_raw": qe / be, "state_src": esrc, "ssb": ssb, "nb_lamports": nb}
            if pend["exit_slot"] <= X:
                entry["net"] = None
                entry["note"] = "exit_trigger_not_after_landing"
            else:
                entry["net"] = {}
                for label, stake in STAKES:
                    pnl, gross = fill_round_trip(qe, be, qx, bx, float(stake))
                    entry["net"][label] = {"pnl_nofail_lamports": pnl, "net_pct_nofail": 100 * pnl / stake, "gross": gross}
            legs[leg] = entry
        ladder_out: dict[str, Any] = {}
        for T, trig_slot in pend["ladder"].items():
            land = trig_slot + pend["el"]
            lq, lb, lsrc = self._state_at(p, land, var)
            row = {"trigger_slot": trig_slot, "landing_slot": land, "q_sol": lq / 1e9, "base": lb, "state_src": lsrc}
            for leg in ("primary", "binding"):
                ent = legs[leg]
                if trig_slot > ent["landing_slot"]:
                    pnl, _ = fill_round_trip(ent["q_sol"] * 1e9, ent["base"], lq, lb, float(STAKES[0][1]))
                    row[f"net_pct_{leg}_0.1"] = 100 * pnl / STAKES[0][1]
            ladder_out[str(int(T))] = row
        rec = {
            "type": "outcome", "variant": var, "pool": p.pool, "mint": p.mint, "s0": p.s0, "trigger_slot": pend["trig_slot"], "sps": sps,
            "exit": {"trigger_slot": pend["exit_slot"], "landing_slot": X_exit, "q_sol": qx / 1e9, "base": bx, "state_src": xsrc},
            "legs": legs, "exit_ladder": ladder_out, "complete": bool(complete), "hw_slot": self.hw_slot, "prints_seen": len(p.prints),
            "chain_breaks": p.chain_breaks, "base_breaks": p.base_breaks, "gap": bool(p.gaps), "gaps": p.gaps[:5],
        }
        self.counters["outcomes"] += 1
        if not complete:
            self.counters["outcomes_incomplete"] += 1
        self.emit(rec)

    def _sealed(self, p: Pool) -> bool:
        """True when this pool's post-decision states must not be written. Fail closed: no mint, no oracle, or an oracle that raises inside
        the window all suppress. Before the window nothing is suppressed."""
        if p.sealed_cache is not None:
            return p.sealed_cache
        p.sealed_cache = self._sealed_now(p)
        return p.sealed_cache

    def _sealed_now(self, p: Pool) -> bool:
        if self.seal_start_ms is None:
            return False
        t = p.s0_ts * 1000 if p.s0_ts else p.s0_recv_ms
        if t < self.seal_start_ms:
            return False
        if p.mint is None or self.suppress_outcome is None:
            return True
        try:
            return bool(self.suppress_outcome(p.mint))
        except Exception:  # noqa: BLE001 - fail closed
            self.counters["seal_oracle_errors"] += 1
            return True

    def _emit_strip(self, p: Pool, sealed: bool) -> None:
        """Compact per-print pre-trade states of a TRIGGERED pool from its first trigger print to the end of its life, so an offline scorer can
        reprice any landing / exit slot exactly (state at X = pre of the first row with slot > X, else post_last), e.g. s0 + round(330 / sps_path)."""
        if not p.trig:
            return
        if sealed:  # EXP-022 s9: not even a stub (its existence would say the pool triggered)
            return
        start = min(r["slot"] for r in p.trig.values())
        base = {"type": "strip", "pool": p.pool, "mint": p.mint, "s0": p.s0, "from_slot": start, "variants": sorted(p.trig)}
        prints = [r for r in p.prints if r.slot >= start]
        rows = [[r.slot, int(round(r.q_pre("pv", p.v0))), int(round(r.q_pre("fv", p.v0))), int(r.b)] for r in prints[:STRIP_MAX_ROWS]]
        last = p.prints[-1]
        self.counters["strips"] += 1
        self.emit({**base, "sps_path": self._sps_path(p), "sps_trigger": {v: r["sps"] for v, r in p.trig.items()}, "n_prints": len(p.prints),
                   "cols": ["slot", "q_pv_pre", "q_fv_pre", "base_pre"], "rows": rows, "truncated": len(prints) > STRIP_MAX_ROWS,
                   "post_last": [last.slot, int(round(last.q_post("pv", p.v0))), int(round(last.q_post("fv", p.v0))), int(last.b_post)]})

    # ---- pool close -----------------------------------------------------------------------------------------
    def _close_due(self, now_ms_: int) -> None:
        for pool in [k for k, p in self.pools.items() if self._due(p, now_ms_)]:
            self._close(self.pools[pool], "horizon")

    def _due(self, p: Pool, now_ms_: int) -> bool:
        sps = p.sps_last or p.sps0 or 0.4
        if self.hw_slot is not None and self.hw_slot >= p.s0 + int(round(POOL_LIFE_S / sps)) + OUTCOME_GRACE_SLOTS:
            return True
        return now_ms_ - p.s0_recv_ms > WALL_CLOSE_S * 1000

    def _close(self, p: Pool, reason: str) -> None:
        for pend in list(p.pending):
            self._resolve(p, pend, final=True)
        sealed = self._sealed(p)
        self._emit_strip(p, sealed)
        ident, src = self.boost_identity(p)
        if ident is None and p.best is not None:
            ident, src = p.best, "behavioural"
        st = p.traders.get(ident) if ident else None
        sps = p.sps_last or p.sps0
        last_rel = last_rel_ts = last_rel_recv = first_rel = None
        if st is not None:
            if sps_ok(sps):
                last_rel = (st[4] - p.s0) * sps
                first_rel = (st[7] - p.s0) * sps
            if st[6] and p.s0_ts:
                last_rel_ts = st[6] - p.s0_ts
            last_rel_recv = (st[5] - p.s0_recv_ms) / 1000
        rec = {
            "type": "pool", "reason": reason, "pool": p.pool, "mint": p.mint, "s0": p.s0, "s0_t_recv_ms": p.s0_recv_ms, "s0_block_time": p.s0_ts,
            "announced_slot": p.announced_slot, "v0": p.v0, "prints": len(p.prints), "buys": sum(1 for r in p.prints if r.buy),
            "sps_at_s0": p.sps0, "sps_last": p.sps_last, "no_sps_evals": p.no_sps,
            "boost_id": ident, "boost_src": src, "boost_pda": p.pda, "boost_event_authority": p.boost_auth,
            "boost_slices": None if st is None else st[0], "boost_total_sol": None if st is None else st[1] / 1e9,
            "boost_first_slice_s": first_rel, "boost_last_slice_s": last_rel, "boost_last_slice_s_blocktime": last_rel_ts,
            "boost_last_slice_s_recv": last_rel_recv, "boost_last_slice_slot": None if st is None else st[4],
            "boost_vault_remaining_sol": None if p.boost_remaining is None else p.boost_remaining / 1e9,
            "min_q_pv_sol": None if (sealed or p.min_q_pv is None) else p.min_q_pv / 1e9,
            "min_q_fv_sol": None if (sealed or p.min_q_fv is None) else p.min_q_fv / 1e9, "sealed": sealed,
            "sps_path": self._sps_path(p), "pv_fv_disagree_sells": None if sealed else p.disagree,
            "triggered": None if sealed else sorted(p.trig), "chain_breaks": p.chain_breaks, "base_breaks": p.base_breaks, "slot_regress": p.slot_regress,
            "base_breaks_unresolved": p.base_unresolved, "base_breaks_unresolved_settled": self.unresolved_settled(p),
            "s0_minus_announced_slots": None if p.announced_slot is None else p.s0 - p.announced_slot,
            "s0_reanchored_slots": p.s0_reanchored_slots,
            "chain_max_rel_err": p.chain_max_rel,
            "gap": bool(p.gaps), "gaps": p.gaps[:5],
        }
        self.emit(rec)
        p.closed = True
        del self.pools[p.pool]

    @staticmethod
    def _sps_path(p: Pool) -> float | None:
        """The pool's own seconds per slot over the prints it was followed for (the rule's per-pool sps, on this pool's first 400 s only)."""
        pts = [(r.slot, r.ts) for r in p.prints if r.ts]
        if len(pts) < 3 or pts[-1][0] <= pts[0][0] + 300:
            return None
        return (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])

    def close_all(self, reason: str) -> None:
        for p in list(self.pools.values()):
            self._close(p, reason)
        self._flush_sealed_hours(self.wall(), final=True)

    def _note_sealed_decision(self, at_ms: int) -> None:
        h = int(at_ms) // 3_600_000
        self._sealed_hours[h] = self._sealed_hours.get(h, 0) + 1

    def _flush_sealed_hours(self, now_ms_: int, final: bool = False) -> None:
        """One unlabelled record per completed UTC hour: how many sealed decisions (trigger or skipped candidates) happened. No pool, mint, slot or
        variant. The only trace of a sealed decision."""
        cur = int(now_ms_) // 3_600_000
        for h in sorted(k for k in self._sealed_hours if final or k < cur):
            n = self._sealed_hours.pop(h)
            hour = datetime.fromtimestamp(h * 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H")
            self.emit({"type": "sealed_hour", "hour": hour, "decisions": n, "partial": bool(final and h >= cur)})


def _neg(s: str) -> tuple:
    """Sort key that makes max() pick the lexicographically lowest id on a tie."""
    return tuple(-ord(c) for c in s)


# ---- output ---------------------------------------------------------------------------------------------------
class JsonlSink:
    """Hourly strict-JSON-lines files, flushed per line. The hour is the UTC hour of the record's t_ms."""

    def __init__(self, out_dir: str | Path, prefix: str = "h5-shadow") -> None:
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self._fh = None
        self._hour: str | None = None
        self.lines = 0

    def path_for(self, t_ms: int) -> Path:
        hour = datetime.fromtimestamp(t_ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")
        return self.dir / f"{self.prefix}-{hour}.jsonl"

    def write(self, rec: dict) -> None:
        t_ms = int(rec.get("t_ms") or now_ms())
        path = self.path_for(t_ms)
        if self._hour != path.name:
            if self._fh:
                self._fh.close()
            self._fh = open(path, "a", encoding="utf-8")
            self._hour = path.name
        self._fh.write(json.dumps(clean(rec), separators=(",", ":"), allow_nan=False) + "\n")
        self._fh.flush()
        self.lines += 1

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


class ErrorLog:
    """Full tracebacks of engine / decode failures, kept out of the JSONL. Capped so a stuck bug cannot fill the disk."""

    def __init__(self, path: str | Path, max_bytes: int = ERRORS_MAX_BYTES) -> None:
        self.path, self.max_bytes, self.bytes, self.dropped = Path(path), max_bytes, 0, 0

    def log(self, exc: BaseException, ctx: dict | None = None) -> None:
        if self.bytes >= self.max_bytes:
            self.dropped += 1
            return
        head = json.dumps(clean({"t_ms": now_ms(), "exc": type(exc).__name__, "msg": str(exc)[:300], **(ctx or {})}), allow_nan=False)
        text = head + "\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(text)
        self.bytes += len(text)


def _strip_url(u: str) -> str:
    """scheme://host/path only: no userinfo, query or fragment (public RPC URLs can carry an api-key query)."""
    try:
        sp = urlsplit(u)
    except ValueError:
        return "REDACTED"
    if not sp.scheme or not sp.hostname:
        return "REDACTED"
    return f"{sp.scheme}://{sp.hostname}{':' + str(sp.port) if sp.port else ''}{sp.path}" + ("?REDACTED" if sp.query else "")


def redact_argv(argv: Sequence[str]) -> list[str]:
    out: list[str] = []
    nxt = False
    for a in argv:
        if nxt:
            out.append(_strip_url(a))
            nxt = False
        elif a == "--ws-url":
            out.append(a)
            nxt = True
        elif a.startswith("--ws-url="):
            out.append("--ws-url=" + _strip_url(a.split("=", 1)[1]))
        else:
            out.append(a)
    return out


def check_out_dir(path: str) -> Path:
    if ".." in Path(path).parts:
        raise ValueError(f"out dir {path!r} contains '..'")
    return Path(path)


def feed_snapshot(source: Any) -> dict | None:
    """Reconnect counters of a trade_source feed (single socket or merged), merged across sockets, for note_feed_stats."""
    stats = getattr(source, "stats", None)
    if stats is None or not isinstance(getattr(stats, "reconnects", None), int):
        return None
    socks = getattr(stats, "sockets", None)
    parts = list(socks) if socks else [stats]

    def merged(attr: str) -> dict:
        out: dict[str, int] = {}
        for s in parts:
            for k, v in (getattr(s, attr, None) or {}).items():
                out[str(k)] = out.get(str(k), 0) + int(v)
        return out

    links = [getattr(s, "link", None) for s in parts]
    snap = {"reconnects": stats.reconnects, "closes": merged("closes"), "rejections": merged("rejections"), "errors": merged("errors"),
            "per_socket": [int(getattr(s, "reconnects", 0)) for s in parts], "sockets": len(parts)}
    if all(l is not None for l in links):
        snap["socket_states"] = [l.snapshot() for l in links]
    return snap


def write_status(path: Path, status: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean(status), indent=1, sort_keys=True, allow_nan=False) + "\n")
    os.replace(tmp, path)


# ---- notice decode (live) -------------------------------------------------------------------------------------
def decode_notice(engine: Engine, note: Any, pool_mints: dict[str, tuple[str, str]]) -> int:
    """One RawNotice (observe.trade_source) into the engine. Returns the number of PumpSwap prints fed. Failed txs are skipped."""
    if note.failed:
        engine.advance(note.slot, note.t_recv_ms, None)
        return 0
    logs = note.logs
    if not any("Program data: " in ln for ln in logs):
        engine.advance(note.slot, note.t_recv_ms, None)
        return 0
    boost_events = []
    for ln in logs:  # a CreatePool announcement must precede the pool's first print; a BOOST event follows the BuyEvent of its own tx
        i = ln.find("Program data: ")
        if i < 0:
            continue
        blob = ln[i + 14:].strip()
        if blob.startswith(_CREATE_POOL_PREFIX):
            raw = _program_data_bytes(ln)
            ev = decode_program_data(raw) if raw else None
            if ev and ev.get("kind") == "create_pool":
                engine.on_create_pool(ev["pool"], ev["base_mint"], ev["quote_mint"], note.slot, note.t_recv_ms)
        elif blob.startswith(_BOOST_EVENT_PREFIX):
            raw = _program_data_bytes(ln)
            ev = decode_extra_event(raw) if raw else None
            if ev and ev.get("type") == "boost_buy_and_burn":
                boost_events.append(ev)
    rows = records_from_logs(logs, slot=note.slot, signature=note.signature, t_recv_ms=note.t_recv_ms, commitment=note.commitment, feed=note.feed,
                             pool_mints=pool_mints, event_v=True)
    n = 0
    for row in rows:
        if row.get("venue") == "pumpswap":
            engine.on_trade(row)
            n += 1
    for ev in boost_events:
        engine.on_boost_event(ev, note.slot, note.t_recv_ms)
    if n == 0:
        engine.advance(note.slot, note.t_recv_ms, None)
    return n


# ---- live runner ----------------------------------------------------------------------------------------------
async def run_feed(source_factory: Callable[[], Any], engine: Engine, stop: asyncio.Event, *, backoff0: float = 1.0, backoff_max: float = 60.0,
                   max_seconds: float | None = None, sleep: Callable[[float], Any] = asyncio.sleep,
                   on_decode_error: Callable[[BaseException, Any], None] | None = None) -> None:
    """Consume notices until stop. A source that raises or ends is rebuilt after a backoff; each restart is a logged gap that flags open pools."""
    pool_mints: dict[str, tuple[str, str]] = {}
    started = time.monotonic()
    backoff = backoff0
    first = True
    while not stop.is_set():
        if not first:
            engine.feed_restart(engine.wall(), "source_restart")
        first = False
        try:
            source = source_factory()
            async for note in source.notices(stop):
                backoff = backoff0
                try:  # an engine / decode bug on one notice must not take the sockets down
                    decode_notice(engine, note, pool_mints)
                except Exception as exc:  # noqa: BLE001
                    engine.counters["decode_errors"] += 1
                    if engine.counters["decode_errors"] <= ERROR_RECORDS_MAX:
                        engine.emit({"type": "error", "where": "decode", "exc": type(exc).__name__, "msg": str(exc)[:200], "slot": getattr(note, "slot", None),
                                     "signature": getattr(note, "signature", None)})
                    if on_decode_error is not None:
                        try:
                            on_decode_error(exc, note)
                        except Exception:  # noqa: BLE001
                            pass
                if len(pool_mints) > 50_000:
                    for k in list(pool_mints)[:25_000]:
                        del pool_mints[k]
                if max_seconds is not None and time.monotonic() - started > max_seconds:
                    stop.set()
                    break
            if stop.is_set():
                break
            log.warning("source ended; restarting")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the feed must outlive any one failure
            engine.counters["feed_errors"] += 1
            log.warning("feed error %s: %s", type(exc).__name__, str(exc)[:200])
        engine.counters["feed_restarts"] += 1
        await sleep(backoff)
        backoff = min(backoff * 2, backoff_max)


async def housekeeping(engine: Engine, sink: JsonlSink, status_path: Path, stop: asyncio.Event, source_ref: dict, interval_s: float = 5.0,
                       hb_s: float = 60.0, on_error: Callable[[BaseException, dict], None] | None = None) -> None:
    last_hb = 0.0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
        now = engine.wall()
        try:
            engine.tick(now)
            src = source_ref.get("source")
            snap = feed_snapshot(src)
            if snap is not None:
                engine.note_feed_stats(id(src), snap, now)
            if time.monotonic() - last_hb >= hb_s or stop.is_set():
                last_hb = time.monotonic()
                st = status_snapshot(engine, sink, src)
                engine.emit({"type": "hb", **st})
                write_status(status_path, {"schema": SCHEMA, "updated_ms": now, **st})
        except Exception as exc:  # noqa: BLE001 - housekeeping must not die
            engine.counters["housekeeping_errors"] += 1
            if on_error is not None:
                on_error(exc, {"where": "housekeeping"})


def status_snapshot(engine: Engine, sink: JsonlSink | None, source: Any) -> dict:
    s = {
        "hw_slot": engine.hw_slot, "last_event_ms": engine.last_event_ms, "open_pools": len(engine.pools), "sps": engine.clock.sps(),
        "sps_points": engine.clock.n_points(), "sps_span_s": engine.clock.span_s(), "announced": len(engine.announced),
        "mint_pools": len(engine.mint_pools), "seen": len(engine.seen),
        "counters": dict(engine.counters),
    }
    if sink is not None:
        s["lines"] = sink.lines
    stats = getattr(source, "stats", None)
    if stats is not None:
        s["feed"] = {"reconnects": getattr(stats, "reconnects", None), "notes": getattr(stats, "notes", None), "slot_jumps": getattr(stats, "slot_jumps", None),
                     "rejections": dict(getattr(stats, "rejections", {}) or {}), "closes": dict(getattr(stats, "closes", {}) or {})}
    return s


def build_source(ws_urls: Sequence[str], sockets: int, commitment: str) -> Any:
    from observe.trade_source import DEFAULT_PUBLIC_WS, LogsSubscribeSource, MultiSocketLogsSource  # needs websockets + certifi (the listener venv)

    urls = list(ws_urls) or [DEFAULT_PUBLIC_WS]
    if sockets > 1:
        return MultiSocketLogsSource(ws_urls=urls, sockets=sockets, programs=(PUMPSWAP_PROGRAM,), commitment=commitment)
    return LogsSubscribeSource(ws_url=urls[0], programs=(PUMPSWAP_PROGRAM,), commitment=commitment)


async def run_live(args: argparse.Namespace) -> int:
    out_dir = check_out_dir(args.out_dir or DEFAULT_OUT_DIR)
    sink = JsonlSink(out_dir)
    errlog = ErrorLog(out_dir / "h5-shadow-errors.log")
    engine = Engine(sink.write, boost_mode=args.boost_mode, suppress_outcome=cap_pick_seal_oracle_stub)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    source_ref: dict = {}

    def factory() -> Any:
        source_ref["source"] = build_source(args.ws_url or [], args.sockets, args.commitment)
        return source_ref["source"]

    engine.emit({"type": "start", "rule": RULE_ID, "rule_sha256": RULE_SHA256, "argv": redact_argv(sys.argv[1:]), "out_dir": str(out_dir), "pid": os.getpid(), "sockets": args.sockets,
                 "commitment": args.commitment, "boost_mode": args.boost_mode, "keys": "none", "sends": "none",
                 "seal": {"reason": SEAL_REASON, "start_ms": SEAL_START_MS, "oracle": "stub_always_true"}})
    hk = asyncio.create_task(housekeeping(engine, sink, out_dir / "h5-shadow-status.json", stop, source_ref, on_error=lambda e, ctx: errlog.log(e, ctx)))
    try:
        await run_feed(factory, engine, stop, max_seconds=args.max_seconds,
                       on_decode_error=lambda e, note: errlog.log(e, {"where": "decode", "slot": getattr(note, "slot", None), "signature": getattr(note, "signature", None)}))
    finally:
        stop.set()
        await hk
        engine.close_all("shutdown")
        engine.emit({"type": "stop", **status_snapshot(engine, sink, source_ref.get("source"))})
        sink.close()
    return 0


# ---- replay (exploration tape) --------------------------------------------------------------------------------
TAPE_DIR = "/data/mal/audit-1008/tape"
WORK_DIR = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"
FROZEN_CONF = "/data/mal/hunt-1008/h5-flows/out/boostdip_frozen_conf.parquet"
FORBIDDEN = ("fresh-0802", "fresh-0808", "fresh-0828", "oracle-live", "forward-paper", "forward-walk", "forward_walk", "forward-1002", "forward-1016",
             "exp012-gate", "runner-status", "/var/lib/mal/paper", ".env", "helius", "keypair", "walk-2", "walk2")
VOID = ("2026-09-15T12", "2026-09-18T23")  # EXP-009 hours [lo, hi)
CUTOFF_HOUR = "2026-10-02T10"


class Refused(Exception):
    pass


def refuse_replay(path: str, hours: Sequence[str]) -> None:
    for bad in FORBIDDEN:
        if bad in path:
            raise Refused(f"{path}: outside the exploration pools ({bad!r})")
    for h in hours:
        if VOID[0] <= h < VOID[1]:
            raise Refused(f"{h}: EXP-009 void/holdout hour")
        if h >= CUTOFF_HOUR:
            raise Refused(f"{h}: at or after {CUTOFF_HOUR}Z (post-upgrade data is not exploration)")


def replay_rows(tape_dir: str, work_dir: str, hours: Sequence[str]) -> tuple[Iterator[dict], dict[str, dict], dict[str, float]]:
    """Rows of the tape's V-range canonical pools for `hours`, in (slot, tx_index, event_index) order, shaped like sealed rows, plus the meta
    of those pools (pool -> {s0, v, mint}) and the frozen rule's per-pool sps (from the day's paths, non-causal, for parity)."""
    import pandas as pd  # lazy: the audit venv

    days = sorted({h[:10] for h in hours})
    meta: dict[str, dict] = {}
    sps_pool: dict[str, float] = {}
    for day in days:
        m = pd.read_parquet(f"{work_dir}/meta/{day}.parquet")
        m = m[(m.v >= V_LO) & (m.v <= V_HI)]
        cnt = m.mint.value_counts()
        m = m[m.mint.isin(cnt[cnt == 1].index)]
        for r in m.itertuples():
            meta[r.pool] = {"s0": int(r.s0), "v": int(r.v), "mint": r.mint}
        pa = pd.read_parquet(f"{work_dir}/paths/{day}.parquet", columns=["mint", "slot", "bt"])
        by_mint = {v["mint"]: k for k, v in meta.items()}
        pa = pa[pa.mint.isin(by_mint)]
        for mint, g in pa.groupby("mint", sort=False):
            s0 = meta[by_mint[mint]]["s0"]
            g = g[g.slot >= s0]
            sl, bt = g.slot.values, g.bt.values
            ok = bt > 0
            if ok.sum() > 2 and sl[ok][-1] > sl[ok][0] + 300:
                sps_pool[by_mint[mint]] = float((bt[ok][-1] - bt[ok][0]) / max(1, sl[ok][-1] - sl[ok][0]))

    def gen() -> Iterator[dict]:
        for h in hours:
            t = pd.read_parquet(f"{tape_dir}/trades/{h}.parquet")
            t = t[(t.venue == "pumpswap") & t.pool.isin(meta)].sort_values(["slot", "tx_index", "event_index"], kind="stable")
            for r in t.itertuples():
                yield {
                    "venue": "pumpswap", "pool": r.pool, "slot": int(r.slot), "side": r.side, "trader": r.trader, "sol_lamports": int(r.sol_lamports),
                    "token_raw": int(r.token_raw), "quote_reserve": int(r.quote_reserve), "base_reserve": int(r.base_reserve),
                    "virtual_quote_reserves": meta[r.pool]["v"], "event_ts": int(r.block_time), "t_recv_ms": int(r.block_time) * 1000,
                    "signature": None, "mint": meta[r.pool]["mint"],
                }

    return gen(), meta, sps_pool


def shuffle_within_slot(rows: Iterable[dict], seed: int) -> Iterator[dict]:
    """Measurement aid: permute rows that share a slot (arrival order is not tx order on the live feed), seeded."""
    import random

    rng = random.Random(seed)
    buf: list[dict] = []
    for r in rows:
        if buf and r["slot"] != buf[0]["slot"]:
            rng.shuffle(buf)
            yield from buf
            buf = []
        buf.append(r)
    rng.shuffle(buf)
    yield from buf


def replay(rows: Iterable[dict], meta: dict[str, dict], engine: Engine) -> None:
    """Feed rows; announce each pool one slot before its first print (the tape's meta is its CreatePool)."""
    announced: set[str] = set()
    for row in rows:
        pool = row["pool"]
        if pool not in announced and pool in meta and row["slot"] == meta[pool]["s0"]:  # only a pool's own first print is its announcement
            announced.add(pool)
            engine.on_create_pool(pool, meta[pool]["mint"], WSOL_MINT, row["slot"] - 1, row["t_recv_ms"])
        engine.wall = lambda ms=row["t_recv_ms"]: ms  # records carry tape time, not the wall clock
        engine.on_trade(row)
    engine.close_all("replay_end")


def compare_frozen(records: list[dict], meta: dict[str, dict], frozen_path: str, s0_hour_prefix: str, tape_hours: Sequence[str]) -> dict:
    """Trigger lists vs the frozen rule's (leg p, exit 'end', stake 0.1) for pools whose s0 block time (UTC, 'YYYY-MM-DDTHH') starts with
    `s0_hour_prefix` (an hour, or a day). The tape hours must cover s0 + 400 s of every such pool."""
    import pandas as pd

    f = pd.read_parquet(frozen_path)
    f = f[(f.D == 40.0) & (f.leg == "p") & (f.H == "end") & (f.stake == "01")]
    pool_of = {v["mint"]: k for k, v in meta.items()}
    pools = {r["pool"]: r for r in records if r["type"] == "pool" and r["s0_block_time"] is not None
             and datetime.fromtimestamp(r["s0_block_time"], tz=timezone.utc).strftime("%Y-%m-%dT%H").startswith(s0_hour_prefix)}
    mine = {r["pool"]: r for r in records if r["type"] == "trigger" and r["variant"] == "pv" and r["pool"] in pools}
    out_by_pool = {r["pool"]: r for r in records if r["type"] == "outcome" and r["variant"] == "pv" and r["pool"] in pools}
    frozen = {pool_of[m]: row for m, row in zip(f.mint, f.itertuples()) if m in pool_of and pool_of[m] in pools}
    both, only_mine, only_frozen = sorted(set(mine) & set(frozen)), sorted(set(mine) - set(frozen)), sorted(set(frozen) - set(mine))
    dt = [abs(mine[p]["t_since_s0_s"] - frozen[p].trig_t) for p in both]
    dq = [abs(mine[p]["q_pv_post_sol"] - frozen[p].q_trig) for p in both]
    dg = []
    for p in both:
        o = out_by_pool.get(p)
        if o and o["legs"]["primary"].get("net"):
            dg.append(abs(o["legs"]["primary"]["net"]["0.1"]["gross"] - frozen[p].gross))
    return {"pools_s0_in_hour": len(pools), "mine": len(mine), "frozen": len(frozen), "both": len(both), "only_mine": only_mine, "only_frozen": only_frozen,
            "max_abs_dt_s": max(dt) if dt else None, "max_abs_dq_sol": max(dq) if dq else None, "median_abs_dq_sol": sorted(dq)[len(dq) // 2] if dq else None,
            "gross_compared": len(dg), "max_abs_dgross": max(dg) if dg else None, "median_abs_dgross": sorted(dg)[len(dg) // 2] if dg else None,
            "tape_hours": list(tape_hours)}


def run_replay(args: argparse.Namespace) -> int:
    hours = [h.strip() for h in args.replay_hours.split(",") if h.strip()]
    try:
        refuse_replay(args.replay_tape, hours)
        refuse_replay(args.work_dir, [])
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    rows, meta, sps_pool = replay_rows(args.replay_tape, args.work_dir, hours)
    if args.shuffle_slot_seed is not None:
        rows = shuffle_within_slot(rows, args.shuffle_slot_seed)
    records: list[dict] = []
    sps_fn = (lambda p: sps_pool.get(p.pool)) if args.sps == "pool" else None
    engine = Engine(records.append, boost_mode=args.boost_mode, sps_fn=sps_fn)
    replay(rows, meta, engine)
    if args.out_dir:
        sink = JsonlSink(args.out_dir, prefix="h5-replay")
        for r in records:
            sink.write(r)
        sink.close()
    summary: dict[str, Any] = {"rule": RULE_ID, "hours": hours, "sps": args.sps, "boost_mode": args.boost_mode, "counters": dict(engine.counters),
                               "triggers_pv": sum(1 for r in records if r["type"] == "trigger" and r["variant"] == "pv"),
                               "triggers_fv": sum(1 for r in records if r["type"] == "trigger" and r["variant"] == "fv"),
                               "pools": sum(1 for r in records if r["type"] == "pool")}
    if args.compare_frozen:
        summary["compare"] = compare_frozen(records, meta, args.compare_frozen, args.compare_s0 or hours[0], hours)
    print(json.dumps(clean(summary), indent=1, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="H5-BOOSTFLOOR v1 live shadow detector (paper only; no keys, no transactions)")
    ap.add_argument("--out-dir", default=None, help=f"live: output dir (default {DEFAULT_OUT_DIR}); replay: written only when given")
    ap.add_argument("--ws-url", action="append", default=None, help="public RPC websocket; repeatable (socket i uses url i mod n)")
    ap.add_argument("--sockets", type=int, default=2, help="redundant logsSubscribe sockets merged by signature")
    ap.add_argument("--commitment", default="confirmed", choices=("processed", "confirmed", "finalized"))
    ap.add_argument("--boost-mode", default="auto", choices=("auto", "pda", "behavioural"))
    ap.add_argument("--max-seconds", type=float, default=None, help="stop after this many seconds (smoke test)")
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--replay-tape", default=None, help="run the engine over this exploration tape dir instead of the live feed")
    ap.add_argument("--replay-hours", default=None, help="comma-separated tape hours, e.g. 2026-09-20T12,2026-09-20T13")
    ap.add_argument("--work-dir", default=WORK_DIR)
    ap.add_argument("--shuffle-slot-seed", type=int, default=None, help="replay: permute prints within a slot (seeded) to measure the reorder-proof chain check")
    ap.add_argument("--sps", default="pool", choices=("pool", "rolling"), help="replay: frozen per-pool sps (parity) or the live rolling slot clock")
    ap.add_argument("--compare-frozen", default=None, help="parquet of the frozen rule's trades (boostdip_frozen_conf.parquet)")
    ap.add_argument("--compare-s0", default=None, help="compare pools whose first print is in this UTC hour or day prefix (default: the first replayed hour)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.out_dir:
        try:
            check_out_dir(args.out_dir)
        except ValueError as e:
            ap.error(str(e))
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.sockets < 1:
        ap.error("--sockets must be >= 1")
    if args.replay_tape:
        if not args.replay_hours:
            ap.error("--replay-tape needs --replay-hours")
        return run_replay(args)
    return asyncio.run(run_live(args))


if __name__ == "__main__":
    sys.exit(main())
