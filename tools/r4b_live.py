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
  * sd(T) needs the first second at or after T to be complete in the clock: decide(T) REFUSES (LiveRefused) until the clock holds a later second; a row
    of an earlier slot that arrives after the decision is not seen.
  * A PumpSwap print that arrives before its mint's `complete` row is buffered (<= 900 s of block_time, bounded) and replayed in arrival order when the
    complete arrives; later than that it is lost (counters pending_*). A bonding row after mslot is skipped (the layout keeps slot <= mslot) and counted.
  * A mark with no rows closes held positions as 'gap' (LiveBook docstring); xs_book / r4b_read label and price the same exits 'end' at e.
  * Bonding constants (c_nbond, c_bond_*) need every bonding print of a mint since its create; a live start sees only prints after the start
    (the same train/serve skew EXP-027 section 3.4 discloses for history before H0).
  * Clock seconds older than 2 h are dropped after each decide (fast mode). Market and create minutes are never dropped: the m_* columns are
    differences of float64 cumulative sums from the first market minute, so the engine keeps the read's first minute (H0) to stay byte-equal (see Engine).

Restart and feed gaps (the host-side pieces; PR r4b-live-2c, DEC-032 section 5 item 2 and Amendment 8)
  * A decision-host restart empties LiveBook, so a position held before it would get no exit row from the new process and would wait for the executor's
    48 h + 1 h + 60 s time stop. `host_start` writes the `start` row and then `restart_exits`: one `exit` row (reason "restart", decision_slot None, t_mark
    the mark at or before now) for every entry order_id that has no exit row. It runs before the new engine decides anything. A second call writes nothing.
  * The new engine's state is incomplete (prints, bonding constants, market minutes since H0). `Engine(warmup_s=...)` emits NO entry until the data it has
    ingested spans warmup_s (block_time of the decided mark minus the first market minute it saw). A host builds its engine with `new_host_engine`, whose
    default is DEFAULT_WARMUP_S = 48 h + 4 h = 52 h: every mint that graduated before the start has left the 48 h grid, and the print windows (up to 4 h)
    and market minutes are rebuilt from ingested rows. It does NOT rebuild the create-derived and bonding constants (t_since_create, c_bond_*, c_nbond*,
    cre_*) of mints created before the engine's start: those keep the EXP-027 §3.4 skew (persistent for long-bonding mints). And a 52 h engine is not
    decision-equal to a full-history engine, because its book starts flat. The warm-up gives the frozen engine a clean start, not byte-equality (QP on #1136).
    A shorter warm-up, or replay-on-start, needs an md5 decision-equivalence proof against the full-history engine (`shorter_warmup_proof`); the engine
    itself defaults to warmup_s None (no gate: a replay ingests the history from H0, which is the frozen decision path, byte for byte).
  * `log_gap` writes a `gap` row for a feed GapRecord that halts buys (tools/r4b_feed_adapter: data_loss not False). The engine never sees a gap. The
    executor holds new buys from it (tools/r4b_arena_executor.py); exits keep running. A socket reconnect without data loss writes nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from array import array
from collections import OrderedDict
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
CLOCK_KEEP_S = 2 * 3600  # fast mode: clock SECONDS older than T - 2 h are dropped after decide(T) (slot_at reads only seconds >= T); market minutes never
HASH_CACHE_MAX = 200_000  # DuckHash LRU size
PENDING_S = 900  # a PumpSwap print whose mint has no `complete` yet is kept this many seconds of block_time
PENDING_MAX_PER_MINT = 2_000  # ... at most this many rows per mint (the earliest are kept, later ones counted as overflow)
PENDING_MAX_TOTAL = 100_000  # ... and this many rows over all mints (the oldest-inserted mint is evicted first)
PENDING_SWEEP_S = 60  # expiry sweep at most once per this many seconds of block_time
RESTART_REASON = "restart"  # the exit reason restart_exits writes; the executor follows it like rank | gap | end
DEFAULT_WARMUP_S = GRID_HI_S + 4 * MARK_S  # 52 h: the 48 h grid plus 4 h for the longest feature window; a shorter warm-up needs an md5 proof

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
    """DuckDB's hash() of a VARCHAR (r4b_layout writes th = hash(trader), ch = hash(creator)); hash(NULL) is a fixed non-null value.
    Cached in an LRU of at most `cache_max` entries (a long run sees every trader once; an evicted value is simply recomputed)."""

    def __init__(self, cache_max: int = HASH_CACHE_MAX) -> None:
        import duckdb

        self._con = duckdb.connect()
        self.cache_max = int(cache_max)
        self._cache: OrderedDict[str | None, int] = OrderedDict()

    def __call__(self, s: str | None) -> int:
        c = self._cache
        v = c.get(s)
        if v is not None:
            c.move_to_end(s)
            return v
        v = int(self._con.execute("select hash(?::varchar)", [s]).fetchone()[0])
        c[s] = v
        if len(c) > self.cache_max:
            c.popitem(last=False)
        return v


_HEAD_SHA: str | None = None


def repo_head_sha(repo: Path | str | None = None) -> str:
    """`git rev-parse HEAD` of the repo that holds this module (or of `repo`, for tests), as the `frozen_sha` of the decision log's `start` row (the bridge
    latches frozen_sha_mismatch on a different value). Refuses (LiveRefused, no guess) when git cannot answer, does not return 40 hex digits, or when tracked
    files of the repo are modified (`git status --porcelain --untracked-files=no` is not empty, staged or not): the sha would then not describe the code
    that runs. Untracked files do not count. The default repo is checked once per process (HEAD and the status together) and cached; another `repo` is
    never cached. A caller that passes frozen_sha explicitly (replay_walker, host_start, the --frozen-sha flag) never reaches this function."""
    global _HEAD_SHA
    if repo is None and _HEAD_SHA is not None:
        return _HEAD_SHA
    path = Path(__file__).resolve().parents[1] if repo is None else Path(repo)
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=15, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, timeout=60, check=True).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise LiveRefused(f"cannot read the git HEAD or status of {path} ({type(exc).__name__}); pass frozen_sha explicitly") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", out):
        raise LiveRefused(f"git rev-parse HEAD did not return a 40-hex sha: {out[:60]!r}; pass frozen_sha explicitly")
    if dirty.strip():
        n = len(dirty.strip().splitlines())
        raise LiveRefused(f"{path} has {n} modified tracked path(s): the HEAD sha would not describe the running code; commit or restore them, or pass frozen_sha explicitly")
    if repo is None:
        _HEAD_SHA = out
    return out


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
    # bonding aggregates (the prep_work SQL over bond rows, slot <= mslot): count, buy / sell sums, per-trader sell sums (keys = distinct traders).
    # SQL SUM is NULL (NaN in the constants) when every term is NULL: b_buy_nn / b_sell_nn say some term of that sum is non-NULL (a row of the other side
    # contributes 0, a row of that side contributes its sol, NULL if sol is NULL); b_null_sell_by_th counts the sells with a NULL sol per trader hash
    # (cre_sell is NULL iff every bonding row is such a sell by the creator).
    b_n: int = 0
    b_buy: int = 0
    b_sell: int = 0
    b_buy_nn: bool = False
    b_sell_nn: bool = False
    b_by_th: dict = field(default_factory=dict)
    b_null_sell_by_th: dict = field(default_factory=dict)
    b_maxslot: int | None = None
    bond_alarm: bool = False  # a bonding row after mslot was counted for this mint (it arrived before the complete row was known)
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


