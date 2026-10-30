#!/usr/bin/env python3
"""r4b_live: the r4-b LIVE DECISION path (EXP-027 book, frozen). DRY and KEYLESS: it decides, it never sends.

What it is
  A streaming engine that ingests walker-schema rows (`trades`, `creates`, `migrations`, the fields `tools/r4b_layout.py` reads) as they arrive and, at
  every UTC hour mark T, makes the r4-b decision of EXP-027 section 2.1 from data before T only:
    universe   members as r4b_layout.build defines them (PDA pool, s0 = first PDA print in [mslot, mslot + 7300), V0 in [17.5e9, 17.7e9], not Mayhem,
               pda_match in event mode), graduated at or after `universe_min_mbt`, 10 min to 48 h after graduation, traded in the last 10 min;
    features   tools/r4b_features.mint_features (the pinned module, unchanged) with grads_rule "asof_pda_first_print" and mint_filters "asof"
               (EXP-027 section 3.1), on the member's prints with slot < sd(T);
    D3         rows with T < block_time(s0) + 600 s are dropped (EXP-027 section 3.4; tools/r4b_read.D3_DROP_S);
    score      tools/r4b_score.bag_score (5 md5-pinned boosters, float64 mean), ranked in float64, ties to the lower mid = (mbt, mint);
    book       LiveBook, the incremental form of tools/r4b_book.xs_book: sell held tokens not in the top 10 at T, buy top-10 tokens not held,
               a token sold at T is not re-bought at T, a gap of more than one hour closes everything at last mark + 1 h.
  Every pinned r4b module is imported, none is copied or edited. The only re-implementations are the walker-row bookkeeping of r4b_layout (SQL there,
  streaming here) and xs_book's loop (incremental here); tools/test_r4b_live.py and scripts/research/r4b_live_equiv.py prove both equal to the pinned code.

What it is not
  Not an executor: LIVE_BUILT is False, nothing here holds a key, opens a wallet or sends. Decisions are written as hash-chained `tools.arena_declog`
  rows (`start`, `entry`, `exit`), the format `tools/arena_live_bridge.py` tails (DEC-032 section 1 item 5 names that bridge over
  `tools/probe_live.LiveExecutor`, the EXP027_SELL_BUILDER pin, as the live path for rule books). The r4-b bridge profile is a separate PR.
  Not run forward: EXP-027 section 11 seals every r4-b outcome at or after W0 until the read, and this lane's brief forbids a forward run before it.
  The only data path built here is `replay_walker`, an EXPLORATION replay (Aug/Sep clean-view blocks of hunt23/RULES.md) that refuses sealed paths and
  any hour at or after 2026-10-01. Nothing here says r4-b makes money; r4-b has zero out-of-sample trades and has not cleared the promotion gate.

Live-feed differences (disclosed, not hidden; the replay proof covers the walker schema only)
  * The public-RPC arena tape has no tx_index and no block_time (tools/arena_tape_schema.md). A feed adapter must supply block_time (event_ts) and
    leave tx_index null (it then sorts first in its slot, as training did with nulls); the within-slot order then follows arrival (frn), not chain order.
  * sd(T) needs the first second at or after T to be complete in the clock; a row of an earlier slot that arrives after the decision is not seen.
  * Bonding constants (c_nbond, c_bond_*) need every bonding print of a mint since its create; a live start sees only prints after the start
    (the same train/serve skew EXP-027 section 3.4 discloses for history before H0).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from array import array
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

if __package__ in (None, ""):  # `python tools/r4b_live.py` from the repo root
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import r4b_features as rf  # noqa: E402
from tools import r4b_frozen as FZ  # noqa: E402
from tools import r4b_layout as rl  # noqa: E402

LIVE_BUILT = False  # this module builds no live entry point; Engine(mode="live") is refused
SCHEMA = "r4b_live_v1"
BOT_ID = "r4b-exp027"
BOOK = "r4-b"
MARK_S = FZ.REBALANCE_S  # 3600
D3_DROP_S = 600  # = tools/r4b_read.D3_DROP_S (a test asserts it)
GRID_LO_S = 600  # decisions from mbt + 600 s (r4b_features grid start)
GRID_HI_S = 48 * 3600  # to mbt + 48 h
HORIZON_S = rl.HORIZON_S  # ut keeps prints with block_time <= mbt + 52 h + 1800 s
WINDOW_SLOTS = rl.WINDOW_SLOTS  # 7300
V_LO, V_HI = rl.V_LO, rl.V_HI
WSOL = rl.WSOL_MINT
FRN_HOUR = 10_000_000_000
S0_FINAL_SLOTS = 9_000  # membership is cached this many slots after the s0 window closes (late rows beyond it are a feed fault)  # r4b_layout frn = hour index * 1e10 + line ordinal

# Exploration replay only: the outcome blocks of /data/mal/hunt-1008/push-1016/hunt23/RULES.md. fresh-08* (the unspent reserve) is NOT here.
EXPLORATION_DIRS = (
    "/data/mal/clean-view/explore-0814",
    "/data/mal/clean-view/oracle-insample-2026-09-22_25",
    "/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00",
    "/data/mal/clean-view/oracle-live-2026-09-25_27",
)
SEALED_PATTERNS = ("/data/mal/blocks", "/data/mal/exp012-forward", "forward-", "truth-", "fresh-08", "fresh-09", "exp011-", "shadow", "canary", "probe",
                   "walk", "meta-capture", "/data/mal/arena", "tip-tape", "/var/lib/mal/sealed", "fwdv1", "/data/mal/r4b")
REPLAY_HOUR_MAX = "2026-10-01T00"  # no replay hour at or after this (October seals: D2/D3, EXP-024 Look-2, walk 2, arena tape counts-only)


class LiveRefused(Exception):
    pass


def hour_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H")


def hour_dt(h: str) -> datetime:
    return datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def hour_epoch(h: str) -> int:
    return int(hour_dt(h).timestamp())


def path_problem(p: str | os.PathLike) -> str | None:
    """None if `p` is under an exploration dir and matches no sealed pattern; else the reason. Resolved path (symlinks followed)."""
    rp = os.path.realpath(str(p))
    for s in SEALED_PATTERNS:
        if s in rp:
            return f"sealed pattern {s!r} in {rp}"
    if not any(rp == d or rp.startswith(d + "/") for d in EXPLORATION_DIRS):
        return f"{rp} is not an exploration block of hunt23/RULES.md"
    return None


# ------------------------------------------------------------------------------------------------------------------------------- hashing
class DuckHash:
    """DuckDB's hash() of a VARCHAR (r4b_layout writes th = hash(trader), ch = hash(creator)); hash(NULL) is a fixed non-null value. Cached."""

    def __init__(self) -> None:
        import duckdb

        self._con = duckdb.connect()
        self._cache: dict[str | None, int] = {}

    def __call__(self, s: str | None) -> int:
        v = self._cache.get(s)
        if v is None:
            v = int(self._con.execute("select hash(?::varchar)", [s]).fetchone()[0])
            self._cache[s] = v
        return v


def default_pda_fn() -> Callable[[str], str]:
    return rl.default_canonical()


