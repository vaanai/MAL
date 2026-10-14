"""Tests for tools/haiku_caller.py. No call to the Anthropic API: the transport function is replaced.

The one transport test that opens a socket talks only to a throwaway HTTP server on 127.0.0.1 inside the test; it checks the
real _http_post (status passthrough, timeout, no redirect following). The key in every test is a fake.
"""
from __future__ import annotations

import http.server
import json
import math
import os
import stat
import threading
import time
import traceback

import pytest

from tools import haiku_caller as hc

FAKE_KEY = "sk-ant-TEST-0123456789abcdef"
PROMPT_MARK = "PROMPT-MARKER-zq81"
SERVER_MSG_MARK = "SERVER-TEXT-MARKER-k77"
P = hc.PRICES[hc.DEFAULT_MODEL]


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv(hc.KEY_ENV, FAKE_KEY)


def ok_body(input_tokens=100, output_tokens=20, cache_creation=None, cache_read=None, text="hello", stop="end_turn") -> bytes:
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    if cache_creation is not None:
        usage["cache_creation_input_tokens"] = cache_creation
    if cache_read is not None:
        usage["cache_read_input_tokens"] = cache_read
    return json.dumps({"id": "msg_1", "type": "message", "role": "assistant", "model": hc.DEFAULT_MODEL,
                       "content": [{"type": "text", "text": text}], "stop_reason": stop, "usage": usage}).encode()


def err_body(etype="rate_limit_error", msg=SERVER_MSG_MARK) -> bytes:
    return json.dumps({"type": "error", "error": {"type": etype, "message": msg}}).encode()


class Transport:
    """Scripted stand-in for hc._http_post. Each script item is (status, headers, body), or an exception instance to raise."""

    def __init__(self, *script, before=None):
        self.script = list(script)
        self.calls: list[dict] = []
        self.before = before

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "body": body, "timeout": timeout})
        if self.before:
            self.before(len(self.calls))
        item = self.script.pop(0) if self.script else (200, {"request-id": "req_default"}, ok_body())
        if isinstance(item, BaseException):
            raise item
        return item


def install(monkeypatch, *script, **kw) -> Transport:
    t = Transport(*script, **kw)
    monkeypatch.setattr(hc, "_http_post", t)
    return t


def req(**over) -> dict:
    r = {"system": "you rank cards " + PROMPT_MARK, "messages": [{"role": "user", "content": "card: " + PROMPT_MARK}], "max_tokens": 200}
    r.update(over)
    return r


def caller(tmp_path, budget=1.0, sleeps=None, **kw) -> hc.HaikuCaller:
    sl = sleeps if sleeps is not None else []
    return hc.HaikuCaller(tmp_path / "ledger.jsonl", budget, sleep=sl.append, **kw)


def rows_of(tmp_path) -> list[dict]:
    p = tmp_path / "ledger.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def seed_ledger(tmp_path, *costs) -> None:
    with open(tmp_path / "ledger.jsonl", "a") as f:
        for c in costs:
            f.write(json.dumps({"cost_usd": c, "http_status": 200}) + "\n")


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# ---------------------------------------------------------------- success path and cost

