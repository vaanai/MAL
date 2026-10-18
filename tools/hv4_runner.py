#!/usr/bin/env python3
"""hv4_runner: the loop of Haiku trader v4 (HV4), 13 arms on one paper stream (DESIGN sections 1-5, 7; build item 4).

PAPER only. No wallet or trading key. It steps `tools.arena_state.ArenaState` with poll(until_ms=T-1) (the strict as-of cut), scans every 5 minutes with
`tools.hv4_cards` (filters, setups, code_vetoes, build_card), asks the shared risk agent and the 7 LLM arms through `tools.hv4_model` (every call is
gated by `tools.hv4_budget`, with symmetric halts), runs the code exits, and sends every order to `tools.arena_paper.ArenaEngine.buy/sell` with
decision_ms equal to the instant the LLM answer arrived (a code exit: the detection instant). The ONLY credential path is `tools.haiku_caller` via
`CallerModel` (it reads ANTHROPIC_API_KEY from the environment at call time); this module never reads, prints or logs it.

DECISION LOG (DEC-031 section 1.2). Per arm and UTC day, `<root>/decisions/<arm>/<day>.jsonl`, hash-chained: every row carries seq, prev and sha
(sha256 of prev + the canonical row without sha). A decision row is appended (flock, fsync) BEFORE the order is sent, so before any fill is priced; a
refused order adds an `order_refused` row. Nothing is generated or replayed afterwards. `resume()` verifies the chain and rebuilds positions, counters
and risk grades from it after a restart; a broken chain raises LogChainError and the runner does not start.

CODE-SIDE NUMBERS. The mark (stop, target, trail, day loss stop) is a code-side monitor: the sell quote of the stake at the state's spot and reserves
(constant product), relative to the stake. It is not the scored P&L; arena_score reads the engine's ledger. After a restart the trail peak restarts from
the current mark (disclosed). BST exits at s0 + 330 s of the pool's first print (the tick that detects it is the decision instant, within 1 s).

NOT HERE: the nightly reviewer, manifests and placebo (hv4-review), the read (hv4-read). `playbook_overrides` is the hook the reviewer's versions use.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import fcntl
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from tools import hv4_arms as arms
from tools import hv4_cards as C
from tools.arena_h5_signal import H5Signal, bst_card, safe_path
from tools.arena_paper import ArenaEngine, ArenaRefused, canon, register_bot
from tools.arena_state import ArenaState
from tools.haiku_caller import canonical_bytes
from tools.hv4_budget import BudgetGuard, utc_day
from tools.hv4_model import Model, ModelReply
from tools.hv4_prompts import (Playbook, build_entry_request, build_recheck_request, build_risk_request, card_sha256, load_playbook, load_template,
                               sha256_hex)
from tools.hv4_validate import Knobs, PredicateError, final_size, parse_watch_predicate, validate_entry, validate_recheck, validate_risk
from tools.hv4_validate import numeric_fields as card_numeric

HV4_ROOT = "/data/mal/arena/haiku-v4"
GENESIS = "0" * 64
MIN_MS = 60_000
SCAN_EVERY_MS = 5 * MIN_MS
SCAN_SECOND_MS = 2_000  # the scanner ticks at second :02
TICK_MS = 1_000
BOARD_EVERY_MS = 15 * MIN_MS
BOARD_SIZE = 10
TOP_K_PER_PERSONA = 8  # cards per persona per scan that go to the LLM arms (two calls of 4)
RISK_REFRESH_MS = 30 * MIN_MS
RISK_HIDE_MS = 60 * MIN_MS
POOL_COOLDOWN_MS = 30 * MIN_MS
ENTRY_SETTLE_MS = 3_000  # no code exit before the slower entry leg can land (the engine refuses an earlier sell)
SELL_RETRY_MAX = 5
R_SEED = 1
BST_EXIT_MS = 330_000
CALL_CAP_PER_H = {("entry", None): 30, ("risk", None): 150, ("recheck", "CON"): 60, ("recheck", "BIG"): 30}
DENIED = ("group_halted", "budget_exceeded", "day_cap", "month_guard", "ledger_unreadable")
LANE_G_PERSONAS = ("CON", "BIG")
MARKS_ARM_ORDER = tuple(arms.ARMS)


class LogChainError(RuntimeError):
    """The decision log's hash chain is broken (or a line is torn). The runner refuses to start."""


def bot_id(arm: str) -> str:
    return f"hv4-{arm}"


def position_id(bot: str, pool: str, decision_ms: int, decision_slot: int) -> str:
    """The engine's deterministic position id (arena_paper.ArenaEngine.buy), so the order id can be logged before the order is sent."""
    return hashlib.sha256(f"{bot}|{pool}|{int(decision_ms)}|{int(decision_slot)}".encode()).hexdigest()[:16]


def _u01(*parts: Any) -> float:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16) / 2 ** 32


# ------------------------------------------------------------------------------------------------------------------------------ decision log
class DecisionLog:
    def __init__(self, root: str | os.PathLike) -> None:
        self.dir = Path(root) / "decisions"
        self._head: dict[str, tuple[str, int]] = {}

    def files(self, name: str) -> list[Path]:
        d = self.dir / name
        return sorted(d.glob("*.jsonl")) if d.exists() else []

    def rows(self, name: str) -> list[dict]:
        out = []
        for f in self.files(name):
            for line in f.read_bytes().splitlines():
                if line.strip():
                    out.append(json.loads(line))
        return out

    def verify(self, name: str) -> tuple[str, int]:
        prev, seq = GENESIS, -1
        for f in self.files(name):
            data = f.read_bytes()
            if data and not data.endswith(b"\n"):
                raise LogChainError(f"{name}: torn last line in {f.name}")
            for line in data.splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    sha = row.pop("sha")
                except (ValueError, KeyError):
                    raise LogChainError(f"{name}: unreadable row in {f.name}") from None
                if row.get("prev") != prev or row.get("seq") != seq + 1 or sha256_hex(prev + canon(row)) != sha:
                    raise LogChainError(f"{name}: chain broken at seq {seq + 1} in {f.name}")
                prev, seq = sha, row["seq"]
        self._head[name] = (prev, seq)
        return prev, seq

    def head(self, name: str) -> tuple[str, int]:
        return self._head.get(name) or self.verify(name)

    def append(self, name: str, ms: int, body: Mapping[str, Any]) -> dict:
        prev, seq = self.head(name)
        row = {"seq": seq + 1, "arm": name, "ms": int(ms), "prev": prev, **body}
        row["sha"] = sha256_hex(prev + canon(row))
        path = self.dir / name / f"{utc_day(ms)}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, canon(row).encode() + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        self._head[name] = (row["sha"], row["seq"])
        return row

    def md5(self) -> str:
        h = hashlib.md5()
        for d in sorted(p for p in self.dir.iterdir() if p.is_dir()) if self.dir.exists() else []:
            for f in sorted(d.glob("*.jsonl")):
                h.update(f.name.encode() + b"\0" + f.read_bytes())
        return h.hexdigest()

    def day_sha256(self, name: str, day: str) -> str | None:
        f = self.dir / name / f"{day}.jsonl"
        return hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else None


# ------------------------------------------------------------------------------------------------------------------------------ chain facts
class CardSink:
    """Every card shown to an LLM arm, by sha256, one JSONL file per UTC day under <root>/cards/ in the CardStore format of tools/hv4_review (#715):
    one line {"sha": <card sha256>, "card": <card>} in canonical JSON, appended under flock, deduplicated by sha within the process. The reviewer reads
    the cards of skipped decisions here, because a skip row in the decision log keeps only the hash. The day is the UTC day of the shown instant."""

    def __init__(self, root: str | os.PathLike) -> None:
        self.dir = Path(root) / "cards"
        self._seen: set[str] = set()

    def put(self, card: Mapping[str, Any], ms: int | None = None) -> str:
        sha = card_sha256(dict(card))
        if sha in self._seen:
            return sha
        self._seen.add(sha)
        day = utc_day(int(ms if ms is not None else card.get("as_of_ms", 0)))
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.dir / f"{day}.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, canonical_bytes({"sha": sha, "card": card}) + b"\n")
        finally:
            os.close(fd)
        return sha


_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}


def local_veto(vetoes: Any, card: Mapping[str, Any]) -> str | None:
    """The first local veto (a WATCH-grammar predicate the reviewer added; it only blocks) that holds on the card, else None. Same rule as
    tools.hv4_review.vetoed: a veto that cannot be parsed against this card never fires."""
    from tools.hv4_validate import numeric_fields

    nums = numeric_fields(card)
    for v in vetoes or ():
        try:
            terms = parse_watch_predicate(v, nums)
        except PredicateError:
            continue
        if all(f in nums and _OPS[op](nums[f], n) for f, op, n in terms):
            return v
    return None


