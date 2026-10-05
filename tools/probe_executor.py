"""DEC-019 execution probe: KEYLESS DRY-RUN executor (live mode is tools/probe_live.py).

Follows the fast-0 paper runner's EXP-012 `enter` decisions, builds the exact PumpSwap buy the
probe would send (0.05 SOL, 500k lamports priority, DEFAULT_SLIPPAGE_CAP), runs
simulateTransaction (sigVerify off) against the live chain, tracks a virtual position with the
paper exit rule (tp50/sl30/30-minute cap), and logs every attempt to an append-only JSONL.

There is no signing, no sending and no key loading in this module (live mode is tools/probe_live.py). The fee
payer in simulation is a public funded address (pumpswap_simulate.DEFAULT_USER), never a key we hold.

FORWARD-READ SEAL (DEC-016 Am.2/Am.3): the only runner file read is ONE of the decisions log or the
intents log (config signals_file), and only rows whose action is `enter` (or intents rows) on the ceiling ledger of the configured book are kept, reduced to
mint, decision_t_ms, score, trigger and book. No exit or P&L field is ever stored here, and no
other runner file (positions, status, latency) is opened.

Run (dry-run, on mal-fast-0, as the probe user):

    python -m tools.probe_executor --config scripts/mal-fast/probe-executor.json [--once]

The RPC URL comes from HELIUS_API_KEY in the environment (systemd EnvironmentFile), parsed by
pumpswap_simulate.load_rpc_url. It is never printed or logged.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import functools
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from solders.hash import Hash
from solders.message import Message
from solders.pubkey import Pubkey

from tools import paper_curve_math as pcm
from tools import pumpswap_simulate as sim
from tools import pumpswap_tx as tx
from tools.paper_curve_math import DEFAULT_SLIPPAGE_CAP
from tools.paper_tape_scoreboard import EXIT_RULES, MAX_HOLD_MS

MODE = "dryrun"
SCHEMA_FILL = "probe_fill_v1"
DEFAULT_BOOK = "exp012_migrate_tp50_sl30"
DEFAULT_LEDGER = "ceiling"
LAMPORTS = 1_000_000_000
MAX_RPS = 2.0  # sustained, shared across every call (Helius plan is shared with the listener)
MAX_READ_BYTES = 1 << 20  # per tail pass
UNPRICED_GRACE_MS = 60_000  # retry window past the 30 min deadline before a forced close
EXIT_RULE = next(r for r in EXIT_RULES if r.rule_id == "tp50_sl30")  # tp 0.50 / sl 0.30, same object the scorer uses
DECISIONS_FILE = "decisions.jsonl"  # legacy signal source (after the simulated latency)
INTENTS_FILE = "intents.jsonl"  # the decision-time intent file (forward_paper_intent_v1); no P&L field exists in it
SIGNAL_FILES = (DECISIONS_FILE, INTENTS_FILE)  # the ONLY runner files this module may open, and only ONE of them is configured
KEEP_FIELDS = ("mint", "decision_t_ms", "score", "trigger", "book")
MAX_LATENCY_JSON = 2048  # the runner row's own `latency` object (decision-time hop timings) is copied only when small
SIGNAL_POLL_MS_DEFAULT, SIGNAL_POLL_MS_MIN, SIGNAL_POLL_MS_MAX = 50, 20, 500
STATIC_TTL_MS = 300_000  # cached global config is never used past this age
STATIC_REFRESH_MS = 60_000  # the slow loop refreshes it this often
PRIORITY_BURST = 4  # buy-path calls may run up to this many rate-limit intervals ahead; sustained rate is unchanged
IDLE_SLICE_S = 0.02
BATCH_MAX_AGE_MS = 1_000  # a batched exit snapshot is not used to price a position once it is older than this
BATCH_ERR_LOG_EVERY_MS = 60_000
EXIT_POLL_MS_MIN = 200  # floor for the open-position poll (`exit_poll_ms`)


def clamp_signal_poll_ms(value: Any) -> int:
    """Signal tail period: default 50 ms, clamped to [20, 500]. Anything unparseable falls back to the default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return SIGNAL_POLL_MS_DEFAULT
    return int(min(SIGNAL_POLL_MS_MAX, max(SIGNAL_POLL_MS_MIN, value)))


def clamp_exit_poll_ms(value: Any, poll_ms: int) -> int:
    """Open-position poll period: default (absent or unparseable) = `poll_ms`; otherwise clamped to [200, poll_ms]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return poll_ms
    return int(min(poll_ms, max(EXIT_POLL_MS_MIN, value)))


# --- limits (pure; the live mode must import and use this unchanged) -------------------------

# DEC-019 section 3 maxima. Config may lower these; it can never raise them.
DEC019_MAX = {
    "max_attempts": 30,
    "max_open": 3,
    "loss_cap_lamports": 250_000_000,
    "max_days": 4,
    "size_lamports": 50_000_000,
    "priority_lamports": 500_000,
}


@dataclass(frozen=True)
class Limits:
    max_attempts: int = DEC019_MAX["max_attempts"]
    max_open: int = DEC019_MAX["max_open"]
    loss_cap_lamports: int = DEC019_MAX["loss_cap_lamports"]
    max_days: float = DEC019_MAX["max_days"]
    size_lamports: int = DEC019_MAX["size_lamports"]
    priority_lamports: int = DEC019_MAX["priority_lamports"]
    stop_file: str = "/var/lib/mal-live/STOP"  # no new buys; exits and in-flight sells continue
    halt_file: str = "/var/lib/mal-live/HALT"  # freezes everything: no buys, no sells, no rebroadcasts

    def __post_init__(self) -> None:
        """Clamp to the DEC-019 maxima on EVERY construction path, and reject NaN/inf/non-positive."""
        for key, cap in DEC019_MAX.items():
            val = getattr(self, key)
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or val <= 0:
                raise ValueError(f"limit {key} must be a finite positive number")
            object.__setattr__(self, key, min(val if key == "max_days" else int(val), cap))

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "Limits":
        kw: dict[str, Any] = {k: cfg[k] for k in DEC019_MAX if cfg.get(k) is not None}
        if cfg.get("stop_file"):
            kw["stop_file"] = str(cfg["stop_file"])
        if cfg.get("halt_file"):
            kw["halt_file"] = str(cfg["halt_file"])
        return cls(**kw)


@dataclass
class State:
    """Persisted counters. A restart reloads these, so attempts and loss cannot be reset."""

    attempts: int = 0
    first_attempt_ms: int | None = None
    realized_lamports: int = 0  # sum of closed round-trip P&L; negative is loss
    open: dict[str, dict[str, Any]] = field(default_factory=dict)
    offset: int = 0
    inode: int | None = None
    started: bool = False  # offset initialised (first run starts at end of file)
    mode: str = MODE
    would_halt: dict[str, int] = field(default_factory=dict)  # dry run: budget stops that live would have hit
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)  # live: in-flight signed txs by mint (buy or sell)
    bought: list[str] = field(default_factory=list)  # live: every mint ever attempted; never re-bought (<= 30 entries)
    max_seen_ms: int = 0  # live: highest clock reading seen; a clock stepping back fails closed

    def save(self, path: Path) -> None:
        """Atomic and durable (fsync file, rename, fsync dir). Called write-ahead of any attempt."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w") as fh:
            fh.write(json.dumps(self.__dict__, separators=(",", ":")))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @classmethod
    def load(cls, path: Path, mode: str = MODE) -> "State":
        if not path.exists():
            return cls(mode=mode)
        raw = json.loads(path.read_text())
        st = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        if st.mode != mode:
            raise SystemExit(f"state file mode {st.mode!r} does not match run mode {mode!r}")
        return st


