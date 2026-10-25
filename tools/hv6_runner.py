#!/usr/bin/env python3
"""hv6_runner: the loop of HV6, a general Haiku 5.5 trader with a regime channel (build item 4 of docs/research/hv6/DESIGN.md).

PAPER only. No wallet or trading key. Five arms on one paper stream: hv6.0-L, -L-nr, -F, -P (LLM, same cards byte for byte) and -X (rule book, no
model). The runner steps tools.arena_state.ArenaState with poll(until_ms=T-1), scans each minute at :02 with tools.hv6_cards, asks the four LLM arms
through tools.hv6_agents (every call gated by hv6_agents.BudgetGuard), runs code exits, and sends orders to tools.arena_paper.ArenaEngine, which prices
every fill. The runner never opens /data/mal/arena/bots.json (registration is tools/hv6_register.py's job; the engine reads its own registry path).

BINDING QP CONDITIONS (comments on #911, #917) and where they are met
  chain      HV6 builds its OWN RpcClient with state under <root>/chain-credits.json and refuses to start if MAL_HV4_CHAIN_STATE is set.
  file I/O   every open/mkdir/glob of the runner, its decision log and its card sink calls tools.hv6_cards.check (allowlist, then deny tokens).
             The import guard (hv6_cards.forbidden_imports) covers this file: no hv4 module except hv4_cards / hv4_chain, no arena module that reads outputs.
  caps       cards per call 4 (2 degraded), entry calls 12/h/arm, re-check calls 24/h/arm, entries 3/h/arm (+ active probe extra, total <= 1),
             young-tier (2-24 h) half the slots, X 2 entries in the trailing 60 min; regime trigger 2/day.
  halt       the group policy (levels, halt, X halt) is computed ONCE per tick (Runner.tick -> TickPolicy) and applied to all five arms. A halt marked
             during a tick takes effect for every arm from the next tick. Any guard/preflight exception is a deny for that tick. The day's max level
             and the instant it was first reached are logged (SCAN row `day_level`) and kept in Runner.day_level.
  probes     only RegimeRuleVerdicts that are ok, mode probe, tag regime_probe, unexpired and within probe_extra_entries_allowed become probe records;
             a malformed stored record grants NO extra entry (fail closed).
  regime     only the regime block JSON (schema hv6_regime.v1, tools/hv6_regime_block.py output) reaches a hook; L-nr never gets it; a block flagged
             drill/smoke, with another schema, or whose file name does not carry its as_of_ms is refused; the loader reads only <root>/regime/block-*.json.

NOT HERE (stated, not hidden): the reviewer and release (item 5), the prompt of the intraday regime review (the runner detects the trigger and calls
`regime_review_fn`; with none set it logs `regime_review_pending`), the chain prefetcher (tools/hv4_prefetch is outside the import allowlist: facts are
fetched at card time), WATCH replies (logged as a skip), the scan prescreen speed path, HV4's creator/sniper tape facts (holder_count and the quote facts are kept).
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import bisect
import hashlib
import json
import math
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import hv4_cards as C
from tools import hv6_agents as A
from tools import hv6_cards as H
from tools.arena_declog import GENESIS, DecisionLog, LogChainError, sha256_hex, utc_day  # noqa: F401
from tools.arena_paper import ArenaRefused, canon
from tools.arena_state import WSOL_MINT, ArenaState

MIN_MS = 60_000
TICK_MS = 1_000
BOT_PREFIX = "hv6.0-"
ARM_ORDER = ("L", "L-nr", "F", "P", "X")
RECHECK_CALLS_PER_H = 24
REGIME_TRIGGERS_PER_DAY = 2
POOL_COOLDOWN_MS = 30 * MIN_MS
ENTRY_SETTLE_MS = 3_000
SELL_EARLY = "slower entry leg"
SELL_ENGINE_DROPS = ("no open position", "already has a sell")
SELL_RETRY_MS = 1_000
DENIED = ("group_halted", "budget_exceeded", "day_cap", "month_guard", "global_day_cap", "ledger_unreadable", "preflight_exception")
BLOCK_SCHEMA = "hv6_regime.v1"
MAX_BLOCK_BYTES = 2_000_000
CHAIN_CREDITS_FILE = "chain-credits.json"
STACK_RENT = 0
STACK_FIELDS: dict[str, Any] = {  # as tools/hv4_runner.STACK_FIELDS (DEC-031 Amendment 2), spelled out: no hv4_runner import
    "send": 505_000, "rent": STACK_RENT, "h_in": 0.002608, "h_out": 0.0016, "k_rounding": "ceil", "stakes_sol": (0.25, 0.5, 1.0), "grace_slots": 10_000, "portal_ppm": 0,
    "press_intercept": -1.4197242838379789, "strand_rule": "both", "e1_floor_per_005": 384_022, "gap_slots": 150, "gap_silence_ms": 60_000, "max_future_ms": 5_000,
}


def bot_id(arm: str) -> str:
    return f"{BOT_PREFIX}{arm}"


def position_id(bot: str, pool: str, decision_ms: int, decision_slot: int) -> str:
    """The engine's deterministic position id (ArenaEngine.buy), so the order id is logged before the order is sent."""
    return hashlib.sha256(f"{bot}|{pool}|{int(decision_ms)}|{int(decision_slot)}".encode()).hexdigest()[:16]


# ------------------------------------------------------------------------------------------------------------------------------ guarded files
class GuardedDecisionLog(DecisionLog):
    """arena_declog.DecisionLog with every path checked by hv6_cards.check before it is read or written (append, verify, rows, md5, day_sha256)."""

    def __init__(self, root: str | os.PathLike) -> None:
        super().__init__(H.check(Path(root), "w"))

    def files(self, name: str) -> list[Path]:
        d = self.dir / name
        H.check(d, "r")
        return super().files(name)

    def append(self, name: str, ms: int, body: Mapping[str, Any]) -> dict:
        H.check(self.dir / name / f"{utc_day(ms)}.jsonl", "w")
        return super().append(name, ms, body)

    def md5(self) -> str:
        H.check(self.dir, "r")
        return super().md5()

    def day_sha256(self, name: str, day: str) -> str | None:
        H.check(self.dir / name / f"{day}.jsonl", "r")
        return super().day_sha256(name, day)


