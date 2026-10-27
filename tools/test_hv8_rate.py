"""Tests for tools/hv8_rate.py (HV8 C1). SYNTHETIC fixtures only: no tape, no card files, no HV4/Q50/HV6 output, no network, no model.
Run: /data/mal/venv/bin/python -B -m pytest tools/test_hv8_rate.py -q"""
from __future__ import annotations

import collections
import copy
import json
import re
from pathlib import Path

import pytest

from tools import hv4_cards as C
from tools import hv4_prompts as P
from tools import hv6_cards as h6
from tools import hv7_runner as R7
from tools import hv8_rate as H
from tools.test_hv4_cards import SLOT, T, base_chain, base_prints, base_snap

ROOT = Path(__file__).resolve().parent
RANK = ROOT / "hv7_playbooks" / "RANK_v0.txt"
RATE = ROOT / "hv8_playbooks" / "RATE_v0.txt"
TD = "2026-08-22T03:02:00.000Z"


def real_card(chain=None, **snap_over):
    return h6.build_card(base_snap(**snap_over), base_prints(), chain or base_chain(), as_of_ms=T, as_of_slot=SLOT)


def shas(n, tag="s"):
    return [P.sha256_hex(f"{tag}{i}") for i in range(n)]


def reply(**over):
    d = {"why": "f.net_flow_15m is positive", "flow": 2, "dump_risk": 1, "trend": 3}
    d.update(over)
    return json.dumps({k: v for k, v in d.items() if v is not ...})


# ---------------------------------------------------------------- the view

def test_removed_and_kept_lists_partition_every_runner_key():
    assert H.KEPT_TOP | H.REMOVED_TOP == R7.CARD_TOP_KEYS and not (H.KEPT_TOP & H.REMOVED_TOP)
    assert H.KEPT_F == R7.CARD_F_KEYS
    assert set(H.KEPT_RISK) | H.REMOVED_RISK == R7.CARD_RISK_KEYS and not (set(H.KEPT_RISK) & H.REMOVED_RISK)


def test_key_removal_list_is_the_rpc_derived_fields():
    for k in ("top1_frac", "top10_frac", "creator_sold_frac", "same_slot_supply", "sniper_share", "sniper_still_holding", "holders_age_s",
              "mint_authority_null", "freeze_authority_null", "token_program", "extensions", "mayhem", "holder_count_cover", "holders_as_of_ms"):
        assert k in H.REMOVED_RISK
    assert H.KEPT_RISK == ("holder_count", "quote_chg_15m", "quote_dd_peak")


def test_view_drops_every_removed_key_and_keeps_the_rest():
    card = real_card()
    v = H.tape_view(card)
    assert set(v["risk"]) == set(H.KEPT_RISK)
    assert "mint" not in v and v["f"] == card["f"] and v["c1"] == card["c1"] and v["setup"] == card["setup"]
    assert v["risk"]["holder_count"] == card["risk"]["holder_count"]


def test_view_equal_across_fake_chain_and_real_chain_on_kept_keys():
    fake = base_chain(top1_frac=0.05, top10_frac=0.30, creator_sold_frac=None, same_slot_supply=None, sniper_share=None, sniper_still_holding=None)
    real = base_chain(top1_frac=0.41, top10_frac=0.88, creator_sold_frac=0.9, same_slot_supply=0.2, sniper_share=0.5, sniper_still_holding=0.4,
                      holders_as_of_ms=T - 10_000, extensions=[], mayhem=False)
    a, b = H.tape_view(real_card(fake)), H.tape_view(real_card(real))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert H.view_sha256(real_card(fake)) == H.view_sha256(real_card(real))


def test_view_fails_closed_on_an_unknown_key():
    for path in (("zzz",), ("f", "zzz"), ("risk", "zzz"), ("tops", "zzz")):
        c = real_card()
        node = c
        for p in path[:-1]:
            node = node[p]
        node[path[-1]] = 1
        with pytest.raises(H.RateError):
            H.tape_view(c)


