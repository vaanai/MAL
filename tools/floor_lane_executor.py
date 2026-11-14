"""floor_lane_executor: the FLOOR book `l1-floor-v2` (frozen sha 5e12f8f5, tools/arena_rule_l1.py) in the DEC-035 live-learning lane, as lane bot_id `floor-lane-1`.

A thin layer over tools/floor_arena_executor.FloorBridge (which stays dry-only and is unchanged except for one overridable start guard). It adds the real order port and the
DEC-035 section 2 limits, in code. The decision logic is the frozen rule's, read from the hash-chained decision log; nothing is re-decided here. tools/arena_rule_l1.py is not touched.

SWITCHES. A real send needs ALL of: config mode "live", the `--live` flag, the lane LIVE_OK file (root-owned, content exactly `floor-lane-1`), and every limit below, re-checked
immediately before EVERY buy. Any one missing runs DRY (a DryPort: logged, never sent) or refuses to start. Only the manager starts the book (DEC-035 s3).

LIMITS (DEC-035 s2; the shared ledger is the h5-lane ledger, tools/h5_lane_executor.LaneLedger, taken under its flock and read before every send)
  1 BUDGET      lane P&L <= -budget: the budget stop writes the lane STOP file and no entry goes. An entry goes only if the full-wipe lane P&L - (new stake + fee reserve per position) > -budget. Code ceiling 2.10 SOL (DEC-035 Am.6; 0.30 at s2, 0.60 Am.4, 1.20 Am.5); the live config sets the value in use.
                Open positions are marked at the last fresh quote; a mark older than mark_stale_s (<= 60) is worth 0.
  2 STAKE       FLOOR_LANE_STAKE_BOUNDS_LAMPORTS, one named constant (code ceiling 0.02 to 0.25 SOL, DEC-035 Am.6; the live config sets the stake in use). The configured stake outside it: the executor refuses to start. One entry per position.
  3 OPEN        at most LANE_MAX_OPEN (code ceiling 16; the live config sets the value in use, 8 at DEC-035 Amendment 1, 2026-11-08) across the lane, counted in the shared ledger. A full lane refuses the entry with reason `lane_full` (first come, first served).
  4 DAILY       lane P&L now minus lane P&L at the last 00:00Z <= -daily (code ceiling FLOOR_LANE_DAILY_MAX_LAMPORTS 0.50 SOL, DEC-035 Am.6; 0.10 at s2, 0.20 Am.5; the live config sets the value in use; the shared LANE_DAILY_MAX_LAMPORTS stays 0.10 for the DSL lane): no new entries until the next 00:00Z. Applied as written (marked P&L); no worst-case daily pre-send (Amendment 1).
  6 STOP        the lane STOP file (and the wallet-wide /var/lib/mal-live/STOP and HALT, which the bridge honours) is checked before every send. New entries only: open positions keep the rule's exits.
  ATA           every sell closes the token account (probe_live._start_sell builds close_token_account, checked at start); a landed sell that refunded no rent latches `ata_rent_not_refunded`.

EXITS. The frozen rule's own exits (tp +50 %, 24 h clock) are followed from the decision log and by the bridge's persisted per-order deadline; positions live in the bridge state, so a
restart resumes them. The lane STOP stops entries only. There is no stop loss (the book has none).

Nothing here says the rule makes money. FLOOR has 0 out-of-sample trades and has not cleared the promotion gate; a lane trade is a measurement, not a promotion.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import fcntl
import functools
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import arena_live_bridge as B
from tools import floor_arena_executor as F
from tools import h5_arena_executor as h5a
from tools import h5_lane_executor as H

LIVE_BUILT = True  # the single constant of the live path. True removes only the `live_not_built` refusal: a send still needs mode live + --live + LIVE_OK + every lane limit.
LANE_BOT_ID = "floor-lane-1"
BOOK_BOT_ID = F.BOT_ID  # the decision-log arm the bridge reads
FROZEN_SHA = F.FROZEN_SHA

# THE stake bounds of this book in the lane (DEC-035 s2 item 2). A later owner decision raises them here and in DEC-035, plus the test that pins them. Nothing else holds a copy.
FLOOR_LANE_STAKE_BOUNDS_LAMPORTS = (20_000_000, 250_000_000)  # floor-lane-1 only (the DSL lane has DSL_LANE_STAKE_BOUNDS_LAMPORTS). Owner step-up 0.03 -> 0.10 SOL (2026-11-10); DEC-035 Am.6 (owner, 2026-11-12): ceiling 0.10 -> 0.25 SOL; the live config still sets the stake in use
LANE_MAX_OPEN = 16  # a ceiling only; the live config sets max_open
LANE_MAX_OPEN_DEFAULT = 8  # what a lane block without max_open gets (unchanged from before the step-up)
# The bridge builds its probe executor with the dec019 profile, which CLAMPS max_open to 3, max_attempts to its pcfg value (60) and max_days to 7. Incident 2026-11-09: a real
# FLOOR entry was refused `limit:max_open` with only 3 open (the lane cap is 8). The lane's own budget, daily stop, max_open and frozen 14-day canary (floor_arena_executor) are the
# real limits, so the live port lifts the probe's counters far above anything the lane can reach. FloorLanePort.__init__ applies them to ex.limits.
LANE_PROBE_MAX_ATTEMPTS = 100_000
LANE_PROBE_MAX_DAYS = 60.0
LANE_BUDGET_MAX_LAMPORTS = 2_100_000_000  # a ceiling only (read by floor-lane-1 validation alone; the DSL lane has DSL_LANE_BUDGET_MAX_LAMPORTS); the live config sets budget_lamports. DEC-035 Am.5 (owner, 2026-11-12): 0.60 -> 1.20 SOL lifetime; Am.6: 1.20 -> 2.10 SOL
LANE_BUDGET_DEFAULT_LAMPORTS = 300_000_000  # what a lane block without budget_lamports gets (unchanged from before the step-up)
LANE_DAILY_MAX_LAMPORTS = 100_000_000  # the SHARED daily ceiling: the DSL lane (tools/forge/dsl_lane_executor.py, a pinned blob) reads it. DEC-035 Am.5 does not move it
FLOOR_LANE_DAILY_MAX_LAMPORTS = 500_000_000  # floor-lane-1 only: DEC-035 Am.5 (owner, 2026-11-12) 0.10 -> 0.20 SOL daily stop ceiling; Am.6: 0.20 -> 0.50 SOL
LANE_DAILY_DEFAULT_LAMPORTS = 100_000_000  # what a lane block without daily_loss_lamports gets (unchanged by Am.5, like LANE_BUDGET_DEFAULT_LAMPORTS)
LANE_MARK_STALE_MAX_S = 60
# probe_executor prices an open position from its pool cache only while the cache is younger than STATIC_TTL_MS (300 s). A FLOOR position is held up to 24 h, so the port re-primes the cache
# of every open mint with one full snapshot at most once per this interval (audit 2026-11-10 option A). Below STATIC_TTL_MS so the cache never lapses between two re-primes.
LANE_REPRIME_MS = 240_000
# What one position can cost beyond its stake: ATA rent, the buy fee, the sell ladder at the pinned p155 fee (F.SELL_RESERVE_LAMPORTS).
LANE_MIN_FEE_RESERVE = F.RENT_RESERVE_LAMPORTS + F.SEND_FEE_LAMPORTS + F.SELL_RESERVE_LAMPORTS
LANE_UNRESOLVED_MS = H.LANE_UNRESOLVED_MS
LANE_RECONCILE_EVERY_MS = H.LANE_RECONCILE_EVERY_MS
A3_POLL_MS = 60_000
LIVE_OK_DEFAULT = "/etc/mal-arena-live/floor-lane-1/LIVE_OK"
LATCH_NO_ATA_REFUND = "ata_rent_not_refunded"
LATCH_BUY_ERROR = "lane_buy_error"
LANE_KEYS = {"ledger_path", "stop_path", "bot_id", "stake_lamports", "max_open", "budget_lamports", "daily_loss_lamports", "fee_reserve_lamports", "mark_stale_s"}
LANE_REASON = {"lane_max_open": "lane_full"}  # the ledger's label, as this book reports it


class LaneConfigError(ValueError):
    pass


def _whole(raw: Mapping[str, Any], key: str, default: Any = None) -> int:
    v = raw.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v != int(v):
        raise LaneConfigError(f"lane.{key} must be a whole number")
    return int(v)


def parse_lane(raw: Any) -> H.LaneConfig:
    """The lane block. Config can only lower the DEC-035 maxima. A stake outside FLOOR_LANE_STAKE_BOUNDS_LAMPORTS is refused (no default stake: it must be written down)."""
    if not isinstance(raw, dict):
        raise LaneConfigError("lane must be an object")
    extra = set(raw) - LANE_KEYS
    if extra:
        raise LaneConfigError(f"lane: unknown keys {sorted(extra)}")
    for key in ("ledger_path", "stop_path"):
        if not isinstance(raw.get(key), str) or not raw[key] or not os.path.isabs(raw[key]):
            raise LaneConfigError(f"lane.{key} is required (absolute path)")
    if raw.get("bot_id", LANE_BOT_ID) != LANE_BOT_ID:
        raise LaneConfigError(f"lane.bot_id must be {LANE_BOT_ID}")
    lo, hi = FLOOR_LANE_STAKE_BOUNDS_LAMPORTS
    stake = _whole(raw, "stake_lamports")
    if not lo <= stake <= hi:
        raise LaneConfigError(f"lane.stake_lamports {stake} is outside [{lo}, {hi}]")
    max_open = _whole(raw, "max_open", LANE_MAX_OPEN_DEFAULT)
    budget = _whole(raw, "budget_lamports", LANE_BUDGET_DEFAULT_LAMPORTS)
    daily = _whole(raw, "daily_loss_lamports", LANE_DAILY_DEFAULT_LAMPORTS)
    if not 1 <= max_open <= LANE_MAX_OPEN:
        raise LaneConfigError(f"lane.max_open must be 1..{LANE_MAX_OPEN}")
    if not 1 <= budget <= LANE_BUDGET_MAX_LAMPORTS:
        raise LaneConfigError(f"lane.budget_lamports must be 1..{LANE_BUDGET_MAX_LAMPORTS}")
    if not 1 <= daily <= FLOOR_LANE_DAILY_MAX_LAMPORTS:
        raise LaneConfigError(f"lane.daily_loss_lamports must be 1..{FLOOR_LANE_DAILY_MAX_LAMPORTS}")
    reserve = _whole(raw, "fee_reserve_lamports", LANE_MIN_FEE_RESERVE)
    if reserve < LANE_MIN_FEE_RESERVE:
        raise LaneConfigError(f"lane.fee_reserve_lamports must be >= {LANE_MIN_FEE_RESERVE}")
    stale = raw.get("mark_stale_s", LANE_MARK_STALE_MAX_S)
    if isinstance(stale, bool) or not isinstance(stale, (int, float)) or not math.isfinite(stale) or not 0 < stale <= LANE_MARK_STALE_MAX_S:
        raise LaneConfigError(f"lane.mark_stale_s must be in (0, {LANE_MARK_STALE_MAX_S}]")
    return H.LaneConfig(Path(raw["ledger_path"]), Path(raw["stop_path"]), LANE_BOT_ID, stake, max_open, budget, daily, reserve, float(stale), "", False)


RPC_RPS_DEFAULT = 2.0  # == probe_executor.MAX_RPS, the limiter rate the lane has always used
RPC_RPS_MAX = 5.0  # == probe_live.LIVE_MAX_RPS (the live ceiling; a test pins the equality)


def parse_rpc_rps(raw: Mapping[str, Any]) -> float:
    """Optional top-level key `rpc_rps`: the lane RPC limiter rate. Absent = 2.0 (unchanged). Range [2.0, 5.0]; anything else is a refusal (explicit raise, python -O safe)."""
    if "rpc_rps" not in raw:
        return RPC_RPS_DEFAULT
    v = raw["rpc_rps"]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < RPC_RPS_DEFAULT or v > RPC_RPS_MAX:
        raise ValueError(f"config rpc_rps must be a number in [{RPC_RPS_DEFAULT}, {RPC_RPS_MAX}] (got {v!r})")
    return float(v)


def make_config(raw: Mapping[str, Any]) -> tuple[B.Config, H.LaneConfig, str]:
    """(bridge Config, lane config, live_ok_path). Fail closed: ValueError on any pin that does not hold. All FLOOR pins of F.make_config apply (bot arm, frozen sha, hold)."""
    mode = raw.get("mode", B.DRY)
    if mode not in (B.DRY, B.LIVE):
        raise ValueError("config mode must be 'dry' or 'live'")
    parse_rpc_rps(raw)
    if mode == B.LIVE and not LIVE_BUILT:
        raise ValueError("live_not_built")
    if mode == B.LIVE and any(raw.get(k) for k in ("stop_file", "halt_file")):
        raise ValueError("live refused: stop_file / halt_file cannot be set in live (the wallet-wide files are pinned; the lane STOP is lane.stop_path)")
    lane = parse_lane(raw.get("lane"))
    if raw.get("stake_lamports") not in (None, lane.stake_lamports):
        raise ValueError("top-level stake_lamports must be absent or equal lane.stake_lamports")
    live_ok = raw.get("live_ok_path", LIVE_OK_DEFAULT)
    if not isinstance(live_ok, str) or not os.path.isabs(live_ok):
        raise ValueError("live_ok_path must be an absolute path")
    base = F.make_config({k: v for k, v in raw.items() if k not in ("lane", "mode", "live_ok_path")} | {"stake_lamports": lane.stake_lamports})
    cfg = dataclasses.replace(
        base, mode=mode, stake_lamports=lane.stake_lamports, max_open=lane.max_open, daily_loss_lamports=lane.daily_loss_lamports, total_loss_lamports=lane.budget_lamports,
        # No stop loss: a whole-position loss is a legal outcome of this book and must not latch the book. Only a loss beyond stake + fee reserve is an anomaly.
        per_trade_loss_lamports=lane.stake_lamports + lane.fee_reserve_lamports)
    return cfg, lane, live_ok


class FloorLaneLedger(H.LaneLedger):
    """The shared lane ledger with this book's stake bounds (the one constant above) in place of the H5 lane's copy."""

    _marks: dict[str, tuple[int, int]] = {}

    def check(self, marks: dict[str, tuple[int, int]], now_ms: int, stake: int) -> str | None:
        self._marks = marks  # evaluate() needs the marks per position for the cost-capped pre-send (the view only has the sum)
        return super().check(marks, now_ms, stake)

    def reserve(self, mint: str, signature: str, marks: dict[str, tuple[int, int]], now_ms: int, stake: int, pool_gap_waived: bool = False) -> str | None:
        self._marks = marks
        return super().reserve(mint, signature, marks, now_ms, stake, pool_gap_waived)

    def _capped_value(self, view: H.LaneView, now_ms: int) -> int:
        """Sum of min(fresh mark, stake) over this bot's filled open positions; a stale or missing mark counts 0 (as in lane_fold). Other bots' positions have no mark here and count 0."""
        c = self.cfg
        value = 0
        for key, p in view.open.items():
            owner, mint = key.split(":", 1)
            mk = self._marks.get(mint) if owner == c.bot_id else None
            if p["filled"] and mk is not None and 0 <= now_ms - mk[1] <= c.mark_stale_s * 1000:
                value += min(int(mk[0]), int(p["stake"]))
        return value

    def capped_pnl(self, view: H.LaneView, now_ms: int) -> int:
        """Lane P&L with every open filled position of this bot counted at min(fresh mark, stake): unrealised gains never add headroom, losses still count (Amendment 1)."""
        return view.cash + self._capped_value(view, now_ms)

    def wipe_floor(self, view: H.LaneView, now_ms: int) -> int:
        """Lane P&L if every open position were worth 0: capped_pnl minus the capped mark values it added (= view.cash), minus the full stake of every UNFILLED open intent (any bot).
        Filled positions' buys are already in cash (the fill row carries sol_out); a send_intent row carries sol_out 0 (h5_lane_executor.py:354-355), so an unfilled intent is charged at stake.
        DEC-035 Amendment 3: the item 1 pre-send is this full-wipe bound. open_stakes must NOT be subtracted for filled positions: that charged each open loss twice."""
        unfilled = sum(int(p["stake"]) for p in view.open.values() if not p["filled"])
        return self.capped_pnl(view, now_ms) - self._capped_value(view, now_ms) - unfilled

    def evaluate(self, view: H.LaneView, now_ms: int, stake: int, n_pending_extra: int = 0) -> str | None:
        c = self.cfg
        lo, hi = FLOOR_LANE_STAKE_BOUNDS_LAMPORTS
        if not lo <= stake <= hi:
            return "lane_stake_out_of_bounds"
        if view.pnl <= -c.budget_lamports:
            return "lane_budget_stop"
        base = view.snaps.get(H.day_of(now_ms))
        if base is not None and (view.pnl - base[0] <= -c.daily_loss_lamports or view.cost - base[1] <= -c.daily_loss_lamports):
            return "lane_daily_stop"
        if len(view.open) >= c.max_open:
            return "lane_max_open"
        reserve = c.fee_reserve_lamports * (len(view.open) + 1)
        if self.wipe_floor(view, now_ms) - (stake + reserve) <= -c.budget_lamports:  # full-wipe bound (Amendment 3): cash already holds the open buys; only the new stake + reserve are added
            return "lane_budget_presend"
        # DEC-035 Amendment 1: no worst-case daily pre-send for floor-lane-1 (h5_lane_executor.LaneLedger.evaluate has one; that file is byte-pinned and unchanged). The marked daily stop above and the item 1 budget pre-send stay.
        return None


def sell_closes_ata(path: Path | None = None) -> bool:
    """True if probe_live.LiveExecutor._start_sell builds a close_token_account instruction (source check; imports nothing from the solana stack)."""
    p = path or Path(__file__).with_name("probe_live.py")
    tree = ast.parse(p.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_start_sell":
            return any(isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "close_token_account" for n in ast.walk(node))
    return False


def live_ok_problem(path: str, root_checks: bool = True) -> str | None:
    why = B.root_ok_problem(Path(path)) if root_checks else (None if Path(path).exists() else "missing")
    if why:
        return "live_ok_" + why
    try:
        data = Path(path).read_text()
    except OSError:
        return "live_ok_unreadable"
    return None if data.strip() == LANE_BOT_ID else "live_ok_other_book"


# --- the live port: fill rows and the pre-buy gate ------------------------------------------------------------------------------------------------

_EVENT_KIND = {"buy_landed": "buy", "buy_failed": "buy", "sell_landed": "sell", "sell_failed": "sell"}


class FloorLanePort(B.OrderPort):
    """Wraps the bridge's probe_live port (B.make_live_port). Refuses every buy unless `pre_buy(order)` returns None, called immediately before the executor is touched (fail closed
    without it); attaches the executor's own fill row to each event (`_row`) so the lane ledger books real cash. Exits are never gated."""

    name = "live"
    live = True

    def __init__(self, inner: B.OrderPort, cfg: B.Config, pre_buy: Callable[[Mapping[str, Any]], str | None] | None = None, on_error: Callable[[str, str], None] | None = None) -> None:
        if not LIVE_BUILT or cfg.mode != B.LIVE or not getattr(inner, "live", False) or getattr(inner, "ex", None) is None:
            raise SystemExit("floor lane port refused: needs LIVE_BUILT, a live config and a live inner port with an executor")
        self.inner, self.cfg, self.pre_buy, self.on_error = inner, cfg, pre_buy, on_error
        self._rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._reprime_at: dict[str, int] = {}
        ex = inner.ex
        orig = ex._log

        def _log(kind: str, mint: str, **kw: Any) -> None:
            if kind in ("buy", "sell"):
                self._rows.setdefault((kind, mint), []).append(dict(kw))
            orig(kind, mint, **kw)

        ex._log = _log
        # Lift the probe dec019 clamps (max_open 3, max_attempts 60, max_days 7); see LANE_PROBE_MAX_ATTEMPTS. The probe Limits also clamp size_lamports to 50,000,000 and loss_cap to
        # 350,000,000 (DEC019_MAX); a lane stake above 0.05 SOL would then be refused `gate:stake_sent_differs`, and a budget above 0.35 SOL would stop at 0.35. Both are set to the
        # lane's own values (identical to the clamped values for stake 30,000,000 / budget 300,000,000). end_ms stays as the bridge set it.
        object.__setattr__(ex.limits, "size_lamports", int(cfg.stake_lamports))
        object.__setattr__(ex.limits, "loss_cap_lamports", int(cfg.total_loss_lamports))
        object.__setattr__(ex.limits, "max_open", int(cfg.max_open))
        object.__setattr__(ex.limits, "max_attempts", LANE_PROBE_MAX_ATTEMPTS)
        object.__setattr__(ex.limits, "max_days", LANE_PROBE_MAX_DAYS)
        object.__setattr__(ex.limits, "priority_lamports", F.PRIORITY_LAMPORTS)  # the book's fee profile p155 (DEC-031 Am.8), not the bridge's 500,000 default

    def mint_for_pool(self, pool: str, meta: Mapping[str, Any]) -> str | None:
        return self.inner.mint_for_pool(pool, meta)

    def universe_problem(self, pool: str, mint: str) -> str | None:
        return self.inner.universe_problem(pool, mint)

    def mint_screen_problem(self, mint: str) -> str | None:
        return self.inner.mint_screen_problem(mint)

    def balance(self) -> int | None:
        return self.inner.balance()

    def buy(self, order: Mapping[str, Any]) -> tuple[bool, str | None]:
        if self.pre_buy is None:
            return False, "gate:no_pre_buy_check"
        try:
            why = self.pre_buy(order)
        except Exception:  # noqa: BLE001 - deny on error and latch, so a restarted unit does not keep buying after whatever raised
            self._error("pre_buy raised")
            return False, "gate:pre_buy_error"
        if why is not None:
            return False, "gate:" + str(why)
        sent = getattr(getattr(self.inner.ex, "limits", None), "size_lamports", None)
        if sent is not None and sent != order.get("stake_lamports"):
            return False, "gate:stake_sent_differs"
        try:
            return self.inner.buy(order)
        except BaseException as exc:  # a tx may already be pending in the executor state: latch, re-raise (the write-ahead order and the ledger intent stay for reconcile)
            self._error(f"inner.buy raised {type(exc).__name__}")
            raise

    def _error(self, why: str) -> None:
        if self.on_error is not None:
            try:
                self.on_error(LATCH_BUY_ERROR, why)
            except Exception:  # noqa: BLE001
                pass

    def sell(self, order: Mapping[str, Any], reason: str) -> tuple[bool, str | None]:
        return self.inner.sell(order, reason)

    def holdings(self) -> dict[str, str] | None:
        return self.inner.holdings()

    def warm(self, mints: list[str]) -> None:
        self._reprime_open(mints)
        self.inner.warm(mints)

    def _reprime_open(self, mints: list[str]) -> None:
        """Keep each open position's pool cache fresh (existing fetch/parse path `ex._snapshot`, V from the pool account), at most one fetch per mint per LANE_REPRIME_MS, success or not.
        A failed fetch leaves the cache and the mark stale (a stale mark counts 0, as before) and is logged by label only. Never raises; touches no order and no entry decision. Side effect (DEC-035 Am.4 item 4): a sell later than ~300 s after entry is built from this warm snapshot (min_out included) instead of a full snapshot fetched at exit."""
        ex = self.inner.ex
        cache, state = getattr(ex, "_pool_cache", None), getattr(ex, "state", None)
        if cache is None or state is None or not getattr(ex, "fast_exit", False):
            return
        now = ex.now_ms()
        for m in mints:
            if m not in state.open or m in state.pending:
                self._reprime_at.pop(m, None)
                continue
            last = self._reprime_at.get(m)
            if last is not None and 0 <= now - last < LANE_REPRIME_MS:
                continue
            cached = cache.get(m)
            if cached is not None and 0 <= now - cached[2] < LANE_REPRIME_MS:
                continue
            self._reprime_at[m] = now
            try:
                snap, _pool, err = ex._snapshot(m)
            except (Exception, SystemExit) as exc:  # label only
                snap, err = None, type(exc).__name__
            if snap is None:
                print(f"floor_lane_executor ALERT mark_reprime_failed mint={m} label={err}", flush=True)

    def position_return(self, mint: str) -> float | None:
        return self.inner.position_return(mint)

    def drain_ledger(self) -> list[tuple[str, str, dict[str, Any]]]:
        return self.inner.drain_ledger()

    def poll(self) -> list[dict[str, Any]]:
        out = []
        for ev in self.inner.poll():
            kind = _EVENT_KIND.get(ev.get("event"))
            q = self._rows.get((kind, ev.get("mint"))) if kind else None
            row = q.pop(0) if q else None
            out.append({**ev, "_row": row} if row is not None else dict(ev))
        return out