@dataclass
class PrintState:
    """The T-independent part of features_at: everything derived from a member's prints (ordered like ut, slot < sd) plus its V0 and creator hash.
    Nothing here reads T, sd, the prep or the member's other constants, so one state serves every mark whose print set it was built from (the T - 5 s
    precompute caches it). Arrays are the full-length ones the T-dependent part searches (window starts); the *_l fields are the values at the last print."""

    n: int
    seed_p: float
    btm: np.ndarray
    lpp: np.ndarray
    cs2: np.ndarray
    cbv: np.ndarray
    csv_: np.ndarray
    cln: np.ndarray
    cnl: np.ndarray
    bidx: np.ndarray
    sidx: np.ndarray
    pb: np.ndarray
    pS: np.ndarray
    cmax_l: np.ndarray
    cmin_l: np.ndarray
    iath_l: np.ndarray
    first_n: np.ndarray
    ccs_n: np.ndarray
    ccb_n: np.ndarray
    qp_l: np.ndarray
    bp_l: np.ndarray
    ck: float


def print_state(V0: float, ch: Any, slot, bt, isbuy, sol, tok, q, b, th, vq) -> PrintState | None:
    """The T-independent half of features_at (every expression is the one features_at had, moved here unchanged). None where features_at has no row for
    ANY T on these prints (fewer than 3 prints, a pre-trade state <= 0, a derived post-state <= 0)."""
    n = len(slot)
    if n < 1:
        return None
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
    # asof mint filter (mint_features: j >= 3, no pre-trade state <= 0 up to jl, derived post-state of jl > 0)
    if j < 3:
        return None
    if (((q + vqa) <= 0) | (bb <= 0)).any():
        return None
    qpost = (q[jl] + so[jl] * (1 - 0.0125) if ib[jl] else q[jl] - so[jl] / (1 - 0.0125)) + vqa[jl]
    bpost = bb[jl] - tk[jl] if ib[jl] else bb[jl] + tk[jl]
    if qpost <= 0 or bpost <= 0:
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
    ck = 1.0 if (ch is not None and not (isinstance(ch, float) and math.isnan(ch))) else 0.0
    isc = (th.astype(np.float64) == np.float64(ch)) if ck else np.zeros(n, bool)
    ccs = np.concatenate([[0], np.cumsum(np.where(isc & ~ib, so, 0))])
    ccb = np.concatenate([[0], np.cumsum(np.where(isc & ib, so, 0))])
    ja = np.array([j])
    jla = np.array([jl])
    return PrintState(n, seed_p, btm, lpp, cs2, cbv, csv_, cln, cnl, bidx, sidx, pb, pS, cmax[jla], cmin[jla], iath[jla], first[ja], ccs[ja], ccb[ja],
                      qp[jla], bp[jla], ck)


def row_at(ps: PrintState, prep: rf.Prep, r: rf.MintConst, T: int, sd: int) -> np.ndarray | None:
    """The T-dependent half of features_at: the (63,) float32 row at mark T from a PrintState, the member's constants and the prep (the six global
    columns and the as-of graduation counts are read here, at T). None where mint_features has no row at T (the last print is not in [T - 600, T))."""
    n = ps.n
    V0 = float(r.v)
    mbt = int(r.mbt)
    btm = ps.btm
    jl = n - 1
    if not (btm[jl] >= T - 600 and btm[jl] < T):
        return None
    seed_p, lpp, cs2, cbv, csv_, cln, cnl = ps.seed_p, ps.lpp, ps.cs2, ps.cbv, ps.csv_, ps.cln, ps.cnl
    bidx, sidx, pb, pS = ps.bidx, ps.sidx, ps.pb, ps.pS
    j = n
    cbt_nan = bool(np.isnan(r.cbt))
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
    F["dd_ath"] = P - ps.cmax_l
    F["rebound_atl"] = P - ps.cmin_l
    F["t_since_ath"] = Ta - btm[ps.iath_l]
    F["ath_seed"] = ps.cmax_l - math.log(seed_p)
    for w in (300, 900, 3600):
        F[f"rv_{w}"] = cs2[ja] - cs2[jw[w]]
        F[f"np_{w}"] = ja - jw[w]
        F[f"bv_{w}"] = (cbv[ja] - cbv[jw[w]]) / 1e9
        F[f"sv_{w}"] = (csv_[ja] - csv_[jw[w]]) / 1e9
    F["np_all"] = ja.astype(float)
    liq = ps.qp_l / 1e9
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
    F["uniq_all"] = ps.first_n
    with np.errstate(divide="ignore", invalid="ignore"):
        F["buyfrac_900"] = np.where(F["np_900"] > 0, (np.searchsorted(bidx, ja) - np.searchsorted(bidx, jw[900])) / np.maximum(F["np_900"], 1), np.nan)
        F["avgsz_900"] = np.where(F["np_900"] > 0, (F["bv_900"] + F["sv_900"]) / np.maximum(F["np_900"], 1), np.nan)
    F["liq_sol"] = liq
    F["fee_tier"] = rf.fee_ppm(ps.qp_l, ps.bp_l)
    F["t_since_last"] = Ta - btm[jla]
    F["cre_sell_post"] = ps.ccs_n / 1e9
    F["cre_buy_post"] = ps.ccb_n / 1e9
    F["cre_known"] = np.full(1, ps.ck)
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


