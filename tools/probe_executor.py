"""DEC-019 execution probe: KEYLESS DRY-RUN executor (live mode is tools/probe_live.py).

Follows the fast-0 paper runner's EXP-012 `enter` decisions, builds the exact PumpSwap buy the
probe would send (0.05 SOL, 500k lamports priority, DEFAULT_SLIPPAGE_CAP), runs
simulateTransaction (sigVerify off) against the live chain, tracks a virtual position with the
paper exit rule (tp50/sl30/30-minute cap), and logs every attempt to an append-only JSONL.

There is no signing, no sending and no key loading in this module (live mode is tools/probe_live.py). The fee
payer in simulation is a public funded address (pumpswap_simulate.DEFAULT_USER), never a key we hold.

FORWARD-READ SEAL (DEC-016 Am.2/Am.3): the only runner file read is the decisions log, and only
rows whose action is `enter` on the ceiling ledger of the configured book are kept, reduced to
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
DECISIONS_FILE = "decisions.jsonl"  # the ONLY runner file this module opens
KEEP_FIELDS = ("mint", "decision_t_ms", "score", "trigger", "book")


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
    stop_file: str = "/var/lib/mal-live/STOP"

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
                 max_rps: float = MAX_RPS):
        if not (math.isfinite(rps) and rps > 0):
            raise ValueError("rps must be finite and positive")
        self.rpc, self.interval, self.retries, self.clock, self.sleep = rpc, 1.0 / min(rps, max_rps), retries, clock, sleep
        self._next = 0.0
        self.calls = 0

    def _wait(self) -> None:
        now = self.clock()
        if now < self._next:
            self.sleep(self._next - now)
            now = self._next
        self._next = now + self.interval

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
                self.sleep(delay)
                delay *= 2
        raise RpcError("unreachable")


def check_stop_file(limits: Limits) -> bool:
    return Path(limits.stop_file).exists()


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


def check_buy(limits: Limits, st: State, now_ms: int, stop_file_present: bool, mode: str = "live") -> str | None:
    """None when a new buy attempt is allowed, else the stop reason. Order is fixed. The default
    mode is the strict one. In mode "dryrun" the three budget stops do not halt (the dry run costs
    nothing and its rows are the dataset); max_open and the stop file still do."""
    if stop_file_present:
        return "stop_file"
    if mode != MODE:
        soft = soft_stops(limits, st, now_ms)
        if soft:
            return soft[0]
    if len(st.open) >= limits.max_open:
        return "max_open"
    return None


def check_sell(stop_file_present: bool) -> str | None:
    """The stop file is the kill switch and halts every action. The automatic stops
    (attempts, loss, days) halt new buys only: an open position is still wound down."""
    return "stop_file" if stop_file_present else None


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


# --- signal tailer (the seal lives here) -----------------------------------------------------


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
    return sig


def tail_signals(path: Path, st: State, book: str, ledger: str = DEFAULT_LEDGER) -> list[dict[str, Any]]:
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
        sig = parse_enter(raw.decode("utf-8", "replace"), book, ledger)
        if sig:
            out.append(sig)
    st.offset += end + 1
    return out


# --- chain access ----------------------------------------------------------------------------


def fetch_snapshot(rpc: Callable[[str, list], dict], pool: str, commitment: str, user: Pubkey) -> Snapshot | str:
    """Pool + vaults + global config in two calls. Returns a Snapshot, or a skip-reason string."""
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
    keys = [str(tx.GLOBAL_CONFIG), str(p["base_mint"]), str(p["base_vault"]), str(p["quote_vault"])]
    multi = rpc("getMultipleAccounts", [keys, {"encoding": "base64", "commitment": commitment}])
    gc, mint, bv, qv = multi["value"]
    if not (gc and mint and bv and qv):
        return "missing_accounts"
    cfg = tx.parse_global_config(sim._b64(gc))
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


class Executor:
    def __init__(self, rpc: Callable[[str, list], dict], cfg: dict[str, Any], *, now_ms: Callable[[], int] | None = None):
        self.rpc = rpc
        self.cfg = cfg
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.limits = Limits.from_config(cfg)
        self.book = cfg.get("book", DEFAULT_BOOK)
        self.decisions = Path(cfg["signals_dir"]) / DECISIONS_FILE
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

    # -- helpers
    def _log(self, kind: str, mint: str, **kw: Any) -> None:
        self.fills.write({"kind": kind, "ts_ms": self.now_ms(), "mint": mint, "book": self.book, **kw})

    def _skip(self, sig: dict[str, Any], reason: str, **kw: Any) -> None:
        self._log("skip", sig["mint"], reason=reason, decision_t_ms=sig["decision_t_ms"], **kw)

    def _snapshot(self, mint: str) -> tuple[Snapshot | None, str, str | None]:
        try:
            pool = str(tx.pool_v2(Pubkey.from_string(mint)))
        except ValueError:
            return None, "", "bad_mint"
        try:
            snap = fetch_snapshot(self.rpc, pool, self.commitment, self.user)
        except (Exception, SystemExit) as exc:  # label only; Ctrl-C and crashes still propagate
            return None, pool, error_label(exc)
        if isinstance(snap, str):
            return None, pool, snap
        return snap, pool, None

    @staticmethod
    def _sim_summary(res: dict) -> dict[str, Any]:
        return {"err": res.get("err"), "cu_used": res.get("unitsConsumed"), "log_tail": sim.summarize_logs(res.get("logs"))}

    def save(self) -> None:
        self.state.save(self.state_path)

    # -- entry
    def handle_signal(self, sig: dict[str, Any]) -> None:
        now = self.now_ms()
        why = check_buy(self.limits, self.state, now, check_stop_file(self.limits), self.mode)
        if why:
            return self._skip(sig, f"limit:{why}")
        if now - sig["decision_t_ms"] > self.max_signal_age_ms:
            return self._skip(sig, "stale_signal")
        if sig["mint"] in self.state.open:
            return self._skip(sig, "already_open")
        snap, pool, err = self._snapshot(sig["mint"])
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
            t_built_ms=t_built, t_sim_ms=t_sim, ms_decision_to_built=t_built - sig["decision_t_ms"],
            ms_built_to_sim=t_sim - t_built, spend_lamports=spend, slippage_bps=self.slip_bps,
            priority_lamports=self.limits.priority_lamports, v_lamports=snap.v, quote_vault_lamports=snap.quote_vault,
            base_reserve=snap.base_reserve, fee_ppm=q["fee_ppm"], expected_tokens=q["tokens"],
            sim_tokens=sim_tokens, would_have_halted=would, tx_bytes=tx.serialized_size(msg), **self._sim_summary(res),
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
        for mint, pos in list(self.state.open.items()):
            if check_sell(check_stop_file(self.limits)):
                return
            snap, _pool, err = self._snapshot(mint)
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
                self._close(mint, pos, snap, chk, now)

    def _close(self, mint: str, pos: dict[str, Any], snap: Snapshot | None, chk: dict[str, Any], now: int) -> None:
        row: dict[str, Any] = dict(
            exit_reason=chk["reason"], ret=chk["ret"], t_entry_ms=pos["t_entry_ms"], hold_ms=now - pos["t_entry_ms"],
            pool=pos["pool"], tokens=pos["tokens"], spend_lamports=pos["spend"], quote_sol_out_lamports=chk["quote_out"],
            pool_slot=snap.slot if snap else None, priority_lamports=self.limits.priority_lamports,
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
    def step(self) -> int:
        sigs = tail_signals(self.decisions, self.state, self.book)
        self.save()  # offset first: a crash mid-signal must not replay it
        for sig in sigs:
            self.handle_signal(sig)
        self.poll_positions()
        return len(sigs)


def status_report(cfg: dict[str, Any]) -> str:
    """Counters, open positions and realized P&L from the state files and the fill log. Reads no key and
    no URL, makes no RPC call."""
    lim = Limits.from_config(cfg)
    lines = [f"stop_file_present={Path(lim.stop_file).exists()}"]
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
        for mint, pos in st.open.items():
            lines.append(f"  open {mint} tokens={pos.get('tokens')} spend={pos.get('spend')} stuck={bool(pos.get('stuck'))} "
                         f"sell_attempts={pos.get('sell_attempts', 0)}")
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
    ap.add_argument("--env-file", default=sim.DEFAULT_ENV_FILE)
    args = ap.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text())
    if args.status:
        print(status_report(cfg))
        return 0
    mode, warn = resolve_mode(cfg.get("mode", MODE), args.live)
    cfg["mode"] = mode
    if warn:
        print(f"probe_executor WARNING {warn}", flush=True)
    poll_s = max(1.0, float(cfg.get("poll_s", 5.0)))
    if mode == "live":
        from tools import probe_live  # key handling lives only in that module

        return probe_live.run_live(cfg, args, poll_s)
    rpc = LimitedRpc(ProbeRpc(sim.load_rpc_url(None, args.env_file)), rps=float(cfg.get("rps", MAX_RPS)))
    ex = Executor(rpc, cfg)
    print(f"probe_executor mode=dryrun book={ex.book} limits={ex.limits}", flush=True)
    while True:
        ex.step()
        if args.once:
            return 0
        time.sleep(poll_s)


if __name__ == "__main__":
    sys.exit(main())