class CardSink:
    """Every card shown to an LLM arm, by sha256, <root>/cards/<day>.jsonl (the reviewer's skipped-card store). Guarded."""

    def __init__(self, root: str | os.PathLike) -> None:
        self.dir = Path(H.check(Path(root) / "cards", "w"))
        self._seen: set[str] = set()

    def put(self, card: Mapping[str, Any], ms: int) -> str:
        sha = A.p4.card_sha256(dict(card))
        if sha in self._seen:
            return sha
        self._seen.add(sha)
        path = H.check(self.dir / f"{utc_day(ms)}.jsonl", "w")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with H.guarded_open(path, "ab") as f:
            f.write(canon({"sha": sha, "card": dict(card)}).encode() + b"\n")
        return sha


# ------------------------------------------------------------------------------------------------------------------------------ chain client
def refuse_hv4_chain_state(env: Mapping[str, str] | None = None) -> None:
    """QP C1: a default-built HV4Chain takes its state file from MAL_HV4_CHAIN_STATE; if it is set HV6 could read or write HV4's chain counters."""
    e = os.environ if env is None else env
    if (e.get("MAL_HV4_CHAIN_STATE") or "").strip():
        raise RuntimeError("MAL_HV4_CHAIN_STATE is set: refused (HV6 keeps its own chain state under its own root; unset the variable)")


def chain_state_path(root: str | os.PathLike) -> Path:
    return Path(H.check(Path(root) / CHAIN_CREDITS_FILE, "w"))


def live_chain_source(root: str | os.PathLike, env: Mapping[str, str] | None = None) -> "ChainSource":
    """HV6's own RpcClient (state under the HV6 root) in an HV4Chain passed explicitly (so MAL_HV4_CHAIN_STATE is never consulted)."""
    refuse_hv4_chain_state(env)
    from tools.hv4_chain import HV4Chain, RpcClient, RpcConfig

    return ChainSource(HV4Chain(RpcClient(RpcConfig.from_env(env), state_path=chain_state_path(root))))


class ChainSource:
    """chain_part(pool, mint, now_ms, slot) -> dict | None, fetched at card time (no prefetcher). None = no card (fail closed)."""

    def __init__(self, chain: Any) -> None:
        self.chain = chain

    def chain_part(self, pool_id: str, mint: str | None, now_ms: int, slot: int) -> dict | None:
        if not mint:
            return None
        mf = self.chain.mint_facts(mint)
        if not mf.ok or mf.as_of_ms is None or mf.as_of_slot is None:
            return None
        mh = self.chain.mayhem_flag(mint, pool=pool_id)
        hd = self.chain.largest_holders(mint, token_program=mf.token_program_id, pool=pool_id, max_age_s=900.0)
        facts: dict[str, Any] = {"as_of_ms": mf.as_of_ms, "as_of_slot": mf.as_of_slot, "mint_authority": mf.mint_authority,
                                 "freeze_authority": mf.freeze_authority, "token_program": mf.token_program_id, "extensions": list(mf.extensions),
                                 "mayhem": mh.is_mayhem}
        if hd is not None and hd.ok and hd.as_of_ms is not None:
            facts.update({"top1_frac": hd.top1_frac, "top10_frac": hd.top10_frac, "holders_as_of_ms": hd.as_of_ms})
        return facts


# ------------------------------------------------------------------------------------------------------------------------------ per-tick policy
@dataclass(frozen=True)
class TickPolicy:
    """The group state for ONE tick, applied to all five arms (QP R-a). Built once by compute_tick_policy; never recomputed inside the tick."""
    ms: int
    level: int
    reason: str | None
    entries_halted: bool        # the four LLM arms
    x_halted: bool              # X, in step with the group (T8)
    board_on: bool
    max_cards: int
    recheck_mult: int
    rechecks_halted: bool


def compute_tick_policy(guard: Any, T: int) -> TickPolicy:
    """Any exception from the guard is a deny for the tick (QP b): level 4, everything halted, reason preflight_exception."""
    if guard is None:
        return TickPolicy(T, 0, None, False, False, True, A.ENTRY_MAX_CARDS, 1, False)
    try:
        pol = guard.policy(T)
        x_halted = bool(guard.x_entries_halted(T))
        halted = bool(pol.entries_halted)
        return TickPolicy(T, int(pol.level), pol.halt_reason, halted, halted or x_halted, bool(pol.board_enabled), int(pol.max_cards_per_call),
                          int(pol.recheck_cadence_multiplier), bool(pol.llm_rechecks_halted))
    except Exception:  # noqa: BLE001 - any failure is a deny, never an allow
        return TickPolicy(T, A.DEGRADE_STEPS, "preflight_exception", True, True, False, A.ENTRY_MAX_CARDS_DEGRADED, 3, True)


# ------------------------------------------------------------------------------------------------------------------------------ probes
def _probe_record_ok(r: Any) -> bool:
    if not isinstance(r, dict):
        return False
    e, x = r.get("extra_entries_per_h"), r.get("expires_ms")
    return (isinstance(e, int) and not isinstance(e, bool) and 0 <= e <= A.PROBE_MAX_EXTRA_ENTRIES_PER_H
            and isinstance(x, int) and not isinstance(x, bool))


class ProbeBook:
    """Active regime probes per arm. A record exists only if built here from an ACCEPTED verdict. Anything else in the list is malformed and, when
    present, grants no extra entry at all (fail closed)."""

    def __init__(self) -> None:
        self.by_arm: dict[str, list[Any]] = {}

    def add(self, arm: str, verdict: Any, now_ms: int) -> bool:
        if arm not in A.regime_arms():
            return False
        if not isinstance(verdict, A.RegimeRuleVerdict) or not verdict.ok or verdict.mode != "probe" or verdict.tag != "regime_probe":
            return False
        ch = verdict.change if isinstance(verdict.change, Mapping) else {}
        rec = {"extra_entries_per_h": ch.get("extra_entries_per_h"), "expires_ms": verdict.expires_ms, "target": ch.get("target")}
        if not _probe_record_ok(rec) or rec["expires_ms"] <= now_ms:
            return False
        active = self.by_arm.setdefault(arm, [])
        if not all(_probe_record_ok(x) for x in active):
            return False
        if not A.probe_extra_entries_allowed(active, rec["extra_entries_per_h"], now_ms):
            return False
        active.append(rec)
        return True

    def extra_entries(self, arm: str, now_ms: int) -> int:
        recs = self.by_arm.get(arm, [])
        if not recs:
            return 0
        if not all(_probe_record_ok(r) for r in recs):
            return 0  # fail closed: a malformed record refuses the extra entry
        n = A.active_extra_entries(recs, now_ms)
        return n if n <= A.MAX_EXTRA_ENTRIES_TOTAL else 0


