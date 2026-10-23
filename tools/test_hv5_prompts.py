"""Tests for tools/hv5_prompts.py and the HV5 texts. No API call."""
from __future__ import annotations

import re

from tools import hv4_arms as arms
from tools import hv4_prompts as p4
from tools import hv5_prompts as p5
from tools import hv5_validate as v5
from tools.hv4_model import ModelReply
from tools.hv4_testkit import make_card, make_cards, reply


def test_hv5_playbooks_load_with_valid_structure_and_stay_under_the_word_cap():
    for name in ("CON", "BIG", "RISK"):
        pb = p5.load_playbook(name)
        assert pb.rules_words <= p4.RULES_WORDS_V0_MAX
        assert pb.sha256 != p4.load_playbook(name).sha256


def test_no_skip_anchor_left_in_hv5_entry_texts():
    for name in ("CON", "BIG"):
        t = p5.load_playbook(name).text
        assert "The default answer is SKIP" not in t
        assert "too eager" not in t and "4 in 10" not in t
        assert "If you cannot, SKIP" not in t
        assert "<=25 words" not in t
    assert "SKIP is the default" not in p5.load_template("entry").text
    assert "HOLD is the default" not in p5.load_template("recheck").text
    # the actions are listed BUY, WATCH, SKIP (SKIP not first), and SKIP asks for a why like the others
    t = p5.load_playbook("CON").text
    assert t.index('"action":"BUY"') < t.index('"action":"WATCH"') < t.index('"action":"SKIP"')
    assert re.search(r'SKIP: \{"id":"<id>","action":"SKIP","why":"<=20 words"\}', t)


def test_hv4_texts_that_hv5_keeps_are_unchanged_where_the_validator_depends_on_them():
    for name in ("CON", "BIG"):
        a, b = p4.load_playbook(name).text, p5.load_playbook(name).text
        for must in ("size is one of 0.25, 0.5, 1.0", "No other keys are accepted", "BASE RATE", "MEASURED DIRECTIONS", "NEUTRAL FIELDS",
                     "AUC 0.237 and 0.349", "Buying fresh pools lost 24.09% per attempt"):
            assert (must in a) and (must in b), must


def test_risk_prompt_asks_for_a_max_size_on_a_reject_and_a_short_why():
    t = p5.load_playbook("RISK").text
    assert "also for a reject" in t and "<=20 words" in t and "<=30 words" not in t
    assert "at most 20 words" in t
    assert arms.RISK_WHY_MAX_WORDS >= 20


def test_requests_build_and_differ_from_hv4_only_by_text():
    cards = make_cards(2)
    r5 = p5.build_entry_request("CON-L", p5.load_playbook("CON"), cards, 1)
    r4 = p4.build_entry_request("CON-L", p4.load_playbook("CON"), cards, 1)
    assert r5.ids == r4.ids and r5.nonce == r4.nonce and r5.max_tokens == r4.max_tokens
    assert r5.system[0]["text"] != r4.system[0]["text"] and r5.template_sha256 != r4.template_sha256
    assert r5.system[0]["cache_control"] == r4.system[0]["cache_control"]
    k5 = p5.build_risk_request(p5.load_playbook("RISK"), [make_card()], 3)
    assert k5.kind == "risk" and "20 words or fewer" in k5.user_text


def test_hv4_loaders_still_read_hv4_texts():
    assert p4.load_playbook("CON").text.count("The default answer is SKIP") == 1
    assert p4.TEMPLATE_DIR.name == "hv4_templates"


def test_stub_reply_following_the_hv5_example_validates():
    cards = make_cards(1)
    text = reply({"id": cards[0]["id"], "action": "SKIP", "why": "net_flow_15m is thin and ret_15m is flat"})
    out = v5.validate_entry(ModelReply(text=text, stop_reason="end_turn"), persona="CON", cards=cards,
                            knobs=v5.Knobs.v0("CON"), received_ms=cards[0]["as_of_ms"] + 1000)
    assert out[0].action == "SKIP" and not out[0].invalid
