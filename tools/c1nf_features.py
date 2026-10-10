#!/usr/bin/env python3
"""C1-NF live feature engine: incremental, event-driven reproduction of C1's 107 stage-2 features.

Ground truth (frozen, do not edit): /data/mal/hunt-1008/c1-cascade-postgrad scripts/11_passA.py (pool flow, shape, holders, wallet
intelligence), scripts/12_passC.py (pre-grad, creator record, narrative heat, market activity), scripts/14_export.py (column order),
ml/rule.json (stage 1 = [1.0, 3.0, 20.0, 20.0]); C1-NF adds the cap h_top1 <= 0.5 (c1nf-verify/VERIFY.md). The October pricing convention
is EXP-025 section 2.4 (ARTIFACTS/exp025/event_v_map.py, loaded sha256-checked).

Decision rule: at every whole UTC minute T from graduation + 600 s to + 24 h, for each pool that printed in the last 60 min, features
use only canonical-pool PumpSwap prints with slot < SD, SD = the first slot whose block_time >= T.

V contract (`FeatureEngine(v_source=...)`, required, no default):
  * "event" (live, October): every PumpSwap print carries its OWN pre-trade event V (`virtual_quote_reserves`, the decoder's EVENT_V_KEYS;
    `on_trade(..., v_event=)`). V0 = the event V of the pool's first print (the universe V band is checked on it), and the engine writes
    quote_reserve := vault + V(t) - V0 with the pinned `event_v_map.map_quote_reserve`, so prices are vault + V(t) and `qreal` is the vault
    net of pending fees, exactly what pass A reads from the EXP-025 adapter tape. A first print with no event V rejects the pool
    (`v0_missing`); a later print with no event V marks the pool bad (`v_missing`): fail closed, counted, never silently priced on a stale V.
    The tip follower's `virtual_quote_reserve` (one cached pool-account read) is NOT event V and is ignored in this mode.
  * "const" (exploration tape, or the EXP-025 adapter tape whose quote_reserve is already mapped): one V0 per pool (set_pool_v, or the row's
    `v_lamports` / `virtual_quote_reserve` / `virtual_quote_reserves` in on_row), quote_reserve used as given. Correct only where V(t) = V0 or
    the column is already mapped; a row whose event V differs from V0 is counted (`const_v_drift_rows`). A pool whose first print arrives
    before its V0 is held (`v_pending`) and replayed when V0 arrives, up to V_PENDING_MAX_S / V_PENDING_MAX_ROWS (then `v_unknown`).

Event API (one call per decoded row, in (slot, tx_index, event_index) order):
    on_row(row, kind=None)                             tip-follower / walker row (trades, creates, migrations): stamps the clock from the
                                                       row's block_time, picks the V key of the mode, counts and drops malformed rows
    on_block(slot, block_time)                         clock only (on_trade / on_row feed the clock too)
    on_create(mint, creator, name, symbol, block_time, mayhem)
    on_graduation(mint, block_time)                    pump.fun 'complete' migration row
    on_trade(venue, mint, trader, is_buy, sol_lamports, token_raw, quote_reserve, base_reserve, pool, slot, block_time, v_event=None)
    set_pool_v(pool, v_lamports)                       "const" mode only: the pool's V0
Query:
    features_at(pool, T, sd=None) -> Features | None   None = pool not in the universe, not alive at T, bad, or no clock entry >= T
    alive_pools(T)                                     eligible pools inside the decision window of T
    health()                                           every counter (dropped rows by reason, pool rejections by reason, ledger state,
                                                       state sizes); nothing is dropped without a counter. strict=True raises instead.
Features.wallet_ok is False when no ledger snapshot for the decision's UTC day was available (a live caller must refuse to pick then: the
42 wallet features are NaN, a different input class from training). A missing snapshot is retried with backoff, never cached.

Differences a live engine cannot avoid (all measured in tools/c1nf_parity.py, none hidden):
  * C1's batch `spot`, `qreal`, `r5/r15/r60/rgrad`, `r_ath`, `vol*`, and the 6 h creator outcomes use the PRE-trade reserves of the first
    print with slot >= SD (a state the batch sees by looking one print ahead). A live engine has only prints with slot < SD, so it uses
    the same fee-model post-trade estimate the batch uses when no later print exists (G_FEE = 1.25%). If the next print is already
    ingested the engine uses its exact pre-trade reserves (this is the batch's behaviour, used by the parity harness "exact" mode).
  * bc_* are frozen at graduation (the batch aggregates the whole tape window per mint, including bonding rows after `complete`).
  * 11_passA drops a mint with fewer than 2 prints in D..D+2T01, and drops the WHOLE mint if any print in that window has invalid reserves
    (11_passA:106, 129-130). The engine cannot see later prints: it decides on the prints so far and marks the pool bad (no further
    decision) from the first invalid print on.
  * 11_passA clips SD to the last clock entry; the engine returns None when no block_time >= T has been seen yet.
  * Memory is bounded (expire): ungraduated mints idle for PRUNE_BONDING_IDLE_S lose their bonding-trader set (bc_n_traders becomes NaN
    if such a mint later graduates, counted `bc_traders_pruned_graduated`); mints idle for PRUNE_INFO_IDLE_S are forgotten and a later pool
    of such a mint is rejected (`info_pruned`); windowed as-of tables keep TABLE_KEEP_S; creator histories keep exact counts.
  * Only numpy is used on the hot path (no pandas).
"""
from __future__ import annotations

import bisect
import collections
from array import array
import hashlib
import importlib.util
import math
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

import numpy as np

NAN = float("nan")
WSOL = "So11111111111111111111111111111111111111112"
VENUE_BONDING = "pump_bonding"
VENUE_PUMPSWAP = "pumpswap"

