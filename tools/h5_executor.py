"""H5-BOOSTFLOOR live executor (RULE H5-BOOSTFLOOR v1, frozen 2026-10-08).

Turns H5 trigger events into PumpSwap buys and slot-timed sells. DRY RUN is the default: it builds and simulates
transactions with a public payer and never signs, sends or touches a key. Live needs ALL of: config "mode": "live",
the --live flag, a LIVE_OK file the manager creates, EXP-024 Part 1 present in the deployed tree, an explicit end
instant, and the key from the systemd credential (tools.probe_live.load_probe_key; there is no path override).

This is a build, not a claim. It trades nothing by itself and is not gate evidence.

What is reused from the DEC-019 probe (nothing is copied; this module subclasses and calls it):
  tools.probe_live    LiveExecutor (write-ahead state, send, rebroadcast, confirm loop, getTransaction meta, fill and
                      sell bookkeeping, stuck handling), validate_message (pre-signing allowlist), parse_meta,
                      classify_failure, BlockhashCache, close_token_account, load_probe_key, harden_process
  tools.probe_executor Limits/State/FillLog, LimitedRpc/ProbeRpc, fetch_snapshot/Snapshot/StaticCache, entry_quote,
                      exit_check, fee_ppm_for, sell_probe_message, sim_message, STOP/HALT file checks
  tools.pumpswap_tx   build_buy/buy_instructions/sell_instructions, cp_buy_out, min_out_with_slippage, canonical_pool

Trigger interface (the shadow detector writes JSONL rows; this module only reads them, it has no detection logic):
  h5_intent_v1  mint, pool, s0_slot, sps, trigger_slot, q_lamports (post-trade real quote + V), base_reserve
                (post-trade raw base), v_lamports (per-print V), decision_ms (wall clock of the decision)
  h5_feed_v1    gap (bool), ms                 feed gap / heartbeat
  h5_boost_v1   mint, s0_slot, sps, last_slice_slot and/or last_slice_s   BOOST last-slice timing per tracked pool
  h5_watch_v1   mint, pool                     optional: pool first seen, lets us pre-fetch static accounts
The same entry points exist as callbacks: handle_trigger(H5Trigger), on_feed_status(gap), on_boost_row(...).
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from tools import probe_executor as pe
from tools import probe_live as pl
from tools import pumpswap_simulate as sim
from tools import pumpswap_tx as tx

RULE_ID = "H5-BOOSTFLOOR-v1"
SCHEMA_INTENT, SCHEMA_FEED, SCHEMA_BOOST, SCHEMA_WATCH = "h5_intent_v1", "h5_feed_v1", "h5_boost_v1", "h5_watch_v1"
SCHEMA_LEDGER = "h5_ledger_v1"
DRYRUN, LIVE = "dryrun", "live"

# --- the frozen rule's own numbers (code constants, never config) ---------------------------------------------------
HOLD_S = 330.0  # exit trigger at s0 + round(330 s / sps)
RULE_TRIGGER_WINDOW_S = 300.0
RULE_Q_MAX_LAMPORTS = 40 * 10**9  # trigger needs post-trade Q = quote + V <= 40 SOL
SPS_MIN, SPS_MAX = 0.15, 0.6
ESCALATE_S = 345.0  # from here on a sell retry uses the escalated ladder level
EXP024_PART1 = "EXP/EXP-024-h5-boostfloor-part1-prereg.md"  # live is honoured only if this is in the deployed tree

# --- live-halt rules (coordinator brief 2026-10-08, PLAN.md section 5). Fixed in code. --------------------------------
BOOST_HALT_S = 335.0  # BOOST last slice earlier than this after s0 on any tracked pool -> halt
BOOST_HALT_TWICE_S = 337.0  # earlier than this on two distinct pools in one UTC day -> halt
LATE_SELL_S = 335.0  # a sell landing after s0 + this is late
LATE_SELL_FRAC = 0.05  # more than 5% of our landed sells late -> halt
LANDING_MEDIAN_MAX_S = 3.0  # median trigger-to-landing over the last LANDING_WINDOW fills above this -> halt
LANDING_WINDOW = 10

# --- EXP-022 seal guard ---------------------------------------------------------------------------------------------
SEAL_START_MS = 1792112400000  # 2026-10-16T01:00:00Z
ORACLE_EARLIEST_MS = 1792116000000  # 2026-10-16T02:00:00Z: the DEC-016 FINAL is written about now, not before
SEAL_END_DEFAULT_MS = 1793930400000  # 2026-11-06T02:00:00Z. Config may move the end later, never earlier.
SEAL_REASONS = frozenset({"seal_window_no_oracle", "seal_pick", "seal_oracle_error"})  # counted, never logged per mint

# --- limits: code maxima, config can only lower (floors: config can only raise) ---------------------------------------
H5_DEFAULT = {
    "stake_lamports": 20_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 80_000_000,
    "total_loss_lamports": 120_000_000, "max_attempts": 120, "max_days": 10, "buy_priority_lamports": 55_000,
    "sell_priority_lamports": 55_000, "escalated_priority_lamports": 150_000, "entry_tolerance_bps": 1500,
}
H5_MAX = {
    "stake_lamports": 50_000_000, "max_open": 3, "max_trades_per_day": 30, "daily_loss_lamports": 80_000_000,
    "total_loss_lamports": 120_000_000, "max_attempts": 300, "max_days": 10, "buy_priority_lamports": 150_000,
    "sell_priority_lamports": 150_000, "escalated_priority_lamports": 150_000, "entry_tolerance_bps": 3000,
}
MIN_WALLET_FLOOR_LAMPORTS = 20_000_000  # config may raise
DEFAULT_WALLET_FLOOR_LAMPORTS = 50_000_000
RENT_RESERVE_LAMPORTS = 2_100_000  # token ATA rent is paid up front on a buy and refunded on the closing sell
SELL_RESERVE_LAMPORTS = 1_000_000  # worst-case fees of one exit (escalated priority, a few attempts)
H5_MAX_RPS = 10.0
SELL_LADDER_SLIP_BPS = (1500, 1500, 3500)  # min_out = quote * (1 - slip): 0.85, 0.85 (fresh quote), 0.65
EMERGENCY_MIN_OUT = 1  # lamport. Never 0; emergency only, at most the position's value is exposed
MAX_ARM_AGE_MS = 8_000  # a precomputed sell older than this is rebuilt before it is sent
BALANCE_MAX_AGE_MS = 30_000


# --- small pure helpers -----------------------------------------------------------------------------------------------


def day_key(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))


def ceil_slots(seconds: float, sps: float) -> int:
    """Same rounding as the frozen scorer: math.ceil(x / sps - 1e-9)."""
    return max(0, math.ceil(seconds / sps - 1e-9))


@dataclass(frozen=True)
class H5Limits:
    stake_lamports: int
    max_open: int
    max_trades_per_day: int
    daily_loss_lamports: int
    total_loss_lamports: int
    max_attempts: int
    max_days: float
    buy_priority_lamports: int
    sell_priority_lamports: int
    escalated_priority_lamports: int
    entry_tolerance_bps: int
    wallet_floor_lamports: int = DEFAULT_WALLET_FLOOR_LAMPORTS
    send_lead_ms: int = 500  # measured send-to-land of a sell (probe: p50 about 480 ms); the sell is sent this early
    exit_land_offset_s: float = 0.0  # 0: land AT s0 + round(330/sps) (coordinator). The rule's sim lands 0.55 s later.
    deadline_s: float = 400.0  # never hold past s0 + this: emergency market sell
    arm_lead_ms: int = 2_000  # the sell is built and signed this long before it is sent
    rebroadcast_ms: int = 400
    status_poll_ms: int = 500
    slot_poll_ms: int = 200
    slot_resync_ms: int = 20_000
    max_trigger_age_ms: int = 10_000
    pool_cache_ttl_ms: int = 600_000
    buy_cu_limit: int = tx.DEFAULT_BUY_CU_LIMIT
    sell_cu_limit: int = tx.DEFAULT_SELL_CU_LIMIT
    track_volume: bool = True  # the probe's proven live shape; set false only after a dry-run simulate comparison
    late_sell_min_n: int = 1  # literal reading of "more than 5% of sells"; config may only raise it
    dry_run_balance_lamports: int = 250_000_000  # dry run has no wallet: balance guard runs against this
    end_ms: int | None = None

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "H5Limits":
        def num(key: str, default: Any, lo: float, hi: float, as_int: bool = True) -> Any:
            v = cfg.get(key)
            v = default if v is None else v
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"{key} must be a finite number")
            if v < lo:
                raise ValueError(f"{key} must be >= {lo}")
            v = min(v, hi)  # config can lower a maximum, never raise it
            return int(v) if as_int else float(v)

        if cfg.get("jito_enabled") or (cfg.get("jito_tip_lamports") or 0) != 0:
            raise ValueError("jito tips are not supported in this build (jito_enabled must be false, jito_tip_lamports 0)")
        kw: dict[str, Any] = {}
        for key, default in H5_DEFAULT.items():
            kw[key] = num(key, default, 1, H5_MAX[key], as_int=(key != "max_days"))
        kw["max_days"] = num("max_days", H5_DEFAULT["max_days"], 0.01, H5_MAX["max_days"], as_int=False)
        kw["wallet_floor_lamports"] = int(max(MIN_WALLET_FLOOR_LAMPORTS, num("wallet_floor_lamports", DEFAULT_WALLET_FLOOR_LAMPORTS, 0, 10**12)))
        kw["send_lead_ms"] = num("send_lead_ms", 500, 0, 3_000)
        kw["exit_land_offset_s"] = num("exit_land_offset_s", 0.0, 0.0, 1.0, as_int=False)
        kw["deadline_s"] = num("deadline_s", 400.0, ESCALATE_S, 400.0, as_int=False)
        kw["arm_lead_ms"] = num("arm_lead_ms", 2_000, 500, 10_000)
        kw["rebroadcast_ms"] = num("rebroadcast_ms", 400, 200, 5_000)
        kw["status_poll_ms"] = num("status_poll_ms", 500, 250, 5_000)
        kw["slot_poll_ms"] = num("slot_poll_ms", 200, 100, 2_000)
        kw["slot_resync_ms"] = num("slot_resync_ms", 20_000, 1_000, 60_000)
        kw["max_trigger_age_ms"] = num("max_trigger_age_ms", 10_000, 1_000, 10_000)
        kw["pool_cache_ttl_ms"] = num("pool_cache_ttl_ms", 600_000, 60_000, 900_000)
        kw["buy_cu_limit"] = num("buy_cu_limit", tx.DEFAULT_BUY_CU_LIMIT, 50_000, 400_000)
        kw["sell_cu_limit"] = num("sell_cu_limit", tx.DEFAULT_SELL_CU_LIMIT, 50_000, 400_000)
        kw["late_sell_min_n"] = num("late_sell_min_n", 1, 1, 10_000)
        kw["dry_run_balance_lamports"] = num("dry_run_balance_lamports", 250_000_000, 0, 10**12)
        tv = cfg.get("track_volume", True)
        if not isinstance(tv, bool):
            raise ValueError("track_volume must be a boolean")
        kw["track_volume"] = tv
        e = cfg.get("end_ms")
        if e is not None:
            if isinstance(e, bool) or not isinstance(e, (int, float)) or not math.isfinite(e) or e <= 0:
                raise ValueError("end_ms must be a finite positive number")
            kw["end_ms"] = int(e)
        return cls(**kw)


# --- trigger interface ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class H5Trigger:
    mint: str
    pool: str
    s0_slot: int
    sps: float
    trigger_slot: int
    q_lamports: int  # the rule's Q_i: post-trade real quote + V
    base_reserve: int  # post-trade raw base reserve
    v_lamports: int  # per-print V (logged; Q already contains it)
    decision_ms: int

    def public(self) -> dict[str, Any]:
        return asdict(self)

    def log_fields(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k != "mint"}  # the ledger row carries the mint itself


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_trigger(row: Any) -> tuple[H5Trigger | None, str | None]:
    """(trigger, None) for a good h5_intent_v1 row, (None, reason) for a malformed one, (None, None) for any other schema.
    Only the whitelisted fields are read; anything else in the row is dropped."""
    if not isinstance(row, dict) or row.get("schema") != SCHEMA_INTENT:
        return None, None
    try:
        mint, pool = row["mint"], row["pool"]
        if not (isinstance(mint, str) and isinstance(pool, str) and mint and pool):
            return None, "bad_intent:ids"
        for k in ("s0_slot", "trigger_slot", "q_lamports", "base_reserve", "v_lamports", "decision_ms"):
            if not _is_int(row[k]) or row[k] <= 0:
                return None, f"bad_intent:{k}"
        sps = row["sps"]
        if isinstance(sps, bool) or not isinstance(sps, (int, float)) or not math.isfinite(sps) or not (SPS_MIN < sps < SPS_MAX):
            return None, "bad_intent:sps"
        if row["trigger_slot"] < row["s0_slot"]:
            return None, "bad_intent:slot_order"
        if row["q_lamports"] < row["v_lamports"]:
            return None, "bad_intent:q_below_v"
    except KeyError as exc:
        return None, f"bad_intent:missing_{exc.args[0]}"
    return H5Trigger(mint, pool, row["s0_slot"], float(sps), row["trigger_slot"], row["q_lamports"], row["base_reserve"],
                     row["v_lamports"], row["decision_ms"]), None


# --- slot arithmetic (the part the tests pin at 200 ms and 400 ms slots) --------------------------------------------


@dataclass(frozen=True)
class ExitPlan:
    s0_slot: int
    sps: float
    exit_slot: int  # s0 + round(330 / sps): the rule's exit trigger
    land_slot: int  # where the sell should LAND (exit_slot + the optional offset)
    send_slot: int  # land_slot minus the send-to-land lead: when the signed sell is sent
    arm_slot: int  # when the sell is built and signed
    escalate_slot: int  # s0 + round(345 / sps)
    deadline_slot: int  # s0 + round(400 / sps): emergency market sell, hard
    late_slot: int  # s0 + round(335 / sps): a sell landing after this is late

    def public(self) -> dict[str, Any]:
        return asdict(self)


def exit_plan(s0_slot: int, sps: float, h5: H5Limits) -> ExitPlan:
    exit_slot = s0_slot + int(round(HOLD_S / sps))  # python round, as the frozen scorer (s14_boostdip.py)
    land_slot = exit_slot + ceil_slots(h5.exit_land_offset_s, sps)
    send_slot = land_slot - ceil_slots(h5.send_lead_ms / 1000.0, sps)
    arm_slot = send_slot - ceil_slots(h5.arm_lead_ms / 1000.0, sps)
    return ExitPlan(s0_slot, sps, exit_slot, land_slot, send_slot, arm_slot, s0_slot + int(round(ESCALATE_S / sps)),
                    s0_slot + int(round(h5.deadline_s / sps)), s0_slot + int(round(LATE_SELL_S / sps)))


def entry_terms(q_lamports: int, base_reserve: int, stake: int, tol_bps: int) -> dict[str, Any]:
    """Expected tokens at the trigger's post-trade state and the min_out that refuses a fill if the price moved up by
    more than the tolerance: min_out = floor(expected / (1 + X)). Reuses probe_executor.entry_quote."""
    snap = pe.Snapshot(None, 0, q_lamports, base_reserve, 0)  # quote_priced = q_lamports + 0; ps is not used by entry_quote
    q = pe.entry_quote(snap, stake)
    return {**q, "expected_tokens": q["tokens"], "min_out": q["tokens"] * 10_000 // (10_000 + tol_bps)}


def balance_need(stake: int, buy_priority: int, n_open: int, pending_buy_stake: int, floor: int) -> int:
    """Lamports the wallet must hold before a buy. Open positions are already out of the balance (their cost left it),
    so assuming they all go to zero is already in the balance; what is added is everything still to be paid: this
    stake, this buy's fees and rent, the exit fees of every open position plus this one, stakes in flight, the floor."""
    return stake + buy_priority + tx.BASE_FEE_PER_SIGNATURE + RENT_RESERVE_LAMPORTS + (n_open + 1) * SELL_RESERVE_LAMPORTS + pending_buy_stake + floor


