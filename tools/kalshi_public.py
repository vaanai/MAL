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
  * Combo (multivariate) markets are dropped client-side (is_mve); mve_filter is never sent
    with series_ticker/event_ticker/tickers (mutually exclusive, HTTP 400). A live
    /markets GET with series_ticker+status+mve_filter returned 200, but we do not rely on it.
  * Sports is excluded by default (``--categories`` to choose another set; a
    series is excluded when its primary category OR any discovery category
    equals an excluded one; series whose category cannot be determined are
    skipped, never assumed allowed).
  * Second sports signal: SPORTS_KEYWORDS (incl. esports) against the series ticker
    (substring) and title (whole word).  Both signals are heuristics: a sports
    series with an unlisted name and a non-sports category label gets through, and
    some non-sports series are dropped when a short keyword starts a ticker token.  Not a guarantee.
  * Candles: per chunk, live tier then historical tier; a unit is done only after
    both were tried, and a unit with no rows is logged as status empty_both_tiers.
  * Trades: taker side is stored as ``taker_outcome_side`` (falls back to the
    deprecated ``taker_side``).  Prices are dollars as floats.
  * Resumability is per unit (series for markets, ticker for trades/candles).
    A unit is marked done only after its files are written.  The manifest is an
    append-only log; for a path that appears twice take the last line.
  * The orderbook observer is a forward sampler: each run starts a new set of
    part files; an empty book yields a row in ``orderbook_snaps`` (n levels 0)
    and no rows in ``orderbook``.
  * orderbook-snap --series (rolling markets).  Timestamps: ``ts`` = local UTC clock taken
    AFTER the response returned (so ts - req_start_ts is the round trip incl. retries and
    backoff); ``req_start_ts`` = local clock before the first attempt; ``server_date`` = the
    response Date header (1 s resolution, null if absent).  ``first_seen_this_run`` is when
    this run first saw the market in an open listing, NOT its open time (use
    markets.open_time).  Markets whose listed close_time has passed are skipped before
    snapping; a market that closes between that check and the fetch yields a row in
    ``orderbook_fetch_errors`` (HTTP status) or an empty-book snap (n levels 0); both are
    recorded, never inferred.  A listing that fails on ANY page counts as a failed listing for
    that series (page 1 is discarded, not half-used); the series' previously known markets are
    carried, and after --max-discover-failures consecutive failures they are dropped and the
    drop is recorded in ``orderbook_discoveries``.  The series list is resolved once and
    fixed for the run; a change needs a new run.
    Coverage tables: orderbook_runs (run_start written before the first listing or snapshot
    request, after the one /series call that resolves the filter; run_end with stop_reason,
    complete=true only for a duration stop; Ctrl-C is stop_reason=interrupt, complete=false;
    chain_head = sha256 of the last chain line at that moment, i.e. BEFORE the final seal that
    contains run_end itself), orderbook_rounds, orderbook_discoveries,
    orderbook_fetch_errors.  Sealing: every part file is listed in the append-only
    ``orderbook-MANIFEST.chain`` (mode 0440, one line per hour, each with prev = sha256 of the
    previous line); read with read_verified(out, kind), which refuses unsealed or altered
    parts, and by default (require_complete_runs=True) also refuses a directory in which any run
    lacks a run_end with complete=true (a live or crashed run included; diagnostics pass False).
    One recorder per --out: the run holds an exclusive flock on ``<out>/.orderbook.lock`` (pid
    inside); a second copy exits 5.  ``quarantine-unsealed`` moves parts not covered by the chain
    to ``<out>/_quarantine/<utc-stamp>/`` with a 0440 manifest; it never touches sealed parts or
    the chain.  Parts written before these columns existed lack them: read_table_unified() fills
    nulls against the current schema.  Use a dedicated --out per recorder: --markets parts
    are never sealed.