def features_at(prep: rf.Prep, r: rf.MintConst, ch: Any, slot, bt, isbuy, sol, tok, q, b, th, vq, T: int, sd: int) -> np.ndarray | None:
    """The row at the single decision T of tools/r4b_features.mint_features(..., grads_rule="asof_pda_first_print", mint_filters="asof"), computed
    for that T only (mint_features loops over the whole 2-min grid from mbt + 600 s, about 8 s a mark on a ramped universe; this is the live path).
    The prints are the member's, ordered like ut and already restricted to slot < sd (= prep.slot_at(T)); the caller has checked
    ceil120(mbt + 600) <= T <= mbt + 48 h. Every expression is mint_features' own, evaluated at j = n (all prints are before sd), so the float32 row
    is byte-equal (tests/test_r4b_live.py: every grid T of random mints; scripts/research/r4b_live_equiv.py: md5 against the pinned batch calls).
    Two halves: `print_state` (prints only, cacheable) and `row_at` (T, sd, prep, constants); this function is their composition, the only path to a row.
    Returns the (63,) float32 row, or None where mint_features has no row at T."""
    if len(slot) < 1:
        return None
    if int(np.asarray(slot)[-1]) >= sd:
        raise ValueError("features_at needs prints with slot < sd only")
    ps = print_state(float(r.v), ch, slot, bt, isbuy, sol, tok, q, b, th, vq)
    return None if ps is None else row_at(ps, prep, r, T, sd)


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
    Marks must strictly increase: step refuses t <= the last stepped mark (empty or not), so a repeated or late mark cannot rewrite the book.
    Ingest and decide run on ONE thread (the engine's per-mint prints are array buffers viewed by numpy while a mark is decided; a concurrent
    append would raise BufferError).

    Trailing / empty marks (a documented live-vs-read difference). xs_book never sees a mark without rows: it is not a group. After the last
    non-empty mark it closes whatever is held as reason 'end' at last mark + rb, and r4b_read prices an 'end' exit at e (the calendar stop), not at that
    T1. Live cannot wait for a next non-empty mark, so a mark WITH NO ROWS at t >= last non-empty mark + rb closes everything now with reason 'gap' at
    T1 = last non-empty mark + rb (the T1 xs_book gives the same positions, either as 'gap' at the next non-empty mark or as 'end'). So when a feed outage
    runs to the end of the read window, live labels the exits 'gap' where xs_book labels them 'end' and r4b_read prices them at e; the T0 and T1 are equal.
    The same holds for a mark that has rows but whose gap since the previous non-empty mark exceeds rb (the xs_book gap rule, unchanged)."""

    def __init__(self, k: int = FZ.K, rb: int = MARK_S) -> None:
        self.k, self.rb = int(k), int(rb)
        self.pos: dict[str, int] = {}  # mint -> T0, insertion order
        self.prev: int | None = None  # last non-empty mark (xs_book's prev)
        self.last: int | None = None  # last stepped mark, empty or not
        self.last_suppressed = 0  # entries a step(entries=False) did not make

    def step(self, t: int, ranked: Sequence[str], entries: bool = True) -> list[tuple]:
        """entries False (a host that holds buys for a mark, tools/r4b_live_host): the exits are made as always, no entry is made, the mints that would
        have entered are not held (last_suppressed counts them) and may enter at a later mark; `prev` and the gap rule are unchanged."""
        t = int(t)
        self.last_suppressed = 0
        if self.last is not None and t <= self.last:
            raise LiveRefused(f"mark {t} is not after the last stepped mark {self.last}")
        self.last = t
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
                if not entries:
                    self.last_suppressed += 1
                    continue
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
class _PreEntry:
    """One member's T - 5 s precompute (Engine.precompute): the print state of its kept prints, and what decides whether a later decide may reuse it."""

    buf: Any  # the member's slot buffer (identity: a pruned or replaced buffer never matches)
    n_total: int  # prints in the buffer when it was built
    s0: int
    v0: int
    mbt: int
    ch: Any
    state: PrintState | None  # None: features_at has no row for any T on these prints
    max_slot: int  # highest slot among the kept prints
    s0bt: float
    fin_bt: bool  # some kept print has a finite block_time (so later prints cannot change s0bt)


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
    warmup: bool = False  # True: the engine is still warming up, the book was not stepped and no entry was made
    suppressed: int = 0  # entries the host held for this mark (decide(hold_entries=True)); 0 otherwise