def state_path_for(state_dir: str | Path, mode: str) -> Path:
    """One state file per mode: a dry run can never consume the live budget."""
    return Path(state_dir) / f"state-{mode}.json"


def guard_live_state(mode: str, state_path: Path, fill_log: Path) -> None:
    """Live mode refuses to start when its state file is gone but the fill log shows live rows:
    deleting the state file must not reset the limits. (Reconstruct by hand, or halt.)"""
    if mode != "live" or state_path.exists() or not fill_log.exists():
        return
    with fill_log.open("rb") as fh:
        for raw in fh:
            if b'"mode":"live"' in raw:
                raise SystemExit("live state file missing but the fill log has live rows: refusing to start")


# --- rpc: rate limit, backoff, labelled errors (never carries a URL or message) ---------------


class RpcError(Exception):
    def __init__(self, label: str):
        super().__init__(label)
        self.label = label


def error_label(exc: BaseException) -> str:
    if isinstance(exc, RpcError):
        return exc.label
    text = str(exc)
    if "429" in text:
        return "rate_limited"
    if "timed out" in text.lower() or isinstance(exc, TimeoutError):
        return "timeout"
    if re.search(r"\b5\d\d\b", text):
        return "server_error"
    return f"rpc_error:{type(exc).__name__}"


class ProbeRpc:
    """HTTP JSON-RPC with only a status-class label on failure. The URL never leaves this object."""

    def __init__(self, url: str, timeout: float = 15.0):
        self._url, self._timeout = url, timeout

    def __call__(self, method: str, params: list) -> dict:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self._url, body, {"Content-Type": "application/json"})
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=self._timeout))
        except urllib.error.HTTPError as exc:
            raise RpcError("rate_limited" if exc.code == 429 else "server_error" if exc.code >= 500 else f"http_{exc.code}") from None
        except (TimeoutError, OSError) as exc:
            raise RpcError("timeout" if "timed out" in str(exc).lower() or isinstance(exc, TimeoutError) else "network") from None
        except ValueError:
            raise RpcError("bad_json") from None
        if "error" in resp:
            code = resp["error"].get("code") if isinstance(resp["error"], dict) else None
            raise RpcError(f"rpc_error_{code}")
        return resp["result"]


class LimitedRpc:
    """Shared token bucket across ALL calls (default 2 rps sustained) plus exponential backoff
    on rate_limited / server_error / timeout. Raises RpcError with the last label."""

    RETRY = {"rate_limited", "server_error", "timeout"}

    def __init__(self, rpc: Callable[[str, list], dict], rps: float = MAX_RPS, retries: int = 3,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 max_rps: float = MAX_RPS, burst: int = PRIORITY_BURST):
        if not (math.isfinite(rps) and rps > 0):
            raise ValueError("rps must be finite and positive")
        self.rpc, self.interval, self.retries, self.clock, self.sleep = rpc, 1.0 / min(rps, max_rps), retries, clock, sleep
        self._next = 0.0
        self.calls = 0
        self.burst = max(1, int(burst))
        self._prio = 0
        self.idle_hook: Callable[[], Any] | None = None  # run while a NON-priority call waits (the loop's signal check)
        self._in_hook = False

    @contextlib.contextmanager
    def priority(self):
        """Buy-path calls inside this block may run up to `burst` intervals ahead of the schedule instead of
        waiting behind position polling. The schedule still advances one interval per call, so the sustained
        rate stays capped (a burst is paid back by later waits)."""
        self._prio += 1
        try:
            yield
        finally:
            self._prio -= 1

    def _sleep(self, total: float) -> None:
        """Plain sleep, or (non-priority only) short slices with the idle hook run between them so a new
        signal is never stuck behind a position poll's rate-limit wait."""
        if self.idle_hook is None or self._prio or self._in_hook:
            self.sleep(total)
            return
        end = self.clock() + total
        while True:
            left = end - self.clock()
            if left <= 0:
                return
            self.sleep(min(left, IDLE_SLICE_S))
            self._in_hook = True
            try:
                self.idle_hook()
            finally:
                self._in_hook = False

    def _wait(self) -> None:
        now = self.clock()
        slack = (self.burst - 1) * self.interval if self._prio else 0.0
        for _ in range(4):  # re-check after each sleep: the idle hook's buy may have pushed the schedule out
            if now + slack >= self._next:
                break
            self._sleep(self._next - slack - now)
            now = max(now, self.clock())
        now = max(now, self._next - slack) if self.idle_hook is None else now
        self._next = max(self._next, now) + self.interval

    def __call__(self, method: str, params: list) -> dict:
        delay = 1.0
        for attempt in range(self.retries + 1):
            self._wait()
            self.calls += 1
            try:
                return self.rpc(method, params)
            except (Exception, SystemExit) as exc:  # label only; Ctrl-C and crashes still propagate
                label = error_label(exc)
                if label not in self.RETRY or attempt == self.retries:
                    raise RpcError(label) from None
                self._sleep(delay)
                delay *= 2
        raise RpcError("unreachable")


def check_stop_file(limits: Limits) -> bool:
    return Path(limits.stop_file).exists()


def check_halt_file(limits: Limits) -> bool:
    return Path(limits.halt_file).exists()


def soft_stops(limits: Limits, st: State, now_ms: int) -> list[str]:
    """The budget stops (attempts, realized loss, days). Live halts on these; dry run only records them."""
    out = []
    if st.attempts >= limits.max_attempts:
        out.append("max_attempts")
    if st.realized_lamports <= -limits.loss_cap_lamports:
        out.append("loss_cap")
    if st.first_attempt_ms is not None and now_ms - st.first_attempt_ms >= limits.max_days * 86_400_000:
        out.append("max_days")
    return out


