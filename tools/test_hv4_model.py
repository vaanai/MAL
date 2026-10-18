"""Tests for tools/hv4_model.py: StubModel, and CallerModel routed through haiku_caller with its transport replaced.

No real API call is made anywhere in this file. The module-level fixture replaces haiku_caller._http_post with a function that
raises, so a test that forgets to install a fake transport cannot reach the network; the key in every test is a fake.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tools import haiku_caller as hc
from tools import hv4_arms as arms
from tools import hv4_budget as bg
from tools import hv4_model as hm
from tools import hv4_prompts as pr
from tools import hv4_validate as v
from tools.hv4_testkit import T0_MS, buy, make_card, make_cards, reply, skip

FAKE_KEY = "sk-ant-TEST-hv4-0123456789abcdef"
NOW = T0_MS + 2_000
TOOLS = Path(__file__).resolve().parent


class RealNetworkAttempt(BaseException):
    pass


@pytest.fixture(autouse=True)
def _env_and_transport(monkeypatch):
    monkeypatch.setenv(hc.KEY_ENV, FAKE_KEY)

    def refuse(*a, **k):
        raise RealNetworkAttempt("a test reached the real _http_post; call install() first")

    monkeypatch.setattr(hc, "_http_post", refuse)


class Wire:
    """A fake transport: records every request and answers from a queue (the last answer repeats)."""

    def __init__(self, monkeypatch, answers):
        self.sent = []
        self._answers = list(answers)
        monkeypatch.setattr(hc, "_http_post", self)

    def __call__(self, url, headers, body, timeout):
        self.sent.append({"url": url, "headers": dict(headers), "body": json.loads(body)})
        a = self._answers[0] if len(self._answers) == 1 else self._answers.pop(0)
        return a


def ok(text, *, stop="end_turn", cache_read=2000, new=900, out=60):
    body = {"id": "msg_1", "type": "message", "role": "assistant", "model": hc.DEFAULT_MODEL, "stop_reason": stop,
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": new, "output_tokens": out, "cache_creation_input_tokens": 0, "cache_read_input_tokens": cache_read}}
    return 200, {"request-id": "req_1"}, json.dumps(body).encode()


@pytest.fixture
def guard(tmp_path):
    return bg.BudgetGuard(tmp_path / "haiku-v4")


def req_for(arm="CON-L", cards=None, seq=0):
    cards = cards or [make_card()]
    persona = arms.ARMS[arm].persona
    return pr.build_entry_request(arm, pr.load_playbook(persona), cards, seq)


def rows(guard, ledger, day="2026-10-21"):
    p = guard.ledger_path(ledger, day)
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def at(day="2026-10-21", hour=9):
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:00:00+00:00").timestamp() * 1000)


# ---------------------------------------------------------------- StubModel

def test_stub_scripts_in_order_then_abstains_and_records_calls(monkeypatch):
    monkeypatch.delenv(hc.KEY_ENV, raising=False)  # the stub never needs a key
    card = make_card()
    req = req_for(cards=[card])
    stub = hm.StubModel([reply(buy(card["id"])), lambda r: reply(skip(r.ids[0]))])
    texts = [stub.complete(req, NOW).text for _ in range(3)]
    assert json.loads(texts[0])[0]["action"] == "BUY" and json.loads(texts[1])[0]["action"] == "SKIP"
    assert json.loads(texts[2]) == [{"id": card["id"], "action": "SKIP"}]   # script exhausted: abstain
    assert len(stub.calls) == 3 and stub.calls[0] is req


def test_stub_callable_script_and_reply_objects():
    req = req_for()
    assert hm.StubModel(lambda r: reply(skip(r.ids[0]))).complete(req, NOW).stop_reason == "end_turn"
    assert hm.StubModel([hm.refusal_reply()]).complete(req, NOW).refused


def test_stub_default_reply_per_kind():
    card = make_card()
    risk = pr.build_risk_request(pr.load_playbook("RISK"), [card], 0)
    out = v.validate_risk(hm.StubModel().complete(risk, NOW), cards=[card])[0]
    assert out.action == "pass"
    pos = [{"id": card["id"], "stop_pct": -7, "target_pct": 12, "mark_ret_pct": 1.0, "as_of_ms": T0_MS}]
    rc = pr.build_recheck_request("CON-L", pr.load_playbook("CON"), pos, [card], 0)
    assert v.validate_recheck(hm.StubModel().complete(rc, NOW), positions=pos, received_ms=NOW)[0].action == "HOLD"


def test_stub_with_guard_charges_the_arm_ledger_and_halts_the_whole_group_on_the_cap(guard):
    stub = hm.StubModel(guard=guard, cost_usd=0.20)
    day_ms = at()
    r1, r2 = (stub.complete(req_for("CON-L", seq=i), day_ms) for i in range(2))
    assert r1.error is None and r2.error is None
    assert guard.spent("CON-L", "2026-10-21") == pytest.approx(0.40)
    r3 = stub.complete(req_for("CON-L", seq=2), day_ms)                  # 0.40 + 0.20 > 0.50
    assert r3.error == "budget_exceeded" and r3.text == ""
    for arm in ("CON-F", "CON-P"):                                         # symmetric: the twins stop too
        out = stub.complete(req_for(arm), day_ms)
        assert out.error == "group_halted", arm
    assert stub.complete(req_for("BIG-L"), day_ms).error is None           # another persona goes on
    assert stub.complete(req_for("CON-L"), at("2026-10-22", 0)).error is None   # 00:00Z lifts it
    # a refusal-by-budget reaches the validator as an abstain that is not the model's fault
    out = v.validate_entry(r3, persona="CON", cards=[make_card()], knobs=v.Knobs.v0("CON"), received_ms=NOW)[0]
    assert out.action == "SKIP" and not out.invalid


def test_stub_goes_through_the_month_guard_too(tmp_path):
    g = bg.BudgetGuard(tmp_path / "x", month_limit_usd=0.30)
    stub = hm.StubModel(guard=g, cost_usd=0.25)
    assert stub.complete(req_for("BIG-L"), at()).error is None
    out = stub.complete(req_for("CON-L"), at())
    assert out.error == "month_guard" or out.error == "budget_exceeded"
    assert stub.complete(req_for("BST-L"), at()).error in ("month_guard", "group_halted")


# ---------------------------------------------------------------- CallerModel through haiku_caller (transport replaced)

def test_caller_model_reserves_calls_settles_and_returns_text(guard, monkeypatch):
    card = make_card()
    wire = Wire(monkeypatch, [ok(reply(buy(card["id"])))])
    req = req_for(cards=[card])
    out = hm.CallerModel(guard).complete(req, at())
    assert out.error is None and json.loads(out.text)[0]["action"] == "BUY" and out.stop_reason == "end_turn"
    assert out.request_sha256 == req.request_sha256() and not out.replayed
    r = rows(guard, "CON-L")
    assert [x["kind"] for x in r] == ["reserve", "settle"] and r[0]["reservation_id"] == r[1]["reservation_id"]
    expected = (900 * 0.10 + 2000 * 0.01 + 60 * 0.50) / 1e6
    assert out.cost_usd == pytest.approx(expected) and guard.spent("CON-L", "2026-10-21") == pytest.approx(expected)
    # what went over the (fake) wire: the request exactly as built, one cache marker, thinking disabled, no temperature
    sent = wire.sent[0]["body"]
    assert sent == req.body() and json.dumps(sent).count("cache_control") == 1
    assert sent["thinking"] == {"type": "disabled"} and "temperature" not in sent and sent["model"] == "claude-haiku-5-5"
    assert sent["max_tokens"] == 400


def test_replay_returns_the_saved_reply_without_a_second_call_or_ledger_row(guard, monkeypatch):
    wire = Wire(monkeypatch, [ok("[]")])
    model = hm.CallerModel(guard)
    req = req_for()
    first = model.complete(req, at())
    n_rows = len(rows(guard, "CON-L"))
    again = model.complete(req_for(), at(hour=10))     # the same arm, cards and seq: a restart rebuilds the same bytes
    assert again.replayed and again.text == first.text and len(wire.sent) == 1 and len(rows(guard, "CON-L")) == n_rows


def test_the_twins_never_share_a_reply_or_a_cache(guard, monkeypatch):
    wire = Wire(monkeypatch, [ok("[]")])
    model = hm.CallerModel(guard)
    cards = [make_card()]
    outs = [model.complete(req_for(a, cards=cards, seq=5), at()) for a in ("CON-L", "CON-F", "CON-P")]
    assert not any(o.replayed for o in outs) and len(wire.sent) == 3
    assert len({o.request_sha256 for o in outs}) == 3
    for a in ("CON-L", "CON-F", "CON-P"):
        assert len(list((guard.root / "cache" / a).glob("*.json"))) == 1
        assert [x["kind"] for x in rows(guard, a)] == ["reserve", "settle"]


def test_a_refusal_from_the_api_is_an_abstain(guard, monkeypatch):
    Wire(monkeypatch, [ok("", stop="refusal")])
    card = make_card()
    out = hm.CallerModel(guard).complete(req_for(cards=[card]), at())
    assert out.refused and out.error is None
    verdict = v.validate_entry(out, persona="CON", cards=[card], knobs=v.Knobs.v0("CON"), received_ms=NOW)[0]
    assert verdict.action == "SKIP" and verdict.reasons == ("refusal",)


def test_truncated_and_unreadable_replies_are_abstains(guard, monkeypatch):
    Wire(monkeypatch, [ok('[{"id": "', stop="max_tokens")])
    card = make_card()
    out = hm.CallerModel(guard).complete(req_for(cards=[card]), at())
    verdict = v.validate_entry(out, persona="CON", cards=[card], knobs=v.Knobs.v0("CON"), received_ms=NOW)[0]
    assert verdict.action == "SKIP" and verdict.reasons == ("truncated",) and verdict.invalid


def test_api_errors_become_error_codes_not_server_text(guard, monkeypatch):
    server = {"type": "error", "error": {"type": "invalid_request_error", "message": "SERVER-TEXT-MARKER"}}
    Wire(monkeypatch, [(400, {}, json.dumps(server).encode())])
    out = hm.CallerModel(guard).complete(req_for(), at())
    assert out.error == "ApiError" and out.text == "" and "SERVER-TEXT-MARKER" not in repr(out)
    assert guard.spent("CON-L", "2026-10-21") == 0.0           # an error response is not billed


def test_missing_key_is_an_error_reply_and_writes_no_ledger_row(guard, monkeypatch):
    monkeypatch.delenv(hc.KEY_ENV)
    out = hm.CallerModel(guard).complete(req_for(), at())
    assert out.error == "MissingKey" and rows(guard, "CON-L") == []


def test_the_caller_budget_is_the_hard_stop_and_halts_the_group(tmp_path, monkeypatch):
    # month headroom is just over the preflight minimum but under one call's worst-case reserve: haiku_caller refuses
    g = bg.BudgetGuard(tmp_path / "haiku-v4", month_limit_usd=0.001, min_call_usd=0.0005)
    wire = Wire(monkeypatch, [ok("[]")])
    worst = hc.worst_case_cost(req_for().body(), hc.PRICES[hc.DEFAULT_MODEL])
    assert worst > 0.001, worst
    out = hm.CallerModel(g).complete(req_for(), at())
    assert out.error == "budget_exceeded" and wire.sent == [] and rows(g, "CON-L") == []
    for arm in arms.llm_arms():
        assert g.preflight(arm, "entry", at()).reason == "group_halted"


def test_month_guard_effective_budget_reaches_the_caller(tmp_path, monkeypatch):
    g = bg.BudgetGuard(tmp_path / "haiku-v4", month_limit_usd=1.0)
    path = g.ledger_path("RISK", "2026-10-02")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"cost_usd": 0.9995}) + "\n")
    wire = Wire(monkeypatch, [ok("[]")])
    assert hm.CallerModel(g).complete(req_for("BIG-L"), at()).error == "month_guard" and wire.sent == []


def test_the_key_is_not_written_anywhere_and_not_read_by_this_layer(guard, monkeypatch):
    Wire(monkeypatch, [ok("[]")])
    hm.CallerModel(guard).complete(req_for(), at())
    for f in guard.root.rglob("*"):
        if f.is_file():
            assert FAKE_KEY.encode() not in f.read_bytes(), f
    for name in ("hv4_arms", "hv4_prompts", "hv4_validate", "hv4_budget", "hv4_model", "hv4_testkit"):
        src = (TOOLS / f"{name}.py").read_text()
        for needle in ("os.environ", "getenv", "anthropic.env", "/var/lib/mal", "urlopen", "http.client", "import requests", "urllib"):
            assert needle not in src, (name, needle)