def test_t8a_field_list_comes_from_code():
    assert len(H.T8A_NUMERIC) == len(C.FEATURE_FIELDS) + 1 + 3 + 1
    assert H.T8A_TAGS == ("setup.tag.BREAKOUT", "setup.tag.PULLBACK", "setup.tag.SECOND_WAVE")
    assert len(H.T8A_FIELDS) == len(set(H.T8A_FIELDS)) == len(C.FEATURE_FIELDS) + 1 + 3 + 1 + 3
    for m in list(H.MIRROR.values()) + [H.ANCHOR]:
        assert m in H.T8A_NUMERIC


# ---------------------------------------------------------------- the request

def test_request_carries_no_rpc_field_and_no_outcome_key():
    card = real_card(base_chain(top1_frac=0.123456, sniper_share=0.654321))
    req = H.build_rate_request(card)
    assert "0.123456" not in req.user_text and "0.654321" not in req.user_text
    line = [ln for ln in req.user_text.split("\n") if ln.startswith("{")][0]
    got = json.loads(line)
    assert set(got["risk"]) == set(H.KEPT_RISK)
    assert not (H.REMOVED_RISK & set(got["risk"])) and not (H.REMOVED_TOP & set(got))
    for k in H.REMOVED_RISK - {"as_of_ms", "as_of_slot"}:      # these two are also legitimate top-level card keys
        assert f'"{k}"' not in req.user_text
    assert req.ids == (card["id"],) and len(req.card_shas) == 1 and req.max_tokens == 300


def test_forbidden_outcome_keys_are_refused():
    for key in ("fwd_ret_60m", "pnl", "label", "outcome", "exit_price", "realized", "fill_price", "next_ret"):
        c = real_card()
        c["f"][key] = 1.0
        with pytest.raises(H.RateError):      # not in the kept list
            H.build_rate_request(c)
    # the backstop guard: no kept key trips it on a real card, and the guard itself flags planted outcome keys
    from tools.hv7_rank import forbidden_keys
    assert forbidden_keys(H.tape_view(real_card())) == []
    assert forbidden_keys({"f": {"fwd_ret_60m": 1}, "x": [{"pnl": 2}]}) == ["fwd_ret_60m", "pnl"]
    c = real_card()
    c["setup"] = {"type": "PULLBACK", "persona": "CON", "z": 1.0, "pnl": 1}
    with pytest.raises(H.RateError):
        H.build_rate_request(c)


def test_request_bytes_stable_and_nonce_by_seq():
    card = real_card()
    a, b = H.build_rate_request(card), H.build_rate_request(copy.deepcopy(card))
    assert a.request_sha256() == b.request_sha256()
    assert a.system == b.system and a.system[0]["cache_control"] == {"type": "ephemeral"} and len(a.system) == 1
    r1 = H.build_rate_request(card, seq=1)
    assert r1.nonce != a.nonce and r1.request_sha256() != a.request_sha256() and r1.system == a.system
    assert "temperature" not in a.body()


def test_system_text_is_the_rate_playbook_and_names_the_three_items():
    pb = H.load_rate_playbook()
    assert pb.text == RATE.read_text(encoding="utf-8")
    for item in H.ITEMS:
        assert item in pb.header
    assert "RATING TASK. You see ONE pool that passed the lab's code screen." in pb.header


def _paras(text):
    text = text.replace("\n=== RULES ===\n", "\n\n").replace("=== FROZEN HEADER ===\n", "")
    return [p.strip("\n") for p in text.split("\n\n") if p.strip("\n")]


def _find(text, prefix):
    m = [p for p in _paras(text) if p.startswith(prefix)]
    assert len(m) == 1, prefix
    return m[0]


