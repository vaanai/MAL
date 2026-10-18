"""Tests for tools/hv4_review.py: bundle cutoff, perturbation invariance, placebo marginals, change validator, size cap, manifest chain, job script.

No API call: every model here is hv4_model.StubModel. No network, no key, no sealed data. The records are synthetic; nothing here says the trader
makes money or that a reviewer learns anything."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from tools import haiku_caller as hc
from tools import hv4_arms as arms
from tools import hv4_prompts as pr
from tools import hv4_review as RV
from tools.hv4_budget import BudgetGuard
from tools.hv4_model import ModelReply, StubModel
from tools.hv4_testkit import make_card

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
DAY = "2026-10-22"
CUT = RV.day_start_ms(DAY)
PREV = RV.day_start_ms("2026-10-21")
H = 3_600_000
ARM = "CON-L"
SHA40 = "a" * 40


def rec(i, *, close=None, kind="trade", net=1.0, ret=0.0, reason="target", hold=300, **card):
    close = CUT - H - i * 1000 if close is None else close
    c = make_card(f"Pool{i:04d}" + "1" * 34, close - hold * 1000, ret_15m=ret, **card)
    if kind == "trade":
        dec = {"conviction": 4, "size": 0.25, "plan": {"stop_pct": -7, "target_pct": 12, "time_min": 45}, "why": "ret_15m is shallow and net_flow_60m is positive"}
        rid, entry = f"t{i:04d}", close - hold * 1000 - RV.EXIT_FILL_MS
    else:
        dec = {"action": "SKIP", "reasons": ["model_skip"]}
        rid, entry = f"s{i:04d}", PREV + 1000 * i
    return {"id": rid, "kind": kind, "entry_ms": entry, "close_ms": close, "card": c, "decision": dec, "exit_reason": reason, "hold_s": hold, "net_pct": net}


def history(n=40):
    """n trades: the first half dip trades that lost, the rest that won (a planted split the validator can find or refuse)."""
    return [rec(i, ret=-10.0 if i < n // 2 else 2.0, net=-3.0 if i < n // 2 else 1.0, reason="stop" if i < n // 2 else "target", hold=100 + i) for i in range(n)]


@pytest.fixture
def root(tmp_path):
    for a in arms.reviewer_arms():
        row, _ = RV.genesis_manifest(a, CUT - 5 * 86_400_000, SHA40)
        RV.append_manifest(tmp_path, a, row, None, None)
    return tmp_path


def night(root, records, reply="[]", arm=ARM, day=DAY, now=CUT + 5 * 60_000, **kw):
    stub = StubModel([reply] * 3)
    row = RV.review_night(arm, day, root=root, ledger_root=root / "ledger", model=stub, now_ms=now, commit=SHA40, records=records, open_positions=2, **kw)
    return row, stub


def change(**over):
    c = {"type": "local_veto", "target": "veto", "new": "ret_15m < -5", "predicate": "ret_15m < -5", "claim": "worse", "evidence_ids": ["t0000", "t0001"],
         "why": "dip trades lost"}
    c.update(over)
    return c


# ---------------------------------------------------------------- the cutoff

def test_a_trade_closing_at_exactly_midnight_is_excluded():
    rs = [rec(1, close=CUT - 1), rec(2, close=CUT), rec(3, close=CUT + 1), rec(4, close=CUT - 2)]
    b = RV.build_bundle(ARM, DAY, RV.v0_state("CON"), rs)
    assert set(b.shown_ids) == {"t0001", "t0004"}
    assert b.max_input_close_ms == CUT - 1
    assert "t0002" not in b.text and "t0003" not in b.text
    assert [r["id"] for r in RV.before_cutoff(rs, CUT)] == ["t0004", "t0001"]


def test_loader_charges_the_exit_fill_leg_so_a_close_at_midnight_is_out(tmp_path):
    led = tmp_path / "ledger" / f"hv4-{ARM}"
    dec = tmp_path / "decisions" / ARM
    led.mkdir(parents=True), dec.mkdir(parents=True)
    card = make_card("PoolA" + "1" * 39, CUT - 2 * H)
    exits = {"pA": CUT - RV.EXIT_FILL_MS - 1, "pB": CUT - RV.EXIT_FILL_MS, "pC": CUT + 5}  # close = exit + 1900: CUT-1, CUT, CUT+1905
    lines, drows = [], []
    for k, (pid, ex) in enumerate(exits.items()):
        drows.append({"seq": k, "kind": "entry", "order_id": pid, "ms": ex - 600_000, "pos": {"pid": pid}, "card": card, "size": 0.25, "conviction": 4, "why": "x"})
        base = {"kind": "trade", "position_id": pid, "stake_sol": 0.25, "decision_ms": {"entry": ex - 600_000, "exit": ex}, "exit_reason": "target"}
        lines.append({**base, "legs": {"total": {"flat_1p9": {"net_lamports": 25_000_000}, "press_1p9": {"net_lamports": -777_000_001}}}})
        lines.append({**base, "stake_sol": 0.5, "legs": {"total": {"flat_1p9": {"net_lamports": 1}, "press_1p9": {"net_lamports": 2}}}})
    (led / "trades-2026-10-21.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    (dec / "2026-10-21.jsonl").write_text("\n".join(json.dumps(x) for x in drows) + "\n")
    got, open_n = RV.load_trade_records(ARM, tmp_path, tmp_path / "ledger", CUT)
    assert [r["id"] for r in got] == ["pA"] and got[0]["net_pct"] == 10.0 and open_n == 3
    b = RV.build_bundle(ARM, DAY, RV.v0_state("CON"), got)
    assert "777000001" not in b.text and "press" not in b.text.lower()  # the pressure leg is never read, so never shown


def test_loader_reads_only_its_own_arm(tmp_path):
    other = tmp_path / "ledger" / "hv4-CON-F"
    other.mkdir(parents=True)
    (other / "trades-2026-10-21.jsonl").write_text(json.dumps({"kind": "trade", "position_id": "zz", "stake_sol": 0.25}) + "\n")
    got, open_n = RV.load_trade_records(ARM, tmp_path, tmp_path / "ledger", CUT)
    assert got == [] and open_n == 0


def test_perturbation_invariance_outcomes_after_the_cutoff_change_nothing():
    state = RV.v0_state("CON")
    base = history(30) + [rec(100 + i, kind="skip", close=CUT - 2 * H, net=0.5, ret=-9.0) for i in range(5)]
    later = [rec(500 + i, close=CUT + i * 1000, net=9.0) for i in range(8)] + [rec(600, close=CUT + 5 * H, net=-50.0)]
    scrambled = [dict(r, net_pct=-r["net_pct"] * 7 - 1, exit_reason="stop", hold_s=1) for r in later]
    for placebo in (False, True):
        a = RV.build_bundle(ARM, DAY, state, base + later, placebo=placebo)
        b = RV.build_bundle(ARM, DAY, state, base + scrambled, placebo=placebo)
        c = RV.build_bundle(ARM, DAY, state, base, placebo=placebo)
        assert a.text == b.text == c.text and a.sha256 == c.sha256
        assert a.request.request_sha256() == c.request.request_sha256()
    # an earlier night's bundle is also byte-identical when only later outcomes are scrambled
    d1, d2 = "2026-10-21", DAY
    old = [rec(i, close=PREV - H - i * 1000, net=float(i % 5)) for i in range(12)]
    new = [rec(50 + i, close=CUT - H - i * 1000, net=float(i % 3)) for i in range(12)]
    new2 = [dict(r, net_pct=r["net_pct"] + 100.0) for r in new]
    assert RV.build_bundle(ARM, d1, state, old + new).text == RV.build_bundle(ARM, d1, state, old + new2).text
    assert RV.build_bundle(ARM, d2, state, old + new).text != RV.build_bundle(ARM, d2, state, old + new2).text


# ---------------------------------------------------------------- the placebo

def test_placebo_keeps_the_marginals_and_cuts_the_link():
    rs = history(40) + [rec(200 + i, kind="skip", close=CUT - 3 * H - i, net=float(i % 4) - 1.5, reason="time" if i % 2 else "stop", hold=50 + i) for i in range(15)]
    sh = RV.shuffle_outcomes(rs, ARM, DAY)
    assert len(sh) == len(rs) and {r["id"] for r in sh} == {r["id"] for r in rs}
    for kind in ("trade", "skip"):
        a = [r for r in rs if r["kind"] == kind]
        b = [r for r in sh if r["kind"] == kind]
        for k in RV.OUTCOME_KEYS:
            assert Counter(r[k] for r in a) == Counter(r[k] for r in b), (kind, k)
        assert Counter(tuple(r[k] for k in RV.OUTCOME_KEYS) for r in a) == Counter(tuple(r[k] for k in RV.OUTCOME_KEYS) for r in b)
    by = {r["id"]: r for r in rs}
    assert any(by[r["id"]]["net_pct"] != r["net_pct"] for r in sh)                      # the link to the card is cut
    assert all(by[r["id"]]["card"] == r["card"] and by[r["id"]]["close_ms"] == r["close_ms"] for r in sh)  # cards and times stay
    assert RV.shuffle_outcomes(rs, ARM, DAY) == sh                                       # deterministic
    assert RV.shuffle_outcomes(rs, ARM, "2026-10-23") != sh and RV.shuffle_outcomes(rs, "CON-P", DAY) != sh  # seeded by arm and date
    one = [rec(1)]
    assert RV.shuffle_outcomes(one, ARM, DAY) == one


def test_placebo_is_never_the_identity_when_outcomes_differ():
    rs = [rec(i, net=float(i)) for i in range(2)]
    for d in range(1, 29):
        sh = RV.shuffle_outcomes(rs, ARM, f"2026-10-{d:02d}")
        assert [r["net_pct"] for r in sh] != [r["net_pct"] for r in sorted(rs, key=lambda r: (r["close_ms"], r["id"]))]


def test_placebo_bundle_differs_from_the_learning_bundle_but_has_the_same_marginals():
    state = RV.v0_state("CON")
    rs = history(30)
    a = RV.build_bundle(ARM, DAY, state, rs)
    p = RV.build_bundle("CON-P", DAY, state, rs, placebo=True)
    assert a.text != p.text
    na = Counter(t["net_pct"] for t in a.obj["trades"])
    assert na == Counter(t["net_pct"] for t in p.obj["trades"])


# ---------------------------------------------------------------- the size cap

def fat(n, pad):
    return [rec(i, net=float(i % 7) - 3, ret=-8.0 + (i % 9), extra_note=None, name="Coin") for i in range(n)]


def big_cards(rs, k):
    for r in rs:
        r["card"]["candles_1m"] = [[j, 1.0 + j * 0.001, 1.1, 0.9, 1.0, 0.5] for j in range(k)]
        r["card"]["candles_5m"] = [[j, 1.0 + j * 0.002, 1.1, 0.9, 1.0, 2.5] for j in range(k)]
    return rs


def test_request_is_always_under_the_limit_and_truncates_in_the_fixed_order():
    state = RV.v0_state("CON")
    small = RV.build_bundle(ARM, DAY, state, history(10))
    assert small.dropped == {"trades": 0, "skips": 0, "candles_stripped": False} and small.request_bytes <= RV.REQUEST_MAX_BYTES
    trades = big_cards(history(60), 400)
    skips = big_cards([rec(300 + i, kind="skip", close=CUT - 2 * H - i, net=0.3) for i in range(60)], 400)
    b = RV.build_bundle(ARM, DAY, state, trades + skips)
    body = b.request.body()
    assert b.request_bytes == len(hc.canonical_bytes(body)) <= RV.REQUEST_MAX_BYTES == 99_700
    assert hc.worst_case_input_tokens(body) <= hc.MAX_INPUT_TOKENS and RV.REQUEST_MAX_BYTES + hc.WORST_CASE_INPUT_MARGIN_TOKENS < hc.MAX_INPUT_TOKENS
    assert b.dropped["candles_stripped"] is True
    assert "candles_1m" not in b.text and "candles_5m" not in b.text
    assert b.dropped["skips"] + len(b.obj["skips"]) == 60 and b.dropped["trades"] + len(b.obj["trades"]) == 60
    # step 2 before step 3: trades are cut only when no skip is left
    assert b.dropped["trades"] == 0 or len(b.obj["skips"]) == 0
    # the cut is minimal: one more skip (in sha256 order) would not have fit
    if b.dropped["skips"] and not b.dropped["trades"]:
        k = len(b.obj["skips"])
        order = RV.skip_order(skips)
        assert [x["id"] for x in b.obj["skips"]] == [x["id"] for x in order[:k]]
        bigger = RV.build_bundle(ARM, DAY, state, trades + order[:k + 1], limit=10 ** 9)
        assert bigger.request_bytes > RV.REQUEST_MAX_BYTES


def test_when_skips_alone_cannot_help_trades_are_cut_from_the_end_and_stay_unshown():
    state = RV.v0_state("CON")
    trades = big_cards(history(400), 2)
    for r in trades:
        r["card"]["pad"] = {f"k{j}": float(j) for j in range(40)}
    b = RV.build_bundle(ARM, DAY, state, trades)
    assert b.dropped["trades"] > 0 and len(b.obj["skips"]) == 0 and b.request_bytes <= RV.REQUEST_MAX_BYTES
    ordered = [r["id"] for r in sorted(trades, key=lambda r: (r["close_ms"], r["id"]))]
    assert list(b.shown_ids) == ordered[:len(b.shown_ids)]                     # a prefix in (close_ms, id) order: cut from the end
    nxt = RV.build_bundle(ARM, DAY, state, trades, shown_before=b.shown_ids)
    assert nxt.obj["trades"][0]["id"] == ordered[len(b.shown_ids)]             # the first dropped trade leads the next bundle
    assert not set(nxt.shown_ids) & set(b.shown_ids)


def test_an_empty_bundle_that_still_does_not_fit_is_refused():
    with pytest.raises(RV.BundleTooLarge):
        RV.build_bundle(ARM, DAY, RV.v0_state("CON"), history(5), limit=500)


def test_review_request_shape_ledger_and_tokens():
    b = RV.build_bundle(ARM, DAY, RV.v0_state("CON"), history(10))
    req = b.request
    assert req.kind == "review" and req.ledger == "REV-CON-L" and req.max_tokens == 4000
    body = req.body()
    assert body["model"] == hc.DEFAULT_MODEL and "temperature" not in body and "cache_control" not in json.dumps(body)
    assert arms.day_cap_usd("REV-CON-L") == 0.10
    assert req.batch_sha == b.sha256 and len(req.nonce) == 64


# ---------------------------------------------------------------- the change validator

def state():
    return RV.v0_state("CON")


def verdict(changes, rs=None):
    rs = rs or history(40)
    return RV.validate_changes(json.dumps(changes), state(), rs)


def test_a_valid_veto_and_knob_are_accepted():
    r = verdict([change(), change(type="knob", target="min_conviction", new=5, predicate="ret_15m < -5", claim="worse")])
    assert [c["type"] for c in r.accepted] == ["local_veto", "knob"] and not r.rejected
    assert r.state.vetoes == ("ret_15m < -5",) and r.state.knobs["min_conviction"] == 5
    assert r.accepted[0]["evidence"]["n_match"] == 20


def test_at_most_three_changes_a_night_else_the_whole_reply_is_refused():
    four = [change(predicate=f"ret_15m < {-5 - i}", new=f"ret_15m < {-5 - i}") for i in range(4)]
    r = verdict(four)
    assert r.accepted == () and r.reply_error == "too_many_changes" and r.state is not None
    assert len(verdict(four[:3]).accepted) == 3


def test_a_predicate_must_match_at_least_ten_past_trades():
    rs = history(40)
    few = [rec(900 + i, ret=-30.0, net=-9.0) for i in range(9)]
    r = verdict([change(predicate="ret_15m < -20", new="ret_15m < -20")], rs + few)
    assert r.rejected == ((0, "fewer_than_10_matches"),)
    ten = few + [rec(990, ret=-30.0, net=-9.0)]
    assert verdict([change(predicate="ret_15m < -20", new="ret_15m < -20")], rs + ten).accepted


def test_the_claimed_sign_must_match_the_measured_one():
    assert verdict([change(claim="better")]).rejected == ((0, "veto_claim_must_be_worse"),)
    r = verdict([change(type="knob", target="min_conviction", new=5, claim="better")])
    assert r.rejected == ((0, "effect_sign_mismatch"),)
    r = verdict([change(type="knob", target="min_conviction", new=3, predicate="ret_15m > 0", claim="better")])
    assert r.accepted


def test_unknown_evidence_ids_and_bad_shapes_are_rejected():
    assert verdict([change(evidence_ids=["nope"])]).rejected == ((0, "unknown_evidence_id"),)
    assert verdict([change(evidence_ids=[])]).rejected == ((0, "bad_evidence_ids"),)
    assert verdict([{**change(), "extra": 1}]).rejected == ((0, "bad_keys"),)
    assert verdict([change(type="scanner_edit")]).rejected == ((0, "bad_type"),)
    assert verdict([change(predicate="ret_15m < 1 OR ret_15m > 2")]).rejected[0][1].startswith("predicate")
    assert verdict([change(predicate="__import__('os')", new="__import__('os')")]).rejected[0][1].startswith("predicate")
    for bad in ("", "nope", "{}", '{"a":1}', "[1]", '[{"a":1,"a":2}]'):
        assert RV.validate_changes(bad, state(), history(40)).reply_error is not None
    assert RV.validate_changes("```json\n[]\n```", state(), history(40)).reply_error is None


@pytest.mark.parametrize("target,new,ok", [
    ("min_conviction", 3, True), ("min_conviction", 5, True), ("min_conviction", 2, False), ("min_conviction", 6, False), ("min_conviction", 4.5, False),
    ("stop_pct", -4, True), ("stop_pct", -10, True), ("stop_pct", -3.9, False), ("stop_pct", -11, False),
    ("target_pct", 6, True), ("target_pct", 30, True), ("target_pct", 31, False), ("time_min", 10, True), ("time_min", 121, False),
    ("trail_pct", 30, False), ("hard_stop_pct", -20, False), ("min_conviction", True, False), ("min_conviction", "4", False)])
def test_knobs_move_only_inside_the_declared_bounds(target, new, ok):
    r = verdict([change(type="knob", target=target, new=new, claim="worse")])
    assert bool(r.accepted) == ok, r.rejected


def test_bst_knob_set_is_min_conviction_only_inside_2_to_5():
    assert RV.check_knob("BST", "min_conviction", 2) is None and RV.check_knob("BST", "min_conviction", 1)
    assert RV.check_knob("BST", "stop_pct", -5) == "knob_unknown"
    assert RV.check_knob("BIG", "trail_pct", 40) is None and RV.check_knob("BIG", "trail_pct", 41)


NEUTRAL_TRIES = [
    change(type="playbook_edit", target="WATCH", new="WATCH. Avoid pools where the creator has sold.", claim="worse"),
    change(type="playbook_edit", target="WATCH", new="WATCH. A higher holder_count helps.", claim="better"),
    change(type="playbook_edit", target="WATCH", new="WATCH. Sniper share is a warning.", claim="worse"),
    change(type="playbook_edit", target="WATCH", new="WATCH. Same slot supply is bad.", claim="worse"),
    change(type="playbook_edit", target="WATCH", new="WATCH. Top10 share above half is risky.", claim="worse"),
    change(predicate="risk.top10_frac > 0.3", new="risk.top10_frac > 0.3"),
    change(predicate="risk.creator_sold_frac > 0.1", new="risk.creator_sold_frac > 0.1"),
    change(predicate="holder_count > 100", new="holder_count > 100"),
    change(predicate="sniper_share > 0.2", new="sniper_share > 0.2"),
    change(predicate="same_slot_supply > 0.2", new="same_slot_supply > 0.2"),
    change(type="knob", target="min_conviction", new=5, predicate="holder_count < 300"),
    change(type="playbook_edit", target="NEUTRAL FIELDS", new="NEUTRAL FIELDS. Nothing here.", claim="worse"),
    change(type="playbook_edit", target="MEASURED DIRECTIONS", new="MEASURED DIRECTIONS. Everything helps.", claim="worse"),
]


@pytest.mark.parametrize("ch", NEUTRAL_TRIES)
def test_a_direction_for_a_neutral_field_is_rejected(ch):
    rs = history(40)
    for r in rs:  # give the neutral predicates real fields and enough matches, so only the neutrality rule can reject them
        r["card"].update(holder_count=500, sniper_share=0.5, same_slot_supply=0.5)
        r["card"]["risk"].update(top10_frac=0.5, creator_sold_frac=0.5)
    r = verdict([ch], rs)
    assert r.accepted == () and r.rejected[0][1] in ("neutral_field", "bad_or_frozen_target"), r.rejected
    assert r.state.playbook.sha256 == state().playbook.sha256


def test_a_clean_playbook_edit_is_applied_and_keeps_the_frozen_parts():
    ch = change(type="playbook_edit", target="EXITS", new="EXITS. Prefer HOLD unless the card shows the reason you bought is gone.", claim="worse")
    r = verdict([ch])
    assert r.accepted and not r.rejected
    base = pr.load_playbook("CON")
    assert r.state.playbook.header_sha256 == base.header_sha256 and "Prefer HOLD" in r.state.playbook.rules
    assert RV.check_playbook_paragraph_rules(r.state.playbook, base) == []
    new = change(type="playbook_edit", target="PATIENCE", new="PATIENCE. Wait for a second candle before a decision.", claim="worse")
    r2 = verdict([new])
    assert r2.accepted and paragraphs_titles(r2.state.playbook)[-1] == "PATIENCE"


def paragraphs_titles(p):
    return [RV.paragraph_title(x) for x in RV.paragraphs(p.rules)]


def test_rules_text_over_1200_words_is_rejected_and_paragraph_charset_is_enforced():
    long = "EXITS. " + " ".join(["word"] * 400)
    assert verdict([change(type="playbook_edit", target="EXITS", new=long)]).rejected == ((0, "bad_paragraph"),)
    pb = pr.load_playbook("CON")
    over = pr.playbook_from_text("CON", "v1", pb.text + "\n\nFILLER. " + " ".join(["w"] * 700) + "\n")
    assert "rules_over_1200_words" in RV.check_playbook_paragraph_rules(over)
    for new in ('EXITS. say "hi"', "EXITS. a {{NONCE}} b", "EXITS. one\ntwo", "EXITS. back`tick"):
        assert verdict([change(type="playbook_edit", target="EXITS", new=new)]).rejected[0][1] == "bad_paragraph"


def test_at_most_eight_local_vetoes_and_no_duplicates():
    cur = RV.ArmState("CON", 3, pr.load_playbook("CON"), dict(state().knobs), tuple(f"ret_15m < {-50 - i}" for i in range(8)))
    r = RV.validate_changes(json.dumps([change()]), cur, history(40))
    assert r.rejected == ((0, "veto_cap"),)
    cur2 = RV.ArmState("CON", 3, pr.load_playbook("CON"), dict(state().knobs), ("ret_15m < -5",))
    assert RV.validate_changes(json.dumps([change()]), cur2, history(40)).rejected == ((0, "duplicate_veto"),)
    assert RV.vetoed(["ret_15m < -5"], make_card("P" + "1" * 40, 1, ret_15m=-9.0)) == "ret_15m < -5"
    assert RV.vetoed(["ret_15m < -5"], make_card("P" + "1" * 40, 1, ret_15m=0.0)) is None


def test_local_veto_new_must_equal_the_predicate():
    assert verdict([change(new="ret_15m < -6")]).rejected == ((0, "veto_differs_from_predicate"),)


def test_the_paragraph_rules_are_the_ones_of_test_hv4_prompts():
    from tools import test_hv4_prompts as TP

    for name in ("TRIGGER", "DIRECTIVE", "UNMEASURED"):
        assert getattr(RV, name).pattern == getattr(TP, name).pattern and getattr(RV, name).flags == getattr(TP, name).flags
    assert RV.paragraphs("a\n\nb\n  \nc") == TP.paragraphs("a\n\nb\n  \nc")
    for persona in ("CON", "BIG", "BST"):
        pb = pr.load_playbook(persona)
        assert RV.check_playbook_paragraph_rules(pb, pb) == [], persona


def test_a_playbook_with_a_direction_for_a_neutral_field_fails_the_paragraph_rules():
    pb = pr.load_playbook("CON")
    bad = pb.text + "\n\nFLAGS. Treat a creator who sold as a danger.\n"
    codes = RV.check_playbook_paragraph_rules(pr.playbook_from_text("CON", "v1", bad), pb)
    assert "trigger_word_outside_neutral_fields" in codes
    bad2 = pb.text.replace("no fixed direction", "a direction")
    assert RV.check_playbook_paragraph_rules(pr.playbook_from_text("CON", "v1", bad2), pb)
    bad3 = pb.text.replace("ROLE", "ROLE X", 1)
    assert "frozen_header_changed" in RV.check_playbook_paragraph_rules(pr.playbook_from_text("CON", "v1", bad3), pb)


# ---------------------------------------------------------------- a night end to end, and the manifest chain

def test_a_night_with_the_stub_writes_a_hash_chained_manifest(root):
    rs = history(40)
    reply = json.dumps([change()])
    row, stub = night(root, rs, reply)
    assert len(stub.calls) == 1 and stub.calls[0].kind == "review" and stub.calls[0].ledger == "REV-CON-L"
    assert row["version"] == 1 and row["seq"] == 1 and row["review_day"] == DAY and row["cutoff_ms"] == CUT
    assert row["max_input_close_ms"] < CUT and row["effective_from_ms"] == CUT + 30 * 60_000 and row["vetoes"] == ["ret_15m < -5"]
    assert row["prev_manifest_sha256"] == RV.read_manifests(root, ARM)[0]["sha256"] and row["shown_ids"] and row["truncation"]["request_bytes"] <= 99_700
    chain = RV.verify_chain(root, ARM)
    assert [r["seq"] for r in chain] == [0, 1]
    av = RV.active_version(root, ARM, CUT + 31 * 60_000)
    assert av.state.vetoes == ("ret_15m < -5",) and av.state.version == 1
    before = RV.active_version(root, ARM, CUT + 10 * 60_000)  # v1 is not in force before 00:30Z
    assert before.state.version == 0 and before.state.vetoes == ()
    row2, stub2 = night(root, rs, reply)  # a rerun changes nothing and makes no second call
    assert row2 == row and stub2.calls == []


def test_second_night_does_not_show_the_same_trades_again_and_logs_the_change_effect(root):
    rs = history(40)
    r1, _ = night(root, rs, json.dumps([change()]))
    day2 = "2026-10-23"
    c2 = RV.day_start_ms(day2)
    more = [rec(700 + i, close=c2 - H - i * 1000, ret=-9.0, net=-2.0) for i in range(5)]
    stub = StubModel(["[]"])
    r2 = RV.review_night(ARM, day2, root=root, ledger_root=root / "ledger", model=stub, now_ms=c2 + 300_000, commit=SHA40, records=rs + more, open_positions=0)
    assert not set(r1["shown_ids"]) & set(r2["shown_ids"]) and set(r2["shown_ids"]) == {r["id"] for r in more}
    user = stub.calls[0].user_text
    assert '"change_log":[{' in user and "ret_15m < -5" in user and r2["version"] == 2


def tamper(root, arm, fn):
    p = root / "manifests" / f"{arm}.jsonl"
    rows = RV._jsonl(p)
    fn(rows)
    p.write_text("".join(RV.canon(r) + "\n" for r in rows))


def reseal(r):
    r["sha256"] = RV.manifest_hash(r)
    return r


def two_nights(root):
    night(root, history(40), json.dumps([change()]))
    c2 = RV.day_start_ms("2026-10-23")
    stub = StubModel(["[]"])
    RV.review_night(ARM, "2026-10-23", root=root, ledger_root=root / "ledger", model=stub, now_ms=c2 + 300_000, commit=SHA40,
                    records=history(40) + [rec(800 + i, close=c2 - H - i, net=1.0) for i in range(3)], open_positions=0)
    return RV.verify_chain(root, ARM)


def test_chain_refusal_hash_edit_break_gap_and_cutoff(root):
    assert len(two_nights(root)) == 3
    orig = (root / "manifests" / f"{ARM}.jsonl").read_text()

    def restore():
        (root / "manifests" / f"{ARM}.jsonl").write_text(orig)

    def refused(code):
        with pytest.raises(RV.ManifestRefused) as e:
            RV.verify_chain(root, ARM)
        assert str(e.value) == code, str(e.value)
        with pytest.raises(RV.ManifestRefused):
            RV.active_version(root, ARM, CUT + 10 ** 9)
        restore()

    tamper(root, ARM, lambda rows: rows[1].update(vetoes=[]))                                   # an edit without a reseal
    refused("manifest_hash")
    tamper(root, ARM, lambda rows: reseal(rows[1].update(prev_manifest_sha256="f" * 64) or rows[1]))  # a re-sealed line with a broken link
    refused("chain_broken")
    tamper(root, ARM, lambda rows: rows.pop(1))                                                  # a deleted line
    refused("chain_broken")
    tamper(root, ARM, lambda rows: rows.__setitem__(slice(1, 3), [rows[2]]))
    refused("chain_broken")

    def late(rows):  # an input that closed at the cutoff, with every hash made consistent afterwards
        rows[1]["max_input_close_ms"] = CUT
        reseal(rows[1])
        rows[2]["prev_manifest_sha256"] = rows[1]["sha256"]
        reseal(rows[2])
    tamper(root, ARM, late)
    refused("input_after_cutoff")

    def early(rows):
        rows[1]["written_ms"] = CUT - 1
        reseal(rows[1])
        rows[2]["prev_manifest_sha256"] = rows[1]["sha256"]
        reseal(rows[2])
    tamper(root, ARM, early)
    refused("written_before_cutoff")

    def reorder(rows):
        rows[1]["cutoff_ms"] = CUT + 86_400_000
        reseal(rows[1])
    tamper(root, ARM, reorder)
    with pytest.raises(RV.ManifestRefused):
        RV.verify_chain(root, ARM)
    restore()
    assert len(RV.verify_chain(root, ARM)) == 3


def test_chain_refusal_files_must_hash_and_a_vetoes_list_may_only_grow(root):
    two_nights(root)
    pb = root / "playbooks" / ARM / "v1.txt"
    good = pb.read_bytes()
    pb.write_bytes(good + b"\nFLAGS. extra.\n")
    with pytest.raises(RV.ManifestRefused) as e:
        RV.verify_chain(root, ARM)
    assert str(e.value) == "playbook_hash"
    pb.write_bytes(good)
    bundle = root / "bundles" / ARM / f"{DAY}.json"
    data = bundle.read_bytes()
    bundle.write_bytes(data.replace(b'"open_positions":2', b'"open_positions":3'))
    with pytest.raises(RV.ManifestRefused) as e:
        RV.verify_chain(root, ARM)
    assert str(e.value) == "bundle_hash"
    bundle.write_bytes(data)

    def drop_veto(rows):
        rows[2]["vetoes"] = []
        rows[2]["vetoes_sha256"] = RV.sha256_hex(RV.canon([]))
        reseal(rows[2])
    tamper(root, ARM, drop_veto)
    with pytest.raises(RV.ManifestRefused) as e:
        RV.verify_chain(root, ARM)
    assert str(e.value) == "vetoes_not_tightening"


def test_a_bundle_file_holding_an_input_after_the_cutoff_is_refused(root):
    row, _ = night(root, history(40), "[]")
    bpath = root / "bundles" / ARM / f"{DAY}.json"
    b = json.loads(bpath.read_text())
    b["trades"][0]["close_ms"] = CUT + 5
    data = RV.canon(b).encode()
    bpath.write_bytes(data)

    def swap(rows):
        rows[1]["bundle_sha256"] = RV.sha256_hex(data)
        reseal(rows[1])
    tamper(root, ARM, swap)
    with pytest.raises(RV.ManifestRefused) as e:
        RV.verify_chain(root, ARM)
    assert str(e.value) == "input_after_cutoff"


def test_manifest_from_other_reviewer_instructions_or_model_is_refused(root):
    night(root, history(40), "[]")
    with pytest.raises(RV.ManifestRefused) as e:
        RV.verify_chain(root, ARM, reviewer_sha256="0" * 64)
    assert str(e.value) == "reviewer_instructions_changed"

    def other_model(rows):
        rows[1]["model"] = "claude-sonnet-5-5"
        reseal(rows[1])
    tamper(root, ARM, other_model)
    with pytest.raises(RV.ManifestRefused):
        RV.verify_chain(root, ARM)


def test_model_errors_and_a_bad_reply_leave_the_playbook_unchanged_and_are_recorded(root):
    rs = history(40)
    stub = StubModel([ModelReply(error="budget_exceeded")])
    row = RV.review_night(ARM, DAY, root=root, ledger_root=root / "ledger", model=stub, now_ms=CUT + 300_000, commit=SHA40, records=rs, open_positions=0)
    assert row["review_error"] == "budget_exceeded" and row["changes"] == [] and row["version"] == 1
    assert row["playbook_sha256"] == RV.read_manifests(root, ARM)[0]["playbook_sha256"]
    row2, _ = night(root, rs, "not json", day="2026-10-23", now=RV.day_start_ms("2026-10-23") + 300_000)
    assert row2["reply_error"] == "not_json" and row2["changes"] == []


def test_the_reviewer_ledger_is_per_arm_per_day_and_capped_at_ten_cents(root):
    guard = BudgetGuard(root)
    stub = StubModel(["[]"], guard=guard, cost_usd=0.04)
    RV.review_night(ARM, DAY, root=root, ledger_root=root / "ledger", model=stub, now_ms=CUT + 300_000, commit=SHA40, records=history(40), open_positions=0)
    assert guard.spent("REV-CON-L", DAY) == pytest.approx(0.04) and guard.spent("REV-CON-P", DAY) == 0
    expensive = StubModel(["[]"], guard=guard, cost_usd=0.11)
    row = RV.review_night("CON-P", DAY, root=root, ledger_root=root / "ledger", model=expensive, now_ms=CUT + 300_000, commit=SHA40, records=history(40), open_positions=0)
    assert row["review_error"] == "budget_exceeded" and guard.spent("REV-CON-P", DAY) == 0


def test_only_learning_arms_have_a_reviewer(root):
    for a in ("CON-F", "BIG-X", "CON-R"):
        with pytest.raises(RV.ReviewError):
            RV.review_night(a, DAY, root=root, ledger_root=root, model=StubModel(), now_ms=CUT, commit=SHA40, records=[])
    assert arms.reviewer_arms() == ("CON-L", "CON-P", "BIG-L", "BST-L") or set(arms.reviewer_arms()) == {"CON-L", "CON-P", "BIG-L", "BST-L"}


def test_the_placebo_arm_goes_through_the_same_procedure_on_shuffled_outcomes(root):
    rs = history(40)
    rl, _ = night(root, rs, json.dumps([change()]), arm="CON-L")
    stub = StubModel([json.dumps([change()])])
    rp = RV.review_night("CON-P", DAY, root=root, ledger_root=root / "ledger", model=stub, now_ms=CUT + 300_000, commit=SHA40, records=rs, open_positions=2)
    assert rl["bundle_sha256"] != rp["bundle_sha256"]
    ev = RV.shuffle_outcomes(RV.before_cutoff(rs, CUT), "CON-P", DAY)
    want = RV.validate_changes(json.dumps([change()]), RV.v0_state("CON"), ev)
    assert [c["new"] for c in rp["changes"]] == [c["new"] for c in want.accepted]


# ---------------------------------------------------------------- isolation and the job script

def test_hv4_review_imports_no_r4b_l3_or_laya_module_even_transitively():
    code = ("import sys\nsys.path.insert(0, %r)\nimport tools.hv4_review\n"
            "bad = sorted(k for k in sys.modules if any(f in k for f in ('r4b', 'laya', 'tools.l3', 'models.r4b')))\n"
            "print('BAD', bad)\n" % str(REPO))
    out = subprocess.run([sys.executable, "-I", "-c", code], cwd=str(REPO), capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    assert out.stdout.strip().endswith("BAD []"), out.stdout


def test_hv4_review_source_has_no_r4b_l3_or_laya_import_and_no_arena_paper_at_import_time():
    tree = ast.parse((TOOLS / "hv4_review.py").read_text())
    for node in ast.walk(tree):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module or ""] + [a.name for a in node.names]) if isinstance(node, ast.ImportFrom) else []
        for n in names:
            assert not re.search(r"r4b|laya|(^|\.)l3($|\.|_)", n.lower()), n
            assert "arena_paper" not in n and "arena_score" not in n
    assert "tools.hv4_runner" not in {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}


def test_job_script_is_posix_sh_with_no_xtrace_and_no_key_echo():
    p = REPO / "scripts" / "research" / "hv4-review-job.sh"
    src = p.read_text()
    assert src.startswith("#!/bin/sh\n")
    assert subprocess.run(["sh", "-n", str(p)]).returncode == 0
    assert "[[" not in src and "PIPESTATUS" not in src and "set -x" not in src.replace("`set -x`", "")
    assert "00:05Z" in src and "REV-<arm>" in src and "tools/hv4_review.py" in src and "ANTHROPIC_API_KEY" in src
    code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    assert not any(re.search(r"\becho\b.*(KEY|ANTHROPIC)", ln) for ln in code if "key file" not in ln)
    out = subprocess.run(["sh", "-x", str(p)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HV4_FROZEN_SHA": "abc"})
    assert out.returncode == 2 and "xtrace" in out.stderr
    out = subprocess.run(["sh", str(p)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HV4_FROZEN_SHA": "abc", "HV4_MODE": "nope"})
    assert out.returncode == 2
    out = subprocess.run(["sh", str(p)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HV4_FROZEN_SHA": "abc", "HV4_DAY": "x"})
    assert out.returncode == 2


def test_cli_genesis_verify_and_stub_run_end_to_end(tmp_path):
    base = [sys.executable, str(TOOLS / "hv4_review.py")]
    run = lambda *a: subprocess.run([*base, *a], capture_output=True, text=True, cwd=str(REPO))  # noqa: E731
    r = run("genesis", "--root", str(tmp_path), "--arm", ARM, "--commit", SHA40, "--clock-start-ms", str(CUT - 86_400_000))
    assert r.returncode == 0, r.stderr[-400:]
    assert run("verify", "--root", str(tmp_path), "--arm", ARM).returncode == 0
    r = run("run", "--root", str(tmp_path), "--ledger-root", str(tmp_path / "l"), "--arm", ARM, "--day", DAY, "--commit", SHA40, "--stub", "--now-ms", str(CUT + 300_000))
    assert r.returncode == 0, r.stderr[-400:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["version"] == 1 and out["accepted"] == 0 and out["review_error"] is None