def test_success_builds_request_and_writes_one_full_row(monkeypatch, tmp_path):
    t = install(monkeypatch, (200, {"request-id": "req_abc123"}, ok_body(input_tokens=1000, output_tokens=200, cache_creation=400, cache_read=5000)))
    res = caller(tmp_path).call(**req())
    c = t.calls[0]
    assert c["url"] == "https://api.anthropic.com/v1/messages" and c["timeout"] == 60
    assert c["headers"] == {"x-api-key": FAKE_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    body = json.loads(c["body"])
    assert body["model"] == "claude-haiku-5-5" and body["thinking"] == {"type": "disabled"} and body["max_tokens"] == 200
    assert c["body"] == json.dumps(body, sort_keys=True, separators=(",", ":")).encode()  # wire bytes are the hashed bytes
    # 1000*0.10 + 400*0.125 + 5000*0.01 + 200*0.50 = 300 micro-USD
    assert res.cost_usd == pytest.approx(3.0e-4, abs=1e-12) and res.spent_after == pytest.approx(3.0e-4, abs=1e-12)
    assert res.text == "hello" and res.attempts == 1 and not res.replayed
    (row,) = rows_of(tmp_path)
    assert row["model"] == "claude-haiku-5-5" and row["http_status"] == 200 and row["request_id"] == "req_abc123"
    assert row["stop_reason"] == "end_turn" and row["attempt"] == 1 and row["thinking"] == {"type": "disabled"}
    assert (row["input_tokens"], row["output_tokens"], row["cache_creation_input_tokens"], row["cache_read_input_tokens"]) == (1000, 200, 400, 5000)
    assert row["cost_usd"] == pytest.approx(3.0e-4, abs=1e-12)
    assert row["request_sha256"] == hc.sha256_hex(c["body"]) and row["response_sha256"] is not None
    assert isinstance(row["latency_ms"], int) and row["latency_ms"] >= 0
    assert row["error_class"] is None and row["error_type"] is None and row["allow_model"] is None
    assert row["ts"].endswith("Z")


def test_missing_usage_fields_count_as_zero_and_bad_ones_never_reduce_spend(monkeypatch, tmp_path):
    body = json.dumps({"content": [], "stop_reason": "end_turn", "usage": {"input_tokens": 100, "output_tokens": -50, "cache_read_input_tokens": "x"}}).encode()
    install(monkeypatch, (200, {}, body))
    res = caller(tmp_path).call(**req())
    assert res.cost_usd == pytest.approx(100 * 0.10 / 1e6)
    row = rows_of(tmp_path)[0]
    assert row["output_tokens"] == 0 and row["cache_creation_input_tokens"] == 0 and row["cache_read_input_tokens"] == 0


def test_thinking_default_off_and_passthrough_and_omit(monkeypatch, tmp_path):
    t = install(monkeypatch)
    c = caller(tmp_path)
    c.call(**req())
    c.call(**req(), thinking={"type": "enabled", "budget_tokens": 1024})
    c.call(**req(), thinking=None)
    bodies = [json.loads(x["body"]) for x in t.calls]
    assert bodies[0]["thinking"] == {"type": "disabled"}
    assert bodies[1]["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert "thinking" not in bodies[2]
    assert [r["thinking"] for r in rows_of(tmp_path)] == [{"type": "disabled"}, {"type": "enabled", "budget_tokens": 1024}, None]


def test_temperature_is_sent_only_when_given(monkeypatch, tmp_path):
    t = install(monkeypatch)
    c = caller(tmp_path)
    c.call(**req())
    c.call(**req(), temperature=0.0)
    assert "temperature" not in json.loads(t.calls[0]["body"]) and json.loads(t.calls[1]["body"])["temperature"] == 0.0


def test_bad_call_arguments_are_refused_before_any_call(monkeypatch, tmp_path):
    t = install(monkeypatch)
    c = caller(tmp_path)
    for bad in (dict(max_tokens=0), dict(max_tokens="5"), dict(max_tokens=True), dict(messages=[]), dict(temperature=2), dict(system=5)):
        with pytest.raises(hc.BadArguments):
            c.call(**req(**bad))
    with pytest.raises(hc.BadArguments):
        c.call(**req(), thinking="off")
    with pytest.raises(TypeError):  # max_tokens has no default
        c.call(messages=[{"role": "user", "content": "x"}])
    assert t.calls == [] and rows_of(tmp_path) == []


# ---------------------------------------------------------------- budget

def test_budget_refusal_before_the_call_appends_nothing(monkeypatch, tmp_path):
    t = install(monkeypatch)
    seed_ledger(tmp_path, 0.9)
    before = (tmp_path / "ledger.jsonl").read_bytes()
    big = req(max_tokens=1_000_000)  # worst case ~0.5 USD
    with pytest.raises(hc.BudgetExceeded) as ei:
        caller(tmp_path, budget=1.0).call(**big)
    assert ei.value.exit_code == 3
    assert t.calls == []
    assert (tmp_path / "ledger.jsonl").read_bytes() == before


def test_budget_boundary_equal_is_allowed_and_one_ulp_less_is_refused(monkeypatch, tmp_path):
    t = install(monkeypatch)
    r = req()
    worst = hc.worst_case_cost(r["system"], r["messages"], r["max_tokens"], P)
    seed_ledger(tmp_path, 0.25)
    exact = 0.25 + worst
    with pytest.raises(hc.BudgetExceeded):
        caller(tmp_path, budget=math.nextafter(exact, 0.0)).call(**r)
    assert t.calls == []
    caller(tmp_path, budget=exact).call(**r)
    assert len(t.calls) == 1


def test_budget_sums_across_two_ledger_instances(monkeypatch, tmp_path):
    t = install(monkeypatch, *[(200, {}, ok_body(input_tokens=0, output_tokens=400))] * 5)  # 400 * 0.5 / 1e6 = 2e-4 each
    r = req()
    worst = hc.worst_case_cost(r["system"], r["messages"], r["max_tokens"], P)
    budget = 2 * 2e-4 + worst - 1e-7  # two actual spends fit; at the third check spent + worst is just over
    a, b = caller(tmp_path, budget=budget), caller(tmp_path, budget=budget)
    a.call(**r)
    b.call(**r)
    with pytest.raises(hc.BudgetExceeded):
        a.call(**r)
    with pytest.raises(hc.BudgetExceeded):
        b.call(**r)
    assert len(t.calls) == 2 and len(rows_of(tmp_path)) == 2
    assert hc.ledger_summary(tmp_path / "ledger.jsonl")["total_cost_usd"] == pytest.approx(4e-4)


def test_concurrent_callers_cannot_overspend_the_budget(monkeypatch, tmp_path):
    """The check and the append sit under one flock with the HTTP call: 8 threads, each with its own fd, get exactly 4 calls."""
    r = req()
    worst = hc.worst_case_cost(r["system"], r["messages"], r["max_tokens"], P)
    actual = 2e-4  # output_tokens 400
    budget = 3 * actual + worst + actual / 2  # spent 0, 1, 2, 3 actual calls pass; the fifth check fails
    lock = threading.Lock()
    n_calls = []

    def slow_transport(url, headers, body, timeout):
        with lock:
            n_calls.append(1)
        time.sleep(0.02)
        return 200, {}, ok_body(input_tokens=0, output_tokens=400)

    monkeypatch.setattr(hc, "_http_post", slow_transport)
    outcomes = []

    def worker():
        try:
            hc.HaikuCaller(tmp_path / "ledger.jsonl", budget, sleep=lambda s: None).call(**r)
            outcomes.append("ok")
        except hc.BudgetExceeded:
            outcomes.append("budget")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert outcomes.count("ok") == 4 and outcomes.count("budget") == 4
    assert len(n_calls) == 4 and len(rows_of(tmp_path)) == 4


def test_budget_is_checked_again_before_every_retry_attempt(monkeypatch, tmp_path):
    def other_process_spends(n):
        if n == 1:  # between attempt 1 and attempt 2 another process spends nearly everything
            seed_ledger(tmp_path, 0.99)

    t = install(monkeypatch, (429, {}, err_body()), before=other_process_spends)
    with pytest.raises(hc.BudgetExceeded):
        caller(tmp_path, budget=1.0).call(**req(max_tokens=100_000))
    assert len(t.calls) == 1
    rows = rows_of(tmp_path)
    assert [r.get("http_status") for r in rows] == [200, 429]  # the other process's seeded row, then attempt 1's row; nothing for attempt 2


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, True, None])
def test_bad_budget_values_are_refused(tmp_path, bad):
    with pytest.raises(hc.BadArguments):
        hc.HaikuCaller(tmp_path / "ledger.jsonl", bad)