def check_buy(limits: Limits, st: State, now_ms: int, stop_file_present: bool, mode: str = "live",
              halt_file_present: bool = False) -> str | None:
    """None when a new buy attempt is allowed, else the stop reason. Order is fixed. The default
    mode is the strict one. In mode "dryrun" the three budget stops do not halt (the dry run costs
    nothing and its rows are the dataset); max_open and the stop file still do."""
    if halt_file_present:
        return "halt_file"
    if stop_file_present:
        return "stop_file"
    if mode != MODE:
        soft = soft_stops(limits, st, now_ms)
        if soft:
            return soft[0]
    if len(st.open) >= limits.max_open:
        return "max_open"
    return None


def check_sell(halt_file_present: bool) -> str | None:
    """Only the HALT file freezes sells. The STOP file and the automatic stops (attempts, loss,
    days) halt new buys only: an open position is still wound down, so nothing strands."""
    return "halt_file" if halt_file_present else None


# --- pricing (V-corrected, mirrors the paper scorer) -----------------------------------------


@dataclass(frozen=True)
class Snapshot:
    ps: tx.PoolState
    slot: int
    quote_vault: int  # vault only
    base_reserve: int
    v: int | None  # pool-account virtual_quote_reserves; None when unreadable

    @property
    def quote_priced(self) -> int | None:
        """Swap math prices on vault + V (CLAUDE.md memory: pumpswap-virtual-reserve)."""
        return None if self.v is None else self.quote_vault + self.v


def fee_ppm_for(quote_priced: int, base: int) -> int:
    return pcm.pumpswap_sol_fee_ppm(pcm.market_cap_sol(quote_priced, base))


def entry_quote(snap: Snapshot, spend: int) -> dict[str, Any]:
    q = snap.quote_priced
    assert q is not None
    fee = fee_ppm_for(q, snap.base_reserve)
    tokens = tx.cp_buy_out(spend, q, snap.base_reserve, fee)
    net = spend * (1_000_000 - fee) // 1_000_000
    mark = pcm.spot_sol_per_ui(q + net, snap.base_reserve - tokens) if tokens > 0 else 0.0
    return {"tokens": tokens, "net_in": net, "fee_ppm": fee, "mark": mark}


def exit_check(pos: dict[str, Any], snap: Snapshot, now_ms: int) -> dict[str, Any]:
    """Paper rule `_walk_exit` (tools/paper_tape_scoreboard.py) on a V-priced book: spot of the
    pool plus our virtual buy against the post-buy mark; tp/sl on that return; time stop at
    MAX_HOLD_MS after entry. Returns {"reason": tp|sl|time_stop|None, ret, quote_out}."""
    q = snap.quote_priced
    book = pcm.reserves_with_our_buy(
        quote_lamports=q or 0, base_raw=snap.base_reserve,
        net_in_lamports=pos["net_in"], tokens_raw=pos["tokens"], same_venue=True,
    ) if q else None
    if book is None:
        return {"reason": "time_stop" if now_ms > pos["t_entry_ms"] + EXIT_RULE.max_hold_ms else None, "ret": None, "quote_out": None}
    spot = pcm.spot_sol_per_ui(*book)
    ret = spot / pos["mark"] - 1.0
    fee = fee_ppm_for(*book)
    out = tx.cp_sell_out(pos["tokens"], book[0], book[1], fee)
    reason = None
    if now_ms - pos["t_entry_ms"] > EXIT_RULE.max_hold_ms:
        reason = "time_stop"
    elif ret >= EXIT_RULE.tp:
        reason = "tp"
    elif ret <= -EXIT_RULE.sl:
        reason = "sl"
    return {"reason": reason, "ret": ret, "quote_out": out}


def signals_path(cfg: dict[str, Any]) -> Path:
    sig_file = cfg.get("signals_file", DECISIONS_FILE)
    if sig_file not in SIGNAL_FILES:
        raise ValueError(f"signals_file must be one of {SIGNAL_FILES}")
    return Path(cfg["signals_dir"]) / sig_file


def signals_problem(path: Path) -> str | None:
    """None when the signals file exists and is readable. The unit's bind is optional (`-`), so a missing
    file would otherwise be a silent no-op: the executor would wait forever for signals that never come."""
    try:
        if not path.is_file():
            return f"signals file {path} does not exist (start the runner first: it creates it)"
        if not os.access(path, os.R_OK):
            return f"signals file {path} is not readable"
    except OSError as exc:
        return f"signals file {path} cannot be checked: {type(exc).__name__}"
    return None


def startup_signals_check(cfg: dict[str, Any], live: bool) -> int:
    """Live: refuse to start (exit 2) so systemd's NRestarts rises and the monitor alerts. Dry run: warn."""
    why = signals_problem(signals_path(cfg))
    if why is None:
        return 0
    if live:
        print(f"probe_executor ALERT startup_refused {why}", flush=True)
        return 2
    print(f"probe_executor WARNING {why}", flush=True)
    return 0


ABSENT_ALERT_EVERY_MS = 60_000


# --- signal tailer (the seal lives here) -----------------------------------------------------


def parse_intent(line: str, book: str, ledger: str) -> dict[str, Any] | None:
    """An intents.jsonl row (schema forward_paper_intent_v1), whitelisted to the same fields as an `enter`
    plus `written_ms` (wall clock the runner wrote it; the staleness clock). Anything else is dropped."""
    if '"forward_paper_intent_v1"' not in line:
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict) or row.get("schema") != "forward_paper_intent_v1":
        return None
    if row.get("book") != book or row.get("ledger") != ledger:
        return None
    sig = {k: row.get(k) for k in KEEP_FIELDS}
    if not sig["mint"] or not isinstance(sig["decision_t_ms"], int):
        return None
    w = row.get("written_ms")
    if not isinstance(w, int) or isinstance(w, bool):
        return None
    sig["written_ms"] = w
    return sig


def signal_age_ms(sig: dict[str, Any], now: int) -> int:
    """Age of a signal. Intents carry written_ms (wall clock); an `enter` row only has the tape time."""
    return now - sig.get("written_ms", sig["decision_t_ms"])


