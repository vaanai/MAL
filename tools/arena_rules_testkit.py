"""Synthetic tape, fakes and helpers for tools/test_arena_rules*.py. TESTS ONLY: nothing here reads the real arena tape, a ledger or bots.json of the arena, and nothing
here says any rule makes money. The tape is a flat, boring PumpSwap market with a few scripted wallet events."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tools.arena_declog import DecisionLog
from tools.arena_paper import ArenaEngine, ArenaRefused
from tools.arena_rules import Host, HostConfig, position_id, register_rules, stack_config
from tools.arena_state import ArenaState

SOL = 1_000_000_000
MIN = 60_000
T0 = 1_790_316_000_000  # 2026-09-25T06:00:00Z, an hour boundary
CLOCK = T0 + 2 * MIN
SLOT0 = 400_000_000
V = 17_584_505_288  # inside the canonical band [17.5e9, 17.7e9]
V_OUT = 25_000_000_000  # outside the band, inside the engine's own 30 SOL bound
WSOL = "So11111111111111111111111111111111111111112"
OTHER = "Quote" + "0" * 39  # a non-WSOL quote mint
SHA = "abcdef1234567"
SHA40 = SHA + "0" * 27
POOL = {k: f"Pool{k}" + "1" * 39 for k in "ABDE"}  # A: WSOL, B: non-WSOL quote, D: WSOL with V out of band, E: WSOL
MINT = {k: f"Mint{k}" + "1" * 39 for k in "ABCDE"}  # C: a bonding curve
END = T0 + 30 * MIN

# offsets (ms from T0) of the scripted events; +137 keeps them off the filler grid
OFF = {"pre_clock_buy_E": 60_000, "buy_A": 300_000, "buy_A_other_wallet": 310_000, "buy_A_again": 320_000, "zed_sell_A": 400_000, "sell_A": 480_000,
       "buy_B": 540_000, "buy_C": 545_000, "buy_D": 550_000, "buy_A_after_exit": 570_000, "buy_E": 600_000, "sell_E_other_wallet": 660_000}
TICK_START = T0 + MIN


def at(name: str) -> int:
    return T0 + OFF[name] + 137


def slot_of(t: int) -> int:
    return SLOT0 + (t - T0) // 400


def swap(t: int, pool_key: str, trader: str, side: str, n: int, *, v: int = V, sol: int = 100_000_000, quote: str = WSOL) -> dict[str, Any]:
    q, b = 60 * SOL + (n % 7) * 1_000_000, (60 * SOL + v) * 1000  # price about 1e-6 SOL per token, flat
    return {"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": slot_of(t), "t_recv_ms": t, "signature": f"s{t}-{pool_key}-{n}", "event_index": 0,
            "pool": POOL[pool_key], "mint": MINT[pool_key], "trader": trader, "side": side, "sol_lamports": sol, "token_raw": sol * 1000, "quote_reserve": q,
            "base_reserve": b, "virtual_quote_reserves": v, "quote_mint": quote, "event_ts": t // 1000}


def curve(t: int, trader: str, side: str, n: int, sol: int = 100_000_000) -> dict[str, Any]:
    return {"v": 1, "type": "trade", "prog": "pumpfun", "venue": "pump_bonding", "slot": slot_of(t), "t_recv_ms": t, "signature": f"c{t}-{n}", "event_index": 0, "pool": None,
            "mint": MINT["C"], "trader": trader, "side": side, "sol_lamports": sol, "token_raw": sol * 1000, "quote_reserve": 30 * SOL, "base_reserve": 1_070_000_000_000_000,
            "quote_mint": WSOL, "event_ts": t // 1000}


def wallets(rule: Any) -> list[str]:
    return sorted(rule.wallets)


def scenario_rows(rule: Any, *, t_end: int = END) -> list[dict[str, Any]]:
    """Flat filler on four pools and a bonding curve, plus the scripted wallet events (see OFF)."""
    w1, w2, w3 = wallets(rule)[:3]
    rows: list[dict[str, Any]] = []
    for k, quote in (("A", WSOL), ("B", OTHER), ("D", WSOL), ("E", WSOL)):
        rows.append({"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": SLOT0 - 1000, "t_recv_ms": T0 - 10 * MIN, "signature": f"pc{k}", "event_index": 0,
                     "pool": POOL[k], "mint": MINT[k], "quote_mint": quote})
    n = 0
    for t in range(T0, t_end, 800):
        for k, quote, v in (("A", WSOL, V), ("B", OTHER, V), ("D", WSOL, V_OUT), ("E", WSOL, V)):
            n += 1
            rows.append(swap(t + (1 if k in "DE" else 0), k, f"FILL{n % 4}", "buy" if n % 2 else "sell", n, v=v, quote=quote))
        n += 1
        rows.append(curve(t + 2, f"FILL{n % 4}", "buy" if n % 2 else "sell", n))
    ev = [("pre_clock_buy_E", "E", w2, "buy"), ("buy_A", "A", w1, "buy"), ("buy_A_other_wallet", "A", w2, "buy"), ("buy_A_again", "A", w1, "buy"),
          ("zed_sell_A", "A", "ZED" + "1" * 40, "sell"), ("sell_A", "A", w1, "sell"), ("buy_B", "B", w1, "buy"), ("buy_D", "D", w1, "buy"),
          ("buy_A_after_exit", "A", w1, "buy"), ("buy_E", "E", w3, "buy"), ("sell_E_other_wallet", "E", w2, "sell")]
    for name, k, trader, side in ev:
        n += 1
        rows.append(swap(at(name), k, trader, side, n, v=V_OUT if k == "D" else V, quote=OTHER if k == "B" else WSOL))
    n += 1
    rows.append(curve(at("buy_C"), w1, "buy", n))
    return sorted(rows, key=lambda r: (r["t_recv_ms"], r["signature"]))


def hour_name(t: int) -> str:
    return "tape-" + datetime.fromtimestamp(t // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"


def write_tape(d: Path, rows: list[dict[str, Any]]) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    by: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(hour_name(r["t_recv_ms"]), []).append(json.dumps(r))
    for name, lines in by.items():
        (d / name).write_text("\n".join(lines) + "\n")
    return d


class FakeEngine:
    """The engine surface the host uses. Its buy / sell FAIL (AssertionError) unless the decision row is already on disk, read fresh from the decision log."""

    def __init__(self, root: Path, refuse_buy: bool = False) -> None:
        self.root, self.refuse_buy = Path(root), refuse_buy
        self.positions: dict[str, Any] = {}
        self.calls: list[tuple] = []
        self.rows_seen = 0
        self.now_fn = None

    def _on_disk(self, bot: str, kind: str, pid: str) -> bool:
        return any(r.get("kind") == kind and r.get("order_id") == pid for r in DecisionLog(self.root).rows(bot))

    def on_row(self, row: Any) -> None:
        self.rows_seen += 1

    def recover(self) -> int:
        return 0

    def start_record(self, bot_id: str) -> bool:
        return False

    def buy(self, bot_id: str, pool: str, sol_in: Any, decision_ms: int, decision_slot: int, meta: Any = None) -> str:
        pid = position_id(bot_id, pool, decision_ms, decision_slot)
        on_disk = self._on_disk(bot_id, "entry", pid)
        self.calls.append(("buy", bot_id, pool, pid, on_disk))
        assert on_disk, "the decision row was not on disk when buy() was called"
        if self.refuse_buy:
            raise ArenaRefused("clock_not_ready: slot clock not ready (sps unknown)")
        self.positions[pid] = SimpleNamespace(bot_id=bot_id, pool=pool, meta=dict(meta or {}), decision_ms=decision_ms, stamp_ms=decision_ms, decision_slot=decision_slot)
        return pid

    def sell(self, bot_id: str, pid: str, frac: float, decision_ms: int, decision_slot: int, reason: str = "") -> None:
        on_disk = self._on_disk(bot_id, "exit", pid)
        self.calls.append(("sell", bot_id, pid, reason, on_disk))
        assert on_disk, "the exit decision row was not on disk when sell() was called"
        if pid not in self.positions:
            raise ArenaRefused(f"no open position {pid} for bot {bot_id}")
        del self.positions[pid]


def make_host(tmp: Path, rules: list[Any], *, rows: list[dict] | None = None, clock: int = CLOCK, fake: bool = False, refuse_buy: bool = False, live: bool = False,
              now_fn: Any = None, binding: Any = None, register: bool = True, **cfg_kw: Any) -> Host:
    """A host over a tape written under tmp/tape (once), with its own decision root, ledger and registry under tmp. Registers the rules unless already registered."""
    tape = tmp / "tape"
    if not tape.exists():
        write_tape(tape, rows if rows is not None else scenario_rows(rules[0]))
    reg = tmp / "bots.json"
    if register and not reg.exists():
        register_rules(rules, str(reg), SHA, clock, now_ms=T0 - 1, verify_sha=False)
    state = ArenaState(tape, start_ms=0)
    engine = FakeEngine(tmp / "rules", refuse_buy) if fake else ArenaEngine(tmp / "ledger", reg, cfg=stack_config(7), sps_fn=lambda: 0.4, code_state_fn=lambda: (SHA40, False))
    cfg = HostConfig(root=str(tmp / "rules"), registry=str(reg), frozen_sha=SHA, clock_start_ms=clock, live=live, **cfg_kw)
    return Host(cfg, rules, state, engine, now_fn=now_fn, binding=binding)


def decision_rows(host: Host, bot: str) -> list[dict]:
    return host.log.rows(bot)


def decision_files(root: Path, bot: str) -> dict[str, str]:
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted((Path(root) / "decisions" / bot).glob("*.jsonl"))}


def ledger_rows(host: Host, bot: str) -> list[dict]:
    """The engine's ledger rows for a bot, minus what differs between processes by construction (the start record, the engine id and pid)."""
    out = []
    for r in host.engine.ledger.read_bot(bot):
        if r.get("kind") == "start":
            continue
        out.append({k: v for k, v in r.items() if k not in ("engine_id", "pid")})
    return out
