"""Tests for tools/kalshi_public.py with a fake HTTP layer (no network)."""
import hashlib
import json
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
            assert q["status"] == ["settled"] and q["mve_filter"] == ["exclude"]
            return j(self.page(items, "markets", q))
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
                                                "mve_filter": "exclude", "limit": 3}, "markets"))
    assert len(got) == 7 and len(fake.calls) == 3
    assert "cursor=3" in fake.calls[1] and "cursor=6" in fake.calls[2]


def test_pagination_stops_on_repeated_cursor():
    pages = iter([{"markets": [1], "cursor": "a"}, {"markets": [2], "cursor": "a"}])
    client = kp.Client(http=lambda u: (200, json.dumps(next(pages)).encode()), rate=1000)
    assert list(kp.paginate(client, "/markets", {}, "markets")) == [1, 2]


# ------------------------------------------------------------ commands, schema, manifest

def test_markets_schema_partitions_routing_and_manifest(tmp_path):
    fake = Fake()
    rc, _ = run(tmp_path, ["markets", "--series", "KXHIGHNY"], fake)
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
    assert set(sub.choices) == {"series", "markets", "trades", "candles", "orderbook-snap"}
