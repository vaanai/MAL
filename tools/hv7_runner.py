#!/usr/bin/env python3
"""hv7_runner: the hourly loop of HV7, a forced-rank Haiku 5.5 book (chunk C2 of docs/research/hv7/DESIGN.md sections 2-5, 8, 9).

PAPER only. No wallet or trading key. Four arms on one paper stream: hv7.0-L (Haiku rank 1), -R (seeded random pick of the screen order), -S (screen
leader), -U (all N candidates, one 0.25 SOL position each, one bot holding up to 8 positions). Every arm buys at the fixed instant t_e = H:02:45.000Z and
exits by HV6's X rule (stop -8%, target +20%, 60 min), priced by tools.arena_paper.ArenaEngine from its own 1.9 s leg from that instant.

This is NOT a subclass of hv6_runner.Runner (no watch/re-ask, regime channel, probes, five arms). Reused as is: hv6_cards (scan, build_card), hv6_phantom,
hv7_rank (pure core), hv4_prompts/hv4_model/hv6_agents model classes, ArenaState, ArenaEngine, and from hv6_runner: QuoteHistory, mark_ret, exit_reason,
Pos, position_id, stack_config, ChainSource helpers. No hv4_ or hv6_ file is edited.

DEVIATIONS from DESIGN section 8, found by reading the code (disclosed for the PR):
  * hv6_runner.GuardedDecisionLog / CardSink and hv6_cards.check are bound to HV6's module-level roots (WRITE_ROOTS = HV6_ROOT only), so HV7 has its own
    path check (`check_path`, same rule: allowlist first, then H.DENY_TOKENS) and its own DecisionLog subclass. H.check is not called.
  * hv6_agents.BudgetGuard's ledger table is HV6's (ledger_name / day_cap_usd are module-level), so HV7 has a thin `Hv7Guard` with the same duck-typed
    surface (preflight / record_refusal, reserve-before-call through haiku_caller's own ledger) and one ledger, RANK-L, capped at HV7_DAY_CAP_USD.
  * The U arm is ONE bot id (hv7.0-U) holding up to 8 positions: the engine keys positions by id (bot, pool, stamp, slot), not by bot. Pricing
    independence across L, R, S and U is a hard test (test_hv7_runner.py), not an assumption.
  * Shared clock (new, not in DESIGN): one engine call clock is frozen for each batch of orders (all arms' entries at t_e; all sells of one tick), so arms
    that call a few ms apart cannot land a slot apart.
  * The L model call never blocks the loop. In live mode it runs on its own daemon thread from t_d; at t_e the loop uses the reply only if it arrived by
    t_e, otherwise the period is a FALLBACK (R's pick, counted) and every arm still enters at t_e, through the engine's own 1.9 s leg from t_e, never from
    L's reply time. Exits keep running on every tick during the call. (QP #998.)
C3a (this file): resume after a crash (`Hv7Runner.resume`), torn-line repair, in-flight shutdown, owner-ref validation, counter lock, ledger
reconciliation of rank-row cost. hv7_count.py, hv7_register.py and scripts/ops/hv7-e1-drill.sh are separate files. `main` is wired but has never been run.

RESUME RULES (stated, tested in test_hv7_resume.py):
  1. A torn last line (no trailing newline) is truncated to the last full line and a `gap` row (reason torn_last_line, dropped byte count and sha256) is
     appended to the SCAN log. A broken hash chain is NOT repairable: LogChainError, the runner refuses to start.
  2. Open positions are rebuilt from the entry rows plus the engine's own recover() (the engine holds the position; the log holds the Pos). An entry row with
     no engine position, no sell row and no order_refused row (crash between the log write and the engine write) is NOT re-sent: a `gap` row
     (reason entry_not_in_engine). No late entry, ever: every entry is at t_e.
  3. Pending period, resumed at or before t_e: re-screened at t_d from the replayed tape (no new SCAN row; the screen ids and card shas must equal the logged
     ones, else the period is excluded, reason resume_rebuild_mismatch) and the L call is re-asked through the replay cache (same request_sha256, no double spend).
  4. Pending period, resumed after t_e, or an L call still in flight at shutdown: the period is EXCLUDED for all four arms (SCAN `period_excluded` row and an L
     `rank` row with fallback true, excluded true and the reason). It is not scored as a fallback entry. hv7_count reports it as `excluded`.
  5. A period whose rank row exists but whose entries are incomplete (kill during enter): a `gap` row per missing (arm, pool) entry; never a late entry.
  6. The resume event itself goes to <root>/resume.jsonl, NOT to the decision log: a resume with no torn line, no unexecuted entry and no in-flight loss leaves
     the decision-log md5 equal to an uninterrupted run. Gap and exclusion rows are in the decision log, so the md5 differs by rule when one is written.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import signal
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import hv4_cards as C
from tools import hv6_agents as A
from tools import hv6_cards as H
from tools import hv7_rank as R
from tools.arena_declog import GENESIS, DecisionLog, LogChainError, sha256_hex, utc_day  # noqa: F401
from tools.arena_paper import ArenaRefused
from tools.arena_state import WSOL_MINT, ArenaState
from tools.haiku_caller import Ledger, LedgerError
from tools.hv6_phantom import PhantomFilter
from tools.hv6_runner import Pos, QuoteHistory, exit_reason, mark_ret, position_id, stack_config  # noqa: F401

MIN_MS = 60_000
HOUR_MS = 3_600_000
TICK_MS = 1_000
T_D_OFFSET_MS = 2 * MIN_MS                 # t_d = H:02:00.000Z
T_E_OFFSET_MS = T_D_OFFSET_MS + 45_000     # t_e = H:02:45.000Z, fixed for every arm
ENTRY_SETTLE_MS = 3_000
SELL_RETRY_MS = 1_000
BOT_PREFIX = "hv7.0-"
ARM_ORDER = ("L", "R", "S", "U")
STAKE_SOL = 0.25
HV7_ROOT = "/data/mal/arena/haiku-v7"
HV7_DAY_CAP_USD = 0.30                     # pinned: DESIGN section 5 cap table; the K1 sum test lives in test_hv7_runner.py
K1_DAY_CAP_USD = 3.00
RANK_LEDGER = "RANK-L"
EXIT = dict(A.X_EXIT)                      # {stop_pct -8, target_pct +20, trail_pct None, time_min 60}: HV6's X rule, one rule for every arm
MAX_OPEN_U = R.N_MAX

# Card allowlist (DESIGN section 9 C2, QP C1 note): a card reaches a request only from hv6_cards.build_card, and its keys must be inside these sets.
# Pinned from a built card; the forbidden-key guard in hv7_rank is a backstop, this is the boundary.
CARD_TOP_KEYS = frozenset({"v", "id", "lane", "as_of_slot", "as_of_ms", "setup", "f", "risk", "tops", "c1", "c5", "untrusted", "x", "mint"})
CARD_F_KEYS = frozenset(C.FEATURE_FIELDS) | frozenset({"age_known", "age_tier", "max_stake"})
CARD_RISK_KEYS = frozenset(C.RISK_NUMERIC_FIELDS) | frozenset({"as_of_ms", "as_of_slot", "extensions", "freeze_authority_null", "holder_count_cover",
                                                              "holders_as_of_ms", "mayhem", "mint_authority_null", "sell_route_ok", "token_program"})
CARD_NESTED = {"f": CARD_F_KEYS, "risk": CARD_RISK_KEYS, "tops": frozenset({"buyer", "seller"}), "untrusted": frozenset({"name", "symbol"})}

READ_ROOTS = (H.TAPE_ROOT, HV7_ROOT)
WRITE_ROOTS = (HV7_ROOT,)


# Owner cost-line reference: a MiScusi notebook id (n_...) or a MiScusi question id (q_...). ASSUMED shape (prefix, then 8-32 base62 characters);
# the pattern is pinned here so a typo or a free-text string cannot unlock the API path.
OWNER_REF_RE = re.compile(r"(?:n|q)_[A-Za-z0-9]{8,32}")


class Hv7Error(RuntimeError):
    pass


def validate_owner_ref(ref: Any) -> str:
    if not isinstance(ref, str) or OWNER_REF_RE.fullmatch(ref) is None:
        raise Hv7Error("owner cost-line ref must be a MiScusi notebook id (n_...) or question id (q_...)")
    return ref


class CardAllowlistError(Hv7Error):
    pass


class PricingIndependenceError(Hv7Error):
    pass


def bot_id(arm: str) -> str:
    if arm not in ARM_ORDER:
        raise Hv7Error(f"unknown arm {arm!r}")
    return f"{BOT_PREFIX}{arm}"


def check_path(path: str | os.PathLike, mode: str = "r") -> str:
    """HV7's own allowlist (H.check is bound to HV6's roots): the path must be under HV7_ROOT (write) or tape / HV7_ROOT (read), and carry no deny token."""
    if mode not in ("r", "w"):
        raise H.PathRefused("bad_mode")
    p = os.fspath(path)
    if not p or "\x00" in p or not os.path.isabs(p):
        raise H.PathRefused("path_not_absolute")
    p = os.path.normpath(p)
    roots = WRITE_ROOTS if mode == "w" else READ_ROOTS
    for cand in (p, os.path.realpath(p)):
        if not any(H._under(cand, r) for r in roots):
            raise H.PathRefused("not_allowlisted")
        if any(tok in cand.lower() for tok in H.DENY_TOKENS):
            raise H.PathRefused("denied_token")
    return p


def check_card_keys(card: Mapping[str, Any]) -> None:
    """Fail loudly if a card has a key outside the pinned allowlist (top level or nested)."""
    bad = sorted(set(card) - CARD_TOP_KEYS)
    for k, allowed in CARD_NESTED.items():
        sub = card.get(k)
        if isinstance(sub, Mapping):
            bad += [f"{k}.{x}" for x in sorted(set(sub) - allowed)]
    if bad:
        raise CardAllowlistError(f"card keys outside the allowlist: {bad}")


class Hv7DecisionLog(DecisionLog):
    """arena_declog.DecisionLog with every path checked by check_path."""

    def __init__(self, root: str | os.PathLike) -> None:
        super().__init__(check_path(Path(root), "w"))

    def files(self, name: str) -> list[Path]:
        check_path(self.dir / name, "r")
        return super().files(name)

    def append(self, name: str, ms: int, body: Mapping[str, Any]) -> dict:
        check_path(self.dir / name / f"{utc_day(ms)}.jsonl", "w")
        return super().append(name, ms, body)


# ------------------------------------------------------------------------------------------------------------------------ budget
class Hv7Guard:
    """HV7's own reserve-before-call gate: one ledger (RANK-L), capped at HV7_DAY_CAP_USD, no group. Same duck-typed surface as hv6_agents.BudgetGuard
    (preflight, record_refusal), so hv4_model.CallerModel / StubModel use it unchanged. A denied preflight is a FALLBACK in the runner, never a trade skip."""

    def __init__(self, root: str | os.PathLike, *, day_cap_usd: float = HV7_DAY_CAP_USD, min_call_usd: float = A.MIN_CALL_USD) -> None:
        for name, v in (("day_cap_usd", day_cap_usd), ("min_call_usd", min_call_usd)):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                raise ValueError(f"{name} must be a finite number >= 0")       # NaN would pass every `>` test below
        if day_cap_usd > HV7_DAY_CAP_USD + 1e-12:
            raise ValueError("day_cap_usd may only be lowered")
        low = str(root).lower()
        bad = next((t for t in A.DENY_ROOT_TOKENS if t in low), None)
        if bad is not None:
            raise ValueError(f"guard root contains the deny token {bad!r}")
        self.root, self.day_cap_usd, self.min_call_usd = Path(root), float(day_cap_usd), float(min_call_usd)

    def preflight(self, arm: str, kind: str, now_ms: int) -> A.Preflight:
        if (arm, kind) != ("L", R.KIND):
            raise ValueError(f"HV7 has one model call: L / {R.KIND}")
        day = utc_day(now_ms)
        path = self.root / "ledgers" / RANK_LEDGER / f"{day}.jsonl"
        cache = self.root / "cache" / RANK_LEDGER

        def pf(ok: bool, reason: str | None, budget: float) -> A.Preflight:
            return A.Preflight(ok, reason, arm, kind, RANK_LEDGER, day, path, cache, budget, self.day_cap_usd, False, False, None)

        try:
            led = Ledger(path)
            with led.locked(exclusive=False):
                spent = led.spent()
        except (LedgerError, OSError):
            return pf(False, "ledger_unreadable", 0.0)      # fail closed
        if self.day_cap_usd - spent < self.min_call_usd:
            return pf(False, "day_cap", 0.0)
        return pf(True, None, self.day_cap_usd)

    def record_refusal(self, pf: A.Preflight, now_ms: int) -> None:
        return None

    def request_cost(self, day: str, request_sha256: str) -> float:
        """Net cost the budget ledger holds for one request: its reserve rows plus the settle rows of those reservations (0.0 if none: a replayed answer
        reserves nothing). Reconciles a rank row's cost_usd against the ledger."""
        led = Ledger(self.root / "ledgers" / RANK_LEDGER / f"{day}.jsonl")
        with led.locked(exclusive=False):
            rows = led.rows()
        ids = {r["reservation_id"] for r in rows if r.get("kind") == "reserve" and r.get("request_sha256") == request_sha256}
        return math.fsum(r["cost_usd"] for r in rows if r.get("reservation_id") in ids)

    def day_spent(self, day: str) -> float:
        led = Ledger(self.root / "ledgers" / RANK_LEDGER / f"{day}.jsonl")
        with led.locked(exclusive=False):
            return led.spent()