# ------------------------------------------------------------------------------------------------------------------------------ regime channel
class RegimeChannel:
    """Reads the pinned regime blocks the builder wrote under <root>/regime/ (block-<as_of_ms>.json only; the radar byte copies are never opened)."""

    def __init__(self, directory: str | os.PathLike) -> None:
        self.dir = Path(directory)
        self.seen_ids: set[str] = set()
        self.refused = 0

    def _load(self, p: Path) -> dict | None:
        try:
            path = H.check(p, "r")
            if os.path.getsize(path) > MAX_BLOCK_BYTES:
                return None
            with H.guarded_open(path, "rb") as f:
                blk = json.loads(f.read())
        except (OSError, ValueError, PermissionError):
            return None
        ms = blk.get("as_of_ms") if isinstance(blk, dict) else None
        if (not isinstance(blk, dict) or blk.get("schema") != BLOCK_SCHEMA or isinstance(ms, bool) or not isinstance(ms, int)
                or any(k in blk for k in ("drill", "smoke", "stub", "synthetic")) or p.name != f"block-{ms}.json"):
            self.refused += 1
            return None
        return blk

    def current(self, T: int) -> dict | None:
        """The newest block with as_of_ms strictly before T."""
        try:
            H.check(self.dir, "r")
            names = sorted(self.dir.glob("block-*.json"))
        except (OSError, PermissionError):
            return None
        best = None
        for p in names:
            blk = self._load(p)
            if blk is not None and blk["as_of_ms"] < T and (best is None or blk["as_of_ms"] > best["as_of_ms"]):
                best = blk
        return best

    @staticmethod
    def block_for(arm: str, block: Mapping | None) -> Mapping | None:
        """L-nr and F get no block; only the regime-review arms (L, P)."""
        return block if block is not None and arm in A.regime_arms() else None

    def new_direct_high(self, block: Mapping | None) -> list[str]:
        if not block or block.get("radar_status") not in A.USABLE_STATUS:
            return []
        out = []
        for e in block.get("events") or []:
            if isinstance(e, dict) and e.get("sev") == "HIGH" and e.get("relevance") == "direct" and isinstance(e.get("id"), str) and e["id"] not in self.seen_ids:
                out.append(e["id"])
        return sorted(out)


class QuoteHistory:
    """Real quote (SOL) of each verified-WSOL PumpSwap pool, for the card's quote_sol_15m_ago and quote_sol_peak facts (a function of the tape alone, read at
    the minute boundaries at :02; the sample at boundary b is the quote after the last row with t <= b - 1). The same rule as HV4's, written out here."""

    def __init__(self, keep_ms: int = 65 * MIN_MS, min_rows: int = 64) -> None:
        self.keep_ms, self.min_rows = keep_ms, min_rows
        self.t: dict[str, list[int]] = {}
        self.q: dict[str, list[float]] = {}

    def add(self, pool: str, t_ms: int, q_sol: float) -> None:
        ts, qs = self.t.setdefault(pool, []), self.q.setdefault(pool, [])
        ts.append(t_ms)
        qs.append(q_sol)
        if len(ts) > self.min_rows and ts[0] < t_ms - 2 * self.keep_ms:
            i = bisect.bisect_left(ts, t_ms - 2 * self.keep_ms) - 1
            if i > 0:
                del ts[:i]
                del qs[:i]

    def _at(self, pool: str, b: int) -> float | None:
        ts = self.t.get(pool)
        if not ts:
            return None
        i = bisect.bisect_right(ts, b - 1) - 1
        return self.q[pool][i] if i >= 0 else None

    @staticmethod
    def _boundaries(lo: int, hi: int) -> range:
        first = (lo - H.SCAN_SECOND_MS + MIN_MS - 1) // MIN_MS * MIN_MS + H.SCAN_SECOND_MS
        return range(first, hi, MIN_MS)

    def ref(self, pool: str, t_ms: int) -> tuple[float, int] | None:
        want = t_ms - C.QUOTE_REF_AGE_MS
        best = None
        for b in self._boundaries(want - C.QUOTE_REF_TOL_MS, min(want + C.QUOTE_REF_TOL_MS + 1, t_ms)):
            v = self._at(pool, b)
            if v is not None and (best is None or abs(b - want) < best[0]):
                best = (abs(b - want), b, v)
        return None if best is None else (best[2], best[1])

    def peak(self, pool: str, t_ms: int) -> float | None:
        vals = [v for b in self._boundaries(t_ms - self.keep_ms, t_ms) if (v := self._at(pool, b)) is not None]
        return max(vals) if vals else None


# ------------------------------------------------------------------------------------------------------------------------------ positions
@dataclass
class Pos:
    arm: str
    pid: str
    pool: str
    mint: str | None
    size: float
    entry_ms: int
    entry_price: float
    tokens_eq: float
    stop_pct: float | None
    target_pct: float | None
    trail_pct: float | None
    time_min: int | None
    hard_stop_pct: float
    max_hold_ms: int
    young: bool
    probe: bool = False
    token: dict = field(default_factory=dict)
    peak: float = 1.0
    last_recheck_ms: int = 0
    sell_tries: int = 0


def mark_ret(pos: Pos, snap: Mapping[str, Any] | None) -> float | None:
    """Code-side mark: constant-product sell quote of the tokens at the state's spot over the stake, minus 1. None when unpriced. Not the scored P&L."""
    if not snap:
        return None
    res = snap.get("reserves") if snap.get("quote_wsol") is True else None
    price = snap.get("price")
    q = res.get("quote_eff_sol") if isinstance(res, Mapping) else None
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in (price, q)):
        return None
    val = pos.tokens_eq * float(price)
    return val * float(q) / (float(q) + val) / pos.size - 1.0


