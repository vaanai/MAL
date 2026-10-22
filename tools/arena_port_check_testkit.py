"""Synthetic tape, a toy kit rule and its hand-written arena twin for tools/test_arena_port_check.py. TESTS ONLY: nothing here reads the real arena tape, a ledger or
bots.json, and nothing here says any rule makes money. The toy rule is the kit's TrivialRule semantics (every N-th in-universe buy print per pool per UTC hour, a fixed
hold) plus the two refusals the kit enforces on every trigger (V in the band, pool age >= 360 s) and the kit's per-cell cooldown after the exit."""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import numpy as np

from tools import arena_screen as S
from tools.arena_rules import Buy, Rule, Sell
from tools.arena_rules_testkit import MINT, OTHER, T0, V, V_OUT, WSOL, swap, write_tape
from tools.arena_state import WSOL_MINT

STEP_MS = 4_000  # one print per pool every 4 s, alternating buy and sell: a buy every 8 s
HOUR = 3_600_000
V_LO, V_HI = 17_500_000_000, 17_700_000_000
YOUNG_START_MS = 20 * 60_000  # pool E's first print, 20 min into the tape
POOLS = (("A", WSOL, V, 0), ("B", OTHER, V, 0), ("D", WSOL, V_OUT, 0), ("E", WSOL, V, YOUNG_START_MS))  # key, quote, V, first print offset


def toy_rows(n_hours: int = 2) -> list[dict[str, Any]]:
    """Flat, boring prints on four pools: A (WSOL, V in band), B (a non-WSOL quote), D (WSOL, V out of band), E (WSOL, in band, first print 20 min in)."""
    rows: list[dict[str, Any]] = []
    n = {k: 0 for k, *_ in POOLS}
    for t in range(T0, T0 + n_hours * HOUR, STEP_MS):
        for k, quote, v, start in POOLS:
            if t - T0 < start:
                continue
            n[k] += 1
            rows.append(swap(t + (1 if k in "DE" else 0), k, f"FILL{n[k] % 4}", "buy" if n[k] % 2 else "sell", n[k], v=v, quote=quote))
    return sorted(rows, key=lambda r: (r["t_recv_ms"], r["signature"]))


def write_toy_tape(d: Path, n_hours: int = 2) -> list[Path]:
    """Writes tape-<hour>.jsonl files and returns them, oldest first."""
    write_tape(d, toy_rows(n_hours))
    return sorted(d.glob("tape-*.jsonl"))


class ToyKit:
    """Kit API (arena_screen): every `every`-th buy print of an in-universe pool per hour, one time exit. `slot_offset` injects an off-by-one in the decision slot."""

    name = "toy-kit"

    def __init__(self, every: int = 20, hold_s: float = 60.0, cooldown_s: float = 60.0, tp: float | None = None, sl: float | None = None, trail: float | None = None,
                 slot_offset: int = 0, cell: str = "toy") -> None:
        self.every, self.hold_s, self.cooldown_s, self.slot_offset, self.cell = int(every), float(hold_s), float(cooldown_s), int(slot_offset), cell
        self.tp, self.sl, self.trail = tp, sl, trail

    def cells(self) -> list[S.Cell]:
        return [S.Cell(self.cell, cooldown_s=self.cooldown_s)]

    def triggers(self, hour: S.HourRows, state: S.RunState) -> list[tuple[str, int, S.ExitSpec]]:
        out = []
        for i in np.flatnonzero(hour.in_universe):
            s = hour.rows(int(i))
            for j in np.flatnonzero(hour.isbuy[s])[self.every - 1::self.every]:
                out.append((str(hour.pools[i]), int(hour.sl[s][j]) + self.slot_offset, S.ExitSpec(self.hold_s, self.tp, self.sl, self.trail, self.cell)))
        return out


_SERIAL = itertools.count()


