import json
from types import SimpleNamespace

import pytest

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
    assert kw["temperature"] == 0
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
    assert c.ledger.path.name.startswith("slowbot-haiku-ledger-")
    row = D.decide(CAND, D.daily_caller(tmp_path, budget_usd=0.0))
    assert row["action"] == "SKIP" and row["decision_error"].startswith("caller_")