def _i(v: Any) -> int:
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else 0


def _fee_parts(fee: int) -> dict[str, int]:
    return {"fee_network": min(fee, F.BASE_FEE_LAMPORTS), "fee_priority": max(0, fee - F.BASE_FEE_LAMPORTS)}


def take_bot_lock(lane: "H.LaneLedger") -> Any:
    """One process per lane bot_id: an exclusive flock on <ledger>.bot.floor-lane-1.lock, held for the life of the process. A second unit on the same ledger would reconcile the first
    one's open positions as orphans. Taken before the port, the state or anything else is built."""
    f = open(f"{lane.path}.bot.{LANE_BOT_ID}.lock", "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise SystemExit(f"floor lane start refused: another process holds bot_id {LANE_BOT_ID!r} on this ledger") from None
    return f


# --- the bridge --------------------------------------------------------------------------------------------------------------------------------------


# DEC-035 Amendment 8: these A3 halt flags concern the H5 BOOST books only, not rule l1-floor-v2 (used only when config a3_floor_flag_list is true).
FLOOR_A3_IGNORED_FLAGS = ("boost_share_low", "boost_last_slice_early", "boost_budget_or_slices_changed")


class FloorLaneBridge(F.FloorBridge):
    """FloorBridge plus the lane. With a DRY config it adds nothing (no ledger is read or written). With a live config the shared lane ledger is read under its lock before every send."""

    def __init__(self, cfg: B.Config, port: B.OrderPort, lane_cfg: H.LaneConfig, *, now_ms: Callable[[], int] | None = None, live_ok_path: str | Path = LIVE_OK_DEFAULT,
                 tier_path: Path = B.TIER_PATH, root_checks: bool = True, close_blocked: Callable[[str], bool | None] | None = None, dry_replay_caps: bool = False, a3_record_file: str = "", bot_lock: Any = None, a3_floor_flag_list: bool = False) -> None:
        self.lane_cfg = lane_cfg
        self.lane: FloorLaneLedger | None = None
        self._lane_bot_lock: Any = None
        self._lane_marks: dict[str, tuple[int, int]] = {}
        self._lane_orphans: set[str] = set()
        self._lane_recon_ms = 0
        self.lane_live_ok_path = str(live_ok_path)
        self.lane_root_checks = root_checks
        live = cfg.mode == B.LIVE
        self.a3_record_file = a3_record_file
        self.a3_floor_flag_list = bool(a3_floor_flag_list)
        self._a3_ignored: tuple[str, ...] = ()
        self._a3_pause: str | None = None
        self._a3_polled_ms: int | None = None
        if live:  # the per-bot lock comes BEFORE the parent loads, reconciles or saves any state: a second process is refused before it can write
            self.lane = FloorLaneLedger(lane_cfg)
            self._lane_bot_lock = bot_lock if bot_lock is not None else take_bot_lock(self.lane)
        try:
            super().__init__(cfg, port, now_ms=now_ms, live_ok_path=Path(live_ok_path), tier_path=tier_path, root_checks=root_checks, close_blocked=close_blocked, dry_replay_caps=dry_replay_caps)
        except BaseException:
            self.release_lane_lock()
            raise
        if live:
            assert isinstance(port, FloorLanePort)
            port.pre_buy = self._lane_pre_buy
            port.on_error = self._latch
            self._lane_record("start", "", code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), frozen_sha=FROZEN_SHA, book=BOOK_BOT_ID,
                              lane={"bot_id": LANE_BOT_ID, "stake_lamports": lane_cfg.stake_lamports, "max_open": lane_cfg.max_open, "budget_lamports": lane_cfg.budget_lamports,
                                    "daily_loss_lamports": lane_cfg.daily_loss_lamports, "fee_reserve_lamports": lane_cfg.fee_reserve_lamports, "mark_stale_s": lane_cfg.mark_stale_s},
                              **({"a3_floor_flag_list": True} if self.a3_floor_flag_list else {}))
            self._lane_reconcile(self.now_ms(), force=True)

    def _start_guard(self, cfg: B.Config, port: B.OrderPort) -> None:
        lo, hi = FLOOR_LANE_STAKE_BOUNDS_LAMPORTS
        if not lo <= self.lane_cfg.stake_lamports <= hi or cfg.stake_lamports != self.lane_cfg.stake_lamports:
            raise SystemExit(f"floor lane start refused: stake {cfg.stake_lamports} / lane stake {self.lane_cfg.stake_lamports} outside [{lo}, {hi}]")
        if cfg.max_open > LANE_MAX_OPEN or cfg.max_open != self.lane_cfg.max_open:
            raise SystemExit("floor lane start refused: max_open")
        if cfg.mode == B.LIVE:
            if not (LIVE_BUILT and port.live and isinstance(port, FloorLanePort)):
                raise SystemExit("floor lane start refused: a live config needs LIVE_BUILT and a FloorLanePort")
            if not sell_closes_ata():
                raise SystemExit("floor lane start refused: the sell does not close the token ATA (rent would be stranded)")
        elif port.live:
            raise SystemExit("floor lane start refused: a DRY config never takes a live port")

    def release_lane_lock(self) -> None:
        if self._lane_bot_lock is not None:
            self._lane_bot_lock.close()
            self._lane_bot_lock = None

    def _log(self, kind: str, **kw: Any) -> None:
        super()._log(kind, lane_bot_id=LANE_BOT_ID, **kw)

    # -- limits: the lane's, not the dry C0 table
    def tier_limits(self) -> tuple[str, dict[str, int], str | None]:
        if self.dry_replay_caps:
            return super().tier_limits()
        c = self.cfg
        return "LANE", {"stake_lamports": c.stake_lamports, "max_open": c.max_open, "max_trades_per_day": min(c.max_trades_per_day, F.C0["max_trades_per_day"]),
                        "daily_loss_lamports": c.daily_loss_lamports, "total_loss_lamports": c.total_loss_lamports}, None

    def effective_live(self) -> tuple[bool, str | None]:
        if self.cfg.mode != B.LIVE:
            return False, "mode_dry"
        why = live_ok_problem(self.lane_live_ok_path, self.lane_root_checks)
        return (False, why) if why else (True, None)

    # -- entry gates
    def a3_pause_tick(self, now: int) -> None:
        """DEC-035 s4: on a pump / PumpSwap redeploy flag (A3 pins_changed or program_changed), a stale, future or unreadable record, new entries pause. Not a latch: it clears
        when the record clears (the manager re-pins first). Exits keep running; this is read in the entry check and again before every send."""
        if self.lane is None or not self.a3_record_file:
            return
        if self._a3_polled_ms is not None and 0 <= now - self._a3_polled_ms < A3_POLL_MS:
            return
        self._a3_polled_ms = now
        try:
            flags, problem = h5a.a3_record_problem(Path(self.a3_record_file), now)
        except Exception:  # noqa: BLE001 - fail closed
            flags, problem = [], "a3_record_error"
        ignored: list[str] = []
        if self.a3_floor_flag_list and not problem:  # problem strings always pause; only the BOOST-book flags are dropped
            ignored = sorted(f for f in flags if f in FLOOR_A3_IGNORED_FLAGS)
            flags = [f for f in flags if f not in FLOOR_A3_IGNORED_FLAGS]
        new = problem or ("a3_flags:" + ",".join(flags) if flags else None)
        if new != self._a3_pause:
            self._log("a3_pause" if new else "a3_resume", why=new, **({"ignored": ignored} if ignored else {}))
            if self.a3_floor_flag_list:
                self._a3_ignored = tuple(ignored)
        elif self.a3_floor_flag_list and tuple(ignored) != self._a3_ignored:
            self._a3_ignored = tuple(ignored)
            self._log("a3_ignored", ignored=ignored)
        self._a3_pause = new

    def _lane_check(self, now: int, stake: int) -> str | None:
        if self.lane is None:
            return None
        self.a3_pause_tick(now)
        if self._a3_pause:
            return "a3_pause:" + self._a3_pause
        if self._lane_orphans:
            return "lane_unresolved_intent"
        why = self.lane.check(self._lane_marks, now, stake)
        if why == "lane_budget_stop":  # DEC-035 s2 item 1: the budget stop writes the lane STOP file (it survives restarts; only the manager removes it)
            self.lane.write_stop(f"lane_budget_stop bot_id={LANE_BOT_ID}", now)
        return LANE_REASON.get(why, why) if why else None

    def entry_refusal(self, row: Mapping[str, Any], now: int, lim: dict[str, int], scope: bool = True) -> str | None:
        lane_why = self._lane_check(now, lim["stake_lamports"]) if (self.lane is not None and scope and not self.dry_replay_caps) else None
        if lane_why == "lane_full":
            return lane_why  # the lane is full whatever else is wrong: report it as such
        why = super().entry_refusal(row, now, lim, scope)
        return why or lane_why

    def _lane_pre_buy(self, order: Mapping[str, Any]) -> str | None:
        """Called by the port immediately before the executor is touched. STOP, latches, files, then the lane limits under the ledger lock with the intent row fsynced (write-ahead)."""
        assert self.lane is not None
        if self.lane.stop_present():
            return "lane_stop_file"
        self._a3_polled_ms = None  # re-read the record right now, not on the 60 s cadence
        self.a3_pause_tick(self.now_ms())
        if self._a3_pause:
            return "a3_pause:" + self._a3_pause
        halt, stop = self._files()
        if halt:
            return "halt_file"
        if stop:
            return "stop_file"
        if self.state.latches:
            return "latched:" + sorted(self.state.latches)[0]
        stake = order.get("stake_lamports")
        lo, hi = FLOOR_LANE_STAKE_BOUNDS_LAMPORTS
        if not isinstance(stake, int) or isinstance(stake, bool) or not lo <= stake <= hi or stake != self.cfg.stake_lamports:
            return "lane_stake_out_of_bounds"
        why = self.lane.reserve(str(order["mint"]), f"intent:{order['order_id']}", self._lane_marks, self.now_ms(), stake)
        return LANE_REASON.get(why, why) if why else None

    def handle_entry(self, row: dict[str, Any]) -> None:
        super().handle_entry(row)
        o = self.state.orders.get(str(row.get("order_id")))
        if self.lane is not None and o is not None and o["status"] == "refused" and o.get("send_ms") is not None:
            # the port refused after the write-ahead (a lane race, a gate or an executor skip, whatever the label): nothing was sent. Close the intent if one was written, release the mint.
            if o["mint"] in self.state.bought:
                self.state.bought.remove(o["mint"])
            self._lane_writeoff_unsent(o)
            self._save()

    # -- books
    def _lane_record(self, ev: str, mint: str, **kw: Any) -> None:
        if self.lane is None:
            return
        try:
            self.lane.record(ev, mint, self._lane_marks, self.now_ms(), **kw)
        except Exception as exc:  # noqa: BLE001
            print(f"floor_lane_executor ALERT lane_ledger_write_failed ev={ev} label={type(exc).__name__}", flush=True)
            if not self.lane.backlog_pending():  # not even the backlog took it: stop entries by file (survives a restart)
                try:
                    self.lane.write_stop(f"lane_ledger_write_failed ev={ev}", self.now_ms())
                except Exception:  # noqa: BLE001
                    pass
        if ev in ("exit", "writeoff"):
            self._lane_marks.pop(mint, None)

    def _lane_writeoff_unsent(self, o: Mapping[str, Any]) -> None:
        try:
            with self.lane.locked():  # type: ignore[union-attr]
                view = H.lane_fold(self.lane.read(), self._lane_marks, self.now_ms(), self.lane_cfg.mark_stale_s, LANE_BOT_ID)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            return
        held = view.open.get(f"{LANE_BOT_ID}:{o['mint']}")
        if held is not None and not held["filled"] and held["signature"] == f"intent:{o['order_id']}":
            self._lane_record("writeoff", o["mint"], reason="not_sent", signature=held["signature"])

    def apply_events(self, events: list[dict[str, Any]]) -> None:
        if self.lane is not None:
            by_mint = {o["mint"]: k for k, o in self.state.orders.items()}
            for ev in events:
                oid = ev.get("order_id") or by_mint.get(ev.get("mint"))
                o = self.state.orders.get(oid) if oid else None
                if o is not None:
                    self._lane_book(ev, o)
        super().apply_events([{k: v for k, v in ev.items() if k != "_row"} for ev in events])

    def _lane_book(self, ev: Mapping[str, Any], o: dict[str, Any]) -> None:
        kind, mint, row = ev.get("event"), str(o["mint"]), ev.get("_row") or {}
        sig = row.get("signature")
        fee = _i(row.get("fee_lamports"))
        if kind == "buy_landed":
            est = not row
            out = _i(row.get("sol_spent_lamports")) if row else int(o["stake_lamports"]) + F.RENT_RESERVE_LAMPORTS + F.SEND_FEE_LAMPORTS
            o["lane_buy_out"] = out
            self._lane_record("fill", mint, ok=True, sol_out=out, signature=sig, estimated=est, rent_paid_lamports=_i(row.get("rent_charged_lamports")), **_fee_parts(fee))
        elif kind == "buy_failed":
            pnl = _i(ev.get("pnl_lamports"))
            if ev.get("label") == "expired" or (pnl == 0 and not row.get("landed")):
                self._lane_record("writeoff", mint, reason="buy_expired", signature=sig)  # an unlanded buy: closed, no SOL moved
            else:
                self._lane_record("fill", mint, ok=False, sol_out=-pnl, signature=sig, **_fee_parts(fee))
        elif kind == "sell_landed":
            refunded = _i(row.get("rent_refunded_lamports"))
            if row:
                net, est = _i(row.get("sell_net_lamports")), False
                sol_in, sol_out = net + fee, fee  # gross proceeds plus the rent refund; the sell's own fee goes out in the same row
            else:
                est, refunded = True, 0
                sol_in, sol_out = max(0, _i(ev.get("pnl_lamports")) + _i(o.get("lane_buy_out"))), 0
            if refunded <= 0:
                self._latch(LATCH_NO_ATA_REFUND, f"{o['order_id']} sell landed with no token-account rent refunded")  # stops new entries; exits continue
            self._lane_record("exit", mint, sol_in=sol_in, sol_out=sol_out, rent_refunded=refunded, signature=sig, estimated=est, **_fee_parts(fee))
        elif kind == "sell_failed":
            cost = _i(row.get("cost_lamports"))
            if cost > 0:  # a landed failed sell paid its fee; the position stays open
                self._lane_record("fee", mint, sol_out=cost, reason="sell_failed", signature=sig, **_fee_parts(fee))

    # -- marks, backlog, unresolved intents
    def tick(self) -> int:
        n = super().tick()
        if self.lane is not None:
            self._lane_update_marks()
            try:
                self.lane.drain()
            except Exception as exc:  # noqa: BLE001
                print(f"floor_lane_executor ALERT lane_backlog_drain_failed label={type(exc).__name__}", flush=True)
            self._lane_reconcile(self.now_ms())
        return n

    def _lane_update_marks(self) -> None:
        """Mark = stake x (1 + return) from the executor's fresh warm quote; a position with no fresh quote keeps its old mark, which goes stale after mark_stale_s and then counts 0."""
        now = self.now_ms()
        for o in self.state.orders.values():
            if o["status"] != "open" or not o["live"]:
                continue
            ret = self.port.position_return(o["mint"])
            if isinstance(ret, (int, float)) and not isinstance(ret, bool) and math.isfinite(ret):
                self._lane_marks[o["mint"]] = (max(0, int(o["stake_lamports"] * (1.0 + ret))), now)

    def _lane_reconcile(self, now: int, force: bool = False) -> None:
        """Ledger against bridge state. Unfilled intent whose order refused: closed at 0 (nothing sent). Unfilled intent whose order landed (the fill event was lost in a crash): booked at a
        flagged estimate of its cost. Filled position whose order is closed with no exit row: written off at 0 proceeds (cash already out). An intent with no order at all stays open at its
        full stake and blocks entries until LANE_UNRESOLVED_MS, then is written off at its full estimated cost."""
        assert self.lane is not None
        if not force and now - self._lane_recon_ms < LANE_RECONCILE_EVERY_MS:
            return
        if self.lane.backlog_pending():
            return
        self._lane_recon_ms = now
        try:
            with self.lane.locked():
                view = H.lane_fold(self.lane.read(), self._lane_marks, now, self.lane_cfg.mark_stale_s, LANE_BOT_ID)
        except Exception:  # noqa: BLE001
            self._lane_orphans = {"?"}  # an unreadable ledger blocks entries
            return
        mine = LANE_BOT_ID + ":"
        orphans: set[str] = set()
        for key, p in view.open.items():
            if not key.startswith(mine):
                continue
            mint = key[len(mine):]
            sig = p.get("signature")
            o = self.state.orders.get(sig[len("intent:"):]) if isinstance(sig, str) and sig.startswith("intent:") else None  # by intent id, never by mint (a mint can be re-entered)
            full = p["stake"] + F.RENT_RESERVE_LAMPORTS + F.SEND_FEE_LAMPORTS
            if o is None:
                if now - p["ts_ms"] >= LANE_UNRESOLVED_MS:
                    print(f"floor_lane_executor ALERT lane_intent_written_off mint={mint}", flush=True)
                    self._lane_record("writeoff", mint, reason="unresolved_intent_10min", sol_out=0 if p["filled"] else full, signature=p["signature"], estimated=True)
                else:
                    orphans.add(mint)
                continue
            if o["status"] == "refused" and not p["filled"]:
                self._lane_record("writeoff", mint, reason="not_sent", signature=p["signature"])  # the order never reached the chain
                continue
            if not p["filled"] and o["status"] in ("open", "sell_sending", "sell_sent", "closed"):
                self._lane_record("fill", mint, ok=True, sol_out=full, signature=p["signature"], estimated=True, reason="fill_event_lost")
                o["lane_buy_out"] = full
            if o["status"] == "closed":  # closed in the bridge with no exit row in the ledger (the sell event was lost): cash is already out, proceeds unknown, counted 0
                self._lane_record("writeoff", mint, reason="closed_unrecorded", signature=p["signature"], estimated=True)
        self._lane_orphans = orphans