# ------------------------------------------------------------------------------------------------------------------------------- per-mint state
_PRINT_FIELDS = (("slot", "q"), ("txi", "q"), ("evi", "q"), ("frn", "q"), ("bt", "d"), ("isbuy", "b"), ("sol", "d"), ("tok", "d"), ("q", "d"),
                 ("b", "d"), ("th", "Q"), ("vq", "d"))


def _nan(x: Any) -> float:
    return float("nan") if x is None else float(x)


@dataclass
class MintState:
    mint: str
    # complete rows: comp = min(slot), min(block_time)
    mslot: int | None = None
    mbt: int | None = None
    # migration rows: arg_min(pool / quote_mint, (slot, txi, evi, frn)), NULL args skipped (DuckDB arg_min)
    mev_pool: str | None = None
    mev_pool_key: tuple | None = None
    mev_quote: str | None = None
    mev_quote_key: tuple | None = None
    has_mig: bool = False
    # creates: arg_min(is_mayhem_mode, slot), any_value(hash(creator)) (first row), min(block_time), min(slot)
    has_create: bool = False
    mayhem: bool | None = None
    mayhem_slot: int | None = None
    ch: int | None = None
    cbt: int | None = None
    cslot: int | None = None
    # PDA-pool PumpSwap prints, arrival order
    prints: dict = field(default_factory=lambda: {k: array(t) for k, t in _PRINT_FIELDS})
    # bonding aggregates (the prep_work SQL over bond rows): count, buy / sell sums, per-trader sell sums (keys = distinct traders)
    b_n: int = 0
    b_buy: int = 0
    b_sell: int = 0
    b_any_sol: bool = False
    b_by_th: dict = field(default_factory=dict)
    b_maxslot: int | None = None
    last_bond_bt: int | None = None
    max_bt: float = float("-inf")  # latest block_time among the PDA prints (a necessary condition for a row: traded in the last 10 min)


