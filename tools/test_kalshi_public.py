"""Tests for tools/kalshi_public.py with a fake HTTP layer (no network)."""
import hashlib
import json
import os
import signal
import stat
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pyarrow.parquet as pq
import pytest

import kalshi_public as kp

NOW = 1_790_000_000
CUTOFF_ISO = "2026-06-01T00:00:00Z"
CUTOFF_TS = kp.to_unix(CUTOFF_ISO)


class Fake:
    """Serves canned data; records every URL. Pages by 'limit' + opaque cursor."""

    def __init__(self):
        self.calls = []
        self.status_script = []  # statuses to emit before behaving normally
        self.series = [
            {"ticker": "KXHIGHNY", "title": "NYC high", "category": "Climate and Weather",
             "categories": ["Climate and Weather"], "tags": ["temp"], "frequency": "daily",
             "fee_type": "quadratic", "fee_multiplier": 1.0, "settlement_sources": [],
             "contract_url": "u", "contract_terms_url": "t"},
            {"ticker": "KXNFLGAME", "title": "NFL", "category": "Sports",
             "categories": ["Sports"], "tags": None, "frequency": "weekly",
             "fee_type": "quadratic", "fee_multiplier": 1.0, "settlement_sources": None,
             "contract_url": "u", "contract_terms_url": "t"},
            {"ticker": "KXCPI", "title": "CPI", "category": "Economics",
             "categories": ["Economics", "Sports"], "tags": [], "frequency": "monthly",
             "fee_type": "quadratic", "fee_multiplier": 1.0, "settlement_sources": [],
             "contract_url": "u", "contract_terms_url": "t"},
            {"ticker": "KXFIN", "title": "Fin", "category": "Financials",
             "categories": ["Financials"], "tags": [], "frequency": "daily",
             "fee_type": "quadratic", "fee_multiplier": 1.0, "settlement_sources": [],
             "contract_url": "u", "contract_terms_url": "t"},
        ]
        self.live_markets = {"KXHIGHNY": [self.mk("KXHIGHNY-26OCT01-T80", "2026-10-01T12:00:00Z"),
                                          self.mk("KXHIGHNY-26OCT02-T80", "2026-10-02T12:00:00Z"),
                                          self.mk("KXHIGHNY-26OCT03-T80", "2026-10-03T12:00:00Z")]}
        self.hist_markets = {"KXHIGHNY": [self.mk("KXHIGHNY-26MAY01-T80", "2026-05-01T12:00:00Z")]}
        self.trades = {"KXHIGHNY-26OCT01-T80": [self.tr(i) for i in range(5)]}
        self.hist_trades = {"KXHIGHNY-26OCT01-T80": [self.tr(100 + i, ts="2026-05-02T00:00:0%dZ" % i)
                                                    for i in range(2)]}

    @staticmethod
    def mk(t, settled):
        return {"ticker": t, "event_ticker": t.rsplit("-", 1)[0], "market_type": "binary",
                "status": "finalized", "result": "yes", "title": "T", "yes_sub_title": "y",
                "no_sub_title": "n", "rules_primary": "rules A", "rules_secondary": "rules B",
                "created_time": "2026-04-01T00:00:00Z", "open_time": "2026-04-02T00:00:00Z",
                "close_time": "2026-10-01T00:00:00.123456789Z", "settlement_ts": settled,
                "settlement_value_dollars": "1.0000", "last_price_dollars": "0.9900",
                "volume_fp": "123.00", "open_interest_fp": "4.00", "expiration_value": "81"}

    @staticmethod
    def tr(i, ts=None):
        return {"trade_id": f"tr{i}", "ticker": "KXHIGHNY-26OCT01-T80", "count_fp": "3.00",
                "yes_price_dollars": "0.4000", "no_price_dollars": "0.6000",
                "taker_outcome_side": "yes", "taker_book_side": "bid",
                "created_time": ts or f"2026-10-01T00:00:0{i}Z", "is_block_trade": False}

    def page(self, items, key, q):
        lim = int(q.get("limit", ["100"])[0])
        off = int(q["cursor"][0]) if "cursor" in q else 0
        chunk = items[off:off + lim]
        nxt = str(off + lim) if off + lim < len(items) else ""
        return {key: chunk, "cursor": nxt}

    def __call__(self, url):
        self.calls.append(url)
        if self.status_script:
            s = self.status_script.pop(0)
            if s != 200:
                return s, b"{}"
        u = urllib.parse.urlparse(url)
        path = u.path.replace("/trade-api/v2", "")
        q = urllib.parse.parse_qs(u.query)
        j = lambda o: (200, json.dumps(o).encode())
        if path == "/series":
            return j({"series": self.series})
        if path == "/historical/cutoff":
            return j({"market_settled_ts": CUTOFF_ISO, "trades_created_ts": CUTOFF_ISO,
                      "orders_updated_ts": CUTOFF_ISO})
        if path == "/markets":
            s = q["series_ticker"][0]
            items = self.live_markets.get(s, [])
            lo = int(q.get("min_settled_ts", ["0"])[0])
            hi = int(q.get("max_settled_ts", [str(10 ** 12)])[0])
            items = [m for m in items if lo <= kp.to_unix(m["settlement_ts"]) <= hi]
            assert q["status"] == ["settled"]
            return j(self.page(items, "markets", q))
        if "mve_filter" in q and any(k in q for k in ("series_ticker", "event_ticker", "tickers")):
            return 400, b'{"message":"filters are mutually exclusive"}'
        if path == "/historical/markets":
            return j(self.page(self.hist_markets.get(q["series_ticker"][0], []), "markets", q))
        if path in ("/markets/trades", "/historical/trades"):
            src = self.trades if path == "/markets/trades" else self.hist_trades
            return j(self.page(src.get(q["ticker"][0], []), "trades", q))
        if path.endswith("/orderbook"):
            return j({"orderbook_fp": {"yes_dollars": [["0.4000", "10.00"], ["0.4100", "5.00"]],
                                       "no_dollars": []}})
        if path.endswith("/candlesticks"):
            if path.startswith("/series"):
                return 404, b"{}"
            return j({"ticker": "x", "candlesticks": [{
                "end_period_ts": NOW, "yes_bid": {"open_dollars": "0.10", "high_dollars": "0.2",
                                                  "low_dollars": "0.1", "close_dollars": "0.15"},
                "yes_ask": {"open_dollars": "0.12", "high_dollars": "0.3",
                            "low_dollars": "0.1", "close_dollars": "0.16"},
                "price": {"open_dollars": None, "mean_dollars": "0.14"},
                "volume_fp": "7.00", "open_interest_fp": "9.00"}]})
        return 404, b"{}"


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def mkclient(fake, clock=None, **kw):
    clock = clock or Clock()
    kw.setdefault("rate", 1000)
    kw.setdefault("jitter", 0)
    return kp.Client(http=fake, sleep=clock.sleep, clock=clock.now, **kw), clock


def run(tmp_path, argv, fake, **kw):
    client, _ = mkclient(fake, **kw)
    return kp.main(["--out", str(tmp_path), *argv], client=client), client


def read(tmp_path, kind):
    return pq.read_table(tmp_path / kind).to_pylist()


# ------------------------------------------------------------ allowlist

@pytest.mark.parametrize("path", [
    "/portfolio/orders", "/portfolio/balance", "/portfolio/positions", "/portfolio/fills",
    "/portfolio/orders/abc", "/historical/fills", "/historical/orders", "/historical/positions",
    "/api-keys", "/account/endpoint_costs", "/markets/X/orders", "/orders", "/markets/../portfolio/orders",
    "/events", "/series/S/markets/T", "https://evil.example/series",
])
def test_allowlist_refuses_trading_and_account_paths(path):
    fake = Fake()
    client, _ = mkclient(fake)
    with pytest.raises(kp.ForbiddenPath):
        client.get(path)
    assert fake.calls == []  # refused before any HTTP


