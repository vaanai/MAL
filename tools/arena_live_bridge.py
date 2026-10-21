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
per_trade_loss_cap, stuck_sell. A latch refuses new buys only; exits of positions already open (book exit rows, and the time-stop and stop-loss
backstops) still run, so nothing is stranded. The HALT file freezes everything. The STOP file stops new buys only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from tools.arena_declog import GENESIS, DecisionLog, LogChainError, sha256_hex, utc_day
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
BUFFER_LAMPORTS = 20_000_000
MAX_DECISION_AGE_MS = 5_000
BACKSTOP_EXTRA_MS = 60_000  # time stop = the book's max hold + this
DEFAULT_SL_BACKSTOP = 0.50  # return on the position at which the backstop sells (config may tighten, never loosen past 0.60)
MAX_SL_BACKSTOP = 0.60
WARM_MS_MIN, WARM_MS_MAX, WARM_MS_DEFAULT = 500, 2_000, 1_000
SELL_RETRY_MS = 5_000
UNIVERSE_V_RANGE = (17_500_000_000, 17_700_000_000)  # Am.2 item 6: V in [17.5e9, 17.7e9], WSOL quote

LATCHES = ("chain_break", "frozen_sha_mismatch", "total_loss_cap", "per_trade_loss_cap", "stuck_sell")


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

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], tier: str = "T0") -> "Config":
        for k in ("bot_id", "frozen_sha", "root", "state_dir", "end_ms", "max_hold_ms"):
            if d.get(k) in (None, ""):
                raise ValueError(f"config {k} is required")
        t = TIERS[tier]
        kw: dict[str, Any] = {k: d[k] for k in ("bot_id", "frozen_sha", "root", "state_dir")}
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

    def warm(self, mints: list[str]) -> None:
        """S2: refresh the sell plan of every open position (batched vault read, blockhash). Never sends."""

    def position_return(self, mint: str) -> float | None:
        """Mark-to-quote return on an open position from the warm snapshot, or None when unpriced."""
        return None

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

    return h5.root_file_problem(path)


def read_tier(path: Path, root_checks: bool) -> tuple[str, str | None]:
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
    return (text, None) if text in TIERS else ("T0", "tier_file_invalid")


# --- the bridge -------------------------------------------------------------------------------------------------------------------