def test_shared_sections_byte_equal_to_rank_v0_except_declared_sentences():
    rank, rate = RANK.read_text(encoding="utf-8"), RATE.read_text(encoding="utf-8")
    drops = {
        "UNTRUSTED TEXT": [],
        "CARDS": [" Return each id exactly as it appears in the card; an id that was not sent to you is discarded."],
        "HONESTY": [],
        "BASE RATE.": [" The book trades your first choice and pays the round trip cost before any move, so rank by what the card fields support."],
        "SCREEN, NOT REASON.": [],
        "MEASURED DIRECTIONS.": [],
    }
    for prefix, sentences in drops.items():
        want = _find(rank, prefix)
        for s in sentences:
            assert s in want
            want = want.replace(s, "")
        assert _find(rate, prefix) == want, prefix


def test_playbook_has_no_ranking_instruction():
    t = RATE.read_text(encoding="utf-8")
    assert "RANKING TASK" not in t and "best first" not in t and "first choice" not in t
    assert "rank by" not in t


def test_rubric_names_only_fields_a_view_can_carry():
    t = RATE.read_text(encoding="utf-8")
    names = set(re.findall(r"(?<![\w.])((?:f|risk|setup)\.[a-z][a-z0-9_]*)", t))
    assert names, "no field names"
    assert names <= H._KNOWN_NAMES, sorted(names - H._KNOWN_NAMES)
    for k in H.REMOVED_RISK:
        assert f"risk.{k}" not in t
    for m in H.MIRROR.values():
        assert m in t


# ---------------------------------------------------------------- the validator

def test_valid_reply():
    v = H.validate_rate(reply())
    assert v.rated and v.ratings == (2, 1, 3) and v.why_defect == () and v.reason is None


@pytest.mark.parametrize("text,reason", [
    (reply(flow=...), "missing_item"),
    (reply(trend=...), "missing_item"),
    (reply(flow=4), "item_out_of_range"),
    (reply(dump_risk=-1), "item_out_of_range"),
    (reply(flow=2.0), "item_not_int"),
    (reply(flow=1.5), "item_not_int"),
    (reply(trend="2"), "item_not_int"),
    (reply(flow=True), "item_not_int"),
    (reply(flow=None), "item_not_int"),
    (reply(why=5), "why_not_string"),
    (reply(why=...), "bad_keys"),
    (reply(extra=1), "bad_keys"),
    ("", "empty"),
    ("   \n", "empty"),
    (None, "empty"),
    ("flow 2 dump_risk 1", "not_json"),
    ("[1,2,3]", "not_object"),
    ('{"why":"f.ret_5m up","flow":1,"flow":2,"dump_risk":1,"trend":1}', "not_json"),
])
def test_unrated(text, reason):
    v = H.validate_rate(text)
    assert not v.rated and v.ratings is None and v.reason == reason


def test_refusal_stop_reason_is_unrated():
    v = H.validate_rate(reply(), stop_reason="refusal")
    assert not v.rated and v.reason == "refusal"


def test_why_defects_are_logged_not_rejected():
    long = " ".join(["f.ret_5m"] + ["word"] * 30)
    v = H.validate_rate(reply(why=long))
    assert v.rated and v.why_defect == ("long",)
    v = H.validate_rate(reply(why="looks fine to me"))
    assert v.rated and v.why_defect == ("no_field",)
    v = H.validate_rate(reply(why="f.not_a_field only"))
    assert v.rated and v.why_defect == ("no_field",)
    v = H.validate_rate(reply(why=" ".join(["word"] * 26)))
    assert v.why_defect == ("long", "no_field")
    assert H.validate_rate(reply(why="x " * 24 + "risk.holder_count")).why_defect == ()


# ---------------------------------------------------------------- retest and batches

def test_retest_decile_is_seeded_and_ceil():
    s = shas(1336)
    r = H.retest_select(s)
    assert len(r) == 134 and len(set(r)) == 134 and set(r) <= set(s)
    assert r == H.retest_select(list(reversed(s)))
    keys = sorted(int(P.sha256_hex("hv8-retest|" + x), 16) for x in s)
    assert [int(P.sha256_hex("hv8-retest|" + x), 16) for x in r] == keys[:134]
    assert len(H.retest_select(shas(11))) == 2 and len(H.retest_select(shas(10))) == 1
    with pytest.raises(H.RateError):
        H.retest_select(["a", "a"])