def exit_reason(pos: Pos, ret: float | None, now_ms: int) -> str | None:
    if ret is not None:
        pct = ret * 100.0
        if pct <= pos.hard_stop_pct:
            return "hard_stop"
        if pos.stop_pct is not None and pct <= pos.stop_pct:
            return "stop"
        if pos.target_pct is not None and pct >= pos.target_pct:
            return "target"
        arm_after = A.GEN_SPEC.code_limits.get("trail_arms_after_pct", 50.0)
        if pos.trail_pct is not None and (pos.peak - 1.0) * 100.0 >= arm_after and 1.0 + ret <= pos.peak * (1.0 - pos.trail_pct / 100.0):
            return "trail"
    age = now_ms - pos.entry_ms
    if pos.time_min is not None and age >= pos.time_min * MIN_MS:
        return "time"
    if age >= pos.max_hold_ms:
        return "max_hold"
    return None


@dataclass
class Book:
    arm: str
    open: dict[str, Pos] = field(default_factory=dict)
    entries_ms: list[int] = field(default_factory=list)
    pool_last_ms: dict[str, int] = field(default_factory=dict)
    realized: dict[str, float] = field(default_factory=dict)


@dataclass
class RunnerConfig:
    root: str = H.HV6_ROOT
    frozen_sha: str = ""
    clock_start_ms: int = 0
    live: bool = False
    strict_leakage: bool = False


