"""dsl_lane_executor: follows the decision rows of ONE FORGE DSL spec (tools/forge/dsl_lane_host.py) and trades them in the DEC-035 live-learning lane, one lane bot_id per spec.

FORGE-SPEC Part C, PR-B. A sibling of tools/floor_lane_executor.py: the same base classes (tools/arena_live_bridge.Bridge, tools/h5_lane_executor.LaneLedger), the same live port
(floor_lane_executor.FloorLanePort), the same DEC-035 section 2 limits, ATA close, STOP/HALT, A3 redeploy pause and LIVE_OK switch. Only the pins, the exit set, the backstop and the
bot_id are the DSL's. Nothing is re-decided here: the entry and exit rows are the host's; nothing says the spec makes money.

PINS (a mismatch refuses; nothing is sent)
  bot_id        config bot_id = lane bot_id = the decision-log arm (`decisions/<bot_id>/`); every row's `arm` must equal it. Not floor-lane-1, h5-lane or any hv4- id.
  frozen_sha    config frozen_sha (40 hex, = the host's checkout HEAD) must equal the start row's frozen_sha (the base bridge latches `frozen_sha_mismatch` otherwise).
  spec_sha256   config spec_sha256 (64 hex) must equal the start row's spec_sha256; a mismatch latches `spec_sha_mismatch` (no entry after it). An entry's meta.spec_sha256, if present, must match too.
  meta.rule     every entry row must carry meta.rule == "dsl" and meta.max_hold_s == the config max_hold_s (the spec's exit.max_hold_s).

EXITS. Every reason the host emits is followed: tp, sl, trail, time, migrated, restart_close (the host's close of an entry left open across its own restart; an exit row for an order whose buy has not landed waits for the landing, then sells). An unknown reason
is not followed (counted `exit_not_followed`). BACKSTOP: if the host is silent, an open position is sold at decision_ms + meta.max_hold_s + DSL_BACKSTOP_MARGIN_MS as reason `time_backstop`.
The executor does no price tracking of its own (no stop loss besides the host's), so a live sell lags the host's trigger by the host's detection time plus the sell build.

STAKE. From config (lane.stake_lamports) inside DSL_LANE_STAKE_BOUNDS_LAMPORTS (0.02 to 0.03 SOL). The row's stake_sol is NOT used for sizing; it is logged as paper_stake_sol on the order and the buy row.

LIMITS. The shared lane ledger and lock, the lane STOP, the total budget presend, the daily stop and the lane max_open come from tools/floor_lane_executor (FloorLaneLedger and its constants),
read at call time, so the lane rules move together. Only the stake-bounds constant is this book's own.
"""

from __future__ import annotations

import argparse
import dataclasses
import fcntl
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import arena_live_bridge as B
from tools import floor_arena_executor as F
from tools import floor_lane_executor as X
from tools import h5_arena_executor as h5a
from tools import h5_lane_executor as H

LIVE_BUILT = True  # as floor_lane_executor: removes only the `live_not_built` refusal; a send still needs mode live + --live + LIVE_OK + every lane limit
RESERVED_BOT_IDS = ("floor-lane-1", "h5-lane", "l1-floor-v2")
DSL_LANE_STAKE_BOUNDS_LAMPORTS = (20_000_000, 30_000_000)  # DEC-035 s2 item 2, this book's one named constant. A later owner decision raises it here, in DEC-035 and in the test that pins it.
DSL_RULE = "dsl"
FOLLOWED_EXIT_REASONS = ("tp", "sl", "trail", "time", "migrated", "restart_close")  # tools/forge/dsl_lane_host.py: the evaluator's REASONS plus no_fill
REASON_BACKSTOP = "time_backstop"
# The entry row is written at the host's fill (its ms = the fill instant); the host's time exit executes at the first grid point >= max_hold_s after it, a grid step is at most 300 s.
# The backstop sits after that (plus 60 s) so the host's own exit normally wins (tested against forge common).
DSL_BACKSTOP_MARGIN_MS = 300_000 + 60_000
PUMPSWAP_VENUE = 1  # meta.venue of the host: 1 = PumpSwap, 0 = bonding curve. The live port buys PumpSwap pools only (mint_for_pool reads a PumpSwap pool account)
LANE_KEYS = X.LANE_KEYS
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
LATCH_SPEC_SHA = "spec_sha_mismatch"
C0 = F.C0
GAP_HOLD_MS = F.GAP_HOLD_MS  # after a host restart (a second start row) new entries wait this long, as FLOOR's executor does; open positions keep their exits and the backstop


def live_ok_default(bot_id: str) -> str:
    return f"/etc/mal-arena-live/{bot_id}/LIVE_OK"


def bot_id_problem(bot_id: Any) -> str | None:
    why = B.c0.bot_id_problem(bot_id)
    if why:
        return why
    if ":" in bot_id or bot_id in RESERVED_BOT_IDS or bot_id.startswith("hv4-"):
        return "bot_id_reserved_or_invalid"
    return None