def review_version_source(root: str | os.PathLike, frozen_sha: str | None = None) -> Callable[[str, int], Any]:
    """The live version source: tools.hv4_review.active_version, imported on first use (lazily, so this module does not depend on the reviewer module at
    import time and there is no stack cycle). It verifies the arm's WHOLE manifest chain and raises ManifestRefused on anything wrong; the runner then
    halts that arm's entries (code exits go on). The manifest's commit must be the frozen sha."""
    def source(arm: str, now_ms: int) -> Any:
        from tools import hv4_review

        kw = {"frozen_sha": frozen_sha} if frozen_sha else {}
        return hv4_review.active_version(root, arm, now_ms, **kw)

    return source


class ChainSource(Protocol):
    def chain_part(self, pool_id: str, mint: str | None, now_ms: int, slot: int) -> dict | None: ...
    def mint_gate(self, mint: str | None, now_ms: int, slot: int) -> dict | None: ...
    def credit_report(self) -> dict: ...


class HV4ChainSource:
    """The real source: tools.hv4_chain over public RPC, or Helius when MAL_HV4_RPC=helius (the public endpoint refuses getTokenLargestAccounts, so the
    holder block needs Helius). A failed mint read gives None (no card, fail closed); a failed holder read leaves the holder keys out (null block)."""

    def __init__(self, chain: Any = None) -> None:
        from tools.hv4_chain import HV4Chain  # lazy: keeps this module importable without the RPC stack in tests

        self.chain = chain if chain is not None else HV4Chain()

    def credit_report(self) -> dict:
        return self.chain.credit_report()

    @staticmethod
    def _ext_values(mf: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in dict(getattr(mf, "ext_info", {}) or {}).items():
            key = str(k).lower().replace("_", "")
            if "transferfee" in key and isinstance(v, Mapping):
                bps = [x for x in (v.get("older_bps"), v.get("newer_bps")) if isinstance(x, int)]
                if bps:
                    out["transfer_fee_bps"] = max(bps)
            if "defaultaccountstate" in key and isinstance(v, Mapping) and isinstance(v.get("state"), str):
                out["default_account_state"] = v["state"]
        return out

    def mint_gate(self, mint: str | None, now_ms: int, slot: int) -> dict | None:
        if not mint:
            return None
        mf = self.chain.mint_facts(mint)
        if not mf.ok:
            return None
        mh = self.chain.mayhem_flag(mint)
        reasons = list(mf.veto_reasons) + (["mayhem"] if mh.counts_as_mayhem else [])
        token = {"token_program": mf.token_program_id, "extensions": list(mf.extensions), **self._ext_values(mf)}
        return {"ok": not reasons and mf.mint_authority is None and mf.freeze_authority is None, "reasons": reasons, "token": token,
                "as_of_ms": mf.as_of_ms, "as_of_slot": mf.as_of_slot}

    def chain_part(self, pool_id: str, mint: str | None, now_ms: int, slot: int) -> dict | None:
        if not mint:
            return None
        mf = self.chain.mint_facts(mint)
        if not mf.ok or mf.as_of_ms is None or mf.as_of_slot is None:
            return None
        mh = self.chain.mayhem_flag(mint, pool=pool_id)
        facts: dict[str, Any] = {"as_of_ms": mf.as_of_ms, "as_of_slot": mf.as_of_slot, "mint_authority": mf.mint_authority,
                                 "freeze_authority": mf.freeze_authority, "token_program": mf.token_program_id, "extensions": list(mf.extensions),
                                 "mayhem": mh.is_mayhem, **self._ext_values(mf)}
        hd = self.chain.largest_holders(mint, token_program=mf.token_program_id, pool=pool_id, max_age_s=C.CHAIN_HOLDERS_MAX_AGE_MS / 1000.0)
        if hd.ok and hd.as_of_ms is not None:
            facts.update({"top1_frac": hd.top1_frac, "top10_frac": hd.top10_frac, "holders_as_of_ms": hd.as_of_ms})
        return facts


def holder_cover(snap: Mapping[str, Any]) -> bool | None:
    """The coverage flag of the tape's holder_count: True when the tape saw the pool from its creation with no data-loss gap since; False when it
    did not; None when that cannot be told. Never anything but a bool or None."""
    ck, gaps, age, seen = snap.get("created_known"), snap.get("gaps_since_first_seen"), snap.get("age_s"), snap.get("age_since_first_seen_s")
    if not isinstance(ck, bool):
        return None
    if not ck:
        return False
    nums = (gaps, age, seen)
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or x != x for x in nums):
        return None
    return bool(gaps == 0 and age - seen <= 60.0)


class QuoteHistory:
    """Real quote (SOL) of every PumpSwap pool, sampled once a minute, for the 15-min-ago and peak facts of veto 7."""

    def __init__(self, keep_ms: int = 60 * MIN_MS + 5 * MIN_MS) -> None:
        self.keep_ms = keep_ms
        self.s: dict[str, list[tuple[int, float]]] = {}

    def sample(self, state: ArenaState, t_ms: int) -> None:
        for p in state.pools_wsol(lambda p: p.prog == "pumpswap" and p.q_real is not None):
            lst = self.s.setdefault(p.id, [])
            lst.append((t_ms, p.q_real / 1e9))
            while lst and lst[0][0] < t_ms - self.keep_ms:
                lst.pop(0)

    def ref(self, pool: str, t_ms: int) -> tuple[float, int] | None:
        want = t_ms - C.QUOTE_REF_AGE_MS
        best = min(((abs(ts - want), ts, q) for ts, q in self.s.get(pool, ()) if ts < t_ms), default=None)
        return None if best is None or best[0] > C.QUOTE_REF_TOL_MS else (best[2], best[1])

    def peak(self, pool: str, t_ms: int) -> float | None:
        qs = [q for ts, q in self.s.get(pool, ()) if ts < t_ms]
        return max(qs) if qs else None


# ------------------------------------------------------------------------------------------------------------------------------ positions
@dataclass
class Pos:
    arm: str
    pid: str
    pool: str
    mint: str | None
    persona: str
    role: str  # L F P X R
    size: float
    entry_ms: int
    entry_price: float
    tokens_eq: float
    stop_pct: float | None
    target_pct: float | None
    trail_pct: float | None
    time_min: int | None
    hard_stop_pct: float | None
    max_hold_ms: int
    young: bool
    timer_ms: int | None = None  # BST: s0 + 330 s
    partner: str | None = None  # R: the L position id it follows
    token: dict = field(default_factory=dict)
    peak: float = 1.0
    last_recheck_ms: int = 0
    sell_tries: int = 0
    flagged_events: int = 0


def mark_ret(pos: Pos, snap: Mapping[str, Any]) -> float | None:
    """Code-side mark: the constant-product sell quote of the position's tokens at the state's spot, over the stake, minus 1. None when unpriced."""
    res = snap.get("reserves") if snap.get("quote_wsol") is True else None
    price = snap.get("price")
    q = res.get("quote_eff_sol") if isinstance(res, Mapping) else None
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in (price, q)):
        return None
    val = pos.tokens_eq * float(price)
    return val * float(q) / (float(q) + val) / pos.size - 1.0


def exit_reason(pos: Pos, ret: float | None, now_ms: int) -> str | None:
    """Code exits, in order: BST timer; hard stop; plan stop; target; trail; plan time; max hold. R positions only follow their L (and max hold)."""
    if pos.role == "R" and pos.persona != "BST":
        return "max_hold" if now_ms - pos.entry_ms >= pos.max_hold_ms else None
    if pos.timer_ms is not None:
        return "timer" if now_ms >= pos.timer_ms else None
    if ret is not None:
        pct = ret * 100.0
        if pos.hard_stop_pct is not None and pct <= pos.hard_stop_pct:
            return "hard_stop"
        if pos.stop_pct is not None and pct <= pos.stop_pct:
            return "stop"
        if pos.target_pct is not None and pct >= pos.target_pct:
            return "target"
        lim = arms.PERSONA_SPECS[pos.persona].code_limits
        if pos.trail_pct is not None and (pos.peak - 1.0) * 100.0 >= lim.get("trail_arms_after_pct", 50.0) and 1.0 + ret <= pos.peak * (1.0 - pos.trail_pct / 100.0):
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
    realized: dict[str, float] = field(default_factory=dict)  # UTC day -> code-side realized SOL
    watches: list[dict] = field(default_factory=list)
    bst_seen: int = 0
    bst_taken: int = 0


