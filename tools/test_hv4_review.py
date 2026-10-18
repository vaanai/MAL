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


def rec(i, *, close=None, kind="trade", net=1.0, ret=0.0, reason="target", hold=300, cut=CUT, **card):
    close = cut - H - i * 1000 if close is None else close
    c = make_card(f"Pool{i:04d}" + "1" * 34, close - hold * 1000, ret_15m=ret, **card)
    if kind == "trade":
        dec = {"conviction": 4, "size": 0.25, "plan": {"stop_pct": -7, "target_pct": 12, "time_min": 45}, "why": "ret_15m is shallow and net_flow_60m is positive"}
        rid, entry = f"t{i:04d}", close - hold * 1000 - RV.EXIT_FILL_MS
    else:
        dec = {"action": "SKIP", "reasons": ["model_skip"]}
        rid, entry = f"s{i:04d}", cut - 86_400_000 + 1000 * i
    return {"id": rid, "kind": kind, "entry_ms": entry, "close_ms": close, "card": c, "decision": dec, "exit_reason": reason, "hold_s": hold, "net_pct": net}


def history(n=40, cut=CUT):
    """n trades: the first half dip trades that lost, the rest that won (a planted split the validator can find or refuse)."""
    return [rec(i, ret=-10.0 if i < n // 2 else 2.0, net=-3.0 if i < n // 2 else 1.0, reason="stop" if i < n // 2 else "target", hold=100 + i, cut=cut)
            for i in range(n)]


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
    assert all(by[r["id"]]["card"] == r["card"] for r in sh)   # cards stay; the records keep their TRUE close_ms internally (cutoff test, audit), and that is never sent
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
    ordered = [r["id"] for r in sorted(trades, key=lambda r: (r["entry_ms"], r["id"]))]
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
    over = pr.playbook_from_text("CON", "v1", pb.text + "\n\nFILLER. " + " ".join(["w"] * 800) + "\n")
    assert over.rules_words > 1200 and "rules_over_1200_words" in RV.check_playbook_paragraph_rules(over)
    # an edit that would push a playbook already near the cap over it is rejected, and the playbook is unchanged
    near = pr.playbook_from_text("CON", "v3", pb.text + "\n\nFILLER. " + " ".join(["w"] * 690) + "\n")
    assert 1100 < near.rules_words <= 1200 and RV.check_playbook_paragraph_rules(near, pb) == []
    st = RV.ArmState("CON", 3, near, dict(state().knobs), ())
    edit = change(type="playbook_edit", target="PATIENCE", new="PATIENCE. " + " ".join(["wait"] * 120), claim="worse")
    r = RV.validate_changes(json.dumps([edit]), st, history(40), pb)
    assert r.rejected == ((0, "rules_over_1200_words"),) and r.state.playbook.sha256 == near.sha256
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


def test_an_audit_file_holding_an_input_after_the_cutoff_is_refused(root):
    row, _ = night(root, history(40), "[]")
    bdir = root / "bundles" / ARM
    assert b"close_ms" not in (bdir / f"{DAY}.json").read_bytes() and b"entry_ms" not in (bdir / f"{DAY}.json").read_bytes()   # the sent bundle has no times
    apath = bdir / f"{DAY}.audit.json"
    a = json.loads(apath.read_text())
    assert len(a["closes"]) == 40 and max(a["closes"].values()) == row["max_input_close_ms"] < CUT
    a["closes"]["t0000"] = CUT + 5
    data = RV.canon(a).encode()
    apath.write_bytes(data)
    with pytest.raises(RV.ManifestRefused) as e:                       # the file no longer hashes to the line
        RV.verify_chain(root, ARM)
    assert str(e.value) == "audit_hash"

    def swap(rows):
        rows[1]["audit_sha256"] = RV.sha256_hex(data)
        reseal(rows[1])
    tamper(root, ARM, swap)
    with pytest.raises(RV.ManifestRefused) as e:                       # ... or the line is re-sealed to it: the close is at or after the cutoff
        RV.verify_chain(root, ARM)
    assert str(e.value) == "input_after_cutoff"


def test_a_bundle_item_missing_from_the_audit_file_is_refused(root):
    night(root, history(40), "[]")
    bdir = root / "bundles" / ARM
    a = json.loads((bdir / f"{DAY}.audit.json").read_text())
    del a["closes"][json.loads((bdir / f"{DAY}.json").read_text())["trades"][0]["id"]]
    data = RV.canon(a).encode()
    (bdir / f"{DAY}.audit.json").write_bytes(data)

    def swap(rows):
        rows[1]["audit_sha256"] = RV.sha256_hex(data)
        reseal(rows[1])
    tamper(root, ARM, swap)
    with pytest.raises(RV.ManifestRefused) as e:
        RV.verify_chain(root, ARM)
    assert str(e.value) == "bundle_item_not_in_audit"


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
    code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("[[" in ln or "PIPESTATUS" in ln or re.search(r"\bset -[a-z]*x", ln) for ln in code)
    assert "00:05Z" in src and "REV-<arm>" in src and "tools/hv4_review.py" in src and "ANTHROPIC_API_KEY" in src
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
    assert run("verify", "--root", str(tmp_path), "--arm", ARM, "--commit", SHA40).returncode == 0
    assert run("verify", "--root", str(tmp_path), "--arm", ARM, "--commit", "a" * 6 + "b" * 34).returncode == 1       # verify checks the frozen sha
    r = run("verify", "--root", str(tmp_path), "--arm", ARM, "--commit", "b" * 40)
    assert r.returncode == 1 and json.loads(r.stderr.strip().splitlines()[-1]) == {"refused": "commit_not_frozen_sha"}
    assert run("verify", "--root", str(tmp_path), "--arm", ARM).returncode == 2                                        # no --commit: refused
    r = run("run", "--root", str(tmp_path), "--ledger-root", str(tmp_path / "l"), "--arm", ARM, "--day", DAY, "--commit", SHA40, "--stub", "--now-ms", str(CUT + 300_000))
    assert r.returncode == 0, r.stderr[-400:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["version"] == 1 and out["accepted"] == 0 and out["review_error"] is None


# ---------------------------------------------------------------- the frozen sha, the runner's contract, local-veto equivalence

def test_check_manifest_refuses_any_commit_that_is_not_the_frozen_sha(root):
    night(root, history(40), "[]")
    frozen = "a" * 40
    assert len(RV.verify_chain(root, ARM, frozen_sha=frozen)) == 2
    assert len(RV.verify_chain(root, ARM, frozen_sha="a" * 7)) == 2            # an abbreviation of the same commit
    assert len(RV.verify_chain(root, ARM, frozen_sha=frozen.upper())) == 2
    for other in ("b" * 40, "a" * 12 + "b" * 28, "a" * 6 + "b" * 34, "deadbeef"):
        with pytest.raises(RV.ManifestRefused) as e:
            RV.verify_chain(root, ARM, frozen_sha=other)
        assert str(e.value) == "commit_not_frozen_sha", other
    with pytest.raises(RV.ManifestRefused) as e:
        RV.verify_chain(root, ARM, frozen_sha="not hex")
    assert str(e.value) == "commit_not_frozen_sha"

    def bad_commit(rows):
        rows[1]["commit"] = "main"
        reseal(rows[1])
    tamper(root, ARM, bad_commit)
    with pytest.raises(RV.ManifestRefused) as e:
        RV.verify_chain(root, ARM)
    assert str(e.value) == "commit_format"


def test_a_night_will_not_extend_a_chain_written_under_another_commit(root):
    night(root, history(40), "[]")
    stub = StubModel(["[]"])
    with pytest.raises(RV.ManifestRefused) as e:
        RV.review_night(ARM, "2026-10-23", root=root, ledger_root=root, model=stub, now_ms=RV.day_start_ms("2026-10-23") + 300_000, commit="c" * 40,
                        records=history(40), open_positions=0)
    assert str(e.value) == "commit_not_frozen_sha" and stub.calls == []


def test_real_active_version_is_what_the_runner_expects(root):
    from tools import hv4_runner as R

    night(root, history(40), json.dumps([change(), change(type="knob", target="min_conviction", new=5, claim="worse")]))
    src = R.review_version_source(root, SHA40)
    av = src(ARM, CUT + 31 * 60_000)
    assert av.arm == ARM and av.manifest["sha256"] and av.manifest["effective_from_ms"] == CUT + 30 * 60_000
    st = av.state
    assert (st.persona, st.version, st.vetoes) == ("CON", 1, ("ret_15m < -5",)) and st.playbook.name == "CON" and st.knobs["min_conviction"] == 5
    import types

    fresh = types.SimpleNamespace(arm_state={a: {"manifest_sha256": None, "version": 0} for a in arms.reviewer_arms()})   # what _check_version reads of the runner
    new = R.Runner._check_version(fresh, ARM, av)                       # the runner's own acceptance check on a real ActiveVersion
    assert new["version"] == 1 and new["vetoes"] == ("ret_15m < -5",) and new["manifest_sha256"] == av.manifest["sha256"]
    with pytest.raises(ValueError):                                      # ... and for the wrong arm it refuses
        R.Runner._check_version(fresh, "BIG-L", av)
    applied = types.SimpleNamespace(arm_state={**fresh.arm_state, ARM: {"manifest_sha256": av.manifest["sha256"], "version": 1}})
    assert R.Runner._check_version(applied, ARM, av)["version"] == 1       # the same line again is not a replacement
    with pytest.raises(RV.ManifestRefused):                              # a frozen sha that is not the manifest's commit: the source refuses
        R.review_version_source(root, "b" * 40)(ARM, CUT + 31 * 60_000)
    assert src("CON-F", CUT) is None                                      # an arm with no reviewer has no manifest file


def test_the_runner_applies_a_real_version_and_halts_the_arm_on_a_refused_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")  # register_bot test overrides, as in test_hv4_runner
    from tools import test_hv4_runner as TR
    from tools import hv4_runner as R

    day = RV.utc_day(TR.T0)
    cut = RV.day_start_ms(day)
    assert TR.T0 - cut >= 40 * 60_000, "the runner test tape must start 40 minutes into its UTC day for this test"
    root = tmp_path / "hv4"
    for a in arms.reviewer_arms():
        row, _ = RV.genesis_manifest(a, cut - 86_400_000, "abcdef1234567")
        RV.append_manifest(root, a, row, None, None)
    RV.review_night(ARM, day, root=root, ledger_root=root / "l", model=StubModel([json.dumps([change()])]), now_ms=cut + 300_000, commit="abcdef1234567",
                    records=history(40, cut=cut), open_positions=0)
    run = TR.make(tmp_path, versions=R.review_version_source(root, "abcdef1234567"))
    run.refresh_versions(TR.T0)
    assert run.arm_state[ARM]["version"] == 1 and run.arm_state[ARM]["vetoes"] == ("ret_15m < -5",) and not run.arm_halt
    assert run.arm_state["CON-F"]["version"] == 0 and run.arm_state["CON-F"]["vetoes"] == ()
    p = root / "manifests" / f"{ARM}.jsonl"
    rows = RV._jsonl(p)
    good_close = rows[1]["max_input_close_ms"]
    rows[1]["max_input_close_ms"] = cut
    rows[1]["sha256"] = RV.manifest_hash(rows[1])
    p.write_text("".join(RV.canon(r) + "\n" for r in rows))
    run.refresh_versions(TR.T0 + 60_000)
    assert run.arm_halt.get(ARM) == "input_after_cutoff"                  # fail closed on the refused arm ...
    assert run.persona_halt("CON") == (ARM, "input_after_cutoff")         # ... and the final runner stops the WHOLE persona group (L, F, P, R and X) on it
    assert run._live_members(arms.group_members("CON"), TR.T0 + 120_000) == ()
    assert run.persona_halt("BIG") is None and run.persona_halt("BST") is None and not run.arm_halt.get("BIG-L") and not run.arm_halt.get("CON-P")
    assert run._live_members(arms.group_members("BIG"), TR.T0 + 120_000) == arms.group_members("BIG")
    rows[1]["max_input_close_ms"] = good_close                             # the chain is repaired to the SAME line the runner applied: the group acts again
    rows[1]["sha256"] = RV.manifest_hash(rows[1])
    p.write_text("".join(RV.canon(r) + "\n" for r in rows))
    run.refresh_versions(TR.T0 + 180_000)
    assert run.persona_halt("CON") is None and run._live_members(arms.group_members("CON"), TR.T0 + 180_000) == arms.group_members("CON")


def test_runner_local_veto_and_reviewer_vetoed_are_the_same_rule():
    from tools import hv4_runner as R

    cards = []
    for i, (r15, nf, top10) in enumerate([(-9.0, 4.0, 0.2), (-2.0, 0.0, 0.5), (0.0, -3.0, 0.9), (12.5, 8.0, 0.1), (-5.0, 1.0, 0.31)]):
        c = make_card(f"Eq{i}" + "1" * 38, 1000 + i, ret_15m=r15, net_flow_60m=nf)
        c["risk"]["top10_frac"] = top10
        cards.append(c)
    cards.append({"id": "Bare" + "1" * 36, "as_of_ms": 1})                                     # no numeric fields at all
    cards.append(make_card("Nul" + "1" * 37, 2, ret_15m=None))                                  # a null field is not a number
    veto_lists = [(), ("ret_15m < -5",), ("ret_15m <= -5",), ("ret_15m >= 0 AND net_flow_60m > 5",), ("nosuchfield > 1",), ("not a predicate",), ("ret_15m < -5 OR 1",),
                  ("__import__('os') > 1",), ("ret_15m > 99", "net_flow_60m < 0"), ("risk.top10_frac > 0.3", "ret_15m < 0"), ("top10_frac > 0.3",),
                  ("a > 1 AND b > 1 AND c > 1 AND d > 1 AND e > 1",), ("ret_15m < -5" + " " * 300,), ("ret_15m < 1e999",), ("ret_15m < nan",), ("",), (None,)]
    n_fired = 0
    for vs in veto_lists:
        for c in cards:
            a, b = R.local_veto(vs, c), RV.vetoed(vs, c)
            assert a == b, (vs, c["id"], a, b)
            n_fired += a is not None
    assert n_fired >= 8                                                                         # the comparison is not vacuous: vetoes do fire


# ---------------------------------------------------------------- skip records and the pricer

class FakePricer:
    name = "fake"

    def __init__(self, table):
        self.table, self.calls = table, []

    def price_many(self, items, persona, cutoff_ms):
        self.calls.append(([k for k, _ in items], persona, cutoff_ms))
        return {k: self.table.get(k) for k, _ in items}


def skip_fixture(tmp_path, n=4, card_day_offset=0):
    """A decision log with n skip rows on 2026-10-21 and their cards in the runner's card-store format."""
    dec = tmp_path / "decisions" / ARM
    dec.mkdir(parents=True)
    cards = tmp_path / "cards"
    cards.mkdir()
    rows, store = [], []
    step = 300_000 if n <= 200 else 40_000  # a big fixture stays inside the one day
    for i in range(n):
        c = make_card(f"Sk{i:02d}" + "1" * 38, PREV + (i + 1) * step, ret_15m=-3.0 - i)
        sha = pr.card_sha256(c)
        rows.append({"seq": i, "arm": ARM, "ms": PREV + (i + 1) * step + 1500, "kind": "skip", "lane": "G", "pool": c["id"], "card_sha256": sha, "reasons": ["model_skip"]})
        store.append({"sha": sha, "card": c})
    rows.append({"seq": n, "arm": ARM, "ms": PREV + 5000, "kind": "skip", "lane": "H", "pool": "h", "card_sha256": store[0]["sha"], "reasons": ["x"]})     # lane H: not priced
    rows.append({"seq": n + 1, "arm": ARM, "ms": PREV + 6000, "kind": "skip", "lane": "G", "pool": "z", "card_sha256": "f" * 64, "reasons": ["x"]})        # card not in the store
    rows.append({"seq": n + 2, "arm": ARM, "ms": PREV + 7000, "kind": "skip", "lane": "G", "pool": store[1]["card"]["id"], "card_sha256": store[1]["sha"], "reasons": ["watch_cap"]})  # same card again
    (dec / "2026-10-21.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (cards / f"{RV.utc_day(PREV - card_day_offset * 86_400_000)}.jsonl").write_text("".join(RV.canon(r) + "\n" for r in store))
    return ["s" + r["card_sha256"][:15] for r in rows[:n]], store


def test_skip_records_count_only_replays_that_finished_before_the_cutoff(tmp_path):
    ids, _ = skip_fixture(tmp_path)
    out = lambda close, key: {"close_ms": close, "exit_reason": "target", "hold_s": 90, "net_pct": 4.5}  # noqa: E731
    fp = FakePricer({ids[0]: out(CUT - 1, 0), ids[1]: out(CUT, 1), ids[2]: out(CUT + 1, 2), ids[3]: None})
    got = RV.load_skip_records(ARM, tmp_path, "2026-10-21", "CON", fp, CUT)
    assert [r["id"] for r in got] == [ids[0]]                       # a replay closing at exactly the cutoff is out, like a trade
    assert got[0]["kind"] == "skip" and got[0]["decision"]["reasons"] == ["model_skip"] and got[0]["net_pct"] == 4.5
    assert len(fp.calls) == 1 and sorted(fp.calls[0][0]) == sorted(ids) and fp.calls[0][1:] == ("CON", CUT)   # one batch; lane H, missing card and the repeat are not priced
    assert RV.load_skip_records(ARM, tmp_path, "2026-10-21", "CON", None, CUT) == []
    assert RV.load_skip_records(ARM, tmp_path, "2026-10-21", "BST", fp, CUT) == []
    b = RV.build_bundle(ARM, DAY, RV.v0_state("CON"), got)
    assert [s["id"] for s in b.obj["skips"]] == [ids[0]] and b.obj["skips"][0]["replay"]["net_pct"] == 4.5 and b.max_input_close_ms == CUT - 1


def test_a_card_shown_just_before_midnight_is_found_in_the_previous_days_store(tmp_path):
    ids, _ = skip_fixture(tmp_path, card_day_offset=1)
    fp = FakePricer({i: {"close_ms": CUT - 5, "exit_reason": "time", "hold_s": 5, "net_pct": -1.0} for i in ids})
    assert len(RV.load_skip_records(ARM, tmp_path, "2026-10-21", "CON", fp, CUT)) == 4


def test_a_night_with_a_pricer_records_it_in_the_manifest(root, tmp_path):
    ids, store = skip_fixture(root)
    fp = FakePricer({ids[0]: {"close_ms": CUT - 10_000, "exit_reason": "stop", "hold_s": 60, "net_pct": -9.0}})
    row = RV.review_night(ARM, DAY, root=root, ledger_root=root / "ledger", model=StubModel(["[]"]), now_ms=CUT + 300_000, commit=SHA40, pricer=fp)
    assert row["skip_pricer"] == "fake"
    bundle = json.loads((root / "bundles" / ARM / f"{DAY}.json").read_text())
    assert [x["id"] for x in bundle["skips"]] == [ids[0]] and bundle["skips"][0]["card"]["id"] == store[0]["card"]["id"]
    assert night(root, [], arm="BIG-L")[0]["skip_pricer"] == "none"


def physical_rows(TR):
    """The runner test's synthetic tape with constant-product reserves: its own reserves keep the quote side fixed, which the strand rule "total" calls an invalid
    exit. Here the pool moves along x*y=k, so a rise or fall in price changes both reserves, as it does on a real pool."""
    rows = TR.trade_rows()
    first: dict[str, float] = {}
    for r in rows:
        if r["type"] != "trade":
            continue
        price = (r["sol_lamports"] / 1e9) / (r["token_raw"] / 1e6)
        p0 = first.setdefault(r["pool"], price)
        q_eff = (60 * TR.SOL + TR.V) / 1e9 * (price / p0) ** 0.5
        r["quote_reserve"] = int(q_eff * 1e9) - TR.V
        r["base_reserve"] = int(q_eff / price * 1e6)
    return rows


def test_replay_pricer_prices_the_default_plan_on_the_arena_tape_and_counts_only_exits_before_the_cutoff(tmp_path):
    from tools import test_hv4_runner as TR
    from tools.hv4_review_replay import ArenaReplayPricer

    TR.write_tape(tmp_path / "tape", physical_rows(TR))
    p = ArenaReplayPricer(tmp_path / "tape", TR.FEED_DELAY_SLOTS, sps_fn=lambda: 0.4)
    t = TR.X + TR.R.SCAN_SECOND_MS
    cards = [("up", {"id": TR.POOLS[0], "as_of_ms": t, "f": {"age_tier": "established"}, "x": None}),
             ("down", {"id": TR.POOLS[1], "as_of_ms": t, "f": {"age_tier": "established"}, "x": None})]
    far = t + 3 * TR.H
    full = p.price_many(cards, "CON", far)
    assert p.price_many(cards, "CON", far) == full                                   # deterministic
    up, down = full["up"], full["down"]
    assert up["exit_reason"] == "target" and down["exit_reason"] == "stop"            # the v0 default plan: stop -7%, target +12%, time 45 min
    assert up["net_pct"] > 0 > down["net_pct"] and abs(up["net_pct"]) < 20 and abs(down["net_pct"]) < 20
    assert down["close_ms"] < up["close_ms"] < far and up["hold_s"] > down["hold_s"] > 0
    assert RV.STAKE_SOL == 0.25 and "press" not in json.dumps(full)
    # the cutoff: a replay whose exit fill lands at or after it is None; one millisecond later it counts
    at_down = p.price_many(cards, "CON", down["close_ms"])
    assert at_down["down"] is None and at_down["up"] is None
    just = p.price_many(cards, "CON", down["close_ms"] + 1)
    assert just["down"] == down and just["up"] is None
    before_up = p.price_many(cards, "CON", up["close_ms"])
    assert before_up["up"] is None and before_up["down"] == down
    assert p.price_many(cards, "CON", up["close_ms"] + 1)["up"] == up
    assert p.price_many([], "CON", far) == {}
    with pytest.raises(RV.ReviewError):
        p.price_many(cards, "BST", far)


def test_hv4_review_replay_imports_no_r4b_l3_or_laya_module():
    code = ("import sys\nsys.path.insert(0, %r)\nimport tools.hv4_review_replay\n"
            "bad = sorted(k for k in sys.modules if any(f in k for f in ('r4b', 'laya', 'tools.l3', 'models.r4b')))\n"
            "print('BAD', bad)\n" % str(REPO))
    out = subprocess.run([sys.executable, "-I", "-c", code], cwd=str(REPO), capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    assert out.stdout.strip().endswith("BAD []"), out.stdout


def test_the_version_is_the_previous_one_plus_one_and_the_runner_refuses_a_replaced_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    from tools import hv4_runner as R
    from tools import test_hv4_runner as TR

    root = tmp_path / "chain"
    row, _ = RV.genesis_manifest(ARM, CUT - 86_400_000, SHA40)
    RV.append_manifest(root, ARM, row, None, None)
    night(root, history(40), json.dumps([change()]))
    c2 = RV.day_start_ms("2026-10-23")
    RV.review_night(ARM, "2026-10-23", root=root, ledger_root=root, model=StubModel(["[]"]), now_ms=c2 + 300_000, commit=SHA40, records=history(40), open_positions=0)
    assert [r["version"] for r in RV.verify_chain(root, ARM)] == [0, 1, 2]

    def skip_a_version(rows):                                                # version 3 after version 1, every hash made consistent
        rows[2]["version"] = 3
        reseal(rows[2])
    orig = (root / "manifests" / f"{ARM}.jsonl").read_text()
    tamper(root, ARM, skip_a_version)
    with pytest.raises(RV.ManifestRefused) as e:
        RV.verify_chain(root, ARM)
    assert str(e.value) == "version_gap"

    def repeat_a_version(rows):
        rows[2]["version"] = 1
        reseal(rows[2])
    tamper(root, ARM, repeat_a_version)
    with pytest.raises(RV.ManifestRefused) as e:
        RV.verify_chain(root, ARM)
    assert str(e.value) == "version_gap"
    (root / "manifests" / f"{ARM}.jsonl").write_text(orig)
    assert RV.verify_chain(root, ARM)[-1]["version"] == 2

    # the final runner: another valid chain whose line for an applied version differs is a replacement, and halts the whole persona group
    day = RV.utc_day(TR.T0)
    cut = RV.day_start_ms(day)
    r2 = tmp_path / "hv4"
    for a in arms.reviewer_arms():
        g, _ = RV.genesis_manifest(a, cut - 86_400_000, "abcdef1234567")
        RV.append_manifest(r2, a, g, None, None)
    RV.review_night(ARM, day, root=r2, ledger_root=r2, model=StubModel(["[]"]), now_ms=cut + 300_000, commit="abcdef1234567", records=history(40, cut=cut), open_positions=0)
    run = TR.make(tmp_path / "run", versions=R.review_version_source(r2, "abcdef1234567"))
    run.refresh_versions(TR.T0)
    assert run.arm_state[ARM]["version"] == 1 and run.persona_halt("CON") is None
    (r2 / "manifests" / f"{ARM}.jsonl").unlink()
    g, _ = RV.genesis_manifest(ARM, cut - 86_400_000, "abcdef1234567")
    RV.append_manifest(r2, ARM, g, None, None)
    knob = json.dumps([change(type="knob", target="min_conviction", new=5, claim="worse")])
    RV.review_night(ARM, day, root=r2, ledger_root=r2, model=StubModel([knob]), now_ms=cut + 301_000, commit="abcdef1234567", records=history(40, cut=cut), open_positions=0)
    run.refresh_versions(TR.T0 + 60_000)                                     # version 1 again, another line: the chain was replaced
    assert run.arm_halt[ARM] == "version_not_forward" and run.arm_state[ARM]["version"] == 1 and run.knobs[ARM].min_conviction == 4
    assert run.persona_halt("CON")[0] == ARM and run._live_members(arms.group_members("CON"), TR.T0 + 61_000) == ()


# ---------------------------------------------------------------- the placebo must not leak the true hold (reviewer 6245962504)

def numeric_leaves(x):
    if isinstance(x, bool):
        return
    if isinstance(x, (int, float)):
        yield float(x)
    elif isinstance(x, dict):
        for v in x.values():
            yield from numeric_leaves(v)
    elif isinstance(x, list):
        for v in x:
            yield from numeric_leaves(v)


def hold_recoverable(item, true_hold_s, skip_keys=("net_pct", "hold_s")):
    """True when some pair of numbers in the item differs by the true hold (in ms or in s, 2 s of slack): the way close_ms - entry_ms would give it away.
    The shuffled keys are left out (their value is the shuffle's, tested separately)."""
    nums = sorted(set(numeric_leaves({k: v for k, v in item.items() if k not in skip_keys})))
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            d = b - a
            if abs(d - true_hold_s * 1000) <= 2000 or abs(d - true_hold_s) <= 2:
                return True
    return False


def holdy(n=40, cut=CUT):
    """40 trades with distinct, large holds (7001 s and up) so that a coincidence between a card number and a hold is not plausible."""
    return [rec(i, ret=-10.0 if i < n // 2 else 2.0, net=float(i % 9) - 4, reason="stop" if i % 2 else "target", hold=7001 + 13 * i, cut=cut) for i in range(n)]


def test_the_true_hold_is_not_recoverable_from_any_field_sent_to_the_model():
    state = RV.v0_state("CON")
    rs = holdy()
    true = {r["id"]: r["hold_s"] for r in rs}
    # positive control: the old item (entry_ms and close_ms sent) gives the hold away for every trade
    leaky = [dict(RV._trade_item(r, False), entry_ms=r["entry_ms"], close_ms=r["close_ms"]) for r in rs]
    assert sum(hold_recoverable(i, true[i["id"]]) for i in leaky) == 40
    for arm, placebo in ((ARM, False), ("CON-P", True)):
        b = RV.build_bundle(arm, DAY, state, rs, placebo=placebo)
        items = b.obj["trades"]
        assert len(items) == 40 and b'"close_ms"' not in b.text.encode() and b'"entry_ms"' not in b.text.encode() and b"close_ms" not in b.text.encode()
        assert sum(hold_recoverable(i, true[i["id"]]) for i in items) == 0, arm      # 0 of 40 from any field but the shuffled ones
        sent = {i["id"]: i["hold_s"] for i in items}
        if placebo:
            fixed = sum(sent[k] == true[k] for k in true)
            assert fixed <= 3                                                            # chance level for a permutation of 40 (about 1 expected)
        else:
            assert sent == true
    # across many nights the shuffled hold carries no information on the true one: no correlation
    rs_corr = []
    for d in range(1, 29):
        day = f"2026-11-{d:02d}"
        cut = RV.day_start_ms(day)
        recs = holdy(cut=cut)
        sh = {r["id"]: r["hold_s"] for r in RV.shuffle_outcomes(RV.before_cutoff(recs, cut), "CON-P", day)}
        xs, ys = [r["hold_s"] for r in recs], [sh[r["id"]] for r in recs]
        mx, my = sum(xs) / 40, sum(ys) / 40
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        rs_corr.append(sxy / (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5)
    assert abs(sum(rs_corr) / len(rs_corr)) < 0.1 and max(abs(c) for c in rs_corr) < 0.5


def test_l_and_p_bundles_have_the_same_schema_and_the_same_item_order():
    state = RV.v0_state("CON")
    rs = holdy() + [rec(300 + i, kind="skip", close=CUT - 2 * H - i * 77, net=float(i % 5), hold=3001 + 7 * i) for i in range(12)]
    L = RV.build_bundle(ARM, DAY, state, rs)
    P = RV.build_bundle("CON-P", DAY, state, rs, placebo=True)

    def shape(x):
        if isinstance(x, dict):
            return {k: shape(v) for k, v in x.items() if k not in ("card", "rules", "why")}
        if isinstance(x, list):
            return [shape(v) for v in x[:1]]
        return type(x).__name__
    assert shape(L.obj["trades"]) == shape(P.obj["trades"]) and shape(L.obj["skips"]) == shape(P.obj["skips"])
    assert set(L.obj) == set(P.obj)
    ids = [t["id"] for t in L.obj["trades"]]
    assert ids == [t["id"] for t in P.obj["trades"]] == [r["id"] for r in sorted((r for r in rs if r["kind"] == "trade"), key=lambda r: (r["entry_ms"], r["id"]))]
    assert [s["id"] for s in L.obj["skips"]] == [s["id"] for s in P.obj["skips"]]
    for s in P.obj["skips"]:
        assert set(s["replay"]) == {"exit_reason", "hold_s", "net_pct"} and "entry_ms" not in s
    skip_true = {r["id"]: r["hold_s"] for r in rs if r["kind"] == "skip"}
    assert sum(hold_recoverable(dict(s, **{}), skip_true[s["id"]], skip_keys=("replay",)) for s in P.obj["skips"]) == 0


# ---------------------------------------------------------------- open positions, the review instant, verify

def test_open_positions_do_not_count_refused_orders_and_follow_the_exit_rule(tmp_path):
    dec = tmp_path / "decisions" / ARM
    dec.mkdir(parents=True)
    t = CUT - 6 * H
    rows = [
        {"kind": "entry", "order_id": "p1", "ms": t, "pos": {"pid": "p1"}},                      # sold well before the cutoff: closed
        {"kind": "entry", "order_id": "p2", "ms": t + 1, "pos": {"pid": "p2"}},                  # the engine refused the buy: never a position
        {"kind": "order_refused", "order_id": "p2", "side": "buy", "ms": t + 1},
        {"kind": "entry", "order_id": "p3", "ms": t + 2, "pos": {"pid": "p3"}},                  # a sell was tried and refused: still open
        {"kind": "sell", "pid": "p3", "ms": t + 600_000},
        {"kind": "order_refused", "order_id": "p3", "side": "sell", "ms": t + 600_000},
        {"kind": "entry", "order_id": "p4", "ms": t + 3, "pos": {"pid": "p4"}},                  # exit decided 1 s before the cutoff: the fill lands after it, so still open
        {"kind": "sell", "pid": "p4", "ms": CUT - 1000},
        {"kind": "entry", "order_id": "p5", "ms": t + 4, "pos": {"pid": "p5"}},                  # open
        {"kind": "entry", "order_id": "p6", "ms": CUT + 5, "pos": {"pid": "p6"}},                # entered after the cutoff: not counted
        {"kind": "sell", "pid": "p1", "ms": t + 300_000},
    ]
    for i, r in enumerate(rows):
        r.setdefault("ms", t + i)
    (dec / "2026-10-21.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    trades, open_n = RV.load_trade_records(ARM, tmp_path, tmp_path / "ledger", CUT)
    assert trades == [] and open_n == 3                                                           # p3, p4, p5


def test_a_review_before_the_cutoff_is_refused_before_any_model_call(root):
    stub = StubModel(["[]"])
    for now in (CUT - 1, CUT - 86_400_000, 0):
        with pytest.raises(RV.ReviewError) as e:
            RV.review_night(ARM, DAY, root=root, ledger_root=root / "ledger", model=stub, now_ms=now, commit=SHA40, records=history(40), open_positions=0)
        assert str(e.value) == "review_before_cutoff"
    assert stub.calls == [] and len(RV.read_manifests(root, ARM)) == 1 and not (root / "bundles").exists()
    row = RV.review_night(ARM, DAY, root=root, ledger_root=root / "ledger", model=stub, now_ms=CUT, commit=SHA40, records=history(40), open_positions=0)
    assert len(stub.calls) == 1 and row["written_ms"] == CUT                                       # at the cutoff itself the night may run
    r = subprocess.run([sys.executable, str(TOOLS / "hv4_review.py"), "run", "--root", str(root), "--ledger-root", str(root / "l"), "--arm", "BIG-L", "--day", DAY,
                        "--commit", SHA40, "--stub", "--now-ms", str(CUT - 1)], capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 1 and json.loads(r.stderr.strip().splitlines()[-1]) == {"refused": "review_before_cutoff"}


def test_job_script_verify_mode_passes_the_frozen_sha():
    src = (REPO / "scripts" / "research" / "hv4-review-job.sh").read_text()
    line = next(ln for ln in src.splitlines() if ln.strip().startswith("verify)"))
    assert '--commit "$HV4_FROZEN_SHA"' in line


# ---------------------------------------------------------------- the skip cap comes before pricing

def test_the_skip_cap_is_applied_before_pricing_and_never_depends_on_an_outcome(tmp_path):
    import time as _time

    ids, store = skip_fixture(tmp_path, n=2000)
    all_ids = sorted(ids, key=lambda i: (RV.sha256_hex(i), i))
    first = lambda c: {"close_ms": CUT - 10, "exit_reason": "time", "hold_s": 9, "net_pct": c}  # noqa: E731
    a = FakePricer({i: first(5.0) for i in ids})
    t0 = _time.time()
    got = RV.load_skip_records(ARM, tmp_path, "2026-10-21", "CON", a, CUT)
    took = _time.time() - t0
    assert len(a.calls) == 1 and a.calls[0][0] == sorted(all_ids[:RV.MAX_SKIP_CARDS]) and len(a.calls[0][0]) == 60
    assert len(got) == 60 and took < 20                                                         # one pass over 2,000 rows, not a file re-read per card
    b = FakePricer({i: (None if k % 2 else first(-9.0)) for k, i in enumerate(all_ids)})         # other outcomes, half unfinished: the same 60 are priced
    got_b = RV.load_skip_records(ARM, tmp_path, "2026-10-21", "CON", b, CUT)
    assert b.calls[0][0] == a.calls[0][0] and 0 < len(got_b) < 60
    bundle = RV.build_bundle(ARM, DAY, RV.v0_state("CON"), got)
    assert len(bundle.obj["skips"]) + bundle.dropped["skips"] == 60 and {s["id"] for s in bundle.obj["skips"]} <= set(all_ids[:60])


def test_the_synthetic_day_benchmark_runs_at_small_scale(tmp_path):
    r = subprocess.run([sys.executable, str(TOOLS / "hv4_review_bench.py"), "--dir", str(tmp_path), "--pools", "4", "--hours", "8", "--skips", "120"],
                       capture_output=True, text=True, cwd=str(REPO), timeout=300)
    assert r.returncode == 0, r.stderr[-400:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["skip_rows"] == 120 and out["priced_at_most"] == 60 and out["tape_rows"] > 1000 and out["peak_rss_mb"] > 0
