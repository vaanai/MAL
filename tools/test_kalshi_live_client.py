import json
import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pytest

pytest.importorskip("cryptography")
import kalshi_live_client as kc
import kalshi_live_gate as kg
import kalshi_live_log as kl
import kalshi_live_orders as ko
import kalshi_live_sign as ks
import kalshi_live_testutil as tu

BASE = tu.FAKE_BASE_URL


@pytest.fixture(scope="module")
def key():
    return tu.make_rsa_key()


def intent(**kw):
    base = dict(ticker="KXGAS-26OCT-B3.50", outcome="yes", price=D("0.42"), count=D("25"),
                client_order_id="lip-0001", expiration_ts=1_800_000_450, order_group_id="grp-1")
    base.update(kw)
    return ko.OrderIntent(**base)


class Mock:
    """Scripted transport: replies is a list of (status, obj_or_bytes, headers) consumed in order."""

    def __init__(self, replies):
        self.replies, self.reqs = list(replies), []

    def __call__(self, req):
        self.reqs.append(req)
        rep = self.replies.pop(0)
        st, body, hdr = rep if len(rep) == 3 else (*rep, {})
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        return kc.HttpResponse(st, body, hdr)


def armed_ctx(tmp_path, key):
    d, _ = tu.make_key_dir(tmp_path / "kd", key=key)
    return kg.ArmedContext(key_id=tu.FAKE_KEY_ID, base_url=BASE, env="demo", key_path=d / "kalshi-private.pem",
                           declaration_sha256="0" * 64)


def live(tmp_path, key, replies, **kw):
    log = kl.EventLog(tmp_path / "ev.jsonl")
    m = Mock(replies)
    c = kc.KalshiClient.from_armed(armed_ctx(tmp_path, key), log, transport=m, sleep=lambda s: None,
                                   now_ms=kw.pop("now_ms", lambda: 1_800_000_000_000), rng=lambda: 0.0, **kw)
    return c, m, log


# ------------------------------------------------------------------ dry-run is the default
def test_dry_run_logs_exact_request_and_sends_nothing(tmp_path):
    log = kl.EventLog(tmp_path / "ev.jsonl")

    def boom(req):
        raise AssertionError("dry-run must never call the transport")
    c = kc.KalshiClient(log, BASE, transport=boom)
    assert not c.live
    r = c.create_order(intent())
    assert isinstance(r, kc.DryRun) and r.sent is False
    assert r.method == "POST" and r.url == BASE + "/portfolio/events/orders"
    ev = log.read()[0]
    assert ev["event"] == "dry_run_request" and ev["sent"] is False and ev["body"] == ko.create_order_body(intent())
    assert ev["auth_headers"] == ["KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP", "KALSHI-ACCESS-SIGNATURE"]
    assert "headers" not in ev and "signature" not in json.dumps(ev).lower().replace("kalshi-access-signature", "")


def test_dry_run_covers_every_wrapper_without_a_key(tmp_path):
    log = kl.EventLog(tmp_path / "ev.jsonl")
    c = kc.KalshiClient(log)
    outs = [c.cancel_order("o-1"), c.cancel_all(), c.get_balance(), c.get_positions(), c.get_resting_orders(),
            c.get_fills(ticker="T", min_ts=5)]
    assert all(isinstance(o, kc.DryRun) for o in outs)
    assert [(o.method, o.url.split("/trade-api/v2")[1]) for o in outs] == [
        ("DELETE", "/portfolio/events/orders/o-1"), ("DELETE", "/portfolio/events/orders"),
        ("GET", "/portfolio/balance"), ("GET", "/portfolio/positions?limit=100"),
        ("GET", "/portfolio/events/orders?limit=100&status=resting"),
        ("GET", "/portfolio/fills?limit=100&min_ts=5&ticker=T")]


def test_dry_run_refuses_a_key_and_live_needs_armed_context(tmp_path, key):
    log = kl.EventLog(tmp_path / "ev.jsonl")
    with pytest.raises(ValueError):
        kc.KalshiClient(log, BASE, private_key=key)
    with pytest.raises(ValueError):
        kc.KalshiClient(log, armed="yes", private_key=key, transport=lambda r: None)
    with pytest.raises(ValueError):
        kc.KalshiClient(log, armed=armed_ctx(tmp_path, key), private_key=None, transport=lambda r: None)


