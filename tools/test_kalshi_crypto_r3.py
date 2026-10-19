"""Tests for tools/kalshi_crypto_r3.py.  Synthetic fixtures only: no Kalshi trade row, market or candle from
the lab data is read, no return is computed.  Spec: hunt18 kalshi-crypto DECLARE.md (rule R3)."""
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # repo root: ``tools`` is a package
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tools import kalshi_crypto_r3 as r3

US = 1_000_000
UTC = dt.timezone.utc


def us(iso):
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) * US


# ------------------------------------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def _test_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MAL_R3_TEST", "1")
    monkeypatch.setenv("MAL_R3_LEDGER", str(tmp_path / "ledger" / "R3_RUNS.jsonl"))
    (tmp_path / "ledger").mkdir()
    yield


def candle_rows(base, t0_iso, t1_iso):
    """1m candles [start, low, high, open, close, vol]; relative close noise 1e-4."""
    rows, i = [], 0
    for st in range(us(t0_iso) // US, us(t1_iso) // US, 60):
        c = base * (1.0 + 1e-4 * ((i * 7) % 5 - 2))
        rows.append([st, c, c, c, c, 1.0])
        i += 1
    return rows


def write_candles(d, t0="2026-10-05T04:00:00Z", t1="2026-10-14T00:00:00Z"):
    d.mkdir(exist_ok=True)
    bases = {"BTC-USD": 100.0, "ETH-USD": 2000.0, "SOL-USD": 50.0}
    for prod, base in bases.items():
        json.dump(candle_rows(base, t0, t1), open(d / f"cb_{prod}.json", "w"))
    return {"BTC": {r[0]: r[4] for r in candle_rows(100.0, t0, t1)},
            "ETH": {r[0]: r[4] for r in candle_rows(2000.0, t0, t1)}}


MARKET_SCHEMA = pa.schema([
    ("ticker", pa.string()), ("event_ticker", pa.string()), ("series_ticker", pa.string()),
    ("market_type", pa.string()), ("status", pa.string()), ("result", pa.string()),
    ("created_time", pa.timestamp("us", tz="UTC")), ("open_time", pa.timestamp("us", tz="UTC")),
    ("close_time", pa.timestamp("us", tz="UTC")), ("expiration_time", pa.timestamp("us", tz="UTC")),
    ("settlement_ts", pa.timestamp("us", tz="UTC")), ("floor_strike", pa.float64()),
    ("expiration_value", pa.string()),
    ("fetched_at", pa.timestamp("us", tz="UTC")), ("raw_json", pa.string()),
])


def mk_market(series, ticker, close_iso, strike, result, dur_s=900, settle_iso=None, raw=None, status="settled"):
    c = us(close_iso)
    o = c - dur_s * US
    s = us(settle_iso) if settle_iso else c + 60 * US
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0] + "-E", "series_ticker": series,
            "market_type": "binary", "status": status, "result": result, "created_time": o, "open_time": o,
            "close_time": c, "expiration_time": c, "settlement_ts": s, "floor_strike": strike,
            "expiration_value": None,
            "fetched_at": c + 3600 * US, "raw_json": json.dumps(raw if raw is not None else {"ticker": ticker})}


def write_markets(root, markets):
    for m in markets:
        d = Path(root) / f"series={m['series_ticker']}" / f"date={r3.utc_date(m['close_time'])}"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"part-{m['ticker']}.parquet"
        tbl = pa.Table.from_pylist([{k: (dt.datetime.fromtimestamp(v / US, UTC) if MARKET_SCHEMA.field(k).type
                                         == pa.timestamp("us", tz="UTC") else v) for k, v in m.items()}],
                                   schema=MARKET_SCHEMA)
        pq.write_table(tbl, f)


TRADE_SCHEMA = pa.schema([
    ("trade_id", pa.string()), ("ticker", pa.string()), ("series_ticker", pa.string()),
    ("created_time", pa.timestamp("us", tz="UTC")), ("yes_price", pa.float64()), ("no_price", pa.float64()),
    ("count", pa.float64()), ("taker_side", pa.string()), ("taker_book_side", pa.string()),
    ("is_block_trade", pa.bool_()), ("source", pa.string()),
])
_ctr = [0]


def tr(ticker, iso, yes, taker, block=False, count=3.0, tid=None, series="KXBTC15M"):
    _ctr[0] += 1
    return {"trade_id": tid or f"tid-{_ctr[0]:06d}", "ticker": ticker, "series_ticker": series,
            "created_time": dt.datetime.fromtimestamp(us(iso) / US, UTC), "yes_price": yes,
            "no_price": round(1.0 - yes, 6), "count": count, "taker_side": taker, "taker_book_side": taker,
            "is_block_trade": block, "source": "synthetic"}


def write_trades(store, rows, name="part-a", series="KXBTC15M", date="2026-10-05", row_group_size=None):
    d = Path(store) / "trades" / f"series={series}" / f"date={date}"
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=TRADE_SCHEMA), d / f"{name}.parquet",
                   row_group_size=row_group_size)


