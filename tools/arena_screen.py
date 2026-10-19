#!/usr/bin/env python3
"""arena_screen: a reusable, fast, IN-SAMPLE screen pricer for a new PumpSwap meme-coin rule. PAPER ONLY. No key, no RPC, no wallet, no model.

    python -m tools.arena_screen --rule tools.arena_screen:TrivialRule --block /data/mal/clean-view/explore-0814 --out DIR
    python -m tools.arena_screen --rule pkg.mod:Cls --block <block> [--block ...] --out DIR --count-only      # triggers per day per cell, no price

A screen is a cheap kill test, not evidence. Everything it prints is exploration: it never says a book makes money (the promotion gate is in CLAUDE.md).

RULE API (a small Python object)
    name: str
    cells() -> list of Cell(name, cooldown_s=60.0) or plain names
    triggers(hour: HourRows, state: RunState) -> iterable of (pool, decision_slot, ExitSpec)
  ExitSpec(hold_s, tp, sl, trail, cell): a time exit after hold_s seconds (always; it bounds the hold), plus optional take-profit multiple tp (> 1), stop multiple sl
  (0 < sl < 1) and trailing multiple trail (0 < trail < 1, against the running peak, which starts at the decision spot), each on the POST-trade price of a print at slot
  >= decision slot + k_slow + 1 (the engine may not sell before the slower entry leg lands). `cell` names the cell a trigger belongs to (optional when the rule has one cell).
  The rule gets ONE whole block hour at a time (columns sorted by pool, slot, tx, event) and may look one hour back through state.prev. A rule MUST decide only on rows
  with slot <= its decision slot (the hour is handed over whole for speed; the kit cannot see look-ahead, `causal_probe` re-runs a rule on a truncated hour to catch it).
  AM.2 UNIVERSE (DEC-031 Amendment 2: the rent refund holds only for it): WSOL quote AND V in [17.5e9, 17.7e9] lamports at decision time. The rule enforces it (hour.in_universe /
  hour.v per row); the kit REFUSES every trigger outside it and counts it: refused_non_wsol, refused_v_out_of_band, refused_v_unknown, refused_unknown_pool.
  At most one open position per (cell, pool): a trigger before the previous exit decision + cell.cooldown_s is dropped and counted (overlap_dropped).

PRICING (the engine's own functions, imported, never copied: tools.arena_stack.mint_states / round_trip / book_fail_legs via tools.arena_paper)
  Decision at 1.9 s (1.3 s reported), stake 0.25 SOL deciding (1.0 SOL priced too, entry impact net_in / Qe at both stakes), feed delay 7 slots, E1 floor, 1.15 guard.
  Fail models: flat 15% and pressure at slope 1 with the intercept refit per cell, latency and strand rule (mean p over the cell's fills = 0.289, as stats.py did).
  Strand rule total DECIDES, sc is reported. Rent 0 DECIDES (Am.2: the sell closes the ATA; a stranded trip still pays 2,039,280), the rent-charged leg (2,039,280 per
  filled trip) is reported beside it. Decision slot = the rule's slot; the guard reference is the last print before it; slot seconds per UTC hour come from the hour's own
  prints. A fill lands on the END-bound state of the first print at slot >= landing + 1. Where no such print exists within GRACE_SLOTS (10,000) the state is derived from the
  last print, as the engine does; an attempt whose derived state would need slots past the end of its block is EXCLUDED and counted (block edge).
  BOOTSTRAP: UTC-date clustered, 1,000 draws, seed 1, 5th percentile of the pooled mean SOL per trade.
KILL (any, deciding leg 1.9 s / 0.25 SOL / total / rent 0): mean, total ex-top-3 or total ex-best-day <= 0 under EITHER fail model, or fewer than 3 blocks positive (a block is
  positive when its total is > 0 under BOTH fail models). `thin` (< --min-attempts-per-day, default 20) is reported beside it, not a kill.

DATA GUARDS (before anything is read): block paths only under /data/mal/clean-view/{explore-0814, oracle-insample-2026-09-22_25, fast-pool-2026-09-18T23_2026-09-22T00,
  oracle-live-2026-09-25_27}; /data/mal/arena/tape closed hours (tape-<hour>.jsonl.zst) ONLY with --count-only (structure counts, never a price after a decision); every other path
  is refused; so is any path containing probe, shadow, canary, _shakedown, forward-, fresh-08, exp011, truth-, tip-tape or sealed (raw and resolved); --out must be outside
  /data/mal/arena and /data/mal/clean-view.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/arena_screen.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import collections
import dataclasses
import importlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import numpy as np

from tools import arena_paper as P
from tools import arena_stack as F

SCHEMA = "arena_screen_v1"
CLEAN_VIEW = "/data/mal/clean-view"
BLOCK_ROOTS = tuple(f"{CLEAN_VIEW}/{b}" for b in ("explore-0814", "oracle-insample-2026-09-22_25", "fast-pool-2026-09-18T23_2026-09-22T00", "oracle-live-2026-09-25_27"))
ARENA_ROOT = "/data/mal/arena"
ARENA_TAPE = f"{ARENA_ROOT}/tape"
FORBIDDEN = ("probe", "shadow", "canary", "_shakedown", "forward-", "fresh-08", "exp011", "truth-", "tip-tape", "sealed")
SEALED_HOURS = (datetime(2026, 10, 2, 10, tzinfo=timezone.utc), datetime(2026, 10, 16, 1, tzinfo=timezone.utc))  # D2/D3: no hour in [10-02T10, 10-16T01)
VMAP_DIR = "/data/mal/pumpswap-virtual"
VMAP_DEFAULT = (f"{VMAP_DIR}/pool_v_0814.json", f"{VMAP_DIR}/pool_v_0909.json")  # the explore map, then the wider fallback (hunt21's choice)
WSOL = "So11111111111111111111111111111111111111112"
V_LO, V_HI = 17_500_000_000, 17_700_000_000
D = 7  # feed_delay_slots, pinned like arena_rules.stack_config(7)
LATS = tuple(F.LATENCIES_S)  # (1.3, 1.9)
LAT_DECIDING = 1.9
STAKE_SOL = 0.25
STAKE_BIG = 1.0
RENT_DECIDING, RENT_CHARGED = F.RENT_DECIDING, F.RENT_REPORT
STRAND_RULES = ("total", "sc")
FAILS = ("flat", "press")
GRACE = P.GRACE_SLOTS
RETAIN_EXTRA_S = 5400.0  # a pool's rows are kept this long past the hold: GRACE_SLOTS at up to 0.54 s per slot, plus margin
MIN_HOLD_S = 3.0
EXIT_REASONS = ("time", "tp", "sl", "trail")


class Refused(Exception):
    """A guard refused: nothing was read or written."""


# ------------------------------------------------------------------------------------------------------------------------------ rule API
@dataclass(frozen=True)
class ExitSpec:
    hold_s: float
    tp: float | None = None
    sl: float | None = None
    trail: float | None = None
    cell: str | None = None

    def problem(self) -> str | None:
        ok = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)  # noqa: E731
        if not ok(self.hold_s) or self.hold_s < MIN_HOLD_S:
            return f"hold_s must be a number >= {MIN_HOLD_S}"
        if self.tp is not None and not (ok(self.tp) and self.tp > 1.0):
            return "tp must be a multiple > 1"
        if self.sl is not None and not (ok(self.sl) and 0.0 < self.sl < 1.0):
            return "sl must be a multiple in (0, 1)"
        if self.trail is not None and not (ok(self.trail) and 0.0 < self.trail < 1.0):
            return "trail must be a multiple in (0, 1)"
        return None


@dataclass(frozen=True)
class Cell:
    name: str
    cooldown_s: float = 60.0


@dataclass
class RunState:
    """Per block. `prev` is the previous hour of the same contiguous run (None after a gap and for the first hour); `scratch` is the rule's own memory."""

    block: str
    vmap: Any = None
    prev: "HourRows | None" = None
    gap_before: bool = False
    hour_index: int = 0
    scratch: dict = field(default_factory=dict)


