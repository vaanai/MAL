"""Tests for tools/hv5_validate.py with recorded/stub replies. No API call, no network."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tools import hv4_validate as v4
from tools import hv5_validate as v5
from tools.hv4_model import ModelReply
from tools.hv4_testkit import T0_MS, buy, make_card, make_cards, reply, skip, watch

NOW = T0_MS + 2_000


def ent(cards, text, persona="CON", received=NOW, stop_reason="end_turn", error=None, mod=v5):
    return mod.validate_entry(ModelReply(text=text, stop_reason=stop_reason, error=error), persona=persona, cards=cards,
                              knobs=v4.Knobs.v0(persona), received_ms=received)


def risk_item(pid, **over):
    d = {"id": pid, "grade": "pass", "flags": [], "max_size": 0.25, "why": "impact_025 is low and real_quote is deep"}
    d.update(over)
    return d


def rk(card, item, mod=v5):
    return mod.validate_risk(ModelReply(text=json.dumps([item]), stop_reason="end_turn"), cards=[card])[0]


# ---- 1. labels: a defect is not a skip

def test_valid_skip_is_model_skip():
    c = make_cards(1)
    out = ent(c, reply(skip(c[0]["id"])))
    assert out[0].action == "SKIP" and out[0].reasons == ("model_skip",) and not out[0].invalid


@pytest.mark.parametrize("text,code", [("not json", "bad_json"), ("", "empty_text"), ('{"a":1}', "not_array")])
def test_reply_defects_are_parse_error_not_skip(text, code):
    c = make_cards(2)
    out = ent(c, text)
    assert [x.action for x in out] == ["PARSE_ERROR"] * 2 and all(x.invalid and x.reasons == (code,) for x in out)


def test_truncated_and_refusal_are_parse_error():
    c = make_cards(1)
    assert ent(c, "[", stop_reason="max_tokens")[0].action == "PARSE_ERROR"
    assert ent(c, "", stop_reason="refusal")[0].action == "PARSE_ERROR"


def test_transport_error_is_no_output_and_not_invalid():
    c = make_cards(1)
    out = ent(c, "", error="ApiError")
    assert out[0].action == "NO_OUTPUT" and not out[0].invalid and out[0].reasons == ("no_output:ApiError",)


def test_item_defect_is_parse_error_and_missing_card_too():
    c = make_cards(2)
    out = ent(c, reply({"id": c[0]["id"], "action": "MAYBE"}))
    assert out[0].action == "PARSE_ERROR" and out[0].reasons == ("enum:action",)
    assert out[1].action == "PARSE_ERROR" and out[1].reasons == ("missing",)


def test_stale_and_below_min_keep_the_wish_and_are_not_skips():
    c = make_cards(1)
    out = ent(c, reply(buy(c[0]["id"])), received=T0_MS + 60_000)
    assert out[0].action == "STALE" and out[0].fields == {"wanted": "BUY"} and not out[0].invalid
    out = ent(c, reply(watch(c[0]["id"])), received=T0_MS + 60_000)
    assert out[0].action == "STALE" and out[0].fields == {"wanted": "WATCH"}
    out = ent(c, reply(buy(c[0]["id"], conviction=2)))
    assert out[0].action == "BELOW_MIN" and out[0].fields["wanted"] == "BUY"


def test_wanted_rate_separates_over_skipping_from_gates():
    c = make_cards(4)
    text = reply(skip(c[0]["id"]), buy(c[1]["id"], conviction=2), buy(c[2]["id"]), {"id": c[3]["id"], "action": "X"})
    r = v5.wanted_rate(ent(c, text))
    assert r == {"valid": 3, "model_skip": 1, "wanted_act": 2, "parse_error": 1, "no_output": 0, "wanted_act_share": 2 / 3}


def test_valid_buy_and_watch_pass_and_are_unchanged():
    c = make_cards(2)
    out = ent(c, reply(buy(c[0]["id"]), watch(c[1]["id"])))
    assert [x.action for x in out] == ["BUY", "WATCH"] and not any(x.invalid for x in out)
    old = ent(c, reply(buy(c[0]["id"]), watch(c[1]["id"])), mod=v4)
    assert [(x.fields, x.reasons) for x in out] == [(x.fields, x.reasons) for x in old]


# ---- 2. why words

def test_word_count_ignores_bare_separators():
    assert v5.word_count("a - b | c + d — e") == 5


def test_hv4_voids_a_risk_grade_for_31_words_hv5_keeps_it_trimmed():
    card = make_card()
    why = " ".join(["impact_025"] + ["word"] * 30)          # 31 words
    old = rk(card, risk_item(card["id"], why=why), mod=v4)
    assert old.action == "HOLD_POOL" and old.reasons == ("bounds:why_words",) and old.invalid
    new = rk(card, risk_item(card["id"], why=why))
    assert new.action == "pass" and not new.invalid and new.reasons == ("why_trimmed",)
    assert v5.word_count(new.fields["why"]) == 30


def test_separators_do_not_count_against_the_limit():
    card = make_card()
    why = " - ".join(["w"] * 30)                             # 59 tokens by split(), 30 words
    assert rk(card, risk_item(card["id"], why=why)).reasons == ()
    assert rk(card, risk_item(card["id"], why=why), mod=v4).invalid


def test_hard_limit_and_char_limit_are_still_invalid():
    card = make_card()
    assert rk(card, risk_item(card["id"], why=" ".join(["w"] * 61))).reasons == ("bounds:why_words",)
    assert rk(card, risk_item(card["id"], why="w" * 401)).reasons == ("schema:why_text",)
    assert rk(card, risk_item(card["id"], why=7)).reasons == ("schema:why_type",)


def test_entry_why_soft_limit_and_two_field_rule_stay():
    c = make_cards(1)
    long_why = "net_flow_60m ret_15m " + " ".join(["x"] * 30)       # 32 words
    out = ent(c, reply(buy(c[0]["id"], why=long_why)))
    assert out[0].action == "BUY" and out[0].reasons == ("why_trimmed",) and v5.word_count(out[0].fields["why"]) == 25
    out = ent(c, reply(buy(c[0]["id"], why="looks good to me")))
    assert out[0].action == "PARSE_ERROR" and out[0].reasons == ("why:names_fewer_than_two_card_fields",)


def test_dotted_decimal_field_name_matches():
    c = make_cards(1)
    out = ent(c, reply(buy(c[0]["id"], why="impact_0.25 is small and impact_05 is fine")))
    assert out[0].action == "BUY"


def test_skip_with_why_is_accepted_and_long_skip_why_trimmed():
    c = make_cards(1)
    out = ent(c, reply({"id": c[0]["id"], "action": "SKIP", "why": " ".join(["w"] * 30)}))
    assert out[0].action == "SKIP" and out[0].reasons == ("model_skip", "why_trimmed")


# ---- 3. risk

def test_reject_without_or_with_zero_max_size_is_valid():
    card = make_card()
    for over in ({"max_size": 0}, {"max_size": None}, {"max_size": 0.0}):
        out = rk(card, risk_item(card["id"], grade="reject", **over))
        assert out.action == "reject" and not out.invalid and out.fields["effective_max_size"] == 0.0
    item = risk_item(card["id"], grade="reject")
    del item["max_size"]
    assert rk(card, item).action == "reject"
    assert rk(card, risk_item(card["id"], grade="reject", max_size=0), mod=v4).invalid


def test_pass_and_caution_still_need_a_real_max_size():
    card = make_card()
    for g in ("pass", "caution"):
        assert rk(card, risk_item(card["id"], grade=g, max_size=0)).reasons == ("enum:max_size",)
        assert rk(card, risk_item(card["id"], grade=g, max_size=None)).reasons == ("schema:missing_max_size",)
    assert rk(card, risk_item(card["id"], grade="caution", max_size=1.0)).fields["effective_max_size"] == 0.25
    assert rk(card, risk_item(card["id"], max_size=True)).invalid


def test_flags_deduped_but_unknown_flag_and_extra_key_still_invalid():
    card = make_card()
    ok = rk(card, risk_item(card["id"], flags=["THIN_EXIT", "THIN_EXIT"]))
    assert ok.action == "pass" and ok.fields["flags"] == ["THIN_EXIT"]
    assert rk(card, risk_item(card["id"], flags=["NEW_POOL"])).reasons == ("enum:flags",)
    assert rk(card, risk_item(card["id"], extra=1)).reasons == ("schema:extra_key",)
    assert rk(card, risk_item(card["id"], grade="great")).reasons == ("enum:grade",)


def test_risk_reply_defect_still_holds_the_pool():
    card = make_card()
    out = v5.validate_risk(ModelReply(text="oops", stop_reason="end_turn"), cards=[card])
    assert out[0].action == "HOLD_POOL" and out[0].invalid


# ---- recheck

def test_recheck_long_why_trimmed_hold_still_works():
    pos = [{"id": "P1", "stop_pct": -7.0, "target_pct": 12.0, "mark_ret_pct": 1.0, "as_of_ms": T0_MS}]
    text = json.dumps([{"id": "P1", "action": "EXIT", "why": " ".join(["w"] * 30)}])
    out = v5.validate_recheck(ModelReply(text=text, stop_reason="end_turn"), positions=pos, received_ms=NOW)
    assert out[0].action == "EXIT" and out[0].reasons == ("why_trimmed",)
    assert v5.validate_recheck(ModelReply(text=json.dumps([{"id": "P1", "action": "HOLD"}]), stop_reason="end_turn"),
                               positions=pos, received_ms=NOW)[0].reasons == ("model_hold",)
    assert v5.validate_recheck(ModelReply(text="x", stop_reason="end_turn"), positions=pos, received_ms=NOW)[0].invalid


# ---- HV4 untouched

HV4_FILES = ["tools/hv4_validate.py", "tools/hv4_prompts.py", "tools/hv4_arms.py", "tools/hv4_runner.py",
             "tools/hv4_templates/entry_user.txt", "tools/hv4_templates/risk_user.txt", "tools/hv4_templates/recheck_user.txt",
             "tools/hv4_playbooks/CON_v0.txt", "tools/hv4_playbooks/BIG_v0.txt", "tools/hv4_playbooks/RISK_v0.txt",
             "tools/hv4_playbooks/BST_v0.txt"]


def test_hv4_blobs_identical_to_main():
    root = Path(__file__).resolve().parent.parent
    try:
        base = subprocess.run(["git", "-C", str(root), "merge-base", "HEAD", "origin/main"], capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        pytest.skip("no origin/main")
    for f in HV4_FILES:
        want = subprocess.run(["git", "-C", str(root), "show", f"{base}:{f}"], capture_output=True, check=True).stdout
        assert hashlib.sha256((root / f).read_bytes()).hexdigest() == hashlib.sha256(want).hexdigest(), f