def parse_lane(raw: Any, bot_id: str) -> H.LaneConfig:
    """The lane block. Config can only lower the DEC-035 maxima; lane.bot_id, if given, must equal the config bot_id. The stake must be written down and inside the bounds."""
    if not isinstance(raw, dict):
        raise X.LaneConfigError("lane must be an object")
    extra = set(raw) - LANE_KEYS
    if extra:
        raise X.LaneConfigError(f"lane: unknown keys {sorted(extra)}")
    for key in ("ledger_path", "stop_path"):
        if not isinstance(raw.get(key), str) or not raw[key] or not os.path.isabs(raw[key]):
            raise X.LaneConfigError(f"lane.{key} is required (absolute path)")
    if raw.get("bot_id", bot_id) != bot_id:
        raise X.LaneConfigError("lane.bot_id must equal the config bot_id")
    lo, hi = DSL_LANE_STAKE_BOUNDS_LAMPORTS
    stake = X._whole(raw, "stake_lamports")
    if not lo <= stake <= hi:
        raise X.LaneConfigError(f"lane.stake_lamports {stake} is outside [{lo}, {hi}]")
    max_open = X._whole(raw, "max_open", X.LANE_MAX_OPEN)
    budget = X._whole(raw, "budget_lamports", X.LANE_BUDGET_MAX_LAMPORTS)
    daily = X._whole(raw, "daily_loss_lamports", X.LANE_DAILY_MAX_LAMPORTS)
    if not 1 <= max_open <= X.LANE_MAX_OPEN:
        raise X.LaneConfigError(f"lane.max_open must be 1..{X.LANE_MAX_OPEN}")
    if not 1 <= budget <= X.LANE_BUDGET_MAX_LAMPORTS:
        raise X.LaneConfigError(f"lane.budget_lamports must be 1..{X.LANE_BUDGET_MAX_LAMPORTS}")
    if not 1 <= daily <= X.LANE_DAILY_MAX_LAMPORTS:
        raise X.LaneConfigError(f"lane.daily_loss_lamports must be 1..{X.LANE_DAILY_MAX_LAMPORTS}")
    reserve = X._whole(raw, "fee_reserve_lamports", X.LANE_MIN_FEE_RESERVE)
    if reserve < X.LANE_MIN_FEE_RESERVE:
        raise X.LaneConfigError(f"lane.fee_reserve_lamports must be >= {X.LANE_MIN_FEE_RESERVE}")
    stale = raw.get("mark_stale_s", X.LANE_MARK_STALE_MAX_S)
    if isinstance(stale, bool) or not isinstance(stale, (int, float)) or not math.isfinite(stale) or not 0 < stale <= X.LANE_MARK_STALE_MAX_S:
        raise X.LaneConfigError(f"lane.mark_stale_s must be in (0, {X.LANE_MARK_STALE_MAX_S}]")
    return H.LaneConfig(Path(raw["ledger_path"]), Path(raw["stop_path"]), bot_id, stake, max_open, budget, daily, reserve, float(stale), "", False)