class Bridge:
    def __init__(self, cfg: Config, port: OrderPort, *, now_ms: Callable[[], int] | None = None,
                 live_ok_path: Path = LIVE_OK_PATH, tier_path: Path = TIER_PATH, root_checks: bool = True) -> None:
        self.cfg = cfg
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
        self._last_warm = 0
        self._last_sell_try: dict[str, int] = {}
        self._verify_at_start()

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

    def _verify_at_start(self) -> None:
        """Full chain check of what exists now. A break latches chain_break (the bridge then never buys). The cursor is set only on a clean pass; a
        restart resumes from the saved cursor, so rows already handled are not replayed (order_id idempotency covers the crash window)."""
        try:
            self.dlog.verify(self.cfg.bot_id)
        except LogChainError as exc:
            self._latch("chain_break", str(exc)[:160])

    # -- tailing
    def read_new_rows(self) -> list[dict[str, Any]]:
        """New complete rows after the cursor, each checked against the chain (prev, seq+1, sha). A break latches and stops the read."""
        out: list[dict[str, Any]] = []
        if "chain_break" in self.state.latches:
            return out
        st = self.state
        for f in self.dlog.files(self.cfg.bot_id):
            off = st.cursor.get(f.name, 0)
            data = f.read_bytes()
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

    def effective_live(self) -> tuple[bool, str | None]:
        """(live, why_dry). A live-configured bridge sends only while LIVE_OK passes the root check; otherwise this entry is DRY (logged, not sent)."""
        if self.cfg.mode != LIVE:
            return False, "mode_dry"
        why = root_ok_problem(self.live_ok_path) if self.root_checks else (None if self.live_ok_path.exists() else "missing")
        return (False, f"live_ok_{why}") if why else (True, None)

    def tier_limits(self) -> tuple[str, dict[str, int], str | None]:
        tier, prob = read_tier(self.tier_path, self.root_checks and self.cfg.mode == LIVE)
        t = TIERS[tier]
        return tier, {k: min(getattr(self.cfg, k), v) for k, v in t.items()}, prob

    def open_orders(self) -> dict[str, dict[str, Any]]:
        return {k: o for k, o in self.state.orders.items() if o["status"] in ("buy_sending", "buy_sent", "open", "sell_sending", "sell_sent")}

    def floor_problem(self, stake: int, bal: int | None) -> str | None:
        if bal is None:
            return "balance_unreadable"
        open_stakes = sum(o["stake_lamports"] for o in self.open_orders().values())
        projected = bal - open_stakes - stake - FEE_RESERVE_LAMPORTS - BUFFER_LAMPORTS
        return "wallet_floor" if projected < self.cfg.wallet_floor_lamports else None

    def entry_refusal(self, row: Mapping[str, Any], now: int, lim: dict[str, int]) -> str | None:
        if self.state.latches:
            return "latched:" + sorted(self.state.latches)[0]
        halt, stop = self._files()
        if halt:
            return "halt_file"
        if stop:
            return "stop_file"
        if now >= self.cfg.end_ms:
            return "end_ms"
        if now - int(row["ms"]) > MAX_DECISION_AGE_MS:
            return "stale_decision"
        if len(self.open_orders()) >= lim["max_open"]:
            return "max_open"
        day = self.state.days.get(utc_day(now), {})
        if day.get("trades", 0) >= lim["max_trades_per_day"]:
            return "max_trades_per_day"
        if day.get("realized", 0) <= -lim["daily_loss_lamports"]:
            return "daily_loss_cap"
        if self.state.realized_lamports <= -lim["total_loss_lamports"]:
            return "total_loss_cap"
        return None

    # -- decisions
    def handle_row(self, row: dict[str, Any]) -> None:
        k = row.get("kind")
        if k == "start":
            self.state.start_seen = True
            if row.get("frozen_sha") != self.cfg.frozen_sha:
                self._latch("frozen_sha_mismatch", f"book {str(row.get('frozen_sha'))[:12]} pinned {self.cfg.frozen_sha[:12]}")
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
        why = self.entry_refusal(row, now, lim)
        pool = str(row.get("pool"))
        meta = row.get("meta") or {}
        mint = self.port.mint_for_pool(pool, meta) if not why else None
        if not why and not mint:
            why = "no_mint"
        if not why and mint in self.state.bought:
            why = "already_bought"
        if not why:
            why = self.port.universe_problem(pool, mint)  # type: ignore[arg-type]
        stake = lim["stake_lamports"]
        if not why:
            why = self.floor_problem(stake, self.port.balance())
        if why:
            return self._refuse(row, why, tier=tier, tier_problem=tier_prob)
        # Write-ahead: the order exists on disk BEFORE the first send, so a crash cannot produce a second buy.
        order = {"order_id": oid, "pool": pool, "mint": mint, "status": "buy_sending", "stake_lamports": stake, "paper_stake_sol": row.get("stake_sol"),
                 "decision_ms": int(row["ms"]), "decision_slot": row.get("decision_slot"), "entry_seq": row["seq"], "live": live, "tier": tier,
                 "dry_reason": why_dry, "t_open_ms": None}
        self.state.orders[oid] = order
        self.state.bought.append(mint)  # type: ignore[arg-type]
        self.state.attempts += 1
        d = self.state.days.setdefault(utc_day(now), {"trades": 0, "realized": 0})
        d["trades"] += 1
        self._save()
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

    def _refuse(self, row: Mapping[str, Any], why: str, **kw: Any) -> None:
        self._count("refused_" + why.split(":")[0])
        self._log("entry_refused", order_id=row["order_id"], why=why, decision_ms=row["ms"], seq=row["seq"], **kw)

    def handle_exit(self, row: dict[str, Any]) -> None:
        oid = str(row["order_id"])
        o = self.state.orders.get(oid)
        if o is None or o["status"] in ("refused", "closed"):
            self._count("exit_unknown_order")
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
        if pnl <= -self.cfg.per_trade_loss_lamports:
            self._latch("per_trade_loss_cap", f"{o['order_id']} pnl {pnl}")
        if self.state.realized_lamports <= -self.cfg.total_loss_lamports:
            self._latch("total_loss_cap", f"realized {self.state.realized_lamports}")

    # -- backstops and warm plan (S2)
    def backstops(self) -> None:
        now = self.now_ms()
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
        self._poll_ports()
        n = 0
        if not Path(self.cfg.halt_file).exists():
            for row in self.read_new_rows():
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

    class BridgeExecutor(pl.LiveExecutor):
        """The probe's send path with no signal tail and no probe exit rules: buys come from `buy`, sells from the bridge (exit_reason is set by the
        bridge only), and the open-position poll prices only positions the bridge asked to sell. Everything else (write-ahead, rebroadcast,
        confirm loop, meta, sell + close ATA, stuck handling) is the base class."""

        def __init__(self, rpc_: Callable, pcfg: dict[str, Any], kp: Any, now: Callable[[], int] | None) -> None:
            with h5._h5_precheck_scope(h5.LIVE):  # the probe's cross-profile flat check on the shared wallet, scoped to this state dir
                super().__init__(rpc_, pcfg, kp, now_ms=now)
            # The profile's own end (dec019: 2026-10-12) is replaced by the bridge's end_ms, which the Bridge enforces before every buy.
            object.__setattr__(self.limits, "end_ms", 2**62)
            self.events: list[dict[str, Any]] = []
            self.last_skip: str | None = None
            self.warm_snaps: dict[str, tuple[Any, int]] = {}

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
                "size_lamports": cfg.stake_lamports, "priority_lamports": 500_000, "max_open": cfg.max_open, "max_attempts": 60,
                "loss_cap_lamports": cfg.total_loss_lamports, "max_signal_age_s": MAX_DECISION_AGE_MS / 1000.0, "exit_poll_ms": cfg.warm_ms,
                "min_balance_buffer_lamports": BUFFER_LAMPORTS,
            }
            Path(pcfg["signals_dir"]).mkdir(parents=True, exist_ok=True)
            self.ex = BridgeExecutor(rpc, pcfg, keypair, now_ms)
            self.now = self.ex.now_ms

        def mint_for_pool(self, pool: str, meta: Mapping[str, Any]) -> str | None:
            m = meta.get("mint") or meta.get("base_mint")
            if m:
                return str(m)
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
            if snap.v is None or not UNIVERSE_V_RANGE[0] <= snap.v <= UNIVERSE_V_RANGE[1]:
                return "universe_v_out_of_range"
            return None

        def balance(self) -> int | None:
            return self.ex._balance()

        def buy(self, order: Mapping[str, Any]) -> tuple[bool, str | None]:
            self.ex.last_skip = None
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

        def warm(self, mints: list[str]) -> None:
            self.ex.refresh_warm(mints)

        def position_return(self, mint: str) -> float | None:
            w, pos = self.ex.warm_snaps.get(mint), self.ex.state.open.get(mint)
            if not w or not pos or w[0].quote_priced is None or self.ex.now_ms() - w[1] > 5_000:
                return None
            return pe.exit_check(pos, w[0], self.ex.now_ms(), own_trade_in_state=True)["ret"]

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
    cfg = Config.from_dict(raw)
    if not want_live:
        port: OrderPort = DryPort(cfg.dry_balance_lamports)
    else:
        from tools import probe_executor as pe
        from tools import probe_live as pl
        from tools import pumpswap_simulate as sim

        if not (os.environ.get("HELIUS_API_KEY") or "").strip():
            print("arena_live_bridge ALERT startup_refused rpc_key_missing", flush=True)
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