def test_batches_at_most_64_with_retests_last():
    s = shas(1336)
    r = H.retest_select(s)
    b = H.split_batches(s, r)
    assert [x["batch"] for x in b] == list(range(1, 25))
    assert all(len(x["shas"]) <= 64 for x in b)
    assert [x["kind"] for x in b] == ["first"] * 21 + ["retest"] * 3
    assert [len(x["shas"]) for x in b[21:]] == [64, 64, 6]
    assert sum(len(x["shas"]) for x in b[:21]) == 1336 and sorted(y for x in b[:21] for y in x["shas"]) == sorted(s)
    assert sorted(y for x in b[21:] for y in x["shas"]) == sorted(r)
    assert b == H.split_batches(list(reversed(s)), r)
    with pytest.raises(H.RateError):
        H.split_batches(s[:10], ["not-a-first"])


def test_call_caps_and_cost_arithmetic():
    assert H.call_caps(1336) == {"first": 1336, "retest": 134, "rerun": 67, "cap": 1537}
    assert H.call_cost_usd() == pytest.approx(0.00056745, abs=1e-12)
    assert H.call_cost_usd(output=300) == pytest.approx(0.00065745, abs=1e-12)
    assert H.call_cost_usd() * 192 == pytest.approx(0.1089504, abs=1e-9)
    assert H.call_cost_usd(cache_write=0, new_input=2874 + 1482) == pytest.approx(0.0004956, abs=1e-12)
    assert H.call_cost_usd() * 25 + 0 == pytest.approx(0.01418625, abs=1e-9)


# ---------------------------------------------------------------- P8 placebo

def test_placebo_deterministic_no_fixed_point_and_a_single_cycle():
    for n in range(2, 9):
        for k in range(40):
            h = shas(n, f"set{k}-")
            pi = H.placebo_perm(TD, h)
            assert pi == H.placebo_perm(TD, list(h))
            assert sorted(pi) == list(range(n))
            assert all(pi[i] != i for i in range(n))
            seen, i = set(), 0
            while i not in seen:      # one N-cycle (Sattolo)
                seen.add(i)
                i = pi[i]
            assert len(seen) == n


def test_placebo_seed_depends_on_time_and_order():
    h = shas(6)
    assert H.placebo_perm(TD, h) != H.placebo_perm("2026-08-22T04:02:00.000Z", h) or H.placebo_perm(TD, h) != H.placebo_perm(TD, h[::-1])


def test_placebo_matches_the_spec_arithmetic_for_a_fixed_seed():
    h = shas(5, "spec")
    seed = P.sha256_hex("hv8-P|" + TD + "|" + ",".join(h))
    pi = list(range(5))
    for i in range(4, 0, -1):
        j = int(P.sha256_hex(seed + "|" + str(i)), 16) % i
        pi[i], pi[j] = pi[j], pi[i]
    assert H.placebo_perm(TD, h) == pi


def test_placebo_uniform_over_n_cycles():
    n, trials = 4, 3000
    cnt = collections.Counter(tuple(H.placebo_perm(TD, shas(n, f"u{t}-"))) for t in range(trials))
    assert len(cnt) == 6       # (n-1)! = 6 cycles
    for c in cnt.values():
        assert 380 < c < 620, cnt


def test_placebo_moves_whole_triples():
    h = shas(5, "tri")
    tr = [(i, i + 10, i + 20) for i in range(5)]
    out = H.placebo_ratings(TD, h, tr)
    pi = H.placebo_perm(TD, h)
    assert out == [tr[pi[k]] for k in range(5)]
    assert all(out[k] != tr[k] for k in range(5)) and sorted(out) == sorted(tr)
    with pytest.raises(H.RateError):
        H.placebo_ratings(TD, h, tr[:4])