V_EVENT = "event"                       # live: per-print event V, quote mapped per EXP-025 s2.4
V_CONST = "const"                       # exploration / adapter tape: one V0 per pool, quote_reserve as given
V_SOURCES = (V_EVENT, V_CONST)
EVENT_V_KEY = "virtual_quote_reserves"  # observe.trade_decode.EVENT_V_KEYS: the print's stored V BEFORE the trade (signed)
CONST_V_KEYS = ("v_lamports", "virtual_quote_reserve", "virtual_quote_reserves")   # "const" mode: the pool's V0, first non-null key
V_PENDING_MAX_S = 900                   # "const": hold a pool's prints this long for its V0 (the tip follower retries V at 30 s .. 300 s)
V_PENDING_MAX_ROWS = 20_000
LEDGER_RETRY_S = (5.0, 300.0)           # first retry after a missing snapshot, cap (doubling); monotonic seconds
LEDGER_KEEP_DAYS = 2                    # ledger snapshots cached (UTC days). c1nf_parity defers evaluations past midnight: 2. A live caller,
                                        # whose decision T only moves forward, passes 1 (tools/c1nf_shadow.build_engine)
PRUNE_BONDING_IDLE_S = 24 * 3600        # ungraduated mint with no bonding trade for this long: drop its trader set
PRUNE_INFO_IDLE_S = 3 * 86400           # ungraduated mint with no bonding trade or create for this long: forget it
TABLE_KEEP_S = 2 * 86400 + 4 * 3600     # windowed as-of tables (nar_*, mk_*): longest window 24 h + pool life 25 h + margin
ART_EXP025 = Path(__file__).resolve().parent.parent / "ARTIFACTS" / "exp025"
TRADE_FIELDS = ("mint", "trader", "sol_lamports", "token_raw")


class RowError(ValueError):
    """strict=True: a row the engine cannot ingest (it is counted under the same reason when strict=False)."""


_EVM: Any = None


