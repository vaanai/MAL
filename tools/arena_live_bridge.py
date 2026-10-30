"""arena_live_bridge: turns an arena book's hash-chained decision log into orders (S1 + S2 of docs L6 READINESS). DRY BY DEFAULT.

Tails `<root>/decisions/<bot_id>/<day>.jsonl` (written by `tools.arena_declog.DecisionLog`; HV4 and the rule bots), verifies the hash chain row by
row with the SAME rule as `DecisionLog.verify`, and maps `entry` rows to a V-priced PumpSwap buy at the canary stake and `exit` rows to a sell-all
(+ close ATA) through the EXISTING probe send path (`tools.probe_live.LiveExecutor`; nothing of the executor is copied or forked). One bridge process
per `bot_id`.

Two layers.
  Bridge        everything that decides: chain, frozen_sha, gates, the shared wallet floor, loss caps, write-ahead state, time-stop and stop-loss
                backstops, the decision-to-send log. It never touches the network or a key. It talks to an OrderPort.
  OrderPort     DryPort (default: no RPC, no key, logs the order it WOULD send) or LiveExecutorPort (wraps probe_live.LiveExecutor; built only with
                BOTH config mode "live" AND --live, and the key from the systemd credential).

Live needs ALL of: config mode "live", --live, LIVE_OK and TIER root-owned files under /etc/mal-arena-live (read here, never created; the root check is
tools.h5_executor.root_file_problem), an explicit end_ms, a pinned frozen_sha that the book's `start` row repeats, a valid chain. A missing or unsafe
LIVE_OK makes a live-configured bridge DRY for that entry (it logs the order it would have sent). The TIER file is read before every buy; the only
tier is T0 (the canary limits); an unknown or missing tier is T0.

This is a build, not a claim. It is not gate evidence and installs nothing. Trades it would make are execution measurements.

Wallet floor (shared with the H5 canary's DEC-020 section 3 formula, deliberately conservative): a buy is refused unless
    balance - sum(open stakes, valued at zero) - stake - fee reserve - buffer >= wallet_floor
where "open" counts pending buys too. The balance has already paid for the open stakes, so this counts them a second time on purpose: it is a stress
test, not an accounting identity.

Latches (stay set in the state file until the manager clears them with the bridge stopped): chain_break, frozen_sha_mismatch, total_loss_cap,
per_trade_loss_cap, stuck_sell. A latch refuses new buys only. Book exit rows of positions already open still run under every latch EXCEPT chain_break:
after a break no row is trusted or read, so only the time-stop and stop-loss backstops close positions. Nothing is stranded either way.
The stop-loss backstop is best effort (it needs a warm quote under 5 s old); the time stop is the guaranteed backstop. The HALT file freezes everything. The STOP file stops new buys only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from tools.arena_declog import GENESIS, DecisionLog, LogChainError, sha256_hex, utc_day
from tools import hv4_c0_parity as c0p
from tools import hv4_live_c0 as c0
from tools.arena_paper import canon

SCHEMA = "arena_live_bridge_v1"
DRY, LIVE = "dry", "live"
ROOT_DIR = Path("/etc/mal-arena-live")
LIVE_OK_PATH = ROOT_DIR / "LIVE_OK"
TIER_PATH = ROOT_DIR / "TIER"
STATE_ROOT_LIVE = Path("/var/lib/mal-live/arena")  # live state/log live under here; dry runs use cfg state_dir (default under the repo-local tmp)

# The canary DEC's proposal (L6 READINESS section 3). Config can only tighten these; a later tier is a reviewed code change.
TIERS: dict[str, dict[str, int]] = {
    "T0": {"stake_lamports": 20_000_000, "max_open": 2, "max_trades_per_day": 10, "daily_loss_lamports": 50_000_000, "total_loss_lamports": 150_000_000},
}
MIN_WALLET_FLOOR = 50_000_000
DEFAULT_WALLET_FLOOR = 400_000_000
FEE_RESERVE_LAMPORTS = 1_010_000  # per trip: 2 x (500,000 priority + 5,000 base)
RENT_RESERVE_LAMPORTS = 2_100_000  # token ATA rent, paid on the buy and refunded on the sell
SELL_FEE_RESERVE_LAMPORTS = 505_000  # one sell: 500,000 priority + 5,000 base; reserved for every open position
BUFFER_LAMPORTS = 20_000_000
MAX_DECISION_AGE_MS = 5_000
BACKSTOP_EXTRA_MS = 60_000  # time stop = the book's max hold + this
DEFAULT_SL_BACKSTOP = 0.50  # return on the position at which the backstop sells (config may tighten, never loosen past 0.60)
MAX_SL_BACKSTOP = 0.60
WARM_MS_MIN, WARM_MS_MAX, WARM_MS_DEFAULT = 500, 2_000, 1_000
SELL_RETRY_MS = 5_000
UNIVERSE_V_RANGE = (17_500_000_000, 17_700_000_000)  # Am.2 item 6: V in [17.5e9, 17.7e9], WSOL quote

LATCHES = ("sealed_path", "chain_break", "frozen_sha_mismatch", "total_loss_cap", "per_trade_loss_cap", "stuck_sell",
           "parity_hard_halt", "landed_buy_unreadable", "stake_mismatch", "twin_pin_mismatch", "read_record", "tieout_mismatch", c0.A3_LATCH, c0.FEED_BREAK_LATCH)  # twin_pin_mismatch and the two before: hv4_c0 parity wiring; read_record: no recorded PASS read for the bot (hv4_c0): hv4_c0 parity wiring (tools/hv4_c0_parity.py)


# --- config -----------------------------------------------------------------------------------------------------------------------


def clamp_warm_ms(v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return WARM_MS_DEFAULT
    return int(min(WARM_MS_MAX, max(WARM_MS_MIN, v)))


@dataclass(frozen=True)
class Config:
    bot_id: str
    frozen_sha: str
    root: str  # the arena root that holds decisions/<bot_id>/
    state_dir: str
    end_ms: int
    max_hold_ms: int  # the book's own max hold; the backstop is this + 60 s
    mode: str = DRY
    stake_lamports: int = TIERS["T0"]["stake_lamports"]
    max_open: int = TIERS["T0"]["max_open"]
    max_trades_per_day: int = TIERS["T0"]["max_trades_per_day"]
    daily_loss_lamports: int = TIERS["T0"]["daily_loss_lamports"]
    total_loss_lamports: int = TIERS["T0"]["total_loss_lamports"]
    per_trade_loss_lamports: int = 15_000_000
    wallet_floor_lamports: int = DEFAULT_WALLET_FLOOR
    sl_backstop: float = DEFAULT_SL_BACKSTOP
    warm_ms: int = WARM_MS_DEFAULT
    dry_balance_lamports: int = 600_000_000  # dry mode only: no RPC, so the balance is a config number
    stop_file: str = "/var/lib/mal-live/STOP"
    halt_file: str = "/var/lib/mal-live/HALT"
    profile: str = ""  # "" = the T0 bridge (H5-path behaviour unchanged); "hv4_c0" = the compiled C0 table of tools/hv4_live_c0
    funded_lamports: int | None = None  # hv4_c0 only: what the wallet was funded with; the floor is derived from it
    parity_dir: str = ""  # hv4_c0 only: where the trips/attempts files and the parity result are written
    twins_file: str = ""  # hv4_c0 only: the paper twins file (tools/hv4_c0_twin.py writes it, with its .run.json record)
    tieout_dir: str = ""  # hv4_c0 only: the daily tie-out results directory (arena_tieout --check-last-day)
    a3_record_file: str = ""  # hv4_c0 only: the A3 daily record (newest line). A halt flag latches a3_pins_changed (new buys stop; exits run). A live start needs it
    reads_dir: str = ""  # hv4_c0 only: the directory that holds the bot's recorded read (`<bot_id>.json`); no decision row is read without a recorded PASS there

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], tier: str = "T0") -> "Config":
        for k in ("bot_id", "frozen_sha", "root", "state_dir", "end_ms", "max_hold_ms"):
            if d.get(k) in (None, ""):
                raise ValueError(f"config {k} is required")
        why = c0.bot_id_problem(d["bot_id"])  # QP N-G: the bot_id is a path component of the decision log, the window file and the feed status
        if why:
            raise ValueError(f"config bot_id refused: {why}")
        t = TIERS[tier]
        if str(d["bot_id"]).startswith("hv4-") and d.get("profile") != c0.PROFILE:
            raise ValueError("hv4_c0 refused: an hv4- bot_id needs profile hv4_c0 (arm rules apply in every mode)")
        kw: dict[str, Any] = {k: d[k] for k in ("bot_id", "frozen_sha", "root", "state_dir")}
        if d.get("profile", "") not in ("", c0.PROFILE):
            raise ValueError(f"config profile must be '{c0.PROFILE}' or absent")
        if d.get("profile") == c0.PROFILE:
            why = c0.config_refusal(d, live=d.get("mode") == LIVE)
            if why:
                raise ValueError(f"hv4_c0 refused: {why}")
            t = c0.C0_LIMITS
            kw["profile"] = c0.PROFILE
            kw["funded_lamports"] = d.get("funded_lamports")
            kw["reads_dir"] = str(d.get("reads_dir") or "")
            a3f = d.get("a3_record_file") or ""
            if a3f and (not isinstance(a3f, str) or not Path(a3f).is_absolute()):
                raise ValueError("hv4_c0 refused: a3_record_file must be an absolute path")
            kw["a3_record_file"] = str(a3f)
            given = [k for k in c0p.PATH_KEYS if d.get(k)]
            if given and len(given) != len(c0p.PATH_KEYS):
                raise ValueError("hv4_c0 refused: parity_dir, twins_file and tieout_dir are set together or not at all")
            if given:
                why = c0p.paths_refusal(d)
                if why:
                    raise ValueError(f"hv4_c0 refused: {why}")
                kw.update({k: str(d[k]) for k in c0p.PATH_KEYS})
        elif "funded_lamports" in d or "priority_pin" in d or any(k in d for k in c0p.PATH_KEYS) or "reads_dir" in d or "a3_record_file" in d:
            raise ValueError("funded_lamports, priority_pin, reads_dir, a3_record_file and the parity paths belong to the hv4_c0 profile")
        for k in ("end_ms", "max_hold_ms"):
            v = d[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v <= 0:
                raise ValueError(f"config {k} must be a positive number")
            kw[k] = int(v)
        for k, cap in t.items():  # tighten only
            v = d.get(k, cap)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v <= 0:
                raise ValueError(f"config {k} must be a positive number")
            kw[k] = min(int(v), cap)
        ptl = d.get("per_trade_loss_lamports", 15_000_000)
        if isinstance(ptl, bool) or not isinstance(ptl, (int, float)) or ptl != ptl or ptl <= 0:
            raise ValueError("config per_trade_loss_lamports must be a positive number")
        kw["per_trade_loss_lamports"] = min(int(ptl), kw["stake_lamports"])
        fl = d.get("wallet_floor_lamports", DEFAULT_WALLET_FLOOR)
        if isinstance(fl, bool) or not isinstance(fl, (int, float)) or fl != fl:
            raise ValueError("config wallet_floor_lamports must be a number")
        kw["wallet_floor_lamports"] = max(int(fl), MIN_WALLET_FLOOR)  # raise only
        if kw.get("funded_lamports") is not None:
            kw["wallet_floor_lamports"] = c0.wallet_floor_lamports(kw["funded_lamports"], kw["wallet_floor_lamports"] if "wallet_floor_lamports" in d else 0)
        sl = d.get("sl_backstop", DEFAULT_SL_BACKSTOP)
        if isinstance(sl, bool) or not isinstance(sl, (int, float)) or not 0 < sl <= MAX_SL_BACKSTOP:
            raise ValueError(f"config sl_backstop must be in (0, {MAX_SL_BACKSTOP}]")
        kw["sl_backstop"] = float(sl)
        kw["warm_ms"] = clamp_warm_ms(d.get("warm_ms", WARM_MS_DEFAULT))
        mode = d.get("mode", DRY)
        if mode not in (DRY, LIVE):
            raise ValueError("config mode must be 'dry' or 'live'")
        kw["mode"] = mode
        if "dry_balance_lamports" in d:
            kw["dry_balance_lamports"] = int(d["dry_balance_lamports"])
        for k in ("stop_file", "halt_file"):
            if d.get(k):
                if mode == LIVE:
                    raise ValueError(f"live refused: {k} cannot be set in live (the wallet-wide files are pinned)")
                kw[k] = str(d[k])
        return cls(**kw)


# --- the order port ---------------------------------------------------------------------------------------------------------------


class OrderPort:
    """What the Bridge needs from the send side. Everything here may touch the network in the live port; the dry port touches nothing."""

    name = "port"
    live = False

    def mint_for_pool(self, pool: str, meta: Mapping[str, Any]) -> str | None:
        raise NotImplementedError

    def universe_problem(self, pool: str, mint: str) -> str | None:
        """None when the pool passes Am.2 item 6 (WSOL quote, V in [17.5e9, 17.7e9]); else a refusal label."""
        raise NotImplementedError

    def balance(self) -> int | None:
        raise NotImplementedError

    def buy(self, order: Mapping[str, Any]) -> tuple[bool, str | None]:
        """Send (live) or log (dry) the buy. (True, None) when sent; (False, label) when the port refused before sending."""
        raise NotImplementedError

    def sell(self, order: Mapping[str, Any], reason: str) -> tuple[bool, str | None]:
        raise NotImplementedError

    def holdings(self) -> dict[str, str] | None:
        """mint -> "pending" (a signed tx is in flight) | "open" (held, nothing in flight), from the executor's own state. None = no executor (dry)."""
        return None

    def warm(self, mints: list[str]) -> None:
        """S2: refresh the sell plan of every open position (batched vault read, blockhash). Never sends."""

    def position_return(self, mint: str) -> float | None:
        """Mark-to-quote return on an open position from the warm snapshot, or None when unpriced."""
        return None

    def drain_ledger(self) -> list[tuple[str, str, dict[str, Any]]]:
        """hv4_c0 parity: the executor's own fill rows since the last call, (kind "buy"|"sell", mint, kw) in order. A dry port has none."""
        return []

    def poll(self) -> list[dict[str, Any]]:
        """Advance in-flight txs; return events: {order_id|mint, event: buy_landed|buy_failed|sell_landed|sell_failed|sell_stuck, pnl_lamports,
        landed_slot, ...}."""
        return []


