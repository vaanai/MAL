#!/usr/bin/env python3
"""C1-NF live feature engine: incremental, event-driven reproduction of C1's 107 stage-2 features.

Ground truth (frozen, do not edit): /data/mal/hunt-1008/c1-cascade-postgrad scripts/11_passA.py (pool flow, shape, holders, wallet
intelligence), scripts/12_passC.py (pre-grad, creator record, narrative heat, market activity), scripts/14_export.py (column order),
ml/rule.json (stage 1 = [1.0, 3.0, 20.0, 20.0]); C1-NF adds the cap h_top1 <= 0.5 (c1nf-verify/VERIFY.md).

Decision rule: at every whole UTC minute T from graduation + 600 s to + 24 h, for each pool that printed in the last 60 min, features
use only canonical-pool PumpSwap prints with slot < SD, SD = the first slot whose block_time >= T.

Event API (tip-tape decoded schema; one call per decoded row, in (slot, tx_index, event_index) order):
    on_block(slot, block_time)                         clock only (on_trade feeds the clock too)
    on_create(mint, creator, name, symbol, block_time, mayhem)
    on_graduation(mint, block_time)                    pump.fun 'complete' migration row
    on_trade(venue, mint, trader, is_buy, sol_lamports, token_raw, quote_reserve, base_reserve, pool, slot, block_time)
    on_trade_row(row)                                  the same from a mapping with the tape / tip-follower keys
    set_pool_v(pool, v_lamports)                       PumpSwap virtual quote reserve of a pool (tip follower stamps it on each row)
Query:
    features_at(pool, T, sd=None) -> Features | None   None = pool not in the universe or not alive at T
    alive_pools(T)                                     eligible pools inside the decision window of T

Differences a live engine cannot avoid (all measured in tools/c1nf_parity.py, none hidden):
  * C1's batch `spot`, `qreal`, `r5/r15/r60/rgrad`, `r_ath`, `vol*`, and the 6 h creator outcomes use the PRE-trade reserves of the first
    print with slot >= SD (a state the batch sees by looking one print ahead). A live engine has only prints with slot < SD, so it uses
    the same fee-model post-trade estimate the batch uses when no later print exists (G_FEE = 1.25%). If the next print is already
    ingested the engine uses its exact pre-trade reserves (this is the batch's behaviour, used by the parity harness "exact" mode).
  * bc_* are frozen at graduation (the batch aggregates the whole tape window per mint, including bonding rows after `complete`).
  * Only numpy is used on the hot path (no pandas).
"""
from __future__ import annotations

import bisect
import math
import re
from typing import Any, Mapping, Optional, Protocol, Sequence

import numpy as np

NAN = float("nan")
WSOL = "So11111111111111111111111111111111111111112"
VENUE_BONDING = "pump_bonding"
VENUE_PUMPSWAP = "pumpswap"

GRID_START = 600
GRID_END = 24 * 3600
G_FEE = 0.0125
V_LO, V_HI = 17.5e9, 17.7e9
FIRST_PRINT_WINDOW = (-5, 120)          # first canonical print minus `complete`, seconds
SUPERSET = (0.5, 1.5, 8.0)              # pass-A superset: v5 >= .5 and (surge >= 1.5 or new5 >= 8)
STAGE1 = (1.0, 3.0, 20.0, 20.0)         # ml/rule.json: (v5 >= a and surge >= s) or new5 >= k, and qreal >= L
H_TOP1_CAP = 0.5                        # C1-NF cap
OUTCOME_S = 6 * 3600
LEDGER_COLS = ("n", "nbond", "cash", "nwin", "nrt", "ndays", "buy")