class Runner:
    def __init__(self, cfg: RunnerConfig, state: Any, engine: Any, model: Any, chain: Any, guard: Any = None, now_fn: Callable[[], int] | None = None,
                 regime_review_fn: Callable[[str, Mapping, int], Any] | None = None) -> None:
        self.cfg, self.state, self.engine, self.model, self.chain, self.guard = cfg, state, engine, model, chain, guard
        self.now_fn = now_fn or (lambda: time.time_ns() // 1_000_000)
        self.root = Path(H.check(cfg.root, "w"))
        self.log = GuardedDecisionLog(self.root)
        self.cards = CardSink(self.root)
        self.regime = RegimeChannel(self.root / "regime")
        self.regime_review_fn = regime_review_fn
        self.probes = ProbeBook()
        self.arm_names = ARM_ORDER
        self.llm_arm_names = tuple(a for a in ARM_ORDER if A.ARMS[a].llm)
        self.books = {a: Book(a) for a in ARM_ORDER}
        self.limiter = C.SetupLimiter()
        self.quotes = QuoteHistory()
        self.xcap = H.XCap()
        self.knobs = {a: A.Knobs.v0() for a in self.llm_arm_names}
        self.plans = {a: dict(A.GEN_SPEC.plan_defaults) for a in self.llm_arm_names}
        self.playbooks = {a: A.load_playbook("v0") for a in self.llm_arm_names}
        self.calls: dict[tuple[str, str], list[int]] = {}
        self.seq: dict[tuple[str, str], int] = {}
        self.regime_triggers: dict[str, int] = {}
        self.day_level: dict[str, dict[str, Any]] = {}
        self.counters: dict[str, int] = {}
        self.invalid = {a: [0, 0] for a in self.llm_arm_names}
        self.slot = 0
        self.sim_now = 0
        self.tp: TickPolicy | None = None
        self._sell_next: dict[str, int] = {}
        self._stop = False
        if not cfg.live and hasattr(engine, "now_fn"):
            engine.now_fn = lambda: self.sim_now
        self.hook_errors: list[str] = []
        orig = state.apply

        def apply(row: dict) -> None:
            orig(row)
            self._on_row(row)

        state.apply = apply  # poll() calls self.apply: the engine sees exactly the rows the state sees, in order

    # -- plumbing
    def _count(self, k: str, n: int = 1) -> None:
        self.counters[k] = self.counters.get(k, 0) + n

    def _now(self, T: int) -> int:
        return self.now_fn() if self.cfg.live else T

    def _dec_ms(self, T: int, reply: Any = None) -> int:
        return self.now_fn() if self.cfg.live else T + int(getattr(reply, "latency_ms", 0) or 0)

    def _on_row(self, row: dict) -> None:
        try:
            s = row.get("slot")
            if row.get("type") == "trade" and isinstance(s, int) and s > self.slot:
                self.slot = s
            self.engine.on_row(row)
            if row.get("type") == "trade" and row.get("prog") == "pumpswap" and row.get("pool"):
                p = self.state.pool(row["pool"])
                if p is not None and p.quote_mint == WSOL_MINT and p.q_real is not None:
                    self.quotes.add(p.id, int(row["t_recv_ms"]), p.q_real / 1e9)
        except Exception as e:  # noqa: BLE001 - recorded, never swallowed silently
            self.hook_errors.append(type(e).__name__)

    def poll_to(self, T: int) -> None:
        self.state.poll(until_ms=T - 1)

    # -- resume
    def resume(self) -> dict[str, int]:
        """Verify every hash chain (a break raises LogChainError and the runner does not start) and rebuild positions, caps and the setup cooldown."""
        n = {"rows": 0, "open": 0}
        for name in (*ARM_ORDER, "SCAN"):
            self.log.verify(name)
            for r in self.log.rows(name):
                n["rows"] += 1
                self._replay_row(name, r)
        n["open"] = sum(len(b.open) for b in self.books.values())
        return n

    def _replay_row(self, name: str, r: Mapping[str, Any]) -> None:
        k, day = r.get("kind"), utc_day(r["ms"])
        if name == "SCAN":
            if k == "scan":
                for pool in r.get("tagged", []):
                    self.limiter.record(pool, H.PERSONA, r["ms"])
            elif k == "day_level":
                self.day_level[day] = {"max_level": r["max_level"], "first_ms": r.get("first_ms"), "reason": r.get("reason")}
            elif k == "regime_trigger":
                self.regime.seen_ids.update(r.get("ids", []))
                self.regime_triggers[day] = self.regime_triggers.get(day, 0) + 1
            return
        b = self.books.get(name)
        if b is None:
            return
        if k == "llm_call":
            self.seq[(name, day)] = self.seq.get((name, day), 0) + 1
            self.calls.setdefault((name, r["call"]), []).append(r["ms"])
        elif k == "entry" and r.get("pos"):
            p = Pos(**r["pos"])
            b.open[p.pid] = p
            b.entries_ms.append(r["ms"])
            b.pool_last_ms[p.pool] = r["ms"]
            if name == "X":
                self.xcap.record(r["ms"])
        elif k == "order_refused" and r.get("side") == "buy":
            p = b.open.pop(r["order_id"], None)
            if p is not None:
                if p.entry_ms in b.entries_ms:
                    b.entries_ms.remove(p.entry_ms)
                if name == "X" and p.entry_ms in self.xcap.entries_ms:
                    self.xcap.entries_ms.remove(p.entry_ms)
        elif k == "sell":
            b.open.pop(r["pid"], None)
            b.realized[day] = b.realized.get(day, 0.0) + float(r.get("est_pnl_sol") or 0.0)
        elif k == "peak" and r["pid"] in b.open:
            b.open[r["pid"]].peak = max(b.open[r["pid"]].peak, float(r["peak"]))

    # -- the group policy: once per tick
    def _begin_tick(self, T: int) -> TickPolicy:
        tp = compute_tick_policy(self.guard, self._now(T))
        self.tp = tp
        day = utc_day(T)
        cur = self.day_level.get(day)
        if cur is None or tp.level > cur["max_level"]:
            self.day_level[day] = {"max_level": tp.level, "first_ms": T, "reason": tp.reason}
            if tp.level > 0 or cur is not None:  # a level-0 day writes nothing until it degrades; the day's max is then 0 by absence
                self.log.append("SCAN", T, {"kind": "day_level", "max_level": tp.level, "first_ms": T, "reason": tp.reason, "x_halted": tp.x_halted})
        return tp

    def day_max_level(self, day: str) -> int:
        return int(self.day_level.get(day, {}).get("max_level", 0))

    # -- model calls
    def _call_ok(self, names: Sequence[str], kind: str, T: int) -> bool:
        cap = H.ENTRY_CALLS_PER_H if kind == "entry" else RECHECK_CALLS_PER_H
        for n in names:
            lst = [t for t in self.calls.get((n, kind), []) if t > T - 60 * MIN_MS]
            self.calls[(n, kind)] = lst
            if len(lst) >= cap:
                return False
        return True

    def _call(self, name: str, req: Any, T: int) -> tuple[Any, int]:
        try:
            reply = self.model.complete(req, self._now(T))
        except Exception:  # noqa: BLE001 - any preflight/guard/model-path exception is a deny for this tick
            reply = A.ModelReply(error="preflight_exception")
            self._count("preflight_exception")
            if self.guard is not None:
                try:
                    self.guard.halt_group(self._now(T), "preflight_exception")  # takes effect from the next tick
                except Exception:  # noqa: BLE001
                    pass
        recv = self._dec_ms(T, reply)
        self.sim_now = max(self.sim_now, recv)
        day = utc_day(recv)
        sq = self.seq.get((name, day), 0)
        self.seq[(name, day)] = sq + 1
        self.calls.setdefault((name, req.kind), []).append(recv)
        self.log.append(name, recv, {"kind": "llm_call", "call": req.kind, "ids": list(req.ids), "card_shas": list(req.card_shas), "model": self.model.model,
                                     "request_sha256": reply.request_sha256 or req.request_sha256(), "response_sha256": reply.response_sha256,
                                     "latency_ms": reply.latency_ms, "cost_usd": reply.cost_usd, "error": reply.error, "stop_reason": reply.stop_reason,
                                     "nonce": req.nonce, "as_of_ms": T, "n_cards": len(req.ids)})
        return reply, recv

    # -- scan, cards
    def scan(self, T: int) -> None:
        tp = self.tp or self._begin_tick(T)
        items, snap_of, prints_of = [], {}, {}
        for p in self.state.pools_wsol(lambda p: p.prog == "pumpswap"):
            snap = self.state.snapshot(p.id)
            if snap is None:
                continue
            prints = list(p.events)
            items.append((snap, prints))
            snap_of[p.id], prints_of[p.id] = snap, prints
        res = H.scan(items, as_of_ms=T, limiter=self.limiter, x_cap=self.xcap, board_on=tp.board_on)
        cards: dict[str, dict] = {}
        for t in res.tags:
            c = self._card(t.pool_id, T, snap_of[t.pool_id], prints_of[t.pool_id], t, res.feats[t.pool_id])
            if c is not None:
                cards[t.pool_id] = c
        for pid in res.board:
            c = self._card(pid, T, snap_of[pid], prints_of[pid], None, res.feats[pid])
            if c is not None:
                cards[pid] = c
        self.log.append("SCAN", T, {"kind": "scan", "as_of_slot": self.slot, "counts": dict(res.counts), "n_cards": len(cards), "level": tp.level,
                                    "tagged": sorted(t.pool_id for t in res.tags), "card_shas": sorted(A.p4.card_sha256(c) for c in cards.values())})
        self._x_round(res.x_tags, cards, T, tp)
        order = [cards[t.pool_id] for t in res.tags if t.pool_id in cards] + [cards[p] for p in res.board if p in cards]
        self._llm_entry_round(order, T, tp)

    def _card(self, pool: str, T: int, snap: Mapping, prints: list, tag: Any, feats: Mapping) -> dict | None:
        part = self.chain.chain_part(pool, snap.get("mint"), T, self.slot)
        if part is None:
            self._count("chain_none")
            self.log.append("SCAN", T, {"kind": "card_refused", "pool": pool, "reasons": ["chain_unavailable"]})
            return None
        p = self.state.pool(pool)
        chain = dict(part)
        ref = self.quotes.ref(pool, T)
        if ref is not None:
            chain.update({"quote_sol_15m_ago": ref[0], "quote_sol_15m_ago_as_of_ms": ref[1]})
        pk = self.quotes.peak(pool, T)
        if pk is not None:
            chain["quote_sol_peak"] = pk
        chain["holder_count"] = sum(1 for v in p.net.values() if v > 0) if p is not None and hasattr(p, "net") else None
        self.token_of = getattr(self, "token_of", {})
        self.token_of[pool] = {k: part[k] for k in ("token_program", "extensions") if k in part}
        try:
            C.assert_as_of(snap, T, prints=prints, chain=chain, as_of_slot=self.slot)
            return H.build_card(snap, prints, chain, as_of_ms=T, as_of_slot=self.slot, setup=tag, feats=feats)
        except C.LeakageError as e:
            self._count("leakage_refused")
            self.log.append("SCAN", T, {"kind": "as_of_refusal", "pool": pool, "detail": str(e)[:160]})
            if self.cfg.strict_leakage:
                raise
        except C.CardRefused as e:
            self._count("card_refused")
            self.log.append("SCAN", T, {"kind": "card_refused", "pool": pool, "reasons": list(e.reasons)})
        return None

    # -- gates and sizes
    @staticmethod
    def _cap_size(card: Mapping, asked: float) -> float:
        f = card.get("f", {})
        impact = C.max_stake_by_impact(f.get("quote_eff_sol"))
        age_cap = C.YOUNG_STAKE_CAP if f.get("age_tier") != "established" else A.SIZES[-1]
        return A.final_size(asked, A.SIZES[-1], impact if impact is not None else 0.0, age_cap)

    def _gates(self, arm: str, pool: str, size: float, young: bool, T: int) -> dict[str, dict]:
        lim = A.GEN_SPEC.code_limits
        b = self.books[arm]
        day = utc_day(T)
        marked = sum((mark_ret(p, self.state.snapshot(p.pool, candles=False)) or 0.0) * p.size for p in b.open.values())
        pnl = b.realized.get(day, 0.0) + marked
        last_h = [t for t in b.entries_ms if t > T - 60 * MIN_MS]
        held = any(p.pool == pool for p in b.open.values())
        since = T - b.pool_last_ms.get(pool, -10 ** 15)
        g = {"size": {"value": size, "need": "> 0", "ok": size > 0},
             "open": {"value": len(b.open), "need": f"< {lim['max_open']}", "ok": len(b.open) < lim["max_open"]},
             "held": {"value": held, "need": "False", "ok": not held},
             "cooldown": {"value": since, "need": f">= {POOL_COOLDOWN_MS}", "ok": since >= POOL_COOLDOWN_MS},
             "day_loss": {"value": round(pnl, 6), "need": f"> -{lim['day_loss_stop_sol']}", "ok": pnl > -lim["day_loss_stop_sol"]}}
        if arm == "X":
            g["x_rate"] = {"value": len([t for t in self.xcap.entries_ms if t > T - 60 * MIN_MS]), "need": f"< {H.X_CAP_PER_H}", "ok": self.xcap.remaining(T) > 0}
        else:
            extra = self.probes.extra_entries(arm, T)
            g["rate"] = {"value": len(last_h), "need": f"< {lim['entries_per_h']}" + (f" (+{extra} probe)" if extra else ""),
                         "ok": len(last_h) < lim["entries_per_h"] + extra}
        if young:
            half = max(1, lim["max_open"] // 2)
            n_young = sum(1 for p in b.open.values() if p.young)
            g["young_slots"] = {"value": n_young, "need": f"< {half}", "ok": n_young < half}
        return g

    def _open(self, arm: str, card: Mapping, size: float, plan: Mapping, dec_ms: int, gates: dict, extra: Mapping) -> str | None:
        lim = A.GEN_SPEC.code_limits
        snap = self.state.snapshot(card["id"], candles=False)
        price = snap.get("price") if snap else None
        bot = bot_id(arm)
        pid = position_id(bot, card["id"], dec_ms, self.slot)
        b = self.books[arm]
        used_probe = arm != "X" and len([t for t in b.entries_ms if t > dec_ms - 60 * MIN_MS]) >= lim["entries_per_h"]
        pos = Pos(arm=arm, pid=pid, pool=card["id"], mint=card.get("mint") or (snap or {}).get("mint"), size=size, entry_ms=dec_ms,
                  entry_price=float(price or 0.0), tokens_eq=(size / float(price)) if price else 0.0, stop_pct=plan.get("stop_pct"),
                  target_pct=plan.get("target_pct"), trail_pct=plan.get("trail_pct"), time_min=plan.get("time_min"), hard_stop_pct=lim["hard_stop_pct"],
                  max_hold_ms=int(lim["max_hold_min"] * MIN_MS), young=card.get("f", {}).get("age_tier") != "established", probe=used_probe,
                  token=dict(getattr(self, "token_of", {}).get(card["id"], {})))
        row = {"kind": "entry", "order_id": pid, "pool": card["id"], "card_sha256": A.p4.card_sha256(card), "card": card, "gates": gates, "size": size,
               "plan": dict(plan), "pos": asdict(pos), "decision_slot": self.slot, "tag": "regime_probe" if used_probe else None, **dict(extra)}
        self.log.append(arm, dec_ms, row)
        try:
            got = self.engine.buy(bot, card["id"], size, dec_ms, self.slot, meta={"arm": arm, "card_sha256": A.p4.card_sha256(card), "order_id": pid})
        except ArenaRefused as e:
            self.log.append(arm, dec_ms, {"kind": "order_refused", "order_id": pid, "side": "buy", "reason": str(e).split(":")[0], "detail": str(e)[:160]})
            self._count("order_refused")
            return None
        assert got == pid, "engine position id differs from the logged order id"
        b.open[pid] = pos
        b.entries_ms.append(dec_ms)
        b.pool_last_ms[card["id"]] = dec_ms
        if arm == "X":
            self.xcap.record(dec_ms)
        return pid

    def _skip(self, arm: str, card: Mapping, ms: int, reasons: Sequence, gates: Mapping | None = None) -> None:
        self.log.append(arm, ms, {"kind": "skip", "pool": card["id"], "card_sha256": A.p4.card_sha256(card), "reasons": list(reasons), "gates": dict(gates or {})})

    # -- X (rule book)
    def _x_round(self, tags: Sequence[Any], cards: Mapping[str, dict], T: int, tp: TickPolicy) -> None:
        if tp.x_halted:
            if tags:
                self.log.append("X", T, {"kind": "halt", "level": tp.level, "reason": tp.reason or "group_halted"})
            return
        plan = A.x_exit()
        for t in tags:
            c = cards.get(t.pool_id)
            if c is None:
                continue
            size = self._cap_size(c, A.SIZES[0])
            gates = self._gates("X", t.pool_id, size, c["f"].get("age_tier") != "established", T)
            if not all(g["ok"] for g in gates.values()):
                self._skip("X", c, T, [k for k, g in gates.items() if not g["ok"]], gates)
                continue
            self._open("X", c, size, plan, T, gates, {"setup": t.type, "z": t.z})

    # -- LLM entry round
    def _llm_entry_round(self, cards: list[dict], T: int, tp: TickPolicy) -> None:
        if not cards:
            return
        if tp.entries_halted:
            for a in self.llm_arm_names:
                self.log.append(a, T, {"kind": "halt", "level": tp.level, "reason": tp.reason or "group_halted"})
            return
        maxc = min(tp.max_cards, H.CARDS_PER_CALL)
        for i in range(0, len(cards), maxc):
            self._entry_call(cards[i:i + maxc], T, maxc)

    def _entry_call(self, chunk: list[dict], T: int, maxc: int) -> None:
        members = self.llm_arm_names
        if not self._call_ok(members, "entry", T):  # symmetric: one member at its cap stops the group
            self._count("entry_call_cap")
            return
        for c in chunk:
            self.cards.put(c, T)
        replies = {}
        for a in members:
            req = A.build_entry_request(a, self.playbooks[a], chunk, self.seq.get((a, utc_day(T)), 0), max_cards=maxc)
            replies[a] = self._call(a, req, T)
        if any(r.error in DENIED for r, _ in replies.values()):
            for a in members:
                self.log.append(a, replies[a][1], {"kind": "symmetry_abstain", "errors": {m: replies[m][0].error for m in members}})
            return
        for a in members:
            reply, recv = replies[a]
            for v, card in zip(A.validate_entry(reply, cards=chunk, knobs=self.knobs[a], received_ms=recv), chunk):
                self.invalid[a][0] += int(v.invalid)
                self.invalid[a][1] += 1
                self._on_verdict(a, v, card, recv)

    def _on_verdict(self, arm: str, v: Any, card: dict, recv: int) -> None:
        if not A.is_entry(v):
            self._skip(arm, card, recv, [v.action, *v.reasons] if v.action != "SKIP" else list(v.reasons) or ["SKIP"])
            return
        asked = A.model_written_size(v) or 0.0
        size = self._cap_size(card, asked)
        gates = self._gates(arm, card["id"], size, card["f"].get("age_tier") != "established", recv)
        if not all(g["ok"] for g in gates.values()):
            self._skip(arm, card, recv, [k for k, g in gates.items() if not g["ok"]], gates)
            return
        plan = {**A.GEN_SPEC.plan_defaults, **(v.fields.get("plan") or {})}
        self._open(arm, card, size, plan, recv, gates, {"conviction": v.fields.get("conviction"), "size_asked": asked})

    # -- exits
    def exits(self, T: int) -> None:
        for arm in ARM_ORDER:
            b = self.books[arm]
            for pid in sorted(b.open):
                pos = b.open.get(pid)
                if pos is None:
                    continue
                ret = mark_ret(pos, self.state.snapshot(pos.pool, candles=False))
                if ret is not None and pos.trail_pct is not None:
                    q = math.floor((1.0 + ret) * 100.0 + 1e-9) / 100.0
                    if q > pos.peak:
                        pos.peak = q
                        self.log.append(arm, T, {"kind": "peak", "pid": pid, "peak": q})
                if T - pos.entry_ms < ENTRY_SETTLE_MS:
                    continue
                why = exit_reason(pos, ret, T)
                if why:
                    self._sell(pos, why, T, ret)

    def _sell(self, pos: Pos, why: str, T: int, ret: float | None, dec_ms: int | None = None) -> bool:
        b = self.books[pos.arm]
        now = self._now(T)
        if self._sell_next.get(pos.pid, 0) > now:
            return False
        dec = T if dec_ms is None else dec_ms
        est = (ret or 0.0) * pos.size
        self.log.append(pos.arm, dec, {"kind": "sell", "pid": pos.pid, "pool": pos.pool, "reason": why, "est_ret": ret, "est_pnl_sol": est, "decision_slot": self.slot})
        try:
            self.engine.sell(bot_id(pos.arm), pos.pid, 1.0, dec, self.slot, reason=why)
        except ArenaRefused as e:
            early = SELL_EARLY in str(e)
            if not early:
                pos.sell_tries += 1
            self._count("sell_refused")
            self.log.append(pos.arm, dec, {"kind": "order_refused", "order_id": pos.pid, "side": "sell", "reason": str(e).split(":")[0], "detail": str(e)[:160]})
            if any(k in str(e) for k in SELL_ENGINE_DROPS):
                b.open.pop(pos.pid, None)
                self._count("sell_orphan")
            else:
                self._sell_next[pos.pid] = now + SELL_RETRY_MS  # the engine may still hold it: kept open, retried
            return False
        self._sell_next.pop(pos.pid, None)
        b.open.pop(pos.pid, None)
        day = utc_day(dec)
        b.realized[day] = b.realized.get(day, 0.0) + est
        return True

    # -- re-checks (minute ticks)
    def rechecks(self, T: int) -> None:
        tp = self.tp or self._begin_tick(T)
        if tp.rechecks_halted:
            return
        for arm in self.llm_arm_names:
            b = self.books[arm]
            if not b.open:
                continue
            due = []
            for pos in sorted(b.open.values(), key=lambda p: p.pid):
                ret = mark_ret(pos, self.state.snapshot(pos.pool, candles=False))
                if ret is None or T - pos.entry_ms < ENTRY_SETTLE_MS:
                    continue
                pct = ret * 100.0
                event = pct <= (pos.stop_pct or -1e9) / 2.0 or pct >= (pos.target_pct or 1e9) / 2.0
                if event or T - max(pos.last_recheck_ms, pos.entry_ms) >= A.RECHECK_MINUTES * MIN_MS * tp.recheck_mult:
                    due.append((pos, pct))
            maxc = min(tp.max_cards, H.CARDS_PER_CALL)
            for i in range(0, len(due), maxc):
                self._recheck_call(arm, due[i:i + maxc], T, maxc)

    def _recheck_call(self, arm: str, group: list[tuple[Pos, float]], T: int, maxc: int) -> None:
        if not self._call_ok([arm], "recheck", T):
            self._count("recheck_call_cap")
            return
        cards, poss, used = [], [], []
        for pos, pct in group:
            snap = self.state.snapshot(pos.pool)
            if snap is None:
                continue
            c = self._card(pos.pool, T, snap, list(self.state.pool(pos.pool).events), None, C.features(snap, C.norm_prints(list(self.state.pool(pos.pool).events)), as_of_ms=T))
            if c is None:
                continue
            cards.append(c)
            used.append(pos)
            poss.append({"id": pos.pool, "stop_pct": pos.stop_pct, "target_pct": pos.target_pct, "mark_ret_pct": round(pct, 4), "as_of_ms": T})
        if not cards:
            return
        req = A.build_recheck_request(arm, self.playbooks[arm], poss, cards, self.seq.get((arm, utc_day(T)), 0))
        reply, recv = self._call(arm, req, T)
        if reply.error in DENIED:
            self.log.append(arm, recv, {"kind": "recheck_denied", "error": reply.error})
            return
        by_pool = {p.pool: p for p in used}
        for v in A.validate_recheck(reply, positions=poss, received_ms=recv):
            pos = by_pool[v.id]
            pos.last_recheck_ms = T
            self.invalid[arm][0] += int(v.invalid)
            self.invalid[arm][1] += 1
            if v.action == "EXIT":
                self._sell(pos, "llm_exit", T, mark_ret(pos, self.state.snapshot(pos.pool, candles=False)), dec_ms=recv)
            elif v.action == "TIGHTEN" and pos.pid in self.books[arm].open:
                pos.stop_pct = v.fields.get("new_stop_pct", pos.stop_pct)
                pos.target_pct = v.fields.get("new_target_pct", pos.target_pct)
                self.log.append(arm, recv, {"kind": "tighten", "pid": pos.pid, "stop_pct": pos.stop_pct, "target_pct": pos.target_pct})

    # -- the intraday regime trigger
    def regime_round(self, T: int) -> list[str]:
        """A new radar HIGH event with relevance direct in the newest block (as_of before T) triggers one review call each for L and P through
        `regime_review_fn`, at most REGIME_TRIGGERS_PER_DAY a day. Only the block mapping is handed over; L-nr and F get nothing."""
        blk = self.regime.current(T)
        ids = self.regime.new_direct_high(blk)
        if not ids:
            return []
        self.regime.seen_ids.update(ids)
        day = utc_day(T)
        if self.regime_triggers.get(day, 0) >= REGIME_TRIGGERS_PER_DAY:
            self.log.append("SCAN", T, {"kind": "regime_trigger_capped", "ids": ids})
            return []
        self.regime_triggers[day] = self.regime_triggers.get(day, 0) + 1
        self.log.append("SCAN", T, {"kind": "regime_trigger", "ids": ids, "block_as_of_ms": blk["as_of_ms"], "n_today": self.regime_triggers[day]})
        for arm in A.regime_arms():
            b = self.regime.block_for(arm, blk)
            if self.regime_review_fn is None:
                self.log.append(arm, T, {"kind": "regime_review_pending", "ids": ids})
            else:
                try:
                    self.regime_review_fn(arm, b, T)
                except Exception as e:  # noqa: BLE001 - a failing hook is logged, never swallowed silently
                    self.log.append(arm, T, {"kind": "regime_review_error", "error": type(e).__name__})
        return ids

    # -- the tick
    def tick(self, T: int) -> None:
        self._begin_tick(T)  # the ONLY place the group policy is computed
        self.poll_to(T)
        self.exits(T)
        if (T // 1000) % 60 == H.SCAN_SECOND_MS // 1000:
            self.scan(T)
            self.rechecks(T)
            self.regime_round(T)

    def run(self, start_ms: int, end_ms: int, sleep: Callable[[float], None] = time.sleep) -> None:
        T = start_ms
        while T < end_ms and not self._stop:
            if self.cfg.live:
                wait = (T + 1000 - self.now_fn()) / 1000.0
                if wait > 0:
                    sleep(wait)
            self.tick(T)
            T += TICK_MS

    def request_stop(self, *_a: Any) -> None:
        self._stop = True


def stack_config(feed_delay_slots: int) -> Any:
    from tools.arena_paper import StackConfig

    if isinstance(feed_delay_slots, bool) or not isinstance(feed_delay_slots, int) or feed_delay_slots < 0:
        raise ValueError("feed_delay_slots must be a whole number of slots >= 0")
    return StackConfig(**{**STACK_FIELDS, "feed_delay_slots": feed_delay_slots})


def main(argv: list[str] | None = None) -> int:
    """A MiScusi job: `python3 -m tools.hv6_runner ...` (submit with resumable: true; it resumes from its own hash-chained log). SIGTERM stops cleanly."""
    ap = argparse.ArgumentParser(description="HV6 runner: paper only. The API key is read by tools.haiku_caller from the environment; it is never printed.")
    ap.add_argument("--tape-dir", default=H.TAPE_ROOT)
    ap.add_argument("--root", default=H.HV6_ROOT)
    ap.add_argument("--ledger-root", default=None, help="engine ledger dir; default <root>/engine-ledger")
    ap.add_argument("--registry", default="/data/mal/arena/bots.json", help="handed to the paper engine; this runner never opens it")
    ap.add_argument("--frozen-sha", required=True)
    ap.add_argument("--clock-start-ms", type=int, required=True)
    ap.add_argument("--feed-delay-slots", type=int, required=True)
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--end-ms", type=int, default=None)
    a = ap.parse_args(argv)
    try:
        refuse_hv4_chain_state()
        H.check(a.root, "w")
        H.check(a.tape_dir, "r")
        ledger_root = a.ledger_root or str(Path(a.root) / "engine-ledger")
        H.check(ledger_root, "w")
    except (RuntimeError, PermissionError) as e:
        print(f"hv6_runner: refused: {e}", file=sys.stderr)
        return 2
    from tools.arena_paper import ArenaEngine

    guard = A.BudgetGuard(a.root)
    model = A.StubModel(guard=guard) if a.stub else A.CallerModel(guard)
    chain = live_chain_source(a.root)
    cfg = RunnerConfig(root=a.root, frozen_sha=a.frozen_sha, clock_start_ms=a.clock_start_ms, live=True)
    run = Runner(cfg, ArenaState(a.tape_dir), ArenaEngine(ledger_root, a.registry, cfg=stack_config(a.feed_delay_slots)), model, chain, guard)
    signal.signal(signal.SIGTERM, run.request_stop)
    print(canon({"resumed": run.resume()}))
    start = max(a.clock_start_ms, int(time.time() * 1000))
    run.run(start - start % TICK_MS, a.end_ms or 2 ** 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