"""
from __future__ import annotations

import argparse
import email.utils
import fcntl
import hashlib
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
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


# Second sports signal (the first is the series category).  Pinned list; matched as a
# case-insensitive prefix (<=4 chars) or substring (longer) of a SERIES TICKER token
# and as a whole word in the series title.  False positives are possible and accepted (excluding is the safe direction).
# Explicitly passing --categories Sports opts out of this list.
SPORTS_KEYWORDS = (
    "nfl", "nba", "wnba", "mlb", "nhl", "ncaa", "mls", "epl", "ufc", "mma", "pga", "atp", "wta",
    "nascar", "formula1", "f1", "golf", "tennis", "soccer", "football", "basketball", "baseball",
    "hockey", "boxing", "cricket", "ipl", "olympic", "laliga", "bundesliga", "ligue1", "seriea",
    "premierleague", "championsleague", "worldcup", "superbowl", "nflgame", "marchmadness",
    "esport", "lol", "csgo", "cs2", "valorant", "dota", "overwatch", "rocketleague", "fortnite",
    "starcraft", "leagueoflegends", "counterstrike",
)
_KW_TITLE = re.compile(r"\b(" + "|".join(re.escape(k) for k in SPORTS_KEYWORDS) + r")\b", re.I)


SHORT_KW = 4  # keywords this long or shorter match only at the START of a ticker token


def ticker_keyword_hit(ticker: str) -> bool:
    """Tokenize on non-alphanumerics, strip a leading KX; short keywords must be a
    token prefix (so NFLGAME hits 'nfl' but INFLATION and SUMMARY do not); longer
    keywords match as substrings of the token."""
    for tok in re.split(r"[^a-z0-9]+", (ticker or "").lower()):
        if tok.startswith("kx"):
            tok = tok[2:]
        for k in SPORTS_KEYWORDS:
            if (tok.startswith(k) if len(k) <= SHORT_KW else k in tok):
                return True
    return False


def sports_keyword_hit(ticker: str, title: str | None) -> bool:
    return ticker_keyword_hit(ticker) or bool(_KW_TITLE.search(title or ""))


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
    """GET only, no credentials.  Returns (status, body_bytes, date_header_or_None); status 0 =
    network error.  (Test fakes may return just (status, body).)"""
    req = urllib.request.Request(
        url, method="GET",
        headers={"Accept": "application/json", "User-Agent": "mal-kalshi-public/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Date")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), (e.headers.get("Date") if e.headers else None)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, b"", None


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
                 base_url=BASE_URL, jitter=0.25, rng=random.random):
        self.http = http or urllib_http
        self.sleep = sleep
        self.limiter = RateLimiter(rate, clock=clock, sleep=sleep)
        self.max_requests = max_requests
        self.max_retries = max_retries
        self.backoff_base, self.backoff_cap = backoff_base, backoff_cap
        self.base_url = base_url
        self.jitter, self.rng = jitter, rng
        self.requests_made = 0  # every HTTP attempt, retries included
        self.backoffs = []
        self.last_date = None  # raw server Date header of the last 200 response (None if absent)

    def get(self, path: str, params: dict | None = None) -> dict:
        self.last_date = None
        path = check_path(path)
        if params and params.get("mve_filter") and any(
                params.get(k) for k in ("series_ticker", "event_ticker", "tickers")):
            raise ValueError("mve_filter is mutually exclusive with series_ticker/event_ticker/tickers")
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        url = self.base_url + path + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in range(self.max_retries + 1):
            if self.max_requests is not None and self.requests_made >= self.max_requests:
                raise MaxRequestsReached(f"--max-requests {self.max_requests} reached")
            self.limiter.wait()
            self.requests_made += 1
            res = self.http(url)
            status, body = res[0], res[1]
            if status == 200:
                self.last_date = res[2] if len(res) > 2 else None
                return json.loads(body)
            retryable = status == 429 or status >= 500 or status == 0
            if not retryable:
                raise HttpError(status, body)
            if attempt == self.max_retries:
                raise HttpError(status, body)
            delay = min(self.backoff_cap, self.backoff_base * (2 ** attempt))
            delay += delay * self.jitter * self.rng()  # 0..jitter extra, after the cap
            self.backoffs.append(delay)
            self.sleep(delay)
        raise AssertionError("unreachable")


def paginate_pages(client: Client, path: str, params: dict, key: str):
    """Yield one list of items per cursor page.  Stops on empty/missing or repeated cursor."""
    params = dict(params)
    params.setdefault("limit", PAGE_LIMIT)
    seen = set()
    while True:
        d = client.get(path, params)
        yield d.get(key) or []
        cur = d.get("cursor")
        if not cur or cur in seen:
            return
        seen.add(cur)
        params["cursor"] = cur


def paginate(client: Client, path: str, params: dict, key: str):
    for page in paginate_pages(client, path, params, key):
        yield from page


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
        # Added with --series mode; parts written before that lack these columns, so read
        # through read_table_unified()/read_verified(), never a bare dataset read.
        ("first_seen_this_run", TS),  # --series: when THIS RUN first saw the market in an open
        #   listing (NOT the market's open time; that is markets.open_time).  null in --markets mode
        ("req_start_ts", TS),   # local clock just before the first HTTP attempt of this snap
        ("attempts", pa.int32()),  # HTTP attempts for this snap (1 + retries)
        ("server_date", TS),    # parsed server Date header of the response (1 s resolution); null if absent
    ]),
    "orderbook_runs": pa.schema([
        ("run_id", pa.string()), ("event", pa.string()), ("ts", TS), ("git_sha", pa.string()),
        ("series_json", pa.string()), ("every_s", pa.float64()), ("depth", pa.int32()),
        ("rate", pa.float64()), ("discover_every_s", pa.float64()), ("strict_cadence", pa.bool_()),
        ("max_discover_failures", pa.int32()),
        ("stop_reason", pa.string()), ("complete", pa.bool_()), ("note", pa.string()),
        ("n_rounds", pa.int32()), ("n_snaps", pa.int64()), ("requests", pa.int64()),
        ("chain_head", pa.string()),  # sha256 of the last chain line when run_end was written
    ]),
    "orderbook_rounds": pa.schema([
        ("run_id", pa.string()), ("round", pa.int32()), ("ts", TS), ("n_open", pa.int32()),
        ("n_snaps_ok", pa.int32()), ("n_errors", pa.int32()), ("errors_json", pa.string()),
        ("n_skipped_closed", pa.int32()), ("every_eff", pa.float64()), ("stretched", pa.bool_()),
    ]),
    "orderbook_discoveries": pa.schema([
        ("run_id", pa.string()), ("ts", TS), ("series_ticker", pa.string()), ("ok", pa.bool_()),
        ("n_open", pa.int32()), ("combos_dropped", pa.int32()), ("pages", pa.int32()),
        ("consecutive_failures", pa.int32()), ("carried_dropped", pa.bool_()),
        ("n_carried", pa.int32()),
    ]),
    "orderbook_fetch_errors": pa.schema([
        ("run_id", pa.string()), ("ts", TS), ("ticker", pa.string()), ("series_ticker", pa.string()),
        ("status", pa.int32()), ("attempts", pa.int32()),
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


def market_row(m: dict, source: str, fetched_at, raw: bool = False) -> dict:
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
        "source": source, "fetched_at": fetched_at, "raw_json": jdump(m) if raw else None,
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
        paths = []
        for (s, dt), rs in sorted(groups.items()):
            paths.append(self.write(kind, rs, part, series=s, date=dt))
        return paths

    def run_record(self, client: Client, complete: bool, note: str = "", error: str | None = None):
        self._log({"type": "run", "command": self.command, "query": self.query,
                   "requests": client.requests_made, "backoffs": len(client.backoffs),
                   "complete": complete, "note": note, "error": error,
                   "at": now_utc().isoformat()})

    def unit_record(self, unit: str, status: str, rows: int):
        self._log({"type": "unit", "command": self.command, "unit": unit, "status": status,
                   "rows": rows, "at": now_utc().isoformat()})


# ---------------------------------------------------------------- selection

def allowed_series(series_rows: list[dict], include: set[str] | None, exclude: set[str],
                   allow: set[str] = frozenset()) -> dict:
    """Map ticker -> series dict for series passing the category filter."""
    out = {}
    for s in series_rows:
        if s.get("ticker") in allow:  # explicit --allow-series override (recorded in the manifest)
            out[s["ticker"]] = s
            continue
        cats = {c.lower() for c in ([s.get("category")] + list(s.get("categories") or [])) if c}
        if not cats:
            continue  # unknown category is never assumed allowed
        if include is not None:
            if not (cats & include):
                continue
        elif cats & exclude:
            continue
        if "sports" not in (include or set()) and sports_keyword_hit(s.get("ticker"), s.get("title")):
            continue
        out[s["ticker"]] = s
    return out


def category_sets(args):
    if args.categories:
        return {c.strip().lower() for c in args.categories.split(",") if c.strip()}, set()
    return None, {"sports"}


def allow_set(args) -> set[str]:
    return {x.strip() for x in (getattr(args, "allow_series", None) or "").split(",") if x.strip()}


def select_series(client, args, log):
    d = client.get("/series")
    inc, exc = category_sets(args)
    allowed = allowed_series(d.get("series") or [], inc, exc, allow_set(args))
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


def is_mve(m: dict) -> bool:
    """Combo / multivariate market.  Marked by ``mve_collection_ticker`` (documented on
    the Market object, get-markets.md) or non-empty ``mve_selected_legs``; the
    KXMVE ticker prefix is a backstop.  Needed because mve_filter cannot be sent
    together with series_ticker / event_ticker / tickers (HTTP 400 on
    /historical/markets, job #1124 probe 2026-10-17)."""
    return bool(m.get("mve_collection_ticker") or m.get("mve_selected_legs")
                or str(m.get("ticker", "")).upper().startswith("KXMVE"))


def fetch_settled_market_pages(client, cutoff: Cutoff, series: str, since, until):
    """Yield (source, [market_dict, ...]) per page for settled markets of ``series`` in [since, until)."""
    c = cutoff.get()["market_settled_ts"]
    if until is None or until >= c:
        p = {"series_ticker": series, "status": "settled"}
        if since is not None:
            p["min_settled_ts"] = since
        if until is not None:
            p["max_settled_ts"] = until
        for page in paginate_pages(client, "/markets", p, "markets"):
            yield "live", [m for m in page if not is_mve(m)]
    if since is None or since < c:
        for page in paginate_pages(client, "/historical/markets",
                                   {"series_ticker": series}, "markets"):
            keep = []
            for m in page:
                ts = to_unix(m.get("settlement_ts"))
                if is_mve(m):
                    continue
                if ts is None or (since is not None and ts < since) or (until is not None and ts >= until):
                    continue
                keep.append(m)
            yield "historical", keep


def fetch_settled_markets(client, cutoff: Cutoff, series: str, since, until):
    for source, page in fetch_settled_market_pages(client, cutoff, series, since, until):
        for m in page:
            yield source, m


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
    keep = allowed_series(d.get("series") or [], inc, exc, allow_set(args)) if not args.all_categories else None
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
        seen, rows, n_rows, chunk, pages = set(), [], 0, 0, 0
        fetched = now_utc()

        def flush():
            nonlocal rows, chunk, n_rows
            store.write_partitioned("markets", rows, part=f"markets:{s}:{chunk}",
                                    time_field="settlement_ts")
            n_rows += len(rows)
            rows, chunk = [], chunk + 1

        for source, page in fetch_settled_market_pages(client, cutoff, s, since, until):
            for m in page:
                if m["ticker"] not in seen:
                    seen.add(m["ticker"])
                    rows.append(market_row(m, source, fetched, raw=args.raw_json))
            pages += 1
            if pages % args.flush_pages == 0:
                flush()
        flush()
        store.mark_done(s)
        log(f"markets {s}: {n_rows}")
    return True


def _tickers_for_trades(client, store, args, log, cutoff, since, until):
    if args.market:
        allowed = set(select_series(client, argparse.Namespace(
            categories=args.categories, allow_series=args.allow_series,
            series=",".join(sorted({series_of(m) for m in args.market.split(",")}))),
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


def iter_trade_batches(client, cutoff: Cutoff, ticker, since, until, flush_pages=50):
    """Yield lists of trade rows, one list per ``flush_pages`` pages; dedupes by trade_id."""
    c = cutoff.get()["trades_created_ts"]
    plans = []
    if since is None or since < c:
        hi = c if until is None else min(until, c)
        p = {"ticker": ticker, "max_ts": hi}
        if since is not None:
            p["min_ts"] = since
        plans.append(("/historical/trades", "historical", p))
    if until is None or until > c:
        p = {"ticker": ticker, "min_ts": c if since is None else max(since, c)}
        if until is not None:
            p["max_ts"] = until
        plans.append(("/markets/trades", "live", p))
    seen, buf, pages = set(), [], 0
    for path, source, p in plans:
        for page in paginate_pages(client, path, p, "trades"):
            for t in page:
                if t.get("trade_id") not in seen:
                    seen.add(t.get("trade_id"))
                    buf.append(trade_row(t, source))
            pages += 1
            if pages % flush_pages == 0:
                yield buf
                buf = []
    if buf:
        yield buf


def cmd_trades(client, store, args, log):
    if not args.market and not args.series:
        raise SystemExit("trades needs --market or --series")
    since, until = window(args)
    cutoff = Cutoff(client)
    for t in _tickers_for_trades(client, store, args, log, cutoff, since, until):
        if t in store.done:
            continue
        n = 0
        for i, rows in enumerate(iter_trade_batches(client, cutoff, t, since, until, args.flush_pages)):
            store.write_partitioned("trades", rows, part=f"trades:{t}:{i}", time_field="created_time")
            n += len(rows)
        store.mark_done(t)
        log(f"trades {t}: {n}")
    return True


def cmd_candles(client, store, args, log):
    """Per chunk: live tier first, then the historical tier if live is empty/404.

    A unit is marked done only after BOTH tiers were tried for every empty chunk;
    if nothing came back at all it is recorded as status ``empty_both_tiers``.
    """
    if not args.market and not args.series:
        raise SystemExit("candles needs --market or --series")
    if not args.since:
        raise SystemExit("candles needs --since")
    since, until = window(args)
    until = until if until is not None else int(now_utc().timestamp())
    period = args.period
    step = period * 60
    cutoff = Cutoff(client)

    def tier(path, p):
        try:
            return client.get(path, p).get("candlesticks") or []
        except HttpError as e:
            if e.status != 404:
                raise
            return []

    for t in _tickers_for_trades(client, store, args, log, cutoff, since, until):
        unit = f"{t}:{period}"
        if unit in store.done:
            continue
        rows, lo, chunks_empty = [], since, 0
        while lo < until:
            hi = min(until, lo + CANDLE_CHUNK * step)
            p = {"start_ts": lo, "end_ts": hi, "period_interval": period}
            source = "live"
            cs = tier(f"/series/{series_of(t)}/markets/{t}/candlesticks", p)
            if not cs:
                source = "historical"
                cs = tier(f"/historical/markets/{t}/candlesticks", p)
            if not cs:
                chunks_empty += 1
            rows += [candle_row(t, period, c, source) for c in cs]
            lo = hi
        uniq = {r["end_period_ts"]: r for r in rows}  # chunk-edge candles can repeat
        rows = [uniq[k] for k in sorted(uniq)]
        store.write_partitioned("candles", rows, part=unit, time_field="end_period_ts")
        status = "ok" if rows else "empty_both_tiers"
        store.unit_record(unit, status, len(rows))
        store.mark_done(unit)
        log(f"candles {unit}: {len(rows)} ({status}; {chunks_empty} chunks empty in both tiers)")
    return True


def read_table_unified(root, kind: str, files=None) -> pa.Table:
    """Read every part of ``kind`` under ``root`` against the CURRENT schema: columns missing
    from older parts become nulls, unknown columns are ignored, types are cast.  (A plain
    dataset read takes the first file's schema and drops or breaks on mixed generations.)"""
    sch = SCHEMAS[kind]
    files = sorted(Path(root, kind).rglob("*.parquet")) if files is None else files
    tabs = []
    for f in files:
        t = pq.read_table(f)
        tabs.append(pa.table({fld.name: (t[fld.name].cast(fld.type) if fld.name in t.column_names
                                         else pa.nulls(t.num_rows, fld.type)) for fld in sch}, schema=sch))
    return pa.concat_tables(tabs) if tabs else sch.empty_table()


def parse_server_date(h):
    try:
        d = email.utils.parsedate_to_datetime(h) if h else None
    except (TypeError, ValueError):
        return None
    if d is None:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def snapshot_rows(ticker: str, ob: dict, ts, first_seen=None, req_start=None, attempts=None,
                  server_date=None):
    o = ob.get("orderbook_fp") or {}
    rows = []
    for side in ("yes", "no"):
        for lvl in o.get(f"{side}_dollars") or []:
            rows.append({"ts": ts, "ticker": ticker, "series_ticker": series_of(ticker),
                         "side": side, "price": fnum(lvl[0]), "size": fnum(lvl[1])})
    snap = {"ts": ts, "ticker": ticker, "series_ticker": series_of(ticker),
            "n_yes_levels": len(o.get("yes_dollars") or []),
            "n_no_levels": len(o.get("no_dollars") or []),
            "first_seen_this_run": first_seen, "req_start_ts": req_start,
            "attempts": attempts, "server_date": server_date}
    return rows, snap


CHAIN_NAME = "orderbook-MANIFEST.chain"
SEALED_KINDS = ("orderbook", "orderbook_snaps", "orderbook_runs", "orderbook_rounds",
                "orderbook_discoveries", "orderbook_fetch_errors")
ZERO_HASH = "0" * 64


class SealError(Exception):
    pass


def _atomic_write(path: Path, data: bytes) -> None:
    """tmp + fsync + os.replace (same approach as meta_capture.atomic_write); pid in the name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _chain_lines(out: Path) -> list[str]:
    p = Path(out) / CHAIN_NAME
    return p.read_text().splitlines() if p.exists() else []


def seal_parts(out, hour: str, paths) -> bool:
    """Append ONE chain line covering the not-yet-covered ``paths`` (closed during ``hour``):
    path, sha256, rows, bytes, prev = sha256 of the previous chain line.  Atomic replace, then
    chmod 0440.  Idempotent: paths already covered are skipped; nothing new -> no write (but
    the mode is still re-applied, so a retry after a failed chmod finishes the job)."""
    out = Path(out)
    lines = _chain_lines(out)
    covered = {f["path"] for ln in lines for f in json.loads(ln)["files"]}
    files = []
    for p in sorted({Path(x) for x in paths}):
        rel = str(p.relative_to(out))
        if rel in covered:
            continue
        files.append({"path": rel, "sha256": sha256_file(p), "rows": pq.read_metadata(p).num_rows,
                      "bytes": p.stat().st_size})
    chain = out / CHAIN_NAME
    if not files:
        if chain.exists():
            os.chmod(chain, 0o440)
        return False
    prev = hashlib.sha256(lines[-1].encode()).hexdigest() if lines else ZERO_HASH
    lines.append(jdump({"hour": hour, "files": files, "prev": prev}))
    _atomic_write(chain, ("\n".join(lines) + "\n").encode())
    os.chmod(chain, 0o440)
    return True


def chain_head(out) -> str | None:
    """sha256 of the last chain line (what the next line's ``prev`` will be); None if no chain."""
    lines = _chain_lines(out)
    return hashlib.sha256(lines[-1].encode()).hexdigest() if lines else None


LOCK_NAME = ".orderbook.lock"


class LockHeld(Exception):
    pass


def acquire_lock(out):
    """Exclusive non-blocking flock on <out>/.orderbook.lock for the life of the returned
    handle (close it to release); writes the pid.  Raises LockHeld if another process has it."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    fh = open(out / LOCK_NAME, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise LockHeld(str(out / LOCK_NAME))
    fh.seek(0)
    fh.truncate()
    fh.write(f"{os.getpid()}\n")
    fh.flush()
    return fh


def quarantine_unsealed(out, utcnow=now_utc, kinds=SEALED_KINDS) -> dict:
    """Move parts of ``kinds`` that the chain does not cover into _quarantine/<utc-stamp>/ (same
    relative layout) and write manifest.json there at 0440 (path, sha256, rows, bytes).  Sealed
    parts and the chain are never touched; a broken chain refuses (SealError) before any move."""
    out = Path(out)
    lines = _chain_lines(out)
    covered, prev = set(), ZERO_HASH
    for n, ln in enumerate(lines):
        rec = json.loads(ln)
        if rec["prev"] != prev:
            raise SealError(f"chain line {n}: prev mismatch; refusing to quarantine")
        prev = hashlib.sha256(ln.encode()).hexdigest()
        covered.update(f["path"] for f in rec["files"])
    todo = [p for k in kinds for p in sorted((out / k).rglob("*.parquet"))
            if str(p.relative_to(out)) not in covered]
    if not todo:
        return {"moved": 0, "dir": None}
    qdir = out / "_quarantine" / utcnow().strftime("%Y%m%dT%H%M%SZ")
    qdir.mkdir(parents=True, exist_ok=False)
    moved = []
    for p in todo:
        rel = p.relative_to(out)
        try:
            rows = pq.read_metadata(p).num_rows
        except Exception:  # unreadable part: still moved, rows unknown
            rows = None
        rec = {"path": str(rel), "sha256": sha256_file(p), "rows": rows, "bytes": p.stat().st_size}
        dest = qdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dest))
        moved.append(rec)
    man = qdir / "manifest.json"
    man.write_text(jdump({"moved_at": utcnow().isoformat(), "files": moved}) + "\n")
    os.chmod(man, 0o440)
    return {"moved": len(moved), "dir": str(qdir)}


def verify_parts(out, kinds=SEALED_KINDS) -> dict:
    """Check the chain links and every part of ``kinds`` on disk: refuses (SealError) a broken
    prev link, a listed part that is missing or whose sha256 mismatches, and any part the chain
    does not cover.  Returns {relative_path: chain_entry}."""
    out = Path(out)
    lines = _chain_lines(out)
    if not lines:
        raise SealError(f"no {CHAIN_NAME} in {out}: nothing is sealed")
    prev, entries = ZERO_HASH, {}
    for n, ln in enumerate(lines):
        rec = json.loads(ln)
        if rec["prev"] != prev:
            raise SealError(f"chain line {n}: prev mismatch")
        prev = hashlib.sha256(ln.encode()).hexdigest()
        for f in rec["files"]:
            entries[f["path"]] = f
    for rel, f in entries.items():
        p = out / rel
        if not p.exists():
            raise SealError(f"sealed part missing: {rel}")
        if sha256_file(p) != f["sha256"]:
            raise SealError(f"sha256 mismatch: {rel}")
    for k in kinds:
        for p in (out / k).rglob("*.parquet"):
            if str(p.relative_to(out)) not in entries:
                raise SealError(f"part not covered by the chain: {p.relative_to(out)}")
    return entries


def read_verified(out, kind: str, require_complete_runs: bool = True) -> pa.Table:
    """verify_parts(), then read ``kind`` against the unified current schema.  With
    ``require_complete_runs`` (default) refuse (SealError) when any run in orderbook_runs has no
    run_end with complete=true (interrupted, crashed, errored or still running); diagnostics
    pass False."""
    entries = verify_parts(out)
    if require_complete_runs:
        runs = read_table_unified(out, "orderbook_runs", sorted(
            Path(out, rel) for rel in entries if rel.split("/", 1)[0] == "orderbook_runs")).to_pylist()
        ok = {r["run_id"] for r in runs if r["event"] == "run_end" and r["complete"] is True}
        bad = sorted({r["run_id"] for r in runs} - ok)
        if bad:
            raise SealError(f"{len(bad)} run(s) without run_end complete=true "
                            f"(first: {bad[0]}); pass require_complete_runs=False for diagnostics")
    files = sorted(Path(out, rel) for rel in entries if rel.split("/", 1)[0] == kind)
    return read_table_unified(out, kind, files)


def git_sha() -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent,
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def list_open_series(client, s):
    """All OPEN, non-combo markets of one series.  Any HTTP error on ANY page raises, so a
    half-read listing is never used (page 1 is discarded)."""
    found, combos, pages = {}, 0, 0
    for page in paginate_pages(client, "/markets", {"series_ticker": s, "status": "open"}, "markets"):
        pages += 1
        for m in page:
            t = m.get("ticker")
            if not t:
                continue
            if is_mve(m):
                combos += 1
                continue
            found[t] = {"series": s, "close": to_unix(m.get("close_time"))}
    return found, combos, pages


def discover_open_markets(client, series_list, prev: dict, fails: dict, max_fails: int,
                          stop=lambda: False):
    """-> (new_markets, reports) or None if ``stop()`` fired between series.  A failed series
    keeps its previously known markets (carried) until ``max_fails`` consecutive failures, then
    they are dropped; either way a report row says so.  ``fails`` is updated in place."""
    new, reports = {}, []
    for s in series_list:
        if stop():
            return None
        try:
            found, combos, pages = list_open_series(client, s)
        except HttpError:
            fails[s] = fails.get(s, 0) + 1
            carried = {t: v for t, v in prev.items() if v["series"] == s}
            drop = fails[s] >= max_fails
            if not drop:
                new.update(carried)
            reports.append({"series_ticker": s, "ok": False, "n_open": 0, "combos_dropped": 0,
                            "pages": 0, "consecutive_failures": fails[s],
                            "carried_dropped": bool(drop and carried), "n_carried": len(carried)})
            continue
        fails[s] = 0
        new.update(found)
        reports.append({"series_ticker": s, "ok": True, "n_open": len(found), "combos_dropped": combos,
                        "pages": pages, "consecutive_failures": 0, "carried_dropped": False,
                        "n_carried": 0})
    return new, reports


def plan_cadence(n_open: int, every_s: float, disc_every_s: float, disc_reqs: float, rate: float):
    """Return (every_eff, disc_eff, stretched, requested_rps).  Discovery may take at most half
    of ``rate``; the snapshot cadence is stretched so snaps/s + discovery/s <= rate."""
    requested = n_open / every_s + disc_reqs / disc_every_s
    disc_eff = max(disc_every_s, disc_reqs / (0.5 * rate)) if disc_reqs > 0 else disc_every_s
    avail = rate - (disc_reqs / disc_eff if disc_reqs > 0 else 0.0)
    every_eff = every_s
    if n_open > 0 and n_open / every_s > avail:
        every_eff = n_open / avail
    stretched = requested > rate * (1 + 1e-9)
    return every_eff, disc_eff, stretched, requested


def _cmd_orderbook_snap_series(client, store, args, log, clock, sleep, utcnow):
    try:
        lock = acquire_lock(store.out)
    except LockHeld:
        log("orderbook-snap: another recorder holds the lock on this --out; exiting (5)")
        raise SystemExit(5)
    try:
        return _orderbook_snap_series_locked(client, store, args, log, clock, sleep, utcnow)
    finally:
        lock.close()


def _orderbook_snap_series_locked(client, store, args, log, clock, sleep, utcnow):
    want = [x.strip() for x in args.series.split(",") if x.strip()]
    series_list = select_series(client, argparse.Namespace(
        categories=args.categories, allow_series=args.allow_series, series=",".join(want)), log)
    if not series_list:
        raise SystemExit("no requested series passed the category/sports filter (see --allow-series)")
    if args.discover_every_s <= 0:
        raise SystemExit("--discover-every-s must be > 0")
    max_fails = max(1, int(getattr(args, "max_discover_failures", 3)))
    rate = 1.0 / client.limiter.interval
    run_id = utcnow().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    t0 = clock()
    bufs = {k: [] for k in SEALED_KINDS}
    pending: list[Path] = []
    n_flush = 0
    stop = {"flag": False}

    def flush():
        nonlocal n_flush
        part = f"{run_id}-{n_flush}"
        for kind, rows in bufs.items():
            if not rows:
                continue
            if kind in ("orderbook", "orderbook_snaps"):
                pending.extend(p for p in store.write_partitioned(kind, rows, part, "ts") if p)
            else:
                p = store.write(kind, rows, part)
                if p:
                    pending.append(p)
            bufs[kind] = []
        n_flush += 1

    def seal(hour):
        if not pending:
            return
        try:
            wrote = seal_parts(store.out, hour, list(pending))
            pending.clear()
            log(f"seal: hour={hour} wrote={int(wrote)} chain_lines={len(_chain_lines(store.out))} "
                f"chain_head={chain_head(store.out)}")
        except OSError as e:  # parts stay pending and are retried at the next seal
            log(f"seal failed ({type(e).__name__}); will retry")

    def nap(until):
        while not stop["flag"]:
            left = until - clock()
            if left <= 0:
                return
            sleep(min(left, 1.0))

    def run_row(event, **kw):
        base = {"run_id": run_id, "event": event, "ts": utcnow(), "series_json": jdump(series_list),
                "every_s": float(args.every_s), "depth": int(args.depth), "rate": rate,
                "discover_every_s": float(args.discover_every_s),
                "strict_cadence": bool(getattr(args, "strict_cadence", False)),
                "max_discover_failures": max_fails}
        base.update(kw)
        return base

    # run_start is on disk (and queued for sealing) before any listing or snapshot request.
    bufs["orderbook_runs"].append(run_row("run_start", git_sha=git_sha()))
    flush()

    old = None
    try:
        old = signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("flag", True))
    except ValueError:  # not the main thread
        pass
    open_m: dict[str, dict] = {}
    first_seen: dict[str, object] = {}
    fails: dict[str, int] = {}
    disc_reqs, every_eff, disc_eff, stretched = float(len(series_list)), args.every_s, args.discover_every_s, False
    next_disc, next_snap, i, n_snaps, n_disc = t0, t0, 0, 0, 0
    cur_hour = utcnow().strftime("%Y-%m-%dT%H")
    reason, note = "duration", ""
    ever_ok: set[str] = set()
    try:
        while not stop["flag"]:
            now = clock()
            if now - t0 > args.duration_s:
                break
            hk = utcnow().strftime("%Y-%m-%dT%H")
            if hk != cur_hour:  # hourly seal: files closed in the ended hour (boundary flush included)
                flush()
                seal(cur_hour)
                cur_hour = hk
            if now >= next_disc:
                r0 = client.requests_made
                res = discover_open_markets(client, series_list, open_m, fails, max_fails,
                                            stop=lambda: stop["flag"])
                if res is None:
                    break
                new, reports = res
                disc_reqs = float(max(1, client.requests_made - r0))
                stamp = utcnow()
                for r in reports:
                    bufs["orderbook_discoveries"].append({"run_id": run_id, "ts": stamp, **r})
                added, dropped = len(set(new) - set(open_m)), len(set(open_m) - set(new))
                for t in new:
                    first_seen.setdefault(t, stamp)
                for t in set(first_seen) - set(new):
                    del first_seen[t]
                open_m, n_disc = new, n_disc + 1
                every_eff, disc_eff, stretched, req = plan_cadence(
                    len(open_m), args.every_s, args.discover_every_s, disc_reqs, rate)
                if stretched:
                    msg = (f"request budget: {len(open_m)} open markets, requested {req:.3f} req/s "
                           f"> --rate {rate:g}")
                    if getattr(args, "strict_cadence", False):
                        log(msg + "; refusing (--strict-cadence)")
                        raise SystemExit(2)
                    log(msg + f"; stretching --every-s {args.every_s:g} -> {every_eff:.1f}")
                    if disc_eff > args.discover_every_s:
                        log(f"stretching discover interval {args.discover_every_s:g} -> {disc_eff:.1f}")
                log(f"discover: series={len(series_list)} ok={sum(r['ok'] for r in reports)} "
                    f"failed={sum(not r['ok'] for r in reports)} open={len(open_m)} added={added} "
                    f"dropped={dropped} requests={int(disc_reqs)}")
                ever_ok.update(r["series_ticker"] for r in reports if r["ok"])
                next_disc = now + disc_eff
                if any(not r["ok"] and r["series_ticker"] not in ever_ok for r in reports):
                    # a series with no listing yet: retry sooner, never above the discovery rate guard
                    floor = disc_reqs / (0.5 * rate) if disc_reqs > 0 else 0.0
                    next_disc = now + max(min(60.0, args.discover_every_s), floor)
            if now >= next_snap:
                ok, errs, skipped = 0, {}, 0
                for t, info in list(open_m.items()):
                    if stop["flag"] or clock() - t0 > args.duration_s:
                        break
                    if info["close"] is not None and utcnow().timestamp() >= info["close"]:
                        skipped += 1  # listed close_time already passed: do not snap
                        continue
                    req_start, r0 = utcnow(), client.requests_made
                    try:
                        ob = client.get(f"/markets/{t}/orderbook", {"depth": args.depth})
                    except HttpError as e:
                        errs[e.status] = errs.get(e.status, 0) + 1
                        bufs["orderbook_fetch_errors"].append({
                            "run_id": run_id, "ts": utcnow(), "ticker": t, "series_ticker": info["series"],
                            "status": e.status, "attempts": client.requests_made - r0})
                        continue
                    ts = utcnow()  # AFTER the response returned
                    rows, snap = snapshot_rows(t, ob, ts, first_seen.get(t), req_start,
                                               client.requests_made - r0,
                                               parse_server_date(client.last_date))
                    bufs["orderbook"] += rows
                    bufs["orderbook_snaps"].append(snap)
                    ok += 1
                n_snaps += ok
                i += 1
                bufs["orderbook_rounds"].append({
                    "run_id": run_id, "round": i, "ts": utcnow(), "n_open": len(open_m),
                    "n_snaps_ok": ok, "n_errors": sum(errs.values()),
                    "errors_json": jdump({str(k): v for k, v in sorted(errs.items())}),
                    "n_skipped_closed": skipped, "every_eff": float(every_eff), "stretched": stretched})
                next_snap = max(next_snap + every_eff, clock())
                log(f"round {i}: open={len(open_m)} ok={ok} errors={dict(sorted(errs.items()))} "
                    f"skipped_closed={skipped} requests={client.requests_made}")
                if len(bufs["orderbook_snaps"]) >= 200:
                    flush()
            nap(min(next_snap, next_disc, t0 + args.duration_s + 1e-9))
        if stop["flag"]:
            reason = "sigterm"
    except MaxRequestsReached:
        reason = "max_requests"
        raise
    except KeyboardInterrupt:
        reason = "interrupt"
        raise
    except SystemExit:
        reason, note = "error", "refused"
        raise
    except Exception as e:
        reason, note = "error", type(e).__name__
        raise
    finally:
        if old is not None:
            signal.signal(signal.SIGTERM, old)
        flush()
        bufs["orderbook_runs"].append(run_row(
            "run_end", stop_reason=reason, complete=(reason == "duration"), note=note,
            n_rounds=i, n_snaps=n_snaps, requests=client.requests_made, chain_head=chain_head(store.out)))
        flush()
        seal(cur_hour)
    log(f"orderbook rounds: {i}, discoveries: {n_disc}, snaps: {n_snaps}, requests: "
        f"{client.requests_made}, stop: {reason}")
    return True


def cmd_orderbook_snap(client, store, args, log, clock=time.time, sleep=time.sleep, utcnow=now_utc):
    if args.every_s <= 0 or args.duration_s <= 0:
        raise SystemExit("--every-s and --duration-s must be > 0")
    if getattr(args, "series", None):
        if args.markets:
            raise SystemExit("--markets and --series are mutually exclusive")
        return _cmd_orderbook_snap_series(client, store, args, log, clock, sleep, utcnow)
    tickers = [m.strip() for m in args.markets.split(",") if m.strip()]
    allowed = set(select_series(client, argparse.Namespace(
        categories=args.categories, allow_series=args.allow_series,
        series=",".join(sorted({series_of(t) for t in tickers}))), log))
    tickers = [t for t in tickers if series_of(t) in allowed and not is_mve({"ticker": t})]
    run_id = utcnow().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
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
            errs: dict = {}
            for t in tickers:
                try:
                    ob = client.get(f"/markets/{t}/orderbook", {"depth": args.depth})
                except HttpError as e:
                    errs[e.status] = errs.get(e.status, 0) + 1
                    continue
                rows, snap = snapshot_rows(t, ob, utcnow())
                ob_rows += rows
                snaps.append(snap)
            if errs:
                log(f"round {i + 1}: orderbook errors by status {dict(sorted(errs.items()))}")
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
    p.add_argument("--flush-pages", type=int, default=50,
                   help="write parquet every N pages instead of holding a whole unit in RAM")
    p.add_argument("--allow-series", default=None,
                   help="comma list of series tickers that bypass the sports category/keyword "
                        "filters; recorded in the manifest query")
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
    m.add_argument("--raw-json", action="store_true", help="also store each market's raw JSON (large)")

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
    g = o.add_mutually_exclusive_group(required=True)
    g.add_argument("--markets", help="fixed comma list of market tickers")
    g.add_argument("--series", help="comma list of series; follows their OPEN markets as they roll")
    o.add_argument("--discover-every-s", type=float, default=300,
                   help="--series mode: re-list open markets this often")
    o.add_argument("--max-discover-failures", type=int, default=3,
                   help="--series mode: after N consecutive failed listings of a series, drop its "
                        "carried markets (recorded in orderbook_discoveries)")
    o.add_argument("--strict-cadence", action="store_true",
                   help="--series mode: refuse instead of stretching when the request budget exceeds --rate")
    o.add_argument("--every-s", type=float, default=60)
    o.add_argument("--duration-s", type=float, required=True)
    o.add_argument("--depth", type=int, default=0)
    q = sub.add_parser("quarantine-unsealed",
                       help="move parts not covered by the chain to <out>/_quarantine/<utc-stamp>/")
    q.add_argument("--out", default=argparse.SUPPRESS)
    return p


QUERY_KEYS = ("categories", "allow_series", "all_categories", "status", "series", "since", "until", "market",
              "period", "markets", "every_s", "duration_s", "depth", "raw_json",
              "discover_every_s", "strict_cadence", "max_discover_failures")
COMMANDS = {"series": cmd_series, "markets": cmd_markets, "trades": cmd_trades,
            "candles": cmd_candles, "orderbook-snap": cmd_orderbook_snap}


def main(argv=None, client: Client | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = lambda s: print(s, file=sys.stderr)
    if args.cmd == "quarantine-unsealed":
        try:
            lock = acquire_lock(args.out)
        except LockHeld:
            log("quarantine-unsealed: a recorder holds the lock on this --out; exiting (5)")
            return 5
        try:
            r = quarantine_unsealed(args.out)
        except SealError as e:
            log(f"refused: {e}")
            return 4
        finally:
            lock.close()
        log(f"quarantined {r['moved']} part(s)" + (f" -> {r['dir']}" if r["dir"] else ""))
        return 0
    client = client or Client(rate=args.rate, max_requests=args.max_requests)
    query = {k: getattr(args, k) for k in QUERY_KEYS if hasattr(args, k)}
    store = Store(args.out, args.cmd, query)
    if args.flush_pages < 1:
        raise SystemExit("--flush-pages must be >= 1")
    try:
        COMMANDS[args.cmd](client, store, args, log)
    except MaxRequestsReached as e:
        store.save_checkpoint()
        store.run_record(client, complete=False, note=str(e))
        log(f"stopped: {e}; re-run the same command to resume")
        return 3
    except Exception as e:  # terminal 4xx / exhausted retries / bug: leave a trace, then fail
        store.save_checkpoint()
        store.run_record(client, complete=False, error=f"{type(e).__name__}: {e}")
        log(f"failed: {type(e).__name__}: {e}; checkpoint kept")
        if isinstance(e, HttpError):
            return 4
        raise
    store.save_checkpoint()
    store.run_record(client, complete=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
