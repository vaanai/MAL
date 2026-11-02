"""H5 executor, live-learning lane mode (DEC-035 s2). A SUBCLASS of tools.h5_executor.H5Executor in its own module.

tools/h5_executor.py is NOT edited: its git blob is pinned by tools/h5_arena_executor.py (SELL_BUILDER_PIN file_sha256, executor_blob) and
tools/arena_h5_score.py (EXECUTOR_BLOB); editing it breaks every importer (233 arena / C1-NF tests failed in PR #1207's first head). This module
overrides only what the lane needs. With the config key `lane` absent, H5LaneExecutor is H5Executor: no override changes a decision.

Limits (all DEC-035 s2, enforced IN ADDITION to the tier limits, TIER_WALLET_FRAC and RULE_Q_MAX_LAMPORTS; the lane only LOWERS: effective = min(tier, lane)):
  - lane ledger: append-only JSONL under an fcntl lock; every send intent, fill, fee, exit and write-off with SOL in/out and every fee.
  - budget: lane P&L <= -budget writes the lane STOP; a send needs pnl - (open stakes + new stake + fee_reserve x (open + 1)) > -budget.
  - daily stop: -0.10 SOL from the 00:00Z snapshot, measured two ways (marks, and cost basis) and also as a worst case before a send.
  - max open 3 from the shared ledger; stake in [0.02, 0.03] SOL; marks older than mark_stale_s (<= 60) are worth 0; lane STOP file before every send.
  - write-ahead: the `send_intent` row is fsynced BEFORE the send; every other row goes through a persisted backlog; a malformed ledger fails closed.
Everything is keyed by (bot_id, mint); `lane_fold` ignores `start` and `snap` rows for positions.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Sequence

from tools import h5_executor as h
from tools import probe_executor as pe
from tools import probe_live as pl
from tools import pumpswap_tx as tx

LANE_SCHEMA = "h5-lane-ledger.v1"
LANE_STAKE_MIN_LAMPORTS = 20_000_000
LANE_STAKE_MAX_LAMPORTS = 30_000_000
LANE_MAX_OPEN = 3
LANE_BUDGET_MAX_LAMPORTS = 300_000_000
LANE_DAILY_MAX_LAMPORTS = 100_000_000
LANE_MARK_STALE_MAX_S = 60
LANE_LOCK_WAIT_S = 5.0
LANE_UNRESOLVED_MS = 600_000  # an intent with no outcome after 10 min is written off at its full cost
LANE_RECONCILE_EVERY_MS = 5_000
# What one position can cost beyond its stake: ATA rent up front, the buy fee, and every sell attempt at the escalated priority.
LANE_MIN_FEE_RESERVE = (pe.FEEARM_RENT_RESERVE_LAMPORTS + tx.BASE_FEE_PER_SIGNATURE + h.H5_DEFAULT["buy_priority_lamports"]
                        + pl.SELL_MAX_ATTEMPTS * (tx.BASE_FEE_PER_SIGNATURE + h.H5_DEFAULT["escalated_priority_lamports"]))
LANE_KEYS = {"ledger_path", "stop_path", "bot_id", "stake_lamports", "max_open", "budget_lamports", "daily_loss_lamports", "fee_reserve_lamports",
             "mark_stale_s"}


class LaneLedgerError(ValueError):
    """A ledger row that cannot be read as what it claims to be. New sends fail closed on it; exits continue."""


@dataclass(frozen=True)
class LaneConfig:
    ledger_path: Path
    stop_path: Path
    bot_id: str
    stake_lamports: int
    max_open: int
    budget_lamports: int
    daily_loss_lamports: int
    fee_reserve_lamports: int
    mark_stale_s: float

    @classmethod
    def parse(cls, raw: Any) -> "LaneConfig":
        """A bad lane config raises ValueError (the executor refuses to start). Config can only lower the DEC-035 maxima."""
        if not isinstance(raw, dict):
            raise ValueError("lane must be an object")
        extra = set(raw) - LANE_KEYS
        if extra:
            raise ValueError(f"lane: unknown keys {sorted(extra)}")

        def num(key: str, default: Any = None) -> int:
            v = raw.get(key, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v != int(v):
                raise ValueError(f"lane.{key} must be a whole number")
            return int(v)

        for key in ("ledger_path", "stop_path"):
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise ValueError(f"lane.{key} is required")
        stake = num("stake_lamports")
        if not LANE_STAKE_MIN_LAMPORTS <= stake <= LANE_STAKE_MAX_LAMPORTS:
            raise ValueError(f"lane.stake_lamports {stake} is outside [{LANE_STAKE_MIN_LAMPORTS}, {LANE_STAKE_MAX_LAMPORTS}]")
        max_open, budget, daily = num("max_open", LANE_MAX_OPEN), num("budget_lamports", LANE_BUDGET_MAX_LAMPORTS), num("daily_loss_lamports", LANE_DAILY_MAX_LAMPORTS)
        if not 1 <= max_open <= LANE_MAX_OPEN:
            raise ValueError(f"lane.max_open must be 1..{LANE_MAX_OPEN}")
        if not 1 <= budget <= LANE_BUDGET_MAX_LAMPORTS:
            raise ValueError(f"lane.budget_lamports must be 1..{LANE_BUDGET_MAX_LAMPORTS}")
        if not 1 <= daily <= LANE_DAILY_MAX_LAMPORTS:
            raise ValueError(f"lane.daily_loss_lamports must be 1..{LANE_DAILY_MAX_LAMPORTS}")
        reserve = num("fee_reserve_lamports")
        if reserve < LANE_MIN_FEE_RESERVE:
            raise ValueError(f"lane.fee_reserve_lamports must be >= {LANE_MIN_FEE_RESERVE} (ATA rent + buy fee + {pl.SELL_MAX_ATTEMPTS} escalated sell attempts)")
        stale = raw.get("mark_stale_s", LANE_MARK_STALE_MAX_S)
        if isinstance(stale, bool) or not isinstance(stale, (int, float)) or not math.isfinite(stale) or not 0 < stale <= LANE_MARK_STALE_MAX_S:
            raise ValueError(f"lane.mark_stale_s must be in (0, {LANE_MARK_STALE_MAX_S}]")
        bot = raw.get("bot_id", "h5-lane")
        if not isinstance(bot, str) or not bot or ":" in bot:
            raise ValueError("lane.bot_id must be a non-empty string without ':'")
        return cls(Path(raw["ledger_path"]), Path(raw["stop_path"]), bot, stake, max_open, budget, daily, reserve, float(stale))


@dataclass
class LaneView:
    cash: int  # sum over every row of (sol_in - sol_out): SOL received minus SOL sent, fees and rent included
    pnl: int  # cash plus the fresh marks of this executor's filled open positions (a stale or missing mark is worth 0): the DEC-035 measure
    cost: int  # cash plus the STAKE of every filled open position: a position is carried at cost until it exits (never optimistic)
    open: dict[str, dict[str, Any]]  # "<bot_id>:<mint>" -> {"stake", "filled", "ts_ms", "signature"}: sent and not yet exited or written off
    snaps: dict[str, tuple[int, int]]  # UTC day -> (pnl, cost) at that day's 00:00Z snapshot

    @property
    def open_stakes(self) -> int:
        return sum(p["stake"] for p in self.open.values())


def _int(r: dict[str, Any], key: str, required: bool = False) -> int:
    v = r.get(key, None if required else 0)
    if v is None and not required:
        v = 0
    if isinstance(v, bool) or not isinstance(v, int):
        raise LaneLedgerError(f"row field {key!r} is not an integer")
    return v


def lane_fold(rows: Sequence[Any], marks: dict[str, tuple[int, int]], now_ms: int, stale_s: float, bot_id: str = "") -> LaneView:
    """Replay the ledger. `marks` is mint -> (value in lamports, quote time ms) for this executor's own positions only; another executor's open
    position has no mark here and counts 0. Raises LaneLedgerError on any row it cannot read (callers fail closed)."""
    cash = 0
    pos: dict[str, dict[str, Any]] = {}
    snaps: dict[str, tuple[int, int]] = {}
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("ev"), str):
            raise LaneLedgerError("ledger row is not an object with an `ev`")
        rid = r.get("row_id")
        if rid is not None:
            if rid in seen:
                continue  # a backlog row appended twice by a crash between append and cleanup
            seen.add(rid)
        ev = r["ev"]
        if ev == "start":
            continue
        if ev == "snap":
            snaps[str(r["day"])] = (_int(r, "pnl_lamports", True), _int(r, "pnl_cost_lamports", True))
            continue
        if not isinstance(r.get("mint"), str) or not isinstance(r.get("bot_id"), str):
            raise LaneLedgerError("ledger row has no bot_id / mint")
        key = f"{r['bot_id']}:{r['mint']}"
        cash += _int(r, "sol_in_lamports") - _int(r, "sol_out_lamports")
        if ev == "send_intent":
            pos[key] = {"stake": _int(r, "stake_lamports", True), "filled": False, "ts_ms": _int(r, "ts_ms", True), "signature": r.get("signature")}
        elif ev == "fill":
            if r.get("ok") is True and key in pos:
                pos[key]["filled"] = True
            else:
                pos.pop(key, None)  # a buy that landed with an error: closed, its fee is in cash
        elif ev in ("exit", "writeoff"):
            pos.pop(key, None)
        # "fee" (a failed or superseded sell that paid its fee) leaves the position open
    value = 0
    for key, p in pos.items():
        owner, mint = key.split(":", 1)
        mk = marks.get(mint) if owner == bot_id else None
        if p["filled"] and mk is not None and 0 <= now_ms - mk[1] <= stale_s * 1000:
            value += int(mk[0])
    cost = cash + sum(p["stake"] for p in pos.values() if p["filled"])
    return LaneView(cash=cash, pnl=cash + value, cost=cost, open=pos, snaps=snaps)


class LaneLedger:
    """Append-only JSONL with an fcntl lock (a sibling .lock file, waited for up to LANE_LOCK_WAIT_S). Check-and-append is one critical section.
    Rows other than `send_intent` go to a persisted per-bot backlog first and are drained into the ledger under the lock; entries are refused
    while the backlog is non-empty, across restarts."""

    def __init__(self, cfg: LaneConfig):
        self.cfg = cfg
        self.path = cfg.ledger_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.backlog_path = Path(f"{self.path}.backlog.{cfg.bot_id}")

    @contextlib.contextmanager
    def locked(self):
        fd = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            deadline = time.monotonic() + LANE_LOCK_WAIT_S
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("lane ledger lock busy") from None
                    time.sleep(0.01)
            yield
        finally:
            os.close(fd)  # closing releases the flock

    def read(self) -> list[Any]:
        """Call under `locked`. A torn or unreadable line is an error (fail closed), never skipped."""
        if not self.path.exists():
            return []
        out = []
        for x in self.path.read_text().splitlines():
            if x.strip():
                try:
                    out.append(json.loads(x))
                except ValueError as exc:
                    raise LaneLedgerError(f"unreadable ledger line: {exc}") from None
        return out

    def _write_line(self, path: Path, row: dict[str, Any]) -> None:
        line = json.dumps(row, sort_keys=True, default=str) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def append(self, row: dict[str, Any]) -> None:
        """Call under `locked`."""
        self._write_line(self.path, {"schema": LANE_SCHEMA, "bot_id": self.cfg.bot_id, "row_id": uuid.uuid4().hex, **row})

    def backlog_pending(self) -> bool:
        try:
            return self.backlog_path.stat().st_size > 0
        except FileNotFoundError:
            return False
        except OSError:
            return True

    def drain_locked(self, rows: list[Any] | None = None) -> None:
        """Move the backlog into the ledger (call under `locked`). Rows already in the ledger (by row_id) are not appended twice."""
        if not self.backlog_pending():
            return
        have = {r.get("row_id") for r in (rows if rows is not None else self.read()) if isinstance(r, dict)}
        for line in self.backlog_path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("row_id") not in have:
                    self._write_line(self.path, row)
        self.backlog_path.unlink()

    def drain(self) -> None:
        if self.backlog_pending():
            with self.locked():
                self.drain_locked()

    def stop_present(self) -> bool:
        """The lane STOP file. Present, or unreadable for any reason but 'not there', blocks new sends."""
        try:
            os.stat(self.cfg.stop_path)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return True

    def write_stop(self, reason: str, now_ms: int) -> None:
        """Create the STOP file with a reason line. An existing file is left alone (it survives restarts; only the manager removes it)."""
        self.cfg.stop_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.cfg.stop_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            return
        with os.fdopen(fd, "w") as f:
            f.write(f"{reason} ts_ms={now_ms} bot_id={self.cfg.bot_id}\n")

    def evaluate(self, view: LaneView, now_ms: int, stake: int, n_pending_extra: int = 0) -> str | None:
        """The DEC-035 s2 limits, in order. Pure: it reads the view only."""
        c = self.cfg
        if not LANE_STAKE_MIN_LAMPORTS <= stake <= LANE_STAKE_MAX_LAMPORTS:
            return "lane_stake_out_of_bounds"
        if view.pnl <= -c.budget_lamports:
            return "lane_budget_stop"
        base = view.snaps.get(day_of(now_ms))
        if base is not None and (view.pnl - base[0] <= -c.daily_loss_lamports or view.cost - base[1] <= -c.daily_loss_lamports):
            return "lane_daily_stop"
        if len(view.open) >= c.max_open:
            return "lane_max_open"
        reserve = c.fee_reserve_lamports * (len(view.open) + 1)  # every open or unconfirmed position can still cost fees and rent, and so can this one
        if view.pnl - (view.open_stakes + stake + reserve) <= -c.budget_lamports:
            return "lane_budget_presend"
        if base is not None and view.cost - base[1] - (view.open_stakes + stake + reserve) <= -c.daily_loss_lamports:
            return "lane_daily_presend"  # the worst case: every open position and this one lost in full must still end inside the day's 0.10 SOL
        return None

    def _snap_locked(self, view: LaneView, now_ms: int) -> None:
        day = day_of(now_ms)
        if day not in view.snaps:  # the first look of a UTC day is its 00:00Z snapshot
            self.append({"ev": "snap", "ts_ms": now_ms, "day": day, "pnl_lamports": view.pnl, "pnl_cost_lamports": view.cost, "estimated": False})
            view.snaps[day] = (view.pnl, view.cost)

    def check(self, marks: dict[str, tuple[int, int]], now_ms: int, stake: int) -> str | None:
        """Read-only early check (no row written). Any trouble reading the ledger refuses the entry."""
        if self.stop_present():
            return "lane_stop_file"
        if self.backlog_pending():
            return "lane_backlog_pending"
        try:
            with self.locked():
                view = lane_fold(self.read(), marks, now_ms, self.cfg.mark_stale_s, self.cfg.bot_id)
        except Exception:
            return "lane_ledger_error"
        return self.evaluate(view, now_ms, stake)

    def reserve(self, mint: str, signature: str, marks: dict[str, tuple[int, int]], now_ms: int, stake: int) -> str | None:
        """Drain the backlog, check every limit and, if they all pass, write and fsync the `send_intent` row, in one critical section. None means
        the send is reserved (write-ahead: the row is on disk before the transaction leaves)."""
        if self.stop_present():
            return "lane_stop_file"
        try:
            with self.locked():
                self.drain_locked()
                view = lane_fold(self.read(), marks, now_ms, self.cfg.mark_stale_s, self.cfg.bot_id)
                held = view.open.get(f"{self.cfg.bot_id}:{mint}")
                if held is not None and held["signature"] == signature:
                    return None  # idempotent: this send is already recorded (a re-send after a restart); its own row is not checked against the limits
                self._snap_locked(view, now_ms)
                why = self.evaluate(view, now_ms, stake)
                if why == "lane_budget_stop":
                    self.write_stop(f"lane_budget_stop pnl_lamports={view.pnl} budget_lamports={self.cfg.budget_lamports}", now_ms)
                if why:
                    return why
                self.append({"ev": "send_intent", "ts_ms": now_ms, "mint": mint, "signature": signature, "stake_lamports": stake,
                             "sol_in_lamports": 0, "sol_out_lamports": 0, "estimated": False})
        except Exception:
            return "lane_ledger_error"
        return None

    def record(self, ev: str, mint: str, marks: dict[str, tuple[int, int]], now_ms: int, *, sol_in: int = 0, sol_out: int = 0, fee_network: int = 0,
               fee_priority: int = 0, fee_tip: int = 0, rent_unreclaimed: int = 0, rent_refunded: int = 0, signature: str | None = None,
               estimated: bool = False, **extra: Any) -> LaneView | None:
        """Persist a fill / fee / exit / writeoff row in the backlog FIRST, then drain it into the ledger. If the drain fails the row stays in the
        backlog (entries are refused until it lands, also after a restart) and the error is raised. Writes the STOP file if the budget is gone."""
        row = {"schema": LANE_SCHEMA, "bot_id": self.cfg.bot_id, "row_id": uuid.uuid4().hex, "ev": ev, "ts_ms": now_ms, "mint": mint, "signature": signature,
               "sol_in_lamports": int(sol_in), "sol_out_lamports": int(sol_out), "fee_network_lamports": int(fee_network),
               "fee_priority_lamports": int(fee_priority), "fee_tip_lamports": int(fee_tip), "rent_unreclaimed_lamports": int(rent_unreclaimed),
               "rent_refunded_lamports": int(rent_refunded), "estimated": bool(estimated), **extra}
        self._write_line(self.backlog_path, row)  # on disk before anything else can fail
        with self.locked():
            rows = self.read()
            view0 = lane_fold(rows, marks, now_ms, self.cfg.mark_stale_s, self.cfg.bot_id)
            self._snap_locked(view0, now_ms)
            self.drain_locked()
            view = lane_fold(self.read(), marks, now_ms, self.cfg.mark_stale_s, self.cfg.bot_id)
            if view.pnl <= -self.cfg.budget_lamports:
                self.write_stop(f"lane_budget_stop pnl_lamports={view.pnl} budget_lamports={self.cfg.budget_lamports}", now_ms)
        return view


def day_of(ms: int) -> str:
    return h.day_key(ms)


_Base = h.H5Executor  # captured at import: h.main is run with h.H5Executor rebound to the lane class, and then `h.H5Executor` is not the parent


class H5LaneExecutor(_Base):
    """H5Executor plus the lane. With no `lane` key in the config it adds nothing."""

    def __init__(self, rpc, cfg: dict[str, Any], keypair, **kw):
        self.lane: LaneLedger | None = None
        self._lane_cfg: LaneConfig | None = None
        self._lane_marks: dict[str, tuple[int, int]] = {}  # mint -> (value in lamports, quote ms): the last fresh exit quote of an open position
        self._lane_orphans: set[str] = set()  # this bot's open lane rows with no state entry: unresolved intents
        self._lane_recon_ms = 0
        self._lane_last_fee_sig: str | None = None
        self._lane_prior_unresolved: list[str] = []
        if "lane" in cfg:  # parsed before the base __init__, which reads `self.h5`
            try:
                self._lane_cfg = LaneConfig.parse(cfg["lane"])
            except ValueError as exc:
                raise SystemExit(f"lane refused: {exc}") from None
            if cfg.get("stake_lamports") not in (None, self._lane_cfg.stake_lamports):
                raise SystemExit("lane refused: top-level stake_lamports must be absent or equal lane.stake_lamports")
        super().__init__(rpc, cfg, keypair, **kw)
        if self._lane_cfg is not None:
            if not LANE_STAKE_MIN_LAMPORTS <= self.h5.stake_lamports <= LANE_STAKE_MAX_LAMPORTS:
                raise SystemExit(f"lane refused: the effective stake {self.h5.stake_lamports} (min of the tier's and lane.stake_lamports) is outside "
                                 f"[{LANE_STAKE_MIN_LAMPORTS}, {LANE_STAKE_MAX_LAMPORTS}]: set the tier file to a tier whose stake is at least the lane's")
            self.lane = LaneLedger(self._lane_cfg)
            self._lane_bot_lock = self._take_bot_lock()
            if not self.dry_run:
                self._lane_record("start", "", code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                  base_code_sha256=hashlib.sha256(Path(h.__file__).read_bytes()).hexdigest(), executor_ledger=str(self.fills.path),
                                  limits=asdict(self.h5), lane=_public(self._lane_cfg))
                self._lane_reconcile(self.now_ms(), force=True)

    def _take_bot_lock(self):
        """One process per bot_id: an exclusive flock on <ledger>.bot.<bot_id>.lock, held for the life of the process. A second unit with the same
        bot_id would reconcile the first one's open positions as orphans and write them off."""
        assert self.lane is not None
        f = open(f"{self.lane.path}.bot.{self.lane.cfg.bot_id}.lock", "a")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise SystemExit(f"lane refused: another process holds bot_id {self.lane.cfg.bot_id!r} on this ledger") from None
        return f

    def release_lane_lock(self) -> None:
        f = getattr(self, "_lane_bot_lock", None)
        if f is not None:
            f.close()
            self._lane_bot_lock = None

    # -- limits: the lane only lowers ------------------------------------------------------------------------------------------
    @property
    def h5(self) -> h.H5Limits:
        lim = super().h5  # the parent's property (tier, config, tipauth stake cap)
        c = self._lane_cfg
        if c is None:
            return lim
        return replace(lim, stake_lamports=min(lim.stake_lamports, c.stake_lamports), max_open=min(lim.max_open, c.max_open),
                       daily_loss_lamports=min(lim.daily_loss_lamports, c.daily_loss_lamports), total_loss_lamports=min(lim.total_loss_lamports, c.budget_lamports))

    # -- entry gates -----------------------------------------------------------------------------------------------------------
    def _kill_reason(self) -> str | None:
        why = super()._kill_reason()
        if why or self.lane is None:
            return why
        return "lane_stop_file" if self.lane.stop_present() else None  # new entries only: no sell path reads _kill_reason

    def _seal_reason(self, trg: h.H5Trigger, now: int) -> str | None:
        """DEC-035 s7: the lane is not stopped for seals (the disclosure is a ledger entry). The parent returns only the CAP-PICK oracle reasons
        (h.SEAL_REASONS: seal_window_no_oracle, seal_pick, seal_oracle_error) or None; with the lane on exactly those are waived, and anything
        else the parent may ever return passes through. Lane off: the parent unchanged."""
        why = super()._seal_reason(trg, now)
        if self.lane is not None and why in h.SEAL_REASONS:
            return None
        return why

    def _hard_refusal(self, trg: h.H5Trigger, now: int) -> str | None:
        why = super()._hard_refusal(trg, now)
        if why or self.lane is None or self.dry_run:
            return why
        if self._lane_orphans:
            return "lane_unresolved_intent"
        return self.lane.check(self._lane_marks, now, self.h5.stake_lamports)  # read-only; the send is reserved under the lock in _send

    def _send(self, p: dict[str, Any], mint: str) -> None:
        if self.lane is None or self.dry_run or p["kind"] != "buy" or p.get("sends", 0) != 0:
            return super()._send(p, mint)
        if self._kill_reason() or self._live_gate() or self._halt_present():
            return super()._send(p, mint)  # the base refuses and cancels the unsent buy by its own rules
        why = self.lane.reserve(mint, p["signature"], self._lane_marks, self.now_ms(), self.h5.stake_lamports)  # check + fsynced intent row
        if why:
            self.state.pending.pop(mint, None)
            self._log("send_blocked", mint, reason=why, kind_="buy")
            return
        super()._send(p, mint)
        if mint not in self.state.pending:  # the base cancelled it after our row went in: nothing was sent
            self._lane_record("writeoff", mint, reason="not_sent", signature=p.get("signature"))

    # -- books: fills, fees, exits, write-offs -----------------------------------------------------------------------------------
    def _lane_record(self, ev: str, mint: str, **kw: Any) -> None:
        """Append one lane row. A failed drain is an alert; the row is already in the persisted backlog, which refuses entries until it lands (also after a
        restart). It never stops an exit."""
        if self.lane is None or self.dry_run:
            return
        try:
            self.lane.record(ev, mint, self._lane_marks, self.now_ms(), **kw)
        except Exception as exc:
            self._alert("lane_ledger_write_failed", mint, ev=ev, label=type(exc).__name__)
            if not self.lane.backlog_pending():  # not even the backlog took it: stop entries by file, which survives a restart
                with contextlib.suppress(Exception):
                    self.lane.write_stop(f"lane_ledger_write_failed ev={ev}", self.now_ms())
        if ev in ("exit", "writeoff"):
            self._lane_marks.pop(mint, None)

    @staticmethod
    def _fee_parts(fee: int) -> dict[str, int]:
        return {"fee_network": min(fee, tx.BASE_FEE_PER_SIGNATURE), "fee_priority": max(0, fee - tx.BASE_FEE_PER_SIGNATURE)}

    @pe.critical
    def _finish_buy(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        super()._finish_buy(mint, p, m)
        if self.lane is None:
            return
        fee, sig = int(m["fee"]), p.get("signature")
        if m["err"] is not None:  # landed with an error: only the fee was paid
            return self._lane_record("fill", mint, ok=False, sol_out=-int(m["sol_delta"]), signature=sig, estimated=bool(m.get("estimated")), **self._fee_parts(fee))
        out, rent, est = -int(m["sol_delta"]), int(m.get("ata_rent_post") or 0), bool(m.get("estimated"))
        if est and rent == 0:  # no meta for 60 s: the estimate has no token-account rent. Charge it (the closing sell's refund is credited), never 0.
            rent = self._ata_rent_estimate(p)
            out += rent
        # the whole wallet delta of the buy: stake, fees and the token-account rent paid up front (refunded only when a close is confirmed)
        self._lane_record("fill", mint, ok=True, sol_out=out, signature=sig, estimated=est, rent_paid_lamports=rent, **self._fee_parts(fee))
        if mint not in self.state.open:  # landed with zero tokens: the SOL is gone and there is no position
            self._lane_record("writeoff", mint, reason="zero_tokens", rent_unreclaimed=rent, signature=sig)

    def _ata_rent_estimate(self, p: dict[str, Any]) -> int:
        try:
            v = int(self.rpc("getBalance", [p["base_ata"], {"commitment": self.commitment}])["value"])
            if v > 0:
                return v
        except (Exception, SystemExit):
            pass
        return pe.FEEARM_RENT_RESERVE_LAMPORTS

    @pe.critical
    def _finish_sell(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        super()._finish_sell(mint, p, m)
        if self.lane is None:
            return
        fee, est, sig = int(m["fee"]), bool(m.get("estimated")), p.get("signature")
        if m["err"] is not None:  # a failed sell paid its fee; the position stays open
            return self._lane_record("fee", mint, sol_out=-int(m["sol_delta"]), reason="sell_failed", signature=sig, estimated=est, **self._fee_parts(fee))
        # gross proceeds (pool leg plus the rent refund of the confirmed close); the sell's own fee goes out in the same row
        self._lane_record("exit", mint, sol_in=int(m["sol_delta"]) + fee, sol_out=fee, rent_refunded=int(m.get("ata_rent_pre") or 0), signature=sig,
                          estimated=est, **self._fee_parts(fee))

    @pe.critical
    def _resolve_expired(self, mint: str, p: dict[str, Any]) -> None:
        super()._resolve_expired(mint, p)
        if self.lane is not None and p.get("kind") == "buy":
            self._lane_record("writeoff", mint, reason="buy_expired", signature=p.get("signature"))  # an unlanded buy: closed, no SOL moved

    def _sell_fee(self, entry: dict[str, Any]) -> int:
        self._lane_last_fee_sig = entry.get("signature")  # the next _book_fee is for this signature
        return super()._sell_fee(entry)

    def _book_fee(self, mint: str, fee: int, why: str) -> None:
        super()._book_fee(mint, fee, why)
        if self.lane is not None:  # a superseded or failed sell paid its fee: an estimate (its meta is not fetched), so flagged
            self._lane_record("fee", mint, sol_out=fee, reason=why, signature=self._lane_last_fee_sig, estimated=True, **self._fee_parts(fee))

    def _log(self, kind: str, mint: str, **kw: Any) -> None:
        super()._log(kind, mint, **kw)
        if kind == "sell_prior_unresolved" and self.lane is not None:
            self._lane_prior_unresolved.append(str(kw.get("signature")))

    def _settle_priors(self, mint: str, priors: list[dict[str, Any]]) -> None:
        self._lane_prior_unresolved = []
        super()._settle_priors(mint, priors)
        if self.lane is None:
            return
        for x in priors:  # one with no status yet may still land and fail later: its fee is booked now (fail closed), estimated
            if x.get("signature") in self._lane_prior_unresolved:
                fee = self._sell_fee(x)
                self._lane_record("fee", mint, sol_out=fee, reason="prior_unresolved", signature=x.get("signature"), estimated=True, **self._fee_parts(fee))

    def _sell_failed(self, pos: dict[str, Any]) -> None:
        was = bool(pos.get("abandoned"))
        super()._sell_failed(pos)
        if self.lane is not None and pos.get("abandoned") and not was:
            self._lane_record("writeoff", pos["mint"], reason="sell_abandoned")  # value 0; the SOL already spent is in cash

    def _arm_sell(self, mint: str, pos: dict[str, Any], level: int, emergency: bool, est: int | None = None, fresh: bool = False):
        rec = super()._arm_sell(mint, pos, level, emergency, est=est, fresh=fresh)
        if self.lane is not None and rec and rec.get("q_out"):
            self._lane_marks[mint] = (int(rec["q_out"]), self.now_ms())  # the lane's mark: the exit quote, stale after lane.mark_stale_s
        return rec

    # -- loop: backlog and unresolved intents ----------------------------------------------------------------------------------------
    def tick(self) -> int:
        n = super().tick()
        if self.lane is not None and not self.dry_run:
            try:
                self.lane.drain()
            except Exception as exc:
                self._alert("lane_backlog_drain_failed", "", label=type(exc).__name__)
            self._lane_reconcile(self.now_ms())
        return n

    def _lane_reconcile(self, now: int, force: bool = False) -> None:
        """Write-ahead recovery. A `send_intent` of this bot with no state entry (a crash between the row and the state save, or a lost position) is
        looked up by signature. Failed: its fee. Landed: its real cost, written off with an alert (the state is gone, so no exit can run). No
        answer: counted open at its full stake until LANE_UNRESOLVED_MS, then written off at its full cost (stake, rent reserve, buy fee)."""
        assert self.lane is not None
        if not force and now - self._lane_recon_ms < LANE_RECONCILE_EVERY_MS:
            return
        if self.lane.backlog_pending():
            return  # a row waiting in the backlog (an exit, say) could make a live position look orphaned
        self._lane_recon_ms = now
        try:
            with self.lane.locked():
                view = lane_fold(self.lane.read(), self._lane_marks, now, self.lane.cfg.mark_stale_s, self.lane.cfg.bot_id)
        except Exception as exc:
            self._lane_orphans = {"?"}  # an unreadable ledger blocks entries
            self._alert("lane_ledger_unreadable", "", label=type(exc).__name__)
            return
        mine = self.lane.cfg.bot_id + ":"
        orphans = {k[len(mine):]: p for k, p in view.open.items() if k.startswith(mine)
                   and k[len(mine):] not in self.state.pending and k[len(mine):] not in self.state.open}
        self._lane_orphans = set(orphans)
        for mint, p in orphans.items():
            try:
                st = self.rpc("getSignatureStatuses", [[p["signature"]], {"searchTransactionHistory": True}])["value"][0]
            except (Exception, SystemExit):
                continue  # unknown: still open at full stake
            if st is None:
                if now - p["ts_ms"] >= LANE_UNRESOLVED_MS:
                    full = p["stake"] + pe.FEEARM_RENT_RESERVE_LAMPORTS + tx.BASE_FEE_PER_SIGNATURE + self.h5.buy_priority_lamports
                    self._alert("lane_intent_written_off", mint, signature=p["signature"], age_ms=now - p["ts_ms"])
                    self._lane_record("writeoff", mint, reason="unresolved_intent_10min", sol_out=full, signature=p["signature"], estimated=True)
                    self._lane_orphans.discard(mint)
                continue
            try:
                res = self.rpc("getTransaction", [p["signature"], {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}])
                meta = res["meta"]
                delta = int(meta["postBalances"][0]) - int(meta["preBalances"][0])  # the fee payer is our wallet
                fee = int(meta["fee"])
            except (Exception, SystemExit, TypeError, KeyError, IndexError):
                continue
            if st.get("err") is not None:
                self._lane_record("fill", mint, ok=False, sol_out=-delta, signature=p["signature"], **self._fee_parts(fee))
            else:
                self._alert("lane_landed_without_state", mint, signature=p["signature"])
                self._lane_record("fill", mint, ok=True, sol_out=-delta, signature=p["signature"], **self._fee_parts(fee))
                self._lane_record("writeoff", mint, reason="landed_state_missing", signature=p["signature"])
            self._lane_orphans.discard(mint)


def _public(c: LaneConfig) -> dict[str, Any]:
    d = asdict(c)
    d["ledger_path"], d["stop_path"] = str(c.ledger_path), str(c.stop_path)
    return d


def lane_writeoff(cfg: dict[str, Any], mint: str, reason: str, now_ms: int | None = None) -> int:
    """Offline: close a lane position by hand (a position closed with --mark-closed, a lost state) with a `writeoff` row appended under the ledger lock.
    Value 0, no SOL moves in the row: what was spent is already in the ledger as cash."""
    lc = LaneConfig.parse(cfg.get("lane"))
    ledger = LaneLedger(lc)
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    with ledger.locked():
        view = lane_fold(ledger.read(), {}, now, lc.mark_stale_s, lc.bot_id)
        if f"{lc.bot_id}:{mint}" not in view.open:
            print(f"lane_writeoff: {mint} is not open in the lane ledger for bot_id {lc.bot_id}; nothing written")
            return 1
        ledger.append({"ev": "writeoff", "ts_ms": now, "mint": mint, "reason": f"manual:{reason}", "sol_in_lamports": 0, "sol_out_lamports": 0, "estimated": False})
    print(f"lane_writeoff: {mint} written off ({reason})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """tools.h5_executor.main with H5LaneExecutor in place of H5Executor, plus --lane-writeoff. A config without `lane` is refused here: the lane unit
    must be a lane unit."""
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--config")
    ap.add_argument("--lane-writeoff", metavar="MINT")
    ap.add_argument("--reason", default="")
    args, _rest = ap.parse_known_args(argv)
    if args.config:
        cfg = json.loads(Path(args.config).read_text())
        if "lane" not in cfg:
            raise SystemExit("h5_lane_executor: the config has no `lane` key")
        if args.lane_writeoff:
            if not args.reason:
                raise SystemExit("--lane-writeoff needs --reason")
            return lane_writeoff(cfg, args.lane_writeoff, args.reason)
    orig = _Base
    h.H5Executor = H5LaneExecutor  # type: ignore[misc]  # h.main builds the executor by this global name
    try:
        return h.main(argv)
    finally:
        h.H5Executor = orig  # type: ignore[misc]


if __name__ == "__main__":
    sys.exit(main())