def test_ledger_path_is_required(tmp_path):
    with pytest.raises(hc.BadArguments):
        hc.HaikuCaller(None, 1.0)


@pytest.mark.parametrize("line", ["not json", '{"http_status": 200}', '{"cost_usd": -1}', '{"cost_usd": NaN}', '[1]', '{"cost_usd": "0.1"}'])
def test_unreadable_ledger_row_fails_closed_without_a_call(monkeypatch, tmp_path, line):
    t = install(monkeypatch)
    (tmp_path / "ledger.jsonl").write_text(line + "\n")
    with pytest.raises(hc.LedgerError) as ei:
        caller(tmp_path).call(**req())
    assert ei.value.exit_code == 2 and t.calls == []


def test_worst_case_formula_and_cache_control_tightening():
    msgs = [{"role": "user", "content": "x" * 300}]
    raw = hc.canonical_bytes({"messages": msgs})
    toks = math.ceil(len(raw) / 3)
    assert hc.estimate_input_tokens(None, msgs) == toks
    assert hc.worst_case_cost(None, msgs, 500, P) == pytest.approx((toks * 0.10 + 500 * 0.50) / 1e6)
    cached = [{"role": "user", "content": [{"type": "text", "text": "x" * 300, "cache_control": {"type": "ephemeral"}}]}]
    toks2 = hc.estimate_input_tokens(None, cached)
    assert hc.worst_case_cost(None, cached, 500, P) == pytest.approx((toks2 * 0.125 + 500 * 0.50) / 1e6)


# ---------------------------------------------------------------- retries

def test_retry_on_429_then_200_gives_two_rows_and_the_2s_backoff(monkeypatch, tmp_path):
    t = install(monkeypatch, (429, {"request-id": "req_1"}, err_body("rate_limit_error")), (200, {"request-id": "req_2"}, ok_body()))
    sleeps: list = []
    res = caller(tmp_path, sleeps=sleeps).call(**req())
    assert len(t.calls) == 2 and sleeps == [2.0] and res.attempts == 2
    first, second = rows_of(tmp_path)
    assert (first["attempt"], first["http_status"], first["error_class"], first["error_type"], first["cost_usd"]) == (1, 429, "HTTPError", "rate_limit_error", 0.0)
    assert (second["attempt"], second["http_status"], second["error_class"]) == (2, 200, None)
    assert first["request_sha256"] == second["request_sha256"]


def test_backoff_schedule_and_retry_after_capped_at_30(monkeypatch, tmp_path):
    install(monkeypatch, (529, {"retry-after": "1000"}, err_body("overloaded_error")), (503, {}, err_body("api_error")), (200, {}, ok_body()))
    sleeps: list = []
    caller(tmp_path, sleeps=sleeps).call(**req())
    assert sleeps == [30.0, 8.0]
    install(monkeypatch, (500, {}, b"oops"), (502, {"retry-after": "0.5"}, b""), (200, {}, ok_body()))
    sleeps = []
    caller(tmp_path, sleeps=sleeps).call(**req())
    assert sleeps == [2.0, 8.0]  # a short retry-after never shortens our own back-off
    install(monkeypatch, (429, {"retry-after": "20"}, err_body()), (200, {}, ok_body()))
    sleeps = []
    caller(tmp_path, sleeps=sleeps).call(**req())
    assert sleeps == [20.0]
    install(monkeypatch, (429, {"retry-after": "soon"}, err_body()), (200, {}, ok_body()))
    sleeps = []
    caller(tmp_path, sleeps=sleeps).call(**req())
    assert sleeps == [2.0]


