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
     EXCEPTION: if the budget ledger holds a reserve row for that request and the cache has no <request_sha256>.json, the call was cut off. It is NEVER re-asked:
     L gets a counted FALLBACK rank row (cut_off true, model_error call_cut_off, pick = R's) and every arm still enters at t_e.
  4. An L call still in flight at shutdown is the same thing: an L FALLBACK rank row (cut_off true, model_error shutdown_in_flight), counted in the 10% fallback
     rule, not an exclusion. A period resumed AFTER t_e, or one whose rebuild does not match, is EXCLUDED in all four arms (SCAN `period_excluded` row and an L
     `rank` row with excluded true). Exclusions have their own 10% cap in hv7_count; over it every arm is NOT EVALUABLE.
  5. A period whose rank row exists but whose entries are incomplete (kill during enter): a `gap` row per missing (arm, pool) entry; never a late entry.
  LIVE LIMITS (disclosed, QP/reviewer #1033): (a) in a live run the prefetcher store is in memory and empty after a restart, and part_at(t_d) serves only facts
     stamped before t_d, so a live restart between t_d and t_e can never rebuild the screen: rule 3 ends in resume_rebuild_mismatch and the period is EXCLUDED
     (counted in the 10% exclusion cap). The success path of rule 3 is reachable only with a direct-fetch chain, as in the drills. (b) A live run adds one
     `run_summary` row to the SCAN log at every stop (and a `start` row carries the provider), so rule 6's "decision-log md5 equal to an uninterrupted run"
     holds on the drill / sim path only. (c) The first screen after a restart (or the first ever) can lack a holders block and may not hold the top pools: the prefetcher only fetches in
     [H:48, H:02) and its store starts empty. (d) engine.start_record is called for the four HV7 books only, never start_all.
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
from tools import hv7_tip_guard as U2  # noqa: E402  (rule U2: only created_known pools reach the age band)
from tools.hv6_phantom import PhantomFilter
from tools.hv6_runner import (CHAIN_CREDITS_FILE, CHAIN_SPEND_FILE, HOUSEKEEP_EVERY_MS, ChainSource, Pos, QuoteHistory, exit_reason, mark_ret,  # noqa: F401
                              pinned_provider, position_id, refuse_hv4_chain_state, stack_config)

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

TIP_FEED_ROOT = "/data2/mal/arena-tip-feed/hv7"      # the research-0 receiver copy of the arena tip writer's derived tape (QP-RULING-1101-arena-tip-reader B1; sealed: only the pinned
#                                                      # tip-source runner, its smoke and the P0 tip counter read its tape files; agents read only its status and arrival log)
TIP_WRITER_DIR = ".writer"                           # the writer's state dir (state.json holds pool addresses and signatures): never readable through this guard
READ_ROOTS = (H.TAPE_ROOT, HV7_ROOT, TIP_FEED_ROOT)
WRITE_ROOTS = (HV7_ROOT,)

# Live chain client. HV7 has its own RPC budget so it does not compete with HV6's 1,000 calls/h; state and spend files sit under HV7_ROOT.
HV7_RPC_CALLS_PER_H = 500
HV7_RPC_CALLS_PER_DAY = 24 * HV7_RPC_CALLS_PER_H     # 12,000
WATCH_MAX = 200                                       # the prefetcher's watch set, top pools by vol_60m. NOT HV6's 2,000: at 500 calls/h the cold mint tier of 1,000 watched pools
#                                                     # eats the budget the hot holders reads need (sim, PR #1033: no holders block for 4 of 5 screens); at 200 it holds for 100/300/1,000 candidates
WATCH_HOT_TOP = 32                                    # hot = the top WATCH_HOT_TOP vol_ok candidates by vol_60m (the pools the screen will try first) ...
WATCH_HOT_MAX = 32                                    # ... plus the screened and card-tried pools of the last screen (at most this many), so hot can reach WATCH_HOT_TOP + WATCH_HOT_MAX = 64
# The prefetcher may fetch only in the window [H:48, H:02) UTC, which ends at t_d. At 500 calls/h the rolling-hour cap is always spent: left free-running, the
# burst re-spends one window later and drifts about a minute an hour relative to t_d, so most screens saw stale or no facts (QP sim, PR #1033). Phase-locked to t_d,
# all of the hour's budget lands in the 14 minutes before the screen, so the facts it reads are at most 14 minutes old (the card limit is 15).
PREFETCH_WINDOW_START_MS = HOUR_MS - 12 * MIN_MS      # H:48:00
PREFETCH_WINDOW_END_MS = T_D_OFFSET_MS                # H:02:00 = t_d, exclusive


def prefetch_window_open(now_ms: int) -> bool:
    m = now_ms % HOUR_MS
    return m >= PREFETCH_WINDOW_START_MS or m < PREFETCH_WINDOW_END_MS
WATCH_EVERY_MS = 60_000                               # the watch is told on every minute tick, before the hourly card build


# Owner cost-line reference: a MiScusi notebook id (n_...) or a MiScusi question id (q_...). real ids contain "-" and "_" (e.g. q_6_kzYJva9XXuYQ), so the body is 12-20 of [A-Za-z0-9_-];
# the pattern is pinned here so a typo or a free-text string cannot unlock the API path.
OWNER_REF_RE = re.compile(r"(?:n|q)_[A-Za-z0-9_-]{12,20}")


class Hv7Error(RuntimeError):
    pass


def registered_pins(registry_path: str | os.PathLike) -> dict[str, dict[str, Any]]:
    """bot id -> {"clock_start_ms", "entries_halt_ms"} for the four HV7 books, read from the registry (the `register` lines and the `hv7_halt` lines that
    tools/hv7_register.py appends). Raises Hv7Error unless all four ids are registered with ONE clock and ONE halt."""
    rp = Path(registry_path)
    reg: dict[str, Any] = {}
    halt: dict[str, int] = {}
    if rp.exists():
        for line in rp.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("kind") == "register" and str(r.get("bot_id", "")).startswith(BOT_PREFIX):
                reg[r["bot_id"]] = r["clock_start_ms"]
            elif r.get("kind") == "hv7_halt":
                halt[r["bot_id"]] = r["entries_halt_ms"]
    ids = [bot_id(a) for a in ARM_ORDER]
    if any(i not in reg or i not in halt for i in ids) or len({reg[i] for i in ids}) != 1 or len({halt[i] for i in ids}) != 1:
        raise Hv7Error("the registry does not hold all four HV7 books with one clock and one entries halt")
    return {i: {"clock_start_ms": reg[i], "entries_halt_ms": halt[i]} for i in ids}


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
        if H._under(cand, TIP_FEED_ROOT) and TIP_WRITER_DIR in cand.split(os.sep):
            raise H.PathRefused("tip_writer_state")
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


def _gated_prefetcher_class() -> type:
    from tools.hv4_prefetch import ChainPrefetcher

    class Hv7ChainPrefetcher(ChainPrefetcher):
        """HV4's ChainPrefetcher (policy v2), unchanged, whose passes run only inside the t_d-locked window (prefetch_window_open). `watch` is not gated: it makes no RPC."""

        def run_once(self, now_ms: int | None = None) -> int:
            now = self.clock() if now_ms is None else now_ms
            return super().run_once(now_ms) if prefetch_window_open(now) else 0

    return Hv7ChainPrefetcher


def live_chain_source(root: str | os.PathLike, env: Mapping[str, str] | None = None) -> ChainSource:
    """HV6's ChainSource (compose and chain_part are HV6's, byte for byte) over an HV4Chain whose RpcClient state file and whose policy-v2 prefetcher spend file are
    under the HV7 root, every path through HV7's check_path (H.check is bound to HV6's roots). Provider pinned and HV4's chain state refused, as in HV6. The thread
    is not started here."""
    refuse_hv4_chain_state(env)
    pinned_provider(env)
    from tools.hv4_chain import HV4Chain, RpcClient, RpcConfig

    state = check_path(Path(root) / CHAIN_CREDITS_FILE, "w")
    spend = check_path(Path(root) / CHAIN_SPEND_FILE, "w")
    os.makedirs(os.path.dirname(state), exist_ok=True)
    chain = HV4Chain(RpcClient(RpcConfig.from_env(env), state_path=state, daily_call_cap=HV7_RPC_CALLS_PER_DAY))
    return ChainSource(chain, _gated_prefetcher_class()(chain, ChainSource.compose, spend_path=Path(spend), policy="v2", hourly_budget=HV7_RPC_CALLS_PER_H))


def watch_sets(vol: Mapping[str, Any], last_hot: Sequence[str] = ()) -> tuple[list[str], list[str], dict[str, float]]:
    """(items, hot, vols) for ChainPrefetcher.watch. vol = vol_60m of every candidate pool. hot = the top WATCH_HOT_TOP vol_ok candidates by vol_60m, then the
    screened and card-tried pools of the last screen that are still candidates. items = hot, then the other candidates by vol_60m, at most WATCH_MAX, never
    cutting the hot set."""
    ok = sorted((q for q in vol if R._vol_ok(vol[q])), key=lambda q: (-float(vol[q]), q))
    hot = list(dict.fromkeys([*ok[:WATCH_HOT_TOP], *(q for q in last_hot if q in vol)]))
    in_hot = set(hot)
    rest = sorted((q for q in vol if q not in in_hot), key=lambda q: (-float(vol[q] or 0.0), q))
    items = (hot + rest)[:max(WATCH_MAX, len(hot))]
    return items, hot, {q: float(vol[q] or 0.0) for q in items}


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

    def reserved_without_cache(self, day: str, request_sha256: str) -> bool:
        """True when the budget ledger holds a reserve row for this request but the replay cache has no <request_sha256>.json: the call was cut off (crash or
        shutdown) after it was reserved. Such a request is NEVER re-asked (a second reserve could pay twice)."""
        led = Ledger(self.root / "ledgers" / RANK_LEDGER / f"{day}.jsonl")
        with led.locked(exclusive=False):
            rows = led.rows()
        if not any(r.get("kind") == "reserve" and r.get("request_sha256") == request_sha256 for r in rows):
            return False
        return not (self.root / "cache" / RANK_LEDGER / f"{request_sha256}.json").exists()

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
    # Memory bound (Hv7Runner._housekeep), the same one HV6 runs: QuoteHistory forgets pools the state no longer holds and the engine trims print history to what
    # a later buy can read. Decision-neutral by construction and by test (tools/test_hv7_runner_mem.py). Off only in the test that proves it changes nothing.
    # CAVEAT (same as HV6, deliberate): a pool revived after more than the state's 6 h eviction horizon (state.evict_idle_ms) idle has had its QuoteHistory swept, so
    # its quote_sol_15m_ago / quote_sol_peak facts are computed from the quotes since the revival only. The state's own eviction already resets such a pool's age, so
    # the sweep follows the state; it is not exact against a never-swept history. tools/test_hv7_runner_mem.py::test_revived_pool_after_sweep pins the behaviour.
    bound_memory: bool = True
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
        self.creation = U2.CreationIndex()
        self.counters: dict[str, int] = {}
        self._last_hot: list[str] = []
        self._lock = threading.Lock()              # the L thread and the loop both count
        self.hook_errors: list[str] = []
        self.slot = 0
        self.sim_now = 0
        self._housekeep_at = 0      # tape instant (t_recv_ms) of the next memory housekeeping
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
            self.creation.note(row)            # U2: the creation index sees exactly the rows the state applied (a phantom-dropped row is in neither)
            self._on_row(row)

        state.apply = apply

    # -- plumbing
    def _snap(self, key: str, **kw: Any) -> Mapping[str, Any] | None:
        """ArenaState.snapshot with the creation the state's 6 h idle eviction forgot put back from the creation index (rule U2, reviewer B1 on PR #1170)."""
        return self.creation.patch(self.state.snapshot(key, **kw))

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
        t = row.get("t_recv_ms")
        if self.cfg.bound_memory and isinstance(t, int) and not isinstance(t, bool) and t >= self._housekeep_at:
            try:  # its own try: a housekeeping failure must never skip a row's engine feed
                self._housekeep(t)
            except Exception as e:  # noqa: BLE001
                self.hook_errors.append("housekeep:" + type(e).__name__)

    def _housekeep(self, t: int) -> None:
        """As Runner._housekeep in HV6 (same bounds, same engine API), driven by the tape clock: QuoteHistory forgets pools idle past the state's eviction horizon
        that the state no longer holds; the engine trims the print history of every pool with no open/pending position (ArenaEngine.trim_history)."""
        self._housekeep_at = t - t % HOUSEKEEP_EVERY_MS + HOUSEKEEP_EVERY_MS
        self._count("quote_pools_swept", self.quotes.sweep(t, self.state.evict_idle_ms, held=lambda k: self.state.pool(k) is not None))
        if hasattr(self.engine, "trim_history"):
            self._count("engine_trims")
            self._count("engine_prints_trimmed", int(self.engine.trim_history() or 0))
        self.sync_engine_counters()

    def sync_engine_counters(self) -> None:
        """Surface the engine's trim-safety counters in the runner counters (set, not added). A NONZERO rows_below_trim_floor or any refused_history_trimmed voids the
        trim's equivalence (arena_paper.py trim note), so a run must show both at 0."""
        ec = getattr(self.engine, "counters", None) or {}
        with self._lock:
            for k in ("rows_below_trim_floor", "refused_history_trimmed", "hist_trimmed_prints"):
                self.counters["engine_" + k] = int(ec.get(k, 0))

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

    def watch_tick(self, T: int) -> None:
        """Once a minute (live chain with a background prefetcher only; a fake chain has no `watch`, so replays and drills never reach the scan below): scan the
        state polled to T and tell the prefetcher the watch set, BEFORE the hourly card build, so the facts the screen reads at t_d are about a minute old, not fifty.
        No RPC and no state change here; the decision path never reads this."""
        if T % WATCH_EVERY_MS or getattr(self.chain, "watch", None) is None:
            return
        pools = self.state.pools_wsol(lambda p: p.prog == "pumpswap")

        def gen():
            for p in pools:
                snap = self._snap(p.id)
                if snap is not None:
                    yield snap, list(p.events)

        res = H.scan(U2.guard(gen()), as_of_ms=T, limiter=C.SetupLimiter(), board_on=False)     # U2: the watch set holds only pools the screen can admit
        vol = {q: res.feats[q].get("vol_60m") for q in res.candidates}
        items, hot, vols = watch_sets(vol, self._last_hot)
        self.chain.watch([(q, self.state.pool(q).mint) for q in items], hot, self._now(T), vols=vols)

    def screen(self, T: int, rebuild: Mapping[str, Any] | None = None, rank_row: Mapping[str, Any] | None = None) -> None:
        """rebuild: the logged period row of a resumed period. The screen is re-run from the replayed tape and must equal the logged one; no new SCAN row. rank_row: the L rank row already logged for it (a fallback written at shutdown): L's pick is taken from it, the model is not asked."""
        t_d = R.iso_from_ms(T)
        pools = self.state.pools_wsol(lambda p: p.prog == "pumpswap")

        def gen():
            for p in pools:
                snap = self._snap(p.id)
                if snap is not None:
                    yield snap, list(p.events)

        scratch = C.SetupLimiter()
        u2_refused = [0]
        res = H.scan(U2.guard(gen(), u2_refused), as_of_ms=T, limiter=scratch, board_on=False)    # rule U2, before `filters` and the cards
        if u2_refused[0]:
            self._count("u2_" + U2.COUNTER, u2_refused[0])
        vol = {pid: res.feats[pid].get("vol_60m") for pid in res.candidates}
        order = sorted((pid for pid in res.candidates if R._vol_ok(vol[pid])), key=lambda q: (-float(vol[q]), q))
        cards: dict[str, dict] = {}
        vetoed: list[str] = []
        tried: list[str] = []
        for pid in order:
            if len(cards) >= self.cfg.n:
                break
            tried.append(pid)
            snap, prints = self._snap(pid), list(self.state.pool(pid).events)
            card = self._card(pid, T, snap, prints, res.feats[pid])
            if card is None:
                continue
            imp = card["f"].get("impact_rt_025")
            if not isinstance(imp, (int, float)) or isinstance(imp, bool) or imp > C.IMPACT_RT_CAP:
                vetoed.append(pid)          # fail closed, code veto, replaced by the next pool by vol_60m
                continue
            cards[pid] = card
        scr = R.screen_set(list(cards), vol, n=self.cfg.n, n_min=self.cfg.n_min)
        self._last_hot = list(dict.fromkeys([*scr.ids, *tried]))[:WATCH_HOT_MAX]      # the next minute ticks keep these hot
        shas = tuple(A.p4.card_sha256(cards[i]) for i in scr.ids)
        base = {"kind": "period", "t_d": t_d, "t_e": R.iso_from_ms(T - T_D_OFFSET_MS + T_E_OFFSET_MS), "counts": {**res.counts, U2.COUNTER: u2_refused[0]}, "n_candidates": len(res.candidates),
                "vetoed": sorted(vetoed), "screen": list(scr.ids), "card_shas": list(shas), "no_trade": scr.no_trade, "reason": scr.reason, "as_of_slot": self.slot}
        if rebuild is not None and (list(scr.ids) != list(rebuild.get("screen", ())) or list(shas) != list(rebuild.get("card_shas", ())) or scr.no_trade):
            self._count("resume_rebuild_mismatch")
            self.pending = None
            self._log_excluded(T, t_d, "resume_rebuild_mismatch")
            return
        if scr.no_trade:
            self.pending = None
            self.log.append("SCAN", T, base)
            return
        ordered = [cards[i] for i in scr.ids]
        rr = R.build_rank_request(t_d, ordered, seq=T // HOUR_MS)
        l_logged = None
        if rank_row is not None:
            l_logged, call = rank_row.get("pick"), None
        elif rebuild is not None and self._cut_off(rr, T):
            call = None                                  # (b): reserved, never answered: a counted L FALLBACK, never re-asked
        else:
            call = _Call(lambda: self._ask(rr, T), threaded=self.cfg.live)
        picks = {"R": [R.r_pick(t_d, scr.ids, shas)], "S": [R.s_pick(scr)], "U": list(R.u_set(scr))}
        if rebuild is None:
            self.log.append("SCAN", T, base)
        self.pending = {"t_e": T - T_D_OFFSET_MS + T_E_OFFSET_MS, "t_d": T, "t_d_iso": t_d, "picks": picks, "cards": {i: cards[i] for i in scr.ids},
                        "rr": rr, "call": call, "scr_ids": scr.ids, "shas": shas, "l_logged": l_logged}
        if rebuild is not None and rank_row is None and call is None:
            self._resolve_l(self.pending, T, forced="call_cut_off")
            self.pending["l_logged"] = self.pending["picks"]["L"][0]

    def _cut_off(self, rr: R.RankRequest, T: int) -> bool:
        g = self.guard
        if g is None or not hasattr(g, "reserved_without_cache"):
            return False
        try:
            hit = g.reserved_without_cache(utc_day(T), rr.request.request_sha256())
        except Exception:  # noqa: BLE001 - an unreadable ledger fails closed: do not re-ask
            self._count("ledger_unreadable")
            return True
        if hit:
            self._count("call_cut_off")
        return hit

    def _resolve_l(self, pend: dict, T: int, forced: str | None = None) -> None:
        """At t_e: the L verdict if the call returned in time, else a FALLBACK ("late"). Logs the rank row and sets L's pick."""
        rr = pend["rr"]
        got = None if forced else (pend["call"].get() if pend["call"] is not None else None)
        if forced:
            verdict = R.validate_rank(None, rr.shown_ids)
            mrow = {"model_error": forced, "refused": False, "latency_ms": None, "cost_usd": 0.0, "response_sha256": None, "late": False, "cut_off": True}
        elif got is None:
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
        if pend.get("l_logged") is not None:
            pend["picks"]["L"] = [pend["l_logged"]]         # the rank row is already in the log (cut-off fallback)
        else:
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

    # -- exclusion, shutdown, resume
    def _log_excluded(self, t_d_ms: int, t_d_iso: str, reason: str) -> None:
        """A period that gets no entry in any arm (rule 4): a SCAN row and an L rank row that says fallback true, excluded true."""
        self.log.append("SCAN", t_d_ms, {"kind": "period_excluded", "t_d": t_d_iso, "reason": reason})
        self.log.append("L", t_d_ms, {"kind": "rank", "model_error": reason, "refused": False, "latency_ms": None, "cost_usd": 0.0, "response_sha256": None,
                                      "late": False, "ok": False, "order": [], "fallback": True, "fallback_reason": reason, "excluded": True, "pick": None,
                                      "pick_display_slot": None})

    def shutdown(self) -> None:
        """Orderly stop. An L call still in flight cannot be waited for: it is an L FALLBACK (rank row, cut_off true, counted in the 10% rule), not an exclusion.
        Its pick is R's. A resume at or before t_e takes L's pick from that row and enters all arms; the call is never re-asked. A pending period whose reply
        is already back is left alone: resume re-asks it through the cache."""
        p = self.pending
        if p is not None and p["call"] is not None and not p["call"].done.is_set():
            self._count("shutdown_in_flight")
            self._resolve_l(p, p["t_d"], forced="shutdown_in_flight")
            p["l_logged"] = p["picks"]["L"][0]
            self.pending = None

    def _repair_tails(self, now_ms: int) -> int:
        n = 0
        for name in ("SCAN",) + ARM_ORDER:
            for f in self.log.files(name):
                data = f.read_bytes()
                if not data or data.endswith(b"\n"):
                    continue
                cut = data.rfind(b"\n") + 1
                dropped = data[cut:]
                check_path(f, "w")
                with open(f, "r+b") as fh:
                    fh.truncate(cut)
                    fh.flush()
                    os.fsync(fh.fileno())
                self.log._head.pop(name, None)
                self.log.verify(name)           # the truncated log must chain, else LogChainError: not repairable
                self.log.append("SCAN", now_ms, {"kind": "gap", "reason": "torn_last_line", "stream": name, "file": f.name, "dropped_bytes": len(dropped),
                                                 "dropped_sha256": hashlib.sha256(dropped).hexdigest()})
                n += 1
        return n

    def _gap_once(self, now_ms: int, key: str, body: dict) -> bool:
        if any(r.get("kind") == "gap" and r.get("key") == key for r in self.log.rows("SCAN")):
            return False
        self.log.append("SCAN", now_ms, {"kind": "gap", "key": key, **body})
        return True

    def resume(self, now_ms: int) -> int:
        """Rebuild after a restart (rules 1-6 in the module docstring). Returns the tick to start `run` from. The tape state is NOT in the log: it is replayed
        by the loop's own poll, as on a first start."""
        now_ms = int(now_ms) // TICK_MS * TICK_MS
        torn = self._repair_tails(now_ms)
        for name in ("SCAN",) + ARM_ORDER:
            self.log._head.pop(name, None)
            self.log.verify(name)
        self.engine.recover()
        gaps = 0
        rows = {a: self.log.rows(a) for a in ARM_ORDER}
        for arm in ARM_ORDER:
            sold = {r["pid"] for r in rows[arm] if r.get("kind") == "sell"}
            refused = {r["order_id"] for r in rows[arm] if r.get("kind") == "order_refused" and r.get("side") == "buy"}
            for r in rows[arm]:
                if r.get("kind") != "entry":
                    continue
                pid = r["order_id"]
                ep = self.engine.positions.get(pid)
                if ep is not None and ep.exit_slot is None:
                    self.open[pid] = Pos(**r["pos"])
                elif ep is None and pid not in sold and pid not in refused:
                    gaps += self._gap_once(now_ms, "entry_not_in_engine:" + pid, {"reason": "entry_not_in_engine", "arm": arm, "order_id": pid, "pool": r["pool"]})
        nxt = now_ms
        periods = [r for r in self.log.rows("SCAN") if r.get("kind") == "period" and not r.get("no_trade")]
        pending_rebuilt = False
        if periods:
            last = periods[-1]
            t_d, t_e = R.ms_from_iso(last["t_d"]), R.ms_from_iso(last["t_e"])
            ranks = [r for r in rows_l(self.log) if r["ms"] == t_d]
            if not ranks:
                if now_ms <= t_e:
                    self.poll_to(t_d)
                    self.sim_now = max(self.sim_now, t_d)
                    self.screen(t_d, rebuild=last)
                    pending_rebuilt = self.pending is not None
                    nxt = t_d + TICK_MS
                else:
                    self._count("restart_after_t_e")
                    self._log_excluded(t_d, last["t_d"], "restart_after_t_e")
            elif (not ranks[-1].get("excluded") and now_ms <= t_e and ranks[-1].get("cut_off") and ranks[-1].get("pick")
                  and not any(r.get("kind") == "entry" and r["ms"] == t_e for a in ROWS_ARMS(rows) for r in a)
                  and not any(r.get("kind") == "entries_halted" and r.get("t_e") == last["t_e"] for r in self.log.rows("SCAN"))):
                self.poll_to(t_d)
                self.sim_now = max(self.sim_now, t_d)
                self.screen(t_d, rebuild=last, rank_row=ranks[-1])
                pending_rebuilt = self.pending is not None
                nxt = t_d + TICK_MS
            elif not ranks[-1].get("excluded") and not any(r.get("kind") == "entries_halted" and r.get("t_e") == last["t_e"] for r in self.log.rows("SCAN")):
                want = {"L": [ranks[-1]["pick"]], "R": [R.r_pick(last["t_d"], last["screen"], last["card_shas"])], "S": [last["screen"][0]], "U": list(last["screen"])}
                for arm in ARM_ORDER:
                    have = {r["pool"] for r in rows[arm] if r.get("kind") == "entry" and r["ms"] == t_e}
                    for pool in want[arm]:
                        if pool not in have:
                            gaps += self._gap_once(now_ms, f"entry_missing:{arm}:{pool}:{t_e}", {"reason": "entry_missing_after_restart", "arm": arm, "pool": pool, "t_e": last["t_e"]})
        ev = {"ms": now_ms, "kind": "resume", "torn_lines": torn, "gap_rows": gaps, "open_rebuilt": len(self.open), "pending_rebuilt": pending_rebuilt,
              "next_tick": nxt, "frozen_sha": self.cfg.frozen_sha}
        path = Path(check_path(self.root / "resume.jsonl", "w"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "ab") as fh:
            fh.write(json.dumps(ev, sort_keys=True, separators=(",", ":")).encode() + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        return nxt

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
        self.watch_tick(T)                             # before the hourly card build (the minute tick at t_d runs first)
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
        self.sync_engine_counters()

    def request_stop(self, *_a: Any) -> None:
        self._stop = True

    def summary(self, provider: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"counters": dict(self.counters), "provider": provider}
        rep = getattr(self.chain, "credit_report", None)
        if rep is not None:
            try:
                out["chain_credits"] = rep()
            except Exception as e:  # noqa: BLE001
                out["chain_credits"] = {"error": type(e).__name__}
        return out

    def log_start(self, provider: str | None = None) -> None:
        # `provider` is recorded only when given (live), so a stub/replay start row is byte-identical to before
        extra = {"provider": provider} if provider is not None else {}
        self.log.append("SCAN", self.sim_now or self.now_fn(), {"kind": "start", **extra, "frozen_sha": self.cfg.frozen_sha, "entries_halt_ms": self.cfg.entries_halt_ms,
                                                              "clock_start_ms": self.cfg.clock_start_ms, "live": bool(self.cfg.live), "day_cap_usd": HV7_DAY_CAP_USD})

    def decision_md5(self) -> str:
        return self.log.md5()


def ROWS_ARMS(rows: Mapping[str, list]) -> list[list]:
    return [rows[a] for a in ARM_ORDER]


def rows_l(log: DecisionLog) -> list[dict]:
    return [r for r in log.rows("L") if r.get("kind") == "rank"]


def main(argv: list[str] | None = None, *, _stub: Mapping[str, Any] | None = None) -> int:
    """A MiScusi job. Refuses unless --owner-cost-line-ref matches OWNER_REF_RE. `_stub` is for tests and the C3b smoke only: a dict with model, chain, engine,
    start_ms and end_ms; the refusals above it (owner ref, registry pins) are the same code path, then the run is simulated (not live), with no signal handler."""
    ap = argparse.ArgumentParser(description="HV7 forced-rank runner (paper only)")
    ap.add_argument("--tape", required=True)
    ap.add_argument("--root", default=HV7_ROOT)
    ap.add_argument("--frozen-sha", required=True)
    ap.add_argument("--clock-start-ms", type=int, required=True)
    ap.add_argument("--entries-halt-ms", type=int, required=True)
    ap.add_argument("--feed-delay-slots", type=int, required=True)
    ap.add_argument("--registry", default=None, help="bots.json to read the pins from (default: the real registry)")
    ap.add_argument("--ledger-root", default=None, help="engine ledger dir; default <root>/engine-ledger; refused outside the HV7 root (the shared arena ledger is HV4's, sealed)")
    ap.add_argument("--owner-cost-line-ref", required=True, help="reference to the owner's HV7 cost line (DESIGN section 5); no API call without it")
    a = ap.parse_args(argv)
    try:
        validate_owner_ref(a.owner_cost_line_ref)       # BEFORE anything is built: CallerModel is never constructed on a bad ref
    except Hv7Error as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    from tools import arena_paper
    from tools.arena_paper import ArenaEngine

    try:            # the halt and the clock come from the registry: a command line that disagrees with it is refused before anything is built
        pins = registered_pins(a.registry or arena_paper.REGISTRY_PATH)
    except (Hv7Error, OSError, ValueError, KeyError) as e:
        print(f"refused: registry pins ({type(e).__name__})", file=sys.stderr)
        return 2
    if any(v["entries_halt_ms"] != a.entries_halt_ms or v["clock_start_ms"] != a.clock_start_ms for v in pins.values()):
        print("refused: --entries-halt-ms / --clock-start-ms differ from the registry", file=sys.stderr)
        return 2
    try:
        ledger_root = check_path(a.ledger_root or Path(a.root) / "engine-ledger", "w")
        base, led = os.path.realpath(a.root), os.path.realpath(ledger_root)
        if led == base or not led.startswith(base + os.sep):        # strictly inside --root (hv7_read refuses anything else)
            raise H.PathRefused("ledger_not_inside_root")
    except (Hv7Error, H.PathRefused) as e:
        print(f"refused: ledger root ({type(e).__name__})", file=sys.stderr)
        return 2
    guard = Hv7Guard(a.root)
    if _stub is not None:
        run = Hv7Runner(Hv7Config(root=a.root, frozen_sha=a.frozen_sha, clock_start_ms=a.clock_start_ms, live=False, entries_halt_ms=a.entries_halt_ms),
                        ArenaState(check_path(a.tape, "r"), start_ms=0), _stub["engine"], _stub["model"], _stub["chain"], guard)
        run.sim_now = int(_stub["start_ms"])        # the start row is stamped with the simulated instant, never the wall clock
        run.log_start()
        try:
            run.run(int(_stub["start_ms"]), int(_stub["end_ms"]))
        finally:
            run.shutdown()
        return 0
    from tools.hv6_agents import CallerModel

    try:
        engine = ArenaEngine(ledger_root, a.registry or arena_paper.REGISTRY_PATH, cfg=stack_config(a.feed_delay_slots))
        chain = live_chain_source(a.root)
        run = Hv7Runner(Hv7Config(root=a.root, frozen_sha=a.frozen_sha, clock_start_ms=a.clock_start_ms, live=True, entries_halt_ms=a.entries_halt_ms),
                        ArenaState(check_path(a.tape, "r"), start_ms=0), engine, CallerModel(guard), chain, guard)
    except Exception as e:      # noqa: BLE001 - every builder error (ChainError, ValueError, OSError, ArenaRefused, ...): the class name only, never the message
        print(f"refused: {type(e).__name__}", file=sys.stderr)
        return 2
    try:
        for arm in ARM_ORDER:           # the four HV7 books only (start_all would write a start row for every registered book of every project into this ledger)
            engine.start_record(bot_id(arm))
    except Exception as e:      # noqa: BLE001
        print(f"refused: {type(e).__name__}", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, run.request_stop)
    try:
        now = int(time.time_ns() // 1_000_000) // TICK_MS * TICK_MS
        provider = getattr(getattr(chain.chain, "rpc", None), "provider", None)
        if run.log.files("SCAN"):
            run.resume(now)             # live: the loop starts at the wall clock; the return value (a sim catch-up tick) is not used
        else:
            run.log_start(provider)
        start = now
        chain.prefetch.start()          # last in the wrapper: nothing can fail after the thread is running. Facts are fetched off the scan path, stamped before the scan instant
    except Exception as e:      # noqa: BLE001
        print(f"refused: {type(e).__name__}", file=sys.stderr)
        return 2
    try:
        run.run(start, 1 << 62)
    finally:
        chain.prefetch.stop()
        run.shutdown()
        summ = run.summary(provider)
        run.log.append("SCAN", int(time.time() * 1000), {"kind": "run_summary", **summ})   # after the last decision row; resume reads the last period row, not the last row
        print(json.dumps({"summary": summ}, sort_keys=True, separators=(",", ":")))
    return 0

if __name__ == "__main__":
    sys.exit(main())