# Exact column order of ml/conf.npz / disc.npz (scripts/14_export.py), 107 columns.
FEATURE_NAMES: tuple[str, ...] = (
    "r_ath", "r_atl", "ath_grad", "vol15", "vol60", "ncum", "ntr_cum", "vcum", "maxbuy5", "avgbuy5", "buyshare5", "cr_sold", "cr_bought",
    "h_npos", "h_top1", "h_top5", "h_top10", "h_pos_frac_supply", "sell_curveholder_share",
    "wb5_n", "wb5_new", "wb5_skill", "wb5_skill_sol", "wb5_cash_med", "wb5_roi_med", "wb5_winrate", "wb5_bot", "wb5_sniper", "wb5_actmed",
    "ws5_n", "ws5_new", "ws5_skill", "ws5_skill_sol", "ws5_cash_med", "ws5_roi_med", "ws5_winrate", "ws5_bot", "ws5_sniper", "ws5_actmed", "sk_net5",
    "wb15_n", "wb15_new", "wb15_skill", "wb15_skill_sol", "wb15_cash_med", "wb15_roi_med", "wb15_winrate", "wb15_bot", "wb15_sniper", "wb15_actmed",
    "ws15_n", "ws15_new", "ws15_skill", "ws15_skill_sol", "ws15_cash_med", "ws15_roi_med", "ws15_winrate", "ws15_bot", "ws15_sniper", "ws15_actmed",
    "sk_net15",
    "bc_dur", "bc_n_trades", "bc_n_traders", "bc_buy_sol", "bc_sell_sol", "mayhem", "has_create",
    "mk_n_ps60", "mk_v_ps60", "mk_n_bd60", "mk_v_bd60", "mk_grads60", "mk_creates60",
    "cr_prev_creates", "cr_creates_24h", "cr_prev_grads", "cr_known_out", "cr_lmax6", "cr_lend6", "cr_big6",
    "nar_cr1h", "nar_cr6h", "nar_gr6h", "nar_gr24h", "nar_sym1h", "nar_nwords",
    "age", "v1", "v5", "v15", "v60", "bs5", "ss5", "net5", "netp5", "nb5", "n5", "new5", "newp5", "qreal", "r5", "r15", "r60", "rgrad", "surge",
    "hod",
)
N_FEATURES = len(FEATURE_NAMES)
FIDX = {n: i for i, n in enumerate(FEATURE_NAMES)}
assert N_FEATURES == 107 and len(FIDX) == 107


def feature_group(name: str) -> str:
    """Parity-report groups (the manager's plan)."""
    if name in ("cr_sold", "cr_bought"):
        return "creator_flow"
    if name.startswith(("h_", "sell_curveholder")):
        return "holders"
    if name.startswith(("wb", "ws", "sk_net")):
        return "wallet"
    if name.startswith("bc_") or name in ("mayhem", "has_create"):
        return "pregrad"
    if name.startswith("mk_"):
        return "market"
    if name.startswith("cr_"):
        return "creator_record"
    if name.startswith("nar_"):
        return "narrative"
    if name == "hod":
        return "hod"
    if name in ("r_ath", "r_atl", "ath_grad", "vol15", "vol60", "ncum", "ntr_cum", "vcum", "maxbuy5", "avgbuy5", "buyshare5"):
        return "shape"
    return "pool_flow"


STOP = {"the", "coin", "token", "sol", "solana", "pump", "fun", "and", "for", "of", "meme", "inu", "official", "new", "first", "just", "you", "this"}


def words(name: Optional[str], sym: Optional[str]) -> list[str]:
    """12_passC.words verbatim."""
    s = f'{name or ""} {sym or ""}'.lower()
    return sorted({w for w in re.findall(r"[a-z0-9]+", s) if len(w) >= 3 and w not in STOP})