# ------------------------------------------------------------------------------------------------------------------------------ guards
def _low(p: str) -> str:
    return p.lower()


def _forbidden_word(p: str) -> str | None:
    return next((w for w in FORBIDDEN if w in _low(p)), None)


def _under(p: str, root: str) -> bool:
    return p == root or p.startswith(root + "/")


def hour_of_name(name: str) -> datetime | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl\.zst$", name)
    return datetime.strptime(m.group(1), "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) if m else None


def check_block_path(path: str | os.PathLike, count_only: bool) -> str:
    """Returns 'clean' (an exploration block) or 'arena' (the closed arena tape, count-only). Raises Refused."""
    raw = os.path.abspath(str(path))
    real = os.path.realpath(raw)
    for p in (str(path), raw, real):
        w = _forbidden_word(p)
        if w:
            raise Refused(f"path_forbidden_word:{w}: {path}")
    if _under(raw, ARENA_ROOT) or _under(real, ARENA_ROOT):
        if not (count_only and _under(raw, ARENA_TAPE) and _under(real, ARENA_TAPE)):
            raise Refused(f"arena_path_refused: only {ARENA_TAPE} closed hours, and only with --count-only: {path}")
        return "arena"
    if any(_under(raw, r) for r in BLOCK_ROOTS) and any(_under(real, r) for r in BLOCK_ROOTS):
        return "clean"
    raise Refused(f"not_an_exploration_block: {path} (allowed: {', '.join(BLOCK_ROOTS)})")


def check_out_dir(out: str | os.PathLike) -> str:
    raw = os.path.abspath(str(out))
    real = os.path.realpath(raw)
    for p in (raw, real):
        if _under(p, ARENA_ROOT) or _under(p, CLEAN_VIEW):
            raise Refused(f"out_dir_inside_protected_tree: {out}")
    return real


def discover(path: str | os.PathLike, kind: str, hours: Sequence[str] | None = None) -> list[tuple[datetime, str]]:
    """The hour files of one block path (a block root, a worker dir, a trades dir, or for the arena tape a dir or one closed-hour file), sorted by hour."""
    p = Path(path)
    if kind == "arena":
        fs = [p] if p.is_file() else sorted(p.glob("tape-*.jsonl.zst"))
        out = []
        for f in fs:
            if re.fullmatch(r"tape-\d{4}-\d{2}-\d{2}T\d{2}\.jsonl\.zst", f.name) is None or f.parent.resolve() != Path(ARENA_TAPE):
                raise Refused(f"arena_file_refused: {f} (only tape-<hour>.jsonl.zst directly in {ARENA_TAPE})")
            if f.with_suffix("").exists() or Path(str(f) + ".tmp").exists():
                raise Refused(f"arena_hour_not_closed: {f}")
            out.append((hour_of_name(f.name), str(f)))
    else:
        fs = [p] if p.is_file() else sorted(set(p.glob("trades/trades-*.jsonl.zst")) | set(p.glob("w*/trades/trades-*.jsonl.zst")) | set(p.glob("trades-*.jsonl.zst")))
        out = []
        for f in fs:
            if hour_of_name(f.name) is None:
                raise Refused(f"unexpected_file_name: {f}")
            if _forbidden_word(str(f)) or _forbidden_word(os.path.realpath(f)):
                raise Refused(f"path_forbidden_word: {f}")
            out.append((hour_of_name(f.name), str(f)))
    for t, f in out:
        if SEALED_HOURS[0] <= t < SEALED_HOURS[1]:
            raise Refused(f"sealed_hour (D2/D3 [2026-10-02T10, 2026-10-16T01)): {f}")
    if hours:
        want = set(hours)
        out = [(t, f) for t, f in out if t.strftime("%Y-%m-%dT%H") in want]
    out.sort(key=lambda x: x[0])
    seen = [t for t, _ in out]
    if len(set(seen)) != len(seen):
        raise Refused(f"duplicate_hour_in_block: {path}")
    if not out:
        raise Refused(f"no_hour_files: {path}")
    return out


# ------------------------------------------------------------------------------------------------------------------------------ V map and hours
class VMap:
    """pool -> V lamports (the trade rows carry none): the first map that has a non-null value wins. Maps are dicts or JSON files {'v': {pool: V|null}}."""

    def __init__(self, maps: Sequence[Any]) -> None:
        self.maps = [json.load(open(m))["v"] if isinstance(m, (str, os.PathLike)) else m for m in maps]

    def get(self, pool: str) -> float | None:
        for m in self.maps:
            v = m.get(pool)
            if v is not None:
                return float(v)
        return None


@dataclass
class HourRows:
    """One block hour, rows sorted by (pool, slot, tx, event). CSR: pool i owns rows ptr[i]:ptr[i+1]. WSOL-quoted PumpSwap rows only (others are counted)."""

    block: str
    hour: str
    T: int  # hour start, epoch s
    idx: int
    pools: np.ndarray
    mints: np.ndarray
    ptr: np.ndarray
    sl: np.ndarray
    tx: np.ndarray
    ev: np.ndarray
    isbuy: np.ndarray
    sol: np.ndarray
    tok: np.ndarray
    q: np.ndarray  # quote_reserve, PRE-trade, V not included
    b: np.ndarray
    bt: np.ndarray
    trader: np.ndarray  # crc32 of the trader
    v: np.ndarray  # V per row (lamports; NaN = unknown)
    slots_per_s: float
    n_pumpswap: int = 0
    n_nonwsol: int = 0
    nonwsol_pools: frozenset = frozenset()
    _index: dict | None = None

    @property
    def n(self) -> int:
        return len(self.sl)

    @property
    def min_slot(self) -> int:
        return int(self.sl.min()) if self.n else -1

    @property
    def max_slot(self) -> int:
        return int(self.sl.max()) if self.n else -1

    def pool_index(self) -> dict:
        if self._index is None:
            self._index = {nm: i for i, nm in enumerate(self.pools.tolist())}
        return self._index

    @property
    def in_universe(self) -> np.ndarray:
        """Per pool: V of the pool's LAST row in this hour is in [17.5e9, 17.7e9] (WSOL is given: other quotes are not loaded). A rule needing it at an earlier print uses self.v."""
        last = np.maximum(self.ptr[1:] - 1, 0)
        v = self.v[last] if self.n else np.zeros(0)
        return (v >= V_LO) & (v <= V_HI)

    def rows(self, i: int) -> slice:
        return slice(int(self.ptr[i]), int(self.ptr[i + 1]))


def _hour_lines(path: str) -> Iterator[bytes]:
    z = subprocess.Popen(["zstd", "-dc", "-q", path], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    g = subprocess.Popen(["grep", "-F", "-e", "pumpswap"], stdin=z.stdout, stdout=subprocess.PIPE)
    assert z.stdout is not None and g.stdout is not None
    z.stdout.close()
    try:
        yield from g.stdout
    finally:
        g.stdout.close()
        grc, zrc = g.wait(), z.wait()
    if zrc != 0 or grc not in (0, 1):
        raise RuntimeError(f"zstd/grep failed (zstd {zrc}, grep {grc}) on {path}")


def build_hour(lines: Iterable[bytes | str], *, block: str, T: int, idx: int, fmt: str, vmap: VMap | None) -> HourRows:
    """Parse trade rows into columns. fmt 'backfill' (exploration views: tx_index, block_time, V from the map) or 'arena' (the live tape: t_recv_ms order, V on the row)."""
    loads = json.loads
    pid_of: dict[str, int] = {}
    names: list[str] = []
    mints: list[str] = []
    vpool: dict[str, float] = {}
    nonwsol: set = set()
    c = {k: [] for k in ("pid", "sl", "tx", "ev", "ib", "so", "tk", "q", "b", "bt", "tr", "v")}
    n_ps = n_non = 0
    arena = fmt == "arena"
    for line in lines:
        r = loads(line)
        if arena and r.get("type", "trade") != "trade":
            continue
        if (r.get("venue") or r.get("prog")) != "pumpswap":
            continue
        pool = r.get("pool")
        if not pool or r.get("slot") is None or r.get("side") not in ("buy", "sell"):
            continue
        n_ps += 1
        wsol = (r.get("quote_mint") == WSOL) if arena else bool(r.get("quote_is_wsol"))
        if not wsol:
            n_non += 1
            nonwsol.add(pool)
            continue
        i = pid_of.get(pool)
        if i is None:
            i = pid_of[pool] = len(names)
            names.append(pool)
            mints.append(r.get("mint") or "")
            if not arena:
                v0 = vmap.get(pool) if vmap is not None else None
                vpool[pool] = float("nan") if v0 is None else v0
        c["pid"].append(i)
        c["sl"].append(r["slot"])
        if arena:
            c["tx"].append(int(r["t_recv_ms"] or 0) % 2_000_000_000)
            vv = r.get("virtual_quote_reserves")
            c["v"].append(float("nan") if vv is None else float(vv))
            c["bt"].append(r.get("event_ts") or 0)
        else:
            c["tx"].append(r.get("tx_index") or 0)
            c["v"].append(vpool[pool])
            c["bt"].append(r.get("block_time") or r.get("event_ts") or 0)
        c["ev"].append(r.get("event_index") or 0)
        c["ib"].append(r["side"] == "buy")
        c["so"].append(r["sol_lamports"])
        c["tk"].append(r["token_raw"])
        c["q"].append(r["quote_reserve"])
        c["b"].append(r["base_reserve"])
        c["tr"].append(zlib.crc32((r.get("trader") or "").encode()))
    pid = np.array(c["pid"], np.int32)
    sl = np.array(c["sl"], np.int64)
    tx = np.array(c["tx"], np.int64)
    ev = np.array(c["ev"], np.int64)
    o = np.lexsort((ev, tx, sl, pid))
    bt = np.array(c["bt"], np.int64)[o]
    npool = len(names)
    ptr = np.searchsorted(pid[o], np.arange(npool + 1), "left").astype(np.int64)
    sl = sl[o]
    sps = float((sl.max() - sl.min()) / max(1, int(bt.max()) - int(bt.min()))) if len(sl) else 0.0
    ib = np.array(c["ib"], bool)[o]
    return HourRows(block, datetime.fromtimestamp(T, tz=timezone.utc).strftime("%Y-%m-%dT%H"), T, idx, np.array(names, dtype=str), np.array(mints, dtype=str), ptr, sl, tx[o], ev[o],
                    ib, np.array(c["so"], np.float64)[o], np.array(c["tk"], np.float64)[o], np.array(c["q"], np.float64)[o], np.array(c["b"], np.float64)[o], bt,
                    np.array(c["tr"], np.int64)[o], np.array(c["v"], np.float64)[o], sps, n_ps, n_non, frozenset(nonwsol))


_WORKER: dict[str, Any] = {}


def _load_job(job: tuple) -> HourRows:
    path, block, T, idx, fmt = job
    return build_hour(_hour_lines(path), block=block, T=T, idx=idx, fmt=fmt, vmap=_WORKER.get("vmap"))


def iter_hours(files: Sequence[tuple[datetime, str]], block: str, fmt: str, vmap: VMap | None, workers: int = 1) -> Iterator[HourRows]:
    """Hours in order. workers > 1 loads the next hours in forked processes (a bounded window); memory is ~100 MB per hour in flight."""
    _WORKER["vmap"] = vmap
    jobs = [(f, block, int(t.timestamp()), i, fmt) for i, (t, f) in enumerate(files)]
    if workers <= 1:
        for j in jobs:
            yield _load_job(j)
        return
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs: collections.deque = collections.deque()
        it = iter(jobs)
        for j in it:
            futs.append(ex.submit(_load_job, j))
            if len(futs) > workers:
                break
        while futs:
            yield futs.popleft().result()
            nxt = next(it, None)
            if nxt is not None:
                futs.append(ex.submit(_load_job, nxt))


# ------------------------------------------------------------------------------------------------------------------------------ pricing
def screen_cfg(rent: int = RENT_DECIDING) -> P.StackConfig:
    """The deciding stack: arena_rules.stack_config(7) with `rent` (0 deciding, 2,039,280 for the rent-charged leg). Pinned equal in the tests."""
    return P.StackConfig(send=505_000, rent=rent, h_in=0.002608, h_out=0.0016, k_rounding="ceil", stakes_sol=(0.25, 0.5, 1.0), grace_slots=10_000, portal_ppm=0,
                         press_intercept=-1.4197242838379789, strand_rule="both", e1_floor_per_005=384_022, feed_delay_slots=D, gap_slots=150, gap_silence_ms=60_000, max_future_ms=5_000)


def legs(st: dict, stake: float, lat: float, rule: str, cfg: P.StackConfig, intercept: float | None = None) -> dict:
    """Vectorised price_leg over trips. st: ref, Qe[lat], Be[lat], ssb[lat], nearby[lat], Qx, Bx arrays. Equal to arena_paper.price_leg trip by trip (tested to 0 lamports).
    intercept None = refit on these trips (the screen); a number = the provisional one the engine writes into its ledger rows."""
    n = len(st["ref"])
    sk = np.full(n, float(stake))
    total = rule == "total"
    kw = dict(return_stranded=True, return_invalid=True, return_exit_invalid=True, auto_stranded=total, k_tol=F.K_TOL if total else None)
    args = (sk, st["Qe"][lat], st["Be"][lat], st["ref"], st["Qx"], st["Bx"], cfg.send, cfg.rent)
    pnl, filled, strand, bad, xbad = F.round_trip(*args, cfg.h_in, cfg.h_out, **kw)
    pnl0 = F.round_trip(*args, 0.0, 0.0, **kw)[0]
    isfilled = filled & ~bad & ~xbad & ~strand
    extra = np.where(isfilled, np.maximum(0.0, cfg.e1_floor_per_005 * sk / 50_000_000.0 - (pnl0 - pnl)), 0.0)
    pnl = pnl - extra
    strandf = total & filled & (bad | xbad | strand)
    flat, press, c, p = P.book_fail_legs(pnl, filled, strandf, st["ssb"][lat], st["nearby"][lat], cfg.send, intercept=intercept)
    return {"pnl": pnl, "flat": flat, "press": press, "filled": filled, "strand": strandf, "c": c}


def post_price(a: dict) -> np.ndarray:
    """Post-trade price of every print (mint_states' own convention; tested equal to its spot)."""
    qp = np.where(a["ib"], a["q"] + a["so"] * 0.99, a["q"] - a["so"] * 1.01) + a["v"]
    bp = np.where(a["ib"], a["b"] - a["tk"], a["b"] + a["tk"])
    return qp / np.where(bp > 0, bp, a["b"])


def resolve_exit(a: dict, pp: np.ndarray, sd: int, spec: ExitSpec, rate: float, sps: float) -> tuple[int, int, bool]:
    """(exit decision slot, reason code, clamped). Time exit at sd + round(hold_s x slots/s); tp / sl / trail on the first eligible print with slot in
    [sd + k_slow + 1, time exit], whichever comes first."""
    sl = a["sl"]
    kslow = int(F.k_slots_vec(LAT_DECIDING, sps, "ceil"))
    floor_slot = sd + kslow + 1
    xt = sd + int(np.rint(spec.hold_s * rate))
    clamped = xt < floor_slot
    xs, why = max(xt, floor_slot), 0
    if spec.tp is None and spec.sl is None and spec.trail is None:
        return xs, why, clamped
    jd = int(np.searchsorted(sl, sd, "left"))
    j0, j1 = int(np.searchsorted(sl, floor_slot, "left")), int(np.searchsorted(sl, xs, "right"))
    if j1 <= j0:
        return xs, why, clamped
    spot0 = pp[max(jd - 1, 0)]
    seg = pp[j0:j1]
    best = None
    for code, hit in ((1, None if spec.tp is None else seg >= spec.tp * spot0), (2, None if spec.sl is None else seg <= spec.sl * spot0),
                      (3, None if spec.trail is None else seg <= spec.trail * np.maximum(spot0, np.maximum.accumulate(pp[jd:j1]))[j0 - jd:])):
        if hit is None:
            continue
        m = np.flatnonzero(hit)
        if m.size and (best is None or m[0] < best[0]):
            best = (int(m[0]), code)
    if best is not None:
        xs, why = int(sl[j0 + best[0]]), best[1]
    return xs, why, clamped


def pool_states(a: dict, sd: np.ndarray, sps_d: np.ndarray, xs: np.ndarray, sps_x: np.ndarray) -> dict:
    args = (a["sl"], a["ib"], a["so"], a["tk"], a["q"], a["b"], a["v"])
    ms = F.mint_states(*args, sd + D, sps_d, t_marks=np.zeros(len(sd)), slot_s_marks=sps_x, clock_slot_marks=xs + D, lats=LATS, rounding="ceil")
    ref = F.mint_states(*args, sd, sps_d, lats=(LATS[0],), rounding="ceil")["spot"]
    return {"ref": ref, "Qe": ms["Qe"], "Be": ms["Be"], "ssb": ms["ssb"], "nearby": ms["nearby"], "Qx": ms["Qx"], "Bx": ms["Bx"]}


def _take(st: dict, m) -> dict:
    return {k: ({l: x[m] for l, x in v.items()} if isinstance(v, dict) else v[m]) for k, v in st.items()}


def _cat(parts: list[dict]) -> dict:
    return {k: ({l: np.concatenate([p[k][l] for p in parts]) for l in parts[0][k]} if isinstance(parts[0][k], dict) else np.concatenate([p[k] for p in parts])) for k in parts[0]}


# ------------------------------------------------------------------------------------------------------------------------------ statistics
def boot_lo(dsum: np.ndarray, dn: np.ndarray, draws: int = F.BOOT_DRAWS, seed: int = F.BOOT_SEED) -> float:
    """Date-clustered: resample UTC days with replacement; statistic = pooled mean SOL per trade; 5th percentile (stats.py of hunt21, same rng stream)."""
    k = len(dsum)
    if k == 0:
        return float("nan")
    rng = np.random.default_rng(seed)
    r = np.empty(draws)
    for i in range(draws):
        ix = rng.integers(0, k, k)
        r[i] = dsum[ix].sum() / max(1, dn[ix].sum())
    return float(np.percentile(r, F.BOOT_PCT))


def stat(lam: np.ndarray, day: np.ndarray, blk: np.ndarray, blocks: Sequence[str], stake_sol: float = STAKE_SOL) -> dict | None:
    x = np.asarray(lam, float) / 1e9
    if len(x) == 0:
        return None
    days, inv = np.unique(day, return_inverse=True)
    dsum, dn = np.bincount(inv, weights=x), np.bincount(inv).astype(float)
    tot, best = float(x.sum()), float(dsum.max())
    top3 = float(np.sort(x)[::-1][:3].sum())
    lo = boot_lo(dsum, dn)
    pb = {}
    for bi, nm in enumerate(blocks):
        m = blk == bi
        pb[nm] = {"n": int(m.sum()), "total": float(x[m].sum()), "mean_pct": float(x[m].mean() / stake_sol * 100) if m.any() else None}
    return {"n": int(len(x)), "days": int(len(days)), "pos_days": int((dsum > 0).sum()), "mean": float(x.mean()), "mean_pct": float(x.mean() / stake_sol * 100), "ci_lo": lo,
            "ci_lo_pct": lo / stake_sol * 100, "total": tot, "ex_top3": tot - top3, "ex_best_day": tot - best, "top_day_share": (best / tot if tot > 0 else None), "per_block": pb}


def kill_verdict(deciding: dict[str, dict | None], blocks: Sequence[str], min_blocks_positive: int = 3) -> tuple[bool, list[str]]:
    """deciding: {'flat': stat, 'press': stat} at 1.9 s, 0.25 SOL, rule total, rent 0."""
    why = []
    for fail in FAILS:
        s = deciding.get(fail)
        if s is None:
            why.append(f"{fail}: no trips")
            continue
        for k in ("mean", "ex_top3", "ex_best_day"):
            if not s[k] > 0:
                why.append(f"{fail}.{k}<=0")
    npos = 0
    for nm in blocks:
        ok = all(deciding.get(f) is not None and deciding[f]["per_block"][nm]["n"] > 0 and deciding[f]["per_block"][nm]["total"] > 0 for f in FAILS)
        npos += bool(ok)
    if npos < min_blocks_positive:
        why.append(f"blocks_positive {npos}/{len(blocks)} < {min_blocks_positive}")
    return bool(why), why


# ------------------------------------------------------------------------------------------------------------------------------ the screen
@dataclass
class _Store:
    chunks: list = field(default_factory=list)
    trigs: list = field(default_factory=list)  # (cell index, decision slot, hour idx, spec)
    until: int = 0


class Screen:
    def __init__(self, rule: Any, *, count_only: bool = False, min_attempts_per_day: float = 20.0) -> None:
        self.rule, self.count_only, self.min_per_day = rule, count_only, float(min_attempts_per_day)
        raw = list(rule.cells())
        self.cells = [c if isinstance(c, Cell) else Cell(str(c)) for c in raw]
        if not self.cells or len({c.name for c in self.cells}) != len(self.cells):
            raise ValueError("a rule needs at least one cell and unique cell names")
        self.cell_ix = {c.name: i for i, c in enumerate(self.cells)}
        self.counters: collections.Counter = collections.Counter()
        self.blocks: list[str] = []
        self.days: set[int] = set()
        self.cnt = [collections.Counter() for _ in self.cells]  # day -> validated triggers
        self.cnt_ub = [collections.Counter() for _ in self.cells]  # day -> after the time-only overlap rule (an upper bound of attempts)
        self.acc: list[list[dict]] = [[] for _ in self.cells]
        self.cfg = {RENT_DECIDING: screen_cfg(RENT_DECIDING), RENT_CHARGED: screen_cfg(RENT_CHARGED)}
        self.rows = self.hours = 0
        self.t_load = self.t_rule = self.t_price = 0.0

    # --- trigger validation
    def _validate(self, hr: HourRows, tr: Any) -> tuple[int, str, int, ExitSpec] | None:
        c = self.counters
        pool, slot, spec = tr
        c["triggers_raw"] += 1
        i = hr.pool_index().get(pool)
        if i is None:
            c["refused_non_wsol" if pool in hr.nonwsol_pools else "refused_unknown_pool"] += 1
            return None
        if not isinstance(spec, ExitSpec) or spec.problem():
            c["refused_bad_exit"] += 1
            return None
        slot = int(slot)
        a, b = int(hr.ptr[i]), int(hr.ptr[i + 1])
        j = int(np.searchsorted(hr.sl[a:b], slot, "right")) - 1
        if j < 0:
            c["refused_no_print_at_or_before_decision"] += 1
            return None
        v = float(hr.v[a + j])
        if not math.isfinite(v):
            c["refused_v_unknown"] += 1
            return None
        if not (V_LO <= v <= V_HI):
            c["refused_v_out_of_band"] += 1
            return None
        name = spec.cell if spec.cell is not None else (self.cells[0].name if len(self.cells) == 1 else None)
        if name is None or name not in self.cell_ix:
            c["refused_no_such_cell"] += 1
            return None
        return self.cell_ix[name], pool, slot, spec

    # --- one block
    def run_block(self, bi: int, name: str, hours: Iterable[HourRows]) -> None:
        self.blocks.append(name)
        rule, state = self.rule, RunState(block=name)
        if hasattr(rule, "start_block"):
            rule.start_block(state)
        stores: dict[str, _Store] = {}
        clk: dict[str, dict[int, Any]] = {"rate": {}, "min": {}, "T": {}}  # per hour index: slots per second, first slot, hour start
        seg_end = -1
        prev: HourRows | None = None
        nextok: dict[tuple, int] = {}
        t0 = time.time()
        for hr in hours:
            self.t_load += time.time() - t0
            self.hours += 1
            self.rows += hr.n
            self.counters["rows_pumpswap"] += hr.n_pumpswap
            self.counters["rows_non_wsol"] += hr.n_nonwsol
            self.counters["rows_wsol_kept"] += hr.n
            if hr.n == 0 or (prev is not None and hr.T != prev.T + 3600):
                self.counters["empty_hours" if hr.n == 0 else "hour_gaps"] += 1
                self._flush(stores, bi, seg_end, clk)
                prev, seg_end = None, -1
                state.gap_before = True
                if hr.n == 0:
                    t0 = time.time()
                    continue
            state.prev, state.hour_index = prev, hr.idx
            t1 = time.time()
            raw = list(rule.triggers(hr, state))
            self.t_rule += time.time() - t1
            state.gap_before = False
            good = [v for v in (self._validate(hr, t) for t in raw) if v is not None]
            good.sort(key=lambda g: (g[0], g[1], g[2]))
            day = hr.T // 86400
            self.days.add(day)
            clk["rate"][hr.idx], clk["min"][hr.idx], clk["T"][hr.idx] = hr.slots_per_s, hr.min_slot, hr.T
            seg_end = max(seg_end, hr.max_slot)
            for ci, pool, slot, spec in good:
                self.cnt[ci][day] += 1
                if self.count_only:  # time-only overlap rule: an upper bound, since a take-profit / stop exit can only free the pool earlier
                    k = (ci, pool)
                    if slot >= nextok.get(k, -1):
                        self.cnt_ub[ci][day] += 1
                        nextok[k] = slot + int(np.rint((spec.hold_s + self.cells[ci].cooldown_s) * hr.slots_per_s))
                    continue
                s = stores.get(pool)
                if s is None:
                    s = stores[pool] = _Store()
                    if prev is not None and pool in prev.pool_index():
                        s.chunks.append(self._chunk(prev, prev.pool_index()[pool]))
                s.trigs.append((ci, slot, hr.idx, spec))
                s.until = max(s.until, hr.idx + int(math.ceil((spec.hold_s + RETAIN_EXTRA_S) / 3600.0)))
            if not self.count_only:
                for pool, s in stores.items():
                    i = hr.pool_index().get(pool)
                    if i is not None:
                        s.chunks.append(self._chunk(hr, i))
                for pool in [p for p, s in stores.items() if s.until <= hr.idx]:
                    self._price_store(stores.pop(pool), pool, bi, seg_end, clk)
            prev = hr
            t0 = time.time()
        if not self.count_only:
            self._flush(stores, bi, seg_end, clk)

    @staticmethod
    def _chunk(hr: HourRows, i: int) -> dict:
        s = hr.rows(i)
        return {"sl": hr.sl[s].copy(), "ib": hr.isbuy[s].copy(), "so": hr.sol[s].copy(), "tk": hr.tok[s].copy(), "q": hr.q[s].copy(), "b": hr.b[s].copy(), "v": hr.v[s].copy()}

    def _flush(self, stores: dict, bi: int, seg_end: int, clk: dict) -> None:
        for pool in list(stores):
            self._price_store(stores.pop(pool), pool, bi, seg_end, clk)

    def _price_store(self, s: _Store, pool: str, bi: int, seg_end: int, clk: dict) -> None:
        rate, minslot = clk["rate"], clk["min"]
        t0 = time.time()
        a = {k: np.concatenate([c[k] for c in s.chunks]) for k in s.chunks[0]}
        a["ib"] = a["ib"].astype(bool)
        pp = post_price(a)
        hours = np.array(sorted(minslot))
        mins = np.array([minslot[h] for h in hours])
        sps_of = lambda h: 1.0 / rate[int(h)]  # noqa: E731
        c = self.counters
        rows: list[tuple] = []
        for ci in sorted({t[0] for t in s.trigs}):
            nxt = -1
            for _, slot, hi, spec in sorted((t for t in s.trigs if t[0] == ci), key=lambda t: t[1]):
                if slot < nxt:
                    c["overlap_dropped"] += 1
                    continue
                xs, why, clamped = resolve_exit(a, pp, slot, spec, rate[hi], sps_of(hi))
                c["hold_clamped"] += clamped
                nxt = xs + int(np.rint(self.cells[ci].cooldown_s * rate[hi]))
                rows.append((ci, slot, hi, xs, why))
        if not rows:
            return
        R = np.array(rows, np.int64)
        ci, sd, hi, xs, why = R.T
        hx = np.clip(np.searchsorted(mins, xs, "right") - 1, 0, len(mins) - 1)
        sps_d = np.array([sps_of(h) for h in hi])
        sps_x = np.array([sps_of(hours[h]) for h in hx])
        st = pool_states(a, sd, sps_d, xs, sps_x)
        need = np.maximum(xs + D + F.exit_lag_vec(sps_x) + 1, sd + D + F.k_slots_vec(LAT_DECIDING, sps_d, "ceil") + 1)
        n = len(a["sl"])
        jx = np.searchsorted(a["sl"], need, "left")
        has = jx < n
        late = has & (a["sl"][np.minimum(jx, n - 1)] >= need + GRACE)
        nop = ~has
        excl = nop & (need + GRACE > seg_end)
        for i in np.flatnonzero(late):  # the engine derived these from the rows before `need` (the clock passed need + GRACE with no print): redo on the truncated history
            sub = {k: v[: jx[i]] for k, v in a.items()}
            one = pool_states(sub, sd[i:i + 1], sps_d[i:i + 1], xs[i:i + 1], sps_x[i:i + 1])
            for k, v in one.items():
                if isinstance(v, dict):
                    for l in v:
                        st[k][l][i] = v[l][0]
                else:
                    st[k][i] = v[0]
        keep = ~excl
        c["excluded_block_edge"] += int(excl.sum())
        c["derived_last_state"] += int((nop & keep).sum())
        c["late_print_derived"] += int(late.sum())
        st = _take(st, keep)
        st.update({"cell": ci[keep], "blk": np.full(int(keep.sum()), bi, np.int64), "T": np.array([clk["T"][int(h)] for h in hi[keep]], np.int64), "xs": xs[keep], "sd": sd[keep],
                   "why": why[keep], "pool": np.full(int(keep.sum()), zlib.crc32(pool.encode()), np.int64)})
        for k in np.unique(st["cell"]):
            self.acc[int(k)].append(_take(st, st["cell"] == k))
        self.t_price += time.time() - t0

    # --- results
    def results(self) -> dict:
        names = self.blocks
        out_cells = {}
        ndays = max(1, len(self.days))
        for ci, cell in enumerate(self.cells):
            if self.count_only:
                out_cells[cell.name] = {"triggers": int(sum(self.cnt[ci].values())), "attempts_upper_bound": int(sum(self.cnt_ub[ci].values())),
                                        "per_day": {self._d(d): int(self.cnt[ci][d]) for d in sorted(self.days)}, "per_day_after_overlap": {self._d(d): int(self.cnt_ub[ci][d]) for d in sorted(self.days)},
                                        "days_with_trigger": int(sum(1 for d in self.days if self.cnt[ci][d]))}
                continue
            out_cells[cell.name] = self._cell_result(ci, names, ndays)
        return out_cells

    @staticmethod
    def _d(day: int) -> str:
        return datetime.fromtimestamp(day * 86400, tz=timezone.utc).strftime("%Y-%m-%d")

    def _cell_result(self, ci: int, names: list[str], ndays: int) -> dict:
        parts = self.acc[ci]
        if not parts:
            return {"attempts": 0, "kill": True, "kill_reasons": ["no attempts"], "thin": True}
        st = _cat(parts)
        n = len(st["ref"])
        day = st["T"] // 86400
        r: dict[str, Any] = {"attempts": n, "pools": int(len(np.unique(st["pool"]))), "exits": {EXIT_REASONS[k]: int((st["why"] == k).sum()) for k in range(4)},
                             "attempts_per_day": n / ndays, "days_in_run": ndays}
        r["thin"] = bool(self.min_per_day and n / ndays < self.min_per_day)
        stats, legsum, press_c = {}, {}, {}
        for lat in LATS:
            for rule in STRAND_RULES:
                for rent, tag in ((RENT_DECIDING, "rent0"), (RENT_CHARGED, "rentC")):
                    L = legs(st, STAKE_SOL * 1e9, lat, rule, self.cfg[rent])
                    for fail in FAILS:
                        stats[f"{lat}|{rule}|{tag}|{fail}"] = stat(L[fail], day, st["blk"], names)
                    if tag == "rent0":
                        legsum[f"{lat}|{rule}"] = {"fills": int(L["filled"].sum()), "strands": int(L["strand"].sum()), "nofail_mean_pct": float(L["pnl"].mean() / (STAKE_SOL * 1e9) * 100)}
                        press_c[f"{lat}|{rule}"] = L["c"]
        big = {}
        for fail in FAILS:
            Lb = legs(st, STAKE_BIG * 1e9, LAT_DECIDING, "total", self.cfg[RENT_DECIDING])
            big[fail] = stat(Lb[fail], day, st["blk"], names, STAKE_BIG)
        imp = {}
        for lat in LATS:
            fl = legs(st, STAKE_SOL * 1e9, lat, "total", self.cfg[RENT_DECIDING])["filled"]
            fee = F.fee_ppm(st["Qe"][lat], st["Be"][lat])
            imp[str(lat)] = {str(s): (float(np.mean(s * 1e9 * (1 - fee[fl] / 1e6) / st["Qe"][lat][fl])) if fl.any() else None) for s in (STAKE_SOL, STAKE_BIG)}
        r.update(stats=stats, legs=legsum, press_intercept=press_c, stake_1_0_total_rent0_1p9=big, impact=imp)
        dec = {f: stats[f"{LAT_DECIDING}|total|rent0|{f}"] for f in FAILS}
        r["kill"], r["kill_reasons"] = kill_verdict(dec, names)
        return r


# ------------------------------------------------------------------------------------------------------------------------------ rules
class TrivialRule:
    """Loader / pipeline proof, NOT a strategy: every `every`-th buy print of an in-universe pool (per hour), one time exit of `hold_s`. No edge claim of any kind."""

    name = "trivial"

    def __init__(self, every: int = 200, hold_s: float = 300.0, tp: float | None = None, sl: float | None = None, trail: float | None = None) -> None:
        self.every, self.spec = int(every), ExitSpec(hold_s, tp, sl, trail)

    def cells(self) -> list[Cell]:
        return [Cell("trivial")]

    def triggers(self, hour: HourRows, state: RunState) -> list[tuple[str, int, ExitSpec]]:
        out = []
        ok = hour.in_universe
        for i in np.flatnonzero(ok):
            s = hour.rows(int(i))
            idx = np.flatnonzero(hour.isbuy[s])
            for j in idx[self.every - 1::self.every]:
                out.append((str(hour.pools[i]), int(hour.sl[s][j]), self.spec))
        return out


def load_rule(spec: str, kwargs_json: str | None = None) -> Any:
    """'pkg.module:Factory' or '/path/to/file.py:Factory'; Factory(**kwargs) returns the rule."""
    mod, _, fac = spec.rpartition(":")
    if not mod or not fac:
        raise Refused("--rule must be module:Factory or file.py:Factory")
    if mod.endswith(".py"):
        sp = importlib.util.spec_from_file_location("screen_rule_" + Path(mod).stem, mod)
        m = importlib.util.module_from_spec(sp)  # type: ignore[arg-type]
        sp.loader.exec_module(m)  # type: ignore[union-attr]
    else:
        m = importlib.import_module(mod)
    return getattr(m, fac)(**(json.loads(kwargs_json) if kwargs_json else {}))


def causal_probe(rule: Any, hour: HourRows, state: RunState, pool: str, slot: int) -> bool:
    """True if `rule` still fires (pool, slot) when the hour is cut after `slot` (no look-ahead past the decision slot)."""
    keep = hour.sl <= slot
    pid = np.repeat(np.arange(len(hour.pools)), np.diff(hour.ptr))[keep]
    ptr = np.searchsorted(pid, np.arange(len(hour.pools) + 1), "left").astype(np.int64)
    cut = dataclasses.replace(hour, ptr=ptr, _index=None, **{k: getattr(hour, k)[keep] for k in ("sl", "tx", "ev", "isbuy", "sol", "tok", "q", "b", "bt", "trader", "v")})
    return any(p == pool and int(s) == int(slot) for p, s, _ in rule.triggers(cut, state))


# ------------------------------------------------------------------------------------------------------------------------------ output
def _f(x: Any, p: int = 2) -> str:
    return "na" if x is None else f"{x:.{p}f}"


def table_md(res: dict, blocks: list[str], meta: dict) -> str:
    L = [f"# arena_screen TABLE ({meta['rule']}): IN-SAMPLE exploration screen, not evidence, no profit claim", "",
         "Deciding leg: 1.9 s, 0.25 SOL, strand rule total, rent 0 (DEC-031 Am.2 universe enforced), feed delay 7. Blocks: " + "; ".join(f"B{i + 1}={b}" for i, b in enumerate(blocks)) + ".", "",
         "| cell | att | fills | days(+) | flat mean% | flat CIlo% | flat tot | ex3 | exbest | press mean% | press CIlo% | press tot | ex3 | exbest | block mean% flat/press | impact .25/1.0 % | 1.0 SOL flat/press mean% | flags |",
         "|" + "---|" * 18]
    for nm, r in res.items():
        if r["attempts"] == 0:
            L.append(f"| {nm} | 0 |" + " |" * 15 + " KILL (no attempts) |")
            continue
        a, b = r["stats"]["1.9|total|rent0|flat"], r["stats"]["1.9|total|rent0|press"]
        pb = " ".join(f"B{i + 1}:{_f(a['per_block'][bn]['mean_pct'])}/{_f(b['per_block'][bn]['mean_pct'])}" for i, bn in enumerate(blocks))
        big = r["stake_1_0_total_rent0_1p9"]
        im = r["impact"]["1.9"]
        flags = ("KILL " if r["kill"] else "survives-screen ") + ("THIN " if r["thin"] else "")
        L.append(f"| {nm} | {r['attempts']} | {r['legs']['1.9|total']['fills']} | {a['days']}({a['pos_days']}) | {_f(a['mean_pct'])} | {_f(a['ci_lo_pct'])} | {_f(a['total'], 3)} | {_f(a['ex_top3'], 3)} | "
                 f"{_f(a['ex_best_day'], 3)} | {_f(b['mean_pct'])} | {_f(b['ci_lo_pct'])} | {_f(b['total'], 3)} | {_f(b['ex_top3'], 3)} | {_f(b['ex_best_day'], 3)} | {pb} | "
                 f"{_f(100 * im['0.25'], 3)}/{_f(100 * im['1.0'], 3)} | {_f(big['flat']['mean_pct'])}/{_f(big['press']['mean_pct'])} | {flags.strip()} |")
    L += ["", "## Reported legs (not deciding): flat mean% / press mean%, total SOL", "",
          "| cell | 1.3 s total rent0 | 1.9 s sc rent0 | 1.9 s total rent-charged | 1.3 s sc rent0 | strands 1.9 total | KILL reasons |", "|---|---|---|---|---|---|---|"]
    for nm, r in res.items():
        if r["attempts"] == 0:
            continue
        g = lambda k: f"{_f(r['stats'][k]['mean_pct'])} ({_f(r['stats'][k]['total'], 3)})"  # noqa: E731
        L.append(f"| {nm} | {g('1.3|total|rent0|flat')} / {g('1.3|total|rent0|press')} | {g('1.9|sc|rent0|flat')} / {g('1.9|sc|rent0|press')} | {g('1.9|total|rentC|flat')} / "
                 f"{g('1.9|total|rentC|press')} | {g('1.3|sc|rent0|flat')} / {g('1.3|sc|rent0|press')} | {r['legs']['1.9|total']['strands']} | {'; '.join(r['kill_reasons']) or '-'} |")
    return "\n".join(L) + "\n"


def counts_md(res: dict, meta: dict) -> str:
    days = sorted({d for r in res.values() for d in r["per_day"]})
    L = [f"# COUNTS ({meta['rule']}): count-only, no price read", "", "Triggers after the universe refusals; `after overlap` applies the time-only one-open-position rule (an upper bound of attempts).", "",
         "| cell | triggers | after overlap | days>=1 | " + " | ".join(d[5:] for d in days) + " |", "|---|---|---|---|" + "---|" * len(days)]
    for nm, r in res.items():
        L.append(f"| {nm} | {r['triggers']} | {r['attempts_upper_bound']} | {r['days_with_trigger']} | " + " | ".join(str(r["per_day_after_overlap"].get(d, 0)) for d in days) + " |")
    return "\n".join(L) + "\n"


def run(args: argparse.Namespace) -> dict:
    out = check_out_dir(args.out)
    specs = []
    for b in args.block:
        kind = check_block_path(b, args.count_only)
        specs.append((b, kind, discover(b, kind, args.hours)))
    kinds = {k for _, k, _ in specs}
    if "arena" in kinds and not args.count_only:
        raise Refused("arena_tape_needs_count_only")
    rule = load_rule(args.rule, args.rule_kwargs)
    vmap = VMap(args.vmap or VMAP_DEFAULT) if "clean" in kinds else None
    Path(out).mkdir(parents=True, exist_ok=True)
    scr = Screen(rule, count_only=args.count_only, min_attempts_per_day=args.min_attempts_per_day)
    t0 = time.time()
    for bi, (b, kind, files) in enumerate(specs):
        name = os.path.relpath(os.path.realpath(b), CLEAN_VIEW) if kind == "clean" else "arena-tape"
        scr.run_block(bi, name, iter_hours(files, name, "arena" if kind == "arena" else "backfill", vmap, args.workers))
    wall = time.time() - t0
    cells = scr.results()
    meta = {"schema": SCHEMA, "rule": getattr(rule, "name", args.rule), "count_only": args.count_only, "blocks": scr.blocks, "hours": scr.hours, "rows_kept": scr.rows,
            "counters": dict(scr.counters), "wall_s": wall, "rows_pumpswap_per_s": scr.counters["rows_pumpswap"] / wall if wall else None, "rows_kept_per_s": scr.rows / wall if wall else None,
            "seconds": {"load_wait": scr.t_load, "rule": scr.t_rule, "price": scr.t_price}, "stack": scr.cfg[RENT_DECIDING].as_dict(), "workers": args.workers,
            "vmap": list(args.vmap or VMAP_DEFAULT) if vmap else None, "written_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    doc = {"meta": meta, "cells": cells}
    name = "counts" if args.count_only else "results"
    Path(out, f"{name}.json").write_text(json.dumps(doc, indent=1, default=float))
    Path(out, "COUNTS.md" if args.count_only else "TABLE.md").write_text(counts_md(cells, meta) if args.count_only else table_md(cells, scr.blocks, meta))
    return doc


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rule", required=True, help="module:Factory or file.py:Factory")
    ap.add_argument("--rule-kwargs", help="JSON kwargs for the factory")
    ap.add_argument("--block", action="append", required=True, help="a block path (repeat for several blocks)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", nargs="*", help="restrict to these hours, YYYY-MM-DDTHH")
    ap.add_argument("--count-only", action="store_true")
    ap.add_argument("--workers", type=int, default=1, help="hour loader processes (<= 2 on mal-fast-0)")
    ap.add_argument("--min-attempts-per-day", type=float, default=20.0)
    ap.add_argument("--vmap", nargs="*", help="V map JSON files, first non-null wins (default pool_v_0814, pool_v_0909)")
    args = ap.parse_args(argv)
    try:
        doc = run(args)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    m = doc["meta"]
    print(json.dumps({k: m[k] for k in ("rule", "count_only", "hours", "rows_pumpswap_per_s", "rows_kept_per_s", "wall_s", "seconds", "counters")}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