def test_timeouts_are_retried_and_recorded_with_no_status(monkeypatch, tmp_path):
    install(monkeypatch, TimeoutError(), ConnectionError("boom"), (200, {}, ok_body()))
    sleeps: list = []
    res = caller(tmp_path, sleeps=sleeps).call(**req())
    assert res.attempts == 3 and sleeps == [2.0, 8.0]
    rows = rows_of(tmp_path)
    assert [(r["http_status"], r["error_class"], r["cost_usd"], r["response_sha256"]) for r in rows[:2]] == [(None, "Timeout", 0.0, None), (None, "NetworkError", 0.0, None)]


def test_no_retry_on_400_and_server_text_is_not_kept(monkeypatch, tmp_path):
    t = install(monkeypatch, (400, {"request-id": "req_9"}, err_body("invalid_request_error", SERVER_MSG_MARK)))
    sleeps: list = []
    with pytest.raises(hc.ApiError) as ei:
        caller(tmp_path, sleeps=sleeps).call(**req())
    e = ei.value
    assert e.exit_code == 5 and e.status == 400 and e.error_type == "invalid_request_error"
    assert len(t.calls) == 1 and sleeps == []
    assert SERVER_MSG_MARK not in str(e) and SERVER_MSG_MARK not in repr(e)
    (row,) = rows_of(tmp_path)
    assert row["http_status"] == 400 and row["error_class"] == "HTTPError" and row["error_type"] == "invalid_request_error" and row["cost_usd"] == 0.0
    assert SERVER_MSG_MARK not in json.dumps(row)


def test_odd_error_types_and_request_ids_are_not_copied(monkeypatch, tmp_path):
    install(monkeypatch, (401, {"request-id": "bad id with spaces " + SERVER_MSG_MARK}, err_body("Invalid x-api-key " + SERVER_MSG_MARK)))
    with pytest.raises(hc.ApiError):
        caller(tmp_path).call(**req())
    (row,) = rows_of(tmp_path)
    assert row["request_id"] is None and row["error_type"] is None and SERVER_MSG_MARK not in json.dumps(row)


@pytest.mark.parametrize("status", [301, 302, 307, 404, 413])
def test_other_non_200_statuses_are_not_retried(monkeypatch, tmp_path, status):
    t = install(monkeypatch, (status, {}, b""))
    with pytest.raises(hc.ApiError) as ei:
        caller(tmp_path).call(**req())
    assert ei.value.status == status and len(t.calls) == 1 and len(rows_of(tmp_path)) == 1


def test_200_with_a_body_that_is_not_json_is_an_api_error_with_a_row(monkeypatch, tmp_path):
    install(monkeypatch, (200, {}, b"<html>"))
    with pytest.raises(hc.ApiError):
        caller(tmp_path).call(**req())
    (row,) = rows_of(tmp_path)
    assert row["http_status"] == 200 and row["error_class"] == "MalformedResponse" and row["cost_usd"] == 0.0


def test_unexpected_transport_exception_is_a_labelled_non_retried_error(monkeypatch, tmp_path):
    t = install(monkeypatch, ValueError("Invalid header value " + FAKE_KEY))
    with pytest.raises(hc.ApiError) as ei:
        caller(tmp_path).call(**req())
    assert FAKE_KEY not in str(ei.value) and len(t.calls) == 1
    assert rows_of(tmp_path)[0]["error_class"] == "TransportError"


def test_retries_exhausted(monkeypatch, tmp_path):
    t = install(monkeypatch, (503, {}, err_body("api_error")), (503, {}, err_body("api_error")), (503, {}, err_body("api_error")))
    sleeps: list = []
    with pytest.raises(hc.RetriesExhausted) as ei:
        caller(tmp_path, sleeps=sleeps).call(**req())
    assert ei.value.exit_code == 6 and ei.value.attempts == 3 and ei.value.last_status == 503
    assert len(t.calls) == 3 and sleeps == [2.0, 8.0]  # no sleep after the last attempt
    assert [r["attempt"] for r in rows_of(tmp_path)] == [1, 2, 3]


# ---------------------------------------------------------------- replay and the text store

def test_replay_hit_makes_no_call_and_no_ledger_row(monkeypatch, tmp_path):
    t = install(monkeypatch, (200, {}, ok_body(text="first answer")))
    store = tmp_path / "store"
    live = caller(tmp_path, save_dir=store).call(**req())
    assert len(t.calls) == 1 and (store / f"{live.request_sha256}.json").exists()
    saved = json.loads((store / f"{live.request_sha256}.json").read_text())
    assert set(saved) == {"request", "response"} and saved["request"]["model"] == "claude-haiku-5-5"
    assert "x-api-key" not in json.dumps(saved) and FAKE_KEY not in json.dumps(saved)
    ledger_before = (tmp_path / "ledger.jsonl").read_bytes()
    monkeypatch.delenv(hc.KEY_ENV)  # a replay hit needs no key
    again = caller(tmp_path, save_dir=store, replay=True).call(**req())
    assert again.replayed and again.text == "first answer" and again.cost_usd == 0.0 and again.attempts == 0
    assert len(t.calls) == 1 and (tmp_path / "ledger.jsonl").read_bytes() == ledger_before
    assert again.spent_after == pytest.approx(live.cost_usd)