@dataclass
class RunnerConfig:
    root: str = HV4_ROOT
    frozen_sha: str = ""
    clock_start_ms: int = 0
    live: bool = False  # live: decision_ms is the wall clock when the answer is in hand; sim: as_of + the reply's latency (deterministic)
    strict_leakage: bool = False
    seed: int = R_SEED


class Runner:
    def __init__(self, cfg: RunnerConfig, state: ArenaState, engine: ArenaEngine, model: Model, chain: ChainSource, guard: BudgetGuard | None = None,
                 h5: H5Signal | None = None, now_fn: Callable[[], int] | None = None, playbook_overrides: Mapping[str, Playbook] | None = None,
                 binding: Mapping[str, Any] | None = None, version_source: Callable[[str, int], Any] | None = None) -> None:
        if cfg.live:
            require_frozen_checkout(binding or {}, cfg.frozen_sha)  # a live runner must prove its checkout
        self.binding = dict(binding) if binding is not None else None
        self.cfg, self.state, self.engine, self.model, self.chain, self.guard, self.h5 = cfg, state, engine, model, chain, guard, h5
        self.now_fn = now_fn or (lambda: time.time_ns() // 1_000_000)
        self.log = DecisionLog(cfg.root)
        self.books = {a: Book(a) for a in arms.ARMS}
        self.playbooks = {n: load_playbook(n) for n in (*arms.PERSONAS, arms.RISK_LEDGER)}
        self.playbooks.update(dict(playbook_overrides or {}))
        self.knobs = {a: Knobs.v0(s.persona) for a, s in arms.ARMS.items() if s.llm}
        # Per-arm version in force: the L, F and P arms of a persona can differ (the reviewer rewrites a learning arm's playbook, knobs and local vetoes).
        self.arm_state: dict[str, dict] = {}
        for a, sp in arms.ARMS.items():
            if sp.llm:
                ps = arms.PERSONA_SPECS[sp.persona]
                self.arm_state[a] = {"version": 0, "playbook": self.playbooks[sp.persona], "knobs": {"min_conviction": ps.min_conviction_v0, **ps.plan_defaults},
                                     "vetoes": (), "manifest_sha256": None}
        self.version_source = version_source  # None = no reviewer wiring: every arm stays on its v0 playbook, knobs and no vetoes
        self.arm_halt: dict[str, str] = {}  # arm -> why its LLM entries and re-checks are stopped (a refused manifest); code exits go on
        self._versions_loaded = False
        self.cards = CardSink(cfg.root)
        self.limiter = C.SetupLimiter()
        self.quotes = QuoteHistory()
        self.risk: dict[str, dict] = {}
        self.seq: dict[tuple[str, str], int] = {}
        self.calls: dict[tuple[str, str], list[int]] = {}
        self.eligible: dict[str, dict] = {}
        self.token_of: dict[str, dict] = {}
        self.slot = 0
        self.trig_q: list[dict] = []
        self.counters: dict[str, int] = {}
        self.hook_errors: list[str] = []
        self.sim_now = 0  # sim mode: the engine's wall clock is the sim instant (its stale / future checks compare decision_ms with it)
        if not cfg.live:
            engine.now_fn = lambda: self.sim_now
        self.invalid = {a: [0, 0] for a in arms.ARMS if arms.ARMS[a].llm}  # arm -> [invalid, total] verdicts (the 10% NOT EVALUABLE rule is read later)
        orig = state.apply

        def apply(row: dict) -> None:
            orig(row)
            self._on_row(row)

        state.apply = apply  # poll() calls self.apply: the engine and the H5 signal see exactly the rows the state sees, in order

    # -- plumbing
    def _count(self, k: str, n: int = 1) -> None:
        self.counters[k] = self.counters.get(k, 0) + n

    def _on_row(self, row: dict) -> None:
        try:
            s = row.get("slot")
            if row.get("type") == "trade" and isinstance(s, int) and s > self.slot:
                self.slot = s
            self.engine.on_row(row)
            if self.h5 is not None:
                self.trig_q.extend(self.h5.feed(row))
        except Exception as e:  # noqa: BLE001 - recorded, never swallowed silently
            self.hook_errors.append(type(e).__name__)

    def _now(self, T: int) -> int:
        return self.now_fn() if self.cfg.live else T

    def _decision_ms(self, as_of: int, reply: ModelReply | None) -> int:
        return self.now_fn() if self.cfg.live else as_of + int(reply.latency_ms if reply else 0)

    def poll_to(self, T: int) -> None:
        self.state.poll(until_ms=T - 1)

    def playbook_for(self, arm: str) -> Playbook:
        return self.arm_state[arm]["playbook"]

    def manifest_sha(self, arm: str) -> str:
        st = self.arm_state[arm]
        if st["manifest_sha256"]:
            return st["manifest_sha256"]  # the reviewer's manifest line, hashed and chained
        pb = st["playbook"]
        return sha256_hex(canon({"arm": arm, "playbook": pb.sha256, "min_conviction": self.knobs[arm].min_conviction,
                                 "templates": {k: load_template(k).sha256 for k in ("entry", "recheck", "risk")}}))

    # -- registration (bots.json lines, before the clock)
    def register_all(self, registry_path: str, now_ms: int | None = None, verify_sha: bool = True) -> dict[str, dict]:
        out = {}
        for a, spec in arms.ARMS.items():
            ids = [self.model.model] if spec.llm else []
            out[a] = register_bot(bot_id(a), self.cfg.frozen_sha, self.cfg.clock_start_ms, registry_path, deciding_stake=0.25, model_ids=ids,
                                  now_ms=now_ms, verify_sha=verify_sha)
        return out

    # -- resume
    def resume(self) -> dict[str, int]:
        n = {"rows": 0, "open": 0}
        for name in (*arms.ARMS, "SCAN", "RISK"):
            self.log.verify(name)
            for r in self.log.rows(name):
                n["rows"] += 1
                self._replay_row(name, r)
        self.engine.recover()
        if self.binding is not None:
            self.log.append("SCAN", self.now_fn() if self.cfg.live else self.sim_now, {"kind": "start", "binding": self.binding, "frozen_sha": self.cfg.frozen_sha})
        n["open"] = sum(len(b.open) for b in self.books.values())
        return n

    def _replay_row(self, name: str, r: Mapping[str, Any]) -> None:
        k = r.get("kind")
        day = utc_day(r["ms"])
        if k == "llm_call":
            self.seq[(name, day)] = self.seq.get((name, day), 0) + 1
            self.calls.setdefault((name, r["call"]), []).append(r["ms"])
        elif k == "risk_grade":
            self.risk[r["pool"]] = dict(r["state"])
        elif name in self.books:
            b = self.books[name]
            if k == "entry" and r.get("pos"):
                p = Pos(**r["pos"])
                b.open[p.pid] = p
                b.entries_ms.append(r["ms"])
                b.pool_last_ms[p.pool] = r["ms"]
                if r.get("lane") == "H":
                    b.bst_taken += 1
                    b.bst_seen += 1
            elif k in ("entry", "skip") and r.get("lane") == "H":
                b.bst_seen += 1
            elif k == "order_refused" and r.get("side") == "buy":
                b.open.pop(r["order_id"], None)
            elif k == "tighten" and r["pid"] in b.open:
                p = b.open[r["pid"]]
                p.stop_pct = r.get("stop_pct", p.stop_pct)
                p.target_pct = r.get("target_pct", p.target_pct)
            elif k == "sell":
                b.open.pop(r["pid"], None)
                b.realized[day] = b.realized.get(day, 0.0) + float(r.get("est_pnl_sol") or 0.0)
            elif k == "watch_add":
                b.watches.append(dict(r["watch"]))
            elif k == "watch_end":
                b.watches = [w for w in b.watches if w["id"] != r["id"]]

    # -- model calls
    def _call_ok(self, names: list[str], kind: str, persona: str | None, T: int) -> bool:
        cap = CALL_CAP_PER_H.get((kind, persona)) or CALL_CAP_PER_H[(kind, None)]
        for n in names:
            lst = [t for t in self.calls.get((n, kind), []) if t > T - 60 * MIN_MS]
            self.calls[(n, kind)] = lst
            if len(lst) >= cap:
                return False
        return True

    def _call(self, name: str, req: Any, T: int) -> tuple[ModelReply, int]:
        reply = self.model.complete(req, self._now(T))
        recv = self._decision_ms(T, reply)
        self.sim_now = max(self.sim_now, recv)
        day = utc_day(recv)
        seq = self.seq.get((name, day), 0)
        self.seq[(name, day)] = seq + 1
        self.calls.setdefault((name, req.kind), []).append(recv)
        self.log.append(name, recv, {"kind": "llm_call", "call": req.kind, "ids": list(req.ids), "card_shas": list(req.card_shas), "model": self.model.model,
                                     "request_sha256": reply.request_sha256 or req.request_sha256(), "response_sha256": reply.response_sha256,
                                     "template_sha256": req.template_sha256, "playbook_sha256": req.playbook_sha256, "playbook_version": req.playbook_version,
                                     "manifest_sha256": self.manifest_sha(name) if name in self.knobs else None, "nonce": req.nonce, "req_seq": req.seq,
                                     "latency_ms": reply.latency_ms, "cost_usd": reply.cost_usd, "usage": reply.usage, "replayed": reply.replayed,
                                     "error": reply.error, "stop_reason": reply.stop_reason, "as_of_ms": T})
        return reply, recv

    def _next_seq(self, name: str, T: int) -> int:
        return self.seq.get((name, utc_day(T)), 0)

    # -- as-of
    def _leak(self, T: int, pool: str, exc: Exception) -> None:
        self._count("leakage_refused")
        self.log.append("SCAN", T, {"kind": "as_of_refusal", "pool": pool, "detail": str(exc)[:160]})
        if self.cfg.strict_leakage:
            raise exc

    # -- cards
    def _tape_facts(self, snap: Mapping[str, Any], T: int) -> dict[str, Any]:
        p = self.state.pool(snap["id"])
        facts: dict[str, Any] = {"holder_count": sum(1 for v in p.net.values() if v > 0) if p is not None else None, "holder_count_cover": holder_cover(snap),
                                 "creator_sold_frac": None, "same_slot_supply": None, "sniper_share": None, "sniper_still_holding": None}
        ref = self.quotes.ref(snap["id"], T)
        if ref is not None:
            facts.update({"quote_sol_15m_ago": ref[0], "quote_sol_15m_ago_as_of_ms": ref[1]})
        pk = self.quotes.peak(snap["id"], T)
        if pk is not None:
            facts["quote_sol_peak"] = pk
        return facts

    def _card(self, pool_id: str, T: int, tag: C.SetupTag | None, feats: Mapping | None = None, snap: Mapping | None = None) -> dict | None:
        snap = snap or self.state.snapshot(pool_id)
        if snap is None:
            return None
        p = self.state.pool(pool_id)
        prints = list(p.events) if p is not None else []
        part = self.chain.chain_part(pool_id, snap.get("mint"), T, self.slot)
        if part is None:
            self._count("chain_none")
            self.log.append("SCAN", T, {"kind": "card_refused", "pool": pool_id, "reasons": ["chain_unavailable"]})
            return None
        chain = {**part, **self._tape_facts(snap, T)}
        self.token_of[pool_id] = {k: part[k] for k in ("token_program", "extensions", "transfer_fee_bps", "default_account_state") if k in part}
        try:
            C.assert_as_of(snap, T, prints=prints, chain=chain, as_of_slot=self.slot)
            return C.build_card(snap, prints, chain, as_of_ms=T, as_of_slot=self.slot, setup=tag)
        except C.LeakageError as e:
            self._leak(T, pool_id, e)
        except C.CardRefused as e:
            self._count("card_refused")
            self.log.append("SCAN", T, {"kind": "card_refused", "pool": pool_id, "reasons": list(e.reasons)})
        return None

    # -- the scan
    def scan(self, T: int) -> None:
        self.poll_to(T)
        slot = self.slot
        cand, tags, feat_of, snap_of = [], [], {}, {}
        for p in self.state.pools_wsol(lambda p: p.prog == "pumpswap"):
            try:
                fr = C.filters(self.state.snapshot(p.id, candles=False), as_of_ms=T)
            except C.LeakageError as e:
                self._leak(T, p.id, e)
                continue
            if not fr.passed:
                continue
            snap = self.state.snapshot(p.id)
            try:
                feats = C.features(snap, list(p.events), as_of_ms=T)
            except C.LeakageError as e:
                self._leak(T, p.id, e)
                continue
            cand.append(p.id)
            feat_of[p.id], snap_of[p.id] = feats, snap
            tags.extend(C.setups(feats, pool_id=p.id, as_of_ms=T, limiter=self.limiter))
        ranked = C.rank_setups(tags)
        per_persona: dict[str, list[C.SetupTag]] = {pr: [t for t in ranked if t.persona == pr][:TOP_K_PER_PERSONA] for pr in LANE_G_PERSONAS}
        for pr in LANE_G_PERSONAS:
            for t in per_persona[pr]:
                self.limiter.record(t.pool_id, pr, T)
        tagged = {t.pool_id for ts in per_persona.values() for t in ts}
        board: list[str] = []
        if (T // 1000) % (BOARD_EVERY_MS // 1000) == SCAN_SECOND_MS // 1000 and self._board_on(T):
            rest = sorted((p for p in cand if p not in tagged), key=lambda p: (-(feat_of[p].get("vol_60m") or 0.0), p))
            board = rest[:BOARD_SIZE]
        cards: dict[tuple[str, str], dict] = {}  # (pool, persona) -> card, byte-identical for every arm of the persona
        for pr in LANE_G_PERSONAS:
            for t in per_persona[pr]:
                c = self._card(t.pool_id, T, t, snap=snap_of[t.pool_id])
                if c is not None:
                    cards[(t.pool_id, pr)] = c
        for pid in board:
            c = self._card(pid, T, None, snap=snap_of[pid])
            if c is not None:
                for pr in LANE_G_PERSONAS:
                    cards[(pid, pr)] = c
        self.log.append("SCAN", T, {"kind": "scan", "as_of_slot": slot, "n_filter_pass": len(cand), "n_tags": {pr: len(per_persona[pr]) for pr in LANE_G_PERSONAS},
                                    "n_board": len(board), "n_cards": len(cards), "card_shas": sorted(card_sha256(c) for c in cards.values())})
        self._risk_round(cards, T)
        self.eligible = {c["id"]: c for (pid, pr), c in sorted(cards.items()) if self._cleared(c["id"], T)}
        for pr in LANE_G_PERSONAS:
            pcards = [cards[k] for k in sorted(cards) if k[1] == pr]
            self._x_round(pr, per_persona[pr], cards, T)
            self._llm_entry_round(pr, [c for c in pcards if self._cleared(c["id"], T)], T)

    def _board_on(self, T: int) -> bool:
        return self.guard is None or all(self.guard.policy(g, self._now(T)).board_enabled for g in LANE_G_PERSONAS)

    # -- risk agent
    def _cleared(self, pool: str, T: int) -> bool:
        r = self.risk.get(pool)
        return bool(r and not r["hold"] and r["grade"] in ("pass", "caution") and T >= r["hide_until"] and T - r["at_ms"] < RISK_REFRESH_MS * 2)

    def _material_change(self, pool: str, card: Mapping) -> bool:
        prev = self.risk.get(pool, {}).get("basis")
        if not prev:
            return False
        f, rk = card["f"], card["risk"]
        t10, t10p = rk.get("top10_frac"), prev.get("top10_frac")
        q, qp = f.get("quote_sol"), prev.get("quote_sol")
        cn, cnp = f.get("creator_net_sol_life"), prev.get("creator_net_sol_life")
        return bool((t10 is not None and t10p is not None and abs(t10 - t10p) >= 0.05) or (q and qp and q <= qp * 0.8)
                    or (cn is not None and cnp is not None and cn - cnp <= -1.0))

    def _risk_round(self, cards: Mapping[tuple[str, str], dict], T: int) -> None:
        mat_only = self.guard is not None and any(self.guard.policy(g, self._now(T)).risk_material_change_only for g in LANE_G_PERSONAS)
        done: set[str] = set()
        for (pool, pr), card in sorted(cards.items()):
            if pool in done:
                continue
            done.add(pool)
            r = self.risk.get(pool)
            stale = r is None or T - r["at_ms"] >= RISK_REFRESH_MS
            mat = self._material_change(pool, card)
            if not (mat or (stale and not (mat_only and r is not None))):
                continue
            if not self._call_ok([arms.RISK_LEDGER], "risk", None, T):
                self._count("risk_call_cap")
                continue
            req = build_risk_request(self.playbooks[arms.RISK_LEDGER], [card], self._next_seq(arms.RISK_LEDGER, T))
            reply, recv = self._call(arms.RISK_LEDGER, req, T)
            v = validate_risk(reply, cards=[card])[0]
            basis = {"top10_frac": card["risk"].get("top10_frac"), "quote_sol": card["f"].get("quote_sol"), "creator_net_sol_life": card["f"].get("creator_net_sol_life")}
            if v.invalid or v.action == "HOLD_POOL":
                st = {"grade": "hold", "hold": True, "effective": 0.0, "at_ms": T, "hide_until": T, "flags": [], "basis": basis}
            else:
                st = {"grade": v.action, "hold": False, "effective": float(v.fields["effective_max_size"]), "at_ms": T, "flags": v.fields["flags"],
                      "hide_until": T + RISK_HIDE_MS if v.action == "reject" else T, "basis": basis}
            self.risk[pool] = st
            self.log.append(arms.RISK_LEDGER, recv, {"kind": "risk_grade", "pool": pool, "card_sha256": card_sha256(card), "reasons": list(v.reasons), "state": st})

    # -- entry gates
    def _gates(self, arm: str, pool: str, size: float, young: bool, T: int) -> dict[str, dict]:
        spec = arms.PERSONA_SPECS[arms.ARMS[arm].persona]
        lim, b = spec.code_limits, self.books[arm]
        day = utc_day(T)
        marked = 0.0
        for p in b.open.values():
            s = self.state.snapshot(p.pool, candles=False)
            m = mark_ret(p, s) if s else None
            marked += (m or 0.0) * p.size
        pnl = b.realized.get(day, 0.0) + marked
        last_h = [t for t in b.entries_ms if t > T - 60 * MIN_MS]
        g = {"size": {"value": size, "need": "> 0", "ok": size > 0},
             "open": {"value": len(b.open), "need": f"< {lim['max_open']}", "ok": len(b.open) < lim["max_open"]},
             "held": {"value": any(p.pool == pool for p in b.open.values()), "need": "False", "ok": not any(p.pool == pool for p in b.open.values())},
             "cooldown": {"value": T - b.pool_last_ms.get(pool, -10 ** 15), "need": f">= {POOL_COOLDOWN_MS}", "ok": T - b.pool_last_ms.get(pool, -10 ** 15) >= POOL_COOLDOWN_MS},
             "day_loss": {"value": round(pnl, 6), "need": f"> -{lim['day_loss_stop_sol']}", "ok": pnl > -lim["day_loss_stop_sol"]}}
        if "entries_per_h" in lim:
            g["rate"] = {"value": len(last_h), "need": f"< {lim['entries_per_h']}", "ok": len(last_h) < lim["entries_per_h"]}
        if young:
            n_young = sum(1 for p in b.open.values() if p.young)
            g["young_slots"] = {"value": n_young, "need": f"< {max(1, lim['max_open'] // 2)}", "ok": n_young < max(1, lim["max_open"] // 2)}
        return g

    def _plan_for(self, arm: str, persona: str, plan: Mapping | None) -> dict[str, Any]:
        spec = arms.PERSONA_SPECS[persona]
        st = self.arm_state.get(arm)  # an LLM arm's defaults are its knobs; the X arms (no LLM) keep the v0 default exit
        d = dict(spec.plan_defaults) if st is None else {k: st["knobs"][k] for k in spec.plan_keys if k in st["knobs"]}
        d.update(dict(plan or {}))
        if "time_min" in d:
            d["time_min"] = int(d["time_min"])
        return d

    def _open(self, arm: str, card: Mapping, size: float, plan: Mapping | None, dec_ms: int, gates: dict, extra: Mapping, role_ctx: Mapping | None = None) -> str | None:
        """Log the decision row, then send the order. Returns the position id, or None when the engine refused."""
        spec = arms.ARMS[arm]
        persona = spec.persona
        lim = arms.PERSONA_SPECS[persona].code_limits
        snap = self.state.snapshot(card["id"], candles=False)
        price = snap.get("price") if snap else None
        bot = bot_id(arm)
        pid = position_id(bot, card["id"], dec_ms, self.slot)
        pl = self._plan_for(arm, persona, plan) if persona != "BST" else {}
        young = card.get("f", {}).get("age_tier") != "established" if persona != "BST" else False
        pos = Pos(arm=arm, pid=pid, pool=card["id"], mint=card.get("mint") or (snap or {}).get("mint"), persona=persona, role=spec.role, size=size, entry_ms=dec_ms,
                  entry_price=float(price or 0.0), tokens_eq=(size / float(price)) if price else 0.0, stop_pct=pl.get("stop_pct"), target_pct=pl.get("target_pct"),
                  trail_pct=pl.get("trail_pct"), time_min=pl.get("time_min"), hard_stop_pct=lim.get("hard_stop_pct"),
                  max_hold_ms=int(lim.get("max_hold_min", 24 * 60) * MIN_MS), young=young, timer_ms=(extra or {}).get("timer_ms"),
                  partner=(role_ctx or {}).get("partner"), token=dict((extra or {}).get("token") or self.token_of.get(card["id"]) or {}))
        row = {"kind": "entry", "order_id": pid, "lane": "H" if persona == "BST" else "G", "pool": card["id"], "card_sha256": card_sha256(card), "card": card,
               "gates": gates, "size": size, "plan": pl, "pos": asdict(pos), "decision_slot": self.slot, **{k: v for k, v in (extra or {}).items() if k != "token"}}
        self.log.append(arm, dec_ms, row)
        try:
            got = self.engine.buy(bot, card["id"], size, dec_ms, self.slot, meta={"arm": arm, "card_sha256": card_sha256(card), "order_id": pid})
        except ArenaRefused as e:
            self.log.append(arm, dec_ms, {"kind": "order_refused", "order_id": pid, "side": "buy", "reason": str(e).split(":")[0], "detail": str(e)[:160]})
            self._count("order_refused")
            return None
        assert got == pid, "engine position id differs from the logged order id"
        b = self.books[arm]
        b.open[pid] = pos
        b.entries_ms.append(dec_ms)
        b.pool_last_ms[card["id"]] = dec_ms
        if spec.role == "L":
            self._match_r(persona, pos, dec_ms, card)
        return pid

    def _skip(self, arm: str, card: Mapping, dec_ms: int, reasons: list, gates: Mapping | None = None, lane: str = "G", fields: Mapping | None = None) -> None:
        self.log.append(arm, dec_ms, {"kind": "skip", "lane": lane, "pool": card["id"], "card_sha256": card_sha256(card), "reasons": list(reasons),
                                      "gates": dict(gates or {}), "fields": dict(fields or {})})

    # -- X arms (rule books) and R (random, matched)
    def _caps(self, card: Mapping, risk_cap: float) -> float:
        """final_size with ALL three caps. A cap that does not apply is arms.SIZES[-1]."""
        qe = card.get("f", {}).get("quote_eff_sol")
        impact_cap = C.max_stake_by_impact(qe)
        age_cap = C.YOUNG_STAKE_CAP if card.get("f", {}).get("age_tier") != "established" else arms.SIZES[-1]
        return final_size(0.25, risk_cap, impact_cap if impact_cap is not None else 0.0, age_cap)

    def _x_round(self, persona: str, tags: list[C.SetupTag], cards: Mapping, T: int) -> None:
        arm = f"{persona}-X"
        for t in tags:
            c = cards.get((t.pool_id, persona))
            if c is None:
                continue
            size = self._caps(c, arms.SIZES[-1])  # the X arms ignore the risk agent
            gates = self._gates(arm, t.pool_id, size, c["f"].get("age_tier") != "established", T)
            if not all(g["ok"] for g in gates.values()):
                self._skip(arm, c, T, [k for k, g in gates.items() if not g["ok"]], gates)
                continue
            self._open(arm, c, size, None, T, gates, {"setup": t.type, "z": t.z})

    def _match_r(self, persona: str, lpos: Pos, dec_ms: int, lcard: Mapping) -> None:
        rarm = f"{persona}-R"
        if rarm not in arms.ARMS:
            return
        pool = sorted(p for p, c in self.eligible.items() if p != lpos.pool and c.get("f", {}).get("max_stake", 0) >= lpos.size
                      and (c["f"].get("age_tier") == "established" or lpos.size <= C.YOUNG_STAKE_CAP)
                      and not any(q.pool == p for q in self.books[rarm].open.values()))
        if not pool:
            self._count("r_no_pool")
            self.log.append(rarm, dec_ms, {"kind": "skip", "lane": "G", "pool": None, "reasons": ["no_eligible_pool"], "partner": lpos.pid, "gates": {}, "fields": {}})
            return
        pick = pool[int(_u01(self.cfg.seed, rarm, lpos.pid) * len(pool))]
        self._open(rarm, self.eligible[pick], lpos.size, None, dec_ms, {}, {"r_pick_of": len(pool), "seed": self.cfg.seed}, {"partner": lpos.pid})

    # -- LLM entry rounds
    def _llm_entry_round(self, persona: str, cards: list[dict], T: int) -> None:
        members = arms.group_members(persona)
        if not cards:
            return
        maxc = arms.ENTRY_MAX_CARDS
        if self.guard is not None:
            pol = self.guard.policy(persona, self._now(T))
            if pol.entries_halted:
                for a in members:
                    self.log.append(a, T, {"kind": "halt", "level": pol.level, "reason": pol.halt_reason})
                return
            maxc = pol.max_cards_per_call
        live = self._live_members(members, T)
        if not live:
            return
        for i in range(0, len(cards), maxc):
            self._entry_call(persona, live, cards[i:i + maxc], T)

    def _live_members(self, members: tuple[str, ...], T: int) -> tuple[str, ...]:
        """The group members not stopped by a refused manifest; each stopped one logs why. (A refused manifest is a safety stop of that arm alone.)"""
        for a in members:
            if a in self.arm_halt:
                self.log.append(a, T, {"kind": "halt", "reason": "manifest_refused:" + self.arm_halt[a]})
        return tuple(a for a in members if a not in self.arm_halt)

    def _entry_call(self, persona: str, members: tuple[str, ...], chunk: list[dict], T: int) -> None:
        if not self._call_ok(list(members), "entry", None, T):  # symmetric: one member at its cap stops the group
            self._count("entry_call_cap")
            return
        replies: dict[str, tuple[ModelReply, int]] = {}
        for c in chunk:
            self.cards.put(c, T)  # shown to every member of the group
        for a in members:
            req = build_entry_request(a, self.playbook_for(a), chunk, self._next_seq(a, T), max_cards=len(chunk))
            replies[a] = self._call(a, req, T)
        if any(r.error in DENIED for r, _ in replies.values()):  # symmetric halt: no member acts on this instant
            for a in members:
                self.log.append(a, replies[a][1], {"kind": "symmetry_abstain", "errors": {m: replies[m][0].error for m in members}})
            return
        for a in members:
            reply, recv = replies[a]
            for v, card in zip(validate_entry(reply, persona=persona, cards=chunk, knobs=self.knobs[a], received_ms=recv), chunk):
                self.invalid[a][0] += int(v.invalid)
                self.invalid[a][1] += 1
                self._on_entry_verdict(a, v, card, recv, T)

    def _on_entry_verdict(self, arm: str, v: Any, card: dict, recv: int, T: int) -> None:
        if v.action == "WATCH":
            b = self.books[arm]
            if len(b.watches) >= arms.WATCH_MAX_PER_ARM:
                self._skip(arm, card, recv, ["watch_cap"])
                return
            w = {"id": sha256_hex(f"{arm}|{card['id']}|{recv}")[:16], "pool": card["id"], "terms": v.fields["watch"]["terms"], "when": v.fields["watch"]["when"],
                 "expires_ms": T + v.fields["watch"]["ttl_min"] * MIN_MS, "card_sha256": card_sha256(card)}
            b.watches.append(w)
            self.log.append(arm, recv, {"kind": "watch_add", "watch": w})
            return
        if v.action != "BUY":
            self._skip(arm, card, recv, list(v.reasons))
            return
        rk = self.risk.get(card["id"])
        risk_cap = rk["effective"] if rk else 0.0
        size = self._caps_trader(card, v.fields["size"], risk_cap)
        gates = self._gates(arm, card["id"], size, card["f"].get("age_tier") != "established", recv)
        gates["risk_cleared"] = {"value": rk["grade"] if rk else None, "need": "pass|caution, not hidden", "ok": self._cleared(card["id"], recv)}
        self._veto_gate(arm, card, gates)
        if not all(g["ok"] for g in gates.values()):
            self._skip(arm, card, recv, [k for k, g in gates.items() if not g["ok"]], gates)
            return
        self._open(arm, card, size, v.fields.get("plan"), recv, gates, {"conviction": v.fields["conviction"], "why": v.fields["why"], "size_asked": v.fields["size"]})

    def _veto_gate(self, arm: str, card: Mapping, gates: dict) -> None:
        """The arm's local vetoes (reviewer-added, tighten-only). The gate is present only for an arm that has any, so a v0 arm's rows are unchanged."""
        vt = self.arm_state[arm]["vetoes"]
        if vt:
            hit = local_veto(vt, card)
            gates["local_veto"] = {"value": hit, "need": "no local veto holds", "ok": hit is None}

    def _caps_trader(self, card: Mapping, asked: float, risk_cap: float) -> float:
        qe = card.get("f", {}).get("quote_eff_sol")
        impact_cap = C.max_stake_by_impact(qe)
        age_cap = C.YOUNG_STAKE_CAP if card.get("f", {}).get("age_tier") != "established" else arms.SIZES[-1]
        return final_size(asked, risk_cap, impact_cap if impact_cap is not None else 0.0, age_cap)

    # -- watches (minute ticks)
    def watches(self, T: int) -> None:
        for arm in arms.llm_arms():
            b = self.books[arm]
            if arms.ARMS[arm].persona == "BST" or arm in self.arm_halt:
                continue
            for w in list(b.watches):
                if T >= w["expires_ms"]:
                    b.watches.remove(w)
                    self.log.append(arm, T, {"kind": "watch_end", "id": w["id"], "why": "expired"})
                    continue
                card = self._card(w["pool"], T, None)
                if card is None:
                    continue
                nums = card_numeric(card)
                if all(f in nums and {">": nums[f] > n, ">=": nums[f] >= n, "<": nums[f] < n, "<=": nums[f] <= n}[op] for f, op, n in w["terms"]):
                    b.watches.remove(w)
                    self.log.append(arm, T, {"kind": "watch_end", "id": w["id"], "why": "fired", "card_sha256": card_sha256(card)})
                    if self._cleared(card["id"], T) and self._call_ok([arm], "entry", None, T):
                        persona = arms.ARMS[arm].persona
                        self.cards.put(card, T)
                        req = build_entry_request(arm, self.playbook_for(arm), [card], self._next_seq(arm, T), max_cards=1)
                        reply, recv = self._call(arm, req, T)
                        if reply.error not in DENIED:
                            v = validate_entry(reply, persona=persona, cards=[card], knobs=self.knobs[arm], received_ms=recv)[0]
                            if v.action == "BUY":
                                self._on_entry_verdict(arm, v, card, recv, T)

    # -- exits
    def exits(self, T: int) -> None:
        for arm in MARKS_ARM_ORDER:
            b = self.books[arm]
            for pid in sorted(b.open):
                pos = b.open.get(pid)
                if pos is None:
                    continue
                if pos.role == "R" and pos.persona != "BST" and pos.partner in self._all_open():
                    continue
                snap = self.state.snapshot(pos.pool, candles=False)
                ret = mark_ret(pos, snap) if snap else None
                if ret is not None:
                    pos.peak = max(pos.peak, 1.0 + ret)
                if T - pos.entry_ms < ENTRY_SETTLE_MS:
                    continue
                why = exit_reason(pos, ret, T)
                if why:
                    self._sell(pos, why, T, ret)

    def _all_open(self) -> set[str]:
        return {pid for b in self.books.values() for pid in b.open}

    def _sell(self, pos: Pos, why: str, T: int, ret: float | None, dec_ms: int | None = None, follow: bool = False) -> bool:
        b = self.books[pos.arm]
        dec = T if dec_ms is None else dec_ms
        snap = self.state.snapshot(pos.pool)
        flag: dict[str, Any] = {}
        try:
            prints = list(self.state.pool(pos.pool).events)
            C.assert_as_of(snap, T, prints=prints, as_of_slot=self.slot)  # the as-of check at exit
            flag = C.sell_route(snap, prints, pos.token, as_of_ms=T, stake=pos.size)
        except C.LeakageError as e:
            self._leak(T, pos.pool, e)
            flag = {"ok": False, "reason": "as_of_refused"}
        except Exception as e:  # noqa: BLE001
            flag = {"ok": False, "reason": type(e).__name__}
        est = (ret or 0.0) * pos.size
        self.log.append(pos.arm, dec, {"kind": "sell", "pid": pos.pid, "pool": pos.pool, "reason": why, "est_ret": ret, "est_pnl_sol": est, "decision_slot": self.slot,
                                       "sell_route_flag": (None if flag.get("ok") else flag), "follow": follow})
        try:
            self.engine.sell(bot_id(pos.arm), pos.pid, 1.0, dec, self.slot, reason=why)
        except ArenaRefused as e:
            pos.sell_tries += 1
            self._count("sell_refused")
            self.log.append(pos.arm, dec, {"kind": "order_refused", "order_id": pos.pid, "side": "sell", "reason": str(e).split(":")[0], "detail": str(e)[:160]})
            if "no open position" in str(e) or pos.sell_tries >= SELL_RETRY_MAX:
                b.open.pop(pos.pid, None)  # an orphan the engine does not know: closed in the book, counted
                self._count("sell_orphan")
            return False
        b.open.pop(pos.pid, None)
        day = utc_day(dec)
        b.realized[day] = b.realized.get(day, 0.0) + est
        for rb in self.books.values():  # R follows its L: closed at the same instant
            for rp in [p for p in rb.open.values() if p.partner == pos.pid]:
                self._sell(rp, "follow_L", T, mark_ret(rp, self.state.snapshot(rp.pool, candles=False) or {}), dec_ms=dec, follow=True)
        return True

    # -- LLM re-checks (minute ticks)
    def rechecks(self, T: int) -> None:
        for arm in arms.llm_arms():
            spec = arms.PERSONA_SPECS[arms.ARMS[arm].persona]
            b = self.books[arm]
            if spec.recheck_minutes is None or not b.open or arm in self.arm_halt:
                continue
            mult = 1
            if self.guard is not None:
                pol = self.guard.policy(arms.ARMS[arm].group, self._now(T))
                if pol.llm_rechecks_halted:
                    continue
                mult = pol.recheck_cadence_multiplier
            due = []
            for pos in sorted(b.open.values(), key=lambda p: p.pid):
                snap = self.state.snapshot(pos.pool, candles=False)
                ret = mark_ret(pos, snap) if snap else None
                if ret is None or T - pos.entry_ms < ENTRY_SETTLE_MS:
                    continue
                pct = ret * 100.0
                half_stop, half_tgt = (pos.stop_pct or -1e9) / 2.0, (pos.target_pct or 1e9) / 2.0
                event = pct <= half_stop or pct >= half_tgt
                if event or T - max(pos.last_recheck_ms, pos.entry_ms) >= spec.recheck_minutes * MIN_MS * mult:
                    due.append((pos, pct))
            for i in range(0, len(due), arms.ENTRY_MAX_CARDS):
                self._recheck_call(arm, due[i:i + arms.ENTRY_MAX_CARDS], T)

    def _recheck_call(self, arm: str, group: list[tuple[Pos, float]], T: int) -> None:
        persona = arms.ARMS[arm].persona
        if not self._call_ok([arm], "recheck", persona, T):
            self._count("recheck_call_cap")
            return
        cards, poss = [], []
        for pos, pct in group:
            card = self._card(pos.pool, T, None)
            if card is None:
                continue
            cards.append(card)
            poss.append({"id": pos.pool, "stop_pct": pos.stop_pct, "target_pct": pos.target_pct, "mark_ret_pct": round(pct, 4), "as_of_ms": T})
        if not cards:
            return
        for c in cards:
            self.cards.put(c, T)
        req = build_recheck_request(arm, self.playbook_for(arm), poss, cards, self._next_seq(arm, T))
        reply, recv = self._call(arm, req, T)
        if reply.error in DENIED:
            self.log.append(arm, recv, {"kind": "recheck_denied", "error": reply.error})  # the budget gate said no: the position keeps its code exits
            return
        by_pool = {p.pool: p for p, _ in group}
        for v in validate_recheck(reply, positions=poss, received_ms=recv):
            pos = by_pool[v.id]
            pos.last_recheck_ms = T
            self.invalid[arm][0] += int(v.invalid)
            self.invalid[arm][1] += 1
            if v.action == "EXIT":
                self._sell(pos, "llm_exit", T, mark_ret(pos, self.state.snapshot(pos.pool, candles=False) or {}), dec_ms=recv)
            elif v.action == "TIGHTEN" and pos.pid in self.books[arm].open:
                pos.stop_pct = v.fields.get("new_stop_pct", pos.stop_pct)
                pos.target_pct = v.fields.get("new_target_pct", pos.target_pct)
                self.log.append(arm, recv, {"kind": "tighten", "pid": pos.pid, "stop_pct": pos.stop_pct, "target_pct": pos.target_pct, "why": v.fields.get("why")})

    # -- lane H (BST)
    def triggers(self, T: int) -> None:
        q, self.trig_q = self.trig_q, []
        for f in q:
            if f["t_recv_ms"] >= T:
                self.trig_q.append(f)  # not yet before the card instant
                continue
            gate = self.chain.mint_gate(f.get("mint"), T, self.slot)
            card = bst_card(f, as_of_ms=T, as_of_slot=self.slot)
            reasons = ["chain_unavailable"] if gate is None else list(gate["reasons"])
            ok = gate is not None and gate["ok"]
            token = (gate or {}).get("token") or {}
            cap = C.max_stake_by_impact(f.get("q_trigger_sol"))
            size = final_size(0.25, arms.SIZES[-1], cap if cap is not None else 0.0, arms.SIZES[-1])
            extra = {"timer_ms": int(f["s0_t_recv_ms"]) + BST_EXIT_MS, "token": token, "trigger_sha256": sha256_hex(canon(f))}
            for arm in ("BST-L", "BST-F"):
                if not ok:
                    self._skip(arm, card, T, reasons, lane="H")
                    self.books[arm].bst_seen += 1
            if not ok:
                self._skip("BST-X", card, T, reasons, lane="H")
                self._skip("BST-R", card, T, reasons, lane="H")
                continue
            lt = self._bst_llm(card, size, extra, T)
            if lt is not None:
                self._bst_x(card, size, extra, T)
                self._bst_r(card, size, extra, T)

    def _bst_llm(self, card: dict, size: float, extra: Mapping, T: int) -> bool | None:
        members = self._live_members(arms.group_members("BST"), T)
        if not members:
            return True
        if self.guard is not None and self.guard.policy("BST", self._now(T)).entries_halted:
            for a in members:
                self.log.append(a, T, {"kind": "halt", "reason": "group_halted"})
            return True
        if not self._call_ok(list(members), "entry", None, T):
            return True
        self.cards.put(card, T)
        replies = {a: self._call(a, build_entry_request(a, self.playbook_for(a), [card], self._next_seq(a, T), max_cards=1), T) for a in members}
        if any(r.error in DENIED for r, _ in replies.values()):
            for a in members:
                self.log.append(a, replies[a][1], {"kind": "symmetry_abstain", "errors": {m: replies[m][0].error for m in members}})
            return True
        for a in members:
            reply, recv = replies[a]
            v = validate_entry(reply, persona="BST", cards=[card], knobs=self.knobs[a], received_ms=recv)[0]
            self.invalid[a][0] += int(v.invalid)
            self.invalid[a][1] += 1
            b = self.books[a]
            b.bst_seen += 1
            if v.action != "BUY":
                self._skip(a, card, recv, list(v.reasons), lane="H")
                continue
            gates = self._gates(a, card["id"], size, False, recv)
            self._veto_gate(a, card, gates)
            if not all(g["ok"] for g in gates.values()):
                self._skip(a, card, recv, [k for k, g in gates.items() if not g["ok"]], gates, lane="H")
                continue
            if self._open(a, card, size, None, recv, gates, {**extra, "conviction": v.fields["conviction"], "why": v.fields["why"]}) is not None:
                b.bst_taken += 1
        return True

    def _bst_x(self, card: dict, size: float, extra: Mapping, T: int) -> None:
        gates = self._gates("BST-X", card["id"], size, False, T)
        if all(g["ok"] for g in gates.values()):
            self._open("BST-X", card, size, None, T, gates, extra)
        else:
            self._skip("BST-X", card, T, [k for k, g in gates.items() if not g["ok"]], gates, lane="H")

    def _bst_r(self, card: dict, size: float, extra: Mapping, T: int) -> None:
        lb = self.books["BST-L"]
        rate = (lb.bst_taken / lb.bst_seen) if lb.bst_seen else 0.0
        coin = _u01(self.cfg.seed, "BST-R", card["id"])
        if coin < rate:
            self._open("BST-R", card, size, None, T, {}, {**extra, "r_rate": rate, "r_coin": coin})
        else:
            self._skip("BST-R", card, T, ["coin"], lane="H", fields={"rate": rate, "coin": coin})

    # -- reviewer versions (once a minute)
    def refresh_versions(self, T: int) -> None:
        """Ask the version source for the version in force for each learning arm (L and CON-P). F arms have no reviewer and are never asked. A new
        manifest line is applied and LOGGED before any decision that uses it; a refused manifest (bad hash, broken chain, input after the cutoff, ...)
        halts that arm's entries and re-checks until a good chain is back. Nothing is applied half-way."""
        self._versions_loaded = True
        if self.version_source is None:
            return
        now = self._now(T)
        for arm in arms.reviewer_arms():
            try:
                av = self.version_source(arm, now)
                new = None if av is None else self._check_version(arm, av)
            except Exception as e:  # noqa: BLE001 - any failure of the source is a refusal (fail closed)
                code = (str(e) or type(e).__name__)[:80]
                self._count("version_refused")
                if self.arm_halt.get(arm) != code:
                    self.arm_halt[arm] = code
                    self.log.append(arm, now, {"kind": "version_refused", "reason": code})
                continue
            if arm in self.arm_halt:
                del self.arm_halt[arm]
                self.log.append(arm, now, {"kind": "version_recovered"})
            if new is not None and new["manifest_sha256"] != self.arm_state[arm]["manifest_sha256"]:
                self.arm_state[arm] = new
                self.knobs[arm] = Knobs(arms.ARMS[arm].persona, int(new["knobs"]["min_conviction"]))
                self.log.append(arm, now, {"kind": "version", "version": new["version"], "manifest_sha256": new["manifest_sha256"], "playbook_sha256": new["playbook"].sha256,
                                           "knobs": dict(new["knobs"]), "vetoes": list(new["vetoes"]), "effective_from_ms": new["effective_from_ms"]})

    def _check_version(self, arm: str, av: Any) -> dict:
        persona = arms.ARMS[arm].persona
        st, man = av.state, av.manifest
        if av.arm != arm or st.persona != persona or st.playbook.name != persona:
            raise ValueError("version_not_for_this_arm")
        knobs = dict(st.knobs)
        Knobs(persona, int(knobs["min_conviction"]))  # raises ValueError outside the persona's bounds
        spec = arms.PERSONA_SPECS[persona]
        if set(knobs) != {"min_conviction", *spec.plan_keys}:
            raise ValueError("knobs_keys")
        for k in spec.plan_keys:
            lo, hi = spec.plan_bounds[k]
            if isinstance(knobs[k], bool) or not isinstance(knobs[k], (int, float)) or not lo <= knobs[k] <= hi:
                raise ValueError("knob_out_of_bounds")
        sha = man.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64:
            raise ValueError("manifest_sha256")
        return {"version": int(st.version), "playbook": st.playbook, "knobs": knobs, "vetoes": tuple(str(v) for v in st.vetoes), "manifest_sha256": sha,
                "effective_from_ms": man.get("effective_from_ms")}

    # -- the loop
    def step(self, T: int) -> None:
        """One tick at instant T (the state is stepped to T-1 first)."""
        self.sim_now = max(self.sim_now, T)
        self.poll_to(T)
        sec = T % MIN_MS
        if sec == SCAN_SECOND_MS or not self._versions_loaded:
            self.refresh_versions(T)  # before any decision of this tick
        if sec == SCAN_SECOND_MS:
            self.quotes.sample(self.state, T)
        self.exits(T)
        self.triggers(T)
        if sec == SCAN_SECOND_MS:
            self.watches(T)
            self.rechecks(T)
            if T % SCAN_EVERY_MS == SCAN_SECOND_MS:
                self.scan(T)

    def run(self, t_from: int, t_to: int, sleep: Callable[[float], None] = time.sleep) -> None:
        """Ticks T = t_from, t_from + 1 s, ... while T < t_to. Live: waits for the wall clock to pass each T."""
        T = t_from
        while T < t_to:
            if self.cfg.live:
                lag = T + 100 - self.now_fn()
                if lag > 0:
                    sleep(lag / 1000.0)
            self.step(T)
            T += TICK_MS


def stack_config(feed_delay_slots: int, **kw: Any) -> Any:
    """arena_paper.StackConfig with the feed delay d passed explicitly (REQUIRED: StackConfig refuses to price without it). The manager pins d from
    tools/arena_feed_delay.py; there is no default here."""
    from tools.arena_paper import StackConfig

    if isinstance(feed_delay_slots, bool) or not isinstance(feed_delay_slots, int) or feed_delay_slots < 0:
        raise ValueError("feed_delay_slots must be a whole number of slots >= 0")
    return StackConfig(feed_delay_slots=feed_delay_slots, **kw)


def head_binding(repo: str | os.PathLike | None = None) -> dict[str, Any]:
    """The running code's identity: {"git_sha": HEAD, "dirty": bool} (dirty = any tracked change or untracked file). A follow-up binds this to open rows."""
    import subprocess

    r = str(repo or Path(__file__).resolve().parent.parent)

    def run_git(*a: str) -> str:
        p = subprocess.run(["git", "-C", r, *a], capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise RuntimeError("git failed")  # no output is echoed: a remote url can carry a credential
        return p.stdout

    return {"git_sha": run_git("rev-parse", "HEAD").strip(), "dirty": bool(run_git("status", "--porcelain").strip())}


def require_frozen_checkout(binding: Mapping[str, Any], frozen_sha: str) -> None:
    """A live book runs the code it registered: a clean checkout at its frozen sha, or it does not start."""
    if binding.get("dirty") is not False:
        raise RuntimeError("the checkout is dirty (or unknown): the runner runs from a clean checkout at its frozen sha")
    sha = str(binding.get("git_sha") or "")
    if len(frozen_sha) < 7 or not (sha.startswith(frozen_sha) or frozen_sha.startswith(sha)) or not sha:
        raise RuntimeError("the checkout is not at the frozen sha")


def first_tick_at_or_after(ms: int) -> int:
    """The first scan instant (minute boundary + :02, 5-min aligned) at or after ms."""
    base = (ms - SCAN_SECOND_MS) // SCAN_EVERY_MS * SCAN_EVERY_MS + SCAN_SECOND_MS
    return base if base >= ms else base + SCAN_EVERY_MS


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV4 runner: paper only. The API key is read by tools.haiku_caller from the environment; it is never printed.")
    ap.add_argument("--tape-dir", default="/data/mal/arena/tape")
    ap.add_argument("--root", default=HV4_ROOT)
    ap.add_argument("--ledger-root", default="/data/mal/arena/ledger")
    ap.add_argument("--registry", default="/data/mal/arena/bots.json")
    ap.add_argument("--frozen-sha", required=True)
    ap.add_argument("--clock-start-ms", type=int, required=True)
    ap.add_argument("--register", action="store_true", help="append the 13 bots.json lines (before the clock) and exit")
    ap.add_argument("--stub", action="store_true", help="StubModel: no API call, every answer abstains (count-only shakedown)")
    ap.add_argument("--feed-delay-slots", type=int, required=True, help="the feed delay the stack charges (StackConfig.feed_delay_slots); the manager pins it")
    ap.add_argument("--end-ms", type=int, default=None, help="stop at this instant (default: run until stopped)")
    a = ap.parse_args(argv)
    from tools.hv4_model import CallerModel, StubModel

    guard = BudgetGuard(a.root)
    model: Model = StubModel(guard=guard) if a.stub else CallerModel(guard)
    state = ArenaState(a.tape_dir)
    engine = ArenaEngine(a.ledger_root, a.registry, cfg=stack_config(feed_delay_slots=a.feed_delay_slots))
    cfg = RunnerConfig(root=a.root, frozen_sha=a.frozen_sha, clock_start_ms=a.clock_start_ms, live=True)
    run = Runner(cfg, state, engine, model, HV4ChainSource(), guard, h5=None, binding=head_binding(), version_source=review_version_source(a.root, a.frozen_sha))
    if a.register:
        for k, v in run.register_all(a.registry).items():
            print(canon({"bot_id": bot_id(k), **{x: v[x] for x in ("frozen_sha", "clock_start_ms", "deciding_stake", "model_ids")}}))
        return 0
    n = run.resume()
    print(canon({"resumed": n}))
    start = max(a.clock_start_ms, int(time.time() * 1000))
    run.run(start - start % TICK_MS, a.end_ms or 2 ** 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