class SlotClock:
    """Dead-reckoned slot estimate anchored on getSlot (processed). Between polls it advances by wall time / sps."""

    def __init__(self) -> None:
        self.slot: int | None = None
        self.wall_ms = 0

    def observe(self, slot: int, wall_ms: int) -> None:
        if self.slot is None or slot >= self.slot:
            self.slot, self.wall_ms = slot, wall_ms

    def est(self, now_ms: int, sps: float) -> int | None:
        if self.slot is None:
            return None
        return self.slot + max(0, int((now_ms - self.wall_ms) / (sps * 1000.0)))

    def age(self, now_ms: int) -> int:
        return 10**12 if self.slot is None else now_ms - self.wall_ms


# --- persistence, lock, ledger ------------------------------------------------------------------------------------------


def _atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        fh.write(json.dumps(obj, separators=(",", ":")))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass
class H5Counters:
    """Persisted beside the probe State: per-day trade and realized counters, the latched halts, the rolling landing
    times, the late-sell tally, the BOOST last-slice sightings and the seal-skip count. A restart cannot reset them."""

    run_mode: str = DRYRUN
    days: dict[str, dict[str, Any]] = field(default_factory=dict)
    halts: dict[str, dict[str, Any]] = field(default_factory=dict)
    landing_s: list[float] = field(default_factory=list)
    sells_landed: int = 0
    sells_late: int = 0
    seal_skips: int = 0

    def day(self, key: str) -> dict[str, Any]:
        return self.days.setdefault(key, {"trades": 0, "realized": 0, "boost_lt337": []})

    def save(self, path: Path) -> None:
        for old in sorted(self.days)[:-14]:
            del self.days[old]
        _atomic_json(path, {"schema": "h5_counters_v1", **self.__dict__})

    @classmethod
    def load(cls, path: Path, run_mode: str) -> "H5Counters":
        if not path.exists():
            return cls(run_mode=run_mode)
        raw = json.loads(path.read_text())
        c = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        if c.run_mode != run_mode:
            raise SystemExit(f"counters file run_mode {c.run_mode!r} does not match {run_mode!r}")
        return c