# --- run ------------------------------------------------------------------------------------------------------------------------------------------------


def build_live_port(cfg: B.Config, rpc_rps: float = RPC_RPS_DEFAULT) -> FloorLanePort:
    """The LIVE port (only `run --live` with a live config reaches this). The key comes ONLY from the systemd credential (probe_live.load_probe_key reads $CREDENTIALS_DIRECTORY; no path
    argument); no Claude session reads it. Imports are local: a DRY run never imports the solana stack."""
    from tools import probe_executor as pe
    from tools import probe_live as pl
    from tools import pumpswap_simulate as sim

    if not (os.environ.get("HELIUS_API_KEY") or "").strip():
        raise SystemExit("floor lane live start refused: rpc_key_missing")
    pl.harden_process()
    kp = pl.load_probe_key()
    rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(None, None, use_env_file=False)), rps=float(rpc_rps), max_rps=pl.LIVE_MAX_RPS)
    return FloorLanePort(B.make_live_port(cfg, rpc, kp), cfg)


def start_refusal(cfg: B.Config, live_ok_path: str, *, live: bool, root_checks: bool = True) -> str | None:
    if live != (cfg.mode == B.LIVE):
        return "live_flag_mode_mismatch"
    if live:
        if not LIVE_BUILT:
            return "live_not_built"
        why = live_ok_problem(live_ok_path, root_checks)
        if why:
            return why
        if not sell_closes_ata():
            return "sell_does_not_close_ata"
    return None