def make_config(raw: Mapping[str, Any]) -> tuple[B.Config, H.LaneConfig, str, dict[str, Any]]:
    """(bridge Config, lane config, live_ok_path, pins). Fail closed: ValueError on any pin that does not hold."""
    mode = raw.get("mode", B.DRY)
    if mode not in (B.DRY, B.LIVE):
        raise ValueError("config mode must be 'dry' or 'live'")
    if mode == B.LIVE and not LIVE_BUILT:
        raise ValueError("live_not_built")
    if mode == B.LIVE and any(raw.get(k) for k in ("stop_file", "halt_file")):
        raise ValueError("live refused: stop_file / halt_file cannot be set in live (the wallet-wide files are pinned; the lane STOP is lane.stop_path)")
    bot_id = raw.get("bot_id")
    why = bot_id_problem(bot_id)
    if why:
        raise ValueError(f"bot_id refused: {why}")
    sha = raw.get("frozen_sha")
    if not isinstance(sha, str) or not SHA40.match(sha):
        raise ValueError("frozen_sha must be the host checkout's 40-hex HEAD sha")
    spec_sha = raw.get("spec_sha256")
    if not isinstance(spec_sha, str) or not SHA64.match(spec_sha):
        raise ValueError("spec_sha256 must be 64 hex")
    hold = raw.get("max_hold_s")
    if isinstance(hold, bool) or not isinstance(hold, (int, float)) or not math.isfinite(hold) or hold <= 0:
        raise ValueError("max_hold_s must be the spec's exit.max_hold_s (a positive number)")
    for k in ("root", "state_dir"):
        if not raw.get(k):
            raise ValueError(f"config {k} is required")
    end = raw.get("end_ms")
    if not F._num(end) or end <= 0:
        raise ValueError("config end_ms is required (explicit, positive)")
    lane = parse_lane(raw.get("lane"), bot_id)
    if raw.get("stake_lamports") not in (None, lane.stake_lamports):
        raise ValueError("top-level stake_lamports must be absent or equal lane.stake_lamports")
    live_ok = raw.get("live_ok_path", live_ok_default(bot_id))
    if not isinstance(live_ok, str) or not os.path.isabs(live_ok):
        raise ValueError("live_ok_path must be an absolute path")
    funded = raw.get("funded_lamports")
    if funded is not None and not (F._num(funded) and F.FUNDED_MIN_LAMPORTS <= funded <= F.FUNDED_MAX_LAMPORTS):
        raise ValueError("funded_lamports must be 0.35 to 1.00 SOL")
    mtd = raw.get("max_trades_per_day", C0["max_trades_per_day"])
    if not F._num(mtd) or mtd <= 0:
        raise ValueError("config max_trades_per_day must be a positive number")
    kw: dict[str, Any] = {}
    if "dry_balance_lamports" in raw:
        kw["dry_balance_lamports"] = int(raw["dry_balance_lamports"])
    for k in ("stop_file", "halt_file"):
        if raw.get(k):
            kw[k] = str(raw[k])
    cfg = B.Config(bot_id=bot_id, frozen_sha=sha, root=str(raw["root"]), state_dir=str(raw["state_dir"]), end_ms=int(end), max_hold_ms=int(hold * 1000) + DSL_BACKSTOP_MARGIN_MS,
                   mode=mode, stake_lamports=lane.stake_lamports, max_open=lane.max_open, max_trades_per_day=min(int(mtd), C0["max_trades_per_day"]),
                   daily_loss_lamports=lane.daily_loss_lamports, total_loss_lamports=lane.budget_lamports,
                   # A whole-position loss is a legal outcome (the host's sl can gap); only a loss beyond stake + fee reserve is an anomaly.
                   per_trade_loss_lamports=lane.stake_lamports + lane.fee_reserve_lamports,
                   wallet_floor_lamports=F.wallet_floor_lamports(None if funded is None else int(funded), int(raw.get("wallet_floor_lamports", 0) or 0)),
                   sl_backstop=B.MAX_SL_BACKSTOP, **kw)
    return cfg, lane, live_ok, {"spec_sha256": spec_sha, "max_hold_s": float(hold)}


class DslLaneLedger(X.FloorLaneLedger):
    """The shared lane ledger. FloorLaneLedger.evaluate (budget, daily, max_open, presend; whatever the base carries) with this book's stake bounds in front, and a COST-CAPPED pre-send:
    each of this bot's open positions counts min(mark, stake) in the pre-send P&L, so an unrealised gain never adds headroom (a full wipe of positions that were up must still end inside
    the lane budget). The stop labels (`lane_budget_stop`, `lane_daily_stop`, and the STOP file) are judged on the real P&L as before. tools/h5_lane_executor.py is untouched."""

    _marks: dict[str, tuple[int, int]] = {}

    def check(self, marks: dict[str, tuple[int, int]], now_ms: int, stake: int) -> str | None:
        self._marks = marks
        return super().check(marks, now_ms, stake)

    def reserve(self, mint: str, signature: str, marks: dict[str, tuple[int, int]], now_ms: int, stake: int, pool_gap_waived: bool = False) -> str | None:
        self._marks = marks
        return super().reserve(mint, signature, marks, now_ms, stake, pool_gap_waived)

    def unrealised_gain(self, view: H.LaneView, now_ms: int) -> int:
        """Sum of max(0, mark - stake) over this bot's filled open positions with a fresh mark (what lane_fold added to pnl above cost)."""
        gain, mine = 0, self.cfg.bot_id + ":"
        for key, p in view.open.items():
            if not key.startswith(mine) or not p["filled"]:
                continue
            mk = self._marks.get(key[len(mine):])
            if mk is not None and 0 <= now_ms - mk[1] <= self.cfg.mark_stale_s * 1000:
                gain += max(0, int(mk[0]) - int(p["stake"]))
        return gain

    def evaluate(self, view: H.LaneView, now_ms: int, stake: int, n_pending_extra: int = 0) -> str | None:
        lo, hi = DSL_LANE_STAKE_BOUNDS_LAMPORTS
        if not lo <= stake <= hi:
            return "lane_stake_out_of_bounds"
        why = super().evaluate(view, now_ms, stake, n_pending_extra)
        if why is not None:
            return why
        gain = self.unrealised_gain(view, now_ms)
        if gain <= 0:
            return None
        capped = dataclasses.replace(view, pnl=view.pnl - gain)
        why = super().evaluate(capped, now_ms, stake, n_pending_extra)
        return {"lane_budget_stop": "lane_budget_presend", "lane_daily_stop": "lane_daily_presend"}.get(why, why) if why else None


