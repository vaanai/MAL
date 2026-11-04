"""Offline tests: no network, no API."""
import json
import os
from types import SimpleNamespace

import pytest

from tools.haiku_caller import BudgetExceeded, HaikuCaller, build_body
from tools.gradscore import auc as au, card as cd, config as cf, discover as dv, outcomes as oc, prompt as pr, run as rn, score as sc

NOW = 1_800_000_000.0
MINT = "AbcDEFghijkLMNopqrsTUVwxyz1234567890abcpump"


def iso(t):
    return rn.iso(t)


def gecko_doc(n_pump=2, now=NOW, age_min=3):
    data, inc = [], []
    for i in range(n_pump + 1):
        dex = "pumpswap" if i < n_pump else "raydium-launchlab"
        mint = f"Mint{i}xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxpump"
        data.append({"id": f"solana_pool{i}", "attributes": {
            "address": f"pool{i}", "pool_created_at": iso(now - age_min * 60 - i * 60), "base_token_price_usd": "0.00004",
            "base_token_price_native_currency": "0.0000004", "reserve_in_usd": "14403.7", "fdv_usd": "38407.9", "market_cap_usd": None,
            "volume_usd": {"m5": "1360.4"}, "transactions": {"m5": {"buys": 28, "sells": 14}}},
            "relationships": {"base_token": {"data": {"id": f"solana_{mint}"}}, "quote_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112"}}, "dex": {"data": {"id": dex}}}})
        inc.append({"id": f"solana_{mint}", "type": "token", "attributes": {"symbol": f"S{i}", "name": f"Name {i}"}})
    return {"data": data, "included": inc}


INFO = {"data": {"attributes": {"description": "  A frog\n in a suit ", "websites": ["https://www.frog.xyz/x"], "twitter_handle": "FrogSol",
                                "telegram_handle": None, "launchpad_details": {"completed": True}}}}


class FakeDisc:
    def __init__(self, docs):
        self.docs, self.calls = list(docs), 0

    def new_pools(self, now, max_age, pages=1):
        self.calls += 1
        return dv.parse_new_pools(self.docs[min(self.calls - 1, len(self.docs) - 1)], now, max_age)

    def token_info(self, mint):
        return dv.parse_token_info(INFO)

    def dex_pair(self, mint, pool):
        return {}