def run(raw: Mapping[str, Any], *, live_flag: bool, once: bool = False, root_checks: bool = True, port_factory: Callable[[B.Config], B.OrderPort] | None = None,
        now_ms: Callable[[], int] | None = None, sleep: Callable[[float], None] | None = None) -> int:
    """DRY unless the config says mode live AND `live_flag`; one without the other does not start (never half-live, never a silent DRY)."""
    mode = raw.get("mode", B.DRY)
    if mode not in (B.DRY, B.LIVE) or (mode == B.LIVE) != live_flag:
        print("floor_lane_executor ALERT startup_refused live_flag_mode_mismatch", flush=True)
        return 2
    try:
        cfg, lane, live_ok = make_config(raw)
    except (ValueError, TypeError) as exc:
        print(f"floor_lane_executor ALERT startup_refused {exc}", flush=True)
        return 2
    a3_file = str(raw.get("a3_record_file") or "")
    a3_fl = raw.get("a3_floor_flag_list", False) is True
    rpc_rps = parse_rpc_rps(raw)  # already validated by make_config
    if live_flag and not (a3_file and os.path.isabs(a3_file)):
        print("floor_lane_executor ALERT startup_refused a3_record_file_missing", flush=True)
        return 2
    why = start_refusal(cfg, live_ok, live=live_flag, root_checks=root_checks)
    if why:
        print(f"floor_lane_executor ALERT startup_refused {why}", flush=True)
        return 2
    lock = None
    if live_flag:  # before the port is built (a second BridgeExecutor on the same probe state) and before any state is loaded
        try:
            lock = take_bot_lock(FloorLaneLedger(lane))
        except SystemExit as exc:
            print(f"floor_lane_executor ALERT startup_refused {exc}", flush=True)
            return 2
    port = (port_factory or functools.partial(build_live_port, rpc_rps=rpc_rps))(cfg) if live_flag else B.DryPort(cfg.dry_balance_lamports)
    br = FloorLaneBridge(cfg, port, lane, now_ms=now_ms, live_ok_path=live_ok, root_checks=root_checks, a3_record_file=a3_file, bot_lock=lock, a3_floor_flag_list=a3_fl)
    print(f"floor_lane_executor mode={cfg.mode} lane_bot={LANE_BOT_ID} book={cfg.bot_id} stake={cfg.stake_lamports} rpc_rps={rpc_rps}", flush=True)
    kw = {"sleep": sleep} if sleep is not None else {}
    br.run(once=once, **kw)
    return 0


def check(raw: Mapping[str, Any], *, live: bool = False, root_checks: bool = True) -> dict[str, Any]:
    try:
        cfg, _lane, live_ok = make_config(raw)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "problem": f"config: {exc}"}
    why = start_refusal(cfg, live_ok, live=live, root_checks=root_checks)
    return {"ok": why is None, "problem": why}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FLOOR lane executor (floor-lane-1). `run` is DRY unless config mode is live AND --live is given; `check` validates only.")
    ap.add_argument("cmd", choices=("run", "check"))
    ap.add_argument("--config", required=True)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    raw = json.loads(Path(a.config).read_text())
    if a.cmd == "check":
        out = check(raw, live=a.live)
        print(json.dumps(out, sort_keys=True))
        return 0 if out["ok"] else 2
    return run(raw, live_flag=a.live, once=a.once)


if __name__ == "__main__":
    sys.exit(main())