def test_replay_only_miss_raises_and_replay_miss_falls_through_to_a_call(monkeypatch, tmp_path):
    t = install(monkeypatch)
    with pytest.raises(hc.ReplayMiss) as ei:
        caller(tmp_path, save_dir=tmp_path / "store", replay_only=True).call(**req())
    assert ei.value.exit_code == 4 and t.calls == [] and rows_of(tmp_path) == []
    res = caller(tmp_path, save_dir=tmp_path / "store", replay=True).call(**req())  # replay without replay_only: a miss calls
    assert not res.replayed and len(t.calls) == 1 and (tmp_path / "store" / f"{res.request_sha256}.json").exists()


def test_replay_needs_a_store_and_a_corrupt_entry_is_loud(monkeypatch, tmp_path):
    t = install(monkeypatch)
    with pytest.raises(hc.BadArguments):
        caller(tmp_path, replay=True)
    store = tmp_path / "store"
    c = caller(tmp_path, save_dir=store, replay=True)
    sha = hc.sha256_hex(hc.canonical_bytes(c._build_body(req()["system"], req()["messages"], 200, None, hc.DEFAULT_THINKING)))
    (store / f"{sha}.json").write_text(json.dumps({"request": {"other": 1}, "response": {"content": []}}))
    with pytest.raises(hc.ReplayCorrupt) as ei:
        c.call(**req())
    assert ei.value.exit_code == 4 and t.calls == []


def test_a_different_request_is_a_different_cache_entry(monkeypatch, tmp_path):
    t = install(monkeypatch)
    store = tmp_path / "store"
    c = caller(tmp_path, save_dir=store, replay=True)
    c.call(**req())
    c.call(**req(max_tokens=201))
    c.call(**req())
    assert len(t.calls) == 2 and len(list(store.glob("*.json"))) == 2


def test_failed_calls_are_not_saved(monkeypatch, tmp_path):
    install(monkeypatch, (400, {}, err_body("invalid_request_error")))
    store = tmp_path / "store"
    with pytest.raises(hc.ApiError):
        caller(tmp_path, save_dir=store).call(**req())
    assert list(store.glob("*")) == []


# ---------------------------------------------------------------- models

def test_other_model_is_refused_without_allow_model_and_a_price(monkeypatch, tmp_path):
    t = install(monkeypatch)
    with pytest.raises(hc.ModelRefused):
        caller(tmp_path, model="claude-sonnet-x")
    with pytest.raises(hc.ModelRefused):  # allow_model must name that model
        caller(tmp_path, model="claude-sonnet-x", allow_model="claude-other", prices={"input": 3, "cache_write": 3.75, "cache_read": 0.3, "output": 15})
    with pytest.raises(hc.ModelRefused):  # allowed but unpriced
        caller(tmp_path, model="claude-sonnet-x", allow_model="claude-sonnet-x")
    with pytest.raises(hc.ModelRefused):  # incomplete price dict
        caller(tmp_path, model="claude-sonnet-x", allow_model="claude-sonnet-x", prices={"input": 3})
    with pytest.raises(hc.ModelRefused):  # the built-in table cannot be overridden
        caller(tmp_path, prices={"input": 0, "cache_write": 0, "cache_read": 0, "output": 0})
    assert t.calls == [] and rows_of(tmp_path) == []


def test_allowed_other_model_is_sent_priced_with_the_given_table_and_recorded(monkeypatch, tmp_path):
    t = install(monkeypatch, (200, {}, ok_body(input_tokens=1_000_000, output_tokens=0)))
    prices = {"input": 3, "cache_write": 3.75, "cache_read": 0.3, "output": 15}
    res = caller(tmp_path, budget=10.0, model="claude-sonnet-x", allow_model="claude-sonnet-x", prices=prices).call(**req())
    assert json.loads(t.calls[0]["body"])["model"] == "claude-sonnet-x" and res.cost_usd == pytest.approx(3.0)
    (row,) = rows_of(tmp_path)
    assert row["model"] == "claude-sonnet-x" and row["allow_model"] == "claude-sonnet-x" and row["custom_prices"]["output"] == 15.0


# ---------------------------------------------------------------- the key