class FakeCaller:
    def __init__(self, text=None, cost=0.001, exc=None):
        self.text = text or json.dumps({"runner_prob_pct": 5, "narrative": 2, "rug_risk": 3, "reason": "plain"})
        self.cost, self.exc, self.calls = cost, exc, []

    def call(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(text=self.text, usage={"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 900,
                                                      "cache_creation_input_tokens": 0}, cost_usd=self.cost, request_sha256="x" * 64)


def mk(tmp_path, caller=None, docs=None, cfg=None, clock=None):
    c = dict(cf.DEFAULTS, **(cfg or {}))
    t = [NOW]
    col = rn.Collector(str(tmp_path), c, disc=FakeDisc(docs or [gecko_doc()]), caller=caller, clock=clock or (lambda: t[0]), sleep=lambda s: None)
    return col


def rows(tmp_path, kind):
    out = []
    for f in sorted(os.listdir(tmp_path)):
        if f.startswith(kind + "-2"):
            out += [json.loads(l) for l in open(tmp_path / f)]
    return out


# ---- discovery / dedupe
def test_parse_new_pools_filters_dex_and_age():
    assert len(dv.parse_new_pools(gecko_doc(2), NOW, 15)) == 2
    assert dv.parse_new_pools(gecko_doc(2, age_min=40), NOW, 15) == []
    p = dv.parse_new_pools(gecko_doc(1), NOW, 15)[0]
    assert p["is_pump_suffix"] and p["pool"] == "pool0" and p["price_native"] == 4e-7


def test_dedupe_by_mint_and_discovery_logged_when_enrichment_fails(tmp_path):
    col = mk(tmp_path, FakeCaller(), docs=[gecko_doc(2)])
    assert col.poll_once() == 2
    assert col.poll_once() == 0  # same doc again: deduped
    assert len(rows(tmp_path, "discoveries")) == 2

    class Boom(FakeDisc):
        def token_info(self, mint):
            raise RuntimeError("x")
    t2 = tmp_path / "b"
    col2 = rn.Collector(str(t2), dict(cf.DEFAULTS), disc=Boom([gecko_doc(1)]), caller=FakeCaller(), clock=lambda: NOW, sleep=lambda s: None)
    col2.poll_once()
    assert len(rows(t2, "discoveries")) == 1
    s = rows(t2, "scores")
    assert [r["skip_reason"] for r in s] == ["card_RuntimeError"] * 2 and not rows(t2, "cards")


def test_resume_does_not_rediscover(tmp_path):
    mk(tmp_path, FakeCaller(), docs=[gecko_doc(1)]).poll_once()
    col = mk(tmp_path, FakeCaller(), docs=[gecko_doc(1)])
    assert col.poll_once() == 0 and len(rows(tmp_path, "discoveries")) == 1


def test_state_rebuilt_from_logs_when_state_json_lost(tmp_path):
    mk(tmp_path, FakeCaller(), docs=[gecko_doc(1)]).poll_once()
    os.remove(tmp_path / "state.json")
    assert mk(tmp_path, FakeCaller(), docs=[gecko_doc(1)]).poll_once() == 0


# ---- card
def test_card_determinism_and_fields():
    disc = dv.parse_new_pools(gecko_doc(1), NOW, 15)[0]
    info = dv.parse_token_info(INFO)
    a = cd.build_card(disc, info, {}, None, ["z", "a"])
    b = cd.build_card(dict(reversed(list(disc.items()))), dict(reversed(list(info.items()))), {}, None, ["z", "a"])
    assert cd.card_text(a) == cd.card_text(b) and cd.card_sha256(a) == cd.card_sha256(b)
    assert a["description"] == "A frog in a suit" and a["website_domains"] == ["frog.xyz"] and a["twitter_handle"] == "frogsol"
    assert a["has_website"] and a["has_twitter"] and not a["has_telegram"] and a["x_context"] == ["z", "a"]
    t = cd.card_text(a)
    assert list(json.loads(t)) == sorted(json.loads(t)) and disc["mint"] not in t and "ts_utc" not in t
    assert "x_context" not in cd.build_card(disc, info, {}, None, None)


# ---- prefix stability
def test_prefix_identical_across_tokens_and_marker_on_last_block():
    for v in ("A", "B"):
        b1 = build_body("claude-haiku-5-5", pr.system_blocks(v), [{"role": "user", "content": "card one"}], 200, None, {"type": "disabled"})
        b2 = build_body("claude-haiku-5-5", pr.system_blocks(v), [{"role": "user", "content": "card two, longer"}], 200, None, {"type": "disabled"})
        assert json.dumps(b1["system"], sort_keys=True) == json.dumps(b2["system"], sort_keys=True)
        assert b1["system"][-1]["cache_control"] == {"type": "ephemeral"} and "ttl" not in b1["system"][-1]["cache_control"]
        assert "temperature" not in b1
    assert pr.system_blocks("A")[0] == pr.system_blocks("B")[0]
    assert len(pr.RUBRIC) / 4 >= 600  # >= 600 tokens at ~4 chars/token
    assert len(pr.EXAMPLES) == 6 and "EXAMPLE 6" in pr.TAIL_B and pr.prefix_sha256("A") != pr.prefix_sha256("B")


def test_collector_sends_card_after_prefix_and_same_prefix_each_time(tmp_path):
    cl = FakeCaller()
    mk(tmp_path, cl, docs=[gecko_doc(2)]).poll_once()
    assert len(cl.calls) == 4  # 2 tokens x 2 variants
    for v, k in (("A", 0), ("B", 1)):
        sysb = [json.dumps(c["system"], sort_keys=True) for c in cl.calls if c["system"][1]["text"].startswith("VARIANT: " + ("plain" if v == "A" else "rubric plus"))]
        assert len(sysb) == 2 and sysb[0] == sysb[1]
    assert all(c["temperature"] is None and c["messages"][0]["role"] == "user" for c in cl.calls)
    assert len({c["messages"][0]["content"] for c in cl.calls}) == 2


# ---- validation / SKIP
@pytest.mark.parametrize("text,reason", [
    ("not json", "parse_not_json"), ("[1]", "parse_not_object"), ('{"runner_prob_pct":5}', "parse_bad_keys"),
    (json.dumps({"runner_prob_pct": 101, "narrative": 2, "rug_risk": 3, "reason": "r"}), "parse_bad_runner_prob_pct"),
    (json.dumps({"runner_prob_pct": 5, "narrative": 6, "rug_risk": 3, "reason": "r"}), "parse_bad_narrative"),
    (json.dumps({"runner_prob_pct": 5, "narrative": 2, "rug_risk": True, "reason": "r"}), "parse_bad_rug_risk"),
    (json.dumps({"runner_prob_pct": 5, "narrative": 2, "rug_risk": 3, "reason": "w " * 21}), "parse_bad_reason"),
])
def test_bad_output_logs_skip(tmp_path, text, reason):
    mk(tmp_path, FakeCaller(text), docs=[gecko_doc(1)]).poll_once()
    s = rows(tmp_path, "scores")
    assert [r["action"] for r in s] == ["SKIP", "SKIP"] and s[0]["skip_reason"] == reason and s[0]["response_text"] == text


def test_good_output_and_fenced_json_and_usage_fields(tmp_path):
    mk(tmp_path, FakeCaller("```json\n" + json.dumps({"runner_prob_pct": 12.5, "narrative": 4, "rug_risk": 1, "reason": "ok"}) + "\n```"), docs=[gecko_doc(1)]).poll_once()
    s = rows(tmp_path, "scores")
    assert s[0]["action"] == "SCORED" and s[0]["runner_prob_pct"] == 12.5
    assert s[0]["cache_read_input_tokens"] == 900 and s[0]["cache_creation_input_tokens"] == 0 and s[0]["usage"]["input_tokens"] == 10
    assert s[0]["card_sha256"] and s[0]["prefix_sha256"]


# ---- cap
def test_cap_stops_calls_but_discovery_continues(tmp_path):
    cl = FakeCaller(exc=BudgetExceeded("cap"))
    col = mk(tmp_path, cl, docs=[gecko_doc(3)])
    col.poll_once()
    assert len(cl.calls) == 1  # first call hits the cap; no further calls that day
    assert len(rows(tmp_path, "discoveries")) == 3
    assert {r["skip_reason"] for r in rows(tmp_path, "scores")} == {"budget_cap"}


def test_real_haikucaller_cap_zero_refuses_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-test-key-not-real")
    caller = sc.DailyCaller(str(tmp_path / "h"), 0.0, now_fn=lambda: __import__("datetime").datetime(2026, 11, 4, tzinfo=__import__("datetime").timezone.utc))
    r = sc.score_one(caller, "A", "{}")
    assert r["cap"] and r["skip_reason"] == "budget_cap"


def test_default_cap_is_8():
    assert cf.DEFAULTS["daily_usd_cap"] == 8.0 and json.load(open(os.path.join(os.path.dirname(cf.__file__), "config.example.json")))["daily_usd_cap"] == 8.0


def test_config_rejects_unknown_key(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"nope": 1}')
    with pytest.raises(ValueError):
        cf.load_config(str(p))


def test_non_pump_graduate_not_scored(tmp_path):
    doc = gecko_doc(1)
    doc["data"][0]["relationships"]["base_token"]["data"]["id"] = "solana_NoSuffixMintxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    cl = FakeCaller()

    class NoGrad(FakeDisc):
        def token_info(self, m):
            return {"launchpad_completed": False}
    col = rn.Collector(str(tmp_path), dict(cf.DEFAULTS), disc=NoGrad([doc]), caller=cl, clock=lambda: NOW, sleep=lambda s: None)
    col.poll_once()
    assert not cl.calls and {r["skip_reason"] for r in rows(tmp_path, "scores")} == {"not_pump_graduate"}


