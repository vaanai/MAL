#!/usr/bin/env python3
"""Read-only downloader for Kalshi's PUBLIC, unauthenticated market data.

Paper research only.  No API key, no authenticated endpoint, no order /
portfolio / account endpoint.  Only the GET paths in ``ALLOWED_GET`` can be
requested; anything else raises ``ForbiddenPath`` before a socket is opened.

Base URL (production, "Production Trade API server", from the OpenAPI
``servers`` block of every page below, read 2026-10-17):
    https://external-api.kalshi.com/trade-api/v2

Endpoints used (all read 2026-10-17):
  GET /series                       https://docs.kalshi.com/api-reference/market/get-series-list.md
  GET /markets                      https://docs.kalshi.com/api-reference/market/get-markets.md
       (status=settled, series_ticker, min/max_settled_ts, mve_filter, cursor, limit<=1000)
  GET /markets/trades               https://docs.kalshi.com/api-reference/market/get-trades.md
       (ticker, min_ts, max_ts, cursor, limit<=1000)
  GET /series/{s}/markets/{t}/candlesticks
                                    https://docs.kalshi.com/api-reference/market/get-market-candlesticks.md
       (start_ts, end_ts, period_interval in {1,60,1440})
  GET /markets/{ticker}/orderbook   https://docs.kalshi.com/api-reference/market/get-market-orderbook.md
       (depth 0..100; yes/no BIDS only, [price_dollars, count_fp])
  GET /historical/cutoff            https://docs.kalshi.com/api-reference/historical/get-historical-cutoff-timestamps.md
  GET /historical/markets           https://docs.kalshi.com/api-reference/historical/get-historical-markets.md
       (series_ticker, mve_filter, cursor, limit; NO time filter, we filter settlement_ts client side)
  GET /historical/trades            https://docs.kalshi.com/api-reference/historical/get-historical-trades.md
  GET /historical/markets/{t}/candlesticks
                                    https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks.md
Routing: https://docs.kalshi.com/getting_started/historical_data.md (markets settled
before ``market_settled_ts`` and trades before ``trades_created_ts`` are ONLY on
/historical/*; newer ones only on the live paths).  Pagination (cursor, empty
cursor = last page): https://docs.kalshi.com/getting_started/pagination.md

Rate limits: https://docs.kalshi.com/getting_started/rate_limits.md documents
token budgets per authenticated tier (default cost 10 tokens/request) and says
429 has no Retry-After and to "apply exponential backoff on 429".  It does NOT
document a limit for unauthenticated callers, so the default here is a
conservative client-side 5 req/s (``--rate``), exponential backoff on 429, 5xx
and network errors, and a hard ``--max-requests`` cap.

Live shape check, 2026-10-17, exactly three unauthenticated GETs, no data saved:
  /markets?status=open&limit=1 (200, used a first time to see the 'cursor'
  field), /markets?status=open&limit=1&mve_filter=exclude (200; note status is
  reported as 'active', first ticker is a combo market unless mve_filter=exclude),
  /markets/<ticker>/orderbook?depth=5 (200 without credentials).

Conventions / caveats:
  * The series of a market ticker is the part before the first '-'.
  * Combo (multivariate) markets are excluded (mve_filter=exclude).
  * Sports is excluded by default (``--categories`` to choose another set; a
    series is excluded when its primary category OR any discovery category
    equals an excluded one; series whose category cannot be determined are
    skipped, never assumed allowed).
  * Trades: taker side is stored as ``taker_outcome_side`` (falls back to the
    deprecated ``taker_side``).  Prices are dollars as floats.
  * Resumability is per unit (series for markets, ticker for trades/candles).
    A unit is marked done only after its files are written.  The manifest is an
    append-only log; for a path that appears twice take the last line.
  * The orderbook observer is a forward sampler: each run starts a new set of
    part files; an empty book yields a row in ``orderbook_snaps`` (n levels 0)
    and no rows in ``orderbook``.  Latency/skew of each sample is the HTTP
    round trip, which is not recorded beyond ``ts``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_OUT = "/data/mal/kalshi"
DEFAULT_RATE = 5.0
PAGE_LIMIT = 1000
CANDLE_CHUNK = 1000  # candles per request (the docs give no cap, stay modest)

# The ONLY paths this module may GET.  Anything else is refused.
ALLOWED_GET = tuple(re.compile(p) for p in (
    r"^/series$",
    r"^/markets$",
    r"^/markets/trades$",
    r"^/markets/[A-Za-z0-9._-]+/orderbook$",
    r"^/series/[A-Za-z0-9._-]+/markets/[A-Za-z0-9._-]+/candlesticks$",
    r"^/historical/cutoff$",
    r"^/historical/markets$",
    r"^/historical/trades$",
    r"^/historical/markets/[A-Za-z0-9._-]+/candlesticks$",
))


class ForbiddenPath(Exception):
    pass


class MaxRequestsReached(Exception):
    pass


class HttpError(Exception):
    def __init__(self, status, body=b""):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body


def check_path(path: str) -> str:
    """Return the path if it is allowlisted, else raise ForbiddenPath."""
    p = path.split("?", 1)[0]
    if ".." in p or not any(rx.match(p) for rx in ALLOWED_GET):
        raise ForbiddenPath(f"path not in the public-data allowlist: {path!r}")
    return p


def urllib_http(url: str, timeout: float = 30.0):
    """GET only, no credentials.  Returns (status, body_bytes); status 0 = network error."""
    req = urllib.request.Request(
        url, method="GET",
        headers={"Accept": "application/json", "User-Agent": "mal-kalshi-public/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, b""


class RateLimiter:
    """Minimum spacing of 1/rate seconds between request starts."""

    def __init__(self, rate: float, clock=time.monotonic, sleep=time.sleep):
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self.interval = 1.0 / rate
        self.clock, self.sleep = clock, sleep
        self._next = None

    def wait(self):
        now = self.clock()
        if self._next is not None and now < self._next:
            self.sleep(self._next - now)
            now = self._next
        self._next = now + self.interval


class Client:
    def __init__(self, http=None, rate=DEFAULT_RATE, max_requests=None, max_retries=6,
                 backoff_base=1.0, backoff_cap=60.0, sleep=time.sleep, clock=time.monotonic,
                 base_url=BASE_URL):
        self.http = http or urllib_http
        self.sleep = sleep
        self.limiter = RateLimiter(rate, clock=clock, sleep=sleep)
        self.max_requests = max_requests
        self.max_retries = max_retries
        self.backoff_base, self.backoff_cap = backoff_base, backoff_cap
        self.base_url = base_url
        self.requests_made = 0  # every HTTP attempt, retries included
        self.backoffs = []

    def get(self, path: str, params: dict | None = None) -> dict:
        path = check_path(path)
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        url = self.base_url + path + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in range(self.max_retries + 1):
            if self.max_requests is not None and self.requests_made >= self.max_requests:
                raise MaxRequestsReached(f"--max-requests {self.max_requests} reached")
            self.limiter.wait()
            self.requests_made += 1
            status, body = self.http(url)
            if status == 200:
                return json.loads(body)
            retryable = status == 429 or status >= 500 or status == 0
            if not retryable:
                raise HttpError(status, body)
            if attempt == self.max_retries:
                raise HttpError(status, body)
            delay = min(self.backoff_cap, self.backoff_base * (2 ** attempt))
            self.backoffs.append(delay)
            self.sleep(delay)
        raise AssertionError("unreachable")


def paginate(client: Client, path: str, params: dict, key: str):
    """Yield items across cursor pages.  Stops on empty/missing cursor or a repeated cursor."""
    params = dict(params)
    params.setdefault("limit", PAGE_LIMIT)
    seen = set()
    while True:
        d = client.get(path, params)
        yield from d.get(key) or []
        cur = d.get("cursor")
        if not cur or cur in seen:
            return
        seen.add(cur)
        params["cursor"] = cur


# ---------------------------------------------------------------- helpers

def parse_dt(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(v).strip()
    if re.fullmatch(r"\d{9,11}", s):
        return datetime.fromtimestamp(int(s), tz=timezone.utc)
    s = s.replace("Z", "+00:00")
    s = re.sub(r"\.(\d{6})\d+", r".\1", s)
    d = datetime.fromisoformat(s)
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def to_unix(v):
    d = parse_dt(v)
    return None if d is None else int(d.timestamp())


def fnum(x):
    if x in (None, ""):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def series_of(ticker: str) -> str:
    return ticker.split("-", 1)[0]


def jdump(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- schemas

TS = pa.timestamp("us", tz="UTC")
SCHEMAS = {
    "series": pa.schema([
        ("ticker", pa.string()), ("title", pa.string()), ("category", pa.string()),
        ("categories", pa.list_(pa.string())), ("tags", pa.list_(pa.string())),
        ("frequency", pa.string()), ("fee_type", pa.string()), ("fee_multiplier", pa.float64()),
        ("settlement_sources_json", pa.string()), ("contract_url", pa.string()),
        ("contract_terms_url", pa.string()), ("fetched_at", TS),
    ]),
    "markets": pa.schema([
        ("ticker", pa.string()), ("event_ticker", pa.string()), ("series_ticker", pa.string()),
        ("market_type", pa.string()), ("status", pa.string()), ("result", pa.string()),
        ("title", pa.string()), ("yes_sub_title", pa.string()), ("no_sub_title", pa.string()),
        ("rules_primary", pa.string()), ("rules_secondary", pa.string()),
        ("created_time", TS), ("open_time", TS), ("close_time", TS),
        ("expiration_time", TS), ("settlement_ts", TS),
        ("settlement_value_dollars", pa.float64()), ("last_price_dollars", pa.float64()),
        ("volume_fp", pa.float64()), ("open_interest_fp", pa.float64()),
        ("strike_type", pa.string()), ("floor_strike", pa.float64()), ("cap_strike", pa.float64()),
        ("expiration_value", pa.string()),
        ("source", pa.string()), ("fetched_at", TS), ("raw_json", pa.string()),
    ]),
    "trades": pa.schema([
        ("trade_id", pa.string()), ("ticker", pa.string()), ("series_ticker", pa.string()),
        ("created_time", TS), ("yes_price", pa.float64()), ("no_price", pa.float64()),
        ("count", pa.float64()), ("taker_side", pa.string()), ("taker_book_side", pa.string()),
        ("is_block_trade", pa.bool_()), ("source", pa.string()),
    ]),
    "candles": pa.schema(
        [("ticker", pa.string()), ("series_ticker", pa.string()), ("period_minutes", pa.int32()),
         ("end_period_ts", TS)]
        + [(f"{grp}_{f}", pa.float64()) for grp in ("yes_bid", "yes_ask")
           for f in ("open", "high", "low", "close")]
        + [(f"price_{f}", pa.float64()) for f in
           ("open", "high", "low", "close", "mean", "previous", "min", "max")]
        + [("volume", pa.float64()), ("open_interest", pa.float64()), ("source", pa.string())]),
    "orderbook": pa.schema([
        ("ts", TS), ("ticker", pa.string()), ("series_ticker", pa.string()),
        ("side", pa.string()), ("price", pa.float64()), ("size", pa.float64()),
    ]),
    "orderbook_snaps": pa.schema([
        ("ts", TS), ("ticker", pa.string()), ("series_ticker", pa.string()),
        ("n_yes_levels", pa.int32()), ("n_no_levels", pa.int32()),
    ]),
}


def table_of(kind: str, rows: list[dict]) -> pa.Table:
    sch = SCHEMAS[kind]
    return pa.Table.from_pylist([{f.name: r.get(f.name) for f in sch} for r in rows], schema=sch)


def series_row(s: dict, fetched_at) -> dict:
    return {
        "ticker": s.get("ticker"), "title": s.get("title"), "category": s.get("category"),
        "categories": list(s.get("categories") or []), "tags": list(s.get("tags") or []),
        "frequency": s.get("frequency"), "fee_type": s.get("fee_type"),
        "fee_multiplier": fnum(s.get("fee_multiplier")),
        "settlement_sources_json": jdump(s.get("settlement_sources")),
        "contract_url": s.get("contract_url"), "contract_terms_url": s.get("contract_terms_url"),
        "fetched_at": fetched_at,
    }


def market_row(m: dict, source: str, fetched_at) -> dict:
    t = m.get("ticker") or ""
    return {
        "ticker": t, "event_ticker": m.get("event_ticker"), "series_ticker": series_of(t),
        "market_type": m.get("market_type"), "status": m.get("status"), "result": m.get("result"),
        "title": m.get("title"), "yes_sub_title": m.get("yes_sub_title"),
        "no_sub_title": m.get("no_sub_title"),
        "rules_primary": m.get("rules_primary"), "rules_secondary": m.get("rules_secondary"),
        "created_time": parse_dt(m.get("created_time")), "open_time": parse_dt(m.get("open_time")),
        "close_time": parse_dt(m.get("close_time")),
        "expiration_time": parse_dt(m.get("expiration_time")),
        "settlement_ts": parse_dt(m.get("settlement_ts")),
        "settlement_value_dollars": fnum(m.get("settlement_value_dollars")),
        "last_price_dollars": fnum(m.get("last_price_dollars")),
        "volume_fp": fnum(m.get("volume_fp")), "open_interest_fp": fnum(m.get("open_interest_fp")),
        "strike_type": m.get("strike_type"), "floor_strike": fnum(m.get("floor_strike")),
        "cap_strike": fnum(m.get("cap_strike")),
        "expiration_value": None if m.get("expiration_value") is None else str(m.get("expiration_value")),
        "source": source, "fetched_at": fetched_at, "raw_json": jdump(m),
    }


def trade_row(t: dict, source: str, series: str | None = None) -> dict:
    tk = t.get("ticker") or ""
    return {
        "trade_id": t.get("trade_id"), "ticker": tk, "series_ticker": series or series_of(tk),
        "created_time": parse_dt(t.get("created_time")),
        "yes_price": fnum(t.get("yes_price_dollars")), "no_price": fnum(t.get("no_price_dollars")),
        "count": fnum(t.get("count_fp")),
        "taker_side": t.get("taker_outcome_side") or t.get("taker_side"),
        "taker_book_side": t.get("taker_book_side"),
        "is_block_trade": bool(t.get("is_block_trade")), "source": source,
    }


def candle_row(ticker: str, period: int, c: dict, source: str) -> dict:
    r = {"ticker": ticker, "series_ticker": series_of(ticker), "period_minutes": period,
         "end_period_ts": parse_dt(c.get("end_period_ts")), "source": source,
         "volume": fnum(c.get("volume_fp")), "open_interest": fnum(c.get("open_interest_fp"))}
    for grp in ("yes_bid", "yes_ask"):
        d = c.get(grp) or {}
        for f in ("open", "high", "low", "close"):
            r[f"{grp}_{f}"] = fnum(d.get(f"{f}_dollars"))
    d = c.get("price") or {}
    for f in ("open", "high", "low", "close", "mean", "previous", "min", "max"):
        r[f"price_{f}"] = fnum(d.get(f"{f}_dollars"))
    return r


# ---------------------------------------------------------------- store

class Store:
    """Parquet writer + append-only manifest.jsonl + per-query checkpoint."""

    def __init__(self, out_dir, command: str, query: dict):
        self.out = Path(out_dir)
        self.command, self.query = command, query
        self.out.mkdir(parents=True, exist_ok=True)
        self.manifest = self.out / "manifest.jsonl"
        qh = hashlib.sha256(jdump({"c": command, "q": query}).encode()).hexdigest()[:16]
        self.ckpt_path = self.out / "_checkpoints" / f"{command}-{qh}.json"
        self.done: set[str] = set()
        if self.ckpt_path.exists():
            self.done = set(json.loads(self.ckpt_path.read_text()).get("done", []))

    def save_checkpoint(self):
        self.ckpt_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ckpt_path.with_suffix(".tmp")
        tmp.write_text(jdump({"command": self.command, "query": self.query,
                              "done": sorted(self.done)}))
        tmp.replace(self.ckpt_path)

    def mark_done(self, unit: str):
        self.done.add(unit)
        self.save_checkpoint()

    def _log(self, rec: dict):
        with open(self.manifest, "a") as f:
            f.write(jdump(rec) + "\n")

    def write(self, kind: str, rows: list[dict], part: str, series: str | None = None,
              date: str | None = None) -> Path | None:
        if not rows:
            return None
        d = self.out / kind
        if series is not None:
            d = d / f"series={series}"
        if date is not None:
            d = d / f"date={date}"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"part-{hashlib.sha1(part.encode()).hexdigest()[:12]}.parquet"
        tmp = path.with_suffix(".tmp")
        pq.write_table(table_of(kind, rows), tmp)
        tmp.replace(path)
        self._log({"type": "file", "path": str(path.relative_to(self.out)),
                   "sha256": sha256_file(path), "rows": len(rows), "kind": kind,
                   "command": self.command, "query": self.query,
                   "written_at": now_utc().isoformat()})
        return path

    def write_partitioned(self, kind: str, rows: list[dict], part: str, time_field: str):
        groups: dict[tuple, list] = {}
        for r in rows:
            t = r.get(time_field)
            key = (r.get("series_ticker") or "unknown", t.strftime("%Y-%m-%d") if t else "unknown")
            groups.setdefault(key, []).append(r)
        for (s, dt), rs in sorted(groups.items()):
            self.write(kind, rs, part, series=s, date=dt)

    def run_record(self, client: Client, complete: bool, note: str = ""):
        self._log({"type": "run", "command": self.command, "query": self.query,
                   "requests": client.requests_made, "backoffs": len(client.backoffs),
                   "complete": complete, "note": note, "at": now_utc().isoformat()})


# ---------------------------------------------------------------- selection

def allowed_series(series_rows: list[dict], include: set[str] | None, exclude: set[str]) -> dict:
    """Map ticker -> series dict for series passing the category filter."""
    out = {}
    for s in series_rows:
        cats = {c.lower() for c in ([s.get("category")] + list(s.get("categories") or [])) if c}
        if not cats:
            continue  # unknown category is never assumed allowed
        if include is not None:
            if not (cats & include):
                continue
        elif cats & exclude:
            continue
        out[s["ticker"]] = s
    return out


def category_sets(args):
    if args.categories:
        return {c.strip().lower() for c in args.categories.split(",") if c.strip()}, set()
    return None, {"sports"}


def select_series(client, args, log):
    d = client.get("/series")
    inc, exc = category_sets(args)
    allowed = allowed_series(d.get("series") or [], inc, exc)
    if getattr(args, "series", None):
        want = [s.strip() for s in args.series.split(",") if s.strip()]
        for w in want:
            if w not in allowed:
                log(f"skip series {w}: unknown to /series or excluded by category filter")
        return [w for w in want if w in allowed]
    return sorted(allowed)


class Cutoff:
    def __init__(self, client):
        self.client, self._v = client, None

    def get(self):
        if self._v is None:
            d = self.client.get("/historical/cutoff")
            self._v = {k: to_unix(v) for k, v in d.items()}
        return self._v


def fetch_settled_markets(client, cutoff: Cutoff, series: str, since, until):
    """Yield (source, market_dict) for settled markets of ``series`` in [since, until)."""
    c = cutoff.get()["market_settled_ts"]
    if until is None or until >= c:
        p = {"series_ticker": series, "status": "settled", "mve_filter": "exclude"}
        if since is not None:
            p["min_settled_ts"] = since
        if until is not None:
            p["max_settled_ts"] = until
        for m in paginate(client, "/markets", p, "markets"):
            yield "live", m
    if since is None or since < c:
        for m in paginate(client, "/historical/markets",
                          {"series_ticker": series, "mve_filter": "exclude"}, "markets"):
            ts = to_unix(m.get("settlement_ts"))
            if ts is None or (since is not None and ts < since) or (until is not None and ts >= until):
                continue
            yield "historical", m


def window(args):
    since = to_unix(args.since) if getattr(args, "since", None) else None
    until = to_unix(args.until) if getattr(args, "until", None) else None
    if since is not None and until is not None and until <= since:
        raise SystemExit("--until must be after --since")
    return since, until


# ---------------------------------------------------------------- commands

def cmd_series(client, store, args, log):
    d = client.get("/series")
    inc, exc = category_sets(args)
    rows = [series_row(s, now_utc()) for s in d.get("series") or []]
    keep = allowed_series(d.get("series") or [], inc, exc) if not args.all_categories else None
    if keep is not None:
        rows = [r for r in rows if r["ticker"] in keep]
    store.write("series", rows, part=now_utc().strftime("%Y-%m-%d"), date=now_utc().strftime("%Y-%m-%d"))
    log(f"series: {len(rows)} rows")
    return True


def cmd_markets(client, store, args, log):
    since, until = window(args)
    cutoff = Cutoff(client)
    for s in select_series(client, args, log):
        if s in store.done:
            continue
        seen, rows = set(), []
        fetched = now_utc()
        for source, m in fetch_settled_markets(client, cutoff, s, since, until):
            if m["ticker"] in seen:
                continue
            seen.add(m["ticker"])
            rows.append(market_row(m, source, fetched))
        store.write_partitioned("markets", rows, part=f"markets:{s}", time_field="settlement_ts")
        store.mark_done(s)
        log(f"markets {s}: {len(rows)}")
    return True


def _tickers_for_trades(client, store, args, log, cutoff, since, until):
    if args.market:
        allowed = set(select_series(client, argparse.Namespace(
            categories=args.categories, series=",".join(sorted({series_of(m) for m in args.market.split(",")}))),
            log))
        for m in [x.strip() for x in args.market.split(",") if x.strip()]:
            if series_of(m) in allowed:
                yield m
            else:
                log(f"skip market {m}: series excluded or unknown")
        return
    for s in select_series(client, args, log):
        # markets that settled on/after --since; their trades are then limited to the window
        for _src, m in fetch_settled_markets(client, cutoff, s, since, None):
            yield m["ticker"]


def fetch_trades(client, cutoff: Cutoff, ticker, since, until):
    c = cutoff.get()["trades_created_ts"]
    out = []
    if since is None or since < c:
        hi = c if until is None else min(until, c)
        p = {"ticker": ticker, "max_ts": hi}
        if since is not None:
            p["min_ts"] = since
        out += [trade_row(t, "historical") for t in paginate(client, "/historical/trades", p, "trades")]
    if until is None or until > c:
        p = {"ticker": ticker, "min_ts": c if since is None else max(since, c)}
        if until is not None:
            p["max_ts"] = until
        out += [trade_row(t, "live") for t in paginate(client, "/markets/trades", p, "trades")]
    seen, uniq = set(), []
    for r in out:
        if r["trade_id"] in seen:
            continue
        seen.add(r["trade_id"])
        uniq.append(r)
    return uniq


def cmd_trades(client, store, args, log):
    if not args.market and not args.series:
        raise SystemExit("trades needs --market or --series")
    since, until = window(args)
    cutoff = Cutoff(client)
    for t in _tickers_for_trades(client, store, args, log, cutoff, since, until):
        if t in store.done:
            continue
        rows = fetch_trades(client, cutoff, t, since, until)
        store.write_partitioned("trades", rows, part=f"trades:{t}", time_field="created_time")
        store.mark_done(t)
        log(f"trades {t}: {len(rows)}")
    return True


def cmd_candles(client, store, args, log):
    if not args.market and not args.series:
        raise SystemExit("candles needs --market or --series")
    if not args.since:
        raise SystemExit("candles needs --since")
    since, until = window(args)
    until = until if until is not None else int(now_utc().timestamp())
    period = args.period
    step = period * 60
    cutoff = Cutoff(client)
    for t in _tickers_for_trades(client, store, args, log, cutoff, since, until):
        unit = f"{t}:{period}"
        if unit in store.done:
            continue
        rows, source = [], "live"
        lo = since
        while lo < until:
            hi = min(until, lo + CANDLE_CHUNK * step)
            p = {"start_ts": lo, "end_ts": hi, "period_interval": period}
            cs = None
            if source == "live":
                try:
                    cs = client.get(f"/series/{series_of(t)}/markets/{t}/candlesticks", p).get("candlesticks")
                except HttpError as e:
                    if e.status != 404:
                        raise
                if not cs and not rows:
                    source = "historical"  # archived (or unknown): try the historical tier
            if source == "historical":
                try:
                    cs = client.get(f"/historical/markets/{t}/candlesticks", p).get("candlesticks")
                except HttpError as e:
                    if e.status != 404:
                        raise
                    cs = []
            rows += [candle_row(t, period, c, source) for c in cs or []]
            lo = hi
        # a candle ending exactly on a chunk edge can appear in two chunks
        uniq = {r["end_period_ts"]: r for r in rows}
        rows = [uniq[k] for k in sorted(uniq)]
        store.write_partitioned("candles", rows, part=unit, time_field="end_period_ts")
        store.mark_done(unit)
        log(f"candles {unit}: {len(rows)}")
    return True


def snapshot_rows(ticker: str, ob: dict, ts):
    o = ob.get("orderbook_fp") or {}
    rows = []
    for side in ("yes", "no"):
        for lvl in o.get(f"{side}_dollars") or []:
            rows.append({"ts": ts, "ticker": ticker, "series_ticker": series_of(ticker),
                         "side": side, "price": fnum(lvl[0]), "size": fnum(lvl[1])})
    snap = {"ts": ts, "ticker": ticker, "series_ticker": series_of(ticker),
            "n_yes_levels": len(o.get("yes_dollars") or []),
            "n_no_levels": len(o.get("no_dollars") or [])}
    return rows, snap


def cmd_orderbook_snap(client, store, args, log, clock=time.time, sleep=time.sleep, utcnow=now_utc):
    if args.every_s <= 0 or args.duration_s <= 0:
        raise SystemExit("--every-s and --duration-s must be > 0")
    tickers = [m.strip() for m in args.markets.split(",") if m.strip()]
    allowed = set(select_series(client, argparse.Namespace(
        categories=args.categories, series=",".join(sorted({series_of(t) for t in tickers}))), log))
    tickers = [t for t in tickers if series_of(t) in allowed]
    run_id = utcnow().strftime("%Y%m%dT%H%M%S")
    t0 = clock()
    ob_rows, snaps, n_flush = [], [], 0

    def flush():
        nonlocal ob_rows, snaps, n_flush
        part = f"{run_id}-{n_flush}"
        store.write_partitioned("orderbook", ob_rows, part, "ts")
        store.write_partitioned("orderbook_snaps", snaps, part, "ts")
        ob_rows, snaps, n_flush = [], [], n_flush + 1

    i = 0
    try:
        while True:
            for t in tickers:
                try:
                    ob = client.get(f"/markets/{t}/orderbook", {"depth": args.depth})
                except HttpError as e:
                    log(f"orderbook {t}: HTTP {e.status}, skipped this round")
                    continue
                rows, snap = snapshot_rows(t, ob, utcnow())
                ob_rows += rows
                snaps.append(snap)
            i += 1
            if len(snaps) >= 200:
                flush()
            nxt = t0 + i * args.every_s
            if nxt - t0 > args.duration_s:
                break
            sleep(max(0.0, nxt - clock()))
    finally:
        if snaps or ob_rows:
            flush()
    log(f"orderbook rounds: {i}")
    return True


# ---------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--rate", type=float, default=DEFAULT_RATE, help="client-side requests/second")
    p.add_argument("--max-requests", type=int, default=None,
                   help="hard cap on HTTP attempts (retries included); checkpoint is kept")
    p.add_argument("--categories", default=None,
                   help="comma list to INCLUDE (case-insensitive). Default: all except Sports")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("series", help="list series with category")
    s.add_argument("--all-categories", action="store_true", help="also write excluded categories")

    m = sub.add_parser("markets", help="settled markets")
    m.add_argument("--status", default="settled", choices=["settled"])
    m.add_argument("--series")
    m.add_argument("--since")
    m.add_argument("--until")

    t = sub.add_parser("trades", help="trade history")
    t.add_argument("--market")
    t.add_argument("--series")
    t.add_argument("--since")
    t.add_argument("--until")

    c = sub.add_parser("candles", help="candlesticks")
    c.add_argument("--market")
    c.add_argument("--series")
    c.add_argument("--since")
    c.add_argument("--until")
    c.add_argument("--period", type=int, default=60, choices=[1, 60, 1440])

    o = sub.add_parser("orderbook-snap", help="forward resting-depth observer")
    o.add_argument("--markets", required=True)
    o.add_argument("--every-s", type=float, default=60)
    o.add_argument("--duration-s", type=float, required=True)
    o.add_argument("--depth", type=int, default=0)
    return p


QUERY_KEYS = ("categories", "all_categories", "status", "series", "since", "until", "market",
              "period", "markets", "every_s", "duration_s", "depth")
COMMANDS = {"series": cmd_series, "markets": cmd_markets, "trades": cmd_trades,
            "candles": cmd_candles, "orderbook-snap": cmd_orderbook_snap}


def main(argv=None, client: Client | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = lambda s: print(s, file=sys.stderr)
    client = client or Client(rate=args.rate, max_requests=args.max_requests)
    query = {k: getattr(args, k) for k in QUERY_KEYS if hasattr(args, k)}
    store = Store(args.out, args.cmd, query)
    try:
        COMMANDS[args.cmd](client, store, args, log)
    except MaxRequestsReached as e:
        store.save_checkpoint()
        store.run_record(client, complete=False, note=str(e))
        log(f"stopped: {e}; re-run the same command to resume")
        return 3
    store.save_checkpoint()
    store.run_record(client, complete=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