def test_missing_empty_or_unsendable_key_is_exit_2_and_never_echoed(monkeypatch, tmp_path, capsys):
    t = install(monkeypatch)
    monkeypatch.setenv("SOME_OTHER_SECRET", "OTHER-SECRET-VALUE-77")
    rq = tmp_path / "rq.json"
    rq.write_text(json.dumps(req()))
    argv = ["--request", str(rq), "--out", str(tmp_path / "out.json"), "--ledger", str(tmp_path / "ledger.jsonl"), "--budget-usd", "1"]
    for value in (None, "", "   ", FAKE_KEY + "\nX-Evil: 1", FAKE_KEY + " space"):
        if value is None:
            monkeypatch.delenv(hc.KEY_ENV)
        else:
            monkeypatch.setenv(hc.KEY_ENV, value)
        assert hc.main(argv) == 2
        cap = capsys.readouterr()
        text = cap.out + cap.err
        assert "ANTHROPIC_API_KEY" in cap.err and "OTHER-SECRET-VALUE-77" not in text and FAKE_KEY not in text and "Evil" not in text
        assert cap.out == ""
    assert t.calls == [] and rows_of(tmp_path) == [] and not (tmp_path / "out.json").exists()


def test_key_never_appears_anywhere_on_success_http_error_timeout_and_budget_paths(monkeypatch, tmp_path, capsys):
    """Runs the four paths through the API and the CLI, with a hostile server that echoes the key in its error text."""
    echo = err_body("authentication_error", "invalid x-api-key " + FAKE_KEY)
    work = tmp_path / "w"
    work.mkdir()
    (work / "rq.json").write_text(json.dumps(req()))
    (work / "big.json").write_text(json.dumps(req(max_tokens=10_000_000)))
    ledger, store = work / "ledger.jsonl", work / "store"
    base = ["--ledger", str(ledger), "--budget-usd", "1", "--save-dir", str(store)]
    texts: list[str] = []
    seen_key_in_headers = []

    def run_cli(script, request, out):
        t = install(monkeypatch, *script)
        code = hc.main(["--request", str(work / request), "--out", str(work / out)] + base, sleep=lambda s: None)
        cap = capsys.readouterr()
        texts.extend([cap.out, cap.err])
        seen_key_in_headers.extend(c["headers"]["x-api-key"] == FAKE_KEY for c in t.calls)
        return code

    assert run_cli([(200, {"request-id": "req_ok"}, ok_body())], "rq.json", "ok.json") == 0
    assert run_cli([(401, {}, echo)], "rq.json", "e401.json") == 5
    assert run_cli([TimeoutError(), TimeoutError(), TimeoutError()], "rq.json", "to.json") == 6
    assert run_cli([(503, {}, echo)] * 3, "rq.json", "e503.json") == 6
    assert run_cli([], "big.json", "never.json") == 3  # 10M max_tokens: worst case 5 USD > 1 USD budget
    assert all(seen_key_in_headers) and len(seen_key_in_headers) == 1 + 1 + 3 + 3  # the fake key really was sent each time

    # the same paths through the Python API: exception text, repr and full traceback
    exc_texts: list[str] = []
    api = hc.HaikuCaller(ledger, 1.0, save_dir=store, sleep=lambda s: None)
    for script, kw in (([(401, {}, echo)], {}), ([TimeoutError()] * 3, {}), ([(400, {}, echo)], {}), ([], {"max_tokens": 10_000_000})):
        install(monkeypatch, *script)
        with pytest.raises(hc.HaikuCallerError) as ei:
            api.call(**req(**kw))
        exc_texts += [str(ei.value), repr(ei.value), "".join(traceback.format_exception(ei.value))]
    install(monkeypatch, (200, {}, ok_body()))
    res = api.call(**req(max_tokens=77))
    exc_texts.append(repr(res))
    exc_texts.append(repr(api) + repr(api.ledger))

    on_disk = []
    for p in work.rglob("*"):
        if p.is_file():
            on_disk.append((p.name, p.read_bytes()))
    assert len(on_disk) >= 5  # ledger, lock, store entries, out files: the walk really saw files
    for text in texts + exc_texts:
        assert FAKE_KEY not in text
    for name, data in on_disk:
        assert FAKE_KEY.encode() not in data, name
    assert any(name == "ledger.jsonl" for name, _ in on_disk)


def test_ledger_and_stdout_contain_no_prompt_or_response_text(monkeypatch, tmp_path, capsys):
    install(monkeypatch, (200, {}, ok_body(text="RESPONSE-MARKER-99")))
    rq = tmp_path / "rq.json"
    rq.write_text(json.dumps(req()))
    code = hc.main(["--request", str(rq), "--out", str(tmp_path / "out.json"), "--ledger", str(tmp_path / "ledger.jsonl"), "--budget-usd", "1"])
    assert code == 0
    ledger_text = (tmp_path / "ledger.jsonl").read_text()
    assert PROMPT_MARK not in ledger_text and "RESPONSE-MARKER-99" not in ledger_text and len(ledger_text.splitlines()) == 1
    out = capsys.readouterr().out
    assert PROMPT_MARK not in out and "RESPONSE-MARKER-99" not in out
    assert "RESPONSE-MARKER-99" in (tmp_path / "out.json").read_text()  # the response goes to --out only


# ---------------------------------------------------------------- file modes

