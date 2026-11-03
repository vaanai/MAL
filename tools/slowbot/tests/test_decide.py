import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import tools.haiku_caller as hc
from tools.haiku_caller import BudgetExceeded, HaikuCaller
from tools.slowbot import decide as D

CAND = {"mint": "M1", "pool": "P1", "venue": "pumpswap", "symbol": "AAA", "name": "Aaa", "age_min": 90, "liq_usd": 50000,
        "mcap_usd": 400000, "arm": "H", "quote_out_lamports": 5, "rule_hit": "a"}


class Stub:
    def __init__(self, text=None, exc=None):
        self.text, self.exc, self.calls = text, exc, []

    def call(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(text=self.text, usage={"input_tokens": 10, "output_tokens": 5}, cost_usd=0.0001,
                               request_sha256="ab" * 32, replayed=False)


GOOD = json.dumps({"action": "BUY", "conviction": 3, "catalyst": "volume expansion", "risk": "none"})


def test_buy_logged(tmp_path):
    s = Stub(GOOD)
    log = tmp_path / "d.jsonl"
    row = D.decide(CAND, s, log_path=log)
    assert row["action"] == "BUY" and row["conviction"] == 3 and row["decision_error"] is None
    kw = s.calls[0]
    assert kw["temperature"] is None  # omitted from the request body: claude-haiku-5-5 rejects it with HTTP 400
    _, sha = D.load_prompt()
    assert row["prompt_sha256"] == sha
    logged = json.loads(log.read_text())
    assert logged["prompt_sha256"] == sha and logged["response_text"] == GOOD


def test_card_hides_quote_and_mint():
    card = D.build_card(CAND)
    assert "mint" not in card and "quote_out_lamports" not in card and "x_context" not in card


def test_x_context_capped():
    s = Stub(GOOD)
    row = D.decide(CAND, s, x_context=[f"s{i}" for i in range(9)] + [""])
    msg = json.loads(s.calls[0]["messages"][0]["content"])
    assert msg["x_context"] == ["s0", "s1", "s2", "s3", "s4"] and row["has_x_context"]


@pytest.mark.parametrize("text,err", [
    ("hello", "parse_not_json"), ("[1]", "parse_not_object"),
    (json.dumps({"action": "MAYBE", "conviction": 3, "catalyst": "x", "risk": "none"}), "parse_bad_action"),
    (json.dumps({"action": "BUY", "conviction": 9, "catalyst": "x", "risk": "none"}), "parse_bad_conviction"),
    (json.dumps({"action": "BUY", "conviction": True, "catalyst": "x", "risk": "none"}), "parse_bad_conviction"),
    (json.dumps({"action": "BUY", "conviction": 3, "catalyst": " ".join(["w"] * 21), "risk": "none"}), "parse_bad_catalyst"),
    (json.dumps({"action": "BUY", "conviction": 3, "catalyst": "x", "risk": "lol"}), "parse_bad_risk"),
    (json.dumps({"action": "BUY", "conviction": 3, "catalyst": "x"}), "parse_bad_keys"),
])
def test_parse_error_is_skip_and_logged(tmp_path, text, err):
    log = tmp_path / "d.jsonl"
    row = D.decide(CAND, Stub(text), log_path=log)
    assert row["action"] == "SKIP" and row["decision_error"] == err
    assert json.loads(log.read_text())["decision_error"] == err


def test_fenced_json_ok():
    assert D.validate_decision("```json\n" + GOOD + "\n```")["action"] == "BUY"


def test_caller_error_is_skip():
    row = D.decide(CAND, Stub(exc=BudgetExceeded("cap")))
    assert row["action"] == "SKIP" and row["decision_error"] == "caller_BudgetExceeded"


def test_prompt_content():
    txt, _ = D.load_prompt()
    assert "right-skewed" in txt and "concrete evidence" in txt


def test_daily_caller_and_zero_budget_no_network(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = D.daily_caller(tmp_path)
    assert isinstance(c, HaikuCaller) and c.budget_usd == 2.0 and c.model == "claude-haiku-5-5"
    row = D.decide(CAND, D.daily_caller(tmp_path, budget_usd=0.0))
    assert row["action"] == "SKIP" and row["decision_error"].startswith("caller_")


def _fake_transport(monkeypatch, calls, in_tok=5000, out_tok=300):
    body = json.dumps({"content": [{"type": "text", "text": GOOD}],
                       "usage": {"input_tokens": in_tok, "output_tokens": out_tok}, "model": "claude-haiku-5-5"}).encode()

    def post(url, headers, data, timeout):
        calls.append(1)
        return 200, {}, body

    monkeypatch.setattr(hc, "_http_post", post)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")


def test_real_caller_small_budget_cap_then_skip(tmp_path, monkeypatch):
    calls = []
    _fake_transport(monkeypatch, calls)
    c = D.daily_caller(tmp_path, budget_usd=0.0012)
    rows = [D.decide(CAND, c) for _ in range(6)]
    n_ok = sum(1 for r in rows if r["decision_error"] is None)
    assert 1 <= n_ok < 6 and len(calls) == n_ok  # transport never called once the cap is hit
    capped = [r for r in rows if r["decision_error"] == "caller_BudgetExceeded"]
    assert capped and all(r["action"] == "SKIP" and r["cost_usd"] == 0.0 and not r["h_buy"] for r in capped)
    assert rows[0]["cost_usd"] > 0


def test_daily_caller_rebuilds_each_utc_day(tmp_path, monkeypatch):
    calls = []
    _fake_transport(monkeypatch, calls)
    clock = {"t": datetime(2026, 11, 5, 23, 59, tzinfo=timezone.utc)}
    dc = D.DailyCaller(tmp_path, now_fn=lambda: clock["t"], budget_usd=0.0012)
    while D.decide(CAND, dc)["decision_error"] is None:
        pass
    assert D.decide(CAND, dc)["decision_error"] == "caller_BudgetExceeded"
    clock["t"] = datetime(2026, 11, 6, 0, 1, tzinfo=timezone.utc)
    assert D.decide(CAND, dc)["decision_error"] is None  # fresh day, fresh cap
    assert len(list(tmp_path.glob("slowbot-haiku-ledger-*.jsonl"))) == 2


def test_h_buy_needs_conviction_3():
    lo = json.dumps({"action": "BUY", "conviction": 2, "catalyst": "x", "risk": "none"})
    assert D.decide(CAND, Stub(lo))["h_buy"] is False
    assert D.decide(CAND, Stub(lo))["action"] == "BUY"
    assert D.decide(CAND, Stub(GOOD))["h_buy"] is True


def test_skip_needs_risk_and_buy_no_risk():
    assert D.decide(CAND, Stub(json.dumps({"action": "SKIP", "conviction": 4, "catalyst": "x", "risk": "none"})))[
        "decision_error"] == "parse_skip_without_risk"
    assert D.decide(CAND, Stub(json.dumps({"action": "BUY", "conviction": 4, "catalyst": "x", "risk": "rug"})))[
        "decision_error"] == "parse_buy_with_risk"
    ok = D.decide(CAND, Stub(json.dumps({"action": "SKIP", "conviction": 4, "catalyst": "x", "risk": "rug"})))
    assert ok["decision_error"] is None and ok["action"] == "SKIP" and not ok["h_buy"]


def test_unexpected_exception_is_skip():
    row = D.decide(CAND, Stub(exc=RuntimeError("weird")))
    assert row["action"] == "SKIP" and row["decision_error"] == "caller_RuntimeError"


def test_r_skip_seeded_and_rate():
    a = [D.r_skip(7, f"c{i}", 0.3) for i in range(2000)]
    assert a == [D.r_skip(7, f"c{i}", 0.3) for i in range(2000)]
    assert 0.25 < sum(a) / 2000 < 0.35
    assert a != [D.r_skip(8, f"c{i}", 0.3) for i in range(2000)]
    assert not any(D.r_skip(1, f"c{i}", 0.0) for i in range(100)) and all(D.r_skip(1, f"c{i}", 1.0) for i in range(100))


def test_trailing_skip_rate_ignores_errors():
    rows = [{"action": "SKIP", "h_buy": False, "decision_error": None}] * 3 + [
        {"action": "BUY", "h_buy": True, "decision_error": None},
        {"action": "SKIP", "h_buy": False, "decision_error": "caller_BudgetExceeded"}]
    assert D.trailing_skip_rate(rows) == 0.75
    assert D.trailing_skip_rate([], default=0.4) == 0.4


def test_request_body_has_no_temperature():
    from tools.haiku_caller import build_body, DEFAULT_THINKING
    body = build_body(D.MODEL, "sys", [{"role": "user", "content": "x"}], D.MAX_TOKENS, D.TEMPERATURE, DEFAULT_THINKING)
    assert "temperature" not in body