# ------------------------------------------------------------------ live signing, requests, parsing
def test_live_create_order_signs_and_parses(tmp_path, key):
    ack = {"order": {"order_id": "o-9", "client_order_id": "lip-0001", "status": "resting", "ticker": "KXGAS-26OCT-B3.50"}}
    c, m, log = live(tmp_path, key, [(201, ack)])
    r = c.create_order(intent())
    assert r == ko.OrderAck("o-9", "lip-0001", "resting", "KXGAS-26OCT-B3.50")
    req = m.reqs[0]
    assert req.method == "POST" and req.url == BASE + "/portfolio/events/orders"
    assert json.loads(req.body) == ko.create_order_body(intent())
    h = req.headers
    assert h["KALSHI-ACCESS-KEY"] == tu.FAKE_KEY_ID and h["KALSHI-ACCESS-TIMESTAMP"] == "1800000000000"
    msg = b"1800000000000POST/trade-api/v2/portfolio/events/orders"
    assert ks.verify_message(key.public_key(), msg, h["KALSHI-ACCESS-SIGNATURE"])


def test_query_string_not_signed_and_get_has_no_body(tmp_path, key):
    c, m, _ = live(tmp_path, key, [(200, {"fills": [], "cursor": ""})])
    assert c.get_fills(ticker="T") == []
    req = m.reqs[0]
    assert req.url.endswith("/portfolio/fills?limit=100&ticker=T") and req.body is None
    assert ks.verify_message(key.public_key(), b"1800000000000GET/trade-api/v2/portfolio/fills",
                             req.headers["KALSHI-ACCESS-SIGNATURE"])


def test_cancel_and_cancel_all_methods_and_paths(tmp_path, key):
    c, m, _ = live(tmp_path, key, [(200, {"order": {"order_id": "o-1"}}), (200, {"cancelled_order_ids": ["a", "b"]})])
    assert c.cancel_order("o-1").order_ids == ("o-1",)
    assert c.cancel_all().order_ids == ("a", "b")
    assert [(r.method, r.url.split("/trade-api/v2")[1]) for r in m.reqs] == [
        ("DELETE", "/portfolio/events/orders/o-1"), ("DELETE", "/portfolio/events/orders")]
    with pytest.raises(ko.OrderError):
        c.cancel_order("../x")


def test_reads_parse_and_paginate(tmp_path, key):
    c, m, _ = live(tmp_path, key, [
        (200, {"balance": 25000}),
        (200, {"market_positions": [{"ticker": "A", "position": 3, "market_exposure": 120}], "cursor": "c2"}),
        (200, {"market_positions": [{"ticker": "B", "position": -4}], "cursor": ""}),
        (200, {"orders": [{"order_id": "o1", "ticker": "A", "side": "ask", "price": "0.7000", "remaining_count": "5.00"}]}),
        (200, {"fills": [{"trade_id": "t", "ticker": "A", "side": "bid", "price": "0.3000", "count": "2.00"}], "cursor": ""})])
    assert c.get_balance() == D("250")
    assert c.get_positions() == [ko.Position("A", D(3), D("1.2")), ko.Position("B", D(-4), None)]
    assert "cursor=c2" in m.reqs[2].url
    assert c.get_resting_orders()[0].outcome == "no"
    fills = c.get_fills()
    assert fills[0].price == D("0.30") and fills[0].outcome == "yes"


# ------------------------------------------------------------------ retries and errors
def test_get_retries_429_and_5xx_with_fresh_signature_each_time(tmp_path, key):
    ts = iter(range(1_800_000_000_000, 1_800_000_009_000, 1000))
    c, m, log = live(tmp_path, key, [(429, {}, {"Retry-After": "3"}), (503, b"x"), (200, {"balance": 100})],
                     now_ms=lambda: next(ts))
    assert c.get_balance() == D(1)
    assert len(m.reqs) == 3
    stamps = [r.headers["KALSHI-ACCESS-TIMESTAMP"] for r in m.reqs]
    sigs = [r.headers["KALSHI-ACCESS-SIGNATURE"] for r in m.reqs]
    assert len(set(stamps)) == 3 and len(set(sigs)) == 3
    for r in m.reqs:
        msg = (r.headers["KALSHI-ACCESS-TIMESTAMP"] + "GET/trade-api/v2/portfolio/balance").encode()
        assert ks.verify_message(key.public_key(), msg, r.headers["KALSHI-ACCESS-SIGNATURE"])
    delays = [e["delay"] for e in log.read() if e["event"] == "retry"]
    assert delays == [3.0, 2.0]  # Retry-After floor, then 2**1


