"""kalshi-crypto rule R3 (late-lock maker, 15M) on trade prints.  Paper research only.

Source of truth: hunt18 DECLARE.md (frozen 2026-10-17T23:00:18Z), kalshi-crypto/:
  Splits: development 2026-10-01..10-09, holdout 2026-10-10..10-16 (close_time UTC date), holdout run ONCE.
  Universe: KXBTC15M, KXETH15M, KXSOL15M; assert no mve_* field / KXMVE prefix before scoring.
  Decision clocks from open_time / scheduled close_time only (never settlement_ts).
  Fair value (frozen): P_yes(t) = Phi( ln(S_t/K) / sqrt( c^2 * s1m^2 * (tau-40s)/60s + e^2 ) ),
    tau = close_time - t (s); S_t = Coinbase 1m candle close of the last candle with end <= t;
    s1m = stdev of 1m log returns over the trailing 60 Coinbase candles ending <= t; K = floor_strike;
    if tau < 60 the window term is s1m^2*(tau/60)/3, scaled the same way (by c^2).
  Fees: taker 0.07*C*P*(1-P), cent rounding UP per fill (deciding), micro alongside; maker fee 0.
  Fills on prints: our resting bid at price b on side X is filled when a print on X at price <= b has the
    taker selling X (taker_side opposite X), created_time > quote time; 1 contract per fill; at most 1 fill
    per market per side per 30 s; 15% of fills dropped at random (seed 1) in the maker-15%-unfilled cell.
    Hold to settlement.  Markout at +10 s, +60 s against the model fair and against the next print.
  R3: at t = close-180s, if P >= 0.90 (or <= 0.10), rest a bid on the favoured side at
    min(last favoured print before t, P_fav - 0.04); filled per the print rule in (t, close-60s].
  Report: n contracts, events, days; mean net/contract (cent) with micro alongside; event-clustered
    bootstrap CI90 (1,000 draws, seed 1); ex-top-3; ex-best-day; with 3 added worst losses; maker cell with
    15% unfilled; per series and per day.

c and e are the development-only fits of `fit-c --split dev` (FROZEN_C, FROZEN_E_BPS below), recorded in hunt18
kalshi-crypto/fit_c_dev/fit_c_dev.json (sha256 d858ca866b0953cca94a8f67f28994380638b09fbed4fa69c0bbd043b02f11bc);
they replace the earlier proxy_model_out.json c, which used four fit instants.  The fee functions are tools/kalshi_fees.py.  The tool computes numbers only: the lab promotion gate is applied
by the manager, not here.

Data discipline.  Markets are filtered to the requested split BEFORE any trade file is opened; trade rows are
read only for tickers of that split (and, in run mode, only for signal markets).  The holdout split takes an
O_EXCL lock in the output directory and a global ledger line before its first trade file is opened.

    python -m tools.kalshi_crypto_r3 run --split dev --trades-stores D1 [D2 ...] --markets-root R \
        --candles-dir C --out OUT [--count-only]
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import hashlib
import json
import math
import os
import resource
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

try:                                    # `python -m tools.kalshi_crypto_r3` and `from tools import ...`
    from tools import kalshi_fees
except ImportError:                     # pragma: no cover - direct script run
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools import kalshi_fees

# ---------------------------------------------------------------------------------------------- constants
HUNT_DIR = "/data/mal/hunt-1008/push-1016/hunt18/kalshi-crypto"
DECLARE_PATH = HUNT_DIR + "/DECLARE.md"
LEDGER_DEFAULT = HUNT_DIR + "/R3_RUNS.jsonl"
LEDGER_ENV = "MAL_R3_LEDGER"
TEST_ENV = "MAL_R3_TEST"

SPLITS = {"dev": ("2026-10-01", "2026-10-09"), "holdout": ("2026-10-10", "2026-10-16")}
SERIES = {"KXBTC15M": ("BTC", "BTC-USD"), "KXETH15M": ("ETH", "ETH-USD"), "KXSOL15M": ("SOL", "SOL-USD")}
# c: `fit-c --split dev` (DECLARE recipe: dev markets only, grid 0.6..1.6 step 0.05, min Brier at open+300 s,
# +600 s, +780 s only, s1m == 0 rows skipped), run 2026-10-19 on 848 dev markets per asset (2,544 rows each):
# output /data/mal/hunt-1008/push-1016/hunt18/kalshi-crypto/fit_c_dev/fit_c_dev.json.  It REPLACES the earlier
# 0.95 / 0.80 / 0.85 of proxy_model_out.json, which minimised over four instants including open+720 s.
# e: comp_tp dev_sd_bps (Coinbase+Bitstamp typical-price proxy error, dev only), identical in both fits.
FROZEN_C = {"BTC": 1.00, "ETH": 0.85, "SOL": 0.80}
FROZEN_E_BPS = {"BTC": 0.831, "ETH": 1.168, "SOL": 1.513}

US = 1_000_000
MARKET_DURATION_S = 900
DECISION_BEFORE_CLOSE_S = 180
WINDOW_END_BEFORE_CLOSE_S = 60
P_LOCK = 0.90
P_LOCK_LOW = 0.10           # a literal: 1.0 - 0.90 is 0.09999999999999998 in floating point
BID_MARGIN = 0.04
FILL_SPACING_US = 30 * US
UNFILLED_FRAC = 0.15
VOL_RETURNS = 60            # proxy_model.py: 60 one-minute returns, at least 40 present
VOL_MIN_RETURNS = 40
MARKOUT_H_S = (10, 60)
BOOTSTRAP_DRAWS = 1000
SEED = 1
MAX_RSS_MB_DEFAULT = 1800

ID_COLS = ["trade_id", "ticker", "created_time", "is_block_trade"]
VAL_COLS = ["yes_price", "no_price", "count", "taker_side"]
FILL_SCHEMA = pa.schema([
    ("ticker", pa.string()), ("event_ticker", pa.string()), ("series", pa.string()),
    ("close_date", pa.string()), ("close_time_us", pa.int64()), ("side", pa.string()), ("quote_time_us", pa.int64()),
    ("fill_time_us", pa.int64()), ("fill_trade_id", pa.string()), ("bid_micro", pa.int64()),
    ("last_print_micro", pa.int64()), ("p_yes", pa.float64()), ("p_fav", pa.float64()),
    ("count", pa.int64()), ("win", pa.bool_()), ("payoff_micro", pa.int64()),
    ("fee_maker_cent_micro", pa.int64()), ("fee_maker_micro_micro", pa.int64()),
    ("net_cent_micro", pa.int64()), ("net_micro_micro", pa.int64()),
    ("taker_equiv_fee_cent_micro", pa.int64()), ("dropped_15", pa.bool_()),
    ("mk10_fair_cent", pa.float64()), ("mk60_fair_cent", pa.float64()),
    ("mk10_print_cent", pa.float64()), ("mk60_print_cent", pa.float64()),
])


class Refusal(Exception):
    """A discipline check failed; nothing further is read."""


# ------------------------------------------------------------------------------------------- fair value
def phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def fair_yes(S: float, K: float, s1m: float, tau_s: float, c: float, e: float) -> float:
    """DECLARE fair value.  tau_s = close_time - t in seconds; e is a log-units stdev."""
    tau_s = max(0.0, float(tau_s))
    if tau_s < 60.0:
        window = s1m * s1m * (tau_s / 60.0) / 3.0
    else:
        window = s1m * s1m * (tau_s - 40.0) / 60.0
    v = c * c * window + e * e
    if not v > 0.0:
        return 1.0 if S > K else (0.0 if S < K else 0.5)
    return phi(math.log(S / K) / math.sqrt(v))


def last_candle_close(closes: dict, t_s: float):
    """Close of the last 1m candle with end <= t (start = floor(t/60)*60 - 60); None if that candle is absent."""
    start = int(t_s // 60) * 60 - 60
    return closes.get(start)


def trailing_vol(closes: dict, t_s: float):
    """Population stdev of up to 60 one-minute log returns of candles ending <= t (>= 40 present), as in
    proxy_model.py; a return needs both neighbouring candles."""
    e0 = int(t_s // 60) * 60
    rets = []
    for k in range(VOL_RETURNS):
        a = closes.get(e0 - 60 - 60 * k)
        b = closes.get(e0 - 120 - 60 * k)
        if a and b:
            rets.append(math.log(a / b))
    if len(rets) < VOL_MIN_RETURNS:
        return None
    return float(np.std(np.asarray(rets, dtype=np.float64)))


def fair_at(closes: dict, asset: str, K: float, close_us: int, t_us: int):
    """Model P_yes at time t_us for a market closing at close_us; None when candle/vol inputs are missing."""
    t_s = t_us / US
    S = last_candle_close(closes, t_s)
    s1m = trailing_vol(closes, t_s)
    if S is None or s1m is None or not s1m > 0 or not S > 0 or not K > 0:   # s1m == 0: skipped, as in the fit
        return None
    return fair_yes(S, K, s1m, (close_us - t_us) / US, FROZEN_C[asset], FROZEN_E_BPS[asset] / 1e4)


def tick_ceil_micro(x_dollars: float) -> int:
    """Round a price UP to the tick grid (0.001 below 0.10 and above 0.90, otherwise 0.01), in micro-dollars."""
    xm = x_dollars * US
    tick = 1000 if (xm < 100_000 or xm > 900_000) else 10_000
    return int(math.ceil(xm / tick - 1e-9)) * tick


def to_micro(x: float) -> int:
    return int(round(float(x) * US))


# ----------------------------------------------------------------------------------------- fills + P&L
def simulate_fills(rows_sorted, side: str, bid_micro: int, t_us: int, end_us: int, strict_below: bool = False):
    """DECLARE print rule.  rows_sorted: (created_us, trade_id, yes_micro, no_micro, taker_side, count)
    sorted by (created_us, trade_id).  Fill when created_us in (t, end], the taker bought the OPPOSITE side,
    the print price on our side <= bid, and >= 30 s since our previous fill in this market.  1 contract per
    fill, filled at our bid.  strict_below=True is the queue-back cell: we are last in the queue at our price, so a
    print AT our bid does not fill; only a print strictly below it does (same window, same spacing)."""
    opp = "no" if side == "yes" else "yes"
    fills, last_fill = [], None
    for created, tid, ym, nm, taker, _count in rows_sorted:
        if created <= t_us or created > end_us or taker != opp:
            continue
        px = ym if side == "yes" else nm
        if px > bid_micro or (strict_below and px >= bid_micro):
            continue
        if last_fill is not None and created - last_fill < FILL_SPACING_US:
            continue
        fills.append((created, tid))
        last_fill = created
    return fills


def choose_bid(side: str, p_fav: float, last_yes_micro: int, last_no_micro: int) -> int:
    """R3 bid: min(last favoured print before t, P_fav - 0.04), the second term rounded UP to the tick grid."""
    last_px = last_yes_micro if side == "yes" else last_no_micro
    return min(last_px, tick_ceil_micro(p_fav - BID_MARGIN))


def maker_fee_micro_pair(price_micro: int):
    """(cent-rounded-up, unrounded/micro) maker fee in micro-dollars for one contract.  tools/kalshi_fees:
    series fee_type 'quadratic' -> maker fee 0 (DECLARE)."""
    p = price_micro / US
    return (to_micro(kalshi_fees.maker_fee(1, p, "quadratic", 1.0, "cent")),
            to_micro(kalshi_fees.maker_fee(1, p, "quadratic", 1.0, "micro")))


def taker_fee_micro_pair(price_micro: int, count: int = 1):
    p = price_micro / US
    return (to_micro(kalshi_fees.taker_fee(count, p, 1.0, "cent")),
            to_micro(kalshi_fees.taker_fee(count, p, 1.0, "micro")))


# ----------------------------------------------------------------------------------------- statistics
def _ci90(sum_net, n, draws=BOOTSTRAP_DRAWS, seed=SEED):
    """Event-clustered bootstrap of mean net per contract: resample events with replacement."""
    E = len(sum_net)
    if E == 0:
        return [None, None]
    rs = np.random.RandomState(seed)
    means = np.empty(draws)
    for b in range(draws):
        idx = rs.randint(0, E, size=E)
        means[b] = sum_net[idx].sum() / n[idx].sum()
    return [float(np.percentile(means, 5)), float(np.percentile(means, 95))]


def _basic(net, net_exact, events, days):
    n = len(net)
    if n == 0:
        return {"n_contracts": 0, "n_events": 0, "n_days": 0, "total_net_cent": 0.0,
                "mean_net_cent": None, "mean_net_micro_cent": None}
    return {"n_contracts": int(n), "n_events": int(len(set(events))), "n_days": int(len(set(days))),
            "total_net_cent": float(net.sum() / 1e4), "mean_net_cent": float(net.mean() / 1e4),
            "total_net_micro_cent": float(net_exact.sum() / 1e4),
            "mean_net_micro_cent": float(net_exact.mean() / 1e4)}


def _sub(net, net_exact, keep):
    k = np.asarray(keep, dtype=bool)
    return {"n_contracts": int(k.sum()), "total_net_cent": float(net[k].sum() / 1e4),
            "mean_net_cent": float(net[k].mean() / 1e4) if k.any() else None}


def _cluster_ci(labels, net):
    """CI90 of mean net per contract with `labels` as the bootstrap cluster (1,000 draws, RandomState(1))."""
    ids, inv = np.unique(np.asarray(labels).astype(str), return_inverse=True)
    cn = np.bincount(inv, weights=net, minlength=len(ids))
    cc = np.bincount(inv, minlength=len(ids)).astype(np.float64)
    lo, hi = _ci90(cn, cc)
    return [None if lo is None else lo / 1e4, None if hi is None else hi / 1e4]


def cell_stats(net, net_exact, price, events, days, win=None, windows=None):
    """Report-per-rule block for one set of fills.  net, net_exact, price in micro-dollars (net uses the
    cent-rounded-up fee = deciding; net_exact the unrounded fee).  `windows` is the close_time of the fill's market
    (the 15-minute window shared by BTC/ETH/SOL): the second bootstrap cluster.  ci90_lo_decider is the lower of
    the event-clustered and window-clustered 5th percentiles."""
    net = np.asarray(net, dtype=np.float64)
    net_exact = np.asarray(net_exact, dtype=np.float64)
    price = np.asarray(price, dtype=np.float64)
    events = np.asarray(events, dtype=object)
    days = np.asarray(days, dtype=object)
    out = _basic(net, net_exact, events, days)
    out["bootstrap"] = {"draws": BOOTSTRAP_DRAWS, "seed": SEED, "ci": "5th..95th percentile",
                        "clusters": ["event_ticker", "close_time window"]}
    n = len(net)
    if n == 0:
        out.update({"ci90_event_cent": [None, None], "ci90_window_cent": [None, None],
                    "ci90_day_cent_informational": [None, None], "ci90_lo_decider": None, "n_windows": 0,
                    "per_day": {}, "days_positive": 0, "win_rate": None, "mean_price_cent": None,
                    "ex_top3_fills": None, "ex_top3_events": None, "ex_best_day": None,
                    "added3_worst_losses": None})
        return out
    ev_ids, ev_inv = np.unique(events.astype(str), return_inverse=True)
    ev_net = np.bincount(ev_inv, weights=net, minlength=len(ev_ids))
    out["ci90_event_cent"] = _cluster_ci(events, net)
    out["ci90_window_cent"] = _cluster_ci(windows, net) if windows is not None else [None, None]
    out["n_windows"] = int(len(set(np.asarray(windows).astype(str)))) if windows is not None else None
    out["ci90_day_cent_informational"] = _cluster_ci(days, net)
    lows = [x for x in (out["ci90_event_cent"][0], out["ci90_window_cent"][0]) if x is not None]
    out["ci90_lo_decider"] = min(lows) if lows else None
    out["win_rate"] = float(np.asarray(win, dtype=bool).mean()) if win is not None else None
    out["mean_price_cent"] = float(price.mean() / 1e4)
    # ex-top-3 fills: drop the 3 best single fills (stable: ties by position)
    order = np.argsort(-net, kind="stable")
    keep = np.ones(n, dtype=bool)
    keep[order[:3]] = False
    out["ex_top3_fills"] = _sub(net, net_exact, keep)
    # ex-top-3 events: drop the 3 events with the largest total net (harsher; all their fills)
    ev_order = np.argsort(-ev_net, kind="stable")
    drop_ev = set(ev_order[:3].tolist())
    out["ex_top3_events"] = _sub(net, net_exact, np.array([i not in drop_ev for i in ev_inv]))
    # per-day totals, days positive, ex-best-day
    day_ids, day_inv = np.unique(days.astype(str), return_inverse=True)
    day_net = np.bincount(day_inv, weights=net, minlength=len(day_ids))
    day_n = np.bincount(day_inv, minlength=len(day_ids))
    out["per_day"] = {}
    for i, d in enumerate(day_ids):
        in_day = day_inv == i
        out["per_day"][str(d)] = {"n_contracts": int(day_n[i]), "n_events": int(len(set(ev_inv[in_day]))),
                                  "total_net_cent": float(day_net[i] / 1e4),
                                  "mean_net_cent": float(day_net[i] / day_n[i] / 1e4)}
    out["days_positive"] = int((day_net > 0).sum())
    best = int(np.argmax(day_net))
    d = _sub(net, net_exact, day_inv != best)
    d["removed_day"] = str(day_ids[best])
    out["ex_best_day"] = d
    # 3 added worst losses: each a full stake loss at the highest fill price in the cell
    loss = float(price.max())
    tot = float(net.sum()) - 3 * loss
    out["added3_worst_losses"] = {"n_contracts": int(n + 3), "loss_each_cent": loss / 1e4,
                                  "total_net_cent": tot / 1e4, "mean_net_cent": tot / (n + 3) / 1e4}
    return out


def unfilled_mask(n: int, frac=UNFILLED_FRAC, seed=SEED):
    """Exactly ceil(frac*n) fills dropped, chosen with RandomState(seed).choice (fills in output order)."""
    k = int(math.ceil(frac * n - 1e-9))
    drop = np.zeros(n, dtype=bool)
    if k > 0:
        drop[np.random.RandomState(seed).choice(n, size=k, replace=False)] = True
    return drop


# ------------------------------------------------------------------------------------------ io: markets
def utc_date(us: int) -> str:
    return dt.datetime.fromtimestamp(us // US, dt.timezone.utc).strftime("%Y-%m-%d")


class Market:
    __slots__ = ("ticker", "event", "series", "asset", "open_us", "close_us", "strike", "result", "close_date",
                 "expiration_value")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


_UNIT_TO_US = {"s": (1_000_000, 1), "ms": (1_000, 1), "us": (1, 1), "ns": (1, 1_000)}   # (multiply, divide)


def _ts_us(col) -> np.ndarray:
    """Timestamp column -> int64 microseconds since epoch, whatever the column's unit; nulls become 0."""
    unit = getattr(col.type, "unit", None)
    if unit not in _UNIT_TO_US:
        raise Refusal(f"timestamp column with unsupported type {col.type}")
    mul, div = _UNIT_TO_US[unit]
    v = np.asarray(col.cast(pa.int64()).fill_null(0).to_numpy(zero_copy_only=False), dtype=np.int64)
    return v * mul // div if (mul != 1 or div != 1) else v