def take_bot_lock(lane: H.LaneLedger) -> Any:
    """One process per lane bot_id: an exclusive flock on <ledger>.bot.<bot_id>.lock for the life of the process, taken before the port, the state or anything else is built."""
    f = open(f"{lane.path}.bot.{lane.cfg.bot_id}.lock", "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise SystemExit(f"dsl lane start refused: another process holds bot_id {lane.cfg.bot_id!r} on this ledger") from None
    return f


def live_ok_problem(path: str, bot_id: str, root_checks: bool = True) -> str | None:
    why = B.root_ok_problem(Path(path)) if root_checks else (None if Path(path).exists() else "missing")
    if why:
        return "live_ok_" + why
    try:
        data = Path(path).read_text()
    except OSError:
        return "live_ok_unreadable"
    return None if data.strip() == bot_id else "live_ok_other_book"


# --- the bridge --------------------------------------------------------------------------------------------------------------------------------------


class DslLaneBridge(B.Bridge):
    """The base Bridge pinned to one DSL spec, plus the lane. With a DRY config it adds no ledger. With a live config the shared lane ledger is read under its lock before every send."""

    def __init__(self, cfg: B.Config, port: B.OrderPort, lane_cfg: H.LaneConfig, pins: Mapping[str, Any], *, now_ms: Callable[[], int] | None = None, live_ok_path: str | Path | None = None,
                 tier_path: Path = B.TIER_PATH, root_checks: bool = True, close_blocked: Callable[[str], bool | None] | None = None, a3_record_file: str = "", bot_lock: Any = None) -> None:
        self.bot = cfg.bot_id
        self.pins = dict(pins)
        self.hold_path = Path(cfg.state_dir) / f"dsl-state-{cfg.mode}.json"
        self.lane_cfg = lane_cfg
        self.lane: DslLaneLedger | None = None
        self._lane_bot_lock: Any = None
        self._lane_marks: dict[str, tuple[int, int]] = {}
        self._lane_orphans: set[str] = set()
        self._lane_recon_ms = 0
        self.lane_live_ok_path = str(live_ok_path if live_ok_path is not None else live_ok_default(cfg.bot_id))
        self.lane_root_checks = root_checks
        live = cfg.mode == B.LIVE
        self.a3_record_file = a3_record_file
        self._a3_pause: str | None = None
        self._a3_polled_ms: int | None = None
        if lane_cfg.bot_id != cfg.bot_id or bot_id_problem(cfg.bot_id):
            raise SystemExit("dsl lane start refused: bot_id pin")
        if live:  # the per-bot lock comes BEFORE the parent loads, reconciles or saves any state
            self.lane = DslLaneLedger(lane_cfg)
            self._lane_bot_lock = bot_lock if bot_lock is not None else take_bot_lock(self.lane)
        try:
            self._start_guard(cfg, port)
            super().__init__(cfg, port, now_ms=now_ms, live_ok_path=Path(self.lane_live_ok_path), tier_path=tier_path, root_checks=root_checks, close_blocked=close_blocked)
        except BaseException:
            self.release_lane_lock()
            raise
        if live:
            assert isinstance(port, X.FloorLanePort)
            port.pre_buy = self._lane_pre_buy
            port.on_error = self._latch
            self._lane_record("start", "", code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), frozen_sha=cfg.frozen_sha, book=cfg.bot_id, spec_sha256=self.pins["spec_sha256"],
                              lane={"bot_id": self.bot, "stake_lamports": lane_cfg.stake_lamports, "max_open": lane_cfg.max_open, "budget_lamports": lane_cfg.budget_lamports,
                                    "daily_loss_lamports": lane_cfg.daily_loss_lamports, "fee_reserve_lamports": lane_cfg.fee_reserve_lamports, "mark_stale_s": lane_cfg.mark_stale_s})
            self._lane_reconcile(self.now_ms(), force=True)

    def _start_guard(self, cfg: B.Config, port: B.OrderPort) -> None:
        lo, hi = DSL_LANE_STAKE_BOUNDS_LAMPORTS
        if not lo <= self.lane_cfg.stake_lamports <= hi or cfg.stake_lamports != self.lane_cfg.stake_lamports:
            raise SystemExit(f"dsl lane start refused: stake {cfg.stake_lamports} / lane stake {self.lane_cfg.stake_lamports} outside [{lo}, {hi}]")
        if cfg.max_open > X.LANE_MAX_OPEN or cfg.max_open != self.lane_cfg.max_open:
            raise SystemExit("dsl lane start refused: max_open")
        if cfg.mode == B.LIVE:
            if not (LIVE_BUILT and port.live and isinstance(port, X.FloorLanePort)):
                raise SystemExit("dsl lane start refused: a live config needs LIVE_BUILT and a FloorLanePort")
            if not X.sell_closes_ata():
                raise SystemExit("dsl lane start refused: the sell does not close the token ATA (rent would be stranded)")
        elif port.live:
            raise SystemExit("dsl lane start refused: a DRY config never takes a live port")

    def release_lane_lock(self) -> None:
        if self._lane_bot_lock is not None:
            self._lane_bot_lock.close()
            self._lane_bot_lock = None

    def _log(self, kind: str, **kw: Any) -> None:
        super()._log(kind, lane_bot_id=self.bot, **kw)

    # -- limits: the lane's
    def tier_limits(self) -> tuple[str, dict[str, int], str | None]:
        c = self.cfg
        return "LANE", {"stake_lamports": c.stake_lamports, "max_open": c.max_open, "max_trades_per_day": min(c.max_trades_per_day, C0["max_trades_per_day"]),
                        "daily_loss_lamports": c.daily_loss_lamports, "total_loss_lamports": c.total_loss_lamports}, None

    def effective_live(self) -> tuple[bool, str | None]:
        if self.cfg.mode != B.LIVE:
            return False, "mode_dry"
        why = live_ok_problem(self.lane_live_ok_path, self.bot, self.lane_root_checks)
        return (False, why) if why else (True, None)

    # -- money (the FLOOR lane's p155 terms: a DSL position costs the same to open and close)
    def _position_worst_case(self, stake: int) -> int:
        return stake + F.SEND_FEE_LAMPORTS + F.RENT_RESERVE_LAMPORTS + F.SELL_RESERVE_LAMPORTS

    def committed_loss_lamports(self, scope: bool = True) -> int:
        total = max(0, -int(self.state.realized_lamports))
        return total + sum(self._position_worst_case(int(o["stake_lamports"])) for o in self.open_orders(scope).values())

    def floor_problem(self, stake: int, bal: int | None, scope: bool = True) -> str | None:
        if bal is None:
            return "balance_unreadable"
        opens = self.open_orders(scope)
        if self.committed_loss_lamports(scope) + self._position_worst_case(stake) > self.cfg.total_loss_lamports:
            return "committed_loss_stop"
        held = sum(int(o["stake_lamports"]) for o in opens.values())
        need = held + stake + F.SEND_FEE_LAMPORTS + F.RENT_RESERVE_LAMPORTS + (len(opens) + 1) * F.SELL_RESERVE_LAMPORTS + self.cfg.wallet_floor_lamports
        return "wallet_floor" if bal < need else None

    # -- entry gates
    def a3_pause_tick(self, now: int) -> None:
        """DEC-035 s4: on a pump / PumpSwap redeploy flag (A3), a stale, future or unreadable record, new entries pause (not a latch; clears with the record). Exits keep running."""
        if self.lane is None or not self.a3_record_file:
            return
        if self._a3_polled_ms is not None and 0 <= now - self._a3_polled_ms < X.A3_POLL_MS:
            return
        self._a3_polled_ms = now
        try:
            flags, problem = h5a.a3_record_problem(Path(self.a3_record_file), now)
        except Exception:  # noqa: BLE001 - fail closed
            flags, problem = [], "a3_record_error"
        new = problem or ("a3_flags:" + ",".join(flags) if flags else None)
        if new != self._a3_pause:
            self._log("a3_pause" if new else "a3_resume", why=new)
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
        if why == "lane_budget_stop":  # DEC-035 s2 item 1: the budget stop writes the lane STOP file
            self.lane.write_stop(f"lane_budget_stop bot_id={self.bot}", now)
        return X.LANE_REASON.get(why, why) if why else None

    def pin_refusal(self, row: Mapping[str, Any]) -> str | None:
        if row.get("arm") != self.bot:
            return "bot_pin"
        meta = row.get("meta")
        if not isinstance(meta, dict) or meta.get("rule") != DSL_RULE:
            return "meta_pin"
        mh = meta.get("max_hold_s")
        if isinstance(mh, bool) or not isinstance(mh, (int, float)) or mh != self.pins["max_hold_s"]:
            return "meta_pin"
        if meta.get("venue") != PUMPSWAP_VENUE:
            return "venue_not_pumpswap"  # explicit, counted refusal: the lane port cannot trade a bonding-curve pool (its `pool` is the mint string)
        if "spec_sha256" in meta and meta["spec_sha256"] != self.pins["spec_sha256"]:
            return "spec_pin"
        if self.now_ms() < self._hold_until():
            return "gap_hold"
        return None

    def entry_refusal(self, row: Mapping[str, Any], now: int, lim: dict[str, int], scope: bool = True) -> str | None:
        lane_why = self._lane_check(now, lim["stake_lamports"]) if (self.lane is not None and scope) else None
        if lane_why == "lane_full":
            return lane_why  # the lane is full whatever else is wrong: report it as such
        why = super().entry_refusal(row, now, lim, scope) or self.pin_refusal(row)
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
        lo, hi = DSL_LANE_STAKE_BOUNDS_LAMPORTS
        if not isinstance(stake, int) or isinstance(stake, bool) or not lo <= stake <= hi or stake != self.cfg.stake_lamports:
            return "lane_stake_out_of_bounds"
        why = self.lane.reserve(str(order["mint"]), f"intent:{order['order_id']}", self._lane_marks, self.now_ms(), stake)
        return X.LANE_REASON.get(why, why) if why else None

    def handle_entry(self, row: dict[str, Any]) -> None:
        super().handle_entry(row)
        o = self.state.orders.get(str(row.get("order_id")))
        if o is not None and o["status"] in ("buy_sending", "buy_sent", "open"):
            o["row_stake_sol"] = row.get("stake_sol")  # logged, never used for sizing
            o["max_hold_s"] = (row.get("meta") or {}).get("max_hold_s")
            self._save()
        if self.lane is not None and o is not None and o["status"] == "refused" and o.get("send_ms") is not None:
            # the port refused after the write-ahead (a lane race, a gate or an executor skip): nothing was sent. Close the intent if one was written, release the mint.
            if o["mint"] in self.state.bought:
                self.state.bought.remove(o["mint"])
            self._lane_writeoff_unsent(o)
            self._save()

    # -- host restart: a gap hold on entries (persisted); positions are untouched
    def _hold_until(self) -> int:
        try:
            return int(json.loads(self.hold_path.read_text()).get("gap_hold_until_ms", 0))
        except (OSError, ValueError, AttributeError, TypeError):
            return 0

    def _set_hold(self, until: int) -> None:
        B._atomic_write(self.hold_path, {"gap_hold_until_ms": int(until)})

    # -- decision rows
    def handle_row(self, row: dict[str, Any]) -> None:
        k = row.get("kind")
        if k == "start":
            if row.get("arm") != self.bot:
                self._latch("bot_pin_mismatch", f"start row arm {str(row.get('arm'))[:40]}")
            elif row.get("spec_sha256") != self.pins["spec_sha256"]:
                self._latch(LATCH_SPEC_SHA, f"start row spec {str(row.get('spec_sha256'))[:12]} pinned {self.pins['spec_sha256'][:12]}")
            if self.state.start_seen:  # a restart of the host (open positions come back as host_down gaps / later exit rows): hold entries, never touch positions
                self._set_hold(max(self._hold_until(), self.now_ms() + GAP_HOLD_MS))
                self._log("host_restart", open_orders=len(self.open_orders(self.cfg.mode == B.LIVE)), hold_until_ms=self._hold_until())
            self._count("host_start_rows")
        elif k == "exit":
            reason = row.get("reason")
            if reason not in FOLLOWED_EXIT_REASONS:
                self._count("exit_not_followed")
                self._log("exit_not_followed", order_id=row.get("order_id"), reason=reason, seq=row.get("seq"))
                self._save()
                return
        super().handle_row(row)

    # -- exits: the host's reasons, plus the clock backstop
    def backstops(self) -> None:
        now = self.now_ms()
        self.reconcile(only_stale=True)
        for oid, o in list(self.state.orders.items()):
            if o["status"] != "open":
                continue
            mh = o.get("max_hold_s")
            if isinstance(mh, bool) or not isinstance(mh, (int, float)) or mh <= 0:
                mh = self.pins["max_hold_s"]
            due = int(o["decision_ms"]) + int(mh * 1000) + DSL_BACKSTOP_MARGIN_MS
            if now >= due:
                o.setdefault("exit_decision_ms", due)  # the backstop's own instant, not the instant we noticed
                self._try_exit(oid, REASON_BACKSTOP)

    # -- re-entry: a mint is released when its sell has landed (the host may signal the same pool again after its cooldown)
    def apply_events(self, events: list[dict[str, Any]]) -> None:
        by_mint = {o["mint"]: k for k, o in self.state.orders.items()}
        sold = [(ev.get("order_id") or by_mint.get(ev.get("mint"))) for ev in events if ev.get("event") == "sell_landed"]
        if self.lane is not None:
            for ev in events:
                oid = ev.get("order_id") or by_mint.get(ev.get("mint"))
                o = self.state.orders.get(oid) if oid else None
                if o is not None:
                    self._lane_book(ev, o)
        super().apply_events([{k: v for k, v in ev.items() if k != "_row"} for ev in events])
        for oid in sold:
            o = self.state.orders.get(oid) if oid else None
            if o is not None and o["status"] == "closed" and o["mint"] in self.state.bought:
                self.state.bought.remove(o["mint"])
        if sold:
            self._save()

    # -- books (the FLOOR lane's, with this bot_id)
    def _lane_record(self, ev: str, mint: str, **kw: Any) -> None:
        if self.lane is None:
            return
        try:
            self.lane.record(ev, mint, self._lane_marks, self.now_ms(), **kw)
        except Exception as exc:  # noqa: BLE001
            print(f"dsl_lane_executor ALERT lane_ledger_write_failed ev={ev} label={type(exc).__name__}", flush=True)
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
                view = H.lane_fold(self.lane.read(), self._lane_marks, self.now_ms(), self.lane_cfg.mark_stale_s, self.bot)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            return
        held = view.open.get(f"{self.bot}:{o['mint']}")
        if held is not None and not held["filled"] and held["signature"] == f"intent:{o['order_id']}":
            self._lane_record("writeoff", o["mint"], reason="not_sent", signature=held["signature"])

    def _lane_book(self, ev: Mapping[str, Any], o: dict[str, Any]) -> None:
        kind, mint, row = ev.get("event"), str(o["mint"]), ev.get("_row") or {}
        sig = row.get("signature")
        fee = X._i(row.get("fee_lamports"))
        if kind == "buy_landed":
            est = not row
            out = X._i(row.get("sol_spent_lamports")) if row else int(o["stake_lamports"]) + F.RENT_RESERVE_LAMPORTS + F.SEND_FEE_LAMPORTS
            o["lane_buy_out"] = out
            self._lane_record("fill", mint, ok=True, sol_out=out, signature=sig, estimated=est, rent_paid_lamports=X._i(row.get("rent_charged_lamports")), **X._fee_parts(fee))
        elif kind == "buy_failed":
            pnl = X._i(ev.get("pnl_lamports"))
            if ev.get("label") == "expired" or (pnl == 0 and not row.get("landed")):
                self._lane_record("writeoff", mint, reason="buy_expired", signature=sig)  # an unlanded buy: closed, no SOL moved
            else:
                self._lane_record("fill", mint, ok=False, sol_out=-pnl, signature=sig, **X._fee_parts(fee))
        elif kind == "sell_landed":
            refunded = X._i(row.get("rent_refunded_lamports"))
            if row:
                net, est = X._i(row.get("sell_net_lamports")), False
                sol_in, sol_out = net + fee, fee
            else:
                est, refunded = True, 0
                sol_in, sol_out = max(0, X._i(ev.get("pnl_lamports")) + X._i(o.get("lane_buy_out"))), 0
            if refunded <= 0:
                self._latch(X.LATCH_NO_ATA_REFUND, f"{o['order_id']} sell landed with no token-account rent refunded")  # stops new entries; exits continue
            self._lane_record("exit", mint, sol_in=sol_in, sol_out=sol_out, rent_refunded=refunded, signature=sig, estimated=est, reason=o.get("sell_reason"), **X._fee_parts(fee))
        elif kind == "sell_failed":
            cost = X._i(row.get("cost_lamports"))
            if cost > 0:  # a landed failed sell paid its fee; the position stays open
                self._lane_record("fee", mint, sol_out=cost, reason="sell_failed", signature=sig, **X._fee_parts(fee))

    # -- marks, backlog, unresolved intents
    def tick(self) -> int:
        n = super().tick()
        if self.lane is not None:
            self._lane_update_marks()
            try:
                self.lane.drain()
            except Exception as exc:  # noqa: BLE001
                print(f"dsl_lane_executor ALERT lane_backlog_drain_failed label={type(exc).__name__}", flush=True)
            self._lane_reconcile(self.now_ms())
        return n

    def _lane_update_marks(self) -> None:
        """Mark = stake x (1 + return) from the executor's fresh warm quote; no fresh quote keeps the old mark, which goes stale after mark_stale_s and then counts 0."""
        now = self.now_ms()
        for o in self.state.orders.values():
            if o["status"] != "open" or not o["live"]:
                continue
            ret = self.port.position_return(o["mint"])
            if isinstance(ret, (int, float)) and not isinstance(ret, bool) and math.isfinite(ret):
                self._lane_marks[o["mint"]] = (max(0, int(o["stake_lamports"] * (1.0 + ret))), now)

    def _lane_reconcile(self, now: int, force: bool = False) -> None:
        """Ledger against bridge state (see floor_lane_executor._lane_reconcile): refused-order intents closed at 0, lost fill events booked at a flagged estimate, closed-without-exit positions
        written off at 0 proceeds, intents with no order blocking entries until LANE_UNRESOLVED_MS and then written off at full estimated cost."""
        assert self.lane is not None
        if not force and now - self._lane_recon_ms < X.LANE_RECONCILE_EVERY_MS:
            return
        if self.lane.backlog_pending():
            return
        self._lane_recon_ms = now
        try:
            with self.lane.locked():
                view = H.lane_fold(self.lane.read(), self._lane_marks, now, self.lane_cfg.mark_stale_s, self.bot)
        except Exception:  # noqa: BLE001
            self._lane_orphans = {"?"}  # an unreadable ledger blocks entries
            return
        mine = self.bot + ":"
        orphans: set[str] = set()
        for key, p in view.open.items():
            if not key.startswith(mine):
                continue
            mint = key[len(mine):]
            sig = p.get("signature")
            o = self.state.orders.get(sig[len("intent:"):]) if isinstance(sig, str) and sig.startswith("intent:") else None  # by intent id, never by mint
            full = p["stake"] + F.RENT_RESERVE_LAMPORTS + F.SEND_FEE_LAMPORTS
            if o is None:
                if now - p["ts_ms"] >= X.LANE_UNRESOLVED_MS:
                    print(f"dsl_lane_executor ALERT lane_intent_written_off mint={mint}", flush=True)
                    self._lane_record("writeoff", mint, reason="unresolved_intent_10min", sol_out=0 if p["filled"] else full, signature=p["signature"], estimated=True)
                else:
                    orphans.add(mint)
                continue
            if o["status"] == "refused" and not p["filled"]:
                self._lane_record("writeoff", mint, reason="not_sent", signature=p["signature"])
                continue
            if not p["filled"] and o["status"] in ("open", "sell_sending", "sell_sent", "closed"):
                self._lane_record("fill", mint, ok=True, sol_out=full, signature=p["signature"], estimated=True, reason="fill_event_lost")
                o["lane_buy_out"] = full
            if o["status"] == "closed":
                self._lane_record("writeoff", mint, reason="closed_unrecorded", signature=p["signature"], estimated=True)
        self._lane_orphans = orphans