def acquire_lock(path: Path) -> int:
    """Single instance: an exclusive non-blocking flock held for the life of the process (dry run and live share it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise SystemExit("h5_executor: another instance holds the lock; refusing to start") from None
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


class H5Ledger(pe.FillLog):
    """The JSONL ledger: every decision, refusal, send, fill, exit and halt. Same writer as the probe's fill log."""

    def write(self, row: dict[str, Any]) -> None:
        super().write({"schema": SCHEMA_LEDGER, **row})


class PublicOnly:
    """Dry-run stand-in for a Keypair: a public key, no signing capability at all."""

    def __init__(self, pubkey: Pubkey):
        self._pk = pubkey

    def pubkey(self) -> Pubkey:
        return self._pk

    def __repr__(self) -> str:
        return f"PublicOnly({self._pk})"


# --- start conditions ---------------------------------------------------------------------------------------------------


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def live_ok_present(path: str | Path) -> bool:
    p = Path(path)
    return p.is_file() and not p.is_symlink()


def exp024_part1_present(root: Path) -> bool:
    """EXP-024 Part 1 is merged on main at the deployed commit: the pre-registration file is in the deployed tree. (The
    pinned installer deploys a main sha; this checks the tree it deployed, it does not shell out to git.)"""
    p = root / EXP024_PART1
    try:
        return p.is_file() and not p.is_symlink() and p.stat().st_size > 0
    except OSError:
        return False


def start_refusal(cfg: dict[str, Any], root: Path | None = None) -> str | None:
    """The live start conditions, keyless. None = may start live."""
    root = root or repo_root()
    if not live_ok_present(cfg_path(cfg, "live_ok_file", "LIVE_OK")):
        return "live_ok_missing"
    if not exp024_part1_present(root):
        return "exp024_part1_missing"
    if cfg.get("end_ms") is None:
        return "end_ms_missing"
    return None


def cfg_path(cfg: dict[str, Any], key: str, name: str) -> Path:
    return Path(cfg[key]) if cfg.get(key) else Path(cfg["state_dir"]) / name


def build_probe_cfg(cfg: dict[str, Any], h5: H5Limits, run_mode: str) -> dict[str, Any]:
    """The cfg the probe base classes want. State, ledger and counters live in <state_dir>/<mode>/ so a dry run can never
    consume the live budget, and nothing is shared with the probe's own files under /var/lib/mal-live."""
    sd = Path(cfg["state_dir"]) / run_mode
    pc: dict[str, Any] = {
        "signals_dir": str(Path(cfg["intents_file"]).parent), "state_dir": str(sd), "fill_log": str(sd / "h5-ledger.jsonl"),
        "stop_file": str(cfg_path(cfg, "stop_file", "STOP")), "halt_file": str(cfg_path(cfg, "halt_file", "HALT")),
        "mode": LIVE, "book": "h5_boostfloor_v1", "commitment": cfg.get("commitment", "confirmed"), "poll_s": float(cfg.get("poll_s", 5.0)),
        "size_lamports": h5.stake_lamports, "priority_lamports": h5.buy_priority_lamports, "max_attempts": h5.max_attempts,
        "max_open": h5.max_open, "loss_cap_lamports": h5.total_loss_lamports, "max_days": h5.max_days,
        "slippage_cap": h5.entry_tolerance_bps / 10_000.0, "sell_retries": int(cfg.get("sell_retries", 5)),
        "signal_poll_ms": int(cfg.get("intent_poll_ms", 25)),
    }
    if h5.end_ms is not None:
        pc["end_ms"] = h5.end_ms
    if cfg.get("user"):
        pc["user"] = cfg["user"]
    return pc


# --- the pick oracle (EXP-022 seal): a boolean and nothing else ---------------------------------------------------------

_MINT_RE = re.compile(r'"mint"\s*:\s*"([1-9A-HJ-NP-Za-km-z]{32,44})"')
_PICK_RE = re.compile(r'"pick"\s*:\s*(true|false)')


class OracleUndecided(Exception):
    """The picks file has no row for the mint: undecided, so the executor refuses (fail closed)."""