MARKET_COLS_PHASE1 = {"ticker", "series_ticker", "event_ticker", "open_time", "close_time"}
MARKET_COLS_PHASE2 = {"floor_strike", "fetched_at", "raw_json"}


def load_markets(markets_root: str, split: str, with_outcomes: bool, with_expiration: bool = False,
                 info: dict = None):
    """Markets of the universe whose close_time UTC date is in the split.  Phase 1 reads ids and times only;
    phase 2 (strike, raw_json, and the result only if with_outcomes) is read for in-split rows only."""
    lo, hi = SPLITS[split]
    counters = defaultdict(int)
    cands = defaultdict(list)
    out_of_split = set()
    for series in SERIES:
        files = sorted(Path(markets_root, f"series={series}").rglob("*.parquet"))
        for f in files:
            pf = pq.ParquetFile(f)
            names = pf.schema_arrow.names
            if any(n.lower().startswith("mve") for n in names):
                raise Refusal(f"mve_* column in {f}")
            if MARKET_COLS_PHASE1 - set(names):
                raise Refusal(f"markets file {f} lacks columns {sorted(MARKET_COLS_PHASE1 - set(names))}")
            t1 = pf.read(columns=["ticker", "series_ticker", "event_ticker", "open_time", "close_time"])
            tick = t1.column("ticker").to_pylist()
            if any(t.startswith("KXMVE") for t in tick):
                raise Refusal(f"KXMVE ticker in {f}")
            sers = t1.column("series_ticker").to_pylist()
            close_us = _ts_us(t1.column("close_time"))
            open_us = _ts_us(t1.column("open_time"))
            days = [utc_date(int(c)) for c in close_us]
            sel = [i for i in range(len(tick)) if lo <= days[i] <= hi and sers[i] == series]
            sel_set = set(sel)
            for i in range(len(tick)):
                if i not in sel_set and sers[i] == series:
                    out_of_split.add(tick[i])
            if not sel:
                continue
            need2 = MARKET_COLS_PHASE2 | ({"result"} if with_outcomes else set()) \
                | ({"expiration_value"} if with_expiration else set())
            if need2 - set(names):
                raise Refusal(f"markets file {f} lacks columns {sorted(need2 - set(names))}")
            if info is not None:                                  # footer names of every file holding in-split rows
                info.setdefault("in_split_files", []).append((str(f), list(names)))
            cols2 = (["floor_strike", "fetched_at", "raw_json"] + (["result"] if with_outcomes else [])
                     + (["expiration_value"] if with_expiration else []))
            t2 = pf.read(columns=cols2).take(pa.array(sel))      # in-split rows only reach Python
            strike = t2.column("floor_strike").to_pylist()
            fetched = _ts_us(t2.column("fetched_at"))
            raw = t2.column("raw_json").to_pylist()
            res = t2.column("result").to_pylist() if with_outcomes else [None] * len(sel)
            expv = t2.column("expiration_value").to_pylist() if with_expiration else [None] * len(sel)
            events = t1.column("event_ticker").to_pylist()
            for k, i in enumerate(sel):
                if raw[k]:
                    try:
                        keys = list(json.loads(raw[k]).keys())
                    except ValueError:
                        keys = []
                    if any(key.lower().startswith("mve") for key in keys):
                        raise Refusal(f"mve_* field in raw_json of {tick[i]}")
                cands[tick[i]].append((int(fetched[k]), res[k], Market(
                    ticker=tick[i], event=events[i], series=series, asset=SERIES[series][0],
                    open_us=int(open_us[i]), close_us=int(close_us[i]), strike=strike[k], result=res[k],
                    close_date=days[i], expiration_value=expv[k])))
    markets, excl = [], defaultdict(int)
    for tkr, lst in cands.items():
        if with_outcomes:
            settled = [c for c in lst if c[1] in ("yes", "no")]
            pick = max(settled or lst, key=lambda c: c[0])[2]
        else:
            pick = max(lst, key=lambda c: c[0])[2]
        if (pick.close_us - pick.open_us) != MARKET_DURATION_S * US:
            excl["bad_duration"] += 1
        elif pick.strike is None or not (pick.strike > 0) or pick.strike != pick.strike:
            excl["no_strike"] += 1
        elif with_outcomes and pick.result not in ("yes", "no"):
            excl["result_not_yes_no"] += 1
        else:
            markets.append(pick)
    markets.sort(key=lambda m: (m.close_us, m.ticker))
    assert not any(m.ticker.startswith("KXMVE") for m in markets)
    return markets, dict(excl), out_of_split