class DryPort(OrderPort):
    """No RPC, no key, no network. Records the order it would send; positions open and close instantly. The balance is a config number."""

    name = "dry"
    live = False

    def __init__(self, balance_lamports: int) -> None:
        self._balance = balance_lamports
        self.would_send: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []

    def mint_for_pool(self, pool: str, meta: Mapping[str, Any]) -> str | None:
        m = meta.get("mint") or meta.get("base_mint")
        return str(m) if m else f"pool:{pool}"  # dry only: a stand-in key so one pool is not entered twice

    def universe_problem(self, pool: str, mint: str) -> str | None:
        return None  # not checked in dry (no RPC); the bridge log says so

    def balance(self) -> int | None:
        return self._balance

    def buy(self, order: Mapping[str, Any]) -> tuple[bool, str | None]:
        self.would_send.append({"side": "buy", "order_id": order["order_id"], "mint": order["mint"], "stake_lamports": order["stake_lamports"]})
        self._events.append({"order_id": order["order_id"], "event": "buy_landed", "pnl_lamports": 0, "landed_slot": None, "dry": True})
        return True, None

    def sell(self, order: Mapping[str, Any], reason: str) -> tuple[bool, str | None]:
        self.would_send.append({"side": "sell", "order_id": order["order_id"], "mint": order["mint"], "reason": reason})
        self._events.append({"order_id": order["order_id"], "event": "sell_landed", "pnl_lamports": 0, "landed_slot": None, "dry": True})
        return True, None

    def poll(self) -> list[dict[str, Any]]:
        out, self._events = self._events, []
        return out


# --- bridge state (write-ahead) ---------------------------------------------------------------------------------------------------


def _atomic_write(path: Path, body: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        fh.write(json.dumps(body, separators=(",", ":"), sort_keys=True))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass
class BridgeState:
    mode: str = DRY
    prev: str = GENESIS  # sha of the last row consumed
    seq: int = -1
    cursor: dict[str, int] = field(default_factory=dict)  # day file name -> byte offset consumed
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)  # order_id -> live record (status: buy_sending|buy_sent|open|sell_sending|sell_sent|closed|refused)
    bought: list[str] = field(default_factory=list)  # mints ever attempted; never re-bought
    attempts: int = 0
    realized_lamports: int = 0
    days: dict[str, dict[str, int]] = field(default_factory=dict)  # UTC day -> {trades, realized}
    latches: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    start_seen: bool = False
    first_start_ms: int | None = None  # unused since #965 N2: the 14-day window start is the write-once file c0.WINDOW_DIR/<bot_id>.json, outside this state
    gap_noted_ms: int = 0  # hv4_c0: the newest gap instant seen (persisted: a restart must not clear against an older review)
    gap_hold_until_ms: int = 0  # hv4_c0, DEC-031 Am.21 3.1: no new buy before this (max over records of max(instant, receipt) + c0.FEED_GAP_HOLD_MS); persisted
    gap_known_ms: list[int] = field(default_factory=list)  # hv4_c0, Am.21 3.1/3.3: coverage-losing instants seen (sorted); unreviewed ones are never pruned
    gap_pruned_upto_ms: int = 0  # hv4_c0, Am.21: the largest instant ever pruned from gap_known_ms (reviewed and older than GAP_KNOWN_KEEP_MS); never registered again
    read_pin: dict[str, Any] | None = None  # hv4_c0: identity {path, dev, ino, sha256} of the recorded read, pinned at the first good check and compared on every run

    def save(self, path: Path) -> None:
        _atomic_write(path, self.__dict__)

    @classmethod
    def load(cls, path: Path, mode: str) -> "BridgeState":
        if not path.exists():
            return cls(mode=mode)
        raw = json.loads(path.read_text())
        st = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        if st.mode != mode:
            raise SystemExit(f"bridge state file mode {st.mode!r} does not match run mode {mode!r}")
        return st


def root_ok_problem(path: Path) -> str | None:
    """None when `path` passes the H5 root-file check (root:root 0644 regular file in a root-owned non-writable dir). Imported lazily: h5_executor
    pulls the solana stack, and a pure-dry bridge does not need it until a live check is asked for."""
    from tools import h5_executor as h5

    return h5.root_file_problem(Path(path))  # a str is accepted (callers pass both); root_file_problem needs .parent


def read_tier(path: Path, root_checks: bool, allowed: Mapping[str, Any] | tuple[str, ...] | None = None) -> tuple[str, str | None]:
    """(tier, problem). Only tiers in TIERS exist here; anything else (including H5's T1/T2) is T0 with a reason."""
    if root_checks:
        why = root_ok_problem(path)
        if why:
            return "T0", f"tier_file_{why}"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return "T0", "tier_file_missing"
    except OSError:
        return "T0", "tier_file_unreadable"
    try:
        text = os.read(fd, 64).decode("ascii", "replace").strip()
    except OSError:
        return "T0", "tier_file_unreadable"
    finally:
        os.close(fd)
    return (text, None) if text in (TIERS if allowed is None else allowed) else ("T0", "tier_file_invalid")


# --- the bridge -------------------------------------------------------------------------------------------------------------------