def _sorted4(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> bool:
    """True iff rows are in non-decreasing lexicographic (a, b, c, d) order: each column is compared only on the adjacent pairs whose earlier
    columns are equal."""
    if len(a) < 2:
        return True
    pos = np.arange(len(a) - 1)
    for x in (a, b, c, d):
        dx = x[pos + 1] - x[pos]
        if (dx < 0).any():
            return False
        pos = pos[dx == 0]
        if not len(pos):
            return True
    return True


def features_at(prep: rf.Prep, r: rf.MintConst, ch: Any, slot, bt, isbuy, sol, tok, q, b, th, vq, T: int, sd: int) -> np.ndarray | None:
    """The row at the single decision T of tools/r4b_features.mint_features(..., grads_rule="asof_pda_first_print", mint_filters="asof"), computed
    for that T only (mint_features loops over the whole 2-min grid from mbt + 600 s, about 8 s a mark on a ramped universe; this is the live path).
    The prints are the member's, ordered like ut and already restricted to slot < sd (= prep.slot_at(T)); the caller has checked
    ceil120(mbt + 600) <= T <= mbt + 48 h. Every expression is mint_features' own, evaluated at j = n (all prints are before sd), so the float32 row
    is byte-equal (tests/test_r4b_live.py: every grid T of random mints; scripts/research/r4b_live_equiv.py: md5 against the pinned batch calls).
    Returns the (63,) float32 row, or None where mint_features has no row at T."""
    n = len(slot)
    if n < 1:
        return None
    V0 = float(r.v)
    mbt = int(r.mbt)
    sl = np.asarray(slot)
    if int(sl[-1]) >= sd:
        raise ValueError("features_at needs prints with slot < sd only")
    bt = np.asarray(bt).astype(float)
    ib = np.asarray(isbuy).astype(bool)
    so = np.asarray(sol).astype(float)
    tk = np.asarray(tok).astype(float)
    q = np.asarray(q).astype(float)
    bb = np.asarray(b).astype(float)
    th = np.asarray(th)
    vqa = np.asarray(vq).astype(float)
    bt = np.where(np.isfinite(bt), bt, -1)
    btm = np.maximum.accumulate(bt)
    j = n
    jl = n - 1
    # asof mint filter at this T (mint_features: j >= 3, no pre-trade state <= 0 up to jl, derived post-state of jl > 0)
    if j < 3:
        return None
    if (((q + vqa) <= 0) | (bb <= 0)).any():
        return None
    qpost = (q[jl] + so[jl] * (1 - 0.0125) if ib[jl] else q[jl] - so[jl] / (1 - 0.0125)) + vqa[jl]
    bpost = bb[jl] - tk[jl] if ib[jl] else bb[jl] + tk[jl]
    if qpost <= 0 or bpost <= 0:
        return None
    if not (btm[jl] >= T - 600 and btm[jl] < T):
        return None
    qp = np.where(ib, q + so * 0.99, q - so * 1.01) + vqa
    bp = np.where(ib, bb - tk, bb + tk)
    bp = np.where(bp > 0, bp, bb)
    pp = qp / bp
    seed_p = (rf.SEED_Q + V0) / rf.SEED_B
    with np.errstate(divide="ignore", invalid="ignore"):
        lpp = np.log(pp)
    lr = np.diff(np.concatenate([[math.log(seed_p)], lpp]))
    cs2 = np.concatenate([[0], np.cumsum(lr * lr)])
    cbv = np.concatenate([[0], np.cumsum(np.where(ib, so, 0))])
    csv_ = np.concatenate([[0], np.cumsum(np.where(ib, 0, so))])
    lg = so >= 1e9
    cln = np.concatenate([[0], np.cumsum(np.where(lg, np.where(ib, so, -so), 0))])
    cnl = np.concatenate([[0], np.cumsum(lg)])
    cmax = np.maximum.accumulate(lpp)
    cmin = np.minimum.accumulate(lpp)
    iath = np.maximum.accumulate(np.where(lpp >= cmax, np.arange(n), 0))
    ps = rf._prev_same(th)
    first = np.concatenate([[0], np.cumsum(ps < 0)])
    bidx = np.nonzero(ib)[0]
    sidx = np.nonzero(~ib)[0]
    pb = rf._prev_same(th[bidx])
    pS = rf._prev_same(th[sidx])
    cbt_nan = bool(np.isnan(r.cbt))
    ck = 1.0 if (ch is not None and not (isinstance(ch, float) and math.isnan(ch))) else 0.0
    isc = (th.astype(np.float64) == np.float64(ch)) if ck else np.zeros(n, bool)
    ccs = np.concatenate([[0], np.cumsum(np.where(isc & ~ib, so, 0))])
    ccb = np.concatenate([[0], np.cumsum(np.where(isc & ib, so, 0))])
    Ta = np.array([T], np.int64)
    ja = np.array([j])
    jla = np.array([jl])
    P = lpp[jla]
    F: dict[str, Any] = {}
    F["age_s"] = Ta - mbt
    F["t_since_create"] = (Ta - r.cbt) if not cbt_nan else np.full(1, np.nan)
    F["r_seed"] = P - math.log(seed_p)
    jw = {w: np.minimum(np.searchsorted(btm, Ta - w, "left"), ja) for w in (60, 300, 900, 3600, 14400)}
    for w in (60, 300, 900, 3600, 14400):
        k = jw[w]
        F[f"ret_{w}"] = P - np.where(k > 0, lpp[np.maximum(k - 1, 0)], math.log(seed_p))
    F["dd_ath"] = P - cmax[jla]
    F["rebound_atl"] = P - cmin[jla]
    F["t_since_ath"] = Ta - btm[iath[jla]]
    F["ath_seed"] = cmax[jla] - math.log(seed_p)
    for w in (300, 900, 3600):
        F[f"rv_{w}"] = cs2[ja] - cs2[jw[w]]
        F[f"np_{w}"] = ja - jw[w]
        F[f"bv_{w}"] = (cbv[ja] - cbv[jw[w]]) / 1e9
        F[f"sv_{w}"] = (csv_[ja] - csv_[jw[w]]) / 1e9
    F["np_all"] = ja.astype(float)
    liq = qp[jla] / 1e9
    with np.errstate(divide="ignore", invalid="ignore"):
        F["nf_900_liq"] = (F["bv_900"] - F["sv_900"]) / liq
        F["nf_3600_liq"] = (F["bv_3600"] - F["sv_3600"]) / liq
    F["bv_all"] = cbv[ja] / 1e9
    F["sv_all"] = csv_[ja] / 1e9
    F["lnet_900"] = (cln[ja] - cln[jw[900]]) / 1e9
    F["lnet_3600"] = (cln[ja] - cln[jw[3600]]) / 1e9
    F["nlarge_3600"] = cnl[ja] - cnl[jw[3600]]
    for w in (900, 3600):
        lo = int(jw[w][0])
        blo, bhi = int(np.searchsorted(bidx, lo)), int(np.searchsorted(bidx, j))
        slo, shi = int(np.searchsorted(sidx, lo)), int(np.searchsorted(sidx, j))
        ub = np.zeros(1)
        us = np.zeros(1)
        if bhi > blo:
            ub[0] = (pb[blo:bhi] < blo).sum()
        if shi > slo:
            us[0] = (pS[slo:shi] < slo).sum()
        F[f"ub_{w}"] = ub
        F[f"us_{w}"] = us
    F["uniq_all"] = first[ja]
    with np.errstate(divide="ignore", invalid="ignore"):
        F["buyfrac_900"] = np.where(F["np_900"] > 0, (np.searchsorted(bidx, ja) - np.searchsorted(bidx, jw[900])) / np.maximum(F["np_900"], 1), np.nan)
        F["avgsz_900"] = np.where(F["np_900"] > 0, (F["bv_900"] + F["sv_900"]) / np.maximum(F["np_900"], 1), np.nan)
    F["liq_sol"] = liq
    F["fee_tier"] = rf.fee_ppm(qp[jla], bp[jla])
    F["t_since_last"] = Ta - btm[jla]
    F["cre_sell_post"] = ccs[ja] / 1e9
    F["cre_buy_post"] = ccb[ja] / 1e9
    F["cre_known"] = np.full(1, ck)
    hr = (Ta % 86400) / 3600.0
    F["hour_sin"] = np.sin(2 * np.pi * hr / 24)
    F["hour_cos"] = np.cos(2 * np.pi * hr / 24)
    g = prep.grads_asof(Ta, np.array([sd], np.int64))
    F["m_grads_1h"], F["m_grads_10m"] = g[0], g[1]
    F["m_vps_10m"] = prep.mwin("v_ps", Ta, 600) / 1e9
    F["m_nps_10m"] = prep.mwin("n_ps", Ta, 600)
    F["m_vbd_10m"] = prep.mwin("v_bd", Ta, 600) / 1e9
    F["m_ncr_10m"] = prep.mwin("n_cr", Ta, 600)
    F["m_ncr_60m"] = prep.mwin("n_cr", Ta, 3600)
    F["m_vps_60m"] = prep.mwin("v_ps", Ta, 3600) / 1e9
    nan = np.nan
    F["c_bond_dur"] = np.full(1, (mbt - r.cbt) if not cbt_nan else nan)
    F["c_nbond"] = np.full(1, r.nb if not np.isnan(r.nb) else nan)
    F["c_nbond_tr"] = np.full(1, r.ntr if not np.isnan(r.ntr) else nan)
    F["c_bond_buy"] = np.full(1, (r.bbuy or 0) / 1e9)
    F["c_bond_sell"] = np.full(1, (r.bsell or 0) / 1e9)
    F["c_cre_bond_sell"] = np.full(1, (r.cre_sell if not np.isnan(r.cre_sell) else nan) / 1e9)
    F["c_pool_delay"] = np.full(1, float(r.s0 - r.mslot))
    F["c_v"] = np.full(1, V0 / 1e9)
    return np.column_stack([np.asarray(F[k], float) for k in rf.FEATS]).astype(np.float32)[0]


@dataclass
class Member:
    mint: str
    pool: str
    mslot: int
    mbt: int
    s0: int
    v0: int
    st: MintState


# ------------------------------------------------------------------------------------------------------------------------------- the book
class LiveBook:
    """tools/r4b_book.xs_book, one mark at a time. step(t, ranked) takes the mints of every finite-score row at mark t in rank order
    (descending score, ties to the lower mid) and returns the actions of that mark: ('exit', mint, T0, T1, reason) then ('entry', mint, T0, rank).
    A mark with no rows is not a group (xs_book): with positions held and t == last mark + rb, live closes them now with reason 'gap' at T1 = t,
    which is the T1 xs_book gives them at the next non-empty mark."""

    def __init__(self, k: int = FZ.K, rb: int = MARK_S) -> None:
        self.k, self.rb = int(k), int(rb)
        self.pos: dict[str, int] = {}  # mint -> T0, insertion order
        self.prev: int | None = None

    def step(self, t: int, ranked: Sequence[str]) -> list[tuple]:
        t = int(t)
        out: list[tuple] = []
        if not ranked:
            if self.pos and self.prev is not None and t - self.prev >= self.rb:
                for m, t0 in self.pos.items():
                    out.append(("exit", m, t0, self.prev + self.rb, "gap"))
                self.pos = {}
            return out
        if self.prev is not None and t - self.prev > self.rb:
            for m, t0 in self.pos.items():
                out.append(("exit", m, t0, self.prev + self.rb, "gap"))
            self.pos = {}
        self.prev = t
        top = list(ranked[: self.k])
        keep = set(top)
        sold = set()
        for m in list(self.pos):
            if m not in keep:
                out.append(("exit", m, self.pos.pop(m), t, "rank"))
                sold.add(m)
        for r, m in enumerate(top):
            if m not in self.pos and m not in sold:
                self.pos[m] = t
                out.append(("entry", m, t, r))
        return out

    def close_all(self, reason: str = "end") -> list[tuple]:
        """xs_book's end: everything still held closes at last mark + rb."""
        if self.prev is None:
            return []
        out = [("exit", m, t0, self.prev + self.rb, reason) for m, t0 in self.pos.items()]
        self.pos = {}
        return out


def trades_from_actions(actions: Iterable[tuple]) -> list[tuple[str, int, int, str]]:
    """(mint, T0, T1, reason) per closed trade, sorted by (T0, mint)."""
    out = [(a[1], int(a[2]), int(a[3]), a[4]) for a in actions if a[0] == "exit"]
    return sorted(out, key=lambda x: (x[1], x[0]))


def trades_md5(trades: Sequence[tuple[str, int, int, str]]) -> str:
    h = hashlib.md5()
    for m, t0, t1, r in sorted(trades, key=lambda x: (x[1], x[0])):
        h.update(f"{m},{t0},{t1},{r}\n".encode())
    return h.hexdigest()


def rows_md5(mints: Sequence[str], T: np.ndarray, sd: np.ndarray, X: np.ndarray, score: np.ndarray) -> str:
    """md5 of the scored decision rows, ordered by (T, mint): mint, T, sd, the 63 float32 features' bytes and the float64 score's bytes."""
    o = sorted(range(len(mints)), key=lambda i: (int(T[i]), mints[i]))
    h = hashlib.md5()
    X = np.ascontiguousarray(np.asarray(X, np.float32))
    sc = np.ascontiguousarray(np.asarray(score, np.float64))
    for i in o:
        h.update(f"{mints[i]},{int(T[i])},{int(sd[i])},".encode())
        h.update(X[i].tobytes())
        h.update(sc[i].tobytes())
        h.update(b"\n")
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------------------------------------- the engine
@dataclass
class MarkResult:
    t: int
    sd: int | None
    mints: list
    T: np.ndarray
    sdv: np.ndarray
    X: np.ndarray
    score: np.ndarray
    ranked: list
    actions: list
    d3_dropped: int = 0
    seconds: float = 0.0


class Engine:
    """Streaming r4-b decision engine. mode "dry" (default) or "replay"; "live" is refused (LIVE_BUILT is False).

    v_source "event": V0 = the event V of the s0 print and every member print's own V, pda_match required (the forward read's mode);
    "static_map": V from a {pool: V} map for every print of the mint, no pda_match (exploration replay of tapes without event V, as E-d).
    `bond_mints`: if given, bonding aggregates are kept for these mints only (a replay may pass the set of mints that complete in its window; for
    decisions this equals keeping all, it only saves memory). `universe_min_mbt`: members graduate at or after it (EXP-027: r4b_count.UNIVERSE_MIN_MBT)."""

    def __init__(self, *, universe_min_mbt: int, v_source: str = "event", static_map: Mapping[str, int | None] | None = None, mode: str = "dry",
                 pda_fn: Callable[[str], str] | None = None, hash_fn: Callable[[str | None], int] | None = None, boosters: list | None = None,
                 score_fn: Callable[[list, np.ndarray], np.ndarray] | None = None, bond_mints: set | None = None, k: int = FZ.K,
                 exact_grid: bool = False) -> None:
        if mode == "live" or LIVE_BUILT is not False:
            raise LiveRefused("r4b_live builds no live mode (LIVE_BUILT is False): no key, no wallet, no send")
        if mode not in ("dry", "replay"):
            raise LiveRefused(f"unknown mode {mode!r}")
        if v_source not in rl.V_SOURCES:
            raise LiveRefused(f"v_source must be one of {rl.V_SOURCES}")
        if v_source == "static_map" and static_map is None:
            raise LiveRefused("v_source static_map needs the {pool: V} map")
        if v_source == "event" and static_map is not None:
            raise LiveRefused("a static map was given with v_source event; refusing to mix the V sources")
        self.mode, self.v_source, self.static_map = mode, v_source, static_map
        self.umin = int(universe_min_mbt)
        self._pda_fn = pda_fn
        self._pda: dict[str, str | None] = {}
        self._hash = hash_fn
        self.boosters = boosters
        self.score_fn = score_fn
        self.bond_mints = bond_mints
        self.mints: dict[str, MintState] = {}
        self.clock: dict[int, int] = {}  # block_time second -> min slot (all trade rows, both venues)
        self.mkt: dict[int, list] = {}  # minute -> [n_ps, v_ps, n_bd, v_bd]
        self.ncr: dict[int, int] = {}  # minute -> creates
        self.book = LiveBook(k=k)
        self.exact_grid = bool(exact_grid)
        self.counters: dict[str, int] = {}
        self._frn = 0
        self.max_slot = -1  # highest slot ingested (any trade row)
        self._member_final: dict[str, Member | None] = {}  # membership once s0 can no longer change

    # -- helpers
    def _count(self, k: str, n: int = 1) -> None:
        self.counters[k] = self.counters.get(k, 0) + n

    def pda(self, mint: str) -> str | None:
        if mint not in self._pda:
            if self._pda_fn is None:
                self._pda_fn = default_pda_fn()
            try:
                self._pda[mint] = str(self._pda_fn(mint))
            except Exception:  # noqa: BLE001 - r4b_layout: a PDA error leaves the mint without a pool (never a member)
                self._pda[mint] = None
                self._count("pda_errors")
        return self._pda[mint]

    def th(self, s: str | None) -> int:
        if self._hash is None:
            self._hash = DuckHash()
        return self._hash(s)

    def _st(self, mint: str) -> MintState:
        st = self.mints.get(mint)
        if st is None:
            st = self.mints[mint] = MintState(mint)
        return st

    def next_frn(self) -> int:
        self._frn += 1
        return self._frn

    # -- ingest: migrations and creates
    def ingest_migration(self, row: Mapping[str, Any], frn: int | None = None) -> None:
        mint, typ = row.get("mint"), row.get("type")
        if mint is None:
            return
        slot, bt = row.get("slot"), row.get("block_time")
        if typ == "complete":
            st = self._st(mint)
            if slot is not None:
                st.mslot = int(slot) if st.mslot is None else min(st.mslot, int(slot))
            if bt is not None:
                st.mbt = int(bt) if st.mbt is None else min(st.mbt, int(bt))
        elif typ == "migration":
            st = self._st(mint)
            st.has_mig = True
            key = (slot if slot is not None else math.inf, -1 if row.get("tx_index") is None else int(row["tx_index"]),
                   -1 if row.get("event_index") is None else int(row["event_index"]), self.next_frn() if frn is None else int(frn))
            if row.get("pool") is not None and (st.mev_pool_key is None or key < st.mev_pool_key):
                st.mev_pool, st.mev_pool_key = str(row["pool"]), key
            if row.get("quote_mint") is not None and (st.mev_quote_key is None or key < st.mev_quote_key):
                st.mev_quote, st.mev_quote_key = str(row["quote_mint"]), key

    def ingest_create(self, row: Mapping[str, Any]) -> None:
        if (row.get("type") if row.get("type") is not None else "create") != "create":
            return
        slot, bt = row.get("slot"), row.get("block_time")
        if bt is not None:  # mkt_creates counts every create row with a block_time (no mint condition)
            mnt = (int(bt) // 60) * 60
            self.ncr[mnt] = self.ncr.get(mnt, 0) + 1
        if row.get("mint") is None:
            return
        st = self._st(row["mint"])
        if not st.has_create:
            st.ch = self.th(row.get("creator"))  # any_value(hash(creator)): hash(NULL) is non-null
        st.has_create = True
        mm = row.get("is_mayhem_mode")
        if mm is not None and slot is not None and (st.mayhem_slot is None or int(slot) < st.mayhem_slot):
            st.mayhem, st.mayhem_slot = bool(mm), int(slot)
        if bt is not None:
            st.cbt = int(bt) if st.cbt is None else min(st.cbt, int(bt))
        if slot is not None:
            st.cslot = int(slot) if st.cslot is None else min(st.cslot, int(slot))

    # -- ingest: trades
    def _clock_mkt(self, venue: Any, slot: Any, bt: Any, sol: Any) -> None:
        if slot is not None and int(slot) > self.max_slot:
            self.max_slot = int(slot)
        if bt is None:
            return
        bt = int(bt)
        if slot is not None:
            c = self.clock.get(bt)
            if c is None or int(slot) < c:
                self.clock[bt] = int(slot)
        a = self.mkt.get((bt // 60) * 60)
        if a is None:
            a = self.mkt[(bt // 60) * 60] = [0.0, 0.0, 0.0, 0.0]
        if venue == "pumpswap":
            a[0] += 1.0
            if sol is not None:
                a[1] += float(sol)
        elif venue == "pump_bonding":
            a[2] += 1.0
            if sol is not None:
                a[3] += float(sol)

    def _mint_trade(self, venue: Any, mint: Any, pool: Any, slot: Any, txi: Any, evi: Any, frn: int, bt: Any, isbuy: Any, sol: Any, tok: Any,
                    q: Any, b: Any, th: int, vq: Any) -> None:
        if mint is None or slot is None:
            return
        if venue == "pumpswap":
            st = self.mints.get(mint)
            if st is None or st.mslot is None:  # only mints whose complete row is in (a PDA pool cannot print before its migration)
                return
            if pool is None or pool != self.pda(mint):
                return
            p = st.prints
            p["slot"].append(int(slot))
            p["txi"].append(-1 if txi is None else int(txi))
            p["evi"].append(-1 if evi is None else int(evi))
            p["frn"].append(int(frn))
            p["bt"].append(_nan(bt))
            if bt is not None and bt > st.max_bt:
                st.max_bt = float(bt)
            p["isbuy"].append(1 if isbuy else 0)
            p["sol"].append(_nan(sol))
            p["tok"].append(_nan(tok))
            p["q"].append(_nan(q))
            p["b"].append(_nan(b))
            p["th"].append(int(th))
            p["vq"].append(_nan(vq))
        elif venue == "pump_bonding":
            if self.bond_mints is not None and mint not in self.bond_mints:
                return
            st = self._st(mint)
            st.b_n += 1
            s = None if sol is None else int(sol)
            if s is not None:
                st.b_any_sol = True
                if isbuy is True:
                    st.b_buy += s
                elif isbuy is False:
                    st.b_sell += s
            prev = st.b_by_th.get(th)
            add = s if (s is not None and isbuy is False) else 0
            st.b_by_th[th] = add if prev is None else prev + add
            st.b_maxslot = int(slot) if st.b_maxslot is None else max(st.b_maxslot, int(slot))
            if bt is not None:
                st.last_bond_bt = int(bt)

    def ingest_trade(self, row: Mapping[str, Any], frn: int | None = None) -> None:
        """One walker-schema trade row, as it arrives (the live API)."""
        side = row.get("side")
        isbuy = None if side is None else (side == "buy")
        tok, q, b = row.get("token_raw"), row.get("quote_reserve"), row.get("base_reserve")
        self._clock_mkt(row.get("venue"), row.get("slot"), row.get("block_time"), row.get("sol_lamports"))
        self._mint_trade(row.get("venue"), row.get("mint"), row.get("pool"), row.get("slot"), row.get("tx_index"), row.get("event_index"),
                         self.next_frn() if frn is None else int(frn), row.get("block_time"), isbuy, row.get("sol_lamports"), tok, q, b,
                         self.th(row.get("trader")), row.get("virtual_quote_reserves"))

    def ingest_trade_block(self, cols: Mapping[str, np.ndarray]) -> None:
        """A block of trade rows in file order (replay): the clock / market aggregates vectorized, then the per-mint path row by row for the rows
        that can matter (PDA prints of completed mints, bonding prints of tracked mints). Same state as ingest_trade on each row (tested).
        cols: venue, mint, pool (object), slot, txi, evi, frn (int64; txi/evi -1 for null), bt, sol, tok, q, b, vq (float64, NaN = null),
        isbuy (object: True/False/None), th (uint64)."""
        venue = cols["venue"]
        n = len(venue)
        if n == 0:
            return
        bt, slot, sol = cols["bt"], cols["slot"], cols["sol"]
        if len(slot):
            self.max_slot = max(self.max_slot, int(slot.max()))
        okb = np.isfinite(bt)
        if okb.any():
            btb, slb = bt[okb].astype(np.int64), slot[okb].astype(np.int64)
            o = np.lexsort((slb, btb))
            ub, first = np.unique(btb[o], return_index=True)
            for s_, v_ in zip(ub.tolist(), slb[o][first].tolist()):
                c = self.clock.get(s_)
                if c is None or v_ < c:
                    self.clock[s_] = v_
            mn = (btb // 60) * 60
            ven = venue[okb]
            ps = ven == "pumpswap"
            bd = ven == "pump_bonding"
            so = np.nan_to_num(sol[okb], nan=0.0)
            um, inv = np.unique(mn, return_inverse=True)
            agg = np.zeros((len(um), 4))
            np.add.at(agg[:, 0], inv, ps.astype(float))
            np.add.at(agg[:, 1], inv, np.where(ps, so, 0.0))
            np.add.at(agg[:, 2], inv, bd.astype(float))
            np.add.at(agg[:, 3], inv, np.where(bd, so, 0.0))
            for m_, r_ in zip(um.tolist(), agg.tolist()):
                a = self.mkt.get(m_)
                if a is None:
                    self.mkt[m_] = r_
                else:
                    for i in range(4):
                        a[i] += r_[i]
        mint, pool = cols["mint"], cols["pool"]
        mints, bm = self.mints, self.bond_mints
        vl, ml = venue.tolist(), mint.tolist()
        sel = [i for i in range(n) if (vl[i] == "pumpswap" and ml[i] in mints and mints[ml[i]].mslot is not None)
               or (vl[i] == "pump_bonding" and ml[i] is not None and (bm is None or ml[i] in bm))]
        if not sel:
            return
        ix = np.asarray(sel, np.int64)
        L = {k: cols[k][ix].tolist() for k in ("pool", "slot", "txi", "evi", "frn", "bt", "sol", "tok", "q", "b", "vq", "isbuy", "th")}
        nn = lambda x: None if x != x else x  # noqa: E731  (NaN -> None)
        for j, i in enumerate(sel):
            tx, ev = L["txi"][j], L["evi"][j]
            self._mint_trade(vl[i], ml[i], L["pool"][j], int(L["slot"][j]), None if tx < 0 else int(tx), None if ev < 0 else int(ev), int(L["frn"][j]),
                             nn(L["bt"][j]), L["isbuy"][j], nn(L["sol"][j]), nn(L["tok"][j]), nn(L["q"][j]), nn(L["b"][j]), int(L["th"][j]), nn(L["vq"][j]))

    # -- membership (r4b_layout.build's m table, as of now)
    def member(self, mint: str) -> Member | None:
        st = self.mints.get(mint)
        if st is None or st.mslot is None or st.mbt is None:
            return None
        if st.mayhem is True:
            return None
        pda = self.pda(mint)
        if pda is None:
            return None
        p = st.prints
        if not len(p["slot"]):
            return None
        sl = np.frombuffer(p["slot"], np.int64)
        w = np.flatnonzero((sl >= st.mslot) & (sl < st.mslot + WINDOW_SLOTS))
        if not len(w):
            return None
        tx, ev, fr = (np.frombuffer(p[k], np.int64)[w] for k in ("txi", "evi", "frn"))
        i0 = w[np.lexsort((fr, ev, tx, sl[w]))[0]]
        s0 = int(sl[i0])
        if self.v_source == "static_map":
            v0 = self.static_map.get(pda) if self.static_map is not None else None  # type: ignore[union-attr]
        else:
            v = p["vq"][i0]
            v0 = None if math.isnan(v) else int(v)
            if not (st.mev_pool == pda and st.mev_quote == WSOL):
                return None
        if v0 is None or not (V_LO <= v0 <= V_HI):
            return None
        return Member(mint, pda, int(st.mslot), int(st.mbt), s0, int(v0), st)

    def members(self, T: int | None = None) -> list[Member]:
        """Members graduated at or after universe_min_mbt; with T, only those that can matter at mark T (mbt >= T - 48 h - 2 h: a decision row needs
        mbt + 48 h >= T, and m_grads counts graduations of the last hour). Membership is cached once no print can still enter the s0 window
        (max ingested slot >= mslot + 7300 + S0_FINAL_SLOTS); before that it is recomputed at every call."""
        out = []
        for mint, st in self.mints.items():
            if st.mslot is None or st.mbt is None or st.mbt < self.umin or (T is not None and st.mbt < T - GRID_HI_S - 2 * MARK_S):
                continue
            if mint in self._member_final:
                m = self._member_final[mint]
            else:
                m = self.member(mint)
                if self.max_slot >= st.mslot + WINDOW_SLOTS + S0_FINAL_SLOTS:
                    self._member_final[mint] = m
            if m is not None:
                out.append(m)
        out.sort(key=lambda m: (m.mbt, m.mint))
        return out

    # -- prep (02a): clock, dense market minutes, graduations
    def prep(self, members: Sequence[Member]) -> rf.Prep:
        if not self.clock or not self.mkt:
            raise LiveRefused("no clock or market rows yet")
        cb = np.array(sorted(self.clock), np.int64)
        cs = np.array([self.clock[s] for s in cb.tolist()], np.int64)
        mnt = np.array(sorted(self.mkt), np.float64)
        rows = [self.mkt[int(m)] for m in mnt]
        cols = {k: np.array([r[i] for r in rows], np.float64) for i, k in enumerate(("n_ps", "v_ps", "n_bd", "v_bd"))}
        cols["n_cr"] = np.array([float(self.ncr.get(int(m), 0)) for m in mnt], np.float64)
        gr = np.array([m.mbt for m in members], np.int64)
        gfs = np.array([m.s0 for m in members], np.float64)
        return rf.build_prep(cb, cs, mnt, cols, gr, gfs)

    def mint_const(self, m: Member, mid: int) -> tuple[rf.MintConst, Any]:
        st = m.st
        if st.b_n:
            nb, ntr = float(st.b_n), float(len(st.b_by_th))
            bbuy, bsell = float(st.b_buy), float(st.b_sell)
            cre = float(st.b_by_th.get(st.ch, 0)) if st.ch is not None else 0.0
            if st.b_maxslot is not None and st.b_maxslot > st.mslot:
                self._count("bond_rows_after_mslot_mints")
        else:
            nb = ntr = bbuy = bsell = cre = float("nan")
        c = rf.MintConst(mid=mid, mbt=m.mbt, mslot=m.mslot, s0=m.s0, v=float(m.v0), cbt=_nan(st.cbt), has_creator=st.ch is not None, nb=nb, ntr=ntr,
                         bbuy=bbuy, bsell=bsell, cre_sell=cre)
        return c, st.ch

    def mint_rows(self, m: Member, mid: int, prep: rf.Prep, T: int, sd: int) -> tuple[np.ndarray, int, float] | None:
        """(X row float32, sd, s0 block_time) of member m at mark T, or None. Prints: slot >= s0, block_time null or <= mbt + HORIZON_S, ordered
        (slot, txi, evi, frn) like ut, truncated to slot < sd(T) (the only prints the row at T reads)."""
        p = m.st.prints
        sl = np.frombuffer(p["slot"], np.int64)
        bt = np.frombuffer(p["bt"], np.float64)
        keep = (sl >= m.s0) & (np.isnan(bt) | (bt <= m.mbt + HORIZON_S))
        idx = np.flatnonzero(keep)
        if not len(idx):
            return None
        tx, ev, fr = (np.frombuffer(p[k], np.int64)[idx] for k in ("txi", "evi", "frn"))
        if not _sorted4(sl[idx], tx, ev, fr):  # prints mostly arrive in order: the O(n) check skips the sort (same result: frn makes the key total)
            idx = idx[np.lexsort((fr, ev, tx, sl[idx]))]
        bt_all = bt[idx]
        s0bt = float(bt_all[0]) if np.isfinite(bt_all[0]) else (float(bt_all[np.isfinite(bt_all)][0]) if np.isfinite(bt_all).any() else float(m.mbt))
        idx = idx[sl[idx] < sd]
        if not len(idx):
            return None
        const, ch = self.mint_const(m, mid)
        vq = (np.frombuffer(p["vq"], np.float64)[idx] if self.v_source == "event" else np.full(len(idx), float(m.v0)))
        if self.v_source == "event" and np.isnan(vq).any():
            self._count("member_null_v_prints")  # r4b_layout refuses the whole build on this; live skips the mint and counts it
            return None
        args = (sl[idx], bt[idx], np.frombuffer(p["isbuy"], np.int8)[idx].astype(bool), np.frombuffer(p["sol"], np.float64)[idx],
                np.frombuffer(p["tok"], np.float64)[idx], np.frombuffer(p["q"], np.float64)[idx], np.frombuffer(p["b"], np.float64)[idx],
                np.frombuffer(p["th"], np.uint64)[idx])
        if self.exact_grid:  # the pinned per-mint loop over the whole 2-min grid up to T (slow; the reference the fast path is tested against)
            res = rf.mint_features(prep, const, ch, *args, vq=vq, seg_end=T + rf.MAXH + rf.MARGIN, grads_rule="asof_pda_first_print", mint_filters="asof")
            if res is None:
                return None
            hit = np.flatnonzero(res.I[:, 1] == T)
            if not len(hit):
                return None
            return res.X[hit[0]], int(res.I[hit[0], 2]), s0bt
        x = features_at(prep, const, ch, *args, vq, T, sd)
        return None if x is None else (x, sd, s0bt)

    def score(self, X: np.ndarray) -> np.ndarray:
        if self.score_fn is not None:
            return np.asarray(self.score_fn(self.boosters, X), np.float64)
        from tools import r4b_score as rs

        if self.boosters is None:
            self.boosters = rs.load_boosters()
        return rs.bag_score(self.boosters, X)

    def decide(self, T: int, w0: int | None = None, last_entry: int | None = None) -> MarkResult:
        """The decision at mark T (T % 3600 == 0). w0 / last_entry bound the counted marks like the read (rows outside give an empty mark)."""
        t_start = time.monotonic()
        T = int(T)
        if T % MARK_S:
            raise LiveRefused(f"{T} is not an hour mark")
        mem = self.members(T)
        prep = self.prep(mem)
        sd = int(prep.slot_at(np.array([T], np.int64))[0])
        in_window = (w0 is None or T >= w0) and (last_entry is None or T <= last_entry)
        mints, Xs, sds = [], [], []
        mid_key = {}
        d3 = 0
        if in_window:
            for mid, m in enumerate(mem):
                t_lo = ((m.mbt + GRID_LO_S + 119) // 120) * 120
                if T < t_lo or T > m.mbt + GRID_HI_S:
                    continue
                if m.st.max_bt < T - 600:  # necessary for a row (mint_features: the last print before sd is within 600 s of T)
                    continue
                r = self.mint_rows(m, mid, prep, T, sd)
                if r is None:
                    continue
                x, sd_r, s0bt = r
                if T < s0bt + D3_DROP_S:
                    d3 += 1
                    continue
                mints.append(m.mint)
                Xs.append(x)
                sds.append(sd_r)
                mid_key[m.mint] = (m.mbt, m.mint)
        X = np.vstack(Xs).astype(np.float32) if Xs else np.zeros((0, FZ.N_FEATURES), np.float32)
        sc = self.score(X) if len(X) else np.zeros(0)
        fin = [i for i in range(len(mints)) if np.isfinite(sc[i])]
        ranked = [mints[i] for i in sorted(fin, key=lambda i: (-sc[i], mid_key[mints[i]]))]
        actions = self.book.step(T, ranked)
        return MarkResult(T, sd, mints, np.full(len(mints), T, np.int64), np.array(sds, np.int64), X, sc, ranked, actions, d3,
                          time.monotonic() - t_start)

    def prune(self, now_bt: int) -> int:
        """Drop per-mint print state that no decision can read again (graduated more than 48 h + 2 h ago) and bonding state of mints idle for 7 days
        that never completed. Graduation times stay (m_grads windows). Returns the number of mints pruned."""
        n = 0
        for mint, st in list(self.mints.items()):
            if st.mbt is not None and now_bt > st.mbt + GRID_HI_S + 2 * MARK_S and len(st.prints["slot"]):
                st.prints = {k: array(t) for k, t in _PRINT_FIELDS}
                n += 1
            elif st.mslot is None and st.last_bond_bt is not None and now_bt > st.last_bond_bt + 7 * 86400:
                del self.mints[mint]
                n += 1
        return n


# ------------------------------------------------------------------------------------------------------------------------------- decision log
def log_actions(log: Any, res: MarkResult, members: Mapping[str, Member] | None, ms: int, stake_sol: float = FZ.STAKE_SOL) -> list[dict]:
    """Append one mark's actions as arena_declog rows (exits first, then entries in rank order). order_id = '<mint>:<T0>'."""
    rows = []
    for a in res.actions:
        if a[0] == "exit":
            rows.append(log.append(BOT_ID, ms, {"kind": "exit", "order_id": f"{a[1]}:{a[2]}", "mint": a[1], "reason": a[4], "t_mark": int(a[3])}))
    sc = {m: float(s) for m, s in zip(res.mints, res.score.tolist())}
    for a in res.actions:
        if a[0] == "entry":
            m = members.get(a[1]) if members else None
            rows.append(log.append(BOT_ID, ms, {"kind": "entry", "order_id": f"{a[1]}:{a[2]}", "pool": m.pool if m else None,
                                                "meta": {"mint": a[1], "v0": m.v0 if m else None, "mbt": m.mbt if m else None, "s0": m.s0 if m else None},
                                                "stake_sol": stake_sol, "decision_slot": res.sd, "t_mark": int(a[2]), "rank": int(a[3]), "score": sc.get(a[1])}))
    return rows


# ------------------------------------------------------------------------------------------------------------------------------- exploration replay
TRADE_SQL_COLS = {k: v for k, v in rl.TRADE_COLS.items()}


def _read_trades(con: Any, path: Path, hix: int) -> dict[str, np.ndarray]:
    src = rl._read_json_sql(path, TRADE_SQL_COLS)
    q = f"""select venue, mint, pool, slot, coalesce(tx_index,-1)::bigint txi, coalesce(event_index,-1)::bigint evi,
            {hix} * {FRN_HOUR} + (row_number() over () - 1) as frn, block_time::double bt, sol_lamports::double sol, token_raw::double tok,
            quote_reserve::double q, base_reserve::double b, virtual_quote_reserves::double vq, side, hash(trader) th from {src}"""
    d = con.sql(q).fetchnumpy()
    out = {}
    for k, a in d.items():
        if k in ("bt", "sol", "tok", "q", "b", "vq"):
            a = np.ma.filled(a.astype(float), np.nan) if np.ma.isMaskedArray(a) else np.asarray(a, float)
        elif np.ma.isMaskedArray(a):
            a = np.ma.filled(a.astype(object), None) if a.dtype == object else np.ma.filled(a, -1)
        out[k] = np.asarray(a)
    side = out.pop("side")
    out["isbuy"] = np.array([None if s is None else (s == "buy") for s in side.tolist()], dtype=object)
    out["th"] = out["th"].astype(np.uint64)
    for k in ("slot", "txi", "evi", "frn"):
        out[k] = out[k].astype(np.int64)
    return out


def _read_rows(con: Any, path: Path, cols: Mapping[str, str]) -> list[dict]:
    src = rl._read_json_sql(path, cols)
    names = list(cols)
    return [dict(zip(names, r)) for r in con.sql(f"select {', '.join(names)} from {src}").fetchall()]


@dataclass
class ReplayResult:
    marks: list
    trades: list
    rows_md5: str
    trades_md5: str
    n_rows: int
    counters: dict
    seconds: list
    log_md5: str | None = None


def replay_walker(walk_dir: Path | Sequence[Path], h0: str, h1: str, w0: int, e: int, *, static_map: Mapping[str, int | None] | None, universe_min_mbt: int,
                  boosters: list | None = None, score_fn: Callable | None = None, pda_fn: Callable | None = None, log_root: Path | None = None,
                  extra_hours: int = 2, allow_dir: bool = False, memory_limit: str = "4GB", progress: Callable[[str], None] | None = None) -> ReplayResult:
    """Feed a walker-layout dir hour by hour through the Engine and decide every mark in [w0, e - 1 h], as the read does (EXP-027 section 4.1).
    Migrations and creates of [h0, h1) and trades of [h0, h1 + extra_hours) are fed, as r4b_layout.build loads them. Mark T is decided once the
    files of hour(T) + 1 are in (every row with block_time before T and the first second at or after T are then in; features read slot < sd(T) only).
    static_map given -> v_source static_map; None -> event. Refuses a non-exploration path or any hour at or after REPLAY_HOUR_MAX unless allow_dir
    (synthetic test dirs only). walk_dir may be a list of window dirs (e.g. explore-0814 w7, w6, w5): each hour file is taken from the first dir
    that has it."""
    import duckdb

    dirs = [Path(walk_dir)] if isinstance(walk_dir, (str, os.PathLike)) else [Path(d) for d in walk_dir]
    if not dirs:
        raise LiveRefused("no walk dir")
    if not allow_dir:
        for d in dirs:
            why = path_problem(d)
            if why:
                raise LiveRefused(f"replay refused: {why}")
    hours = rl.hour_range(h0, h1)
    trade_hours = hours + [hour_key(hour_dt(h1) + timedelta(hours=i)) for i in range(extra_hours)]
    if any(h >= REPLAY_HOUR_MAX for h in trade_hours):
        raise LiveRefused(f"replay refused: an hour at or after {REPLAY_HOUR_MAX} (October seals)")
    if not (hour_epoch(h0) <= w0 < e <= hour_epoch(h1)) or w0 % MARK_S or e % MARK_S:
        raise LiveRefused("need h0 <= w0 < e <= h1 on hour marks")
    con = duckdb.connect()
    con.sql(f"SET memory_limit='{memory_limit}'; SET threads=4; SET enable_progress_bar=false; SET preserve_insertion_order=true")

    def f(sub: str, h: str) -> Path:
        for d in dirs:
            p = rl._hour_file(d / sub, sub, h)
            if p is not None:
                return p
        raise LiveRefused(f"hour {h}: no {sub} file")

    # pre-scan: mints completing in [h0, h1) (bonding aggregates only for them; equal for decisions, saves memory)
    bond_mints: set = set()
    for h in hours:
        bond_mints |= {r["mint"] for r in _read_rows(con, f("migrations", h), {"type": "VARCHAR", "mint": "VARCHAR"}) if r["type"] == "complete" and r["mint"]}
    eng = Engine(universe_min_mbt=universe_min_mbt, v_source="static_map" if static_map is not None else "event", static_map=static_map, mode="replay",
                 pda_fn=pda_fn, hash_fn=lambda s: int(con.execute("select hash(?::varchar)", [s]).fetchone()[0]), boosters=boosters, score_fn=score_fn,
                 bond_mints=bond_mints)
    log = None
    if log_root is not None:
        from tools.arena_declog import DecisionLog

        log = DecisionLog(log_root)
        log.append(BOT_ID, w0 * 1000, {"kind": "start", "book": BOOK, "exp": "EXP-027", "mode": "replay", "schema": SCHEMA})
    marks = list(range(w0, e, MARK_S))  # entry marks [W0, E - 1 h]
    last_entry = e - MARK_S
    pending = list(marks)
    results: list[MarkResult] = []
    actions: list[tuple] = []
    for hix, h in enumerate(trade_hours):
        if h < h1:
            for i, r in enumerate(_read_rows(con, f("migrations", h), rl.MIG_COLS)):
                eng.ingest_migration(r, frn=hix * FRN_HOUR + i)
            for r in _read_rows(con, f("creates", h), rl.CREATE_COLS):
                eng.ingest_create(r)
        eng.ingest_trade_block(_read_trades(con, f("trades", h), hix))
        eng.prune(hour_epoch(h))
        loaded_end = hour_epoch(h) + MARK_S  # files of hours < loaded_end are in
        while pending and pending[0] + 2 * MARK_S <= loaded_end:
            T = pending.pop(0)
            res = eng.decide(T, w0=w0, last_entry=last_entry)
            results.append(res)
            actions.extend(res.actions)
            if log is not None and res.actions:
                log_actions(log, res, {m.mint: m for m in eng.members()}, T * 1000)
            if progress:
                progress(f"mark {hour_key(datetime.fromtimestamp(T, tz=timezone.utc))}: rows {len(res.mints)} actions {len(res.actions)} {res.seconds:.2f}s")
    if pending:
        raise LiveRefused(f"{len(pending)} mark(s) could not be decided: their hour + 1 is not in the loaded trade hours")
    end = eng.book.close_all("end")
    actions.extend(end)
    if log is not None and end:
        for a in end:
            log.append(BOT_ID, (int(a[3])) * 1000, {"kind": "exit", "order_id": f"{a[1]}:{a[2]}", "mint": a[1], "reason": a[4], "t_mark": int(a[3])})
    allm = [m for r in results for m in r.mints]
    T = np.concatenate([r.T for r in results]) if results else np.zeros(0, np.int64)
    sd = np.concatenate([r.sdv for r in results]) if results else np.zeros(0, np.int64)
    X = np.vstack([r.X for r in results]) if results else np.zeros((0, FZ.N_FEATURES), np.float32)
    sc = np.concatenate([r.score for r in results]) if results else np.zeros(0)
    tr = trades_from_actions(actions)
    counters = dict(eng.counters)
    counters["d3_dropped"] = int(sum(r.d3_dropped for r in results))
    counters["entries"] = int(sum(1 for a in actions if a[0] == "entry"))
    counters["bond_mints"] = len(bond_mints)
    con.close()
    return ReplayResult(results, tr, rows_md5(allm, T, sd, X, sc), trades_md5(tr), len(allm), counters, [r.seconds for r in results],
                        log.md5() if log is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="r4-b live decision path: EXPLORATION replay only (DRY, keyless). md5s and counts out, no prices.")
    ap.add_argument("--walk-dir", required=True, action="append", help="walker-layout dir under an exploration block (hunt23/RULES.md); repeat for windows")
    ap.add_argument("--from-hour", required=True)
    ap.add_argument("--to-hour", required=True)
    ap.add_argument("--w0", required=True, help="first entry mark, YYYY-MM-DDTHH")
    ap.add_argument("--e", required=True, help="calendar stop, YYYY-MM-DDTHH (last entry mark = e - 1 h)")
    ap.add_argument("--static-map", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    smap = rl.load_static_map(a.static_map) if a.static_map else None
    try:
        r = replay_walker([Path(d) for d in a.walk_dir], a.from_hour, a.to_hour, hour_epoch(a.w0), hour_epoch(a.e), static_map=smap,
                          universe_min_mbt=hour_epoch(a.from_hour), log_root=Path(a.out) / "declog", progress=lambda s: print(s, flush=True))
    except LiveRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    out = {"schema": SCHEMA, "rows_md5": r.rows_md5, "trades_md5": r.trades_md5, "n_rows": r.n_rows, "n_trades": len(r.trades), "counters": r.counters,
           "decide_seconds_p50": float(np.median(r.seconds)) if r.seconds else None, "decide_seconds_max": float(max(r.seconds)) if r.seconds else None,
           "declog_md5": r.log_md5}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "replay.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