def test_get_gives_up_after_max_retries(tmp_path, key):
    c, m, _ = live(tmp_path, key, [(500, b"")] * 3, max_retries=2)
    with pytest.raises(kc.ApiError) as ei:
        c.get_balance()
    assert ei.value.status == 500 and len(m.reqs) == 3


def test_4xx_is_not_retried_and_reports_code(tmp_path, key):
    c, m, log = live(tmp_path, key, [(400, {"error": {"code": "post_only_cross", "message": "would cross"}})])
    with pytest.raises(kc.ApiError) as ei:
        c.create_order(intent())
    assert (ei.value.status, ei.value.code, len(m.reqs)) == (400, "post_only_cross", 1)
    assert [e["event"] for e in log.read()][-1] == "api_error"


def test_post_retries_429_only_and_never_resends_after_5xx_or_network_error(tmp_path, key):
    ack = {"order": {"order_id": "o-1", "client_order_id": "lip-0001", "status": "resting"}}
    c, m, _ = live(tmp_path, key, [(429, {}), (201, ack)])
    assert c.create_order(intent()).order_id == "o-1" and len(m.reqs) == 2
    for st in (500, 502, 0):
        c, m, log = live(tmp_path, key, [(st, b""), (201, ack)])
        with pytest.raises(kc.AmbiguousOrder):
            c.create_order(intent())
        assert len(m.reqs) == 1  # no second POST
        assert log.read()[-1]["event"] == "ambiguous_order"


def test_non_json_2xx_and_echo_mismatch_raise_parse_error(tmp_path, key):
    c, _, _ = live(tmp_path, key, [(200, b"<html>")])
    with pytest.raises(ko.ParseError):
        c.get_balance()
    c, _, _ = live(tmp_path, key, [(201, {"order": {"order_id": "o", "client_order_id": "someone-else"}})])
    with pytest.raises(ko.ParseError):
        c.create_order(intent())


def test_rate_limiter_is_used_before_every_attempt(tmp_path, key):
    class Lim:
        n = 0

        def acquire(self, cost=1.0):
            Lim.n += 1
    c, _, _ = live(tmp_path, key, [(503, b""), (200, {"balance": 0})], limiter=Lim())
    c.get_balance()
    assert Lim.n == 2


# ------------------------------------------------------------------ no credential material in the log
def test_log_never_contains_key_id_or_any_signature(tmp_path, key):
    ack = {"order": {"order_id": "o-9", "client_order_id": "lip-0001", "status": "resting"}}
    c, m, log = live(tmp_path, key, [(429, {}), (201, ack), (200, {"cancelled_order_ids": ["o-9"]}),
                                     (400, {"error": {"code": "bad", "message": f"key {tu.FAKE_KEY_ID} rejected"}})])
    c.create_order(intent())
    c.cancel_all()
    with pytest.raises(kc.ApiError):
        c.cancel_order("o-9")
    text = (tmp_path / "ev.jsonl").read_text()
    assert len(m.reqs) == 4 and text
    assert tu.FAKE_KEY_ID not in text
    for r in m.reqs:
        assert r.headers["KALSHI-ACCESS-SIGNATURE"] not in text
        assert r.headers["KALSHI-ACCESS-SIGNATURE"][:40] not in text
    assert "BEGIN" not in text and "PRIVATE" not in text
    assert repr(m.reqs[0]).count(tu.FAKE_KEY_ID) == 0  # HttpRequest.headers is repr=False


def test_urllib_transport_maps_errors_without_sockets(monkeypatch):
    import io
    import urllib.error

    class R:
        status = 200
        headers = {"Date": "x"}

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    seen = {}

    def fake(req, timeout):
        seen["req"], seen["timeout"] = req, timeout
        return R()
    monkeypatch.setattr(kc.urllib.request, "urlopen", fake)
    r = kc.urllib_transport(kc.HttpRequest("GET", "https://x.invalid/a", {"Accept": "a"}))
    assert r.status == 200 and seen["req"].get_method() == "GET" and seen["timeout"] == kc.TIMEOUT_S

    def http_err(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "x", {"Retry-After": "2"}, io.BytesIO(b"{}"))
    monkeypatch.setattr(kc.urllib.request, "urlopen", http_err)
    r = kc.urllib_transport(kc.HttpRequest("GET", "https://x.invalid/a", {}))
    assert r.status == 429 and r.headers.get("Retry-After") == "2"

    def net_err(req, timeout):
        raise urllib.error.URLError("down")
    monkeypatch.setattr(kc.urllib.request, "urlopen", net_err)
    assert kc.urllib_transport(kc.HttpRequest("GET", "https://x.invalid/a", {})).status == 0
