"""Synthetic tape and helpers for tools/test_arena_rule_j3.py and tools/test_arena_j3_explore.py. TESTS ONLY.

Nothing here reads the real arena tape, a ledger, bots.json or a shakedown directory, and nothing here says any J3 cell makes money: the parent markets are flat and
boring, so every priced trip is a small loss to fees. The clock is inside the W_e range (2026-10-19T08 to 2026-10-20T08) so the explorer accepts the files."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tools.arena_rules import StateView
from tools.arena_rules_testkit import hour_name
from tools.arena_state import ArenaState

SOL = 1_000_000_000
SEC = 1000
HOUR = 3_600_000
B = int(datetime(2026, 10, 19, 8, 5, 20, tzinfo=timezone.utc).timestamp() * 1000)  # 2026-10-19T08:05:20Z, the start of W_e and of the synthetic tape (first receive time)
HOUR0 = B - 320 * SEC  # 2026-10-19T08:00:00Z, the hour that holds it
SLOT0 = 400_000_000
V = 17_584_505_288  # inside the canonical band [17.5e9, 17.7e9]
V_OUT = 25_000_000_000  # outside the band, inside the engine's own 30 SOL bound
WSOL = "So11111111111111111111111111111111111111112"
OTHER = "Quote" + "0" * 39  # a non-WSOL quote mint that is not a parent in the scenario
SHA = "abcdef1234567"


def addr(tag: str) -> str:
    return (tag + "1" * 44)[:44]


def slot_of(t: int) -> int:
    return SLOT0 + (t - B) // 400


class World:
    """Rows of a synthetic PumpSwap market, built by hand. `rows` is sorted by (t_recv_ms, signature) like a tape."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self._n = 0

    def _sig(self, t: int, tag: str) -> str:
        self._n += 1
        return f"s{t}-{tag}-{self._n}"

    @property
    def rows(self) -> list[dict[str, Any]]:
        return sorted(self._rows, key=lambda r: (r["t_recv_ms"], r["signature"]))

    @property
    def ordered(self) -> list[dict[str, Any]]:
        """Sorted by time only (stable): rows of the same millisecond keep the order they were added in, which `rows` does not promise."""
        return sorted(self._rows, key=lambda r: r["t_recv_ms"])

    def pool_create(self, t: int, pool: str, mint: str, quote: str = WSOL) -> None:
        self._rows.append({"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": slot_of(t), "t_recv_ms": t, "signature": self._sig(t, "pc"), "event_index": 0,
                           "pool": pool, "mint": mint, "quote_mint": quote})

    def trade(self, t: int, pool: str, mint: str, side: str, *, quote: str = WSOL, v: int | None = V, trader: str = "FILL0", sol: int = 100_000_000) -> None:
        """One PumpSwap print. A WSOL pool is flat at about 1e-6 SOL per token (reserves PRE-trade, V in the row). A non-WSOL pool is in other units."""
        self._n += 1
        if quote == WSOL:
            q, b = 60 * SOL + (self._n % 7) * 1_000_000, (60 * SOL + (v or 0)) * 1000
        else:
            q, b = 2_000_000_000_000 + (self._n % 7) * 1_000_000, 2_000_000_000_000_000
        row = {"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": slot_of(t), "t_recv_ms": t, "signature": self._sig(t, "tr"), "event_index": 0,
               "pool": pool, "mint": mint, "trader": trader, "side": side, "sol_lamports": sol, "token_raw": sol * 1000, "quote_reserve": q, "base_reserve": b,
               "quote_mint": quote, "event_ts": t // 1000}
        if v is not None:
            row["virtual_quote_reserves"] = v
        self._rows.append(row)

    def flow(self, pool: str, mint: str, t0: int, t1: int, *, every: int = 3 * SEC, quote: str = WSOL, v: int | None = V) -> None:
        """Alternating buys and sells every `every` ms in [t0, t1)."""
        for i, t in enumerate(range(t0, t1, every)):
            self.trade(t, pool, mint, "buy" if i % 2 == 0 else "sell", quote=quote, v=v, trader=f"FILL{i % 4}")

    def child_buys(self, pool: str, mint: str, parent_mint: str, first_buy: int, n_buys: int, *, every: int = 10 * SEC, with_sells: bool = True) -> list[int]:
        """`n_buys` buys of a child pool (quote = the parent mint) from `first_buy`, a sell half-way between consecutive buys. Returns the buy times."""
        times = []
        for j in range(n_buys):
            t = first_buy + j * every
            times.append(t)
            self.trade(t, pool, mint, "buy", quote=parent_mint, v=V, trader=f"CB{j % 3}")  # an in-band V on a non-WSOL pool: a trap, never V-range
            if with_sells:
                self.trade(t + every // 2, pool, mint, "sell", quote=parent_mint, v=V, trader=f"CS{j % 3}")
        return times


# ---- the scenario ---------------------------------------------------------------------------------------------------------------------------------
POOL = {k: addr(f"Pool{k}") for k in ("PA", "PB", "PC", "PD", "PE", "PS", "PG", "CA1", "CA2", "CA3", "CB", "CC", "CD", "CE", "CE2", "CS", "CF")}
MINT = {k: addr(f"Mint{k}") for k in ("PA", "PB", "PC", "PD", "PE", "PS", "PG", "CA1", "CA2", "CA3", "CB", "CC", "CD", "CE", "CE2", "CS", "CF")}
OFF = 137  # keeps the scripted prints off the parents' 3 s filler grid
TAPE_SECONDS = 3_500  # the parents trade until B + 3,500 s


def at(seconds: int) -> int:
    return B + seconds * SEC + OFF


def scenario() -> tuple[World, dict[str, list[int]]]:
    """One tape, 12 cells' worth of structure. Parents (the priced pools) and children (trigger events only):

      PA  WSOL, V in band, create +5 s, trades every 3 s                     -> children CA1 (first buy +400 s), CA2 (+420 s, inside CA1's hold), CA3 (+2,000 s)
      PB  WSOL, V out of band (25e9)                                          -> child CB (+400 s): parent_v_out_of_band
      PC  mint MC has ONE pool, quoted in OTHER (not WSOL) with V in band     -> child CC (+400 s): parent_no_wsol_pool
      PD  WSOL, create +500 s                                                 -> child CD (+800 s): s0 only 299 s before the decision
      PE  WSOL, create NOT on the tape, first print +1 s                      -> child CE (+400 s) enters (399 s after PE's first print); CE2 (+200 s) is too early
      PS  WSOL, create +5 s, trades only until +300 s                         -> child CS (+500 s): last print before s0 + 360 s
      CF  a child pool whose create is not on the tape (buys from +300 s)    -> child_create_not_on_tape
      PG  an ordinary WSOL pool with buys: not a child, never a trigger
    Each child's buys are 10 s apart with a sell between them."""
    w = World()
    end = B + TAPE_SECONDS * SEC
    for key, v in (("PA", V), ("PB", V_OUT)):
        w.pool_create(B + 5 * SEC, POOL[key], MINT[key])
        w.flow(POOL[key], MINT[key], B + 10 * SEC, end, v=v)
    w.pool_create(B + 5 * SEC, POOL["PC"], MINT["PC"], OTHER)
    w.flow(POOL["PC"], MINT["PC"], B + 10 * SEC, end, quote=OTHER, v=V)
    w.pool_create(B + 500 * SEC, POOL["PD"], MINT["PD"])
    w.flow(POOL["PD"], MINT["PD"], B + 501 * SEC, end)
    w.flow(POOL["PE"], MINT["PE"], B + 1 * SEC, end)  # no pool_create row: the create is before the tape
    w.pool_create(B + 5 * SEC, POOL["PS"], MINT["PS"])
    w.flow(POOL["PS"], MINT["PS"], B + 10 * SEC, B + 300 * SEC)
    w.pool_create(B + 5 * SEC, POOL["PG"], MINT["PG"])
    w.flow(POOL["PG"], MINT["PG"], B + 10 * SEC, end)
    buys: dict[str, list[int]] = {}
    for child, parent, first, n, created in (("CA1", "PA", 400, 25, 100), ("CA2", "PA", 420, 25, 150), ("CA3", "PA", 2000, 25, 1500), ("CB", "PB", 400, 25, 100),
                                             ("CC", "PC", 400, 25, 100), ("CD", "PD", 800, 25, 700), ("CE", "PE", 400, 25, 100), ("CE2", "PE", 200, 1, 50),
                                             ("CS", "PS", 500, 25, 100), ("CF", "PA", 300, 25, None)):
        if created is not None:
            w.pool_create(B + created * SEC, POOL[child], MINT[child], MINT[parent])
        buys[child] = w.child_buys(POOL[child], MINT[child], MINT[parent], at(first), n)
    for j in range(3):  # an ordinary WSOL pool is bought at the same instants: not a child, never a trigger
        w.trade(at(400) + 10 * j, POOL["PG"], MINT["PG"], "buy", trader=f"PGB{j}")
    return w, buys


def write_hours(d: Path, rows: list[dict[str, Any]]) -> list[Path]:
    """Write rows into tape-<hour>.jsonl files under d and compact each to tape-<hour>.jsonl.zst with the zstd CLI (the source is removed, like the feed's
    compactor does). Returns the .zst paths, oldest hour first."""
    d.mkdir(parents=True, exist_ok=True)
    by: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(hour_name(r["t_recv_ms"]), []).append(json.dumps(r))
    out = []
    for name in sorted(by):
        src = d / name
        src.write_text("\n".join(by[name]) + "\n")
        dst = d / (name + ".zst")
        with open(dst, "wb") as fh:
            subprocess.run(["zstd", "-q", "-c", str(src)], stdout=fh, check=True)
        src.unlink()
        out.append(dst)
    return out


class RuleSim:
    """A real ArenaState fed row by row, with the rule shown each row after the state applied it (the host's order). For pure-rule tests."""

    def __init__(self, tmp: Path, clock_start_ms: int = B) -> None:
        tmp.mkdir(parents=True, exist_ok=True)
        self.state = ArenaState(tmp, start_ms=0)
        self.clock_start_ms = clock_start_ms

    def ctx(self, attempted: Any = (), positions: Any = ()) -> SimpleNamespace:
        return SimpleNamespace(state=StateView(self.state), attempted=frozenset(attempted), positions=tuple(positions), now_ms=self.state.now_ms,
                               clock_start_ms=self.clock_start_ms)

    def apply(self, row: dict[str, Any]) -> None:
        self.state.apply(row)

    def feed(self, rule: Any, row: dict[str, Any], **kw: Any):
        self.state.apply(row)
        return rule.evaluate(self.ctx(**kw), row)