def test_modes_dirs_700_files_600_and_out_is_tightened(monkeypatch, tmp_path):
    install(monkeypatch)
    old_umask = os.umask(0)  # even with a permissive umask
    try:
        store = tmp_path / "deep" / "store"
        res = hc.HaikuCaller(tmp_path / "logs" / "ledger.jsonl", 1.0, save_dir=store, sleep=lambda s: None).call(**req())
        assert mode(store) == 0o700
        assert mode(store / f"{res.request_sha256}.json") == 0o600
        assert mode(tmp_path / "logs" / "ledger.jsonl") == 0o600 and mode(tmp_path / "logs" / "ledger.jsonl.lock") == 0o600
        out = tmp_path / "out.json"
        out.write_text("old")
        os.chmod(out, 0o644)
        rq = tmp_path / "rq.json"
        rq.write_text(json.dumps(req()))
        code = hc.main(["--request", str(rq), "--out", str(out), "--ledger", str(tmp_path / "logs" / "ledger.jsonl"), "--budget-usd", "1"])
        assert code == 0 and mode(out) == 0o600 and json.loads(out.read_text())["content"][0]["text"] == "hello"
        out2 = tmp_path / "fresh.json"
        assert hc.main(["--request", str(rq), "--out", str(out2), "--ledger", str(tmp_path / "logs" / "ledger.jsonl"), "--budget-usd", "1"]) == 0
        assert mode(out2) == 0o600
    finally:
        os.umask(old_umask)


# ---------------------------------------------------------------- CLI

def test_cli_success_prints_one_summary_line(monkeypatch, tmp_path, capsys):
    install(monkeypatch, (200, {}, ok_body(input_tokens=1000, output_tokens=200)))
    rq = tmp_path / "rq.json"
    rq.write_text(json.dumps(req()))
    code = hc.main(["--request", str(rq), "--out", str(tmp_path / "out.json"), "--ledger", str(tmp_path / "ledger.jsonl"), "--budget-usd", "25"])
    cap = capsys.readouterr()
    assert code == 0 and cap.err == ""
    lines = cap.out.splitlines()
    assert len(lines) == 1
    fields = dict(kv.split("=") for kv in lines[0].split())
    assert fields["status"] == "200" and fields["input_tokens"] == "1000" and fields["output_tokens"] == "200"
    assert float(fields["cost_usd"]) == pytest.approx(2e-4) and float(fields["spent_after"]) == pytest.approx(2e-4)
    assert float(fields["budget_usd"]) == 25.0 and int(fields["latency_ms"]) >= 0


def test_cli_exit_codes_for_budget_replay_api_and_retries(monkeypatch, tmp_path, capsys):
    rq = tmp_path / "rq.json"
    rq.write_text(json.dumps(req()))
    base = ["--request", str(rq), "--out", str(tmp_path / "out.json"), "--ledger", str(tmp_path / "ledger.jsonl")]
    install(monkeypatch)
    assert hc.main(base + ["--budget-usd", "0"]) == 3
    assert hc.main(base + ["--budget-usd", "1", "--save-dir", str(tmp_path / "s"), "--replay-only"]) == 4
    install(monkeypatch, (400, {}, err_body("invalid_request_error")))
    assert hc.main(base + ["--budget-usd", "1"]) == 5
    install(monkeypatch, *[(529, {}, err_body("overloaded_error"))] * 3)
    assert hc.main(base + ["--budget-usd", "1"], sleep=lambda s: None) == 6
    err = capsys.readouterr().err
    assert "status=400 type=invalid_request_error" in err and SERVER_MSG_MARK not in err
    assert not (tmp_path / "out.json").exists()  # nothing is written to --out on a failure


def test_cli_replay_prints_status_replay(monkeypatch, tmp_path, capsys):
    install(monkeypatch)
    rq = tmp_path / "rq.json"
    rq.write_text(json.dumps(req()))
    base = ["--request", str(rq), "--out", str(tmp_path / "out.json"), "--ledger", str(tmp_path / "ledger.jsonl"), "--budget-usd", "1",
            "--save-dir", str(tmp_path / "s")]
    assert hc.main(base) == 0
    capsys.readouterr()
    assert hc.main(base + ["--replay-only"]) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("status=replay ") and "cost_usd=0.00000000" in line and len(rows_of(tmp_path)) == 1