class World:
    """Dev-split fixture.  Candle noise is 1e-4 relative; K is set 2e-3 away from the last close so P is
    ~1 (or ~0); t = close - 180 s."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.closes = write_candles(tmp / "candles")
        self.markets = tmp / "markets"
        self.s1, self.s2 = tmp / "store1", tmp / "store2"

    def strike(self, asset, close_iso, kind):
        t_s = (us(close_iso) - 180 * US) / US
        S = r3.last_candle_close(self.closes[asset], t_s)
        return {"yes": S * math.exp(-2e-3), "no": S * math.exp(2e-3), "mid": S}[kind]


T1 = "2026-10-05T10:15:00Z"      # M1 close, t = 10:12:00, window end 10:14:00
T2 = "2026-10-05T10:30:00Z"
T3 = "2026-10-06T11:15:00Z"
T4 = "2026-10-06T12:00:00Z"
T6 = "2026-10-07T09:15:00Z"
T7 = "2026-10-07T13:15:00Z"
TH = "2026-10-10T00:00:00Z"      # holdout-split market (close date 10-10)


def build_world(tmp, with_holdout_rows=True):
    w = World(tmp)
    ms = [
        mk_market("KXBTC15M", "KXBTC15M-M1-15", T1, w.strike("BTC", T1, "yes"), "yes"),
        mk_market("KXBTC15M", "KXBTC15M-M2-30", T2, w.strike("BTC", T2, "no"), "no"),
        mk_market("KXBTC15M", "KXBTC15M-M3-15", T3, w.strike("BTC", T3, "yes"), "no"),     # favourite loses
        mk_market("KXBTC15M", "KXBTC15M-M4-00", T4, w.strike("BTC", T4, "mid"), "yes"),    # no signal
        mk_market("KXBTC15M", "KXBTC15M-M6-15", T6, w.strike("BTC", T6, "yes"), "yes"),    # no prior print
        mk_market("KXETH15M", "KXETH15M-M7-15", T7, w.strike("ETH", T7, "yes"), "yes"),
        mk_market("KXBTC15M", "KXBTC15M-HOLD-00", TH, w.strike("BTC", T1, "yes"), "yes"),  # holdout market
    ]
    write_markets(w.markets, ms)
    m1 = [
        tr("KXBTC15M-M1-15", "2026-10-05T10:10:00Z", 0.97, "yes"),
        tr("KXBTC15M-M1-15", "2026-10-05T10:11:30Z", 0.955, "no"),                  # last print before t
        tr("KXBTC15M-M1-15", "2026-10-05T10:12:00Z", 0.90, "no"),                   # == t: not > t
        tr("KXBTC15M-M1-15", "2026-10-05T10:12:05Z", 0.955, "no"),                  # fill 1
        tr("KXBTC15M-M1-15", "2026-10-05T10:12:20Z", 0.95, "no"),                   # < 30 s after fill 1
        tr("KXBTC15M-M1-15", "2026-10-05T10:12:36Z", 0.95, "no"),                   # fill 2
        tr("KXBTC15M-M1-15", "2026-10-05T10:12:40Z", 0.94, "yes"),                  # same-side taker: no
        tr("KXBTC15M-M1-15", "2026-10-05T10:12:50Z", 0.956, "no"),                  # price > bid
        tr("KXBTC15M-M1-15", "2026-10-05T10:13:10Z", 0.955, "no"),                  # fill 3
        tr("KXBTC15M-M1-15", "2026-10-05T10:13:40Z", 0.90, "no", block=True),       # block: ignored
        tr("KXBTC15M-M1-15", "2026-10-05T10:14:00Z", 0.95, "no"),                   # == close-60: fill 4
        tr("KXBTC15M-M1-15", "2026-10-05T10:14:01Z", 0.95, "no"),                   # after window end
    ]
    m2 = [
        tr("KXBTC15M-M2-30", "2026-10-05T10:26:00Z", 0.02, "yes"),                  # last no_price 0.98
        tr("KXBTC15M-M2-30", "2026-10-05T10:27:10Z", 0.04, "yes"),                  # taker buys YES; no_price .96
        tr("KXBTC15M-M2-30", "2026-10-05T10:27:50Z", 0.03, "yes"),                  # no_price 0.97 > bid
    ]
    m2.append(tr("KXBTC15M-M2-30", "2026-10-05T10:28:00Z", 0.04, "no"))             # taker buys NO: not opposite
    m3 = [
        tr("KXBTC15M-M3-15", "2026-10-06T11:10:00Z", 0.99, "yes"),
        tr("KXBTC15M-M3-15", "2026-10-06T11:12:05Z", 0.95, "no"),                   # fill, favourite lost
    ]
    m6 = [tr("KXBTC15M-M6-15", "2026-10-07T09:12:10Z", 0.90, "no")]                 # signal, nothing before t
    m7 = [
        tr("KXETH15M-M7-15", "2026-10-07T13:10:00Z", 0.97, "yes", series="KXETH15M"),
        tr("KXETH15M-M7-15", "2026-10-07T13:12:30Z", 0.96, "no", series="KXETH15M"),
    ]
    hold = [tr("KXBTC15M-HOLD-00", "2026-10-05T10:12:05Z", 0.95, "no"),
            tr("KXBTC15M-HOLD-00", "2026-10-05T10:13:05Z", 0.95, "no")]
    m4 = [tr("KXBTC15M-M4-00", "2026-10-06T11:59:30Z", 0.5, "no")]                  # after close-60: not a window print
    btc = m1 + m2 + m3 + m4 + m6
    write_trades(w.s1, btc + (hold if with_holdout_rows else []), "part-a")
    # store2 repeats M1's rows (same trade ids) and holds the ETH market
    write_trades(w.s2, m1, "part-a")
    write_trades(w.s2, m7, "part-eth", series="KXETH15M")
    return w


def run_dev(w, out, count_only=False, split="dev"):
    return r3.run(split, [str(w.s1), str(w.s2)], str(w.markets), str(w.tmp / "candles"), str(out), count_only)


# ----------------------------------------------------------------------------------------- unit tests
def test_fair_value_hand_computed():
    # x = 1e-3, s1m = 5e-4, c = 1, tau = 160 s -> window = (160-40)/60 = 2; e = 0 -> v = 5e-7... z = sqrt(2)
    K = 100.0
    S = K * math.exp(1e-3)
    p = r3.fair_yes(S, K, 5e-4, 160.0, 1.0, 0.0)
    assert p == pytest.approx(0.9213503964748575, abs=1e-12)          # Phi(sqrt 2), hand value
    # with e^2 = 5e-7 added: v = 1e-6, sqrt = 1e-3, z = 1 -> Phi(1)
    p = r3.fair_yes(S, K, 5e-4, 160.0, 1.0, math.sqrt(5e-7))
    assert p == pytest.approx(0.8413447460685429, abs=1e-12)
    # c scales the window term only: c = 2, e = 0 -> v = 4 * 5e-7 -> z = sqrt(2)/2
    assert r3.fair_yes(S, K, 5e-4, 160.0, 2.0, 0.0) == pytest.approx(r3.phi(math.sqrt(2) / 2), abs=1e-12)
    # tau < 60: window = s^2 * (tau/60)/3 = 3.6e-7*(30/60)/3 = 6e-8; z = 1 when x = sqrt(6e-8)
    S2 = K * math.exp(math.sqrt(6e-8))
    assert r3.fair_yes(S2, K, 6e-4, 30.0, 1.0, 0.0) == pytest.approx(0.8413447460685429, abs=1e-12)
    # S below K -> below 0.5, symmetric
    assert r3.fair_yes(K * math.exp(-1e-3), K, 5e-4, 160.0, 1.0, 0.0) == pytest.approx(1 - 0.9213503964748575, abs=1e-12)


def test_last_candle_and_vol_use_only_candles_ending_at_or_before_t():
    closes = {60 * i: 100.0 + i for i in range(0, 200)}
    t = 60 * 100                                  # candle [t-60, t) is the last one with end <= t
    assert r3.last_candle_close(closes, t) == closes[60 * 99]
    assert r3.last_candle_close(closes, t + 30) == closes[60 * 99]      # candle 100 not yet complete
    assert r3.last_candle_close(closes, t + 60) == closes[60 * 100]
    v = r3.trailing_vol(closes, t)
    rets = [math.log(closes[60 * (99 - k)] / closes[60 * (98 - k)]) for k in range(60)]
    assert v == pytest.approx(float(np.std(rets)), rel=1e-12)
    assert r3.trailing_vol({60 * i: 100.0 for i in range(30)}, 60 * 29) is None      # < 40 returns


def test_fees_cent_round_up_with_micro_alongside():
    # taker 0.07*C*P*(1-P): C=1, P=0.5 -> 0.0175 -> cent-up 0.02, micro 0.0175
    c, m = r3.taker_fee_micro_pair(500_000, 1)
    assert (c, m) == (20_000, 17_500)
    # exact-cent amount is not bumped: C=100, P=0.5 -> 1.75 exactly
    assert r3.taker_fee_micro_pair(500_000, 100) == (1_750_000, 1_750_000)
    # C=1, P=0.95 -> 0.003325 -> cent-up 0.01
    assert r3.taker_fee_micro_pair(950_000, 1) == (10_000, 3_325)
    # maker fee 0 (quadratic series)
    assert r3.maker_fee_micro_pair(950_000) == (0, 0)


def test_fill_rule_taker_side_price_time_spacing_and_window():
    t = us("2026-10-05T10:12:00Z")
    end = us("2026-10-05T10:14:00Z")

    def row(s, yes, taker, cnt=3.0):
        ym = int(round(yes * US))
        return (us(f"2026-10-05T{s}Z"), f"id-{s}-{taker}-{yes}", ym, US - ym, taker, cnt)

    rows = sorted([row("10:12:00", 0.90, "no"),            # == t: not > t
                   row("10:12:05", 0.955, "no"),           # fill
                   row("10:12:20", 0.95, "no"),            # 15 s after the fill: spacing
                   row("10:12:35", 0.95, "no"),            # exactly 30 s after: allowed
                   row("10:12:40", 0.94, "yes"),           # taker on the same side as our bid: no
                   row("10:12:50", 0.9561, "no"),          # print above bid
                   row("10:14:00", 0.95, "no"),            # == close-60: allowed (34 s... 85 s later)
                   row("10:14:01", 0.95, "no")])           # after window
    f = r3.simulate_fills(rows, "yes", 955_000, t, end)
    assert [x[0] // US % 3600 for x in f] == [12 * 60 + 5, 12 * 60 + 35, 14 * 60]
    # NO side: bid on NO, taker must have bought YES, price is no_price
    rows_no = sorted([(us("2026-10-05T10:12:10Z"), "a", 40_000, 960_000, "yes", 1.0),    # no_price 0.96 <= bid
                      (us("2026-10-05T10:12:50Z"), "b", 40_000, 960_000, "no", 1.0),     # wrong taker side
                      (us("2026-10-05T10:13:30Z"), "c", 30_000, 970_000, "yes", 1.0)])   # no_price 0.97 > bid
    assert [x[1] for x in r3.simulate_fills(rows_no, "no", 960_000, t, end)] == ["a"]


def test_bid_is_min_of_last_print_and_pfav_minus_4c_rounded_up_to_tick():
    # P_fav 0.9883 -> 0.9483 -> tick 0.001 above 0.90 -> 0.949 (rounded UP); last print lower wins
    assert r3.choose_bid("yes", 0.9883, 940_000, 60_000) == 940_000
    assert r3.choose_bid("yes", 0.9883, 990_000, 10_000) == 949_000
    assert r3.choose_bid("no", 0.9883, 10_000, 955_000) == 949_000          # NO side uses the no price
    assert r3.choose_bid("no", 0.9883, 10_000, 930_000) == 930_000
    # 0.01 tick between 0.10 and 0.90: 0.8651 -> 0.87
    assert r3.tick_ceil_micro(0.8651) == 870_000
    assert r3.tick_ceil_micro(0.96) == 960_000                              # float noise does not bump a tick


def test_favoured_side_selection_boundaries(monkeypatch):
    ms = [r3.Market(ticker=f"T{i}", close_us=us("2026-10-05T10:15:00Z"), strike=1.0, asset="BTC")
          for i in range(6)]
    ps = {"T0": 0.90, "T1": 0.8999, "T2": 0.10, "T3": 0.1001, "T4": 0.5, "T5": 0.999}
    monkeypatch.setattr(r3, "fair_at", lambda closes, asset, K, close_us, t_us: None)
    pending = iter(ms)

    def fake(closes, asset, K, close_us, t_us):
        return ps[next(pending).ticker]
    monkeypatch.setattr(r3, "fair_at", fake)
    sig, skip = r3.compute_signals(ms, {"BTC": {}})
    assert sig["T0"]["side"] == "yes" and sig["T0"]["p_fav"] == pytest.approx(0.90)
    assert "T1" not in sig and "T3" not in sig and "T4" not in sig
    assert sig["T2"]["side"] == "no" and sig["T2"]["p_fav"] == pytest.approx(0.90)
    assert sig["T5"]["side"] == "yes"
    assert all(s["t_us"] == us("2026-10-05T10:12:00Z") for s in sig.values())


def test_unfilled_15_is_exactly_ceil_and_seeded():
    d = r3.unfilled_mask(20)
    assert d.sum() == 3
    assert (d == r3.unfilled_mask(20)).all()
    idx = np.random.RandomState(1).choice(20, size=3, replace=False)
    assert set(np.flatnonzero(d)) == set(idx.tolist())
    assert r3.unfilled_mask(0).sum() == 0


def test_cell_stats_hand_numbers_and_bootstrap_seed():
    # six fills over 4 events, 2 days: nets (micro $) +45000 x3, +40000, -955000, +45000
    net = np.array([45_000, 45_000, 45_000, 40_000, -955_000, 45_000], dtype=float)
    price = np.array([955_000, 955_000, 955_000, 960_000, 955_000, 955_000], dtype=float)
    events = ["E1", "E1", "E1", "E2", "E3", "E4"]
    days = ["2026-10-05", "2026-10-05", "2026-10-05", "2026-10-05", "2026-10-06", "2026-10-06"]
    c = r3.cell_stats(net, net, price, events, days, win=net > 0)
    assert (c["n_contracts"], c["n_events"], c["n_days"]) == (6, 4, 2)
    assert c["mean_net_cent"] == pytest.approx(net.mean() / 1e4)
    assert c["mean_net_micro_cent"] == pytest.approx(net.mean() / 1e4)
    # ex-top-3 fills: drop three +45000 fills -> remaining 45000 + 40000 - 955000 + 45000... (3 of the 4 best)
    assert c["ex_top3_fills"]["n_contracts"] == 3
    assert c["ex_top3_fills"]["total_net_cent"] == pytest.approx((net.sum() - 135_000) / 1e4)
    # ex-top-3 events: E1 (135000), E4 (45000), E2 (40000) removed -> only E3 left
    assert c["ex_top3_events"]["n_contracts"] == 1
    assert c["ex_top3_events"]["total_net_cent"] == pytest.approx(-95.5)
    # best day = 10-05 (+175000)
    assert c["ex_best_day"]["removed_day"] == "2026-10-05"
    assert c["ex_best_day"]["n_contracts"] == 2
    # three added full-stake losses at the highest price (0.96)
    assert c["added3_worst_losses"]["n_contracts"] == 9
    assert c["added3_worst_losses"]["total_net_cent"] == pytest.approx((net.sum() - 3 * 960_000) / 1e4)
    # event-clustered bootstrap, 1000 draws, RandomState(1), 5th/95th percentile
    en, nn = np.array([135_000, 40_000, -955_000, 45_000]), np.array([3.0, 1, 1, 1])
    rs = np.random.RandomState(1)
    means = []
    for _ in range(1000):
        i = rs.randint(0, 4, size=4)
        means.append(en[i].sum() / nn[i].sum())
    assert c["ci90_event_cent"] == pytest.approx([np.percentile(means, 5) / 1e4, np.percentile(means, 95) / 1e4])
    assert c["bootstrap"]["draws"] == 1000 and c["bootstrap"]["seed"] == 1
    # empty cell is well formed
    assert r3.cell_stats([], [], [], [], [])["n_contracts"] == 0


# ---------------------------------------------------------------------------------- end-to-end (dev)
def test_end_to_end_dev_settlement_pnl_fees_clock_and_outputs(tmp_path):
    w = build_world(tmp_path)
    out = tmp_path / "out"
    s = run_dev(w, out)
    # signals: M1, M2, M3, M6 (BTC) and M7 (ETH); M4 (P = 0.5) none; the holdout market is never scored
    assert s["signals_by_series"] == {"KXBTC15M": 4, "KXETH15M": 1}
    assert s["signals_without_prior_favoured_print"] == 1                  # M6
    assert s["n_markets_in_split"] == 6
    fills = pq.read_table(out / "r3_dev_fills.parquet").to_pylist()
    by = {}
    for f in fills:
        by.setdefault(f["ticker"], []).append(f)
    # M1: last print before t = 0.955 (the == t print is not 'before t', the block trade is ignored)
    m1 = by["KXBTC15M-M1-15"]
    assert [f["bid_micro"] for f in m1] == [955_000] * 4
    assert [f["fill_time_us"] for f in m1] == [us(f"2026-10-05T{x}Z") for x in
                                              ("10:12:05", "10:12:36", "10:13:10", "10:14:00")]
    assert {f["quote_time_us"] for f in m1} == {us("2026-10-05T10:12:00Z")}     # close - 180 s, not settlement_ts
    assert all(f["win"] and f["net_cent_micro"] == 45_000 and f["fee_maker_cent_micro"] == 0 for f in m1)
    # M2: NO-favoured, bid min(0.98, ceil(P-0.04)=0.96) -> fill once at 0.96, wins: +0.04
    m2 = by["KXBTC15M-M2-30"]
    assert len(m2) == 1 and m2[0]["side"] == "no" and m2[0]["bid_micro"] == 960_000
    assert m2[0]["net_cent_micro"] == 40_000
    # M3: favourite loses -> -bid
    m3 = by["KXBTC15M-M3-15"]
    assert len(m3) == 1 and m3[0]["bid_micro"] == 960_000 and not m3[0]["win"]
    assert m3[0]["net_cent_micro"] == -960_000 and m3[0]["net_micro_micro"] == -960_000
    assert "KXBTC15M-M6-15" not in by and "KXBTC15M-M4-00" not in by and "KXBTC15M-HOLD-00" not in by
    # ETH market comes only from store2
    assert by["KXETH15M-M7-15"][0]["bid_micro"] == 960_000
    main = s["main"]
    assert main["n_contracts"] == len(fills) == 4 + 1 + 1 + 1
    exp_total = (4 * 45_000 + 40_000 - 960_000 + (1_000_000 - 960_000)) / 1e4
    assert main["total_net_cent"] == pytest.approx(exp_total)
    assert main["mean_net_cent"] == pytest.approx(exp_total / 7)
    assert main["n_events"] == 4 and main["n_days"] == 3
    assert set(s["per_series"]) == {"KXBTC15M", "KXETH15M", "KXSOL15M"}
    assert s["per_series"]["KXSOL15M"]["n_contracts"] == 0
    assert set(s["main"]["per_day"]) == {"2026-10-05", "2026-10-06", "2026-10-07"}
    assert s["maker_15pct_unfilled"]["n_dropped"] == math.ceil(0.15 * 7)
    assert s["maker_15pct_unfilled"]["n_contracts"] == 7 - math.ceil(0.15 * 7)
    assert sum(f["dropped_15"] for f in fills) == 2
    assert (out / "r3_dev_summary.json").exists()
    assert json.load(open(out / "r3_dev_summary.json"))["main"]["n_contracts"] == 7
    # markout against the model fair and the next print exist as numbers
    assert s["markout_cent"]["mk10_print_cent"]["n"] > 0
    assert s["peak_rss_mb"] < 2000


def test_dedupe_across_stores_keeps_one_and_reports_count(tmp_path):
    w = build_world(tmp_path)
    s = run_dev(w, tmp_path / "out")
    # store2 repeats the 12 M1 rows of store1 (11 are non-holdout rows of a signal market, all counted)
    assert s["duplicates_removed"] == 12
    # no double fills from the repeated rows
    fills = pq.read_table(tmp_path / "out" / "r3_dev_fills.parquet").to_pylist()
    ids = [f["fill_trade_id"] for f in fills]
    assert len(ids) == len(set(ids))


def test_decision_clock_is_close_time_not_settlement_ts(tmp_path):
    w = build_world(tmp_path)
    # rewrite M1 with a wildly different settlement_ts: nothing may change
    m = mk_market("KXBTC15M", "KXBTC15M-M1-15", T1, w.strike("BTC", T1, "yes"), "yes",
                  settle_iso="2026-10-20T00:00:00Z")
    for f in Path(w.markets).rglob("part-KXBTC15M-M1-15.parquet"):
        f.unlink()
    write_markets(w.markets, [m])
    s = run_dev(w, tmp_path / "out")
    fills = pq.read_table(tmp_path / "out" / "r3_dev_fills.parquet").to_pylist()
    assert {f["quote_time_us"] for f in fills if f["ticker"] == "KXBTC15M-M1-15"} == {us("2026-10-05T10:12:00Z")}
    assert s["main"]["n_contracts"] == 7


def test_non_900s_markets_and_unsettled_are_excluded_and_counted(tmp_path):
    w = build_world(tmp_path)
    extra = [mk_market("KXBTC15M", "KXBTC15M-LONG-15", "2026-10-08T10:15:00Z", 100.0, "yes", dur_s=1800),
             mk_market("KXBTC15M", "KXBTC15M-OPEN-15", "2026-10-08T11:15:00Z", 100.0, "", status="active")]
    write_markets(w.markets, extra)
    s = run_dev(w, tmp_path / "out")
    assert s["excluded_markets"] == {"bad_duration": 1, "result_not_yes_no": 1}


# ------------------------------------------------------------------------------- split / discipline
def test_split_refusal_holdout_ticker_never_read_in_dev(tmp_path, monkeypatch):
    w = build_world(tmp_path)
    # a holdout-split ticker stamped inside the dev markets' time span, in its own row group, in a file
    # that is not time-prunable
    write_trades(w.s1, [tr("KXBTC15M-HOLD-00", "2026-10-05T10:12:05Z", 0.95, "no"),
                        tr("KXBTC15M-HOLD-00", "2026-10-05T10:13:05Z", 0.95, "no")], "part-hold-only")
    # mixed row group: dev and holdout tickers share a row group
    write_trades(w.s2, [tr("KXBTC15M-HOLD-00", "2026-10-06T11:12:30Z", 0.95, "no"),
                        tr("KXBTC15M-M3-15", "2026-10-06T11:12:40Z", 0.95, "no", tid="mixed-m3")],
                 "part-mixed", date="2026-10-06")
    calls = []

    class SpyPF(pq.ParquetFile):
        def read_row_group(self, i, columns=None, **kw):
            tbl = super().read_row_group(i, columns=columns, **kw)
            if columns and "yes_price" in columns:
                tk = set(super().read_row_group(i, columns=["ticker"]).column("ticker").to_pylist())
                calls.append(tk)
            return tbl
    monkeypatch.setattr(r3.pq, "ParquetFile", SpyPF)
    s = run_dev(w, tmp_path / "out")
    assert calls, "value columns were read for the in-split signal markets"
    assert not any(tk <= {"KXBTC15M-HOLD-00"} for tk in calls)          # a holdout-only row group: never value-read
    fills = pq.read_table(tmp_path / "out" / "r3_dev_fills.parquet").to_pylist()
    assert all("HOLD" not in f["ticker"] for f in fills)
    assert s["main"]["n_contracts"] >= 7
    # the accumulator itself refuses a forbidden ticker in `wanted`
    with pytest.raises(r3.Refusal):
        r3.TradeScan({"X": (1, 2)}, values=True, forbidden={"X"})


def test_mve_ticker_or_field_is_refused(tmp_path):
    w = build_world(tmp_path)
    bad = mk_market("KXBTC15M", "KXMVE-BAD-15", "2026-10-08T10:15:00Z", 100.0, "yes")
    write_markets(w.markets, [bad])
    with pytest.raises(r3.Refusal):
        run_dev(w, tmp_path / "out")
    w2 = build_world(tmp_path / "second") if (tmp_path / "second").mkdir() is None else None
    badf = mk_market("KXBTC15M", "KXBTC15M-MVEF-15", "2026-10-08T10:15:00Z", 100.0, "yes",
                     raw={"ticker": "x", "mve_collection_ticker": "KXMVE-1"})
    write_markets(w2.markets, [badf])
    with pytest.raises(r3.Refusal):
        run_dev(w2, tmp_path / "out2")


def test_prices_outside_unit_interval_abort(tmp_path):
    w = build_world(tmp_path)
    write_trades(w.s1, [tr("KXBTC15M-M1-15", "2026-10-05T10:12:30Z", 95.0, "no")], "part-cents")
    with pytest.raises(r3.Refusal):
        run_dev(w, tmp_path / "out")


# ---------------------------------------------------------------------------------- holdout + ledger
def holdout_world(tmp):
    w = World(tmp)
    ms = [mk_market("KXBTC15M", "KXBTC15M-H1-15", "2026-10-12T10:15:00Z",
                    w.strike("BTC", "2026-10-12T10:15:00Z", "yes"), "yes")]
    write_markets(w.markets, ms)
    write_trades(w.s1, [tr("KXBTC15M-H1-15", "2026-10-12T10:10:00Z", 0.97, "yes"),
                        tr("KXBTC15M-H1-15", "2026-10-12T10:12:30Z", 0.95, "no")], "part-h", date="2026-10-12")
    write_trades(w.s2, [tr("KXBTC15M-H1-15", "2026-10-12T10:10:00Z", 0.97, "yes")], "part-h2", date="2026-10-12")
    return w


def test_holdout_run_once_lock_and_ledger_line(tmp_path):
    w = holdout_world(tmp_path)
    out = tmp_path / "hout"
    s = run_dev(w, out, split="holdout")
    assert (out / "HOLDOUT.lock").exists()
    assert s["main"]["n_contracts"] == 1
    ledger = tmp_path / "ledger" / "R3_RUNS.jsonl"
    lines = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert [(x["split"], x["mode"], x["status"]) for x in lines] == [("holdout", "run", "started"),
                                                                    ("holdout", "run", "finished")]
    assert lines[0]["tool_sha256"] == r3.sha256_file(r3.__file__) and lines[0]["git_sha"]
    assert lines[0]["ts"].endswith("Z")
    # same out dir again: refused (ledger and lock), different out dir: refused by the ledger
    with pytest.raises(r3.Refusal):
        run_dev(w, out, split="holdout")
    with pytest.raises(r3.Refusal):
        run_dev(w, tmp_path / "hout2", split="holdout")
    # a repeat run is refused before it opens anything (bad markets path would otherwise be a different error)
    with pytest.raises(r3.Refusal):
        r3.run("holdout", [str(w.s1)], "/nonexistent/markets", str(tmp_path / "candles"), str(out), False)
    # lock alone also refuses (ledger removed): O_EXCL
    ledger.unlink()
    with pytest.raises(r3.Refusal, match="HOLDOUT.lock"):
        run_dev(w, out, split="holdout")
    assert not (tmp_path / "hout2" / "HOLDOUT.lock").exists()


def test_holdout_lock_is_taken_before_any_trade_file_is_opened(tmp_path, monkeypatch):
    w = holdout_world(tmp_path)
    out = tmp_path / "hout"
    seen = {}
    real = r3.TradeScan.scan

    def spy(self, stores):
        seen["lock_exists"] = (out / "HOLDOUT.lock").exists()
        return real(self, stores)
    monkeypatch.setattr(r3.TradeScan, "scan", spy)
    run_dev(w, out, split="holdout")
    assert seen["lock_exists"] is True


def test_holdout_count_only_needs_explicit_flag_and_takes_no_lock(tmp_path):
    w = holdout_world(tmp_path)
    with pytest.raises(r3.Refusal):
        run_dev(w, tmp_path / "o", count_only=True, split="holdout")
    res = r3.run("holdout", [str(w.s1), str(w.s2)], str(w.markets), str(w.tmp / "candles"), str(tmp_path / "o"),
                 True, allow_holdout_count_only=True)
    assert not (tmp_path / "o" / "HOLDOUT.lock").exists()
    assert res["n_markets"] == 1
    # the real run is still available afterwards
    assert run_dev(w, tmp_path / "o", split="holdout")["main"]["n_contracts"] == 1


def test_ledger_env_override_refused_without_test_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("MAL_R3_LEDGER", str(tmp_path / "x.jsonl"))
    monkeypatch.delenv("MAL_R3_TEST")
    with pytest.raises(r3.Refusal):
        r3.ledger_path()
    monkeypatch.setenv("MAL_R3_TEST", "1")
    assert r3.ledger_path() == str(tmp_path / "x.jsonl")
    monkeypatch.delenv("MAL_R3_LEDGER")
    with pytest.raises(r3.Refusal):                                   # tests never fall back to the real ledger
        r3.ledger_path()
    monkeypatch.delenv("MAL_R3_TEST")
    assert r3.ledger_path() == r3.LEDGER_DEFAULT


# ---------------------------------------------------------------------------------------- count-only
def test_count_only_prints_structure_without_price_fill_or_return(tmp_path, capsys):
    w = build_world(tmp_path)
    out = tmp_path / "cnt"
    rc = r3.main(["run", "--split", "dev", "--trades-stores", str(w.s1), str(w.s2), "--markets-root",
                  str(w.markets), "--candles-dir", str(w.tmp / "candles"), "--out", str(out), "--count-only"])
    assert rc == 0
    text = capsys.readouterr().out
    res = json.loads(text)
    assert res["markets_per_series_per_day"]["KXBTC15M"]["2026-10-05"] == 2
    assert res["n_markets"] == 6
    assert res["signals_by_series"] == {"KXBTC15M": 4, "KXETH15M": 1}
    assert res["signals_with_a_prior_print_by_series"] == {"KXBTC15M": 3, "KXETH15M": 1}
    # window (t, close-60]: M1, M2, M3, M6, M7 have prints there; M4 none
    assert res["markets_with_any_print_in_window"] == {"KXBTC15M": 4, "KXETH15M": 1}
    low = text.lower()
    for word in ("fill", "net", "return", "p_fav", "p_yes", "bid", "markout", "mean", "pnl", "win_rate", "result"):
        assert word not in low, word
    assert (out / "r3_dev_counts.json").exists()
    assert not list(out.glob("*fills*")) and not list(out.glob("*summary*"))
    # the id-only scan never asked for a value column
    ledger = [json.loads(x) for x in (tmp_path / "ledger" / "R3_RUNS.jsonl").read_text().splitlines()]
    assert ledger[-1]["mode"] == "count_only"


def _spy_columns(monkeypatch):
    cols_asked = []

    class SpyPF(pq.ParquetFile):
        def read_row_group(self, i, columns=None, **kw):
            cols_asked.append(tuple(columns or ()))
            return super().read_row_group(i, columns=columns, **kw)

        def read(self, columns=None, **kw):
            cols_asked.append(tuple(columns or ()))
            return super().read(columns=columns, **kw)
    monkeypatch.setattr(r3.pq, "ParquetFile", SpyPF)
    return cols_asked


def test_count_only_never_reads_result_and_holdout_count_only_reads_ids_only(tmp_path, monkeypatch):
    w = build_world(tmp_path)
    cols_asked = _spy_columns(monkeypatch)
    run_dev(w, tmp_path / "out", count_only=True)
    flat = {c for cs in cols_asked for c in cs}
    assert "result" not in flat and "yes_price" in flat          # dev count-only: tick-grid check reads the price
    # holdout count-only: ids only (no value column, no result)
    (tmp_path / "h").mkdir()
    h = holdout_world(tmp_path / "h")
    cols_asked.clear()
    r3.run("holdout", [str(h.s1), str(h.s2)], str(h.markets), str(h.tmp / "candles"), str(tmp_path / "ho"), True,
           allow_holdout_count_only=True)
    flat = {c for cs in cols_asked for c in cs}
    assert not flat & {"yes_price", "no_price", "count", "taker_side", "result"}
    assert "trade_id" in flat


def test_count_only_structure_checks_time_unit_grid_share_and_null_counts(tmp_path, capsys):
    w = build_world(tmp_path)
    s3 = tmp_path / "store3"
    rows = [tr("KXBTC15M-M4-00", "2026-10-06T11:58:00Z", y, "no", tid=f"g{i}")
            for i, y in enumerate([0.50, 0.505, 0.04, 0.045, 0.955, 0.96])]
    rows.append({**tr("KXBTC15M-M4-00", "2026-10-06T11:58:10Z", 0.5, "no", tid="nullprice"), "yes_price": None})
    rows.append({**tr("KXBTC15M-M4-00", "2026-10-06T11:58:20Z", 0.5, "no", tid="x"), "trade_id": None})
    rows.append({**tr("KXBTC15M-M4-00", "2026-10-06T11:58:30Z", 0.5, "no", tid="nulltime"), "created_time": None})
    write_trades(s3, rows, "part-s3", date="2026-10-06")
    res = r3.run("dev", [str(s3)], str(w.markets), str(w.tmp / "candles"), str(tmp_path / "o3"), True)
    sc = res["structure_checks"]
    assert sc["created_time_min_iso"] == "2026-10-06T11:58:00+00:00"
    assert sc["created_time_max_iso"] == "2026-10-06T11:58:10+00:00"
    assert sc["null_counts_per_column"] == {"yes_price": 1, "trade_id": 1, "created_time": 1}
    g = sc["yes_price_off_grid_share_by_region"]
    assert g["lt_0.10"] == {"n": 2, "off_0.01_grid_share": 0.5, "off_0.001_grid_share": 0.0}
    assert g["0.10_to_0.90"] == {"n": 2, "off_0.01_grid_share": 0.5, "off_0.001_grid_share": 0.0}
    assert g["gt_0.90"] == {"n": 2, "off_0.01_grid_share": 0.5, "off_0.001_grid_share": 0.0}
    assert res["trade_rows"]["null_trade_id_excluded"] == 1 and res["trade_rows"]["null_created_time_excluded"] == 1


def test_cli_run_end_to_end(tmp_path, capsys):
    w = build_world(tmp_path)
    out = tmp_path / "cli"
    rc = r3.main(["run", "--split", "dev", "--trades-stores", str(w.s1), str(w.s2), "--markets-root",
                  str(w.markets), "--candles-dir", str(w.tmp / "candles"), "--out", str(out)])
    assert rc == 0 and "n_fills=7" in capsys.readouterr().out
    rc = r3.main(["run", "--split", "holdout", "--trades-stores", str(w.s1), "--markets-root", str(w.markets),
                  "--candles-dir", str(w.tmp / "candles"), "--out", str(tmp_path / "ho"), "--count-only"])
    assert rc == 2                                              # refused without --allow-holdout-count-only


# ------------------------------------------------------------------------------------- fit-c (dev only)
def fit_world(tmp):
    """BTC dev markets on 2026-10-02 over a random-walk candle series, the last ones inside a flat (s1m == 0)
    block.  Returns (markets_root, candles_dir, ref) where ref holds the dict candles for a reference fit."""
    rng = np.random.RandomState(7)
    t0 = us("2026-10-01T20:00:00Z") // US
    t1 = us("2026-10-02T12:00:00Z") // US
    flat_from = us("2026-10-02T08:00:00Z") // US
    px, cb, bs = 100.0, [], []
    for st in range(t0, t1, 60):
        if st < flat_from:
            px *= math.exp(rng.normal(0, 3e-4))
        o = px
        cb.append([st, px * 0.9999, px * 1.0001, o, px, 1.0])
        bs.append({"timestamp": str(st), "open": str(px * 1.00001), "high": str(px * 1.0002),
                   "low": str(px * 0.9998), "close": str(px * 0.99999), "volume": "1"})
    cdir = tmp / "fitcandles"
    cdir.mkdir()
    json.dump(cb, open(cdir / "cb_BTC-USD.json", "w"))
    json.dump(bs, open(cdir / "bs_btcusd.json", "w"))
    for prod, pair in (("ETH-USD", "ethusd"), ("SOL-USD", "solusd")):
        json.dump(cb, open(cdir / f"cb_{prod}.json", "w"))
        json.dump(bs, open(cdir / f"bs_{pair}.json", "w"))
    close = {r[0]: r[4] for r in cb}
    ms = []
    for i in range(40):
        o = us("2026-10-02T00:00:00Z") // US + 900 * i
        if i >= 30:
            o = us("2026-10-02T09:30:00Z") // US + 900 * (i - 30)           # inside the flat block
        K = close.get(o - 60, 100.0)
        ev = close.get(o + 900 - 60, K) * (1 + rng.normal(0, 1e-5))
        m = mk_market("KXBTC15M", f"KXBTC15M-F{i:02d}-15", dt.datetime.fromtimestamp(o + 900, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      K, "yes" if ev >= K else "no")
        m["expiration_value"] = repr(ev)
        ms.append(m)
    write_markets(tmp / "fitmarkets", ms)
    return tmp / "fitmarkets", cdir, {"cb": cb, "bs": bs, "markets": ms}


def ref_fit(ref):
    """proxy_model.py arithmetic restated with plain loops and the DECLARE instants (no +720)."""
    import statistics as st
    cb = {r[0]: r for r in ref["cb"]}
    bs = {int(r["timestamp"]): r for r in ref["bs"]}
    pts = {}
    for m in ref["markets"]:
        pts[m["close_time"] // US] = float(m["expiration_value"])
        pts[m["open_time"] // US] = m["floor_strike"]
    errs = []
    for T, v in pts.items():
        c, b = cb.get(T - 60), bs.get(T - 60)
        if c and b:
            btp = sum(float(b[k]) for k in ("open", "high", "low", "close")) / 4
            errs.append(1e4 * math.log((((c[1] + c[2] + c[3] + c[4]) / 4) + btp) / 2 / v))
    e = round(st.pstdev(errs), 3) / 1e4

    def sig(t):
        rs = []
        for k in range(60):
            a, b = cb.get(t - 60 - 60 * k), cb.get(t - 120 - 60 * k)
            if a and b:
                rs.append(math.log(a[4] / b[4]))
        return st.pstdev(rs) if len(rs) >= 40 else None
    rows = []
    for m in ref["markets"]:
        o = m["open_time"] // US
        for d in (300, 600, 780):
            c, s = cb.get(o + d - 60), sig(o + d)
            if not c or not s:
                continue
            rows.append((math.log(c[4] / m["floor_strike"]), s, 900 - d, 1 if m["result"] == "yes" else 0))
    grid = [round(0.6 + 0.05 * i, 2) for i in range(21)]
    br = {cc: st.mean((r3.phi(x / math.sqrt(cc * cc * s * s * (tau - 40) / 60 + e * e)) - y) ** 2
                      for x, s, tau, y in rows) for cc in grid}
    best = min(grid, key=lambda cc: br[cc])
    return best, br[best], len(rows), round(st.pstdev(errs), 3)


def test_fit_c_matches_declare_recipe_three_instants_and_skips_flat_vol(tmp_path, capsys):
    mroot, cdir, ref = fit_world(tmp_path)
    res = r3.fit_c("dev", str(mroot), str(cdir), str(tmp_path / "fitout"))
    best, br, n, e_bps = ref_fit(ref)
    a = res["assets"]["BTC"]
    assert (a["c"], a["n_rows"], a["e_bps"]) == (best, n, e_bps)
    assert a["brier"] == pytest.approx(br, rel=1e-12)
    # 3 instants per market only (no open+720): rows = 3 x markets outside the flat block, and the flat-block
    # markets (s1m == 0) are skipped, not fitted
    assert a["n_rows"] <= 3 * 30 and a["n_rows_skipped"] >= 3 * 10 and a["n_rows"] + a["n_rows_skipped"] == 3 * 40
    assert res["assets"]["ETH"]["c"] is None and res["assets"]["ETH"]["n_rows"] == 0     # no ETH markets
    assert list(a["grid_brier"]) == [str(round(0.6 + 0.05 * i, 2)) for i in range(21)]
    assert json.load(open(tmp_path / "fitout" / "fit_c_dev.json"))["assets"]["BTC"]["c"] == best
    # CLI prints only per-asset c, brier, n rows, e
    rc = r3.main(["fit-c", "--split", "dev", "--markets-root", str(mroot), "--candles-dir", str(cdir),
                  "--out", str(tmp_path / "fitout2")])
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0 and out[0].startswith(f"BTC c={best} brier=") and f"n_rows={n} e_bps={e_bps}" in out[0]
    assert len(out) == 3


def test_fit_c_refuses_any_non_dev_split_before_reading(tmp_path, capsys):
    with pytest.raises(r3.Refusal):
        r3.fit_c("holdout", "/nonexistent", "/nonexistent", str(tmp_path / "x"))
    rc = r3.main(["fit-c", "--split", "holdout", "--markets-root", "/nonexistent", "--candles-dir", "/nonexistent",
                  "--out", str(tmp_path / "x")])
    assert rc == 2 and not (tmp_path / "x").exists()


def test_ts_us_normalises_units_and_flat_vol_is_a_missing_input():
    base = [1_790_000_000, None, 1_790_000_001]
    for unit, mult in (("s", 1_000_000), ("ms", 1_000), ("us", 1), ("ns", 1 / 1_000)):
        col = pa.chunked_array([pa.array([None if v is None else int(v * {"s": 1, "ms": 1e3, "us": 1e6, "ns": 1e9}[unit])
                                          for v in base], type=pa.int64()).cast(pa.timestamp(unit))])
        got = r3._ts_us(col)
        assert got.tolist() == [1_790_000_000 * US, 0, 1_790_000_001 * US], unit
    flat = {60 * i: 100.0 for i in range(0, 200)}
    assert r3.trailing_vol(flat, 60 * 150) == 0.0
    assert r3.fair_at(flat, "BTC", 99.9, 60 * 160, 60 * 150 + 0) is None            # s1m == 0 -> skipped


def test_vol_needs_at_least_40_returns():
    closes = {60 * i: 100.0 + (i % 3) * 0.01 for i in range(0, 300)}
    t = 60 * 200
    full = {k: v for k, v in closes.items() if t - 60 * 61 <= k < t}          # 61 candles -> 60 returns
    assert r3.trailing_vol(full, t) is not None
    keep40 = {k: v for k, v in full.items() if k >= t - 60 * 41}              # 41 candles -> 40 returns: allowed
    assert r3.trailing_vol(keep40, t) is not None
    keep39 = {k: v for k, v in full.items() if k >= t - 60 * 40}              # 40 candles -> 39 returns: None
    assert r3.trailing_vol(keep39, t) is None


def test_frozen_constants_are_pinned_to_the_dev_fit():
    assert r3.FROZEN_C == {"BTC": 1.00, "ETH": 0.85, "SOL": 0.80}          # fit-c --split dev, 2026-10-19
    assert r3.FROZEN_E_BPS == {"BTC": 0.831, "ETH": 1.168, "SOL": 1.513}
    assert r3.FIT_INSTANTS_S == (300, 600, 780) and r3.FIT_GRID[0] == 0.6 and r3.FIT_GRID[-1] == 1.6
    assert len(r3.FIT_GRID) == 21
    fit = Path("/data/mal/hunt-1008/push-1016/hunt18/kalshi-crypto/fit_c_dev/fit_c_dev.json")
    if fit.exists():                                                         # derivation record (dev only)
        a = json.load(open(fit))["assets"]
        assert {k: v["c"] for k, v in a.items()} == r3.FROZEN_C
        assert {k: v["e_bps"] for k, v in a.items()} == r3.FROZEN_E_BPS
    old = Path("/data/mal/hunt-1008/push-1016/hunt18/kalshi-crypto/proxy_model_out.json")
    if old.exists():                                                         # e agrees with the earlier fit
        o = json.load(open(old))
        assert {k: o[f"{k}_model"]["e_dev_bps"] for k in ("BTC", "ETH", "SOL")} == r3.FROZEN_E_BPS


# ------------------------------------------------------------- gate cells: queue-back, per-day, window CI
def test_queue_back_fill_rule_print_at_bid_does_not_fill():
    t = us("2026-10-05T10:12:00Z")
    end = us("2026-10-05T10:14:00Z")

    def row(s, yes, taker):
        ym = int(round(yes * US))
        return (us(f"2026-10-05T{s}Z"), f"id-{s}", ym, US - ym, taker, 3.0)

    rows = sorted([row("10:12:05", 0.955, "no"),            # at the bid: main fills, queue-back does not
                   row("10:12:20", 0.9549, "no"),           # strictly below: queue-back fills
                   row("10:12:36", 0.94, "no"),             # 16 s after the queue-back fill: spacing
                   row("10:12:55", 0.94, "yes"),            # taker on our side: no
                   row("10:13:10", 0.955, "no"),            # at the bid again: no
                   row("10:13:30", 0.9, "no")])             # below, 70 s after: fills
    main = r3.simulate_fills(rows, "yes", 955_000, t, end)
    qb = r3.simulate_fills(rows, "yes", 955_000, t, end, strict_below=True)
    assert [x[1] for x in main] == ["id-10:12:05", "id-10:12:36", "id-10:13:10"]
    assert [x[1] for x in qb] == ["id-10:12:20", "id-10:13:30"]      # re-simulated, not a subset of main
    # NO side: price is no_price
    rows_no = sorted([(us("2026-10-05T10:12:10Z"), "a", 40_000, 960_000, "yes", 1.0),
                      (us("2026-10-05T10:12:50Z"), "b", 41_000, 959_000, "yes", 1.0)])
    assert [x[1] for x in r3.simulate_fills(rows_no, "no", 960_000, t, end, strict_below=True)] == ["b"]


def test_end_to_end_queue_back_cell_and_gate_fields_on_every_cell(tmp_path):
    w = build_world(tmp_path)
    out = tmp_path / "out"
    s = run_dev(w, out)
    # M1 (bid 0.955): at-bid prints 10:12:05, 10:13:10 do not fill; 10:12:20 (0.95) and 10:14:00 (0.95) do
    qb = pq.read_table(out / "r3_dev_fills_queue_back.parquet").to_pylist()
    by = {}
    for f in qb:
        by.setdefault(f["ticker"], []).append(f)
    assert [f["fill_time_us"] for f in by["KXBTC15M-M1-15"]] == [us("2026-10-05T10:12:20Z"), us("2026-10-05T10:14:00Z")]
    assert [f["bid_micro"] for f in by["KXBTC15M-M1-15"]] == [955_000, 955_000]       # filled at our bid
    assert "KXBTC15M-M2-30" not in by and "KXETH15M-M7-15" not in by                 # prints exactly at the bid
    assert len(by["KXBTC15M-M3-15"]) == 1 and by["KXBTC15M-M3-15"][0]["net_cent_micro"] == -960_000
    assert not any(f["dropped_15"] for f in qb)                                       # no random drop
    q = s["queue_back"]
    assert q["n_contracts"] == 3 and q["n_events"] == 2 and q["n_days"] == 2
    assert q["total_net_cent"] == pytest.approx((2 * 45_000 - 960_000) / 1e4)
    assert "strictly below" in q["rule"]
    # every gate cell carries the same fields
    for name in ("main", "maker_15pct_unfilled", "queue_back"):
        c = s[name]
        for key in ("n_contracts", "n_events", "n_days", "per_day", "days_positive", "ex_best_day",
                    "ex_top3_events", "added3_worst_losses", "ci90_event_cent", "ci90_window_cent",
                    "ci90_lo_decider"):
            assert key in c, (name, key)
        assert c["n_days"] == len(c["per_day"])
        assert c["days_positive"] == sum(1 for d in c["per_day"].values() if d["total_net_cent"] > 0)
        assert sum(d["total_net_cent"] for d in c["per_day"].values()) == pytest.approx(c["total_net_cent"])
    assert s["main"]["days_positive"] == 2 and s["queue_back"]["days_positive"] == 1
    # the 15% cell: per-day totals of the kept fills only
    assert sum(d["n_contracts"] for d in s["maker_15pct_unfilled"]["per_day"].values()) == \
        s["maker_15pct_unfilled"]["n_contracts"]
    assert s["ci90_lo_decider"] == {k: s[k]["ci90_lo_decider"] for k in ("main", "maker_15pct_unfilled", "queue_back")}
    # headline label and skip counts
    h = s["headline"]
    assert h["population"] == "R3 on signal markets with a print in [close-300 s, close-180 s)"
    assert h["signals_without_prior_favoured_print_by_series"] == {"KXBTC15M": 1}
    assert h["n_filled_events"] == {"main": 4, "maker_15pct_unfilled": s["maker_15pct_unfilled"]["n_events"],
                                    "queue_back": 2}


def test_window_clustered_ci_and_decider_are_the_lower_of_the_two():
    net = np.array([45_000, 45_000, 45_000, 40_000, -955_000, 45_000], dtype=float)
    price = np.array([955_000, 955_000, 955_000, 960_000, 955_000, 955_000], dtype=float)
    events = ["E1", "E1", "E1", "E2", "E3", "E4"]
    days = ["2026-10-05"] * 4 + ["2026-10-06"] * 2
    win_t = [1, 1, 1, 1, 2, 3]                 # E2 closes in the same 15-minute window as E1 (BTC + ETH)
    c = r3.cell_stats(net, net, price, events, days, win=net > 0, windows=win_t)
    wn, wc = np.array([175_000.0, -955_000.0, 45_000.0]), np.array([4.0, 1.0, 1.0])
    rs = np.random.RandomState(1)
    means = []
    for _ in range(1000):
        i = rs.randint(0, 3, size=3)
        means.append(wn[i].sum() / wc[i].sum())
    assert c["ci90_window_cent"] == pytest.approx([np.percentile(means, 5) / 1e4, np.percentile(means, 95) / 1e4])
    assert c["n_windows"] == 3
    assert c["ci90_lo_decider"] == min(c["ci90_event_cent"][0], c["ci90_window_cent"][0])
    assert c["per_day"]["2026-10-05"] == {"n_contracts": 4, "n_events": 2, "total_net_cent": pytest.approx(17.5),
                                          "mean_net_cent": pytest.approx(4.375)}
    assert c["per_day"]["2026-10-06"]["total_net_cent"] == pytest.approx(-91.0)
    assert c["days_positive"] == 1 and c["n_days"] == 2
    assert c["ex_best_day"]["total_net_cent"] == pytest.approx(-91.0)
    # fewer clusters than events: the window CI is not the event CI
    assert c["ci90_window_cent"] != c["ci90_event_cent"]
    # without windows the decider falls back to the event CI only
    c2 = r3.cell_stats(net, net, price, events, days, win=net > 0)
    assert c2["ci90_lo_decider"] == c2["ci90_event_cent"][0] and c2["ci90_window_cent"] == [None, None]


# --------------------------------------------------------------- protocol: preflight, order, null handling
def _ledger_lines(tmp_path):
    f = tmp_path / "ledger" / "R3_RUNS.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def test_holdout_preflight_refusals_leave_no_lock_and_no_ledger_line(tmp_path):
    w = holdout_world(tmp_path)
    cd = str(w.tmp / "candles")
    empty = tmp_path / "emptystore"
    (empty / "trades" / "series=KXBTC15M").mkdir(parents=True)
    other = tmp_path / "otherseries"
    write_trades(other, [tr("KXFOO-1", "2026-10-12T10:12:30Z", 0.5, "no", series="KXFOO")], "p", series="KXFOO")
    cases = [
        ([str(tmp_path / "nonexistent")], str(w.markets), cd),             # mistyped store
        ([str(w.s1), str(empty)], str(w.markets), cd),                     # a store without parquet
        ([str(other)], str(w.markets), cd),                                # parquet outside the universe only
        ([str(w.s1)], str(w.markets), str(tmp_path / "nocandles")),        # candles dir missing
        ([str(w.s1)], str(w.markets), str(empty)),                         # candles files missing
        ([str(w.s1)], str(tmp_path / "nomarkets"), cd),                    # markets root missing
    ]
    for stores, mroot, cdir in cases:
        out = tmp_path / "hout"
        with pytest.raises(r3.Refusal):
            r3.run("holdout", stores, mroot, cdir, str(out), False)
        assert not (out / "HOLDOUT.lock").exists()
        assert _ledger_lines(tmp_path) == []
    # a split with 0 markets (the world only has holdout markets; ask for dev)
    with pytest.raises(r3.Refusal, match="0 markets"):
        r3.run("dev", [str(w.s1)], str(w.markets), cd, str(tmp_path / "d0"), False)
    assert _ledger_lines(tmp_path) == []
    # candles that do not cover the split: no market has model inputs
    short = tmp_path / "shortcandles"
    write_candles(short, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")
    with pytest.raises(r3.Refusal, match="model inputs"):
        r3.run("holdout", [str(w.s1)], str(w.markets), str(short), str(tmp_path / "hout"), False)
    assert _ledger_lines(tmp_path) == [] and not (tmp_path / "hout" / "HOLDOUT.lock").exists()
    # and the real run is still available afterwards
    assert r3.run("holdout", [str(w.s1), str(w.s2)], str(w.markets), cd, str(tmp_path / "hout"), False)["main"][
        "n_contracts"] == 1


def test_holdout_lock_and_started_line_precede_the_result_column(tmp_path, monkeypatch):
    w = holdout_world(tmp_path)
    out = tmp_path / "hout"
    seen = []
    real = r3.load_markets

    def spy(markets_root, split, with_outcomes, with_expiration=False):
        seen.append((with_outcomes, (out / "HOLDOUT.lock").exists(),
                     [x["status"] for x in _ledger_lines(tmp_path)]))
        return real(markets_root, split, with_outcomes, with_expiration)
    monkeypatch.setattr(r3, "load_markets", spy)
    run_dev(w, out, split="holdout")
    pre = [x for x in seen if not x[0]]          # preflight: no result column, no lock yet
    post = [x for x in seen if x[0]]             # the result-reading load: lock and 'started' already there
    assert pre and all(not lock and led == [] for _o, lock, led in pre)
    assert post == [(True, True, ["started"])]


def test_crash_after_the_lock_is_recorded_as_aborted_and_spends_the_holdout(tmp_path, monkeypatch):
    w = holdout_world(tmp_path)
    out = tmp_path / "hout"

    def boom(*a, **k):
        raise RuntimeError("boom")
    with monkeypatch.context() as mp:                  # compute_signals also runs in the preflight: pre-lock crash
        mp.setattr(r3, "compute_signals", boom)
        with pytest.raises(RuntimeError):
            run_dev(w, out, split="holdout")
    assert not (out / "HOLDOUT.lock").exists() and _ledger_lines(tmp_path) == []
    with monkeypatch.context() as mp:                  # a crash while scanning trades is after the lock
        mp.setattr(r3.TradeScan, "scan", lambda self, stores: boom())
        with pytest.raises(RuntimeError):
            run_dev(w, out, split="holdout")
    assert [x["status"] for x in _ledger_lines(tmp_path)] == ["started", "aborted"]
    with pytest.raises(r3.Refusal):                    # the holdout stays spent
        run_dev(w, tmp_path / "hout2", split="holdout")


def test_unparseable_ledger_line_blocks_the_holdout_fail_closed(tmp_path):
    w = holdout_world(tmp_path)
    (tmp_path / "ledger" / "R3_RUNS.jsonl").write_text('{"ts": "2026-10-20T00:00:00Z", "split": "hold')
    with pytest.raises(r3.Refusal, match="unparseable"):
        run_dev(w, tmp_path / "hout", split="holdout")
    assert not (tmp_path / "hout" / "HOLDOUT.lock").exists()


def test_second_holdout_count_only_is_refused_and_ledger_records_the_tick_rule(tmp_path):
    w = holdout_world(tmp_path)
    args = ("holdout", [str(w.s1), str(w.s2)], str(w.markets), str(w.tmp / "candles"), str(tmp_path / "o"), True)
    r3.run(*args, allow_holdout_count_only=True)
    with pytest.raises(r3.Refusal, match="count-only already taken"):
        r3.run(*args, allow_holdout_count_only=True)
    first = _ledger_lines(tmp_path)[0]
    assert first["tick_rounding"] == "ceil_to_tick" and first["frozen_c"] == r3.FROZEN_C
    assert [x["status"] for x in _ledger_lines(tmp_path)] == ["started", "finished"]


def test_null_price_and_null_trade_id_are_counted_exclusions_not_refusals(tmp_path):
    w = build_world(tmp_path)
    extra = [
        {**tr("KXBTC15M-M1-15", "2026-10-05T10:12:45Z", 0.90, "no", tid="np"), "yes_price": None},
        {**tr("KXBTC15M-M1-15", "2026-10-05T10:12:46Z", 0.90, "no", tid="np2"), "no_price": None},
        {**tr("KXBTC15M-M1-15", "2026-10-05T10:13:20Z", 0.90, "no"), "trade_id": None},
        {**tr("KXBTC15M-M1-15", "2026-10-05T10:13:21Z", 0.90, "no"), "trade_id": None},
        {**tr("KXBTC15M-M1-15", "2026-10-05T10:13:22Z", 0.90, "no"), "trade_id": None},
    ]
    write_trades(w.s1, extra, "part-nulls")
    s = run_dev(w, tmp_path / "out")
    assert s["trade_rows"]["null_price_excluded"] == 2
    assert s["trade_rows"]["null_trade_id_excluded"] == 3          # three distinct prints, none collapsed
    assert s["trade_rows"]["duplicates"] == 12                     # unchanged: null ids are not duplicates
    assert s["main"]["n_contracts"] == 7                           # the excluded prints (price 0.90 <= bid) never fill


def test_price_outside_unit_interval_still_refuses(tmp_path):
    w = build_world(tmp_path)
    write_trades(w.s1, [{**tr("KXBTC15M-M1-15", "2026-10-05T10:12:30Z", 0.5, "no"), "no_price": 95.0}], "part-c")
    with pytest.raises(r3.Refusal, match="outside"):
        run_dev(w, tmp_path / "out")
