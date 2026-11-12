"""Kalshi order client for the LIP live lane (OFFLINE core).

Two modes, one class:

* DRY-RUN (default): logs the exact request it WOULD send (method, URL, body, and the NAMES of the auth
  headers, never a value) and returns a :class:`DryRun`.  It holds no key, signs nothing, never calls the
  transport.
* LIVE: only :meth:`KalshiClient.from_armed`, which needs an :class:`kalshi_live_gate.ArmedContext`
  (``--live`` + LIVE-ARMED sha256 + 0600 key dir).  It signs each attempt with a fresh timestamp.

The HTTP transport is injectable (``transport(HttpRequest) -> HttpResponse``).  Tests use mocks; the only
code that can reach a socket is :func:`urllib_transport`, which nothing in this PR calls.

Retries: GET and DELETE retry on 429, 5xx and network errors (status 0) with capped exponential backoff.
POST (create order) retries on 429 only (the order was not processed); a 5xx or network error on a POST is
ambiguous and raises :class:`AmbiguousOrder`: the caller reconciles from the exchange, it does not resend.

MAL is paper-only today.  Not to be merged before a DEC amendment approved by the owner allows a Kalshi
key on a host.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlparse

import kalshi_live_net as net
import kalshi_live_orders as ko
import kalshi_live_sign as ks
from kalshi_live_gate import ArmedContext
from kalshi_live_log import EventLog

PLACEHOLDER_BASE_URL = "https://kalshi-base-url-unset.invalid/trade-api/v2"  # dry-run default; never resolves
MAX_PAGES = 20
PAGE_LIMIT = 100
TIMEOUT_S = 10.0


class ApiError(Exception):
    def __init__(self, status: int, code: str | None, message: str):
        super().__init__(f"HTTP {status} {code or ''}".strip())
        self.status, self.code, self.message = status, code, message


class AmbiguousOrder(Exception):
    """A POST got a 5xx or no answer: the order may or may not exist.  Reconcile from the exchange."""


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict = field(repr=False)  # carries auth values in live mode: never logged, never repr'd
    body: bytes | None = None
    timeout: float = TIMEOUT_S


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes = b""
    headers: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DryRun:
    op: str
    method: str
    url: str
    body: dict | None
    sent: bool = False


def urllib_transport(req: HttpRequest) -> HttpResponse:
    """The only network-capable function in the live core.  Status 0 = network error."""
    r = urllib.request.Request(req.url, data=req.body, method=req.method, headers=dict(req.headers))
    try:
        with urllib.request.urlopen(r, timeout=req.timeout) as resp:
            return HttpResponse(resp.status, resp.read(), dict(resp.headers))
    except urllib.error.HTTPError as e:
        return HttpResponse(e.code, e.read(), dict(e.headers or {}))
    except (urllib.error.URLError, TimeoutError, OSError):
        return HttpResponse(0, b"", {})


def _now_ms() -> int:
    return int(time.time() * 1000)


class KalshiClient:
    def __init__(self, log: EventLog, base_url: str = PLACEHOLDER_BASE_URL, *, armed: ArmedContext | None = None,
                 private_key=None, transport=None, limiter: net.TokenBucket | None = None, now_ms=_now_ms,
                 sleep=time.sleep, max_retries: int = 4, backoff_base: float = 1.0, backoff_cap: float = 60.0,
                 jitter: float = 0.25, rng=random.random):
        self.log = log
        self.armed = armed
        self.live = armed is not None
        if self.live:
            if not isinstance(armed, ArmedContext) or private_key is None or transport is None:
                raise ValueError("live mode needs an ArmedContext, a private key and a transport")
            base_url = armed.base_url
            log.add_forbidden(armed.key_id)
        elif private_key is not None:
            raise ValueError("dry-run holds no key")
        self.base_url = base_url.rstrip("/")
        self._base_path = urlparse(self.base_url).path
        self._key = private_key
        self.transport = transport
        self.limiter = limiter or net.TokenBucket(rate=5.0, burst=5.0, sleep=sleep)
        self.now_ms, self.sleep = now_ms, sleep
        self.max_retries, self.backoff_base, self.backoff_cap = max_retries, backoff_base, backoff_cap
        self.jitter, self.rng = jitter, rng

    @classmethod
    def from_armed(cls, armed: ArmedContext, log: EventLog, transport=None, **kw) -> "KalshiClient":
        key = ks.load_private_key(armed.key_path)
        return cls(log, armed=armed, private_key=key, transport=transport or urllib_transport, **kw)

    # ------------------------------------------------------------------ core

    def _call(self, op: str, method: str, endpoint: str, query: dict | None = None, body: dict | None = None):
        qs = urlencode(sorted((k, str(v)) for k, v in (query or {}).items() if v is not None))
        url = self.base_url + endpoint + (("?" + qs) if qs else "")
        sign_path = self._base_path + endpoint  # query string is NOT signed
        data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode() if body is not None else None
        if not self.live:
            self.log.emit("dry_run_request", op=op, method=method, url=url, body=body,
                          auth_headers=list(ks.AUTH_HEADER_NAMES), sent=False)
            return DryRun(op, method, url, body)
        for attempt in range(self.max_retries + 1):
            self.limiter.acquire()
            headers = ks.auth_headers(self.armed.key_id, self._key, method, sign_path, self.now_ms())
            self.log.add_forbidden(headers[ks.HDR_SIG])
            headers.update({"Accept": "application/json"})
            if data is not None:
                headers["Content-Type"] = "application/json"
            self.log.emit("request", op=op, method=method, url=url, attempt=attempt, body=body)
            resp = self.transport(HttpRequest(method, url, headers, data))
            if 200 <= resp.status < 300:
                self.log.emit("response", op=op, status=resp.status)
                return self._json(resp)
            code, msg = ko.parse_error(resp.body)
            retry_after = net.parse_retry_after(resp.headers)
            transient = resp.status == 429 or resp.status >= 500 or resp.status == 0
            if method == "POST" and resp.status != 429 and transient:
                self.log.emit("ambiguous_order", op=op, status=resp.status)
                raise AmbiguousOrder(f"{op}: status {resp.status}; reconcile from the exchange, do not resend")
            if not transient or attempt == self.max_retries:
                self.log.emit("api_error", op=op, status=resp.status, code=code, message=msg, attempt=attempt)
                raise ApiError(resp.status, code, msg)
            delay = net.backoff_delay(attempt, self.backoff_base, self.backoff_cap, self.jitter, self.rng, retry_after)
            self.log.emit("retry", op=op, status=resp.status, attempt=attempt, delay=round(delay, 3))
            self.sleep(delay)
        raise AssertionError("unreachable")

    @staticmethod
    def _json(resp: HttpResponse):
        if not resp.body:
            return {}
        try:
            return json.loads(resp.body)
        except ValueError:
            raise ko.ParseError("2xx response is not JSON") from None

    # ------------------------------------------------------------------ orders

    def create_order(self, intent: ko.OrderIntent, tick=ko.DEFAULT_TICK):
        body = ko.create_order_body(intent, tick)
        r = self._call("create_order", "POST", ko.EP_ORDERS, body=body)
        if isinstance(r, DryRun):
            return r
        ack = ko.parse_order_ack(r, expect_client_order_id=intent.client_order_id)
        self.log.emit("order_placed", ticker=intent.ticker, outcome=intent.outcome, price=intent.price,
                      count=intent.count, client_order_id=intent.client_order_id, order_id=ack.order_id,
                      status=ack.status)
        return ack

    def cancel_order(self, order_id: str):
        if not isinstance(order_id, str) or not ko._ID.match(order_id):
            raise ko.OrderError("bad order id")
        r = self._call("cancel_order", "DELETE", ko.EP_ORDER.format(order_id=order_id))
        if isinstance(r, DryRun):
            return r
        res = ko.parse_cancel(r)
        self.log.emit("cancel", order_id=order_id, cancelled=list(res.order_ids))
        return res

    def cancel_all(self):
        r = self._call("cancel_all", "DELETE", ko.EP_ORDERS)
        if isinstance(r, DryRun):
            return r
        res = ko.parse_cancel(r)
        self.log.emit("cancel_all", cancelled=list(res.order_ids), n=len(res.order_ids))
        return res

    # ------------------------------------------------------------------ reads

    def get_balance(self):
        r = self._call("get_balance", "GET", ko.EP_BALANCE)
        return r if isinstance(r, DryRun) else ko.parse_balance(r)

    def _paged(self, op: str, endpoint: str, query: dict, parse):
        out, cursor = [], None
        for _ in range(MAX_PAGES):
            r = self._call(op, "GET", endpoint, query={**query, "limit": PAGE_LIMIT, "cursor": cursor})
            if isinstance(r, DryRun):
                return r
            rows, cursor = parse(r)
            out += rows
            if not cursor:
                return out
        raise ko.ParseError(f"{op}: more than {MAX_PAGES} pages")

    def get_positions(self):
        return self._paged("get_positions", ko.EP_POSITIONS, {},
                           lambda r: (ko.parse_positions(r), r.get("cursor") or None))

    def get_resting_orders(self):
        return self._paged("get_resting_orders", ko.EP_ORDERS, {"status": "resting"},
                           lambda r: (ko.parse_resting_orders(r), r.get("cursor") or None))

    def get_fills(self, ticker: str | None = None, min_ts: int | None = None):
        return self._paged("get_fills", ko.EP_FILLS, {"ticker": ticker, "min_ts": min_ts}, ko.parse_fills)