def load_candles(candles_dir: str):
    """Coinbase 1m candles [start, low, high, open, close, volume] -> {asset: {start_s: close}}."""
    out = {}
    for series, (asset, prod) in SERIES.items():
        rows = json.load(open(Path(candles_dir) / f"cb_{prod}.json"))
        out[asset] = {int(r[0]): float(r[4]) for r in rows}
    return out


def load_candle_ohlc(candles_dir: str):
    """For fit-c: {asset: {"cb": {start: (low, high, open, close)}, "bs": {start: (open, high, low, close)}}}
    from cb_<PRODUCT>.json (Coinbase 1m [start, low, high, open, close, vol]) and bs_<pair>.json (Bitstamp 1m
    {"timestamp","open","high","low","close"})."""
    out = {}
    for _series, (asset, prod) in SERIES.items():
        cb = json.load(open(Path(candles_dir) / f"cb_{prod}.json"))
        bs = json.load(open(Path(candles_dir) / f"bs_{prod.replace('-', '').lower()}.json"))
        out[asset] = {
            "cb": {int(r[0]): (float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in cb},
            "bs": {int(r["timestamp"]): tuple(float(r[k]) for k in ("open", "high", "low", "close")) for r in bs}}
    return out


# ------------------------------------------------------------------------------------------ fit-c (dev only)
FIT_INSTANTS_S = (300, 600, 780)                     # DECLARE: open+300 s, +600 s, +780 s (not +720 s)
FIT_GRID = [round(0.6 + 0.05 * i, 2) for i in range(21)]     # 0.6 .. 1.6 step 0.05


def proxy_error_bps(cb: dict, bs: dict, points: dict):
    """Log error (bps) of the Coinbase+Bitstamp typical-price proxy of the candle [T-60, T) against Kalshi's own
    60 s index values at instants T (proxy_model.py 'comp_tp'); points {T_seconds: value}."""
    errs = []
    for T, v in points.items():
        c, b = cb.get(T - 60), bs.get(T - 60)
        if not (c and b) or not v > 0:
            continue
        prox = (sum(c) / 4.0 + sum(b) / 4.0) / 2.0
        errs.append(1e4 * math.log(prox / v))
    return errs


def fit_c_asset(markets, closes: dict, cb: dict, bs: dict):
    """DECLARE: c = vol multiplier on development markets only, grid 0.6..1.6 step 0.05, minimum Brier over
    t = open+300 s, +600 s, +780 s; e = stdev (log) of the proxy error measured on the same development
    markets' index instants.  S and s1m are the tool's definitions; rows with s1m == 0 or missing inputs are
    skipped.  e is rounded to 3 decimals of a basis point, as in proxy_model_out.json."""
    pts = {}
    for m in markets:
        try:
            ev = float(m.expiration_value)
        except (TypeError, ValueError):
            ev = None
        if ev is not None and ev > 0:
            pts[m.close_us // US] = ev
        pts[m.open_us // US] = float(m.strike)
    errs = proxy_error_bps(cb, bs, pts)
    e_exact = float(np.std(np.asarray(errs, dtype=np.float64))) if errs else float("nan")
    e_bps = round(e_exact, 3)
    rows = []
    skipped = 0
    for m in markets:
        o = m.open_us // US
        y = 1.0 if m.result == "yes" else 0.0
        for d in FIT_INSTANTS_S:
            t = o + d
            S, s = last_candle_close(closes, t), trailing_vol(closes, t)
            if S is None or s is None or not s > 0:
                skipped += 1
                continue
            rows.append((S, float(m.strike), s, MARKET_DURATION_S - d, y))
    if not rows:
        return {"c": None, "brier": None, "n_rows": 0, "n_rows_skipped": skipped, "e_bps": e_bps,
                "e_bps_unrounded": e_exact, "n_proxy_points": len(errs), "n_markets": len(markets),
                "grid_brier": {}}
    brier = {}
    for c in FIT_GRID:
        brier[c] = float(np.mean([(fair_yes(S, K, s, tau, c, e_bps / 1e4) - y) ** 2 for S, K, s, tau, y in rows]))
    best = min(FIT_GRID, key=lambda c: brier[c])
    return {"c": best, "brier": brier[best], "n_rows": len(rows), "n_rows_skipped": skipped, "e_bps": e_bps,
            "e_bps_unrounded": e_exact, "n_proxy_points": len(errs), "n_markets": len(markets),
            "grid_brier": {str(c): brier[c] for c in FIT_GRID}}


def fit_c(split, markets_root, candles_dir, out_dir):
    """Re-derive c (and e) per asset on the development split only.  Reads dev market metadata incl. result
    and expiration_value, and the Coinbase/Bitstamp candle files; no trade row, no other split."""
    if split != "dev":
        raise Refusal("fit-c is development-only (DECLARE: c fit on development only)")
    markets, excl, _oos = load_markets(markets_root, "dev", with_outcomes=True, with_expiration=True)
    ohlc = load_candle_ohlc(candles_dir)
    res = {"meta": {"split": "dev", "dates": list(SPLITS["dev"]), "tool_sha256": sha256_file(__file__),
                    "git_sha": git_sha(), "instants_s": list(FIT_INSTANTS_S), "grid": FIT_GRID,
                    "excluded_markets": excl, "previous_frozen_c": FROZEN_C,
                    "previous_frozen_e_bps": FROZEN_E_BPS}, "assets": {}}
    for _series, (asset, _prod) in SERIES.items():
        ms = [m for m in markets if m.asset == asset]
        closes = {t: v[3] for t, v in ohlc[asset]["cb"].items()}
        res["assets"][asset] = fit_c_asset(ms, closes, ohlc[asset]["cb"], ohlc[asset]["bs"])
    res["differs_from_current_frozen_c"] = {a: r["c"] != FROZEN_C[a] for a, r in res["assets"].items()}
    res["differs_from_current_frozen_e"] = {a: r["e_bps"] != FROZEN_E_BPS[a] for a, r in res["assets"].items()}
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(out_dir) / "fit_c_dev.json", "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True, default=_json_default)
    return res


# ------------------------------------------------------------------------------------------ signals
def compute_signals(markets, candles):
    """P_yes at t = close-180s; a signal when P >= 0.90 (favoured YES) or P <= 0.10 (favoured NO)."""
    sig, skip = {}, defaultdict(int)
    for m in markets:
        t_us = m.close_us - DECISION_BEFORE_CLOSE_S * US
        p = fair_at(candles[m.asset], m.asset, m.strike, m.close_us, t_us)
        if p is None:
            skip["no_model_inputs"] += 1
            continue
        if p >= P_LOCK:
            sig[m.ticker] = {"side": "yes", "p_yes": p, "p_fav": p, "t_us": t_us}
        elif p <= P_LOCK_LOW:
            sig[m.ticker] = {"side": "no", "p_yes": p, "p_fav": 1.0 - p, "t_us": t_us}
    return sig, dict(skip)


# ----------------------------------------------------------------------------------------- io: trades
def _trade_files(store: str):
    base = Path(store)
    if (base / "trades").is_dir():
        base = base / "trades"
    for f in sorted(base.rglob("*.parquet")):
        ser = [p[len("series="):] for p in f.parts if p.startswith("series=")]
        if ser and ser[-1] not in SERIES:
            continue
        yield f


def _stat_us(v):
    if isinstance(v, dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=dt.timezone.utc)
        return (v - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)) // dt.timedelta(microseconds=1)
    return None


def _rg_span(pf, i):
    try:
        j = pf.schema_arrow.names.index("created_time")
        st = pf.metadata.row_group(i).column(j).statistics
        if st is not None and st.has_min_max:
            a, b = _stat_us(st.min), _stat_us(st.max)
            if a is not None and b is not None:
                return a, b
    except Exception:
        pass
    return None


class TradeScan:
    """Streams trade files, keeping per-ticker state only for `wanted` tickers.

    wanted: {ticker: (t_us, close_us)}.  values=False reads the id columns only (trade_id, ticker,
    created_time, is_block_trade): used by --count-only, never touches a price.  values=True keeps, per
    ticker, the last non-block print before t and every non-block print in (t, close]."""

    def __init__(self, wanted, values: bool, forbidden=(), max_rss_mb=MAX_RSS_MB_DEFAULT, checks: bool = False):
        """checks=True (dev count-only only): also read the value columns to report null counts and the share of
        prints off the tick grid by price region; nothing price-like is kept per market."""
        if checks and values:
            raise ValueError("checks is a count-only mode")
        self.checks = checks
        self.nulls = defaultdict(int)
        self.grid = {r: [0, 0, 0] for r in ("lt_0.10", "0.10_to_0.90", "gt_0.90")}   # n, off 0.01, off 0.001
        self.ct_min = self.ct_max = None
        self.wanted, self.values, self.forbidden, self.max_rss_mb = wanted, values, set(forbidden), max_rss_mb
        if self.forbidden & set(wanted):
            raise Refusal("wanted tickers overlap forbidden (out-of-split) tickers")
        self.seen = set()
        self.last = {}                        # ticker -> (created_us, trade_id, yes_micro, no_micro)
        self.rows = defaultdict(list)         # ticker -> [(created_us, trade_id, yes, no, taker, count)]
        self.has_prior, self.has_window = set(), set()
        self.c = defaultdict(int)
        self.value_reads = set()              # tickers of every row whose value columns were materialised
        self.rg_value_reads = 0
        self.hi_us = max((v[1] for v in wanted.values()), default=0)
        self.lo_us = min((v[1] for v in wanted.values()), default=0) - MARKET_DURATION_S * US

    def _guard_rss(self):
        mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        self.c["peak_rss_mb"] = int(max(self.c["peak_rss_mb"], mb))
        if mb > self.max_rss_mb:
            raise Refusal(f"peak RSS {mb:.0f} MB exceeds --max-rss-mb {self.max_rss_mb}")

    def scan(self, stores):
        if not self.wanted:
            return self
        allowed = pa.array(sorted(self.wanted))
        for store in stores:
            for f in _trade_files(store):
                pf = pq.ParquetFile(f)
                names = set(pf.schema_arrow.names)
                if any(n.lower().startswith("mve") for n in names):
                    raise Refusal(f"mve_* column in trade file {f}")
                self.c["files_seen"] += 1
                for i in range(pf.metadata.num_row_groups):
                    span = _rg_span(pf, i)
                    if span is not None and (span[1] < self.lo_us or span[0] > self.hi_us):
                        self.c["row_groups_pruned_by_time"] += 1
                        continue
                    ids = pf.read_row_group(i, columns=ID_COLS)
                    mask = pc.is_in(ids.column("ticker"), value_set=allowed)
                    n_in = int(pc.sum(pc.cast(mask, pa.int64())).as_py() or 0)
                    self.c["rows_id_scanned"] += ids.num_rows
                    if n_in == 0:
                        continue
                    ids = ids.filter(mask)
                    vals = None
                    if self.values or self.checks:
                        vals = pf.read_row_group(i, columns=VAL_COLS).filter(mask)
                        self.rg_value_reads += 1
                    self._ingest(ids, vals)
                    self._guard_rss()
        return self

    def _ingest(self, ids, vals):
        tick = ids.column("ticker").to_pylist()
        tid = ids.column("trade_id").to_pylist()
        cu = _ts_us(ids.column("created_time"))
        cnull = pc.is_null(ids.column("created_time")).to_pylist()
        blk = ids.column("is_block_trade").to_pylist()
        has_vals = vals is not None
        if has_vals:
            yp = vals.column("yes_price").to_pylist()
            npx = vals.column("no_price").to_pylist()
            cnt = vals.column("count").to_pylist()
            tk = vals.column("taker_side").to_pylist()
        for j in range(len(tick)):
            tkr = tick[j]
            if tkr in self.forbidden or tkr not in self.wanted or tkr.startswith("KXMVE"):
                raise Refusal(f"row of out-of-split/forbidden ticker {tkr} reached the accumulator")
            self.c["rows_in_split_markets"] += 1
            for name, v in (("trade_id", tid[j]), ("is_block_trade", blk[j])):
                if v is None:
                    self.nulls[name] += 1
            if cnull[j]:
                self.nulls["created_time"] += 1
            if has_vals:
                for name, v in (("yes_price", yp[j]), ("no_price", npx[j]), ("count", cnt[j]), ("taker_side", tk[j])):
                    if v is None:
                        self.nulls[name] += 1
            if tid[j] is None:                      # never collapsed by the dedupe: a counted exclusion
                self.c["null_trade_id_excluded"] += 1
                continue
            if tid[j] in self.seen:
                self.c["duplicates"] += 1
                continue
            self.seen.add(tid[j])
            if cnull[j]:
                self.c["null_created_time_excluded"] += 1
                continue
            c = int(cu[j])
            self.ct_min = c if self.ct_min is None else min(self.ct_min, c)
            self.ct_max = c if self.ct_max is None else max(self.ct_max, c)
            if self.checks and yp[j] is not None and yp[j] == yp[j]:
                y = yp[j]
                g = self.grid["lt_0.10" if y < 0.10 else ("gt_0.90" if y > 0.90 else "0.10_to_0.90")]
                g[0] += 1
                g[1] += abs(y * 100 - round(y * 100)) > 1e-6
                g[2] += abs(y * 1000 - round(y * 1000)) > 1e-6
            if blk[j] is None:                      # unknown is not "not a block trade": counted exclusion
                self.c["null_is_block_trade_excluded"] += 1
                continue
            if blk[j]:
                self.c["block_trades_excluded"] += 1
                continue
            t_us, close_us = self.wanted[tkr]
            end_us = close_us - WINDOW_END_BEFORE_CLOSE_S * US
            if not self.values:
                if c < t_us:
                    self.has_prior.add(tkr)
                elif t_us < c <= end_us:
                    self.has_window.add(tkr)
                continue
            self.value_reads.add(tkr)
            ym, nm = yp[j], npx[j]
            if ym is None or nm is None or ym != ym or nm != nm:        # null/NaN price: counted, not fatal
                self.c["null_price_excluded"] += 1
                continue
            if not (0.0 <= ym <= 1.0 and 0.0 <= nm <= 1.0):             # a units error is systematic: refuse
                raise Refusal(f"trade price outside [0,1] (units?) in {tkr}: {ym}, {nm}")
            ym, nm = to_micro(ym), to_micro(nm)
            if abs(ym + nm - US) > 1:
                self.c["price_sum_anomaly_excluded"] += 1
                continue
            if not (cnt[j] or 0) > 0 or tk[j] not in ("yes", "no"):
                self.c["bad_count_or_taker_side_excluded"] += 1
                continue
            if c > close_us:
                self.c["after_close_excluded"] += 1
                continue
            if c < t_us:
                cur = self.last.get(tkr)
                if cur is None or (c, tid[j]) > (cur[0], cur[1]):
                    self.last[tkr] = (c, tid[j], ym, nm)
            elif c > t_us:
                self.rows[tkr].append((c, tid[j], ym, nm, tk[j], cnt[j]))
        return

    def structure_checks(self):
        iso = lambda v: None if v is None else dt.datetime.fromtimestamp(v / US, dt.timezone.utc).isoformat()
        out = {"created_time_min_iso": iso(self.ct_min), "created_time_max_iso": iso(self.ct_max),
               "null_counts_per_column": dict(self.nulls)}
        if self.checks:
            out["yes_price_off_grid_share_by_region"] = {
                r: {"n": g[0], "off_0.01_grid_share": (g[1] / g[0]) if g[0] else None,
                    "off_0.001_grid_share": (g[2] / g[0]) if g[0] else None} for r, g in self.grid.items()}
        return out


# --------------------------------------------------------------------------------- ledger + holdout lock
def sha256_file(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def git_sha() -> str:
    try:
        r = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def ledger_path() -> str:
    env = os.environ.get(LEDGER_ENV)
    if env:
        if os.environ.get(TEST_ENV) != "1":
            raise Refusal(f"{LEDGER_ENV} is only honoured when {TEST_ENV}=1")
        return env
    if os.environ.get(TEST_ENV) == "1":
        raise Refusal(f"{TEST_ENV}=1 requires an explicit {LEDGER_ENV}; the real ledger is never written by tests")
    return LEDGER_DEFAULT


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ledger_lines(path: str):
    if not os.path.exists(path):
        return []
    out = []
    for ln in open(path):
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except ValueError:
                out.append({"unparseable": ln})
    return out


def check_ledger_writable(path: str):
    """The ledger directory must exist on this host (a run on the wrong host must not see an empty ledger as
    free) and be writable, so that 'started' can be written after the lock."""
    d = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(d):
        raise Refusal(f"ledger directory {d} does not exist on this host (wrong host?)")
    if not os.access(d, os.W_OK | os.X_OK) or (os.path.exists(path) and not os.access(path, os.W_OK)):
        raise Refusal(f"ledger {path} is not writable")


def ledger_append(path: str, split: str, mode: str, status: str, out_dir: str, extra: dict = None):
    rec = {"ts": _now(), "split": split, "mode": mode, "status": status, "git_sha": git_sha(),
           "tool_sha256": sha256_file(__file__), "out": str(out_dir), "tick_rounding": "ceil_to_tick",
           "frozen_c": FROZEN_C, "frozen_e_bps": FROZEN_E_BPS, **(extra or {})}
    with open(path, "a") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def check_holdout_free(out_dir: str, ledger: str):
    """Side-effect-free precheck: refuse if the global ledger already holds a holdout run line or the lock exists."""
    for rec in ledger_lines(ledger):
        if "unparseable" in rec:                                  # fail closed: a torn line may be a started run
            raise Refusal(f"unparseable line in ledger {ledger}: holdout refused until it is resolved")
        if rec.get("split") == "holdout" and rec.get("mode") == "run":
            raise Refusal(f"holdout already run or started (ledger {ledger}: {rec})")
    if os.path.exists(os.path.join(out_dir, "HOLDOUT.lock")):
        raise Refusal(f"{os.path.join(out_dir, 'HOLDOUT.lock')} exists: the holdout is run once")


def check_holdout_count_only_free(ledger: str):
    """The holdout id-only count may be taken once."""
    for rec in ledger_lines(ledger):
        if "unparseable" in rec:
            raise Refusal(f"unparseable line in ledger {ledger}: holdout refused until it is resolved")
        if rec.get("split") == "holdout" and rec.get("mode") == "count_only":
            raise Refusal(f"holdout count-only already taken (ledger {ledger}: {rec})")


def preflight(split, stores, markets_root, candles_dir, max_rss_mb=None, need_result=True):
    """Everything that can be checked without a trade row or a result: a failure here must cost nothing (no lock,
    no ledger line).  Stores must exist and hold universe parquet; the candle files must exist; the split must
    have markets with model inputs."""
    for st in stores:
        if not Path(st).is_dir():
            raise Refusal(f"trades store missing: {st}")
        if next(iter(_trade_files(st)), None) is None:
            raise Refusal(f"trades store holds no parquet for the universe: {st}")
    if not Path(markets_root).is_dir():
        raise Refusal(f"markets root missing: {markets_root}")
    cdir = Path(candles_dir)
    if not cdir.is_dir():
        raise Refusal(f"candles dir missing: {candles_dir}")
    for _series, (_asset, prod) in SERIES.items():
        if not (cdir / f"cb_{prod}.json").is_file():
            raise Refusal(f"candle file missing: {cdir / f'cb_{prod}.json'}")
    info = {}
    markets, _excl, oos = load_markets(markets_root, split, with_outcomes=False, info=info)   # no result column
    if not markets:
        raise Refusal(f"split {split} has 0 markets in {markets_root}")
    both = oos & {m.ticker for m in markets}
    if both:                                    # TradeScan would raise on this after the lock
        raise Refusal(f"{len(both)} ticker(s) are both in and out of the split, e.g. {sorted(both)[:3]}")
    need = MARKET_COLS_PHASE2 | ({"result"} if need_result else set())
    for f, names in info.get("in_split_files", []):
        if need - set(names):
            raise Refusal(f"markets file {f} lacks columns {sorted(need - set(names))} needed after the lock")
    _sig, skip = compute_signals(markets, load_candles(candles_dir))
    if skip.get("no_model_inputs", 0) == len(markets):
        raise Refusal("no market of the split has model inputs: the candle files do not cover it")
    if max_rss_mb is not None:                  # the limit must leave a 2x margin over what is already held
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        if peak >= 0.5 * max_rss_mb:
            raise Refusal(f"peak RSS after preflight is {peak:.0f} MB; --max-rss-mb {max_rss_mb} leaves less "
                          f"than a 2x margin (need >= {2 * peak:.0f})")
    return len(markets)


def acquire_holdout_lock(out_dir: str, ledger: str):
    """Run-once: refuse if the global ledger already holds a holdout run line, then O_EXCL the lock file."""
    check_holdout_free(out_dir, ledger)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "HOLDOUT.lock")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    except FileExistsError:
        raise Refusal(f"{path} exists: the holdout is run once")
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps({"ts": _now(), "git_sha": git_sha(), "tool_sha256": sha256_file(__file__)}) + "\n")
    return path


# ---------------------------------------------------------------------------------------------- run
def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def run(split, trades_stores, markets_root, candles_dir, out_dir, count_only=False,
        allow_holdout_count_only=False, max_rss_mb=MAX_RSS_MB_DEFAULT):
    """Order for the holdout run: ledger/lock precheck (no side effect) -> preflight (no trade row, no result) ->
    lock + ledger 'started' -> only then the markets' result column and the trade files.  Any exception after the
    lock writes 'aborted' and the holdout stays spent."""
    if split not in SPLITS:
        raise Refusal(f"unknown split {split}")
    ledger = ledger_path()
    check_ledger_writable(ledger)                                # missing directory = wrong host: no stray lock
    if split == "holdout" and count_only and not allow_holdout_count_only:
        raise Refusal("--count-only on the holdout needs --allow-holdout-count-only (reads ids only)")
    if split == "holdout" and count_only:
        check_holdout_count_only_free(ledger)
    if split == "holdout" and not count_only:
        check_holdout_free(out_dir, ledger)                      # a repeat run does nothing at all
    preflight(split, trades_stores, markets_root, candles_dir, max_rss_mb=max_rss_mb,
              need_result=not count_only)                        # refusals here leave no lock and no ledger line
    mode = "count_only" if count_only else "run"
    os.makedirs(out_dir, exist_ok=True)
    if split == "holdout" and not count_only:
        acquire_holdout_lock(out_dir, ledger)                    # before the result column / any trade file
    ledger_append(ledger, split, mode, "started", out_dir)
    try:
        markets, excl, out_of_split = load_markets(markets_root, split, with_outcomes=not count_only)
        candles = load_candles(candles_dir)
        signals, sig_skip = compute_signals(markets, candles)
        by_ticker = {m.ticker: m for m in markets}
        meta = {"split": split, "dates": list(SPLITS[split]), "mode": mode, "git_sha": git_sha(),
                "tool_sha256": sha256_file(__file__), "frozen_c": FROZEN_C, "frozen_e_bps": FROZEN_E_BPS,
                "tick_rounding": "ceil_to_tick",
                "declare_sha256": sha256_file(DECLARE_PATH) if os.path.exists(DECLARE_PATH) else None,
                "stores": [str(s) for s in trades_stores]}
        if count_only:
            res = _count_only(split, markets, excl, signals, sig_skip, by_ticker, out_of_split,
                              trades_stores, max_rss_mb, meta)
        else:
            res = _full_run(split, markets, excl, signals, sig_skip, by_ticker, out_of_split, candles,
                            trades_stores, out_dir, max_rss_mb, meta)
    except BaseException as e:
        spent = split == "holdout" and not count_only            # the lock is taken: this holdout is spent
        ledger_append(ledger, split, mode, "aborted", out_dir,
                      {"error": f"{type(e).__name__}: {str(e)[:300]}", "holdout_spent": spent})
        if spent:
            msg = f"HOLDOUT SPENT (lock taken, ledger 'aborted'): {type(e).__name__}: {e}"
            if isinstance(e, Refusal):
                raise Refusal(msg) from e
            print(msg, file=sys.stderr)
        raise
    ledger_append(ledger, split, mode, "finished", out_dir)
    return res


def _split_counts(markets):
    per = defaultdict(lambda: defaultdict(int))
    for m in markets:
        per[m.series][m.close_date] += 1
    return {s: dict(sorted(d.items())) for s, d in sorted(per.items())}


def _count_only(split, markets, excl, signals, sig_skip, by_ticker, out_of_split, stores, max_rss_mb, meta):
    wanted = {m.ticker: (signals[m.ticker]["t_us"] if m.ticker in signals
                         else m.close_us - DECISION_BEFORE_CLOSE_S * US, m.close_us) for m in markets}
    scan = TradeScan(wanted, values=False, forbidden=out_of_split, max_rss_mb=max_rss_mb,
                     checks=(split == "dev")).scan(stores)             # holdout: ids only, no value column
    win_by = defaultdict(int)
    for t in scan.has_window:
        win_by[by_ticker[t].series] += 1
    sig_by, sig_prior_by = defaultdict(int), defaultdict(int)
    for t in signals:
        sig_by[by_ticker[t].series] += 1
        if t in scan.has_prior:
            sig_prior_by[by_ticker[t].series] += 1
    res = {"meta": meta, "markets_per_series_per_day": _split_counts(markets), "n_markets": len(markets),
           "excluded": excl, "markets_with_any_print_in_window": dict(win_by),
           "signals_by_series": dict(sig_by), "signals_with_a_prior_print_by_series": dict(sig_prior_by),
           "signals_skipped": sig_skip, "trade_rows": {k: v for k, v in scan.c.items()},
           "structure_checks": scan.structure_checks(),
           "note": "structure counts only"}
    return res


def _build_fills(signals, by_ticker, scan, candles, strict_below):
    """Fills of one cell for every signal market with a prior favoured print (re-simulated per cell)."""
    fills_rows, no_prior, no_prior_by = [], 0, defaultdict(int)
    for tkr in sorted(signals):
        m, s = by_ticker[tkr], signals[tkr]
        last = scan.last.get(tkr)
        if last is None:
            no_prior += 1
            no_prior_by[m.series] += 1
            continue
        side = s["side"]
        last_px = last[2] if side == "yes" else last[3]
        bid = choose_bid(side, s["p_fav"], last[2], last[3])
        if bid <= 0:
            continue
        rows = sorted(scan.rows.get(tkr, []), key=lambda r: (r[0], r[1]))
        end_us = m.close_us - WINDOW_END_BEFORE_CLOSE_S * US
        times = [r[0] for r in rows]
        for created, tid in simulate_fills(rows, side, bid, s["t_us"], end_us, strict_below):
            win = (side == m.result)
            payoff = US if win else 0
            fee_c, fee_m = maker_fee_micro_pair(bid)
            tfee_c, _ = taker_fee_micro_pair(bid)
            mk = {}
            for h in MARKOUT_H_S:
                ref = created + h * US
                py = fair_at(candles[m.asset], m.asset, m.strike, m.close_us, ref)
                if py is None:
                    mk[f"mk{h}_fair_cent"] = float("nan")
                else:
                    mk[f"mk{h}_fair_cent"] = ((py if side == "yes" else 1.0 - py) * US - bid) / 1e4
                k = bisect.bisect_left(times, ref)
                if k < len(rows):
                    px = rows[k][2] if side == "yes" else rows[k][3]
                    mk[f"mk{h}_print_cent"] = (px - bid) / 1e4
                else:
                    mk[f"mk{h}_print_cent"] = float("nan")
            fills_rows.append({
                "ticker": tkr, "event_ticker": m.event, "series": m.series, "close_date": m.close_date,
                "close_time_us": m.close_us, "side": side, "quote_time_us": s["t_us"], "fill_time_us": created,
                "fill_trade_id": tid, "bid_micro": bid, "last_print_micro": last_px, "p_yes": s["p_yes"],
                "p_fav": s["p_fav"], "count": 1, "win": win, "payoff_micro": payoff,
                "fee_maker_cent_micro": fee_c, "fee_maker_micro_micro": fee_m, "net_cent_micro": payoff - bid - fee_c,
                "net_micro_micro": payoff - bid - fee_m, "taker_equiv_fee_cent_micro": tfee_c,
                "dropped_15": False, **mk})
    fills_rows.sort(key=lambda r: (r["ticker"], r["fill_time_us"], r["fill_trade_id"]))
    return fills_rows, no_prior, dict(no_prior_by)


def _fcol(rows, name):
    return np.array([r[name] for r in rows])


def _cell_rows(rows):
    if not rows:
        return cell_stats([], [], [], [], [])
    return cell_stats(_fcol(rows, "net_cent_micro"), _fcol(rows, "net_micro_micro"), _fcol(rows, "bid_micro"),
                      _fcol(rows, "event_ticker"), _fcol(rows, "close_date"), win=_fcol(rows, "win"),
                      windows=_fcol(rows, "close_time_us"))


def _write_fills(path, rows):
    tbl = pa.Table.from_pylist(rows, schema=FILL_SCHEMA) if rows else FILL_SCHEMA.empty_table()
    pq.write_table(tbl, path)


POPULATION_LABEL = "R3 on signal markets with a print in [close-300 s, close-180 s)"
QUEUE_BACK_RULE = ("pressure-model stand-in (QP pre-registered): a print AT our bid does not fill; only a print "
                   "strictly below our bid on our side with the taker selling our side fills; same window "
                   "(t, close-60 s], same 30 s spacing, fill at our bid, no random drop, re-simulated")


def _full_run(split, markets, excl, signals, sig_skip, by_ticker, out_of_split, candles, stores, out_dir,
              max_rss_mb, meta):
    wanted = {t: (s["t_us"], by_ticker[t].close_us) for t, s in signals.items()}
    scan = TradeScan(wanted, values=True, forbidden=out_of_split, max_rss_mb=max_rss_mb).scan(stores)
    assert set(scan.value_reads) <= set(wanted)
    fills_rows, no_prior, no_prior_by = _build_fills(signals, by_ticker, scan, candles, strict_below=False)
    qb_rows, _, _ = _build_fills(signals, by_ticker, scan, candles, strict_below=True)
    drop = unfilled_mask(len(fills_rows))
    for r, d in zip(fills_rows, drop):
        r["dropped_15"] = bool(d)
    kept = [r for r in fills_rows if not r["dropped_15"]]
    per_series = {s: _basic_rows([r for r in fills_rows if r["series"] == s]) for s in SERIES}

    def mean_valid(name):
        v = _fcol(fills_rows, name).astype(float) if fills_rows else np.array([])
        v = v[~np.isnan(v)] if len(v) else v
        return {"n": int(len(v)), "mean_cent": float(v.mean()) if len(v) else None}

    stress = None
    if fills_rows:
        sn = _fcol(fills_rows, "net_cent_micro") - _fcol(fills_rows, "taker_equiv_fee_cent_micro")
        stress = {"note": "NOT in DECLARE: maker charged the taker formula, cent rounded up",
                  "n_contracts": len(sn), "total_net_cent": float(sn.sum() / 1e4),
                  "mean_net_cent": float(sn.mean() / 1e4)}
    cells = {
        "main": _cell_rows(fills_rows),
        "maker_15pct_unfilled": {**_cell_rows(kept), "n_dropped": int(drop.sum()), "drop_rule":
                                 "ceil(0.15*N) fills via RandomState(1).choice over fills sorted by "
                                 "(ticker, fill_time, trade_id)"},
        "queue_back": {**_cell_rows(qb_rows), "rule": QUEUE_BACK_RULE},
    }
    summary = {
        "meta": dict(meta), "rule": "R3 late-lock maker 15M",
        "headline": {"population": POPULATION_LABEL, "n_markets_in_split": len(markets),
                     "n_signals": len(signals), "signals_by_series": _by_series(signals, by_ticker),
                     "signals_without_prior_favoured_print": no_prior,
                     "signals_without_prior_favoured_print_by_series": no_prior_by,
                     "excluded_markets": excl, "signals_skipped": sig_skip,
                     "n_filled_events": {k: v["n_events"] for k, v in cells.items()},
                     "n_contracts": {k: v["n_contracts"] for k, v in cells.items()}},
        "n_markets_in_split": len(markets), "excluded_markets": excl, "n_signals": len(signals),
        "signals_by_series": _by_series(signals, by_ticker), "signals_skipped": sig_skip,
        "signals_without_prior_favoured_print": no_prior,
        "trade_rows": dict(scan.c), "duplicates_removed": scan.c.get("duplicates", 0),
        "peak_rss_mb": scan.c.get("peak_rss_mb", 0),
        **cells,
        "ci90_lo_decider": {k: v["ci90_lo_decider"] for k, v in cells.items()},
        "per_series": per_series,
        "markout_cent": {k: mean_valid(k) for k in
                         ("mk10_fair_cent", "mk60_fair_cent", "mk10_print_cent", "mk60_print_cent")},
        "informational": {"stress_taker_equiv_fee": stress},
        "gate": "not computed by this tool; the manager applies the lab promotion gate",
    }
    outp = Path(out_dir)
    outp.mkdir(parents=True, exist_ok=True)
    _write_fills(outp / f"r3_{split}_fills.parquet", fills_rows)
    _write_fills(outp / f"r3_{split}_fills_queue_back.parquet", qb_rows)
    with open(outp / f"r3_{split}_summary.json", "w") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True, default=_json_default)
    return summary