def test_cli_bad_arguments_exit_2(monkeypatch, tmp_path, capsys):
    t = install(monkeypatch)
    ok = tmp_path / "rq.json"
    ok.write_text(json.dumps(req()))
    cases = {
        "missing_required": [],
        "unknown_request_key": None,
        "no_max_tokens": None,
        "not_json": None,
        "model_without_allow": ["--model", "claude-sonnet-x"],
        "bad_prices_json": ["--model", "m", "--allow-model", "m", "--prices-json", "{nope"],
        "nan_budget": ["--budget-usd", "nan"],
        "out_dir_missing": None,
    }
    common = ["--ledger", str(tmp_path / "ledger.jsonl"), "--budget-usd", "1"]
    for name, extra in cases.items():
        rqf, out = ok, tmp_path / "out.json"
        if name == "unknown_request_key":
            rqf = tmp_path / "bad1.json"
            rqf.write_text(json.dumps(req(model="claude-opus-x")))
        elif name == "no_max_tokens":
            rqf = tmp_path / "bad2.json"
            rqf.write_text(json.dumps({"messages": [{"role": "user", "content": "x"}]}))
        elif name == "not_json":
            rqf = tmp_path / "bad3.json"
            rqf.write_text("{")
        elif name == "out_dir_missing":
            out = tmp_path / "nope" / "out.json"
        if name == "missing_required":
            argv = []
        elif name == "nan_budget":
            argv = ["--request", str(rqf), "--out", str(out), "--ledger", str(tmp_path / "ledger.jsonl")] + extra
        else:
            argv = ["--request", str(rqf), "--out", str(out)] + common + (extra or [])
        assert hc.main(argv) == 2, name
    capsys.readouterr()
    assert t.calls == [] and rows_of(tmp_path) == []


def test_request_file_thinking_default_and_explicit_null(monkeypatch, tmp_path):
    t = install(monkeypatch)
    for i, extra in enumerate(({}, {"thinking": None}, {"thinking": {"type": "enabled", "budget_tokens": 1024}})):
        rq = tmp_path / f"rq{i}.json"
        rq.write_text(json.dumps(req(**extra)))
        assert hc.main(["--request", str(rq), "--out", str(tmp_path / f"o{i}.json"), "--ledger", str(tmp_path / "ledger.jsonl"), "--budget-usd", "1"]) == 0
    bodies = [json.loads(c["body"]) for c in t.calls]
    assert bodies[0]["thinking"] == {"type": "disabled"} and "thinking" not in bodies[1] and bodies[2]["thinking"]["type"] == "enabled"


# ---------------------------------------------------------------- ledger_summary

def test_ledger_summary(tmp_path):
    p = tmp_path / "ledger.jsonl"
    rows = [
        {"http_status": 200, "input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 3, "cache_read_input_tokens": 7, "cost_usd": 0.001, "latency_ms": 100},
        {"http_status": 200, "input_tokens": 20, "output_tokens": 6, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1, "cost_usd": 0.002, "latency_ms": 300},
        {"http_status": 200, "input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "cost_usd": 0.0005, "latency_ms": 200},
        {"http_status": 429, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_ms": 9000},
        {"http_status": None, "cost_usd": 0.0, "latency_ms": 60000, "error_class": "Timeout"},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows) + "\n")
    s = hc.ledger_summary(p)
    assert s["calls"] == 5 and s["http_error_rows"] == 1 and s["transport_error_rows"] == 1
    assert s["tokens"] == {"input": 31, "output": 12, "cache_creation_input": 3, "cache_read_input": 8}
    assert s["total_cost_usd"] == pytest.approx(0.0035) and s["median_latency_ms_200"] == 200
    assert hc.ledger_summary(tmp_path / "absent.jsonl") == {
        "calls": 0, "http_error_rows": 0, "transport_error_rows": 0,
        "tokens": {"input": 0, "output": 0, "cache_creation_input": 0, "cache_read_input": 0},
        "total_cost_usd": 0.0, "median_latency_ms_200": None}


# ---------------------------------------------------------------- the real transport, against a loopback server only

class _Handler(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        self.rfile.read(n)
        type(self).hits.append((self.path, self.headers.get("x-api-key")))
        if self.path == "/redirect":
            self.send_response(307)
            self.send_header("Location", "/landed")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/bad":
            body = err_body("invalid_request_error")
            self.send_response(400)
            self.send_header("Request-Id", "req_loop")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/slow":
            time.sleep(1.0)
        else:
            body = ok_body()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


@pytest.fixture
def loopback(monkeypatch):
    for v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    _Handler.hits = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_real_transport_passes_status_through_times_out_and_never_follows_a_redirect(loopback):
    h = {"x-api-key": FAKE_KEY, "content-type": "application/json"}
    status, headers, raw = hc._http_post(loopback + "/ok", h, b"{}", 5)
    assert status == 200 and json.loads(raw)["stop_reason"] == "end_turn"
    status, headers, raw = hc._http_post(loopback + "/bad", h, b"{}", 5)
    assert status == 400 and headers["request-id"] == "req_loop" and json.loads(raw)["error"]["type"] == "invalid_request_error"
    status, headers, raw = hc._http_post(loopback + "/redirect", h, b"{}", 5)
    assert status == 307
    assert [p for p, _ in _Handler.hits] == ["/ok", "/bad", "/redirect"]  # /landed was never requested, so the key never left
    with pytest.raises(TimeoutError) as ei:
        hc._http_post(loopback + "/slow", h, b"{}", 0.2)
    assert FAKE_KEY not in str(ei.value) and ei.value.__cause__ is None and ei.value.__suppress_context__
    with pytest.raises(ConnectionError) as ei:
        hc._http_post("http://127.0.0.1:1/x", h, b"{}", 2)
    assert FAKE_KEY not in str(ei.value) and ei.value.__cause__ is None and ei.value.__suppress_context__