def grid_times(g0: int, t_end: Optional[int] = None) -> np.ndarray:
    """Whole UTC minutes from graduation + 10 min to + 24 h (clipped to t_end when given)."""
    end = g0 + GRID_END if t_end is None else min(g0 + GRID_END, t_end)
    return np.arange((g0 + GRID_START + 59) // 60 * 60, end + 1, 60, dtype=np.int64)


def utc_day(t: int) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(int(t), dt.timezone.utc).strftime("%Y-%m-%d")


class LedgerSnapshot(Protocol):
    """Prior-day wallet ledger as of one UTC day: trader pubkey -> the 7 values in LEDGER_COLS (floats), or None if unknown."""

    def get(self, trader: str) -> Optional[Sequence[float]]: ...


class LedgerProvider(Protocol):
    """Task 1 (claude/c1nf-ledger) builds the ledger. `snapshot_for_day('YYYY-MM-DD')` must contain tape days strictly BEFORE that
    UTC day, nothing from the day itself. Return None when no prior day exists (wallet features then stay NaN, as in C1)."""

    def snapshot_for_day(self, day: str) -> Optional[LedgerSnapshot]: ...


class SlotClock:
    """block_time -> min slot, sorted by block_time. decision_slot(T) = min slot of the smallest block_time >= T (11_passA.dec_slot)."""

    def __init__(self) -> None:
        self.bts: list[int] = []
        self.minslot: dict[int, int] = {}

    def add(self, slot: int, bt: int) -> None:
        cur = self.minslot.get(bt)
        if cur is None:
            if not self.bts or bt > self.bts[-1]:
                self.bts.append(bt)
            else:
                bisect.insort(self.bts, bt)
            self.minslot[bt] = slot
        elif slot < cur:
            self.minslot[bt] = slot

    def decision_slot(self, T: int) -> Optional[int]:
        i = bisect.bisect_left(self.bts, T)
        return self.minslot[self.bts[i]] if i < len(self.bts) else None

    def prune(self, before_bt: int) -> None:
        i = bisect.bisect_left(self.bts, before_bt)
        for bt in self.bts[:i]:
            self.minslot.pop(bt, None)
        del self.bts[:i]


class Features:
    __slots__ = ("pool", "mint", "t", "sd", "i1", "vec", "pre", "stage1", "h_top1", "cap_ok")

    def __init__(self, pool, mint, t, sd, i1, vec, pre, stage1, h_top1, cap_ok):
        self.pool, self.mint, self.t, self.sd, self.i1 = pool, mint, t, sd, i1
        self.vec, self.pre, self.stage1, self.h_top1, self.cap_ok = vec, pre, stage1, h_top1, cap_ok

    def as_float32(self) -> np.ndarray:
        return self.vec.astype(np.float32)

    def get(self, name: str) -> float:
        return float(self.vec[FIDX[name]])


class _Info:
    __slots__ = ("creator", "cbt", "name", "sym", "words", "mayhem", "has_create", "gbt", "bc_n", "bc_buy", "bc_sell", "bc_traders", "bc_frozen")

    def __init__(self) -> None:
        self.creator = None; self.cbt = None; self.name = None; self.sym = ""; self.words = []; self.mayhem = None
        self.has_create = False; self.gbt = None
        self.bc_n = 0; self.bc_buy = 0.0; self.bc_sell = 0.0; self.bc_traders = set(); self.bc_frozen = None


class _Pool:
    __slots__ = ("pool", "mint", "V", "creator", "slot", "bt", "isb", "sol", "tok", "q", "b", "th", "qpre", "ppre", "cs_vol", "cs_bs", "cs_ss",
                 "cs_nb", "cs_new", "cs_any", "cs_crs", "cs_crb", "csl", "lp", "am_hi", "am_lo", "hold", "seen_any", "seen_buy", "first_slot",
                 "first_bt", "g0", "eligible", "bad", "est", "outcome", "dead", "n_final")

    def __init__(self, pool: str, mint: str, V: float, creator: Optional[str]) -> None:
        self.pool, self.mint, self.V, self.creator = pool, mint, float(V), creator
        self.slot: list[int] = []; self.bt: list[int] = []; self.isb: list[bool] = []; self.sol: list[float] = []; self.tok: list[float] = []
        self.q: list[float] = []; self.b: list[float] = []; self.th: list[str] = []
        self.qpre: list[float] = []; self.ppre: list[float] = []
        self.cs_vol = [0.0]; self.cs_bs = [0.0]; self.cs_ss = [0.0]; self.cs_nb = [0.0]; self.cs_new = [0.0]; self.cs_any = [0.0]
        self.cs_crs = [0.0]; self.cs_crb = [0.0]
        self.csl: list[float] = []; self.lp: list[float] = []; self.am_hi = [NAN]; self.am_lo = [NAN]
        self.hold: dict[str, float] = {}; self.seen_any: dict[str, int] = {}; self.seen_buy: dict[str, int] = {}
        self.first_slot = None; self.first_bt = None; self.g0 = None; self.eligible = None; self.bad = False
        self.est = None            # (q_incl_V, b, price) after the last ingested print, fee-model estimate; None = invalid
        self.outcome = None        # (max6, end6) over p_grad, once the first print with bt >= g0 + 6 h is ingested
        self.dead = False; self.n_final = 0

    def add(self, slot: int, bt: Optional[int], isb: bool, sol: float, tok: float, q: float, b: float, th: str) -> None:
        j = len(self.slot)
        if self.first_slot is None:
            self.first_slot = slot
        if bt is None:
            btv = self.bt[-1] if j else (self.g0 if self.g0 is not None else -1)
        else:
            btv = bt if not j else max(bt, self.bt[-1])
            if self.first_bt is None:
                self.first_bt = bt
        qp = q + self.V
        if not (qp > 0 and b > 0 and math.isfinite(qp) and math.isfinite(b)):
            self.bad = True
        p = qp / b if b else NAN
        self.slot.append(slot); self.bt.append(btv); self.isb.append(isb); self.sol.append(sol); self.tok.append(tok)
        self.q.append(q); self.b.append(b); self.th.append(th); self.qpre.append(qp); self.ppre.append(p)
        self.cs_vol.append(self.cs_vol[-1] + sol)
        self.cs_bs.append(self.cs_bs[-1] + (sol if isb else 0.0))
        self.cs_ss.append(self.cs_ss[-1] + (0.0 if isb else sol))
        self.cs_nb.append(self.cs_nb[-1] + (1.0 if isb else 0.0))
        first_any = th not in self.seen_any
        if first_any:
            self.seen_any[th] = j
        first_buy = isb and th not in self.seen_buy
        if first_buy:
            self.seen_buy[th] = j
        self.cs_new.append(self.cs_new[-1] + (1.0 if first_buy else 0.0))
        self.cs_any.append(self.cs_any[-1] + (1.0 if first_any else 0.0))
        is_cr = self.creator is not None and th == self.creator
        self.cs_crs.append(self.cs_crs[-1] + (tok if (is_cr and not isb) else 0.0))
        self.cs_crb.append(self.cs_crb[-1] + (tok if (is_cr and isb) else 0.0))
        self.hold[th] = self.hold.get(th, 0.0) + (tok if isb else -tok)
        lpj = math.log(p) if p > 0 else NAN
        if j == 0:
            self.csl.append(0.0)
        else:
            d = lpj - self.lp[j - 1]
            self.csl.append(self.csl[j - 1] + d * d)
            self.am_hi.append(p if j == 1 else max(self.am_hi[j - 1], p))
            self.am_lo.append(p if j == 1 else min(self.am_lo[j - 1], p))
        self.lp.append(lpj)
        if isb:
            qf, bf = qp + sol * (1 - G_FEE), b - tok
        else:
            qf, bf = qp - sol / (1 - G_FEE), b + tok
        self.est = (qf, bf, qf / bf) if (qf > 0 and bf > 0 and math.isfinite(qf) and math.isfinite(bf)) else None
        if self.outcome is None and self.g0 is not None and j >= 1 and btv >= self.g0 + OUTCOME_S and not self.bad:
            self.outcome = (self.am_hi[j] / self.ppre[0], self.ppre[j] / self.ppre[0])

    def outcome_now(self) -> Optional[tuple[float, float]]:
        """6 h outcome: exact once a print at/after g0 + 6 h is ingested, else the estimate from the current state."""
        if self.outcome is not None:
            return self.outcome
        n = len(self.slot)
        if self.bad or n < 2 or self.est is None:
            return None
        return (max(self.am_hi[n - 1], self.est[2]) / self.ppre[0], self.est[2] / self.ppre[0])

    def free(self) -> None:
        self.n_final = len(self.slot)
        for a in ("slot", "bt", "isb", "sol", "tok", "q", "b", "th", "qpre", "ppre", "cs_vol", "cs_bs", "cs_ss", "cs_nb", "cs_new", "cs_any",
                  "cs_crs", "cs_crb", "csl", "lp", "am_hi", "am_lo", "hold", "seen_any", "seen_buy"):
            setattr(self, a, None)
        self.dead = True


def _nanmedian(a: np.ndarray) -> float:
    a = a[~np.isnan(a)]
    return float(np.median(a)) if len(a) else NAN


class FeatureEngine:
    def __init__(self, ledger: Optional[LedgerProvider] = None) -> None:
        self.ledger = ledger
        self.clock = SlotClock()
        self._info: dict[str, _Info] = {}
        self._v: dict[str, float] = {}
        self._pools: dict[str, _Pool] = {}
        self._rejected: dict[str, int] = {}
        self._mint_first: dict[str, tuple[int, str, int]] = {}
        self._cr_pools: dict[str, list[_Pool]] = {}
        # as-of tables (12_passC)
        self._by_cr: dict[str, list[int]] = {}
        self._g_cr: dict[str, list[int]] = {}
        self._w_c: dict[str, list[int]] = {}
        self._w_g: dict[str, list[int]] = {}
        self._s_c: dict[str, list[int]] = {}
        self._all_c: list[int] = []
        self._all_g: list[int] = []
        self._mk: dict[int, list] = {}           # minute -> [n_ps, v_ps, n_bd, v_bd]
        self._ledger_cache: dict[str, Any] = {}
        self.stats = {"trades": 0, "ps_rows": 0, "bad_pools": 0}

    # ------------------------------------------------------------------ events
    def set_pool_v(self, pool: str, v_lamports: Optional[float]) -> None:
        if v_lamports is not None:
            self._v[pool] = float(v_lamports)

    def on_block(self, slot: int, block_time: Optional[int]) -> None:
        if block_time is not None:
            self.clock.add(slot, int(block_time))

    def on_create(self, mint: str, creator: Optional[str], name: Optional[str], symbol: Optional[str], block_time: Optional[int],
                  mayhem: Optional[bool] = None) -> None:
        if block_time is None:
            return
        info = self._info.get(mint)
        if info is None:
            info = self._info[mint] = _Info()
        if info.has_create:
            return                                   # first create wins (min slot)
        info.has_create = True; info.creator = creator; info.cbt = int(block_time); info.name = name; info.mayhem = mayhem
        info.sym = (symbol if symbol is not None else "").lower().strip()
        info.words = words(name, symbol)
        bisect.insort(self._all_c, info.cbt)
        if creator is not None:
            bisect.insort(self._by_cr.setdefault(creator, []), info.cbt)
        for w in info.words:
            bisect.insort(self._w_c.setdefault(w, []), info.cbt)
        if info.sym:
            bisect.insort(self._s_c.setdefault(info.sym, []), info.cbt)
        P = self._pools.get(self._pool_of_mint(mint) or "")
        if P is not None and P.creator is None:
            P.creator = creator

    def _pool_of_mint(self, mint: str) -> Optional[str]:
        mf = self._mint_first.get(mint)
        return mf[1] if mf else None

    def on_graduation(self, mint: str, block_time: Optional[int]) -> None:
        if block_time is None:
            return
        info = self._info.get(mint)
        if info is None:
            info = self._info[mint] = _Info()
        if info.gbt is not None:
            return
        info.gbt = int(block_time)
        info.bc_frozen = (info.bc_n, len(info.bc_traders), info.bc_buy, info.bc_sell)
        info.bc_traders = set()
        if info.has_create:
            bisect.insort(self._all_g, info.gbt)
            if info.creator is not None:
                bisect.insort(self._g_cr.setdefault(info.creator, []), info.gbt)
            for w in info.words:
                bisect.insort(self._w_g.setdefault(w, []), info.gbt)
        P = self._pools.get(self._pool_of_mint(mint) or "")
        if P is not None:
            P.g0 = info.gbt
            self._register(P)

    def on_trade_row(self, r: Mapping[str, Any]) -> None:
        v = r.get("v_lamports")
        if v is not None and r.get("pool") is not None:
            self._v[r["pool"]] = float(v)
        self.on_trade(r["venue"], r["mint"], r["trader"], r["side"] == "buy", r["sol_lamports"], r["token_raw"], r["quote_reserve"],
                      r["base_reserve"], r.get("pool"), r["slot"], r.get("block_time"))

    def on_trade(self, venue: str, mint: str, trader: str, is_buy: bool, sol_lamports: float, token_raw: float, quote_reserve: float,
                 base_reserve: float, pool: Optional[str], slot: int, block_time: Optional[int]) -> None:
        self.stats["trades"] += 1
        if block_time is not None:
            self.clock.add(slot, block_time)
        if mint == WSOL:
            return
        ok_row = block_time is not None and base_reserve is not None and base_reserve > 0 and quote_reserve is not None and quote_reserve == quote_reserve
        if ok_row:
            m = block_time // 60 * 60
            e = self._mk.get(m)
            if e is None:
                e = self._mk[m] = [0, 0.0, 0, 0.0]
            if venue == VENUE_PUMPSWAP:
                e[0] += 1; e[1] += sol_lamports
            elif venue == VENUE_BONDING:
                e[2] += 1; e[3] += sol_lamports
        if venue == VENUE_BONDING:
            if ok_row:
                info = self._info.get(mint)
                if info is None:
                    info = self._info[mint] = _Info()
                if info.gbt is None:
                    info.bc_n += 1
                    info.bc_traders.add(trader)
                    if is_buy:
                        info.bc_buy += sol_lamports
                    else:
                        info.bc_sell += sol_lamports
            return
        if venue != VENUE_PUMPSWAP:
            return
        self.stats["ps_rows"] += 1
        P = self._pools.get(pool)
        if P is None:
            if pool in self._rejected:
                return
            key = (slot, pool)
            cur = self._mint_first.get(mint)
            canonical = cur is None or key < (cur[0], cur[1])
            if canonical:
                if cur is not None:
                    old = self._pools.get(cur[1])
                    if old is not None:
                        old.eligible = False
                self._mint_first[mint] = (slot, pool, block_time if block_time is not None else -1)
            V = self._v.get(pool)
            if not canonical or V is None or not (V_LO <= V <= V_HI):
                self._rejected[pool] = block_time if block_time is not None else -1
                return
            info = self._info.get(mint)
            P = _Pool(pool, mint, V, info.creator if info is not None else None)
            if info is not None and info.gbt is not None:
                P.g0 = info.gbt
            self._pools[pool] = P
            P.add(slot, block_time, is_buy, float(sol_lamports), float(token_raw), float(quote_reserve), float(base_reserve), trader)
            self._register(P)
            return
        if P.eligible is False or P.dead:
            return
        P.add(slot, block_time, is_buy, float(sol_lamports), float(token_raw), float(quote_reserve), float(base_reserve), trader)
        if P.eligible is None:
            self._register(P)

    def _register(self, P: _Pool) -> None:
        """Decide universe membership once graduation and the first print are both known (10_meta universe rule)."""
        if P.eligible is not None or P.g0 is None or P.first_bt is None:
            return
        mf = self._mint_first.get(P.mint)
        d = P.first_bt - P.g0
        P.eligible = bool(mf is not None and mf[1] == P.pool and FIRST_PRINT_WINDOW[0] <= d <= FIRST_PRINT_WINDOW[1] and V_LO <= P.V <= V_HI)
        if not P.eligible:
            self._pools.pop(P.pool, None)
            self._rejected[P.pool] = P.first_bt
            return
        if P.bt and P.bt[0] < 0:                      # 11_passA: a null first block_time becomes the graduation time
            P.bt[0] = P.g0
            for k in range(1, len(P.bt)):
                if P.bt[k] < P.bt[k - 1]:
                    P.bt[k] = P.bt[k - 1]
        if P.creator is not None:
            self._cr_pools.setdefault(P.creator, []).append(P)

    def expire(self, now_bt: int) -> int:
        """Free pool arrays older than the decision window; keep the 6 h outcome for the creator record. Returns pools freed."""
        n = 0
        for pid in [p for p, P in self._pools.items() if (P.g0 is not None and now_bt > P.g0 + GRID_END + 3600) or
                    (P.g0 is None and P.first_bt is not None and now_bt > P.first_bt + 6 * 3600)]:
            P = self._pools.pop(pid)
            if P.outcome is None and P.eligible:
                P.outcome = P.outcome_now()
            P.free(); n += 1
        for m in [m for m, v in self._mk.items() if m < now_bt - 2 * 3600]:
            del self._mk[m]
        for p in [p for p, bt in self._rejected.items() if bt != -1 and bt < now_bt - 86400]:
            del self._rejected[p]
        self.clock.prune(now_bt - 3 * 3600)
        return n

    # ------------------------------------------------------------------ queries
    def alive_pools(self, T: int) -> list[str]:
        return [pid for pid, P in self._pools.items() if P.eligible and P.g0 is not None and P.g0 + GRID_START <= T <= P.g0 + GRID_END]

    def _ledger_for(self, day: str):
        if self.ledger is None:
            return None
        if day not in self._ledger_cache:
            self._ledger_cache = {day: self.ledger.snapshot_for_day(day)}
        return self._ledger_cache[day]

    def features_at(self, pool: str, T: int, sd: Optional[int] = None) -> Optional[Features]:
        P = self._pools.get(pool)
        if P is None or not P.eligible or P.dead or P.bad:
            return None
        T = int(T)
        if sd is None:
            sd = self.clock.decision_slot(T)
            if sd is None:
                return None
        n = len(P.slot)
        i1 = bisect.bisect_left(P.slot, sd)
        bt = P.bt
        i60 = min(bisect.bisect_left(bt, T - 3600), i1)
        if i1 - i60 <= 0:
            return None
        if i1 == n and P.est is None:
            return None
        g0 = P.g0
        bl = bisect.bisect_left
        i1m = min(bl(bt, T - 60), i1); i5 = min(bl(bt, T - 300), i1); i10 = min(bl(bt, T - 600), i1); i15 = min(bl(bt, T - 900), i1)
        V = P.V

        def price(i: int) -> float:
            return P.ppre[i] if i < n else P.est[2]

        qstate = P.qpre[i1] if i1 < n else P.est[0]
        spot = price(i1)
        cv, cb, cs_ = P.cs_vol, P.cs_bs, P.cs_ss
        v1 = (cv[i1] - cv[i1m]) / 1e9; v5 = (cv[i1] - cv[i5]) / 1e9; v15 = (cv[i1] - cv[i15]) / 1e9; v60 = (cv[i1] - cv[i60]) / 1e9
        bs5 = (cb[i1] - cb[i5]) / 1e9; ss5 = (cs_[i1] - cs_[i5]) / 1e9
        bsp5 = (cb[i5] - cb[i10]) / 1e9; ssp5 = (cs_[i5] - cs_[i10]) / 1e9
        nb5 = P.cs_nb[i1] - P.cs_nb[i5]; n5 = float(i1 - i5)
        new5 = P.cs_new[i1] - P.cs_new[i5]; newp5 = P.cs_new[i5] - P.cs_new[i10]
        qreal = (qstate - V) / 1e9
        p5, p15, p60, p_grad = price(i5), price(i15), price(i60), P.ppre[0]
        surge = v5 / (v60 / 12.0 + 0.05)
        net5 = bs5 - ss5; netp5 = bsp5 - ssp5
        f: dict[str, float] = dict(age=float(T - g0), v1=v1, v5=v5, v15=v15, v60=v60, bs5=bs5, ss5=ss5, net5=net5, netp5=netp5, nb5=nb5, n5=n5,
                                   new5=new5, newp5=newp5, qreal=qreal, r5=spot / p5 - 1, r15=spot / p15 - 1, r60=spot / p60 - 1,
                                   rgrad=spot / p_grad, surge=surge, hod=float((T % 86400) // 3600))
        a, s_thr, k_thr = SUPERSET
        pre = bool(v5 >= a and (surge >= s_thr or new5 >= k_thr))
        if not pre:
            return self._finish(P, T, sd, i1, f, False)
        # ---- trajectory shape
        hi = spot if i1 - 1 < 1 else max(P.am_hi[i1 - 1], spot)
        lo = spot if i1 - 1 < 1 else min(P.am_lo[i1 - 1], spot)

        def csl_at(k: int) -> float:
            if k < n:
                return P.csl[k]
            d = math.log(P.est[2]) - P.lp[n - 1]
            return P.csl[n - 1] + d * d

        c1 = csl_at(i1)
        f["r_ath"] = spot / hi; f["r_atl"] = spot / lo; f["ath_grad"] = hi / p_grad
        f["vol15"] = math.sqrt(max(c1 - csl_at(i15), 0.0) / max(i1 - i15, 1))
        f["vol60"] = math.sqrt(max(c1 - csl_at(i60), 0.0) / max(i1 - i60, 1))
        f["ncum"] = float(i1); f["ntr_cum"] = P.cs_any[i1]; f["vcum"] = cv[i1] / 1e9
        isb, sol, th = P.isb, P.sol, P.th
        f["maxbuy5"] = (max((sol[k] if isb[k] else 0.0) for k in range(i5, i1)) / 1e9) if i1 > i5 else 0.0
        f["avgbuy5"] = bs5 / max(nb5, 1); f["buyshare5"] = bs5 / max(v5, 1e-9)
        f["cr_sold"] = P.cs_crs[i1] / 1e15; f["cr_bought"] = P.cs_crb[i1] / 1e15
        # ---- holders (PumpSwap flows only)
        if i1 == n:
            hold = P.hold
        else:
            hold = {}
            for k in range(i1):
                hold[th[k]] = hold.get(th[k], 0.0) + (P.tok[k] if isb[k] else -P.tok[k])
        pos = np.sort(np.array([x for x in hold.values() if x > 0], dtype=np.float64))[::-1]
        tot = pos.sum() if len(pos) else 0.0
        f["h_npos"] = float(len(pos))
        f["h_top1"] = float(pos[:1].sum() / tot) if tot > 0 else NAN
        f["h_top5"] = float(pos[:5].sum() / tot) if tot > 0 else NAN
        f["h_top10"] = float(pos[:10].sum() / tot) if tot > 0 else NAN
        f["h_pos_frac_supply"] = float(tot / 1e15)
        sidx = [k for k in range(i5, i1) if not isb[k]]
        if sidx:
            sb = P.seen_buy
            num = np.array([sol[k] for k in sidx if sb.get(th[k], 1 << 60) >= i1], dtype=np.float64).sum()
            den = np.array([sol[k] for k in sidx], dtype=np.float64).sum()
            f["sell_curveholder_share"] = float(num / max(den, 1))
        else:
            f["sell_curveholder_share"] = NAN
        # ---- wallet intelligence
        led = self._ledger_for(utc_day(T))
        if led is not None:
            for wname, ia in (("5", i5), ("15", i15)):
                skill_sol = {}
                for side, want in (("b", True), ("s", False)):
                    pre_ = f"w{side}{wname}_"
                    acc: dict[str, float] = {}
                    for k in range(ia, i1):
                        if isb[k] == want:
                            acc[th[k]] = acc.get(th[k], 0.0) + sol[k]
                    if not acc:
                        f[pre_ + "n"] = 0.0; skill_sol[side] = 0.0
                        continue
                    users = list(acc)
                    usol = np.array([acc[u] for u in users], dtype=np.float64)
                    st = np.full((len(users), 7), np.nan)
                    known = np.zeros(len(users), dtype=bool)
                    for i, u in enumerate(users):
                        row = led.get(u)
                        if row is not None:
                            st[i] = row; known[i] = True
                    nn, nbond, cash, nwin, nrt, ndays, buy = st.T
                    with np.errstate(invalid="ignore", divide="ignore"):
                        skill = known & (cash > 0)
                        f[pre_ + "n"] = float(len(users))
                        f[pre_ + "new"] = float((~known).mean())
                        f[pre_ + "skill"] = float(skill.sum() / max(known.sum(), 1))
                        f[pre_ + "skill_sol"] = float(usol[skill].sum() / max(usol.sum(), 1))
                        skill_sol[side] = float(usol[skill].sum() / 1e9)
                        f[pre_ + "cash_med"] = _nanmedian(cash) if known.any() else NAN
                        f[pre_ + "roi_med"] = _nanmedian(cash / np.maximum(buy, 0.1)) if known.any() else NAN
                        wr = nwin / np.where(nrt >= 5, nrt, np.nan)
                        f[pre_ + "winrate"] = float(np.nanmean(wr)) if np.isfinite(wr).any() else NAN
                        f[pre_ + "bot"] = float((known & (nn / np.maximum(ndays, 1) >= 500)).mean())
                        f[pre_ + "sniper"] = float((known & (nn >= 20) & (nbond / np.maximum(nn, 1) >= 0.9)).mean())
                        f[pre_ + "actmed"] = _nanmedian(nn / np.maximum(ndays, 1)) if known.any() else NAN
                f[f"sk_net{wname}"] = skill_sol["b"] - skill_sol["s"]
        self._passc(P, T, f)
        return self._finish(P, T, sd, i1, f, True)

    def _finish(self, P: _Pool, T: int, sd: int, i1: int, f: dict, pre: bool) -> Features:
        vec = np.full(N_FEATURES, np.nan)
        for k, v in f.items():
            vec[FIDX[k]] = v
        stage1 = False
        h1 = NAN
        if pre:
            v5, sg, nw, qr = (float(np.float32(f[x])) for x in ("v5", "surge", "new5", "qreal"))   # scoring reads the float32 export
            a, s, k, L = STAGE1
            stage1 = bool((((v5 >= a) and (sg >= s)) or (nw >= k)) and (qr >= L))
            h1 = f["h_top1"]
        cap_ok = bool(h1 == h1 and float(np.float32(h1)) <= H_TOP1_CAP)
        return Features(P.pool, P.mint, T, sd, i1, vec, pre, stage1, h1, cap_ok)

    # ---- pass C: pre-grad, creator record, narrative heat, market activity (all strictly as-of T)
    def _passc(self, P: _Pool, T: int, f: dict) -> None:
        info = self._info.get(P.mint)
        if info is None:
            info = _Info()
        g = P.g0
        if info.bc_frozen is not None:
            n_, nt_, buy_, sell_ = info.bc_frozen
            if n_:
                f["bc_n_trades"] = float(n_); f["bc_n_traders"] = float(nt_); f["bc_buy_sol"] = buy_ / 1e9; f["bc_sell_sol"] = sell_ / 1e9
        if info.has_create:
            f["bc_dur"] = float(g - info.cbt)
            f["mayhem"] = float(bool(info.mayhem)) if info.mayhem is not None else NAN
        f["has_create"] = float(info.has_create)
        if info.has_create:
            ch = info.creator
            cbt = info.cbt
            a = self._by_cr.get(ch, [])
            cnt = lambda arr, lo, hi: bisect.bisect_left(arr, hi) - bisect.bisect_left(arr, lo)
            f["cr_prev_creates"] = float(bisect.bisect_left(a, cbt))
            f["cr_creates_24h"] = float(cnt(a, T - 86400, T) - (1 if T - 86400 <= cbt < T else 0))
            ga = self._g_cr.get(ch, [])
            f["cr_prev_grads"] = float(bisect.bisect_left(ga, T) - (1 if g < T else 0))
            ents = []
            for Q in self._cr_pools.get(ch, ()):
                if Q is P or Q.g0 is None or Q.g0 + OUTCOME_S > T:
                    continue
                o = Q.outcome if Q.outcome is not None else (Q.outcome_now() if not Q.dead else None)
                if o is not None:
                    ents.append((Q.g0 + OUTCOME_S, o))
            ents.sort(key=lambda e: e[0])
            f["cr_known_out"] = float(len(ents))
            if ents:
                mx = np.array([math.log(max(o[0], 1e-9)) for _, o in ents]); en = np.array([math.log(max(o[1], 1e-9)) for _, o in ents])
                bg = np.array([1.0 if o[0] >= 2 else 0.0 for _, o in ents])
                f["cr_lmax6"] = float(mx.mean()); f["cr_lend6"] = float(en.mean()); f["cr_big6"] = float(bg.mean())
            w = info.words
            f["nar_cr1h"] = float(max([cnt(self._w_c.get(x, []), T - 3600, T) for x in w] or [0]))
            f["nar_cr6h"] = float(max([cnt(self._w_c.get(x, []), T - 6 * 3600, T) for x in w] or [0]))
            f["nar_gr6h"] = float(max([cnt(self._w_g.get(x, []), T - 6 * 3600, T) for x in w] or [0]))
            f["nar_gr24h"] = float(max([cnt(self._w_g.get(x, []), T - 86400, T) for x in w] or [0]))
            f["nar_sym1h"] = float(cnt(self._s_c.get(info.sym, []), T - 3600, T)) if info.sym else 0.0
            f["nar_nwords"] = float(len(w))
        tot = [0, 0.0, 0, 0.0]
        for m in range((T - 3600 + 59) // 60 * 60, (T - 60 + 59) // 60 * 60, 60):      # minutes in [T-3600, T-60)
            e = self._mk.get(m)
            if e is not None:
                tot[0] += e[0]; tot[1] += e[1]; tot[2] += e[2]; tot[3] += e[3]
        f["mk_n_ps60"] = float(tot[0]); f["mk_v_ps60"] = tot[1] / 1e9; f["mk_n_bd60"] = float(tot[2]); f["mk_v_bd60"] = tot[3] / 1e9
        f["mk_grads60"] = float(bisect.bisect_left(self._all_g, T) - bisect.bisect_left(self._all_g, T - 3600))
        f["mk_creates60"] = float(bisect.bisect_left(self._all_c, T) - bisect.bisect_left(self._all_c, T - 3600))
