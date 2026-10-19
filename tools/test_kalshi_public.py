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
    "/incentive_programs/abc", "/incentive_programs/abc/claim", "/incentive_programs/../portfolio/orders",
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
    "/historical/markets/KX-1/candlesticks", "/incentive_programs"])
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
                           "incentive-snap", "quarantine-unsealed"}


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


# ---- candles: per-ticker window bounded to the market's own open/close ----

DAY = 86400
WIN_SINCE = kp.to_unix("2026-07-01T00:00:00Z")
WIN_UNTIL = WIN_SINCE + 109 * DAY  # 2026-10-18
LIFE_T = "KXHIGHNY-26SEP12-T80"
LIFE_OPEN = kp.to_unix("2026-09-10T14:00:00Z")
LIFE_CLOSE = LIFE_OPEN + 2 * DAY  # a 2-day market


class LifeFake(Fake):
    """Daily-high style markets that live ~2 days inside a 109-day window.  Candles are served
    on the live tier for every period whose end_period_ts is in [start_ts, end_ts] and inside the
    market's life (with_data), else both tiers are empty (historical 404).  Counts candle GETs."""

    def __init__(self, markets=((LIFE_T, LIFE_OPEN, LIFE_CLOSE),), with_data=False, period_s=60):
        super().__init__()
        self.life = {t: (o, c) for t, o, c in markets}
        self.live_markets = {"KXHIGHNY": [self.mk_life(t, o, c) for t, o, c in markets]}
        self.hist_markets = {}
        self.with_data, self.period_s = with_data, period_s
        self.live_reqs, self.hist_reqs, self.windows = [], [], []

    @staticmethod
    def mk_life(t, o, c):
        m = Fake.mk(t, kp.parse_dt(c + 3600).strftime("%Y-%m-%dT%H:%M:%SZ"))
        m["open_time"] = kp.parse_dt(o).strftime("%Y-%m-%dT%H:%M:%SZ")
        m["close_time"] = kp.parse_dt(c).strftime("%Y-%m-%dT%H:%M:%SZ")
        return m

    def __call__(self, url):
        u = urllib.parse.urlparse(url)
        path = u.path.replace("/trade-api/v2", "")
        if not path.endswith("/candlesticks"):
            return super().__call__(url)
        self.calls.append(url)
        q = urllib.parse.parse_qs(u.query)
        lo, hi = int(q["start_ts"][0]), int(q["end_ts"][0])
        t = path.split("/")[-2]
        if path.startswith("/series"):
            self.live_reqs.append(t)
            self.windows.append((lo, hi))
            if not self.with_data:
                return 200, b'{"candlesticks": []}'
            o, c = self.life[t]
            e0 = (max(lo, o) // self.period_s + 1) * self.period_s
            ends = range(e0, min(hi, c) + 1, self.period_s)
            return 200, json.dumps({"candlesticks": [
                {"end_period_ts": e, "yes_bid": {}, "yes_ask": {}, "price": {"mean_dollars": "0.5"},
                 "volume_fp": "1.00", "open_interest_fp": "1.00"} for e in ends]}).encode()
        self.hist_reqs.append(t)
        return 404, b"{}"

    def candle_gets(self):
        return len(self.live_reqs) + len(self.hist_reqs)


CANDLE_ARGS = ["--since", str(WIN_SINCE), "--until", str(WIN_UNTIL), "--period", "1"]


def test_candles_two_day_market_unbounded_when_open_close_unknown(tmp_path, capsys):
    # --market mode, nothing stored: old behaviour, the whole 109-day window, counted in the log
    fake = LifeFake()
    rc, _ = run(tmp_path, ["candles", "--market", LIFE_T, *CANDLE_ARGS], fake)
    assert rc == 0
    chunks = -(-(WIN_UNTIL - WIN_SINCE) // (kp.CANDLE_CHUNK * 60))  # 109 d of 1-min candles / 1000
    assert chunks == 157
    assert (len(fake.live_reqs), len(fake.hist_reqs)) == (157, 157)  # both tiers, every chunk
    assert fake.windows[0][0] == WIN_SINCE and fake.windows[-1][1] == WIN_UNTIL
    u = [r for r in manifest(tmp_path) if r["type"] == "unit"][0]
    assert u["window"] == "unbounded" and u["chunks"] == 157 and u["status"] == "empty_both_tiers"
    err = capsys.readouterr().err
    assert "0 ticker(s) bounded" in err and "1 unbounded" in err


def test_candles_two_day_market_bounded_in_series_mode(tmp_path, capsys):
    # --series mode: open/close come from the settled listing already fetched, no extra request
    fake = LifeFake()
    rc, _ = run(tmp_path, ["candles", "--series", "KXHIGHNY", *CANDLE_ARGS], fake)
    assert rc == 0
    # 2 days + one period each side = 2882 one-minute candles -> 3 chunks, 2 tiers each
    assert (len(fake.live_reqs), len(fake.hist_reqs)) == (3, 3)
    assert fake.windows[0][0] == LIFE_OPEN - 60 and fake.windows[-1][1] == LIFE_CLOSE + 60
    assert all(b[1] - b[0] <= kp.CANDLE_CHUNK * 60 for b in fake.windows)
    u = [r for r in manifest(tmp_path) if r["type"] == "unit"][0]
    assert u["window"] == "bounded" and u["chunks"] == 3 and u["status"] == "empty_both_tiers"
    assert "1 ticker(s) bounded" in capsys.readouterr().err
    # besides the 6 candle GETs: one /series, one cutoff, one /markets page
    assert len(fake.calls) == 3 + 6


def test_candles_bounded_and_unbounded_return_the_same_rows(tmp_path):
    a, b = tmp_path / "bounded", tmp_path / "unbounded"
    fa, fb = LifeFake(with_data=True), LifeFake(with_data=True)
    assert run(a, ["candles", "--series", "KXHIGHNY", *CANDLE_ARGS], fa)[0] == 0
    assert run(b, ["candles", "--market", LIFE_T, *CANDLE_ARGS], fb)[0] == 0
    ra, rb = read(a, "candles"), read(b, "candles")
    assert len(ra) == len(rb) == 2 * 24 * 60  # one candle per minute of the 2 days
    assert {r["end_period_ts"] for r in ra} == {r["end_period_ts"] for r in rb}
    assert all(r["source"] == "live" for r in ra)
    # data in every bounded chunk: live answered, historical never needed
    assert (len(fa.live_reqs), len(fa.hist_reqs)) == (3, 0)
    assert len(fb.live_reqs) == 157 and len(fb.hist_reqs) == 154


def test_candles_market_mode_bounds_from_stored_markets_table(tmp_path):
    fake = LifeFake()
    assert run(tmp_path, ["markets", "--series", "KXHIGHNY", "--since", str(WIN_SINCE)], fake)[0] == 0
    n0 = len(fake.calls)
    rc, _ = run(tmp_path, ["candles", "--market", LIFE_T, *CANDLE_ARGS], fake)
    assert rc == 0
    assert (len(fake.live_reqs), len(fake.hist_reqs)) == (3, 3)
    # /series for the category check + the 6 candle GETs; the stored table cost no request
    assert len(fake.calls) - n0 == 1 + 6
    assert [r for r in manifest(tmp_path) if r["type"] == "unit"][-1]["window"] == "bounded"


def test_candle_window_clipping_and_fallbacks():
    s, h = 30 * DAY, 40 * DAY
    assert kp.candle_window(0, 100 * DAY, s, h, 60) == (s - 60, h + 60, True)
    assert kp.candle_window(s, h, s - 5 * DAY, h + 5 * DAY, 60) == (s, h, True)  # market outlives window
    assert kp.candle_window(0, 100 * DAY, None, h, 60) == (0, 100 * DAY, False)
    assert kp.candle_window(0, 100 * DAY, s, None, 60) == (0, 100 * DAY, False)
    assert kp.candle_window(0, 100 * DAY, h, s, 60) == (0, 100 * DAY, False)  # close < open: bad data
    assert kp.candle_window(0, 100 * DAY, s, h, 3600) == (s - 3600, h + 3600, True)  # one period = 1 h


def test_candles_market_outside_window_sends_no_request(tmp_path):
    late = (LIFE_T, WIN_UNTIL + 5 * DAY, WIN_UNTIL + 7 * DAY)  # opens after --until
    fake = LifeFake(markets=(late,))
    rc, _ = run(tmp_path, ["candles", "--series", "KXHIGHNY", *CANDLE_ARGS], fake)
    assert rc == 0
    assert fake.candle_gets() == 0
    u = [r for r in manifest(tmp_path) if r["type"] == "unit"][0]
    assert u["status"] == "outside_window" and u["rows"] == 0 and u["chunks"] == 0
    assert u["window"] == "bounded"


def test_candles_resume_skips_done_units_and_new_units_use_bounded_window(tmp_path):
    other = ("KXHIGHNY-26SEP20-T80", LIFE_OPEN + 8 * DAY, LIFE_CLOSE + 8 * DAY)
    args = ["candles", "--series", "KXHIGHNY", *CANDLE_ARGS]
    f1 = LifeFake(markets=((LIFE_T, LIFE_OPEN, LIFE_CLOSE), other))
    # /series + /historical/cutoff + /markets = 3, then 6 candle GETs for the first market; the cap
    # (checked before each attempt) stops the second one on its first candle GET
    rc, _ = run(tmp_path, args, f1, max_requests=9)
    assert rc == 3
    assert set(f1.live_reqs + f1.hist_reqs) == {LIFE_T}
    f2 = LifeFake(markets=((LIFE_T, LIFE_OPEN, LIFE_CLOSE), other))
    rc, _ = run(tmp_path, args, f2)
    assert rc == 0
    assert set(f2.live_reqs + f2.hist_reqs) == {other[0]}  # the done unit is not requested again
    assert (len(f2.live_reqs), len(f2.hist_reqs)) == (3, 3)  # the new unit: bounded
    assert f2.windows[0][0] == other[1] - 60 and f2.windows[-1][1] == other[2] + 60
    units = [r for r in manifest(tmp_path) if r["type"] == "unit"]
    assert [u["unit"] for u in units] == [f"{LIFE_T}:1", f"{other[0]}:1"]


def test_stored_market_lifetimes_merges_duplicates_and_nulls(tmp_path):
    ft = kp.parse_dt
    rows = [
        {"ticker": "A-1", "open_time": ft(100), "close_time": None, "fetched_at": ft(10)},
        {"ticker": "A-1", "open_time": None, "close_time": ft(500), "fetched_at": ft(20)},
        {"ticker": "A-1", "open_time": ft(150), "close_time": ft(600), "fetched_at": ft(30)},
        {"ticker": "B-1", "open_time": None, "close_time": None, "fetched_at": ft(10)},
    ]
    store = kp.Store(tmp_path, "markets", {})
    store.write("markets", rows, part="x")
    got = kp.stored_market_lifetimes(tmp_path)
    assert got["A-1"] == (150, 600) and got["B-1"] == (None, None)
    assert kp.stored_market_lifetimes(tmp_path / "nowhere") == {}


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


# ---------------------------------------------------------------- incentive-snap (LIP program list)

# Shape of a live row (GET /incentive_programs, 2026-10-19); note the 4-digit fractional second.
LIVE_ROW = {
    "discount_factor_bps": 5000, "end_date": "2026-10-19T23:16:24.0773Z",
    "id": "651f29d7-c15b-4b49-8394-538d94058e4a", "incentive_description": "series_lip",
    "incentive_type": "liquidity", "market_id": "9baa8cc2-a5ac-4388-932b-86093ec9ed12",
    "market_ticker": "KXRT-CLA-60", "paid_out": False, "period_reward": 1000000,
    "start_date": "2026-10-18T23:16:24.0773Z", "target_size_fp": "1000.00"}


def ip_row(i, **kw):
    r = dict(LIVE_ROW, id=f"id-{i:04d}", market_id=f"mid-{i}", market_ticker=f"KXRT-CLA-{i}")
    r.update(kw)
    return r


class IncentiveFake(Fake):
    """Serves /incentive_programs like the live endpoint: list key incentive_programs, cursor key
    next_cursor (NOT cursor), opaque cursor, no cursor key on the last page's next_cursor."""

    def __init__(self, n=250, clk=None):
        super().__init__()
        self.clk, self.programs, self.date = clk, [ip_row(i) for i in range(n)], DATE_HDR
        self.fail = {}  # 1-based page number within a poll -> HTTP status
        self.list_key, self.cursor_key, self.same_cursor = "incentive_programs", "next_cursor", False
        self.out, self.runs_files_at_first_page = None, None
        self.on_page = None  # callable(page_no) run before the response is built
        self.ip_calls = []

    def __call__(self, url):
        u = urllib.parse.urlparse(url)
        if not u.path.endswith("/incentive_programs"):
            return super().__call__(url)
        q = urllib.parse.parse_qs(u.query)
        self.calls.append(url)
        self.ip_calls.append(q)
        if self.runs_files_at_first_page is None and self.out is not None:
            self.runs_files_at_first_page = len(list((self.out / "incentive_runs").rglob("*.parquet")))
        if self.status_script:  # same contract as Fake: statuses to emit before behaving normally
            st = self.status_script.pop(0)
            if st != 200:
                return st, b"{}"
        lim = int(q["limit"][0])
        cur = q["cursor"][0] if "cursor" in q else None
        off = 0 if cur is None else (lim if cur == "cur-same" else int(cur.split("-")[1]))
        page_no = off // lim + 1
        if self.on_page:
            self.on_page(page_no)
        if page_no in self.fail:
            return self.fail[page_no], b"{}"
        if self.clk:
            self.clk.t += 0.5  # round trip
        nxt = f"cur-{off + lim}" if off + lim < len(self.programs) else ""
        if self.same_cursor:
            nxt = "cur-same"
        body = {self.list_key: self.programs[off:off + lim], self.cursor_key: nxt}
        raw = json.dumps(body).encode()
        return (200, raw, self.date) if self.date else (200, raw)


IP_BASE = ["incentive-snap", "--every-s", "900", "--page-limit", "100"]


def run_ip(tmp_path, fake, argv, on_sleep=None, log=None, rate=1000, globals_=(), clk=None,
           max_requests=None):
    clk = clk or Clock()
    fake.clk, fake.out = clk, tmp_path
    client, _ = mkclient(fake, clock=clk, rate=rate, max_requests=max_requests)

    def sleep(s):
        clk.sleep(s)
        if on_sleep:
            on_sleep(clk)
    args = kp.build_parser().parse_args(["--out", str(tmp_path), *globals_, *argv])
    kp.cmd_incentive_snap(client, kp.Store(tmp_path, "incentive-snap", {}), args,
                          log or (lambda s: None), clock=clk.now, sleep=sleep,
                          utcnow=lambda: kp.parse_dt(NOW + clk.t))
    return client, clk


def ip_runs(out, event):
    return [r for r in tab(out, "incentive_runs") if r["event"] == event]


def test_incentive_pagination_follows_next_cursor_until_it_ends(tmp_path):
    fake = IncentiveFake(n=250)
    run_ip(tmp_path, fake, IP_BASE + ["--duration-s", "1"])
    assert len(fake.ip_calls) == 3  # 100 + 100 + 50 rows, then the empty cursor ends the poll
    assert [("cursor" in q) for q in fake.ip_calls] == [False, True, True]
    assert all(q["limit"] == ["100"] and q["status"] == ["active"] and q["type"] == ["liquidity"]
               for q in fake.ip_calls)
    assert fake.ip_calls[1]["cursor"] == ["cur-100"] and fake.ip_calls[2]["cursor"] == ["cur-200"]
    (p,) = tab(tmp_path, "incentive_polls")
    assert (p["ok"], p["end_reason"], p["n_pages"], p["n_rows"], p["n_rows_discarded"], p["requests"]) == \
        (True, "cursor_end", 3, 250, 0, 3)
    assert p["status"] is None and p["n_distinct_ids"] == 250 and p["n_missing_id"] == 0
    assert len(tab(tmp_path, "incentive_programs")) == 250


def test_incentive_poll_accepts_plain_cursor_key_as_fallback():
    fake = IncentiveFake(n=130)
    fake.cursor_key = "cursor"
    client, _ = mkclient(fake)
    res = kp.poll_incentive_programs(client, {"limit": 100}, max_pages=10)
    assert res["ok"] and res["end_reason"] == "cursor_end" and len(res["pages"]) == 2
    assert sum(len(rows) for _, _, rows in res["pages"]) == 130


def test_incentive_every_row_stored_with_raw_json_and_typed_columns(tmp_path):
    fake = IncentiveFake(n=203)
    fake.programs[5] = ip_row(5, paid_out=True, future_field={"a": [1, 2]}, status="paid_out")
    fake.programs[6] = ip_row(6, target_size_fp="2500.00", discount_factor_bps=4000,
                              period_reward=6000000, start_date="2026-10-19T03:01:16.824443Z",
                              end_date="2026-10-24T22:50:00Z", incentive_description="")
    fake.programs[7] = dict(LIVE_ROW, id="id-0007", market_ticker=None, incentive_type=None)
    del fake.programs[7]["market_id"]
    run_ip(tmp_path, fake, IP_BASE + ["--duration-s", "1"])
    rows = sorted(tab(tmp_path, "incentive_programs"), key=lambda r: r["id"])
    assert len(rows) == 203 and [r["id"] for r in rows] == [f"id-{i:04d}" for i in range(203)]
    for r, src in zip(rows, fake.programs):
        assert json.loads(r["raw_json"]) == src  # raw JSON kept, nothing dropped
    r0, r5, r6, r7 = rows[0], rows[5], rows[6], rows[7]
    assert r0["market_ticker"] == "KXRT-CLA-0" and r0["series_ticker"] == "KXRT"
    assert (r0["period_reward"], r0["target_size_fp"], r0["discount_factor_bps"]) == (1000000.0, 1000.0, 5000.0)
    assert r0["paid_out"] is False and r0["incentive_type"] == "liquidity" and r0["status"] is None
    assert r0["incentive_description"] == "series_lip" and r0["market_id"] == "mid-0"
    assert r0["start_date"] == kp.parse_dt("2026-10-18T23:16:24.077300Z")
    assert r0["end_date"] == kp.parse_dt("2026-10-19T23:16:24.0773Z")
    assert r5["paid_out"] is True and r5["status"] == "paid_out"
    assert json.loads(r5["raw_json"])["future_field"] == {"a": [1, 2]}  # unknown fields survive in raw
    assert (r6["target_size_fp"], r6["discount_factor_bps"], r6["period_reward"]) == (2500.0, 4000.0, 6000000.0)
    assert r6["start_date"] == kp.parse_dt("2026-10-19T03:01:16.824443Z") and r6["incentive_description"] == ""
    assert r7["market_ticker"] is None and r7["series_ticker"] is None and r7["market_id"] is None
    assert {r["page"] for r in rows} == {1, 2, 3} and {r["page"] for r in rows if r["id"] < "id-0100"} == {1}
    assert all(r["poll"] == 1 and r["poll_ts"] == rows[0]["poll_ts"] for r in rows)


def test_incentive_timestamps_poll_ts_before_request_ts_after_and_server_date(tmp_path):
    fake = IncentiveFake(n=150)
    run_ip(tmp_path, fake, IP_BASE + ["--duration-s", "1"])
    rows = tab(tmp_path, "incentive_programs")
    p1 = [r for r in rows if r["page"] == 1][0]
    p2 = [r for r in rows if r["page"] == 2][0]
    assert (p1["ts"] - p1["poll_ts"]).total_seconds() >= 0.5 - 1e-6  # ts taken AFTER the 0.5 s response
    assert (p2["ts"] - p1["ts"]).total_seconds() >= 0.5 - 1e-6 and p2["poll_ts"] == p1["poll_ts"]
    assert p1["server_date"] == kp.parse_dt("2026-10-05T12:00:00Z")
    fake2 = IncentiveFake(n=10)
    fake2.date = None
    run_ip(tmp_path / "nodate", fake2, IP_BASE + ["--duration-s", "1"])
    assert all(r["server_date"] is None for r in tab(tmp_path / "nodate", "incentive_programs"))


def test_incentive_polls_repeat_every_interval_and_track_changes(tmp_path):
    fake = IncentiveFake(n=120)

    def mutate(clk):
        if clk.t >= 1000 + 900 and len(fake.programs) == 120:
            fake.programs.append(ip_row(900))  # a new program appears
            fake.programs[0] = ip_row(0, paid_out=True)
        if clk.t >= 1000 + 1800 and len(fake.programs) == 121:
            del fake.programs[1]  # one ended

    run_ip(tmp_path, fake, IP_BASE + ["--duration-s", "2000"], on_sleep=mutate)
    polls = sorted(tab(tmp_path, "incentive_polls"), key=lambda r: r["poll"])
    assert [p["poll"] for p in polls] == [1, 2, 3] and all(p["ok"] for p in polls)
    assert [p["n_rows"] for p in polls] == [120, 121, 120]
    gaps = [(b["poll_ts"] - a["poll_ts"]).total_seconds() for a, b in zip(polls, polls[1:])]
    assert all(900 <= g <= 905 for g in gaps)
    rows = tab(tmp_path, "incentive_programs")
    by = {}
    for r in rows:
        by.setdefault(r["poll"], {})[r["id"]] = r
    assert len(rows) == 361 and "id-0900" in by[2] and "id-0900" not in by[1]
    assert by[1]["id-0000"]["paid_out"] is False and by[2]["id-0000"]["paid_out"] is True
    assert "id-0001" in by[2] and "id-0001" not in by[3]
    assert all(len({r["poll_ts"] for r in rows if r["poll"] == k}) == 1 for k in (1, 2, 3))


def test_incentive_duplicate_and_missing_ids_stored_as_received_and_counted(tmp_path):
    fake = IncentiveFake(n=150)
    fake.programs[120] = dict(fake.programs[3])  # same id on page 1 and page 2
    del fake.programs[130]["id"]
    run_ip(tmp_path, fake, IP_BASE + ["--duration-s", "1"])
    (p,) = tab(tmp_path, "incentive_polls")
    assert p["n_rows"] == 150 and p["n_distinct_ids"] == 148 and p["n_missing_id"] == 1
    assert len(tab(tmp_path, "incentive_programs")) == 150


def test_incentive_failed_page_discards_the_whole_poll_and_next_poll_recovers(tmp_path):
    fake = IncentiveFake(n=250)
    fake.fail = {2: 404}

    def heal(clk):
        if clk.t >= 1000 + 900:
            fake.fail = {}
    logs = []
    run_ip(tmp_path, fake, IP_BASE + ["--duration-s", "1000"], on_sleep=heal, log=logs.append)
    polls = sorted(tab(tmp_path, "incentive_polls"), key=lambda r: r["poll"])
    assert [p["ok"] for p in polls] == [False, True]
    bad = polls[0]
    assert (bad["end_reason"], bad["status"], bad["n_pages"], bad["n_rows"], bad["n_rows_discarded"]) == \
        ("http_error", 404, 1, 0, 100)
    rows = tab(tmp_path, "incentive_programs")
    assert {r["poll"] for r in rows} == {2} and len(rows) == 250  # page 1 of poll 1 was not half-used
    assert any("ok=False" in m and "reason=http_error" in m and "status=404" in m for m in logs)
    assert not any("KXRT" in m or "id-0" in m for m in logs)  # counts only
    end = ip_runs(tmp_path, "run_end")[0]
    assert (end["n_polls"], end["n_polls_ok"], end["n_rows"]) == (2, 1, 250)


def test_incentive_transient_500_is_retried_by_the_shared_client(tmp_path):
    fake = IncentiveFake(n=150)
    fake.status_script = [200, 500, 429]  # page 1 ok, page 2 first try 500 then 429, then ok
    run_ip(tmp_path, fake, IP_BASE + ["--duration-s", "1"])
    (p,) = tab(tmp_path, "incentive_polls")
    assert p["ok"] and p["n_rows"] == 150 and p["requests"] == 4


@pytest.mark.parametrize("mode,reason", [("same_cursor", "repeated_cursor"), ("max_pages", "max_pages"),
                                         ("renamed_key", "bad_shape"), ("not_a_list", "bad_shape")])
def test_incentive_poll_that_does_not_reach_the_cursor_end_is_not_ok(tmp_path, mode, reason):
    fake = IncentiveFake(n=500)
    extra = []
    if mode == "same_cursor":
        fake.same_cursor = True
    elif mode == "max_pages":
        extra = ["--max-pages", "2"]
    elif mode == "renamed_key":
        fake.list_key = "programs"
    else:
        fake.programs = [["not", "a", "row"]] * 5
    run_ip(tmp_path, fake, IP_BASE + ["--duration-s", "1"] + extra)
    (p,) = tab(tmp_path, "incentive_polls")
    assert p["ok"] is False and p["end_reason"] == reason and p["n_rows"] == 0
    assert not (tmp_path / "incentive_programs").exists()  # nothing half-read was stored
    if mode == "same_cursor":
        assert p["n_pages"] == 2 and p["n_rows_discarded"] == 200
    if mode == "max_pages":
        assert p["n_pages"] == 2 and p["n_rows_discarded"] == 200 and len(fake.ip_calls) == 2
    end = ip_runs(tmp_path, "run_end")[0]
    assert end["stop_reason"] == "duration" and end["complete"] is True  # a bad poll is data, not a crash


def test_incentive_explicit_null_list_is_an_empty_page(tmp_path):
    fake = IncentiveFake(n=0)
    fake.programs = []
    run_ip(tmp_path, fake, IP_BASE + ["--duration-s", "1"])
    (p,) = tab(tmp_path, "incentive_polls")
    assert p["ok"] and p["n_rows"] == 0 and p["n_pages"] == 1
    assert not (tmp_path / "incentive_programs").exists()


def test_incentive_status_and_type_flags_are_sent_and_empty_means_omitted(tmp_path):
    fake = IncentiveFake(n=5)
    run_ip(tmp_path / "a", fake, IP_BASE + ["--duration-s", "1", "--status", "paid_out"])
    assert fake.ip_calls[0]["status"] == ["paid_out"] and fake.ip_calls[0]["type"] == ["liquidity"]
    fake2 = IncentiveFake(n=5)
    run_ip(tmp_path / "b", fake2, IP_BASE + ["--duration-s", "1", "--status", "", "--type", ""])
    assert "status" not in fake2.ip_calls[0] and "type" not in fake2.ip_calls[0]
    start = ip_runs(tmp_path / "b", "run_start")[0]
    assert json.loads(start["params_json"]) == {"limit": 100}
    start = ip_runs(tmp_path / "a", "run_start")[0]
    assert json.loads(start["params_json"]) == {"limit": 100, "status": "paid_out", "type": "liquidity"}


def test_incentive_exclusive_lock_exit_5_counts_only_and_pid(tmp_path):
    logs = []
    held = kp.acquire_lock(tmp_path)
    try:
        assert (tmp_path / ".orderbook.lock").read_text().strip() == str(os.getpid())
        fake = IncentiveFake(n=5)
        with pytest.raises(SystemExit) as e:
            run_ip(tmp_path, fake, IP_BASE + ["--duration-s", "10"], log=logs.append)
        assert e.value.code == 5
        assert fake.calls == [] and not (tmp_path / "incentive_runs").exists()  # before run_start
        assert not any(str(tmp_path) in m for m in logs)
    finally:
        held.close()
    run_ip(tmp_path, IncentiveFake(n=5), IP_BASE + ["--duration-s", "1"])  # free again


def test_incentive_lock_blocks_an_orderbook_recorder_on_the_same_out(tmp_path):
    held = kp.acquire_lock(tmp_path)  # what a running recorder of either kind holds
    try:
        with pytest.raises(SystemExit) as e:
            run_series(tmp_path, OpenFake(), ["--series", "KXFIN", "--duration-s", "10"])
        assert e.value.code == 5
    finally:
        held.close()
    run_ip(tmp_path, IncentiveFake(n=5), IP_BASE + ["--duration-s", "1"])  # free again


def test_incentive_run_start_on_disk_before_first_request_and_run_end_fields(tmp_path):
    fake = IncentiveFake(n=150)
    run_ip(tmp_path, fake, IP_BASE + ["--duration-s", "1"], rate=7.0)
    assert fake.runs_files_at_first_page == 1  # run_start already on disk at the first page
    (start,) = ip_runs(tmp_path, "run_start")
    (end,) = ip_runs(tmp_path, "run_end")
    assert "git_sha" in start and end["run_id"] == start["run_id"]
    assert (start["every_s"], start["page_limit"], start["max_pages"], start["rate"]) == \
        (900.0, 100, 1000, pytest.approx(7.0))
    assert end["stop_reason"] == "duration" and end["complete"] is True and end["note"] == ""
    assert (end["n_polls"], end["n_polls_ok"], end["n_rows"], end["requests"]) == (1, 1, 150, 2)


def test_incentive_run_end_chain_head_and_seal_logging(tmp_path):
    logs = []
    run_ip(tmp_path, IncentiveFake(n=120), IP_BASE + ["--duration-s", "4000"], log=logs.append)
    seals = [m for m in logs if m.startswith("seal:")]
    assert len(seals) >= 2  # crossed an hour boundary, plus the final seal
    heads = [m.split("chain_head=")[1] for m in seals]
    assert heads[-1] == kp.chain_head(tmp_path)
    assert all(len(h) == 64 for h in heads) and all("KXRT" not in m for m in seals)
    (end,) = ip_runs(tmp_path, "run_end")
    lines = (tmp_path / kp.CHAIN_NAME).read_text().splitlines()
    # run_end is sealed in the last chain line, so its head is the head of the line before it
    assert end["chain_head"] == hashlib.sha256(lines[-2].encode()).hexdigest()
    assert end["chain_head"] in heads
    assert stat.S_IMODE((tmp_path / kp.CHAIN_NAME).stat().st_mode) == 0o440
    recs = [json.loads(x) for x in lines]
    assert recs[0]["prev"] == "0" * 64 and len({r["hour"] for r in recs}) >= 2
    entries = kp.verify_parts(tmp_path)
    assert {k.split("/")[0] for k in entries} == {"incentive_programs", "incentive_polls", "incentive_runs"}
    assert kp.read_verified(tmp_path, "incentive_programs").num_rows == 5 * 120


def test_incentive_keyboard_interrupt_writes_interrupt_and_incomplete(tmp_path):
    def boom(clk):
        if clk.t >= 1070:
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        run_ip(tmp_path, IncentiveFake(n=120), IP_BASE + ["--duration-s", "100000"], on_sleep=boom)
    (end,) = ip_runs(tmp_path, "run_end")
    assert end["stop_reason"] == "interrupt" and end["complete"] is False
    assert end["n_polls"] == 1 and end["n_rows"] == 120  # the finished poll was kept
    assert (tmp_path / ".orderbook.lock").read_text().strip() != ""  # pid file remains
    kp.acquire_lock(tmp_path).close()  # lock released on the way out
    kp.verify_parts(tmp_path)  # everything written was sealed
    with pytest.raises(kp.SealError, match="complete"):
        kp.read_verified(tmp_path, "incentive_programs")
    assert kp.read_verified(tmp_path, "incentive_programs", require_complete_runs=False).num_rows == 120


def test_incentive_sigterm_between_polls_is_labelled_and_handler_restored(tmp_path):
    sent = []

    def term(clk):
        if clk.t >= 1070 and not sent:
            sent.append(1)
            os.kill(os.getpid(), signal.SIGTERM)
    prev = signal.getsignal(signal.SIGTERM)
    _, clk = run_ip(tmp_path, IncentiveFake(n=120), IP_BASE + ["--duration-s", "100000"], on_sleep=term)
    assert sent and clk.t < 1300 and signal.getsignal(signal.SIGTERM) is prev
    (end,) = ip_runs(tmp_path, "run_end")
    assert end["stop_reason"] == "sigterm" and end["complete"] is False and end["n_rows"] == 120


def test_incentive_sigterm_mid_poll_discards_that_poll_and_records_it(tmp_path):
    fake = IncentiveFake(n=300)
    fake.on_page = lambda n: os.kill(os.getpid(), signal.SIGTERM) if n == 2 else None
    run_ip(tmp_path, fake, IP_BASE + ["--duration-s", "100000"])
    (p,) = tab(tmp_path, "incentive_polls")
    assert (p["ok"], p["end_reason"], p["n_pages"], p["n_rows"], p["n_rows_discarded"]) == \
        (False, "stopped", 2, 0, 200)
    assert len(fake.ip_calls) == 2 and not (tmp_path / "incentive_programs").exists()
    (end,) = ip_runs(tmp_path, "run_end")
    assert end["stop_reason"] == "sigterm" and end["complete"] is False


def test_incentive_max_requests_labelled_and_partial_poll_not_stored(tmp_path):
    with pytest.raises(kp.MaxRequestsReached):
        run_ip(tmp_path, IncentiveFake(n=500), IP_BASE + ["--duration-s", "100000"], max_requests=3)
    (end,) = ip_runs(tmp_path, "run_end")
    assert end["stop_reason"] == "max_requests" and end["complete"] is False
    assert end["requests"] == 3 and end["n_rows"] == 0
    assert not (tmp_path / "incentive_programs").exists()
    kp.verify_parts(tmp_path)


def test_incentive_unexpected_error_labelled_error(tmp_path):
    fake = IncentiveFake(n=120)

    def bad(n):
        raise RuntimeError("boom")
    fake.on_page = bad
    with pytest.raises(RuntimeError):
        run_ip(tmp_path, fake, IP_BASE + ["--duration-s", "100"])
    (end,) = ip_runs(tmp_path, "run_end")
    assert end["stop_reason"] == "error" and end["note"] == "RuntimeError" and end["complete"] is False


def test_incentive_verify_and_quarantine_cover_incentive_parts(tmp_path):
    import shutil
    run_ip(tmp_path, IncentiveFake(n=120), IP_BASE + ["--duration-s", "1"])
    kp.verify_parts(tmp_path)
    part = sorted((tmp_path / "incentive_programs").rglob("*.parquet"))[0]
    extra = part.with_name("part-extra.parquet")
    shutil.copy(part, extra)
    with pytest.raises(kp.SealError, match="not covered"):
        kp.verify_parts(tmp_path)
    good = part.read_bytes()
    part.write_bytes(good + b"x")
    with pytest.raises(kp.SealError, match="mismatch"):
        kp.read_verified(tmp_path, "incentive_programs")
    part.write_bytes(good)
    chain_before = (tmp_path / kp.CHAIN_NAME).read_bytes()
    assert kp.main(["--out", str(tmp_path), "quarantine-unsealed"]) == 0
    assert not extra.exists() and part.read_bytes() == good
    assert (tmp_path / kp.CHAIN_NAME).read_bytes() == chain_before
    kp.verify_parts(tmp_path)


def test_incentive_main_exit_zero_manifest_and_query_keys(tmp_path):
    fake = IncentiveFake(n=130)
    rc, client = run(tmp_path, ["incentive-snap", "--duration-s", "1"], fake)
    assert rc == 0 and client.requests_made == 2
    recs = [json.loads(l) for l in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert recs[-1]["type"] == "run" and recs[-1]["complete"] is True
    q = recs[-1]["query"]
    assert (q["every_s"], q["duration_s"], q["status"], q["incentive_type"], q["page_limit"],
            q["max_pages"]) == (900, 1, "active", "liquidity", 100, 1000)
    assert fake.ip_calls[0]["limit"] == ["100"]
    # a second copy on the same --out while one holds the lock exits 5 through main()
    held = kp.acquire_lock(tmp_path)
    try:
        with pytest.raises(SystemExit) as e:
            run(tmp_path, ["incentive-snap", "--duration-s", "1"], IncentiveFake(n=5))
        assert e.value.code == 5
    finally:
        held.close()


def test_incentive_refuses_non_positive_arguments(tmp_path):
    for bad in (["--every-s", "0"], ["--duration-s", "0"], ["--page-limit", "0"], ["--max-pages", "0"]):
        fake = IncentiveFake(n=5)
        argv = ["incentive-snap", "--duration-s", "10"] + bad
        argv = argv if bad[0] != "--duration-s" else ["incentive-snap"] + bad
        with pytest.raises(SystemExit):
            run_ip(tmp_path, fake, argv)
        assert fake.calls == [] and not (tmp_path / "incentive_runs").exists()


def test_incentive_never_sends_credentials_or_non_get(tmp_path):
    seen = []

    def http(url):
        seen.append(url)
        return 200, json.dumps({"incentive_programs": [], "next_cursor": ""}).encode()
    client = kp.Client(http=http, rate=1000)
    client.get("/incentive_programs", {"status": "active"})
    assert seen[0].startswith(kp.BASE_URL + "/incentive_programs?") and "key" not in seen[0].lower()


# ------------------------------------------------------------ trades --tail-s

TAIL_SINCE = "2026-10-01T00:00:00Z"
TAIL_UNTIL = "2026-10-02T00:00:00Z"
TAIL_ARGS = ["trades", "--series", "KXBTC15M", "--since", TAIL_SINCE, "--until", TAIL_UNTIL,
             "--tail-s", "600"]
FULL_ARGS = ["trades", "--series", "KXBTC15M", "--since", TAIL_SINCE, "--until", TAIL_UNTIL]


def u(iso):
    return kp.to_unix(iso)


def tmk(t, close, settle):
    m = Fake.mk(t, settle)
    m["close_time"] = close
    return m


def ttr(ticker, tid, iso):
    return {"trade_id": tid, "ticker": ticker, "count_fp": "5.00", "yes_price_dollars": "0.9000",
            "no_price_dollars": "0.1000", "taker_outcome_side": "yes", "taker_book_side": "bid",
            "created_time": iso, "is_block_trade": False}


# ticker -> (close_time, settlement_ts)
TA = "KXBTC15M-26OCT010005-05"   # closes 00:05, so its tail starts BEFORE --since
TB = "KXBTC15M-26OCT011200-00"   # settles 3.5 h after close
TC = "KXBTC15M-26OCT012345-45"   # closes before --until, settles after it
TD = "KXBTC15M-26OCT020000-00"   # closes exactly at --until: excluded
TE = "KXBTC15M-26SEP302345-45"   # closes before --since, settles after it: excluded
TF = "KXBTC15M-26OCT010600-00"   # no close_time: skipped
TAIL_MARKETS = {
    TA: ("2026-10-01T00:05:00Z", "2026-10-01T00:05:07Z"),
    TB: ("2026-10-01T12:00:00Z", "2026-10-01T15:30:00Z"),
    TC: ("2026-10-01T23:45:00Z", "2026-10-02T05:00:00Z"),
    TD: ("2026-10-02T00:00:00Z", "2026-10-02T00:00:09Z"),
    TE: ("2026-09-30T23:45:00Z", "2026-10-01T00:00:05Z"),
    TF: (None, "2026-10-01T06:00:09Z"),
}


def offsets(ticker, close_iso):
    """Trades 601 s, 600 s, 300 s, 1 s, 0 s before close and 1 s after."""
    c = u(close_iso)
    return [ttr(ticker, f"{ticker}:{d}", kp.parse_dt(c - d).strftime("%Y-%m-%dT%H:%M:%SZ"))
            for d in (601, 600, 300, 1, 0, -1)]


class TailFake(Fake):
    """Crypto 15M series; /markets/trades honours ticker, min_ts, max_ts, limit, cursor and records
    every trades query.  The real endpoint's bound semantics are not verified, so both are
    modelled: ``strict=False`` bounds are inclusive, ``strict=True`` exclusive on both sides."""

    def __init__(self, markets=TAIL_MARKETS, strict=False):
        super().__init__()
        self.strict = strict
        self.series.append({"ticker": "KXBTC15M", "title": "BTC 15 min", "category": "Crypto",
                            "categories": ["Crypto"], "tags": [], "frequency": "fifteen_min",
                            "fee_type": "quadratic", "fee_multiplier": 1.0, "settlement_sources": [],
                            "contract_url": "u", "contract_terms_url": "t"})
        self.live_markets = {"KXBTC15M": [tmk(t, c, s) for t, (c, s) in markets.items()]}
        self.hist_markets = {}
        self.trades = {t: offsets(t, c) for t, (c, _s) in markets.items() if c}
        self.hist_trades = {}
        self.trade_queries = []

    def __call__(self, url):
        u_ = urllib.parse.urlparse(url)
        path = u_.path.replace("/trade-api/v2", "")
        if path not in ("/markets/trades", "/historical/trades"):
            return super().__call__(url)
        self.calls.append(url)
        if self.status_script:
            st = self.status_script.pop(0)
            if st != 200:
                return st, b"{}"
        q = urllib.parse.parse_qs(u_.query)
        self.trade_queries.append((path, {k: v[0] for k, v in q.items()}))
        lo = int(q["min_ts"][0]) if "min_ts" in q else 0
        hi = int(q["max_ts"][0]) if "max_ts" in q else 10 ** 12
        src = self.trades if path == "/markets/trades" else self.hist_trades
        ts = lambda t: kp.parse_dt(t["created_time"]).timestamp()
        ok = (lambda t: lo < ts(t) < hi) if self.strict else (lambda t: lo <= ts(t) <= hi)
        items = [t for t in src.get(q["ticker"][0], []) if ok(t)]
        return 200, json.dumps(self.page(items, "trades", q)).encode()

    def asked(self):
        return [q["ticker"] for _p, q in self.trade_queries]


def test_tail_window_helper():
    c = u("2026-10-01T00:15:00Z")
    # request bounds: padded by 1 s per side so exclusive API bounds cannot lose an edge print
    assert kp.tail_window("2026-10-01T00:15:00Z", 600) == (c - 601, c + 1)
    assert kp.tail_window(str(c), 60) == (c - 61, c + 1)  # epoch seconds as text
    # fractional close: floor / ceil first, then the pad
    assert kp.tail_window("2026-10-01T00:15:00.500000000Z", 600) == (c - 601, c + 2)
    assert kp.tail_window(None, 600) is None and kp.tail_window("", 600) is None
    # the stored window is exact and inclusive at both ends
    lo, hi = kp.tail_keep("2026-10-01T00:15:00Z", 600)
    assert (lo.timestamp(), hi.timestamp()) == (c - 600, c) and lo.tzinfo is not None
    lo, hi = kp.tail_keep("2026-10-01T00:15:00.500000000Z", 600)
    assert (lo.timestamp(), hi.timestamp()) == (c - 599.5, c + 0.5)
    assert kp.tail_keep(None, 600) is None


def test_tail_sends_window_params_from_each_markets_own_close_time(tmp_path):
    fake = TailFake()
    rc, _ = run(tmp_path, TAIL_ARGS, fake)
    assert rc == 0
    assert len(fake.trade_queries) == 3  # one page per market
    for path, q in fake.trade_queries:
        close, _settle = TAIL_MARKETS[q["ticker"]]
        assert path == "/markets/trades"  # October is after the (June) cutoff: live tier only
        assert int(q["max_ts"]) == u(close) + 1  # padded 1 s per side (bound semantics unverified)
        assert int(q["min_ts"]) == u(close) - 600 - 1
        assert q["limit"] == str(kp.PAGE_LIMIT) and "cursor" not in q
    # TB settles 3.5 h after close: the window is still close-based, never settlement_ts-based
    qb = [q for _p, q in fake.trade_queries if q["ticker"] == TB][0]
    assert (int(qb["min_ts"]), int(qb["max_ts"])) == (u("2026-10-01T11:50:00Z") - 1,
                                                      u("2026-10-01T12:00:00Z") + 1)
    # TA's window opens before --since and is not clipped to it
    qa = [q for _p, q in fake.trade_queries if q["ticker"] == TA][0]
    assert int(qa["min_ts"]) == u("2026-09-30T23:55:00Z") - 1 < u(TAIL_SINCE)
    # the padded request also returned the prints at close-601 s and close+1 s; they are cut
    # client-side, so the stored rows are exactly [close-600, close], across the date partition
    got = {r["trade_id"] for r in read(tmp_path, "trades")}
    want = {f"{t}:{d}" for t in (TA, TB, TC) for d in (600, 300, 1, 0)}
    assert got == want
    assert {r["source"] for r in read(tmp_path, "trades")} == {"live"}
    assert {p.name for p in (tmp_path / "trades" / "series=KXBTC15M").iterdir()} == {
        "date=2026-09-30", "date=2026-10-01"}  # TA's tail starts on the previous UTC day


def test_tail_selection_is_close_time_in_since_until_half_open(tmp_path, capsys):
    fake = TailFake()
    rc, _ = run(tmp_path, TAIL_ARGS, fake)
    assert rc == 0
    assert sorted(fake.asked()) == sorted([TA, TB, TC])  # not TD (== until), TE (before since), TF
    assert TD not in "".join(fake.calls) and TE not in "".join(fake.calls)
    err = capsys.readouterr().err
    assert f"skip {TF}: no close_time" in err
    # the listing itself is the existing settled listing: min_settled_ts = since, no upper bound
    listing = [urllib.parse.parse_qs(urllib.parse.urlparse(c).query) for c in fake.calls
               if urllib.parse.urlparse(c).path.endswith("/markets")]
    assert len(listing) == 1 and listing[0]["min_settled_ts"] == [str(u(TAIL_SINCE))]
    assert "max_settled_ts" not in listing[0]


def test_tail_straddling_the_cutoff_splits_across_tiers(tmp_path):
    close = "2026-06-01T00:01:40Z"  # 100 s after the cutoff: window [close-600, close] spans it
    fake = TailFake({TA: (close, "2026-06-01T00:01:50Z")})
    fake.hist_trades = {TA: [ttr(TA, "h1", "2026-05-31T23:55:00Z")]}
    rc, _ = run(tmp_path, ["trades", "--series", "KXBTC15M", "--since", "2026-05-31T00:00:00Z",
                           "--tail-s", "600"], fake)
    assert rc == 0
    by_path = dict(fake.trade_queries)
    assert (int(by_path["/historical/trades"]["min_ts"]), int(by_path["/historical/trades"]["max_ts"])) == (
        u(close) - 601, CUTOFF_TS)
    assert (int(by_path["/markets/trades"]["min_ts"]), int(by_path["/markets/trades"]["max_ts"])) == (
        CUTOFF_TS, u(close) + 1)
    assert {r["source"] for r in read(tmp_path, "trades")} == {"historical", "live"}


def test_tail_pages_follow_cursor_inside_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr(kp, "PAGE_LIMIT", 2)
    fake = TailFake({TA: TAIL_MARKETS[TA]})
    rc, client = run(tmp_path, TAIL_ARGS, fake)
    assert rc == 0
    qs = [q for _p, q in fake.trade_queries]
    assert len(qs) == 3  # 6 trades in the padded request, 2 per page; last page has an empty cursor
    assert "cursor" not in qs[0] and "cursor" in qs[1] and "cursor" in qs[2]
    assert len({(q["min_ts"], q["max_ts"]) for q in qs}) == 1  # same window on every page
    unit = [m for m in manifest(tmp_path) if m["type"] == "unit"][0]
    assert unit["rows"] == 4 and unit["requests"] == 3 and unit["tail_s"] == 600
    assert unit["fetched"] == 6  # the padded request also returned close-601 s and close+1 s
    assert (unit["min_ts"], unit["max_ts"]) == (u(TAIL_MARKETS[TA][0]) - 601, u(TAIL_MARKETS[TA][0]) + 1)


def test_tail_s_is_recorded_in_the_manifest_and_full_runs_do_not_carry_it(tmp_path):
    run(tmp_path / "tail", TAIL_ARGS, TailFake())
    recs = manifest(tmp_path / "tail")
    for typ in ("file", "run"):
        sel = [r for r in recs if r["type"] == typ]
        assert sel and all(r["query"]["tail_s"] == 600 for r in sel)
    assert [r for r in recs if r["type"] == "run"][-1]["complete"] is True
    ck = json.loads(next((tmp_path / "tail" / "_checkpoints").glob("trades-*.json")).read_text())
    assert ck["query"]["tail_s"] == 600
    # a full run: no tail_s key anywhere, so its checkpoint name is what it was before this option
    run(tmp_path / "full", FULL_ARGS, TailFake())
    recs = manifest(tmp_path / "full")
    assert all("tail_s" not in r["query"] for r in recs if r["type"] in ("file", "run"))
    legacy = {"allow_series": None, "categories": None, "market": None, "series": "KXBTC15M",
              "since": TAIL_SINCE, "until": TAIL_UNTIL}
    qh = hashlib.sha256(kp.jdump({"c": "trades", "q": legacy}).encode()).hexdigest()[:16]
    assert (tmp_path / "full" / "_checkpoints" / f"trades-{qh}.json").exists()
    # and the name an in-flight full run already has on mal-fast-0 is still produced for its query
    host_q = {"allow_series": None, "categories": None, "market": None,
              "series": "KXBTCD,KXETHD,KXSOLD,KXBTC,KXETH,KXSOL,KXBTC15M,KXETH15M,KXSOL15M,KXXRPD,KXDOGED",
              "since": "2026-10-01T00:00:00Z", "until": None}
    assert kp.Store(tmp_path / "h", "trades", host_q).ckpt_path.name == "trades-6a363748d3082949.json"


def test_tail_and_full_runs_never_share_checkpoint_or_part_files(tmp_path):
    full_fake = TailFake()
    rc, _ = run(tmp_path, FULL_ARGS, full_fake)
    assert rc == 0
    full_parts = {p: kp.sha256_file(p) for p in (tmp_path / "trades").rglob("*.parquet")}
    full_rows = len(read(tmp_path, "trades"))
    # a tail run over the same tickers is a fresh run: it does not inherit the full run's done set
    tail_fake = TailFake()
    rc, _ = run(tmp_path, TAIL_ARGS, tail_fake)
    assert rc == 0 and sorted(tail_fake.asked()) == sorted([TA, TB, TC])
    assert len(list((tmp_path / "_checkpoints").glob("trades-*.json"))) == 2
    # the full run's part files are untouched; the tail wrote its own
    assert all(p.exists() and kp.sha256_file(p) == h for p, h in full_parts.items())
    now_parts = set((tmp_path / "trades").rglob("*.parquet"))
    assert now_parts > set(full_parts)
    assert len(read(tmp_path, "trades")) > full_rows  # overlap by trade_id: readers dedupe
    # and the other way round, on a fresh store
    d = tmp_path / "rev"
    run(d, TAIL_ARGS, TailFake())
    full2 = TailFake()
    rc, _ = run(d, FULL_ARGS, full2)
    assert rc == 0 and set(full2.asked()) == {TA, TB, TC, TD, TE, TF}  # tail's done set not inherited


def test_tail_refuses_a_full_checkpoint_and_a_full_run_refuses_a_tail_checkpoint(tmp_path, capsys):
    run(tmp_path, FULL_ARGS, TailFake())
    full_ck = next((tmp_path / "_checkpoints").glob("trades-*.json"))
    rc, _ = run(tmp_path, [*TAIL_ARGS], TailFake(), max_requests=3)  # leaves a tail checkpoint
    assert rc == 3
    tail_ck = [p for p in (tmp_path / "_checkpoints").glob("trades-*.json") if p != full_ck][0]
    full_text, tail_text = full_ck.read_text(), tail_ck.read_text()
    n_manifest = len((tmp_path / "manifest.jsonl").read_text().splitlines())
    capsys.readouterr()
    # full-run checkpoint content under the tail checkpoint's name
    tail_ck.write_text(full_text)
    fake = TailFake()
    rc, client = run(tmp_path, TAIL_ARGS, fake)
    assert rc == 4 and fake.calls == [] and client.requests_made == 0
    err = capsys.readouterr().err
    assert "refused" in err and "tail_s=None there, 600 now" in err
    assert tail_ck.read_text() == full_text  # not rewritten
    # tail-run checkpoint content under the full run's name
    tail_ck.write_text(tail_text)
    full_ck.write_text(tail_text)
    fake = TailFake()
    rc, client = run(tmp_path, FULL_ARGS, fake)
    assert rc == 4 and fake.calls == [] and client.requests_made == 0
    assert "tail_s=600 there, None now" in capsys.readouterr().err
    assert len((tmp_path / "manifest.jsonl").read_text().splitlines()) == n_manifest  # no run record


def test_tail_checkpoint_resumes_only_its_own_tail_run(tmp_path):
    # hand-written checkpoint of the SAME tail query is honoured (control for the refusals above)
    rc, _ = run(tmp_path, TAIL_ARGS, TailFake(), max_requests=3)  # series, cutoff, listing: no market done
    assert rc == 3
    ck = next((tmp_path / "_checkpoints").glob("trades-*.json"))
    d = json.loads(ck.read_text())
    assert d["done"] == [] and d["query"]["tail_s"] == 600
    d["done"] = [TA]
    ck.write_text(json.dumps(d))
    fake = TailFake()
    rc, _ = run(tmp_path, TAIL_ARGS, fake)
    assert rc == 0 and sorted(fake.asked()) == sorted([TB, TC])
    # a tail run with a different N is a different run: its own checkpoint, TA fetched again
    fake = TailFake()
    rc, _ = run(tmp_path, [*TAIL_ARGS[:-1], "900"], fake)
    assert rc == 0 and TA in fake.asked() and len(list((tmp_path / "_checkpoints").glob("trades-*"))) == 2


def test_tail_max_requests_is_respected_and_resume_skips_done_markets(tmp_path):
    # requests: /series, /historical/cutoff, markets listing (1 page), then 1 trades page per market
    rc, c1 = run(tmp_path, TAIL_ARGS, f1 := TailFake(), max_requests=5)
    assert rc == 3 and c1.requests_made == 5
    assert f1.asked() == [TA, TB]  # the 6th request (TC) was never sent
    ck = json.loads(next((tmp_path / "_checkpoints").glob("trades-*.json")).read_text())
    assert ck["done"] == [TA, TB]
    last = manifest(tmp_path)[-1]
    assert last["type"] == "run" and last["complete"] is False and last["requests"] == 5
    assert last["query"]["tail_s"] == 600
    f2 = TailFake()
    rc, c2 = run(tmp_path, TAIL_ARGS, f2, max_requests=4)  # exactly enough for series+cutoff+list+TC
    assert rc == 0 and c2.requests_made == 4 and f2.asked() == [TC]
    assert {r["ticker"] for r in read(tmp_path, "trades")} == {TA, TB, TC}
    rc, c3 = run(tmp_path, TAIL_ARGS, f3 := TailFake())  # everything done: no trades request
    assert rc == 0 and f3.asked() == []


def test_tail_retries_count_against_max_requests(tmp_path):
    fake = TailFake({TA: TAIL_MARKETS[TA]})
    rc, client = run(tmp_path, TAIL_ARGS, fake, max_requests=5)
    assert rc == 0 and client.requests_made == 4
    fake = TailFake({TA: TAIL_MARKETS[TA]})
    fake.status_script = [200, 200, 200, 429, 429]  # trades page 429 twice, then the cap hits
    rc, client = run(tmp_path / "b", TAIL_ARGS, fake, max_requests=5)
    assert rc == 3 and client.requests_made == 5


def test_tail_s_argument_validation_sends_nothing(tmp_path):
    for argv in (["trades", "--series", "KXBTC15M", "--tail-s", "0"],
                 ["trades", "--series", "KXBTC15M", "--tail-s", "-5"],
                 ["trades", "--market", TA, "--tail-s", "600"],
                 ["candles", "--series", "KXBTC15M", "--since", TAIL_SINCE, "--tail-s", "600"],
                 ["markets", "--series", "KXBTC15M", "--tail-s", "600"],
                 ["trades", "--series", "KXBTC15M", "--tail-s", "ten"]):
        fake = TailFake()
        with pytest.raises(SystemExit):
            run(tmp_path, argv, fake)
        assert fake.calls == [], argv


def test_tail_stores_the_same_rows_whether_api_bounds_are_inclusive_or_exclusive(tmp_path):
    out = {}
    for name, strict in (("incl", False), ("excl", True)):
        fake = TailFake(strict=strict)
        rc, _ = run(tmp_path / name, TAIL_ARGS, fake)
        assert rc == 0
        out[name] = {r["trade_id"] for r in read(tmp_path / name, "trades")}
        for _p, q in fake.trade_queries:  # the request is padded either way
            close = TAIL_MARKETS[q["ticker"]][0]
            assert (int(q["min_ts"]), int(q["max_ts"])) == (u(close) - 601, u(close) + 1)
        units = [m for m in manifest(tmp_path / name) if m["type"] == "unit"]
        assert [m["rows"] for m in units] == [4, 4, 4]
        # inclusive API also returns close-601 s and close+1 s (cut client-side); exclusive does not
        assert [m["fetched"] for m in units] == ([6, 6, 6] if not strict else [4, 4, 4])
    want = {f"{t}:{d}" for t in (TA, TB, TC) for d in (600, 300, 1, 0)}
    # with STRICT bounds the prints at exactly close-600 s and close are still stored
    assert out["excl"] == out["incl"] == want


def test_tail_cuts_sub_second_edges_to_the_exact_close_window(tmp_path):
    def iso(x):
        return kp.parse_dt(x).isoformat()

    c = u(TAIL_MARKETS[TA][0])
    fake = TailFake({TA: TAIL_MARKETS[TA]}, strict=True)
    fake.trades[TA] = [ttr(TA, "in_lo", iso(c - 599.5)), ttr(TA, "out_lo", iso(c - 600.5)),
                       ttr(TA, "in_hi", iso(c - 0.5)), ttr(TA, "out_hi", iso(c + 0.5))]
    rc, _ = run(tmp_path / "whole", TAIL_ARGS, fake)
    assert rc == 0 and {r["trade_id"] for r in read(tmp_path / "whole", "trades")} == {"in_lo", "in_hi"}
    # fractional close c+0.5: window is [c-599.5, c+0.5], both ends inclusive
    frac = {TA: ("2026-10-01T00:05:00.500000Z", "2026-10-01T00:05:07Z")}
    for strict in (False, True):
        d = tmp_path / f"frac{strict}"
        fake = TailFake(frac, strict=strict)
        fake.trades[TA] = [ttr(TA, "edge_lo", iso(c - 599.5)), ttr(TA, "before", iso(c - 599.6)),
                           ttr(TA, "edge_hi", iso(c + 0.5)), ttr(TA, "after", iso(c + 0.6))]
        rc, _ = run(d, TAIL_ARGS, fake)
        assert rc == 0 and {r["trade_id"] for r in read(d, "trades")} == {"edge_lo", "edge_hi"}
        q = fake.trade_queries[0][1]
        assert (int(q["min_ts"]), int(q["max_ts"])) == (c - 601, c + 2)
