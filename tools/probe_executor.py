"""DEC-019 execution probe, PR-A: KEYLESS DRY-RUN executor.

Follows the fast-0 paper runner's EXP-012 `enter` decisions, builds the exact PumpSwap buy the
probe would send (0.05 SOL, 500k lamports priority, DEFAULT_SLIPPAGE_CAP), runs
simulateTransaction (sigVerify off) against the live chain, tracks a virtual position with the
paper exit rule (tp50/sl30/30-minute cap), and logs every attempt to an append-only JSONL.

There is no signing, no sending and no key loading in this module (live mode is PR-B). The fee
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
import os
import sys
import time
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
    stop_file: str = "/var/lib/mal/live/STOP"

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "Limits":
        kw: dict[str, Any] = {}
        for key, cap in DEC019_MAX.items():
            if key in cfg and cfg[key] is not None:
                kw[key] = min(type(cap)(cfg[key]) if key != "max_days" else float(cfg[key]), cap)
                if kw[key] <= 0:
                    raise ValueError(f"limit {key} must be positive")
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

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.__dict__, separators=(",", ":")))
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


def check_stop_file(limits: Limits) -> bool:
    return Path(limits.stop_file).exists()


def check_buy(limits: Limits, st: State, now_ms: int, stop_file_present: bool) -> str | None:
    """None when a new buy attempt is allowed, else the stop reason. Order is fixed."""
    if stop_file_present:
        return "stop_file"
    if st.attempts >= limits.max_attempts:
        return "max_attempts"
    if st.realized_lamports <= -limits.loss_cap_lamports:
        return "loss_cap"
    if st.first_attempt_ms is not None and now_ms - st.first_attempt_ms >= limits.max_days * 86_400_000:
        return "max_days"
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
        data = fh.read()
    end = data.rfind(b"\n")
    if end < 0:
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


def buy_probe_message(snap: Snapshot, user: Pubkey, spend: int, slip_bps: int, expected: int, priority: int) -> Message:
    return tx.build_buy(snap.ps, user, spend, slip_bps, expected, priority_total_lamports=priority)


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
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def write(self, row: dict[str, Any]) -> None:
        row = {"schema": SCHEMA_FILL, "mode": MODE, **row}
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
        self.state_path = Path(cfg["state_file"])
        self.state = State.load(self.state_path)
        self.fills = FillLog(Path(cfg["fill_log"]))
        self.user = Pubkey.from_string(cfg.get("user") or sim.DEFAULT_USER)  # public, read-only
        self.commitment = cfg.get("commitment", "confirmed")
        self.slip_bps = int(round(float(cfg.get("slippage_cap", DEFAULT_SLIPPAGE_CAP)) * 10_000))
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
        except (SystemExit, Exception) as exc:  # Rpc raises SystemExit with a redacted message
            return None, pool, f"rpc_error:{type(exc).__name__}"
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
        why = check_buy(self.limits, self.state, now, check_stop_file(self.limits))
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
        msg = buy_probe_message(snap, self.user, spend, self.slip_bps, q["tokens"], self.limits.priority_lamports)
        t_built = self.now_ms()
        user_base = tx.ata(self.user, snap.ps.base_mint, snap.ps.base_token_program)
        # The attempt counts once a buy is built, so limits are exercised in dry-run too.
        self.state.attempts += 1
        if self.state.first_attempt_ms is None:
            self.state.first_attempt_ms = t_built
        try:
            res = sim_message(self.rpc, msg, self.user, [self.user, user_base])
        except (SystemExit, Exception) as exc:
            res = {"err": f"rpc_error:{type(exc).__name__}"}
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
            sim_tokens=sim_tokens, tx_bytes=tx.serialized_size(msg), **self._sim_summary(res),
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
                if now - pos["t_entry_ms"] <= EXIT_RULE.max_hold_ms:
                    continue  # no usable V this poll: never price on the vault alone; retry
                chk = {"reason": "time_stop", "ret": None, "quote_out": None}
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
            except (SystemExit, Exception) as exc:
                res = {"err": f"rpc_error:{type(exc).__name__}"}
            row.update(t_built_ms=t_built, t_sim_ms=self.now_ms(), probe_sell_tokens=sell_tokens, probe_min_sol_out=min_out, **self._sim_summary(res))
            err = res.get("err")
        if chk["quote_out"] is not None:
            # Dry-run P&L is the V-priced quote less both priority fees and base fees. Pool fees are
            # inside the quote. Token/WSOL ATA rent is refunded on close and ignored.
            fees = 2 * self.limits.priority_lamports + 2 * tx.BASE_FEE_PER_SIGNATURE
            pnl = chk["quote_out"] - pos["spend"] - fees
        else:
            pnl = -pos["spend"]  # nothing priceable at the time stop: worst case, stops stay conservative
        row.update(pnl_lamports=pnl, sell_probe_err=err)
        self._log("sell", mint, **row)
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DEC-019 probe executor, keyless dry-run")
    ap.add_argument("--config", required=True)
    ap.add_argument("--once", action="store_true", help="one tail+poll pass, then exit")
    ap.add_argument("--env-file", default=sim.DEFAULT_ENV_FILE)
    args = ap.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text())
    if cfg.get("mode", MODE) != MODE:
        raise SystemExit("only mode=dryrun exists in this build (live is PR-B)")
    rpc = sim.Rpc(sim.load_rpc_url(None, args.env_file))
    ex = Executor(rpc, cfg)
    poll_s = float(cfg.get("poll_s", 2.0))
    print(f"probe_executor dryrun book={ex.book} limits={ex.limits}", flush=True)
    while True:
        ex.step()
        if args.once:
            return 0
        time.sleep(poll_s)


if __name__ == "__main__":
    sys.exit(main())