@pytest.mark.parametrize("path", [
    "/series", "/markets", "/markets/trades", "/markets/KX-1/orderbook", "/historical/cutoff",
    "/historical/markets", "/historical/trades", "/series/S/markets/KX-1/candlesticks",
    "/historical/markets/KX-1/candlesticks"])
def test_allowlist_accepts_public_paths(path):
    assert kp.check_path(path) == path


def test_client_sends_no_auth_and_only_get():
    seen = []

    def http(url):  # the only thing the Client passes is the url
        seen.append(url)
        return 200, b"{}"
    c = kp.Client(http=http, rate=1000)
    c.get("/series")
    assert seen == [kp.BASE_URL + "/series"]
    import inspect
    src = inspect.getsource(kp.urllib_http)
    assert 'method="GET"' in src and "Authorization" not in src and "KALSHI-ACCESS" not in src


# ------------------------------------------------------------ limiter / backoff / cap

def test_rate_limiter_spaces_requests():
    clk = Clock()
    rl = kp.RateLimiter(5, clock=clk.now, sleep=clk.sleep)
    t = []
    for _ in range(4):
        rl.wait()
        t.append(clk.now())
    assert t == pytest.approx([1000.0, 1000.2, 1000.4, 1000.6])
    clk.t += 10  # idle time does not bank a burst
    rl.wait(); a = clk.now()
    rl.wait(); b = clk.now()
    assert b - a == pytest.approx(0.2)


def test_backoff_on_429_and_5xx_then_success():
    fake = Fake()
    fake.status_script = [429, 503, 500, 0]
    client, clk = mkclient(fake, backoff_base=1.0)
    assert "series" in client.get("/series")
    assert client.backoffs == [1.0, 2.0, 4.0, 8.0]
    assert [s for s in clk.sleeps if s >= 1.0] == [1.0, 2.0, 4.0, 8.0]
    assert client.requests_made == 5


def test_backoff_gives_up_and_4xx_not_retried():
    fake = Fake()
    fake.status_script = [429] * 10
    client, _ = mkclient(fake, max_retries=2)
    with pytest.raises(kp.HttpError) as e:
        client.get("/series")
    assert e.value.status == 429 and client.requests_made == 3
    client2, _ = mkclient(Fake())
    with pytest.raises(kp.HttpError) as e:
        client2.get("/markets/ZZ/orderbook")  # fake returns 200 for orderbook; use candles 404
        client2.get("/series/S/markets/T/candlesticks")
    assert e.value.status == 404 and client2.requests_made == 2


def test_backoff_capped():
    fake = Fake()
    fake.status_script = [429] * 6
    client, _ = mkclient(fake, backoff_base=10, backoff_cap=25, max_retries=8)
    client.get("/series")
    assert max(client.backoffs) == 25


def test_max_requests_cap_counts_retries():
    fake = Fake()
    fake.status_script = [429, 429]
    client, _ = mkclient(fake, max_requests=2)
    with pytest.raises(kp.MaxRequestsReached):
        client.get("/series")
    assert len(fake.calls) == 2


# ------------------------------------------------------------ pagination

def test_pagination_follows_cursor_until_empty():
    fake = Fake()
    fake.live_markets["KXHIGHNY"] = [Fake.mk(f"KXHIGHNY-{i}", "2026-10-01T00:00:00Z") for i in range(7)]
    client, _ = mkclient(fake)
    got = list(kp.paginate(client, "/markets", {"series_ticker": "KXHIGHNY", "status": "settled",
                                                "limit": 3}, "markets"))
    assert len(got) == 7 and len(fake.calls) == 3
    assert "cursor=3" in fake.calls[1] and "cursor=6" in fake.calls[2]


def test_pagination_stops_on_repeated_cursor():
    pages = iter([{"markets": [1], "cursor": "a"}, {"markets": [2], "cursor": "a"}])
    client = kp.Client(http=lambda u: (200, json.dumps(next(pages)).encode()), rate=1000)
    assert list(kp.paginate(client, "/markets", {}, "markets")) == [1, 2]


# ------------------------------------------------------------ commands, schema, manifest