def parse_enter(line: str, book: str, ledger: str) -> dict[str, Any] | None:
    """The cheap substring test runs first; the row is then parsed and only a whitelisted
    subset of an `enter` row is returned. Anything else is dropped without being kept."""
    if '"enter"' not in line:
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict) or row.get("action") != "enter":
        return None
    if row.get("book") != book or row.get("ledger") != ledger:
        return None
    sig = {k: row.get(k) for k in KEEP_FIELDS}
    if not sig["mint"] or not isinstance(sig["decision_t_ms"], int):
        return None
    lat = row.get("latency")  # decision-time hop timings: only finite numeric values are kept (no free-form text)
    if isinstance(lat, dict):
        num = {str(k): v for k, v in lat.items()
               if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
        if num and len(json.dumps(num, separators=(",", ":"))) <= MAX_LATENCY_JSON:
            sig["latency"] = num
    return sig


def tail_signals(path: Path, st: State, book: str, ledger: str = DEFAULT_LEDGER, *, intents: bool = False) -> list[dict[str, Any]]:
    """New `enter` signals since the persisted offset. Advances st.offset only over complete
    lines. First run (no state) starts at end of file so history is never replayed. A smaller
    file or a new inode (rotation or truncation) restarts from 0."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return []
    if not st.started:
        st.started, st.offset, st.inode = True, stat.st_size, stat.st_ino
        return []
    if st.inode != stat.st_ino or stat.st_size < st.offset:
        st.offset, st.inode = 0, stat.st_ino
    out: list[dict[str, Any]] = []
    with path.open("rb") as fh:
        fh.seek(st.offset)
        data = fh.read(MAX_READ_BYTES)
    end = data.rfind(b"\n")
    if end < 0:
        if len(data) >= MAX_READ_BYTES:  # one line longer than the cap: skip it, never grow without bound
            st.offset += len(data)
        return []
    for raw in data[: end + 1].splitlines():
        parse = parse_intent if intents else parse_enter
        sig = parse(raw.decode("utf-8", "replace"), book, ledger)
        if sig:
            out.append(sig)
    st.offset += end + 1
    return out


# --- chain access ----------------------------------------------------------------------------


class StaticCache:
    """Pre-warmed static accounts: the parsed global config (fee recipients). A buy then needs only the pool
    and its vaults. Never used past STATIC_TTL_MS; a refresh failure keeps the old value until then."""

    def __init__(self) -> None:
        self.gc: dict[str, Any] | None = None
        self.t_ms = 0

    def get(self, now_ms: int) -> dict[str, Any] | None:
        return self.gc if self.gc is not None and 0 <= now_ms - self.t_ms < STATIC_TTL_MS else None

    def put(self, gc: dict[str, Any], now_ms: int) -> None:
        self.gc, self.t_ms = gc, now_ms

    def refresh(self, rpc: Callable[[str, list], dict], commitment: str, now_ms: int) -> None:
        if self.gc is not None and 0 <= now_ms - self.t_ms < STATIC_REFRESH_MS:
            return
        try:
            info = rpc("getAccountInfo", [str(tx.GLOBAL_CONFIG), {"encoding": "base64", "commitment": commitment}]).get("value")
            if info:
                self.put(tx.parse_global_config(sim._b64(info)), now_ms)
        except KeyboardInterrupt:
            raise
        except BaseException:  # incl. the solders panic type (a BaseException) on malformed account data
            pass  # keep serving the cached value until its TTL; the buy path refetches inline after that


def fetch_snapshot(rpc: Callable[[str, list], dict], pool: str, commitment: str, user: Pubkey,
                   static: StaticCache | None = None, now_ms: int = 0) -> Snapshot | str:
    """Pool + vaults (+ global config when not cached) in two calls. Returns a Snapshot, or a skip-reason string."""
    pk = Pubkey.from_string(pool)
    res = rpc("getAccountInfo", [pool, {"encoding": "base64", "commitment": commitment}])
    info = res.get("value")
    if not info or info.get("owner") != str(tx.PUMPSWAP_PROGRAM):
        return "no_pool"
    pdata = sim._b64(info)
    if len(pdata) < 243:  # parse_pool_account's own >=211 check is looser than the fields it reads (coin_creator ends at 243)
        return "no_pool"
    try:
        p = tx.parse_pool_account(pdata)
    except ValueError:
        return "no_pool"
    cfg = static.get(now_ms) if static is not None else None
    keys = [str(p["base_mint"]), str(p["base_vault"]), str(p["quote_vault"])]
    if cfg is None:
        keys.insert(0, str(tx.GLOBAL_CONFIG))
    multi = rpc("getMultipleAccounts", [keys, {"encoding": "base64", "commitment": commitment}])
    vals = list(multi["value"])
    gc = vals.pop(0) if cfg is None else True
    mint, bv, qv = vals
    if not (gc and mint and bv and qv):
        return "missing_accounts"
    if cfg is None:
        cfg = tx.parse_global_config(sim._b64(multi["value"][0]))
        if static is not None:
            static.put(cfg, now_ms)
    recips = [r for r in cfg["protocol_fee_recipients"] if r != user]
    ps = tx.pool_state_from_accounts(
        pk, pdata, base_token_program=Pubkey.from_string(mint["owner"]),
        protocol_fee_recipient=recips[1] if len(recips) > 1 else recips[0],
        buyback_fee_recipient=cfg["buyback_fee_recipients"][0],
    )
    v = p.get("virtual_quote_reserves")
    if not isinstance(v, int) or v <= 0 or v >= 2**63:  # same unreadable test as pumpswap_virtual.parse_virtual
        v = None
    slot = int((multi.get("context") or {}).get("slot") or 0)
    return Snapshot(ps, slot, sim.token_amount(sim._b64(qv)), sim.token_amount(sim._b64(bv)), v)


def sim_message(rpc: Callable, msg: Message, user: Pubkey, watch: list[Pubkey]) -> dict:
    return sim.simulate(rpc, msg, user, watch)


def buy_probe_message(snap: Snapshot, user: Pubkey, spend: int, slip_bps: int, expected: int, priority: int,
                      blockhash: Hash | None = None) -> Message:
    return tx.build_buy(snap.ps, user, spend, slip_bps, expected, priority_total_lamports=priority, blockhash=blockhash)


def sell_probe_message(snap: Snapshot, user: Pubkey, spend: int, slip_bps: int, priority: int) -> tuple[Message, int, int]:
    """Dry-run sell validity probe. A keyless user holds no tokens, so a bare sell cannot
    simulate. This builds buy+sell in ONE message at the exit-time pool (buy `spend`, then sell
    the guaranteed-minimum tokens of that buy) so the sell instruction, accounts, ATA close and
    WSOL unwrap are exercised against live state. Returns (message, sell_tokens, min_sol_out).
    Live mode sells the real balance instead."""
    q = snap.quote_priced
    assert q is not None
    fee = fee_ppm_for(q, snap.base_reserve)
    got = tx.cp_buy_out(spend, q, snap.base_reserve, fee)
    net = spend * (1_000_000 - fee) // 1_000_000
    sell_tokens = tx.min_out_with_slippage(got, slip_bps)
    book_q, book_b = q + net, snap.base_reserve - got
    expected_out = tx.cp_sell_out(sell_tokens, book_q, book_b, fee_ppm_for(book_q, book_b))
    min_out = tx.min_out_with_slippage(expected_out, slip_bps)
    cu = tx.DEFAULT_BUY_CU_LIMIT + tx.DEFAULT_SELL_CU_LIMIT
    ixs = [
        *tx.compute_budget_ixs(priority, cu),
        *tx.buy_instructions(snap.ps, user, spend, got, slip_bps, priority_total_lamports=priority)[2:],
        *tx.sell_instructions(snap.ps, user, sell_tokens, min_out, priority_total_lamports=priority)[2:],
    ]
    return Message.new_with_blockhash(ixs, user, Hash.default()), sell_tokens, min_out


# --- executor --------------------------------------------------------------------------------


class FillLog:
    def __init__(self, path: Path, mode: str = MODE):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.mode = path, mode

    def write(self, row: dict[str, Any]) -> None:
        row = {"schema": SCHEMA_FILL, "mode": self.mode, **row}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def critical(fn):
    """Mark a multi-step state mutation with RPC calls inside. While it runs, the signal hook inside the limiter
    wait does not handle signals (`signal_tick` returns 0; the next tick picks the rows up)."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        self._crit += 1
        try:
            return fn(self, *a, **kw)
        finally:
            self._crit -= 1
    return wrapper


class Executor:
    def __init__(self, rpc: Callable[[str, list], dict], cfg: dict[str, Any], *, now_ms: Callable[[], int] | None = None):
        self.rpc = rpc
        self.cfg = cfg
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.limits = Limits.from_config(cfg)
        self.book = cfg.get("book", DEFAULT_BOOK)
        self.decisions = signals_path(cfg)
        self.use_intents = self.decisions.name == INTENTS_FILE
        self._absent_last_ms: int | None = None
        self.mode = cfg.get("mode", MODE)
        self.state_path = state_path_for(cfg["state_dir"], self.mode)
        guard_live_state(self.mode, self.state_path, Path(cfg["fill_log"]))
        self.state = State.load(self.state_path, self.mode)
        self.fills = FillLog(Path(cfg["fill_log"]), self.mode)
        self.user = Pubkey.from_string(cfg.get("user") or sim.DEFAULT_USER)  # public, read-only
        self.commitment = cfg.get("commitment", "confirmed")
        cap = float(cfg.get("slippage_cap", DEFAULT_SLIPPAGE_CAP))
        if not math.isfinite(cap) or cap <= 0:
            raise ValueError("slippage_cap must be finite and positive")
        self.slip_bps = int(round(min(cap, DEFAULT_SLIPPAGE_CAP) * 10_000))  # config can lower, never raise
        self.max_signal_age_ms = int(float(cfg.get("max_signal_age_s", 120)) * 1000)
        self.signal_poll_ms = clamp_signal_poll_ms(cfg.get("signal_poll_ms", SIGNAL_POLL_MS_DEFAULT))
        self.poll_ms = int(max(1.0, float(cfg.get("poll_s", 5.0))) * 1000)  # slow loop: positions, exits, pre-warm
        self.fast_exit = "exit_poll_ms" in cfg  # the exit fast path (batched vault read, buy-meta sell amount, priority) is opt-in
        self.exit_poll_ms = clamp_exit_poll_ms(cfg.get("exit_poll_ms"), self.poll_ms)
        self.exit_commitment = cfg.get("exit_commitment") or self.commitment  # exit snapshot + sell quote only
        self.static = StaticCache()
        self._pool_cache: dict[str, tuple[tx.PoolState, int | None, int]] = {}  # mint -> (pool state, V, parsed at ms)
        self._crit = 0
        self._last_slow: int | None = None
        self._last_pos: int | None = None
        self._batch_ms = 0  # when the last batched exit read returned
        self._batch_err_ms: int | None = None

    def _prio(self):
        """Rate-limiter priority for the buy path (a no-op for a plain callable rpc)."""
        fn = getattr(self.rpc, "priority", None)
        return fn() if fn else contextlib.nullcontext()

    def _exit_prio(self):
        """Exit-path calls skip ahead of non-urgent calls on the limiter (fast-exit configs only)."""
        return self._prio() if self.fast_exit else contextlib.nullcontext()

    # -- helpers
    def _log(self, kind: str, mint: str, **kw: Any) -> None:
        self.fills.write({"kind": kind, "ts_ms": self.now_ms(), "mint": mint, "book": self.book, **kw})

    def _skip(self, sig: dict[str, Any], reason: str, **kw: Any) -> None:
        self._log("skip", sig["mint"], reason=reason, decision_t_ms=sig["decision_t_ms"], **kw)

    def _snapshot(self, mint: str, commitment: str | None = None) -> tuple[Snapshot | None, str, str | None]:
        try:
            pool = str(tx.canonical_pool(Pubkey.from_string(mint)))
        except ValueError:
            return None, "", "bad_mint"
        try:
            snap = fetch_snapshot(self.rpc, pool, commitment or self.commitment, self.user, self.static, self.now_ms())
        except (Exception, SystemExit) as exc:  # label only; Ctrl-C and crashes still propagate
            return None, pool, error_label(exc)
        if isinstance(snap, str):
            return None, pool, snap
        if str(snap.ps.base_mint) != mint or snap.ps.quote_mint != tx.WSOL_MINT:
            return None, pool, "no_canonical_pool"
        if self.fast_exit:
            self._pool_cache[mint] = (snap.ps, snap.v, self.now_ms())  # vault addresses and V are constant per pool
        return snap, pool, None

    def _batched_exit_snapshots(self, mints: list[str]) -> dict[str, Snapshot]:
        """ONE getMultipleAccounts for the base+quote vaults of every listed position whose pool was already
        parsed (cache younger than STATIC_TTL_MS). Pool state and V come from the cache. Evidence that they are
        constant per pool: tools/pumpswap_virtual_history.py (PR #280) found V about 17.584 SOL, constant per pool
        across 2026-08-26 to 09-21, and the live fills show v_lamports 17,584,505,493 and 17,584,505,488 on two
        different pools. The 300 s TTL still bounds how long a cached entry is trusted. Anything missing or
        unparseable is simply left out: the caller falls back to a full `fetch_snapshot` for it."""
        if not self.fast_exit:
            return {}
        now = self.now_ms()
        ok = [m for m in mints if m in self._pool_cache and 0 <= now - self._pool_cache[m][2] < STATIC_TTL_MS]
        if not ok:
            return {}
        keys: list[str] = []
        for m in ok:
            ps = self._pool_cache[m][0]
            keys += [str(ps.base_vault), str(ps.quote_vault)]
        out: dict[str, Snapshot] = {}
        try:
            with self._prio():
                multi = self.rpc("getMultipleAccounts", [keys, {"encoding": "base64", "commitment": self.exit_commitment}])
            vals = list(multi["value"])
            if len(vals) != len(keys):
                self._batch_err("bad_value_count")
                return {}
            slot = int((multi.get("context") or {}).get("slot") or 0)
            for i, m in enumerate(ok):
                bv, qv = vals[2 * i], vals[2 * i + 1]
                if not (bv and qv):
                    continue
                ps, v, _t = self._pool_cache[m]
                out[m] = Snapshot(ps, slot, sim.token_amount(sim._b64(qv)), sim.token_amount(sim._b64(bv)), v)
        except (Exception, SystemExit) as exc:  # the full fetch is the fallback; log the label at most once a minute
            self._batch_err(error_label(exc))
            return {}
        self._batch_ms = self.now_ms()
        return out

    def _batch_err(self, label: str) -> None:
        now = self.now_ms()
        if self._batch_err_ms is None or not 0 <= now - self._batch_err_ms < BATCH_ERR_LOG_EVERY_MS:
            self._batch_err_ms = now
            self._log("batch_fallback", "", label=label)

    def _batch_stale(self, mint: str, batched: dict[str, Snapshot]) -> bool:
        """True when `mint` would be priced from a batch fetched more than BATCH_MAX_AGE_MS ago (an earlier
        sell in this pass was slow). The caller skips it; the next tick re-fetches."""
        return mint in batched and self.now_ms() - self._batch_ms > BATCH_MAX_AGE_MS
    def _exit_snapshot(self, mint: str, batched: dict[str, Snapshot]) -> tuple[Snapshot | None, str, str | None, str]:
        """(snapshot, pool, err, "batched"|"full") for one open position."""
        if mint in batched:
            return batched[mint], str(self._pool_cache[mint][0].pool), None, "batched"
        with self._exit_prio():
            snap, pool, err = self._snapshot(mint, self.exit_commitment)
        return snap, pool, err, "full"

    def _exit_fields(self, kind: str) -> dict[str, Any]:
        return {"exit_poll_ms": self.exit_poll_ms, "exit_commitment": self.exit_commitment, "exit_snapshot": kind}

    @staticmethod
    def _sim_summary(res: dict) -> dict[str, Any]:
        return {"err": res.get("err"), "cu_used": res.get("unitsConsumed"), "log_tail": sim.summarize_logs(res.get("logs"))}

    def save(self) -> None:
        self.state.save(self.state_path)

    # -- entry
    def handle_signal(self, sig: dict[str, Any]) -> None:
        now = self.now_ms()
        why = check_buy(self.limits, self.state, now, check_stop_file(self.limits), self.mode, check_halt_file(self.limits))
        if why:
            return self._skip(sig, f"limit:{why}")
        if signal_age_ms(sig, now) > self.max_signal_age_ms:
            return self._skip(sig, "stale_signal")
        if sig["mint"] in self.state.open:
            return self._skip(sig, "already_open")
        snap, pool, err = self._snapshot(sig["mint"])
        t_state = self.now_ms()
        if snap is None:
            return self._skip(sig, err or "no_pool", pool=pool)
        if snap.quote_priced is None:
            return self._skip(sig, "no_v", pool=pool, pool_slot=snap.slot)  # null-V guard: never price on vault alone
        spend = self.limits.size_lamports
        q = entry_quote(snap, spend)
        if q["tokens"] <= 0:
            return self._skip(sig, "zero_quote", pool=pool, pool_slot=snap.slot)
        # Write-ahead: the attempt is counted and durably persisted BEFORE anything is built or
        # simulated (PR-B: before send), so a crash can only over-count, never reset.
        would = soft_stops(self.limits, self.state, now) if self.mode == MODE else []
        for w in would:
            self.state.would_halt[w] = self.state.would_halt.get(w, 0) + 1
        self.state.attempts += 1
        if self.state.first_attempt_ms is None:
            self.state.first_attempt_ms = now
        self.save()
        msg = buy_probe_message(snap, self.user, spend, self.slip_bps, q["tokens"], self.limits.priority_lamports)
        validate_err = None
        try:  # would the live signer's whitelist accept this message? (a real-pool check during the dry run)
            from tools import probe_live

            probe_live.validate_message(msg, snap.ps, Pubkey.from_string(sig["mint"]), self.user, self.limits.priority_lamports)
        except Exception as exc:
            validate_err = getattr(exc, "label", type(exc).__name__)
        t_built = self.now_ms()
        user_base = tx.ata(self.user, snap.ps.base_mint, snap.ps.base_token_program)
        try:
            res = sim_message(self.rpc, msg, self.user, [self.user, user_base])
        except (Exception, SystemExit) as exc:  # label only; Ctrl-C and crashes still propagate
            res = {"err": error_label(exc)}
        t_sim = self.now_ms()
        sim_tokens = None
        accts = res.get("accounts") or [None, None]
        if res.get("err") is None and len(accts) > 1 and accts[1]:
            sim_tokens = sim.token_amount(sim._b64(accts[1]))
        row = dict(
            decision_t_ms=sig["decision_t_ms"], pool=pool, pool_slot=snap.slot, score=sig.get("score"),
            seen_ms=sig.get("seen_ms"), state_ms=t_state, state_slot=snap.slot, built_ms=t_built, simulated_ms=t_sim,
            latency=sig.get("latency"), t_built_ms=t_built, t_sim_ms=t_sim, ms_decision_to_built=t_built - sig["decision_t_ms"],
            ms_built_to_sim=t_sim - t_built, spend_lamports=spend, slippage_bps=self.slip_bps,
            priority_lamports=self.limits.priority_lamports, v_lamports=snap.v, quote_vault_lamports=snap.quote_vault,
            base_reserve=snap.base_reserve, fee_ppm=q["fee_ppm"], expected_tokens=q["tokens"],
            sim_tokens=sim_tokens, would_have_halted=would, live_validate_err=validate_err, tx_bytes=tx.serialized_size(msg), **self._sim_summary(res),
        )
        if sim_tokens is not None and q["tokens"]:
            row["sim_vs_expected_bps"] = round((sim_tokens - q["tokens"]) * 10_000 / q["tokens"], 2)
        self._log("buy", sig["mint"], **row)
        if res.get("err") is None:
            self.state.open[sig["mint"]] = {
                "mint": sig["mint"], "pool": pool, "t_entry_ms": t_sim, "tokens": q["tokens"],
                "net_in": q["net_in"], "mark": q["mark"], "spend": spend,
            }
        self.save()

    # -- exit
    def poll_positions(self) -> None:
        batched = self._batched_exit_snapshots(list(self.state.open))
        for mint, pos in list(self.state.open.items()):
            if check_sell(check_halt_file(self.limits)):
                return
            if self._batch_stale(mint, batched):
                continue
            snap, _pool, err, kind = self._exit_snapshot(mint, batched)
            now = self.now_ms()
            if snap is None or snap.quote_priced is None:
                # No usable price (RPC error or no V): never price on the vault alone. Keep retrying
                # through the deadline and a grace window; only then force a labelled close.
                if now - pos["t_entry_ms"] <= EXIT_RULE.max_hold_ms + UNPRICED_GRACE_MS:
                    continue
                chk = {"reason": "timeout_unpriced", "ret": None, "quote_out": None}
            else:
                chk = exit_check(pos, snap, now)
            if chk["reason"]:
                self._close(mint, pos, snap, chk, now, kind)

    @critical
    def _close(self, mint: str, pos: dict[str, Any], snap: Snapshot | None, chk: dict[str, Any], now: int, kind: str = "full") -> None:
        row: dict[str, Any] = dict(
            exit_reason=chk["reason"], ret=chk["ret"], t_entry_ms=pos["t_entry_ms"], hold_ms=now - pos["t_entry_ms"],
            pool=pos["pool"], tokens=pos["tokens"], spend_lamports=pos["spend"], quote_sol_out_lamports=chk["quote_out"],
            pool_slot=snap.slot if snap else None, priority_lamports=self.limits.priority_lamports,
            **self._exit_fields(kind),
        )
        err: Any = "no_snapshot"
        if snap is not None and snap.quote_priced is not None:
            msg, sell_tokens, min_out = sell_probe_message(snap, self.user, pos["spend"], self.slip_bps, self.limits.priority_lamports)
            t_built = self.now_ms()
            try:
                res = sim_message(self.rpc, msg, self.user, [self.user])
            except (Exception, SystemExit) as exc:  # label only; Ctrl-C and crashes still propagate
                res = {"err": error_label(exc)}
            row.update(t_built_ms=t_built, t_sim_ms=self.now_ms(), probe_sell_tokens=sell_tokens, probe_min_sol_out=min_out, **self._sim_summary(res))
            err = res.get("err")
        if chk["quote_out"] is not None:
            # Dry-run P&L is the V-priced quote less both priority fees and base fees. Pool fees are
            # inside the quote. Token/WSOL ATA rent is refunded on close and ignored.
            fees = 2 * self.limits.priority_lamports + 2 * tx.BASE_FEE_PER_SIGNATURE
            pnl = chk["quote_out"] - pos["spend"] - fees
        else:
            pnl = None  # forced close with no price: labelled, and NOT booked into realized loss
        row.update(pnl_lamports=pnl, sell_probe_err=err)
        self._log("sell", mint, **row)
        if pnl is not None:
            self.state.realized_lamports += pnl
        del self.state.open[mint]
        self.save()

    # -- loop
    def signal_tick(self) -> int:
        """The fast path. Stat first (inode and size against the persisted offset) and read only when the file
        grew, rotated or shrank. New `enter` rows are handled immediately, under buy priority on the limiter."""
        if self._crit:
            return 0
        try:
            s = self.decisions.stat()
        except FileNotFoundError:
            self._signals_absent()
            return 0
        self._absent_last_ms = None
        st = self.state
        if st.started and st.inode == s.st_ino and s.st_size == st.offset:
            return 0
        before = (st.started, st.offset, st.inode)
        sigs = tail_signals(self.decisions, st, self.book, intents=self.use_intents)
        seen = self.now_ms()
        if (st.started, st.offset, st.inode) != before:
            self.save()  # offset first: a crash mid-signal must not replay it
        if not sigs:
            return 0
        with self._prio():
            for sig in sigs:
                sig["seen_ms"] = seen
                self.handle_signal(sig)
        return len(sigs)

    def _signals_absent(self) -> None:
        """The signals file is gone or never appeared: say so loudly, at most once a minute (live: ALERT)."""
        now = self.now_ms()
        if self._absent_last_ms is not None and 0 <= now - self._absent_last_ms < ABSENT_ALERT_EVERY_MS:
            return
        self._absent_last_ms = now
        tag = "ALERT" if self.mode == "live" else "WARNING"
        print(f"probe_executor {tag} signals_file_missing path={self.decisions}", flush=True)

    def step(self) -> int:
        n = self.signal_tick()
        self.poll_positions()
        return n

    def prewarm(self) -> None:
        self.static.refresh(self.rpc, self.commitment, self.now_ms())

    def _clock_note(self, now: int) -> None:
        pass

    def housekeeping(self, now: int) -> None:
        pass

    def tick(self) -> int:
        """One pass of the split loop: signal check every call (the caller sleeps `signal_poll_ms`), position
        work and pre-warm every `poll_ms`. If a buy was handled this pass the slow work waits one cycle, unless
        it is already more than a full period overdue."""
        now = self.now_ms()
        self._clock_note(now)
        n = self.signal_tick()
        now = self.now_ms()
        self.housekeeping(now)
        last = self._last_slow
        if last is None or now - last >= self.poll_ms:
            if n and last is not None and now - last < 2 * self.poll_ms:
                return n
            self._last_slow = now
            self._last_pos = now
            self.prewarm()
            self.poll_positions()
        elif (self.exit_poll_ms < self.poll_ms and not n and self._active_positions()
              and (self._last_pos is None or now - self._last_pos >= self.exit_poll_ms)):
            self._last_pos = now  # fast exit poll: positions only; pre-warm stays on the slow cadence
            self.poll_positions()
        return n

    def _active_positions(self) -> bool:
        pending = self.state.pending
        return any(m not in pending for m in self.state.open)

    def run_loop(self, sleep: Callable[[float], None] = time.sleep) -> None:
        if hasattr(self.rpc, "idle_hook"):
            self.rpc.idle_hook = self.signal_tick  # a rate-limit wait inside a position poll still sees new signals
        while True:
            self.tick()
            sleep(self.signal_poll_ms / 1000.0)


def status_report(cfg: dict[str, Any]) -> str:
    """Counters, open positions and realized P&L from the state files and the fill log. Reads no key and
    no URL, makes no RPC call."""
    lim = Limits.from_config(cfg)
    lines = [f"stop_file_present={Path(lim.stop_file).exists()} halt_file_present={Path(lim.halt_file).exists()}"]
    for mode in (MODE, "live"):
        path = state_path_for(cfg["state_dir"], mode)
        if not path.exists():
            lines.append(f"[{mode}] no state file")
            continue
        st = State.load(path, mode)
        lines.append(
            f"[{mode}] attempts={st.attempts}/{lim.max_attempts} realized_sol={st.realized_lamports / LAMPORTS:.6f} "
            f"loss_cap_sol={lim.loss_cap_lamports / LAMPORTS:.3f} open={len(st.open)}/{lim.max_open} pending={len(st.pending)} "
            f"first_attempt_ms={st.first_attempt_ms} would_halt={st.would_halt}")
        exposure = sum(int(p.get("spend") or 0) for p in st.open.values()) + sum(
            int(p.get("spend") or 0) for p in st.pending.values() if p.get("kind") == "buy")
        lines.append(f"  open_exposure_sol={exposure / LAMPORTS:.6f} (cost of open + in-flight buys; the loss cap counts realized loss only)")
        for mint, pos in st.open.items():
            lines.append(f"  open {mint} tokens={pos.get('tokens')} spend={pos.get('spend')} stuck={bool(pos.get('stuck'))} "
                         f"abandoned={bool(pos.get('abandoned'))} sell_attempts={pos.get('sell_attempts', 0)}")
        for mint, p in st.pending.items():
            lines.append(f"  pending {p.get('kind')} {mint} sig={p.get('signature')} sends={p.get('sends')}")
    counts: dict[str, int] = {}
    fl = Path(cfg["fill_log"])
    if fl.exists():
        for raw in fl.read_text().splitlines():
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            key = f"{r.get('mode')}:{r.get('kind')}"
            counts[key] = counts.get(key, 0) + 1
    lines.append(f"fill_rows={dict(sorted(counts.items()))}")
    return "\n".join(lines)


STAGES = (  # (label, from field, to field); live rows end at sent_ms, dry-run rows at simulated_ms
    ("decision_to_seen", "decision_t_ms", "seen_ms"),
    ("seen_to_state", "seen_ms", "state_ms"),
    ("state_to_built", "state_ms", "built_ms"),
    ("built_to_sent", "built_ms", "sent_ms"),
    ("built_to_simulated", "built_ms", "simulated_ms"),
    ("decision_to_sent", "decision_t_ms", "sent_ms"),
    ("decision_to_simulated", "decision_t_ms", "simulated_ms"),
)


def _pct(vals: list[float], q: float) -> float:
    v = sorted(vals)
    pos = (len(v) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def latency_report(cfg: dict[str, Any]) -> str:
    """p50/p90 of every stage delta over the buy rows of the fill log, per mode. Timing only: no P&L, no key,
    no URL, no RPC call."""
    fl = Path(cfg["fill_log"])
    rows: list[dict[str, Any]] = []
    if fl.exists():
        for raw in fl.read_text().splitlines():
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("kind") == "buy" and r.get("seen_ms") is not None:
                rows.append(r)
    lines: list[str] = []
    for mode in sorted({str(r.get("mode")) for r in rows}):
        rs = [r for r in rows if str(r.get("mode")) == mode]
        lines.append(f"[{mode}] buy rows with stage stamps: {len(rs)} (ms)")
        series: list[tuple[str, list[float]]] = []
        for label, a, b in STAGES:
            vals = [r[b] - r[a] for r in rs if isinstance(r.get(a), (int, float)) and isinstance(r.get(b), (int, float))]
            series.append((label, vals))
        slots = [r["landed_slot"] - r["state_slot"] for r in rs
                 if isinstance(r.get("landed_slot"), int) and isinstance(r.get("state_slot"), int) and r["state_slot"]]
        series.append(("state_to_landed_slots", slots))
        applied = [r["latency"]["applied_latency_ms"] for r in rs
                   if isinstance(r.get("latency"), dict) and isinstance(r["latency"].get("applied_latency_ms"), (int, float))]
        series.append(("runner_applied_latency_ms", applied))
        for label, vals in series:
            if vals:
                lines.append(f"  {label:<28} n={len(vals):<5} p50={_pct(vals, 0.5):.1f} p90={_pct(vals, 0.9):.1f}")
    return "\n".join(lines) if lines else "no buy rows with stage stamps"


def resolve_mode(cfg_mode: str, live_flag: bool) -> tuple[str, str | None]:
    """Live needs BOTH the config mode and the --live flag. Returns (mode, warning)."""
    if cfg_mode not in (MODE, "live"):
        raise SystemExit(f"unknown mode {cfg_mode!r}")
    cfg_live = cfg_mode == "live"
    if cfg_live and live_flag:
        return "live", None
    if cfg_live or live_flag:
        missing = "--live flag" if cfg_live else 'config "mode": "live"'
        return MODE, f"live requested but {missing} is missing: running DRY RUN"
    return MODE, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DEC-019 probe executor (dry run by default; live needs config mode AND --live)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--once", action="store_true", help="one tail+poll pass, then exit")
    ap.add_argument("--live", action="store_true", help='second live switch; also needs config "mode": "live"')
    ap.add_argument("--status", action="store_true", help="print counters, open positions, realized P&L; no key, no URL")
    ap.add_argument("--latency-report", action="store_true", help="p50/p90 of each stage delta from the fill log; no P&L, no key, no URL")
    ap.add_argument("--env-file", default=sim.DEFAULT_ENV_FILE)
    args = ap.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text())
    if args.status:
        print(status_report(cfg))
        return 0
    if args.latency_report:
        print(latency_report(cfg))
        return 0
    mode, warn = resolve_mode(cfg.get("mode", MODE), args.live)
    cfg["mode"] = mode
    if warn:
        print(f"probe_executor WARNING {warn}", flush=True)
    poll_s = max(1.0, float(cfg.get("poll_s", 5.0)))
    rc = startup_signals_check(cfg, mode == "live")
    if rc:
        return rc
    if mode == "live":
        from tools import probe_live  # key handling lives only in that module

        return probe_live.run_live(cfg, args, poll_s)
    rpc = LimitedRpc(ProbeRpc(sim.load_rpc_url(None, args.env_file)), rps=float(cfg.get("rps", MAX_RPS)))
    ex = Executor(rpc, cfg)
    print(f"probe_executor mode=dryrun book={ex.book} limits={ex.limits}", flush=True)
    if args.once:
        ex.step()
        return 0
    ex.run_loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