def cap_sum_usd() -> float:
    """HV6's registered caps (config only: no ledger, spend or output) plus HV7's pinned cap. Must stay <= K1_DAY_CAP_USD (tested)."""
    return math.fsum([A.caps_total_usd(), HV7_DAY_CAP_USD])


# ------------------------------------------------------------------------------------------------------------------------ pricing independence
_IDENTITY_KEYS = ("bot_id", "position_id", "trade_id", "frozen_sha", "meta", "written_ms", "engine_id", "pid", "code_head", "code_dirty", "seq", "sha", "prev")


def price_fingerprint(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Canonical text of every priced row (kind trade/excluded) with the identity fields removed: what is left is pool, instants, slots, legs (both fail
    models, both latencies, every stake) and the pricing inputs. Two bots that bought and sold the same pool at the same instants must give equal lists."""
    from tools.arena_paper import canon

    out = []
    for r in rows:
        if r.get("kind") not in ("trade", "excluded"):
            continue
        out.append(canon({k: v for k, v in r.items() if k not in _IDENTITY_KEYS}))
    return sorted(out)


def assert_identical_pricing(rows_by_bot: Mapping[str, Sequence[Mapping[str, Any]]]) -> int:
    """DESIGN section 3 hard test: raise PricingIndependenceError unless every bot's fingerprint is byte-identical. Returns the number of priced rows compared."""
    prints = {b: price_fingerprint(rows) for b, rows in rows_by_bot.items()}
    ref_bot, ref = next(iter(prints.items()))
    if not ref:
        raise PricingIndependenceError("no priced rows to compare")
    for b, fp in prints.items():
        if fp != ref:
            raise PricingIndependenceError(f"pricing differs between {ref_bot} and {b}: the design is invalid until this is explained")
    return len(ref)


# ------------------------------------------------------------------------------------------------------------------------ the runner
@dataclass
class Hv7Config:
    root: str = HV7_ROOT
    frozen_sha: str = ""
    clock_start_ms: int = 0
    live: bool = False
    entries_halt_ms: int | None = None
    phantom_filter: bool = True
    n: int = R.N_DEFAULT
    n_min: int = R.N_MIN


class _Call:
    """One model call. Sim mode: run inline (the reply time is T + latency_ms, judged in _ask). Live mode: run on a daemon thread; get() is None until the
    call returns, so the loop can keep ticking and decide at t_e."""

    def __init__(self, fn: Callable[[], Any], threaded: bool) -> None:
        self.out: Any = None
        self.done = threading.Event()
        self._fn = fn
        if threaded:
            threading.Thread(target=self._run, daemon=True).start()
        else:
            self._run()

    def _run(self) -> None:
        try:
            self.out = self._fn()
        finally:
            self.done.set()

    def get(self) -> Any:
        return self.out if self.done.is_set() else None


class Hv7Runner:
    def __init__(self, cfg: Hv7Config, state: Any, engine: Any, model: Any, chain: Any, guard: Any = None, now_fn: Callable[[], int] | None = None) -> None:
        self.cfg, self.state, self.engine, self.model, self.chain, self.guard = cfg, state, engine, model, chain, guard
        self.now_fn = now_fn or (lambda: time.time_ns() // 1_000_000)
        self.root = Path(check_path(cfg.root, "w"))
        self.log = Hv7DecisionLog(self.root)
        self.quotes = QuoteHistory()
        self.open: dict[str, Pos] = {}              # engine position id -> Pos
        self.arm_of: dict[str, str] = {}
        self.pending: dict[str, Any] | None = None
        self.counters: dict[str, int] = {}
        self._lock = threading.Lock()              # the L thread and the loop both count
        self.hook_errors: list[str] = []
        self.slot = 0
        self.sim_now = 0
        self._stop = False
        self._sell_next: dict[str, int] = {}
        self._complete_logged = False
        if not cfg.live and hasattr(engine, "now_fn"):
            engine.now_fn = lambda: self.sim_now
        self.phantom = PhantomFilter() if cfg.phantom_filter else None
        orig = state.apply

        def apply(row: dict) -> None:
            if self.phantom is not None:
                try:
                    kind = self.phantom.check(row)
                except Exception as e:  # noqa: BLE001 - a filter failure keeps the row and is recorded
                    kind = None
                    self.hook_errors.append("phantom:" + type(e).__name__)
                if kind is not None:
                    self._count("phantom_drop_" + kind)
                    return
            orig(row)
            self._on_row(row)

        state.apply = apply

    # -- plumbing
    def _count(self, k: str, n: int = 1) -> None:
        with self._lock:
            self.counters[k] = self.counters.get(k, 0) + n

    def _now(self, T: int) -> int:
        return self.now_fn() if self.cfg.live else T

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

    @contextlib.contextmanager
    def _frozen_clock(self, T: int):
        """One call clock for a whole batch of orders (all arms' entries at t_e, all sells of one tick): the engine starts the latency at
        max(stamp, call clock, latest feed row), so arms that call a few ms apart could otherwise land a slot apart."""
        prev = self.engine.now_fn
        call = max(int(self.now_fn()), T) if self.cfg.live else T
        self.engine.now_fn = lambda: call
        try:
            yield
        finally:
            self.engine.now_fn = prev

    def entries_halted_at(self, T: int) -> bool:
        return self.cfg.entries_halt_ms is not None and T >= self.cfg.entries_halt_ms

    # -- t_d: screen, cards, the L call
    def _card(self, pool: str, T: int, snap: Mapping, prints: list, feats: Mapping) -> dict | None:
        part = self.chain.chain_part(pool, snap.get("mint"), T, self.slot)
        if part is None:
            self._count("chain_none")
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
            card = H.build_card(snap, prints, chain, as_of_ms=T, as_of_slot=self.slot, feats=feats,
                                holders_max_age_ms=getattr(self.chain, "holders_max_age_ms", None))
        except C.LeakageError:
            self._count("leakage_refused")
            return None
        except C.CardRefused as e:
            self._count("card_refused")
            self._count("card_refused:" + ",".join(sorted(str(r) for r in e.reasons))[:60])
            return None
        check_card_keys(card)       # raises: a key outside the allowlist is a fault, not a skip
        return card

    def screen(self, T: int) -> None:
        t_d = R.iso_from_ms(T)
        pools = self.state.pools_wsol(lambda p: p.prog == "pumpswap")

        def gen():
            for p in pools:
                snap = self.state.snapshot(p.id)
                if snap is not None:
                    yield snap, list(p.events)

        scratch = C.SetupLimiter()
        res = H.scan(gen(), as_of_ms=T, limiter=scratch, board_on=False)
        vol = {pid: res.feats[pid].get("vol_60m") for pid in res.candidates}
        order = sorted((pid for pid in res.candidates if R._vol_ok(vol[pid])), key=lambda q: (-float(vol[q]), q))
        cards: dict[str, dict] = {}
        vetoed: list[str] = []
        for pid in order:
            if len(cards) >= self.cfg.n:
                break
            snap, prints = self.state.snapshot(pid), list(self.state.pool(pid).events)
            card = self._card(pid, T, snap, prints, res.feats[pid])
            if card is None:
                continue
            imp = card["f"].get("impact_rt_025")
            if not isinstance(imp, (int, float)) or isinstance(imp, bool) or imp > C.IMPACT_RT_CAP:
                vetoed.append(pid)          # fail closed, code veto, replaced by the next pool by vol_60m
                continue
            cards[pid] = card
        scr = R.screen_set(list(cards), vol, n=self.cfg.n, n_min=self.cfg.n_min)
        shas = tuple(A.p4.card_sha256(cards[i]) for i in scr.ids)
        base = {"kind": "period", "t_d": t_d, "t_e": R.iso_from_ms(T - T_D_OFFSET_MS + T_E_OFFSET_MS), "counts": dict(res.counts), "n_candidates": len(res.candidates),
                "vetoed": sorted(vetoed), "screen": list(scr.ids), "card_shas": list(shas), "no_trade": scr.no_trade, "reason": scr.reason, "as_of_slot": self.slot}
        if scr.no_trade:
            self.pending = None
            self.log.append("SCAN", T, base)
            return
        ordered = [cards[i] for i in scr.ids]
        rr = R.build_rank_request(t_d, ordered, seq=T // HOUR_MS)
        call = _Call(lambda: self._ask(rr, T), threaded=self.cfg.live)
        picks = {"R": [R.r_pick(t_d, scr.ids, shas)], "S": [R.s_pick(scr)], "U": list(R.u_set(scr))}
        self.log.append("SCAN", T, base)
        self.pending = {"t_e": T - T_D_OFFSET_MS + T_E_OFFSET_MS, "t_d": T, "t_d_iso": t_d, "picks": picks, "cards": {i: cards[i] for i in scr.ids},
                        "rr": rr, "call": call, "scr_ids": scr.ids, "shas": shas}

    def _resolve_l(self, pend: dict, T: int) -> None:
        """At t_e: the L verdict if the call returned in time, else a FALLBACK ("late"). Logs the rank row and sets L's pick."""
        rr = pend["rr"]
        got = pend["call"].get()
        if got is None:
            self._count("late_reply")
            verdict = R.validate_rank(None, rr.shown_ids)
            mrow = {"model_error": "no_reply_by_t_e", "refused": False, "latency_ms": None, "cost_usd": 0.0, "response_sha256": None, "late": True}
        else:
            verdict, mrow = got
        choice = R.l_choice(verdict, pend["t_d_iso"], pend["scr_ids"], pend["shas"])
        slot = R.display_slot_of(rr.perm, pend["scr_ids"], choice.pool_id) if not choice.fallback else None
        self.log.append("L", pend["t_d"], {"kind": "rank", **mrow, "request_sha256": rr.request.request_sha256(), "shown": list(rr.shown_ids),
                                           "perm": list(rr.perm), "ok": verdict.ok, "order": list(verdict.order), "fallback": choice.fallback,
                                           "fallback_reason": choice.reason, "pick": choice.pool_id, "pick_display_slot": slot,
                                           "why_defect": verdict.why_defect, "why_words_over_20": verdict.why_words_over_20,
                                           "why_without_field": verdict.why_without_field})
        pend["picks"]["L"] = [choice.pool_id]

    def _ask(self, rr: R.RankRequest, T: int) -> tuple[R.RankVerdict, dict]:
        """One model call. Anything but a full permutation in time (error, refusal, budget stop, exception, late reply) is a FALLBACK verdict."""
        t_e = T - T_D_OFFSET_MS + T_E_OFFSET_MS
        row: dict[str, Any] = {"model_error": None, "refused": False, "latency_ms": None, "cost_usd": 0.0, "response_sha256": None, "late": False}
        try:
            reply = self.model.complete(rr.request, self._now(T))
        except Exception as e:  # noqa: BLE001 - class name only
            row["model_error"] = "exception:" + type(e).__name__
            self._count("model_exception")
            return R.validate_rank(None, rr.shown_ids), row
        row.update(model_error=reply.error, refused=bool(reply.refused), latency_ms=reply.latency_ms, cost_usd=reply.cost_usd, response_sha256=reply.response_sha256,
                   replayed=bool(getattr(reply, "replayed", False)))
        row.update(self._reconcile(rr, T, row))
        recv = self.now_fn() if self.cfg.live else T + int(reply.latency_ms or 0)
        if reply.error is not None or reply.refused:
            return R.validate_rank(None, rr.shown_ids), row
        if recv > t_e:
            row["late"] = True
            self._count("late_reply")
            return R.validate_rank(None, rr.shown_ids), row
        return R.validate_rank(reply.text, rr.shown_ids), row

    def _reconcile(self, rr: R.RankRequest, T: int, row: Mapping[str, Any]) -> dict:
        """Log the budget ledger's own figure for this request next to the row's cost_usd. A replayed answer must show 0.0 in the ledger. A mismatch is counted,
        never hidden; an unreadable ledger is recorded as null."""
        g = self.guard
        if g is None or not hasattr(g, "request_cost"):
            return {"ledger_cost_usd": None, "cost_reconciled": None}
        try:
            led = g.request_cost(utc_day(T), rr.request.request_sha256())
        except Exception:  # noqa: BLE001 - recorded as null
            self._count("ledger_unreadable")
            return {"ledger_cost_usd": None, "cost_reconciled": None}
        want = 0.0 if row.get("replayed") else float(row.get("cost_usd") or 0.0)
        ok = abs(led - want) <= 1e-9
        if not ok:
            self._count("cost_mismatch")
        return {"ledger_cost_usd": led, "cost_reconciled": ok}

    # -- t_e: entries
    def enter(self, T: int) -> None:
        pend = self.pending
        if pend is None or pend["t_e"] != T:
            return
        self.pending = None
        self._resolve_l(pend, T)
        if self.entries_halted_at(T):
            self.log.append("SCAN", T, {"kind": "entries_halted", "t_e": R.iso_from_ms(T)})
            return
        with self._frozen_clock(T):
            for arm in ARM_ORDER:
                for pool in pend["picks"][arm]:
                    self._open(arm, pend["cards"][pool], T)

    def _open(self, arm: str, card: Mapping, T: int) -> str | None:
        snap = self.state.snapshot(card["id"], candles=False)
        price = snap.get("price") if snap else None
        bot = bot_id(arm)
        pid = position_id(bot, card["id"], T, self.slot)
        lim = A.GEN_SPEC.code_limits
        pos = Pos(arm=arm, pid=pid, pool=card["id"], mint=(snap or {}).get("mint"), size=STAKE_SOL, entry_ms=T, entry_price=float(price or 0.0),
                  tokens_eq=(STAKE_SOL / float(price)) if price else 0.0, stop_pct=EXIT["stop_pct"], target_pct=EXIT["target_pct"], trail_pct=EXIT["trail_pct"],
                  time_min=EXIT["time_min"], hard_stop_pct=lim["hard_stop_pct"], max_hold_ms=int(EXIT["time_min"] * MIN_MS), young=False)
        self.log.append(arm, T, {"kind": "entry", "order_id": pid, "pool": card["id"], "card_sha256": A.p4.card_sha256(card), "size": STAKE_SOL,
                                 "plan": dict(EXIT), "decision_slot": self.slot, "pos": asdict(pos)})
        try:
            got = self.engine.buy(bot, card["id"], STAKE_SOL, T, self.slot, meta={"arm": arm, "card_sha256": A.p4.card_sha256(card), "order_id": pid})
        except ArenaRefused as e:
            self.log.append(arm, T, {"kind": "order_refused", "order_id": pid, "side": "buy", "reason": "arena_refused", "error_class": type(e).__name__})
            self._count("order_refused")
            return None
        assert got == pid, "engine position id differs from the logged order id"
        self.open[pid] = pos
        return pid

    # -- exits
    def exits(self, T: int) -> None:
        with self._frozen_clock(T):
            for pid in sorted(self.open, key=lambda i: (self.open[i].pool, self.open[i].arm, i)):
                pos = self.open.get(pid)
                if pos is None:
                    continue
                ret = mark_ret(pos, self.state.snapshot(pos.pool, candles=False))
                if T - pos.entry_ms < ENTRY_SETTLE_MS:
                    continue
                why = exit_reason(pos, ret, T)
                if why:
                    self._sell(pos, why, T, ret)

    def _sell(self, pos: Pos, why: str, T: int, ret: float | None) -> bool:
        now = self._now(T)
        if self._sell_next.get(pos.pid, 0) > now:
            return False
        self.log.append(pos.arm, T, {"kind": "sell", "pid": pos.pid, "pool": pos.pool, "reason": why, "est_ret": ret, "decision_slot": self.slot})
        try:
            self.engine.sell(bot_id(pos.arm), pos.pid, 1.0, T, self.slot, reason=why)
        except ArenaRefused as e:
            self._count("sell_refused")
            drop = any(k in str(e) for k in ("no open position", "already has a sell"))
            self.log.append(pos.arm, T, {"kind": "order_refused", "order_id": pos.pid, "side": "sell", "reason": "arena_refused", "error_class": type(e).__name__,
                                         "engine_drop": drop})
            if drop:
                self.open.pop(pos.pid, None)
            else:
                self._sell_next[pos.pid] = now + SELL_RETRY_MS
            return False
        self._sell_next.pop(pos.pid, None)
        self.open.pop(pos.pid, None)
        return True

    # -- loop
    def tick(self, T: int) -> None:
        self.sim_now = max(self.sim_now, T)
        self.poll_to(T)
        self.exits(T)                                  # exits before entries, always
        if self.entries_halted_at(T):
            if not self.open and not self._complete_logged:
                self._complete_logged = True
                self.log.append("SCAN", T, {"kind": "run_complete", "reason": "entries_halt_and_no_open_position"})
                self._stop = True
            return
        m = T % HOUR_MS
        if m == T_D_OFFSET_MS:
            self.screen(T)
        elif m == T_E_OFFSET_MS:
            self.enter(T)

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

    def log_start(self) -> None:
        self.log.append("SCAN", self.sim_now or self.now_fn(), {"kind": "start", "frozen_sha": self.cfg.frozen_sha, "entries_halt_ms": self.cfg.entries_halt_ms,
                                                              "clock_start_ms": self.cfg.clock_start_ms, "live": bool(self.cfg.live), "day_cap_usd": HV7_DAY_CAP_USD})

    def decision_md5(self) -> str:
        return self.log.md5()


def main(argv: list[str] | None = None) -> int:
    """A MiScusi job. NEVER RUN in C2 (no owner cost line yet; C3 hardens, drills and registers it). Refuses without an explicit owner-line reference."""
    ap = argparse.ArgumentParser(description="HV7 forced-rank runner (paper only)")
    ap.add_argument("--tape", required=True)
    ap.add_argument("--root", default=HV7_ROOT)
    ap.add_argument("--frozen-sha", required=True)
    ap.add_argument("--clock-start-ms", type=int, required=True)
    ap.add_argument("--entries-halt-ms", type=int, required=True)
    ap.add_argument("--feed-delay-slots", type=int, required=True)
    ap.add_argument("--owner-cost-line-ref", required=True, help="reference to the owner's HV7 cost line (DESIGN section 5); no API call without it")
    a = ap.parse_args(argv)
    from tools.arena_paper import ArenaEngine
    from tools.hv6_agents import CallerModel
    from tools.hv6_runner import live_chain_source

    guard = Hv7Guard(a.root)
    engine = ArenaEngine(cfg=stack_config(a.feed_delay_slots))
    run = Hv7Runner(Hv7Config(root=a.root, frozen_sha=a.frozen_sha, clock_start_ms=a.clock_start_ms, live=True, entries_halt_ms=a.entries_halt_ms),
                    ArenaState(check_path(a.tape, "r"), start_ms=0), engine, CallerModel(guard), live_chain_source(a.root), guard)
    signal.signal(signal.SIGTERM, run.request_stop)
    engine.start_all()
    run.log_start()
    run.run(int(time.time_ns() // 1_000_000) // TICK_MS * TICK_MS, 1 << 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