def test_markets_schema_partitions_routing_and_manifest(tmp_path):
    fake = Fake()
    rc, _ = run(tmp_path, ["markets", "--series", "KXHIGHNY", "--raw-json"], fake)
    assert rc == 0
    rows = read(tmp_path, "markets")
    assert {r["ticker"] for r in rows} == {"KXHIGHNY-26OCT01-T80", "KXHIGHNY-26OCT02-T80",
                                           "KXHIGHNY-26OCT03-T80", "KXHIGHNY-26MAY01-T80"}
    by = {r["ticker"]: r for r in rows}
    assert by["KXHIGHNY-26MAY01-T80"]["source"] == "historical"
    assert by["KXHIGHNY-26OCT01-T80"]["source"] == "live"
    r = by["KXHIGHNY-26OCT01-T80"]
    assert r["rules_primary"] == "rules A" and r["result"] == "yes"
    assert r["settlement_ts"].isoformat().startswith("2026-10-01T12:00:00")
    assert r["close_time"].microsecond == 123456  # >6 fractional digits tolerated
    assert r["series_ticker"] == "KXHIGHNY" and r["settlement_value_dollars"] == 1.0
    assert json.loads(r["raw_json"])["ticker"] == r["ticker"]
    schema = pq.read_table(next((tmp_path / "markets").rglob("*.parquet"))).schema
    assert schema.equals(kp.SCHEMAS["markets"])
    parts = sorted(p.relative_to(tmp_path / "markets").parts[:2]
                   for p in (tmp_path / "markets").rglob("*.parquet"))
    assert ("series=KXHIGHNY", "date=2026-10-02") in parts and ("series=KXHIGHNY", "date=2026-05-01") in parts
    # manifest
    lines = [json.loads(l) for l in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    files = [l for l in lines if l["type"] == "file"]
    assert sum(f["rows"] for f in files) == 4
    for f in files:
        p = tmp_path / f["path"]
        assert hashlib.sha256(p.read_bytes()).hexdigest() == f["sha256"]
        assert pq.read_metadata(p).num_rows == f["rows"]
        assert f["query"]["series"] == "KXHIGHNY" and f["command"] == "markets"
    assert lines[-1]["type"] == "run" and lines[-1]["complete"] is True


def test_markets_time_window_filters_both_tiers(tmp_path):
    fake = Fake()
    run(tmp_path, ["markets", "--series", "KXHIGHNY", "--since", "2026-10-02",
                   "--until", "2026-10-03"], fake)
    assert [r["ticker"] for r in read(tmp_path, "markets")] == ["KXHIGHNY-26OCT02-T80"]
    # since after cutoff: the historical tier is never queried
    assert not any("/historical/markets" in c for c in fake.calls)


def test_sports_excluded_by_default(tmp_path):
    fake = Fake()
    rc, _ = run(tmp_path, ["markets"], fake)
    queried = {urllib.parse.parse_qs(urllib.parse.urlparse(c).query).get("series_ticker", [None])[0]
               for c in fake.calls if "/markets" in c}
    # Sports series and the series that lists Sports as a discovery category are never queried
    assert "KXNFLGAME" not in queried and "KXCPI" not in queried
    assert {"KXHIGHNY", "KXFIN"} <= queried


def test_explicit_series_in_excluded_category_is_skipped(tmp_path):
    fake = Fake()
    run(tmp_path, ["markets", "--series", "KXNFLGAME,NOPE"], fake)
    assert not any("series_ticker" in c for c in fake.calls)


def test_categories_flag_selects_only_those(tmp_path):
    fake = Fake()
    run(tmp_path, ["--categories", "financials", "markets"], fake)
    queried = {urllib.parse.parse_qs(urllib.parse.urlparse(c).query).get("series_ticker", [None])[0]
               for c in fake.calls if "series_ticker" in c}
    assert queried == {"KXFIN"}
    run(tmp_path / "s", ["--categories", "Sports", "markets"], Fake())  # explicit opt-in works


def test_series_command_schema_and_filter(tmp_path):
    rc, _ = run(tmp_path, ["series"], Fake())
    rows = read(tmp_path, "series")
    assert {r["ticker"] for r in rows} == {"KXHIGHNY", "KXFIN"}
    assert rows[0]["categories"] is not None
    schema = pq.read_table(next((tmp_path / "series").rglob("*.parquet"))).schema
    assert schema.equals(kp.SCHEMAS["series"])
    run(tmp_path / "all", ["series", "--all-categories"], Fake())
    assert len(read(tmp_path / "all", "series")) == 4


def test_trades_routes_live_and_historical_and_schema(tmp_path):
    fake = Fake()
    rc, _ = run(tmp_path, ["trades", "--market", "KXHIGHNY-26OCT01-T80"], fake)
    assert rc == 0
    rows = read(tmp_path, "trades")
    assert len(rows) == 7 and {r["source"] for r in rows} == {"live", "historical"}
    r = [x for x in rows if x["trade_id"] == "tr1"][0]
    assert (r["yes_price"], r["no_price"], r["count"], r["taker_side"]) == (0.4, 0.6, 3.0, "yes")
    assert r["series_ticker"] == "KXHIGHNY"
    schema = pq.read_table(next((tmp_path / "trades").rglob("*.parquet"))).schema
    assert schema.equals(kp.SCHEMAS["trades"])
    hist = [c for c in fake.calls if "/historical/trades" in c][0]
    assert f"max_ts={CUTOFF_TS}" in hist
    live = [c for c in fake.calls if "/markets/trades" in c][0]
    assert f"min_ts={CUTOFF_TS}" in live


def test_trades_by_series_uses_settled_markets_and_skips_sports_market(tmp_path):
    fake = Fake()
    run(tmp_path, ["trades", "--series", "KXHIGHNY", "--since", "2026-10-01"], fake)
    asked = {urllib.parse.parse_qs(urllib.parse.urlparse(c).query)["ticker"][0]
             for c in fake.calls if "trades" in c}
    assert asked == {"KXHIGHNY-26OCT01-T80", "KXHIGHNY-26OCT02-T80", "KXHIGHNY-26OCT03-T80"}
    fake2 = Fake()
    run(tmp_path / "x", ["trades", "--market", "KXNFLGAME-26OCT18-A"], fake2)
    assert not any("trades" in c for c in fake2.calls)


def test_resume_after_max_requests_skips_done_units(tmp_path):
    fake = Fake()
    # series(1) + cutoff(1) + KXFIN markets(live 1 + hist 1) = 4, then KXHIGHNY needs more
    rc, c1 = run(tmp_path, ["markets"], fake, max_requests=5)
    assert rc == 3 and c1.requests_made == 5
    ck = json.loads(next((tmp_path / "_checkpoints").glob("markets-*.json")).read_text())
    assert ck["done"] == ["KXFIN"]
    last = json.loads((tmp_path / "manifest.jsonl").read_text().splitlines()[-1])
    assert last["type"] == "run" and last["complete"] is False
    fake2 = Fake()
    rc, _ = run(tmp_path, ["markets"], fake2)
    assert rc == 0
    asked = {urllib.parse.parse_qs(urllib.parse.urlparse(c).query).get("series_ticker", [None])[0]
             for c in fake2.calls if "series_ticker" in c}
    assert asked == {"KXHIGHNY"}  # KXFIN not refetched
    assert len(read(tmp_path, "markets")) == 4
    # a different query does not share the checkpoint
    fake3 = Fake()
    run(tmp_path, ["markets", "--since", "2026-10-02"], fake3)
    assert any("series_ticker=KXFIN" in c for c in fake3.calls)


def test_rerun_overwrites_same_part_files(tmp_path):
    run(tmp_path, ["trades", "--market", "KXHIGHNY-26OCT01-T80"], Fake())
    n = len(list((tmp_path / "trades").rglob("*.parquet")))
    (tmp_path / "_checkpoints").rename(tmp_path / "_ck_old")
    run(tmp_path, ["trades", "--market", "KXHIGHNY-26OCT01-T80"], Fake())
    assert len(list((tmp_path / "trades").rglob("*.parquet"))) == n


def test_candles_falls_back_to_historical_and_schema(tmp_path):
    fake = Fake()
    rc, _ = run(tmp_path, ["candles", "--market", "KXHIGHNY-26MAY01-T80",
                           "--since", "2026-05-01", "--until", "2026-05-02", "--period", "60"], fake)
    assert rc == 0
    rows = read(tmp_path, "candles")
    assert len(rows) == 1 and rows[0]["source"] == "historical"
    assert rows[0]["yes_bid_close"] == 0.15 and rows[0]["price_open"] is None
    assert rows[0]["price_mean"] == 0.14 and rows[0]["volume"] == 7.0
    schema = pq.read_table(next((tmp_path / "candles").rglob("*.parquet"))).schema
    assert schema.equals(kp.SCHEMAS["candles"])


def test_orderbook_snap_records_depth_only_with_timestamps(tmp_path):
    fake = Fake()
    clk = Clock()
    client, _ = mkclient(fake, clock=clk)
    args = kp.build_parser().parse_args(["--out", str(tmp_path), "orderbook-snap",
                                         "--markets", "KXHIGHNY-26OCT01-T80,KXNFLGAME-1",
                                         "--every-s", "60", "--duration-s", "180"])
    store = kp.Store(tmp_path, "orderbook-snap", {})
    kp.cmd_orderbook_snap(client, store, args, lambda s: None, clock=clk.now, sleep=clk.sleep,
                          utcnow=lambda: kp.parse_dt(NOW + clk.t))
    rows = read(tmp_path, "orderbook")
    # 4 rounds (t=0,60,120,180), sports market excluded, 2 yes levels each
    assert len(rows) == 8 and {r["ticker"] for r in rows} == {"KXHIGHNY-26OCT01-T80"}
    assert {r["side"] for r in rows} == {"yes"}
    assert len({r["ts"] for r in rows}) == 4
    assert set(rows[0]) - {"series", "date"} == {f.name for f in kp.SCHEMAS["orderbook"]}  # hive cols added by dir read
    snaps = read(tmp_path, "orderbook_snaps")
    assert len(snaps) == 4 and snaps[0]["n_no_levels"] == 0
    assert sum(1 for c in fake.calls if "/orderbook" in c) == 4


def test_cli_parser_has_no_trading_subcommands():
    sub = [a for a in kp.build_parser()._actions if a.dest == "cmd"][0]
    assert set(sub.choices) == {"series", "markets", "trades", "candles", "orderbook-snap",
                           "quarantine-unsealed"}


# ------------------------------------------------------------ review round 1

def manifest(tmp_path):
    return [json.loads(l) for l in (tmp_path / "manifest.jsonl").read_text().splitlines()]


def test_raw_json_off_by_default(tmp_path):
    run(tmp_path, ["markets", "--series", "KXHIGHNY"], Fake())
    assert all(r["raw_json"] is None for r in read(tmp_path, "markets"))


def test_jitter_added_after_cap():
    fake = Fake()
    fake.status_script = [429, 429, 429]
    client, _ = mkclient(fake, jitter=0.5, rng=lambda: 1.0, backoff_base=10, backoff_cap=15)
    client.get("/series")
    # min(15,10)=10 -> 15 ; then capped 15 -> 22.5 twice
    assert client.backoffs == [15.0, 22.5, 22.5]


def test_markets_flush_every_n_pages(tmp_path, monkeypatch):
    fake = Fake()
    fake.live_markets["KXHIGHNY"] = [Fake.mk(f"KXHIGHNY-X{i}", "2026-10-01T00:00:00Z") for i in range(7)]
    monkeypatch.setattr(kp, "PAGE_LIMIT", 2)  # 4 live pages
    rc, _ = run(tmp_path, ["--flush-pages", "2", "markets", "--series", "KXHIGHNY"], fake)
    assert rc == 0
    files = [f for f in manifest(tmp_path) if f["type"] == "file"]
    live = [f for f in files if "date=2026-10-01" in f["path"]]
    assert len(live) >= 2
    assert sum(f["rows"] for f in files) == 8  # 7 live + 1 historical
    assert len({f["path"] for f in files}) == len(files)


def test_trades_flush_batches_and_dedupe(tmp_path, monkeypatch):
    monkeypatch.setattr(kp, "PAGE_LIMIT", 1)
    run(tmp_path, ["--flush-pages", "2", "trades", "--market", "KXHIGHNY-26OCT01-T80"], Fake())
    rows = read(tmp_path, "trades")
    assert len(rows) == 7 and len({r["trade_id"] for r in rows}) == 7
    assert len([x for x in manifest(tmp_path) if x["type"] == "file"]) >= 3


class ChunkFake(Fake):
    """Candles: live data only for chunks starting at/after live_from; historical 404."""

    def __init__(self, live_from=None):
        super().__init__()
        self.live_from = live_from

    def __call__(self, url):
        u = urllib.parse.urlparse(url)
        path = u.path.replace("/trade-api/v2", "")
        if path.endswith("/candlesticks"):
            self.calls.append(url)
            lo = int(urllib.parse.parse_qs(u.query)["start_ts"][0])
            if path.startswith("/series"):
                if self.live_from is not None and lo >= self.live_from:
                    return 200, json.dumps({"candlesticks": [{
                        "end_period_ts": lo + 3600, "yes_bid": {}, "yes_ask": {},
                        "price": {"mean_dollars": "0.5"},
                        "volume_fp": "1.00", "open_interest_fp": "1.00"}]}).encode()
                return 200, b'{"candlesticks": []}'
            return 404, b"{}"
        return super().__call__(url)


def test_candles_empty_first_chunk_then_live_chunk(tmp_path, monkeypatch):
    base = 1_790_000_000
    monkeypatch.setattr(kp, "CANDLE_CHUNK", 1)  # one hourly candle per request
    fake = ChunkFake(live_from=base + 3600)
    rc, _ = run(tmp_path, ["candles", "--market", "KXHIGHNY-26OCT01-T80", "--since", str(base),
                           "--until", str(base + 7200), "--period", "60"], fake)
    assert rc == 0
    rows = read(tmp_path, "candles")
    assert len(rows) == 1 and rows[0]["source"] == "live"  # chunk 2 not lost
    # chunk 1 was tried on live AND historical before being treated as empty
    assert any("/historical/markets/" in c and f"start_ts={base}&" in c for c in fake.calls)
    assert [u for u in manifest(tmp_path) if u["type"] == "unit"][0]["status"] == "ok"


def test_candles_empty_in_both_tiers_is_recorded(tmp_path):
    base = 1_790_000_000
    fake = ChunkFake(live_from=None)
    rc, _ = run(tmp_path, ["candles", "--market", "KXHIGHNY-26OCT01-T80", "--since", str(base),
                           "--until", str(base + 7200)], fake)
    assert rc == 0
    u = [r for r in manifest(tmp_path) if r["type"] == "unit"][0]
    assert u["status"] == "empty_both_tiers" and u["rows"] == 0
    assert any("/historical/markets/" in c for c in fake.calls)


def test_terminal_4xx_writes_run_record_and_checkpoint(tmp_path):
    fake = Fake()
    fake.status_script = [400]
    rc, _ = run(tmp_path, ["series"], fake)
    assert rc == 4
    last = manifest(tmp_path)[-1]
    assert last["type"] == "run" and last["complete"] is False and "HttpError" in last["error"]
    assert list((tmp_path / "_checkpoints").glob("series-*.json"))


def test_exhausted_retries_records_error(tmp_path):
    fake = Fake()
    fake.status_script = [503] * 20
    rc, _ = run(tmp_path, ["series"], fake, max_retries=1)
    assert rc == 4
    assert "HTTP 503" in manifest(tmp_path)[-1]["error"]


def test_orderbook_parts_unique_across_runs(tmp_path):
    for _ in range(2):
        clk = Clock()
        client, _ = mkclient(Fake(), clock=clk)
        args = kp.build_parser().parse_args(["--out", str(tmp_path), "orderbook-snap",
                                             "--markets", "KXHIGHNY-26OCT01-T80",
                                             "--every-s", "60", "--duration-s", "60"])
        kp.cmd_orderbook_snap(client, kp.Store(tmp_path, "orderbook-snap", {}), args, lambda s: None,
                              clock=clk.now, sleep=clk.sleep, utcnow=lambda: kp.parse_dt(NOW))
    # identical second and inputs: the uuid suffix keeps the files from overwriting each other
    assert len(list((tmp_path / "orderbook").rglob("*.parquet"))) == 2


def test_sports_keyword_second_signal():
    rows = [
        {"ticker": "KXNBAMVP", "title": "MVP", "category": "Politics", "categories": ["Politics"]},
        {"ticker": "KXLOLWORLDS", "title": "Worlds", "category": "Entertainment", "categories": ["Entertainment"]},
        {"ticker": "KXWHO", "title": "Who wins the NFL draft", "category": "Entertainment", "categories": []},
        {"ticker": "KXSTUFF", "title": "Summary stuff", "category": "Economics", "categories": ["Economics"]},
        {"ticker": "KXCPI", "title": "CPI", "category": "Economics", "categories": ["Economics"]},
    ]
    kept = kp.allowed_series(rows, None, {"sports"})
    # NOTE: KXWHO has no category list entry but a primary category; the title says NFL
    assert set(kept) == {"KXSTUFF", "KXCPI"}  # 'mma' inside 'summary' is not a title hit
    # explicit opt-in to Sports bypasses the keyword list
    opted = kp.allowed_series([dict(rows[0], category="Sports", categories=["Sports"])], {"sports"}, set())
    assert "KXNBAMVP" in opted


def test_inflation_cpi_fed_pass_sports_and_esports_blocked():
    def r(t, title="x"):
        return {"ticker": t, "title": title, "category": "Economics", "categories": ["Economics"]}
    ok = ["KXINFLATION", "KXCPI", "KXFED", "KXFEDDECISION", "KXSUMMARY", "KXINFL", "INFL", "KXHIGHNY"]
    bad = ["KXNFLGAME", "NFLGAME", "KXNBAMVP", "KXLOLWORLDS", "KXCS2MAJOR", "KXVALORANTMAP",
           "KXMLSTOTAL", "KXUFCFIGHT", "KXSUPERBOWLWINNER", "KXESPORTSX"]
    kept = kp.allowed_series([r(t) for t in ok + bad], None, {"sports"})
    assert set(kept) == set(ok)


def test_allow_series_override_recorded_in_manifest(tmp_path):
    fake = Fake()
    fake.series.append({"ticker": "KXNFLGAME", "title": "NFL", "category": "Sports",
                        "categories": ["Sports"], "tags": [], "frequency": "w", "fee_type": "quadratic",
                        "fee_multiplier": 1.0, "settlement_sources": [], "contract_url": "u",
                        "contract_terms_url": "t"})
    fake.live_markets["KXNFLGAME"] = [Fake.mk("KXNFLGAME-26OCT01-A", "2026-10-01T12:00:00Z")]
    rc, _ = run(tmp_path, ["--allow-series", "KXNFLGAME", "markets", "--series", "KXNFLGAME"], fake)
    assert rc == 0 and [r["ticker"] for r in read(tmp_path, "markets")] == ["KXNFLGAME-26OCT01-A"]
    assert all(m["query"]["allow_series"] == "KXNFLGAME" for m in manifest(tmp_path))
    # without the override it is skipped
    fake2 = Fake()
    run(tmp_path / "n", ["markets", "--series", "KXNFLGAME"], fake2)
    assert not any("series_ticker" in c for c in fake2.calls)


def test_mve_filter_never_sent_with_series_and_combos_dropped_client_side(tmp_path):
    fake = Fake()
    combo = Fake.mk("KXMVECROSS-S1-ABC", "2026-10-02T12:00:00Z")
    combo["mve_collection_ticker"] = "KXMVECROSS"
    fake.live_markets["KXHIGHNY"].append(combo)
    hcombo = Fake.mk("KXHIGHNY-HCOMBO", "2026-05-02T12:00:00Z")
    hcombo["mve_selected_legs"] = [{"market_ticker": "X"}]
    fake.hist_markets["KXHIGHNY"].append(hcombo)
    rc, _ = run(tmp_path, ["markets", "--series", "KXHIGHNY"], fake)
    assert rc == 0  # the fake answers 400 if both are sent
    assert not any("mve_filter" in c for c in fake.calls)
    got = {r["ticker"] for r in read(tmp_path, "markets")}
    assert got == {"KXHIGHNY-26OCT01-T80", "KXHIGHNY-26OCT02-T80", "KXHIGHNY-26OCT03-T80",
                   "KXHIGHNY-26MAY01-T80"}


def test_client_refuses_mve_filter_with_series():
    client, _ = mkclient(Fake())
    for k in ("series_ticker", "event_ticker", "tickers"):
        with pytest.raises(ValueError):
            client.get("/markets", {k: "A", "mve_filter": "exclude"})


# ---------------------------------------------------------------- orderbook-snap --series (discovery)

class OpenFake(Fake):
    """Open-market listing per series that tests mutate between discovery intervals."""

    def __init__(self):
        super().__init__()
        self.open = {"KXHIGHNY": ["KXHIGHNY-A"], "KXFIN": ["KXFIN-A"]}
        self.mve = {"KXHIGHNY": [{"ticker": "KXMVECROSS-1", "mve_collection_ticker": "C"}]}
        self.listed = []

    def __call__(self, url):
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        if u.path.endswith("/markets") and q.get("status") == ["open"]:
            assert "mve_filter" not in q
            s = q["series_ticker"][0]
            self.listed.append(s)
            items = [{"ticker": t} for t in self.open.get(s, [])] + self.mve.get(s, [])
            self.calls.append(url)
            return 200, json.dumps(self.page(items, "markets", q)).encode()
        return super().__call__(url)


def snap_args(tmp_path, *extra):
    return kp.build_parser().parse_args(["--out", str(tmp_path), "orderbook-snap", *extra])


def run_series(tmp_path, fake, argv, on_sleep=None, log=None, rate=1000, globals_=()):
    clk = Clock()
    client, _ = mkclient(fake, clock=clk, rate=rate)

    def sleep(s):
        clk.sleep(s)
        if on_sleep:
            on_sleep(clk)
    args = kp.build_parser().parse_args(["--out", str(tmp_path), *globals_, "orderbook-snap", *argv])
    kp.cmd_orderbook_snap(client, kp.Store(tmp_path, "orderbook-snap", {}), args,
                          log or (lambda s: None), clock=clk.now, sleep=sleep,
                          utcnow=lambda: kp.parse_dt(NOW + clk.t))
    return client, clk


def test_series_discovery_adds_and_drops_markets_and_drops_mve(tmp_path):
    fake = OpenFake()

    def mutate(clk):
        if clk.t >= 1100:
            fake.open["KXHIGHNY"] = ["KXHIGHNY-B"]  # A closed, B opened

    run_series(tmp_path, fake, ["--series", "KXHIGHNY,KXFIN", "--every-s", "60", "--duration-s", "300",
                                "--discover-every-s", "120"], on_sleep=mutate)
    by = {}
    for s in read(tmp_path, "orderbook_snaps"):
        by.setdefault(s["ticker"], []).append(s)
    assert set(by) == {"KXHIGHNY-A", "KXHIGHNY-B", "KXFIN-A"}  # combo never snapped
    assert len(by["KXFIN-A"]) > len(by["KXHIGHNY-A"]) and len(by["KXHIGHNY-B"]) >= 1
    assert by["KXHIGHNY-B"][0]["first_seen_this_run"] > by["KXHIGHNY-A"][0]["first_seen_this_run"]
    # A is no longer snapped once B was discovered
    assert max(s["ts"] for s in by["KXHIGHNY-A"]) < min(s["ts"] for s in by["KXHIGHNY-B"])
    assert fake.listed.count("KXHIGHNY") >= 3
    assert not any("mve_filter" in c for c in fake.calls)


def test_series_sports_refused_unless_allowed(tmp_path):
    fake = OpenFake()
    fake.open["KXNFLGAME"] = ["KXNFLGAME-1"]
    with pytest.raises(SystemExit):
        run_series(tmp_path, fake, ["--series", "KXNFLGAME", "--duration-s", "100"])
    assert fake.listed == [] and not any("/orderbook" in c for c in fake.calls)
    run_series(tmp_path / "ok", fake, ["--series", "KXNFLGAME", "--duration-s", "10"],
               globals_=["--allow-series", "KXNFLGAME"])
    assert {s["ticker"] for s in read(tmp_path / "ok", "orderbook_snaps")} == {"KXNFLGAME-1"}


def test_plan_cadence_never_exceeds_rate():
    e, d, st, req = kp.plan_cadence(10, 60, 300, 2, rate=2.0)
    assert (e, d, st) == (60, 300, False)
    e, d, st, req = kp.plan_cadence(600, 60, 300, 2, rate=2.0)
    assert st and req > 2.0 and 600 / e + 2 / d <= 2.0 + 1e-9
    e, d, st, req = kp.plan_cadence(50, 10, 1, 40, rate=2.0)  # discovery alone too costly
    assert st and d > 1 and 50 / e + 40 / d <= 2.0 + 1e-9


def test_series_cadence_stretched_and_logged_counts_only(tmp_path):
    fake = OpenFake()
    fake.open["KXFIN"] = [f"KXFIN-{i}" for i in range(20)]
    logs = []
    client, clk = run_series(tmp_path, fake, ["--series", "KXFIN", "--every-s", "5",
                                              "--duration-s", "100", "--discover-every-s", "50"],
                             log=logs.append, rate=2.0)
    assert any("stretching" in m for m in logs)
    assert not any("0.4000" in m or "yes_dollars" in m for m in logs)
    assert client.requests_made / (clk.t - 1000.0) <= 2.0 + 0.1  # fake-clock request rate
    ts = sorted(s["ts"] for s in read(tmp_path, "orderbook_snaps") if s["ticker"] == "KXFIN-0")
    assert (ts[1] - ts[0]).total_seconds() >= 10 - 1e-6  # 20 markets at under 2 req/s


def test_series_strict_cadence_refuses(tmp_path):
    fake = OpenFake()
    fake.open["KXFIN"] = [f"KXFIN-{i}" for i in range(20)]
    with pytest.raises(SystemExit):
        run_series(tmp_path, fake, ["--series", "KXFIN", "--every-s", "5", "--duration-s", "100",
                                    "--strict-cadence"], rate=2.0)
    assert not any("/orderbook" in c for c in fake.calls)


def test_series_sigterm_flushes_and_exits_zero(tmp_path):
    import os
    import signal
    fake = OpenFake()
    sent = []

    def term(clk):
        if clk.t >= 1070 and not sent:
            sent.append(1)
            os.kill(os.getpid(), signal.SIGTERM)

    prev = signal.getsignal(signal.SIGTERM)
    _, clk = run_series(tmp_path, fake, ["--series", "KXFIN", "--every-s", "60",
                                         "--duration-s", "100000"], on_sleep=term)
    assert sent and clk.t < 1300  # stopped long before the duration
    assert signal.getsignal(signal.SIGTERM) is prev  # handler restored
    assert len(read(tmp_path, "orderbook_snaps")) >= 1  # buffered rows were flushed


def test_series_main_exit_zero_and_manifest(tmp_path):
    fake = OpenFake()
    rc, _ = run(tmp_path, ["orderbook-snap", "--series", "KXFIN", "--every-s", "1",
                           "--duration-s", "2", "--discover-every-s", "1"], fake)
    assert rc == 0
    recs = [json.loads(l) for l in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert recs[-1]["type"] == "run" and recs[-1]["complete"] is True
    assert recs[-1]["query"]["series"] == "KXFIN" and recs[-1]["query"]["discover_every_s"] == 1


def test_series_max_requests_cap_still_applies(tmp_path):
    fake = OpenFake()
    rc, client = run(tmp_path, ["orderbook-snap", "--series", "KXFIN", "--every-s", "1",
                                "--duration-s", "50", "--discover-every-s", "1"], fake, max_requests=6)
    assert rc == 3 and client.requests_made == 6


def test_markets_and_series_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        snap_args(tmp_path, "--markets", "KXFIN-A", "--series", "KXFIN", "--duration-s", "10")
    with pytest.raises(SystemExit):
        snap_args(tmp_path, "--duration-s", "10")  # one of the two is required


# ---------------------------------------------------------------- review round: contracts, coverage, seal

import os  # noqa: E402
import stat  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

DATE_HDR = "Mon, 05 Oct 2026 12:00:00 GMT"


def iso(unix):
    return datetime.fromtimestamp(unix, tz=timezone.utc).isoformat().replace("+00:00", "Z")


class ScriptFake(OpenFake):
    """Scriptable failures, close times, Date header, round-trip time on the shared clock."""

    def __init__(self, clk=None):
        super().__init__()
        self.clk, self.date = clk, DATE_HDR
        self.fail_series, self.page2_fail, self.page_size = set(), False, None
        self.ob_status, self.close, self.retry_once = {}, {}, set()
        self.out, self.runs_files_at_first_listing = None, None

    def __call__(self, url):
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        if u.path.endswith("/markets") and q.get("status") == ["open"]:
            if self.runs_files_at_first_listing is None and self.out is not None:
                self.runs_files_at_first_listing = len(list((self.out / "orderbook_runs").rglob("*.parquet")))
            s = q["series_ticker"][0]
            self.calls.append(url)
            self.listed.append(s)
            if s in self.fail_series:
                return 404, b"{}"
            if self.page_size:
                if "cursor" in q and self.page2_fail:
                    return 404, b"{}"
                items = [{"ticker": t} for t in self.open.get(s, [])]
                off = int(q["cursor"][0]) if "cursor" in q else 0
                nxt = str(off + self.page_size) if off + self.page_size < len(items) else ""
                return 200, json.dumps({"markets": items[off:off + self.page_size], "cursor": nxt}).encode()
            items = [{"ticker": t, **({"close_time": self.close[t]} if t in self.close else {})}
                     for t in self.open.get(s, [])] + self.mve.get(s, [])
            return 200, json.dumps({"markets": items, "cursor": ""}).encode()
        if u.path.endswith("/orderbook"):
            t = u.path.split("/")[-2]
            self.calls.append(url)
            if t in self.retry_once:
                self.retry_once.discard(t)
                return 500, b"{}"
            if t in self.ob_status:
                return self.ob_status[t], b"{}"
            if self.clk:
                self.clk.t += 2  # round trip
            body = json.dumps({"orderbook_fp": {"yes_dollars": [["0.4000", "10.00"]], "no_dollars": []}}).encode()
            return (200, body, self.date) if self.date else (200, body)
        return super().__call__(url)


def run2(tmp_path, fake, argv, on_sleep=None, log=None, rate=1000, globals_=(), clk=None, max_requests=None):
    clk = clk or Clock()
    fake.clk, fake.out = clk, tmp_path
    client, _ = mkclient(fake, clock=clk, rate=rate, max_requests=max_requests)

    def sleep(s):
        clk.sleep(s)
        if on_sleep:
            on_sleep(clk)
    args = kp.build_parser().parse_args(["--out", str(tmp_path), *globals_, "orderbook-snap", *argv])
    kp.cmd_orderbook_snap(client, kp.Store(tmp_path, "orderbook-snap", {}), args,
                          log or (lambda s: None), clock=clk.now, sleep=sleep,
                          utcnow=lambda: kp.parse_dt(NOW + clk.t))
    return client, clk


def tab(out, kind):
    return kp.read_verified(out, kind, require_complete_runs=False).to_pylist()


BASE = ["--series", "KXFIN", "--every-s", "60", "--discover-every-s", "60"]


def test_ts_is_after_response_and_c1_columns(tmp_path):
    fake = ScriptFake()
    fake.retry_once = {"KXFIN-A"}
    run2(tmp_path, fake, BASE + ["--duration-s", "1"])
    s = tab(tmp_path, "orderbook_snaps")[0]
    assert (s["ts"] - s["req_start_ts"]).total_seconds() >= 2 - 1e-6  # ts taken after the 2 s response
    assert s["attempts"] == 2  # one 500 retried
    assert s["server_date"] == datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
    assert s["first_seen_this_run"] is not None


def test_server_date_null_when_absent(tmp_path):
    fake = ScriptFake()
    fake.date = None
    run2(tmp_path, fake, BASE + ["--duration-s", "1"])
    s = tab(tmp_path, "orderbook_snaps")[0]
    assert s["server_date"] is None and s["attempts"] == 1
    assert kp.parse_server_date("garbage") is None


def test_read_table_unified_fills_old_parts(tmp_path):
    import pyarrow as pa
    old = tmp_path / "orderbook_snaps" / "series=KXOLD" / "date=2026-10-01"
    old.mkdir(parents=True)
    pq.write_table(pa.table({"ts": pa.array([kp.parse_dt(NOW)], type=kp.TS), "ticker": ["KXOLD-1"],
                             "series_ticker": ["KXOLD"], "n_yes_levels": pa.array([1], pa.int32()),
                             "n_no_levels": pa.array([0], pa.int32())}), old / "part-old.parquet")
    run2(tmp_path, ScriptFake(), BASE + ["--duration-s", "1"])
    t = kp.read_table_unified(tmp_path, "orderbook_snaps")
    assert t.schema == kp.SCHEMAS["orderbook_snaps"]
    rows = {r["ticker"]: r for r in t.to_pylist()}
    assert rows["KXOLD-1"]["req_start_ts"] is None and rows["KXOLD-1"]["attempts"] is None
    assert rows["KXFIN-A"]["attempts"] == 1


def test_failed_listing_page2_discards_page1(tmp_path):
    fake = ScriptFake()
    fake.open["KXFIN"] = ["KXFIN-1", "KXFIN-2", "KXFIN-3"]
    fake.page_size, fake.page2_fail = 2, True
    run2(tmp_path, fake, BASE + ["--duration-s", "1"])
    assert not any("/orderbook" in c for c in fake.calls)  # page-1 markets not used
    d = tab(tmp_path, "orderbook_discoveries")[0]
    assert d["ok"] is False and d["consecutive_failures"] == 1


def test_failed_listing_carries_then_drops_after_n_and_records(tmp_path):
    fake = ScriptFake()

    def brk(clk):
        if clk.t >= 1100:
            fake.fail_series = {"KXFIN"}
    run2(tmp_path, fake, BASE + ["--duration-s", "400", "--max-discover-failures", "2"], on_sleep=brk)
    d = tab(tmp_path, "orderbook_discoveries")
    fails = [r for r in d if not r["ok"]]
    assert [r["consecutive_failures"] for r in fails][:2] == [1, 2]
    assert not fails[0]["carried_dropped"] and fails[0]["n_carried"] == 1
    assert fails[1]["carried_dropped"] and fails[1]["n_carried"] == 1
    drop_ts = fails[1]["ts"]
    snaps = [s for s in tab(tmp_path, "orderbook_snaps") if s["ticker"] == "KXFIN-A"]
    assert len(snaps) >= 2 and max(s["ts"] for s in snaps) <= drop_ts  # nothing snapped after the drop
    # the carried market WAS snapped after the first failure
    assert any(s["ts"] > fails[0]["ts"] for s in snaps)


def test_failure_counter_resets_on_success():
    fake = ScriptFake()
    client, _ = mkclient(fake)
    fails = {"KXFIN": 1}
    new, rep = kp.discover_open_markets(client, ["KXFIN"], {}, fails, 3)
    assert fails["KXFIN"] == 0 and rep[0]["ok"] and "KXFIN-A" in new


def test_discovery_stop_between_series():
    fake = ScriptFake()
    client, _ = mkclient(fake)
    n = {"c": 0}

    def stop():
        n["c"] += 1
        return n["c"] > 1
    assert kp.discover_open_markets(client, ["KXHIGHNY", "KXFIN"], {}, {}, 3, stop=stop) is None
    assert fake.listed == ["KXHIGHNY"]  # second series never requested


def test_logs_counts_only_errors_aggregated(tmp_path):
    fake = ScriptFake()
    fake.open["KXFIN"] = ["KXFIN-A", "KXFIN-B", "KXFIN-C"]
    fake.ob_status = {"KXFIN-A": 404, "KXFIN-B": 404}
    logs = []
    run2(tmp_path, fake, BASE + ["--duration-s", "1"], log=logs.append)
    assert logs and not any("KXFIN-" in m for m in logs)
    assert any("{404: 2}" in m for m in logs)
    r = tab(tmp_path, "orderbook_rounds")[0]
    assert r["n_snaps_ok"] == 1 and r["n_errors"] == 2 and json.loads(r["errors_json"]) == {"404": 2}
    e = tab(tmp_path, "orderbook_fetch_errors")
    assert {x["ticker"] for x in e} == {"KXFIN-A", "KXFIN-B"} and {x["status"] for x in e} == {404}


def test_legacy_markets_log_has_no_tickers_and_drops_mve(tmp_path):
    fake = ScriptFake()
    fake.series.append({"ticker": "KXMVEX", "title": "combo", "category": "Financials",
                        "categories": ["Financials"], "tags": [], "frequency": "daily",
                        "fee_type": "quadratic", "fee_multiplier": 1.0, "settlement_sources": [],
                        "contract_url": "u", "contract_terms_url": "t"})
    fake.ob_status = {"KXFIN-B": 404}
    logs = []
    client, clk = mkclient(fake, clock=Clock())
    args = snap_args(tmp_path, "--markets", "KXFIN-A,KXFIN-B,KXMVEX-1", "--duration-s", "1")
    kp.cmd_orderbook_snap(client, kp.Store(tmp_path, "orderbook-snap", {}), args, logs.append,
                          clock=clk.now, sleep=clk.sleep, utcnow=lambda: kp.parse_dt(NOW + clk.t))
    assert not any("KXMVEX-1" in c for c in fake.calls)
    assert any("/KXFIN-A/orderbook" in c for c in fake.calls)
    assert not any("KXFIN-" in m for m in logs) and any("{404: 1}" in m for m in logs)


def test_closed_market_skipped_before_snap(tmp_path):
    fake = ScriptFake()
    fake.open["KXFIN"] = ["KXFIN-A", "KXFIN-B"]
    fake.close = {"KXFIN-A": iso(NOW + 1030)}  # closes between round 1 (t=1000) and round 2 (t=1060)
    run2(tmp_path, fake, BASE + ["--duration-s", "130"])
    a = [s for s in tab(tmp_path, "orderbook_snaps") if s["ticker"] == "KXFIN-A"]
    b = [s for s in tab(tmp_path, "orderbook_snaps") if s["ticker"] == "KXFIN-B"]
    assert len(a) == 1 and len(b) >= 3
    assert sum(r["n_skipped_closed"] for r in tab(tmp_path, "orderbook_rounds")) >= 1
    assert sum("/KXFIN-A/orderbook" in c for c in fake.calls) == 1


def test_duration_stops_mid_round(tmp_path):
    fake = ScriptFake()
    fake.open["KXFIN"] = [f"KXFIN-{i}" for i in range(30)]
    run2(tmp_path, fake, ["--series", "KXFIN", "--every-s", "100", "--duration-s", "3",
                          "--discover-every-s", "100"], rate=2.0)
    snaps = tab(tmp_path, "orderbook_snaps")
    assert 0 < len(snaps) < 30  # a 30-market round at 2 req/s cannot fit in 3 s
    end = [r for r in tab(tmp_path, "orderbook_runs") if r["event"] == "run_end"][0]
    assert end["stop_reason"] == "duration" and end["complete"] is True


def test_coverage_tables_run_start_before_first_request_and_run_end(tmp_path):
    fake = ScriptFake()
    run2(tmp_path, fake, BASE + ["--duration-s", "130"], rate=7.0)
    assert fake.runs_files_at_first_listing == 1  # run_start already on disk at the first listing
    runs = sorted(tab(tmp_path, "orderbook_runs"), key=lambda r: r["event"], reverse=True)
    start = [r for r in runs if r["event"] == "run_start"][0]
    end = [r for r in runs if r["event"] == "run_end"][0]
    assert json.loads(start["series_json"]) == ["KXFIN"]
    assert (start["every_s"], start["depth"], start["rate"]) == (60.0, 0, pytest.approx(7.0))
    assert "git_sha" in start and end["run_id"] == start["run_id"]
    assert end["stop_reason"] == "duration" and end["complete"] is True and end["n_snaps"] >= 2
    d = tab(tmp_path, "orderbook_discoveries")[0]
    assert d["ok"] and d["n_open"] == 1 and d["series_ticker"] == "KXFIN"
    r = tab(tmp_path, "orderbook_rounds")[0]
    assert r["n_open"] == 1 and r["stretched"] is False and r["every_eff"] == 60.0


def test_combos_counted_in_discoveries(tmp_path):
    fake = ScriptFake()
    run2(tmp_path, fake, ["--series", "KXHIGHNY", "--duration-s", "1"])
    d = tab(tmp_path, "orderbook_discoveries")[0]
    assert d["combos_dropped"] == 1 and d["n_open"] == 1


def test_run_end_sigterm_and_max_requests(tmp_path):
    import signal
    fake = ScriptFake()
    sent = []

    def term(clk):
        if clk.t >= 1070 and not sent:
            sent.append(1)
            os.kill(os.getpid(), signal.SIGTERM)
    run2(tmp_path / "a", fake, BASE + ["--duration-s", "100000"], on_sleep=term)
    end = [r for r in tab(tmp_path / "a", "orderbook_runs") if r["event"] == "run_end"][0]
    assert end["stop_reason"] == "sigterm" and end["complete"] is False
    with pytest.raises(kp.MaxRequestsReached):
        run2(tmp_path / "b", ScriptFake(), BASE + ["--duration-s", "300"], max_requests=4)
    end = [r for r in tab(tmp_path / "b", "orderbook_runs") if r["event"] == "run_end"][0]
    assert end["stop_reason"] == "max_requests" and end["complete"] is False


def test_seal_chain_hourly_mode_and_verify(tmp_path):
    fake = ScriptFake()
    run2(tmp_path, fake, ["--series", "KXFIN", "--every-s", "600", "--discover-every-s", "600",
                          "--duration-s", "4000"])
    chain = tmp_path / kp.CHAIN_NAME
    assert stat.S_IMODE(chain.stat().st_mode) == 0o440
    lines = chain.read_text().splitlines()
    recs = [json.loads(x) for x in lines]
    assert len(recs) >= 2 and len({r["hour"] for r in recs}) >= 2  # crossed an hour boundary
    assert recs[0]["prev"] == "0" * 64
    import hashlib as h
    assert recs[1]["prev"] == h.sha256(lines[0].encode()).hexdigest()
    f = recs[0]["files"][0]
    assert set(f) == {"path", "sha256", "rows", "bytes"}
    entries = kp.verify_parts(tmp_path)
    assert all(k in entries for k in [x["path"] for r in recs for x in r["files"]])
    assert len(kp.read_verified(tmp_path, "orderbook_snaps").to_pylist()) >= 6


def test_seal_idempotent_and_chmod_retry(tmp_path):
    run2(tmp_path, ScriptFake(), BASE + ["--duration-s", "1"])
    before = (tmp_path / kp.CHAIN_NAME).read_bytes()
    parts = sorted(tmp_path.rglob("part-*.parquet"))
    assert kp.seal_parts(tmp_path, "2026-09-21T14", parts) is False
    os.chmod(tmp_path / kp.CHAIN_NAME, 0o640)
    assert kp.seal_parts(tmp_path, "2026-09-21T14", parts) is False
    assert (tmp_path / kp.CHAIN_NAME).read_bytes() == before
    assert stat.S_IMODE((tmp_path / kp.CHAIN_NAME).stat().st_mode) == 0o440


def test_verify_refuses_tampered_uncovered_missing_and_broken_chain(tmp_path):
    import shutil
    run2(tmp_path, ScriptFake(), BASE + ["--duration-s", "1"])
    kp.verify_parts(tmp_path)
    part = sorted((tmp_path / "orderbook_snaps").rglob("*.parquet"))[0]
    good = part.read_bytes()
    part.write_bytes(good + b"x")
    with pytest.raises(kp.SealError, match="mismatch"):
        kp.read_verified(tmp_path, "orderbook_snaps")
    part.write_bytes(good)
    extra = part.with_name("part-extra.parquet")
    shutil.copy(part, extra)
    with pytest.raises(kp.SealError, match="not covered"):
        kp.verify_parts(tmp_path)
    extra.unlink()
    part.rename(part.with_name("moved.bak"))
    with pytest.raises(kp.SealError, match="missing"):
        kp.verify_parts(tmp_path)
    part.with_name("moved.bak").rename(part)
    chain = tmp_path / kp.CHAIN_NAME
    chain.chmod(0o640)
    rec = json.loads(chain.read_text().splitlines()[0])
    rec["prev"] = "1" * 64
    chain.write_text(json.dumps(rec) + "\n")
    with pytest.raises(kp.SealError, match="prev"):
        kp.verify_parts(tmp_path)
    chain.unlink()
    with pytest.raises(kp.SealError, match="nothing is sealed"):
        kp.verify_parts(tmp_path)


# ---------------------------------------------------------------- hardening (#701 reviews)

def test_keyboard_interrupt_writes_interrupt_and_incomplete(tmp_path):
    def boom(clk):
        if clk.t >= 1070:
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        run2(tmp_path, ScriptFake(), BASE + ["--duration-s", "100000"], on_sleep=boom)
    end = [r for r in tab(tmp_path, "orderbook_runs") if r["event"] == "run_end"][0]
    assert end["stop_reason"] == "interrupt" and end["complete"] is False
    assert not (tmp_path / ".orderbook.lock").read_text().strip() == ""  # pid file remains
    kp.acquire_lock(tmp_path).close()  # lock released on the way out


def test_exclusive_lock_exit_5_counts_only_and_pid(tmp_path):
    logs = []
    held = kp.acquire_lock(tmp_path)
    try:
        assert (tmp_path / ".orderbook.lock").read_text().strip() == str(os.getpid())
        fake = ScriptFake()
        with pytest.raises(SystemExit) as e:
            run2(tmp_path, fake, BASE + ["--duration-s", "10"], log=logs.append)
        assert e.value.code == 5
        assert fake.calls == [] and not (tmp_path / "orderbook_runs").exists()  # before run_start
        assert not any(str(tmp_path) in m or "KXFIN" in m for m in logs)
    finally:
        held.close()
    run2(tmp_path, ScriptFake(), BASE + ["--duration-s", "1"])  # free again


def test_chain_head_logged_at_every_seal_and_in_run_end(tmp_path):
    logs = []
    run2(tmp_path, ScriptFake(), ["--series", "KXFIN", "--every-s", "600", "--discover-every-s", "600",
                                  "--duration-s", "4000"], log=logs.append)
    seals = [m for m in logs if m.startswith("seal:")]
    assert len(seals) >= 2
    heads = [m.split("chain_head=")[1] for m in seals]
    assert heads[-1] == kp.chain_head(tmp_path)
    assert all(len(h) == 64 for h in heads) and all("KXFIN" not in m for m in seals)
    ends = [r for r in tab(tmp_path, "orderbook_runs") if r["event"] == "run_end"]
    lines = (tmp_path / kp.CHAIN_NAME).read_text().splitlines()
    # run_end is sealed in the last line, so its head is the head of the line before it
    assert ends[0]["chain_head"] == hashlib.sha256(lines[-2].encode()).hexdigest()
    assert ends[0]["chain_head"] in heads


def test_read_verified_requires_complete_runs(tmp_path):
    def term(clk):
        if clk.t >= 1070:
            os.kill(os.getpid(), signal.SIGTERM)
    run2(tmp_path / "a", ScriptFake(), BASE + ["--duration-s", "100000"], on_sleep=term)
    with pytest.raises(kp.SealError, match="complete"):
        kp.read_verified(tmp_path / "a", "orderbook_snaps")
    assert kp.read_verified(tmp_path / "a", "orderbook_snaps", require_complete_runs=False).num_rows > 0
    run2(tmp_path / "b", ScriptFake(), BASE + ["--duration-s", "1"])
    assert kp.read_verified(tmp_path / "b", "orderbook_snaps").num_rows > 0


def test_quarantine_unsealed_moves_only_uncovered_and_writes_manifest(tmp_path):
    import shutil
    run2(tmp_path, ScriptFake(), BASE + ["--duration-s", "1"])
    sealed = sorted((tmp_path / "orderbook_snaps").rglob("*.parquet"))[0]
    extra = sealed.with_name("part-extra.parquet")
    shutil.copy(sealed, extra)
    chain_before = (tmp_path / kp.CHAIN_NAME).read_bytes()
    sealed_before = sealed.read_bytes()
    want_sha = kp.sha256_file(extra)
    assert kp.main(["--out", str(tmp_path), "quarantine-unsealed"]) == 0
    assert not extra.exists() and sealed.read_bytes() == sealed_before
    assert (tmp_path / kp.CHAIN_NAME).read_bytes() == chain_before
    kp.verify_parts(tmp_path)  # clean again
    (qdir,) = (tmp_path / "_quarantine").iterdir()
    man = qdir / "manifest.json"
    assert stat.S_IMODE(man.stat().st_mode) == 0o440
    (f,) = json.loads(man.read_text())["files"]
    assert f == {"path": str(extra.relative_to(tmp_path)), "sha256": want_sha,
                 "rows": pq.read_metadata(qdir / f["path"]).num_rows, "bytes": f["bytes"]}
    assert (qdir / f["path"]).exists()
    assert kp.main(["--out", str(tmp_path), "quarantine-unsealed"]) == 0  # nothing left: no new dir
    assert len(list((tmp_path / "_quarantine").iterdir())) == 1
    held = kp.acquire_lock(tmp_path)
    try:
        assert kp.main(["quarantine-unsealed", "--out", str(tmp_path)]) == 5
    finally:
        held.close()


def test_first_failed_listing_retries_after_min_60_and_keeps_rate_guard(tmp_path):
    fake = ScriptFake()
    fake.fail_series = {"KXFIN"}

    def heal(clk):
        if clk.t >= 1100:
            fake.fail_series = set()
    run2(tmp_path, fake, ["--series", "KXFIN", "--every-s", "60", "--discover-every-s", "600",
                          "--duration-s", "200"], on_sleep=heal)
    d = tab(tmp_path, "orderbook_discoveries")
    d = sorted(d, key=lambda r: r["ts"])
    assert d[0]["ok"] is False and (d[1]["ts"] - d[0]["ts"]).total_seconds() == pytest.approx(60, abs=2)
    assert any(r["ok"] for r in d) and tab(tmp_path, "orderbook_snaps")  # recovered long before 600 s
    # rate guard: at --rate 0.02 (1 request / 50 s) discovery may use at most half the budget,
    # i.e. one listing per 100 s, not one per min(60, 600) s
    fake2 = ScriptFake()
    fake2.fail_series = {"KXFIN"}
    run2(tmp_path / "slow", fake2, ["--series", "KXFIN", "--every-s", "60", "--discover-every-s", "600",
                                    "--duration-s", "1000"], rate=0.02)
    assert 5 <= len(tab(tmp_path / "slow", "orderbook_discoveries")) <= 11