def pinned_event_v_map():
    """ARTIFACTS/exp025/event_v_map.py, loaded only after its sha256 equals the SHA256SUMS line (fail closed on any difference)."""
    global _EVM
    if _EVM is None:
        sums = {}
        for ln in (ART_EXP025 / "SHA256SUMS").read_text().splitlines():
            if ln.strip():
                d, rel = ln.split(None, 1)
                sums[rel.strip()] = d
        path = ART_EXP025 / "event_v_map.py"
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if sums.get("event_v_map.py") != got:
            raise RuntimeError(f"{path}: sha256 {got} != SHA256SUMS {sums.get('event_v_map.py')}")
        spec = importlib.util.spec_from_file_location("exp025_event_v_map_pinned", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _EVM = m
    return _EVM


def _int_or_none(v: Any) -> Optional[int]:
    """An integer lamport value (int, or an integral finite float); bool, NaN, non-integral or anything else is None."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)) and math.isfinite(v) and float(v).is_integer():
        return int(v)
    return None


def _num(v: Any) -> bool:
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool) and math.isfinite(float(v))

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
    UTC day, nothing from the day itself. Return None when that snapshot does not exist (yet): in a replay of the first tape day that means
    "no prior day" (wallet features NaN, as in C1); live it means the nightly rollup has not finished. The engine never caches None: it
    asks again with backoff, and Features.wallet_ok tells the caller which case each decision was in. The live provider is
    AsofLedgerProvider.from_root (AsofLedger.open_for_day)."""

    def snapshot_for_day(self, day: str) -> Optional[LedgerSnapshot]: ...


class AsofLedgerAdapter:
    """Adapts tools/c1nf_wallet_ledger.AsofLedger (claude/c1nf-ledger, PR #502) to the engine's snapshot interface.

    The ledger is keyed by th = DuckDB hash(trader) (UBIGINT); the engine keys by pubkey, so the caller supplies `th_of(list[str]) ->
    uint64 array` (see duckdb_th_of). `asof.passa_matrix(th)` returns (known mask, float64 [n, 7] in PASSA_COLS == LEDGER_COLS order, NaN
    for unknown wallets): exactly the matrix 11_passA builds."""

    def __init__(self, asof: Any, th_of) -> None:
        self.asof, self.th_of = asof, th_of

    def lookup_many(self, traders: Sequence[str]):
        th = np.asarray(self.th_of(list(traders)), dtype=np.uint64)
        return self.asof.passa_matrix(th)

    def get(self, trader: str) -> Optional[Sequence[float]]:
        known, m = self.lookup_many([trader])
        return tuple(float(x) for x in m[0]) if known[0] else None


class AsofLedgerProvider:
    """LedgerProvider over per-day AsofLedger snapshots: open_day('YYYY-MM-DD') -> AsofLedger | None (asof-<day> = days strictly before).

    Live callers build it with `from_root(out_root, th_of)`: every day is opened through `AsofLedger.open_for_day(out_root, day)` (PR #502),
    the ledger's own reader for decision day D, which opens asof-<D> only and raises StaleLedger otherwise. Do not pass `AsofLedger.open`
    or a directory picker of your own as `open_day` on the live path.

    A second guard here: a snapshot whose MANIFEST `asof_day` is not the requested day is refused (None, counted in `refused`). A decision
    on day D must not silently use asof-(D-1), which lacks day D-1 (PR #502 review). A ledger without a manifest attribute (a duck-typed
    test double) is used as is."""

    def __init__(self, open_day, th_of) -> None:
        self.open_day, self.th_of = open_day, th_of
        self.refused = 0
        self.stale = 0

    @classmethod
    def from_root(cls, out_root, th_of, *, verify: bool = False, ledger_cls: Any = None, stale_exc: Any = None) -> "AsofLedgerProvider":
        """The live provider over a tools/c1nf_wallet_ledger.py output root. open_day(D) = AsofLedger.open_for_day(out_root, D, verify).

        StaleLedger (asof-<D> not built yet: 00:00Z until the nightly rollup finishes, or the rollup failed) -> None, counted in `stale`;
        the engine never caches None and asks again with backoff, and Features.wallet_ok is False meanwhile. Never falls back to asof-<D-1>.
        Any other Refused (schema, sha256 with verify=True) propagates; the engine counts it as ledger_errors and retries the same way.
        ledger_cls / stale_exc replace the PR #502 classes in tests only."""
        if ledger_cls is None or stale_exc is None:
            from tools.c1nf_wallet_ledger import AsofLedger, StaleLedger
            ledger_cls = AsofLedger if ledger_cls is None else ledger_cls
            stale_exc = StaleLedger if stale_exc is None else stale_exc
        root = str(out_root)
        prov: Optional["AsofLedgerProvider"] = None

        def open_day(day: str):
            try:
                return ledger_cls.open_for_day(root, day, verify=verify)
            except stale_exc:
                prov.stale += 1
                return None

        prov = cls(open_day, th_of)
        return prov

    def snapshot_for_day(self, day: str):
        a = self.open_day(day)
        if a is None:
            return None
        man = getattr(a, "manifest", None)
        if man is not None and (not isinstance(man, Mapping) or man.get("asof_day") != day):
            self.refused += 1
            return None
        return AsofLedgerAdapter(a, self.th_of)


def duckdb_th_of(con, cache_max: int = 2_000_000):
    """th_of backed by DuckDB hash(VARCHAR) (the pinned function; the ledger module refuses a duckdb whose hash differs).
    Each trader is hashed once (one query for the unknown ones only) and cached; the cache is cleared when it passes cache_max."""
    cache: dict[str, int] = {}

    def th_of(traders: Sequence[str]) -> np.ndarray:
        need = list({t for t in traders if t not in cache})
        if need:
            if len(cache) + len(need) > cache_max:
                cache.clear()
            for a, b in con.execute("SELECT x, hash(x) FROM (SELECT unnest(?) AS x)", [need]).fetchall():
                cache[a] = b
        return np.array([cache[t] for t in traders], dtype=np.uint64)
    th_of.cache = cache
    return th_of


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
    """wallet_ok: a ledger snapshot for utc_day(t) was available (ledger_day = that day, else None). False means the 42 wallet features are
    NaN because the snapshot was missing (live: the rollup is late), and a live caller must not pick on this row."""
    __slots__ = ("pool", "mint", "t", "sd", "i1", "vec", "pre", "stage1", "h_top1", "cap_ok", "wallet_ok", "ledger_day")

    def __init__(self, pool, mint, t, sd, i1, vec, pre, stage1, h_top1, cap_ok, wallet_ok=False, ledger_day=None):
        self.pool, self.mint, self.t, self.sd, self.i1 = pool, mint, t, sd, i1
        self.vec, self.pre, self.stage1, self.h_top1, self.cap_ok = vec, pre, stage1, h_top1, cap_ok
        self.wallet_ok, self.ledger_day = wallet_ok, ledger_day

    def as_float32(self) -> np.ndarray:
        return self.vec.astype(np.float32)

    def get(self, name: str) -> float:
        return float(self.vec[FIDX[name]])


class _Info:
    __slots__ = ("creator", "cbt", "name", "sym", "words", "mayhem", "has_create", "gbt", "bc_n", "bc_buy", "bc_sell", "bc_traders", "bc_frozen",
                 "last_bt", "bc_pruned", "revived")

    def __init__(self) -> None:
        self.creator = None; self.cbt = None; self.name = None; self.sym = ""; self.words = []; self.mayhem = None
        self.has_create = False; self.gbt = None
        self.bc_n = 0; self.bc_buy = 0.0; self.bc_sell = 0.0; self.bc_traders = set(); self.bc_frozen = None
        self.last_bt = None        # last create / bonding-trade / graduation block_time (memory pruning)
        self.bc_pruned = False     # bc_traders dropped while idle: bc_n_traders is unknown if the mint graduates later
        self.revived = False       # this mint was forgotten (PRUNE_INFO_IDLE_S) and seen again: its pool is rejected (`info_pruned`)


def _isb_code(isb: Any) -> int:
    """The byte stored in `_Pool.isb` for one print's is_buy value. Every read of the column gives what the value itself gave when the column
    was a list: truthiness is `code & 1`, and `code == want` (want True or False) is `isb == want`. 0 = False, 1 = True, 2 = falsy but not
    == False (None: a null side), 3 = truthy but not == True. A bool or numpy bool is always 0 or 1."""
    if isb == True:  # noqa: E712 - equality, not identity: numpy bool and 1 count
        return 1
    if isb == False:  # noqa: E712
        return 0
    return 3 if isb else 2


class _Pool:
    """One pool's prints, as typed columns (memory, job #608): array('q') for slot and bt, a bytearray of _isb_code for isb, array('d') for the
    floats (a double round-trips exactly, and every sum is formed in the same order as with lists, so every value is bit-equal). Kept as
    scalars: q0 (the first print's quote_reserve as ingested) and lp_last (log price of the last print; only lp[j-1] and lp[n-1] were read).
    Not kept: per-print q and b (never read), seen_any (its keys were exactly the keys of `hold`)."""
    __slots__ = ("pool", "mint", "V", "creator", "slot", "bt", "isb", "sol", "tok", "q0", "th", "qpre", "ppre", "cs_vol", "cs_bs", "cs_ss",
                 "cs_nb", "cs_new", "cs_any", "cs_crs", "cs_crb", "csl", "lp_last", "am_hi", "am_lo", "hold", "seen_buy", "first_slot",
                 "first_bt", "g0", "eligible", "bad", "bad_reason", "est", "outcome", "dead", "n_final")

    def __init__(self, pool: str, mint: str, V: float, creator: Optional[str]) -> None:
        self.pool, self.mint, self.V, self.creator = pool, mint, float(V), creator
        self.slot = array("q"); self.bt = array("q"); self.isb = bytearray(); self.sol = array("d"); self.tok = array("d")
        self.q0: Optional[float] = None; self.th: list[str] = []
        self.qpre = array("d"); self.ppre = array("d")
        self.cs_vol = array("d", [0.0]); self.cs_bs = array("d", [0.0]); self.cs_ss = array("d", [0.0]); self.cs_nb = array("d", [0.0])
        self.cs_new = array("d", [0.0]); self.cs_any = array("d", [0.0]); self.cs_crs = array("d", [0.0]); self.cs_crb = array("d", [0.0])
        self.csl = array("d"); self.lp_last = NAN; self.am_hi = array("d", [NAN]); self.am_lo = array("d", [NAN])
        self.hold: dict[str, float] = {}; self.seen_buy: dict[str, int] = {}
        self.first_slot = None; self.first_bt = None; self.g0 = None; self.eligible = None; self.bad = False; self.bad_reason = None
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
        if j == 0:
            self.q0 = q
        self.slot.append(slot); self.bt.append(btv); self.isb.append(_isb_code(isb)); self.sol.append(sol); self.tok.append(tok)
        self.th.append(th); self.qpre.append(qp); self.ppre.append(p)
        self.cs_vol.append(self.cs_vol[-1] + sol)
        self.cs_bs.append(self.cs_bs[-1] + (sol if isb else 0.0))
        self.cs_ss.append(self.cs_ss[-1] + (0.0 if isb else sol))
        self.cs_nb.append(self.cs_nb[-1] + (1.0 if isb else 0.0))
        first_any = th not in self.hold              # hold gets every trader of every print (below), so its keys are the traders seen
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
            d = lpj - self.lp_last
            self.csl.append(self.csl[j - 1] + d * d)
            self.am_hi.append(p if j == 1 else max(self.am_hi[j - 1], p))
            self.am_lo.append(p if j == 1 else min(self.am_lo[j - 1], p))
        self.lp_last = lpj
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
        for a in ("slot", "bt", "isb", "sol", "tok", "th", "qpre", "ppre", "cs_vol", "cs_bs", "cs_ss", "cs_nb", "cs_new", "cs_any",
                  "cs_crs", "cs_crb", "csl", "am_hi", "am_lo", "hold", "seen_buy"):
            setattr(self, a, None)
        self.dead = True


def _nanmedian(a: np.ndarray) -> float:
    a = a[~np.isnan(a)]
    return float(np.median(a)) if len(a) else NAN


class FeatureEngine:
    def __init__(self, ledger: Optional[LedgerProvider] = None, *, v_source: str, strict: bool = False,
                 ledger_retry_s: tuple[float, float] = LEDGER_RETRY_S, prune_bonding_idle_s: Optional[int] = PRUNE_BONDING_IDLE_S,
                 prune_info_idle_s: Optional[int] = PRUNE_INFO_IDLE_S, monotonic: Callable[[], float] = time.monotonic,
                 ledger_keep_days: int = LEDGER_KEEP_DAYS) -> None:
        if v_source not in V_SOURCES:
            raise ValueError(f"v_source must be one of {V_SOURCES}, got {v_source!r}")
        if isinstance(ledger_keep_days, bool) or not isinstance(ledger_keep_days, int) or ledger_keep_days < 1:
            raise ValueError(f"ledger_keep_days must be an int >= 1, got {ledger_keep_days!r}")
        self.ledger_keep_days = ledger_keep_days
        self.v_source = v_source
        self.strict = strict
        self._map_q = pinned_event_v_map().map_quote_reserve if v_source == V_EVENT else None
        self.ledger = ledger
        self.ledger_retry_s = ledger_retry_s
        self.prune_bonding_idle_s, self.prune_info_idle_s = prune_bonding_idle_s, prune_info_idle_s
        self._mono = monotonic
        self.clock = SlotClock()
        self._hw_bt: Optional[int] = None         # highest block_time seen (rejection stamps, pruning)
        self._info: dict[str, _Info] = {}
        self._pruned: dict[str, int] = {}         # forgotten ungraduated mint -> block_time it was forgotten
        self._v: dict[str, float] = {}            # "const": pool -> V0
        self._vpend: dict[str, dict] = {}         # "const": pool whose first print came before its V0 -> held prints
        self._pools: dict[str, _Pool] = {}
        self._rejected: dict[str, int] = {}
        self._mint_first: dict[str, tuple[int, str, int]] = {}
        self._cr_pools: dict[str, list[_Pool]] = {}
        # as-of tables (12_passC). _by_cr / _g_cr: full creator history (exact counts since tape start). The others are windowed.
        self._by_cr: dict[str, list[int]] = {}
        self._g_cr: dict[str, list[int]] = {}
        self._w_c: dict[str, list[int]] = {}
        self._w_g: dict[str, list[int]] = {}
        self._s_c: dict[str, list[int]] = {}
        self._all_c: list[int] = []
        self._all_g: list[int] = []
        self._mk: dict[int, list] = {}           # minute -> [n_ps, v_ps, n_bd, v_bd]
        self._ledger_cache: dict[str, Any] = {}
        self._ledger_next: dict[str, tuple[float, float]] = {}
        self.stats: collections.Counter = collections.Counter(trades=0, ps_rows=0, bad_pools=0)
        self.pool_rejects: collections.Counter = collections.Counter()

    # ------------------------------------------------------------------ bookkeeping
    def _drop(self, reason: str, detail: Any = None) -> None:
        """Count a row the engine cannot use; strict=True raises RowError instead."""
        self.stats["drop_" + reason] += 1
        if self.strict:
            raise RowError(f"{reason}: {detail!r}" if detail is not None else reason)

    def _reject(self, pool: str, reason: str, bt: Optional[int]) -> None:
        self._rejected[pool] = bt if bt is not None else (self._hw_bt if self._hw_bt is not None else -1)
        self.pool_rejects[reason] += 1
        self._v.pop(pool, None)

    def _mark_bad(self, P: _Pool, reason: str) -> None:
        if not P.bad:
            P.bad = True
            P.bad_reason = reason
            self.stats["bad_pools"] += 1
            self.stats["bad_pool_" + reason] += 1

    def _seen_bt(self, bt: Optional[int]) -> None:
        if bt is not None and (self._hw_bt is None or bt > self._hw_bt):
            self._hw_bt = bt

    def _info_of(self, mint: str) -> _Info:
        info = self._info.get(mint)
        if info is None:
            info = self._info[mint] = _Info()
            if mint in self._pruned:
                info.revived = True
                self.stats["pruned_mint_revived"] += 1
        return info

    def health(self) -> dict:
        """Every counter: dropped rows (drop_*), pool rejections by reason, bad pools by reason, ledger state, and state sizes."""
        return {"v_source": self.v_source, "stats": dict(self.stats), "pool_rejects": dict(self.pool_rejects),
                "ledger": {"cached_days": sorted(self._ledger_cache), "retry": {d: round(w, 3) for d, (_, w) in self._ledger_next.items()}},
                "sizes": self.state_sizes()}

    def state_sizes(self) -> dict:
        return {"pools": len(self._pools), "infos": len(self._info), "bc_traders": sum(len(i.bc_traders) for i in self._info.values() if i.bc_traders),
                "pruned_mints": len(self._pruned), "v": len(self._v), "v_pending": len(self._vpend), "rejected": len(self._rejected),
                "mint_first": len(self._mint_first), "cr_pools": sum(len(v) for v in self._cr_pools.values()), "market_minutes": len(self._mk),
                "w_c": sum(len(v) for v in self._w_c.values()), "w_g": sum(len(v) for v in self._w_g.values()),
                "s_c": sum(len(v) for v in self._s_c.values()), "all_c": len(self._all_c), "all_g": len(self._all_g),
                "by_cr": sum(len(v) for v in self._by_cr.values()), "g_cr": sum(len(v) for v in self._g_cr.values()), "clock": len(self.clock.bts)}

    def held_sizes(self) -> dict:
        """What drives memory (job #608): prints held in the per-print columns of the pools not yet freed, and their (pool, trader) pairs."""
        prints = pairs = 0
        for P in self._pools.values():
            if P.slot is not None:
                prints += len(P.slot)
                pairs += len(P.hold)
        return {"held_prints": prints, "held_pairs": pairs}

    # ------------------------------------------------------------------ events
    def set_pool_v(self, pool: str, v_lamports: Optional[float]) -> None:
        """"const" mode: the pool's V0. A pool already started keeps its V0; held prints of a pool waiting for its V0 are replayed now.
        "event" mode: ignored and counted (V0 is the first print's own event V)."""
        if self.v_source == V_EVENT:
            self.stats["set_pool_v_ignored_event_mode"] += 1
            return
        if v_lamports is None or not _num(v_lamports) or pool in self._rejected:
            return
        if pool in self._pools:
            if float(v_lamports) != self._pools[pool].V:
                self.stats["const_v_changed_after_start"] += 1
            return
        self._v[pool] = float(v_lamports)
        pend = self._vpend.pop(pool, None)
        if pend is not None:
            self.stats["v_pending_resolved"] += 1
            for args in pend["rows"]:
                self._on_ps(*args)

    def on_block(self, slot: int, block_time: Optional[int]) -> None:
        if block_time is not None:
            self.clock.add(slot, int(block_time))
            self._seen_bt(int(block_time))

    def on_row(self, row: Mapping[str, Any], kind: Optional[str] = None) -> bool:
        """One tip-follower / walker row (tools/fast_tip_follower.py trades-/creates-/migrations-<hour>.jsonl, or a tape row with the same
        keys). kind in ("trades", "creates", "migrations"); inferred from `type` when None. Returns True if the row reached the engine.

        The clock is stamped from the row's `block_time` (on_block). PumpSwap V: "event" mode reads only `virtual_quote_reserves` (the
        print's own event V); "const" mode takes the first non-null of CONST_V_KEYS as the pool's V0. Every row that cannot be used is counted
        under drop_<reason> (strict=True raises RowError): no_slot, bad_block_time, <kind>_no_block_time (creates and migrations need it),
        bad_side, missing_<field>, bad_kind. A PumpSwap print with a null or non-numeric quote_reserve/base_reserve is counted under
        drop_missing_reserves and still forwarded with both reserves None, so the pool is marked bad (bad_reserves), as 11_passA:129-130 drops
        the whole mint and as on_trade does."""
        if kind is None:
            t = row.get("type")
            kind = "migrations" if t in ("complete", "migration") else "creates" if t == "create" else "trades"
        slot = row.get("slot")
        if isinstance(slot, bool) or not isinstance(slot, (int, np.integer)):
            self._drop("no_slot", kind)
            return False
        slot = int(slot)
        bt_raw = row.get("block_time")
        bt = _int_or_none(bt_raw)
        if bt_raw is not None and bt is None:
            self._drop("bad_block_time", bt_raw)
            return False
        if bt is not None:
            self.on_block(slot, bt)
        if kind == "trades":
            side = row.get("side")
            if side not in ("buy", "sell"):
                self._drop("bad_side", side)
                return False
            venue = row.get("venue")
            need = TRADE_FIELDS + (("pool",) if venue == VENUE_PUMPSWAP else ())
            for k in need:
                v = row.get(k)
                if v is None or (k in ("sol_lamports", "token_raw") and not _num(v)):
                    self._drop("missing_" + k, venue)
                    return False
            qr, br = row.get("quote_reserve"), row.get("base_reserve")
            if venue == VENUE_PUMPSWAP and not (_num(qr) and _num(br)):
                self._drop("missing_reserves", venue)   # counted (strict raises), then fail closed:
                qr = br = None                          # on_trade -> _ingest marks the pool bad (11_passA:129-130)
            if bt is None:
                self.stats["trades_no_block_time"] += 1           # kept: 11_passA's null block_time rule (running max / graduation)
            pool = row.get("pool")
            v_event = None
            if venue == VENUE_PUMPSWAP:
                if self.v_source == V_EVENT:
                    v_event = _int_or_none(row.get(EVENT_V_KEY))
                    if v_event is None:
                        self.stats["ps_rows_no_event_v"] += 1     # the pool is rejected (first print) or marked bad (later print)
                else:
                    for k in CONST_V_KEYS:
                        v = row.get(k)
                        if v is not None and _num(v):
                            if pool not in self._pools:
                                self.set_pool_v(pool, v)
                            break
                    ev = _int_or_none(row.get(EVENT_V_KEY))
                    P = self._pools.get(pool)
                    if ev is not None and P is not None and float(ev) != P.V:
                        self.stats["const_v_drift_rows"] += 1     # event V(t) != V0 on a "const" engine: this tape needs "event"
            self.on_trade(venue, row["mint"], row["trader"], side == "buy", row["sol_lamports"], row["token_raw"], qr, br, pool, slot, bt,
                          v_event=v_event)
            return True
        if kind == "creates":
            if row.get("mint") is None:
                self._drop("missing_mint", kind)
                return False
            if bt is None:
                self._drop("creates_no_block_time", row.get("mint"))
                return False
            self.on_create(row["mint"], row.get("creator"), row.get("name"), row.get("symbol"), bt, row.get("is_mayhem_mode"))
            return True
        if kind == "migrations":
            if row.get("type", "complete") != "complete":
                self.stats["migrations_other_type"] += 1          # PumpSwap `migration` rows: graduation is the bonding `complete` row
                return False
            if row.get("mint") is None:
                self._drop("missing_mint", kind)
                return False
            if bt is None:
                self._drop("migrations_no_block_time", row.get("mint"))
                return False
            self.on_graduation(row["mint"], bt)
            return True
        self._drop("bad_kind", kind)
        return False

    def on_trade_row(self, r: Mapping[str, Any]) -> bool:
        """A trade row (tape or tip-follower keys); same as on_row(r, "trades")."""
        return self.on_row(r, "trades")

    def on_create(self, mint: str, creator: Optional[str], name: Optional[str], symbol: Optional[str], block_time: Optional[int],
                  mayhem: Optional[bool] = None) -> None:
        if block_time is None:
            self._drop("creates_no_block_time", mint)
            return
        self._seen_bt(int(block_time))
        info = self._info_of(mint)
        if info.has_create:
            return                                   # first create wins (min slot)
        info.has_create = True; info.creator = creator; info.cbt = int(block_time); info.name = name; info.mayhem = mayhem
        info.last_bt = info.cbt if info.last_bt is None else max(info.last_bt, info.cbt)
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
            self._drop("migrations_no_block_time", mint)
            return
        self._seen_bt(int(block_time))
        info = self._info_of(mint)
        if info.gbt is not None:
            return
        info.gbt = int(block_time)
        info.last_bt = info.gbt if info.last_bt is None else max(info.last_bt, info.gbt)
        n_tr = NAN if info.bc_pruned else len(info.bc_traders)
        if info.bc_pruned:
            self.stats["bc_traders_pruned_graduated"] += 1
        info.bc_frozen = (info.bc_n, n_tr, info.bc_buy, info.bc_sell)
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

    def on_trade(self, venue: str, mint: str, trader: str, is_buy: bool, sol_lamports: float, token_raw: float, quote_reserve: float,
                 base_reserve: float, pool: Optional[str], slot: int, block_time: Optional[int], v_event: Optional[int] = None) -> None:
        """quote_reserve is the print's PRE-trade quote: the raw vault in "event" mode (mapped here with v_event), already mapped (or the
        vault where V(t) = V0) in "const" mode. v_event is ignored in "const" mode."""
        self.stats["trades"] += 1
        if block_time is not None:
            self.clock.add(slot, block_time)
            self._seen_bt(block_time)
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
                info = self._info_of(mint)
                if info.gbt is None:
                    info.bc_n += 1
                    info.last_bt = block_time if info.last_bt is None else max(info.last_bt, block_time)
                    if info.bc_pruned:
                        self.stats["bc_trade_after_prune"] += 1
                    else:
                        info.bc_traders.add(trader)
                    if is_buy:
                        info.bc_buy += sol_lamports
                    else:
                        info.bc_sell += sol_lamports
            return
        if venue != VENUE_PUMPSWAP:
            return
        self.stats["ps_rows"] += 1
        self._on_ps(mint, trader, is_buy, sol_lamports, token_raw, quote_reserve, base_reserve, pool, slot, block_time, v_event)

    def _on_ps(self, mint, trader, is_buy, sol_lamports, token_raw, quote_reserve, base_reserve, pool, slot, block_time, v_event) -> None:
        """The pool part of a PumpSwap print (market minutes are counted once, in on_trade; held "const" prints are replayed here)."""
        if pool is None:
            self._drop("missing_pool", mint)
            return
        P = self._pools.get(pool)
        if P is None:
            pend = self._vpend.get(pool)
            if pend is not None:                         # "const": still waiting for this pool's V0
                pend["rows"].append((mint, trader, is_buy, sol_lamports, token_raw, quote_reserve, base_reserve, pool, slot, block_time, None))
                if len(pend["rows"]) > V_PENDING_MAX_ROWS or (block_time is not None and pend["t0"] is not None
                                                             and block_time - pend["t0"] > V_PENDING_MAX_S):
                    del self._vpend[pool]
                    self._reject(pool, "v_unknown", block_time)
                return
            if pool in self._rejected:
                return
            key = (slot, pool)
            cur = self._mint_first.get(mint)
            canonical = cur is None or cur[1] == pool or key < (cur[0], cur[1])
            if canonical and (cur is None or cur[1] != pool):
                if cur is not None:
                    old = self._pools.get(cur[1])
                    if old is not None:
                        old.eligible = False
                    if self._vpend.pop(cur[1], None) is not None:
                        self._reject(cur[1], "not_canonical", block_time)
                self._mint_first[mint] = (slot, pool, block_time if block_time is not None else -1)
            if not canonical:
                self._reject(pool, "not_canonical", block_time)
                return
            if self.v_source == V_EVENT:
                V0 = v_event
                if V0 is None:
                    self._reject(pool, "v0_missing", block_time)
                    return
            else:
                V0 = self._v.get(pool)
                if V0 is None:
                    self._vpend[pool] = {"mint": mint, "t0": block_time, "rows": [(mint, trader, is_buy, sol_lamports, token_raw, quote_reserve,
                                                                                   base_reserve, pool, slot, block_time, None)]}
                    self.stats["v_pending_pools"] += 1
                    return
            if not (V_LO <= V0 <= V_HI):
                self._reject(pool, "v_out_of_band", block_time)
                return
            info = self._info.get(mint)
            if info is not None and info.revived:
                self._reject(pool, "info_pruned", block_time)
                return
            P = _Pool(pool, mint, V0, info.creator if info is not None else None)
            if info is not None and info.gbt is not None:
                P.g0 = info.gbt
            self._pools[pool] = P
            self._v.pop(pool, None)
            self._ingest(P, slot, block_time, is_buy, sol_lamports, token_raw, quote_reserve, base_reserve, trader, v_event)
            self._register(P)
            return
        if P.eligible is False or P.dead:
            return
        if P.bad:
            self.stats["rows_on_bad_pool"] += 1
            return
        if P.slot and slot < P.slot[-1]:
            self.stats["out_of_order_rows"] += 1
            self._mark_bad(P, "out_of_order")
            return
        self._ingest(P, slot, block_time, is_buy, sol_lamports, token_raw, quote_reserve, base_reserve, trader, v_event)
        if P.eligible is None:
            self._register(P)

    def _ingest(self, P: _Pool, slot, block_time, is_buy, sol_lamports, token_raw, quote_reserve, base_reserve, trader, v_event) -> None:
        if quote_reserve is None or base_reserve is None or not _num(quote_reserve) or not _num(base_reserve):
            self._mark_bad(P, "bad_reserves")
            return
        if self.v_source == V_EVENT:
            if v_event is None:
                self._mark_bad(P, "v_missing")
                return
            vault = _int_or_none(quote_reserve)
            if vault is None:
                self._mark_bad(P, "bad_reserves")
                return
            q = float(self._map_q(vault, int(v_event), int(P.V)))     # EXP-025 s2.4: vault + V(t) - V0, the print's own values
        else:
            q = float(quote_reserve)
        P.add(slot, block_time, is_buy, float(sol_lamports), float(token_raw), q, float(base_reserve), trader)
        if P.bad and P.bad_reason is None:
            P.bad = False
            self._mark_bad(P, "bad_reserves")

    def _register(self, P: _Pool) -> None:
        """Decide universe membership once graduation and the first print are both known (10_meta universe rule)."""
        if P.eligible is not None or P.g0 is None or P.first_bt is None:
            return
        mf = self._mint_first.get(P.mint)
        d = P.first_bt - P.g0
        P.eligible = bool(mf is not None and mf[1] == P.pool and FIRST_PRINT_WINDOW[0] <= d <= FIRST_PRINT_WINDOW[1] and V_LO <= P.V <= V_HI)
        if not P.eligible:
            self._pools.pop(P.pool, None)
            self._reject(P.pool, "not_canonical" if (mf is None or mf[1] != P.pool) else "first_print_window", P.first_bt)
            return
        if P.bt and P.bt[0] < 0:                      # 11_passA: a null first block_time becomes the graduation time
            P.bt[0] = P.g0
            for k in range(1, len(P.bt)):
                if P.bt[k] < P.bt[k - 1]:
                    P.bt[k] = P.bt[k - 1]
        if P.creator is not None:
            self._cr_pools.setdefault(P.creator, []).append(P)

    def expire(self, now_bt: int, market_keep_s: int = 2 * 3600) -> int:
        """Free pool arrays older than the decision window (keep the 6 h outcome for the creator record), and bound every other table:
        market minutes (market_keep_s), rejected pools (24 h), held "const" pools (V_PENDING_MAX_S), idle ungraduated mints (bonding-trader
        set after prune_bonding_idle_s, the whole mint after prune_info_idle_s), graduated mints after their pool's life, windowed as-of
        tables (TABLE_KEEP_S), the slot clock (3 h). Returns pools freed."""
        n = 0
        for pid in [p for p, P in self._pools.items() if (P.g0 is not None and now_bt > P.g0 + GRID_END + 3600) or
                    (P.g0 is None and P.first_bt is not None and now_bt > P.first_bt + 6 * 3600)]:
            P = self._pools.pop(pid)
            if P.outcome is None and P.eligible:
                P.outcome = P.outcome_now()
            P.free(); n += 1
            self._rejected[pid] = now_bt                 # later prints of a freed pool are ignored (not a rejection: not counted)
        for m in [m for m in self._mk if m < now_bt - market_keep_s]:
            del self._mk[m]
        for p in [p for p, bt in self._rejected.items() if bt < now_bt - 86400]:
            del self._rejected[p]
        for p in [p for p, e in self._vpend.items() if e["t0"] is None or now_bt - e["t0"] > V_PENDING_MAX_S]:
            del self._vpend[p]
            self._reject(p, "v_unknown", now_bt)
        alive_mints = {P.mint for P in self._pools.values()}
        for mint, info in list(self._info.items()):
            last = info.last_bt
            if mint in alive_mints or last is None:
                continue
            if info.gbt is not None:
                if now_bt > info.gbt + GRID_END + 2 * 3600:
                    del self._info[mint]
                    self._mint_first.pop(mint, None)
                continue
            idle = now_bt - last
            if self.prune_info_idle_s is not None and idle > self.prune_info_idle_s:
                del self._info[mint]
                self._mint_first.pop(mint, None)
                self._pruned[mint] = now_bt
                self.stats["mints_pruned"] += 1
            elif self.prune_bonding_idle_s is not None and idle > self.prune_bonding_idle_s and not info.bc_pruned:
                info.bc_traders = set()
                info.bc_pruned = True
                self.stats["bc_traders_pruned"] += 1
        for mint in [m for m, t in self._pruned.items() if now_bt - t > 30 * 86400]:
            del self._pruned[mint]
        horizon = now_bt - TABLE_KEEP_S
        for tab in (self._w_c, self._w_g, self._s_c):
            for k in [k for k, arr in tab.items() if arr[-1] < horizon]:
                del tab[k]
            for arr in tab.values():
                if arr[0] < horizon:
                    del arr[:bisect.bisect_left(arr, horizon)]
        for arr in (self._all_c, self._all_g):
            if arr and arr[0] < horizon:
                del arr[:bisect.bisect_left(arr, horizon)]
        self.clock.prune(now_bt - 3 * 3600)
        return n

    # ------------------------------------------------------------------ queries
    def alive_pools(self, T: int) -> list[str]:
        return [pid for pid, P in self._pools.items() if P.eligible and P.g0 is not None and P.g0 + GRID_START <= T <= P.g0 + GRID_END]

    def _ledger_for(self, day: str, keep_days: Optional[int] = None):
        """The snapshot for `day`, or None. A present snapshot is cached (keep_days days, default self.ledger_keep_days = 2, so deferred
        evaluations do not thrash; a live caller keeps 1); None and a provider error are never cached: the provider is asked again after
        ledger_retry_s[0] seconds, doubling up to ledger_retry_s[1]. A day dropped from the cache is opened again if asked for: same values."""
        if self.ledger is None:
            return None
        keep = self.ledger_keep_days if keep_days is None else keep_days
        snap = self._ledger_cache.get(day)
        if snap is not None:
            return snap
        now = self._mono()
        nxt = self._ledger_next.get(day)
        if nxt is not None and now < nxt[0]:
            self.stats["ledger_backoff_skips"] += 1
            return None
        try:
            snap = self.ledger.snapshot_for_day(day)
        except Exception:  # noqa: BLE001 - a provider error is a missing snapshot, retried
            self.stats["ledger_errors"] += 1
            snap = None
        if snap is None:
            wait = self.ledger_retry_s[0] if nxt is None else min(nxt[1] * 2, self.ledger_retry_s[1])
            self._ledger_next[day] = (now + wait, wait)
            if len(self._ledger_next) > 4:
                for d in sorted(self._ledger_next)[:-4]:
                    del self._ledger_next[d]
            self.stats["ledger_missing"] += 1
            return None
        self._ledger_next.pop(day, None)
        if len(self._ledger_cache) >= keep:                  # keep - 1 days stay beside the new one (keep 2: the latest day, as before)
            for d in sorted(self._ledger_cache)[:len(self._ledger_cache) - keep + 1]:
                del self._ledger_cache[d]
        self._ledger_cache[day] = snap
        return snap

    def features_at(self, pool: str, T: int, sd: Optional[int] = None, next_state: Optional[tuple] = None) -> Optional[Features]:
        """next_state = the PRE-trade state of the first print with slot >= SD, when the caller already knows it (parity harness "exact"
        mode): (quote_reserve, base_reserve) in "const" mode, (vault, base_reserve, event V) in "event" mode (mapped like on_trade).
        Live callers leave it None and get the fee-model post-trade estimate."""
        P = self._pools.get(pool)
        if P is None or not P.eligible or P.dead or P.bad:
            return None
        T = int(T)
        if sd is None:
            sd = self.clock.decision_slot(T)
            if sd is None:
                return None
        if next_state is not None:
            if self.v_source == V_EVENT:
                if len(next_state) != 3:
                    raise ValueError('"event" mode: next_state = (vault, base_reserve, event V)')
                next_state = (float(self._map_q(int(next_state[0]), int(next_state[2]), int(P.V))), next_state[1])
            elif len(next_state) != 2:
                raise ValueError('"const" mode: next_state = (quote_reserve, base_reserve)')
        n = len(P.slot)
        i1 = bisect.bisect_left(P.slot, sd)
        bt = P.bt
        i60 = min(bisect.bisect_left(bt, T - 3600), i1)
        if i1 - i60 <= 0:
            return None
        est = P.est
        if i1 == n and next_state is not None:
            qq = next_state[0] + P.V
            est = (qq, next_state[1], qq / next_state[1]) if (qq > 0 and next_state[1] > 0) else None
        if i1 == n and est is None:
            return None
        g0 = P.g0
        bl = bisect.bisect_left
        i1m = min(bl(bt, T - 60), i1); i5 = min(bl(bt, T - 300), i1); i10 = min(bl(bt, T - 600), i1); i15 = min(bl(bt, T - 900), i1)
        V = P.V

        def price(i: int) -> float:
            return P.ppre[i] if i < n else est[2]

        qstate = P.qpre[i1] if i1 < n else est[0]
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
        led = self._ledger_for(utc_day(T))
        if not pre:
            return self._finish(P, T, sd, i1, f, False, led)
        # ---- trajectory shape
        hi = spot if i1 - 1 < 1 else max(P.am_hi[i1 - 1], spot)
        lo = spot if i1 - 1 < 1 else min(P.am_lo[i1 - 1], spot)

        def csl_at(k: int) -> float:
            if k < n:
                return P.csl[k]
            d = math.log(est[2]) - P.lp_last                   # lp[n - 1]
            return P.csl[n - 1] + d * d

        c1 = csl_at(i1)
        f["r_ath"] = spot / hi; f["r_atl"] = spot / lo; f["ath_grad"] = hi / p_grad
        f["vol15"] = math.sqrt(max(c1 - csl_at(i15), 0.0) / max(i1 - i15, 1))
        f["vol60"] = math.sqrt(max(c1 - csl_at(i60), 0.0) / max(i1 - i60, 1))
        f["ncum"] = float(i1); f["ntr_cum"] = P.cs_any[i1]; f["vcum"] = cv[i1] / 1e9
        isb, sol, th = P.isb, P.sol, P.th
        f["maxbuy5"] = (max((sol[k] if isb[k] & 1 else 0.0) for k in range(i5, i1)) / 1e9) if i1 > i5 else 0.0
        f["avgbuy5"] = bs5 / max(nb5, 1); f["buyshare5"] = bs5 / max(v5, 1e-9)
        f["cr_sold"] = P.cs_crs[i1] / 1e15; f["cr_bought"] = P.cs_crb[i1] / 1e15
        # ---- holders (PumpSwap flows only)
        if i1 == n:
            hold = P.hold
        else:
            hold = {}
            for k in range(i1):
                hold[th[k]] = hold.get(th[k], 0.0) + (P.tok[k] if isb[k] & 1 else -P.tok[k])
        pos = np.sort(np.array([x for x in hold.values() if x > 0], dtype=np.float64))[::-1]
        tot = pos.sum() if len(pos) else 0.0
        f["h_npos"] = float(len(pos))
        f["h_top1"] = float(pos[:1].sum() / tot) if tot > 0 else NAN
        f["h_top5"] = float(pos[:5].sum() / tot) if tot > 0 else NAN
        f["h_top10"] = float(pos[:10].sum() / tot) if tot > 0 else NAN
        f["h_pos_frac_supply"] = float(tot / 1e15)
        sidx = [k for k in range(i5, i1) if not (isb[k] & 1)]
        if sidx:
            sb = P.seen_buy
            num = np.array([sol[k] for k in sidx if sb.get(th[k], 1 << 60) >= i1], dtype=np.float64).sum()
            den = np.array([sol[k] for k in sidx], dtype=np.float64).sum()
            f["sell_curveholder_share"] = float(num / max(den, 1))
        else:
            f["sell_curveholder_share"] = NAN
        # ---- wallet intelligence
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
                    if hasattr(led, "lookup_many"):               # AsofLedgerAdapter: one vectorised lookup per window
                        known, st = led.lookup_many(users)
                        known = np.asarray(known, dtype=bool); st = np.array(st, dtype=np.float64)
                        st[~known] = np.nan
                    else:
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
        return self._finish(P, T, sd, i1, f, True, led)

    def _finish(self, P: _Pool, T: int, sd: int, i1: int, f: dict, pre: bool, led: Any = None) -> Features:
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
        return Features(P.pool, P.mint, T, sd, i1, vec, pre, stage1, h1, cap_ok, led is not None, utc_day(T) if led is not None else None)

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