def _by_series(signals, by_ticker):
    d = defaultdict(int)
    for t in signals:
        d[by_ticker[t].series] += 1
    return dict(d)


def _basic_rows(rows):
    if not rows:
        return _basic(np.array([]), np.array([]), [], [])
    net = np.array([r["net_cent_micro"] for r in rows], dtype=np.float64)
    ex = np.array([r["net_micro_micro"] for r in rows], dtype=np.float64)
    b = _basic(net, ex, [r["event_ticker"] for r in rows], [r["close_date"] for r in rows])
    b["win_rate"] = float(np.mean([r["win"] for r in rows]))
    return b


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kalshi_crypto_r3", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--split", required=True, choices=sorted(SPLITS))
    r.add_argument("--trades-stores", nargs="+", required=True)
    r.add_argument("--markets-root", required=True)
    r.add_argument("--candles-dir", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--count-only", action="store_true")
    r.add_argument("--allow-holdout-count-only", action="store_true")
    r.add_argument("--max-rss-mb", type=int, default=None,
                   help=f"RSS guard (default {MAX_RSS_MB_DEFAULT}); REQUIRED for a holdout run: an abort on it "
                        "after the lock spends the holdout; size it from the dev peak_rss_mb with >= 2x margin")
    f = sub.add_parser("fit-c", help="re-derive c and e per asset on the development split only")
    f.add_argument("--split", required=True, choices=sorted(SPLITS))
    f.add_argument("--markets-root", required=True)
    f.add_argument("--candles-dir", required=True)
    f.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "fit-c":
        try:
            res = fit_c(a.split, a.markets_root, a.candles_dir, a.out)
        except Refusal as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 2
        for asset, r in res["assets"].items():
            b = "n/a" if r["brier"] is None else f"{r['brier']:.6f}"
            print(f"{asset} c={r['c']} brier={b} n_rows={r['n_rows']} e_bps={r['e_bps']}")
        return 0
    if a.split == "holdout" and not a.count_only and a.max_rss_mb is None:
        print("REFUSED: --max-rss-mb is required for a holdout run (an RSS abort after the lock spends the "
              "holdout; size it from the dev run's peak_rss_mb with at least 2x margin)", file=sys.stderr)
        return 2
    try:
        res = run(a.split, a.trades_stores, a.markets_root, a.candles_dir, a.out, a.count_only,
                  a.allow_holdout_count_only, a.max_rss_mb or MAX_RSS_MB_DEFAULT)
    except Refusal as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    if a.count_only:
        text = json.dumps(res, indent=1, sort_keys=True, default=_json_default)
        os.makedirs(a.out, exist_ok=True)
        Path(a.out, f"r3_{a.split}_counts.json").write_text(text)
        print(text)
    else:
        print(f"wrote {Path(a.out) / f'r3_{a.split}_summary.json'} and r3_{a.split}_fills.parquet "
              f"(n_fills={res['main']['n_contracts']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