class JsonlPickOracle:
    """pick_oracle(mint) -> bool over a boolean-only picks file: one row per gate decision, {"mint": ..., "pick": true|false}.
    The row is NOT parsed as JSON: two regexes pull the mint and the flag and nothing else of the line is read into a
    value, so a score, P&L or position field in a richer file is never touched. A mint with no row raises OracleUndecided."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._off = 0
        self._ino: int | None = None
        self._flags: dict[str, bool] = {}

    def _refresh(self) -> None:
        st = self.path.stat()
        if self._ino != st.st_ino or st.st_size < self._off:
            self._off, self._ino, self._flags = 0, st.st_ino, {}
        with self.path.open("rb") as fh:
            fh.seek(self._off)
            data = fh.read(pe.MAX_READ_BYTES * 8)
        end = data.rfind(b"\n")
        if end < 0:
            return
        for raw in data[: end + 1].splitlines():
            line = raw.decode("utf-8", "replace")
            m, f = _MINT_RE.findall(line), _PICK_RE.findall(line)
            if len(m) == 1 and len(f) == 1:
                self._flags[m[0]] = f[0] == "true"
        self._off += end + 1

    def __call__(self, mint: str) -> bool:
        self._refresh()
        if mint not in self._flags:
            raise OracleUndecided("undecided")
        return self._flags[mint]


# --- the executor -------------------------------------------------------------------------------------------------------


class H5Executor(pl.LiveExecutor):
    def __init__(self, rpc: Callable[[str, list], dict], cfg: dict[str, Any], keypair: Keypair | None, *,
                 now_ms: Callable[[], int] | None = None, pick_oracle: Callable[[str], bool] | None = None,
                 root: Path | None = None):
        self.h5 = H5Limits.from_config(cfg)
        self.dry_run = keypair is None
        self.run_mode = DRYRUN if self.dry_run else LIVE
        self.h5cfg = cfg
        self.root = root or repo_root()
        self.intents_path = Path(cfg["intents_file"])
        probe_cfg = build_probe_cfg(cfg, self.h5, self.run_mode)
        kp = keypair if keypair is not None else PublicOnly(Pubkey.from_string(cfg.get("user") or sim.DEFAULT_USER))
        sd = Path(probe_cfg["state_dir"])
        self.counters_path = sd / "h5-counters.json"
        pe.guard_live_state(self.run_mode, self.counters_path, sd / "h5-ledger.jsonl")  # deleting the counters cannot reset the limits
        super().__init__(rpc, probe_cfg, kp, now_ms=now_ms)  # type: ignore[arg-type]
        self.fills = H5Ledger(sd / "h5-ledger.jsonl", self.run_mode)
        self.decisions = self.intents_path  # base-class messages name the file we actually tail
        self.counters = H5Counters.load(self.counters_path, self.run_mode)
        self.live_ok_file = cfg_path(cfg, "live_ok_file", "LIVE_OK")
        self.final_marker = cfg_path(cfg, "final_marker_file", "FINAL_WRITTEN")
        seal_end = int(cfg.get("seal_end_ms") or SEAL_END_DEFAULT_MS)
        self.seal_end_ms = max(seal_end, SEAL_END_DEFAULT_MS)  # config may extend the seal, never shorten it
        self.pick_oracle = pick_oracle
        self.slots = SlotClock()
        self.pool_cache: dict[str, tuple[tx.PoolState, int]] = {}
        self.armed: dict[str, dict[str, Any]] = {}
        self.feed_gap = False
        self.feed_last_ms: int | None = None
        self.heartbeat_max_age_ms = int(cfg.get("feed_heartbeat_max_age_ms") or 0)
        self.refusals: dict[str, int] = {}
        self._bal: tuple[int, int] | None = None
        self._last_adv = 0
        self._last_sell_try: dict[str, int] = {}
        self._slot_alert_ms = 0
        self._slot_fail_ms: int | None = None
        self._seal_logged = self.counters.seal_skips
        self._seal_logged_ms = 0
        self.save()  # state and counters exist on disk before the first ledger row, so the anti-reset guards hold from the first start
        self.counters.save(self.counters_path)
        self._log("start", "", rule=RULE_ID, run_mode=self.run_mode, limits=asdict(self.h5), user=str(self.user),
                  code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), seal_end_ms=self.seal_end_ms)

    def __repr__(self) -> str:
        return f"H5Executor(mode={self.run_mode}, user={self.user})"

    # -- guards and kill switches --------------------------------------------------------------------------------------
    def _kill_reason(self) -> str | None:
        if pe.check_halt_file(self.limits):
            return "halt_file"
        if pe.check_stop_file(self.limits):
            return "stop_file"
        return None

    def _live_gate(self) -> str | None:
        """Checked before every live buy send: LIVE_OK present and EXP-024 Part 1 in the deployed tree."""
        if self.dry_run:
            return None
        if not live_ok_present(self.live_ok_file):
            return "live_ok_missing"
        if not exp024_part1_present(self.root):
            return "exp024_part1_missing"
        return None

    def _latch(self, name: str, **detail: Any) -> None:
        """Latch a live halt: new buys stop, the reason goes to the ledger, and it survives a restart. Cleared only by
        --clear-halt (a ledgered, manual step), never by the executor."""
        if name in self.counters.halts:
            return
        now = self.now_ms()
        self.counters.halts[name] = {"ts_ms": now, **detail}
        self.counters.save(self.counters_path)
        self._log("halt_latched", "", reason=name, **detail)
        self._alert(f"halt_{name}", "", **detail)

    def _seal_reason(self, trg: H5Trigger, now: int) -> str | None:
        if not (SEAL_START_MS <= now < self.seal_end_ms):
            return None
        if self.pick_oracle is None or now < ORACLE_EARLIEST_MS or not Path(self.final_marker).is_file():
            return "seal_window_no_oracle"  # the gate log may be read only after the FINAL; until then all buys refused
        try:
            picked = self.pick_oracle(trg.mint)
        except Exception:
            return "seal_oracle_error"
        if not isinstance(picked, bool):
            return "seal_oracle_error"
        return "seal_pick" if picked else None

    def _budget_stop(self, now: int) -> str | None:
        st, h5 = self.state, self.h5
        if self.counters.halts:
            return "halt_latched:" + ",".join(sorted(self.counters.halts))
        if st.realized_lamports <= -h5.total_loss_lamports:
            return "total_loss_stop"
        day = self.counters.day(day_key(now))
        if day["realized"] <= -h5.daily_loss_lamports:
            return "daily_loss_stop"
        if day["trades"] >= h5.max_trades_per_day:
            return "max_trades_day"
        if st.attempts >= h5.max_attempts:
            return "max_attempts"
        if st.first_attempt_ms is not None and now - st.first_attempt_ms >= h5.max_days * 86_400_000:
            return "max_days"
        if h5.end_ms is not None and now >= h5.end_ms:
            return "end_instant"
        return None

    def _hard_refusal(self, trg: H5Trigger, now: int) -> str | None:
        """Refusals that hold in both modes."""
        why = self._kill_reason() or self._live_gate()
        if why:
            return why
        if now + pl.CLOCK_BACK_TOLERANCE_MS < self.state.max_seen_ms:
            return "clock_backwards"
        if self.feed_gap:
            return "feed_gap"
        if self.heartbeat_max_age_ms and (self.feed_last_ms is None or now - self.feed_last_ms > self.heartbeat_max_age_ms):
            return "feed_stale"
        if self.sell_stuck():
            return "sell_stuck"
        if now - trg.decision_ms > self.h5.max_trigger_age_ms:
            return "stale_trigger"
        if (trg.trigger_slot - trg.s0_slot) * trg.sps > RULE_TRIGGER_WINDOW_S + 1.0:
            return "outside_rule_window"
        if trg.q_lamports > RULE_Q_MAX_LAMPORTS:
            return "q_above_rule_max"
        if trg.mint in self.state.open or trg.mint in self.state.pending or trg.mint in self.state.bought:
            return "already_bought"
        pend = sum(1 for p in self.state.pending.values() if p["kind"] == "buy")
        if len(self.state.open) + pend >= self.h5.max_open:
            return "max_open"
        return None

    def _refuse(self, trg: H5Trigger, reason: str, **kw: Any) -> None:
        if reason in SEAL_REASONS:  # a count only: which mints the gate picked is never written down
            self.counters.seal_skips += 1
            self.counters.save(self.counters_path)
            return
        self.refusals[reason] = self.refusals.get(reason, 0) + 1
        self._skip({"mint": trg.mint, "decision_t_ms": trg.decision_ms}, reason, **kw)

    # -- balance -------------------------------------------------------------------------------------------------------
    def _balance_value(self, now: int) -> int | None:
        if self.dry_run:
            return self.h5.dry_run_balance_lamports
        if self._bal is not None and 0 <= now - self._bal[1] <= BALANCE_MAX_AGE_MS:
            return self._bal[0]
        v = self._balance()
        if v is not None:
            self._bal = (v, now)
        return v

    def _balance_refusal(self, now: int) -> str | None:
        bal = self._balance_value(now)
        if bal is None:
            return "balance_unreadable"
        pend_stake = sum(int(p.get("spend") or 0) for p in self.state.pending.values() if p["kind"] == "buy")
        need = balance_need(self.h5.stake_lamports, self.h5.buy_priority_lamports, len(self.state.open), pend_stake, self.h5.wallet_floor_lamports)
        return None if bal >= need else "balance_floor"

    # -- entry ---------------------------------------------------------------------------------------------------------
    def _static_for(self, trg: H5Trigger, now: int) -> tuple[tx.PoolState | None, str | None, pe.Snapshot | None]:
        ent = self.pool_cache.get(trg.mint)
        if ent is not None and 0 <= now - ent[1] <= self.h5.pool_cache_ttl_ms:
            ps = ent[0]
            if str(ps.pool) != trg.pool or str(ps.base_mint) != trg.mint:
                return None, "pool_mismatch", None
            return ps, None, None
        snap, pool, err = self._snapshot(trg.mint)  # the probe's two-call read; used only on a cache miss
        if snap is None:
            return None, err or "no_pool", None
        if snap.quote_priced is None:
            return None, "no_v", None
        if pool != trg.pool:
            return None, "pool_mismatch", None
        self.pool_cache[trg.mint] = (snap.ps, now)
        return snap.ps, None, snap

    def prefetch(self, mint: str, pool: str) -> None:
        """h5_watch_v1: read the static accounts when the pool first appears, so a trigger needs no RPC read."""
        now = self.now_ms()
        if mint in self.pool_cache or len(self.pool_cache) >= 200:
            return
        snap, p, _err = self._snapshot(mint)
        if snap is not None and p == pool:
            self.pool_cache[mint] = (snap.ps, now)

    def _buy_message(self, ps: tx.PoolState, user: Pubkey, min_out: int, blockhash: Hash | None) -> Message:
        ixs = tx.buy_instructions(ps, user, self.h5.stake_lamports, min_out, 0, priority_total_lamports=self.h5.buy_priority_lamports,
                                  cu_limit=self.h5.buy_cu_limit, exact_quote_in=True, track_volume=self.h5.track_volume)
        return Message.new_with_blockhash(ixs, user, blockhash or Hash.default())

    @pe.critical
    def handle_trigger(self, trg: H5Trigger, seen_ms: int | None = None) -> None:
        now = self.now_ms()
        why = self._hard_refusal(trg, now)
        would: str | None = None
        if not why:
            budget = self._budget_stop(now)
            if budget and not self.dry_run:
                why = budget
            elif budget:
                would = budget  # dry run: budget stops are recorded, not enforced
        if not why:
            why = self._seal_reason(trg, now)
        if not why:
            why = self._balance_refusal(now)
        if why:
            return self._refuse(trg, why)
        with self._prio():
            ps, err, snap = self._static_for(trg, now)
            if ps is None:
                return self._refuse(trg, err or "no_pool")
            terms = entry_terms(trg.q_lamports, trg.base_reserve, self.h5.stake_lamports, self.h5.entry_tolerance_bps)
            if terms["expected_tokens"] <= 0 or terms["min_out"] <= 0:
                return self._refuse(trg, "zero_quote")
            drift = None
            if snap is not None and snap.quote_priced:  # free: we already hold a fresh read on this path
                drift = (snap.quote_priced / snap.base_reserve) / (trg.q_lamports / trg.base_reserve) - 1.0
                if drift > self.h5.entry_tolerance_bps / 10_000.0:
                    return self._refuse(trg, "price_moved", drift_vs_trigger=drift)
            plan = exit_plan(trg.s0_slot, trg.sps, self.h5)
            if self.dry_run:
                return self._dry_buy(trg, ps, terms, plan, now, would, drift)
            self._live_buy(trg, ps, terms, plan, now, drift, seen_ms)

    def _live_buy(self, trg: H5Trigger, ps: tx.PoolState, terms: dict[str, Any], plan: ExitPlan, now: int, drift: float | None,
                  seen_ms: int | None) -> None:
        try:
            bhash, lvbh = self.bh.get()
            msg = self._buy_message(ps, self.user, terms["min_out"], bhash)
            signature, tx_b64 = self._sign(msg, ps, trg.mint, cap=self.h5.buy_priority_lamports)
        except (Exception, SystemExit) as exc:
            if isinstance(exc, pl.UnsafeTx):
                self._alert("unsafe_tx_refused", trg.mint, why=exc.label)
            return self._refuse(trg, f"build_error:{pe.error_label(exc)}")
        t_built = self.now_ms()
        late = self._kill_reason() or self._live_gate()  # the kill switch, once more, immediately before the send
        if late:
            return self._refuse(trg, late)
        st = self.state
        st.attempts += 1
        st.bought.append(trg.mint)
        if st.first_attempt_ms is None:
            st.first_attempt_ms = now
        self.counters.day(day_key(now))["trades"] += 1
        spend = self.h5.stake_lamports
        st.pending[trg.mint] = {
            "kind": "buy", "signature": signature, "tx_b64": tx_b64, "lvbh": lvbh, "pool": trg.pool, "snap_slot": trg.trigger_slot,
            "drift_vs_seed": None, "decision_t_ms": trg.decision_ms, "receive_ms": now, "score": None, "spend": spend,
            "q_tokens": terms["expected_tokens"], "q_net_in": terms["net_in"], "q_mark": terms["mark"], "fee_ppm": terms["fee_ppm"],
            "v_lamports": trg.v_lamports, "base_vault": str(ps.base_vault), "quote_vault": str(ps.quote_vault),
            "base_ata": str(tx.ata(self.user, ps.base_mint, ps.base_token_program)), "base_mint": str(ps.base_mint),
            "base_tp": str(ps.base_token_program), "sends": 0, "seen_ms": seen_ms, "state_ms": now, "state_slot": trg.trigger_slot,
            "built_ms": t_built, "runner_latency": None,
            "h5": {"trigger": trg.public(), "plan": plan.public(), "min_out": terms["min_out"], "tolerance_bps": self.h5.entry_tolerance_bps},
        }
        self.save()
        self.counters.save(self.counters_path)
        p = st.pending[trg.mint]
        self._send(p, trg.mint)
        self.save()
        if trg.mint not in st.pending:  # the kill switch fired between the gate and the send: nothing was sent
            return
        self._log("decision", trg.mint, **trg.log_fields(), stake_lamports=spend, expected_tokens=terms["expected_tokens"], min_out=terms["min_out"],
                  fee_ppm=terms["fee_ppm"], tolerance_bps=self.h5.entry_tolerance_bps, plan=plan.public(), signature=signature,
                  built_ms=t_built, sent_ms=p.get("first_send_ms"), ms_decision_to_send=(p["first_send_ms"] - trg.decision_ms) if p.get("first_send_ms") else None,
                  drift_vs_trigger=drift, priority_lamports=self.h5.buy_priority_lamports)
        self._after_send(trg, ps)

    def _after_send(self, trg: H5Trigger, ps: tx.PoolState) -> None:
        """Off the hot path: anchor the slot clock and keep the ATA/pool cache."""
        self.pool_cache[trg.mint] = (ps, self.now_ms())
        self.refresh_slot()

    def _dry_buy(self, trg: H5Trigger, ps: tx.PoolState, terms: dict[str, Any], plan: ExitPlan, now: int, would: str | None,
                 drift: float | None) -> None:
        msg = self._buy_message(ps, self.user, terms["min_out"], None)
        validate_err = None
        try:
            pl.validate_message(msg, ps, Pubkey.from_string(trg.mint), self.user, self.h5.buy_priority_lamports, self.h5.stake_lamports)
        except Exception as exc:
            validate_err = getattr(exc, "label", type(exc).__name__)
        t_built = self.now_ms()
        try:
            res = pe.sim_message(self.rpc, msg, self.user, [self.user, tx.ata(self.user, ps.base_mint, ps.base_token_program)])
        except (Exception, SystemExit) as exc:
            res = {"err": pe.error_label(exc)}
        sim_tokens = None
        accts = res.get("accounts") or [None, None]
        if res.get("err") is None and len(accts) > 1 and accts[1]:
            sim_tokens = sim.token_amount(sim._b64(accts[1]))
        self.state.attempts += 1
        self.state.bought.append(trg.mint)
        if self.state.first_attempt_ms is None:
            self.state.first_attempt_ms = now
        self.counters.day(day_key(now))["trades"] += 1
        self._log("decision", trg.mint, **trg.log_fields(), stake_lamports=self.h5.stake_lamports, expected_tokens=terms["expected_tokens"],
                  min_out=terms["min_out"], fee_ppm=terms["fee_ppm"], tolerance_bps=self.h5.entry_tolerance_bps, plan=plan.public(),
                  would_have_halted=would, live_validate_err=validate_err, sim_tokens=sim_tokens, drift_vs_trigger=drift,
                  err=res.get("err"), cu_used=res.get("unitsConsumed"), ms_decision_to_built=t_built - trg.decision_ms,
                  ms_built_to_sim=self.now_ms() - t_built, tx_bytes=tx.serialized_size(msg), sent=False)
        if res.get("err") is None:  # a virtual position, so the exit timer and max_open are exercised
            self.state.open[trg.mint] = {
                "mint": trg.mint, "pool": trg.pool, "t_entry_ms": now, "tokens": terms["expected_tokens"], "net_in": terms["net_in"],
                "mark": terms["mark"], "spend": self.h5.stake_lamports, "virtual": True, "h5": {"trigger": trg.public(), "plan": plan.public()},
                "base_mint": str(ps.base_mint), "base_ata": str(tx.ata(self.user, ps.base_mint, ps.base_token_program)),
                "base_tp": str(ps.base_token_program), "sell_attempts": 0,
            }
            self.pool_cache[trg.mint] = (ps, now)
        self.save()
        self.counters.save(self.counters_path)
        self.refresh_slot()

    # -- signing and sending (the only two places a key or the network is touched) ------------------------------------------
    def _sign(self, msg: Message, ps: tx.PoolState, mint: str, cap: int | None = None) -> tuple[str, str]:
        if self.dry_run:
            raise RuntimeError("dry run cannot sign")
        pl.validate_message(msg, ps, Pubkey.from_string(mint), self.user, cap if cap is not None else self.h5.escalated_priority_lamports,
                            self.limits.size_lamports)
        t = VersionedTransaction(msg, [self._kp])
        raw = bytes(t)
        if len(raw) > tx.TX_SIZE_LIMIT:
            raise ValueError("tx too large")
        return str(t.signatures[0]), base64.b64encode(raw).decode()

    def _send(self, p: dict[str, Any], mint: str) -> None:
        if self.dry_run:
            self._log("send_blocked_dry_run", mint, kind_=p.get("kind"))
            return
        if pe.check_halt_file(self.limits):
            self._log("send_blocked", mint, reason="halt_file", kind_=p.get("kind"))
            return
        if p["kind"] == "buy" and p.get("sends", 0) == 0:
            why = self._kill_reason() or self._live_gate()
            if why:
                self.state.pending.pop(mint, None)
                self._log("send_blocked", mint, reason=why, kind_="buy")
                return
        super()._send(p, mint)

    # -- feed rows -----------------------------------------------------------------------------------------------------------
    def on_feed_status(self, gap: bool) -> None:
        if gap and not self.feed_gap:
            self._log("feed_gap", "", gap=True)
        self.feed_gap = bool(gap)
        self.feed_last_ms = self.now_ms()

    def on_boost_row(self, mint: str, s0_slot: int | None, sps: float | None, last_slice_slot: int | None, last_slice_s: float | None) -> None:
        """BOOST last-slice timing for a tracked pool (the detector logs it). Halts new buys when it comes early."""
        sec = last_slice_s
        if sec is None and last_slice_slot is not None and s0_slot is not None and sps:
            sec = (last_slice_slot - s0_slot) * sps
        if isinstance(sec, bool) or not isinstance(sec, (int, float)) or not math.isfinite(sec) or not (0 < sec < 2_000):
            return self._log("boost_row_ignored", mint, why="bad_seconds")
        day = self.counters.day(day_key(self.now_ms()))
        self._log("boost_last_slice", mint, seconds_after_s0=round(float(sec), 3))
        if sec < BOOST_HALT_S:
            self._latch("boost_last_slice_lt_335", pool_mint=mint, seconds=round(float(sec), 3))
        elif sec < BOOST_HALT_TWICE_S:
            if mint not in day["boost_lt337"]:
                day["boost_lt337"].append(mint)
            self.counters.save(self.counters_path)
            if len(day["boost_lt337"]) >= 2:
                self._latch("boost_last_slice_lt_337_twice", pools=list(day["boost_lt337"]))

    # -- intent file ---------------------------------------------------------------------------------------------------------
    def signal_tick(self) -> int:
        return self.intent_tick()

    def intent_tick(self) -> int:
        if self._crit:
            return 0
        try:
            s = self.intents_path.stat()
        except FileNotFoundError:
            self._signals_absent()
            return 0
        st = self.state
        if st.started and st.inode == s.st_ino and s.st_size == st.offset:
            return 0
        before = (st.started, st.offset, st.inode)
        lines = tail_lines(self.intents_path, st)
        seen = self.now_ms()
        if (st.started, st.offset, st.inode) != before:
            self.save()  # the offset first: a crash mid-trigger must not replay it
        triggers: list[H5Trigger] = []
        watches: list[tuple[str, str]] = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            schema = row.get("schema")
            if schema == SCHEMA_FEED:
                self.on_feed_status(bool(row.get("gap")))
            elif schema == SCHEMA_BOOST:
                self.on_boost_row(str(row.get("mint") or ""), row.get("s0_slot") if _is_int(row.get("s0_slot")) else None,
                                  row.get("sps") if isinstance(row.get("sps"), (int, float)) else None,
                                  row.get("last_slice_slot") if _is_int(row.get("last_slice_slot")) else None,
                                  row.get("last_slice_s") if isinstance(row.get("last_slice_s"), (int, float)) else None)
            elif schema == SCHEMA_WATCH and isinstance(row.get("mint"), str) and isinstance(row.get("pool"), str):
                watches.append((row["mint"], row["pool"]))
            elif schema == SCHEMA_INTENT:
                trg, bad = parse_trigger(row)
                if trg is not None:
                    triggers.append(trg)
                elif bad:
                    self._log("skip", str(row.get("mint") or ""), reason=bad)
        for trg in triggers:  # triggers first: the buy path is the latency path
            self.handle_trigger(trg, seen)
        for mint, pool in watches:
            self.prefetch(mint, pool)
        return len(triggers)

    # -- exit scheduler ------------------------------------------------------------------------------------------------------
    def refresh_slot(self) -> None:
        t0 = self.now_ms()
        if self._slot_fail_ms is not None and 0 <= t0 - self._slot_fail_ms < 200:
            return  # a failing getSlot is retried at 5 per second at most, not every loop pass
        try:
            with self._prio():
                slot = int(self.rpc("getSlot", [{"commitment": "processed"}]))
        except (Exception, SystemExit) as exc:
            self._slot_fail_ms = t0
            if t0 - self._slot_alert_ms >= 60_000:
                self._slot_alert_ms = t0
                self._alert("slot_clock_error", "", label=pe.error_label(exc))
            return
        self._slot_fail_ms = None
        t1 = self.now_ms()
        self.slots.observe(slot, (t0 + t1) // 2)

    def _slot_fresh(self, now: int, plan: dict[str, Any]) -> None:
        """Poll getSlot at slot_poll_ms inside the final window before the send, at slot_resync_ms otherwise."""
        est = self.slots.est(now, plan["sps"])
        window = ceil_slots(5.0, plan["sps"])
        close = est is None or est >= plan["arm_slot"] - window
        limit = self.h5.slot_poll_ms if close else self.h5.slot_resync_ms
        if self.slots.age(now) >= limit:
            self.refresh_slot()

    @pe.critical
    def exit_tick(self, now: int) -> None:
        if pe.check_halt_file(self.limits):
            return  # HALT freezes everything, sells included
        for mint, pos in list(self.state.open.items()):
            plan = pos.get("h5", {}).get("plan")
            if plan is None or mint in self.state.pending or pos.get("abandoned"):
                continue
            if pos.get("stuck"):
                wait = min(pl.STUCK_RETRY_MS * 2 ** max(0, pos["sell_attempts"] - self.sell_retries), pl.STUCK_RETRY_MAX_MS)
                if now - pos.get("last_sell_fail_ms", 0) < wait:
                    continue
            self._slot_fresh(now, plan)
            est = self.slots.est(self.now_ms(), plan["sps"])
            if est is None:
                continue
            if est >= plan["deadline_slot"]:
                self._fire_sell(mint, pos, est, emergency=True)
            elif est >= plan["send_slot"]:
                self._fire_sell(mint, pos, est, emergency=False)
            elif est >= plan["arm_slot"] and not self.dry_run:  # a dry run has nothing to precompute: it cannot sign
                armed = self.armed.get(mint)
                if armed is None or self.now_ms() - armed["armed_ms"] > MAX_ARM_AGE_MS:
                    if self.now_ms() - self._last_sell_try.get(mint, 0) >= 500:
                        self._last_sell_try[mint] = self.now_ms()
                        with self._prio():
                            self._arm_sell(mint, pos, self._level(pos, est, False), False)

    @staticmethod
    def _level(pos: dict[str, Any], est: int, emergency: bool) -> int:
        lvl = min(int(pos.get("sell_attempts", 0)), len(SELL_LADDER_SLIP_BPS) - 1)
        if est >= pos["h5"]["plan"]["escalate_slot"]:
            lvl = len(SELL_LADDER_SLIP_BPS) - 1
        return lvl

    def _sell_balance(self, pos: dict[str, Any]) -> tuple[int | None, str]:
        known = pos.get("tokens")
        if (pos.get("sell_attempts", 0) == 0 and isinstance(known, int) and known > 0 and not pos.get("balance_pending")
                and pos.get("ata_pre_amount") == 0):
            return known, "buy_meta"
        return self._ata_balance(pos["base_ata"]), "rpc"

    def _arm_sell(self, mint: str, pos: dict[str, Any], level: int, emergency: bool) -> dict[str, Any] | None:
        """Precompute the sell: balance, quote, min_out, blockhash, signature. Nothing is sent. Returns the pending record."""
        now = self.now_ms()
        bal, bsrc = self._sell_balance(pos)
        if not bal or bal <= 0:
            if not pos.get("zero_alerted"):
                pos["zero_alerted"] = True
                self._alert("zero_token_balance", mint)
            return None
        ent = self.pool_cache.get(mint)
        snap, _pool, _err = self._snapshot(mint, self.exit_commitment)
        ps = snap.ps if snap is not None else (ent[0] if ent else None)
        quote = ret = None
        if snap is not None and snap.quote_priced is not None:
            chk = pe.exit_check({**pos, "tokens": bal}, snap, now, own_trade_in_state=True)
            quote, ret = chk["quote_out"], chk["ret"]
        if ps is None:
            return None
        if emergency:
            min_out = EMERGENCY_MIN_OUT  # a market sell: never 0, never blocked by a price floor
        elif quote and quote > 0:
            min_out = tx.min_out_with_slippage(quote, SELL_LADDER_SLIP_BPS[level])
        else:
            if now - pos.get("unpriced_alert_ms", 0) >= 60_000:  # never sell blind outside the emergency
                pos["unpriced_alert_ms"] = now
                self._alert("unpriced_position", mint)
            return None
        prio = self.h5.escalated_priority_lamports if (level >= len(SELL_LADDER_SLIP_BPS) - 1 or emergency) else self.h5.sell_priority_lamports
        try:
            bhash, lvbh = self.bh.get(fresh=pos.get("sell_attempts", 0) > 0 or emergency)
            ixs = [*tx.sell_instructions(ps, self.user, bal, min_out, priority_total_lamports=prio, cu_limit=self.h5.sell_cu_limit),
                   pl.close_token_account(Pubkey.from_string(pos["base_ata"]), self.user, Pubkey.from_string(pos["base_tp"]))]
            signature, tx_b64 = self._sign(Message.new_with_blockhash(ixs, self.user, bhash), ps, mint, cap=prio)
            if signature in pos.setdefault("sell_sigs", []):
                raise pe.RpcError("duplicate_signature")
            pos["sell_sigs"].append(signature)
        except (Exception, SystemExit) as exc:
            self._alert("sell_build_error", mint, label=pe.error_label(exc))
            return None
        rec = {
            "kind": "sell", "signature": signature, "tx_b64": tx_b64, "lvbh": lvbh, "pool": pos["pool"], "snap_slot": snap.slot if snap else None,
            "decision_t_ms": now, "receive_ms": now, "tokens": bal, "q_out": quote or 0, "min_out": min_out,
            "reason": "h5_emergency" if emergency else "h5_timed_exit", "ret": ret, "sends": 0, **self._exit_fields("full"),
            "balance_source": bsrc, "h5": {"level": level, "emergency": emergency, "priority": prio},
        }
        self.armed[mint] = {"p": rec, "armed_ms": now, "level": level, "emergency": emergency}
        return rec

    def _fire_sell(self, mint: str, pos: dict[str, Any], est: int, emergency: bool) -> None:
        now = self.now_ms()
        if self.now_ms() - self._last_sell_try.get(mint, 0) < 250 and mint not in self.armed:
            return
        if self.dry_run:
            return self._dry_sell(mint, pos, est, emergency)
        level = self._level(pos, est, emergency)
        armed = self.armed.get(mint)
        if armed and now - armed["armed_ms"] <= MAX_ARM_AGE_MS and armed["level"] == level and armed["emergency"] == emergency:
            rec = armed["p"]
        else:
            self._last_sell_try[mint] = now
            with self._prio():
                rec = self._arm_sell(mint, pos, level, emergency)
        self.armed.pop(mint, None)
        if rec is None:
            if emergency and not pos.get("emergency_attempted"):
                pos["emergency_attempted"] = True
                self._latch("stuck_position", position_mint=mint, why="emergency_sell_not_built", est_slot=est)
            return
        rec["decision_t_ms"] = rec["receive_ms"] = now
        pos["exit_reason"] = rec["reason"]
        self.state.pending[mint] = rec
        self.save()  # write-ahead
        self._send(rec, mint)
        self.save()
        plan = pos["h5"]["plan"]
        self._log("sell_sent", mint, level=rec["h5"]["level"], emergency=emergency, est_slot=est, send_slot=plan["send_slot"],
                  land_slot=plan["land_slot"], deadline_slot=plan["deadline_slot"], min_out=rec["min_out"], priority_lamports=rec["h5"]["priority"],
                  signature=rec["signature"], sent_ms=rec.get("first_send_ms"))
        if emergency and not pos.get("emergency_attempted"):
            pos["emergency_attempted"] = True
            self._latch("stuck_position", position_mint=mint, why="not_sold_by_deadline", est_slot=est, deadline_slot=plan["deadline_slot"])
            self.save()

    def _dry_sell(self, mint: str, pos: dict[str, Any], est: int, emergency: bool) -> None:
        """Outcome-blind: simulate the sell shape against live state and log only err, CU, slot and timing. No quote, no P&L."""
        snap, _pool, _err = self._snapshot(mint, self.exit_commitment)
        row: dict[str, Any] = {"emergency": emergency, "est_slot": est, "send_slot": pos["h5"]["plan"]["send_slot"], "land_slot": pos["h5"]["plan"]["land_slot"],
                               "late_slots": est - pos["h5"]["plan"]["send_slot"], "sent": False}
        if snap is not None and snap.quote_priced is not None:
            msg, _tok, _min = pe.sell_probe_message(snap, self.user, pos["spend"], self.slip_bps, self.h5.sell_priority_lamports)
            try:
                res = pe.sim_message(self.rpc, msg, self.user, [self.user])
            except (Exception, SystemExit) as exc:
                res = {"err": pe.error_label(exc)}
            row.update(err=res.get("err"), cu_used=res.get("unitsConsumed"), pool_slot=snap.slot)
        else:
            row.update(err="no_snapshot")
        self._log("dry_sell", mint, **row)
        del self.state.open[mint]
        self.save()

    # -- landing hooks: daily counters and the halt rules ------------------------------------------------------------------
    def _book_realized(self, delta: int) -> None:
        if delta:
            self.counters.day(day_key(self.now_ms()))["realized"] += delta
            self.counters.save(self.counters_path)

    @pe.critical
    def _finish_buy(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        before = self.state.realized_lamports
        super()._finish_buy(mint, p, m)
        self._bal = None  # the balance moved
        self._book_realized(self.state.realized_lamports - before)
        pos = self.state.open.get(mint)
        h5 = p.get("h5")
        if pos is None or h5 is None:
            return
        pos["h5"] = {**h5, "buy_landed_slot": m["slot"]}
        if m.get("slot"):
            self._note_buy_landing(h5["trigger"]["trigger_slot"], m["slot"], h5["trigger"]["sps"])
        self.save()

    def _note_buy_landing(self, trigger_slot: int, landed_slot: int, sps: float) -> None:
        """Halt rule: median trigger-to-landing over the last 10 fills above 3.0 s (strictly above; fewer than 10 fills: no halt)."""
        sec = (landed_slot - trigger_slot) * sps  # trigger print to landing, in seconds of slot time
        self.counters.landing_s = [*self.counters.landing_s, round(sec, 4)][-50:]
        self.counters.save(self.counters_path)
        last = self.counters.landing_s[-LANDING_WINDOW:]
        if len(last) >= LANDING_WINDOW and statistics.median(last) > LANDING_MEDIAN_MAX_S:
            self._latch("landing_median_gt_3s", median_s=round(statistics.median(last), 4), n=len(last))

    @pe.critical
    def _finish_sell(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
        before = self.state.realized_lamports
        pos = self.state.open.get(mint)
        plan = (pos or {}).get("h5", {}).get("plan")
        super()._finish_sell(mint, p, m)
        self._bal = None
        self._book_realized(self.state.realized_lamports - before)
        if m.get("err") is None and plan is not None and mint not in self.state.open and m.get("slot"):
            self._note_sell_landing(mint, plan, m["slot"], bool(p.get("h5", {}).get("emergency")))

    def _note_sell_landing(self, mint: str, plan: dict[str, Any], landed_slot: int, emergency: bool) -> None:
        """Halt rule: more than 5% of our landed sells landing after s0 + 335 s (strictly more than 5%)."""
        c = self.counters
        c.sells_landed += 1
        late = landed_slot > plan["late_slot"]
        c.sells_late += 1 if late else 0
        self._log("exit_landing", mint, landed_slot=landed_slot, exit_slot=plan["exit_slot"], land_slot=plan["land_slot"],
                  error_slots=landed_slot - plan["land_slot"], late=late, emergency=emergency)
        c.save(self.counters_path)
        if c.sells_landed >= self.h5.late_sell_min_n and c.sells_late / c.sells_landed > LATE_SELL_FRAC:
            self._latch("late_sells_gt_5pct", late=c.sells_late, landed=c.sells_landed)

    @pe.critical
    def _resolve_expired(self, mint: str, p: dict[str, Any]) -> None:
        before = self.state.realized_lamports
        super()._resolve_expired(mint, p)
        self._book_realized(self.state.realized_lamports - before)

    # -- loop ----------------------------------------------------------------------------------------------------------------
    def prewarm(self) -> None:
        super().prewarm()
        now = self.now_ms()
        if not self.dry_run:
            v = self._balance()
            if v is not None:
                self._bal = (v, now)
        c = self.counters
        if c.seal_skips != self._seal_logged and now - self._seal_logged_ms >= 60_000:
            self._seal_logged, self._seal_logged_ms = c.seal_skips, now
            self._log("seal_count", "", seal_skips=c.seal_skips)  # a count and nothing else

    def housekeeping(self, now: int) -> None:
        if not self.state.pending:
            return
        if now - self._last_adv >= self.h5.status_poll_ms:
            self._last_adv = now
            self.advance_pending()
        if pe.check_halt_file(self.limits):
            return
        for mint, p in list(self.state.pending.items()):
            if (p.get("sends", 0) >= 1 and "expired_seen_ms" not in p and now - p.get("last_send_ms", 0) >= self.h5.rebroadcast_ms
                    and now - p.get("first_send_ms", now) <= 25_000):
                self._send(p, mint)

    def tick(self) -> int:
        now = self.now_ms()
        self._clock_note(now)
        n = self.intent_tick()
        now = self.now_ms()
        self.housekeeping(now)
        self.exit_tick(now)
        if self._last_slow is None or now - self._last_slow >= self.poll_ms:
            self._last_slow = now
            self.prewarm()
        return n

    def step(self) -> int:
        return self.tick()


def tail_lines(path: Path, st: pe.State) -> list[str]:
    """New complete lines since the persisted offset (the probe's tailer, minus its fixed schema parsing). First run starts
    at the end of the file so history is never replayed; rotation or truncation restarts from 0."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return []
    if not st.started:
        st.started, st.offset, st.inode = True, stat.st_size, stat.st_ino
        return []
    if st.inode != stat.st_ino or stat.st_size < st.offset:
        st.offset, st.inode = 0, stat.st_ino
    with path.open("rb") as fh:
        fh.seek(st.offset)
        data = fh.read(pe.MAX_READ_BYTES)
    end = data.rfind(b"\n")
    if end < 0:
        if len(data) >= pe.MAX_READ_BYTES:
            st.offset += len(data)
        return []
    st.offset += end + 1
    return [raw.decode("utf-8", "replace") for raw in data[: end + 1].splitlines()]


# --- CLI ------------------------------------------------------------------------------------------------------------------


def status_report(cfg: dict[str, Any]) -> str:
    """Counters, limits and kill-file state from disk. No key, no RPC."""
    h5 = H5Limits.from_config(cfg)
    lines = [f"rule={RULE_ID} stake_sol={h5.stake_lamports / pe.LAMPORTS:.3f} max_open={h5.max_open}",
             f"stop_file={Path(cfg_path(cfg, 'stop_file', 'STOP')).exists()} halt_file={Path(cfg_path(cfg, 'halt_file', 'HALT')).exists()} "
             f"live_ok={live_ok_present(cfg_path(cfg, 'live_ok_file', 'LIVE_OK'))} exp024_part1={exp024_part1_present(repo_root())}"]
    for mode in (DRYRUN, LIVE):
        sd = Path(cfg["state_dir"]) / mode
        sp = pe.state_path_for(sd, LIVE)
        if not sp.exists():
            lines.append(f"[{mode}] no state")
            continue
        st = pe.State.load(sp, LIVE)
        c = H5Counters.load(sd / "h5-counters.json", mode) if (sd / "h5-counters.json").exists() else H5Counters(run_mode=mode)
        today = c.days.get(day_key(int(time.time() * 1000)), {})
        lines.append(f"[{mode}] attempts={st.attempts}/{h5.max_attempts} realized_sol={st.realized_lamports / pe.LAMPORTS:.6f} "
                     f"open={len(st.open)}/{h5.max_open} pending={len(st.pending)} today_trades={today.get('trades', 0)} "
                     f"today_realized_sol={today.get('realized', 0) / pe.LAMPORTS:.6f} halts={sorted(c.halts)} seal_skips={c.seal_skips} "
                     f"sells_landed={c.sells_landed} sells_late={c.sells_late}")
    return "\n".join(lines)


def clear_halt(cfg: dict[str, Any], name: str, run_mode: str) -> int:
    """Offline, manual and ledgered: clear one latched halt. Needs the lock (so no executor is running)."""
    lock = acquire_lock(Path(cfg["state_dir"]) / "h5-executor.lock")
    sd = Path(cfg["state_dir"]) / run_mode
    path = sd / "h5-counters.json"
    c = H5Counters.load(path, run_mode)
    if name not in c.halts:
        print(f"no such halt: {name}; latched: {sorted(c.halts)}")
        os.close(lock)
        return 1
    gone = c.halts.pop(name)
    c.save(path)
    H5Ledger(sd / "h5-ledger.jsonl", run_mode).write({"kind": "halt_cleared", "ts_ms": int(time.time() * 1000), "mint": "", "reason": name, "was": gone})
    print(f"cleared {name}")
    os.close(lock)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="H5-BOOSTFLOOR executor (dry run by default; live needs config mode AND --live AND LIVE_OK)")
    ap.add_argument("--config", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="the default: build and simulate, never sign or send")
    g.add_argument("--live", action="store_true", help='also needs config "mode": "live", a LIVE_OK file and EXP-024 Part 1 in the tree')
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--clear-halt", metavar="NAME")
    ap.add_argument("--env-file", default=sim.DEFAULT_ENV_FILE)
    args = ap.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text())
    try:
        H5Limits.from_config(cfg)
    except ValueError as exc:
        raise SystemExit(f"limits refused: {exc}") from None
    if args.status:
        print(status_report(cfg))
        return 0
    mode, warn = pe.resolve_mode(cfg.get("mode", DRYRUN), args.live and not args.dry_run)
    if args.clear_halt:
        return clear_halt(cfg, args.clear_halt, mode)
    if warn:
        print(f"h5_executor WARNING {warn}", flush=True)
    lock_fd = acquire_lock(Path(cfg["state_dir"]) / "h5-executor.lock")
    try:
        oracle = JsonlPickOracle(cfg["pick_file"]) if cfg.get("pick_file") else None
        if mode == LIVE:
            why = start_refusal(cfg)
            if why:
                print(f"h5_executor ALERT startup_refused {why}", flush=True)
                return 2
            rc = pe.startup_rpc_env_check(True)
            if rc:
                return rc
            pl.harden_process()  # before the key is read
            if "key_path" in cfg:
                raise SystemExit("live mode has no key path override: the key comes from the systemd credential only")
            if not (os.environ.get("HELIUS_API_KEY") or "").strip():
                print("h5_executor ALERT startup_refused rpc_key_missing", flush=True)
                return 2
            kp = pl.load_probe_key()
            rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(None, args.env_file, use_env_file=False)), rps=float(cfg.get("rps", 8.0)), max_rps=H5_MAX_RPS)
            ex = H5Executor(rpc, cfg, kp, pick_oracle=oracle)
        else:
            rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(None, args.env_file)), rps=float(cfg.get("rps", 8.0)), max_rps=H5_MAX_RPS)
            ex = H5Executor(rpc, cfg, None, pick_oracle=oracle)
        print(f"h5_executor mode={ex.run_mode} user={ex.user} limits={ex.h5}", flush=True)
        if args.once:
            ex.tick()
            return 0
        ex.run_loop()
        return 0
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