class Bridge:
    def __init__(self, cfg: Config, port: OrderPort, *, now_ms: Callable[[], int] | None = None,
                 live_ok_path: Path = LIVE_OK_PATH, tier_path: Path = TIER_PATH, root_checks: bool = True,
                 close_blocked: Callable[[str], bool | None] | None = None, parity_runner: Callable[[list[str]], int] | None = None,
                 owner_dec_path: Path | str | None = None, owner_pin: str | None = None, gap_clear_dir: Path | str | None = None) -> None:
        """close_blocked: optional S3 hook, mint -> True (blocked) | False (ok). Interface only here. When set, a True, a None or a raise all refuse."""
        if c0.bot_id_problem(cfg.bot_id):
            raise SystemExit(f"bridge refused: {c0.bot_id_problem(cfg.bot_id)}")
        self.cfg = cfg
        self.close_blocked = close_blocked
        self.port = port
        self.dry_port = port if not port.live else DryPort(cfg.dry_balance_lamports)  # an entry that is not live-eligible (LIVE_OK missing) is routed here: logged, never sent
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.live_ok_path = live_ok_path
        self.tier_path = tier_path
        self.root_checks = root_checks
        if cfg.mode == LIVE and not port.live:
            raise SystemExit("live config with a non-live port: refused")
        if cfg.mode == DRY and port.live:
            raise SystemExit("dry config with a live port: refused")
        self.sd = Path(cfg.state_dir)
        self.state_path = self.sd / f"bridge-state-{cfg.mode}.json"
        self.log_path = self.sd / f"bridge-log-{cfg.mode}.jsonl"
        self.state = BridgeState.load(self.state_path, cfg.mode)
        self.dlog = DecisionLog(cfg.root)
        self.owner_dec_path = Path(owner_dec_path) if owner_dec_path is not None else Path(c0.OWNER_DEC_PATH)
        self.owner_pin = owner_pin
        self.gap_clear_dir = str(gap_clear_dir) if gap_clear_dir is not None else c0.GAP_CLEAR_DIR
        self._gks: set[int] | None = None  # the in-memory set of state.gap_known_ms (_gap_known)
        self._gks_src: list[int] | None = None
        self._read_ident: dict[str, Any] = {}
        if cfg.profile == c0.PROFILE:
            first, why = c0.window_start_ms(cfg.bot_id, self.now_ms(), create=True)
            why = why or c0.end_cap_problem(cfg.end_ms, first)
            if why:
                raise SystemExit(f"hv4_c0 refused: {c0.refusal_text(why)}")
            self.window_start_ms = first
            self._read_ident = dict(self.state.read_pin or {})
        self.guarded = cfg.profile == c0.PROFILE  # hv4_c0: every decision-log read is path-guarded (symlink, hard link, swap, arena realpath), in the object itself
        self._last_warm = 0
        self._bal_ms: int | None = None  # hv4_c0: when the wallet balance was last read successfully
        self._chain_ok_ms: int | None = None  # hv4_c0: when the decision log was last read and verified without a break
        self.gates = None
        if cfg.profile == c0.PROFILE and cfg.parity_dir:  # parity, twin and tie-out gates (DEC-032 s5): hv4_c0 only, active when the paths are configured
            self.gates = c0p.build_gates(cfg.parity_dir, cfg.twins_file, cfg.tieout_dir, cfg.stop_file, self._parity_state, runner=parity_runner)
        self._last_sell_try: dict[str, int] = {}
        self._a3_polled_ms: int | None = None
        self._a3_refuse: str | None = None  # DEC-032 Am.3: a pending pooled guard refuses new buys (no latch)
        self._started_ms = self.now_ms()
        self._last_row_ms: int | None = None  # hv4_c0: the newest decision row consumed (any kind)
        if cfg.profile == c0.PROFILE:
            self._log("bridge_start", **c0.start_row_fields())  # the pins this process runs under (key frozen_sha as in the runner's own `start` row)
        self._verify_at_start()
        if not {"chain_break", "sealed_path", "read_record"} & set(self.state.latches):
            self._chain_ok_ms = self.now_ms()  # verified just now
        self.reconcile()

    # -- plumbing
    def _log(self, kind: str, **kw: Any) -> None:
        self.sd.mkdir(parents=True, exist_ok=True)
        row = {"schema": SCHEMA, "kind": kind, "ts_ms": self.now_ms(), "bot_id": self.cfg.bot_id, "mode": self.cfg.mode, **kw}
        with self.log_path.open("a") as fh:
            fh.write(canon(row) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _save(self) -> None:
        self.state.save(self.state_path)

    def _count(self, k: str) -> None:
        self.state.counts[k] = self.state.counts.get(k, 0) + 1

    def _latch(self, name: str, why: str) -> None:
        if name not in self.state.latches:
            self.state.latches[name] = why
            self._log("latch", latch=name, why=why)
            print(f"arena_live_bridge ALERT latch={name} why={why}", flush=True)
            self._save()

    def _read_gate(self) -> bool:
        """hv4_c0: before ANY decision row is read, the bot's recorded PASS read must exist (c0.read_record_problem; any hv4- bot_id). Checked on every read
        against the identity (dev, ino, sha256) of the first good one. A hit latches read_record and nothing is read."""
        if self.cfg.profile != c0.PROFILE:
            return True
        if "read_record" in self.state.latches:
            return False
        why, ident = c0.read_record_problem(self.cfg.reads_dir, self.cfg.bot_id, self._read_ident or None, self.cfg.mode == DRY and c0.synthetic_root(self.cfg.root))
        if why:
            self._latch("read_record", why)
            return False
        self._read_ident = ident
        if self.state.read_pin is None:  # persisted: a restart, a new process or a swapped record is compared with THIS identity
            self.state.read_pin = dict(ident)
            self._save()
        return True

    def _verify_at_start(self) -> None:
        """Full chain check of what exists now. A break latches chain_break (the bridge then never buys). The cursor is set only on a clean pass; a
        restart resumes from the saved cursor, so rows already handled are not replayed (order_id idempotency covers the crash window)."""
        if self.guarded:
            if not self._read_gate():
                return
            try:
                names = c0.guarded_listdir(self.cfg.root, self.cfg.bot_id)
                datas = [c0.guarded_read(self.cfg.root, self.cfg.bot_id, n) for n in names]
            except c0.SealedPath as exc:
                return self._latch("sealed_path", str(exc))
            self._verify_bytes(datas, names)
            return
        try:
            self.dlog.verify(self.cfg.bot_id)
        except LogChainError as exc:
            self._latch("chain_break", str(exc)[:160])

    def _verify_bytes(self, datas: list[bytes], names: list[str]) -> None:
        """DecisionLog.verify's rule over bytes this object already read through the guard (so the verified bytes are the guarded bytes)."""
        prev, seq = GENESIS, -1
        try:
            for name, data in zip(names, datas):
                if data and not data.endswith(b"\n"):
                    raise LogChainError(f"{self.cfg.bot_id}: torn last line in {name}")
                for line in data.splitlines():
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                        sha = row.pop("sha")
                    except (ValueError, KeyError):
                        raise LogChainError(f"{self.cfg.bot_id}: unreadable row in {name}") from None
                    if row.get("prev") != prev or row.get("seq") != seq + 1 or sha256_hex(prev + canon(row)) != sha:
                        raise LogChainError(f"{self.cfg.bot_id}: chain broken at seq {seq + 1} in {name}")
                    prev, seq = sha, row["seq"]
        except LogChainError as exc:
            self._latch("chain_break", str(exc)[:160])

    # -- tailing
    def read_new_rows(self) -> list[dict[str, Any]]:
        """New complete rows after the cursor, each checked against the chain (prev, seq+1, sha). A break latches and stops the read."""
        out: list[dict[str, Any]] = []
        if "chain_break" in self.state.latches or "sealed_path" in self.state.latches or "read_record" in self.state.latches:
            return out
        st = self.state
        if self.guarded and not self._read_gate():
            return out
        if self.guarded:  # check and read every file first (all or nothing): a hit latches sealed_path and no row of this call is used
            try:
                gfiles = [(Path(n), c0.guarded_read(self.cfg.root, self.cfg.bot_id, n)) for n in c0.guarded_listdir(self.cfg.root, self.cfg.bot_id)]
            except c0.SealedPath as exc:
                self._latch("sealed_path", str(exc))
                return out
        else:
            gfiles = [(f, None) for f in self.dlog.files(self.cfg.bot_id)]
        for f, gdata in gfiles:
            off = st.cursor.get(f.name, 0)
            data = gdata if gdata is not None else f.read_bytes()
            if len(data) <= off:
                continue
            chunk = data[off:]
            end = chunk.rfind(b"\n")
            if end < 0:
                continue  # a partial last line: the writer is mid-append; wait
            consumed = 0
            for line in chunk[: end + 1].splitlines(keepends=True):
                if line.strip():
                    try:
                        row = json.loads(line)
                        sha = row.pop("sha")
                    except (ValueError, KeyError):
                        self._latch("chain_break", f"unreadable row in {f.name}")
                        return out
                    if row.get("prev") != st.prev or row.get("seq") != st.seq + 1 or sha256_hex(st.prev + canon(row)) != sha:
                        self._latch("chain_break", f"chain broken at seq {st.seq + 1} in {f.name}")
                        return out
                    st.prev, st.seq = sha, row["seq"]
                    row["sha"] = sha
                    out.append(row)
                consumed += len(line)
            st.cursor[f.name] = off + consumed
        return out

    # -- gates
    def _files(self) -> tuple[bool, bool]:
        return Path(self.cfg.halt_file).exists(), Path(self.cfg.stop_file).exists()

    # -- hv4_c0 parity / twin / tie-out (tools/hv4_c0_parity.py); every method below is a no-op unless self.gates is set
    def _parity_state(self, now: int) -> dict[str, Any]:
        """state.json of the parity tool: a stamp only for a read that succeeded just now (a missing stamp is P7 and no buy)."""
        try:
            bal = self.port.balance()
        except Exception:  # noqa: BLE001 - an unreadable balance is a missing stamp
            bal = None
        if bal is not None:
            self._bal_ms = now
        loss_ok = True  # the committed loss is computed from the state this process holds; it cannot fail to read
        control_ok = self.cfg.mode == DRY or (self.effective_live()[0] and self.tier_limits()[2] is None)
        chain_ok = (self._chain_ok_ms is not None and 0 <= now - self._chain_ok_ms <= c0p.CHAIN_FRESH_MS
                    and "chain_break" not in self.state.latches and "sealed_path" not in self.state.latches)
        return c0p.parity_state(now, loss_ok=loss_ok, balance_ms=self._bal_ms if bal is not None else None, twins_file=self.cfg.twins_file,
                                trip_ids=self.gates.recorder.trips, chain_ok=chain_ok, control_ok=bool(control_ok), hard_halts=self.state.latches)

    def _first_live_send_ms(self) -> int | None:
        sent = [o["send_ms"] for o in self.state.orders.values() if o.get("live") and isinstance(o.get("send_ms"), int)]
        return min(sent) if sent else None

    def gate_refusal(self, now: int) -> str | None:
        """Before a buy: a fresh rc-0 parity result, a twin row for every live trip, and (once a UTC day has closed) a signed-off tie-out. None without gates."""
        if self.gates is None:
            return None
        why = self.gates.parity_refusal(now) or self.gates.twin_refusal(now) or self.gates.tieout_refusal(now, self._first_live_send_ms())
        self._latch_parity_hard()
        if why and why.startswith("twin_gate:pin_mismatch"):
            self._latch("twin_pin_mismatch", why)
        return why

    def export_tieout(self, day: str) -> str | None:
        """hv4_c0 only (precondition 8): write the bridge's book for the CLOSED UTC `day` to <tieout_dir>/LEDGER-<day>.json for the daily tie-out (tools/hv4_c0_tieout.py;
        write-once). A fee or stake that does not match the schedule, the order or the parity trips file latches `tieout_mismatch` (a hard halt). Returns "created",
        "same", or None when this bridge has no tie-out directory; raises ValueError on an unusable input or a conflicting earlier file."""
        if self.gates is None or not self.cfg.tieout_dir:
            return None
        from tools import arena_tieout_ledger as atl
        from tools import hv4_c0_tieout as tie

        self._save()
        try:
            return tie.export_day(self.sd / "probe" / "fills.jsonl", self.state_path, self.cfg.tieout_dir, day, self.now_ms(), self.gates.recorder.trips_path)
        except tie.TieoutMismatch as exc:
            self._latch("tieout_mismatch", str(exc)[:300])
            return "mismatch"
        except (atl.BadLedger, tie.WriteConflict, OSError) as exc:
            raise ValueError(f"tieout_export_refused: {exc}") from exc

    def _latch_parity_hard(self) -> None:
        run = self.gates.parity_run if self.gates is not None else None
        if run is not None and run.get("rc") == c0p.RC_HARD:
            self._latch("parity_hard_halt", f"arena_live_parity rc {c0p.RC_HARD} at {run.get('at_ms')}")

    def feed_ledger(self) -> None:
        """Move the executor's fill rows into the parity recorder. A landed buy that spent other than the order's S is a hard latch (live stake = S = twin stake)."""
        if self.gates is None:
            return
        rows = self.port.drain_ledger()
        if not rows:
            return
        now = self.now_ms()
        by_mint = {o["mint"]: o for o in self.state.orders.values()}
        for kind, mint, kw in rows:
            o = by_mint.get(mint)
            if kind == "buy":
                bound = c0p.exit_bound_ms(o["decision_ms"], self.cfg.max_hold_ms, BACKSTOP_EXTRA_MS) if o else None
                bad = c0p.stake_problem(kw, o["stake_lamports"]) if o else ("stake_mismatch:landed_buy_without_order" if kw.get("landed") else None)  # a landed buy with no order is a stake mismatch, not a skip
                if bad:
                    self._latch("stake_mismatch", bad)
                err = self.gates.recorder.on_buy(mint, kw, now, bound, stake_fallback=o["stake_lamports"] if o else None)
                if err:
                    self._latch("landed_buy_unreadable", f"{mint} {err}")
            elif kind == "sell":
                self.gates.recorder.on_sell(mint, kw, now)
        if self.gates.recorder.broken:
            self._latch("parity_hard_halt", str(self.gates.recorder.broken))
        self._save()

    def effective_live(self) -> tuple[bool, str | None]:
        """(live, why_dry). A live-configured bridge sends only while LIVE_OK passes the root check; otherwise this entry is DRY (logged, not sent)."""
        if self.cfg.mode != LIVE:
            return False, "mode_dry"
        why = root_ok_problem(self.live_ok_path) if self.root_checks else (None if self.live_ok_path.exists() else "missing")
        if not why and self.cfg.profile == c0.PROFILE:  # the C0 LIMITS file names the bot_id
            try:
                why = c0.live_ok_problem(self.live_ok_path.read_text(), self.cfg.bot_id)
            except OSError:
                why = "unreadable"
            why = why[len("live_ok_"):] if why and why.startswith("live_ok_") else why
            if not why:  # the owner's line in a ROOT-OWNED file outside the installed tree, whose sha256 is the pinned blob
                why = c0.owner_line_problem(self.owner_dec_path, self.cfg.bot_id, self.owner_pin, root_ok_problem if self.root_checks else None)
        return (False, f"live_ok_{why}") if why else (True, None)

    def tier_limits(self) -> tuple[str, dict[str, int], str | None]:
        if self.cfg.profile == c0.PROFILE:  # the C0 table is compiled; the TIER file must read exactly T1 (a live start), config only tightens
            tier, prob = read_tier(self.tier_path, self.root_checks and self.cfg.mode == LIVE, allowed=(c0.TIER_NAME,))
            return c0.TIER_NAME, {k: min(getattr(self.cfg, k), v) for k, v in c0.C0_LIMITS.items()}, prob
        tier, prob = read_tier(self.tier_path, self.root_checks and self.cfg.mode == LIVE)
        t = TIERS[tier]
        return tier, {k: min(getattr(self.cfg, k), v) for k, v in t.items()}, prob

    def in_scope(self, o: Mapping[str, Any], scope: bool) -> bool:
        """Orders count against the limits and the floor of their own kind: a live-config bridge does not let dry-routed orders (LIVE_OK missing)
        use up live headroom. In a dry-config bridge every order is dry and they all count against the dry limits."""
        return (bool(o["live"]) or self.cfg.mode == DRY) == scope

    def open_orders(self, scope: bool = True) -> dict[str, dict[str, Any]]:
        return {k: o for k, o in self.state.orders.items()
                if o["status"] in ("buy_sending", "buy_sent", "open", "sell_sending", "sell_sent") and self.in_scope(o, scope)}

    def floor_problem(self, stake: int, bal: int | None, scope: bool = True) -> str | None:
        if bal is None:
            return "balance_unreadable"
        opens = self.open_orders(scope)
        open_stakes = sum(o["stake_lamports"] for o in opens.values())
        fee, sell_fee = (c0.TRIP_FEE_RESERVE_LAMPORTS, c0.SELL_FEE_RESERVE_LAMPORTS) if self.cfg.profile == c0.PROFILE else (FEE_RESERVE_LAMPORTS, SELL_FEE_RESERVE_LAMPORTS)
        projected = (bal - open_stakes - stake - fee - RENT_RESERVE_LAMPORTS - BUFFER_LAMPORTS
                     - sell_fee * len(opens))
        return "wallet_floor" if projected < self.cfg.wallet_floor_lamports else None

    def entry_refusal(self, row: Mapping[str, Any], now: int, lim: dict[str, int], scope: bool = True) -> str | None:
        if self.state.latches:
            return "latched:" + sorted(self.state.latches)[0]
        halt, stop = self._files()
        if halt:
            return "halt_file"
        if stop:
            return "stop_file"
        why = self.feed_refusal(now) or self.gap_entry_refusal(row)
        if why:
            return why
        if now >= self.cfg.end_ms:
            return "end_ms"
        if now - int(row["ms"]) > MAX_DECISION_AGE_MS:
            return "stale_decision"
        if len(self.open_orders(scope)) >= lim["max_open"]:
            return "max_open"
        day = self.state.days.get(utc_day(now), {})
        if day.get("trades" if scope else "dry_trades", 0) >= lim["max_trades_per_day"]:
            return "max_trades_per_day"
        if day.get("realized", 0) <= -lim["daily_loss_lamports"]:
            return "daily_loss_cap"
        if self.state.realized_lamports <= -lim["total_loss_lamports"]:
            return "total_loss_cap"
        return None

    def feed_refusal(self, now: int) -> str | None:
        """hv4_c0 TRANSPORT guard (an EXTRA guard, NOT the DEC-032 s5.2 halt, which is the `feed_break` latch in gap_tick): new buys stop while the forwarder's status file (<root>/transport/<bot_id>.status.json) is missing, stale, not `connected` or
        silent past tools.hv4_live_c0.FEED_FRAME_MAX_MS. Not a latch: it clears when the feed resumes. Exits are unaffected. LIVE always checks it; a DRY bridge
        on a root without SYNTHETIC_MARKER needs it too."""
        if self.cfg.profile != c0.PROFILE:
            return None
        if self._a3_refuse:
            self._count("a3_guard_pending_refusal")
            return self._a3_refuse
        if not c0.FEED_GAP_MANUAL_REVIEW:  # DEC-031 Am.21 3.1: the self-clearing short hold after a coverage-losing record, timed from its receipt (_note_gap)
            why = c0.gap_hold_problem(self.state.gap_hold_until_ms, now)
            if why:
                self._count("feed_gap_hold_refusal")
                return why
        why = c0.runner_silent_problem(self._last_row_ms, self._started_ms, now)
        if why:
            self._count("runner_silent_refusal")
            return why
        path = c0.feed_status_path(self.cfg.root, self.cfg.bot_id)
        if self.cfg.mode != LIVE and not os.path.lexists(path) and c0.synthetic_root(self.cfg.root):
            return None  # only a synthetic fixture root may run without a forwarder status; any other root (dry included) refuses
        why = c0.feed_gap_problem(path, self.cfg.bot_id, now)
        if why:
            self._count("transport_stall_refusal")
        return f"transport_stall:{why}" if why else None

    def _note_gap(self, gap_ms: int, why: str) -> None:
        """One COVERAGE-LOSING record (kind and instant only), e.g. a `gap` row of the decision stream. Open trades are marked `gap` (DEC-032 s5.2; Am.21 3.1).
        With c0.FEED_GAP_MANUAL_REVIEW (s5 item 2 as built) feed_break latches. Otherwise (DEC-031 Am.21 item 3) it goes through the batched path _note_gaps."""
        if c0.FEED_GAP_MANUAL_REVIEW:
            if gap_ms > self.state.gap_noted_ms:
                self.state.gap_noted_ms = gap_ms
                if self.gates is not None:
                    self.gates.recorder.note_gap(gap_ms)
            self._latch(c0.FEED_BREAK_LATCH, why)
            self._save()
            return
        if self._note_gaps([gap_ms], why):
            self._save()

    def _gap_known(self) -> set[int]:
        """The coverage-losing instants already registered, as a set (QP/reviewer part A: a list scan cost 11.9 ms per tick at 2,000 gaps). Rebuilt from the
        persisted list after a load or a prune."""
        lst = self.state.gap_known_ms
        if self._gks is None or self._gks_src is not lst or len(self._gks) != len(lst):
            self._gks, self._gks_src = set(lst), lst
        return self._gks

    def _note_gaps(self, instants: Iterable[int], why: str) -> bool:
        """DEC-031 Am.21 3.1, BATCHED (reviewer part A: one save and one log line per record cost 33 s for 2,000 records inside a tick, with exits waiting):
        register every instant not seen before and later than state.gap_pruned_upto_ms (an instant that was pruned, i.e. reviewed AND older than
        c0.GAP_KNOWN_KEEP_MS, is never registered again, so it never re-arms the hold). The review itself is NOT a filter here (QP on 99222ec7: a
        future-dated review file must not switch off the hold, the per-entry refusal or the trip mark of a record the bridge has never seen). Among the new
        instants, only those not older than c0.GAP_KNOWN_KEEP_MS restart the hold (an aged-out instant is tracked for the 24 h latch but never re-arms it).
        One hold update, one trip mark, one summary log line. Returns True when the state changed; the CALLER saves once."""
        now = self.now_ms()
        ks = self._gap_known()
        floor = self.state.gap_pruned_upto_ms
        new = sorted({ms for ms in instants if ms not in ks and ms > floor})
        if not new:
            return False
        ks.update(new)
        self.state.gap_known_ms.extend(new)
        self.state.gap_known_ms.sort()
        if len(self.state.gap_known_ms) > c0.GAP_KNOWN_MAX:
            self._latch(c0.FEED_BREAK_LATCH, f"{c0.FEED_GAP_LOG_LATCH}:gap_known_overflow")  # never a silent drop
        newest = new[-1]
        if newest > self.state.gap_noted_ms:
            self.state.gap_noted_ms = newest
            if self.gates is not None:
                self.gates.recorder.note_gap(newest)
        rearm = [ms for ms in new if now - ms <= c0.GAP_KNOWN_KEEP_MS]
        if rearm:
            self.state.gap_hold_until_ms = max(self.state.gap_hold_until_ms, max(rearm[-1], now) + c0.FEED_GAP_HOLD_MS)
        late = sum(1 for ms in new if now - ms > c0.FEED_GAP_LATENCY_BOUND_MS)
        if late:
            self.state.counts["feed_gap_late"] = self.state.counts.get("feed_gap_late", 0) + late
        self._log("feed_gap_hold", n=len(new), first_ms=new[0], newest_ms=newest, seen_ms=now, rearmed=len(rearm), late=late,
                  hold_until_ms=self.state.gap_hold_until_ms, why=why[:200])
        return True

    def gap_entry_refusal(self, row: Mapping[str, Any]) -> str | None:
        """DEC-031 Am.21 3.1 per-entry refusal (hv4_c0, not in the as-built mode): no buy on an entry decided at or before the newest coverage-losing
        instant the bridge has seen. Logged as the skip `feed_gap`."""
        if self.cfg.profile != c0.PROFILE or c0.FEED_GAP_MANUAL_REVIEW:
            return None
        why = c0.gap_entry_problem(row.get("ms"), self.state.gap_noted_ms)
        if why:
            self._count("feed_gap_refusal")
        return why

    def gap_tick(self, now: int) -> None:
        """hv4_c0 feed-gap rule. Records: the forwarder's gap log, the tape-gap stream (tools/hv4_gap_relay.py), a `gap` row in the decision stream.
        c0.FEED_GAP_MANUAL_REVIEW True = DEC-032 s5 item 2 as built: every gap record latches feed_break, removed ONLY when the feed has been gap-free for 10
        minutes counted from the END of the gap AND the manager's root-owned review file names exactly the newest gap instant; a forwarder stopped_mismatch
        latches too. False = DEC-031 Am.21 item 3 (see the hv4_live_c0 block): coverage-losing records hold and refuse per entry (_note_gap), transport events
        and stopped_mismatch leave the latch, a coverage-losing record older than 24 h and later than the review latches feed_break until a review covers it,
        and a bad gap log latches until it reads clean, the forwarder is connected and the review covers the newest coverage-losing instant."""
        if self.cfg.profile != c0.PROFILE:
            return
        fentries, resumes, bad = c0.gap_log_entries(os.path.join(self.cfg.root, "transport", f"{self.cfg.bot_id}.gaps.jsonl"))
        tentries, _tres, tbad = c0.gap_log_entries(os.path.join(self.cfg.root, "transport", f"{self.cfg.bot_id}.tapegaps.jsonl"))  # tools/hv4_gap_relay.py: the book's own TAPE gaps
        bad = bad or tbad
        sp = c0.feed_status_path(self.cfg.root, self.cfg.bot_id)
        status_why = c0.feed_gap_problem(sp, self.cfg.bot_id, now) if os.path.lexists(sp) else "feed_status_missing"
        if c0.FEED_GAP_MANUAL_REVIEW:
            gaps = [*(ms for _k, ms in fentries), *(ms for _k, ms in tentries)]
            if bad:
                self._latch(c0.FEED_BREAK_LATCH, bad)  # an unreadable, oversize or malformed gap log is not a clear
            newest = max([*gaps, self.state.gap_noted_ms]) if (gaps or self.state.gap_noted_ms) else None
            since = max([*gaps, *resumes, self.state.gap_noted_ms]) if newest is not None else None
            if gaps and max(gaps) > self.state.gap_noted_ms:
                self._note_gap(max(gaps), f"gap record at {max(gaps)}")
            if status_why == "feed_state_stopped_mismatch":
                self._latch(c0.FEED_BREAK_LATCH, "forwarder_stopped_mismatch")
            if c0.FEED_BREAK_LATCH in self.state.latches and newest is not None and not bad and status_why is None:
                if self._gap_cleared(newest, now, since):
                    del self.state.latches[c0.FEED_BREAK_LATCH]
                    self._log("latch_cleared", latch=c0.FEED_BREAK_LATCH, reviewed_gap_ms=newest)
                    self._save()
            return
        # DEC-031 Am.21 item 3 (batched: at most one state save per tick from the bookkeeping below)
        if bad:
            self._latch(c0.FEED_BREAK_LATCH, f"{c0.FEED_GAP_LOG_LATCH}:{bad}")
        reviewed = self._gap_reviewed_ms()
        loss = [ms for k, ms in fentries if c0.gap_loses_data(k)]
        loss.extend(ms for _k, ms in tentries)
        dirty = self._note_gaps(loss, "coverage-losing records")
        unrev = c0.gap_unreviewed(self.state.gap_known_ms, reviewed, now)
        if unrev:
            self._latch(c0.FEED_BREAK_LATCH, f"{c0.FEED_GAP_UNREVIEWED}:{unrev[0]}")
        why = self.state.latches.get(c0.FEED_BREAK_LATCH)
        if why is not None and not unrev:
            clear = False
            if why.startswith(c0.FEED_GAP_UNREVIEWED + ":"):
                clear = not bad
            elif why.startswith(c0.FEED_GAP_LOG_LATCH + ":"):
                clear = not bad and status_why is None and reviewed >= self.state.gap_noted_ms and len(self.state.gap_known_ms) <= c0.GAP_KNOWN_MAX
            if clear:
                del self.state.latches[c0.FEED_BREAK_LATCH]
                self._log("latch_cleared", latch=c0.FEED_BREAK_LATCH, why=why[:200], reviewed_gap_ms=reviewed)
                dirty = True
        keep = [ms for ms in self.state.gap_known_ms if ms > reviewed or now - ms <= c0.GAP_KNOWN_KEEP_MS]  # unreviewed instants are never pruned
        if len(keep) != len(self.state.gap_known_ms):
            pruned = [ms for ms in self.state.gap_known_ms if not (ms > reviewed or now - ms <= c0.GAP_KNOWN_KEEP_MS)]
            self.state.gap_pruned_upto_ms = max(self.state.gap_pruned_upto_ms, max(pruned))  # persisted: a pruned instant is never registered again
            self.state.gap_known_ms = keep  # _gap_known rebuilds the set
            dirty = True
        if dirty:
            self._save()

    def _gap_reviewed_ms(self) -> int:
        p = Path(self.gap_clear_dir) / f"{self.cfg.bot_id}.json"
        try:
            return c0.gap_review_ms(str(p), self.cfg.bot_id, root_ok_problem if self.root_checks else None, self.now_ms())
        except Exception:  # noqa: BLE001 - nothing reviewed; never propagates into tick()
            return 0

    def _gap_cleared(self, last: int, now: int, since: int | None = None) -> bool:
        p = Path(self.gap_clear_dir) / f"{self.cfg.bot_id}.json"
        try:
            return c0.gap_clear_problem(str(p), self.cfg.bot_id, last, now, root_ok_problem if self.root_checks else None, since) is None
        except Exception:  # noqa: BLE001 - any failure refuses the clear; it must never propagate into tick() (exits keep running)
            return False

    def a3_tick(self, now: int) -> None:
        """hv4_c0 A3 halt: poll the A3 daily record (throttled) and latch a3_pins_changed on any halt flag (pins_changed included; synthetic_share_high is the pooled guard's, DEC-032 Am.3) or a record that cannot be
        read. A latch refuses new buys only; exits keep running. Live needs a configured record (main refuses a live start without it)."""
        if self.cfg.profile != c0.PROFILE or (not self.cfg.a3_record_file and self.cfg.mode != LIVE):
            return
        if self._a3_polled_ms is not None and 0 <= now - self._a3_polled_ms < c0.A3_POLL_MS:
            return
        self._a3_polled_ms = now
        why, self._a3_refuse = c0.a3_problem(self.cfg.a3_record_file, now) if self.cfg.a3_record_file else ("a3_record_file_missing", None)
        if why:
            self._latch(c0.A3_LATCH, why)

    # -- decisions
    def handle_row(self, row: dict[str, Any]) -> None:
        k = row.get("kind")
        if k == "start":
            self.state.start_seen = True
            if row.get("frozen_sha") != self.cfg.frozen_sha:
                self._latch("frozen_sha_mismatch", f"book {str(row.get('frozen_sha'))[:12]} pinned {self.cfg.frozen_sha[:12]}")
        elif k == "gap" and self.cfg.profile == c0.PROFILE:
            if isinstance(row.get("ms"), int) and not isinstance(row.get("ms"), bool):  # kind and instant only; no other field is read
                self._note_gap(int(row["ms"]), f"gap row at {row['ms']}")
        elif k == "entry":
            self.handle_entry(row)
        elif k == "exit":
            self.handle_exit(row)
        elif k == "order_refused" and row.get("side") == "buy":
            o = self.state.orders.get(row.get("order_id"))
            if o and o["status"] in ("buy_sent", "open"):
                self._count("paper_refused_live_open")
                o["want_exit"] = "paper_refused"  # the paper book never held it; close ours
                self._try_exit(row["order_id"], "paper_refused")

    def handle_entry(self, row: dict[str, Any]) -> None:
        now = self.now_ms()
        oid = str(row["order_id"])
        if oid in self.state.orders:
            self._count("duplicate_entry")
            self._log("duplicate_entry", order_id=oid, seq=row["seq"])
            return
        if not self.state.start_seen:
            return self._refuse(row, "no_start_row")
        live, why_dry = self.effective_live()
        tier, lim, tier_prob = self.tier_limits()
        scope = live or self.cfg.mode == DRY
        why = self.entry_refusal(row, now, lim, scope)
        pool = str(row.get("pool"))
        meta = row.get("meta") or {}
        mint = self.port.mint_for_pool(pool, meta) if not why else None
        if not why and not mint:
            why = "no_mint"
        if not why and meta.get("mint") and str(meta["mint"]) != mint:
            why = "mint_pool_mismatch"  # the row's mint is not the pool's base mint
        if not why and self.close_blocked is not None:
            try:
                blocked = self.close_blocked(mint)
            except Exception:  # noqa: BLE001 - deny on error when a prefilter is configured
                blocked = None
            if blocked is not False:
                why = "close_blocked" if blocked else "close_blocked_unknown"
        if not why and mint in self.state.bought:
            why = "already_bought"
        if not why:
            why = self.port.universe_problem(pool, mint)  # type: ignore[arg-type]
        stake = lim["stake_lamports"]
        size_map: dict[str, Any] | None = None
        if self.cfg.profile == c0.PROFILE:
            if not why and tier_prob and self.cfg.mode == LIVE:
                why = tier_prob  # a live-config C0 entry needs the TIER file to read T1
            s_c0, size_map = c0.c0_stake(row, stake)
            if not why and s_c0 is None:
                why = size_map["refused"]
            if s_c0 is not None:
                stake = s_c0
            if not why:  # DEC-032 s3 committed loss: realized loss + every open/pending/in-flight position's worst case + this buy's (same terms as the floor)
                opens = self.open_orders(scope)
                why = c0.total_stop_problem(max(0, -self.state.realized_lamports), [o["stake_lamports"] for o in opens.values()], stake, RENT_RESERVE_LAMPORTS)
        if not why and self.gates is not None:
            why = self.gate_refusal(now)
        if not why:
            why = self.floor_problem(stake, self.port.balance(), scope)
        if why and self.cfg.profile == c0.PROFILE and why in c0.SKIP_UNIVERSE_LABELS:
            # A universe skip is not a cap and not a refusal: it is its own count and log kind, so the canary's narrower band can be told apart from limits.
            self._count("universe_skip")
            self._log("universe_skip", order_id=row["order_id"], why=why, decision_ms=row["ms"], seq=row["seq"], v_range=list(c0.UNIVERSE_V_RANGE))
            return
        if why:
            return self._refuse(row, why, tier=tier, tier_problem=tier_prob)
        # Write-ahead: the order exists on disk BEFORE the first send, so a crash cannot produce a second buy.
        order = {"order_id": oid, "pool": pool, "mint": mint, "status": "buy_sending", "stake_lamports": stake, "paper_stake_sol": row.get("stake_sol"),
                 "decision_ms": int(row["ms"]), "decision_slot": row.get("decision_slot"), "entry_seq": row["seq"], "live": live, "tier": tier,
                 "dry_reason": why_dry, "t_open_ms": None}
        if size_map is not None:
            order["size_map"] = size_map  # logged mapping: card stake / size -> S; the paper twin is priced at the same S
        self.state.orders[oid] = order
        self.state.bought.append(mint)  # type: ignore[arg-type]
        self.state.attempts += 1
        d = self.state.days.setdefault(utc_day(now), {"trades": 0, "realized": 0})
        d["trades" if scope else "dry_trades"] = d.get("trades" if scope else "dry_trades", 0) + 1
        order["last_action_ms"] = now
        self._save()
        late = self._presend_refusal(row)  # end_ms, STOP/HALT and decision age again, after the RPCs above
        if late:
            order["status"] = "refused"
            order["refused"] = late
            self._count("refused_" + late.split(":")[0])
            self._log("entry_refused", order_id=oid, why=late, decision_ms=row["ms"], seq=row["seq"], presend=True)
            self._save()
            return
        order["send_ms"] = self.now_ms()
        ok, label = (self.port if order["live"] else self.dry_port).buy(order)
        order["status"] = "buy_sent" if ok else "refused"
        if not ok:
            order["refused"] = label
            self._count("port_refused_buy")
        self._log("buy_sent" if ok else "buy_port_refused", order_id=oid, mint=mint, stake_lamports=stake, paper_stake_sol=row.get("stake_sol"),
                  decision_ms=order["decision_ms"], send_ms=order["send_ms"], decision_to_send_ms=order["send_ms"] - order["decision_ms"],
                  decision_slot=order["decision_slot"], would_send=not order["live"], dry_reason=why_dry, tier=tier, label=label)
        self._save()

    def _presend_refusal(self, row: Mapping[str, Any]) -> str | None:
        now = self.now_ms()
        halt, stop = self._files()
        if halt:
            return "halt_file"
        if stop:
            return "stop_file"
        why = self.feed_refusal(now) or self.gap_entry_refusal(row)  # the feed can stall, or a gap record arrive, during the RPCs above (hv4_c0 only)
        if why:
            return why
        if now >= self.cfg.end_ms:
            return "end_ms"
        if now - int(row["ms"]) > MAX_DECISION_AGE_MS:
            return "stale_decision"
        return None

    def _refuse(self, row: Mapping[str, Any], why: str, **kw: Any) -> None:
        self._count("refused_" + why.split(":")[0])
        self._log("entry_refused", order_id=row["order_id"], why=why, decision_ms=row["ms"], seq=row["seq"], **kw)

    def handle_exit(self, row: dict[str, Any]) -> None:
        oid = str(row["order_id"])
        o = self.state.orders.get(oid)
        if o is None or o["status"] in ("refused", "closed"):
            self._count("exit_unknown_order")
            return
        frac = row.get("frac", 1.0)
        if not isinstance(frac, (int, float)) or isinstance(frac, bool) or frac < 1.0:
            self._count("exit_partial_refused")  # v1: full exits only
            self._log("exit_refused", order_id=oid, why="partial_exit", frac=frac)
            return
        o["exit_decision_ms"] = o.get("exit_decision_ms") or int(row["ms"])
        o["exit_decision_slot"] = o.get("exit_decision_slot") or row.get("decision_slot")
        self._try_exit(oid, str(row.get("reason") or "book_exit"))

    def _try_exit(self, oid: str, reason: str) -> None:
        o = self.state.orders[oid]
        if o["status"] in ("sell_sending", "sell_sent"):
            self._count("exit_while_selling")  # the earlier sell is in flight: never a second sell
            return
        if o["status"] == "buy_sent":
            o["want_exit"] = reason  # waits for the buy to land (S2: never double-sell, never sell before the buy exists)
            self._log("exit_deferred", order_id=oid, reason=reason)
            self._save()
            return
        if o["status"] != "open":
            return
        if Path(self.cfg.halt_file).exists():
            self._count("exit_held_halt_file")
            return
        now = self.now_ms()
        if now - self._last_sell_try.get(oid, 0) < SELL_RETRY_MS and o.get("sell_tries"):
            return
        self._last_sell_try[oid] = now
        o["status"] = "sell_sending"  # write-ahead
        o["last_action_ms"] = now
        o["sell_reason"] = reason
        o["sell_tries"] = o.get("sell_tries", 0) + 1
        self._save()
        o["sell_send_ms"] = self.now_ms()
        ok, label = (self.port if o["live"] else self.dry_port).sell(o, reason)
        if ok:
            o["status"] = "sell_sent"
        else:
            o["status"] = "open"
            self._count("port_refused_sell")
        dms = o.get("exit_decision_ms")
        self._log("sell_sent" if ok else "sell_port_refused", order_id=oid, mint=o["mint"], reason=reason, decision_ms=dms, send_ms=o["sell_send_ms"],
                  decision_to_send_ms=(o["sell_send_ms"] - dms) if dms else None, decision_slot=o.get("exit_decision_slot"), tries=o["sell_tries"],
                  would_send=not o["live"], label=label)
        self._save()

    # -- events from the port
    def apply_events(self, events: list[dict[str, Any]]) -> None:
        by_mint = {o["mint"]: k for k, o in self.state.orders.items()}
        for ev in events:
            oid = ev.get("order_id") or by_mint.get(ev.get("mint"))
            o = self.state.orders.get(oid) if oid else None
            if o is None:
                continue
            e = ev["event"]
            if e == "buy_landed":
                o["status"] = "open"
                o["t_open_ms"] = self.now_ms()
                o["buy_landed_slot"] = ev.get("landed_slot")
                self._log("buy_landed", order_id=oid, landed_slot=ev.get("landed_slot"), decision_slot=o.get("decision_slot"),
                          decision_to_landed_slots=(ev["landed_slot"] - o["decision_slot"]) if isinstance(ev.get("landed_slot"), int) and isinstance(o.get("decision_slot"), int) else None,
                          dry=bool(ev.get("dry")))
                if o.get("want_exit"):
                    self._try_exit(oid, o["want_exit"])
            elif e == "buy_failed":
                o["status"] = "closed"
                self._book_pnl(o, int(ev.get("pnl_lamports") or 0), "buy_failed")
                self._log("buy_failed", order_id=oid, cost_lamports=-int(ev.get("pnl_lamports") or 0), label=ev.get("label"))
            elif e == "sell_landed":
                o["status"] = "closed"
                self._book_pnl(o, int(ev.get("pnl_lamports") or 0), o.get("sell_reason") or "sell")
                self._log("sell_landed", order_id=oid, pnl_lamports=ev.get("pnl_lamports"), landed_slot=ev.get("landed_slot"), reason=o.get("sell_reason"),
                          exit_decision_slot=o.get("exit_decision_slot"),
                          decision_to_landed_slots=(ev["landed_slot"] - o["exit_decision_slot"]) if isinstance(ev.get("landed_slot"), int) and isinstance(o.get("exit_decision_slot"), int) else None,
                          dry=bool(ev.get("dry")))
            elif e == "sell_failed":
                o["status"] = "open"  # the next exit row or the backstop retries
                self._log("sell_failed", order_id=oid, label=ev.get("label"))
            elif e == "sell_stuck":
                self._latch("stuck_sell", f"{o['mint']}")
        self._save()

    def _book_pnl(self, o: dict[str, Any], pnl: int, why: str) -> None:
        self.state.realized_lamports += pnl
        d = self.state.days.setdefault(utc_day(self.now_ms()), {"trades": 0, "realized": 0})
        d["realized"] += pnl
        limit = self.cfg.per_trade_loss_lamports
        if self.cfg.profile == c0.PROFILE and isinstance(o.get("stake_lamports"), int):
            limit = c0.per_trade_loss_lamports(o["stake_lamports"], limit)  # 30% of THIS order's S, config may only tighten
        if pnl <= -limit:
            self._latch("per_trade_loss_cap", f"{o['order_id']} pnl {pnl}")
        if self.state.realized_lamports <= -self.cfg.total_loss_lamports:
            self._latch("total_loss_cap", f"realized {self.state.realized_lamports}")

    # -- reconciliation of half-finished orders (crash between the write-ahead save and the executor's own state)
    def reconcile(self, only_stale: bool = False) -> None:
        """Compare every in-flight order with the executor's own open + pending positions.
        pending -> keep (status sent); held with nothing in flight -> open; in neither -> a buy is refused (nothing was bought), a sell is closed
        (flagged: P&L unknown, the next live balance read is the truth). Dry orders have no executor: their fills are instantaneous."""
        now = self.now_ms()
        held = self.port.holdings() if self.port.live else None
        changed = False
        for oid, o in self.state.orders.items():
            st = o["status"]
            if st not in ("buy_sending", "buy_sent", "sell_sending", "sell_sent"):
                continue
            if only_stale and now - o.get("last_action_ms", 0) < SELL_RETRY_MS:
                continue
            was = st
            if not o["live"] or held is None:
                if not o["live"]:
                    o["status"] = "open" if st.startswith("buy") else "closed"
                    if o["status"] == "open" and o.get("t_open_ms") is None:
                        o["t_open_ms"] = now
                else:
                    continue
            else:
                where = held.get(o["mint"])
                if where == "pending":
                    o["status"] = "buy_sent" if st.startswith("buy") else "sell_sent"
                    if o["status"] == was:
                        continue
                elif where == "open":
                    o["status"] = "open"
                    if o.get("t_open_ms") is None:
                        o["t_open_ms"] = now
                elif st.startswith("buy"):
                    if st == "buy_sent" and now - o.get("last_action_ms", 0) < SELL_RETRY_MS:
                        continue
                    o["status"] = "refused"
                    o["refused"] = "reconcile_no_position"
                else:
                    o["status"] = "closed"
                    o["closed_unknown_pnl"] = True
                    print(f"arena_live_bridge ALERT reconcile_sell_without_position order={oid}", flush=True)
            o["last_action_ms"] = now
            changed = True
            self._log("reconcile", order_id=oid, was=was, now=o["status"], where=(held or {}).get(o["mint"]) if held is not None else None)
        if changed:
            self._save()

    # -- backstops and warm plan (S2)
    def backstops(self) -> None:
        now = self.now_ms()
        self.reconcile(only_stale=True)  # a *_sending order left by a crash, or a sent one that vanished from the executor, is fixed here
        for oid, o in list(self.state.orders.items()):
            if o["status"] != "open" or o.get("t_open_ms") is None:
                continue
            reason = None
            if now - o["t_open_ms"] > self.cfg.max_hold_ms + BACKSTOP_EXTRA_MS:
                reason = "time_stop_backstop"
            else:
                ret = self.port.position_return(o["mint"])
                if ret is not None and ret <= -self.cfg.sl_backstop:
                    reason = "sl_backstop"
            if reason:
                o.setdefault("exit_decision_ms", now)  # the backstop's own decision instant
                self._try_exit(oid, reason)

    def warm(self) -> None:
        now = self.now_ms()
        if now - self._last_warm < self.cfg.warm_ms:
            return
        self._last_warm = now
        mints = [o["mint"] for o in self.state.orders.values() if o["status"] == "open"]
        if mints:
            self.port.warm(mints)

    # -- loop
    def tick(self) -> int:
        """One pass: port events, rows, backstops, warm plan. Returns rows consumed."""
        self.a3_tick(self.now_ms())
        self.gap_tick(self.now_ms())
        self._poll_ports()
        n = 0
        if not Path(self.cfg.halt_file).exists():
            rows = self.read_new_rows()
            if "chain_break" not in self.state.latches and "sealed_path" not in self.state.latches:
                self._chain_ok_ms = self.now_ms()  # the log was read and verified just now (the parity state's decision_chain stamp), before any entry in it is handled
            if rows and self.cfg.profile == c0.PROFILE:
                self._last_row_ms = self.now_ms()  # a row arrived: the runner wrote just now (the row's own ms is the decision instant, not the arrival)
            for row in rows:
                n += 1
                self.handle_row(row)
            if n or self.state.latches:
                self._save()
            self.backstops()
            self.warm()
            self._poll_ports()
        return n

    def _poll_ports(self) -> None:
        self.apply_events(self.port.poll())
        if self.dry_port is not self.port:
            self.apply_events(self.dry_port.poll())
        if self.gates is not None:
            self.feed_ledger()
            if self._chain_ok_ms is not None:  # never before the first verified read: a stamp-less run would write a STOP file for nothing
                self.gates.maybe_run_parity(self.now_ms())  # off the hot path: every interval and after every landed buy or closed trip
            self._latch_parity_hard()

    def run(self, poll_s: float = 0.05, once: bool = False, sleep: Callable[[float], None] = time.sleep) -> None:
        while True:
            self.tick()
            if once:
                return
            sleep(poll_s)


# --- the live port (wraps the existing probe executor; built only for --live) --------------------------------------------------------


def make_live_port(cfg: Config, rpc: Callable[[str, list], Any], keypair: Any, *, now_ms: Callable[[], int] | None = None) -> OrderPort:
    """LiveExecutorPort over a `probe_live.LiveExecutor` subclass. Imported here so a dry bridge never imports the solana stack or sees a key."""
    from tools import h5_executor as h5
    from tools import probe_executor as pe
    from tools import probe_live as pl
    from tools import pumpswap_tx as tx_mod

    if cfg.profile == c0.PROFILE:
        pin_why = c0.sell_pin_problem()
        if pin_why:
            raise RuntimeError(f"hv4_c0 live port refused: {pin_why}")

    class BridgeExecutor(pl.LiveExecutor):
        """The probe's send path with no signal tail and no probe exit rules: buys come from `buy`, sells from the bridge (exit_reason is set by the
        bridge only), and the open-position poll prices only positions the bridge asked to sell. Everything else (write-ahead, rebroadcast,
        confirm loop, meta, sell + close ATA, stuck handling) is the base class."""

        def __init__(self, rpc_: Callable, pcfg: dict[str, Any], kp: Any, now: Callable[[], int] | None, end_ms: int) -> None:
            with h5._h5_precheck_scope(h5.LIVE):  # the probe's cross-profile flat check on the shared wallet, scoped to this state dir
                super().__init__(rpc_, pcfg, kp, now_ms=now)
            # The profile's own end (dec019: 2026-10-12) is replaced by the bridge's end_ms: the executor's check_buy soft stop (end_instant) then
            # enforces it at send time too, and the Bridge re-checks it right before the send.
            object.__setattr__(self.limits, "end_ms", int(end_ms))
            self.sent_priority: dict[str, int] = {}
            self.events: list[dict[str, Any]] = []
            self.ledger: list[tuple[str, str, dict[str, Any]]] = []
            self.last_skip: str | None = None
            self.warm_snaps: dict[str, tuple[Any, int]] = {}

        def _priority(self, mint: str) -> int:
            """hv4_c0 only: the pinned 55,000 buy / 55,000 first sell / 150,000 escalated sell schedule. Any other profile keeps the base class
            (the configured limit for every send)."""
            if cfg.profile != c0.PROFILE:
                return super()._priority(mint)
            pend, pos = self.state.pending.get(mint), self.state.open.get(mint)
            if pend is not None and pend.get("kind") == "buy":
                return c0.PRIORITY_PIN["buy"]
            if pend is not None or pos is not None:  # a sell in flight, or the sell being built (position open, nothing pending yet)
                src = pos if pos is not None else {}
                if getattr(self, "_c0_building", False):
                    elapsed = self._c0_elapsed  # the SAME elapsed value the min_out rung used (one clock read per build)
                else:
                    elapsed = self.now_ms() - src["c0_first_sell_ms"] if "c0_first_sell_ms" in src else None
                return c0.sell_priority(src.get("sell_attempts", 0), bool(src.get("stuck")), elapsed)
            return c0.PRIORITY_PIN["buy"]

        @property
        def slip_bps(self) -> int:  # type: ignore[override]
            """hv4_c0: while a SELL is being built the slippage is the compiled ladder's (c0.sell_slip_bps: min_out 0.85 of the quote, 0.65 at the escalation
            point); at every other time (the buy) it is the base value, untouched."""
            sell = getattr(self, "_c0_sell_slip", None)
            return sell if sell is not None else self._slip_base

        @slip_bps.setter
        def slip_bps(self, v: int) -> None:
            self._slip_base = v

        def _start_sell(self, mint: str, pos: dict[str, Any], snap: Any, reason: str, now: int, exit_snapshot: str = "full") -> None:
            """hv4_c0: set the sell's min_out rung from the SAME escalation predicate as the priority, then run the pinned base builder unchanged."""
            if cfg.profile != c0.PROFILE:
                return super()._start_sell(mint, pos, snap, reason, now, exit_snapshot)
            # One clock value (`now`) feeds both the rung and the priority at the 345 s boundary. The 345 s clock starts only when a sell is actually
            # built (a pending sell exists after the call), not on a call that returns early (balance error, zero quote, build error).
            first = pos.get("c0_first_sell_ms")
            self._c0_elapsed = None if first is None else now - first
            self._c0_building = True
            self._c0_sell_slip = c0.sell_slip_bps(pos.get("sell_attempts", 0), bool(pos.get("stuck")), self._c0_elapsed)
            try:
                return super()._start_sell(mint, pos, snap, reason, now, exit_snapshot)
            finally:
                self._c0_sell_slip = None
                self._c0_building = False
                if (self.state.pending.get(mint) or {}).get("kind") == "sell":
                    pos.setdefault("c0_first_sell_ms", now)

        def _sign(self, msg: Any, ps: Any, mint: str, priority: int | None = None) -> Any:
            """hv4_c0: a SELL whose decoded min_out is 0 (a 1-lamport quote rounds 0.85 x quote down to 0) is never signed: the raise is caught by the base builder's
            own build-error path (alert sell_build_error, nothing pending, nothing sent). The pinned base `_sign` runs unchanged after the check."""
            if cfg.profile == c0.PROFILE and getattr(self, "_c0_building", False):
                keys = list(msg.account_keys)
                for ix in msg.instructions:
                    d = bytes(ix.data)
                    if str(keys[ix.program_id_index]) == str(tx_mod.PUMPSWAP_PROGRAM) and d[:8] == bytes(tx_mod.DISC_SELL) and int.from_bytes(d[16:24], "little") <= 0:
                        raise RuntimeError("c0_min_out_zero")
            if cfg.profile == c0.PROFILE:  # N7: remember the priority of the tx actually signed, so the fill row logs what was SENT (the base class recomputes
                # `_priority` at landing, when the pending entry is gone); the pinned base `_sign` runs unchanged with that same value
                priority = self._priority(mint) if priority is None else priority
                self.sent_priority[mint] = priority
            return super()._sign(msg, ps, mint, priority)

        def _log(self, kind: str, mint: str, **kw: Any) -> None:
            if cfg.profile == c0.PROFILE and kind in ("buy", "sell") and "priority_lamports" in kw and mint in getattr(self, "sent_priority", {}):
                kw["priority_lamports"] = self.sent_priority[mint]  # the priority of the last tx signed for this mint, not a recomputation
            super()._log(kind, mint, **kw)
            if cfg.profile == c0.PROFILE and kind in ("buy", "sell"):
                self.ledger.append((kind, mint, dict(kw)))  # the parity recorder reads the executor's own fill rows (drained by the port)

        def _skip(self, sig: dict[str, Any], reason: str, **kw: Any) -> None:
            self.last_skip = reason
            super()._skip(sig, reason, **kw)

        def _finish_buy(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
            r0 = self.state.realized_lamports
            super()._finish_buy(mint, p, m)
            if mint in self.state.open:
                self.events.append({"mint": mint, "event": "buy_landed", "landed_slot": m.get("slot")})
            else:
                self.events.append({"mint": mint, "event": "buy_failed", "pnl_lamports": self.state.realized_lamports - r0, "label": "landed_failed"})

        def _resolve_expired(self, mint: str, p: dict[str, Any]) -> None:
            super()._resolve_expired(mint, p)
            if p["kind"] == "buy":
                self.events.append({"mint": mint, "event": "buy_failed", "pnl_lamports": 0, "label": "expired"})
            else:
                self.events.append({"mint": mint, "event": "sell_failed", "label": "expired"})

        def _finish_sell(self, mint: str, p: dict[str, Any], m: dict[str, Any]) -> None:
            r0 = self.state.realized_lamports
            was_stuck = bool(self.state.open.get(mint, {}).get("stuck"))
            super()._finish_sell(mint, p, m)
            if mint not in self.state.open:
                self.events.append({"mint": mint, "event": "sell_landed", "landed_slot": m.get("slot"), "pnl_lamports": self.state.realized_lamports - r0})
            else:
                self.events.append({"mint": mint, "event": "sell_failed", "label": "landed_failed"})
                if self.state.open[mint].get("stuck") and not was_stuck:
                    self.events.append({"mint": mint, "event": "sell_stuck"})

        def poll_positions(self) -> None:
            """Sell only positions with a bridge-set exit_reason. Uses the pre-warmed snapshot when it is young; else one batched read."""
            cands = [m for m in self._exit_candidates() if self.state.open[m].get("exit_reason")]
            if not cands:
                return
            batched = self._batched_exit_snapshots(cands)
            for mint in cands:
                if pe.check_sell(pe.check_halt_file(self.limits)):
                    return
                pos = self.state.open[mint]
                now = self.now_ms()
                warm = self.warm_snaps.get(mint)
                if warm and 0 <= now - warm[1] <= pe.BATCH_MAX_AGE_MS and mint not in batched:
                    snap, kind = warm[0], "warm"
                else:
                    if self._batch_stale(mint, batched):
                        continue
                    snap, _pool, _err, kind = self._exit_snapshot(mint, batched)
                if snap is None or snap.quote_priced is None:
                    continue  # never sell blind
                with self._exit_prio():
                    self._start_sell(mint, pos, snap, pos["exit_reason"], now, kind)

        def refresh_warm(self, mints: list[str]) -> None:
            live = [m for m in mints if m in self.state.open and m not in self.state.pending]
            batched = self._batched_exit_snapshots(live)
            now = self.now_ms()
            for m, s in batched.items():
                self.warm_snaps[m] = (s, now)
            try:
                self.bh.get()
            except (Exception, SystemExit):
                pass

    class LiveExecutorPort(OrderPort):
        name = "live"
        live = True

        def __init__(self) -> None:
            pcfg = {
                "signals_dir": str(Path(cfg.state_dir) / "sig"), "state_dir": str(Path(cfg.state_dir) / "probe"), "fill_log": str(Path(cfg.state_dir) / "probe" / "fills.jsonl"),
                "stop_file": cfg.stop_file, "halt_file": cfg.halt_file, "mode": "live", "book": f"arena:{cfg.bot_id}", "poll_s": 5.0,
                "size_lamports": cfg.stake_lamports, "priority_lamports": c0.PRIORITY_PIN["buy"] if cfg.profile == c0.PROFILE else 500_000, "max_open": cfg.max_open, "max_attempts": 60,
                "loss_cap_lamports": cfg.total_loss_lamports, "max_signal_age_s": MAX_DECISION_AGE_MS / 1000.0, "exit_poll_ms": cfg.warm_ms,
                "min_balance_buffer_lamports": BUFFER_LAMPORTS,
            }
            Path(pcfg["signals_dir"]).mkdir(parents=True, exist_ok=True)
            self.ex = BridgeExecutor(rpc, pcfg, keypair, now_ms, cfg.end_ms)
            self.now = self.ex.now_ms

        def mint_for_pool(self, pool: str, meta: Mapping[str, Any]) -> str | None:
            """Always the pool account's own base mint; a mint in the row's meta is only compared with it by the Bridge."""
            try:  # the pool account's base mint (one read; the executor's canonical-pool check re-verifies it before any buy)
                from tools import pumpswap_simulate as sim_
                from tools import pumpswap_tx as tx_

                res = self.ex.rpc("getAccountInfo", [pool, {"encoding": "base64", "commitment": self.ex.commitment}])
                info = res.get("value")
                if not info or info.get("owner") != str(tx_.PUMPSWAP_PROGRAM):
                    return None
                return str(tx_.parse_pool_account(sim_._b64(info))["base_mint"])
            except (Exception, SystemExit):
                return None

        def universe_problem(self, pool: str, mint: str) -> str | None:
            snap, _pool, err = self.ex._snapshot(mint)
            if snap is None:
                return f"pool_unreadable:{err}"
            if cfg.profile == c0.PROFILE:
                return c0.universe_problem(snap.ps.quote_mint, snap.v)
            if snap.ps.quote_mint != tx_mod.WSOL_MINT:
                return "quote_not_wsol"
            if snap.v is None or not UNIVERSE_V_RANGE[0] <= snap.v <= UNIVERSE_V_RANGE[1]:
                return "universe_v_out_of_range"
            return None

        def balance(self) -> int | None:
            return self.ex._balance()

        def buy(self, order: Mapping[str, Any]) -> tuple[bool, str | None]:
            self.ex.last_skip = None
            if cfg.profile == c0.PROFILE:  # the live port sends the ORDER's S (twin stake = S), never cfg.stake_lamports; larger than the table is refused
                s = order.get("stake_lamports")
                if isinstance(s, bool) or not isinstance(s, int) or not 0 < s <= min(cfg.stake_lamports, c0.C0_LIMITS["stake_lamports"]):
                    return False, "stake_not_s"
                object.__setattr__(self.ex.limits, "size_lamports", s)
            sig = {"mint": order["mint"], "decision_t_ms": order["decision_ms"], "score": None, "seen_ms": self.now()}
            with self.ex._prio():
                self.ex.handle_signal(sig)
            if order["mint"] in self.ex.state.pending or order["mint"] in self.ex.state.open:
                return True, None
            return False, self.ex.last_skip or "not_sent"

        def sell(self, order: Mapping[str, Any], reason: str) -> tuple[bool, str | None]:
            pos = self.ex.state.open.get(order["mint"])
            if pos is None:
                return False, "no_open_position"
            pos["exit_reason"] = reason
            self.ex.poll_positions()
            return (order["mint"] in self.ex.state.pending), None

        def holdings(self) -> dict[str, str]:
            out = {m: "open" for m in self.ex.state.open}
            out.update({m: "pending" for m in self.ex.state.pending})
            return out

        def warm(self, mints: list[str]) -> None:
            self.ex.refresh_warm(mints)

        def position_return(self, mint: str) -> float | None:
            w, pos = self.ex.warm_snaps.get(mint), self.ex.state.open.get(mint)
            if not w or not pos or w[0].quote_priced is None or self.ex.now_ms() - w[1] > 5_000:
                return None
            return pe.exit_check(pos, w[0], self.ex.now_ms(), own_trade_in_state=True)["ret"]

        def drain_ledger(self) -> list[tuple[str, str, dict[str, Any]]]:
            out, self.ex.ledger = self.ex.ledger, []
            return out

        def poll(self) -> list[dict[str, Any]]:
            self.ex.advance_pending()
            self.ex._clock_note(self.ex.now_ms())
            self.ex.poll_positions()
            out, self.ex.events = self.ex.events, []
            return out

    return LiveExecutorPort()


# --- CLI --------------------------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Arena decision log -> orders. DRY unless config mode is live AND --live is given.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--live", action="store_true", help="with config mode 'live': send real transactions")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    raw = json.loads(Path(args.config).read_text())
    want_live = raw.get("mode") == LIVE and args.live
    if raw.get("mode") == LIVE and not args.live:
        print("arena_live_bridge: config says live but --live is missing: running DRY", flush=True)
    if args.live and raw.get("mode") != LIVE:
        print("arena_live_bridge: --live given but config mode is not live: running DRY", flush=True)
    raw = {**raw, "mode": LIVE if want_live else DRY}
    try:
        cfg = Config.from_dict(raw)
    except ValueError as exc:
        print(f"arena_live_bridge ALERT startup_refused {exc}", flush=True)
        return 2
    if cfg.profile == c0.PROFILE:
        # PR4 gates, before anything else is touched (the order below is the order of the labels a start prints).
        if want_live:
            why = c0p.paths_refusal(raw)  # a live start needs all three parity paths, absolute
            if not why and not cfg.a3_record_file:
                why = "a3_record_file_missing"
            if not why:
                why = c0.owner_line_problem(c0.OWNER_DEC_PATH, cfg.bot_id, None, root_ok_problem)
        else:
            why = None
        st0 = BridgeState.load(Path(cfg.state_dir) / f"bridge-state-{cfg.mode}.json", cfg.mode)
        why = why or c0.read_record_problem(cfg.reads_dir, cfg.bot_id, st0.read_pin, (not want_live) and c0.synthetic_root(cfg.root))[0]
        if not why:
            first, why = c0.window_start_ms(cfg.bot_id, int(time.time() * 1000), create=False)
            why = why or c0.end_cap_problem(cfg.end_ms, first if first is not None else int(time.time() * 1000))
        if why:
            print(f"arena_live_bridge ALERT startup_refused {c0.refusal_text(why)}", flush=True)
            return 2
        # Seal: R2.1 of QP-RULING-1025-hv4-read-rule. No hv4_c0 start, dry or live, until a read-record gate exists, except on a synthetic fixture root.
        why = c0.start_refusal(cfg.root, want_live, cfg.bot_id)
        if why:
            print(f"arena_live_bridge ALERT startup_refused {why}", flush=True)
            return 2
        why = c0.guard_problem()
        if why:
            print(f"arena_live_bridge ALERT startup_refused {why}", flush=True)
            return 2
    if not want_live:
        port: OrderPort = DryPort(cfg.dry_balance_lamports)
    else:
        from tools import probe_executor as pe
        from tools import probe_live as pl
        from tools import pumpswap_simulate as sim

        if not (os.environ.get("HELIUS_API_KEY") or "").strip():
            print("arena_live_bridge ALERT startup_refused rpc_key_missing", flush=True)
            return 2
        if cfg.profile == c0.PROFILE:
            why = c0.sell_pin_problem()
            if why:
                print(f"arena_live_bridge ALERT startup_refused {why}", flush=True)
                return 2
        pl.harden_process()
        kp = pl.load_probe_key()
        rpc = pe.LimitedRpc(pe.ProbeRpc(sim.load_rpc_url(None, None, use_env_file=False)), rps=float(raw.get("rps", pe.MAX_RPS)), max_rps=pl.LIVE_MAX_RPS)
        port = make_live_port(cfg, rpc, kp)
    br = Bridge(cfg, port)
    print(f"arena_live_bridge mode={cfg.mode} bot={cfg.bot_id} stake={cfg.stake_lamports} floor={cfg.wallet_floor_lamports}", flush=True)
    br.run(once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