class ToyPort(Rule):
    """The hand-written streaming twin of ToyKit. Pure: what it needs comes from the state (events, V, first print) and the log (positions, attempt keys).
    Injections: `skip_pools` (a dropped pool), `cooldown_blind` (only 'one open position per pool', no cooldown after the exit), `age_blind` (no 360 s pool-age refusal),
    `nondeterministic` (a process-wide counter in the entry meta), `no_hold_meta` (the port forgets to declare hold_s),
    `raise_in_on_row` (a port bug), `tp` / `sl` / `trail` / `event_exit` (declared exit fields; the toy never exits on them)."""

    deciding_stake = 0.25
    model_ids: tuple[str, ...] = ()
    uses_ticks = True
    universe = "pumpswap_wsol"

    def __init__(self, every: int = 20, hold_s: float = 60.0, cooldown_s: float = 60.0, cell: str = "toy", bot_id: str = "toy-port", skip_pools: tuple[str, ...] = (),
                 cooldown_blind: bool = False, age_blind: bool = False, nondeterministic: bool = False, no_hold_meta: bool = False, raise_in_on_row: bool = False,
                 tp: float | None = None, sl: float | None = None, trail: float | None = None, event_exit: bool = False) -> None:
        self.tp, self.sl, self.trail, self.event_exit = tp, sl, trail, event_exit
        self.every, self.hold_s, self.cooldown_s, self.cell, self.bot_id = int(every), float(hold_s), float(cooldown_s), cell, bot_id
        self.skip_pools, self.cooldown_blind, self.age_blind = tuple(skip_pools), cooldown_blind, age_blind
        self.nondeterministic, self.no_hold_meta, self.raise_in_on_row = nondeterministic, no_hold_meta, raise_in_on_row

    def on_row(self, ctx: Any, row: Any) -> list:
        if row.get("type") != "trade" or row.get("side") != "buy" or row.get("prog") != "pumpswap":
            return []
        if self.raise_in_on_row:
            raise RuntimeError("toy port bug")
        pid = row.get("pool")
        p = ctx.state.pool(pid)
        if p is None or p.quote_mint != WSOL_MINT or pid in self.skip_pools:
            return []
        t = int(row["t_recv_ms"])
        hs = t - t % HOUR
        if sum(1 for e in p.events if e[2] and e[0] >= hs) % self.every:  # the N-th, 2N-th ... buy of this UTC hour (the state has applied this row)
            return []
        if not (V_LO <= p.v <= V_HI):
            return []
        if not self.age_blind and t // 1000 - p.first_seen_ms // 1000 < 360:
            return []
        if any(pos.pool == pid for pos in ctx.positions):
            return []
        if not self.cooldown_blind:
            last = max((int(k.split("|")[1]) for k in ctx.attempted if k.startswith(pid + "|")), default=None)
            if last is not None and t < last + int((self.hold_s + self.cooldown_s) * 1000):
                return []
        meta: dict[str, Any] = {"cell": self.cell, "tp": self.tp, "sl": self.sl, "trail": self.trail, "event_exit": self.event_exit}
        if not self.no_hold_meta:
            meta["hold_s"] = self.hold_s
        if self.nondeterministic:
            meta["serial"] = next(_SERIAL)
        return [Buy(pid, meta, attempt_key=f"{pid}|{t}")]

    def on_tick(self, ctx: Any, T: int) -> list:
        return [Sell(pos.position_id, "hold") for pos in ctx.positions if not pos.exiting and T >= pos.decision_ms + int(self.hold_s * 1000)]


def make_rule(**kw: Any) -> ToyPort:
    return ToyPort(**kw)


def never_fire(view: Any) -> bool:
    """A pure exit_fire predicate that never fires: the position runs to its time cap."""
    return False


class ToyFireKit(ToyKit):
    """ToyKit with an event exit (ExitSpec.exit_fire): the kit admits each trigger as the data arrive and keeps the position open until the cap (the predicate never fires)."""

    name = "toy-fire-kit"

    def triggers(self, hour: S.HourRows, state: S.RunState) -> list[tuple[str, int, S.ExitSpec]]:
        return [(p, s, S.ExitSpec(sp.hold_s, sp.tp, sp.sl, sp.trail, sp.cell, exit_fire=never_fire)) for p, s, sp in super().triggers(hour, state)]