class Engine:
    """Streaming r4-b decision engine. mode "dry" (default) or "replay"; "live" is refused (LIVE_BUILT is False).

    v_source "event": V0 = the event V of the s0 print and every member print's own V, pda_match required (the forward read's mode);
    "static_map": V from a {pool: V} map for every print of the mint, no pda_match (exploration replay of tapes without event V, as E-d).
    `bond_mints`: if given, bonding aggregates are kept for these mints only (a replay may pass the set of mints that complete in its window; for
    decisions this equals keeping all, it only saves memory). `universe_min_mbt`: members graduate at or after it (EXP-027: r4b_count.UNIVERSE_MIN_MBT).

    Clock guard: decide(T) refuses unless the clock holds a second strictly after s*, the first second at or after T (sd(T) is the min slot of s*, which
    is final only once a later second has been seen); a replay always satisfies this because it loads the files of hour(T) + 1 first. Marks must
    advance: decide(T) refuses T <= the last decided mark. The certified trigger (decide(T, certified_sd=...), Addendum 5 ruling (a)) replaces only this
    guard by the caller's own "every slot up to sd is delivered" signal; sd is re-derived and must equal the certified one.
    Bounded growth: in the default fast mode (exact_grid False) decide(T) drops clock SECONDS older than T - keep_clock_s (CLOCK_KEEP_S = 2 h; slot_at
    reads only seconds at or after the mark) once the mark is decided; keep_clock_s=None keeps everything and exact_grid always does (the pinned per-mint
    loop reads old grid seconds). Market and create minutes are NEVER dropped: the m_* columns are differences of float64 cumulative minute sums
    (rf.build_prep), whose low bits depend on the first minute once the sums pass 2^53, so the engine keeps the read's first minute (H0) to stay byte-equal.
    Late PumpSwap prints: a print whose mint has no `complete` row yet is buffered (<= PENDING_S of block_time, <= PENDING_MAX_PER_MINT rows per mint,
    <= PENDING_MAX_TOTAL overall) and replayed in arrival order when the complete arrives (ingest_trade only; the block path of a replay feeds migrations first).
    Warm-up: warmup_s (default None = no gate, the replay / full-history path) makes decide(T) emit NO entry until T - (the first market minute this engine
    ingested) >= warmup_s. During it the ranking is computed (MarkResult.ranked) but the book is not stepped (it is empty after a restart), so no entry row
    exists; once a mark is decided past it the gate never closes again. A live host passes DEFAULT_WARMUP_S (see new_host_engine).
    Threads: ingest_* and decide run on ONE thread (np.frombuffer views over the array buffers of a mint make a concurrent append raise BufferError)."""

    def __init__(self, *, universe_min_mbt: int, v_source: str = "event", static_map: Mapping[str, int | None] | None = None, mode: str = "dry",
                 pda_fn: Callable[[str], str] | None = None, hash_fn: Callable[[str | None], int] | None = None, boosters: list | None = None,
                 score_fn: Callable[[list, np.ndarray], np.ndarray] | None = None, bond_mints: set | None = None, k: int = FZ.K,
                 exact_grid: bool = False, keep_clock_s: int | None = CLOCK_KEEP_S, warmup_s: int | None = None) -> None:
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
        if warmup_s is not None and (isinstance(warmup_s, bool) or not isinstance(warmup_s, int) or warmup_s < 0):
            raise LiveRefused("warmup_s must be None or a non-negative int (seconds of ingested block_time)")
        self.warmup_s = warmup_s
        self._warm_done = False
        self.exact_grid = bool(exact_grid)
        self.keep_clock_s = None if (exact_grid or keep_clock_s is None) else int(keep_clock_s)
        self.counters: dict[str, int] = {}
        self._frn = 0
        self.max_slot = -1  # highest slot ingested (any trade row)
        self._clock_hi = -1  # highest block_time second ingested (any trade row)
        self._last_T: int | None = None  # last decided mark
        self._member_final: dict[str, Member | None] = {}  # membership once s0 can no longer change
        self._pending: dict[str, list] = {}  # mint -> PumpSwap prints that arrived before the mint's complete row (arrival order)
        self._pending_n = 0
        self._pending_swept = -1
        self._pre: dict[str, "_PreEntry"] = {}  # T - 5 s precompute (see precompute): mint -> cached print state, valid for mark _pre_T only
        self._pre_T: int | None = None

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
            if st.mslot is not None and mint in self._pending:
                self._pending_replay(mint, st)
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
        if bt > self._clock_hi:
            self._clock_hi = bt
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

    def _add_print(self, st: MintState, mint: str, pool: Any, slot: Any, txi: Any, evi: Any, frn: int, bt: Any, isbuy: Any, sol: Any, tok: Any,
                   q: Any, b: Any, th: int | None, trader: Any, vq: Any) -> None:
        """Append one PumpSwap print of a completed mint if it is on the mint's PDA pool. The trader is hashed here, for kept rows only."""
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
        p["th"].append(int(self.th(trader) if th is None else th))
        p["vq"].append(_nan(vq))

    # -- PumpSwap prints that arrive before their mint's complete row (live feeds do not deliver the streams in chain order)
    def _pending_add(self, mint: str, rec: tuple) -> None:
        """rec = (pool, slot, txi, evi, frn, bt, isbuy, sol, tok, q, b, th, trader, vq). Bounded: PENDING_S of block_time, per mint and overall."""
        if rec[0] is None:  # no pool: never the PDA pool
            return
        bt = rec[5]
        if bt is not None and bt < self._clock_hi - PENDING_S:
            self._count("pending_expired")
            return
        if self._clock_hi >= self._pending_swept + PENDING_SWEEP_S:
            self._pending_sweep()
        lst = self._pending.get(mint)
        if lst is None:
            lst = self._pending[mint] = []
        if len(lst) >= PENDING_MAX_PER_MINT:
            self._count("pending_overflow")
            return
        lst.append(rec + (self._clock_hi if bt is None else bt,))
        self._pending_n += 1
        self._count("pending_buffered")
        while self._pending_n > PENDING_MAX_TOTAL:
            old = next(iter(self._pending))
            n = len(self._pending.pop(old))
            self._pending_n -= n
            self._count("pending_evicted", n)

    def _pending_sweep(self) -> None:
        cut = self._clock_hi - PENDING_S
        self._pending_swept = self._clock_hi
        for mint in list(self._pending):
            lst = self._pending[mint]
            keep = [r for r in lst if r[-1] >= cut]
            if len(keep) != len(lst):
                self._pending_n -= len(lst) - len(keep)
                self._count("pending_expired", len(lst) - len(keep))
                if keep:
                    self._pending[mint] = keep
                else:
                    del self._pending[mint]

    def _pending_replay(self, mint: str, st: MintState) -> None:
        lst = self._pending.pop(mint)
        self._pending_n -= len(lst)
        cut = self._clock_hi - PENDING_S
        for r in lst:
            if r[-1] < cut:
                self._count("pending_expired")
                continue
            pool, slot, txi, evi, frn, bt, isbuy, sol, tok, q, b, th, trader, vq = r[:-1]
            self._add_print(st, mint, pool, slot, txi, evi, frn, bt, isbuy, sol, tok, q, b, th, trader, vq)
            self._count("pending_replayed")

    def _mint_trade(self, venue: Any, mint: Any, pool: Any, slot: Any, txi: Any, evi: Any, frn: int, bt: Any, isbuy: Any, sol: Any, tok: Any,
                    q: Any, b: Any, th: int | None, vq: Any, trader: Any = None) -> None:
        """th: the DuckDB hash of the trader when the caller already has it (block ingest), else None: the trader string is hashed here, and only
        for rows that are kept (a PDA print of a completed mint, a tracked bonding row at or before mslot)."""
        if mint is None or slot is None:
            return
        if venue == "pumpswap":
            st = self.mints.get(mint)
            if st is None or st.mslot is None:  # a PDA pool cannot print before its migration: buffer until the complete row is in (bounded)
                self._pending_add(mint, (pool, int(slot), txi, evi, frn, bt, isbuy, sol, tok, q, b, th, trader, vq))
                return
            self._add_print(st, mint, pool, slot, txi, evi, frn, bt, isbuy, sol, tok, q, b, th, trader, vq)
        elif venue == "pump_bonding":
            if self.bond_mints is not None and mint not in self.bond_mints:
                return
            st = self._st(mint)
            if st.mslot is not None and int(slot) > st.mslot:  # the layout keeps slot < mslot + 1; pump.fun has no bonding trade after complete
                self._count("bond_rows_after_mslot_skipped")  # parity alarm: expected 0
                return
            if th is None:
                th = self.th(trader)
            st.b_n += 1
            s = None if sol is None else int(sol)
            if isbuy is not True or s is not None:  # a term of SUM(case when isbuy then sol else 0 end) that is not NULL
                st.b_buy_nn = True
            if isbuy is not False or s is not None:  # same for SUM(case when not isbuy then sol else 0 end)
                st.b_sell_nn = True
            if s is not None:
                if isbuy is True:
                    st.b_buy += s
                elif isbuy is False:
                    st.b_sell += s
            elif isbuy is False:
                st.b_null_sell_by_th[th] = st.b_null_sell_by_th.get(th, 0) + 1
            prev = st.b_by_th.get(th)
            add = s if (s is not None and isbuy is False) else 0
            st.b_by_th[th] = add if prev is None else prev + add
            st.b_maxslot = int(slot) if st.b_maxslot is None else max(st.b_maxslot, int(slot))
            if bt is not None:
                st.last_bond_bt = int(bt)

    def ingest_trade(self, row: Mapping[str, Any], frn: int | None = None, *, market: bool = True, per_mint: bool = True) -> None:
        """One walker-schema trade row, as it arrives (the live API). The trader is hashed only if the row is kept.
        market / per_mint split the two effects for a feed that learns a PumpSwap row's mint late (tools/r4b_feed_adapter): the row is counted in the
        clock and market minutes at arrival (market only; mint None) and reaches the per-mint path once resolved (per_mint only), never twice."""
        side = row.get("side")
        isbuy = None if side is None else (side == "buy")
        tok, q, b = row.get("token_raw"), row.get("quote_reserve"), row.get("base_reserve")
        if market:
            self._clock_mkt(row.get("venue"), row.get("slot"), row.get("block_time"), row.get("sol_lamports"))
        if not per_mint:
            return
        self._mint_trade(row.get("venue"), row.get("mint"), row.get("pool"), row.get("slot"), row.get("tx_index"), row.get("event_index"),
                         self.next_frn() if frn is None else int(frn), row.get("block_time"), isbuy, row.get("sol_lamports"), tok, q, b,
                         None, row.get("virtual_quote_reserves"), trader=row.get("trader"))

    def ingest_trade_block(self, cols: Mapping[str, np.ndarray]) -> None:
        """A block of trade rows in file order (replay): the clock / market aggregates vectorized, then the per-mint path row by row for the rows
        that can matter (PDA prints of completed mints, bonding prints of tracked mints). Same state as ingest_trade on each row whose mint's complete
        row is already in (tested); a PumpSwap print of a mint without one is dropped here, not buffered (a replay feeds migrations before trades).
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
            self._clock_hi = max(self._clock_hi, int(btb.max()))
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

    def members(self, T: int | None = None, cache: bool = True) -> list[Member]:
        """Members graduated at or after universe_min_mbt; with T, only those that can matter at mark T (mbt >= T - 48 h - 2 h: a decision row needs
        mbt + 48 h >= T, and m_grads counts graduations of the last hour). Membership is cached once no print can still enter the s0 window
        (max ingested slot >= mslot + 7300 + S0_FINAL_SLOTS); before that it is recomputed at every call.
        cache False reads that cache but never writes it (the precompute, which must leave no trace that a later decide could see)."""
        out = []
        for mint, st in self.mints.items():
            if st.mslot is None or st.mbt is None or st.mbt < self.umin or (T is not None and st.mbt < T - GRID_HI_S - 2 * MARK_S):
                continue
            if mint in self._member_final:
                m = self._member_final[mint]
            else:
                m = self.member(mint)
                if cache and self.max_slot >= st.mslot + WINDOW_SLOTS + S0_FINAL_SLOTS:
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
            nan = float("nan")  # SQL SUM over only-NULL terms is NULL (NaN here, and feature c_bond_* then NaN as in the layout)
            bbuy = float(st.b_buy) if st.b_buy_nn else nan
            bsell = float(st.b_sell) if st.b_sell_nn else nan
            if st.ch is None:
                cre = 0.0  # th = NULL is never true: every term is the ELSE 0
            elif st.b_null_sell_by_th.get(st.ch, 0) == st.b_n:
                cre = nan  # every bonding row is a creator sell with a NULL sol
            else:
                cre = float(st.b_by_th.get(st.ch, 0))
            if st.b_maxslot is not None and st.b_maxslot > st.mslot and not st.bond_alarm:
                st.bond_alarm = True  # rows after mslot reached the aggregate (they arrived before the complete row): parity alarm, once per mint
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
        pre = self._pre.get(m.mint) if (self._pre and self._pre_T == T) else None
        if pre is not None:
            s0bt = self._pre_valid(m, pre, sl, bt, sd)
            if s0bt is not None:  # the cached state was built from exactly the prints a fresh pass would use (slot < sd): same arrays, same row
                self._count("precompute_hits")
                const, _ = self.mint_const(m, mid)
                if pre.state is None:
                    return None
                x = row_at(pre.state, prep, const, T, sd)
                return None if x is None else (x, sd, s0bt)
            self._count("precompute_misses")
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

    # -- T - 5 s precompute
    def _kept_sorted(self, m: Member) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """(sl, bt, idx): the member's kept prints (slot >= s0, block_time null or <= mbt + HORIZON_S) ordered (slot, txi, evi, frn), NOT yet cut at sd."""
        p = m.st.prints
        sl = np.frombuffer(p["slot"], np.int64)
        bt = np.frombuffer(p["bt"], np.float64)
        idx = np.flatnonzero((sl >= m.s0) & (np.isnan(bt) | (bt <= m.mbt + HORIZON_S)))
        if not len(idx):
            return None
        tx, ev, fr = (np.frombuffer(p[k], np.int64)[idx] for k in ("txi", "evi", "frn"))
        if not _sorted4(sl[idx], tx, ev, fr):
            idx = idx[np.lexsort((fr, ev, tx, sl[idx]))]
        return sl, bt, idx

    def precompute(self, T: int) -> dict:
        """Run at about T - 5 s of wall time, before decide(T): cache every candidate member's print state (the T-independent half of features_at) so that
        decide(T) recomputes only the members that got a print after this instant, plus the T-dependent half for everyone (windows at T, the six global
        columns m_vps_10m, m_nps_10m, m_vbd_10m, m_ncr_10m, m_ncr_60m, m_vps_60m, the as-of graduation counts m_grads_1h and m_grads_10m, which are read
        from the prep that decide builds at T). It changes no engine state a decision reads: no member cache is written (members(cache=False)), no counter
        a decision depends on moves, and decide(T) is byte-identical with and without it (tests). The entries are used only by decide(T) of the same T
        and dropped after it. A member is reused only if its V0, s0, mbt, creator hash and print buffer are unchanged, every cached print has slot < sd(T)
        and every print that arrived since is either not kept or has slot >= sd(T) (so the print set a fresh pass would cut is the cached one); anything
        else is recomputed in full. Returns {"candidates", "cached", "seconds"}. Refused in exact_grid mode and for a mark that is not after the last
        decided one."""
        t0 = time.monotonic()
        T = int(T)
        if self.exact_grid:
            raise LiveRefused("exact_grid is the reference path: no precompute")
        if T % MARK_S:
            raise LiveRefused(f"{T} is not an hour mark")
        if self._last_T is not None and T <= self._last_T:
            raise LiveRefused(f"mark {T} is not after the last decided mark {self._last_T}")
        self._pre, self._pre_T = {}, T
        cand = 0
        for m in self.members(T, cache=False):
            t_lo = ((m.mbt + GRID_LO_S + 119) // 120) * 120
            if T < t_lo or T > m.mbt + GRID_HI_S or m.st.max_bt < T - 600:
                continue
            cand += 1
            k = self._kept_sorted(m)
            if k is None:
                continue
            sl, bt, idx = k
            p = m.st.prints
            if self.v_source == "event":
                vq = np.frombuffer(p["vq"], np.float64)[idx]
                if np.isnan(vq).any():
                    continue  # decide counts member_null_v_prints and skips the mint: leave it to that path
            else:
                vq = np.full(len(idx), float(m.v0))
            bt_all = bt[idx]
            fin = np.isfinite(bt_all)
            s0bt = float(bt_all[0]) if fin[0] else (float(bt_all[fin][0]) if fin.any() else float(m.mbt))
            state = print_state(float(m.v0), m.st.ch, sl[idx], bt_all, np.frombuffer(p["isbuy"], np.int8)[idx].astype(bool), np.frombuffer(p["sol"], np.float64)[idx],
                                np.frombuffer(p["tok"], np.float64)[idx], np.frombuffer(p["q"], np.float64)[idx], np.frombuffer(p["b"], np.float64)[idx],
                                np.frombuffer(p["th"], np.uint64)[idx], vq)
            self._pre[m.mint] = _PreEntry(p["slot"], len(sl), m.s0, m.v0, m.mbt, m.st.ch, state, int(sl[idx][-1]), s0bt, bool(fin.any()))
        self._count("precompute_runs")
        return {"candidates": cand, "cached": len(self._pre), "seconds": time.monotonic() - t0}

    def _pre_valid(self, m: Member, pre: "_PreEntry", sl: np.ndarray, bt: np.ndarray, sd: int) -> float | None:
        """The entry's s0 block_time if the cached state is exactly what a fresh pass at sd would build for this member, else None (see precompute)."""
        if m.st.prints["slot"] is not pre.buf or (pre.s0, pre.v0, pre.mbt, pre.ch) != (m.s0, m.v0, m.mbt, m.st.ch):
            return None
        if pre.max_slot >= sd or len(sl) < pre.n_total:
            return None
        if len(sl) > pre.n_total:
            nsl, nbt = sl[pre.n_total:], bt[pre.n_total:]
            kept = (nsl >= m.s0) & (np.isnan(nbt) | (nbt <= m.mbt + HORIZON_S))
            if kept.any() and ((nsl[kept] < sd).any() or not pre.fin_bt):
                return None  # a kept print the fresh pass would cut in, or one that could be the first finite block_time (s0bt)
        return pre.s0bt

    def drop_precompute(self) -> None:
        """Forget any cached precompute (decide recomputes everything). Safe at any time."""
        self._pre, self._pre_T = {}, None

    def sd_at(self, T: int) -> int | None:
        """sd(T) as decide(T) will compute it (the min slot of the first clock second at or after T), or None while decide(T)'s clock guard would still refuse."""
        pt = self.clock_point(T)
        return None if pt is None else pt[0]

    def clock_point(self, T: int, provisional: bool = False) -> tuple[int, int] | None:
        """(sd, s*): sd(T) and the first clock second s* >= T it comes from, or None until a later second is in (decide's guard). Same value as Prep.slot_at.
        provisional True (the certified trigger, QP-RULING-1030 Addendum 5 ruling (a)): the same value as soon as ANY second at or after T is in, without the
        later-second guard. That sd is only as final as the caller's own completeness signal makes it (every slot up to sd delivered); decide(T,
        certified_sd=sd) re-derives it and refuses if it differs."""
        T = int(T)
        if provisional:
            if self._clock_hi < T:
                return None
            later = [s for s in self.clock if s >= T]
            if not later:
                return None
            s_star = min(later)
            return int(self.clock[s_star]), int(s_star)
        if self._clock_hi < T + 1:
            return None
        later = [s for s in self.clock if s >= T]
        if len(later) < 2:
            return None
        s_star = min(later)
        return int(self.clock[s_star]), int(s_star)

    def score(self, X: np.ndarray) -> np.ndarray:
        if self.score_fn is not None:
            return np.asarray(self.score_fn(self.boosters, X), np.float64)
        from tools import r4b_score as rs

        if self.boosters is None:
            self.boosters = rs.load_boosters()
        return rs.bag_score(self.boosters, X)

    def decide(self, T: int, w0: int | None = None, last_entry: int | None = None, hold_entries: bool = False, certified_sd: int | None = None) -> MarkResult:
        """The decision at mark T (T % 3600 == 0). w0 / last_entry bound the counted marks like the read (rows outside give an empty mark).
        Refuses (LiveRefused, nothing changed) when T is not after the last decided mark, or when the clock has no second strictly after s*, the first
        second at or after T: sd(T) = the min slot of s* would not be final (an early call would silently use the last known second).
        hold_entries (a host that must not buy at this mark: incomplete inputs, a credit cap): the ranking, the scores and the exits are those of an ordinary
        decide, but the book makes no entry (MarkResult.suppressed counts them); the mints stay unheld and may enter at a later mark.
        A precompute(T) made before is used here and dropped when this mark is decided (a refusal leaves it for a retry).
        certified_sd (QP-RULING-1030 Addendum 5 ruling (a), the certified trigger): the caller holds a completeness signal that every slot up to sd(T) is
        delivered (r4b_feed_merge.mark_complete(T, sd).complete in full, never a tip alone), so the one-chain-second clock guard is skipped. ONLY the trigger
        changes: sd is still derived here by the same Prep.slot_at, the decision function and the slot < sd filter are the same lines, and the call REFUSES
        (LiveRefused, nothing changed) when the engine's own sd(T) differs from certified_sd or when no clock second at or after T is in. The caller retries
        or falls back. With complete inputs the result is byte-equal to the guard path's (tests)."""
        t_start = time.monotonic()
        T = int(T)
        if T % MARK_S:
            raise LiveRefused(f"{T} is not an hour mark")
        if self._last_T is not None and T <= self._last_T:
            raise LiveRefused(f"mark {T} is not after the last decided mark {self._last_T}")
        if certified_sd is None:
            if self._clock_hi < T + 1 or sum(1 for sec in self.clock if sec >= T) < 2:
                raise LiveRefused(f"clock not complete at {T}: need a second after the first second >= {T} (latest ingested second {self._clock_hi})")
        else:
            if isinstance(certified_sd, (bool, np.bool_)) or not isinstance(certified_sd, (int, np.integer)):
                raise LiveRefused("certified_sd must be an int slot")
            pt = self.clock_point(T, provisional=True)  # a cheap check first: nothing below (members, prep) has run
            if pt is None:
                raise LiveRefused(f"certified decide at {T}: no clock second at or after {T} yet (latest ingested second {self._clock_hi})")
            if pt[0] != int(certified_sd):
                raise LiveRefused(f"certified sd {int(certified_sd)} != the engine's sd({T}) {pt[0]}: not decided")
        mem = self.members(T)
        prep = self.prep(mem)
        sd = int(prep.slot_at(np.array([T], np.int64))[0])
        if certified_sd is not None and sd != int(certified_sd):  # the authoritative derivation (the one the features use) must agree too
            raise LiveRefused(f"certified sd {int(certified_sd)} != the engine's sd({T}) {sd}: not decided")
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
        warming = self._warming(T)
        if warming:
            self._count("warmup_marks")
        actions = self.book.step(T, [] if warming else ranked, entries=not hold_entries)  # warming: the book is empty and stays empty, no entry row can exist
        self._last_T = T
        self._pre, self._pre_T = {}, None
        if self.keep_clock_s is not None:
            self.prune_clock(T)
        return MarkResult(T, sd, mints, np.full(len(mints), T, np.int64), np.array(sds, np.int64), X, sc, ranked, actions, d3,
                          time.monotonic() - t_start, warmup=warming, suppressed=0 if warming else self.book.last_suppressed)

    def _warming(self, T: int) -> bool:
        """True while the mark T is inside the warm-up: the engine has ingested less than warmup_s of block_time before T. The first market minute never
        leaves the engine (see prune_clock), so it is the engine's own start of data. Once a mark has passed, the gate stays open."""
        if self.warmup_s is None or self._warm_done:
            return False
        if self.mkt and T - min(self.mkt) >= self.warmup_s:
            self._warm_done = True
            return False
        return True

    def prune_clock(self, T: int) -> tuple[int, int]:
        """Drop clock SECONDS older than T - keep_clock_s (CLOCK_KEEP_S): only slot_at(T') for later marks reads the clock, and it never reads a second
        before T'. Market minutes and create minutes are NEVER pruned (reviewer on #1130): the m_* columns are differences of float64 cumsums built
        from the first market minute (rf.build_prep / Prep.mwin), and once those sums pass 2**53 the low bits of a difference depend on the prefix, so
        the engine must keep the same first minute as the read (its H0) to stay byte-equal. They are small (1,440 a day). Returns (seconds, 0).
        Refused in exact_grid mode."""
        if self.exact_grid:
            raise LiveRefused("exact_grid reads old grid seconds: no clock pruning")
        lo = int(T) - (CLOCK_KEEP_S if self.keep_clock_s is None else self.keep_clock_s)
        old_s = [x for x in self.clock if x < lo]
        for x in old_s:
            del self.clock[x]
        self._count("clock_pruned", len(old_s))
        return len(old_s), 0

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
    """Append one mark's actions as arena_declog rows (exits first, then entries in rank order). order_id = '<mint>:<T0>'. Entries and exits both carry
    decision_slot = res.sd = sd(T), the slot the mark's decision is made at (the bridge's decision-to-landed counts from it)."""
    rows = []
    for a in res.actions:
        if a[0] == "exit":
            rows.append(log.append(BOT_ID, ms, {"kind": "exit", "order_id": f"{a[1]}:{a[2]}", "mint": a[1], "reason": a[4], "t_mark": int(a[3]),
                                                "decision_slot": res.sd}))
    sc = {m: float(s) for m, s in zip(res.mints, res.score.tolist())}
    for a in res.actions:
        if a[0] == "entry":
            m = members.get(a[1]) if members else None
            rows.append(log.append(BOT_ID, ms, {"kind": "entry", "order_id": f"{a[1]}:{a[2]}", "pool": m.pool if m else None,
                                                "meta": {"mint": a[1], "v0": m.v0 if m else None, "mbt": m.mbt if m else None, "s0": m.s0 if m else None},
                                                "stake_sol": stake_sol, "decision_slot": res.sd, "t_mark": int(a[2]), "rank": int(a[3]), "score": sc.get(a[1])}))
    return rows


# ------------------------------------------------------------------------------------------------------------------------------- host restart and feed gaps
def _check_log_clock(log: Any, bot_id: str, now_ms: Any) -> list[dict]:
    """Verify the hash chain (a broken chain raises tools.arena_declog.LogChainError: nothing is written on a log we cannot trust) and refuse a clock that is
    behind the newest row (the log is one file per UTC day; a row with an earlier ms would land in an older file ahead of newer rows and break the chain
    order). Returns the rows."""
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms <= 0:
        raise LiveRefused("now_ms (int, epoch ms) is required")
    log.verify(bot_id)
    rows = log.rows(bot_id)
    if rows and now_ms < max(int(r["ms"]) for r in rows):
        raise LiveRefused(f"now_ms {now_ms} is before the newest logged row {max(int(r['ms']) for r in rows)}: the clock went backwards")
    return rows


def open_entries(log: Any, bot_id: str = BOT_ID) -> list[dict]:
    """The entry rows (first row per order_id, in log order) whose order_id has no exit row of any reason."""
    entries: dict[str, dict] = {}
    exited: set[str] = set()
    for r in log.rows(bot_id):
        oid = r.get("order_id")
        if not isinstance(oid, str):
            continue
        if r.get("kind") == "entry":
            entries.setdefault(oid, r)
        elif r.get("kind") == "exit":
            exited.add(oid)
    return [e for oid, e in entries.items() if oid not in exited]


def restart_exits(log: Any, bot_id: str = BOT_ID, now_ms: int | None = None, reason: str = RESTART_REASON) -> list[dict]:
    """Exit-on-start. A restarted engine starts with an empty LiveBook, so it never writes the exit of a position the previous process entered. This appends
    one `exit` row (reason "restart", decision_slot None, order_id and mint copied from the entry, t_mark = the UTC hour mark at or before now_ms) for every
    entry order_id that has no exit row, at ms = now_ms, in entry order, and returns them. Call it on every start BEFORE the engine decides anything
    (`host_start` does). Idempotent: once every entry has an exit row it writes nothing, so a crash between two starts cannot double-exit. A position the
    executor never took (capped out) gets a row too; the executor ignores and counts an exit for a position it does not hold. now_ms is required."""
    if not isinstance(reason, str) or not reason:
        raise LiveRefused("reason must be a non-empty string")
    _check_log_clock(log, bot_id, now_ms)
    t_mark = (int(now_ms) // 1000 // MARK_S) * MARK_S  # type: ignore[arg-type]
    out = []
    for e in open_entries(log, bot_id):
        oid = e["order_id"]
        mint = (e.get("meta") or {}).get("mint") or oid.rsplit(":", 1)[0]
        out.append(log.append(bot_id, int(now_ms), {"kind": "exit", "order_id": oid, "mint": mint, "reason": reason, "t_mark": t_mark, "decision_slot": None}))  # type: ignore[arg-type]
    return out


def _check_warmup(warmup_s: Any, shorter_warmup_proof: Any) -> None:
    if isinstance(warmup_s, bool) or not isinstance(warmup_s, int) or warmup_s < 0:
        raise LiveRefused("warmup_s must be a non-negative int (seconds of ingested block_time)")
    if warmup_s < DEFAULT_WARMUP_S and not (isinstance(shorter_warmup_proof, str) and shorter_warmup_proof.strip()):
        raise LiveRefused(f"a warm-up shorter than {DEFAULT_WARMUP_S} s (48 h + 4 h) needs an md5 decision-equivalence proof: pass its reference as shorter_warmup_proof")


def new_host_engine(*, warmup_s: int = DEFAULT_WARMUP_S, shorter_warmup_proof: str | None = None, **kw: Any) -> "Engine":
    """The engine a live host builds after (re)start: Engine(**kw) with the warm-up gate on. warmup_s defaults to DEFAULT_WARMUP_S (52 h); a shorter value
    is refused unless shorter_warmup_proof names the md5 equivalence proof that covers it."""
    _check_warmup(warmup_s, shorter_warmup_proof)
    return Engine(warmup_s=warmup_s, **kw)


def host_start(log: Any, *, now_ms: int, frozen_sha: str | None = None, bot_id: str = BOT_ID, warmup_s: int = DEFAULT_WARMUP_S,
               shorter_warmup_proof: str | None = None, extra: Mapping[str, Any] | None = None) -> dict:
    """What a decision host does when it starts, in this order: write the `start` row (frozen_sha = repo_head_sha() unless given; the executor latches
    frozen_sha_mismatch on another value and counts a second start row as a host restart), then restart_exits for every position the previous process left
    open. The engine decides only after this returns, and it is built with the same warmup_s (new_host_engine). Returns {"start", "exits", "warmup_s"}.
    extra: run-configuration fields the host records in the start row (tools/r4b_live_host: the trigger and the change_id of the run, Addendum 5); they
    can never replace a field this function writes."""
    _check_warmup(warmup_s, shorter_warmup_proof)
    sha = frozen_sha if frozen_sha is not None else repo_head_sha()
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise LiveRefused("frozen_sha must be 40 hex digits")
    _check_log_clock(log, bot_id, now_ms)
    body = {"kind": "start", "book": BOOK, "exp": "EXP-027", "mode": "dry", "schema": SCHEMA, "frozen_sha": sha, "warmup_s": warmup_s}
    if warmup_s < DEFAULT_WARMUP_S:
        body["warmup_proof"] = shorter_warmup_proof
    for k, v in (extra or {}).items():
        if k in body or k in ("seq", "arm", "ms", "prev", "sha"):
            raise LiveRefused(f"host_start extra may not set {k!r}")
        body[k] = v
    start = log.append(bot_id, now_ms, body)
    return {"start": start, "exits": restart_exits(log, bot_id, now_ms), "warmup_s": warmup_s}


def log_gap(log: Any, gap: Any, *, now_ms: int | None = None, bot_id: str = BOT_ID) -> dict | None:
    """Append a `gap` row for a feed gap that halts buys, and return it; return None (nothing written) when it does not. `gap` is a
    tools.r4b_feed_adapter GapRecord (or a Mapped of kind "gap"), or a Reject / Mapped "reject" that halts buys (a data fault: the feed lost or mangled a row).
    The row is {"kind": "gap", "ms": <instant>, "gap_kind", "from_ms", "to_ms", "data_loss"}: ms = the record's instant (its to_ms, else its receipt time);
    a record without an instant uses now_ms; neither is a refusal. If the instant is earlier than the newest logged row, ms is raised to that row's ms
    (the log is one file per UTC day and must stay in order) and the true instant is kept as `instant_ms`; the executor holds from max(ms, to_ms), so a
    late ms only lengthens the hold. A GapRecord whose data_loss is False (a socket reconnect without loss) halts nothing and writes nothing."""
    rec = getattr(gap, "row", gap)
    if not bool(getattr(rec, "halts_buys", True)):
        return None
    if hasattr(rec, "reason"):  # a Reject: no span, no instant of its own
        kind, frm, to, inst, dl = f"reject_{rec.reason}", None, None, None, None
    else:
        kind, frm, to, inst, dl = rec.kind, rec.from_ms, rec.to_ms, rec.instant_ms, rec.data_loss
    for v in (inst, now_ms):
        if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
            raise LiveRefused("a gap instant must be an int (epoch ms)")
    ms = inst if inst is not None else now_ms
    if ms is None:
        raise LiveRefused("gap record has no instant and no now_ms was given")
    rows = log.rows(bot_id)
    newest = max((int(r["ms"]) for r in rows), default=None)
    body = {"kind": "gap", "gap_kind": kind, "from_ms": frm, "to_ms": to, "data_loss": dl}
    if newest is not None and ms < newest:
        body["instant_ms"] = ms
        ms = newest
    return log.append(bot_id, ms, body)


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
                  extra_hours: int = 2, allow_dir: bool = False, memory_limit: str = "4GB", progress: Callable[[str], None] | None = None,
                  frozen_sha: str | None = None) -> ReplayResult:
    """Feed a walker-layout dir hour by hour through the Engine and decide every mark in [w0, e - 1 h], as the read does (EXP-027 section 4.1).
    Migrations and creates of [h0, h1) and trades of [h0, h1 + extra_hours) are fed, as r4b_layout.build loads them. Mark T is decided once the
    files of hour(T) + 1 are in (every row with block_time before T and the first second at or after T are then in; features read slot < sd(T) only).
    static_map given -> v_source static_map; None -> event. Refuses a non-exploration path or any hour at or after REPLAY_HOUR_MAX unless allow_dir
    (synthetic test dirs only). walk_dir may be a list of window dirs (e.g. explore-0814 w7, w6, w5): each hour file is taken from the first dir
    that has it. frozen_sha: written on the declog `start` row (the bridge compares it with its pin); default = repo_head_sha(), read once per process
    from `git rev-parse HEAD` (refused when tracked files are modified), and only when log_root is given."""
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
        log.append(BOT_ID, w0 * 1000, {"kind": "start", "book": BOOK, "exp": "EXP-027", "mode": "replay", "schema": SCHEMA,
                                       "frozen_sha": frozen_sha if frozen_sha is not None else repo_head_sha()})
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
    ap.add_argument("--frozen-sha", default=None, help="written on the declog start row; default: git rev-parse HEAD of this repo")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    smap = rl.load_static_map(a.static_map) if a.static_map else None
    try:
        r = replay_walker([Path(d) for d in a.walk_dir], a.from_hour, a.to_hour, hour_epoch(a.w0), hour_epoch(a.e), static_map=smap,
                          universe_min_mbt=hour_epoch(a.from_hour), log_root=Path(a.out) / "declog", progress=lambda s: print(s, flush=True),
                          frozen_sha=a.frozen_sha)
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