# --- run ------------------------------------------------------------------------------------------------------------------------------------------------


def build_live_port(cfg: B.Config) -> X.FloorLanePort:
    """The LIVE port: the same as the FLOOR lane's (key only from the systemd credential; imports local). Only `run --live` with a live config reaches this."""
    return X.build_live_port(cfg)


def start_refusal(cfg: B.Config, live_ok_path: str, *, live: bool, root_checks: bool = True) -> str | None:
    if live != (cfg.mode == B.LIVE):
        return "live_flag_mode_mismatch"
    if live:
        if not LIVE_BUILT:
            return "live_not_built"
        why = live_ok_problem(live_ok_path, cfg.bot_id, root_checks)
        if why:
            return why
        if not X.sell_closes_ata():
            return "sell_does_not_close_ata"
    return None


def run(raw: Mapping[str, Any], *, live_flag: bool, once: bool = False, root_checks: bool = True, port_factory: Callable[[B.Config], B.OrderPort] | None = None,
        now_ms: Callable[[], int] | None = None, sleep: Callable[[float], None] | None = None) -> int:
    """DRY unless the config says mode live AND `live_flag`; one without the other does not start (never half-live, never a silent DRY)."""
    mode = raw.get("mode", B.DRY)
    if mode not in (B.DRY, B.LIVE) or (mode == B.LIVE) != live_flag:
        print("dsl_lane_executor ALERT startup_refused live_flag_mode_mismatch", flush=True)
        return 2
    try:
        cfg, lane, live_ok, pins = make_config(raw)
    except (ValueError, TypeError) as exc:
        print(f"dsl_lane_executor ALERT startup_refused {exc}", flush=True)
        return 2
    a3_file = str(raw.get("a3_record_file") or "")
    if live_flag and not (a3_file and os.path.isabs(a3_file)):
        print("dsl_lane_executor ALERT startup_refused a3_record_file_missing", flush=True)
        return 2
    why = start_refusal(cfg, live_ok, live=live_flag, root_checks=root_checks)
    if why:
        print(f"dsl_lane_executor ALERT startup_refused {why}", flush=True)
        return 2
    lock = None
    if live_flag:  # before the port is built and before any state is loaded
        try:
            lock = take_bot_lock(DslLaneLedger(lane))
        except SystemExit as exc:
            print(f"dsl_lane_executor ALERT startup_refused {exc}", flush=True)
            return 2
    port = (port_factory or build_live_port)(cfg) if live_flag else B.DryPort(cfg.dry_balance_lamports)
    br = DslLaneBridge(cfg, port, lane, pins, now_ms=now_ms, live_ok_path=live_ok, root_checks=root_checks, a3_record_file=a3_file, bot_lock=lock)
    print(f"dsl_lane_executor mode={cfg.mode} lane_bot={cfg.bot_id} stake={cfg.stake_lamports}", flush=True)
    kw = {"sleep": sleep} if sleep is not None else {}
    br.run(once=once, **kw)
    return 0


def check(raw: Mapping[str, Any], *, live: bool = False, root_checks: bool = True) -> dict[str, Any]:
    try:
        cfg, _lane, live_ok, _pins = make_config(raw)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "problem": f"config: {exc}"}
    why = start_refusal(cfg, live_ok, live=live, root_checks=root_checks)
    return {"ok": why is None, "problem": why}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FORGE DSL lane executor (one lane bot_id per spec). `run` is DRY unless config mode is live AND --live is given; `check` validates only.")
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
